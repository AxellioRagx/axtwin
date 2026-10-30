"""Splunk ingest plugin — the SIEM family's first vendor (`ingest/siem/SPEC.md`).

Every test stubs `urllib.request.urlopen` the way `test_axquery_ingest.py` does: the repo-wide
conftest forbids real network egress, and no Splunk is reachable from a test box anyway. The
fixtures are AUTHORED, not recorded — shaped from Splunk's documented `search/v2/jobs/export`
JSON output (`{"preview": false, "offset": n, "result": {...}, "lastrow": true}` per line),
`server/info` entries and the CIM field names — so a live Splunk may differ in detail; the
SPEC's live acceptance (a Corelight-fed index against the Zeek reader) is the check for that.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import re
import urllib.parse
import urllib.request

import pytest

from aximprint.events import FlowEvent, IdentityEvent, SensorHealthEvent, ServiceEvent
from aximprint.ingest import get_plugin, list_plugins
from aximprint.ingest.siem import splunk as mod
from aximprint.ingest.siem.http import ENV_API_KEY, ENV_PASSWORD, ENV_USER
from aximprint.ingest.siem.rows import ZEEK_CONN_STATE_CLASS
from aximprint.ingest.siem.splunk import EXPORT_PATH, INFO_PATH, SplunkError

BASE = "splunk:https://127.0.0.1:8089"
T0 = 1790000000.0


def _iso(ts: float) -> str:
    """`_time` as Splunk's JSON output spells it."""
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(timespec="milliseconds")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# -- authored rows --------------------------------------------------------------------

def _fw(ts, action="allowed", bytes_in=200, bytes_out=100, src="10.0.0.1", dst="10.0.0.2",
        dest_port=443, transport="tcp", **kw):
    """A CIM Network_Traffic row as a firewall add-on extracts it."""
    row = {"_time": _iso(ts), "sourcetype": "pan:traffic", "index": "fw", "src_ip": src,
           "src_port": 51000, "dest_ip": dst, "dest_port": dest_port, "transport": transport,
           "app": "ssl", "action": action, "bytes_in": bytes_in, "bytes_out": bytes_out,
           "packets_in": 3 if bytes_in else 0, "packets_out": 2, "duration": 5}
    row.update(kw)
    return row


def _netflow(ts, **kw):
    """An unpaired NetFlow row: one direction, a single `bytes`, no action."""
    row = {"_time": _iso(ts), "sourcetype": "stream:netflow", "index": "netflow",
           "src_ip": "10.0.0.5", "src_port": 40000, "dest_ip": "10.0.0.9", "dest_port": 53,
           "transport": "udp", "bytes": 80, "packets": 1}
    row.update(kw)
    return row


def _corelight(ts, stem="conn", **zeek):
    """A Corelight add-on row: the Zeek record verbatim in `_raw`, plus the extracted fields."""
    rec = {"ts": ts, "uid": "CAbc1", "id.orig_h": "10.0.0.1", "id.orig_p": 51000,
           "id.resp_h": "10.0.0.2", "id.resp_p": 443, "proto": "tcp", "service": "ssl",
           "duration": 1.5, "orig_bytes": 100, "resp_bytes": 200, "orig_pkts": 2,
           "resp_pkts": 3, "conn_state": "SF", "local_orig": True, "local_resp": True}
    rec.update(zeek)
    return {"_time": _iso(ts), "sourcetype": f"corelight_{stem}", "index": "corelight",
            "_raw": json.dumps(rec), **{k: v for k, v in rec.items() if k != "ts"}}


# -- the stubbed Splunk --------------------------------------------------------------

_SPL_BOUNDS = re.compile(r"earliest=(\d+) latest=(\d+)")
_SPL_HEAD = re.compile(r"\| head (\d+)")


def _stream_of(spl: str) -> str:
    if spl.startswith("| tstats min(_time)"):
        return "span"
    if spl.startswith("| tstats"):
        return "tstats"
    if "tag=network tag=communicate" in spl:
        return "flow"
    if "tag=resolution" in spl:
        return "dns"
    if "tag=web" in spl:
        return "web"
    if "tag=certificate" in spl:
        return "certs"
    if spl.startswith("search") and "sourcetype=corelight_*" in spl and " NOT " not in spl:
        return "zeek"
    raise AssertionError(f"unrecognized SPL: {spl}")


