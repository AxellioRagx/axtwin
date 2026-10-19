"""Tests for the Proxmox adapter: naming + routing + REST/SSH dispatch
plumbing. Real REST + SSH calls are mocked at the client level — the slice
deploys against a live cluster for actual validation (ADAPTER.md §6).
"""
from __future__ import annotations

import pytest

from axrange.adapter_proxmox import (
    AdapterConfig,
    ConfigError,
    ExternalSensor,
    ProxmoxAdapter,
    load_config,
)
from axrange.adapter_proxmox.adapter import _PROBE_OPS, _REST_OPS, _SSH_OPS
from axrange.adapter_proxmox.naming import (
    bridge_name_for_net,
    exr_bridge_name_for_component,
    exr_vm_name_for_component,
    exr_vnet_id_for_component,
    exr_zone_id_for_component,
    gre_key_for_bridge,
    gre_key_for_bridge_sensor,
    gre_port_for_bridge,
    gre_port_for_bridge_sensor,
    img_vm_name_for_image,
    is_exr,
    is_exr_bridge,
    is_exr_vm_name,
    is_exr_vnet,
    is_exr_zone,
    is_img,
    is_img_vm_name,
    is_o2,
    is_o2_bridge,
    is_o2_vm_name,
    is_o2_vnet,
    is_o2_zone,
    is_protected_external,
    is_tap,
    is_tap_gretap,
    is_tap_mirror,
    is_te,
    is_te_bridge,
    is_te_vm_name,
    is_te_vmid,
    is_te_vnet,
    is_te_zone,
    is_tfc,
    is_tfc_bridge,
    is_tfc_vm_name,
    is_tfc_vnet,
    is_tfc_zone,
    mac_for_nic,
    mirror_name_for_vmid,
    o2_bridge_name_for_component,
    o2_vm_name_for_component,
    o2_vnet_id_for_component,
    tfc_bridge_name_for_component,
    tfc_vm_name_for_component,
    tfc_vnet_id_for_component,
    tfc_zone_id_for_component,
    underlay_bridge_for_plan,
    vmid_for_op,
    vm_name_for_host,
    vnet_id_for_net,
    zone_id_for_plan,
)
from axrange.adapter_proxmox.rest import (
    _OP_TO_METHOD,
    RestBackend,
    _normalize_pve_disk_size,
    _parse_mem_mb,
)
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext
from axrange.planner.cluster import ClusterContext
from axrange.ops.registry import IdempotencyClass, OpKind


# ---------------------------------------------------------------------------
# Naming determinism + constraints
# ---------------------------------------------------------------------------


def test_vmid_in_range():
    vmid = vmid_for_op("plan-123", "plan-123:clone-vm[h-fin]")
    assert 100000 <= vmid <= 999999


def test_vmid_is_deterministic_per_op_id():
    a = vmid_for_op("plan-A", "plan-A:clone-vm[h-fin]")
    b = vmid_for_op("plan-A", "plan-A:clone-vm[h-fin]")
    assert a == b


def test_vmid_differs_per_op_id():
    a = vmid_for_op("plan-A", "plan-A:clone-vm[h-fin]")
    b = vmid_for_op("plan-A", "plan-A:clone-vm[h-corp-dc]")
    assert a != b


def test_zone_id_length_cap():
    """Proxmox SDN zone names cap at 8 alphanum chars."""
    z = zone_id_for_plan("ransomware-hospital-abcdef12")
    assert len(z) <= 8
    assert z.isalnum()


def test_vnet_id_length_cap():
    v = vnet_id_for_net("a-very-long-net-id-that-overflows", plan_id="plan-123")
    assert len(v) <= 8
    assert v.isalnum()


# ---------------------------------------------------------------------------
# 2026-07-27 dev45 data-loss regression: `vnet_id_for_net` (and its `o2`/
# `tfc`/`exr` tier variants) MUST discriminate on `plan_id`, not just
# `net_id`. Before this fix two different twins with a same-named network
# (e.g. both declaring a `dmz` net) computed the IDENTICAL vnet id, so
# destroying one twin's throwaway deploy silently deleted the OTHER twin's
# live vnet (CLAUDE.md task narrative; a real dev45 outage). This is the
# test whose absence let the outage happen.
# ---------------------------------------------------------------------------
def test_vnet_id_for_net_is_plan_scoped_te():
    """Same `net_id`, different `plan_id` -> DIFFERENT `te`-tier vnet ids."""
    a = vnet_id_for_net("dmz", plan_id="dmz-perimeter-a2d0ba5b")
    b = vnet_id_for_net("dmz", plan_id="corp-enterprise-f525eed4")
    assert a != b
    assert is_te_vnet(a) and is_te_vnet(b)


@pytest.mark.parametrize("tier", ["o2", "tfc", "exr"])
def test_vnet_id_for_net_is_plan_scoped_all_tiers(tier):
    """Same regression, generalized to every tier variant
    (`o2_vnet_id_for_component`/`tfc_vnet_id_for_component`/
    `exr_vnet_id_for_component`) — leaving any of these three unfixed would
    have been half a fix (CLAUDE.md task instructions)."""
    a = vnet_id_for_net("dmz", plan_id="plan-A", tier=tier)
    b = vnet_id_for_net("dmz", plan_id="plan-B", tier=tier)
    assert a != b


def test_vnet_id_for_net_plan_id_is_required_keyword_only():
    """`plan_id` has no default — every caller must supply it explicitly
    (CLAUDE.md task instructions: a default would silently reproduce the
    exact bug this fixes)."""
    with pytest.raises(TypeError):
        vnet_id_for_net("dmz")  # type: ignore[call-arg]


def test_vnet_id_for_net_stable_for_same_plan_and_net():
    """Same `(plan_id, net_id)` -> the SAME vnet id, deterministically,
    across repeated calls and across tiers."""
    for tier in ("te", "o2", "tfc", "exr"):
        a = vnet_id_for_net("corp", plan_id="plan-123", tier=tier)
        b = vnet_id_for_net("corp", plan_id="plan-123", tier=tier)
        assert a == b


def test_bridge_name_length_cap():
    b = bridge_name_for_net("very-long-net-id-overflow")
    assert len(b) <= 15
    assert b.startswith("tebr_")  # underscore, not hyphen — PVE iface regex


def test_bridge_name_no_hyphens():
    """PVE::Network rejects iface names with hyphens. Confirm the helper
    converts every non-alphanumeric char to underscore."""
    b = bridge_name_for_net("corp-dmz-mgmt")
    assert "-" not in b
    assert b == "tebr_corp_dmz_m"  # trimmed at 15 chars ("tebr_" leaves 10)


def test_vm_name_format():
    n = vm_name_for_host("ransomware-hospital-7b5b010a", "h-fin")
    assert n.startswith("te-")
    assert len(n) <= 63


def test_gre_port_for_bridge_unique_and_safe():
    """OVS interface names are globally unique, so the GRE-out port name must
    differ per scenario bridge (a constant collides across concurrent tapped
    scenarios — surfaced on the lab cluster 2026-06-01)."""
    a = gre_port_for_bridge("vbr_corp")
    b = gre_port_for_bridge("vbr_corp2")
    assert a != b
    assert a == gre_port_for_bridge("vbr_corp")  # deterministic
    assert len(a) <= 15 and a.startswith("tapg_")
    assert "-" not in a  # kernel/OVS ifname-safe


def test_gre_key_for_bridge_distinct_nonzero():
    """Per-bridge GRE key: distinct so two tunnels to one sensor coexist at the
    OVS datapath; non-zero + 32-bit (docs/ARCHITECTURE.md §4)."""
    a = gre_key_for_bridge("vbr_corp")
    b = gre_key_for_bridge("vbr_corp2")
    assert a != b
    assert a == gre_key_for_bridge("vbr_corp")  # deterministic
    assert 1 <= a <= 0xFFFFFFFF and 1 <= b <= 0xFFFFFFFF


