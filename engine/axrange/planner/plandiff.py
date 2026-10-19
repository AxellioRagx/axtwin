"""Plan diff — the deliberately REDUCED subset of `docs/PLANNER.md` §9
(re-planning) this build implements, per `docs/WORK_ORDER_
twin_incremental_update.md` §3.1. Full §9.2 (the redo-rules table) is NOT
implemented here — a `Mutated` classification is a hard refusal, never a
partial redo (see the work order for why: "a partially-correct redo table
is worse than none").

Pure, no I/O: given a freshly-built `Plan` and the persisted `ops` rows of
the standing deploy it is replacing, classify every op/barrier id into one
of `docs/PLANNER.md` §9.1's four classes (Unchanged / Added / Removed /
Mutated), plus a fifth `retry` bucket this reduced subset needs that §9.1's
table doesn't name: a row present in both but not `completed` (failed /
halted / stuck-running / pending) is explicitly NOT "Unchanged" — it must
still be retried by today's ordinary resume path (`StateStore.
reset_incomplete_ops`), and it is not eligible for the Mutated refusal
either (work order §3.1's table restricts Mutated, like Unchanged, to
`completed` rows — redoing something that never finished isn't dangerous
the way silently redoing something that DID is).

**The caller owns the safety property this depends on**: `new_plan` MUST be
built under the STANDING deploy's PINNED `plan_id`
(`build_plan(..., plan_id=standing.plan_id, ...)`), never a freshly-derived
one — see `dag.derive_plan_id`'s docstring and `docs/
WORK_ORDER_twin_incremental_update.md` §2.4/§3. Diffing against a plan built
under the wrong plan_id compares two disjoint op-id namespaces and
classifies everything as added/removed; this module has no way to detect
that mistake from inside a pure function, so it doesn't try.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from axrange.ops.registry import OpDefinition, list_actions_and_waits
from axrange.planner.dag import BarrierInstance, OpInstance, Plan
from axrange.planner.opview import op_fields
from axrange.planner.state import OpRow, OpStatus, op_to_row

# Sentinel for "this input key was absent on this side of the diff" —
# distinguishable from a legitimate resolved-input value of `None` (several
# op inputs are legitimately `None`, e.g. an unset `template_vmid`).
ABSENT = object()

# Fields that USED TO be persisted as part of an op's `inputs_resolved`
# (and so live in a pre-existing deploy's `inputs_resolved_json`) but are now
# carried planner-internally instead, never serialized at all (`OpInstance.
# planner_hints`, `dag.py`) -- `docs/WORK_ORDER_twin_incremental_update.md`
# M1's migration clause. `_diff_fields` tolerates ONLY the "present on the
# OLD (persisted) side, absent from the new (freshly-built) side" direction
# for these keys -- a deploy created BEFORE a field's migration (old row
# still carries the key) compares clean against a freshly-built op (which
# never carries it at all any more) without a false Mutated field. This is
# DELIBERATELY ASYMMETRIC (code review), not an unconditional both-sides
# skip: a key present on BOTH sides (e.g. re-introduced for a genuinely
# different purpose, or a bug that starts persisting it again) is compared
# NORMALLY and can still surface as Mutated -- an unconditional skip would
# make exactly that re-introduction invisible forever, defeating the point
# of keeping this set at all. Add a new key here ONLY when retiring a
# persisted input the same way `served_hosts` was retired, not casually --
# see `OpInstance.planner_hints`'s docstring (`dag.py`), which
# cross-references this set for exactly that reason.
_RETIRED_PERSISTED_INPUT_KEYS: frozenset[str] = frozenset({"served_hosts"})


def render_value(v: Any, *, max_len: int | None = None) -> str:
    """Stringify one side of a `PlanDiff.mutations` field-value pair for a
    refusal/diff message. `ABSENT` renders as `<unset>` rather than a
    misleadingly bare `None`.

    `max_len` (opt-in; unset = today's unbounded `repr`, so every
    pre-existing caller is byte-identical) swaps a value whose `repr` is
    longer than `max_len` for its `content_digest`. The refusal path passes
    it: now that `script_content` is a routinely-diffed field (the
    `--reapply` slice below), a `run-script` mutation that `--reapply`
    declines to cover would otherwise dump TWO entire shell scripts into
    stderr / an HTTP error body.

    Caveat: this is a LENGTH heuristic, not a content-sensitivity check — a
    value whose `repr` is `<= max_len` still prints VERBATIM, same as
    before this parameter existed. A short `script_content` (or any other
    short value) is not elided just because the field is sensitive."""
    if v is ABSENT:
        return "<unset>"
    out = repr(v)
    if max_len is not None and len(out) > max_len:
        return f"<{content_digest(v)}>"
    return out


def content_digest(v: Any) -> str:
    """`sha256:<12 hex> (<n> bytes)` for one side of a mutation — what the
    `--reapply` plan prints INSTEAD of the value itself, per option A's
    design (`docs/WORK_ORDER_responder_op_row_drift.md`). An operator shell
    script is both large and sometimes sensitive; the fingerprint is what
    makes "old -> new" verifiable without reproducing either. `ABSENT`
    renders as `<unset>`, matching `render_value`."""
    if v is ABSENT:
        return "<unset>"
    text = v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)
    raw = text.encode("utf-8", "replace")
    return f"sha256:{hashlib.sha256(raw).hexdigest()[:12]} ({len(raw)} bytes)"


@dataclass(frozen=True)
class PlanDiff:
    """The classification of every op/barrier id in a new `Plan` against a
    standing deploy's persisted `ops` rows.

    `unchanged` / `added` / `removed` / `mutated` / `retry` are a strict
    partition of `new_ids | persisted_ids` (every id lands in exactly one).
    `mutations[op_id]` holds `{field: (old_value, new_value)}` for each id in
    `mutated` — the "differing input keys with old->new values" `docs/
    WORK_ORDER_twin_incremental_update.md` §4.1 asks for; either side may be
    `ABSENT` (the key didn't exist on that side at all).

    `op_names`/`scopes` cover every id in `added | removed` (code review
    S7/S8) — an added id's op is always still IN `new_plan`, but a removed
    id's is not (its only remaining record is the persisted row this diff
    was built against), so a renderer that wants to group/label a removed
    entity (e.g. `cli.main`'s `--update` diff printer) needs this diff to
    have captured it rather than re-fetching the store itself.
    """

    unchanged: frozenset[str] = frozenset()
    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    mutated: frozenset[str] = frozenset()
    retry: frozenset[str] = frozenset()
    mutations: dict[str, dict[str, tuple[Any, Any]]] = field(default_factory=dict)
    op_names: dict[str, str] = field(default_factory=dict)
    scopes: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def is_noop(self) -> bool:
        """True when this diff executes and destroys nothing at all — an
        `--update` re-applied with no edits since the last apply (beyond
        ordinary retry of an already-`completed`-everywhere deploy)."""
        return not (self.added or self.removed or self.mutated or self.retry)


def _persisted_inputs(row: OpRow) -> dict[str, Any]:
    _name, _scope, inputs, _outputs = op_fields(row)
    return inputs


def _new_inputs(node: OpInstance | BarrierInstance | None) -> dict[str, Any]:
    """Round-trip `node`'s resolved inputs through the EXACT JSON
    serialization the state store persists (`state.op_to_row`), so the
    comparison against a persisted row's `inputs_resolved_json` isn't tripped
    up by a Python-only distinction JSON doesn't have (tuple vs list, etc.) —
    reuses the existing serializer instead of re-deriving its shape
    (LES-037). A `BarrierInstance` never carries resolved inputs
    (`state.barrier_to_row` always persists `"{}"`), so it always compares
    equal and can never classify as Mutated — `barriers_to_reevaluate` below
    is the barrier-specific concern this module handles separately."""
    if not isinstance(node, OpInstance):
        return {}
    row = op_to_row(node, deploy_id="__plandiff__")
    return json.loads(row.inputs_resolved_json)


def _diff_fields(old: dict[str, Any], new: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    out: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(old) | set(new)):
        # Asymmetric (code review, SHOULD-FIX 4): only the migration
        # direction -- persisted on the OLD row, absent from the freshly-
        # built op -- is tolerated. See `_RETIRED_PERSISTED_INPUT_KEYS`'s
        # own docstring for why this must NOT be an unconditional
        # both-sides skip.
        if key in _RETIRED_PERSISTED_INPUT_KEYS and key in old and key not in new:
            continue
        old_v = old.get(key, ABSENT)
        new_v = new.get(key, ABSENT)
        if old_v != new_v:
            out[key] = (old_v, new_v)
    return out


def diff_plan(new_plan: Plan, persisted_ops: Iterable[OpRow]) -> PlanDiff:
    """Classify every op/barrier id in `new_plan` against `persisted_ops`
    (typically `StateStore.ops_for_deploy(standing_deploy_id)`).

    Rows already `destroyed` (a prior `--update`'s removal sweep, or an
    ordinary `axrange destroy`) are excluded from the persisted set entirely
    (code review M2) — they are ALREADY swept and belong in no bucket. Left
    in, a `destroyed` row's op_id (absent from `new_plan`, same as it was
    the update that removed it) would be re-classified `removed` on EVERY
    later diff FOREVER, since nothing ever clears it from the `ops` table —
    measured: re-running an identical `--update` without `--allow-destroy`
    kept refusing "would remove N op(s)" indefinitely, and WITH the flag,
    re-issued real teardown calls against already-gone cluster resources on
    every re-run instead of the clean no-op §6(c) requires.
    """
    persisted: dict[str, OpRow] = {
        row.op_id: row for row in persisted_ops if row.status != OpStatus.DESTROYED
    }
    new_ids = set(new_plan.all_nodes().keys())
    old_ids = set(persisted.keys())

    added = new_ids - old_ids
    removed = old_ids - new_ids
    shared = new_ids & old_ids

    unchanged: set[str] = set()
    retry: set[str] = set()
    mutated: set[str] = set()
    mutations: dict[str, dict[str, tuple[Any, Any]]] = {}

    for op_id in shared:
        row = persisted[op_id]
        if row.status != OpStatus.COMPLETED:
            # Present on both sides but never finished — still retried by
            # today's resume path (`reset_incomplete_ops`); not eligible for
            # Unchanged OR the Mutated refusal (see module docstring).
            retry.add(op_id)
            continue
        node = new_plan.get(op_id)
        diff = _diff_fields(_persisted_inputs(row), _new_inputs(node))
        if diff:
            mutated.add(op_id)
            mutations[op_id] = diff
        else:
            unchanged.add(op_id)

    op_names: dict[str, str] = {}
    scopes: dict[str, dict[str, str]] = {}
    for op_id in added | removed:
        # An ADDED id's node is always still in `new_plan` (by construction,
        # `added = new_ids - old_ids`) and may be an `OpInstance` OR a
        # `BarrierInstance` -- `opview.op_fields` only duck-types `OpInstance`/
        # `OpRow` (its own docstring), never `BarrierInstance` (no `op_name`
        # attribute -- it's `barrier_name`), so branch locally here rather
        # than widen that shared helper for a shape none of ITS other
        # callers (all persisted-`OpRow` iteration) ever pass it. A REMOVED
        # id's node is always `None` (symmetric reasoning) -- op_fields
        # handles its `OpRow` fine.
        node = new_plan.get(op_id)
        if node is not None:
            name = node.op_name if isinstance(node, OpInstance) else node.barrier_name
            scope = dict(node.scope)
        else:
            name, scope, _inputs, _outputs = op_fields(persisted[op_id])
        op_names[op_id] = name
        scopes[op_id] = scope

    return PlanDiff(
        unchanged=frozenset(unchanged),
        added=frozenset(added),
        removed=frozenset(removed),
        mutated=frozenset(mutated),
        retry=frozenset(retry),
        mutations=mutations,
        op_names=op_names,
        scopes=scopes,
    )


def barriers_to_reevaluate(new_plan: Plan, added: Iterable[str]) -> frozenset[str]:
    """Barrier op_ids (work order §3.3) that must be re-evaluated even though
    their persisted row may already be `completed`: a barrier present in
    `new_plan.barriers` (i.e. NOT itself in `added` — a brand-new barrier is
    already handled by the ordinary Added path) whose new-DAG predecessor
    set includes at least one `added` op_id.

    We cannot compare against the OLD plan's edges directly — the state
    store persists op ROWS, never a graph (there is no `edges` table) — so
    this only catches the dangerous direction, "gained a predecessor that
    hasn't run yet." A barrier that merely LOST a predecessor (e.g. the last
    node on a removed segment) stays correctly satisfied by the subset that
    already ran; leaving it `completed` is safe, so it is not returned here.

    The caller force-resets the returned ids back to `pending`
    (`StateStore.force_reset_ops`) BEFORE running the executor, so the
    resume short-circuit doesn't skip a barrier whose completion no longer
    means what it used to (`docs/WORK_ORDER_twin_incremental_update.md`
    §3.3: "a stale-satisfied barrier is how a new node gets released before
    its network is ready")."""
    new_plan.freeze()  # idempotent; guarantees predecessors() is populated
    added_set = set(added)
    out: set[str] = set()
    for barrier_id in new_plan.barriers:
        if barrier_id in added_set:
            continue
        if new_plan.predecessors(barrier_id) & added_set:
            out.add(barrier_id)
    return frozenset(out)


def _has_bare_star_after(op_def: OpDefinition) -> bool:
    """True when `op_def`'s catalog `after:` tuple contains a bare
    `<name>[*]` pattern (docs/OP_CATALOG.md §5; `pass3_ordering.
    _resolve_pattern`'s `scope_spec == "*"` branch): "depends on EVERY
    instance of that producer type currently in the plan, regardless of
    scope" — the catalog-encoded signal that this op's own REAL effect
    spans the whole matching set, not merely its own scope's slice of it
    (e.g. `apply-sdn-config`'s `PUT /cluster/sdn` commits every staged
    zone/bridge/vnet/subnet in the plan; `create-sdn-zone` similarly
    references "the" underlay bridge via a bare `create-ovs-bridge[*]`).

    Deliberately narrower than the far more common `[$var@*]` shape (e.g.
    `start-vm[$host]`'s `attach-vm-to-vnet[$host@*]`, `run-script`'s
    `provision-app[$app@*]`): that pattern ties the fan-in to the SAME
    entity as the consumer — ordinary sequencing, not a cross-entity
    commit — and critically covers non-idempotent ops (`run-script`,
    `CleanupStrategy.ESCALATE`) that must never be silently force-
    re-executed just because an unrelated peer op landed elsewhere in the
    plan. `.endswith("[*]")` (not `"*" in pattern` or a regex) is the
    precise test: `"name[$var@*]".endswith("[*]")` is False (it ends in
    `"@*]"`), so a bound-star-tail pattern is never mistaken for a bare
    one."""
    return any(pattern.strip().endswith("[*]") for pattern in op_def.after)


def _ops_with_bare_star_after() -> frozenset[str]:
    """Every catalog op NAME whose `after:` clause has >=1 bare-star
    pattern (`_has_bare_star_after`) — computed from `list_actions_and_
    waits()` (never hand-maintained), so a FUTURE catalog op that grows
    this shape is automatically picked up by `ops_to_reexecute` below
    instead of needing a second special-case. `tests/test_plandiff.py`'s
    `test_bare_star_after_pattern_ops_matches_expected_catalog_set` pins
    today's set (`create-sdn-zone`, `apply-sdn-config`, `verify-capture`)
    so a change here — deliberate or a catalog regression — is a reviewed
    diff, not a silent scope change to what gets force-re-executed."""
    return frozenset(
        op_def.name
        for op_def in list_actions_and_waits()
        if _has_bare_star_after(op_def)
    )


def ops_to_reexecute(new_plan: Plan, added: Iterable[str]) -> frozenset[str]:
    """Non-barrier op_ids (work order §3.3's rule, extended past barriers —
    code review MUST-FIX 1) that must be re-executed even though their
    persisted row may already be `completed`: an op present in `new_plan.
    ops` (i.e. NOT itself in `added` — a brand-new op is already handled by
    the ordinary Added path) whose catalog definition has a bare-star
    `after:` pattern (`_ops_with_bare_star_after`) AND whose new-DAG
    predecessor set includes at least one `added` op_id.

    Why this exists: `diff_plan` classifies purely on an op's OWN
    `inputs_resolved` fingerprint. An aggregate/"commit" op like
    `apply-sdn-config` (`PUT /cluster/sdn`) takes no input that enumerates
    what it commits — its only input is `zone_id` — so adding a new
    `create-vnet`/`create-subnet` elsewhere in the plan never changes its
    fingerprint and `diff_plan` classifies it Unchanged forever, even
    though its REAL effect (commit every staged SDN object) now needs to
    run again. Two measured failure shapes this fixes: (1) LOUD — a new
    network's vnet stays forever staged-but-uncommitted, so the new host's
    `start-vm` fails "bridge does not exist"; (2) SILENT — flipping
    `egress: true` on a network that already has a `gateway:` adds only
    `create-subnet[$net]`; every op still reports COMPLETED and the deploy
    is green, but the segment gets no gateway and no SNAT.

    Restricted to the bare-star shape (not "any op that gained a
    predecessor") on purpose — see `_has_bare_star_after`'s docstring for
    why the far-more-common `[$var@*]` shape must NOT be swept in here.
    Every op this function can flag today is `IdempotencyClass.INFRA`/
    `CONFIG` with `CleanupStrategy.REDO` in the catalog (`apply-sdn-config`,
    `create-sdn-zone`, `verify-capture`) — re-running is the documented
    safe/idempotent case, matching `barriers_to_reevaluate`'s own "barriers
    are cheap" framing.

    Mirrors `barriers_to_reevaluate`'s exact shape and caller contract: the
    caller force-resets the returned ids back to `pending` (`StateStore.
    force_reset_ops`) BEFORE running the executor, and re-running one of
    these ops is a normal, idempotent apply-and-wait, not a redo of
    something dangerous."""
    new_plan.freeze()  # idempotent; guarantees predecessors() is populated
    added_set = set(added)
    bare_star_names = _ops_with_bare_star_after()
    out: set[str] = set()
    for op_id, op in new_plan.ops.items():
        if op_id in added_set:
            continue
        if op.op_name not in bare_star_names:
            continue
        if new_plan.predecessors(op_id) & added_set:
            out.add(op_id)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Content-only re-apply -- `docs/WORK_ORDER_responder_op_row_drift.md` option
# A (Authority: **user**, 2026-09-02). ONE row of `docs/PLANNER.md` §9.2's
# otherwise-deferred redo-rules table (`apps[*].install` -> "redo affected
# install action ops"), built for exactly ONE op type, behind an opt-in flag
# (`te deploy --update --reapply`). Everything NOT matched here stays the
# hard Mutated refusal §3.1 has always been -- the narrowness IS the safety
# argument, so widen this predicate only with the same deliberation the work
# order's §4 decision took.
# ---------------------------------------------------------------------------

#: The only op a `--reapply` may redo, and the only differing resolved-input
#: field that qualifies it. `run-script` is `CleanupStrategy.ESCALATE` /
#: `IdempotencyClass.CONFIG` (`ops/registry.py`) -- opaque, non-idempotent
#: operator shell -- so the safety argument is NOT "re-running this is
#: idempotent". It is: the operator opted in per-invocation, was shown the
#: exact op ids and old->new content fingerprints, and confirmed
#: (`cli.main._print_update_diff`'s re-apply section + the existing
#: `--yes`/prompt gate). The blast radius is whatever the script does.
#:
#: Matched by op NAME, not by the catalog id (`OP-APP-030`) that CLAUDE.md
#: calls the documented stable handle ("never renumber an OP-<CAT>-<NNN>")
#: -- code review (2026-09-02) flagged the mismatch. Kept as a name because
#: it is the SAME idiom `_ops_with_bare_star_after` above already uses (both
#: match `OpInstance.op_name` straight off the freshly-built `Plan`, never a
#: persisted row), and because switching would also mean widening the
#: shared test fixture `tests/test_plandiff.py::_op` off its placeholder
#: `op_catalog_id="OP-TEST-000"` for every op it constructs (used well
#: beyond this predicate's own tests) -- a bigger, riskier change than this
#: single-op predicate needs. The safety net instead is a pin:
#: `tests/test_plandiff.py::test_reapply_op_name_matches_the_documented_
#: catalog_id` asserts `REAPPLY_OP_NAME` resolves to `OP-APP-030` via the
#: real op registry, so a future rename OR renumber of `run-script` fails
#: loudly here rather than silently un-matching.
REAPPLY_OP_NAME = "run-script"
REAPPLY_FIELDS: frozenset[str] = frozenset({"script_content"})


def reapply_ineligibility_reason(
    op_name: str, changed_fields: Iterable[str]
) -> str | None:
    """Why `--reapply` does NOT cover a mutated op -- or `None` when it does.

    The single source of truth for the eligibility rule: `reapplyable_ops`
    below is defined in terms of this function, so the predicate and the
    refusal message can never drift apart (a refusal naming a reason the
    predicate doesn't actually test is worse than no reason at all).

    `provision-app` is DELIBERATELY out of scope for this slice, even though
    it is the `pkg:` sibling with the same `redo_on=("apps[*].install",)`
    catalog trigger and the same "content changed" shape. The work order
    (§4 option A, "Against") names it as the one thing that is "not obviously
    in or out"; the decision recorded here is **out, for now**. Re-running a
    `provision-app` re-runs an Ansible play whose delivered action SET may
    itself have changed (a dropped `pkg:` is not uninstalled by re-running
    the play without it), so "re-execute it and the guest converges" is not
    true for it the way it is for a self-contained, re-runnable script. That
    is its own decision with its own blast radius, not a widening of this one.
    """
    fields = frozenset(changed_fields)
    if op_name != REAPPLY_OP_NAME:
        return (
            f"--reapply covers only {REAPPLY_OP_NAME!r} content changes; "
            f"{op_name!r} is out of scope for this slice "
            "(docs/WORK_ORDER_responder_op_row_drift.md §4 option A)"
        )
    if fields != REAPPLY_FIELDS:
        return (
            f"--reapply covers a change to {sorted(REAPPLY_FIELDS)} ALONE; "
            f"this op's differing field(s) are {sorted(fields)}"
        )
    return None


def reapplyable_ops(new_plan: Plan, diff: PlanDiff) -> frozenset[str]:
    """The subset of `diff.mutated` that `te deploy --update --reapply` may
    re-execute in place: an op whose name is `REAPPLY_OP_NAME` AND whose set
    of differing resolved-input fields is EXACTLY `REAPPLY_FIELDS`
    (`reapply_ineligibility_reason` above is the actual test).

    Pure, no I/O -- like everything else in this module. The caller
    (`api.service.resolve_deploy_plan`) refuses on `diff.mutated - <this>`
    exactly as it refuses on ALL of `diff.mutated` without the flag, then
    force-resets the returned ids to `pending` (`StateStore.force_reset_ops`)
    so the resume short-circuit doesn't skip them, and lets the ORDINARY
    executor run them -- same DAG, same `after:` edges, same config-exec/QGA
    delivery path. Nothing here relaxes ordering: a re-applied
    `run-script[$app@$host]` still sits behind its `host-ready[$host]` /
    `provision-app[$app@*]` predecessors (already `completed` on a standing
    twin, so in practice it runs immediately -- but the edges are the plan's,
    not this function's, to relax).

    Mirrors `barriers_to_reevaluate`/`ops_to_reexecute`'s shape and caller
    contract deliberately -- with the difference those two docstrings lean
    on: they force-re-execute ops that are documented-SAFE to redo
    (`REDO`/idempotent). This one force-re-executes an op that is NOT, which
    is exactly why it is opt-in, confirmed, and this narrow.

    Sibling tiers need no reconciliation for a re-apply: a `tap`/`tfc`/`exr`
    deploy binds to a HOST (its vmid / role / interfaces), none of which a
    content-only script change touches -- unlike `--update`'s removal half,
    which really can leave a sibling pointing at a deleted host
    (`api.service._dangling_sibling_warnings`, `docs/WORK_ORDER_twin_
    incremental_update.md` §3.5).
    """
    out: set[str] = set()
    for op_id in diff.mutated:
        node = new_plan.get(op_id)
        if not isinstance(node, OpInstance):
            # A barrier never carries resolved inputs, so it can never BE
            # Mutated (`_new_inputs`) -- explicit rather than assumed.
            continue
        reason = reapply_ineligibility_reason(
            node.op_name, diff.mutations.get(op_id, {})
        )
        if reason is None:
            out.add(op_id)
    return frozenset(out)
