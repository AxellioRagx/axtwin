"""Tests for `axrange destroy` teardown (`axrange/planner/teardown.py`).

Drives the orchestrator with a fake `TeardownBackend` (no Proxmox) over a real
StateStore, seeded with deploys whose op outputs carry REAL deterministic names
(`adapter_proxmox/naming.py`) so the name-guards are exercised faithfully.

Covers: full reverse-order sweep; idempotent re-run; lease release; the
name-guards (foreign vmid refused, `vmbr0` never reaches the bridge delete);
error-continues-without-clean-sweep; unknown deploy.
"""
from __future__ import annotations

import hashlib
import json

from axrange.adapter_proxmox import AdapterError
from axrange.adapter_proxmox.naming import (
    gre_port_for_bridge_sensor,
    img_vm_name_for_image,
    underlay_bridge_for_plan,
    vm_name_for_host,
    vmid_for_op,
    vnet_id_for_net,
    zone_id_for_plan,
)
from axrange.planner import StateStore, TeardownError, destroy_deploy, destroy_ops_scoped
from axrange.planner.state import OpRow, OpStatus


# ---------------------------------------------------------------------------
# Fakes + seeding
# ---------------------------------------------------------------------------
class _RecordTeardown:
    """Records every backend call; returns a fixed status (default 'removed').
    `fail_on` makes the named kind raise, to test continue-on-error.
    `vm_names` simulates the PVE-resolved display name per vmid (the protect
    guard's Model-C name assertion, docs/WORK_ORDER_resource_tags.md §3) —
    a vmid absent from the map resolves to `None`, matching a real backend's
    "no name available" fallback."""

    def __init__(self, status: str = "removed", fail_on: str | None = None,
                 vm_names: dict[int, str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status = status
        self.fail_on = fail_on
        self.vm_names = vm_names or {}

    def _rec(self, kind: str, target: str) -> str:
        self.calls.append((kind, target))
        if self.fail_on == kind:
            raise AdapterError(f"boom {kind}")
        return self.status

    def vm_name(self, node, vmid):              return self.vm_names.get(vmid)
    def destroy_vm(self, node, vmid):           return self._rec("vm", str(vmid))
    def delete_mirror_snippets(self, node, vmid): return self._rec("mirror", str(vmid))
    def delete_gretap(self, dev):               return self._rec("gretap", dev)
    def delete_vnet(self, vnet_id):             return self._rec("vnet", vnet_id)
    def delete_sdn_zone(self, zone_id):         return self._rec("zone", zone_id)
    def apply_sdn(self):                        return self._rec("sdn-apply", "")
    def delete_ovs_bridge(self, node, b):       return self._rec("ovs-bridge", b)


def _op(deploy_id, op_id, op_name, scope, outputs, *, status=OpStatus.COMPLETED):
    return OpRow(
        op_id=op_id, deploy_id=deploy_id, op_name=op_name, op_catalog_id="OP-X",
        kind="action", scope_json=json.dumps(scope),
        inputs_resolved_json="{}", inputs_from_outputs_json="{}",
        outputs_json=json.dumps(outputs), cleanup_strategy=None,
        idempotency_class=None, status=status, started_at=1.0, completed_at=2.0,
    )


def seed_deploy(db_path, *, deploy_id="dep-x", scenario_id="sc",
                plan_id="plan-abc", node="pve1",
                vmid=None, bridge=None):
    """Create a completed-ish deploy with realistic infra op outputs. Returns a
    dict of the seeded resource ids so tests can assert against them."""
    store = StateStore(db_path)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=scenario_id, scenario_version=1,
        scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
        placement={"h-a": node},
    )
    real_bridge = underlay_bridge_for_plan(plan_id)
    bridge = bridge or real_bridge
    zone = zone_id_for_plan(plan_id)
    vnet = vnet_id_for_net("corp", plan_id=plan_id)
    gretap = gre_port_for_bridge_sensor(real_bridge, "default")
    vmid = vmid if vmid is not None else vmid_for_op(plan_id, "clone-h-a")
    ops = [
        _op(deploy_id, "op1", "create-ovs-bridge", {"net": "corp"}, {"bridge_name": bridge}),
        _op(deploy_id, "op2", "create-sdn-zone", {}, {"zone_id": zone}),
        _op(deploy_id, "op3", "create-vnet", {"net": "corp"}, {"vnet_id": vnet}),
        _op(deploy_id, "op4", "clone-vm", {"host": "h-a"}, {"vmid": vmid}),
        _op(deploy_id, "op5", "attach-gre-tunnel", {"net": "corp"}, {"gre_port": gretap}),
        _op(deploy_id, "op6", "install-ovs-mirror", {"host": "h-a"}, {"hook_installed": True}),
    ]
    store.insert_ops(deploy_id, ops)
    store.close()
    return {
        "deploy_id": deploy_id, "scenario_id": scenario_id, "plan_id": plan_id,
        "node": node, "bridge": bridge, "zone": zone, "vnet": vnet,
        "gretap": gretap, "vmid": vmid,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_full_teardown_reverse_order(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])

        # Reverse dependency order: VMs → mirror → gretap → vnet → zone →
        # sdn-apply → ovs-bridge.
        assert [k for k, _ in backend.calls] == [
            "vm", "mirror", "gretap", "vnet", "zone", "sdn-apply", "ovs-bridge",
        ]
        # Right targets reached the backend.
        assert ("vm", str(info["vmid"])) in backend.calls
        assert ("gretap", info["gretap"]) in backend.calls
        assert ("vnet", info["vnet"]) in backend.calls
        assert ("zone", info["zone"]) in backend.calls
        assert ("ovs-bridge", info["bridge"]) in backend.calls

        assert result.destroyed is True
        assert result.errors == []
        assert result.removed >= 6

        # State transitioned: deploy + every op are `destroyed`.
        d = store.get_deploy(info["deploy_id"])
        assert d.status.value == "destroyed"
        assert all(op.status.value == "destroyed"
                   for op in store.ops_for_deploy(info["deploy_id"]))
    finally:
        store.close()


def test_teardown_removes_recorded_pre_fix_vnet_name_verbatim(tmp_path):
    """Migration safety pin (2026-07-27 `vnet_id_for_net` plan-scoping fix,
    CLAUDE.md task narrative): `destroy_deploy` deletes vnets from the
    RECORDED `create-vnet` op output (`out["vnet_id"]`), never by
    recomputing the name from `vnet_id_for_net`. A deploy created BEFORE the
    fix has an old-style vnet id baked into its state (hashed on `net_id`
    ALONE, no plan discriminator) -- that exact stale name must still be the
    one torn down, proving the fix does not strand/orphan an already-
    deployed twin's vnet just because the naming formula changed underneath
    it."""
    plan_id = "plan-migrated-pre-fix"
    net_id = "corp"
    # The pre-fix formula: `hashlib.sha256(net_id.encode()).hexdigest()[:5]`,
    # with NO plan_id folded in (see naming.py's vnet_id_for_net docstring).
    old_style_vnet = "tev" + hashlib.sha256(net_id.encode()).hexdigest()[:5]

    # Sanity: the OLD-style name differs from what the FIXED producer computes
    # for this exact (plan_id, net_id) pair -- otherwise this test would prove
    # nothing about the migration hazard.
    assert old_style_vnet != vnet_id_for_net(net_id, plan_id=plan_id)

    db = str(tmp_path / "s.db")
    deploy_id = "dep-pre-fix"
    store = StateStore(db)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
        scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
        placement={"h-a": "pve1"},
    )
    store.insert_ops(deploy_id, [
        _op(deploy_id, "op1", "create-vnet", {"net": net_id},
            {"vnet_id": old_style_vnet}),
    ])
    store.close()

    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert result.destroyed is True, result.errors
        assert ("vnet", old_style_vnet) in backend.calls
    finally:
        store.close()


