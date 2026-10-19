"""E1 — egress phase policy + sink next-hop routing.

`docs/WORK_ORDER_protocol_engines.md` §E1. What these tests hold down:

  * **default CLOSED** — no phase anywhere means `measure`, in the renderer AND in
    the in-guest engine's own fallback chain;
  * the **rendered state per phase** — `measure` drops WAN forwarding and policy-
    routes everything else at the sink; `provision` restores the masquerade;
  * an **unobserved** external address still reaches the sink in `measure` (the
    default-route decision, as opposed to the per-prefix one);
  * **idempotent re-apply** — every write is a full rebuild, never an append;
  * the **VyOS branch refuses loudly** rather than no-oping (LES-001);
  * `configure-router` without an `egress` input touches nothing (the 8 shipped
    twin configs keep today's behavior).
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from axrange.adapter_proxmox import AdapterConfig
from axrange.adapter_proxmox.egress_render import (
    DEFAULT_EGRESS_PHASE,
    EGRESS_CONF,
    EGRESS_ENGINE,
    EGRESS_INSTALLER,
    EGRESS_OK_MARKER,
    EGRESS_PROBE_DST,
    EGRESS_TABLE,
    EgressRenderError,
    healthcheck_command,
    normalize_egress,
    normalize_phase,
    phase_set_command,
    render_conf,
    render_engine,
    render_install,
)
from axrange.adapter_proxmox.rest import RestBackend
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext

REPO = Path(__file__).resolve().parents[1]
SINK = "10.20.0.9"


def _egress(**over):
    spec = {"phase": "measure", "sink_ip": SINK}
    spec.update(over)
    return spec


# ---------------------------------------------------------------------------
# Default CLOSED
# ---------------------------------------------------------------------------


def test_missing_phase_is_measure():
    """A twin that never gets told otherwise must not reach the internet."""
    assert DEFAULT_EGRESS_PHASE == "measure"
    assert normalize_phase(None) == "measure"
    assert normalize_phase("") == "measure"
    assert normalize_phase("   ") == "measure"
    # ...and a policy declared with no phase at all renders the closed one.
    assert normalize_egress({"sink_ip": SINK})["phase"] == "measure"
    assert f"{EGRESS_ENGINE} measure" in render_install({"sink_ip": SINK})


def test_unknown_phase_is_a_loud_error_not_a_quiet_demotion():
    """A typo must not silently become a different policy (LES-001). Closed is
    the right *default*, but not the right answer to `phase: measrue`."""
    with pytest.raises(EgressRenderError, match="not one of"):
        normalize_phase("measrue")
    with pytest.raises(EgressRenderError):
        normalize_egress(_egress(phase="off"))
    with pytest.raises(EgressRenderError):
        phase_set_command("open")


def test_engine_falls_back_to_measure_with_no_state_file():
    """The in-guest fallback chain: argv, then the persisted phase, then the
    conf's initial phase, then `measure`."""
    engine = render_engine()
    resolve = engine.split("axr_resolve_phase() {", 1)[1].split("}\n", 1)[0]
    assert "axr_persisted_phase" in resolve
    assert 'printf \'%s\\n\' "measure"' in resolve
    # An unparsable state file is not honoured either.
    persisted = engine.split("axr_persisted_phase() {", 1)[1].split("\n}", 1)[0]
    assert "provision|measure)" in persisted
    assert "*) return 1" in persisted


def test_egress_policy_requires_a_sink():
    with pytest.raises(EgressRenderError, match="sink_ip"):
        normalize_egress({"phase": "measure"})
    with pytest.raises(EgressRenderError, match="not an IPv4"):
        normalize_egress({"phase": "measure", "sink_ip": "sink.twin.lab"})
    # A provision-phase router records the sink too, so the flip needs no new input.
    assert normalize_egress(_egress(phase="provision"))["sink_ip"] == SINK


def test_no_policy_means_no_policy():
    """`None` in, `None` out — the router keeps today's permissive behavior."""
    assert normalize_egress(None) is None


# ---------------------------------------------------------------------------
# The rendered state per phase
# ---------------------------------------------------------------------------


def test_measure_drops_wan_forwarding_both_directions():
    engine = render_engine()
    fw = engine.split("axr_fw_measure() {", 1)[1].split("\naxr_fw_measure_present", 1)[0]
    # nft branch: forwarded traffic to or from the WAN is dropped, with counters
    # so "zero WAN-forwarded packets" is directly readable.
    assert 'oifname "$_w" counter drop comment "axr-egress-measure-wan-out-drop"' in fw
    assert 'iifname "$_w" counter drop comment "axr-egress-measure-wan-in-drop"' in fw
    # The router's OWN new connections out the WAN die too (so apt on the router
    # fails loud), but DHCP and already-established flows survive the flip.
    assert 'oifname "$_w" udp dport 67 counter accept' in fw
    assert 'oifname "$_w" ct state established,related counter accept' in fw
    assert 'oifname "$_w" counter drop comment "axr-egress-measure-wan-self-drop"' in fw
    # iptables fallback for an image without nft — same semantics, not a no-op.
    assert 'iptables -A AXR_EGRESS_F -o "$_w" -j DROP' in fw
    assert 'iptables -A AXR_EGRESS_F -i "$_w" -j DROP' in fw
    assert "refusing to claim containment" in fw  # neither tool present -> die


