"""axquery ingest plugin — the project's SECOND sensor.

Every test here stubs `urllib.request.urlopen`: the repo-wide conftest guard forbids real
network egress, and more importantly a test that needed a live axquery would be a test nobody
can run. The fixtures are shaped from rows measured on the real instance on 2026-08-20 — the
whole point of this plugin's design is what that measurement said, so inventing prettier rows
would test a server that does not exist.
"""
from __future__ import annotations

import io
import json

import pytest

from aximprint.events import FlowEvent, SensorHealthEvent
from aximprint.ingest import get_plugin
from aximprint.ingest.axquery.sources import (
    PAGE_ROWS, AxQueryError, AxQuerySource,
)

BASE = "axquery:http://127.0.0.1:19090"
T0 = 1780872000.0


def _conn(ts, o="10.0.0.1", r="10.0.0.2", op=1234, rp=443, transport="TCP",
          oflags=None, rflags=None, **kw):
    row = {"origIp": o, "respIp": r, "origPort": op, "respPort": rp,
           "transport": transport, "protocol": "other",
           "origBytes": 100, "respBytes": 200, "origPackets": 2, "respPackets": 3,
           "tsSec": int(ts), "tsNsec": 0, "lastUpdateSec": int(ts) + 5}
    if oflags is not None:
        row["origTcpFlags"] = oflags
    if rflags is not None:
        row["respTcpFlags"] = rflags
    row.update(kw)
    return row


def _stub(monkeypatch, handler):
    """Route every urlopen through `handler(path, params) -> dict`."""
    import urllib.parse
    import urllib.request

    def fake_urlopen(url, timeout=None):
        # The transport sends a `Request` (it carries the Authorization header);
        # a bare URL string is still accepted so older stubs read the same.
        url = getattr(url, "full_url", url)
        parsed = urllib.parse.urlparse(url)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        body = json.dumps(handler(parsed.path, params)).encode()
        return io.BytesIO(body)

    # A BytesIO is not a context manager returning itself in older forms; it is here.
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


# -- locator (D6) ----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["http://x:1", "axquery:127.0.0.1:19090", "axquery:", "zeek:/x"])
def test_locator_refuses_anything_that_is_not_an_absolute_axquery_url(bad):
    """Guessing a scheme would connect to the wrong service and report it as data."""
    with pytest.raises(AxQueryError):
        AxQuerySource(bad)


def test_locator_accepts_and_normalizes_a_trailing_slash():
    assert AxQuerySource("axquery:http://127.0.0.1:19090/").base == "http://127.0.0.1:19090"


# -- timestamps (D7) -------------------------------------------------------------

def test_three_timestamp_representations_all_normalize_to_epoch_seconds():
    """Measured: connections split tsSec/tsNsec, entities return ISO-8601, identity/users
    return NANOSECONDS. Guessing between them turns 2026 into 1970."""
    from aximprint.ingest.axquery.plugin import _epoch

    assert _epoch(1780872000, "t") == pytest.approx(1780872000.0)
    assert _epoch(1780906203446124739, "t") == pytest.approx(1780906203.446, abs=1e-2)
    assert _epoch("2026-06-07T23:52:53.309Z", "t") == pytest.approx(1780876373.309, abs=1)


def test_an_unrecognized_timestamp_shape_fails_loud_rather_than_coercing():
    from aximprint.ingest.axquery.plugin import _epoch

    for bad in (None, "", "not-a-time", {}):
        with pytest.raises(AxQueryError):
            _epoch(bad, "t")


# -- VLAN (D3) -------------------------------------------------------------------

def test_vlan_zero_means_not_observed_tagged_and_becomes_none():
    """axquery's own encoding: 0 = direction not observed tagged. Passing it through would
    invent VLAN 0 and hand segments.py an `observed` fact nobody observed (LES-006)."""
    from aximprint.ingest.axquery.plugin import _vlan

    assert _vlan(0) is None
    assert _vlan("0") is None
    assert _vlan(None) is None
    assert _vlan(20) == 20


def test_vlan_is_carried_per_direction_not_collapsed(monkeypatch):
    """Two fields, because asymmetric tagging is real at a capture point and axquery already
    backed out of a single connection-level VLAN for exactly that reason."""
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(T0, origVlanId=20, respVlanId=30)], "coverage": {"complete": True}})
    (flow,) = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 1)
               if isinstance(e, FlowEvent)]
    assert (flow.vlan_src, flow.vlan_dst) == (20, 30)


# -- state_class (D2) ------------------------------------------------------------

