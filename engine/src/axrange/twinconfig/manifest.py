"""The `twins/<id>/twin.yaml` root manifest -- one twin, one folder
(`docs/WORK_ORDER_twin_consolidation.md` §2, Authority: user, 2026-09-25;
the minimal Phase 2 loader, `docs/WORK_ORDER_corp_twin.md` S0).

A twin folder holds every tier's default file and its variants beside the
twin's own `personas/`, `scripts/` and `generated/`:

    twins/<id>/
      twin.yaml        this manifest
      te.yaml          the twin_config
      tap/<v>.yaml     tfc/<v>.yaml     exr/<name>.yaml     (o2.yaml)
      personas/  scripts/  generated/

The manifest is WIRING, not content: it names which file is each tier's
default and which are its variants. It never restates what a tier file
already says -- in particular `containment:` is a CLAIM checked against the
`te` file's `twin_config.egress:` block, not a second copy of the phase
(the consolidation work order's D3; `planner/egress.py`'s "resolved, not
re-typed" rule). `contained` means "te declares an egress posture";
`uncontained` means "te declares none, on purpose". The live phase is read
back from the deployed router by `te info`, never from here.

Single tier files stay deployable standalone -- nothing here is mandatory
for `axrange tap deploy twins/<id>/tap/x.yaml`. What a manifest adds is a
default wiring (`--twin <id> [--variant x]`) and two folder-derived
defaults the CLI applies to a tier file found inside a twin folder: the
twin's `personas/` joins the persona search path, and a file that declares
no `target:` targets the twin id.

One consequence to know: because `--config <id>` resolves through this
manifest when one is present, a BROKEN `twin.yaml` also blocks
`te ... --config <id>` for that twin (fail loud -- `manifest_te_path`), and
the tolerant `twin-config list` flags the entry rather than hiding it. The
tier files themselves stay deployable by path.

Boundary: content-side, like the rest of `axrange.twinconfig` -- parses
YAML and checks paths; imports nothing from the planner or adapter.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ConfigDict
from ruamel.yaml import YAML

from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw
from axrange.twinconfig.errors import TwinConfigNotFoundError, TwinManifestError
from axrange.twinconfig.schema import TCBase

MANIFEST_FILENAME = "twin.yaml"

#: The five tiers of the twin model (`docs/design/02-twin-model.md`), in the
#: order a whole-twin bring-up would run them (`exr` is never auto-run).
TIERS: tuple[str, ...] = ("te", "o2", "tap", "tfc", "exr")

#: `containment:` vocabulary -- a claim about the te file, checked on load.
CONTAINMENT_VALUES: tuple[str, ...] = ("contained", "uncontained")

# <project-root>/catalog/twins -- spelled once in `axrange.paths`.
from axrange.paths import TWINS_DIR as _DEFAULT_TWINS_DIR


def default_twins_dir() -> Path:
    return _DEFAULT_TWINS_DIR


# Every manifest model is FROZEN and holds tuples, not lists: a loaded twin
# is memoised (`load_twin_manifest`) and handed to several callers, so it
# must be immutable all the way down -- attribute assignment AND container
# mutation fail loudly (LES-284). Nothing writes into a manifest after parse.
class _Frozen(TCBase):
    model_config = ConfigDict(frozen=True)


class TwinTierFiles(_Frozen):
    """One tier's files, as paths RELATIVE to the twin folder."""

    default: Optional[str] = None
    variants: tuple[str, ...] = ()

    def all_files(self) -> list[str]:
        return ([self.default] if self.default else []) + list(self.variants)


class TwinManifestTiers(_Frozen):
    te: TwinTierFiles
    o2: Optional[TwinTierFiles] = None
    tap: Optional[TwinTierFiles] = None
    tfc: Optional[TwinTierFiles] = None
    exr: Optional[TwinTierFiles] = None


