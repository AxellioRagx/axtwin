"""Splunk ingest plugin — connection-level rows out of a Splunk index over its REST API.

`ingest/siem/SPEC.md` is the design of record; `http.py` is the scaffolding shared with the
rest of the family (locator, env-only credentials, time-bisected leaves, health side channel)
and `rows.py` the row semantics (Zeek routing, firewall/NetFlow `state_class`).

Locator: ``splunk:<absolute http(s) URL>[?index=<name>&datamodel=Network_Traffic]``. The URL
is the management port (`https://splunk:8089`), never the web UI. `index` defaults to `*`
(every non-internal index, which is also what `probe` reports) so the pull and the probe read
the same thing; `datamodel` turns on the AGGREGATE fallback (below).

Credentials: `AXIMPRINT_SOURCE_USER`/`_PASSWORD` -> HTTP Basic (Splunk's REST API takes it
natively); `AXIMPRINT_SOURCE_API_KEY` -> `Authorization: Bearer <token>` (a Splunk
authentication token). Never `argv`, never the locator (`http.py`).

Five streams per pull, each time-bisected on its own and k-way merged by `ts`, every one an
`search/v2/jobs/export` call with `output_mode=json`, which STREAMS results without the
result cap a search job carries. The leaf discipline still bisects by time: each leaf's SPL
carries `earliest`/`latest` and a `| head <limit>` so a dense leaf comes back full and is
split, exactly as under every other vendor's cap.

    zeek   (sourcetype=corelight_* | zeek* | bro*)   -> the Zeek reader's parser per stem
    flow   tag=network tag=communicate, NOT zeek      -> FlowEvent, CIM Network_Traffic fields
    dns    tag=network tag=resolution tag=dns, NOT zeek -> IdentityEvent(dns), CIM Network_Resolution
    web    tag=web, NOT zeek                          -> ServiceEvent(http), CIM Web
    certs  tag=certificate, NOT zeek                  -> ServiceEvent(ssl), CIM Certificates

**Zeek-fed rows** (the Corelight add-on's `corelight_<stem>` sourcetypes, Splunk's Zeek add-on's
`zeek:<stem>:json` / `bro:<stem>:json`) are recognised by SOURCETYPE in every stream and routed
through the Zeek reader's parser for the stem, so a `conn` row keeps its `conn_state` and takes
its `state_class` from the Zeek table — a Corelight-fed Splunk yields the Zeek reader's fidelity.
The row the parser sees is the sensor's verbatim JSON (`_raw`) overlaid on the extracted fields.
Zeek `notice`/`weird` are alert tables and are COUNTED into `unparsed_stems`, never mapped.

**The aggregate fallback** (`datamodel=Network_Traffic`): the flow stream becomes a `tstats`
over the accelerated data model, grouped by second and 5-tuple with summed counters. It is
explicit (the operator asked), labelled (every flow carries `counters_imputed`, `state` says
`tstats`), and emits one `SensorHealthEvent(kind="aggregate_fallback")` per leaf naming the
features it silences: inter-arrival cadence, per-flow size distribution and per-flow duration.
In that mode a Zeek `conn` row is counted onto `unparsed_stems`, not emitted — the tstats
aggregate already counted it, and a second flow row would double-count.

Declined by name (`_DECLINED`): the CIM `Alerts` and `Intrusion_Detection` data models and
Enterprise Security's `notable` and `risk` indexes. `AlertEvent` has no second producer yet.

No coverage statement reaches a health event from this vendor: the export stream carries
search `messages`, and an `ERROR`/`FATAL` one fails the leaf loud, but Splunk makes no
per-request completeness claim of the kind axquery's `coverage` block or Elasticsearch's
`_shards` make, and inventing one would be the quiet green this project refuses.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from aximprint.events import Event, IdentityEvent, SensorHealthEvent, ServiceEvent
from aximprint.ingest.base import register
from aximprint.ingest.reorder import is_ip
from aximprint.ingest.siem import http as _http
from aximprint.ingest.siem import rows as _rows
from aximprint.ingest.siem.http import HttpPullPlugin, HttpSourceError, Locator
from aximprint.ingest.siem.rows import as_float, as_int, as_str, first_of

__all__ = ["EXPORT_PATH", "INFO_PATH", "SPLUNK_PREFIX", "SplunkError", "SplunkPlugin",
           "zeek_stem_of"]

SPLUNK_PREFIX = "splunk:"
EXPORT_PATH = "/services/search/v2/jobs/export"
INFO_PATH = "/services/server/info"

#: The one data model the aggregate fallback reads. Closed: `tstats` over any other model would
#: need its own field table, and a value nobody has measured is refused, not guessed at.
AGGREGATE_DATAMODELS = ("Network_Traffic",)

#: Rows per leaf and the floor-leaf ceiling, this module's so a test can shrink them here.
PAGE_ROWS = _http.PAGE_ROWS
LEAF_ROWS_MAX = _http.LEAF_ROWS_MAX

#: Sourcetypes that carry a Zeek log: the Corelight add-on (`corelight_conn`,
#: `corelight:conn`), Splunk's Zeek/Bro add-ons (`zeek:conn:json`, `bro:dns:json`,
#: `zeek_conn`). The captured group is the stem.
_ZEEK_SOURCETYPE = re.compile(r"^(?:corelight|zeek|bro)[_:]([a-z0-9_]+?)(?::json)?$", re.I)
_INDEX_OK = re.compile(r"^[A-Za-z0-9_*\-]+$")     # spliced into SPL: nothing else is admitted
_ZEEK_SOURCETYPE_SPL = ("(sourcetype=corelight_* OR sourcetype=corelight:* OR "
                        "sourcetype=zeek* OR sourcetype=bro*)")

#: Splunk's own per-event metadata, never a Zeek field; dropped before a row reaches a parser.
_SPLUNK_META = frozenset({
    "_time", "_raw", "_indextime", "_cd", "_bkt", "_serial", "_si", "_sourcetype",
    "_subsecond", "_kv", "sourcetype", "source", "host", "index", "linecount",
    "splunk_server", "splunk_server_group", "punct", "eventtype", "tag", "timestamp",
    "date_hour", "date_mday", "date_minute", "date_month", "date_second", "date_wday",
    "date_year", "date_zone",
})


class SplunkError(HttpSourceError):
    """Splunk was unreachable, refused the pull, or answered with something unreadable."""


def zeek_stem_of(sourcetype: str) -> Optional[str]:
    """The Zeek stem a sourcetype carries, or `None` for a non-Zeek sourcetype. Corelight's
    `conn_long` and `conn_red` are the conn log under other names and read as `conn`."""
    m = _ZEEK_SOURCETYPE.match(sourcetype.strip())
    if not m:
        return None
    stem = m.group(1).lower()
    if stem in ("connection", "conn_long", "conn_red"):
        return "conn"
    return stem


def _zeek_row(result: dict, ts: float) -> dict:
    """The row the Zeek parser sees: the sensor's verbatim JSON (`_raw`) winning over the
    extracted fields, with `ts` from `_time` when the raw event carries none (an ISO-8601
    `ts` in the raw JSON is converted by `rows.zeek_events`)."""
    row = {k: v for k, v in result.items() if k not in _SPLUNK_META and not k.startswith("_")}
    raw = _rows.parse_raw_json(result.get("_raw"))
    if raw:
        row.update(raw)
    if "ts" not in row or row["ts"] in (None, "", "-"):
        row["ts"] = ts
    return row


@register
class SplunkPlugin(HttpPullPlugin):
    """Adapt one Splunk search head to the normalized event model."""

    name = "splunk"
    source_help = ("a Splunk REST endpoint, addressed as "
                   "'splunk:<base-url>[?index=<name>&datamodel=Network_Traffic]'")
    prefix = SPLUNK_PREFIX
    locator_options = ("index", "datamodel")
    error = SplunkError
    example = "splunk:https://splunk.example:8089?index=netflow"

    _DECLINED: dict[str, str] = {
        "Alerts": "the CIM Alerts data model — an alert table; `AlertEvent` wants a measured "
                  "shape before a second producer is wired in",
        "Intrusion_Detection": "the CIM IDS data model — an alert table, same reason",
        "notable": "Enterprise Security's notable-event index — incidents, not traffic",
        "risk": "Enterprise Security's risk index — scores derived from alerts, not traffic",
    }

    # -- locator ------------------------------------------------------------------

    empty_index_hint = "omit it to read every non-internal index (index=*)"

    def locator(self, source: str) -> Locator:
        loc = super().locator(source)
        dm = loc.options.get("datamodel")
        if dm is not None and dm not in AGGREGATE_DATAMODELS:
            raise SplunkError(f"splunk locator datamodel={dm!r} is not one of "
                              f"{list(AGGREGATE_DATAMODELS)} — the aggregate fallback reads "
                              f"only the data model whose fields this build maps")
        idx = loc.options.get("index")
        if idx is not None and not _INDEX_OK.match(idx):
            raise SplunkError(f"splunk locator index={idx!r} is not an index name or wildcard "
                              f"(letters, digits, '_', '-', '*') — it is spliced into the search")
        return loc

    @staticmethod
    def _index(loc: Locator) -> str:
        return loc.options.get("index", "*")

    @staticmethod
    def _aggregate(loc: Locator) -> bool:
        return "datamodel" in loc.options

    # -- transport ----------------------------------------------------------------

    def _request(self, base: str, path: str, data: Optional[dict] = None) -> urllib.request.Request:
        if data is None:
            return _http.request(f"{base}{path}?output_mode=json", key_scheme="Bearer")
        return _http.request(f"{base}{path}", key_scheme="Bearer",
                             data=urllib.parse.urlencode(data).encode("utf-8"),
                             content_type="application/x-www-form-urlencoded")

    def _export(self, base: str, spl: str,
                warnings: Optional[list[str]] = None) -> list[dict]:
        """One `search/v2/jobs/export` call: the result rows of a streamed search.

        The body is newline-delimited JSON, one object per result (`{"preview": false,
        "offset": n, "result": {...}, "lastrow": true}`); a `preview` object is skipped
        (never final), a `messages` object with an `ERROR`/`FATAL` entry fails the leaf by
        name — a search that could not run must not read as an empty window. A `WARN`
        message (an unreachable search peer, results that may be incomplete) does not fail the
        leaf: its text is appended to `warnings` for the caller to turn into a health event
        once the leaf is consumed.
        """
        req = self._request(base, EXPORT_PATH,
                            {"search": spl, "output_mode": "json", "preview": "false"})
        try:
            body = _http.fetch(req, label="splunk export", base=base, error=SplunkError)
        except SplunkError as exc:
            if "HTTP 404" in str(exc):
                raise SplunkError(f"{exc} -- search/v2/jobs/export needs Splunk 9.0.1 or "
                                  f"later") from exc
            raise
        rows: list[dict] = []
        for line in body.splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SplunkError(f"splunk export returned a non-JSON line: {line[:200]!r}") from exc
            if not isinstance(obj, dict):
                raise SplunkError(f"splunk export returned {type(obj).__name__}, expected objects")
            for msg in obj.get("messages") or []:
                kind = str(msg.get("type", "")).upper() if isinstance(msg, dict) else ""
                if kind in ("ERROR", "FATAL"):
                    raise SplunkError(f"splunk search failed: {msg.get('text', msg)} :: {spl}")
                if kind == "WARN" and warnings is not None:
                    warnings.append(str(msg.get("text", msg)))
            if obj.get("preview"):
                continue
            result = obj.get("result")
            if isinstance(result, dict):
                rows.append(result)
        return rows

    # -- SPL ----------------------------------------------------------------------

    def _search_spl(self, loc: Locator, clause: str, lo: float, hi: float, limit: int) -> str:
        a, b = _http.second_bounds(lo, hi)
        return (f"search index={self._index(loc)} {clause} earliest={a} "
                f"latest={b} | head {int(limit)}")

    def _tstats_spl(self, loc: Locator, lo: float, hi: float, limit: int) -> str:
        """The aggregate fallback: one row per (second, 5-tuple) with summed counters."""
        dm = loc.options["datamodel"]
        a, b = _http.second_bounds(lo, hi)
        return (f"| tstats count sum(All_Traffic.bytes_in) as bytes_in "
                f"sum(All_Traffic.bytes_out) as bytes_out "
                f"sum(All_Traffic.packets_in) as packets_in "
                f"sum(All_Traffic.packets_out) as packets_out "
                f"max(All_Traffic.duration) as duration values(All_Traffic.action) as action "
                f"values(All_Traffic.app) as app "
                f"from datamodel={dm}.All_Traffic "
                f"where index={self._index(loc)} earliest={a} latest={b} "
                f"by _time span=1s All_Traffic.src All_Traffic.src_port All_Traffic.dest "
                f"All_Traffic.dest_port All_Traffic.transport "
                f"| rename All_Traffic.* as * | head {int(limit)}")

    _STREAM_CLAUSES = {
        "zeek": _ZEEK_SOURCETYPE_SPL,
        "flow": f"tag=network tag=communicate NOT {_ZEEK_SOURCETYPE_SPL}",
        "dns": f"tag=network tag=resolution tag=dns NOT {_ZEEK_SOURCETYPE_SPL}",
        "web": f"tag=web NOT {_ZEEK_SOURCETYPE_SPL}",
        "certs": f"tag=certificate NOT {_ZEEK_SOURCETYPE_SPL}",
    }

    # -- contract -----------------------------------------------------------------

    def _span_rows(self, loc: Locator) -> list[dict]:
        spl = (f"| tstats min(_time) as first_ts max(_time) as last_ts count as events "
               f"where index={self._index(loc)} by index")
        return self._export(loc.base, spl)

    def _span(self, loc: Locator) -> tuple[Optional[float], Optional[float]]:
        firsts, lasts = [], []
        for r in self._span_rows(loc):
            if r.get("first_ts") not in (None, ""):
                firsts.append(_rows.epoch(r["first_ts"], "tstats.first_ts", SplunkError))
            if r.get("last_ts") not in (None, ""):
                lasts.append(_rows.epoch(r["last_ts"], "tstats.last_ts", SplunkError))
        return (min(firsts) if firsts else None, max(lasts) if lasts else None)

    @staticmethod
    def _span_index_rows(span_rows: list[dict]) -> list[dict]:
        """Per-index span rows with their timestamps normalized; a malformed one raises
        `SplunkError` (the probe answers it, it does not crash)."""
        indexes = []
        for r in span_rows:
            indexes.append({
                "index": as_str(r.get("index")),
                "first_ts": (_rows.epoch(r["first_ts"], "tstats.first_ts", SplunkError)
                             if r.get("first_ts") not in (None, "") else None),
                "last_ts": (_rows.epoch(r["last_ts"], "tstats.last_ts", SplunkError)
                            if r.get("last_ts") not in (None, "") else None),
                "events": as_int(r.get("events")) or 0,
            })
        return indexes

    def probe(self, source: str) -> dict:
        """`server/info` for reachability, one `tstats` for the span and the indexes the pull
        will read (under `extra.indexes`, each with its own span and event count). Never an
        event.

        The span is ALL events per index (`tstats ... by index`); Elastic's is filtered to
        `event.category: network`. Splunk has no portable "network" predicate over raw events
        (CIM tags are search-time), so the two spans differ by design (SPEC.md Notes)."""
        try:
            loc = self.locator(source)
            info = _http.fetch_json(self._request(loc.base, INFO_PATH), label="splunk server/info",
                                    base=loc.base, error=SplunkError)
            span_rows = self._span_rows(loc)
            indexes = self._span_index_rows(span_rows)
        except HttpSourceError as exc:
            return self._unreachable(exc)
        content = {}
        if isinstance(info, dict):
            entries = info.get("entry") or []
            if entries and isinstance(entries[0], dict):
                content = entries[0].get("content") or {}
        firsts = [i["first_ts"] for i in indexes if i["first_ts"] is not None]
        lasts = [i["last_ts"] for i in indexes if i["last_ts"] is not None]
        return {
            "supported": True, "reachable": True, "error": None,
            "first_ts": min(firsts) if firsts else None,
            "last_ts": max(lasts) if lasts else None,
            "vantage": None,
            "extra": {
                "version": content.get("version"),
                "server_name": content.get("serverName"),
                "index": self._index(loc),
                "indexes": indexes,
                "events": sum(i["events"] for i in indexes),
                "mode": "aggregate" if self._aggregate(loc) else "raw",
                "datamodel": loc.options.get("datamodel"),
            },
        }

    def _streams(self, loc: Locator, lo: float, hi: float) -> list[Iterator[Event]]:
        streams = [self._stream(loc, "zeek", lo, hi)]
        if self._aggregate(loc):
            streams.append(self._aggregate_stream(loc, lo, hi))
        else:
            streams.append(self._stream(loc, "flow", lo, hi))
        for kind in ("dns", "web", "certs"):
            streams.append(self._stream(loc, kind, lo, hi))
        return streams

    def _stream(self, loc: Locator, kind: str, lo: float, hi: float) -> Iterator[Event]:
        clause = self._STREAM_CLAUSES[kind]

        def leaf(a: float, b: float, limit: int) -> list[dict]:
            warnings: list[str] = []
            rows = self._export(loc.base, self._search_spl(loc, clause, a, b, limit), warnings)
            if len(rows) < limit:       # only a consumed leaf speaks; a full page is re-read
                self._warn(kind, a, warnings)
            return rows

        rows = _http.bisected_rows(leaf, lo, hi, self._start, page_rows=PAGE_ROWS,
                                   leaf_rows_max=LEAF_ROWS_MAX, label=f"splunk {kind}",
                                   error=SplunkError)
        return self._leaf_stream(rows, lambda r: self._route(loc, kind, r))

    def _warn(self, label: str, ts: float, warnings: list[str]) -> None:
        """A `WARN` from a consumed leaf is the server saying its answer may be partial."""
        for text in dict.fromkeys(warnings):
            self._health.append(SensorHealthEvent(
                ts=float(ts), kind="partial_results", value=1.0,
                detail=f"splunk {label} search warned: {text}"))

    def _aggregate_stream(self, loc: Locator, lo: float, hi: float) -> Iterator[Event]:
        dm = loc.options["datamodel"]

        def leaf(a: float, b: float, limit: int) -> list[dict]:
            warnings: list[str] = []
            rows = self._export(loc.base, self._tstats_spl(loc, a, b, limit), warnings)
            if len(rows) < limit:
                self._warn("tstats", a, warnings)
            if rows and len(rows) < limit:
                # A short page is the one that gets consumed: label it, once per leaf.
                self._health.append(SensorHealthEvent(
                    ts=float(a), kind="aggregate_fallback",
                    value=float(sum(as_int(r.get("count")) or 0 for r in rows)),
                    detail=f"splunk tstats over datamodel={dm}: server-side aggregate by "
                           f"1s/5-tuple ({len(rows)} groups); `count` is a FLOW count — one "
                           f"FlowEvent per (second, 5-tuple) group, so connection counts are "
                           f"understated; a multi-valued action reads as blocked if any is; silences inter-arrival cadence, "
                           f"per-flow size distribution and per-flow duration; every flow "
                           f"carries counters_imputed"))
            return rows

        rows = _http.bisected_rows(leaf, lo, hi, self._start, page_rows=PAGE_ROWS,
                                   leaf_rows_max=LEAF_ROWS_MAX, label="splunk tstats",
                                   error=SplunkError)
        return self._leaf_stream(rows, lambda r: self._cim_flow(r, aggregate=True))

    # The leaf partition key: the row's `_time` — the field `earliest`/`latest` select on.
    _start = staticmethod(_rows.start_from("_time", SplunkError))

    # -- row routing ----------------------------------------------------------------

    def _route(self, loc: Locator, kind: str, r: dict) -> Iterator[Event]:
        sourcetype = as_str(r.get("sourcetype"))
        stem = zeek_stem_of(sourcetype) if sourcetype else None
        if stem is not None:
            if _rows.is_alert_stem(stem):
                self._count(self.unparsed_stems, sourcetype)
                return
            if stem == "conn" and self._aggregate(loc):
                self._count(self.unparsed_stems, f"{sourcetype} (counted by tstats aggregate)")
                return
            if stem not in _rows.ZEEK_STEMS:
                self._count(self.unrecognized_stems, sourcetype)
                return
            ts = self._start(r)
            if ts is None:
                return
            yield from _rows.zeek_events(stem, _zeek_row(r, ts), ts)
            return
        if kind == "flow":
            yield from self._cim_flow(r)
        elif kind == "dns":
            yield from self._cim_dns(r)
        elif kind == "web":
            yield from self._cim_web(r)
        elif kind == "certs":
            yield from self._cim_cert(r)
        else:
            self._count(self.unrecognized_stems, sourcetype or "(no sourcetype)")

    def _cim_flow(self, r: dict, aggregate: bool = False) -> Iterator[Event]:
        src = as_str(first_of(r, "src_ip", "src"))
        dst = as_str(first_of(r, "dest_ip", "dest"))
        ts = self._start(r)
        if not (src and dst) or ts is None:
            return
        if not (is_ip(src) and is_ip(dst)):
            # CIM `src`/`dest` may be hostnames; a flow keyed on a name binds nothing.
            self._count(self.unrecognized_stems, "(flow src/dest not an IP)")
            return
        bytes_in, bytes_out = as_int(r.get("bytes_in")), as_int(r.get("bytes_out"))
        pkts_in, pkts_out = as_int(r.get("packets_in")), as_int(r.get("packets_out"))
        b_out, b_in, p_out, p_in, imputed = _rows.netflow_counters(
            bytes_in, bytes_out, pkts_in, pkts_out, as_int(r.get("bytes")), as_int(r.get("packets")))
        state, state_class = _rows.flow_state(r.get("action"), b_in, p_in)
        if aggregate:
            state = f"tstats/{state}" if state else "tstats"
            imputed = True
        yield _rows.flow_from(
            ts=ts, src_ip=src, dst_ip=dst,
            src_port=as_int(r.get("src_port")) or 0, dst_port=as_int(r.get("dest_port")) or 0,
            proto=as_str(r.get("transport")), service=as_str(r.get("app")),
            duration=as_float(r.get("duration")) or 0.0,
            state=state, state_class=state_class, bytes_out=b_out, bytes_in=b_in,
            pkts_out=p_out, pkts_in=p_in, counters_imputed=imputed)

    def _cim_dns(self, r: dict) -> Iterator[Event]:
        """CIM Network_Resolution: `query` <-> each ADDRESS in `answer` (multivalue), on a
        response. CNAME/MX/TXT answers are names and bind nothing the resolver keys on."""
        ts = self._start(r)
        name = as_str(r.get("query"))
        if ts is None or not name:
            return
        mtype = as_str(r.get("message_type")).lower()
        if mtype and mtype != "response":
            return
        answers = r.get("answer")
        if isinstance(answers, str):
            answers = [answers]
        ttl = float(as_int(r.get("ttl")) or 0)
        for a in answers or []:
            a = as_str(a)
            if is_ip(a):
                yield IdentityEvent(ts=ts, kind="dns", ip=a, hostname=name, record_ttl=ttl)

    def _cim_web(self, r: dict) -> Iterator[Event]:
        """CIM Web -> ServiceEvent(http), responder-keyed; attrs spelled as the Zeek reader
        spells them so one `http` consumer reads both."""
        ts = self._start(r)
        ip = as_str(first_of(r, "dest_ip", "dest"))
        if ts is None or not ip:
            return
        if not is_ip(ip):
            self._count(self.unrecognized_stems, "(service dest not an IP)")
            return
        attrs = {k: v for k, v in {
            "method": as_str(r.get("http_method")),
            "host": as_str(first_of(r, "site", "http_host", "dest_host")),
            "uri": as_str(first_of(r, "uri_path", "url")),
            "user_agent": as_str(r.get("http_user_agent")),
            "status_code": as_str(r.get("status")),
        }.items() if v}
        yield ServiceEvent(ts=ts, kind="http", ip=ip, port=as_int(r.get("dest_port")) or 80,
                           attrs=attrs)

    def _cim_cert(self, r: dict) -> Iterator[Event]:
        """CIM Certificates -> ServiceEvent(ssl), responder-keyed."""
        ts = self._start(r)
        ip = as_str(first_of(r, "dest_ip", "dest"))
        if ts is None or not ip:
            return
        if not is_ip(ip):
            self._count(self.unrecognized_stems, "(service dest not an IP)")
            return
        attrs = {k: v for k, v in {
            "server_name": as_str(first_of(r, "ssl_name", "ssl_subject_common_name")),
            "version": as_str(r.get("ssl_version")),
            "subject": as_str(r.get("ssl_subject")),
            "issuer": as_str(r.get("ssl_issuer")),
            "serial": as_str(r.get("ssl_serial")),
        }.items() if v}
        yield ServiceEvent(ts=ts, kind="ssl", ip=ip, port=as_int(r.get("dest_port")) or 443,
                           attrs=attrs)

