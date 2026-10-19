"""Tests for `axrange.o2control.taps` (A2, `docs/design/06-taps-sensors.md`).

Covers, per `docs/WORK_ORDER_taps.md` par.3:
  - fan-out (one tap -> N sensors) emits N gretaps + N chained mirred entries
  - exclusivity (one tap -> one sensor) routes per-tap
  - distinct GRE key per (bridge, sensor)
  - tap_ tagging (gretap/mirror device names)
  - idempotent re-apply (Model C -- unchanged content is a no-op)
  - `destroy taps` removes ONLY tap_ resources, leaving te_/o2_ deploys intact
  - a te_ destroy still cascades ITS OWN (pre-A2, in-deploy) tap_ ops -- a
    regression check that this work doesn't touch that unrelated path

No live cluster: every test drives a real `StateStore` (tmp SQLite file) with
hand-seeded op rows (the same "derive purely from state.db" posture
`axrange.o2control.control._inventory_from_ops` uses) plus FAKE adapter/
teardown backends -- the idiom `tests/test_planner_executor.py` and
`tests/test_o2control.py` use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axrange.adapter_proxmox import ExternalSensor
from axrange.adapter_proxmox import naming as n
from axrange.ir.parser import parse_ir
from axrange.o2control import (
    TapPlanError,
    apply_tap_plan,
    cascade_destroy_for_te,
    destroy_taps,
)
from axrange.o2control.control import SensorInventoryEntry
from axrange.o2control.taps import (
    TapBindingSpec,
    TapPlanSpec,
    _bridge_for_net,
    _compose_ops,
    _delete_dropped_gretaps,
    _gateway_ip_for_net,
    _gre_naming_bridge,
    _is_route_ambiguous,
    _is_router_host,
    _resolve_bindings,
    _resolve_tap_target,
    _scan_te_inventory,
    _select_verify_injector,
    _tapped_iface_set,
    _TeInventory,
    merge_external_sensors,
    parse_tap_plan,
    resolve_live_target_deploy,
    resolve_tap_plan_bindings,
    resolve_target_deploy,
    tap_info,
    tap_status,
)
from axrange.planner import StateStore, build_plan, destroy_deploy
from axrange.planner.cluster import ClusterContext
from axrange.planner.state import OpRow, OpStatus, barrier_to_row, op_to_row
from axrange.planner.tappoints import TapPoint, TapPointShapeError


# ---------------------------------------------------------------------------
# Seeding helpers -- build synthetic TE / O2-surface deploys directly in
# state.db, without running a real IR-driven deploy (taps.py only ever reads
# `state.db`, so this is a faithful, much faster substitute).
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


TE_DEPLOY = "te-taps-test-1"


def _seed_te(store, *, deploy_id=TE_DEPLOY, with_native_gre=False):
    """A TE with two attached hosts (`docs/WORK_ORDER_tap_eligibility.md` D1,
    Authority: user, 2026-07-28 -- the te publishes its FULL attachment
    topology via `attach-vm-to-vnet`; there is no more tap-INTENT op at all,
    and no more tapped/untapped distinction -- ANY published interface is
    tap-plan-eligible):
      - h1 (vmid 100001), attached to net 'corp' nic 0. h1 also has a
        `wait-os-ready` (os_family=linux) on file, so it's a known-QGA
        host.
      - h2 (vmid 100002), attached to net 'dmz' nic 0. No `wait-os-ready`
        on file for h2 (h2 is NOT a known-QGA host -- exercises the "no
        known injector" fallback).
    'corp' is a net-level tap identifier (one attached host today, but
    net-scoped); 'h2' is a host-level tap identifier.
    """
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="te-scn", scenario_version=1,
        scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
        placement={"h1": "pxtest1", "h2": "pxtest1"}, tier="te",
    )
    rows = [
        _op_row(deploy_id, "op-clone-h1", "clone-vm", "OP-HOST-010", {"host": "h1"},
                inputs={"target_node": "pxtest1"}, outputs={"vmid": 100001}),
        _op_row(deploy_id, "op-clone-h2", "clone-vm", "OP-HOST-010", {"host": "h2"},
                inputs={"target_node": "pxtest1"}, outputs={"vmid": 100002}),
        _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
        _op_row(deploy_id, "op-br-dmz", "create-ovs-bridge", "OP-NET-020",
                {"net": "dmz"}, outputs={"bridge_name": "br-dmz"}),
        _op_row(
            deploy_id, "op-attach-h1-corp", "attach-vm-to-vnet", "OP-HOST-040",
            {"host": "h1", "net": "corp"}, inputs={"nic_index": 0},
        ),
        _op_row(
            deploy_id, "op-attach-h2-dmz", "attach-vm-to-vnet", "OP-HOST-040",
            {"host": "h2", "net": "dmz"}, inputs={"nic_index": 0},
        ),
        _op_row(deploy_id, "op-wait-os-h1", "wait-os-ready", "OP-HOST-080",
                {"host": "h1"}, inputs={"os_family": "linux"}),
    ]
    if with_native_gre:
        # A LEGACY pre-A2 in-deploy mirror plumbing shape (a scenario's OWN
        # sensors:/tap: declared at TE-deploy-time, retired by this work order
        # for NEW deploys) -- present so the "te destroy still cascades its
        # OWN tap_ ops" regression test has something to sweep. Unaffected by
        # this work: `planner/teardown.py`'s sweep is driven purely by op_name
        # presence in state.db, not by whether pass1_emit.py still emits it.
        rows.append(
            _op_row(
                deploy_id, "op-gre-corp-orig", "attach-gre-tunnel", "OP-MIR-010",
                {"net": "corp", "sensor": "orig-sensor"},
                outputs={"gre_port": n.gre_port_for_bridge_sensor("br-corp", "orig-sensor")},
            )
        )
    store.insert_ops(deploy_id, rows)
    return deploy_id


O2_DEPLOY = "o2-taps-test-1"


def _seed_o2_surface(store, *, deploy_id=O2_DEPLOY, sensors):
    """`sensors`: list of (sensor_id, sensor_type, transport_ip, vmid)."""
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="o2-surface", scenario_version=1,
        scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
        placement={}, tier="o2",
    )
    rows = []
    for sid, stype, ip, vmid in sensors:
        rows.append(_op_row(deploy_id, f"op-clone-{sid}", "clone-vm", "OP-HOST-010",
                             {"host": sid}, outputs={"vmid": vmid}))
        rows.append(_op_row(deploy_id, f"op-ds-{sid}", "deploy-sensor", "OP-MIR-050",
                             {"host": sid},
                             inputs={"sensor_id": sid, "sensor_type": stype}))
        rows.append(_op_row(deploy_id, f"op-attach-{sid}", "attach-vm-to-vnet",
                             "OP-HOST-040", {"host": sid, "net": "o2-transport"},
                             inputs={"ip": ip}))
    store.insert_ops(deploy_id, rows)
    return deploy_id


DEFAULT_SENSORS = [
    ("pktxpress-1", "tcpdump-sensor", "10.255.250.11", 200001),
    ("suricata-1", "suricata-sensor", "10.255.250.12", 200002),
    ("pktxpress-2", "tcpdump-sensor", "10.255.250.13", 200003),
]


class _FakeMirAdapter:
    """Returns canned/naming-derived outputs for the three OP-MIR ops this
    module composes. Used as BOTH `adapter=` (infra-class: attach-gre-tunnel,
    install-ovs-mirror) and `config_backend=` (config-class: verify-capture)
    -- same idiom as `tests/test_o2control.py`'s `_stand_up` helper."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == "attach-gre-tunnel":
            return {
                "gre_port": n.gre_port_for_bridge_sensor(
                    resolved_inputs["bridge_name"], resolved_inputs["sensor_id"]
                )
            }
        if op.op_name == "install-ovs-mirror":
            return {"hook_installed": True}
        if op.op_name == "verify-capture":
            return {"capture_verified": True, "token": "t"}
        raise AssertionError(f"unexpected op {op.op_name}")


class _FakeTeardown:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def _rec(self, kind, target):
        self.calls.append((kind, target))
        return "removed"

    def vm_name(self, node, vmid):
        return None

    def destroy_vm(self, node, vmid):
        return self._rec("vm", str(vmid))

    def delete_mirror_snippets(self, node, vmid):
        return self._rec("mirror", str(vmid))

    def delete_gretap(self, dev):
        return self._rec("gretap", dev)

    def delete_vnet(self, vnet_id):
        return self._rec("vnet", vnet_id)

    def delete_sdn_zone(self, zone_id):
        return self._rec("zone", zone_id)

    def apply_sdn(self):
        return self._rec("sdn-apply", "")

    def delete_ovs_bridge(self, node, b):
        return self._rec("ovs-bridge", b)


def _apply(tmp_path, bindings, *, adapter=None, db=None, target=TE_DEPLOY,
           teardown_backend=None):
    adapter = adapter or _FakeMirAdapter()
    db = db or str(tmp_path / "state.db")
    spec = TapPlanSpec(target=target, bindings=bindings)
    status = apply_tap_plan(
        spec=spec, db_path=db, adapter=adapter, config_backend=adapter,
        target_node="pxtest1", teardown_backend=teardown_backend,
    )
    return db, adapter, status


def _seeded_db(tmp_path, *, with_native_gre=False):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store, with_native_gre=with_native_gre)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    return db


# ---------------------------------------------------------------------------
# Tap-plan spec parsing
# ---------------------------------------------------------------------------
def test_parse_tap_plan_requires_top_level_key():
    with pytest.raises(TapPlanError, match="tap-plan:"):
        parse_tap_plan({"target": "te1", "bindings": []})


def test_parse_tap_plan_target_is_optional():
    """`docs/WORK_ORDER_tap_target_resolution.md`: `target:` is no longer
    required -- a PREDEFINED/library plan can omit it entirely, relying on a
    `--target`/API-body override at apply time instead."""
    spec = parse_tap_plan(
        {"tap-plan": {"bindings": [{"tap": "corp", "sensors": ["s1"]}]}}
    )
    assert spec.target is None
    assert len(spec.bindings) == 1


def test_parse_tap_plan_rejects_non_string_target():
    with pytest.raises(TapPlanError, match="target"):
        parse_tap_plan({
            "tap-plan": {
                "target": 123,
                "bindings": [{"tap": "corp", "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_empty_string_target():
    with pytest.raises(TapPlanError, match="target"):
        parse_tap_plan({
            "tap-plan": {
                "target": "",
                "bindings": [{"tap": "corp", "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_requires_nonempty_bindings():
    with pytest.raises(TapPlanError, match="bindings"):
        parse_tap_plan({"tap-plan": {"target": "te1", "bindings": []}})


def test_parse_tap_plan_rejects_duplicate_tap():
    with pytest.raises(TapPlanError, match="duplicate"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [
                    {"tap": "corp", "sensors": ["s1"]},
                    {"tap": "corp", "sensors": ["s2"]},
                ],
            }
        })


def test_parse_tap_plan_happy_path():
    spec = parse_tap_plan({
        "tap-plan": {
            "target": "te1",
            "bindings": [
                {"tap": "corp-net", "sensors": ["pktxpress-1", "suricata-1"]},
                {"tap": "dmz-host-h1", "sensors": ["pktxpress-2"]},
            ],
        }
    })
    assert spec.target == "te1"
    assert len(spec.bindings) == 2
    assert spec.bindings[0].sensors == ["pktxpress-1", "suricata-1"]


# ---------------------------------------------------------------------------
# Tap-point addressing (`docs/WORK_ORDER_tap_points.md`) -- `tap_point:`/
# `selector:` binding parse-time validation, alongside the legacy `tap:`.
# ---------------------------------------------------------------------------
def test_parse_tap_plan_accepts_tap_point_binding():
    spec = parse_tap_plan({
        "tap-plan": {
            "target": "te1",
            "bindings": [{"tap_point": "corp-lan", "sensors": ["s1"]}],
        }
    })
    assert spec.bindings[0].tap is None
    assert spec.bindings[0].tap_point == "corp-lan"
    assert spec.bindings[0].mode == "fanout"


def test_parse_tap_plan_accepts_selector_binding():
    spec = parse_tap_plan({
        "tap-plan": {
            "target": "te1",
            "bindings": [
                {"selector": {"scope": "boundary"}, "sensors": ["s1"], "mode": "exclusive"},
            ],
        }
    })
    b = spec.bindings[0]
    assert b.tap is None and b.tap_point is None
    assert b.selector == {"scope": "boundary"}
    assert b.mode == "exclusive"


def test_parse_tap_plan_requires_exactly_one_addressing_mode():
    with pytest.raises(TapPlanError, match="exactly one of"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"tap": "corp", "tap_point": "corp-lan", "sensors": ["s1"]}],
            }
        })
    with pytest.raises(TapPlanError, match="exactly one of"):
        parse_tap_plan({
            "tap-plan": {"target": "te1", "bindings": [{"sensors": ["s1"]}]},
        })


def test_parse_tap_plan_rejects_unknown_selector_key():
    with pytest.raises(TapPlanError, match="unknown key"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [
                    {"selector": {"direction": "ew"}, "sensors": ["s1"]},
                ],
            }
        })


def test_parse_tap_plan_rejects_empty_selector():
    with pytest.raises(TapPlanError, match="non-empty mapping"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_bad_mode():
    with pytest.raises(TapPlanError, match="mode"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [
                    {"tap_point": "corp-lan", "sensors": ["s1"], "mode": "sideways"},
                ],
            }
        })


def test_parse_tap_plan_exclusive_mode_requires_one_sensor():
    with pytest.raises(TapPlanError, match="mode=exclusive requires exactly 1 sensor"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [
                    {
                        "tap_point": "corp-lan", "sensors": ["s1", "s2"],
                        "mode": "exclusive",
                    },
                ],
            }
        })


def test_parse_tap_plan_rejects_duplicate_tap_point():
    with pytest.raises(TapPlanError, match="duplicate"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [
                    {"tap_point": "corp-lan", "sensors": ["s1"]},
                    {"tap_point": "corp-lan", "sensors": ["s2"]},
                ],
            }
        })


def test_parse_tap_plan_duplicate_check_distinguishes_addressing_modes():
    """A `tap: corp` and a `tap_point: corp` binding are NOT the same
    identity (different addressing vocabularies) -- the dedupe check must
    not conflate them."""
    spec = parse_tap_plan({
        "tap-plan": {
            "target": "te1",
            "bindings": [
                {"tap": "corp", "sensors": ["s1"]},
                {"tap_point": "corp", "sensors": ["s2"]},
            ],
        }
    })
    assert len(spec.bindings) == 2