@pytest.mark.parametrize("o,r,expected", [
    (27, 27, "established"),   # FIN+SYN+PSH+ACK both ways -- Zeek SF
    (26, 26, "established"),   # SYN+PSH+ACK, no teardown seen
    (31, 27, "reset_orig"),    # originator aborted
    (27, 31, "reset_resp"),    # responder aborted
    (2, 0, "attempted"),       # SYN out, nothing back
    (24, 24, "other"),         # PSH+ACK with no SYN -- midstream capture
    (219, 91, "established"),  # ECN bits set: decode all EIGHT flags, not six
])
def test_state_class_derives_from_the_two_tcp_flag_bytes(o, r, expected):
    """axquery exposes no connection-state string -- `state_history` reaches no endpoint -- so
    the Zeek-equivalent outcome is derived. Every pair here was measured on real rows."""
    from aximprint.ingest.axquery.plugin import _state_class

    assert _state_class(o, r, "TCP")[1] == expected


def test_icmp_and_other_flagless_transports_stay_unknown_and_are_never_bucketed():
    """ICMP carries no flags AND its `dst_port` is the message TYPE, not a service port, so a
    promoted icmp would mint a bogus `listens_on 8/icmp` (LES-006, and the accept gate's own
    comment in `windowing/windows.py`). UDP is the one non-TCP transport whose `respPort` is a
    real port and is covered separately below."""
    from aximprint.ingest.axquery.plugin import _state_class

    for transport in ("ICMP", "gre", "esp"):
        state, klass = _state_class(None, None, transport)
        assert klass == "unknown" and state == ""


# -- UDP listeners (2026-09-08: derived from DIRECTIONALITY, since UDP has no flags) ---

def test_udp_with_a_responder_reply_is_established_and_rides_the_accept_gate():
    """A UDP responder that returned any traffic proves a live listener — an `established` flow,
    which is what carries DNS/53, NTP/123, DHCP/67 and SNMP/161 into the twin's services[]. It
    MUST land in `FLOW_ACCEPTED` so the listen-census gate mints `listens_on <port>/udp`."""
    from aximprint.events import FLOW_ACCEPTED
    from aximprint.ingest.axquery.plugin import _state_class

    state, klass = _state_class(None, None, "udp", responder_replied=True)
    assert klass == "established" and klass in FLOW_ACCEPTED and state == "udp/reply"


def test_udp_with_no_reply_is_attempted_and_never_reaches_the_accept_gate():
    """Only-originator UDP is the TCP-`S0` analogue: the host may be up, but nothing proves a
    listener, so it must stay OUT of `FLOW_ACCEPTED`."""
    from aximprint.events import FLOW_ACCEPTED
    from aximprint.ingest.axquery.plugin import _state_class

    state, klass = _state_class(None, None, "udp", responder_replied=False)
    assert klass == "attempted" and klass not in FLOW_ACCEPTED and state == "udp/noreply"


def test_a_udp_flow_with_a_reply_becomes_established_on_the_responder_port():
    """The whole flow path in one: `respBytes>0` -> `responder_replied` -> `established`, on the
    RESPONDER port (53), proto udp — exactly the (port, proto) the listen census keys on."""
    from aximprint.events import FLOW_ACCEPTED
    from aximprint.ingest.axquery.plugin import AxQueryPlugin

    flow = AxQueryPlugin._flow(_conn(T0, transport="udp", rp=53, respBytes=120, respPackets=1))
    assert flow.proto == "udp" and flow.dst_port == 53
    assert flow.state_class == "established" and flow.state_class in FLOW_ACCEPTED


def test_a_udp_flow_with_no_reply_is_attempted_and_carries_no_listener():
    from aximprint.ingest.axquery.plugin import AxQueryPlugin

    flow = AxQueryPlugin._flow(_conn(T0, transport="udp", rp=53, respBytes=0, respPackets=0))
    assert flow.proto == "udp" and flow.state_class == "attempted"


def test_the_raw_flag_pair_is_kept_so_the_derivation_stays_auditable():
    from aximprint.ingest.axquery.plugin import _state_class

    assert _state_class(27, 31, "TCP")[0] == "O:27/R:31"


# -- bisection (D5) --------------------------------------------------------------

def test_a_full_page_splits_the_interval_instead_of_asking_for_more(monkeypatch):
    """No endpoint on this server pages, and raising `limit` is what OOM-killed it once.
    Completeness comes from time, and peak server memory stays at one page."""
    seen = []

    def handler(path, p):
        lo, hi = int(p["after"]), int(p["before"])
        seen.append((lo, hi))
        # Saturate only the top-level request, forcing exactly one split.
        if hi - lo > 50:
            return {"connections": [_conn(lo + i % 10) for i in range(PAGE_ROWS)],
                    "coverage": {"complete": True}}
        return {"connections": [_conn(lo)], "coverage": {"complete": True}}

    _stub(monkeypatch, handler)
    flows = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 100)
             if isinstance(e, FlowEvent)]
    assert len(seen) > 1, "a full page must split, not truncate"
    assert all(f.ts is not None for f in flows)