def test_attach_gre_tunnel_creates_keyed_gretap_netdev(monkeypatch):
    """attach-gre-tunnel (Option 1, 2026-06-04) creates a keyed gretap *netdev*
    (not an OVS GRE port) per (bridge, sensor), so coexisting tunnels don't collide
    and the per-tap tc mirror has a target to point at."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    calls: list[str] = []

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        return ""  # `ip link show` empty -> create path

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    op = OpInstance(
        op_id="p1:attach-gre-tunnel[corp@sensor2]", op_name="attach-gre-tunnel",
        op_catalog_id="OP-MIR-010", kind=OpKind.ACTION,
        scope={"net": "corp", "sensor": "sensor2"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n", sensor_transport_ip="10.0.0.86"),
        placement={},
    )
    out = backend.dispatch(
        op,
        {"bridge_name": "vbr_corp", "sensor_id": "sensor2",
         "sensor_transport_ip": "10.0.0.99"},
        ctx,
    )
    dev = gre_port_for_bridge_sensor("vbr_corp", "sensor2")
    key = gre_key_for_bridge_sensor("vbr_corp", "sensor2")
    assert out["gre_port"] == dev
    add_cmds = [c for c in calls if "ip link add" in c]
    assert add_cmds, calls
    assert "type gretap" in add_cmds[0]
    assert "remote 10.0.0.99" in add_cmds[0]
    assert f"key {key}" in add_cmds[0]
    assert dev in add_cmds[0]
    # The gretap is brought up at the capture MTU.
    assert any(f"ip link set {dev} mtu" in c and " up" in c for c in calls)
    # No OVS port-mirror constructs anymore.
    assert not any("ovs-vsctl" in c for c in calls)


def test_attach_gre_tunnel_adopts_existing_netdev(monkeypatch):
    """Model C: if the gretap netdev already exists, attach-gre-tunnel adopts it
    (no `ip link add`, so a live capture isn't dropped) and still idempotently
    re-asserts MTU + up."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    dev = gre_port_for_bridge_sensor("vbr_corp", "default")
    calls: list[str] = []

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("ip -o link show"):
            return f"7: {dev}@NONE: <BROADCAST,MULTICAST,UP> mtu 1500"
        return ""

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    op = OpInstance(
        op_id="p1:attach-gre-tunnel[corp@default]", op_name="attach-gre-tunnel",
        op_catalog_id="OP-MIR-010", kind=OpKind.ACTION,
        scope={"net": "corp", "sensor": "default"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n", sensor_transport_ip="10.0.0.86"),
        placement={},
    )
    out = backend.dispatch(op, {"bridge_name": "vbr_corp"}, ctx)
    assert out["gre_port"] == dev
    assert not any("ip link add" in c for c in calls)  # adopted, not recreated
    assert any(f"ip link set {dev} mtu" in c for c in calls)


def _gretap_dispatch(monkeypatch, cfg, *, existing: bool):
    """Run attach-gre-tunnel against a faked node; return (dev, issued cmds)."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    dev = gre_port_for_bridge_sensor("vbr_corp", "default")
    calls: list[str] = []

    def fake_run_ssh(_cfg, cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("ip -o link show"):
            return f"7: {dev}@NONE: <BROADCAST,MULTICAST,UP> mtu 1500" if existing else ""
        return ""

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    backend = ssh_mod.SshBackend(cfg)
    op = OpInstance(
        op_id="p1:attach-gre-tunnel[corp@default]", op_name="attach-gre-tunnel",
        op_catalog_id="OP-MIR-010", kind=OpKind.ACTION,
        scope={"net": "corp", "sensor": "default"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n", sensor_transport_ip="10.0.0.86"),
        placement={},
    )
    backend.dispatch(op, {"bridge_name": "vbr_corp"}, ctx)
    return dev, calls


def test_attach_gre_tunnel_replaces_node_default_qdisc(monkeypatch):
    """A new gretap inherits the NODE's default qdisc -- fq_codel on PVE 9.x, an
    AQM that DROPS to hold latency down. That is wrong for a mirror: the copy is
    fire-and-forget, so a drop is lost evidence and no sender backs off.
    Measured on dev45 2026-08-13: 9,349 frames lost in 90s at ~2.38 Gbit/s
    before this, zero after. attach-gre-tunnel must assert a deep FIFO."""
    cfg = AdapterConfig(ovs_ssh_host="h", mirror_gretap_txqueuelen=20000)
    dev, calls = _gretap_dispatch(monkeypatch, cfg, existing=False)
    assert any(f"tc qdisc replace dev {dev} root pfifo limit 20000" in c
               for c in calls), calls
    assert any(f"ip link set {dev} txqueuelen 20000" in c for c in calls), calls


def test_attach_gre_tunnel_asserts_qdisc_on_adopted_netdev(monkeypatch):
    """The assert must run on the ADOPT path too, not just on create -- this is
    the whole point. attach-gre-tunnel adopts a pre-existing netdev, so a gretap
    built before this change would otherwise keep fq_codel forever, and the
    operator would have to re-assert the qdisc by hand after every tap deploy
    (exactly what the dev45 run had to do)."""
    cfg = AdapterConfig(ovs_ssh_host="h", mirror_gretap_txqueuelen=20000)
    dev, calls = _gretap_dispatch(monkeypatch, cfg, existing=True)
    assert not any("ip link add" in c for c in calls)  # still adopted
    assert any(f"tc qdisc replace dev {dev} root pfifo" in c for c in calls), calls


def test_attach_gre_tunnel_qdisc_optout_is_zero(monkeypatch):
    """MIRROR_GRETAP_TXQUEUELEN=0 is the explicit opt-out for a node whose qdisc
    is managed elsewhere: no tc/txqueuelen command is issued at all, and the
    gretap keeps the node default (the pre-2026-08-13 behaviour)."""
    cfg = AdapterConfig(ovs_ssh_host="h", mirror_gretap_txqueuelen=0)
    dev, calls = _gretap_dispatch(monkeypatch, cfg, existing=False)
    assert not any("tc qdisc" in c for c in calls), calls
    assert not any("txqueuelen" in c for c in calls), calls
    # ...but the tunnel is still built and brought up.
    assert any(f"ip link set {dev} mtu" in c for c in calls)


def test_attach_gre_tunnel_creates_with_outer_df_off(monkeypatch):
    """corp-twin live run 3 (2026-09-28): a full-size mirrored frame encapsulates
    to 1542/1546 B, over a 1500-MTU transport. With the kernel default outer DF
    (`pmtudisc`) inner-DF IPv4 was lost as gretap tx_errors (E2BIG) and 802.1Q/
    non-DF frames as tx_dropped (IpFragFails) -- ~30% / ~17% of frames on the
    busiest segments. The gretap must be CREATED `nopmtudisc ignore-df` so the
    transport fragments instead (the sensor reassembles before decap)."""
    cfg = AdapterConfig(ovs_ssh_host="h")
    dev, calls = _gretap_dispatch(monkeypatch, cfg, existing=False)
    add = [c for c in calls if c.startswith(f"ip link add {dev} type gretap")]
    assert len(add) == 1, calls
    assert add[0].endswith(" nopmtudisc ignore-df"), add[0]
    # Created with the policy already -> no redundant changelink on this path.
    assert not any(" type gretap nopmtudisc" in c and c.startswith("ip link set")
                   for c in calls), calls


def test_attach_gre_tunnel_heals_df_policy_on_adopted_netdev(monkeypatch):
    """A gretap built before the DF fix is ADOPTED by a tap re-apply (never
    recreated -- that would drop a live capture). The DF policy must therefore be
    asserted in place with a changelink, before the tunnel is (re)brought up."""
    cfg = AdapterConfig(ovs_ssh_host="h")
    dev, calls = _gretap_dispatch(monkeypatch, cfg, existing=True)
    assert not any("ip link add" in c for c in calls)
    heal = f"ip link set {dev} type gretap nopmtudisc ignore-df"
    assert heal in calls, calls
    up = next(i for i, c in enumerate(calls) if c.startswith(f"ip link set {dev} mtu"))
    assert calls.index(heal) < up, calls


def test_install_mirror_hook_uses_tc_into_gretap(monkeypatch):
    """install-ovs-mirror writes a post-start/pre-stop hook that installs the
    per-tap tc mirror (clsact + matchall mirred, both directions) into the
    gretap, with a fail-loud self-check, and registers it as the VM hookscript.
    No OVS constructs remain."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    calls: list[str] = []
    written: dict[str, str] = {}

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        if "qm status" in cmd:
            return "stopped\n"  # not running -> no in-band path
        return ""

    def fake_scp(cfg, content, path, **kw):
        written["path"] = path
        written["content"] = content

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(ssh_mod, "scp_to_remote", fake_scp)
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    gretap = gre_port_for_bridge_sensor("vbr_corp", "sensor2")
    op = OpInstance(
        op_id="p1:install-ovs-mirror[h-a]", op_name="install-ovs-mirror",
        op_catalog_id="OP-MIR-020", kind=OpKind.ACTION, scope={"host": "h-a"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n"), placement={},
    )
    out = backend.dispatch(
        op,
        {"vmid": 12345, "node_id": "px1", "bridge_name": "vbr_corp",
         "gre_port": gretap},
        ctx,
    )
    assert out["hook_installed"] is True
    c = written["content"]
    # Legacy single-NIC input (no tapped_nics) -> the hook's per-NIC pairs carry
    # nic 0 -> this gretap (dynamic-tap hook generalization, 2026-06-19), with
    # an explicit "both" direction (the legacy-path default, `docs/
    # WORK_ORDER_tap_scopes.md` M5 -- never a silent narrowing).
    assert f'pairs="0:{gretap}:both"' in c
    assert 'tc qdisc add dev "$vmnic" clsact' in c
    # Both directions install a matchall filter whose action list chains a mirred
    # mirror per gretap (multi-sensor fan-out, C4 finding 2026-06-23) -- "both"
    # direction means BOTH the ingress and the egress filter get it.
    assert "ingress matchall $ingress_acts" in c
    assert "egress matchall $egress_acts" in c
    assert "action mirred egress mirror dev $g" in c
    # Fail-loud self-check is present (the 2026-06-03 finding was a silent loss).
    assert "grep -q mirred" in c
    # Re-mechanized away from OVS.
    assert "ovs-vsctl" not in c
    # Registered as the VM hookscript at the deterministic path.
    assert written["path"].endswith("axrange-mirror-12345.sh")
    assert any(cmd.startswith("qm set 12345 --hookscript") for cmd in calls)


def test_install_mirror_inband_tc_when_vm_running(monkeypatch):
    """When the VM is already running (Model C adopt on re-deploy, so the
    post-start hook won't fire), install-ovs-mirror asserts the tc mirror in-band
    against the live tap, pointing at the same gretap."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    calls: list[str] = []

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        if "qm status" in cmd:
            return "running\n"
        return ""

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(ssh_mod, "scp_to_remote", lambda *a, **k: None)
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    gretap = gre_port_for_bridge_sensor("vbr_corp", "default")
    op = OpInstance(
        op_id="p1:install-ovs-mirror[h-a]", op_name="install-ovs-mirror",
        op_catalog_id="OP-MIR-020", kind=OpKind.ACTION, scope={"host": "h-a"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n"), placement={},
    )
    backend.dispatch(
        op,
        {"vmid": 777, "node_id": "px1", "bridge_name": "vbr_corp",
         "gre_port": gretap},
        ctx,
    )
    tc_cmds = [c for c in calls if "tc filter add dev tap777i0" in c]
    assert tc_cmds, calls
    assert f"mirror dev {gretap}" in tc_cmds[0]
    assert "clsact" in " ".join(calls)


def test_install_mirror_empty_tapped_nics_clears_not_raises(monkeypatch):
    """A NARROWING tap re-apply composes install-ovs-mirror with EMPTY
    tapped_nics to REVERT a dropped host (o2control/taps.py::_compose_ops). The
    adapter must CLEAR the VM's mirror (empty no-op hook + `tc qdisc del clsact`
    on its live tap ifaces), NOT raise 'no tapped NICs' -- which failed every
    dropped host and broke the whole re-apply (dev45 corp-enterprise
    segment->boundary, 2026-07-26)."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    calls: list[str] = []
    scpd: list[str] = []

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        if "qm status" in cmd:
            return "running\n"
        if "ip -o link show" in cmd:
            return "tap888i0\ntap888i1\n"
        return ""

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(
        ssh_mod, "scp_to_remote",
        lambda cfg, content, path, **k: scpd.append(content),
    )
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    op = OpInstance(
        op_id="p1:install-ovs-mirror[dropped]", op_name="install-ovs-mirror",
        op_catalog_id="OP-MIR-020", kind=OpKind.ACTION, scope={"host": "dropped"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n"), placement={},
    )
    # EMPTY desired: tapped_nics == [] and no gre_port -> revert, not error.
    res = backend.dispatch(op, {"vmid": 888, "node_id": "px1", "tapped_nics": []}, ctx)
    assert res.get("mirror_cleared") is True
    # An empty (no-op) hook is written -- no mirror pairs in it.
    assert scpd and 'pairs=""' in scpd[0]
    # The live tc mirror is cleared on EVERY tap iface the VM owns.
    assert any("tc qdisc del dev tap888i0 clsact" in c for c in calls), calls
    assert any("tc qdisc del dev tap888i1 clsact" in c for c in calls), calls
    # ...and the (empty) hookscript is registered so a reboot reinstates nothing.
    assert any("qm set 888 --hookscript" in c for c in calls)


def test_install_mirror_narrowing_nic_set_clears_stale_nic(monkeypatch):
    """Fix round S1 (`docs/WORK_ORDER_tap_scopes.md`): a NON-empty re-apply
    that narrows a host's tapped NIC SET (e.g. it moves from nic1 to nic0,
    or drops one of several tapped NICs on a re-shard) must clear the
    now-undesired NIC's live tc mirror too, not just install the NEW one --
    the empty-set path already swept every `tap<vmid>i*` iface; the
    non-empty path must do the same for whichever ifaces AREN'T in the
    freshly desired set."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    calls: list[str] = []

    def fake_run_ssh(cfg, cmd, **kw):
        calls.append(cmd)
        if "qm status" in cmd:
            return "running\n"
        if "ip -o link show" in cmd:
            # The VM currently has BOTH nic0 and nic1 mirrored (a prior,
            # wider apply) -- the new desired set (below) only wants nic0.
            return "tap999i0\ntap999i1\n"
        return ""

    monkeypatch.setattr(ssh_mod, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(ssh_mod, "scp_to_remote", lambda *a, **k: None)
    backend = ssh_mod.SshBackend(AdapterConfig(ovs_ssh_host="h"))
    gretap = gre_port_for_bridge_sensor("vbr_corp", "default")
    op = OpInstance(
        op_id="p1:install-ovs-mirror[narrowed]", op_name="install-ovs-mirror",
        op_catalog_id="OP-MIR-020", kind=OpKind.ACTION, scope={"host": "narrowed"},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="n"), placement={},
    )
    # Desired: ONLY nic0 tapped now (nic1 dropped by this apply).
    out = backend.dispatch(
        op,
        {
            "vmid": 999, "node_id": "px1",
            "tapped_nics": [
                {"nic_index": 0, "net_id": "corp", "sensor_id": "default",
                 "gre_port_input": "gre_port__0", "direction": "both"},
            ],
            "gre_port__0": gretap,
        },
        ctx,
    )
    # The NEW mirror is installed on nic0.
    assert any(
        "tc filter add dev tap999i0" in c and f"mirror dev {gretap}" in c
        for c in calls
    ), calls
    # nic1's STALE mirror is explicitly cleared, not left dangling until reboot.
    assert any("tc qdisc del dev tap999i1 clsact" in c for c in calls), calls
    # The stale-clearing sweep names nic1 ONLY -- it's a STANDALONE command
    # (no "&&"), unlike the install path's combined
    # "qdisc del && qdisc add && filter add ..." chain for nic0.
    sweep_calls = [
        c for c in calls
        if c.strip().startswith("tc qdisc del dev") and "&&" not in c
    ]
    assert sweep_calls == ["tc qdisc del dev tap999i1 clsact 2>/dev/null || true"]
    # Fix round G6 (`docs/WORK_ORDER_tap_scopes.md`): the narrowing sweep's
    # result must be legible after the fact (LES-102) -- not just a flat
    # `{"hook_installed": True}` with no record of which NIC(s) it reset.
    assert out["cleared_ifaces"] == ["tap999i1"]


# ---------------------------------------------------------------------------
# Per-gretap direction at the mirror (`docs/WORK_ORDER_tap_scopes.md` M5) --
# the hook install path (VM stopped, so the post-start/pre-stop hook is what
# actually runs the tc install come next boot) and the in-band live-install
# path (`_tc_mirror_cmd`, VM already running) must BOTH: chain every gretap
# on ingress, chain ONLY the "both"-direction gretaps on egress, and OMIT the
# egress `tc filter add` entirely when a NIC has no "both" destination at all
# (an empty-action `tc filter` is invalid `tc` syntax, not just wasted --
# an omission bug here is a hard cluster failure, not cosmetic).
# ---------------------------------------------------------------------------
def _run_hook_script_post_start(tmp_path, hook_text: str, *, vmid: int) -> list[str]:
    """Actually RUN the generated hook script's post-start phase against a
    fake `tc` on PATH, returning every `tc` invocation as a list of
    space-joined-arg strings -- proves the bash conditional logic itself
    (ingress always chains, egress only for "both", omitted when none),
    not just that the template's Python-side inputs look right."""
    import os
    import subprocess

    log_path = tmp_path / "tc.log"
    fake_tc = tmp_path / "tc"
    fake_tc.write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> {log_path}\n'
        # The hook's fail-loud self-check greps `tc filter show ... | grep -q
        # mirred` -- the fake must emit something containing "mirred" for
        # THAT invocation so the real hook script's `set -e` doesn't abort.
        'if [ "$1 $2" = "filter show" ]; then echo "action mirred"; fi\n'
        "exit 0\n"
    )
    fake_tc.chmod(0o755)
    script_path = tmp_path / "hook.sh"
    script_path.write_text(hook_text)
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    subprocess.run(
        ["bash", str(script_path), str(vmid), "post-start"],
        check=True, env=env, timeout=10,
    )
    return [l for l in log_path.read_text().splitlines() if l.strip()]


def test_hook_script_mixed_direction_nic_chains_ingress_all_egress_only_both(tmp_path):
    """One NIC (0) fans to two sensors: one `"both"`, one `"rx"`. Ingress
    must chain BOTH gretaps; egress must chain ONLY the `"both"` one."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    hook = ssh_mod._hook_script(
        "p1", [(0, "gre-both", "both"), (0, "gre-rx", "rx")],
    )
    calls = _run_hook_script_post_start(tmp_path, hook, vmid=999)

    ingress = [c for c in calls if "tap999i0" in c and "ingress matchall" in c]
    egress = [c for c in calls if "tap999i0" in c and "egress matchall" in c]
    assert len(ingress) == 1
    assert "gre-both" in ingress[0] and "gre-rx" in ingress[0]
    assert len(egress) == 1
    assert "gre-both" in egress[0]
    assert "gre-rx" not in egress[0]


def test_hook_script_all_rx_nic_omits_egress_filter_entirely(tmp_path):
    """A NIC (1) whose gretaps are ALL `"rx"` gets NO egress `tc filter add`
    at all -- an empty-action `tc filter` is invalid syntax, not merely
    wasted, so an omission bug here would be a hard cluster failure."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    hook = ssh_mod._hook_script(
        "p1", [(1, "gre-rx-a", "rx"), (1, "gre-rx-b", "rx")],
    )
    calls = _run_hook_script_post_start(tmp_path, hook, vmid=999)

    ingress = [c for c in calls if "tap999i1" in c and "ingress matchall" in c]
    egress = [c for c in calls if "tap999i1" in c and "egress matchall" in c]
    assert len(ingress) == 1
    assert "gre-rx-a" in ingress[0] and "gre-rx-b" in ingress[0]
    assert not egress  # no egress tc filter add invocation for this NIC at all


def test_hook_script_two_nics_mixed_and_all_rx_together(tmp_path):
    """Both cases in ONE VM's hook (the realistic shape: a multi-sensor NIC
    next to an all-rx one) -- NIC 0 is mixed (egress chains only "both"),
    NIC 1 is all-rx (egress omitted for it specifically), proving the
    per-NIC decision doesn't leak across NICs."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    hook = ssh_mod._hook_script(
        "p1",
        [
            (0, "gre-both", "both"),
            (0, "gre-rx", "rx"),
            (1, "gre-rx2", "rx"),
        ],
    )
    calls = _run_hook_script_post_start(tmp_path, hook, vmid=777)

    nic0_egress = [c for c in calls if "tap777i0" in c and "egress matchall" in c]
    nic1_egress = [c for c in calls if "tap777i1" in c and "egress matchall" in c]
    assert len(nic0_egress) == 1 and "gre-both" in nic0_egress[0]
    assert not nic1_egress


def test_tc_mirror_cmd_mixed_direction_and_all_rx():
    """The in-band (VM-already-running) live-install path -- Python-side, no
    shell execution needed: `_tc_mirror_cmd` builds the SAME per-direction
    chaining `_hook_script`'s bash template does."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    mixed = ssh_mod._tc_mirror_cmd(
        "tap1i0", [("gre-both", "both"), ("gre-rx", "rx")],
    )
    assert mixed.count("ingress matchall") == 1
    assert mixed.count("egress matchall") == 1
    ingress_part, egress_part = mixed.split("egress matchall", 1)
    assert "gre-both" in ingress_part and "gre-rx" in ingress_part
    assert "gre-both" in egress_part and "gre-rx" not in egress_part

    all_rx = ssh_mod._tc_mirror_cmd("tap1i1", [("gre-rx2", "rx")])
    assert "ingress matchall" in all_rx
    assert "egress matchall" not in all_rx  # omitted entirely, not empty-action


