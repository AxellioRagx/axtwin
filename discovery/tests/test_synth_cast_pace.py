"""The pace-test round trip (2026-11-19): a cast from a one-hour axquery PIT of a known twin.

Ground truth: `catalog/twins/pace-test` -- four clients each running dig/curl/smbclient against
bind9, nginx and Samba through one router. These tests pin what that round trip measured:

* emits are paced PER EMIT from `ir_edge.flows` (a one-window PIT has no rate quantiles, and
  every emit used to fire every 512 s);
* a persona whose every emit has a real client tool is `drive: command`, with its tools'
  packages installed on its hosts;
* axquery's `tls` label weights and paces 443;
* the gateway address is the router's leg, not one more host;
* sink groups no assigned persona dials are not deployed.
"""
import json

import pytest

from aximprint.synth.personas import (
    COMMAND_PROTO_PACKAGES, client_packages, command_knobs, emit_flow_rates, pace_for,
    synthesize_personas,
)

HOUR = 3600.0


def _pace_pit():
    """pace-test's first hour, reduced: 2 clients, 3 servers, the gateway, one external peer."""
    ents = []
    for i in (2, 3):
        ents.append({"entity_key": f"address:10.72.1.{i}", "role": "generic-endpoint",
                     "locality": "local", "segment": "10.72.1.0/24",
                     "listen_ports": "[]", "addresses": f'["10.72.1.{i}"]',
                     "dial_ports": '["443/tcp","445/tcp","53/udp","80/tcp"]'})
    for ip, role, listen in (("10.72.2.11", "dns-resolver", '["53/udp"]'),
                             ("10.72.2.12", "web-tier", '["443/tcp","80/tcp"]'),
                             ("10.72.2.13", "file-server", '["445/tcp"]')):
        ents.append({"entity_key": f"address:{ip}", "role": role, "locality": "local",
                     "segment": "10.72.2.0/24", "listen_ports": listen,
                     "addresses": f'["{ip}"]', "dial_ports": "[]"})
    ents.append({"entity_key": "address:10.72.2.1", "role": None, "locality": "local",
                 "segment": "10.72.2.0/24", "listen_ports": "[]", "dial_ports": "[]",
                 "addresses": '["10.72.2.1"]'})
    edges = []
    for i in (2, 3):
        src = f"address:10.72.1.{i}"
        edges += [
            {"src_key": src, "dst_key": "address:10.72.2.11", "proto": "dns", "flows": 4200},
            {"src_key": src, "dst_key": "address:10.72.2.12", "proto": "http", "flows": 550},
            {"src_key": src, "dst_key": "address:10.72.2.12", "proto": "tls", "flows": 548},
            {"src_key": src, "dst_key": "address:10.72.2.12", "proto": "icmp", "flows": 386},
            {"src_key": src, "dst_key": "address:10.72.2.13", "proto": "smb", "flows": 206},
        ]
    edges.append({"src_key": "address:10.72.2.1", "dst_key": "address:10.72.2.11",
                  "proto": "arp", "flows": 82})
    return {"ir_pit": [{"observed_from": 0.0, "observed_to": HOUR}],
            "ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": []}


def test_emit_rates_are_flows_per_member_host_per_second_and_tls_counts_for_443():
    rates = emit_flow_rates(_pace_pit())
    assert rates[("generic-endpoint", "53/udp", "dns-resolver")] == pytest.approx(4200 / HOUR)
    assert rates[("generic-endpoint", "445/tcp", "file-server")] == pytest.approx(206 / HOUR)
    # axquery says `tls`, Zeek says `ssl`: both are 443.
    assert rates[("generic-endpoint", "443/tcp", "web-tier")] == pytest.approx(548 / HOUR)
    assert rates[("generic-endpoint", "80/tcp", "web-tier")] == pytest.approx(550 / HOUR)


def test_a_pit_with_no_span_yields_no_rates_and_keeps_the_group_cadence():
    pit = _pace_pit()
    pit["ir_pit"] = [{"observed_from": None, "observed_to": None}]
    assert emit_flow_rates(pit) == {}


@pytest.mark.parametrize("rate,expected", [
    (4200 / HOUR, ("cyclic:7s", "med")),     # 2 msgs would need 1.7 s: med's 8 fit the floor
    (206 / HOUR, ("cyclic:35s", "low")),
    (0.0001, ("cyclic:600s", "low")),        # clamped at 10 min, not silently slower
    (1000.0, ("cyclic:5s", "bulk")),         # the 5 s floor wins; the cadence says so
])
def test_pace_reproduces_the_rate_within_the_documented_clamps(rate, expected):
    assert pace_for(rate) == expected