class TwinManifest(_Frozen):
    id: str
    description: Optional[str] = None
    cluster: Optional[str] = None  # the adapter.conf target this twin is meant for
    containment: str
    # The tap points the capture a cast twin came from was taken at
    # (`<appliance>:<streams>`, the PIT's `ir_pit.vantage` / `vantages`),
    # copied in at cast (docs/design/topology-layers.md delta D9) so a twin
    # states what its capture could and could not have seen without the
    # store. Absent on a hand-authored twin.
    vantage: tuple[str, ...] = ()
    tiers: TwinManifestTiers


class TwinManifestFile(_Frozen):
    twin: TwinManifest


def _yaml() -> YAML:
    return YAML(typ="safe")


@dataclass(frozen=True)
class LoadedTwin:
    """A validated manifest plus the folder it came from. Frozen (and its
    manifest models frozen, with tuple fields): a loaded twin is memoised and
    handed to several callers, none of which may mutate it (LES-284)."""

    dir: Path
    manifest: TwinManifest

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def te_path(self) -> Path:
        # `te.default` is required by `_check`, so this is never None here.
        return self.dir / str(self.manifest.tiers.te.default)

    @property
    def personas_dir(self) -> Optional[Path]:
        p = self.dir / "personas"
        return p if p.is_dir() else None

    def tier(self, tier: str) -> Optional[TwinTierFiles]:
        if tier not in TIERS:
            raise TwinManifestError(f"unknown tier {tier!r} (one of {', '.join(TIERS)})")
        return getattr(self.manifest.tiers, tier)

    def tier_path(self, tier: str, variant: Optional[str] = None) -> Path:
        """The file for `tier`: its `default`, or the named `variant`.

        A variant is named by its file STEM (`reshard` -> `tap/reshard.yaml`)
        or by its relative path; the default is selectable by name too, so a
        script can always pass `--variant`. Only files the manifest DECLARES
        resolve -- an undeclared file in the folder is not a variant."""
        files = self.tier(tier)
        if files is None:
            raise TwinManifestError(f"twin {self.id!r} declares no {tier!r} tier")
        if variant is None:
            if not files.default:
                raise TwinManifestError(
                    f"twin {self.id!r}: the {tier!r} tier has no default "
                    f"(variants: {', '.join(files.variants) or 'none'}) -- pass --variant"
                )
            return self.dir / files.default
        for rel in files.all_files():
            if variant in (rel, Path(rel).stem):
                return self.dir / rel
        known = ", ".join(Path(r).stem for r in files.all_files()) or "none"
        raise TwinManifestError(
            f"twin {self.id!r}: no {tier!r} variant {variant!r} (declared: {known})"
        )


def _check_rel(twin_dir: Path, twin_id: str, tier: str, rel: str) -> None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise TwinManifestError(
            f"twin {twin_id!r}: {tier} file {rel!r} must be a path inside the twin "
            "folder (relative, no '..') -- a twin never reaches into another twin's folder"
        )
    if p.suffix not in (".yaml", ".yml"):
        raise TwinManifestError(f"twin {twin_id!r}: {tier} file {rel!r} is not a .yaml file")
    if not (twin_dir / p).is_file():
        raise TwinManifestError(f"twin {twin_id!r}: {tier} file {rel!r} does not exist")
    # A symlink (the file, or a directory on its way) may not smuggle the path
    # back out of the folder -- the lexical check above cannot see that.
    real_dir = twin_dir.resolve()
    if not (twin_dir / p).resolve().is_relative_to(real_dir):
        raise TwinManifestError(
            f"twin {twin_id!r}: {tier} file {rel!r} resolves outside the twin folder "
            "(a symlink) -- a twin never reaches into another twin's folder"
        )