def test_resolve_nic_gretaps_defaults_missing_direction_to_both():
    """M5's safe-default claim: a `tapped_nics` entry that predates the
    `direction` field (a RESUMED deploy's already-persisted op input,
    LES-096) must default to `"both"` -- never a silent narrowing to
    rx-only just because the field happens to be absent. Exercises
    `_resolve_nic_gretaps` directly (the exact function the docstring's
    claim is about), not just through the legacy-no-`tapped_nics`-at-all
    path the pre-existing hook test covers."""
    from axrange.adapter_proxmox import ssh as ssh_mod

    inp = {
        "tapped_nics": [
            {
                "nic_index": 0, "net_id": "corp", "sensor_id": "s1",
                "gre_port_input": "gre_port__0__s1",
                # No "direction" key at all.
            },
        ],
        "gre_port__0__s1": "gre-corp-s1",
    }
    out = ssh_mod.SshBackend._resolve_nic_gretaps(inp)
    assert out == [(0, "gre-corp-s1", "both")]


def test_mirror_name_format():
    assert mirror_name_for_vmid(123) == "tapm-123"


def test_img_vm_name_format():
    assert img_vm_name_for_image("debian-12-cloud") == "img-debian-12-cloud"
    assert img_vm_name_for_image("suricata-sensor") == "img-suricata-sensor"


def test_te_tap_predicates_match_own_deterministic_names():
    """Finding #6 (2026-07-07), retagged (docs/WORK_ORDER_resource_tags.md):
    the `is_te_*`/`is_tap_*` predicates recognize exactly the shapes their
    sibling generators produce (single source of truth `teardown.py` imports
    instead of hand-rolling the same regex/range)."""
    zone = zone_id_for_plan("plan-123")
    vnet = vnet_id_for_net("corp", plan_id="plan-123")
    bridge_underlay = underlay_bridge_for_plan("plan-123")
    bridge_net = bridge_name_for_net("corp")
    vm_name = vm_name_for_host("plan-123", "h-fin")
    gretap = gre_port_for_bridge_sensor("vbr_corp", "sensor1")
    mirror = mirror_name_for_vmid(12345)
    vmid = vmid_for_op("plan-123", "plan-123:clone-vm[h-fin]")

    assert is_te_zone(zone)
    assert is_te_vnet(vnet)
    assert is_te_bridge(bridge_underlay)
    assert is_te_bridge(bridge_net)
    assert is_te_vm_name(vm_name)
    assert is_tap_gretap(gretap)
    assert is_tap_mirror(mirror)
    assert is_te_vmid(vmid)


def test_te_tap_predicates_reject_foreign_names():
    assert not is_te_vnet("vmbr0")
    assert not is_te_zone("myzone")
    assert not is_te_bridge("vmbr0")
    assert not is_te_vm_name("some-other-vm")
    assert not is_tap_gretap("eth0")
    assert not is_tap_mirror("eth0")
    assert not is_te_vmid(100)        # below the 100000-999999 range
    assert not is_te_vmid(9000)       # a typical hand-managed template id


def test_o2_img_predicates_match_own_producers():
    """`o2_*` producers are defined but NOT wired into any live emission path
    (docs/WORK_ORDER_resource_tags.md §2) — this only proves the shapes and
    predicates agree, ahead of the future O2 control layer calling them."""
    vnet = o2_vnet_id_for_component("plan-1", "sensor")
    bridge = o2_bridge_name_for_component("sensor")
    vm = o2_vm_name_for_component("sensor")
    img_vm = img_vm_name_for_image("debian-12-cloud")

    assert is_o2_vnet(vnet)
    assert is_o2_bridge(bridge)
    assert is_o2_vm_name(vm)
    assert is_o2(vnet) and is_o2(bridge) and is_o2(vm)
    assert is_img_vm_name(img_vm)
    assert is_img(img_vm)


def test_is_protected_external_matches_builtin_and_extra():
    """`XPress-SW` (the packetXpress sink/capture box) is protected out of the
    box; an unknown/empty name is not; an operator `extra` name (adapter.conf
    PROTECTED_EXTERNAL_NAMES) is also protected; the match is exact/case-
    sensitive, not a shape/prefix match."""
    assert is_protected_external("XPress-SW")
    assert not is_protected_external("some-other-vm")
    assert not is_protected_external("")
    assert is_protected_external("foo-sink", extra=["foo-sink"])
    assert not is_protected_external("foo-sink")
    assert not is_protected_external("xpress-sw")  # case-sensitive exact match


def test_is_protected_external_strips_whitespace():
    """Review hardening: a stray-whitespace PVE display name (leading/
    trailing spaces a real backend could plausibly return) must not silently
    slip past the guard -- both `name` and `extra` entries are stripped
    before comparing. Case sensitivity is unaffected."""
    assert is_protected_external("XPress-SW ")
    assert is_protected_external(" XPress-SW")
    assert is_protected_external("foo-sink ", extra=[" foo-sink"])
    assert not is_protected_external(" xpress-sw ")  # still case-sensitive


def test_tier_predicates_are_mutually_exclusive():
    """The blast-radius tiers must never overlap: no name produced by ANY
    producer may satisfy more than one tier's predicate
    (docs/WORK_ORDER_resource_tags.md §3 validation item (c))."""
    plan_id = "ransomware-hospital-7b5b010a"
    names_by_producer = {
        "te_zone": zone_id_for_plan(plan_id),
        "te_vnet": vnet_id_for_net("corp", plan_id=plan_id),
        "te_bridge_underlay": underlay_bridge_for_plan(plan_id),
        "te_bridge_net": bridge_name_for_net("corp-dmz-mgmt"),
        "te_vm_name": vm_name_for_host(plan_id, "h-fin"),
        "tap_gretap": gre_port_for_bridge_sensor("some-bridge", "sensor1"),
        "tap_mirror": mirror_name_for_vmid(555),
        "o2_vnet": o2_vnet_id_for_component(plan_id, "sensor"),
        "o2_bridge": o2_bridge_name_for_component("sensor"),
        "o2_vm_name": o2_vm_name_for_component("sensor"),
        "img_vm_name": img_vm_name_for_image("debian-12-cloud"),
        # Traffic tier (tfc) — docs/WORK_ORDER_tier_split.md.
        "tfc_zone": zone_id_for_plan(plan_id, tier="tfc"),
        "tfc_vnet": vnet_id_for_net("corp", plan_id=plan_id, tier="tfc"),
        "tfc_bridge_underlay": underlay_bridge_for_plan(plan_id, tier="tfc"),
        "tfc_bridge_net": bridge_name_for_net("corp-dmz-mgmt", tier="tfc"),
        "tfc_vm_name": vm_name_for_host(plan_id, "h-fin", tier="tfc"),
        "tfc_zone_component": tfc_zone_id_for_component("sensor"),
        "tfc_vnet_component": tfc_vnet_id_for_component(plan_id, "sensor"),
        "tfc_bridge_component": tfc_bridge_name_for_component("sensor"),
        "tfc_vm_component": tfc_vm_name_for_component(plan_id, "attacker"),
        # Exercise tier (exr).
        "exr_zone": zone_id_for_plan(plan_id, tier="exr"),
        "exr_vnet": vnet_id_for_net("corp", plan_id=plan_id, tier="exr"),
        "exr_bridge_underlay": underlay_bridge_for_plan(plan_id, tier="exr"),
        "exr_bridge_net": bridge_name_for_net("corp-dmz-mgmt", tier="exr"),
        "exr_vm_name": vm_name_for_host(plan_id, "h-fin", tier="exr"),
        "exr_zone_component": exr_zone_id_for_component("sensor"),
        "exr_vnet_component": exr_vnet_id_for_component(plan_id, "sensor"),
        "exr_bridge_component": exr_bridge_name_for_component("sensor"),
        "exr_vm_component": exr_vm_name_for_component(plan_id, "attacker"),
    }
    predicates = {
        "te_zone": is_te_zone,
        "te_vnet": is_te_vnet,
        "te_bridge": is_te_bridge,
        "te_vm_name": is_te_vm_name,
        "tap_gretap": is_tap_gretap,
        "tap_mirror": is_tap_mirror,
        "o2_vnet": is_o2_vnet,
        "o2_bridge": is_o2_bridge,
        "o2_vm_name": is_o2_vm_name,
        "img_vm_name": is_img_vm_name,
        "tfc_zone": is_tfc_zone,
        "tfc_vnet": is_tfc_vnet,
        "tfc_bridge": is_tfc_bridge,
        "tfc_vm_name": is_tfc_vm_name,
        "exr_zone": is_exr_zone,
        "exr_vnet": is_exr_vnet,
        "exr_bridge": is_exr_bridge,
        "exr_vm_name": is_exr_vm_name,
    }
    # Each producer's own shape maps to exactly one predicate key it must
    # satisfy (the "home" predicate); everything else must reject it.
    home = {
        "te_zone": "te_zone", "te_vnet": "te_vnet",
        "te_bridge_underlay": "te_bridge", "te_bridge_net": "te_bridge",
        "te_vm_name": "te_vm_name", "tap_gretap": "tap_gretap",
        "tap_mirror": "tap_mirror", "o2_vnet": "o2_vnet",
        "o2_bridge": "o2_bridge", "o2_vm_name": "o2_vm_name",
        "img_vm_name": "img_vm_name",
        "tfc_zone": "tfc_zone", "tfc_vnet": "tfc_vnet",
        "tfc_bridge_underlay": "tfc_bridge", "tfc_bridge_net": "tfc_bridge",
        "tfc_vm_name": "tfc_vm_name",
        "tfc_zone_component": "tfc_zone", "tfc_vnet_component": "tfc_vnet",
        "tfc_bridge_component": "tfc_bridge", "tfc_vm_component": "tfc_vm_name",
        "exr_zone": "exr_zone", "exr_vnet": "exr_vnet",
        "exr_bridge_underlay": "exr_bridge", "exr_bridge_net": "exr_bridge",
        "exr_vm_name": "exr_vm_name",
        "exr_zone_component": "exr_zone", "exr_vnet_component": "exr_vnet",
        "exr_bridge_component": "exr_bridge", "exr_vm_component": "exr_vm_name",
    }
    for producer_key, name in names_by_producer.items():
        matches = [pkey for pkey, pred in predicates.items() if pred(name)]
        assert matches == [home[producer_key]], (
            f"{producer_key}={name!r} matched {matches}, expected only "
            f"[{home[producer_key]!r}]"
        )

    # Aggregate tier predicates (is_te/is_tap/is_o2/is_tfc/is_exr/is_img) must
    # partition the same set: every producer's name matches exactly ONE
    # aggregate, and it's the right one.
    aggregate = {
        "te": is_te, "tap": is_tap, "o2": is_o2, "tfc": is_tfc, "exr": is_exr,
        "img": is_img,
    }
    home_aggregate = {k: v.split("_")[0] for k, v in home.items()}
    for producer_key, name in names_by_producer.items():
        matches = [tier for tier, pred in aggregate.items() if pred(name)]
        assert matches == [home_aggregate[producer_key]], (
            f"{producer_key}={name!r} aggregate-matched {matches}, expected "
            f"only [{home_aggregate[producer_key]!r}]"
        )


_TIER_AGGREGATE_PREDICATE = {
    "te": is_te, "o2": is_o2, "tfc": is_tfc, "exr": is_exr,
}

_EDGE_IDS = [
    pytest.param("", id="empty"),
    pytest.param("!!!___###---", id="all-non-alphanumeric"),
    pytest.param("x" * 500, id="very-long"),
    pytest.param("héllo-世界-🎉-données", id="unicode"),
]


