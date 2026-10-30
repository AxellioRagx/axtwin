"""pcap ingest plugin — a capture file, read by running the pinned Zeek over it.

`ingest/pcap/SPEC.md` is the design of record. The plugin takes a pcap (or a directory of
pcaps), runs Zeek over each into ONE temporary log directory, hands that directory to the
`zeek` plugin, and removes it when the event iterator closes. It serves the **discover** step
for the operator who has a capture file and no sensor: `aximprint ingest --plugin pcap
--source pcap:/caps/site.pcap --vantage …` yields the same imprint the appliance path yields.

**This is the deliberate exception to "aximprint is not a sensor."** The plugin runs a packet
parser, but nothing from `ingest` downward sees a packet: the parser is Zeek (in a container, or
a native binary), and its output is sensor metadata on disk, read by the same `zeek` reader every
other Zeek-shaped source ends in (ARCHITECTURE §3). The plugin does not parse packets itself and
will not — the role learner needs a sensor-reported acceptance state for every flow, and the
Windows roles need `kerberos`/`ntlm`/`smb_mapping`/`dce_rpc` records only Zeek structures.

Locator: ``pcap:<file or directory>[?image=<ref>&scripts=local|bare&checksums=skip|verify]``.
Plugin options ride in the query string because `config_schema` is closed (`source` plus the
shared run options, asserted by `tests/test_ingest_plugins.py`). An unknown key or value is
refused by name (CONVENTIONS §4, fail closed).

The runner is `aximprint.zeekrun`'s — the same module `acquire/harvest` drives — taken by
constructor injection (tests pass `FakeZeekRunner`) or, when none is injected, chosen by a
PROBE at run time: a native `zeek` on `PATH` first, then `podman`, then `docker`; none → the run
is refused with the reason. A found runtime that cannot actually run (the pinned image absent
from podman's store, say) is refused with ITS reason rather than falling through to the next —
one clear answer, not a cascade.

The temporary directory is created under the process's temp root (`TMPDIR`), never in the
checkout, and is removed in the generator's `finally` — so it is gone whether the iterator was
exhausted, closed early, or abandoned to the garbage collector.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import parse_qsl

from aximprint.events import Event
from aximprint.ingest.base import IngestPlugin, register
from aximprint.ingest.zeek import ZeekPlugin
from aximprint.vantage import Vantage
from aximprint.zeekrun import (
    DEFAULT_SCRIPT_POLICY,
    DEFAULT_ZEEK_IMAGE,
    NativeZeekRunner,
    PodmanZeekRunner,
    ScriptPolicy,
    ZeekRunner,
    _require_pinned_image,
)

__all__ = ["PCAP_PREFIX", "PCAP_SUFFIXES", "Locator", "PcapError", "PcapPlugin",
           "parse_locator", "pcap_files"]

PCAP_PREFIX = "pcap:"

#: File suffixes taken as captures when the locator names a directory. Zeek reads pcap and
#: pcapng natively; a compressed capture is not read (Zeek cannot), so it is not listed.
PCAP_SUFFIXES = frozenset({".pcap", ".pcapng", ".cap"})

#: `scripts=` → the script policy handed to the runner. `local` is `zeekrun`'s own default (the
#: full log set, including `known_*`, `capture_loss` and `stats`, the only sources of sensor
#: health); `bare` is Zeek's base scripts alone.
_SCRIPT_POLICIES: dict[str, ScriptPolicy] = {
    "local": DEFAULT_SCRIPT_POLICY,
    "bare": ScriptPolicy(scripts=()),
}

#: `checksums=` → `ignore_checksums`. `skip` passes `-C`; see `zeekrun.DEFAULT_IGNORE_CHECKSUMS`
#: for the measured reason that is the default (a mirrored capture's checksums are wrong by
#: construction, and Zeek silently records every such flow as never established).
_CHECKSUM_MODES: dict[str, bool] = {"skip": True, "verify": False}

_OPTION_KEYS = frozenset({"image", "scripts", "checksums"})

#: Runner probe order (`SPEC.md`, "Config"): the binary looked for on `PATH`, in order.
_RUNTIMES: tuple[str, ...] = ("zeek", "podman", "docker")


class PcapError(ValueError):
    """A locator, a capture path, or a runner the plugin refuses — always with the reason."""


@dataclass(frozen=True)
class Locator:
    """A parsed `pcap:` locator: the capture path plus the three runner options."""

    path: Path
    image: str = DEFAULT_ZEEK_IMAGE
    scripts: str = "local"
    checksums: str = "skip"

    @property
    def script_policy(self) -> ScriptPolicy:
        return _SCRIPT_POLICIES[self.scripts]

    @property
    def ignore_checksums(self) -> bool:
        return _CHECKSUM_MODES[self.checksums]


def parse_locator(source: str) -> Locator:
    """`pcap:<path>[?k=v&…]` → `Locator`. Refuses, by name, a missing prefix, an empty path, an
    unknown key, a repeated key, an unknown `scripts`/`checksums` value, and an unpinned image
    (`zeekrun._require_pinned_image`: `:latest` or tagless is refused before anything runs).
    """
    if not source.startswith(PCAP_PREFIX):
        raise PcapError(f"pcap source must be '{PCAP_PREFIX}<file or directory>[?image=&scripts=&"
                        f"checksums=]', got {source!r}")
    rest = source[len(PCAP_PREFIX):]
    raw_path, sep, query = rest.partition("?")
    if not raw_path:
        raise PcapError(f"pcap source names no file or directory: {source!r}")
    options: dict[str, str] = {}
    if sep:
        for key, value in parse_qsl(query, keep_blank_values=True, strict_parsing=False):
            if key not in _OPTION_KEYS:
                raise PcapError(f"pcap source option {key!r} is not one of "
                                f"{sorted(_OPTION_KEYS)}: {source!r}")
            if key in options:
                raise PcapError(f"pcap source option {key!r} given twice: {source!r}")
            options[key] = value
    scripts = options.get("scripts", "local")
    if scripts not in _SCRIPT_POLICIES:
        raise PcapError(f"pcap source scripts={scripts!r} is not one of "
                        f"{sorted(_SCRIPT_POLICIES)}")
    checksums = options.get("checksums", "skip")
    if checksums not in _CHECKSUM_MODES:
        raise PcapError(f"pcap source checksums={checksums!r} is not one of "
                        f"{sorted(_CHECKSUM_MODES)}")
    image = options.get("image", DEFAULT_ZEEK_IMAGE)
    if not image:
        raise PcapError("pcap source image= is empty; omit it for the pinned default "
                        f"({DEFAULT_ZEEK_IMAGE})")
    _require_pinned_image(image)  # raises PinnedImageRequiredError, a ZeekError, by name
    return Locator(path=Path(raw_path), image=image, scripts=scripts, checksums=checksums)


def pcap_files(path: Path) -> list[Path]:
    """The capture files a locator path names: the one file, or the directory's immediate
    children with a capture suffix (`PCAP_SUFFIXES`), name-sorted. Not recursive — a nested
    layout is not a shape this plugin claims to read. Refuses a missing path and a directory
    holding no capture at all (a run over nothing would be a quiet, green, empty imprint).
    """
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise PcapError(f"pcap source {str(path)!r}: no such file or directory")
    files = sorted(p for p in path.iterdir()
                   if p.is_file() and p.suffix.lower() in PCAP_SUFFIXES)
    if not files:
        raise PcapError(f"pcap source {str(path)!r}: a directory with no "
                        f"{'/'.join(sorted(PCAP_SUFFIXES))} file directly under it")
    return files


def _runtime_on_path() -> Optional[str]:
    """The first of `_RUNTIMES` found on `PATH`, or `None`. A probe, not a config (`SPEC.md`)."""
    for name in _RUNTIMES:
        if shutil.which(name):
            return name
    return None


def _build_runner(loc: Locator, runtime: str) -> ZeekRunner:
    if runtime == "zeek":
        return NativeZeekRunner(script_policy=loc.script_policy,
                                ignore_checksums=loc.ignore_checksums)
    if runtime == "podman":
        return PodmanZeekRunner(loc.image, podman_bin="podman", script_policy=loc.script_policy,
                                ignore_checksums=loc.ignore_checksums)
    if runtime == "docker":
        # docker has no `--userns=keep-id`; `--user uid:gid` is how its logs land owned by the
        # invoking user (so the temporary directory can be removed afterwards).
        return PodmanZeekRunner(loc.image, podman_bin="docker", script_policy=loc.script_policy,
                                ignore_checksums=loc.ignore_checksums,
                                user_args=("--user", f"{os.getuid()}:{os.getgid()}"))
    raise PcapError(f"unknown zeek runtime {runtime!r}")  # pragma: no cover — closed table


@register
class PcapPlugin(IngestPlugin):
    name = "pcap"
    source_kind = "path"
    source_help = ("pcap:<file or directory>[?image=<ref>&scripts=local|bare&checksums=skip|"
                   "verify] -- a capture (or a directory of captures) run through the pinned "
                   "Zeek into a temporary log directory and read as zeek logs; no --from/--to")

    def __init__(self, runner: ZeekRunner | None = None):
        #: The runner, by constructor injection (`FakeZeekRunner` in tests). `None` means
        #: "choose by probe when a run or a probe needs one" — `get_plugin()` constructs
        #: with no arguments, so this is the production path.
        self._runner = runner
        #: The delegate reader. Every event, and every `unparsed_stems`/`unrecognized_stems`
        #: answer, is the `zeek` plugin's own, taken over the temporary directory.
        self._zeek = ZeekPlugin()

    # -- the inherited stem inventory is the delegate's (`SPEC.md`, "Seam") ------------------

    @property
    def unparsed_stems(self) -> dict[str, int] | None:  # type: ignore[override]
        return self._zeek.unparsed_stems

    @property
    def unrecognized_stems(self) -> dict[str, int] | None:  # type: ignore[override]
        return self._zeek.unrecognized_stems

    def _say(self, msg: str) -> None:
        print(f"[pcap] {msg}", file=sys.stderr, flush=True)

    # -- runner choice ----------------------------------------------------------------------

    def _choose_runner(self, loc: Locator) -> tuple[str, ZeekRunner]:
        """`(name, runner)`: the injected runner under its class name, else the first runtime
        on `PATH` built for `loc`. Raises `PcapError` naming why when there is none, or when the
        one found cannot run (its own `probe()` says so — the image absent, say).
        """
        if self._runner is not None:
            return type(self._runner).__name__, self._runner
        runtime = _runtime_on_path()
        if runtime is None:
            raise PcapError("no zeek runner available: none of "
                            f"{', '.join(_RUNTIMES)} is on PATH (install zeek, or podman/docker "
                            f"with the {loc.image} image pulled)")
        runner = _build_runner(loc, runtime)
        answer = runner.probe()
        if not answer.ok:
            raise PcapError(f"zeek runner {runtime!r} cannot run: {answer.detail}")
        return runtime, runner

    # -- IngestPlugin -----------------------------------------------------------------------

    def probe(self, source: str) -> dict:
        """Reach the capture without running Zeek: how many files, how many bytes, and which
        runner would run them. `reachable: False` with the reason when the locator is refused,
        the path is missing, or no runner can run — an answer, not a crash.
        """
        answer: dict = {"supported": True, "reachable": False, "error": None,
                        "files": 0, "bytes": 0, "runner": None, "vantage": None}
        try:
            loc = parse_locator(source)
            files = pcap_files(loc.path)
        except ValueError as exc:  # PcapError and zeekrun's ZeekError both root here
            answer["error"] = str(exc)
            return answer
        answer["files"] = len(files)
        answer["bytes"] = sum(p.stat().st_size for p in files)
        try:
            answer["runner"], _ = self._choose_runner(loc)
        except PcapError as exc:
            answer["error"] = str(exc)
            return answer
        answer["reachable"] = True
        return answer

    def probe_vantage(self, source: str) -> Optional[Vantage]:
        """`None`, always: a capture file records no tap point, and nothing here guesses one.
        The operator names it with `--vantage` (`LES-006`: unknown is a state, not a default).
        """
        return None

    def events(self, source: str, ts_from: float | None = None,
               ts_to: float | None = None) -> Iterator[Event]:
        """Validate everything first — the locator, the files, the runner — then hand back the
        generator that runs Zeek and reads the result. Refusals land here, before a temporary
        directory exists; a `--from`/`--to` slice is refused outright, because a pcap has no
        partition index (slice the capture with `editcap`, or read the `parquet:` archive a
        `features` run writes).
        """
        if ts_from is not None or ts_to is not None:
            raise ValueError("--from/--to are not supported on a pcap source: a capture has no "
                             "partition index to slice on; cut it with editcap, or read the "
                             "parquet: archive a `features` run writes")
        loc = parse_locator(source)
        files = pcap_files(loc.path)
        runtime, runner = self._choose_runner(loc)
        return self._events(loc, files, runtime, runner)

    def _events(self, loc: Locator, files: list[Path], runtime: str,
                runner: ZeekRunner) -> Iterator[Event]:
        out_root = Path(tempfile.mkdtemp(prefix="aximprint-pcap-"))
        try:
            # One file: Zeek's logs land flat in `out_root`. Several: one window directory per
            # file, named by the file's position in the sorted list — a shape `LogDirSource`
            # reads as a window tree (every immediate subdirectory name parses as a window_id),
            # merging each stem across the directories on every row's own `ts`, never on the
            # directory name, so the name need only be shape-valid and unique.
            if len(files) == 1:
                targets = [(files[0], out_root)]
            else:
                targets = [(pcap, out_root / str(i)) for i, pcap in enumerate(files)]
            for pcap, out_dir in targets:
                self._say(f"zeek ({runtime}) -r {pcap.name} scripts={loc.scripts} "
                          f"checksums={loc.checksums}")
                result = runner.run(pcap, out_dir)
                if not result.ok:
                    # A failed run can still have written a partial log set, which the reader
                    # would take for a quiet capture — the false-green shape. Refuse instead.
                    raise PcapError(f"zeek failed on {pcap.name} (exit {result.returncode}): "
                                    f"{result.stderr_tail.strip() or 'no stderr'}")
                note = (f"zeek {result.version}: {pcap.name}: {len(result.logs)} log(s) in "
                        f"{result.duration_s:.1f}s")
                if result.unrecognized:
                    note += f"; unrecognized stems: {', '.join(result.unrecognized)}"
                self._say(note)
            yield from self._zeek.events(str(out_root))
        finally:
            shutil.rmtree(out_root, ignore_errors=True)
