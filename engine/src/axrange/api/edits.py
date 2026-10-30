"""The edit model's storage and classification (`api/Spec.md`, "The edit
model: baseline + working copy"; seams A2 and A3).

Three things live here, none of which touches the engine:

* **The twin-config document** -- a bundle (`catalog/twins/<id>/`) as the
  editable object: every text file in the folder keyed by its bundle-relative
  path (`te.yaml`, `twin.yaml`, `tfc/default.yaml`, `tap/default.yaml`,
  `personas/*.yaml`, ...). `read_document` / `write_document` are the ONE
  reader and writer; `generated/` (the cast's machine-written evidence,
  `twinconfig.bundle`), `.history`, dot-files and `*.tmp` are never part of
  it, so a Persist cannot clobber a cast's provenance and a Save-As never
  copies it. Keys are normalised ONCE at the boundary (`normalize_files`);
  bytes round-trip exactly (`newline=""` both ways).

* **The working copy store** -- one SQLite file BESIDE the planner state DB
  (`<state>.edits.db`, `working_copy_db_path`), one row per deploy. Beside,
  not inside: `planner/state.py` gates every open on `SCHEMA_VERSION` and a
  complete object set, so a new table there is a schema bump that refuses
  every existing state DB ("re-init required"), and `api/Spec.md` A7 already
  rules that non-planner records do not go into `state.db`, "whose schema is
  its own". The sidecar follows the deploy record's store (a target with
  several stores keeps each twin's copy next to its own record), is server-
  side so a console on any host reads the same copy, a read opens it
  read-only and never creates it, and every applied-state merge is one
  `BEGIN IMMEDIATE` transaction that asserts the row is still there.

* **The classifier** -- which tier file a pending change touches decides
  what Apply may do (the spec's table): the tfc default (and what it reaches
  through `roles_from:`), the personas the default profile assigns, and the
  tap default are attribute changes the tier's own playout re-applies;
  `te.yaml` is topology (`requires-replan`) unless the only difference is a
  node's `role` with its `image` unchanged; `twin.yaml` is attribute when it
  only re-wires the tfc/tap/exr tier files, topology when it touches the te
  tier or the twin's identity; a tier file no playout reads (a variant, an
  unassigned persona), `exr/` and top-level prose are inert. A comment-only
  edit is no change at all. Anything unrecognised fails closed as topology.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

#: Folders that are never part of the editable document, wherever they sit.
SKIP_DIRS = frozenset({"generated", ".history", "__pycache__"})
#: The temp suffix `console_files.atomic_write` uses; never a document file.
TMP_SUFFIX = ".tmp"

MANIFEST = "twin.yaml"

KIND_ATTRIBUTE = "attribute"
KIND_TOPOLOGY = "topology"
KIND_INERT = "inert"

#: The refusal codes Apply answers with.
REQUIRES_REPLAN = "requires-replan"
BASELINE_DRIFT = "baseline-drift"

#: Applied-state values per tier (`WorkingCopy.applied["tiers"]`).
APPLIED_ATTEMPTED = "attempted"
APPLIED_DONE = "done"


class DocumentError(ValueError):
    """A document that cannot be read or written as a bundle (a bad relative
    path, a te file that is not this twin, a missing te file). The message
    is the operator-facing reason."""


class WorkingCopyGone(RuntimeError):
    """The working-copy row vanished under an operation that needed it."""


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def check_relpath(rel: str) -> str:
    """A bundle-relative POSIX path the document may carry, normalised
    (`./a//b` -> `a/b`). Refuses absolute paths, backslashes, `.`/`..`
    parts, dot-files, `*.tmp` and the machine-written folders ANYWHERE in
    the path."""
    if not isinstance(rel, str) or not rel.strip():
        raise DocumentError("a document path must be a non-empty string")
    if "\\" in rel:
        raise DocumentError(f"document path {rel!r}: use '/' separators")
    if rel.startswith("/") or rel.startswith("~"):
        raise DocumentError(f"document path {rel!r} must be relative to the bundle")
    p = PurePosixPath(rel)
    parts = p.parts
    if not parts or any(not part or part in (".", "..") for part in parts):
        raise DocumentError(f"document path {rel!r} may not contain '.' or '..' parts")
    if any(part.startswith(".") for part in parts):
        raise DocumentError(f"document path {rel!r}: dot-files are not part of a document")
    bad = [part for part in parts if part in SKIP_DIRS]
    if bad:
        raise DocumentError(
            f"document path {rel!r}: {bad[0]!r} is machine-written and not editable"
        )
    if parts[-1].endswith(TMP_SUFFIX):
        raise DocumentError(f"document path {rel!r}: '*{TMP_SUFFIX}' names a temp file")
    return str(p)


def normalize_files(files: Any) -> dict[str, str]:
    """`files` as the request carried it -> `{normalised relpath: text}`.
    The one place a document's keys are checked; every reader and writer
    below takes its output. Two keys that normalise to one path, or a
    non-text value, are refused."""
    if not isinstance(files, dict):
        raise DocumentError("'files' must be an object of {path: text}")
    out: dict[str, str] = {}
    for rel, text in files.items():
        norm = check_relpath(rel)
        if not isinstance(text, str):
            raise DocumentError(f"document file {rel!r} must be text")
        if norm in out:
            raise DocumentError(f"document path {rel!r} duplicates {norm!r}")
        out[norm] = text
    return out


def _is_document_file(rel: PurePosixPath | Path) -> bool:
    parts = rel.parts
    return not any(
        part in SKIP_DIRS or part.startswith(".") for part in parts
    ) and not parts[-1].endswith(TMP_SUFFIX)


def _read_text_exact(path: Path) -> str:
    # `newline=""`: no CRLF translation, so a read after a write is the
    # written bytes (the console round-trips Windows-authored files).
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def read_document(bundle_dir: str | Path) -> tuple[dict[str, str], list[str]]:
    """Every text file under `bundle_dir` (recursively, sorted), keyed by its
    relative POSIX path, plus the relative paths of files that were SKIPPED
    because they are not UTF-8 text (a pcap under `scripts/`, say). Skipped
    files are not part of the document: a write leaves them in place."""
    d = Path(bundle_dir)
    files: dict[str, str] = {}
    skipped: list[str] = []
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(d)
        if not _is_document_file(rel):
            continue
        try:
            files[rel.as_posix()] = _read_text_exact(p)
        except UnicodeDecodeError:
            skipped.append(rel.as_posix())
    return files, skipped


def read_single_file_document(path: str | Path) -> dict[str, str]:
    """The legacy flat layout (`<root>/<id>.yaml`, no folder): the document
    is that one file."""
    p = Path(path)
    return {p.name: _read_text_exact(p)}


def document_hash(files: dict[str, str]) -> str:
    """A content hash of a document, key order independent."""
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def write_document(
    bundle_dir: str | Path, files: dict[str, str], *, replace: bool,
) -> None:
    """Write `files` (already normalised) under `bundle_dir`, created as
    needed. Each file lands through `console_files.atomic_write` (a unique
    dot-prefixed temp + `os.replace`), so a reader never sees a half-written
    file; a target that resolves outside the bundle through a symlink is
    refused. New content is written BEFORE anything stale goes: with
    `replace`, every document file already in the folder that `files` no
    longer names is then removed and emptied folders pruned -- the folder
    then IS the document (skipped binaries and `generated/` stay)."""
    from axrange.api.console_files import ConsoleFileWriteError, atomic_write

    d = Path(bundle_dir)
    d.mkdir(parents=True, exist_ok=True)
    root = d.resolve()
    targets: list[tuple[Path, str]] = []
    for rel, text in files.items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _inside(root, target.parent.resolve()) or target.is_symlink():
            raise DocumentError(f"document path {rel!r} escapes the bundle through a link")
        targets.append((target, text))
    try:
        for target, text in targets:
            atomic_write(target, text)
    except ConsoleFileWriteError as e:
        raise DocumentError(str(e)) from e
    if replace:
        existing, _skipped = read_document(d)
        for rel in existing:
            if rel not in files:
                (d / rel).unlink(missing_ok=True)
        _prune_empty_dirs(d)


def _prune_empty_dirs(root: Path) -> None:
    for p in sorted((q for q in root.rglob("*") if q.is_dir()), reverse=True):
        if p.name in SKIP_DIRS or p.name.startswith("."):
            continue
        try:
            p.rmdir()  # only succeeds when empty
        except OSError:
            pass


def _yaml() -> YAML:
    return YAML(typ="safe")


def parse_yaml(text: str) -> Any:
    """`None` when the text is not YAML (the caller treats an unparseable
    tier file as a whole-file change)."""
    try:
        return _yaml().load(io.StringIO(text))
    except Exception:  # noqa: BLE001 -- ruamel's own error family
        return None


def _manifest_tier_default(files: dict[str, str], tier: str) -> str | None:
    """`twin.yaml`'s `tiers.<tier>.default`, normalised; None without one.
    Returns the sentinel `""` when the manifest is present but names none."""
    manifest = files.get(MANIFEST)
    if manifest is None:
        return None
    raw = parse_yaml(manifest)
    twin = raw.get("twin") if isinstance(raw, dict) else None
    tiers = twin.get("tiers") if isinstance(twin, dict) else None
    t = tiers.get(tier) if isinstance(tiers, dict) else None
    default = t.get("default") if isinstance(t, dict) else None
    if isinstance(default, str) and default:
        try:
            return check_relpath(default)
        except DocumentError:
            return ""
    return ""


def te_file_of(twin_id: str, files: dict[str, str]) -> str:
    """Which document file is the twin config: the manifest's
    `tiers.te.default`, else `te.yaml`, else `<id>.yaml`. Raises
    `DocumentError` when the document has none."""
    default = _manifest_tier_default(files, "te")
    if default:
        return default
    for cand in ("te.yaml", f"{twin_id}.yaml"):
        if cand in files:
            return cand
    raise DocumentError(
        f"document for {twin_id!r} has no twin config: expected {MANIFEST} naming "
        f"tiers.te.default, or te.yaml, or {twin_id}.yaml"
    )


def check_document_identity(twin_id: str, files: dict[str, str]) -> str:
    """The document must BE `twin_id`: its te file's `twin_config.id` (and
    its manifest's `twin.id`, when present) must equal it -- the registry
    lists by declared id, so a Save-As whose body still says the source's id
    would land a folder listed under another name. Returns the te file."""
    te = te_file_of(twin_id, files)
    raw = parse_yaml(files[te])
    tc = raw.get("twin_config") if isinstance(raw, dict) else None
    declared = tc.get("id") if isinstance(tc, dict) else None
    if declared != twin_id:
        raise DocumentError(
            f"{te}: twin_config.id {declared!r} must equal {twin_id!r} "
            "(the id the document is saved under)"
        )
    manifest = files.get(MANIFEST)
    if manifest is not None:
        raw_m = parse_yaml(manifest)
        twin = raw_m.get("twin") if isinstance(raw_m, dict) else None
        m_id = twin.get("id") if isinstance(twin, dict) else None
        if m_id != twin_id:
            raise DocumentError(f"{MANIFEST}: twin.id {m_id!r} must equal {twin_id!r}")
    return te


def tier_file_of(files: dict[str, str], tier: str) -> str | None:
    """The document's DEFAULT file for `tier` (`tfc`|`tap`|`exr`): the
    manifest's `tiers.<tier>.default`, else `<tier>/default.yaml` when the
    document has it and there is no manifest. None when the tier has no
    default."""
    default = _manifest_tier_default(files, tier)
    if default is not None:
        return default if default and default in files else None
    cand = f"{tier}/default.yaml"
    return cand if cand in files else None


def profile_reach(files: dict[str, str], profile_rel: str | None) -> set[str]:
    """The tfc files the playout reads for a profile: the file itself and
    the `roles_from:` fragment it names (one level, relative to it)."""
    if not profile_rel or profile_rel not in files:
        return set()
    out = {profile_rel}
    raw = parse_yaml(files[profile_rel])
    body = raw.get("traffic-profile") if isinstance(raw, dict) else None
    roles_from = body.get("roles_from") if isinstance(body, dict) else None
    if isinstance(roles_from, str) and roles_from:
        try:
            out.add(check_relpath(str(PurePosixPath(profile_rel).parent / roles_from)))
        except DocumentError:
            pass
    return out


def profile_personas(files: dict[str, str], profile_rel: str | None) -> set[str]:
    """The persona ids the default profile assigns."""
    if not profile_rel or profile_rel not in files:
        return set()
    raw = parse_yaml(files[profile_rel])
    body = raw.get("traffic-profile") if isinstance(raw, dict) else None
    assignments = body.get("assignments") if isinstance(body, dict) else None
    return {
        str(a["persona"]) for a in (assignments or [])
        if isinstance(a, dict) and a.get("persona")
    }


def persona_id_of(text: str | None) -> str | None:
    raw = parse_yaml(text or "")
    p = raw.get("persona") if isinstance(raw, dict) else None
    pid = p.get("id") if isinstance(p, dict) else None
    return str(pid) if pid else None


# ---------------------------------------------------------------------------
# The working copy store
# ---------------------------------------------------------------------------
def working_copy_db_path(state_db_path: str | Path) -> Path:
    """`~/.axrange/state.db` -> `~/.axrange/state.edits.db`: beside the deploy
    record's store, never inside it (module docstring)."""
    p = Path(state_db_path)
    stem = p.name[: -len(p.suffix)] if p.suffix else p.name
    return p.with_name(f"{stem}.edits.db")


_EDITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS working_copies (
    deploy_id     TEXT PRIMARY KEY,
    twin_id       TEXT NOT NULL,
    document_json TEXT NOT NULL,
    baseline_hash TEXT NOT NULL DEFAULT '',
    applied_json  TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
"""

#: Paths whose DDL ran in this process (once per path, never on a read).
_INITIALIZED: set[Path] = set()
_INIT_LOCK = threading.Lock()


@dataclass
class WorkingCopy:
    deploy_id: str
    twin_id: str
    files: dict[str, str]
    #: `document_hash` of the baseline the copy was first taken from.
    baseline_hash: str
    #: What Apply pushed onto the running twin from this copy, or None:
    #: `{"at": <epoch>, "tiers": {"tfc": "done"|"attempted", ...},
    #: "roles": {group: role}}`.
    applied: dict[str, Any] | None
    created_at: float
    updated_at: float


class WorkingCopyStore:
    """The per-deploy working copies of one state store's deploys. A READ
    opens the file read-only (`mode=ro`) and answers "no copy" when it does
    not exist; the first PUT creates it (mode 0600 under a 0700 directory,
    `O_CREAT|O_EXCL`) and runs the DDL -- once per path per process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, state_db_path: str | Path) -> WorkingCopyStore:
        return cls(working_copy_db_path(state_db_path))

    # -- open --
    def _open_ro(self) -> sqlite3.Connection | None:
        if not self.path.is_file():
            return None
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _open_rw(self) -> sqlite3.Connection:
        key = self.path.resolve() if self.path.exists() else self.path.absolute()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass  # a concurrent creator won; the DDL below is idempotent
        conn = sqlite3.connect(str(self.path), isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        with _INIT_LOCK:
            if key not in _INITIALIZED:
                conn.executescript(_EDITS_SCHEMA)
                _INITIALIZED.add(key)
        return conn

    @staticmethod
    def _select(conn: sqlite3.Connection, where: str, params: tuple) -> list[tuple]:
        try:
            return conn.execute(
                "SELECT deploy_id, twin_id, document_json, baseline_hash, applied_json, "
                f"created_at, updated_at FROM working_copies WHERE {where}", params,
            ).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return []  # created but never initialised: nothing recorded
            raise

    # -- reads --
    def get(self, deploy_id: str) -> WorkingCopy | None:
        conn = self._open_ro()
        if conn is None:
            return None
        try:
            rows = self._select(conn, "deploy_id = ?", (deploy_id,))
        finally:
            conn.close()
        return _row(rows[0]) if rows else None

    def for_twin(self, twin_id: str) -> list[WorkingCopy]:
        conn = self._open_ro()
        if conn is None:
            return []
        try:
            rows = self._select(conn, "twin_id = ? ORDER BY deploy_id", (twin_id,))
        finally:
            conn.close()
        return [_row(r) for r in rows]

    # -- writes --
    def put(
        self, deploy_id: str, twin_id: str, files: dict[str, str], *, baseline_hash: str,
    ) -> WorkingCopy:
        """Replace the copy's document. The baseline hash is recorded at the
        FIRST put and kept after; what was applied from the previous document
        stays recorded (Reset still has to revert it)."""
        now = time.time()
        conn = self._open_rw()
        try:
            conn.execute(
                "INSERT INTO working_copies (deploy_id, twin_id, document_json, "
                "baseline_hash, applied_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?) "
                "ON CONFLICT(deploy_id) DO UPDATE SET twin_id = excluded.twin_id, "
                "document_json = excluded.document_json, updated_at = excluded.updated_at",
                (deploy_id, twin_id, json.dumps(files, sort_keys=True), baseline_hash, now, now),
            )
            rows = self._select(conn, "deploy_id = ?", (deploy_id,))
        finally:
            conn.close()
        assert rows
        return _row(rows[0])

    def mark_tier(self, deploy_id: str, tier: str, state: str, *,
                  roles: dict[str, str] | None = None) -> dict[str, Any]:
        """Merge one tier's applied state (and any role re-assignments) into
        the row's record -- read-merge-write inside ONE `BEGIN IMMEDIATE`,
        asserting the row is still there (`WorkingCopyGone` otherwise)."""
        conn = self._open_rw()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT applied_json FROM working_copies WHERE deploy_id = ?", (deploy_id,),
                ).fetchone()
                if row is None:
                    raise WorkingCopyGone(f"working copy of {deploy_id} is gone")
                applied: dict[str, Any] = json.loads(row[0]) if row[0] else {}
                tiers = dict(applied.get("tiers") or {})
                tiers[tier] = state
                applied = {
                    "at": time.time(), "tiers": tiers,
                    "roles": {**(applied.get("roles") or {}), **(roles or {})},
                }
                cur = conn.execute(
                    "UPDATE working_copies SET applied_json = ?, updated_at = ? "
                    "WHERE deploy_id = ?",
                    (json.dumps(applied, sort_keys=True), applied["at"], deploy_id),
                )
                if cur.rowcount != 1:
                    raise WorkingCopyGone(f"working copy of {deploy_id} is gone")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return applied

    def delete(self, deploy_id: str) -> bool:
        if not self.path.is_file():
            return False
        conn = self._open_rw()
        try:
            cur = conn.execute("DELETE FROM working_copies WHERE deploy_id = ?", (deploy_id,))
            return cur.rowcount > 0
        finally:
            conn.close()


