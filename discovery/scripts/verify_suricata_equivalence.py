#!/usr/bin/env python3
"""verify_suricata_equivalence — the Track B acceptance gate.

`docs/WORK_ORDER_axrange_integration.md` Track B: "a Suricata capture ingests and windows
EQUIVALENTLY to a Zeek capture of the same traffic." This script is where "equivalently" is
given a checkable definition, in code, rather than left as a word review can interpret
however a given day's diff needs it to mean.

## Method

`scripts/verify_determinism.py::_synthetic_cast()` generates ONE seeded traffic plan (host
cast, flows, identity facts, service facts). `build_synthetic_capture` renders it as Zeek
logs; `build_synthetic_suricata_capture` renders the SAME plan as `eve.json`. Both are run
through the real ingest plugin (`aximprint.ingest.get_plugin`) and the real `WindowManager` —
no shortcut, no mock. This script then compares the two resulting `WindowResult` streams.

## What "equivalent" means here, and why NOT byte-identical

Byte-identical is impossible by construction — two different sensors, two different field
sets, two different state vocabularies (task brief) — so this gate defines "equivalent"
STRUCTURALLY, at the level DESIGN_BRIEF's own event model is meant to make sensor-agnostic:

  * the SAME address set appears as traffic endpoints ("node set"),
  * the SAME (src, dst) conversations appear, with the SAME flow counts ("edge set" /
    "comparable flow counts") — NOT keyed on the edge's own "proto" label, which is
    each sensor's OWN open L7 vocabulary (`FlowEvent.service`), never normalized
    across plugins; see `aggregate_edges`'s own docstring for why that field is
    compared as a VOCABULARY (expected to differ) rather than a key (expected to
    match),
  * the SAME host-scoped identity bindings appear where both sensors can see them
    (dhcp, kerberos) ("identity bindings") — compared on `(ip, bound_to, scope,
    namespace, identifier)`, NOT on the raw string alone, AND asserted to be host-scoped
    rather than merely equal; see `aggregate_identity` for the measured reason both
    halves are load-bearing (agreement alone passed while both sensors were broken),
  * the SAME durable service facts are projected where both sensors parse the protocol
    (ssl/http/ssh/quic/snmp/ldap) ("service census"),
  * every flow ends up in the SAME `FLOW_STATE_CLASSES` bucket and every host in the
    SAME `listen_ports` set — the two axes that decide whether a host is believed to
    RUN something, and the two that were compared nowhere at all until 2026-08-25.

## Equal to each other is HALF a gate — every axis is also anchored to the plan

**Two implementations over one shared lower layer agree perfectly whenever that layer
regresses.** The two plugins differ only above `events/model.py`; `windowing/` is one
body of code serving both. So a regression in `FLOW_ACCEPTED`, in `_add_flow`, in
`EntityResolver`, or in a projection table changes BOTH sensors identically, leaves
every cross-sensor comparison equal, and this gate prints a full page of ticks. That is
not a hypothetical failure mode — it is the one this script has actually shipped twice:
the Kerberos `@`-vs-`/` bug (both renderers wrong the same way, "identical bindings
(20)") and the `state_mix`/`listen_ports` axes that were not compared at all while a
20-host phantom-listener claim sat in this docstring as accepted prose.

So since 2026-08-25 every axis carries **at least one assertion anchored to the seeded
plan** (`verify_determinism._synthetic_cast`), which is the only thing in the comparison
that does not move when the code does:

  | axis | plan-derived expectation |
  |---|---|
  | `state_mix` | the plan's own outcome census, through `PLAN_STATE_CLASS` |
  | `listen_ports` | `(dst, dport, proto)` of every ACCEPTED flow, plus "no host claims a listener on a port the plan only ever swept" |
  | window placement | the boundary-straddler count must be non-zero |
  | node/edge bytes | the exact refused-flow byte delta, per direction |
  | `__longtail__` | which sources spill, and by how many peers, past `TOP_PEERS` |
  | identity bindings | `(ip, raw, scope, namespace)` for every DHCP lease and Kerberos machine account on the wire |
  | `arp` | every announcement parsed, every declared non-binding record refused |
  | `x509` | PRODUCED by the plugin, then dropped by windowing — not merely absent |
  | unparsed/unrecognized | the `_SKIP` half and the unrecognized half, separately |

Three of those (`listen_ports`, `state_mix`, `__longtail__`) are deliberately derived
from the plan's own wire outcomes and NOT through the product tables they check —
deriving `listen_ports` through `events.FLOW_ACCEPTED` would make the expectation grow
exactly when the bug does, which is the same defect one level up.

Comparison is RUN-LEVEL (summed/unioned across every window), not per-window. `WindowResult`
node/edge keys are RESOLVED ENTITY IDS, and `windowing/entities.py` D3 states outright that
entity ids are minted sequentially and **explicitly not meant to compare across captures** —
even two runs of the identical plugin over the identical source are not promised to number
entities alike if internal event order shifts. Comparing `ent-0001 == ent-0001` across two
DIFFERENT SENSORS would therefore test a numbering coincidence, not equivalence. This gate
instead re-keys every node/edge by the one thing that IS comparable across sensors: the
literal address (`_entity_to_address`, using each run's own `entity_roster()`; D4's
un-anchored fallback means an address with no host-scoped evidence already equals its own
entity id).

Per-window structure is compared in exactly ONE respect and no more: **which window each
flow landed in** (section 2). Everything else per-window — sub-bucket spread, quantiles,
burstiness, per-window health — is still not compared, on the original reasoning that
nothing in "ingests and windows equivalently" requires two sensors to describe the same
seconds identically. Window PLACEMENT is different in kind and was wrong to lump in with
them: a window index is `floor(ts / window_seconds)`, a pure function of a flow's own start
time and a shared constant, so two plugins handed the identical plan must agree, and if
they do not, one of them is keying on the wrong timestamp. That is not hypothetical —
`ingest/suricata/plugin.py::_flow`'s `M4` fix exists because the first landing keyed
`FlowEvent.ts` on the record's emit `timestamp` (when the flow CLOSED) rather than on
`flow.start`, which misplaces every flow that spans a boundary. A run-level comparison
cannot see that at all: a flow counted in the wrong window is still counted once in the
run total. The fixture's own half of this is in `verify_determinism
.build_synthetic_suricata_capture` (stamping records at close, and `_synthetic_cast`'s
three deliberately boundary-straddling flows); section 2 asserts the straddler count is
non-zero before trusting its own result, because at zero the check passes for either
keying choice and proves nothing.

## What legitimately CANNOT be made equivalent (named here, asserted explicitly below,
## never smoothed over by loosening a check until it passes)

  1. **`sensor_pkts_dropped_max` — Zeek-only, and by RULING rather than by capability.**
     The eve.json fixture carries `stats.capture.kernel_drops` (40, then 12 — the same
     two readings `stats.log` carries) and `stats` is in `_PARSERS`, so this is not a
     missing shape: `ingest/suricata/plugin.py::_stats` reads the rows and deliberately
     mints nothing. Suricata's counters are cumulative-since-start unless the deployment
     set `deltas: yes`, and one JSON record cannot prove which mode produced it, so the
     kind `pkts_dropped` ("packets the capture layer discarded") is indistinguishable
     from "drops since the engine started" — one unverified premise cannot both justify
     that module's decline of `stats.decoder.pkts`/`.bytes` and be waved past here. So a
     Suricata run reports `None`: "not observed", never "no drops" (`LES-006`). Asserted
     as Zeek-set / Suricata-`None` below, in the same shape as `capture_loss_pct_max`,
     with a named reopen condition — one measurement (`stats.uptime` across two
     consecutive records, or the deployment's `deltas:` setting) settles it, at which
     point this becomes a plain equality assertion on 40.0.

     **What used to occupy this slot, and why it is gone.** Until 2026-08-25 exception #1
     was the scanner's REJ sweep: `_flow_state_class` was said to be structurally unable
     to tell a refusal from an established-then-reset connection, so Suricata classified
     all 20 as `"reset_resp"` (inside `events.FLOW_ACCEPTED`) where Zeek says `"refused"`
     (outside it), minting `listens_on 23/tcp` on 20 hosts that never listened. Code
     review `M2` disproved the premise — an established-then-reset flow necessarily
     carries the responder's own SYN in the accumulated `tcp_flags_tc` byte and a refused
     one cannot, so the two ARE distinguishable from a flow record — and the classifier
     now returns `"refused"`. Measured after that fix: `state_mix` run totals are
     identical on both sensors (`established 1372 / attempted 40 / refused 20`) and
     `listen_ports` is identical on all 80 addresses. The lesson kept from it is not the
     exception but its invisibility: NEITHER field was compared anywhere in this script,
     so a 20-host phantom-listener claim lived here as accepted English prose. Both are
     asserted EQUAL now, with no exception (section 3).
  2. **NTLM identity evidence.** No `ntlm` event_type exists in Suricata's eve.json
     vocabulary; `ingest/suricata/plugin.py` has no parser for it. The Zeek fixture's
     `ntlm.log` anchors the DC and file server as host-scoped entities; the Suricata run
     has no such evidence for those two hosts at all, so they resolve un-anchored (D4:
     the address stands for itself) rather than to an `ent-NNNN`. Asserted below as an
     entity-KIND divergence for exactly those two addresses, not silently absorbed by
     this gate's address-keyed re-keying (which hides it for every OTHER comparison but
     must not hide it here). Asserted TWICE, in two independent shapes: as the roster
     membership difference in section 5, and — since 2026-08-25 — as the mid-window
     anchoring split in section 2 (resolution is forward-only, D2, so an address anchored
     part-way through a window holds two node keys in that window: 11 addresses do on
     Zeek, 9 on Suricata, and the 2 extra are exactly these).
  3. **`capture_profile` (the `ProfileEvent`/`Q29` channel).** `ingest/suricata/plugin.py`
     does not import `ProfileEvent` — there is no code path to this channel at all, in
     any window, regardless of what a future eve.json fixture might contain. Zeek's
     `ocsp_cache_lifetime` measurement has no Suricata-side analogue and none is
     manufactured.
  4. **`tunnel`, `x509`, sensor-side `capture_loss`.** Not real Suricata eve.json
     event types (verified against Suricata's documented schema, not merely "not yet
     wired") — no shape exists to render them into, so `build_synthetic_suricata_capture`
     does not attempt to. `x509` costs the comparison nothing (`_SERVICE_NOT_PROJECTED
     ["x509"]`: inert on the Zeek side too, no connection tuple). `tunnel` and sensor-side
     `capture_loss` DO cost something and are named as real, disclosed coverage gaps.
  5. **ARP identity evidence — exception #2 running the other way, and the one with a
     TRIP-WIRE.** Suricata logs `arp` (verified in its own `etc/schema.json`) and Zeek
     ships no `arp.log`, so the eve.json fixture renders ARP and the Zeek one cannot.
     `ingest/suricata/plugin.py::_arp` parses it into `IdentityEvent(kind="arp")` — but
     `windowing.entities.IDENTITY_SCOPES` has **no `arp` row**, so that evidence is
     counted (`unscoped_identity_kinds`) and anchors nothing, on either sensor. The
     divergence asserted below is therefore "Suricata collects host-scoped-shaped
     evidence that the resolver currently refuses to interpret", which is the honest
     current state and not a desirable one. It is asserted with a positive check that
     `"arp" not in IDENTITY_SCOPES`, so the day that row lands this gate FAILS and names
     what has to be re-decided (ARP would then anchor hosts on one sensor and not the
     other) rather than silently continuing to describe a world that ended.
  6. **Raw byte counts on the 20 "refused" edges.** `build_synthetic_suricata_capture`
     renders `refused` with a physically honest single RST/ACK reply (`pkts_toclient=1`,
     `bytes_toclient=40`) rather than reusing the Zeek fixture's own `resp_bytes=0` for
     those rows (correct for Zeek, whose classifier never looks at byte counts at all).
     Those 20 edges are therefore expected to carry exactly 40 more bytes on the Suricata
     side — checked as an exact, named delta below, not ignored.

Anything not named above is asserted EQUAL. If a future edit makes one of those equal
assertions fail, that is a regression in a plugin, not a reason to add a new named
exception here — see CLAUDE.md's tester charter: "do not loosen the comparison until it
passes."

Usage:
  python scripts/verify_suricata_equivalence.py
  python scripts/verify_suricata_equivalence.py --keep   # leave the temp dir for inspection

Exit 0 = every comparable axis agrees, agrees WITH THE PLAN where the plan can speak to
it, and every named exception is exactly the disclosed shape above (no MORE divergence
than named). Exit 1 otherwise, with every finding printed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from verify_determinism import (
    _synthetic_cast,
    build_synthetic_capture,
    build_synthetic_suricata_capture,
)

WINDOW_SECONDS = 3600
SUBBUCKETS = 12

#: `_SyntheticFlow.outcome` -> the `events.FLOW_STATE_CLASSES` bucket that outcome MUST
#: land in, on EITHER sensor. This is the gate's own reading of the shared plan, declared
#: here rather than read out of either pipeline's output — the whole point of a ground
#: truth is that it does not move when the thing it measures does.
#:
#: `outcome` names what happened ON THE WIRE (`_SyntheticFlow`'s own docstring), so the
#: mapping is a fact about the traffic, not about a sensor: a flow the responder accepted
#: is `established`, a lone unanswered SYN is `attempted`, a refusal is `refused`.
PLAN_STATE_CLASS = {"accepted": "established", "no_reply": "attempted",
                    "refused": "refused"}

#: The plan's outcome that means "the responder ACCEPTED, so this port has a listener".
#:
#: **Deliberately NOT expressed as `PLAN_STATE_CLASS[outcome] in events.FLOW_ACCEPTED`,
#: and that is the entire value of this constant.** Deriving the expectation through
#: `FLOW_ACCEPTED` would make it move with the very table the check exists to police:
#: widen `FLOW_ACCEPTED` to include `refused` and BOTH sensors mint 20 phantom
#: `23/tcp` listeners AND the expectation would grow to predict them, and the gate would
#: pass on a fabricated service census. Mutation-proven in exactly that shape (2026-08-25):
#: with `FLOW_ACCEPTED` patched to include `refused`, the two sensors still agree with each
#: other perfectly and this gate now fails, naming the 20 hosts.
PLAN_ACCEPTED_OUTCOME = "accepted"

#: Stems/event_types the two fixtures deliberately carry that NO parser table claims,
#: split by WHICH refusal each one is — the split is the checkable claim, not the total.
#: `unrecognized ⊆ unparsed` always, so "both non-empty" proves only the unrecognized
#: half; deleting a `_SKIP` row would leave that check green while the no-fall-through
#: discipline it is supposed to police had quietly stopped being exercised.
#:
#: Declared from the FIXTURE (`verify_determinism.build_synthetic_capture` writes
#: `reporter.log` and `__not_a_zeek_stem__.log`; the eve.json renderer writes a `drop`
#: and a `__not_a_suricata_event_type__` record) rather than imported from each plugin's
#: own `_SKIP` — importing the table under test would make the assertion agree with the
#: code by construction and check nothing.
EXPECTED_UNPARSED = {
    "zeek":     {"skipped_by_name": {"reporter"}, "unrecognized": {"__not_a_zeek_stem__"}},
    "suricata": {"skipped_by_name": {"drop"},
                 "unrecognized": {"__not_a_suricata_event_type__"}},
}

_FAILURES: list[str] = []


def fail(msg: str) -> None:
    _FAILURES.append(msg)
    print(f"  ✗ {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# -- run one plugin over its own fixture, collect the raw artifacts -----------------

class Run(NamedTuple):
    """One sensor's whole run: what the windowing layer produced, AND what the plugin
    handed it before windowing had a chance to drop anything.

    `census` is the second half and exists for a specific class of blindness this gate
    had until 2026-08-25: "both sensors report nothing here" is satisfied equally by
    *produced it and correctly dropped it* and by *never produced it at all*, and only
    the first is what several of the exceptions below actually claim (`x509` is the
    named case). A window artifact cannot tell those apart by construction — the event
    is gone either way — so the distinction has to be observed one layer up, at the
    plugin's own output.
    """
    windows: list
    roster: dict
    census: Counter       # ("service"|"identity", kind) -> events the PLUGIN emitted
    plugin: object        # for `parse_notes`: the declared, counted row-level decisions


def run_pipeline(plugin_name: str, source: str) -> Run:
    """-> `Run` — the real plugin, the real WindowManager, no shortcut (mirrors
    `verify_determinism.canonical_windows`'s own driver shape).
    """
    from aximprint.events import IdentityEvent, ServiceEvent
    from aximprint.ingest import get_plugin
    from aximprint.windowing import WindowManager

    plugin = get_plugin(plugin_name)
    plugin.progress_rows = 0  # keep this gate's own output readable
    wm = WindowManager(window_seconds=WINDOW_SECONDS, subbuckets=SUBBUCKETS,
                       unparsed_log_types_provider=lambda: plugin.unparsed_stems,
                       unrecognized_log_types_provider=lambda: plugin.unrecognized_stems)
    census: Counter = Counter()

    def tee(events):
        """Count what the plugin emits, pass it through untouched. A tee, never a
        buffer: the whole point of the streaming design is that this gate can hold a
        run's worth of windows but not a run's worth of events.
        """
        for ev in events:
            if isinstance(ev, ServiceEvent):
                census[("service", ev.kind)] += 1
            elif isinstance(ev, IdentityEvent):
                census[("identity", ev.kind)] += 1
            yield ev

    windows = list(wm.process(tee(plugin.events(source))))
    windows += list(wm.finalize())
    return Run(windows, wm.entity_roster(), census, plugin)


def entity_to_address(roster: dict) -> dict[str, str]:
    """`ent-NNNN` -> its one address (see module docstring on why address, not entity id,
    is this gate's join key). Fails loud if this fixture ever stops being 1:1 — a merge
    would need this gate extended, not silently miscounted.
    """
    out: dict[str, str] = {}
    for rec in roster["bound"]:
        addrs = rec["addresses"]
        if len(addrs) != 1:
            raise AssertionError(
                f"entity {rec['entity']} is bound to {len(addrs)} addresses {addrs!r} — "
                "this gate's address-keyed comparison assumes a 1:1 entity:address "
                "mapping in this synthetic fixture (no host ever changes address "
                "mid-run); that assumption just broke, so the gate needs redesigning, "
                "not a wider try/except")
        out[rec["entity"]] = addrs[0]
    return out


def addr_of(entity_or_addr: str, ent2addr: dict[str, str]) -> str:
    return ent2addr.get(entity_or_addr, entity_or_addr)  # D4: unanchored -> itself


# -- aggregation: entity-keyed, per-window -> address-keyed, run-level -------------

NODE_SUM_FIELDS = ("flows_out", "flows_in", "bytes_out", "bytes_in")


def aggregate_nodes(windows, ent2addr) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for w in windows:
        for ent, feats in w.nodes.items():
            addr = addr_of(ent, ent2addr)
            agg = out.setdefault(addr, {
                "flows_out": 0, "flows_in": 0, "bytes_out": 0, "bytes_in": 0,
                "listen_ports": set(), "tainted": False,
                "services": defaultdict(set), "segments": set(),
                "state_mix": defaultdict(int),
            })
            for k in NODE_SUM_FIELDS:
                agg[k] += feats[k]
            # `state_mix` is credited on the SOURCE node only (`windowing/windows.py
            # ::_add_flow`), so summing it per address across windows recovers exactly
            # "how did this host's own outbound flows end up" — a class histogram over
            # `events.FLOW_STATE_CLASSES`, which IS a normalized cross-sensor vocabulary
            # (unlike `FlowEvent.state`/`FlowEvent.service`, both sensor-native), and is
            # therefore comparable.
            #
            # `s0_count`/`rej_count` used to sit beside it and were **not** comparable:
            # they gated on the verbatim Zeek strings `"S0"`/`"REJ"` (`ev.state`, the
            # sensor's own untranslated word), which no Suricata or axquery record can
            # carry, so they were structurally `0` on every non-Zeek sensor — a confident
            # "no such flows" published beside thousands of `attempted` ones (`LES-006`).
            # `Q34` DELETED them 2026-08-26 rather than re-keying them on `state_class`,
            # because a `state_class`-keyed scalar would duplicate two keys this histogram
            # already carries in the same row, under a vocabulary `features/vocabulary.py`
            # declares CLOSED (`attempted`/`refused` are guaranteed columns whether or not
            # a window observes either), with nothing checking the two agree. So
            # `state_mix` is now the ONLY hygiene field a node row publishes, and comparing
            # it is the whole check — which is why the `all_or_nothing` scan below exists:
            # this gate did not catch `Q34`, and the reason it did not is that
            # `NODE_SUM_FIELDS` excluded those two scalars as "known incomparable" and
            # nothing ever revisited the exclusion.
            for cls, n in feats["state_mix"].items():
                agg["state_mix"][cls] += n
            agg["listen_ports"].update(feats["listen_ports"])
            agg["tainted"] = agg["tainted"] or feats["tainted"]
            agg["segments"].update(feats["segments"])
            for kind, facts in feats["services"].items():
                agg["services"][kind].update(facts)
    return out


def node_field_variation(windows) -> dict[str, set[str]]:
    """published node-bundle field -> the distinct values it took over EVERY node row of
    the run (canonical JSON, capped at 2 — "varies" is the only distinction needed).

    Read from `w.nodes` directly and keyed on the row's OWN keys, deliberately NOT from
    `aggregate_nodes` (which projects a hand-written subset) and NOT filtered by any list
    maintained in this script. A field added, renamed or resurrected in
    `windowing/windows.py::_close` is in scope here by default; that default is the whole
    point (see the `Q34` note at the call site).
    """
    out: dict[str, set[str]] = defaultdict(set)
    for w in windows:
        for feats in w.nodes.values():
            for k, v in feats.items():
                if len(out[k]) < 2:
                    out[k].add(json.dumps(v, sort_keys=True, default=str))
    return out


def window_placement(windows, ent2addr) -> dict[float, tuple[int, int]]:
    """window_start -> (flows placed in it, distinct node ADDRESSES in it).

    **The one PER-WINDOW comparison this gate makes, and it exists because the
    run-level comparison everywhere else is structurally blind to the exact bug
    `ingest/suricata/plugin.py::_flow`'s `M4` fix repairs.** Every other axis here sums
    or unions across windows, so a flow placed in the WRONG window still lands in the
    same run total — a plugin that keyed `FlowEvent.ts` on the record's emit
    `timestamp` (when the flow CLOSED) instead of `flow.start` would move rows between
    windows and change nothing this gate could see.

    Comparing window placement across two different sensors is legitimate here, and it
    is worth being precise about why, because the module docstring's general refusal to
    compare per-window structure still stands for everything else: a window assignment
    is `floor(ts / window_seconds)`, a pure function of the flow's own start time and a
    constant both runs share. Both plugins are handed the identical plan with identical
    start times (`_synthetic_cast`), so they must agree — not as a coincidence of
    bucketing, but because they are bucketing the same numbers. Sub-bucket spread,
    quantiles and window health are still NOT compared; only "which window did this flow
    land in", which is the claim `M4` is about.

    Flows are counted as `sum(flows_out)` — each flow is credited to exactly one source
    node in exactly one window, so this is an exact per-window flow census with no
    double-count (`flows_in` would give the identical total, from the other side).

    The node count is of distinct ADDRESSES, re-keyed through `ent2addr` exactly as every
    other axis here is, and NOT of raw `w.nodes` keys — measured, after a first version
    that used the raw count: window 0 holds 91 node keys on Zeek against 89 on Suricata
    for 80 addresses on both. That is not a placement difference. Resolution is
    forward-only (D2), so an address that acquires host-scoped evidence PART-WAY THROUGH a
    window appears under two keys in that window (its own address before the anchor, its
    `ent-NNNN` after) — 11 addresses do on Zeek, 9 on Suricata, and the 2 extra are
    precisely the NTLM-anchored DC and file server of named exception #2. Counting raw
    keys here would restate that one exception as a second, differently-worded failure of
    an unrelated axis; it is checked, in its own right, by `window_entity_splits` below.
    """
    out: dict[float, tuple[int, int]] = {}
    for w in windows:
        flows = sum(n["flows_out"] for n in w.nodes.values())
        addrs = {addr_of(ent, ent2addr) for ent in w.nodes}
        out[w.window_start] = (flows, len(addrs))
    return out


def window_entity_splits(windows, ent2addr) -> dict[str, int]:
    """address -> the largest number of DISTINCT entity keys it held within one window.

    An address holds two keys in a window when host-scoped evidence for it arrives
    part-way through: resolution is forward-only (D2), so flows before the anchor stay
    keyed on the address and flows after are keyed on the minted `ent-NNNN`, and both
    rows are in the same `WindowResult.nodes`. Every other comparison in this script
    re-keys that away on purpose; this one keeps it, so the divergence has somewhere to
    be asserted rather than being either hidden or misread as a placement bug.
    """
    out: dict[str, int] = {}
    for w in windows:
        per_addr: dict[str, set] = defaultdict(set)
        for ent in w.nodes:
            per_addr[addr_of(ent, ent2addr)].add(ent)
        for addr, ents in per_addr.items():
            out[addr] = max(out.get(addr, 0), len(ents))
    return {a: n for a, n in out.items() if n > 1}


LONGTAIL = "__longtail__"


def aggregate_edges(windows, ent2addr):
    """-> (explicit: {(src_addr,dst_addr): {"flows","bytes"}},
           longtail: {src_addr: {"flows","bytes","tail_peers"}},
           labels: {distinct edge "proto" strings actually used})

    Summed across windows. Valid because a window's own top-K eviction (`TOP_PEERS`)
    is applied ONCE at that window's close over that window's own accumulated totals —
    summing per-window rows for the same key, plus per-window longtail rows for the
    same src, recovers the true run total exactly, with no double-counting (each flow
    lands in exactly one window's accumulator).

    Keyed on `(src, dst)` ONLY, deliberately dropping `WindowResult.edges[]`'s own
    `"proto"` field from the comparison key — found empirically, not assumed: that
    field is `windowing/windows.py::_add_flow`'s `ev.service or ev.proto or "?"`, an
    OPEN, SENSOR-NATIVE L7 label (`FlowEvent.service`'s own docstring: "L7 label if
    the sensor knew it"), never a normalized cross-sensor vocabulary the way
    `ServiceEvent.kind`/`IdentityEvent.kind` are (`SERVICE_PROJECTIONS`/
    `IDENTITY_PROJECTIONS`, which `ingest/suricata/plugin.py` deliberately reuses
    verbatim — its own module docstring's explicit design goal). Zeek names the SAME
    real Kerberos conversation `"krb"`; Suricata's `app_proto` names it `"krb5"`. For
    the scanner's unidentified sweep, Zeek's `service` is unset so this field falls
    back to the bare transport (`"tcp"`), while Suricata's `app_proto="failed"` (a
    real, meaningful value: "detection attempted, no protocol identified") is truthy
    and wins instead. Neither sensor is wrong; nothing anywhere promises these labels
    line up, so keying the comparison on them would fail on a difference that carries
    no information about whether the two sensors saw the same conversations. `labels`
    is returned so the gate can assert the two VOCABULARIES legitimately differ,
    instead of silently dropping the fact.
    """
    explicit: dict[tuple, dict] = {}
    longtail: dict[str, dict] = {}
    labels: set[str] = set()
    for w in windows:
        for e in w.edges:
            src = addr_of(e["src"], ent2addr)
            if e["dst"] == LONGTAIL:
                t = longtail.setdefault(src, {"flows": 0, "bytes": 0, "tail_peers": 0})
                t["flows"] += e["flows"]
                t["bytes"] += e["bytes"]
                t["tail_peers"] += e["tail_peers"]
                continue
            dst = addr_of(e["dst"], ent2addr)
            labels.add(e["proto"])
            key = (src, dst)
            acc = explicit.setdefault(key, {"flows": 0, "bytes": 0})
            acc["flows"] += e["flows"]
            acc["bytes"] += e["bytes"]
    return explicit, longtail, labels


def aggregate_identity(windows) -> dict[str, set]:
    """kind -> {(ip, bound_to, scope, namespace, identifier)} for every OPENED binding.

    `change == "opened"` only (`Q9`): a `closed`/`final` row restates a binding this
    run already counted once, and counting it again would inflate a set that is
    supposed to answer "how many distinct bindings did this run ever see".

    **The key carries `scope`, `namespace` and the PARSED `identifier`, not just the raw
    string — and that widening is the whole lesson of 2026-08-25** (`windowing/entities
    .py::EntityResolver._record` publishes all four). `bound_to` is `_Binding.raw`: what
    the sensor said, verbatim, before `windowing/entities.py` parsed it. `scope`
    (`host`/`user`/`referenced`) is what the resolver CONCLUDED, and only `host` may ever
    merge two addresses (D1) — so two sensors can agree perfectly on every raw string
    while disagreeing about whether any of it anchors a host, which is the difference
    between resolving a network and merely transcribing it.

    Measured, on this gate's own fixture, before this key was widened: `build_synthetic_
    capture`'s `kerberos.log` wrote `ws000$@CORP.AXR.LAB` where Zeek writes
    `ws000$/CORP.AXR.LAB`, so `kerberos_identifier` (which splits on `/`, per its own
    docstring) read one component, found a primary not ending in `$`, and classified all
    20 AD machine accounts as USER principals anchoring nothing. The Suricata renderer
    composed its principal with the same wrong separator, so BOTH sides were broken
    IDENTICALLY, both produced the same raw string, and this gate reported "kerberos:
    identical bindings (20)" — a pass earned by agreement-in-brokenness, on precisely the
    axis the gate exists to check.

    Be precise about what this widening does and does not buy, because getting that wrong
    is how the hole reopens: carrying `scope`/`namespace` makes the broken state VISIBLE
    (`user` where `host` belongs, printed in the finding and in the pass line) but does
    NOT by itself make it FAIL — two identically-broken sides carry identical scopes and
    still compare equal. What closes it is the POSITIVE assertion at the call site: every
    binding this fixture produces must be `host`-scoped, because every identifier in it
    names a machine. Equality across sensors and correctness of meaning are two different
    claims, and only checking both catches a sensor pair that is wrong the same way.

    `entity` is deliberately NOT in the key: entity ids are run-scoped and D3 says
    outright they are not comparable across captures (module docstring). Whether an
    address is anchored AT ALL is checked separately, by set membership in each run's own
    roster, in section 5's `entity-kind divergence` assertion.
    """
    out: dict[str, set] = defaultdict(set)
    for w in windows:
        for b in w.identity_delta:
            if b["change"] == "opened":
                out[b["kind"]].add((b["ip"], b["bound_to"], b["scope"],
                                    b["namespace"], b["identifier"]))
    return out


def aggregate_health(windows) -> dict:
    drops = [w.health.get("sensor_pkts_dropped_max") for w in windows]
    drops = [d for d in drops if d is not None]
    loss = [w.health.get("capture_loss_pct_max") for w in windows]
    loss = [x for x in loss if x is not None]
    unparsed_final = windows[-1].health.get("unparsed_log_types") if windows else None
    unrecognized_final = windows[-1].health.get("unrecognized_log_types") if windows else None
    unparsed_all_none_but_last = all(
        w.health.get("unparsed_log_types") is None for w in windows[:-1])
    return {
        "sensor_pkts_dropped_max": max(drops) if drops else None,
        "capture_loss_pct_max": max(loss) if loss else None,
        "unparsed_log_types_final": unparsed_final,
        "unrecognized_log_types_final": unrecognized_final,
        "unparsed_none_until_last": unparsed_all_none_but_last,
    }


def unscoped_identity_total(windows) -> int:
    """Run total of `unscoped_identity_kinds` — one per `IdentityEvent` whose `kind` is
    absent from `windowing.entities.IDENTITY_SCOPES`, i.e. evidence the resolver recorded
    the EXISTENCE of and refused to interpret (`entities.observe`'s first branch).

    Summed rather than maxed: it is a per-window count of events, and the question here is
    "how much identity evidence did this run collect that nothing can yet use".
    """
    return sum(w.health.get("unscoped_identity_kinds") or 0 for w in windows)


def capture_profile_union(windows) -> dict[str, int]:
    """parameter -> total `n` observed across the run (0 if the parameter never fires)."""
    out: dict[str, int] = defaultdict(int)
    for w in windows:
        for param, stats in w.capture_profile.items():
            out[param] += stats.get("n", 0)
    return out


# -- the gate itself -----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="keep the temp workdir")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="aximprint-suricata-equiv-"))
    try:
        zeek_dir = build_synthetic_capture(workdir / "zeek")
        suricata_dir = build_synthetic_suricata_capture(workdir / "suricata")

        print(f"── zeek fixture:     {zeek_dir}")
        print(f"── suricata fixture: {suricata_dir}")

        zrun = run_pipeline("zeek", str(zeek_dir))
        srun = run_pipeline("suricata", str(suricata_dir))
        zwins, swins = zrun.windows, srun.windows
        print(f"── zeek: {len(zwins)} window(s)   suricata: {len(swins)} window(s)")

        z2a, s2a = entity_to_address(zrun.roster), entity_to_address(srun.roster)

        # -- 1. node set ---------------------------------------------------------
        print("\n[1/7] node set (traffic-endpoint addresses)")
        znodes = aggregate_nodes(zwins, z2a)
        snodes = aggregate_nodes(swins, s2a)
        zaddrs, saddrs = set(znodes), set(snodes)
        if zaddrs == saddrs:
            ok(f"identical address set ({len(zaddrs)} addresses)")
        else:
            fail(f"node sets differ: zeek-only={sorted(zaddrs - saddrs)[:10]} "
                f"suricata-only={sorted(saddrs - zaddrs)[:10]}")

        # The shared traffic plan, re-derived here as GROUND TRUTH for the sections
        # below — read from `_synthetic_cast` itself, never guessed from the pipeline
        # output being checked. Section 2 uses it for the boundary-straddler count;
        # sections 2 and 3 use the refused-flow census that follows.
        #
        # Ground truth for the ONE named byte exception (module docstring #5): exactly
        # which addresses were the SOURCE or DESTINATION of a "refused" flow, and how
        # many times — read from the shared plan itself, not guessed from the pipeline
        # output being checked. `_add_flow` (`windowing/windows.py`) credits a flow's
        # RESPONSE bytes (`ev.bytes_in`) onto the SOURCE's `bytes_in` and the
        # DESTINATION's `bytes_out` — so the extra 40 Suricata-only bytes per refused
        # flow (`build_synthetic_suricata_capture`'s `pkts_toclient`/`bytes_toclient`
        # override) lands on the SOURCE's `bytes_in` and the DESTINATION's `bytes_out`,
        # never the reverse.
        REFUSED_EXTRA_BYTES = 40
        cast = _synthetic_cast()
        refused_as_src: dict[str, int] = defaultdict(int)
        refused_as_dst: dict[str, int] = defaultdict(int)
        refused_pair_count: dict[tuple, int] = defaultdict(int)
        for f in cast.flows:
            if f.outcome == "refused":
                refused_as_src[f.src] += 1
                refused_as_dst[f.dst] += 1
                refused_pair_count[(f.src, f.dst)] += 1

        # -- 2. window placement --------------------------------------------------
        print("\n[2/7] window placement (which window each flow landed in — the `M4` axis)")
        zplace, splace = window_placement(zwins, z2a), window_placement(swins, s2a)
        # Ground truth, read from the shared plan rather than from either pipeline: how
        # many flows START in one window and CLOSE in the next. If this is ever 0 the
        # comparison below is vacuous for `M4` — it would pass for a plugin keyed on
        # either field — so the count is asserted, not merely printed.
        straddlers = sum(1 for f in cast.flows
                         if int(f.ts // WINDOW_SECONDS)
                         != int((f.ts + f.duration) // WINDOW_SECONDS))
        if not straddlers:
            fail("no flow in `_synthetic_cast` starts in one window and closes in the "
                "next — window-placement equivalence is then satisfied by ANY keying "
                "choice, `flow.start` or the record's emit `timestamp` alike, so this "
                "section proves nothing about `M4`; restore a boundary-straddling flow")
        elif zplace == splace:
            ok(f"identical window placement across {len(zplace)} window(s) ("
              + ", ".join(f"{int(k)}:{v[0]}f/{v[1]}n" for k, v in sorted(zplace.items()))
              + f"), including the {straddlers} flow(s) whose close time falls in a "
              "LATER window than their start — the only shape that can tell `flow.start` "
              "from the record's own emit `timestamp`")
        else:
            fail(f"window placement differs: zeek={dict(sorted(zplace.items()))} "
                f"suricata={dict(sorted(splace.items()))}")

        # The mid-window anchoring split (D2, forward-only): an address that acquires
        # host-scoped evidence part-way through a window appears under TWO keys in that
        # window. This is the second, independent place named exception #2 must show up —
        # the two NTLM-anchored hosts split on Zeek and cannot on Suricata — and it is
        # asserted rather than left to be re-discovered as a confusing node-count delta.
        zsplit, ssplit = (window_entity_splits(zwins, z2a),
                          window_entity_splits(swins, s2a))
        zeek_only_bound = set(z2a.values()) - set(s2a.values())
        if (set(ssplit) <= set(zsplit) and set(zsplit) - set(ssplit) == zeek_only_bound
                and all(zsplit[a] == ssplit[a] for a in ssplit)):
            ok(f"mid-window anchoring split: {len(ssplit)} address(es) split on BOTH "
              f"sensors identically, plus {sorted(zeek_only_bound)} on zeek only — "
              "exactly the ntlm-anchored hosts of named exception #2, in the one place "
              "this gate's address re-keying would otherwise show up as a bare node-count "
              "difference")
        else:
            fail(f"mid-window anchoring split diverged beyond the ntlm hosts: "
                f"zeek={dict(sorted(zsplit.items()))} suricata={dict(sorted(ssplit.items()))} "
                f"zeek-only-bound={sorted(zeek_only_bound)}")

        # -- 3. per-node flow/byte counts, with the ONE named exception ----------
        print("\n[3/7] per-node flow/byte totals (named exception: the 20 REJ-swept hosts'"
             " extra 40 bytes each, credited by direction — see module docstring #5)")
        mismatches = []
        for addr in sorted(zaddrs & saddrs):
            z, s = znodes[addr], snodes[addr]
            if z["flows_out"] != s["flows_out"] or z["flows_in"] != s["flows_in"]:
                mismatches.append(f"{addr}.flows_{{out,in}}: zeek=({z['flows_out']},"
                                  f"{z['flows_in']}) suricata=({s['flows_out']},"
                                  f"{s['flows_in']})")
            expected_out_delta = REFUSED_EXTRA_BYTES * refused_as_dst.get(addr, 0)
            expected_in_delta = REFUSED_EXTRA_BYTES * refused_as_src.get(addr, 0)
            got_out_delta = s["bytes_out"] - z["bytes_out"]
            got_in_delta = s["bytes_in"] - z["bytes_in"]
            if got_out_delta != expected_out_delta:
                mismatches.append(f"{addr}.bytes_out: zeek={z['bytes_out']} "
                                  f"suricata={s['bytes_out']} (delta={got_out_delta}, "
                                  f"expected {expected_out_delta})")
            if got_in_delta != expected_in_delta:
                mismatches.append(f"{addr}.bytes_in: zeek={z['bytes_in']} "
                                  f"suricata={s['bytes_in']} (delta={got_in_delta}, "
                                  f"expected {expected_in_delta})")
        if mismatches:
            for m in mismatches[:20]:
                fail(m)
        else:
            ok("flows_out/flows_in identical on every address; bytes_out/bytes_in "
              "differ ONLY by the exact, precomputed multiple of "
              f"{REFUSED_EXTRA_BYTES} bytes the refused-flow ground truth predicts "
              f"({sum(refused_as_dst.values())} dst-side and "
              f"{sum(refused_as_src.values())} src-side occurrence(s))")

        # Per-node `state_mix` and `listen_ports`. Added 2026-08-25, and the reason is
        # worth stating because their ABSENCE is what let a real divergence sit in this
        # gate as an accepted exception for a whole round: before `ingest/suricata/
        # plugin.py::_flow_state_class`'s `M2` fix, Suricata classified the scanner's 20
        # REJ-shaped refusals as `"reset_resp"` (inside `events.FLOW_ACCEPTED`) instead of
        # `"refused"` (outside it), which minted a phantom `listens_on 23/tcp` on 20 hosts
        # that never listened. Neither field was compared ANYWHERE in this script, so the
        # only trace of a 20-host false-listener claim was English prose in a module
        # docstring calling it a sensor limitation. `state_mix` is the normalized
        # `FLOW_STATE_CLASSES` histogram — a cross-sensor vocabulary by design, unlike
        # `FlowEvent.state` — and `listen_ports` is what the whole role/service layer
        # downstream is built on, so both are asserted EQUAL, with no exception.
        # **GROUND TRUTH for both axes, derived from the shared PLAN — this is the half
        # that was missing until 2026-08-25 and it is a different claim from the equality
        # beside it.** Both axes were asserted equal BETWEEN THE SENSORS and against
        # nothing else, and the two plugins share their whole lower layer (`events/model
        # .py`, `windowing/`): widen `FLOW_ACCEPTED`, break `_add_flow`'s transport gate,
        # or change a class name, and both sensors change IDENTICALLY, both stay equal,
        # and the gate reports agreement about a fabricated census. Two implementations
        # over one shared layer agree perfectly whenever that layer regresses — so at
        # least one assertion on every axis has to be anchored to the plan, which is the
        # one thing that does not move when the code does.
        expected_mix = Counter(PLAN_STATE_CLASS[f.outcome] for f in cast.flows)
        expected_listen: dict[str, set] = defaultdict(set)
        swept_ports: set = set()
        for f in cast.flows:
            if f.proto not in ("tcp", "udp"):
                continue  # `dst_port` is only a port for tcp/udp (`_add_flow`'s own gate)
            port_fact = f"{f.dport}/{f.proto}"
            if f.outcome == PLAN_ACCEPTED_OUTCOME:
                expected_listen[f.dst].add(port_fact)
            else:
                swept_ports.add(port_fact)
        # A port this plan ONLY ever swept and NO host anywhere accepted on: the exact
        # signature a phantom listener would wear. Asserted absent from every host on both
        # sensors, and asserted NON-EMPTY first, since with no such port the check below
        # would be satisfied by a fixture that never scanned anything.
        phantom_ports = swept_ports - {pf for pfs in expected_listen.values() for pf in pfs}

        state_mismatches, listen_mismatches = [], []
        for addr in sorted(zaddrs & saddrs):
            z, s = znodes[addr], snodes[addr]
            if dict(z["state_mix"]) != dict(s["state_mix"]):
                state_mismatches.append(f"{addr}.state_mix: zeek={dict(sorted(z['state_mix'].items()))} "
                                        f"suricata={dict(sorted(s['state_mix'].items()))}")
            if z["listen_ports"] != s["listen_ports"]:
                listen_mismatches.append(
                    f"{addr}.listen_ports: zeek-only={sorted(z['listen_ports'] - s['listen_ports'])} "
                    f"suricata-only={sorted(s['listen_ports'] - z['listen_ports'])}")
        run_mixes = {}
        for name, nodes in (("zeek", znodes), ("suricata", snodes)):
            mix: Counter = Counter()
            for feats in nodes.values():
                for cls, n in feats["state_mix"].items():
                    mix[cls] += n
            run_mixes[name] = mix
        if state_mismatches:
            for m in state_mismatches[:20]:
                fail(m)
        elif len(expected_mix) < 2:
            fail(f"the PLAN spans only {sorted(expected_mix)} — one class is not a "
                "discrimination test; `_synthetic_cast` is supposed to exercise "
                "accepted / no_reply / refused separately, and with one class this "
                "section cannot tell a working classifier from a constant")
        else:
            for name, mix in run_mixes.items():
                if mix != expected_mix:
                    fail(f"{name}: state_mix run totals {dict(sorted(mix.items()))} do not "
                        f"match the plan's own outcome census {dict(sorted(expected_mix.items()))} "
                        "— the plan says what happened on the wire, so this is a "
                        "classifier (or a `FLOW_STATE_CLASSES`) regression, not a "
                        "disagreement between sensors: note that the two sensors can still "
                        "agree with EACH OTHER while both drift from it")
            if all(mix == expected_mix for mix in run_mixes.values()):
                ok("state_mix identical on every address AND equal to the plan's own "
                  f"outcome census {dict(sorted(expected_mix.items()))} — the two "
                  "classifiers (`_CONN_STATE_CLASS` on a Zeek token, `_flow_state_class` "
                  "on counters + RFC 793 flag bytes) reach the SAME, and the RIGHT, "
                  "`FLOW_STATE_CLASSES` verdict on every flow, refusals included")
        got_listen = {
            name: {a: set(f["listen_ports"]) for a, f in nodes.items() if f["listen_ports"]}
            for name, nodes in (("zeek", znodes), ("suricata", snodes))}
        if listen_mismatches:
            for m in listen_mismatches[:20]:
                fail(m)
        elif not expected_listen:
            fail("the PLAN contains no accepted tcp/udp flow, so `listen_ports` has no "
                "ground truth to be checked against and every claim below is vacuous")
        elif not phantom_ports:
            fail("the PLAN has no port that was swept and never accepted anywhere, so "
                "the phantom-listener check cannot fire; `_synthetic_cast`'s scanner "
                "sweep is what makes this axis meaningful (`M2`)")
        else:
            for name, got in got_listen.items():
                if got != expected_listen:
                    invented = {a: sorted(got[a] - expected_listen.get(a, set())) for a in got
                                if got[a] - expected_listen.get(a, set())}
                    missed = {a: sorted(expected_listen[a] - got.get(a, set()))
                              for a in expected_listen
                              if expected_listen[a] - got.get(a, set())}
                    fail(f"{name}: listen_ports disagrees with the plan — INVENTED "
                        f"{dict(sorted(invented.items()))} (a service asserted on a host "
                        f"that never accepted there), MISSED {dict(sorted(missed.items()))}")
                phantoms = {a: sorted(pfs & phantom_ports) for a, pfs in got.items()
                            if pfs & phantom_ports}
                if phantoms:
                    fail(f"{name}: {len(phantoms)} host(s) claim a listener on a port this "
                        f"plan only ever SWEPT ({sorted(phantom_ports)}) and nothing ever "
                        f"accepted on: {dict(sorted(phantoms.items()))} — the M2 "
                        "phantom-listener shape, on the axis the whole role/service layer "
                        "rests on")
            if all(got == expected_listen for got in got_listen.values()):
                n_listen = sum(len(pfs) for pfs in expected_listen.values())
                ok(f"listen_ports identical on both sensors AND equal to the plan "
                  f"({len(expected_listen)} host(s), {n_listen} (port, proto) fact(s)); no host "
                  f"on either sensor claims a listener on the swept-only port(s) "
                  f"{sorted(phantom_ports)}")

        # -- the two checks `Q34` proves this gate was missing (added 2026-08-26) ----
        # `Q34` is EXACTLY the class of defect this script exists to catch — a published
        # node field that is silently sensor-specific — and this script did not catch it.
        # `s0_count`/`rej_count` gated on the verbatim Zeek `conn_state` words, read a
        # confident `0` on every Suricata and axquery row, and shipped for five days.
        # Naming the reason precisely matters, because it is a process failure rather than
        # a missing feature: `NODE_SUM_FIELDS` is a HAND-WRITTEN allowlist of what to
        # compare, those two fields were simply not in it, and "excluded as not
        # comparable" was never revisited. Both checks below therefore read the row's OWN
        # published key set instead of any list maintained in this file, so a field added,
        # renamed or resurrected upstream is in scope by default rather than on the next
        # editor remembering to widen a tuple.
        #
        # (a) `state_mix` must ACCOUNT FOR every flow the host opened. `_add_flow`
        # increments `flows_out` and `state_mix[ev.state_class]` on the same node in the
        # same breath, so `sum(state_mix.values()) == flows_out` on every row, and the
        # published bundle says so in its own comment ("summing to `flows_out`"). This is
        # what makes an ABSENT class a proven zero instead of an unknown — the property
        # `Q34`'s deletion rests on, since the whole argument for deleting rather than
        # re-keying is that `state_mix["attempted"]` already answers the question
        # correctly. It is also the shape that catches the NEXT `Q34`: a class histogram
        # that quietly stops being credited on one sensor's path (the way `s0_count` was
        # never credited on any non-Zeek path) shows up here as a total that no longer
        # reconciles against an independently-maintained counter, on that sensor only.
        # Checked per address per sensor, and it is NOT a cross-sensor comparison — two
        # sensors agreeing about a broken histogram is precisely what this gate's own
        # docstring says equality cannot see.
        recon = []
        for name, nodes in (("zeek", znodes), ("suricata", snodes)):
            for addr in sorted(nodes):
                feats = nodes[addr]
                total = sum(feats["state_mix"].values())
                if total != feats["flows_out"]:
                    recon.append(f"{name}/{addr}: state_mix totals {total} but flows_out "
                                 f"is {feats['flows_out']} "
                                 f"({dict(sorted(feats['state_mix'].items()))})")
        if recon:
            for m in recon[:20]:
                fail(m)
        else:
            n_rows = len(zaddrs) + len(saddrs)
            ok(f"state_mix reconciles with flows_out on all {n_rows} address-rows across "
              "both sensors — every flow a host opened is accounted for in exactly one "
              "`FLOW_STATE_CLASSES` bucket, which is what makes an absent class a PROVEN "
              "zero rather than a field nobody incremented (`Q34`)")

        # (b) No published node field may be structurally CONSTANT across every row on one
        # sensor while VARYING on the other. That asymmetry is the fingerprint `s0_count`
        # wore: `{0, 40}` on Zeek, `{0}` on Suricata and on axquery, for every host in
        # every window — a field that is not measuring anything on one sensor, which is a
        # different and more insidious failure than a field the two sensors disagree
        # about (that one this gate already catches everywhere). Deliberately ONE-SIDED:
        # a field constant on BOTH sensors is this fixture being small (`locality`,
        # `segments`), not a sensor-specific field, and flagging it would make the check
        # noise that someone eventually suppresses.
        #
        # This is a heuristic and is written as one. It cannot prove a field is
        # cross-sensor honest, and a legitimately one-sided-constant field could exist
        # later — if one does, it belongs in this script's named-exception list up top,
        # DECLARED with its reason, which is exactly the review this gate failed to force
        # the first time.
        zvar, svar = node_field_variation(zwins), node_field_variation(swins)
        if set(zvar) != set(svar):
            fail(f"the two sensors publish different node-bundle FIELDS: "
                f"zeek-only={sorted(set(zvar) - set(svar))} "
                f"suricata-only={sorted(set(svar) - set(zvar))} — every node row comes "
                "from one `windowing/windows.py::_close`, so this cannot happen from a "
                "plugin difference; a key that appears on one sensor only is a row shape "
                "that was never designed")
        else:
            zconst = {k for k, v in zvar.items() if len(v) == 1}
            sconst = {k for k, v in svar.items() if len(v) == 1}
            one_sided = sorted(zconst ^ sconst)
            varies_on_both = sorted(set(zvar) - zconst - sconst)
            if one_sided:
                for field_name in one_sided:
                    flat, other = (("zeek", zvar), ("suricata", svar)) if \
                        field_name in zconst else (("suricata", svar), ("zeek", zvar))
                    fail(f"node field `{field_name}` is CONSTANT on {flat[0]} "
                        f"({sorted(flat[1][field_name])[0][:60]}) across every row of "
                        f"every window, and VARIES on {other[0]} — the `Q34` fingerprint: "
                        "a field keyed on one sensor's own vocabulary measures nothing on "
                        "the other while still publishing a confident value. Either fix "
                        "the field or add it to this script's named exceptions WITH its "
                        "reason; do not widen this check")
            elif not varies_on_both:
                fail("no node field varies across rows on BOTH sensors — this scan has "
                    "nothing to discriminate with and would pass for any field at all; "
                    "the fixture stopped producing a heterogeneous host population")
            else:
                both_const = sorted(zconst & sconst)
                ok(f"no node field is constant on one sensor and varying on the other "
                  f"({len(zvar)} published field(s) scanned, {len(varies_on_both)} vary on "
                  f"both, {len(both_const)} constant on both: {both_const}) — the `Q34` "
                  "shape (`s0_count` = 0 on every non-Zeek row, forever) has nowhere left "
                  "to hide unnoticed")

        # -- 4. edge set -----------------------------------------------------------
        print("\n[4/7] edge set ((src, dst) conversations) + top-K longtail split")
        from aximprint.windowing.windows import TOP_PEERS
        zedges, zlong, zlabels = aggregate_edges(zwins, z2a)
        sedges, slong, slabels = aggregate_edges(swins, s2a)
        if set(zedges) == set(sedges):
            ok(f"identical (src, dst) edge set ({len(zedges)} edges)")
        else:
            fail(f"edge sets differ: zeek-only={sorted(set(zedges) - set(sedges))[:5]} "
                f"suricata-only={sorted(set(sedges) - set(zedges))[:5]}")
        edge_mismatches = []
        for key in sorted(set(zedges) & set(sedges)):
            z, s = zedges[key], sedges[key]
            if z["flows"] != s["flows"]:
                edge_mismatches.append(f"{key}.flows: zeek={z['flows']} suricata={s['flows']}")
                continue
            expected_delta = REFUSED_EXTRA_BYTES * refused_pair_count.get(key, 0)
            got_delta = s["bytes"] - z["bytes"]
            if got_delta != expected_delta:
                edge_mismatches.append(f"{key}.bytes: zeek={z['bytes']} suricata={s['bytes']} "
                                       f"(delta={got_delta}, expected {expected_delta})")
        if edge_mismatches:
            for m in edge_mismatches[:20]:
                fail(m)
        else:
            ok("every shared edge's flow count matches exactly; byte totals match "
              f"exactly except the named +{REFUSED_EXTRA_BYTES}-byte refused-scan case")
        # GROUND TRUTH for the spill, from the plan. "Identical on both sensors" is
        # satisfied by both being EMPTY — and an empty spill on both sides would print
        # "the same top-64-by-bytes ranking was reached independently" while proving that
        # top-K eviction never ran at all. Which sources must spill, and by how many
        # peers, is a pure function of the plan: eviction happens once per window at close
        # over that window's own accumulated edges, keyed `(src, dst, proto)`, keeping
        # `TOP_PEERS` and spilling the rest, so a source with N > TOP_PEERS distinct
        # (dst, proto) peers in a window spills exactly N - TOP_PEERS of them.
        #
        # Two assumptions, both true of THIS plan and both worth stating because the
        # derivation is exact only while they hold: (1) the spilling source is never
        # anchored mid-window, so its edges do not split across two entity keys — the
        # collector carries no DHCP/Kerberos/NTLM evidence in either fixture; (2) it sends
        # exactly ONE flow per peer, so a DESTINATION that anchors mid-window cannot
        # contribute two edge keys to its ranking either (a split needs flows on both
        # sides of the anchor). If either stops holding, this assertion fails loudly rather
        # than silently degrading, which is the right way round.
        peers_per_window: dict[tuple, set] = defaultdict(set)
        for f in cast.flows:
            peers_per_window[(int(f.ts // WINDOW_SECONDS), f.src)].add((f.dst, f.proto))
        expected_tail: dict[str, int] = defaultdict(int)
        for (_w, src), peers in peers_per_window.items():
            if len(peers) > TOP_PEERS:
                expected_tail[src] += len(peers) - TOP_PEERS
        if not expected_tail:
            fail(f"no source in the plan exceeds TOP_PEERS={TOP_PEERS} peers in any single "
                "window, so `__longtail__` is expected to be EMPTY on both sensors and "
                "comparing the two proves nothing about top-K eviction; give the "
                "collector more peers (`_synthetic_cast`) or this axis is untested "
                "(`Q11` is about exactly this spill path)")
        elif set(zlong) != set(slong) or any(zlong[k] != slong[k] for k in zlong):
            fail(f"__longtail__ spill differs between sensors: zeek={zlong} suricata={slong}")
        else:
            for name, long in (("zeek", zlong), ("suricata", slong)):
                if set(long) != set(expected_tail):
                    fail(f"{name}: sources that spilled {sorted(long)} != the plan's own "
                        f"{sorted(expected_tail)} — top-K eviction fired on the wrong "
                        "entities (or stopped firing)")
                elif any(long[src]["tail_peers"] != expected_tail[src] for src in long):
                    fail(f"{name}: spilled peer counts "
                        f"{ {src: v['tail_peers'] for src, v in sorted(long.items())} } != "
                        f"the plan's own {dict(sorted(expected_tail.items()))}")
            if (set(zlong) == set(expected_tail)
                    and all(zlong[src]["tail_peers"] == expected_tail[src] for src in zlong)):
                ok(f"__longtail__ spill identical on both sensors AND equal to the plan: "
                  f"{ {src: v['tail_peers'] for src, v in sorted(zlong.items())} } peer(s) "
                  f"evicted past TOP_PEERS={TOP_PEERS}, same flows/bytes on both sides — "
                  "the same top-64-by-bytes ranking, reached independently, over a spill "
                  "that demonstrably HAPPENED (byte totals stay cross-sensor-only: which "
                  "64 peers survive is a byte RANKING, not derivable from the plan without "
                  "reimplementing the eviction this is checking)")
        # The edge KEY deliberately drops the L7 "proto" label (see `aggregate_edges`'s
        # own docstring) — assert here, explicitly, that the two sensors' label
        # VOCABULARIES genuinely differ, rather than silently relying on the key
        # having dropped the field to hide it. If this ever starts passing as EQUAL
        # SETS it means the fixture stopped exercising the divergence, not that the
        # plugin started normalizing labels (nothing in `SERVICE_PROJECTIONS`/
        # `IDENTITY_PROJECTIONS`'s cross-sensor discipline applies to `FlowEvent
        # .service`, which stays sensor-native by design).
        if zlabels != slabels and "krb" in zlabels and "krb5" in slabels:
            ok(f"edge L7-label vocabularies differ as expected: zeek={sorted(zlabels)} "
              f"suricata={sorted(slabels)} (FlowEvent.service is sensor-native, never "
              "normalized across plugins — not a bug)")
        else:
            fail(f"expected the edge label vocabularies to differ (zeek 'krb' vs "
                f"suricata 'krb5' at minimum); got zeek={sorted(zlabels)} "
                f"suricata={sorted(slabels)}")

        # -- 5. identity bindings ---------------------------------------------------
        print("\n[5/7] identity bindings (dhcp, kerberos comparable; ntlm Zeek-only, named)")
        zident = aggregate_identity(zwins)
        sident = aggregate_identity(swins)
        # **GROUND TRUTH, from the plan's own identity facts** (`_SyntheticCast.dhcp_leases`
        # / `.kerberos_principals`, hoisted out of the two renderers 2026-08-25 so there is
        # exactly one declaration of what is on the wire). The key is `(ip, raw, scope,
        # namespace)`: `raw` is `_Binding.raw`, the sensor's own string BEFORE
        # `windowing/entities.py` parsed it, so this expectation needs no reimplementation
        # of `computer_name`/`kerberos_identifier` to be exact — it asserts that the right
        # host was bound to the right identifier in the right namespace at host scope.
        #
        # Why this is a different claim from the two beside it, and why all three are
        # needed: equality-across-sensors catches one plugin drifting from the other;
        # the positive `scope == host` assertion catches BOTH drifting the same way (the
        # `@`-vs-`/` bug); and only this one catches both LOSING the axis together —
        # break DHCP option-12 hostname extraction and both sensors emit 76 bindings
        # instead of 152, agreeing perfectly, all of them host-scoped, and the two older
        # assertions print a clean pass at half the coverage.
        #
        # The normalized `identifier` (`rec[4]`) is deliberately NOT in the expectation:
        # it is the resolver's own key derivation, unit-tested where it lives. It is
        # checked here only for the one property this layer owns — that it is namespaced,
        # so two identifiers from different namespaces can never collide.
        expected_bindings = {
            "dhcp": ({(ip, mac, "host", "mac") for ip, mac, _hn in cast.dhcp_leases}
                     | {(ip, hn, "host", "hostname") for ip, _mac, hn in cast.dhcp_leases}),
            # Joined with `/`: the principal grammar `kerberos_identifier` parses, and what
            # BOTH sensors are expected to carry — Zeek's `kerberos.log` logs it pre-joined,
            # Suricata's `_krb5` joins `cname`+`realm` itself (`M1`). A plugin that joined
            # it differently would fail here, which is the correct outcome: that is the
            # exact defect this axis shipped with, and it anchored nothing while looking
            # identical on both sides.
            "kerberos": {(ip, f"{cname}/{realm}", "host", "hostname")
                         for ip, cname, realm in cast.kerberos_principals},
        }
        for kind in ("dhcp", "kerberos"):
            z_k, s_k = zident.get(kind, set()), sident.get(kind, set())
            if z_k != s_k:
                fail(f"{kind}: bindings differ — "
                    f"zeek-only={sorted(z_k - s_k)[:5]} "
                    f"suricata-only={sorted(s_k - z_k)[:5]}")
                continue
            # AGREEMENT IS NOT ENOUGH, and this is the 2026-08-25 lesson in its most
            # concentrated form. Two sensors can agree perfectly and both be wrong: the
            # Zeek fixture wrote `ws000$@CORP.AXR.LAB` and the Suricata parser composed
            # the same string with the same wrong separator, so `kerberos_identifier`
            # (which splits on `/`) classified all 20 AD machine accounts as USER
            # principals — anchoring nothing (D1) — on BOTH sides, and the equality check
            # above printed "kerberos: identical bindings (20)" and passed. Widening the
            # comparison key to carry `scope`/`namespace` makes that state VISIBLE but
            # still not FAILING, because the two broken sides carry identical scopes too.
            # Only a positive assertion about what the evidence must MEAN closes it.
            #
            # Every binding this fixture produces is host-scoped by construction — 76 DHCP
            # leases (mac + option-12 hostname) and 20 Active Directory computer accounts,
            # all naming machines, none naming people — so `user`/`referenced` here means a
            # parse failed, not that the traffic changed.
            scopes = {rec[2] for rec in z_k}
            namespaces = {rec[3] for rec in z_k}
            expected = expected_bindings[kind]
            got = {rec[:4] for rec in z_k}
            unnamespaced = sorted(rec for rec in z_k if not rec[4].startswith(rec[3] + ":"))
            if not z_k:
                fail(f"{kind}: ZERO bindings on both sensors — the equality above is "
                    "vacuous, and this kind is not actually a checked axis")
            elif got != expected:
                fail(f"{kind}: both sensors agree, and both disagree with the PLAN "
                    f"({len(got)} binding(s) against {len(expected)} on the wire) — "
                    f"missing={sorted(expected - got)[:4]} unexpected={sorted(got - expected)[:4]}. "
                    "Agreement between two plugins over one shared resolver cannot see "
                    "an axis they both lost")
            elif unnamespaced:
                fail(f"{kind}: {len(unnamespaced)} binding(s) carry an identifier that is "
                    f"not prefixed by their own namespace, e.g. {unnamespaced[0]} — two "
                    "namespaces can then collide on one key")
            elif scopes != {"host"}:
                fail(f"{kind}: bindings agree across sensors but are scoped "
                    f"{sorted(scopes)}, not ['host'] — every identifier in this fixture "
                    "names a machine (DHCP mac/hostname, AD computer accounts), so a "
                    "`user` or `referenced` scope means `windowing/entities.py` could not "
                    "parse it and it anchors nothing (D1). Both sensors being wrong the "
                    "SAME way is exactly what the equality check cannot see")
            else:
                ok(f"{kind}: identical bindings on both sensors ({len(z_k)}), all "
                  f"host-scoped, namespaces {sorted(namespaces)}, and EQUAL TO THE PLAN "
                  f"({len(expected)} (ip, raw, scope, namespace) fact(s) on the wire) — "
                  "agreeing, anchoring, and complete, three separate claims")
        z_ntlm, s_ntlm = zident.get("ntlm", set()), sident.get("ntlm", set())
        if z_ntlm and not s_ntlm:
            ok(f"ntlm: zeek has {len(z_ntlm)} binding(s), suricata has 0 — the named, "
              "structural gap (no ntlm event_type in Suricata's eve.json), not a bug")
        else:
            fail(f"ntlm: expected zeek-only ({len(z_ntlm)} bound) vs suricata NONE; "
                f"got zeek={z_ntlm} suricata={s_ntlm} — the disclosed asymmetry changed "
                "shape and this gate's exception no longer describes reality")
        # And the two NTLM-anchored hosts (dc, fileserver) must show up as a REAL
        # entity-kind divergence: bound (ent-NNNN) on Zeek, unanchored (self) on
        # Suricata -- the one place this gate's address-keyed re-keying would
        # otherwise quietly erase exactly the finding #2 exists to name.
        ntlm_addrs = {rec[0] for rec in z_ntlm}  # rec = (ip, bound_to, scope, ns, ident)
        z_bound_addrs = set(z2a.values())
        s_bound_addrs = set(s2a.values())
        if ntlm_addrs and ntlm_addrs <= z_bound_addrs and ntlm_addrs.isdisjoint(s_bound_addrs):
            ok(f"entity-kind divergence confirmed: {sorted(ntlm_addrs)} are host-scoped "
              "(ent-NNNN) on zeek, unanchored (D4, self-named) on suricata")
        else:
            fail(f"expected the ntlm-anchored addresses {sorted(ntlm_addrs)} bound on "
                f"zeek only; zeek-bound={ntlm_addrs & z_bound_addrs} "
                f"suricata-bound={ntlm_addrs & s_bound_addrs}")

        # -- arp: Suricata-only evidence that anchors NOTHING on either sensor -------
        # The mirror image of the ntlm exception above (there Zeek sees what Suricata
        # cannot; here Suricata sees what Zeek cannot — Zeek ships no `arp.log`), and the
        # highest-value parser this plugin gained: mac<->ip is host-scoped evidence, the
        # only class that may merge two addresses (`Q1`, D1). Until 2026-08-25 it was
        # exercised by the unit suite ONLY — tier 2 never saw one ARP record, so it going
        # back into `_SKIP` on the false premise it started from would have failed nothing.
        #
        # What is asserted is the HONEST CURRENT STATE, which is a three-part claim and not
        # a clean one: the parser produces the evidence, `windowing.entities
        # .IDENTITY_SCOPES` has no `arp` row, so the resolver COUNTS it
        # (`unscoped_identity_kinds`) and binds nothing. The first assertion below is a
        # deliberate TRIP-WIRE on that third part: the day the scope row lands, this fails
        # loudly, because on that day ARP stops being a named divergence and becomes a real
        # identity axis that has to be compared like `dhcp`/`kerberos` — with the awkward
        # consequence, which is exactly why it must not pass silently, that Suricata would
        # then anchor hosts Zeek structurally cannot.
        from aximprint.windowing.entities import IDENTITY_SCOPES
        expected_arp = len(cast.arp_announcements)
        expected_unruled = sum(1 for _ip, _mac, _op, why in cast.arp_nonbinding
                               if why == "unruled opcode")
        z_arp, s_arp = zrun.census[("identity", "arp")], srun.census[("identity", "arp")]
        z_unscoped, s_unscoped = (unscoped_identity_total(zwins),
                                  unscoped_identity_total(swins))
        unruled_notes = sum(n for note, n in srun.plugin.parse_notes.items()
                            if note.startswith("unruled arp opcode"))
        if "arp" in IDENTITY_SCOPES:
            fail("`windowing.entities.IDENTITY_SCOPES` now HAS an `arp` row — this gate's "
                "arp exception describes a world that no longer exists. ARP is now "
                "host-scoped evidence one sensor produces and the other structurally "
                "cannot, so it must be re-asserted as an entity-KIND divergence (like "
                "ntlm, in the other direction) and the `unscoped_identity_kinds` "
                "expectations below are wrong; do NOT delete this check to make the gate "
                "green")
        elif not expected_arp:
            fail("the eve.json fixture renders no bindable ARP record, so `_arp` — the "
                "parser with the most identity value in this plugin — is exercised at "
                "tier 2 not at all (`_SyntheticCast.arp_announcements`)")
        elif (z_arp, s_arp) != (0, expected_arp):
            fail(f"arp events: expected zeek=0 (no `arp.log` exists) and "
                f"suricata={expected_arp} (the plan's bindable announcements, and ONLY "
                f"those — an RFC 5227 probe and an unruled opcode must mint nothing); got "
                f"zeek={z_arp} suricata={s_arp}")
        elif unruled_notes != expected_unruled:
            fail(f"arp: {unruled_notes} row(s) counted as an unruled opcode, expected "
                f"{expected_unruled} — `_ARP_SENDER_OPCODES` is a CLOSED vocabulary whose "
                "refusals are counted by name, and a fall-through would show up here as 0")
        elif sident.get("arp") or (z_unscoped, s_unscoped) != (0, expected_arp):
            fail(f"arp: expected {expected_arp} unscoped identity event(s) on suricata and "
                f"0 bindings; got suricata unscoped={s_unscoped} zeek unscoped={z_unscoped} "
                f"bindings={sorted(sident.get('arp', set()))[:3]}")
        else:
            ok(f"arp: suricata parses {s_arp} announcement(s) into IdentityEvents "
              f"(refusing the plan's {len(cast.arp_nonbinding)} declared non-binding "
              f"record(s), {expected_unruled} of them counted as an unruled opcode), zeek "
              "has none (no `arp.log` exists); ALL of them land in "
              f"`unscoped_identity_kinds` ({s_unscoped}) and bind nothing, because "
              "`IDENTITY_SCOPES` has no `arp` row yet — the named, currently-honest state, "
              "with a trip-wire above for the day it changes")

        # -- 6. service census -------------------------------------------------------
        print("\n[6/7] service census (comparable kinds exact; tunnel/x509 named Zeek-only)")
        COMPARABLE_KINDS = ("ssl", "http", "ssh", "quic", "ldap_op")
        for kind in COMPARABLE_KINDS:
            z_facts = {(a, f) for a, feats in znodes.items()
                      for f in feats["services"].get(kind, ())}
            s_facts = {(a, f) for a, feats in snodes.items()
                      for f in feats["services"].get(kind, ())}
            if z_facts == s_facts and z_facts:
                ok(f"{kind}: identical service facts ({len(z_facts)})")
            elif z_facts == s_facts:
                fail(f"{kind}: both sensors report ZERO facts — fixture exercised nothing "
                    "for this kind, so this is not actually a checked axis")
            else:
                fail(f"{kind}: facts differ — zeek-only={sorted(z_facts - s_facts)} "
                    f"suricata-only={sorted(s_facts - z_facts)}")
        # snmp: `SERVICE_PROJECTIONS["snmp"]` joins `(version, display_string)`, and the
        # two sensors do NOT tie here — `ingest/suricata/plugin.py::_SNMP_ATTRS`'s own
        # docstring discloses "no confident `display_string` (sysDescr) equivalent was
        # found in Suricata's own SNMP eve fields" (Suricata's SNMP logger is
        # protocol-structural, not semantically extracting sysDescr the way Zeek's
        # analyzer does) — a real, disclosed, sensor-capability gap, not a bug. The
        # Zeek fixture's own `snmp.log` row (untouched by this work) sets
        # `display_string`, so Zeek's joined fact carries it and Suricata's cannot.
        # Comparable claim: both agree on the part BOTH sensors can see (`version`).
        z_snmp = {(a, f) for a, feats in znodes.items() for f in feats["services"].get("snmp", ())}
        s_snmp = {(a, f) for a, feats in snodes.items() for f in feats["services"].get("snmp", ())}
        z_snmp_version_only = {(a, f.split("|", 1)[0]) for a, f in z_snmp}
        if z_snmp and s_snmp and z_snmp_version_only == s_snmp and z_snmp != s_snmp:
            ok(f"snmp: agree on the `version`-only projection ({sorted(s_snmp)}); "
              f"zeek ALSO carries `display_string` ({sorted(z_snmp)}) that Suricata's "
              "eve.json structurally cannot supply -- the named, disclosed asymmetry, "
              "not a bug")
        else:
            fail(f"snmp: expected zeek's fact to be suricata's fact plus a "
                f"'|display_string' suffix; got zeek={sorted(z_snmp)} "
                f"suricata={sorted(s_snmp)}")
        # tunnel: zeek-only -- no Suricata eve.json event_type exists for it at all.
        z_tunnel = {(a, f) for a, feats in znodes.items()
                   for f in feats["services"].get("tunnel", ())}
        s_tunnel = {(a, f) for a, feats in snodes.items()
                   for f in feats["services"].get("tunnel", ())}
        if z_tunnel and not s_tunnel:
            ok(f"tunnel: zeek-only ({len(z_tunnel)} fact(s)) — no Suricata eve.json "
              "event_type exists for this stem, the named coverage gap")
        else:
            fail(f"tunnel: expected zeek-only, got zeek={z_tunnel} suricata={s_tunnel}")
        # x509: BOTH empty, by construction, on EITHER sensor -- `_SERVICE_NOT_PROJECTED
        # ["x509"]`'s own reasoning applies regardless of which sensor produced the
        # ServiceEvent: the certificate carries no connection tuple, so `ev.ip` is
        # always "" and `_add_service`'s `if not ev.ip: return` makes this kind inert
        # before `SERVICE_PROJECTIONS` is ever consulted. Asserting "both empty" here
        # is a REAL equivalence claim (matches by construction on both sides), not the
        # absence of one -- worth stating explicitly rather than skipping the kind.
        z_x509 = {(a, f) for a, feats in znodes.items()
                 for f in feats["services"].get("x509", ())}
        s_x509 = {(a, f) for a, feats in snodes.items()
                 for f in feats["services"].get("x509", ())}
        # "Both empty" is satisfied EQUALLY by *produced it and correctly dropped it* and
        # by *never produced it at all*, and only the first is what the claim above says.
        # The window artifact cannot tell them apart — the event is gone either way — so
        # the distinction is read one layer up, from the plugin's own event census
        # (`Run.census`): Zeek's `x509.log` row must become a ServiceEvent that
        # `_add_service` then drops for want of an `ip`. Suricata genuinely never produces
        # one (no such event_type), which is a DIFFERENT fact and is stated as such rather
        # than folded into the same sentence.
        z_x509_emitted = zrun.census[("service", "x509")]
        s_x509_emitted = srun.census[("service", "x509")]
        if z_x509 or s_x509:
            fail(f"x509: expected no node-level fact on either sensor, got "
                f"zeek={z_x509} suricata={s_x509}")
        elif not z_x509_emitted:
            fail("x509: the zeek plugin emitted NO ServiceEvent(kind='x509') at all, so "
                "'produced and correctly dropped' is not what is being observed here — "
                "the parser stopped producing, and an inertness claim about "
                "`_SERVICE_NOT_PROJECTED['x509']` cannot be made from an empty input "
                "(`build_synthetic_capture` writes an `x509.log` row for exactly this)")
        elif s_x509_emitted:
            fail(f"x509: suricata emitted {s_x509_emitted} ServiceEvent(kind='x509') — no "
                "eve.json event_type for it exists, so either the fixture or the parser "
                "grew one and this exception needs rewriting")
        else:
            ok(f"x509: zeek PRODUCED {z_x509_emitted} ServiceEvent(kind='x509') and the "
              "windowing layer correctly dropped it (no connection tuple to key on, "
              "`_add_service`'s `if not ev.ip`); suricata produces none at all — both "
              "empty downstream, for two different and separately-checked reasons")

        # -- 7. taint, capture_profile, sensor health --------------------------------
        print("\n[7/7] alert taint, capture_profile channel, sensor health")
        z_tainted = {a for a, f in znodes.items() if f["tainted"]}
        s_tainted = {a for a, f in snodes.items() if f["tainted"]}
        if z_tainted == s_tainted and z_tainted:
            ok(f"identical tainted-address set ({sorted(z_tainted)})")
        else:
            fail(f"tainted sets differ: zeek={sorted(z_tainted)} suricata={sorted(s_tainted)}")

        z_profile = capture_profile_union(zwins)
        s_profile = capture_profile_union(swins)
        if z_profile.get("ocsp_cache_lifetime", 0) > 0 and not s_profile:
            ok(f"capture_profile: zeek observed {z_profile}, suricata is {{}} in every "
              "window — SuricataPlugin never imports ProfileEvent, a structural gap, "
              "not a fixture omission")
        else:
            fail(f"capture_profile: expected zeek non-empty / suricata empty; got "
                f"zeek={dict(z_profile)} suricata={dict(s_profile)}")

        zhealth, shealth = aggregate_health(zwins), aggregate_health(swins)
        # `sensor_pkts_dropped_max`: a named ZEEK-ONLY exception since 2026-08-25, in the
        # same shape as `capture_loss_pct_max` below, and it is a RULING rather than a
        # capability gap — the eve.json fixture DOES carry `stats.capture.kernel_drops`
        # (40, then 12, the same two readings `stats.log` carries), and `stats` is in
        # `_PARSERS`. `ingest/suricata/plugin.py::_stats` reads them and mints nothing:
        # Suricata's counters are cumulative-since-start unless the deployment set
        # `deltas: yes`, and nothing in one JSON record proves which mode produced it, so
        # `pkts_dropped` ("packets the capture layer discarded") cannot be distinguished
        # from "drops since the engine started" — two different quantities that would be
        # compared across sensors and across windows as if they were one. That decline is
        # symmetric with the same module's decline of `stats.decoder.pkts`/`.bytes`.
        #
        # Asserted as `None`, not skipped, because `None` is the load-bearing half:
        # `LES-006`'s "not observed" is not "no drops". The reopen condition is one
        # measurement, named here so this exception has a way to die — read `stats.uptime`
        # across two consecutive `stats` records, or read the deployment's `deltas:`
        # setting; either settles every counter in that parser at once, at which point
        # this becomes an equality assertion on 40.0 and this comment goes away.
        if zhealth["sensor_pkts_dropped_max"] is not None and \
                shealth["sensor_pkts_dropped_max"] is None:
            ok(f"sensor_pkts_dropped_max: zeek={zhealth['sensor_pkts_dropped_max']}, "
              "suricata=None — `_stats` DECLINES `capture.kernel_drops` (cumulative vs "
              "per-interval is unprovable from one record); a named ruling with a "
              "one-measurement path back, and `None` is 'not observed', never 'no drops'")
        else:
            fail(f"sensor_pkts_dropped_max: expected zeek-set/suricata-None, got "
                f"zeek={zhealth['sensor_pkts_dropped_max']} "
                f"suricata={shealth['sensor_pkts_dropped_max']} — either Zeek stopped "
                "reporting drops, or `_stats` started minting a counter it declined; "
                "both change what this exception describes")
        if zhealth["capture_loss_pct_max"] is not None and shealth["capture_loss_pct_max"] is None:
            ok(f"capture_loss_pct_max: zeek={zhealth['capture_loss_pct_max']}, suricata=None "
              "— no `capture_loss`-shaped eve.json event exists; named, not a bug")
        else:
            fail("capture_loss_pct_max: expected zeek-set/suricata-None, got "
                f"zeek={zhealth['capture_loss_pct_max']} suricata={shealth['capture_loss_pct_max']}")
        # `unparsed_log_types`/`unrecognized_log_types`: the TWO transports cannot even
        # agree on the unit (Zeek: file stems, audited from window 0; Suricata:
        # event_type values, audited only once the source is fully drained — that
        # module's own "the census contract is weaker than Zeek's" section). Comparable
        # claim: both eventually (by the run's LAST window) report a real, non-empty
        # census with at least one skipped-by-name and one genuinely unrecognized entry,
        # and Suricata's shows `None` for every window but the last, exactly as disclosed.
        #
        # **Asserted as the SPLIT, not as two non-empty lists.** `unrecognized` is a
        # SUBSET of `unparsed` by construction, so "both non-empty" proves only the
        # unrecognized half: delete a plugin's `_SKIP["drop"]`/`_SKIP["reporter"]` row and
        # the stem simply moves from one bucket into the other, both lists stay non-empty,
        # and the check stays green while the no-fall-through discipline it exists to
        # police has stopped being exercised at all. The residual
        # `unparsed - unrecognized` is the skipped-BY-NAME set, and it is the half that
        # carries the discipline: a stem in it was ruled on, a stem outside it was not.
        # Both halves are compared against what the FIXTURE deliberately plants
        # (`EXPECTED_UNPARSED`) rather than against each plugin's own `_SKIP` table, which
        # would be the assertion agreeing with the code under test.
        split_findings = []
        for name, health in (("zeek", zhealth), ("suricata", shealth)):
            unparsed = set(health["unparsed_log_types_final"] or ())
            unrecognized = set(health["unrecognized_log_types_final"] or ())
            expected = EXPECTED_UNPARSED[name]
            if not unrecognized & unparsed and unrecognized:
                split_findings.append(
                    f"{name}: unrecognized {sorted(unrecognized)} is not a subset of "
                    f"unparsed {sorted(unparsed)} — the two are supposed to nest")
            if unrecognized != expected["unrecognized"]:
                split_findings.append(
                    f"{name}: unrecognized={sorted(unrecognized)}, fixture plants "
                    f"{sorted(expected['unrecognized'])}")
            skipped = unparsed - unrecognized
            if skipped != expected["skipped_by_name"]:
                split_findings.append(
                    f"{name}: skipped-BY-NAME (unparsed - unrecognized) = "
                    f"{sorted(skipped)}, fixture plants "
                    f"{sorted(expected['skipped_by_name'])} — a stem that fell out of "
                    "`_SKIP` moves into the unrecognized bucket instead of disappearing, "
                    "which is why only this residual can see it")
        if split_findings:
            for m in split_findings:
                fail(m)
        elif not shealth["unparsed_none_until_last"]:
            fail("suricata reported a non-None unparsed census before its final window — "
                "that transport cannot know it until the source is drained (its own "
                "'the census contract is weaker than Zeek's'), so a value here is a "
                "premature claim, not an improvement")
        else:
            ok("unparsed/unrecognized log types: BOTH buckets exactly as planted on both "
              f"sensors — skipped-by-name zeek={sorted(EXPECTED_UNPARSED['zeek']['skipped_by_name'])} "
              f"suricata={sorted(EXPECTED_UNPARSED['suricata']['skipped_by_name'])}, "
              f"unrecognized zeek={sorted(EXPECTED_UNPARSED['zeek']['unrecognized'])} "
              f"suricata={sorted(EXPECTED_UNPARSED['suricata']['unrecognized'])}; suricata "
              "correctly reports None for every earlier window (the disclosed weaker "
              "timing), zeek does not need to (pre-first-yield census)")

        print()
        if _FAILURES:
            print(f"✗ {len(_FAILURES)} equivalence finding(s):")
            for f in _FAILURES:
                print(f"    - {f}")
            args.keep = True
            return 1
        print("✓ Suricata and Zeek pipelines agree on every comparable axis; every "
             "named exception is exactly the disclosed shape")
        return 0
    finally:
        if args.keep:
            print(f"  (workdir kept: {workdir})")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