def _server(monkeypatch, tables: dict | None = None, *, span=None, info=None, seen=None,
            fill=None):
    """Route every urlopen through a fake Splunk.

    `tables` holds authored rows per stream (`flow`, `dns`, `web`, `certs`, `zeek`, `tstats`);
    the fake honours `earliest`/`latest` on `_time` (`latest` exclusive) and `| head`, which is
    what makes the leaf discipline observable. `fill(stream, lo, hi, limit)` overrides a
    stream's answer outright for the paging tests.
    """
    tables = tables or {}
    seen = seen if seen is not None else []
    span = [{"index": "fw", "first_ts": str(int(T0)), "last_ts": str(int(T0 + 3600)),
             "events": "12"}] if span is None else span
    info = {"entry": [{"content": {"version": "9.2.1", "serverName": "sh1"}}]} if info is None \
        else info

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        parsed = urllib.parse.urlparse(req.full_url)
        if parsed.path == INFO_PATH:
            return _Resp(json.dumps(info).encode())
        assert parsed.path == EXPORT_PATH, parsed.path
        form = dict(urllib.parse.parse_qsl(req.data.decode()))
        assert form["output_mode"] == "json"
        spl = form["search"]
        kind = _stream_of(spl)
        if kind == "span":
            rows = span
        else:
            lo, hi = (int(x) for x in _SPL_BOUNDS.search(spl).groups())
            limit = int(_SPL_HEAD.search(spl).group(1))
            if fill is not None:
                rows = fill(kind, lo, hi, limit)
            else:
                rows = [r for r in tables.get(kind, [])
                        if lo <= _dt.datetime.fromisoformat(r["_time"]).timestamp() < hi][:limit]
        body = "\n".join(json.dumps({"preview": False, "offset": i, "result": r,
                                     "lastrow": i == len(rows) - 1})
                         for i, r in enumerate(rows))
        return _Resp(body.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _spls(seen) -> list[str]:
    return [dict(urllib.parse.parse_qsl(r.data.decode()))["search"] for r in seen
            if r.data is not None]


def _small_pages(monkeypatch, page=100, ceiling=400):
    monkeypatch.setattr(mod, "PAGE_ROWS", page)
    monkeypatch.setattr(mod, "LEAF_ROWS_MAX", ceiling)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    for k in (ENV_USER, ENV_PASSWORD, ENV_API_KEY):
        monkeypatch.delenv(k, raising=False)


def _pull(source=BASE, lo=T0, hi=T0 + 3600):
    return list(get_plugin("splunk").events(source, lo, hi))


# -- listing ----------------------------------------------------------------------------

def test_listed_as_an_http_plugin_with_the_closed_shared_schema():
    row = next(r for r in list_plugins() if r["name"] == "splunk")
    assert row["source_kind"] == "http"
    schema = row["config_schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["source"]
    assert "index" not in schema["properties"], "plugin options ride the locator, not the schema"


# -- locator ----------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "splunk:127.0.0.1:8089", "splunk:splunk.example:8089", "splunk:", "https://x:8089",
    "elastic:https://x:8089",
])
def test_locator_refuses_a_bare_host_and_anything_not_splunk(bad):
    with pytest.raises(SplunkError):
        get_plugin("splunk").locator(bad)


def test_locator_options_are_a_closed_table():
    loc = get_plugin("splunk").locator("splunk:https://x:8089/?index=netflow&datamodel=Network_Traffic")
    assert loc.base == "https://x:8089" and loc.options == {"index": "netflow",
                                                           "datamodel": "Network_Traffic"}
    with pytest.raises(SplunkError, match="not one of"):
        get_plugin("splunk").locator("splunk:https://x:8089?sourcetype=foo")
    with pytest.raises(SplunkError, match="twice"):
        get_plugin("splunk").locator("splunk:https://x:8089?index=a&index=b")
    with pytest.raises(SplunkError, match="datamodel"):
        get_plugin("splunk").locator("splunk:https://x:8089?datamodel=Web")


