"""Targets file `lab:` block (`docs/design/targets-and-lab.md` §2.1, §4 auto).

Step 1 of the build order: the schema (closed role, type closed per role),
the loader, and the merge of lab sensors into each target's loaded adapter
config -- `EXTERNAL_SENSORS` read from the lab first, adapter.conf's list
still merged for one release, a sensor id in both refused at serve start by
name. No cluster: adapter.conf files are temp files with no reachable API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api.settings import ApiSettingsError
from axrange.api.targets import load_targets_file, register_labs
from axrange.platform import ConfigError, adapter_for_config, load_config
from axrange.platform.lab import (
    ROLES,
    TYPES_BY_ROLE,
    lab_sensors_for,
    load_labs_from_targets_file,
    parse_lab,
)

LAB = """\
    lab:
      components:
        - id: XPress-SW-A
          role: sensor
          type: packetxpress
          reach: {vmid: 104, transport_ip: 10.255.251.21, capture_iface: gre-cap}
          managed: external
          protected: true
        - id: Zeek-A
          role: sensor
          type: zeek
          reach: {vmid: 270, transport_ip: 10.255.251.30}
        - id: grafana
          role: telemetry
          type: grafana
          reach: {url: "http://10.80.72.52:3000"}
          health: {http: /api/health}
        - id: caldera
          role: attack
          type: caldera
          reach: {url: "http://10.80.72.52:8888"}
        - id: wazuh
          role: defence
          type: wazuh
          reach: {url: "https://10.80.72.52:55000"}
