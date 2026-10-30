"""The edit model's storage and classification (`api/Spec.md`, "The edit
model: baseline + working copy"; seams A2 and A3).

Three things live here, none of which touches the engine:

* **The twin-config document** -- a bundle (`catalog/twins/<id>/`) as the
  editable object: every text file in the folder keyed by its bundle-relative
  path (`te.yaml`, `twin.yaml`, `tfc/default.yaml`, `tap/default.yaml`,
  `personas/*.yaml`, ...). `read_document` / `write_document` are the ONE
  reader and writer; `generated/` (the cast's machine-written evidence,
  `twinconfig.bundle`), `.history` and dot-files are never part of it, so a
  Persist cannot clobber a cast's provenance and a Save-As never copies it.

* **The working copy store** -- one SQLite file BESIDE the planner state DB
  (`<state>.edits.db`, `working_copy_db_path`), one row per deploy. Beside,
  not inside: `planner/state.py` gates every open on `SCHEMA_VERSION` and a
  complete object set, so a new table there is a schema bump that refuses
  every existing state DB ("re-init required"), and `api/Spec.md` A7 already
  rules that non-planner records do not go into `state.db`, "whose schema is
  its own". The sidecar follows the deploy record's store (a target with
  several stores keeps each twin's copy next to its own record), is server-
  side so a console on any host reads the same copy, and a read never
  creates it.

* **The classifier** -- which tier file a pending change touches decides
  what Apply may do (the spec's table): `tfc/`, `personas/`, `tap/` are
  attribute changes the tier's own playout re-applies; `te.yaml` is topology
  (`requires-replan`) unless the only difference is a node's `role` with its
  `image` unchanged; `twin.yaml` is attribute when it only re-wires the
  tfc/tap/exr tier files, topology when it touches the te tier or the twin's
  identity; `exr/` and prose are inert (nothing on the running twin reads
  them; Persist carries them). Anything unrecognised fails closed as
  topology.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

#: Folders inside a bundle that are never part of the editable document.
SKIP_DIRS = frozenset({"generated", ".history", "__pycache__"})

MANIFEST = "twin.yaml"

KIND_ATTRIBUTE = "attribute"
KIND_TOPOLOGY = "topology"
KIND_INERT = "inert"

#: The refusal code Apply answers when a pending change is topology.
REQUIRES_REPLAN = "requires-replan"


class DocumentError(ValueError):
    """A document that cannot be read or written as a bundle (a bad relative
    path, a te file that is not this twin, a missing te file). The message
    is the operator-facing reason."""


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def check_relpath(rel: str) -> str:
    """A bundle-relative POSIX path the document may carry. Refuses absolute
    paths, `..`, backslashes, empty parts, dot-files and the machine-written
    folders. Returns the normalized path."""
    if not isinstance(rel, str) or not rel:
        raise DocumentError("a document path must be a non-empty string")
    if "\\" in rel or rel.startswith("/"):
        raise DocumentError(f"document path {rel!r} must be relative, with '/' separators")
    p = PurePosixPath(rel)
    parts = p.parts
    if not parts or any(not part or part in (".", "..") for part in parts):
        raise DocumentError(f"document path {rel!r} may not contain '.' or '..' parts")
    if any(part.startswith(".") for part in parts):
        raise DocumentError(f"document path {rel!r}: dot-files are not part of a document")
    if parts[0] in SKIP_DIRS:
        raise DocumentError(
            f"document path {rel!r}: {parts[0]!r} is machine-written and not editable"
        )
    return str(p)


def read_document(bundle_dir: str | Path) -> tuple[dict[str, str], list[str]]:
    """Every text file under `bundle_dir` (recursively, sorted), keyed by its
    relative POSIX path, plus the relative paths of files that were SKIPPED
    because they are not UTF-8 text (a pcap under `scripts/`, say). Skipped
    files are not part of the document: a write leaves them in place."""
    d = Path(bundle_dir)
    files: dict[str, str] = {}
    skipped: list[str] = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(d)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        try:
            files[rel.as_posix()] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(rel.as_posix())
    return files, skipped


def read_single_file_document(path: str | Path) -> dict[str, str]:
    """The legacy flat layout (`<root>/<id>.yaml`, no folder): the document
    is that one file."""
    p = Path(path)
    return {p.name: p.read_text(encoding="utf-8")}


def write_document(
    bundle_dir: str | Path, files: dict[str, str], *, replace: bool,
) -> None:
    """Write `files` under `bundle_dir` (created as needed). Each file lands
    through a temp sibling + `os.replace`, so a reader never sees a half-
    written file. With `replace`, every document file already in the folder
    that `files` no longer names is removed and emptied folders pruned --
    the folder then IS the document (skipped binaries and `generated/` stay).
    """
    d = Path(bundle_dir)
    clean = {check_relpath(rel): text for rel, text in files.items()}
    for rel, text in clean.items():
        if not isinstance(text, str):
            raise DocumentError(f"document file {rel!r} must be text")
    d.mkdir(parents=True, exist_ok=True)
    if replace:
        existing, _skipped = read_document(d)
        for rel in existing:
            if rel not in clean:
                (d / rel).unlink(missing_ok=True)
    for rel, text in clean.items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    if replace:
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


def te_file_of(twin_id: str, files: dict[str, str]) -> str:
    """Which document file is the twin config: the manifest's
    `tiers.te.default`, else `te.yaml`, else `<id>.yaml`. Raises
    `DocumentError` when the document has none."""
    manifest = files.get(MANIFEST)
    if manifest is not None:
        raw = parse_yaml(manifest)
        twin = raw.get("twin") if isinstance(raw, dict) else None
        tiers = twin.get("tiers") if isinstance(twin, dict) else None
        te = tiers.get("te") if isinstance(tiers, dict) else None
        default = te.get("default") if isinstance(te, dict) else None
        if isinstance(default, str) and default:
            return check_relpath(default)
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
    document has it. None when the tier has no default."""
    manifest = files.get(MANIFEST)
    if manifest is not None:
        raw = parse_yaml(manifest)
        twin = raw.get("twin") if isinstance(raw, dict) else None
        tiers = twin.get("tiers") if isinstance(twin, dict) else None
        t = tiers.get(tier) if isinstance(tiers, dict) else None
        default = t.get("default") if isinstance(t, dict) else None
        if isinstance(default, str) and default:
            rel = check_relpath(default)
            return rel if rel in files else None
        return None
    cand = f"{tier}/default.yaml"
    return cand if cand in files else None


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
    applied_json  TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
