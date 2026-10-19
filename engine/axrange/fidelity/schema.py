"""Fidelity scorecard output shapes + the §4.1 verdict rollup
(`docs/WORK_ORDER_fidelity_scorecard.md` §4.1,
`docs/design/13-golden-twin-fidelity.md` §3/§3.3).

Everything here is a plain dataclass with a `to_dict()` — the document is a
NEW, self-describing JSON artifact (`scorecard_version: "1"` names the SPEC
version certified against, not this file's shape), not an existing
version-locked schema. It is written beside the twin config it certifies
(§4.2) and served over the API through `axrange.api.dtos`.

Three load-bearing properties, each pinned by a test rather than left to
convention:

  * **Per-fact evidence states, never one number.** Every row publishes its
    **evidence mix** — counts per class over the six-class model of
    `docs/design/14-operator-assertions.md` §2 (`asserted` / `imported` /
    `observed` / `referenced` / `imputed` / `compiler`, in precedence
    order). `compiler` is never scored. A seventh bucket,
    `unclassified`, counts facts the reference declared no class for — the
    honest state while the provenance sidecar is absent (§8.1); it is NOT a
    seventh evidence class, and re-labelling those facts `observed` would be
    exactly the imputed-as-observed fabrication the spec exists to prevent.
  * **`not-computed` is a first-class verdict with a MANDATORY reason,**
    distinct from `fail` (LES-089: "capture UNVERIFIED", never a silent
    skip). `Row.__post_init__` refuses to construct one without a reason.
  * **`provisional: true` on rows 6/8/9/10** whatever the verdict, until
    spec v1.1 fixes their tolerances (spec §8.3). A provisional row never
    contributes to the rollup on spec v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# The SPEC version certified against (`docs/design/13-golden-twin-fidelity.md`),
# not this module's own shape version.
SCORECARD_VERSION = "1"

# `docs/design/14-operator-assertions.md` §2, in PRECEDENCE order (highest
# first). `compiler` lives in the sidecar only and is never scored.
EVIDENCE_CLASSES = (
    "asserted", "imported", "observed", "referenced", "imputed", "compiler",
)
# NOT a seventh class -- see the module docstring.
UNCLASSIFIED = "unclassified"
_MIX_KEYS = (*EVIDENCE_CLASSES, UNCLASSIFIED)

# Row verdicts.
PASS = "pass"
FAIL = "fail"
NOT_COMPUTED = "not-computed"
ATTESTED = "attested"          # row 11 only -- an operator attestation
NOT_ATTESTED = "not-attested"  # row 11 only -- never `pass` (§3.2)

# Document verdicts (§4.1).
GOLDEN = "golden"
NOT_GOLDEN = "not-golden"
INCOMPLETE = "incomplete"

# The one per-fact STATE this document names in prose, from the §3.4
# deployment-scope ruling (work order §9.6): a host, segment or listen fact
# the DEPLOY GATE kept out at the twin's recorded threshold. Reported, NEVER
# failed.
#
# `14` §2's other per-fact states (*observed-*, *referenced-*, *imported-*
# and *asserted-and-reproduced*, *imputed*, *compiler (not scored)*) are
# published as the per-row EVIDENCE MIX rather than as constants: the mix is
# the counted form of exactly those states, and a second, uncounted
# vocabulary that nothing emits would be the inert-field shape this project
# has retired repeatedly (LES-172).
STATE_BELOW_DEPLOY_THRESHOLD = "observed, below deploy threshold"

# §4.1's `gate_set`, verbatim. A row may legitimately appear in more than one
# list (row 6 is provisional AND deferred; row 9 is provisional AND reported)
# -- that is the spec's own shape and is published unchanged.
GATING_ROWS = (1, 2, 3, 5)
PROVISIONAL_ROWS = (6, 8, 9, 10)
DEFERRED_ROWS = (4, 6, 7)
REPORTED_ROWS = (9, 11)
GATE_SET_POLICY = "category: structural + behavioral (spec §3.3)"


def gate_set() -> dict[str, Any]:
    return {
        "policy": GATE_SET_POLICY,
        "gating": list(GATING_ROWS),
        "provisional": list(PROVISIONAL_ROWS),
        "deferred": list(DEFERRED_ROWS),
        "reported": list(REPORTED_ROWS),
    }


def row_status(row: int) -> str:
    """`gates` | `deferred` | `reported` for one row number.

    Derived from the three lists above in that order, so the published
    `status` never contradicts `gate_set`. Rows 8 and 10 are `reported` on
    spec v1 and gate from v1.1 (spec §3.3) -- which is exactly "not in
    `gating`", so no special case is needed here.
    """
    if row in GATING_ROWS:
        return "gates"
    if row in DEFERRED_ROWS:
        return "deferred"
    return "reported"


@dataclass
class EvidenceMix:
    """Counts per evidence class for ONE row (`14` §2's ruling: every row
    publishes its mix). Missing keys are published as 0 rather than omitted,
    so a reader can diff two scorecards field by field."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, evidence_class: Optional[str], n: int = 1) -> None:
        """Count `n` facts of `evidence_class`. `None`/unknown -> the
        `unclassified` bucket, never silently promoted to `observed`."""
        key = (evidence_class or "").strip().lower()
        if key not in _MIX_KEYS:
            key = UNCLASSIFIED
        self.counts[key] = self.counts.get(key, 0) + n

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict[str, int]:
        return {k: int(self.counts.get(k, 0)) for k in _MIX_KEYS}