@pytest.mark.parametrize("bad", [
    "splunk:https://admin:secret@127.0.0.1:8089",
    "splunk:https://127.0.0.1:8089?token=abc",
    "splunk:https://127.0.0.1:8089?password=abc",
    "splunk:https://127.0.0.1:8089?api_key=abc",
])
def test_a_credential_in_the_locator_is_refused_by_name(bad):
    """The locator is `argv` (`aximprint ingest --source …`) and provenance; a secret there is
    in `ps`, shell history and every run record."""
    with pytest.raises(SplunkError, match="credential"):
        get_plugin("splunk").locator(bad)


# -- credentials ------------------------------------------------------------------------

def test_no_environment_credential_means_no_authorization_header(monkeypatch):
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    _pull()
    assert seen and all(r.get_header("Authorization") is None for r in seen)


def test_user_and_password_become_basic_auth(monkeypatch):
    monkeypatch.setenv(ENV_USER, "ro")
    monkeypatch.setenv(ENV_PASSWORD, "s3cret")
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    _pull()
    assert {r.get_header("Authorization") for r in seen} == {"Basic cm86czNjcmV0"}


def test_an_api_key_becomes_a_bearer_token(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "tok")
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    _pull()
    assert {r.get_header("Authorization") for r in seen} == {"Bearer tok"}


def test_basic_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv(ENV_USER, "ro")
    monkeypatch.setenv(ENV_PASSWORD, "s3cret")
    monkeypatch.setenv(ENV_API_KEY, "tok")
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    _pull()
    assert {r.get_header("Authorization") for r in seen} == {"Basic cm86czNjcmV0"}


def test_a_401_names_the_environment_settings(monkeypatch):
    import urllib.error

    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "nope", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", refused)
    got = get_plugin("splunk").probe(BASE)
    assert got["reachable"] is False and "HTTP 401" in got["error"]
    assert ENV_API_KEY in got["error"]


# -- the query ---------------------------------------------------------------------------

def test_the_flow_search_is_cim_network_traffic_over_the_export_endpoint(monkeypatch):
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    _pull("splunk:https://127.0.0.1:8089?index=fw")
    paths = {urllib.parse.urlparse(r.full_url).path for r in seen}
    assert paths == {EXPORT_PATH}
    flow = [s for s in _spls(seen) if "tag=network tag=communicate" in s]
    assert flow, "the raw pull is tag=network tag=communicate"
    assert all(s.startswith("search index=fw ") for s in flow)
    assert all(f"earliest={int(T0)} latest={int(T0 + 3600)}" in s for s in flow)
    assert all(re.search(r"\| head \d+$", s) for s in flow), "a page cap per leaf"
    assert all(r.get_method() == "POST" for r in seen)


def test_every_stream_is_read_and_the_zeek_sourcetypes_are_kept_apart(monkeypatch):
    seen = _server(monkeypatch, {})
    _pull()
    kinds = {_stream_of(s) for s in _spls(seen)}
    assert kinds == {"zeek", "flow", "dns", "web", "certs"}
    for s in _spls(seen):
        if _stream_of(s) != "zeek":
            assert "NOT (sourcetype=corelight_*" in s


def test_an_unbounded_call_resolves_its_bounds_from_the_span(monkeypatch):
    seen = _server(monkeypatch, {"flow": [_fw(T0 + 1)]},
                   span=[{"index": "fw", "first_ts": str(int(T0)), "last_ts": str(int(T0 + 9)),
                          "events": "1"}])
    flows = [e for e in get_plugin("splunk").events(BASE) if isinstance(e, FlowEvent)]
    assert len(flows) == 1
    bounds = {_SPL_BOUNDS.search(s).groups() for s in _spls(seen) if _SPL_BOUNDS.search(s)}
    assert bounds == {(str(int(T0)), str(int(T0 + 10)))}, "last_ts is inclusive; the slice is half-open"