"""


@dataclass
class WorkingCopy:
    deploy_id: str
    twin_id: str
    files: dict[str, str]
    #: What Apply pushed onto the running twin from this copy, or None:
    #: `{"at": <epoch>, "tiers": ["tfc", "tap"], "roles": {host: role}}`.
    applied: dict[str, Any] | None
    created_at: float
    updated_at: float


class WorkingCopyStore:
    """The per-deploy working copies of one state store's deploys. A READ of
    a store that does not exist answers "no copy" and creates nothing; the
    first PUT creates the file (mode 0600, like the discovery DB)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, state_db_path: str | Path) -> WorkingCopyStore:
        return cls(working_copy_db_path(state_db_path))

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.is_file():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        conn = sqlite3.connect(str(self.path), isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_EDITS_SCHEMA)
        if fresh:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return conn

    def get(self, deploy_id: str) -> WorkingCopy | None:
        conn = self._connect(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT deploy_id, twin_id, document_json, applied_json, created_at, "
                "updated_at FROM working_copies WHERE deploy_id = ?", (deploy_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row(row) if row else None

    def for_twin(self, twin_id: str) -> list[WorkingCopy]:
        conn = self._connect(create=False)
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT deploy_id, twin_id, document_json, applied_json, created_at, "
                "updated_at FROM working_copies WHERE twin_id = ? ORDER BY deploy_id",
                (twin_id,),
            ).fetchall()
        finally:
            conn.close()
        return [_row(r) for r in rows]

    def put(self, deploy_id: str, twin_id: str, files: dict[str, str]) -> WorkingCopy:
        """Replace the copy's document; what was applied from the previous
        document stays recorded (Reset still has to revert it)."""
        now = time.time()
        conn = self._connect(create=True)
        assert conn is not None
        try:
            conn.execute(
                "INSERT INTO working_copies (deploy_id, twin_id, document_json, "
                "applied_json, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?) "
                "ON CONFLICT(deploy_id) DO UPDATE SET twin_id = excluded.twin_id, "
                "document_json = excluded.document_json, updated_at = excluded.updated_at",
                (deploy_id, twin_id, json.dumps(files, sort_keys=True), now, now),
            )
        finally:
            conn.close()
        got = self.get(deploy_id)
        assert got is not None
        return got

    def mark_applied(self, deploy_id: str, applied: dict[str, Any] | None) -> None:
        conn = self._connect(create=True)
        assert conn is not None
        try:
            conn.execute(
                "UPDATE working_copies SET applied_json = ?, updated_at = ? WHERE deploy_id = ?",
                (json.dumps(applied, sort_keys=True) if applied is not None else None,
                 time.time(), deploy_id),
            )
        finally:
            conn.close()

    def delete(self, deploy_id: str) -> bool:
        conn = self._connect(create=False)
        if conn is None:
            return False
        try:
            cur = conn.execute("DELETE FROM working_copies WHERE deploy_id = ?", (deploy_id,))
            return cur.rowcount > 0
        finally:
            conn.close()


def _row(r: tuple[Any, ...]) -> WorkingCopy:
    return WorkingCopy(
        deploy_id=r[0], twin_id=r[1], files=json.loads(r[2]),
        applied=json.loads(r[3]) if r[3] else None,
        created_at=float(r[4]), updated_at=float(r[5]),
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
            if [x["name"] for x in a] != [x["name"] for x in b] and not any(
                p.startswith(f"{path}[") and p.count("[") == path.count("[") + 1
                and p.endswith("]") for p in out
            ):
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


def classify_te(path: str, before: str, after: str) -> PendingChange:
    """A modified te file: attribute when every difference is a node's
    `role` whose `image` is unchanged (the spec's `role` row), topology
    otherwise -- including a file that no longer parses (fail closed)."""
    a, b = parse_yaml(before), parse_yaml(after)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return PendingChange(
            path=path, op="modified", kind=KIND_TOPOLOGY, paths=[path],
            reason="twin config does not parse as a mapping",
        )
    paths = yaml_diff(a, b)
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


def classify_manifest(path: str, before: str, after: str) -> PendingChange:
    a, b = parse_yaml(before), parse_yaml(after)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return PendingChange(
            path=path, op="modified", kind=KIND_TOPOLOGY, paths=[path],
            reason="manifest does not parse as a mapping",
        )
    paths = yaml_diff(a, b)
    if all(p.startswith(_MANIFEST_ATTRIBUTE_PREFIXES) for p in paths):
        via = sorted({
            tier for tier in ("tfc", "tap") if any(p.startswith(f"twin.tiers.{tier}") for p in paths)
        })
        return PendingChange(
            path=path, op="modified", kind=KIND_ATTRIBUTE, via=via, paths=paths,
            reason="tier wiring changed: the named tiers' playouts re-apply their defaults",
        )
    return PendingChange(
        path=path, op="modified", kind=KIND_TOPOLOGY, paths=paths,
        reason="manifest changed the twin's te tier or identity",
    )


def classify_file(path: str, op: str) -> PendingChange:
    """An added / removed / modified tier file other than te and manifest."""
    top = PurePosixPath(path).parts[0]
    if top == "tfc":
        return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tfc"], paths=[path],
                             reason="traffic profile: the tfc playout re-applies")
    if top == "personas":
        return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tfc"], paths=[path],
                             reason="twin-local persona: the tfc playout re-applies")
    if top == "tap":
        return PendingChange(path=path, op=op, kind=KIND_ATTRIBUTE, via=["tap"], paths=[path],
                             reason="tap plan: the tap playout re-applies")
    if top == "exr" or path.lower().endswith((".md", ".txt")):
        return PendingChange(path=path, op=op, kind=KIND_INERT, paths=[path],
                             reason="nothing on the running twin reads this file; Persist carries it")
    return PendingChange(path=path, op=op, kind=KIND_TOPOLOGY, paths=[path],
                         reason="not a tier file Apply knows how to re-apply")


def classify(
    baseline: dict[str, str], working: dict[str, str], *, te_file: str,
) -> list[PendingChange]:
    """Every file that differs between the two documents, classified."""
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
                out.append(classify_file(path, op))
            continue
        if path == te_file:
            out.append(classify_te(path, before, after))
        elif path == MANIFEST:
            out.append(classify_manifest(path, before, after))
        else:
            out.append(classify_file(path, "modified"))
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
