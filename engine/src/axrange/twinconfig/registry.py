"""Twin-config catalog discovery — reads `twin-configs/<id>/<id>.yaml` (a
CONTENT directory, like `target-environments/`; `docs/CONVENTIONS.md`), not
code. Each config lives in its own subfolder alongside its tap-plans
(`twin-configs/<id>/tap-*.yaml`); the flat `twin-configs/<id>.yaml` layout is
still discovered too (back-compat). Mirrors `axrange.manifest.loader`'s
directory-scan idiom (LES-037): open, parse, wrap into a caller-specific
typed error. Discovery is RECURSIVE (`rglob`); a non-twin-config YAML in the
tree (e.g. a `tap-*.yaml` tap-plan) is skipped by the `twin_config:` filter.

**Search path** (`docs/WORK_ORDER_console_selector_and_stale_deploys.md`
follow-up, Authority: user, 2026-08-27): `catalog_dir` accepts a
colon-separated STRING of multiple directories, not only one — mirroring
`AXRANGE_PERSONAS_DIR`'s colon-separated idiom (`axrange/personas/
registry.py`) so an operator can list the shipped `twin-configs/` catalog
AND a cast/working catalog (e.g. `twin-configs-cast/`) together, instead of
one replacing the other ("a single swappable dir means the shipped
archetypes disappear the moment you point at cast output" — the concrete
complaint this generalizes from). A single directory with no `:` in it
round-trips completely unchanged, so every existing caller (the CLI's
historical `--twin-configs-dir <one-dir>`, every test fixture) is
unaffected — this is purely additive. On an id collision across dirs, the
LATER directory in the search path wins (same direction as
`AXRANGE_PERSONAS_DIR`: a local/cast override shadows a same-named shipped
entry, not the other way around) — order the search path shipped-first,
override-last.

**`twins/<id>/` folders** (`docs/WORK_ORDER_twin_consolidation.md` §2,
Authority: user, 2026-09-25; the minimal Phase 2 loader,
`docs/WORK_ORDER_corp_twin.md` S0): the DEFAULT search path is now
`twin-configs/` THEN `twins/` -- the new root is later, so a migrated twin
shadows its old catalog copy by the rule above, and every old catalog id
keeps resolving. Since the consolidation's Phase 2 every shipped twin lives
in `twins/` (`default_catalog_dir()`); `twin-configs/` stays on the path
only as the legacy compatibility reader until Phase 4 removes it. Inside a root, a `<id>/twin.yaml` manifest's
`tiers.te.default` (normally `te.yaml`) is tried before the old
`<id>/<id>.yaml` name (`axrange.twinconfig.manifest`).
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from ruamel.yaml import YAML

from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw
from axrange.twinconfig.errors import TwinConfigNotFoundError, TwinConfigShapeError
from axrange.twinconfig.schema import TwinConfig, TwinConfigFile

# <project-root>/catalog/twin-configs (spelled once in `axrange.paths`). The
# LEGACY root: every twin it held moved to `twins/<id>/`
# (`docs/WORK_ORDER_twin_consolidation.md` Phase 2); it stays on the default
# search path as the compatibility reader until Phase 4 retires it.
from axrange.paths import LEGACY_TWIN_CONFIGS_DIR as _LEGACY_CATALOG_DIR


def default_catalog_dir() -> Path:
    """The catalog root, `twins/` (was `twin-configs/` until the twin
    consolidation, Phase 3). The default SEARCH PATH is
    `default_search_dirs()`, which keeps the legacy root in front of it."""
    from axrange.twinconfig.manifest import default_twins_dir

    return default_twins_dir()


def legacy_catalog_dir() -> Path:
    """The legacy `twin-configs/` root -- searched, never written."""
    return _LEGACY_CATALOG_DIR


def default_search_dirs() -> list[Path]:
    """`[twin-configs/, twins/]` -- the search path when no `catalog_dir` is
    given (module docstring). `twins/` is later, so it wins an id collision."""
    return [_LEGACY_CATALOG_DIR, default_catalog_dir()]


def _yaml_loader() -> YAML:
    return YAML(typ="safe")


@dataclass
class TwinConfigCatalogEntry:
    id: str
    path: Path
    description: Optional[str]
    # Set when the entry sits in a `twins/<id>/` folder whose `twin.yaml`
    # fails to load: the te file parses, but `--config <id>` would refuse it
    # (`manifest_te_path` fails loud). Listed, flagged -- never shown as
    # healthy, never silently dropped (the tolerant-listing posture of the
    # imprint catalog's `skipped`, `api/dtos.py` `ImprintSkippedDTO`).
    error: Optional[str] = None


CatalogDirArg = Optional[str | Path | Sequence[str | Path]]


def search_dirs(catalog_dir: CatalogDirArg = None) -> list[Path]:
    """Normalize a `catalog_dir` argument into an ordered list of
    directories to search — the one place the colon-separated search-path
    convention (module docstring) is parsed, so every function below shares
    the identical rule.

    - `None`/empty -> `default_search_dirs()` (`twin-configs/` then
      `twins/`; was `[twin-configs/]` alone before the `twins/` root).
    - A single `str`/`Path` with no `:` -> `[that one dir]` (unchanged
      single-dir behavior — what every existing caller passes today).
    - A colon-separated `str` (`"dir1:dir2"`) -> `[dir1, dir2]`.
    - An explicit list/tuple of `str`/`Path` -> used as-is, in order (a
      caller that already has a resolved list, e.g. a future CLI
      `--twin-configs-dir` repeated flag, does not need to re-join and
      re-split a string).
    """
    if not catalog_dir:
        return default_search_dirs()
    if isinstance(catalog_dir, (list, tuple)):
        parts = [str(p).strip() for p in catalog_dir if str(p).strip()]
    else:
        parts = [p.strip() for p in str(catalog_dir).split(":") if p.strip()]
    return [Path(p).expanduser() for p in parts] if parts else default_search_dirs()


def _under_generated(root: Path, path: Path) -> bool:
    """True when `path` sits under a twin folder's `generated/` (build
    output, never catalog content): a `generated` directory whose parent
    holds a `twin.yaml`."""
    rel = path.relative_to(root).parts[:-1]
    for i, part in enumerate(rel):
        if part == "generated" and (root.joinpath(*rel[:i]) / "twin.yaml").is_file():
            return True
    return False


#: The listing's per-file memo: resolved path -> (stat key, `(id, description)`
#: or None for a YAML that is not a twin-config). The catalog listing parsed
#: every `*.yaml` under every twin folder (tap, tfc, personas, history: ~350
#: files, 4 s with ruamel's pure-Python parser) on every call, and the canvas
#: lists it on each open (R3-6, 2026-11-08). Keyed per LES-283 like
#: `manifest._MEMO`: resolved path + (ino, size, mtime_ns, ctime_ns) with the
#: stat taken BEFORE the read, failures never cached, locked. Unbounded on
#: purpose: one small entry per catalog file. Residual, named: a
#: same-nanosecond in-place rewrite keeping all four signals is invisible.
_ENTRY_MEMO: dict[Path, tuple[tuple, Optional[tuple[str, Optional[str]]]]] = {}
_ENTRY_LOCK = threading.Lock()


def _stat_key(p: Path) -> Optional[tuple]:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _catalog_head(path: Path) -> Optional[tuple[str, Optional[str]]]:
    """`(twin_config.id, description)` of one YAML, or None when it is not a
    twin-config. Raises `YamlLoadError` (never cached) when it does not load."""
    key = path.resolve()
    st = _stat_key(path)  # BEFORE the read (LES-283)
    with _ENTRY_LOCK:
        hit = _ENTRY_MEMO.get(key)
    if hit is not None and st is not None and hit[0] == st:
        return hit[1]
    # A file that never spells `twin_config` cannot carry the block: skip the
    # parse (most of a twin folder's YAML is tap / traffic / persona files).
    try:
        if b"twin_config" not in path.read_bytes():
            head: Optional[tuple[str, Optional[str]]] = None
        else:
            head = _parse_head(path)
    except OSError as e:
        raise YamlLoadError(f"{path}: {e}") from e
    if st is not None:
        with _ENTRY_LOCK:
            _ENTRY_MEMO[key] = (st, head)
    return head


def _parse_head(path: Path) -> Optional[tuple[str, Optional[str]]]:
    raw = load_yaml_raw(path, loader=_yaml_loader())
    tc_raw = raw.get("twin_config") if isinstance(raw, dict) else None
    if not isinstance(tc_raw, dict) or not isinstance(tc_raw.get("id"), str):
        return None
    return tc_raw["id"], tc_raw.get("description")


def _list_one_dir(root: Path) -> list[TwinConfigCatalogEntry]:
    """One search-path directory's scan -- `list_twin_configs` below calls
    this once per entry in the search path."""
    from axrange.twinconfig.errors import TwinConfigError
    from axrange.twinconfig.manifest import MANIFEST_FILENAME, load_twin_manifest

    out: list[TwinConfigCatalogEntry] = []
    if not root.is_dir():
        return out
    manifest_errors: dict[Path, Optional[str]] = {}
    # RECURSIVE: each config now lives in `twin-configs/<id>/<id>.yaml`
    # alongside its `tap-*.yaml` tap-plans; the flat `twin-configs/<id>.yaml`
    # layout still matches. A tap-plan (or any non-twin-config YAML) in the
    # tree is dropped by the `twin_config:` shape filter just below.
    for path in sorted(root.rglob("*.yaml")):
        if _under_generated(root, path):
            continue
        try:
            head = _catalog_head(path)
        except YamlLoadError:
            continue
        if head is None:
            continue
        twin_id, description = head
        error: Optional[str] = None
        folder = path.parent
        if (folder / MANIFEST_FILENAME).is_file():
            if folder not in manifest_errors:
                try:
                    load_twin_manifest(folder)
                    manifest_errors[folder] = None
                except TwinConfigError as e:
                    manifest_errors[folder] = str(e)
            error = manifest_errors[folder]
        out.append(
            TwinConfigCatalogEntry(
                id=twin_id, path=path, description=description,
                error=error,
            )
        )
    return out


def list_twin_configs(catalog_dir: CatalogDirArg = None) -> list[TwinConfigCatalogEntry]:
    """Every `*.yaml` across the catalog SEARCH PATH (module docstring) that
    parses as a twin-config (has a top-level `twin_config:` block with an
    `id:`), sorted by id. A file that fails to parse/shape-check is skipped
    here -- `list` is a browsing operation; `load_twin_config`/
    `load_twin_config_raw` are where a caller who actually wants ONE named
    config gets a loud error instead. An entry inside a twin folder whose
    `twin.yaml` is broken is listed WITH its `error` set, not as healthy.

    ACROSS directories the listing follows the resolver's later-wins rule:
    an id a later root also declares is shown once, from the later root --
    the one `--config <id>` would actually load (a migrated `twins/<id>/`
    shadows its old `twin-configs/<id>/`). WITHIN one directory two files
    declaring the same id both appear, as they always have.
    """
    by_root = [_list_one_dir(root) for root in search_dirs(catalog_dir)]
    out: list[TwinConfigCatalogEntry] = []
    for i, entries in enumerate(by_root):
        later_ids = {e.id for later in by_root[i + 1:] for e in later}
        out.extend(e for e in entries if e.id not in later_ids)
    return sorted(out, key=lambda e: e.id)


def load_twin_config_raw(
    config_id: str, *, catalog_dir: CatalogDirArg = None,
    warnings: Optional[list[str]] = None,
) -> tuple[dict, Path]:
    """Find `<config_id>.yaml` across the catalog SEARCH PATH (module
    docstring) — filename match first, falling back to a scan-by-declared-id
    in case the file was renamed — and return its raw parsed dict + the path
    it came from. Raises `TwinConfigNotFoundError` if nothing matches.

    Both passes search the directories in REVERSE order (the LAST entry in
    the search path first), so a later/override directory's entry wins an
    id collision against an earlier/shipped one — see the module docstring.
    `warnings`: see `resolve_twin_config_path`.
    """
    path = resolve_twin_config_path(config_id, catalog_dir=catalog_dir, warnings=warnings)
    try:
        raw = load_yaml_raw(path, loader=_yaml_loader())
    except YamlLoadError as e:
        raise TwinConfigShapeError(str(e)) from e
    return raw, path


def _fast_path_hit(root: Path, config_id: str) -> Optional[Path]:
    """The stat-only lookup of `config_id` in ONE root: a twin folder's
    manifest te file, then `<id>/te.yaml`, `<id>/<id>.yaml`, `<id>.yaml`.
    A PRESENT manifest is parsed (memoised) and a broken one raises."""
    from axrange.twinconfig.manifest import manifest_te_path

    te_from_manifest = manifest_te_path(root / config_id)
    if te_from_manifest is not None:
        return te_from_manifest
    for candidate in (
        root / config_id / "te.yaml",
        root / config_id / f"{config_id}.yaml",
        root / f"{config_id}.yaml",
    ):
        if candidate.is_file():
            return candidate
    return None


def _shadowed_candidates(root: Path, config_id: str) -> list[Path]:
    """Files in `root` a fast-path lookup of `config_id` could have picked --
    stat only, never a manifest parse (an earlier root's broken manifest must
    not fail a lookup the later root already answered)."""
    return [
        c for c in (
            root / config_id / "twin.yaml",
            root / config_id / "te.yaml",
            root / config_id / f"{config_id}.yaml",
            root / f"{config_id}.yaml",
        ) if c.is_file()
    ]


def resolve_twin_config_path(
    config_id: str, *, catalog_dir: CatalogDirArg = None,
    warnings: Optional[list[str]] = None,
) -> Path:
    """The FILE `load_twin_config_raw` reads for `config_id` -- ONE resolution
    rule for the loader, the expander behind it and `twin_config_source_dir`.

    The fast path -- a twin folder's `<id>/twin.yaml` (its `tiers.te.default`)
    first, then `<id>/te.yaml`, then the subfolder layout `<id>/<id>.yaml`,
    then the flat back-compat `<id>.yaml`, later search-path directories
    first -- is a stat, not a parse, except that a PRESENT manifest is parsed
    and validated (a broken one raises rather than being stepped around).
    Only the renamed-file fallback, a scan of every `*.yaml` for its declared
    id, parses, as it always did. Raises `TwinConfigNotFoundError` when
    nothing matches. (GAP-082 review round 2, SF1: the source-directory
    lookup used to parse the whole file and throw the parse away.)

    Later-root-wins is kept, but never silent: when an EARLIER root also has
    `config_id` on the fast path, one line naming both goes to `warnings`
    (the out-list `materialize_twin_config` already threads to the CLI/API),
    or to stderr when the caller passed none."""
    roots = search_dirs(catalog_dir)
    for i in range(len(roots) - 1, -1, -1):
        hit = _fast_path_hit(roots[i], config_id)
        if hit is None:
            continue
        shadowed = [c for r in roots[:i] for c in _shadowed_candidates(r, config_id)]
        if shadowed:
            _warn(
                warnings,
                f"twin-config {config_id!r}: using {hit}; it shadows "
                f"{', '.join(str(c) for c in shadowed)} (later search-path root wins)",
            )
        return hit
    for root in reversed(roots):
        for entry in _list_one_dir(root):
            if entry.id == config_id:
                return entry.path
    where = ":".join(str(r) for r in roots)
    raise TwinConfigNotFoundError(
        f"no twin-config {config_id!r} in {where} (looked for "
        f"'{config_id}.yaml' and scanned every *.yaml's declared id)"
    )


def _warn(warnings: Optional[list[str]], line: str) -> None:
    if warnings is not None:
        if line not in warnings:  # one --config run resolves the id more than once
            warnings.append(line)
    else:
        sys.stderr.write(f"[warn] {line}\n")


def twin_config_source_dir(config_id: str, *, catalog_dir: CatalogDirArg = None) -> Path:
    """The directory a twin config's RELATIVE paths resolve against -- an
    `install: [{script: scripts/...}]` is read by `pass1_emit._read_script`
    relative to the planner's `scenario_dir`.

    It is the REAL directory of the exact `<id>.yaml` `load_twin_config_raw`
    (and so `materialize_twin_config`) reads -- found by the SAME rule,
    `resolve_twin_config_path`, WITHOUT parsing it (GAP-082 review round 2,
    SF1) -- with SYMLINKS RESOLVED: a cast bundle
    may be a directory of symlinks (`twin-configs-cast/pit-395a61f04e-
    1780405200/<id>.yaml -> ../corpapcn-disco/twin_config.yaml`) whose
    `scripts/` live beside the link's TARGET. Never the tempdir a
    materialized expansion lands in -- which is where every relative script
    resolved before this helper existed, so no twin config with a script
    install could plan through the API (GAP-082 review, M2).

    ONE helper for the CLI's `--config` path and both API resolvers
    (`service._resolve_config_scenario_path`/`_resolve_imprint_scenario_path`):
    the CLI had fixed this locally with its own catalog scan, which could
    even pick a DIFFERENT file than the loader does on an id collision."""
    # A throwaway `warnings`: this lookup always follows the load that
    # already reported any shadowing -- saying it twice is noise.
    return resolve_twin_config_path(
        config_id, catalog_dir=catalog_dir, warnings=[],
    ).resolve().parent


def load_twin_config(
    config_id: str, *, catalog_dir: CatalogDirArg = None,
) -> TwinConfig:
    """Parse `<config_id>.yaml`'s `twin_config:` block into a typed,
    UNSUBSTITUTED `TwinConfig` (params still carry their declared defaults,
    not any `--param` override) -- used by `axrange twin-config show <id>`
    and by tests inspecting the declared shape. `axrange.twinconfig.expand`
    is where param substitution + IR-building happen."""
    raw, _path = load_twin_config_raw(config_id, catalog_dir=catalog_dir)
    if not isinstance(raw, dict) or "twin_config" not in raw:
        raise TwinConfigShapeError(f"{config_id}: missing top-level 'twin_config:' key")
    try:
        wrapped = TwinConfigFile.model_validate(raw)
    except Exception as e:  # noqa: BLE001 -- reduced to one typed error, like the IR parser
        raise TwinConfigShapeError(f"{config_id}: {e}") from e
    return wrapped.twin_config
