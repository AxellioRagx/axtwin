"""A3 Stage A -- the default-agent-set (defense + exporter) and
`overlays.adversary` (Sandcat/Caldera agent) engine,
docs/design/08-exercise-measurement.md.

Covers the registry (axrange.overlays.default_agents), render (exporter +
Sandcat, linux + windows content), backend (install-agent over QGA, OP-OVL-070),
planner emission (adversary overlay fan-out + the default-agent sweep, per-node
opt-out, router exclusion, coexistence with a hand-authored overlays.defense
entry), and validator (REF-220/REF-230).
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    agent_script_path,
    render_exporter_agent,
    render_sandcat_agent,
    render_windows_exporter_agent,
    render_windows_sandcat_agent,
)
from axrange.ir.parser import parse_ir
from axrange.ir.schema import Node
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.overlays.default_agents import (
    UNCONDITIONAL_DEFAULT_KINDS,
    default_defense_entry,
    default_exporter_entry,
    is_opted_out,
    resolve_default_kinds,
)
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext
from axrange.validator.engine import validate_full


# ---------------------------------------------------------------------------
# registry (axrange.overlays.default_agents)
# ---------------------------------------------------------------------------


def test_registry_unconditional_defaults_are_defense_and_exporter_only():
    assert set(UNCONDITIONAL_DEFAULT_KINDS) == {"defense", "exporter"}
    assert "adversary" not in UNCONDITIONAL_DEFAULT_KINDS  # never a blanket default


def test_registry_resolve_default_kinds_respects_opt_out():
    on = Node(id="h1", kind="host")
    assert resolve_default_kinds(on) == ["defense", "exporter"]
    off_one = Node(id="h2", kind="host", default_agents_off=["exporter"])
    assert resolve_default_kinds(off_one) == ["defense"]
    off_both = Node(id="h3", kind="host", default_agents_off=["defense", "exporter"])
    assert resolve_default_kinds(off_both) == []


def test_registry_is_opted_out_tolerates_dict_and_bare_node():
    assert is_opted_out({"default_agents_off": ["defense"]}, "defense") is True
    assert is_opted_out({}, "defense") is False
    assert is_opted_out(Node(id="h"), "defense") is False


def test_registry_default_entries_shape():
    d = default_defense_entry("h1")
    assert d == {"id": "default-defense-h1", "tool": "wazuh-agent", "installed_on": ["h1"]}
    e = default_exporter_entry("h1")
    assert e == {"id": "default-exporter-h1"}


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_exporter_agent_bash_clean_and_installs_node_exporter():
    s = render_exporter_agent({"id": "exp1"})
    if shutil.which("bash"):
        r = subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
    assert "prometheus-node-exporter" in s
    assert "systemctl enable --now" in s
    assert "exp1" in s


def test_render_windows_exporter_agent_best_effort_enable():
    ps = render_windows_exporter_agent({"id": "exp1"})
    assert "windows_exporter" in ps
    assert "exp1" in ps


def test_render_sandcat_agent_points_at_server_and_defaults_group():
    s = render_sandcat_agent({"id": "red1"}, server="10.20.40.67")
    if shutil.which("bash"):
        r = subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
    assert "http://10.20.40.67:8888" in s
    assert "GROUP=red" in s  # default group
    assert "sandcat.go" in s


def test_render_sandcat_agent_honors_explicit_url_and_group():
    s = render_sandcat_agent({"id": "red1", "group": "blue"}, server="https://caldera:8443")
    assert "https://caldera:8443" in s
    assert "GROUP=blue" in s
    assert "http://https://" not in s  # doesn't double-prefix an already-URL server


def test_render_windows_sandcat_agent_points_at_server():
    ps = render_windows_sandcat_agent({"id": "red1"}, server="10.20.40.67")
    assert "http://10.20.40.67:8888" in ps
    assert "sandcat.go" in ps


def test_agent_script_path_deterministic_and_distinct():
    assert agent_script_path("red1") == agent_script_path("red1")
    assert agent_script_path("a") != agent_script_path("b")


# ---------------------------------------------------------------------------
# backend (install-agent, OP-OVL-070)
# ---------------------------------------------------------------------------


class _FakeGA:
    def __init__(self):
        self.execs: list[tuple] = []
        self.written: dict[str, str] = {}
        self.mkdirs: set[str] = set()

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:2] == ["mkdir", "-p"]:
            self.mkdirs.add(argv[2])
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        parent = path.rsplit("/", 1)[0]
        if parent not in self.mkdirs:
            raise AdapterError(
                f"writing {path}: parent {parent} missing", error_class="guest-write-failed"
            )
        self.written[path] = content


def _ctx():
    return ExecutionContext(
        plan_id="p", deploy_id="d", cluster=ClusterContext(default_node="px1"), placement={}
    )


def _backend(fake):
    return ConfigBackend(
        AdapterConfig(), rest_client=object(), guest_agent_factory=lambda client, node, **kw: fake
    )


def _agent_op(kind: str = "exporter") -> OpInstance:
    d = get_op("install-agent")
    return OpInstance(
        op_id="p:install-agent", op_name="install-agent", op_catalog_id=d.id,
        kind=OpKind.ACTION, scope={"agent": "a1", "host": "h1", "kind": kind},
        idempotency_class=IdempotencyClass.CONFIG, cleanup_strategy=d.cleanup_strategy,
    )


def test_install_agent_exporter_writes_and_runs():
    fake = _FakeGA()
    out = _backend(fake).run(
        _agent_op("exporter"),
        {
            "vmid": 100, "agent_id": "default-exporter-h1", "agent_kind": "exporter",
            "agent_entry": {"id": "default-exporter-h1"},
        },
        _ctx(),
    )
    assert out == {"agent_installed": True}
    path = agent_script_path("default-exporter-h1")
    assert path in fake.written
    assert "node-exporter" in fake.written[path] or "node_exporter" in fake.written[path]
    assert any(a == (path,) for a in fake.execs)


def test_install_agent_adversary_writes_and_runs_with_server():
    fake = _FakeGA()
    out = _backend(fake).run(
        _agent_op("adversary"),
        {
            "vmid": 101, "agent_id": "red1", "agent_kind": "adversary",
            "agent_entry": {"id": "red1", "group": "red"}, "server": "10.20.40.67",
        },
        _ctx(),
    )
    assert out == {"agent_installed": True}
    path = agent_script_path("red1")
    assert "10.20.40.67" in fake.written[path]


def test_install_agent_adversary_missing_server_errors():
    fake = _FakeGA()
    with pytest.raises(AdapterError) as e:
        _backend(fake).run(
            _agent_op("adversary"),
            {"vmid": 101, "agent_id": "red1", "agent_kind": "adversary", "agent_entry": {"id": "red1"}},
            _ctx(),
        )
    assert e.value.error_class == "agent-no-server"


def test_install_agent_unknown_kind_errors():
    fake = _FakeGA()
    with pytest.raises(AdapterError) as e:
        _backend(fake).run(
            _agent_op("bogus"),
            {"vmid": 1, "agent_id": "x", "agent_kind": "bogus", "agent_entry": {}},
            _ctx(),
        )
    assert e.value.error_class == "agent-unknown-kind"


def test_install_agent_exec_failure_errors():
    class _FailGA(_FakeGA):
        def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
            self.execs.append(tuple(argv))
            if argv[:2] == ["mkdir", "-p"]:
                self.mkdirs.add(argv[2])
                return GuestExecResult(0, "", "")
            if len(argv) == 1 and argv[0].endswith(".sh"):
                return GuestExecResult(3, "", "boom")
            return GuestExecResult(0, "", "")

    with pytest.raises(AdapterError) as e:
        _backend(_FailGA()).run(
            _agent_op("exporter"),
            {"vmid": 1, "agent_id": "exp1", "agent_kind": "exporter", "agent_entry": {"id": "exp1"}},
            _ctx(),
        )
    assert e.value.error_class == "agent-install-failed"


# ---------------------------------------------------------------------------
# planner emission -- overlays.adversary
#
# Exercise-tier scope-shift (2026-07-24, docs/WORK_ORDER_exercise_tier.md,
# docs/design/02-twin-model.md): a te deploy no longer emits ANY
# `install-agent` (agent_kind=adversary) op for a declared `overlays.
# adversary:` -- attack playout (Sandcat plant included) is re-homed to the
# standalone Exercise (`exr`) tier (`axrange.o2control.exercise`), which
# composes the SAME op by hand against an already-deployed twin's explicit
# `attack.adversary.targets:` (its own id/targets/server/kind validation --
# ported from what pass1_emit used to enforce here -- lives in
# test_exercise.py now, not here). Only the "te plans to zero adversary ops"
# shape is still this file's concern.
# ---------------------------------------------------------------------------

_ADV_SCN_TEXT = (
    "scenario_schema_version: 1\n"
    "scenario: { id: adversary-demo, version: 1, seed: 7 }\n"
    "layers:\n"
    "  infra:\n"
    "    networks:\n"
    "      - { id: corp, cidr: 10.10.0.0/24, vlan: 10, gateway: 10.10.0.1 }\n"
    "    nodes:\n"
    "      - { id: ws1, kind: host, nets: [{ net: corp, ip: 10.10.0.10 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n"
    "      - { id: ws2, kind: host, nets: [{ net: corp, ip: 10.10.0.11 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n"
    "  apps: []\n"
    "  activity: []\n"
    "bindings: []\n"
    "overlays:\n"
    "  adversary:\n"
    "    - id: red-team\n"
    "      targets: [ws1, ws2]\n"
    "      server: 10.20.40.67\n"
    "      group: red\n"
)


def _adv_plan(manifests, text=_ADV_SCN_TEXT):
    parsed = parse_ir(text=text)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    return plan, parsed


# A router (VyOS -- no QGA) or switch target would hang at deploy (install-agent
# is delivered over QGA) rather than fail at plan/validate time (code-review
# SHOULD-FIX #2, 2026-07-11). Still used by the REF-240 validator tests below --
# REF-220/230/240 are IR-level validator rules, unaffected by the Exercise-tier
# emission scope-shift (they still fire at `axrange te validate` regardless of
# which tier eventually runs the overlay).
_ADV_SCN_ROUTER_TEXT = _ADV_SCN_TEXT.replace(
    "      - { id: ws2, kind: host, nets: [{ net: corp, ip: 10.10.0.11 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n",
    "      - { id: ws2, kind: host, nets: [{ net: corp, ip: 10.10.0.11 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n"
    "      - { id: gw, kind: router, nets: [{ net: corp, ip: 10.10.0.1 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n",
).replace("targets: [ws1, ws2]", "targets: [ws1, gw]")


def test_te_plan_emits_zero_adversary_agents(manifests):
    """A `te_` deploy plans cleanly with `overlays.adversary:` declared, but
    emits NO adversary install-agent op -- that's the exr tier's job now."""
    plan, _ = _adv_plan(manifests)
    ops = [
        o for o in plan.ops.values()
        if o.op_name == "install-agent" and o.inputs_resolved.get("agent_kind") == "adversary"
    ]
    assert ops == []


# ---------------------------------------------------------------------------
# planner emission -- default-agent-set sweep (scenario.default_agents)
# ---------------------------------------------------------------------------

_DEFAULTS_TEXT = (
    "scenario_schema_version: 1\n"
    "scenario: {{ id: default-agents-demo, version: 1{flag} }}\n"
    "layers:\n"
    "  infra:\n"
    "    networks:\n"
    "      - {{ id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }}\n"
    "    nodes:\n"
    "      - {{ id: gw, kind: router, nets: [{{ net: corp, ip: 10.0.0.1 }}],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }}\n"
    "      - {{ id: h-a, kind: host, nets: [{{ net: corp, ip: 10.0.0.10 }}],\n"
    "          image: ubuntu-server-22@v5, requires: [linux] }}\n"
    "      - {{ id: h-b, kind: host, nets: [{{ net: corp, ip: 10.0.0.11 }}],\n"
    "          image: ubuntu-server-22@v5, requires: [linux],\n"
    "          default_agents_off: [exporter] }}\n"
    "  apps: []\n"
    "  activity: []\n"
    "bindings: []\n"
)


def test_default_agents_on_by_default_no_flag_needed(manifests):
    """ON BY DEFAULT (user decision, 2026-07-11): an unset scenario.
    default_agents fires the sweep exactly like an explicit `true` -- no flag
    needed. Router still excluded, opt-out still honored."""
    parsed = parse_ir(text=_DEFAULTS_TEXT.format(flag=""))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    defense = [o for o in plan.ops.values() if o.op_name == "install-defense-agent"]
    exporter = [
        o for o in plan.ops.values()
        if o.op_name == "install-agent" and o.inputs_resolved.get("agent_kind") == "exporter"
    ]
    assert {o.inputs_resolved["host_id"] for o in defense} == {"h-a", "h-b"}
    assert {o.inputs_resolved["host_id"] for o in exporter} == {"h-a"}  # h-b opted out


def test_default_agents_explicit_false_disables_the_sweep(manifests):
    """The scenario-level escape hatch: `default_agents: false` turns the
    WHOLE baked-in sweep off (no explicit overlays.defense entries in this
    fixture, so zero install-defense-agent/install-agent ops)."""
    parsed = parse_ir(text=_DEFAULTS_TEXT.format(flag=", default_agents: false"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    names = {o.op_name for o in plan.ops.values()}
    assert "install-defense-agent" not in names
    assert "install-agent" not in names


def test_default_agents_on_covers_every_host_excludes_router(manifests):
    parsed = parse_ir(text=_DEFAULTS_TEXT.format(flag=", default_agents: true"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    defense = [o for o in plan.ops.values() if o.op_name == "install-defense-agent"]
    exporter = [
        o for o in plan.ops.values()
        if o.op_name == "install-agent" and o.inputs_resolved.get("agent_kind") == "exporter"
    ]
    defense_hosts = {o.inputs_resolved["host_id"] for o in defense}
    exporter_hosts = {o.inputs_resolved["host_id"] for o in exporter}
    assert defense_hosts == {"h-a", "h-b"}  # both hosts get the default Wazuh agent
    assert exporter_hosts == {"h-a"}  # h-b opted OUT of the exporter default
    assert "gw" not in defense_hosts  # the router is not a default-agent target
    assert "gw" not in exporter_hosts


def test_default_agents_gated_on_host_ready_and_apps(manifests):
    parsed = parse_ir(text=_DEFAULTS_TEXT.format(flag=", default_agents: true"))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    op = next(
        o for o in plan.ops.values()
        if o.op_name == "install-defense-agent" and o.inputs_resolved["host_id"] == "h-a"
    )
    preds = set()
    for p in plan.predecessors(op.op_id):
        n = plan.get(p)
        preds.add(getattr(n, "op_name", None) or getattr(n, "barrier_name", "?"))
    assert "host-ready" in preds


def test_default_agents_and_explicit_defense_entry_coexist(manifests):
    """A hand-authored overlays.defense entry AND the baked-in default both
    emit -- distinct op ids, no collision (item 1 -- extend, don't replace)."""
    text = _DEFAULTS_TEXT.format(flag=", default_agents: true") + (
        "overlays:\n"
        "  defense:\n"
        "    - id: custom-audit\n"
        "      tool: auditd\n"
        "      installed_on: [h-a]\n"
    )
    parsed = parse_ir(text=text)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    da = [o for o in plan.ops.values() if o.op_name == "install-defense-agent"]
    ids = {o.inputs_resolved["defense_id"] for o in da}
    assert {"custom-audit", "default-defense-h-a", "default-defense-h-b"} <= ids


def test_default_agents_dedup_skips_default_when_explicit_wazuh_present(manifests):
    """MANDATORY dedup fix (code-review, 2026-07-11): a host with an
    EXPLICIT overlays.defense wazuh-agent entry gets exactly ONE
    install-defense-agent op, not two (two ops would both write
    /var/ossec/etc/ossec.conf). Default-on, no flag needed."""
    text = _DEFAULTS_TEXT.format(flag="") + (
        "overlays:\n"
        "  defense:\n"
        "    - id: manual-wazuh\n"
        "      tool: wazuh-agent\n"
        "      installed_on: [h-a]\n"
    )
    parsed = parse_ir(text=text)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    da_h_a = [
        o for o in plan.ops.values()
        if o.op_name == "install-defense-agent" and o.inputs_resolved["host_id"] == "h-a"
    ]
    assert len(da_h_a) == 1
    assert da_h_a[0].inputs_resolved["defense_id"] == "manual-wazuh"  # the explicit one wins
    # h-b (no explicit entry) still gets its default.
    da_h_b = [
        o for o in plan.ops.values()
        if o.op_name == "install-defense-agent" and o.inputs_resolved["host_id"] == "h-b"
    ]
    assert len(da_h_b) == 1
    assert da_h_b[0].inputs_resolved["defense_id"] == "default-defense-h-b"


def test_default_agents_dedup_exporter_alias(manifests):
    """Same dedup for the exporter default if an author routes an explicit
    node-exporter through overlays.defense's generic tool fallback."""
    text = _DEFAULTS_TEXT.format(flag="") + (
        "overlays:\n"
        "  defense:\n"
        "    - id: manual-exporter\n"
        "      tool: node-exporter\n"
        "      installed_on: [h-a]\n"
    )
    parsed = parse_ir(text=text)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    exp_h_a = [
        o for o in plan.ops.values()
        if o.op_name == "install-agent" and o.inputs_resolved.get("agent_kind") == "exporter"
        and o.inputs_resolved["host_id"] == "h-a"
    ]
    assert len(exp_h_a) == 0  # the exporter DEFAULT is skipped -- the explicit
    # entry (a regular install-defense-agent, not install-agent) covers h-a's
    # node-exporter instead. h-a's separate DEFENSE default (wazuh) is
    # unaffected -- "node-exporter" isn't a wazuh alias -- so h-a still gets
    # both: the explicit exporter-alias entry AND the default Wazuh agent.
    manual_ids = {
        o.inputs_resolved["defense_id"] for o in plan.ops.values()
        if o.op_name == "install-defense-agent" and o.inputs_resolved["host_id"] == "h-a"
    }
    assert manual_ids == {"manual-exporter", "default-defense-h-a"}


# A DC (role: adds host) needs its default Wazuh agent's op scoped with
# `domain` so the domain-ready[$domain] edge resolves and host-side AD
# auditing (render_windows_defense_agent) runs POST-promotion -- else the
# promotion reboot silently wipes it (code-review MUST-FIX #1, 2026-07-11).
_DC_DEFAULTS_TEXT = (
    "scenario_schema_version: 1\n"
    "scenario: { id: dc-default-agents, version: 1, default_agents: true }\n"
    "layers:\n"
    "  infra:\n"
    "    networks:\n"
    "      - { id: corp, cidr: 10.0.0.0/24, vlan: 10, gateway: 10.0.0.1 }\n"
    "    nodes:\n"
    "      - { id: dc1, kind: host, nets: [{ net: corp, ip: 10.0.0.10 }],\n"
    "          image: debian-12-cloud@v1, requires: [linux] }\n"
    "  apps:\n"
    "    - id: ad\n"
    "      install: [{ role: adds }]\n"
    "      provides: [{ domain: corp.local }]\n"
    "  activity: []\n"
    "bindings:\n"
    "  - { app: ad, host: dc1 }\n"
)


def test_default_agents_dc_defense_agent_scoped_with_domain(manifests):
    parsed = parse_ir(text=_DC_DEFAULTS_TEXT)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    da = next(
        o for o in plan.ops.values()
        if o.op_name == "install-defense-agent"
        and o.inputs_resolved["host_id"] == "dc1"
    )
    assert da.scope.get("domain") == "corp.local"
    # And the domain-ready[$domain] edge actually resolves (not just the
    # scope key being present -- the edge is what makes the audit config
    # land post-promotion).
    barr = next(b for b in plan.barriers.values() if b.barrier_name == "domain-ready")
    assert barr.op_id in set(plan.predecessors(da.op_id))


# ---------------------------------------------------------------------------
# validator: REF-220 / REF-230
# ---------------------------------------------------------------------------


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def test_ref_220_missing_id(manifests):
    text = _ADV_SCN_TEXT.replace(
        "    - id: red-team\n      targets:", "    - targets:"
    )
    result = validate_full(text=text, manifests=manifests)
    assert "REF-220" in _ids(result)


def test_ref_230_flags_every_bad_target_in_one_fan_out_entry(manifests):
    """LES-034: two unknown targets in one entry => two REF-230 findings."""
    text = _ADV_SCN_TEXT.replace("targets: [ws1, ws2]", "targets: [nope1, nope2]")
    result = validate_full(text=text, manifests=manifests)
    findings = [f for f in result.findings if f.rule_id == "REF-230"]
    assert len(findings) == 2
    assert {f.extra["host_id"] for f in findings} == {"nope1", "nope2"}


def test_ref_230_silent_when_targets_resolve(manifests):
    result = validate_full(text=_ADV_SCN_TEXT, manifests=manifests)
    assert "REF-230" not in _ids(result)
    assert "REF-220" not in _ids(result)


def test_ref_240_flags_non_host_adversary_target(manifests):
    result = validate_full(text=_ADV_SCN_ROUTER_TEXT, manifests=manifests)
    findings = [f for f in result.findings if f.rule_id == "REF-240"]
    assert len(findings) == 1
    assert findings[0].extra["host_id"] == "gw"
    assert findings[0].extra["kind"] == "router"


def test_ref_240_silent_for_host_target(manifests):
    result = validate_full(text=_ADV_SCN_TEXT, manifests=manifests)
    assert "REF-240" not in _ids(result)
