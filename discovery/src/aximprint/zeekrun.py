"""zeekrun — running Zeek over one pcap, reproducibly.

**Sits below both `acquire` and `ingest`, and imports nothing from `aximprint`** — the
`vantage.py` precedent. `acquire/harvest` (the appliance harvester) and `ingest/pcap` (the pcap
plugin) each need the same thing — a way to turn one pcap into one directory of Zeek logs — and
CONVENTIONS §3 forbids either from importing the other, so the runner lives here, once, and
`acquire/harvest/zeek.py` re-exports every name so its callers and tests are unchanged
(`ingest/pcap/SPEC.md`, "Seam"). Being below both layers is also why `ZeekError` roots in
`ValueError` alone here rather than in `acquire.errors.XtractError`: that base belongs to
`acquire`, and this module may not reach up for it.

Turns one pcap into one directory of Zeek logs and records everything the archive manifest will
later need: the exact Zeek version, which log stems were produced, how long it took, and what went
wrong if anything did (`ZeekResult`). Zeek is normally not installed on the host — it runs
containerized via `podman` (`PodmanZeekRunner`), image **pinned by tag or digest**; a `:latest`
image is refused outright, because a moving image silently changes the log schema under a growing
archive (`docs/PLAN_metadata_harvester.md` §5). Where a native `zeek` binary IS on `PATH`,
`NativeZeekRunner` drives it with the same argv shape and the same result discipline.

**`FakeZeekRunner` is production code, not a test helper.** Every later work order (H-4 the
pipeline, H-5 the converter, H-7 resume) has to be testable on a host where Zeek does not exist —
which is this host today — so the fake lives here, satisfies the same `ZeekRunner` protocol, and is
built with the same care as the real thing.

**The cold tier is the evidence tier — this module never trims Zeek's log set.** `ZeekResult.logs`
reports whatever `zeek` (real or fake) actually produced, enumerated by listing the output directory
after the run, never assumed. `aximprint` currently parses only 28 of Zeek's log types
(`KNOWN_PARSED_STEMS`) and explicitly skips 7 more (`KNOWN_SKIPPED_STEMS`); this module reports which
produced stems fall outside that combined set (`ZeekResult.unrecognized`) so a Zeek upgrade that
starts emitting a new log type is visible, not silently generated-and-ignored or silently discarded.

Scope, deliberately narrow: no log parsing, no Parquet conversion, no archive writing, no
*owning* of concurrency (no thread pool, no work scheduling) — those are H-4/H-5/H-3's jobs.
`PodmanZeekRunner` does guard its own small piece of lazily-cached internal state (the resolved
Zeek version) with a `threading.Lock`, because H-4's worker pool is expected to share one runner
instance across threads — that is safety for being *called* concurrently, not this module taking
on concurrency management itself. `subprocess` is the only I/O this module performs, and only from
`PodmanZeekRunner`; `FakeZeekRunner` touches only the filesystem.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

__all__ = [
    "DEFAULT_IGNORE_CHECKSUMS",
    "DEFAULT_SCRIPT_POLICY",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_ZEEK_IMAGE",
    "KNOWN_PARSED_STEMS",
    "KNOWN_SKIPPED_STEMS",
    "KNOWN_ZEEK_STEMS",
    "PROBE_TIMEOUT_S",
    "RUNNER_FAILED_RETURNCODE",
    "TIMEOUT_RETURNCODE",
    "FakeZeekRunner",
    "NativeZeekRunner",
    "PinnedImageRequiredError",
    "PodmanZeekRunner",
    "ProbeResult",
    "ScriptPolicy",
    "ZeekError",
    "ZeekResult",
    "ZeekRunner",
    "unrecognized_stems",
]


class ZeekError(ValueError):
    """Common root for every error this module raises. A plain `ValueError` root, not
    `acquire.errors.XtractError`: see the module docstring — this module sits below `acquire`.
    """


class PinnedImageRequiredError(ZeekError):
    """Raised at `PodmanZeekRunner` construction when `image` is not pinned by an explicit tag or
    a `@sha256:`/`@sha512:` digest. A `:latest` (or tagless, which defaults to `:latest`) image
    silently changes the log schema Zeek emits as the image is updated out from under a growing
    archive — refused explicitly rather than run and discovered later.

    "Pinned" here means "not `:latest`", not "immutable". A minor-version tag like `:8.0`
    (`DEFAULT_ZEEK_IMAGE` below) is itself a MOVING pin: the registry retags it from, say, 8.0.9 to
    8.0.10 as patch releases land, with no signal to this code at all — this check refuses only
    the strictly worse `:latest`-shaped case, and cannot and does not prevent that slower drift.
    `ZeekResult.version` (the real `zeek --version` output from inside the running container) is
    what detects a `:8.0` pin having moved underneath a run; that detection happens downstream of
    this module, not here.
    """


# `aximprint` parses exactly these 36 Zeek log types (`docs/WORK_ORDER_zeek_runner.md` §2;
# `software` joined 2026-08-11, and eleven more — `ocsp` last, once `Q29` gave it a subject-less
# landing shape — joined 2026-08-13 via `docs/WORK_ORDER_zeek_stem_coverage.md` build step 3; all eight
# stems a TWELVE-stream vantage produces that a four-stream one never showed joined 2026-08-20 via
# `docs/WORK_ORDER_zeek_stems_wide_vantage.md` build steps 1-4, closing 43 = 36 + 7) —
# NEVER used to trim what Zeek is told
# to generate (see module docstring). Only to classify, in `ZeekResult.unrecognized`, what a
# run produced that neither this list nor `KNOWN_SKIPPED_STEMS` already accounts for.
KNOWN_PARSED_STEMS: frozenset[str] = frozenset(
    {
        "conn",
        "dhcp",
        "dns",
        "kerberos",
        "known_hosts",
        "ntlm",
        "radius",
        "rdp",
        "rfb",
        "redis",
        "websocket",
        "syslog",
        "mqtt_connect",
        "known_certs",
        "ssl",
        "dce_rpc",
        "ldap_search",
        "ldap",
        "smb_mapping",
        "smb_files",
        "ntp",
        "known_services",
        "software",
        "http",
        "ssh",
        "quic",
        "snmp",
        "tunnel",
        "smtp",
        "sip",
        "x509",
        "ocsp",
        "notice",
        "weird",
        "capture_loss",
        "stats",
    }
)

# `aximprint` explicitly skips these 7 — deliberate, documented non-parsing, distinct from a stem
# neither list has ever heard of. `pe`/`analyzer`/`reporter` joined 2026-08-13
# (`docs/WORK_ORDER_zeek_stem_coverage.md` build step 2): `pe` and `analyzer` are each declined
# with a named path back (`Q26`, `Q28` — see `ingest.zeek.plugin._SKIP`'s matching reasons, kept
# in sync by `tests/test_acquire_zeek_stem_sync.py`), not declined outright like the other five.
KNOWN_SKIPPED_STEMS: frozenset[str] = frozenset(
    {"telemetry", "loaded_scripts", "packet_filter", "files", "pe", "analyzer", "reporter"}
)

KNOWN_ZEEK_STEMS: frozenset[str] = KNOWN_PARSED_STEMS | KNOWN_SKIPPED_STEMS


def unrecognized_stems(logs: Mapping[str, Path]) -> tuple[str, ...]:
    """The produced log stems that are neither parsed nor explicitly skipped by `aximprint` —
    sorted, so the report is stable across `out_dir` listing order.
    """
    return tuple(sorted(stem for stem in logs if stem not in KNOWN_ZEEK_STEMS))


@dataclass(frozen=True)
class ZeekResult:
    """Everything the archive manifest needs to know about one Zeek run over one pcap.

    `version` is the Zeek version string (`zeek --version`, run inside the same pinned image —
    never derived from the image tag, which would be wrong for a digest-pinned image). `logs` maps
    each produced log stem to its file path, built by listing `out_dir` after the run — never a
    hardcoded set. It is a `Mapping`, always a `types.MappingProxyType` in practice: a plain `dict`
    field on an otherwise-`frozen=True` dataclass would still be mutable through
    `result.logs["x"] = ...`, defeating the point of freezing the rest of the result — the proxy
    makes that a `TypeError` too. `logs` is excluded from the generated `__hash__` (`field(hash=
    False)`) because a mapping — proxied or not — is itself unhashable; every other field, all of
    them plain scalars/tuples, still participates, so `ZeekResult` stays hashable. `unrecognized`
    is the subset of `logs`' stems outside `KNOWN_ZEEK_STEMS`. `returncode`/`stderr_tail` are
    always populated, including on a non-zero exit, a timeout (`TIMEOUT_RETURNCODE`), or the
    runner itself failing to execute at all (`RUNNER_FAILED_RETURNCODE`) — a failed run is a
    value, never an exception (`ok` below).
    """

    version: str
    logs: Mapping[str, Path] = field(hash=False)
    duration_s: float
    returncode: int
    stderr_tail: str
    bytes_in: int
    unrecognized: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """`True` only for a clean `returncode == 0` exit. A caller that only checks `logs` is
        non-empty would miss a run that failed after producing partial output.
        """
        return self.returncode == 0


@runtime_checkable
class ZeekRunner(Protocol):
    """What `harvest` needs from *some* way of running Zeek over a pcap — satisfied identically by
    `FakeZeekRunner` and `PodmanZeekRunner`, so callers never branch on which one they hold.
    """

    def run(self, pcap: Path, out_dir: Path) -> ZeekResult:
        """Run Zeek over `pcap`, writing its logs into `out_dir` (created if missing). Never
        raises for a Zeek-side failure (non-zero exit, timeout) — those come back as a `ZeekResult`
        with `ok=False`, so a multi-day pipeline can record-and-continue.
        """
        ...

    def probe(self) -> ProbeResult:
        """A structured "can this runner actually run?" answer, checked once at startup rather
        than discovered on the first pcap after an hour of extraction.
        """
        ...


@dataclass(frozen=True)
class ProbeResult:
    """The answer to "can this `ZeekRunner` actually run?", with enough detail to name which
    precondition failed: `podman` not on `PATH`, the pinned image not present locally, or
    `zeek --version` failing inside the image once it *is* present. `podman_available` reads as
    "the runtime is available" for every runner — `docker` driven through `PodmanZeekRunner`,
    or the host binary through `NativeZeekRunner` — the field name predates both.
    """

    ok: bool
    podman_available: bool
    image_present: bool
    zeek_version: str | None
    detail: str


@dataclass(frozen=True)
class ScriptPolicy:
    """Which extra Zeek scripts to load, beyond whatever the image's own default script set
    already loads (typically `local.zeek` plus the base policy scripts).

    `DEFAULT_SCRIPT_POLICY` loads `local` — see its comment for the measurement that forced that.
    It deliberately does **not** trim Zeek's log set to `aximprint`'s 28 parsed types (see module
    docstring); the cold tier is the evidence tier, and discarding a log at generation time makes
    it unrecoverable. Note that "no scripts" is *also* a trim, in the other direction, which is why
    an empty policy is not the default.

    `ScriptPolicy(scripts=())` remains available for a caller who genuinely wants Zeek's bare base
    scripts, and a caller needing more has one explicit place to say so instead of hand-editing
    argv construction.
    """

    scripts: tuple[str, ...] = ()


# Loads Zeek's site policy (`local.zeek`). NOT optional, and NOT a "trim" in either direction:
# measured 2026-08-06 against the pinned image, `zeek -r <pcap>` with no scripts produces only
# 5 of the 16 log types `aximprint` parsed at that date, while `zeek -r <pcap> local` produces
# 10 — the five it gains are `capture_loss`, `known_hosts`, `known_services`, `notice` and
# `stats`. (`aximprint` parses 28 today, `software` having joined 2026-08-11 and eleven more via
# `docs/WORK_ORDER_zeek_stem_coverage.md` build step 3 on 2026-08-13 — this measurement predates
# all of that and is not re-anchored to it; the two ratios are not the same claim, `LES-093`.)
#
# `capture_loss` and `stats` are the ONLY sources of `SensorHealthEvent`. Running without `local`
# would produce an archive with no sensor-health evidence whatsoever, and because the cold tier is
# the evidence tier, that omission would be unrecoverable after the fact.
DEFAULT_SCRIPT_POLICY = ScriptPolicy(scripts=("local",))

# Pass `-C` (ignore checksum errors) to Zeek. DEFAULT TRUE, and this is a correctness default
# rather than a convenience one.
#
# A capture taken from a mirrored VIRTUAL interface — which is what every axRange-class capture
# plane produces (`tc` mirror on a VM tap netdev -> gretap -> sink) — carries packets whose L3/L4
# checksums were never computed by the guest at all: NIC checksum offload leaves the field as a
# placeholder for the hardware to fill in, and the mirror copies the frame BEFORE that happens.
# The checksums are therefore wrong by construction, on every single packet, for reasons that have
# nothing to do with the data being corrupt.
#
# Zeek's default is to validate them, and a packet failing validation is not analyzed: it is
# recorded in `conn.log` with `history=C`, `orig_pkts=0`, `resp_pkts=0`, `conn_state=OTH`, and NO
# `service`. The run still succeeds, still writes every log, and still exits 0 — so nothing
# downstream sees an error. What it sees is a capture in which no connection ever established, no
# listener was ever proven, and therefore no role can be inferred.
#
# MEASURED, 2026-08-27 (`docs/JOURNAL_imprint_loop.md`, GAP-L3): a 10-minute, 3.31 GB capture of a
# 14-host twin produced 50,485 flows of which 100% were unanalyzable this way. The belief store
# learned all four segments and all 13 non-router hosts correctly, and then `cast` emitted an
# EMPTY TWIN — zero nodes — because not one host carried a role. That is the LES-001 false-green
# class in its purest form: a green pipeline whose output is empty for a reason no stage reports.
#
# Set `ignore_checksums=False` only for a capture taken off the wire by a hardware tap where
# checksum validity is a signal you actually intend to police.
DEFAULT_IGNORE_CHECKSUMS = True

# 15 minutes: generous for a single ~300s-window pcap (`docs/PLAN_metadata_harvester.md` §5's
# default `--window`) run through Zeek, short enough that a hung/malformed capture doesn't stall a
# multi-day pipeline run past a coffee break.
DEFAULT_TIMEOUT_S = 900.0

# Returned as `ZeekResult.returncode` when a run was killed for exceeding its timeout — the same
# convention GNU coreutils' `timeout(1)` uses, so it reads as "timed out" rather than an arbitrary
# sentinel.
TIMEOUT_RETURNCODE = 124

# Returned as `ZeekResult.returncode` when the runner itself never got as far as running Zeek at
# all: the input pcap vanished between admission and this call, `out_dir` still holds a previous
# run's logs (see `_stale_output_dir_result`), `podman`/the binary isn't on `PATH`, a permission
# error, or `OSError(EAGAIN)` from `fork()` under memory pressure. Distinct from a Zeek-internal
# failure (an ordinary non-zero `returncode` from the process actually running) and from
# `TIMEOUT_RETURNCODE` — this is "could not even start", matching the family of exit codes
# `docker run`/`podman run` themselves use for "container failed to run" (this project's own
# choice of value, not measured against podman's own convention).
RUNNER_FAILED_RETURNCODE = 125

# `probe()`'s own preconditions (`podman --version`, `podman image exists`) are a fast local
# check, not a Zeek run — using the full `DEFAULT_TIMEOUT_S` (900s) here would let a hung/wedged
# `podman` stall startup for up to 15 minutes, defeating the entire point of a startup probe.
PROBE_TIMEOUT_S = 10.0

# Live-measured 2026-08-06 against the actual pinned image (see the PROVENANCE comment above
# `_SCHEMAS` below for the full verification record): `docker.io/zeek/zeek:8.0` resolves to Zeek
# 8.0.9 at digest `sha256:51a0d106…`. Exported so a caller constructing `PodmanZeekRunner` has a
# ready-made, already-pinned default instead of hand-typing (and possibly mistyping) the tag.
DEFAULT_ZEEK_IMAGE = "docker.io/zeek/zeek:8.0"

_STDERR_TAIL_CHARS = 4000

# A single shared empty, immutable `logs` value for every failed-before-Zeek-ran `ZeekResult` —
# avoids allocating (and forgetting to proxy) a fresh empty mapping at each such call site.
_EMPTY_LOGS: Mapping[str, Path] = MappingProxyType({})


def _tail(text: str | None) -> str:
    """The last `_STDERR_TAIL_CHARS` of `text`, so a huge stderr stream from a misbehaving Zeek run
    never bloats a manifest record — empty string for `None`/empty input.
    """
    if not text:
        return ""
    return text[-_STDERR_TAIL_CHARS:]


def _exc_tail(exc: BaseException, subject: str) -> str:
    """`LES-039`: a raw exception's `str()` — `OSError`/`FileNotFoundError` messages embed full
    filesystem paths — must never reach `stderr_tail`, which flows into the archive manifest. Only
    the exception's type name plus a bare, non-path `subject` (a binary name like `"podman"`, or a
    bare filename like `pcap.name` — never a full path) is safe to record here.
    """
    return _tail(f"{type(exc).__name__}: {subject}")


def _decode_capture(data: bytes | str | None) -> str:
    """Normalize a `subprocess.TimeoutExpired.stdout`/`.stderr` value to `str`: `text=True` on the
    `subprocess.run()` call already makes these `str`, but `TimeoutExpired` itself does not
    enforce that on whatever a caller constructs it with (this module's own tests included), so
    decode defensively rather than assume.
    """
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _collect_logs(out_dir: Path) -> Mapping[str, Path]:
    """Every `*.log` file in `out_dir`, keyed by stem — the *only* way this module decides what a
    Zeek run produced. Never assume a fixed set of stems (module docstring). Returns an immutable
    `MappingProxyType` — see `ZeekResult.logs`'s docstring for why.
    """
    return MappingProxyType({p.stem: p for p in sorted(out_dir.glob("*.log"))})


def _missing_pcap_result(pcap: Path, version: str, started: float) -> ZeekResult:
    """A failed `ZeekResult` for a `pcap` that does not exist (or vanished) at run time — shared by
    `FakeZeekRunner` and `PodmanZeekRunner` so the two never diverge on this ordinary, expected
    production path (a window whose extraction never landed a file). Never raises `FileNotFoundError`
    — see the module docstring's "a failed run is a value" discipline.
    """
    return ZeekResult(
        version=version,
        logs=_EMPTY_LOGS,
        duration_s=time.monotonic() - started,
        returncode=RUNNER_FAILED_RETURNCODE,
        stderr_tail=_tail(f"FileNotFoundError: {pcap.name}"),
        bytes_in=0,
    )


def _stale_output_dir_result(out_dir: Path, version: str, started: float) -> ZeekResult:
    """A failed `ZeekResult` when `out_dir` already contains `*.log` files before this run has
    written anything — reusing an `out_dir` across two different pcaps would otherwise report a
    previous run's stale logs as this run's, corrupting provenance. Checked before either runner
    writes anything, so a caller must pass a fresh `out_dir` per pcap rather than this module
    silently merging (or worse, fully attributing) two runs' output.
    """
    return ZeekResult(
        version=version,
        logs=_EMPTY_LOGS,
        duration_s=time.monotonic() - started,
        returncode=RUNNER_FAILED_RETURNCODE,
        stderr_tail=_tail(f"StaleOutputDirError: {out_dir.name!r} already contains *.log files"),
        bytes_in=0,
    )


# ---------------------------------------------------------------------------------------------
# FakeZeekRunner — production code (module docstring), not a test double.
# ---------------------------------------------------------------------------------------------


def _schema(fields: str, types: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Build a (fields, types) schema pair from space-separated strings — matches how the
    `#fields`/`#types` header lines read once tab-split, and is far less error-prone to transcribe
    than nested tuple literals.
    """
    return tuple(fields.split()), tuple(types.split())


# PROVENANCE, and why it is worth stating precisely. (Updated 2026-08-06 — an earlier version of
# this comment described a `docker.io/zeek/zeek:7.0` / Zeek 7.0.9 pin with a 21-field `conn`
# lacking `ip_proto`; that had gone stale relative to both the schema table below and the image
# this project actually pins. Never delete the corrected record below when it, in turn, goes
# stale — supersede it in place, the same discipline `docs/LESSONS.md` uses.)
#
# The pinned image is `DEFAULT_ZEEK_IMAGE` = `docker.io/zeek/zeek:8.0`, which resolves to Zeek
# **8.0.9** at digest `sha256:51a0d106…`. `conn`, `dns`, `ssl`, `ntp`, `weird` and `packet_filter`
# are transcribed directly from a live verification run of that image, 2026-08-06, over a real
# extracted pcap, with `DEFAULT_SCRIPT_POLICY` (`local`) loaded — the same policy this module
# actually applies. They are ground truth for the version this project pins. `conn` is 22/22
# fields, WITH `ip_proto` (the schema table below already carried this shape; only this comment
# had drifted).
#
# The remaining 13 stems — `capture_loss`, `dce_rpc`, `files`, `kerberos`, `known_hosts`,
# `ldap_search`, `loaded_scripts`, `smb_files`, `smb_mapping`, `stats`, `telemetry`,
# `known_services` and `notice` — were originally transcribed from `aximprint`'s reference
# capture, produced by a Zeek version different from what this project pins. The same 2026-08-06
# live verification cross-checked all 13 against the pinned 8.0 image and confirmed every one
# matches.
#
# `dhcp` is the sole schema below with no live cross-check at all, from either capture: no DHCP
# traffic was present in the verification pcap. It remains transcribed from Zeek's documented
# default `dhcp.log` shape (see the comment at its `_SCHEMAS` entry) rather than an observed one.
#
# Stems that did not appear anywhere in the 2026-08-06 verification capture — so their schemas,
# while cross-checked above against `aximprint`'s reference, were not independently re-observed
# against the pinned image by this specific run: `dhcp`, `kerberos`, `dce_rpc`, `ldap_search`,
# `smb_mapping`, `smb_files`, `files`. None of DHCP, SMB, Kerberos, LDAP, or file-extraction
# traffic was present in that capture; a future verification run with such traffic would let this
# comment shrink that list.
#
# Consequence for H-5: the converter MUST derive its Parquet schema from each log's own
# `#fields`/`#types` header at runtime, never from this table. This table exists to make the fake
# realistic, not to define what production will encounter.
#
# Header shapes for every stem `aximprint` knows about (`KNOWN_ZEEK_STEMS`, 35/35 today —
# `tests/acquire/test_zeek_runner.py` asserts this; this comment said 20/20 through the
# `software` round (2026-08-11), then 24/24 through build step 2's `pe`/`analyzer`/`reporter`
# additions AND, for a full day, through build step 3's eleven newly-PARSED stems too (both
# 2026-08-13, `docs/WORK_ORDER_zeek_stem_coverage.md`) — neither growing this count to match,
# `LES-087`'s own shape, recurring in this same comment a second time). Only `dhcp` carries no captured
# reference at all — no DHCP traffic was ever present in any capture this table draws from — so
# it gets its own entry below instead, from Zeek's documented default shape rather than an
# observed one (see the comment at that entry); every other entry here, including
# `pe`/`analyzer`/`reporter` (from the 2026-08-13 `/tmp/aximprint-zeek-test/` baseline — see
# their own comment below), is transcribed from a real Zeek run. `_FALLBACK_SCHEMA`, further
# below, is reserved for a stem OUTSIDE `KNOWN_ZEEK_STEMS` entirely (e.g. a test deliberately
# exercising `unrecognized_stems`) — every stem `aximprint` actually knows about has a real
# entry here, never the two-column fallback.
_SCHEMAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "capture_loss": _schema(
        "ts ts_delta peer gaps acks percent_lost",
        "time interval string count count double",
    ),
    "conn": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p proto service duration orig_bytes resp_bytes conn_state local_orig local_resp missed_bytes history orig_pkts orig_ip_bytes resp_pkts resp_ip_bytes tunnel_parents ip_proto",
        "time string addr port addr port enum string interval count count string bool bool count string count count count count set[string] count",
    ),
    "dce_rpc": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p rtt named_pipe endpoint operation",
        "time string addr port addr port interval string string string",
    ),
    "dns": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p proto trans_id rtt query qclass qclass_name qtype qtype_name rcode rcode_name AA TC RD RA Z answers TTLs rejected",
        "time string addr port addr port enum count interval string count string count string count string bool bool bool bool count vector[string] vector[interval] bool",
    ),
    "files": _schema(
        "ts fuid uid id.orig_h id.orig_p id.resp_h id.resp_p source depth analyzers mime_type "
        "filename duration local_orig is_orig seen_bytes total_bytes missing_bytes overflow_bytes "
        "timedout parent_fuid md5 sha1 sha256 extracted extracted_cutoff extracted_size",
        "time string string addr port addr port string count set[string] string string interval "
        "bool bool count count count count bool string string string string string bool count",
    ),
    "kerberos": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p request_type client service success "
        "error_msg from till cipher forwardable renewable client_cert_subject client_cert_fuid "
        "server_cert_subject server_cert_fuid",
        "time string addr port addr port string string string bool string time time string bool "
        "bool string string string string",
    ),
    "known_hosts": _schema(
        "ts host",
        "time addr",
    ),
    "ldap_search": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p message_id scope deref_aliases base_object "
        "result_count result diagnostic_message filter attributes",
        "time string addr port addr port int string string string count string string string "
        "vector[string]",
    ),
    "loaded_scripts": _schema(
        "name",
        "string",
    ),
    "ntp": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p version mode stratum poll precision root_delay root_disp ref_id ref_time org_time rec_time xmt_time num_exts",
        "time string addr port addr port count count count interval interval interval interval string time time time time count",
    ),
    "packet_filter": _schema(
        "ts node filter init success failure_reason",
        "time string string bool bool string",
    ),
    "smb_files": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p fuid action path name size prev_name "
        "times.modified times.accessed times.created times.changed",
        "time string addr port addr port string enum string string count string time time time "
        "time",
    ),
    "smb_mapping": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p path service native_file_system share_type",
        "time string addr port addr port string string string string",
    ),
    "ssl": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p version cipher curve server_name resumed last_alert next_protocol established ssl_history cert_chain_fps client_cert_chain_fps sni_matches_cert validation_status",
        "time string addr port addr port string string string string bool string string bool string vector[string] vector[string] bool string",
    ),
    "stats": _schema(
        "ts peer mem pkts_proc bytes_recv pkts_dropped pkts_link pkt_lag pkts_filtered events_proc events_queued active_tcp_conns active_udp_conns active_icmp_conns tcp_conns udp_conns icmp_conns timers active_timers files active_files dns_requests active_dns_requests reassem_tcp_size reassem_file_size reassem_frag_size reassem_unknown_size",
        "time string count count count count count interval count count count count count count count count count count count count count count count count count count count",
    ),
    "telemetry": _schema(
        "ts peer metric_type name labels label_values value",
        "time string string string vector[string] vector[string] double",
    ),
    "weird": _schema(
        "ts uid id.orig_h id.orig_p id.resp_h id.resp_p name addl notice peer source",
        "time string addr port addr port string string bool string string",
    ),
}