@dataclass
class Clause:
    """One sub-check of a row. A clause reported rather than scored carries
    its `evidence_class` explicitly (§4.1: "every inference-backed clause
    carrying `evidence_class: imputed`")."""

    clause: str
    verdict: str
    detail: Optional[str] = None
    reason: Optional[str] = None
    evidence_class: Optional[str] = None
    scored: bool = True

    def __post_init__(self) -> None:
        if self.verdict == NOT_COMPUTED and not self.reason:
            raise ValueError(
                f"clause {self.clause!r}: a not-computed clause MUST carry a "
                "reason (docs/WORK_ORDER_fidelity_scorecard.md §4.1)"
            )

    def to_dict(self) -> dict[str, Any]:
        # EVERY key, always, `None` included: this document is meant to be
        # DIFFED (two dated scorecards side by side are the substrate a drift
        # report will need, §10), and a key that appears only sometimes turns
        # a value change into a shape change. It is also what keeps the CLI's
        # `--json` and the API's `ScorecardDTO` byte-identical (§6 S4)
        # without either side needing an `exclude_none` rule the other would
        # have to mirror.
        return {
            "clause": self.clause,
            "verdict": self.verdict,
            "detail": self.detail,
            "reason": self.reason,
            "evidence_class": self.evidence_class,
            "scored": self.scored,
        }


@dataclass
class Row:
    row: int
    dimension: str
    category: str
    channel: str
    verdict: str
    reason: Optional[str] = None
    granularity: Optional[str] = None
    evidence: EvidenceMix = field(default_factory=EvidenceMix)
    measured: dict[str, Any] = field(default_factory=dict)
    clauses: list[Clause] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict == NOT_COMPUTED and not self.reason:
            raise ValueError(
                f"row {self.row} ({self.dimension}): a not-computed row MUST "
                "carry a reason -- it is a first-class verdict, distinct from "
                "fail (docs/WORK_ORDER_fidelity_scorecard.md §4.1)"
            )

    @property
    def status(self) -> str:
        return row_status(self.row)

    @property
    def provisional(self) -> bool:
        return self.row in PROVISIONAL_ROWS

    def to_dict(self) -> dict[str, Any]:
        # Same all-keys-always rule as `Clause.to_dict` -- see its comment.
        return {
            "row": self.row,
            "dimension": self.dimension,
            "category": self.category,
            "channel": self.channel,
            "status": self.status,
            "verdict": self.verdict,
            "provisional": self.provisional,
            "reason": self.reason,
            "granularity": self.granularity,
            "evidence": self.evidence.to_dict(),
            "measured": self.measured,
            "clauses": [c.to_dict() for c in self.clauses],
        }


def rollup_verdict(rows: list[Row]) -> str:
    """§4.1's rollup, over the GATING rows only.

        golden      iff every gating row is `pass`
        not-golden  iff every gating row is pass|fail and >= 1 is `fail`
        incomplete  iff ANY gating row is `not-computed`   <- never not-golden

    A gate that could not be evaluated is not a failure, and no build is
    golden on a scorecard with an uncomputed gate. A `provisional` row never
    contributes on spec v1 (spec §8.3) -- which falls out of the rule above,
    since no provisional row is in `GATING_ROWS` on v1.

    A gating row missing from `rows` entirely is treated as `not-computed`:
    a document that silently omits a gate must not read as golden.
    """
    by_number = {r.row: r for r in rows}
    verdicts = []
    for n in GATING_ROWS:
        r = by_number.get(n)
        verdicts.append(r.verdict if r is not None else NOT_COMPUTED)
    if any(v not in (PASS, FAIL) for v in verdicts):
        return INCOMPLETE
    if any(v == FAIL for v in verdicts):
        return NOT_GOLDEN
    return GOLDEN


@dataclass
class Scorecard:
    generated_at: str
    subject: dict[str, Any]
    reference: dict[str, Any]
    rows: list[Row] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scorecard_version: str = SCORECARD_VERSION

    @property
    def verdict(self) -> str:
        return rollup_verdict(self.rows)

    def row(self, number: int) -> Optional[Row]:
        return next((r for r in self.rows if r.row == number), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scorecard_version": self.scorecard_version,
            "generated_at": self.generated_at,
            "verdict": self.verdict,
            "subject": self.subject,
            "reference": self.reference,
            "gate_set": gate_set(),
            "rows": [r.to_dict() for r in self.rows],
            "warnings": list(self.warnings),
        }
