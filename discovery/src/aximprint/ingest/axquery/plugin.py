"""axquery ingest plugin — what an axquery row *means*.

The project's SECOND sensor, and the first exercise of DESIGN_BRIEF §0.1's per-sensor plugin
contract by anything other than Zeek. `docs/WORK_ORDER_axquery_ingest.md` is the design.

**axquery is structurally unlike Zeek, and that is the whole point.** Zeek hands us records and
aximprint infers; axquery is a query layer over an appliance's own analytics ENTITY DATABASE and
has already inferred. The user's D1 ruling (2026-08-19) is that its outputs enter as
**observations** — aximprint does not run a parallel learner layer to re-derive what the sensor
states — with one carve-out ruled 2026-08-20:

    measurements are `observed`   -- /api/connections, /api/events, /api/entities.apps,
                                     extents, and the coverage block
    conclusions are `imputed`     -- /api/devices.roles

Otherwise a foreign system's conclusion would carry a STRONGER evidence class than aximprint's
own conclusion drawn from the same kind of behaviour, and `catalog/roles.yaml` fixes `has_role`
as `imputed`, never `observed`, for exactly that reason.

Endpoint table (no fall-through: an endpoint in neither the table nor the declined list is a
coverage gap this build reports rather than ignores):

  /api/connections        -> FlowEvent           + SensorHealthEvent from `coverage`
  /api/events?type=dhcp   -> IdentityEvent(dhcp)  hostname<->mac<->assigned ip
  /api/events?type=dns    -> IdentityEvent(dns)   query name<->answer ip (referenced)
  /api/info               -> probe_vantage() only, never events

The `/api/events` identity read was DECLINED at build (2026-08-20) and REVISITED 2026-09-08 under
a user directive to mine axquery's richer data for the twin-discovery loop (Supersedes the
`/api/events` decline for the identity half; `docs/WORK_ORDER_axquery_ingest.md`). Only the
APP-LAYER identity half is taken — never a second flow row — so the double-count the original
decline named cannot occur; `_connections` still owns every flow.

What this plugin deliberately does NOT do, each measured:

* **No MAC identity FROM `/api/entities`.** `/api/entities.mac` is `00:00:00:00:00:00` on 5,000
  of 5,000 sampled rows, and conn-level MACs are next-hop routers on this rdr mirror. The REAL
  macs come from DHCP option-61/`dhcp.client_mac` (`_ev_dhcp`), joined to the client-supplied
  host name in ONE row — the one identity anchor a null-mac router mirror otherwise lacks.
* **No `/api/events?type=kerberos|ntlm`.** Measured 0 rows in this build's windows: the AD
  identity on this rdr-mirror surfaces via the `/api/identity/users` census, not via connection
  events. Wired back the moment a capture populates them (see `_IDENTITY_PARSERS`).
* **No `/api/identity/users` account census.** It carries NO address, so it anchors nothing in
  the resolver, and there is no IR carrier for an address-less directory dump today — the realm
  and account list are `docs/WORK_ORDER_reference_ingest.md` S5 (`ir_policy(scope='domain')`).
  See `_DECLINED`.
* **No locality.** axquery declares no `local_nets` equivalent, so `local_src`/`local_dst` stay
  `None` and `windowing/segments.py` falls back to RFC1918 at `imputed` 0.6 for every flow from
  this sensor (D4). VLAN partially rescues it — see `_flow`.
"""
from __future__ import annotations

import datetime as _dt
import heapq
import ipaddress
from typing import Iterator, Optional