def test_idempotent_rerun(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    try:
        destroy_deploy(store, _RecordTeardown(), info["deploy_id"])
        # Re-run against an 'everything absent' backend → still a clean sweep.
        result2 = destroy_deploy(store, _RecordTeardown(status="absent"), info["deploy_id"])
        assert result2.destroyed is True
        assert result2.errors == []
        assert all(a.status == "absent" for a in result2.actions)
    finally:
        store.close()


def test_lease_released_after_destroy(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    try:
        store.acquire_lease(info["scenario_id"], info["deploy_id"])
        assert store.get_lease(info["scenario_id"]) is not None
        destroy_deploy(store, _RecordTeardown(), info["deploy_id"])
        # The orchestrator releases the lease (and any resource locks) at the end.
        assert store.get_lease(info["scenario_id"]) is None
    finally:
        store.close()


def test_guard_rejects_foreign_vmid(tmp_path):
    """A vmid outside the te-tier range (100000-999999) is refused, never
    touched — the secondary `img`-template exclusion guard."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db, vmid=9000)  # 9000 == typical template range, NOT te
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])
        assert ("vm", "9000") not in backend.calls
        assert any(a.kind == "vm" and a.status == "guard-violation"
                   for a in result.actions)
        # A guard violation blocks the clean sweep — deploy stays un-destroyed.
        assert result.destroyed is False
        assert store.get_deploy(info["deploy_id"]).status.value != "destroyed"
    finally:
        store.close()


def test_protect_guard_refuses_o2_named_vm(tmp_path):
    """S1 (code review): the protect guard must also cover the VM path, not
    just gretap/vnet/zone/bridge. A vmid that otherwise falls inside the
    te-tier range (100000-999999) is still refused if the PVE-resolved
    display name is shaped like a standing `o2_*`/`img_*` resource — the
    Model-C name assertion this path previously lacked
    (docs/WORK_ORDER_resource_tags.md §3). The mirror-snippet step (keyed by
    the same vmid) must also be skipped, not just the VM destroy itself."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)  # default vmid is a valid te-range vmid
    store = StateStore(db)
    backend = _RecordTeardown(vm_names={info["vmid"]: "o2-sensor-vm"})
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])
        assert ("vm", str(info["vmid"])) not in backend.calls
        assert ("mirror", str(info["vmid"])) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "o2-sensor-vm"
        assert "protected" in (viol[0].detail or "")
        # A guard violation blocks the clean sweep — deploy stays un-destroyed.
        assert result.destroyed is False
        assert store.get_deploy(info["deploy_id"]).status.value != "destroyed"
    finally:
        store.close()


def test_o2_tier_destroy_refuses_a_te_named_vm(tmp_path):
    """Code-review N1: the symmetric positive gate on the VM step. An
    `o2`-tiered destroy must refuse a VM whose PVE-resolved name is
    `te`-shaped (not `o2`-shaped) — the reverse direction from
    `test_protect_guard_refuses_o2_named_vm` above. Unreachable in practice
    (an `o2` plan only ever records `o2-*` names) but the guard shape must
    stay symmetric with the vnet/zone/gretap steps, which already had this."""
    db = str(tmp_path / "s.db")
    deploy_id = "dep-o2-1"
    store = StateStore(db)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="o2-surface", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-o2", ir_hash="hash",
        placement={"o2-sensor": "pve1"}, tier="o2",
    )
    vmid = vmid_for_op("plan-o2", "clone-o2-sensor")
    store.insert_ops(deploy_id, [
        _op(deploy_id, "op1", "clone-vm", {"host": "o2-sensor"}, {"vmid": vmid}),
    ])
    store.close()

    store = StateStore(db)
    # Foreign-shaped name: `te-*`, not `o2-*` — this deploy's OWN tier is o2.
    backend = _RecordTeardown(vm_names={vmid: "te-abc123-o2-sensor"})
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert ("vm", str(vmid)) not in backend.calls
        assert ("mirror", str(vmid)) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "te-abc123-o2-sensor"
        assert result.destroyed is False
    finally:
        store.close()


def _seed_single_op(db_path, deploy_id, op_name, outputs, *, scope=None):
    """Minimal single-op deploy, for exercising one resource kind's guard in
    isolation (docs/WORK_ORDER_resource_tags.md §3 validation item (b))."""
    store = StateStore(db_path)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-guard", ir_hash="hash",
        placement={"h-a": "pve1"},
    )
    store.insert_ops(
        deploy_id, [_op(deploy_id, "op1", op_name, scope or {}, outputs)]
    )
    store.close()


def test_protect_guard_refuses_o2_named_vnet(tmp_path):
    """Hard protect guard (docs/WORK_ORDER_resource_tags.md §3): a resource
    whose recorded name is shaped like a standing `o2_*` surface component
    must be refused outright — defense-in-depth in case a te/tap positive
    shape-match ever had a bug that let one through."""
    db = str(tmp_path / "s.db")
    _seed_single_op(db, "dep-o2-vnet", "create-vnet", {"vnet_id": "o2v12345"})
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, "dep-o2-vnet")
        assert ("vnet", "o2v12345") not in backend.calls
        viol = [a for a in result.actions if a.kind == "vnet" and a.status == "guard-violation"]
        assert viol and "protected" in (viol[0].detail or "")
        assert result.destroyed is False
    finally:
        store.close()


