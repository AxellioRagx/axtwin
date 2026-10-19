"""Belief store -> a PIT (`docs/WORK_ORDER_network_ir.md` build step 3).

Reads the belief store and produces the rows `docs/SPEC_network_ir.md` describes. This is the one
module in `ir/` that imports from elsewhere in the project, and it imports **downward only**
(`ir` -> `store`), the same direction `synth` already takes.

**Deterministic, and it matters more here than anywhere else in the pipeline.** A drift comparator
subtracts two PITs, so *any* instability reads as drift — a set reaching an output, an unordered
DuckDB result, a wall-clock value. `ir_pit`'s build columns are the one declared exception, and the
SPEC tells a comparator to exclude them from a structural diff and report a build change separately.

**Step 1b landed here rather than in the windowing layer**, and the reason is a measurement rather
than a shortcut: per-segment coverage turns out to be fully derivable from `node_features`, which
already records each entity's segments per window. "Traffic was observed from segment S in window W"
is exactly "some entity in window W had S among its segments". No new accumulator, no event-model
change — so building it in `windowing/` would have added a counter to compute what the store can
already answer.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from aximprint.ir.keys import derive_entity_key, find_collisions, unusable_namespaces
from aximprint.ir.schema import SCHEMA_VERSION, evidence_rank
from aximprint.provenance import build_stamp


class MaterializeError(Exception):
    """The store cannot produce an honest PIT. Loud, never a degraded one."""


def pit_id_for(source: str, as_of: float, since: Optional[float] = None,
               vantage: Optional[str] = None) -> str:
    """A deterministic id for `(source, since, as_of, vantage)` — D3's identity, one column.

    Hashed rather than concatenated because `source` is a filesystem path: it can contain any
    character, be arbitrarily long, and would make the id unusable as a label. The `as_of` stays
    readable so a human can order PITs by eye, and the hash is what disambiguates two sources
    captured at the same instant.

    **`since` joined the identity 2026-08-17, and it is a fix rather than a widening.** The
    original identity was `(source, as_of)`, from before `since` existed; build step 5 of
    `WORK_ORDER_network_ir.md` then added a lower bound without revisiting it, so a PIT over
    `[W1, W2]` and a PIT over `[W2, W2]` — the same source, the same `as_of`, genuinely different
    observations — collided on one id and the second write was refused as a duplicate. Found by
    D10's own preferred workflow: *"materialize over the span"* and *"materialize each window"*
    are the two things a time-axis user does first, and they collide.

    **`vantage` joined for the same reason**, and the case is D1's own: two DIFFERENT taps writing
    into one directory share a source, so without it they share an id — and the second one cannot
    even be stored beside the first, let alone refused by the comparator for the right reason. The
    identity of a PIT includes where it was watched from.

    **An unbounded, vantage-less PIT's id is unchanged**, deliberately: with both `None` this
    hashes exactly what it hashed before, so every cumulative PIT ever written keeps its id and
    nothing that referenced one has to be re-derived (`LES-113` — a key whose derivation moves
    silently stops joining). Only the cases that could not be written at all before get a new
    spelling.
    """
    keyed = source
    if since is not None:
        keyed += f"|since={since!r}"
    if vantage is not None:
        keyed += f"|vantage={vantage}"
    digest = hashlib.sha256(keyed.encode("utf-8")).hexdigest()[:10]
    return f"pit-{digest}-{int(as_of)}"


def _run_context(store) -> dict:
    """Source, vantage, network and build identity for the PIT, from the `runs` table.

    **Fails loud, because a PIT that cannot say what it describes is not a PIT.** A store nothing
    ever cleanly closed into has no `runs` row and no source.

    **The source-equality refusal this used to make is replaced by a network-identity check**
    (`docs/WORK_ORDER_visibility_and_vantage.md` D3, which supersedes it). The old rule conflated
    two cases it could not tell apart, because the model had no concept of *the network* separate
    from *the capture*:

      * **different networks** — refuse, and that half was right;
      * **different vantages on one network**, or one vantage re-harvested into a second
        directory — refused too, and that half was wrong. Re-harvesting a tap changes a filesystem
        path and nothing about the network, and combining two taps is the entire point of the
        exercise.

    So: two distinct **networks** are refused, two distinct **vantages** are refused *and pointed
    at `merge`* (which is where a multi-vantage view is built — never implicitly, inside this
    function, D4), and differing **sources** are accepted only when every run agrees on one
    declared vantage or one declared network. Nothing is inferred: `network` is operator-declared
    (nothing in traffic identifies it) and `vantage` comes from the capture's own harvest manifest
    or an operator flag, so a store whose runs declare neither still behaves exactly as before.
    """
    rows = store.con.execute(
        "SELECT source, aximprint_version, code_fingerprint, git_commit, window_seconds, "
        "vantage, network FROM runs ORDER BY finished_at").fetchall()
    if not rows:
        raise MaterializeError(
            "belief store has no `runs` row: nothing ever closed cleanly into it, so this PIT "
            "could not say what capture it describes. Re-run `aximprint ingest` to completion.")
    sources = sorted({r[0] for r in rows})
    vantages = sorted({r[5] for r in rows if r[5]})
    networks = sorted({r[6] for r in rows if r[6]})
    # Declared by EVERY run, not merely by one: a store where run A named a tap and run B did not
    # cannot claim both runs saw through A's tap, and the undeclared half is exactly the part a
    # false claim would be about.
    one_vantage = len({r[5] for r in rows}) == 1 and len(vantages) == 1
    one_network = len({r[6] for r in rows}) == 1 and len(networks) == 1
    if len(networks) > 1:
        raise MaterializeError(
            f"belief store mixes {len(networks)} declared networks {networks}: a PIT describes ONE "
            f"network as of one time. Materialize each network from its own store.")
    if len(vantages) > 1:
        raise MaterializeError(
            f"belief store mixes {len(vantages)} vantages {vantages}: a PIT is one tap point's "
            f"view, and unioning two taps here would hide the view difference inside a single "
            f"artifact. Materialize each vantage into its own PIT and combine them with "
            f"`aximprint merge --axis vantage`, which records which tap saw what.")
    if len(sources) > 1 and not (one_vantage or one_network):
        raise MaterializeError(
            f"belief store mixes {len(sources)} sources {sources} and declares neither one vantage "
            f"nor one network for all of them: attributing this PIT to any single source would be "
            f"false for the rest, and nothing here can tell a re-harvest of one tap from two "
            f"different taps. Re-ingest with `--vantage`/`--network`, or materialize from a store "
            f"written by one source.")
    _source, version, fingerprint, commit, window_seconds, _v, _n = rows[-1]
    return {
        # Every contributing source, joined, when a declared vantage/network says they are one
        # capture's worth of evidence. `pit_id` hashes this, so the id stays deterministic and the
        # PIT keeps saying what it was built from rather than naming one of its inputs.
        "source": sources[0] if len(sources) == 1 else " + ".join(sources),
        "vantage": vantages[0] if one_vantage else None,
        "network": networks[0] if one_network else None,
        "aximprint_version": version, "code_fingerprint": fingerprint,
        "git_commit": commit, "window_seconds": window_seconds or 3600}


def _referenced_bindings(store, since: Optional[float], as_of: Optional[float]) -> list[tuple]:
    """The one store read `_unseen` needs, lifted out so `_unseen` itself is pure.

    Part of the evidence/conclusions split (`docs/WORK_ORDER_evidence_plane_snapshots.md` build
    step 1): every read of the store belongs in `snapshot_evidence`, so that what `resolve` works
    from is a bounded, frozen set of rows rather than a live connection it can re-query.
    """
    cond, params = _bound(since, as_of)
    joiner = "AND" if cond else "WHERE"
    return store.con.execute(
        f"SELECT ip, bound_to, max(count), min(first_seen), max(last_seen) "
        f"FROM identity_bindings {cond} {joiner} scope = 'referenced' "
        f"GROUP BY ip, bound_to", params).fetchall()


def _unseen(rows: list[tuple], seen_addresses: set, observed_segments: set) -> list[dict]:
    """The referenced-but-unseen census: hosts this vantage KNOWS exist and never saw (D7).

    DESIGN_BRIEF §8's own first input to the visibility model — *"referenced-but-unseen hosts (DNS
    answers, SPNs, redirects, XFF)"* — of which the DNS half already exists in the belief store and
    was dropped by the IR until schema `1.2`: **2,418 addresses on the 2026-08-11 baseline**. Pure
    re-derivation, no pipeline change, the same shape step 1b turned out to have.

    "Unseen" means *never an endpoint of a flow at this vantage*, which is why `seen_addresses` is
    a parameter rather than something re-derived here: the caller already assembled it from the
    same roster it keys entities with, and deriving it twice is how two answers to one question
    start disagreeing.

    **Locality is graded, and the strong case is the useful one.** An address whose /24 (or /64) is
    a segment this PIT actually observed traffic from is `local` with that segment named — 9 of the
    2,418 on that near-full-visibility baseline, and on a partial vantage that number *is* the
    blind spot. Everything else falls back to `windowing.segments.locality`'s own rule, the one the
    windowing layer applies when a sensor offers no verdict, so this module invents nothing: an
    RFC1918 address outside every observed segment stays `local` (it is somebody's estate, just not
    a network this tap ever saw) and a global address is `external`.

    `observations` is the per-name MAXIMUM summed over names, never the sum of every row's counter.
    `identity_bindings` is a change log (`Q1`), so one binding contributes an `opened` row carrying
    a small count and a `final` row carrying the accumulated one; summing them counts the same
    references twice. Taking the max per (address, name) also degrades correctly under the
    resolver's LRU eviction, where one name legitimately spans several intervals: it under-counts
    rather than inflating, and an inflated reference count would be evidence of nothing.
    """
    from aximprint.windowing.segments import locality as locality_of
    from aximprint.windowing.segments import segment_of

    per_address: dict[str, dict] = {}
    for address, name, count, first_ts, last_ts in rows:
        if not address or address in seen_addresses:
            continue
        acc = per_address.setdefault(address, {"names": set(), "observations": 0,
                                               "first": [], "last": []})
        acc["names"].add(name)
        acc["observations"] += int(count or 0)
        # Collected, then reduced below: a `min()` against a running value that may still be
        # `None` (a binding row with no timestamp) raises rather than skipping, and skipping is
        # what "no evidence for this one row" means (`LES-006`).
        if first_ts is not None:
            acc["first"].append(first_ts)
        if last_ts is not None:
            acc["last"].append(last_ts)
    out = []
    for address, acc in per_address.items():
        cidr = segment_of(address)
        in_observed = cidr in observed_segments
        out.append({
            "pit_id": None, "address": address,
            "locality": "local" if in_observed else locality_of(address, None)[0],
            "segment": cidr if in_observed else None,
            "reason": "referenced_by_dns", "evidence_class": "referenced",
            "identifiers": json.dumps(sorted(acc["names"])),
            "observations": acc["observations"],
            "first_ts": min(acc["first"]) if acc["first"] else None,
            "last_ts": max(acc["last"]) if acc["last"] else None,
        })
    return sorted(out, key=lambda r: r["address"])


def _bound(since: Optional[float], as_of: Optional[float]) -> tuple[str, list]:
    """The SQL window bound shared by every read below, as `(clause, params)`.

    One helper rather than the same conditional repeated five times: `since` was added after the
    fact (build step 5 found that cumulative PITs are never commensurable with each other), and
    five hand-written copies is five places for the next bound to be forgotten.
    """
    clauses, params = [], []
    if since is not None:
        clauses.append("window_start >= ?")
        params.append(since)
    if as_of is not None:
        clauses.append("window_start <= ?")
        params.append(as_of)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def _window_facts(store, since: Optional[float], as_of: Optional[float]) -> dict:
    """Window starts and the global capture-loss maximum, within the bound."""
    cond, params = _bound(since, as_of)
    rows = store.con.execute(
        f"SELECT window_start, health FROM window_health {cond} ORDER BY window_start",
        params).fetchall()
    starts = [r[0] for r in rows]
    losses = [v for v in (json.loads(r[1]).get("capture_loss_pct_max") for r in rows)
              if v is not None]
    # `None`, not 0.0, when nothing reported loss: a window nobody audited and a window audited
    # clean are different findings, and a comparator reading 0.0 as "clean" would be wrong
    # (`LES-006`).
    return {"starts": starts, "capture_loss_pct_max": max(losses) if losses else None}


def _segment_windows(store, since: Optional[float], as_of: Optional[float]) -> dict[str, int]:
    """Segment CIDR -> how many windows any traffic was observed from it (step 1b).

    A **proxy**, and `coverage_source='inferred_from_traffic'` is what says so. Zeek reports no
    per-segment loss, so this cannot be a measurement — and D4a is explicit that the difference
    matters: a host absent across a run of windows with good segment coverage is a decommission
    signal, while the same run with the tap down on that VLAN is *zero* signal, and a consumer
    that cannot tell them apart will learn the first from the second.
    """
    cond, params = _bound(since, as_of)
    present: dict[str, set] = {}
    for window_start, features in store.con.execute(
            f"SELECT window_start, features FROM node_features {cond}", params).fetchall():
        for segment in (json.loads(features).get("segments") or []):
            present.setdefault(segment, set()).add(window_start)
    return {cidr: len(windows) for cidr, windows in present.items()}


def _entity_extents(store, since: Optional[float], as_of: Optional[float], canon) -> dict[str, tuple[float, float]]:
    """Entity -> (first_seen, last_seen) window starts, **canonicalized across merges**.

    `snapshot()` carries the LAST window and a count but not the first, and D4a's within-PIT
    presence shape needs the interval rather than just its end.

    `canon` is not optional and the reason is a bug this walked into: `node_features` is
    **pre-merge** (`Q1` D2's forward-only design — a host seen as a bare address in window 1 and as
    `ent-0001` from window 2 has rows under BOTH), while `snapshot()` is post-merge. Reading one
    against the other without canonicalizing silently loses every merged entity's rows. The phase-1
    finding in `WORK_ORDER_synth_v1.md` names this exact hazard; it was written down and then
    walked into anyway, which is why the parameter is required rather than defaulted.
    """
    cond, params = _bound(since, as_of)
    out: dict[str, tuple[float, float]] = {}
    for entity, lo, hi in store.con.execute(
            f"SELECT entity, min(window_start), max(window_start) FROM node_features {cond} "
            "GROUP BY entity", params).fetchall():
        key = canon(entity, entity)
        prev = out.get(key)
        out[key] = (lo, hi) if prev is None else (min(prev[0], lo), max(prev[1], hi))
    return out


def _entity_locality(store, since: Optional[float], as_of: Optional[float], canon) -> dict[str, Optional[str]]:
    """Entity -> one inside/outside verdict for the whole PIT.

    Resolved from the per-WINDOW verdicts by `windowing.windows.locality_verdict` — the same rule
    the window layer applies one time scale down (majority, ties to local), imported rather than
    restated so the two cannot drift.

    Load-bearing because `segment IS NULL` alone conflates an internet peer with a local host on
    nothing provisionable (link-local, multicast — 117 of them on the 2026-08-11 baseline). A
    comparator filtering the monitored network on `segment IS NOT NULL` would silently drop the
    second group with no way to notice (`LES-006`).
    """
    from collections import Counter

    from aximprint.windowing.windows import locality_verdict

    cond, params = _bound(since, as_of)
    seen: dict[str, Counter] = {}
    for entity, features in store.con.execute(
            f"SELECT entity, features FROM node_features {cond}", params).fetchall():
        verdict = json.loads(features).get("locality")
        if verdict is not None:
            # Canonicalized for the same reason as `_entity_extents` — see its docstring.
            seen.setdefault(canon(entity, entity), Counter())[verdict] += 1
    return {e: locality_verdict(c) for e, c in seen.items()}


#: `docs/WORK_ORDER_traffic_profiles.md` D2/D3/D4 — the behavioral-profile constants. All four
#: below are **proposed, UNMEASURED defaults**: build step 6 of that work order pins them against
#: the ten-day baseline (`/home/rocky/harvest-corpapcn-10d/beliefs-v2.duckdb`). Naming them here
#: rather than inlining the numbers is what gives step 6 one place to change, and what makes clear
#: they were chosen, not measured (`R2`'s `snmp` `display_string` mistake is the cautionary tale
#: for the alternative).
_HOUR_BUCKETS = 24
_DOW_BUCKETS = 7

#: A host active in `_DIURNAL_MIN_WINDOWS` or fewer windows has no diurnal SHAPE — publishing a
#: 24-bucket histogram from a handful of samples would imply a daily rhythm nothing observed,
#: exactly the confident-zero-from-too-little-evidence failure `Q34` deleted two counters over.
_DIURNAL_MIN_WINDOWS = 48
#: The rate/occupancy fields need far less: a quantile from one or two samples is not a
#: distribution, but four is enough to say something about spread without over-claiming.
_RATE_MIN_WINDOWS = 4

#: D3's schedule classifier: the width of the contiguous UTC band it searches for, and the two
#: thresholds on the fraction of an entity's flows that band must hold. A perfectly FLAT 24x7
#: host puts `_BAND_HOURS / _HOUR_BUCKETS` (~0.33) of its flows in every possible band, so both
#: thresholds sit with margin either side of that midpoint rather than at it.
_BAND_HOURS = 8
_BUSY_BAND_MIN = 0.60
_QUIET_BAND_MAX = 0.10

#: Explicit rather than `sketches.Quantiles`'s own default (`_QPOINTS`) — the same discipline
#: `windowing.windows._PROFILE_QUANTILE_POINTS` already applies, so a future change to the
#: sketch's own default can never silently move a D2 field that was never told about it.
_RATE_QUANTILE_POINTS: tuple[float, ...] = (0.10, 0.50, 0.90, 0.99)

#: The zero-evidence profile every entity in `node_features` starts from. `_entity_traffic` never
#: RETURNS this itself — an entity present in `node_features` has `windows_active >= 1` by
#: construction, since a `NodeAcc` row is only created by a flow touching that entity — but
#: `resolve()` falls back to it for a keyed entity `_entity_traffic` never saw at all, the same
#: defensive `.get(key, default)` shape `extents`/`locality` already use rather than an assert:
#: `snapshot()`'s post-merge entities and `node_features`'s pre-merge rows disagreeing is exactly
#: the class of gap `LES-114` was found by once already, not a proof it cannot recur.
_EMPTY_TRAFFIC_PROFILE: dict = {
    "windows_active": 0, "hod_flows": None, "dow_flows": None,
    "flows_per_window_q": None, "bytes_per_window_q": None,
    "burstiness_p50": None, "client_ratio_p50": None, "interarrival_p50": None,
}


@dataclass
class _TrafficAcc:
    """Per-entity accumulator for one span's worth of `node_features` rows.

    Mirrors `windowing.windows.NodeAcc`'s shape one layer up: a plain field per D2 column,
    reduced once at the end (`_entity_traffic`) rather than incrementally, since every reduction
    here (a quantile, a median) needs the whole list anyway.
    """
    hod: list = field(default_factory=lambda: [0.0] * _HOUR_BUCKETS)
    dow: list = field(default_factory=lambda: [0.0] * _DOW_BUCKETS)
    flows_per_window: list = field(default_factory=list)
    bytes_per_window: list = field(default_factory=list)
    burstiness: list = field(default_factory=list)
    client_ratio: list = field(default_factory=list)
    interarrival_p50: list = field(default_factory=list)
    windows_active: int = 0


def classify_schedule(hod_flows: Optional[list]) -> Optional[str]:
    """D3: a claim about SHAPE, not the clock.

    **Public and reused one level up** by `synth/compiler.py`, which applies the identical rule
    to a role's pooled member histogram — the same "declare once, reuse at the coarser grain"
    shape `windowing.windows.locality_verdict` already establishes for `_entity_locality` above,
    just one layer further out.

    `None` in (no diurnal evidence, D4's floor), `None` out — never a class from nothing. A
    non-`None` input must be a genuine `_HOUR_BUCKETS`-bucket histogram; a wrong shape is a
    caller bug, not an evidence gap, and is asserted rather than silently absorbed (CONVENTIONS
    §3: assertions over silent defaults).

    Because the capture carries no timezone, "business-hours" cannot mean "9-5 local" — it means
    *concentrated rather than flat*: some contiguous `_BAND_HOURS`-hour UTC band holds most of an
    entity's traffic and the quietest such band holds almost none. `docs/WORK_ORDER_
    traffic_profiles.md` D3 is explicit that `cast` must caveat this: a twin deployed in a
    different timezone shifts which hours the *deployed* gate treats as busy, a fidelity
    difference, not a correctness one.
    """
    if hod_flows is None:
        return None
    assert len(hod_flows) == _HOUR_BUCKETS, (
        f"classify_schedule expects a {_HOUR_BUCKETS}-bucket histogram, got {len(hod_flows)}")
    total = sum(hod_flows)
    if total <= 0:
        # Defensively unreachable for a histogram `_entity_traffic` produced itself (see
        # `_EMPTY_TRAFFIC_PROFILE`'s docstring: a real `node_features` row never contributes a
        # zero flow total, so any histogram built from real rows sums positive) — kept because
        # `synth/compiler.py` feeds this a MEAN of member vectors it does not fully control, and
        # a divide-by-zero must never reach a caller as an exception for an input shape a caller
        # can legitimately produce (only a WRONG LENGTH is a bug, asserted above).
        return None
    doubled = hod_flows + hod_flows
    bands = [sum(doubled[i:i + _BAND_HOURS]) / total for i in range(_HOUR_BUCKETS)]
    if max(bands) >= _BUSY_BAND_MIN and min(bands) <= _QUIET_BAND_MAX:
        return "business-hours"
    return "24x7"


def _pooled_quantiles(values: list) -> list:
    """p10/p50/p90/p99 over a list of per-window observations — the whole reason
    `ir/materialize.py` needs `sketches.Quantiles` for the traffic profile, imported here rather
    than at module level to keep every `windowing` import function-local, matching this module's
    existing convention (see its own docstring: "the one module in `ir/` that imports from
    elsewhere in the project", kept to the narrowest scope each import needs)."""
    from aximprint.windowing.sketches import Quantiles

    q = Quantiles()
    for v in values:
        q.add(v)
    return q.quantiles(points=_RATE_QUANTILE_POINTS)


def _entity_traffic(store, since, as_of, canon) -> dict[str, dict]:
    """Entity -> the D2 behavioral profile, aggregated across the span under the RESOLVED entity.

    Mirrors `_entity_extents`/`_entity_locality` exactly, including WHY `canon` is a required
    positional rather than an optional one (`LES-114`): `node_features` is pre-merge,
    `snapshot()` is post-merge, and a join between them that forgets to canonicalize silently
    drops every merged entity's rows — found once already, one layer over, and the fix that
    actually held was moving the guard into the signature rather than trusting a comment.

    Every field but `windows_active` is floor-gated HERE (D4), not by the caller: nothing that
    reads this dict's output can see a value built from too little evidence, because the value
    was never computed in the first place below a floor.

    Integer flow counts accumulate into `hod`/`dow` with plain `+=` and no `ORDER BY` on the
    read below — deliberately: integer addition is exactly order-independent, which is why no
    sort is owed here the way one would be for a float accumulation (`LES-013`: sorting derives
    an order from something that has none, it does not make `+=` itself associative — and where
    the reduction already IS associative, as this one is, that argument does not apply).
    """
    cond, params = _bound(since, as_of)
    acc: dict[str, _TrafficAcc] = {}
    for window_start, entity, features in store.con.execute(
            f"SELECT window_start, entity, features FROM node_features {cond}",
            params).fetchall():
        key = canon(entity, entity)
        a = acc.setdefault(key, _TrafficAcc())
        f = json.loads(features)
        flows_total = f["flows_out"] + f["flows_in"]
        bytes_total = f["bytes_out"] + f["bytes_in"]
        when = datetime.fromtimestamp(window_start, tz=timezone.utc)
        a.hod[when.hour] += flows_total
        a.dow[when.weekday()] += flows_total          # 0 = Monday (`datetime.weekday()`)
        a.flows_per_window.append(float(flows_total))
        a.bytes_per_window.append(float(bytes_total))
        a.burstiness.append(float(f["burstiness"]))
        a.client_ratio.append(float(f["client_ratio"]))
        a.interarrival_p50.append(float(f["interarrival_q"][1]))    # index 1 == p50, `_QPOINTS`
        a.windows_active += 1

    out: dict[str, dict] = {}
    for key, a in acc.items():
        row = dict(_EMPTY_TRAFFIC_PROFILE)
        row["windows_active"] = a.windows_active
        if a.windows_active >= _DIURNAL_MIN_WINDOWS:
            total_hod, total_dow = sum(a.hod), sum(a.dow)
            # Provably positive whenever `windows_active >= 1` (see `_EMPTY_TRAFFIC_PROFILE`'s
            # docstring) -- guarded anyway rather than trusted, never divide-by-zero on a path a
            # future change to `NodeAcc` could make reachable without this function noticing.
            row["hod_flows"] = [v / total_hod for v in a.hod] if total_hod else None
            row["dow_flows"] = [v / total_dow for v in a.dow] if total_dow else None
        if a.windows_active >= _RATE_MIN_WINDOWS:
            row["flows_per_window_q"] = _pooled_quantiles(a.flows_per_window)
            row["bytes_per_window_q"] = _pooled_quantiles(a.bytes_per_window)
            row["burstiness_p50"] = statistics.median(a.burstiness)
            row["client_ratio_p50"] = statistics.median(a.client_ratio)
            row["interarrival_p50"] = statistics.median(a.interarrival_p50)
        out[key] = row
    return out


def _dump(value: Optional[list]) -> Optional[str]:
    """`json.dumps`, keeping `None` as `None` rather than the JSON string `"null"` — so a
    floor-gated field (D4) stays genuinely NULL in the artifact, never a string a reader has to
    parse before discovering it is absent."""
    return json.dumps(value) if value is not None else None


def _profile_facts(store, since, as_of) -> list[tuple]:
    """`profile_facts` rows for the span, UNREDUCED — the one store read D7's capture-wide
    profile needs, lifted out so `_capture_profile` (below) stays pure the same way `_unseen`
    already stays pure over `_referenced_bindings`
    (`docs/WORK_ORDER_evidence_plane_snapshots.md` build step 1: every store read belongs in
    `snapshot_evidence`, so `resolve()` works from frozen evidence and nothing it builds is a
    dict some earlier call already handed to a caller).
    """
    cond, params = _bound(since, as_of)
    return store.con.execute(
        f"SELECT window_start, parameter, stat, value, unit FROM profile_facts {cond}",
        params).fetchall()


def _capture_profile(rows: list[tuple]) -> list[dict]:
    """D7: `profile_facts` reaches the IR as `ir_capture_profile`, pooled across the span.

    `profile_facts` is per-WINDOW; the IR wants one row per (parameter, stat) for the whole PIT.
    Pooling the underlying OBSERVATIONS is not available — the channel stores only each window's
    own already-reduced quantile summary, never what was behind it (`Q29`; `BeliefStore.snapshot`'s
    own docstring names this exact ambiguity — "pooling raw values, weighting by `n`, or something
    else entirely" — and explicitly declines to resolve it, leaving the choice to "whoever builds
    the `tfc` compiler". This function is that choice, made once, here, rather than silently by
    whichever caller reads `profile_facts` first.)

    The rule taken: `p10`/`p50`/`p90`/`p99` reduce to the MEDIAN ACROSS WINDOWS of that window's
    own stat value — exactly the operation D2 already applies to `ir_traffic_profile.
    interarrival_p50` (median across windows of each window's own interarrival p50), applied here
    at all four quantile points instead of one, so this is symmetry with an existing,
    work-order-sanctioned rule rather than a new invention. `n` is SUMMED, not medianed: it is a
    count of observations, and the honest way to combine several windows' sample counts is to add
    them.
    """
    per_key: dict[tuple, list] = {}
    unit_of: dict[tuple, str] = {}
    windows_by_param: dict[str, set] = {}
    for window_start, parameter, stat, value, unit in rows:
        k = (parameter, stat)
        per_key.setdefault(k, []).append(value)
        unit_of[k] = unit
        windows_by_param.setdefault(parameter, set()).add(window_start)

    out = []
    for (parameter, stat), values in per_key.items():
        pooled = sum(values) if stat == "n" else statistics.median(values)
        out.append({
            "pit_id": None, "parameter": parameter, "stat": stat,
            "value": float(pooled), "unit": unit_of[(parameter, stat)],
            "windows": len(windows_by_param[parameter]),
            "evidence_class": "observed",
        })
    # Sorted here, inline, the same way `_unseen` sorts its own output inline rather than leaving
    # it to `resolve()`'s persistence-boundary block -- both are built from a dict whose iteration
    # order is not the artifact's row order (`LES-009`).
    return sorted(out, key=lambda r: (r["parameter"], r["stat"]))


def _roster(store) -> dict[str, tuple[list, list]]:
    """Entity -> (identifiers, addresses) from `entity_roster`, for key derivation."""
    return {e: (json.loads(i), json.loads(a)) for e, i, a in store.con.execute(
        "SELECT entity, identifiers, addresses FROM entity_roster").fetchall()}


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """Everything read from the belief store for one bounded span, frozen.

    **The evidence half of the evidence/conclusions split**
    (`docs/WORK_ORDER_evidence_plane_snapshots.md`, the reversal the user ratified 2026-08-20 —
    build step 1). Its whole purpose is that it holds ROWS and no live connection: `resolve()`
    below cannot re-query, so what a PIT is derived from is exactly what this object carries, and
    two PITs built from equal evidence are equal by construction rather than by coincidence.

    **`snapshot` is still a post-merge read, and moving that is build step 2, not this one.**
    `BeliefStore.snapshot()` applies `same_entity_as` internally — its own docstring says
    *"Entity merges are applied HERE"* — so resolution is not yet fully on the far side of this
    boundary. Step 1 is deliberately a pure refactor with byte-identical output; pretending the
    boundary is already in its final place would make the next step's diff unreadable.
    """
    #: `_run_context` — the run's source, vantage, network, window size, build fingerprint.
    ctx: dict
    #: `_window_facts` — window starts and the span's worst capture loss.
    windows: dict
    #: `BeliefStore.snapshot(as_of, since)` — entities, edges, bindings, segments, roles.
    snapshot: dict
    #: `entity_aliases` — the merge map `snapshot()` applied, needed again to join pre- and
    #: post-merge views of one host.
    canon: object
    roster: dict
    extents: dict
    locality: dict
    seg_windows: dict
    #: Rows for the referenced-but-unseen census (§8), read here so `_unseen` stays pure.
    referenced_bindings: list
    #: `_entity_traffic` — the D2 behavioral profile per resolved entity, already floor-gated.
    #: Safe to compute (read + reduce together) HERE rather than splitting store-read from
    #: reduction the way `referenced_bindings`/`_unseen` do: `resolve()` never stores one of
    #: these dicts directly into an output row (it always builds a fresh `{}` literal, copying
    #: values out), so there is no dict `resolve()` could mutate on a second call the way a
    #: pre-built ROW dict would be (`docs/WORK_ORDER_traffic_profiles.md` build step 1).
    traffic: dict
    #: `_profile_facts` — unreduced `profile_facts` rows for the span. Kept unreduced here, unlike
    #: `traffic` above, because `_capture_profile` builds actual OUTPUT ROW dicts that get
    #: `pit_id` stamped onto them by `resolve()`'s persistence-boundary loop — exactly the shape
    #: `referenced_bindings`/`_unseen` already split for, and for the identical reason.
    profile_facts: list
    #: The bounds this evidence was gathered under. `as_of` is RESOLVED (never `None`), because
    #: a snapshot whose upper bound is unset cannot be ordered against another one.
    since: Optional[float]
    as_of: float


def snapshot_evidence(store, as_of: Optional[float] = None,
                      since: Optional[float] = None) -> EvidenceSnapshot:
    """Read one bounded span of the belief store into a frozen `EvidenceSnapshot`.

    Every store access `materialize()` used to make now happens here and nowhere else, which is
    what makes `resolve()` a pure function of its input. Splitting them is build step 1 of
    `docs/WORK_ORDER_evidence_plane_snapshots.md`: evidence composes and conclusions do not, so
    the snapshot boundary belongs on the evidence side — measured at **48x** (a host-named entity
    is active in a median of 124 windows and named in 2).

    Raises rather than returning an empty snapshot: a span with no windows describes nothing, and
    a PIT with no observation extent could not be compared against anything.
    """
    if since is not None and as_of is not None and since > as_of:
        raise MaterializeError(f"since={since} is after as_of={as_of}: an empty span")
    ctx = _run_context(store)
    windows = _window_facts(store, since, as_of)
    if not windows["starts"]:
        raise MaterializeError(
            f"no windows in [since={since}, as_of={as_of}]: there is nothing to describe. A PIT "
            f"with no observation extent could not be compared against anything.")
    resolved_as_of = as_of if as_of is not None else max(windows["starts"])
    return EvidenceSnapshot(
        ctx=ctx,
        windows=windows,
        snapshot=store.snapshot(as_of=resolved_as_of, since=since),
        canon=store.entity_aliases(resolved_as_of).get,
        roster=_roster(store),
        extents=_entity_extents(store, since, resolved_as_of,
                                store.entity_aliases(resolved_as_of).get),
        locality=_entity_locality(store, since, resolved_as_of,
                                  store.entity_aliases(resolved_as_of).get),
        seg_windows=_segment_windows(store, since, resolved_as_of),
        referenced_bindings=_referenced_bindings(store, since, resolved_as_of),
        traffic=_entity_traffic(store, since, resolved_as_of,
                                store.entity_aliases(resolved_as_of).get),
        profile_facts=_profile_facts(store, since, resolved_as_of),
        since=since,
        as_of=resolved_as_of,
    )


def materialize(store, as_of: Optional[float] = None,
                since: Optional[float] = None) -> dict:
    """A complete PIT: `{table_name: [row dicts]}`, ready for `IRStore.write_pit`.

    Kept as the one-call form every caller already uses. Since build step 1 of
    `docs/WORK_ORDER_evidence_plane_snapshots.md` it is exactly
    `resolve(snapshot_evidence(...))` and nothing else — the two halves are separable, and this
    composition is what a test pins as byte-identical to the pre-split output.
    """
    return resolve(snapshot_evidence(store, as_of=as_of, since=since))


def resolve(ev: EvidenceSnapshot) -> dict:
    """Turn frozen evidence into a PIT: `{table_name: [row dicts]}`.

    `as_of=None` means "everything known", and is **resolved to a concrete timestamp** (the latest
    window start) rather than carried through as null. A PIT whose `as_of` is unset cannot be
    ordered against another one, and ordering is the entire point of the artifact.

    **`since` bounds the observation from below, and build step 5 is what proved it necessary.**
    Without it every PIT from one store is *cumulative* — as-of T means everything known up to T —
    so any two of them have nested extents, differ in duration, and are refused as incommensurable
    by `compare()`. That refusal is correct; the gap was that a single store could then never
    produce a comparable pair at all. Note the two purposes want different shapes and both are
    legitimate: **building a twin** wants everything known (cumulative, `since=None`), while
    **measuring drift** wants what was observed during a span. `as_of` keeps its meaning either
    way — it is the upper bound — and `since` makes explicit a lower bound that was previously
    implicit at the store's beginning.
    """
    from aximprint.windowing.segments import segment_name

    # Nothing below reads the store: `ev` is the whole input, which is the property build step 1
    # exists to establish (`docs/WORK_ORDER_evidence_plane_snapshots.md`).
    ctx, windows, snapshot = ev.ctx, ev.windows, ev.snapshot
    roster, extents, locality, seg_windows = ev.roster, ev.extents, ev.locality, ev.seg_windows
    traffic = ev.traffic
    since, resolved_as_of = ev.since, ev.as_of
    total_windows = len(windows["starts"])

    # PRECEDENCE IS RESOLVED HERE, not in the store (D3/D4,
    # `docs/WORK_ORDER_reference_ingest.md` S2). `snapshot()` returns every class's reading of a
    # segment membership and of a role — one row per (entity, class) — because the store's job is
    # to keep both readings and because `evidence_rank` lives in `aximprint.ir`, which `store` may
    # not import (CONVENTIONS §3 puts `ir` above it). D3's own argument lands in the same place:
    # `resolve()` is pure and takes a frozen snapshot, so the resolver belongs on the conclusions
    # side.
    #
    # The order is TOTAL and explicit — `(evidence_rank, -confidence, value)` — so nothing depends
    # on the order DuckDB returned rows in (`LES-009`). The last component only ever decides
    # between two readings of one class at one confidence, which the store cannot produce today
    # (it groups by class and takes `max(confidence)`); it is there so the key is total rather
    # than usually-total, which is the same rule `synth/personas.py`'s vote tiebreak learned the
    # hard way (`LES-138`).
    def _strongest(rows, value_at: int):
        best: dict = {}
        for row in rows:
            entity, value, conf, cls = row[0], row[value_at], row[-2], row[-1]
            order = (evidence_rank(cls), -(conf if conf is not None else 0.0), str(value))
            if entity not in best or order < best[entity][0]:
                best[entity] = (order, row)
        return best

    # `segments` keys on (entity, cidr, class) upstream, so one entity CAN legitimately carry two
    # different CIDRs — that predates this change and is unchanged by it. What is resolved here is
    # which reading of that entity's membership wins, exactly as the role below.
    segment_of = {e: (row[1], row[2], row[3])
                  for e, (_o, row) in _strongest(snapshot["segments"], 1).items()}
    # `role_evidence_class` is a LOOKUP now, not the constant `"imputed" if role else None` (D4).
    # It was right while the role census was the only producer of `has_role` and wrong the moment
    # an operator could assert one: publishing an `asserted` role as `imputed` is the masquerade
    # the whole class model exists to prevent, arriving through the door built to fix it.
    role_of = {e: (row[1], row[2], row[3])
               for e, (_o, row) in _strongest(snapshot["roles"], 1).items()}
    # Port evidence, carried onto the entity so persona SYNTHESIS can read it from the IR
    # (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C9). The role is the INFERENCE; these are the
    # observations it was inferred from, and a persona generated to drive a twin node has to
    # emit the latter or the node cannot exhibit the former.
    listens_of: dict[str, list[str]] = {}
    for e, port, _c in snapshot["listens"]:
        listens_of.setdefault(e, []).append(port)
    dials_of: dict[str, list[str]] = {}
    for e, port, _c in snapshot.get("dials", []):
        dials_of.setdefault(e, []).append(port)
    seen_of = {e: seen for e, _last, seen in snapshot["entities"]}

    # -- entities ---------------------------------------------------------------
    keyed: dict[str, tuple[str, str]] = {}
    unusable: dict[str, int] = {}
    unkeyable = 0
    entities = []
    # D1-D6 (`docs/WORK_ORDER_traffic_profiles.md`): one `ir_traffic_profile` row per KEYED
    # entity, built in the SAME loop as `entities` so it never has to re-derive `key` or repeat
    # the `derived is None` skip. Ordered by `source_entity` here too, exactly like `entities`,
    # and re-sorted by `entity_key` at the persistence boundary below for the same reason.
    traffic_rows = []
    # Every address this PIT saw as an endpoint of a flow — the complement of `ir_unseen` (D7).
    # Accumulated in this loop rather than re-derived, and over EVERY entity including the
    # unkeyable ones: a host that could not be given a natural key was still observed, and
    # listing it as "referenced but never seen" would be plainly false.
    seen_addresses: set = set()
    # The single vantage this PIT sees through, as a per-entity column (D4). Constant across a
    # materialized PIT, and that is not redundancy: it is what a MERGED PIT's per-entity union
    # is a generalization of, so a consumer asking "which taps saw this host" gets the same
    # column shape either way rather than a special case per PIT kind.
    entity_vantages = json.dumps([ctx["vantage"]]) if ctx["vantage"] else None
    for source_entity in sorted(seen_of):
        identifiers, addresses = roster.get(source_entity, ([], [source_entity]))
        seen_addresses.update(addresses)
        seen_addresses.add(source_entity)
        for namespace, count in unusable_namespaces(identifiers).items():
            unusable[namespace] = unusable.get(namespace, 0) + count
        derived = derive_entity_key(identifiers, addresses)
        if derived is None:
            # Counted, never given a synthetic key: a fabricated key is indistinguishable in the
            # artifact from a real one, which is the failure `evidence_class` exists to prevent
            # applied to identity instead of to a fact.
            unkeyable += 1
            continue
        keyed[source_entity] = derived
        key, kind = derived
        cidr, seg_conf, seg_class = segment_of.get(source_entity, (None, None, None))
        role, role_conf, role_class = role_of.get(source_entity, (None, None, None))
        first_seen, last_seen = extents.get(source_entity, (None, None))
        entities.append({
            "pit_id": None, "entity_key": key, "key_kind": kind,
            "source_entity": source_entity,
            # `None` where nothing could judge it — never coerced to "external" (`LES-006`).
            "locality": locality.get(source_entity),
            "segment": cidr, "segment_confidence": seg_conf,
            "segment_evidence_class": seg_class,
            "role": role, "role_confidence": role_conf,
            # **A LOOKUP since 2026-09-05, not the constant `"imputed" if role else None`**
            # (`docs/WORK_ORDER_reference_ingest.md` D4, landed with S2). It used to read "every
            # role is `imputed` by construction — a role is inferred from ports, never observed",
            # which held while the role census was the only producer of `has_role`; the reference
            # ingest is the second, and an operator's asserted role published as `imputed` is the
            # masquerade the class model exists to prevent. `None` where no role was resolved,
            # which is a supported outcome rather than a gap.
            "role_evidence_class": role_class,
            # `observed`, unlike `role`: the sensor genuinely saw these ports. Sorted for the
            # byte-stability every row set here maintains (`LES-002`).
            "listen_ports": json.dumps(sorted(set(listens_of.get(source_entity, [])))),
            "dial_ports": json.dumps(sorted(set(dials_of.get(source_entity, [])))),
            "windows_seen": seen_of[source_entity],
            "first_seen": first_seen, "last_seen": last_seen,
            "addresses": json.dumps(sorted(addresses)),
            "identifiers": json.dumps(sorted(identifiers)),
            "vantages": entity_vantages,
        })

        tp = traffic.get(source_entity, _EMPTY_TRAFFIC_PROFILE)
        schedule = classify_schedule(tp["hod_flows"])
        traffic_rows.append({
            "pit_id": None, "entity_key": key,
            "windows_active": tp["windows_active"], "windows_total": total_windows,
            "hod_flows": _dump(tp["hod_flows"]), "dow_flows": _dump(tp["dow_flows"]),
            "schedule": schedule,
            # `imputed` exactly when a class was drawn at all — the histogram beneath it is
            # `observed` (folded into the row-level `evidence_class` below), never the
            # classification itself (D3).
            "schedule_evidence_class": "imputed" if schedule is not None else None,
            "flows_per_window_q": _dump(tp["flows_per_window_q"]),
            "bytes_per_window_q": _dump(tp["bytes_per_window_q"]),
            "burstiness_p50": tp["burstiness_p50"], "client_ratio_p50": tp["client_ratio_p50"],
            "interarrival_p50": tp["interarrival_p50"],
            "evidence_class": "observed",
        })

    collisions = find_collisions(keyed)
    if collisions:
        raise MaterializeError(
            f"{len(collisions)} entity key(s) claimed by more than one entity: "
            f"{dict(list(collisions.items())[:3])}. Writing this PIT would describe two hosts as "
            f"one — a merge destroys evidence a split merely fragments. This is a report about "
            f"RESOLUTION, not about key derivation (`LES-107` strips the domain, so two hosts of "
            f"one short name in different domains collide, and the resolver already merged them).")

    # -- segments ---------------------------------------------------------------
    per_segment_entities: dict[str, int] = {}
    for row in entities:
        if row["segment"]:
            per_segment_entities[row["segment"]] = per_segment_entities.get(row["segment"], 0) + 1
    segments = [{
        "pit_id": None, "cidr": cidr, "name": segment_name(cidr),
        "entities": per_segment_entities.get(cidr, 0),
        "windows_present": seg_windows.get(cidr, 0),
        "windows_total": total_windows,
        "coverage_source": "inferred_from_traffic",
    } for cidr in sorted(per_segment_entities)]

    # -- edges ------------------------------------------------------------------
    # Both endpoints must be keyed, or the edge would reference an entity no row describes.
    # A dropped edge is counted the same way an unkeyable entity is.
    edges, dangling = [], 0
    for src, dst, proto, flows, byts in snapshot["edges"]:
        if src not in keyed or dst not in keyed:
            dangling += 1
            continue
        edges.append({
            "pit_id": None, "src_key": keyed[src][0], "dst_key": keyed[dst][0], "proto": proto,
            "flows": int(flows), "bytes": int(byts), "first_ts": None, "last_ts": None,
            # `observed` by construction today. This column is where a GNN-imputed edge lands
            # beside an observed one, distinguishable rather than merged (DESIGN_BRIEF §6).
            "evidence_class": "observed",
        })

    # -- identity bindings ------------------------------------------------------
    identity = []
    for kind, _ip, bound_to, first_seen, last_seen, entity, scope in snapshot["bindings"]:
        if entity not in keyed:
            continue
        identity.append({
            "pit_id": None, "entity_key": keyed[entity][0], "kind": kind,
            "namespace": scope, "identifier": bound_to,
            "confidence": None,
            "evidence_class": "referenced" if scope == "referenced" else "observed",
            "from_ts": first_seen, "to_ts": last_seen,
        })

    # -- the PIT row ------------------------------------------------------------
    build = build_stamp()
    pit = {
        "pit_id": pit_id_for(ctx["source"], resolved_as_of, since, ctx["vantage"]),
        "source": ctx["source"], "as_of": resolved_as_of,
        "observed_from": min(windows["starts"]),
        "observed_to": max(windows["starts"]) + ctx["window_seconds"],
        "windows": total_windows,
        "schema_version": SCHEMA_VERSION,
        "aximprint_version": build.get("aximprint_version") or ctx["aximprint_version"],
        "code_fingerprint": build.get("code_fingerprint") or ctx["code_fingerprint"],
        "git_commit": build.get("git_commit") or ctx["git_commit"],
        "capture_loss_pct_max": windows["capture_loss_pct_max"],
        "entities": len(entities), "segments": len(segments),
        # `None` throughout is UNKNOWN, not "no tap" and not "not merged into anything"
        # (`LES-006`). `vantages`/`merge_axis`/`merged_from` stay null on a materialized PIT and
        # are set only by `ir.merge`, which is what makes "this PIT is one tap's view" and "this
        # PIT combines several" tellable apart by a reader that was handed only the artifact.
        "vantage": ctx["vantage"], "network": ctx["network"],
        "vantages": None, "merge_axis": None, "merged_from": None,
    }
    unseen = _unseen(ev.referenced_bindings, seen_addresses, set(per_segment_entities))
    # D7: the capture-wide behavioral profile, built from the unreduced evidence rather than
    # stored ready-made on `ev` — see `EvidenceSnapshot.profile_facts`'s own docstring for why.
    capture_rows = _capture_profile(ev.profile_facts)
    out = {"ir_pit": [pit], "ir_entity": entities, "ir_segment": segments,
           "ir_edge": edges, "ir_identity": identity, "ir_unseen": unseen,
           "ir_traffic_profile": traffic_rows, "ir_capture_profile": capture_rows}
    for table, rows in out.items():
        for row in rows:
            row["pit_id"] = pit["pit_id"]
    # Sorted at the persistence boundary so the bytes depend on row content, never on the order a
    # dict or a DuckDB result handed them over (`LES-009`). `ir_capture_profile` is NOT re-sorted
    # here — `_capture_profile` already returns it sorted, the same treatment `ir_unseen` gets
    # from `_unseen` above.
    out["ir_entity"] = sorted(entities, key=lambda r: r["entity_key"])
    out["ir_traffic_profile"] = sorted(traffic_rows, key=lambda r: r["entity_key"])
    out["ir_edge"] = sorted(edges, key=lambda r: (r["src_key"], r["dst_key"], r["proto"]))
    out["ir_identity"] = sorted(
        identity, key=lambda r: (r["entity_key"], r["kind"], r["identifier"] or ""))
    out["_stats"] = {"unkeyable_entities": unkeyable, "dangling_edges": dangling,
                     "unusable_identifier_namespaces": dict(sorted(unusable.items())),
                     # D7's headline number, reported rather than buried in a row count: how many
                     # hosts this vantage knows exist and never saw, and how many of those are
                     # inside a segment it DID observe — the second is the blind spot proper, the
                     # first is mostly the internet.
                     "unseen_hosts": len(unseen),
                     "unseen_hosts_in_observed_segments":
                         sum(1 for r in unseen if r["segment"]),
                     # D4: how many KEYED entities fell below each behavioral-profile floor, the
                     # same "counted, never silently absorbed" discipline `unkeyable_entities`
                     # already gets, one field group over.
                     "entities_below_diurnal_floor":
                         sum(1 for r in traffic_rows if r["hod_flows"] is None),
                     "entities_below_rate_floor":
                         sum(1 for r in traffic_rows if r["flows_per_window_q"] is None)}
    return out
