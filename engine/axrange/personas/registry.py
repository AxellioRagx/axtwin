"""The persona catalog registry (docs/design/07-traffic-personas.md,
docs/WORK_ORDER_persona_library.md §2) — mirrors `axrange.sensors.registry`'s
role (an open, engine-vocabulary catalog `traffic_tier` will import
downward) but is data-file-backed: each `personas/catalog/*.yaml` is one
persona, loaded once at import time into `PERSONAS`.

Reuses `axrange.ir.yaml_util.load_yaml_raw` (LES-025 — the same "open one
YAML file, fail loudly" primitive `axrange.manifest.loader` uses) rather
than hand-rolling a YAML read.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from ruamel.yaml import YAML

from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw
from axrange.personas.schema import Persona, PersonaSchemaError, load_persona

_CATALOG_DIR = Path(__file__).parent / "catalog"


class PersonaRegistryError(Exception):
    """A catalog file failed to load or parse."""


def _yaml_loader() -> YAML:
    return YAML(typ="safe")


def _load_catalog_dir(catalog_dir: Path) -> dict[str, Persona]:
    out: dict[str, Persona] = {}
    if not catalog_dir.is_dir():
        return out
    for path in sorted(catalog_dir.glob("*.yaml")):
        try:
            data = load_yaml_raw(path, loader=_yaml_loader()) or {}
        except YamlLoadError as e:
            raise PersonaRegistryError(str(e)) from e
        try:
            persona = load_persona(data)
        except PersonaSchemaError as e:
            raise PersonaRegistryError(f"{path}: {e}") from e
        if persona.id in out:
            raise PersonaRegistryError(
                f"{path}: duplicate persona id {persona.id!r} "
                f"(already defined by another catalog file)"
            )
        out[persona.id] = persona
    return out


#: Extra persona directories, colon-separated, from `AXRANGE_PERSONAS_DIR`.
#:
#: A twin CAST from observed traffic needs personas derived from that traffic — emitting the
#: ports it was actually seen using, at the rates it was seen using them — and those ship beside
#: the twin config that references them, not in this curated starter catalog. Without a way to
#: load them, a cast twin can only be driven by generic archetypes, which is what made a role's
#: detection signature and its persona's emitted signature disagree in the first place (aximprint
#: `docs/JOURNAL_corpapcn_fidelity.md` GAP-C8).
#:
#: An env var rather than a CLI flag, deliberately: personas are resolved at import time by
#: `traffic_tier`, the validator and the drivers alike, and threading a flag through all three
#: would put the same path in three places that could disagree.
#:
#: Later directories override earlier ones on an id collision — the local, capture-derived
#: persona wins over a same-named starter archetype, which is the useful direction. A collision
#: WITHIN one directory is still an error, since that is a genuine authoring mistake.
_EXTRA_DIRS_ENV = "AXRANGE_PERSONAS_DIR"


def _extra_dirs() -> list[Path]:
    raw = os.environ.get(_EXTRA_DIRS_ENV, "")
    return [Path(p).expanduser() for p in raw.split(":") if p.strip()]


def _load_all() -> dict[str, Persona]:
    out = _load_catalog_dir(_CATALOG_DIR)
    for extra in _extra_dirs():
        out.update(_load_catalog_dir(extra))
    return out


# Loaded once at import time — the starter catalog is curated, static content
# (like `axrange.sensors.SENSOR_TYPES`), not something that changes per-run.
PERSONAS: dict[str, Persona] = _load_all()


def reload_personas() -> dict[str, Persona]:
    """Re-read the catalog plus `AXRANGE_PERSONAS_DIR`, in place.

    Import-time loading is right for a static catalog and wrong for a process that sets the env
    var after import (a test, or a CLI that resolves its own paths first). Mutates `PERSONAS`
    rather than rebinding it, so callers holding a reference to the dict see the update.
    """
    PERSONAS.clear()
    PERSONAS.update(_load_all())
    return PERSONAS


# ---------------------------------------------------------------------------
# Per-context overlay (Unified Console, `docs/reference/ui-consolidation/
# 00-design-ruling.md` §3.2; closes GAP-105 for the API).
#
# The CLI makes a twin's local personas visible by prepending its folder to
# `AXRANGE_PERSONAS_DIR` and `reload_personas()` -- fine for a one-shot
# process, wrong for `axrange serve`, where two requests for two twins would
# race on the one global dict and twin A's ids would leak into twin B's
# picker. An overlay is a ContextVar: it is visible to every `get()` made in
# the same thread/task (the resolver, the op composer, `PersonaBackend`, the
# stop path) and to nobody else, and it never touches `PERSONAS`. A thread
# does NOT inherit it -- a background worker re-enters `overlay(...)` itself.
# ---------------------------------------------------------------------------
_OVERLAY: ContextVar[dict[str, Persona] | None] = ContextVar(
    "axrange_persona_overlay", default=None
)


@contextmanager
def overlay(personas: dict[str, Persona] | None) -> Iterator[None]:
    """Make `personas` win over the process catalog for the duration of the
    block, in this context only. `None`/empty is a no-op overlay."""
    token = _OVERLAY.set(dict(personas) if personas else None)
    try:
        yield
    finally:
        _OVERLAY.reset(token)


def current_overlay() -> dict[str, Persona]:
    """The overlay active in this context (a copy; `{}` when none)."""
    return dict(_OVERLAY.get() or {})


def twin_overlay(personas_dir: str | Path | None) -> dict[str, Persona]:
    """A twin's local personas as an overlay, with the SAME precedence the
    CLI's `_resolve_twin_tier_spec` gives them: the twin's folder sits AHEAD
    of `AXRANGE_PERSONAS_DIR`, so an id an env-var directory also defines
    keeps the env-var body (which is already in `PERSONAS`) and is left out
    of the overlay. Raises `PersonaRegistryError` on a bad file."""
    if personas_dir is None:
        return {}
    d = Path(personas_dir)
    # Re-read only when a file changed (S11): keyed on every YAML's
    # (name, mtime, size) plus the env-var directory list.
    try:
        stamp = tuple(sorted(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d.glob("*.yaml")
        ))
    except OSError:
        stamp = None
    key = (str(d.resolve()), stamp, os.environ.get(_EXTRA_DIRS_ENV, ""))
    hit = _TWIN_OVERLAY_CACHE.get(key) if stamp is not None else None
    if hit is not None:
        return dict(hit)
    mine = _load_catalog_dir(d)
    env_ids: set[str] = set()
    for extra in _extra_dirs():
        env_ids.update(_load_catalog_dir(extra))
    out = {pid: p for pid, p in mine.items() if pid not in env_ids}
    if stamp is not None:
        _TWIN_OVERLAY_CACHE[key] = out
    return dict(out)


_TWIN_OVERLAY_CACHE: dict[tuple, dict[str, Persona]] = {}


def get(persona_id: str) -> Persona | None:
    ov = _OVERLAY.get()
    if ov and persona_id in ov:
        return ov[persona_id]
    return PERSONAS.get(persona_id)


def is_known_id(persona_id: str | None) -> bool:
    return persona_id is not None and get(persona_id) is not None


def all_ids() -> list[str]:
    return sorted(set(PERSONAS) | set(_OVERLAY.get() or {}))


def load_catalog_dir(catalog_dir: str | Path) -> dict[str, Persona]:
    """Load an alternate catalog directory (test fixtures, a future
    operator-supplied override dir) without touching the module-level
    `PERSONAS` — kept separate so tests can exercise the loader against a
    deliberately-bad fixture without mutating global state."""
    return _load_catalog_dir(Path(catalog_dir))
