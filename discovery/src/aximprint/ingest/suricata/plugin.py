"""Suricata ingest plugin — what an eve.json record *means*.

**Where records come from is `sources.py`'s job, not this file's** (that module's own
docstring states the rule; `ingest/zeek/plugin.py`'s module docstring states the
identical rule for the sibling plugin this package mirrors). A source locates and
decodes; this module owns the parser table, the ordering guarantee, and the merge.

**The stem analogue is `event_type`, not a filename.** Zeek splits by log FILE — one
stem per stream, so a directory listing is already a census. Suricata puts every record
type in ONE stream (`eve.json`) and discriminates on the `event_type` field carried
inside each JSON line. Coverage is still one table with no fall-through (`_PARSERS` /
`_SKIP`, the Zeek naming, per the task brief): an `event_type` in neither is *reported*
as unrecognized and left unparsed, per "fail loud, fail closed" (CONVENTIONS §3).

Breadth per the ratified parity table (coordinator ruling, folded into this build rather
than deferred — there is no second dispatch for it). Every kind on the right is the
**Zeek-side** kind string already declared in `windowing.windows.SERVICE_PROJECTIONS` /
`IDENTITY_PROJECTIONS` / `SENSOR_HEALTH_AGGREGATION`, reused verbatim so this plugin
inherits every downstream projection for free rather than minting a parallel vocabulary
that would silently land in an `unmodelled_*_kinds` counter:

  flow      -> FlowEvent
  alert     -> AlertEvent
  stats     -> nothing, today                      every counter declined pending one
                                                    measurement — see `_stats` (`S5`)
  anomaly   -> AlertEvent(category=weird)          Suricata's protocol-oddity flag; the
                                                    direct analogue of Zeek's `weird.log`
  dns       -> IdentityEvent(dns)                  hostname<->answer ip (v2 nested answers[]
                                                    and the v1 flat single-answer shape)
  dhcp      -> IdentityEvent(dhcp)                 mac<->ip (+ lease)
  arp       -> IdentityEvent(arp)                  mac<->ip, SENDER side only — see `_arp`
  krb5      -> IdentityEvent(kerberos)             principal<->client ip
  tls       -> ServiceEvent(ssl)                   server_name / version / ja3s
  http      -> ServiceEvent(http)                  host / uri / user_agent / ...
  ssh       -> ServiceEvent(ssh)                   server banner
  smb       -> ServiceEvent(smb)                   share_type / share / dialect
  snmp      -> ServiceEvent(snmp)                  version / community
  sip       -> ServiceEvent(sip)                   method / uri / code / reason —
                                                    collected, never projected (`R1`)
  smtp      -> ServiceEvent(smtp)                  helo / mail_from / rcpt_to — collected,
                                                    never projected (`R1`, see `_smtp`)
  dcerpc    -> ServiceEvent(dce_rpc)               endpoint (interface uuid) / operation (opnum)
  quic      -> ServiceEvent(quic)                  server_name / version
  ldap      -> ServiceEvent(ldap_op)               opcode / message_id / result

**`arp` was declined on a FALSE premise and is now parsed.** The first landing moved it
to `_SKIP` reasoning that "vanilla Suricata's documented eve.json event-type vocabulary
has no `arp` event_type at all" — asserted from recollection, and wrong: `arp` is in
Suricata's own `etc/schema.json` (`src_ip`, `src_mac`, `dest_ip`, `dest_mac`, `opcode`,
`hw_type`, `proto_type`) and in its `eve-log: types:` list. That decline cost this
plugin the single highest-value record type it has for the entity resolver, since
mac<->ip is HOST-scoped evidence and host-scoped evidence is the only class that may
merge two addresses (`Q1`, `windowing.entities.IDENTITY_SCOPES`). `_arp` parses the
SENDER side only, gated on a declared opcode — see its docstring for why the target side
of a request is a placeholder for an address nobody has seen yet.

**Every field spelling below was re-resolved against Suricata's own `etc/schema.json`,
not recollection**, after that check found six defects in the first landing: four SILENT
(`ldap.response.result_code` and `sip.reason_phrase` do not exist; `sip.user_agent` does
not exist at all; `arp` above), one MANGLING (`smtp.rcpt_to` is an array), and one
census gap (`mdns`/`llmnr`/`pop3`/`doh2` were in NEITHER table — the no-fall-through
discipline failing by omission rather than by fall-through). 40 of the 43 dotted paths
checked were correct, including every one the author had flagged as low-confidence,
which is the shape of the lesson: confidence was uncorrelated with correctness, so the
answer is a machine check against the schema, not more careful recollection.
`_EXPECTED_ATTRS` (see `_ROW_NOTES`, below) is the runtime half of the same guard —
it makes the NEXT wrong spelling a counted, named line in the closing summary instead of
an attr that is quietly never set.

**The `R1` keying bug, checked per the coordinator's explicit instruction.** `smtp` and
`sip` were declined from `windowing.windows.SERVICE_PROJECTIONS` on the Zeek side (user
-ruled code review, `LES-096`) because both attach a CLIENT-emitted field to a
RESPONDER-keyed event — Zeek's own `helo`/`user_agent` are the connecting CLIENT's own
self-announcement, but the event is keyed on `id.resp_h`, the server that merely
received it. Suricata's `smtp.helo`/`sip.method` carry the identical
hazard (`sip.user_agent` was in that list until the schema check found it does not
exist; `sip.method`/`smtp.helo` remain): they are the client's own protocol fields, and
this plugin's `_smtp`/`_sip`
still key their `ServiceEvent` on `dest_ip`/`dest_port` (the flow's responder, matching
every other application-protocol parser in this table) because THAT is the correct
subject for "a server exists here and speaks SMTP/SIP" — the fact these two stems ARE
admitted for. The bug the Zeek side hit was PROJECTING the client field as if it
described the responder, not the keying of the event itself. Both fields are collected
here on `attrs` (forensic completeness, CONVENTIONS §3) but land on a kind
(`windowing.windows._SERVICE_NOT_PROJECTED["smtp"]`/`["sip"]`) already declined from
`SERVICE_PROJECTIONS`, so nothing this build does can re-introduce `R1`'s actual mistake
without ALSO re-ruling that decline — which is out of scope here and untouched. See
`_smtp`/`_sip`'s own docstrings for the field-by-field accounting.

**Genuinely new evidence with no honest Zeek-side counterpart, declined rather than
minted a kind** (task brief: "if a Suricata record carries something with no honest
Zeek-side counterpart, report it as a decision rather than minting a kind"): Suricata's
`drop` event_type (packets its own inline/IPS engine discarded — a firewall decision,
not sensor-side capture loss the way `stats`/`capture_loss` are) and `frame`
(app-layer frame-level tracing) both name real facts Zeek has no analogue for at all.
Declined in `_SKIP` rather than minted a new `SensorHealthEvent`/`ServiceEvent` kind,
since minting one is a vocabulary decision this dispatch was not asked to make and would
land nowhere downstream (no `windowing` table declares it) regardless.

**Confidence disclosure, read before trusting a parser at scale**: no real Suricata
`eve.json` capture was available to build or check any of this against (CLAUDE.md's gate
on reading a real capture or a store derived from one). What HAS happened since the
first landing is that every dotted path here was resolved against Suricata's own
`etc/schema.json` (OISF master), which is a check on SPELLING and SHAPE — it says a key
exists and what type it has; it does not say how often that key is populated in real
traffic, nor which of several plausible readings a deployment's configuration produces.
So two things remain unmeasured and are named rather than assumed: **how populated** each
field is (Zeek's own `snmp.display_string` measured empty in 7/7 real rows — `R2` — and
nothing here has had that test), and **the two configuration-dependent readings**:
`stats`' cumulative-vs-delta counters (`_stats`, declined outright pending it) and
`sources.REORDER_HORIZON_SECONDS`' real displacement under `flow.start` keying.
`tcp.state`'s own value vocabulary is still not consulted at all —
`_flow_state_class` reads counters and RFC 793 flag bits instead, neither of which is a
sensor vocabulary that could be misspelled.

**The census contract is weaker than Zeek's, and this is a real, disclosed finding, not
a bug** (task brief: "if a single-pass streaming read makes a pre-first-yield census
impossible, that is a real finding: report it, do not fake it"). `ingest/zeek/plugin.py
::events` computes `unparsed_stems`/`unrecognized_stems` synchronously, before its own
first `yield`, because Zeek's stem inventory comes from a directory LISTING — reading
file names costs nothing and needs no row read at all (`LES-106`). Suricata's
`event_type` census cannot be taken that way: the discriminating field lives INSIDE
each row, so a genuine, complete census requires having read every row in every
discovered file — which a single streaming pass cannot know until it reaches the end.
Two options were weighed: (1) read every file TWICE — a cheap first pass extracting only
`event_type` to build the census before the real pass, matching Zeek's pre-first-yield
guarantee at the cost of doubling I/O on what this whole system is built to stream
through once; (2) accept a WEAKER, but never dishonest, contract — `unparsed_stems`/
`unrecognized_stems` stay `None` (unaudited) for the whole run, becoming final only once
every discovered file has actually been read to exhaustion. This build takes (2): a
double read is a real, measurable cost with nothing to weigh it against (no real
capture to measure the actual file sizes this matters for), while `None` is never a
false claim — `LES-006`'s "no evidence is not evidence of none" applies here exactly:
window 1 of a Suricata-sourced run reporting `unparsed_log_types=None` is the TRUTH
(this build has not yet audited the whole source), where a Zeek-shaped implementation
would have to either lie (report a partial census as final) or duplicate the read.
Concretely: `WindowManager`'s `unparsed_log_types_provider` (`cli._pipeline`) is called
FRESH AT EVERY WINDOW CLOSE, so a per-window health record from this plugin will show
`unparsed_log_types=None`/`unrecognized_log_types=None` for every window except one that
happens to close strictly AFTER the underlying source is fully drained (in practice,
only reachable via `WindowManager.finalize()`'s trailing window, or a source small
enough to fit inside one window) — this is a genuinely reduced signal relative to what
Zeek's plugin gives every window, disclosed here rather than silently inherited. The
RUN-level read (`cli._pipeline`'s post-loop block, "`plugin.unparsed_stems` is
guaranteed to hold its final, run-scoped value HERE") stays CORRECT for this plugin too:
that read happens only after `wm.process(plugin.events(...))`'s own `for` loop has
completed, which cannot happen without Python's `for`-loop protocol asking this
generator for one more item and receiving `StopIteration` — the exact point this
module's own `finally` block (see `SuricataPlugin.events`) finalizes both attributes.
So the `runs` table's per-run counts are exactly as trustworthy as Zeek's; only the
PER-WINDOW health record's honesty during a multi-window run is weaker, and it is weaker
by reporting `None` (true) rather than a wrong `{}`/partial dict (false).

**Two conditions, not one, must hold before the census is published** (`_publish_census`).
Draining proves every discovered file was READ; it does not prove the read produced
anything a census can be taken OVER. A file of well-formed JSON carrying no `event_type`,
or a file that is not JSON at all, drains perfectly and leaves `event_type_counts` empty
— and the first landing published `{}` for it, i.e. "audited, nothing left over", which
is exactly what a perfectly-covered run publishes. So a run in which NOT ONE row carried
an `event_type` refuses to publish at all: it reports loudly and leaves both fields
`None` (unaudited), which is the truth (`LES-006`).
"""
from __future__ import annotations

import heapq
import ipaddress
import math
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from aximprint.events import (
    AlertEvent,
    Event,
    FlowEvent,
    IdentityEvent,
    ServiceEvent,
)
from aximprint.ingest.base import IngestPlugin, register
from aximprint.ingest.reorder import event_ts, in_order, is_ip
from aximprint.ingest.suricata.sources import (
    REORDER_HORIZON_SECONDS,
    REORDER_ROWS,
    EveStream,
    SuricataSource,
    parse_suricata_timestamp,
)