_SCHEMAS["known_services"] = _schema(
    "ts host port_num port_proto service",
    "time addr port enum set[string]",
)

# `software` joined KNOWN_PARSED_STEMS on 2026-08-11 (a partial D2 ruling,
# `docs/WORK_ORDER_zeek_stem_coverage.md`), so it needs an entry here or `FakeZeekRunner` would
# hand it the two-column `_FALLBACK_SCHEMA` and every test built on the fake would exercise a
# parser that can never fire -- the failure that work order's amended build step 3 exists to
# prevent (`LES-087`). Transcribed from the 2026-08-11 capture's own header, like its neighbours.
_SCHEMAS["software"] = _schema(
    "ts host host_p software_type name version.major version.minor version.minor2"
    " version.minor3 version.addl unparsed_version",
    "time addr port enum string count count count count string string",
)

_SCHEMAS["notice"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p fuid file_mime_type file_desc proto note msg sub src dst p n peer_descr actions email_dest suppress_for remote_location.country_code remote_location.region remote_location.city remote_location.latitude remote_location.longitude",
    "time string addr port addr port string string string enum enum string string addr addr port count string set[enum] set[string] interval string string string double double",
)

# `dhcp` carries no *captured* reference — the reference run had no DHCP traffic. Its field list
# below is transcribed from Zeek's documented default `dhcp.log`, and is marked as such: it is a
# documented shape, not an observed one. Recorded rather than left on `_FALLBACK_SCHEMA`, because a
# fallback that omits `assigned_addr`/`mac` makes `aximprint`'s `_dhcp` emit nothing, and a fake
# whose output silently yields zero events is worse than no fake at all.
_SCHEMAS["dhcp"] = _schema(
    "ts uids client_addr server_addr mac host_name client_fqdn domain requested_addr "
    "assigned_addr lease_time client_message server_message msg_types duration",
    "time set[string] addr addr string string string string addr addr interval string string "
    "vector[string] interval",
)

