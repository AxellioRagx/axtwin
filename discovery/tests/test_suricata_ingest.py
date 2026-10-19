"""Streaming Suricata ingest: no-fall-through parser table, flow-state mapping,
census timing, and the k-way merge across rotated eve.json files.

Synthetic fixtures only — a real capture carries PII and never enters a test
(`LES-003`). Shapes here are this build's best-effort reconstruction of Suricata's
documented eve.json schema (see `ingest/suricata/plugin.py`'s own module docstring for
the confidence caveat), not a real capture's own bytes.
"""
from __future__ import annotations

import gzip
import json

import pytest

from aximprint.events import (
    FLOW_ACCEPTED, AlertEvent, FlowEvent, IdentityEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.ingest.base import IngestPlugin
from aximprint.ingest.suricata import plugin as sp
from aximprint.ingest.suricata import sources as ss
from aximprint.windowing.entities import IDENTITY_SCOPES
from aximprint.windowing.windows import SERVICE_PROJECTIONS, WindowManager

T0 = 1_782_036_000  # 2026-06-21T10:00:00+0000, exactly — matches _iso(T0) below


#: A made-up event_type guaranteed never to be a real Suricata one — the reorder/
#: no-fall-through fixtures' "something outside the known set" (mirrors
#: `tests/test_zeek_ingest.py::_UNKNOWN_STEM`, and its own `LES-086` caution: a
#: currently-true "Suricata doesn't emit this" fact is a landmine if this build's own
#: parity table ever widens to include it, so the value is deliberately unpronounceable
#: as a real protocol name).
_UNKNOWN_TYPE = "__not_a_suricata_event_type__"


def _iso(epoch: float) -> str:
    """Epoch seconds -> the exact ISO-8601 shape `parse_suricata_timestamp` accepts
    (`+0000`, no colon) — the fixture-side inverse of the function under test, so a
    fixture builder can never silently drift from what the parser actually expects.
    """
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+0000"


def _eve(path, rows) -> None:
    """Write a deterministic JSON-lines eve.json fixture."""
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _flow_row(ts=T0, src_ip="10.0.0.5", dst_ip="93.184.216.34", src_port=51820,
             dst_port=443, proto="TCP", app_proto="tls", pkts_out=12, pkts_in=10,
             bytes_out=1500, bytes_in=8000, flow_state="closed", tcp_flags_ts="1b",
             tcp_flags_tc="1a", extra_flow=None, tcp=True) -> dict:
    flow = {"pkts_toserver": pkts_out, "pkts_toclient": pkts_in,
           "bytes_toserver": bytes_out, "bytes_toclient": bytes_in,
           "start": _iso(ts - 2), "end": _iso(ts), "state": flow_state,
           "reason": "timeout"}
    if extra_flow:
        flow.update(extra_flow)
    row = {"timestamp": _iso(ts), "event_type": "flow", "src_ip": src_ip,
          "src_port": src_port, "dest_ip": dst_ip, "dest_port": dst_port,
          "proto": proto, "app_proto": app_proto, "flow": flow}
    if tcp:
        row["tcp"] = {"state": flow_state, "tcp_flags_ts": tcp_flags_ts,
                      "tcp_flags_tc": tcp_flags_tc}
    return row


def _plugin(quiet=True) -> sp.SuricataPlugin:
    p = sp.SuricataPlugin()
    if quiet:
        p.progress_rows = 0
    return p


# -- ingest-plugin CONTRACT ------------------------------------------------------

def test_suricata_plugin_events_matches_the_widened_base_contract():
    """`SuricataPlugin.events` and `IngestPlugin.events` must accept the identical call
    shape (mirrors `tests/test_zeek_ingest.py`'s identical check for `ZeekPlugin`)."""
    import inspect

    base_params = inspect.signature(IngestPlugin.events).parameters
    suricata_params = inspect.signature(sp.SuricataPlugin.events).parameters
    assert list(base_params) == list(suricata_params)


def test_registered_under_the_suricata_name():
    from aximprint.ingest import get_plugin

    assert isinstance(get_plugin("suricata"), sp.SuricataPlugin)


def test_from_to_are_refused_for_a_bare_eve_json_source(tmp_path):
    """Mirrors `ingest/zeek/sources.py::open_source`'s identical refusal for a log
    directory: no partition index exists to slice on, so a silently-ignored bound would
    make a run look like it honored a range it never applied."""
    _eve(tmp_path / "eve.json", [_flow_row()])
    with pytest.raises(ValueError, match="partition index"):
        list(_plugin().events(str(tmp_path), ts_from=1.0))


# -- the parser table, as ratified (coordinator's parity ruling) ----------------

_RATIFIED_PARSERS = {
    "flow", "alert", "stats", "anomaly", "dns", "dhcp", "krb5", "arp",
    "tls", "http", "ssh", "smb", "snmp", "sip", "smtp", "dcerpc", "quic", "ldap",
}
_RATIFIED_SKIPS = {
    "netflow", "drop", "fileinfo", "files", "frame", "packet", "ftp", "ftp_data",
    "rdp", "nfs", "tftp", "ike", "mqtt", "rfb", "pgsql", "http2", "websocket",
    "bittorrent-dht", "dnp3", "modbus", "enip", "mdns", "llmnr", "pop3", "doh2",
}

#: Suricata's OWN `eve-log: types:` list, 35 entries, transcribed from
#: `suricata.yaml.in` (OISF master) alongside the `etc/schema.json` audit that found
#: four of them named in NEITHER of this plugin's tables. This is the census the
#: no-fall-through discipline actually owes (`Q24`'s "every stem accounted for BY
#: NAME", applied to `event_type`): a type in neither table is not declined, it is
#: unnoticed.
_EVE_LOG_TYPES = {
    "alert", "frame", "anomaly", "http", "dns", "mdns", "tls", "files", "smtp",
    "websocket", "ftp", "rdp", "nfs", "smb", "tftp", "ike", "dcerpc", "krb5",
    "bittorrent-dht", "snmp", "rfb", "sip", "quic", "ldap", "pop3", "llmnr", "arp",
    "dhcp", "ssh", "mqtt", "http2", "doh2", "pgsql", "stats", "flow",
}


def test_parser_table_matches_the_ratified_parity_list():
    assert set(sp._PARSERS) == _RATIFIED_PARSERS


def test_skip_table_names_every_ratified_decline():
    assert _RATIFIED_SKIPS <= set(sp._SKIP)


def test_parsers_and_skip_are_disjoint():
    """The import-time `assert` in `plugin.py` already enforces this; re-checked here
    so a regression fails a named pytest, not only an import-time AssertionError with
    no fixture context."""
    assert not (set(sp._PARSERS) & set(sp._SKIP))


def test_every_eve_log_type_is_named_in_one_table_or_the_other():
    """`Q24`'s discipline applied to `event_type`: a type in NEITHER table is not a
    decision, it is an omission — and omission is exactly how `mdns`/`llmnr`/`pop3`/
    `doh2` went unnoticed through the first landing (`SCHEMA_FINDINGS` Defect 6) while
    both tables looked complete."""
    named = set(sp._PARSERS) | set(sp._SKIP)
    assert _EVE_LOG_TYPES <= named, (
        f"unnamed eve-log type(s): {sorted(_EVE_LOG_TYPES - named)}")


def test_arp_is_parsed_not_declined():
    """The FIRST landing declined `arp` on a stated-as-verified premise ("vanilla
    Suricata emits no `arp` event_type") that the schema contradicts — and it is the
    single highest-value record type here for the entity resolver, since mac<->ip is
    the host-scoped evidence `Q1`'s D1 makes the only mergeable class. Pinned as a
    parser, with the decline's own reason gone, so the false premise cannot return by
    someone re-reading the old comment."""
    assert "arp" in sp._PARSERS
    assert "arp" not in sp._SKIP


def test_unrecognized_event_type_is_reported_not_dropped(tmp_path):
    """A made-up event_type produces no events but is not silently dropped — the
    no-fall-through discipline (`_PARSERS`/`_SKIP`, task brief) applied to
    `event_type` the way Zeek's own table applies it to a log stem."""
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0), "event_type": _UNKNOWN_TYPE, "foo": "bar"},
        _flow_row(ts=T0 + 1),
    ])
    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert len(events) == 1  # only the flow row produced an event
    assert p.unparsed_stems == {_UNKNOWN_TYPE: 1}
    assert p.unrecognized_stems == {_UNKNOWN_TYPE: 1}


def test_declared_skip_is_counted_in_unparsed_but_not_unrecognized(tmp_path):
    skip_type = sorted(sp._SKIP)[0]
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0), "event_type": skip_type},
        _flow_row(ts=T0 + 1),
    ])
    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {skip_type: 1}
    assert p.unrecognized_stems == {}


# -- the census timing contract: None-until-exhausted (weaker than Zeek's, disclosed) --

def test_unparsed_stems_is_none_until_events_is_iterated(tmp_path):
    _eve(tmp_path / "eve.json", [_flow_row()])
    p = sp.SuricataPlugin()
    p.events(str(tmp_path))  # constructed, never driven
    assert p.unparsed_stems is None
    assert p.unrecognized_stems is None


