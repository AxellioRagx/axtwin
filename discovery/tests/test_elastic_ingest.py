"""Elastic ingest plugin — the SIEM family's second vendor (`ingest/siem/SPEC.md`).

Every test stubs `urllib.request.urlopen` the way `test_axquery_ingest.py` does. The fixtures
are AUTHORED, not recorded — shaped from Elasticsearch's documented search API (`_search`
with `pit` + `search_after` + `sort`, `_shards`, `hits.hits[]._source`/`sort`, `_pit`,
`_resolve/index`, the root `GET /` version block) and ECS / Filebeat `zeek` module field
names — so a live cluster may differ in detail; the SPEC's live acceptance (a Filebeat-fed
index against the Zeek reader) is the check for that.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from aximprint.events import FlowEvent, IdentityEvent, SensorHealthEvent, ServiceEvent
from aximprint.ingest import get_plugin, list_plugins
from aximprint.ingest.siem import elastic as mod
from aximprint.ingest.siem.elastic import DEFAULT_INDEX, ElasticError
from aximprint.ingest.siem.http import ENV_API_KEY, ENV_PASSWORD, ENV_USER
from aximprint.ingest.siem.rows import ZEEK_CONN_STATE_CLASS

BASE = "elastic:https://127.0.0.1:9200"
T0 = 1790000000.0


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")


def _ts(doc: dict) -> float:
    return _dt.datetime.fromisoformat(doc["@timestamp"].replace("Z", "+00:00")).timestamp()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# -- authored documents ---------------------------------------------------------------------

def _fw(ts, event_type="allowed", bytes_in=200, bytes_out=100, **kw):
    """An ECS firewall session as the panw/cisco modules write it."""
    doc = {"@timestamp": _iso(ts), "event": {"category": ["network"], "type": [event_type],
                                              "dataset": "panw.panos", "kind": "event",
                                              "duration": 5_000_000_000},
           "source": {"ip": "10.0.0.1", "port": 51000, "bytes": bytes_out, "packets": 2},
           "destination": {"ip": "10.0.0.2", "port": 443, "bytes": bytes_in,
                           "packets": 3 if bytes_in else 0},
           "network": {"transport": "tcp", "protocol": "tls"}}
    for k, v in kw.items():
        doc[k] = v
    return doc


def _netflow(ts, **kw):
    """An unpaired NetFlow record: `network.bytes` only, no verdict."""
    doc = {"@timestamp": _iso(ts), "event": {"category": ["network_traffic", "network"],
                                              "dataset": "netflow.log", "type": ["connection"]},
           "source": {"ip": "10.0.0.5", "port": 40000},
           "destination": {"ip": "10.0.0.9", "port": 53},
           "network": {"transport": "udp", "bytes": 80, "packets": 1}}
    doc.update(kw)
    return doc


def _zeek_conn(ts, state="SF", **kw):
    """A `zeek.connection` document as Filebeat's zeek module writes it."""
    doc = {"@timestamp": _iso(ts),
           "event": {"category": ["network"], "dataset": "zeek.connection", "kind": "event",
                     "duration": 1_500_000_000},
           "source": {"ip": "10.0.0.1", "port": 51000, "bytes": 100, "packets": 2},
           "destination": {"ip": "10.0.0.2", "port": 443, "bytes": 200, "packets": 3},
           "network": {"transport": "tcp", "protocol": "ssl"},
           "zeek": {"session_id": "CAbc1",
                    "connection": {"state": state, "history": "ShADadFf", "local_orig": True,
                                   "local_resp": True}}}
    doc.update(kw)
    return doc


# -- the stubbed cluster --------------------------------------------------------------------

