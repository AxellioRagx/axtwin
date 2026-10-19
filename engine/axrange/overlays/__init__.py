"""axrange.overlays -- small, shared, dependency-free overlay registries.

Not a new execution plane: this package holds pure data/lookup helpers that
the planner (`planner/pass1_emit.py`) reads at emission time and the
validator can read too, without either of them reaching into `config_exec`
(the render/QGA-delivery machinery, which the planner must never import --
LES-025) or into each other's territory. Kept intentionally tiny.

First (and so far only) member: `default_agents` -- the A3 Stage A
"default-agent-set" registry (docs/design/08-exercise-measurement.md).
"""