def _row(r: tuple[Any, ...]) -> WorkingCopy:
    return WorkingCopy(
        deploy_id=r[0], twin_id=r[1], files=json.loads(r[2]), baseline_hash=r[3] or "",
        applied=json.loads(r[4]) if r[4] else None,
        created_at=float(r[5]), updated_at=float(r[6]),
    )


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------
@dataclass
class PendingChange:
    path: str              # the document file
    op: str                # added | removed | modified
    kind: str              # attribute | topology | inert
    via: list[str] = field(default_factory=list)   # tiers whose playout re-applies
    paths: list[str] = field(default_factory=list)  # the differing paths inside the file
    reason: str = ""
    #: For a te `role` change classified attribute: node name -> new role.
    roles: dict[str, str] = field(default_factory=dict)


_ROLE_PATH = re.compile(r"^twin_config\.nodes\[(?P<name>[^\]]+)\]\.role$")
_IMAGE_PATH = re.compile(r"^twin_config\.nodes\[(?P<name>[^\]]+)\]\.image$")
_MANIFEST_ATTRIBUTE_PREFIXES = (
    "twin.tiers.tfc", "twin.tiers.tap", "twin.tiers.exr", "twin.description",
)
_MAX_PATHS = 40


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


def yaml_diff(a: Any, b: Any, prefix: str = "") -> list[str]:
    """Dotted paths at which two parsed YAML values differ. A `nodes[]` /
    `segments[]` list under `twin_config` is matched by `name`, so a
    re-ordered or edited entry reports as `nodes[<name>]...` rather than by
    index; other lists are compared by index."""
    out: list[str] = []
    _diff(a, b, prefix, out)
    return out


