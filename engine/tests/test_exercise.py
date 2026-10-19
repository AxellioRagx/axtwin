"""Tests for `axrange.o2control.exercise` (the Exercise tier, `docs/
WORK_ORDER_exercise_tier.md`).

No live cluster: every apply/destroy test drives a real `StateStore` (tmp
SQLite file) with hand-seeded op rows for a synthetic twin (the same "derive
purely from state.db" posture `test_traffic_tier.py`/`test_o2_taps.py` use)
plus a FAKE config backend, run through the REAL `Executor` (via
`apply_exercise` -> `run_deploy_executor`).
"""
from __future__ import annotations

import json

import pytest

from axrange.o2control import (
    ExercisePlanError,
    apply_exercise,
    cascade_destroy_exercises_for_te,
    compose_exercise_ops,
    destroy_exercise,
    exercise_status,
    parse_exercise_spec,
    playout_hash,
    resolve_exercise_bindings,
)
from axrange.o2control.exercise import ExerciseSpec
from axrange.planner import StateStore
from axrange.planner.state import OpRow, OpStatus


# ---------------------------------------------------------------------------
# Seeding helpers -- a synthetic twin (`te`-tier deploy), same idiom as
# test_traffic_tier.py._seed_twin.
# ---------------------------------------------------------------------------
def _op_row(deploy_id, op_id, op_name, op_catalog_id, scope, *,
            inputs=None, outputs=None, status=OpStatus.COMPLETED):
    return OpRow(
        op_id=op_id, deploy_id=deploy_id, op_name=op_name,
        op_catalog_id=op_catalog_id, kind="action",
        scope_json=json.dumps(scope, sort_keys=True),
        inputs_resolved_json=json.dumps(inputs or {}, sort_keys=True, default=str),
        inputs_from_outputs_json="{}",
        outputs_json=(
            json.dumps(outputs, sort_keys=True, default=str)
            if outputs is not None else None
        ),
        cleanup_strategy="redo", idempotency_class="infra",
        status=status, started_at=0.0, completed_at=0.0,
    )


TWIN_DEPLOY = "te-twin-exr-1"
# `os` is intentionally NOT wired here (no `manifests=` passed to
# `build_twin_inventory` in these tests) -- `HostInfo.os` resolves via an
# image_id -> manifest lookup only when `manifests` is supplied
# (`axrange.planner.inventory._os_family_lookup`), so every host resolves to
# the "linux" default in this file's fan-out/compose tests (os_family
# realism is exercised elsewhere, e.g. `test_default_agents.py`'s Windows
# `install-defense-agent` tests).
_HOSTS = [
    {"host_id": "attacker", "vmid": 200001},
    {"host_id": "ws01", "vmid": 200002},
    {"host_id": "ws02", "vmid": 200003},
]


def _seed_twin(store, *, deploy_id=TWIN_DEPLOY, hosts=None, tier="te"):
    hosts = hosts if hosts is not None else _HOSTS
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="twin-scn", scenario_version=1,
        scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
        placement={h["host_id"]: "pxtest1" for h in hosts}, tier=tier,
    )
    rows = []
    for h in hosts:
        rows.append(_op_row(
            deploy_id, f"op-clone-{h['host_id']}", "clone-vm", "OP-HOST-010",
            {"host": h["host_id"]},
            inputs={"target_node": "pxtest1", "image_id": h.get("image_id")},
            outputs={"vmid": h["vmid"]},
        ))
        rows.append(_op_row(
            deploy_id, f"op-attach-{h['host_id']}", "attach-vm-to-vnet",
            "OP-HOST-040", {"host": h["host_id"], "net": "lan"},
            inputs={"net_id": "lan", "ip": "10.0.0.1", "nic_index": 0},
        ))
    store.insert_ops(deploy_id, rows)
    return deploy_id


def _seeded_db(tmp_path, *, hosts=None, deploy_id=TWIN_DEPLOY, tier="te"):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_twin(store, deploy_id=deploy_id, hosts=hosts, tier=tier)
    finally:
        store.close()
    return db


