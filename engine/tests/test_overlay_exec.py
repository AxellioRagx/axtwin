"""OP-OVL data overlay engine (seed-dataset MVP, docs/SLICE_OVERLAY.md).

Covers render (deterministic synthetic seeder), backend (seed over QGA), and
planner emission (seed-dataset gated on host-ready + the host's apps; identity/
defense overlays rejected as follow-on).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    DatasetRenderError,
    defense_agent_script_path,
    network_sensor_script_path,
    overlay_seeder_path,
    parse_size,
    render_dataset_seeder,
    render_defense_agent_config,
    render_network_sensor_setup,
)
from axrange.ir.parser import parse_ir
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"
OVL_SCN = FIXTURES / "ir" / "valid_overlay_data" / "scenario.yaml"

_ENTRY = {
    "id": "crown-jewels", "host": "app-srv", "path": "/srv/data/customers.csv",
    "classification": "PII", "crown_jewel": True, "synthetic_size": "4Ki",
}


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_parse_size_units():
    assert parse_size("10Mi") == 10 * 1024 ** 2
    assert parse_size("500Ki") == 512000
    assert parse_size("2048") == 2048
    assert parse_size(None) == 1024
    with pytest.raises(DatasetRenderError):
        parse_size("10 furlongs")


def test_seeder_is_deterministic_by_seed():
    a = render_dataset_seeder(_ENTRY, seed=42)
    b = render_dataset_seeder(_ENTRY, seed=42)
    c = render_dataset_seeder(_ENTRY, seed=43)
    assert a == b and a != c
    assert a.startswith("#!/usr/bin/env python3")
    assert "/srv/data/customers.csv" in a


def test_seeder_runs_and_produces_sized_reproducible_file(tmp_path):
    target = tmp_path / "pii.dat"
    entry = {**_ENTRY, "path": str(target), "synthetic_size": "4Ki"}
    script = tmp_path / "seed.py"
    script.write_text(render_dataset_seeder(entry, seed=7))
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert target.stat().st_size == 4096
    assert target.read_bytes().startswith(b"# axRange synthetic dataset: crown-jewels")
    first = target.read_bytes()
    subprocess.run([sys.executable, str(script)], capture_output=True)
    assert target.read_bytes() == first  # same seed -> identical bytes


def test_seeder_rejects_authored_content():
    with pytest.raises(DatasetRenderError) as e:
        render_dataset_seeder({"id": "f", "path": "/p", "content": "flag{x}"}, seed=1)
    assert "content" in str(e.value)


def test_seeder_rejects_missing_path():
    with pytest.raises(DatasetRenderError):
        render_dataset_seeder({"id": "f"}, seed=1)


def test_seeder_rejects_oversize():
    with pytest.raises(DatasetRenderError):
        render_dataset_seeder({"id": "f", "path": "/p", "synthetic_size": "5Gi"}, seed=1)


# ---------------------------------------------------------------------------
# backend (fake QGA)
# ---------------------------------------------------------------------------


class _FakeGA:
    def __init__(self, *, python_present=True, seed_exit=0):
        self.python_present = python_present
        self.seed_exit = seed_exit
        self.execs: list[tuple] = []
        self.written: dict[str, str] = {}
        self.mkdirs: set[str] = set()

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:2] == ["mkdir", "-p"]:
            self.mkdirs.add(argv[2])
        if argv[:2] == ["sh", "-lc"] and "python3" in argv[2]:
            return GuestExecResult(0 if self.python_present else 1, "", "")
        if argv[:1] == ["python3"]:
            return GuestExecResult(self.seed_exit, "seeded", "" if self.seed_exit == 0 else "boom")
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        parent = path.rsplit("/", 1)[0]
        if parent not in self.mkdirs:
            raise AdapterError(f"writing {path}: parent {parent} missing",
                               error_class="guest-write-failed")
        self.written[path] = content


def _ctx():
    return ExecutionContext(plan_id="p", deploy_id="d",
                            cluster=ClusterContext(default_node="px1"), placement={})


def _backend(fake):
    return ConfigBackend(AdapterConfig(), rest_client=object(),
                         guest_agent_factory=lambda client, node, **kw: fake)


def _seed_op():
    d = get_op("seed-dataset")
    return OpInstance(op_id="p:seed-dataset", op_name="seed-dataset",
                      op_catalog_id="OP-OVL-040", kind=OpKind.ACTION,
                      scope={"data": "crown-jewels", "host": "app-srv"},
                      idempotency_class=IdempotencyClass.CONFIG,
                      cleanup_strategy=d.cleanup_strategy)


def test_seed_dataset_renders_writes_and_runs():
    fake = _FakeGA()
    out = _backend(fake).run(
        _seed_op(),
        {"vmid": 100, "data_id": "crown-jewels", "data_entry": _ENTRY, "seed": 1337},
        _ctx(),
    )
    assert out == {"dataset_seeded": True}
    seeder = overlay_seeder_path("crown-jewels")
    assert seeder in fake.written
    assert "customers.csv" in fake.written[seeder]
    assert any(a[:1] == ("python3",) for a in fake.execs)  # executed the seeder


def test_seed_dataset_no_python_errors():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA(python_present=False)).run(
            _seed_op(), {"vmid": 1, "data_id": "crown-jewels", "data_entry": _ENTRY, "seed": 1}, _ctx())
    assert e.value.error_class == "dataset-no-python"


def test_seed_dataset_seeder_failure_errors():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA(seed_exit=2)).run(
            _seed_op(), {"vmid": 1, "data_id": "crown-jewels", "data_entry": _ENTRY, "seed": 1}, _ctx())
    assert e.value.error_class == "dataset-seed-failed"


def test_seed_dataset_bad_entry_errors():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA()).run(
            _seed_op(),
            {"vmid": 1, "data_id": "f", "data_entry": {"id": "f", "content": "x", "path": "/p"}, "seed": 1},
            _ctx())
    assert e.value.error_class == "dataset-render-failed"


# ---------------------------------------------------------------------------
# planner emission
# ---------------------------------------------------------------------------


def _ovl_plan(manifests):
    parsed = parse_ir(path=str(OVL_SCN))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw,
                      scenario_dir=str(OVL_SCN.parent))
    plan.freeze()
    return plan, parsed


def test_seed_dataset_emitted_and_gated_on_host_apps(manifests):
    plan, _ = _ovl_plan(manifests)
    sd = [o for o in plan.ops.values() if o.op_name == "seed-dataset"]
    assert len(sd) == 1
    op = sd[0]
    assert op.idempotency_class == IdempotencyClass.CONFIG
    assert op.inputs_resolved["seed"] == 1337  # from scenario.seed
    preds = set()
    for p in plan.predecessors(op.op_id):
        n = plan.get(p)
        preds.add(getattr(n, "op_name", None) or getattr(n, "barrier_name", "?"))
    assert "host-ready" in preds
    assert "provision-app" in preds  # gated on the host's apps


def test_identity_overlay_domain_without_dc_rejected(manifests):
    """W2b: a domain identity is emitted (create-domain-user) only when the
    scenario has a DC (a role:adds app providing that domain). Referencing a
    domain with no DC is a clear EmissionError. (Positive emission tests for the
    domain-user path live in tests/test_windows_ad.py.)"""
    parsed = parse_ir(path=str(OVL_SCN))
    from axrange.ir.schema import IdentityEntry
    parsed.ir.overlays.identity = [IdentityEntry(id="u1", domain="corp.local")]
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    with pytest.raises(Exception) as e:
        build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw,
                   scenario_dir=str(OVL_SCN.parent))
    msg = str(e.value).lower()
    assert "domain controller" in msg or "role: adds" in msg


def test_data_overlay_unknown_host_rejected(manifests):
    parsed = parse_ir(path=str(OVL_SCN))
    parsed.ir.overlays.data[0].host = "nope"
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    with pytest.raises(Exception) as e:
        build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    assert "nope" in str(e.value)


def test_overlay_fixture_validates(manifests):
    from axrange.validator import validate_full
    res = validate_full(str(OVL_SCN), manifests=manifests)
    assert res.exit_code == 0, res.disposition


# ---------------------------------------------------------------------------
# defense host agent (install-defense-agent)
# ---------------------------------------------------------------------------

_DEF_ENTRY = {"id": "host-audit", "tool": "auditd", "installed_on": ["app-srv"],
              "ruleset": "axrange-baseline"}


def test_defense_render_known_tool_config_and_enable():
    script = render_defense_agent_config(_DEF_ENTRY, config_content="-w /etc/passwd -p wa")
    if shutil.which("bash"):
        r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
    assert "/etc/audit/rules.d/axrange.rules" in script  # auditd config dest
    assert "augenrules --load" in script                 # auditd enable
    assert "-w /etc/passwd -p wa" in script
    assert "RULESET=axrange-baseline" in script


def test_defense_render_unknown_tool_generic_dest():
    script = render_defense_agent_config({"id": "x", "tool": "mystery-edr"}, config_content="cfg")
    assert "/etc/axrange/defense/mystery-edr.conf" in script  # generic dest
    assert "systemctl enable --now mystery-edr" in script     # generic enable


def test_defense_render_no_config_still_enables():
    script = render_defense_agent_config({"id": "x", "tool": "auditd"}, config_content=None)
    assert "AXR_DEFENSE_CFG" not in script   # no config heredoc
    assert "CONFIG_DEST=" in script


def _defense_op():
    d = get_op("install-defense-agent")
    return OpInstance(op_id="p:install-defense-agent", op_name="install-defense-agent",
                      op_catalog_id="OP-OVL-050", kind=OpKind.ACTION,
                      scope={"defense": "host-audit", "host": "app-srv"},
                      idempotency_class=IdempotencyClass.CONFIG,
                      cleanup_strategy=d.cleanup_strategy)


def test_install_defense_agent_writes_and_runs():
    fake = _FakeGA()
    out = _backend(fake).run(
        _defense_op(),
        {"vmid": 100, "defense_id": "host-audit", "defense_entry": _DEF_ENTRY,
         "config_content": "-w /etc/passwd -p wa"},
        _ctx(),
    )
    assert out == {"agent_installed": True}
    path = defense_agent_script_path("host-audit")
    assert path in fake.written
    assert "auditd" in fake.written[path]
    assert any(a == (path,) for a in fake.execs)  # exec'd the config script


def test_install_defense_agent_failure_errors():
    # Force a failure of the config-script exec (single-element argv ending .sh).
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
            _defense_op(),
            {"vmid": 1, "defense_id": "host-audit", "defense_entry": _DEF_ENTRY, "config_content": None},
            _ctx())
    assert e.value.error_class == "defense-agent-failed"


def test_defense_host_agent_emitted_gated_on_apps(manifests):
    """Default-on (2026-07-11): app-srv's explicit auditd entry (tool !=
    wazuh-agent, so no dedup collision) now coexists with its default Wazuh
    agent -- two install-defense-agent ops. This test's actual concern is the
    EXPLICIT entry's gating, so it filters to that one specifically."""
    plan, _ = _ovl_plan(manifests)
    da = [o for o in plan.ops.values() if o.op_name == "install-defense-agent"]
    assert len(da) == 2
    ids = {o.inputs_resolved["defense_id"] for o in da}
    assert ids == {"host-audit", "default-defense-app-srv"}
    explicit = next(o for o in da if o.inputs_resolved["defense_id"] == "host-audit")
    preds = set()
    for p in plan.predecessors(explicit.op_id):
        n = plan.get(p)
        preds.add(getattr(n, "op_name", None) or getattr(n, "barrier_name", "?"))
    assert "host-ready" in preds and "provision-app" in preds