def _diff(a: Any, b: Any, path: str, out: list[str]) -> None:
    if len(out) > _MAX_PATHS:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            sub = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                out.append(sub)
            else:
                _diff(a[key], b[key], sub, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if _named_list(a) and _named_list(b):
            by_a = {x["name"]: x for x in a}
            by_b = {x["name"]: x for x in b}
            for name in sorted(set(by_a) | set(by_b), key=str):
                sub = f"{path}[{name}]"
                if name not in by_a or name not in by_b:
                    out.append(sub)
                else:
                    _diff(by_a[name], by_b[name], sub, out)
            if [x["name"] for x in a] != [x["name"] for x in b] and set(by_a) == set(by_b):
                out.append(f"{path} (order)")
            return
        if len(a) != len(b):
            out.append(f"{path}.length")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
        return
    if a != b:
        out.append(path or "(document)")


def _named_list(xs: list[Any]) -> bool:
    return bool(xs) and all(
        isinstance(x, dict) and isinstance(x.get("name"), str) for x in xs
    ) and len({x["name"] for x in xs}) == len(xs)


def _node_by_name(doc: Any) -> dict[str, dict[str, Any]]:
    tc = doc.get("twin_config") if isinstance(doc, dict) else None
    nodes = tc.get("nodes") if isinstance(tc, dict) else None
    if not isinstance(nodes, list):
        return {}
    return {
        n["name"]: n for n in nodes if isinstance(n, dict) and isinstance(n.get("name"), str)
    }


def classify_te(path: str, before: str, after: str) -> PendingChange | None:
    """A modified te file: None when the YAML is semantically unchanged (a
    comment or whitespace edit); attribute when every difference is a node's
    `role` whose `image` is unchanged (the spec's `role` row); topology
    otherwise -- including a file that no longer parses (fail closed)."""
    a, b = parse_yaml(before), parse_yaml(after)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return PendingChange(
            path=path, op="modified", kind=KIND_TOPOLOGY, paths=[path],
            reason="twin config does not parse as a mapping",
        )
    paths = yaml_diff(a, b)
    if not paths:
        return None
    nodes_a, nodes_b = _node_by_name(a), _node_by_name(b)
    roles: dict[str, str] = {}
    re_imaged: list[str] = []
    for p in paths:
        m = _ROLE_PATH.match(p)
        if m is None:
            m_img = _IMAGE_PATH.match(p)
            if m_img is not None and m_img.group("name") in nodes_a and m_img.group("name") in nodes_b:
                re_imaged.append(m_img.group("name"))
                continue
            return PendingChange(
                path=path, op="modified", kind=KIND_TOPOLOGY, paths=paths,
                reason="segments, nodes, attachments, counts or images changed: "
                       "the twin must be re-planned",
            )
        name = m.group("name")
        if name not in nodes_a or name not in nodes_b:
            return PendingChange(
                path=path, op="modified", kind=KIND_TOPOLOGY, paths=paths,
                reason=f"node {name!r} added or removed: the twin must be re-planned",
            )
        roles[name] = str(nodes_b[name].get("role") or "")
    if re_imaged:
        named = ", ".join(repr(n) for n in sorted(set(re_imaged)))
        return PendingChange(
            path=path, op="modified", kind=KIND_TOPOLOGY, paths=paths,
            reason=(
                f"node {named}: role changed together with its image"
                if any(n in roles for n in re_imaged)
                else f"node {named}: image changed"
            ) + " -- the twin must be re-planned",
        )
    return PendingChange(
        path=path, op="modified", kind=KIND_ATTRIBUTE, via=["tfc"], paths=paths,
        reason="role re-assignment with the image unchanged: the tfc playout re-asserts it",
        roles=roles,
    )


def classify_manifest(path: str, before: str, after: str) -> PendingChange | None:
    a, b = parse_yaml(before), parse_yaml(after)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return PendingChange(
            path=path, op="modified", kind=KIND_TOPOLOGY, paths=[path],
            reason="manifest does not parse as a mapping",
        )
    paths = yaml_diff(a, b)
    if not paths:
        return None
    if all(any(_under(p, pre) for pre in _MANIFEST_ATTRIBUTE_PREFIXES) for p in paths):
        via = [
            tier for tier in ("tfc", "tap")
            if any(_under(p, f"twin.tiers.{tier}") for p in paths)
        ]
        return PendingChange(
            path=path, op="modified", kind=KIND_ATTRIBUTE, via=via, paths=paths,
            reason="tier wiring changed: the named tiers' playouts re-apply their defaults",
        )
    return PendingChange(
        path=path, op="modified", kind=KIND_TOPOLOGY, paths=paths,
        reason="manifest changed the twin's te tier or identity",
    )


@dataclass
class _Reach:
    """What the playouts read in a document: the tfc default and its
    `roles_from`, the persona ids the default profile assigns, the tap
    default."""

    tfc: set[str]
    personas: set[str]
    tap: str | None

    @classmethod
    def of(cls, files: dict[str, str]) -> _Reach:
        tfc_default = tier_file_of(files, "tfc")
        return cls(
            tfc=profile_reach(files, tfc_default),
            personas=profile_personas(files, tfc_default),
            tap=tier_file_of(files, "tap"),
        )


def classify_file(
    path: str, op: str, *, before: str | None, after: str | None,
    reach_before: _Reach, reach_after: _Reach,
) -> PendingChange:
    """An added / removed / modified file other than te and manifest."""
    top = PurePosixPath(path).parts[0]
    if top == "tfc":
        if path in reach_after.tfc or path in reach_before.tfc:
            return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tfc"], paths=[path],
                                 reason="traffic profile (or its roles_from): the tfc playout re-applies")
        return PendingChange(path=path, op=op, kind=KIND_INERT, paths=[path],
                             reason="not the tfc default nor reached from it: no playout reads it")
    if top == "personas":
        pid = persona_id_of(after) or persona_id_of(before)
        if pid and (pid in reach_after.personas or pid in reach_before.personas):
            return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tfc"], paths=[path],
                                 reason=f"twin-local persona {pid!r} the default profile assigns: "
                                        "the tfc playout re-applies")
        return PendingChange(path=path, op=op, kind=KIND_INERT, paths=[path],
                             reason="persona the default profile does not assign: no playout reads it")
    if top == "tap":
        if path in (reach_after.tap, reach_before.tap):
            return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tap"], paths=[path],
                                 reason="tap plan default: the tap playout re-applies")
        return PendingChange(path=path, op=op, kind=KIND_INERT, paths=[path],
                             reason="not the tap default: no playout reads it")
    if top == "exr" or ("/" not in path and path.lower().endswith((".md", ".txt"))):
        return PendingChange(path=path, op=op, kind=KIND_INERT, paths=[path],
                             reason="nothing on the running twin reads this file; Persist carries it")
    return PendingChange(path=path, op=op, kind=KIND_TOPOLOGY, paths=[path],
                         reason="not a tier file Apply knows how to re-apply")