def test_measure_installs_the_sink_next_hop_route():
    """The integration point the sink cannot solve itself: the sink answers for
    any destination but not ARP for foreign addresses, so the ROUTER carries the
    next-hop route."""
    engine = render_engine()
    routes = engine.split("axr_routes_measure() {", 1)[1].split("\naxr_probe_dst", 1)[0]
    assert (
        'ip route replace default via "$AXR_EGRESS_SINK" dev "$_dev" '
        "table $AXR_TABLE" in routes
    )
    # Inter-segment LAN traffic must NOT be swallowed by that default route: main
    # is consulted first with its own default suppressed.
    assert "lookup main suppress_prefixlength 0" in routes
    assert "lookup $AXR_TABLE" in routes
    # The sink's own traffic is never bounced back at the sink (no routing loop).
    assert 'ip rule add from "$AXR_EGRESS_SINK" priority $AXR_PRIO_SINK_SELF lookup main' in routes
    # A sink that is only reachable out the WAN is a twin-config error, not a
    # thing to route at.
    assert "resolves out the WAN interface" in routes


def test_measure_applies_the_drop_before_the_routes():
    """Fail-safe ordering: if the route step dies we are closed, never open."""
    apply_measure = render_engine().split("axr_apply_measure() {", 1)[1].split("\n}", 1)[0]
    assert apply_measure.index("axr_fw_measure") < apply_measure.index("axr_routes_measure")
    assert "DROP FIRST" in apply_measure


def test_unobserved_external_address_still_reaches_the_sink_in_measure():
    """The default-route decision, asserted in-guest. TEST-NET-3 (RFC 5737) is an
    address no cast can have observed; a FIB lookup as if forwarding it from the
    LAN must answer with the sink as next hop."""
    engine = render_engine()
    assert EGRESS_PROBE_DST.startswith("203.0.113.")  # TEST-NET-3
    probe = engine.split("axr_probe_dst() {", 1)[1].split("\n}", 1)[0]
    assert "$AXR_PROBE_DST" in probe
    assert_measure = engine.split("axr_assert_measure() {", 1)[1].split("\naxr_assert_provision", 1)[0]
    assert 'ip route get "$_dst" iif "$_lan" from "$_src"' in assert_measure
    assert 'grep -q "via $AXR_EGRESS_SINK"' in assert_measure
    assert "does NOT route to the sink" in assert_measure
    # Every LAN leg, not a sample; and never the router's own address as source.
    assert "for _lan in $(axr_lan_ifaces)" in assert_measure
    assert '_src=$(axr_probe_src "$_lan")' in assert_measure
    assert "head -n 1" not in assert_measure


# ---------------------------------------------------------------------------
# The measure assertion, EXECUTED against a fake `ip` that behaves like the
# kernel (corp-twin devlab live run 2): `ip route get D iif X from <a local
# address>` is EINVAL on Linux (martian source), whatever the policy is.
# ---------------------------------------------------------------------------

_FAKE_IP = r"""#!/bin/sh
# ADDRS: "dev addr/len" lines; STEERED: legs carrying the 1000/1001 rules.
case "$*" in
  "-4 route show default") echo "default via 192.0.2.1 dev eth9 proto dhcp" ;;
  "-4 -o addr show scope global"|"-4 -o addr show")
      echo "$FAKE_ADDRS" | while read -r d a; do
          [ -n "$d" ] && echo "2: $d    inet $a brd x scope global $d"; done ;;
  "-4 -o addr show dev "*" scope global")
      _d=$6
      echo "$FAKE_ADDRS" | while read -r d a; do
          [ "$d" = "$_d" ] && echo "2: $d    inet $a brd x scope global $d"; done ;;
  "rule show") echo "1001: from all iif eth0 lookup 210" ;;
  "route show table 210") echo "default via $FAKE_SINK dev eth0" ;;
  "route get "*)
      _dst=$3; _if=$5; _src=$7
      for _l in $(echo "$FAKE_ADDRS" | awk '{ split($2, a, "/"); print a[1] }'); do
          if [ "$_l" = "$_src" ]; then echo "RTNETLINK answers: Invalid argument" >&2; exit 2; fi
      done
      case " $FAKE_STEERED " in
        *" $_if "*) echo "$_dst from $_src via $FAKE_SINK dev eth0 table 210" ;;
        *) echo "$_dst from $_src via 192.0.2.1 dev eth9" ;;
      esac ;;
  *) exit 0 ;;
esac
"""

_FAKE_NFT = "#!/bin/sh\necho 'oifname \"eth9\" counter drop comment \"axr-egress-measure-wan-out-drop\"'\n"


def _run_assert_measure(tmp_path, *, addrs, steered, sink="10.250.234.66"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("ip", _FAKE_IP), ("nft", _FAKE_NFT), ("ping", "#!/bin/sh\nexit 1\n")):
        (bindir / name).write_text(body, encoding="utf-8")
        (bindir / name).chmod(0o755)
    prefix = render_engine().split('AXR_CMD="${1:-}"', 1)[0]
    script = tmp_path / "assert.sh"
    script.write_text(
        prefix + f"\nAXR_WAN_CACHE={tmp_path}/wan\nAXR_EGRESS_SINK={sink}\n"
        "axr_assert_measure\n",
        encoding="utf-8",
    )
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "FAKE_ADDRS": "\n".join(f"{d} {a}" for d, a in addrs),
        "FAKE_STEERED": " ".join(steered),
        "FAKE_SINK": sink,
    }
    return subprocess.run(["sh", str(script)], capture_output=True, text=True,
                          timeout=30, env=env)


_CORP_LIKE = [
    ("eth0", "10.250.234.65/28"),   # the sink's own segment
    ("eth1", "10.180.0.1/20"),
    ("eth2", "10.180.32.1/31"),     # a point-to-point leg
    ("eth3", "10.180.40.1/32"),     # nothing to probe from
    ("eth9", "192.0.2.10/24"),      # the WAN
]