def test_an_unsplittable_full_page_is_raised_never_silently_truncated(monkeypatch):
    """Returning a prefix here would be the invisible truncation this project treats as a lie."""
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(int(p["after"])) for _ in range(PAGE_ROWS)],
        "coverage": {"complete": True}})
    with pytest.raises(AxQueryError, match="floor"):
        list(get_plugin("axquery").events(BASE, T0, T0 + 2))


def test_only_connections_STARTING_in_a_leaf_are_emitted_by_it(monkeypatch):
    """`after`/`before` select conns that OVERLAP the range, so a long-lived conn matches every
    sub-interval it spans. Measured before this filter: 202,961 rows carrying 50,887 identities
    and a ts reaching 14.9 days back. Partitioning on the conn's own start makes leaves exact,
    which is what removes the need for a dedup set (and keeps the read streaming)."""
    long_lived = _conn(T0 - 86400)            # started a day before the window
    inside = _conn(T0 + 10)
    _stub(monkeypatch, lambda path, p: {
        "connections": [long_lived, inside], "coverage": {"complete": True}})

    flows = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 100)
             if isinstance(e, FlowEvent)]
    assert len(flows) == 1, "the conn that began before the slice belongs to an earlier one"
    assert flows[0].ts == pytest.approx(T0 + 10)


def test_every_emitted_flow_falls_inside_the_requested_half_open_slice(monkeypatch):
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(T0 - 1), _conn(T0), _conn(T0 + 50), _conn(T0 + 100)],
        "coverage": {"complete": True}})
    flows = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 100)
             if isinstance(e, FlowEvent)]
    assert [f.ts for f in flows] == [T0, T0 + 50], "half-open: T0 included, T0+100 excluded"


# -- coverage -> health ----------------------------------------------------------

def test_incomplete_coverage_becomes_a_sensor_health_event(monkeypatch):
    """The thing Zeek never gave us: a SENSOR-supplied completeness statement, against the
    range we asked for rather than the range it happened to hold."""
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(T0)],
        "coverage": {"complete": False, "reason": "chunks_missing", "chunksWanted": 4,
                     "chunksCovered": 3, "chunksMissing": 1, "fraction": 0.75}})
    health = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 10)
              if isinstance(e, SensorHealthEvent)]
    assert len(health) == 1
    assert health[0].kind == "capture_loss"
    assert health[0].value == pytest.approx(25.0)
    assert "1 missing" in health[0].detail


def test_complete_coverage_emits_no_health_event(monkeypatch):
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(T0)], "coverage": {"complete": True, "fraction": 1.0}})
    assert not [e for e in get_plugin("axquery").events(BASE, T0, T0 + 10)
                if isinstance(e, SensorHealthEvent)]


# -- vantage (D6) ----------------------------------------------------------------

def test_probe_vantage_reads_the_tap_identity_without_pulling_an_event(monkeypatch):
    """The first plugin able to answer this with no harvest manifest."""
    _stub(monkeypatch, lambda path, p: {
        "first_ts": 1779585094, "last_ts": 1781831495,
        "sites": [{"location": "axellio-hq",
                   "systems": [{"name": "corp-apcn-rdr1", "streams": 12}]}]})
    v = get_plugin("axquery").probe_vantage(BASE)
    assert v.vantage == "axellio-hq/corp-apcn-rdr1:12"
    assert v.produced_by == "axquery"


def test_probe_vantage_returns_none_when_the_server_is_unreachable(monkeypatch):
    """`None` means "this sensor records no vantage" and must never be a fabricated identity."""
    import urllib.request

    def boom(url, timeout=None):
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert get_plugin("axquery").probe_vantage(BASE) is None


# -- declined endpoints ----------------------------------------------------------

def test_the_three_census_endpoints_are_declined_by_name_with_a_reason():
    """Measured: none of them pages, and none accepts a time bound, so the reachable prefix is
    ascending-address-ordered -- a biased sample, not a census."""
    from aximprint.ingest.axquery.plugin import AxQueryPlugin

    for ep in ("/api/entities", "/api/devices", "/api/identity/users"):
        assert ep in AxQueryPlugin._DECLINED
        assert len(AxQueryPlugin._DECLINED[ep]) > 40, f"{ep} declined without a real reason"


