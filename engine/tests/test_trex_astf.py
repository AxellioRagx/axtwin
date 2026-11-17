"""The `trex-astf` persona driver's pure parts (cfg renderer, schedule,
profile spec + builder) and its launch flow over a fake guest agent.

`build_astf_profile` runs here against a recording fake of `trex.astf.api`;
it was also run against the real v3.08 package once (2026-11-16, recorded in
`catalog/twins/trex-test/README.md`). Set `TREX_HOME` to a release
directory to repeat that check here.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys

import pytest

from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.personas.drivers import get_driver
from axrange.personas.drivers.base import PersonaDriverError, instance_paths
from axrange.personas.drivers.trex_astf import (
    TrexAstfDriver,
    build_astf_profile,
    daemon_unit,
    diurnal_steps,
    mult_at,
    normalize_schedule,
    parse_guest_net,
    profile_spec,
    render_controller,
    render_launch,
    render_trex_cfg,
)
from axrange.personas.schema import DRIVE_KINDS, Persona

KNOBS = {
    "side": "client",
    "crowd": {"range": "10.250.1.0/24", "count": 50},
    "servers": {"range": "10.250.2.0/28"},
    "templates": [
        {"name": "http", "pcap": "avl/delay_10_http_browsing_0.pcap", "cps": 20},
        {"name": "dns", "pcap": "avl/delay_10_dns_0.pcap", "cps": 30},
        {"name": "smb", "port": 445, "cps": 5,
         "program": [{"send": "00 00 00 45 fe 53 4d 42"}, {"recv": 120}]},
    ],
    "mult": 1.5,
    "schedule": [{"at": "5m", "mult": 2}],
}


# --- cfg ---------------------------------------------------------------------
def test_cfg_puts_the_real_nic_on_the_sides_port_and_dummy_on_the_other():
    c = render_trex_cfg(side="client", iface="eth0", ip="10.71.1.10", gateway="10.71.1.1")
    assert 'interfaces: ["eth0", "dummy"]' in c
    assert "low_end: true" in c and "port_limit: 2" in c
    assert c.index("ip: 10.71.1.10") < c.index("ip: 169.254.0.1")
    s = render_trex_cfg(side="server", iface="ens18", ip="10.71.2.10", gateway="10.71.2.1")
    assert 'interfaces: ["dummy", "ens18"]' in s
    assert s.index("ip: 169.254.0.1") < s.index("ip: 10.71.2.10")
    assert "default_gw: 10.71.2.1" in s


@pytest.mark.parametrize("kw", [
    {"side": "both"}, {"ip": "10.71.1"}, {"gateway": "nope"}, {"iface": "eth0; rm -rf /"},
])
def test_cfg_refuses_bad_input(kw):
    args = {"side": "client", "iface": "eth0", "ip": "10.71.1.10", "gateway": "10.71.1.1", **kw}
    with pytest.raises(PersonaDriverError):
        render_trex_cfg(**args)


# --- schedule ----------------------------------------------------------------
def test_schedule_normalizes_every_form():
    steps = normalize_schedule([
        {"at": "5m", "mult": 2}, {"every": "10m", "mult": [1, 0.5]}, {"daily": "14:30", "mult": 3},
    ])
    assert steps == [
        {"kind": "offset", "at_s": 300, "mult": 2.0},
        {"kind": "cycle", "every_s": 600, "mults": [1.0, 0.5]},
        {"kind": "daily", "at_s": 52200, "mult": 3.0},
    ]
    assert normalize_schedule(None) == []


@pytest.mark.parametrize("bad", [
    "5m", [{"mult": 2}], [{"at": "5m"}], [{"at": "5m", "every": "1m", "mult": 1}],
    [{"at": "soon", "mult": 1}], [{"daily": "25:00", "mult": 1}], [{"at": "1m", "mult": -1}],
    [{"diurnal": {"peak": 2}}], [{"diurnal": {"peak": 2, "trough": 1, "step": "7m"}}],
])
def test_schedule_refuses_bad_steps(bad):
    with pytest.raises(PersonaDriverError):
        normalize_schedule(bad)


def test_mult_at_offset_cycle_and_daily_with_later_steps_winning():
    off = normalize_schedule([{"at": "5m", "mult": 2}])
    assert mult_at(off, 1.0, 299, 0) == 1.0
    assert mult_at(off, 1.0, 300, 0) == 2.0
    cyc = normalize_schedule([{"every": "1m", "mult": [1, 3]}])
    assert [mult_at(cyc, 9, t, 0) for t in (0, 59, 60, 119, 120)] == [1, 1, 3, 3, 1]
    day = normalize_schedule([{"daily": "08:00", "mult": 2}, {"daily": "18:00", "mult": 0.5}])
    assert mult_at(day, 1, 0, 7 * 3600) == 0.5      # before 08:00: yesterday's 18:00 step
    assert mult_at(day, 1, 0, 9 * 3600) == 2
    assert mult_at(day, 1, 0, 19 * 3600) == 0.5
    both = normalize_schedule([{"daily": "08:00", "mult": 2}, {"at": "1m", "mult": 4}])
    assert mult_at(both, 1, 30, 9 * 3600) == 2 and mult_at(both, 1, 90, 9 * 3600) == 4


def test_diurnal_peaks_at_peak_at_and_bottoms_twelve_hours_later():
    steps = diurnal_steps(peak=3.0, trough=0.5, peak_at="14:00", step="1h")
    assert len(steps) == 24
    by_hour = {s["at_s"] // 3600: s["mult"] for s in steps}
    assert by_hour[14] == 3.0 and by_hour[2] == 0.5
    assert max(by_hour.values()) == 3.0 and min(by_hour.values()) == 0.5
    assert mult_at(steps, 1, 0, 14 * 3600 + 1800) == 3.0
    assert normalize_schedule([{"diurnal": {"peak": 3, "trough": 0.5}}]) == steps


# --- profile spec ------------------------------------------------------------
def test_profile_spec_ranges_templates_and_schedule():
    spec = profile_spec(KNOBS)
    assert spec["client_range"] == ["10.250.1.1", "10.250.1.50"]
    assert spec["server_range"] == ["10.250.2.1", "10.250.2.14"]
    assert [t["name"] for t in spec["templates"]] == ["http", "dns", "smb"]
    assert spec["templates"][2]["program"] == [{"send": "00000045fe534d42"}, {"recv": 120}]
    assert spec["mult"] == 1.5 and spec["schedule"][0]["at_s"] == 300
    json.dumps(spec)  # the guest reads it as JSON


def test_server_range_defaults_to_the_peer_and_a_server_shares_its_clients_profile():
    k = {**KNOBS, "servers": None, "peer_role": "trex-server"}
    assert profile_spec(k, peers={"trex-server": "10.71.2.10"})["server_range"] == ["10.71.2.10"] * 2
    with pytest.raises(PersonaDriverError, match="peer_role"):
        profile_spec(k, peers={})
    server = profile_spec({"side": "server", "profile_from": "x"}, shared=KNOBS)
    assert server["side"] == "server" and server["templates"] == profile_spec(KNOBS)["templates"]


@pytest.mark.parametrize("change", [
    {"side": "middle"}, {"templates": []}, {"crowd": {}},
    {"crowd": {"range": "10.250.1.0/24", "count": 300}},
    {"templates": [{"name": "a", "cps": 1}]},                                   # neither pcap nor program
    {"templates": [{"name": "a", "cps": 0.1, "pcap": "x.pcap"}]},               # under TRex's minimum
    {"templates": [{"name": "a", "cps": 1, "pcap": "../etc/x.pcap"}]},
    {"templates": [{"name": "a", "cps": 1, "program": [{"send": "00"}]}]},      # program without port
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"send": "zz"}]}]},
    {"templates": [{"name": "a", "cps": 1, "pcap": "x.pcap"}, {"name": "a", "cps": 1, "pcap": "y.pcap"}]},
    {"vlan": 5000},
    {"templates": [{"name": "a", "cps": 1, "pcap": "x.pcap", "servers": {}}]},       # servers without range
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"send": "00", "recv": 1}]}]},  # two ops
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"send": "0000", "size": 1}]}]},  # size < bytes
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"reply": "00", "size": 9}]}]},   # size without fill
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"send": "00", "fill": "ab"}]}]}, # fill without size
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"recv": 4, "size": 9}]}]},
    {"templates": [{"name": "a", "cps": 1, "port": 1, "program": [{"send": "00", "bogus": 1}]}]},
])
def test_profile_spec_refuses_bad_knobs(change):
    with pytest.raises(PersonaDriverError):
        profile_spec({**KNOBS, **change})


# --- profile builder (fake trex.astf.api) ------------------------------------
class _Rec:
    def __init__(self, name, *a, **kw):
        self.name, self.a, self.kw, self.calls = name, a, kw, []

    def send(self, b, size=0, fill=None):
        self.calls.append(("send", b) if not size else ("send", b, size, fill))

    def recv(self, n):
        self.calls.append(("recv", n))


class _FakeApi:
    def __getattr__(self, name):
        return lambda *a, **kw: _Rec(name, *a, **kw)


def test_build_profile_breaks_exact_cps_ratios_without_moving_the_rates():
    """The v3.08 KxuNuRand abort on exact ratios (4:8:2:1 is trex-test's mix):
    every template's cps is nudged by a DISTINCT tiny factor, so no two keep
    their exact ratio, and no rate moves by more than 1e-5."""
    spec = profile_spec({**KNOBS, "templates": [
        {"name": n, "pcap": f"{n}.pcap", "cps": c} for n, c in (("a", 20), ("b", 40), ("c", 10), ("d", 5))]})
    caps = build_astf_profile(spec, _FakeApi(), "/x").kw["cap_list"]
    got = [c.kw["cps"] for c in caps]
    assert got == pytest.approx([20, 40, 10, 5], rel=1e-5)
    assert got[1] / got[0] != 2 and got[0] / got[3] != 4
    assert spec["templates"][0]["cps"] == 20  # the caller's spec is untouched


def test_build_profile_one_group_per_template_and_mirrored_programs():
    prof = build_astf_profile(profile_spec(KNOBS), _FakeApi(), "/opt/trex/current")
    assert prof.name == "ASTFProfile"
    gen = prof.kw["default_ip_gen"]
    assert gen.kw["dist_client"].kw["ip_range"] == ["10.250.1.1", "10.250.1.50"]
    assert gen.kw["dist_server"].kw["ip_range"] == ["10.250.2.1", "10.250.2.14"]
    caps = prof.kw["cap_list"]
    assert [c.kw["tg_name"] for c in caps] == ["http", "dns"]
    assert caps[0].kw["file"] == "/opt/trex/current/avl/delay_10_http_browsing_0.pcap"
    (tpl,) = prof.kw["templates"]
    assert tpl.kw["tg_name"] == "smb"
    client, server = tpl.kw["client_template"], tpl.kw["server_template"]
    assert client.kw["port"] == 445 and client.kw["cps"] == pytest.approx(5, rel=1e-5)
    assert client.kw["program"].calls == [("send", bytes.fromhex("00000045fe534d42")), ("recv", 120)]
    assert server.kw["program"].calls == [("recv", 8), ("send", b"\x00" * 120)]
    assert server.kw["assoc"].kw["port"] == 445


def test_build_profile_per_template_servers_replies_fill_and_range_association():
    """trex-test's internal/external split: a template may name its own server
    range (one ip_gen per distinct range, shared by templates that name none);
    `reply` steps are server-sent bytes; size/fill pads a step to a file body;
    a program with its own range is associated by port AND range, so two
    sessions on port 445 (two file servers) stay distinct."""
    knobs = {**KNOBS, "templates": [
        {"name": "http", "pcap": "avl/h.pcap", "cps": 20},
        {"name": "dns", "pcap": "avl/d.pcap", "cps": 40, "servers": {"range": "203.0.113.0/29", "count": 2}},
        {"name": "rd", "port": 445, "cps": 3, "servers": {"range": "10.250.2.1/32"},
         "program": [{"send": "0011"}, {"reply": "aabb", "size": 10, "fill": "ff"}]},
        {"name": "wr", "port": 445, "cps": 2, "servers": {"range": "10.250.2.2/32"},
         "program": [{"send": "01", "size": 5, "fill": "00ee"}, {"recv": 3}]},
    ]}
    spec = profile_spec(knobs)
    assert spec["templates"][1]["server_range"] == ["203.0.113.1", "203.0.113.2"]
    assert "server_range" not in spec["templates"][0]
    prof = build_astf_profile(spec, _FakeApi(), "/t")
    http, dns = prof.kw["cap_list"]
    assert "ip_gen" not in http.kw                      # the profile-wide generator
    assert dns.kw["ip_gen"].kw["dist_server"].kw["ip_range"] == ["203.0.113.1", "203.0.113.2"]
    rd, wr = prof.kw["templates"]
    c, sv = rd.kw["client_template"], rd.kw["server_template"]
    assert c.kw["ip_gen"].kw["dist_server"].kw["ip_range"] == ["10.250.2.1", "10.250.2.1"]
    assert c.kw["program"].calls == [("send", b"\x00\x11"), ("recv", 10)]
    assert sv.kw["program"].calls == [("recv", 2), ("send", b"\xaa\xbb", 10, b"\xff")]
    assert sv.kw["assoc"].kw == {"port": 445, "ip_start": "10.250.2.1", "ip_end": "10.250.2.1"}
    c, sv = wr.kw["client_template"], wr.kw["server_template"]
    assert c.kw["program"].calls == [("send", b"\x01", 5, b"\x00\xee"), ("recv", 3)]
    assert sv.kw["program"].calls == [("recv", 5), ("send", b"\x00" * 3)]
    assert sv.kw["assoc"].kw["ip_start"] == "10.250.2.2"


def test_trex_test_persona_builds():
    """The shipped client persona (with its generated SMB sessions) passes the
    spec checks, and its SMB frames carry consistent NetBIOS lengths."""
    import yaml  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load((root / "catalog/twins/trex-test/personas/trex-crowd-client.yaml").read_text())
    spec = profile_spec(doc["persona"]["knobs"])
    smb = [t for t in spec["templates"] if t["name"].startswith("smb-")]
    assert [t["server_range"] for t in smb] == [["10.250.2.1"] * 2, ["10.250.2.2"] * 2]
    for t in smb:
        for st in t["program"]:
            (op,) = [k for k in st if k in ("send", "reply")]
            b = bytes.fromhex(st[op])
            assert b[4:8] == b"\xfeSMB"
            assert int.from_bytes(b[1:4], "big") + 4 == st.get("size", len(b))


@pytest.mark.skipif(not os.environ.get("TREX_HOME"), reason="TREX_HOME (a v3.08 release dir) not set")
def test_build_profile_against_the_real_release():
    home = os.environ["TREX_HOME"]
    sys.path.insert(0, f"{home}/automation/trex_control_plane/interactive")
    from trex.astf import api  # noqa: PLC0415

    j = build_astf_profile(profile_spec(KNOBS), api, home).to_json()
    assert sorted(j["tg_names"]) == ["dns", "http", "smb"]


# --- controller + launch -----------------------------------------------------
def test_controller_is_valid_python_and_embeds_the_tested_functions():
    src = render_controller()
    tree = ast.parse(src)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"mult_at", "build_astf_profile", "main", "counters"} <= names
    assert "/opt/trex/current/automation/trex_control_plane/interactive" in src
    for call in ("c.load_profile(", "c.start(mult=mult, client_mask=", "c.update(mult=want)", "get_traffic_tg_stats("):
        assert call in src


def test_launch_flushes_the_nic_and_starts_daemon_then_controller():
    p = Persona(id="trex-client", os_family="linux", drive="trex-astf", knobs=KNOBS)
    sh = render_launch("tc-trex-client", p, side="client", iface="eth0", ip="10.71.1.10", prefix=24, gateway="10.71.1.1")
    paths = instance_paths("tc-trex-client")
    assert "ip -4 addr flush dev eth0" in sh
    assert "--astf-client-mask 0x1" in sh and "--no-termio" in sh
    assert sh.index(f"--unit={daemon_unit('tc-trex-client')}") < sh.index(f"--unit={paths['unit']} ")
    assert "nohup" not in sh
    srv = render_launch("ts-trex-server", p, side="server", iface="eth0", ip="10.71.2.10", prefix=24, gateway="10.71.2.1")
    assert "--astf-server-only" in srv and "client-mask" not in srv
    if shutil.which("bash"):
        for s in (sh, srv):
            assert subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode == 0


def test_parse_guest_net_takes_the_default_routes_device():
    addr = json.dumps([
        {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}]},
        {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.71.1.10", "prefixlen": 24, "scope": "global"}]},
    ])
    route = json.dumps([{"dst": "default", "gateway": "10.71.1.1", "dev": "eth0"}])
    assert parse_guest_net(addr, route) == {"iface": "eth0", "ip": "10.71.1.10", "prefix": 24, "gateway": "10.71.1.1"}
    with pytest.raises(PersonaDriverError):
        parse_guest_net(addr, "[]")


# --- the driver over a fake guest agent ---------------------------------------
class _GA:
    def __init__(self, *, active=True, stats=None):
        self.files: dict[str, str] = {}
        self.execs: list[list[str]] = []
        self.active, self.stats = active, stats

    def exec(self, vmid, argv, *, timeout_s=300):
        self.execs.append(list(argv))
        if argv[:2] == ["ip", "-j"]:
            if "route" in argv:
                return GuestExecResult(0, json.dumps([{"gateway": "10.71.1.1", "dev": "eth0"}]), "")
            return GuestExecResult(0, json.dumps([{"ifname": "eth0", "addr_info": [
                {"family": "inet", "local": "10.71.1.10", "prefixlen": 24, "scope": "global"}]}]), "")
        if argv[0] == "cat":
            path = argv[1]
            if path.endswith("stats.json") and self.stats is not None:
                return GuestExecResult(0, json.dumps(self.stats), "")
            return GuestExecResult(0, self.files[path], "") if path in self.files else GuestExecResult(1, "", "no")
        if argv[:3] == ["systemctl", "is-active", "--quiet"]:
            return GuestExecResult(0 if self.active else 3, "", "")
        return GuestExecResult(0, "ok", "")

    def write_file(self, vmid, path, content, *, mode=None):
        self.files[path] = content


def test_driver_is_registered_and_its_kind_is_in_the_vocabulary():
    assert "trex-astf" in DRIVE_KINDS
    assert isinstance(get_driver("trex-astf"), TrexAstfDriver)


def test_start_writes_cfg_spec_controller_and_reuses_the_cached_leg_on_restart():
    p = Persona(id="trex-client", os_family="linux", drive="trex-astf", knobs=KNOBS)
    ga = _GA()
    out = TrexAstfDriver().start(ga, 101, p, instance_id="tc-trex-client")
    d = instance_paths("tc-trex-client")["dir"]
    assert out == {"started": True, "unit": instance_paths("tc-trex-client")["unit"], "side": "client"}
    assert 'interfaces: ["eth0", "dummy"]' in ga.files[f"{d}/trex_cfg.yaml"]
    assert json.loads(ga.files[f"{d}/spec.json"])["client_range"] == ["10.250.1.1", "10.250.1.50"]
    assert json.loads(ga.files[f"{d}/net.json"])["ip"] == "10.71.1.10"
    assert ["bash", f"{d}/launch.sh"] in ga.execs
    # A re-start (the NIC already flushed) reads net.json, never `ip -j` again.
    ga.execs.clear()
    TrexAstfDriver().start(ga, 101, p, instance_id="tc-trex-client")
    assert not any(e[:2] == ["ip", "-j"] for e in ga.execs)


def test_start_refuses_bad_knobs_before_touching_the_guest():
    p = Persona(id="trex-client", os_family="linux", drive="trex-astf", knobs={**KNOBS, "templates": []})
    ga = _GA()
    with pytest.raises(PersonaDriverError):
        TrexAstfDriver().start(ga, 101, p, instance_id="tc")
    assert ga.execs == [] and ga.files == {}


def test_stop_restores_the_leg_and_heartbeat_returns_stats():
    p = Persona(id="trex-client", os_family="linux", drive="trex-astf", knobs=KNOBS)
    stats = {"side": "client", "mult": 2.0, "templates": {"http": {"cps": 19.8}}}
    ga = _GA(stats=stats)
    TrexAstfDriver().start(ga, 101, p, instance_id="tc")
    hb = TrexAstfDriver().heartbeat(ga, 101, p, instance_id="tc")
    assert hb["alive"] is True and hb["stats"] == stats
    TrexAstfDriver().stop(ga, 101, p, instance_id="tc")
    restore = [e for e in ga.execs if e[:2] == ["sh", "-c"] and "addr replace" in e[2]]
    assert restore and "10.71.1.10/24 dev eth0" in restore[0][2] and "default via 10.71.1.1" in restore[0][2]
    assert TrexAstfDriver().heartbeat(_GA(active=False), 101, p, instance_id="tc")["alive"] is False