# ---------------------------------------------------------------------------
# network sensor (deploy-network-sensor — network-sensor Suricata, Option C)
# ---------------------------------------------------------------------------


def test_sensor_render_default_rules_and_iface():
    s = render_network_sensor_setup(
        {"id": "ids", "tool": "suricata", "observes": [{"net": "dmz"}], "ruleset": "et-open"},
        iface="cap-mirror", rules_content=None)
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode == 0
    assert "axRange ICMP" in s          # default rules
    assert "-i cap-mirror" in s         # af-packet on the mirror iface
    assert "OBSERVES=dmz" in s
    # live-verify regression: must stop the packaged service (it sniffs the wrong
    # iface) and use a dedicated pidfile so our mirror-iface instance wins.
    assert "systemctl stop suricata" in s
    assert "/run/suricata-axrange.pid" in s


def test_sensor_render_custom_rules():
    s = render_network_sensor_setup(
        {"id": "ids", "tool": "suricata"}, iface="eth1",
        rules_content='alert ip any any -> any any (msg:"x"; sid:1;)')
    assert 'alert ip any any' in s and "-i eth1" in s


def test_sensor_render_network_sensor_start_check_parity():
    """code-review finding 1: render_network_sensor_setup must be at parity
    with render_sensor_setup's suricata branch — clear the stale pidfile
    before start, and fail loud (exit 1) both when suricata is missing and
    when the daemon doesn't come up (no silent "configured" on a dead
    daemon)."""
    s = render_network_sensor_setup(
        {"id": "ids", "tool": "suricata", "observes": [{"net": "dmz"}]},
        iface="cap-mirror", rules_content=None)
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode == 0
    # stale pidfile cleared before (re)start
    assert "rm -f /run/suricata-axrange.pid" in s
    # start-check keyed off the daemon's cmdline (comm is "Suricata-Main", so
    # `pgrep -x suricata` would miss it), and fails loud
    assert "pgrep -f 'suricata.*axrange'" in s
    assert "exit 1" in s
    # the suricata-absent branch fails loud too, not just an echo-and-continue
    assert "command -v suricata" in s
    lines = s.splitlines()
    absent_idx = next(i for i, ln in enumerate(lines) if "not installed on the network sensor" in ln)
    assert any("exit 1" in ln for ln in lines[absent_idx:absent_idx + 2])