def _server(monkeypatch, docs=None, *, distribution=None, seen=None, fill=None, shards=None,
            resolved=None, ignore_range=False):
    """Route every urlopen through a fake Elasticsearch/OpenSearch.

    `docs` are the `_source` documents; `_search` honours the `@timestamp` range, `size` and
    `search_after` (sort values are `[epoch_ms, ordinal]`) over them, which is what makes
    the paging discipline observable. `fill(lo, hi)` overrides the population per range.
    """
    docs = docs or []
    seen = seen if seen is not None else []
    shards = shards or {"total": 1, "successful": 1, "skipped": 0, "failed": 0}
    resolved = resolved if resolved is not None else {
        "indices": [{"name": "filebeat-8.13.0"}], "aliases": [],
        "data_streams": [{"name": "logs-zeek.connection-default"}]}
    version = {"number": "8.13.0"}
    if distribution:
        version["distribution"] = distribution

    def search(body: dict) -> dict:
        rng = next(f for f in body["query"]["bool"]["filter"] if "range" in f)
        lo, hi = rng["range"]["@timestamp"]["gte"], rng["range"]["@timestamp"]["lt"]
        pool = fill(lo, hi) if fill is not None else docs
        rows = sorted(((_ts(d), i, d) for i, d in enumerate(pool)
                       if ignore_range or lo <= _ts(d) < hi), key=lambda x: (x[0], x[1]))
        after = body.get("search_after")
        if after is not None:
            rows = [r for r in rows if (int(r[0] * 1000), r[1]) > (after[0], after[1])]
        page = rows[:body["size"]]
        return {"_shards": shards, "hits": {"hits": [
            {"_index": "filebeat-8.13.0", "_id": str(i), "_source": d,
             "sort": [int(t * 1000), i]} for t, i, d in page]}}

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        parsed = urllib.parse.urlparse(req.full_url)
        path, method = parsed.path, req.get_method()
        body = json.loads(req.data) if req.data else None
        if path == "/" and method == "GET":
            return _Resp(json.dumps({"name": "es1", "cluster_name": "lab",
                                     "version": version}).encode())
        if path.startswith("/_resolve/index/"):
            return _Resp(json.dumps(resolved).encode())
        if path.endswith("/_search") and body and body.get("size") == 0 and "aggs" in body:
            net = [d for d in docs if "network" in (d.get("event") or {}).get("category", [])]
            first = min((_ts(d) for d in net), default=None)
            last = max((_ts(d) for d in net), default=None)
            return _Resp(json.dumps({
                "hits": {"total": {"value": len(net), "relation": "eq"}},
                "aggregations": {
                    "first": {"value": first * 1000 if first is not None else None},
                    "last": {"value": last * 1000 if last is not None else None}}}).encode())
        if path.endswith("/_pit") and method == "POST":
            return _Resp(json.dumps({"id": "pit-1"}).encode())
        if path.endswith("/_search/point_in_time") and method == "POST":
            return _Resp(json.dumps({"pit_id": "ospit-1"}).encode())
        if path == "/_pit" and method == "DELETE":
            return _Resp(json.dumps({"succeeded": True, "num_freed": 1}).encode())
        if path == "/_search/point_in_time" and method == "DELETE":
            return _Resp(json.dumps({"pits": [{"successful": True}]}).encode())
        if path == "/_search" and method == "POST":
            return _Resp(json.dumps(search(body)).encode())
        raise AssertionError(f"unexpected request {method} {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _searches(seen) -> list[dict]:
    return [json.loads(r.data) for r in seen
            if urllib.parse.urlparse(r.full_url).path == "/_search" and r.get_method() == "POST"]


def _small_pages(monkeypatch, page=100, ceiling=400):
    monkeypatch.setattr(mod, "PAGE_ROWS", page)
    monkeypatch.setattr(mod, "LEAF_ROWS_MAX", ceiling)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    for k in (ENV_USER, ENV_PASSWORD, ENV_API_KEY):
        monkeypatch.delenv(k, raising=False)


def _pull(source=BASE, lo=T0, hi=T0 + 3600):
    return list(get_plugin("elastic").events(source, lo, hi))


# -- listing ----------------------------------------------------------------------------------

def test_listed_as_an_http_plugin_with_the_closed_shared_schema():
    row = next(r for r in list_plugins() if r["name"] == "elastic")
    assert row["source_kind"] == "http"
    schema = row["config_schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["source"]
    assert "index" not in schema["properties"]


# -- locator ----------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["elastic:127.0.0.1:9200", "elastic:es.example", "elastic:",
                                 "https://x:9200", "splunk:https://x:9200"])
def test_locator_refuses_a_bare_host_and_anything_not_elastic(bad):
    with pytest.raises(ElasticError):
        get_plugin("elastic").locator(bad)