def test_unparsed_stems_stays_none_after_one_event_pulled_mid_stream(tmp_path):
    """The deliberate departure from `ingest/zeek/plugin.py`'s pre-first-yield
    guarantee (`LES-106`): `event_type` lives inside a row, not a file name, so a
    genuine census cannot be final after only one event when more rows remain
    unread. Must read `None` (honest: not yet audited), never a premature `{}` or a
    partial dict that would look final but is not (`LES-006`)."""
    _eve(tmp_path / "eve.json", [
        _flow_row(ts=T0),
        {"timestamp": _iso(T0 + 1), "event_type": _UNKNOWN_TYPE},
        _flow_row(ts=T0 + 2),
    ])
    p = _plugin()
    gen = p.events(str(tmp_path))
    next(gen)  # exactly one event pulled
    assert p.unparsed_stems is None, (
        "must stay unaudited mid-stream — the unknown row has not been read yet")
    list(gen)  # drain the rest
    assert p.unparsed_stems == {_UNKNOWN_TYPE: 1}, "final once the source is exhausted"


def test_unparsed_stems_is_empty_dict_once_drained_and_every_type_is_known(tmp_path):
    """`{}`, not `None`: audited, and nothing was left over — reachable here (unlike
    Zeek's Parquet archive transport, task brief), because one eve.json stream CAN take
    a genuine census of its own event types once fully read."""
    _eve(tmp_path / "eve.json", [_flow_row(), {"timestamp": _iso(T0 + 1),
                                               "event_type": "alert",
                                               "src_ip": "10.0.0.5",
                                               "dest_ip": "10.0.0.9",
                                               "alert": {"signature": "x"}}])
    p = _plugin()
    assert list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {}
    assert p.unrecognized_stems == {}


def test_unparsed_stems_is_final_at_the_point_the_source_generator_is_exhausted(tmp_path):
    """Mirrors `cli._pipeline`'s own reliance (`WindowManager.process`'s `for` loop
    over the plugin's `events()` cannot complete without asking for one more item and
    receiving `StopIteration`) — confirmed directly here rather than only through the
    weaker per-`next()` guarantee the test above checks."""
    _eve(tmp_path / "eve.json", [
        _flow_row(ts=T0), {"timestamp": _iso(T0 + 1), "event_type": _UNKNOWN_TYPE},
    ])
    p = _plugin()
    wm = WindowManager(window_seconds=3600)
    list(wm.process(p.events(str(tmp_path))))
    assert p.unparsed_stems == {_UNKNOWN_TYPE: 1}, (
        "the driver's own for-loop protocol must have exhausted the generator")


def test_unparsed_stems_resets_across_repeated_events_calls(tmp_path):
    _eve(tmp_path / "eve.json", [
        _flow_row(), {"timestamp": _iso(T0 + 1), "event_type": _UNKNOWN_TYPE},
    ])
    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {_UNKNOWN_TYPE: 1}
    _eve(tmp_path / "eve.json", [_flow_row()])
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {}, "must not still carry the first call's event_type"


# -- flow -------------------------------------------------------------------------

def test_flow_maps_core_fields():
    ev = next(iter(sp._flow(_flow_row())))
    assert isinstance(ev, FlowEvent)
    assert (ev.src_ip, ev.dst_ip, ev.src_port, ev.dst_port) == (
        "10.0.0.5", "93.184.216.34", 51820, 443)
    assert ev.proto == "tcp"          # lowercased from Suricata's "TCP"
    assert ev.service == "tls"        # app_proto, verbatim
    assert ev.bytes_out == 1500 and ev.bytes_in == 8000
    assert ev.pkts_out == 12 and ev.pkts_in == 10
    assert ev.originator_is_src is True
    assert ev.local_src is None and ev.local_dst is None, (
        "Suricata has no local_nets equivalent; imputing here would be "
        "windowing/segments.py's job, not this parser's")
    assert ev.duration == pytest.approx(2.0)


@pytest.mark.parametrize("service", ["failed", "unknown", ""])
def test_flow_service_field_is_kept_verbatim_including_sentinel_looking_values(service):
    """`"failed"`/`"unknown"` are real, distinct Suricata claims (attempted-and-failed
    detection vs never-attempted), not a marker this parser gets to fold into blank."""
    ev = next(iter(sp._flow(_flow_row(app_proto=service))))
    assert ev.service == service


def test_flow_state_class_established_when_responder_answers_with_no_rst():
    ev = next(iter(sp._flow(_flow_row(tcp_flags_ts="1b", tcp_flags_tc="1a"))))
    assert ev.state_class == "established"
    assert ev.state == "closed"  # verbatim tcp.state


def test_flow_state_class_attempted_when_responder_never_answers():
    ev = next(iter(sp._flow(_flow_row(pkts_in=0, bytes_in=0, flow_state="new",
                                      tcp_flags_ts="02", tcp_flags_tc=""))))
    assert ev.state_class == "attempted"
    # `_ZERO_IS_HONEST`: a genuinely one-sided flow's zero to-client counters are the
    # OBSERVATION, not an imputation.
    assert ev.counters_imputed is False


def test_flow_state_class_reset_orig_when_originator_sends_rst_after_responder_answers():
    ev = next(iter(sp._flow(_flow_row(tcp_flags_ts="06", tcp_flags_tc="12"))))
    assert ev.state_class == "reset_orig"


def test_flow_state_class_reset_resp_when_responder_syned_then_reset():
    """`reset_resp` means ESTABLISHED and then aborted by the responder, so the
    responder's own accumulated flags must carry a SYN (0x02) as well as the RST
    (0x04) — 0x16 here is SYN|RST|ACK. Without the SYN this is a refusal, not a reset,
    which is the distinction `M2` added and the test below pins from the other side."""
    ev = next(iter(sp._flow(_flow_row(tcp_flags_ts="1a", tcp_flags_tc="16"))))
    assert ev.state_class == "reset_resp"
    assert ev.state_class in FLOW_ACCEPTED  # the responder did accept, then aborted


def test_flow_state_class_unknown_when_pkts_toclient_is_unreadable():
    """An unmappable/unreadable state must go to `unknown`, never a bucketed guess
    (task brief; `events.FLOW_STATE_CLASSES`'s own closed vocabulary)."""
    row = _flow_row()
    del row["flow"]["pkts_toclient"]
    ev = next(iter(sp._flow(row)))
    assert ev.state_class == "unknown"
    assert ev.state == ""


def test_flow_state_class_established_for_non_tcp_when_responder_answers():
    ev = next(iter(sp._flow(_flow_row(proto="UDP", app_proto="dns", tcp=False,
                                      flow_state="established"))))
    assert ev.state_class == "established"
    assert ev.state == "established"  # falls back to flow.state; no tcp object at all


def test_flow_counters_imputed_when_a_counter_is_genuinely_unset():
    row = _flow_row()
    del row["flow"]["bytes_toclient"]
    ev = next(iter(sp._flow(row)))
    assert ev.counters_imputed is True


def test_flow_duration_falls_back_to_age_when_start_end_do_not_parse():
    row = _flow_row(extra_flow={"start": "not-a-timestamp", "end": "also-not",
                                "age": 7})
    ev = next(iter(sp._flow(row)))
    assert ev.duration == 7.0


def test_flow_duration_none_and_imputed_when_neither_form_is_available():
    row = _flow_row(extra_flow={"start": "x", "end": "y"})
    row["flow"].pop("age", None)
    ev = next(iter(sp._flow(row)))
    assert ev.duration == 0.0
    assert ev.counters_imputed is True


def test_flow_drops_rows_with_no_usable_endpoint():
    row = _flow_row()
    row["src_ip"] = ""
    assert list(sp._flow(row)) == []


def test_flow_drops_rows_only_when_BOTH_ts_sources_are_unparseable():
    """`M4`: `flow.start` is the key and `timestamp` is the fallback, so a row is
    dropped only when neither parses. A row with a broken `timestamp` and a good
    `flow.start` is a perfectly good flow — dropping it would discard real traffic over
    a field this parser no longer keys on."""
    row = _flow_row()
    row["timestamp"] = "not-a-timestamp"
    assert len(list(sp._flow(row))) == 1
    row["flow"]["start"] = "also-not-a-timestamp"
    assert list(sp._flow(row)) == []


# -- alert / anomaly / stats -------------------------------------------------------

def test_alert_maps_to_alert_event():
    row = {"timestamp": _iso(T0), "event_type": "alert", "src_ip": "10.0.0.5",
          "dest_ip": "93.184.216.34",
          "alert": {"category": "Potentially Bad Traffic", "signature": "GPL TEST",
                    "signature_id": 2100498}}
    ev = next(iter(sp._alert(row)))
    assert isinstance(ev, AlertEvent)
    assert (ev.ip, ev.peer_ip) == ("10.0.0.5", "93.184.216.34")
    assert ev.category == "Potentially Bad Traffic"
    assert ev.detail == "GPL TEST"


def test_anomaly_maps_to_alert_event_with_weird_category():
    """The direct analogue of Zeek's `weird.log` — reuses the FIXED `"weird"` category
    so a store consumer can group both sensors' protocol-oddity signals under one label."""
    row = {"timestamp": _iso(T0), "event_type": "anomaly", "src_ip": "10.0.0.5",
          "dest_ip": "10.0.0.9", "anomaly": {"event": "applayer_wrong_direction"}}
    ev = next(iter(sp._anomaly(row)))
    assert isinstance(ev, AlertEvent)
    assert ev.category == "weird"
    assert ev.detail == "applayer_wrong_direction"