def test_measure_assertion_passes_on_a_correctly_steered_router(tmp_path):
    proc = _run_assert_measure(tmp_path, addrs=_CORP_LIKE, steered=["eth0", "eth1", "eth2"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    # never the router's own address, never the sink: the next free host
    assert "from 10.250.234.67 iif eth0 -> sink" in out
    assert "from 10.180.0.2 iif eth1 -> sink" in out
    assert "from 10.180.32.0 iif eth2 -> sink" in out   # /31: the other end
    assert "skip eth3" in out
    assert "iif eth9" not in out                          # the WAN is not a LAN leg


def test_measure_assertion_fails_when_any_leg_is_not_steered(tmp_path):
    """The old probe sampled ONE leg; a leg missing its rules is real egress."""
    proc = _run_assert_measure(tmp_path, addrs=_CORP_LIKE, steered=["eth0", "eth2"])
    assert proc.returncode != 0
    assert "FAIL 203.0.113.9 from 10.180.0.2 iif eth1 does NOT route to the sink" in proc.stdout


def test_measure_assertion_fails_with_no_probeable_leg(tmp_path):
    proc = _run_assert_measure(
        tmp_path, addrs=[("eth3", "10.180.40.1/32"), ("eth9", "192.0.2.10/24")], steered=["eth3"],
    )
    assert proc.returncode != 0
    assert "FAIL no LAN interface with an address to probe from" in proc.stdout


def test_cidrs_mode_is_per_prefix_and_states_the_trade_off():
    ctx = normalize_egress(
        _egress(mode="cidrs", cidrs=["203.0.113.0/24", "198.51.100.0/24", "# a comment"])
    )
    assert ctx["cidrs"] == ["203.0.113.0/24", "198.51.100.0/24"]
    engine = render_engine()
    routes = engine.split("axr_routes_measure() {", 1)[1].split("\naxr_probe_dst", 1)[0]
    assert 'ip route replace "$_c" via "$AXR_EGRESS_SINK"' in routes
    # The reason `default` is the default is written down where it is decided.
    assert "black hole IS the signal here" in routes
    assert "no corpus ever saw" in routes
    # The prefix list ships with the installer and is required, never improvised.
    installer = render_install(ctx)
    assert "203.0.113.0/24" in installer
    assert "mode=cidrs but" in installer  # the engine's fail-loud on a missing file
    with pytest.raises(EgressRenderError, match="non-empty `cidrs`"):
        normalize_egress(_egress(mode="cidrs"))
    with pytest.raises(EgressRenderError, match="not a CIDR"):
        normalize_egress(_egress(mode="cidrs", cidrs=["not-a-prefix"]))


def test_cidrs_mode_probes_a_listed_prefix_not_test_net_3():
    """In narrow mode an unobserved address is DELIBERATELY unreachable, so the
    in-guest assertion must probe something the mode actually serves."""
    probe = render_engine().split("axr_probe_dst() {", 1)[1].split("\n}", 1)[0]
    assert '[ "$AXR_EGRESS_MODE" = cidrs ]' in probe
    assert "$AXR_CIDRS" in probe


def test_provision_restores_working_masquerade_egress():
    engine = render_engine()
    apply_prov = engine.split("axr_apply_provision() {", 1)[1].split("\n}", 1)[0]
    # configure-router's own NAT helper is re-asserted (it is idempotent), the
    # sink policy routes come down, and the WAN drop goes away.
    assert "/usr/local/sbin/axr-router-nat.sh" in apply_prov
    assert "axr_routes_clear" in apply_prov
    assert "axr_fw_provision" in apply_prov
    # ...and it FAILS if egress did not actually come back.
    assert "axr_assert_provision || axr_die" in apply_prov
    assert_prov = engine.split("axr_assert_provision() {", 1)[1].split("\naxr_apply_measure", 1)[0]
    assert "no LAN->WAN masquerade" in assert_prov
    assert "measure-phase WAN drop is still installed" in assert_prov
    assert "sink policy rule is still installed" in assert_prov
    # `provision` leaves a present-but-empty marker table: "allowed on purpose"
    # and "nothing ever ran" must not look the same.
    fw_prov = engine.split("axr_fw_provision() {", 1)[1].split("\naxr_fw_measure_present", 1)[0]
    assert 'counter comment "axr-egress-provision-allow"' in fw_prov


def test_a_failed_provision_transition_lands_closed():
    engine = render_engine()
    assert "forced back to measure" in engine
    # measure records its state BEFORE applying; provision only AFTER success.
    prov_branch = engine.split("    provision)\n", 1)[1].split("        ;;", 1)[0]
    assert prov_branch.index("axr_apply_provision") < prov_branch.index("axr_record provision")
    assert "axr_record measure" in prov_branch


# ---------------------------------------------------------------------------
# Idempotency + determinism
# ---------------------------------------------------------------------------


def test_render_is_deterministic():
    spec = _egress(mode="cidrs", cidrs=["203.0.113.0/24"])
    assert render_install(spec) == render_install(spec)
    assert render_engine() == render_engine()
    assert render_conf(_egress()) == render_conf(_egress())


def test_reapply_is_idempotent_by_construction():
    """Every apply REBUILDS its state rather than appending to it — a second run
    can never double a rule or a route."""
    engine = render_engine()
    # nft: one atomic add-delete-define batch per apply.
    assert engine.count("add table ip axregress\ndelete table ip axregress") == 2
    # iptables fallback: a private chain, flushed before it is refilled, jumped
    # in only when the jump is absent.
    assert "iptables -F AXR_EGRESS_F" in engine
    assert "iptables -C FORWARD -j AXR_EGRESS_F 2>/dev/null || iptables -I FORWARD 1" in engine
    # routes: rules deleted until none remain, table flushed, `replace` not `add`.
    clear = engine.split("axr_routes_clear() {", 1)[1].split("\n}", 1)[0]
    assert clear.count("while ip rule del priority") == 3
    assert "ip route flush table $AXR_TABLE" in clear
    assert "ip route add " not in engine  # `replace` everywhere
    # installer: full overwrites and a no-op-when-enabled unit.
    installer = render_install(_egress())
    assert f"cat > {EGRESS_ENGINE} <<'AXR_EGRESS_ENGINE_EOF'" in installer
    assert f"cat > {EGRESS_CONF} <<'AXR_EGRESS_CONF_EOF'" in installer
    assert "systemctl enable axr-egress-phase.service" in installer


def test_policy_is_reboot_persistent():
    """LES-013's shape: nft rules, ip rules and a routing table are runtime state,
    so a `measure` twin that reboots must not come back on the real internet."""
    installer = render_install(_egress())
    assert "/etc/systemd/system/axr-egress-phase.service" in installer
    assert "After=network-online.target" in installer
    assert "RemainAfterExit=yes" in installer
    assert f"ExecStart={EGRESS_ENGINE}" in installer
    # The WAN name is cached, so a lapsed DHCP lease cannot leave the twin open
    # by making the WAN interface unnameable at boot.
    assert "AXR_WAN_CACHE" in render_engine()


@pytest.mark.parametrize(
    "spec",
    [
        {"sink_ip": SINK},
        {"sink_ip": SINK, "phase": "provision"},
        {"sink_ip": SINK, "phase": "measure", "mode": "cidrs", "cidrs": ["203.0.113.0/24"]},
    ],
)
def test_rendered_shell_is_syntactically_valid(spec, tmp_path):
    """`sh -n` the real text — a heredoc or quoting slip in a containment script
    is not something to discover on a cluster."""
    for name, text in (("engine.sh", render_engine()), ("install.sh", render_install(spec))):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            ["sh", "-n", str(path)], capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_engine_exposes_a_healthcheck_and_a_flip_command():
    assert healthcheck_command() == f"{EGRESS_ENGINE} --healthcheck"
    assert phase_set_command("measure") == f"{EGRESS_ENGINE} measure"
    engine = render_engine()
    assert "--healthcheck)" in engine
    assert "--show)" in engine
    # --show is what labels a capture window (§6): phase + when it was entered.
    assert 'echo "phase=$_p since=' in engine


# ---------------------------------------------------------------------------
# The adapter wiring
# ---------------------------------------------------------------------------


class _Rest:
    def request(self, method, path, *, params=None, body=None):
        if method == "GET" and path.endswith("/config"):
            return {"net0": "virtio=AA:BB:CC:11:22:33,bridge=vmbr0"}
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


def _router_op(inputs):
    return OpInstance(
        op_id="p1:configure-router",
        op_name="configure-router",
        op_catalog_id="OP-HOST-090",
        kind=OpKind.ACTION,
        scope={"host": "gw"},
        idempotency_class=IdempotencyClass.INFRA,
        inputs_resolved=inputs,
    )


def _ctx(node="px1"):
    return ExecutionContext(
        plan_id="p1", deploy_id="d1",
        cluster=ClusterContext(default_node=node), placement={},
    )


def _fake_ga(monkeypatch, captured, *, installer_rc=0, installer_out=None):
    import axrange.adapter_proxmox.guest_agent as ga_mod
    from axrange.adapter_proxmox.guest_agent import GuestExecResult

    class _FakeGA:
        def __init__(self, client, node, **kw):
            pass

        def wait_ready(self, vmid, *, timeout_s=300):
            pass

        def write_file(self, vmid, path, content, *, mode=None):
            captured.setdefault("files", {})[path] = content
            captured.setdefault("modes", {})[path] = mode

        def exec(self, vmid, argv, *, timeout_s=300):
            captured.setdefault("execs", []).append(argv)
            if argv[0] == "/bin/sh":
                out = (
                    installer_out
                    if installer_out is not None
                    else f"{EGRESS_OK_MARKER} phase=measure"
                )
                return GuestExecResult(exit_code=installer_rc, stdout=out, stderr="")
            return GuestExecResult(
                exit_code=0, stdout="AXR_ROUTER_OK wan=eth0 forward=1", stderr=""
            )

    monkeypatch.setattr(ga_mod, "GuestAgent", _FakeGA)


def test_configure_router_without_egress_touches_nothing(monkeypatch):
    """Backward compatibility, deliberately: the shipped twin configs declare no
    egress policy and must keep today's permissive masquerade."""
    captured: dict = {}
    _fake_ga(monkeypatch, captured)
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700})
    out = backend.op_configure_router(op, op.inputs_resolved, _ctx())

    assert out == {"configured": True}
    assert "egress_phase" not in out
    assert captured.get("files") is None
    assert all(argv[0] != "/bin/sh" for argv in captured["execs"])