def test_locator_index_pattern_defaults_and_is_the_only_option():
    plugin = get_plugin("elastic")
    assert plugin._index(plugin.locator(BASE)) == DEFAULT_INDEX
    assert plugin.locator(BASE + "/?index=logs-zeek.*").options == {"index": "logs-zeek.*"}
    with pytest.raises(ElasticError, match="not one of"):
        plugin.locator(BASE + "?datamodel=x")
    with pytest.raises(ElasticError, match="empty"):
        plugin.locator(BASE + "?index=")


@pytest.mark.parametrize("bad", ["elastic:https://elastic:changeme@127.0.0.1:9200",
                                 "elastic:https://127.0.0.1:9200?api_key=abc",
                                 "elastic:https://127.0.0.1:9200?user=elastic"])
def test_a_credential_in_the_locator_is_refused_by_name(bad):
    with pytest.raises(ElasticError, match="credential"):
        get_plugin("elastic").locator(bad)


# -- credentials ------------------------------------------------------------------------------

def test_no_environment_credential_means_no_authorization_header(monkeypatch):
    seen = _server(monkeypatch, [_fw(T0 + 1)])
    _pull()
    assert seen and all(r.get_header("Authorization") is None for r in seen)


def test_user_and_password_become_basic_auth(monkeypatch):
    monkeypatch.setenv(ENV_USER, "ro")
    monkeypatch.setenv(ENV_PASSWORD, "s3cret")
    seen = _server(monkeypatch, [_fw(T0 + 1)])
    _pull()
    assert {r.get_header("Authorization") for r in seen} == {"Basic cm86czNjcmV0"}


def test_an_api_key_is_sent_under_the_apikey_scheme_not_bearer(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "aWQ6c2VjcmV0")
    seen = _server(monkeypatch, [_fw(T0 + 1)])
    _pull()
    assert {r.get_header("Authorization") for r in seen} == {"ApiKey aWQ6c2VjcmV0"}


def test_a_403_names_the_environment_settings(monkeypatch):
    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", refused)
    got = get_plugin("elastic").probe(BASE)
    assert got["reachable"] is False and "HTTP 403" in got["error"] and ENV_USER in got["error"]


# -- the query ---------------------------------------------------------------------------------

def test_the_search_is_a_timestamp_range_with_the_network_category_under_a_pit(monkeypatch):
    seen = _server(monkeypatch, [_fw(T0 + 1)])
    _pull(BASE + "?index=logs-zeek.*")
    pit_opens = [r for r in seen if urllib.parse.urlparse(r.full_url).path.endswith("/_pit")
                 and r.get_method() == "POST"]
    assert len(pit_opens) == 1 and "/logs-zeek.*/_pit" in pit_opens[0].full_url
    assert "keep_alive=" in pit_opens[0].full_url
    bodies = _searches(seen)
    assert bodies
    for b in bodies:
        assert b["pit"]["id"] == "pit-1"
        assert b["sort"] == [{"@timestamp": "asc"}, {"_shard_doc": "asc"}]
        assert "from" not in b, "never from+size"
        filters = b["query"]["bool"]["filter"]
        assert {"term": {"event.category": "network"}} in filters
        rng = next(f for f in filters if "range" in f)["range"]["@timestamp"]
        assert (rng["gte"], rng["lt"], rng["format"]) == (int(T0), int(T0 + 3600), "epoch_second")
    closes = [r for r in seen if r.get_method() == "DELETE"]
    assert len(closes) == 1 and json.loads(closes[0].data) == {"id": "pit-1"}


def test_the_pit_is_closed_even_when_the_pull_fails(monkeypatch):
    _small_pages(monkeypatch)
    seen = _server(monkeypatch, fill=lambda lo, hi: [_fw(lo) for _ in range(401)])
    with pytest.raises(ElasticError):
        _pull(hi=T0 + 2)
    assert [r for r in seen if r.get_method() == "DELETE"], "the PIT is released in finally"


def test_opensearch_spells_the_point_in_time_endpoint_its_own_way(monkeypatch):
    seen = _server(monkeypatch, [_fw(T0 + 1)], distribution="opensearch")
    _pull()
    paths = [(r.get_method(), urllib.parse.urlparse(r.full_url).path) for r in seen]
    assert ("POST", f"/{DEFAULT_INDEX}/_search/point_in_time") in paths
    assert ("DELETE", "/_search/point_in_time") in paths
    assert not any(p.endswith("/_pit") for _, p in paths)
    assert all(b["pit"]["id"] == "ospit-1" for b in _searches(seen))


