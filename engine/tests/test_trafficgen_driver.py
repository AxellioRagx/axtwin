"""trafficgen B5 (`engine/src/axrange/trafficgen/SPEC.md`): the in-VM
controller and wrapper (`guest_tg.py`) against a fake TRex client, the
`trex-astf` generator driver against a fake guest agent, the op composition,
and the live rate change -- TRex faked throughout, as the Zeek runner is."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from axrange.personas.drivers.base import PersonaDriverError
from axrange.personas.drivers.trex_generator import TrexGeneratorDriver
from axrange.planner.dag import Plan
from axrange.trafficgen import guest_tg
from axrange.trafficgen.compose import ComposeError, compose_generator_ops, te_segment_attach
from axrange.trafficgen.tfc import LinesError, line_status, update_line_rate

FIX = Path(__file__).parent / "fixtures" / "trafficgen" / "three-tier-dc-manual"
PLAN = json.loads((FIX / "expected-plan.json").read_text())
DOC = PLAN["generators"][1]["document"]          # gen-internet--rack2-lan
LINE = DOC["lines"][0]["id"]


# --- the controller ---------------------------------------------------------
class FakeClient:
    def __init__(self):
        self.calls, self.profiles, self.running = [], {}, {}
        self.open_flows, self.tx_bps = 0, 0.0

    def connect(self): self.calls.append(("connect",))
    def acquire(self, force=False): self.calls.append(("acquire", force))
    def disconnect(self): pass
    def get_profiles(self): return ["_", *self.profiles]

    def load_profile(self, prof, pid_input):
        self.calls.append(("load", pid_input)); self.profiles[pid_input] = prof

    def start(self, mult, pid_input, client_mask):
        self.calls.append(("start", pid_input, mult, client_mask)); self.running[pid_input] = mult

    def update(self, mult, pid_input):
        self.calls.append(("update", pid_input, mult)); self.running[pid_input] = mult

    def stop(self, pid_input, is_remove=False):
        self.calls.append(("stop", pid_input, is_remove)); self.running.pop(pid_input, None)
        if is_remove:
            self.profiles.pop(pid_input, None)

    def get_stats(self, skip_zero=False): return {"global": {"tx_bps": self.tx_bps}}

    def get_traffic_stats(self, skip_zero=False, pid_input=None):
        return {"client": {"tcps_connattempt": 100, "tcps_connects": 100 + self.open_flows, "tcps_closed": 100,
                           "tcps_sndbyte": 1000, "tcps_rcvbyte": 9000},
                "server": {"tcps_accepts": 100, "err_cwf": 0}}


@pytest.fixture
def vm(tmp_path, monkeypatch):
    for name, rel in (("STATE", "state"), ("DOC", "generator.json"), ("PROFILE", "astf_profile.py"),
                      ("FILES", "files"), ("DESIRED", "state/desired.json"), ("STATS", "state/stats.json")):
        monkeypatch.setattr(guest_tg, name, str(tmp_path / rel))
    (tmp_path / "state").mkdir()
    (tmp_path / "generator.json").write_text(json.dumps(DOC))
    client = FakeClient()
    api = SimpleNamespace(ASTFClient=lambda server: client,
                          ASTFProfile=SimpleNamespace(load=lambda path, tunables: ("profile", tuple(tunables))))
    ctl = guest_tg.Controller(api, ensure_ns=lambda doc: (True, []))
    return SimpleNamespace(ctl=ctl, client=client, root=tmp_path)


def _desire(vm, lines):
    guest_tg.write_json(guest_tg.DESIRED, {"generation": 1 + (guest_tg.read_json(guest_tg.DESIRED, {}) or {}).get("generation", 0),
                                           "lines": lines})


def test_the_controller_loads_starts_updates_and_removes(vm):
    st = vm.ctl.step(now=0)
    assert st["trex_connected"] and st["namespaces_ok"] and st["lines"] == {}
    _desire(vm, {LINE: {"state": "loaded", "mult": 1.0}})
    vm.ctl.step(now=2)
    assert ("load", LINE) in vm.client.calls and LINE not in vm.client.running
    _desire(vm, {LINE: {"state": "running", "mult": 1.0}})
    st = vm.ctl.step(now=4)
    assert vm.client.running == {LINE: 1.0} and ("start", LINE, 1.0, 1) in vm.client.calls
    assert st["lines"][LINE]["established"] == 100 and st["applied_generation"] == 2
    _desire(vm, {LINE: {"state": "running", "mult": 2.0}})
    vm.ctl.step(now=6)
    assert ("update", LINE, 2.0) in vm.client.calls and vm.client.running[LINE] == 2.0
    n = len(vm.client.calls)
    vm.ctl.step(now=8)                        # nothing changed: no TRex call beyond reads
    assert [c for c in vm.client.calls[n:] if c[0] in ("load", "start", "update", "stop")] == []
    _desire(vm, {})
    vm.ctl.step(now=10)
    assert ("stop", LINE, True) in vm.client.calls and LINE not in vm.client.profiles


def test_a_changed_line_is_reloaded(vm):
    _desire(vm, {LINE: {"state": "running", "mult": 1.0}})
    vm.ctl.step(now=0)
    doc = json.loads(json.dumps(DOC)); doc["lines"][0]["rate_cps"] = 99
    (vm.root / "generator.json").write_text(json.dumps(doc))
    vm.ctl.step(now=2)
    assert vm.client.calls.count(("load", LINE)) == 2 and ("stop", LINE, True) in vm.client.calls


def test_max_concurrent_is_enforced_by_lowering_the_multiplier(vm):
    _desire(vm, {LINE: {"state": "running", "mult": 1.0}})
    vm.ctl.step(now=0)
    vm.client.open_flows = DOC["lines"][0]["max_concurrent"] + 50
    st = vm.ctl.step(now=2)
    assert st["lines"][LINE]["capped"] == "max_concurrent" and vm.client.running[LINE] == 0.8
    vm.client.open_flows = 0
    vm.ctl.step(now=4)
    assert vm.client.running[LINE] == 1.0      # back up to what was asked, never above


def test_the_budget_mbps_is_enforced(vm):
    _desire(vm, {LINE: {"state": "running", "mult": 1.0}})
    vm.ctl.step(now=0)
    vm.client.tx_bps = (DOC["budget"]["mbps"] + 10) * 1e6
    st = vm.ctl.step(now=2)
    assert st["lines"][LINE]["capped"] == "budget_mbps" and vm.client.running[LINE] < 1.0


def test_trex_down_is_reported_and_reconnected(vm):
    vm.ctl.api.ASTFClient = lambda server: (_ for _ in ()).throw(ConnectionError("no RPC"))
    st = vm.ctl.step(now=0)
    assert st["trex_connected"] is False and "no RPC" in st["error"]


def test_desire_waits_for_the_controller_and_is_idempotent(vm, capsys):
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            vm.ctl.step(); time.sleep(0.05)
    t = threading.Thread(target=loop, daemon=True); t.start()
    try:
        guest_tg.main(["desire", "--state", "running", "--timeout", "10"])
        first = json.loads(capsys.readouterr().out)
        assert first["changed"] is True and first["lines"][LINE]["state"] == "running"
        guest_tg.main(["desire", "--state", "running", "--timeout", "10"])
        again = json.loads(capsys.readouterr().out)
        assert again["changed"] is False and again["applied_generation"] == first["applied_generation"]
        guest_tg.main(["desire", "--line", LINE, "--mult", "2", "--timeout", "10"])
        assert json.loads(capsys.readouterr().out)["lines"][LINE]["mult_effective"] == 2.0
        with pytest.raises(SystemExit, match="unknown line"):
            guest_tg.main(["desire", "--line", "nope", "--mult", "2", "--timeout", "2"])
    finally:
        stop.set(); t.join(2)


def test_render_cfg_uses_the_interfaces_found_by_mac():
    cfg = guest_tg.render_cfg(DOC, {"0": "ens18", "1": "ens19"})
    assert 'interfaces: ["ens18", "ens19"]' in cfg and "stack: linux_based" in cfg and "low_end: true" in cfg
    assert "- ip: 10.250.0.3\n      default_gw: 10.250.0.1" in cfg


# --- the driver ---------------------------------------------------------------
class FakeGA:
    def __init__(self, replies=None):
        self.execs, self.files, self.replies = [], {}, replies or {}

    def exec(self, vmid, argv, timeout_s=30):
        self.execs.append(argv)
        cmd = " ".join(argv)
        out, ok = "", True
        for key, (o, k) in self.replies.items():
            if key in cmd:
                out, ok = o, k
        return SimpleNamespace(ok=ok, exit_code=0 if ok else 1, stdout=out, stderr="" if ok else "boom")

    def write_file(self, vmid, path, text, mode=None):
        self.files[path] = text


def test_provision_pushes_everything_and_maps_legs_by_mac():
    ga = FakeGA({"tg.py provision": ('{"provisioned": true, "ifaces": {"0": "ens18"}}', True)})
    r = TrexGeneratorDriver().provision(ga, 101, document_json='{"x":1}', legs={"0": "bc:00:00:00:00:01"},
                                        files={"tls-session--capture.pcap": b"\x01\x02"})
    assert r["ifaces"] == {"0": "ens18"}
    assert set(ga.files) == {"/opt/axrange-tg/tg.py", "/opt/axrange-tg/astf_profile.py", "/opt/axrange-tg/generator.json"}
    assert "class Controller" in ga.files["/opt/axrange-tg/tg.py"]
    cmd = " ".join(ga.execs[-1])
    assert "provision --legs" in cmd and "bc:00:00:00:00:01" in cmd
    assert any("tls-session--capture.pcap" in " ".join(a) and "base64 -d" in " ".join(a) for a in ga.execs)


def test_provision_refuses_a_vm_without_trex():
    ga = FakeGA({"t-rex-64": ("", False)})
    with pytest.raises(PersonaDriverError, match="no baked TRex"):
        TrexGeneratorDriver().provision(ga, 101, document_json="{}", legs={})


def test_start_update_stop_go_through_desire():
    ga = FakeGA({"tg.py desire": ('{"applied_generation": 3}', True)})
    d = TrexGeneratorDriver()
    d.start(ga, 101, mults={LINE: 1.0, "other": 2.0})
    d.stop(ga, 101, LINE)
    cmds = [" ".join(a) for a in ga.execs]
    assert any("desire --state running" in c for c in cmds)
    assert any("desire --line other --mult 2.0" in c for c in cmds)
    assert not any(f"desire --line {LINE}" in c for c in cmds)       # 1.0 needs no update
    assert any(f"desire --remove {LINE}" in c for c in cmds)
    with pytest.raises(PersonaDriverError):
        d.update(ga, 101, LINE, 0)


def test_a_failed_wrapper_call_is_a_driver_error():
    ga = FakeGA({"tg.py desire": ("", False)})
    with pytest.raises(PersonaDriverError, match="load-traffic-profile failed"):
        TrexGeneratorDriver().load(ga, 101)


# --- composition ------------------------------------------------------------
TE_ROWS = [
    {"op_name": "create-switch", "scope": {"switch": "rack1-tor"}, "inputs": {}, "outputs": {"bridge_name": "tesw_1"}},
    {"op_name": "create-switch", "scope": {"switch": "rack2-tor"}, "inputs": {}, "outputs": {"bridge_name": "tesw_2"}},
    {"op_name": "create-switch", "scope": {"switch": "rack3-tor"}, "inputs": {}, "outputs": {"bridge_name": "tesw_3"}},
    {"op_name": "create-vnet", "scope": {"net": "internet"}, "inputs": {}, "outputs": {"vnet_id": "tev4d037"}},
    *[{"op_name": "attach-vm-to-switch-port", "scope": {}, "outputs": {},
       "inputs": {"net_id": f"rack{i}-lan", "switch_id": f"rack{i}-tor", "vlan_tag": 60 + 10 * i, "mode": "access"}}
      for i in (1, 2, 3)],
    {"op_name": "attach-vm-to-vnet", "scope": {}, "outputs": {}, "inputs": {"net_id": "internet", "vlan": None}},
]
BINDING = {"image_id": "trex-astf", "template_vmid": 102, "clone_mode": "linked", "ci_user": "debian"}


def _composed():
    from axrange.o2control.traffic import load_traffic_profile
    from axrange.trafficgen.plan import plan_profile
    from axrange.trafficgen.twin import TwinView

    tg = plan_profile(load_traffic_profile(FIX / "tfc" / "default.yaml"), TwinView.from_te_path(FIX / "te.yaml"))
    plan = Plan(plan_id="tfc-three-tier-dc-manual-x", scenario_id="s", scenario_version=None, ir_hash="", tier="tfc")
    ids = compose_generator_ops(plan, tg, attach=te_segment_attach(TE_ROWS), binding=BINDING,
                                target_node="dev45", mults={LINE: 2.0})
    plan.freeze()
    return plan, ids


def test_te_segment_attach_reads_the_te_networks():
    a = te_segment_attach(TE_ROWS)
    assert a["rack1-lan"] == {"op": "attach-vm-to-switch-port", "bridge_name": "tesw_1", "vlan_tag": 70, "switch_id": "rack1-tor"}
    assert a["internet"] == {"op": "attach-vm-to-vnet", "vnet_id": "tev4d037", "vlan": None}


def test_compose_adds_a_vm_and_its_generator_ops_per_generator():
    plan, ids = _composed()
    names = [plan.ops[i].op_name for i in ids]
    assert names.count("clone-vm") == 2 and names.count("provision-generator") == 2
    by = {(plan.ops[i].op_name, json.dumps(plan.ops[i].scope, sort_keys=True)): plan.ops[i] for i in ids}
    clone = by[("clone-vm", '{"host": "gen-internet--rack2-lan"}')]
    assert clone.inputs_resolved["template_vmid"] == 102
    hw = by[("configure-vm-hardware", '{"host": "gen-internet--rack2-lan"}')]
    assert (hw.inputs_resolved["cpu"], hw.inputs_resolved["ram"]) == (2, "2253M")
    inet = by[("attach-vm-to-vnet", '{"host": "gen-internet--rack2-lan", "net": "internet"}')]
    assert (inet.inputs_resolved["vnet_id"], inet.inputs_resolved["nic_index"]) == ("tev4d037", 0)
    r2 = by[("attach-vm-to-switch-port", '{"host": "gen-internet--rack2-lan", "net": "rack2-lan"}')]
    assert (r2.inputs_resolved["bridge_name"], r2.inputs_resolved["vlan_tag"], r2.inputs_resolved["nic_index"]) == ("tesw_2", 80, 1)
    start = by[("start-traffic", '{"generator": "gen-internet--rack2-lan"}')]
    assert start.inputs_resolved["mults"] == {LINE: 2.0}
    prov = by[("provision-generator", '{"generator": "gen-internet--rack2-lan"}')]
    assert prov.inputs_resolved["files"][0]["name"] == "tls-session--capture.pcap"
    assert prov.inputs_from_outputs == {"vmid": [clone.op_id, "vmid"]}


def test_compose_orders_the_chain():
    plan, ids = _composed()
    by = {(plan.ops[i].op_name, plan.ops[i].scope.get("host") or plan.ops[i].scope.get("generator")): i for i in ids}
    g = "gen-rack1-lan--rack3-lan"
    attach = "attach-vm-to-switch-port"            # both legs of this generator are switch segments
    by_leg = {plan.ops[i].scope.get("net"): i for i in ids
              if plan.ops[i].op_name == attach and plan.ops[i].scope.get("host") == g}
    edges = {(e.src_op_id, e.dst_op_id) for e in plan.edges}
    chain = ["configure-vm-hardware", "configure-cloud-init"]
    assert (by[(chain[0], g)], by[(chain[1], g)]) in edges
    for leg in ("rack1-lan", "rack3-lan"):
        assert (by[("configure-cloud-init", g)], by_leg[leg]) in edges      # NICs after cloud-init
        assert (by_leg[leg], by[("start-vm", g)]) in edges                  # boot after both NICs
    tail = ["start-vm", "wait-guest-agent", "provision-generator", "load-traffic-profile", "start-traffic"]
    for a, b in zip(tail, tail[1:]):
        assert (by[(a, g)], by[(b, g)]) in edges, (a, b)
    assert (by[("clone-vm", g)], by[("configure-vm-hardware", g)]) in edges   # dataflow: the vmid


def test_compose_is_deterministic_and_refuses_an_unknown_segment():
    p1, i1 = _composed()
    p2, i2 = _composed()
    assert i1 == i2
    from axrange.o2control.traffic import load_traffic_profile
    from axrange.trafficgen.plan import plan_profile
    from axrange.trafficgen.twin import TwinView
    tg = plan_profile(load_traffic_profile(FIX / "tfc" / "default.yaml"), TwinView.from_te_path(FIX / "te.yaml"))
    plan = Plan(plan_id="p", scenario_id="s", scenario_version=None, ir_hash="", tier="tfc")
    with pytest.raises(ComposeError, match="internet"):
        compose_generator_ops(plan, tg, attach={k: v for k, v in te_segment_attach(TE_ROWS).items() if k != "internet"},
                              binding=BINDING, target_node="dev45")


# --- live rate change and status --------------------------------------------
class FakeStore:
    def __init__(self, plan, ids):
        self.rows, self.merged = [], {}
        for i in ids:
            op = plan.ops[i]
            out = {"vmid": 4242} if op.op_name == "clone-vm" else {}
            self.rows.append(SimpleNamespace(
                op_id=op.op_id, op_name=op.op_name, scope_json=json.dumps(op.scope),
                inputs_resolved_json=json.dumps(op.inputs_resolved), outputs_json=json.dumps(out),
                status=SimpleNamespace(value="completed")))

    def ops_for_deploy(self, deploy_id):
        return self.rows

    def merge_op_outputs(self, deploy_id, op_id, patch):
        for r in self.rows:
            if r.op_id == op_id:
                r.outputs_json = json.dumps({**json.loads(r.outputs_json or "{}"), **patch})
        self.merged[op_id] = patch
        return True


def test_update_line_rate_runs_the_op_and_records_the_multiplier():
    plan, ids = _composed()
    store, calls = FakeStore(plan, ids), []
    r = update_line_rate(store, "tfc-x", LINE, 45.0, run_update=lambda *a: calls.append(a))
    assert r["mult"] == pytest.approx(1.5) and calls == [("gen-internet--rack2-lan", 4242, LINE, 1.5)]
    assert list(store.merged.values()) == [{"mults": {LINE: 1.5}}]


def test_update_line_rate_refuses_over_budget_and_unknown_lines():
    plan, ids = _composed()
    store = FakeStore(plan, ids)
    with pytest.raises(LinesError, match="over budget"):
        update_line_rate(store, "tfc-x", LINE, 95.0, run_update=lambda *a: None)
    with pytest.raises(LinesError, match="no running traffic line"):
        update_line_rate(store, "tfc-x", "nope", 5.0, run_update=lambda *a: None)


def test_line_status_reads_each_generator():
    plan, ids = _composed()
    store = FakeStore(plan, ids)
    st = line_status(store, "tfc-x", read_status=lambda vmid: {"lines": {LINE: {"state": "running", "established": 77, "errors": 0}}})
    assert st[LINE]["established"] == 77 and st[LINE]["rate_cps"] == 60.0 and st[LINE]["mult"] == 2.0
    assert st["rack1-web--rack3-db--sql-oltp"]["generator"] == "gen-rack1-lan--rack3-lan"


# --- apply end to end (store + fake backend) ----------------------------------
def test_apply_plans_and_runs_the_generators(tmp_path, monkeypatch):
    """A lines-only profile in the twin's folder, applied onto a seeded te
    deploy: the generator VMs' ops run in order through the infra and config
    slots, and the te file is never written."""
    import shutil

    from axrange.manifest.loader import load_registry
    from axrange.o2control.traffic import apply_traffic_profile
    from axrange.planner import StateStore
    from tests.test_traffic_tier import _op_row

    twin = tmp_path / "three-tier-dc-manual"
    shutil.copytree(FIX, twin)
    prof = twin / "tfc" / "lines.yaml"
    prof.write_text((FIX / "tfc" / "default.yaml").read_text().replace(
        "  assignments:\n    - role: web-tier\n      persona: web-tier\n    - role: db-tier\n      persona: db-tier\n", ""))
    te_before = (twin / "te.yaml").read_bytes()
    db = str(tmp_path / "state.db")
    te_id = "three-tier-dc-manual-abc123"
    store = StateStore(db)
    store.create_deploy(deploy_id=te_id, scenario_id="three-tier-dc-manual", scenario_version=1,
                        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={}, tier="te")
    store.insert_ops(te_id, [_op_row(te_id, f"te-{i}", r["op_name"], "OP-X", r["scope"], inputs=r["inputs"],
                                     outputs=r["outputs"]) for i, r in enumerate(TE_ROWS)])
    store.close()

    ran = []

    class Rec:
        def run(self, op, inp, ctx):
            ran.append((op.op_name, op.scope.get("host") or op.scope.get("generator")))
            return {"vmid": 4242} if op.op_name == "clone-vm" else {"ok": True}

    cfg = SimpleNamespace(default_node="dev45", platform="proxmox")
    monkeypatch.setattr("axrange.platform.load_config", lambda p=None: cfg)
    status = apply_traffic_profile(spec_path=str(prof), db_path=db, target=te_id, persona_backend=Rec(),
                                   manifests=load_registry(root=Path(__file__).parent / "fixtures"))
    assert status.status == "completed", status.warnings
    g = "gen-internet--rack2-lan"
    seq = [n for n, h in ran if h == g]
    assert seq[0] == "clone-vm" and seq[-3:] == ["provision-generator", "load-traffic-profile", "start-traffic"]
    assert seq.index("start-vm") > max(seq.index("attach-vm-to-vnet"), seq.index("attach-vm-to-switch-port"))
    assert {h for _, h in ran} == {g, "gen-rack1-lan--rack3-lan"}
    assert (twin / "te.yaml").read_bytes() == te_before
    assert any("2 generator(s)" in w for w in status.warnings)


def test_tfc_destroy_removes_generator_vms_on_the_default_node(tmp_path, monkeypatch):
    """A tfc deploy records no placement; its generators were cloned on the
    adapter's default node, so the teardown must look there (live gate
    2026-11-24: 'no PVE node resolved' before this fallback)."""
    from axrange.o2control.traffic import destroy_traffic_profile
    from axrange.planner import StateStore
    from tests.test_traffic_tier import _op_row

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    tfc = "tfc-twin-1-0001"
    store.create_deploy(deploy_id=tfc, scenario_id="tfc-twin-1", scenario_version=None, scenario_schema_version=1,
                        plan_id="tfc-twin-1", ir_hash="", placement={}, tier="tfc")
    store.insert_ops(tfc, [_op_row(tfc, "c1", "clone-vm", "OP-HOST-010", {"host": "gen-a--b"},
                                   inputs={}, outputs={"vmid": 432100})])
    store.close()
    calls = []

    class Teardown:
        def vm_name(self, node, vmid): return "tfc-abcdef-gen-a--b"
        def destroy_vm(self, node, vmid): calls.append((node, vmid))
        def __getattr__(self, name): return lambda *a, **k: None

    cfg = SimpleNamespace(default_node="dev45", platform="proxmox", protected_external_names=(),
                          protected_external_vmids=())
    monkeypatch.setattr("axrange.platform.load_config", lambda p=None: cfg)
    destroy_traffic_profile(db_path=db, deploy_id=tfc, teardown_backend=Teardown(),
                            persona_backend=SimpleNamespace())
    assert calls == [("dev45", 432100)]
