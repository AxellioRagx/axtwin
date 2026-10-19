"""Synthesizer v0 (STUB with real shape): belief snapshot -> draft twin YAML.

Rules that already hold in v0 and must survive every iteration (DESIGN_BRIEF §9):
deterministic compiler (same snapshot -> same YAML), per-field provenance
(observed | referenced | imputed | defaulted). Role classification is defaulted
to 'unknown' until learners land; segments/conduits come with the extractors.

**This module's provenance vocabulary is the PRE-2026-09-04 one, deliberately.** The real
compiler (`synth/compiler.py`) renamed `defaulted` -> `compiler` and its sidecar declares
`axr-cast-provenance/1` so a consumer can tell the rename from a reader bug
(`docs/WORK_ORDER_reference_ingest.md` D5/D6). Nothing outside this repo reads THIS path's
output — it is the v0 draft, superseded on 2026-08-14 and kept only because
`scripts/verify_determinism.py` check [2] still pins it — so migrating it would be churn on a
module scheduled for retirement, and half-migrating it would be worse than either. Retire the
module and this note goes with it.
"""
from __future__ import annotations

import yaml

# The ONE edit this file takes from `Q1`'s round, and it is a literal-to-constant swap with no
# behavioural change: `__longtail__` used to be spelled out here as a third independent copy of a
# sentinel that `windowing.windows` mints and `learners/extractors.py` filters (`LES-017` —
# a published namespace is the union of every path that can mint a member, and a sentinel written
# out in three places is one edit away from being declared in two of them and minted in the
# third). Entity SEMANTICS reaching the twin — one twin host per entity, external peers included
# — remains that work order's own round and is untouched here.
from aximprint.windowing.windows import LONGTAIL_ENTITY


def synthesize(snapshot: dict) -> dict:
    nodes = []
    listens_by_ent: dict[str, list] = {}
    for subject, obj, conf in sorted(snapshot["listens"]):
        listens_by_ent.setdefault(subject, []).append({"port": obj, "confidence": conf})
    for entity, last_ws, windows_seen in sorted(snapshot["entities"]):
        nodes.append({
            "entity": entity,
            "role": {"value": "unknown", "provenance": "defaulted"},
            "services": {"value": listens_by_ent.get(entity, []),
                         "provenance": "observed"},
            "windows_observed": windows_seen,
        })
    conduits = [{"from": s, "to": d, "proto": p,
                 "flows": int(f), "bytes": int(b),
                 "provenance": "observed", "policy_side": "allow-floor-only"}
                for s, d, p, f, b in sorted(snapshot["edges"])
                if d != LONGTAIL_ENTITY]
    return {
        "twin_config_draft": {
            "generated_by": "aximprint synth v0",
            "as_of": snapshot.get("as_of"),
            "nodes": nodes,
            "conduits": conduits,
            "identity": {"value": [
                {"kind": k, "ip": ip, "bound_to": bt}
                for k, ip, bt, *_ in sorted(snapshot["bindings"])],
                "provenance": "observed"},
            "caveats": [
                "roles defaulted: learners not yet implemented",
                "segments/gateways not yet extracted",
                "deny-side policy structurally unrecoverable from capture",
            ],
        }
    }


def to_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