def test_configure_router_applies_the_egress_policy(monkeypatch):
    captured: dict = {}
    _fake_ga(monkeypatch, captured)
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700, "egress": _egress()})
    out = backend.op_configure_router(op, op.inputs_resolved, _ctx())

    assert out["configured"] is True
    assert out["egress_phase"] == "measure"
    installer = captured["files"][EGRESS_INSTALLER]
    assert captured["modes"][EGRESS_INSTALLER] == "0755"
    assert f"AXR_EGRESS_SINK={SINK}" in installer
    assert captured["execs"][-1] == ["/bin/sh", EGRESS_INSTALLER]


def test_configure_router_fails_loud_when_the_phase_does_not_apply(monkeypatch):
    """No marker in the output = the phase was not reached. The op must not
    report a phase the router is not actually in."""
    captured: dict = {}
    _fake_ga(monkeypatch, captured, installer_rc=1, installer_out="axr-egress: sink unreachable")
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700, "egress": _egress()})
    with pytest.raises(AdapterError) as ei:
        backend.op_configure_router(op, op.inputs_resolved, _ctx())
    assert ei.value.error_class == "egress-phase-failed"

    # ...and a zero exit with the WRONG phase in the marker is equally a failure.
    captured2: dict = {}
    _fake_ga(monkeypatch, captured2, installer_out=f"{EGRESS_OK_MARKER} phase=provision")
    with pytest.raises(AdapterError, match="did not apply"):
        backend.op_configure_router(_router_op({"vmid": 700, "egress": _egress()}),
                                    {"vmid": 700, "egress": _egress()}, _ctx())