# ---------------------------------------------------------------------------
# M2 (code review): a selector with every key present but DEGENERATE
# (`{id: null}`, ...) must be refused at PARSE time -- "match everything"
# must never be an emergent default.
# ---------------------------------------------------------------------------
def test_parse_tap_plan_rejects_selector_with_only_null_or_empty_values():
    with pytest.raises(TapPlanError, match="no EFFECTIVE criterion"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {"id": None}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_selector_scope_not_in_tap_scopes():
    """SHOULD-FIX: an unrecognized `scope` would otherwise silently match
    NOTHING (no real `TapPoint` ever has that scope) -- reading exactly like
    "the twin has none of these" instead of "you typo'd the selector\""""
    with pytest.raises(TapPlanError, match="unknown scope"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {"scope": "direction"}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_retired_leg_scope_with_actionable_message():
    """`docs/WORK_ORDER_tap_scopes.md` M6 -- `scope: leg` (renamed to `port`)
    gets its SPECIFIC replacement guidance, not the generic "unknown scope"
    a genuine typo deserves -- `_selector_from_dict` -> `scope_error_detail`
    (`axrange.planner.tappoints`), the SAME wording `TapPoint.__post_init__`
    and `twinconfig.expand._apply_tap_points` use."""
    with pytest.raises(TapPlanError, match="renamed to 'port'"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {"scope": "leg"}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_retired_host_scope_with_actionable_message():
    """`docs/WORK_ORDER_tap_scopes.md` M6 -- `scope: host` (dropped
    entirely) gets ITS specific replacement guidance too, distinct from
    `leg`'s wording."""
    with pytest.raises(TapPlanError, match="dropped -- express it as N"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {"scope": "host"}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_rejects_selector_labels_key_entirely():
    """`docs/WORK_ORDER_tap_eligibility.md` D3 (Authority: user, 2026-07-28):
    `selector: {labels: ...}` is REMOVED from the tap-plan grammar entirely
    -- `_SELECTOR_KEYS` no longer includes `labels`, so ANY `labels:` key
    (list-valued or not -- this used to be a narrower "must be a list of
    strings" check on a bare-string value) is now caught by the same
    "unknown key" check every other unrecognized selector key hits."""
    with pytest.raises(TapPlanError, match="unknown key"):
        parse_tap_plan({
            "tap-plan": {
                "target": "te1",
                "bindings": [{"selector": {"labels": ["ns"]}, "sensors": ["s1"]}],
            }
        })


def test_parse_tap_plan_accepts_selector_target_as_two_sequence():
    """The brief's own §4 boundary-target shape, `target: [seg-a, seg-b]`,
    is a VALID selector (not rejected at parse time) -- resolution-side
    matching is exercised in `tests/test_planner_tappoints.py`."""
    spec = parse_tap_plan({
        "tap-plan": {
            "target": "te1",
            "bindings": [
                {"selector": {"target": ["l3-ops", "l4-corp"]}, "sensors": ["s1"]},
            ],
        }
    })
    assert spec.bindings[0].selector == {"target": ["l3-ops", "l4-corp"]}


# ---------------------------------------------------------------------------
# TE inventory scan + tap-target resolution
# ---------------------------------------------------------------------------
def test_scan_te_inventory_reads_vmid_bridge_tapped_nics_and_qga_hosts(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()

    assert inv.host_vmid == {"h1": 100001, "h2": 100002}
    assert inv.net_bridge == {"corp": "br-corp", "dmz": "br-dmz"}
    assert inv.tappable["h1"] == [(0, "corp")]
    assert inv.tappable["h2"] == [(0, "dmz")]
    assert inv.qga_hosts == {"h1"}


def test_tapped_iface_set_and_tap_target_resolution(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    assert ifaces == {("h1", "corp", 0), ("h2", "dmz", 0)}

    # net-level identifier
    assert _resolve_tap_target(ifaces, "corp") == [("h1", "corp", 0)]
    # host-level identifier
    assert _resolve_tap_target(ifaces, "h2") == [("h2", "dmz", 0)]
    # unknown identifier
    assert _resolve_tap_target(ifaces, "nope") == []


# ---------------------------------------------------------------------------
# LES-065: producer/consumer round-trip. Everything above asserts each side
# independently -- the producer (pass1_emit.py) against its own expected
# shape in test_planner_dag.py, and the consumer (_scan_te_inventory, just
# above) against a HAND-SEEDED OpRow fixture (_seed_te). Neither catches the
# two sides' field names (`nic_index`/`net_id`) drifting apart. This test
# builds a REAL plan (pass1_emit's actual `attach-vm-to-vnet` ops -- the D1
# full-topology source, `docs/WORK_ORDER_tap_eligibility.md`, Authority:
# user, 2026-07-28; formerly `record-tap-intent`, retired by D4), persists
# it through the REAL `op_to_row` + `StateStore` path (exactly what
# `Executor.persist_plan` does -- no hand-seeded OpRow), and reads it back
# through A2's REAL `_scan_te_inventory` + `_tapped_iface_set`.
# ---------------------------------------------------------------------------
_ROUNDTRIP_IR = """
scenario_schema_version: 1
scenario: { id: roundtrip, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.0.0.0/24, vlan: 10 }
      - { id: dmz, cidr: 10.0.1.0/24, vlan: 11 }
    nodes:
      - { id: h-a, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - { id: h-b, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - id: r-edge
        kind: router
        nets: [{ net: corp, ip: 10.0.0.1 }, { net: dmz, ip: 10.0.1.1 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {cpu:1,ram:1Gi,disk:10Gi}
  apps: []
  activity: []
bindings: []
"""


def test_attach_vm_to_vnet_producer_consumer_roundtrip(tmp_path, manifests):
    """The (host, net_id, nic_index) triples pass1_emit resolved and emitted
    into `attach-vm-to-vnet` are exactly the triples A2 discovers (via
    `build_twin_inventory`'s full topology) after a real
    persist-to-state.db/read-back round trip -- no independent fixture on
    either side that could silently drift from the other."""
    ir = parse_ir(text=_ROUNDTRIP_IR).ir
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(ir, manifests, ctx, raw_ir={})
    plan.freeze()

    # The producer's own truth, read directly off the real OpInstance objects
    # (independent of any persistence round-trip).
    expected: set[tuple[str, str, int]] = set()
    for op in plan.ops.values():
        if op.op_name != "attach-vm-to-vnet":
            continue
        host = op.scope["host"]
        net_id = op.inputs_resolved["net_id"]
        nic_index = op.inputs_resolved["nic_index"]
        expected.add((host, net_id, nic_index))
    assert expected == {
        ("h-a", "corp", 0),
        ("h-b", "corp", 0),
        ("r-edge", "corp", 0),
        ("r-edge", "dmz", 1),
        # r-edge is `kind: router` -- its WAN uplink is unconditionally
        # eligible too (`docs/WORK_ORDER_tap_scopes.md` M3, unchanged by D1).
        ("r-edge", "__wan__", 2),
    }

    deploy_id = "roundtrip-te-1"
    store = StateStore(str(tmp_path / "state.db"))
    try:
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="roundtrip", scenario_version=1,
            scenario_schema_version=1, plan_id=plan.plan_id, ir_hash="h",
            placement={h: "px1" for h in ("h-a", "h-b", "r-edge")}, tier="te",
        )
        # The REAL persistence path (mirrors `Executor.persist_plan`):
        # `op_to_row`/`barrier_to_row` do the actual JSON serialization the
        # state store writes, not a hand-built dict like `_seed_te` above.
        rows = [op_to_row(op, deploy_id) for op in plan.ops.values()]
        rows += [barrier_to_row(b, deploy_id) for b in plan.barriers.values()]
        store.insert_ops(deploy_id, rows)

        inv = _scan_te_inventory(store, deploy_id)
    finally:
        store.close()

    discovered = _tapped_iface_set(inv)
    assert discovered == expected


# ---------------------------------------------------------------------------
# Binding resolution validation
# ---------------------------------------------------------------------------
def _sensor_map():
    return {
        "pktxpress-1": SensorInventoryEntry(
            sensor_id="pktxpress-1", type="tcpdump-sensor",
            transport_ip="10.255.250.11", deploy_id=O2_DEPLOY,
        ),
        "suricata-1": SensorInventoryEntry(
            sensor_id="suricata-1", type="suricata-sensor",
            transport_ip="10.255.250.12", deploy_id=O2_DEPLOY,
        ),
    }


def test_resolve_bindings_rejects_unknown_tap(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
        TapBindingSpec(tap="not-a-real-tap", sensors=["pktxpress-1"])
    ])
    with pytest.raises(TapPlanError, match="not a network or host"):
        _resolve_bindings(spec, ifaces, _sensor_map())


def test_resolve_bindings_rejects_unknown_sensor(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
        TapBindingSpec(tap="corp", sensors=["no-such-sensor"])
    ])
    with pytest.raises(TapPlanError, match="unknown sensor"):
        _resolve_bindings(spec, ifaces, _sensor_map())


def test_resolve_bindings_rejects_sensor_with_no_transport_ip(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    sensors = _sensor_map()
    sensors["pktxpress-1"] = SensorInventoryEntry(
        sensor_id="pktxpress-1", type="tcpdump-sensor", transport_ip=None,
        deploy_id=O2_DEPLOY,
    )
    spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
        TapBindingSpec(tap="corp", sensors=["pktxpress-1"])
    ])
    with pytest.raises(TapPlanError, match="no transport IP"):
        _resolve_bindings(spec, ifaces, sensors)


# ---------------------------------------------------------------------------
# SHOULD-FIX (code review): a `tap_point:` id ambiguous ACROSS scopes (a net
# named `corp--dmz` colliding with a derived boundary id, a net named
# `rtr-legs` colliding with a derived host id) must be REFUSED, not silently
# widened into the union of both matches' interfaces.
# ---------------------------------------------------------------------------
def test_resolve_bindings_rejects_ambiguous_tap_point_id_across_scopes(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    # Two DISTINCT points sharing the SAME id across DIFFERENT scopes -- the
    # exact collision an auto-derived catalog could produce (a net literally
    # named `corp--dmz` vs. a derived `corp--dmz` boundary id).
    points = [
        TapPoint(id="corp--dmz", scope="segment", target="corp"),
        TapPoint(id="corp--dmz", scope="boundary", target=("corp", "dmz")),
    ]
    spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
        TapBindingSpec(tap_point="corp--dmz", sensors=["pktxpress-1"])
    ])

    # The ambiguity check fires before `resolve_tap_point` is ever called
    # (short-circuits), so `twin`/`index` never need to be REAL objects here
    # -- any non-`None` placeholder satisfies `_resolve_binding_targets`'s
    # "were points=/twin=/index= supplied at all" precondition.
    with pytest.raises(TapPlanError, match="ambiguous"):
        _resolve_bindings(
            spec, ifaces, _sensor_map(), points=points, twin=object(),
            index=object(),
        )


# ---------------------------------------------------------------------------
# Config-declared EXTERNAL sensors as a tap target (follow-on to the
# XPress-SW protection change) -- an operator-managed sink like `XPress-SW`,
# declared via adapter.conf `EXTERNAL_SENSORS`, resolvable WITHOUT ever
# appearing in A1's standing O2 inventory.
# ---------------------------------------------------------------------------
XPRESS_SW = ExternalSensor(
    id="XPress-SW", vmid=250, transport_ip="10.255.250.20",
    type="packetxpress", capture_iface="gre-cap",
)


def test_merge_external_sensors_adds_new_and_a1_wins_on_collision():
    a1 = {
        "pktxpress-1": SensorInventoryEntry(
            sensor_id="pktxpress-1", type="tcpdump-sensor",
            transport_ip="10.255.250.11", deploy_id=O2_DEPLOY,
        ),
    }
    merged = merge_external_sensors(a1, [XPRESS_SW])
    assert set(merged) == {"pktxpress-1", "XPress-SW"}
    assert merged["XPress-SW"].transport_ip == "10.255.250.20"
    assert merged["XPress-SW"].deploy_id == ""  # the "no QGA reachable" marker

    # A1 wins on an id collision -- an operator's adapter.conf naming the
    # same id as a standing sensor must not shadow the authoritative one.
    a1_shadow = {
        "XPress-SW": SensorInventoryEntry(
            sensor_id="XPress-SW", type="tcpdump-sensor",
            transport_ip="10.255.250.99", deploy_id=O2_DEPLOY,
        ),
    }
    merged2 = merge_external_sensors(a1_shadow, [XPRESS_SW])
    assert merged2["XPress-SW"].transport_ip == "10.255.250.99"
    assert merged2["XPress-SW"].deploy_id == O2_DEPLOY


def test_merge_external_sensors_collision_emits_warning():
    """SHOULD-FIX #3 (code review, LES-001 family): an id collision is
    resolved deterministically (A1 wins) but must NOT be silent -- a
    binding that meant to reach the external sink would otherwise wire to
    A1's (different) transport_ip with nothing to explain why capture at
    the intended sink turns up empty."""
    a1_shadow = {
        "XPress-SW": SensorInventoryEntry(
            sensor_id="XPress-SW", type="tcpdump-sensor",
            transport_ip="10.255.250.99", deploy_id=O2_DEPLOY,
        ),
    }
    warnings: list[str] = []
    merge_external_sensors(a1_shadow, [XPRESS_SW], warnings=warnings)
    assert len(warnings) == 1
    assert "external sensor 'XPress-SW' shadowed" in warnings[0]
    assert "standing O2 sensor" in warnings[0]

    # No collision -> no warning.
    warnings2: list[str] = []
    merge_external_sensors({}, [XPRESS_SW], warnings=warnings2)
    assert warnings2 == []


def test_resolve_tap_plan_bindings_surfaces_external_sensor_collision_warning(tmp_path):
    """The collision warning also flows through `resolve_tap_plan_bindings`'s
    `TapPlanResolution.warnings` -- the same channel `apply_tap_plan` folds
    into its own `TapPlanStatus.warnings`."""
    db = _seeded_db(tmp_path)  # A1 already has pktxpress-1/suricata-1/pktxpress-2
    shadow = ExternalSensor(
        id="pktxpress-1", vmid=999, transport_ip="10.255.250.199",
    )
    store = StateStore(db)
    try:
        spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
            TapBindingSpec(tap="corp", sensors=["pktxpress-1"])
        ])
        resolution = resolve_tap_plan_bindings(
            spec, store, db_path=db, external_sensors=[shadow],
        )
    finally:
        store.close()
    # `corp` in `_seed_te`'s twin has only ONE tapped host (h1) -- fix round
    # S8 (`docs/WORK_ORDER_tap_scopes.md`) also promotes this single-port
    # `tap:` resolution from rx to both and logs its OWN warning, so this
    # collision-specific assertion filters for the one it's actually
    # testing rather than asserting an exact total count.
    collision_warnings = [w for w in resolution.warnings if "shadowed" in w]
    assert len(collision_warnings) == 1
    assert "external sensor 'pktxpress-1' shadowed" in collision_warnings[0]
    # A1 wins -- the REAL standing transport_ip is used, not the shadow's.
    assert resolution.sensor_by_id["pktxpress-1"].transport_ip == "10.255.250.11"


def test_resolve_tap_plan_bindings_accepts_config_external_sensor(tmp_path):
    """A binding naming `XPress-SW` -- NOT in A1's standing inventory at
    all -- resolves successfully once `external_sensors=[XPRESS_SW]` is
    threaded through, using the config-declared transport_ip."""
    db = _seeded_db(tmp_path)  # A1 only knows pktxpress-1/suricata-1/pktxpress-2
    store = StateStore(db)
    try:
        spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
            TapBindingSpec(tap="corp", sensors=["XPress-SW"])
        ])
        resolution = resolve_tap_plan_bindings(
            spec, store, db_path=db, external_sensors=[XPRESS_SW],
        )
    finally:
        store.close()
    assert resolution.sensor_by_id["XPress-SW"].transport_ip == "10.255.250.20"
    assert any("XPress-SW" in sids for sids in resolution.iface_new_sensors.values())

    # Without external_sensors=, the SAME binding is rejected -- proves the
    # param is actually load-bearing, not a no-op.
    store = StateStore(db)
    try:
        with pytest.raises(TapPlanError, match="unknown sensor"):
            resolve_tap_plan_bindings(spec, store, db_path=db)
    finally:
        store.close()


def test_compose_ops_wires_gretap_to_external_transport_ip_but_skips_verify_capture(
    tmp_path,
):
    """attach-gre-tunnel + install-ovs-mirror wire fully to the external
    sensor's config transport_ip (LES-012's flow-based gre-cap decap needs
    no key coordination) -- but NO verify-capture is composed for that pair:
    axRange must never build a QGA read into an operator-managed,
    hand-provisioned node."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        sensor_by_id = merge_external_sensors({}, [XPRESS_SW])
        iface_new_sensors = {("h1", "corp", 0): {"XPress-SW": "both"}}
        plan = _compose_ops(
            "tap-plan-ext", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    gre_ops = [o for o in plan.ops.values() if o.op_name == "attach-gre-tunnel"]
    assert len(gre_ops) == 1
    assert gre_ops[0].inputs_resolved["sensor_transport_ip"] == "10.255.250.20"

    mirror_ops = [o for o in plan.ops.values() if o.op_name == "install-ovs-mirror"]
    assert len(mirror_ops) == 1
    assert mirror_ops[0].inputs_resolved["tapped_nics"][0]["sensor_id"] == "XPress-SW"

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert verify_ops == []


def test_apply_tap_plan_end_to_end_with_config_external_sensor(tmp_path):
    """Full apply through the engine executor: a tap-plan naming a
    config-declared external sensor (read from a real adapter.conf's
    EXTERNAL_SENSORS -- `apply_tap_plan` has no direct `external_sensors=`
    param, it comes from `load_config(adapter_config)`) applies cleanly
    (gretap + mirror wired to its transport_ip), composing no verify-capture
    heartbeat for it."""
    db = _seeded_db(tmp_path)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20","type":"packetxpress",'
        '"capture_iface":"gre-cap"}]\n'
    )
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[TapBindingSpec(tap="corp", sensors=["XPress-SW"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter,
        target_node="pxtest1", adapter_config=str(conf),
    )
    assert status.status == "completed"
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 1
    assert gre_calls[0][1]["sensor_transport_ip"] == "10.255.250.20"
    assert not any(c[0] == "verify-capture" for c in adapter.calls)

    # MUST-FIX #1 (code review, LES-001 trap): the verify-capture skip is
    # correct -- the sink is operator-managed and unreachable over QGA -- but
    # must NEVER be silent. `tap apply` returning "completed" with zero proof
    # a frame reached XPress-SW is exactly the "green deploy, no capture"
    # failure LES-001 forbids; the machine-readable `warnings` field is where
    # the caller (CLI/API) surfaces it. `corp` here has only ONE tapped host
    # (h1), so fix round S8 (`docs/WORK_ORDER_tap_scopes.md`) ALSO logs its
    # own promotion warning -- filter for the capture-specific one.
    capture_warnings = [w for w in status.warnings if "capture UNVERIFIED" in w]
    assert len(capture_warnings) == 1
    assert "capture UNVERIFIED for external sensor 'XPress-SW'" in capture_warnings[0]
    assert "net 'corp'" in capture_warnings[0]
    assert "operator-managed sink" in capture_warnings[0]


def test_apply_tap_plan_standing_a1_sensor_emits_no_capture_warning(tmp_path):
    """The negative case: a plain apply against a STANDING A1 sensor (which
    DOES get a real verify-capture heartbeat) must carry NO capture-
    UNVERIFIED/injector-unreliable warning -- that class of warning is
    specific to an external-sensor skip or an unreliable injector fallback,
    not a blanket apply-time note. `corp` here has only ONE tapped host
    (h1), so fix round S8 (`docs/WORK_ORDER_tap_scopes.md`) DOES still log
    its own (unrelated, expected) single-port direction-promotion warning --
    filtered out here since it isn't what this test is about."""
    _seeded_db(tmp_path)
    db, adapter, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
    )
    assert status.status == "completed"
    assert any(c[0] == "verify-capture" for c in adapter.calls)
    unrelated_warnings = [w for w in status.warnings if "promoted from rx to both" not in w]
    assert unrelated_warnings == []


# ---------------------------------------------------------------------------
# Op composition -- fan-out / exclusivity / distinct GRE key / tap tagging
# ---------------------------------------------------------------------------
def test_compose_ops_fan_out_emits_n_gretaps_and_chained_mirred(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        ifaces = _tapped_iface_set(inv)
        sensor_by_id = _sensor_map()
        spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
            TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"]),
        ])
        iface_new_sensors = _resolve_bindings(spec, ifaces, sensor_by_id)
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    gre_ops = [o for o in plan.ops.values() if o.op_name == "attach-gre-tunnel"]
    assert len(gre_ops) == 2  # fan-out: one gretap per sensor
    assert {o.scope["sensor"] for o in gre_ops} == {"pktxpress-1", "suricata-1"}
    assert all(o.scope["net"] == "corp" for o in gre_ops)

    mirror_ops = [o for o in plan.ops.values() if o.op_name == "install-ovs-mirror"]
    assert len(mirror_ops) == 1  # only h1 (corp's tapped host) is affected
    mop = mirror_ops[0]
    assert mop.scope["host"] == "h1"
    tapped = mop.inputs_resolved["tapped_nics"]
    # Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    # the desired baseline starts EMPTY (no TE-original mirror to preserve --
    # the te never installs one of its own any more) -- ONLY this tap-plan's
    # own fan-out bindings populate it.
    sensor_ids = sorted(e["sensor_id"] for e in tapped)
    assert sensor_ids == ["pktxpress-1", "suricata-1"]
    # Each fan-out entry names a DISTINCT gretap input key, each resolved to
    # a distinct literal gretap device name (the chained-mirred targets).
    gre_devs = {
        e["sensor_id"]: mop.inputs_resolved[e["gre_port_input"]] for e in tapped
    }
    assert len(set(gre_devs.values())) == 2  # two distinct devices, no collision

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert len(verify_ops) == 2  # one heartbeat per new (net, sensor) pair
    assert {o.scope["sensor"] for o in verify_ops} == {"pktxpress-1", "suricata-1"}
    assert all(o.scope["host"] == "h1" for o in verify_ops)  # the only bound host


def test_compose_ops_exclusive_routes_per_tap(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        ifaces = _tapped_iface_set(inv)
        sensor_by_id = _sensor_map()
        sensor_by_id["pktxpress-2"] = SensorInventoryEntry(
            sensor_id="pktxpress-2", type="tcpdump-sensor",
            transport_ip="10.255.250.13", deploy_id=O2_DEPLOY,
        )
        spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
            TapBindingSpec(tap="h2", sensors=["pktxpress-2"]),
        ])
        iface_new_sensors = _resolve_bindings(spec, ifaces, sensor_by_id)
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    gre_ops = [o for o in plan.ops.values() if o.op_name == "attach-gre-tunnel"]
    assert len(gre_ops) == 1
    assert gre_ops[0].scope == {"net": "dmz", "sensor": "pktxpress-2"}

    mirror_ops = [o for o in plan.ops.values() if o.op_name == "install-ovs-mirror"]
    assert len(mirror_ops) == 1
    assert mirror_ops[0].scope["host"] == "h2"
    # h1 (untouched by this binding) never appears in the composed plan.
    assert all(o.scope.get("host") != "h1" for o in mirror_ops)

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert len(verify_ops) == 1
    assert verify_ops[0].scope["host"] == "h2"  # only candidate, even though
    # h2 was never a KNOWN qga_host (falls back to the sole candidate).


def test_compose_ops_distinct_gre_key_per_bridge_sensor(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        ifaces = _tapped_iface_set(inv)
        sensor_by_id = _sensor_map()
        spec = TapPlanSpec(target=TE_DEPLOY, bindings=[
            TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"]),
        ])
        iface_new_sensors = _resolve_bindings(spec, ifaces, sensor_by_id)
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    gre_ops = {o.scope["sensor"]: o for o in plan.ops.values()
               if o.op_name == "attach-gre-tunnel"}
    key_a = n.gre_key_for_bridge_sensor(
        gre_ops["pktxpress-1"].inputs_resolved["bridge_name"], "pktxpress-1")
    key_b = n.gre_key_for_bridge_sensor(
        gre_ops["suricata-1"].inputs_resolved["bridge_name"], "suricata-1")
    assert key_a != key_b
    dev_a = n.gre_port_for_bridge_sensor(
        gre_ops["pktxpress-1"].inputs_resolved["bridge_name"], "pktxpress-1")
    dev_b = n.gre_port_for_bridge_sensor(
        gre_ops["suricata-1"].inputs_resolved["bridge_name"], "suricata-1")
    assert dev_a != dev_b
    assert n.is_tap_gretap(dev_a) and n.is_tap_gretap(dev_b)


def test_compose_ops_no_bridge_recorded_raises(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    inv.net_bridge.pop("corp")  # simulate a missing bridge record
    sensor_by_id = _sensor_map()
    iface_new_sensors = {("h1", "corp", 0): {"pktxpress-1": "both"}}
    with pytest.raises(TapPlanError, match="no recorded OVS bridge"):
        _compose_ops("p", inv, sensor_by_id, iface_new_sensors, store, "pxtest1")


# ---------------------------------------------------------------------------
# apply_tap_plan -- full path with a fake adapter/config_backend
# ---------------------------------------------------------------------------
def test_apply_tap_plan_tags_tap_and_completes(tmp_path):
    db = _seeded_db(tmp_path)
    _, adapter, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"])],
        db=db,
    )
    assert status.status == "completed"
    assert status.deploy_id is not None
    assert status.target == TE_DEPLOY
    assert status.bindings == [
        TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"])
    ] or [b.tap for b in status.bindings] == ["corp"]

    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 2
    devs = {c[1]["sensor_id"]: n.gre_port_for_bridge_sensor(
        c[1]["bridge_name"], c[1]["sensor_id"]) for c in gre_calls}
    assert all(n.is_tap_gretap(d) for d in devs.values())

    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 1
    assert mirror_calls[0][1]["vmid"] == 100001

    verify_calls = [c for c in adapter.calls if c[0] == "verify-capture"]
    assert len(verify_calls) == 2
    # sensor_vmid resolved for the O2 sensor VM (read over QGA in production).
    assert all("sensor_vmid" in c[1] for c in verify_calls)

    # Deploy row itself: tier="te" (per module docstring -- the tap tag lives
    # on the individual resource NAMES, not the deploy-level tier).
    store = StateStore(db)
    try:
        d = store.get_deploy(status.deploy_id)
        assert d.tier == "te"
        assert d.plan_id == f"tap-{TE_DEPLOY}"
    finally:
        store.close()


def test_apply_tap_plan_exclusive_binding(tmp_path):
    db = _seeded_db(tmp_path)
    _, adapter, status = _apply(
        tmp_path, [TapBindingSpec(tap="h2", sensors=["pktxpress-2"])], db=db,
    )
    assert status.status == "completed"
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 1
    assert gre_calls[0][1]["sensor_id"] == "pktxpress-2"
    assert gre_calls[0][1]["net_id"] == "dmz"


def test_apply_tap_plan_idempotent_reapply_reasserts_unchanged_no_mutation(tmp_path):
    """M1: re-applying the SAME tap-plan content is a CLUSTER no-op (the
    reasserted install-ovs-mirror content is byte-identical), not literally
    "zero adapter calls" -- `install-ovs-mirror` is force-reset on every
    apply (M1) so a narrow/revert is correctly re-asserted rather than
    skipped because an identically-shaped op_id happens to already be
    `completed` (see the A->B->A test below for the bug this fixes)."""
    db = _seeded_db(tmp_path)
    _, adapter, status1 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    first_mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(first_mirror_calls) == 1

    _, _, status2 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        db=db, adapter=adapter,
    )
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"

    # A new adapter call DID happen (force-reset, by design) -- but its
    # CONTENT (the desired cluster state) is unchanged: no mutation.
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 2
    assert mirror_calls[0][1]["tapped_nics"] == mirror_calls[1][1]["tapped_nics"]