def _sensor_op():
    d = get_op("deploy-network-sensor")
    return OpInstance(op_id="p:deploy-network-sensor", op_name="deploy-network-sensor",
                      op_catalog_id="OP-OVL-060", kind=OpKind.ACTION,
                      scope={"defense": "ids", "sensor": "default"},
                      idempotency_class=IdempotencyClass.CONFIG,
                      cleanup_strategy=d.cleanup_strategy)


def _backend_network_sensor(fake, vmid=8000):
    return ConfigBackend(AdapterConfig(network_sensor_vmid=vmid), rest_client=object(),
                         guest_agent_factory=lambda client, node, **kw: fake)


def test_deploy_network_sensor_no_network_sensor_errors():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA()).run(  # default config: network_sensor_vmid=0
            _sensor_op(),
            {"defense_id": "ids", "tool": "suricata",
             "defense_entry": {"id": "ids", "tool": "suricata"}, "config_content": None},
            _ctx())
    assert e.value.error_class == "sensor-no-network-sensor"


def test_deploy_network_sensor_configures_network_sensor():
    fake = _FakeGA()
    out = _backend_network_sensor(fake).run(
        _sensor_op(),
        {"defense_id": "ids", "tool": "suricata",
         "defense_entry": {"id": "ids", "tool": "suricata", "observes": [{"net": "dmz"}]},
         "config_content": None},
        _ctx())
    assert out == {"sensor_live": True}
    path = network_sensor_script_path("ids")
    assert path in fake.written and "suricata" in fake.written[path]
    assert any(a == (path,) for a in fake.execs)