@pytest.mark.parametrize("tier", ["te", "o2", "tfc", "exr"])
@pytest.mark.parametrize("edge_id", _EDGE_IDS)
def test_name_producers_never_emit_a_name_their_own_predicate_rejects(tier, edge_id):
    """A producer must never emit a name its own predicate rejects (the
    `bridge_name_for_net`-`or "x"`-fallback bug class, LES-088 /
    `docs/WORK_ORDER_tier_split.md` §4). Round-trips every producer x every
    tier against pathological ids: empty, all-non-alphanumeric, very long
    (past every length cap), and unicode -- not just the one well-behaved
    `plan_id` string `test_tier_predicates_are_mutually_exclusive` uses.

    NOTE: `vm_name_for_host`/`o2_vm_name_for_component`/
    `tfc_vm_name_for_component`/`exr_vm_name_for_component` against a truly
    EMPTY `host_id` was a producer/predicate mismatch found adversarially by
    this exact test (the same `bridge_name_for_net`-`or "x"`-fallback bug
    class, never propagated to the vm-name family) -- FIXED in
    `adapter_proxmox/naming.py` (each vm-name producer now falls back to `x`
    for an empty-after-sanitize `host_id`, same as `bridge_name_for_net`).
    See `test_vm_name_producers_accept_own_output_for_empty_host_id` below
    for the dedicated positive regression check. Every edge id (including
    empty) round-trips cleanly through the VM-name producers now, so vm-name
    assertions stay in this loop for every id, not just the non-empty ones."""
    zone = zone_id_for_plan(edge_id, tier=tier)
    vnet = vnet_id_for_net(edge_id, plan_id=edge_id, tier=tier)
    underlay = underlay_bridge_for_plan(edge_id, tier=tier)
    net_bridge = bridge_name_for_net(edge_id, tier=tier)

    zone_ok = {"te": is_te_zone, "o2": is_o2_zone, "tfc": is_tfc_zone, "exr": is_exr_zone}[tier]
    vnet_ok = {"te": is_te_vnet, "o2": is_o2_vnet, "tfc": is_tfc_vnet, "exr": is_exr_vnet}[tier]
    bridge_ok = {"te": is_te_bridge, "o2": is_o2_bridge, "tfc": is_tfc_bridge, "exr": is_exr_bridge}[tier]

    # Positive: the producer's own predicate accepts what it just produced.
    assert zone_ok(zone), f"{tier} zone_id_for_plan({edge_id!r}) = {zone!r} rejected by its own predicate"
    assert vnet_ok(vnet), f"{tier} vnet_id_for_net({edge_id!r}) = {vnet!r} rejected by its own predicate"
    assert bridge_ok(underlay), f"{tier} underlay_bridge_for_plan({edge_id!r}) = {underlay!r} rejected"
    assert bridge_ok(net_bridge), f"{tier} bridge_name_for_net({edge_id!r}) = {net_bridge!r} rejected"

    # Length caps (Proxmox / kernel constraints) hold regardless of input.
    assert len(zone) <= 8 and zone.isalnum()
    assert len(vnet) <= 8 and vnet.isalnum()
    assert len(underlay) <= 15 and "-" not in underlay
    assert len(net_bridge) <= 15 and "-" not in net_bridge

    names_to_check = [zone, vnet, underlay, net_bridge]
    if edge_id != "":
        plan_id = "fallback-plan-id"
        vm_name = vm_name_for_host(plan_id, edge_id, tier=tier)
        vm_ok = {"te": is_te_vm_name, "o2": is_o2_vm_name, "tfc": is_tfc_vm_name, "exr": is_exr_vm_name}[tier]
        assert vm_ok(vm_name), f"{tier} vm_name_for_host(..., {edge_id!r}) = {vm_name!r} rejected"
        assert len(vm_name) <= 63
        names_to_check.append(vm_name)

    # Negative: no OTHER tier's aggregate predicate accepts any of these
    # names -- the disjointness invariant must hold even at the edges.
    for produced in names_to_check:
        matches = [t for t, pred in _TIER_AGGREGATE_PREDICATE.items() if pred(produced)]
        assert matches == [tier], (
            f"{tier}-tier name {produced!r} aggregate-matched {matches}, expected only [{tier!r}]"
        )
        assert not is_tap(produced) and not is_img(produced)


@pytest.mark.parametrize("tier", ["te", "o2", "tfc", "exr"])
def test_vm_name_producers_accept_own_output_for_empty_host_id(tier):
    """FIXED (was `test_vm_name_producers_reject_own_output_for_empty_host_id_
    KNOWN_BUG`, an `xfail(strict=True)` -- docs/WORK_ORDER_tier_split.md review
    consolidated fix pass, MUST-FIX #1): an empty `host_id` used to sanitize
    to an empty tail (`te-<hex>-`, `o2-`, `tfc-<hex>-`, `exr-<hex>-`), which
    the producer's OWN predicate regex rejected (the tail requires 1+ chars)
    -- the same bug class the module docstring already flagged for
    `bridge_name_for_net`'s `or "x"` fallback, never propagated to the
    vm-name family until now. Each vm-name producer falls back to `x` for an
    empty-after-sanitize `host_id`, same as `bridge_name_for_net`; this is
    now a plain positive assertion, not an expected failure. Consequence of
    the bug this closes: `teardown.py`'s `_vm_name_ok` gate would otherwise
    refuse a legitimately-owned VM as a `guard-violation` whenever a host id
    sanitized to empty, permanently blocking that deploy from ever reaching
    `destroyed`."""
    plan_id = "fallback-plan-id"
    vm_name = vm_name_for_host(plan_id, "", tier=tier)
    vm_ok = {"te": is_te_vm_name, "o2": is_o2_vm_name, "tfc": is_tfc_vm_name, "exr": is_exr_vm_name}[tier]
    assert vm_ok(vm_name), f"{tier} vm_name_for_host(..., '') = {vm_name!r} rejected by its own predicate"


def test_tier_tables_coverage_survives_dash_o():
    """A plain pytest MIRROR of `naming.py`'s two import-time coverage
    `assert`s (review SHOULD-FIX #3, third part): those module-level
    `assert`s are load-bearing (LES-088 fail-closed for the tier machinery)
    but `python -O` strips bare `assert` statements at compile time -- this
    repo runs no `-O` today, so the module-level asserts stay (fast,
    fail-at-import-time), but a TEST re-expressing the same invariant
    survives an optimized run and fails legibly in CI regardless of
    interpreter flags. A tier wired into `_VALID_TIERS`/the producers but
    forgotten in `TIER_SHAPE_CHECKS`/`TIER_PREDICATES` is exactly the
    "no predicate owns this name, so nothing refuses it" silent-permissive
    failure mode a blast-radius guard must never have."""
    from axrange.adapter_proxmox.naming import (
        _VALID_TIERS,
        TIER_PREDICATES,
        TIER_SHAPE_CHECKS,
    )

    assert set(_VALID_TIERS) == set(TIER_SHAPE_CHECKS), (
        "_VALID_TIERS and TIER_SHAPE_CHECKS have drifted out of lockstep -- "
        "every deploy tier the producers route to must have a shape-check "
        "triple, and vice versa"
    )
    assert set(_VALID_TIERS) <= set(TIER_PREDICATES), (
        "every deploy tier in _VALID_TIERS must have an aggregate predicate "
        "in TIER_PREDICATES -- planner/teardown.py's foreign-tier refusal "
        "loop keys off this exact table"
    )
    # Every TIER_SHAPE_CHECKS entry is a real (vnet_ok, zone_ok, vm_name_ok)
    # triple of callables, not a placeholder.
    for tier, triple in TIER_SHAPE_CHECKS.items():
        assert len(triple) == 3 and all(callable(f) for f in triple), tier
    for tier, pred in TIER_PREDICATES.items():
        assert callable(pred), tier


# ---------------------------------------------------------------------------
# Op-routing coverage
# ---------------------------------------------------------------------------


def test_slice_op_partition():
    """Every slice op (13 callable + 3 barriers) is routed to a backend.

    wait-os-ready was originally a "probe" path (orchestrator-side SSH); the
    slice pivots it to a Proxmox qemu-guest-agent exec via REST because the
    orchestrator can't reach the scenario's isolated SDN networks. See
    CHANGELOG 2026-05-29 for the rationale.
    """
    routable = _REST_OPS | _SSH_OPS | _PROBE_OPS
    assert routable == {
        "create-sdn-zone", "create-ovs-bridge", "create-vnet", "create-subnet",
        "apply-sdn-config", "clone-vm", "configure-vm-hardware",
        "configure-cloud-init", "attach-vm-to-vnet", "start-vm",
        "wait-guest-agent", "attach-gre-tunnel", "install-ovs-mirror",
        "wait-os-ready", "configure-router", "configure-routing",
    }
    assert len(_REST_OPS) == 13
    assert len(_SSH_OPS) == 3  # +configure-routing (fabric slice)
    assert len(_PROBE_OPS) == 0


def test_rest_op_to_method_table_matches_rest_ops():
    assert set(_OP_TO_METHOD.keys()) == _REST_OPS


def test_adapter_unknown_op_raises_unsupported():
    cfg = AdapterConfig()
    adapter = ProxmoxAdapter(cfg)
    op = OpInstance(
        op_id="p1:bogus[scope]", op_name="bogus", op_catalog_id="OP-XXX-000",
        kind=OpKind.ACTION, scope={},
        cleanup_strategy=None, idempotency_class=IdempotencyClass.INFRA,
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="x"), placement={},
    )
    with pytest.raises(AdapterError) as exc:
        adapter.run(op, {}, ctx)
    assert "no backend" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------


def test_parse_mem_mb_handles_k8s_units():
    assert _parse_mem_mb("4Gi") == 4096
    assert _parse_mem_mb("4G") == 4096
    assert _parse_mem_mb("512Mi") == 512
    assert _parse_mem_mb("512M") == 512
    assert _parse_mem_mb("1024") == 1024


def test_normalize_pve_disk_size_strips_binary_i_suffix():
    """The IR's binary-unit disk sizes (`10Gi`, same convention as
    `resources.ram`) must convert to PVE's decimal-suffix resize regex
    (`10G`) — PVE 400s on the raw `Gi`/`Mi`/`Ti`/`Ki` form."""
    assert _normalize_pve_disk_size("10Gi") == "10G"
    assert _normalize_pve_disk_size("512Mi") == "512M"
    assert _normalize_pve_disk_size("2Ti") == "2T"
    assert _normalize_pve_disk_size("1024Ki") == "1024K"


def test_normalize_pve_disk_size_passes_through_already_valid():
    assert _normalize_pve_disk_size("10G") == "10G"
    assert _normalize_pve_disk_size("10240") == "10240"


def test_normalize_pve_disk_size_preserves_leading_plus():
    assert _normalize_pve_disk_size("+2Gi") == "+2G"


def test_normalize_pve_disk_size_rejects_uncoercible_value():
    with pytest.raises(AdapterError, match="not a valid PVE resize size"):
        _normalize_pve_disk_size("bogus")


def test_rest_dispatch_uses_op_to_method(monkeypatch):
    """A REST op dispatches to its named method on RestBackend."""
    cfg = AdapterConfig()
    backend = RestBackend(cfg, client=_NullRest())
    op = OpInstance(
        op_id="p1:create-sdn-zone[scenario=demo]",
        op_name="create-sdn-zone",
        op_catalog_id="OP-NET-010",
        kind=OpKind.ACTION,
        scope={"scenario": "demo"},
        cleanup_strategy=None,
        idempotency_class=IdempotencyClass.INFRA,
        inputs_resolved={"scenario_id": "demo", "zone_type": "simple"},
    )
    ctx = ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node="px1"), placement={},
    )
    out = backend.dispatch(op, op.inputs_resolved, ctx)
    assert "zone_id" in out


class _NullRest:
    """REST client stub that returns empty data for GETs (so check-then-act
    sees "no resource"), succeeds for POST/PUT, and never raises."""

    def request(self, method, path, *, params=None, body=None):
        if method == "GET":
            raise AdapterError("not found", error_class="proxmox-api-http")
        if method == "POST" and "/clone" in path:
            return "UPID:px1:test:test:fake"
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


# ---------------------------------------------------------------------------
# OP-NET SDN-native (2026-06-03) — the four network ops against a fake client
# ---------------------------------------------------------------------------


class _RecordingRest:
    """Records every request; GET reports a resource present iff its path is in
    `exists` (else raises, so check-then-act sees "absent")."""

    def __init__(self, exists=()):
        self.calls: list[tuple[str, str, dict]] = []
        self.exists = set(exists)

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, dict(body or {})))
        if method == "GET":
            if path in self.exists:
                return {"present": True}
            raise AdapterError("not found", error_class="proxmox-api-http")
        if method == "POST" and "/clone" in path:
            return "UPID:fake"
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None

    def posts_to(self, path):
        return [b for m, p, b in self.calls if m == "POST" and p == path]


def _net_ctx(node="px1"):
    return ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node=node), placement={},
    )


def _net_op(name, scope, inputs):
    return OpInstance(
        op_id=f"p1:{name}{sorted(scope.items())}",
        op_name=name, op_catalog_id="OP-NET", kind=OpKind.ACTION,
        scope=scope, idempotency_class=IdempotencyClass.INFRA,
        inputs_resolved=inputs,
    )


class _AgentRest:
    """Fake RestClient handling the QGA endpoints (ping/exec/exec-status)."""

    def __init__(self, exitcode: int = 0):
        self.calls: list[tuple] = []
        self.exitcode = exitcode

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if path.endswith("/agent/ping"):
            return {}
        if method == "POST" and path.endswith("/agent/exec"):
            return {"pid": 1}
        if method == "GET" and path.endswith("/agent/exec-status"):
            return {"exited": True, "exitcode": self.exitcode, "out-data": "", "err-data": ""}
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None

    def execs(self):
        return [b for m, p, _params, b in self.calls if m == "POST" and p.endswith("/agent/exec")]


def test_wait_os_ready_windows_uses_powershell_probe():
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("wait-os-ready", {"host": "h"},
                 {"vmid": 102, "os_family": "windows", "first_boot_budget": 60})
    out = backend.op_wait_os_ready(op, op.inputs_resolved, _net_ctx())
    assert out["os_ready"] is True
    argv = client.execs()[-1]["command"]
    # W1.2 (post 2026-06-17): poll the os-ready MARKER (specialized signal), not a
    # bare CIM probe (which passes even at the OOBE screen).
    assert argv[0] == "powershell" and "axr-osready" in argv[-1]
    assert "Test-Path" in argv[-1]
    # No cloud-init on Windows.
    assert not any("cloud-init" in str(b["command"]) for b in client.execs())