def test_an_unbounded_call_resolves_its_bounds_from_the_span(monkeypatch):
    seen = _server(monkeypatch, [_fw(T0 + 1), _fw(T0 + 9)])
    flows = [e for e in get_plugin("elastic").events(BASE) if isinstance(e, FlowEvent)]
    assert len(flows) == 2
    rng = _searches(seen)[0]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
    assert (rng["gte"], rng["lt"]) == (int(T0 + 1), int(T0 + 10)), "last is inclusive; the slice is not"


# -- paging --------------------------------------------------------------------------------------

def test_a_full_page_splits_the_leaf_instead_of_trusting_it(monkeypatch):
    _small_pages(monkeypatch)

    def fill(lo, hi):
        return [_fw(lo + i % 10) for i in range(100)] if hi - lo > 50 else [_fw(lo + 1)]

    seen = _server(monkeypatch, fill=fill)
    flows = [e for e in _pull(hi=T0 + 100) if isinstance(e, FlowEvent)]
    ranges = [(b["query"]["bool"]["filter"][0]["range"]["@timestamp"]["gte"],
               b["query"]["bool"]["filter"][0]["range"]["@timestamp"]["lt"]) for b in _searches(seen)]
    assert ranges[0] == (int(T0), int(T0 + 100)) and len(ranges) > 1
    assert all(b["size"] <= 100 for b in _searches(seen))
    assert flows and all(T0 <= f.ts < T0 + 100 for f in flows)


def test_a_floor_width_leaf_widens_by_walking_search_after_under_the_pit(monkeypatch):
    """A one-second burst cannot be split finer; the leaf's limit doubles and the extra rows
    come through `search_after` in pages no larger than PAGE_ROWS — never a bigger `size`."""
    _small_pages(monkeypatch, page=100, ceiling=800)
    burst = [_fw(T0 + 1, source={"ip": "10.0.0.1", "port": 10000 + i, "bytes": 1, "packets": 1})
             for i in range(250)]
    seen = _server(monkeypatch, fill=lambda lo, hi: burst)
    flows = [e for e in _pull(hi=T0 + 4) if isinstance(e, FlowEvent)]
    assert len(flows) == 250, "the whole burst, once"
    bodies = _searches(seen)
    assert max(b["size"] for b in bodies) == 100, "size never exceeds the page"
    assert any("search_after" in b for b in bodies), "the wider leaf is walked, not re-sized"
    assert all("from" not in b for b in bodies)


def test_past_the_leaf_ceiling_the_leaf_is_refused_not_truncated(monkeypatch):
    _small_pages(monkeypatch)
    _server(monkeypatch, fill=lambda lo, hi: [_fw(lo) for _ in range(401)])
    with pytest.raises(ElasticError, match="will NOT help"):
        _pull(hi=T0 + 2)


def test_only_documents_whose_own_timestamp_falls_in_a_leaf_are_emitted_by_it(monkeypatch):
    """A server that lets a document outside the leaf through (a lax range, a clock skew)
    must not have it counted outside the slice."""
    _server(monkeypatch, [_fw(T0 + 5), _fw(T0 + 50), _fw(T0 + 500)], ignore_range=True)
    flows = [e for e in _pull(hi=T0 + 100) if isinstance(e, FlowEvent)]
    assert sorted(f.ts for f in flows) == [T0 + 5, T0 + 50]


# -- coverage ------------------------------------------------------------------------------------

def test_a_failed_shard_becomes_a_capture_loss_health_event(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1)], shards={"total": 4, "successful": 3, "skipped": 0,
                                                "failed": 1})
    events = _pull()
    h, = [e for e in events if isinstance(e, SensorHealthEvent)]
    assert h.kind == "capture_loss" and h.value == 25.0 and "3/4 shards" in h.detail
    assert h.ts <= next(e.ts for e in events if isinstance(e, FlowEvent))


def test_all_shards_answering_emits_no_health_event(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1)])
    assert not [e for e in _pull() if isinstance(e, SensorHealthEvent)]