# `pe`, `analyzer` and `reporter` join `_SCHEMAS` on being declared skipped, not parsed
# (`docs/WORK_ORDER_zeek_stem_coverage.md` build step 2) — `KNOWN_ZEEK_STEMS <= set(_SCHEMAS)`
# (`LES-087`, `tests/acquire/test_zeek_runner.py`) does not care whether a stem is parsed or
# skipped, only that every stem `aximprint` has ANY opinion about gets a real fake-runner header
# instead of silently falling back to `_FALLBACK_SCHEMA`'s two columns — exactly the failure
# this work order's own step 2 text claimed a skip declaration does not need to guard against,
# and was wrong about. All three are transcribed from the 2026-08-13 read of
# `/tmp/aximprint-zeek-test/`: `analyzer` and `reporter` from the same headers
# `docs/OPEN_QUESTIONS.md` `Q24`'s "Headers verified" section records; `pe` from one of the two
# window directories it actually appears in (its 2/12 presence) — a real, observed header, not
# the documented-shape guesswork `dhcp`, above, is stuck with.
_SCHEMAS["analyzer"] = _schema(
    "ts analyzer_kind analyzer_name uid fuid id.orig_h id.orig_p id.resp_h id.resp_p proto"
    " failure_reason failure_data",
    "time string string string string addr port addr port enum string string",
)