"""


def _conf(path: Path, *, sensors: list[dict] | None = None, node: str = "nodea") -> str:
    text = f"PROXMOX_API_URL=https://10.0.0.1:8006/api2/json\nDEFAULT_NODE={node}\n"
    if sensors is not None:
        text += f"EXTERNAL_SENSORS={json.dumps(sensors)}\n"
    path.write_text(text)
    return str(path)


def _targets(tmp_path: Path, conf: str, lab: str = LAB, name: str = "dev45") -> Path:
    tf = tmp_path / "targets.yaml"
    tf.write_text(
        "targets:\n"
        f"  - name: {name}\n"
        "    platform: proxmox\n"
        f"    adapter_config: {conf}\n"
        f"    db: {tmp_path / 'state.db'}\n"
        + lab
    )
    return tf


# ---- schema -------------------------------------------------------------------

def test_lab_parses_every_role_and_keeps_reach_as_declared(tmp_path):
    (t,) = load_targets_file(_targets(tmp_path, _conf(tmp_path / "a.conf")))
    comps = {c.id: c for c in t.lab.components}
    assert [c.role for c in t.lab.components] == [
        "sensor", "sensor", "telemetry", "attack", "defence",
    ]
    x = comps["XPress-SW-A"]
    assert (x.type, x.protected, x.managed) == ("packetxpress", True, "external")
    assert x.reach == {"vmid": 104, "transport_ip": "10.255.251.21", "capture_iface": "gre-cap"}
    # capture_iface defaults like EXTERNAL_SENSORS' did.
    assert comps["Zeek-A"].reach["capture_iface"] == "gre-cap"
    assert comps["grafana"].reach == {"url": "http://10.80.72.52:3000"}
    # Declared probe wins; absent -> the type default.
    assert comps["grafana"].health_http == "/api/health"
    assert comps["caldera"].health_http == "/api/v2/health"
    assert comps["wazuh"].health_http == "/"


def test_closed_sets_are_the_designs():
    assert ROLES == ("sensor", "telemetry", "attack", "defence")
    assert TYPES_BY_ROLE["sensor"] == ("zeek", "suricata", "tcpdump", "packetxpress")
    assert TYPES_BY_ROLE["telemetry"] == ("grafana", "prometheus", "loki")


@pytest.mark.parametrize("component, message", [
    ({"id": "g", "role": "monitor", "type": "grafana", "reach": {"url": "http://h"}},
     r"components\[0\] \(g\)\.role: unknown role 'monitor'"),
    ({"id": "g", "role": "telemetry", "type": "kibana", "reach": {"url": "http://h"}},
     r"components\[0\] \(g\)\.type: unknown telemetry type 'kibana'"),
    # A type from another role is still unknown for this one.
    ({"id": "s", "role": "sensor", "type": "grafana", "reach": {"vmid": 1, "transport_ip": "1.2.3.4"}},
     r"unknown sensor type 'grafana'"),
    ({"id": "s", "role": "sensor", "type": "zeek", "reach": {"transport_ip": "1.2.3.4"}},
     r"reach: missing vmid"),
    ({"id": "s", "role": "sensor", "type": "zeek", "reach": {"vmid": 1, "transport_ip": " "}},
     r"transport_ip must not be blank"),
    ({"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "10.0.0.1:3100"}},
     r"reach.url must be an http\(s\) URL"),
    ({"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "http://u:p@h:3100"}},
     r"must not carry credentials"),
    ({"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "http://h"}, "managed": "axrange"},
     r"managed: 'axrange' is not supported"),
    ({"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "http://h"}, "protected": True},
     r"protected applies to sensors only"),
    ({"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "http://h"}, "password": "x"},
     r"unknown key\(s\) \['password'\]"),
])
def test_bad_components_are_refused_by_name(component, message):
    with pytest.raises(ConfigError, match=message):
        parse_lab({"components": [component]}, where="t")


def test_duplicate_component_id_is_refused():
    c = {"id": "g", "role": "telemetry", "type": "loki", "reach": {"url": "http://h"}}
    with pytest.raises(ConfigError, match="duplicate component id 'g'"):
        parse_lab({"components": [c, dict(c)]}, where="t")


def test_unknown_role_refuses_serve_start_by_name(tmp_path):
    lab = LAB.replace("role: attack", "role: offence")
    tf = _targets(tmp_path, _conf(tmp_path / "a.conf"), lab=lab)
    with pytest.raises(ApiSettingsError, match=r"\(caldera\)\.role: unknown role 'offence'"):
        create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))


def test_unknown_type_refuses_serve_start_by_name(tmp_path):
    lab = LAB.replace("type: zeek", "type: snort")
    tf = _targets(tmp_path, _conf(tmp_path / "a.conf"), lab=lab)
    with pytest.raises(ApiSettingsError, match=r"\(Zeek-A\)\.type: unknown sensor type 'snort'"):
        create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))


# ---- the merge ----------------------------------------------------------------

def test_lab_sensors_merge_into_the_loaded_config(tmp_path):
    conf = _conf(tmp_path / "a.conf", sensors=[
        {"id": "Tcpdump-SW-A", "vmid": 260, "transport_ip": "10.255.251.12", "type": "tcpdump"},
    ])
    targets = load_targets_file(_targets(tmp_path, conf))
    register_labs(targets)
    cfg = load_config(conf)
    # Lab first is the authority; adapter.conf's list still read (one release).
    assert [s.id for s in cfg.external_sensors] == ["Tcpdump-SW-A", "XPress-SW-A", "Zeek-A"]
    x = next(s for s in cfg.external_sensors if s.id == "XPress-SW-A")
    assert (x.vmid, x.transport_ip, x.type, x.capture_iface) == (
        104, "10.255.251.21", "packetxpress", "gre-cap",
    )
    # managed: external -> every lab sensor's vmid is teardown-protected;
    # protected: true also puts its id on the name guard.
    assert {104, 270, 260} <= set(cfg.protected_external_vmids)
    assert "XPress-SW-A" in cfg.protected_external_names
    assert "Zeek-A" not in cfg.protected_external_names
    # Only sensors merge: telemetry/attack/defence are not external sensors.
    assert "grafana" not in {s.id for s in cfg.external_sensors}
    # The registry is keyed by the resolved path, so a relative spelling hits.
    assert [s.id for s in lab_sensors_for(Path(conf))] == ["XPress-SW-A", "Zeek-A"]


def test_inventory_external_sensor_facts_reads_the_merged_list(tmp_path):
    conf = _conf(tmp_path / "a.conf")
    register_labs(load_targets_file(_targets(tmp_path, conf)))
    cfg = load_config(conf)
    facts = adapter_for_config(cfg).inventory.external_sensor_facts()
    assert [f["id"] for f in facts] == ["XPress-SW-A", "Zeek-A"]
    assert facts[0]["transport_ip"] == "10.255.251.21"


def test_tap_resolution_finds_a_lab_sensor(tmp_path):
    from axrange.o2control.taps import merge_external_sensors

    conf = _conf(tmp_path / "a.conf")
    register_labs(load_targets_file(_targets(tmp_path, conf)))
    merged = merge_external_sensors({}, load_config(conf).external_sensors)
    assert {"XPress-SW-A", "Zeek-A"} <= set(merged)


def test_a_config_without_a_registered_lab_is_untouched(tmp_path):
    conf_a = _conf(tmp_path / "a.conf")
    conf_b = _conf(tmp_path / "b.conf", sensors=[
        {"id": "Tcpdump-SW-A", "vmid": 260, "transport_ip": "10.255.251.12"},
    ])
    register_labs(load_targets_file(_targets(tmp_path, conf_a)))
    assert [s.id for s in load_config(conf_b).external_sensors] == ["Tcpdump-SW-A"]


def test_sensor_in_both_places_refuses_serve_start_by_name(tmp_path):
    conf = _conf(tmp_path / "a.conf", sensors=[
        {"id": "XPress-SW-A", "vmid": 104, "transport_ip": "10.255.251.21"},
    ])
    tf = _targets(tmp_path, conf)
    with pytest.raises(ApiSettingsError) as ei:
        create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))
    msg = str(ei.value)
    assert "target 'dev45'" in msg and "'XPress-SW-A'" in msg
    assert "both" in msg and "EXTERNAL_SENSORS" in msg


def test_serve_start_registers_the_lab(tmp_path):
    conf = _conf(tmp_path / "a.conf")
    tf = _targets(tmp_path, conf)
    create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))
    assert [s.id for s in load_config(conf).external_sensors] == ["XPress-SW-A", "Zeek-A"]


def test_a_target_without_a_lab_is_todays_behaviour(tmp_path):
    conf = _conf(tmp_path / "a.conf", sensors=[
        {"id": "XPress-SW-A", "vmid": 104, "transport_ip": "10.255.251.21"},
    ])
    tf = _targets(tmp_path, conf, lab="")
    create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))
    cfg = load_config(conf)
    assert [s.id for s in cfg.external_sensors] == ["XPress-SW-A"]
    assert "XPress-SW-A" in cfg.protected_external_names


# ---- CLI ------------------------------------------------------------------------

def test_cli_reads_the_labs_from_axrange_targets(tmp_path, monkeypatch):
    from axrange.cli.main import _register_labs_from_env

    conf = _conf(tmp_path / "a.conf")
    tf = _targets(tmp_path, conf)
    assert [(ac, [c.id for c in lab.sensors]) for ac, lab in load_labs_from_targets_file(tf)] == [
        (conf, ["XPress-SW-A", "Zeek-A"]),
    ]
    monkeypatch.setenv("AXRANGE_TARGETS", str(tf))

    class _Args:
        command = "tap"

    _register_labs_from_env(_Args())
    assert [s.id for s in load_config(conf).external_sensors] == ["XPress-SW-A", "Zeek-A"]


def test_cli_missing_targets_file_is_a_config_error(tmp_path, monkeypatch):
    from axrange.cli.main import _register_labs_from_env

    monkeypatch.setenv("AXRANGE_TARGETS", str(tmp_path / "nope.yaml"))

    class _Args:
        command = "tap"

    with pytest.raises(ConfigError, match="does not exist"):
        _register_labs_from_env(_Args())