def _sensor_ir(manifests, *, capture: bool):
    from axrange.ir.schema import DefenseEntry, DefenseObserves
    parsed = parse_ir(path=str(OVL_SCN))
    if capture:
        parsed.ir.layers.infra.nodes[0].tap = True  # a tapped host -> mirror-ready
    parsed.ir.overlays.defense = [
        DefenseEntry(id="ids", tool="suricata",
                     observes=[DefenseObserves(net="corp", mode="live")],
                     tap_from="external-mirror", ruleset="et-open")
    ]
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    return parsed, ctx


def test_network_sensor_emitted_but_no_mirror_ready_to_gate_on(manifests):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    deploy-network-sensor still emits (its `capture_sensor_id is None` gate is
    unaffected — it only needs `any_tapped`, not real mirror wiring), but its
    catalog `after: mirror-ready[$sensor]` now resolves to zero predecessors —
    the te emits no mirror-ready barrier of its own any more (documented
    no-op, pass3_ordering)."""
    parsed, ctx = _sensor_ir(manifests, capture=True)
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw,
                      scenario_dir=str(OVL_SCN.parent))
    plan.freeze()
    sens = [o for o in plan.ops.values() if o.op_name == "deploy-network-sensor"]
    assert len(sens) == 1
    assert sens[0].idempotency_class == IdempotencyClass.CONFIG
    preds = {getattr(plan.get(p), "op_name", None) or getattr(plan.get(p), "barrier_name", "?")
             for p in plan.predecessors(sens[0].op_id)}
    assert "mirror-ready" not in preds
    assert not any(b.barrier_name == "mirror-ready" for b in plan.barriers.values())


def test_network_sensor_builds_even_with_no_capture_hint(manifests):
    """Formerly `..._rejected` (pre-2026-07-28): `deploy-network-sensor`
    (Option C) used to REFUSE to build when no host in the scenario carried
    `tap: true` (`any_tapped`, `EmissionError`) -- a te-emission-time
    "nothing will be captured" check. Full-topology tap eligibility (`docs/
    WORK_ORDER_tap_eligibility.md` D1/D2, Authority: user, 2026-07-28)
    retired that check along with the `tap:` field it read: whether
    anything is ACTUALLY captured is now entirely a POST-DEPLOY, tap-plan-
    time question (A2's job, against the twin's published topology) --
    unknowable, and therefore unenforceable, at te-emission time. A
    network-sensor declaration with no author-set `tap: true` anywhere
    (the `capture=False` case `_sensor_ir` still constructs, harmlessly
    inert now -- no schema field reads it) builds exactly like the
    `capture=True` case above: `deploy-network-sensor` still emits."""
    parsed, ctx = _sensor_ir(manifests, capture=False)
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw,
                      scenario_dir=str(OVL_SCN.parent))
    sens = [o for o in plan.ops.values() if o.op_name == "deploy-network-sensor"]
    assert len(sens) == 1