def _check(twin_dir: Path, m: TwinManifest) -> None:
    if m.id != twin_dir.name:
        raise TwinManifestError(
            f"{twin_dir / MANIFEST_FILENAME}: twin.id {m.id!r} must equal the folder "
            f"name {twin_dir.name!r} (deploy names and state-DB history key off the id)"
        )
    if m.containment not in CONTAINMENT_VALUES:
        raise TwinManifestError(
            f"twin {m.id!r}: containment {m.containment!r} is not one of "
            f"{', '.join(CONTAINMENT_VALUES)}"
        )
    if not m.tiers.te.default:
        raise TwinManifestError(f"twin {m.id!r}: tiers.te.default is required")
    for tier in TIERS:
        files = getattr(m.tiers, tier)
        if files is None:
            continue
        seen: set[str] = set()
        for rel in files.all_files():
            if rel in seen:
                raise TwinManifestError(f"twin {m.id!r}: {tier} file {rel!r} is listed twice")
            seen.add(rel)
            _check_rel(twin_dir, m.id, tier, rel)

    # The te file must BE this twin, and must back the containment claim.
    te_path = twin_dir / m.tiers.te.default
    try:
        raw = load_yaml_raw(te_path, loader=_yaml())
    except YamlLoadError as e:
        raise TwinManifestError(str(e)) from e
    tc = raw.get("twin_config") if isinstance(raw, dict) else None
    if not isinstance(tc, dict):
        raise TwinManifestError(f"twin {m.id!r}: {te_path} has no top-level 'twin_config:'")
    if tc.get("id") != m.id:
        raise TwinManifestError(
            f"twin {m.id!r}: {te_path.name} declares twin_config.id {tc.get('id')!r}; "
            "the te file of a twin folder must carry the twin's id"
        )
    declares_egress = tc.get("egress") is not None
    if m.containment == "contained" and not declares_egress:
        raise TwinManifestError(
            f"twin {m.id!r}: containment 'contained' but {te_path.name} declares no "
            "twin_config.egress: -- the posture is enforced ON THE ROUTER from that "
            "block (GAP-101); a manifest claim alone contains nothing"
        )
    if m.containment == "uncontained" and declares_egress:
        raise TwinManifestError(
            f"twin {m.id!r}: containment 'uncontained' but {te_path.name} declares "
            "twin_config.egress: -- make the manifest say 'contained'"
        )


#: `load_twin_manifest`'s memo: resolved twin.yaml path -> (twin.yaml stat
#: key, te path, te stat key, LoadedTwin). One `--config` run resolves the
#: same twin several times (the expander's read, `twin_config_source_dir`,
#: the tier-spec resolver) and each load re-parses te.yaml for the
#: containment check -- the memo makes the repeats a stat (GAP-082 SF1).
#: Keyed per LES-283: resolved path + (ino, size, mtime_ns, ctime_ns) of BOTH
#: files, stat taken BEFORE the read, failures never cached, bounded, locked.
#: Residuals, named: a same-nanosecond in-place rewrite keeping all four
#: signals is invisible, and a TIER file deleted after a cached load is not
#: re-checked here (its own reader fails loudly when it opens it).
_MEMO: dict[Path, tuple[tuple, Path, tuple, "LoadedTwin"]] = {}
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 64