def test_wait_os_ready_linux_uses_cloud_init():
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("wait-os-ready", {"host": "h"},
                 {"vmid": 100, "os_family": "linux", "first_boot_budget": 60})
    out = backend.op_wait_os_ready(op, op.inputs_resolved, _net_ctx())
    assert out["os_ready"] is True
    assert any(b["command"][:2] == ["cloud-init", "status"] for b in client.execs())


def test_render_windows_userdata():
    import re

    from axrange.adapter_proxmox.rest import _render_windows_userdata

    ud = _render_windows_userdata(hostname="ws-very-long-name-01", admin_password="P@ss'wd")
    assert ud.startswith("<powershell>") and ud.rstrip().endswith("</powershell>")
    assert "net user Administrator 'P@ss''wd'" in ud  # single-quote escaped
    cn = re.search(r"Rename-Computer -NewName '([^']*)'", ud).group(1)
    assert 1 <= len(cn) <= 15  # NetBIOS limit
    assert "QEMU-GA" in ud and "axr-osready" in ud


def _patch_ssh(monkeypatch):
    from axrange.adapter_proxmox import ssh as ssh_mod

    scps: list[tuple] = []
    monkeypatch.setattr(ssh_mod, "run_ssh", lambda cfg, cmd, **kw: "")
    monkeypatch.setattr(
        ssh_mod, "scp_to_remote",
        lambda cfg, content, path, **kw: scps.append((content, path)),
    )
    return scps


def test_configure_cloud_init_windows_branch(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-cloud-init", {"host": "ws01"}, {
        "vmid": 200, "hostname": "ws01", "os_family": "windows",
        "ci_user": "Administrator",
        "ip_config": {"corp": {"ip": "10.0.0.5", "cidr": "10.0.0.0/24", "gateway": "10.0.0.1"}},
    })
    out = backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    assert out["cloud_init_attached"] is True
    assert any("<powershell>" in c for c, _p in scps)  # windows snippet scp'd
    put = next(b for m, p, _pr, b in client.calls if m == "PUT" and p.endswith("/config"))
    assert put["ciuser"] == "Administrator" and "cicustom" in put
    assert put["ipconfig0"] == "ip=10.0.0.5/24,gw=10.0.0.1"


def test_configure_cloud_init_linux_unchanged(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-cloud-init", {"host": "h"}, {
        "vmid": 201, "hostname": "h", "os_family": "linux", "ci_user": "debian",
        "ip_config": {"corp": {"ip": "10.0.0.6", "cidr": "10.0.0.0/24", "gateway": "10.0.0.1"}},
    })
    backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    assert any("#cloud-config" in c for c, _p in scps)  # linux path unchanged
    put = next(b for m, p, _pr, b in client.calls if m == "PUT" and p.endswith("/config"))
    assert put["ciuser"] == "debian"


def test_create_ovs_bridge_vlan_creates_ovs_underlay():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-ovs-bridge", {"scenario": "demo"},
                 {"net_id": None, "zone_type": "vlan"})
    out = backend.op_create_ovs_bridge(op, op.inputs_resolved, _net_ctx())
    assert out["bridge_name"] == underlay_bridge_for_plan("p1")
    # One OVSBridge POST to the node network, then a node-network commit.
    net_posts = [b for m, p, b in client.calls
                 if m == "POST" and p == "/nodes/px1/network"]
    assert len(net_posts) == 1
    assert net_posts[0]["type"] == "OVSBridge"
    assert net_posts[0]["iface"] == underlay_bridge_for_plan("p1")
    assert ("PUT", "/nodes/px1/network", {}) in client.calls


def test_create_ovs_bridge_simple_makes_no_node_network_call():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-ovs-bridge", {"net": "corp"},
                 {"net_id": "corp", "zone_type": "simple"})
    out = backend.op_create_ovs_bridge(op, op.inputs_resolved, _net_ctx())
    # Simple zone: the vnet materializes the bridge; no /nodes/network call.
    assert out["bridge_name"] == vnet_id_for_net("corp", plan_id="p1")
    assert client.calls == []


def test_create_sdn_zone_vlan_posts_zone_with_bridge():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    underlay = underlay_bridge_for_plan("p1")
    op = _net_op("create-sdn-zone", {"scenario": "demo"},
                 {"scenario_id": "demo", "zone_type": "vlan",
                  "bridge_name": underlay})
    out = backend.op_create_sdn_zone(op, op.inputs_resolved, _net_ctx())
    assert out["zone_id"] == zone_id_for_plan("p1")
    zone_posts = client.posts_to("/cluster/sdn/zones")
    assert len(zone_posts) == 1
    assert zone_posts[0]["type"] == "vlan"
    assert zone_posts[0]["zone"] == zone_id_for_plan("p1")
    assert zone_posts[0]["bridge"] == underlay


def test_create_sdn_zone_simple_posts_zone_without_bridge():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-sdn-zone", {"scenario": "demo"},
                 {"scenario_id": "demo", "zone_type": "simple"})
    backend.op_create_sdn_zone(op, op.inputs_resolved, _net_ctx())
    zone_posts = client.posts_to("/cluster/sdn/zones")
    assert zone_posts[0]["type"] == "simple"
    assert "bridge" not in zone_posts[0]


def test_create_sdn_zone_idempotent_when_zone_exists():
    zone_id = zone_id_for_plan("p1")
    client = _RecordingRest(exists=[f"/cluster/sdn/zones/{zone_id}"])
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-sdn-zone", {"scenario": "demo"},
                 {"scenario_id": "demo", "zone_type": "vlan",
                  "bridge_name": underlay_bridge_for_plan("p1")})
    backend.op_create_sdn_zone(op, op.inputs_resolved, _net_ctx())
    # Already present -> no POST.
    assert client.posts_to("/cluster/sdn/zones") == []


def test_apply_sdn_config_waits_for_reload_task(monkeypatch):
    """PUT /cluster/sdn is async (returns a reloadnetworkall UPID); the op must
    wait for it so the vnet bridge is live before any start-vm attaches a NIC
    (dev45 2026-06-18 race: start-vm 'bridge does not exist'). The UPID's node
    segment drives which node we poll."""
    from axrange.adapter_proxmox import ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "run_ssh", lambda cfg, cmd, **kw: "")

    waited: list[tuple[str, str]] = []

    class _ReloadRest(_RecordingRest):
        def request(self, method, path, *, params=None, body=None):
            self.calls.append((method, path, dict(body or {})))
            if method == "PUT" and path == "/cluster/sdn":
                return "UPID:nodeX:0001:reloadnetworkall::root@pam:"
            return {}

        def wait_task(self, node, upid, *, timeout_s=60):
            waited.append((node, upid))

    client = _ReloadRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("apply-sdn-config", {"scenario": "demo"}, {})
    out = backend.op_apply_sdn_config(op, op.inputs_resolved, _net_ctx())
    assert out == {"applied": True}
    # Waited on exactly the reload task, on the node from the UPID.
    assert waited == [("nodeX", "UPID:nodeX:0001:reloadnetworkall::root@pam:")]


def test_apply_sdn_config_skips_wait_when_no_upid(monkeypatch):
    """A non-UPID return (older PVE / mocked) must not break apply — fall through
    to the SSH settle/scan without a task wait."""
    from axrange.adapter_proxmox import ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "run_ssh", lambda cfg, cmd, **kw: "")
    waited: list = []

    class _NoUpidRest(_RecordingRest):
        def wait_task(self, node, upid, *, timeout_s=60):
            waited.append(upid)

    client = _NoUpidRest()  # PUT /cluster/sdn returns {} (no UPID)
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("apply-sdn-config", {"scenario": "demo"}, {})
    backend.op_apply_sdn_config(op, op.inputs_resolved, _net_ctx())
    assert waited == []


def test_create_vnet_vlan_carries_tag():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-vnet", {"net": "corp"},
                 {"net_id": "corp", "vlan": 10, "zone_id": zone_id_for_plan("p1")})
    out = backend.op_create_vnet(op, op.inputs_resolved, _net_ctx())
    assert out["vnet_id"] == vnet_id_for_net("corp", plan_id="p1")
    vnet_posts = client.posts_to("/cluster/sdn/vnets")
    assert len(vnet_posts) == 1
    assert vnet_posts[0]["vnet"] == vnet_id_for_net("corp", plan_id="p1")
    assert vnet_posts[0]["zone"] == zone_id_for_plan("p1")
    assert vnet_posts[0]["tag"] == 10


def test_create_vnet_simple_is_untagged():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("create-vnet", {"net": "flat"},
                 {"net_id": "flat", "vlan": None, "zone_id": zone_id_for_plan("p1")})
    backend.op_create_vnet(op, op.inputs_resolved, _net_ctx())
    vnet_posts = client.posts_to("/cluster/sdn/vnets")
    assert "tag" not in vnet_posts[0]


def test_apply_sdn_config_does_cluster_wide_apply(monkeypatch):
    import axrange.adapter_proxmox.ssh as ssh_mod
    ssh_calls = []
    monkeypatch.setattr(ssh_mod, "run_ssh",
                        lambda cfg, cmd, **kw: ssh_calls.append(cmd) or "")
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("apply-sdn-config", {"scenario": "demo"},
                 {"zone_id": zone_id_for_plan("p1")})
    out = backend.op_apply_sdn_config(op, op.inputs_resolved, _net_ctx())
    assert out["applied"] is True
    # Cluster-wide SDN apply, NOT a per-node /nodes/<node>/network PUT.
    assert ("PUT", "/cluster/sdn", {}) in client.calls
    assert not any(p.startswith("/nodes/") for m, p, _ in client.calls)


# ---------------------------------------------------------------------------
# VyOS router config over SSH (vyos-ssh-set)
# ---------------------------------------------------------------------------


class _VyosRest:
    """REST stub: returns the VM config with a WAN MAC on net0."""

    def __init__(self, mac="AA:BB:CC:11:22:33"):
        self.mac = mac

    def request(self, method, path, *, params=None, body=None):
        if method == "GET" and path.endswith("/config"):
            return {"net0": f"virtio={self.mac},bridge=vmbr0"}
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


def _router_op(inputs):
    return OpInstance(
        op_id="p1:configure-router",
        op_name="configure-router", op_catalog_id="OP-HOST-090",
        kind=OpKind.ACTION, scope={"host": "fw"},
        idempotency_class=IdempotencyClass.INFRA, inputs_resolved=inputs,
    )


def test_vyos_configure_router_ssh_set(monkeypatch):
    """VyOS configure-router resolves the WAN IP via arp-scan and applies LAN
    interfaces + masquerade + static routes via vbash over SSH; an idempotent
    'No configuration changes' (rc!=0) is treated as success."""
    import axrange.adapter_proxmox.ssh as ssh_mod
    captured = {}

    def fake_discover(cfg, mac, **kw):
        captured["mac"] = mac
        return "10.80.72.99"

    monkeypatch.setattr(ssh_mod, "discover_guest_ip_by_mac", fake_discover)

    def fake_guest(cfg, host, user, cmd, *, stdin_text=None, timeout_s=60):
        captured["host"] = host
        captured["user"] = user
        if "route show default" in cmd:  # WAN-gateway discovery (fabric egress leak)
            return (0, "default via 10.80.72.1 dev eth0 proto dhcp")
        captured["script"] = stdin_text
        return (1, "No configuration changes to commit")  # benign idempotent

    monkeypatch.setattr(ssh_mod, "run_ssh_guest", fake_guest)

    backend = RestBackend(AdapterConfig(), client=_VyosRest(mac="AA:BB:CC:11:22:33"))
    op = _router_op({
        "vmid": 700, "router_os": "vyos", "wan_eth": "eth0",
        "wan_bridge": "vmbr0",
        "lan_ifaces": [
            {"eth": "eth1", "address": "10.0.0.1/24"},
            {"eth": "eth2", "address": "10.0.1.1/24"},
        ],
        "nameservers": ["8.8.8.8"],
        "static_routes": [{"to": "10.9.0.0/16", "via": "10.0.0.254"}],
    })
    out = backend.op_configure_router(op, op.inputs_resolved, _net_ctx())

    assert out["configured"] is True
    assert out["wan_ip"] == "10.80.72.99"
    assert out["wan_gw"] == "10.80.72.1"  # discovered for the fabric tenant-VRF egress leak
    assert captured["mac"] == "AA:BB:CC:11:22:33"
    assert captured["host"] == "10.80.72.99"
    s = captured["script"]
    assert "source /opt/vyatta/etc/functions/script-template" in s
    assert "set interfaces ethernet eth1 address '10.0.0.1/24'" in s
    assert "set interfaces ethernet eth2 address '10.0.1.1/24'" in s
    assert "set nat source rule 100 outbound-interface name 'eth0'" in s
    assert "set nat source rule 100 translation address 'masquerade'" in s
    assert "set system name-server '8.8.8.8'" in s
    assert "set protocols static route 10.9.0.0/16 next-hop 10.0.0.254" in s
    assert "commit" in s and "save" in s


def test_ssh_connect_failed_helper():
    from axrange.adapter_proxmox.ssh import ssh_connect_failed
    assert ssh_connect_failed(255, "vyos@10.0.0.1: Permission denied (publickey,password).")
    assert ssh_connect_failed(0, "ssh: connect to host h port 22: Connection refused")
    assert not ssh_connect_failed(1, "No configuration changes to commit")  # benign vbash no-op
    assert not ssh_connect_failed(0, "")