def test_protect_guard_refuses_img_named_zone(tmp_path):
    """Same protect guard, exercised on the zone step with an `img_*`-shaped
    name."""
    db = str(tmp_path / "s.db")
    _seed_single_op(db, "dep-img-zone", "create-sdn-zone", {"zone_id": "img-golden-template"})
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, "dep-img-zone")
        assert ("zone", "img-golden-template") not in backend.calls
        viol = [a for a in result.actions if a.kind == "zone" and a.status == "guard-violation"]
        assert viol and "protected" in (viol[0].detail or "")
        assert result.destroyed is False
    finally:
        store.close()


def test_protect_guard_refuses_o2_named_gretap(tmp_path):
    db = str(tmp_path / "s.db")
    _seed_single_op(
        db, "dep-o2-gretap", "attach-gre-tunnel", {"gre_port": "o2-sensor"},
    )
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, "dep-o2-gretap")
        assert ("gretap", "o2-sensor") not in backend.calls
        viol = [a for a in result.actions if a.kind == "gretap" and a.status == "guard-violation"]
        assert viol and "protected" in (viol[0].detail or "")
        assert result.destroyed is False
    finally:
        store.close()


def test_protect_guard_refuses_protected_external_vm_at_te_tier(tmp_path):
    """A hand-created operator-managed sink node (`XPress-SW`, the
    packetXpress box) must never be destroyed by a `te`-tier destroy, even if
    it somehow ended up recorded against this deploy's ops -- defense in
    depth (in normal operation it's never in a te's created-resource lists).
    A guard-violation is recorded and the clean sweep is blocked."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)  # default vmid is a valid te-range vmid
    store = StateStore(db)
    backend = _RecordTeardown(vm_names={info["vmid"]: "XPress-SW"})
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])
        assert ("vm", str(info["vmid"])) not in backend.calls
        assert ("mirror", str(info["vmid"])) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "XPress-SW"
        assert "protected external" in (viol[0].detail or "")
        assert result.destroyed is False
        assert store.get_deploy(info["deploy_id"]).status.value != "destroyed"
    finally:
        store.close()


def test_protect_guard_refuses_protected_external_vm_at_o2_tier(tmp_path):
    """The key distinction from `is_o2`: an `o2`-tier destroy IS allowed to
    sweep its own `o2_*` resources, but must STILL refuse `XPress-SW` -- a
    sink node outlives both the te substrate it captures and the o2 surface
    it feeds. Unconditional, like `is_img`, and NOT gated on `own_tier`."""
    db = str(tmp_path / "s.db")
    deploy_id = "dep-o2-xpress"
    store = StateStore(db)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="o2-surface", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-o2", ir_hash="hash",
        placement={"o2-sensor": "pve1"}, tier="o2",
    )
    vmid = vmid_for_op("plan-o2", "clone-o2-sensor")
    store.insert_ops(deploy_id, [
        _op(deploy_id, "op1", "clone-vm", {"host": "o2-sensor"}, {"vmid": vmid}),
    ])
    store.close()

    store = StateStore(db)
    backend = _RecordTeardown(vm_names={vmid: "XPress-SW"})
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert ("vm", str(vmid)) not in backend.calls
        assert ("mirror", str(vmid)) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "XPress-SW"
        assert "protected external" in (viol[0].detail or "")
        assert result.destroyed is False
    finally:
        store.close()


def test_protect_guard_refuses_operator_configured_extra_name(tmp_path):
    """An operator-configured extra name (`adapter.conf`
    PROTECTED_EXTERNAL_NAMES, simulated here via `protected_names=`) is
    refused exactly like the built-in default -- LES-069, thread the extra
    allowlist through every teardown call site consistently."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown(vm_names={info["vmid"]: "foo-sink"})
    try:
        result = destroy_deploy(
            store, backend, info["deploy_id"], protected_names=["foo-sink"],
        )
        assert ("vm", str(info["vmid"])) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "foo-sink"
        assert result.destroyed is False
    finally:
        store.close()


def test_protect_guard_refuses_protected_vmid_even_when_name_resolution_fails(tmp_path):
    """Review hardening (the fail-OPEN gap): `backend.vm_name()` returning
    `None` (a name-resolution flake/absence) previously fell straight
    through the name guard to the ordinary vmid-range check, which a
    protected vmid inside the te-tier range would pass -- destroying it.
    `protected_vmids` is checked FIRST, before any name resolution is even
    attempted, so it is resolution-independent: `destroy_vm` must never be
    called for a protected vmid no matter what the name lookup returns."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)  # default vmid is a valid te-range vmid
    store = StateStore(db)
    backend = _RecordTeardown(vm_names={})  # vm_name(...) resolves to None
    try:
        result = destroy_deploy(
            store, backend, info["deploy_id"],
            protected_vmids=[info["vmid"]],
        )
        assert ("vm", str(info["vmid"])) not in backend.calls
        assert ("mirror", str(info["vmid"])) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == str(info["vmid"])
        assert "protected external" in (viol[0].detail or "")
        assert result.destroyed is False
        assert store.get_deploy(info["deploy_id"]).status.value != "destroyed"
    finally:
        store.close()


def test_bridge_guard_never_touches_vmbr0(tmp_path):
    """The blast-radius guard: a recorded bridge_name that isn't THIS plan's
    deterministic underlay (e.g. `vmbr0`) is never deleted from the node net."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db, bridge="vmbr0")
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])
        assert ("ovs-bridge", "vmbr0") not in backend.calls
        assert all(t != "vmbr0" for _, t in backend.calls)
        # vmbr0 is reported skipped (absent), not an error → the rest can sweep.
        assert any(a.kind == "ovs-bridge" and a.target == "vmbr0"
                   and a.status == "absent" for a in result.actions)
    finally:
        store.close()