# -- state_class per source family -----------------------------------------------------------------

def test_a_filebeat_zeek_connection_keeps_its_conn_state_and_takes_the_zeek_table(monkeypatch):
    _server(monkeypatch, [_zeek_conn(T0 + 1, "SF"), _zeek_conn(T0 + 2, "S0"),
                          _zeek_conn(T0 + 3, "REJ"), _zeek_conn(T0 + 4, "OTH")])
    flows = sorted((e for e in _pull() if isinstance(e, FlowEvent)), key=lambda f: f.ts)
    assert [(f.state, f.state_class) for f in flows] == [
        ("SF", "established"), ("S0", "attempted"), ("REJ", "refused"), ("OTH", "other")]
    assert all(f.state_class == ZEEK_CONN_STATE_CLASS[f.state] for f in flows)
    f = flows[0]
    assert (f.src_ip, f.src_port, f.dst_ip, f.dst_port) == ("10.0.0.1", 51000, "10.0.0.2", 443)
    assert f.proto == "tcp" and f.service == "ssl" and f.duration == 1.5
    assert (f.bytes_out, f.bytes_in, f.pkts_out, f.pkts_in) == (100, 200, 2, 3)
    assert f.local_src is True and f.local_dst is True and not f.counters_imputed


def test_a_plain_json_shipper_with_zeek_keys_at_the_top_level_reads_the_same(monkeypatch):
    doc = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "zeek.conn"},
           "ts": T0 + 1, "id.orig_h": "10.0.0.3", "id.orig_p": 4000, "id.resp_h": "10.0.0.4",
           "id.resp_p": 22, "proto": "tcp", "service": "ssh", "conn_state": "RSTO",
           "orig_bytes": 10, "resp_bytes": 20, "orig_pkts": 1, "resp_pkts": 1, "duration": 0.2}
    _server(monkeypatch, [doc])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class, f.dst_port, f.service) == ("RSTO", "reset_orig", 22, "ssh")


def test_the_verbatim_zeek_json_in_event_original_wins_over_the_extraction(monkeypatch):
    doc = _zeek_conn(T0 + 1, "SF")
    doc["event"]["original"] = json.dumps({"ts": T0 + 1, "id.orig_h": "10.0.0.1",
                                           "id.resp_h": "10.0.0.2", "conn_state": "S3"})
    _server(monkeypatch, [doc])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert f.state == "S3" and f.state_class == "established"


def test_firewall_documents_map_allowed_with_reply_to_established(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1, "allowed", bytes_in=200)])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("allowed", "established")
    assert f.bytes_in == 200 and not f.counters_imputed and f.duration == 5.0


def test_firewall_documents_map_allowed_with_no_reply_to_attempted(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1, "allowed", bytes_in=0)])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("allowed", "attempted")


def test_firewall_documents_map_denied_to_unknown(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1, "denied", bytes_in=0)])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("denied", "unknown")


def test_event_action_is_the_fallback_verdict_when_event_type_carries_none(monkeypatch):
    doc = _fw(T0 + 1, "connection", bytes_in=0)
    doc["event"]["action"] = "deny"
    _server(monkeypatch, [doc])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert (f.state, f.state_class) == ("deny", "unknown")


def test_unpaired_netflow_is_unknown_with_counters_imputed(monkeypatch):
    _server(monkeypatch, [_netflow(T0 + 1)])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert f.state_class == "unknown" and f.state == "" and f.counters_imputed is True
    assert (f.bytes_out, f.bytes_in, f.pkts_out) == (80, 0, 1) and f.proto == "udp"


def test_paired_netflow_keeps_observed_counters_and_stays_unknown(monkeypatch):
    doc = _netflow(T0 + 1, source={"ip": "10.0.0.5", "port": 40000, "bytes": 80, "packets": 1},
                   destination={"ip": "10.0.0.9", "port": 53, "bytes": 120, "packets": 1})
    _server(monkeypatch, [doc])
    f, = [e for e in _pull() if isinstance(e, FlowEvent)]
    assert f.state_class == "unknown" and f.counters_imputed is False and f.bytes_in == 120


def test_locality_and_vlan_are_none_for_every_non_zeek_document(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 1), _netflow(T0 + 2)])
    for f in (e for e in _pull() if isinstance(e, FlowEvent)):
        assert (f.local_src, f.local_dst, f.vlan_src, f.vlan_dst) == (None, None, None, None)


