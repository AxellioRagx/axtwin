"""The network IR — a point-in-time structural representation of the monitored network.

`docs/WORK_ORDER_network_ir.md` is the design; `docs/SPEC_network_ir.md` is the schema of record.

**The one versioned schema in this project** (DESIGN_BRIEF §0.2, amended 2026-08-14 by user
ratification). Everything else stays incubating — see `schema.py`'s module docstring before
concluding anything general from the presence of `SCHEMA_VERSION` here.

**One narrow exception exists as of 2026-09-04, and it is not here:** the cast provenance
sidecar declares `axr-cast-provenance/1` (`synth/compiler.py::PROVENANCE_SCHEMA`) and the
topology export declares `axr-topology/…` (`synth/imprint_bundle.py::BUNDLE_SCHEMA`), because
axRange reads both on an independent lifecycle — `LES-001`'s own amended discriminator. Neither
is an IR version, neither is spelled with this module's constant name, and neither widens the
ratification this package carries. The event model, feature shapes, plugin contract and belief
store are untouched and stay incubating.

Layering: this package sits between `store` and `synth`. It reads the belief store (once the
materializer lands in build step 3) and is read by the twin compiler, the drift comparator, and
whatever else consumes a documented schema. It is **forbidden for `acquire`**, on the same
meaning-not-cycles grounds as `catalog` (CONVENTIONS §3).

Build step 1 landed the schema alone; steps 3 and 5 added `materialize` and `compare`, and
`docs/WORK_ORDER_visibility_and_vantage.md` added `merge` (combine PITs across taps or across
spans) and `visibility` (what a vantage saw and knows it missed) on 2026-08-17. Those four are
imported by their consumers directly rather than re-exported here — this module re-exports the
VOCABULARIES, which is what a reader of a persisted artifact needs and what several tests check
the SPEC against.
"""
from aximprint.ir.keys import (
    KEY_NAMESPACES, NOT_A_KEY, derive_entity_key, find_collisions, unusable_namespaces,
)
from aximprint.ir.schema import (
    COVERAGE_SOURCES, EVIDENCE_CLASSES, IR_TABLES, KEY_KINDS, LOCALITY_CLASSES, MERGE_AXES,
    SCHEDULE_CLASSES, SCHEMA_VERSION, UNSEEN_REASONS, can_read, evidence_rank, parse_version,
    vantage_set,
)

__all__ = [
    "COVERAGE_SOURCES", "EVIDENCE_CLASSES", "IR_TABLES", "KEY_KINDS", "KEY_NAMESPACES",
    "LOCALITY_CLASSES", "MERGE_AXES", "NOT_A_KEY", "SCHEDULE_CLASSES", "SCHEMA_VERSION",
    "UNSEEN_REASONS", "can_read", "derive_entity_key", "evidence_rank", "find_collisions",
    "parse_version", "unusable_namespaces", "vantage_set",
]