def test_vyos_configure_router_ssh_auth_failure_raises(monkeypatch):
    """An unauthorized key / unreachable router must FAIL the op, not false-succeed —
    the SSH 'Permission denied' matches no VyOS config-error marker. Caught live on
    dev45 2026-06-30 (vyos-template2 didn't authorize the orchestrator key)."""
    import axrange.adapter_proxmox.ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "discover_guest_ip_by_mac", lambda cfg, mac, **kw: "10.80.72.99")
    monkeypatch.setattr(
        ssh_mod, "run_ssh_guest",
        lambda *a, **k: (255, "vyos@10.80.72.99: Permission denied (publickey,password)."),
    )
    backend = RestBackend(AdapterConfig(), client=_VyosRest(mac="AA:BB:CC:11:22:33"))
    op = _router_op({
        "vmid": 700, "router_os": "vyos", "wan_eth": "eth0", "wan_bridge": "vmbr0",
        "lan_ifaces": [{"eth": "eth1", "address": "10.0.0.1/24"}],
        "nameservers": [], "static_routes": [],
    })
    with pytest.raises(AdapterError) as ei:
        backend.op_configure_router(op, op.inputs_resolved, _net_ctx())
    assert ei.value.error_class == "router-ssh-failed"


def test_vyos_configure_router_detects_apply_error(monkeypatch):
    """A real VyOS error (stderr marker) raises, despite any exit code."""
    import axrange.adapter_proxmox.ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "discover_guest_ip_by_mac",
                        lambda cfg, mac, **kw: "10.80.72.99")
    monkeypatch.setattr(ssh_mod, "run_ssh_guest",
                        lambda *a, **k: (0, "Commit failed\n[[interfaces]] failed"))
    backend = RestBackend(AdapterConfig(), client=_VyosRest())
    op = _router_op({
        "vmid": 700, "router_os": "vyos", "wan_eth": "eth0",
        "lan_ifaces": [{"eth": "eth1", "address": "10.0.0.1/24"}],
    })
    with pytest.raises(AdapterError, match="router-config-failed|apply failed"):
        backend.op_configure_router(op, op.inputs_resolved, _net_ctx())


def test_debian_configure_router_persists_nat(monkeypatch):
    """The Debian/QGA path installs forwarding + masquerade as a reboot-persistent
    systemd oneshot (re-detects WAN at boot) and applies immediately — no apt
    dependency (CHANGELOG 2026-06-24). Asserts the script's shape over QGA."""
    import axrange.adapter_proxmox.guest_agent as ga_mod
    from axrange.adapter_proxmox.guest_agent import GuestExecResult
    captured = {}

    class _FakeGA:
        def __init__(self, client, node, **kw):
            captured["node"] = node

        def wait_ready(self, vmid, *, timeout_s=300):
            captured["waited"] = vmid

        def exec(self, vmid, argv, *, timeout_s=300):
            captured["vmid"] = vmid
            captured["script"] = argv[-1]
            return GuestExecResult(
                exit_code=0, stdout="AXR_ROUTER_OK wan=eth1 forward=1", stderr=""
            )

    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGA)
    backend = RestBackend(AdapterConfig(), client=_VyosRest())
    op = _router_op({"vmid": 700})  # no router_os -> Debian/QGA path
    out = backend.op_configure_router(op, op.inputs_resolved, _net_ctx())

    assert out["configured"] is True
    assert captured["vmid"] == 700
    s = captured["script"]
    # Reboot-persistent: a systemd oneshot that re-applies on boot.
    assert "/etc/systemd/system/axr-router-nat.service" in s
    assert "systemctl enable axr-router-nat.service" in s
    assert "After=network-online.target" in s
    assert "/usr/local/sbin/axr-router-nat.sh" in s
    # Forwarding persisted + applied; masquerade via nft OR iptables.
    assert "net.ipv4.ip_forward=1" in s
    assert "/etc/sysctl.d/99-axr-router.conf" in s
    assert "masquerade" in s and "MASQUERADE" in s
    # The fragile apt-install-nftables dependency is gone.
    assert "apt-get" not in s


def test_debian_configure_router_no_wan_fails(monkeypatch):
    """No WAN default route -> AXR_ROUTER_FAIL -> the op raises (not a silent ok)."""
    import axrange.adapter_proxmox.guest_agent as ga_mod
    from axrange.adapter_proxmox.guest_agent import GuestExecResult

    class _FakeGA:
        def __init__(self, client, node, **kw): pass
        def wait_ready(self, vmid, *, timeout_s=300): pass
        def exec(self, vmid, argv, *, timeout_s=300):
            return GuestExecResult(
                exit_code=1, stdout="AXR_ROUTER_FAIL no-WAN-default-route", stderr=""
            )

    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGA)
    backend = RestBackend(AdapterConfig(), client=_VyosRest())
    op = _router_op({"vmid": 700})
    with pytest.raises(AdapterError, match="setup failed"):
        backend.op_configure_router(op, op.inputs_resolved, _net_ctx())


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROXMOX_API_URL", "https://test:8006/api2/json")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "PVEAPIToken=axr@pve!t=secret")
    monkeypatch.setenv("OVS_SSH_HOST", "10.0.0.85")
    monkeypatch.setenv("DEFAULT_NODE", "pxtwin1")
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.proxmox_api_url == "https://test:8006/api2/json"
    assert cfg.ovs_ssh_host == "10.0.0.85"
    assert cfg.default_node == "pxtwin1"


def test_load_config_from_file(tmp_path, monkeypatch):
    # Clear env first so it doesn't override the file.
    for k in (
        "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "OVS_SSH_HOST",
        "OVS_SSH_USER", "OVS_SSH_KEY", "DEFAULT_NODE",
        "PROXMOX_VERIFY_TLS",
    ):
        monkeypatch.delenv(k, raising=False)

    p = tmp_path / "adapter.conf"
    p.write_text(
        "PROXMOX_API_URL=https://1.2.3.4:8006/api2/json\n"
        "OVS_SSH_HOST=1.2.3.4\n"
        "OVS_SSH_KEY=/tmp/keyfile\n"
        "DEFAULT_NODE=nodeA\n"
    )
    cfg = load_config(p)
    assert cfg.proxmox_api_url == "https://1.2.3.4:8006/api2/json"
    assert cfg.ovs_ssh_host == "1.2.3.4"
    assert cfg.default_node == "nodeA"


def test_load_config_mirror_gretap_txqueuelen(monkeypatch, tmp_path):
    """The mirror-gretap queue depth parses defensively. `load_config` is called
    from a dozen independent CLI/API/o2control entry points, so a bare int()
    here would surface as a raw traceback on paths that never expected one
    (LES-090): a malformed value falls back to the default and a negative one
    clamps to 0 (= leave the node's default qdisc alone)."""
    monkeypatch.delenv("MIRROR_GRETAP_TXQUEUELEN", raising=False)
    assert load_config(tmp_path / "missing.conf").mirror_gretap_txqueuelen == 20000

    for raw, want in (("50000", 50000), ("0", 0), ("not-a-number", 20000),
                      ("-1", 0)):
        p = tmp_path / f"c-{raw}.conf"
        p.write_text(f"MIRROR_GRETAP_TXQUEUELEN={raw}\n")
        assert load_config(p).mirror_gretap_txqueuelen == want, raw


def test_load_config_no_longer_reads_sensor_star(monkeypatch, tmp_path):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    no code path reads SENSOR_* from adapter.conf/env any more."""
    for k in ("SENSOR_TRANSPORT_IP", "SENSOR_SSH_HOST", "SENSOR_DMZ_TRANSPORT_IP"):
        monkeypatch.setenv(k, "10.255.255.10")
    cfg = load_config(tmp_path / "missing.conf")
    assert not hasattr(cfg, "sensor_transport_ip")
    assert not hasattr(cfg, "sensor_ssh_host")
    assert not hasattr(cfg, "sensors")


def test_load_config_overrides_take_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFAULT_NODE", "env-node")
    cfg = load_config(tmp_path / "x", overrides={"DEFAULT_NODE": "override-node"})
    assert cfg.default_node == "override-node"


def test_load_config_capture_water_marks_default_85_70(monkeypatch, tmp_path):
    """No CAPTURE_HIGH_WATER_PCT/CAPTURE_LOW_WATER_PCT in env or file ->
    AdapterConfig defaults to 85/70 (user decision 2026-07-17), the same
    default `ConfigBackend._deploy_sensor` falls back to."""
    for k in ("CAPTURE_HIGH_WATER_PCT", "CAPTURE_LOW_WATER_PCT"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.capture_high_water_pct == 85
    assert cfg.capture_low_water_pct == 70


def test_load_config_capture_water_marks_from_file(tmp_path, monkeypatch):
    for k in ("CAPTURE_HIGH_WATER_PCT", "CAPTURE_LOW_WATER_PCT"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text(
        "CAPTURE_HIGH_WATER_PCT=92\n"
        "CAPTURE_LOW_WATER_PCT=55\n"
    )
    cfg = load_config(p)
    assert cfg.capture_high_water_pct == 92
    assert cfg.capture_low_water_pct == 55


def test_load_config_capture_water_marks_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPTURE_HIGH_WATER_PCT", "80")
    monkeypatch.setenv("CAPTURE_LOW_WATER_PCT", "65")
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.capture_high_water_pct == 80
    assert cfg.capture_low_water_pct == 65


def test_load_config_capture_max_files_default_2000(monkeypatch, tmp_path):
    """No CAPTURE_MAX_FILES in env or file -> AdapterConfig defaults to 2000
    (review SHOULD-FIX 2026-07-17, the file-count prune ceiling)."""
    monkeypatch.delenv("CAPTURE_MAX_FILES", raising=False)
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.capture_max_files == 2000


def test_load_config_capture_max_files_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPTURE_MAX_FILES", raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text("CAPTURE_MAX_FILES=500\n")
    cfg = load_config(p)
    assert cfg.capture_max_files == 500


def test_load_config_capture_low_water_clamped_below_high(monkeypatch, tmp_path):
    """NIT (review 2026-07-17, parity with the standing appliance's own
    sanity guard, o2/tcpdump-sensor/bin/capture-watchdog): an operator
    setting CAPTURE_LOW_WATER_PCT >= CAPTURE_HIGH_WATER_PCT would otherwise
    ping-pong the prune loop (a "successful" prune to LOW never actually
    relieves HIGH pressure). `load_config` silently clamps LOW to HIGH - 1."""
    for k in ("CAPTURE_HIGH_WATER_PCT", "CAPTURE_LOW_WATER_PCT"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text(
        "CAPTURE_HIGH_WATER_PCT=80\n"
        "CAPTURE_LOW_WATER_PCT=90\n"  # LOW > HIGH -- misconfigured
    )
    cfg = load_config(p)
    assert cfg.capture_high_water_pct == 80
    assert cfg.capture_low_water_pct == 79  # clamped to HIGH - 1


def test_load_config_capture_low_water_clamped_when_equal_to_high(monkeypatch, tmp_path):
    for k in ("CAPTURE_HIGH_WATER_PCT", "CAPTURE_LOW_WATER_PCT"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text(
        "CAPTURE_HIGH_WATER_PCT=85\n"
        "CAPTURE_LOW_WATER_PCT=85\n"  # LOW == HIGH -- also misconfigured
    )
    cfg = load_config(p)
    assert cfg.capture_high_water_pct == 85
    assert cfg.capture_low_water_pct == 84


def test_load_config_protected_external_names_default_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("PROTECTED_EXTERNAL_NAMES", raising=False)
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.protected_external_names == []


def test_load_config_protected_external_names_parses_comma_list(tmp_path, monkeypatch):
    monkeypatch.delenv("PROTECTED_EXTERNAL_NAMES", raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text("PROTECTED_EXTERNAL_NAMES=XPress-SW, foo-sink\n")
    cfg = load_config(p)
    assert cfg.protected_external_names == ["XPress-SW", "foo-sink"]


def _clear_external_sensor_env(monkeypatch):
    for k in ("EXTERNAL_SENSORS", "PROTECTED_EXTERNAL_NAMES", "PROTECTED_EXTERNAL_VMIDS"):
        monkeypatch.delenv(k, raising=False)


def test_load_config_external_sensors_default_empty(monkeypatch, tmp_path):
    _clear_external_sensor_env(monkeypatch)
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.external_sensors == []
    assert cfg.protected_external_vmids == []


def test_load_config_external_sensors_parses_json(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20","type":"packetxpress",'
        '"capture_iface":"gre-cap"}]\n'
    )
    cfg = load_config(p)
    assert cfg.external_sensors == [
        ExternalSensor(
            id="XPress-SW", vmid=250, transport_ip="10.255.250.20",
            type="packetxpress", capture_iface="gre-cap",
        )
    ]


def test_load_config_external_sensors_defaults_type_and_iface(tmp_path, monkeypatch):
    """type/capture_iface are optional -- fall back to packetxpress/gre-cap."""
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,"transport_ip":"10.255.250.20"}]\n'
    )
    cfg = load_config(p)
    assert cfg.external_sensors[0].type == "packetxpress"
    assert cfg.external_sensors[0].capture_iface == "gre-cap"


def test_load_config_external_sensor_auto_protects_id_and_vmid(tmp_path, monkeypatch):
    """Declaring an external sensor is SUFFICIENT to protect it -- no
    double-declaration in PROTECTED_EXTERNAL_NAMES/_VMIDS needed."""
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,"transport_ip":"10.255.250.20"}]\n'
    )
    cfg = load_config(p)
    assert "XPress-SW" in cfg.protected_external_names
    assert 250 in cfg.protected_external_vmids


def test_load_config_protected_external_vmids_parses_comma_list(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text("PROTECTED_EXTERNAL_VMIDS=250, 300\n")
    cfg = load_config(p)
    assert cfg.protected_external_vmids == [250, 300]


def test_load_config_external_sensors_malformed_json_fails_loud(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text("EXTERNAL_SENSORS=not-json\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_external_sensors_missing_required_key_fails_loud(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text('EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250}]\n')  # no transport_ip
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_external_sensors_not_a_list_fails_loud(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text('EXTERNAL_SENSORS={"id":"XPress-SW"}\n')  # object, not a list
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_external_sensors_blank_transport_ip_fails_loud(tmp_path, monkeypatch):
    """NICE-TO-HAVE #4 (code review): key PRESENCE alone doesn't catch a
    blank value -- `"transport_ip": ""` would otherwise attach a gretap with
    an empty remote."""
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,"transport_ip":""}]\n'
    )
    with pytest.raises(ConfigError, match="transport_ip"):
        load_config(p)


def test_load_config_external_sensors_blank_id_fails_loud(tmp_path, monkeypatch):
    _clear_external_sensor_env(monkeypatch)
    p = tmp_path / "adapter.conf"
    p.write_text(
        'EXTERNAL_SENSORS=[{"id":"  ","vmid":250,"transport_ip":"10.255.250.20"}]\n'
    )
    with pytest.raises(ConfigError, match="id"):
        load_config(p)


def test_create_subnet_posts_gateway_and_snat():
    """Router slice Phase 1 — create-subnet POSTs a PVE SDN subnet with the
    network's gateway + SNAT to the vnet, for internet/apt egress."""
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op(
        "create-subnet", {"net": "corp"},
        {"net_id": "corp", "cidr": "10.10.0.0/24", "gateway": "10.10.0.1", "snat": True},
    )
    out = backend.op_create_subnet(op, op.inputs_resolved, _net_ctx())
    vnet = vnet_id_for_net("corp", plan_id="p1")
    posts = client.posts_to(f"/cluster/sdn/vnets/{vnet}/subnets")
    assert len(posts) == 1
    body = posts[0]
    assert body["subnet"] == "10.10.0.0/24"
    assert body["gateway"] == "10.10.0.1"
    assert body["snat"] == 1
    assert out["vnet_id"] == vnet


