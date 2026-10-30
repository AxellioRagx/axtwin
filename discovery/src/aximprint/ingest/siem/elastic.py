"""Elastic ingest plugin — connection-level rows out of an Elasticsearch index over its search API.

`ingest/siem/SPEC.md` is the design of record; `http.py` is the scaffolding shared with the
rest of the family and `rows.py` the row semantics. One plugin serves three platforms: an
Elasticsearch cluster, OpenSearch (the Wazuh indexer is one) and Graylog's store — the same
search API with ECS documents, differing only in the spelling of the point-in-time endpoint,
which `GET /` tells apart (`version.distribution`).

Locator: ``elastic:<absolute http(s) URL>[?index=<pattern>]``. `index` is the pattern the pull
and the probe both read, default ``logs-*,filebeat-*`` (Elastic's data-stream convention and
the Beats legacy indices); `probe` resolves it to the concrete indices and data streams under
`extra.indexes` so an operator sees what a run would touch.

Credentials: `AXIMPRINT_SOURCE_USER`/`_PASSWORD` -> HTTP Basic; `AXIMPRINT_SOURCE_API_KEY` ->
`Authorization: ApiKey <key>`. Never `argv`, never the locator (`http.py`).

One stream per pull: a bool filter of `@timestamp` in the leaf's `[lo, hi)` AND
`event.category: network`, sorted by `@timestamp` then `_shard_doc`, walked under ONE
point-in-time with `search_after` — never `from`+`size`, whose `index.max_result_window` cap
is exactly the silent truncation this family refuses. The leaf discipline is the family's:
a leaf whose rows reach its limit is split; at the floor the limit doubles and the leaf is
walked further by `search_after`. Each response's `_shards` is the server's own completeness
claim for the request and lands as a `capture_loss` health event when a shard failed.

Every hit is routed by its ECS shape:

    event.dataset zeek.<stem>      -> the Zeek reader's parser for the stem (`zeek.connection`
                                      is `conn`), fed a Zeek-shaped row rebuilt from the ECS
                                      tuple/counters, the `zeek.<stem>.*` leaves (Filebeat's
                                      renames mapped back: `zeek.connection.state` is
                                      `conn_state`) and, when present, the verbatim Zeek JSON in
                                      `event.original`/`message`, which wins
    dns.*                          -> IdentityEvent(dns) per ADDRESS answer
    tls.*                          -> ServiceEvent(ssl), responder-keyed
    http.* / url.* + http method   -> ServiceEvent(http), responder-keyed
    source.* + destination.*       -> FlowEvent (ECS network fields), only for a hit that is
                                      not an app-layer record above — a Packetbeat/Zeek source
                                      records the connection separately, and counting each
                                      DNS transaction as a flow too would double-count

`state_class` follows `rows.py`: Zeek rows keep `conn_state`; a hit whose `event.type` (or
`event.action`) says allowed/denied is a firewall row; everything else is the unpaired
NetFlow family (`unknown`, `counters_imputed` when `source.bytes`/`destination.bytes` are
missing and only `network.bytes` is there).

Declined by name (`_DECLINED`): the detection-alert indices (`.alerts-security.alerts-*`,
`.siem-signals-*`) and, when they arrive through the pattern, `zeek.notice`/`zeek.weird` and
any hit with `event.kind: alert` (Suricata's `suricata.eve` alerts) — counted onto
`unparsed_stems`, never mapped.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

from aximprint.events import Event, IdentityEvent, ServiceEvent
from aximprint.ingest.base import register
from aximprint.ingest.siem import http as _http
from aximprint.ingest.siem import rows as _rows
from aximprint.ingest.siem.http import HttpPullPlugin, HttpSourceError, Locator
from aximprint.ingest.siem.rows import as_float, as_int, as_str, first_of

__all__ = ["DEFAULT_INDEX", "ELASTIC_PREFIX", "PIT_KEEP_ALIVE", "ElasticError", "ElasticPlugin"]

ELASTIC_PREFIX = "elastic:"
DEFAULT_INDEX = "logs-*,filebeat-*"
PIT_KEEP_ALIVE = "5m"

#: Rows per search and the floor-leaf ceiling, this module's so a test can shrink them here.
#: `PAGE_ROWS` is also the most ONE search asks for (`size`): Elasticsearch's default
#: `index.max_result_window` is 10,000, so a wider floor leaf is walked by `search_after`
#: in pages of this size rather than asked for in one.
PAGE_ROWS = _http.PAGE_ROWS
LEAF_ROWS_MAX = _http.LEAF_ROWS_MAX

#: Filebeat's `zeek` module renames a few Zeek fields; mapped back so the Zeek reader's
#: parser sees the names it knows. Keyed by stem, `filebeat leaf -> zeek field`.
_FILEBEAT_RENAMES: dict[str, dict[str, str]] = {
    "conn": {"state": "conn_state"},
    "dhcp": {"address.assigned": "assigned_addr", "address.requested": "requested_addr",
             "hostname": "host_name", "lease_time": "lease_time"},
}

#: ECS -> Zeek synonyms every stem shares (the tuple and the counters), applied only where the
#: Zeek name is still missing after the `zeek.<stem>.*` leaves and the verbatim JSON.
_ECS_TO_ZEEK = {
    "id.orig_h": ("source.ip",), "id.orig_p": ("source.port",),
    "id.resp_h": ("destination.ip",), "id.resp_p": ("destination.port",),
    "proto": ("network.transport",), "service": ("network.protocol",),
    "orig_bytes": ("source.bytes",), "resp_bytes": ("destination.bytes",),
    "orig_pkts": ("source.packets",), "resp_pkts": ("destination.packets",),
    "uid": ("zeek.session_id",),
    # app-layer synonyms, harmless for stems that do not read them
    "server_name": ("tls.client.server_name",), "ja3": ("tls.client.ja3",),
    "ja3s": ("tls.server.ja3s",), "subject": ("tls.server.subject",),
    "issuer": ("tls.server.issuer",), "version": ("tls.version",),
    "method": ("http.request.method",), "host": ("url.domain",),
    "uri": ("url.original", "url.path"), "user_agent": ("user_agent.original",),
    "status_code": ("http.response.status_code",), "query": ("dns.question.name",),
    "mac": ("client.mac", "source.mac"),
}


class ElasticError(HttpSourceError):
    """Elasticsearch was unreachable, refused the pull, or answered with something unreadable."""


def _categories(flat: dict) -> set[str]:
    v = flat.get("event.category")
    if isinstance(v, str):
        return {v.lower()}
    if isinstance(v, (list, tuple)):
        return {str(x).lower() for x in v}
    return set()


def _listed(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return []


@register
class ElasticPlugin(HttpPullPlugin):
    """Adapt one Elasticsearch/OpenSearch endpoint to the normalized event model."""

    name = "elastic"
    source_help = ("an Elasticsearch (or OpenSearch) endpoint, addressed as "
                   "'elastic:<base-url>[?index=<pattern>]'")
    prefix = ELASTIC_PREFIX
    locator_options = ("index",)
    error = ElasticError
    example = "elastic:https://es.example:9200?index=logs-zeek.*"

    _DECLINED: dict[str, str] = {
        ".alerts-security.alerts-*": "Elastic Security's detection alerts — an alert table; "
                                     "`AlertEvent` wants a measured shape before a second "
                                     "producer is wired in",
        ".siem-signals-*": "the legacy detection-alert index, same reason",
    }

    def __init__(self) -> None:
        super().__init__()
        self._distribution: Optional[str] = None

    # -- locator ------------------------------------------------------------------

    def locator(self, source: str) -> Locator:
        loc = super().locator(source)
        if "index" in loc.options and not loc.options["index"]:
            raise ElasticError(f"elastic locator index= is empty; omit it for the default "
                               f"pattern ({DEFAULT_INDEX})")
        return loc

    @staticmethod
    def _index(loc: Locator) -> str:
        return loc.options.get("index", DEFAULT_INDEX)

    # -- transport ----------------------------------------------------------------

    def _request(self, base: str, path: str, body: Optional[dict] = None,
                 method: Optional[str] = None) -> urllib.request.Request:
        headers = {"Accept": "application/json", **_http.auth_headers(key_scheme="ApiKey")}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(f"{base}{path}", data=data, headers=headers,
                                      method=method or ("POST" if data is not None else "GET"))

    def _json(self, loc: Locator, path: str, body: Optional[dict] = None,
              method: Optional[str] = None, label: str = "elastic") -> Any:
        return _http.fetch_json(self._request(loc.base, path, body, method), label=label,
                                base=loc.base, error=ElasticError)

    def _root(self, loc: Locator) -> dict:
        info = self._json(loc, "/", label="elastic /")
        if not isinstance(info, dict):
            raise ElasticError(f"elastic / returned {type(info).__name__}, expected an object")
        version = info.get("version") or {}
        self._distribution = str(version.get("distribution") or "elasticsearch").lower()
        return info

    def _open_pit(self, loc: Locator) -> str:
        """One point-in-time over the index pattern; the OpenSearch spelling when `GET /`
        said so (chosen by the server's own answer, never by trying both)."""
        if self._distribution is None:
            self._root(loc)
        index = urllib.parse.quote(self._index(loc), safe="*,-_.")
        if self._distribution == "opensearch":
            blob = self._json(loc, f"/{index}/_search/point_in_time?keep_alive={PIT_KEEP_ALIVE}",
                              method="POST", label="elastic point_in_time")
            pit = blob.get("pit_id") if isinstance(blob, dict) else None
        else:
            blob = self._json(loc, f"/{index}/_pit?keep_alive={PIT_KEEP_ALIVE}",
                              method="POST", label="elastic _pit")
            pit = blob.get("id") if isinstance(blob, dict) else None
        if not pit:
            raise ElasticError(f"elastic opened no point-in-time over {self._index(loc)!r}: "
                               f"{str(blob)[:200]}")
        return str(pit)

    def _close_pit(self, loc: Locator, pit: str) -> None:
        try:
            if self._distribution == "opensearch":
                self._json(loc, "/_search/point_in_time", {"pit_id": [pit]}, method="DELETE",
                           label="elastic point_in_time close")
            else:
                self._json(loc, "/_pit", {"id": pit}, method="DELETE", label="elastic _pit close")
        except HttpSourceError:
            pass        # the PIT expires on its own; a failed close must not fail a finished pull

    @staticmethod
    def _query(lo: float, hi: float) -> dict:
        return {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": int(lo), "lt": int(hi), "format": "epoch_second"}}},
            {"term": {"event.category": "network"}},
        ]}}

    def _leaf(self, loc: Locator, pit: str, lo: float, hi: float, limit: int) -> list[dict]:
        """One leaf: `search_after` pages of at most `PAGE_ROWS` under the PIT until the leaf
        has `limit` rows (full -> the caller splits or widens) or a page comes back short."""
        rows: list[dict] = []
        after: Optional[list] = None
        while len(rows) < limit:
            size = min(int(PAGE_ROWS), limit - len(rows))
            body: dict = {
                "size": size, "query": self._query(lo, hi), "track_total_hits": False,
                "pit": {"id": pit, "keep_alive": PIT_KEEP_ALIVE},
                "sort": [{"@timestamp": "asc"}, {"_shard_doc": "asc"}],
            }
            if after is not None:
                body["search_after"] = after
            blob = self._json(loc, "/_search", body, label="elastic _search")
            if not isinstance(blob, dict):
                raise ElasticError(f"elastic _search returned {type(blob).__name__}")
            shards = blob.get("_shards") or {}
            total, failed = int(shards.get("total") or 0), int(shards.get("failed") or 0)
            if total and failed:
                self._health.append(_http.coverage_event(
                    float(lo), (total - failed) / total,
                    f"elastic _shards: {total - failed}/{total} shards answered, {failed} "
                    f"failed for [{int(lo)}, {int(hi)})"))
            hits = ((blob.get("hits") or {}).get("hits")) or []
            for h in hits:
                src = h.get("_source") if isinstance(h, dict) else None
                if isinstance(src, dict):
                    rows.append(src)
            if len(hits) < size:
                break
            after = hits[-1].get("sort")
            if after is None:
                raise ElasticError("elastic _search hit carries no sort values; cannot page "
                                   "with search_after")
        return rows

    # -- contract -----------------------------------------------------------------

    def _span_blob(self, loc: Locator) -> dict:
        index = urllib.parse.quote(self._index(loc), safe="*,-_.")
        body = {"size": 0, "track_total_hits": True,
                "query": {"term": {"event.category": "network"}},
                "aggs": {"first": {"min": {"field": "@timestamp"}},
                         "last": {"max": {"field": "@timestamp"}}}}
        blob = self._json(loc, f"/{index}/_search?ignore_unavailable=true", body,
                          label="elastic span")
        if not isinstance(blob, dict):
            raise ElasticError(f"elastic span returned {type(blob).__name__}")
        return blob

    @staticmethod
    def _agg_ts(blob: dict, name: str) -> Optional[float]:
        agg = (blob.get("aggregations") or {}).get(name) or {}
        value = agg.get("value")
        if value is None:
            return None
        return _rows.epoch(value, f"aggregations.{name}", ElasticError)

    def _span(self, loc: Locator) -> tuple[Optional[float], Optional[float]]:
        blob = self._span_blob(loc)
        return self._agg_ts(blob, "first"), self._agg_ts(blob, "last")

    def probe(self, source: str) -> dict:
        """`GET /` for reachability and the distribution, `_resolve/index` for what the pattern
        names, one aggregation for the span and the hit count. Never an event."""
        try:
            loc = self.locator(source)
            info = self._root(loc)
            index = urllib.parse.quote(self._index(loc), safe="*,-_.")
            resolved = self._json(loc, f"/_resolve/index/{index}", label="elastic _resolve")
            span = self._span_blob(loc)
        except HttpSourceError as exc:
            return {"supported": True, "reachable": False, "error": str(exc),
                    "first_ts": None, "last_ts": None, "vantage": None, "extra": {}}
        names: list[str] = []
        if isinstance(resolved, dict):
            for key in ("indices", "data_streams", "aliases"):
                for entry in resolved.get(key) or []:
                    if isinstance(entry, dict) and entry.get("name"):
                        names.append(str(entry["name"]))
        total = ((span.get("hits") or {}).get("total") or {})
        version = info.get("version") or {}
        return {
            "supported": True, "reachable": True, "error": None,
            "first_ts": self._agg_ts(span, "first"), "last_ts": self._agg_ts(span, "last"),
            "vantage": None,
            "extra": {
                "version": version.get("number"),
                "distribution": self._distribution,
                "cluster_name": info.get("cluster_name"),
                "index": self._index(loc),
                "indexes": sorted(names),
                "events": int(total.get("value") or 0) if isinstance(total, dict) else 0,
            },
        }

    def _streams(self, loc: Locator, lo: float, hi: float) -> list[Iterator[Event]]:
        return [self._stream(loc, lo, hi)]

    def _stream(self, loc: Locator, lo: float, hi: float) -> Iterator[Event]:
        pit = self._open_pit(loc)
        try:
            rows = _http.bisected_rows(
                lambda a, b, limit: self._leaf(loc, pit, a, b, limit), lo, hi, self._start,
                page_rows=PAGE_ROWS, leaf_rows_max=LEAF_ROWS_MAX, label="elastic network",
                error=ElasticError)
            yield from self._leaf_stream(rows, self._route)
        finally:
            self._close_pit(loc, pit)

    @staticmethod
    def _start(doc: dict) -> Optional[float]:
        """The leaf partition key: `@timestamp`, the field the range filter selects on."""
        t = doc.get("@timestamp")
        if t in (None, ""):
            return None
        try:
            return _rows.epoch(t, "@timestamp", ElasticError)
        except ElasticError:
            return None

    # -- row routing ----------------------------------------------------------------

    def _route(self, doc: dict) -> Iterator[Event]:
        flat = _rows.flatten(doc)
        ts = self._start(doc)
        if ts is None:
            return
        dataset = as_str(flat.get("event.dataset"))
        kinds = {k.lower() for k in _listed(flat.get("event.kind"))}
        if "alert" in kinds:
            self._count(self.unparsed_stems, f"{dataset or '(no dataset)'}:alert")
            return
        if dataset.startswith("zeek."):
            suffix = dataset[len("zeek."):]           # Filebeat's name: `connection` for conn
            stem = "conn" if suffix == "connection" else suffix
            if _rows.is_alert_stem(stem):
                self._count(self.unparsed_stems, dataset)
                return
            if stem not in _rows.ZEEK_STEMS:
                self._count(self.unrecognized_stems, dataset)
                return
            yield from _rows.zeek_events(stem, self._zeek_row(flat, suffix, stem, ts))
            return
        emitted = False
        if "dns.question.name" in flat:
            emitted = True
            yield from self._ecs_dns(flat, ts)
        if any(k.startswith("tls.") for k in flat):
            emitted = True
            yield from self._ecs_service(flat, ts, "ssl")
        if any(k.startswith("http.") for k in flat):
            emitted = True
            yield from self._ecs_service(flat, ts, "http")
        if emitted:
            return
        if flat.get("source.ip") and flat.get("destination.ip"):
            yield from self._ecs_flow(flat, ts)
            return
        self._count(self.unrecognized_stems, dataset or "(no dataset)")

    @staticmethod
    def _zeek_row(flat: dict, suffix: str, stem: str, ts: float) -> dict:
        """Rebuild the Zeek row the reader's parser expects (module docstring). `suffix` is the
        dataset's own spelling (`zeek.connection.*` leaves), `stem` the Zeek reader's."""
        row: dict[str, Any] = {}
        prefix = f"zeek.{suffix}."
        renames = _FILEBEAT_RENAMES.get(stem, {})
        for k, v in flat.items():
            if k.startswith(prefix):
                leaf = k[len(prefix):]
                row[renames.get(leaf, leaf)] = v
        for zeek_key, ecs_keys in _ECS_TO_ZEEK.items():
            if zeek_key not in row:
                v = first_of(flat, *ecs_keys)
                if v is not None:
                    row[zeek_key] = v
        if "duration" not in row:
            ns = as_float(flat.get("event.duration"))
            if ns is not None:
                row["duration"] = ns / 1e9
        if "ts" not in row:
            row["ts"] = ts
        # A plain JSON shipper keeps Zeek's own keys at the top level — they ARE the row.
        for k, v in flat.items():
            if k.startswith("id.") or k in ("conn_state", "history", "ts", "uid", "proto",
                                            "service", "duration", "orig_bytes", "resp_bytes",
                                            "orig_pkts", "resp_pkts", "local_orig",
                                            "local_resp", "query", "answers", "TTLs"):
                row[k] = v
        raw = _rows.parse_raw_json(first_of(flat, "event.original", "message"))
        if raw and ("id.orig_h" in raw or "ts" in raw):
            row.update(_rows.flatten(raw))
        if isinstance(row.get("ts"), str):
            row["ts"] = _rows.epoch(row["ts"], "zeek.ts", ElasticError)
        return row

    @staticmethod
    def _ecs_dns(flat: dict, ts: float) -> Iterator[Event]:
        name = as_str(flat.get("dns.question.name"))
        if not name:
            return
        answers = flat.get("dns.answers")
        addrs: list[tuple[str, float]] = []
        if isinstance(answers, list):
            for a in answers:
                if isinstance(a, dict):
                    addrs.append((as_str(a.get("data")), as_float(a.get("ttl")) or 0.0))
                else:
                    addrs.append((as_str(a), 0.0))
        for ip in _listed(flat.get("dns.resolved_ip")):
            if ip not in {a for a, _ in addrs}:
                addrs.append((ip, 0.0))
        for ip, ttl in addrs:
            if _rows.is_ip(ip):
                yield IdentityEvent(ts=ts, kind="dns", ip=ip, hostname=name, record_ttl=ttl)

    @staticmethod
    def _ecs_service(flat: dict, ts: float, kind: str) -> Iterator[Event]:
        ip = as_str(flat.get("destination.ip"))
        if not ip:
            return
        if kind == "ssl":
            attrs = {
                "server_name": as_str(flat.get("tls.client.server_name")),
                "version": as_str(first_of(flat, "tls.version", "tls.version_protocol")),
                "ja3": as_str(flat.get("tls.client.ja3")),
                "ja3s": as_str(flat.get("tls.server.ja3s")),
                "subject": as_str(flat.get("tls.server.subject")),
                "issuer": as_str(flat.get("tls.server.issuer")),
            }
            default_port = 443
        else:
            attrs = {
                "method": as_str(flat.get("http.request.method")),
                "host": as_str(first_of(flat, "url.domain", "destination.domain")),
                "uri": as_str(first_of(flat, "url.path", "url.original")),
                "user_agent": as_str(flat.get("user_agent.original")),
                "status_code": as_str(flat.get("http.response.status_code")),
                "version": as_str(flat.get("http.version")),
            }
            default_port = 80
        yield ServiceEvent(ts=ts, kind=kind, ip=ip,
                           port=as_int(flat.get("destination.port")) or default_port,
                           attrs={k: v for k, v in attrs.items() if v})

    @staticmethod
    def _ecs_flow(flat: dict, ts: float) -> Iterator[Event]:
        bytes_in, bytes_out = as_int(flat.get("destination.bytes")), as_int(flat.get("source.bytes"))
        pkts_in, pkts_out = as_int(flat.get("destination.packets")), as_int(flat.get("source.packets"))
        b_out, b_in, p_out, p_in, imputed = _rows.netflow_counters(
            bytes_in, bytes_out, pkts_in, pkts_out,
            as_int(flat.get("network.bytes")), as_int(flat.get("network.packets")))
        verdicts = [t.lower() for t in _listed(flat.get("event.type"))]
        action = next((t for t in verdicts if t in _rows.ALLOWED_ACTIONS | _rows.BLOCKED_ACTIONS),
                      "")
        if not action:
            candidate = as_str(flat.get("event.action")).lower()
            if candidate in _rows.ALLOWED_ACTIONS | _rows.BLOCKED_ACTIONS:
                action = candidate
        if action:
            state, state_class = _rows.firewall_state(action, bytes_in=b_in, pkts_in=p_in)
        else:
            state, state_class = "", "unknown"          # unpaired NetFlow/IPFIX
        ns = as_float(flat.get("event.duration"))
        yield _rows.flow_from(
            ts=ts, src_ip=as_str(flat.get("source.ip")), dst_ip=as_str(flat.get("destination.ip")),
            src_port=as_int(flat.get("source.port")) or 0,
            dst_port=as_int(flat.get("destination.port")) or 0,
            proto=as_str(flat.get("network.transport")),
            service=as_str(flat.get("network.protocol")),
            duration=(ns / 1e9) if ns is not None else 0.0,
            state=state, state_class=state_class, bytes_out=b_out, bytes_in=b_in,
            pkts_out=p_out, pkts_in=p_in, counters_imputed=imputed)