def test_error_continues_without_clean_sweep(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown(fail_on="vnet")
    try:
        result = destroy_deploy(store, backend, info["deploy_id"])
        # The vnet error is recorded...
        assert any(a.kind == "vnet" and a.status == "error" for a in result.actions)
        # ...but teardown CONTINUES past it (zone + bridge still attempted).
        assert ("zone", info["zone"]) in backend.calls
        assert ("ovs-bridge", info["bridge"]) in backend.calls
        # Not a clean sweep → deploy keeps its prior status for a resume.
        assert result.destroyed is False
        assert store.get_deploy(info["deploy_id"]).status.value != "destroyed"
    finally:
        store.close()


def test_destroy_of_second_deploy_sweeps_its_own_resources(tmp_path):
    """CRITICAL regression (code-review finding #1, 2026-07-07): two deploys
    of the SAME plan (identical deterministic op_ids/resource names — the
    `--fresh` redeploy shape) must destroy independently. Destroying the
    SECOND deploy must sweep ITS OWN op rows and transition ITS OWN deploy
    row without touching the first deploy's already-`destroyed` state (the
    pre-fix bug: `ops_for_deploy` on a deploy whose rows aliased onto the
    prior deploy would return nothing, so `axrange destroy` swept zero
    resources yet still reported DESTROYED)."""
    db = str(tmp_path / "s.db")
    info_a = seed_deploy(db, deploy_id="dep-a", plan_id="plan-shared")
    info_b = seed_deploy(db, deploy_id="dep-b", plan_id="plan-shared")
    # Same plan_id => identical deterministic resource identities (this is
    # exactly what a `--fresh` redeploy against an uncleared state DB looks
    # like: same op_ids, same vmid/bridge/vnet/zone/gretap names).
    assert info_a["vmid"] == info_b["vmid"]
    assert info_a["bridge"] == info_b["bridge"]

    store = StateStore(db)
    try:
        result_a = destroy_deploy(store, _RecordTeardown(), "dep-a")
        assert result_a.destroyed is True

        # Deploy B must be fully intact — untouched by A's destroy despite
        # sharing every op_id string and every resource identity.
        ops_b = store.ops_for_deploy("dep-b")
        assert len(ops_b) == 6
        assert all(op.status.value == "completed" for op in ops_b)
        assert store.get_deploy("dep-b").status.value != "destroyed"

        backend_b = _RecordTeardown()
        result_b = destroy_deploy(store, backend_b, "dep-b")
        assert result_b.destroyed is True
        # B actually swept its own resources — not a zero-op no-op.
        assert len(backend_b.calls) >= 6

        assert store.get_deploy("dep-a").status.value == "destroyed"
        assert store.get_deploy("dep-b").status.value == "destroyed"
    finally:
        store.close()


def test_resource_locks_released_only_on_clean_sweep(tmp_path):
    """Resource locks (`ovs:<node>` etc — shared across DIFFERENT scenarios'
    deploys, PLANNER §8.4) must not be released while the resource they
    protect might still exist (code-review finding #7, 2026-07-07): a
    concurrent unrelated deploy could then start touching that same node
    before this teardown's retry finishes clearing it."""
    db = str(tmp_path / "s.db")
    info = seed_deploy(db)
    store = StateStore(db)
    try:
        store.acquire_resource_locks(["vm:999"], info["deploy_id"], "op1")

        # A partial (errored) teardown must NOT release the lock.
        result = destroy_deploy(store, _RecordTeardown(fail_on="vnet"), info["deploy_id"])
        assert result.destroyed is False
        assert store.held_resource_locks(info["deploy_id"]) != []

        # A subsequent clean sweep DOES release it.
        result2 = destroy_deploy(store, _RecordTeardown(), info["deploy_id"])
        assert result2.destroyed is True
        assert store.held_resource_locks(info["deploy_id"]) == []
    finally:
        store.close()


def test_unknown_deploy_raises(tmp_path):
    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        import pytest

        with pytest.raises(TeardownError):
            destroy_deploy(store, _RecordTeardown(), "does-not-exist")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Six-tier cascade/protect matrix (docs/design/02-twin-model.md,
# docs/WORK_ORDER_tier_split.md — the `tfc`/`exr` tier-split keystone).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


def test_tfc_and_exr_tiers_sweep_their_own_resources(tmp_path):
    """A `tfc`/`exr`-tiered destroy touches its OWN tier's resources exactly
    like `te`/`o2` already do — the positive `_shape_checks_for_tier` half of
    LES-088's fail-closed pair."""
    for tier in ("tfc", "exr"):
        db = str(tmp_path / f"s-{tier}.db")
        deploy_id = f"dep-{tier}"
        plan_id = f"plan-{tier}"
        store = StateStore(db)
        store.create_deploy(
            deploy_id=deploy_id, scenario_id=f"sc-{tier}", scenario_version=1,
            scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
            placement={"h-a": "pve1"}, tier=tier,
        )
        vmid = vmid_for_op(plan_id, "clone-h-a")
        vm_name = vm_name_for_host(plan_id, "h-a", tier=tier)
        bridge = underlay_bridge_for_plan(plan_id, tier=tier)
        zone = zone_id_for_plan(plan_id, tier=tier)
        vnet = vnet_id_for_net("corp", plan_id=plan_id, tier=tier)
        store.insert_ops(deploy_id, [
            _op(deploy_id, "op1", "create-ovs-bridge", {"net": "corp"}, {"bridge_name": bridge}),
            _op(deploy_id, "op2", "create-sdn-zone", {}, {"zone_id": zone}),
            _op(deploy_id, "op3", "create-vnet", {"net": "corp"}, {"vnet_id": vnet}),
            _op(deploy_id, "op4", "clone-vm", {"host": "h-a"}, {"vmid": vmid}),
        ])
        store.close()

        store = StateStore(db)
        backend = _RecordTeardown(vm_names={vmid: vm_name})
        try:
            result = destroy_deploy(store, backend, deploy_id)
            assert result.destroyed is True, f"{tier}: {result.errors}"
            assert ("vm", str(vmid)) in backend.calls
            assert ("vnet", vnet) in backend.calls
            assert ("zone", zone) in backend.calls
            assert ("ovs-bridge", bridge) in backend.calls
        finally:
            store.close()


@pytest.mark.parametrize("own_tier", ["te", "o2", "tfc", "exr"])
def test_every_tier_refuses_every_foreign_tiers_vm(own_tier, tmp_path):
    """The core §2.1 claim: an `own_tier` destroy structurally cannot touch
    ANY other tier's VM, across all six tiers (minus `img`/protected-external,
    covered elsewhere) — the reverse-direction generalization of
    `test_o2_tier_destroy_refuses_a_te_named_vm`. `te`'s one documented
    exception is `tap` (the cascade), so it is excluded from the foreign set
    for `own_tier == "te"`."""
    all_tiers = {"te", "o2", "tfc", "exr"}
    foreign_tiers = all_tiers - {own_tier}
    plan_id = f"plan-{own_tier}"
    for foreign_tier in foreign_tiers:
        db = str(tmp_path / f"s-{own_tier}-vs-{foreign_tier}.db")
        deploy_id = "dep-x"
        store = StateStore(db)
        store.create_deploy(
            deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
            placement={"h-a": "pve1"}, tier=own_tier,
        )
        vmid = vmid_for_op(plan_id, "clone-h-a")
        foreign_name = vm_name_for_host(f"plan-{foreign_tier}", "h-a", tier=foreign_tier)
        store.insert_ops(deploy_id, [
            _op(deploy_id, "op1", "clone-vm", {"host": "h-a"}, {"vmid": vmid}),
        ])
        store.close()

        store = StateStore(db)
        backend = _RecordTeardown(vm_names={vmid: foreign_name})
        try:
            result = destroy_deploy(store, backend, deploy_id)
            assert ("vm", str(vmid)) not in backend.calls, (
                f"{own_tier} destroy touched a {foreign_tier}-named VM"
            )
            viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
            assert viol and viol[0].target == foreign_name
            assert result.destroyed is False
        finally:
            store.close()


def test_te_tier_destroy_tolerates_tap_named_resources():
    """The one documented cascade exception (LES-058): a `te` deploy's own
    ops table may (defense-in-depth, though it no longer happens in
    practice post-sensor-agnostic-te) carry `tap_*`-shaped resource names
    without `_protected()` refusing them, unlike every other foreign tier."""
    from axrange.planner.teardown import _CASCADE_ALLOWED_FOREIGN

    assert _CASCADE_ALLOWED_FOREIGN.get("te") == frozenset({"tap"})
    assert _CASCADE_ALLOWED_FOREIGN.get("o2", frozenset()) == frozenset()
    assert _CASCADE_ALLOWED_FOREIGN.get("tfc", frozenset()) == frozenset()
    assert _CASCADE_ALLOWED_FOREIGN.get("exr", frozenset()) == frozenset()


# ---------------------------------------------------------------------------
# Exhaustive (own_tier, foreign resource kind) matrix — not just the VM path.
# `_protected()` is one function shared by every resource-kind loop, but the
# validation bar (WORK_ORDER_tier_split.md §4) asks this be PROVEN, not
# inferred from reading the code once.
# ---------------------------------------------------------------------------
import pytest as _pytest  # noqa: E402
from axrange.adapter_proxmox.naming import (  # noqa: E402
    gre_port_for_bridge_sensor as _gps,
)

_ALL_TIERS = ("te", "o2", "tfc", "exr")


def _seed_single_op_tiered(db_path, deploy_id, own_tier, op_name, outputs, *, scope=None):
    store = StateStore(db_path)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
        scenario_schema_version=1, plan_id=f"plan-{deploy_id}", ir_hash="hash",
        placement={"h-a": "pve1"}, tier=own_tier,
    )
    store.insert_ops(
        deploy_id, [_op(deploy_id, "op1", op_name, scope or {}, outputs)]
    )
    store.close()


@_pytest.mark.parametrize("own_tier", _ALL_TIERS)
@_pytest.mark.parametrize("foreign_tier", _ALL_TIERS)
@_pytest.mark.parametrize(
    "kind,op_name,out_key,producer",
    [
        ("vnet", "create-vnet", "vnet_id",
         lambda tier: vnet_id_for_net("corp", plan_id="plan-x", tier=tier)),
        ("zone", "create-sdn-zone", "zone_id",
         lambda tier: zone_id_for_plan("plan-x", tier=tier)),
        ("ovs-bridge", "create-ovs-bridge", "bridge_name",
         lambda tier: underlay_bridge_for_plan("plan-x", tier=tier)),
    ],
)
def test_every_tier_refuses_every_foreign_tiers_non_vm_resources(
    own_tier, foreign_tier, kind, op_name, out_key, producer, tmp_path
):
    """Generalizes the VM-only matrix to vnet/zone/ovs-bridge: an `own_tier`
    destroy must never sweep a FOREIGN tier's vnet/zone/underlay-bridge,
    across all 4x4 (own, foreign) pairs where own != foreign."""
    if own_tier == foreign_tier:
        _pytest.skip("same-tier is the positive case, covered elsewhere")
    db = str(tmp_path / f"m-{own_tier}-{foreign_tier}-{kind}.db")
    deploy_id = "dep-x"
    foreign_name = producer(foreign_tier)
    _seed_single_op_tiered(
        db, deploy_id, own_tier, op_name, {out_key: foreign_name},
        scope={"net": "corp"} if kind == "vnet" else {},
    )
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert (kind, foreign_name) not in backend.calls, (
            f"{own_tier} destroy touched a {foreign_tier}-named {kind}"
        )
        viol = [a for a in result.actions if a.kind == kind and a.status == "guard-violation"]
        assert viol and viol[0].target == foreign_name, (
            f"{own_tier} destroy did not record a guard-violation for the "
            f"{foreign_tier}-named {kind} {foreign_name!r}: {result.actions}"
        )
        assert result.destroyed is False
    finally:
        store.close()


@_pytest.mark.parametrize("own_tier", _ALL_TIERS)
def test_te_cascade_to_tap_gretap_is_the_only_exception(own_tier, tmp_path):
    """Positive assertion of the ONE allowed cascade
    (`docs/WORK_ORDER_tier_split.md` / brief §2.1): a `tap_*`-shaped gretap
    is swept by a `te`-tier destroy (the cascade) but refused by every OTHER
    tier's destroy (`o2`/`tfc`/`exr` — none of which are allowed to cascade
    into `tap`). Guards a future refactor from silently widening or dropping
    this exact exception."""
    db = str(tmp_path / f"cascade-{own_tier}.db")
    deploy_id = "dep-cascade"
    tap_dev = _gps("some-bridge", "sensor1")
    _seed_single_op_tiered(
        db, deploy_id, own_tier, "attach-gre-tunnel", {"gre_port": tap_dev},
    )
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, deploy_id)
        if own_tier == "te":
            assert ("gretap", tap_dev) in backend.calls
            assert not [a for a in result.actions if a.status == "guard-violation"]
            assert result.destroyed is True
        else:
            assert ("gretap", tap_dev) not in backend.calls
            viol = [a for a in result.actions if a.kind == "gretap" and a.status == "guard-violation"]
            assert viol and viol[0].target == tap_dev
            assert result.destroyed is False
    finally:
        store.close()