def test_stats_emits_nothing_even_when_every_counter_is_present():
    """`S5`, and it REVERSES this plugin's first landing, which mapped
    `stats.capture.kernel_drops` onto kind `pkts_dropped`. The two halves of that
    landing contradicted each other: `pkts_proc`/`bytes_recv` were declined because this
    build cannot tell a per-interval delta from a cumulative total, while `kernel_drops`
    was admitted under the same unverified premise on the grounds that its `max` reducer
    survives either reading — true of the REDUCER, false of the KIND, since `max` of a
    cumulative counter is "drops since engine start", a different quantity published
    under a name that means something else. Both halves are declined now, by name, with
    the measurement that would settle them named in `_stats`."""
    row = {"timestamp": _iso(T0), "event_type": "stats",
          "stats": {"capture": {"kernel_packets": 1000, "kernel_drops": 5,
                                "kernel_ifdrops": 2},
                    "decoder": {"pkts": 1000, "bytes": 500_000}}}
    assert list(sp._stats(row)) == []


def test_a_parser_that_emits_nothing_by_design_says_so_by_name():
    """The guard that keeps `S5`'s honest empty from training a reader to ignore
    `_summary`'s "PRODUCED NO EVENTS ... parser and record shape likely disagree"
    warning — which exists to catch exactly the silent field-spelling failure this
    round found six of."""
    assert "stats" in sp._EMITS_NOTHING_BY_DESIGN
    assert "stats" in sp._PARSERS, "still parsed by name; it just publishes nothing"


def test_stats_emits_nothing_when_the_stats_object_is_absent_too():
    assert list(sp._stats({"timestamp": _iso(T0), "event_type": "stats"})) == []


# -- identity: dns / dhcp / krb5 ---------------------------------------------------

def test_dns_v2_nested_answers_shape():
    row = {"timestamp": _iso(T0), "event_type": "dns",
          "dns": {"type": "answer", "rrname": "example.com",
                  "answers": [{"rrname": "example.com", "rrtype": "A", "ttl": 300,
                              "rdata": "93.184.216.34"},
                             {"rrname": "example.com", "rrtype": "CNAME", "ttl": 60,
                              "rdata": "cdn.example.net"}]}}
    events = list(sp._dns(row))
    assert len(events) == 1, "the CNAME answer names nothing the v1 resolver can key on"
    ev = events[0]
    assert isinstance(ev, IdentityEvent)
    assert ev.kind == "dns"
    assert ev.ip == "93.184.216.34"
    assert ev.hostname == "example.com"
    assert ev.record_ttl == 300.0


def test_dns_v1_flat_answer_shape():
    row = {"timestamp": _iso(T0), "event_type": "dns",
          "dns": {"type": "answer", "rrname": "example.com", "rrtype": "A",
                  "rdata": "93.184.216.34", "ttl": 120}}
    events = list(sp._dns(row))
    assert len(events) == 1
    assert events[0].ip == "93.184.216.34" and events[0].record_ttl == 120.0


def test_dns_query_only_row_binds_nothing():
    row = {"timestamp": _iso(T0), "event_type": "dns",
          "dns": {"type": "query", "rrname": "example.com"}}
    assert list(sp._dns(row)) == []


def test_dhcp_maps_to_identity_event():
    row = {"timestamp": _iso(T0), "event_type": "dhcp",
          "dhcp": {"assigned_ip": "10.0.0.50", "client_mac": "aa:bb:cc:dd:ee:ff",
                   "hostname": "host1", "lease_time": 3600}}
    ev = next(iter(sp._dhcp(row)))
    assert isinstance(ev, IdentityEvent)
    assert (ev.kind, ev.ip, ev.mac, ev.hostname) == (
        "dhcp", "10.0.0.50", "aa:bb:cc:dd:ee:ff", "host1")
    assert ev.lease_seconds == 3600.0


def test_dhcp_requires_both_ip_and_mac():
    row = {"timestamp": _iso(T0), "event_type": "dhcp", "dhcp": {"client_mac": "aa:bb"}}
    assert list(sp._dhcp(row)) == []


def _krb5_event(cname, realm="CORP.EXAMPLE"):
    row = {"timestamp": _iso(T0), "event_type": "krb5", "src_ip": "10.0.0.5",
          "krb5": {"cname": cname, "realm": realm}}
    return next(iter(sp._krb5(row)))


def test_krb5_machine_account_is_classified_HOST_SCOPED_by_the_consumer():
    """`M1`, and the assertion is deliberately on the CONSUMER's classification rather
    than on `IdentityEvent.principal`'s own string — asserting the string is exactly how
    this shipped broken.

    `windowing.entities.kerberos_identifier` parses an RFC 4120 principal as
    `component[/component...]` with the LAST component the realm. Composed with `@`
    (this parser's first landing, copying how Zeek PRINTS the joined value) it saw one
    component, `"ws000$@CORP.EXAMPLE"`, whose primary does not end in `$` — so every
    machine account resolved as a USER principal, which anchors no host (D1), and every
    Kerberos host anchor on the Suricata path was silently discarded while the event's
    own field looked right."""
    ident = IDENTITY_SCOPES["kerberos"](_krb5_event("ws000$"))
    assert len(ident) == 1
    assert ident[0].scope == "host", (
        "a `$`-suffixed cname is an AD computer account and MUST anchor a host; "
        f"got scope={ident[0].scope!r} from {ident[0]!r}")
    assert ident[0].value == "ws000"


def test_krb5_user_principal_stays_user_scoped():
    """The other half of the same parse: a user principal names a PERSON and must not
    merge two addresses (D1). A fix that made everything host-scoped would pass the test
    above and be worse than the bug."""
    ident = IDENTITY_SCOPES["kerberos"](_krb5_event("alice"))
    assert ident[0].scope == "user"


def test_krb5_host_service_principal_is_host_scoped():
    """`host/fqdn@REALM` — the third branch of `kerberos_identifier`'s own parse, which
    the `@` separator also made unreachable from this plugin."""
    ident = IDENTITY_SCOPES["kerberos"](_krb5_event("host/dc01.corp.example"))
    assert ident[0].scope == "host"
    assert ident[0].value == "dc01"


def test_krb5_without_realm_uses_bare_cname():
    row = {"timestamp": _iso(T0), "event_type": "krb5", "src_ip": "10.0.0.5",
          "krb5": {"cname": "alice"}}
    ev = next(iter(sp._krb5(row)))
    assert ev.principal == "alice"


# -- responder-keyed ServiceEvents: kind reuse + the downstream-vocabulary claim --

def test_tls_maps_to_ssl_kind_with_projected_fields():
    row = {"timestamp": _iso(T0), "event_type": "tls", "dest_ip": "93.184.216.34",
          "dest_port": 443,
          "tls": {"sni": "example.com", "version": "TLS 1.3",
                  "ja3s": {"hash": "deadbeef"}, "subject": "CN=example.com"}}
    ev = next(iter(sp._tls(row)))
    assert isinstance(ev, ServiceEvent)
    assert ev.kind == "ssl"  # Suricata's own event_type is "tls"; the KIND is Zeek's
    assert (ev.ip, ev.port) == ("93.184.216.34", 443)
    for key in SERVICE_PROJECTIONS["ssl"][0]:
        assert key in ev.attrs, f"{key} required by SERVICE_PROJECTIONS['ssl']"


@pytest.mark.parametrize("event_type,kind,row_extra,required_ok", [
    ("http", "http", {"http": {"hostname": "example.com", "url": "/",
                               "http_method": "GET"}}, True),
    ("ssh", "ssh", {"ssh": {"server": {"software_version": "OpenSSH_9.0"}}}, True),
    ("smb", "smb", {"smb": {"share_type": "FILE", "share": "shared"}}, True),
    ("snmp", "snmp", {"snmp": {"version": "2c", "community": "public"}}, True),
    ("quic", "quic", {"quic": {"sni": "example.com", "version": "1"}}, True),
])
def test_responder_keyed_service_kinds_reuse_the_zeek_vocabulary(
        event_type, kind, row_extra, required_ok):
    row = {"timestamp": _iso(T0), "event_type": event_type,
          "dest_ip": "93.184.216.34", "dest_port": 443, **row_extra}
    parse = sp._PARSERS[event_type]
    ev = next(iter(parse(row)))
    assert isinstance(ev, ServiceEvent)
    assert ev.kind == kind
    assert ev.ip == "93.184.216.34" and ev.port == 443
    if kind in SERVICE_PROJECTIONS:
        declared = SERVICE_PROJECTIONS[kind][0]
        assert any(k in ev.attrs for k in declared), (
            f"{kind}: none of {declared} present — SERVICE_PROJECTIONS would decline "
            f"to project this event at all")


def test_dcerpc_maps_endpoint_and_operation_from_uuid_and_opnum():
    row = {"timestamp": _iso(T0), "event_type": "dcerpc", "dest_ip": "10.0.0.9",
          "dest_port": 135,
          "dcerpc": {"opnum": 3, "request": "REQUEST",
                    "interfaces": [{"uuid": "e1af8308-5d1f-11c9-91a4-08002b14a0fa"}]}}
    ev = next(iter(sp._dcerpc(row)))
    assert ev.kind == "dce_rpc"
    assert ev.attrs["endpoint"] == "e1af8308-5d1f-11c9-91a4-08002b14a0fa"
    assert ev.attrs["operation"] == "3"
    declared = SERVICE_PROJECTIONS["dce_rpc"][0]
    assert any(k in ev.attrs for k in declared)