def test_apply_tap_plan_noop_reapply_leaves_verify_capture_op_intact(tmp_path):
    """LES-112 de-scope must be surgical: a re-apply with IDENTICAL bindings
    (same host, same net, same sensor -> same injector, same op_id) must
    NOT mark the verify-capture op destroyed -- it stays exactly the row it
    was (still COMPLETED from the first apply's run), never spuriously
    dropped just because a de-scope pass now exists."""
    db = _seeded_db(tmp_path)
    _apply(tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db)

    store = StateStore(db)
    try:
        deploy_row = store.find_deploy_for_plan(f"tap-{TE_DEPLOY}")
        assert deploy_row is not None
        deploy_id = deploy_row.deploy_id
        op_ids_before = {
            op.op_id for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    assert len(op_ids_before) == 1

    _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )

    store = StateStore(db)
    try:
        rows_after = {
            op.op_id: op.status for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    # The SAME op_id, still present, and NEVER destroyed.
    assert set(rows_after) == op_ids_before
    (op_id,) = op_ids_before
    assert rows_after[op_id] != OpStatus.DESTROYED
    assert rows_after[op_id] == OpStatus.COMPLETED


def test_apply_tap_plan_repoint_adds_new_ops_and_drops_old_gretap(tmp_path):
    db = _seeded_db(tmp_path)
    _, adapter, status1 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    calls_after_first = len(adapter.calls)

    # Re-point corp to a DIFFERENT sensor -- pktxpress-1 is fully DROPPED.
    teardown = _FakeTeardown()
    _, adapter, status2 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["suricata-1"])],
        db=db, adapter=adapter, teardown_backend=teardown,
    )
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"
    assert len(adapter.calls) > calls_after_first
    new_gre = [c for c in adapter.calls[calls_after_first:]
               if c[0] == "attach-gre-tunnel"]
    assert any(c[1]["sensor_id"] == "suricata-1" for c in new_gre)

    # The reasserted mirror content no longer names pktxpress-1 (and, being
    # sensor-agnostic, carries no TE-original entry to begin with).
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    last_sensor_ids = {e["sensor_id"] for e in mirror_calls[-1][1]["tapped_nics"]}
    assert last_sensor_ids == {"suricata-1"}

    # And its now-unused gretap was explicitly deleted (S2's drop mechanism).
    dropped_dev = n.gre_port_for_bridge_sensor("br-corp", "pktxpress-1")
    assert ("gretap", dropped_dev) in teardown.calls


def test_apply_tap_plan_dropped_pair_descopes_its_verify_capture_op(tmp_path):
    """LES-112: a re-apply that fully DROPS a (net, sensor) pair -- not just
    changes its injector, the pair vanishes entirely -- must also mark that
    pair's OLD verify-capture op DESTROYED. Same fixture/shape as
    `test_apply_tap_plan_repoint_adds_new_ops_and_drops_old_gretap` above,
    checking the ops-table side of that same re-point instead of the
    gretap-teardown side."""
    db = _seeded_db(tmp_path)
    _, adapter, status1 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    deploy_id = status1.deploy_id
    store = StateStore(db)
    try:
        old_verify_op_ids = {
            op.op_id for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    assert len(old_verify_op_ids) == 1

    # Re-point corp to a DIFFERENT sensor -- pktxpress-1's (net, sensor) pair
    # is fully dropped, not just re-injected-from-elsewhere.
    _, adapter, status2 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["suricata-1"])],
        db=db, adapter=adapter,
    )
    assert status2.deploy_id == deploy_id

    store = StateStore(db)
    try:
        verify_ops_after = {
            op.op_id: op.status for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    # The old (corp, pktxpress-1) heartbeat op is gone from the CURRENT
    # binding set -- it must be DESTROYED, never left pending/completed.
    (old_op_id,) = old_verify_op_ids
    assert old_op_id in verify_ops_after
    assert verify_ops_after[old_op_id] == OpStatus.DESTROYED
    # And a genuinely new op exists for (corp, suricata-1).
    new_op_ids = set(verify_ops_after) - old_verify_op_ids
    assert len(new_op_ids) == 1
    assert verify_ops_after[next(iter(new_op_ids))] == OpStatus.COMPLETED


def test_apply_tap_plan_narrow_a_to_b_to_a_reverts_correctly(tmp_path):
    """M1 -- the exact bug: apply corp->[px1] (A), apply corp->[px1,suri1]
    (B, fan-out), then apply corp->[px1] again (back to A). Content-hashing
    install-ovs-mirror's op_id would make the THIRD apply recompute the SAME
    op_id as the FIRST (already `completed`) and SKIP it -- leaving the
    cluster's mirror still fanned out to suri1 even though the operator
    "removed" it. The host must end at {px1} ONLY (sensor-agnostic te,
    2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md -- no TE-original entry
    to fall back to), and suri1's gretap must be torn down."""
    db = _seeded_db(tmp_path)
    _, adapter, _ = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    _, adapter, _ = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"])],
        db=db, adapter=adapter,
    )
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    fanned_out_sensor_ids = {e["sensor_id"] for e in mirror_calls[-1][1]["tapped_nics"]}
    assert fanned_out_sensor_ids == {"pktxpress-1", "suricata-1"}

    teardown = _FakeTeardown()
    _, adapter, status3 = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        db=db, adapter=adapter, teardown_backend=teardown,
    )
    assert status3.status == "completed"

    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    # A THIRD call happened at all -- the bug was that this call never fired
    # (skipped as "already completed" under the old content-hashed op_id).
    assert len(mirror_calls) == 3
    reverted_sensor_ids = {e["sensor_id"] for e in mirror_calls[-1][1]["tapped_nics"]}
    assert reverted_sensor_ids == {"pktxpress-1"}
    assert "suricata-1" not in reverted_sensor_ids

    dropped_dev = n.gre_port_for_bridge_sensor("br-corp", "suricata-1")
    assert ("gretap", dropped_dev) in teardown.calls


def test_apply_tap_plan_unknown_target_raises_404(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    with pytest.raises(TapPlanError) as exc:
        _apply(tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
               db=db, target="nonexistent-te")
    assert exc.value.status == 404


def test_apply_tap_plan_rejects_non_te_target(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="o2-not-a-te", scenario_id="o2-surface", scenario_version=1,
            scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
            tier="o2",
        )
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    with pytest.raises(TapPlanError, match="must be a target-environment"):
        _apply(tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
               db=db, target="o2-not-a-te")


def test_apply_tap_plan_rejects_a_tap_plan_playout_id_as_target(tmp_path):
    """SHOULD-FIX (code review): a tap-plan playout deploy is ITSELF
    `tier="te"`, so passing its OWN deploy id as a target (e.g. a
    copy-paste mistake, or `--target <wrong-id>`) must fail FAST with a
    clear, actionable message -- not sail past the tier guard and die much
    later, confusingly, deep in bindings resolution ("target TE has no
    tapped interfaces to bind")."""
    db = _seeded_db(tmp_path)
    _, _, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    assert status.status == "completed"
    with pytest.raises(TapPlanError, match="tap-plan playout"):
        _apply(tmp_path, [TapBindingSpec(tap="dmz", sensors=["pktxpress-2"])],
               db=db, target=status.deploy_id)


# ---------------------------------------------------------------------------
# destroy_taps -- tap_-scoped only
# ---------------------------------------------------------------------------
def test_destroy_taps_removes_only_tap_resources_leaves_te_and_o2(tmp_path):
    db = _seeded_db(tmp_path)
    _, apply_adapter, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1", "suricata-1"])],
        db=db,
    )

    teardown = _FakeTeardown()
    descope_adapter = _FakeMirAdapter()
    result = destroy_taps(
        db_path=db, target=TE_DEPLOY, teardown_backend=teardown,
        adapter=descope_adapter,
    )
    assert result.destroyed is True
    assert result.errors == []
    # Exactly the two gretaps this tap-plan created, tap_-shaped names.
    gretap_calls = [t for (k, t) in teardown.calls if k == "gretap"]
    assert len(gretap_calls) == 2
    assert all(n.is_tap_gretap(d) for d in gretap_calls)
    # NOTHING vm/vnet/zone-shaped was touched (this deploy never emitted
    # clone-vm/create-vnet/create-sdn-zone ops of its own).
    assert not any(k in ("vm", "vnet", "zone", "ovs-bridge") for k, _t in teardown.calls)

    # S2 de-scope pass: h1's mirror was reverted to EMPTY (sensor-agnostic te,
    # 2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md -- there is no
    # TE-original entry to fall back to) BEFORE the gretaps were deleted --
    # proving the dangling-mirror gap is closed.
    descope_calls = [c for c in descope_adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(descope_calls) == 1
    reverted_sensor_ids = {e["sensor_id"] for e in descope_calls[0][1]["tapped_nics"]}
    assert reverted_sensor_ids == set()

    store = StateStore(db)
    try:
        te = store.get_deploy(TE_DEPLOY)
        o2 = store.get_deploy(O2_DEPLOY)
        tap_dep = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert te.status.value != "destroyed"
    assert o2.status.value != "destroyed"
    assert tap_dep.status.value == "destroyed"


def test_destroy_taps_by_target_finds_the_deploy(tmp_path):
    db = _seeded_db(tmp_path)
    _apply(tmp_path, [TapBindingSpec(tap="h2", sensors=["pktxpress-2"])], db=db)
    teardown = _FakeTeardown()
    result = destroy_taps(
        db_path=db, target=TE_DEPLOY, teardown_backend=teardown,
        adapter=_FakeMirAdapter(),
    )
    assert result.destroyed is True


def test_destroy_taps_by_NAME_target_finds_the_deploy(tmp_path):
    """W1 dev45 live-verify regression, tap-tier half (2026-08-04): `tap
    destroy --target <name>` must resolve the scenario NAME to the concrete te
    id the tap `plan_id` was keyed off -- otherwise it computes `tap-<name>`
    and never matches a plan created by name. This gap predates W1
    (`docs/WORK_ORDER_tap_target_resolution.md`, 2026-07-25, added name
    resolution to apply but not destroy); the identical tfc-tier gap is what
    the live-verify surfaced. Applies AND destroys by NAME (`te-scn`, the
    seeded twin's scenario_id)."""
    db = _seeded_db(tmp_path)
    _apply(tmp_path, [TapBindingSpec(tap="h2", sensors=["pktxpress-2"])], db=db, target="te-scn")
    result = destroy_taps(
        db_path=db, target="te-scn", teardown_backend=_FakeTeardown(),
        adapter=_FakeMirAdapter(),
    )
    assert result.destroyed is True


def test_destroy_taps_descope_pass_is_best_effort_on_adapter_failure(tmp_path):
    """S2: if the de-scope revert fails (e.g. the TE VM is already gone --
    exactly what happens when this runs via the S3 cascade AFTER the TE's
    own destroy), the gretap teardown must still proceed and still succeed."""
    class _AlwaysFailAdapter:
        def run(self, op, resolved_inputs, ctx):
            raise RuntimeError("simulated: VM no longer exists")

    db = _seeded_db(tmp_path)
    _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    teardown = _FakeTeardown()
    result = destroy_taps(
        db_path=db, target=TE_DEPLOY, teardown_backend=teardown,
        adapter=_AlwaysFailAdapter(),
    )
    assert result.destroyed is True
    assert result.errors == []
    gretap_calls = [t for (k, t) in teardown.calls if k == "gretap"]
    assert len(gretap_calls) == 1


def test_destroy_taps_no_target_no_deploy_id_raises(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(TapPlanError, match="target= or deploy_id="):
        destroy_taps(db_path=db, teardown_backend=_FakeTeardown())


def test_destroy_taps_unknown_target_raises_404(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(TapPlanError) as exc:
        destroy_taps(db_path=db, target="no-such-te", teardown_backend=_FakeTeardown())
    assert exc.value.status == 404


def test_destroy_taps_refuses_a_non_tap_plan_deploy(tmp_path):
    """The plan_id-prefix guard: `axrange o2 tap destroy` must never be
    pointable at a TE's own deploy_id (or an O2 surface deploy_id)."""
    db = _seeded_db(tmp_path)
    with pytest.raises(TapPlanError, match="not a tap-plan deploy"):
        destroy_taps(db_path=db, deploy_id=TE_DEPLOY, teardown_backend=_FakeTeardown())


def test_destroy_taps_refuses_tap_prefixed_deploy_with_non_tap_ops(tmp_path):
    """S4: the `tap-` plan_id PREFIX alone is not a safe guard -- a real TE
    deploy for a scenario literally named `tap-evil` would ALSO produce a
    plan_id starting with `tap-`. The STRUCTURAL check (op names subset of
    {attach-gre-tunnel, install-ovs-mirror, verify-capture}) must refuse it
    even though the prefix matches."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="evil-te-1", scenario_id="tap-evil", scenario_version=1,
            scenario_schema_version=1, plan_id="tap-evil-deadbeef", ir_hash="h",
            placement={"h1": "pxtest1"}, tier="te",
        )
        store.insert_ops(
            "evil-te-1",
            [
                _op_row(
                    "evil-te-1", "op-clone-h1", "clone-vm", "OP-HOST-010",
                    {"host": "h1"}, outputs={"vmid": 300001},
                ),
            ],
        )
    finally:
        store.close()

    with pytest.raises(TapPlanError, match="not a tap-plan deploy"):
        destroy_taps(
            db_path=db, deploy_id="evil-te-1", teardown_backend=_FakeTeardown(),
        )

    # And the real VM was never touched.
    store = StateStore(db)
    try:
        d = store.get_deploy("evil-te-1")
    finally:
        store.close()
    assert d.status.value != "destroyed"


def test_destroy_taps_refuses_tap_prefixed_deploy_with_zero_ops(tmp_path):
    """S4's vacuous-subset hole (code review, docs/WORK_ORDER_cli_lifecycle.md):
    `set() <= _TAP_OP_NAMES` is vacuously True, so a zero-op `tap-*`-named te
    must not pass the subset check alone -- non-emptiness is part of the
    discriminator too (`_is_tap_shaped_op_names`)."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="evil-te-2", scenario_id="tap-empty", scenario_version=1,
            scenario_schema_version=1, plan_id="tap-empty-deadbeef", ir_hash="h",
            placement={}, tier="te",
        )
        # No ops inserted at all.
    finally:
        store.close()

    with pytest.raises(TapPlanError, match="not a tap-plan deploy"):
        destroy_taps(db_path=db, deploy_id="evil-te-2", teardown_backend=_FakeTeardown())


def test_is_tap_plan_deploy_and_latest_tap_plan_deploy_skip_a_tap_named_te(tmp_path):
    """The READ-path discriminator (`is_tap_plan_deploy`/
    `latest_tap_plan_deploy`) must apply the SAME structural check as
    `destroy_taps`'s S4 guard, not just the `tap-` plan_id prefix -- a
    genuine te scenario named `tap-*` (ordinary te-shaped ops, not the
    three OP-MIR ops) must never be mistaken for a tap-plan playout on a
    discovery/default-selection path either (code review finding,
    docs/WORK_ORDER_cli_lifecycle.md)."""
    from axrange.o2control.taps import is_tap_plan_deploy, latest_tap_plan_deploy

    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="decoy-te-1", scenario_id="tap-decoy", scenario_version=1,
            scenario_schema_version=1, plan_id="tap-decoy-deadbeef", ir_hash="h",
            placement={"h1": "pxtest1"}, tier="te",
        )
        store.insert_ops(
            "decoy-te-1",
            [
                _op_row(
                    "decoy-te-1", "op-clone-h1", "clone-vm", "OP-HOST-010",
                    {"host": "h1"}, outputs={"vmid": 400001},
                ),
            ],
        )
        decoy_row = store.get_deploy("decoy-te-1")
        assert is_tap_plan_deploy(store, decoy_row) is False
        # The decoy is the ONLY `tap-`-prefixed row on file so far -- a
        # naive prefix-only scan would already return it here.
        assert latest_tap_plan_deploy(store) is None
    finally:
        store.close()

    # Apply a REAL tap-plan so there's a genuine one to find.
    apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY, bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
        target_node="pxtest1",
    )

    store = StateStore(db)
    try:
        latest = latest_tap_plan_deploy(store)
        assert latest is not None
        assert latest.deploy_id != "decoy-te-1"
        assert latest.plan_id == f"tap-{TE_DEPLOY}"
    finally:
        store.close()


def test_resolve_target_deploy_resolves_a_live_te_named_tap_star_by_scenario(tmp_path):
    """Code review MUST-FIX regression: `resolve_target_deploy`'s scenario-
    NAME path must NOT strip every `tap-%` `plan_id` from its candidate set
    before checking `scenario_id == target` -- that over-exclusion would
    404 a GENUINE, LIVE `te` twin whose scenario is itself named `tap-*`
    (`derive_plan_id` mints `tap-<name>-<hash>` for it) and would make the
    structural `is_tap_plan_deploy` check unreachable dead code. Reuses the
    SAME `decoy-te-1` fixture (`scenario_id="tap-decoy"`, `plan_id=
    "tap-decoy-deadbeef"`, ordinary te-shaped ops) the sibling test just
    above builds -- this is a LIVE, genuine `te` deploy, not a tap-plan
    playout, and `resolve_target_deploy(store, "tap-decoy")` MUST resolve
    to it by scenario name, while an ACTUAL tap-plan playout stays
    excluded."""
    from axrange.o2control.taps import resolve_target_deploy

    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="decoy-te-1", scenario_id="tap-decoy", scenario_version=1,
            scenario_schema_version=1, plan_id="tap-decoy-deadbeef", ir_hash="h",
            placement={"h1": "pxtest1"}, tier="te",
        )
        store.insert_ops(
            "decoy-te-1",
            [
                _op_row(
                    "decoy-te-1", "op-clone-h1", "clone-vm", "OP-HOST-010",
                    {"host": "h1"}, outputs={"vmid": 400001},
                ),
            ],
        )
        # The genuine te-named-tap-* decoy resolves cleanly by scenario name.
        assert resolve_target_deploy(store, "tap-decoy") == "decoy-te-1"
    finally:
        store.close()

    # A REAL tap-plan playout (its own scenario_id == its plan_id, e.g.
    # f"tap-{TE_DEPLOY}") must STILL be excluded by the structural check --
    # the over-exclusion fix must not have accidentally disabled that guard.
    apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY, bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
        target_node="pxtest1",
    )
    store = StateStore(db)
    try:
        tap_deploy = store.find_deploy_for_plan(f"tap-{TE_DEPLOY}")
        assert tap_deploy is not None
        with pytest.raises(TapPlanError) as exc:
            resolve_target_deploy(store, tap_deploy.scenario_id)
        assert exc.value.status == 404
        # The genuine decoy still resolves correctly afterward too.
        assert resolve_target_deploy(store, "tap-decoy") == "decoy-te-1"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Regression: a te_ destroy still cascades ITS OWN (pre-A2, in-deploy) tap_