@_pytest.mark.parametrize("own_tier", _ALL_TIERS)
@_pytest.mark.parametrize(
    "kind,op_name,out_key,scope",
    [
        ("vnet", "create-vnet", "vnet_id", {"net": "corp"}),
        ("zone", "create-sdn-zone", "zone_id", {}),
        ("ovs-bridge", "create-ovs-bridge", "bridge_name", {}),
        ("gretap", "attach-gre-tunnel", "gre_port", {}),
    ],
)
def test_img_refused_by_every_tier_for_every_resource_kind(
    own_tier, kind, op_name, out_key, scope, tmp_path
):
    """`img_*` (golden templates) is refused UNCONDITIONALLY — even by a
    tier's OWN destroy — across every resource kind the teardown walks, not
    just the zone-kind spot-check the pre-tier-split suite had."""
    db = str(tmp_path / f"img-{own_tier}-{kind}.db")
    deploy_id = "dep-img"
    img_name = img_vm_name_for_image("debian-12-cloud")
    _seed_single_op_tiered(db, deploy_id, own_tier, op_name, {out_key: img_name}, scope=scope)
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert (kind, img_name) not in backend.calls
        viol = [a for a in result.actions if a.kind == kind and a.status == "guard-violation"]
        assert viol and viol[0].target == img_name
        assert result.destroyed is False
    finally:
        store.close()


