"""Tests for `axrange.planner.inventory` (the twin-inventory keystone,
`docs/design/02-twin-model.md`, `docs/WORK_ORDER_tier_split.md`;
consolidated review fix pass, 2026-07-23; tap-agnostic twin substrate,
`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28).

Drives a REAL `build_plan` + `Executor` run against a fake adapter (the same
idiom `tests/test_planner_executor.py` uses) so `clone-vm`/`attach-vm-to-
vnet`'s inputs are read back from ACTUALLY PERSISTED op rows, not a
hand-built fixture standing in for the real serialization path (LES-068).

No `tap_points[]`/`record-tap-intent` anywhere in this file any more (D5/D4)
-- the twin publishes only `hosts[].nets[]`; `h-untapped` (a host with no
`tap:` in the OLD model) is renamed `h-other` throughout, since there is no
more "tapped vs. untapped host" distinction to name it after.
"""
from __future__ import annotations

import pytest

from axrange.ir.parser import parse_ir
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.inventory import (
    HostInfo,
    HostNet,
    InventoryError,
    build_twin_inventory,
)


_IR = """
scenario_schema_version: 1
scenario: { id: inventory-roundtrip, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.0.0.0/24, vlan: 10 }
      - { id: dmz, cidr: 10.0.1.0/24, vlan: 11 }
    nodes:
      - { id: h-a, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - { id: h-other, kind: host, net: dmz, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
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


class _FakeAdapter:
    """Canned per-op outputs; `clone-vm` returns a DISTINCT vmid per host
    (keyed by scope) so hosts stay individually identifiable — unlike
    `tests/test_planner_executor.py`'s single-host-oriented fake."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next_vmid = 100100

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == "clone-vm":
            self._next_vmid += 1
            return {"vmid": self._next_vmid}
        if op.op_name == "create-ovs-bridge":
            # This fixture's networks are all VLAN'd, so pass1_emit emits
            # exactly ONE underlay-bridge op (scope {"scenario": ...}) —
            # `build_twin_inventory` needs a real `bridge_name` output to
            # populate `net_bridge`.
            return {"bridge_name": "tebr_test"}
        return {}


def _build_and_run(tmp_path, manifests, *, deploy_id="inv-te-1"):
    ir = parse_ir(text=_IR).ir
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(ir, manifests, ctx, raw_ir={})

    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                  deploy_id=deploy_id, sleep_fn=lambda _: None)
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    return store, deploy_id


def test_hosts_cover_every_cloned_host(tmp_path, manifests):
    """hosts[] includes h-other alongside h-a/r-edge -- clone-vm alone is
    enough to earn a roster row (`docs/WORK_ORDER_tap_eligibility.md`: there
    is no more tap-gated/untapped host distinction at all). `nets`/`os` are
    sourced from UNCONDITIONAL ops (`attach-vm-to-vnet`/`clone-vm`), so every
    host gets them populated — only `role` (never fabricated, review
    MUST-FIX #2) stays `None` for everyone here."""
    store, deploy_id = _build_and_run(tmp_path, manifests)
    try:
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()

    assert inv.deploy_id == deploy_id
    assert inv.deploy_status == "completed"
    by_id = {h.host_id: h for h in inv.hosts}
    assert set(by_id) == {"h-a", "h-other", "r-edge"}

    other = by_id["h-other"]
    assert other.vmid is not None
    assert other.vm_status == "completed"
    assert other.vm_name is not None and other.vm_name.startswith("te-")
    assert other.pve_node == "px1"
    assert other.role is None  # never fabricated -- persona_library's job
    assert other.nets == [HostNet(net_id="dmz", ip=None, nic_index=0)]
    assert other.os == "linux"

    h_a = by_id["h-a"]
    assert h_a.vmid is not None
    assert h_a.vmid != other.vmid
    assert h_a.role is None
    assert h_a.nets == [HostNet(net_id="corp", ip=None, nic_index=0)]
    assert h_a.os == "linux"

    router = by_id["r-edge"]
    assert router.role is None
    # Multi-homed: BOTH legs carried, not just the first (review MUST-FIX #2),
    # PLUS the synthetic WAN uplink now RECORDED (not discarded, `docs/
    # WORK_ORDER_tap_scopes.md` M2), marked `external=True`. Sorted by
    # nic_index for comparison -- `nets`' underlying order follows
    # `ops_for_deploy`'s op_id-hash sort (`StateStore.ops_for_deploy`), not
    # NIC/emission order, so it is not meaningfully orderable otherwise.
    assert sorted(router.nets, key=lambda n: n.nic_index or 0) == [
        HostNet(net_id="corp", ip="10.0.0.1", nic_index=0),
        HostNet(net_id="dmz", ip="10.0.1.1", nic_index=1),
        HostNet(net_id="__wan__", ip=None, nic_index=2, external=True),
    ]


