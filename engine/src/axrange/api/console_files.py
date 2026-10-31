"""The two OPERATOR-AUTHORED files beside a cast bundle — the assertions
document and the Console's layout pins (`docs/WORK_ORDER_console_canvas.md`
S1.3/S1.6; `docs/design/14-operator-assertions.md` §3; `docs/design/
15-console-canvas.md` §4/§10).

    <bundle>/<id>.assertions.yaml   `axr-assertions/1` -- the operator's
                                    hand-authored facts. axRange VALIDATES
                                    and WRITES it; aximprint READS it and
                                    owns precedence.
    <bundle>/layout.json            `axr-layout/1` -- the Console's optional
                                    pins. NEVER evidence: no engine or
                                    compiler path may consume it, and it is
                                    excluded from every fidelity/provenance
                                    surface by construction (nothing outside
                                    this module and its two endpoints reads
                                    it).

Both live beside the CAST BUNDLE (D4 (a), `docs/WORK_ORDER_console_canvas.md`
§8.1) rather than beside aximprint's IR store: git-tracked, diffable, and
safe from the cast installer, which writes named files and never clears the
directory. The recompile is handed the assertions path explicitly, so the
file's location is the API's call, not the compiler's.

**THE VOCABULARY IS AN ARTIFACT, NOT A RESTATED LIST** (ruling R2,
2026-09-05). `axrange/api/schemas/axr-assertions-1.schema.json` is a
BYTE-IDENTICAL VENDORED COPY of aximprint's canonical
`src/aximprint/schemas/axr-assertions-1.schema.json` -- their file declares
itself canonical ("edit it in aximprint and copy, never the other way
round") -- and every closed set below is DERIVED from it at import time,
never transcribed. A test asserts the two copies are byte-identical, and
skips with the path while aximprint has not published yet.

The previous shape -- the sets transcribed by hand into this module --
drifted on six separate axes (`mac:` keys, strict CIDRs, the listen protos,
`zone_type`, `resources` keys, conduit canonicalization) and every one of
them was found by a live round trip against the real compiler rather than by
the suite (LES candidate: a cross-repo file contract restated by hand
drifts, and only a live round trip finds it).

The schema states the CONTRACT; three things are this endpoint's own policy
on top of it and are declared in the code with their reasons
(`_DOC_REQUIRED`'s `imprint`, `ADD_REQUIRED`, `REFUSED_ADD_KINDS`), because
putting axRange-only rules into a file the other repo owns is how a shared
artifact stops being shared.

**aximprint's reader is the READER OF RECORD** (ruling R1). This module must
accept exactly what `parse_assertions` accepts and refuse exactly what it
refuses. Two deliberate, documented deviations, neither of which can produce
a file the compiler rejects:

  * `imprint:` is REQUIRED here and optional there -- the endpoint writes
    one imprint's file under that imprint's id and cross-checks the two;
  * a `segment` add must carry `name` and a `host` add must carry `segment`
    (`14` §3). The compiler defers both to compile time, where they exit 2
    with a message; refusing at save time refuses nothing the compiler would
    ultimately have accepted.

One place where the compiler is MORE tolerant and this module follows rather
than tightening: the value part of a host key is not parsed, so
`address:not-an-ip` is accepted and WARNED about (it will join against
nothing) instead of refused.

**Records carry no `kind` on disk.** The block IS the kind: an `add` record
declares one (the compiler requires it), and an `override`/`suppress`
record's entity kind is derived from its key's shape. The API's JSON keeps
`kind` on all three as a discriminator for the SPA; it is stripped on write
and re-derived on read, so a GET payload round-trips through PUT unchanged
and the file stays exactly what the compiler accepts.

**Concurrency is an etag plus a process-local lock** (work order §10). Every
write carries the etag of the document it was derived from and is refused
when the file on disk has moved on; read + guard + write run under a
per-file lock so two concurrent PUTs cannot both pass the guard. Two
operators on one imprint get last-writer-REFUSED, never a silent merge --
there is no cross-process locking, no presence and no operational transform,
and the design says so rather than pretending otherwise. The lock is
process-local, which is sufficient for the one `axrange serve` process the
Console runs against and is stated rather than assumed.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import ipaddress
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML, YAMLError

# ---------------------------------------------------------------------------
# The vendored schema -> the closed sets (ruling R2)
# ---------------------------------------------------------------------------
SCHEMA_FILENAME = "axr-assertions-1.schema.json"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / SCHEMA_FILENAME

#: Where aximprint publishes its own copy. Used ONLY by the drift test; this
#: module never reads it (the vendored copy is what ships).
AXIMPRINT_SCHEMA_RELPATH = f"src/aximprint/schemas/{SCHEMA_FILENAME}"


class ConsoleFileError(Exception):
    """Base for every failure this module raises, so a caller's single
    `except ConsoleFileError` covers read, shape and write (LES-110: a bare
    `ValueError` from a coercion is indistinguishable from a real bug)."""


class ConsoleFileShapeError(ConsoleFileError):
    """The document on disk, or the one a caller is trying to write, is not
    the schema it claims. Message always names the accepted set."""


class ConsoleFileWriteError(ConsoleFileError):
    """The document was fine; the FILESYSTEM refused (a read-only bundle
    directory, a full disk, a `.history` that is a file). Distinct from a
    shape error because it is the operator's ENVIRONMENT rather than their
    document, and the two want different HTTP statuses and different next
    steps (M5)."""


class ConsoleFileConflictError(ConsoleFileError):
    """The caller's etag does not match the file on disk -- the write is
    REFUSED, never merged. Carries the on-disk etag so the caller can report
    it and reload."""

    def __init__(self, message: str, *, on_disk_etag: Optional[str]) -> None:
        super().__init__(message)
        self.on_disk_etag = on_disk_etag


def _load_schema() -> dict[str, Any]:
    """Read the vendored schema, or fail at IMPORT with the reason.

    Loud rather than degraded: a package built without `schemas/` would
    otherwise fall back to some empty vocabulary and accept anything, which
    is the exact failure this artifact exists to prevent."""
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # pragma: no cover -- packaging fault
        raise ImportError(
            f"the {SCHEMA_FILENAME} vocabulary artifact is missing or "
            f"unreadable at {SCHEMA_PATH} ({e}); axRange derives the "
            "axr-assertions/1 closed sets from it at import time and refuses "
            "to guess them"
        ) from e


_SCHEMA: dict[str, Any] = _load_schema()
_DEFS: dict[str, Any] = _SCHEMA.get("$defs") or {}


def _def(name: str) -> dict[str, Any]:
    node = _DEFS.get(name)
    if not isinstance(node, dict):  # pragma: no cover -- schema drift
        raise ImportError(
            f"{SCHEMA_FILENAME} has no `$defs.{name}` -- the vendored copy and "
            "this reader have diverged; re-vendor aximprint's canonical file"
        )
    return node


def _props(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple((node.get("properties") or {}).keys())


def _content_fields(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(k for k in _props(node) if k not in ("kind", "key", "note")))


def _add_branches() -> dict[str, dict[str, Any]]:
    """`{kind: branch}` for `$defs.addRecord`'s discriminated union, keyed by
    the `kind` CONST each branch declares -- never by position, so a reorder
    of the file (it is emitted `sort_keys=True`) cannot silently re-map a
    vocabulary."""
    out: dict[str, dict[str, Any]] = {}
    for branch in _def("addRecord").get("oneOf") or []:
        const = ((branch.get("properties") or {}).get("kind") or {}).get("const")
        if isinstance(const, str):
            out[const] = branch
    if not out:  # pragma: no cover -- schema drift
        raise ImportError(f"{SCHEMA_FILENAME}: `$defs.addRecord` declares no kinds")
    return out


def _override_branches() -> dict[str, dict[str, Any]]:
    """`{entity kind: branch}` for `$defs.overrideRecord`, keyed by which key
    shape the branch accepts (`hostKey` -> host, `cidr` -> segment). The kind
    is a property of the KEY on both sides of the seam, so deriving it from
    the key's `$ref` is the same rule the parser applies, not a second one."""
    by_ref = {"#/$defs/hostKey": "host", "#/$defs/cidr": "segment"}
    out: dict[str, dict[str, Any]] = {}
    for branch in _def("overrideRecord").get("oneOf") or []:
        ref = ((branch.get("properties") or {}).get("key") or {}).get("$ref")
        kind = by_ref.get(ref)
        if kind:
            out[kind] = branch
    if not out:  # pragma: no cover -- schema drift
        raise ImportError(f"{SCHEMA_FILENAME}: `$defs.overrideRecord` declares no kinds")
    return out


