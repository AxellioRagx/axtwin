"""The deploy gate — `DEPLOY_MIN_OCCUPANCY` over a cast twin-config's
`observation:` blocks (`docs/WORK_ORDER_deploy_gate.md`,
`docs/design/13-golden-twin-fidelity.md` §3.4 "the cast describes, the deploy
selects").

An imprint compiler (aximprint) no longer DROPS a thinly-observed host at
compile time: it emits every observed host and annotates each node/segment
with what the observation actually was (`windows_seen`/`windows_total`/
`occupancy`/`role_confidence`). This module is the consumer half — it turns
that annotation into a selection at expansion time, at a threshold the
operator sets. One cast then serves every threshold.

Boundary (LES-025, and `expand.py`'s own version-lock note): this module is
CONTENT-side, like the rest of `axrange.twinconfig`. `confirm_deploy_gate`
is the ONE function here that reaches the engine, and it does so with a
FUNCTION-LOCAL import of `axrange.planner.dag.hash_ir` — deliberately, and
only to reuse the planner's own canonical hash rather than write a second
one that would then have to be kept in agreement with it (two hash
definitions that must match is the bug, not the import). Nothing else in
this package imports `axrange.planner`.

Never silently skip (LES-089): every gated-out entity, every dropped
conduit, every router kept in spite of a sub-threshold block, and the
effective thresholds themselves are reported — through the expander's
`warnings` out-parameter on `te validate|plan|deploy --config` and the
API's `config_warnings`, and uncapped in `axrange twin-config show`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from axrange.twinconfig.errors import TwinConfigError, TwinConfigParamError, TwinConfigShapeError
from axrange.twinconfig.registry import CatalogDirArg, load_twin_config_raw
from axrange.twinconfig.schema import TwinConfigObservation

# The bar `docs/design/13-golden-twin-fidelity.md` row 1 judges against
# ("is every observed host above the bar placed?"), so the deployable set and
# the set row 1 expects are ONE set by construction (Authority: user,
# CHANGELOG 2026-09-04). A cast that forgets to declare the param still gates
# here.
DEFAULT_MIN_OCCUPANCY = 0.8

# OFF by default: at 0.0 this clause can never keep a present host out, which
# is exactly what "no block / no role_confidence => never gated out" requires.
DEFAULT_MIN_ROLE_CONFIDENCE = 0.0

PARAM_MIN_OCCUPANCY = "DEPLOY_MIN_OCCUPANCY"
PARAM_MIN_ROLE_CONFIDENCE = "DEPLOY_MIN_ROLE_CONFIDENCE"

# Comparison epsilon (work order §4.2): "seen in 8 of 10 windows" at a 0.8
# threshold is IN -- the plain reading of ">= 80%". A float boundary flip here
# silently changes the node set and therefore `plan_id`, so it is pinned by a
# test rather than left to chance.
GATE_EPSILON = 1e-9

# `occupancy` is CARRIED (it is the compiler's own definition and may stop
# being a plain ratio), but CROSS-CHECKED against the two window counts, so a
# producer bug surfaces at expansion instead of silently selecting the wrong
# node set (work order §3.2). The counts are a checksum, not a second source.
OCCUPANCY_TOLERANCE = 1e-6

# Per-entity warning lines only; the summary lines are never capped. An
# operator who wants the full table runs `axrange twin-config show <id>`.
WARNING_DETAIL_CAP = 50


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeployThresholds:
    min_occupancy: float = DEFAULT_MIN_OCCUPANCY
    min_role_confidence: float = DEFAULT_MIN_ROLE_CONFIDENCE


def _threshold(params: dict[str, Any], name: str, default: float) -> float:
    """One resolved threshold out of the ALREADY-resolved param map
    (`params.resolve_params` has run: a `--param` override is merged and
    coerced, a declared default is present as authored).

    Absent -> the built-in default. Present but non-numeric or outside
    `[0, 1]` -> `TwinConfigParamError` naming the value: `coerce_scalar("abc")`
    yields the plain string `"abc"`, which must fail LOUDLY rather than
    compare falsy against a float and silently gate at 0.0.
    """
    if name not in params or params[name] is None:
        return default
    raw = params[name]
    # bool is an int subclass -- `--param DEPLOY_MIN_OCCUPANCY=true` would
    # otherwise resolve to 1.0 and gate everything out.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TwinConfigParamError(
            f"{name}: {raw!r} is not a number -- it must be a fraction in [0, 1] "
            f"(e.g. --param {name}=0.5)"
        )
    value = float(raw)
    if not (0.0 <= value <= 1.0):
        raise TwinConfigParamError(
            f"{name}: {value} is outside [0, 1] -- it is a FRACTION of the "
            "observation windows a host was seen in, not a percentage"
        )
    return value


def deploy_thresholds(params: dict[str, Any]) -> DeployThresholds:
    """Resolve both gate thresholds from a twin-config's effective params."""
    return DeployThresholds(
        min_occupancy=_threshold(params, PARAM_MIN_OCCUPANCY, DEFAULT_MIN_OCCUPANCY),
        min_role_confidence=_threshold(
            params, PARAM_MIN_ROLE_CONFIDENCE, DEFAULT_MIN_ROLE_CONFIDENCE
        ),
    )


