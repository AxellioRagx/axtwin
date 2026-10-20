"""Tests for `axrange.o2control.traffic` (the Traffic tier, `docs/
WORK_ORDER_traffic_tier.md`) plus the Part-1 prerequisite (`role:` IR field,
`HostInfo.role`, and the twin-config expander emitting it).

No live cluster: every apply/destroy test drives a real `StateStore` (tmp
SQLite file) with hand-seeded op rows (the same "derive purely from
state.db" posture `tests/test_o2_taps.py` uses) plus a FAKE persona backend,
run through the REAL `Executor` (via `apply_traffic_profile` ->
`run_deploy_executor`) -- the same idiom `test_o2_taps.py` uses for A2.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

_CATALOG = __import__("pathlib").Path(__file__).resolve().parents[2] / "catalog"  # twin content root

from axrange.ir.parser import parse_ir
from axrange.o2control import (
    TrafficPlanError,
    apply_traffic_profile,
    cascade_destroy_tfc_for_te,
    destroy_traffic_profile,
    traffic_status,
)
from axrange.o2control.traffic import (
    AssignmentSpec,
    PersonaBackend,
    ReplayBindingSpec,
    RoleAssignmentSpec,
    TrafficProfileSpec,
    _hosts_by_role,
    _stop_instances,
    compose_traffic_ops,
    parse_traffic_profile,
    resolve_traffic_bindings,
)
from axrange.personas.drivers.base import instance_paths
from axrange.planner import StateStore
from axrange.planner.cluster import ClusterContext
from axrange.planner.inventory import build_twin_inventory
from axrange.planner.state import OpRow, OpStatus


# ---------------------------------------------------------------------------
# Seeding helpers -- a synthetic twin (`te`-tier deploy) with hosts carrying
# `role`/`vmid`/an attached leg IP, exactly what `build_twin_inventory`
# derives from real `clone-vm`/`attach-vm-to-vnet` ops.
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


TWIN_DEPLOY = "te-twin-test-1"


def _seed_twin(store, *, deploy_id=TWIN_DEPLOY, hosts, tier="te"):
    """`hosts`: list of dicts `{host_id, role, vmid, ip, net_id}` (net_id
    defaults to "lan")."""
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
            inputs={
                "target_node": "pxtest1", "node_role": h.get("role"),
                "image_id": h.get("image_id"),
            },
            outputs={"vmid": h["vmid"]},
        ))
        rows.append(_op_row(
            deploy_id, f"op-attach-{h['host_id']}", "attach-vm-to-vnet",
            "OP-HOST-040", {"host": h["host_id"], "net": h.get("net_id", "lan")},
            inputs={
                "net_id": h.get("net_id", "lan"), "ip": h.get("ip"), "nic_index": 0,
            },
        ))
    store.insert_ops(deploy_id, rows)
    return deploy_id


_HOSTS = [
    {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
    {"host_id": "ws02", "role": "corp-workstation", "vmid": 100002, "ip": "10.0.0.22"},
    {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
]


def _seeded_db(tmp_path, *, hosts=None, deploy_id=TWIN_DEPLOY, tier="te"):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_twin(store, deploy_id=deploy_id, hosts=hosts or _HOSTS, tier=tier)
    finally:
        store.close()
    return db


class _FakePersonaBackend:
    """Records every `drive-persona` call; used as `persona_backend=` for
    `apply_traffic_profile` -- same idiom as `test_o2_taps.py`'s
    `_FakeMirAdapter`. Also implements the `stop_instance` test seam
    `_stop_instances` looks for, so destroy/drop-diff tests don't need a
    real GuestAgent either.

    `running` (code review, MUST-FIX corollary) tracks which instance_ids
    are CURRENTLY started-and-not-yet-stopped -- lets a test assert "exactly
    one unit running on the host" after a persona swap, not just "both a
    start and a stop call happened somewhere in the call history\""""

    def __init__(self):
        self.calls: list[dict] = []
        self.stopped: list[tuple] = []
        self.running: set[str] = set()

    def run(self, op, resolved_inputs, ctx):
        self.calls.append(dict(resolved_inputs))
        # `assign-role` (OP-TFC-020) has no `instance_id` and starts nothing --
        # it is pure metadata (W1). Only `drive-persona` tracks a running unit.
        if op.op_name == "assign-role":
            return {"assigned": True}
        self.running.add(resolved_inputs["instance_id"])
        return {"started": True}

    def stop_instance(self, vmid, drive, instance_id):
        self.stopped.append((vmid, drive, instance_id))
        self.running.discard(instance_id)


def _apply(tmp_path, spec, *, db=None, backend=None):
    backend = backend or _FakePersonaBackend()
    db = db or str(tmp_path / "state.db")
    status = apply_traffic_profile(
        spec=spec, db_path=db, target_node="pxtest1", persona_backend=backend,
    )
    return db, backend, status


# ---------------------------------------------------------------------------
# PART 1 -- role: IR field, HostInfo.role, twin-config expander
# ---------------------------------------------------------------------------
_ROLE_IR = """
scenario_schema_version: 1
scenario: { id: role-ir, version: 1 }
layers:
  infra:
    networks:
      - { id: lan, cidr: 10.5.0.0/24 }
    nodes:
      - { id: ws01, kind: host, net: lan, image: debian-12-cloud, role: corp-workstation }
      - { id: dc01, kind: host, net: lan, image: debian-12-cloud, role: domain-controller }
  apps: []
