"""Apply — save + recompile + install — and the bundle history the post-apply
diff reads (`docs/WORK_ORDER_console_canvas.md` S1.5/S1.7, decision **D2 (a)**;
`docs/design/14-operator-assertions.md` §3/§4).

The Console's apply is THREE steps, and the middle one is two commands
because the producer's own flow is two commands (ruling **R3**, 2026-09-05):

    1. write `<bundle>/<id>.assertions.yaml`   (`console_files.write_assertions`,
       unchanged and shared with the plain save)

    2. aximprint cast --store <STORE> --pit <PIT> --out <SCRATCH> \\
                      --imprint-id <ID> [--relocate X] [--max-count N] \\
                      [--no-chatter] --assertions <PATH>

    3. aximprint install-cast <SCRATCH> <CATALOG_ROOT> <ID> [--sensors NAME ...]

**Why both stages, and why the flags come from the bundle.** A cast writes
GENERIC stems into `--out` (`twin_config.yaml`, `topology.json`,
`provenance.json`, `tap.yaml`, `tfc.yaml`, `personas/`, `scripts/`); the
`<id>.*` names this API reads are chosen by the INSTALL stage, which also
rewrites the PIT-derived twin id to the stable one and expands the traffic
profile's node-group assignments to instances. A one-stage apply pointed at
the bundle directory therefore reported success and changed nothing the API
could see -- found live, 2026-09-05. And a cast's other flags (`--relocate`,
`--max-count`, `--no-chatter`) change the twin's shape, so re-running without
them would silently recompile a DIFFERENT twin: they are read from the
bundle's own `cast_invocation` block rather than guessed, and a bundle
without that block makes apply refuse by name (LES candidate: a build's own
invocation is part of its identity; re-deriving it from defaults is a guess
wearing a fact's clothes).

**The change detector is a CONTENT fingerprint axRange computes itself**
(ruling **R4**), never a `code_fingerprint` out of the bundle. Keying
`changed` and the history snapshot on one made `changed` permanently false
and collapsed a keep-5 history into keep-1 (measured: `3f5015bd85894407`
across two casts with different field counts).

**Two different `code_fingerprint`s live in a bundle, and this module now
tells them apart** (GAP-088, corrected 2026-09-16 -- both docstrings here
used to call the first one "the compiler's build hash", which it is not):

  * `<id>.provenance.json`'s `code_fingerprint` is the **PIT row's** build
    stamp -- the identity of the aximprint run that materialized the
    CAPTURE. A re-cast does not re-materialize the PIT, so this value does
    NOT move when the compiler is upgraded, which is why it could never be
    the tell for GAP-087. Published as `pit_code_fingerprint_before/after`.
  * `<id>.topology.json`'s `cast_invocation.code_fingerprint` is the build
    that ran **this cast**. THIS one moves with a compiler upgrade, and
    `compare_compiler_builds` below reads it on both sides of an apply so a
    compiler change is reported as its own outcome rather than arriving
    silently inside the operator's edit. Published as
    `cast_code_fingerprint_before/after` plus a `compiler_changed` verdict.
    Measured on the shipped golden bundle: the sidecar says
    `3f5015bd85894407` and the invocation says `0e2878a932b8b0f8` -- two
    values, two questions, one field name.

**No import in either direction** (`docs/design/aximprint-integration/
WORK_ORDERS.md` §0). The seam is two commands and a set of files, never a
package: this module builds two argvs and hashes three artifacts, and would
work identically against a compiler written in another language.

**D2 (a), and why the contract survives it.** v1 runs those commands as
subprocesses from inside `axrange serve`. The route is a plain `def`, so
Starlette runs it in a worker thread and a long compile never sits on the
event loop; a per-imprint lock (`imprint_apply_lock`) makes two concurrent
applies of one imprint a loud 409 rather than two compilers interleaving
writes into one bundle directory. Everything the endpoint publishes --
fingerprint before/after, record counts, the compilers' own stdout/stderr --
is derived from the FILES and the process results, so replacing
`SubprocessRecompile` with a helper process or the aximprint watcher `14` §4
names changes nothing the SPA can observe.

**Honest limits** (work order §10): a compile that hangs holds a slot until
`timeout_s`; the caller's only recourse on failure is the compilers' own
stderr, which is surfaced verbatim-but-BOUNDED (LES-206) rather than replaced
with a generic message; and only apply-produced fingerprints are diffable,
because the history snapshot is written by the apply path and nothing else.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol

from axrange.api.console_files import atomic_write

# Where an apply parks the bundle summary it is about to replace, keyed by
# the CONTENT fingerprint that summary HAD. `GET .../diff?since=<fingerprint>`
# reads exactly these; a fingerprint no apply produced has no snapshot and is
# a 404, never a fabricated "nothing changed".
HISTORY_DIRNAME = ".history"
HISTORY_KEEP = 5

#: The snapshot document's own version (N3). Without it, a snapshot written
#: by an older axRange reads as "every key added" -- a diff that looks like a
#: catastrophic change and is actually a schema mismatch. A snapshot whose
#: version this reader does not know is reported UNKNOWN and omitted, never
#: compared: "I cannot answer" and "nothing changed" are different answers
#: and only one of them is true.
SNAPSHOT_SCHEMA = "axr-bundle-snapshot/1"

#: The sections `diff_snapshots` knows how to compare, per side.
SNAPSHOT_SECTIONS: dict[str, tuple[str, ...]] = {
    "observed": ("nodes", "segments"),
    "twin": ("hosts", "segments", "conduits"),
}

#: Seconds. S15: the real cast is fast (measured 0.74 s for 786 nodes on the
#: golden PIT), and the previous 900 s was a number chosen when nobody had
#: timed it. 120 leaves two orders of magnitude of headroom while keeping a
#: hung compiler's hold on the per-imprint lock bounded. NOTE FOR OPERATORS:
#: apply is SYNCHRONOUS, so a reverse proxy in front of `axrange serve` needs
#: its read timeout >= this value (nginx `proxy_read_timeout`), or the proxy
#: 504s a compile that is still running and the SPA is left holding an etag
#: for a save that did land.
DEFAULT_TIMEOUT_S = 120.0

# The two settings this path refuses without, named in the refusal so an
# operator is told which key to set rather than "recompile unavailable".
# Spelled ONCE, here, and imported by `settings.py` (LES-105).
SETTING_BIN = "AXRANGE_AXIMPRINT_BIN"
SETTING_STORE = "AXRANGE_AXIMPRINT_STORE"
SETTING_TIMEOUT = "AXRANGE_AXIMPRINT_TIMEOUT_S"

#: The producer's second verb (`scripts/install_cast_as_twin.py` as a
#: subcommand). Spelled once so the argv pin and the docstring cannot drift.
CAST_VERB = "cast"
INSTALL_VERB = "install-cast"

#: The bundle key R3 reads the cast's own flags from.
CAST_INVOCATION_KEY = "cast_invocation"

#: The files a bundle's CONTENT fingerprint is taken over, in sorted order.
#: The twin config first because it is the one a deploy consumes; the
#: sidecar included so a provenance-only change is still a change.
FINGERPRINT_FILES = ("{id}.provenance.json", "{id}.topology.json", "{id}.yaml")
FINGERPRINT_PREFIX_LEN = 16

#: LES-206: every surface that prints a captured stream caps it. A compiler
#: that dies printing a 40 MB traceback must not put 40 MB in a JSON error
#: body or in an exception message.
MAX_STREAM_CHARS = 8000

#: A pit id becomes an argv value, so it is checked against a plain-token
#: shape first -- the bundle is a file another repo wrote, and "trusted
#: producer" is not the same as "validated input".
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RecompileError(Exception):
    """Base for every apply failure, so one `except` covers them all."""


class RecompileUnavailableError(RecompileError):
    """The API is not configured to recompile (no binary / no store), or the
    binary is not on disk / not executable. Distinct from a FAILED compile:
    nothing was run."""


class RecompileInvocationError(RecompileError):
    """The BUNDLE cannot say how it was cast (no `cast_invocation` block, or
    a malformed one). Refused loudly rather than reconstructed from defaults:
    a cast re-run without the `--relocate`/`--max-count`/`--no-chatter` the
    original used compiles a different twin into the same directory."""


class RecompileShapeError(RecompileError):
    """The compiler rejected the assertions file (exit 2). Carries the one
    stderr line it printed -- the operator's own mistake, reported in the
    compiler's words."""

    def __init__(self, message: str, *, result: "RecompileResult") -> None:
        super().__init__(message)
        self.result = result


