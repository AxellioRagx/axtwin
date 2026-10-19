"""Check function implementations, organized by category.

Each module exports a `CHECKS: list[Check]` of (rule_id, check_fn) pairs.
The engine iterates through them in pass order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from axrange.ir.parser import RawParseFinding
from axrange.ir.schema import IR
from axrange.manifest.loader import ManifestRegistry
from axrange.validator.finding import Finding


@dataclass
class ValidatorContext:
    """Everything a check function may inspect.

    `raw` is the merged-with-includes IR dict before Pydantic typing — used
    to inspect unknown keys (STR-180) and structural shapes the typed IR
    normalizes away.
    """

    ir: IR
    raw: dict
    manifests: ManifestRegistry
    parse_findings: list[RawParseFinding]
    reproducible: bool = False
    skip_unknown_image_lifecycle: bool = False


Check = Callable[[ValidatorContext], list[Finding]]


def node_kind(node) -> str | None:
    """Normalize `node.kind` to its plain string value.

    `node.kind` may come through as a `NodeKind` enum or a bare string
    depending on how the IR was parsed; every check that switches on kind
    (host/router/switch) needs the same normalization. Was duplicated
    ad hoc across structural.py, network.py, capability.py, and fabric.py
    (as `_kind`) — promoted here as the one shared helper.
    """
    k = node.kind
    return k.value if hasattr(k, "value") else k