def normalize_declared_thresholds(declared: dict[str, Any]) -> dict[str, Any]:
    """Return `declared` with the two gate thresholds coerced to `float`.

    `params._coerce_like` types a `--param` override against the config's own
    declared default, so a config that writes the perfectly natural
    `DEPLOY_MIN_ROLE_CONFIDENCE: 0` (an INT) makes
    `--param DEPLOY_MIN_ROLE_CONFIDENCE=0.9` fail with "not a valid int".
    Fixed HERE, narrowly, for the two keys this module owns -- NOT by
    widening `_coerce_like`, which would silently re-type every int param a
    config declares (`N_WORKSTATIONS: 25` must keep rejecting `=2.5`).
    Non-numeric values are left untouched: `_threshold` raises on them with
    a message naming the value.
    """
    out = dict(declared)
    for name in (PARAM_MIN_OCCUPANCY, PARAM_MIN_ROLE_CONFIDENCE):
        value = out.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[name] = float(value)
    return out


def normalize_threshold(value: float) -> str:
    """The ONE canonical string form of a threshold (work order §6).

    `scenario.labels` values join `ir_hash`, so the same effective threshold
    must always produce the same bytes: `str(0.1 + 0.7)` is
    `'0.7999999999999999'`, which would mint a different `plan_id` than a
    literal `0.8` for the same intended bar. Fixed 6-decimal rendering with
    trailing zeros trimmed: `0.8 -> "0.8"`, `0.0 -> "0"`, `1.0 -> "1"`.
    """
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# Observation-block validation (work order §3.3)
# ---------------------------------------------------------------------------
def check_observation(
    obs: TwinConfigObservation, entity: str, *, role_confidence_allowed: bool,
) -> None:
    """Validate one `observation:` block, naming `entity` (e.g. `node 'ws'`).

    Rule (6) -- all-or-nothing -- is why the three numeric fields are
    `Optional` on the model: a missing one must be reported against the
    ENTITY (`node 'file-server-10-80-64'`), not against a pydantic list index
    the author would then have to count out by hand. `extra="forbid"` covers
    the reverse (an unknown inner key).
    """
    missing = [
        name for name in ("windows_seen", "windows_total", "occupancy")
        if getattr(obs, name) is None
    ]
    if missing:
        raise TwinConfigShapeError(
            f"{entity}: observation: is missing required field(s) {missing} -- "
            "a partial block is an error, not a partial observation "
            "(windows_seen, windows_total and occupancy are all required)"
        )

    windows_seen = int(obs.windows_seen)  # type: ignore[arg-type]
    windows_total = int(obs.windows_total)  # type: ignore[arg-type]
    occupancy = float(obs.occupancy)  # type: ignore[arg-type]

    if windows_total < 1:
        raise TwinConfigShapeError(
            f"{entity}: observation.windows_total {windows_total} must be >= 1"
        )
    if not (0 <= windows_seen <= windows_total):
        raise TwinConfigShapeError(
            f"{entity}: observation.windows_seen {windows_seen} must be in "
            f"[0, windows_total={windows_total}]"
        )
    if not (0.0 <= occupancy <= 1.0):
        raise TwinConfigShapeError(
            f"{entity}: observation.occupancy {occupancy} must be in [0.0, 1.0]"
        )
    ratio = windows_seen / windows_total
    if abs(occupancy - ratio) > OCCUPANCY_TOLERANCE:
        raise TwinConfigShapeError(
            f"{entity}: observation.occupancy {occupancy} disagrees with "
            f"windows_seen/windows_total ({windows_seen}/{windows_total} = "
            f"{ratio:.6f}) -- the counts are the carried occupancy's checksum "
            "(docs/WORK_ORDER_deploy_gate.md §3.2); fix the producer"
        )
    if obs.role_confidence is None:
        return
    if not role_confidence_allowed:
        # A segment has no ROLE, so the producer contract says the field is
        # absent there (`docs/WORK_ORDER_deploy_gate.md` §10.1/§11.1). Parsing
        # it and then ignoring it is the false-affordance shape this project
        # has retired four times (LES-172) -- reject it instead.
        raise TwinConfigShapeError(
            f"{entity}: observation.role_confidence is not applicable to a "
            "segment (a segment has no role, and nothing reads the field "
            "here) -- remove it"
        )
    if not (0.0 <= float(obs.role_confidence) <= 1.0):
        raise TwinConfigShapeError(
            f"{entity}: observation.role_confidence {obs.role_confidence} "
            "must be in [0.0, 1.0] (or absent)"
        )