def test_a_search_error_message_fails_the_leaf_loud(monkeypatch):
    def fake_urlopen(req, timeout=None):
        body = json.dumps({"preview": False, "messages": [
            {"type": "FATAL", "text": "Error in 'search': index=nope does not exist"}]})
        return _Resp(body.encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SplunkError, match="does not exist"):
        _pull()


# -- paging --------------------------------------------------------------------------------

def test_a_full_page_splits_the_leaf_instead_of_trusting_it(monkeypatch):
    _small_pages(monkeypatch)
    asked = []

    def fill(kind, lo, hi, limit):
        if kind != "flow":
            return []
        asked.append((lo, hi, limit))
        if hi - lo > 50:
            return [_fw(lo + i % 10) for i in range(limit)]     # full -> must split
        return [_fw(lo + 1)]

    _server(monkeypatch, fill=fill)
    flows = [e for e in _pull(hi=T0 + 100) if isinstance(e, FlowEvent)]
    assert len(asked) > 1, "a full page must split, not be consumed"
    assert asked[0] == (int(T0), int(T0 + 100), 100)
    assert all(limit == 100 for _, _, limit in asked), "splitting never asks for more rows"
    assert flows and all(T0 <= f.ts < T0 + 100 for f in flows)
    assert len(flows) == len(asked) - 1, "exactly one flow per consumed (short) leaf"


def test_a_floor_width_leaf_widens_its_head_rather_than_splitting_finer(monkeypatch):
    _small_pages(monkeypatch, page=100, ceiling=800)
    burst = [_fw(T0 + 1, src_port=10000 + i) for i in range(250)]
    asked = []

    def fill(kind, lo, hi, limit):
        if kind != "flow":
            return []
        asked.append((lo, hi, limit))
        return [r for r in burst if lo <= T0 + 1 < hi][:limit]

    _server(monkeypatch, fill=fill)
    flows = [e for e in _pull(hi=T0 + 4) if isinstance(e, FlowEvent)]
    assert len(flows) == 250, "the whole burst, once"
    wide = [(lo, hi, limit) for lo, hi, limit in asked if limit > 100]
    assert wide and all(hi - lo <= 1 for lo, hi, _ in wide), "only a floor-width leaf widens"
    assert max(limit for _, _, limit in asked) == 400, "100 -> 200 -> 400 came back short"


def test_past_the_leaf_ceiling_the_leaf_is_refused_not_truncated(monkeypatch):
    _small_pages(monkeypatch)

    def fill(kind, lo, hi, limit):
        return [_fw(lo) for _ in range(limit)] if kind == "flow" else []

    _server(monkeypatch, fill=fill)
    with pytest.raises(SplunkError, match="will NOT help"):
        _pull(hi=T0 + 2)


def test_only_rows_whose_own_time_falls_in_a_leaf_are_emitted_by_it(monkeypatch):
    """A server that ignores `latest` (or a row straddling a boundary) must not be counted
    twice or outside the slice."""
    def fill(kind, lo, hi, limit):
        return [_fw(T0 + 5), _fw(T0 + 50), _fw(T0 + 500)] if kind == "flow" else []

    _server(monkeypatch, fill=fill)
    flows = [e for e in _pull(hi=T0 + 100) if isinstance(e, FlowEvent)]
    assert sorted(f.ts for f in flows) == [T0 + 5, T0 + 50]


# -- state_class per source family ----------------------------------------------------------

def test_a_zeek_fed_row_keeps_its_conn_state_and_takes_the_zeek_table(monkeypatch):
    _server(monkeypatch, {"zeek": [_corelight(T0 + 1, conn_state="SF"),
                                   _corelight(T0 + 2, conn_state="S0"),
                                   _corelight(T0 + 3, conn_state="REJ"),
                                   _corelight(T0 + 4, conn_state="RSTR")]})
    flows = sorted((e for e in _pull() if isinstance(e, FlowEvent)), key=lambda f: f.ts)
    assert [(f.state, f.state_class) for f in flows] == [
        ("SF", "established"), ("S0", "attempted"), ("REJ", "refused"), ("RSTR", "reset_resp")]
    assert all(f.state_class == ZEEK_CONN_STATE_CLASS[f.state] for f in flows)
    assert flows[0].local_src is True and flows[0].service == "ssl" and flows[0].duration == 1.5
    assert flows[0].src_ip == "10.0.0.1" and flows[0].dst_port == 443