def test_ldap_maps_to_ldap_op_kind_not_ldap():
    """Coordinator ruling: Suricata's single `ldap` event_type maps to kind `ldap_op`,
    NOT `ldap` — Zeek's `ldap_search.log` already owns `"ldap"` for a different stem,
    and `SERVICE_PROJECTIONS` is keyed by kind (`ingest/zeek/plugin.py::_ldap`'s own
    reasoning, reused verbatim)."""
    row = {"timestamp": _iso(T0), "event_type": "ldap", "dest_ip": "10.0.0.9",
          "dest_port": 389,
          "ldap": {"request": {"operation": "bindRequest", "message_id": 1}}}
    ev = next(iter(sp._ldap(row)))
    assert ev.kind == "ldap_op"
    assert ev.attrs["opcode"] == "bindRequest"
    declared = SERVICE_PROJECTIONS["ldap_op"][0]
    assert any(k in ev.attrs for k in declared)


# -- the R1 keying check: sip/smtp collect client fields but never misattribute them --

def test_smtp_is_keyed_on_the_responder_and_has_no_projection_entry():
    """`_SERVICE_NOT_PROJECTED["smtp"]` already declines this kind on the Zeek side for
    exactly the reason this test names (`R1`/`LES-096`): `helo` is the CLIENT's own
    announcement. Confirms this build's `_smtp` still keys the EVENT at the responder
    (correct — "a server exists here" is the fact smtp IS admitted for) while relying
    on the untouched, existing decline to keep the client field from ever being
    published as a responder property."""
    from aximprint.windowing.windows import _SERVICE_NOT_PROJECTED

    row = {"timestamp": _iso(T0), "event_type": "smtp", "dest_ip": "10.0.0.25",
          "dest_port": 25,
          "smtp": {"helo": "attacker-client.example", "mail_from": "a@b.test"}}
    ev = next(iter(sp._smtp(row)))
    assert ev.kind == "smtp"
    assert ev.ip == "10.0.0.25", "keyed on the SERVER, matching Zeek's own smtp keying"
    assert ev.attrs["helo"] == "attacker-client.example"  # collected, forensic completeness
    assert "smtp" not in SERVICE_PROJECTIONS
    assert "smtp" in _SERVICE_NOT_PROJECTED, (
        "the R1 decline must still be in place, or this event's client-authored "
        "attrs would be published as if they described the responder")


def test_sip_is_keyed_on_the_responder_and_has_no_projection_entry():
    from aximprint.windowing.windows import _SERVICE_NOT_PROJECTED

    row = {"timestamp": _iso(T0), "event_type": "sip", "dest_ip": "10.0.0.30",
          "dest_port": 5060,
          "sip": {"method": "INVITE", "uri": "sip:1000@corp.example",
                  "user_agent": "friendly-scanner"}}
    ev = next(iter(sp._sip(row)))
    assert ev.ip == "10.0.0.30"
    assert ev.attrs["method"] == "INVITE"
    # `SCHEMA_FINDINGS` Defect 3: `sip.user_agent` does not exist in Suricata's schema
    # at all (`sip` is `additionalProperties: false` with seven named keys). The mapping
    # was removed rather than replaced with a guess — so even a row that DOES carry the
    # key must not produce the attr, or the fix is only a rename of the same invention.
    assert "user_agent" not in ev.attrs
    assert "sip" not in SERVICE_PROJECTIONS
    assert "sip" in _SERVICE_NOT_PROJECTED


# -- end-to-end: the downstream-vocabulary claim, through the real WindowManager ----

def test_ssl_kind_is_projected_end_to_end_through_windowing():
    """The whole point of reusing Zeek's kind vocabulary (module docstring): a
    Suricata-sourced `ServiceEvent(kind="ssl")` must reach a node's `services["ssl"]`
    through the UNMODIFIED `windowing.windows.SERVICE_PROJECTIONS` table, with no
    Suricata-specific code in `windowing` at all.
    """
    row = {"timestamp": _iso(T0), "event_type": "tls", "dest_ip": "93.184.216.34",
          "dest_port": 443,
          "tls": {"sni": "example.com", "version": "TLS 1.3", "ja3s": {"hash": "de"}}}
    events = list(sp._tls(row))
    wm = WindowManager(window_seconds=3600)
    results = list(wm.process(events)) + list(wm.finalize())
    assert len(results) == 1
    node = results[0].nodes["93.184.216.34"]
    assert node["services"]["ssl"] == ["example.com|TLS 1.3|de"]


# -- transport: discovery, rotation, gzip, malformed lines -------------------------

def test_bare_file_locator_is_read_directly(tmp_path):
    f = tmp_path / "custom-name.json"
    _eve(f, [_flow_row()])
    assert len(list(_plugin().events(str(f)))) == 1


def test_directory_with_no_eve_file_is_refused(tmp_path):
    (tmp_path / "unrelated.json").write_text("{}\n")
    with pytest.raises(ss.SuricataSourceError, match="no eve.json"):
        list(_plugin().events(str(tmp_path)))


def test_missing_source_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(_plugin().events(str(tmp_path / "does-not-exist")))


def _anomaly_row(ts, event):
    """An `anomaly` row — the fixture shape the rotation/merge tests key on. Was
    `stats` until `S5` declined every stats counter and that parser stopped emitting;
    a merge-ordering test needs a parser that actually yields, and `anomaly` carries a
    per-row `detail` string this can assert an exact emission ORDER on."""
    return {"timestamp": _iso(ts), "event_type": "anomaly", "src_ip": "10.0.0.5",
            "dest_ip": "10.0.0.9", "anomaly": {"event": event}}


def test_rotated_and_gzipped_siblings_are_discovered_and_merged_in_ts_order(tmp_path):
    _eve(tmp_path / "eve.json.1", [_anomaly_row(T0, "a"), _anomaly_row(T0 + 5, "b")])
    with gzip.open(tmp_path / "eve.json.2.gz", "wt") as fh:
        fh.write(json.dumps(_anomaly_row(T0 + 2, "c")) + "\n")
        fh.write(json.dumps(_anomaly_row(T0 + 10, "d")) + "\n")
    (tmp_path / "unrelated.log").write_text("not eve\n")

    events = list(_plugin().events(str(tmp_path)))
    ts_values = [e.ts for e in events]
    assert ts_values == sorted(ts_values), "must be time-ordered across rotated files"
    assert [e.detail for e in events] == ["a", "c", "b", "d"]


def test_malformed_json_line_is_skipped_and_counted(tmp_path):
    path = tmp_path / "eve.json"
    path.write_text(json.dumps(_flow_row()) + "\nnot json at all\n")
    p = _plugin()
    events = list(p.events(str(path)))
    assert len(events) == 1
    assert p.log_stats["flow"].rows == 1  # the malformed line never reached a parser


def test_non_object_json_line_is_skipped_and_counted(tmp_path):
    path = tmp_path / "eve.json"
    path.write_text(json.dumps(_flow_row()) + "\n" + json.dumps([1, 2, 3]) + "\n")
    events = list(_plugin().events(str(path)))
    assert len(events) == 1


@pytest.mark.parametrize("ts", ["nan", "inf", "-inf", "not-a-timestamp", ""])
def test_unparseable_timestamp_strings_drop_the_row_rather_than_producing_a_bad_event(
        tmp_path, ts):
    """A structural difference from `ingest/zeek/plugin.py`, worth pinning down rather
    than assuming: Zeek's `_f("nan")` returns a genuine (truthy, non-finite) float,
    which is why that plugin needs `math.isfinite(ev.ts)` as a belt-and-braces
    accumulator guard (`tests/test_zeek_ingest.py::test_non_finite_ts_is_rejected_at_
    the_plugin`). `parse_suricata_timestamp` cannot produce `nan`/`inf` at all — none
    of these strings match `_TS_FORMATS`'s `strptime` patterns, so it returns `None`
    for every one of them, and every parser's own `ts is None: return` gate discards
    the row before an `Event` is ever constructed. Confirmed here via the real ingest
    path (a fixture, not a direct call) so the two layers cannot silently drift apart.
    """
    row = _flow_row()
    row["timestamp"] = ts
    row["flow"]["start"] = ts  # `M4`: both ts sources, or the fallback keeps the row
    path = tmp_path / "eve.json"
    _eve(path, [row])
    assert list(_plugin().events(str(path))) == []


def test_reorder_repairs_disorder_within_the_horizon(tmp_path):
    """Mirrors `tests/test_zeek_ingest.py::test_merge_repairs_within_file_disorder`:
    Suricata can write flow-summary rows out of ts order within one file too (a flow
    logs at CLOSE, keyed on its own START — the identical pathology Zeek's `conn.log`
    has), and the bounded reorder buffer must smooth it."""
    rows = [_flow_row(ts=T0 + t) for t in (0, 30, 10, 40, 5, 60, 50, 20)]
    _eve(tmp_path / "eve.json", rows)
    p = _plugin()
    ts_values = [e.ts for e in p.events(str(tmp_path)) if isinstance(e, FlowEvent)]
    assert ts_values == sorted(ts_values)


def test_disorder_beyond_the_horizon_is_counted_not_hidden(tmp_path):
    rows = [_flow_row(ts=T0 + 100 + i) for i in range(8)] + [_flow_row(ts=T0)]
    _eve(tmp_path / "eve.json", rows)
    p = _plugin()
    p.reorder_rows = 2  # far smaller than the 8-row displacement
    out = [e.ts for e in p.events(str(tmp_path)) if isinstance(e, FlowEvent)]
    assert out != sorted(out), "fixture must actually exceed the lookahead"
    unordered_health = [e for e in p.events(str(tmp_path))
                        if isinstance(e, SensorHealthEvent) and e.kind == "ingest_unordered"]
    assert len(unordered_health) == 1