_SCHEMAS["reporter"] = _schema(
    "ts level message location",
    "time enum string string",
)

_SCHEMAS["pe"] = _schema(
    "ts id machine compile_ts os subsystem is_exe is_64bit uses_aslr uses_dep"
    " uses_code_integrity uses_seh has_import_table has_export_table has_cert_table"
    " has_debug_data section_names",
    "time string string time string string bool bool bool bool bool bool bool bool bool bool"
    " vector[string]",
)

# All eleven of `docs/WORK_ORDER_zeek_stem_coverage.md` step 3's newly-parsed stems now land:
# `ocsp` joined 2026-08-13 (`docs/WORK_ORDER_subject_less_facts.md`, closing `Q29`), the last of
# the eleven, once the store gained a channel for a fact with no subject (see its own entry,
# below, for why it alone was excluded from this round's first ten). Every entry below is
# transcribed from the SAME 2026-08-13 read of `/tmp/aximprint-zeek-test/` that `pe`/`analyzer`/
# `reporter` above draw from — real `#fields`/`#types` headers, not documented-shape guesswork
# (`LES-087`, `LES-090`): all eleven stems were present and header-identical across every one of
# the 12 windows (verified by hashing each window's own `#fields` line).
_SCHEMAS["ntlm"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p username hostname domainname"
    " server_nb_computer_name server_dns_computer_name server_tree_name success",
    "time string addr port addr port string string string string string string bool",
)