def test_create_subnet_no_snat_when_not_requested():
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op(
        "create-subnet", {"net": "corp"},
        {"net_id": "corp", "cidr": "10.10.0.0/24", "gateway": "10.10.0.1", "snat": False},
    )
    backend.op_create_subnet(op, op.inputs_resolved, _net_ctx())
    body = client.posts_to(f"/cluster/sdn/vnets/{vnet_id_for_net('corp', plan_id='p1')}/subnets")[0]
    assert "snat" not in body


def test_is_already_present_matches_pve_wordings():
    """GAP-004: PVE reports SDN collisions with inconsistent wording — the
    idempotency guard must catch subnets' 'already defined', not just vnets/zones'
    'already exists'. A non-collision error must NOT be swallowed."""
    from axrange.adapter_proxmox.rest import _is_already_present
    assert _is_already_present(AdapterError("sdn subnet object ID 'x' already defined"))
    assert _is_already_present(AdapterError("vnet 'x' already exists"))
    assert not _is_already_present(AdapterError("HTTP 500 permission denied"))


def test_create_subnet_tolerates_already_defined():
    """GAP-004 regression: a POST that fails with PVE's 'already defined' subnet
    collision must be a no-op (Model C), NOT a hard failure that halts the SDN apply.
    (The GET pre-check missing it — the TOCTOU window — falls through to the backstop.)"""
    class _AlreadyDefinedRest:
        def __init__(self):
            self.posts = 0
        def request(self, method, path, *, params=None, body=None):
            if method == "GET":  # _subnet_exists sees nothing
                raise AdapterError("not found", error_class="proxmox-api-http")
            if method == "POST":
                self.posts += 1
                raise AdapterError(
                    "create sdn subnet object failed: sdn subnet object ID "
                    "'axr1-10.10.0.0-24' already defined",
                    error_class="proxmox-api-http",
                )
            return {}
        def wait_task(self, *a, **k):
            return None
    backend = RestBackend(AdapterConfig(), client=_AlreadyDefinedRest())
    op = _net_op(
        "create-subnet", {"net": "corp"},
        {"net_id": "corp", "cidr": "10.10.0.0/24", "gateway": "10.10.0.1"},
    )
    out = backend.op_create_subnet(op, op.inputs_resolved, _net_ctx())  # must NOT raise
    assert out["vnet_id"] == vnet_id_for_net("corp", plan_id="p1")


def test_create_subnet_precheck_skips_post_when_present():
    """Model C check-then-act: if the subnet already exists, issue no POST at all
    (matches create-zone/vnet, which create-subnet previously lacked — GAP-004)."""
    vnet = vnet_id_for_net("corp", plan_id="p1")
    class _SubnetPresentRest:
        def __init__(self):
            self.posts = []
        def request(self, method, path, *, params=None, body=None):
            if method == "GET" and path == f"/cluster/sdn/vnets/{vnet}/subnets":
                return [{"cidr": "10.10.0.0/24", "subnet": "axr1-10.10.0.0-24"}]
            if method == "GET":
                raise AdapterError("not found", error_class="proxmox-api-http")
            if method == "POST":
                self.posts.append(path)
            return {}
        def wait_task(self, *a, **k):
            return None
    client = _SubnetPresentRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op(
        "create-subnet", {"net": "corp"},
        {"net_id": "corp", "cidr": "10.10.0.0/24", "gateway": "10.10.0.1"},
    )
    backend.op_create_subnet(op, op.inputs_resolved, _net_ctx())
    assert client.posts == []  # pre-check short-circuited — no POST issued


# ---------------------------------------------------------------------------
# Code-review fixes (2026-07-07): resize error handling, clone-vm name-guard,
# ipconfig builder dedup, deterministic NIC MAC.
# ---------------------------------------------------------------------------


class _ResizeRest:
    """REST stub for configure-vm-hardware: GET .../config reports a fake VM
    config (for boot-disk-key resolution); PUT .../resize either succeeds or
    raises `resize_error`."""

    def __init__(self, resize_error=None, config=None):
        self.resize_error = resize_error
        self.config = config if config is not None else {}
        self.resize_calls: list[dict] = []

    def request(self, method, path, *, params=None, body=None):
        if method == "GET" and path.endswith("/config"):
            return self.config
        if method == "PUT" and path.endswith("/resize"):
            self.resize_calls.append(dict(body or {}))
            if self.resize_error is not None:
                raise self.resize_error
            return {}
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


def test_configure_vm_hardware_resize_swallows_shrink_wording():
    """Finding #1: the benign shrink/same-size PVE resize rejection is still a
    Model C no-op (must NOT raise)."""
    client = _ResizeRest(resize_error=AdapterError(
        "resize failed: shrinking disks is not supported"
    ))
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 500, "disk": "40G"})
    out = backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())
    assert out == {"configured": True}
    assert client.resize_calls  # the resize was attempted


def test_configure_vm_hardware_resize_reraises_real_error():
    """Finding #1: a non-shrink/same-size resize failure (e.g. storage
    exhaustion) must propagate, NOT report `{"configured": True}` over an
    undersized VM."""
    client = _ResizeRest(resize_error=AdapterError(
        "resize failed: no space left on device"
    ))
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 501, "disk": "500G"})
    with pytest.raises(AdapterError, match="no space left"):
        backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())


def test_configure_vm_hardware_resize_resolves_boot_disk_key():
    """Finding #1: the resize targets the disk key discovered from the VM's
    live config (bootdisk field here), not a hardcoded scsi0."""
    client = _ResizeRest(config={
        "bootdisk": "virtio0",
        "virtio0": "local-lvm:vm-501-disk-0,size=32G",
    })
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 501, "disk": "64G"})
    backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())
    assert client.resize_calls[0]["disk"] == "virtio0"


def test_configure_vm_hardware_resize_falls_back_to_scsi0():
    """Finding #1: no bootdisk/boot/disk-shaped key in the config -> fall back
    to scsi0 (every axRange-built template's boot disk), preserving old
    behavior for the common case and when the config read fails."""
    client = _ResizeRest(config={})
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 502, "disk": "64G"})
    backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())
    assert client.resize_calls[0]["disk"] == "scsi0"


def test_configure_vm_hardware_resize_normalizes_binary_disk_unit():
    """The IR's `disk: 10Gi` (binary unit, same convention as `resources.ram`)
    must reach PVE's `/resize` as `10G` — PVE's `size` regex rejects the raw
    `Gi` suffix with a 400, which the old blanket `except: pass` used to
    silently swallow (confirmed live on dev45)."""
    client = _ResizeRest(config={})
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 504, "disk": "10Gi"})
    backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())
    assert client.resize_calls[0]["size"] == "10G"


def test_configure_vm_hardware_resize_prefers_scsi0_over_cloudinit_drive():
    """2026-07-07 review fix: when neither `bootdisk` nor `boot=order=` is
    set, the last-resort fallback used to be a plain `sorted()` over every
    disk-shaped key — `ide2` (a cloud-init drive, not a boot disk) sorts
    ahead of `scsi0` alphabetically ('i' < 's'), so the resize could target
    the wrong disk. `scsi0` must win over any other disk-shaped key found
    this way."""
    client = _ResizeRest(config={
        "ide2": "local-lvm:vm-503-cloudinit,media=cdrom",
        "scsi0": "local-lvm:vm-503-disk-0,size=32G",
    })
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op("configure-vm-hardware", {"host": "h"}, {"vmid": 503, "disk": "64G"})
    backend.op_configure_vm_hardware(op, op.inputs_resolved, _net_ctx())
    assert client.resize_calls[0]["disk"] == "scsi0"


class _CloneVmRest:
    """REST stub for clone-vm: `status/current` reports an existing VM (with
    `existing_name`, or absent if None); clone POSTs are recorded."""

    def __init__(self, existing_name=None):
        self.existing_name = existing_name
        self.clone_calls: list[dict] = []

    def request(self, method, path, *, params=None, body=None):
        if method == "GET" and path.endswith("/status/current"):
            if self.existing_name is None:
                raise AdapterError("not found", error_class="proxmox-api-http")
            return {"status": "running", "name": self.existing_name}
        if method == "POST" and "/clone" in path:
            self.clone_calls.append(dict(body or {}))
            return "UPID:fake"
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


def _clone_op(host_id: str) -> OpInstance:
    return OpInstance(
        op_id=f"p1:clone-vm[{host_id}]", op_name="clone-vm",
        op_catalog_id="OP-HOST-010", kind=OpKind.ACTION,
        scope={"host": host_id}, idempotency_class=IdempotencyClass.INFRA,
        inputs_resolved={"template_vmid": 9000},
    )


def test_clone_vm_adopts_when_existing_name_matches():
    """Finding #2: Model C adoption requires the existing VM's PVE name to
    match `vm_name_for_host` -- a normal resume/redeploy of OUR OWN VM."""
    op = _clone_op("h-fin")
    expected_name = vm_name_for_host("p1", "h-fin")
    client = _CloneVmRest(existing_name=expected_name)
    backend = RestBackend(AdapterConfig(), client=client)
    out = backend.op_clone_vm(op, op.inputs_resolved, _net_ctx())
    assert out["vmid"] == vmid_for_op("p1", op.op_id)
    assert client.clone_calls == []  # adopted, not cloned


def test_clone_vm_raises_on_foreign_name_collision():
    """Finding #2: a VM already sitting on the deterministic VMID under a
    DIFFERENT name must NOT be silently adopted (hash collision / reused id) --
    the naming.py docstring's claimed check now actually exists."""
    op = _clone_op("h-fin")
    client = _CloneVmRest(existing_name="someone-elses-vm")
    backend = RestBackend(AdapterConfig(), client=client)
    with pytest.raises(AdapterError) as ei:
        backend.op_clone_vm(op, op.inputs_resolved, _net_ctx())
    assert ei.value.error_class == "vmid-collision-foreign"
    assert client.clone_calls == []  # never touched the foreign VM


def test_clone_vm_clones_when_vmid_absent():
    """Baseline: no existing VM at the deterministic VMID -> normal clone POST."""
    op = _clone_op("h-fin")
    client = _CloneVmRest(existing_name=None)
    backend = RestBackend(AdapterConfig(), client=client)
    out = backend.op_clone_vm(op, op.inputs_resolved, _net_ctx())
    assert out["vmid"] == vmid_for_op("p1", op.op_id)
    assert len(client.clone_calls) == 1


def test_build_ipconfig_body_static_and_dhcp():
    """Finding #3: the extracted ipconfig builder handles static (with/without
    gateway), dhcp, and a NIC with no `ip` (skipped) -- shared by the Linux and
    Windows first-boot paths."""
    from axrange.adapter_proxmox.rest import _build_ipconfig_body

    body = _build_ipconfig_body({
        "corp": {"ip": "10.0.0.5", "cidr": "10.0.0.0/24", "gateway": "10.0.0.1"},
        "wan": "dhcp",
        "unset": {"ip": None},
    })
    assert body == {"ipconfig0": "ip=10.0.0.5/24,gw=10.0.0.1", "ipconfig1": "ip=dhcp"}
    assert "ipconfig2" not in body


def test_build_ipconfig_body_static_no_gateway_defaults_prefix_24():
    from axrange.adapter_proxmox.rest import _build_ipconfig_body

    body = _build_ipconfig_body({"corp": {"ip": "10.0.0.5"}})
    assert body["ipconfig0"] == "ip=10.0.0.5/24"


def test_attach_vm_to_vnet_carries_deterministic_mac():
    """Finding #8: the NIC spec carries a `macaddr=` derived from
    (plan_id, host, nic_index) -- no more PVE-minted random MAC on every attach."""
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op = _net_op(
        "attach-vm-to-vnet", {"host": "h-a", "net": "corp"},
        {"vmid": 12345, "vnet_id": "vncorp1", "nic_index": 0, "vlan": None},
    )
    out = backend.op_attach_vm_to_vnet(op, op.inputs_resolved, _net_ctx())
    assert out["nic_attached"] is True
    put = next(b for m, p, b in client.calls if m == "PUT")
    expected_mac = mac_for_nic("p1", "h-a", 0)
    assert put["net0"] == f"virtio={expected_mac},bridge=vncorp1"


def test_attach_vm_to_vnet_mac_is_deterministic_across_runs():
    """Finding #8: the same (plan, host, nic_index) always yields the same MAC
    -- a redeploy re-attach doesn't churn it (breaks VyOS WAN-IP-by-MAC
    discovery + DHCP lease stability otherwise)."""
    op = _net_op(
        "attach-vm-to-vnet", {"host": "h-a", "net": "corp"},
        {"vmid": 12345, "vnet_id": "vncorp1", "nic_index": 0, "vlan": None},
    )
    client1 = _RecordingRest()
    RestBackend(AdapterConfig(), client=client1).op_attach_vm_to_vnet(
        op, op.inputs_resolved, _net_ctx()
    )
    client2 = _RecordingRest()
    RestBackend(AdapterConfig(), client=client2).op_attach_vm_to_vnet(
        op, op.inputs_resolved, _net_ctx()
    )
    mac1 = next(b for m, p, b in client1.calls if m == "PUT")["net0"]
    mac2 = next(b for m, p, b in client2.calls if m == "PUT")["net0"]
    assert mac1 == mac2