class RecompileFailedError(RecompileError):
    """A stage failed for some other reason (any other non-zero exit, or a
    timeout). The stderr tail travels with it."""

    def __init__(self, message: str, *, result: Optional["RecompileResult"] = None) -> None:
        super().__init__(message)
        self.result = result


class ApplyInProgressError(RecompileError):
    """Another apply of THIS imprint is running. Refused rather than queued:
    two compilers writing one bundle directory is the failure this guard
    exists to prevent, and a queue would hide it."""


def clip(text: str, *, limit: int = MAX_STREAM_CHARS, keep: str = "tail") -> str:
    """Bound a captured stream, saying how much was dropped (LES-206).

    `keep="tail"` for a LOG (stderr: the useful line is the last one);
    `keep="head"` for a DOCUMENT (a JSON object truncated from the end at
    least still shows what it is; truncated from the FRONT it loses its
    opening brace and stops being JSON at all).

    That direction was not a style choice: applying the tail-keeping bound to
    stdout BEFORE the parse is what made a real 8356-character cast document
    -- 4% over the cap, almost all of it `caveats[]` -- report as "the
    recompiler's stdout is not JSON" and return empty record counts, blaming
    the producer for axRange's own truncation (measured live, 2026-09-06).
    The bound now runs AFTER the parse, and never on the way in."""
    text = text or ""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    if keep == "head":
        return text[:limit] + f"\n...[{dropped} later character(s) elided]"
    return text[-limit:] + f"\n...[{dropped} earlier character(s) elided]"


# ---------------------------------------------------------------------------
# The bundle's own record of how it was cast (R3)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CastInvocation:
    """`<id>.topology.json`'s `cast_invocation` block -- the flags the cast
    that produced this bundle actually ran with (`bundle_schema:
    axr-topology/1.5`).

    `code_fingerprint` is the build that ran THIS cast -- the compiler's own
    source digest (aximprint's `build_stamp()`), not this bundle's content
    and not the sidecar's PIT stamp. It is still never an identity (R4:
    `bundle_fingerprint` is), but it is no longer report-only: it is the one
    value in the bundle that moves when the sibling repo is upgraded, so
    `compare_compiler_builds` reads it on both sides of an apply (GAP-087)."""

    pit_id: str
    imprint_id: Optional[str] = None
    relocate: Optional[str] = None
    max_count: Optional[int] = None
    chatter: bool = True
    assertions: Optional[str] = None
    code_fingerprint: Optional[str] = None


def read_cast_invocation(
    bundle_dir: Path, imprint_id: str, *, block: Optional[dict[str, Any]] = None,
) -> CastInvocation:
    """Read + validate the block, or refuse by name.

    Every field is checked before it can become an argv value: this file is
    written by another repo, and a `subprocess.run` argument list is exactly
    the wrong place to discover it carries a shell-shaped string.

    `block` lets a caller that has ALREADY loaded the export hand the raw
    block in (`ReferenceBundle.cast_invocation`) instead of making this
    re-parse a 732 KB document for seven scalars (round 3 nit). Validation is
    identical either way -- the shortcut skips the READ, never the checks."""
    path = _fingerprint_path(bundle_dir, imprint_id, f"{imprint_id}.topology.json")
    if block is None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as e:
            raise RecompileInvocationError(
                f"imprint {imprint_id!r}: cannot read {path.name} ({e}), so the "
                f"`{CAST_INVOCATION_KEY}` block the recompile's flags come from "
                "is unreadable"
            ) from e
        except ValueError as e:
            raise RecompileInvocationError(
                f"imprint {imprint_id!r}: {path.name} is not valid JSON ({e})"
            ) from e
        block = raw.get(CAST_INVOCATION_KEY) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise RecompileInvocationError(
            f"imprint {imprint_id!r}: {path.name} carries no "
            f"`{CAST_INVOCATION_KEY}` block, so this API cannot know which "
            "flags produced the bundle (--relocate / --max-count / "
            "--no-chatter all change the twin's shape). Recast with a "
            "compiler that records its invocation, then apply again -- "
            "guessing the flags would silently compile a different twin into "
            "this directory"
        )

    pit_id = block.get("pit_id")
    if not isinstance(pit_id, str) or not _TOKEN_RE.match(pit_id):
        raise RecompileInvocationError(
            f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.pit_id is "
            f"{pit_id!r}, which is not a plain token "
            "([A-Za-z0-9][A-Za-z0-9._:-]*) -- it becomes a `--pit` argument"
        )

    relocate = block.get("relocate")
    if relocate is not None:
        if not isinstance(relocate, str):
            raise RecompileInvocationError(
                f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.relocate must "
                f"be a CIDR string or null, got {type(relocate).__name__}"
            )
        try:
            ipaddress.ip_network(relocate.strip(), strict=False)
        except ValueError as e:
            raise RecompileInvocationError(
                f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.relocate "
                f"{relocate!r} is not a CIDR ({e})"
            ) from None
        relocate = relocate.strip()

    max_count = block.get("max_count")
    if max_count is not None:
        if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 1:
            raise RecompileInvocationError(
                f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.max_count must "
                f"be a positive integer or null, got {max_count!r}"
            )

    chatter = block.get("chatter", True)
    if not isinstance(chatter, bool):
        raise RecompileInvocationError(
            f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.chatter must be a "
            f"boolean, got {chatter!r}"
        )

    recorded_id = block.get("imprint_id")
    if recorded_id is not None and not isinstance(recorded_id, str):
        raise RecompileInvocationError(
            f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.imprint_id must be "
            f"a string or null, got {type(recorded_id).__name__}"
        )
    assertions = block.get("assertions")
    if assertions is not None and not isinstance(assertions, str):
        raise RecompileInvocationError(
            f"imprint {imprint_id!r}: {CAST_INVOCATION_KEY}.assertions must be "
            f"a basename or null, got {type(assertions).__name__}"
        )
    code_fingerprint = block.get("code_fingerprint")
    return CastInvocation(
        pit_id=pit_id,
        imprint_id=recorded_id,
        relocate=relocate,
        max_count=max_count,
        chatter=chatter,
        assertions=assertions,
        code_fingerprint=str(code_fingerprint) if code_fingerprint is not None else None,
    )