from aximprint.events import (
    Event, FlowEvent, IdentityEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.ingest.base import IngestPlugin, register
from aximprint.vantage import Vantage

from .sources import AxQueryError, AxQuerySource

#: TCP flag bits, all EIGHT of them. Values above 63 carry ECN (`ECE`/`CWR`) and were measured
#: on real rows (`219/91` pairs), so a six-bit decode mislabels real traffic.
_FIN, _SYN, _RST, _PSH, _ACK, _URG, _ECE, _CWR = 1, 2, 4, 8, 16, 32, 64, 128

#: `/api/entities.apps[].type` -> whether the entity was SERVING or merely talking. axquery's
#: own vocabulary is `<Proto><Role>` (`DNSServer`, `HTTPSServer`, `UDPUnknownClient`). Only the
#: server side is a durable property of the host; a client app is a fact about a session.
#: Declared as a suffix rule rather than an enum because the vocabulary is the sensor's and open
#: — an unmatched suffix is COUNTED, never assumed to be one or the other.
_SERVER_SUFFIX = "Server"
_CLIENT_SUFFIX = "Client"


def _epoch(value: object, field: str) -> float:
    """One timestamp normalizer, because axquery uses THREE representations (D7).

    Measured: `/api/connections` splits `tsSec`/`tsNsec`, `/api/entities` returns ISO-8601
    strings (`"2026-06-07T23:52:53.309Z"`), `/api/identity/users` returns NANOSECOND integers
    (`1780906203446124739`). Guessing between them is how a 2026 timestamp becomes a 1970 one.

    Fails loud on a shape it does not recognize rather than coercing — a wrong `ts` silently
    reassigns an event to another window, which no downstream check would catch.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        # Nanoseconds since epoch are ~1.8e18 today; seconds are ~1.8e9. The gap is nine orders
        # of magnitude, so this discriminates without a heuristic threshold anyone must tune.
        return v / 1e9 if v > 1e15 else v
    if isinstance(value, str) and value:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _dt.datetime.fromisoformat(text).timestamp()
        except ValueError as exc:
            raise AxQueryError(f"axquery {field}: unparseable timestamp {value!r}") from exc
    raise AxQueryError(f"axquery {field}: unrecognized timestamp shape {value!r}")


def _vlan(value: object) -> Optional[int]:
    """axquery stores 0 for *direction not observed tagged*, which is NOT VLAN 0 (`LES-006`).

    Its own source says so: *"0 = direction not observed tagged."* Passing the 0 through would
    invent a VLAN and hand `segments.py` an `observed` fact that was never observed.
    """
    if value is None or value == "":
        return None
    try:
        vid = int(value)
    except (TypeError, ValueError):
        return None
    return vid or None


def _state_class(o_flags: Optional[int], r_flags: Optional[int], transport: str,
                 responder_replied: bool = False) -> tuple[str, str]:
    """(state, state_class) from the cumulative per-direction TCP flag bytes.

    `state_class` is mandatory on `FlowEvent` and the vocabulary is closed, so this is the one
    place a mis-map would quietly mis-describe every flow this sensor produces. axquery exposes
    no connection-state STRING (`state_history` exists in its DB but reaches no endpoint), so
    the Zeek-equivalent outcome is DERIVED from the two flag bytes — which is a computation over
    observations, permitted by D1, and measured to separate cleanly on real rows:

        SYN+PSH+ACK / same          established, no teardown seen   x18
        FIN+SYN+PSH+ACK / same      clean open and close            x14   (Zeek SF)
        ...+RST... on either side   aborted by that side            x17   (RSTO / RSTR)
        SYN / none                  attempt, no reply               x5    (S0)
        PSH+ACK or ACK, no SYN      midstream capture               x5    (OTH)

    **UDP is derived from DIRECTIONALITY, not from flags** (it has none). axquery's orig/resp
    are direction-exact and `respPort` is the SERVER, so a responder that returned any traffic
    (`respBytes>0` or `respPackets>0`, threaded in as `responder_replied`) proves a live
    listener there — an `established` flow that rides the same `FLOW_ACCEPTED` listen-census gate
    (`windowing/windows.py`) as an accepted TCP flow. This is what carries DNS/53, NTP/123,
    DHCP/67 and SNMP/161 into the twin's `services[]`; before it, every non-TCP flow collapsed to
    `unknown` and NO UDP service ever reached the twin. A UDP flow the responder never answered
    is `attempted` — the TCP-`S0` analogue, up but not a listener.

    **ICMP (and any other non-TCP, non-UDP transport) stays `unknown`, COUNTED as such**, and
    MUST NOT be promoted: for ICMP `dst_port` is the message TYPE, not a service port, so an
    accepted class there would mint a bogus `listens_on 8/icmp` (the gate's own comment in
    `windowing/windows.py`). UDP is the one non-TCP transport whose `respPort` is a real port.
    `state` keeps a compact raw token so the derivation stays auditable downstream.
    """
    if transport.lower() == "udp":
        return ("udp/reply", "established") if responder_replied else ("udp/noreply", "attempted")
    if o_flags is None and r_flags is None:
        return "", "unknown"
    o, r = o_flags or 0, r_flags or 0
    raw = f"O:{o}/R:{r}"
    if transport.lower() != "tcp":
        return raw, "unknown"
    saw_syn = bool(o & _SYN) or bool(r & _SYN)
    if not saw_syn:
        return raw, "other"                      # midstream: no handshake observed
    if not r:
        return raw, "attempted"                  # SYN out, nothing back at all
    if o & _RST:
        return raw, "reset_orig"
    if r & _RST:
        return raw, "reset_resp"
    if not (o & _ACK) or not (r & _ACK):
        return raw, "partial"                    # one direction only — a vantage fact
    return raw, "established"


# -- identity, from /api/events (revisited 2026-09-08, see the module docstring) ----------------

def _s(value: object) -> str:
    """A stripped string, or `''` — never `None`, so a missing field reads as absent rather than
    as the literal string `'None'` (`ingest/zeek/plugin.py::_s`'s discipline, one transport over)."""
    return str(value).strip() if value not in (None, "") else ""


def _is_ip(value: str) -> bool:
    """True only for a real IPv4/IPv6 literal. A DNS answer that is a NAME (CNAME/MX/SRV) binds
    nothing the resolver keys on, so it is dropped rather than coerced into an address
    (`ingest/zeek/plugin.py::_is_ip`; fail closed)."""
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _event_ts(r: dict, field: str) -> float:
    """A `/api/events` row's start: `tsSec`(+`tsNsec`), the same representation `/api/connections`
    uses (D7's one timestamp normalizer covers both). Fails loud on an unrecognized shape."""
    return _epoch(r.get("tsSec"), field) + float(r.get("tsNsec") or 0) / 1e9


def _event_start(r: dict) -> Optional[float]:
    """The bisection partition key for a `/api/events` row — its own start, or `None` when the row
    carries no `tsSec` (mirrors `_connections._start`; a row `bisected_rows` cannot place in a
    leaf is skipped, not fail-loud, because it is a partition key, not the emitted `ts`)."""
    sec = r.get("tsSec")
    if sec is None:
        return None
    return float(sec) + float(r.get("tsNsec") or 0) / 1e9


def _ev_dhcp(r: dict) -> Iterator[Event]:
    """DHCP -> IdentityEvent(dhcp): the client host name <-> its mac <-> its address.

    THE identity anchor a router-mirror otherwise lacks — every conn-level mac on this vantage is
    null, but DHCP carries the real `dhcp.client_mac` alongside the client-supplied
    `dhcp.hostname` in ONE row, which is the cross-namespace link `_identifiers_dhcp` fuses into a
    single host. The ASSIGNED address (`dhcp.your_ip`, on the server's ack) wins over the
    REQUESTED one (`dhcp.requested_ip`, on the client's request), mirroring the zeek plugin's
    assigned-or-requested fallback; either alone still yields a mac/name binding at that address.
    """
    mac = _s(r.get("dhcp.client_mac"))
    ip = _s(r.get("dhcp.your_ip")) or _s(r.get("dhcp.requested_ip"))
    if not (ip and mac):
        return
    yield IdentityEvent(ts=_event_ts(r, "events.dhcp.tsSec"), kind="dhcp", ip=ip, mac=mac,
                        hostname=_s(r.get("dhcp.hostname")))


def _ev_dns(r: dict) -> Iterator[Event]:
    """DNS RESPONSE -> IdentityEvent(dns): query name <-> each A/AAAA answer address.

    Responses only (`dns.flag_qr == 1`; a query carries no answer to bind). Each entry in
    `dns.answers[]` whose `value` is an ADDRESS binds the name to it (`referenced` — a third
    party's address, which D1 keeps out of the merge path); CNAME/MX/SRV/TXT answers are names
    and bind nothing the resolver keys on, so they are dropped, never coerced. Populates the
    golden bundle's `observations.dns.names`.
    """
    if int(r.get("dns.flag_qr") or 0) != 1:
        return
    name = _s(r.get("dns.query"))
    if not name:
        return
    ts = _event_ts(r, "events.dns.tsSec")
    for ans in (r.get("dns.answers") or []):
        if not isinstance(ans, dict):
            continue
        addr = _s(ans.get("value"))
        if _is_ip(addr):
            yield IdentityEvent(ts=ts, kind="dns", ip=addr, hostname=name,
                                record_ttl=float(ans.get("ttl") or 0))


#: `/api/events?type=<key>` -> the parser for that type's identity. Only kinds that carry an
#: ADDRESS reach the resolver (`windowing.entities.IDENTITY_SCOPES`), and only the two MEASURED as
#: populated on this build are here. `kerberos`/`ntlm` are deliberately ABSENT — 0 rows measured
#: 2026-09-08 (module docstring) — and a parser is added the moment a capture populates them, the
#: same positive-gate discipline `_ENDPOINTS`/`_DECLINED` apply to whole endpoints.
_IDENTITY_PARSERS = {"dhcp": _ev_dhcp, "dns": _ev_dns}


@register
class AxQueryPlugin(IngestPlugin):
    """Adapt one axquery instance to the normalized event model."""

    name = "axquery"
    source_help = "an axquery instance, addressed as 'axquery:<base-url>'"
    source_kind = "http"

    #: Endpoints this build reads. Anything the server offers that is not here and not in
    #: `_DECLINED` is reported by `unrecognized_stems` — the same positive-gate discipline the
    #: zeek plugin applies to log stems, one transport over. `/api/events` reads the APP-LAYER
    #: identity half only (`_IDENTITY_PARSERS`: dhcp/dns); the flow half stays `/api/connections`.
    _ENDPOINTS = ("/api/connections", "/api/events")

    #: Declined BY NAME, each with a reason, so a reader finds it here instead of inferring it
    #: from absence (`ingest/zeek/plugin.py::_SKIP`'s discipline).
    _DECLINED: dict[str, str] = {
        "/api/graph": "a derived topology view over the same conns — a CONCLUSION, and one "
                      "aximprint's own edge accumulator already computes from the flows",
        "/api/v1/query": "the Prometheus API: whole-DB counters for dashboards, no per-entity "
                         "facts",
        "/metrics": "Prometheus exposition of this server's own health, not the network's",
        "/api/tunnels": "unmeasured on this instance; admitting an endpoint nobody has counted "
                        "is the mistake `radius`/`rdp` already cost this project twice",
        "/api/findings/counts": "server-side detections — an alert channel, and `AlertEvent` "
                                "wants a measured shape before a second producer is wired in",
        # The three census endpoints. DECLINED ON MEASUREMENT, not on preference, and this is
        # the finding that shaped this build: measured 2026-08-20, NO endpoint on this server
        # pages. `offset` is ignored and `nextOffset` is always null on every one of them, so
        # the only lever is `limit` — and `/api/entities` holds 1,420,319 rows while walking
        # `limit` up is what OOM-killed this server. Unlike `/api/connections` these accept no
        # time bound either, so bisection cannot rescue them: the reachable prefix is ordered by
        # ascending address, which makes it a SYSTEMATICALLY BIASED sample rather than a census.
        # Emitting facts from it would publish "the numerically lowest addresses" as if it were
        # the network. Named path back: axquery gains a cursor, or these are derived from the
        # flows this plugin already reads completely.
        "/api/entities": "server-side app census — no cursor, no time filter, 1.42 M rows; the "
                         "reachable prefix is ascending-address-ordered and therefore biased. "
                         "`apps[].type` is also largely derivable from the flows already read",
        "/api/devices": "axquery's ROLE conclusions (`imputed` per the D1 carve-out) and the "
                        "one thing here not derivable from flows — but same truncation: 925 "
                        "rows at limit=5000 and `count` still rises with `limit`, so the API "
                        "cannot even confirm that 925 is all of them",
        # REVISITED 2026-09-08 under the same rich-data directive that admitted `/api/events`
        # identity, and DELIBERATELY LEFT DECLINED — the directive does not change the fact that
        # the census carries NO address. Under R2's subject discipline it anchors nothing in the
        # resolver, and there is no IR carrier for an address-less directory dump: the golden
        # bundle's identity overlay (`synth/imprint_bundle.py::_observations`) projects
        # `ir_identity`, which is address-keyed, so a realm/account list has nowhere to land. That
        # carrier is `docs/WORK_ORDER_reference_ingest.md` S5 (`ir_policy(scope='domain',
        # parameter='realm')`), and its own known transform is realm-case normalization
        # (AXELLIO/Axellio/axellio.dom are one realm). The address-bearing AD identity this build
        # DOES need — hostnames bound to addresses — comes from `/api/events?type=dhcp` instead.
        "/api/identity/users": "account census with NO address, so under R2's subject "
                               "discipline it anchors nothing anyway, and no IR carrier exists "
                               "for an address-less directory dump (S5, see the block comment). "
                               "Also un-consolidated: `totalUsers` grows with `limit` "
                               "(22 -> 67 -> 505) exactly like `totalMatches`",
    }

    def __init__(self) -> None:
        self._source: Optional[AxQuerySource] = None

    # -- contract ---------------------------------------------------------------

    def probe_vantage(self, source: str) -> Optional[Vantage]:
        """Where the tap sat, from `/api/info`, before a single event is pulled.

        The first plugin able to answer this WITHOUT a harvest manifest: axquery reports its own
        site, system and stream count. Note the identity it gives (`axellio-hq/corp-apcn-rdr1`,
        12 streams) is NOT the string the Zeek side records for the same tap
        (`https://10.80.65.7:0-11`), so PITs from the two sensors will not compare until an
        operator declares them equal — `compare` gates on vantage-set equality (D6). Naming that
        here is the point: the mismatch is visible before a run, not after one.
        """
        try:
            info = AxQuerySource(source).info()
        except AxQueryError:
            return None
        sites = info.get("sites") or []
        if not sites:
            return None
        site = sites[0]
        systems = site.get("systems") or [{}]
        system = systems[0]
        appliance = f"{site.get('location', '?')}/{system.get('name', '?')}"
        streams = system.get("streams")
        return Vantage(
            vantage=f"{appliance}:{streams}" if streams is not None else appliance,
            appliance=appliance,
            streams=str(streams) if streams is not None else None,
            produced_by="axquery",
            planned_from=float(info["first_ts"]) if info.get("first_ts") else None,
            planned_to=float(info["last_ts"]) if info.get("last_ts") else None,
        )

    def probe(self, source: str) -> dict:
        """`/api/info` as a reachability-plus-span answer, never events (`ingest/base.py`).

        What the control plane's "test this source" button shows, and what turns a
        "last N days" request into bounds: `last_ts` is INCLUSIVE on this API, so the
        half-open upper bound a caller wants is `last_ts + 1`. `error` is the one
        `AxQueryError` message, verbatim -- a wrong port, a closed tunnel and a non-JSON
        answer each read differently and the operator needs the difference.
        """
        try:
            info = AxQuerySource(source).info()
        except AxQueryError as exc:
            return {"supported": True, "reachable": False, "error": str(exc),
                    "first_ts": None, "last_ts": None, "vantage": None, "extra": {}}
        vantage = self.probe_vantage(source)
        extra = {k: info[k] for k in ("chunks", "packets", "bytes", "entities", "events",
                                      "endpoints", "applications")
                 if isinstance(info.get(k), (int, float))}
        extra["sites"] = info.get("sites") or []
        return {
            "supported": True, "reachable": True, "error": None,
            "first_ts": float(info["first_ts"]) if info.get("first_ts") is not None else None,
            "last_ts": float(info["last_ts"]) if info.get("last_ts") is not None else None,
            "vantage": vantage.vantage if vantage is not None else None,
            "extra": extra,
        }

    def events(self, source: str, ts_from: float | None = None,
               ts_to: float | None = None) -> Iterator[Event]:
        """Yield normalized events for `[ts_from, ts_to)`.

        Unlike a Zeek log directory — which has no partition index and must REFUSE a slice it
        cannot honour — axquery filters natively on `after`/`before` and reports its own
        coverage against the range asked for. That is why this plugin accepts a bound the zeek
        plugin rejects on the same argument.
        """
        src = AxQuerySource(source)
        self._source = src
        # Endpoint inventory is a genuine census: `_ENDPOINTS` and `_DECLINED` are this build's
        # own closed tables, not a remote listing, so both are final before the first yield —
        # the contract requires exactly that (`ingest/base.py`).
        self.unparsed_stems = {p: 1 for p in self._DECLINED}
        self.unrecognized_stems = {}

        # An unbounded call still needs CONCRETE bounds, because completeness here comes from
        # time-bisection and you cannot bisect infinity. `/api/info` supplies the server's own
        # span, which is the honest full range for "everything this sensor holds".
        if ts_from is None or ts_to is None:
            info = src.info()
            first, last = info.get("first_ts"), info.get("last_ts")
            if first is None or last is None:
                raise AxQueryError(
                    "axquery /api/info carries no first_ts/last_ts, so an unbounded read has no "
                    "range to bisect — pass --from/--to")
            ts_from = float(first) if ts_from is None else ts_from
            # `last_ts` is inclusive on this API; the slice contract is half-open.
            ts_to = float(last) + 1.0 if ts_to is None else ts_to

        # k-way MERGE by ts, exactly like the zeek plugin's `heapq.merge` over its log-type
        # streams — never a concatenation. Each stream (`_connections` and one per identity type)
        # is already ~ascending because `bisected_rows` walks leaves left-to-right, so the merged
        # output is globally ~ascending and the downstream reorder buffer only has bounded local
        # disorder to smooth. Concatenating instead — all flows, THEN all dhcp, THEN all dns —
        # made every identity event arrive ~hours behind the clock the flow stream had already
        # advanced, so the windower dumped them all into the final window (measured 2026-09-08).
        streams: list[Iterator[Event]] = [self._connections(src, float(ts_from), float(ts_to))]
        for etype, parse in _IDENTITY_PARSERS.items():
            streams.append(self._identity_stream(src, float(ts_from), float(ts_to), etype, parse))
        yield from heapq.merge(*streams, key=lambda ev: ev.ts)

    # -- endpoints --------------------------------------------------------------

    def _identity_stream(self, src: AxQuerySource, after: float, before: float,
                         etype: str, parse) -> Iterator[Event]:
        """One address-bearing identity stream from `/api/events?type=<etype>`, ts-ascending.

        Only kinds that carry an ADDRESS reach the resolver (`windowing.entities.IDENTITY_SCOPES`),
        so `_IDENTITY_PARSERS` holds exactly the two this build MEASURED as populated: `dhcp`
        (hostname<->mac<->assigned ip — the anchor a null-mac rdr mirror otherwise lacks) and
        `dns` (query name<->answer ip). Bisected on TIME with the same discipline as
        `_connections` — `/api/events` filters on `after`/`before` and ignores `offset`/`limit`
        for paging, exactly like `/api/connections`, so a dense window splits rather than
        truncates. `include_extents=0` is passed for parity; the identity fields this reads
        (`dhcp.*`, `dns.*`) are top-level, never inside `extents[]`. Kept a SEPARATE stream per
        type (rather than one method yielding both) so `events()` can k-way merge it with the flow
        stream by ts.

        NO coverage-health event is emitted here on purpose: coverage is a property of the CHUNKS
        over `[after, before)`, and `_connections` already reports it for the identical range over
        the same server — a second report from this read would double-count one health signal.
        """
        for r in src.bisected_rows("/api/events", "events", after, before,
                                   _event_start, type=etype, include_extents=0):
            yield from parse(r)

    def _connections(self, src: AxQuerySource, after: float,
                     before: float) -> Iterator[Event]:
        """Every connection in `[after, before)`, complete by construction.

        `include_extents=0` on purpose: the extent array is the biggest thing in a response
        (2,880 entries on one measured TLS flow) and nothing consumes it yet — `WindowResult`
        exposes no sub-bucket channel to a plugin. Asking for it would inflate every page on a
        server that has already been killed once by a large response.

        A `SensorHealthEvent` is emitted per LEAF request rather than once per call, because
        coverage is reported per request and a bisected read makes many: collapsing them would
        average away the one incomplete sub-interval, which is the only one worth knowing about.
        """
        def _start(r: dict) -> Optional[float]:
            """A connection's own start, which is what the leaf filter partitions on."""
            sec = r.get("tsSec")
            if sec is None:
                return None
            return float(sec) + float(r.get("tsNsec") or 0) / 1e9

        for r in src.bisected_rows("/api/connections", "connections", after, before,
                                   _start, include_extents=0):
            ev = self._flow(r)
            if ev is not None:
                yield ev
                cov = src.last_coverage
                if cov is not None and not cov.complete:
                    # The sensor's OWN completeness claim, against the range asked for. Emitted
                    # as a loss percentage so it lands in the same health channel as Zeek's
                    # `capture_loss` instead of inventing a second vocabulary for one question.
                    yield SensorHealthEvent(
                        ts=ev.ts, kind="capture_loss",
                        value=round(max(0.0, 1.0 - cov.fraction) * 100.0, 6),
                        detail=f"axquery coverage: {cov.chunks_covered}/{cov.chunks_wanted} "
                               f"chunks, {cov.chunks_missing} missing, "
                               f"reason={cov.reason or 'none'}")

    @staticmethod
    def _flow(r: dict) -> Optional[FlowEvent]:
        ip_o, ip_r = r.get("origIp"), r.get("respIp")
        if not ip_o or not ip_r:
            return None
        transport = str(r.get("transport") or "")
        bytes_in = int(r.get("respBytes") or 0)
        pkts_in = int(r.get("respPackets") or 0)
        state, state_class = _state_class(
            r.get("origTcpFlags"), r.get("respTcpFlags"), transport,
            # A responder that returned ANY traffic proves a live listener — the only signal a
            # flagless UDP flow carries. Unused for TCP (its outcome is the flag bytes).
            responder_replied=(bytes_in > 0 or pkts_in > 0))
        ts = _epoch(r.get("tsSec"), "connections.tsSec")
        last = r.get("lastUpdateSec")
        return FlowEvent(
            ts=ts + float(r.get("tsNsec") or 0) / 1e9,
            src_ip=str(ip_o), dst_ip=str(ip_r),
            src_port=int(r.get("origPort") or 0), dst_port=int(r.get("respPort") or 0),
            proto=transport.lower(),
            # axquery's `protocol` is the L7 label (`dns`/`tls`/`ssh`/`other`), which is what
            # `service` means here — NOT the transport. Filtering on it is also the API's
            # sharpest trap: `proto=tcp` returns 0 rows in a window holding 24 TCP flows.
            service=str(r.get("protocol") or ""),
            duration=max(0.0, _epoch(last, "connections.lastUpdateSec") - ts) if last else 0.0,
            bytes_out=int(r.get("origBytes") or 0), bytes_in=bytes_in,
            pkts_out=int(r.get("origPackets") or 0), pkts_in=pkts_in,
            state=state, state_class=state_class,
            originator_is_src=True,          # axquery's orig/resp are direction-exact
            vlan_src=_vlan(r.get("origVlanId") if "origVlanId" in r else r.get("vlanId")),
            vlan_dst=_vlan(r.get("respVlanId")),
            # D4: axquery declares no local_nets equivalent. `None`, never a guess — segments.py
            # imputes and SAYS it imputed.
            local_src=None, local_dst=None,
        )