# ---------------------------------------------------------------------------
# The predicate (work order §4.2)
# ---------------------------------------------------------------------------
def _fmt(value: float) -> str:
    """PROSE rendering (6 significant digits) -- readable, and never pasted
    back into a command."""
    return f"{value:g}"


def exact_param_value(value: float) -> str:
    """A threshold value a printed remedy can be PASTED BACK verbatim.

    `_fmt`'s `%g` would render `8/11` as `0.727273`, and re-running at
    `0.727273` still refuses the node: the gate's comparison epsilon is 1e-9
    while the occupancy checksum tolerates 1e-6, so a full-precision producer
    (`occupancy: 0.7272727272727273`) sits just below its own rounded
    remedy. `repr` is exact by construction -- `float(repr(x)) == x` for
    every finite float.
    """
    return repr(float(value))


@dataclass(frozen=True)
class GateVerdict:
    """Why one entity did not pass. `metric` names the clause that fired
    (`occupancy` | `role_confidence`) so a caller can offer the RIGHT remedy
    -- suggesting `--param DEPLOY_MIN_OCCUPANCY=...` to someone whose host was
    removed by the role-confidence clause is an actively wrong instruction."""

    metric: str
    reason: str


def gate_verdict(
    obs: Optional[TwinConfigObservation], thresholds: DeployThresholds,
    *, role_confidence_applies: bool,
) -> Optional[GateVerdict]:
    """`None` when the block passes the gate; otherwise the human reason it
    did not, ready to print. `obs is None` always passes -- no block means
    never gated out (work order §3.4), which is what keeps every shipped
    `twin-configs/` config and every compiler-invented entity emitted.

    Assumes `check_observation` has already run on `obs` (`_build_ir` calls
    it on every surviving entity first) -- this function reads the numbers,
    it does not re-validate them.

    `role_confidence_applies` is REQUIRED, not defaulted, because the answer
    differs by entity kind and getting it wrong is silent: a NODE's absent
    `role_confidence` is compared as 0.0 (§4.4 -- "only deploy hosts whose
    role I trust" excludes role-less hosts on purpose), but a SEGMENT has no
    role at all, so the clause is NOT APPLICABLE there. Applying it to
    segments made every observed segment fail the moment
    `DEPLOY_MIN_ROLE_CONFIDENCE` was raised above zero -- and then hard-error
    for being gated out from under its own emitted nodes.
    """
    if obs is None:
        return None
    occupancy = float(obs.occupancy)  # type: ignore[arg-type]
    if occupancy < thresholds.min_occupancy - GATE_EPSILON:
        return GateVerdict("occupancy", (
            f"occupancy {_fmt(occupancy)} (seen in {obs.windows_seen}/"
            f"{obs.windows_total} windows) is below "
            f"{PARAM_MIN_OCCUPANCY}={_fmt(thresholds.min_occupancy)}"
        ))
    if not role_confidence_applies:
        return None
    # An ABSENT role_confidence is not defaulted anywhere it is stored or
    # reported -- it is `None` end to end (segments and role-less hosts
    # legitimately have none). It is treated as 0.0 for THIS comparison only,
    # so a threshold above zero excludes role-less hosts: deliberate, and
    # said out loud in the reason, because "only deploy hosts whose role I
    # trust" is the only thing an operator setting this can mean (§4.4).
    confidence = 0.0 if obs.role_confidence is None else float(obs.role_confidence)
    if confidence < thresholds.min_role_confidence - GATE_EPSILON:
        absent = " (absent, compared as 0)" if obs.role_confidence is None else ""
        return GateVerdict("role_confidence", (
            f"role_confidence {_fmt(confidence)}{absent} is below "
            f"{PARAM_MIN_ROLE_CONFIDENCE}={_fmt(thresholds.min_role_confidence)}"
        ))
    return None


