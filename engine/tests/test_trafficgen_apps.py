"""trafficgen B1 (`engine/src/axrange/trafficgen/SPEC.md`, "Config"): the
traffic-app registry, the app-mix persona shape, the shipped catalog, and the
read side of the API (`GET /api/traffic-apps[/{id}]`, `mix` on
`GET /api/personas`)."""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from axrange.personas.registry import PERSONAS
from axrange.personas.schema import MixEntry, PersonaSchemaError, load_persona
from axrange.trafficgen.apps import AppSchemaError, list_apps, load_app

from tests.test_api_edit_model import _client, env  # noqa: F401 -- env is a fixture

CATALOG_APPS = {"http-browse", "rest-api-call", "dns-lookup", "tls-session", "sql-query"}
CATALOG_MIXES = {"web-browse": {"http-browse", "tls-session"},
                 "api-east-west": {"rest-api-call"}, "sql-oltp": {"sql-query"}}

GOOD = """app:
  id: {id}
  label: "Probe"
  proto: http
  transport: tcp
  port: 80
  real_server_ok: false
  source:
    program:
      - {{ send_text: "GET {{path}} HTTP/1.1\\r\\nHost: {{server_name}}\\r\\n\\r\\n" }}
      - {{ reply: "48545450", size: "{{n}}", fill: "41" }}
  tunables:
    path: {{ default: "/" }}
    n: {{ default: 100, min: 10, max: 1000 }}
"""


def _write(root: Path, app_id: str, text: str) -> Path:
    d = root / app_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "app.yaml").write_text(text)
    return d / "app.yaml"


# --- the shipped catalog ------------------------------------------------------
def test_the_catalog_ships_five_apps_that_all_load():
    apps, warnings = list_apps()
    assert warnings == []
    assert set(apps) == CATALOG_APPS
    assert {a.id: a.source_kind for a in apps.values()} == {
        "http-browse": "program", "rest-api-call": "program", "dns-lookup": "program",
        "tls-session": "pcap", "sql-query": "program"}
    assert apps["dns-lookup"].transport == "udp"
    assert [a.id for a in apps.values() if a.real_server_ok] == ["http-browse"]


def test_the_catalog_mixes_name_installed_apps_and_cover_them_in_emits():
    apps, _ = list_apps()
    for mid, want in CATALOG_MIXES.items():
        p = PERSONAS[mid]
        assert p.drive == "trex-astf" and p.category == "traffic"
        assert {e.app for e in p.mix} == want
        emitted = {(e.proto, port) for e in p.emits for port in e.ports}
        for e in p.mix:
            assert e.weight > 0
            a = apps[e.app]
            assert (a.proto, a.port) in emitted, (mid, e.app)


def _render(app, **over):
    vals = {k: t.default for k, t in app.tunables.items()}
    vals.update(server_name="db01.corp.example", **over)
    out = []
    for st in app.program:
        for k in ("send", "reply"):
            if k in st:
                out.append((k, bytes.fromhex(st[k])))
        for k in ("send_text", "reply_text"):
            if k in st:
                out.append((k.split("_")[0], st[k].format(**vals).encode()))
    return out


def test_rest_content_lengths_match_their_bodies():
    """The REST bodies are literal JSON with fixed Content-Length values; a
    body edited without its length would break the exchange silently."""
    app = list_apps()[0]["rest-api-call"]
    msgs = _render(app)
    assert len(msgs) == 4
    for _, m in msgs:
        head, _, body = m.partition(b"\r\n\r\n")
        cl = re.search(rb"Content-Length: (\d+)", head)
        if cl:
            assert int(cl.group(1)) == len(body), head[:40]
            assert body.startswith(b"{") and body.endswith(b"}")


def test_sql_messages_are_whole_mysql_packets():
    app = list_apps()[0]["sql-query"]
    for _, m in _render(app):
        i = 0
        while i < len(m):
            (n,) = struct.unpack("<I", m[i:i + 3] + b"\x00")
            i += 4 + n
        assert i == len(m)


def test_dns_messages_are_one_question_and_one_answer():
    app = list_apps()[0]["dns-lookup"]
    (_, q), (_, r) = _render(app)
    assert struct.unpack(">HHHHHH", q[:12])[1:] == (0x0100, 1, 0, 0, 0)
    assert struct.unpack(">HHHHHH", r[:12])[1:] == (0x8180, 1, 1, 0, 0)
    assert q[:2] == r[:2]


def test_the_tls_capture_is_beside_its_app():
    a = list_apps()[0]["tls-session"]
    assert a.pcap.name == "capture.pcap" and a.pcap.stat().st_size > 1000


# --- the loader's shape rules -------------------------------------------------
def test_a_good_app_loads(tmp_path):
    a = load_app(_write(tmp_path, "probe", GOOD.format(id="probe")))
    assert a.placeholders() == {"path", "server_name", "n"}
    assert a.tunables["n"].max == 1000