def _stat_key(p: Path) -> Optional[tuple]:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def load_twin_manifest(twin_dir: str | Path) -> LoadedTwin:
    """Parse and validate `<twin_dir>/twin.yaml`. Raises `TwinManifestError`
    (a `TwinConfigShapeError`) on any shape or consistency problem, and
    `TwinConfigNotFoundError` when the folder has no manifest.

    Memoised on (twin.yaml, te file) identity + change signals (`_MEMO`);
    the value is immutable, so a hit hands out the shared instance."""
    d = Path(twin_dir).expanduser()
    path = d / MANIFEST_FILENAME
    if not path.is_file():
        raise TwinConfigNotFoundError(f"no {MANIFEST_FILENAME} in {d}")
    key = path.resolve()
    m_stat = _stat_key(path)  # BEFORE the read (LES-283)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
    if hit is not None and hit[0] == m_stat and hit[3].dir == d:
        if _stat_key(hit[1]) == hit[2]:
            return hit[3]
    try:
        raw = load_yaml_raw(path, loader=_yaml())
    except YamlLoadError as e:
        raise TwinManifestError(str(e)) from e
    if not isinstance(raw, dict) or "twin" not in raw:
        raise TwinManifestError(f"{path}: missing top-level 'twin:' key")
    try:
        wrapped = TwinManifestFile.model_validate(raw)
    except Exception as e:  # noqa: BLE001 -- reduced to one typed error, like registry.py
        raise TwinManifestError(f"{path}: {e}") from e
    te_path = d / str(wrapped.twin.tiers.te.default or "")
    te_stat = _stat_key(te_path)  # BEFORE `_check` reads it
    _check(d, wrapped.twin)
    loaded = LoadedTwin(dir=d, manifest=wrapped.twin)
    with _MEMO_LOCK:
        if len(_MEMO) >= _MEMO_MAX and key not in _MEMO:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[key] = (m_stat, te_path, te_stat, loaded)
    return loaded


def _valid_twin_id(twin_id: str) -> bool:
    return (
        bool(twin_id) and "/" not in twin_id and "\\" not in twin_id
        and twin_id not in (".", "..")
    )


def find_twin_dir(
    twin_id: str, roots: Optional[list[Path]] = None, *,
    warnings: Optional[list[str]] = None,
) -> Path:
    """The folder `<root>/<twin_id>/` holding a `twin.yaml`, searching `roots`
    LAST-first (the catalog search path's later-wins rule, `registry.py`).
    Default roots: `[default_twins_dir()]`. An id is a folder NAME -- one
    with a path separator or `..` is refused, never joined. A shadowed
    earlier copy is reported to `warnings`, or stderr when none is passed."""
    if not _valid_twin_id(twin_id):
        raise TwinManifestError(
            f"twin id {twin_id!r} is not a folder name (no '/', no '..')"
        )
    search = [Path(r).expanduser() for r in (roots or [_DEFAULT_TWINS_DIR])]
    for i in range(len(search) - 1, -1, -1):
        cand = search[i] / twin_id
        if (cand / MANIFEST_FILENAME).is_file():
            shadowed = [
                str(r / twin_id) for r in search[:i] if (r / twin_id / MANIFEST_FILENAME).is_file()
            ]
            if shadowed:
                # Later-root-wins, but never silently (the registry's rule).
                line = (f"twin {twin_id!r}: using {cand}; it shadows "
                        f"{', '.join(shadowed)} (later root wins)")
                if warnings is not None:
                    warnings.append(line)
                else:
                    sys.stderr.write(f"[warn] {line}\n")
            return cand
    where = ":".join(str(r) for r in search)
    raise TwinConfigNotFoundError(f"no twin {twin_id!r} ({twin_id}/{MANIFEST_FILENAME}) in {where}")


def twin_dir_for_file(path: str | Path) -> Optional[Path]:
    """The twin folder a tier file lives in (`twins/<id>/tap/x.yaml` ->
    `twins/<id>`), or None. Looks at the file's own directory and its parent
    only -- a tier file is either at the twin root (`te.yaml`) or one level
    down (`tap/`, `tfc/`, `exr/`). Symlinks are NOT resolved: the folder the
    operator named is the twin they meant."""
    p = Path(path).expanduser().absolute()
    for cand in (p.parent, p.parent.parent):
        if (cand / MANIFEST_FILENAME).is_file():
            return cand
    return None


def manifest_te_path(twin_dir: Path) -> Optional[Path]:
    """The te file a twin folder's manifest names, or None when the folder
    has no manifest. Used by `registry.resolve_twin_config_path`'s fast path;
    a PRESENT but broken manifest raises (fail loud -- silently falling back
    to a sibling `te.yaml` would deploy a twin its own manifest rejects)."""
    if not (twin_dir / MANIFEST_FILENAME).is_file():
        return None
    return load_twin_manifest(twin_dir).te_path