def test_determinism_two_runs_over_the_same_source_are_identical(tmp_path):
    """`scripts/verify_determinism.py`'s invariant, checked directly: nothing wall
    -clock, unseeded, or set-iteration-ordered may reach an output (`LES-002`)."""
    rows = [_flow_row(ts=T0 + i, src_port=1000 + i) for i in range(15)]
    _eve(tmp_path / "eve.json", rows)
    first = [repr(e) for e in _plugin().events(str(tmp_path))]
    second = [repr(e) for e in _plugin().events(str(tmp_path))]
    assert first == second
    assert len(first) == 15


# ===================================================================================
# The fix round (two independent audits: `SCHEMA_FINDINGS` against Suricata's own
# `etc/schema.json`, and a code review). Every test below would have FAILED before the
# fix it names.
# ===================================================================================

# -- M2: the state classes this plugin declared itself unable to produce -----------

def test_flow_state_class_refused_when_responder_rst_without_ever_synning():
    """`M2`. The first landing's `_flow_state_class` docstring declared this case
    IMPOSSIBLE — "nothing in this shape tells them apart", so a REJ-shaped refusal and
    an established-then-reset flow both landed in `reset_resp`. The accumulated
    responder flag byte does tell them apart: a responder that ACCEPTED necessarily sent
    its own SYN (in SYN/ACK), so RST-without-SYN from the responder is a refusal.

    The consequence of getting this wrong is not cosmetic: `reset_resp` is in
    `FLOW_ACCEPTED` and `refused` is not, so every scanned port on every swept host was
    being published as a real listener (`windowing.windows._add_flow`'s `listens_on`
    gate)."""
    ev = next(iter(sp._flow(_flow_row(tcp_flags_ts="02", tcp_flags_tc="14",
                                      pkts_in=1, bytes_in=40))))
    assert ev.state_class == "refused"
    assert ev.state_class not in FLOW_ACCEPTED, (
        "a refusal proves the host is up, NOT that the port is open")


def test_flow_state_class_partial_when_only_the_responder_was_seen():
    """`M2`. `FLOW_STATE_CLASSES` calls `partial` "only one direction seen — a vantage
    fact, not a host fact", i.e. Zeek's `RSTRH`/`SHR`. Read from the counters, so it
    needs no Suricata state vocabulary. Before this, a responder-only record was
    classified `established` — a claim about the HOSTS derived from a fact about the
    TAP."""
    ev = next(iter(sp._flow(_flow_row(pkts_out=0, bytes_out=0, pkts_in=6,
                                      tcp_flags_ts="", tcp_flags_tc="1a"))))
    assert ev.state_class == "partial"
    assert ev.state_class not in FLOW_ACCEPTED


def test_flow_state_class_other_when_no_handshake_was_observed():
    """`M2`: a midstream flow — Suricata began tracking after the handshake, so neither
    direction's accumulated flags carry a SYN. Zeek's own `OTH`."""
    ev = next(iter(sp._flow(_flow_row(tcp_flags_ts="18", tcp_flags_tc="18"))))
    assert ev.state_class == "other"
    assert ev.state_class not in FLOW_ACCEPTED


def test_flow_state_class_does_not_read_absent_flag_bytes_as_a_missing_handshake():
    """The `LES-006` half of `M2`, and the reason `_tcp_flags` returns `Optional[int]`
    rather than a per-bit bool: a build or config that does not emit `tcp_flags_*` must
    not make every TCP flow look midstream. No flag evidence -> fall through to the
    counter-based reading, not to `other`."""
    row = _flow_row()
    del row["tcp"]["tcp_flags_ts"]
    del row["tcp"]["tcp_flags_tc"]
    assert next(iter(sp._flow(row))).state_class == "established"


def test_refused_flow_with_unset_counters_is_not_marked_imputed():
    """`M2`'s `_ZERO_IS_HONEST` widening, now identical to `ingest/zeek/plugin.py`'s:
    a refused connection exchanged nothing, so an unset byte counter reads as an honest
    zero rather than an imputation. It was narrower here only because `refused` could
    not be produced at all."""
    row = _flow_row(tcp_flags_ts="02", tcp_flags_tc="14", pkts_in=1)
    del row["flow"]["bytes_toclient"]
    ev = next(iter(sp._flow(row)))
    assert ev.state_class == "refused"
    assert ev.counters_imputed is False


# -- M4: a flow is keyed on when it STARTED ----------------------------------------

def test_flow_event_ts_is_flow_start_not_the_record_emission_time():
    """`M4`. Suricata writes a flow record at CLOSE/timeout and stamps `timestamp` with
    THAT moment; `flow.start` is when the conversation began, which is what Zeek's
    `conn.log` `ts` means and what the windower buckets on. Keyed on `timestamp`, a flow
    that began at 10:59 and ended at 11:01 was reported in the 11:00 window by Suricata
    and the 10:00 window by Zeek — one wire, two sensors, two different answers to
    "which hour did this conversation happen in"."""
    row = _flow_row(ts=T0 + 3600)
    row["flow"]["start"] = _iso(T0 + 100)
    ev = next(iter(sp._flow(row)))
    assert ev.ts == float(T0 + 100)


def test_flow_event_ts_falls_back_to_timestamp_when_flow_start_is_unusable():
    row = _flow_row(ts=T0 + 42)
    row["flow"].pop("start")
    assert next(iter(sp._flow(row))).ts == float(T0 + 42)


# -- M3: the census is only published when the source was actually drained ---------

def test_abandoned_events_generator_leaves_the_census_unaudited(tmp_path):
    """`M3`. A `finally` also runs when the CONSUMER walks away — a `break` in the
    driver's loop, an exception, an `islice`, a `next()` in a test. The first landing
    published `unparsed_stems` from that path too, i.e. asserted a complete census
    ("audited, and nothing was left over") over rows nobody had read. `None` is the only
    honest answer for an abandoned run (`LES-006`)."""
    _eve(tmp_path / "eve.json", [
        _flow_row(ts=T0 + i) for i in range(5)
    ] + [{"timestamp": _iso(T0 + 9), "event_type": _UNKNOWN_TYPE}])
    p = _plugin()
    gen = p.events(str(tmp_path))
    next(gen)
    gen.close()  # exactly what a `break` out of the driver's for-loop does
    assert p.unparsed_stems is None, "an abandoned run audited nothing"
    assert p.unrecognized_stems is None


def test_drained_events_generator_still_publishes_the_census(tmp_path):
    _eve(tmp_path / "eve.json", [_flow_row(), {"timestamp": _iso(T0 + 1),
                                               "event_type": _UNKNOWN_TYPE}])
    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {_UNKNOWN_TYPE: 1}


# -- Defect 1: ldap responses[] ------------------------------------------------------

def _ldap_row(ldap):
    return {"timestamp": _iso(T0), "event_type": "ldap", "dest_ip": "10.0.0.9",
            "dest_port": 389, "ldap": ldap}


def test_ldap_result_is_read_from_the_responses_ARRAY():
    """`SCHEMA_FINDINGS` Defect 1: `ldap.response.result_code` does not exist — the
    schema has `request` (object) and `responses` (ARRAY). `_dig` walked to a missing
    key and the `result` attr was never once set, on any row, silently."""
    ev = next(iter(sp._ldap(_ldap_row({
        "request": {"operation": "bind_request", "message_id": 3},
        "responses": [{"operation": "bind_response", "message_id": 3,
                       "bind_response": {"result_code": "success"}}],
    }))))
    assert ev.attrs["result"] == "success"
    assert ev.attrs["opcode"] == "bind_request"


def test_ldap_result_selects_the_response_matching_the_request_message_id():
    """The declared selection rule: LDAP's own correlation field, not "the first
    element", when a record carries several responses."""
    ev = next(iter(sp._ldap(_ldap_row({
        "request": {"operation": "search_request", "message_id": 7},
        "responses": [
            {"operation": "search_result_entry", "message_id": 6,
             "search_result_entry": {"base_object": "dc=corp"}},
            {"operation": "search_result_done", "message_id": 7,
             "search_result_done": {"result_code": "no_such_object"}},
        ],
    }))))
    assert ev.attrs["result"] == "no_such_object"


def test_ldap_result_is_absent_rather_than_empty_when_no_response_carries_one():
    """An intermediate response carries no result at all — schema-verified. The attr is
    then not set, rather than set to `""`, so `_add_service`'s presence gate reads
    "not observed" instead of "observed empty"."""
    ev = next(iter(sp._ldap(_ldap_row({
        "request": {"operation": "extended_request", "message_id": 1},
        "responses": [{"operation": "intermediate_response", "message_id": 1,
                       "intermediate_response": {"name": "1.3.6.1"}}],
    }))))
    assert "result" not in ev.attrs
    assert ev.attrs["opcode"] == "extended_request"  # the rest of the event survives


# -- Defect 2: sip.reason ------------------------------------------------------------