def test_attach_vm_to_vnet_mac_differs_per_nic_index():
    """Finding #8: a multi-homed host (e.g. a router) gets a distinct MAC per NIC."""
    client = _RecordingRest()
    backend = RestBackend(AdapterConfig(), client=client)
    op0 = _net_op(
        "attach-vm-to-vnet", {"host": "fw", "net": "corp"},
        {"vmid": 700, "vnet_id": "vncorp1", "nic_index": 0, "vlan": None},
    )
    op1 = _net_op(
        "attach-vm-to-vnet", {"host": "fw", "net": "dmz"},
        {"vmid": 700, "vnet_id": "vndmz01", "nic_index": 1, "vlan": None},
    )
    backend.op_attach_vm_to_vnet(op0, op0.inputs_resolved, _net_ctx())
    backend.op_attach_vm_to_vnet(op1, op1.inputs_resolved, _net_ctx())
    puts = [b for m, p, b in client.calls if m == "PUT"]
    assert puts[0]["net0"] != puts[1]["net1"]


# ---------------------------------------------------------------------------
# docs/WORK_ORDER_access_facts.md Part A -- DEFAULT_PASSWORD config + userdata
# ---------------------------------------------------------------------------


def test_load_config_default_password_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_PASSWORD", raising=False)
    cfg = load_config(tmp_path / "missing.conf")
    assert cfg.default_password == "Axr4nge-Lab!"


def test_load_config_default_password_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_PASSWORD", raising=False)
    p = tmp_path / "adapter.conf"
    p.write_text("DEFAULT_PASSWORD=SuperSecret1!\n")
    cfg = load_config(p)
    assert cfg.default_password == "SuperSecret1!"


def test_render_userdata_enables_password_login():
    from axrange.adapter_proxmox.rest import _render_userdata

    ud = _render_userdata(hostname="h1", user="debian", ssh_pubkey="", password="Axr4nge-Lab!")
    assert "lock_passwd: false" in ud
    assert "ssh_pwauth: true" in ud
    assert "chpasswd:" in ud
    assert "debian:Axr4nge-Lab!" in ud
    # SSH key installation is untouched when no pubkey is configured.
    assert "ssh_authorized_keys" not in ud


def test_configure_cloud_init_linux_uses_cfg_default_password(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(default_password="MyDefaultPw!"), client=client)
    op = _net_op("configure-cloud-init", {"host": "h"}, {
        "vmid": 300, "hostname": "h", "os_family": "linux", "ci_user": "debian",
        "ip_config": {},
    })
    backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    userdata = next(c for c, _p in scps if "#cloud-config" in c)
    assert "debian:MyDefaultPw!" in userdata


def test_configure_cloud_init_linux_explicit_password_wins(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(default_password="MyDefaultPw!"), client=client)
    op = _net_op("configure-cloud-init", {"host": "h"}, {
        "vmid": 301, "hostname": "h", "os_family": "linux", "ci_user": "debian",
        "ip_config": {}, "password": "BindingPw!",
    })
    backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    userdata = next(c for c, _p in scps if "#cloud-config" in c)
    assert "debian:BindingPw!" in userdata
    assert "MyDefaultPw!" not in userdata


def test_configure_cloud_init_windows_uses_cfg_default_password(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(default_password="WinDefaultPw!"), client=client)
    op = _net_op("configure-cloud-init", {"host": "ws01"}, {
        "vmid": 302, "hostname": "ws01", "os_family": "windows", "ci_user": "Administrator",
        "ip_config": {},
    })
    backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    put = next(b for m, p, _pr, b in client.calls if m == "PUT" and p.endswith("/config"))
    assert put["cipassword"] == "WinDefaultPw!"
    userdata = next(c for c, _p in scps if "<powershell>" in c)
    assert "WinDefaultPw!" in userdata


def test_configure_cloud_init_windows_explicit_password_wins(monkeypatch):
    scps = _patch_ssh(monkeypatch)
    client = _AgentRest()
    backend = RestBackend(AdapterConfig(default_password="WinDefaultPw!"), client=client)
    op = _net_op("configure-cloud-init", {"host": "ws01"}, {
        "vmid": 303, "hostname": "ws01", "os_family": "windows", "ci_user": "Administrator",
        "ip_config": {}, "password": "WinBindingPw!",
    })
    backend.op_configure_cloud_init(op, op.inputs_resolved, _net_ctx())
    put = next(b for m, p, _pr, b in client.calls if m == "PUT" and p.endswith("/config"))
    assert put["cipassword"] == "WinBindingPw!"


# ---------------------------------------------------------------------------
# docs/WORK_ORDER_access_facts.md Parts B/C -- live-resolution helpers
# (adapter_proxmox/resolve.py) + AdapterConfig.resolve_o2_endpoints
# ---------------------------------------------------------------------------


class _ResolveRest:
    """Fake RestClient for `resolve.py`/`resolve_o2_endpoints` tests: replies
    per-path, accepts (and ignores) `timeout_s` like the real `RestClient`."""

    def __init__(self, *, status=None, iface_result="RAISE", config_body=None,
                 resources=None, raise_status=False):
        self.status = status
        self.iface_result = iface_result
        self.config_body = config_body or {}
        self.resources = resources or []
        self.raise_status = raise_status
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, *, params=None, body=None, timeout_s=60):
        self.calls.append((method, path))
        if path.endswith("/status/current"):
            if self.raise_status:
                raise AdapterError("down", error_class="proxmox-api-transport")
            return self.status
        if path.endswith("/agent/network-get-interfaces"):
            if self.iface_result == "RAISE":
                raise AdapterError("no agent", error_class="proxmox-api-http")
            return self.iface_result
        if path.endswith("/config") and "/qemu/" in path:
            return self.config_body
        if path == "/cluster/resources":
            return self.resources
        return {}


def _resolve_cfg(**kw):
    return AdapterConfig(
        proxmox_api_url="https://test:8006/api2/json",
        proxmox_api_token="PVEAPIToken=x@y!z=s",
        default_node="px1",
        **kw,
    )


def test_resolve_vm_name_returns_none_when_cluster_unconfigured():
    from axrange.adapter_proxmox.resolve import resolve_vm_name

    cfg = AdapterConfig()  # no proxmox_api_url / default_node
    assert resolve_vm_name(cfg, 100) is None


def test_resolve_vm_name_success():
    from axrange.adapter_proxmox.resolve import resolve_vm_name

    client = _ResolveRest(status={"name": "te-abc123-h-a", "status": "running"})
    cfg = _resolve_cfg()
    assert resolve_vm_name(cfg, 100, client=client) == "te-abc123-h-a"


def test_resolve_vm_ip_via_guest_agent():
    from axrange.adapter_proxmox.resolve import resolve_vm_ip

    iface_result = {
        "result": [
            {"name": "lo", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}
            ]},
            {"name": "eth0", "ip-addresses": [
                {"ip-address-type": "ipv6", "ip-address": "fe80::1"},
                {"ip-address-type": "ipv4", "ip-address": "10.60.1.5"},
            ]},
        ]
    }
    client = _ResolveRest(iface_result=iface_result)
    cfg = _resolve_cfg()
    assert resolve_vm_ip(cfg, 100, client=client) == "10.60.1.5"


def test_resolve_vm_ip_falls_back_to_static_ipconfig_when_no_agent():
    from axrange.adapter_proxmox.resolve import resolve_vm_ip

    client = _ResolveRest(iface_result="RAISE", config_body={"ipconfig0": "ip=10.60.1.9/24,gw=10.60.1.1"})
    cfg = _resolve_cfg()
    assert resolve_vm_ip(cfg, 100, client=client) == "10.60.1.9"


def test_resolve_vm_ip_returns_none_on_unreachable_cluster():
    """Best-effort + graceful: an unreachable cluster degrades to None, never
    raises (docs/WORK_ORDER_access_facts.md guardrail)."""
    from axrange.adapter_proxmox.resolve import resolve_vm_ip

    class _AllRaise:
        def request(self, method, path, *, params=None, body=None, timeout_s=60):
            raise AdapterError("unreachable", error_class="proxmox-api-transport")

    cfg = _resolve_cfg()
    assert resolve_vm_ip(cfg, 100, client=_AllRaise()) is None


def test_resolve_vm_ip_returns_none_on_read_phase_oserror():
    """SHOULD-FIX 1 (code review, 2026-07-25): `RestClient.request` only
    wraps CONNECT-phase urllib failures into `AdapterError` -- a READ-phase
    failure against a reachable-but-hung host (`socket.timeout`,
    `ConnectionResetError`, ... all bare `OSError`) must ALSO degrade to
    `None`, not crash `te info`/`o2 info`/`tap info`."""
    from axrange.adapter_proxmox.resolve import resolve_vm_ip

    class _ReadTimeoutRest:
        def request(self, method, path, *, params=None, body=None, timeout_s=60):
            raise OSError("read timed out")

    cfg = _resolve_cfg()
    assert resolve_vm_ip(cfg, 100, client=_ReadTimeoutRest()) is None


def test_resolve_vm_name_returns_none_on_read_phase_oserror():
    from axrange.adapter_proxmox.resolve import resolve_vm_name

    class _ReadTimeoutRest:
        def request(self, method, path, *, params=None, body=None, timeout_s=60):
            raise OSError("read timed out")

    cfg = _resolve_cfg()
    assert resolve_vm_name(cfg, 100, client=_ReadTimeoutRest()) is None


def test_resolve_vmid_by_name_returns_none_on_read_phase_oserror():
    from axrange.adapter_proxmox.resolve import resolve_vmid_by_name

    class _ReadTimeoutRest:
        def request(self, method, path, *, params=None, body=None, timeout_s=60):
            raise OSError("read timed out")

    cfg = _resolve_cfg()
    assert resolve_vmid_by_name(cfg, "o2-services", client=_ReadTimeoutRest()) is None


def test_resolve_vmid_by_name_match():
    from axrange.adapter_proxmox.resolve import resolve_vmid_by_name

    client = _ResolveRest(resources=[
        {"vmid": 101, "name": "other-vm", "node": "px1"},
        {"vmid": 102, "name": "o2-services", "node": "px2"},
    ])
    cfg = _resolve_cfg()
    assert resolve_vmid_by_name(cfg, "o2-services", client=client) == (102, "px2")


def test_resolve_vmid_by_name_no_match_returns_none():
    from axrange.adapter_proxmox.resolve import resolve_vmid_by_name

    client = _ResolveRest(resources=[{"vmid": 101, "name": "other-vm", "node": "px1"}])
    cfg = _resolve_cfg()
    assert resolve_vmid_by_name(cfg, "o2-services", client=client) is None


def test_resolve_o2_endpoints_explicit_url_wins_over_services_vm(monkeypatch):
    """An explicit O2_<SVC>_URL wins for ITS OWN service even when a services
    VM is also declared (which the OTHER, unset services still resolve
    against)."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.0.0.90")
    cfg = _resolve_cfg(
        o2_endpoints={"caldera": "http://override:8888"},
        o2_services_vmid=102,
    )
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["caldera"] == "http://override:8888"
    assert endpoints["grafana"] == "http://10.0.0.90:3000"


def test_resolve_o2_endpoints_not_configured_without_services_vm():
    cfg = AdapterConfig()
    endpoints = cfg.resolve_o2_endpoints()
    expected = (
        "(not configured — set O2_SERVICES_VMID or a per-service O2_<SVC>_URL "
        "in adapter.conf)"
    )
    assert endpoints == {name: expected for name in
                         ("grafana", "loki", "prometheus", "caldera", "wazuh")}


def test_resolve_o2_endpoints_resolves_ip_from_services_vmid(monkeypatch):
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.0.0.90")
    cfg = _resolve_cfg(o2_services_vmid=102)
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["grafana"] == "http://10.0.0.90:3000"
    assert endpoints["wazuh"] == "https://10.0.0.90:443"


def test_resolve_o2_endpoints_port_override(monkeypatch):
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.0.0.90")
    cfg = _resolve_cfg(o2_services_vmid=102, o2_service_ports={"grafana": 4000})
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["grafana"] == "http://10.0.0.90:4000"


def test_resolve_o2_endpoints_unresolved_ip_degrades_gracefully(monkeypatch):
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: None)
    cfg = _resolve_cfg(o2_services_vmid=102)
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["grafana"] == "(services vmid=102 — IP unresolved)"


def test_resolve_o2_endpoints_resolves_via_services_name_when_no_vmid(monkeypatch):
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "resolve_vmid_by_name", lambda cfg, name, **kw: (102, "px2")
    )
    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.0.0.91")
    cfg = _resolve_cfg(o2_services_name="o2-services")
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["loki"] == "http://10.0.0.91:3100"


def test_resolve_o2_endpoints_services_name_not_found_degrades_gracefully(monkeypatch):
    """A services VM declared by NAME (no vmid) that `resolve_vmid_by_name`
    can't find (typo'd name, VM renamed/gone, unreachable cluster) degrades
    to the "(services vmid=... — IP unresolved)" label using the NAME
    (there's no vmid to show) -- never raises, and never calls
    `resolve_vm_ip` at all since there's no vmid to look an IP up for."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vmid_by_name", lambda cfg, name, **kw: None)

    def _boom(cfg, vmid, **kw):
        raise AssertionError("resolve_vm_ip must not be called with no resolved vmid")

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", _boom)
    cfg = _resolve_cfg(o2_services_name="ghost-vm")
    endpoints = cfg.resolve_o2_endpoints()
    assert endpoints["grafana"] == "(services vmid=ghost-vm — IP unresolved)"
