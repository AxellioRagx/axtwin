"""axRange persona library — benign, twin-agnostic behavioral archetypes
(docs/design/07-traffic-personas.md, docs/WORK_ORDER_persona_library.md).

Engine vocabulary, like `axrange.sensors`: imported DOWNWARD by the future
Traffic tier (`tfc`), never imported by `axrange.planner`/`axrange.
adapter_proxmox` (LES-025). No profile/binding logic lives here (that's the
Traffic tier's job) and no attacker behavior (that's the Exercise tier's).
"""
from __future__ import annotations

from axrange.personas.schema import Emit, Persona, PersonaSchemaError, load_persona
from axrange.personas.registry import (
    PERSONAS, all_ids, get, is_known_id, reload_personas,
)

__all__ = [
    "Emit",
    "Persona",
    "PersonaSchemaError",
    "load_persona",
    "PERSONAS",
    "all_ids",
    "reload_personas",
    "get",
    "is_known_id",
]