# `radius` joined 2026-08-20 (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` build step 1) — the
# first of the eight stems a TWELVE-stream vantage produces that the four-stream baseline never
# showed, so this header is transcribed from `/home/rocky/harvest-corpapcn-10d/zeek/` instead,
# verified identical across all 17 windows that produced the stem (`LES-087`, `LES-090`).
_SCHEMAS["radius"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p username mac framed_addr tunnel_client"
    " connect_info reply_msg result ttl",
    "time string addr port addr port string string addr string string string string interval",
)

# The other seven stems of `docs/WORK_ORDER_zeek_stems_wide_vantage.md` (build steps 2-4,
# 2026-08-20). Same discipline as `radius` above: every header transcribed from
# `/home/rocky/harvest-corpapcn-10d/zeek/` and verified identical across every window that
# produced the stem (`LES-087`, `LES-090`) — 54, 522, 7, 1, 1, 58 and 6 windows respectively.
_SCHEMAS["rdp"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p cookie result security_protocol"
    " client_channels keyboard_layout client_build client_name client_dig_product_id"
    " desktop_width desktop_height requested_color_depth cert_type cert_count cert_permanent"
    " encryption_level encryption_method",
    "time string addr port addr port string string string vector[string] string string string"
    " string count count string string count bool string string",
)

_SCHEMAS["known_certs"] = _schema(
    "ts host port_num subject issuer_subject serial",
    "time addr port string string string",
)

_SCHEMAS["rfb"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p client_major_version client_minor_version"
    " server_major_version server_minor_version authentication_method auth share_flag"
    " desktop_name width height",
    "time string addr port addr port string string string string string bool bool string count"
    " count",
)

_SCHEMAS["redis"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p cmd.name cmd.key cmd.value success"
    " reply.value",
    "time string addr port addr port string string string bool string",
)

_SCHEMAS["websocket"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p host uri user_agent subprotocol"
    " client_protocols server_extensions client_extensions",
    "time string addr port addr port string string string string vector[string] vector[string]"
    " vector[string]",
)

_SCHEMAS["syslog"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p proto facility severity message",
    "time string addr port addr port enum string string string",
)

_SCHEMAS["mqtt_connect"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p proto_name proto_version client_id"
    " connect_status will_topic will_payload",
    "time string addr port addr port string string string string string string",
)

_SCHEMAS["ldap"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p message_id version opcode result"
    " diagnostic_message object argument",
    "time string addr port addr port int int string string string string string",
)

_SCHEMAS["http"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p trans_depth method host uri referrer"
    " version user_agent origin request_body_len response_body_len status_code status_msg"
    " info_code info_msg tags username password proxied orig_fuids orig_filenames"
    " orig_mime_types resp_fuids resp_filenames resp_mime_types",
    "time string addr port addr port count string string string string string string string"
    " count count count string count string set[enum] string string set[string] vector[string]"
    " vector[string] vector[string] vector[string] vector[string] vector[string]",
)

_SCHEMAS["ssh"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p version auth_success auth_attempts"
    " direction client server cipher_alg mac_alg compression_alg kex_alg host_key_alg host_key"
    " remote_location.country_code remote_location.region remote_location.city"
    " remote_location.latitude remote_location.longitude",
    "time string addr port addr port count bool count enum string string string string string"
    " string string string string string string double double",
)

_SCHEMAS["quic"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p version client_initial_dcid client_scid"
    " server_scid server_name client_protocol history",
    "time string addr port addr port string string string string string string string",
)

_SCHEMAS["snmp"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p duration version community get_requests"
    " get_bulk_requests get_responses set_requests display_string up_since",
    "time string addr port addr port interval string string count count count count string time",
)

_SCHEMAS["tunnel"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p tunnel_type action",
    "time string addr port addr port enum enum",
)

_SCHEMAS["smtp"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p trans_depth helo mailfrom rcptto date from"
    " to cc reply_to msg_id in_reply_to subject x_originating_ip first_received second_received"
    " last_reply path user_agent tls fuids is_webmail",
    "time string addr port addr port count string string set[string] string string set[string]"
    " set[string] string string string string addr string string string vector[addr] string"
    " bool vector[string] bool",
)

_SCHEMAS["sip"] = _schema(
    "ts uid id.orig_h id.orig_p id.resp_h id.resp_p trans_depth method uri date request_from"
    " request_to response_from response_to reply_to call_id seq subject request_path"
    " response_path user_agent status_code status_msg warning request_body_len"
    " response_body_len content_type",
    "time string addr port addr port count string string string string string string string"
    " string string string string vector[string] vector[string] string count string string"
    " count count string",
)

_SCHEMAS["x509"] = _schema(
    "ts fingerprint certificate.version certificate.serial certificate.subject"
    " certificate.issuer certificate.not_valid_before certificate.not_valid_after"
    " certificate.key_alg certificate.sig_alg certificate.key_type certificate.key_length"
    " certificate.exponent certificate.curve san.dns san.uri san.email san.ip"
    " basic_constraints.ca basic_constraints.path_len host_cert client_cert",
    "time string count string string string time time string string string count string string"
    " vector[string] vector[string] vector[string] vector[addr] bool count bool bool",
)

# `ocsp` is the eleventh admit, and the one that took a separate work order
# (`docs/WORK_ORDER_subject_less_facts.md`, `Q29`) to land: `ocsp.log` names no connection at all
# (no `id.orig_h`/`id.resp_h`, only the response's own `id` -- a certificate-status hash), so it
# could not become a `ServiceEvent` the way the other ten did, and the store had no channel for a
# fact with no subject until this round built one. Transcribed from the same 2026-08-13 baseline
# read as its ten siblings above -- present and header-identical in all 12 windows.
_SCHEMAS["ocsp"] = _schema(
    "ts id hashAlgorithm issuerNameHash issuerKeyHash serialNumber certStatus revoketime"
    " revokereason thisUpdate nextUpdate",
    "time string string string string string string time string time time",
)

# Used for any stem `_SCHEMAS` has no captured reference for (an arbitrary stem a
# caller/test asks `FakeZeekRunner` to also produce, e.g. to exercise `unrecognized_stems`).
_FALLBACK_SCHEMA = _schema("ts uid", "time string")

# Zeek writes the `#separator` line's *value* as the literal text `\x09`, not an actual tab byte —
# every other header line is genuinely tab-separated. Confirmed against
# `/home/rocky/aximprint/metadata/zeek/conn.log` byte-for-byte (`od -c`).
_SEPARATOR_LINE = "#separator \\x09"


# A few fields need values that are *semantically* usable, not merely well-typed. A consumer's
# parser may require a particular shape before it will emit anything at all: `aximprint`'s `_dns`
# binds a hostname to an answer only when the answer parses as a real IP address, so a
# type-plausible `-` for `answers` (declared `vector[addr]`) produces a structurally perfect log
# that yields **zero** events. Measured: running `aximprint`'s own ZeekPlugin over this fake's
# output reported `dns 3 rows -> 0 events [parser and source schema likely disagree]`.
#
# That is exactly the failure this fake exists to prevent downstream, so these overrides are keyed
# on field NAME and take precedence over the type-driven default below.
_FIELD_VALUES: dict[str, Callable[[int], str]] = {
    "answers": lambda i: f"10.1.0.{i % 250 + 1}",
    "query": lambda i: f"host{i}.example.test",
    "TTLs": lambda i: "300.000000",
    "assigned_addr": lambda i: f"10.2.0.{i % 250 + 1}",
    "requested_addr": lambda i: f"10.2.0.{i % 250 + 1}",
    "mac": lambda i: f"00:00:5e:00:53:{i % 256:02x}",
    "host_name": lambda i: f"host{i}",
    "client": lambda i: f"user{i}@EXAMPLE.TEST",
    "host": lambda i: f"10.3.0.{i % 250 + 1}",
    "server_name": lambda i: f"host{i}.example.test",
}