def test_configure_router_rejects_a_malformed_policy(monkeypatch):
    captured: dict = {}
    _fake_ga(monkeypatch, captured)
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700, "egress": {"phase": "measure"}})  # no sink
    with pytest.raises(AdapterError) as ei:
        backend.op_configure_router(op, op.inputs_resolved, _ctx())
    assert ei.value.error_class == "egress-policy-invalid"


def test_vyos_router_with_an_egress_policy_refuses_loudly(monkeypatch):
    """LES-001: never silently no-op. A VyOS router asked to contain egress must
    fail the deploy, not bring up NAT and skip the containment half."""
    import axrange.adapter_proxmox.ssh as ssh_mod

    def _boom(*a, **kw):  # the VyOS path must not get this far
        raise AssertionError("VyOS egress policy must refuse before touching SSH")

    monkeypatch.setattr(ssh_mod, "discover_guest_ip_by_mac", _boom)
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700, "router_os": "vyos", "egress": _egress()})
    with pytest.raises(AdapterError) as ei:
        backend.op_configure_router(op, op.inputs_resolved, _ctx())
    assert ei.value.error_class == "egress-phase-unsupported-os"
    assert "not implemented for VyOS" in str(ei.value)


def test_vyos_router_without_an_egress_policy_is_unchanged(monkeypatch):
    """The refusal is scoped to routers that actually declare a policy."""
    import axrange.adapter_proxmox.ssh as ssh_mod

    monkeypatch.setattr(ssh_mod, "discover_guest_ip_by_mac", lambda cfg, mac, **kw: "10.80.72.99")
    monkeypatch.setattr(ssh_mod, "apply_vyos_sets", lambda *a, **kw: None)
    monkeypatch.setattr(ssh_mod, "run_ssh_guest", lambda *a, **kw: (0, "default via 10.80.72.1 dev eth0"))
    backend = RestBackend(AdapterConfig(), client=_Rest())
    op = _router_op({"vmid": 700, "router_os": "vyos"})
    out = backend.op_configure_router(op, op.inputs_resolved, _ctx())
    assert out["configured"] is True
    assert "egress_phase" not in out


def test_op_catalog_signature_declares_the_new_input_and_output():
    """E1 EXTENDS OP-HOST-090 rather than minting an op (the work order's
    preference: a new op type costs an OP_CATALOG version bump)."""
    op_def = get_op("configure-router")
    assert "egress" in op_def.inputs
    assert "egress_phase" in op_def.outputs
    assert op_def.id == "OP-HOST-090"  # id unchanged


# ---------------------------------------------------------------------------
# The twin-config delivery path (`scripts/gen_egress_policy.py`)
# ---------------------------------------------------------------------------


def _load_gen():
    path = REPO / "scripts" / "gen_egress_policy.py"
    spec = importlib.util.spec_from_file_location("gen_egress_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_generator_writes_a_self_contained_installer(tmp_path):
    """One `script:` action per app is the limit (`pass1_emit._classify_install`),
    so the twin-config payload carries the engine AND its data AND the apply."""
    gen = _load_gen()
    rc = gen.main(["--sink", SINK, "--out", str(tmp_path), "--phase", "provision"])
    assert rc == 0
    path = tmp_path / gen.INSTALLER_NAME
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert path.stat().st_mode & 0o111
    assert f"AXR_EGRESS_SINK={SINK}" in text
    assert f"{EGRESS_ENGINE} provision" in text
    # Same inputs in, byte-identical file out.
    gen.main(["--sink", SINK, "--out", str(tmp_path), "--phase", "provision"])
    assert path.read_text(encoding="utf-8") == text
    # ...and it is the SAME text the adapter delivers — one source, two paths.
    assert text == "#!/bin/sh\n" + render_install(
        {"phase": "provision", "sink_ip": SINK}
    ).split("\n", 1)[1]


def test_generator_cidrs_mode_needs_a_prefix_file(tmp_path):
    gen = _load_gen()
    with pytest.raises(SystemExit):
        gen.main(["--sink", SINK, "--out", str(tmp_path), "--mode", "cidrs"])
    cidrs = tmp_path / "external-cidrs.txt"
    cidrs.write_text("# generated\n203.0.113.0/24\n198.51.100.0/24\n", encoding="utf-8")
    assert gen.main(
        ["--sink", SINK, "--out", str(tmp_path), "--mode", "cidrs", "--cidrs", str(cidrs)]
    ) == 0
    text = (tmp_path / gen.INSTALLER_NAME).read_text(encoding="utf-8")
    assert "203.0.113.0/24" in text and "198.51.100.0/24" in text
    assert "AXR_EGRESS_MODE=cidrs" in text
    # The policy table id is baked once, in the engine, not per call site.
    assert f"AXR_TABLE={EGRESS_TABLE}" in text


# ---------------------------------------------------------------------------
# The nftables text itself
# ---------------------------------------------------------------------------


def test_rendered_nftables_blocks_parse():
    """`nft -c -f` the real rule text. The first draft of this renderer put the
    rule `comment` BEFORE the verdict (`counter comment "x" drop`), which nft
    rejects outright — a containment rule set that fails to load at apply time is
    exactly the failure this slice must not ship.

    `nft -c` still wants netlink access to build its cache, which an unprivileged
    run does not get; the PARSE errors are reported separately, so only those are
    inspected.
    """
    nft = shutil.which("nft")
    if nft is None:  # pragma: no cover — environment-dependent
        pytest.skip("nft not installed")
    blocks = re.findall(
        r"nft -f - <<AXR_NFT_EOF\n(.*?)\nAXR_NFT_EOF", render_engine(), re.S
    )
    assert len(blocks) == 2, "measure + provision rule sets"
    for block in blocks:
        proc = subprocess.run(
            [nft, "-c", "-f", "/dev/stdin"],
            input=block.replace('"$_w"', '"eth0"'),
            capture_output=True,
            text=True,
            timeout=30,
        )
        parse_errors = [
            line
            for line in proc.stderr.splitlines()
            if "Error:" in line and "cache initialization" not in line
        ]
        assert not parse_errors, parse_errors


# ---------------------------------------------------------------------------
# The AUTHORING path (GAP-101) — a twin declares its phase; nothing is applied
# by hand.
#
# E1 built the router-side mechanism with nowhere to ask for it. That gap is
# what let a real containment breach go unnoticed on 2026-09-21 (twin hosts
# reached the actual internet; `github.com` presented a Sectigo certificate
# instead of the lab CA). These tests hold down the link from a twin config to
# `configure-router`'s `egress` input, and the read-back that makes the posture
# visible without an ssh to the router.
# ---------------------------------------------------------------------------

_TWIN_NETS = (
    "scenario_schema_version: 1\n"
    "scenario: {{ id: {sid}, version: 1 }}\n"
    "layers:\n"
    "  infra:\n"
    "    networks:\n"
    "      - {{ id: corp, cidr: 10.66.20.0/24, vlan: 10, gateway: 10.66.20.1 }}\n"
    "      - {{ id: ext, cidr: 10.66.30.0/24, vlan: 11, gateway: 10.66.30.1 }}\n"
    "    nodes:\n"
    "{nodes}"
    "  apps: []\n"
    "  activity: []\n"
    "bindings: []\n"
    "{egress}"
)

_ROUTER_NODE = (
    "      - {{ id: gw, kind: router, nets: [{{ net: corp }}, {{ net: ext }}],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }}\n"
)
_SINK_NODE = (
    "      - {{ id: sink, kind: host, role: sink, nets: [{{ net: ext, ip: 10.66.30.10 }}],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }}\n"
)


def _plan_for(manifests, *, sid="contained", nodes=None, egress=""):
    from axrange.ir.parser import parse_ir as _parse
    from axrange.planner import build_plan

    text = _TWIN_NETS.format(
        sid=sid,
        nodes=(nodes if nodes is not None else _ROUTER_NODE + _SINK_NODE).format(),
        egress=egress,
    )
    parsed = _parse(text=text)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    return build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)