# ops -- unaffected by this work (taps.py never touches planner/pass1_emit.py
# or planner/teardown.py's existing sweep logic).
# ---------------------------------------------------------------------------
def test_te_destroy_still_cascades_its_own_native_tap_ops(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store, with_native_gre=True)
    finally:
        store.close()

    store = StateStore(db)
    try:
        teardown = _FakeTeardown()
        result = destroy_deploy(store, teardown, TE_DEPLOY)
    finally:
        store.close()

    assert result.destroyed is True
    gretap_calls = [t for (k, t) in teardown.calls if k == "gretap"]
    assert n.gre_port_for_bridge_sensor("br-corp", "orig-sensor") in gretap_calls
    # The two hosts' mirror-hook snippet cleanup also ran (vmid-keyed).
    mirror_calls = {t for (k, t) in teardown.calls if k == "mirror"}
    assert mirror_calls == {"100001", "100002"}



# ---------------------------------------------------------------------------
# S3 -- a te_ destroy cascades to its standalone tap-plan
# (docs/design/06-taps-sensors.md).
# ---------------------------------------------------------------------------
def test_cascade_destroy_for_te_removes_standalone_tap_plan(tmp_path):
    db = _seeded_db(tmp_path)
    _, _, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )

    # As in a real `axrange destroy <te>`: the TE itself is torn down first
    # (its own native mirror plumbing, if any -- none here), THEN the cascade
    # runs against the standalone tap-plan.
    store = StateStore(db)
    try:
        te_teardown = _FakeTeardown()
        te_result = destroy_deploy(store, te_teardown, TE_DEPLOY)
    finally:
        store.close()
    assert te_result.destroyed is True

    tap_teardown = _FakeTeardown()
    tap_result = cascade_destroy_for_te(
        db_path=db, te_deploy_id=TE_DEPLOY, teardown_backend=tap_teardown,
        adapter=_FakeMirAdapter(),
    )
    assert tap_result is not None
    assert tap_result.destroyed is True
    assert tap_result.deploy_id == status.deploy_id
    gretap_calls = [t for (k, t) in tap_teardown.calls if k == "gretap"]
    assert len(gretap_calls) == 1

    store = StateStore(db)
    try:
        tap_dep = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert tap_dep.status.value == "destroyed"


def test_cascade_destroy_for_te_no_tap_plan_returns_none(tmp_path):
    """The common case -- most TEs never had a tap-plan applied -- must be
    silent (no error), not a surfaced failure on an ordinary TE destroy."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store)
    finally:
        store.close()

    result = cascade_destroy_for_te(
        db_path=db, te_deploy_id=TE_DEPLOY, teardown_backend=_FakeTeardown(),
    )
    assert result is None


# ---------------------------------------------------------------------------
# Tap-point addressing end-to-end (`docs/WORK_ORDER_tap_points.md`) --
# `tap_point:`/`selector:` bindings composed/applied through the SAME
# `_compose_ops` path as `tap:` (no new capture mechanism, just vocabulary).
# Reuses `tests/test_twin_inventory.py`'s multi-homed router fixture (h-a on
# corp, h-untapped on dmz untapped, r-edge multi-homed corp+dmz with its dmz
# leg individually tapped) -- a REAL build_plan+Executor run (LES-068), not a
# hand-seeded fixture, so vmid/bridge outputs are genuinely populated.
# ---------------------------------------------------------------------------
def _seed_tp_te(tmp_path, manifests):
    """Real TE (LES-068 idiom, `tests/test_twin_inventory.py::_build_and_run`)
    plus a standing O2 surface with `DEFAULT_SENSORS` in the SAME state.db --
    returns `(db_path, te_deploy_id)`."""
    from tests.test_twin_inventory import _build_and_run

    store, deploy_id = _build_and_run(tmp_path, manifests)
    _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    store.close()
    return str(tmp_path / "state.db"), deploy_id


def test_apply_tap_plan_binds_via_tap_point(tmp_path, manifests):
    """`tap_point: corp` addresses the auto-derived `segment` point for the
    `corp` net -- resolves to EVERY host leg tapped on it (h-a + r-edge),
    exactly as `tap: corp` would (same underlying interfaces, new
    vocabulary)."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    hosts_touched = {c[1]["vmid"] for c in mirror_calls}
    # Both h-a and r-edge have a tapped corp leg -- both get an
    # install-ovs-mirror op.
    assert len(mirror_calls) == 2
    assert len(hosts_touched) == 2


def test_apply_tap_plan_binds_via_selector_scope(tmp_path, manifests):
    """`selector: {scope: boundary}` binds the auto-derived `corp<->dmz`
    boundary point -- ONLY r-edge's two legs, not h-a's corp leg."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(
                    selector={"scope": "boundary"}, sensors=["pktxpress-1"],
                    mode="exclusive",
                ),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 1  # only r-edge is touched
    tapped = mirror_calls[0][1]["tapped_nics"]
    assert {(e["nic_index"], e["net_id"]) for e in tapped} == {(0, "corp"), (1, "dmz")}

    gre_ops = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert {c[1]["net_id"] for c in gre_ops} == {"corp", "dmz"}


# ---------------------------------------------------------------------------
# verify-capture injector selection (dev45 live-verify finding, boundary tap;
# REVISED for fix round F1, `docs/WORK_ORDER_tap_scopes.md` -- rx-only
# broke the original bystander-leaf mechanism, since it relied on the
# router's mirrored port catching a leaf's broadcast on EGRESS, which
# rx-only never mirrors). A `boundary` tap point's mirror sits on the
# ROUTER's leg (r-edge is the only host multi-homed across corp+dmz) and is
# `"rx"`-only, so the sentinel must now be injected FROM THE ROUTER ITSELF
# (its own rx side captures its own send), targeting a UNICAST address on
# the segment so the kernel's directly-connected route -- not the default
# route -- picks the mirrored NIC. The bystander-leaf mechanism SURVIVES,
# unchanged, for a `"both"`-direction multi-homed pair (e.g. `perimeter`).
# ---------------------------------------------------------------------------
def test_apply_tap_plan_boundary_verify_capture_injects_from_router_not_leaf(
    tmp_path, manifests,
):
    """End-to-end (real `_seed_tp_te` twin: r-edge multi-homed corp+dmz,
    h-a a corp leaf, h-untapped a dmz leaf): the boundary selector's TWO
    verify-capture heartbeats (one per mirrored net) must inject from
    r-edge ITSELF now (fix round F1 -- `boundary` is rx-only, so the
    pre-fix bystander-LEAF mechanism no longer applies), never from the
    leaf. This fixture's leaves have no recorded static IP (the shared
    `_build_and_run` IR never sets one), so no real unicast target is
    resolvable here -- the heartbeat is still COMPOSED, not silently
    skipped (LES-001), with the expected unreliable-fallback warning; the
    UNIT test above (`..._injects_from_router_with_unicast_target`) covers
    the target-resolved case directly against a fixture that has leaf
    IPs on file."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(
                    selector={"scope": "boundary"}, sensors=["pktxpress-1"],
                    mode="exclusive",
                ),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    verify_calls = {
        c[1]["net_id"]: c[1]
        for c in adapter.calls if c[0] == "verify-capture"
    }
    assert set(verify_calls) == {"corp", "dmz"}
    # r-edge injects on BOTH its own mirrored legs now (rx-only forces it) --
    # NEVER the leaf (h-a/h-untapped), which is exactly the F1 regression fix.
    assert verify_calls["corp"]["injector_host_id"] == "r-edge"
    assert verify_calls["dmz"]["injector_host_id"] == "r-edge"
    assert all(
        "sentinel_target_ip" not in c for c in verify_calls.values()
    )  # no leaf IP on file here -- broadcast fallback
    assert any("could not find a reliable injector" in w for w in status.warnings)


def test_select_verify_injector_rx_only_boundary_injects_from_router_with_unicast_target():
    """Unit-level case split (mock inventory: `gw` a router on corp+dmz,
    `h-corp`/`h-dmz` single-homed leaves on each): a boundary-shaped
    (`"rx"`-only) binding whose bound candidate is `gw` alone must inject
    from `gw` ITSELF (fix round F1), targeting the OTHER leaf's known IP on
    that net -- never `gw`'s own address -- and reports no fallback warning
    (a real unicast target was found)."""
    inv = _TeInventory(
        host_vmid={"gw": 1, "h-corp": 2, "h-dmz": 3},
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
        vlan_underlay=None, tappable={},
        qga_hosts={"gw", "h-corp", "h-dmz"},
        host_nets={"gw": {"corp", "dmz"}, "h-corp": {"corp"}, "h-dmz": {"dmz"}},
        host_net_ip={
            ("gw", "corp"): "10.0.0.1", ("gw", "dmz"): "10.0.1.1",
            ("h-corp", "corp"): "10.0.0.2", ("h-dmz", "dmz"): "10.0.1.2",
        },
    )
    injector, unreliable, target = _select_verify_injector(
        inv, "corp", {"gw"}, {"gw": "rx"},
    )
    assert injector == "gw"
    assert unreliable is False
    assert target == "10.0.0.2"  # h-corp's IP, never gw's own

    injector, unreliable, target = _select_verify_injector(
        inv, "dmz", {"gw"}, {"gw": "rx"},
    )
    assert injector == "gw"
    assert unreliable is False
    assert target == "10.0.1.2"  # h-dmz's IP


def test_unicast_leaf_target_prefers_a_qga_live_host_over_a_decoy():
    """Fix round G4 (`docs/WORK_ORDER_tap_scopes.md`, final round): a
    unicast ping needs an ARP reply to actually reach the wire (unlike
    broadcast, which every host answers at L2 with no resolution needed) --
    so `_unicast_leaf_target` must prefer a host in `inv.qga_hosts` (an
    EXISTING liveness signal: it proved itself alive via `wait-os-ready`)
    over the alphabetically-first known IP, which could belong to a
    powered-off/never-booted host with a merely-PLANNED address."""
    from axrange.o2control.taps import _unicast_leaf_target

    inv = _TeInventory(
        host_vmid={"a-decoy": 1, "z-live": 2},
        net_bridge={"corp": "br-corp"}, vlan_underlay=None, tappable={},
        # "a-decoy" sorts FIRST alphabetically but is NOT QGA-live;
        # "z-live" sorts last but IS -- the fix must pick "z-live" anyway.
        qga_hosts={"z-live"},
        host_nets={"a-decoy": {"corp"}, "z-live": {"corp"}},
        host_net_ip={
            ("a-decoy", "corp"): "10.0.0.9", ("z-live", "corp"): "10.0.0.10",
        },
    )
    assert _unicast_leaf_target(inv, "corp", exclude=set()) == "10.0.0.10"

    # With no QGA-live candidate at all, falls back to the alphabetically-
    # first known IP (the old behavior) rather than returning nothing.
    inv_no_qga = _TeInventory(
        host_vmid={"a-decoy": 1, "z-live": 2},
        net_bridge={"corp": "br-corp"}, vlan_underlay=None, tappable={},
        qga_hosts=set(),
        host_nets={"a-decoy": {"corp"}, "z-live": {"corp"}},
        host_net_ip={
            ("a-decoy", "corp"): "10.0.0.9", ("z-live", "corp"): "10.0.0.10",
        },
    )
    assert _unicast_leaf_target(inv_no_qga, "corp", exclude=set()) == "10.0.0.9"


def test_select_verify_injector_both_direction_still_picks_bystander_leaf():
    """The `"both"`-direction multi-homed case (e.g. `perimeter`) is
    UNCHANGED by F1 -- the pre-rx-only bystander-leaf mechanism still
    applies (egress IS mirrored), so the injector is the LEAF, not the
    router, and no unicast target is threaded (broadcast still works)."""
    inv = _TeInventory(
        host_vmid={"gw": 1, "h-corp": 2, "h-dmz": 3},
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
        vlan_underlay=None, tappable={},
        qga_hosts={"gw", "h-corp", "h-dmz"},
        host_nets={"gw": {"corp", "dmz"}, "h-corp": {"corp"}, "h-dmz": {"dmz"}},
    )
    injector, unreliable, target = _select_verify_injector(
        inv, "corp", {"gw"}, {"gw": "both"},
    )
    assert injector == "h-corp"
    assert unreliable is False
    assert target is None

    injector, unreliable, target = _select_verify_injector(
        inv, "dmz", {"gw"}, {"gw": "both"},
    )
    assert injector == "h-dmz"
    assert unreliable is False
    assert target is None


def test_select_verify_injector_segment_tap_unchanged_prefers_safe_candidate():
    """The SEGMENT-tap case (the mirror sits on a host's OWN leg) must stay
    exactly as before: when a safe (single-homed) candidate is bound, it is
    picked -- even when a multi-homed host (`gw`) is ALSO among the bound
    candidates (e.g. both a leaf's own leg and the router's leg on the same
    net are tapped to the same sensor), and REGARDLESS of direction."""
    inv = _TeInventory(
        host_vmid={"gw": 1, "h-corp": 2},
        net_bridge={"corp": "br-corp"}, vlan_underlay=None, tappable={},
        qga_hosts={"gw", "h-corp"},
        host_nets={"gw": {"corp", "dmz"}, "h-corp": {"corp"}},
    )
    # A lone segment candidate -- unchanged single-candidate behavior.
    injector, unreliable, target = _select_verify_injector(
        inv, "corp", {"h-corp"}, {"h-corp": "rx"},
    )
    assert injector == "h-corp"
    assert unreliable is False
    assert target is None

    # Both a leaf AND the router bound to the same (net, sensor) pair -- the
    # safe leaf must still win, never the multi-homed router.
    injector, unreliable, target = _select_verify_injector(
        inv, "corp", {"h-corp", "gw"}, {"h-corp": "rx", "gw": "rx"},
    )
    assert injector == "h-corp"
    assert unreliable is False
    assert target is None


def test_select_verify_injector_dual_homed_non_router_host_is_not_treated_as_safe():
    """G3 regression guard (`docs/WORK_ORDER_tap_scopes.md`, final round):
    a dual-homed `kind: host` node (fully expressible in the IR, even
    though no shipped archetype has one today) must NOT be treated as a
    safe, single-homed injector just because `HostInfo.kind` says "host,"
    not "router." A PRIOR revision of this fix round re-keyed the shared
    injector-safety predicate onto `kind == "router"`, which would have
    silently broadcast-injected from this exact host with
    `needs_warning=False` -- a false-negative heartbeat, since a dual-homed
    host's broadcast is JUST as route-ambiguous as a router's."""
    inv = _TeInventory(
        host_vmid={"dual-host": 1},
        net_bridge={"corp": "br-corp"}, vlan_underlay=None, tappable={},
        qga_hosts={"dual-host"},
        host_nets={"dual-host": {"corp", "mgmt"}},
        host_kind={"dual-host": "host"},  # explicitly NOT "router"
        host_net_ip={("dual-host", "corp"): "10.0.0.5"},
    )
    # `_is_router_host` correctly says this is NOT the router...
    assert _is_router_host(inv, "dual-host") is False
    # ...but `_is_route_ambiguous` still correctly flags it as unsafe to
    # broadcast-inject from (it has >=2 legs, regardless of `kind`).
    assert _is_route_ambiguous(inv, "dual-host") is True

    # No OTHER candidate exists on this net -- `dual-host` is the only bound
    # candidate, and it is NOT safe (route-ambiguous), so it falls to the
    # forced-injector path (rx-only, no `both`-direction candidate) rather
    # than being silently treated as `safe`.
    injector, unreliable, target = _select_verify_injector(
        inv, "corp", {"dual-host"}, {"dual-host": "rx"},
    )
    assert injector == "dual-host"
    # No OTHER host is attached to "corp" to target a unicast sentinel at
    # (only "dual-host" itself has a leg there) -- structurally unreliable,
    # must warn.
    assert unreliable is True
    assert target is None


def test_select_verify_injector_boundary_no_leaf_falls_back_with_warning():
    """A boundary segment with ONLY the router attached (no leaf host on
    that net at all, and no other host's IP on file): `_select_verify_
    injector` must fall back to the router (never silently skip the
    heartbeat, LES-001) AND flag the fallback so the caller can surface a
    warning -- no unicast target is resolvable, so the fallback is a plain
    broadcast injection from the router (fix round F1)."""
    inv = _TeInventory(
        host_vmid={"gw": 1},
        net_bridge={"wan-only": "br-wan"}, vlan_underlay=None, tappable={},
        qga_hosts={"gw"},
        host_nets={"gw": {"wan-only", "corp"}},
    )
    injector, unreliable, target = _select_verify_injector(
        inv, "wan-only", {"gw"}, {"gw": "rx"},
    )
    assert injector == "gw"
    assert unreliable is True
    assert target is None


def _seed_router_only_boundary_te(tmp_path):
    """Hand-seeded TE (matching `_seed_te`'s idiom, extended with
    `attach-vm-to-vnet`): `gw` is multi-homed across `corp` + `wan-only`, and
    `wan-only` has NO other host attached at all -- the degenerate boundary
    case with no leaf to fall back on."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        deploy_id = "te-router-only-boundary-1"
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="router-only", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
            placement={"gw": "pxtest1"}, tier="te",
        )
        rows = [
            _op_row(deploy_id, "op-clone-gw", "clone-vm", "OP-HOST-010",
                    {"host": "gw"}, outputs={"vmid": 900001}),
            _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                    {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
            _op_row(deploy_id, "op-br-wan-only", "create-ovs-bridge", "OP-NET-020",
                    {"net": "wan-only"}, outputs={"bridge_name": "br-wan-only"}),
            _op_row(deploy_id, "op-attach-gw-corp", "attach-vm-to-vnet", "OP-HOST-040",
                    {"host": "gw", "net": "corp"},
                    inputs={"net_id": "corp", "ip": "10.0.0.1", "nic_index": 0}),
            _op_row(deploy_id, "op-attach-gw-wan", "attach-vm-to-vnet", "OP-HOST-040",
                    {"host": "gw", "net": "wan-only"},
                    inputs={"net_id": "wan-only", "ip": "10.0.2.1", "nic_index": 1}),
            _op_row(deploy_id, "op-wait-os-gw", "wait-os-ready", "OP-HOST-080",
                    {"host": "gw"}, inputs={"os_family": "linux"}),
        ]
        store.insert_ops(deploy_id, rows)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    return db, deploy_id


def test_apply_tap_plan_boundary_no_leaf_still_composes_heartbeat_with_warning(
    tmp_path,
):
    """End-to-end negative case: a `wan-only` net with ONLY the (multi-
    homed) router attached -- the tap-plan still composes the verify-capture
    heartbeat (never silently skips it, LES-001) but surfaces the
    unreliable-injector warning; the fallback injector is `gw` itself."""
    db, te_id = _seed_router_only_boundary_te(tmp_path)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="wan-only", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    verify_calls = [c for c in adapter.calls if c[0] == "verify-capture"]
    assert len(verify_calls) == 1  # heartbeat IS composed, not skipped
    assert verify_calls[0][1]["injector_host_id"] == "gw"

    fallback_warnings = [
        w for w in status.warnings if "could not find a reliable injector" in w
    ]
    assert len(fallback_warnings) == 1
    assert "wan-only" in fallback_warnings[0]
    assert "'gw'" in fallback_warnings[0]


# ---------------------------------------------------------------------------
# Windows verify-capture sentinel -- `os_family`/`gateway_ip` threading
# (2026-07-24) + the orphaned-injector de-scope fix (LES-112).
# ---------------------------------------------------------------------------
def test_gateway_ip_for_net_resolves_multihomed_router_leg():
    inv = _TeInventory(
        host_vmid={"gw": 1, "h-corp": 2},
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
        host_nets={"gw": {"corp", "dmz"}, "h-corp": {"corp"}},
        host_net_ip={("gw", "corp"): "10.0.0.1", ("gw", "dmz"): "10.0.1.1"},
    )
    assert _gateway_ip_for_net(inv, "corp") == "10.0.0.1"
    assert _gateway_ip_for_net(inv, "dmz") == "10.0.1.1"
    # no host attached to this net at all -> unresolvable.
    assert _gateway_ip_for_net(inv, "wan-only") is None


def test_gateway_ip_for_net_none_when_no_multihomed_host_on_net():
    """A flat, router-less segment (every attached host single-homed) has no
    gateway to resolve, even though a host IS attached."""
    inv = _TeInventory(
        host_vmid={"h-corp": 1},
        net_bridge={"corp": "br-corp"}, vlan_underlay=None, tappable={},
        qga_hosts=set(), host_nets={"h-corp": {"corp"}},
        host_net_ip={("h-corp", "corp"): "10.0.0.5"},
    )
    assert _gateway_ip_for_net(inv, "corp") is None


def test_compose_ops_threads_os_family_and_gateway_for_windows_injector(tmp_path):
    """A Windows injector (`inv.host_os_family[h] == "windows"`) with a
    resolvable gateway on its net gets BOTH `os_family` and `gateway_ip`
    threaded into the composed `verify-capture` op's resolved inputs --
    `config_exec._verify_capture` picks the PowerShell sentinel off these."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        # h1 is corp's (single-homed, safe) tapped host in `_seed_te` --
        # override it to Windows and give `corp` a resolvable gateway via a
        # synthetic multi-homed "gw" host (`_seed_te` itself has no router).
        inv.host_os_family["h1"] = "windows"
        inv.host_nets["h1"] = {"corp"}
        inv.host_nets["gw"] = {"corp", "wan"}
        inv.host_net_ip[("gw", "corp")] = "10.9.9.1"
        sensor_by_id = _sensor_map()
        iface_new_sensors = {("h1", "corp", 0): {"pktxpress-1": "both"}}
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert len(verify_ops) == 1
    vop = verify_ops[0]
    assert vop.scope["host"] == "h1"
    assert vop.inputs_resolved["os_family"] == "windows"
    assert vop.inputs_resolved["gateway_ip"] == "10.9.9.1"