def _sample_value(type_token: str, row_index: int, field_name: str = "") -> str:
    """A deterministic TSV cell for `type_token` — good enough to make the file a structurally
    valid Zeek log, and, for the fields in `_FIELD_VALUES`, semantically usable by a consumer's
    parser as well.
    """
    override = _FIELD_VALUES.get(field_name)
    if override is not None:
        return override(row_index)
    if type_token == "time":
        return f"{1700000000 + row_index}.000000"
    if type_token == "interval":
        return "0.000100"
    if type_token == "addr":
        return "10.0.0.1" if row_index % 2 == 0 else "10.0.0.2"
    if type_token == "port":
        return str(50000 + row_index)
    if type_token in ("count", "int"):
        return str(row_index)
    if type_token == "double":
        return "0.0"
    if type_token == "bool":
        return "T"
    if type_token == "enum":
        return "tcp"
    if type_token.startswith(("vector[", "set[")):
        return "-"
    if type_token == "string":
        return f"sample{row_index}"
    return "-"


def _write_zeek_log(path: Path, stem: str, fields: Sequence[str], types: Sequence[str], row_count: int) -> None:
    """Write one Zeek-shaped TSV log: the full 8-line header (`#separator`, `#set_separator`,
    `#empty_field`, `#unset_field`, `#path`, `#open`, `#fields`, `#types` — the same tag order a
    real Zeek log carries) followed by `row_count` synthetic data rows.
    """
    open_ts = datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")
    lines = [
        _SEPARATOR_LINE,
        "#set_separator\t,",
        "#empty_field\t(empty)",
        "#unset_field\t-",
        f"#path\t{stem}",
        f"#open\t{open_ts}",
        "#fields\t" + "\t".join(fields),
        "#types\t" + "\t".join(types),
    ]
    lines.extend(
        "\t".join(_sample_value(t, i, f) for t, f in zip(types, fields, strict=True))
        for i in range(row_count)
    )
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


class FakeZeekRunner:
    """A `ZeekRunner` that writes small, valid Zeek TSV logs without needing Zeek or `podman` —
    production code (module docstring), so every module built on top of `ZeekRunner` is testable on
    a host with neither, which is this host today. Deliberately mirrors `PodmanZeekRunner`'s
    observable behavior at every point the two must agree (see the shared conformance suite in
    `tests/acquire/test_zeek_runner.py`): a missing pcap, a stale `out_dir`, a configurable failure, and a
    quiet window that produces no logs at all are all things H-4/H-5/H-7 must be able to exercise
    against this fake exactly as they would against the real runner.

    `stems` (deduplicated, order preserved; default `("conn",)`) is which log stems to write;
    `extra_stems` adds more on top, for the common case of "the default plus a couple more" without
    having to repeat `"conn"`. Pass `stems=()` (with no `extra_stems`) to model a quiet window that
    Zeek processes successfully but that produces no logs at all — the common case on this
    appliance (`LES-035`'s sibling: a quiet window is ordinary, not exceptional, at every layer of
    this pipeline). Each stem's header uses the real captured schema in `_SCHEMAS` when one exists,
    else `_FALLBACK_SCHEMA` — a caller can pass a stem outside `KNOWN_ZEEK_STEMS` deliberately, to
    exercise `ZeekResult.unrecognized`.

    `returncode`/`stderr_tail` let a test construct a fake that fails: the real `PodmanZeekRunner`
    can fail mid-run and still have written partial output, so this fake writes `stems` regardless
    of `returncode` — a caller modeling "failed with partial output" passes a non-zero `returncode`
    together with a non-empty `stems`, and a caller modeling "failed with nothing written" combines
    it with `stems=()`. Without this knob, H-4's single most important behavior — a failed Zeek run
    must not release its pcap, so the window stays resident and retryable — could never be exercised
    against the fake at all, since every prior run of it unconditionally succeeded.
    """

    def __init__(
        self,
        *,
        version: str = "5.0.9-fake",
        stems: Iterable[str] = ("conn",),
        extra_stems: Iterable[str] = (),
        rows_per_log: int = 3,
        returncode: int = 0,
        stderr_tail: str = "",
    ) -> None:
        self.version = version
        self.stems = tuple(dict.fromkeys((*stems, *extra_stems)))  # dedup, preserve order
        self.rows_per_log = rows_per_log
        self.returncode = returncode
        self.stderr_tail = stderr_tail

    def run(self, pcap: Path, out_dir: Path) -> ZeekResult:
        started = time.monotonic()
        out_dir.mkdir(parents=True, exist_ok=True)

        if any(out_dir.glob("*.log")):
            return _stale_output_dir_result(out_dir, self.version, started)
        if not pcap.exists():
            return _missing_pcap_result(pcap, self.version, started)
        bytes_in = pcap.stat().st_size

        for stem in self.stems:
            fields, types = _SCHEMAS.get(stem, _FALLBACK_SCHEMA)
            _write_zeek_log(out_dir / f"{stem}.log", stem, fields, types, self.rows_per_log)

        logs = _collect_logs(out_dir)
        return ZeekResult(
            version=self.version,
            logs=logs,
            duration_s=time.monotonic() - started,
            returncode=self.returncode,
            stderr_tail=self.stderr_tail,
            bytes_in=bytes_in,
            unrecognized=unrecognized_stems(logs),
        )

    def probe(self) -> ProbeResult:
        """Always available — nothing external to check."""
        return ProbeResult(
            ok=True,
            podman_available=True,
            image_present=True,
            zeek_version=self.version,
            detail="FakeZeekRunner: always available, no podman/Zeek involved",
        )


# ---------------------------------------------------------------------------------------------
# PodmanZeekRunner — drives the real Zeek, containerized.
# ---------------------------------------------------------------------------------------------


def _require_pinned_image(image: str) -> None:
    """Refuse an `image` that isn't pinned by an explicit tag or a digest. See
    `PinnedImageRequiredError`.
    """
    if "@sha256:" in image or "@sha512:" in image:
        return  # digest-pinned: immune to a moving tag regardless of any trailing tag text.

    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon <= last_slash:
        raise PinnedImageRequiredError(
            f"zeek image {image!r} has no explicit tag (defaults to ':latest'): refusing, because "
            "a moving image silently changes the Zeek log schema under a growing archive — pin an "
            "explicit version tag (e.g. ':5.2.1') or a '@sha256:' digest"
        )
    tag = image[last_colon + 1 :]
    if tag == "latest":
        raise PinnedImageRequiredError(
            f"zeek image {image!r} is pinned to ':latest': refusing, because a moving image "
            "silently changes the Zeek log schema under a growing archive — pin an explicit "
            "version tag (e.g. ':5.2.1') or a '@sha256:' digest instead"
        )


def _default_container_name() -> str:
    return f"xtract-zeek-{uuid.uuid4().hex[:12]}"


_VERSION_RE = re.compile(r"version\s+(\S+)")


def _parse_zeek_version(stdout: str) -> str:
    """Extract the version token from `zeek --version` output (e.g. `"zeek version 5.0.9-105"`) —
    empty string if the output doesn't match the expected shape.
    """
    match = _VERSION_RE.search(stdout)
    return match.group(1) if match else ""