# ---------------------------------------------------------------------------
# Did the COMPILER change under this apply? (GAP-087)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompilerBuildComparison:
    """The build that cast the bundle, before and after an apply.

    `changed` is a THREE-valued answer and must stay one (LES-273/LES-333):
    `True`/`False` when both sides were read, and `None` when one of them was
    not -- a build hash that could not be READ is not a build that did not
    MOVE, and folding the two together is exactly the silence GAP-087 is
    about. `note` carries this module's own sentence for the two outcomes an
    operator needs told, and is `None` for the ordinary "same build" one."""

    before: Optional[str] = None
    after: Optional[str] = None
    changed: Optional[bool] = None
    note: Optional[str] = None


def cast_code_fingerprint(
    block: Optional[dict[str, Any]], *, where: str,
) -> tuple[Optional[str], Optional[str]]:
    """`(build hash, reason it is absent)` out of a RAW `cast_invocation`.

    Deliberately lenient where `read_cast_invocation` is strict: that one
    guards values on their way into an argv and refuses the apply, this one
    only REPORTS, and a reporting read must never fail an apply that has
    already succeeded. Every way of having no value gets its own reason
    (LES-235), because "the export has no invocation block" and "the
    invocation records no build hash" send an operator to different places."""
    if not isinstance(block, dict):
        return None, (
            f"{where} carries no `{CAST_INVOCATION_KEY}` block "
            "(a bundle cast before `axr-topology/1.5`)"
        )
    value = block.get("code_fingerprint")
    if value is None:
        return None, (
            f"{where}'s `{CAST_INVOCATION_KEY}` records no `code_fingerprint`"
        )
    if not isinstance(value, str):
        # LES-235: a dict/bool/number here is a malformed producer field, not
        # a build hash -- str()-coercing it would invent a "hash" that then
        # compares equal/unequal for no reason at all.
        return None, (
            f"{where}'s `{CAST_INVOCATION_KEY}.code_fingerprint` is malformed "
            f"(a {type(value).__name__}, not a string)"
        )
    text = value.strip()
    if not text:
        return None, (
            f"{where}'s `{CAST_INVOCATION_KEY}.code_fingerprint` is empty"
        )
    return text, None


def compare_compiler_builds(
    before_block: Optional[dict[str, Any]],
    after_block: Optional[dict[str, Any]],
    *,
    bundle_changed: Optional[bool] = None,
) -> CompilerBuildComparison:
    """GAP-087: report a compiler upgrade that rode along on an apply.

    A recompile re-runs the producer from the bundle's own `cast_invocation`,
    which pins the original cast's FLAGS and not the compiler BUILD, so any
    upgrade to the sibling repo between the cast and this apply is applied
    silently on the operator's next edit. Measured once, on the S6 acceptance
    proof: one suppression produced 22 hosts added and 23 removed, because
    aximprint HEAD had renamed 11 node groups since the bundle was cast.

    **Reported, not refused** (ruled 2026-09-16). The recompile is the
    operator's own request and the new bundle is a legitimate one; what was
    missing is that nobody said the population moved for a reason that was
    not the edit. An opt-in refusal (`?allow_compiler_change=1`) and pinning
    the compiler revision in `cast_invocation` were both considered and left
    unbuilt -- the first makes the common, harmless case (an upgraded sibling
    repo) a wall, and the second is the producer's field to define.

    `bundle_changed` is the apply's own content-fingerprint verdict. When it
    is `False` nothing in the bundle moved, so an undetermined build keeps
    its reason but drops the "a population difference may be the
    compiler's" clause -- there is no population difference to attribute."""
    before, before_why = cast_code_fingerprint(before_block, where="the bundle's export")
    after, after_why = cast_code_fingerprint(
        after_block, where="the recompiled bundle's export",
    )
    if before is None or after is None:
        why = "; ".join(w for w in (before_why, after_why) if w)
        note = (
            "whether the compiler changed between the cast and this apply "
            f"could not be determined: {why}"
        )
        if bundle_changed is not False:
            note += (
                '. This is not the same as "the compiler is unchanged" -- a '
                "population difference in this apply's diff may still be the "
                "compiler's, not your edit's"
            )
        return CompilerBuildComparison(
            before=before, after=after, changed=None, note=note,
        )
    if before == after:
        return CompilerBuildComparison(before=before, after=after, changed=False)
    return CompilerBuildComparison(
        before=before, after=after, changed=True,
        note=(
            f"the compiler changed between the cast and this apply ({before} "
            f"-> {after}): this bundle was recompiled by a DIFFERENT aximprint "
            "build than the one that cast it, so population differences in "
            "this apply's diff may be the compiler's and not your edit's"
        ),
    )


TAP_PLAN_NAMES = ("tap-cast.yaml", "tap.yaml")


def _tap_plan_names(bundle_dir: Path) -> tuple[str, ...]:
    """The bundle's tap plan(s), relative to it: the legacy root names, or --
    in a `twins/<id>/` folder -- the manifest's declared `tap` default then
    its variants (`tap/cast.yaml`, ...). A broken manifest yields none here;
    the addressing guard every route runs has already refused that bundle."""
    from axrange.twinconfig.bundle import is_twin_folder
    from axrange.twinconfig.errors import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest

    if not is_twin_folder(bundle_dir):
        return TAP_PLAN_NAMES
    try:
        tap = load_twin_manifest(bundle_dir).tier("tap")
    except TwinConfigError:
        return ()
    return tuple(tap.all_files()) if tap else ()


