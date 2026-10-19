"""Zeek ingest plugin — what a Zeek record *means*.

**Where records come from is `sources.py`'s job, not this file's.** A source locates
and decodes; this module owns the parser table, the ordering guarantee, and the
merge. Two transports exist today and share every line below:

  ``<path>``           a directory of Zeek text logs (TSV ``#fields`` header or
                       JSON-lines, optionally gzipped) — a flat directory, or a
                       *window tree* (one subdirectory per pcap window, the
                       shape ``xtract harvest``'s cold tier writes); the
                       source decides which, transparently to this file
  ``parquet:<path>``   the metadata archive of ``docs/SPEC_zeek_metadata_archive.md``
                       — written once by an external converter, read many times
                       here, with the columns below pushed down as a projection

Events are produced **streaming**: a bounded reorder buffer per stream feeds a
``heapq.merge`` k-way merge, so peak memory is O(streams x REORDER_ROWS) rather
than O(events). This closes the v0 shortcut (OPEN_QUESTIONS ``Q2``), which
materialized every event into a list and sorted it — not viable at real-capture scale.
`sources.py` reuses the identical shape one layer down, to merge a window tree's
several directories per stem, or an archive's several partition files per stem,
into the ONE stream this file's own top-level merge expects per log type.

The reorder buffer's guarantee is stated in **seconds** (``REORDER_HORIZON_SECONDS``),
because that is the unit the pathology is measured in; ``REORDER_ROWS`` is only a
memory backstop (see ``sources.reorder_by_key``, which ``_in_order`` below wraps).
Disorder the buffer cannot repair is counted *and* emitted as a
``SensorHealthEvent(kind="ingest_unordered")``, so the asterisk reaches the window
health record instead of dying on stderr (brief §5.4 #5).

Coverage is one table with no fall-through (``_PARSERS`` / ``_SKIP``): a log type in
neither is *reported* as unrecognized and left unparsed, per "fail loud, fail closed".

  conn            -> FlowEvent
  dhcp            -> IdentityEvent(dhcp)          mac<->ip (+ lease)
  dns             -> IdentityEvent(dns)           hostname<->answer ip
  kerberos        -> IdentityEvent(kerberos)      principal<->client ip
  known_hosts     -> IdentityEvent(known_host)    Zeek's own host census
  ntlm            -> IdentityEvent(ntlm)          Windows hostname<->answering ip
  ssl             -> ServiceEvent(ssl)            server_name / ja3 / version
  dce_rpc         -> ServiceEvent(dce_rpc)        endpoint / operation / named pipe
  ldap_search     -> ServiceEvent(ldap)           scope / result
  ldap            -> ServiceEvent(ldap_op)        opcode / bind DN (ldap_search owns kind=ldap)
  smb_mapping     -> ServiceEvent(smb)            share type / dialect
  smb_files       -> ServiceEvent(smb_files)      action on a share
  ntp             -> ServiceEvent(ntp)            version / mode / stratum
  known_services  -> ServiceEvent(known_service)  Zeek's own listen-port census
  software        -> ServiceEvent(software)       banner-derived software fingerprint
  http            -> ServiceEvent(http)           vhost / URI / UA / basic-auth creds
  ssh             -> ServiceEvent(ssh)            version banner / host key
  quic            -> ServiceEvent(quic)           server_name / version
  snmp            -> ServiceEvent(snmp)           sysDescr / community
  tunnel          -> ServiceEvent(tunnel)         overlay type (VXLAN/GRE/Teredo/...)
  smtp            -> ServiceEvent(smtp)           helo / relay chain / originating ip
  sip             -> ServiceEvent(sip)            user_agent / call routing
  x509            -> ServiceEvent(x509)           cert fields + SANs (no connection tuple)
  ocsp            -> ProfileEvent(ocsp_cache_lifetime)  cache-lifetime cadence, no subject (Q29)
  notice          -> AlertEvent
  weird           -> AlertEvent(category=weird)
  capture_loss    -> SensorHealthEvent(capture_loss)
  stats           -> SensorHealthEvent(pkts_dropped | pkt_lag)

Plugins are lossy by design (brief §3): each parser maps the fields the event model
can carry and drops the rest.
"""
from __future__ import annotations