class PodmanZeekRunner:
    """A `ZeekRunner` that drives real Zeek inside a pinned `podman` container.

    `image` must be pinned by an explicit tag or a `@sha256:`/`@sha512:` digest
    (`PinnedImageRequiredError` otherwise — see module docstring). Every `podman run` invocation
    this class makes passes `--pull=never`: pulling is a gated, network-touching action the
    orchestrator performs once, deliberately (`docs/WORK_ORDER_zeek_runner.md` §4) — this runner
    never triggers one implicitly.
    """

    def __init__(
        self,
        image: str,
        *,
        podman_bin: str = "podman",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        script_policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
        ignore_checksums: bool = DEFAULT_IGNORE_CHECKSUMS,
        container_name_factory: Callable[[], str] = _default_container_name,
        user_args: Sequence[str] = ("--userns=keep-id",),
    ) -> None:
        _require_pinned_image(image)
        self.image = image
        self.podman_bin = podman_bin
        self.timeout_s = timeout_s
        self.script_policy = script_policy
        self.ignore_checksums = ignore_checksums
        self.container_name_factory = container_name_factory
        #: How the container's user is mapped onto the invoking one, so the logs land owned by
        #: that user (see `_run_argv`). Podman's spelling is the default; `docker` has no
        #: `--userns=keep-id` and takes `("--user", "<uid>:<gid>")` instead — the only argv
        #: difference between the two runtimes this runner is driven with (`ingest/pcap`).
        self.user_args = tuple(user_args)
        self._version: str | None = None  # cached after the first successful `zeek --version`.
        # Guards the lazy fetch-and-cache of `self._version` in `run()`: H-4 runs M worker threads
        # sharing one `PodmanZeekRunner`, and without this lock N of them can all observe
        # `self._version is None` simultaneously on the very first pcap and each spawn its own
        # `zeek --version` container concurrently — wasted work at best, and a data race on
        # `self._version` at worst. `probe()` resolves the version once, up front, precisely to
        # make the lazy path in `run()` a rare cold-start case rather than the common one.
        self._version_lock = threading.Lock()

    def _run_argv(self, pcap: Path, out_dir: Path, container_name: str) -> list[str]:
        """The exact argv for the Zeek run itself — the contract with the container runtime
        (`docs/WORK_ORDER_zeek_runner.md` §3, "Assert the constructed argv exactly").

        `:Z` (not `:z`) on both bind mounts: SELinux-enforcing hosts (this one, Rocky) refuse to let
        the container read/write a bind mount without a relabel, and `:Z` (private label) is correct
        here since neither the pcap nor `out_dir` is shared with another container.
        `--userns=keep-id`: output files land owned by the invoking user, not root — root-owned
        logs would break the reaper (H-3).

        `-C` (ignore checksum errors) precedes `-r` when `ignore_checksums` is set — see
        `DEFAULT_IGNORE_CHECKSUMS` for why that is the default and what silently breaks without it.
        """
        return [
            self.podman_bin,
            "run",
            "--rm",
            *self.user_args,
            "--pull=never",
            "--name",
            container_name,
            "-v",
            f"{pcap}:/in.pcap:ro,Z",
            "-v",
            f"{out_dir}:/out:Z",
            "-w",
            "/out",
            self.image,
            "zeek",
            *(("-C",) if self.ignore_checksums else ()),
            "-r",
            "/in.pcap",
            *self.script_policy.scripts,
        ]

    def _version_argv(self) -> list[str]:
        return [self.podman_bin, "run", "--rm", "--pull=never", self.image, "zeek", "--version"]

    def _fetch_version(self) -> tuple[str, int, str]:
        """Run `zeek --version` inside the pinned image. Returns `("", returncode, stderr_tail)`
        on any failure (non-zero exit, timeout, unparseable output, or the runner failing to
        execute at all — `podman` off `PATH`, a permission error, `OSError(EAGAIN)` from `fork()`
        under memory pressure) — never raises, so a version failure surfaces as an ordinary failed
        `ZeekResult`/`ProbeResult` (see `run()`/`probe()`), not a crash.
        """
        try:
            proc = subprocess.run(
                self._version_argv(), capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except subprocess.TimeoutExpired:
            return "", TIMEOUT_RETURNCODE, _tail(f"zeek --version timed out after {self.timeout_s}s")
        except OSError as exc:
            return "", RUNNER_FAILED_RETURNCODE, _exc_tail(exc, self.podman_bin)
        if proc.returncode != 0:
            return "", proc.returncode, _tail(proc.stderr)
        version = _parse_zeek_version(proc.stdout)
        if not version:
            return "", proc.returncode, _tail(f"could not parse zeek version from stdout: {proc.stdout!r}")
        return version, 0, ""

    def _kill_container(self, container_name: str) -> None:
        """Best-effort cleanup after a timeout: `subprocess.run(..., timeout=...)` only kills our
        local `podman run` client process, which does not reliably stop the container itself
        (rootless `podman`'s conmon/container process isn't guaranteed to be in the same process
        group). `podman rm -f <name>` is the reliable way to actually stop and remove it. Swallowed
        deliberately (`LES-042`): this already-failed run must still return its `ZeekResult`, and a
        cleanup failure (container already gone, `podman` itself wedged) must never mask that.
        """
        with contextlib.suppress(Exception):
            subprocess.run(
                [self.podman_bin, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

    def run(self, pcap: Path, out_dir: Path) -> ZeekResult:
        started = time.monotonic()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            stale = any(out_dir.glob("*.log"))
        except OSError as exc:
            return ZeekResult(
                version=self._version or "",
                logs=_EMPTY_LOGS,
                duration_s=time.monotonic() - started,
                returncode=RUNNER_FAILED_RETURNCODE,
                stderr_tail=_exc_tail(exc, out_dir.name),
                bytes_in=0,
            )
        if stale:
            return _stale_output_dir_result(out_dir, self._version or "", started)

        try:
            bytes_in = pcap.stat().st_size
        except OSError as exc:
            return ZeekResult(
                version=self._version or "",
                logs=_EMPTY_LOGS,
                duration_s=time.monotonic() - started,
                returncode=RUNNER_FAILED_RETURNCODE,
                stderr_tail=_exc_tail(exc, pcap.name),
                bytes_in=0,
            )

        version = self._version
        if version is None:
            # Guard the lazy fetch: H-4's worker threads can all reach this on the very first
            # pcap concurrently (see the `_version_lock` comment in `__init__`). Re-check inside
            # the lock — another thread may have already resolved and cached it while this one
            # was waiting to acquire it.
            with self._version_lock:
                version = self._version
                if version is None:
                    version, rc, stderr_tail = self._fetch_version()
                    if not version:
                        return ZeekResult(
                            version="",
                            logs=_EMPTY_LOGS,
                            duration_s=time.monotonic() - started,
                            returncode=rc,
                            stderr_tail=stderr_tail,
                            bytes_in=bytes_in,
                        )
                    self._version = version

        container_name = self.container_name_factory()
        argv = self._run_argv(pcap, out_dir, container_name)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            self._kill_container(container_name)
            logs = _collect_logs(out_dir)
            message = f"zeek timed out after {self.timeout_s}s; container was killed"
            stderr_captured = _decode_capture(exc.stderr)
            stdout_captured = _decode_capture(exc.stdout)
            if stderr_captured:
                message += f"; stderr: {stderr_captured}"
            if stdout_captured:
                message += f"; stdout: {stdout_captured}"
            return ZeekResult(
                version=version,
                logs=logs,
                duration_s=time.monotonic() - started,
                returncode=TIMEOUT_RETURNCODE,
                stderr_tail=_tail(message),
                bytes_in=bytes_in,
                unrecognized=unrecognized_stems(logs),
            )
        except OSError as exc:
            return ZeekResult(
                version=version,
                logs=_EMPTY_LOGS,
                duration_s=time.monotonic() - started,
                returncode=RUNNER_FAILED_RETURNCODE,
                stderr_tail=_exc_tail(exc, self.podman_bin),
                bytes_in=bytes_in,
            )

        if proc.returncode < 0:
            # A signal-killed client process (e.g. the OOM killer on a multi-day run) never goes
            # through the `TimeoutExpired` path above, so without this the container it started
            # would leak — `--rm` only removes it on a normal exit.
            self._kill_container(container_name)

        logs = _collect_logs(out_dir)
        return ZeekResult(
            version=version,
            logs=logs,
            duration_s=time.monotonic() - started,
            returncode=proc.returncode,
            stderr_tail=_tail(proc.stderr),
            bytes_in=bytes_in,
            unrecognized=unrecognized_stems(logs),
        )

    def _check_podman_available(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [self.podman_bin, "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False
            )
        except FileNotFoundError:
            return False, f"{self.podman_bin!r} not found on PATH"
        except OSError as exc:
            return False, _exc_tail(exc, self.podman_bin)
        if proc.returncode != 0:
            return False, _tail(proc.stderr) or f"{self.podman_bin} --version exited {proc.returncode}"
        return True, proc.stdout.strip()

    def _check_image_present(self) -> tuple[bool, str]:
        """`podman image exists <image>` — checks local presence without pulling (exit `0` present,
        `1` absent; never invoked with `podman pull`, which is the orchestrator's gated action).
        """
        try:
            proc = subprocess.run(
                [self.podman_bin, "image", "exists", self.image],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
        except OSError as exc:
            return False, _exc_tail(exc, self.podman_bin)
        if proc.returncode == 0:
            return True, f"{self.image} present locally"
        return False, f"{self.image} not present locally — pull it before running (not done here)"

    def probe(self) -> ProbeResult:
        podman_available, podman_detail = self._check_podman_available()
        if not podman_available:
            return ProbeResult(
                ok=False, podman_available=False, image_present=False, zeek_version=None, detail=podman_detail
            )

        image_present, image_detail = self._check_image_present()
        if not image_present:
            return ProbeResult(
                ok=False, podman_available=True, image_present=False, zeek_version=None, detail=image_detail
            )

        version, _rc, stderr_tail = self._fetch_version()
        if not version:
            return ProbeResult(
                ok=False,
                podman_available=True,
                image_present=True,
                zeek_version=None,
                detail=stderr_tail or "zeek --version failed",
            )
        with self._version_lock:
            self._version = version
        return ProbeResult(ok=True, podman_available=True, image_present=True, zeek_version=version, detail="ok")


# ---------------------------------------------------------------------------------------------
# NativeZeekRunner — drives a `zeek` binary already on PATH, no container.
# ---------------------------------------------------------------------------------------------


class NativeZeekRunner:
    """A `ZeekRunner` that drives a `zeek` binary installed on the host — the first choice of
    `ingest/pcap`'s runner probe (`ingest/pcap/SPEC.md`, "Config": native `zeek` on `PATH`,
    then podman, then docker). Same argv shape as `PodmanZeekRunner` minus the container
    (`zeek [-C] -r <pcap> <scripts>`, run with `out_dir` as the working directory, which is where
    Zeek writes its logs), and the same result discipline: a failed run is a `ZeekResult` with
    `ok=False`, never an exception.

    No image, so no pin to check: `ZeekResult.version` (the binary's own `zeek --version`) is the
    only record of which Zeek produced the logs, exactly as it is for a digest-pinned image.
    `ProbeResult.podman_available` reads as "the runtime is available" here — the field keeps its
    name because `ZeekRunner.probe()` is one contract across every runner — and `image_present`
    is `True` whenever the binary is, since there is no image to be absent.
    """

    def __init__(
        self,
        *,
        zeek_bin: str = "zeek",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        script_policy: ScriptPolicy = DEFAULT_SCRIPT_POLICY,
        ignore_checksums: bool = DEFAULT_IGNORE_CHECKSUMS,
    ) -> None:
        self.zeek_bin = zeek_bin
        self.timeout_s = timeout_s
        self.script_policy = script_policy
        self.ignore_checksums = ignore_checksums
        self._version: str | None = None
        self._version_lock = threading.Lock()

    def _run_argv(self, pcap: Path) -> list[str]:
        return [
            self.zeek_bin,
            *(("-C",) if self.ignore_checksums else ()),
            "-r",
            str(pcap),
            *self.script_policy.scripts,
        ]

    def _fetch_version(self) -> tuple[str, int, str]:
        """`zeek --version` on the host binary; `("", returncode, stderr_tail)` on any failure,
        never an exception (the same contract as `PodmanZeekRunner._fetch_version`)."""
        try:
            proc = subprocess.run(
                [self.zeek_bin, "--version"], capture_output=True, text=True,
                timeout=PROBE_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            return "", TIMEOUT_RETURNCODE, _tail(f"zeek --version timed out after {PROBE_TIMEOUT_S}s")
        except FileNotFoundError:
            return "", RUNNER_FAILED_RETURNCODE, f"{self.zeek_bin!r} not found on PATH"
        except OSError as exc:
            return "", RUNNER_FAILED_RETURNCODE, _exc_tail(exc, self.zeek_bin)
        if proc.returncode != 0:
            return "", proc.returncode, _tail(proc.stderr)
        version = _parse_zeek_version(proc.stdout)
        if not version:
            return "", proc.returncode, _tail(f"could not parse zeek version from stdout: {proc.stdout!r}")
        return version, 0, ""

    def run(self, pcap: Path, out_dir: Path) -> ZeekResult:
        started = time.monotonic()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            stale = any(out_dir.glob("*.log"))
        except OSError as exc:
            return ZeekResult(
                version=self._version or "", logs=_EMPTY_LOGS,
                duration_s=time.monotonic() - started, returncode=RUNNER_FAILED_RETURNCODE,
                stderr_tail=_exc_tail(exc, out_dir.name), bytes_in=0,
            )
        if stale:
            return _stale_output_dir_result(out_dir, self._version or "", started)
        try:
            bytes_in = pcap.stat().st_size
        except OSError as exc:
            return ZeekResult(
                version=self._version or "", logs=_EMPTY_LOGS,
                duration_s=time.monotonic() - started, returncode=RUNNER_FAILED_RETURNCODE,
                stderr_tail=_exc_tail(exc, pcap.name), bytes_in=0,
            )

        version = self._version
        if version is None:
            with self._version_lock:
                version = self._version
                if version is None:
                    version, rc, stderr_tail = self._fetch_version()
                    if not version:
                        return ZeekResult(
                            version="", logs=_EMPTY_LOGS,
                            duration_s=time.monotonic() - started, returncode=rc,
                            stderr_tail=stderr_tail, bytes_in=bytes_in,
                        )
                    self._version = version

        try:
            proc = subprocess.run(
                self._run_argv(pcap.resolve()), capture_output=True, text=True,
                timeout=self.timeout_s, check=False, cwd=str(out_dir),
            )
        except subprocess.TimeoutExpired as exc:
            logs = _collect_logs(out_dir)
            message = f"zeek timed out after {self.timeout_s}s"
            stderr_captured = _decode_capture(exc.stderr)
            if stderr_captured:
                message += f"; stderr: {stderr_captured}"
            return ZeekResult(
                version=version, logs=logs, duration_s=time.monotonic() - started,
                returncode=TIMEOUT_RETURNCODE, stderr_tail=_tail(message), bytes_in=bytes_in,
                unrecognized=unrecognized_stems(logs),
            )
        except OSError as exc:
            return ZeekResult(
                version=version, logs=_EMPTY_LOGS, duration_s=time.monotonic() - started,
                returncode=RUNNER_FAILED_RETURNCODE, stderr_tail=_exc_tail(exc, self.zeek_bin),
                bytes_in=bytes_in,
            )

        logs = _collect_logs(out_dir)
        return ZeekResult(
            version=version, logs=logs, duration_s=time.monotonic() - started,
            returncode=proc.returncode, stderr_tail=_tail(proc.stderr), bytes_in=bytes_in,
            unrecognized=unrecognized_stems(logs),
        )

    def probe(self) -> ProbeResult:
        version, _rc, stderr_tail = self._fetch_version()
        if not version:
            return ProbeResult(
                ok=False, podman_available=False, image_present=False, zeek_version=None,
                detail=stderr_tail or f"{self.zeek_bin} --version failed",
            )
        with self._version_lock:
            self._version = version
        return ProbeResult(ok=True, podman_available=True, image_present=True,
                           zeek_version=version, detail="ok")