def test_os_is_none_without_a_manifests_registry(tmp_path, manifests):
    """`os` must never be fabricated: omitting `manifests` (or its lookup
    failing) publishes `None`, not a guessed `"linux"` default (review
    MUST-FIX #2, review finding 12)."""
    store, deploy_id = _build_and_run(tmp_path, manifests)
    try:
        inv = build_twin_inventory(store, deploy_id)  # no manifests passed
    finally:
        store.close()

    assert all(h.os is None for h in inv.hosts)


# `test_tap_points_derive_from_record_tap_intent` REMOVED here (`docs/
# WORK_ORDER_tap_eligibility.md` D5, Authority: user, 2026-07-28) -- there is
# no more `tap_points[]`/`record-tap-intent` for it to test; the WAN-uplink
# recording it also covered is proven by `test_hosts_cover_every_cloned_host`
# above (`r-edge`'s `HostNet(net_id="__wan__", ...)` assertion).


def test_to_dict_shape_round_trips_json_safe(tmp_path, manifests):
    import json

    store, deploy_id = _build_and_run(tmp_path, manifests)
    try:
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()

    d = inv.to_dict()
    # json-serializable (the CLI's --json path relies on this).
    reparsed = json.loads(json.dumps(d))
    assert reparsed["deploy_id"] == deploy_id
    assert reparsed["deploy_status"] == "completed"
    assert isinstance(reparsed["hosts"], list) and reparsed["hosts"]
    # No `tap_points` key at all (`docs/WORK_ORDER_tap_eligibility.md` D5).
    assert "tap_points" not in reparsed
    # `vm_name` is the ONE host-identity field name now -- no `node` alias
    # (review SHOULD-FIX #2, second round: dropped rather than shipped
    # alongside `vm_name`, since this module has zero pre-existing consumers
    # and the alias had already produced a shape mismatch against the API's
    # `HostInventoryDTO`, which never carried it).
    host_keys = set(reparsed["hosts"][0])
    assert host_keys == set(HostInfo.__dataclass_fields__)
    assert "node" not in host_keys


def test_unknown_deploy_raises(tmp_path, manifests):
    store, _deploy_id = _build_and_run(tmp_path, manifests)
    try:
        with pytest.raises(InventoryError):
            build_twin_inventory(store, "does-not-exist")
    finally:
        store.close()


