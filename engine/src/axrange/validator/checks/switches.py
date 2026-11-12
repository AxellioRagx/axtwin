"""NSW -- native switches (`docs/design/native-switches.md` §9; Authority:
user, 2026-11-12). Thin over `axrange.switchmodel`, which owns the model the
planner and the tap playout read too; this module only turns its problems
into findings. Fires only when the platform's capability set is known
(`ValidatorContext.platform_caps`); the planner's Pass 1 runs the same model
with the target's capabilities and refuses its errors."""
from __future__ import annotations

from axrange.switchmodel import resolve_switches
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.finding import Finding, Severity


def _model_findings(ctx: ValidatorContext, rule_id: str) -> list[Finding]:
    if ctx.platform_caps is None:
        return []
    model = resolve_switches(ctx.ir, ctx.platform_caps)
    findings: list[Finding] = []
    for p in model.problems:
        if p.rule_id != rule_id:
            continue
        f = make_finding(p.rule_id, location=p.location, detail=p.message)
        if p.severity == "warning" and f.severity != Severity.WARNING:
            f.severity = Severity.WARNING  # NSW-070's no-effect half
        findings.append(f)
    return findings


def _rule_check(rule_id: str) -> Check:
    def check(ctx: ValidatorContext) -> list[Finding]:
        return _model_findings(ctx, rule_id)
    check.__name__ = f"check_{rule_id.lower().replace('-', '_')}"
    check.__doc__ = f"{rule_id}: see axrange.switchmodel.resolve_switches."
    return check


RULE_IDS = ("NSW-020", "NSW-030", "NSW-040", "NSW-050", "NSW-060", "NSW-070", "NSW-080")
CHECKS: list[Check] = [_rule_check(r) for r in RULE_IDS]