def _host_key_prefixes() -> tuple[str, ...]:
    """`^(?:address|hostname|mac):.*\\S` -> `("address:", "hostname:", "mac:")`.

    The one set the canonical schema expresses ONLY as a pattern, so it is
    read out of the alternation rather than restated -- and the extraction is
    anchored tightly enough that a change to the pattern's SHAPE fails at
    import instead of quietly yielding a shorter set. A test pins that every
    extracted prefix still matches the pattern it came from."""
    pattern = _def("hostKey").get("pattern") or ""
    m = re.match(r"^\^\(\?:([A-Za-z|]+)\):", pattern)
    if not m:  # pragma: no cover -- schema drift
        raise ImportError(
            f"{SCHEMA_FILENAME}: `$defs.hostKey.pattern` is {pattern!r}, which "
            "this reader cannot read a prefix alternation out of -- re-vendor "
            "the canonical file and update `_host_key_prefixes`"
        )
    return tuple(f"{name}:" for name in m.group(1).split("|"))


_ADD_BRANCHES = _add_branches()
_OVERRIDE_BRANCHES = _override_branches()

ASSERTIONS_SCHEMA: str = _SCHEMA["properties"]["schema"]["const"]
LAYOUT_SCHEMA = "axr-layout/1"

ASSERTIONS_SUFFIX = ".assertions.yaml"
LAYOUT_FILENAME = "layout.json"

# Top-level keys. Closed: an unknown key is a refusal, not an ignored extra
# -- a typo'd `overrides:` that silently did nothing would read as "my
# override was saved" (the file is the contract).
_DOC_KEYS: tuple[str, ...] = _props(_SCHEMA)

# ---------------------------------------------------------------------------
# THE THREE axRange-ONLY ADDITIONS, declared here rather than in the schema.
#
# The schema is aximprint's canonical file and states the CONTRACT; these are
# this endpoint's own policy on top of it. Each is listed with why it cannot
# produce a file the compiler would refuse -- which is the test R1 sets, and
# the reason none of them belongs in the shared artifact.
# ---------------------------------------------------------------------------
#: `imprint` is OPTIONAL to the compiler and REQUIRED here: the endpoint
#: writes one imprint's file under that imprint's id and cross-checks the
#: two, and a document with no `imprint` cannot be checked.
_DOC_REQUIRED: tuple[str, ...] = tuple(
    sorted(set(_SCHEMA.get("required") or ()) | {"imprint"})
)
#: A `segment` add needs `name` and a `host` add needs `segment` (`14` §3).
#: The compiler defers both to COMPILE time, where they exit 2 with a message
#: ("an added host with no derivable segment"); refusing at save time refuses
#: nothing it would ultimately have accepted, and refuses it where the
#: operator can still see their own form.
ADD_REQUIRED: dict[str, tuple[str, ...]] = {
    "conduit": (), "host": ("segment",), "segment": ("name",),
}
#: `kind: route` is refused BY NAME. The canonical schema carries the reason
#: as PROSE in `$defs.addRecord.description` (a refusal-by-name is not
#: expressible as data), so the message is restated here; a test pins that
#: `route` is not among the kinds the schema does accept.
REFUSED_ADD_KINDS: dict[str, str] = {
    "route": (
        "a twin config has no route object at all -- inter-segment "
        "reachability belongs to the synthetic gateway, and `kind: conduit` "
        "is how it is authored. The only route-shaped surface in axRange is "
        "the fabric slice's per-router `routing:`, which a cast never emits"
    ),
}
#: The `source:` prefix. The schema expresses it as a case-insensitive
#: pattern; a test pins that this literal satisfies that pattern.
SOURCE_PREFIX = "operator:"

# GET decorations PUT tolerates and strips, so the SPA can round-trip exactly
# what it read (`{...document..., etag, imprint_id, path, exists, warnings}`)
# without hand-assembling a second shape.
_READ_ONLY_KEYS = ("etag", "imprint_id", "path", "exists", "warnings")

#: Sorted, matching the compiler's own `ADD_KINDS`, so a refusal lists them
#: in the order the other repo's message does.
ADD_KINDS: tuple[str, ...] = tuple(sorted(_ADD_BRANCHES))

#: Every key an `add` of that kind may carry, minus its own identity keys.
ADD_FIELDS: dict[str, tuple[str, ...]] = {
    kind: _content_fields(branch) for kind, branch in _ADD_BRANCHES.items()
}

#: Per-kind `override.field`, CLOSED. The entity kind is derived from the
#: key, so this is keyed by the kind the KEY resolves to.
OVERRIDE_FIELDS: dict[str, tuple[str, ...]] = {
    kind: tuple(((branch.get("properties") or {}).get("field") or {}).get("enum") or ())
    for kind, branch in _OVERRIDE_BRANCHES.items()
}
_OVERRIDE_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(k for b in _OVERRIDE_BRANCHES.values() for k in _props(b))
)
_SUPPRESS_KEYS: tuple[str, ...] = _props(_def("suppressRecord"))

HOST_KEY_PREFIXES: tuple[str, ...] = _host_key_prefixes()
_HOST_KEY_RE = re.compile(_def("hostKey")["pattern"])
_SOURCE_RE = re.compile(_SCHEMA["properties"]["source"]["pattern"])
ZONE_TYPES: tuple[str, ...] = tuple(_def("zoneType").get("enum") or ())
_PORT_ENTRY: dict[str, Any] = _def("portList").get("items") or {}
PORT_PROTOS: tuple[str, ...] = tuple(
    ((_PORT_ENTRY.get("properties") or {}).get("proto") or {}).get("enum") or ()
)
RESOURCE_KEYS: tuple[str, ...] = tuple(
    sorted((_def("resources").get("propertyNames") or {}).get("enum") or ())
)
_PORT_ENTRY_KEYS: tuple[str, ...] = _props(_PORT_ENTRY)

#: `suppress` takes host keys only: pruning a whole segment is a topology
#: edit, never a suppression.
_SUPPRESS_KINDS = ("host",)

#: The API/DTO key order (the FILE order is `_file_record`'s, which drops
#: `kind` outside `add` -- see the module docstring).
_CANONICAL_RECORD_ORDER = (
    "kind", "key", "field", "value", "name", "segment", "zone_type",
    "role", "image", "resources", "listen", "dial", "note",
)

_KEY_SHAPES = ("address:<ip>", "hostname:<name>", "mac:<addr>", "<cidr>", "<cidr>|<cidr>")

# `site:<prefix>` / `segment:<cidr>` / `host:<name>`. The `host:` kind is the
# twin design canvas's (`ui/src/features/edit-canvas/SPEC.md`): a host chip
# dragged inside its segment. An ENTITY-KEY shaped pin (`address:` /
# `hostname:` / `mac:` -- the assertions file's addressing) is still refused
# by name, so a key that was never a pin cannot be saved as one; a pin that
# silently did nothing would be worse than a refusal.
_PIN_PREFIXES = ("site:", "segment:", "host:")
_REFUSED_PIN_PREFIXES = ("address:", "hostname:", "mac:")