def test_unrecognized_tier_raises_inventory_error_not_bare_value_error(tmp_path):
    """Same crash class `planner/teardown.py::destroy_deploy` closes (review
    MUST-FIX #4, second round): `vm_name_for_host(..., tier=deploy.tier or
    "te")` would otherwise raise a bare, untyped `ValueError`
    (`adapter_proxmox.naming._check_tier`) for an unrecognized `deploys.tier`
    -- routed through this module's OWN `InventoryError` instead, so every
    caller's `except InventoryError` handling covers it.

    `StateStore.create_deploy` now validates `tier=` at write time (review
    MUST-FIX #13), so provoking this requires raw-SQL seeding -- bypassing
    `create_deploy` entirely, exactly like a stale/hand-edited row would."""
    import time as _time

    db = str(tmp_path / "state.db")
    deploy_id = "dep-bad-tier"
    store = StateStore(db)
    now = _time.time()
    store._conn.execute(
        """
        INSERT INTO deploys (
            deploy_id, scenario_id, scenario_version, scenario_schema_version,
            plan_id, ir_hash, status, placement_json, started_at, updated_at,
            completed_at, tier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (deploy_id, "sc", 1, 1, "plan-raw", "hash", "completed", "{}", now, now, now, "tap"),
    )
    try:
        with pytest.raises(InventoryError, match="unrecognized tier"):
            build_twin_inventory(store, deploy_id)
    finally:
        store.close()


def test_scan_te_inventory_discovers_the_full_topology_no_tap_gate(
    tmp_path, manifests
):
    """`docs/WORK_ORDER_tap_eligibility.md` D1/D4 (Authority: user,
    2026-07-28): A2's `_scan_te_inventory` (a thin adapter over
    `build_twin_inventory`, review SHOULD-FIX #8) discovers its `tappable`
    set from `hosts[].nets[]` -- the FULL attachment topology -- not a
    `record-tap-intent`-resolved subset (the op no longer exists at all: a
    te deploy emits NO OP-MIR op). Proven against a REAL build_plan+Executor
    run (LES-068), not a hand-seeded fixture: `h-other` (which carried NO
    special "tap" declaration in the OLD model) is now EQUALLY eligible as
    h-a/r-edge -- this is the headline behavior change D1 makes."""
    from axrange.o2control.taps import _scan_te_inventory

    store, deploy_id = _build_and_run(tmp_path, manifests, deploy_id="inv-te-scan")
    try:
        # Sanity: no OP-MIR op of any kind is recorded for this deploy at all
        # (the te's entire tap-emission surface is now empty).
        ops = store.ops_for_deploy(deploy_id)
        mir_ops = [o for o in ops if o.op_name.startswith("record-tap") or "mirror" in o.op_name]
        assert mir_ops == [], f"expected zero OP-MIR ops, got {[o.op_name for o in mir_ops]}"

        inv = _scan_te_inventory(store, deploy_id)
    finally:
        store.close()

    # LIST equality, not `set(...)` (review SHOULD-FIX #3): `_scan_te_
    # inventory` sorts each host's tappable list by nic_index.
    assert inv.tappable.get("h-a") == [(0, "corp")]
    # r-edge (kind: router) carries its WAN uplink (nic 2) too -- recorded
    # unconditionally by `attach-vm-to-vnet`, same as every other leg.
    assert inv.tappable.get("r-edge") == [(0, "corp"), (1, "dmz"), (2, "__wan__")]
    # h-other is now IN `tappable` -- the headline D1 behavior change (it
    # would have been ABSENT under the old tap-gated model).
    assert inv.tappable.get("h-other") == [(0, "dmz")]
    # host_vmid discovery (clone-vm) is unaffected.
    assert set(inv.host_vmid) == {"h-a", "h-other", "r-edge"}


# ---------------------------------------------------------------------------
# `net_bridge`/`vlan_underlay` key-domain restoration (review MUST-FIX,
# second round). Own small VLAN-zoned IR with a SECOND network (`mgmt`), so
# `net_bridge` can only cover it via the VLAN-underlay backfill (a VLAN
# zone has no per-net `create-ovs-bridge` op) -- exactly the case an
# earlier version of this fix narrowed away.
# ---------------------------------------------------------------------------
_IR_UNTAPPED_NET = """
scenario_schema_version: 1
scenario: { id: net-bridge-domain, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.1.0.0/24, vlan: 20 }
      - { id: mgmt, cidr: 10.1.1.0/24, vlan: 21 }
    nodes:
      - { id: h-a, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - { id: h-mgmt, kind: host, net: mgmt, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
  apps: []
  activity: []
bindings: []
"""


def _build_and_run_second_net(tmp_path, manifests, *, deploy_id="inv-net-bridge-1"):
    ir = parse_ir(text=_IR_UNTAPPED_NET).ir
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(ir, manifests, ctx, raw_ir={})

    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                  deploy_id=deploy_id, sleep_fn=lambda _: None)
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    return store, deploy_id


def test_net_bridge_covers_every_declared_network(tmp_path, manifests):
    """`net_bridge`/`vlan_underlay` must resolve EVERY declared network (via
    the VLAN-underlay backfill, since a VLAN zone has no per-net
    `create-ovs-bridge` op of its own) -- a tap-plan binding may name ANY
    of them (`docs/WORK_ORDER_tap_eligibility.md` D1: full-topology
    eligibility, no gate). This is the exact key domain an earlier version
    of this fix narrowed to "only nets with a tap point" (a concept that no
    longer exists at all), which bit `_delete_dropped_gretaps` on a
    tap-plan's drop path (review MUST-FIX, second round)."""
    store, deploy_id = _build_and_run_second_net(tmp_path, manifests)
    try:
        inv = build_twin_inventory(store, deploy_id)
    finally:
        store.close()

    assert inv.vlan_underlay is not None
    assert inv.net_bridge.get("mgmt") == inv.vlan_underlay
    assert inv.net_bridge.get("corp") == inv.vlan_underlay


def test_scan_te_inventory_bridge_for_net_resolves_any_declared_network(
    tmp_path, manifests
):
    """The same proof via A2's own `_bridge_for_net`/`_scan_te_inventory` --
    the exact path `_delete_dropped_gretaps` calls on the DROP side of a
    tap-plan apply. Must resolve `mgmt`'s bridge."""
    from axrange.o2control.taps import _bridge_for_net, _scan_te_inventory

    store, deploy_id = _build_and_run_second_net(
        tmp_path, manifests, deploy_id="inv-net-bridge-scan"
    )
    try:
        inv = _scan_te_inventory(store, deploy_id)
    finally:
        store.close()

    assert inv.vlan_underlay is not None
    assert _bridge_for_net(inv, "mgmt") is not None
    assert _bridge_for_net(inv, "mgmt") == inv.vlan_underlay


# ---------------------------------------------------------------------------
# W1 -- role precedence (`docs/WORK_ORDER_role_persona_tier.md` §4.2). The
# published role is EFFECTIVE (a tfc assignment wins); the twin's own value
# stays visible as `role_baseline`. This is the change that makes a role
# re-assignable without re-cloning the node -- the role used to live INSIDE
# clone-vm's persisted inputs, which is exactly why changing one needed a
# redeploy.
#
# NOTE `_IR` above declares no `role:` on any node, so the BASELINE is None
# here; that is the interesting case for a bare-bone twin (the whole role map
# comes from the tfc tier) and it keeps these tests independent of the
# fixture's content.
# ---------------------------------------------------------------------------
def test_role_override_wins_over_clone_vm_baseline(tmp_path, manifests):
    store, deploy_id = _build_and_run(tmp_path, manifests, deploy_id="inv-role-1")
    try:
        inv = build_twin_inventory(
            store, deploy_id, manifests=manifests,
            role_overrides={"h-a": "file-server"},
        )
    finally:
        store.close()
    by_id = {h.host_id: h for h in inv.hosts}
    assert by_id["h-a"].role == "file-server"      # effective
    assert by_id["h-a"].role_baseline is None      # twin declared none
    # An un-overridden host is untouched.
    assert by_id["h-other"].role is None


def test_no_override_is_identical_to_the_baseline(tmp_path, manifests):
    """Omitting `role_overrides` must reproduce the pre-2026-08-04 behavior
    exactly -- every existing caller keeps working unchanged."""
    store, deploy_id = _build_and_run(tmp_path, manifests, deploy_id="inv-role-2")
    try:
        inv = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()
    for h in inv.hosts:
        assert h.role == h.role_baseline


def test_override_for_an_unknown_host_is_ignored(tmp_path, manifests):
    """A stale override naming a host this twin doesn't have must not
    fabricate a roster entry."""
    store, deploy_id = _build_and_run(tmp_path, manifests, deploy_id="inv-role-3")
    try:
        inv = build_twin_inventory(
            store, deploy_id, manifests=manifests, role_overrides={"ghost": "x"},
        )
    finally:
        store.close()
    assert "ghost" not in {h.host_id for h in inv.hosts}


def test_a_swept_host_leaves_the_published_inventory(tmp_path, manifests):
    """`te deploy --update --allow-destroy` tombstones a removed host's op rows
    `destroyed`. Its VM is GONE from the cluster, so it is no longer part of the
    twin's published binding contract.

    Measured on dev45 (2026-08-27) when this did not hold: after a 73-host
    removal the inventory still reported 196 hosts for a 123-host twin, the tap
    playout resolved a `segment` scope onto the deleted hosts, and 73
    `install-ovs-mirror` ops failed with `qm set <vmid> --hookscript ...` ->
    "Configuration file ... does not exist", taking the whole re-apply down --
    the very re-apply the update's own dangling-sibling warning tells the
    operator to run.
    """

    store, deploy_id = _build_and_run(tmp_path, manifests, deploy_id="inv-swept-1")
    try:
        before = {h.host_id for h in build_twin_inventory(store, deploy_id).hosts}
        victim = sorted(before)[0]
        for op in store.ops_for_deploy(deploy_id):
            if op.scope_json and f'"{victim}"' in op.scope_json:
                store.mark_op_destroyed(deploy_id, op.op_id)
        after = {h.host_id for h in build_twin_inventory(store, deploy_id).hosts}
    finally:
        store.close()

    assert victim in before
    assert victim not in after
    assert after == before - {victim}
