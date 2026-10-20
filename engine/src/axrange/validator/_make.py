"""Helper to construct Findings with severity-promotion applied."""
from __future__ import annotations

from typing import Any

from axrange.validator.catalog import get_rule
from axrange.validator.finding import Finding, Severity


def make_finding(
    rule_id: str,
    *,
    reproducible: bool = False,
    location: str | None = None,
    extra: dict[str, Any] | None = None,
    **fields: Any,
) -> Finding:
    """Construct a Finding for `rule_id`.

    `fields` are substituted into the rule's `message_template`. Missing
    placeholders are tolerated (left as `{name}`) to keep failure modes
    visible rather than silent KeyError crashes.
    """
    rule = get_rule(rule_id)
    severity = rule.severity
    if rule.reproducible_promotes_to_error and reproducible:
        severity = severity.promote()
    try:
        message = rule.message_template.format(**fields)
    except (KeyError, IndexError, ValueError):
        message = rule.message_template
    return Finding(
        rule_id=rule.id,
        severity=severity,
        rule_severity=rule.severity,
        category=rule.category,
        title=rule.title,
        message=message,
        location=location,
        related=rule.related,
        fixable=rule.fixable,
        extra=extra or dict(fields),
    )


def parse_finding_to_finding(parse_f, *, reproducible: bool = False) -> Finding:
    """Convert a RawParseFinding (from the parser) into a Finding."""
    fields = dict(parse_f.extra)
    fields["message"] = parse_f.message
    if "path" not in fields and parse_f.file:
        fields["path"] = parse_f.file
    return make_finding(
        parse_f.rule_id,
        reproducible=reproducible,
        location=parse_f.file,
        **fields,
    )