# The optional PLANE STAMP (`o2/console/src/layout/plane.ts`, R2): the frame of
# reference a pin's ABSOLUTE `{x, y}` was authored against.
#
# **This writer treats it as an OPAQUE TOKEN and never interprets it.** The
# plane is a property of the drawing code, which lives in the SPA; a server
# that tried to decide whether a stamp was current would be a second opinion
# about a layout it does not draw. It is shape-checked and round-tripped, and
# the SPA compares it with the plane it is about to draw -- accepting a stale
# pin set with a note rather than silently drawing it wrong or silently
# dropping it (LES-289).
#
# Bounded and character-restricted for the same reason every other field here
# is: this is a file an operator's browser writes into a git-tracked bundle, so
# an unbounded string would be an unbounded write. An ABSENT stamp is legal and
# means LEGACY (written before the stamp existed) -- absent is not empty.
_PLANE_MAX_LEN = 64
_PLANE_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_LAYOUT_KEYS = ("schema", "imprint", "pins", "plane", "annotations")

# The OPTIONAL `annotations` list (the canvas's free text and shapes, each a
# positioned SVG snippet: `ui/src/features/edit-canvas/SPEC.md`). Still
# `axr-layout/1`: the key is optional, absent stays absent, so every document
# written before it existed validates and round-trips byte-identical. Like
# the pins it is presentation, never evidence. The file is served back to a
# browser, so every snippet passes `_check_svg`: no script, no event-handler
# attribute, no foreignObject, no href of any kind -- refused by name, never
# stripped (a sanitiser that rewrites is a second opinion about the drawing).
_ANNOTATION_KINDS = ("text", "rect", "ellipse", "line")
_ANNOTATION_KEYS = ("id", "kind", "x", "y", "w", "h", "svg", "locked", "hidden")
_ANNOTATION_ID_MAX_LEN = 64
_ANNOTATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_ANNOTATION_SVG_MAX_BYTES = 8 * 1024
_ANNOTATIONS_MAX = 200
_SVG_REFUSED = (
    (re.compile(r"<\s*script", re.I), "a <script> element"),
    (re.compile(r"<\s*foreignObject", re.I), "a <foreignObject> element"),
    (re.compile(r"\bon[a-z]+\s*=", re.I), "an on*= event-handler attribute"),
    (re.compile(r"\b(?:xlink:)?href\s*=", re.I), "an href / xlink:href attribute"),
)


# ---------------------------------------------------------------------------
# Paths + etags
# ---------------------------------------------------------------------------
def assertions_path(bundle_dir: Path, imprint_id: str) -> Path:
    """`<bundle>/<id>.assertions.yaml` -- the id-stemmed name `14` §3 names,
    so a bundle directory holding several documents stays self-describing."""
    return Path(bundle_dir) / f"{imprint_id}{ASSERTIONS_SUFFIX}"


def layout_path(bundle_dir: Path) -> Path:
    """`<bundle>/layout.json` -- NOT id-stemmed (one bundle, one layout;
    `docs/WORK_ORDER_console_canvas.md` S1.6). In a `twins/<id>/` folder it
    is `generated/layout.json` (`axrange.twinconfig.bundle`: machine-written
    files live under `generated/`; the assertions document is operator
    content and stays at the root)."""
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(bundle_dir, LAYOUT_FILENAME)