import heapq
import ipaddress
import math
import sys
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from aximprint.events import (
    AlertEvent, Event, FlowEvent, IdentityEvent, ProfileEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.ingest.base import IngestPlugin, register
from aximprint.ingest.zeek.sources import (
    DISPLACEMENT_COLUMN, REORDER_HORIZON_SECONDS, REORDER_ROWS, ArchiveContractError,
    ZeekSource, ZeekStream, open_source, reorder_by_key, source_vantage,
)

_UNSET = {"-", "(empty)", ""}

#: Emit a stderr progress line every N rows of a given log; 0 disables reporting.
PROGRESS_ROWS = 250_000


@dataclass
class _LogStats:
    """Per-log-type run telemetry: progress reporting + the ordering/parse audits."""
    rows: int = 0
    events: int = 0
    unordered: int = 0   # emitted below the running max ts: disorder beyond the horizon
    capped: int = 0      # flushes forced by REORDER_ROWS, i.e. the horizon was not honored
    malformed: int = 0   # rows skipped: column count disagrees with #fields, or ts unusable
    #: `M2`: cap-forced flushes from `LogDirSource`'s window-tree mode's INNER,
    #: per-window-directory reorder pass — distinct from `capped` (the OUTER,
    #: per-stem pass's own count) because the inner pass has no second
    #: observer able to re-derive it: `capped` is read in exactly one place
    #: (`_summary`, below), and that line only ever saw the outer pass before
    #: this field existed, so an inner-pass cap event had nothing downstream
    #: watching for it and simply vanished (`LES-004`, `LES-006`). Forwarded
    #: from `sources._PerFileTally`, which is the only writer. Always 0 for
    #: the flat-directory path and for `ParquetArchiveSource`, neither of
    #: which has an inner reorder pass.
    source_capped: int = 0


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _num(v) -> Optional[float]:
    """Float, or None when the sensor left the field unset.

    Distinct from `_f` on purpose: an unset counter means *unknown*, and coercing
    it to 0.0 publishes an imputed value as an observed one (CONVENTIONS §3,
    "imputed never masquerades as observed").
    """
    if v is None or v in _UNSET:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


#: Zeek writes a `bool` as `T`/`F` on the TSV transport and as a native `True`/`False` on
#: JSON-lines; the Parquet archive keeps it VARCHAR (`scripts/make_zeek_archive.py`'s default —
#: deliberately NOT typed BOOLEAN in `_STEM_TYPES`, since the two transports must hand `_bool`
#: values it resolves identically, and `T`/`F` round-tripped as text does that with no
#: dependence on DuckDB's own boolean-literal parsing). All three shapes reach here.
_TRUE = {"T", "true", "True", "1"}
_FALSE = {"F", "false", "False", "0"}


def _bool(v) -> Optional[bool]:
    """Tri-state bool: True / False / None when the sensor left the field unset.

    `_num`'s discipline for a different type, and for the same reason — the caller
    (`_conn`'s `local_orig`/`local_resp`) feeds a field where "unset" and "false" are
    opposite claims. Coercing an unset `local_resp` to `False` would assert that the
    responder is OUTSIDE the monitored network, which is exactly the fabricated evidence
    `LES-006` exists to prevent; downstream that would drop a real host out of the twin.
    An unrecognized token is `None` (unknown), never silently `False`.
    """
    if isinstance(v, bool):
        return v
    if v is None or v in _UNSET:
        return None
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _s(v) -> str:
    """String coercion for `_attrs()`. A Zeek `vector`/`set` field (e.g. `vector[string]`,
    `set[string]`) arrives as a plain comma-joined string on the TSV transport, but as a
    native, already-ORDERED `list` on a transport that preserves structure (JSON-lines, a
    Parquet `LIST` column — SPEC "`answers`/`TTLs` may be either a Parquet `LIST` or a
    comma-joined `VARCHAR`"). `v in _UNSET` raises `TypeError: unhashable type: 'list'` on
    the latter, so a list/tuple is joined on `,` first — matching Zeek's own TSV writer
    convention for the same field — before the unset check ever sees it. `known_services`'s
    `service` (`set[string]`) and `ldap_search`'s `attributes` (`vector[string]`) are the two
    fields this matters for today; a genuinely unordered container (a bare `set`) is
    deliberately NOT handled here, since joining one without sorting first would be a
    determinism hazard (CONVENTIONS §3) and no transport in `sources.py` actually produces
    one — `json.loads` and DuckDB's `LIST` both hand back an already-ordered `list`.
    """
    if isinstance(v, (list, tuple)):
        v = ",".join(str(x) for x in v)
    return "" if v is None or v in _UNSET else str(v)


def _attrs(r: dict, keys) -> dict:
    """Sensor fields that are actually set, in the order `keys` names them."""
    return {k: _s(r.get(k)) for k in keys if _s(r.get(k))}


# -- conn state -----------------------------------------------------------------

# Zeek conn_state -> the normalized outcome vocabulary (events.FLOW_STATE_CLASSES).
# Zeek encodes two things in one token: whether the responder ever answered, and
# who tore the connection down. A closed table keeps both readable; a state
# missing from it becomes "unknown" and shows up in the window's state_mix.
_CONN_STATE_CLASS = {
    "S0": "attempted",        # SYN sent, never answered
    "SH": "attempted",        # originator SYN then FIN, never answered
    "RSTOS0": "attempted",    # originator SYN then RST, never answered
    "REJ": "refused",         # answered with a refusal: host up, port shut
    "SF": "established",      # answered, normal close
    "S1": "established",      # answered, still open when logged
    "S2": "established",      # answered, originator half-closed
    "S3": "established",      # answered, responder half-closed
    "RSTO": "reset_orig",     # established, then the originator aborted
    "RSTR": "reset_resp",     # established, then the responder aborted
    "RSTRH": "partial",       # responder side only — the sensor missed a direction
    "SHR": "partial",         # responder side only — the sensor missed a direction
    "OTH": "other",           # midstream; no handshake observed
}


# -- per-log parsers ------------------------------------------------------------

#: conn counters (duration/bytes/packets) that a row may leave unset.
_CONN_COUNTERS = ("duration", "orig_bytes", "resp_bytes", "orig_pkts", "resp_pkts")

#: Outcome classes where an unset counter honestly reads as 0: nothing was ever
#: exchanged, so "no bytes" is the observation, not an imputation.
_ZERO_IS_HONEST = frozenset({"attempted", "refused"})


def _conn(r: dict) -> Iterator[Event]:
    ts = _f(r.get("ts"))
    if not ts:
        return
    state = _s(r.get("conn_state"))
    state_class = _CONN_STATE_CLASS.get(state, "unknown")
    # Deliberate: the numeric fields keep `_f`/`_i` rather than `_num`'s Optional,
    # because every accumulator downstream does arithmetic on them and a None would
    # have to be re-imputed there instead — one layer further from the evidence.
    # But `-` is only honestly 0 for a flow that never exchanged anything (S0/REJ);
    # for a midstream or one-sided flow it means the sensor did not see the payload,
    # and 0 UNDERSTATES it. So the imputation is marked rather than hidden, and the
    # window health record counts it (CONVENTIONS §3: imputed never masquerades as
    # observed). On the reference capture this is ~0.01% of rows, all `other`/`partial`.
    imputed = (state_class not in _ZERO_IS_HONEST
               and any(_num(r.get(k)) is None for k in _CONN_COUNTERS))
    yield FlowEvent(
        ts=ts,
        src_ip=_s(r.get("id.orig_h")), dst_ip=_s(r.get("id.resp_h")),
        src_port=_i(r.get("id.orig_p")), dst_port=_i(r.get("id.resp_p")),
        proto=_s(r.get("proto")), service=_s(r.get("service")),
        duration=_f(r.get("duration")),
        bytes_out=_i(r.get("orig_bytes")), bytes_in=_i(r.get("resp_bytes")),
        pkts_out=_i(r.get("orig_pkts")), pkts_in=_i(r.get("resp_pkts")),
        state=state, state_class=state_class, counters_imputed=imputed,
        # The sensor's own inside/outside judgment (`Site::local_nets`), the input to
        # segment extraction (`windowing/segments.py`). `_bool`, not `_s(...) == "T"`:
        # unset must stay distinguishable from false — see `_bool`'s docstring.
        # Measured 100% populated on the 2026-08-11 baseline (15,037 conn rows in one
        # window: 7,143 T/T, 4,165 F/F, 3,716 T/F, 13 F/T, zero unset), but a Zeek that
        # never declared `local_nets` writes them unset, and that must read as unknown.
        local_src=_bool(r.get("local_orig")), local_dst=_bool(r.get("local_resp")),
    )


def _dhcp(r: dict) -> Iterator[Event]:
    ts = _f(r.get("ts"))
    ip = _s(r.get("assigned_addr")) or _s(r.get("requested_addr"))
    mac = _s(r.get("mac"))
    if ts and ip and mac:
        yield IdentityEvent(ts=ts, kind="dhcp", ip=ip, mac=mac,
                            hostname=_s(r.get("host_name")),
                            lease_seconds=_f(r.get("lease_time")))


def _is_ip(value: str) -> bool:
    """True only for a real IPv4/IPv6 literal.

    Replaces a `count(".") == 3` shape heuristic that treated any four-label DNS
    name as an address: on the reference capture 34.4% of dns-derived bindings were
    a name bound as an `ip`, each reaching the store as an `observed` assertion.
    A binding is an identity claim — parse it, never guess at it (fail closed).
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _dns(r: dict) -> Iterator[Event]:
    ts = _f(r.get("ts"))
    name = _s(r.get("query"))
    answers = r.get("answers") or []
    if isinstance(answers, str):
        answers = [a for a in answers.split(",") if a]
    if ts and name:
        for a in answers:
            a = _s(a)
            # CNAME/MX/SRV/TXT answers are names, not addresses: they bind nothing
            # the v0 resolver can key on, so they are dropped rather than coerced.
            if _is_ip(a):
                yield IdentityEvent(ts=ts, kind="dns", ip=a, hostname=name,
                                    record_ttl=_f(r.get("TTLs", "0").split(",")[0]
                                                  if isinstance(r.get("TTLs"), str)
                                                  else (r.get("TTLs") or [0])[0]))


def _kerberos(r: dict) -> Iterator[Event]:
    ts = _f(r.get("ts"))
    client = _s(r.get("client"))
    ip = _s(r.get("id.orig_h"))
    if ts and client and ip:
        yield IdentityEvent(ts=ts, kind="kerberos", ip=ip, principal=client)


def _known_hosts(r: dict) -> Iterator[Event]:
    """Zeek's first-seen host census -> IdentityEvent.

    The row carries an address and nothing to bind it to, so the v0 resolver
    (entity == ip) records no binding from it (`Q1`); the event still lands in the
    window-health event counts, which is what makes the census visible at all today.
    """
    ts = _f(r.get("ts"))
    ip = _s(r.get("host"))
    if ts and ip:
        yield IdentityEvent(ts=ts, kind="known_host", ip=ip)


def _ntlm(r: dict) -> Iterator[Event]:
    """NTLM handshake -> IdentityEvent: binds a Windows host NAME to the address that
    answered it, AT THE ADDRESS ITSELF -- unlike a DNS answer, which binds a third
    party's (`docs/WORK_ORDER_entity_resolution.md`). The only new identity source
    among `docs/WORK_ORDER_zeek_stem_coverage.md` step 3's ten admits (`Q24`).

    `server_dns_computer_name` wins when set; `server_nb_computer_name` is the
    fallback for a row where Zeek only ever saw the older NetBIOS field -- the same
    either-or shape `_dhcp` already uses for `assigned_addr`/`requested_addr`.
    `IdentityEvent` has no room for `username`/`domainname`/`success`: the same
    structural loss every Identity parser here takes (module docstring's "plugins
    are lossy by design"), not a disclosure refusal -- forensic completeness
    (CONVENTIONS §3) governs what a PARSER collects, and `IdentityEvent`'s shape is
    fixed independent of this round, so there is no attrs bag here to refuse from.
    """
    ts = _f(r.get("ts"))
    ip = _s(r.get("id.resp_h"))
    hostname = _s(r.get("server_dns_computer_name")) or _s(r.get("server_nb_computer_name"))
    if ts and ip and hostname:
        yield IdentityEvent(ts=ts, kind="ntlm", ip=ip, hostname=hostname)


def _radius(r: dict) -> Iterator[Event]:
    """RADIUS authentication -> IdentityEvent, keyed on `framed_addr` and NOTHING ELSE.

    **The subject is neither endpoint of this flow, and getting that wrong is catastrophic.**
    A RADIUS exchange runs between a NAS and the RADIUS server; the host being authenticated is
    on neither end. Binding `mac` to `id.orig_h` or `id.resp_h` would attach every authenticated
    host in the building to the RADIUS server -- one entity swallowing a whole population, and a
    false merge is unrecoverable (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` R2, ruled
    2026-08-20). So the row's OWN `framed_addr` -- the address the NAS says it assigned -- is the
    subject, and a row without one anchors nothing and yields nothing.

    `_is_ip` gates it for the same reason `_dns` does: a binding is an identity claim, so parse
    the address rather than trusting the column's declared type.

    **Measured on the ten-day baseline and worth knowing before trusting this parser:** all 22
    rows have `mac` and `framed_addr` EMPTY, `result=unknown`, and 7 carry a username (`nessus`
    x6 from an internal scanner, `steve` x1 from a public address) -- scan traffic, not
    authentications. The work order that admitted this stem listed `radius.log`'s SCHEMA as
    "fields it carries" and read it as a measurement; it is not one. This parser is therefore a
    no-op on that capture by construction, which is the correct behavior and not a defect.
    """
    ts = _f(r.get("ts"))
    ip = _s(r.get("framed_addr"))
    if ts and ip and _is_ip(ip):
        yield IdentityEvent(ts=ts, kind="radius", ip=ip, mac=_s(r.get("mac")),
                            principal=_s(r.get("username")))


def _ssl(r: dict) -> Iterator[Event]:
    ts = _f(r.get("ts"))
    if ts:
        yield ServiceEvent(ts=ts, kind="ssl", ip=_s(r.get("id.resp_h")),
                           port=_i(r.get("id.resp_p")),
                           attrs=_attrs(r, ("server_name", "version", "ja3", "ja3s",
                                            "subject", "issuer")))


# -- responder-keyed ServiceEvent parsers ----------------------------------------
#
# Every parser from here through `_x509` (except `_x509` itself) shares one shape: keyed
# on the RESPONDER (`id.resp_h`/`id.resp_p`), emitting nothing unless both `ts` and `ip`
# are present. Factored into `_responder_service` (code review `S6`,
# `docs/WORK_ORDER_zeek_stem_coverage.md` step 3) so an `id.orig_h`/`id.resp_h` slip or a
# dropped `port=` has nowhere left to hide — the near-identical bodies this replaces were
# the one seam in the six-table Zeek-stem web (`LES-087`) that was never a TABLE at all,
# just repetition, so nothing checked the copies stayed identical. Each stem's attrs
# tuple is ALSO named once, module-level (`S5`, same review), and reused by `_COLUMNS`
# below — the field list a parser reads and the columns a columnar transport projects
# were previously written out twice with nothing checking they agreed.
#
# `_ssl` (above) is deliberately NOT converted: it gates on `ts` alone, with no `ip`
# truthiness check — a difference that predates this round. Converting it would be a
# behavior change to code this round did not touch, not a refactor, and is out of scope.
# `_known_services`/`_software` (below) are also not converted: both key on `host`/
# `host_p`, not `id.resp_h`/`id.resp_p`, so they are not the same shape at all.

def _responder_service(r: dict, kind: str, keys: tuple[str, ...]) -> Iterator[Event]:
    """Shared body for a responder-keyed `ServiceEvent`. See the module comment above."""
    ts = _f(r.get("ts"))
    ip = _s(r.get("id.resp_h"))
    if ts and ip:
        yield ServiceEvent(ts=ts, kind=kind, ip=ip, port=_i(r.get("id.resp_p")),
                           attrs=_attrs(r, keys))


_KNOWN_CERTS_ATTRS = ("subject", "issuer_subject", "serial")

# Every field the stem carries. `client_name` and the rest of the client info block measure
# 0/116 on the ten-day capture and structurally cannot populate under NLA (`result=encrypted`
# on 111/116, `HYBRID_EX`) -- collected anyway, because an absent field costs nothing
# (`_attrs()` gates on truthiness) and the tuple activates the moment a capture is not
# NLA-enforced.
_RDP_ATTRS = ("cookie", "result", "security_protocol", "keyboard_layout", "client_build",
              "client_name", "client_dig_product_id", "desktop_width", "desktop_height",
              "requested_color_depth", "cert_type", "cert_count", "cert_permanent",
              "encryption_level", "encryption_method")

_RFB_ATTRS = ("client_major_version", "client_minor_version", "server_major_version",
              "server_minor_version", "authentication_method", "auth", "share_flag",
              "desktop_name", "width", "height")

_REDIS_ATTRS = ("cmd.name", "cmd.key", "cmd.value", "success", "reply.value")

_WEBSOCKET_ATTRS = ("host", "uri", "user_agent", "subprotocol", "client_protocols",
                    "server_extensions", "client_extensions")

_SYSLOG_ATTRS = ("proto", "facility", "severity", "message")

_MQTT_ATTRS = ("proto_name", "proto_version", "client_id", "connect_status", "will_topic",
               "will_payload")


def _rdp(r: dict) -> Iterator[Event]:
    """RDP -> ServiceEvent (responder-keyed: the server offers the desktop).

    **No `IdentityEvent`.** `client_name` was this stem's reason for admission -- a computer name
    from a third source beside NTLM and Kerberos -- and it is 0/116 on the ten-day capture, along
    with the whole client info block, because the block is sent after the handshake goes encrypted
    (`result=encrypted` 111/116 at `HYBRID_EX`; the other 5 are `HYBRID_REQUIRED_BY_SERVER`). On
    an NLA-enforcing deployment it is never in cleartext.

    `cookie` (the `mstshash` token, 105/116) is collected and deliberately NOT minted as a USER
    identifier either: 53 of 105 values are opaque `@@`-prefixed tokens and 10 are `MS-MGLN`, a
    product string, so minting it would populate the user census with non-users -- the silent
    default this project refuses. The name-like values are also truncated (every value <=10
    chars, 92/105 exactly 9), so they are lossy even where they are names.
    """
    return _responder_service(r, "rdp", _RDP_ATTRS)


def _rfb(r: dict) -> Iterator[Event]:
    """VNC/RFB -> ServiceEvent. `desktop_name` is collected, never minted: it is user-set and
    only incidentally the computer name."""
    return _responder_service(r, "rfb", _RFB_ATTRS)


def _redis(r: dict) -> Iterator[Event]:
    return _responder_service(r, "redis", _REDIS_ATTRS)


def _websocket(r: dict) -> Iterator[Event]:
    return _responder_service(r, "websocket", _WEBSOCKET_ATTRS)


def _syslog(r: dict) -> Iterator[Event]:
    """Syslog -> ServiceEvent keyed on the RESPONDER, which for syslog is a log COLLECTOR --
    infrastructure, and a role a twin wants. `message` is collected and never projected: free
    text in a projection tuple burns all `SERVICE_MAX_KEYS` slots on noise and evicts the real
    facts (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` R5)."""
    return _responder_service(r, "syslog", _SYSLOG_ATTRS)


def _mqtt_connect(r: dict) -> Iterator[Event]:
    """MQTT CONNECT -> ServiceEvent. The responder is a broker. `client_id` is collected and
    never minted: it names an application instance, which is neither a machine nor a person and
    has no scope in the identity vocabulary."""
    return _responder_service(r, "mqtt", _MQTT_ATTRS)


_DCE_RPC_ATTRS = ("endpoint", "operation", "named_pipe")


def _dce_rpc(r: dict) -> Iterator[Event]:
    return _responder_service(r, "dce_rpc", _DCE_RPC_ATTRS)


_LDAP_SEARCH_ATTRS = ("scope", "result", "result_count", "base_object", "filter",
                     "attributes", "diagnostic_message")


def _ldap_search(r: dict) -> Iterator[Event]:
    """LDAP query traffic -> ServiceEvent (the server is a directory, and answering).

    `base_object`, `filter`, `attributes` and `diagnostic_message` are collected: under
    forensic completeness (CONVENTIONS §3, CHANGELOG 2026-08-12) a parser does not withhold
    a field on disclosure grounds. They are the log's densest identity content —
    `base_object`/`filter` in particular can name computer accounts, users and OUs the tap
    never saw as an address at all, which is real evidence, not redundant with `conn`
    (`Q24`'s referenced-entity amendment). None of it has a subject to bind to yet:
    `windowing/entities.py` is address-in/address-out and drops a binding with no IP (`Q1`),
    and the durable-fact projection for this kind stays declined pending that work
    (`_SERVICE_NOT_PROJECTED["ldap"]`, `Q27`).
    """
    return _responder_service(r, "ldap", _LDAP_SEARCH_ATTRS)


_LDAP_OP_ATTRS = ("message_id", "version", "opcode", "result", "diagnostic_message",
                 "object", "argument")


def _ldap(r: dict) -> Iterator[Event]:
    """LDAP protocol operation (`ldap.log`) -> ServiceEvent, responder-keyed.

    Kind is `ldap_op`, NOT `ldap`: `_ldap_search` (above, a DIFFERENT stem,
    `ldap_search.log`) already owns kind `"ldap"`, and `SERVICE_PROJECTIONS` is
    keyed by kind -- collapsing the two under one name would silently merge two
    distinct facts under one predicate
    (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3).

    `opcode` is the durable fact this stem was admitted for (`serves_ldap`).
    `argument` (a bind DN) is credential-adjacent identity content, collected
    under forensic completeness but with no subject to bind to under today's
    `entity == ip` resolver (`Q1`).
    """
    return _responder_service(r, "ldap_op", _LDAP_OP_ATTRS)


_SMB_MAPPING_ATTRS = ("share_type", "service", "path", "native_file_system")


def _smb_mapping(r: dict) -> Iterator[Event]:
    return _responder_service(r, "smb", _SMB_MAPPING_ATTRS)


_SMB_FILES_ATTRS = ("action", "path", "name")


def _smb_files(r: dict) -> Iterator[Event]:
    """SMB file activity -> ServiceEvent. `name` (the filename) is collected: under forensic
    completeness (CONVENTIONS §3, CHANGELOG 2026-08-12) a parser does not withhold an
    identifying field on disclosure grounds. Collecting it in full also makes this kind
    plausibly high-cardinality (near one filename per action) where it was near-free before —
    the durable-fact projection stays declined pending re-ruling on that basis, not privacy
    (`_SERVICE_NOT_PROJECTED["smb_files"]`, `Q27`).
    """
    return _responder_service(r, "smb_files", _SMB_FILES_ATTRS)


_NTP_ATTRS = ("version", "mode", "stratum", "poll")


def _ntp(r: dict) -> Iterator[Event]:
    """NTP exchange -> ServiceEvent. `poll` joins version/mode/stratum: a poll interval is a
    schedule parameter (DESIGN_BRIEF §9's "fitted intensities/schedules/seeds"), and dropping
    it before anything could weigh it was a forensic-completeness gap in its own right, not
    merely an unused field — `_SERVICE_NOT_PROJECTED["ntp"]`'s stated reason was "no field for
    it", which this fixes without re-ruling the decline itself (`Q27`).
    """
    return _responder_service(r, "ntp", _NTP_ATTRS)


def _known_certs(r: dict) -> Iterator[Event]:
    """Zeek's own certificate census -> ServiceEvent, keyed on `host`/`port_num`.

    NOT `_responder_service`: this stem keys on `host`, exactly like its siblings
    `known_hosts`/`known_services`, which is why the module comment above excludes that shape
    from the shared helper (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` R4).

    This is also the address `x509` structurally lacks -- `_SERVICE_NOT_PROJECTED["x509"]` declines
    that stem because "the certificate carries no connection tuple at all". Here the tuple is the
    row. `subject` is deliberately NOT minted as an identifier: a certificate subject is routinely
    a wildcard or a SAN list shared across many hosts, which is the same fanout failure as binding
    on a router MAC, arriving through TLS instead of Ethernet.
    """
    ts = _f(r.get("ts"))
    ip = _s(r.get("host"))
    if ts and ip:
        yield ServiceEvent(ts=ts, kind="known_cert", ip=ip, port=_i(r.get("port_num")),
                           attrs=_attrs(r, _KNOWN_CERTS_ATTRS))


def _known_services(r: dict) -> Iterator[Event]:
    """Zeek's own listen-port census -> ServiceEvent (a direct service statement)."""
    ts = _f(r.get("ts"))
    ip = _s(r.get("host"))
    if ts and ip:
        yield ServiceEvent(ts=ts, kind="known_service", ip=ip,
                           port=_i(r.get("port_num")),
                           attrs=_attrs(r, ("port_proto", "service")))


def _software(r: dict) -> Iterator[Event]:
    """Software fingerprint -> ServiceEvent. Evidence for what a host RUNS.

    Zeek's software framework reads BANNERS -- a User-Agent, an SSH version
    string, a `Server:` header -- not OS fingerprints. Measured on a real
    capture (2026-08-11, 12 windows): 187 rows over 58 hosts, entirely
    `HTTP::BROWSER` / `SSH::CLIENT` / `HTTP::SERVER`, and **zero** `OS`-typed
    rows. So this is a direct statement about installed software and only an
    INDIRECT, low-coverage hint about the host's operating system -- the OS
    token, where it exists at all, is buried inside `unparsed_version`
    (`Windows NT 10.0` in a User-Agent, `Ubuntu-3ubuntu0.1` in an SSH banner).
    Extracting that is deliberately not attempted here; see `Q26`.

    `unparsed_version` is carried in `attrs` but is deliberately NOT what
    windowing accumulates into node features: a full User-Agent is very nearly
    unique per client, so publishing it as a categorical dimension would blow
    the vocabulary's cardinality open (`LES-017`). The structured
    `software_type`/`name`/major.minor triple is the bounded part, and that is
    what `windowing` keeps.
    """
    ts = _f(r.get("ts"))
    ip = _s(r.get("host"))
    if ts and ip:
        yield ServiceEvent(ts=ts, kind="software", ip=ip, port=_i(r.get("host_p")),
                           attrs=_attrs(r, ("software_type", "name", "version.major",
                                            "version.minor", "version.minor2",
                                            "version.minor3", "version.addl",
                                            "unparsed_version")))


_HTTP_ATTRS = ("trans_depth", "method", "host", "uri", "referrer", "version",
              "user_agent", "origin", "request_body_len", "response_body_len",
              "status_code", "status_msg", "info_code", "info_msg", "tags", "username",
              "password", "proxied", "orig_fuids", "orig_filenames", "orig_mime_types",
              "resp_fuids", "resp_filenames", "resp_mime_types")


def _http(r: dict) -> Iterator[Event]:
    """HTTP request/response -> ServiceEvent, responder-keyed. `host` is the cleartext
    analogue of `ssl.server_name` -- which vhost an address answers for -- and nothing
    already parsed carries it (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3, `Q24`).

    Every field the log offers is collected under forensic completeness (CONVENTIONS
    §3, CHANGELOG 2026-08-12), including `username`/`password`/`uri`/`referrer`/
    `user_agent` -- the earlier privacy-motivated refusal of exactly these fields is
    VOID (`Q24`'s amendment box). `SERVICE_MAX_KEYS` is the expected truncation site
    for this kind: a FORWARD proxy keyed on `id.resp_h` accumulates one `host` per
    external site visited, unlike the CDN case that cap was originally measured
    against -- read the truncation count in tier 3 rather than raising the cap
    pre-emptively.
    """
    return _responder_service(r, "http", _HTTP_ATTRS)


_SSH_ATTRS = ("version", "auth_success", "auth_attempts", "direction", "client",
             "server", "cipher_alg", "mac_alg", "compression_alg", "kex_alg",
             "host_key_alg", "host_key", "remote_location.country_code",
             "remote_location.region", "remote_location.city",
             "remote_location.latitude", "remote_location.longitude")


def _ssh(r: dict) -> Iterator[Event]:
    """SSH handshake -> ServiceEvent, responder-keyed. `server`, the version banner Zeek
    reads off the handshake WITH the responder itself, is admin-surface evidence and the
    OS hint `software` failed to deliver (`Q26`); `client` is not taken here --
    `software.log`'s `SSH::CLIENT` already has it.

    Kept at confidence 0.9 (`serves_ssh`) rather than `software`'s 0.7 -- the one
    judgment call in this admit: a version banner is editable, but `server` is read off
    the handshake with the responder itself rather than inferred from a third party's
    header, and the entity it describes is the one that emitted it
    (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3). Every field is collected under
    forensic completeness, including `host_key` (the earlier refusal is void, `Q24`'s
    amendment box).

    Real-baseline check (`/tmp/aximprint-zeek-test/`, ungated): on this network `server`
    is 100% GitHub's own terse banner (`SSH-2.0-<hash>`; all 6 distinct responders sit
    in GitHub's documented SSH IP range) -- outbound git-over-ssh, not diverse internal
    admin servers. The banner IS genuinely observed (not spoofable the way a User-Agent
    is), which is what the confidence judgment above is actually about; what this
    baseline does not demonstrate is the OS-fingerprinting DIVERSITY the admit's
    rationale hoped for. Recorded rather than acted on -- a content finding for a
    future re-ruling, not a reason to change the confidence unilaterally here.
    """
    return _responder_service(r, "ssh", _SSH_ATTRS)


_QUIC_ATTRS = ("version", "client_initial_dcid", "client_scid", "server_scid",
              "server_name", "client_protocol", "history")


def _quic(r: dict) -> Iterator[Event]:
    """QUIC handshake -> ServiceEvent, responder-keyed. Mirrors `ssl` field for field
    (`server_name`, `version`) -- the one admit whose value GROWS over time rather than
    decaying: `ssl` coverage shrinks as traffic migrates to QUIC, so this holds station
    rather than adding detail (`Q24`).
    """
    return _responder_service(r, "quic", _QUIC_ATTRS)


_SNMP_ATTRS = ("version", "community", "get_requests", "get_bulk_requests",
              "get_responses", "set_requests", "display_string", "up_since",
              "duration")


def _snmp(r: dict) -> Iterator[Event]:
    """SNMP exchange -> ServiceEvent, responder-keyed. `display_string` is sysDescr, a
    first-class device description landing on infrastructure (switches, routers) that
    `software.log` structurally never sees -- admitted at the LOWEST presence of any
    stem in this round (4/12 windows) on content, not frequency (`Q24`). Every field is
    collected under forensic completeness, including `community` (an SNMP community
    string, a credential; the earlier refusal is void, `Q24`'s amendment box) and
    `duration`.

    `duration` was DELIBERATELY excluded from `attrs` in this stem's first landing, on
    the reasoning that `scripts/make_zeek_archive.py`'s `_TYPES` typed the bare column
    name `"duration"` DOUBLE for `conn.log`'s own, unrelated field, and that typing was
    keyed on the column NAME alone, globally, not per stem -- silently returning a
    different STRING on the archive transport than Zeek's own TSV text (`LES-092`).
    RESTORED (code review `S1`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3): the
    fix belongs at the actual defect, five lines from its source, not as a permanent
    forfeiture of a real field here -- `make_zeek_archive.py`'s typing is now keyed by
    `(stem, column)`, so `("snmp", "duration")` correctly stays VARCHAR (text end to
    end) while `("conn", "duration")` stays DOUBLE, and both are safe simultaneously.

    `display_string`'s own admit rationale is separately unconfirmed: MEASURED EMPTY in
    7/7 real baseline rows across all 12 windows (`/tmp/aximprint-zeek-test/`) --
    `windowing.windows.SERVICE_PROJECTIONS`'s own comment records the finding and the
    user's ruling to keep the admit anyway (`R2`, code review). Not a reason to touch
    this parser; recorded here because a reader of the docstring should not have to
    find it in a different module to know it.
    """
    return _responder_service(r, "snmp", _SNMP_ATTRS)


_TUNNEL_ATTRS = ("tunnel_type", "action")


def _tunnel(r: dict) -> Iterator[Event]:
    """Tunnel encapsulation (VXLAN/GRE/Teredo/...) -> ServiceEvent, responder-keyed.
    Which address terminates an overlay is a fact `conn` structurally cannot express,
    and a twin that misses it has the WRONG topology, not merely a thinner one (`Q24`).

    Projects to `terminates_tunnel`, not `serves_tunnel` -- see
    `windowing.windows.SERVICE_PROJECTIONS`'s entry for why the naming departs from the
    `serves_*` convention on purpose.
    """
    return _responder_service(r, "tunnel", _TUNNEL_ATTRS)


_SMTP_ATTRS = ("trans_depth", "helo", "mailfrom", "rcptto", "date", "from", "to", "cc",
              "reply_to", "msg_id", "in_reply_to", "subject", "x_originating_ip",
              "first_received", "second_received", "last_reply", "path", "user_agent",
              "tls", "fuids", "is_webmail")


def _smtp(r: dict) -> Iterator[Event]:
    """SMTP transaction -> ServiceEvent, responder-keyed. Every field is collected under
    forensic completeness (CONVENTIONS §3), including `date` (RESTORED, code review
    `S1` -- see below) and `helo`.

    `helo` is NOT projected: it is the CLIENT's self-announced HELO/EHLO argument, but
    this event is keyed on `id.resp_h`, the SERVER -- projecting it publishes the
    client's claim as a property of the server that merely received it, a KEYING bug
    (`windowing.windows._SERVICE_NOT_PROJECTED["smtp"]`, `R1`, user-ruled code review,
    reversing this kind's own earlier `Q24` ruling). The durable fact this stem was
    actually admitted for is `path` (the `Received:` relay chain) and
    `x_originating_ip`: IP ADDRESSES of hosts the sensor never saw at all, the one
    referenced-entity case usable under today's `entity == ip` resolver (`Q24`
    amendment 1). They are collected here on the event's `attrs`, not turned into a
    second subject in the resolver or a `referenced`-evidence-class assertion -- that
    remains open, recorded as a named remainder rather than silently narrowed
    (`S8`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3).

    `date` (the message's own self-reported `Date:` header) was DELIBERATELY excluded
    on this stem's first landing: `docs/SPEC_zeek_metadata_archive.md`'s archive layout
    partitions on `date=<YYYY-MM-DD>` (Hive-style), and DuckDB's hive-partitioning
    inference silently SHADOWED a same-named DATA column with the partition-derived
    value when read back through the archive transport, with no error raised.
    RESTORED (`S1`, code review): the fix belongs at the actual defect --
    `sources.ParquetArchiveSource._read` now passes `hive_partitioning=false`, which
    buys this reader nothing it needed (every partition column it cares about, `log`
    and `date`, is already read from the DIRECTORY structure, never from a row) and
    immunizes every column name, present and future, rather than requiring every new
    stem to avoid two reserved names forever.
    """
    return _responder_service(r, "smtp", _SMTP_ATTRS)


_SIP_ATTRS = ("trans_depth", "method", "uri", "date", "request_from", "request_to",
             "response_from", "response_to", "reply_to", "call_id", "seq", "subject",
             "request_path", "response_path", "user_agent", "status_code",
             "status_msg", "warning", "request_body_len", "response_body_len",
             "content_type")


def _sip(r: dict) -> Iterator[Event]:
    """SIP transaction -> ServiceEvent, responder-keyed. Every field is collected under
    forensic completeness, including `date` (restored, see `_smtp`'s docstring -- the
    same Hive-partition hazard and the same fix, `S1`) and `user_agent`.

    `user_agent` is NOT projected: it is the CLIENT's own User-Agent header, but this
    event is keyed on `id.resp_h`, the SERVER -- the same keying bug as `_smtp`'s
    `helo` (`windowing.windows._SERVICE_NOT_PROJECTED["sip"]`, `R1`, user-ruled code
    review, reversing this kind's own earlier `Q24` ruling). Measured on the baseline:
    a SIPVicious scanner's own tool name (`"friendly-scanner"`) published as a
    property of the host it SCANNED, not the host running the scanner. SIP URIs
    (extensions, DIDs) remain unprojected regardless, pending `Q1`.
    """
    return _responder_service(r, "sip", _SIP_ATTRS)


_X509_ATTRS = ("fingerprint", "certificate.version", "certificate.serial",
              "certificate.subject", "certificate.issuer",
              "certificate.not_valid_before", "certificate.not_valid_after",
              "certificate.key_alg", "certificate.sig_alg", "certificate.key_type",
              "certificate.key_length", "certificate.exponent", "certificate.curve",
              "san.dns", "san.uri", "san.email", "san.ip", "basic_constraints.ca",
              "basic_constraints.path_len", "host_cert", "client_cert")


def _x509(r: dict) -> Iterator[Event]:
    """Certificate metadata -> ServiceEvent. NO connection tuple: verified against the
    baseline (`Q24` "Headers verified"), `x509.log` carries no `id.orig_h`/`id.resp_h`
    at all -- it is keyed by `fingerprint` (the certificate's own hash), not a
    connection, so this does NOT use `_responder_service` (there is no `id.resp_h` to
    gate on). Attributing a certificate to a host needs a join against `ssl.log`'s
    `cert_chain_fps`, which this parser does not attempt.

    `ip` therefore stays unset, and `_add_service`'s own `if not ev.ip: return`
    already makes this event structurally inert for node/edge features today --
    collected in full (forensic completeness, CONVENTIONS §3) but not attributable.
    SANs (`san.dns`/`san.uri`/`san.email`/`san.ip`) are the reason this stem parses at
    all rather than being redundant with `ssl.subject` (`Q24`); they need `Q1` before
    they have a subject to reach, hence no `SERVICE_PROJECTIONS` entry
    (`windowing.windows._SERVICE_NOT_PROJECTED["x509"]`).
    """
    ts = _f(r.get("ts"))
    if ts:
        yield ServiceEvent(ts=ts, kind="x509", attrs=_attrs(r, _X509_ATTRS))


def _ocsp(r: dict) -> Iterator[Event]:
    """OCSP validity response -> ProfileEvent, not a ServiceEvent (`Q29`). Verified against the
    baseline (`Q24` "Headers verified"): `ocsp.log` carries NO connection tuple at all -- it is
    keyed by `id`, the response's own hash, not `id.orig_h`/`id.resp_h` -- so there is no `ip` for
    a `ServiceEvent` to require, the way every other stem admitted this round has one.

    `nextUpdate - thisUpdate` is the response's CACHE LIFETIME: a population-scoped schedule
    parameter (DESIGN_BRIEF §9's "fitted intensities/schedules/seeds"), not a property of any host
    on this network -- `conn` sees that a connection happened, never the validity window that
    explains its interval (`Q24`'s third amendment, which is why this stem parses at all).

    `_num`, not `_f`, for both timestamps: an unset `thisUpdate`/`nextUpdate` must not be coerced
    to `0.0` and subtracted into a manufactured delta (CONVENTIONS §3, "imputed never masquerades
    as observed"). On the real baseline both are always set (967/967 rows across 12/12 windows),
    but a parser written to that observation rather than to the field's own honest-absence
    contract would silently start imputing the moment a differently-configured CA left one blank.

    `certStatus`/`revoketime`/`revokereason`/the hash columns are not read here -- `ProfileEvent`
    has no attrs bag to carry them (`events/model.py`'s deliberate minimalism, `Q29`), the same
    structural loss every parser here takes for a field its target event class has no room for
    (`_ntlm`'s docstring), not a disclosure refusal. Nothing is lost on disk: the cold tier and any
    Parquet archive built from it capture every column this log has regardless of what this parser
    reads (`scripts/make_zeek_archive.py::_column_union`).
    """
    ts = _f(r.get("ts"))
    this_update = _num(r.get("thisUpdate"))
    next_update = _num(r.get("nextUpdate"))
    if ts and this_update is not None and next_update is not None:
        yield ProfileEvent(ts=ts, parameter="ocsp_cache_lifetime",
                           value=next_update - this_update)


def _notice(r: dict, category: str = "") -> Iterator[Event]:
    ts = _f(r.get("ts"))
    if ts:
        yield AlertEvent(ts=ts, ip=_s(r.get("id.orig_h") or r.get("src")),
                         peer_ip=_s(r.get("id.resp_h") or r.get("dst")),
                         category=category or _s(r.get("note")),
                         detail=_s(r.get("msg") or r.get("name")))


def _weird(r: dict) -> Iterator[Event]:
    return _notice(r, category="weird")


def _capture_loss(r: dict) -> Iterator[Event]:
    """Zeek's ACK-gap loss estimate -> SensorHealthEvent, only when it is set.

    `_num`, not `_f`: an unset `percent_lost` would otherwise manufacture an
    *observed 0.0% loss* event — evidence of a clean window invented out of no
    evidence at all (CONVENTIONS §3).
    """
    ts = _f(r.get("ts"))
    value = _num(r.get("percent_lost"))
    if ts and value is not None:
        yield SensorHealthEvent(ts=ts, kind="capture_loss", value=value,
                                detail=_s(r.get("peer")))


def _stats(r: dict) -> Iterator[Event]:
    """Zeek's own process metrics -> SensorHealthEvent, one per reported counter.

    Richer than capture_loss alone (which only sees ACK gaps): pkts_dropped, pkt_lag,
    pkts_proc and bytes_recv all come from the packet source. Each is emitted ONLY
    when the sensor actually set it — some sources leave a counter unset, and a `-`
    there means unknown, not zero. `_num` enforces that.

    MEASURED on a `harvest`-derived cold tier (`/tmp/aximprint-zeek-test/`, 12
    windows, 39 `stats.log` rows total, ungated per CONVENTIONS §3): `pkts_dropped`/
    `pkt_lag` are `-` in EVERY row, because offline `zeek -r <pcap>` replay
    (`harvest`'s own mode, `docs/PLAN_metadata_harvester.md`) has no LIVE link
    counters to report. This is a property of the SENSOR MODE, not a parser defect —
    `_stats` correctly declines to invent a zero for them, same discipline `_num`
    always enforces. `_capture_loss` (above) is therefore the ONLY loss signal on a
    `harvest`-derived cold tier, which is now the primary ingest path. Flagged as a
    candidate for a new `docs/OPEN_QUESTIONS.md` entry (not yet numbered — the doc
    stage's call): this stem structurally cannot supply loss/lag evidence at that
    vantage, and nothing else currently names that gap.

    `pkts_proc`/`bytes_recv`, in contrast, are ALWAYS set on that same baseline — the
    only `stats.log` fields that actually produce an event there, and the practical
    "did the sensor see traffic" signal at that vantage (distinct from `capture_loss`,
    which is the LOSS signal, not a presence one). Joined as kinds later than
    `pkts_dropped`/`pkt_lag` (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3, code
    review addendum, user-ruled) once it was measured that they are safe to aggregate:
    see `windowing.windows.SENSOR_HEALTH_AGGREGATION`'s own comment for why both are
    reduced with `sum`, not `max` — they are PER-INTERVAL DELTAS on this baseline
    (`pkts_proc` visibly decreases within a single window), not cumulative counters,
    and that distinction decides the whole aggregation.
    """
    ts = _f(r.get("ts"))
    if not ts:
        return
    peer = _s(r.get("peer"))
    for kind in ("pkts_dropped", "pkt_lag", "pkts_proc", "bytes_recv"):
        value = _num(r.get(kind))
        if value is not None:
            yield SensorHealthEvent(ts=ts, kind=kind, value=value, detail=peer)


#: Log stem -> parser. The ONLY route into the event stream; no fall-through.
_PARSERS: dict[str, Callable[[dict], Iterator[Event]]] = {
    "conn": _conn,
    "dhcp": _dhcp,
    "dns": _dns,
    "kerberos": _kerberos,
    "known_hosts": _known_hosts,
    "ntlm": _ntlm,
    "radius": _radius,
    "rdp": _rdp,
    "rfb": _rfb,
    "redis": _redis,
    "websocket": _websocket,
    "syslog": _syslog,
    "mqtt_connect": _mqtt_connect,
    "ssl": _ssl,
    "dce_rpc": _dce_rpc,
    "ldap_search": _ldap_search,
    "ldap": _ldap,
    "smb_mapping": _smb_mapping,
    "smb_files": _smb_files,
    "ntp": _ntp,
    "known_services": _known_services,
    "known_certs": _known_certs,
    "software": _software,
    "http": _http,
    "ssh": _ssh,
    "quic": _quic,
    "snmp": _snmp,
    "tunnel": _tunnel,
    "smtp": _smtp,
    "sip": _sip,
    "x509": _x509,
    "ocsp": _ocsp,
    "notice": _notice,
    "weird": _weird,
    "capture_loss": _capture_loss,
    "stats": _stats,
}

#: Log stem -> why it is skipped. Skipping is a named decision, never an accident:
#: a stem in neither table is reported as unrecognized (see `ZeekPlugin.events`).
_SKIP: dict[str, str] = {
    "telemetry": "Zeek-internal metric stream; nothing maps to the event model "
                 "(and it is the second-largest log in a real capture)",
    "loaded_scripts": "Zeek-internal script inventory; no ts field, no network facts",
    "packet_filter": "Zeek-internal BPF record; capture-layer concern, not aximprint's",
    "files": "file-analysis records; no event-model home today (see smb_files)",
    "pe": "file-analysis family, refines files' own skip — pe.log itself has only a file id, "
          "no connection tuple. Contested, not settled: files DOES carry the full 4-tuple and "
          "is_orig directly, and files.mime_type + pe.machine is a candidate "
          "image:/architecture source, so this skip has a named path back (Q26 candidate 3)",
    "analyzer": "Zeek's own diagnostics — which protocol analyzer ran or failed — not "
                "traffic. But a DISABLED analyzer is evidence of a protocol the sensor could "
                "not see, a state nothing else here represents, so this skip also has a named "
                "path back (Q28)",
    "reporter": "Zeek's own script errors and warnings (ts level message location — no "
                "connection fields to attribute them to anything); declined outright",
}

#: Log stem -> (columns a parser cannot work without, columns it reads if present).
#: Two purposes, both only meaningful for a *columnar* transport: it is the
#: projection pushed into the archive query (13 of conn.log's ~20 fields), and it
#: is what a declared archive schema is validated against.
#:
#: The split is not cosmetic. A missing REQUIRED column yields structurally
#: complete-looking events with empty endpoints — strictly worse than a parse
#: error (`LES-007`), so it is rejected. A missing OPTIONAL column is a legitimate
#: Zeek variation whose consequence is already honest: absent `orig_bytes` marks
#: every non-`S0`/`REJ` flow `counters_imputed` rather than publishing a zero.
_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # `local_orig`/`local_resp` are OPTIONAL, not required, and the split is the one this
    # table's own docstring describes. A Zeek with no `Site::local_nets` declared omits them
    # legitimately, and their absence is already honest downstream: `_bool` yields `None`,
    # `windowing/segments.py` imputes locality from address shape and marks the result
    # `imputed`. Requiring them would reject a real, if less informative, capture.
    "conn": (("ts", "id.orig_h", "id.resp_h", "id.orig_p", "id.resp_p", "proto",
              "conn_state"),
             ("service", "duration", "orig_bytes", "resp_bytes", "orig_pkts",
              "resp_pkts", "local_orig", "local_resp")),
    "dhcp": (("ts", "mac"),
             ("assigned_addr", "requested_addr", "host_name", "lease_time")),
    "dns": (("ts", "query"), ("answers", "TTLs")),
    "kerberos": (("ts", "client", "id.orig_h"), ()),
    "known_hosts": (("ts", "host"), ()),
    "ntlm": (("ts", "id.resp_h"),
             ("server_dns_computer_name", "server_nb_computer_name")),
    # `framed_addr` is REQUIRED, not optional, and mirrors `dhcp`'s required `mac`: it is this
    # stem's anchor, so an archive that dropped the column (plausible -- it is all-null on the
    # baseline) cannot represent what the log held, and refusing is the honest answer.
    "radius": (("ts", "framed_addr"), ("mac", "username", "tunnel_client", "result")),
    "rdp": (("ts", "id.resp_h"), ("id.resp_p",) + _RDP_ATTRS),
    "rfb": (("ts", "id.resp_h"), ("id.resp_p",) + _RFB_ATTRS),
    "redis": (("ts", "id.resp_h"), ("id.resp_p",) + _REDIS_ATTRS),
    "websocket": (("ts", "id.resp_h"), ("id.resp_p",) + _WEBSOCKET_ATTRS),
    "syslog": (("ts", "id.resp_h"), ("id.resp_p",) + _SYSLOG_ATTRS),
    "mqtt_connect": (("ts", "id.resp_h"), ("id.resp_p",) + _MQTT_ATTRS),
    "known_certs": (("ts", "host"), ("port_num",) + _KNOWN_CERTS_ATTRS),
    "known_services": (("ts", "host"), ("port_num", "port_proto", "service")),
    "ssl": (("ts", "id.resp_h"),
            ("id.resp_p", "server_name", "version", "ja3", "ja3s", "subject", "issuer")),
    # From here down, the optional tuple for every RESPONDER-keyed stem is
    # `("id.resp_p", *_STEM_ATTRS)` — the same named constant `_responder_service` (above)
    # passes to `_attrs()`, so the field list a parser reads and the columns a columnar
    # transport projects are written ONCE, not twice (`S5`, code review,
    # `docs/WORK_ORDER_zeek_stem_coverage.md` step 3).
    "dce_rpc": (("ts", "id.resp_h"), ("id.resp_p", *_DCE_RPC_ATTRS)),
    "ldap_search": (("ts", "id.resp_h"), ("id.resp_p", *_LDAP_SEARCH_ATTRS)),
    "ldap": (("ts", "id.resp_h"), ("id.resp_p", *_LDAP_OP_ATTRS)),
    "smb_mapping": (("ts", "id.resp_h"), ("id.resp_p", *_SMB_MAPPING_ATTRS)),
    "smb_files": (("ts", "id.resp_h"), ("id.resp_p", *_SMB_FILES_ATTRS)),
    "software": (("ts", "host"),
                 ("host_p", "software_type", "name", "version.major", "version.minor",
                  "version.minor2", "version.minor3", "version.addl", "unparsed_version")),
    "ntp": (("ts", "id.resp_h"), ("id.resp_p", *_NTP_ATTRS)),
    "http": (("ts", "id.resp_h"), ("id.resp_p", *_HTTP_ATTRS)),
    "ssh": (("ts", "id.resp_h"), ("id.resp_p", *_SSH_ATTRS)),
    "quic": (("ts", "id.resp_h"), ("id.resp_p", *_QUIC_ATTRS)),
    "snmp": (("ts", "id.resp_h"), ("id.resp_p", *_SNMP_ATTRS)),
    "tunnel": (("ts", "id.resp_h"), ("id.resp_p", *_TUNNEL_ATTRS)),
    "smtp": (("ts", "id.resp_h"), ("id.resp_p", *_SMTP_ATTRS)),
    "sip": (("ts", "id.resp_h"), ("id.resp_p", *_SIP_ATTRS)),
    "x509": (("ts",), _X509_ATTRS),
    # No `id.resp_h`-shaped required tuple: `ocsp.log` names no host at all (`Q29`), so what is
    # actually required is the pair the parser SUBTRACTS -- a manifest missing either one would
    # let `_ocsp` silently emit zero events from a log full of rows (the same "worse than a parse
    # error" failure `LES-007` names for an empty-endpoint conn row, one field short here instead
    # of an address).
    "ocsp": (("ts", "thisUpdate", "nextUpdate"), ()),
    "notice": (("ts",),
               ("id.orig_h", "id.resp_h", "src", "dst", "note", "msg", "name")),
    "weird": (("ts",), ("id.orig_h", "id.resp_h", "src", "dst", "name", "msg")),
    "capture_loss": (("ts", "percent_lost"), ("peer",)),
    # `_stats` itself reads only `peer`/`pkts_dropped`/`pkt_lag`/`pkts_proc`/`bytes_recv`
    # (the other 21 are collected but not yet read by anything — a deliberate, narrow
    # departure from every OTHER entry in this table, which lists exactly what its parser
    # reads). Widened for forensic completeness at the ARCHIVE/collection layer
    # (CONVENTIONS §3; code review, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3): the
    # cold tier should not silently narrow what a future reader can reach for, even where
    # `_stats` itself has nowhere to put a value yet (`SensorHealthEvent` has no attrs bag,
    # and minting a health-event kind per field is explicitly not decided). If `_stats` is
    # ever extended to use one of the other 21, the column is already flowing on both
    # transports — no second round of "the archive doesn't project this" needed.
    "stats": (("ts",),
              ("peer", "pkts_dropped", "pkt_lag", "pkts_proc", "bytes_recv", "mem",
               "pkts_link", "pkts_filtered", "events_proc", "events_queued",
               "active_tcp_conns", "active_udp_conns", "active_icmp_conns", "tcp_conns",
               "udp_conns", "icmp_conns", "timers", "active_timers", "files",
               "active_files", "dns_requests", "active_dns_requests", "reassem_tcp_size",
               "reassem_file_size", "reassem_frag_size", "reassem_unknown_size")),
}

# A parser with no column declaration would silently project nothing on the
# archive transport and produce zero events from a log full of rows. The tables
# are two halves of one fact, so they are checked against each other at import
# rather than trusted to stay in step (`LES-017`: enumerate every path, do not
# assume the quiet one is absent).
assert set(_COLUMNS) == set(_PARSERS), (
    "_COLUMNS and _PARSERS disagree: "
    f"parsers without columns {sorted(set(_PARSERS) - set(_COLUMNS))}, "
    f"columns without parsers {sorted(set(_COLUMNS) - set(_PARSERS))}")


def required_columns(stem: str) -> tuple[str, ...]:
    """Columns whose absence makes `stem` unparseable. Used by archive validation."""
    return _COLUMNS[stem][0]


def wanted_columns() -> dict[str, list[str]]:
    """Every column any parser reads, per log stem — the columnar projection."""
    return {stem: [*req, *opt] for stem, (req, opt) in _COLUMNS.items()}


# -- streaming merge ------------------------------------------------------------

def _ts(ev: Event) -> float:
    return ev.ts


def _in_order(events: Iterator[Event], stem: str, horizon: float, max_rows: int,
              stats: _LogStats) -> Iterator[Event]:
    """Yield one log's events in non-decreasing ts using a bounded reorder buffer.

    A Zeek log is only *approximately* ts-sorted: conn.log rows are written when a
    connection closes but keyed on when it started, so a long flow surfaces after
    shorter ones that began later. The displacement that causes is a *duration* —
    on the reference capture, up to 169.6 s of ts (878 rows at ~6 rows/s) — and it
    does not shrink as line rate rises. So the buffer holds a row until it is
    `horizon` seconds behind the newest ts seen, which makes the guarantee
    "disorder narrower than `horizon` seconds is repaired" at any line rate.

    `max_rows` is a *memory* backstop, not the guarantee: peak memory is O(open
    files x max_rows). When it forces a flush the horizon is not being honored,
    and `stats.capped` records that rather than letting it pass unseen.

    This is a thin wrapper over `sources.reorder_by_key` (the identical shape,
    generalized so `LogDirSource`'s window-tree mode can reuse it one layer
    lower, over raw rows instead of parsed events, `Q2`) — everything about
    THIS function's own behavior is unchanged from before that generalization:
    ties still break on the event's position within its stream, so output order
    is a pure function of the input bytes (brief §5.4), and disorder wider than
    the horizon still cannot be repaired — each unrepairable event is counted
    AND announced as a `SensorHealthEvent(kind="ingest_unordered")`, so the
    window whose features are built from it carries the asterisk (brief §5.4
    #5) instead of reading clean. Constructing that event is this function's
    own job, not `reorder_by_key`'s: the shared helper only reports how far
    behind an item emerged, because it must stay ignorant of the event model
    (`sources.py`'s own module docstring) to be reusable at the row level too.
    """
    for ev, displacement in reorder_by_key(events, _ts, horizon=horizon,
                                           max_rows=max_rows, stats=stats):
        if displacement is not None:
            # Same ts as the event it qualifies, so it lands in the same window.
            yield SensorHealthEvent(ts=ev.ts, kind="ingest_unordered",
                                    value=displacement, detail=stem)
        yield ev


@register
class ZeekPlugin(IngestPlugin):
    name = "zeek"

    #: how far back the in-file reorder can repair disorder — the actual guarantee
    reorder_horizon_seconds: float = REORDER_HORIZON_SECONDS
    #: memory backstop for that buffer; peak memory is this x open files
    reorder_rows: int = REORDER_ROWS
    #: stderr progress cadence in rows (0 silences progress *and* the summary)
    progress_rows: int = PROGRESS_ROWS

    def __init__(self):
        #: per-stem run telemetry, populated as `events()` is consumed
        self.log_stats: dict[str, _LogStats] = {}
        #: `IngestPlugin.unparsed_stems`'s contract, satisfied here: reset on every
        #: `events()` call (same discipline as `log_stats`, immediately below), then
        #: set once `unparsed` is computed inside `events()` -- before this instance's
        #: `events()` has been iterated even once, this stays the declared "not yet
        #: known" default.
        self.unparsed_stems: dict[str, int] | None = None
        #: `IngestPlugin.unrecognized_stems`'s contract (code review `S4`, additive to
        #: D5) -- same reset/set discipline as `unparsed_stems`, one line above.
        self.unrecognized_stems: dict[str, int] | None = None

    def probe_vantage(self, source: str):
        """`IngestPlugin.probe_vantage`, satisfied by reading the harvest manifest.

        One delegation, no logic: `sources.source_vantage` already knows which of the three locator
        shapes can carry a manifest and which (the Parquet archive) structurally cannot. Keeping
        the transport knowledge there rather than here is the same separation this package already
        makes everywhere else — `sources.py` locates and decodes, this module owns semantics.
        """
        return source_vantage(source)

    # -- reporting (stderr only; nothing here may reach a feature) --------------

    def _say(self, msg: str) -> None:
        print(f"[zeek] {msg}", file=sys.stderr, flush=True)

    def _summary(self) -> None:
        if not self.progress_rows:
            return
        rows = sum(s.rows for s in self.log_stats.values())
        events = sum(s.events for s in self.log_stats.values())
        self._say(f"done: {rows:,} rows -> {events:,} events "
                  f"across {len(self.log_stats)} log type(s)")
        for stem in sorted(self.log_stats):
            s = self.log_stats[stem]
            notes = []
            if s.unordered:
                notes.append(f"{s.unordered:,} row(s) displaced past the "
                             f"{self.reorder_horizon_seconds:.0f}s reorder horizon")
            if s.capped:
                notes.append(f"{s.capped:,} flush(es) forced by the "
                             f"{self.reorder_rows}-row memory cap — the horizon was "
                             f"not honored for them")
            if s.source_capped:
                # `M2`: this stem's window-tree mode INNER pass, not the pass
                # right above — a different budget (`sources._merged_rows`'s
                # per-substream aggregate share, `M1`) forced these, and they
                # would otherwise have no health signal at all (`LES-004`,
                # `LES-006`).
                notes.append(f"{s.source_capped:,} flush(es) forced by the "
                             f"per-window-directory memory budget inside the "
                             f"window-tree merge — the horizon was not "
                             f"honored for them either")
            if s.malformed:
                notes.append(f"{s.malformed:,} malformed row(s) skipped")
            # The generic drift tripwire. A required-column check can only catch
            # the schema breakages someone anticipated; this one catches the rest,
            # because every way a log can silently stop mapping ends here. Rows
            # with no events is a coverage claim of zero, and it should never pass
            # as quietly as a log that legitimately had nothing to say.
            if s.rows and not s.events:
                notes.append("PRODUCED NO EVENTS from a non-empty log — parser and "
                             "source schema likely disagree")
            note = f"  [{'; '.join(notes)}]" if notes else ""
            self._say(f"  {stem:<16} {s.rows:>10,} rows -> {s.events:>10,} events{note}")

    # -- event stream ----------------------------------------------------------

    def _stream_events(self, stream: ZeekStream, parse, stats: _LogStats) -> Iterator[Event]:
        every = self.progress_rows
        horizon = self.reorder_horizon_seconds
        stem = stream.stem
        for r in stream.rows(stats):
            stats.rows += 1
            # A transport that sorted its rows carries the sensor's ORIGINAL
            # disorder in a column, because sorting erased the only other way to
            # see it (SPEC "The disorder contract"). Re-emit it here so a window's
            # health record reads the same on both transports: a sorted archive
            # must not make a disordered sensor look clean (`LES-006`, `LES-013`).
            # Absent on the text path, where `_in_order` still judges order live.
            displaced = r.get(DISPLACEMENT_COLUMN)
            if displaced is not None and displaced > horizon:
                ts = _f(r.get("ts"))
                if math.isfinite(ts):
                    stats.unordered += 1
                    # Same ts as the row it qualifies, so it lands in the same
                    # window — and emitted before that row's events, matching the
                    # order `_in_order.flush` produces on the text path.
                    yield SensorHealthEvent(ts=ts, kind="ingest_unordered",
                                            value=float(displaced), detail=stem)
            dropped = False
            for ev in parse(r):
                # `_f("nan") -> nan` and every parser's `if not ts` guard accepts it
                # (NaN and inf are truthy). Reject it HERE, where the row is still in
                # hand, instead of raising inside an accumulator three layers down
                # with nothing left to name the offending file.
                if not math.isfinite(ev.ts):
                    dropped = True
                    continue
                stats.events += 1
                yield ev
            if dropped:
                stats.malformed += 1
            if every and stats.rows % every == 0:
                self._say(f"{stem}: {stats.rows:,} rows -> {stats.events:,} events")

    def _validate_declared_schema(self, src: ZeekSource) -> None:
        """Check a transport's *declared* schema against what the parsers need.

        Structural validity is the source's job; whether a declared column set can
        actually feed a parser is semantics, and semantics live here with
        `_PARSERS`. A transport that declares no schema (text logs, where a row's
        shape is only known once it is read) has nothing to check.
        """
        manifest = getattr(src, "manifest", None)
        if manifest is None:
            return
        for stem, spec in sorted(manifest.logs.items()):
            if stem not in _COLUMNS:
                continue  # not parsed by this build; reported as skipped/unrecognized
            declared = set(spec.get("columns") or ())
            missing = [c for c in required_columns(stem) if c not in declared]
            if missing:
                raise ArchiveContractError(
                    f"{manifest.path}: logs.{stem} is missing required column(s) "
                    f"{missing}. Without them the parser still yields events — with "
                    f"empty endpoints — so this is refused rather than read "
                    f"(SPEC 'Columns'; `LES-007`)")

    def events(self, source: str, ts_from: float | None = None,
               ts_to: float | None = None) -> Iterator[Event]:
        # Configured bound reaches LogDirSource too: its window-tree mode does
        # its own per-file reorder pass one layer down (`sources.py`), and a
        # test overriding `self.reorder_rows` etc. must reach it the same way
        # it already reaches this method's own `_in_order` calls below.
        src = open_source(source, ts_from, ts_to,
                          reorder_horizon_seconds=self.reorder_horizon_seconds,
                          reorder_rows=self.reorder_rows)
        discovered = src.streams(wanted_columns())
        self._validate_declared_schema(src)
        for note in src.notes():
            self._say(note)

        self.log_stats = {}
        # Reset for this call. Unlike `log_stats` (mutated INCREMENTALLY across the
        # whole iteration below, via `setdefault`/`+=` on every row -- it genuinely
        # needs a fresh start each call), `unparsed_stems`/`unrecognized_stems` are
        # each set at most ONCE per call, a few lines down, and only when
        # `src.inventory_is_complete` -- an incomplete-inventory source
        # (`ParquetArchiveSource`) never assigns either at all, and this reset is
        # what that branch relies on to answer `None` rather than silently carrying
        # over an EARLIER call's dict (a plugin instance reused across two
        # different sources, one complete and one not, is exactly the case this
        # line exists for -- see `tests/test_zeek_ingest.py::test_unparsed_stems_
        # resets_across_repeated_events_calls` and `tests/test_zeek_parquet_source
        # .py::test_unparsed_stems_reset_survives_reusing_one_plugin_across_a_
        # complete_then_incomplete_source`).
        self.unparsed_stems = None
        self.unrecognized_stems = None
        streams: list[Iterator[Event]] = []
        for stream in discovered:
            parse = _PARSERS.get(stream.stem)
            if parse is None:
                continue  # named below, from the source's own inventory
            stats = self.log_stats.setdefault(stream.stem, _LogStats())
            streams.append(_in_order(self._stream_events(stream, parse, stats),
                                     stream.stem, self.reorder_horizon_seconds,
                                     self.reorder_rows, stats))

        # Reported from the inventory rather than from `discovered`, because a
        # transport is free to not even open a stream it was given no columns for
        # — and an unrecognized log type must be named whether or not it was read.
        unparsed = {s: n for s, n in src.inventory.items() if s not in _PARSERS}
        # `IngestPlugin.unparsed_stems` (`docs/WORK_ORDER_zeek_stem_coverage.md` D5):
        # every stem the source reported that has no route into `_PARSERS` -- a
        # declared `_SKIP` entry AND a genuinely unrecognized stem both count (D5's
        # own wording is "present in the input and not parsed", with no carve-out
        # for a skip that was merely decided rather than merely unmapped; the
        # decision's own reason stays one lookup away, in `_SKIP` itself, for
        # whoever wants it).
        #
        # Gated on `src.inventory_is_complete` (`sources.ZeekSource`) -- NOT assigned
        # unconditionally -- because `unparsed` is only as trustworthy as `src
        # .inventory` itself. `ParquetArchiveSource.inventory` can only ever contain
        # stems its manifest declares, and the manifest is written by a converter
        # whose own stem gate is `_PARSERS`'s key set exactly (`sources.py`'s own
        # comment on `ParquetArchiveSource.inventory_is_complete`) -- so `unparsed`
        # would be `{}` on EVERY archive-transport run regardless of what the
        # capture actually contained, a false "audited, nothing left over" this
        # gate exists to refuse. When the source cannot back that claim,
        # `self.unparsed_stems` is left at its reset default (`None`, above) instead
        # of being set to a dict this build cannot trust.
        #
        # Either way -- assigned or left `None` -- this is the ENTIRE mechanism a
        # caller needs for correctness on window 1: the DECISION of what to do runs
        # here, in this generator's OWN body, strictly before the `yield from`
        # below, so it has already executed, in full, by the time `next()` is
        # called on this generator for the very FIRST time (a generator function
        # does not suspend until it reaches a `yield`, and the first one in this
        # function is several lines below). A caller that starts consuming
        # `events()` at all -- even a caller whose very first `next()` raises
        # `StopIteration` because there is nothing to yield -- has therefore
        # already forced this decision to complete. `WindowManager.process()`'s own
        # `for ev in events:` loop cannot close (or ever open) a window without
        # doing exactly that pull, so by construction no window -- including the
        # first -- can close before this has already run (`cli._pipeline` wires the
        # callable that reads it).
        if src.inventory_is_complete:
            self.unparsed_stems = unparsed
            # `IngestPlugin.unrecognized_stems` (code review `S4`, additive to D5,
            # no reversal): the SUBSET of `unparsed` with no `_SKIP` entry -- a stem
            # this build has never even heard of, as opposed to one it declined BY
            # NAME. Gated on the identical `inventory_is_complete` condition as
            # `unparsed_stems`, for the identical reason (it is a subset of
            # `unparsed`, so it is exactly as trustworthy and exactly as
            # untrustworthy) -- and computed here, alongside it, so the SAME
            # single synchronous point before `events()`'s own first `yield`
            # makes it correct for window 1 too, with no second sequencing
            # argument to make.
            self.unrecognized_stems = {s: n for s, n in unparsed.items() if s not in _SKIP}
        for stem in sorted(s for s in unparsed if s in _SKIP):
            self._say(f"skip {stem} ({unparsed[stem]} {src.unit}(s)): {_SKIP[stem]}")
        for stem in sorted(s for s in unparsed if s not in _SKIP):
            # Fail loud, fail closed: not parsed (closed) and named (loud). A new
            # Zeek log type is a coverage gap to decide on, not a silent drop.
            self._say(f"UNRECOGNIZED log type '{stem}' ({unparsed[stem]} "
                      f"{src.unit}(s)) — not parsed; add it to _PARSERS or name "
                      f"it in _SKIP")
        if not streams:
            self._say(f"no parsable zeek logs under {source} — 0 events")

        # Merge is exact given per-stream order: `heapq.merge` breaks ties on the
        # position of the source iterator in this list, and discovery order is
        # sorted, so equal timestamps resolve the same way on every run.
        try:
            yield from heapq.merge(*streams, key=_ts)
        finally:
            self._summary()
            close = getattr(src, "close", None)
            if close is not None:
                close()