bindings: []
"""


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def test_ir_node_role_is_optional_and_parses():
    ir = parse_ir(text=_ROLE_IR).ir
    nodes = {n.id: n for n in ir.layers.infra.nodes}
    assert nodes["ws01"].role == "corp-workstation"
    assert nodes["dc01"].role == "domain-controller"


def test_ir_without_role_still_parses_unchanged():
    """Every existing (role-less) IR must still parse -- role: is additive."""
    src = (
        "scenario_schema_version: 1\n"
        "scenario: { id: no-role, version: 1 }\n"
        "layers: { infra: { networks: [{id: lan, cidr: 10.5.0.0/24}], "
        "nodes: [{id: h1, kind: host, net: lan, image: debian-12-cloud}] }, apps: [] }\n"
        "bindings: []\n"
    )
    ir = parse_ir(text=src).ir
    assert ir.layers.infra.nodes[0].role is None


def test_target_environments_and_twin_configs_still_validate():
    """Every shipped target-environments/*/scenario.yaml and every twin-config
    still expands/validates unchanged -- role: is additive, no version bump
    needed to keep existing content green (STR-020 still accepts
    scenario_schema_version: 1)."""
    from axrange.validator.engine import validate_structural

    result = validate_structural(text=_ROLE_IR)
    assert "STR-020" not in _ids(result)


def test_str_020_accepts_version_2_too():
    from axrange.validator.engine import validate_structural

    src = _ROLE_IR.replace("scenario_schema_version: 1", "scenario_schema_version: 2")
    result = validate_structural(text=src)
    assert "STR-020" not in _ids(result)


def test_str_020_still_rejects_unrecognized_version():
    from axrange.validator.engine import validate_structural

    src = _ROLE_IR.replace("scenario_schema_version: 1", "scenario_schema_version: 99")
    result = validate_structural(text=src)
    assert "STR-020" in _ids(result)


def test_host_info_role_populated_from_node_role(tmp_path):
    """GAP-027: `HostInfo.role` must reflect the IR's `node.role`, sourced
    from the UNCONDITIONAL `clone-vm` op (not a tap-gated one)."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        inv = build_twin_inventory(store, TWIN_DEPLOY)
    finally:
        store.close()
    roles = {h.host_id: h.role for h in inv.hosts}
    assert roles == {
        "ws01": "corp-workstation", "ws02": "corp-workstation", "dc01": "domain-controller",
    }


def test_host_info_role_none_when_undeclared(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_twin(store, hosts=[{"host_id": "h1", "role": None, "vmid": 100010, "ip": "10.0.0.9"}])
        inv = build_twin_inventory(store, TWIN_DEPLOY)
    finally:
        store.close()
    assert inv.hosts[0].role is None


def test_twinconfig_expander_emits_role_on_nodes():
    """`name:` is the id stem, `role:` the OPTIONAL semantic baseline
    (2026-08-04, `docs/WORK_ORDER_role_persona_tier.md` §4.5). This pins the
    interesting case the split makes expressible: a node whose role DIFFERS
    from its name (`db-replica` named `db02`), which the pre-split single
    field could not represent at all."""
    from axrange.twinconfig.expand import expand_twin_config_raw

    raw = {
        "twin_config": {
            "id": "role-check",
            "segments": [{"name": "lan", "cidr": "10.9.0.0/24"}],
            "nodes": [
                {"name": "ws", "role": "corp-workstation",
                 "image": "debian-12-cloud", "net": "lan", "count": 2},
                {"name": "dc01", "role": "domain-controller",
                 "image": "debian-12-cloud", "net": "lan"},
            ],
        }
    }
    ir_dict, _params = expand_twin_config_raw(raw)
    roles = {n["id"]: n["role"] for n in ir_dict["layers"]["infra"]["nodes"]}
    assert roles == {
        "ws01": "corp-workstation",
        "ws02": "corp-workstation",
        "dc01": "domain-controller",
    }


def test_twinconfig_node_without_role_publishes_no_role_key():
    """The bare-bone twin: omitting `role:` is a supported authoring choice,
    and the expanded IR then carries no `role` key at all rather than a
    fabricated one -- a traffic profile's `roles:` block supplies the whole
    map (`docs/WORK_ORDER_role_persona_tier.md` §4.5)."""
    from axrange.twinconfig.expand import expand_twin_config_raw

    raw = {
        "twin_config": {
            "id": "bare-bone",
            "segments": [{"name": "lan", "cidr": "10.9.0.0/24"}],
            "nodes": [{"name": "h", "image": "debian-12-cloud", "net": "lan"}],
        }
    }
    ir_dict, _params = expand_twin_config_raw(raw)
    node = ir_dict["layers"]["infra"]["nodes"][0]
    assert node["id"] == "h"
    assert "role" not in node


@pytest.mark.parametrize(
    "config_id",
    ["corp-enterprise", "datacenter", "ics-ot", "dmz-perimeter", "sme-flat"],
)
def test_all_five_starter_twin_configs_expand_with_role(config_id):
    """Every shipped `twins/<id>/te.yaml` expands to IR whose nodes carry
    `role:` -- confirming the round-trip end to end, not just a synthetic
    fixture."""
    from axrange.twinconfig.expand import expand_twin_config

    ir_dict, _params = expand_twin_config(config_id, catalog_dir=str(_CATALOG / "twins"))
    nodes = ir_dict["layers"]["infra"]["nodes"]
    assert nodes, f"{config_id}: expanded with zero nodes"
    for n in nodes:
        assert n.get("role"), f"{config_id}: node {n.get('id')!r} has no role"


def test_role_surfaces_in_inventory_after_deploy_round_trip(tmp_path):
    """Build a REAL plan from the role-bearing IR (pass1_emit's actual
    clone-vm emission), persist it through the REAL StateStore/op_to_row
    path (LES-065/LES-068 round-trip discipline), and read it back through
    `build_twin_inventory` -- not a hand-seeded OpRow fixture."""
    from axrange.manifest.loader import ManifestRegistry
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.state import op_to_row

    ir = parse_ir(text=_ROLE_IR).ir
    manifests = ManifestRegistry(manifests={}, bindings={})
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(ir, manifests, ctx, plan_id="role-rt-plan", raw_ir={})

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="role-rt-1", scenario_id=plan.scenario_id, scenario_version=1,
            scenario_schema_version=1, plan_id=plan.plan_id, ir_hash="h",
            placement={}, tier="te",
        )
        store.insert_ops(
            "role-rt-1", [op_to_row(op, "role-rt-1") for op in plan.ops.values()]
        )
        inv = build_twin_inventory(store, "role-rt-1")
    finally:
        store.close()
    roles = {h.host_id: h.role for h in inv.hosts}
    assert roles.get("ws01") == "corp-workstation"
    assert roles.get("dc01") == "domain-controller"


# ---------------------------------------------------------------------------
# PART 2 -- traffic-profile parsing
# ---------------------------------------------------------------------------
def test_parse_traffic_profile_requires_top_level_key():
    with pytest.raises(TrafficPlanError, match="traffic-profile:"):
        parse_traffic_profile({"twin": "t1", "assignments": []})


def test_parse_traffic_profile_target_is_optional():
    """A profile with NO target is a legitimate PREDEFINED library file since
    2026-08-04 (`docs/WORK_ORDER_role_persona_tier.md` §4.3, tap-plan parity)
    -- it parses fine and relies on a `--target`/API-body override at apply
    time. It used to be a hard parse error."""
    spec = parse_traffic_profile(
        {"traffic-profile": {"assignments": [{"role": "x", "persona": "y"}]}}
    )
    assert spec.target is None


def test_parse_traffic_profile_target_present_but_malformed_still_errors():
    """Present-but-malformed is a parse error, never silently coerced to
    'absent' (same posture as `taps.parse_tap_plan`)."""
    with pytest.raises(TrafficPlanError, match="non-empty string"):
        parse_traffic_profile({
            "traffic-profile": {"target": "", "assignments": [{"role": "x", "persona": "y"}]},
        })


def test_parse_traffic_profile_legacy_twin_key_still_accepted():
    """`twin:` is the pre-2026-08-04 spelling and stays valid, so every
    already-written profile keeps parsing unchanged."""
    spec = parse_traffic_profile({
        "traffic-profile": {"twin": "te-1", "assignments": [{"role": "x", "persona": "y"}]},
    })
    assert spec.target == "te-1"


def test_parse_traffic_profile_rejects_both_target_and_twin():
    with pytest.raises(TrafficPlanError, match="both"):
        parse_traffic_profile({
            "traffic-profile": {
                "target": "a", "twin": "b",
                "assignments": [{"role": "x", "persona": "y"}],
            },
        })


def test_parse_traffic_profile_requires_at_least_one_entry():
    with pytest.raises(TrafficPlanError, match="assignments\\[\\] or replay\\[\\]"):
        parse_traffic_profile({"traffic-profile": {"twin": "t1"}})


def test_parse_traffic_profile_assignment_requires_exactly_one_addressing_mode():
    with pytest.raises(TrafficPlanError, match="exactly one of"):
        parse_traffic_profile({
            "traffic-profile": {
                "twin": "t1",
                "assignments": [{"role": "x", "node": "y", "persona": "corp-workstation"}],
            }
        })
    with pytest.raises(TrafficPlanError, match="exactly one of"):
        parse_traffic_profile({
            "traffic-profile": {"twin": "t1", "assignments": [{"persona": "corp-workstation"}]},
        })


def test_parse_traffic_profile_assignment_requires_persona():
    with pytest.raises(TrafficPlanError, match="persona"):
        parse_traffic_profile({
            "traffic-profile": {"twin": "t1", "assignments": [{"role": "corp-workstation"}]},
        })


def test_parse_traffic_profile_happy_path():
    spec = parse_traffic_profile({
        "traffic-profile": {
            "twin": "te-1",
            "assignments": [
                {"role": "corp-workstation", "persona": "corp-workstation", "seed": 7},
                {"node": "dc01", "persona": "domain-controller"},
            ],
        }
    })
    assert spec.target == "te-1"
    assert len(spec.assignments) == 2
    assert spec.assignments[0].seed == 7


def test_parse_traffic_profile_replay_requires_template_and_onto_role():
    with pytest.raises(TrafficPlanError, match="template"):
        parse_traffic_profile({
            "traffic-profile": {"twin": "t1", "replay": [{"onto_role": "historian"}]},
        })
    with pytest.raises(TrafficPlanError, match="onto_role"):
        parse_traffic_profile({
            "traffic-profile": {"twin": "t1", "replay": [{"template": "/x.pcap"}]},
        })


def test_parse_traffic_profile_replay_only_is_accepted():
    spec = parse_traffic_profile({
        "traffic-profile": {
            "twin": "t1",
            "replay": [{"template": "/x.pcap", "onto_role": "historian", "orig_ip": "10.1.1.1"}],
        }
    })
    assert spec.replay[0].template == "/x.pcap"
    assert spec.replay[0].iface == "eth0"


# ---------------------------------------------------------------------------
# `schedule:` override normalization (2026-07-25, WORK_ORDER_traffic_tier.md).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("24x7", "24x7"),
        ("24/7", "24x7"),
        ("always", "24x7"),
        ("ALWAYS", "24x7"),
        ("24X7", "24x7"),
        ("business-hours", "business-hours"),
        ("business_hours", "business-hours"),
        ("Business-Hours", "business-hours"),
    ],
)
def test_parse_traffic_profile_schedule_normalizes_spelling(raw, expected):
    spec = parse_traffic_profile({
        "traffic-profile": {
            "twin": "t1",
            "assignments": [
                {"role": "corp-workstation", "persona": "corp-workstation", "schedule": raw},
            ],
        }
    })
    assert spec.assignments[0].schedule == expected


def test_parse_traffic_profile_schedule_unset_is_none():
    spec = parse_traffic_profile({
        "traffic-profile": {
            "twin": "t1",
            "assignments": [{"role": "corp-workstation", "persona": "corp-workstation"}],
        }
    })
    assert spec.assignments[0].schedule is None


def test_parse_traffic_profile_schedule_bad_value_fails_loud_at_parse():
    with pytest.raises(TrafficPlanError, match="unknown schedule override"):
        parse_traffic_profile({
            "traffic-profile": {
                "twin": "t1",
                "assignments": [{
                    "role": "corp-workstation", "persona": "corp-workstation",
                    "schedule": "bussiness-hours",
                }],
            }
        })


def test_parse_traffic_profile_schedule_non_string_fails_loud_at_parse():
    with pytest.raises(TrafficPlanError, match="'schedule' must be a string"):
        parse_traffic_profile({
            "traffic-profile": {
                "twin": "t1",
                "assignments": [{
                    "role": "corp-workstation", "persona": "corp-workstation",
                    "schedule": 247,
                }],
            }
        })


# ---------------------------------------------------------------------------
# Binding resolution -- role/node addressing, requires[]/peer_role rejection.
# ---------------------------------------------------------------------------
def test_resolve_bindings_by_role_fans_out_to_every_matching_host(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    host_ids = sorted(ra.host_id for ra in res.resolved)
    assert host_ids == ["ws01", "ws02"]
    assert res.assignment_results[0].hosts == ["ws01", "ws02"]


def test_resolve_bindings_by_node(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert [ra.host_id for ra in res.resolved] == ["dc01"]
    assert res.resolved[0].instance_id == "dc01-domain-controller"


def test_resolve_bindings_unknown_node_raises(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(node="nope", persona=_SIMPLE_PERSONA)],
        )
        with pytest.raises(TrafficPlanError, match="not a host"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_bindings_unknown_role_raises(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="nonexistent-role", persona=_SIMPLE_PERSONA)],
        )
        with pytest.raises(TrafficPlanError, match="matches no host"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_bindings_unknown_persona_raises(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="corp-workstation", persona="not-a-real-persona")],
        )
        with pytest.raises(TrafficPlanError, match="unknown persona"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


# `dmz-web` (requires: [], emits only to the reserved `internet` peer_role)
# stands in for a "any valid, no-fuss persona" reference in tests that are
# about resolution MECHANICS (fan-out/dedup/vmid/tier), not persona content
# semantics -- `corp-workstation`'s real `requires: [domain-controller,
# egress-proxy, file-server, mail-server]` (dns-resolver/print-server are
# `optional:`, 2026-07-25 refinement -- see the dedicated tests below) would
# still force every such test to stand up a four-role twin just to get past
# `_check_requires`.
_SIMPLE_PERSONA = "dmz-web"


def test_resolve_bindings_rejects_missing_requires_role(tmp_path):
    """`domain-controller` isn't in this twin, so a persona whose `requires`
    lists it must be rejected fail-loud."""
    hosts = [{"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"}]
    db = _seeded_db(tmp_path, hosts=hosts)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="corp-workstation", persona="corp-workstation")],
        )
        with pytest.raises(TrafficPlanError, match="requires role"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_peers_rejects_unresolvable_non_reserved_peer_role():
    """A non-reserved `peer_role` with NO matching host in the twin is a
    real authoring error -- fail-loud (LES-098/LES-001), never a silent
    skip (unlike a RESERVED peer_role, exercised just above)."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.schema import Emit, Persona

    persona = Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="print", ports=(631,), peer_role="print-server"),),
    )
    with pytest.raises(TrafficPlanError, match="peer_role"):
        _resolve_peers(persona, {})


def test_resolve_peers_optional_peer_absent_skips_with_warning():
    """2026-07-25 refinement: an `optional: true` peer_role with NO
    matching host is resolved-if-present, NOT a fail-loud authoring error
    -- it's skipped, and a warning is recorded (never silently dropped,
    LES-089's "surface, don't swallow" posture)."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.schema import Emit, Persona

    persona = Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="print", ports=(631,), peer_role="print-server", optional=True),),
    )
    warnings: list[str] = []
    peers = _resolve_peers(persona, {}, warnings=warnings)
    assert peers == {}
    assert len(warnings) == 1
    assert "print-server" in warnings[0]
    assert "optional" in warnings[0]


def _mesh_persona(**emit_kw):
    """A persona whose peer_role is a role the SENDER also holds -- any mesh."""
    from axrange.personas.schema import Emit, Persona

    return Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="netflow", ports=(2055,), peer_role="peer-role",
                    **emit_kw),),
    )


def _mesh_hosts(*ids):
    from axrange.planner.inventory import HostInfo, HostNet

    return {"peer-role": [
        HostInfo(host_id=hid, vmid=100 + i, role="peer-role",
                 nets=[HostNet(net_id="lan", ip=f"10.0.0.{10 + i}")])
        for i, hid in enumerate(ids)
    ]}


def test_resolve_peers_excludes_own_host_on_single_peer_path():
    """`peer_scope: all` has always excluded the instance's own host
    (LES-170); the single-peer path did not. A persona whose peer_role is a
    role the SENDER ALSO HOLDS therefore resolved to its own IP and talked to
    itself over loopback -- traffic real enough to log `sent ... fails=0` and
    invisible to every tap, because loopback never crosses a tap point.
    Found live on dev45 2026-08-13, where a 3-host mesh reported perfect
    health while putting ZERO frames on the wire."""
    from axrange.o2control.traffic import _resolve_peers

    hosts = _mesh_hosts("h-a", "h-b")
    # h-a must pick h-b (10.0.0.11), never its own 10.0.0.10.
    peers = _resolve_peers(_mesh_persona(), hosts, exclude_host_id="h-a")
    assert peers == {"peer-role": "10.0.0.11"}
    # ...and symmetrically for h-b.
    peers = _resolve_peers(_mesh_persona(), hosts, exclude_host_id="h-b")
    assert peers == {"peer-role": "10.0.0.10"}


def test_resolve_peers_sole_holder_is_self_skips_with_warning():
    """When the instance is the role's ONLY holder there is genuinely no peer.
    That is a normal topology (a one-DC twin), not an authoring error -- so
    skip with a warning rather than either failing the bind or, as before,
    papering over it with a self-peer. The guest client already logs-and-skips
    an emit with no resolved peer."""
    from axrange.o2control.traffic import _resolve_peers

    warnings: list[str] = []
    peers = _resolve_peers(_mesh_persona(), _mesh_hosts("h-a"),
                           warnings=warnings, exclude_host_id="h-a")
    assert peers == {}, "must NOT resolve to its own host"
    assert len(warnings) == 1 and "h-a" in warnings[0]


def test_resolve_peers_sole_holder_self_does_not_fail_a_hard_emit():
    """The sole-holder-is-self case must not be conflated with 'no host has
    this role at all', which stays fail-loud. A HARD (non-optional) emit whose
    role exists but is held only by the sender is skipped, not raised on --
    otherwise this fix would break every single-DC twin's tfc deploy, since
    `domain-controller`'s replication emit targets its own role."""
    from axrange.o2control.traffic import _resolve_peers

    peers = _resolve_peers(_mesh_persona(optional=False), _mesh_hosts("only"),
                           exclude_host_id="only")
    assert peers == {}
    # ...while a role no host carries at all still fails loud, unchanged.
    with pytest.raises(TrafficPlanError, match="peer_role"):
        _resolve_peers(_mesh_persona(optional=False), {}, exclude_host_id="only")


def test_shipped_domain_controller_replication_no_longer_self_peers():
    """The shipped `domain-controller` persona replicates to `peer_role:
    domain-controller` and documents the intent in its own comment: inter-DC
    traffic "only exists when the twin has MORE THAN ONE domain controller".
    That was true and previously defeated -- the candidate list contained the
    DC itself, so the `optional:` skip never fired and it replicated to
    loopback on every single-DC twin."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.registry import PERSONAS
    from axrange.planner.inventory import HostInfo, HostNet

    dc = PERSONAS["domain-controller"]
    # The persona also serves corp-workstation (dns/kerberos/ldap/smb/rpc/ntp),
    # all HARD peers -- supply one so the bind reaches the replication emit.
    one_dc = {
        "domain-controller": [
            HostInfo(host_id="dc01", vmid=1, role="domain-controller",
                     nets=[HostNet(net_id="srv", ip="10.62.10.2")])],
        "corp-workstation": [
            HostInfo(host_id="ws01", vmid=9, role="corp-workstation",
                     nets=[HostNet(net_id="lan", ip="10.62.0.2")])],
    }
    warnings: list[str] = []
    peers = _resolve_peers(dc, one_dc, warnings=warnings, exclude_host_id="dc01")
    assert peers.get("domain-controller") is None, "single-DC twin: no self-peer"

    two_dc = dict(one_dc)
    two_dc["domain-controller"] = list(one_dc["domain-controller"]) + [
        HostInfo(host_id="dc02", vmid=2, role="domain-controller",
                 nets=[HostNet(net_id="srv", ip="10.62.10.3")])]
    peers = _resolve_peers(dc, two_dc, exclude_host_id="dc01")
    assert peers["domain-controller"] == "10.62.10.3", "two-DC twin: real peer"


def test_resolve_peers_optional_peer_resolves_when_present():
    """The other half: an optional peer_role that DOES have a matching
    host resolves exactly like a hard one -- no warning, real IP threaded."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.schema import Emit, Persona
    from axrange.planner.inventory import HostInfo, HostNet

    persona = Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", ports=(53,), peer_role="dns-resolver", optional=True),),
    )
    hosts_by_role = {
        "dns-resolver": [
            HostInfo(host_id="dns01", vmid=1, role="dns-resolver",
                     nets=[HostNet(net_id="lan", ip="10.0.0.7")]),
        ],
    }
    warnings: list[str] = []
    peers = _resolve_peers(persona, hosts_by_role, warnings=warnings)
    assert peers == {"dns-resolver": "10.0.0.7"}
    assert warnings == []


def test_resolve_bindings_corp_workstation_onto_corp_enterprise_shaped_twin(tmp_path):
    """The EXACT live dev45 case that motivated this refinement: a twin
    carrying domain-controller/egress-proxy/file-server/mail-server (the
    corp-workstation persona's HARD requires) but NEITHER dns-resolver NOR
    print-server (both marked `optional:` now) must bind cleanly -- this
    used to reject with 'requires role(s) [dns-resolver, print-server]'."""
    hosts = [
        {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
        {"host_id": "dc01", "role": "domain-controller", "vmid": 100002, "ip": "10.0.0.5"},
        {"host_id": "prx01", "role": "egress-proxy", "vmid": 100003, "ip": "10.0.0.6"},
        {"host_id": "file01", "role": "file-server", "vmid": 100004, "ip": "10.0.0.8"},
        {"host_id": "mail01", "role": "mail-server", "vmid": 100005, "ip": "10.0.0.9"},
        # deliberately NO dns-resolver, NO print-server host.
    ]
    db = _seeded_db(tmp_path, hosts=hosts)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(node="ws01", persona="corp-workstation")],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert [ra.host_id for ra in res.resolved] == ["ws01"]
    peers = res.resolved[0].peers
    assert "dns-resolver" not in peers
    assert "print-server" not in peers
    assert peers["domain-controller"] == "10.0.0.5"
    assert peers["egress-proxy"] == "10.0.0.6"
    assert peers["file-server"] == "10.0.0.8"
    assert peers["mail-server"] == "10.0.0.9"
    warning_text = " ".join(res.warnings)
    assert "dns-resolver" in warning_text
    assert "print-server" in warning_text


def test_resolve_peers_round_robin_fans_out_across_hub_role_candidates():
    """Should-fix (twin fidelity, code review): a hub role with MULTIPLE
    hosts (e.g. two domain controllers) must not have every instance
    hammer the same deterministic-first one -- `index=` round-robins
    across the `host_id`-sorted candidates. Deterministic: the SAME index
    always resolves to the SAME peer."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.schema import Emit, Persona
    from axrange.planner.inventory import HostInfo, HostNet

    persona = Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="kerberos", ports=(88,), peer_role="domain-controller"),),
    )
    hosts_by_role = {
        "domain-controller": [
            HostInfo(host_id="dc01", vmid=1, role="domain-controller",
                     nets=[HostNet(net_id="lan", ip="10.0.0.5")]),
            HostInfo(host_id="dc02", vmid=2, role="domain-controller",
                     nets=[HostNet(net_id="lan", ip="10.0.0.6")]),
        ],
    }
    picks = [
        _resolve_peers(persona, hosts_by_role, index=i)["domain-controller"]
        for i in range(4)
    ]
    assert picks == ["10.0.0.5", "10.0.0.6", "10.0.0.5", "10.0.0.6"]
    # Determinism: re-resolving the SAME index always gives the SAME peer.
    assert _resolve_peers(persona, hosts_by_role, index=2)["domain-controller"] == "10.0.0.5"


def test_resolve_bindings_role_fanout_distributes_peers_across_two_dcs(tmp_path):
    """End-to-end (`resolve_traffic_bindings`, not just `_resolve_peers` in
    isolation): 4 `dmz-web`-persona`-carrying hosts assigned via ONE
    role-based assignment, with TWO hosts of the peer role present --
    instances must split across both, round-robin by resolved-host order,
    not all land on one."""
    hosts = [
        {"host_id": f"web{i:02d}", "role": "web-host", "vmid": 100010 + i, "ip": f"10.0.1.{i}"}
        for i in range(1, 5)
    ] + [
        {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
        {"host_id": "dc02", "role": "domain-controller", "vmid": 100004, "ip": "10.0.0.6"},
    ]
    db = _seeded_db(tmp_path, hosts=hosts)
    store = StateStore(db)
    try:
        # A synthetic-but-catalog-registered persona would need a real
        # catalog file; simplest is to reuse a real one whose ONLY
        # non-reserved peer_role is exactly what we want to fan out --
        # `bastion-jump` requires [corp-workstation] and emits inbound only
        # (no peer_role at all) per the catalog, so instead exercise this
        # directly against `_resolve_peers`/`_hosts_by_role` through a
        # hand-built resolution using the twin's real inventory.
        from axrange.o2control.traffic import _hosts_by_role, _resolve_peers
        from axrange.planner.inventory import build_twin_inventory
        from axrange.personas.schema import Emit, Persona

        twin = build_twin_inventory(store, TWIN_DEPLOY)
        hosts_by_role = _hosts_by_role(twin)
        persona = Persona(
            id="synthetic-fanout", os_family="linux", drive="scripted",
            emits=(Emit(proto="ldap", ports=(389,), peer_role="domain-controller"),),
        )
        web_hosts = hosts_by_role["web-host"]
        picks = [
            _resolve_peers(persona, hosts_by_role, index=i)["domain-controller"]
            for i in range(len(web_hosts))
        ]
    finally:
        store.close()
    # Both DCs get at least one instance's traffic -- not all 4 on one.
    assert set(picks) == {"10.0.0.5", "10.0.0.6"}


def test_resolve_bindings_reserved_peer_roles_need_no_resolution(tmp_path):
    """`power-user-dev` emits to reserved peer_roles (git/SSH/HTTPS ->
    registries+CI is modeled as... check corp-workstation's own DNS emit is
    'resolver', not reserved -- use a synthetic persona-free path instead:
    directly exercise `_resolve_peers` with a persona whose emits use only
    reserved roles."""
    from axrange.o2control.traffic import _resolve_peers
    from axrange.personas.schema import Emit, Persona

    persona = Persona(
        id="synthetic", os_family="linux", drive="scripted",
        emits=(Emit(proto="https", ports=(443,), peer_role="internet"),),
    )
    peers = _resolve_peers(persona, {})
    assert peers == {}


def test_resolve_bindings_rejects_duplicate_host_claim(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[
                AssignmentSpec(node="ws01", persona=_SIMPLE_PERSONA),
                AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA),
            ],
        )
        with pytest.raises(TrafficPlanError, match="claimed by both"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_bindings_no_vmid_raises(tmp_path):
    """A host recorded with no vmid (twin deploy incomplete) can't carry a
    persona instance."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id=TWIN_DEPLOY, scenario_id="s", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{TWIN_DEPLOY}-plan", ir_hash="h",
            placement={}, tier="te",
        )
        store.insert_ops(TWIN_DEPLOY, [
            _op_row(TWIN_DEPLOY, "op-clone-ws01", "clone-vm", "OP-HOST-010", {"host": "ws01"},
                    inputs={"node_role": "corp-workstation"}, outputs=None,
                    status=OpStatus.PENDING),
        ])
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
        )
        with pytest.raises(TrafficPlanError, match="no recorded vmid"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_bindings_unknown_target_twin_404(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target="nonexistent",
            assignments=[AssignmentSpec(role="x", persona="corp-workstation")],
        )
        with pytest.raises(TrafficPlanError) as exc:
            resolve_traffic_bindings(spec, store)
        assert exc.value.status == 404
    finally:
        store.close()


def test_resolve_bindings_wrong_tier_rejected(tmp_path):
    db = _seeded_db(tmp_path, deploy_id="o2-1", tier="o2")
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target="o2-1", assignments=[AssignmentSpec(role="x", persona="corp-workstation")],
        )
        with pytest.raises(TrafficPlanError, match="tier=.*'te'"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_resolve_bindings_replay_binding(tmp_path):
    hosts = _HOSTS + [
        {"host_id": "hist01", "role": "historian", "vmid": 100006, "ip": "10.0.0.9"},
    ]
    db = _seeded_db(tmp_path, hosts=hosts)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            replay=[ReplayBindingSpec(
                template="/tmp/historian.pcap", onto_role="historian", orig_ip="10.9.9.9",
            )],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert len(res.resolved) == 1
    ra = res.resolved[0]
    assert ra.host_id == "hist01"
    assert ra.persona.drive == "replay"
    assert ra.extra == {
        "template_pcap_path": "/tmp/historian.pcap", "orig_ip": "10.9.9.9",
        "node_ip": "10.0.0.9", "iface": "eth0",
    }


def test_resolve_bindings_replay_requires_orig_ip(tmp_path):
    hosts = _HOSTS + [
        {"host_id": "hist01", "role": "historian", "vmid": 100006, "ip": "10.0.0.9"},
    ]
    db = _seeded_db(tmp_path, hosts=hosts)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            replay=[ReplayBindingSpec(template="/tmp/x.pcap", onto_role="historian")],
        )
        with pytest.raises(TrafficPlanError, match="orig_ip"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Op composition
# ---------------------------------------------------------------------------
def test_compose_traffic_ops_one_op_per_instance_tier_tfc(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    plan = compose_traffic_ops(res, f"tfc-{TWIN_DEPLOY}")
    assert plan.tier == "tfc"
    assert len(plan.ops) == 2
    for op in plan.ops.values():
        assert op.op_name == "drive-persona"
        assert op.inputs_resolved["persona_id"] == _SIMPLE_PERSONA
        # review fix (2026-07-25): os_family must be PERSISTED into the op's
        # inputs, or `_stop_instances` can't recover it at destroy/drop-diff
        # time and silently stops a Windows persona via the Linux path.
        assert op.inputs_resolved["os_family"] == "linux"  # dmz-web is linux


def test_compose_traffic_ops_persists_os_family_for_windows_persona(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    plan = compose_traffic_ops(res, f"tfc-{TWIN_DEPLOY}")
    (op,) = plan.ops.values()
    assert op.inputs_resolved["os_family"] == "windows"


# ---------------------------------------------------------------------------
# Apply / idempotent re-apply / tfc tagging
# ---------------------------------------------------------------------------
def test_apply_traffic_profile_tags_tfc_and_completes(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)
    assert status.status == "completed"
    assert status.deploy_id is not None
    assert status.twin == TWIN_DEPLOY
    assert len(backend.calls) == 2  # ws01 + ws02

    store = StateStore(db)
    try:
        d = store.get_deploy(status.deploy_id)
        assert d.tier == "tfc"
        assert d.plan_id == f"tfc-{TWIN_DEPLOY}"
    finally:
        store.close()


def test_apply_by_NAME_target_preserves_roles_block(tmp_path):
    """W1 dev45 live-verify regression (2026-08-04): when `target:` is a
    scenario NAME (not an already-minted deploy id), `apply_traffic_profile`
    rebuilds the spec with the resolved concrete id -- and that rebuild
    DROPPED the `roles:` map (`TrafficProfileSpec(target=..., assignments=...,
    replay=...)` silently defaulted `roles` to `[]`). So an assignment that
    addresses a role only the profile's own `roles:` block assigns matched no
    host and hard-failed -- the exact "roles resolve before assignments"
    sequencing W1 exists to guarantee, broken on the name-target path.

    The whole unit suite missed it because every OTHER apply test targets by
    a literal deploy id (`TWIN_DEPLOY`), which skips the rebuild branch
    entirely; this test targets by NAME (`twin-scn`, the seeded twin's
    scenario_id) so the branch actually fires. Fixed with
    `dataclasses.replace()`, which carries every field (LES-037)."""
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target="twin-scn",  # the NAME, not TWIN_DEPLOY -> triggers the rebuild
        roles=[RoleAssignmentSpec(node="ws01", role="special-role")],
        assignments=[AssignmentSpec(role="special-role", persona=_SIMPLE_PERSONA)],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)

    # Pre-fix: this raised "role 'special-role' matches no host". Post-fix it
    # resolves via the surviving roles: block.
    assert status.status == "completed"
    assign = [c for c in backend.calls if c.get("role") == "special-role"]
    assert assign and assign[0]["host_id"] == "ws01", "assign-role for ws01 must run"
    drive = [c for c in backend.calls if c.get("persona_id") == _SIMPLE_PERSONA]
    assert drive and drive[0]["host_id"] == "ws01", "persona must drive on the roled host"


def test_apply_by_NAME_target_still_resolves_to_the_live_twin(tmp_path):
    """The companion happy-path pin: a NAME target with NO roles: block (the
    common case) also still resolves + applies -- the rebuild must not have
    broken the ordinary name-resolution the branch was added for."""
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target="twin-scn",
        assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
    )
    _, _backend, status = _apply(tmp_path, spec, db=db)
    assert status.status == "completed"
    assert status.twin == TWIN_DEPLOY  # resolved NAME -> concrete deploy id


def test_apply_traffic_profile_by_node(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)
    assert status.status == "completed"
    assert len(backend.calls) == 1
    assert backend.calls[0]["instance_id"] == "dc01-domain-controller"


def test_apply_traffic_profile_idempotent_reapply_reasserts(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _, backend, status1 = _apply(tmp_path, spec, db=db)
    assert len(backend.calls) == 1

    _, backend, status2 = _apply(tmp_path, spec, db=db, backend=backend)
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"
    # Force-reset re-asserts every apply (LES-057) -- a SECOND call happened,
    # with byte-identical content (a cluster no-op in effect).
    assert len(backend.calls) == 2
    assert backend.calls[0] == backend.calls[1]


def test_apply_traffic_profile_narrowing_reapply_stops_dropped_instance(tmp_path):
    """MUST-FIX (code review, LES-112): re-assertion (force-reset, LES-057)
    and de-scoping are two SEPARATE obligations. Apply two assignments, then
    re-apply with one DROPPED -- the dropped instance's driver must be
    STOPPED (not just left running forever until a full `tfc destroy`), and
    `tfc status` must stop reporting it once it's gone."""
    db = _seeded_db(tmp_path)
    spec1 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="dc01", persona="domain-controller"),
            AssignmentSpec(node="ws01", persona=_SIMPLE_PERSONA),
        ],
    )
    _, backend, status1 = _apply(tmp_path, spec1, db=db)
    assert status1.status == "completed"
    assert backend.running == {"dc01-domain-controller", f"ws01-{_SIMPLE_PERSONA}"}
    assert backend.stopped == []

    spec2 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _, backend, status2 = _apply(tmp_path, spec2, db=db, backend=backend)
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"

    # The dropped instance was stopped -- exactly one unit running now.
    assert backend.running == {"dc01-domain-controller"}
    assert any(iid == f"ws01-{_SIMPLE_PERSONA}" for _v, _d, iid in backend.stopped)

    # And `tfc status` reflects ONLY the current profile -- the dropped
    # instance is no longer reported as an active assignment.
    status = traffic_status(db_path=db, target=TWIN_DEPLOY)
    reported_personas = {a.persona for a in status.assignments}
    assert reported_personas == {"domain-controller"}
    reported_hosts = {h for a in status.assignments for h in a.hosts}
    assert "ws01" not in reported_hosts


def test_apply_traffic_profile_persona_swap_stops_old_starts_new(tmp_path):
    """The headline case (`docs/design/07-traffic-personas.md`): swap the
    persona bound to ONE host ("busy corp -> quiet OT"). `instance_id` is
    `f'{host_id}-{persona_id}'`, so a swap changes the systemd unit NAME --
    without the drop-diff, the OLD unit keeps running alongside the new one.
    Confirm exactly ONE unit is running on the host after the swap, not two."""
    spec1 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona="corp-workstation")],
    )
    # `corp-workstation` needs a full role roster (requires:); seed one.
    hosts = _HOSTS + [
        {"host_id": "prx01", "role": "egress-proxy", "vmid": 100004, "ip": "10.0.0.6"},
        {"host_id": "dns01", "role": "dns-resolver", "vmid": 100005, "ip": "10.0.0.7"},
        {"host_id": "file01", "role": "file-server", "vmid": 100006, "ip": "10.0.0.8"},
        {"host_id": "mail01", "role": "mail-server", "vmid": 100007, "ip": "10.0.0.9"},
        {"host_id": "print01", "role": "print-server", "vmid": 100008, "ip": "10.0.0.10"},
    ]
    db = _seeded_db(tmp_path, hosts=hosts)
    _, backend, status1 = _apply(tmp_path, spec1, db=db)
    assert status1.status == "completed"
    old_instance = "ws01-corp-workstation"
    assert backend.running == {old_instance}

    # Swap ws01 to a DIFFERENT persona -- a different instance_id/unit name.
    spec2 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona=_SIMPLE_PERSONA)],
    )
    _, backend, status2 = _apply(tmp_path, spec2, db=db, backend=backend)
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"

    new_instance = f"ws01-{_SIMPLE_PERSONA}"
    # Exactly ONE unit running on the host -- the new persona, not two.
    assert backend.running == {new_instance}
    assert old_instance not in backend.running
    assert any(iid == old_instance for _v, _d, iid in backend.stopped)

    status = traffic_status(db_path=db, target=TWIN_DEPLOY)
    assert {a.persona for a in status.assignments} == {_SIMPLE_PERSONA}


def test_apply_traffic_profile_seed_threaded_to_driver(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller", seed=1234)],
    )
    _, backend, _status = _apply(tmp_path, spec, db=db)
    assert backend.calls[0]["seed"] == 1234


def test_apply_traffic_profile_schedule_threaded_to_driver(tmp_path):
    """`schedule` rides the same "like os_family/seed" path all the way to
    the op's `inputs_resolved` (`compose_traffic_ops`) -- an unset one is
    `None`, an assignment-set one is the (already-normalized) string."""
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="dc01", persona="domain-controller", schedule="24x7"),
        ],
    )
    _, backend, _status = _apply(tmp_path, spec, db=db)
    assert backend.calls[0]["schedule"] == "24x7"


def test_apply_traffic_profile_schedule_unset_is_none_in_op_inputs(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _, backend, _status = _apply(tmp_path, spec, db=db)
    assert backend.calls[0]["schedule"] is None


def test_apply_traffic_profile_missing_target_node_raises(tmp_path, monkeypatch):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    from axrange.adapter_proxmox import load_config

    cfg = load_config(None)
    cfg.default_node = ""
    monkeypatch.setattr("axrange.adapter_proxmox.load_config", lambda *_a, **_k: cfg)
    with pytest.raises(TrafficPlanError, match="DEFAULT_NODE"):
        apply_traffic_profile(
            spec=spec, db_path=db, persona_backend=_FakePersonaBackend(),
        )


# ---------------------------------------------------------------------------
# Destroy -- tfc_-scoped ONLY; cascade from a te destroy.
# ---------------------------------------------------------------------------
def test_destroy_traffic_profile_stops_instances_and_destroys(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)

    result = destroy_traffic_profile(
        db_path=db, target=TWIN_DEPLOY, persona_backend=backend,
    )
    assert result.destroyed is True
    assert result.errors == []
    assert len(backend.stopped) == 2

    store = StateStore(db)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
        tfc = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert twin.status.value != "destroyed"
    assert tfc.status.value == "destroyed"


def test_destroy_traffic_profile_by_NAME_target_finds_the_deploy(tmp_path):
    """W1 dev45 live-verify regression (2026-08-04): `tfc destroy --target
    <name>` must resolve the scenario NAME to the concrete te id the tfc
    `plan_id` was keyed off, exactly as apply does -- otherwise it computes
    `tfc-<name>` and can NEVER find a deploy created by name (proof 2's whole
    point). The apply path got name-resolution in W1; the destroy path didn't.

    Applies AND destroys by NAME (`twin-scn`, the seeded twin's scenario_id) --
    the existing destroy test uses the concrete `TWIN_DEPLOY` for both, which
    skips the resolution the bug lived in."""
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target="twin-scn",  # NAME
        assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)

    # Pre-fix: raised "no traffic-profile deploy found for target 'twin-scn'".
    result = destroy_traffic_profile(db_path=db, target="twin-scn", persona_backend=backend)
    assert result.destroyed is True
    store = StateStore(db)
    try:
        assert store.get_deploy(status.deploy_id).status.value == "destroyed"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# `_stop_instances` real (non-fake-backend) fallback -- MUST-FIX (review,
# 2026-07-25): os_family must be recovered from the persisted op input and
# threaded into BOTH the placeholder `Persona` AND the `GuestAgent`, or a
# Windows persona's stop silently takes the Linux `stop_unit` path (LES-001
# -- reports "stopped" while its Scheduled Task keeps running and survives
# reboots). `test_destroy_traffic_profile_stops_instances_and_destroys`
# above only exercises the FAKE-backend seam (`persona_backend.
# stop_instance`), which never reaches this code path at all.
# ---------------------------------------------------------------------------
class _StopInstancesFakeExecResult:
    ok = True
    exit_code = 0
    stdout = ""
    stderr = ""


class _StopInstancesFakeGA:
    def __init__(self, client, node, os_family="linux"):
        self.os_family = os_family
        self.exec_calls: list[list[str]] = []
        self.shell_execs: list[str] = []

    def exec(self, vmid, argv, timeout_s=300):
        self.exec_calls.append(list(argv))
        return _StopInstancesFakeExecResult()

    def shell_exec(self, vmid, command, timeout_s=300):
        self.shell_execs.append(command)
        return _StopInstancesFakeExecResult()


def _patch_stop_instances_ga(monkeypatch):
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod

    created: dict[str, _StopInstancesFakeGA] = {}

    def factory(client, node, os_family="linux"):
        ga = _StopInstancesFakeGA(client, node, os_family=os_family)
        created["ga"] = ga
        return ga

    monkeypatch.setattr(ga_mod, "GuestAgent", factory)
    monkeypatch.setattr(rest_mod, "RestClient", lambda cfg: object())
    return created


def test_stop_instances_windows_persona_uses_scheduled_task_not_systemctl(monkeypatch):
    created = _patch_stop_instances_ga(monkeypatch)
    logs: list[str] = []
    _stop_instances(
        [{
            "instance_id": "ws01-corp-workstation",
            "vmid": 100001,
            "drive": "scripted",
            "persona_id": "corp-workstation",
            "os_family": "windows",
        }],
        cfg=SimpleNamespace(default_node="pxtest1"),
        persona_backend=None,
        log=logs.append,
    )
    ga = created["ga"]
    assert ga.os_family == "windows"
    assert ga.exec_calls == []  # never a raw `sh -c systemctl ...` argv
    assert len(ga.shell_execs) == 1
    assert "Unregister-ScheduledTask" in ga.shell_execs[0]
    assert logs == ["  - persona ws01-corp-workstation: stopped"]


def test_stop_instances_linux_persona_still_uses_systemctl(monkeypatch):
    """Regression guard: a Linux persona's stop must still go through
    `stop_unit` (`ga.exec`), never `ga.shell_exec` -- the os_family
    recovery fix must not flip the Linux path too."""
    created = _patch_stop_instances_ga(monkeypatch)
    logs: list[str] = []
    _stop_instances(
        [{
            "instance_id": "bkp01-backup-server",
            "vmid": 100002,
            "drive": "scripted",
            "persona_id": "backup-server",
            "os_family": "linux",
        }],
        cfg=SimpleNamespace(default_node="pxtest1"),
        persona_backend=None,
        log=logs.append,
    )
    ga = created["ga"]
    assert ga.os_family == "linux"
    assert ga.shell_execs == []
    assert len(ga.exec_calls) == 1
    assert ga.exec_calls[0][:2] == ["sh", "-c"]
    assert logs == ["  - persona bkp01-backup-server: stopped"]


def test_stop_instances_missing_os_family_defaults_to_linux(monkeypatch):
    """Back-compat: an already-persisted op row from before this fix has no
    `os_family` key at all -- must default to linux (unchanged prior
    behavior), not raise."""
    created = _patch_stop_instances_ga(monkeypatch)
    logs: list[str] = []
    _stop_instances(
        [{
            "instance_id": "bkp01-backup-server",
            "vmid": 100002,
            "drive": "scripted",
            "persona_id": "backup-server",
        }],
        cfg=SimpleNamespace(default_node="pxtest1"),
        persona_backend=None,
        log=logs.append,
    )
    ga = created["ga"]
    assert ga.os_family == "linux"
    assert logs == ["  - persona bkp01-backup-server: stopped"]


def test_destroy_traffic_profile_no_deploy_raises_404(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(TrafficPlanError) as exc:
        destroy_traffic_profile(db_path=db, target=TWIN_DEPLOY)
    assert exc.value.status == 404


def test_destroy_traffic_profile_refuses_non_tfc_deploy(tmp_path):
    db = _seeded_db(tmp_path)  # TWIN_DEPLOY itself is tier="te"
    with pytest.raises(TrafficPlanError, match="not 'tfc'"):
        destroy_traffic_profile(db_path=db, deploy_id=TWIN_DEPLOY)


def test_cascade_destroy_tfc_for_te_removes_standalone_profile(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _apply(tmp_path, spec, db=db)

    result = cascade_destroy_tfc_for_te(
        db_path=db, te_deploy_id=TWIN_DEPLOY, persona_backend=_FakePersonaBackend(),
    )
    assert result is not None
    assert result.destroyed is True


def test_cascade_destroy_tfc_for_te_no_profile_returns_none(tmp_path):
    db = _seeded_db(tmp_path)
    result = cascade_destroy_tfc_for_te(db_path=db, te_deploy_id=TWIN_DEPLOY)
    assert result is None


def test_cmd_destroy_cascades_to_standalone_traffic_profile(tmp_path, monkeypatch):
    """A real `axrange te destroy` (`axrange.cli.main.cmd_destroy`) also
    sweeps a standalone traffic-profile deploy bound to it -- exercised
    through the actual CLI verb, mirroring `tests/test_cli.py`'s tap-cascade
    coverage for the SAME S3-style wiring."""
    import argparse

    import axrange.adapter_proxmox as adapter_mod
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller")],
    )
    _, _backend, status = _apply(tmp_path, spec, db=db_path)
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
        tfc = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert twin.status.value == "destroyed"
    assert tfc.status.value == "destroyed"


def test_traffic_status_not_applied(tmp_path):
    db = _seeded_db(tmp_path)
    status = traffic_status(db_path=db, target=TWIN_DEPLOY)
    assert status.status == "not-applied"
    assert status.deploy_id is None


def test_traffic_status_after_apply(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(role="corp-workstation", persona=_SIMPLE_PERSONA)],
    )
    _apply(tmp_path, spec, db=db)
    status = traffic_status(db_path=db, target=TWIN_DEPLOY)
    assert status.status == "completed"
    assert status.assignments
    assert status.assignments[0].persona == _SIMPLE_PERSONA
    assert sorted(status.assignments[0].hosts) == ["ws01", "ws02"]


# ---------------------------------------------------------------------------
# PersonaBackend end-to-end through the REAL scripted driver -- proves the
# driver launches via `systemd-run --unit=`, never `nohup`, and that a
# same-seed re-render is byte-identical (LES-103's "verify against a real
# round-trip", plus the seed-determinism requirement).
# ---------------------------------------------------------------------------
class _FakeExecResult:
    ok = True
    exit_code = 0
    stdout = ""
    stderr = ""


class _FakeGuestAgent:
    def __init__(self, client, node, os_family="linux"):
        self.node = node
        self.os_family = os_family
        self.files: dict[str, str] = {}
        self.exec_calls: list[list[str]] = []
        self.shell_execs: list[str] = []

    def exec(self, vmid, argv, timeout_s=300):
        self.exec_calls.append(list(argv))
        return _FakeExecResult()

    def shell_exec(self, vmid, command, timeout_s=300):
        # Windows driver paths (Get-Service/Get-ScheduledTask/etc.) go
        # through `ga.shell_exec` (PowerShell), never a raw argv `ga.exec` --
        # mirror `GuestAgent.shell_exec` closely enough for these tests
        # (the real one dispatches to `powershell -Command`; this fake just
        # records the command string).
        self.shell_execs.append(command)
        return _FakeExecResult()

    def write_file(self, vmid, path, content, mode=None):
        self.files[path] = content


def _persona_backend_with_fake_ga():
    holder: dict[str, _FakeGuestAgent] = {}

    def factory(client, node, os_family="linux"):
        ga = _FakeGuestAgent(client, node, os_family=os_family)
        holder["ga"] = ga
        return ga

    backend = PersonaBackend(config=object(), rest_client=object(), guest_agent_factory=factory)
    return backend, holder


def _drive(backend, **inputs):
    op = SimpleNamespace(op_name="drive-persona")
    ctx = SimpleNamespace(cluster=ClusterContext.single_node("pxtest1"))
    return backend.run(op, inputs, ctx)


def test_persona_backend_scripted_driver_uses_systemd_run_never_nohup():
    """`backup-server` is a LINUX scripted persona -- proves the Linux path
    (unchanged by the Windows port) still launches via `systemd-run --unit=`,
    never `nohup`."""
    backend, holder = _persona_backend_with_fake_ga()
    instance_id = "bkp01-backup-server"
    result = _drive(
        backend, persona_id="backup-server", drive="scripted",
        instance_id=instance_id, vmid=100050,
        peers={"file-server": "10.0.0.6"}, seed=42,
    )
    assert result == {"started": True}
    ga = holder["ga"]
    paths = instance_paths(instance_id)
    launch_src = ga.files[paths["dir"] + "/launch.sh"]
    assert "systemd-run --unit=" in launch_src
    assert "nohup" not in launch_src


def test_persona_backend_scripted_driver_windows_uses_scheduled_task_never_nohup():
    """`corp-workstation` is a WINDOWS scripted persona (the headline
    Windows-port case, `docs/WORK_ORDER_persona_library.md`) -- must launch
    via a Scheduled Task (`Register-ScheduledTask`/`Start-ScheduledTask`),
    never `Start-Process` (LES-086's Windows analog) and never `nohup`."""
    backend, holder = _persona_backend_with_fake_ga()
    instance_id = "ws01-corp-workstation"
    result = _drive(
        backend, persona_id="corp-workstation", drive="scripted",
        instance_id=instance_id, vmid=100050,
        peers={"domain-controller": "10.0.0.5"}, seed=42,
    )
    assert result == {"started": True}
    ga = holder["ga"]
    paths = instance_paths(instance_id, os_family="windows")
    launch_src = ga.files[paths["dir"] + "\\launch.ps1"]
    assert "Register-ScheduledTask" in launch_src
    assert "Start-ScheduledTask" in launch_src
    assert "Start-Process" not in launch_src
    assert "nohup" not in launch_src
    client_src = ga.files[paths["script"]]
    assert "[System.Net.Sockets.TcpClient]::new(" in client_src


def test_persona_backend_schedule_threads_to_scripted_render_windows():
    """End-to-end through the REAL scripted driver + Windows render:
    `schedule="24x7"` passed on the drive-persona op's inputs must force
    every emit's gate to `Gate='always'` in the rendered client, overriding
    `corp-workstation`'s own diurnal cadence."""
    backend, holder = _persona_backend_with_fake_ga()
    instance_id = "ws01-corp-workstation"
    _drive(
        backend, persona_id="corp-workstation", drive="scripted",
        instance_id=instance_id, vmid=100050,
        peers={"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"},
        seed=42, schedule="24x7",
    )
    ga = holder["ga"]
    paths = instance_paths(instance_id, os_family="windows")
    client_src = ga.files[paths["script"]]
    assert "Gate='business_hours'" not in client_src
    assert "Gate='always'" in client_src


def test_persona_backend_schedule_unset_leaves_cadence_derived_gate_windows():
    """BACKWARD-COMPAT: no `schedule` on the op's inputs (the `.get()`
    default) must render the SAME diurnal-gated client as before this
    override existed."""
    backend, holder = _persona_backend_with_fake_ga()
    instance_id = "ws01-corp-workstation"
    _drive(
        backend, persona_id="corp-workstation", drive="scripted",
        instance_id=instance_id, vmid=100050,
        peers={"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"},
        seed=42,
    )
    ga = holder["ga"]
    paths = instance_paths(instance_id, os_family="windows")
    client_src = ga.files[paths["script"]]
    assert "Gate='business_hours'" in client_src


def test_persona_backend_seed_determinism_byte_stable_render(tmp_path):
    """Two independent drives of the SAME persona/instance/seed render
    byte-identical client scripts -- Linux and Windows both."""
    backend1, holder1 = _persona_backend_with_fake_ga()
    backend2, holder2 = _persona_backend_with_fake_ga()
    kwargs = dict(
        persona_id="backup-server", drive="scripted",
        instance_id="bkp01-backup-server", vmid=100050,
        peers={"file-server": "10.0.0.6"}, seed=42,
    )
    _drive(backend1, **kwargs)
    _drive(backend2, **kwargs)
    paths = instance_paths("bkp01-backup-server")
    script1 = holder1["ga"].files[paths["script"]]
    script2 = holder2["ga"].files[paths["script"]]
    assert script1 == script2


def test_persona_backend_seed_determinism_byte_stable_render_windows(tmp_path):
    """Same proof as above, for the WINDOWS scripted client."""
    backend1, holder1 = _persona_backend_with_fake_ga()
    backend2, holder2 = _persona_backend_with_fake_ga()
    kwargs = dict(
        persona_id="corp-workstation", drive="scripted",
        instance_id="ws01-corp-workstation", vmid=100050,
        peers={"domain-controller": "10.0.0.5"}, seed=42,
    )
    _drive(backend1, **kwargs)
    _drive(backend2, **kwargs)
    paths = instance_paths("ws01-corp-workstation", os_family="windows")
    script1 = holder1["ga"].files[paths["script"]]
    script2 = holder2["ga"].files[paths["script"]]
    assert script1 == script2


def test_persona_backend_real_service_driver_asserts_via_extras():
    """`domain-controller` is a WINDOWS real-service persona (GAP-032: it
    now carries a `knobs.service_name: NTDS` catalog default too, but an
    explicit `service_name` extra -- exactly like a twin-config override --
    still wins). `PersonaBackend` threads `service_name`/`process_match`
    through from the op's `inputs_resolved` (`_DRIVER_EXTRA_KEYS`) exactly
    like the replay driver's extras. Being Windows, the assertion runs over
    `ga.shell_exec` (`Get-Service`), never a raw `systemctl` argv."""
    from axrange.personas import registry as persona_registry

    persona = persona_registry.get("domain-controller")
    assert persona is not None and persona.drive == "real-service"
    assert persona.os_family == "windows"

    backend, holder = _persona_backend_with_fake_ga()
    result = _drive(
        backend, persona_id="domain-controller", drive="real-service",
        instance_id="dc01-domain-controller", vmid=100003, peers={}, seed=None,
        service_name="wazuh-agent",
    )
    assert result["started"] is True
    ga = holder["ga"]
    assert ga.exec_calls == []  # windows path never touches raw exec/systemctl
    assert len(ga.shell_execs) == 1  # the Get-Service assertion
    assert "Get-Service" in ga.shell_execs[0]
    assert "wazuh-agent" in ga.shell_execs[0]


def test_persona_backend_real_service_driver_catalog_default_windows():
    """With NO explicit override, `domain-controller`'s own catalog
    `knobs.service_name: NTDS` (GAP-032) is what gets asserted."""
    backend, holder = _persona_backend_with_fake_ga()
    result = _drive(
        backend, persona_id="domain-controller", drive="real-service",
        instance_id="dc01-domain-controller", vmid=100003, peers={}, seed=None,
    )
    assert result["started"] is True
    ga = holder["ga"]
    assert len(ga.shell_execs) == 1
    assert "NTDS" in ga.shell_execs[0]


def test_persona_backend_real_service_driver_catalog_default_linux():
    """`file-server` is a LINUX real-service persona with a catalog
    `knobs.service_name: smbd` (GAP-032) -- asserted over `systemctl`, never
    `ga.shell_exec`."""
    backend, holder = _persona_backend_with_fake_ga()
    result = _drive(
        backend, persona_id="file-server", drive="real-service",
        instance_id="fs01-file-server", vmid=100004, peers={}, seed=None,
    )
    assert result["started"] is True
    ga = holder["ga"]
    assert ga.shell_execs == []
    assert len(ga.exec_calls) == 1
    assert "smbd" in ga.exec_calls[0]



def test_persona_backend_real_service_driver_fails_loud_with_no_binding():
    """The honest, un-bound case: no knobs, no threaded extra -- fail-loud,
    never a silent 'started' with nothing actually asserted (LES-001). Every
    shipped catalog real-service persona now carries a GAP-032 default
    binding, so this exercises an UNCATALOGED persona id (falls back to
    `PersonaBackend`'s empty-knobs placeholder `Persona`) to keep proving the
    genuinely-unbound case."""
    from axrange.planner.executor import AdapterError

    backend, _holder = _persona_backend_with_fake_ga()
    with pytest.raises(AdapterError, match="service_name"):
        _drive(
            backend, persona_id="not-in-the-catalog", drive="real-service",
            instance_id="dc01-not-in-the-catalog", vmid=100003, peers={}, seed=None,
        )


def test_persona_backend_unknown_drive_raises_adapter_error():
    from axrange.planner.executor import AdapterError

    backend, _holder = _persona_backend_with_fake_ga()
    with pytest.raises(AdapterError):
        _drive(
            backend, persona_id="corp-workstation", drive="not-a-real-drive",
            instance_id="x", vmid=1, peers={}, seed=None,
        )


def test_persona_backend_replay_driver_extras_threaded(tmp_path):
    template = tmp_path / "template.pcap"
    template.write_bytes(b"\x00\x01\x02\x03")
    backend, holder = _persona_backend_with_fake_ga()
    result = _drive(
        backend, persona_id="replay:historian", drive="replay",
        instance_id="hist01-replay-historian", vmid=100006, peers={}, seed=None,
        template_pcap_path=str(template), orig_ip="10.9.9.9", node_ip="10.0.0.9",
        iface="eth0",
    )
    assert result == {"started": True}
    ga = holder["ga"]
    paths = instance_paths("hist01-replay-historian")
    launch_src = ga.files[paths["dir"] + "/launch.sh"]
    assert "tcpreplay" in launch_src
    assert "systemd-run --unit=" in launch_src


# ---------------------------------------------------------------------------
# Full CLI argv-level dispatch (argparse -> tier/verb -> handler), proving
# the `tfc` grid wiring itself, not just the underlying engine functions.
# ---------------------------------------------------------------------------
def test_cli_tfc_validate_and_deploy_argv_dispatch(tmp_path, monkeypatch):
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "traffic-profile:\n"
        f"  twin: {TWIN_DEPLOY}\n"
        "  assignments:\n"
        "    - node: dc01\n"
        "      persona: domain-controller\n"
    )

    rc = climain.main(["tfc", "validate", str(profile_path), "--db", db_path])
    assert rc == 0

    monkeypatch.setattr(
        "axrange.o2control.traffic.PersonaBackend",
        lambda cfg: _FakePersonaBackend(),
    )
    rc = climain.main([
        "tfc", "deploy", str(profile_path), "--db", db_path, "--node", "pxtest1",
    ])
    assert rc == 0

    rc = climain.main(["tfc", "status", "--db", db_path, "--target", TWIN_DEPLOY])
    assert rc == 0


def test_cli_tfc_info_text_shows_te_info_pointer_line(tmp_path, capsys):
    """Nice-to-have (code review 2026-07-25): `tfc info`'s text output ends
    with a pointer at `te info <twin>` for per-node access facts -- text
    only, substituting the real twin id; `--json` is unaffected."""
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "traffic-profile:\n"
        f"  twin: {TWIN_DEPLOY}\n"
        "  assignments:\n"
        "    - node: dc01\n"
        "      persona: domain-controller\n"
    )

    rc = climain.main(["tfc", "info", str(profile_path), "--db", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"axrange te info {TWIN_DEPLOY}" in out
    assert "per-node access facts" in out

    rc = climain.main(["tfc", "info", str(profile_path), "--db", db_path, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "axrange te info" not in json.dumps(payload)


def test_cli_tfc_destroy_argv_dispatch_refuses_without_allow_and_then_destroys(
    tmp_path, monkeypatch,
):
    """`axrange tfc destroy` end to end via the real argv parser: refused
    without `--allow-destroy`, then tears down cleanly with it, leaving the
    target twin untouched."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.adapter_proxmox.guest_agent as ga_mod
    import axrange.adapter_proxmox.rest as rest_mod
    from axrange.cli import main as climain

    db_path = _seeded_db(tmp_path)
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "traffic-profile:\n"
        f"  twin: {TWIN_DEPLOY}\n"
        "  assignments:\n"
        "    - node: dc01\n"
        "      persona: domain-controller\n"
    )
    monkeypatch.setattr(
        "axrange.o2control.traffic.PersonaBackend",
        lambda cfg: _FakePersonaBackend(),
    )
    rc = climain.main([
        "tfc", "deploy", str(profile_path), "--db", db_path, "--node", "pxtest1",
    ])
    assert rc == 0

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

    # Refused without --allow-destroy.
    rc = climain.main(["tfc", "destroy", "--db", db_path, "--target", TWIN_DEPLOY])
    assert rc != 0

    rc = climain.main([
        "tfc", "destroy", "--db", db_path, "--target", TWIN_DEPLOY,
        "--adapter-config", "/nonexistent.conf", "--allow-destroy",
    ])
    assert rc == 0

    store = StateStore(db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
    finally:
        store.close()
    assert twin.status.value != "destroyed"


# ---------------------------------------------------------------------------
# ADVERSARIAL ADDITIONS (tester pass, docs/WORK_ORDER_traffic_tier.md §4)
# ---------------------------------------------------------------------------


def test_hosts_by_role_deterministic_pick_is_by_host_id_not_ip(tmp_path):
    """`_resolve_peers` picks `hosts_by_role[role][0]` after `_hosts_by_role`
    sorts by `host_id` -- confirm the determinism key is actually `host_id`,
    not IP (a host whose `host_id` sorts first but whose IP is numerically
    LATER must still win), so a future refactor that swaps the sort key
    can't silently change which peer gets addressed without a test noticing."""
    from axrange.planner.inventory import HostInfo, HostNet

    class _FakeTwin:
        hosts = [
            HostInfo(host_id="dc-b", vmid=1, role="domain-controller",
                     nets=[HostNet(net_id="lan", ip="10.0.0.1")]),
            HostInfo(host_id="dc-a", vmid=2, role="domain-controller",
                     nets=[HostNet(net_id="lan", ip="10.0.0.99")]),
        ]

    by_role = _hosts_by_role(_FakeTwin())
    picked = by_role["domain-controller"][0]
    assert picked.host_id == "dc-a"
    assert picked.nets[0].ip == "10.0.0.99"


def test_role_surfaces_in_inventory_for_expanded_twin_config_round_trip(tmp_path):
    """The twin-config expander path end to end (docs/design/07-traffic-personas.md's
    own "confirm the twin-config expander path" probe): expand
    `corp-enterprise` (small param override to keep this fast), build a REAL
    plan, persist through the REAL StateStore round-trip, and confirm
    `dc01`/workstation nodes carry their config-declared `role:` into the
    published inventory -- not just a hand-authored IR fixture."""
    from axrange.manifest.loader import load_registry
    from axrange.planner import build_plan
    from axrange.planner.state import op_to_row
    from axrange.twinconfig.expand import expand_twin_config, materialize_twin_config

    ir_dict, _params = expand_twin_config(
        "corp-enterprise", catalog_dir=str(_CATALOG / "twins"),
        param_overrides={"N_WORKSTATIONS": "2"},
    )
    node_roles = {n["id"]: n.get("role") for n in ir_dict["layers"]["infra"]["nodes"]}
    assert node_roles.get("domain-controller") == "domain-controller"
    assert any(
        role == "corp-workstation" for nid, role in node_roles.items() if "workstation" in nid
    ), node_roles

    ir_path = materialize_twin_config(
        "corp-enterprise", catalog_dir=str(_CATALOG / "twins"),
        param_overrides={"N_WORKSTATIONS": "2"}, out_path=tmp_path / "corp-ent-rt.yaml",
    )
    ir = parse_ir(path=ir_path).ir

    # `tests/fixtures/images` is the one COMPLETE manifest set covering every
    # image id the starter twin-configs reference (test_twinconfig_cli.py's
    # own posture) -- needed here because plan_builder's Windows-only
    # `joins:` check reads a real os_family off the image manifest.
    manifests = load_registry(root="tests/fixtures")
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(ir, manifests, ctx, plan_id="corp-ent-rt-plan", raw_ir={})

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="corp-ent-rt-1", scenario_id=plan.scenario_id, scenario_version=1,
            scenario_schema_version=1, plan_id=plan.plan_id, ir_hash="h",
            placement={}, tier="te",
        )
        store.insert_ops(
            "corp-ent-rt-1", [op_to_row(op, "corp-ent-rt-1") for op in plan.ops.values()]
        )
        inv = build_twin_inventory(store, "corp-ent-rt-1")
    finally:
        store.close()

    roles = {h.host_id: h.role for h in inv.hosts}
    assert roles.get("domain-controller") == "domain-controller"
    ws_roles = {hid: r for hid, r in roles.items() if "workstation" in hid}
    assert ws_roles, roles
    assert all(r == "corp-workstation" for r in ws_roles.values())


def test_apply_traffic_profile_changed_seed_on_same_instance_reasserts_new_value(tmp_path):
    """The precise 'changed profile, not just re-applied unchanged' probe:
    same host/persona (same instance_id, same op_id) across two applies, but
    a DIFFERENT seed the second time -- the second backend call must carry
    the NEW seed, not the stale first one and not be skipped as a dedup."""
    db = _seeded_db(tmp_path)
    spec1 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller", seed=1)],
    )
    _, backend, status1 = _apply(tmp_path, spec1, db=db)
    assert backend.calls[-1]["seed"] == 1

    spec2 = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="dc01", persona="domain-controller", seed=99)],
    )
    _, backend, status2 = _apply(tmp_path, spec2, db=db, backend=backend)
    assert status2.deploy_id == status1.deploy_id
    assert len(backend.calls) == 2
    assert backend.calls[-1]["seed"] == 99


def test_apply_traffic_profile_full_playout_never_uses_nohup(tmp_path, monkeypatch):
    """Inspect the launch string emitted through the REAL `apply_traffic_
    profile` -> `run_deploy_executor` -> `PersonaBackend.run` playout path
    (not `PersonaBackend.run()` called directly in isolation, unlike
    `test_persona_backend_scripted_driver_uses_systemd_run_never_nohup`
    above) -- confirms the systemd-run/nohup guard survives end to end
    through the tfc lifecycle. A synthetic no-`requires`/no-`emits` scripted
    persona is registered as a test seam (bypassing `_check_requires`/
    `_resolve_peers` plumbing, which is exercised separately elsewhere) so
    this test is purely about the launch-string mechanics, not persona
    content."""
    import axrange.personas.registry as persona_registry_mod
    from axrange.adapter_proxmox import load_config
    from axrange.o2control.traffic import PersonaBackend
    from axrange.personas.schema import Persona

    synthetic = Persona(id="test-scripted-noop", os_family="linux", drive="scripted")
    real_get = persona_registry_mod.get
    monkeypatch.setattr(
        persona_registry_mod, "get",
        lambda pid: synthetic if pid == synthetic.id else real_get(pid),
    )

    class _FakeExecResult:
        ok = True
        exit_code = 0
        stdout = ""
        stderr = ""

    class _FakeGA:
        def __init__(self, client, node, os_family="linux"):
            self.files: dict[str, str] = {}

        def exec(self, vmid, argv, timeout_s=300):
            return _FakeExecResult()

        def write_file(self, vmid, path, content, mode=None):
            self.files[path] = content

    holder: dict[str, _FakeGA] = {}

    def factory(client, node, os_family="linux"):
        ga = _FakeGA(client, node, os_family=os_family)
        holder["ga"] = ga
        return ga

    cfg = load_config(None)
    backend = PersonaBackend(config=cfg, rest_client=object(), guest_agent_factory=factory)

    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[AssignmentSpec(node="ws01", persona=synthetic.id)],
    )
    status = apply_traffic_profile(
        spec=spec, db_path=db, target_node="pxtest1", persona_backend=backend,
    )
    assert status.status == "completed"

    ga = holder["ga"]
    launch_paths = [p for p in ga.files if p.endswith("launch.sh")]
    assert launch_paths, ga.files.keys()
    for p in launch_paths:
        assert "systemd-run --unit=" in ga.files[p]
        assert "nohup" not in ga.files[p]


# ---------------------------------------------------------------------------
# W1 -- role/persona decoupling (`docs/WORK_ORDER_role_persona_tier.md`,
# Authority: user, 2026-08-04). Roles became tfc-owned and runtime-swappable;
# these pin the parts that make that TRUE rather than merely declared.
# ---------------------------------------------------------------------------
def test_roles_block_addressing_by_role_is_rejected():
    """The chicken-and-egg case: this block is what DEFINES roles, so
    selecting hosts BY role here would be circular and order-dependent."""
    with pytest.raises(TrafficPlanError, match="exactly one of 'node' / 'selector'"):
        parse_traffic_profile({"traffic-profile": {
            "target": "t1",
            "roles": [{"role": "x"}],
        }})


def test_roles_block_rejects_unknown_selector_key():
    with pytest.raises(TrafficPlanError, match="unknown selector key"):
        parse_traffic_profile({"traffic-profile": {
            "target": "t1",
            "roles": [{"selector": {"nope": "x"}, "role": "r"}],
        }})


def test_profile_with_only_roles_is_valid():
    """A role-map-only profile is legitimate: it re-labels a twin without
    driving any traffic at all."""
    spec = parse_traffic_profile({"traffic-profile": {
        "target": "t1", "roles": [{"node": "h1", "role": "file-server"}],
    }})
    assert len(spec.roles) == 1 and not spec.assignments


def test_compose_emits_assign_role_op_per_resolved_role():
    from axrange.o2control.traffic import ResolvedRole, TrafficPlanResolution, compose_traffic_ops

    res = TrafficPlanResolution(
        target_status="completed", twin=None,
        roles=[ResolvedRole(host_id="h1", role="file-server"),
               ResolvedRole(host_id="h2", role="db-replica")],
    )
    plan = compose_traffic_ops(res, "tfc-t1")
    ops = {o.op_name: o for o in plan.ops.values()}
    assert "assign-role" in ops
    by_host = {
        o.scope["host"]: o.inputs_resolved["role"]
        for o in plan.ops.values() if o.op_name == "assign-role"
    }
    assert by_host == {"h1": "file-server", "h2": "db-replica"}


def test_assign_role_op_holds_no_resource_categories():
    """It touches nothing, so it must not burn a throughput slot or take a
    lock -- the invariant `test_config_ops_use_ansible_pool` carves out."""
    from axrange.ops.registry import get_op

    op = get_op("assign-role")
    assert op is not None
    assert op.resource_categories == ()


def test_roles_from_shares_one_map_across_profiles(tmp_path):
    """The mechanism that answers "wouldn't a role tier avoid duplicating the
    map across profiles?" without a sixth tier
    (`docs/WORK_ORDER_role_persona_tier.md` §3.1)."""
    from axrange.o2control.traffic import load_traffic_profile

    (tmp_path / "roles.yaml").write_text(
        "traffic-profile:\n"
        "  roles:\n"
        "    - {node: h1, role: file-server}\n"
    )
    (tmp_path / "quiet.yaml").write_text(
        "traffic-profile:\n"
        "  target: twin-a\n"
        "  roles_from: roles.yaml\n"
        "  assignments:\n"
        "    - {role: file-server, persona: file-server}\n"
    )
    spec = load_traffic_profile(tmp_path / "quiet.yaml")
    assert [(r.node, r.role) for r in spec.roles] == [("h1", "file-server")]
    assert spec.target == "twin-a"


def test_roles_from_and_inline_roles_together_is_rejected(tmp_path):
    (tmp_path / "roles.yaml").write_text(
        "traffic-profile:\n  roles:\n    - {node: h1, role: r}\n"
    )
    (tmp_path / "p.yaml").write_text(
        "traffic-profile:\n"
        "  target: t\n"
        "  roles_from: roles.yaml\n"
        "  roles:\n"
        "    - {node: h2, role: r2}\n"
    )
    from axrange.o2control.traffic import load_traffic_profile

    with pytest.raises(TrafficPlanError, match="both 'roles:' and 'roles_from:'"):
        load_traffic_profile(tmp_path / "p.yaml")


def test_roles_from_does_not_chain(tmp_path):
    """One level only -- a shared map that itself points elsewhere is a
    config graph, not a shared constant."""
    from axrange.o2control.traffic import load_traffic_profile

    (tmp_path / "a.yaml").write_text(
        "traffic-profile:\n  roles_from: b.yaml\n  roles: []\n"
    )
    (tmp_path / "b.yaml").write_text(
        "traffic-profile:\n  roles:\n    - {node: h, role: r}\n"
    )
    (tmp_path / "p.yaml").write_text(
        "traffic-profile:\n  target: t\n  roles_from: a.yaml\n"
    )
    with pytest.raises(TrafficPlanError, match="do not chain"):
        load_traffic_profile(tmp_path / "p.yaml")


def test_shipped_tfc_profiles_parse_and_reference_real_personas():
    """Every profile shipped beside a twin-config must load and name only
    personas that RESOLVE -- the check the removed inert `persona:` field used
    to make against a field that reached nothing.

    "Resolve" means the shipped catalog OR the profile's own co-located
    `personas/` directory, which is the shipped bundle-local mechanism
    (`AXRANGE_PERSONAS_DIR`, `axrange/personas/registry.py`) and is how
    `twins/corpapcn-golden/` and `twins/svc-verify/` both
    carry personas derived FOR one twin rather than curated for every twin.
    Loaded via `load_catalog_dir` (which does not touch the module-level
    `PERSONAS`) rather than by setting the env var, so this test cannot leak
    a persona into another test's registry.

    A typo is still caught: an id in neither source fails exactly as before.
    """
    import pathlib

    from axrange.o2control.traffic import load_traffic_profile
    from axrange.personas import registry as persona_registry

    from axrange.twinconfig.manifest import load_twin_manifest

    # Every profile a `twins/<id>/twin.yaml` DECLARES (a `roles_from:`
    # fragment is not declared, and is not a profile), plus the legacy
    # `twin-configs/*/tfc-*.yaml` still held for Phase 4. A twin's personas
    # sit at the twin root, beside `tfc/`.
    profiles: list[tuple[pathlib.Path, pathlib.Path]] = []
    for d in sorted((_CATALOG / "twins").iterdir()):
        if (d / "twin.yaml").is_file():
            tfc = load_twin_manifest(d).tier("tfc")
            profiles += [(d / rel, d) for rel in (tfc.all_files() if tfc else [])]
    profiles += [(f, f.parent) for f in sorted((_CATALOG / "twin-configs").glob("*/tfc-*.yaml"))]
    assert profiles, "no shipped tfc profiles found"
    for f, twin_dir in profiles:
        spec = load_traffic_profile(f)
        assert spec.target, f"{f}: no target"
        local_dir = twin_dir / "personas"
        local = (
            persona_registry.load_catalog_dir(local_dir)
            if local_dir.is_dir() else {}
        )
        for a in spec.assignments:
            resolved = persona_registry.get(a.persona) or local.get(a.persona)
            assert resolved is not None, (
                f"{f}: persona {a.persona!r} is in neither the catalog nor "
                f"{local_dir}"
            )


# ---------------------------------------------------------------------------
# The `command` drive kind through PersonaBackend (2026-09-02,
# docs/WORK_ORDER_protocol_faithful_traffic.md §8.2).
#
# The point of these two: `traffic.py` needs NO new branch for a fourth drive
# kind. It calls `get_driver(drive)` and hands it the same
# peers/peer_sets/seed/schedule every driver already gets -- so a `command`
# assignment must run end-to-end through the UNCHANGED backend, and the
# de-scope stop path must reach it the same way.
# ---------------------------------------------------------------------------
def _register_command_persona(monkeypatch, persona_id="probe-client"):
    from axrange.personas import registry as persona_registry
    from axrange.personas.schema import Emit, Persona

    persona = Persona(
        id=persona_id, os_family="linux", drive="command",
        emits=(
            Emit(proto="dns", ports=(53,), peer_role="dns-resolver"),
            Emit(proto="https", ports=(443,), peer_role="web-tier"),
        ),
        requires=("dns-resolver", "web-tier"),
    )
    monkeypatch.setitem(persona_registry.PERSONAS, persona_id, persona)
    return persona


def test_persona_backend_drives_a_command_persona_with_no_new_branch(monkeypatch):
    """End-to-end through the REAL `CommandDriver` and the UNCHANGED
    `PersonaBackend`: the rendered client runs `dig`/`curl` at baked peer IPs
    and launches as a transient systemd unit, never `nohup`."""
    _register_command_persona(monkeypatch)
    backend, holder = _persona_backend_with_fake_ga()
    instance_id = "ws01-probe-client"
    result = _drive(
        backend, persona_id="probe-client", drive="command",
        instance_id=instance_id, vmid=100090,
        peers={"dns-resolver": "10.0.0.5", "web-tier": "10.0.0.9"}, seed=42,
    )
    assert result == {"started": True}
    ga = holder["ga"]
    paths = instance_paths(instance_id)
    client_src = ga.files[paths["script"]]
    assert "'dig', '@10.0.0.5'" in client_src
    assert "https://10.0.0.9:443/" in client_src
    launch_src = ga.files[paths["dir"] + "/launch.sh"]
    assert "systemd-run --unit=" in launch_src
    assert "nohup" not in launch_src


def test_persona_backend_command_render_is_seed_deterministic(monkeypatch):
    """Same seed + same instance => byte-identical delivered client, the same
    replay-identity property every other driver carries."""
    _register_command_persona(monkeypatch)
    peers = {"dns-resolver": "10.0.0.5", "web-tier": "10.0.0.9"}
    rendered = []
    for _ in range(2):
        backend, holder = _persona_backend_with_fake_ga()
        _drive(
            backend, persona_id="probe-client", drive="command",
            instance_id="ws01-probe-client", vmid=100090, peers=peers, seed=42,
        )
        rendered.append(
            holder["ga"].files[instance_paths("ws01-probe-client")["script"]]
        )
    assert rendered[0] == rendered[1]


def test_stop_instances_reaches_a_command_persona(monkeypatch):
    """The de-scope / destroy path is `get_driver(drive).stop(...)` too -- a
    `command` instance must be stoppable by the same persisted-op sweep."""
    _register_command_persona(monkeypatch)
    stopped: list[str] = []

    class _Backend:
        def stop_instance(self, vmid, drive, instance_id):
            stopped.append(f"{drive}:{instance_id}")

    _stop_instances(
        [{"instance_id": "ws01-probe-client", "vmid": 100090, "drive": "command",
          "persona_id": "probe-client"}],
        cfg=object(), persona_backend=_Backend(), log=lambda m: None,
    )
    assert stopped == ["command:ws01-probe-client"]