def test_pace_divides_the_rate_across_a_fanout_and_honours_a_floor_tier():
    assert pace_for(8 / 60, peers=4) == ("cyclic:60s", "low")
    assert pace_for(206 / HOUR, min_tier="med")[1] == "med"


def test_the_client_persona_is_command_driven_and_paced_per_emit():
    personas = synthesize_personas(_pace_pit())
    doc = next(p["persona"] for pid, p in personas.items()
               if pid.startswith("cast-generic-endpoint"))
    assert doc["drive"] == "command"
    by_proto = {e["proto"]: (e["cadence"], e["volume"]) for e in doc["emits"]}
    assert by_proto["dns"] == ("cyclic:7s", "med")
    assert by_proto["smb"] == ("cyclic:35s", "low")
    assert by_proto["https"][0] != "cyclic:512s"       # the old one-cadence-fits-all fallback
    assert by_proto["icmp"] == ("cyclic:19s", "low")   # 386 pings/h to the web tier
    assert next(e for e in doc["emits"] if e["proto"] == "icmp")["peer_role"] == "web-tier"
    assert doc["knobs"]["auth_credential"] == "none"
    assert doc["knobs"]["smb_share"] == "share"
    assert client_packages({"persona": doc}) == ("curl", "dnsutils", "smbclient")


def test_a_persona_with_a_protocol_no_tool_covers_stays_scripted():
    pit = _pace_pit()
    for e in pit["ir_entity"]:
        if e["role"] == "generic-endpoint":
            e["dial_ports"] = '["9997/tcp"]'
        if e["role"] == "web-tier":
            e["listen_ports"] = '["9997/tcp"]'
    doc = next(iter(synthesize_personas(pit).values()))["persona"]
    assert doc["drive"] == "scripted"
    assert "auth_credential" not in doc["knobs"]
    assert client_packages({"persona": doc}) == ()


def test_the_command_package_table_agrees_with_axranges_command_driver():
    """The table is a local copy (aximprint does not import axrange at run time); this is
    what keeps it from drifting from the driver that actually probes for the binaries."""
    load_persona = pytest.importorskip("axrange.personas.schema").load_persona
    required_packages = pytest.importorskip(
        "axrange.personas.drivers.command").required_packages
    for proto, pkgs in COMMAND_PROTO_PACKAGES.items():
        emit = {"proto": proto, "peer_role": "x", "direction": "EW",
                "cadence": "cyclic:60s", "volume": "low"}
        if proto != "icmp":
            emit["ports"] = [{"dns": 53, "http": 80, "https": 443, "smb": 445}[proto]]
        persona = load_persona({"id": "p", "os_family": "linux", "drive": "command",
                                "emits": [emit], "requires": [],
                                "knobs": command_knobs()})
        base = {"iputils-ping"}           # in the base cloud image, never installed by a cast
        assert set(required_packages(persona)) - base == set(pkgs), proto


# -- the compiler and the cast CLI --------------------------------------------------------

WINDOW = 3600
W1 = 1_754_000_000.0 - (1_754_000_000.0 % WINDOW)


def _flow(ts, src, dst, dport=445, proto="tcp"):
    from aximprint.events import FlowEvent

    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto=proto, service="", duration=1.0, bytes_out=100, bytes_in=200,
                     pkts_out=2, pkts_in=2, state="SF", state_class="established",
                     local_src=True, local_dst=True)