def test_declined_endpoints_are_reported_as_unparsed_not_hidden(monkeypatch):
    """Same discipline as the zeek plugin's `_SKIP`: a decline is visible, never an absence."""
    _stub(monkeypatch, lambda path, p: {"connections": [], "coverage": {"complete": True}})
    p = get_plugin("axquery")
    events = p.events(BASE, T0, T0 + 1)
    next(events, None)
    assert p.unrecognized_stems == {}
    assert "/api/entities" in (p.unparsed_stems or {})


def test_axquery_declares_no_locality_and_says_so_rather_than_guessing(monkeypatch):
    """D4: axquery has no `local_nets` equivalent. `None`, never a guess -- segments.py imputes
    and marks the result imputed, which is where that decision belongs."""
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(T0)], "coverage": {"complete": True}})
    (flow,) = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 10)
               if isinstance(e, FlowEvent)]
    assert flow.local_src is None and flow.local_dst is None


def test_page_rows_exceeds_observed_peak_concurrency():
    """Sized against CONCURRENCY, not rate, and that distinction is what a first value of 500 got
    wrong. `after`/`before` match conns that OVERLAP an interval, so as the interval shrinks the
    row count converges to the number of conns ALIVE at that instant — a floor bisection cannot
    get under. Measured at the second that first hit it: 525 overlapping, 101 actually starting.
    A four-hour read failed at 500 and succeeds at 5,000.
    """
    assert PAGE_ROWS >= 5000, "must stay above measured peak concurrency (525) with headroom"


def test_the_floor_error_says_narrowing_will_not_help(monkeypatch):
    """The refusal must not send the reader down the path that already failed. Narrowing the
    range cannot fix a concurrency floor; only raising `PAGE_ROWS` can."""
    _stub(monkeypatch, lambda path, p: {
        "connections": [_conn(int(p["after"])) for _ in range(PAGE_ROWS)],
        "coverage": {"complete": True}})
    with pytest.raises(AxQueryError) as exc:
        list(get_plugin("axquery").events(BASE, T0, T0 + 2))
    assert "will NOT help" in str(exc.value)
    assert "PAGE_ROWS" in str(exc.value)


# -- identity from /api/events (2026-09-08: the rich half a router-mirror needs) ------

def _events_stub(monkeypatch, by_type):
    """`/api/connections` empty; `/api/events?type=X` returns `by_type[X]`, everything complete."""
    def handler(path, p):
        if path == "/api/events":
            return {"events": by_type.get(p.get("type"), []), "coverage": {"complete": True}}
        return {"connections": [], "coverage": {"complete": True}}
    _stub(monkeypatch, handler)


def test_dhcp_events_bind_hostname_mac_and_the_assigned_address():
    """The one identity anchor a null-mac router mirror otherwise lacks. `dhcp.your_ip` (the
    server's ack) is the ASSIGNED address, and it, the client mac and the client-supplied host
    name arrive together."""
    from aximprint.ingest.axquery.plugin import _ev_dhcp

    (ev,) = list(_ev_dhcp({"tsSec": int(T0), "tsNsec": 0, "dhcp.message_type": "ack",
                           "dhcp.client_mac": "3c:9c:0f:eb:6c:70", "dhcp.your_ip": "10.80.20.191",
                           "dhcp.hostname": "ENG-AD-CO-LTW11"}))
    assert ev.kind == "dhcp" and ev.ip == "10.80.20.191"
    assert ev.mac == "3c:9c:0f:eb:6c:70" and ev.hostname == "ENG-AD-CO-LTW11"


def test_dhcp_falls_back_to_requested_ip_when_no_assigned_address_is_present():
    """A DHCP request has no `your_ip`; `dhcp.requested_ip` is the address the binding is about —
    the assigned-or-requested fallback the zeek plugin also uses."""
    from aximprint.ingest.axquery.plugin import _ev_dhcp

    (ev,) = list(_ev_dhcp({"tsSec": int(T0), "dhcp.client_mac": "aa:bb:cc:dd:ee:ff",
                           "dhcp.requested_ip": "10.80.20.191", "dhcp.hostname": "WS01"}))
    assert ev.ip == "10.80.20.191" and ev.mac == "aa:bb:cc:dd:ee:ff"


def test_dhcp_without_both_a_mac_and_an_address_binds_nothing():
    from aximprint.ingest.axquery.plugin import _ev_dhcp
    assert list(_ev_dhcp({"tsSec": int(T0), "dhcp.hostname": "x"})) == []