def test_sip_status_message_comes_from_reason_not_reason_phrase():
    """`SCHEMA_FINDINGS` Defect 2: the key is `reason`; `reason_phrase` does not exist."""
    ev = next(iter(sp._sip({"timestamp": _iso(T0), "event_type": "sip",
                           "dest_ip": "10.0.0.30", "dest_port": 5060,
                           "sip": {"code": "404", "reason": "Not Found",
                                   "reason_phrase": "SHOULD NEVER BE READ"}})))
    assert ev.attrs["status_msg"] == "Not Found"
    assert ev.attrs["status_code"] == "404"


# -- Defect 4: smtp.rcpt_to is an ARRAY ---------------------------------------------

def test_smtp_rcpt_to_array_is_joined_not_stringified_as_a_python_list():
    """`SCHEMA_FINDINGS` Defect 4 — the one defect that MANGLES rather than drops:
    `str(["a@b", "c@d"])` yields `"['a@b', 'c@d']"`, a value that reads as observed and
    is not. Comma-joined instead, matching Zeek's own vector-field convention, so the
    same fact reads the same way whichever sensor produced it."""
    ev = next(iter(sp._smtp({"timestamp": _iso(T0), "event_type": "smtp",
                            "dest_ip": "10.0.0.25", "dest_port": 25,
                            "smtp": {"helo": "mail.corp.example",
                                     "rcpt_to": ["a@b.test", "c@d.test"]}})))
    assert ev.attrs["rcpt_to"] == "a@b.test,c@d.test"
    assert "[" not in ev.attrs["rcpt_to"] and "'" not in ev.attrs["rcpt_to"]


# -- Defect 5: arp ------------------------------------------------------------------

def _arp_row(opcode="request", src_ip="10.0.0.5", src_mac="aa:bb:cc:dd:ee:ff",
            dest_ip="10.0.0.9", dest_mac="00:00:00:00:00:00"):
    return {"timestamp": _iso(T0), "event_type": "arp",
            "arp": {"opcode": opcode, "src_ip": src_ip, "src_mac": src_mac,
                    "dest_ip": dest_ip, "dest_mac": dest_mac,
                    "hw_type": "ethernet", "proto_type": "ipv4"}}


def test_arp_binds_the_sender_mac_to_the_sender_ip():
    ev = next(iter(sp._arp(_arp_row())))
    assert isinstance(ev, IdentityEvent)
    assert (ev.kind, ev.ip, ev.mac) == ("arp", "10.0.0.5", "aa:bb:cc:dd:ee:ff")


def test_arp_never_binds_the_target_side_of_a_request():
    """The correctness nuance the schema audit called out explicitly: in an ARP
    REQUEST the `dest_mac` is a placeholder (all-zero) for an address the sender is
    ASKING about. Binding it asserts that a host nobody has seen owns a hardware
    address that does not exist. Exactly one event per row, and it is the sender's."""
    events = list(sp._arp(_arp_row(opcode="request")))
    assert len(events) == 1
    assert events[0].ip == "10.0.0.5"
    assert "10.0.0.9" not in (events[0].ip, events[0].mac)


def test_arp_reply_also_binds_only_its_own_sender():
    ev = next(iter(sp._arp(_arp_row(opcode="reply", src_ip="10.0.0.9",
                                    src_mac="12:22:33:44:55:66"))))
    assert (ev.ip, ev.mac) == ("10.0.0.9", "12:22:33:44:55:66")


def test_arp_probe_with_the_unspecified_address_binds_nothing():
    """RFC 5227's ARP probe carries `src_ip = 0.0.0.0` — a host asking whether an
    address is free, i.e. the one case where the sender is explicitly NOT claiming it.
    Binding it would mint a host entity for `0.0.0.0`."""
    assert list(sp._arp(_arp_row(src_ip="0.0.0.0"))) == []


def test_arp_with_an_all_zero_hardware_address_binds_nothing():
    assert list(sp._arp(_arp_row(src_mac="00:00:00:00:00:00"))) == []


def test_arp_with_a_non_address_sender_binds_nothing():
    """`is_ip` parses, never pattern-matches (`LES-008`)."""
    assert list(sp._arp(_arp_row(src_ip="not-an-address"))) == []


def test_arp_opcode_outside_the_declared_set_is_refused_and_counted(tmp_path):
    """Fail closed AND loud: Suricata's schema types `arp.opcode` as a bare string with
    no enumeration, so the exact spellings a build writes cannot be resolved from the
    schema. An unruled opcode mints nothing and is COUNTED by name — which is what makes
    a wrong guess about the vocabulary a visible number rather than an empty census."""
    _eve(tmp_path / "eve.json", [_arp_row(opcode="17")])
    p = _plugin()
    assert list(p.events(str(tmp_path))) == []
    assert p.parse_notes == {"unruled arp opcode '17'": 1}


def test_arp_reaches_the_window_manager_and_is_counted_as_unscoped_not_dropped(tmp_path):
    """The named downstream gap, pinned so the next reader inherits the measurement
    rather than the assumption: `windowing.entities.IDENTITY_SCOPES` has no `arp` row
    yet, so this evidence is COUNTED (`unscoped_identity_kinds`) and anchors nothing.
    That is the loud, honest state — and this test is what will change, to an anchor
    assertion, when the one-line table row lands in `windowing/`."""
    _eve(tmp_path / "eve.json", [_arp_row(), _flow_row(ts=T0 + 60)])
    p = _plugin()
    wm = WindowManager(window_seconds=3600)
    results = list(wm.process(p.events(str(tmp_path)))) + list(wm.finalize())
    assert sum(w.health["event_counts"].get("IdentityEvent", 0) for w in results) == 1
    assert sum(w.health["unscoped_identity_kinds"] for w in results) == 1, (
        "arp identity evidence must reach windowing and be COUNTED there as unscoped; "
        "0 means the event never arrived, and a merge would mean `IDENTITY_SCOPES` "
        "grew an `arp` row and this test should now assert the anchor instead")
    assert all(not w.identity_delta for w in results), (
        "unscoped evidence anchors nothing today — D1's own rule, not a bug here")


# -- S1/S2: the shared machinery moved BELOW both plugins ---------------------------

def test_suricata_package_does_not_import_the_zeek_plugin():
    """`S1`. The reorder buffer was imported from `ingest/zeek/sources.py` — a
    same-layer edge that made one plugin's importability a precondition for another's.
    An AST walk, mirroring the layering test's own method: a string search would miss an
    import and match a docstring (this file's own module docstring names `zeek`
    repeatedly)."""
    import ast
    import pathlib

    pkg = pathlib.Path(sp.__file__).parent
    offenders = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "aximprint.ingest.zeek"):
                offenders.append(f"{path.name}: from {node.module} import ...")
            if isinstance(node, ast.Import):
                offenders += [f"{path.name}: import {a.name}" for a in node.names
                              if a.name.startswith("aximprint.ingest.zeek")]
    assert offenders == [], offenders


def test_the_shared_reorder_module_is_the_one_the_zeek_side_re_exports():
    """One implementation, two callers — the `Q2` property, now held by identity rather
    than by two files agreeing. If this ever fails, a second bounded reorder buffer
    exists somewhere and the two will drift (silently, and only on captures shaped just
    wrong enough to trip it)."""
    from aximprint.ingest import reorder
    from aximprint.ingest.zeek import sources as zs

    assert zs.reorder_by_key is reorder.reorder_by_key


def test_each_plugin_declares_its_own_reorder_horizon():
    """`LES-119`: Zeek's 600 s is backed by a measured 169.6 s worst-case `conn.log`
    displacement — evidence about one sensor. Suricata's identical VALUE is a declared
    default with its own disclosure, and the two must be separately settable, or the
    next measurement on either side silently retunes both."""
    from aximprint.ingest.zeek import sources as zs

    assert ss.REORDER_HORIZON_SECONDS is not None
    assert "REORDER_HORIZON_SECONDS" in zs.__dict__ and "REORDER_HORIZON_SECONDS" in ss.__dict__


# -- S3: app_proto is a declared vocabulary -----------------------------------------

def test_unmapped_app_proto_is_published_verbatim_AND_counted(tmp_path):
    """`S3`. Not rejected — `FlowEvent.service` stays sensor-native, and a label this
    build has not seen is still the sensor's own claim. But it is COUNTED, so a protocol
    vocabulary nobody has accounted for surfaces as a number instead of just appearing
    in a feature."""
    _eve(tmp_path / "eve.json", [_flow_row(app_proto="some-new-protocol")])
    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert events[0].service == "some-new-protocol"
    assert p.parse_notes == {"unmapped app_proto 'some-new-protocol'": 1}


def test_declared_app_proto_is_not_counted_and_is_not_renamed(tmp_path):
    """The other half: a declared value produces no note, and is NOT rewritten into
    Zeek's vocabulary — cross-sensor normalization of `FlowEvent.service` is a
    direction call this table deliberately does not make (see its own comment)."""
    _eve(tmp_path / "eve.json", [_flow_row(app_proto="krb5")])
    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert events[0].service == "krb5"
    assert p.parse_notes == {}


@pytest.mark.parametrize("sentinel", ["failed", "unknown"])
def test_suricatas_two_non_protocol_app_proto_values_are_declared_not_unmapped(sentinel):
    assert sp._APP_PROTO_TO_SERVICE[sentinel] == sentinel


# -- S4: the timestamp fast path ----------------------------------------------------