# -- identity and services ---------------------------------------------------------------------------

def test_ecs_dns_documents_bind_each_address_answer_and_drop_the_names(monkeypatch):
    doc = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "packetbeat.dns"},
           "source": {"ip": "10.0.0.1", "port": 5353}, "destination": {"ip": "10.0.0.53", "port": 53},
           "network": {"transport": "udp"},
           "dns": {"type": "answer", "question": {"name": "www.example.com"},
                   "answers": [{"name": "www.example.com", "type": "CNAME", "data": "edge.cdn.net", "ttl": 60},
                               {"name": "edge.cdn.net", "type": "A", "data": "93.184.216.34", "ttl": 300}],
                   "resolved_ip": ["93.184.216.34"]}}
    _server(monkeypatch, [doc])
    events = _pull()
    ids = [e for e in events if isinstance(e, IdentityEvent)]
    assert [(i.kind, i.ip, i.hostname, i.record_ttl) for i in ids] == [
        ("dns", "93.184.216.34", "www.example.com", 300.0)]
    assert not [e for e in events if isinstance(e, FlowEvent)], \
        "an app-layer record is not a second flow row"


def test_ecs_tls_documents_become_ssl_services_on_the_responder(monkeypatch):
    doc = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "suricata.eve"},
           "source": {"ip": "10.0.0.1"}, "destination": {"ip": "10.0.0.2", "port": 8443},
           "tls": {"version": "1.3", "client": {"server_name": "mail.corp", "ja3": "abc"},
                   "server": {"ja3s": "def", "subject": "CN=mail.corp", "issuer": "CN=Corp CA"}}}
    _server(monkeypatch, [doc])
    s, = [e for e in _pull() if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port) == ("ssl", "10.0.0.2", 8443)
    assert s.attrs == {"server_name": "mail.corp", "version": "1.3", "ja3": "abc", "ja3s": "def",
                       "subject": "CN=mail.corp", "issuer": "CN=Corp CA"}


def test_ecs_http_documents_become_http_services_on_the_responder(monkeypatch):
    doc = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network", "web"], "dataset": "suricata.eve"},
           "source": {"ip": "10.0.0.1"}, "destination": {"ip": "10.0.0.80", "port": 8080},
           "http": {"request": {"method": "GET"}, "response": {"status_code": 200}, "version": "1.1"},
           "url": {"domain": "intranet.corp", "path": "/index.html"},
           "user_agent": {"original": "curl/8"}}
    _server(monkeypatch, [doc])
    s, = [e for e in _pull() if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port) == ("http", "10.0.0.80", 8080)
    assert s.attrs == {"method": "GET", "host": "intranet.corp", "uri": "/index.html",
                       "user_agent": "curl/8", "status_code": "200", "version": "1.1"}


def test_filebeat_zeek_dns_ssl_and_dhcp_datasets_reach_the_zeek_parsers(monkeypatch):
    dns = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "zeek.dns"},
           "source": {"ip": "10.0.0.1"}, "destination": {"ip": "10.0.0.53", "port": 53},
           "zeek": {"dns": {"query": "dc1.corp", "answers": ["10.0.0.10"], "TTLs": [60.0]}}}
    ssl = {"@timestamp": _iso(T0 + 2), "event": {"category": ["network"], "dataset": "zeek.ssl"},
           "source": {"ip": "10.0.0.1"}, "destination": {"ip": "10.0.0.2", "port": 443},
           "zeek": {"ssl": {"server_name": "mail.corp", "version": "TLSv13"}},
           "tls": {"client": {"ja3": "abc"}}}
    dhcp = {"@timestamp": _iso(T0 + 3), "event": {"category": ["network"], "dataset": "zeek.dhcp"},
            "client": {"mac": "aa:bb:cc:dd:ee:ff"},
            "zeek": {"dhcp": {"address": {"assigned": "10.0.0.77", "requested": "10.0.0.77"},
                              "hostname": "laptop-7", "lease_time": 3600}}}
    _server(monkeypatch, [dns, ssl, dhcp])
    events = _pull()
    ids = [e for e in events if isinstance(e, IdentityEvent)]
    assert {(i.kind, i.ip, i.hostname) for i in ids} == {("dns", "10.0.0.10", "dc1.corp"),
                                                         ("dhcp", "10.0.0.77", "laptop-7")}
    assert next(i for i in ids if i.kind == "dhcp").mac == "aa:bb:cc:dd:ee:ff"
    s, = [e for e in events if isinstance(e, ServiceEvent)]
    assert (s.kind, s.ip, s.port) == ("ssl", "10.0.0.2", 443)
    assert s.attrs["server_name"] == "mail.corp" and s.attrs["ja3"] == "abc"
    assert not [e for e in events if isinstance(e, FlowEvent)]


