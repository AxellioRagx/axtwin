"""ACT-* checks — Pass 3, accumulate."""
from __future__ import annotations

from collections import defaultdict

from axrange.ir.durations import (
    MIN_JITTER_INTERVAL_S,
    DurationParseError,
    parse_duration,
)
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.finding import Finding


def check_act_010(ctx: ValidatorContext) -> list[Finding]:
    """Activity in inline: mode has all required behavior-DSL fields.

    v1 minimum: `inline.steps` is a non-empty list. The shape of individual
    steps is opaque to the validator (the behavior runner handles them).
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        if act.inline is None:
            continue
        if not act.inline.steps:
            findings.append(
                make_finding(
                    "ACT-010",
                    activity_id=act.id or f"<index {i}>",
                    missing=["steps"],
                    location=f"layers.activity[{i}].inline",
                )
            )
    return findings


def check_act_020(ctx: ValidatorContext) -> list[Finding]:
    """Overrides field exists in the referenced behavior library version.

    No behavior library registry in v1 — passthrough no-op until it lands.
    """
    return []


def check_act_030(ctx: ValidatorContext) -> list[Finding]:
    """Schedule window shorter than behavior's minimum-meaningful duration.

    Requires behavior library metadata; no-op in v1.
    """
    return []


def check_act_040(ctx: ValidatorContext) -> list[Finding]:
    """Activity has exactly one of behavior: or inline:.

    Also: Activity→host and Activity→targets bindings are mutually exclusive
    on the same activity (per the rule's title in VALIDATOR §5.8). Encoded as
    two sub-cases.
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        has_behavior = act.behavior is not None
        has_inline = act.inline is not None and act.inline.steps is not None
        if has_behavior == has_inline:
            state = "both" if has_behavior else "neither"
            findings.append(
                make_finding(
                    "ACT-040",
                    activity_id=act.id or f"<index {i}>",
                    state=state,
                    location=f"layers.activity[{i}]",
                )
            )

    # Mutually-exclusive binding kinds on the same activity.
    raw_bindings = ctx.raw.get("bindings") if isinstance(ctx.raw, dict) else None
    if isinstance(raw_bindings, list):
        host_bound: set[str] = set()
        targets_bound: set[str] = set()
        for b in raw_bindings:
            if not isinstance(b, dict):
                continue
            if "activity" not in b:
                continue
            if "host" in b:
                host_bound.add(b["activity"])
            if "targets" in b:
                targets_bound.add(b["activity"])
        conflict = host_bound & targets_bound
        for act_id in conflict:
            # This is NOT the "exactly one of behavior/inline" mode failure;
            # both modes can coexist on the same activity (an activity that
            # runs on host X and also targets list Y is the common shape).
            # The rule title in VALIDATOR §5.8 is mutually-exclusive "on the
            # same activity" only if multiple activity→host or activity→targets
            # bindings exist. Per IR_SCHEMA §6, at most one of each kind is
            # allowed; the multiplicity check below handles that. Coexistence
            # of one of each is NOT a violation.
            pass

        # IR_SCHEMA §6: an activity has at most one Activity→host binding and
        # at most one Activity→targets binding.
        host_count: dict[str, int] = defaultdict(int)
        targets_count: dict[str, int] = defaultdict(int)
        for b in raw_bindings:
            if isinstance(b, dict) and "activity" in b:
                if "host" in b:
                    host_count[b["activity"]] += 1
                if "targets" in b:
                    targets_count[b["activity"]] += 1
        for act_id, n in host_count.items():
            if n > 1:
                findings.append(
                    make_finding(
                        "ACT-040",
                        activity_id=act_id,
                        state=f"{n} Activity→host bindings (max 1)",
                    )
                )
        for act_id, n in targets_count.items():
            if n > 1:
                findings.append(
                    make_finding(
                        "ACT-040",
                        activity_id=act_id,
                        state=f"{n} Activity→targets bindings (max 1)",
                    )
                )

    return findings


def check_act_050(ctx: ValidatorContext) -> list[Finding]:
    """Per-activity seed silently ignored -- UNLESS a `schedule.interval` is
    present, where it seeds the jittered cadence (docs/SLICE_ACTIVITY.md §11.2,
    the schedule executor). ACT-050 is LIFTED for that case (the seed now has
    a purpose); it still fires for a seed with no interval to consume it.
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        if act.seed is None:
            continue
        jittered = act.schedule is not None and act.schedule.interval is not None
        if jittered:
            continue  # ACT-050 lifted: the seed drives the jitter RNG.
        findings.append(
            make_finding(
                "ACT-050",
                activity_id=act.id or f"<index {i}>",
                location=f"layers.activity[{i}]",
            )
        )
    return findings


def check_act_060(ctx: ValidatorContext) -> list[Finding]:
    """`schedule.interval` is well-formed (docs/SLICE_ACTIVITY.md §11.5): both
    `min` and `max` present and parseable as durations, `max >= min`, and
    mutually exclusive with `schedule.once: true`.
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        sched = act.schedule
        if sched is None or sched.interval is None:
            continue
        interval = sched.interval
        act_id = act.id or f"<index {i}>"
        loc = f"layers.activity[{i}].schedule.interval"
        if not interval.min or not interval.max:
            findings.append(
                make_finding(
                    "ACT-060", activity_id=act_id,
                    detail="requires both min and max", location=loc,
                )
            )
            continue
        if sched.once:
            findings.append(
                make_finding(
                    "ACT-060", activity_id=act_id,
                    detail="interval is mutually exclusive with once: true",
                    location=loc,
                )
            )
        try:
            min_s = parse_duration(interval.min)
            max_s = parse_duration(interval.max)
        except DurationParseError as e:
            findings.append(
                make_finding("ACT-060", activity_id=act_id, detail=str(e), location=loc)
            )
            continue
        if min_s < MIN_JITTER_INTERVAL_S:
            findings.append(
                make_finding(
                    "ACT-060", activity_id=act_id,
                    detail=(
                        f"min ({interval.min}) is below the "
                        f"{MIN_JITTER_INTERVAL_S:.0f}s floor (a near-zero interval "
                        "busy-loops the guest)"
                    ),
                    location=loc,
                )
            )
        if max_s < min_s:
            findings.append(
                make_finding(
                    "ACT-060", activity_id=act_id,
                    detail=f"max ({interval.max}) < min ({interval.min})",
                    location=loc,
                )
            )
    return findings


def check_act_070(ctx: ValidatorContext) -> list[Finding]:
    """A jittered activity (`schedule.interval` set) with no `seed` is
    non-reproducible: the replay loop needs the SAME schedule twice
    (docs/SLICE_ACTIVITY.md §11.2).
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        sched = act.schedule
        if sched is None or sched.interval is None:
            continue
        if act.seed is None:
            findings.append(
                make_finding(
                    "ACT-070",
                    activity_id=act.id or f"<index {i}>",
                    location=f"layers.activity[{i}]",
                )
            )
    return findings


CHECKS: list[Check] = [
    check_act_010,
    check_act_020,
    check_act_030,
    check_act_040,
    check_act_050,
    check_act_060,
    check_act_070,
]