def sensors_from_tap_plan(bundle_dir: Path) -> tuple[tuple[str, ...], list[str]]:
    """`(sensors, warnings)` -- the `--sensors` names to hand the install
    stage, recovered from the bundle's own tap plan.

    A sensor name is a LAB fact the compiler cannot observe and the installer
    fills in; without recovering it, an apply reinstalls a tap plan with
    `sensors:` empty -- an install-time fact silently lost to an edit, and a
    tap plan axRange then refuses to apply. Sorted and de-duplicated, so the
    argv depends on content rather than on file order (LES-009).

    **An empty result is REPORTED whenever a plan file exists** (N2). Five
    different states used to return a bare `()`: no plan at all, `sensors:
    []`, a singular `sensor:` key, malformed YAML, and a different root key
    -- and only the first is the ordinary state. The rest are an operator's
    tap plan quietly not surviving their own edit, which is precisely the
    class of silence this recovery exists to end. A MISSING file stays
    silent, because a bundle with no tap plan is not a problem."""
    from ruamel.yaml import YAML, YAMLError

    out: set[str] = set()
    warnings: list[str] = []
    for name in _tap_plan_names(bundle_dir):
        path = Path(bundle_dir) / name
        if not path.is_file():
            continue
        try:
            raw = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        except (OSError, YAMLError) as e:
            warnings.append(
                f"{name} exists but could not be read ({e}), so no `--sensors` "
                "were recovered -- the reinstalled tap plan will carry an "
                "empty `sensors:` and axRange will refuse to apply it"
            )
            continue
        if not isinstance(raw, dict) or "tap-plan" not in raw:
            warnings.append(
                f"{name} has no top-level `tap-plan:` key (found "
                f"{sorted(raw)[:5] if isinstance(raw, dict) else type(raw).__name__}), "
                "so no `--sensors` were recovered"
            )
            continue
        plan = raw.get("tap-plan") or {}
        singular = [
            b for b in (plan.get("bindings") or [])
            if isinstance(b, dict) and "sensor" in b and "sensors" not in b
        ]
        for binding in plan.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            for sensor in binding.get("sensors") or []:
                if isinstance(sensor, str) and sensor.strip():
                    out.add(sensor.strip())
        if out:
            return tuple(sorted(out)), warnings
        if singular:
            warnings.append(
                f"{name} spells its target(s) `sensor:` on {len(singular)} "
                "binding(s); the plan's key is `sensors:` (a list), so no "
                "`--sensors` were recovered"
            )
        else:
            warnings.append(
                f"{name} names no sensors (every binding's `sensors:` is empty "
                "or absent), so no `--sensors` were recovered -- an install-"
                "time lab fact this apply cannot carry forward"
            )
    return tuple(sorted(out)), warnings


# ---------------------------------------------------------------------------
# The bundle's CONTENT fingerprint (R4)
# ---------------------------------------------------------------------------
def _fingerprint_path(bundle_dir: Path, imprint_id: str, name: str) -> Path:
    """Where a FINGERPRINT_FILES member lives: the legacy root, or -- in a
    `twins/<id>/` folder -- `te.yaml` and `generated/` (`axrange.twinconfig.
    bundle`). The digest still hashes the aximprint NAME, so a bundle's
    fingerprint does not depend on which layout holds it."""
    from axrange.twinconfig.bundle import (
        bundle_artifact_path, bundle_config_path, is_twin_folder,
    )

    if name == f"{imprint_id}.yaml" and is_twin_folder(bundle_dir):
        return bundle_config_path(bundle_dir, imprint_id)
    return bundle_artifact_path(bundle_dir, name)


