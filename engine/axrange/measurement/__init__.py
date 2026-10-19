"""axrange.measurement — A4 detection-scoring analysis code
(docs/design/08-exercise-measurement.md).

O2-side ATT&CK coverage tooling: the ground-truth marker line format, the
detection->technique resolver, and the truth x detections coverage join. The
Grafana coverage dashboard (`o2/grafana/dashboards/attack-coverage.json`) is
the operator-facing view over the SAME Loki streams this package's pure
functions join in python (the work order's "make it testable" requirement) —
useful today for tests and later for an automated scorer (`o2control`,
deferred, docs/design/08-exercise-measurement.md).

Lives inside the `axrange` package for reuse/testability (LES-025-style
downward organization), but is a LEAF: the engine proper (`axrange.ir`,
`axrange.validator`, `axrange.planner`, `axrange.adapter_proxmox`,
`axrange.cli`) never imports this package. It is consumed by O2-side tooling
only.
"""
from __future__ import annotations

from axrange.measurement.coverage import (
    Detection,
    RunCoverage,
    TechniqueCoverage,
    TruthMarker,
    join_coverage,
    parse_truth_marker,
)
from axrange.measurement.detections import (
    FALLBACK_RULE_TECHNIQUE_MAP,
    parse_technique_from_rule,
    technique_for_detection,
)

__all__ = [
    "Detection",
    "RunCoverage",
    "TechniqueCoverage",
    "TruthMarker",
    "join_coverage",
    "parse_truth_marker",
    "FALLBACK_RULE_TECHNIQUE_MAP",
    "parse_technique_from_rule",
    "technique_for_detection",
]