# ---------------------------------------------------------------------------
# The report (work order §4.5)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GatedEntity:
    """One entity the gate acted on. `kind`: `node` | `segment` | `conduit` |
    `router-leg` | `router` (the last is a KEPT router, never a removal)."""

    kind: str
    name: str
    reason: str
    # Which clause removed it (`occupancy` | `role_confidence`), or None for a
    # row the gate did not decide (a cascade-dropped segment/conduit).
    metric: Optional[str] = None
    occupancy: Optional[float] = None
    windows_seen: Optional[int] = None
    windows_total: Optional[int] = None
    role_confidence: Optional[float] = None
    # How many NODES this row accounts for -- a `count: 3` spec gates as ONE
    # unit but removes three hosts, and "3 node specs gated out" would hide
    # that (the operator cares how many VMs are not there).
    count: Optional[int] = None

    def line(self) -> str:
        """One UNPREFIXED report line; each surface adds its own prefix (the
        warnings channel a `deploy gate: `, `twin-config show` an indent)."""
        verb = "kept" if self.kind == "router" else "gated out"
        if self.kind in ("conduit", "router-leg"):
            verb = "dropped" if self.kind == "conduit" else "pruned"
        fanout = f" (count: {self.count})" if self.count and self.count > 1 else ""
        return f"{verb} {self.kind} {self.name!r}{fanout} -- {self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "reason": self.reason,
            "metric": self.metric,
            "occupancy": self.occupancy,
            "windows_seen": self.windows_seen,
            "windows_total": self.windows_total,
            "role_confidence": self.role_confidence,
            "count": self.count,
        }


