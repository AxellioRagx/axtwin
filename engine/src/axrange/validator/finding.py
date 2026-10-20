"""Finding + severity types per docs/VALIDATOR.md §3.

A Finding is one rule firing on one IR location. The engine assembles
findings into a ValidationResult; the CLI renders them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    def rank(self) -> int:
        """Ordering for severity-promotion (info < warning < error)."""
        return {"info": 0, "warning": 1, "error": 2}[self.value]

    def promote(self) -> "Severity":
        """One step up; error is the ceiling."""
        if self == Severity.INFO:
            return Severity.WARNING
        if self == Severity.WARNING:
            return Severity.ERROR
        return Severity.ERROR


class RuleCategory(str, Enum):
    STRUCTURAL = "structural"
    INCLUDE_CONSISTENCY = "include-consistency"
    REFERENCE_INTEGRITY = "reference-integrity"
    CAPABILITY = "capability"
    LIFECYCLE = "lifecycle"
    NETWORK_SANITY = "network-sanity"
    OVERLAY = "overlay"
    ACTIVITY = "activity"
    DEFENSE = "defense"
    FABRIC = "fabric"
    CLUSTER = "cluster"
    PERSONA = "persona"


@dataclass
class Finding:
    """One rule firing.

    `severity` is the effective severity (after `reproducible_promotes_to_error`
    has been applied if relevant). `rule_severity` is the rule's declared
    severity for rendering ("[warn]" labels and the like).
    """

    rule_id: str
    severity: Severity
    rule_severity: Severity
    category: RuleCategory
    title: str
    message: str
    location: str | None = None  # IR path / file / line, where computable
    related: tuple[str, ...] = ()
    fixable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    @property
    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    @property
    def is_info(self) -> bool:
        return self.severity == Severity.INFO

    def render_line(self) -> str:
        """One-line summary for tree/output rendering."""
        tag = {"error": "[error]", "warning": "[warn] ", "info": "[info] "}[
            self.severity.value
        ]
        loc = f" {self.location}" if self.location else ""
        return f"{self.rule_id} {tag} {self.message}{loc}"