def _pit(tmp_path, events):
    """Events -> belief store -> PIT, the way `test_synth_compiler._pit` does it."""
    from aximprint.ir.materialize import materialize
    from aximprint.learners import extract_assertions
    from aximprint.provenance import build_stamp
    from aximprint.store.belief_store import BeliefStore
    from aximprint.windowing import WindowManager

    st = BeliefStore(str(tmp_path / "s.duckdb"))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    st.write_entity_roster("/cap/one", wm.entity_roster())
    st.write_run({"source": "/cap/one", "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12}, build_stamp(), len(wins), W1, W1 + 1)
    pit = materialize(st)
    pit.pop("_stats")
    return pit


def _gw_events(gw_dport=None):
    """Two segments; the router's `.1` seen only as a talker to the DNS server (its ARP/ICMP on
    the wire), or -- with `gw_dport` -- as a server something dials."""
    ev = [_flow(W1 + 1, "10.0.1.10", "10.0.2.30"),             # file-server on 10.0.2.0/24
          _flow(W1 + 2, "10.0.1.11", "10.0.2.30"),
          _flow(W1 + 3, "10.0.2.1", "10.0.2.30", 53, proto="udp")]
    if gw_dport:
        ev.append(_flow(W1 + 4, "10.0.1.10", "10.0.2.1", gw_dport))
    return ev


def test_the_gateway_address_is_folded_into_the_router(tmp_path):
    from aximprint.synth.compiler import compile_twin

    out = compile_twin(_pit(tmp_path, _gw_events()))
    nodes = out["twin_config"]["twin_config"]["nodes"]
    assert not any(n.get("role") != "gw" and "10-0-2" in str(n.get("net"))
                   and n.get("role") != "file-server" for n in nodes)
    assert any("gateway position (10.0.2.1)" in c for c in out["caveats"])


def test_a_server_at_the_gateway_address_is_kept(tmp_path):
    from aximprint.synth.compiler import compile_twin

    out = compile_twin(_pit(tmp_path, _gw_events(gw_dport=445)))
    assert not any("gateway position" in c for c in out["caveats"])


def test_drop_indexed_rekeys_provenance_with_the_items():
    from aximprint.cli import _drop_indexed

    out = {"provenance": {"fields": {
        "twin_config.nodes[0]": "a", "twin_config.nodes[1].role": "b",
        "twin_config.nodes[2]": "c", "twin_config.nodes[2].image": "c2",
        "twin_config.segments[1]": "s"}}}
    kept = _drop_indexed(out, "twin_config.nodes", ["A", "B", "C"], {1})
    assert kept == ["A", "C"]
    assert out["provenance"]["fields"] == {
        "twin_config.nodes[0]": "a", "twin_config.nodes[1]": "c",
        "twin_config.nodes[1].image": "c2", "twin_config.segments[1]": "s"}


def test_sink_groups_no_assigned_persona_dials_are_pruned_and_the_segment_with_them():
    from aximprint.cli import _prune_unused_sink

    tc = {"segments": [{"name": "internet-sink"}, {"name": "lan"}],
          "nodes": [{"name": "internet-sink-8", "role": "internet-sink-8", "net": "internet-sink"},
                    {"name": "cli", "role": "client", "net": "lan"},
                    {"name": "gw", "role": "gw", "kind": "router",
                     "nets": [{"segment": "internet-sink"}, {"segment": "lan"}]}],
          "conduits": []}
    out = {"twin_config": {"twin_config": tc}, "caveats": [], "provenance": {"fields": {}}}
    personas = {"cast-client": {"persona": {"emits": [{"peer_role": "web"}]}},
                "cast-dns": {"persona": {"emits": [{"peer_role": "internet-sink-8"}]}}}
    _prune_unused_sink(out, personas, used={"cast-client"})
    assert [n["name"] for n in tc["nodes"]] == ["cli", "gw"]
    assert [s["name"] for s in tc["segments"]] == ["lan"]
    assert tc["nodes"][1]["nets"] == [{"segment": "lan"}]
    assert "internet-sink-8" in out["caveats"][0]


def test_a_dialled_sink_group_survives_the_prune():
    from aximprint.cli import _prune_unused_sink

    tc = {"segments": [{"name": "internet-sink"}],
          "nodes": [{"name": "internet-sink-1", "role": "internet-sink-1"},
                    {"name": "internet-sink-8", "role": "internet-sink-8"}], "conduits": []}
    out = {"twin_config": {"twin_config": tc}, "caveats": [], "provenance": {"fields": {}}}
    personas = {"p": {"persona": {"emits": [{"peer_role": "internet-sink-1"}]}}}
    _prune_unused_sink(out, personas, used={"p"})
    assert [n["role"] for n in tc["nodes"]] == ["internet-sink-1"]
    assert tc["segments"] == [{"name": "internet-sink"}]


def test_command_clients_get_their_tools_as_pkg_installs_ahead_of_scripts():
    from aximprint.cli import _install_client_tools

    nodes = [{"name": "cli", "role": "client", "count": 2,
              "install": [{"pkg": "curl"}, {"script": "scripts/x.sh"}]},
             {"name": "srv", "role": "server"}]
    personas = {"cast-client": {"persona": {"drive": "command", "emits": [
        {"proto": "dns"}, {"proto": "https"}, {"proto": "smb"}]}}}
    _install_client_tools(nodes, [{"node": "cli01", "persona": "cast-client"}], personas)
    assert nodes[0]["install"] == [{"pkg": "dnsutils"}, {"pkg": "smbclient"},
                                   {"pkg": "curl"}, {"script": "scripts/x.sh"}]
    assert "install" not in nodes[1]
    assert json.dumps(nodes)          # stays plain data
