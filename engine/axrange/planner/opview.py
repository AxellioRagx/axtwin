"""axrange.planner.opview — a tiny "op fields" duck-typing helper shared by
every read-only info/status extraction across the CLI
(`docs/WORK_ORDER_cli_lifecycle.md` §3.2).

An `OpInstance` (an in-memory `Plan`'s op, `axrange.planner.dag`) and an
`OpRow` (a persisted op, `axrange.planner.state`) carry the same logical
`(op_name, scope, inputs_resolved, outputs)` shape, but `OpInstance`'s are
plain dict attributes while `OpRow`'s are JSON-encoded columns. This is the
ONE place that normalizes both into a uniform dict shape, so every extractor
(`axrange.cli.main`'s `te info`, `axrange.o2control.control`'s surface
inventory, `axrange.o2control.taps`'s tap-plan info) reads either an
in-memory dry-planned `Plan` (not yet deployed — "resolved config") or a
live deploy's persisted ops (`StateStore.ops_for_deploy` — "config + access
facts") through the SAME code path, instead of a duck-typing helper
reimplemented per caller (`o2control.control._inventory_from_ops` had its
own 3-tuple version before this generalized it — LES-037).
"""
from __future__ import annotations

import json
from typing import Any


def _json_obj(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def op_fields(op: Any) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """`(op_name, scope, inputs_resolved, outputs)` for either an
    `OpInstance` or an `OpRow`.

    `outputs` is always `{}` for an `OpInstance` (an in-memory, not-yet-
    executed op never carries outputs) and is `{}` for an `OpRow` that
    hasn't completed yet (`outputs_json` is `NULL` until the op finishes).
    """
    if hasattr(op, "scope"):
        # OpInstance (axrange.planner.dag) — an in-memory Plan's op.
        return op.op_name, dict(op.scope), dict(op.inputs_resolved), {}
    # OpRow (axrange.planner.state) — persisted, JSON-encoded columns.
    return (
        op.op_name,
        _json_obj(op.scope_json),
        _json_obj(op.inputs_resolved_json),
        _json_obj(op.outputs_json),
    )