def test_compose_ops_boundary_verify_capture_orders_after_routers_mirror_not_injectors(
    tmp_path,
):
    """A boundary tap's chosen injector (a bystander LEAF) has NO
    install-ovs-mirror op of its own -- the op that actually matters for
    ordering is the ROUTER's (the real candidate, whose mirror the
    heartbeat is proving). `_compose_ops` must order verify-capture after
    the ROUTER's install-ovs-mirror, not silently skip ordering just
    because the injector itself was never mirrored."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        # h1 (real, single-homed, on corp) plays the bystander LEAF; "gw" is
        # a synthetic multi-homed router candidate on corp+wan.
        inv.host_nets["h1"] = {"corp"}
        inv.host_nets["gw"] = {"corp", "wan"}
        inv.host_vmid["gw"] = 900000
        sensor_by_id = _sensor_map()
        # Only "gw" is the bound candidate for this (net, sensor) pair --
        # exactly the boundary-tap shape (the mirror sits on the router's leg).
        iface_new_sensors = {("gw", "corp", 0): {"pktxpress-1": "both"}}
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    mirror_ops = {o.scope["host"]: o for o in plan.ops.values()
                  if o.op_name == "install-ovs-mirror"}
    assert set(mirror_ops) == {"gw"}  # h1 (the injector) has NO mirror op

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert len(verify_ops) == 1
    vop = verify_ops[0]
    assert vop.scope["host"] == "h1"  # the leaf was chosen as injector

    edges = {(e.src_op_id, e.dst_op_id) for e in plan.edges if e.kind == "ordering"}
    assert (mirror_ops["gw"].op_id, vop.op_id) in edges


def test_compose_ops_windows_injector_without_gateway_raises(tmp_path):
    """No multi-homed host anywhere on `corp` -> no gateway to resolve -- a
    Windows injector must fail LOUD at compose time (never silently fall
    back to a broadcast target it structurally cannot use)."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        inv.host_os_family["h1"] = "windows"
        sensor_by_id = _sensor_map()
        iface_new_sensors = {("h1", "corp", 0): {"pktxpress-1": "both"}}
        with pytest.raises(TapPlanError, match="Windows but no unicast target"):
            _compose_ops(
                "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store,
                "pxtest1",
            )
    finally:
        store.close()