def _router_input(plan):
    op = next(o for o in plan.ops.values() if o.op_name == "configure-router")
    return op.inputs_resolved


def test_declared_phase_reaches_the_router_op_input(manifests):
    """The whole of GAP-101 in one assertion: a twin says `egress:` and the
    router's op input carries it. Before this, the phase had no schema slot
    anywhere between the twin config and the adapter input that already
    existed."""
    plan = _plan_for(manifests, egress="egress: { phase: measure }\n")
    assert _router_input(plan)["egress"] == {
        "phase": "measure", "sink_ip": "10.66.30.10", "mode": "default", "cidrs": [],
    }


def test_sink_ip_is_resolved_from_the_role_sink_node(manifests):
    """The address is stated ONCE, on the node that owns it. A second
    hand-typed copy in the egress block is a fact that can drift, and a
    `measure` phase pointed at a stale address black-holes the twin while
    reporting itself contained."""
    plan = _plan_for(manifests, egress="egress: { phase: measure }\n")
    assert _router_input(plan)["egress"]["sink_ip"] == "10.66.30.10"


def test_explicit_sink_ip_overrides_resolution(manifests):
    plan = _plan_for(
        manifests, egress="egress: { phase: measure, sink_ip: 10.66.30.99 }\n"
    )
    assert _router_input(plan)["egress"]["sink_ip"] == "10.66.30.99"


def test_named_sink_node_wins_over_the_role_scan(manifests):
    nodes = (
        _ROUTER_NODE
        + _SINK_NODE
        + "      - {{ id: other, kind: host, nets: [{{ net: ext, ip: 10.66.30.44 }}],\n"
          "          image: debian-12-cloud@v1, requires: [linux] }}\n"
    )
    plan = _plan_for(
        manifests, nodes=nodes, egress="egress: { phase: measure, sink: other }\n"
    )
    assert _router_input(plan)["egress"]["sink_ip"] == "10.66.30.44"


def test_declared_without_a_phase_is_measure(manifests):
    """Default CLOSED, stated in the MODEL and not only in the renderer: once
    a twin says the word `egress:`, the fail-safe direction is containment."""
    plan = _plan_for(manifests, egress="egress: {}\n")
    assert _router_input(plan)["egress"]["phase"] == "measure"


def test_measure_with_no_resolvable_sink_fails_loud_at_plan_time(manifests):
    """A measure phase pointed at nothing is the failure GAP-101 is about.
    Caught at `te plan` (cluster-free), not mid-deploy after the VMs exist."""
    from axrange.planner import PlanBuildError

    with pytest.raises((PlanBuildError, Exception)) as exc:
        _plan_for(
            manifests, nodes=_ROUTER_NODE, egress="egress: { phase: measure }\n"
        )
    msg = str(exc.value)
    assert "role: sink" in msg and "sink_ip" in msg


def test_two_sinks_are_ambiguous_not_guessed(manifests):
    nodes = (
        _ROUTER_NODE
        + _SINK_NODE
        + "      - {{ id: sink2, kind: host, role: sink, nets: [{{ net: ext, ip: 10.66.30.11 }}],\n"
          "          image: debian-12-cloud@v1, requires: [linux] }}\n"
    )
    with pytest.raises(Exception) as exc:
        _plan_for(manifests, nodes=nodes, egress="egress: { phase: measure }\n")
    assert "ambiguous" in str(exc.value)


