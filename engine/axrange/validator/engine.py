"""4-pass execution engine + 4 entry points per docs/VALIDATOR.md §2 + §4.

Pass 1 (STR + INC) — halt on first error.
Pass 2 (REF)        — halt on first error.
Pass 3 (CAP/LIF/NET/OVR/ACT/DEF) — accumulate.
Pass 4 (CLU)        — planner-side only; gated on a Proxmox snapshot.

The four entry points share this engine; they differ only in which passes
they run and whether they expect a cluster snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from axrange.ir.parser import (
    IncludeError,
    ParseError,
    ParseResult,
    RawParseFinding,
    StructuralError,
    parse_ir,
)
from axrange.ir.schema import IR
from axrange.manifest.loader import ManifestRegistry
from axrange.validator._make import make_finding
from axrange.validator.catalog import Rule, get_rule, list_rules
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.checks import (
    activity as _activity,
    capability as _capability,
    defense as _defense,
    fabric as _fabric,
    includes as _includes,
    lifecycle as _lifecycle,
    network as _network,
    overlay as _overlay,
    references as _references,
    structural as _structural,
)
from axrange.validator.finding import Finding, RuleCategory, Severity


PASS_1_CHECKS: list[Check] = _structural.CHECKS + _includes.CHECKS
PASS_2_CHECKS: list[Check] = _references.CHECKS
PASS_3_CHECKS: list[Check] = (
    _capability.CHECKS
    + _lifecycle.CHECKS
    + _network.CHECKS
    + _overlay.CHECKS
    + _activity.CHECKS
    + _defense.CHECKS
    + _fabric.CHECKS
)


class Disposition(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS-WITH-WARNINGS"
    FAIL = "FAIL"


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)
    passes_run: list[int] = field(default_factory=list)
    ir: IR | None = None
    source_files: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.INFO]

    @property
    def disposition(self) -> Disposition:
        if any(f.severity == Severity.ERROR for f in self.findings):
            return Disposition.FAIL
        if any(f.severity == Severity.WARNING for f in self.findings):
            return Disposition.PASS_WITH_WARNINGS
        return Disposition.PASS

    @property
    def exit_code(self) -> int:
        return 1 if self.disposition == Disposition.FAIL else 0

    def by_pass(self, pass_: int) -> list[Finding]:
        rules_in_pass = {r.id for r in list_rules(pass_=pass_)}
        return [f for f in self.findings if f.rule_id in rules_in_pass]


def _run_checks(checks: list[Check], ctx: ValidatorContext) -> list[Finding]:
    out: list[Finding] = []
    for check in checks:
        try:
            out.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001
            # Surface check crashes as STR-010-like findings rather than
            # bringing down validation. Operator can repro and we can fix.
            out.append(
                make_finding(
                    "STR-010",
                    reason=f"internal check failure in {check.__name__}: {exc!r}",
                )
            )
    return out


def _has_error(findings: list[Finding]) -> bool:
    return any(f.severity == Severity.ERROR for f in findings)


def _build_context(
    parse_result: ParseResult,
    manifests: ManifestRegistry,
    *,
    skip_unknown_image_lifecycle: bool = False,
) -> ValidatorContext:
    reproducible = bool(parse_result.ir.scenario and parse_result.ir.scenario.reproducible)
    return ValidatorContext(
        ir=parse_result.ir,
        raw=parse_result.raw,
        manifests=manifests,
        parse_findings=parse_result.parse_findings,
        reproducible=reproducible,
        skip_unknown_image_lifecycle=skip_unknown_image_lifecycle,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _parse(source_or_path: str | Any | None, *, text: str | None = None) -> ParseResult:
    """Accept a filename, a ParseResult, or text= alone.

    Coarse parse failures (unparseable YAML, missing root mapping) are
    converted into a ParseResult carrying a single STR-010 RawParseFinding
    rather than propagating as exceptions — the engine surfaces them as
    Pass 1 findings and reports normally.
    """
    if isinstance(source_or_path, ParseResult):
        return source_or_path
    try:
        if text is not None:
            return parse_ir(text=text)
        if source_or_path is None:
            raise ValueError("validate_* requires either a scenario path/ParseResult or text=")
        return parse_ir(path=source_or_path)
    except StructuralError as e:
        from axrange.ir.schema import IR
        return ParseResult(
            ir=IR(),
            raw={},
            source_files=[e.path or "<unknown>"],
            parse_findings=[
                RawParseFinding(
                    rule_id="STR-010",
                    message=str(e),
                    file=e.path,
                )
            ],
        )
    except IncludeError as e:
        # Top-level path missing entirely — surface as STR-010 with a clear note.
        from axrange.ir.schema import IR
        return ParseResult(
            ir=IR(),
            raw={},
            source_files=[e.path or "<unknown>"],
            parse_findings=[
                RawParseFinding(
                    rule_id="STR-010",
                    message=str(e),
                    file=e.path,
                )
            ],
        )


def validate_structural(
    scenario: str | ParseResult | None = None,
    *,
    text: str | None = None,
) -> ValidationResult:
    """Run Passes 1 + 2 only. Used by `axrange validate` when manifests are
    unavailable, and by the canvas on every keystroke.
    """
    parse_result = _parse(scenario, text=text)
    # Empty manifest registry is fine — Pass 2 doesn't need image data. This
    # entry point never has manifests available (canvas/shape-only checks),
    # so image-lifecycle rules (REF-120 et al.) are told to skip via the flag
    # rather than inferring "skip" from an empty registry — validate_full can
    # ALSO be called with an empty registry (e.g. a bad --images path) and
    # that case must still fail loudly.
    ctx = _build_context(
        parse_result, ManifestRegistry(), skip_unknown_image_lifecycle=True
    )
    result = ValidationResult(
        ir=parse_result.ir, source_files=parse_result.source_files
    )

    # Pass 1
    p1 = _run_checks(PASS_1_CHECKS, ctx)
    result.findings.extend(p1)
    result.passes_run.append(1)
    if _has_error(p1):
        return result

    # Pass 2
    p2 = _run_checks(PASS_2_CHECKS, ctx)
    result.findings.extend(p2)
    result.passes_run.append(2)
    return result


def validate_full(
    scenario: str | ParseResult | None = None,
    manifests: ManifestRegistry | None = None,
    *,
    text: str | None = None,
) -> ValidationResult:
    """Run Passes 1–3. The standard `axrange validate` entry point."""
    parse_result = _parse(scenario, text=text)
    if manifests is None:
        manifests = ManifestRegistry()
    ctx = _build_context(parse_result, manifests)
    result = ValidationResult(
        ir=parse_result.ir, source_files=parse_result.source_files
    )

    p1 = _run_checks(PASS_1_CHECKS, ctx)
    result.findings.extend(p1)
    result.passes_run.append(1)
    if _has_error(p1):
        return result

    p2 = _run_checks(PASS_2_CHECKS, ctx)
    result.findings.extend(p2)
    result.passes_run.append(2)
    if _has_error(p2):
        return result

    p3 = _run_checks(PASS_3_CHECKS, ctx)
    result.findings.extend(p3)
    result.passes_run.append(3)
    return result


def validate_with_cluster(
    scenario: str | ParseResult | None = None,
    manifests: ManifestRegistry | None = None,
    cluster_snapshot: Any = None,
    *,
    text: str | None = None,
) -> ValidationResult:
    """Run Passes 1–4. The `axrange deploy` entry point.

    Pass 4 (CLU) is planner-side and depends on the Proxmox adapter (OPEN_QUESTIONS Q5).
    Until the adapter ratifies, this entry point is shape-only: it accepts a
    `cluster_snapshot` argument and currently emits no CLU findings. The signature
    is locked so the planner can be wired against it once Q5 lands.
    """
    result = validate_full(scenario, manifests=manifests, text=text)
    # Pass 4 stub: a future implementation walks cluster_snapshot to check
    # CLU-010..CLU-070. Recorded but skipped.
    if cluster_snapshot is not None and Disposition.FAIL not in (result.disposition,):
        # When the adapter lands, run CLU checks here.
        result.passes_run.append(4)
    return result


def explain_rule(rule_id: str) -> Rule:
    """Return the rule definition for an `axrange validate --explain` call.

    Raises KeyError on unknown rule_id (caller renders the missing-id message).
    """
    return get_rule(rule_id)