def bundle_fingerprint(bundle_dir: Path, imprint_id: str) -> Optional[str]:
    """sha256 over the bundle's canonical files, sorted by name, truncated.

    THIS is the change detector and the history key -- never either of the
    bundle's own `code_fingerprint`s (GAP-088): the sidecar's is the PIT
    row's build stamp, which a re-cast does not move at all, and the
    invocation's is the compiler build, which moves when the sibling repo is
    upgraded and NOT when the bundle's content changes. Each file contributes its
    NAME and its bytes (so a file appearing or vanishing changes the digest),
    and an absent file contributes its name and an absence marker rather than
    being skipped.

    `None` only when the bundle carries none of the canonical files at all,
    which is not a bundle."""
    h = hashlib.sha256()
    present = 0
    for pattern in sorted(FINGERPRINT_FILES):
        name = pattern.format(id=imprint_id)
        path = _fingerprint_path(bundle_dir, imprint_id, name)
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        try:
            data = path.read_bytes()
        except OSError:
            h.update(b"<absent>\0")
            continue
        present += 1
        h.update(str(len(data)).encode("ascii"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    if not present:
        return None
    return h.hexdigest()[:FINGERPRINT_PREFIX_LEN]


# ---------------------------------------------------------------------------
# The backend seam
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RecompileRequest:
    """Everything both stages need, resolved by the caller.

    `out_dir` is the SCRATCH directory stage 1 writes into -- never the
    bundle directory, which stage 2 installs into (`catalog_root/<id>/`).
    Naming it `out_dir` keeps the field named after the flag it becomes."""

    imprint_id: str
    bundle_dir: Path
    catalog_root: Path
    assertions_path: Path
    store: str
    out_dir: Path
    invocation: CastInvocation
    sensors: tuple[str, ...] = ()

    @property
    def pit_id(self) -> str:
        """The PIT the cast runs against, from the bundle's own record."""
        return self.invocation.pit_id


@dataclass
class RecompileResult:
    # Stage 1 (`cast`).
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    # Stage 2 (`install-cast`). Empty/`None` when stage 1 failed first.
    install_argv: list[str] = field(default_factory=list)
    install_returncode: Optional[int] = None
    install_stdout: str = ""
    install_stderr: str = ""
    # The cast's stdout parsed as JSON when it is JSON. `None` -- with a
    # warning -- when it is not; never an invented shape.
    document: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def combined_stderr(self) -> str:
        """Both stages' stderr, labelled, for a caller that reports one
        string. Bounded (LES-206)."""
        parts = []
        if self.stderr.strip():
            parts.append(f"[{CAST_VERB}] {self.stderr.strip()}")
        if self.install_stderr.strip():
            parts.append(f"[{INSTALL_VERB}] {self.install_stderr.strip()}")
        return clip("\n".join(parts))


class RecompileBackend(Protocol):
    """One method, so a test injects a fake and a later helper/watcher
    (D2 (b)/(c)) drops in without the endpoint or the SPA noticing."""

    def recompile(self, request: RecompileRequest) -> RecompileResult:  # pragma: no cover
        ...


def first_stderr_line(stderr: str) -> str:
    """The compiler contracts to print ONE line on stderr for a shape error;
    take exactly that."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[0]


def stderr_tail(stderr: str, *, limit: int = 20) -> str:
    lines = [ln.rstrip() for ln in (stderr or "").splitlines() if ln.strip()]
    return clip("\n".join(lines[-limit:]))


class SubprocessRecompile:
    """D2 (a): run aximprint's CLI, both stages. Nothing here knows what a
    cast IS."""

    def __init__(self, binary: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.binary = binary
        self.timeout_s = timeout_s

    def build_argv(self, request: RecompileRequest) -> list[str]:
        """Stage 1, pinned by a test (work order §9 seam 4).

        `--out` is a SCRATCH directory: the cast writes generic stems there
        and stage 2 installs them under `<id>.*` into the catalog. The three
        optional flags come from the bundle's `cast_invocation`, never from a
        default (R3)."""
        inv = request.invocation
        argv = [
            self.binary, CAST_VERB,
            "--store", str(request.store),
            "--pit", str(inv.pit_id),
            "--out", str(request.out_dir),
            "--imprint-id", request.imprint_id,
        ]
        if inv.relocate:
            argv += ["--relocate", str(inv.relocate)]
        if inv.max_count is not None:
            argv += ["--max-count", str(inv.max_count)]
        if not inv.chatter:
            argv.append("--no-chatter")
        argv += ["--assertions", str(request.assertions_path)]
        return argv

    def build_install_argv(self, request: RecompileRequest) -> list[str]:
        """Stage 2, pinned by a test. Three positionals -- the scratch cast
        directory, the catalog root, the stable id -- mirroring
        `scripts/install_cast_as_twin.py`, plus the recovered sensor names.

        `--sensors NAME ...` is spelled as ONE flag followed by every name
        (the shape ruling R3 gives). A single sensor -- the only case any
        shipped bundle has -- is identical under every candidate spelling, so
        the blast radius of this being the wrong one is a multi-sensor plan."""
        argv = [
            self.binary, INSTALL_VERB,
            str(request.out_dir),
            str(request.catalog_root),
            request.imprint_id,
        ]
        if request.sensors:
            argv += ["--sensors", *request.sensors]
        return argv

    def recompile(self, request: RecompileRequest) -> RecompileResult:
        argv = self.build_argv(request)
        proc = self._run(argv)
        # G1: the FULL stdout goes to the parser, and only what is
        # REPUBLISHED is bounded (afterwards, head-first). stderr is a log
        # and keeps its tail-bound on the way in.
        result = RecompileResult(
            argv=argv, returncode=proc.returncode,
            stdout=proc.stdout or "", stderr=clip(proc.stderr or ""),
        )
        self._check(result, result.returncode, result.stderr, stage=CAST_VERB)
        _parse_stdout(result)
        result.stdout = clip(result.stdout, keep="head")

        install_argv = self.build_install_argv(request)
        iproc = self._run(install_argv, result=result)
        result.install_argv = install_argv
        result.install_returncode = iproc.returncode
        result.install_stdout = clip(iproc.stdout or "", keep="head")
        result.install_stderr = clip(iproc.stderr or "")
        self._check(
            result, iproc.returncode, result.install_stderr, stage=INSTALL_VERB,
            partial=True,
        )
        return result

    def _run(self, argv: list[str], *, result: Optional[RecompileResult] = None):
        try:
            return subprocess.run(  # noqa: S603 -- argv list, no shell
                argv, capture_output=True, text=True, timeout=self.timeout_s,
            )
        except OSError as e:
            # M5: FileNotFoundError is only one of the ways an argv[0] can be
            # unusable -- a directory, or a file without +x, raises
            # IsADirectoryError/PermissionError and used to escape as a 500.
            raise RecompileUnavailableError(
                f"cannot run {self.binary!r}: {e} "
                f"(set {SETTING_BIN} to the aximprint executable)"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RecompileFailedError(
                f"{argv[1] if len(argv) > 1 else 'recompile'} timed out after "
                f"{self.timeout_s:g}s: {' '.join(argv)}",
                result=result or RecompileResult(
                    argv=argv, returncode=-1,
                    stdout=clip(e.stdout if isinstance(e.stdout, str) else ""),
                    stderr=clip(e.stderr if isinstance(e.stderr, str) else ""),
                ),
            ) from e

    @staticmethod
    def _check(
        result: RecompileResult, code: int, stderr: str, *, stage: str,
        partial: bool = False,
    ) -> None:
        """`partial` says a PREVIOUS stage already succeeded, so a failure
        here may have left the bundle half-written. An operator reading a 502
        needs that distinction: a failed `cast` changed nothing, a failed
        `install-cast` may have replaced some of the bundle's files and not
        others."""
        if code == 2:
            raise RecompileShapeError(
                first_stderr_line(stderr)
                or f"the recompiler rejected the assertions file ({stage}, exit 2)",
                result=result,
            )
        if code != 0:
            note = (
                " -- the earlier stage SUCCEEDED, so the bundle may be "
                "PARTIALLY INSTALLED; re-apply, or re-install the cast by hand"
                if partial else ""
            )
            raise RecompileFailedError(
                f"{stage} failed (exit {code}): "
                f"{stderr_tail(stderr) or '<no stderr>'}{note}",
                result=result,
            )


def _parse_stdout(result: RecompileResult) -> None:
    text = (result.stdout or "").strip()
    if not text:
        result.warnings.append(
            "the recompiler printed nothing on stdout -- record counts are "
            "unavailable for this apply (expected a JSON document with "
            "`imprint_id` and record counts)"
        )
        return
    try:
        doc = json.loads(text)
    except ValueError:
        result.warnings.append(
            "the recompiler's stdout is not JSON -- record counts are "
            "unavailable for this apply; stdout is reported verbatim"
        )
        return
    if not isinstance(doc, dict):
        result.warnings.append(
            "the recompiler's stdout JSON is not an object -- record counts "
            "are unavailable for this apply"
        )
        return
    result.document = doc


def record_counts_from(document: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The ASSERTION record counts, in the producer's real shape (F4).

    The live `aximprint cast` prints `{pit_id, imprint_id, out, assertions:
    {path, add, override, suppress}, segments, nodes, conduits, assignments,
    provenance_fields, caveats}` -- there is no `record_counts` key. The
    `assertions` block IS the record count of the file this apply just saved,
    which is what "record counts" means on this endpoint; `record_counts` is
    still read first for any producer that publishes one."""
    doc = document or {}
    explicit = doc.get("record_counts")
    if isinstance(explicit, dict):
        return dict(explicit)
    block = doc.get("assertions")
    if isinstance(block, dict):
        return {
            k: v for k, v in block.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    return {}


def cast_counts_from(document: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The BUNDLE's own counts from the same document (`segments`, `nodes`,
    `conduits`, `assignments`, `provenance_fields`) -- every top-level
    numeric member, published beside the record counts rather than folded
    into them: "how many segments the cast emitted" and "how many records the
    operator asserted" are two different questions."""
    doc = document or {}
    return {
        k: v for k, v in doc.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def caveats_from(document: Optional[dict[str, Any]]) -> list[str]:
    """The compiler's own `caveats[]`, republished as warnings (F4) -- a
    capped node group or a dropped persona variant is exactly the thing an
    operator needs told, and it was being dropped on the floor."""
    raw = (document or {}).get("caveats")
    if not isinstance(raw, list):
        return []
    return [f"aximprint caveat: {c}" for c in raw if isinstance(c, str) and c.strip()]


def check_output_landed(
    request: RecompileRequest, result: RecompileResult, *,
    before: Optional[str], after: Optional[str],
) -> None:
    """R4: did the recompile actually change the bundle this API serves?

    Compares the CONTENT fingerprint, not file existence -- the old check
    looked for `<out>/<id>.yaml`, which on a real bundle is the PRE-EXISTING
    file the install stage will overwrite, so it was silent whether or not
    anything landed.

    An unchanged fingerprint is not automatically wrong: `14` §3's
    determinism rule says a no-op save is a no-op recompile.

    **GAP-090: say WHICH reading applies when the evidence decides it.** The
    warning used to offer the operator a bare "either ... or ..." while this
    function was already holding everything needed to pick: the install
    stage's exit code (did stage 2 run and report success?) and the cast's
    own record counts (how many assertion records did the compiler read?).
    With both in hand the byte-identical case is stated as the determinism
    result it is, naming N. The disjunction survives for -- and ONLY for --
    the case that genuinely is one: a compiler whose stdout said nothing this
    reader could count, or a stage 2 that never reported."""
    if after is None:
        result.warnings.append(
            f"the recompile reported success but {request.bundle_dir} carries "
            "none of the bundle's canonical files -- nothing landed"
        )
        return
    if before is not None and before == after:
        result.warnings.append(
            f"the bundle's content fingerprint is unchanged ({after}): "
            + _unchanged_reading(result)
        )


def _unchanged_reading(result: RecompileResult) -> str:
    """The second half of the unchanged-fingerprint warning (GAP-090).

    Three sentences, in decreasing certainty, and each says what it is based
    on so an operator can check the reasoning rather than trust the verdict:
    an empty assertions file (nothing was asked of the recompile), a counted
    one (the determinism rule, with N), and the genuinely-unknown case.

    The "install stage did not report an exit code" branch is only reachable
    from a non-subprocess backend (a fake/in-process recompiler): the
    subprocess path always sets `install_returncode`, and a non-zero one
    raises before this warning is ever composed."""
    counts = record_counts_from(result.document)
    installed = result.install_returncode == 0
    if not installed or not counts:
        why = (
            "the install stage did not report an exit code"
            if not installed
            else "the recompiler printed no record counts this apply could read"
        )
        return (
            f"either this apply was a no-op (the determinism rule -- the same "
            "store plus the same assertions compiles to the same cast), or the "
            "recompile did not land in this bundle directory. Which one cannot "
            f"be said here: {why}"
        )
    total = int(sum(
        v for v in counts.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ))
    detail = ", ".join(f"{k}={counts[k]}" for k in sorted(counts))
    if total == 0:
        return (
            "the recompile was byte-identical, and the saved assertions file "
            f"holds no records at all ({detail}) -- there was nothing for this "
            "apply to change. The install stage returned 0, so the recompile "
            "did land"
        )
    return (
        "the recompile produced no change to the cast (the install stage "
        f"returned 0, so it did land): your {total} assertion record(s) "
        f"({detail}) compiled to the same bytes this bundle already held -- "
        "the determinism rule, the same store plus the same assertions "
        "compiling to the same cast. Byte-identity does not show each record "
        "took effect (one naming an entity the cast no longer has changes "
        "nothing either)"
    )


# ---------------------------------------------------------------------------
# One apply at a time, per imprint
# ---------------------------------------------------------------------------
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def apply_in_progress(imprint_id: str) -> bool:
    """Is an apply of this imprint running RIGHT NOW?

    A read-only check, deliberately not an acquire: a plain `PUT` must refuse
    while an APPLY holds the lock, but two plain PUTs must NOT refuse each
    other -- their answer is the etag guard's 412, which names the on-disk
    etag and is recoverable, where a 409 says only "try later".

    Inherently a TOCTOU check, and stated as one: an apply can start in the
    microseconds after it returns False. That is proportionate here -- the
    window is one file write, the design's whole concurrency story is "an
    etag and nothing more" (work order §10), and both writers still go
    through `console_files`' own per-file lock and etag guard. What this
    closes is the COMMON case: a save landing in the seconds-long gap between
    an apply's own save and its recompile, which would make the apply report
    a document that is no longer on disk."""
    lock = _LOCKS.get(imprint_id)
    return lock is not None and lock.locked()


@contextmanager
def imprint_apply_lock(imprint_id: str) -> Iterator[None]:
    """NON-blocking, and process-local (a sibling of `console_files._locked`,
    which blocks). Two compilers writing one bundle directory have no correct
    interleaving, so the second is refused; two SAVES of one file do have one
    (the etag guard's 412), so those serialize instead."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(imprint_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ApplyInProgressError(
            f"an apply of imprint {imprint_id!r} is already running -- refused "
            "rather than queued, so two recompiles can never interleave in one "
            "bundle directory"
        )
    try:
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Bundle history (S1.7's input) -- written by apply, read by diff
# ---------------------------------------------------------------------------
def history_dir(bundle_dir: Path) -> Path:
    """`<bundle>/.history/`, or `generated/.history/` in a twin folder."""
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(bundle_dir, HISTORY_DIRNAME)


class HistorySnapshotUnreadable(Exception):
    """S3: a snapshot file that EXISTS but cannot be parsed. Distinct from
    absent, because the two need different messages: "no apply produced that
    fingerprint" sends the operator looking for the wrong thing when the
    truth is "the snapshot is there and corrupt"."""


def snapshot_bundle(bundle: Any, *, fingerprint: Optional[str]) -> dict[str, Any]:
    """A compact, diffable view of a cast bundle -- BOTH populations.

    `bundle` is a `axrange.fidelity.reference.ReferenceBundle`, typed loosely
    here only to keep this module free of a circular import.

    **Two sides, because an edit can move either one** (G2, measured live
    2026-09-06). The OBSERVED side is what the vantage saw, keyed by the
    stable entity keys assertions use (`14` §3) -- and an assertion cannot
    change it: a `suppress` removes a host from the TWIN while all 786
    observed nodes stay exactly where they were, so a snapshot holding only
    this side reported an EMPTY diff for the first edit the Console's own
    edit set leads with. The TWIN side is the population the cast actually
    emitted, keyed by the twin's own node ids, where that suppress shows up
    as a removal the canvas can highlight.

    The twin's host KEYS come from the twin config's own node specs, because
    the sidecar's `population.emitted_hosts` is a COUNT and not a key set --
    checked against the real sidecar rather than assumed from its name
    (LES-228). `population` carries the sidecar's own accounting beside them,
    including `suppressed[]`, which IS a key set when it is non-empty.

    `twin_config_sha256` rides along so the diff can say "the topology is
    unchanged but the twin config moved" without claiming to know WHICH kind
    of change that was.
    """
    return {
        "schema": SNAPSHOT_SCHEMA,
        "fingerprint": fingerprint,
        "bundle_id": getattr(bundle, "bundle_id", None),
        "pit_id": getattr(bundle, "pit_id", None),
        "source": (
            "topology export"
            if getattr(bundle, "topology_export", "absent") == "present"
            else "absent"
        ),
        "observed": _observed_side(bundle),
        "twin": _twin_side(bundle),
        "population": _population_of(bundle),
        "twin_config_sha256": _twin_config_sha256(bundle),
    }


def _observed_side(bundle: Any) -> dict[str, Any]:
    """What the vantage saw. Empty maps when the export is absent -- reported
    by `source`, never faked from the twin config's relocated CIDRs (which
    would join against nothing; `reference.py`'s own note)."""
    nodes: dict[str, Any] = {}
    for n in getattr(bundle, "observed_nodes", []) or []:
        nodes[n.id] = {
            "role": n.role,
            "segment": n.segment,
            "locality": n.locality,
            "windows_seen": n.windows_seen,
        }
    segments: dict[str, Any] = {}
    for seg in getattr(bundle, "observed_segments", []) or []:
        if not seg.cidr:
            continue
        segments[seg.cidr] = {"name": seg.name, "entities": seg.entities}
    return {"nodes": nodes, "segments": segments}


def _twin_side(bundle: Any) -> dict[str, Any]:
    """The population the cast EMITTED: every host instance the twin config
    fans out to, its segments, and its conduits.

    Hosts are keyed by the twin's own node ids (`TwinGroup.node_ids`, minted
    by the shared expander rule) rather than by observed entity keys: the two
    address different things, and a twin id is what a deploy, a `tfc`
    assignment and the canvas's twin view all use."""
    hosts: dict[str, Any] = {}
    for group in getattr(bundle, "groups", []) or []:
        for node_id in group.node_ids or ():
            hosts[node_id] = {
                "role": group.role,
                "kind": group.kind,
                "segment": group.segment,
                "group": group.name,
            }
    segments: dict[str, Any] = {}
    for seg in getattr(bundle, "twin_segments", []) or []:
        # Keyed by NAME -- what a node's `net:` references, and stable across
        # a re-address -- with the cidr in the value, so a relocation shows as
        # a changed field rather than as an add plus a remove.
        segments[str(seg.name)] = {"cidr": seg.cidr}
    return {"hosts": hosts, "segments": segments, "conduits": _conduits_of(bundle)}


def _population_of(bundle: Any) -> dict[str, Any]:
    """The sidecar's own accounting, republished as data the diff can use.

    `suppressed[]` is the one member that carries KEYS; the rest are counts,
    which is exactly why the twin's host key set has to come from the twin
    config (see `snapshot_bundle`)."""
    pop = (getattr(bundle, "provenance", None) or {}).get("population")
    if not isinstance(pop, dict):
        return {}
    out: dict[str, Any] = {}
    for name in ("pit_entities", "placed_hosts", "emitted_hosts"):
        value = pop.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            out[name] = value
    keys = _suppressed_keys(pop.get("suppressed"))
    if keys is not None:
        out["suppressed"] = sorted(keys)
    return out


def _suppressed_keys(rows: Any) -> Optional[list[str]]:
    """THE `population.suppressed` row normalizer, used by both readers.

    Two shapes are in the wild and both mean one key set: the canonical
    sidecar writes `{key, note, placed, matched}` rows, and a snapshot this
    module wrote (or an older producer) may carry bare key strings.

    `None` means **there is no list here at all** -- absent accounting, which
    is a different answer from an empty suppression set (LES-240) and the
    reason this returns `Optional` rather than `[]`.
    """
    if not isinstance(rows, list):
        return None
    keys: list[str] = []
    for entry in rows:
        if isinstance(entry, str):
            keys.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("key"), str):
            keys.append(entry["key"])
    return keys


def _conduits_of(bundle: Any) -> dict[str, Any]:
    """The twin config's conduits, keyed as an assertion spells one:
    `"<cidrA>|<cidrB>"` with the halves sorted.

    S1.7 names segments, nodes AND conduits; conduits are one of the three
    `add` kinds an operator can author. Keyed by the SEGMENTS' CIDRs where
    they resolve, so the key matches the one the operator would type."""
    tc = getattr(bundle, "twin_config", None)
    cidr_of: dict[str, str] = {}
    for seg in getattr(tc, "segments", None) or []:
        name = getattr(seg, "name", None)
        cidr = getattr(seg, "cidr", None)
        if name:
            cidr_of[str(name)] = str(cidr or name)
    out: dict[str, Any] = {}
    for conduit in getattr(tc, "conduits", None) or []:
        # `TwinConfigConduit` spells its endpoints `from_` (aliased `from`)
        # and `to`; they are SEGMENT NAMES, resolved to the CIDR an
        # assertion's key would use where the segment is known.
        a = getattr(conduit, "from_", None)
        b = getattr(conduit, "to", None)
        if not a or not b:
            continue
        key = "|".join(sorted((cidr_of.get(str(a), str(a)), cidr_of.get(str(b), str(b)))))
        out[key] = {"from": str(a), "to": str(b)}
    return out


def _twin_config_sha256(bundle: Any) -> Optional[str]:
    path = getattr(bundle, "bundle_dir", None)
    bundle_id = getattr(bundle, "bundle_id", None)
    if not path or not bundle_id:
        return None
    try:
        return hashlib.sha256(
            _fingerprint_path(path, bundle_id, f"{bundle_id}.yaml").read_bytes()
        ).hexdigest()
    except OSError:  # pragma: no cover -- the loader already read it
        return None


def write_history_snapshot(
    bundle_dir: Path, fingerprint: str, snapshot: dict[str, Any], *,
    keep: int = HISTORY_KEEP,
) -> Path:
    """Atomic (S3): a snapshot half-written by an interrupted apply would be
    reported as 'corrupt' by the very next diff, which is a worse answer than
    'absent' for a file the API wrote itself."""
    d = history_dir(bundle_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_safe_fingerprint(fingerprint)}.json"
    atomic_write(path, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    prune_history(bundle_dir, keep=keep)
    return path


def load_history_snapshot(bundle_dir: Path, fingerprint: str) -> Optional[dict[str, Any]]:
    """`None` for absent; `HistorySnapshotUnreadable` for present-but-broken
    (S3). The caller turns each into its own 404 text."""
    path = history_dir(bundle_dir) / f"{_safe_fingerprint(fingerprint)}.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HistorySnapshotUnreadable(f"{path.name}: {e}") from e
    if not isinstance(doc, dict):
        raise HistorySnapshotUnreadable(
            f"{path.name}: the snapshot is a {type(doc).__name__}, not an object"
        )
    return doc


def list_history(bundle_dir: Path) -> list[str]:
    d = history_dir(bundle_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def prune_history(bundle_dir: Path, *, keep: int = HISTORY_KEEP) -> None:
    d = history_dir(bundle_dir)
    if not d.is_dir():
        return
    snaps = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in snaps[keep:]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover
            pass


def _safe_fingerprint(fingerprint: str) -> str:
    """A fingerprint indexes a file, so it may not be a path. Refuse rather
    than sanitize -- a mangled name would silently not match the snapshot the
    apply wrote (same discipline as `resolve_imprint_dir`'s traversal
    guard)."""
    fp = (fingerprint or "").strip()
    if not fp or "/" in fp or "\\" in fp or ".." in fp:
        raise ValueError(f"not a usable fingerprint: {fingerprint!r}")
    return fp


def snapshot_version(snapshot: dict[str, Any]) -> Optional[str]:
    """The snapshot's declared version, or `None` for one written before
    versioning existed (N3). `None` is not `SNAPSHOT_SCHEMA`."""
    value = snapshot.get("schema")
    return str(value) if isinstance(value, str) and value else None


def diff_snapshots(
    before: dict[str, Any], after: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """`(diff, warnings)` -- added / removed / changed, per side, per entity
    class, by stable key.

    TWO SIDES (G2): `observed.*` is what the vantage saw and `twin.*` is what
    the cast emitted. An assertion moves the twin, not the observation, so a
    one-sided diff answered "nothing changed" for a suppress -- the first
    edit the Console's edit set leads with.

    A section either side cannot supply is OMITTED and warned about, never
    reported as an empty diff (N3): an older snapshot with no `twin` block
    genuinely cannot say whether the twin moved, and `{"added": [], ...}`
    would be a claim it made up. `changed` carries the per-field before/after
    so the SPA can say WHAT changed rather than only that something did."""
    diff: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for side, sections in SNAPSHOT_SECTIONS.items():
        b_side, a_side = before.get(side), after.get(side)
        if not isinstance(b_side, dict) or not isinstance(a_side, dict):
            missing = "the recorded snapshot" if not isinstance(b_side, dict) else "the current bundle"
            warnings.append(
                f"the `{side}` side is absent from {missing}, so this diff "
                f"cannot say whether it changed -- omitted rather than "
                f"reported as empty"
            )
            continue
        diff[side] = {
            name: _diff_map(b_side.get(name) or {}, a_side.get(name) or {})
            for name in sections
        }
    return diff, warnings


def suppressed_key_delta(
    before: dict[str, Any], after: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Which AUTHORED keys the compiler reports as suppressed, then vs now.

    **The §12.6 residual, settled in S2** (`docs/WORK_ORDER_console_canvas.md`;
    the decision record is `o2/console/README.md`). The twin side of the diff
    is keyed by the twin's own node ids, so a `suppress` shows up there as
    `generic-endpoint-10-1-1-core` disappearing -- and the canvas, which
    authored `address:10.1.10.222`, cannot join the two. This function is the
    join, at the only granularity the data supports.

    **Set granularity, and that is not a shortcut.** The sidecar's suppressed
    rows are `{key, note, placed, matched}` -- they do NOT name the twin node
    the suppression removed, and the twin's group ids are minted by
    aximprint's own grouping rule (role x segment x occupancy rung), which
    axRange must not reimplement to guess a mapping. So a per-removal
    `authored_key` column on `TwinDiffDTO.hosts.removed` would be a
    correlation presented as a join: with two suppressions and three removals
    (one of them the deploy gate's doing) it would attribute the wrong key to
    the wrong host, confidently. What IS measurable is the key set moving, and
    that is what a consumer needs to mark an edit applied.

    `None` (never an empty section) when either side carries no
    `population.suppressed` accounting -- a bundle with no sidecar, or one in
    the normalized shape, cannot say "nothing is suppressed" (LES-240/LES-226:
    `null` is an absence, `[]` is a claim).
    """
    b = _suppressed_keys_of(before)
    a = _suppressed_keys_of(after)
    if b is None or a is None:
        which = "the recorded snapshot" if b is None else "the current bundle"
        # States WHAT WAS MEASURED and stops (LES-239). The earlier wording
        # named a cause -- "its sidecar is absent or in the normalized
        # shape" -- which this function cannot know: a producer that simply
        # omits the key is a third possibility, and asserting one of three
        # sends an operator to check the wrong thing.
        return None, [
            f"{which} carries no `population.suppressed` list, so this diff "
            "cannot say which authored keys were suppressed -- omitted "
            "rather than reported as an empty set. Causes include an absent "
            "or normalized sidecar and a producer that does not emit the key"
        ]
    return (
        {"added": sorted(set(a) - set(b)), "removed": sorted(set(b) - set(a)),
         "changed": []},
        [],
    )


def _suppressed_keys_of(snapshot: dict[str, Any]) -> Optional[list[str]]:
    """A SNAPSHOT's suppressed key set, through the one shared normalizer
    (`_suppressed_keys`).

    `None` = this snapshot has no accounting to compare, which is a different
    answer from an empty suppression set."""
    pop = snapshot.get("population")
    if not isinstance(pop, dict):
        return None
    return _suppressed_keys(pop.get("suppressed"))


def _diff_map(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    for key in sorted(set(before) & set(after)):
        b, a = before[key], after[key]
        if b == a:
            continue
        fields = {}
        for f in sorted(set(b) | set(a)):
            if b.get(f) != a.get(f):
                fields[f] = {"before": b.get(f), "after": a.get(f)}
        changed.append({"key": key, "fields": fields})
    return {"added": added, "removed": removed, "changed": changed}