class _FakeConfigBackend:
    """Records every op it runs; used as `config_backend=` for
    `apply_exercise` -- same idiom as `test_traffic_tier.py`'s
    `_FakePersonaBackend`."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == "deploy-behavior-agent":
            return {"agent_deployed": True}
        if op.op_name == "start-activity":
            return {"activity_started": True}
        if op.op_name == "wait-actor-ready":
            return {"actor_ready": True}
        if op.op_name == "install-agent":
            return {"agent_installed": True}
        raise AssertionError(f"unexpected op {op.op_name}")  # pragma: no cover


_STEPS = [{"tool": "nmap", "args": "-sV", "target": "10.0.0.0/24", "technique": "T1595"}]


def _activity_raw(**overrides):
    base = {"id": "ad-kill-chain", "host": "attacker", "inline": {"steps": _STEPS}}
    base.update(overrides)
    return base


def _adversary_raw(**overrides):
    base = {
        "id": "red-sandcat", "targets": ["ws01", "ws02"],
        "server": "http://caldera.o2.lab:8888",
    }
    base.update(overrides)
    return base


def _spec_raw(*, activity=True, adversary=False, twin=TWIN_DEPLOY, **extra):
    attack = {}
    if activity:
        attack["activity"] = _activity_raw()
    if adversary:
        attack["adversary"] = _adversary_raw()
    body = {"exercise": {"twin": twin, "attack": attack, **extra}}
    return body


# ---------------------------------------------------------------------------
# parse_exercise_spec
# ---------------------------------------------------------------------------
def test_parse_minimal_activity_spec():
    spec = parse_exercise_spec(_spec_raw())
    assert spec.twin == TWIN_DEPLOY
    assert spec.activity is not None
    assert spec.activity.host == "attacker"
    assert spec.activity.activity.id == "ad-kill-chain"
    assert spec.adversary is None


def test_parse_requires_top_level_key():
    with pytest.raises(ExercisePlanError, match="top-level 'exercise:'"):
        parse_exercise_spec({"nope": {}})


def test_parse_twin_is_optional():
    """`twin:` is OPTIONAL since 2026-08-04 (name-targeting parity with
    tfc/tap, `docs/WORK_ORDER_corp_enterprise_bundle.md` §4) -- a spec may omit
    it and rely on a `--target`/API-body override, so it can be a reusable
    library file. It used to be a hard parse error."""
    body = _spec_raw()
    del body["exercise"]["twin"]
    spec = parse_exercise_spec(body)
    assert spec.twin is None


def test_parse_twin_present_but_malformed_still_errors():
    body = _spec_raw(twin="")
    with pytest.raises(ExercisePlanError, match="non-empty string"):
        parse_exercise_spec(body)


def test_parse_requires_at_least_one_attack_mode():
    body = _spec_raw(activity=False, adversary=False)
    with pytest.raises(ExercisePlanError, match="activity.*adversary"):
        parse_exercise_spec(body)


def test_parse_activity_requires_host():
    body = _spec_raw()
    del body["exercise"]["attack"]["activity"]["host"]
    with pytest.raises(ExercisePlanError, match="host"):
        parse_exercise_spec(body)


def test_parse_activity_requires_id():
    body = _spec_raw()
    del body["exercise"]["attack"]["activity"]["id"]
    with pytest.raises(ExercisePlanError, match="id is required"):
        parse_exercise_spec(body)


def test_parse_activity_rejects_behavior_ref():
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["behavior"] = "office-workday@v2"
    del body["exercise"]["attack"]["activity"]["inline"]
    with pytest.raises(ExercisePlanError, match="behavior"):
        parse_exercise_spec(body)


def test_parse_activity_rejects_overrides():
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["overrides"] = {"x": 1}
    with pytest.raises(ExercisePlanError, match="overrides"):
        parse_exercise_spec(body)


def test_parse_activity_requires_inline_steps():
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["inline"] = {"steps": []}
    with pytest.raises(ExercisePlanError, match="inline.steps"):
        parse_exercise_spec(body)


def test_parse_activity_interval_missing_max_raises():
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["schedule"] = {"interval": {"min": "30s"}}
    with pytest.raises(ExercisePlanError, match="interval"):
        parse_exercise_spec(body)


def test_parse_activity_interval_with_once_raises():
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["schedule"] = {
        "once": True, "interval": {"min": "30s", "max": "5m"},
    }
    with pytest.raises(ExercisePlanError, match="mutually exclusive"):
        parse_exercise_spec(body)


def test_parse_adversary_requires_id():
    body = _spec_raw(activity=False, adversary=True)
    del body["exercise"]["attack"]["adversary"]["id"]
    with pytest.raises(ExercisePlanError, match="id is required"):
        parse_exercise_spec(body)


def test_parse_adversary_requires_targets():
    body = _spec_raw(activity=False, adversary=True)
    del body["exercise"]["attack"]["adversary"]["targets"]
    with pytest.raises(ExercisePlanError, match="targets"):
        parse_exercise_spec(body)


def test_parse_adversary_requires_server():
    body = _spec_raw(activity=False, adversary=True)
    del body["exercise"]["attack"]["adversary"]["server"]
    with pytest.raises(ExercisePlanError, match="server"):
        parse_exercise_spec(body)


def test_parse_defense_and_measurement_carried_through():
    body = _spec_raw(
        defense=[{"note": "tuned wazuh rule 9000006"}],
        measurement={"run_label": "run1", "techniques": ["T1595"]},
        seed=7,
    )
    spec = parse_exercise_spec(body)
    assert spec.defense_notes == [{"note": "tuned wazuh rule 9000006"}]
    assert spec.measurement == {"run_label": "run1", "techniques": ["T1595"]}
    assert spec.seed == 7
    # S1 (code review): the top-level seed is not a silent no-op -- it folds
    # into attack.activity.seed (unset in _activity_raw()'s default body).
    assert spec.activity.activity.seed == 7


# ---------------------------------------------------------------------------
# S1 (code review) -- top-level `exercise.seed` must never be a silent no-op:
# fold into attack.activity.seed when unset, fail loud on a real conflict or
# when there is no attack.activity at all to apply it to.
# ---------------------------------------------------------------------------
def test_parse_top_level_seed_falls_back_into_unset_activity_seed():
    body = _spec_raw(seed=99)
    spec = parse_exercise_spec(body)
    assert spec.activity.activity.seed == 99


def test_parse_top_level_seed_matching_explicit_activity_seed_is_a_noop():
    body = _spec_raw(seed=5)
    body["exercise"]["attack"]["activity"]["seed"] = 5
    spec = parse_exercise_spec(body)
    assert spec.activity.activity.seed == 5


def test_parse_top_level_seed_conflicting_with_explicit_activity_seed_raises():
    body = _spec_raw(seed=1)
    body["exercise"]["attack"]["activity"]["seed"] = 2
    with pytest.raises(ExercisePlanError, match="DIFFERENT values"):
        parse_exercise_spec(body)


def test_parse_top_level_seed_without_activity_raises():
    body = _spec_raw(activity=False, adversary=True, seed=7)
    with pytest.raises(ExercisePlanError, match="no attack.activity"):
        parse_exercise_spec(body)


def test_playout_hash_reflects_top_level_seed_fallback(tmp_path):
    """The determinism-tier bug this closes: two specs differing ONLY in
    `exercise.seed` (attack.activity.seed left unset in both) must now hash
    DIFFERENTLY -- before the fix, the top-level seed was inert and BOTH
    would silently hash identically, mislabeling two genuinely different
    seeded runs as an "identical playout" replay."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        body1 = _spec_raw(seed=1)
        resolution1 = resolve_exercise_bindings(parse_exercise_spec(body1), store)
        body2 = _spec_raw(seed=2)
        resolution2 = resolve_exercise_bindings(parse_exercise_spec(body2), store)
    finally:
        store.close()
    plan1 = compose_exercise_ops(resolution1, "exr-seed-fallback")
    plan2 = compose_exercise_ops(resolution2, "exr-seed-fallback")
    assert playout_hash(plan1) != playout_hash(plan2)