#: Emit a stderr progress line every N rows of a discovered file; 0 disables reporting
#: (and, mirroring `ingest/zeek/plugin.py::PROGRESS_ROWS`, silences the closing summary
#: too).
PROGRESS_ROWS = 250_000


# -- value coercion --------------------------------------------------------------
#
# Suricata's JSON gives native types already: a missing key and a JSON `null` are the
# same absence (`dict.get` already collapses them), and a JSON string is never a
# stringified sentinel the way Zeek's TSV `"-"`/`"(empty)"` are. So there is no
# `_UNSET` vocabulary to normalize away here — `ingest/zeek/plugin.py`'s `_s`/`_num`/
# `_bool` are NOT imported (they are private, and tuned to a sentinel vocabulary this
# transport does not have); these are reimplemented, smaller, for Suricata's own shape.

def _s(v: object) -> str:
    """String coercion. A JSON array (e.g. `smtp.rcpt_to`) is comma-joined, matching
    Zeek's own vector-field convention (`ingest/zeek/plugin.py::_s`) so a multi-valued
    field reads the same way regardless of which sensor produced it.
    """
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_s(x) for x in v if x is not None)
    return str(v)


def _num(v: object) -> float | None:
    """Float, or `None` when unset — missing key, JSON `null`, or a non-numeric value all
    read as unknown, never coerced to `0.0` (CONVENTIONS §3, "imputed never masquerades
    as observed"; mirrors `ingest/zeek/plugin.py::_num`'s identical contract). `bool` is
    excluded explicitly: Python's `bool` is an `int` subclass, so `float(True) == 1.0`
    would otherwise silently accept a JSON boolean (`flow.alerted`, e.g.) as if it were a
    genuine numeric reading.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: object, default: int = 0) -> int:
    """Int with a default — for a field an accumulator downstream does arithmetic on,
    mirroring `ingest/zeek/plugin.py::_i`'s reasoning: re-imputing a `None` one layer
    further from the evidence is worse than reading an honestly-absent counter as the
    class's own zero-is-honest default (`_ZERO_IS_HONEST`, below, decides WHICH classes
    that is true for; this function does not).
    """
    n = _num(v)
    return int(n) if n is not None else default


def _f(v: object, default: float = 0.0) -> float:
    n = _num(v)
    return n if n is not None else default


def _dig(d: object, dotted_path: str) -> object:
    """`d["a"]["b"]` for `dotted_path="a.b"` — Suricata nests fields inside per-protocol
    sub-objects (`flow.state`, `tls.sni`), unlike Zeek's own JSON-lines transport, which
    uses a FLAT key literally containing a dot (`"id.orig_h"`) as one top-level dict
    entry. Every mapping table below (`_TLS_ATTRS` etc.) names a real nested path this
    way, applied to the RAW ROW `r`, not to a pre-extracted sub-object — so a path like
    `"tls.sni"` includes the `"tls."` prefix explicitly, every time.
    """
    cur = d
    for part in dotted_path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _mapped_attrs(r: dict, mapping: dict[str, str]) -> dict:
    """`{output_key: value}` for every `output_key -> dotted_source_path` in `mapping`
    whose value is actually set — the Suricata-side analogue of
    `ingest/zeek/plugin.py::_attrs()`, adapted for two real differences: Suricata nests
    fields inside sub-objects rather than flattening them into dotted top-level keys
    (`_dig`, above), and the OUTPUT key must match the ZEEK kind's own attrs vocabulary
    (`windowing.windows.SERVICE_PROJECTIONS`) rather than Suricata's own field name, so a
    projection already declared for the shared kind finds its value under the name it
    actually looks up (module docstring's "reuse the vocabulary" constraint) — e.g.
    `server_name`, never `sni`.
    """
    return {out: v for out, path in mapping.items() if (v := _s(_dig(r, path)))}


# `is_ip` is imported from `ingest/reorder.py`, not reimplemented (code review `S2`).
# It used to be copied in here on a "four lines, no state, no drift risk" argument;
# what that argument missed is that the RULE is the asset, not the lines — `LES-008`
# was paid for by a real defect (34.4% of dns-derived bindings on the reference capture
# were a NAME bound as an `ip` by a `count(".") == 3` heuristic), and a rule enforced in
# N copies is a rule that can be relaxed in one of them.


# -- flow state classification ----------------------------------------------------

#: TCP flag bit positions (RFC 793 §3.1) — protocol-level facts, not a Suricata
#: vocabulary, so these are stable regardless of Suricata version. (The first landing
#: of this line cited RFC 3168, which defines ECN/CWR in the two bits ABOVE these and
#: says nothing about SYN or RST — a wrong citation on a right constant.)
_TCP_FLAG_SYN = 0x02
_TCP_FLAG_RST = 0x04

#: Outcome classes where an unset counter honestly reads as 0: nothing was ever
#: exchanged, so "no bytes" is the observation, not an imputation. Now IDENTICAL to
#: `ingest/zeek/plugin.py::_ZERO_IS_HONEST` — it was narrower here (`{"attempted"}`)
#: only because `_flow_state_class` could not emit `"refused"` at all before code
#: review `M2`; it can now, and a refused connection exchanged nothing by definition,
#: exactly as it does on the Zeek side.
_ZERO_IS_HONEST = frozenset({"attempted", "refused"})

#: Counter/duration fields whose absence marks a flow's `counters_imputed` (outside
#: `_ZERO_IS_HONEST`) — named once so the imputation check and the field list it reads
#: cannot drift apart, mirroring `ingest/zeek/plugin.py::_CONN_COUNTERS`. `_flow` reads
#: each field EXACTLY ONCE, through this table, into one dict (code review nit: `_num`
#: used to be called three times per counter, and the four locals duplicated this tuple).
_FLOW_COUNTER_FIELDS = ("pkts_toserver", "pkts_toclient", "bytes_toserver", "bytes_toclient")


def _tcp_flags(value: object) -> int | None:
    """One of Suricata's accumulated per-direction hex flag bytes (`tcp.tcp_flags_ts`/
    `tcp_flags_tc` — every packet Suricata saw in that direction, OR'd together, for the
    whole flow) -> its integer value, or `None` when the field is absent/unparseable.

    `None` is load-bearing and is why this returns an `Optional[int]` rather than the
    per-bit boolean the first landing used. "This direction's flags carry no SYN" and
    "this row has no flag byte to read" are different findings, and only the first may
    drive a classification: a build or configuration that does not emit `tcp_flags_*`
    would otherwise make EVERY TCP flow look like it had no handshake, i.e. midstream
    (`LES-006`, "no evidence" is not "evidence of none").
    """
    try:
        return int(value, 16)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _flow_state_class(flow: dict, tcp: dict, pkts_toclient: float | None,
                      pkts_toserver: float | None) -> tuple[str, str]:
    """(state, state_class) — `events.FLOW_STATE_CLASSES`, from Suricata's OWN evidence
    rather than Zeek's single composite `conn_state` token (module docstring; task
    brief: "an unmappable state goes to unknown, never bucketed with a neighbour").

    Unlike Zeek, which hands one composite token, Suricata splits the same story across
    `flow.state` (new/established/closed/bypassed, protocol-agnostic), `flow.reason`
    (why the record was emitted: timeout/forced/shutdown — not consulted here, since
    neither value distinguishes an outcome this vocabulary needs), and — TCP only —
    `tcp.state` plus the per-direction accumulated flag bytes. No single field is Zeek's
    equivalent, so this COMBINES fields rather than keying a lookup table on one.

    Priority, highest-to-lowest confidence in what the fields actually prove:

    1. `pkts_toclient` itself unreadable (the row carries no usable `flow` object at
       all) -> `"unknown"`. A fact about the ROW, true for every protocol — `LES-006`:
       absence of evidence must not read as a (false) claim that the responder never
       answered.
    2. Responder sent nothing back (`pkts_toclient <= 0`) -> `"attempted"`: exactly
       `FLOW_STATE_CLASSES`'s own definition ("no reply from the responder at all"), and
       the one classification this function makes with the same confidence as Zeek's S0.
    3. ORIGINATOR sent nothing (`pkts_toserver <= 0`) while the responder did ->
       `"partial"`. `FLOW_STATE_CLASSES` calls this "only one direction seen — a vantage
       fact, not a host fact", which is precisely what a responder-only flow record is,
       and it is Zeek's own `RSTRH`/`SHR`. Read from a counter, not from a state string,
       so it needs no Suricata state vocabulary. Checked BELOW `attempted` deliberately:
       when both counters are zero there is no directional evidence at all, and
       "the responder never answered" is the claim the accumulators downstream act on.
    4. TCP flag readings, RFC-level facts about who sent what. **Each is gated on
       exactly the byte(s) it reads** (`_tcp_flags` returns `None` for an absent or
       unreadable byte): only the `"other"` reading needs both directions, and a
       reading whose own byte is missing is skipped rather than disabling the rest.
       Requiring both for all four — the first landing — sent a refused connection with
       no `tcp_flags_ts` to `"established"`, i.e. into `FLOW_ACCEPTED`, minting a
       listener on a host that refused: `M2`'s phantom listener, reachable again
       through a partial record. Readings, in order:
         * neither direction carries SYN -> `"other"`: Suricata began tracking this flow
           mid-connection, Zeek's own `OTH` ("midstream; no handshake observed"). This
           is checked FIRST among the flag readings because it disqualifies the
           handshake-shaped reasoning the next two lines depend on. It is also the ONE
           reading that genuinely needs both bytes, so with `tcp_flags_ts` absent a
           midstream responder-RST flow reads `"refused"` instead. That mis-reading is
           deliberately the safe direction: `"refused"` is OUTSIDE `FLOW_ACCEPTED`, so
           it under-claims (no listener minted) where the alternative over-claims.
         * responder RST **without** a responder SYN -> `"refused"`: the responder
           answered the attempt with a refusal and never offered a SYN/ACK — host up,
           port shut, Zeek's `REJ`. **This is the case the first landing of this
           function declared impossible** ("nothing in this shape tells them apart") and
           the accumulated flag byte does tell them apart: an ESTABLISHED-then-reset
           flow necessarily carries the responder's own SYN in `tcp_flags_tc`, and a
           refused one cannot.
         * RST from the originator -> `"reset_orig"`; RST from the responder (which, by
           the line above, must have SYN'd first) -> `"reset_resp"`.
    5. Otherwise -> `"established"` (responder answered; no RST, or not TCP at all).

    **What this still does not attempt**: `tcp.state`'s own value vocabulary is
    deliberately not consulted — this build has no verified, real-capture-checked table
    of its exact spellings the way `ingest/zeek/plugin.py::_CONN_STATE_CLASS` has for
    Zeek's `conn_state`, and guessing spellings into a lookup table would be
    indistinguishable, from the outside, from a measured one. Everything above reads
    counters and RFC 793 flag bits instead, neither of which is a sensor vocabulary.

    `state` (the verbatim string CONVENTIONS asks for) is `tcp.state` when TCP and
    present — the more granular of the two real candidates — else `flow.state`, which
    is present for every protocol and never empty on a well-formed Suricata flow record.
    """
    if pkts_toclient is None:
        return "", "unknown"
    # `tcp.state` wins when TCP and set (more granular); `flow.state` is the fallback
    # that also covers UDP/ICMP, which never carry a `tcp` object at all. Written as one
    # `or`-chain rather than nested ifs so there is exactly one place this precedence
    # lives, not two redundant checks that could silently drift apart.
    verbatim = (_s(tcp.get("state")) if tcp else "") or _s(flow.get("state"))
    if pkts_toclient <= 0:
        return verbatim, "attempted"
    if pkts_toserver is not None and pkts_toserver <= 0:
        return verbatim, "partial"
    if tcp:
        ts_flags = _tcp_flags(tcp.get("tcp_flags_ts"))
        tc_flags = _tcp_flags(tcp.get("tcp_flags_tc"))
        # Each reading is gated on exactly the byte(s) IT reads. The first landing
        # required BOTH for all four and fell through to `established` whenever either
        # was absent — which is `M2`'s phantom listener re-armed by a PARTIAL record: a
        # refused connection with no `tcp_flags_ts` read as `established`, which is
        # inside `events.FLOW_ACCEPTED`, which mints `listens_on <port>/tcp` on a host
        # that refused. `_tcp_flags` returns `Optional[int]` for exactly this reason —
        # one missing byte must cost only the readings that need it, not all of them.
        if (ts_flags is not None and tc_flags is not None
                and not (ts_flags | tc_flags) & _TCP_FLAG_SYN):
            return verbatim, "other"
        if (tc_flags is not None and tc_flags & _TCP_FLAG_RST
                and not tc_flags & _TCP_FLAG_SYN):
            return verbatim, "refused"
        if ts_flags is not None and ts_flags & _TCP_FLAG_RST:
            return verbatim, "reset_orig"
        if tc_flags is not None and tc_flags & _TCP_FLAG_RST:
            return verbatim, "reset_resp"
    return verbatim, "established"


def _flow_duration(flow: dict) -> float | None:
    """`flow.end - flow.start`, both Suricata's own ISO-8601 timestamps — `None` when
    either side does not parse or the pair is inverted, never a manufactured `0.0`
    (`LES-005`'s general form: any derived quantity that is a difference of two
    sensor-supplied numbers needs its own domain gate, applied here exactly as
    `ingest/zeek/plugin.py::_ocsp` applies it to `nextUpdate - thisUpdate`).

    Falls back to the coarser integer `flow.age` (seconds) when the precise pair is
    unavailable — `age` is Suricata's own pre-computed span for the identical fact at
    lower precision, still an OBSERVED value, not an imputation.
    """
    start = parse_suricata_timestamp(flow.get("start"))
    end = parse_suricata_timestamp(flow.get("end"))
    if start is not None and end is not None and end >= start:
        return end - start
    age = _num(flow.get("age"))
    if age is not None and age >= 0:
        return age
    return None


#: `app_proto` -> the `FlowEvent.service` label published for it. **Declared, so an
#: unmapped value is COUNTED rather than silently published** (code review `S3`,
#: surfaced through `_ROW_NOTES`/`SuricataPlugin.parse_notes` and the closing summary):
#: before this table any string Suricata put in `app_proto` became a service label with
#: nothing declaring it, which is the silent-default shape `SERVICE_PROJECTIONS` /
#: `IDENTITY_PROJECTIONS` / `PROFILE_PARAMETERS` each exist to prevent one layer over.
#:
#: **Every row maps to ITSELF, and that is the point, not an oversight.**
#: `FlowEvent.service` is each sensor's OWN L7 label ("L7 label if the sensor knew it")
#: and is deliberately NOT normalized across plugins — Zeek says `krb`, Suricata says
#: `krb5`, and `windowing` keys nothing on the string (`_add_flow` uses it only as an
#: opaque edge label). Rewriting Suricata's vocabulary into Zeek's here would be a
#: cross-sensor normalization decision with consequences well past this parser
#: (`scripts/verify_suricata_equivalence.py` asserts the two vocabularies differ, on
#: purpose), so this table declines to make it and says so. What it DOES buy is
#: coverage: a value not named here is a protocol this build has never seen Suricata
#: identify, and the counter says so instead of the label just appearing in a feature.
#:
#: Names resolved against Suricata's own `etc/schema.json` (the per-protocol top-level
#: object each `app_proto` names), never recollection — `SCHEMA_FINDINGS`' method, and
#: the reason six field spellings in this file were wrong before this round.
_APP_PROTO_TO_SERVICE: dict[str, str] = {
    p: p for p in (
        "arp", "bittorrent_dht", "dcerpc", "dhcp", "dnp3", "dns", "enip", "ftp",
        "ftp_data", "http", "http2", "ike", "krb5", "ldap", "llmnr", "mdns", "modbus",
        "mqtt", "nfs", "ntp", "pgsql", "pop3", "quic", "rdp", "rfb", "rpc", "sip",
        "smb", "smtp", "snmp", "ssh", "tftp", "tls", "websocket",
    )
}
#: Suricata's two non-protocol `app_proto` values, declared beside the protocols
#: precisely because they LOOK like sentinels and are not: `"failed"` means detection
#: was attempted and did not resolve, `"unknown"` means it was never attempted. Two
#: distinct claims the sensor is making, kept verbatim — folding either into blank
#: would be `LES-008`'s mistake, a proxy standing in for the sensor's own claim.
_APP_PROTO_TO_SERVICE.update({"failed": "failed", "unknown": "unknown"})


def _flow_notes(r: dict) -> Iterator[str]:
    """Row-level telemetry for a `flow` record (`_ROW_NOTES`): name any `app_proto`
    outside `_APP_PROTO_TO_SERVICE`. Reported, never rejected — the label is still
    published verbatim; what the count buys is that a protocol vocabulary this build
    has not accounted for surfaces as a number instead of silently entering a feature.
    """
    value = _s(r.get("app_proto"))
    if value and value not in _APP_PROTO_TO_SERVICE:
        yield f"unmapped app_proto {value!r}"


def _flow(r: dict) -> Iterator[Event]:
    """Suricata's periodic flow-summary record -> FlowEvent — the direct analogue of
    Zeek's `conn.log` (`ingest/zeek/plugin.py::_conn`).

    **`ts` is `flow.start`, not the record's own `timestamp`** (code review `M4`). Zeek
    keys a `conn.log` row on when the connection STARTED; Suricata's top-level
    `timestamp` on a flow record is when the record was EMITTED, i.e. when the flow
    closed or timed out. Keying on `timestamp` put a long flow in the window it ENDED
    in while Zeek put the same flow in the window it BEGAN in — two sensors watching one
    wire disagreeing about which hour a conversation happened in, which is a
    cross-sensor equivalence break and, worse, a claim about the network that is simply
    wrong for any flow spanning a window boundary. `timestamp` remains the fallback for
    a record with no parseable `flow.start`, since a flow event at approximately the
    right time beats dropping the row. The cost is disorder: keyed on start, records
    arrive later relative to their own key, bounded by Suricata's flow timeouts rather
    than by anything this plugin controls — `sources.REORDER_HORIZON_SECONDS` carries
    that disclosure and the path to measuring it.

    `originator_is_src=True` (the dataclass default, left untouched): Suricata's
    `src_ip`/`src_port` name whoever sent the flow's FIRST packet, by construction, for
    every protocol Suricata tracks flows for — the identical convention Zeek's
    `id.orig_h` already uses, so there is no flip to apply here.

    `service` <- `app_proto` through `_APP_PROTO_TO_SERVICE` (verbatim; see that table
    for what "declared" buys when every row maps to itself).

    `local_src`/`local_dst` stay at the dataclass default (`None`): Suricata has no
    `Site::local_nets`-equivalent verdict on this record, so `None` (sensor said
    nothing) is the honest value — imputing here would be the fabricated-evidence
    failure `windowing/segments.py` exists to own instead, not this parser (task brief;
    `FlowEvent.local_src`'s own docstring).

    Endpoint presence is checked explicitly (`src_ip`/`dst_ip` both truthy), unlike
    Zeek's own `_conn`, which gates on `ts` alone — a deliberate STRENGTHENING, not an
    inconsistency: an empty-endpoint `FlowEvent` is strictly worse than dropping the row
    (`LES-007`), and nothing else in this build protects against one for a sensor this
    project has no real-capture measurement of.
    """
    flow = r.get("flow") or {}
    ts = parse_suricata_timestamp(flow.get("start"))
    if ts is None:
        ts = parse_suricata_timestamp(r.get("timestamp"))
    src_ip, dst_ip = _s(r.get("src_ip")), _s(r.get("dest_ip"))
    if ts is None or not src_ip or not dst_ip:
        return
    tcp = r.get("tcp") or {}
    # Each counter read ONCE, through the one table that names them (code review nit).
    counters = {k: _num(flow.get(k)) for k in _FLOW_COUNTER_FIELDS}
    state, state_class = _flow_state_class(flow, tcp, counters["pkts_toclient"],
                                           counters["pkts_toserver"])
    honest_zero = state_class in _ZERO_IS_HONEST
    counters_missing = any(v is None for v in counters.values())
    duration = _flow_duration(flow)
    service = _s(r.get("app_proto"))
    yield FlowEvent(
        ts=ts, src_ip=src_ip, dst_ip=dst_ip,
        src_port=_i(r.get("src_port")), dst_port=_i(r.get("dest_port")),
        proto=_s(r.get("proto")).lower(),
        service=_APP_PROTO_TO_SERVICE.get(service, service),
        duration=duration if duration is not None else 0.0,
        bytes_out=_i(counters["bytes_toserver"]), bytes_in=_i(counters["bytes_toclient"]),
        pkts_out=_i(counters["pkts_toserver"]), pkts_in=_i(counters["pkts_toclient"]),
        state=state, state_class=state_class,
        counters_imputed=(not honest_zero and (counters_missing or duration is None)),
    )


# -- identity ----------------------------------------------------------------------

def _dhcp(r: dict) -> Iterator[Event]:
    """DHCP transaction -> IdentityEvent(dhcp), the direct analogue of
    `ingest/zeek/plugin.py::_dhcp`. `assigned_ip` (a completed lease) wins over
    `client_ip` (the same either-or shape Zeek's own parser uses for
    `assigned_addr`/`requested_addr`) — moderate confidence on the exact nested field
    names (module docstring); gated on `ip and mac` both being set, matching Zeek's
    identical positive gate, so an in-flight DISCOVER/OFFER with no confirmed binding
    yet mints nothing.
    """
    ts = parse_suricata_timestamp(r.get("timestamp"))
    dhcp = r.get("dhcp") or {}
    ip = _s(dhcp.get("assigned_ip")) or _s(dhcp.get("client_ip"))
    mac = _s(dhcp.get("client_mac"))
    if ts is not None and ip and mac:
        yield IdentityEvent(ts=ts, kind="dhcp", ip=ip, mac=mac,
                            hostname=_s(dhcp.get("hostname")),
                            lease_seconds=_f(dhcp.get("lease_time")))


def _krb5(r: dict) -> Iterator[Event]:
    """Kerberos exchange -> IdentityEvent(kerberos), the direct analogue of
    `ingest/zeek/plugin.py::_kerberos`. Bound at the CLIENT's own address (`src_ip` —
    Suricata's flow orig/resp convention, matching Zeek's `id.orig_h`), evidence
    observed AT THE ADDRESS ITSELF, not a third party's claim about it — the same
    `IDENTITY_PROJECTIONS["kerberos"]` confidence (0.9) this event rides applies for the
    identical reason.

    **`principal` composes `cname/realm`, with a SLASH** (code review `M1`) — and the
    separator is the whole fix, not a formatting preference. `IdentityEvent.principal`
    is consumed by exactly one thing, `windowing.entities.kerberos_identifier`, which
    parses an RFC 4120 principal as `component[/component...]` and takes the LAST
    component as the realm. Composed with `@` — this parser's first landing, copying the
    shape Zeek's `kerberos.log` prints — that function saw ONE component,
    `"ws000$@CORP.AXR.LAB"`, whose primary does not end in `$`, and classified every
    Suricata Kerberos record as a USER principal: `USER` scope anchors no host (D1), so
    **every machine-account anchor this parser produced was silently discarded** while
    the parser itself looked correct and the event's own field looked right. Measured on
    `scripts/verify_suricata_equivalence.py`'s fixture: 20 machine principals, 0 host
    anchors, and the gate recorded the resulting entity-kind divergence as an expected
    sensor difference. The lesson is in `IdentityEvent.principal`'s under-specification
    (the field says "principal" and does not say in whose grammar) — reported for
    `docs/OPEN_QUESTIONS.md` rather than fixed by widening the consumer, which is on
    another branch. **Assert on the CONSUMER's classification, never on this string.**

    A `cname` with no `realm` is published bare: inventing a separator with nothing on
    the far side of it would hand the parser a second component that does not exist.
    """
    ts = parse_suricata_timestamp(r.get("timestamp"))
    ip = _s(r.get("src_ip"))
    krb5 = r.get("krb5") or {}
    cname = _s(krb5.get("cname"))
    if ts is not None and ip and cname:
        realm = _s(krb5.get("realm"))
        yield IdentityEvent(ts=ts, kind="kerberos", ip=ip,
                            principal=f"{cname}/{realm}" if realm else cname)


#: ARP opcodes whose SENDER fields (`src_mac`/`src_ip`) are a self-assertion this
#: parser will bind. Declared, lowercased-for-comparison, and CLOSED: an opcode outside
#: this set mints nothing and is counted by name (`_arp_decline`), because Suricata's
#: schema types `arp.opcode` as a bare string with no enumeration, so the exact
#: spellings a given build writes cannot be resolved from the schema — failing closed
#: and loudly is the honest response to that, and the counter is the path back (if a
#: real capture shows numeric or capitalized opcodes, this table gains those rows; it
#: does not get replaced by a fall-through).
_ARP_SENDER_OPCODES = frozenset({"request", "reply"})

#: The hardware/protocol address families this parser will read a `src_mac`/`src_ip`
#: pair out of, same declare-and-fail-closed discipline as the opcodes above (code
#: review `S5`). ARP carries the two families in the record itself, and `src_mac` only
#: means "a 48-bit Ethernet address" when `hw_type` says so — Suricata writes the
#: unrecognized case as a bare `0x%04x` string, which would otherwise walk straight
#: into `IdentityEvent.mac` and, through `windowing.entities`' `NS_MAC`, into a merge
#: key. An absent or unruled value declines and is counted; it is never assumed.
_ARP_HW_TYPES = frozenset({"ethernet"})
_ARP_PROTO_TYPES = frozenset({"ipv4"})

_HEX = frozenset("0123456789abcdefABCDEF")


def parse_mac(value: str) -> str | None:
    """Six colon- or hyphen-separated hex octets -> the canonical lowercase, colon-form
    MAC. `None` for anything else — a **positive** gate that PARSES the value rather
    than pattern-matching its shape (`LES-008`, on the one field that becomes an entity
    merge key: `windowing.entities` takes `IdentityEvent.mac` verbatim into `NS_MAC`,
    so nothing downstream re-checks this).

    The predecessor was `not any(ch not in "0:.-" for ch in mac)`, a "does it contain a
    non-separator character" test read as a validity check, which accepted `"unknown"`,
    `"-"`, `"n/a"` and every other non-empty string a sensor might put in a field it
    could not fill — and, being a shape test, could not reject `ff:ff:ff:ff:ff:ff`
    either.

    Two domain rejections beyond the shape, both because a SENDER address is being
    asserted (`LES-110`'s general form: a successful parse proves the value is an
    address, never that it is the KIND of address the output means):

      * **group addresses** (`first octet & 1`) — broadcast `ff:ff:ff:ff:ff:ff` and every
        multicast address. No interface owns one, so binding one to an IP would merge
        every host that ever sent to it into a single entity.
      * **the all-zero address**, ARP's own "I do not know this yet" placeholder.

    Normalized to lowercase colon-form on the way out for the reason `LES-107` gives
    for computer names: `AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are one interface,
    and leaving the sensor's spelling verbatim hands the resolver two keys for it.
    """
    v = value.strip()
    sep = ":" if ":" in v else "-" if "-" in v else ""
    if not sep:
        return None
    parts = v.split(sep)
    if len(parts) != 6:
        return None
    octets: list[int] = []
    for part in parts:
        if len(part) != 2 or any(ch not in _HEX for ch in part):
            return None
        octets.append(int(part, 16))
    if octets[0] & 1:
        return None  # the I/G bit: a group (broadcast/multicast) address, never a sender
    if not any(octets):
        return None  # the all-zero placeholder names no interface
    return ":".join(f"{o:02x}" for o in octets)


def _is_bindable_sender_ip(value: str) -> bool:
    """Positive gate on "an address a host can claim as its own" — IPv4 (ARP's only
    protocol family here, `_ARP_PROTO_TYPES`), parsed not shape-matched, and neither
    the unspecified address (RFC 5227's probe: the sender is explicitly NOT claiming
    it), a multicast address, nor the 240.0.0.0/4 reserved block that
    `255.255.255.255` sits in. `LES-110`: an address that parses is not automatically
    an address of the kind this output means.
    """
    if not is_ip(value):
        return False
    addr = ipaddress.ip_address(value)
    if addr.version != 4:
        return False
    return not (addr.is_unspecified or addr.is_multicast or addr.is_reserved)


def _arp_decline(r: dict) -> str | None:
    """Why this `arp` row binds nothing — or `None` when it binds. **One
    implementation, two readers**: `_arp_notes` counts what it returns and `_arp` obeys
    it, because two copies of a gate this consequential drift apart silently and the
    counter is the declared path back for every one of these branches.

    Every decline is named. The first landing's `if opcode and ...` guard meant a
    missing or renamed `arp.opcode` key produced zero bindings AND zero notes — the
    counter that exists to disclose "this build does not understand this sensor's ARP
    records" was silent in precisely the case that needs it (`LES-006`).

    Note texts carry the offending value only for the sensor's own low-cardinality
    enum-ish fields (opcode, hw_type, proto_type). The address fields are deliberately
    summarized without their value: `SuricataPlugin.parse_notes` is a dict keyed on
    this string, and one key per rejected host address is an unbounded dimension
    (`LES-017`).
    """
    arp = r.get("arp") or {}
    opcode = _s(arp.get("opcode")).strip()
    if not opcode:
        return "arp record with no `arp.opcode` — binds nothing"
    if opcode.lower() not in _ARP_SENDER_OPCODES:
        return f"unruled arp opcode {opcode!r}"
    hw_type = _s(arp.get("hw_type")).strip()
    if hw_type.lower() not in _ARP_HW_TYPES:
        return (f"unruled arp hw_type {hw_type!r}" if hw_type else
                "arp record with no `arp.hw_type` — binds nothing")
    proto_type = _s(arp.get("proto_type")).strip()
    if proto_type.lower() not in _ARP_PROTO_TYPES:
        return (f"unruled arp proto_type {proto_type!r}" if proto_type else
                "arp record with no `arp.proto_type` — binds nothing")
    if not _is_bindable_sender_ip(_s(arp.get("src_ip"))):
        return ("arp sender ip is not a claimable unicast address (unspecified, "
                "multicast, broadcast/reserved, or unparseable) — binds nothing")
    if parse_mac(_s(arp.get("src_mac"))) is None:
        return ("arp sender mac is not a unicast 48-bit hardware address — binds "
                "nothing")
    if parse_suricata_timestamp(r.get("timestamp")) is None:
        return "arp record with an unreadable `timestamp` — binds nothing"
    return None


def _arp_notes(r: dict) -> Iterator[str]:
    note = _arp_decline(r)
    if note is not None:
        yield note


def _arp(r: dict) -> Iterator[Event]:
    """ARP -> IdentityEvent(arp), **the SENDER side only** — mac<->ip, the class of
    evidence `windowing.entities.IDENTITY_SCOPES` calls host-scoped (`Q1`), the only
    one that may merge two addresses. Verified present in Suricata's own
    `etc/schema.json` (`arp`: `src_ip`, `src_mac`, `dest_ip`, `dest_mac`, `opcode`,
    `hw_type`, `proto_type`) and in its `eve-log: types:` list.

    **This event_type was previously in `_SKIP` on a FALSE premise** — "vanilla
    Suricata emits no `arp` event_type at all" — asserted from recollection and
    contradicted by the schema. It is the highest-value record type here for the entity
    resolver, and `events.IdentityEvent.kind`'s own comment already lists `arp`.

    Every gate lives in `_arp_decline`, which is also what `_ROW_NOTES` counts, so a
    decline is never silent. Each exists because failing it mints a WRONG binding
    rather than merely a useless one:

    1. **Sender side only.** In an ARP *request* the `dest_mac` is a placeholder
       (typically all-zero) for an address the sender is ASKING about — binding it would
       assert that a host the sensor has never seen owns a hardware address that does
       not exist. The target side is meaningful only on a reply, and there it is the
       REPLIER's own sender fields that carry it, so the sender-only rule collects it
       anyway, one packet later.
    2. **A declared opcode** (`_ARP_SENDER_OPCODES`), and **declared address families**
       (`_ARP_HW_TYPES`/`_ARP_PROTO_TYPES`, code review `S5`). An unrecognized or
       missing value is a record shape this build has not ruled on; it is counted,
       never guessed at.
    3. **A claimable sender address** (`_is_bindable_sender_ip`) — an RFC 5227 probe's
       `0.0.0.0`, a multicast or broadcast address: parsed, then checked against what
       the domain permits (`LES-110`).
    4. **A parseable unicast hardware address** (`parse_mac`) — six hex octets, group
       bit clear, not all-zero, canonicalized to lowercase colon-form.

    **Downstream gap, named rather than papered over**: `windowing.entities
    .IDENTITY_SCOPES` and `windowing.windows.IDENTITY_PROJECTIONS` have no `arp` row
    yet, so today this event is COUNTED as `unscoped_identity_kinds` and anchors
    nothing — loud and honest, exactly what that counter is for (it is NOT also counted
    as `unmodelled_identity_kinds`: that one fires per OPENED binding, and an unscoped
    kind opens none), but not yet the merge evidence the record actually is.

    **Scoping `arp` as host evidence NEEDS A RULING; it is not a mechanical two-row
    edit, and an earlier version of this docstring said it was.** ARP's sender fields
    are only a self-assertion when one interface answers for its own address, and two
    ordinary deployments break that: **proxy ARP** puts a router's MAC beside an
    address belonging to some host behind it, and **VRRP/HSRP** share one virtual MAC
    (`00:00:5e:00:01:xx` / `00:00:0c:07:ac:xx`) across every member of a redundancy
    group. Host-scoped ARP with no many-addresses-per-MAC guard would therefore collapse
    N hosts into one entity — and `Q1`'s merges are FORWARD-ONLY (D2), so that
    collapse cannot be undone by a later observation. The guard, its threshold, and
    whether the virtual-MAC ranges are refused by name are all decisions for whoever
    owns `windowing/` and the user who ratified D2; recorded for
    `docs/OPEN_QUESTIONS.md` rather than assumed here. This parser producing the
    evidence is the half that had to land first — a table row pointing at a parser that
    does not exist would be the worse order.
    """
    if _arp_decline(r) is not None:
        return
    arp = r.get("arp") or {}
    ts = parse_suricata_timestamp(r.get("timestamp"))
    mac = parse_mac(_s(arp.get("src_mac")))
    if ts is None or mac is None:  # unreachable: `_arp_decline` gates both (assert-shaped)
        return
    yield IdentityEvent(ts=ts, kind="arp", ip=_s(arp.get("src_ip")).strip(), mac=mac)


def _dns_answer(ts: float, query: str, rrname: str, rdata: str, ttl: object) -> Iterator[Event]:
    """One DNS answer -> IdentityEvent(dns), shared by both eve DNS shapes below.
    `rrname` is the answer's OWN name (relevant for a CNAME chain's final A/AAAA record,
    which may not share the query's name) with `query` as the fallback — mirrors the
    query name Zeek's own `dns.log` row always carries. CNAME/MX/SRV/TXT-shaped `rdata`
    (a name, not an address) binds nothing the resolver can key on and is dropped rather
    than coerced (`ingest/zeek/plugin.py::_dns`'s identical `is_ip` gate, `LES-008`).
    """
    if is_ip(rdata):
        yield IdentityEvent(ts=ts, kind="dns", ip=rdata, hostname=rrname or query,
                            record_ttl=_f(ttl))


def _dns(r: dict) -> Iterator[Event]:
    """DNS query/answer -> IdentityEvent(dns). Suricata's eve DNS log has TWO
    documented shapes across versions: the current one (`dns.type == "answer"`, an
    `answers` LIST, each with its own `rrname`/`rdata`/`ttl`) and the older, pre-v2
    shape (one flat `rdata`/`rrtype`/`ttl` directly under `dns`, one JSON line per
    answer). Both are handled — `isinstance(answers, list)` is the positive gate
    deciding which, never a version-number check this build has no way to make
    reliably.
    """
    ts = parse_suricata_timestamp(r.get("timestamp"))
    dns = r.get("dns") or {}
    query = _s(dns.get("rrname"))
    if ts is None or not query:
        return
    answers = dns.get("answers")
    if isinstance(answers, list):
        for a in answers:
            if isinstance(a, dict):
                yield from _dns_answer(ts, query, _s(a.get("rrname")),
                                       _s(a.get("rdata")), a.get("ttl"))
    elif dns.get("type") == "answer":
        yield from _dns_answer(ts, query, query, _s(dns.get("rdata")), dns.get("ttl"))


# -- responder-keyed ServiceEvent parsers ------------------------------------------
#
# Every application-protocol parser below is keyed on the RESPONDER (`dest_ip`/
# `dest_port`) — Suricata's own flow orig/resp convention makes `dest_ip` the answering
# side for every protocol these parsers cover, matching Zeek's `id.resp_h` exactly
# (`_flow`'s own docstring). Factored through one shared body, mirroring
# `ingest/zeek/plugin.py::_responder_service` for the identical reason: the near
# -identical bodies this replaces are the one seam where a `src_ip`/`dest_ip` slip has
# nowhere to hide if written out N times instead of once (`LES-087`).

def _emit_responder_service(r: dict, kind: str, attrs: dict) -> Iterator[Event]:
    ts = parse_suricata_timestamp(r.get("timestamp"))
    ip = _s(r.get("dest_ip"))
    if ts is not None and ip:
        yield ServiceEvent(ts=ts, kind=kind, ip=ip, port=_i(r.get("dest_port")), attrs=attrs)


def _responder_service(r: dict, kind: str, mapping: dict[str, str]) -> Iterator[Event]:
    return _emit_responder_service(r, kind, _mapped_attrs(r, mapping))


#: `windowing.windows.SERVICE_PROJECTIONS["ssl"]` reads `server_name`/`version`/`ja3s` —
#: all three present here under those exact names. `ja3`/`subject`/`issuer` are
#: collected (forensic completeness) but not projected, matching Zeek's own `ssl` attrs
#: tuple exactly (`_SSL_ATTRS` there projects the identical three of its own six).
_TLS_ATTRS = {
    "server_name": "tls.sni",
    "version": "tls.version",
    "ja3": "tls.ja3.hash",
    "ja3s": "tls.ja3s.hash",
    "subject": "tls.subject",
    "issuer": "tls.issuerdn",
}


def _tls(r: dict) -> Iterator[Event]:
    """TLS handshake -> ServiceEvent(ssl) — Suricata calls its own eve event_type `tls`;
    the KIND stays `ssl` to match Zeek's vocabulary (module docstring), since
    `SERVICE_PROJECTIONS`/`IDENTITY_PROJECTIONS` are keyed on that string, not on either
    sensor's own log-file name.
    """
    return _responder_service(r, "ssl", _TLS_ATTRS)


#: `SERVICE_PROJECTIONS["http"]` reads only `host` — Suricata's `http.hostname` is the
#: same fact Zeek's `http.host` is (the Host: header / requested vhost), and the
#: identical acceptance argument applies: it is evidence about which vhost the
#: RESPONDER answers as, not a bare client self-announcement (`ingest/zeek/plugin.py
#: ::_http`'s own docstring). `user_agent` is collected, never projected — matching
#: Zeek's own `SERVICE_PROJECTIONS["http"]`, which also does not project it, for the
#: same client-authored-field reasoning `_smtp`/`_sip` document explicitly below.
_HTTP_ATTRS = {
    "host": "http.hostname",
    "uri": "http.url",
    "method": "http.http_method",
    "version": "http.protocol",
    "user_agent": "http.http_user_agent",
    "referrer": "http.http_refer",
    "status_code": "http.status",
    "response_body_len": "http.length",
}


def _http(r: dict) -> Iterator[Event]:
    return _responder_service(r, "http", _HTTP_ATTRS)


#: `SERVICE_PROJECTIONS["ssh"]` reads only `server` — Suricata's `ssh.server
#: .software_version` is read off the handshake WITH the responder itself, the same
#: evidence quality Zeek's own `server` field has (`ingest/zeek/plugin.py::_ssh`'s
#: docstring: "the entity it describes is the one that emitted it"). `client`/
#: `*_proto_version` are collected, never projected — this build has no `software.log`
#: equivalent to defer the client side to, but that is a coverage gap to name, not a
#: reason to attribute a client's own banner to the server it connected to.
_SSH_ATTRS = {
    "server": "ssh.server.software_version",
    "server_proto": "ssh.server.proto_version",
    "client": "ssh.client.software_version",
    "client_proto": "ssh.client.proto_version",
}


def _ssh(r: dict) -> Iterator[Event]:
    return _responder_service(r, "ssh", _SSH_ATTRS)


#: `SERVICE_PROJECTIONS["smb"]` reads `share_type`/`service` (either satisfies it).
#: `share_type` is Suricata's own field (moderate confidence, module docstring); no
#: confident `service`-equivalent was found (Zeek's own `share_type`/`service` pair
#: means "resource kind" / "SMB dialect-adjacent service label" — Suricata's closest
#: candidate, `dialect`, is a DIFFERENT fact, the wire protocol version, not a service
#: label, so it is collected but not mapped onto `service`'s name) — the projection
#: still fires on `share_type` alone, matching `_add_service`'s "any one declared key
#: present" gate.
_SMB_ATTRS = {
    "share_type": "smb.share_type",
    "share": "smb.share",
    "command": "smb.command",
    "dialect": "smb.dialect",
    "filename": "smb.filename",
}


def _smb(r: dict) -> Iterator[Event]:
    return _responder_service(r, "smb", _SMB_ATTRS)


#: `SERVICE_PROJECTIONS["snmp"]` reads `version`/`display_string` (either satisfies
#: it). No confident `display_string` (sysDescr) equivalent was found in Suricata's own
#: SNMP eve fields — Suricata's SNMP logger is protocol-structural (PDU type, OIDs),
#: not semantically extracting sysDescr the way Zeek's analyzer does — so only
#: `version` is mapped; the projection still fires on it alone (mirrors Zeek's own `R2`
#: finding that `display_string` measured empty in 7/7 real baseline rows anyway).
#: `community` (a credential) is collected under forensic completeness, matching
#: Zeek's own `_snmp` (CONVENTIONS §3, CHANGELOG 2026-08-12).
_SNMP_ATTRS = {
    "version": "snmp.version",
    "community": "snmp.community",
    "pdu_type": "snmp.pdu_type",
}


def _snmp(r: dict) -> Iterator[Event]:
    return _responder_service(r, "snmp", _SNMP_ATTRS)


#: `sip` has NO `SERVICE_PROJECTIONS` entry at all — declined on the Zeek side
#: (`_SERVICE_NOT_PROJECTED["sip"]`) for the `R1` keying reason this module's own
#: docstring explains. `method`/`uri`/`request_line` are the CLIENT's own protocol
#: fields (a request line and its parts), so none of them is a property of the
#: RESPONDER this event is keyed on — collected here under forensic completeness
#: (CONVENTIONS §3) exactly as Zeek's `_sip` collects `user_agent`, published on an
#: event keyed at the server, but landing on a kind the existing (untouched) decline
#: table already keeps out of `_add_service`'s node-feature path. Moderate-to-low
#: confidence on the exact nested field names (module docstring).
#: Resolved against `etc/schema.json`, which types `sip` as `additionalProperties:
#: false` with exactly `code`, `method`, `reason`, `request_line`, `response_line`,
#: `uri`, `version` (plus an `sdp` sub-object). Two corrections landed here from that
#: check: `sip.reason_phrase` DOES NOT EXIST (the key is `reason`) and `sip.user_agent`
#: does not exist at all — the latter is simply removed rather than replaced with a
#: guess, because there is no field to substitute (the `R1` reasoning below is about
#: what would happen if there were, and stands unchanged for `method`/`uri`).
_SIP_ATTRS = {
    "method": "sip.method",
    "uri": "sip.uri",
    "status_code": "sip.code",
    "status_msg": "sip.reason",
    "request_line": "sip.request_line",
    "version": "sip.version",
}


def _sip(r: dict) -> Iterator[Event]:
    return _responder_service(r, "sip", _SIP_ATTRS)


#: `smtp` has NO `SERVICE_PROJECTIONS` entry either, for the identical `R1` reason —
#: `helo` is the connecting CLIENT's own HELO/EHLO argument (Zeek's own docs, quoted in
#: `ingest/zeek/plugin.py::_smtp`'s docstring, say so explicitly), collected here under
#: forensic completeness but never a property of the responder this event is keyed on.
_SMTP_ATTRS = {
    "helo": "smtp.helo",
    "mail_from": "smtp.mail_from",
    "rcpt_to": "smtp.rcpt_to",
}


def _smtp(r: dict) -> Iterator[Event]:
    """SMTP transaction -> ServiceEvent(smtp). Suricata's own SMTP eve fields are
    thinner than Zeek's `smtp.log` — no `path`/`x_originating_ip` relay-chain analogue
    was found (Suricata's SMTP support exists mainly for file-extraction context, not
    full header parsing), so the referenced-entity evidence Zeek's own `_smtp` was
    admitted for (`Q24` amendment 1: IP addresses of hosts the sensor never directly
    saw) has no Suricata-side counterpart here — a real coverage gap relative to Zeek,
    named rather than papered over with an invented field.
    """
    return _responder_service(r, "smtp", _SMTP_ATTRS)


def _dcerpc_interfaces(r: dict) -> list[dict]:
    """The `dcerpc.interfaces[]` entries this parser can read, in the record's own
    order. One place, so `_dcerpc` and `_dcerpc_notes` cannot disagree about what
    "an interface" is.
    """
    interfaces = (r.get("dcerpc") or {}).get("interfaces")
    if not isinstance(interfaces, list):
        return []
    return [i for i in interfaces if isinstance(i, dict)]


def _dcerpc_notes(r: dict) -> Iterator[str]:
    """Row-level telemetry (`_ROW_NOTES`): how much of a multi-interface bind this
    parser's declared selection rule threw away (code review `S6`). A silent
    `interfaces[0]` is indistinguishable, downstream, from a record that only ever had
    one interface in it — and a DCE/RPC bind negotiating several is exactly where a
    single-endpoint census stops being a census.
    """
    extra = len(_dcerpc_interfaces(r)) - 1
    if extra > 0:
        yield f"dcerpc bind with {extra + 1} interfaces; {extra} not published"


def _dcerpc(r: dict) -> Iterator[Event]:
    """DCE/RPC call -> ServiceEvent(dce_rpc). `SERVICE_PROJECTIONS["dce_rpc"]` reads
    `endpoint`/`operation` — Suricata's own DCERPC eve log identifies an interface by
    UUID and a call by its numeric `opnum`, not by the friendly name (`"epmapper"`,
    `"Bind"`) Zeek's own analyzer resolves internally from its own UUID/opnum tables.
    Structurally the SAME fact (which RPC interface, which call), in a less readable
    representation — sufficient for `_add_service`'s own use (an opaque census key
    joined with `|`), so this is mapped rather than declined; readability is a
    follow-up, not a correctness gap.

    **The selection rule, declared rather than incidental** (code review `S6`): one
    DCE/RPC bind can negotiate SEVERAL interfaces, and `attrs["endpoint"]` is singular.
    This publishes the FIRST entry in the record's own order — the interface the client
    asked for first, which is the one the transaction that follows actually uses — and
    every discarded entry is COUNTED (`_dcerpc_notes`). Rejected alternatives, so the
    next reader does not have to re-derive them: joining all UUIDs into one string mints
    a census key no sensor reported and that no other kind's `attrs` shape resembles;
    emitting one `ServiceEvent` per interface multiplies one observation into N
    identical-looking facts about the same port. Neither is free, both are reversible,
    and the counter is what will say whether the case is common enough to revisit.
    """
    dcerpc = r.get("dcerpc") or {}
    interfaces = _dcerpc_interfaces(r)
    attrs = {k: v for k, v in {
        "endpoint": _s(interfaces[0].get("uuid")) if interfaces else "",
        "operation": _s(dcerpc.get("opnum")),
        "request": _s(dcerpc.get("request")),
        "response": _s(dcerpc.get("response")),
    }.items() if v}
    return _emit_responder_service(r, "dce_rpc", attrs)


#: `SERVICE_PROJECTIONS["quic"]` reads `server_name`/`version` — QUIC's own TLS-in-QUIC
#: handshake mirrors `tls` field for field (`ingest/zeek/plugin.py::_quic`'s own
#: docstring makes the identical observation for Zeek's version).
_QUIC_ATTRS = {
    "server_name": "quic.sni",
    "version": "quic.version",
    "ja3": "quic.ja3.hash",
}


def _quic(r: dict) -> Iterator[Event]:
    return _responder_service(r, "quic", _QUIC_ATTRS)


#: Kind `ldap_op`, NOT `ldap` — coordinator ruling, matching
#: `ingest/zeek/plugin.py::_ldap`'s own reasoning: Zeek's `ldap_search.log` already owns
#: kind `"ldap"` for a DIFFERENT stem (a search-specific log this Suricata build has no
#: separate analogue for — its single `ldap` event_type covers protocol operations
#: generically), and `SERVICE_PROJECTIONS` is keyed by kind, so reusing `"ldap"` would
#: silently merge two distinct facts under one predicate. `SERVICE_PROJECTIONS
#: ["ldap_op"]` reads `opcode` — mapped from the request's own operation name.
_LDAP_OP_ATTRS = {
    "opcode": "ldap.request.operation",
    "message_id": "ldap.request.message_id",
}

#: The per-operation response objects that carry an LDAP `result_code`, in the order
#: this parser reads them — every one the schema declares, named explicitly rather than
#: discovered by scanning for keys ending in `_response` (which would also match
#: `intermediate_response`, the one response object with no result at all, and would
#: silently absorb any future key whose shape happens to match). Resolved against
#: `etc/schema.json`; the order is the schema's own alphabetical one, and it is
#: load-bearing only in that it must be FIXED — a well-formed response element carries
#: exactly one of these (`LES-009`: nothing iteration-ordered may reach an output).
_LDAP_RESULT_CARRIERS = (
    "add_response", "bind_response", "compare_response", "del_response",
    "extended_response", "mod_dn_response", "modify_response", "search_result_done",
)


def _ldap_result(ldap: dict) -> str:
    """The LDAP result code this transaction reported, or `""`.

    **`ldap.response.result_code` DOES NOT EXIST** and this attr was therefore never
    once populated (`SCHEMA_FINDINGS` Defect 1, machine-checked against
    `etc/schema.json`): the schema declares `ldap.request` (an object) and
    `ldap.responses` (an ARRAY), no `response` key at all, and a `_dig` down a missing
    key returns `None` silently — a whole attr that looked implemented and produced
    nothing, on every row, forever.

    Two decisions the schema does not make for us, so they are made here and stated:

    * **Which element.** One eve `ldap` record can carry several responses (a search
      returns N entries then a `search_result_done`). This takes the one whose
      `message_id` matches the request's — the LDAP protocol's own correlation field,
      which is what "the result OF this request" means — and falls back to the FIRST
      element when the request carries no `message_id` to match on. Not the last, and
      not a join of all of them: the attr is singular and a made-up aggregate would be
      a value no sensor reported.
    * **Which sub-object.** `_LDAP_RESULT_CARRIERS`, a declared, fixed-order tuple.

    `_dig` is deliberately not used for the array hop: it walks dict keys only, which is
    exactly how the original defect stayed invisible.
    """
    responses = ldap.get("responses")
    if not isinstance(responses, list):
        return ""
    elements = [e for e in responses if isinstance(e, dict)]
    if not elements:
        return ""
    want = (ldap.get("request") or {}).get("message_id")
    chosen = elements[0]
    if want is not None:
        chosen = next((e for e in elements if e.get("message_id") == want), elements[0])
    for carrier in _LDAP_RESULT_CARRIERS:
        sub = chosen.get(carrier)
        if isinstance(sub, dict):
            code = _s(sub.get("result_code"))
            if code:
                return code
    return ""


def _ldap(r: dict) -> Iterator[Event]:
    attrs = _mapped_attrs(r, _LDAP_OP_ATTRS)
    result = _ldap_result(r.get("ldap") or {})
    if result:
        attrs["result"] = result
    return _emit_responder_service(r, "ldap_op", attrs)


# -- alerts / health -----------------------------------------------------------------

def _alert(r: dict) -> Iterator[Event]:
    """Suricata rule match -> AlertEvent — the direct analogue of Zeek's `notice.log`
    (`ingest/zeek/plugin.py::_notice`). `category`/`detail` are left as whatever the
    sensor actually reported (no fallback literal manufactured for an empty
    `alert.category`) — an empty classification is itself the honest reading for a
    custom rule with no `classtype` set, not evidence this parser should paper over.
    """
    ts = parse_suricata_timestamp(r.get("timestamp"))
    if ts is None:
        return
    alert = r.get("alert") or {}
    yield AlertEvent(ts=ts, ip=_s(r.get("src_ip")), peer_ip=_s(r.get("dest_ip")),
                     category=_s(alert.get("category")), detail=_s(alert.get("signature")))


def _anomaly(r: dict) -> Iterator[Event]:
    """Suricata's protocol-oddity flag -> AlertEvent(category=weird) — the direct
    analogue of Zeek's `weird.log` (`ingest/zeek/plugin.py::_weird`, itself
    `category="weird"` fixed): both name a sensor-observed protocol irregularity, not a
    signature match, and reusing the fixed category lets a store consumer group the two
    sensors' oddity signals under one label rather than inventing a Suricata-only one.
    """
    ts = parse_suricata_timestamp(r.get("timestamp"))
    if ts is None:
        return
    anomaly = r.get("anomaly") or {}
    yield AlertEvent(ts=ts, ip=_s(r.get("src_ip")), peer_ip=_s(r.get("dest_ip")),
                     category="weird",
                     detail=_s(anomaly.get("event")) or _s(anomaly.get("type")))


def _stats(r: dict) -> Iterator[Event]:
    """Suricata's own engine-stats record -> **nothing, today** (code review `S5`).

    `stats` stays in `_PARSERS` by name — the rows ARE read, and a build that starts
    emitting a counter this project can honestly reduce has a parser waiting — but this
    function currently publishes no `SensorHealthEvent` at all. That is an honest empty,
    and `_EMITS_NOTHING_BY_DESIGN` makes the summary say so instead of warning that the
    parser and the record shape disagree.

    **What changed and why.** The first landing mapped `stats.capture.kernel_drops` onto
    kind `pkts_dropped` (the `max` reducer in `windowing.windows
    .SENSOR_HEALTH_AGGREGATION`) while DECLINING `stats.decoder.pkts`/`.bytes` for Zeek's
    `sum`-reduced `pkts_proc`/`bytes_recv` kinds, on the grounds that this build cannot
    tell whether a given deployment's counters are per-interval deltas or cumulative
    totals (Suricata's documented default is cumulative-since-start unless `deltas: yes`
    is set, and nothing in one JSON record proves which mode produced it). Those two
    halves contradict each other: the admit was defended by arguing `max` survives
    either reading, which is true of the REDUCER and not of the KIND — `pkts_dropped`
    means "packets the capture layer discarded", and a cumulative counter's `max` is
    "drops since the engine started", a different quantity that would be compared,
    across sensors and across windows, as if it were the first. One unverified premise
    cannot both justify a decline and be waved past for an admit.

    So both halves are declined, symmetrically, **by name and with a path back**: read
    `stats.uptime` across two consecutive `stats` records, or read the `deltas` setting
    the deployment ran with, and one measurement settles every counter here at once.
    Until then this parser mints nothing rather than publishing a number whose meaning
    depends on a configuration flag nothing checks.

    The cost is real and worth stating: a Suricata-sourced run currently has NO
    sensor-side loss signal (`capture_loss` has no eve analogue either), so window
    health reports `sensor_pkts_dropped_max = None` — "not observed", which is the
    truthful reading, not "no drops" (`LES-006`).
    """
    return iter(())


#: `event_type` -> parser. The ONLY route into the event stream; no fall-through.
_PARSERS: dict[str, Callable[[dict], Iterator[Event]]] = {
    "flow": _flow,
    "alert": _alert,
    "stats": _stats,
    "anomaly": _anomaly,
    "dns": _dns,
    "dhcp": _dhcp,
    "krb5": _krb5,
    "arp": _arp,
    "tls": _tls,
    "http": _http,
    "ssh": _ssh,
    "smb": _smb,
    "snmp": _snmp,
    "sip": _sip,
    "smtp": _smtp,
    "dcerpc": _dcerpc,
    "quic": _quic,
    "ldap": _ldap,
}

#: `event_type` -> why it is skipped. Skipping is a named decision, never an accident —
#: `ingest/zeek/plugin.py::_SKIP`'s identical discipline: an event_type in neither table
#: is reported as unrecognized (see `SuricataPlugin.events`).
#:
#: **This is a DECLINE list, not a census** (`SCHEMA_FINDINGS`' closing nit). Together
#: with `_PARSERS` it names all 35 entries of Suricata's own `eve-log: types:` list —
#: that part IS a census, and `tests/test_suricata_ingest.py` pins it — but a few
#: entries beyond those 35 name a top-level schema object whose eve `event_type` spelling
#: may differ (`files` is configured as `files` and arrives as `fileinfo`; both are
#: named) or that a stock build may never emit at all (`packet`, `enip`, `dnp3`,
#: `modbus`). Declining something that never arrives costs nothing and cannot hide a
#: gap; the reverse — a real type in neither table — is the failure this pair prevents.
_SKIP: dict[str, str] = {
    "netflow": "Suricata's own flow-record aggregate view; the `flow` event_type already "
              "reports the identical connection in this build's richer, per-flow shape — "
              "parsing both would double-count one flow under two kinds. That argument "
              "has a PRECONDITION (`flow` actually being emitted) which a deployment can "
              "switch off, so it is checked rather than assumed: see the "
              "netflow-without-flow warning in `SuricataPlugin._publish_census` (`S9`)",
    "drop": "packets Suricata's own inline/IPS engine discarded — a firewall/IPS DECISION, "
            "not sensor-side capture loss the way `stats`/Zeek's `capture_loss` are; no "
            "existing kind names this fact, and minting one is a vocabulary decision this "
            "build was not asked to make (task brief: report, don't mint)",
    "fileinfo": "file-analysis metadata; no event-model home today, matching Zeek's own "
               "decline of `files.log`/`pe.log` (`ingest/zeek/plugin.py::_SKIP`) for the "
               "identical reason",
    "files": "as `fileinfo` — declined together in case a build emits both names for the "
            "file-analysis family",
    "frame": "app-layer frame-level tracing; no connection tuple's worth of evidence beyond "
            "what `flow`/the protocol-specific stem already carries, and no Zeek-side "
            "analogue at all",
    "packet": "raw/base64 packet payload attached to an alert, or a dedicated packet log — "
              "capture-layer content, not a normalized network fact (spirit of Zeek's own "
              "decline of `packet_filter.log`, even though the mechanism differs)",
    "ftp": "FTP control-channel commands; no Zeek-side stem admitted in this project's own "
           "35-stem table, so there is no existing kind to reuse and none is minted here "
           "without a ruling",
    "ftp_data": "the FTP data-channel companion to `ftp`; same decline",
    "rdp": "named explicitly in `docs/WORK_ORDER_zeek_stems_wide_vantage.md`'s own finding as "
           "carrying identity the resolver wants, but still UNRULED there (`LES-120`) — "
           "admitting it here would mint ahead of the very ruling that work order is "
           "waiting on",
    "nfs": "file-share protocol; no Zeek-side stem admitted",
    "tftp": "a control-only protocol with no admitted Zeek-side counterpart, same reasoning "
            "as `ftp`",
    "ike": "IPsec key exchange; no Zeek-side stem admitted",
    "mqtt": "IoT pub/sub; no Zeek-side stem admitted",
    "rfb": "VNC; no Zeek-side stem admitted",
    "pgsql": "PostgreSQL wire protocol; no Zeek-side stem admitted (this project has never "
             "parsed a database wire protocol on either sensor)",
    "http2": "HTTP semantics already reach `_PARSERS` via the `http` event_type; whether "
             "Suricata's `http2` event_type reports the SAME requests a second time or "
             "genuinely disjoint ones was not verified against a real capture (module "
             "docstring) — admitting it without that check risks double-counting one "
             "request under two kinds",
    "websocket": "an HTTP upgrade; the underlying `http` event_type already covers the "
                 "handshake, and websocket FRAME content has no Zeek-side analogue",
    "bittorrent-dht": "P2P DHT traffic; no Zeek-side stem admitted",
    "dnp3": "industrial control protocol; no Zeek-side stem admitted (matches Zeek's own "
            "zero coverage of ICS protocols in this project)",
    "modbus": "industrial control protocol; same reasoning as `dnp3`",
    "enip": "industrial control protocol; same reasoning as `dnp3`",
    "llmnr": "link-local multicast name resolution. Schema-verified as a REAL event_type "
             "(and its `answers[]` items carry `a`/`aaaa`, so it is genuinely "
             "address-bearing evidence, unlike `mdns` below) — declined only because "
             "`events.IdentityEvent.kind`'s vocabulary has no counterpart and neither "
             "`windowing.entities.IDENTITY_SCOPES` nor `IDENTITY_PROJECTIONS` declares "
             "one, so admitting it means MINTING a kind, which is a ruling this build "
             "was not asked to make. Named path back: rule on the kind, add the two "
             "table rows, then move this to `_PARSERS` — the parser itself is the "
             "cheapest part",
    "pop3": "mail retrieval; request/response commands and status only (schema-verified: "
            "`request.{command,args}`, `response.{status,header,data,success}`). No "
            "Zeek-side stem admitted in this project's own 35-stem table, so there is "
            "no existing kind to reuse and none is minted here without a ruling — the "
            "identical reasoning `ftp` gets one line up",
    "doh2": "DNS-over-HTTPS/2. Real event_type, and its records ARE dns-shaped, but the "
            "traffic it describes is by construction traffic the network operator chose "
            "to make opaque; whether a DoH-derived binding is `observed` at all, or "
            "belongs in a different evidence class, is an evidence-quality ruling with "
            "no precedent in this project — declined by name pending it rather than "
            "folded in beside plain `dns` as if the two were the same observation",
    "mdns": "multicast DNS. Declined for a SHAPE reason, not a vocabulary one — this is "
            "the one decline here with a counterpart kind already in "
            "`events.IdentityEvent.kind`, so it would otherwise be admissible. "
            "Schema-verified: `mdns.answers[]` items expose only "
            "`{ptr, rrname, rrname_truncated, txt}` — there is no `rdata`/`ttl` the way "
            "`dns.answers[]` has, and no `a`/`aaaa` the way `llmnr.answers[]` does, so "
            "`_dns_answer` is NOT reusable and the address-bearing field cannot be "
            "resolved from the schema alone. Guessing a key here is precisely what "
            "produced six silent defects in this file's first landing. Named path back: "
            "read one real mDNS record, implement against its actual shape",
}

# A parser table with no corresponding decline-or-parse entry would silently mishandle an
# event_type nobody has actually ruled on. Checked at import, mirroring
# `ingest/zeek/plugin.py`'s identical `_COLUMNS`/`_PARSERS` cross-check (`LES-017`:
# enumerate every path, do not assume the quiet one is absent) — here the two tables must
# be DISJOINT (an event_type is parsed XOR declined, never both), not equal in key set,
# since there is no third "columns" table to reconcile against.
_overlap = set(_PARSERS) & set(_SKIP)
assert not _overlap, f"_PARSERS and _SKIP both name {sorted(_overlap)} — pick one"


# -- declared guards and row-level telemetry -----------------------------------------

#: `event_type` -> a callable naming, per row, anything this build wants COUNTED but
#: not rejected. The counts land in `SuricataPlugin.parse_notes` and in the closing
#: summary; nothing here changes what is parsed. One table, so a future "count this
#: too" is a row rather than another branch inside the dispatch loop.
_ROW_NOTES: dict[str, Callable[[dict], Iterator[str]]] = {
    "flow": _flow_notes,        # `S3`: an app_proto outside `_APP_PROTO_TO_SERVICE`
    "dcerpc": _dcerpc_notes,    # `S6`: interfaces discarded by the selection rule
    "arp": _arp_notes,          # an opcode outside `_ARP_SENDER_OPCODES`
}

#: kind -> the attrs that kind EXISTS to produce (code review `S7`). Two DIFFERENT
#: readings are taken from this table, and the difference is the whole point:
#:
#:   * **per declared KEY** (`SuricataPlugin.declared_attrs_seen`): every key here that
#:     was never once populated, across every event of that kind in the run, is named at
#:     close. This is the reading that catches a single wrong field spelling — the
#:     realistic failure, since spellings break one at a time.
#:   * **per EVENT** (`_TypeStats.attrless`): events carrying NONE of their kind's keys,
#:     counted. Strictly weaker as a spelling tripwire — the first landing had only this
#:     one, and mutating `tls`'s `version` and `ja3s` paths left it reading zero while
#:     two attrs were silently gone — but it is a real per-event volume the per-key
#:     tally cannot express, so both are reported.
#:
#: **This is the guard that turns a field-spelling mistake from silent into loud, and it
#: is worth more than any individual spelling fix in this file.** Six wrong dotted paths
#: shipped here (`SCHEMA_FINDINGS`), four of them SILENT: `_dig` walks to a missing key,
#: `_mapped_attrs` drops the empty value, and the parser emits a well-formed event with
#: an attrs dict quietly missing the one thing the event was for. Row counts do not
#: catch it (rows were parsed), event counts do not catch it (events were emitted), and
#: `_summary`'s "PRODUCED NO EVENTS" warning does not fire. This does.
#:
#: The values MIRROR `windowing.windows.SERVICE_PROJECTIONS` for every projected kind
#: and are declared here rather than imported: `ingest` must not import `windowing`
#: (CONVENTIONS §3, downward only). `tests/test_suricata_ingest.py` asserts the two
#: agree, which is where the cross-layer check belongs. `smtp`/`sip` have no projection
#: at all (`R1`) and name their own primary field instead — a kind nothing projects can
#: still be measurably broken.
_EXPECTED_ATTRS: dict[str, tuple[str, ...]] = {
    "ssl": ("server_name", "version", "ja3s"),
    "http": ("host",),
    "ssh": ("server",),
    "quic": ("server_name", "version"),
    "snmp": ("version", "display_string"),
    "smb": ("share_type", "service"),
    "dce_rpc": ("endpoint", "operation"),
    "ldap_op": ("opcode",),
    "smtp": ("helo", "mail_from", "rcpt_to"),
    "sip": ("method", "uri", "status_code"),
}

#: event_type -> why a parser that emits NOTHING is the intended state, not a defect.
#: Without this, `_summary`'s "parser and record shape likely disagree" warning — which
#: exists to catch exactly the silent-spelling failure `_EXPECTED_ATTRS` guards against —
#: would cry wolf on every run for a deliberate, reasoned decline (`S5`).
_EMITS_NOTHING_BY_DESIGN: dict[str, str] = {
    "stats": "every counter declined pending one cumulative-vs-delta measurement — see "
             "`_stats`",
}


# -- streaming merge -----------------------------------------------------------------

@dataclass
class _FileStats:
    """One discovered file's own reorder/parse telemetry — satisfies
    `ingest/reorder.py::ReorderStats` (`.unordered`/`.capped`) and `sources._rows`'s
    (`.malformed`) at once, mirroring `ingest/zeek/plugin.py::_LogStats` doing the same
    for a Zeek stem. Reorder buffering happens PER FILE here (not per `event_type`,
    which is not known until a row is decoded) — see the module docstring for why
    dispatch happens on the parsed EVENT stream, after decode.
    """
    rows: int = 0
    malformed: int = 0
    unordered: int = 0
    capped: int = 0


@dataclass
class _TypeStats:
    """One `event_type`'s own row/event telemetry, for the closing summary — separate
    from `_FileStats` because the domains differ: reorder buffering is a property of
    the FILE a row came from, while "how many alerts fired" is a property of the
    `event_type` a row carries, and Suricata's files-to-types relationship is
    many-to-many (module docstring), unlike Zeek's one-file-one-stem shape where one
    dataclass could serve both roles at once.
    """
    rows: int = 0
    events: int = 0
    malformed: int = 0
    #: `ServiceEvent`s emitted carrying none of `_EXPECTED_ATTRS[kind]` — the ANY-gate
    #: reading of that table, which cannot see a SINGLE broken spelling; its per-key
    #: counterpart is `SuricataPlugin.declared_attrs_seen` (`S1`).
    attrless: int = 0


@register
class SuricataPlugin(IngestPlugin):
    name = "suricata"

    #: how far back the in-file reorder can repair disorder — the actual guarantee
    reorder_horizon_seconds: float = REORDER_HORIZON_SECONDS
    #: memory backstop for that buffer; peak memory is this x open files
    reorder_rows: int = REORDER_ROWS
    #: stderr progress cadence in rows (0 silences progress *and* the summary)
    progress_rows: int = PROGRESS_ROWS

    def __init__(self) -> None:
        #: per-`event_type` run telemetry, populated as `events()` is consumed
        self.log_stats: dict[str, _TypeStats] = {}
        #: `IngestPlugin.unparsed_stems`'s contract, satisfied here on a WEAKER timing
        #: guarantee than `ingest/zeek/plugin.py` gives it — see the module docstring's
        #: "the census contract is weaker than Zeek's" section for the full reasoning.
        #: Reset on every `events()` call, then set at most once, at exhaustion — and
        #: NOT set at all when the drained read produced no `event_type` to audit
        #: (`_publish_census`'s refusal branch).
        self.unparsed_stems: dict[str, int] | None = None
        #: `IngestPlugin.unrecognized_stems`'s contract — same reset/set discipline,
        #: same weaker timing, one line over.
        self.unrecognized_stems: dict[str, int] | None = None
        #: discovered file label -> that file's own reorder/parse telemetry. Held on
        #: the plugin, not thrown away per stream: `.capped` means the reorder horizon
        #: was NOT honored for those rows, which `LES-004` requires be disclosed as a
        #: health fact, and the first landing constructed this dataclass, threaded it
        #: through two layers and never read a field of it (code review, second round).
        #: `ingest/zeek/plugin.py::_summary` is the shape mirrored here.
        self.file_stats: dict[str, _FileStats] = {}
        #: kind -> which of `_EXPECTED_ATTRS[kind]`'s DECLARED keys were populated at
        #: least once this run, and how many events of that kind were emitted at all
        #: (`S1`). Bounded by the ~40 declared keys, never by attr cardinality.
        self.declared_attrs_seen: dict[str, set[str]] = {}
        self.kind_events: dict[str, int] = {}
        #: note -> how many rows produced it (`_ROW_NOTES`): the declared, counted
        #: half of "never a silent default" for the decisions a parser makes INSIDE a
        #: row it accepts — an unmapped `app_proto` (`S3`), a DCE/RPC bind whose extra
        #: interfaces the selection rule discards (`S6`), an ARP opcode nothing has
        #: ruled on. Reset per `events()` call, like everything else here.
        self.parse_notes: dict[str, int] = {}

    # -- reporting (stderr only; nothing here may reach a feature) ------------------

    def _say(self, msg: str) -> None:
        print(f"[suricata] {msg}", file=sys.stderr, flush=True)

    def _summary(self, event_type_counts: dict[str, int]) -> None:
        if not self.progress_rows:
            return
        total_rows = sum(event_type_counts.values())
        total_events = sum(s.events for s in self.log_stats.values())
        self._say(f"done: {total_rows:,} rows -> {total_events:,} events across "
                  f"{len(self.log_stats)} event_type(s)")
        for event_type in sorted(self.log_stats):
            s = self.log_stats[event_type]
            notes = []
            if s.malformed:
                notes.append(f"{s.malformed:,} row(s) produced no finite-ts event")
            if s.rows and not s.events:
                # A declared empty is not the same finding as a broken parser, and
                # warning about the first would train a reader to ignore the second.
                declared = _EMITS_NOTHING_BY_DESIGN.get(event_type)
                notes.append(f"emits nothing BY DESIGN: {declared}" if declared else
                             "PRODUCED NO EVENTS from event_type rows present in the "
                             "input — parser and record shape likely disagree")
            if s.attrless:
                notes.append(
                    f"{s.attrless:,} event(s) carried NONE of the attrs this kind "
                    "exists to produce — a field spelling almost certainly disagrees "
                    "with this sensor's real record shape (`_EXPECTED_ATTRS`)")
            note = f"  [{'; '.join(notes)}]" if notes else ""
            self._say(f"  {event_type:<12} {s.rows:>10,} rows -> {s.events:>10,} "
                      f"events{note}")
        # Per-DISCOVERED-FILE reorder/parse telemetry. Separate from the per-event_type
        # block above because the domains differ (`_FileStats`' own docstring): reorder
        # buffering is a property of the FILE, and `event_type` is not known until a row
        # is decoded. Mirrors `ingest/zeek/plugin.py::_summary`'s identical block rather
        # than inventing a second vocabulary for the same facts.
        for label in sorted(self.file_stats):
            f = self.file_stats[label]
            notes = []
            if f.unordered:
                notes.append(f"{f.unordered:,} row(s) displaced past the "
                             f"{self.reorder_horizon_seconds:.0f}s reorder horizon")
            if f.capped:
                notes.append(f"{f.capped:,} flush(es) forced by the "
                             f"{self.reorder_rows}-row memory cap — the horizon was "
                             f"not honored for them")
            if f.malformed:
                notes.append(f"{f.malformed:,} row(s) unreadable or carrying no "
                             f"`event_type`")
            note = f"  [{'; '.join(notes)}]" if notes else ""
            self._say(f"  file {label}: {f.rows:,} row(s) decoded{note}")
        # `S1`: the per-DECLARED-KEY half of `_EXPECTED_ATTRS`. Reported only for kinds
        # that actually emitted — a declared key of a kind this capture never carried is
        # an absence of evidence, not evidence of a broken spelling (`LES-006`).
        for kind in sorted(_EXPECTED_ATTRS):
            emitted = self.kind_events.get(kind, 0)
            if not emitted:
                continue
            never = [k for k in _EXPECTED_ATTRS[kind]
                     if k not in self.declared_attrs_seen.get(kind, ())]
            if never:
                self._say(f"  ATTR {kind}: declared key(s) {', '.join(never)} were "
                          f"NEVER populated across {emitted:,} emitted event(s) — this "
                          f"build's field spelling for them almost certainly disagrees "
                          f"with this sensor's real record shape (`_EXPECTED_ATTRS`)")
        for note in sorted(self.parse_notes):  # sorted: nothing dict-ordered is reported
            self._say(f"  note: {note} ({self.parse_notes[note]:,} row(s))")

    # -- event stream ----------------------------------------------------------------

    def _stream_events(self, stream: EveStream, file_stats: _FileStats,
                       event_type_counts: dict[str, int]) -> Iterator[Event]:
        """One discovered file's rows -> events, tallying `event_type_counts` as a SIDE
        EFFECT of reading — the only way to build the census at all, since `event_type`
        lives inside each row rather than in a file name Zeek's own directory LISTING
        can read for free (module docstring).
        """
        every = self.progress_rows
        for r in stream.rows(file_stats):
            file_stats.rows += 1
            event_type = _s(r.get("event_type"))
            if not event_type:
                file_stats.malformed += 1
                continue
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
            parse = _PARSERS.get(event_type)
            if parse is None:
                continue  # named below, once every discovered file is exhausted
            stats = self.log_stats.setdefault(event_type, _TypeStats())
            stats.rows += 1
            note_for = _ROW_NOTES.get(event_type)
            if note_for is not None:
                for note in note_for(r):
                    self.parse_notes[note] = self.parse_notes.get(note, 0) + 1
            dropped = False
            for ev in parse(r):
                # Mirrors `ingest/zeek/plugin.py::_stream_events`'s identical guard: a
                # non-finite ts must be rejected here, with the row still in hand,
                # rather than raising inside an accumulator three layers down.
                if not math.isfinite(ev.ts):
                    dropped = True
                    continue
                # `S7`/`S1`: what the attrs this kind EXISTS to produce actually did.
                # Checked on the EVENT, not by re-walking the row — it sees exactly what
                # was published, which is the claim the guard is about.
                expected = _EXPECTED_ATTRS.get(getattr(ev, "kind", ""))
                if expected is not None and isinstance(ev, ServiceEvent):
                    kind = ev.kind
                    self.kind_events[kind] = self.kind_events.get(kind, 0) + 1
                    seen = self.declared_attrs_seen.setdefault(kind, set())
                    populated = [k for k in expected if k in ev.attrs]
                    seen.update(populated)
                    if not populated:
                        stats.attrless += 1
                stats.events += 1
                yield ev
            if dropped:
                stats.malformed += 1
            if every and file_stats.rows % every == 0:
                self._say(f"{stream.label}: {file_stats.rows:,} rows read")

    def events(self, source: str, ts_from: float | None = None,
              ts_to: float | None = None) -> Iterator[Event]:
        if ts_from is not None or ts_to is not None:
            # Mirrors `ingest/zeek/sources.py::open_source`'s identical refusal for a
            # Zeek log directory: a bare eve.json file or directory has no partition
            # index to slice on, and silently ignoring the bound would make a run look
            # like it honored a range it never applied.
            raise ValueError(
                "--from/--to require a source with a partition index; a bare eve.json "
                "file or directory has none to slice on")
        src = SuricataSource(source, reorder_horizon_seconds=self.reorder_horizon_seconds,
                             reorder_rows=self.reorder_rows)
        discovered = src.streams()
        for note in src.notes():
            self._say(note)

        self.log_stats = {}
        self.file_stats = {}
        self.declared_attrs_seen = {}
        self.kind_events = {}
        self.unparsed_stems = None
        self.unrecognized_stems = None
        self.parse_notes = {}
        event_type_counts: dict[str, int] = {}

        streams: list[Iterator[Event]] = []
        for stream in discovered:
            file_stats = self.file_stats.setdefault(stream.label, _FileStats())
            streams.append(in_order(
                self._stream_events(stream, file_stats, event_type_counts),
                stream.label, self.reorder_horizon_seconds, self.reorder_rows,
                file_stats))
        # `sources._discover` returns at least one file or raises, so an empty stream
        # list is a broken invariant, not an empty capture. The first landing printed a
        # reassuring "0 events" line here that no reachable path could produce (`S7`).
        assert streams, (f"no eve stream for {source}: sources._discover returned "
                         f"nothing without raising")

        # Merge is exact given per-stream order: `heapq.merge` breaks ties on the
        # position of the source iterator in this list, and discovery order is sorted
        # (`sources.py::_discover`), so equal timestamps resolve the same way on every
        # run (`LES-009`).
        drained = False
        try:
            yield from heapq.merge(*streams, key=event_ts)
            drained = True
        finally:
            # `drained` is the whole point of this flag (code review `M3`): a `finally`
            # runs when the consumer ABANDONS the generator too — a `break` out of the
            # driver's loop, an exception, `islice`, a `next()` in a test — and the
            # first landing published `unparsed_stems` there regardless. That is a
            # census asserted over rows nobody read: `{}` ("audited, nothing left over")
            # from a run that stopped on row one. Only a normal `StopIteration` from the
            # merge proves every discovered file reached exhaustion, which is exactly
            # the claim these two fields make, so only that path sets them; an abandoned
            # run leaves them `None` — unaudited, which is the truth (`LES-006`).
            if drained:
                self._publish_census(event_type_counts, len(discovered))
            elif self.progress_rows:
                self._say("events() abandoned before the source was drained — "
                          "unparsed/unrecognized event_type census stays None "
                          "(unaudited), not an empty census")

    def _publish_census(self, event_type_counts: dict[str, int],
                        files_discovered: int) -> None:
        """Set `unparsed_stems`/`unrecognized_stems` and report — called ONLY once every
        discovered file has been read to exhaustion (see `events`'s `drained` flag and
        the module docstring's "the census contract is weaker than Zeek's" section).
        """
        if files_discovered and not sum(event_type_counts.values()):
            # Nothing this run read carried an `event_type` at all — every line was
            # unreadable, or every decoded row was missing the one field the census is
            # taken over. Publishing `{}` here would say "audited, nothing left over"
            # (`LES-006`), making an unreadable source indistinguishable from a
            # perfectly-covered one. Same reasoning as `events`' `drained` flag (`M3`),
            # one step further in: draining proves the files were READ, and this proves
            # the read produced something a census can be taken of.
            rows = sum(f.rows for f in self.file_stats.values())
            malformed = sum(f.malformed for f in self.file_stats.values())
            self._say(
                f"REFUSING to publish an event_type census: {files_discovered} file(s) "
                f"read to exhaustion and NOT ONE row carried an `event_type` "
                f"({rows:,} row(s) decoded, {malformed:,} unreadable or typeless). "
                f"unparsed/unrecognized stay None (unaudited), never {{}} — check that "
                f"--source points at a real eve.json.")
            self._summary(event_type_counts)
            return
        unparsed = {t: n for t, n in event_type_counts.items() if t not in _PARSERS}
        self.unparsed_stems = unparsed
        self.unrecognized_stems = {t: n for t, n in unparsed.items() if t not in _SKIP}
        for event_type in sorted(t for t in unparsed if t in _SKIP):
            self._say(f"skip {event_type} ({unparsed[event_type]} record(s)): "
                      f"{_SKIP[event_type]}")
        for event_type in sorted(t for t in unparsed if t not in _SKIP):
            # Fail loud, fail closed: not parsed (closed) and named (loud).
            self._say(f"UNRECOGNIZED event_type {event_type!r} "
                      f"({unparsed[event_type]} record(s)) — not parsed; add it to "
                      f"_PARSERS or name it in _SKIP")
        # `S9`: `netflow` is declined on the grounds that `flow` already reports the
        # same connection in a richer shape (`_SKIP["netflow"]`). That argument is only
        # true if `flow` is actually being emitted — a deployment configured with
        # `netflow` output and `flow` disabled produces a run in which this plugin
        # silently sees no traffic at all while both tables look correct. Named here,
        # where the counts to detect it are in hand.
        if unparsed.get("netflow") and not event_type_counts.get("flow"):
            self._say(
                f"WARNING: {unparsed['netflow']:,} `netflow` record(s) and ZERO `flow` "
                "records. `netflow` is declined because `flow` reports the same "
                "connections more richly — with no `flow` output that decline drops "
                "this run's entire traffic view. Enable `flow` in this sensor's "
                "eve-log types, or rule on parsing `netflow`.")
        self._summary(event_type_counts)