@_pytest.mark.parametrize("own_tier", _ALL_TIERS)
@_pytest.mark.parametrize(
    "kind,op_name,out_key,scope",
    [
        ("vnet", "create-vnet", "vnet_id", {"net": "corp"}),
        ("zone", "create-sdn-zone", "zone_id", {}),
        ("ovs-bridge", "create-ovs-bridge", "bridge_name", {}),
        ("gretap", "attach-gre-tunnel", "gre_port", {}),
    ],
)
def test_protected_external_refused_by_every_tier_for_every_resource_kind(
    own_tier, kind, op_name, out_key, scope, tmp_path
):
    """`XPress-SW` (protected-external) is refused UNCONDITIONALLY, same
    posture as `img`, across every resource kind and every tier."""
    db = str(tmp_path / f"ext-{own_tier}-{kind}.db")
    deploy_id = "dep-ext"
    _seed_single_op_tiered(db, deploy_id, own_tier, op_name, {out_key: "XPress-SW"}, scope=scope)
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert (kind, "XPress-SW") not in backend.calls
        viol = [a for a in result.actions if a.kind == kind and a.status == "guard-violation"]
        assert viol and viol[0].target == "XPress-SW"
        assert result.destroyed is False
    finally:
        store.close()


@_pytest.mark.parametrize("own_tier", _ALL_TIERS)
@_pytest.mark.parametrize("protected_name,detail_fragment", [
    (img_vm_name_for_image("debian-12-cloud"), "protected img_"),
    ("XPress-SW", "protected external"),
])
def test_img_and_protected_external_refused_on_vm_path_for_every_tier(
    own_tier, protected_name, detail_fragment, tmp_path
):
    """Generalizes `test_protect_guard_refuses_protected_external_vm_at_te_tier`
    / `..._at_o2_tier` (pre-tier-split) to `tfc`/`exr` too: the VM path's
    protect guard refuses an `img_*`-named or protected-external-named VM
    regardless of which of the four tiers is doing its OWN destroy."""
    db = str(tmp_path / f"vm-{own_tier}-{protected_name[:4]}.db")
    deploy_id = "dep-vm-protect"
    plan_id = f"plan-{own_tier}"
    store = StateStore(db)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
        scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
        placement={"h-a": "pve1"}, tier=own_tier,
    )
    vmid = vmid_for_op(plan_id, "clone-h-a")
    store.insert_ops(deploy_id, [
        _op(deploy_id, "op1", "clone-vm", {"host": "h-a"}, {"vmid": vmid}),
    ])
    store.close()

    store = StateStore(db)
    backend = _RecordTeardown(vm_names={vmid: protected_name})
    try:
        result = destroy_deploy(store, backend, deploy_id)
        assert ("vm", str(vmid)) not in backend.calls
        assert ("mirror", str(vmid)) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == protected_name
        assert detail_fragment in (viol[0].detail or "")
        assert result.destroyed is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Unrecognized-tier fail-closed regression (review MUST-FIX #4, second round):
# `StateStore.create_deploy` now validates `tier=` via `naming.check_tier`
# (docs/WORK_ORDER_tier_split.md MUST-FIX #13), which closes the WRITE
# boundary -- so provoking an unrecognized `deploys.tier` value requires
# seeding the row with raw SQL, bypassing `create_deploy` entirely (exactly
# what a stale/hand-edited/future-schema row would look like).
# ---------------------------------------------------------------------------
def _seed_deploy_row_raw(db_path, *, deploy_id, tier, plan_id="plan-raw"):
    """Insert a `deploys` row directly via SQL, bypassing `create_deploy`'s
    `check_tier` guard -- the only way to get an unrecognized `tier` into
    `state.db` at all now that the write path validates it."""
    import time as _time

    store = StateStore(db_path)
    now = _time.time()
    store._conn.execute(
        """
        INSERT INTO deploys (
            deploy_id, scenario_id, scenario_version, scenario_schema_version,
            plan_id, ir_hash, status, placement_json, started_at, updated_at,
            completed_at, tier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (deploy_id, "sc", 1, 1, plan_id, "hash", "running", "{}", now, now, None, tier),
    )
    store.close()


def test_destroy_deploy_refuses_unrecognized_tier_without_crashing(tmp_path):
    """The crash this review round found end-to-end: a raw-SQL-seeded
    `tier="tap"` row (NOT a real `_VALID_TIERS` deploy tier -- and
    `docs/WORK_ORDER_tap_points.md` deliberately keeps it that way, `tap`
    stays a resource name-shape tag, not a deploy tier, see `axrange.
    o2control.taps`'s module docstring for the reasoning) must be refused
    with the module's OWN typed `TeardownError`,
    BEFORE a single resource is touched and BEFORE `store.release_lease`
    would ever run -- not a bare `ValueError` escaping mid-sweep from
    `underlay_bridge_for_plan` after VMs/gretaps are already destroyed,
    which would leave a held lease blocking the very retry that could fix
    it. Also asserts the lease is untouched (never acquired here, so still
    absent) and the deploy's own status is untouched (never transitioned)."""
    import pytest

    db = str(tmp_path / "s.db")
    deploy_id = "dep-bad-tier"
    _seed_deploy_row_raw(db, deploy_id=deploy_id, tier="tap")

    store = StateStore(db)
    try:
        store.acquire_lease("sc", deploy_id)
        with pytest.raises(TeardownError, match="unrecognized tier"):
            destroy_deploy(store, _RecordTeardown(), deploy_id)
        # Refused before anything ran -- the deploy is still `running`, and
        # (critically) the lease acquired above is STILL HELD, since
        # `destroy_deploy` never reached `store.release_lease` at the end.
        # This is the exact "leaked lease blocks the retry" failure mode the
        # top-of-function validation prevents by never starting the sweep.
        assert store.get_deploy(deploy_id).status.value == "running"
        assert store.get_lease("sc") is not None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# `destroy_ops_scoped` — the `--update` removal-sweep half
# (`docs/WORK_ORDER_twin_incremental_update.md` §3.2/§4.2). Reuses `_sweep`
# (LES-037), so the name-guard/protect-guard coverage above already proves
# those apply here too; these tests focus on what's DIFFERENT: scoping,
# shared-resource safety, and the no-lease/no-status-transition contract.
# ---------------------------------------------------------------------------
def seed_two_host_deploy(db_path, *, deploy_id="dep-2h", scenario_id="sc2",
                         plan_id="plan-2h", node="pve1"):
    """Two hosts (h-a, h-b) sharing one vnet/zone/bridge -- the shared-
    resource shape work order §3.2 depends on: removing h-b's clone-vm op
    must NOT also sweep the vnet/zone/bridge h-a still needs."""
    store = StateStore(db_path)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=scenario_id, scenario_version=1,
        scenario_schema_version=1, plan_id=plan_id, ir_hash="hash",
        placement={"h-a": node, "h-b": node},
    )
    bridge = underlay_bridge_for_plan(plan_id)
    zone = zone_id_for_plan(plan_id)
    vnet = vnet_id_for_net("corp", plan_id=plan_id)
    vmid_a = vmid_for_op(plan_id, "clone-h-a")
    vmid_b = vmid_for_op(plan_id, "clone-h-b")
    ops = [
        _op(deploy_id, "op-bridge", "create-ovs-bridge", {"scenario": scenario_id},
            {"bridge_name": bridge}),
        _op(deploy_id, "op-zone", "create-sdn-zone", {"scenario": scenario_id},
            {"zone_id": zone}),
        _op(deploy_id, "op-vnet", "create-vnet", {"net": "corp"}, {"vnet_id": vnet}),
        _op(deploy_id, "op-clone-a", "clone-vm", {"host": "h-a"}, {"vmid": vmid_a}),
        _op(deploy_id, "op-clone-b", "clone-vm", {"host": "h-b"}, {"vmid": vmid_b}),
    ]
    store.insert_ops(deploy_id, ops)
    store.close()
    return {
        "deploy_id": deploy_id, "scenario_id": scenario_id, "plan_id": plan_id,
        "node": node, "bridge": bridge, "zone": zone, "vnet": vnet,
        "vmid_a": vmid_a, "vmid_b": vmid_b,
    }


def test_destroy_ops_scoped_sweeps_only_the_removed_op_ids(tmp_path):
    """Removing h-b (op-clone-b) must destroy ONLY h-b's VM -- h-a's VM and
    the SHARED vnet/zone/bridge (whose OWN ops are not in the removed set)
    are never touched, proving §3.2's "no refcounting needed" claim."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown()
    try:
        result = destroy_ops_scoped(store, backend, info["deploy_id"], ["op-clone-b"])

        assert ("vm", str(info["vmid_b"])) in backend.calls
        assert ("mirror", str(info["vmid_b"])) in backend.calls
        assert ("vm", str(info["vmid_a"])) not in backend.calls
        assert not any(k in ("vnet", "zone", "ovs-bridge", "sdn-apply") for k, _ in backend.calls)
        assert result.errors == []
    finally:
        store.close()


def test_destroy_ops_scoped_marks_only_the_removed_ops_destroyed(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    try:
        destroy_ops_scoped(store, _RecordTeardown(), info["deploy_id"], ["op-clone-b"])

        assert store.get_op(info["deploy_id"], "op-clone-b").status.value == "destroyed"
        # Everything NOT in the removed set keeps its prior status.
        assert store.get_op(info["deploy_id"], "op-clone-a").status.value == "completed"
        assert store.get_op(info["deploy_id"], "op-vnet").status.value == "completed"
        assert store.get_op(info["deploy_id"], "op-zone").status.value == "completed"
        assert store.get_op(info["deploy_id"], "op-bridge").status.value == "completed"
    finally:
        store.close()


def test_destroy_ops_scoped_does_not_transition_deploy_status(tmp_path):
    """Unlike `destroy_deploy`, the DEPLOY as a whole is still standing --
    only some of its resources went away. `create_deploy` starts a fresh row
    at `running` (the state a live `--update` finds it in mid-run); a scoped
    sweep must leave that untouched regardless of a clean/unclean outcome."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    try:
        assert store.get_deploy(info["deploy_id"]).status.value == "running"
        result = destroy_ops_scoped(store, _RecordTeardown(), info["deploy_id"], ["op-clone-b"])
        assert result.destroyed is False  # never set by the scoped variant
        assert store.get_deploy(info["deploy_id"]).status.value == "running"
    finally:
        store.close()


def test_destroy_ops_scoped_does_not_release_lease_or_locks(tmp_path):
    """The `--update` caller holds the lease/heartbeat for the WHOLE run
    (added-ops execution AND this removal sweep, work order §4.4) -- the
    scoped sweep must never release it out from under that caller."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    try:
        store.acquire_lease(info["scenario_id"], info["deploy_id"])
        destroy_ops_scoped(store, _RecordTeardown(), info["deploy_id"], ["op-clone-b"])
        assert store.get_lease(info["scenario_id"]) is not None
    finally:
        store.close()


def test_destroy_ops_scoped_partial_failure_leaves_ops_unmarked(tmp_path):
    """A resource that fails to clear must NOT be marked `destroyed` --
    a retried --update needs to see it as still-removed-but-not-yet-swept."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown(fail_on="vm")
    try:
        result = destroy_ops_scoped(store, backend, info["deploy_id"], ["op-clone-b"])
        assert result.errors != []
        assert store.get_op(info["deploy_id"], "op-clone-b").status.value == "completed"
    finally:
        store.close()


def test_destroy_ops_scoped_protect_guard_still_refuses_o2_named_vm(tmp_path):
    """§3.2: "An incremental update is not a licence to touch o2_/img_/
    external-sink resources." The SAME `_protected()` guard `destroy_deploy`
    uses applies unchanged inside the scoped sweep."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    backend = _RecordTeardown(vm_names={info["vmid_b"]: "o2-sensor-vm"})
    try:
        result = destroy_ops_scoped(store, backend, info["deploy_id"], ["op-clone-b"])
        assert ("vm", str(info["vmid_b"])) not in backend.calls
        viol = [a for a in result.actions if a.kind == "vm" and a.status == "guard-violation"]
        assert viol and viol[0].target == "o2-sensor-vm"
        # Refused -> not a clean sweep -> the op row must NOT be marked destroyed.
        assert store.get_op(info["deploy_id"], "op-clone-b").status.value == "completed"
    finally:
        store.close()


def test_destroy_ops_scoped_uses_the_placement_recorded_before_the_call(tmp_path):
    """A removed host's `vm_node` must resolve from the deploy row's
    CURRENT placement at call time -- callers must sweep BEFORE overwriting
    placement with the new (host-dropped) generation (`docs/
    WORK_ORDER_twin_incremental_update.md` §4.2's docstring note). Proven by
    contrast: sweeping with the FULL (pre-update) placement still on the row
    destroys h-b cleanly; sweeping with placement ALREADY narrowed to just
    h-a (simulating a caller that updated the generation too early) can't
    resolve h-b's node and records an error instead of destroying it."""
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db, deploy_id="dep-2h-early")
    store = StateStore(db)
    try:
        # Full placement still on the row (the correct call order).
        result_ok = destroy_ops_scoped(
            store, _RecordTeardown(), info["deploy_id"], ["op-clone-b"],
        )
        assert result_ok.errors == []
        assert not any(a.kind == "vm" and a.status == "error" for a in result_ok.actions)
    finally:
        store.close()

    db2 = str(tmp_path / "s2.db")
    info2 = seed_two_host_deploy(db2, deploy_id="dep-2h-narrowed")
    store2 = StateStore(db2)
    try:
        # Simulate a caller that (incorrectly) already wrote the NEW
        # (host-dropped) generation's placement BEFORE sweeping the removal
        # -- empty here since removing h-b's node ref leaves nothing in a
        # single-node cluster's placement map to fall back to either
        # (`primary_node`'s own fallback would otherwise paper over a
        # narrowing that still left SOME entry, since every host in the
        # slice-MVP shares one target node).
        store2.update_deploy_generation(info2["deploy_id"], ir_hash="h2", placement={})
        result_bad = destroy_ops_scoped(
            store2, _RecordTeardown(), info2["deploy_id"], ["op-clone-b"],
        )
        assert any(a.kind == "vm" and a.status == "error" for a in result_bad.actions)
    finally:
        store2.close()


def test_destroy_ops_scoped_empty_op_ids_is_a_clean_noop(tmp_path):
    db = str(tmp_path / "s.db")
    info = seed_two_host_deploy(db)
    store = StateStore(db)
    try:
        result = destroy_ops_scoped(store, _RecordTeardown(), info["deploy_id"], [])
        assert result.actions == []
        assert result.errors == []
    finally:
        store.close()


def test_destroy_ops_scoped_refuses_unrecognized_tier_before_touching_anything(tmp_path):
    db = str(tmp_path / "s.db")
    deploy_id = "dep-bad-tier-scoped"
    _seed_deploy_row_raw(db, deploy_id=deploy_id, tier="tap")
    store = StateStore(db)
    try:
        with pytest.raises(TeardownError, match="unrecognized tier"):
            destroy_ops_scoped(store, _RecordTeardown(), deploy_id, ["whatever"])
    finally:
        store.close()


def test_sweepable_op_names_matches_the_sweep_chain(tmp_path):
    """S8 (`docs/WORK_ORDER_twin_incremental_update.md` code review):
    `teardown.SWEEPABLE_OP_NAMES` must name EXACTLY the op names `_sweep`'s
    resource-grouping if/elif chain acts on. Proven by running
    `destroy_ops_scoped` over one op of EVERY name in `SWEEPABLE_OP_NAMES`
    (asserting each produces a backend call) and separately over several
    representative NON-sweepable op names a real `te` plan emits (asserting
    NONE do) -- so the constant can't silently drift out of sync with the
    if/elif chain in either direction."""
    from axrange.planner.teardown import SWEEPABLE_OP_NAMES

    # REAL deterministic names (`adapter_proxmox/naming.py`), matching this
    # file's own header note ("op outputs carry REAL deterministic names ...
    # so the name-guards are exercised faithfully") -- a synthetic
    # placeholder like `"vnet-x"` fails the positive name-shape guard
    # (`_vnet_ok`/`_zone_ok`/`_gretap_ok`) and is recorded as a
    # guard-violation instead of ever reaching the backend, which would
    # make this test pass for the wrong reason (proving the guard exists,
    # not that the op-name dispatch does).
    plan_id = "plan-sweep-yes"
    bridge = underlay_bridge_for_plan(plan_id)
    zone = zone_id_for_plan(plan_id)
    vnet = vnet_id_for_net("corp", plan_id=plan_id)
    gretap = gre_port_for_bridge_sensor(bridge, "default")
    vmid = vmid_for_op(plan_id, "clone-h-a")
    sweepable_outputs = {
        "clone-vm": {"vmid": vmid},
        "attach-gre-tunnel": {"gre_port": gretap},
        "create-vnet": {"vnet_id": vnet},
        "create-sdn-zone": {"zone_id": zone},
        "create-ovs-bridge": {"bridge_name": bridge},
    }
    # This test covers every member of the constant, or it isn't proving
    # what it claims to.
    assert set(sweepable_outputs) == SWEEPABLE_OP_NAMES

    db = str(tmp_path / "s.db")
    deploy_id = "dep-sweep-yes"
    store = StateStore(db)
    store.create_deploy(
        deploy_id=deploy_id, scenario_id="sc", scenario_version=1,
        scenario_schema_version=1, plan_id=plan_id, ir_hash="h",
        placement={"h-a": "pve1"},
    )
    ops = [
        _op(deploy_id, f"op-{name}", name, {"host": "h-a"}, outputs)
        for name, outputs in sweepable_outputs.items()
    ]
    store.insert_ops(deploy_id, ops)
    backend = _RecordTeardown()
    try:
        destroy_ops_scoped(
            store, backend, deploy_id, [f"op-{n}" for n in sweepable_outputs],
        )
        kinds = {kind for kind, _target in backend.calls}
        assert kinds >= {"vm", "gretap", "vnet", "zone", "ovs-bridge"}
    finally:
        store.close()

    non_sweepable = {
        "configure-vm-hardware": {"configured": True},
        "attach-vm-to-vnet": {"attached": True},
        "start-vm": {"started": True},
        "provision-app": {"provisioned": True},
        "install-defense-agent": {"installed": True},
        "host-ready": {},  # a barrier -- also has no teardown counterpart
    }
    assert not (set(non_sweepable) & SWEEPABLE_OP_NAMES)  # sanity: disjoint

    db2 = str(tmp_path / "s2.db")
    deploy_id2 = "dep-sweep-no"
    store2 = StateStore(db2)
    store2.create_deploy(
        deploy_id=deploy_id2, scenario_id="sc2", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-sweep-no", ir_hash="h",
        placement={"h-a": "pve1"},
    )
    ops2 = [
        _op(deploy_id2, f"op-{name}", name, {"host": "h-a"}, outputs)
        for name, outputs in non_sweepable.items()
    ]
    store2.insert_ops(deploy_id2, ops2)
    backend2 = _RecordTeardown()
    try:
        result2 = destroy_ops_scoped(
            store2, backend2, deploy_id2, [f"op-{n}" for n in non_sweepable],
        )
        assert backend2.calls == []
        assert result2.actions == []
    finally:
        store2.close()