@pytest.mark.parametrize("text", [
    "2026-06-21T10:00:00.123456+0000",
    "2026-06-21T10:00:00+0000",
    "2026-06-21T10:00:00.123456+00:00",
    "2026-06-21T10:00:00.123456Z",
    "2026-06-21T03:00:00.000000-0700",
])
def test_timestamp_fast_path_agrees_with_the_strptime_fallback(text):
    """`S4` is a performance change on a function that runs once per field per row, so
    the property that matters is that it changed NOTHING about the answers."""
    import datetime as _dt

    expected = None
    for fmt in ss._TS_FORMATS:
        try:
            expected = _dt.datetime.strptime(
                text[:-1] + "+0000" if text.endswith("Z") else text, fmt).timestamp()
            break
        except ValueError:
            continue
    assert expected is not None, "fixture must be parseable by the fallback too"
    assert ss.parse_suricata_timestamp(text) == expected


def test_naive_timestamp_is_refused_rather_than_resolved_in_host_local_time():
    """The property `S4` had to KEEP, and the one `fromisoformat` would happily break:
    `.timestamp()` on a naive datetime resolves it in the HOST's timezone, so the same
    capture would ingest to different epoch seconds on two machines (`LES-002`). The
    `strptime` path could never produce a naive result (`%z` is mandatory in both
    formats); the fast path can, which is why the guard is explicit."""
    assert ss.parse_suricata_timestamp("2026-06-21T10:00:00.123456") is None
    assert ss.parse_suricata_timestamp("2026-06-21T10:00:00") is None


# -- S6: the dcerpc selection rule --------------------------------------------------

def test_dcerpc_publishes_the_first_interface_and_counts_the_discarded(tmp_path):
    """`S6`. `attrs["endpoint"]` is singular and a bind can negotiate several
    interfaces. Silently taking `interfaces[0]` is indistinguishable, downstream, from a
    record that only ever had one — so the rule is declared and the remainder counted."""
    row = {"timestamp": _iso(T0), "event_type": "dcerpc", "dest_ip": "10.0.0.9",
          "dest_port": 135,
          "dcerpc": {"opnum": 0, "interfaces": [
              {"uuid": "e1af8308-5d1f-11c9-91a4-08002b14a0fa"},
              {"uuid": "12345778-1234-abcd-ef00-0123456789ab"},
              {"uuid": "6bffd098-a112-3610-9833-46c3f87e345a"}]}}
    _eve(tmp_path / "eve.json", [row])
    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert events[0].attrs["endpoint"] == "e1af8308-5d1f-11c9-91a4-08002b14a0fa"
    assert p.parse_notes == {"dcerpc bind with 3 interfaces; 2 not published": 1}


def test_dcerpc_single_interface_produces_no_note(tmp_path):
    row = {"timestamp": _iso(T0), "event_type": "dcerpc", "dest_ip": "10.0.0.9",
          "dest_port": 135, "dcerpc": {"opnum": 3, "interfaces": [{"uuid": "abc"}]}}
    _eve(tmp_path / "eve.json", [row])
    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.parse_notes == {}


# -- S7: the guard that makes the NEXT wrong field spelling loud --------------------

def test_expected_attrs_agrees_with_the_windowing_projection_table():
    """`_EXPECTED_ATTRS` is declared inside `ingest` because `ingest` must not import
    `windowing` (CONVENTIONS §3, downward only) — so the cross-layer check lives here,
    in a test, which is the one place allowed to read both."""
    for kind, expected in sp._EXPECTED_ATTRS.items():
        if kind in SERVICE_PROJECTIONS:
            assert tuple(expected) == SERVICE_PROJECTIONS[kind][0], (
                f"{kind}: this plugin's idea of what the kind exists to produce has "
                f"drifted from what windowing actually projects")


def test_every_service_kind_this_plugin_emits_declares_its_expected_attrs():
    """A kind with no row here is a kind whose field spellings nothing checks — the
    exact hole four silent defects hid in."""
    emitted = {"ssl", "http", "ssh", "smb", "snmp", "sip", "smtp", "dce_rpc", "quic",
               "ldap_op"}
    assert emitted <= set(sp._EXPECTED_ATTRS)


def test_a_service_event_carrying_none_of_its_expected_attrs_is_counted(tmp_path):
    """The runtime half. A wrong dotted path does not fail: `_dig` returns `None`,
    `_mapped_attrs` drops the empty value, and a well-formed event goes out with the one
    attr it existed for quietly missing. Row counts, event counts and the "PRODUCED NO
    EVENTS" warning all read clean. This counter is what does not."""
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0), "event_type": "tls", "dest_ip": "10.0.0.9",
         "dest_port": 443, "tls": {"fingerprint": "aa:bb"}},   # no sni/version/ja3s
        {"timestamp": _iso(T0 + 1), "event_type": "tls", "dest_ip": "10.0.0.9",
         "dest_port": 443, "tls": {"sni": "example.com"}},      # a real one
    ])
    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert len(events) == 2, "still emitted — this guard counts, it does not reject"
    assert p.log_stats["tls"].attrless == 1


# -- S9: the netflow decline's own precondition -------------------------------------

def test_netflow_without_flow_is_warned_about(tmp_path, capsys):
    """`S9`. `netflow` is declined because `flow` reports the same connections more
    richly — an argument that silently stops being true in a deployment configured with
    `netflow` output and `flow` disabled, where this plugin sees no traffic at all and
    both tables still look correct."""
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0), "event_type": "netflow", "src_ip": "10.0.0.5"},
        {"timestamp": _iso(T0 + 1), "event_type": "netflow", "src_ip": "10.0.0.6"},
    ])
    p = sp.SuricataPlugin()  # progress_rows left ON: the warning is a report
    list(p.events(str(tmp_path)))
    err = capsys.readouterr().err
    assert "ZERO `flow` records" in err
    assert p.unparsed_stems == {"netflow": 2}


def test_netflow_alongside_flow_is_not_warned_about(tmp_path, capsys):
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0), "event_type": "netflow", "src_ip": "10.0.0.5"},
        _flow_row(ts=T0 + 1),
    ])
    p = sp.SuricataPlugin()
    list(p.events(str(tmp_path)))
    assert "ZERO `flow` records" not in capsys.readouterr().err


# ===================================================================================
# The SECOND fix round (code review #2). Every test below fails without the fix it
# names.
# ===================================================================================

# -- #1: a PARTIAL flag record must not re-arm `M2`'s phantom listener --------------

def _refusal_row(**kw):
    """A REJ-shaped flow — responder answered with RST and never SYN'd — whose
    `tcp_flags_ts` the sensor did not write. The first fix round gated all four flag
    readings on BOTH bytes, so this fell through to `established`.
    """
    row = _flow_row(tcp_flags_tc="14", pkts_in=1, bytes_in=40, **kw)
    del row["tcp"]["tcp_flags_ts"]
    return row


def test_refusal_is_still_refused_when_the_originator_flag_byte_is_absent():
    """`refused` is decided by the RESPONDER's own accumulated byte alone (RST, no
    SYN). Requiring `tcp_flags_ts` too sent this row to `established` — which is inside
    `FLOW_ACCEPTED`."""
    ev = next(iter(sp._flow(_refusal_row())))
    assert ev.state_class == "refused"
    assert ev.state_class not in FLOW_ACCEPTED


def test_a_partial_flag_record_mints_no_listener_on_a_host_that_refused(tmp_path):
    """The CONSEQUENCE, not the internal state: `M2`'s 20-host false-listener claim was
    reachable again through any record missing one flag byte. Asserted where the damage
    lands — the assertion layer's `listens_on` — rather than on `state_class`."""
    from aximprint.learners.extractors import extract_assertions

    _eve(tmp_path / "eve.json", [_refusal_row(dst_ip="10.0.0.9", dst_port=23,
                                              app_proto="failed")])
    wm = WindowManager(window_seconds=3600)
    windows = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    listens = [(a["subject"], a["object"]) for w in windows
               for a in extract_assertions(w) if a["predicate"] == "listens_on"]
    assert listens == [], (
        "a refusal proves the host is up, never that the port is open — a missing "
        "`tcp_flags_ts` must not turn one into a listener claim")


def test_reset_orig_is_still_read_when_the_responder_flag_byte_is_absent():
    row = _flow_row(tcp_flags_ts="06")
    del row["tcp"]["tcp_flags_tc"]
    assert next(iter(sp._flow(row))).state_class == "reset_orig"


def test_reset_resp_is_still_read_when_the_originator_flag_byte_is_absent():
    """`0x16` = SYN|RST|ACK: the responder accepted, then aborted. Its own byte carries
    the whole story, so the originator's absence costs nothing."""
    row = _flow_row(tcp_flags_tc="16")
    del row["tcp"]["tcp_flags_ts"]
    assert next(iter(sp._flow(row))).state_class == "reset_resp"


def test_other_is_the_one_reading_that_still_needs_both_directions():
    """`other` means "NEITHER direction carried a SYN", which is unanswerable with one
    byte missing — so it is skipped, and the counter-based reading stands. Under-claiming
    (`established` here, `refused` for a midstream reset) is the safe direction: both
    are decisions this build can defend from evidence it actually has."""
    row = _flow_row(tcp_flags_tc="18")  # ACK|PSH — no SYN, no RST, responder answered
    del row["tcp"]["tcp_flags_ts"]
    assert next(iter(sp._flow(row))).state_class == "established"


# -- #2: an unreadable source must not publish a clean census ----------------------