# ---------------------------------------------------------------------------
# resolve_exercise_bindings
# ---------------------------------------------------------------------------
def test_resolve_unknown_twin_raises_404(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = ExerciseSpec(twin="nope", activity=None, adversary=None)
        with pytest.raises(ExercisePlanError) as exc:
            resolve_exercise_bindings(spec, store)
        assert exc.value.status == 404
    finally:
        store.close()


def test_resolve_wrong_tier_rejected(tmp_path):
    db = _seeded_db(tmp_path, deploy_id="tfc-not-a-twin", tier="tfc")
    store = StateStore(db)
    try:
        spec = ExerciseSpec(twin="tfc-not-a-twin", activity=None, adversary=None)
        with pytest.raises(ExercisePlanError, match="must be a target-environment"):
            resolve_exercise_bindings(spec, store)
    finally:
        store.close()


def test_resolve_unknown_activity_host_rejected(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw())
        spec.activity.host = "nope"
        with pytest.raises(ExercisePlanError, match="nope"):
            resolve_exercise_bindings(spec, store)
    finally:
        store.close()


def test_resolve_unknown_adversary_target_rejected(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        spec.adversary.entry.targets = ["ws01", "nope"]
        with pytest.raises(ExercisePlanError, match="nope"):
            resolve_exercise_bindings(spec, store)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# compose_exercise_ops
# ---------------------------------------------------------------------------
def test_compose_activity_trio_ordered(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw())
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan = compose_exercise_ops(resolution, "exr-test-1")
    names = {o.op_name for o in plan.ops.values()}
    assert names == {"deploy-behavior-agent", "start-activity", "wait-actor-ready"}

    def _op(name):
        return next(o for o in plan.ops.values() if o.op_name == name)

    deploy_op, start_op, wait_op = _op("deploy-behavior-agent"), _op("start-activity"), _op("wait-actor-ready")
    assert deploy_op.op_id in plan.predecessors(start_op.op_id)
    assert start_op.op_id in plan.predecessors(wait_op.op_id)
    assert deploy_op.inputs_resolved["inline_steps"] == _STEPS
    assert deploy_op.inputs_resolved["vmid"] == 200001


def test_compose_adversary_fan_out_two_targets(tmp_path):
    """LES-034: two targets => two install-agent ops, not a last-wins single one."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan = compose_exercise_ops(resolution, "exr-test-2")
    ops = [o for o in plan.ops.values() if o.op_name == "install-agent"]
    assert len(ops) == 2
    hosts = {o.inputs_resolved["host_id"] for o in ops}
    assert hosts == {"ws01", "ws02"}
    for o in ops:
        assert o.inputs_resolved["agent_kind"] == "adversary"
        assert o.inputs_resolved["server"] == "http://caldera.o2.lab:8888"
        assert o.inputs_resolved["os_family"] == "linux"  # unresolved default, see _HOSTS note


def test_compose_both_activity_and_adversary(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw(activity=True, adversary=True))
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan = compose_exercise_ops(resolution, "exr-test-3")
    names = {o.op_name for o in plan.ops.values()}
    assert "deploy-behavior-agent" in names
    assert len([o for o in plan.ops.values() if o.op_name == "install-agent"]) == 2


def test_compose_activity_threads_jittered_schedule_and_seed_into_every_op(tmp_path):
    """The resolved schedule + seed (§ "ported here since that function no
    longer exists" in the module docstring, `_schedule_to_dict`) must survive
    onto ALL THREE ops' inputs -- deploy-behavior-agent/start-activity/
    wait-actor-ready alike -- not just the deploy op (this porting was the
    highest-risk part of moving `_emit_activity`'s logic by hand into this
    module; `pass1_emit`'s own equivalent coverage,
    `test_emit_activity_threads_jittered_schedule_and_seed`, was deleted with
    the te-side emission, so this is the ONLY place that shape is still
    checked)."""
    db = _seeded_db(tmp_path)
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["schedule"] = {
        "interval": {"min": "30s", "max": "5m"},
        "days": ["Mon"], "start": "09:00", "end": "17:00",
    }
    body["exercise"]["attack"]["activity"]["seed"] = 1337
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(body)
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan = compose_exercise_ops(resolution, "exr-test-sched")

    for op_name in ("deploy-behavior-agent", "start-activity", "wait-actor-ready"):
        op = next(o for o in plan.ops.values() if o.op_name == op_name)
        assert op.inputs_resolved["schedule"]["interval"] == {"min": "30s", "max": "5m"}
        assert op.inputs_resolved["schedule"]["days"] == ["Mon"]
        assert op.inputs_resolved["schedule"]["start"] == "09:00"
        assert op.inputs_resolved["schedule"]["end"] == "17:00"
        assert op.inputs_resolved["seed"] == 1337


def test_compose_activity_no_schedule_omits_schedule_input(tmp_path):
    """The un-scheduled default spec must not thread a `schedule` input at
    all -- mirrors the deleted pass1_emit test's assertion for this module's
    own hand-ported `_schedule_to_dict`."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw())
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan = compose_exercise_ops(resolution, "exr-test-nosched")
    deploy_op = next(o for o in plan.ops.values() if o.op_name == "deploy-behavior-agent")
    assert "schedule" not in deploy_op.inputs_resolved
    assert "seed" not in deploy_op.inputs_resolved


# ---------------------------------------------------------------------------
# playout_hash -- replay determinism
# ---------------------------------------------------------------------------
def test_playout_hash_identical_for_same_spec_different_plan_ids(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = parse_exercise_spec(_spec_raw())
        resolution = resolve_exercise_bindings(spec, store)
    finally:
        store.close()
    plan_a = compose_exercise_ops(resolution, "exr-twin-aaaaaaaa")
    plan_b = compose_exercise_ops(resolution, "exr-twin-bbbbbbbb")
    assert playout_hash(plan_a) == playout_hash(plan_b)


def test_playout_hash_differs_for_different_seed(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec1 = parse_exercise_spec(_spec_raw())
        spec1.activity.activity.seed = 1
        resolution1 = resolve_exercise_bindings(spec1, store)
        spec2 = parse_exercise_spec(_spec_raw())
        spec2.activity.activity.seed = 2
        resolution2 = resolve_exercise_bindings(spec2, store)
    finally:
        store.close()
    plan1 = compose_exercise_ops(resolution1, "exr-x")
    plan2 = compose_exercise_ops(resolution2, "exr-x")
    assert playout_hash(plan1) != playout_hash(plan2)


def test_playout_hash_differs_for_different_targets(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec1 = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        resolution1 = resolve_exercise_bindings(spec1, store)
        spec2 = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        spec2.adversary.entry.targets = ["ws01"]
        resolution2 = resolve_exercise_bindings(spec2, store)
    finally:
        store.close()
    plan1 = compose_exercise_ops(resolution1, "exr-y")
    plan2 = compose_exercise_ops(resolution2, "exr-y")
    assert playout_hash(plan1) != playout_hash(plan2)


def test_playout_hash_is_independent_of_adversary_target_order(tmp_path):
    """Nice-to-have (code review): reordering `attack.adversary.targets` for
    an otherwise-IDENTICAL effective target SET must hash identically --
    before the fix, `agent_entry.model_dump()` embedded the full ordered
    `targets` list per-op and JSON preserves order, so a reorder alone
    changed the hash despite the playout being effectively unchanged."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec1 = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        spec1.adversary.entry.targets = ["ws01", "ws02"]
        resolution1 = resolve_exercise_bindings(spec1, store)
        spec2 = parse_exercise_spec(_spec_raw(activity=False, adversary=True))
        spec2.adversary.entry.targets = ["ws02", "ws01"]
        resolution2 = resolve_exercise_bindings(spec2, store)
    finally:
        store.close()
    plan1 = compose_exercise_ops(resolution1, "exr-z")
    plan2 = compose_exercise_ops(resolution2, "exr-z")
    assert playout_hash(plan1) == playout_hash(plan2)


# ---------------------------------------------------------------------------
# apply_exercise -- always a FRESH deploy_id (ephemeral); replay = identical
# playout_hash across two independent applies.
# ---------------------------------------------------------------------------
def test_apply_exercise_runs_activity_trio_and_completes(tmp_path):
    db = _seeded_db(tmp_path)
    backend = _FakeConfigBackend()
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=backend,
    )
    assert status.status == "completed"
    assert status.twin == TWIN_DEPLOY
    assert status.activity_host == "attacker"
    op_names = [name for name, _ in backend.calls]
    assert op_names == ["deploy-behavior-agent", "start-activity", "wait-actor-ready"]

    store = StateStore(db)
    try:
        d = store.get_deploy(status.deploy_id)
        twin = store.get_deploy(TWIN_DEPLOY)
    finally:
        store.close()
    assert d.tier == "exr"
    assert d.ir_hash == status.playout_hash
    assert twin.status.value != "destroyed"


def test_apply_exercise_by_NAME_target_resolves_to_concrete_twin(tmp_path):
    """Name-targeting parity (2026-08-04, `docs/WORK_ORDER_corp_enterprise_
    bundle.md` §4): a spec whose `twin:` is a scenario NAME must resolve to the
    concrete te deploy id before the exr plan_id is built. This matters for
    more than portability -- the exr run's plan_id is `exr-<twin>-<token>` and
    the twin-destroy cascade finds every exr run by the `exr-<te_deploy_id>-*`
    prefix, so a name-authored run keyed on `exr-<name>-*` would be a
    cascade-orphan. Applies with `twin='twin-scn'` (the seeded twin's
    scenario_id)."""
    db = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(twin="twin-scn")),
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    assert status.status == "completed"
    assert status.twin == TWIN_DEPLOY  # resolved NAME -> concrete deploy id
    assert status.deploy_id.startswith(f"exr-{TWIN_DEPLOY}-")  # concrete-keyed plan_id


def test_apply_exercise_target_override_beats_spec_twin(tmp_path):
    """`--target`/API `target` overrides the spec's `twin:` (precedence
    target > spec). A spec with no twin at all resolves via the override."""
    db = _seeded_db(tmp_path)
    body = _spec_raw()
    del body["exercise"]["twin"]  # spec declares no twin
    status = apply_exercise(
        spec=parse_exercise_spec(body), target="twin-scn",
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    assert status.status == "completed"
    assert status.twin == TWIN_DEPLOY


def test_apply_exercise_no_twin_no_target_raises(tmp_path):
    db = _seeded_db(tmp_path)
    body = _spec_raw()
    del body["exercise"]["twin"]
    with pytest.raises(ExercisePlanError, match="no exercise target"):
        apply_exercise(
            spec=parse_exercise_spec(body), db_path=db, target_node="pxtest1",
            config_backend=_FakeConfigBackend(),
        )


def test_apply_exercise_replay_same_spec_seed_gives_identical_hash_new_deploy(tmp_path):
    """The real replay loop (`docs/design/08-exercise-measurement.md`):
    run -> measure -> destroy -> tune defense -> replay. Destroying run1
    FIRST is required now (S2, code review) -- a same-host second run while
    the first is still live is refused (`test_apply_exercise_same_host_
    second_run_refused_while_first_is_live` covers that refusal directly)."""
    db = _seeded_db(tmp_path)
    spec = parse_exercise_spec(_spec_raw())
    spec.activity.activity.seed = 42
    status1 = apply_exercise(
        spec=spec, db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    destroy_exercise(db_path=db, deploy_id=status1.deploy_id)
    status2 = apply_exercise(
        spec=spec, db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    assert status1.deploy_id != status2.deploy_id  # ephemeral -- always fresh
    assert status1.playout_hash == status2.playout_hash  # REPLAY -- identical playout


def test_apply_exercise_adversary_only(tmp_path):
    db = _seeded_db(tmp_path)
    backend = _FakeConfigBackend()
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(activity=False, adversary=True)),
        db_path=db, target_node="pxtest1", config_backend=backend,
    )
    assert status.status == "completed"
    assert sorted(status.adversary_targets) == ["ws01", "ws02"]
    assert len(backend.calls) == 2


def test_apply_exercise_same_host_second_run_refused_while_first_is_live(tmp_path):
    """S2 (code review): two exr runs targeting the SAME host at once collide
    on the host-global in-guest agent handle (a shared systemd-unit/scheduler
    path). A second activity run also on `attacker` while run1 is still live
    (non-destroyed) must be refused, fail-loud, before it does anything."""
    db = _seeded_db(tmp_path)
    status1 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    assert status1.status == "completed"

    backend2 = _FakeConfigBackend()
    with pytest.raises(ExercisePlanError, match="already carry a live") as exc:
        apply_exercise(
            spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
            config_backend=backend2,
        )
    assert exc.value.status == 409
    assert backend2.calls == []  # refused before any op ran


def test_apply_exercise_same_host_second_run_allowed_after_first_destroyed(tmp_path):
    """The other half: once run1 is destroyed, the SAME host is free again --
    this is the replay loop (`exr destroy` then re-run)."""
    db = _seeded_db(tmp_path)
    status1 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    destroy_exercise(db_path=db, deploy_id=status1.deploy_id)

    status2 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    assert status2.status == "completed"


def test_apply_exercise_disjoint_host_second_run_allowed_while_first_is_live(tmp_path):
    """The positive coexistence case: an activity run on `attacker` and an
    adversary run on `ws01`/`ws02` (disjoint host sets) may both stay live at
    once -- the S2 guard only refuses an OVERLAPPING host set."""
    db = _seeded_db(tmp_path)
    status1 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    status2 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(activity=False, adversary=True)),
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    assert status1.status == "completed"
    assert status2.status == "completed"
    assert status1.deploy_id != status2.deploy_id


def test_apply_exercise_missing_default_node_raises(tmp_path, monkeypatch):
    from axrange.adapter_proxmox import load_config

    db = _seeded_db(tmp_path)
    cfg = load_config(None)
    cfg.default_node = ""
    monkeypatch.setattr("axrange.adapter_proxmox.load_config", lambda *_a, **_k: cfg)
    with pytest.raises(ExercisePlanError, match="DEFAULT_NODE"):
        apply_exercise(
            spec=parse_exercise_spec(_spec_raw()), db_path=db,
            config_backend=_FakeConfigBackend(),
        )


# ---------------------------------------------------------------------------
# Destroy -- exr_-scoped ONLY; twin destroy cascades every bound exercise.
# ---------------------------------------------------------------------------
def test_destroy_exercise_leaves_twin_untouched(tmp_path):
    db = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    result = destroy_exercise(db_path=db, deploy_id=status.deploy_id)
    assert result.destroyed is True
    assert result.errors == []

    store = StateStore(db)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
        exr = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert twin.status.value != "destroyed"
    assert exr.status.value == "destroyed"


def test_destroy_exercise_no_deploy_id_raises(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(ExercisePlanError, match="deploy_id"):
        destroy_exercise(db_path=db)


def test_destroy_exercise_missing_deploy_raises_404(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(ExercisePlanError) as exc:
        destroy_exercise(db_path=db, deploy_id="nope")
    assert exc.value.status == 404


def test_destroy_exercise_refuses_non_exr_deploy(tmp_path):
    db = _seeded_db(tmp_path)  # TWIN_DEPLOY itself is tier="te"
    with pytest.raises(ExercisePlanError, match="not 'exr'"):
        destroy_exercise(db_path=db, deploy_id=TWIN_DEPLOY)


def test_cascade_destroy_exercises_for_te_removes_every_run(tmp_path):
    """A twin can have MULTIPLE independent exr runs bound to it (ephemeral,
    never auto-reaped) -- the cascade sweeps every non-destroyed one."""
    db = _seeded_db(tmp_path)
    status1 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    status2 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(activity=False, adversary=True)),
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    results = cascade_destroy_exercises_for_te(db_path=db, te_deploy_id=TWIN_DEPLOY)
    assert len(results) == 2
    assert all(r.destroyed for r in results)

    store = StateStore(db)
    try:
        d1 = store.get_deploy(status1.deploy_id)
        d2 = store.get_deploy(status2.deploy_id)
    finally:
        store.close()
    assert d1.status.value == "destroyed"
    assert d2.status.value == "destroyed"


def test_cascade_destroy_exercises_for_te_none_bound_returns_empty(tmp_path):
    db = _seeded_db(tmp_path)
    assert cascade_destroy_exercises_for_te(db_path=db, te_deploy_id=TWIN_DEPLOY) == []


def test_destroy_one_exercise_run_does_not_touch_sibling_run(tmp_path):
    """Two independent exr runs against the SAME twin coexist (§2.1's "no
    twin-scoped mutex" posture, module docstring) -- destroying ONE must not
    stop/affect the OTHER's still-running in-guest process or state row.
    Uses distinct hosts (run1's jittered activity scheduler on `attacker`,
    run2's adversary plant on ws01/ws02) so a leaked stop-call is
    unambiguously attributable to the wrong run."""
    db = _seeded_db(tmp_path)
    body1 = _spec_raw()
    body1["exercise"]["attack"]["activity"]["schedule"] = {
        "interval": {"min": "30s", "max": "1m"},
    }
    status1 = apply_exercise(
        spec=parse_exercise_spec(body1), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    status2 = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(activity=False, adversary=True)),
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )
    assert status1.deploy_id != status2.deploy_id

    stopped_vmids: list[int] = []

    class _SpyGA:
        def __init__(self, _client, _node, os_family="linux"):
            pass

        def exec(self, vmid, _argv, timeout_s=30):
            stopped_vmids.append(vmid)

            class _R:
                ok = True

            return _R()

    result = destroy_exercise(db_path=db, deploy_id=status2.deploy_id, ga_factory=_SpyGA)
    assert result.destroyed is True

    # Only run2's adversary targets (ws01=200002/ws02=200003) were stopped --
    # run1's still-armed jittered scheduler on attacker (200001) is untouched.
    assert set(stopped_vmids) == {200002, 200003}
    assert 200001 not in stopped_vmids

    store = StateStore(db)
    try:
        d1 = store.get_deploy(status1.deploy_id)
        d2 = store.get_deploy(status2.deploy_id)
    finally:
        store.close()
    assert d1.status.value == "completed"  # run1 untouched, still "up"
    assert d2.status.value == "destroyed"


def test_destroy_jittered_activity_run_stops_its_own_scheduler(tmp_path):
    """The OTHER half of the sibling-isolation test above: destroying a
    jittered-schedule run DOES stop ITS OWN scheduler process (the positive
    case -- `_stop_exercise_processes` isn't a no-op for the run being torn
    down, only for OTHER runs)."""
    from axrange.config_exec.render import activity_paths

    db = _seeded_db(tmp_path)
    body = _spec_raw()
    body["exercise"]["attack"]["activity"]["schedule"] = {
        "interval": {"min": "30s", "max": "1m"},
    }
    status = apply_exercise(
        spec=parse_exercise_spec(body), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )

    calls: list[tuple[int, list[str]]] = []

    class _SpyGA:
        def __init__(self, _client, _node, os_family="linux"):
            pass

        def exec(self, vmid, argv, timeout_s=30):
            calls.append((vmid, list(argv)))

            class _R:
                ok = True

            return _R()

    result = destroy_exercise(db_path=db, deploy_id=status.deploy_id, ga_factory=_SpyGA)
    assert result.destroyed is True
    assert len(calls) == 1
    vmid, argv = calls[0]
    assert vmid == 200001  # attacker
    paths = activity_paths("ad-kill-chain")
    assert argv == ["pkill", "-f", paths["scheduler"]]


def test_destroy_one_shot_activity_run_issues_no_stop_call(tmp_path):
    """A one-shot (no `schedule.interval`) activity's runner already finished
    on its own -- destroy must issue ZERO pkill calls for it (the module
    docstring's "only a JITTERED one leaves a persistent process" rule)."""
    db = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )

    calls: list[int] = []

    class _SpyGA:
        def __init__(self, _client, _node, os_family="linux"):
            pass

        def exec(self, vmid, argv, timeout_s=30):
            calls.append(vmid)

            class _R:
                ok = True

            return _R()

    result = destroy_exercise(db_path=db, deploy_id=status.deploy_id, ga_factory=_SpyGA)
    assert result.destroyed is True
    assert calls == []


def test_destroy_adversary_run_stops_the_planted_agent(tmp_path):
    """An adversary (Sandcat) plant is ALWAYS a persistent daemon -- destroy
    must issue a stop call for every planted target, Linux branch
    (`systemctl disable --now axr-sandcat.service` -- `disable`, not a bare
    `stop`, since the unit was installed `enable --now`; a bare `stop` would
    leave it re-armed on the next boot, code review nice-to-have)."""
    db = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw(activity=False, adversary=True)),
        db_path=db, target_node="pxtest1", config_backend=_FakeConfigBackend(),
    )

    calls: list[tuple[int, list[str]]] = []

    class _SpyGA:
        def __init__(self, _client, _node, os_family="linux"):
            pass

        def exec(self, vmid, argv, timeout_s=30):
            calls.append((vmid, list(argv)))

            class _R:
                ok = True

            return _R()

    result = destroy_exercise(db_path=db, deploy_id=status.deploy_id, ga_factory=_SpyGA)
    assert result.destroyed is True
    stopped_vmids = {vmid for vmid, _argv in calls}
    assert stopped_vmids == {200002, 200003}  # ws01, ws02
    for _vmid, argv in calls:
        assert argv == ["systemctl", "disable", "--now", "axr-sandcat.service"]


# ---------------------------------------------------------------------------
# Read-only status
# ---------------------------------------------------------------------------
def test_exercise_status_not_applied(tmp_path):
    db = _seeded_db(tmp_path)
    status = exercise_status(db_path=db, twin=TWIN_DEPLOY)
    assert status.status == "not-applied"
    assert status.deploy_id is None


def test_exercise_status_reflects_applied_run(tmp_path):
    db = _seeded_db(tmp_path)
    applied = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    status = exercise_status(db_path=db, deploy_id=applied.deploy_id)
    assert status.status == "completed"
    assert status.playout_hash == applied.playout_hash
    assert status.activity_host == "attacker"


# ---------------------------------------------------------------------------
# Full CLI argv-level dispatch (argparse -> tier/verb -> handler) -- proving
# the `exr` grid wiring itself (`_add_exr_deploy`/`_add_exr_status`/
# `_add_exr_destroy`, the `_HANDLERS` table's `("exr", ...)` rows, and
# `cmd_destroy`'s exr-cascade block), none of which any other test in this
# file exercises (they all call `axrange.o2control.exercise` functions
# directly). Mirrors `test_traffic_tier.py`'s
# `test_cli_tfc_validate_and_deploy_argv_dispatch` / `test_cli.py`'s
# `test_cmd_destroy_cascades_to_standalone_tap_plan`.
# ---------------------------------------------------------------------------
def _write_spec(tmp_path, *, name="spec.yaml", activity=True, adversary=False):
    lines = [f"exercise:\n  twin: {TWIN_DEPLOY}\n  attack:\n"]
    if activity:
        lines.append(
            "    activity:\n"
            "      id: ad-kill-chain\n"
            "      host: attacker\n"
            "      inline:\n"
            "        steps:\n"
            "          - tool: nmap\n"
            "            args: '-sV'\n"
            "            target: 10.0.0.0/24\n"
        )
    if adversary:
        lines.append(
            "    adversary:\n"
            "      id: red-sandcat\n"
            "      targets: [ws01, ws02]\n"
            "      server: http://caldera.o2.lab:8888\n"
        )
    p = tmp_path / name
    p.write_text("".join(lines))
    return str(p)


def test_cli_exr_deploy_and_status_argv_dispatch(tmp_path, monkeypatch):
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    spec_path = _write_spec(tmp_path)

    monkeypatch.setattr(
        "axrange.config_exec.ConfigBackend", lambda cfg: _FakeConfigBackend(),
    )
    rc = climain.main([
        "exr", "deploy", spec_path, "--db", db_path, "--node", "pxtest1",
    ])
    assert rc == 0

    rc = climain.main(["exr", "status", "--db", db_path, "--target", TWIN_DEPLOY])
    assert rc == 0


def test_cli_exr_run_is_a_first_class_alias_of_deploy(tmp_path, monkeypatch):
    """`run` and `deploy` both dispatch to the same handler (work order's own
    `axrange exr run <twin> [--spec FILE]` phrasing)."""
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    spec_path = _write_spec(tmp_path)
    monkeypatch.setattr(
        "axrange.config_exec.ConfigBackend", lambda cfg: _FakeConfigBackend(),
    )
    rc = climain.main(["exr", "run", spec_path, "--db", db_path, "--node", "pxtest1"])
    assert rc == 0


def test_cli_exr_destroy_argv_dispatch_refuses_without_allow_and_then_destroys(
    tmp_path, monkeypatch,
):
    import axrange.adapter_proxmox as adapter_mod
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    spec_path = _write_spec(tmp_path)
    monkeypatch.setattr(
        "axrange.config_exec.ConfigBackend", lambda cfg: _FakeConfigBackend(),
    )
    rc = climain.main([
        "exr", "deploy", spec_path, "--db", db_path, "--node", "pxtest1",
    ])
    assert rc == 0

    class _FakeTeardown:
        def __init__(self, *_a, **_k):
            pass

        def vm_name(self, node, vmid):
            return None

    class _FakeGuestAgent:
        def __init__(self, *_a, **_k):
            pass

        def exec(self, vmid, argv, timeout_s=300):
            class R:
                ok = True
                exit_code = 0
                stdout = ""
                stderr = ""
            return R()

    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: _FakeTeardown())
    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGuestAgent)
    monkeypatch.setattr(rest_mod, "RestClient", lambda cfg: object())

    # Refused without --allow-destroy.
    rc = climain.main(["exr", "destroy", "--db", db_path, "--target", TWIN_DEPLOY])
    assert rc != 0

    rc = climain.main([
        "exr", "destroy", "--db", db_path, "--target", TWIN_DEPLOY,
        "--adapter-config", "/nonexistent.conf", "--allow-destroy",
    ])
    assert rc == 0

    store = StateStore(db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
    finally:
        store.close()
    assert twin.status.value != "destroyed"  # exr destroy never touches the twin


def test_cmd_destroy_cascades_to_standalone_exercise_run(tmp_path, monkeypatch):
    """A real `axrange te destroy` (`axrange.cli.main.cmd_destroy`) also
    sweeps every exercise run bound to that twin (the CLI-side counterpart to
    `test_destroy_deploy_cascades_to_every_exercise_run` in `test_api.py`,
    which exercises the SAME cascade through the API instead)."""
    import argparse

    import axrange.adapter_proxmox as adapter_mod
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db_path, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    assert status.status == "completed"

    class _FakeTeardown:
        def __init__(self, *_a, **_k):
            self.calls = []

        def vm_name(self, node, vmid):
            return None

        def destroy_vm(self, node, vmid):
            self.calls.append(("vm", vmid))
            return "removed"

        def delete_mirror_snippets(self, node, vmid):
            return "absent"

        def delete_gretap(self, dev):
            return "absent"

        def delete_vnet(self, vnet_id):
            return "absent"

        def delete_sdn_zone(self, zone_id):
            return "absent"

        def apply_sdn(self):
            return "ok"

        def delete_ovs_bridge(self, node, bridge_name):
            return "absent"

    class _FakeGuestAgent:
        def __init__(self, *_a, **_k):
            pass

        def exec(self, vmid, argv, timeout_s=300):
            class R:
                ok = True
                exit_code = 0
                stdout = ""
                stderr = ""
            return R()

    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: _FakeTeardown())
    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGuestAgent)
    monkeypatch.setattr(rest_mod, "RestClient", lambda cfg: object())

    args = argparse.Namespace(
        deploy_id=TWIN_DEPLOY, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(args)
    assert rc == 0

    store = StateStore(db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
        exr = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert twin.status.value == "destroyed"
    assert exr.status.value == "destroyed"
    assert status.twin == TWIN_DEPLOY


def test_cmd_destroy_exr_cascade_survives_an_unrelated_sibling_cascade_crash(
    tmp_path, monkeypatch,
):
    """S3 (code review): an UNEXPECTED exception in the tap-plan cascade (a
    DB hiccup, an adapter bug -- NOT that cascade's own typed `TapPlanError`)
    must not propagate and skip the REMAINING sibling cascades or the twin's
    own DestroyResponse. Before the fix, this would have raised out of
    `cmd_destroy` entirely, leaving the exr run undestroyed and the CLI
    call itself failing with a raw traceback instead of `Result: DESTROYED`."""
    import argparse

    import axrange.adapter_proxmox as adapter_mod
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod
    import axrange.o2control.taps as taps_mod
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    status = apply_exercise(
        spec=parse_exercise_spec(_spec_raw()), db_path=db_path, target_node="pxtest1",
        config_backend=_FakeConfigBackend(),
    )
    assert status.status == "completed"

    def _boom(**_kwargs):
        raise RuntimeError("simulated DB hiccup in the tap-plan cascade")

    monkeypatch.setattr(taps_mod, "cascade_destroy_for_te", _boom)

    class _FakeTeardown:
        def __init__(self, *_a, **_k):
            pass

        def vm_name(self, node, vmid):
            return None

        def destroy_vm(self, node, vmid):
            return "removed"

        def delete_mirror_snippets(self, node, vmid):
            return "absent"

        def delete_gretap(self, dev):
            return "absent"

        def delete_vnet(self, vnet_id):
            return "absent"

        def delete_sdn_zone(self, zone_id):
            return "absent"

        def apply_sdn(self):
            return "ok"

        def delete_ovs_bridge(self, node, bridge_name):
            return "absent"

    class _FakeGuestAgent:
        def __init__(self, *_a, **_k):
            pass

        def exec(self, vmid, argv, timeout_s=300):
            class R:
                ok = True
                exit_code = 0
                stdout = ""
                stderr = ""
            return R()

    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: _FakeTeardown())
    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGuestAgent)
    monkeypatch.setattr(rest_mod, "RestClient", lambda cfg: object())

    args = argparse.Namespace(
        deploy_id=TWIN_DEPLOY, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(args)
    assert rc == 0  # the twin's OWN destroy still succeeds

    store = StateStore(db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
        exr = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert twin.status.value == "destroyed"
    # The exr cascade (a DIFFERENT sibling, unaffected by the tap crash)
    # still ran and destroyed the exercise run.
    assert exr.status.value == "destroyed"
