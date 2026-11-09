"""Targets API (`docs/design/targets-and-lab.md` §2.2, §4 auto), build step 2:

- `GET /api/targets` carries `lab.components`, never a credential;
- `GET /api/targets/{name}/health`: a status per component -- an
  unreachable url is `down`, a missing probe is `unknown`; cached 15 s;
- `POST /api/validate` and `POST /api/plan` take `?target=` and report
  `sensor <id> not in lab of target <name>` (at the binding's path) and
  `image <x> has no binding for platform <p>` (at the host's path).

No cluster: the adapter.confs point at nothing, the health probes hit closed
local ports or are stubbed.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

from axrange.api import ApiSettings, create_app
from axrange.api import labhealth

FIXT_ROOT = Path(__file__).parent / "fixtures"


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _twin(root: Path, *, image2: str = "debian-12-cloud@v1", sensor: str = "XPress-SW-A") -> Path:
    d = root / "lab-twin"
    (d / "tap").mkdir(parents=True)
    (d / "twin.yaml").write_text(
        "twin:\n  id: lab-twin\n  description: preflight fixture\n  containment: uncontained\n"
        "  tiers:\n    te:\n      default: te.yaml\n    tap:\n      default: tap/default.yaml\n"
    )
    (d / "te.yaml").write_text(
        "twin_config:\n"
        "  id: lab-twin\n"
        "  description: preflight fixture\n"
        "  segments:\n"
        "    - {name: corp, cidr: 10.20.0.0/24, zone_type: simple}\n"
        "  nodes:\n"
        "    - name: h-a\n"
        "      image: debian-12-cloud@v1\n"
        "      requires: [linux]\n"
        "      resources: {cpu: 1, ram: 1Gi, disk: 10Gi}\n"
        "      net: corp\n"
        "    - name: h-b\n"
        f"      image: {image2}\n"
        "      requires: [linux]\n"
        "      resources: {cpu: 1, ram: 1Gi, disk: 10Gi}\n"
        "      net: corp\n"
    )
    (d / "tap" / "default.yaml").write_text(
        "tap-plan:\n"
        "  target: lab-twin\n"
        "  bindings:\n"
        "    - selector: {scope: segment, target: corp}\n"
        f"      sensors: [{sensor}]\n"
    )
    return d


def _setup(tmp_path: Path, *, grafana_url: str | None = None, image2: str = "debian-12-cloud@v1",
           sensor: str = "XPress-SW-A") -> TestClient:
    grafana_url = grafana_url or f"http://127.0.0.1:{_closed_port()}"
    px = tmp_path / "px.conf"
    px.write_text("PROXMOX_API_URL=https://10.0.0.1:8006/api2/json\nDEFAULT_NODE=dev45\n"
                  "PROXMOX_API_TOKEN=root@pam!t=SECRET-TOKEN-VALUE\n")
    kv = tmp_path / "kv.conf"
    kv.write_text("PLATFORM=libvirt\nLIBVIRT_URI=qemu:///system\nDEFAULT_NODE=kvm1\n"
                  f"LIBVIRT_STORAGE_POOL=default\nGUEST_SSH_PUBKEY_FILE={tmp_path}/k.pub\n")
    twins = tmp_path / "twins"
    _twin(twins, image2=image2, sensor=sensor)
    tf = tmp_path / "targets.yaml"
    tf.write_text(
        "targets:\n"
        "  - name: dev45\n"
        "    platform: proxmox\n"
        f"    adapter_config: {px}\n"
        f"    db: {tmp_path / 'px.db'}\n"
        "    lab:\n"
        "      components:\n"
        "        - id: XPress-SW-A\n"
        "          role: sensor\n"
        "          type: packetxpress\n"
        "          reach: {vmid: 104, transport_ip: 127.0.0.2}\n"
        "          protected: true\n"
        "        - id: Tcpdump-SW-A\n"
        "          role: sensor\n"
        "          type: tcpdump\n"
        "          reach: {vmid: 260, transport_ip: 127.0.0.3}\n"
        "        - id: grafana\n"
        "          role: telemetry\n"
        "          type: grafana\n"
        f"          reach: {{url: \"{grafana_url}\"}}\n"
        "          health: {http: /api/health}\n"
        "  - name: kvm1\n"
        "    platform: libvirt\n"
        f"    adapter_config: {kv}\n"
        f"    db: {tmp_path / 'kv.db'}\n"
        "    lab:\n"
        "      components:\n"
        "        - id: Tcpdump-SW-A\n"
        "          role: sensor\n"
        "          type: tcpdump\n"
        "          reach: {vmid: 260, transport_ip: 127.0.0.3}\n"
    )
    app = create_app(ApiSettings(
        targets_file=str(tf), twin_configs_dir=str(twins), imprints_dir=str(twins),
        images_root=str(FIXT_ROOT.resolve()),
    ))
    return TestClient(app)


# ---- GET /api/targets ------------------------------------------------------------

def test_targets_carry_the_lab_and_no_credential(tmp_path):
    c = _setup(tmp_path)
    r = c.get("/api/targets")
    assert r.status_code == 200
    body = r.text
    ts = {t["name"]: t for t in r.json()["targets"]}
    lab = ts["dev45"]["lab"]["components"]
    assert [(x["id"], x["role"], x["type"]) for x in lab] == [
        ("XPress-SW-A", "sensor", "packetxpress"),
        ("Tcpdump-SW-A", "sensor", "tcpdump"),
        ("grafana", "telemetry", "grafana"),
    ]
    assert lab[0]["reach"] == {"vmid": 104, "transport_ip": "127.0.0.2", "capture_iface": "gre-cap"}
    assert lab[0]["protected"] is True and lab[0]["managed"] == "external"
    assert lab[0]["health_http"] is None and lab[2]["health_http"] == "/api/health"
    assert ts["kvm1"]["platform"] == "libvirt"
    assert [x["id"] for x in ts["kvm1"]["lab"]["components"]] == ["Tcpdump-SW-A"]
    # The merged sensor list the canvas offers.
    assert ts["dev45"]["sensors"] == ["XPress-SW-A", "Tcpdump-SW-A"]
    # Never a credential anywhere in the answer.
    assert "SECRET-TOKEN-VALUE" not in body and "password" not in body.lower()


# ---- GET /api/targets/{name}/health -----------------------------------------------

def test_health_down_for_an_unreachable_url_unknown_without_a_resolution(tmp_path, monkeypatch):
    # No platform to resolve the sensor VMs; their transport does not answer.
    monkeypatch.setattr(labhealth, "_sensor_facts", lambda cfg, ids: {})
    monkeypatch.setattr(labhealth, "tcp_open", lambda h, p, timeout=2.0: False)
    c = _setup(tmp_path)
    r = c.get("/api/targets/dev45/health")
    assert r.status_code == 200
    by = {x["id"]: x for x in r.json()["components"]}
    assert r.json()["target"] == "dev45"
    assert list(by) == ["XPress-SW-A", "Tcpdump-SW-A", "grafana"]
    assert by["grafana"]["status"] == "down"
    assert "/api/health" in by["grafana"]["detail"] and by["grafana"]["checked_at"]
    # No platform to resolve the VM, transport not reachable: unknown, said so.
    assert by["XPress-SW-A"]["status"] == "unknown"
    assert "not resolved" in by["XPress-SW-A"]["detail"]


def test_health_up_on_2xx_and_a_component_with_no_probe_is_unknown(tmp_path, monkeypatch):
    from axrange.platform import lab as labmod

    monkeypatch.setattr(labhealth, "http_status", lambda url, timeout=2.0: (200, "HTTP 200"))
    monkeypatch.setattr(labhealth, "_sensor_facts", lambda cfg, ids: {})
    monkeypatch.setattr(labhealth, "tcp_open", lambda h, p, timeout=2.0: False)
    c = _setup(tmp_path)
    by = {x["id"]: x for x in c.get("/api/targets/dev45/health").json()["components"]}
    assert by["grafana"]["status"] == "up"
    # No declared probe and no type default: unknown.
    monkeypatch.delitem(labmod.DEFAULT_HEALTH_HTTP, "loki")
    bare = labmod.LabComponent(id="loki", role="telemetry", type="loki", reach={"url": "http://h"})
    out = labhealth._url_component(bare)
    assert out.status == "unknown" and "no health probe" in out.detail


def test_health_wazuh_401_is_up(monkeypatch):
    from axrange.platform.lab import LabComponent

    monkeypatch.setattr(labhealth, "http_status", lambda url, timeout=2.0: (401, "HTTP 401"))
    w = LabComponent(id="wazuh", role="defence", type="wazuh", reach={"url": "https://h:55000"})
    g = LabComponent(id="g", role="telemetry", type="grafana", reach={"url": "http://h:3000"})
    assert labhealth._url_component(w).status == "up"
    assert labhealth._url_component(g).status == "down"


def test_health_sensor_up_via_its_resolved_vm(monkeypatch):
    from axrange.platform.lab import LabComponent

    seen = []
    monkeypatch.setattr(labhealth, "tcp_open", lambda h, p, timeout=2.0: seen.append((h, p)) or True)
    s = LabComponent(id="X", role="sensor", type="packetxpress",
                     reach={"vmid": 104, "transport_ip": "10.255.251.21"})
    out = labhealth._sensor_component(s, {"resolved_ip": "10.80.72.43", "resolved_name": "XPress-SW-A"})
    assert out.status == "up" and seen == [("10.80.72.43", 22)]
    monkeypatch.setattr(labhealth, "tcp_open", lambda h, p, timeout=2.0: False)
    assert labhealth._sensor_component(s, {"resolved_ip": "10.80.72.43"}).status == "down"


def test_health_is_cached_15s(tmp_path, monkeypatch):
    calls = []

    def probe(url, timeout=2.0):
        calls.append(url)
        return 200, "HTTP 200"

    monkeypatch.setattr(labhealth, "http_status", probe)
    monkeypatch.setattr(labhealth, "_sensor_facts", lambda cfg, ids: {})
    monkeypatch.setattr(labhealth, "tcp_open", lambda h, p, timeout=2.0: False)
    c = _setup(tmp_path)
    c.get("/api/targets/dev45/health")
    c.get("/api/targets/dev45/health")
    assert len(calls) == 1


def test_health_unknown_target_is_404(tmp_path):
    c = _setup(tmp_path)
    r = c.get("/api/targets/nope/health")
    assert r.status_code == 404 and r.json()["detail"]["targets"] == ["dev45", "kvm1"]


# ---- validate / plan ?target= -------------------------------------------------------

def _errors(findings, rule):
    return [(f["message"], f["location"]) for f in findings if f["rule_id"] == rule]


def test_validate_clean_on_the_default_target(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/api/validate", params={"target": "dev45"}, json={"config": "lab-twin"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert not [f for f in j["findings"] if f["rule_id"].startswith("TARGET-")]


def test_validate_sensor_not_in_lab_names_the_binding(tmp_path):
    c = _setup(tmp_path)
    j = c.post("/api/validate", params={"target": "kvm1"}, json={"config": "lab-twin"}).json()
    assert _errors(j["findings"], "TARGET-SENSOR") == [
        ("sensor XPress-SW-A not in lab of target kvm1", "tap/default.yaml: tap-plan.bindings[0].sensors[0]"),
    ]
    assert j["disposition"] == "FAIL" and j["errors"] >= 1


def test_validate_unbound_image_names_the_host(tmp_path):
    # ubuntu-server-22 has a proxmox binding in the fixture catalog, no libvirt one.
    c = _setup(tmp_path, image2="ubuntu-server-22@v5", sensor="Tcpdump-SW-A")
    j = c.post("/api/validate", params={"target": "kvm1"}, json={"config": "lab-twin"}).json()
    assert _errors(j["findings"], "TARGET-IMAGE") == [
        ("image ubuntu-server-22@v5 has no binding for platform libvirt",
         "te.yaml: twin_config.nodes[1].image"),
    ]
    assert _errors(j["findings"], "TARGET-SENSOR") == []
    j2 = c.post("/api/validate", params={"target": "dev45"}, json={"config": "lab-twin"}).json()
    assert _errors(j2["findings"], "TARGET-IMAGE") == []


def test_validate_without_target_is_unchanged(tmp_path):
    c = _setup(tmp_path, sensor="Nowhere-Sensor")
    j = c.post("/api/validate", json={"config": "lab-twin"}).json()
    assert not [f for f in j["findings"] if f["rule_id"].startswith("TARGET-")]


def test_validate_document_takes_the_target(tmp_path):
    c = _setup(tmp_path)
    twin = tmp_path / "twins" / "lab-twin"
    files = {str(p.relative_to(twin)): p.read_text() for p in twin.rglob("*.yaml")}
    j = c.post("/api/validate", params={"target": "kvm1"},
               json={"document": {"id": "lab-twin", "files": files}}).json()
    assert _errors(j["findings"], "TARGET-SENSOR") == [
        ("sensor XPress-SW-A not in lab of target kvm1", "tap/default.yaml: tap-plan.bindings[0].sensors[0]"),
    ]


def test_validate_unknown_target_is_404(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/api/validate", params={"target": "nope"}, json={"config": "lab-twin"})
    assert r.status_code == 404


def test_plan_carries_the_preflight(tmp_path):
    c = _setup(tmp_path, image2="ubuntu-server-22@v5")
    r = c.post("/api/plan", params={"target": "dev45"}, json={"config": "lab-twin"})
    assert r.status_code == 200, r.text
    pf = r.json()["preflight"]
    assert pf["target"] == "dev45" and pf["platform"] == "proxmox" and pf["clean"] is True
    assert (pf["images_bound"], pf["images_total"]) == (2, 2)
    assert pf["sensors"] == ["Tcpdump-SW-A", "XPress-SW-A"]

    r = c.post("/api/plan", params={"target": "kvm1"}, json={"config": "lab-twin"})
    assert r.status_code == 200, r.text
    pf = r.json()["preflight"]
    assert pf["platform"] == "libvirt" and pf["clean"] is False
    assert (pf["images_bound"], pf["images_total"]) == (1, 2)
    assert {f["rule_id"] for f in pf["findings"]} == {"TARGET-SENSOR", "TARGET-IMAGE"}
    assert pf["sensors"] == ["Tcpdump-SW-A"]


def test_plan_without_target_has_no_preflight(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/api/plan", json={"config": "lab-twin"})
    assert r.status_code == 200, r.text
    assert r.json()["preflight"] is None


# ---- the Twins page's plan / deploy (/api/twins/{id}/plan|deploy ?target=) ------------

def test_twin_plan_carries_the_preflight(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/api/twins/lab-twin/plan", params={"target": "kvm1"}, json={})
    assert r.status_code == 200, r.text
    pf = r.json()["preflight"]
    assert pf["target"] == "kvm1" and pf["platform"] == "libvirt" and pf["clean"] is False
    assert [(f["rule_id"], f["location"]) for f in pf["findings"]] == [
        ("TARGET-SENSOR", "tap/default.yaml: tap-plan.bindings[0].sensors[0]"),
    ]
    r = c.post("/api/twins/lab-twin/plan", params={"target": "dev45"}, json={})
    assert r.json()["preflight"]["clean"] is True
    assert c.post("/api/twins/lab-twin/plan", json={}).json()["preflight"] is None


def test_twin_deploy_refuses_an_unclean_preflight_before_any_write(tmp_path):
    c = _setup(tmp_path)
    started = []
    c.app.state.deploy_runner = lambda *a: started.append(a)
    c.app.state.deploy_background = lambda job: job()
    r = c.post("/api/twins/lab-twin/deploy", params={"target": "kvm1"}, json={})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"]["code"] == "preflight"
    assert "sensor XPress-SW-A not in lab of target kvm1" in body["error"]
    assert started == [] and not (tmp_path / "kv.db").exists()


def test_twin_deploy_on_a_clean_target_lands_in_its_store(tmp_path):
    c = _setup(tmp_path, sensor="Tcpdump-SW-A")
    started = []
    c.app.state.deploy_runner = lambda settings, plan, cfg, ctx, did: started.append((settings.db_path, did))
    c.app.state.deploy_background = lambda job: job()
    r = c.post("/api/twins/lab-twin/deploy", params={"target": "kvm1"}, json={})
    assert r.status_code == 200, r.text
    assert started and started[0][0] == str((tmp_path / "kv.db").resolve())
