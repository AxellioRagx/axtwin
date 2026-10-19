"""Unit tests for the planner DAG (passes 1-4 + validation).

Uses the slice fixture (2 Linux hosts on one network -- capture is now a
standalone tap-plan concern against the deployed twin's published topology,
`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28, not
authored in the IR) plus the existing image manifest/binding fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from axrange.ir.parser import parse_ir
from axrange.ops.registry import CleanupStrategy, IdempotencyClass
from axrange.planner import PlanBuildError, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import PlanValidationError, _op_id


FIXTURES = Path(__file__).parent / "fixtures"
SLICE_SCN = FIXTURES / "ir" / "valid_slice" / "scenario.yaml"


@pytest.fixture
def slice_plan(manifests):
    parsed = parse_ir(path=str(SLICE_SCN))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    return build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)


# ---------------------------------------------------------------------------
# Build + shape
# ---------------------------------------------------------------------------


def test_slice_plan_builds(slice_plan):
    assert slice_plan.plan_id.startswith("slice-demo-")
    assert slice_plan.scenario_id == "slice-demo"
    assert slice_plan.scenario_version == 1
    assert slice_plan.placement == {"h-a": "pxtest1", "h-b": "pxtest1"}


def test_slice_op_counts(slice_plan):
    """The slice emits the base op DAG (ADAPTER.md §6) PLUS the A3 Stage A
    default-agent sweep, ON BY DEFAULT since 2026-07-11 (no
    scenario.default_agents: flag needed) -- 2 hosts x (install-defense-agent
    + install-agent[exporter]) = 4 more ops. Full-topology tap eligibility
    (2026-07-28, docs/WORK_ORDER_tap_eligibility.md D1/D4): the te records NO
    tap/mirror-related state of its own at all any more -- no
    record-tap-intent (retired along with attach-gre-tunnel / install-ovs-
    mirror / verify-capture before it) and no mirror-ready barrier. Capture
    is entirely the A2 tap playout's job, against an already-deployed te's
    published `hosts[].nets[]` topology."""
    # Action/wait ops:
    #   OP-NET (4):  create-ovs-bridge[scenario] (VLAN underlay — slice fixture
    #                declares a VLAN), create-sdn-zone, create-vnet[corp],
    #                apply-sdn-config
    #   OP-HOST per host (7): clone, hw, ci, attach@corp, start, wait-ga, wait-os
    #   OP-OVL default-agent sweep per host (2): install-defense-agent (wazuh),
    #                install-agent (exporter) -- A3 Stage A, default-on
    #   Barriers (3): network-ready[corp], host-ready[h-a], host-ready[h-b]
    by_name: dict[str, int] = {}
    for op in slice_plan.ops.values():
        by_name[op.op_name] = by_name.get(op.op_name, 0) + 1
    for b in slice_plan.barriers.values():
        by_name[b.barrier_name] = by_name.get(b.barrier_name, 0) + 1

    assert by_name == {
        # network-side: 4
        "create-sdn-zone": 1,
        "create-ovs-bridge": 1,
        "create-vnet": 1,
        "apply-sdn-config": 1,
        # host-side x2: 7 ops per host = 14
        "clone-vm": 2,
        "configure-vm-hardware": 2,
        "configure-cloud-init": 2,
        "attach-vm-to-vnet": 2,
        "start-vm": 2,
        "wait-guest-agent": 2,
        "wait-os-ready": 2,
        # default-agent sweep x2: 4 (A3 Stage A, default-on 2026-07-11)
        "install-defense-agent": 2,
        "install-agent": 2,
        # barriers: 3
        "network-ready": 1,
        "host-ready": 2,
    }


def test_configure_cloud_init_threads_explicit_binding_password(manifests):
    """docs/WORK_ORDER_access_facts.md Part A: a binding's `cloud_init.password`
    extra field (ManifestBase's extra="allow" -- not a declared IMAGE_MANIFEST
    §3.5 field, so no schema bump) threads into `configure-cloud-init`'s
    resolved inputs as `password`, for the adapter's
    `inp.get("password") or cfg.default_password` fallback to read."""
    parsed = parse_ir(path=str(SLICE_SCN))
    found = False
    for binding_doc in manifests.bindings.values():
        for v in binding_doc.versions:
            if v.cloud_init is not None:
                v.cloud_init.password = "fixture-binding-pw"
                found = True
    assert found, "expected at least one binding with a cloud_init block"

    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    ci_ops = [op for op in plan.ops.values() if op.op_name == "configure-cloud-init"]
    assert ci_ops
    for op in ci_ops:
        assert op.inputs_resolved.get("password") == "fixture-binding-pw"


def test_configure_cloud_init_password_defaults_to_none_when_binding_unset(slice_plan):
    """No `cloud_init.password` on the binding -> `password: None` in the
    resolved inputs (the adapter fills the deploy-wide default at execute
    time, exactly like `ci_user`'s existing None-fallback)."""
    ci_ops = [op for op in slice_plan.ops.values() if op.op_name == "configure-cloud-init"]
    assert ci_ops
    for op in ci_ops:
        assert op.inputs_resolved.get("password") is None


def test_topological_order_is_deterministic(slice_plan):
    a = slice_plan.topological_order()
    b = slice_plan.topological_order()
    assert a == b


# ---------------------------------------------------------------------------
# Edge wiring (does the slice DAG order things the way the catalog says?)
# ---------------------------------------------------------------------------


def _ids(plan):
    """Return {op_name+scope -> op_id} for compact lookups."""
    out: dict[str, str] = {}
    for op in plan.ops.values():
        scope = "+".join(f"{k}={v}" for k, v in sorted(op.scope.items()))
        out[f"{op.op_name}|{scope}"] = op.op_id
    for b in plan.barriers.values():
        scope = "+".join(f"{k}={v}" for k, v in sorted(b.scope.items()))
        out[f"{b.barrier_name}|{scope}"] = b.op_id
    return out


def test_network_chain_ordering(slice_plan):
    """SDN-native (2026-06-03): create-ovs-bridge -> create-sdn-zone ->
    create-vnet -> apply -> network-ready. The slice fixture declares a VLAN,
    so the OVS bridge is the single per-zone underlay (scenario-scoped) and is
    created before the VLAN zone that references it."""
    i = _ids(slice_plan)
    order = slice_plan.topological_order()
    pos = {oid: idx for idx, oid in enumerate(order)}

    assert pos[i["create-ovs-bridge|scenario=slice-demo"]] < pos[
        i["create-sdn-zone|scenario=slice-demo"]
    ]
    assert pos[i["create-sdn-zone|scenario=slice-demo"]] < pos[
        i["create-vnet|net=corp"]
    ]
    assert pos[i["create-vnet|net=corp"]] < pos[
        i["apply-sdn-config|scenario=slice-demo"]
    ]
    assert pos[i["apply-sdn-config|scenario=slice-demo"]] < pos[
        i["network-ready|net=corp"]
    ]


def test_host_chain_ordering(slice_plan):
    """clone -> hw -> ci -> attach -> start -> wait-ga -> wait-os -> host-ready"""
    i = _ids(slice_plan)
    order = slice_plan.topological_order()
    pos = {oid: idx for idx, oid in enumerate(order)}

    chain = [
        ("clone-vm", "host=h-a"),
        ("configure-vm-hardware", "host=h-a"),
        ("configure-cloud-init", "host=h-a"),
        ("attach-vm-to-vnet", "host=h-a+net=corp"),
        ("start-vm", "host=h-a"),
        ("wait-guest-agent", "host=h-a"),
        ("wait-os-ready", "host=h-a"),
        ("host-ready", "host=h-a"),
    ]
    for (a_n, a_s), (b_n, b_s) in zip(chain, chain[1:]):
        assert pos[i[f"{a_n}|{a_s}"]] < pos[i[f"{b_n}|{b_s}"]], (
            f"{a_n}[{a_s}] must precede {b_n}[{b_s}]"
        )


def test_tapped_start_vm_not_gated_on_mirror_ops(slice_plan):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    the te plan carries NO install-ovs-mirror/attach-gre-tunnel at all (A2
    composes those separately, against an already-deployed te) -- start-vm's
    conditional mirror predecessors never apply here any more."""
    op_names = {op.op_name for op in slice_plan.ops.values()}
    assert "install-ovs-mirror" not in op_names
    assert "attach-gre-tunnel" not in op_names
    assert "verify-capture" not in op_names


def test_attach_vm_to_vnet_joins_on_network_ready(slice_plan):
    """attach-vm-to-vnet depends on network-ready[$net] (catalog after:)."""
    i = _ids(slice_plan)
    op_id = i["attach-vm-to-vnet|host=h-a+net=corp"]
    preds = slice_plan.predecessors(op_id)
    assert i["network-ready|net=corp"] in preds
    assert i["configure-cloud-init|host=h-a"] in preds


def test_mirror_ready_barrier_not_emitted_by_te(slice_plan):
    """mirror-ready is NOT emitted by the te planner any more (2026-07-13,
    docs/WORK_ORDER_sensor_agnostic_te.md) -- its predecessors (attach-gre-
    tunnel/install-ovs-mirror/verify-capture, and as of 2026-07-28's
    full-topology tap eligibility, record-tap-intent too, `docs/
    WORK_ORDER_tap_eligibility.md` D4) are never emitted here either, so a
    te-side instance would be permanently vacuous."""
    barrier_names = {b.barrier_name for b in slice_plan.barriers.values()}
    assert "mirror-ready" not in barrier_names


# `test_record_tap_intent_emitted_per_tapped_host` REMOVED (`docs/
# WORK_ORDER_tap_eligibility.md` D4, Authority: user, 2026-07-28) --
# `record-tap-intent` (OP-MIR-005) is retired; the te emits no tap/mirror
# op of any kind (see `test_slice_op_counts` above).


def test_clone_vm_does_not_gate_on_network_ready(slice_plan):
    """OP_CATALOG §5.2 parallelism point: clone-vm overlaps SDN apply."""
    i = _ids(slice_plan)
    clone_id = i["clone-vm|host=h-a"]
    preds = slice_plan.predecessors(clone_id)
    assert preds == frozenset(), (
        f"clone-vm should be a root op; got preds={preds}"
    )


# ---------------------------------------------------------------------------
# Slice-gating: out-of-scope IR is rejected
# ---------------------------------------------------------------------------


def test_router_node_emits_configure_router(manifests):
    """Router slice Phase 2 — a kind:router node is no longer rejected; it emits
    the host-ops + a WAN NIC attach (uplink bridge) + configure-router."""
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: routed, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks:\n"
            "      - { id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }\n"
            "      - { id: dmz, cidr: 10.0.1.0/24, vlan: 11, gateway: 10.0.1.1 }\n"
            "    nodes:\n"
            "      - id: r-edge\n"
            "        kind: router\n"
            "        nets: [{ net: corp, ip: 10.0.0.1 }, { net: dmz, ip: 10.0.1.1 }]\n"
            "        image: debian-12-cloud@v1\n"
            "        requires: [linux]\n"
            "        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n"
            "  apps: []\n"
            "  activity: []\n"
            "bindings: []\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    names = [o.op_name for o in plan.ops.values()]
    assert "configure-router" in names
    # A WAN NIC is attached to the uplink bridge (multi-homed router + WAN).
    wan = [o for o in plan.ops.values()
           if o.op_name == "attach-vm-to-vnet" and o.scope.get("net") == "__wan__"]
    assert len(wan) == 1
    assert wan[0].inputs_resolved["vnet_id"] == "vmbr0"
    assert wan[0].inputs_resolved["nic_index"] == 2  # after the 2 LAN NICs


def test_router_gates_served_hosts_provisioning(manifests):
    """A host behind a router must provision (apt over NAT) only after
    configure-router brings up forwarding+masquerade — pass-3 gates its
    provision-app/run-script on configure-router (CHANGELOG 2026-06-24)."""
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: routed-prov, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks:\n"
            "      - { id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }\n"
            "    nodes:\n"
            "      - { id: gw, kind: router, nets: [{ net: corp, ip: 10.0.0.1 }],\n"
            "          image: debian-12-cloud@v1, requires: [linux] }\n"
            "      - { id: h-a, kind: host, nets: [{ net: corp, ip: 10.0.0.10 }],\n"
            "          image: ubuntu-server-22@v5, requires: [linux] }\n"
            "  apps:\n"
            "    - { id: web, install: [{ pkg: nginx }] }\n"
            "  activity: []\n"
            "bindings:\n"
            "  - { app: web, host: h-a }\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    cr = next(o for o in plan.ops.values() if o.op_name == "configure-router")
    # `served_hosts` is planner-internal bookkeeping (docs/
    # WORK_ORDER_twin_incremental_update.md M1) -- it lives in
    # `planner_hints`, NEVER `inputs_resolved` (the only dict `state.
    # op_to_row` persists), so an `--update`'s plandiff never sees it as a
    # spurious Mutated field on an unrelated add/remove.
    assert cr.planner_hints["served_hosts"] == ["h-a"]  # not the router itself
    assert "served_hosts" not in cr.inputs_resolved
    pa = next(o for o in plan.ops.values()
              if o.op_name == "provision-app" and o.scope.get("host") == "h-a")
    assert cr.op_id in set(plan.predecessors(pa.op_id))


def test_router_gates_its_own_in_guest_script(manifests, tmp_path):
    """The ROUTER ITSELF is gated on its own configure-router too (E1,
    docs/SLICE_ROUTER.md §9). `served_hosts` is deliberately "every NON-router
    node on my LAN nets", so a script on the router used to get no edge at all
    — and E1's egress-phase payload asserts the LAN→WAN masquerade that
    configure-router is the thing that installs."""
    (tmp_path / "install-egress-policy.sh").write_text("#!/bin/sh\necho egress\n")
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: routed-self, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks:\n"
            "      - { id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }\n"
            "    nodes:\n"
            "      - { id: gw, kind: router, nets: [{ net: corp, ip: 10.0.0.1 }],\n"
            "          image: debian-12-cloud@v1, requires: [linux] }\n"
            "  apps:\n"
            "    - { id: egress, install: [{ script: ./install-egress-policy.sh }] }\n"
            "  activity: []\n"
            "bindings:\n"
            "  - { app: egress, host: gw }\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(tmp_path)
    )
    cr = next(o for o in plan.ops.values() if o.op_name == "configure-router")
    assert cr.planner_hints["served_hosts"] == []  # the router is its own only host
    rs = next(o for o in plan.ops.values()
              if o.op_name == "run-script" and o.scope.get("host") == "gw")
    assert cr.op_id in set(plan.predecessors(rs.op_id))


def test_vyos_router_wan_first_and_ssh_set(manifests):
    """VyOS router (vyos-ssh-set): WAN is net0/eth0 (the template's baked DHCP
    mgmt iface), LANs shift to eth1..; configure-router carries router_os=vyos +
    ordered lan_ifaces; and NO configure-cloud-init is emitted (generic ISO has
    no cloud-init — config rides SSH)."""
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: vyrouted, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks:\n"
            "      - { id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }\n"
            "      - { id: dmz, cidr: 10.0.1.0/24, vlan: 11, gateway: 10.0.1.1 }\n"
            "    nodes:\n"
            "      - id: fw\n"
            "        kind: router\n"
            "        nets: [{ net: corp, ip: 10.0.0.1 }, { net: dmz, ip: 10.0.1.1 }]\n"
            "        image: vyos@1.5\n"
            "        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n"
            "  apps: []\n"
            "  activity: []\n"
            "bindings: []\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    ops = list(plan.ops.values())
    names = [o.op_name for o in ops]

    # No cloud-init op for VyOS.
    assert "configure-cloud-init" not in names

    # WAN attached at nic_index 0 (eth0); the two LAN NICs at 1 and 2.
    wan = [o for o in ops
           if o.op_name == "attach-vm-to-vnet" and o.scope.get("net") == "__wan__"]
    assert len(wan) == 1
    assert wan[0].inputs_resolved["nic_index"] == 0
    lan_idx = sorted(o.inputs_resolved["nic_index"] for o in ops
                     if o.op_name == "attach-vm-to-vnet"
                     and o.scope.get("net") in ("corp", "dmz"))
    assert lan_idx == [1, 2]

    # configure-router is vyos-flavored with ordered lan_ifaces (eth1, eth2).
    cr = [o for o in ops if o.op_name == "configure-router"]
    assert len(cr) == 1
    ri = cr[0].inputs_resolved
    assert ri["router_os"] == "vyos"
    assert ri["wan_eth"] == "eth0"
    assert ri["lan_ifaces"] == [
        {"eth": "eth1", "address": "10.0.0.1/24"},
        {"eth": "eth2", "address": "10.0.1.1/24"},
    ]


def test_slice_emits_provision_app(manifests):
    """Config slice: an app->host binding emits a config-class provision-app,
    scoped {app,host}, vmid sourced from the host's clone-vm, gated on
    host-ready (docs/SLICE_CONFIG.md)."""
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: with-apps, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks: [{ id: corp, cidr: 10.0.0.0/24, vlan: 10 }]\n"
            "    nodes:\n"
            "      - id: h-a\n"
            "        kind: host\n"
            "        nets: [{ net: corp, ip: 10.0.0.10 }]\n"
            "        image: debian-12-cloud@v1\n"
            "        requires: [linux]\n"
            "  apps: [{ id: web, install: [{pkg: nginx}] }]\n"
            "  activity: []\n"
            "bindings: [{ app: web, host: h-a }]\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)

    prov = [op for op in plan.ops.values() if op.op_name == "provision-app"]
    assert len(prov) == 1
    p = prov[0]
    assert p.scope == {"app": "web", "host": "h-a"}
    assert p.idempotency_class == IdempotencyClass.CONFIG
    assert p.inputs_resolved["install_actions"] == [{"pkg": "nginx"}]
    assert p.inputs_resolved["package_manager"] == "apt"

    # vmid wired from the host's clone-vm (dataflow).
    clone_id = _op_id(plan.plan_id, "clone-vm", {"host": "h-a"})
    assert p.inputs_from_outputs["vmid"] == (clone_id, "vmid")

    # gated after host-ready[h-a] (ordering edge from the catalog `after`).
    host_ready_id = _op_id(plan.plan_id, "host-ready", {"host": "h-a"})
    assert host_ready_id in plan.predecessors(p.op_id)


def _app_scenario(install: str, app_id: str = "web") -> str:
    return (
        "scenario_schema_version: 1\n"
        f"scenario: {{ id: cfg-{app_id}, version: 1 }}\n"
        "layers:\n"
        "  infra:\n"
        "    networks: [{ id: corp, cidr: 10.0.0.0/24, vlan: 10 }]\n"
        "    nodes:\n"
        "      - id: h-a\n"
        "        kind: host\n"
        "        nets: [{ net: corp, ip: 10.0.0.10 }]\n"
        "        image: debian-12-cloud@v1\n"
        "        requires: [linux]\n"
        f"  apps: [{{ id: {app_id}, install: {install} }}]\n"
        "  activity: []\n"
        f"bindings: [{{ app: {app_id}, host: h-a }}]\n"
    )


def test_slice_emits_run_script(manifests, tmp_path):
    """A {script:} action emits run-script (OP-APP-030), scoped {app,host},
    script content read at emit time, gated after host-ready AND the app's
    provision-app (docs/SLICE_CONFIG.md §11)."""
    (tmp_path / "setup.sh").write_text("#!/bin/sh\necho hi\n")
    parsed = parse_ir(text=_app_scenario("[{pkg: nginx}, {script: ./setup.sh}]"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(tmp_path)
    )
    prov = [op for op in plan.ops.values() if op.op_name == "provision-app"]
    run = [op for op in plan.ops.values() if op.op_name == "run-script"]
    assert len(prov) == 1 and len(run) == 1
    r = run[0]
    assert r.scope == {"app": "web", "host": "h-a"}
    assert r.idempotency_class == IdempotencyClass.CONFIG
    assert r.cleanup_strategy == CleanupStrategy.ESCALATE
    assert r.inputs_resolved["script_name"] == "web-setup.sh"
    assert "echo hi" in r.inputs_resolved["script_content"]

    clone_id = _op_id(plan.plan_id, "clone-vm", {"host": "h-a"})
    assert r.inputs_from_outputs["vmid"] == (clone_id, "vmid")
    preds = plan.predecessors(r.op_id)
    assert _op_id(plan.plan_id, "host-ready", {"host": "h-a"}) in preds
    assert prov[0].op_id in preds  # run-script after provision-app[$app@*]


def test_script_only_app_gates_on_host_ready(manifests, tmp_path):
    """A script-only app (no pkg) emits run-script but no provision-app; the
    `after provision-app[$app@*]` predecessor resolves to nothing and is dropped."""
    (tmp_path / "setup.sh").write_text("#!/bin/sh\n:\n")
    parsed = parse_ir(text=_app_scenario("[{script: ./setup.sh}]"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(tmp_path)
    )
    assert not [op for op in plan.ops.values() if op.op_name == "provision-app"]
    run = [op for op in plan.ops.values() if op.op_name == "run-script"]
    assert len(run) == 1
    assert _op_id(plan.plan_id, "host-ready", {"host": "h-a"}) in plan.predecessors(run[0].op_id)


def test_slice_rejects_multiple_scripts(manifests, tmp_path):
    (tmp_path / "a.sh").write_text("#!/bin/sh\n")
    (tmp_path / "b.sh").write_text("#!/bin/sh\n")
    parsed = parse_ir(text=_app_scenario("[{script: ./a.sh}, {script: ./b.sh}]"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    with pytest.raises(PlanBuildError) as exc:
        build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(tmp_path))
    assert "one script" in str(exc.value)


def test_script_without_scenario_dir_errors(manifests):
    parsed = parse_ir(text=_app_scenario("[{script: ./setup.sh}]"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    with pytest.raises(PlanBuildError) as exc:
        build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)  # no scenario_dir
    assert "scenario_dir" in str(exc.value)


def test_slice_rejects_unsupported_install_kind(manifests):
    """config / role / container kinds are deferred — rejected at emit time."""
    parsed = parse_ir(text=_app_scenario("[{config: /etc/foo.conf}]"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    with pytest.raises(PlanBuildError) as exc:
        build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    assert "config" in str(exc.value)


def test_slice_builds_with_no_sensor_identity_at_all(manifests):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md),
    reinforced by full-topology tap eligibility (2026-07-28, docs/
    WORK_ORDER_tap_eligibility.md D1/D4): the slice builds fine with NO
    sensor_transport_ip / sensor identity of any kind on the ClusterContext
    -- NO op in a te plan needs any (there is no more record-tap-intent to
    need one either). Tap->sensor binding is entirely the A2 tap playout's
    job, which runs against an already-deployed te, not at plan time."""
    parsed = parse_ir(path=str(SLICE_SCN))
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    op_names = {op.op_name for op in plan.ops.values()}
    assert "record-tap-intent" not in op_names
    assert "attach-gre-tunnel" not in op_names
    assert "install-ovs-mirror" not in op_names


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------


def test_topological_order_visits_every_node(slice_plan):
    order = slice_plan.topological_order()
    assert len(order) == len(slice_plan.ops) + len(slice_plan.barriers)


def test_descendants_of_clone_include_host_ready(slice_plan):
    i = _ids(slice_plan)
    desc = slice_plan.descendants(i["clone-vm|host=h-a"])
    assert i["host-ready|host=h-a"] in desc
    assert i["wait-os-ready|host=h-a"] in desc


def test_egress_emits_create_subnet_only_for_opted_in_net(manifests):
    """Router slice Phase 1 — a network with `egress: true` (+ gateway) emits
    create-subnet (SDN subnet+SNAT); a network without it does not."""
    parsed = parse_ir(
        text=(
            "scenario_schema_version: 1\n"
            "scenario: { id: egresstest, version: 1 }\n"
            "layers:\n"
            "  infra:\n"
            "    networks:\n"
            "      - { id: pub, cidr: 10.9.0.0/24, vlan: 10, gateway: 10.9.0.1, egress: true }\n"
            "      - { id: priv, cidr: 10.9.1.0/24, vlan: 11, gateway: 10.9.1.1 }\n"
            "    nodes:\n"
            "      - { id: h1, kind: host, nets: [{ net: pub, ip: 10.9.0.10 }], image: debian-12-cloud@v1, requires: [linux], resources: { cpu: 1, ram: 1Gi, disk: 10Gi } }\n"
            "      - { id: h2, kind: host, nets: [{ net: priv, ip: 10.9.1.10 }], image: debian-12-cloud@v1, requires: [linux], resources: { cpu: 1, ram: 1Gi, disk: 10Gi } }\n"
            "  apps: []\n"
            "  activity: []\n"
            "bindings: []\n"
        )
    )
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)

    subnets = [o for o in plan.ops.values() if o.op_name == "create-subnet"]
    assert len(subnets) == 1, "create-subnet only for the egress-opted-in net"
    s = subnets[0]
    assert s.scope.get("net") == "pub"
    assert s.inputs_resolved["gateway"] == "10.9.0.1"
    assert s.inputs_resolved["snat"] is True

    # apply-sdn-config must depend on create-subnet (committed in the apply).
    apply_ops = [o for o in plan.ops.values() if o.op_name == "apply-sdn-config"]
    assert apply_ops
    preds = plan.predecessors(apply_ops[0].op_id)
    pred_names = {plan.ops[p].op_name for p in preds if p in plan.ops}
    assert "create-subnet" in pred_names