def test_a_zeek_sourcetype_is_routed_through_zeek_in_whatever_stream_it_arrives(monkeypatch):
    """Recognised by sourcetype, not by which tag query fetched it."""
    _server(monkeypatch, {"flow": [_corelight(T0 + 1, conn_state="S1")]})
    flows = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert [(f.state, f.state_class) for f in flows] == [("S1", "established")]


@pytest.mark.parametrize("sourcetype", ["corelight_conn", "zeek:conn:json", "bro:conn:json",
                                        "zeek_conn", "corelight_conn_long"])
def test_every_zeek_sourcetype_spelling_reads_as_the_conn_stem(sourcetype):
    assert mod.zeek_stem_of(sourcetype) == "conn"
    assert mod.zeek_stem_of("pan:traffic") is None


def test_firewall_rows_map_allowed_with_reply_to_established(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1, action="allowed", bytes_in=200)]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("allowed", "established")
    assert f.bytes_in == 200 and f.bytes_out == 100 and not f.counters_imputed
    assert f.proto == "tcp" and f.service == "ssl" and f.duration == 5.0


def test_firewall_rows_map_allowed_with_no_reply_to_attempted(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1, action="allowed", bytes_in=0)]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("allowed", "attempted")


def test_firewall_rows_map_blocked_to_unknown_because_a_drop_proves_nothing(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1, action="blocked", bytes_in=0, bytes_out=60)]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("blocked", "unknown")


def test_an_unknown_action_is_unknown_and_kept_verbatim(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1, action="quarantined")]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("quarantined", "unknown")


def test_unpaired_netflow_is_unknown_with_counters_imputed(monkeypatch):
    _server(monkeypatch, {"flow": [_netflow(T0 + 1)]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert f.state_class == "unknown" and f.counters_imputed is True
    assert f.bytes_out == 80 and f.bytes_in == 0 and f.pkts_out == 1


def test_paired_netflow_is_still_unknown_but_its_counters_are_observed(monkeypatch):
    _server(monkeypatch, {"flow": [_netflow(T0 + 1, bytes_in=40, bytes_out=80,
                                            packets_in=1, packets_out=1)]})
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert f.state_class == "unknown" and f.counters_imputed is False
    assert (f.bytes_out, f.bytes_in) == (80, 40)


def test_locality_and_vlan_are_none_for_every_non_zeek_row(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1), _netflow(T0 + 2)]})
    for f in (e for e in _pull() if isinstance(e, FlowEvent)):
        assert f.local_src is None and f.local_dst is None
        assert f.vlan_src is None and f.vlan_dst is None


# -- identity and services -------------------------------------------------------------------

def test_network_resolution_rows_bind_each_address_answer_to_the_query(monkeypatch):
    _server(monkeypatch, {"dns": [{
        "_time": _iso(T0 + 1), "sourcetype": "infoblox:dns", "query": "www.example.com",
        "message_type": "Response", "answer": ["93.184.216.34", "www.example.com.cdn.net"],
        "ttl": "300", "src": "10.0.0.1", "dest": "10.0.0.53"}]})
    ids = [e for e in _pull() if isinstance(e, IdentityEvent)]
    assert [(i.kind, i.ip, i.hostname, i.record_ttl) for i in ids] == [
        ("dns", "93.184.216.34", "www.example.com", 300.0)]


def test_a_dns_query_row_binds_nothing(monkeypatch):
    _server(monkeypatch, {"dns": [{"_time": _iso(T0 + 1), "query": "www.example.com",
                                   "message_type": "Query"}]})
    assert not [e for e in _pull() if isinstance(e, IdentityEvent)]