@pytest.mark.parametrize("edit,msg", [
    (lambda s: s.replace("id: probe", "id: other"), "directory name"),
    (lambda s: s.replace("transport: tcp", "transport: sctp"), "transport"),
    (lambda s: s.replace("port: 80", "port: 70000"), "port"),
    (lambda s: s.replace("  real_server_ok: false\n", "  real_server_ok: maybe\n"), "real_server_ok"),
    (lambda s: s.replace("    program:", "    pcap: x.pcap\n    program:"), "exactly one of program / pcap"),
    (lambda s: s.replace(', fill: "41"', ""), "size needs a fill"),
    (lambda s: s.replace('reply: "48545450"', 'reply: "zz"'), "hex"),
    (lambda s: s.replace("{path}", "{nope}"), "nope"),
    (lambda s: s.replace("default: 100", "default: 5"), "outside"),
    (lambda s: s.replace("  proto: http\n", "  proto: http\n  colour: red\n"), "unknown key"),
    (lambda s: s.replace('size: "{n}", ', 'size: "{n}", recv: 4, '), "exactly one of"),
    (lambda s: s.replace('path: { default: "/" }', 'server_name: { default: "x" }\n    path: { default: "/" }'), "shadow"),
])
def test_shape_errors_are_refused(tmp_path, edit, msg):
    f = _write(tmp_path, "probe", edit(GOOD.format(id="probe")))
    with pytest.raises(AppSchemaError, match=msg):
        load_app(f)


def test_udp_programs_have_no_recv(tmp_path):
    text = GOOD.format(id="probe").replace("transport: tcp", "transport: udp").replace(
        '      - { reply: "48545450", size: "{n}", fill: "41" }', "      - { recv: 10 }")
    with pytest.raises(AppSchemaError, match="UDP"):
        load_app(_write(tmp_path, "probe", text))


def test_an_all_digit_hex_must_be_quoted(tmp_path):
    text = GOOD.format(id="probe").replace('reply: "48545450"', "reply: 48545450")
    with pytest.raises(AppSchemaError, match="hex"):
        load_app(_write(tmp_path, "probe", text))


def test_a_pcap_must_stay_inside_its_directory(tmp_path):
    (tmp_path / "secret.pcap").write_bytes(b"x")
    text = GOOD.format(id="probe").split("  source:")[0] + "  source:\n    pcap: ../secret.pcap\n"
    with pytest.raises(AppSchemaError, match="inside"):
        load_app(_write(tmp_path, "probe", text))


def test_a_bad_file_is_a_warning_and_the_rest_still_load(tmp_path):
    _write(tmp_path, "good", GOOD.format(id="good"))
    _write(tmp_path, "bad", "app: [1, 2]\n")
    (tmp_path / "not-an-app").mkdir()
    apps, warnings = list_apps([tmp_path])
    assert set(apps) == {"good"}
    assert len(warnings) == 1 and "bad/app.yaml" in warnings[0]


def test_extra_dirs_add_to_and_override_the_catalog(tmp_path, monkeypatch):
    _write(tmp_path, "probe", GOOD.format(id="probe"))
    _write(tmp_path, "sql-query", GOOD.format(id="sql-query"))
    monkeypatch.setenv("AXRANGE_TRAFFIC_APPS_DIR", str(tmp_path))
    apps, warnings = list_apps()
    assert {"probe", "sql-query"} <= set(apps) and apps["sql-query"].label == "Probe"
    assert any("overrides" in w for w in warnings)


# --- the mix persona shape ----------------------------------------------------
def _mix(**extra):
    return {"persona": {"id": "m", "os_family": "linux", "drive": "trex-astf", "emits": [], **extra}}


def test_a_mix_loads_and_other_personas_have_none():
    p = load_persona(_mix(mix=[{"app": "a", "weight": 3}, {"app": "b"}]))
    assert p.mix == (MixEntry("a", 3.0), MixEntry("b", 1.0))
    assert PERSONAS["web-tier"].mix == ()


@pytest.mark.parametrize("mix", [{"app": "a"}, ["a"], [{"weight": 1}], [{"app": "a", "weight": "x"}],
                                 [{"app": "a", "weight": True}], [{"app": "a", "share": 1}]])
def test_mix_shape_errors(mix):
    with pytest.raises(PersonaSchemaError):
        load_persona(_mix(mix=mix))


# --- the API ------------------------------------------------------------------
def test_get_traffic_apps_lists_what_is_installed(env):  # noqa: F811
    settings, _ = env
    c, _, _ = _client(settings)
    j = c.get("/api/traffic-apps").json()
    by = {a["id"]: a for a in j["apps"]}
    assert set(by) == CATALOG_APPS and j["warnings"] == []
    assert by["sql-query"]["used_by"] == ["sql-oltp"]
    assert by["dns-lookup"]["used_by"] == []
    assert by["http-browse"]["tunables"]["resp_bytes"] == {"default": 8192, "min": 128, "max": 1048576}
    one = c.get("/api/traffic-apps/tls-session").json()
    assert one["source_kind"] == "pcap" and one["port"] == 443
    r = c.get("/api/traffic-apps/nope")
    assert r.status_code == 404 and "sql-query" in r.json()["detail"]["apps"]


def test_get_personas_carries_the_mix(env):  # noqa: F811
    settings, _ = env
    c, _, _ = _client(settings)
    by = {p["id"]: p for p in c.get("/api/personas").json()["personas"]}
    wb = by["web-browse"]
    assert [(m["app"], m["share"], m["proto"], m["port"]) for m in wb["mix"]] == [
        ("http-browse", 0.7, "http", 80), ("tls-session", 0.3, "tls", 443)]
    assert wb["real_server_ok"] is False          # tls-session is not
    assert by["web-tier"]["mix"] == [] and by["web-tier"]["real_server_ok"] is False