def classify(
    baseline: dict[str, str], working: dict[str, str], *, te_file: str,
) -> list[PendingChange]:
    """Every file that differs between the two documents, classified. A
    file whose text differs but whose meaning does not is no change."""
    reach_b, reach_w = _Reach.of(baseline), _Reach.of(working)
    out: list[PendingChange] = []
    for path in sorted(set(baseline) | set(working)):
        before, after = baseline.get(path), working.get(path)
        if before == after:
            continue
        if before is None or after is None:
            op = "added" if before is None else "removed"
            if path in (te_file, MANIFEST):
                out.append(PendingChange(
                    path=path, op=op, kind=KIND_TOPOLOGY, paths=[path],
                    reason=f"{path} {op}: the twin must be re-planned",
                ))
            else:
                out.append(classify_file(
                    path, op, before=before, after=after, reach_before=reach_b, reach_after=reach_w,
                ))
            continue
        if path == te_file:
            change = classify_te(path, before, after)
        elif path == MANIFEST:
            change = classify_manifest(path, before, after)
        else:
            change = classify_file(
                path, "modified", before=before, after=after,
                reach_before=reach_b, reach_after=reach_w,
            )
        if change is not None:
            out.append(change)
    return out


def tiers_to_apply(changes: Iterable[PendingChange]) -> list[str]:
    tiers: set[str] = set()
    for c in changes:
        if c.kind == KIND_ATTRIBUTE:
            tiers.update(c.via)
    return [t for t in ("tfc", "tap") if t in tiers]


def role_overrides(changes: Iterable[PendingChange]) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in changes:
        if c.kind == KIND_ATTRIBUTE:
            out.update(c.roles)
    return out