def test_a_multi_homed_sink_needs_an_explicit_address(manifests):
    """Two legs, two possible next hops — refuse rather than pick one."""
    nodes = (
        _ROUTER_NODE
        + "      - {{ id: sink, kind: host, role: sink,\n"
          "          nets: [{{ net: ext, ip: 10.66.30.10 }}, {{ net: corp, ip: 10.66.20.10 }}],\n"
          "          image: debian-12-cloud@v1, requires: [linux] }}\n"
    )
    with pytest.raises(Exception) as exc:
        _plan_for(manifests, nodes=nodes, egress="egress: { phase: measure }\n")
    assert "multi-homed" in str(exc.value)


def test_a_declared_policy_with_no_router_is_refused(manifests):
    """The phase is enforced ON THE ROUTER. A routerless twin that declares
    containment would deploy green and contain nothing (LES-172)."""
    with pytest.raises(Exception) as exc:
        _plan_for(manifests, nodes=_SINK_NODE, egress="egress: { phase: measure }\n")
    assert "no `kind: router`" in str(exc.value)


def test_a_typo_phase_is_refused_at_plan_time(manifests):
    with pytest.raises(Exception) as exc:
        _plan_for(manifests, egress="egress: { phase: measuer }\n")
    assert "measuer" in str(exc.value)


def test_undeclared_twin_emits_no_egress_input_at_all(manifests):
    """UNSPECIFIED MEANS UNCHANGED — not closed. Every shipped twin config
    apt-installs over the router's permissive masquerade; a silent flip to
    `measure` would break all of them. The key must be ABSENT, not `None`:
    an unconditional key retro-classifies every standing deploy's op row as
    Mutated (LES-224)."""
    plan = _plan_for(manifests, sid="unspecified")
    assert "egress" not in _router_input(plan)


def test_vyos_router_with_a_declared_egress_refuses_at_plan_time(manifests):
    """The adapter already refuses a VyOS router carrying an egress policy;
    with a TWIN-level declaration the refusal has to happen at plan time too,
    or a VyOS router in a declaring twin silently forwards to the real WAN
    while the twin believes it is closed (LES-001)."""
    nodes = (
        "      - {{ id: gw, kind: router, nets: [{{ net: corp }}, {{ net: ext }}],\n"
        "          image: vyos-router@v1, requires: [vyos] }}\n"
    ) + _SINK_NODE
    with pytest.raises(Exception) as exc:
        _plan_for(manifests, nodes=nodes, egress="egress: { phase: measure }\n")
    assert "VyOS" in str(exc.value)


# ---- the twin-config authoring surface ------------------------------------


def test_twin_config_egress_block_expands_into_the_ir():
    from axrange.twinconfig.expand import expand_twin_config_raw

    ir_dict, _params = expand_twin_config_raw({
        "twin_config": {
            "id": "declaring-twin",
            "segments": [{"name": "ext", "cidr": "10.66.30.0/24", "zone_type": "simple"}],
            "nodes": [
                {"name": "gw", "kind": "router", "image": "debian-12-cloud@v1",
                 "requires": ["linux"], "nets": [{"segment": "ext"}]},
                {"name": "sink", "role": "sink", "image": "debian-12-cloud@v1",
                 "requires": ["linux"],
                 "nets": [{"segment": "ext", "ip": "10.66.30.10"}]},
            ],
            "egress": {"phase": "measure"},
        }
    })
    assert ir_dict["egress"] == {"phase": "measure"}


def test_twin_config_without_egress_emits_no_key():
    """The mechanism behind every shipped config's `plan_id` staying pinned
    (LES-126/LES-213): absent in, absent out — not `egress: null`, which
    would move `ir_hash` for all eight."""
    from axrange.twinconfig.expand import expand_twin_config_raw

    ir_dict, _params = expand_twin_config_raw({
        "twin_config": {
            "id": "quiet-twin",
            "segments": [{"name": "corp", "cidr": "10.0.0.0/24", "zone_type": "simple"}],
            "nodes": [
                {"name": "h", "image": "debian-12-cloud@v1", "requires": ["linux"],
                 "nets": [{"segment": "corp"}]},
            ],
        }
    })
    assert "egress" not in ir_dict


def test_shipped_svc_engines_declares_its_phase():
    """The seam this gap is named for: `twin-configs/svc-engines/` carried a
    hand-written SEAM comment telling the operator to push the policy by
    hand. It declares now."""
    from axrange.twinconfig import expand_twin_config

    ir_dict, _params = expand_twin_config("svc-engines")
    assert ir_dict["egress"]["phase"] == "provision"
    # ...and the sink address is NOT re-typed there.
    assert "sink_ip" not in ir_dict["egress"]


@pytest.mark.parametrize(
    "config_id",
    ["corp-enterprise", "datacenter", "dmz-perimeter", "ics-ot", "sme-flat", "svc-verify"],
)
def test_other_shipped_configs_declare_nothing(config_id):
    """Stated as its own assertion rather than left implicit in the `plan_id`
    pin (`tests/test_twinconfig_catalog.py`), so a regression says WHY a hash
    moved (LES-213)."""
    from axrange.twinconfig import expand_twin_config

    ir_dict, _params = expand_twin_config(config_id)
    assert "egress" not in ir_dict


def test_top_level_egress_key_is_known_to_the_validator():
    """STR-180 flags unknown top-level keys as info; `egress:` must not read
    as a typo now that it is a real block."""
    from axrange.validator.engine import validate_structural

    src = (
        "scenario_schema_version: 1\n"
        "scenario: { id: m, version: 1 }\n"
        "layers: { infra: { networks: [], nodes: [] }, apps: [], activity: [] }\n"
        "bindings: []\n"
        "egress: { phase: measure }\n"
    )
    result = validate_structural(text=src)
    assert "STR-180" not in {f.rule_id for f in result.findings}


# ---- the read-back: "is this twin contained?" without an ssh ---------------


