"""Twin configs — parameterized IR templates + a starter catalog
(`docs/design/09-content-overlays.md`, `docs/WORK_ORDER_twin_configs.md`).

A `twin_config` is content, not code: a small, permissive YAML schema
(`schema.py`) an author writes once, plus `params: {NAME: default}` that a
deploy-time `--param NAME=value` can override (`params.py`'s minimal
`${NAME:default}` substitution — the deferred idea from `docs/
OPEN_QUESTIONS.md` Q2, reused rather than reinvented). `expand.py` renders a
config + resolved params into a standard `te`-tier Scenario IR dict — the
SAME shape `axrange.ir.parser`/`axrange.validator`/`axrange.planner` already
consume, completely unchanged (LES-025: this package produces IR, it does
not become a second IR dialect or a second validator/planner).

Boundary: twin CONTENT only (infra/apps/identity/data) — no attack/activity
(that's `exr`), no traffic drivers (that's `tfc`). `axrange.twinconfig` never
imports `axrange.o2control`, `axrange.personas`'s internals, or
`scenario_ai_research` (LES-025's reuse-downward direction); it may reuse
`axrange.ir`/`axrange.manifest` the same way the CLI and `imagebuild` do.
The ONE exception is `gate.confirm_deploy_gate`, which reuses the planner's
own `hash_ir` (function-local import) to CONFIRM a recorded `ir_hash` —
reusing the engine's canonical hash rather than maintaining a second
definition that would have to agree with it. Nothing else here reaches the
engine.
"""
from __future__ import annotations

from axrange.twinconfig.errors import (
    DeployGateRefusalError,
    TwinConfigError,
    TwinConfigNotFoundError,
    TwinConfigParamError,
    TwinConfigShapeError,
)
from axrange.twinconfig.expand import (
    expand_twin_config,
    instance_ids,
    materialize_twin_config,
)
from axrange.twinconfig.gate import (
    DEFAULT_MIN_OCCUPANCY,
    DEFAULT_MIN_ROLE_CONFIDENCE,
    DeployGateRecord,
    DeployGateReport,
    confirm_deploy_gate,
    deploy_gate_report,
)
from axrange.twinconfig.registry import list_twin_configs, load_twin_config
from axrange.twinconfig.schema import TwinConfig, TwinConfigObservation

__all__ = [
    "DEFAULT_MIN_OCCUPANCY",
    "DEFAULT_MIN_ROLE_CONFIDENCE",
    "DeployGateRecord",
    "DeployGateRefusalError",
    "DeployGateReport",
    "TwinConfig",
    "TwinConfigError",
    "TwinConfigNotFoundError",
    "TwinConfigObservation",
    "TwinConfigParamError",
    "TwinConfigShapeError",
    "confirm_deploy_gate",
    "deploy_gate_report",
    "expand_twin_config",
    "instance_ids",
    "materialize_twin_config",
    "list_twin_configs",
    "load_twin_config",
]
