"""INC-* checks — Pass 1, halt on first error.

Most INC findings are produced by the parser (`axrange.ir.parser`) and
folded into Pass 1 by the engine. INC-050 (unused include) needs the merged
IR, so it lives here.
"""
from __future__ import annotations

from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.finding import Finding


def _passthrough(ctx: ValidatorContext, rule_id: str) -> list[Finding]:
    return [
        make_finding(
            rule_id,
            path=p.file or "",
            message=p.message,
            location=p.file,
            **{k: v for k, v in p.extra.items() if k not in ("message", "path")},
        )
        for p in ctx.parse_findings
        if p.rule_id == rule_id
    ]


def check_inc_010(ctx: ValidatorContext) -> list[Finding]:
    return _passthrough(ctx, "INC-010")


def check_inc_020(ctx: ValidatorContext) -> list[Finding]:
    return _passthrough(ctx, "INC-020")


def check_inc_030(ctx: ValidatorContext) -> list[Finding]:
    return _passthrough(ctx, "INC-030")


def check_inc_040(ctx: ValidatorContext) -> list[Finding]:
    return _passthrough(ctx, "INC-040")


def check_inc_050(ctx: ValidatorContext) -> list[Finding]:
    """Info: included file defines content the main scenario does not reference.

    Implementation: an include is "unused" if every ID it contributes is
    unreferenced by any of the IR's reference fields. Walking that exhaustively
    is non-trivial; for v1 we emit no INC-050 finding unless the parser
    explicitly flagged it. Operator can re-evaluate when fragment hygiene
    matters in practice.
    """
    return _passthrough(ctx, "INC-050")


CHECKS: list[Check] = [
    check_inc_010,
    check_inc_020,
    check_inc_030,
    check_inc_040,
    check_inc_050,
]
