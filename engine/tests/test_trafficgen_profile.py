"""trafficgen B4 (`engine/src/axrange/trafficgen/SPEC.md`): the generic ASTF
profile builds each line from the generator document, with a fake TRex API
(the real release is exercised by `astf-sim` in the generator; see SPEC)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from axrange.trafficgen import astf_profile

FIX = Path(__file__).parent / "fixtures" / "trafficgen" / "three-tier-dc-manual"
PLAN = json.loads((FIX / "expected-plan.json").read_text())
DOCS = {g["host_id"]: g["document"] for g in PLAN["generators"]}


class _Obj:
    def __init__(self, kind, *a, **kw):
        self.kind, self.a, self.kw, self.calls = kind, a, kw, []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*a, **kw):
            self.calls.append((name, a, kw))
        return call


class _FakeApi:
    def __getattr__(self, name):
        return lambda *a, **kw: _Obj(name, *a, **kw)


def _build(gen, line):
    return astf_profile.build(DOCS[gen], line, _FakeApi(), "/files")


def test_the_file_imports_nothing_from_axrange():
    tree = ast.parse(Path(astf_profile.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m and m.startswith("axrange")}
    assert "trex.astf.api" in mods


def test_a_program_line_builds_mirrored_tcp_programs_at_the_line_rate():
    prof = _build("gen-rack1-lan--rack3-lan", "rack1-web--rack3-db--sql-oltp")
    assert prof.kind == "ASTFProfile" and "cap_list" not in prof.kw
    (tpl,) = prof.kw["templates"]
    ct, st = tpl.kw["client_template"], tpl.kw["server_template"]
    assert ct.kw["port"] == 3306 and ct.kw["cps"] == pytest.approx(20.0, rel=1e-5)
    gen = ct.kw["ip_gen"]
    assert gen.kw["dist_client"].kw["ip_range"] == ["10.200.0.11", "10.200.0.30"]
    assert gen.kw["dist_server"].kw["ip_range"] == ["10.200.2.5", "10.200.2.5"]
    assert gen.kw["glob"].kw["ip_offset"] == "0.0.0.0"
    assoc = st.kw["assoc"]
    assert (assoc.kw["port"], assoc.kw["ip_start"], assoc.kw["ip_end"]) == (3306, "10.200.2.5", "10.200.2.5")
    pc, ps = ct.kw["program"], st.kw["program"]
    assert pc.kw["stream"] is True
    # Server speaks first (the MySQL greeting): the client's first action is a recv.
    assert [c[0] for c in pc.calls] == ["recv", "send", "recv", "send", "recv", "delay", "send", "recv", "send"]
    assert [c[0] for c in ps.calls] == ["send", "recv", "send", "recv", "send", "recv", "send", "recv"]
    assert ("delay", (20000,), {}) in pc.calls
    sent = sum(len(c[1][0]) for c in pc.calls if c[0] == "send")
    assert sent == sum(c[1][0] for c in ps.calls if c[0] == "recv")


def test_a_mixed_line_builds_a_replay_and_a_padded_program():
    prof = _build("gen-internet--rack2-lan", "internet-users--rack2-web--web-browse")
    (cap,) = prof.kw["cap_list"]
    assert cap.kw["file"] == "/files/tls-session--capture.pcap" and cap.kw["cps"] == pytest.approx(9.0, rel=1e-5)
    (tpl,) = prof.kw["templates"]
    ps = tpl.kw["server_template"].kw["program"]
    body = [c for c in ps.calls if c[0] == "send"][1]
    assert body[2]["size"] == 8192 and body[2]["fill"]
    pc = tpl.kw["client_template"].kw["program"]
    assert [c for c in pc.calls if c[0] == "recv"][1][1] == (8192,)


def test_cps_ratios_are_nudged_apart():
    prof = _build("gen-internet--rack2-lan", "internet-users--rack2-web--web-browse")
    http = prof.kw["templates"][0].kw["client_template"].kw["cps"]
    tls = prof.kw["cap_list"][0].kw["cps"]
    assert http / tls != pytest.approx(21.0 / 9.0, rel=1e-9)
    assert http / tls == pytest.approx(21.0 / 9.0, rel=1e-5)


def test_udp_programs_carry_whole_messages():
    doc = {"generator": "g", "lines": [{"id": "dns", "clients": ["10.0.0.1", "10.0.0.9"], "servers": ["10.0.1.1", "10.0.1.1"],
           "templates": [{"app": "dns-lookup", "transport": "udp", "port": 53, "cps": 5,
                          "program": [{"send": "aabb"}, {"reply": "ccdd"}]}]}]}
    prof = astf_profile.build(doc, "dns", _FakeApi(), "/f")
    pc = prof.kw["templates"][0].kw["client_template"].kw["program"]
    assert pc.kw["stream"] is False
    assert pc.calls == [("send_msg", (b"\xaa\xbb",), {}), ("recv_msg", (1,), {})]


def test_an_unknown_line_is_refused():
    with pytest.raises(ValueError, match="not in generator"):
        _build("gen-rack1-lan--rack3-lan", "nope")