def test_compose_ops_windows_forced_router_injector_warns_self_ping_not_broadcast(
    tmp_path,
):
    """Fix round G8 (`docs/WORK_ORDER_tap_scopes.md`, final round): a
    Windows router forced to be its own verify-capture injector (multi-
    homed, `both`-direction mirror, no bystander leaf on the net at all)
    resolves `sentinel_target_ip=None` -- but UNLIKE Linux, a Windows
    sentinel never falls back to broadcast (`_verify_capture` ALWAYS
    unicasts); it falls back to `gateway_ip`, which here is the router's
    OWN address on this net -- a self-ping, not "egressing the wrong NIC."
    The unreliable-injector warning text must say so, not claim broadcast."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        # A standalone `_TeInventory`, built by hand -- NOT `_seed_te` (its
        # `attach-vm-to-vnet` rows, `docs/WORK_ORDER_tap_eligibility.md` D1,
        # now put h1 on "corp" too, which would give `_select_verify_injector`
        # a real bystander leaf and defeat this test's whole premise). "gw"
        # must be the TRUE sole host on "corp" for the no-leaf-on-net
        # fallback this test targets to actually trigger.
        inv = _TeInventory(
            host_vmid={"gw": 900001}, net_bridge={"corp": "br-corp"},
            vlan_underlay=None, tappable={}, qga_hosts=set(),
            host_nets={"gw": {"corp", "wan"}},
            host_os_family={"gw": "windows"},
            host_net_ip={("gw", "corp"): "10.9.9.1"},
        )
        sensor_by_id = _sensor_map()
        iface_new_sensors = {("gw", "corp", 0): {"pktxpress-1": "both"}}
        warnings: list[str] = []
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store,
            "pxtest1", warnings=warnings,
        )
    finally:
        store.close()

    verify_ops = [o for o in plan.ops.values() if o.op_name == "verify-capture"]
    assert len(verify_ops) == 1
    vop = verify_ops[0]
    assert vop.scope["host"] == "gw"
    assert vop.inputs_resolved["os_family"] == "windows"
    assert vop.inputs_resolved["gateway_ip"] == "10.9.9.1"
    assert "sentinel_target_ip" not in vop.inputs_resolved

    assert len(warnings) == 1
    msg = warnings[0]
    assert "self-ping" in msg
    assert "broadcast" not in msg


def test_compose_ops_linux_injector_defaults_os_family_and_optional_gateway(
    tmp_path,
):
    """The unmodified `_seed_te` fixture has no Windows hosts and no router
    (`host_os_family`/`host_net_ip` both empty) -- `os_family` must default
    to `"linux"` and `gateway_ip` must simply be ABSENT from resolved
    inputs (never required, unlike the Windows case)."""
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
        inv = _scan_te_inventory(store, TE_DEPLOY)
        sensor_by_id = _sensor_map()
        iface_new_sensors = {("h1", "corp", 0): {"pktxpress-1": "both"}}
        plan = _compose_ops(
            "tap-test-plan", inv, sensor_by_id, iface_new_sensors, store, "pxtest1",
        )
    finally:
        store.close()

    vop = next(o for o in plan.ops.values() if o.op_name == "verify-capture")
    assert vop.inputs_resolved["os_family"] == "linux"
    assert "gateway_ip" not in vop.inputs_resolved


def _seed_mixed_os_key_domain_te(tmp_path):
    """A TE exercising the FULL key domain `_scan_te_inventory` must publish
    (LES-099) end-to-end (real ops, no manual `_TeInventory` patching):
      - h-win: Windows, attached to corp.
      - h-untapped: Linux, attached to corp (name kept from the pre-D1
        "attached but not tapped" era, `docs/WORK_ORDER_tap_eligibility.md`
        -- there is no more tapped/untapped distinction to name it after,
        but the 2 call sites below still say `h-untapped`).
      - h-noos: attached to corp, but NO `wait-os-ready` op on file at all --
        the genuine lookup-miss case.
      - gw: multi-homed (corp + wan), Linux, with a recorded IP on BOTH legs
        -- the router `_gateway_ip_for_net` resolves against.
    """
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        deploy_id = "te-mixed-os-key-domain-1"
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="mixed-os", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
            placement={
                "h-win": "pxtest1", "h-untapped": "pxtest1",
                "h-noos": "pxtest1", "gw": "pxtest1",
            }, tier="te",
        )
        rows = [
            _op_row(deploy_id, "op-clone-win", "clone-vm", "OP-HOST-010",
                    {"host": "h-win"}, outputs={"vmid": 500001}),
            _op_row(deploy_id, "op-clone-untapped", "clone-vm", "OP-HOST-010",
                    {"host": "h-untapped"}, outputs={"vmid": 500002}),
            _op_row(deploy_id, "op-clone-noos", "clone-vm", "OP-HOST-010",
                    {"host": "h-noos"}, outputs={"vmid": 500003}),
            _op_row(deploy_id, "op-clone-gw", "clone-vm", "OP-HOST-010",
                    {"host": "gw"}, outputs={"vmid": 500004}),
            _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                    {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
            _op_row(deploy_id, "op-br-wan", "create-ovs-bridge", "OP-NET-020",
                    {"net": "wan"}, outputs={"bridge_name": "br-wan"}),
            _op_row(deploy_id, "op-attach-win-corp", "attach-vm-to-vnet",
                    "OP-HOST-040", {"host": "h-win", "net": "corp"},
                    inputs={"net_id": "corp", "nic_index": 0}),
            _op_row(deploy_id, "op-attach-untapped-corp", "attach-vm-to-vnet",
                    "OP-HOST-040", {"host": "h-untapped", "net": "corp"},
                    inputs={"net_id": "corp", "nic_index": 0}),
            _op_row(deploy_id, "op-attach-noos-corp", "attach-vm-to-vnet",
                    "OP-HOST-040", {"host": "h-noos", "net": "corp"},
                    inputs={"net_id": "corp", "nic_index": 0}),
            _op_row(deploy_id, "op-attach-gw-corp", "attach-vm-to-vnet",
                    "OP-HOST-040", {"host": "gw", "net": "corp"},
                    inputs={"net_id": "corp", "ip": "10.5.5.1", "nic_index": 0}),
            _op_row(deploy_id, "op-attach-gw-wan", "attach-vm-to-vnet",
                    "OP-HOST-040", {"host": "gw", "net": "wan"},
                    inputs={"net_id": "wan", "ip": "10.5.9.1", "nic_index": 1}),
            _op_row(deploy_id, "op-wait-os-win", "wait-os-ready", "OP-HOST-080",
                    {"host": "h-win"}, inputs={"os_family": "windows"}),
            _op_row(deploy_id, "op-wait-os-untapped", "wait-os-ready", "OP-HOST-080",
                    {"host": "h-untapped"}, inputs={"os_family": "linux"}),
            # h-noos: deliberately NO wait-os-ready op at all.
            _op_row(deploy_id, "op-wait-os-gw", "wait-os-ready", "OP-HOST-080",
                    {"host": "gw"}, inputs={"os_family": "linux"}),
        ]
        store.insert_ops(deploy_id, rows)
    finally:
        store.close()
    return db, deploy_id


def test_scan_te_inventory_publishes_full_key_domain_for_all_hosts(tmp_path):
    """LES-099 key-domain probe: `host_os_family`/`host_net_ip`/`host_nets`
    must be populated for EVERY host `_scan_te_inventory` sees -- Windows
    and Linux, tapped and untapped -- and a host with NO `wait-os-ready` on
    file must be a genuine dict lookup MISS (not defaulted/fabricated at
    scan time; `_compose_ops` is the layer allowed to default to "linux"),
    so a downstream miss can never be silently confused with a recorded
    Linux host."""
    db, deploy_id = _seed_mixed_os_key_domain_te(tmp_path)
    store = StateStore(db)
    try:
        inv = _scan_te_inventory(store, deploy_id)
    finally:
        store.close()

    # host_nets: full attachment set for EVERY host, tapped or not.
    assert inv.host_nets["h-win"] == {"corp"}
    assert inv.host_nets["h-untapped"] == {"corp"}
    assert inv.host_nets["h-noos"] == {"corp"}
    assert inv.host_nets["gw"] == {"corp", "wan"}

    # host_os_family: populated for every host THAT ran wait-os-ready,
    # Windows included, tapped or not -- and ABSENT (not "linux") for the
    # one host with no wait-os-ready on file at all.
    assert inv.host_os_family["h-win"] == "windows"
    assert inv.host_os_family["h-untapped"] == "linux"
    assert inv.host_os_family["gw"] == "linux"
    assert "h-noos" not in inv.host_os_family

    # host_net_ip: only legs with a recorded IP; the router's two legs both
    # resolve, the DHCP/no-ip legs are simply absent (not "").
    assert inv.host_net_ip[("gw", "corp")] == "10.5.5.1"
    assert inv.host_net_ip[("gw", "wan")] == "10.5.9.1"
    assert ("h-untapped", "corp") not in inv.host_net_ip
    assert ("h-win", "corp") not in inv.host_net_ip

    # qga_hosts stays the LINUX-only injector-preference signal: gw and
    # h-untapped qualify, the Windows host does not (a separate, narrower
    # signal from host_os_family -- see the field's own docstring).
    assert inv.qga_hosts == {"h-untapped", "gw"}

    # tappable now lists EVERY attached host, "gw" included (`docs/
    # WORK_ORDER_tap_eligibility.md` D1, Authority: user, 2026-07-28 -- no
    # more tapped-subset gate; any published interface is tap-plan-eligible).
    assert set(inv.tappable) == {"h-win", "h-untapped", "h-noos", "gw"}

    # And the derived helpers agree: gw is the router (and route-ambiguous),
    # resolves as the gateway for both its legs; a single-homed host is
    # neither (fix round G3 split -- both predicates agree in this simple
    # case where the router is the only multi-homed host).
    assert _is_router_host(inv, "gw") is True
    assert _is_router_host(inv, "h-untapped") is False
    assert _is_route_ambiguous(inv, "gw") is True
    assert _is_route_ambiguous(inv, "h-untapped") is False
    assert _gateway_ip_for_net(inv, "corp") == "10.5.5.1"
    assert _gateway_ip_for_net(inv, "wan") == "10.5.9.1"


def _seed_two_leaf_corp_te(tmp_path):
    """A TE with TWO independent single-homed hosts (h1a, h1b) both attached
    to `corp` nic 0 -- lets a tap-plan bind EITHER one individually (a
    host-level `tap:` identifier), so re-applying with the OTHER host bound
    changes `_select_verify_injector`'s candidate set for the SAME (net,
    sensor) pair, exactly the "injector changed on re-apply" shape the
    LES-112 orphaned-op de-scope fix targets (2026-07-24) -- without needing
    a router/boundary topology at all."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        deploy_id = "te-two-leaf-1"
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="two-leaf", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
            placement={"h1a": "pxtest1", "h1b": "pxtest1"}, tier="te",
        )
        rows = [
            _op_row(deploy_id, "op-clone-h1a", "clone-vm", "OP-HOST-010",
                    {"host": "h1a"}, outputs={"vmid": 300001}),
            _op_row(deploy_id, "op-clone-h1b", "clone-vm", "OP-HOST-010",
                    {"host": "h1b"}, outputs={"vmid": 300002}),
            _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                    {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
            _op_row(
                deploy_id, "op-attach-h1a", "attach-vm-to-vnet", "OP-HOST-040",
                {"host": "h1a", "net": "corp"},
                inputs={"net_id": "corp", "nic_index": 0},
            ),
            _op_row(
                deploy_id, "op-attach-h1b", "attach-vm-to-vnet", "OP-HOST-040",
                {"host": "h1b", "net": "corp"},
                inputs={"net_id": "corp", "nic_index": 0},
            ),
            _op_row(deploy_id, "op-wait-os-h1a", "wait-os-ready", "OP-HOST-080",
                    {"host": "h1a"}, inputs={"os_family": "linux"}),
            _op_row(deploy_id, "op-wait-os-h1b", "wait-os-ready", "OP-HOST-080",
                    {"host": "h1b"}, inputs={"os_family": "linux"}),
        ]
        store.insert_ops(deploy_id, rows)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    return db, deploy_id


def test_apply_tap_plan_injector_change_descopes_stale_verify_capture_op(tmp_path):
    """LES-112 orphaned-injector de-scope (2026-07-24): a re-apply that
    swaps the verify-capture INJECTOR for the SAME (net, sensor) pair (here:
    the host-level binding moves h1a -> h1b, both on `corp`, same sensor)
    must mark the OLD injector's `verify-capture` op DESTROYED -- it must
    never linger as a stale `pending`/`completed` row that makes a
    read-only status view see a phantom incomplete/failed op."""
    db, te_id = _seed_two_leaf_corp_te(tmp_path)

    adapter1 = _FakeMirAdapter()
    status1 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id, bindings=[TapBindingSpec(tap="h1a", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter1, config_backend=adapter1, target_node="pxtest1",
    )
    assert status1.status == "completed"
    verify_calls_1 = [c for c in adapter1.calls if c[0] == "verify-capture"]
    assert len(verify_calls_1) == 1
    assert verify_calls_1[0][1]["injector_host_id"] == "h1a"

    store = StateStore(db)
    try:
        deploy_row = store.find_deploy_for_plan(f"tap-{te_id}")
        assert deploy_row is not None
        deploy_id = deploy_row.deploy_id
        old_op_ids = {
            op.op_id for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    assert len(old_op_ids) == 1

    adapter2 = _FakeMirAdapter()
    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id, bindings=[TapBindingSpec(tap="h1b", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter2, config_backend=adapter2, target_node="pxtest1",
    )
    assert status2.status == "completed"
    assert status2.deploy_id == deploy_id  # the SAME tap-plan deploy, reused
    verify_calls_2 = [c for c in adapter2.calls if c[0] == "verify-capture"]
    assert len(verify_calls_2) == 1
    assert verify_calls_2[0][1]["injector_host_id"] == "h1b"

    store = StateStore(db)
    try:
        op_status_by_id = {
            op.op_id: op.status for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "verify-capture"
        }
    finally:
        store.close()
    # Both the OLD (h1a) and NEW (h1b) op_ids now exist for this deploy_id --
    # the old one must be DESTROYED, never left `pending`/`completed`.
    assert len(op_status_by_id) == 2
    (old_op_id,) = old_op_ids
    assert old_op_id in op_status_by_id
    assert op_status_by_id[old_op_id] == OpStatus.DESTROYED
    new_op_ids = set(op_status_by_id) - old_op_ids
    assert len(new_op_ids) == 1
    assert op_status_by_id[next(iter(new_op_ids))] == OpStatus.COMPLETED


def _seed_partial_segment_te(tmp_path, *, deploy_id="te-partial-seg-1"):
    """A REAL 3-host, single-net TE -- formerly (pre-D1) seeded so only 2 of
    3 hosts were "tapped" (`record-tap-intent`) to exercise M5's partial-
    coverage warning; `docs/WORK_ORDER_tap_eligibility.md` D1 (Authority:
    user, 2026-07-28) removed the op that made "attached but not tapped"
    expressible at all -- EVERY `attach-vm-to-vnet`'d host is now
    unconditionally tap-plan-eligible, h3 included. Kept the name (2 call
    sites still construct "the fixture that used to encode a coverage gap")
    -- see the two tests below, now pinning that the gap can no longer be
    constructed, not that it's detected."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="partial-seg", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
            placement={"h1": "pxtest1", "h2": "pxtest1", "h3": "pxtest1"}, tier="te",
        )
        rows = [
            _op_row(deploy_id, "op-clone-h1", "clone-vm", "OP-HOST-010",
                    {"host": "h1"}, outputs={"vmid": 500001}),
            _op_row(deploy_id, "op-clone-h2", "clone-vm", "OP-HOST-010",
                    {"host": "h2"}, outputs={"vmid": 500002}),
            _op_row(deploy_id, "op-clone-h3", "clone-vm", "OP-HOST-010",
                    {"host": "h3"}, outputs={"vmid": 500003}),
            _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                    {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
        ]
        for h, ip in (("h1", "10.0.0.1"), ("h2", "10.0.0.2"), ("h3", "10.0.0.3")):
            rows.append(
                _op_row(
                    deploy_id, f"op-attach-{h}", "attach-vm-to-vnet", "OP-HOST-040",
                    {"host": h, "net": "corp"},
                    inputs={"net_id": "corp", "ip": ip, "nic_index": 0},
                )
            )
        store.insert_ops(deploy_id, rows)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    return db, deploy_id


def test_apply_tap_plan_segment_partial_coverage_emits_warning(tmp_path):
    """M5's coverage warning ('segment covers N of M attached hosts')
    compares `resolve_tap_point`'s result against `segment_attached_hosts`
    -- as of D1 those two ALWAYS agree (`segment_attached_hosts`'s own
    docstring), so this fixture, which used to encode "2 of 3 tapped" via
    `record-tap-intent`, now has no way left to construct a coverage gap:
    all 3 hosts resolve, no warning fires. Kept as a regression pin for
    this specific discovered D1 side effect (M5 itself is out of this work
    order's D1-D6 scope -- left in place as harmless, now-unreachable-in-
    practice code, same posture as `TapPointResolutionError`)."""
    db, te_id = _seed_partial_segment_te(tmp_path)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    assert not [w for w in status.warnings if "covers" in w]
    # `install-ovs-mirror` is per-HOST (one op per host whose port(s) join
    # the mirror, `test_compose_ops_fan_out_emits_n_gretaps_and_chained_
    # mirred`'s own `mop.scope["host"]` shape) -- one gretap is still shared
    # across the whole (bridge, sensor) pair, so THIS is the count that
    # proves h1/h2/h3 are ALL covered, not `attach-gre-tunnel` (stays 1).
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 3


def test_apply_tap_plan_legacy_tap_net_binding_also_emits_coverage_warning(tmp_path):
    """Same D1 consequence via the legacy `tap: corp` (net-level) address --
    M5 was never tied to the tap_point/selector vocabulary specifically, so
    neither is its now-unreachable status."""
    db, te_id = _seed_partial_segment_te(tmp_path)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    assert not [w for w in status.warnings if "covers" in w]
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 3


def test_apply_tap_plan_full_segment_coverage_emits_no_warning(tmp_path, manifests):
    """The negative case: a `segment` point covering EVERY attached host
    carries no coverage warning at all (`test_apply_tap_plan_binds_via_tap_
    point`'s `corp` -- h-a + r-edge are the ENTIRE attached set)."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    assert not any("covers" in w for w in status.warnings)


# ---------------------------------------------------------------------------
# M6 (code review): `_delete_dropped_gretaps`'s `log=` is wired to
# `warnings.append` (was `lambda _m: None`) -- a dropped-pair gretap
# removal/failure now actually reaches `TapPlanStatus.warnings`.
# ---------------------------------------------------------------------------
def test_apply_tap_plan_reshard_surfaces_dropped_gretap_in_warnings(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    # Re-point to a different sensor -- pktxpress-1 is fully dropped, so
    # `_delete_dropped_gretaps` actually runs and logs a removal.
    #
    # `teardown_backend` MUST be injected here: without it
    # `_delete_dropped_gretaps` builds a real `ProxmoxTeardown(load_config(None))`,
    # and `load_config` falls back to `./adapter.conf` -- so from the repo root this
    # test used to pass only by opening a real SSH session to whatever OVS host that
    # gitignored file names (`OVS_SSH_HOST=192.0.2.1` made it fail after an ~11 s
    # timeout), and it failed in any checkout without the file. A unit test that
    # resolves a gitignored file from cwd is a live-cluster test in disguise.
    teardown = _FakeTeardown()
    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["suricata-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        teardown_backend=teardown,
    )
    assert status2.status == "completed"
    assert any("gretap" in w and "removed" in w for w in status2.warnings)


def test_apply_tap_plan_reshard_swap_across_shared_underlay_survives(
    tmp_path, manifests,
):
    """The end-to-end reproduction of the dev45 live bug (2026-07-25): `_seed_
    tp_te`'s twin is a VLAN zone (`corp`/`dmz` both carry `vlan:`), so BOTH
    nets share ONE underlay bridge (`create-ovs-bridge`'s single scenario-
    scoped op). Bind corp->pktxpress-1, dmz->suricata-1, then SWAP them
    (corp->suricata-1, dmz->pktxpress-1) -- every dropped (net, sensor) pair
    aliases a device a still-needed pair uses (same shared bridge, same two
    sensors, just swapped nets), so NEITHER gretap may be deleted and no
    `... removed` warning may appear, only `KEPT` ones. This is the exact
    shape that silently killed capture on dev45: apply reported `completed`
    while both live gretaps were deleted underneath it."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status1 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"]),
                TapBindingSpec(tap_point="dmz", sensors=["suricata-1"]),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status1.status == "completed"

    teardown = _FakeTeardown()
    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(tap_point="corp", sensors=["suricata-1"]),
                TapBindingSpec(tap_point="dmz", sensors=["pktxpress-1"]),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        teardown_backend=teardown,
    )
    assert status2.status == "completed"
    # The fix: NOTHING was deleted -- both gretap devices are still needed
    # (just by the swapped net), so both must survive the apply.
    assert not any(k == "gretap" for k, _t in teardown.calls)
    assert not any("gretap" in w and "removed" in w for w in status2.warnings)
    assert any("KEPT" in w for w in status2.warnings)


def test_apply_tap_plan_exclusive_binding_matching_multiple_points_is_allowed(
    tmp_path, manifests,
):
    """`mode: exclusive` constrains SENSOR count on the binding (exactly 1),
    never the number of tap POINTS a selector may match -- a selector
    matching BOTH `corp` and `dmz` segment points, routed exclusively to one
    sensor, is perfectly valid (both nets' traffic goes to that one
    sensor)."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(
                    selector={"scope": "segment"}, sensors=["pktxpress-1"],
                    mode="exclusive",
                ),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    # Both corp and dmz segment points matched -- both wired to the ONE
    # exclusive sensor.
    assert {c[1]["net_id"] for c in gre_calls} == {"corp", "dmz"}
    assert all(c[1]["sensor_id"] == "pktxpress-1" for c in gre_calls)


def test_apply_tap_plan_unknown_tap_point_raises(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    with pytest.raises(TapPlanError, match="resolved to zero interfaces"):
        apply_tap_plan(
            spec=TapPlanSpec(
                target=te_id,
                bindings=[TapBindingSpec(tap_point="not-a-point", sensors=["pktxpress-1"])],
            ),
            db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
            target_node="px1",
        )


def test_apply_tap_plan_selector_no_match_raises(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    with pytest.raises(TapPlanError, match="resolved to zero interfaces"):
        apply_tap_plan(
            spec=TapPlanSpec(
                target=te_id,
                bindings=[
                    TapBindingSpec(
                        selector={"id": "does-not-exist"}, sensors=["pktxpress-1"],
                    ),
                ],
            ),
            db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
            target_node="px1",
        )


def test_tap_point_reapply_reshards_without_touching_te_tier(tmp_path, manifests):
    """A re-apply that moves the `corp` tap_point to a different sensor
    (re-shard) must re-point IN PLACE -- and must not touch the target TE's
    own deploy row (status/tier) or add a single op to ITS `ops` table
    (`docs/design/02-twin-model.md`: Tap 'binds to Twin + Lab
    sensors... self [cascade] only' -- a `tap` re-apply never redeploys or
    mutates the twin)."""
    db, te_id = _seed_tp_te(tmp_path, manifests)

    store = StateStore(db)
    try:
        te_before = store.get_deploy(te_id)
        te_ops_before = len(store.ops_for_deploy(te_id))
    finally:
        store.close()

    adapter = _FakeMirAdapter()
    status1 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status1.status == "completed"

    teardown = _FakeTeardown()
    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["suricata-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        teardown_backend=teardown,
    )
    assert status2.status == "completed"
    assert status2.deploy_id == status1.deploy_id  # same tap-plan deploy, re-pointed

    # Re-shard proof: the old sensor's gretap was torn down (dropped-pair
    # cleanup, M1/S2), and the new one was wired -- all WITHOUT redeploying
    # or mutating the twin.
    dropped = [t for (k, t) in teardown.calls if k == "gretap"]
    assert dropped  # at least the dropped pktxpress-1 pair was cleaned up
    new_gre = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert any(c[1]["sensor_id"] == "suricata-1" for c in new_gre)

    store = StateStore(db)
    try:
        te_after = store.get_deploy(te_id)
        te_ops_after = len(store.ops_for_deploy(te_id))
    finally:
        store.close()
    assert te_after.status.value == te_before.status.value
    assert te_ops_after == te_ops_before  # not one op added to the TWIN's deploy


def test_tap_status_reports_direction_actually_applied_after_reapply(tmp_path, manifests):
    """GAP-044/LES-157, live-repro'd on dev45: `tap status` must read back
    the direction the MOST RECENT apply put on the wire, not whatever the
    tap-plan deploy's FIRST-EVER apply persisted.

    Apply 1 binds `h-a` alone (a HOST-id `tap:` hit -- `_resolve_tap_target`'s
    host-hit branch, not a net match -- so direction is `"both"`). Apply 2
    re-binds by NET (`tap: corp`, which now also picks up `r-edge`'s corp
    leg) -- 2 targets on that net makes it `"rx"`
    (`_resolve_binding_targets`). `install-ovs-mirror[h-a]` is a STABLE
    op_id (scope is just `{host}`, `docs/design/06-taps-sensors.md`) that
    apply 2 force-resets and re-runs with the NEW `direction: rx` --
    `tap status` must report `rx` for h-a's leg after apply 2, not the
    `both` its row was born with. Before the GAP-044 fix this asserted
    `both` (h-a's row stuck at apply 1's value) unioned with r-edge's fresh
    `rx` into the exact symptom seen live -- `[both+rx]`."""
    db, te_id = _seed_tp_te(tmp_path, manifests)

    adapter = _FakeMirAdapter()
    status1 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="h-a", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status1.status == "completed"
    bindings1 = tap_status(db_path=db, target=te_id).bindings
    [corp1] = [b for b in bindings1 if b.tap == "corp"]
    assert corp1.direction == "both"

    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status2.status == "completed"
    assert status2.deploy_id == status1.deploy_id  # same tap-plan deploy, re-applied

    bindings2 = tap_status(db_path=db, target=te_id).bindings
    [corp2] = [b for b in bindings2 if b.tap == "corp"]
    assert corp2.direction == "rx", (
        "tap status must report the direction the RE-APPLY actually put on "
        f"the wire (rx), not a stale union with apply 1's value: got "
        f"{corp2.direction!r}"
    )


def test_tap_status_and_info_hide_a_non_completed_mirror_op(tmp_path, manifests):
    """M2, code review (LES-113 scope widening, LES-159) -- GAP-044's
    write-back fix made a COMPLETED-only filter mandatory, not optional:
    `set_op_running` now stamps the CURRENT plan's inputs onto a row the
    INSTANT an op starts an attempt, even on a path where the adapter is
    NEVER actually called (`LockResolutionError`/a lock-acquisition
    timeout, `attempts=0`, final_status=FAILED). Unfiltered, a
    never-applied set of inputs would render as "what's live" -- the exact
    GAP-044 over-report through a new door. A `failed` `install-ovs-mirror`
    row (simulating exactly that path) must vanish from BOTH `tap status`
    and `tap info`, not just one."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="h-a", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    # Sanity: before the mutation below, both surfaces show the binding.
    assert tap_status(db_path=db, target=te_id).bindings
    assert tap_info(db_path=db, deploy_id=te_id).bindings

    store = StateStore(db)
    try:
        # Both op names `_bindings_summary_from_ops`/`tap_info_from_deploy`
        # read: `attach-gre-tunnel` (the `tap -> sensors` map itself) and
        # `install-ovs-mirror` (the per-nic direction/gre_port metadata).
        mir_ops = [
            op for op in store.ops_for_deploy(status.deploy_id)
            if op.op_name in ("install-ovs-mirror", "attach-gre-tunnel")
        ]
        assert mir_ops
        for op in mir_ops:
            # Simulates the LockResolutionError/lock-timeout path: the op's
            # inputs were stamped by `set_op_running` (M2's whole premise)
            # but the adapter was never actually invoked -- FAILED, not
            # COMPLETED.
            store.fail_op(status.deploy_id, op.op_id)
    finally:
        store.close()

    assert tap_status(db_path=db, target=te_id).bindings == [], (
        "a non-COMPLETED install-ovs-mirror row must not be reported as a "
        "live tap status binding"
    )
    assert tap_info(db_path=db, deploy_id=te_id).bindings == [], (
        "a non-COMPLETED install-ovs-mirror row must not be reported as a "
        "live tap info binding either"
    )


def test_tap_status_drops_a_de_scoped_sensor_matching_tap_info(tmp_path, manifests):
    """GAP-045 (code review, S3): narrowing `corp` from TWO sensors to ONE
    must make `tap status` agree with `tap info` -- both must report only
    the surviving sensor. `_delete_dropped_gretaps` removes the gretap
    NETDEV but, before this fix, never touched the dropped pair's OWN
    `attach-gre-tunnel` op row -- `force_reset_ops` only resets op_ids IN
    the new plan, so the dropped pair's row just sat there `completed`
    forever, and `_bindings_summary_from_ops` (unfiltered by op PRESENCE,
    only by status) kept reporting the de-scoped sensor as live. Reproduced
    live on the fixed tree pre-S3: `tap status: [pktxpress-1, suricata-1]`
    vs `tap info: [pktxpress-1]`."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status1 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(tap_point="corp", sensors=["pktxpress-1", "suricata-1"]),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status1.status == "completed"
    bindings1 = tap_status(db_path=db, target=te_id).bindings
    [corp1] = [b for b in bindings1 if b.tap == "corp"]
    assert sorted(corp1.sensors) == ["pktxpress-1", "suricata-1"]

    teardown = _FakeTeardown()
    status2 = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        teardown_backend=teardown,
    )
    assert status2.status == "completed"
    assert status2.deploy_id == status1.deploy_id

    bindings2 = tap_status(db_path=db, target=te_id).bindings
    [corp2] = [b for b in bindings2 if b.tap == "corp"]
    assert corp2.sensors == ["pktxpress-1"], (
        "tap status must drop the de-scoped sensor (suricata-1), not keep "
        f"reporting it forever: got {corp2.sensors!r}"
    )

    info_bindings = tap_info(db_path=db, deploy_id=te_id).bindings
    info_sensors = sorted({s for b in info_bindings for s in b.sensors})
    assert info_sensors == ["pktxpress-1"]
    # The two read-back surfaces must AGREE (GAP-044/LES-157's own point).
    assert sorted(corp2.sensors) == info_sensors


def test_tap_destroy_after_tap_point_apply_touches_only_tap(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )

    teardown = _FakeTeardown()
    result = destroy_taps(
        db_path=db, target=te_id, teardown_backend=teardown, adapter=_FakeMirAdapter(),
    )
    assert result.destroyed is True
    assert not any(k in ("vm", "vnet", "zone", "ovs-bridge") for k, _t in teardown.calls)

    store = StateStore(db)
    try:
        te = store.get_deploy(te_id)
        o2 = store.get_deploy(O2_DEPLOY)
        tap_dep = store.get_deploy(status.deploy_id)
    finally:
        store.close()
    assert te.status.value != "destroyed"
    assert o2.status.value != "destroyed"
    assert tap_dep.status.value == "destroyed"


# ---------------------------------------------------------------------------
# Adversarial deepening (tester pass, `docs/WORK_ORDER_tap_points.md` §4).
# ---------------------------------------------------------------------------

# LES-099 regression guard: `_delete_dropped_gretaps`'s unresolvable-bridge
# branch must LOG LOUDLY, never silently `continue` -- the exact bug class
# that orphaned a gretap in the previous build. `net_bridge` now covers
# every network a TE ever declared (`build_twin_inventory`), so this branch
# is "unreachable in practice" per the code's own comment -- exercised here
# directly (bypassing that invariant) to prove the GUARD ITSELF still does
# the right thing if it's ever hit again by a future regression.
def test_delete_dropped_gretaps_unresolvable_bridge_logs_loudly_not_silent(tmp_path):
    from axrange.o2control.taps import _TeInventory

    inv = _TeInventory(
        host_vmid={}, net_bridge={}, vlan_underlay=None, tappable={}, qga_hosts=set(),
    )
    teardown = _FakeTeardown()
    logs: list[str] = []
    store = StateStore(str(tmp_path / "state.db"))
    _delete_dropped_gretaps(
        inv, {("ghost-net", "pktxpress-1")}, needed_pairs=set(),
        teardown_backend=teardown, adapter_config=None, log=logs.append,
        plan_id="tap-te1", store=store, deploy_id="d1",
    )
    store.close()
    # LOUD: a log line was emitted (not silently swallowed)...
    assert any("could not resolve a bridge" in m for m in logs)
    assert any("ghost-net" in m and "pktxpress-1" in m for m in logs)
    # ...and no delete was attempted (there's no device name to delete).
    assert teardown.calls == []


def test_delete_dropped_gretaps_resolvable_bridge_actually_deletes(tmp_path):
    """The contrast case: when the bridge DOES resolve, the gretap is
    actually torn down (not just logged about) -- the drop path's positive
    behavior, alongside the loud-failure negative one above."""
    from axrange.o2control.taps import _TeInventory

    inv = _TeInventory(
        host_vmid={}, net_bridge={"corp": "br-corp"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
    )
    teardown = _FakeTeardown()
    logs: list[str] = []
    store = StateStore(str(tmp_path / "state.db"))
    _delete_dropped_gretaps(
        inv, {("corp", "pktxpress-1")}, needed_pairs=set(),
        teardown_backend=teardown, adapter_config=None, log=logs.append,
        plan_id="tap-te1", store=store, deploy_id="d1",
    )
    store.close()
    assert teardown.calls == [("gretap", n.gre_port_for_bridge_sensor("br-corp", "pktxpress-1"))]
    assert any("removed" in m for m in logs)


# ---------------------------------------------------------------------------
# 2026-07-25 fix: the VLAN-zone gretap DEVICE-ALIASING data-loss bug, found
# live on dev45. `gre_port_for_bridge_sensor(bridge, sensor)` keys the
# netdev on the underlay BRIDGE, not the net -- and in a VLAN zone every
# vnet in the zone shares ONE underlay bridge, so a dropped (net, sensor)
# pair can alias the SAME device a still-needed (net', sensor) pair uses.
# The drop-diff must therefore diff at the DEVICE level (skip a dropped
# pair's device if any needed pair still maps to it), not the pair level.
# ---------------------------------------------------------------------------
def test_delete_dropped_gretaps_keeps_device_shared_by_needed_pair_vlan_reshard(
    tmp_path,
):
    """The EXACT live case (dev45, re-shard swap): a VLAN zone with
    server-vlan/corp-lan/dmz all sharing ONE underlay bridge, swapped
    server-vlan<->o2-tcpdump / {corp-lan,dmz}<->o2-suricata. The prior apply's
    pairs are {(server-vlan, suricata), (corp-lan, tcpdump), (dmz, tcpdump)};
    the current needed set is {(server-vlan, tcpdump), (corp-lan, suricata),
    (dmz, suricata)}. Every dropped pair's gretap device is the SAME device a
    needed pair still uses (both (bridge, tcpdump) and (bridge, suricata)
    devices are needed) -- so NOTHING may be deleted, and the two live
    gretaps must survive the apply. This is the exact bug that silently
    killed capture on dev45 (2 gretaps present mid-apply, 0 the instant it
    finished) despite `apply` reporting `completed` and `verify-capture`
    staying green (LES-001 false-green)."""
    from axrange.o2control.taps import _TeInventory, _op_id

    shared_bridge = "tebr_shared"
    inv = _TeInventory(
        host_vmid={},
        net_bridge={
            "server-vlan": shared_bridge,
            "corp-lan": shared_bridge,
            "dmz": shared_bridge,
        },
        vlan_underlay=shared_bridge, tappable={}, qga_hosts=set(),
    )
    prior_pairs = {
        ("server-vlan", "o2-suricata"),
        ("corp-lan", "o2-tcpdump"),
        ("dmz", "o2-tcpdump"),
    }
    needed_pairs = {
        ("server-vlan", "o2-tcpdump"),
        ("corp-lan", "o2-suricata"),
        ("dmz", "o2-suricata"),
    }
    dropped_pairs = prior_pairs - needed_pairs
    assert dropped_pairs == prior_pairs  # none of the prior pairs survive AS PAIRS

    teardown = _FakeTeardown()
    logs: list[str] = []
    store = StateStore(str(tmp_path / "state.db"))
    # Seed a REAL deploy + the three dropped pairs' own attach-gre-tunnel rows,
    # so `mark_op_destroyed` is an observable UPDATE rather than a 0-row no-op.
    # Without this the assertions below are vacuous -- see the LES-160 note.
    store.create_deploy(
        deploy_id="d1", scenario_id="tap-te1", scenario_version=1,
        scenario_schema_version=1, plan_id="tap-te1", ir_hash="h",
        placement={}, tier="te",
    )
    dropped_op_ids = {
        (net, sensor): _op_id(
            "tap-te1", "attach-gre-tunnel", {"net": net, "sensor": sensor}
        )
        for net, sensor in dropped_pairs
    }
    store.insert_ops(
        "d1",
        [
            _op_row(
                "d1", op_id, "attach-gre-tunnel", "OP-MIR-010",
                {"net": net, "sensor": sensor},
            )
            for (net, sensor), op_id in dropped_op_ids.items()
        ],
    )
    _delete_dropped_gretaps(
        inv, dropped_pairs, needed_pairs=needed_pairs,
        teardown_backend=teardown, adapter_config=None, log=logs.append,
        plan_id="tap-te1", store=store, deploy_id="d1",
    )
    statuses = {
        o.op_id: o.status for o in store.ops_for_deploy("d1")
    }
    store.close()
    # The bug: this used to delete BOTH live devices. The fix: zero deletions,
    # because every dropped pair's device is still needed by some pair.
    assert teardown.calls == []
    assert all("KEPT" in m for m in logs)
    assert len(logs) == len(dropped_pairs)
    # GAP-045 / LES-160: the op row is marked destroyed for EVERY dropped pair,
    # INCLUDING these -- whose gretap DEVICE was deliberately KEPT under
    # VLAN-zone aliasing. "Is this binding still desired?" and "is the shared
    # device still needed?" are independent facts, and the dropped pair's own
    # `tc` mirror really is gone (`prior_hosts` re-asserts every previously
    # mirrored host, incl. the empty-`tapped_nics` clear). Gating the mark on
    # `delete_gretap` succeeding is the plausible "tighter" refactor and it
    # reintroduces GAP-045 for exactly this configuration -- this assertion is
    # the ONLY thing that fails when someone makes it.
    assert set(statuses) == set(dropped_op_ids.values())
    assert all(
        st == OpStatus.DESTROYED for st in statuses.values()
    ), f"every dropped pair's op must be DESTROYED even when its device is KEPT: {statuses}"


def test_delete_dropped_gretaps_deletes_device_when_sensor_fully_dropped_vlan(
    tmp_path,
):
    """Contrast case in the SAME shared-underlay-bridge setting: a sensor
    dropped ENTIRELY (no needed pair references it at all) has a genuinely
    unshared device -- it must still be deleted, not spuriously kept."""
    from axrange.o2control.taps import _TeInventory

    shared_bridge = "tebr_shared"
    inv = _TeInventory(
        host_vmid={},
        net_bridge={"server-vlan": shared_bridge, "corp-lan": shared_bridge},
        vlan_underlay=shared_bridge, tappable={}, qga_hosts=set(),
    )
    dropped_pairs = {("server-vlan", "o2-old-sensor")}
    needed_pairs = {("corp-lan", "o2-tcpdump")}  # unrelated sensor, no aliasing

    teardown = _FakeTeardown()
    logs: list[str] = []
    store = StateStore(str(tmp_path / "state.db"))
    _delete_dropped_gretaps(
        inv, dropped_pairs, needed_pairs=needed_pairs,
        teardown_backend=teardown, adapter_config=None, log=logs.append,
        plan_id="tap-te1", store=store, deploy_id="d1",
    )
    store.close()
    assert teardown.calls == [
        ("gretap", n.gre_port_for_bridge_sensor(shared_bridge, "o2-old-sensor"))
    ]
    assert any("removed" in m for m in logs)
    assert not any("KEPT" in m for m in logs)


# ---------------------------------------------------------------------------
# G2 (`docs/WORK_ORDER_tap_scopes.md`, final fix round): `vmbr0` (or
# whatever the cluster's real uplink bridge is) is node-SHARED, unlike every
# OTHER bridge that has ever fed `gre_port_for_bridge_sensor`/
# `gre_key_for_bridge_sensor` (all twin-unique by construction --
# `tebr_*`/`tev*`). Two twins' `perimeter: wan` bindings to the SAME sensor
# must resolve to DIFFERENT gretap devices, or tearing down one twin's tap
# plan silently kills the other's WAN capture (the LES-118 aliasing shape,
# one scope up). `_gre_naming_bridge` fixes this by discriminating the
# naming-seed with the tap-plan's own `plan_id` for the external bridge
# ONLY -- an ordinary twin-scoped bridge's name must stay BYTE-IDENTICAL
# (regression guard against renaming an already-deployed twin's live
# gretap).
# ---------------------------------------------------------------------------
def test_gre_naming_bridge_is_byte_identical_for_a_twin_scoped_bridge():
    """The CRITICAL regression guard: a twin-scoped bridge (`tebr_*`/
    `tev*`) must resolve to the EXACT SAME string regardless of `plan_id`
    -- discriminating it would rename the gretap device on an already-
    deployed twin and orphan its live capture."""
    inv = _TeInventory(
        host_vmid={}, net_bridge={"corp": "tebr_abc123"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
    )
    assert _gre_naming_bridge(inv, "corp", "tap-twin-a") == "tebr_abc123"
    assert _gre_naming_bridge(inv, "corp", "tap-twin-b") == "tebr_abc123"
    # Byte-identical to the RAW, undiscriminated `_bridge_for_net` value --
    # not just equal-looking.
    assert _gre_naming_bridge(inv, "corp", "tap-twin-a") == _bridge_for_net(inv, "corp")


def test_gre_naming_bridge_discriminates_the_shared_wan_bridge_per_twin():
    """The actual G2 fix: the SAME external bridge (`vmbr0`, published as
    `net_bridge["__wan__"]` since fix round F3), seeded by TWO DIFFERENT
    tap-plans' `plan_id`s, must resolve to two DIFFERENT naming-seed
    strings."""
    inv = _TeInventory(
        host_vmid={}, net_bridge={"__wan__": "vmbr0"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
    )
    bridge_a = _gre_naming_bridge(inv, "__wan__", "tap-twin-a")
    bridge_b = _gre_naming_bridge(inv, "__wan__", "tap-twin-b")
    assert bridge_a != bridge_b
    # And neither equals the raw, undiscriminated bridge name -- the WAN
    # bridge is NEVER used bare for naming purposes any more.
    assert bridge_a != "vmbr0"
    assert bridge_b != "vmbr0"


def test_two_twins_perimeter_wan_bindings_to_the_same_sensor_get_different_gretap_devices():
    """The end-to-end claim: two DIFFERENT twins (different `plan_id`s),
    each with a `perimeter: wan`-shaped binding to the SAME sensor, resolve
    to DIFFERENT `gre_port_for_bridge_sensor` device names -- so tearing
    down twin A's tap plan can never delete twin B's still-live WAN gretap
    (the concrete failure mode G2 fixes)."""
    inv = _TeInventory(
        host_vmid={}, net_bridge={"__wan__": "vmbr0"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
    )
    sensor_id = "o2-tcpdump"
    dev_twin_a = n.gre_port_for_bridge_sensor(
        _gre_naming_bridge(inv, "__wan__", "tap-twin-a"), sensor_id,
    )
    dev_twin_b = n.gre_port_for_bridge_sensor(
        _gre_naming_bridge(inv, "__wan__", "tap-twin-b"), sensor_id,
    )
    assert dev_twin_a != dev_twin_b
    # And a re-apply of the SAME tap-plan (same plan_id) resolves the SAME
    # device -- deterministic, not random per-run.
    dev_twin_a_again = n.gre_port_for_bridge_sensor(
        _gre_naming_bridge(inv, "__wan__", "tap-twin-a"), sensor_id,
    )
    assert dev_twin_a == dev_twin_a_again


def test_gretap_device_for_pair_and_delete_dropped_gretaps_use_plan_scoped_naming(tmp_path):
    """`_gretap_device_for_pair` (the de-scope diff's own device-name
    resolver) and `_delete_dropped_gretaps` (the actual delete path) must
    BOTH resolve the SAME plan-scoped WAN device -- if they disagreed, the
    de-scope diff could think a device is still "needed" by a different
    plan_id's computation, or delete the wrong twin's device outright."""
    from axrange.o2control.taps import _gretap_device_for_pair

    inv = _TeInventory(
        host_vmid={}, net_bridge={"__wan__": "vmbr0"}, vlan_underlay=None,
        tappable={}, qga_hosts=set(),
    )
    dev = _gretap_device_for_pair(inv, "__wan__", "o2-tcpdump", "tap-twin-a")
    assert dev == n.gre_port_for_bridge_sensor(
        _gre_naming_bridge(inv, "__wan__", "tap-twin-a"), "o2-tcpdump",
    )

    # `_delete_dropped_gretaps` deletes the SAME device for the SAME plan_id.
    teardown = _FakeTeardown()
    logs: list[str] = []
    store = StateStore(str(tmp_path / "state.db"))
    _delete_dropped_gretaps(
        inv, {("__wan__", "o2-tcpdump")}, needed_pairs=set(),
        teardown_backend=teardown, adapter_config=None, log=logs.append,
        plan_id="tap-twin-a", store=store, deploy_id="d1",
    )
    store.close()
    assert teardown.calls == [("gretap", dev)]


# ---------------------------------------------------------------------------
# Backward compatibility: `_scan_te_inventory`'s new optional `twin=` param
# must not change results for any EXISTING caller (none of which passes it).
# ---------------------------------------------------------------------------
def test_scan_te_inventory_twin_param_is_byte_identical_to_no_twin(tmp_path):
    import dataclasses as dc

    from axrange.planner.inventory import build_twin_inventory

    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        twin = build_twin_inventory(store, TE_DEPLOY)
        default = _scan_te_inventory(store, TE_DEPLOY)
        with_twin = _scan_te_inventory(store, TE_DEPLOY, twin=twin)
    finally:
        store.close()
    assert dc.asdict(default) == dc.asdict(with_twin)


# ---------------------------------------------------------------------------
# `_resolve_binding_targets`'s defensive internal-error path: a tap_point/
# selector binding invoked WITHOUT a resolved twin/points (every real caller,
# `resolve_tap_plan_bindings`, always supplies them -- this only exercises
# the guard itself).
# ---------------------------------------------------------------------------
def test_resolve_bindings_tap_point_without_twin_raises_internal_error(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    try:
        _seed_te(store)
        inv = _scan_te_inventory(store, TE_DEPLOY)
    finally:
        store.close()
    ifaces = _tapped_iface_set(inv)
    spec = TapPlanSpec(
        target=TE_DEPLOY,
        bindings=[TapBindingSpec(tap_point="corp", sensors=["s1"])],
    )
    with pytest.raises(TapPlanError, match="internal error"):
        _resolve_bindings(spec, ifaces, {}, points=None, twin=None)


# ---------------------------------------------------------------------------
# LES-098 posture (code review follow-up): `resolve_tap_plan_bindings` wraps
# BOTH `build_twin_inventory` and `derive_default_tap_points` in the SAME
# try/except that already catches `TapPointResolutionError` -- widened to
# also catch `InventoryError`/`TapPointShapeError`. Neither can fire from
# TODAY's auto-derivation path (tier is pre-checked; every auto-derived
# `TapPoint` is well-formed by construction) but `TapPointShapeError` exists
# FOR the imminent `docs/WORK_ORDER_twin_configs.md` author-declared
# `tap_points:` path -- simulated here by making `derive_default_tap_points`
# itself raise, so a malformed declared point maps to a `TapPlanError`(400)
# instead of escaping as a bare, uncaught exception (an API 500 / CLI
# traceback).
# ---------------------------------------------------------------------------
def test_resolve_tap_plan_bindings_maps_malformed_tap_point_shape_error_to_400(
    tmp_path, monkeypatch,
):
    db = _seeded_db(tmp_path)
    store = StateStore(db)

    def _boom(*args, **kwargs):
        raise TapPointShapeError("simulated malformed author-declared tap point")

    monkeypatch.setattr("axrange.o2control.taps.derive_default_tap_points", _boom)
    try:
        with pytest.raises(TapPlanError, match="simulated malformed") as exc:
            resolve_tap_plan_bindings(
                TapPlanSpec(
                    target=TE_DEPLOY,
                    bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
                ),
                store, db_path=db,
            )
        assert exc.value.status == 400
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Overlapping selectors: two DIFFERENT bindings whose targets overlap on the
# SAME interface, addressed to DIFFERENT sensors -- the per-interface
# resolution map is a UNION, so the interface genuinely fans out to BOTH
# sensors regardless of what mode either individual binding declared. This
# demonstrates `mode` is a per-binding sensor-count check only (parse-time),
# never a promise that this interface won't ALSO be reached by some other
# binding -- see the `mode: exclusive` doesn't cross-binding-exclude test
# below, which nails this down explicitly.
# ---------------------------------------------------------------------------
def test_two_overlapping_selectors_fan_out_to_both_sensors(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"]),
                TapBindingSpec(
                    selector={"scope": "segment"}, sensors=["suricata-1"],
                ),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    corp_sensors = {c[1]["sensor_id"] for c in gre_calls if c[1]["net_id"] == "corp"}
    # Both bindings' sensors reach the SAME (corp) interface -- a genuine
    # union/fan-out, not a last-writer-wins overwrite.
    assert corp_sensors == {"pktxpress-1", "suricata-1"}


# ---------------------------------------------------------------------------
# Capacity guardrail (`docs/WORK_ORDER_tap_eligibility.md` §7, Authority:
# user, 2026-07-28): a SOFT, non-blocking warning -- proceed anyway -- when
# a resolved plan's port count or (port, sensor) mirrored-copy count crosses
# a large threshold (full-topology eligibility, D1, makes "bind everything"
# trivial to author, and every fan-out duplicates frames on the shared
# transport wire).
# ---------------------------------------------------------------------------
def test_capacity_warning_below_both_thresholds_is_none():
    from axrange.o2control.taps import _capacity_warning

    small = {("h1", "corp", 0): {"pktxpress-1": "both"}}
    assert _capacity_warning(small) is None
    assert _capacity_warning({}) is None


def test_capacity_warning_fires_past_port_threshold_names_the_numbers():
    from axrange.o2control.taps import _CAPACITY_WARN_PORTS, _capacity_warning

    # One port over the port-count threshold, one sensor each (pair count
    # stays well under ITS threshold) -- isolates the port-count trigger.
    wide = {
        (f"h{i}", "corp", 0): {"pktxpress-1": "both"}
        for i in range(_CAPACITY_WARN_PORTS + 1)
    }
    msg = _capacity_warning(wide)
    assert msg is not None
    assert msg.startswith("capacity:")
    assert str(_CAPACITY_WARN_PORTS + 1) in msg  # names the actual port count
    assert str(_CAPACITY_WARN_PORTS + 1) in msg.split("producing")[1]  # pair count too (1 sensor/port)
    assert "1x" in msg  # heaviest single-port fan-out


def test_capacity_warning_fires_past_pair_threshold_via_heavy_fanout():
    from axrange.o2control.taps import _CAPACITY_WARN_PAIRS, _capacity_warning

    # Few ports, each fanned out to many sensors -- isolates the (port,
    # sensor) pair-count / fan-out trigger independent of port count.
    n_sensors = _CAPACITY_WARN_PAIRS // 2 + 1
    heavy = {
        (f"h{i}", "corp", 0): {f"s{j}": "both" for j in range(n_sensors)}
        for i in range(2)
    }
    msg = _capacity_warning(heavy)
    assert msg is not None
    assert msg.startswith("capacity:")
    assert f"{n_sensors}x" in msg  # heaviest single-port fan-out named


def test_apply_tap_plan_wide_bind_surfaces_capacity_warning_end_to_end(tmp_path):
    """Not just the pure function -- the real `apply_tap_plan` pipeline
    (`tap deploy`'s own entry point) actually threads the warning through
    `TapPlanStatus.warnings`, proving the wiring, not just the logic."""
    from axrange.o2control.taps import _CAPACITY_WARN_PORTS

    n_hosts = _CAPACITY_WARN_PORTS + 1
    deploy_id = "te-wide-1"
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="wide", scenario_version=1,
            scenario_schema_version=1, plan_id=f"{deploy_id}-plan", ir_hash="h",
            placement={f"h{i}": "pxtest1" for i in range(n_hosts)}, tier="te",
        )
        rows = [
            _op_row(deploy_id, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                    {"net": "corp"}, outputs={"bridge_name": "br-corp"}),
        ]
        for i in range(n_hosts):
            h = f"h{i}"
            rows.append(_op_row(deploy_id, f"op-clone-{h}", "clone-vm", "OP-HOST-010",
                                 {"host": h}, outputs={"vmid": 400000 + i}))
            rows.append(_op_row(deploy_id, f"op-attach-{h}", "attach-vm-to-vnet",
                                 "OP-HOST-040", {"host": h, "net": "corp"},
                                 inputs={"net_id": "corp", "nic_index": 0}))
        store.insert_ops(deploy_id, rows)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=deploy_id,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    capacity_warnings = [w for w in status.warnings if w.startswith("capacity:")]
    assert len(capacity_warnings) == 1
    assert str(n_hosts) in capacity_warnings[0]


# ---------------------------------------------------------------------------
# M4's own subtlest correctness claim (`docs/WORK_ORDER_tap_scopes.md`):
# **direction is UNIONED per (port, sensor)**, not last-binding-wins. Two
# bindings that both name the SAME sensor for the SAME interface -- one via
# a multi-port (`segment`-like, `"rx"`) address, the other via a single-port
# (`"both"`) address -- must produce exactly ONE mirror entry for that
# interface, carrying `"both"` (the union), regardless of binding order.
# `_seed_te`'s h1 (vmid 100001) is tapped on net `corp` nic 0 -- `tap: corp`
# (net-hit, `"rx"`) and `tap: h1` (host-hit, `"both"`) both resolve onto it.
# ---------------------------------------------------------------------------
def test_direction_union_segment_and_port_same_sensor_yields_one_both_mirror(tmp_path):
    db = _seeded_db(tmp_path)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[
                TapBindingSpec(tap="corp", sensors=["pktxpress-1"]),
                TapBindingSpec(tap="h1", sensors=["pktxpress-1"]),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"

    # Exactly one gretap for (corp, pktxpress-1) -- not duplicated by the two
    # bindings both claiming it.
    gre_calls = [
        c for c in adapter.calls if c[0] == "attach-gre-tunnel"
        and c[1]["net_id"] == "corp" and c[1]["sensor_id"] == "pktxpress-1"
    ]
    assert len(gre_calls) == 1

    mirror_calls = [
        c for c in adapter.calls if c[0] == "install-ovs-mirror" and c[1]["vmid"] == 100001
    ]
    assert len(mirror_calls) == 1
    tapped = mirror_calls[0][1]["tapped_nics"]
    matches = [
        e for e in tapped
        if e["nic_index"] == 0 and e["net_id"] == "corp" and e["sensor_id"] == "pktxpress-1"
    ]
    # ONE entry for this (nic, net, sensor) -- not two (one per binding).
    assert len(matches) == 1
    # "both" wins over "rx" -- neither a last-wins nor an rx-only result.
    assert matches[0]["direction"] == "both"


def test_direction_union_is_order_independent(tmp_path):
    """The SAME union as above, with the bindings reversed -- proving the
    result is a genuine commutative union (`_union_direction`), not an
    accident of processing order (a last-wins bug would flip the outcome
    when the "both"-direction binding is listed FIRST instead of second)."""
    db = _seeded_db(tmp_path)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[
                TapBindingSpec(tap="h1", sensors=["pktxpress-1"]),
                TapBindingSpec(tap="corp", sensors=["pktxpress-1"]),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    mirror_calls = [
        c for c in adapter.calls if c[0] == "install-ovs-mirror" and c[1]["vmid"] == 100001
    ]
    assert len(mirror_calls) == 1
    tapped = mirror_calls[0][1]["tapped_nics"]
    matches = [
        e for e in tapped
        if e["nic_index"] == 0 and e["net_id"] == "corp" and e["sensor_id"] == "pktxpress-1"
    ]
    assert len(matches) == 1
    assert matches[0]["direction"] == "both"


def test_mode_exclusive_is_enforced_against_every_other_binding(tmp_path, manifests):
    """M3 (code review, `docs/WORK_ORDER_tap_points.md`): `mode: exclusive`
    is now an ENFORCED cross-binding invariant, not just a per-binding
    sensor-count check -- an EARLIER revision validated only `len(sensors)
    == 1` at parse time and left it at that, so a SEPARATE `fanout` binding
    resolving onto the SAME interface(s) silently fanned the "exclusive"
    point out anyway (verified: `[{tap_point: corp, sensors:[sa],
    mode: exclusive}, {selector:{scope: segment}, sensors:[sb]}]` used to
    resolve `('rtr','corp',0) -> ['sa','sb']`). `apply_tap_plan` now raises,
    naming BOTH binding labels, instead of silently completing."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    with pytest.raises(TapPlanError, match="mode=exclusive") as exc:
        apply_tap_plan(
            spec=TapPlanSpec(
                target=te_id,
                bindings=[
                    TapBindingSpec(
                        tap_point="corp", sensors=["pktxpress-1"], mode="exclusive",
                    ),
                    TapBindingSpec(
                        selector={"scope": "segment", "target": "corp"},
                        sensors=["suricata-1"], mode="fanout",
                    ),
                ],
            ),
            db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        )
    # Both binding labels named, per the code-review fix's own requirement.
    assert "tap_point:corp" in str(exc.value)
    assert "selector:" in str(exc.value)


# ---------------------------------------------------------------------------
# M1 FIXED (`docs/WORK_ORDER_tap_points.md` code review): a binding built
# with NONE of tap/tap_point/selector set now raises at the RESOLUTION
# layer itself (`_resolve_binding_targets`'s `has_criteria` check), not just
# at `parse_tap_plan`'s parse-time gate -- so a `TapBindingSpec` constructed
# directly (bypassing `parse_tap_plan` entirely, e.g. the PRIOR shape of
# `axrange.api.service.o2_tap_apply`, which built `TapBindingSpec` straight
# from `dtos.TapBindingDTO` with no "exactly one" validator) can no longer
# silently resolve as "match every tap point in the twin." `o2_tap_apply`
# itself is ALSO fixed (routes the request through `parse_tap_plan`, see
# `tests/test_api.py`'s matching test) -- this test now additionally proves
# the fix holds even for a hand-built spec that skips `parse_tap_plan`
# altogether, which the API fix alone would not have covered.
# ---------------------------------------------------------------------------
def test_apply_tap_plan_binding_with_no_addressing_is_rejected(tmp_path, manifests):
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    with pytest.raises(TapPlanError, match="no addressing key") as exc:
        apply_tap_plan(
            spec=TapPlanSpec(
                target=te_id,
                # No tap/tap_point/selector at all -- exactly what a
                # TapBindingDTO with none of the three fields set used to
                # produce once handed to TapBindingSpec by o2_tap_apply.
                bindings=[TapBindingSpec(sensors=["pktxpress-1"])],
            ),
            db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
        )
    assert exc.value.status == 400


# ---------------------------------------------------------------------------
# `perimeter: wan` end-to-end (`docs/WORK_ORDER_tap_scopes.md` M2/M3) -- the
# twin's WHOLE EXTERNAL EDGE, addressed through every binding vocabulary a
# tap-plan supports. `_seed_tp_te`'s real twin (`tests/test_twin_inventory.py`
# `_build_and_run`) has r-edge (`kind: router`) with its synthetic WAN uplink
# (net_id `__wan__`, nic 2) ALWAYS marked tappable (M3) -- exactly the
# fixture this needs, no bespoke IR required.
# ---------------------------------------------------------------------------
def _wan_mirror_entries(adapter) -> list[dict]:
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    return [
        e for c in mirror_calls for e in c[1]["tapped_nics"]
        if e["net_id"] == "__wan__"
    ]


def test_apply_tap_plan_perimeter_wan_via_tap_point(tmp_path, manifests):
    """The auto-derived `wan-perimeter` tap point (id `f"wan-perimeter"`,
    M1) resolves the router's tapped WAN uplink and composes a `"both"`-
    direction mirror -- `perimeter`'s derived direction, brief §3/§4."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="wan-perimeter", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert any(
        c[1]["net_id"] == "__wan__" and c[1]["sensor_id"] == "pktxpress-1"
        for c in gre_calls
    )
    wan_entries = _wan_mirror_entries(adapter)
    assert len(wan_entries) == 1
    assert wan_entries[0]["sensor_id"] == "pktxpress-1"
    assert wan_entries[0]["direction"] == "both"


def test_apply_tap_plan_perimeter_wan_via_selector_alias(tmp_path, manifests):
    """`selector: {scope: perimeter, target: wan}` -- the author-facing
    `wan` alias (M3) resolved to the `__wan__` sentinel BEFORE
    `tappoints.py` ever sees it, matching the SAME `wan-perimeter` point
    `tap_point:` addresses by id, same `"both"` direction."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[
                TapBindingSpec(
                    selector={"scope": "perimeter", "target": "wan"},
                    sensors=["pktxpress-1"],
                ),
            ],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    wan_entries = _wan_mirror_entries(adapter)
    assert len(wan_entries) == 1
    assert wan_entries[0]["sensor_id"] == "pktxpress-1"
    assert wan_entries[0]["direction"] == "both"


def test_apply_tap_plan_legacy_host_tap_excludes_wan_uplink(tmp_path, manifests):
    """Fix round G7 (`docs/WORK_ORDER_tap_scopes.md`): since M3, r-edge's
    tappable set gained its synthetic `__wan__` uplink alongside its
    ordinary corp/dmz legs (`inv.tappable["r-edge"] ==
    [(0, "corp"), (1, "dmz"), (2, "__wan__")]`, `test_twin_inventory.py`).
    A legacy `tap: r-edge` (host-addressed) binding predates that and means
    "this host's own (LAN) interfaces" -- it must NOT silently pick up the
    WAN leg too just because M3 widened what's tappable on that host.
    Capturing the uplink is `perimeter`'s job (or an explicit `tap: wan`)."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="r-edge", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    tapped_nets = {
        e["net_id"] for c in mirror_calls for e in c[1]["tapped_nics"]
    }
    assert tapped_nets == {"corp", "dmz"}
    assert "__wan__" not in tapped_nets
    assert _wan_mirror_entries(adapter) == []


def test_apply_tap_plan_legacy_tap_wan_alias_resolves_interface(tmp_path, manifests):
    """The legacy, pre-scope-vocabulary `tap:` addressing ALSO resolves the
    `wan` alias (`_resolve_tap_target`'s own `_resolve_wan_alias` call) to
    the router's tapped WAN uplink -- the interface-selection half of M3
    works identically whether a binding names it by `tap:`, `tap_point:`,
    or `selector:`.

    Direction (fix round F2, `docs/WORK_ORDER_tap_scopes.md`): `tap: wan`
    resolves via `_resolve_tap_target`'s NET-hit branch, which
    `_resolve_binding_targets` would otherwise infer as `segment`-like and
    therefore `"rx"`-only -- but `__wan__` is special-cased to `"both"`
    REGARDLESS of which addressing path names it: every port on the WAN net
    is a router uplink (`perimeter` semantics), on every addressing path,
    not just `tap_point:`/`selector:`. An earlier revision left this as
    `"rx"` on the legacy path alone, silently losing everything inbound for
    an operator who happened to spell it `tap: wan` instead of
    `selector: {scope: perimeter, target: wan}` -- fixed here, not merely
    documented as a known gap."""
    db, te_id = _seed_tp_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap="wan", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    wan_entries = _wan_mirror_entries(adapter)
    assert len(wan_entries) == 1
    assert wan_entries[0]["sensor_id"] == "pktxpress-1"
    assert wan_entries[0]["direction"] == "both"  # fix round F2


# ---------------------------------------------------------------------------
# The FLAT / no-segmentation twin, end-to-end (`docs/design/02-twin-model.md`'s
# worked example, `sme-flat`) -- a GENUINE first-class case, not a
# token one: one net, THREE hosts, no router anywhere, all real
# build_plan+Executor output (LES-068 idiom) -- proving tap-point binding
# works cleanly for the exact topology §4.1 exists to make work, not just at
# the `axrange.planner.tappoints` unit level (`tests/test_planner_tappoints.py`
# already covers derivation/resolution against a hand-built flat
# `TwinInventory`; THIS closes the loop through a real op-derived twin +
# apply_tap_plan + the executor).
# ---------------------------------------------------------------------------
_FLAT_IR = """
scenario_schema_version: 1
scenario: { id: flat-roundtrip, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.9.0.0/24, vlan: 40 }
    nodes:
      - { id: h1, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - { id: h2, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - { id: h3, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
  apps: []
  activity: []
bindings: []
"""


def _seed_flat_te(tmp_path, manifests):
    """A genuinely flat twin (one net, three hosts, NO router/segmentation
    anywhere) plus a standing O2 surface, in the same state.db."""
    from axrange.ir.parser import parse_ir
    from axrange.planner import Executor
    from axrange.planner.cluster import ClusterContext
    from tests.test_twin_inventory import _FakeAdapter

    ir = parse_ir(text=_FLAT_IR).ir
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(ir, manifests, ctx, raw_ir={})

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    store.create_deploy(
        deploy_id="flat-1", scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(
        plan, store, adapter, ctx, config_backend=adapter,
        deploy_id="flat-1", sleep_fn=lambda _: None,
    )
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result

    _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    store.close()
    return db, "flat-1"


def test_flat_twin_derives_only_segment_and_port_points_from_a_real_deploy(
    tmp_path, manifests,
):
    from axrange.planner.inventory import build_twin_inventory
    from axrange.planner.tappoints import derive_default_tap_points

    db, deploy_id = _seed_flat_te(tmp_path, manifests)
    store = StateStore(db)
    try:
        twin = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    points = derive_default_tap_points(twin)
    by_scope: dict[str, list[str]] = {}
    for p in points:
        by_scope.setdefault(p.scope, []).append(p.id)
    assert set(by_scope) == {"segment", "port"}  # zero perimeter/boundary points
    assert by_scope["segment"] == ["corp"]
    assert set(by_scope["port"]) == {"h1:corp:0", "h2:corp:0", "h3:corp:0"}


def test_flat_twin_binds_cleanly_end_to_end_via_tap_point(tmp_path, manifests):
    """The bar §4.1 sets: a flat twin must produce USABLE tap points and
    BIND CLEANLY end to end -- a real `apply_tap_plan` run (through the
    engine executor, fake adapter) binding the `corp` segment tap point to
    a standing sensor, covering all three flat hosts."""
    db, te_id = _seed_flat_te(tmp_path, manifests)
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=te_id,
            bindings=[TapBindingSpec(tap_point="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=adapter, config_backend=adapter, target_node="px1",
    )
    assert status.status == "completed"
    mirror_calls = [c for c in adapter.calls if c[0] == "install-ovs-mirror"]
    hosts_touched = {c[1]["vmid"] for c in mirror_calls}
    assert len(mirror_calls) == 3  # all three flat hosts, none skipped
    assert len(hosts_touched) == 3
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 1  # one (net, sensor) pair for the whole segment
    assert gre_calls[0][1]["sensor_id"] == "pktxpress-1"
    verify_calls = [c for c in adapter.calls if c[0] == "verify-capture"]
    assert len(verify_calls) == 1  # capture heartbeat still fires for a flat twin


# ---------------------------------------------------------------------------
# Target resolution (`docs/WORK_ORDER_tap_target_resolution.md`) -- a
# predefined tap-plan's `target:` (or a `--target`/API override) can now be
# EITHER a literal deploy id OR a scenario name, resolved to the newest LIVE
# `te` deploy of that scenario.
# ---------------------------------------------------------------------------
def test_resolve_target_deploy_hits_literal_deploy_id(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        assert resolve_target_deploy(store, TE_DEPLOY) == TE_DEPLOY
    finally:
        store.close()


def test_resolve_target_deploy_resolves_scenario_name_to_newest_te(tmp_path):
    """`_seed_te` stamps `scenario_id="te-scn"` -- resolving that NAME (not
    the deploy id) must land on the same deploy."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        assert resolve_target_deploy(store, "te-scn") == TE_DEPLOY
    finally:
        store.close()


def test_resolve_target_deploy_picks_newest_by_updated_at(tmp_path):
    """Two `te` deploys of the SAME scenario name -- the resolver picks the
    one with the newer `updated_at` (LES-084), not merely the first one
    created."""
    import time as _t

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-older", scenario_id="corp-enterprise", scenario_version=1,
            scenario_schema_version=1, plan_id="p-older", ir_hash="h", placement={},
            tier="te",
        )
        _t.sleep(0.01)
        store.create_deploy(
            deploy_id="te-newer", scenario_id="corp-enterprise", scenario_version=1,
            scenario_schema_version=1, plan_id="p-newer", ir_hash="h", placement={},
            tier="te",
        )
        assert resolve_target_deploy(store, "corp-enterprise") == "te-newer"
    finally:
        store.close()


def test_resolve_target_deploy_excludes_destroyed(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-gone", scenario_id="ghost-scn", scenario_version=1,
            scenario_schema_version=1, plan_id="p-gone", ir_hash="h", placement={},
            tier="te",
        )
        store.mark_destroyed("te-gone")
        with pytest.raises(TapPlanError) as exc:
            resolve_target_deploy(store, "ghost-scn")
        assert exc.value.status == 404
    finally:
        store.close()


def test_resolve_target_deploy_excludes_tap_plan_playout_of_same_name(tmp_path):
    """LES-082: a tap-plan playout deploy is ALSO `tier="te"` and, if its
    scenario were literally named the SAME as a real TE's `plan_id` prefix
    convention (`tap-<real-target>`), must never be surfaced by name
    resolution. Reuses the SAME fixture `test_is_tap_plan_deploy_and_
    latest_tap_plan_deploy_skip_a_tap_named_te` builds (a genuine `te`
    deploy named `tap-foo` alongside an actual tap-plan playout)."""
    db = _seeded_db(tmp_path)
    _, _, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    assert status.status == "completed"

    store = StateStore(db)
    try:
        # The tap-plan playout's OWN scenario_id equals its plan_id
        # (`plan_id=f"tap-{TE_DEPLOY}"`, `_compose_ops`'s `Plan(scenario_id=
        # plan_id, ...)`) -- resolving that exact string as a "scenario
        # name" must NOT resolve to the tap-plan playout itself.
        tap_deploy = store.find_deploy_for_plan(f"tap-{TE_DEPLOY}")
        assert tap_deploy is not None
        with pytest.raises(TapPlanError) as exc:
            resolve_target_deploy(store, tap_deploy.scenario_id)
        assert exc.value.status == 404
    finally:
        store.close()


def test_resolve_target_deploy_neither_id_nor_name_raises_404(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        with pytest.raises(TapPlanError) as exc:
            resolve_target_deploy(store, "totally-nonexistent")
        assert exc.value.status == 404
    finally:
        store.close()


def test_resolve_live_target_deploy_accepts_a_live_te(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        row = resolve_live_target_deploy(store, "te-scn")
        assert row.deploy_id == TE_DEPLOY
    finally:
        store.close()


def test_resolve_live_target_deploy_rejects_destroyed_literal_id(tmp_path):
    """A literal deploy id that resolves (`resolve_target_deploy`'s
    id-direct path never re-checks liveness) but is DESTROYED must still be
    refused by the stricter `resolve_live_target_deploy` (work order §6:
    `tap validate`'s target-present mode)."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        store.mark_destroyed(TE_DEPLOY)
        with pytest.raises(TapPlanError) as exc:
            resolve_live_target_deploy(store, TE_DEPLOY)
        assert exc.value.status == 400
    finally:
        store.close()


def test_resolve_live_target_deploy_rejects_non_te_tier(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="o2-thing", scenario_id="o2-surface", scenario_version=1,
            scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
            tier="o2",
        )
        with pytest.raises(TapPlanError) as exc:
            resolve_live_target_deploy(store, "o2-thing")
        assert exc.value.status == 400
    finally:
        store.close()


def test_resolve_live_target_deploy_rejects_a_tap_plan_playout_id(tmp_path):
    """SHOULD-FIX (code review): `tap validate`'s target-PRESENT mode must
    reject a literal `--target <tap-plan-playout-deploy-id>` with a clear,
    actionable message -- a tap-plan playout passes `resolve_target_deploy`
    (it's a real deploy id) AND the live/tier=te/non-destroyed check (a
    tap-plan playout IS tier="te" and non-destroyed), so only the
    structural `is_tap_plan_deploy` re-check catches this."""
    db = _seeded_db(tmp_path)
    _, _, status = _apply(
        tmp_path, [TapBindingSpec(tap="corp", sensors=["pktxpress-1"])], db=db,
    )
    assert status.status == "completed"

    store = StateStore(db)
    try:
        with pytest.raises(TapPlanError, match="tap-plan playout") as exc:
            resolve_live_target_deploy(store, status.deploy_id)
        assert exc.value.status == 400
    finally:
        store.close()


# ---------------------------------------------------------------------------
# `apply_tap_plan`'s `target=` override -- effective-target precedence and
# the "neither present" hard error (`docs/WORK_ORDER_tap_target_resolution.md`).
# ---------------------------------------------------------------------------
def test_apply_tap_plan_override_target_wins_over_plan_target(tmp_path):
    """`--target`/the `target=` kwarg OVERRIDES the plan's own `target:`."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store, deploy_id=TE_DEPLOY)
        _seed_te(store, deploy_id="te-other", with_native_gre=False)
    finally:
        store.close()
    # Give the two deploys distinct scenario_ids so we can address the
    # SECOND one by name via the override, unambiguously.
    store = StateStore(db)
    try:
        store._conn.execute(
            "UPDATE deploys SET scenario_id = 'te-other-scn' WHERE deploy_id = 'te-other'"
        )
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,  # the PLAN's own target
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        target="te-other-scn",  # the OVERRIDE -- must win
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    assert status.target == "te-other"


def test_apply_tap_plan_no_target_anywhere_raises_before_any_state_write(tmp_path):
    db = str(tmp_path / "state.db")
    spec = TapPlanSpec(
        target=None,
        bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
    )
    with pytest.raises(TapPlanError) as exc:
        apply_tap_plan(spec=spec, db_path=db, target_node="pxtest1")
    assert exc.value.status == 400
    assert "no tap-plan target" in exc.value.message
    # No state written at all -- the DB file itself must not even exist
    # (StateStore's own file-create side effect never ran).
    assert not Path(db).exists()


def test_apply_tap_plan_override_target_supplies_missing_plan_target(tmp_path):
    """A PREDEFINED plan (`target=None`, no `target:` at all) applies to
    completion when `--target`/the `target=` kwarg supplies the missing
    target by NAME -- the actual functional (not merely CLI-plumbing) case
    `docs/WORK_ORDER_tap_target_resolution.md` done-criterion #1 describes:
    "a plan file with no `target:` + `tap deploy --plan f --target
    corp-enterprise` deploys, resolving `corp-enterprise` ... to the newest
    live `te` deploy." (`test_apply_tap_plan_override_target_wins_over_plan_
    target` above only covers OVERRIDE-WINS-OVER-PRESENT-PLAN-TARGET; this
    covers OVERRIDE-SUPPLIES-ABSENT-PLAN-TARGET, the other half of the `or`
    in `effective_target = target or resolved.target`.)"""
    db = _seeded_db(tmp_path)  # TE_DEPLOY, scenario_id="te-scn"
    adapter = _FakeMirAdapter()
    status = apply_tap_plan(
        spec=TapPlanSpec(
            target=None,  # no target in the plan itself
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        target="te-scn",  # the override, resolved by SCENARIO NAME
        db_path=db, adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status.status == "completed"
    assert status.target == TE_DEPLOY
    gre_calls = [c for c in adapter.calls if c[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 1


def test_apply_tap_plan_no_target_anywhere_adds_no_deploy_row_to_existing_db(tmp_path):
    """A stricter version of `test_apply_tap_plan_no_target_anywhere_raises_
    before_any_state_write` above (which only asserts the DB FILE never gets
    created): here the DB already has real deploys on file (an existing `te`
    + standing `o2` surface) BEFORE the no-target call, so a bare "file
    exists" check wouldn't catch a bug that appended a stray row to an
    ALREADY-EXISTING state.db. Asserts the deploy ROW COUNT is unchanged."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        before = {row.deploy_id for row in store.latest_deploys(limit=100)}
    finally:
        store.close()

    spec = TapPlanSpec(
        target=None,
        bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
    )
    with pytest.raises(TapPlanError) as exc:
        apply_tap_plan(spec=spec, db_path=db, target_node="pxtest1")
    assert exc.value.status == 400

    store = StateStore(db)
    try:
        after = {row.deploy_id for row in store.latest_deploys(limit=100)}
    finally:
        store.close()
    assert after == before
