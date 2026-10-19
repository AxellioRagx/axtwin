"""End-to-end tests for `axrange te deploy --update`
(`docs/WORK_ORDER_twin_incremental_update.md`) through the real CLI path
(`cli.main.cmd_deploy`), a real `StateStore`, and a FAKE adapter (no
Proxmox) — matching `tests/test_cli.py`'s established idiom.

Covers work order §6 criteria 2, 3, 4, 6, 7, 8, 9 end-to-end (criterion 1
and the barrier-reeval mechanism itself are unit-tested directly in
`tests/test_plandiff.py`; criterion 5's message SHAPE is also exercised
here). §3.5's sibling-dangling-host warning is tested directly against
`api.service.apply_update_tail` (hand-seeded state, mirroring `tests.
test_o2_taps`'s idiom) rather than through a full traffic-tier apply.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from axrange.cli import main as climain
from axrange.planner import StateStore
from axrange.planner.dag import _op_id
from axrange.planner.state import DeployStatus, OpStatus
from tests.test_planner_executor import _FakeAdapter

FIXT_ROOT = Path(__file__).parent / "fixtures"


BASE_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: incr-update-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.20.0.0/24
        vlan: 20
        gateway: 10.20.0.1
      - id: dmz
        cidr: 10.21.0.0/24
        vlan: 21
        gateway: 10.21.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{{ net: corp, ip: 10.20.0.10 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: {ha_ram}, disk: 10Gi }}
      - id: h-b
        kind: host
        nets: [{{ net: corp, ip: 10.20.0.11 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: 1Gi, disk: 10Gi }}
      - id: h-dmz
        kind: host
        nets: [{{ net: dmz, ip: 10.21.0.10 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: 1Gi, disk: 10Gi }}
  apps: []
  activity: []
bindings: []
"""