class _PostureAdapter:
    """Canned outputs; `configure-router` reports the phase it REACHED (the
    op's real contract) so the read-back is exercised end to end, not from a
    hand-built row (LES-068)."""

    def __init__(self, reached="measure"):
        self.reached = reached
        self._vmid = 900100

    def run(self, op, resolved_inputs, ctx):
        if op.op_name == "clone-vm":
            self._vmid += 1
            return {"vmid": self._vmid}
        if op.op_name == "create-ovs-bridge":
            return {"bridge_name": "tebr_test"}
        if op.op_name == "configure-router":
            out = {"configured": True}
            if resolved_inputs.get("egress") and self.reached:
                out["egress_phase"] = self.reached
            return out
        return {}


def _deployed(tmp_path, manifests, *, egress="egress: { phase: measure }\n",
              reached="measure", deploy_id="egress-te-1"):
    from axrange.planner import Executor, StateStore

    plan = _plan_for(manifests, sid="posture", egress=egress)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _PostureAdapter(reached=reached)
    ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                  deploy_id=deploy_id, sleep_fn=lambda _: None)
    ex.persist_plan()
    assert ex.run().succeeded
    return store, deploy_id


def test_inventory_publishes_the_applied_phase(tmp_path, manifests):
    """`te inventory` / `GET /api/deploys/{id}/inventory` answer the question
    nobody could answer during the 2026-09-21 live run."""
    from axrange.planner.inventory import build_twin_inventory

    store, did = _deployed(tmp_path, manifests)
    try:
        inv = build_twin_inventory(store, did, manifests=manifests)
    finally:
        store.close()
    assert inv.egress is not None
    assert inv.egress.phase_applied == "measure"
    assert inv.egress.phase_declared == "measure"
    assert inv.egress.sink_ip == "10.66.30.10"
    assert inv.egress.router_host == "gw"
    assert inv.egress.contained is True
    assert inv.to_dict()["egress"]["contained"] is True


def test_te_info_publishes_the_applied_phase(tmp_path, manifests):
    from axrange.planner.access import te_info_from_ops

    store, did = _deployed(tmp_path, manifests, deploy_id="egress-te-2")
    try:
        info = te_info_from_ops(
            store.ops_for_deploy(did), scenario_id="posture", plan_id=None
        )
    finally:
        store.close()
    assert info.egress == {
        "router_host": "gw", "phase_declared": "measure", "phase_applied": "measure",
        "sink_ip": "10.66.30.10", "mode": "default", "contained": True,
        "live_read_at": None,
    }


def test_provision_phase_reads_as_open_not_contained(tmp_path, manifests):
    from axrange.planner.inventory import build_twin_inventory

    store, did = _deployed(
        tmp_path, manifests, egress="egress: { phase: provision }\n",
        reached="provision", deploy_id="egress-te-3",
    )
    try:
        inv = build_twin_inventory(store, did, manifests=manifests)
    finally:
        store.close()
    assert inv.egress.phase_applied == "provision"
    assert inv.egress.contained is False


def test_a_declaration_the_router_never_applied_is_not_a_containment(tmp_path, manifests):
    """The exact state the 2026-09-21 breach was in: someone MEANT to
    contain it. An unapplied declaration must never read as contained."""
    from axrange.planner.inventory import build_twin_inventory

    store, did = _deployed(
        tmp_path, manifests, reached=None, deploy_id="egress-te-4",
    )
    try:
        inv = build_twin_inventory(store, did, manifests=manifests)
    finally:
        store.close()
    assert inv.egress.phase_declared == "measure"
    assert inv.egress.phase_applied is None
    assert inv.egress.contained is False


def test_an_undeclared_twin_publishes_no_posture(tmp_path, manifests):
    """Never fabricated — same posture as `role`/`os`/`listen`. Every twin
    deployed before GAP-101 is in this state."""
    from axrange.planner.inventory import build_twin_inventory

    store, did = _deployed(tmp_path, manifests, egress="", deploy_id="egress-te-5")
    try:
        inv = build_twin_inventory(store, did, manifests=manifests)
    finally:
        store.close()
    assert inv.egress is None
    assert inv.to_dict()["egress"] is None


def test_cli_renders_the_posture(capsys):
    from axrange.cli.main import _write_egress_line

    _write_egress_line(None, indent="  ")
    assert capsys.readouterr().out == ""

    _write_egress_line(
        {"phase_declared": "measure", "phase_applied": "measure",
         "sink_ip": "10.66.30.10", "mode": "default", "contained": True},
        indent="  ",
    )
    assert "measure -- CONTAINED" in capsys.readouterr().out

    _write_egress_line(
        {"phase_declared": "measure", "phase_applied": None,
         "sink_ip": "10.66.30.10", "mode": "default", "contained": False},
        indent="  ",
    )
    out = capsys.readouterr().out
    assert "NOT APPLIED" in out and "CONTAINED" not in out


def test_api_dtos_carry_the_posture(tmp_path, manifests, monkeypatch):
    """The API is a peer of the CLI, never a second implementation — the DTO
    is built from the SAME `EgressPosture.to_dict()` shape."""
    from axrange.api import dtos, service
    from axrange.api.settings import ApiSettings

    store, did = _deployed(tmp_path, manifests, deploy_id="egress-te-6")
    store.close()
    settings = ApiSettings(db_path=str(tmp_path / "state.db"))
    inv: dtos.TwinInventoryDTO = service.deploy_inventory(settings, did)
    assert inv.egress is not None
    assert inv.egress.phase_applied == "measure"
    assert inv.egress.contained is True

    # No cluster call from a unit test: `fill_access_facts`'s best-effort
    # live-IP fallback would otherwise REST to whatever `adapter.conf` names
    # (the router's legs are unpinned here, so it WOULD fire).
    import axrange.adapter_proxmox.resolve as _resolve
    monkeypatch.setattr(_resolve, "resolve_vm_ip", lambda *a, **kw: None)
    facts = service.deploy_access_facts(settings, did)
    assert facts.egress is not None and facts.egress.sink_ip == "10.66.30.10"