def test_census_stays_none_when_not_one_row_carried_an_event_type(tmp_path, capsys):
    """`LES-006`: `{}` means "audited, nothing left over". A file of well-formed JSON
    with no `event_type` in it published exactly that, making an unreadable source
    indistinguishable from a perfectly-covered one."""
    _eve(tmp_path / "eve.json",
         [{"timestamp": _iso(T0 + i), "src_ip": "10.0.0.5"} for i in range(500)])
    p = sp.SuricataPlugin()  # progress_rows ON: the refusal is a report
    assert list(p.events(str(tmp_path))) == []
    assert p.unparsed_stems is None and p.unrecognized_stems is None
    assert "REFUSING to publish an event_type census" in capsys.readouterr().err


def test_census_stays_none_when_the_source_is_not_json_at_all(tmp_path, capsys):
    (tmp_path / "eve.json").write_text("\n".join(f"not json {i}" for i in range(500)))
    p = sp.SuricataPlugin()
    assert list(p.events(str(tmp_path))) == []
    assert p.unparsed_stems is None and p.unrecognized_stems is None
    err = capsys.readouterr().err
    assert "REFUSING to publish an event_type census" in err
    assert "500 unreadable or typeless" in err


def test_census_is_still_published_when_only_SOME_rows_are_unreadable(tmp_path):
    """The other half of the gate: partial damage is a counted health fact, not a
    reason to withhold a census that was genuinely taken."""
    (tmp_path / "eve.json").write_text(
        "not json\n" + json.dumps(_flow_row()) + "\n{\"no\": \"type\"}\n")
    p = _plugin()
    assert len(list(p.events(str(tmp_path)))) == 1
    assert p.unparsed_stems == {}


# -- #3: `_FileStats` reaches a reporting path -------------------------------------

def test_a_cap_forced_flush_is_disclosed_in_the_closing_summary(tmp_path, capsys):
    """`LES-004`: a cap-forced flush means the reorder horizon was NOT honored for those
    rows. The first landing constructed `_FileStats`, threaded it through two layers and
    read no field of it — the counter existed and disclosed nothing."""
    _eve(tmp_path / "eve.json", [_flow_row(ts=T0 + i) for i in range(12)])
    p = sp.SuricataPlugin()
    p.reorder_rows = 2  # force the memory backstop long before the 600 s horizon
    list(p.events(str(tmp_path)))
    label, stats = next(iter(p.file_stats.items()))
    assert stats.capped > 0, "fixture no longer forces a cap-flush"
    err = capsys.readouterr().err
    assert "memory cap" in err and "horizon was not honored" in err
    assert label in err


def test_unreadable_rows_are_disclosed_per_file_in_the_closing_summary(tmp_path, capsys):
    (tmp_path / "eve.json").write_text(
        "not json\n" + json.dumps(_flow_row()) + "\n{\"no\": \"type\"}\n")
    p = sp.SuricataPlugin()
    list(p.events(str(tmp_path)))
    assert next(iter(p.file_stats.values())).malformed == 2
    assert "unreadable or carrying no `event_type`" in capsys.readouterr().err


def test_file_stats_reset_across_repeated_events_calls(tmp_path):
    _eve(tmp_path / "eve.json", [_flow_row()])
    p = _plugin()
    list(p.events(str(tmp_path)))
    list(p.events(str(tmp_path)))
    assert sum(f.rows for f in p.file_stats.values()) == 1, "second call re-counted"


# -- #4/S5: the ARP sender fields are PARSED, not shape-matched --------------------

@pytest.mark.parametrize("mac", ["unknown", "-", "n/a", "", "0:0:0", "aa:bb:cc:dd:ee",
                                 "aa:bb:cc:dd:ee:gg", "aabbccddeeff"])
def test_arp_sender_mac_that_is_not_a_mac_binds_nothing(mac):
    """`LES-008` on the field that becomes a merge key: the predecessor gate
    (`not any(ch not in "0:.-" for ch in mac)`) asked "does this contain a character
    that is not a separator", which every one of these passes."""
    assert list(sp._arp(_arp_row(src_mac=mac))) == []


@pytest.mark.parametrize("mac", ["ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:fb",
                                 "33:33:00:00:00:01"])
def test_arp_group_sender_mac_binds_nothing(mac):
    """Broadcast and multicast (`first octet & 1`). No interface owns one, so binding it
    to an IP would merge every host that ever used it into a single entity —
    forward-only, per `Q1` D2."""
    assert list(sp._arp(_arp_row(src_mac=mac))) == []


def test_arp_sender_mac_is_canonicalized_to_lowercase_colon_form():
    """`LES-107`'s rule one layer down: `AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are
    one interface, and leaving the sensor's spelling verbatim hands the resolver two
    keys for it."""
    ev = next(iter(sp._arp(_arp_row(src_mac="AA-BB-CC-DD-EE-FF"))))
    assert ev.mac == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("ip", ["255.255.255.255", "224.0.0.251", "0.0.0.0",
                                "fe80::1", "not-an-address"])
def test_arp_sender_ip_that_no_host_can_claim_binds_nothing(ip):
    assert list(sp._arp(_arp_row(src_ip=ip))) == []


def _notes(tmp_path, row):
    """Run one row through the plugin and return its counted parse notes."""
    _eve(tmp_path / "eve.json", [row])
    p = _plugin()
    list(p.events(str(tmp_path)))
    return p.parse_notes


def test_arp_with_no_opcode_is_counted_rather_than_silent(tmp_path):
    """#5. The first landing's `if opcode and ...` guard meant a missing or renamed key
    produced zero bindings AND zero notes — the counter that is the declared path back
    was silent in the one case that needs it."""
    row = _arp_row()
    del row["arp"]["opcode"]
    assert _notes(tmp_path, row) == {"arp record with no `arp.opcode` — binds nothing": 1}


@pytest.mark.parametrize("field, value, expected", [
    ("hw_type", "0x0806", "unruled arp hw_type '0x0806'"),
    ("proto_type", "ipv6", "unruled arp proto_type 'ipv6'"),
])
def test_arp_with_an_unruled_address_family_binds_nothing_and_is_counted(
        tmp_path, field, value, expected):
    """`S5`. `src_mac` only means "a 48-bit Ethernet address" when `hw_type` says so;
    Suricata writes the unrecognized case as a bare `0x%04x` string, which would
    otherwise walk into `IdentityEvent.mac` and on into a merge key."""
    row = _arp_row()
    row["arp"][field] = value
    assert list(sp._arp(row)) == []
    assert _notes(tmp_path, row) == {expected: 1}


@pytest.mark.parametrize("field", ["hw_type", "proto_type"])
def test_arp_with_a_missing_address_family_binds_nothing_and_is_counted(tmp_path, field):
    row = _arp_row()
    del row["arp"][field]
    assert list(sp._arp(row)) == []
    assert _notes(tmp_path, row) == {f"arp record with no `arp.{field}` — binds nothing": 1}


def test_arp_address_declines_do_not_carry_the_address_into_the_note_key(tmp_path):
    """`LES-017`: `parse_notes` is a dict keyed on the note string, so one key per
    rejected host address is an unbounded dimension. The sensor's own enum-ish fields
    (opcode, hw_type, proto_type) carry their value; per-host fields do not."""
    notes = {}
    for i in range(50):
        row = _arp_row(src_ip=f"224.0.0.{i}", src_mac=f"aa:bb:cc:dd:ee:{i:02x}")
        for note in sp._arp_notes(row):
            notes[note] = notes.get(note, 0) + 1
    assert list(notes.values()) == [50], "one key, not one per address"


def test_arp_notes_and_arp_agree_on_every_decline(tmp_path):
    """One implementation, two readers: a row that binds nothing must produce exactly
    one note, and a row that binds must produce none. Two copies of this gate drift
    apart silently."""
    binding = _arp_row()
    declining = [_arp_row(opcode="17"), _arp_row(src_mac="unknown"),
                 _arp_row(src_ip="255.255.255.255")]
    assert list(sp._arp_notes(binding)) == [] and len(list(sp._arp(binding))) == 1
    for row in declining:
        assert len(list(sp._arp_notes(row))) == 1 and list(sp._arp(row)) == []


# -- S1: `_EXPECTED_ATTRS` catches ONE wrong spelling, not only a total loss --------

def test_a_declared_attr_never_populated_is_named_at_close(tmp_path, capsys):
    """The ANY-gate this replaces fired only when a kind lost EVERY attr; mutating two
    of `tls`'s three dotted paths left it reading zero. Spellings break one at a time."""
    _eve(tmp_path / "eve.json", [
        {"timestamp": _iso(T0 + i), "event_type": "tls", "dest_ip": "10.0.0.9",
         "dest_port": 443, "tls": {"sni": "example.com"}} for i in range(3)])
    p = sp.SuricataPlugin()
    list(p.events(str(tmp_path)))
    err = capsys.readouterr().err
    assert "ATTR ssl" in err
    assert "version" in err and "ja3s" in err
    assert p.declared_attrs_seen["ssl"] == {"server_name"}
    assert p.log_stats["tls"].attrless == 0, (
        "the per-EVENT gate cannot see this — that is the whole point")


def test_no_attr_warning_for_a_kind_this_capture_never_carried(tmp_path, capsys):
    """`LES-006`: a declared key of a kind with no rows is an absence of evidence, not
    evidence of a broken spelling."""
    _eve(tmp_path / "eve.json", [_flow_row()])
    p = sp.SuricataPlugin()
    list(p.events(str(tmp_path)))
    assert "ATTR " not in capsys.readouterr().err