# BASE + a new host `h-new` on `corp` -- the "add a node" update target.
ADD_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: incr-update-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.20.0.0/24
        vlan: 20
        gateway: 10.20.0.1
      - id: dmz
        cidr: 10.21.0.0/24
        vlan: 21
        gateway: 10.21.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{ net: corp, ip: 10.20.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-b
        kind: host
        nets: [{ net: corp, ip: 10.20.0.11 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-dmz
        kind: host
        nets: [{ net: dmz, ip: 10.21.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-new
        kind: host
        nets: [{ net: corp, ip: 10.20.0.12 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
  apps: []
  activity: []
bindings: []
"""

# BASE minus h-b, minus h-dmz, minus the (now-orphaned) dmz network --
# exercises §3.2's "both directions" in one update: corp survives (h-a
# remains) so its vnet must NOT be swept; dmz has no survivor so its vnet
# MUST be swept.
REMOVE_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: incr-update-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.20.0.0/24
        vlan: 20
        gateway: 10.20.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{ net: corp, ip: 10.20.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
  apps: []
  activity: []
bindings: []
"""

# BASE + a whole NEW network `eng` (with a new host `h-eng` on it) -- the
# "add a network" MUST-FIX 1 LOUD failure shape (code review):
# apply-sdn-config's own `inputs_resolved` (just `zone_id`) never mentions
# what it commits, so before the fix the new vnet stayed forever
# staged-but-uncommitted and `h-eng`'s `start-vm` would fail "bridge does
# not exist" on a real cluster.
ADD_NETWORK_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: incr-update-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.20.0.0/24
        vlan: 20
        gateway: 10.20.0.1
      - id: dmz
        cidr: 10.21.0.0/24
        vlan: 21
        gateway: 10.21.0.1
      - id: eng
        cidr: 10.22.0.0/24
        vlan: 22
        gateway: 10.22.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{ net: corp, ip: 10.20.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-b
        kind: host
        nets: [{ net: corp, ip: 10.20.0.11 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-dmz
        kind: host
        nets: [{ net: dmz, ip: 10.21.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-eng
        kind: host
        nets: [{ net: eng, ip: 10.22.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
  apps: []
  activity: []
bindings: []
"""

# BASE with `egress: true` flipped ON for `corp` (which already has a
# `gateway:`) -- the "flip egress on" MUST-FIX 1 SILENT failure shape (code
# review): classifies as `added: ['create-subnet[corp]']` ALONE (`mutated:
# []`); apply-sdn-config's own inputs never mention subnets, so before the
# fix every op reported COMPLETED and the deploy stayed green with NO
# gateway and NO SNAT ever actually committed for the segment.
EGRESS_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: incr-update-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.20.0.0/24
        vlan: 20
        gateway: 10.20.0.1
        egress: true
      - id: dmz
        cidr: 10.21.0.0/24
        vlan: 21
        gateway: 10.21.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{ net: corp, ip: 10.20.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-b
        kind: host
        nets: [{ net: corp, ip: 10.20.0.11 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      - id: h-dmz
        kind: host
        nets: [{ net: dmz, ip: 10.21.0.10 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
  apps: []
  activity: []
bindings: []
"""


def _base_scenario(*, ha_ram: str = "1Gi") -> str:
    return BASE_SCENARIO.format(ha_ram=ha_ram)


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _deploy_args(*, scenario: str, db: str, fresh: bool = False, update: bool = False,
                 allow_destroy: bool = False, yes: bool = True, node: str = "pxtest1",
                 reapply: bool = False):
    return argparse.Namespace(
        command="deploy", scenario=scenario, images=str(FIXT_ROOT),
        adapter_config="/nonexistent.conf", node=node, db=db, dry_run=False,
        fresh=fresh, update=update, allow_destroy=allow_destroy,
        # `reapply` is NOT coerced the way `yes` is below: the
        # "--reapply without --update is a usage error" test needs to pass
        # exactly that combination, and no other caller sets it.
        reapply=reapply,
        # `yes`'s default of True above is a TEST-convenience default (every
        # removal test wants to skip the confirm prompt without having to
        # say so explicitly) -- but `--yes` only means anything WITH
        # `--update` (S5, code review), so force it False on every
        # non-update call regardless of that default, or an ordinary
        # first-time `_deploy_base` call trips the "only makes sense with
        # --update" guard purely because of this helper's convenience
        # default, not anything the test itself asked for.
        yes=(yes if update else False),
    )


class _NamingAwareFakeAdapter(_FakeAdapter):
    """`_FakeAdapter` returns the SAME canned output for every op of a given
    type regardless of scope (e.g. every `create-vnet` call gets back
    `vnet_id: "tevcorp"`) -- fine for the executor-only tests it was built
    for, but this file's removal-sweep tests run a REAL `destroy_ops_scoped`
    sweep against whatever `clone-vm`/`create-vnet`/`create-ovs-bridge`/
    `create-sdn-zone` recorded, and that sweep checks the SAME real
    deterministic-naming shape guards a live cluster would -- a canned,
    scope-blind output collides across networks/hosts and trips a guard
    violation that has nothing to do with this feature. This subclass
    computes REAL, per-scope, naming.py-shaped outputs for exactly those
    four op types (mirroring what the real adapter does); every other op
    type still falls through to `_FakeAdapter.OUTPUTS`."""

    def run(self, op, resolved_inputs, ctx):
        from axrange.adapter_proxmox import naming

        if op.op_name == "clone-vm":
            self.calls.append((op.op_name, dict(resolved_inputs)))
            return {"vmid": naming.vmid_for_op(ctx.plan_id, op.op_id)}
        if op.op_name == "create-vnet":
            self.calls.append((op.op_name, dict(resolved_inputs)))
            return {
                "vnet_id": naming.vnet_id_for_net(
                    op.scope.get("net", ""), plan_id=ctx.plan_id,
                )
            }
        if op.op_name == "create-ovs-bridge":
            self.calls.append((op.op_name, dict(resolved_inputs)))
            return {"bridge_name": naming.underlay_bridge_for_plan(ctx.plan_id)}
        if op.op_name == "create-sdn-zone":
            self.calls.append((op.op_name, dict(resolved_inputs)))
            return {"zone_id": naming.zone_id_for_plan(ctx.plan_id)}
        return super().run(op, resolved_inputs, ctx)


@pytest.fixture
def fake_env(monkeypatch):
    """`ProxmoxAdapter`/`ConfigBackend` -> `_NamingAwareFakeAdapter`;
    `ProxmoxTeardown` -> a recording fake, matching `tests/test_teardown.
    py`'s idiom, so an `--update`'s removal-sweep half runs against a fake
    too. Returns the teardown fake so tests can inspect `.calls`."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_teardown import _RecordTeardown

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _NamingAwareFakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _NamingAwareFakeAdapter())
    backend = _RecordTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: backend)
    return backend


def _deploy_base(tmp_path, db, fake_env, *, ha_ram: str = "1Gi") -> str:
    scn = _write(tmp_path, "base.yaml", _base_scenario(ha_ram=ha_ram))
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db))
    assert rc == 0
    store = StateStore(db)
    did = store.latest_deploys(limit=1)[0].deploy_id
    store.close()
    return did


# ---------------------------------------------------------------------------
# Criterion 2 — pinned plan_id ⇒ every surviving op id/vmid/name/zone/vnet/
# bridge is identical before and after.
# ---------------------------------------------------------------------------
def test_update_add_node_pins_plan_id_and_leaves_existing_ops_untouched(
    tmp_path, fake_env, capsys,
):
    from axrange.adapter_proxmox import naming

    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    before = store.get_deploy(deploy_id)
    plan_id_before = before.plan_id
    scenario_id = before.scenario_id
    ha_op_id = _op_id(plan_id_before, "clone-vm", {"host": "h-a"})
    hb_op_id = _op_id(plan_id_before, "clone-vm", {"host": "h-b"})
    vnet_op_id = _op_id(plan_id_before, "create-vnet", {"net": "corp"})
    zone_op_id = _op_id(plan_id_before, "create-sdn-zone", {"scenario": scenario_id})
    bridge_op_id = _op_id(plan_id_before, "create-ovs-bridge", {"scenario": scenario_id})
    ha_row_before = store.get_op(deploy_id, ha_op_id)
    hb_row_before = store.get_op(deploy_id, hb_op_id)
    assert ha_row_before is not None and ha_row_before.status == OpStatus.COMPLETED
    # A SNAPSHOT captured BEFORE the update -- `vm_name_for_host` has no
    # persisted counterpart to check against (`clone-vm`'s output carries
    # only `vmid`, never a display name), so the non-circular check for it
    # (§6.2 rewrite, code review) is comparing this INDEPENDENTLY captured
    # value against a LIVE post-update recompute, not re-deriving it from
    # `plan_id_before` a second time.
    ha_vm_name_snapshot = naming.vm_name_for_host(plan_id_before, "h-a")
    store.close()

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    # Grouped-by-entity diff shape (S7, code review): a `+ host/h-new`
    # header, with each op indented underneath.
    assert deploy_id in out  # M5-i: the diff header names the deploy_id
    assert "+ host/h-new" in out
    assert "clone-vm[h-new]" in out

    store = StateStore(db)
    after = store.get_deploy(deploy_id)
    # THE safety property: plan_id never moved.
    assert after.plan_id == plan_id_before
    assert after.deploy_id == deploy_id  # same deploy row, not a new one

    ha_row_after = store.get_op(deploy_id, ha_op_id)
    hb_row_after = store.get_op(deploy_id, hb_op_id)
    # Byte-identical persisted rows -- proof neither op was redone.
    assert ha_row_after.outputs_json == ha_row_before.outputs_json
    assert ha_row_after.completed_at == ha_row_before.completed_at
    assert hb_row_after.outputs_json == hb_row_before.outputs_json
    assert hb_row_after.completed_at == hb_row_before.completed_at

    # §6.2 rewrite (code review -- the ORIGINAL version of this assertion
    # was circular: it compared `vmid_for_op(plan_id_before, op)` against
    # `vmid_for_op(after.plan_id, op)` having ALREADY asserted `after.
    # plan_id == plan_id_before` a few lines up, so it was guaranteed to
    # pass regardless of what `vmid_for_op` even computes). This instead
    # compares the LIVE naming.py recompute (under the pinned plan_id)
    # against WHAT THE CLUSTER ACTUALLY RECORDED (`_NamingAwareFakeAdapter`
    # persists real deterministic names, not canned ones) -- a genuine
    # cross-check that would fail if plan_id pinning, or naming.py's own
    # formulas, ever silently diverged from what was actually built.
    hnew_op_id = _op_id(plan_id_before, "clone-vm", {"host": "h-new"})
    hnew_row = store.get_op(deploy_id, hnew_op_id)
    assert hnew_row is not None and hnew_row.status == OpStatus.COMPLETED

    for op_id, row in ((ha_op_id, ha_row_after), (hb_op_id, hb_row_after), (hnew_op_id, hnew_row)):
        recorded_vmid = json.loads(row.outputs_json)["vmid"]
        assert recorded_vmid == naming.vmid_for_op(after.plan_id, op_id)

    vnet_row = store.get_op(deploy_id, vnet_op_id)
    assert json.loads(vnet_row.outputs_json)["vnet_id"] == naming.vnet_id_for_net(
        "corp", plan_id=after.plan_id
    )
    zone_row = store.get_op(deploy_id, zone_op_id)
    assert json.loads(zone_row.outputs_json)["zone_id"] == naming.zone_id_for_plan(after.plan_id)
    bridge_row = store.get_op(deploy_id, bridge_op_id)
    assert json.loads(bridge_row.outputs_json)["bridge_name"] == naming.underlay_bridge_for_plan(
        after.plan_id
    )
    assert naming.vm_name_for_host(after.plan_id, "h-a") == ha_vm_name_snapshot
    store.close()


def test_update_ir_hash_advances_but_plan_id_and_deploy_id_do_not(tmp_path, fake_env):
    """§3.4: after an update, `ir_hash` (current generation) changes while
    `plan_id` (stable identity) does not — the documented consequence."""
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    store = StateStore(db)
    ir_hash_before = store.get_deploy(deploy_id).ir_hash
    plan_id_before = store.get_deploy(deploy_id).plan_id
    store.close()

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0

    store = StateStore(db)
    d = store.get_deploy(deploy_id)
    assert d.ir_hash != ir_hash_before
    assert d.plan_id == plan_id_before
    store.close()


# ---------------------------------------------------------------------------
# Criterion 3 — additive edit ⇒ removed set empty, zero adapter calls for
# unchanged ops.
# ---------------------------------------------------------------------------
def test_update_additive_edit_makes_zero_adapter_calls_for_unchanged_ops(
    tmp_path, fake_env, capsys, monkeypatch,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    # §6.3 rewrite (code review): a snapshot of {op_id: (status, started_at,
    # completed_at, outputs_json)} for EVERY pre-existing row -- catches a
    # spurious re-run of ANY op type, not just ones whose `inputs_resolved`
    # happens to carry a `node_id`/`host` key the adapter-call check below
    # can filter on. The ORIGINAL version of this test missed 7 of 9
    # per-host op types that way: `configure-vm-hardware`/
    # `attach-vm-to-vnet`/`start-vm`/`wait-guest-agent`/`wait-os-ready`
    # carry no host key in their `inputs_resolved` at all (only `vmid`,
    # resolved from a prior op's OUTPUT at run time, never a static input),
    # so a spurious re-run of any of THEM would have gone completely
    # undetected by `called_hosts <= {"h-new"}` alone.
    store = StateStore(db)
    before_rows = store.ops_for_deploy(deploy_id)
    before_ids = {row.op_id for row in before_rows}
    before_snapshot = {
        row.op_id: (row.status, row.started_at, row.completed_at, row.outputs_json)
        for row in before_rows
    }
    store.close()

    # A FRESH _FakeAdapter instance for the update run -- any call against
    # an unchanged op would show up as a call THIS instance recorded.
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod

    fresh_adapter = _FakeAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fresh_adapter)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: fresh_adapter)

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Unchanged:" in out
    assert not any(line.startswith("  - ") for line in out.splitlines())  # nothing removed

    # Every call this fresh adapter instance saw must be for h-new only.
    called_hosts = set()
    for op_name, inputs in fresh_adapter.calls:
        node_id = inputs.get("node_id") or inputs.get("host")
        if node_id:
            called_hosts.add(node_id)
    assert called_hosts <= {"h-new"}
    assert len(fresh_adapter.calls) > 0  # h-new's ops DID run

    # The durable-record half (§6.3 rewrite): EVERY pre-existing row is
    # byte-identical after the update -- catches a spurious re-run the
    # adapter-call check above is blind to (a host-less op that got
    # re-executed against the SAME inputs would make no NEW distinguishable
    # call, but its `started_at`/`completed_at` would still advance).
    store = StateStore(db)
    after_rows = {row.op_id: row for row in store.ops_for_deploy(deploy_id)}
    store.close()

    assert before_ids <= set(after_rows)  # nothing pre-existing vanished
    for op_id in before_ids:
        after = after_rows[op_id]
        after_snapshot = (after.status, after.started_at, after.completed_at, after.outputs_json)
        assert after_snapshot == before_snapshot[op_id], (
            f"{op_id} changed after a purely-additive --update"
        )

    new_ids = set(after_rows) - before_ids
    assert new_ids  # h-new's ops actually appeared
    assert all("h-new" in op_id for op_id in new_ids)  # scoped to the added host only


# ---------------------------------------------------------------------------
# Criterion 4 — removing the last node on a segment sweeps its vnet;
# removing one of several does not (both directions, one update).
# ---------------------------------------------------------------------------
def test_update_removal_sweeps_orphaned_vnet_but_not_a_still_used_one(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    )
    assert rc == 0
    out = capsys.readouterr().out
    # Only the DIFF block (before the executor's "Resuming deploy:" section,
    # which prints one line per op in the WHOLE topological walk, including
    # every unchanged/resumed op -- `create-vnet[corp]` legitimately shows
    # up THERE regardless of this diff's own content).
    diff_block = out.split("Resuming deploy:")[0]
    # Grouped-by-entity diff shape (S7, code review): `- host/h-b`/
    # `- host/h-dmz`/`- net/dmz` headers, each op indented underneath.
    assert "- host/h-b" in diff_block
    assert "clone-vm[h-b]" in diff_block
    assert "- host/h-dmz" in diff_block
    assert "clone-vm[h-dmz]" in diff_block
    assert "- net/dmz" in diff_block
    assert "create-vnet[dmz]" in diff_block
    assert "- net/corp" not in diff_block
    assert "create-vnet[corp]" not in diff_block
    # SHOULD-FIX 5 (code review): removing h-b/h-dmz/dmz sweeps every one of
    # them WHOLESALE -- each host's own `clone-vm` and the net's own
    # `create-vnet` are ALL in `diff.removed` too, so every subordinate op
    # (`configure-vm-hardware[h-b]`, `attach-vm-to-vnet[h-dmz@dmz]`, the
    # `network-ready[dmz]` barrier, ...) must carry NO "no teardown"
    # annotation at all -- the VM/vnet itself is swept, taking every
    # in-guest/on-wire effect with it. See the dedicated annotation test
    # below for the residual case where the entity SURVIVES.
    assert "no teardown" not in diff_block
    assert "no separate teardown" not in diff_block

    vnet_calls = [target for kind, target in fake_env.calls if kind == "vnet"]
    assert len(vnet_calls) == 1  # only dmz's vnet was ever handed to the backend

    store = StateStore(db)
    plan_id = store.get_deploy(deploy_id).plan_id
    dmz_vnet_op = store.get_op(deploy_id, _op_id(plan_id, "create-vnet", {"net": "dmz"}))
    corp_vnet_op = store.get_op(deploy_id, _op_id(plan_id, "create-vnet", {"net": "corp"}))
    assert dmz_vnet_op.status == OpStatus.DESTROYED
    assert corp_vnet_op.status == OpStatus.COMPLETED
    store.close()


def test_update_removal_annotation_still_applies_when_the_entity_survives(
    tmp_path, fake_env, capsys,
):
    """SHOULD-FIX 5's residual case (code review): removing `corp`'s
    `create-subnet` (by editing `egress: true` back OFF -- the mirror of
    the MUST-FIX 1 `EGRESS_SCENARIO` test above) while `corp` itself
    SURVIVES (its `create-vnet` is unchanged, kept alive by both hosts
    still on it) is exactly the shape where the "no separate teardown"
    annotation is still ACCURATE, unlike the whole-node-removal case
    pinned above: `create-subnet` has no sweep action of its own
    (`teardown.SWEEPABLE_OP_NAMES`), and the network's gateway/SNAT stays
    genuinely half-torn-down (config removed, cluster object not) unless
    an operator notices and acts on the annotation.

    (Dropping a still-alive host's NIC instead, the more obvious "entity
    survives" shape, does NOT isolate a single non-sweepable removal this
    cleanly -- it also mutates that host's `configure-cloud-init` `ip_
    config`, which correctly hard-refuses as Mutated per §3.1 before ever
    reaching the annotation logic this test targets.)"""
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "egress-on.yaml", EGRESS_SCENARIO)
    rc0 = climain.cmd_deploy(_deploy_args(scenario=scn, db=db))
    assert rc0 == 0
    capsys.readouterr()

    scn2 = _write(tmp_path, "egress-off.yaml", _base_scenario())
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    )
    assert rc == 0
    out = capsys.readouterr().out
    diff_block = out.split("Resuming deploy:")[0]

    assert "- net/corp" in diff_block
    assert "create-subnet[corp]  (no separate teardown)" in diff_block
    # corp's own vnet SURVIVES the update -- it never shows up as removed
    # (would be a false-annotation-suppression bug if it did).
    assert "create-vnet[corp]" not in diff_block


# ---------------------------------------------------------------------------
# Work order §6 criterion (c) (gated live-verify's own scripted step, pinned
# here at the unit/integration level per code review M2): re-running the
# IDENTICAL --update a second time must be a clean no-op -- zero teardown-
# backend calls, an empty removed set, no re-prompt. Before the M2 fix, a
# row a prior update's sweep had already marked `destroyed` was re-
# classified `removed` on EVERY later diff forever: the second run refused
# without --allow-destroy ("would remove N op(s)") and, WITH the flag,
# re-issued real teardown calls against already-gone resources.
# ---------------------------------------------------------------------------
def test_update_rerun_of_the_identical_removal_is_a_clean_noop(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    rc1 = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    )
    assert rc1 == 0
    capsys.readouterr()
    calls_after_first = list(fake_env.calls)
    assert calls_after_first  # the first run genuinely swept something

    # The IDENTICAL update again, same scenario file, no state edited by
    # hand in between -- this is the exact re-run scenario §6(c) describes.
    rc2 = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    )
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "Nothing to add or remove" in out  # is_noop (S11) surfaced to the operator
    assert "  - " not in out  # nothing listed as removed a second time

    # ZERO new teardown-backend calls -- the sweep genuinely did nothing,
    # not merely "nothing new that happens to look different."
    assert list(fake_env.calls) == calls_after_first

    store = StateStore(db)
    plan_id = store.get_deploy(deploy_id).plan_id
    dmz_vnet_op = store.get_op(deploy_id, _op_id(plan_id, "create-vnet", {"net": "dmz"}))
    assert dmz_vnet_op.status == OpStatus.DESTROYED  # unchanged by the re-run
    store.close()


# ---------------------------------------------------------------------------
# Criterion 5 — mutation refusal names op + field + old -> new; nothing runs.
# ---------------------------------------------------------------------------
def test_update_mutation_refuses_names_op_field_and_values_executes_nothing(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    ir_hash_before = store.get_deploy(deploy_id).ir_hash
    ha_op_id = _op_id(store.get_deploy(deploy_id).plan_id, "configure-vm-hardware", {"host": "h-a"})
    hardware_row_before = store.get_op(deploy_id, ha_op_id)
    store.close()

    mutated_scn = _write(tmp_path, "mutate.yaml", _base_scenario(ha_ram="4Gi"))
    rc = climain.cmd_deploy(_deploy_args(scenario=mutated_scn, db=db, update=True))

    assert rc != 0
    err = capsys.readouterr().err
    assert "configure-vm-hardware[h-a]" in err
    assert "'ram'" in err
    assert "1Gi" in err and "4Gi" in err
    assert "mutated" in err

    # Nothing executed: the deploy row + the mutated op's row are untouched.
    store = StateStore(db)
    assert store.get_deploy(deploy_id).ir_hash == ir_hash_before
    hardware_row_after = store.get_op(deploy_id, ha_op_id)
    assert hardware_row_after.outputs_json == hardware_row_before.outputs_json
    assert hardware_row_after.completed_at == hardware_row_before.completed_at
    store.close()


# ---------------------------------------------------------------------------
# Criterion 6 — removal without --allow-destroy refuses, executes nothing.
# ---------------------------------------------------------------------------
def test_update_removal_without_allow_destroy_refuses_and_executes_nothing(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    ir_hash_before = store.get_deploy(deploy_id).ir_hash
    hb_op_id = _op_id(store.get_deploy(deploy_id).plan_id, "clone-vm", {"host": "h-b"})
    hb_row_before = store.get_op(deploy_id, hb_op_id)
    store.close()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))  # no allow_destroy

    assert rc != 0
    err = capsys.readouterr().err
    assert "--allow-destroy" in err

    assert fake_env.calls == []  # the teardown backend was never touched
    store = StateStore(db)
    assert store.get_deploy(deploy_id).ir_hash == ir_hash_before
    hb_row_after = store.get_op(deploy_id, hb_op_id)
    assert hb_row_after.status == OpStatus.COMPLETED  # still standing
    assert hb_row_after.completed_at == hb_row_before.completed_at
    store.close()


# ---------------------------------------------------------------------------
# Criterion 7 — `_protected()` still refuses o2/img/external-sink resources
# inside a removal sweep.
# ---------------------------------------------------------------------------
def test_update_removal_protect_guard_still_refuses_a_foreign_tier_name(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    hb_vmid_op = _op_id(store.get_deploy(deploy_id).plan_id, "clone-vm", {"host": "h-b"})
    hb_vmid = None
    import json as _json

    outputs = _json.loads(store.get_op(deploy_id, hb_vmid_op).outputs_json or "{}")
    hb_vmid = outputs.get("vmid")
    store.close()
    assert hb_vmid is not None
    # h-b's PVE-resolved name will look like a standing o2_ resource.
    fake_env.vm_names[hb_vmid] = "o2-sensor-vm"

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    )
    # M3 (code review): the added-ops half (empty here) still succeeded, but
    # the REMOVAL half is NOT clean (the protect guard refused h-b's VM) --
    # `apply_update_tail`'s `UpdateTailResult.clean=False` now propagates to
    # a non-zero exit + an "INCOMPLETE" message, mirroring `te destroy`'s
    # own "Result: INCOMPLETE" posture. This used to silently report rc=0
    # (a bug this work order's own code review caught): a VM the protect
    # guard refused was left running with nothing telling the operator so.
    assert rc == 1
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert ("vm", str(hb_vmid)) not in fake_env.calls

    store = StateStore(db)
    hb_row = store.get_op(deploy_id, hb_vmid_op)
    assert hb_row.status == OpStatus.COMPLETED  # refused -> never marked destroyed
    # SHOULD-FIX 3 (code review): the PERSISTED deploy row must agree with
    # the terminal operator -- `executor.run()`'s own successful added-ops
    # pass already wrote `completed`; a later `te status`/`GET /api/
    # deploys/{id}` must not report a green twin while h-b's protect-
    # guard-refused VM is still running. Mirrors `_proxmox_runner`'s
    # `post_run` downgrade (`api/service.py`) so both surfaces agree.
    assert store.get_deploy(deploy_id).status == DeployStatus.FAILED
    store.close()


# ---------------------------------------------------------------------------
# Criterion 8 — a barrier whose predecessor set changed is re-evaluated.
#
# The MECHANISM itself (does a barrier whose predecessor set gained an
# Added op get correctly identified) is unit-tested directly against
# synthetic Plan/edges in `tests/test_plandiff.py`
# (`test_barrier_gaining_a_new_predecessor_is_flagged_for_reeval` and
# neighbors) -- the `te`-emittable op catalog's OWN barriers don't happen
# to exhibit an aggregate "gains a predecessor from a newly-added PEER
# entity" shape without heavy fabric-router/AD-domain setup
# (`network-ready[$net]`'s sole predecessor is the single scenario-scoped
# `apply-sdn-config`, not a per-host attach op; `host-ready[$host]`'s
# predecessors are all `[$host]`-scoped to itself). The catalog barriers
# that DO have an aggregate `[*]`/`[$var@*]` shape (`mirror-ready`,
# `domain-ready`, `fabric-ready`) are either never `te`-emitted or need
# fabric/AD content out of proportion to what this test needs to prove.
# So this test proves the WIRING instead: `cmd_deploy` must force-reset
# EXACTLY what `barriers_to_reevaluate` returns, via `StateStore.
# force_reset_ops`, BEFORE the executor runs.
# ---------------------------------------------------------------------------
def test_update_wires_barriers_to_reevaluate_result_into_force_reset(
    tmp_path, fake_env, monkeypatch,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)

    store = StateStore(db)
    plan_id = store.get_deploy(deploy_id).plan_id
    network_ready_corp = _op_id(plan_id, "network-ready", {"net": "corp"})
    store.close()

    import axrange.planner.plandiff as plandiff_mod
    from axrange.planner.state import StateStore as _SS

    monkeypatch.setattr(
        plandiff_mod, "barriers_to_reevaluate",
        lambda plan, added: frozenset({network_ready_corp}),
    )

    force_reset_calls: list[tuple[str, set]] = []
    orig_force_reset = _SS.force_reset_ops

    def _spy(self, deploy_id_, op_ids):
        force_reset_calls.append((deploy_id_, set(op_ids)))
        return orig_force_reset(self, deploy_id_, op_ids)

    monkeypatch.setattr(_SS, "force_reset_ops", _spy)

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0

    assert (deploy_id, {network_ready_corp}) in force_reset_calls

    # And the effect actually lands: the barrier's completed_at genuinely
    # advanced (it went pending -> re-completed), not left stale.
    store = StateStore(db)
    row = store.get_op(deploy_id, network_ready_corp)
    assert row.status == OpStatus.COMPLETED
    store.close()


def test_update_no_barrier_reeval_call_when_nothing_needs_it(tmp_path, fake_env, monkeypatch):
    """The selective half of the same guarantee: when `barriers_to_reevaluate`
    finds nothing (the ordinary case for a plain host add against this
    catalog), `force_reset_ops` is never called for barriers at all."""
    db = str(tmp_path / "state.db")
    _deploy_base(tmp_path, db, fake_env)

    from axrange.planner.state import StateStore as _SS

    force_reset_calls: list[tuple[str, set]] = []
    orig_force_reset = _SS.force_reset_ops

    def _spy(self, deploy_id_, op_ids):
        force_reset_calls.append((deploy_id_, set(op_ids)))
        return orig_force_reset(self, deploy_id_, op_ids)

    monkeypatch.setattr(_SS, "force_reset_ops", _spy)

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    assert force_reset_calls == []


# ---------------------------------------------------------------------------
# MUST-FIX 1 (code review) — §3.3 extended past barriers to non-barrier
# AGGREGATE ops (`plandiff.ops_to_reexecute`). Both measured failure shapes
# pinned end-to-end through the real CLI path against the REAL te-emittable
# catalog: adding a whole new network (LOUD — on a real cluster the new
# host's `start-vm` would fail "bridge does not exist" without the fix) and
# flipping `egress: true` on a network that already has a `gateway:`
# (SILENT — nothing fails or refuses on its own; the deploy stays green
# with the segment's gateway/SNAT never actually committed). The mechanism
# itself (which op TYPES qualify, and the boundary against non-idempotent
# `[$var@*]`-shaped ops like `run-script`) is unit-tested directly against
# synthetic Plans in `tests/test_plandiff.py`.
# ---------------------------------------------------------------------------
def test_update_adding_a_network_reexecutes_apply_sdn_config(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """The LOUD shape: a brand-new network's `create-vnet`/`network-ready`
    land in `diff.added`, but `apply-sdn-config`'s own `inputs_resolved`
    (just `zone_id`) never mentions them — before this fix it stayed
    stale-`completed` forever, so the new vnet was never actually committed
    cluster-wide even though `create-vnet[eng]` itself reports COMPLETED."""
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    deploy_row = store.get_deploy(deploy_id)
    plan_id, scenario_id = deploy_row.plan_id, deploy_row.scenario_id
    apply_op_id = _op_id(plan_id, "apply-sdn-config", {"scenario": scenario_id})
    apply_row_before = store.get_op(deploy_id, apply_op_id)
    assert apply_row_before.status == OpStatus.COMPLETED
    completed_at_before = apply_row_before.completed_at
    store.close()

    # A FRESH adapter instance for the update run: the direct proof that
    # apply-sdn-config was actually CALLED AGAIN, not merely left
    # `completed` from the base deploy — the exact bug this fix closes.
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod

    fresh_adapter = _NamingAwareFakeAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fresh_adapter)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: fresh_adapter)

    scn2 = _write(tmp_path, "add-net.yaml", ADD_NETWORK_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "+ net/eng" in out
    assert "create-vnet[eng]" in out
    assert "+ host/h-eng" in out
    assert "clone-vm[h-eng]" in out

    assert any(name == "apply-sdn-config" for name, _ in fresh_adapter.calls)

    store = StateStore(db)
    apply_row_after = store.get_op(deploy_id, apply_op_id)
    assert apply_row_after.status == OpStatus.COMPLETED
    assert apply_row_after.completed_at != completed_at_before  # genuinely re-ran

    heng_op = _op_id(plan_id, "clone-vm", {"host": "h-eng"})
    heng_row = store.get_op(deploy_id, heng_op)
    assert heng_row is not None and heng_row.status == OpStatus.COMPLETED
    store.close()


def test_update_flipping_egress_on_reexecutes_apply_sdn_config(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """The SILENT shape — the more important test, per code review, since
    nothing fails on its own: flipping `egress: true` on `corp` (which
    already has a `gateway:`) classifies as `added: ['create-subnet
    [corp]']` ALONE (`mutated: []`, `removed: []`) — the diff output looks
    perfectly ordinary, `create-subnet[corp]` itself reports COMPLETED, and
    BEFORE this fix `apply-sdn-config` would never be force-reset, so the
    segment's gateway/SNAT stayed forever uncommitted with the deploy still
    reporting green throughout."""
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    deploy_row = store.get_deploy(deploy_id)
    plan_id, scenario_id = deploy_row.plan_id, deploy_row.scenario_id
    apply_op_id = _op_id(plan_id, "apply-sdn-config", {"scenario": scenario_id})
    completed_at_before = store.get_op(deploy_id, apply_op_id).completed_at
    store.close()

    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod

    fresh_adapter = _NamingAwareFakeAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fresh_adapter)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: fresh_adapter)

    scn2 = _write(tmp_path, "egress.yaml", EGRESS_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    # Confirms this really is the additive-only silent shape: create-subnet
    # lands in `added`, nothing is `removed`, nothing refuses as `mutated`
    # (a mutation refusal would have made `rc != 0` above).
    assert "+ net/corp" in out
    assert "create-subnet[corp]" in out
    assert "  - " not in out  # nothing removed

    assert any(name == "apply-sdn-config" for name, _ in fresh_adapter.calls)

    store = StateStore(db)
    apply_row_after = store.get_op(deploy_id, apply_op_id)
    assert apply_row_after.completed_at != completed_at_before  # genuinely re-ran

    subnet_op = _op_id(plan_id, "create-subnet", {"net": "corp"})
    subnet_row = store.get_op(deploy_id, subnet_op)
    assert subnet_row is not None and subnet_row.status == OpStatus.COMPLETED
    store.close()


# ---------------------------------------------------------------------------
# Criterion 9 — --update + --fresh errors.
# ---------------------------------------------------------------------------
def test_update_and_fresh_together_errors(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, fresh=True)
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "mutually exclusive" in err
    assert fake_env.calls == []


# ---------------------------------------------------------------------------
# --update against a scenario with no standing deploy refuses (a corollary
# of criterion 9's "refuse cleanly" family, not separately numbered).
# ---------------------------------------------------------------------------
def test_update_with_no_standing_deploy_refuses(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "base.yaml", _base_scenario())
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db, update=True))
    assert rc != 0
    err = capsys.readouterr().err
    assert "--update requires a standing deploy" in err
    store = StateStore(db)
    assert store.latest_deploys(limit=1) == []
    store.close()


# ---------------------------------------------------------------------------
# S5 (code review): --allow-destroy/--yes only mean anything WITH --update
# -- must be refused, not silently ignored, matching the module's existing
# `--param only makes sense with --config` precedent.
# ---------------------------------------------------------------------------
def test_allow_destroy_without_update_is_refused(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "base.yaml", _base_scenario())
    # `update=False` (the default) -- `_deploy_args` already forces
    # `yes=False` in that case, so this exercises `allow_destroy` alone.
    args = _deploy_args(scenario=scn, db=db, allow_destroy=True)
    rc = climain.cmd_deploy(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "--allow-destroy" in err
    assert "--update" in err
    store = StateStore(db)
    assert store.latest_deploys(limit=1) == []  # refused before touching state
    store.close()


def test_yes_without_update_is_refused(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "base.yaml", _base_scenario())
    args = _deploy_args(scenario=scn, db=db)
    args.update = False
    args.yes = True  # explicitly set, bypassing _deploy_args's own coercion
    rc = climain.cmd_deploy(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "--yes" in err
    assert "--update" in err
    store = StateStore(db)
    assert store.latest_deploys(limit=1) == []
    store.close()


def test_allow_destroy_and_yes_with_update_are_not_refused(tmp_path, fake_env, capsys):
    """The guard is specific to the ABSENCE of --update -- an ordinary
    --update call with both flags set must not trip it."""
    db = str(tmp_path / "state.db")
    _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True, yes=True)
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "only makes sense with --update" not in err


# ---------------------------------------------------------------------------
# S6 (code review): `--dry-run --update` must resolve the standing deploy,
# print the diff, and return 0 WITHOUT touching state -- previously it
# returned `plan.render_tree()` of the UNPINNED plan before `resolve_
# deploy_plan` ever ran, so the one preview an operator wants before a
# destructive update was unreachable.
# ---------------------------------------------------------------------------
def test_dry_run_with_update_prints_diff_and_touches_nothing(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    store = StateStore(db)
    ir_hash_before = store.get_deploy(deploy_id).ir_hash
    store.close()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    args = _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True)
    args.dry_run = True
    rc = climain.cmd_deploy(args)
    assert rc == 0
    out = capsys.readouterr().out
    # It's the --update diff (grouped, deploy_id-headered), not the plain
    # plan.render_tree() an ordinary --dry-run prints.
    assert deploy_id in out
    assert "- host/h-b" in out
    assert "Apply?" not in out  # dry-run never reaches the confirm prompt

    # Nothing executed and nothing was swept.
    assert fake_env.calls == []
    store = StateStore(db)
    assert store.get_deploy(deploy_id).ir_hash == ir_hash_before
    hb_op_id = _op_id(store.get_deploy(deploy_id).plan_id, "clone-vm", {"host": "h-b"})
    assert store.get_op(deploy_id, hb_op_id).status == OpStatus.COMPLETED
    store.close()


def test_dry_run_without_update_is_unaffected(tmp_path, fake_env, capsys):
    """The ordinary (no --update) --dry-run path is untouched: it still
    returns the plain plan tree before a StateStore is even opened."""
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "base.yaml", _base_scenario())
    args = _deploy_args(scenario=scn, db=db)
    args.dry_run = True
    rc = climain.cmd_deploy(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "clone-vm[h-a]" in out
    assert "Plan diff for" not in out
    import os

    assert not os.path.exists(db)  # never even opened a StateStore


def test_apply_update_tail_teardown_error_is_caught_not_a_traceback(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """S9 (code review): `apply_update_tail` can raise `TeardownError`
    (defense-in-depth -- practically unreachable for a `te`-tier deploy,
    whose own tier is always recognized, but `cmd_deploy` must still catch
    it cleanly rather than let it surface as a bare traceback, the same
    posture every other refusal in this function already has."""
    from axrange.planner.teardown import TeardownError

    db = str(tmp_path / "state.db")
    _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    def _boom(*_a, **_kw):
        raise TeardownError("simulated orchestration-level refusal")

    import axrange.api.service as service_mod

    monkeypatch.setattr(service_mod, "apply_update_tail", _boom)

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))

    assert rc == 1
    err = capsys.readouterr().err
    assert "simulated orchestration-level refusal" in err


# ---------------------------------------------------------------------------
# The `find_deploy_for_plan`-callers sweep (docs/WORK_ORDER_
# twin_incremental_update.md §3.4): a BARE `te deploy` (no --update) of the
# SAME (already-updated, unedited-since) content must resume the pinned
# standing deploy, not mint a duplicate one just because a fresh
# `derive_plan_id(scenario_id, ir_hash)` no longer matches the pinned id.
# ---------------------------------------------------------------------------
def test_bare_redeploy_of_unedited_content_after_an_update_resumes_not_duplicates(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "add.yaml", ADD_SCENARIO)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db, update=True))
    assert rc == 0
    capsys.readouterr()

    store = StateStore(db)
    pinned_plan_id = store.get_deploy(deploy_id).plan_id
    store.close()

    # A PLAIN re-deploy (no --update) of the EXACT SAME (already-updated)
    # scenario file -- a freshly-derived plan_id for THIS content would NOT
    # equal `pinned_plan_id` (it was pinned away by the update above). This
    # must resume the SAME standing deploy, not create a second, parallel
    # one under a fresh plan_id (which would duplicate the whole twin).
    rc2 = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db))
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "already deployed" in out.lower() or "resuming" in out.lower()

    store = StateStore(db)
    all_deploys = store.latest_deploys(limit=10)
    # Still exactly ONE deploy for this scenario -- no duplicate was minted.
    assert [d.deploy_id for d in all_deploys] == [deploy_id]
    assert store.get_deploy(deploy_id).plan_id == pinned_plan_id
    store.close()


# ---------------------------------------------------------------------------
# M4 (code review): the auto-heal fallback must require the standing row's
# plan_id to have been PINNED AWAY by an earlier update, not just that its
# ir_hash happens to match. `deploy -> --fresh -> destroy the NEWER one` is
# a real, reachable way to produce two deploys sharing one plan_id (unedited
# content always hashes to the same plan_id, regardless of --fresh) --
# `find_deploy_for_plan`'s own docstring (state.py) pins that a destroyed
# top match is NEVER revived / never searched past for an older row sharing
# the same plan_id ("mint a fresh deploy_id below exactly as --fresh
# would"). Before the fix, the heal branch didn't check plan_id at all, so
# it silently rebound onto the OLDER standing deploy the moment its ir_hash
# happened to match -- reviving a deploy_id that same docstring says must
# never be revived in place.
# ---------------------------------------------------------------------------
def test_bare_redeploy_after_fresh_and_destroy_of_the_newer_mints_a_third_id(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id_1 = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn = _write(tmp_path, "base2.yaml", _base_scenario())
    rc_fresh = climain.cmd_deploy(_deploy_args(scenario=scn, db=db, fresh=True))
    assert rc_fresh == 0
    capsys.readouterr()

    store = StateStore(db)
    all_after_fresh = store.latest_deploys(limit=10)
    assert len(all_after_fresh) == 2
    deploy_id_2 = next(d.deploy_id for d in all_after_fresh if d.deploy_id != deploy_id_1)
    # Unedited content -> the SAME plan_id, by construction (derive_plan_id
    # is a pure function of scenario_id + ir_hash; --fresh only changes
    # which deploy_id gets minted).
    assert store.get_deploy(deploy_id_2).plan_id == store.get_deploy(deploy_id_1).plan_id
    store.close()

    destroy_args = argparse.Namespace(
        deploy_id=deploy_id_2, db=db, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc_destroy = climain.cmd_destroy(destroy_args)
    assert rc_destroy == 0
    capsys.readouterr()

    # A PLAIN redeploy (no --update, no --fresh) of the identical, unedited
    # content: `direct = find_deploy_for_plan(plan_id, exclude_destroyed=
    # True)` misses (deploy_id_2, the newest row sharing that plan_id, is
    # now destroyed -- and that lookup deliberately does not search past a
    # destroyed top match). This must mint a THIRD, brand-new deploy_id --
    # NOT silently rebind onto deploy_id_1 just because its ir_hash happens
    # to match.
    rc3 = climain.cmd_deploy(_deploy_args(scenario=scn, db=db))
    assert rc3 == 0
    out = capsys.readouterr().out
    assert "Deploy:" in out  # a FRESH deploy banner, not "already deployed"/"Resuming"

    store = StateStore(db)
    all_deploys = {d.deploy_id for d in store.latest_deploys(limit=10)}
    assert len(all_deploys) == 3
    assert deploy_id_1 in all_deploys and deploy_id_2 in all_deploys
    new_id = next(iter(all_deploys - {deploy_id_1, deploy_id_2}))
    assert new_id != deploy_id_1
    # deploy_id_1 (the untouched original) was never silently revived/reused.
    assert store.get_deploy(deploy_id_1).status.value == "completed"
    store.close()


# ---------------------------------------------------------------------------
# M5 (code review): `resolve_deploy_plan` must refuse rather than silently
# pick one of several standing `te` deploys of the same scenario_id --
# trivially reachable via the LES-126 edit-without-`--update` path (two
# genuinely different edits of one scenario, deployed back to back with no
# `--update`, both never destroyed).
# ---------------------------------------------------------------------------
def test_update_refuses_ambiguously_when_two_standing_te_deploys_share_a_scenario(
    tmp_path, fake_env, capsys,
):
    db = str(tmp_path / "state.db")
    deploy_id_1 = _deploy_base(tmp_path, db, fake_env, ha_ram="1Gi")
    capsys.readouterr()

    # An EDIT, deployed WITHOUT --update: mints a second, independent
    # standing deploy sharing the same scenario_id (LES-126's documented
    # dead-end path) -- both deploys are now live, non-destroyed.
    scn2 = _write(tmp_path, "edit2.yaml", _base_scenario(ha_ram="2Gi"))
    rc2 = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db))
    assert rc2 == 0
    capsys.readouterr()

    store = StateStore(db)
    standing = store.deploys_matching(
        scenario_id="incr-update-demo", tier="te", exclude_destroyed=True,
    )
    assert len(standing) == 2
    deploy_id_2 = next(d.deploy_id for d in standing if d.deploy_id != deploy_id_1)
    store.close()

    scn3 = _write(tmp_path, "edit3.yaml", _base_scenario(ha_ram="3Gi"))
    rc3 = climain.cmd_deploy(_deploy_args(scenario=scn3, db=db, update=True))
    assert rc3 != 0
    err = capsys.readouterr().err
    assert "2 standing te deploys" in err
    assert deploy_id_1 in err and deploy_id_2 in err

    # Refused before anything executed -- neither standing deploy was touched.
    assert fake_env.calls == []
    store = StateStore(db)
    assert store.get_deploy(deploy_id_1).status.value == "completed"
    assert store.get_deploy(deploy_id_2).status.value == "completed"
    store.close()


def test_ordinary_deploy_with_two_standing_deploys_mints_a_third_not_refused(
    tmp_path, fake_env, capsys,
):
    """MUST-FIX 2 (code review): the ambiguity refusal above is
    `--update`-ONLY. A PLAIN (no `--update`) deploy call that reaches
    `resolve_deploy_plan` with edited content -- `direct` misses because
    the content changed -- must NOT be blocked by an ambiguous store; it
    mints a fresh deploy_id exactly as it always has (the documented
    LES-126 dead-end), not a 409 instructing the operator to destroy two
    LIVE twins. Before the fix, this identical setup (two standing
    deploys, then a THIRD ordinary edit deployed WITHOUT --update)
    incorrectly raised the exact same refusal the `--update`-flagged test
    above legitimately exercises -- because the length check used to run
    BEFORE the `if update:` branch, unconditionally."""
    db = str(tmp_path / "state.db")
    deploy_id_1 = _deploy_base(tmp_path, db, fake_env, ha_ram="1Gi")
    capsys.readouterr()

    # An EDIT, deployed WITHOUT --update -- mints a second, independent
    # standing deploy sharing the same scenario_id (LES-126's documented
    # dead-end path) -- both deploys are now live, non-destroyed.
    scn2 = _write(tmp_path, "edit2.yaml", _base_scenario(ha_ram="2Gi"))
    rc2 = climain.cmd_deploy(_deploy_args(scenario=scn2, db=db))
    assert rc2 == 0
    capsys.readouterr()

    store = StateStore(db)
    standing = store.deploys_matching(
        scenario_id="incr-update-demo", tier="te", exclude_destroyed=True,
    )
    assert len(standing) == 2
    store.close()

    # A THIRD ordinary edit, deployed WITHOUT --update -- must mint a
    # fresh THIRD deploy_id, not refuse.
    scn3 = _write(tmp_path, "edit3.yaml", _base_scenario(ha_ram="3Gi"))
    rc3 = climain.cmd_deploy(_deploy_args(scenario=scn3, db=db))
    assert rc3 == 0
    out = capsys.readouterr().out
    assert "Deploy:" in out  # a FRESH deploy banner ("Deploy: <id>"), not a refusal

    store = StateStore(db)
    all_deploys = store.latest_deploys(limit=10)
    all_ids = {d.deploy_id for d in all_deploys}
    assert len(all_ids) == 3  # a genuine THIRD deploy_id was minted
    assert deploy_id_1 in all_ids
    for d in all_deploys:
        # All three genuinely deployed and completed -- not left `pending`/
        # `failed` by a refusal that fired partway through.
        assert d.status.value == "completed"
    store.close()


# ---------------------------------------------------------------------------
# The interactive confirmation gate (docs/PLANNER.md §9.3) — only reached
# when the removed set is non-empty and --yes wasn't passed.
# ---------------------------------------------------------------------------
def test_update_removal_prompts_and_aborts_on_a_non_y_answer(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    args = _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True, yes=False)
    rc = climain.cmd_deploy(args, confirm_fn=lambda _prompt: "n")

    assert rc != 0
    out = capsys.readouterr().out
    assert "Aborted" in out
    assert fake_env.calls == []
    store = StateStore(db)
    hb_op_id = _op_id(store.get_deploy(deploy_id).plan_id, "clone-vm", {"host": "h-b"})
    assert store.get_op(deploy_id, hb_op_id).status == OpStatus.COMPLETED
    store.close()


@pytest.mark.parametrize("raised", [EOFError, KeyboardInterrupt])
def test_update_removal_prompt_non_interactive_stdin_aborts_like_an_explicit_no(
    tmp_path, fake_env, capsys, raised,
):
    """S9 (code review): non-interactive stdin (e.g. `--update
    --allow-destroy` run in CI with no `--yes`) must abort exactly like an
    explicit "n" answer, not surface as an uncaught traceback -- `input()`
    raises `EOFError` when stdin is closed/empty; a Ctrl-C during the prompt
    raises `KeyboardInterrupt`."""
    db = str(tmp_path / "state.db")
    deploy_id = _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    def _raise(_prompt):
        raise raised

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    args = _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True, yes=False)
    rc = climain.cmd_deploy(args, confirm_fn=_raise)

    assert rc != 0
    out = capsys.readouterr().out
    assert "Aborted" in out
    assert fake_env.calls == []
    store = StateStore(db)
    hb_op_id = _op_id(store.get_deploy(deploy_id).plan_id, "clone-vm", {"host": "h-b"})
    assert store.get_op(deploy_id, hb_op_id).status == OpStatus.COMPLETED
    store.close()


def test_update_removal_prompt_proceeds_on_y_answer(tmp_path, fake_env, capsys):
    db = str(tmp_path / "state.db")
    _deploy_base(tmp_path, db, fake_env)
    capsys.readouterr()

    scn2 = _write(tmp_path, "remove.yaml", REMOVE_SCENARIO)
    args = _deploy_args(scenario=scn2, db=db, update=True, allow_destroy=True, yes=False)
    rc = climain.cmd_deploy(args, confirm_fn=lambda _prompt: "y")

    assert rc == 0
    assert any(kind == "vm" for kind, _ in fake_env.calls)


# ---------------------------------------------------------------------------
# §3.5 — sibling-tier dangling-host warning. Hand-seeded (mirrors
# `tests.test_o2_taps`'s idiom); exercised against `apply_update_tail`
# directly rather than the full traffic-tier apply.
# ---------------------------------------------------------------------------
def _surviving_clone_vm(op_id: str, host: str) -> "OpInstance":
    """A minimal `OpInstance` standing in for a SURVIVING op in the new
    plan — `diff_plan` classifies by op_id set membership, so this only
    needs to exist with the matching literal `op_id` the hand-seeded
    persisted row used (`_op_row`'s free-form `op_id`, not a real
    `dag._op_id`-computed one) for `diff_plan` to correctly NOT count it as
    removed."""
    from axrange.ops.registry import CleanupStrategy, IdempotencyClass, OpKind
    from axrange.planner.dag import OpInstance

    return OpInstance(
        op_id=op_id, op_name="clone-vm", op_catalog_id="OP-HOST-010",
        kind=OpKind.ACTION, scope={"host": host}, inputs_resolved={},
        inputs_from_outputs={}, cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA, resource_categories=(),
        timeout_s=None,
    )


def test_apply_update_tail_warns_about_a_dangling_tfc_binding(tmp_path):
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.api.service import apply_update_tail
    from axrange.planner.dag import Plan
    from tests.test_o2_taps import _op_row

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        te_deploy_id = "te-warn-1"
        store.create_deploy(
            deploy_id=te_deploy_id, scenario_id="warn-scn", scenario_version=1,
            scenario_schema_version=1, plan_id="te-warn-1-plan", ir_hash="h1",
            placement={"h-a": "pxtest1", "h-b": "pxtest1"}, tier="te",
        )
        store.insert_ops(te_deploy_id, [
            _op_row(te_deploy_id, "op-clone-a", "clone-vm", "OP-HOST-010",
                    {"host": "h-a"}, outputs={"vmid": 100001}),
            _op_row(te_deploy_id, "op-clone-b", "clone-vm", "OP-HOST-010",
                    {"host": "h-b"}, outputs={"vmid": 100002}),
        ])
        # A standing tfc deploy bound to te_deploy_id, still driving h-b.
        tfc_deploy_id = f"tfc-{te_deploy_id}"
        store.create_deploy(
            deploy_id=tfc_deploy_id, scenario_id=tfc_deploy_id, scenario_version=None,
            scenario_schema_version=None, plan_id=tfc_deploy_id, ir_hash="", placement={},
            tier="tfc",
        )
        store.insert_ops(tfc_deploy_id, [
            _op_row(tfc_deploy_id, "op-assign-b", "assign-role", "OP-TFC-020",
                    {"host": "h-b"}, inputs={"host_id": "h-b", "role": "workstation"}),
            _op_row(tfc_deploy_id, "op-drive-b", "drive-persona", "OP-TFC-010",
                    {"instance": "h-b-scripted"},
                    inputs={"host_id": "h-b", "persona_id": "scripted"}),
        ])

        # h-b's clone-vm op is still `completed` here -- `diff_plan` runs
        # BEFORE the removal sweep in the real `--update` flow (a row
        # marked `destroyed` is EXCLUDED from `removed` entirely, M2 code
        # review: that's what makes a re-diff after a clean sweep a no-op).
        # h-b is simply absent from the freshly-built plan below, which is
        # what makes diff_plan classify its op as removed.
        plan = Plan(
            plan_id="te-warn-1-plan", scenario_id="warn-scn", scenario_version=1,
            ir_hash="h2",
        )
        plan.add_op(_surviving_clone_vm("op-clone-a", "h-a"))  # h-a survives the update
        plan.freeze()
        cfg = AdapterConfig()
        logged: list[str] = []
        from axrange.planner.plandiff import diff_plan

        diff = diff_plan(plan, store.ops_for_deploy(te_deploy_id))
        assert diff.removed == {"op-clone-b"}  # ONLY h-b, not the surviving h-a

        apply_update_tail(
            store, cfg, te_deploy_id, plan, diff, log=lambda m: logged.append(m),
        )

        assert any(
            "tfc" in m and tfc_deploy_id in m and "h-b" in m for m in logged
        ), logged
    finally:
        store.close()


def test_apply_update_tail_no_warning_when_sibling_does_not_reference_removed_host(
    tmp_path,
):
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.api.service import apply_update_tail
    from axrange.planner.dag import Plan
    from axrange.planner.plandiff import diff_plan
    from tests.test_o2_taps import _op_row

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        te_deploy_id = "te-warn-2"
        store.create_deploy(
            deploy_id=te_deploy_id, scenario_id="warn-scn-2", scenario_version=1,
            scenario_schema_version=1, plan_id="te-warn-2-plan", ir_hash="h1",
            placement={"h-a": "pxtest1", "h-b": "pxtest1"}, tier="te",
        )
        store.insert_ops(te_deploy_id, [
            _op_row(te_deploy_id, "op-clone-a", "clone-vm", "OP-HOST-010",
                    {"host": "h-a"}, outputs={"vmid": 100001}),
            _op_row(te_deploy_id, "op-clone-b", "clone-vm", "OP-HOST-010",
                    {"host": "h-b"}, outputs={"vmid": 100002}),
        ])
        tfc_deploy_id = f"tfc-{te_deploy_id}"
        store.create_deploy(
            deploy_id=tfc_deploy_id, scenario_id=tfc_deploy_id, scenario_version=None,
            scenario_schema_version=None, plan_id=tfc_deploy_id, ir_hash="", placement={},
            tier="tfc",
        )
        # tfc only touches the SURVIVING host, h-a.
        store.insert_ops(tfc_deploy_id, [
            _op_row(tfc_deploy_id, "op-assign-a", "assign-role", "OP-TFC-020",
                    {"host": "h-a"}, inputs={"host_id": "h-a", "role": "workstation"}),
        ])
        # h-b's clone-vm op is still `completed` here -- diff_plan runs
        # BEFORE the removal sweep in the real --update flow (M2 code
        # review); it's absent from the freshly-built plan below, which is
        # what makes diff_plan classify its op as removed.

        plan = Plan(
            plan_id="te-warn-2-plan", scenario_id="warn-scn-2", scenario_version=1,
            ir_hash="h2",
        )
        plan.add_op(_surviving_clone_vm("op-clone-a", "h-a"))  # h-a survives the update
        plan.freeze()
        diff = diff_plan(plan, store.ops_for_deploy(te_deploy_id))
        assert diff.removed == {"op-clone-b"}
        logged: list[str] = []
        apply_update_tail(
            store, AdapterConfig(), te_deploy_id, plan, diff,
            log=lambda m: logged.append(m),
        )
        assert not any("tfc" in m for m in logged)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Fixture-gap regression (code review "Test weak spots"): every hand-
# authored scenario ABOVE this point is router-free, so the M1 class of bug
# ("configure-router's served_hosts roster hard-refuses every --update on a
# router-bearing scenario") is invisible to them. Every SHIPPED twin-config
# AND every target-environments/ archetype declares `kind: router` --
# measured 100% refusal before the fix. Without a test like this one, the M1
# class of regression could come back with the whole suite still green.
#
# Uses `target-environments/fabric-verify/scenario.yaml` directly (loaded +
# one host node appended, exactly like the hand-authored ADD_SCENARIO
# fixture above, but starting from REAL, shipped content) rather than a
# twin-config `--config`/`--param` `count:` bump: growing a twin-config's
# `count:`-fan-out node shifts every LATER-declared sibling's auto-assigned
# IP (a separate, pre-existing bug in `axrange/twinconfig/expand.py`'s
# ordinal IP allocation, found while building this test and OUT OF SCOPE
# for this work order -- see the hand-off notes) and would make this test
# fail for a reason that has nothing to do with M1. `fabric-verify` has no
# `script:` actions (only `apps: []`), so a scenario_dir-relative file
# dependency can't bite when the edited copy is written to `tmp_path`
# instead of alongside the archetype's own directory -- and its 5 VyOS
# routers (each carrying `routing:`, so each ALSO computes a `served_hosts`-
# shaped roster) make this a considerably harder proof than the minimum one
# router the review asked for.
# ---------------------------------------------------------------------------
_FABRIC_VERIFY_SCENARIO = (
    Path(__file__).parent.parent / "target-environments" / "fabric-verify" / "scenario.yaml"
)


def test_update_add_node_on_a_real_router_bearing_archetype_succeeds(
    tmp_path, fake_env, capsys,
):
    import yaml

    db = str(tmp_path / "state.db")
    raw_text = _FABRIC_VERIFY_SCENARIO.read_text()

    base_path = tmp_path / "base.yaml"
    base_path.write_text(raw_text)
    rc1 = climain.main([
        "te", "deploy", str(base_path), "--images", str(FIXT_ROOT),
        "--adapter-config", "/nonexistent.conf", "--node", "pxtest1", "--db", db,
    ])
    assert rc1 == 0, capsys.readouterr()
    capsys.readouterr()

    data = yaml.safe_load(raw_text)
    # A new host `hc`, on the SAME access segment `ha` already sits on
    # (`va`) -- shares a net with `leaf-1` (a router), the exact shape that
    # used to make `leaf-1`'s `served_hosts` roster change and hard-refuse.
    data["layers"]["infra"]["nodes"].append({
        "id": "hc", "kind": "host",
        "nets": [{"net": "va", "ip": "10.1.10.11"}],
        "image": "debian-12-cloud@v1", "requires": ["linux"],
        "resources": {"cpu": 1, "ram": "1Gi", "disk": "10Gi"},
    })
    add_path = tmp_path / "add.yaml"
    add_path.write_text(yaml.safe_dump(data, sort_keys=False))

    rc2 = climain.main([
        "te", "deploy", str(add_path), "--images", str(FIXT_ROOT),
        "--adapter-config", "/nonexistent.conf", "--node", "pxtest1", "--db", db,
        "--update",
    ])
    captured = capsys.readouterr()
    # THE regression this test pins: before the M1 fix, this refused with
    # "N op(s) mutated" (every fabric router sharing a net with the new host
    # had its configure-router's served_hosts roster change) on every
    # router-bearing scenario, 100% of the time.
    assert rc2 == 0, captured.err
    assert "+ host/hc" in captured.out
    assert "clone-vm[hc]" in captured.out

    store = StateStore(db)
    deploys = store.deploys_matching(scenario_id="fabric-verify")
    assert len(deploys) == 1  # same standing deploy, not a duplicate
    d = deploys[0]
    assert d.status.value == "completed"
    hc_op = _op_id(d.plan_id, "clone-vm", {"host": "hc"})
    row = store.get_op(d.deploy_id, hc_op)
    assert row is not None and row.status == OpStatus.COMPLETED
    # Every fabric router's OWN op is untouched (not redone) -- proof the
    # served_hosts roster change never reached inputs_resolved at all, on
    # EVERY router in this multi-router fabric, not just one.
    for router in ("spine-1", "spine-2", "leaf-1", "leaf-2", "border-1", "prov-1"):
        cr_op = _op_id(d.plan_id, "configure-router", {"host": router})
        cr_row = store.get_op(d.deploy_id, cr_op)
        assert cr_row is not None and cr_row.status == OpStatus.COMPLETED, router
    store.close()


# ===========================================================================
# `--reapply`: the content-only re-apply carve-out
# (`docs/WORK_ORDER_responder_op_row_drift.md` option A, Authority: user
# 2026-09-02). Same CLI path / real StateStore / fake adapter idiom as
# everything above.
#
# The scenario below carries a real `run-script` app so a `script_content`
# mutation is producible the way it happens in practice: the SCENARIO FILE
# is byte-identical across the update and only the referenced shell script
# changes on disk (`pass1_emit._read_script` reads it at emit time), so
# `ir_hash`/`plan_id` never move and the ONLY thing the diff sees is the
# op's own `script_content` input -- exactly the drift the work order
# describes 19 dev45 guests standing in.
# ===========================================================================
SCRIPT_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: reapply-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.30.0.0/24
        vlan: 30
        gateway: 10.30.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{{ net: corp, ip: 10.30.0.10 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: 1Gi, disk: 10Gi }}
  apps:
    - id: responder
      install: [{{ pkg: {pkg} }}, {{ script: ./{script} }}]
  activity: []
bindings: [{{ app: responder, host: h-a }}]
"""

# Deliberately longer than `api.service._REFUSAL_VALUE_MAX` (160): a real
# responder script is, and the elision-instead-of-dump behavior in the
# refusal message only engages past that threshold, so a short fixture would
# leave it untested. The `*-port-*` tokens are the sentinels the tests assert
# never appear in operator-facing output.
_SCRIPT_PAD = "# axrange test responder -- padding to exceed the refusal elision threshold\n" * 3
_OLD_SCRIPT = f"#!/bin/sh\n{_SCRIPT_PAD}systemctl stop responder || true\necho old-port-8080\n"
_NEW_SCRIPT = f"#!/bin/sh\n{_SCRIPT_PAD}systemctl stop responder || true\necho new-port-9090\n"


def _script_scenario(*, pkg: str = "nginx", script: str = "setup.sh") -> str:
    return SCRIPT_SCENARIO.format(pkg=pkg, script=script)


# SCRIPT_SCENARIO + a new host `h-new` on the SAME already-existing `corp`
# network -- the "add a node" half of the combined add+reapply test below,
# mirroring `ADD_SCENARIO`'s own shape (§ "Criterion 2" above) so that half
# is proven to trigger NO barrier/aggregate reevaluation of its own
# (`test_update_no_barrier_reeval_call_when_nothing_needs_it`'s finding for
# a plain host add onto an existing network).
SCRIPT_ADD_SCENARIO = """
scenario_schema_version: 1
scenario:
  id: reapply-demo
  version: 1
layers:
  infra:
    networks:
      - id: corp
        cidr: 10.30.0.0/24
        vlan: 30
        gateway: 10.30.0.1
    nodes:
      - id: h-a
        kind: host
        nets: [{{ net: corp, ip: 10.30.0.10 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: 1Gi, disk: 10Gi }}
      - id: h-new
        kind: host
        nets: [{{ net: corp, ip: 10.30.0.11 }}]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {{ cpu: 1, ram: 1Gi, disk: 10Gi }}
  apps:
    - id: responder
      install: [{{ pkg: {pkg} }}, {{ script: ./{script} }}]
  activity: []
bindings: [{{ app: responder, host: h-a }}]
"""


def _script_add_scenario(*, pkg: str = "nginx", script: str = "setup.sh") -> str:
    return SCRIPT_ADD_SCENARIO.format(pkg=pkg, script=script)


def _deploy_script_base(
    tmp_path, db, *, content: str = _OLD_SCRIPT, pkg: str = "nginx",
    script: str = "setup.sh",
) -> tuple[str, str]:
    """Deploy the script-bearing scenario once. Returns (deploy_id,
    scenario_path) -- the SAME scenario path is reused for the update, so a
    later edit to `<script>` alone is a genuinely content-only change."""
    (tmp_path / script).write_text(content)
    scn = _write(tmp_path, "script-base.yaml", _script_scenario(pkg=pkg, script=script))
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db))
    assert rc == 0
    store = StateStore(db)
    did = store.latest_deploys(limit=1)[0].deploy_id
    store.close()
    return did, scn


def _script_op_ids(db: str, deploy_id: str) -> tuple[str, str, str]:
    """(run-script, provision-app, clone-vm) op ids for the fixture."""
    store = StateStore(db)
    plan_id = store.get_deploy(deploy_id).plan_id
    store.close()
    scope = {"app": "responder", "host": "h-a"}
    return (
        _op_id(plan_id, "run-script", scope),
        _op_id(plan_id, "provision-app", scope),
        _op_id(plan_id, "clone-vm", {"host": "h-a"}),
    )


def _fresh_fakes(monkeypatch):
    """A FRESH fake adapter for a second `cmd_deploy` call, so `.calls`
    records ONLY what that run issued (the idiom
    `test_update_additive_edit_makes_zero_adapter_calls_for_unchanged_ops`
    established)."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod

    fake = _NamingAwareFakeAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fake)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: fake)
    return fake


def test_reapply_reexecutes_only_the_script_op_and_persists_the_new_content(
    tmp_path, fake_env, capsys, monkeypatch,
):
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, prov_op, clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = {
        row.op_id: (row.status, row.started_at, row.completed_at, row.outputs_json)
        for row in store.ops_for_deploy(deploy_id)
    }
    assert json.loads(
        store.get_op(deploy_id, script_op).inputs_resolved_json
    )["script_content"] == _OLD_SCRIPT
    store.close()

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    fresh = _fresh_fakes(monkeypatch)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    )
    assert rc == 0
    out = capsys.readouterr().out

    # The plan is disclosed before anything runs: op label, host grouping,
    # old->new FINGERPRINTS -- and never the script text itself.
    assert "Re-applying (--reapply): 1 op(s)" in out
    assert "↺ host/h-a" in out
    assert "run-script[responder@h-a]" in out
    assert out.count("sha256:") >= 2
    assert "old-port-8080" not in out and "new-port-9090" not in out

    # Exactly ONE adapter/config call this run, and it is the script op.
    assert [name for name, _inputs in fresh.calls] == ["run-script"]
    assert fresh.calls[0][1]["script_content"] == _NEW_SCRIPT

    store = StateStore(db)
    after_rows = {row.op_id: row for row in store.ops_for_deploy(deploy_id)}
    # The re-applied row: completed again, now carrying the NEW content.
    reapplied = after_rows[script_op]
    assert reapplied.status == OpStatus.COMPLETED
    assert json.loads(reapplied.inputs_resolved_json)["script_content"] == _NEW_SCRIPT
    # Every OTHER pre-existing row is byte-identical -- nothing else redone.
    for op_id, snapshot in before.items():
        if op_id == script_op:
            continue
        row = after_rows[op_id]
        assert (row.status, row.started_at, row.completed_at, row.outputs_json) == snapshot, op_id
    assert prov_op in before and clone_op in before  # the neighbours really were checked
    store.close()


def test_script_content_mutation_without_reapply_still_refuses(
    tmp_path, fake_env, capsys,
):
    """The pre-existing hard refusal is unchanged without the opt-in flag --
    `--reapply` adds a path, it does not soften the default."""
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = store.get_op(deploy_id, script_op)
    store.close()

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db, update=True))

    assert rc != 0
    err = capsys.readouterr().err
    assert "mutated" in err
    assert "run-script[responder@h-a]" in err
    assert "'script_content'" in err
    # Long values are fingerprinted, not dumped, in the refusal too.
    assert "old-port-8080" not in err and "new-port-9090" not in err

    store = StateStore(db)
    after = store.get_op(deploy_id, script_op)
    assert after.completed_at == before.completed_at
    assert json.loads(after.inputs_resolved_json)["script_content"] == _OLD_SCRIPT
    store.close()


def test_reapply_refuses_when_a_second_field_changed_too(
    tmp_path, fake_env, capsys,
):
    """Renaming the script file changes `script_name` alongside
    `script_content` -- two differing fields, so `--reapply` must refuse.
    The narrowness is the whole safety argument (work order §7.3)."""
    db = str(tmp_path / "state.db")
    deploy_id, _scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = store.get_op(deploy_id, script_op)
    store.close()

    (tmp_path / "boot.sh").write_text(_NEW_SCRIPT)
    renamed = _write(
        tmp_path, "script-base.yaml", _script_scenario(script="boot.sh")
    )
    rc = climain.cmd_deploy(
        _deploy_args(scenario=renamed, db=db, update=True, reapply=True)
    )

    assert rc != 0
    err = capsys.readouterr().err
    assert "run-script[responder@h-a]" in err
    assert "'script_content'" in err and "'script_name'" in err
    assert "not covered by --reapply" in err
    assert "script_name" in err.split("not covered by --reapply", 1)[1]

    store = StateStore(db)
    after = store.get_op(deploy_id, script_op)
    assert after.completed_at == before.completed_at  # nothing executed
    store.close()


def test_reapply_refuses_a_provision_app_pkg_change_naming_the_scope_decision(
    tmp_path, fake_env, capsys,
):
    """`provision-app` is the `pkg:` sibling with the same `redo_on`
    trigger and is DELIBERATELY out of scope for this slice -- the refusal
    must SAY so, not merely decline (work order §4 option A, "Against")."""
    db = str(tmp_path / "state.db")
    deploy_id, _scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    _script_op, prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = store.get_op(deploy_id, prov_op)
    store.close()

    swapped = _write(tmp_path, "script-base.yaml", _script_scenario(pkg="htop"))
    rc = climain.cmd_deploy(
        _deploy_args(scenario=swapped, db=db, update=True, reapply=True)
    )

    assert rc != 0
    err = capsys.readouterr().err
    assert "provision-app[responder@h-a]" in err
    assert "not covered by --reapply" in err
    assert "out of scope for this slice" in err
    assert "run-script" in err  # names what IS covered

    store = StateStore(db)
    after = store.get_op(deploy_id, prov_op)
    assert after.completed_at == before.completed_at
    store.close()


def test_second_update_after_a_successful_reapply_is_a_clean_noop(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """§7.2 / `docs/WORK_ORDER_twin_incremental_update.md` §6(c): once the
    re-apply has persisted the new content, the op classifies Unchanged --
    the drift is genuinely closed, not merely papered over for one run."""
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    assert climain.cmd_deploy(
        _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    ) == 0
    capsys.readouterr()

    store = StateStore(db)
    after_first = {
        row.op_id: (row.status, row.started_at, row.completed_at, row.outputs_json)
        for row in store.ops_for_deploy(deploy_id)
    }
    store.close()

    # A second --update, WITHOUT --reapply (nothing left to re-apply) and
    # with a fresh adapter: no refusal, no adapter call, nothing changed.
    fresh = _fresh_fakes(monkeypatch)
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing to add or remove" in out
    assert fresh.calls == []

    store = StateStore(db)
    for row in store.ops_for_deploy(deploy_id):
        assert (
            row.status, row.started_at, row.completed_at, row.outputs_json
        ) == after_first[row.op_id], row.op_id
    assert json.loads(
        store.get_op(deploy_id, script_op).inputs_resolved_json
    )["script_content"] == _NEW_SCRIPT
    store.close()


def test_failed_reapply_exits_nonzero_and_leaves_the_op_retryable(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """LES-181 (exit-code ownership): a re-apply that fails in the guest
    must surface as a non-zero exit AND leave a row a re-run retries --
    never `completed` carrying content that was never delivered."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from axrange.planner import executor as executor_mod
    from axrange.planner.executor import AdapterError, RetryConfig

    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    class _ScriptFails(_NamingAwareFakeAdapter):
        def run(self, op, resolved_inputs, ctx):
            if op.op_name == "run-script":
                self.calls.append((op.op_name, dict(resolved_inputs)))
                raise AdapterError("run-script: boom", error_class="script-failed")
            return super().run(op, resolved_inputs, ctx)

    failing = _ScriptFails()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: failing)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: failing)
    # One attempt, no backoff -- the executor reads this module global at
    # construction time (`self.retry = retry or RetryConfig()`), so the
    # default 3-attempt/5s-backoff policy would otherwise put ~15s of real
    # `time.sleep` in this test for no added coverage.
    monkeypatch.setattr(
        executor_mod, "RetryConfig",
        lambda: RetryConfig(max_attempts=1, initial_backoff_s=0.0, cap_s=0.0,
                            jitter_pct=0.0),
    )

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    )
    assert rc != 0
    assert [name for name, _i in failing.calls] == ["run-script"]

    store = StateStore(db)
    row = store.get_op(deploy_id, script_op)
    assert row.status != OpStatus.COMPLETED  # retryable, not falsely green
    store.close()

    # A retry (still --reapply) against a working backend now succeeds, and
    # the row lands on the new content -- proving "retryable" is real, not
    # just a status string.
    _fresh_fakes(monkeypatch)
    assert climain.cmd_deploy(
        _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    ) == 0
    store = StateStore(db)
    row = store.get_op(deploy_id, script_op)
    assert row.status == OpStatus.COMPLETED
    assert json.loads(row.inputs_resolved_json)["script_content"] == _NEW_SCRIPT
    store.close()


def test_reapply_without_update_is_refused(tmp_path, fake_env, capsys):
    """Same S5 precedent as `--allow-destroy`/`--yes`: a flag that only
    means something WITH --update is refused, never silently ignored."""
    db = str(tmp_path / "state.db")
    scn = _write(tmp_path, "base.yaml", _base_scenario())
    rc = climain.cmd_deploy(_deploy_args(scenario=scn, db=db, reapply=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "--reapply" in err
    assert "--update" in err
    store = StateStore(db)
    assert store.latest_deploys(limit=1) == []  # refused before touching state
    store.close()


def test_reapply_dry_run_previews_the_plan_and_touches_nothing(
    tmp_path, fake_env, capsys,
):
    """`--dry-run --update --reapply` is the "what would this redo?"
    preview -- it must show the ↺ section and execute nothing."""
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = store.get_op(deploy_id, script_op)
    store.close()

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    args = _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    args.dry_run = True
    assert climain.cmd_deploy(args) == 0
    out = capsys.readouterr().out
    assert "Re-applying (--reapply): 1 op(s)" in out

    store = StateStore(db)
    after = store.get_op(deploy_id, script_op)
    assert after.status == OpStatus.COMPLETED
    assert after.completed_at == before.completed_at
    assert json.loads(after.inputs_resolved_json)["script_content"] == _OLD_SCRIPT
    store.close()


# ---------------------------------------------------------------------------
# LES-185: validate the reapply predicate against a REAL shipped archetype,
# not only the hand-written `SCRIPT_SCENARIO` fixture above -- a synthetic
# fixture designed to exercise "script content changed" cannot also prove
# "on content shaped like what we actually ship" unless it deliberately
# mirrors that shape. `dfir-hunt-lab` is the smallest of the eight shipped
# `target-environments/*/scenario.yaml` archetypes that use a `{script:}`
# install action -- and, like every one of them, it ALSO carries a `kind:
# router` node and a Windows host, so this incidentally re-covers the exact
# multi-node/router shape LES-185 itself was born from (`docs/WORK_ORDER_
# twin_incremental_update.md`'s `served_hosts` regression), now for
# `--reapply` instead of plain `--update`.
# ---------------------------------------------------------------------------
def test_reapply_on_a_real_shipped_archetype_reapplies_only_the_edited_script(
    tmp_path, fake_env, capsys, monkeypatch,
):
    import shutil

    src_dir = Path(__file__).parent.parent / "target-environments" / "dfir-hunt-lab"
    dst_dir = tmp_path / "dfir-hunt-lab"
    shutil.copytree(src_dir, dst_dir)
    scn = str(dst_dir / "scenario.yaml")

    rc1 = climain.cmd_deploy(_deploy_args(scenario=scn, db=str(tmp_path / "state.db")))
    assert rc1 == 0, capsys.readouterr()
    capsys.readouterr()
    db = str(tmp_path / "state.db")

    store = StateStore(db)
    deploys = store.deploys_matching(scenario_id="dfir-hunt-lab")
    assert len(deploys) == 1
    plan_id = deploys[0].plan_id
    deploy_id = deploys[0].deploy_id
    victim_op = _op_id(plan_id, "run-script", {"app": "victim-artifacts", "host": "victim"})
    other_ops = [
        _op_id(plan_id, "run-script", {"app": "dfir-kit", "host": "analyst"}),
        _op_id(plan_id, "run-script", {"app": "hunt-stack", "host": "hunt"}),
        _op_id(plan_id, "configure-router", {"host": "gw"}),
        _op_id(plan_id, "clone-vm", {"host": "victim"}),
        _op_id(plan_id, "clone-vm", {"host": "workstation"}),
    ]
    before = {
        op_id: (
            row.status, row.started_at, row.completed_at, row.outputs_json,
        )
        for op_id in other_ops
        if (row := store.get_op(deploy_id, op_id)) is not None
    }
    assert len(before) == len(other_ops)  # every comparison op really exists
    old_content = store.get_op(deploy_id, victim_op)
    assert old_content is not None
    old_script = json.loads(old_content.inputs_resolved_json)["script_content"]
    store.close()

    # Content-only edit: the scenario.yaml tree is otherwise untouched, only
    # the referenced script file changes on disk -- exactly the out-of-band
    # drift shape §1 of the work order describes.
    script_path = dst_dir / "scripts" / "victim_artifacts.sh"
    script_path.write_text(old_script + "\necho '[axr-test] reapplied'\n")

    fresh = _fresh_fakes(monkeypatch)
    rc2 = climain.cmd_deploy(
        _deploy_args(scenario=scn, db=db, update=True, reapply=True)
    )
    out2 = capsys.readouterr().out
    assert rc2 == 0, out2
    assert "run-script[victim-artifacts@victim]" in out2

    # Exactly the one edited op reached the (fresh, per-call) fake adapter --
    # neither sibling `run-script`, the router, nor either clone-vm redone.
    assert [name for name, _inputs in fresh.calls] == ["run-script"]

    store = StateStore(db)
    for op_id, snapshot in before.items():
        row = store.get_op(deploy_id, op_id)
        assert (row.status, row.started_at, row.completed_at, row.outputs_json) == snapshot, op_id
    after_victim = store.get_op(deploy_id, victim_op)
    assert after_victim.status == OpStatus.COMPLETED
    new_script = json.loads(after_victim.inputs_resolved_json)["script_content"]
    assert new_script != old_script
    assert "[axr-test] reapplied" in new_script
    store.close()


def test_reapply_prompts_and_aborts_on_a_non_y_answer(tmp_path, fake_env, capsys):
    """A re-apply re-runs NON-idempotent operator shell, so it goes through
    the same confirmation gate a removal does -- answering anything but `y`
    executes nothing."""
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    before = store.get_op(deploy_id, script_op)
    store.close()

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    args = _deploy_args(scenario=scn, db=db, update=True, reapply=True, yes=False)
    rc = climain.cmd_deploy(args, confirm_fn=lambda _p: "n")

    assert rc == 1
    assert "Aborted; nothing executed." in capsys.readouterr().out
    store = StateStore(db)
    after = store.get_op(deploy_id, script_op)
    assert after.completed_at == before.completed_at
    assert json.loads(after.inputs_resolved_json)["script_content"] == _OLD_SCRIPT
    store.close()


def test_reapply_combined_with_a_node_addition_in_one_invocation(
    tmp_path, fake_env, capsys, monkeypatch,
):
    """`--update --reapply` covering BOTH an Added node and a content-only
    Mutated `run-script` in the SAME invocation -- the two mechanisms
    (`diff.added`'s ordinary pending-op path vs `reapply_ops`'s
    force-reset, `api.service.resolve_deploy_plan`/`cmd_deploy`) must not
    collide or leak into each other. `h-new` lands on the SAME
    already-existing `corp` network `h-a` is already on (mirroring
    `ADD_SCENARIO`/`test_update_no_barrier_reeval_call_when_nothing_needs_
    it`'s "plain host add onto an existing network triggers no barrier/
    aggregate reeval" finding), so the single `force_reset_ops` call this
    run makes is attributable ONLY to the reapply -- proving the added
    node's ops run through the ordinary path, not through force-reset."""
    db = str(tmp_path / "state.db")
    deploy_id, scn = _deploy_script_base(tmp_path, db)
    capsys.readouterr()
    script_op, _prov_op, _clone_op = _script_op_ids(db, deploy_id)

    store = StateStore(db)
    plan_id = store.get_deploy(deploy_id).plan_id
    store.close()
    added_clone_op = _op_id(plan_id, "clone-vm", {"host": "h-new"})
    assert added_clone_op != script_op  # the two sets can't collide by id

    from axrange.planner.state import StateStore as _SS

    force_reset_calls: list[tuple[str, set]] = []
    orig_force_reset = _SS.force_reset_ops

    def _spy(self, deploy_id_, op_ids):
        force_reset_calls.append((deploy_id_, set(op_ids)))
        return orig_force_reset(self, deploy_id_, op_ids)

    monkeypatch.setattr(_SS, "force_reset_ops", _spy)

    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)
    scn2 = _write(tmp_path, "script-add.yaml", _script_add_scenario())
    fresh = _fresh_fakes(monkeypatch)
    rc = climain.cmd_deploy(
        _deploy_args(scenario=scn2, db=db, update=True, reapply=True)
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "+ host/h-new" in out
    assert "Re-applying (--reapply): 1 op(s)" in out

    # Both mechanisms actually ran this one invocation: the added host's
    # clone-vm, and the re-applied script carrying its NEW content.
    called = [name for name, _inputs in fresh.calls]
    assert "clone-vm" in called
    run_script_calls = [i for n, i in fresh.calls if n == "run-script"]
    assert len(run_script_calls) == 1
    assert run_script_calls[0]["script_content"] == _NEW_SCRIPT
    # No aggregate op (e.g. `apply-sdn-config`, Unchanged here -- `corp`
    # already existed) was swept up by either mechanism.
    assert "apply-sdn-config" not in called

    store = StateStore(db)
    added_row = store.get_op(deploy_id, added_clone_op)
    assert added_row is not None and added_row.status == OpStatus.COMPLETED
    script_row = store.get_op(deploy_id, script_op)
    assert script_row.status == OpStatus.COMPLETED
    assert json.loads(script_row.inputs_resolved_json)["script_content"] == _NEW_SCRIPT
    store.close()

    # DISJOINT sets, and no barrier/aggregate reeval piggybacked on the
    # reapply: the run's one `force_reset_ops` call carries EXACTLY the
    # reapplied op -- never the added node's (brand-new rows need no
    # force-reset at all) and nothing else.
    assert force_reset_calls == [(deploy_id, {script_op})]
