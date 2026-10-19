"""Windowing + feature layer (v0). See DESIGN_BRIEF §5.

Three transformations at once: time-bucketing (tunable window), identity-folding
(addresses -> entities via the resolver), statistical compression (events -> vectors).
Window close is a pure function of (events, resolver state, code build) — keep it that way.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional

from aximprint.events import (
    FLOW_ACCEPTED,
    AlertEvent, Event, FlowEvent, IdentityEvent, ProfileEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.windowing.entities import EntityResolver
from aximprint.windowing.segments import locality, segment_of
from aximprint.windowing.sketches import DistinctCount, Quantiles, TopK

TOP_PEERS = 64  # exact per-entity peer edges kept; tail spills to aggregate (brief §5.3)

#: The aggregate edge `dst` `_close` mints past `TOP_PEERS` — a member of the published ENTITY
#: namespace that no sensor emitted and no resolver minted, and which never gets a `nodes` row of
#: its own (`Q11`). Named here rather than repeated as a literal in `_close`, `learners/
#: extractors.py` and the entity roster: `LES-017`'s rule is that a published namespace is the
#: union of every path that can mint a member "including fallbacks, spill sentinels, and fields
#: nobody declared", and a sentinel spelled out three times is one edit away from being declared
#: in two of them and minted in the third.
LONGTAIL_ENTITY = "__longtail__"
#: Distinct service facts kept per entity, per kind. Measured on the 2026-08-11 real-capture
#: baseline (`Q27`), **no entity exceeded 13** for any kind — `ssl`'s 78,890 rows collapse to 1,764
#: distinct facts over 1,111 entities, max 10 each — so this is a backstop, not a working limit.
#: The volume in a service log is REPETITION, not cardinality. Overflow is COUNTED, never silently
#: dropped: a cap whose truncation is invisible turns "saw nothing more" into "stopped looking"
#: with no way downstream to tell them apart. Load-bearing where this network is not typical —
#: one CDN edge or reverse proxy can serve thousands of SNIs from a single address.
SERVICE_MAX_KEYS = 32

#: `WindowResult.health` keys whose VALUE legitimately differs between transports
#: reading the identical underlying capture (code review `S1`, `docs/WORK_ORDER_
#: zeek_stem_coverage.md` D5) — not a determinism break (`LES-002`'s invariant is
#: about the SAME transport agreeing with itself across two runs; this is about
#: two DIFFERENT transports, which is a different claim entirely) but a fact about
#: what a given transport can even know. `LogDirSource` walks the filesystem
#: directly and can audit every stem present; `ParquetArchiveSource` reads only
#: what an external converter chose to write into a manifest and structurally
#: cannot (`ingest/zeek/sources.py::ZeekSource.inventory_is_complete`) — so
#: `unparsed_log_types`/`unrecognized_log_types` correctly read a real list on one
#: and `None` on the other for the SAME capture. Exported (not a string literal
#: re-declared at each comparison site) so `scripts/verify_pipeline_gates.sh`'s
#: transport-equivalence gate imports this rather than hand-maintaining a second
#: copy that could silently drift from `health`'s own key list — the identical
#: discipline `features/jsonl.py`'s `WALLCLOCK_FIELD` and this module's own
#: `NONDETERMINISTIC_TABLES` (`store/belief_store.py`) already use for their own
#: "this one field/table is allowed to differ" exemptions (`LES-009` #3).
TRANSPORT_DEPENDENT_HEALTH_FIELDS = frozenset({"unparsed_log_types", "unrecognized_log_types"})

#: `ServiceEvent.kind` -> (attrs forming the DURABLE fact, assertion predicate, confidence).
#: Public because `learners/extractors.py` reads it: one table declares what is accumulated AND
#: what it is asserted as, so a kind cannot be kept under one name and published under another.
#:
#: A service log reports events; a belief store wants properties. The projection is what turns one
#: into the other, and it is a judgment per kind — not an algorithm — which is why it is a declared
#: table rather than "keep every attr". Ruled by the user 2026-08-11 (`Q27`, option A + C's table).
#:
#: Predicate naming is not cosmetic. Every kind here except `software` is keyed on the RESPONDER
#: (`id.resp_h`, or `known_services`' own `host`), so the entity is serving — `serves_*`. `software`
#: is keyed on `host`, which for `HTTP::BROWSER` is the CLIENT, so it says `runs_*` instead.
#: Confidence follows evidence quality: Zeek's own census is near-certain, an observed handshake is
#: strong, a self-reported and trivially spoofable banner is not.
#:
#: Six kinds below `software` joined 2026-08-13 (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3,
#: `Q24`'s ruling, amended through 2026-08-13): `http`, `ssh`, `quic`, `snmp`, `tunnel`, `ldap_op`.
#: All are `0.9`, the same evidence class as `ssl`/`dce_rpc`/`smb` (an observed protocol exchange)
#: — `ssh` is the one judgment call among them (a version banner is editable, which is why
#: `software` sits at `0.7`), kept at `0.9` because `server` is read off the handshake with the
#: responder itself. `ntlm` (an eleventh admit) needs no entry here — it lands as an
#: `IdentityEvent` and rides the resolver (`windowing.entities`, `IDENTITY_PROJECTIONS` below),
#: never `_add_service`. `x509` is declined below (`_SERVICE_NOT_PROJECTED["x509"]`): it carries
#: no `ip` at all, so `_add_service` never reaches this table for it regardless. `smtp` and `sip`
#: were projected here in the first landing of this round and were WRONG — see their entries in
#: `_SERVICE_NOT_PROJECTED` below (code review, user-ruled `R1`): both attach a CLIENT's
#: self-announced field to an event keyed on the RESPONDER, which is a keying bug, not a judgment
#: call about evidence quality.
#:
#: `snmp`'s `display_string`: **measured empty in 7/7 real baseline rows**
#: (`/tmp/aximprint-zeek-test/`, all 12 windows) — the ruling's own reopen condition
#: (`docs/OPEN_QUESTIONS.md` `Q26` candidate 2: "if `display_string` is empty across all
#: windows... the admit is worth revisiting") is tripped. **User-ruled to keep it as built**
#: (code review `R2`): an unset field never enters `attrs` (`_attrs()`'s positive gate), so
#: nothing false is published; the tuple activates automatically the moment a future capture
#: populates it; and 7 rows is too small a sample to reverse an admit on. Recorded here so the
#: next reader inherits the measurement, not the assumption that this field was ever confirmed
#: informative.
SERVICE_PROJECTIONS: dict[str, tuple[tuple[str, ...], str, float]] = {
    "known_service": (("port_proto", "service"), "serves_known_service", 0.95),
    "ssl": (("server_name", "version", "ja3s"), "serves_ssl", 0.9),
    "dce_rpc": (("endpoint", "operation"), "serves_dce_rpc", 0.9),
    "smb": (("share_type", "service"), "serves_smb", 0.9),
    "software": (("software_type", "name", "version.major", "version.minor"), "runs_software", 0.7),
    "http": (("host",), "serves_http", 0.9),
    "ssh": (("server",), "serves_ssh", 0.9),
    "quic": (("server_name", "version"), "serves_quic", 0.9),
    "snmp": (("version", "display_string"), "serves_snmp", 0.9),
    # Departs from the `serves_*` convention ON PURPOSE: responder-keyed like every kind above, and
    # the responder does terminate the tunnel, but "serves" would imply an OFFERED service, which an
    # overlay endpoint is not (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3). Do not rename this
    # to `serves_tunnel` — that would be the naming lying about the keying, exactly what the rule
    # above this table exists to prevent.
    "tunnel": (("tunnel_type",), "terminates_tunnel", 0.9),
    # Kind `ldap_op`, NOT `ldap`: `ldap_search`'s parser already owns kind `"ldap"` (declined,
    # below), and this table is keyed by kind — reusing the name would silently merge two distinct
    # facts under one predicate (`ingest/zeek/plugin.py::_ldap`'s docstring).
    "ldap_op": (("opcode",), "serves_ldap", 0.9),
    # Seven stems from `docs/WORK_ORDER_zeek_stems_wide_vantage.md` (2026-08-20). Every tuple here
    # was chosen from MEASURED population over the whole ten-day cold tier, not from the work
    # order's field list — that list is `radius.log`'s and friends' SCHEMA, and reading it as a
    # measurement is what put an empty field in three of this table's first-draft rows.
    "known_cert": (("subject", "issuer_subject"), "serves_known_cert", 0.95),   # 649/649
    "rdp": (("security_protocol",), "serves_rdp", 0.9),                          # 111/116
    # `authentication_method` alone is only 3/8; the server version pair is 8/8 and equally
    # responder-side, so it carries the row when the auth method is absent.
    "rfb": (("server_major_version", "server_minor_version", "authentication_method"),
            "serves_rfb", 0.9),
    "syslog": (("facility", "severity"), "serves_syslog", 0.9),                  # 350/350
    # `connect_status`, NOT `proto_version`: the latter is 0/511 and the former is 511/511 AND is
    # the responder's own answer, which is what a responder-keyed projection should carry anyway.
    "mqtt": (("connect_status",), "serves_mqtt", 0.9),
    # `server_extensions`, NOT `subprotocol` (0/1). `host`/`uri`/`user_agent` populate but are
    # CLIENT-emitted, and projecting a client field onto a responder-keyed event is the `R1`
    # keying bug (`smtp`/`sip`, reversed last round).
    "websocket": (("server_extensions",), "serves_websocket", 0.9),
}

#: `IdentityEvent.kind` -> (assertion predicate, confidence). Mirrors `SERVICE_PROJECTIONS` for
#: the identity side of the store, closing the same silent-default gap one layer over (code review
#: `M5`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3): before this table, `extract_assertions`
#: minted `f"bound_{kind}"` and picked a confidence from an inline tuple literal
#: (`0.9 if kind in ("dhcp", "kerberos") else 0.7`) for WHATEVER kind showed up in
#: `identity_delta` — so a brand-new `IdentityEvent.kind` would silently get both a predicate and
#: a confidence with nothing declared and nothing counted, the exact failure
#: `SERVICE_PROJECTIONS`/`_SERVICE_NOT_PROJECTED`/`unmodelled_service_kinds` exist to prevent on
#: the service side. A kind absent from this table is counted in
#: `health["unmodelled_identity_kinds"]` (`WindowManager._add`) rather than silently assigned a
#: predicate anyway. Partial answer to `Q23` (should `IdentityEvent.kind` get the
#: raw-value-plus-classified-companion treatment `FlowEvent.state`/`state_class` already has) —
#: this declares what an already-open, sensor-supplied vocabulary DOES with a kind once it
#: arrives, not a closed enum for the field itself, so `Q23` stays open.
#:
#: The confidence discriminator is "observed AT THE ADDRESS" vs "a THIRD PARTY's claim", not
#: spoofability: `kerberos`'s principal is self-asserted by the client and still sits at `0.9`,
#: because the principal is read off a request the address itself received. `dhcp`'s assignment
#: and `ntlm`'s hostname (`0.9`, code review `M4`) share that property — `ntlm`'s own docstring
#: already argued this ("AT THE ADDRESS ITSELF") and the confidence tuple was not updated to
#: match when `ntlm` was added; fixed here rather than left as a known-wrong pinned test value.
#: `dns` is the one `0.7`: a DNS answer binds a THIRD PARTY's address — the one the query
#: resolved to — not the address that answered the query.
#:
#: `known_host` (Zeek's own first-seen census, `_known_hosts`) never reaches this table at all:
#: it carries an address with nothing to bind it to, so it produces no binding and therefore no
#: `identity_delta` entry. Since `Q1`'s resolver landed that is a DECLARED decline rather than a
#: fall-through — `windowing.entities.IDENTITY_SCOPES` names it explicitly (`_identifiers_none`),
#: which is what keeps it out of `unscoped_identity_kinds`. There is still nothing to decline for
#: it *here*, unlike the service side's `_SERVICE_NOT_PROJECTED`.
IDENTITY_PROJECTIONS: dict[str, tuple[str, float]] = {
    "dhcp": ("bound_dhcp", 0.9),
    "kerberos": ("bound_kerberos", 0.9),
    "ntlm": ("bound_ntlm", 0.9),
    # 0.9 with `dhcp`, and for the same reason: infrastructure asserting an assignment it
    # CONTROLS. RADIUS is one hop further removed than DHCP -- the NAS speaks for a client that
    # is not on the wire here -- but the discriminator this table uses is "observed at the
    # address vs a third party's claim", and the NAS is not a third party to an address it
    # handed out. The subject question that makes `radius` dangerous is settled in the parser
    # (`ingest/zeek/plugin.py::_radius`), not by discounting confidence here.
    "radius": ("bound_radius", 0.9),
    "dns": ("bound_dns", 0.7),
}

#: Kinds parsed but deliberately NOT projected — declined **by name, with a reason**, the same
#: discipline `ingest/zeek/plugin.py`'s `_SKIP` applies to log stems. An omission and a decision
#: look identical in a table that only lists what it keeps; this is the half that says which is
#: which. A kind in NEITHER table is counted as unmodelled (see `_add_service`) rather than
#: silently dropped, so a new `ServiceEvent.kind` surfaces instead of vanishing.
_SERVICE_NOT_PROJECTED: dict[str, str] = {
    "redis": "admitted as a parsed stem 2026-08-20 and declined HERE on measurement: `cmd.name`/"
             "`cmd.key`/`cmd.value` are 0/2 across the whole ten-day cold tier, and the only "
             "populated fields are `success` and `reply.value` — a per-command outcome and a "
             "payload, neither a durable property of the host. Declining is not the same as "
             "projecting an empty tuple: `_add_service`'s `if not parts: return` means an empty "
             "tuple would silently produce NOTHING while still claiming a projection exists, so "
             "the decline is stated here where a reader finds it. Named path back: the moment a "
             "capture populates `cmd.name`, that alone is the durable fact (which commands a "
             "host serves), and this entry moves to `SERVICE_PROJECTIONS`",
    "ntp": "version/mode/stratum describes the protocol exchange, not a durable property of the "
           "host. `poll` (collected on the event since `docs/WORK_ORDER_zeek_stem_coverage.md` "
           "build step 2b) is a `tfc` schedule candidate (DESIGN_BRIEF §9), not a twin_config "
           "node property — this table publishes per-entity node/edge facts, and the store has "
           "no channel yet for a population-scoped traffic parameter (`Q29`). Two reasons, not "
           "one: even once `Q29` closes, `poll` cannot simply project onto THIS subject — "
           "`_ntp` keys the event on `id.resp_h` (the server), while `poll` on a mode-3 row is "
           "the CLIENT's own chosen polling interval, so projecting it here would attribute a "
           "client's schedule to the server. A keying problem, not only a missing channel — "
           "stays true after `Q29` closes. Declined pending both, not for lack of a field "
           "(`Q27`)",
    "ldap": "scope/result is one query's outcome, not a durable host property, and 'serves LDAP "
            "already arrives via known_service/listen_ports' is true only for hosts the tap "
            "observed. `base_object`/`filter` name computer accounts, users and OUs that may "
            "have no observed address at all — real, unrepresented evidence — but "
            "`windowing/entities.py` has no subject for a name-only entity (`Q1`), so this "
            "stays declined pending that resolver work, not for redundancy (`Q27`). NOTE "
            "(`S4`, code review): the `serves_ldap` predicate is TAKEN — kind `ldap_op` "
            "(`ldap.log`, above) already projects to it. If this decline is ever reversed, "
            "`ldap_search`'s facts land under the SAME `(subject, predicate)` as `ldap_op`'s, "
            "separable only by `source` (`extractor:service-census:ldap` vs `:ldap_op`) — "
            "worth a distinct predicate at that point, not a reuse",
    "smtp": "`helo` is the CLIENT's self-announced name (Zeek's own docs: the connecting "
            "client's HELO/EHLO argument), but `_smtp` keys the event on `id.resp_h` — the "
            "SERVER. Projecting it publishes the client's claim as a property of the server "
            "that merely received it. Measured on the baseline: "
            "`(23.103.199.138, serves_smtp, \"dev06.localdomain\")` — a client's own hostname "
            "asserted about Exchange Online. This is a KEYING problem, not redundancy and not "
            "thinness (`R1`, user-ruled code review; reverses this kind's own D2/`Q24` ruling — "
            "see `docs/OPEN_QUESTIONS.md` `Q24`, which projected `smtp` at 0.9 without checking "
            "which end of the connection `helo` actually describes). `_smtp`'s own docstring "
            "already named `helo` as weak evidence for exactly this reason and projected it "
            "anyway — that contradiction is what this decline fixes. Every field, including "
            "`helo`, stays collected on the event (forensic completeness, CONVENTIONS §3); "
            "`path`/`x_originating_ip` remain this stem's actual reason for parsing at all. The "
            "way back is a CLIENT-keyed fact (e.g. `runs_smtp_client`, keyed on `id.orig_h`), "
            "which needs a second subject on the event and overlaps `Q1`, not a re-ruling of "
            "this table",
    "sip": "`user_agent` is the CLIENT's own User-Agent header (Zeek's docs: from the client), "
           "but `_sip` keys the event on `id.resp_h` — the SERVER. Measured on the baseline: "
           "`(50.209.234.65, serves_sip, \"friendly-scanner\")` — a SIPVicious scanner's own "
           "tool name published as a durable property of the host it SCANNED. Same keying "
           "problem as `smtp` above, not redundancy and not thinness (`R1`, user-ruled code "
           "review; reverses this kind's own `Q24` ruling). Every field, including "
           "`user_agent`, stays collected on the event; SIP URIs (extensions, DIDs) remain "
           "unprojected pending `Q1` regardless. The way back is a CLIENT-keyed fact (e.g. "
           "`runs_sip_agent`, keyed on `id.orig_h`), which needs a second subject on the event "
           "and overlaps `Q1`",
    "smb_files": "per-file-action, not a stable host property. 278 rows collapsed to 3 distinct "
                 "`(action, path)` facts, measured before `name` was collected — build step 2b "
                 "adds `name`, and it alone is what makes this kind plausibly high-cardinality "
                 "(near one fact per action); the post-change cardinality is unmeasured, not "
                 "merely unstated, and a distinct-`(action, path, name)` count over the same "
                 "278 rows in the tier-3 baseline settles it in one query. Whether it fits "
                 "`SERVICE_MAX_KEYS` (`LES-017`) is the open question — not privacy (retired, "
                 "CONVENTIONS §3) and not redundancy, pending re-ruling (`Q27`)",
    "x509": "the certificate carries no connection tuple at all (verified against the baseline, "
            "`Q24` 'Headers verified' — keyed on `fingerprint`, not an address); attributing it "
            "to a host needs a join against `ssl.log`'s `cert_chain_fps` that "
            "`ingest/zeek/plugin.py::_x509` does not attempt, so `ev.ip` is always empty and "
            "`_add_service`'s own `if not ev.ip: return` already makes this kind inert before "
            "this table is ever consulted for it. Named here anyway, same discipline as `ldap`/"
            "`smb_files`/`ntp` above, so a reader finds the reason in one place instead of "
            "re-deriving it from `_add_service`'s control flow. The SAN hostnames "
            "(`san.dns`/`san.uri`/`san.email`/`san.ip`) are the reason this stem parses at all "
            "rather than being redundant with `ssl.subject` (`Q24`); they need `Q1` — a subject "
            "for a name-only entity — before any of this can reach an assertion",
}

#: `stats.log`-sourced `SensorHealthEvent.kind` -> how to reduce its `value` across a window's
#: several reports into one health number. A kind's reducer is a judgment about what its VALUE
#: MEANS, not a mechanical choice — same argument as `SERVICE_PROJECTIONS`/`IDENTITY_PROJECTIONS`
#: above: declared once, in one table, so a new kind cannot silently inherit whichever reducer
#: happened to be hardcoded (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3, code review, folding
#: in a separate user-ruled addendum). `capture_loss` (a DIFFERENT log, `capture_loss.log`) is
#: NOT in this table — it keeps its own dedicated `capture_loss_pct_max` field and code path,
#: untouched, because its published key never followed this table's `sensor_<kind>_<reducer>`
#: naming to begin with.
#:
#: `pkts_dropped`/`pkt_lag` are `max`: a drop counter or a lag reading, where the worst single
#: report in the window is the fact worth keeping — the existing behavior, key names UNCHANGED
#: (`sensor_pkts_dropped_max`, `sensor_pkt_lag_max`) so nothing downstream breaks.
#:
#: `pkts_proc`/`bytes_recv` are `sum`, and that is not the obvious choice — it was checked against
#: the baseline (`/tmp/aximprint-zeek-test/`) before being declared, not assumed: these are
#: PER-INTERVAL DELTAS, not cumulative counters. `pkts_proc` visibly DECREASES within a single
#: window (e.g. `400053 -> 482875` in one run, then `465161 -> 458630` DROPPING in the next run's
#: own rows) — a running total could never do that. Each row reports activity SINCE THE PREVIOUS
#: stats report, so `max` would report the single busiest ~5-minute interval as if it were the
#: window's whole traffic; `sum` is the reducer that actually recovers the window total. This
#: holds because a `harvest`-derived cold tier's `stats.log` rows are deltas WITHIN one Zeek run
#: over one pcap window (~600s, `docs/PLAN_metadata_harvester.md` §5's default), and a 1h
#: aximprint window spans roughly SIX such runs (harvest's pcap windows are 600s apart) — summing
#: ACROSS runs is still correct, because each run's own rows are already deltas, not cumulative.
#: That premise would NOT hold if a future sensor emitted cumulative counters instead — re-check
#: it before reusing `sum` for a new kind, rather than assuming this table's existing choices
#: transfer. The FIRST row of every zeek run is a startup artifact (`pkts_proc = 1`,
#: `bytes_recv ~= 80`, measured) — harmless inside a sum, noted here so a future reader never
#: mistakes it for a parsing bug.
#:
#: Unlike `pkts_dropped`/`pkt_lag` (always `-`/unset on the baseline — offline `zeek -r <pcap>`
#: replay has no live link counters, see `_stats`'s docstring), `pkts_proc`/`bytes_recv` are
#: ALWAYS set there, making them the only `stats.log` fields that actually produce an event on a
#: `harvest`-derived cold tier — the practical "did the sensor see traffic" signal, with
#: `capture_loss.log` remaining the only LOSS signal at that vantage.
SENSOR_HEALTH_AGGREGATION: dict[str, str] = {
    "pkts_dropped": "max",
    "pkt_lag": "max",
    "pkts_proc": "sum",
    "bytes_recv": "sum",
}


def _reduce_sensor_health(reducer: str, values: list | None) -> float | None:
    """Apply `reducer` (a `SENSOR_HEALTH_AGGREGATION` value) to one kind's collected readings.

    `None` — not `0` — when `values` is empty or `None`: the sensor reported NOTHING for this
    kind this window, which is "unaudited", not "audited and found zero" (`LES-006`). A window
    where the sensor genuinely processed nothing and a window `stats.log` never reported for at
    all must stay distinguishable.
    """
    if not values:
        return None
    if reducer == "max":
        return max(values)
    if reducer == "sum":
        return sum(values)
    # Fail loud: a reducer string this function does not implement must never silently fall
    # through to one of the above (CONVENTIONS §3).
    raise ValueError(f"unrecognized SENSOR_HEALTH_AGGREGATION reducer: {reducer!r}")


def locality_verdict(obs: Counter) -> Optional[str]:
    """One inside/outside verdict from an entity's per-endpoint observations.

    **Public because `ir/materialize.py` applies the identical rule one time scale up** — it
    resolves an entity's per-WINDOW verdicts into a per-PIT one, and re-implementing "majority,
    ties to local" there would be two copies of a judgment call that must not drift apart. The
    argument for the tie-break is the same at both scales, so it is stated once, here.

    `None` when there are none — the entity was never seen at a usable address, which is not
    the same claim as "external" and must not become it (`LES-006`).

    **Majority wins, and a tie goes to `local`.** Both halves are declared rather than
    incidental. A conflict is only reachable when one ENTITY spans addresses that disagree —
    the resolver merged a host that held an internal address and later a public one, or
    `local_nets` changed mid-capture — because for any single address Zeek's judgment is a
    pure function of that address. So the majority is over addresses, not noise, and it is
    the right summary. The tie-break is a genuine choice with no evidence behind it, resolved
    toward inclusion: an extra host in the twin is a visible, reviewable error, while a
    dropped one is invisible in exactly the output that is supposed to enumerate the network.
    `health["locality_conflicts"]` counts every entity that needed either rule, so a capture
    where this is doing real work says so.
    """
    if not obs:
        return None
    local, external = obs.get("local", 0), obs.get("external", 0)
    return "local" if local >= external else "external"


#: `ProfileEvent.parameter` -> (accumulator, unit) -- a fact true of the CAPTURE, not of any
#: entity in it (`Q29`). Same idiom `SERVICE_PROJECTIONS`/`IDENTITY_PROJECTIONS`/
#: `SENSOR_HEALTH_AGGREGATION` above establish, and for the same reason: a parameter is declined
#: by name, never by omission, and one absent from this table is a counted health fact
#: (`unmodelled_profile_parameters`, `WindowManager._add_profile`) rather than a silent drop. The
#: one structural difference from those three tables: there is no per-entity/per-kind loop here at
#: all, because a profile fact has no subject to key on -- the accumulator below summarizes the
#: WINDOW's own distribution for the parameter, once, not once per resolved entity. Unlike the
#: service side, there is deliberately no THIRD, by-name-decline table (`_SERVICE_NOT_PROJECTED`'s
#: analogue) here yet -- nothing has been proposed and rejected for this channel; when one is, it
#: earns that same discipline rather than a silent gap.
#:
#: Both elements are actually READ, matching the other three tables' own discipline (an element
#: nobody reads is documentation wearing a data costume): `accumulator` at `_add_profile` -- a
#: zero-argument factory, a class already is one, the same shape `NodeAcc` uses for its own
#: `field(default_factory=Quantiles)` sketches -- and `unit` at
#: `store/belief_store.py::profile_facts_rows`, carried into `profile_facts.unit` so a `tfc`
#: reader never has to hardcode what a bare `604800.0` means. Per-parameter MEANING (what it is,
#: why it is population-scoped) is prose, not data, and lives beside each entry below instead --
#: the same split `SERVICE_PROJECTIONS`'s own inline comments (`tunnel`, `ldap_op`) already use
#: for a one-off entry that needs more than the table's shape can carry.
#:
#: The channel's REAL contract today is narrower than "any accumulator, any value": every
#: declared parameter is `sketches.Quantiles` over a NON-NEGATIVE float, summarized at four fixed
#: quantile points (`_PROFILE_QUANTILE_POINTS`) -- reusing `Quantiles` inherits `Q8`'s
#: O(input)-not-O(output) caveat honestly rather than growing a second, differently-caveated
#: accumulator, but a declared `TopK`/`DistinctCount` entry would raise `AttributeError` at
#: `_close` (no `.quantiles()`), so the assert below enforces that narrower contract rather than
#: leaving it merely implied. Neither an integer seed nor a diurnal 24-bucket schedule --both
#: named in DESIGN_BRIEF §9's "fitted intensities/schedules/seeds" -- fits this shape; widening
#: past "one non-negative float, four quantiles" is a real design question for whoever adds the
#: second entry, not answered here.
PROFILE_PARAMETERS: dict[str, tuple[type, str]] = {
    # nextUpdate - thisUpdate: how long the issuing CA says an OCSP response's revocation
    # status may be cached before it must be re-checked. Population-scoped BY CONSTRUCTION
    # (`ingest/zeek/plugin.py::_ocsp` -- ocsp.log carries no connection tuple, so there is no
    # host to attribute it to); a `tfc` schedule candidate (DESIGN_BRIEF §9), not a twin_config
    # node/edge property. Always non-negative in principle -- `_add_profile` refuses and counts
    # (`profile_values_refused`) a negative reading rather than let a responder's own clock
    # skew publish a corrupted schedule parameter into the sketch (`LES-005`).
    "ocsp_cache_lifetime": (Quantiles, "seconds"),
}

assert all(cls is Quantiles for cls, _ in PROFILE_PARAMETERS.values()), (
    "PROFILE_PARAMETERS declares an accumulator this table's own contract does not support: "
    "`_close` calls `.quantiles(points=...)` on every accumulator unconditionally, so a "
    "TopK/DistinctCount entry would raise far from this declaration, not here. Generalize "
    "`_close`'s profile-summary logic before declaring a non-Quantiles parameter."
)

#: (quantile point, stat label) pairs, declared TOGETHER and in this order -- passed EXPLICITLY
#: to `sketches.Quantiles.quantiles(points=...)` at `_close`, never left to that method's own
#: default (`sketches._QPOINTS`), so this table's labels can never silently desynchronize from
#: what it actually publishes if that default ever changes. `zip(..., strict=True)` at the call
#: site is the other half of the same guarantee: a length mismatch raises instead of truncating
#: a stat off the end with nothing to say so (mirrors `plugin.py`'s import-time
#: `assert set(_COLUMNS) == set(_PARSERS)` for the identical class of drift).
_PROFILE_QUANTILE_POINTS: tuple[tuple[float, str], ...] = (
    (0.10, "p10"), (0.50, "p50"), (0.90, "p90"), (0.99, "p99"),
)


@dataclass
class NodeAcc:
    flows_out: int = 0
    flows_in: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    peers_out: DistinctCount = field(default_factory=DistinctCount)
    peers_in: DistinctCount = field(default_factory=DistinctCount)
    dst_ports: DistinctCount = field(default_factory=DistinctCount)
    listen_ports: Counter = field(default_factory=Counter)   # answered as responder
    #: Ports this node DIALLED as originator (`docs/JOURNAL_imprint_loop.md` GAP-L5). The
    #: client-side mirror of `listen_ports`, and the only evidence a pure client ever
    #: generates: a host that accepts nothing is invisible to a listen census, so 8 of this
    #: project's 13 reference hosts reached `cast` role-less and were dropped.
    #:
    #: Counted on EVERY originated tcp/udp flow, accepted or not — deliberately unlike
    #: `listen_ports`, which gates on FLOW_ACCEPTED. The two answer different questions: a
    #: listener is only proven by an accepted connection, whereas a client dialling a port
    #: that REFUSED it still tells you what that host was looking for. A domain member
    #: probing its DC is a domain member whether or not the DC answered.
    connect_ports: Counter = field(default_factory=Counter)
    proto_bytes: TopK = field(default_factory=lambda: TopK(16))
    flow_size_q: Quantiles = field(default_factory=Quantiles)
    duration_q: Quantiles = field(default_factory=Quantiles)
    interarrival_q: Quantiles = field(default_factory=Quantiles)
    #: None until the first flow. Not 0.0: a sentinel that is also a valid epoch
    #: cannot say "no flow yet" and "a flow at the epoch" apart.
    last_flow_ts: float | None = None
    #: Normalized transport outcomes as originator (`events.FLOW_STATE_CLASSES`), and the
    #: ONLY hygiene counter this bundle publishes. Two scalars, `s0`/`rej`, used to sit
    #: above this line counting the Zeek-verbatim strings `"S0"`/`"REJ"`; they were deleted
    #: 2026-08-26 (`Q34`) because `FlowEvent.state` is each sensor's OWN vocabulary, so on
    #: axquery (`O:<flags>/R:<flags>`) and Suricata (flag-derived) they published a
    #: confident `0` — "evidence of none", where the truth was "this build never looked"
    #: (`LES-006`). Do NOT re-add them keyed on `state_class` instead: `attempted` and
    #: `refused` are two of the eight keys below, and `features/vocabulary.py` declares
    #: `state_mix` with `closed=FLOW_STATE_CLASSES`, so both are guaranteed columns whether
    #: or not a window observes one. A scalar beside them would be a second name for a
    #: number already published in the same row, with nothing checking the two agree
    #: (`LES-101`). Whoever genuinely needs the finer, SENSOR-NATIVE distinction Zeek's
    #: `S0` drew against `SH`/`RSTOS0` wants a per-sensor raw-state mix, declared as such
    #: and documented as not cross-sensor comparable — not two scalars named for one
    #: sensor's spellings in a sensor-agnostic artifact.
    state_mix: Counter = field(default_factory=Counter)
    subbucket_flows: list[int] = field(default_factory=list)
    tainted: bool = False
    #: Durable service properties, `ServiceEvent.kind` -> Counter of projected fact keys
    #: (`SERVICE_PROJECTIONS`). Capped per kind at `SERVICE_MAX_KEYS`; overflow lands in
    #: `services_truncated` rather than disappearing.
    services: dict[str, Counter] = field(default_factory=dict)
    services_truncated: Counter = field(default_factory=Counter)
    #: Twin-segment membership: segment CIDR -> observations (`windowing/segments.py`).
    #: Populated only for endpoints judged `local` — an external address has a well-defined
    #: /24 that must not become a twin segment.
    #:
    #: **Deliberately uncapped**, unlike `services` above, and the reason is a cardinality
    #: argument rather than an oversight (`LES-006`'s "say which"). `services` is keyed on
    #: sensor-supplied strings whose count is unbounded by anything — one reverse proxy can
    #: serve thousands of SNIs. Segment membership is keyed on the ADDRESSES one entity
    #: resolves to, and that population is bounded upstream by the resolver's `MAX_HOST_BINDINGS`
    #: insert cap (`Q1`) — the HOST-scoped one, which is what governs how many addresses can
    #: anchor to one entity. (`NONHOST_WORKING_SET` is not a bound on this at all: it is an LRU
    #: dedup window over DNS answers, and it evicts losslessly rather than refusing.) A second cap
    #: here would be a bound on a bounded quantity, disclosed nowhere the first is not.
    #: `health["segments_observed"]` publishes the per-window distinct count so growth is
    #: visible if that reasoning ever stops holding.
    segments: Counter = field(default_factory=Counter)
    #: "local"/"external" -> observations (`segments.LOCALITY_CLASSES`). A Counter, not a
    #: bool, because an entity CAN be observed both ways — a resolver merge across a NAT
    #: boundary, or a `local_nets` that changed mid-capture — and collapsing that to a single
    #: verdict at accumulation time would destroy the evidence that the two disagreed.
    #: `_close` resolves it to one verdict and counts the disagreement.
    locality_obs: Counter = field(default_factory=Counter)
    #: Of those observations, how many came from address SHAPE rather than from the sensor.
    #: Drives `evidence_class="imputed"` on the resulting assertion.
    locality_imputed: int = 0


@dataclass
class EdgeAcc:
    """One (src, dst, proto) conversation in a window.

    `first_ts`/`last_ts` are an interval, so they are maintained with min/max on
    EVERY flow. Fixing `first_ts` at creation and overwriting `last_ts` reads
    correctly only on a perfectly ordered stream; under the residual disorder the
    bounded ingest reorder permits it produces `last_ts < first_ts` — a
    negative-duration edge — which reaches the store and the synthesizer intact.
    (The `__longtail__` aggregate row in `_close` has always used min/max; these
    two disagreeing about the same quantity was the actual bug.)
    """
    flows: int = 0
    bytes: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0


@dataclass
class WindowResult:
    window_start: float
    window_seconds: int
    nodes: dict            # entity -> node feature dict
    edges: list            # list of edge feature dicts
    identity_delta: list   # binding CHANGE dicts this window (`Q9`: opened/closed/final, never
                           # a restatement — `windowing.entities.EntityResolver.observe`)
    health: dict
    #: parameter -> {stat: value}, e.g. `{"ocsp_cache_lifetime": {"p10": ..., "n": ..., ...}}`
    #: (`PROFILE_PARAMETERS`, `Q29`) -- the fourth channel beside `nodes`/`edges`/`identity_delta`,
    #: for a fact with no subject. Named `capture_profile`, not `profile`: both this and `nodes`
    #: are a bare `dict[str, dict]`, so a generic `for k, v in win.____.items()` would read
    #: identically for the per-entity channel and this subject-less one -- the name has to carry
    #: "no subject" at every call site, not rely on a reader remembering which field is which
    #: (code review). Defaulted to `{}` so the six-field positional constructor every existing
    #: caller (production and test) already uses keeps working unchanged; an EMPTY dict is the
    #: honest reading for a window with no profile-shaped evidence at all, the same "no evidence"
    #: a quiet window's empty `nodes`/`edges` already carry -- not `None`, because nothing here is
    #: a maximum over evidence that might never arrive (contrast `health`'s
    #: `capture_loss_pct_max`, `LES-006`): a parameter that fires produces a value every time.
    capture_profile: dict = field(default_factory=dict)
    #: Entity merges LEARNED this window (`Q1`, D2) — `{from_entity, to_entity, reason, ts,
    #: evidence, identifiers}`. The fifth channel, and the one that exists BECAUSE resolution is
    #: forward-only: a merge learned in window 40 must not rewrite windows 1-39, so the windows
    #: that closed under the old id keep it and this says the two ids name one host. Read by
    #: `learners.extract_assertions` into `same_entity_as`, applied by the snapshot reader
    #: (`store/belief_store.py::snapshot`) — never by rewriting a closed window, which would
    #: re-spend `Q2`'s streaming win and make window close a function of the future.
    #:
    #: Defaulted, like `capture_profile` above and for the same reason: every existing positional
    #: six-field construction (production and test) keeps working. `[]` is the honest reading —
    #: a window that learned no merge, which is nearly all of them.
    entity_merges: list = field(default_factory=list)


class WindowManager:
    """Assign events to aligned windows; accumulate; emit WindowResults on close.

    Events arrive roughly time-ordered (the zeek plugin stream-merges them, with a
    bounded reorder buffer that repairs *most* disorder, not all of it). A window
    closes when an event lands past its end; `finalize()` flushes the remainder.

    What this layer guarantees about the residual disorder, and nothing more:

    * Every event whose ts falls outside the open window — any class, not just
      flows — is counted in `health["late_events"]`. The count is taken once, in
      `_add`, before dispatch, so no event class can be folded into a neighbouring
      window while both windows still read clean.
    * No out-of-order event corrupts an accumulator in place. Sub-bucket indices
      are floored and clamped (never wrapped), per-node interarrival is measured
      only forward (`health["interarrival_skipped"]` counts the rest), and edge
      intervals are min/max.
    * A window's features are still *attributed* by the window that was open when
      the event arrived, so a late event lands in the wrong window's counts. That
      is a real limitation, and the reason the count exists: it is disclosed in the
      health record, not repaired here (brief §5.4 #5 — the asterisk travels).
    """

    def __init__(self, window_seconds: int = 3600, subbuckets: int = 12,
                 resolver: EntityResolver | None = None,
                 unparsed_log_types_provider: Callable[[], dict[str, int] | None] | None = None,
                 unrecognized_log_types_provider: Callable[[], dict[str, int] | None] | None = None):
        if window_seconds <= 0 or subbuckets <= 0:
            raise ValueError("window_seconds and subbuckets must be positive")
        self.window_seconds = window_seconds
        self.subbuckets = subbuckets
        #: `docs/WORK_ORDER_zeek_stem_coverage.md` D5: a zero-arg callable, read FRESH
        #: at every `_close()`, never cached here at construction -- because the ONE
        #: run-level fact it exposes (an ingest plugin's own `unparsed_stems`,
        #: `ingest/base.py`) is discovered LAZILY, no earlier than the first event
        #: pulled from that plugin's `events()`, and `WindowManager` is constructed
        #: (here) before a single event has been read. A callable, not a plain value,
        #: is what lets the caller (`cli._pipeline`, which owns the plugin -- this
        #: module must not import `ingest`, CONVENTIONS §3) hand this in NOW and have
        #: it resolve correctly LATER, without buffering a single window (`Q2`). WHY
        #: reading it fresh at every `_close()` -- including the FIRST one -- is
        #: correct, and not a rediscovery risk each time this module changes, is
        #: `ingest/zeek/plugin.py::events`'s to state once; this is the ONE canonical
        #: explanation (code review nice-to-have: the identical argument used to be
        #: restated near-verbatim in four separate files, the `LES-087`/`LES-093`
        #: shape of one invariant getting out of step with itself the moment only
        #: some of the copies are updated). This does not reopen "window close is a
        #: pure function of (events, resolver state, code build)" (module docstring):
        #: the value returned is itself a deterministic function of the same SOURCE
        #: that produced the events, constant for the whole run, and read here
        #: through `sorted()` only -- never through an unordered iteration that
        #: could vary run to run.
        self._unparsed_log_types_provider = unparsed_log_types_provider
        #: `IngestPlugin.unrecognized_stems`'s provider (code review `S4`, additive to
        #: D5): identical shape and discipline as `_unparsed_log_types_provider`
        #: immediately above -- see that one's own docstring, not repeated here --
        #: for the SUBSET of `unparsed_stems` with no `_SKIP` entry. Kept as a
        #: SEPARATE callable rather than derived from `_unparsed_log_types_provider`'s
        #: own return value in `_close()`, because deriving it here would require
        #: this module to know about `_SKIP` (`ingest/zeek/plugin.py`), which
        #: `windowing` must never import (CONVENTIONS §3) -- the PLUGIN does that
        #: filtering, once, at the same synchronous point it computes
        #: `unparsed_stems`, and hands both answers to the driver as two
        #: independent, already-filtered values.
        self._unrecognized_log_types_provider = unrecognized_log_types_provider
        self._subbucket_seconds = window_seconds / subbuckets
        self.resolver = resolver or EntityResolver()
        self._cur_start: float | None = None
        self._nodes: dict[str, NodeAcc] = {}
        self._edges: dict[tuple, EdgeAcc] = {}
        self._identity_delta: list[dict] = []
        self._counts: Counter = Counter()
        self._loss_pct: list[float] = []
        #: `stats.log`-sourced kind -> readings collected this window, reduced at close per
        #: `SENSOR_HEALTH_AGGREGATION`. Replaces two hardcoded lists (`_pkts_dropped`/`_pkt_lag`)
        #: so a new declared kind (`pkts_proc`/`bytes_recv`, code review addendum) needs no
        #: change here — only a `SENSOR_HEALTH_AGGREGATION` entry.
        self._sensor_health: dict[str, list[float]] = {}
        self._late: int = 0
        self._interarrival_skipped: int = 0
        self._unknown_state: int = 0
        self._imputed_counters: int = 0
        self._unmodelled_health: int = 0
        #: ServiceEvent kinds in neither `SERVICE_PROJECTIONS` nor `_SERVICE_NOT_PROJECTED` —
        #: a kind nobody has ruled on. Surfaced as a health fact, never silently dropped.
        self._unmodelled_service: int = 0
        #: IdentityEvent kinds that produced a binding (`EntityResolver.observe()` returned one)
        #: but are absent from `IDENTITY_PROJECTIONS` — mirrors `_unmodelled_service` one layer
        #: over (`M5`, code review).
        self._unmodelled_identity: int = 0
        #: Flow endpoints whose locality had to be imputed from address shape because the
        #: sensor declared none, and endpoints where not even that was possible (the subject
        #: did not parse as an address). Two counters, not one: the first is a fidelity
        #: measure — how much of this window's segment map is a guess — and the second is an
        #: input-shape complaint. Collapsing them would make a capture from a Zeek with no
        #: `Site::local_nets` indistinguishable from one carrying malformed addresses.
        self._locality_imputed: int = 0
        self._locality_unknown: int = 0
        #: Endpoints judged local whose address names no provisionable network.
        self._segments_declined: int = 0
        #: `ProfileEvent.parameter` -> accumulator (`PROFILE_PARAMETERS`), reduced to quantiles at
        #: `_close`. No resolver involved — a profile fact has no subject to resolve (`Q29`).
        self._profile: dict[str, object] = {}
        #: `ProfileEvent.parameter`s absent from `PROFILE_PARAMETERS` — mirrors
        #: `_unmodelled_service`/`_unmodelled_identity` one channel over.
        self._unmodelled_profile: int = 0
        #: Recognized-parameter `ProfileEvent`s whose VALUE was refused — negative, today the
        #: only declared shape (`_add_profile`). `LES-005` recurring: its own origin incident
        #: was a negative interarrival reaching a quantile sketch with nothing raised; refused
        #: and counted here rather than silently accumulated (code review `M2`).
        self._profile_values_refused: int = 0
        self._ingest_unordered: int | None = None
        #: Per-ENTITY peer-tail spills into a `LONGTAIL_ENTITY` edge row, across the whole run —
        #: `_close` increments once per source entity that exceeded `TOP_PEERS`, so a single
        #: window contributes as many as it had busy entities (measured on the 2026-08-11
        #: baseline: 22 spills across 3 windows). RUN-level on purpose, deliberately not reset in
        #: `_close` with every counter beside it, because its consumer is the run-scoped entity
        #: roster (`entity_roster`), which has to state whether that mint path fired AT ALL this
        #: run (`LES-017`).
        self._longtail_spills: int = 0

    def process(self, events: Iterable[Event]) -> Iterator[WindowResult]:
        for ev in events:
            start = ev.ts - (ev.ts % self.window_seconds)
            if self._cur_start is None:
                self._cur_start = start
            elif start > self._cur_start:
                yield self._close()
                self._cur_start = start
            self._add(ev)
        # caller decides whether to finalize (streaming vs batch)

    def finalize(self) -> Iterator[WindowResult]:
        if self._cur_start is not None and (self._nodes or self._counts):
            # Every binding still open at end of stream is published once here, into the last
            # window, carrying its final `last_seen`/`count` (`EntityResolver.flush_open`). This
            # is the other half of change-based emission (`Q9`): without it, a binding that opened
            # and was never superseded would be on record exactly once, at creation, with
            # `count=1` and a `last_seen` equal to its `from_ts` — the reduction bought by
            # throwing away the observation extent, which is most of what a binding record is for.
            # Inside the guard, not before it: a flush appended to a window that then never closes
            # is a fact written nowhere.
            self._identity_delta.extend(self.resolver.flush_open())
            yield self._close()

    # -- accumulation -----------------------------------------------------------

    def _node(self, entity: str) -> NodeAcc:
        acc = self._nodes.get(entity)
        if acc is None:
            acc = NodeAcc(subbucket_flows=[0] * self.subbuckets)
            self._nodes[entity] = acc
        return acc

    def _add(self, ev: Event) -> None:
        if self._cur_start is None:  # fail loud: no window is open to accumulate into
            raise RuntimeError("WindowManager._add before a window was opened")
        self._counts[type(ev).__name__] += 1
        # Lateness is a property of the EVENT, so it is measured once here, ahead of
        # dispatch. Measuring it inside `_add_flow` left every other class silently
        # mis-attributed: a late SensorHealthEvent folded into the next window makes
        # one window read clean and its neighbour read degraded, with no counter
        # anywhere saying so.
        if not 0 <= ev.ts - self._cur_start < self.window_seconds:
            self._late += 1
        if isinstance(ev, FlowEvent):
            self._add_flow(ev)
        elif isinstance(ev, IdentityEvent):
            # A LIST since `Q1`'s resolver landed, not an `Optional[dict]`: one DHCP row carries
            # both a mac and a host name, and it is their co-observation in one row that links the
            # two namespaces (`windowing.entities.EntityResolver._link`). It may also be EMPTY for
            # a row that only restated a binding already known — the `Q9` change-detection this
            # loop exists downstream of.
            for binding in self.resolver.observe(ev):
                self._identity_delta.append(binding)
                # `IDENTITY_PROJECTIONS` mirrors `SERVICE_PROJECTIONS`/`_SERVICE_NOT_PROJECTED`
                # one layer over (`M5`, code review): a bound kind absent from it is a kind
                # nobody has ruled on, and must surface rather than silently pick up a default
                # predicate/confidence in `extract_assertions`. Counted only on `opened`, because
                # that is the only change `extract_assertions` turns into an assertion — counting
                # the `closed`/`final` records too would report one unruled kind two or three
                # times for one binding.
                if binding["change"] == "opened" and binding["kind"] not in IDENTITY_PROJECTIONS:
                    self._unmodelled_identity += 1
        elif isinstance(ev, AlertEvent):
            for ip in (ev.ip, ev.peer_ip):
                if ip:
                    self._node(self.resolver.resolve(ip, ev.ts)).tainted = True
        elif isinstance(ev, SensorHealthEvent):
            # Named kinds only. `event_counts` alone could not tell a recognized
            # kind from an unmodelled one — both read `SensorHealthEvent: 1` — so
            # the ones this layer does not model are counted as such.
            if ev.kind == "capture_loss":
                self._loss_pct.append(ev.value)
            elif ev.kind in SENSOR_HEALTH_AGGREGATION:
                self._sensor_health.setdefault(ev.kind, []).append(ev.value)
            elif ev.kind == "ingest_unordered":
                # The ingest layer could not put a row back in ts order. Nothing
                # else downstream can recover that fact, and until it reached here
                # it only ever reached a terminal (brief §5.4 #5).
                self._ingest_unordered = (self._ingest_unordered or 0) + 1
            else:
                self._unmodelled_health += 1
        elif isinstance(ev, ServiceEvent):
            self._add_service(ev)
        elif isinstance(ev, ProfileEvent):
            self._add_profile(ev)

    def _add_profile(self, ev: ProfileEvent) -> None:
        """Accumulate a subject-less measurement into its parameter's window distribution (`Q29`).

        Mirrors `_add_service`'s discipline one channel over: a `parameter` absent from
        `PROFILE_PARAMETERS` is counted (`_unmodelled_profile`), never silently accumulated under
        a guessed shape — an undeclared parameter has no known accumulator or unit, and that is
        exactly the ambiguity this channel exists to close, applied per parameter rather than to
        the channel as a whole. Unlike `_add_service`/`_add_flow` there is no `resolver.resolve`
        call anywhere in this method: a profile fact has no address to fold into an entity.

        The negative-value gate below is `_add_flow`'s own interarrival shape (`if ev.ts >=
        s.last_flow_ts: accumulate else: count`), one line simpler because a profile value needs
        no accumulator STATE to validate — `_ocsp` already computed the whole delta before this
        event existed. `LES-005` recurring verbatim: its own origin incident was a negative
        interarrival reaching a quantile sketch with nothing raised, and every parameter this
        channel declares today (`PROFILE_PARAMETERS`'s own comment) is a non-negative
        duration/rate/cadence, so a negative reading is evidence the SOURCE was wrong — a
        responder's own clock skew, for `ocsp_cache_lifetime` — not a value to clamp into the
        sketch wearing an observed face (code review `M2`).
        """
        decl = PROFILE_PARAMETERS.get(ev.parameter)
        if decl is None:
            self._unmodelled_profile += 1
            return
        if ev.value < 0:
            self._profile_values_refused += 1
            return
        accumulator_cls, _unit = decl
        acc = self._profile.get(ev.parameter)
        if acc is None:
            acc = self._profile[ev.parameter] = accumulator_cls()
        acc.add(ev.value)

    def _add_service(self, ev: ServiceEvent) -> None:
        """Project a service EVENT onto a durable per-entity PROPERTY (`Q27`).

        The projection keeps only the attrs `SERVICE_PROJECTIONS` declares for the kind, joined
        with `|` in declared order. Everything else stays on the event and is never published as a
        node feature — `ssl`'s full attr set and `software`'s `unparsed_version` (a raw User-Agent,
        very nearly unique per client) would enter the categorical vocabulary as unbounded
        dimensions (`LES-017`). What survives is bounded by the *service* population, not the
        client population, which is what the measured 45x-113x collapse ratios describe.
        """
        if not ev.ip:
            return
        proj = SERVICE_PROJECTIONS.get(ev.kind)
        if proj is None:
            # Declined by name, or genuinely new. Only the second is a problem, and only the
            # second is counted — an unrecognized kind must surface, never vanish (CONVENTIONS §3).
            if ev.kind not in _SERVICE_NOT_PROJECTED:
                self._unmodelled_service += 1
            return
        attrs, _, _ = proj
        parts = [v for k in attrs if (v := ev.attrs.get(k, ""))]
        if not parts:
            return          # nothing identifying in this row; do not mint an empty key
        key = "|".join(parts)
        a = self._node(self.resolver.resolve(ev.ip, ev.ts))
        kept = a.services.setdefault(ev.kind, Counter())
        if key in kept or len(kept) < SERVICE_MAX_KEYS:
            kept[key] += 1
        else:
            a.services_truncated[ev.kind] += 1   # disclosed as a feature, never dropped silently

    def _observe_locality(self, entity: str, addr: str, sensor_says: Optional[bool]) -> None:
        """Record one endpoint's inside/outside verdict and, if inside, its segment.

        Called twice per flow — `resolve()`'s own hot-path neighbour — so it stays a
        dict-increment and one `ipaddress` parse. The parse is the cost; it is paid here
        rather than at close because close no longer has the address.
        """
        verdict, imputed = locality(addr, sensor_says)
        if verdict is None:
            # Neither the sensor nor the address shape could say anything — the subject was
            # not an address at all. No evidence, so nothing is recorded and nothing is
            # imputed; the count is what makes that visible instead of silent.
            self._locality_unknown += 1
            return
        acc = self._node(entity)
        acc.locality_obs[verdict] += 1
        if imputed:
            acc.locality_imputed += 1
            self._locality_imputed += 1
        if verdict == "local":
            seg = segment_of(addr)
            if seg is not None:
                acc.segments[seg] += 1
            else:
                # Local, but on nothing a twin could provision — multicast, link-local,
                # loopback (`segments.is_provisionable`). Counted, not silently skipped:
                # on the real baseline this is 105 hosts' worth of `fe80::` alone, and a
                # decline that leaves no trace is indistinguishable from no traffic.
                self._segments_declined += 1

    def _add_flow(self, ev: FlowEvent) -> None:
        src = self.resolver.resolve(ev.src_ip, ev.ts)
        dst = self.resolver.resolve(ev.dst_ip, ev.ts)
        # floor(), not int(): int() truncates TOWARD ZERO, so a flow up to one
        # sub-bucket behind the window start computed sb == 0, passed the `0 <= sb`
        # guard, and was credited to sub-bucket 0 of the wrong window — the silent
        # absorption the guard exists to prevent. Lateness itself is counted in
        # `_add` for every event class; the clamp here is the defensive half, which
        # only has to keep a negative index from wrapping into the tail.
        sb = math.floor((ev.ts - self._cur_start) / self._subbucket_seconds)
        sb = 0 if sb < 0 else min(sb, self.subbuckets - 1)
        if ev.state_class == "unknown":
            self._unknown_state += 1
        if ev.counters_imputed:
            self._imputed_counters += 1
        total = ev.bytes_out + ev.bytes_in
        proto = ev.service or ev.proto or "?"
        # Segment membership, per ENDPOINT (`WORK_ORDER_synth_v1.md` phase 1). Keyed on the
        # entity but derived from the ADDRESS, which is the only place both are in hand: above
        # this line everything speaks addresses, below it everything speaks entities. Doing it
        # in `learners/` instead would mean putting the addresses into node features purely so
        # a later layer could redo CIDR arithmetic on them.
        self._observe_locality(src, ev.src_ip, ev.local_src)
        self._observe_locality(dst, ev.dst_ip, ev.local_dst)

        s = self._node(src)
        s.flows_out += 1
        s.bytes_out += ev.bytes_out
        s.bytes_in += ev.bytes_in
        s.peers_out.add(dst)
        s.dst_ports.add(ev.dst_port)
        if ev.proto in ("tcp", "udp"):
            # `ev.proto`, NOT the local `proto` — they are different things and mixing them up
            # is a cross-sensor correctness bug, caught by `verify_suricata_equivalence`. The
            # local is `ev.service or ev.proto or "?"`, an APPLICATION-protocol label for
            # `proto_mix`. Zeek usually leaves `service` empty, so it falls through to the
            # transport and a gate on it appears to work; Suricata fills it from `app_proto`
            # ("dns", "krb5", ...), so the same gate matched nothing and this feature was
            # CONSTANTLY EMPTY on that sensor while looking like a confident measurement —
            # the `Q34` fingerprint exactly. `listen_ports` gets this right one branch below;
            # this now matches it.
            #
            # Transport gate itself: for icmp, Zeek puts the message TYPE in `dst_port`, so
            # counting it would invent a "dialled 8/icmp" fact about a host that dialled no
            # port at all.
            s.connect_ports[(ev.dst_port, ev.proto)] += 1
        s.proto_bytes.add(proto, total)
        s.flow_size_q.add(total)
        s.duration_q.add(ev.duration)
        # Forward gaps only, and the node's clock never moves backwards. A flow
        # arriving behind this node's last one recorded a NEGATIVE interarrival and
        # then rewound `last_flow_ts`, poisoning the following reading too — the
        # quantiles corrupted with no counter anywhere admitting it.
        if s.last_flow_ts is None:
            s.last_flow_ts = ev.ts
        elif ev.ts >= s.last_flow_ts:
            s.interarrival_q.add(ev.ts - s.last_flow_ts)
            s.last_flow_ts = ev.ts
        else:
            self._interarrival_skipped += 1
        s.subbucket_flows[sb] += 1
        # `state_class`, never `state`: one line here, and deliberately only one. `Q34` was
        # two more, gating on `ev.state == "S0"` / `"REJ"` — the sensor's raw word — which
        # is a Zeek-only test that no other plugin's vocabulary can ever satisfy. See
        # `NodeAcc.state_mix` for why nothing replaced them.
        s.state_mix[ev.state_class] += 1

        d = self._node(dst)
        d.flows_in += 1
        d.bytes_in += ev.bytes_out
        d.bytes_out += ev.bytes_in
        d.peers_in.add(src)
        d.subbucket_flows[sb] += 1
        if ev.state_class in FLOW_ACCEPTED and ev.proto in ("tcp", "udp"):
            # The responder accepted -> a real listener. Gating on state == "SF"
            # alone missed every connection that established and was later reset,
            # which is ~40% of flows on a real capture.
            #
            # Transport gate: `dst_port` is only a port for tcp/udp. For icmp Zeek
            # puts the ICMP *type* there and can still assign an accepted state, so
            # an echo request became a "listens_on 8/icmp" claim at confidence 0.95
            # — a service asserted on a host that has no such port at all.
            d.listen_ports[(ev.dst_port, ev.proto)] += 1

        e = self._edges.get((src, dst, proto))
        if e is None:
            e = self._edges[(src, dst, proto)] = EdgeAcc(first_ts=ev.ts, last_ts=ev.ts)
        e.flows += 1
        e.bytes += total
        e.first_ts = min(e.first_ts, ev.ts)
        e.last_ts = max(e.last_ts, ev.ts)

    # -- run-scoped -------------------------------------------------------------

    def entity_roster(self) -> dict:
        """The run's whole entity namespace, assembled from BOTH layers that mint into it.

        `LES-017`'s rule is that a published namespace is the union of every path that can mint a
        member — and the resolver is not that union. It owns three paths (a bound entity, D4's
        bare-address fallback, and the `"?"` sentinel for an empty address); the fourth,
        `LONGTAIL_ENTITY`, is minted HERE, by `_close`, as an edge `dst` that never gets a `nodes`
        row at all. A roster built from the resolver alone would publish a namespace with a member
        it had never heard of — which is exactly the failure that lesson is about, one layer up.

        This does not fix `Q11` (the long-tail spill is still untested and still dangling); it
        makes the dangling adjacency READABLE from the run artifact rather than inferable only by
        noticing an edge `dst` with no matching node row.

        Called once per run, after the stream, by the driver (`cli._pipeline`).
        """
        roster = self.resolver.roster()
        roster["mint_paths"].append({
            "path": "longtail", "pattern": LONGTAIL_ENTITY, "enumerated": True,
            # ONE member of the namespace no matter how often it is minted — the sentinel is a
            # single name shared by every spill, not one per spill. `spills` is the separate
            # question ("how much adjacency is hidden behind it"), counted per SOURCE ENTITY
            # rather than per window, since one window can spill for many entities.
            "entities": 1 if self._longtail_spills else 0,
            "spills": self._longtail_spills,
            "note": f"minted by the windowing layer past TOP_PEERS={TOP_PEERS} as an aggregate "
                    "edge dst; never a resolver output and never a node row of its own (`Q11`)"})
        return roster

    # -- close ------------------------------------------------------------------

    def _close(self) -> WindowResult:
        nodes = {}
        for ent, a in self._nodes.items():
            total_flows = a.flows_out + a.flows_in
            mean_sb = total_flows / self.subbuckets if total_flows else 0.0
            var_sb = (sum((x - mean_sb) ** 2 for x in a.subbucket_flows) / self.subbuckets
                      if total_flows else 0.0)
            nodes[ent] = {
                "flows_out": a.flows_out, "flows_in": a.flows_in,
                "bytes_out": a.bytes_out, "bytes_in": a.bytes_in,
                "client_ratio": a.flows_out / total_flows if total_flows else 0.0,
                "peers_out": len(a.peers_out), "peers_in": len(a.peers_in),
                "dst_ports_distinct": len(a.dst_ports),
                "listen_ports": sorted(f"{p}/{pr}" for (p, pr), n in
                                       a.listen_ports.items() if n > 0),
                # GAP-L5. Capped at the 24 most-dialled, because a scanner or a busy client
                # can otherwise put thousands of ports in one window's feature row; the cap
                # is on the PUBLISHED list only, and ties break on the port string so the
                # output stays byte-stable for the determinism check.
                "connect_ports": [f"{p}/{pr}" for (p, pr), _n in sorted(
                    a.connect_ports.items(), key=lambda kv: (-kv[1], f"{kv[0][0]}/{kv[0][1]}")
                )[:24]],
                "proto_mix": a.proto_bytes.normalized(),
                "flow_size_q": a.flow_size_q.quantiles(),
                "duration_q": a.duration_q.quantiles(),
                "interarrival_q": a.interarrival_q.quantiles(),
                "subbucket_flows": a.subbucket_flows,
                "burstiness": (var_sb / mean_sb) if mean_sb else 0.0,
                # The window's whole hygiene picture, and the only shape of it published
                # (`Q34`): eight normalized classes, sorted -> byte-stable, summing to
                # `flows_out` — so a consumer wanting "attempted and unanswered" reads
                # `state_mix["attempted"]`, and its ABSENCE is a proven zero rather than
                # an unknown, because the keys account for every flow this entity opened.
                "state_mix": dict(sorted(a.state_mix.items())),
                "tainted": a.tainted,
                # sorted at both levels: byte-stable (`LES-002`)
                "services": {k: sorted(v) for k, v in sorted(a.services.items())},
                "services_truncated": dict(sorted(a.services_truncated.items())),
                # -- twin placement (`WORK_ORDER_synth_v1.md` phase 1) --------------------
                # `locality` is the verdict, `segments` is where it puts the host, and
                # `locality_imputed` says whether the verdict was the sensor's or ours.
                #
                # `None` locality means NO endpoint of this entity ever carried a usable
                # address — not "external" (`LES-006`). `segments: []` on a `local` entity
                # is likewise not a contradiction to paper over; it cannot happen today
                # (a local verdict implies a parsed address implies a segment) and if it
                # ever does, an empty list is the honest reading.
                "locality": locality_verdict(a.locality_obs),
                "segments": sorted(a.segments),
                "locality_imputed": a.locality_imputed > 0,
            }
        # per-entity top-K peer edges exact; tail spilled to one aggregate row
        by_src: dict[str, list] = defaultdict(list)
        for (src, dst, proto), e in self._edges.items():
            by_src[src].append((src, dst, proto, e))
        edges = []
        for src, items in by_src.items():
            items.sort(key=lambda t: t[3].bytes, reverse=True)
            for s_, d_, p_, e in items[:TOP_PEERS]:
                edges.append({"src": s_, "dst": d_, "proto": p_, "flows": e.flows,
                              "bytes": e.bytes, "first_ts": e.first_ts, "last_ts": e.last_ts})
            tail = items[TOP_PEERS:]
            if tail:
                self._longtail_spills += 1
                edges.append({"src": src, "dst": LONGTAIL_ENTITY, "proto": "*",
                              "flows": sum(e.flows for *_, e in tail),
                              "bytes": sum(e.bytes for *_, e in tail),
                              "first_ts": min(e.first_ts for *_, e in tail),
                              "last_ts": max(e.last_ts for *_, e in tail),
                              "tail_peers": len(tail)})
        # Subject-less measurements, reduced to one quantile summary per parameter (`Q29`).
        # Sorted by parameter name (an open vocabulary, however small today); each parameter's
        # own stat dict is built from `_PROFILE_QUANTILE_POINTS`' FIXED (point, label) pairs --
        # points passed EXPLICITLY (never the sketch's own default) and `strict=True` (never a
        # silently truncated stat) -- plus `n`, the sample count behind the summary: two windows
        # 1 and 1031 observations apart would otherwise publish byte-identical four-row
        # summaries with nothing to tell them apart (`Quantiles.__len__`, code review `S1`).
        _q_points = tuple(p for p, _ in _PROFILE_QUANTILE_POINTS)
        _q_labels = tuple(lbl for _, lbl in _PROFILE_QUANTILE_POINTS)
        capture_profile = {}
        for param, acc in sorted(self._profile.items()):
            stats = dict(zip(_q_labels, acc.quantiles(points=_q_points), strict=True))
            stats["n"] = len(acc)
            capture_profile[param] = stats
        # Three kinds of field live here, and the difference is load-bearing:
        #   * counts this layer observes itself -> an honest 0 when nothing happened;
        #   * maxima over evidence that may never arrive -> None, because "no
        #     evidence" and "evidence of none" are different findings (§5.4 #5);
        #   * `ingest_unordered` and `unparsed_log_types`/`unrecognized_log_types`,
        #     which only exist if an upstream layer audits something and says so ->
        #     None means unaudited, NOT clean (`docs/WORK_ORDER_zeek_stem_coverage.md`
        #     D5).
        #
        # `unparsed_log_types`/`unrecognized_log_types`: names only, sorted -- a SET
        # is idempotent under union, so an operator aggregating across windows cannot
        # double-count it the way a summed count would (this is also why the per-stem
        # COUNTS live in `runs`, once per run, not here -- D5's own reasoning,
        # `cli._pipeline`). `None` when no provider was supplied at all (a caller that
        # never wired an ingest plugin in, e.g. most tests in this file); the
        # provider itself returning `None` means one WAS supplied but the plugin
        # behind it has nothing analogous to report (`IngestPlugin.unparsed_stems`/
        # `.unrecognized_stems`'s own contract); `[]` means the plugin looked and
        # found nothing left over. Deliberately NOT reset per window like every
        # counter above: the fact is source-level, constant for the whole run, so
        # re-reading the SAME provider at every `_close()` is correct, not stale
        # state escaping the accumulator reset below.
        #
        # "Source-level, constant for the whole run" means constant for the run's
        # own SOURCE, not for the underlying capture in the abstract (code review
        # `S6`): re-running `aximprint ingest`/`features` over a SINGLE window
        # directory sliced out of a `harvest`-derived cold tree, instead of the
        # whole tree, is a genuinely DIFFERENT source with its own, smaller stem
        # set, so its `unparsed_log_types`/`unrecognized_log_types` can legitimately
        # differ from a full-tree run's -- not a determinism break (the SAME source,
        # re-run, still agrees with itself), but exactly the shape a determinism
        # break LOOKS like from one window's own health record alone, worth naming
        # here rather than leaving the next reader to debug it as one.
        #
        # `unrecognized_log_types` (code review `S4`, additive to D5, no reversal):
        # the SUBSET of `unparsed_log_types` with no `_SKIP` entry -- `unparsed_log_
        # types` is right for the store's honesty claim (every one of these stems
        # really did go unparsed) but merges away a distinction the STORE cannot
        # recover on its own: `_SKIP` lives in `ingest`, which every store consumer
        # is forbidden to import (CONVENTIONS §3), and is this BUILD's own table
        # while the store is read as-of-T across many builds, so deriving the split
        # at READ time instead of recording it at WRITE time would re-read history
        # wrong the day a stem moves from skipped to parsed. This field is `[]`
        # today on a real baseline (`Q24`'s ruling closed the unrecognized set) and
        # stays meaningful the day it is not -- the whole reason D3 wanted it kept
        # distinguishable rather than folded permanently into the 7-entries-forever
        # skip set.
        _unparsed_stems = (self._unparsed_log_types_provider()
                           if self._unparsed_log_types_provider is not None else None)
        _unrecognized_stems = (self._unrecognized_log_types_provider()
                               if self._unrecognized_log_types_provider is not None else None)
        health = {
            "event_counts": dict(self._counts),
            "capture_loss_pct_max": max(self._loss_pct) if self._loss_pct else None,
            # None, not 0.0: the sensor reported no drop/lag/proc/recv evidence for this
            # window. Some packet sources leave stats.log's link counters unset, and a zero
            # here would be an imputed "clean" wearing an observed face (§3 Provenance) —
            # `_reduce_sensor_health` enforces this per kind, keyed by `SENSOR_HEALTH_AGGREGATION`
            # (declared once, sorted here so key order in the output cannot depend on dict
            # insertion/iteration order, `LES-002`) rather than two hand-written lines that would
            # need a third and fourth copy for every kind this table grows by.
            **{
                f"sensor_{kind}_{reducer}":
                    _reduce_sensor_health(reducer, self._sensor_health.get(kind))
                for kind, reducer in sorted(SENSOR_HEALTH_AGGREGATION.items())
            },
            # Events of ANY class whose ts fell outside the window that was open
            # when they arrived. They are still accumulated — into this window — so
            # this count is what tells a reader the attribution is approximate.
            "late_events": self._late,
            "interarrival_skipped": self._interarrival_skipped,
            "unknown_state_flows": self._unknown_state,
            "flows_imputed_counters": self._imputed_counters,
            "unmodelled_health_kinds": self._unmodelled_health,
            "unmodelled_service_kinds": self._unmodelled_service,
            "unmodelled_identity_kinds": self._unmodelled_identity,
            "unmodelled_profile_parameters": self._unmodelled_profile,
            "profile_values_refused": self._profile_values_refused,
            "ingest_unordered": self._ingest_unordered,
            "unparsed_log_types": sorted(_unparsed_stems) if _unparsed_stems is not None else None,
            "unrecognized_log_types": (sorted(_unrecognized_stems)
                                       if _unrecognized_stems is not None else None),
            "entities_active": len(nodes),
            # -- twin placement (`WORK_ORDER_synth_v1.md` phase 1) ------------------------
            # How much of this window's segment map is evidence and how much is inference.
            # `locality_imputed_endpoints` counts flow ENDPOINTS (two per flow), not flows,
            # because that is the unit the verdict is made at. On a Zeek that declares
            # `Site::local_nets` this is 0 and the map is entirely the sensor's; on one that
            # does not it equals the endpoint count and every derived assertion carries
            # `evidence_class="imputed"` — the number is what tells those two apart without
            # opening the assertions table.
            "locality_imputed_endpoints": self._locality_imputed,
            "locality_unknown_endpoints": self._locality_unknown,
            "segments_declined_endpoints": self._segments_declined,
            "locality_conflicts": sum(
                1 for a in self._nodes.values()
                if a.locality_obs.get("local", 0) and a.locality_obs.get("external", 0)),
            "segments_observed": len({s for a in self._nodes.values() for s in a.segments}),
            # The resolver's own per-window facts, TAKEN (and reset) rather than read, in the same
            # place every counter above is reset — `resolver_ambiguity` (now `None` when nothing
            # resolved through host-scoped evidence, never a hardcoded `0.0`),
            # `resolver_anchored_resolutions` (its denominator, published so the rate is
            # interpretable rather than merely present), `unscoped_identity_kinds` and
            # `identity_bindings_refused`. Spread from ONE call so the resolver's window state
            # cannot be read twice and reset once (`windowing.entities.EntityResolver
            # .take_window_stats`); the dict it returns is a literal, so key order here is stable
            # by construction (`LES-002`), exactly as `SENSOR_HEALTH_AGGREGATION`'s spread above.
            **self.resolver.take_window_stats(),
        }
        result = WindowResult(self._cur_start, self.window_seconds, nodes, edges,
                              self._identity_delta, health, capture_profile,
                              self.resolver.take_merges())
        self._nodes, self._edges = {}, {}
        self._identity_delta, self._counts, self._loss_pct = [], Counter(), []
        self._sensor_health, self._late = {}, 0
        self._interarrival_skipped = self._unknown_state = 0
        self._imputed_counters = self._unmodelled_health = self._unmodelled_service = 0
        self._unmodelled_identity = 0
        self._locality_imputed = self._locality_unknown = self._segments_declined = 0
        self._profile, self._unmodelled_profile = {}, 0
        self._profile_values_refused = 0
        self._ingest_unordered = None
        return result