def test_web_rows_become_http_services_on_the_responder(monkeypatch):
    _server(monkeypatch, {"web": [{
        "_time": _iso(T0 + 1), "sourcetype": "squid", "dest_ip": "10.0.0.80", "dest_port": "8080",
        "http_method": "GET", "site": "intranet.corp", "uri_path": "/index.html",
        "http_user_agent": "curl/8", "status": "200"}]})
    s, = [e for e in _pull() if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port) == ("http", "10.0.0.80", 8080)
    assert s.attrs == {"method": "GET", "host": "intranet.corp", "uri": "/index.html",
                       "user_agent": "curl/8", "status_code": "200"}


def test_certificate_rows_become_ssl_services_on_the_responder(monkeypatch):
    _server(monkeypatch, {"certs": [{
        "_time": _iso(T0 + 1), "sourcetype": "stream:tcp", "dest": "10.0.0.2",
        "ssl_name": "mail.corp", "ssl_version": "TLSv1.3", "ssl_subject": "CN=mail.corp",
        "ssl_issuer": "CN=Corp CA"}]})
    s, = [e for e in _pull() if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port) == ("ssl", "10.0.0.2", 443)
    assert s.attrs["server_name"] == "mail.corp" and s.attrs["issuer"] == "CN=Corp CA"


def test_corelight_identity_and_service_stems_reach_the_zeek_parsers(monkeypatch):
    dns = {"_time": _iso(T0 + 1), "sourcetype": "corelight_dns", "_raw": json.dumps({
        "ts": T0 + 1, "query": "dc1.corp", "answers": ["10.0.0.10"], "TTLs": [60.0]})}
    dhcp = {"_time": _iso(T0 + 2), "sourcetype": "corelight_dhcp", "_raw": json.dumps({
        "ts": T0 + 2, "mac": "aa:bb:cc:dd:ee:ff", "assigned_addr": "10.0.0.77",
        "host_name": "laptop-7", "lease_time": 3600.0})}
    ssl = {"_time": _iso(T0 + 3), "sourcetype": "corelight_ssl", "_raw": json.dumps({
        "ts": T0 + 3, "id.resp_h": "10.0.0.2", "id.resp_p": 443, "server_name": "mail.corp"})}
    _server(monkeypatch, {"zeek": [dns, dhcp, ssl]})
    events = _pull()
    ids = [e for e in events if isinstance(e, IdentityEvent)]
    assert {(i.kind, i.ip, i.hostname) for i in ids} == {
        ("dns", "10.0.0.10", "dc1.corp"), ("dhcp", "10.0.0.77", "laptop-7")}
    assert next(i for i in ids if i.kind == "dhcp").mac == "aa:bb:cc:dd:ee:ff"
    s, = [e for e in events if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port, s.attrs["server_name"]) == ("ssl", "10.0.0.2", 443, "mail.corp")


def test_streams_are_merged_by_ts_not_concatenated(monkeypatch):
    _server(monkeypatch, {
        "flow": [_fw(T0 + 10), _fw(T0 + 30)],
        "dns": [{"_time": _iso(T0 + 20), "query": "a.corp", "answer": "10.0.0.1"}],
    })
    ts = [e.ts for e in _pull()]
    assert ts == sorted(ts) and len(ts) == 3


# -- declines ----------------------------------------------------------------------------------

def test_alert_tables_are_named_into_unparsed_stems_never_mapped(monkeypatch):
    notice = {"_time": _iso(T0 + 1), "sourcetype": "corelight_notice", "_raw": json.dumps({
        "ts": T0 + 1, "note": "Scan::Port_Scan", "msg": "10.0.0.1 scanned"})}
    _server(monkeypatch, {"zeek": [notice]})
    plugin = get_plugin("splunk")
    events = list(plugin.events(BASE, T0, T0 + 3600))
    assert events == [], "an alert row yields nothing"
    assert {"Alerts", "Intrusion_Detection", "notable", "risk"} <= set(plugin.unparsed_stems)
    assert plugin.unparsed_stems["corelight_notice"] == 1
    assert plugin.unrecognized_stems == {}


def test_an_unknown_zeek_stem_is_counted_as_unrecognized(monkeypatch):
    _server(monkeypatch, {"zeek": [{"_time": _iso(T0 + 1), "sourcetype": "corelight_newthing",
                                    "_raw": "{}"}]})
    plugin = get_plugin("splunk")
    assert list(plugin.events(BASE, T0, T0 + 3600)) == []
    assert plugin.unrecognized_stems == {"corelight_newthing": 1}