def test_events_come_out_in_ts_order(monkeypatch):
    _server(monkeypatch, [_fw(T0 + 30), _zeek_conn(T0 + 10), _netflow(T0 + 20)])
    ts = [e.ts for e in _pull()]
    assert ts == sorted(ts) and len(ts) == 3


# -- declines ------------------------------------------------------------------------------------

def test_alert_documents_are_named_into_unparsed_stems_never_mapped(monkeypatch):
    notice = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "zeek.notice"},
              "zeek": {"notice": {"note": "Scan::Port_Scan", "msg": "scanned"}}}
    alert = {"@timestamp": _iso(T0 + 2), "event": {"category": ["network"], "kind": "alert",
                                                   "dataset": "suricata.eve"},
             "source": {"ip": "10.0.0.1"}, "destination": {"ip": "10.0.0.2"}}
    _server(monkeypatch, [notice, alert])
    plugin = get_plugin("elastic")
    assert list(plugin.events(BASE, T0, T0 + 3600)) == []
    assert {".alerts-security.alerts-*", ".siem-signals-*"} <= set(plugin.unparsed_stems)
    assert plugin.unparsed_stems["zeek.notice"] == 1
    assert plugin.unparsed_stems["suricata.eve:alert"] == 1
    assert plugin.unrecognized_stems == {}


def test_a_document_of_no_known_shape_is_counted_as_unrecognized(monkeypatch):
    doc = {"@timestamp": _iso(T0 + 1), "event": {"category": ["network"], "dataset": "custom.thing"},
           "message": "hello"}
    _server(monkeypatch, [doc])
    plugin = get_plugin("elastic")
    assert list(plugin.events(BASE, T0, T0 + 3600)) == []
    assert plugin.unrecognized_stems == {"custom.thing": 1}


# -- probe ------------------------------------------------------------------------------------------

def test_probe_reports_span_reachability_and_the_indices_the_pattern_names(monkeypatch):
    seen = _server(monkeypatch, [_fw(T0 + 1), _fw(T0 + 100)])
    got = get_plugin("elastic").probe(BASE + "?index=logs-zeek.*")
    assert got["supported"] and got["reachable"] and got["error"] is None
    assert (got["first_ts"], got["last_ts"]) == (T0 + 1, T0 + 100), "epoch millis -> seconds"
    assert got["extra"]["indexes"] == ["filebeat-8.13.0", "logs-zeek.connection-default"]
    assert got["extra"]["index"] == "logs-zeek.*" and got["extra"]["events"] == 2
    assert got["extra"]["version"] == "8.13.0" and got["extra"]["distribution"] == "elasticsearch"
    assert got["vantage"] is None
    assert not _searches(seen), "a probe pulls no event"
    assert any("/_resolve/index/logs-zeek.*" in r.full_url for r in seen)


def test_probe_unreachable_is_an_answer_not_a_crash(monkeypatch):
    def down(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    got = get_plugin("elastic").probe(BASE)
    assert got["supported"] and got["reachable"] is False and "unreachable" in got["error"]


def test_probe_refuses_a_bare_host_like_the_locator_does():
    got = get_plugin("elastic").probe("elastic:10.0.0.1:9200")
    assert got["reachable"] is False and "absolute http(s) base URL" in got["error"]


def test_probe_of_an_empty_index_has_no_span(monkeypatch):
    _server(monkeypatch, [])
    got = get_plugin("elastic").probe(BASE)
    assert got["reachable"] and got["first_ts"] is None and got["last_ts"] is None
    with pytest.raises(ElasticError, match="no data span"):
        list(get_plugin("elastic").events(BASE))