@dataclass
class DeployGateReport:
    """What the gate did during one expansion. Produced by `expand._build_ir`
    on every expansion; `active` is False (and every list empty) for a config
    that carries no `observation:` block at all -- which is every shipped
    `twin-configs/` config, so their expanded IR, `ir_hash` and `plan_id` are
    byte-identical to before this feature existed."""

    config_id: str
    thresholds: DeployThresholds = field(default_factory=DeployThresholds)
    active: bool = False
    emitted_node_specs: int = 0
    emitted_nodes: int = 0
    emitted_segments: int = 0
    gated: list[GatedEntity] = field(default_factory=list)
    kept_routers: list[GatedEntity] = field(default_factory=list)

    # -- convenience views ------------------------------------------------
    def of_kind(self, kind: str) -> list[GatedEntity]:
        return [g for g in self.gated if g.kind == kind]

    @property
    def gated_nodes(self) -> list[GatedEntity]:
        return self.of_kind("node")

    @property
    def gated_segments(self) -> list[GatedEntity]:
        return self.of_kind("segment")

    @property
    def dropped_conduits(self) -> list[GatedEntity]:
        return self.of_kind("conduit")

    @property
    def pruned_router_legs(self) -> list[GatedEntity]:
        """Router legs the cascade removed because their segment left. Not a
        gating decision -- a consequence of one -- but it changes a real
        router's `nets:`, so it is reported, never silent (LES-089)."""
        return self.of_kind("router-leg")

    # -- rendering --------------------------------------------------------
    def summary_lines(self) -> list[str]:
        """Thresholds + counts. Printed whenever the gate RAN, even with
        nothing gated out: "0 gated out at 0.8" is a meaningful answer and
        its absence is not (work order §4.5)."""
        if not self.active:
            return []
        return [
            # "effective", because a `--param` override is invisible in the
            # `params:` block printed two lines above it in `twin-config show`.
            f"effective {PARAM_MIN_OCCUPANCY}={_fmt(self.thresholds.min_occupancy)}, "
            f"{PARAM_MIN_ROLE_CONFIDENCE}="
            f"{_fmt(self.thresholds.min_role_confidence)}",
            f"{len(self.gated_nodes)} node spec(s) "
            f"({sum(g.count or 1 for g in self.gated_nodes)} node(s)) and "
            f"{len(self.gated_segments)} segment(s) removed "
            f"({sum(1 for s in self.gated_segments if s.metric)} gated, "
            f"{sum(1 for s in self.gated_segments if not s.metric)} emptied), "
            f"{len(self.dropped_conduits)} conduit(s) dropped, "
            f"{len(self.pruned_router_legs)} router leg(s) pruned; "
            f"{self.emitted_node_specs} node spec(s) "
            f"({self.emitted_nodes} node(s)) and {self.emitted_segments} "
            "segment(s) emitted",
        ]

    def detail_lines(self) -> list[str]:
        """Every per-entity line, uncapped (`twin-config show`)."""
        return [g.line() for g in list(self.gated) + list(self.kept_routers)]

    def warning_lines(self, cap: int = WARNING_DETAIL_CAP) -> list[str]:
        """Summary + capped detail, prefixed, for the expander's `warnings`
        channel (`te validate|plan|deploy --config` prints these as
        `[warn] deploy gate: ...`; the API returns them in
        `config_warnings`)."""
        details = self.detail_lines()
        if not self.active and not details:
            return []
        lines = [*self.summary_lines(), *details[:cap]]
        if len(details) > cap:
            lines.append(
                f"... and {len(details) - cap} more; run 'axrange twin-config "
                f"show {self.config_id}' for the full table"
            )
        return [f"deploy gate: {line}" for line in lines]

    # -- recovery ---------------------------------------------------------
    def labels(self) -> dict[str, str]:
        """The `scenario.labels` entries recording the effective thresholds
        (work order §6). Emitted ONLY when the gate actually ran, which is
        what keeps a blockless config's `ir_hash` byte-identical."""
        if not self.active:
            return {}
        return {
            "deploy_min_occupancy": normalize_threshold(self.thresholds.min_occupancy),
            "deploy_min_role_confidence": normalize_threshold(
                self.thresholds.min_role_confidence
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "active": self.active,
            "min_occupancy": self.thresholds.min_occupancy,
            "min_role_confidence": self.thresholds.min_role_confidence,
            "emitted_node_specs": self.emitted_node_specs,
            "emitted_nodes": self.emitted_nodes,
            "emitted_segments": self.emitted_segments,
            "gated_out": [g.to_dict() for g in self.gated],
            "kept_routers": [g.to_dict() for g in self.kept_routers],
        }


# ---------------------------------------------------------------------------
# Threshold recovery (work order §6, D1 (c))
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeployGateRecord:
    """A CONFIRMED answer to "what threshold was this twin deployed at, and
    what did that leave out?" -- never a guess. Only ever constructed when a
    re-expansion of the config reproduces the deploy's recorded `ir_hash`
    byte for byte, which simultaneously proves the threshold AND makes the
    gated-out set exactly reproducible."""

    config_id: str
    ir_hash: str
    gate_active: bool
    min_occupancy: float
    min_role_confidence: float
    emitted_nodes: int
    gated_out: list[GatedEntity] = field(default_factory=list)
    kept_routers: list[GatedEntity] = field(default_factory=list)

    # No `to_dict()` here on purpose: this record has exactly one consumer,
    # the fidelity scorecard, which is a separate work order and will shape
    # its own payload. A serializer with no caller is the dead weight this
    # project keeps retiring (LES-172). `DeployGateReport.to_dict()` DOES
    # have one -- `axrange twin-config show --json`.


def deploy_gate_report(
    config_id: str, *,
    catalog_dir: CatalogDirArg = None,
    param_overrides: Optional[dict[str, str]] = None,
) -> DeployGateReport:
    """Expand `config_id` and return ONLY what its deploy gate did (the IR
    itself is discarded). Used by `axrange twin-config show` to preview a
    threshold -- `--param DEPLOY_MIN_OCCUPANCY=0.5` -- before deploying it,
    and by `confirm_deploy_gate` below.

    Raises the ordinary `TwinConfigError` family: a config that cannot be
    found or expanded has no gate to report."""
    # Function-local: `expand` imports THIS module at module level, so a
    # module-level import back would be a cycle. (`registry` is fine at module
    # level -- it imports neither.)
    from axrange.twinconfig.expand import expand_twin_config_raw_with_gate

    raw, _path = load_twin_config_raw(config_id, catalog_dir=catalog_dir)
    _ir, _params, report = expand_twin_config_raw_with_gate(
        raw, param_overrides=param_overrides,
    )
    return report


def confirm_deploy_gate(
    config_id: str, ir_hash: str, *,
    catalog_dir: CatalogDirArg = None,
    param_overrides: Optional[dict[str, str]] = None,
) -> Optional[DeployGateRecord]:
    """Re-expand `config_id` at a candidate threshold and CONFIRM it against
    a deploy's recorded `ir_hash` (work order §6; the fidelity scorecard's one
    entry point).

    Returns a `DeployGateRecord` only when the re-expansion hashes IDENTICALLY
    to `ir_hash` -- which proves both the threshold and the exact gated-out
    set. Returns `None` otherwise: the config drifted since deploy, the
    threshold guess is wrong, the config is gone, or it no longer expands.
    A caller must render that as `threshold: UNCONFIRMED`, never as a number
    nobody can stand behind (a `--param` override is persisted NOWHERE, so
    reading `params:` alone can never distinguish it from the declared
    default -- this is why confirmation, not recomputation, is the mechanism).

    NOTE the thresholds also join `ir_hash` through `scenario.labels`, so a
    twin deployed at 0.5 can never accidentally confirm at 0.8.
    """
    # Reuse the planner's OWN canonical hash rather than write a second one
    # that would have to be kept in agreement with it (see this module's
    # docstring for the boundary note). Function-local by intent.
    from axrange.planner.dag import hash_ir
    from axrange.twinconfig.expand import expand_twin_config_raw_with_gate

    try:
        # `warnings=[]`: a scorecard confirmation, not a load the operator
        # asked for -- a shadowing notice here would only be stderr noise.
        raw, _path = load_twin_config_raw(config_id, catalog_dir=catalog_dir, warnings=[])
        ir_dict, _params, report = expand_twin_config_raw_with_gate(
            raw, param_overrides=param_overrides,
        )
    except TwinConfigError:
        return None

    if hash_ir(ir_dict) != ir_hash:
        return None

    return DeployGateRecord(
        config_id=config_id,
        ir_hash=ir_hash,
        gate_active=report.active,
        min_occupancy=report.thresholds.min_occupancy,
        min_role_confidence=report.thresholds.min_role_confidence,
        emitted_nodes=report.emitted_nodes,
        gated_out=list(report.gated),
        kept_routers=list(report.kept_routers),
    )