# -- the aggregate fallback ---------------------------------------------------------------------

AGG = "splunk:https://127.0.0.1:8089?index=fw&datamodel=Network_Traffic"


def test_the_aggregate_fallback_is_tstats_labelled_and_announced(monkeypatch):
    seen = _server(monkeypatch, {"tstats": [
        {"_time": _iso(T0 + 1), "count": "7", "src": "10.0.0.1", "src_port": "51000",
         "dest": "10.0.0.2", "dest_port": "443", "transport": "tcp", "bytes_in": "1400",
         "bytes_out": "700", "packets_in": "21", "packets_out": "14", "duration": "5",
         "action": "allowed", "app": "ssl"}]})
    events = _pull(AGG)
    spls = [s for s in _spls(seen) if _stream_of(s) == "tstats"]
    assert spls and all("from datamodel=Network_Traffic.All_Traffic" in s for s in spls)
    assert all("where index=fw earliest=" in s and "span=1s" in s for s in spls)
    assert not any(_stream_of(s) == "flow" for s in _spls(seen)), "tstats REPLACES the raw pull"
    f, = [e for e in events if isinstance(e, FlowEvent)]
    assert f.counters_imputed is True and f.state == "tstats/allowed"
    assert f.state_class == "established" and f.bytes_in == 1400
    h, = [e for e in events if isinstance(e, SensorHealthEvent)]
    assert h.kind == "aggregate_fallback" and h.value == 7.0 and h.ts <= f.ts
    for silenced in ("cadence", "size distribution", "duration", "counters_imputed"):
        assert silenced in h.detail


def test_in_aggregate_mode_a_zeek_conn_row_is_counted_not_emitted_twice(monkeypatch):
    _server(monkeypatch, {"zeek": [_corelight(T0 + 1)], "tstats": []})
    plugin = get_plugin("splunk")
    events = list(plugin.events(AGG, T0, T0 + 3600))
    assert not [e for e in events if isinstance(e, FlowEvent)]
    assert plugin.unparsed_stems["corelight_conn (counted by tstats aggregate)"] == 1


def test_the_raw_pull_emits_no_aggregate_health_event(monkeypatch):
    _server(monkeypatch, {"flow": [_fw(T0 + 1)]})
    assert not [e for e in _pull() if isinstance(e, SensorHealthEvent)]


# -- probe ----------------------------------------------------------------------------------------

def test_probe_reports_span_reachability_and_the_indexes_it_will_read(monkeypatch):
    seen = _server(monkeypatch, span=[
        {"index": "fw", "first_ts": str(int(T0)), "last_ts": str(int(T0 + 100)), "events": "5"},
        {"index": "corelight", "first_ts": str(int(T0 - 50)), "last_ts": str(int(T0 + 60)),
         "events": "9"}])
    got = get_plugin("splunk").probe(BASE)
    assert got["supported"] and got["reachable"] and got["error"] is None
    assert (got["first_ts"], got["last_ts"]) == (T0 - 50, T0 + 100)
    assert got["extra"]["version"] == "9.2.1" and got["extra"]["index"] == "*"
    assert [i["index"] for i in got["extra"]["indexes"]] == ["fw", "corelight"]
    assert got["extra"]["events"] == 14 and got["extra"]["mode"] == "raw"
    assert got["vantage"] is None, "a SIEM does not know where the tap sat"
    assert {urllib.parse.urlparse(r.full_url).path for r in seen} == {INFO_PATH, EXPORT_PATH}


def test_probe_unreachable_is_an_answer_not_a_crash(monkeypatch):
    def down(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    got = get_plugin("splunk").probe(BASE)
    assert got["supported"] and got["reachable"] is False and "unreachable" in got["error"]


def test_probe_refuses_a_bare_host_like_the_locator_does():
    got = get_plugin("splunk").probe("splunk:10.0.0.1:8089")
    assert got["reachable"] is False and "absolute http(s) base URL" in got["error"]