def test_dns_responses_bind_each_address_answer_and_drop_the_name_answers():
    """A DNS response binds the query name to each A/AAAA answer (`referenced`); CNAME/MX/SRV
    answers are names and bind nothing the resolver keys on, so they are dropped, never coerced."""
    from aximprint.ingest.axquery.plugin import _ev_dns

    evs = list(_ev_dns({"tsSec": int(T0), "tsNsec": 0, "dns.flag_qr": 1,
                        "dns.query": "live.github.com",
                        "dns.answers": [{"typeName": "A", "value": "140.82.112.25", "ttl": 59},
                                        {"typeName": "CNAME", "value": "gh.cdn.net", "ttl": 30}]}))
    assert [(e.kind, e.ip, e.hostname) for e in evs] == \
        [("dns", "140.82.112.25", "live.github.com")]
    assert evs[0].record_ttl == 59


def test_dns_queries_bind_nothing_only_responses_carry_answers():
    from aximprint.ingest.axquery.plugin import _ev_dns
    assert list(_ev_dns({"tsSec": int(T0), "dns.flag_qr": 0, "dns.query": "example.com",
                         "dns.answers": [{"typeName": "A", "value": "1.2.3.4"}]})) == []


def test_events_pulls_identity_after_flows(monkeypatch):
    """End to end through `events()`: a DHCP ack and a DNS response reach the stream as
    IdentityEvents alongside the flow half from `/api/connections`."""
    from aximprint.events import IdentityEvent

    _events_stub(monkeypatch, {
        "dhcp": [{"tsSec": int(T0 + 1), "tsNsec": 0, "dhcp.client_mac": "3c:9c:0f:eb:6c:70",
                  "dhcp.your_ip": "10.80.20.191", "dhcp.hostname": "ENG-AD-CO-LTW11"}],
        "dns": [{"tsSec": int(T0 + 2), "tsNsec": 0, "dns.flag_qr": 1, "dns.query": "dc.corp.lab",
                 "dns.answers": [{"typeName": "A", "value": "10.80.20.4", "ttl": 300}]}],
    })
    idents = [e for e in get_plugin("axquery").events(BASE, T0, T0 + 100)
              if isinstance(e, IdentityEvent)]
    assert sorted(e.kind for e in idents) == ["dhcp", "dns"]
    assert {e.kind: e.ip for e in idents} == {"dhcp": "10.80.20.191", "dns": "10.80.20.4"}


def test_flows_and_identity_are_merged_by_ts_not_concatenated(monkeypatch):
    """The zeek plugin's k-way `heapq.merge`, one transport over. Concatenating — all flows, THEN
    all identity — made every identity event arrive hours behind the clock the flow stream had
    advanced, so the windower dumped them ALL into the final window (measured 2026-09-08). The
    merged stream must be globally non-decreasing in ts, so each event lands in its OWN window."""
    from aximprint.events import IdentityEvent

    def handler(path, p):
        if path == "/api/events":
            if p.get("type") == "dhcp":
                return {"events": [{"tsSec": int(T0 + 50), "dhcp.client_mac": "aa:bb:cc:dd:ee:01",
                                    "dhcp.your_ip": "10.0.0.5", "dhcp.hostname": "h1"},
                                   {"tsSec": int(T0 + 150), "dhcp.client_mac": "aa:bb:cc:dd:ee:02",
                                    "dhcp.your_ip": "10.0.0.6", "dhcp.hostname": "h2"}],
                        "coverage": {"complete": True}}
            return {"events": [], "coverage": {"complete": True}}
        return {"connections": [_conn(T0), _conn(T0 + 100), _conn(T0 + 200)],
                "coverage": {"complete": True}}

    _stub(monkeypatch, handler)
    evs = list(get_plugin("axquery").events(BASE, T0, T0 + 300))
    ts = [e.ts for e in evs]
    assert ts == sorted(ts), f"merged stream must be ts-ordered, got {ts}"
    # the two DHCP identity events are INTERLEAVED among the flows, not appended after them
    id_ts = sorted(e.ts for e in evs if isinstance(e, IdentityEvent))
    assert id_ts == [T0 + 50, T0 + 150]
    assert 0 < ts.index(T0 + 50) < ts.index(T0 + 200) == len(ts) - 1


def test_api_events_is_read_for_identity_not_declined():
    """`/api/events` was declined at build (double-count of the flow half) and REVISITED
    2026-09-08 for the APP-LAYER identity half only; the address-less user census stays declined."""
    from aximprint.ingest.axquery.plugin import AxQueryPlugin

    assert "/api/events" in AxQueryPlugin._ENDPOINTS
    assert "/api/events" not in AxQueryPlugin._DECLINED
    assert "/api/identity/users" in AxQueryPlugin._DECLINED
