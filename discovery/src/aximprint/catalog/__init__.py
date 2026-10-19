"""Catalog content: data files, and the loaders that validate them.

A **shared, bottom-of-the-stack package** — it imports nothing from `aximprint` and both
`learners` (which infers a role from evidence) and `synth` (which needs that role's image and
sizing) import down into it. Same shape as `provenance.py`, and for the same reason: the
alternative was `learners` importing `synth` or vice versa, which is an upward import either
way (CONVENTIONS §3).

The split this package exists to hold: `roles.yaml` is **content** — axRange image ids, CPU/RAM
sizings, package names — that a user should be able to retune without touching Python, and that
names another project's vocabulary. `roles.py` is the loader, and it is code, because a data
file that is not validated on read is a silent-default trap with a YAML extension.
"""
from aximprint.catalog.roles import (
    DEFAULT_NODE, ROLE_CATALOG, SYNTHETIC_ROLES, UNROLED_ROLE, CatalogError, RoleRule,
    SyntheticRole, match_role,
)

__all__ = ["DEFAULT_NODE", "ROLE_CATALOG", "SYNTHETIC_ROLES", "UNROLED_ROLE", "CatalogError",
           "RoleRule", "SyntheticRole", "match_role"]