def compute_etag(text: str) -> str:
    """A CONTENT hash, not an mtime/inode: two machines serving the same
    checked-in file agree, and a rewrite with identical bytes (a no-op save)
    keeps its etag rather than inventing a conflict for the next writer."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_etag(value: Optional[str]) -> Optional[str]:
    """Accept `"<hex>"`, `W/"<hex>"` and a bare `<hex>` alike -- an HTTP
    `If-Match` is quoted by spec while a JSON body field naturally is not,
    and a guard that silently failed to match one of the two spellings
    would refuse every legitimate write."""
    if value is None:
        return None
    v = value.strip()
    if v.startswith("W/"):
        v = v[2:].strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v or None


# ---------------------------------------------------------------------------
# One writer at a time, per FILE (M2)
# ---------------------------------------------------------------------------
# A sibling of `recompile.imprint_apply_lock` and, like it, PROCESS-LOCAL --
# acceptable because the Console runs against one `axrange serve`, and stated
# rather than assumed. BLOCKING, unlike the apply lock: two saves of one file
# are meant to serialize into a 200 and a 412 (the etag guard's own answer),
# where two concurrent COMPILES writing one bundle directory have no such
# answer and are refused outright.
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.setdefault(key, threading.Lock())
    return lock


class _locked:
    """`with _locked(path):` -- read + guard + write as one critical section.

    Without it, two concurrent PUTs (the routes are sync `def`, so Starlette
    runs them in a threadpool) both read "no file", both pass the guard, and
    both write: one wins silently, defeating the last-writer-REFUSED promise
    the etag exists to keep. The PID-keyed temp name this replaced made the
    same race lose a file entirely (`FileNotFoundError` -> 500)."""

    def __init__(self, path: Path) -> None:
        self._lock = _file_lock(path)

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *exc: Any) -> None:
        self._lock.release()


#: The mode a NEW operator file is created with. `tempfile.mkstemp` makes
#: 0600, which is right for a secret and wrong for these two: they are
#: git-tracked documents beside a cast bundle, read by the compiler, by a
#: reviewer and by `git diff` (N1). An EXISTING file keeps whatever mode it
#: already has, so a save never re-narrows a 0644 checkout.
DEFAULT_FILE_MODE = 0o644


def atomic_write(path: Path, text: str) -> None:
    """Write via a UNIQUE sibling temp + `os.replace`, so a crashed or
    interrupted save can never leave a half-written assertions file that the
    next compile would read as the operator's intent.

    PUBLIC (round 3): `recompile.write_history_snapshot` needs the same
    guarantee for its own file, and reaching across a module boundary for a
    `_`-private name is how two writers end up with one and a half of the
    same invariant.

    `tempfile.mkstemp`, not a PID-keyed name: two threads in one process
    share a PID, so the old name collided under exactly the concurrency
    `_locked` now serializes -- and a collision meant one writer's
    `os.replace` ran against a file the other had already moved. mkstemp's
    0600 is then WIDENED before the replace (see `DEFAULT_FILE_MODE`),
    because the atomic-write idiom carries the temp file's mode onto the
    target and these files are not secrets."""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = DEFAULT_FILE_MODE
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
        )
    except OSError as e:
        raise ConsoleFileWriteError(
            f"cannot write {path.name} in {path.parent}: {e}"
        ) from e
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as e:
        raise ConsoleFileWriteError(f"cannot write {path}: {e}") from e
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover -- best effort
                pass


#: The private spelling kept as a deprecated alias for one cycle: tests that
#: monkeypatch it by name predate the rename.
_atomic_write = atomic_write


def _read_text(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConsoleFileError(f"cannot read {path}: {e}") from e


def _guard_etag(path: Path, current_text: Optional[str], supplied: Optional[str]) -> None:
    """The whole concurrency story (work order §10). Three cases, each loud:
    a stale etag, a missing etag against an existing file, and an etag
    supplied for a file that does not exist yet."""
    on_disk = compute_etag(current_text) if current_text is not None else None
    supplied = normalize_etag(supplied)
    if on_disk is None:
        if supplied:
            raise ConsoleFileConflictError(
                f"{path.name}: etag {supplied!r} was supplied but the file does "
                "not exist (it was deleted, or this is a first save against a "
                "stale view) -- reload and retry",
                on_disk_etag=None,
            )
        return
    if not supplied:
        raise ConsoleFileConflictError(
            f"{path.name}: the file already exists (etag {on_disk!r}) -- send "
            "it back in `If-Match` (or the body's `etag`) to confirm you are "
            "editing the current version; a write with no etag is refused, "
            "never merged",
            on_disk_etag=on_disk,
        )
    if supplied != on_disk:
        raise ConsoleFileConflictError(
            f"{path.name}: etag mismatch -- you have {supplied!r}, the file on "
            f"disk is {on_disk!r}; reload and re-apply your edits (a stale "
            "write is refused, never merged)",
            on_disk_etag=on_disk,
        )


# ---------------------------------------------------------------------------
# Entity keys (`14` §3: "the stable entity keys the topology export already
# uses", never a twin-config index)
# ---------------------------------------------------------------------------
def key_kind(key: Any, *, where: str) -> str:
    """`host` | `segment` | `conduit`, derived from the key SHAPE, in the
    compiler's own precedence order: a host prefix first, then a `|`, then a
    strict CIDR.

    The kind is a property of the key, not an independent field: a record
    that declares one kind and carries another's key is internally
    inconsistent, so a declared kind is cross-checked against this rather
    than trusted (and, on `override`/`suppress`, is not written at all).
    """
    if not isinstance(key, str) or not key.strip():
        raise ConsoleFileShapeError(
            f"{where}: `key` must be a non-empty string; accepted shapes: "
            f"{list(_KEY_SHAPES)}"
        )
    k = key.strip()
    if any(k.startswith(p) for p in HOST_KEY_PREFIXES):
        if not k.split(":", 1)[1].strip():
            raise ConsoleFileShapeError(
                f"{where}: key {key!r} carries an empty value part; accepted "
                f"shapes: {list(_KEY_SHAPES)}"
            )
        return "host"
    if "|" in k:
        return "conduit"
    if _strict_cidr(k) is not None:
        return "segment"
    if _loose_cidr(k) is not None:
        raise ConsoleFileShapeError(
            f"{where}: segment key {key!r} is not a NETWORK address -- a "
            f"segment's key is its observed CIDR and must be the network "
            f"form ({_strict_form(k)}), or it joins against nothing; accepted "
            f"shapes: {list(_KEY_SHAPES)}"
        )
    raise ConsoleFileShapeError(
        f"{where}: key {key!r} matches no accepted key shape; accepted: "
        f"{list(_KEY_SHAPES)}"
    )


def _strict_cidr(value: str) -> Optional[str]:
    """The compiler's `_cidr`: parsed, never pattern-matched, `strict=True`
    so `10.80.31.5/24` is refused rather than silently widened."""
    if "/" not in value:
        return None
    try:
        return str(ipaddress.ip_network(value.strip(), strict=True))
    except ValueError:
        return None


def _loose_cidr(value: str) -> Optional[str]:
    if "/" not in value:
        return None
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        return None


def _strict_form(value: str) -> str:
    return _loose_cidr(value) or value


def _require_strict_cidr(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise ConsoleFileShapeError(
            f"{where}: expected a CIDR string, got {type(value).__name__}"
        )
    strict = _strict_cidr(value)
    if strict is None:
        loose = _loose_cidr(value)
        if loose is not None:
            raise ConsoleFileShapeError(
                f"{where}: {value!r} is not a NETWORK CIDR -- a segment's key "
                f"is its observed CIDR and must be the network form "
                f"({loose}), or it joins against nothing"
            )
        raise ConsoleFileShapeError(
            f"{where}: {value!r} is not a CIDR; accepted shapes: {list(_KEY_SHAPES)}"
        )
    return strict


def _canonical_conduit_key(key: str, *, where: str) -> str:
    """Two DIFFERENT strict CIDRs, `|`-joined and SORTED.

    Sorted here rather than trusted from the author, matching the compiler's
    `conduit_key` exactly: the compiler derives its own conduits from
    `tuple(sorted((a, b)))`, so the other order would describe one conduit
    under a second name and the two would merge as different records."""
    if key.count("|") != 1:
        raise ConsoleFileShapeError(
            f"{where}: a conduit key is exactly two CIDRs joined by '|' "
            f'("<cidrA>|<cidrB>"), got {key!r}'
        )
    left, right = (
        _require_strict_cidr(part.strip(), where=f"{where} (conduit half)")
        for part in key.split("|")
    )
    if left == right:
        raise ConsoleFileShapeError(
            f"{where}: a conduit joins two DIFFERENT segments; both sides are {left!r}"
        )
    return "|".join(sorted((left, right)))


# ---------------------------------------------------------------------------
# The assertions document
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AssertionsDoc:
    imprint_id: str
    path: Path
    exists: bool
    # None for a file that does not exist yet -- the first save creates it.
    etag: Optional[str]
    schema: str
    imprint: str
    as_of: Optional[str]
    source: Optional[str]
    add: list[dict[str, Any]] = field(default_factory=list)
    override: list[dict[str, Any]] = field(default_factory=list)
    suppress: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    # S11: the declared key order survives the dump, so the file diffs like
    # the design's own example instead of alphabetically (`kind` after
    # `image`). The compiler sets `sort_keys=False` for the same reason.
    y.representer.sort_base_mapping_type_on_output = False
    return y


def empty_assertions(imprint_id: str, path: Path) -> AssertionsDoc:
    """What `GET` returns for a bundle with no assertions file yet: an EMPTY
    document with `exists: false` and `etag: null`, never a 404 -- "nothing
    asserted yet" is the ordinary state of every imprint before the first
    save, not a missing resource."""
    return AssertionsDoc(
        imprint_id=imprint_id, path=path, exists=False, etag=None,
        schema=ASSERTIONS_SCHEMA, imprint=imprint_id, as_of=None, source=None,
    )


def read_assertions(bundle_dir: Path, imprint_id: str) -> AssertionsDoc:
    path = assertions_path(bundle_dir, imprint_id)
    text = _read_text(path)
    if text is None:
        return empty_assertions(imprint_id, path)
    return _doc_from_text(text, imprint_id, path, where=path.name)


def _doc_from_text(text: str, imprint_id: str, path: Path, *, where: str) -> AssertionsDoc:
    try:
        raw = _yaml().load(io.StringIO(text))
    except YAMLError as e:
        raise ConsoleFileShapeError(f"{where}: not valid YAML: {e}") from e
    doc, warnings = validate_assertions(raw, imprint_id, where=where)
    return AssertionsDoc(
        imprint_id=imprint_id, path=path, exists=True, etag=compute_etag(text),
        schema=doc["schema"], imprint=doc["imprint"], as_of=doc.get("as_of"),
        source=doc.get("source"), add=doc["add"], override=doc["override"],
        suppress=doc["suppress"], warnings=warnings,
    )


def write_assertions(
    bundle_dir: Path, imprint_id: str, body: dict[str, Any], *,
    if_match: Optional[str],
) -> AssertionsDoc:
    """Validate `body` against `axr-assertions/1`, refuse a stale etag, and
    write. Validation runs BEFORE the etag guard is consumed and before any
    byte is written, so a refused document leaves the file untouched; the
    read + guard + write are one critical section, so a second concurrent
    writer is refused rather than silently overwritten (M2)."""
    path = assertions_path(bundle_dir, imprint_id)
    doc, warnings = validate_assertions(body, imprint_id, where="request body")
    text = _dump_assertions(doc)
    with _locked(path):
        _guard_etag(path, _read_text(path), if_match)
        atomic_write(path, text)
    return AssertionsDoc(
        imprint_id=imprint_id, path=path, exists=True, etag=compute_etag(text),
        schema=doc["schema"], imprint=doc["imprint"], as_of=doc.get("as_of"),
        source=doc.get("source"), add=doc["add"], override=doc["override"],
        suppress=doc["suppress"], warnings=warnings,
    )


def _dump_assertions(doc: dict[str, Any]) -> str:
    """The FILE shape: no `kind` outside `add`, keys in the declared order.

    The API's `doc` carries a derived `kind` on every record so the SPA has
    one discriminator; the compiler's `_parse_override`/`_parse_suppress` are
    fail-closed on their key sets and reject it (`suppress[0]: unknown
    key(s) ['kind']`, exit 2 -- found live, 2026-09-05). Stripping it here
    rather than never deriving it keeps both true at once."""
    file_doc = {
        "schema": doc["schema"],
        "imprint": doc["imprint"],
        "as_of": doc["as_of"],
        "source": doc["source"],
    }
    for block in ("add", "override", "suppress"):
        file_doc[block] = [_file_record(r, block) for r in doc[block]]
    buf = io.StringIO()
    _yaml().dump(file_doc, buf)
    return buf.getvalue()


def _file_record(rec: dict[str, Any], block: str) -> dict[str, Any]:
    if block == "add":
        content = sorted(k for k in rec if k not in ("kind", "key", "note"))
        return {
            "kind": rec["kind"], "key": rec["key"],
            **{k: rec[k] for k in content}, "note": rec["note"],
        }
    if block == "override":
        return {
            "key": rec["key"], "field": rec["field"],
            "value": rec.get("value"), "note": rec["note"],
        }
    return {"key": rec["key"], "note": rec["note"]}


def validate_assertions(
    raw: Any, imprint_id: str, *, where: str,
) -> tuple[dict[str, Any], list[str]]:
    """Shape-check + canonicalize. Returns `(document, warnings)`.

    Every refusal names the ACCEPTED set rather than only what was wrong --
    an operator (or an SPA author) who reads "kind 'network' is not a record
    kind; accepted: ['conduit', 'host', 'segment']" needs no second round
    trip. axRange validates and writes; it never interprets precedence.
    """
    warnings: list[str] = []
    if raw is None:
        raise ConsoleFileShapeError(f"{where}: empty document")
    if not isinstance(raw, dict):
        raise ConsoleFileShapeError(
            f"{where}: top level must be a mapping with keys {list(_DOC_KEYS)}"
        )
    body = {k: v for k, v in raw.items() if k not in _READ_ONLY_KEYS}

    unknown = [k for k in body if k not in _DOC_KEYS]
    if unknown:
        raise ConsoleFileShapeError(
            f"{where}: unknown top-level key(s) {sorted(unknown)}; accepted: "
            f"{list(_DOC_KEYS)}"
        )
    missing = [k for k in _DOC_REQUIRED if not body.get(k)]
    if missing:
        raise ConsoleFileShapeError(
            f"{where}: missing required key(s) {missing}; every assertions "
            f"document carries {list(_DOC_REQUIRED)} "
            "(`as_of` is the snapshot's time bound and is never stamped for "
            "you -- an API-invented timestamp would churn the file on every "
            "no-op save and break the determinism rule)"
        )
    if body["schema"] != ASSERTIONS_SCHEMA:
        raise ConsoleFileShapeError(
            f"{where}: schema {body['schema']!r} is not {ASSERTIONS_SCHEMA!r}; "
            "this writer speaks exactly one schema"
        )
    if body["imprint"] != imprint_id:
        raise ConsoleFileShapeError(
            f"{where}: document declares imprint {body['imprint']!r} but is "
            f"being written for {imprint_id!r} -- writing one imprint's "
            "assertions under another's id is worse than refusing"
        )
    as_of = _canonical_as_of(body["as_of"], where=where)
    source = _canonical_source(body["source"], where=where)

    add = _validate_add(body.get("add"), where=where, warnings=warnings)
    override = _validate_override(body.get("override"), where=where, warnings=warnings)
    suppress = _validate_suppress(body.get("suppress"), where=where, warnings=warnings)
    _cross_check(add, override, suppress, where=where)

    doc = {
        "schema": ASSERTIONS_SCHEMA,
        "imprint": imprint_id,
        "as_of": as_of,
        "source": source,
        "add": add,
        "override": override,
        "suppress": suppress,
    }
    return doc, warnings


def _canonical_as_of(value: Any, *, where: str) -> str:
    """The snapshot's time bound, coerced to one ISO-8601 `Z` spelling.

    **`datetime`/`date` are accepted, not refused** (M3): a hand-authored
    `as_of: 2026-09-04T18:00:00Z` -- exactly the design note's own example,
    unquoted -- is resolved to a `datetime` by ruamel (and by PyYAML on the
    compiler's side) before any reader sees a string, so an `isinstance(str)`
    check refuses the one spelling the design tells operators to write.
    Parsed rather than pattern-matched, matching the compiler's `_parse_as_of`
    including its naive-datetime-is-UTC rule."""
    if isinstance(value, _dt.datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    elif isinstance(value, _dt.date):
        stamp = _dt.datetime(
            value.year, value.month, value.day, tzinfo=_dt.timezone.utc,
        )
    elif isinstance(value, str):
        try:
            stamp = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ConsoleFileShapeError(
                f"{where}: `as_of` {value!r} is not an ISO-8601 timestamp "
                "(e.g. 2026-09-04T18:00:00Z). It is this snapshot's time "
                "bound and becomes the `window_start` of every row the "
                "reference ingest writes"
            ) from None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    else:
        raise ConsoleFileShapeError(
            f"{where}: `as_of` must be an ISO-8601 timestamp, got "
            f"{type(value).__name__}"
        )
    return stamp.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_source(value: Any, *, where: str) -> str:
    """`operator: <name>`, REFUSED rather than warned about when it is not.

    The compiler is fail-closed here, so a warning would have written a file
    the recompile then rejects with exit 2 -- a save that looks like it
    worked. Kept as AUTHORED (`operator: name` and `operator:name` are both
    accepted and both preserved): the compiler canonicalizes internally and
    accepts either, so reformatting a string the operator typed would churn
    the file for nothing."""
    if not isinstance(value, str):
        raise ConsoleFileShapeError(
            f"{where}: `source` must be a string, got {type(value).__name__}"
        )
    text = value.strip()
    if not _SOURCE_RE.match(text):
        raise ConsoleFileShapeError(
            f"{where}: `source` {value!r} must be {SOURCE_PREFIX!r} followed "
            "by a name -- an assertion's source is a PERSON, and saying so is "
            "what lets a scorecard's evidence mix tell 'an operator said so' "
            "from 'an export said so'"
        )
    if not text[len(SOURCE_PREFIX):].strip():
        raise ConsoleFileShapeError(
            f"{where}: `source` {value!r} names no operator after "
            f"{SOURCE_PREFIX!r}"
        )
    return text


def _records(value: Any, *, block: str, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConsoleFileShapeError(f"{where}: `{block}` must be a list of records")
    out = []
    for i, rec in enumerate(value):
        if not isinstance(rec, dict):
            raise ConsoleFileShapeError(
                f"{where}: `{block}[{i}]` must be a mapping, got "
                f"{type(rec).__name__}"
            )
        # Tolerant IN, strict OUT: a null-valued key means "not authored"
        # and is dropped before the closed-set check, because a browser form
        # that serializes every field it knows about (`name: null` on a host
        # record) is a client shape, not an authoring mistake. `value` on an
        # OVERRIDE is the one exception -- it may legitimately BE null, and
        # dropping it would turn a valid record into a "requires `value`"
        # refusal. Scoped to the override block on purpose (M4): the same
        # carve-out is applied identically by `dtos.AssertionRecordDTO.
        # _compact`, and two layers applying one null-drop with only one of
        # them carrying the exception is what broke the GET -> PUT round
        # trip.
        keep_null_value = block == "override"
        out.append({
            k: v for k, v in rec.items()
            if v is not None or (k == "value" and keep_null_value)
        })
    return out


def _require_note(rec: dict[str, Any], *, where: str) -> None:
    note = rec.get("note")
    if not isinstance(note, str) or not note.strip():
        raise ConsoleFileShapeError(
            f"{where}: every record carries a non-empty `note` (docs/design/"
            "14-operator-assertions.md §3) -- a canonical re-emit of this file "
            "drops YAML comments, so the note is what survives"
        )


def _reject_authored_confidence(rec: dict[str, Any], *, where: str) -> None:
    if "confidence" in rec:
        raise ConsoleFileShapeError(
            f"{where}: `confidence` is never authored -- an asserted fact is "
            "1.0 by convention (docs/design/14-operator-assertions.md §3). A "
            "file that could set it would let an operator tune the evidence "
            "mix that is the scorecard's only guardrail"
        )


def _canonical(rec: dict[str, Any]) -> dict[str, Any]:
    """One key order for every record, so a save produces a diff a human can
    read (and a no-op save is byte-identical)."""
    return {k: rec[k] for k in _CANONICAL_RECORD_ORDER if k in rec}


def _validate_add(
    value: Any, *, where: str, warnings: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, rec in enumerate(_records(value, block="add", where=where)):
        w = f"{where}: add[{i}]"
        _reject_authored_confidence(rec, where=w)
        declared = rec.get("kind")
        if declared in REFUSED_ADD_KINDS:
            raise ConsoleFileShapeError(
                f"{w}: `kind: {declared}` is refused in {ASSERTIONS_SCHEMA} -- "
                f"{REFUSED_ADD_KINDS[declared]}. Accepted kinds: {list(ADD_KINDS)}"
            )
        if not declared:
            raise ConsoleFileShapeError(
                f"{w}: `kind` is required on an add record; accepted: "
                f"{list(ADD_KINDS)}"
            )
        if declared not in ADD_KINDS:
            raise ConsoleFileShapeError(
                f"{w}: kind {declared!r} is not a record kind; accepted: "
                f"{list(ADD_KINDS)}"
            )
        kind = key_kind(rec.get("key"), where=w)
        if kind != declared:
            raise ConsoleFileShapeError(
                f"{w}: declares kind {declared!r} but key {rec.get('key')!r} "
                f"is a {kind} key"
            )
        allowed = ("kind", "key", "note") + ADD_FIELDS[kind]
        unknown = [k for k in rec if k not in allowed]
        if unknown:
            raise ConsoleFileShapeError(
                f"{w}: unknown field(s) {sorted(unknown)} on a {kind} add; "
                f"accepted: {list(allowed)}"
            )
        missing = [k for k in ADD_REQUIRED[kind] if not rec.get(k)]
        if missing:
            raise ConsoleFileShapeError(
                f"{w}: a {kind} add requires {list(ADD_REQUIRED[kind])}; "
                f"missing {missing}"
            )
        _require_note(rec, where=w)
        key = _canonical_key(kind, str(rec["key"]).strip(), where=w, warnings=warnings)
        fields = {
            name: _validate_field(kind, name, rec[name], where=f"{w}.{name}")
            for name in ADD_FIELDS[kind] if name in rec
        }
        if key in seen:
            raise ConsoleFileShapeError(
                f"{w}: key {key!r} is added twice -- one add per key "
                "(a second add is ambiguous, not a merge)"
            )
        seen.add(key)
        out.append(_canonical({**rec, **fields, "kind": kind, "key": key}))
    return out


def _validate_override(
    value: Any, *, where: str, warnings: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i, rec in enumerate(_records(value, block="override", where=where)):
        w = f"{where}: override[{i}]"
        _reject_authored_confidence(rec, where=w)
        kind = key_kind(rec.get("key"), where=w)
        unknown = [k for k in rec if k not in ("kind",) + _OVERRIDE_KEYS]
        if unknown:
            raise ConsoleFileShapeError(
                f"{w}: unknown field(s) {sorted(unknown)} on an override "
                f"record; accepted: {['kind'] + list(_OVERRIDE_KEYS)} "
                "(`confidence` is implied 1.0 and is never authored; an "
                "`evidence_class` is never authored either -- an assertion's "
                "class is a property of the CHANNEL; and `kind` is a "
                "transport-only discriminator, not written to the file)"
            )
        if rec.get("kind") and rec["kind"] != kind:
            raise ConsoleFileShapeError(
                f"{w}: declares kind {rec['kind']!r} but key {rec.get('key')!r} "
                f"is a {kind} key"
            )
        fields = OVERRIDE_FIELDS.get(kind)
        if not fields:
            raise ConsoleFileShapeError(
                f"{w}: nothing is overridable on a {kind}; overrides are "
                f"accepted on {sorted(OVERRIDE_FIELDS)} keys only"
            )
        field_name = rec.get("field")
        if field_name not in fields:
            raise ConsoleFileShapeError(
                f"{w}: field {field_name!r} is not overridable on a {kind}; "
                f"accepted: {list(fields)}"
            )
        if "value" not in rec:
            raise ConsoleFileShapeError(f"{w}: an override record requires `value`")
        _require_note(rec, where=w)
        key = _canonical_key(kind, str(rec["key"]).strip(), where=w, warnings=warnings)
        value_out = (
            None if rec["value"] is None
            else _validate_field(kind, field_name, rec["value"], where=f"{w}.value")
        )
        if (key, field_name) in seen:
            raise ConsoleFileShapeError(
                f"{w}: ({key!r}, {field_name!r}) is overridden twice -- one "
                "override per (key, field)"
            )
        seen.add((key, field_name))
        out.append(_canonical({**rec, "kind": kind, "key": key, "value": value_out}))
    return out


def _validate_suppress(
    value: Any, *, where: str, warnings: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, rec in enumerate(_records(value, block="suppress", where=where)):
        w = f"{where}: suppress[{i}]"
        _reject_authored_confidence(rec, where=w)
        kind = key_kind(rec.get("key"), where=w)
        if kind not in _SUPPRESS_KINDS:
            raise ConsoleFileShapeError(
                f"{w}: key {rec.get('key')!r} is a {kind} key; `suppress` "
                f"takes {list(_SUPPRESS_KINDS)} keys only -- removing a "
                "segment that still carries placed hosts is a topology edit, "
                "not a suppression"
            )
        unknown = [k for k in rec if k not in ("kind",) + _SUPPRESS_KEYS]
        if unknown:
            raise ConsoleFileShapeError(
                f"{w}: unknown field(s) {sorted(unknown)} on a suppress "
                f"record; accepted: {['kind'] + list(_SUPPRESS_KEYS)} (`kind` "
                "is a transport-only discriminator and is not written to the "
                "file)"
            )
        # S13: a mis-declared `kind` is REFUSED here as it already was on an
        # override, rather than silently rewritten -- the two blocks had two
        # different answers to one question.
        if rec.get("kind") and rec["kind"] != kind:
            raise ConsoleFileShapeError(
                f"{w}: declares kind {rec['kind']!r} but key {rec.get('key')!r} "
                f"is a {kind} key"
            )
        _require_note(rec, where=w)
        key = _canonical_key(kind, str(rec["key"]).strip(), where=w, warnings=warnings)
        if key in seen:
            raise ConsoleFileShapeError(f"{w}: key {key!r} is suppressed twice")
        seen.add(key)
        out.append(_canonical({**rec, "kind": kind, "key": key}))
    return out


def _canonical_key(
    kind: str, key: str, *, where: str, warnings: list[str],
) -> str:
    """The key as the FILE spells it. A segment key is normalized to its
    strict network form; a conduit key is sorted (see
    `_canonical_conduit_key`); a host key is left exactly as authored."""
    if kind == "segment":
        return _require_strict_cidr(key, where=f"{where}.key")
    if kind == "conduit":
        return _canonical_conduit_key(key, where=f"{where}.key")
    # S12, resolved against R1: the compiler does NOT parse the value part of
    # a host key, so refusing `address:not-an-ip` here would refuse a
    # document the reader of record accepts. Warn instead -- the operator is
    # told it will join against nothing, and the file stays writable.
    prefix, _, rest = key.partition(":")
    if prefix == "address":
        try:
            ipaddress.ip_address(rest.strip())
        except ValueError:
            warnings.append(
                f"{where}: key {key!r} does not carry a parseable IP address. "
                "The compiler accepts it, so it is written as authored -- but "
                "it will join against nothing in the belief store"
            )
    return key


def _validate_field(kind: str, name: str, value: Any, *, where: str) -> Any:
    """One record field's own shape, returning the canonical value.

    Mirrors the compiler's `_validate_field` branch for branch, including its
    final positive gate: a field that reaches here with no validator is a
    refusal, not a silent pass-through, so a vocabulary that grows in the
    schema and not here cannot ship unvalidated (LES-087)."""
    if name in ("role", "image"):
        if not isinstance(value, str) or not value.strip():
            raise ConsoleFileShapeError(
                f"{where}: a {name} is a non-empty string, got {value!r}"
            )
        return value.strip()
    if name == "segment":
        return _require_strict_cidr(value, where=where)
    if name == "resources":
        return _validate_resources(value, where=where)
    if name == "zone_type":
        if value not in ZONE_TYPES:
            raise ConsoleFileShapeError(
                f"{where}: unknown zone type {value!r}; accepted: {list(ZONE_TYPES)}"
            )
        return value
    if name == "name":
        if (not isinstance(value, str) or not value.strip()
                or any(c.isspace() for c in value)):
            raise ConsoleFileShapeError(
                f"{where}: a segment name is a non-empty identifier with no "
                f"whitespace -- it is what a node's `net:` references -- got "
                f"{value!r}"
            )
        return value
    if name in ("listen", "dial"):
        return _validate_ports(value, where=where)
    raise ConsoleFileShapeError(
        f"{where}: no validator is declared for field {name!r} on a {kind}; "
        f"accepted: {list(ADD_FIELDS.get(kind, ()))}"
    )


def _validate_resources(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConsoleFileShapeError(
            f"{where}: `resources` must be a mapping; accepted keys: "
            f"{list(RESOURCE_KEYS)}"
        )
    extra = sorted(set(value) - set(RESOURCE_KEYS))
    if extra:
        raise ConsoleFileShapeError(
            f"{where}: unknown resource key(s) {extra}; accepted: "
            f"{list(RESOURCE_KEYS)}. axRange's own `twin_config` is "
            '`extra="forbid"`, so a fourth key is refused at deploy time, '
            "one repo and several hours later"
        )
    if not value:
        raise ConsoleFileShapeError(f"{where}: an empty `resources` block asserts nothing")
    for rk in sorted(value):
        rv = value[rk]
        if isinstance(rv, bool) or not isinstance(rv, (str, int)):
            raise ConsoleFileShapeError(
                f"{where}.{rk}: must be a string or an integer, got "
                f"{type(rv).__name__}"
            )
    return {k: value[k] for k in sorted(value)}


def _validate_ports(value: Any, *, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ConsoleFileShapeError(
            f"{where}: must be a non-empty list of {{port, proto}} entries, "
            f"got {value!r}"
        )
    out = []
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ConsoleFileShapeError(
                f"{where}[{i}]: must be a mapping; accepted: {list(_PORT_ENTRY_KEYS)}"
            )
        unknown = [k for k in entry if k not in _PORT_ENTRY_KEYS]
        if unknown:
            raise ConsoleFileShapeError(
                f"{where}[{i}]: unknown field(s) {sorted(unknown)}; accepted: "
                f"{list(_PORT_ENTRY_KEYS)}"
            )
        port = entry.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConsoleFileShapeError(
                f"{where}[{i}]: `port` must be an integer 1..65535, got {port!r}"
            )
        proto = entry.get("proto")
        if proto not in PORT_PROTOS:
            raise ConsoleFileShapeError(
                f"{where}[{i}]: unknown protocol {proto!r}; accepted: "
                f"{list(PORT_PROTOS)}. These become `\"<port>/<proto>\"` "
                "strings joined against the IR's own port columns, whose "
                "producer is Zeek's `proto` column, so a value outside this "
                "set would match nothing"
            )
        # Authored ORDER is preserved (the compiler dedupes and sorts
        # internally but accepts either); only the shape is canonicalized.
        out.append({"port": port, "proto": proto})
    return out


def _cross_check(
    add: list[dict[str, Any]], override: list[dict[str, Any]],
    suppress: list[dict[str, Any]], *, where: str,
) -> None:
    """The two cross-record refusals `14` §3 names. Both are refusals rather
    than precedence rules ON PURPOSE: axRange does not resolve precedence,
    so a document that needs precedence to be read is a document this writer
    must not write."""
    add_keys = {r["key"] for r in add}
    for rec in suppress:
        if rec["key"] in add_keys:
            raise ConsoleFileShapeError(
                f"{where}: key {rec['key']!r} appears in both `add` and "
                "`suppress` -- that is a refusal, not a precedence "
                "(docs/design/14-operator-assertions.md §3)"
            )
    add_fields = {
        (r["key"], f) for r in add for f in r
        if f not in ("kind", "key", "note")
    }
    for rec in override:
        if (rec["key"], rec["field"]) in add_fields:
            raise ConsoleFileShapeError(
                f"{where}: ({rec['key']!r}, {rec['field']!r}) appears in both "
                "`add` and `override` -- that is a refusal, not a precedence; "
                "carry the value on the add record alone"
            )


# ---------------------------------------------------------------------------
# The layout (pins) document -- never evidence
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LayoutDoc:
    imprint_id: str
    path: Path
    exists: bool
    etag: Optional[str]
    schema: str
    imprint: str
    pins: dict[str, dict[str, float]] = field(default_factory=dict)
    #: The layout plane the pins were authored against, or `None` for a LEGACY
    #: file that carries none. Opaque here (see `_PLANE_RE`).
    plane: Optional[str] = None
    #: The canvas's annotations (`_ANNOTATION_KEYS`), canonical; `[]` when the
    #: file carries none.
    annotations: list[dict[str, Any]] = field(default_factory=list)


def read_layout(bundle_dir: Path, imprint_id: str) -> LayoutDoc:
    path = layout_path(bundle_dir)
    text = _read_text(path)
    if text is None:
        return LayoutDoc(
            imprint_id=imprint_id, path=path, exists=False, etag=None,
            schema=LAYOUT_SCHEMA, imprint=imprint_id, pins={}, plane=None,
        )
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise ConsoleFileShapeError(f"{path.name}: not valid JSON: {e}") from e
    doc = validate_layout(raw, imprint_id, where=path.name)
    return LayoutDoc(
        imprint_id=imprint_id, path=path, exists=True, etag=compute_etag(text),
        schema=doc["schema"], imprint=doc["imprint"], pins=doc["pins"],
        plane=doc.get("plane"), annotations=list(doc.get("annotations") or []),
    )


def write_layout(
    bundle_dir: Path, imprint_id: str, body: dict[str, Any], *,
    if_match: Optional[str],
) -> LayoutDoc:
    path = layout_path(bundle_dir)
    doc = validate_layout(body, imprint_id, where="request body")
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    try:
        # A twin folder's `generated/` may not exist yet (a hand-authored
        # twin's first pin). A failure here is reported by `atomic_write`.
        path.parent.mkdir(exist_ok=True)
    except OSError:
        pass
    with _locked(path):
        _guard_etag(path, _read_text(path), if_match)
        atomic_write(path, text)
    return LayoutDoc(
        imprint_id=imprint_id, path=path, exists=True, etag=compute_etag(text),
        schema=doc["schema"], imprint=doc["imprint"], pins=doc["pins"],
        plane=doc.get("plane"), annotations=list(doc.get("annotations") or []),
    )


def validate_layout(raw: Any, imprint_id: str, *, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConsoleFileShapeError(
            f"{where}: top level must be a mapping with keys {list(_LAYOUT_KEYS)}"
        )
    body = {k: v for k, v in raw.items() if k not in _READ_ONLY_KEYS}
    unknown = [k for k in body if k not in _LAYOUT_KEYS]
    if unknown:
        raise ConsoleFileShapeError(
            f"{where}: unknown top-level key(s) {sorted(unknown)}; accepted: "
            f"{list(_LAYOUT_KEYS)}"
        )
    schema = body.get("schema")
    if schema != LAYOUT_SCHEMA:
        raise ConsoleFileShapeError(
            f"{where}: schema {schema!r} is not {LAYOUT_SCHEMA!r}"
        )
    if body.get("imprint") != imprint_id:
        raise ConsoleFileShapeError(
            f"{where}: document declares imprint {body.get('imprint')!r} but is "
            f"being written for {imprint_id!r}"
        )
    pins_raw = body.get("pins") or {}
    if not isinstance(pins_raw, dict):
        raise ConsoleFileShapeError(f"{where}: `pins` must be a mapping")
    pins: dict[str, dict[str, float]] = {}
    for key, value in pins_raw.items():
        _validate_pin_key(str(key), where=where)
        if not isinstance(value, dict):
            raise ConsoleFileShapeError(
                f"{where}: pin {key!r} must be a mapping {{x, y}}"
            )
        unknown_pin = [k for k in value if k not in ("x", "y")]
        if unknown_pin:
            raise ConsoleFileShapeError(
                f"{where}: pin {key!r} has unknown field(s) "
                f"{sorted(unknown_pin)}; accepted: ['x', 'y']"
            )
        coords = {}
        for axis in ("x", "y"):
            v = value.get(axis)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConsoleFileShapeError(
                    f"{where}: pin {key!r} `{axis}` must be a number, got {v!r}"
                )
            coords[axis] = float(v)
        pins[str(key)] = coords
    doc: dict[str, Any] = {
        "schema": LAYOUT_SCHEMA, "imprint": imprint_id, "pins": pins,
    }
    # ABSENT stays absent: a document written with no stamp is not rewritten
    # with `plane: null`, so a legacy file that is read and re-saved unchanged
    # stays byte-identical and the "no stamp" state remains a fact about the
    # file rather than a value this writer invented.
    plane = body.get("plane")
    if plane is not None:
        doc["plane"] = _canonical_plane(plane, where=where)
    # Same absent-stays-absent rule for the annotations, and an EMPTY list
    # is the same statement as no key ("no annotations" is one state): a GET
    # answers `[]` for a legacy file, and PUTting that answer back verbatim
    # leaves the file byte-identical. The shape is checked either way.
    if body.get("annotations") is not None:
        annotations = _canonical_annotations(body["annotations"], where=where)
        if annotations:
            doc["annotations"] = annotations
    return doc


def _canonical_annotations(raw: Any, *, where: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ConsoleFileShapeError(f"{where}: `annotations` must be a list")
    if len(raw) > _ANNOTATIONS_MAX:
        raise ConsoleFileShapeError(
            f"{where}: {len(raw)} annotations; at most {_ANNOTATIONS_MAX}"
        )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        label = f"annotations[{i}]"
        if not isinstance(item, dict):
            raise ConsoleFileShapeError(f"{where}: {label} must be a mapping")
        unknown = [k for k in item if k not in _ANNOTATION_KEYS]
        if unknown:
            raise ConsoleFileShapeError(
                f"{where}: {label} has unknown field(s) {sorted(unknown)}; "
                f"accepted: {list(_ANNOTATION_KEYS)}"
            )
        aid = item.get("id")
        if (
            not isinstance(aid, str) or not aid or len(aid) > _ANNOTATION_ID_MAX_LEN
            or not _ANNOTATION_ID_RE.match(aid)
        ):
            raise ConsoleFileShapeError(
                f"{where}: {label} `id` must be 1-{_ANNOTATION_ID_MAX_LEN} characters "
                f"from [A-Za-z0-9._:-], got {aid!r}"
            )
        if aid in seen:
            raise ConsoleFileShapeError(f"{where}: {label} `id` {aid!r} is used twice")
        seen.add(aid)
        kind = item.get("kind")
        if kind not in _ANNOTATION_KINDS:
            raise ConsoleFileShapeError(
                f"{where}: {label} `kind` {kind!r} is not one of {list(_ANNOTATION_KINDS)}"
            )
        canon: dict[str, Any] = {"id": aid, "kind": kind}
        for axis in ("x", "y"):
            v = item.get(axis)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConsoleFileShapeError(
                    f"{where}: {label} `{axis}` must be a number, got {v!r}"
                )
            canon[axis] = float(v)
        for size in ("w", "h"):
            v = item.get(size)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ConsoleFileShapeError(
                    f"{where}: {label} `{size}` must be a non-negative number, got {v!r}"
                )
            canon[size] = float(v)
        svg = item.get("svg")
        if not isinstance(svg, str) or not svg.strip():
            raise ConsoleFileShapeError(f"{where}: {label} `svg` must be a non-empty string")
        if len(svg.encode("utf-8")) > _ANNOTATION_SVG_MAX_BYTES:
            raise ConsoleFileShapeError(
                f"{where}: {label} `svg` is {len(svg.encode('utf-8'))} bytes; at most "
                f"{_ANNOTATION_SVG_MAX_BYTES}"
            )
        _check_svg(svg, where=where, label=label)
        canon["svg"] = svg
        for flag in ("locked", "hidden"):
            v = item.get(flag, False)
            if not isinstance(v, bool):
                raise ConsoleFileShapeError(
                    f"{where}: {label} `{flag}` must be a boolean, got {v!r}"
                )
            canon[flag] = v
        out.append(canon)
    return out


def _check_svg(svg: str, *, where: str, label: str) -> None:
    """Refuse, by name, what must never be served back to a browser from a
    layout file. A match is refused outright; nothing is stripped."""
    for pattern, what in _SVG_REFUSED:
        if pattern.search(svg):
            raise ConsoleFileShapeError(
                f"{where}: {label} `svg` carries {what}, which a layout annotation "
                "may not (the file is served back to browsers)"
            )


def _canonical_plane(value: Any, *, where: str) -> str:
    """Shape-check the plane stamp. Opaque, bounded, and never interpreted --
    the SPA owns what a stamp MEANS (see `_PLANE_RE`'s note)."""
    if not isinstance(value, str):
        raise ConsoleFileShapeError(
            f"{where}: `plane` must be a string, got {type(value).__name__}"
        )
    text = value.strip()
    if not text:
        raise ConsoleFileShapeError(
            f"{where}: `plane` is empty -- OMIT the key to mean 'no stamp' "
            "(a legacy file), which is a different statement from an empty one"
        )
    if len(text) > _PLANE_MAX_LEN or not _PLANE_RE.match(text):
        raise ConsoleFileShapeError(
            f"{where}: `plane` {value!r} is not an accepted stamp -- at most "
            f"{_PLANE_MAX_LEN} characters from [A-Za-z0-9._:-]. It is an "
            "opaque token minted by the Console (`o2/console/src/layout/"
            "plane.ts`); this writer round-trips it and never interprets it"
        )
    return text


_SITE_KEY_RE = re.compile(r"^site:[0-9A-Za-z._:-]+$")
# A host pin names the node by its graph id (an entity key such as
# `address:10.80.20.4`, or a twin-config node name): anything printable but
# whitespace, bounded so a pasted document cannot carry an unbounded key.
_HOST_PIN_RE = re.compile(r"^host:\S{1,200}$")


def _validate_pin_key(key: str, *, where: str) -> None:
    for bad in _REFUSED_PIN_PREFIXES:
        if key.startswith(bad):
            raise ConsoleFileShapeError(
                f"{where}: pin key {key!r} names a host by its entity key -- "
                "hosts are NEVER pinnable by entity key "
                "(docs/design/15-console-canvas.md §4); a host chip is pinned "
                f"as `host:<name>`; accepted prefixes: {list(_PIN_PREFIXES)}"
            )
    if key.startswith("segment:"):
        cidr = key.split(":", 1)[1]
        if _loose_cidr(cidr) is None:
            raise ConsoleFileShapeError(
                f"{where}: pin key {key!r} must be `segment:<cidr>`; "
                f"{cidr!r} is not a CIDR"
            )
        return
    if key.startswith("host:"):
        if not _HOST_PIN_RE.match(key):
            raise ConsoleFileShapeError(
                f"{where}: pin key {key!r} must be `host:<name>` -- a "
                "non-empty name with no whitespace, at most 200 characters"
            )
        return
    if _SITE_KEY_RE.match(key):
        return
    raise ConsoleFileShapeError(
        f"{where}: pin key {key!r} matches no accepted shape; accepted "
        f"prefixes: {list(_PIN_PREFIXES)} (`site:<prefix>`, `segment:<cidr>`, "
        "`host:<name>`)"
    )
