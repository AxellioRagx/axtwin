"""Combining PITs — two axes, and they are not the same operation.

`docs/WORK_ORDER_visibility_and_vantage.md` D4 (vantage) and D10 (time), build steps 4 and 8.

**Why an explicit axis rather than one `merge`.** Both axes union the same row sets, and there the
resemblance stops:

| | **vantage** — several taps, one span | **time** — one tap, several spans |
|---|---|---|
| the same window is | seen by BOTH PITs | in exactly ONE PIT |
| coverage | takes the **best**: if either tap saw the segment well, the combined view did | **accumulates**: N of M plus N' of M' |
| `windows_seen` | **max** — one host present in 3 of 3 windows at either tap is present in 3 | **sum** — different windows |
| edge volume | **max** — both taps may have seen the same flow, and summing counts it twice | **sum** — disjoint spans, nothing double-counted |
| the refusal | different networks, or the same tap twice | **overlapping spans** |

A single untyped union would compute one of those wrongly for one axis, silently, and the wrong
answer looks exactly like the right one. So the axis is a required argument, not an inference.

**For the time axis, materializing over a span is the PREFERRED mechanism** (D10a):
`aximprint materialize --since W1 --as-of W40` re-reads the belief store and re-aggregates from
source rows, so it is exact rather than an approximation of exact. This module's time axis exists
for the one case that cannot serve: the belief store has been pruned (`Q5` decay) or is gone, and
only the IR artifacts survive.

**And "preferred" is a measured claim, not a preference.** A time merge combines rows that were
already KEYED, so it cannot re-key them — and entity resolution works across the seam that the
merge cannot see. Measured on the 2026-08-11 baseline, two adjacent one-hour PITs merged on the
time axis against one PIT materialized over both: **3,237 entities against 3,231**. The six extra
are hosts whose hostname or MAC evidence appeared in only ONE of the two windows
(`10.80.20.178` → `hostname:idrac-c28pf33`, `10.80.20.194` → `mac:da:f2:f0:91:2e:7b`, four more):
the span materialize resolves them, the merge leaves them as bare `address:` keys and as a second
copy of a host it already has. So a combined PIT's membership is a **superset**, never a subset —
it fragments identity rather than losing hosts — and the fragmentation is exactly proportional to
how much host-scoped evidence falls on one side of a seam. `tests/test_ir_merge.py` pins both
halves of that: the superset always, and equality on a fixture with no cross-seam evidence.

**Nothing here filters.** D9: the IR is the complete tap-point view. A merged PIT is a bigger
complete view, never a curated one.
"""
from __future__ import annotations

import hashlib
import json

from aximprint.ir.schema import (
    EVIDENCE_CLASSES,
    MERGE_AXES,
    SCHEMA_VERSION,
    evidence_rank,
    parse_version,
    vantage_set,
)


class MergeError(Exception):
    """These PITs cannot honestly be combined. Refusing is the feature, as with `compare`."""


def merged_pit_id(pit_ids, axis: str, as_of: float) -> str:
    """A deterministic id for the combination, visibly a merge.

    **Not** `materialize.pit_id_for(source, as_of)`: on the time axis every contributing PIT shares
    one source, so that function would hand the merged PIT the same id as its own latest input and
    the write would land on top of it. Hashing the contributing ids plus the axis also makes the
    two axes over one input set distinguishable, which they must be — they are different artifacts.
    """
    digest = hashlib.sha256("|".join([axis, *sorted(pit_ids)]).encode("utf-8")).hexdigest()[:10]
    return f"pit-merge-{digest}-{int(as_of)}"


def _json_list(value) -> list:
    """A JSON column as a list, whether DuckDB handed back a string or a list. `None` -> `[]`."""
    if not value:
        return []
    return json.loads(value) if isinstance(value, str) else list(value)


def _strongest(classes) -> str | None:
    """The strongest evidence class among several readings of one fact. `None` if none spoke.

    A fact observed at one tap and imputed at another **is observed** — the merge keeps the
    strongest evidence rather than the weakest or the last, the same rule `BeliefStore.snapshot`
    applies to segment membership across windows, and for the same reason: absence of evidence at
    one vantage does not weaken what another one saw.

    **Ranked by `ir/schema.py::evidence_rank`, never by a table of its own.** This module used to
    declare `_EVIDENCE_RANK = ("observed", "referenced", "imputed")` — a SECOND rank table, and
    the exact drift `docs/WORK_ORDER_reference_ingest.md` D1 made `EVIDENCE_CLASSES` an ordered
    tuple to prevent (`LES-101`). It had already gone wrong: the `1.4` widening added `asserted`
    and `imported` **above** `observed`, and the old table's `else 99` fallback would have ranked
    both of them BELOW `imputed` — silently inverting the model's whole ordering on every merged
    PIT, with no test able to see it because nothing produces those classes yet.

    An unrecognized class is **refused**, not ranked last. Ranking it last is a decision — it
    asserts the unknown reading is the weakest available, which is precisely the "no evidence"
    versus "evidence of none" conflation `LES-006` forbids — and this merge cannot compute an
    honest `evidence_class` for a value it cannot place. Refusing here is the same posture
    `_check_mergeable` already takes: a merge that cannot be made honestly is not made
    (CONVENTIONS §3). Note this is narrower than a version refusal: a higher-MINOR artifact stays
    readable, and only a row actually CARRYING an unknown class stops a merge.
    """
    present = [c for c in classes if c]
    if not present:
        return None
    if unknown := sorted({c for c in present if c not in EVIDENCE_CLASSES}):
        raise MergeError(
            f"evidence class(es) {unknown} are not in this build's vocabulary "
            f"{list(EVIDENCE_CLASSES)}, so their strength cannot be ranked and the merged row's "
            f"`evidence_class` would be a guess. Ranking an unknown class last would assert it "
            f"is the weakest reading available, which is a claim, not a default.")
    return min(present, key=evidence_rank)


def _min_opt(values):
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _max_opt(values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _check_mergeable(summaries: list[dict], axis: str) -> None:
    """Refuse the pairs whose combination would be a lie rather than a bigger truth."""
    if axis not in MERGE_AXES:
        raise MergeError(f"unknown merge axis {axis!r}; expected one of {list(MERGE_AXES)}")
    if len(summaries) < 2:
        raise MergeError("a merge needs at least two PITs")
    majors = {parse_version(s["schema_version"])[0] for s in summaries}
    if len(majors) > 1:
        raise MergeError(
            f"contributing PITs span schema MAJOR versions {sorted(majors)}: a subtractive change "
            f"may have removed a field this merge depends on (DESIGN_BRIEF §11, fail closed).")
    networks = {s.get("network") for s in summaries}
    vantage_sets = [vantage_set(s) for s in summaries]

    if axis == "vantage":
        # A network name is REQUIRED here and nowhere else, because it is the only thing that can
        # assert two different captures are of one network. Nothing in traffic says so (D3), so
        # without it this merge would be combining two arbitrary networks into a fiction.
        if len(networks) > 1 or None in networks:
            raise MergeError(
                f"a vantage merge requires every PIT to declare the SAME network, and these "
                f"declare {sorted(str(n) for n in networks)}. Nothing in traffic identifies a "
                f"network, so combining taps without that declaration would union two networks "
                f"into one artifact. Re-ingest each capture with `--network NAME`.")
        if any(v is None for v in vantage_sets):
            raise MergeError(
                "a vantage merge requires every PIT to name its vantage; at least one records "
                "none. Re-ingest it with `--vantage`, or harvest it so a vantage manifest is "
                "written beside the cold tier.")
        # The same limit `compare` refuses a drift pair on, for the same reason one layer over:
        # on this axis `windows` is the MAX across contributors, so every entity's occupancy is
        # measured against the longest PIT's denominator. Merge a 3-hour tap with a 71-hour one
        # and the short tap's hosts all read as barely-present — a scale artifact indistinguishable
        # from a real presence pattern. Imported rather than restated so the two cannot drift.
        from aximprint.ir.compare import MAX_DURATION_RATIO

        spans = [s["observed_to"] - s["observed_from"] for s in summaries]
        if min(spans) <= 0:
            raise MergeError("a PIT with a zero-length observation window cannot be merged")
        ratio = max(spans) / min(spans)
        if ratio > MAX_DURATION_RATIO:
            raise MergeError(
                f"observation spans differ by {ratio:.1f}x, beyond the {MAX_DURATION_RATIO}x "
                f"limit: occupancy in the combined PIT is measured against the longest span, so "
                f"the shorter tap's hosts would all read as barely present. Materialize the "
                f"vantages over comparable spans.")
        seen: set = set()
        for vs in vantage_sets:
            if vs & seen:
                raise MergeError(
                    f"vantage(s) {sorted(vs & seen)} appear in more than one contributing PIT: "
                    f"on this axis the same window is seen by both, so a repeated tap would have "
                    f"its coverage counted as if a second tap had confirmed it. Two spans from "
                    f"ONE tap are a `time` merge.")
            seen |= vs
    else:
        declared = {n for n in networks if n}
        if len(declared) > 1:
            raise MergeError(
                f"contributing PITs declare {sorted(declared)}: spans of two different networks "
                f"are not one network's history.")
        # `not (exactly one network, declared by all)` — NOT `len(networks) > 1`, which is the
        # shape this check first had and which passes when every PIT declares nothing at all
        # (`networks == {None}`, length one). Two different sources with no declaration between
        # them is precisely the case that cannot be justified.
        one_network = len(networks) == 1 and None not in networks
        if len({s["source"] for s in summaries}) > 1 and not one_network:
            raise MergeError(
                "a time merge combines spans from ONE capture stream; these PITs have different "
                "sources and declare no common network. Re-ingest with `--network NAME` if they "
                "are the same network.")
        if len({frozenset(v) if v else None for v in vantage_sets}) > 1:
            raise MergeError(
                f"a time merge requires one vantage across every PIT; these see through "
                f"{[sorted(v) if v else None for v in vantage_sets]}. Combine the vantages first "
                f"(`--axis vantage`), then the spans.")
        # THE refusal of this axis. Overlapping spans double-count: summing edge volumes across
        # PIT(W1-20) and PIT(W10-30) counts W10-20 twice, and the result is an inflated number
        # that looks exactly like a busier network.
        spans = sorted((s["observed_from"], s["observed_to"], s["pit_id"]) for s in summaries)
        for (a_from, a_to, a_id), (b_from, b_to, b_id) in zip(spans, spans[1:]):
            if b_from < a_to:
                raise MergeError(
                    f"observation spans of {a_id} and {b_id} overlap "
                    f"([{a_from:.0f}, {a_to:.0f}) and [{b_from:.0f}, {b_to:.0f})): every window "
                    f"in the overlap would be counted twice, inflating edge volumes and occupancy. "
                    f"Materialize adjacent, non-overlapping spans — or, if the belief store still "
                    f"has them, materialize the whole range at once with `--since`/`--as-of`, "
                    f"which is exact rather than a combination.")


def _merge_entities(raw: dict[str, dict], axis: str, pit_vantage: dict[str, list]) -> list[dict]:
    grouped: dict[str, list[tuple[str, dict]]] = {}
    for pit_id, tables in raw.items():
        for row in tables["ir_entity"]:
            grouped.setdefault(row["entity_key"], []).append((pit_id, row))
    out = []
    for key, members in grouped.items():
        rows = [r for _p, r in members]
        # Strongest segment evidence wins, chosen on confidence and not on "the last row" — the
        # same discipline `snapshot()` applies across windows one layer down.
        best_segment = max(rows, key=lambda r: (r["segment"] is not None,
                                                r["segment_confidence"] or 0.0))
        best_role = max(rows, key=lambda r: (r["role"] is not None, r["role_confidence"] or 0.0))
        localities = {r["locality"] for r in rows if r["locality"]}
        vantages = set()
        for pit_id, row in members:
            vantages |= set(_json_list(row["vantages"]) or pit_vantage.get(pit_id, []))
        seen_counts = [r["windows_seen"] or 0 for r in rows]
        out.append({
            "pit_id": None, "entity_key": key, "key_kind": rows[0]["key_kind"],
            # Every run-scoped id this entity was assembled from, kept rather than picked: `Q1` D3
            # makes `ent-NNNN` meaningful only within one run, so naming one contributor's id
            # would silently attribute the merged host to that run alone.
            "source_entity": " + ".join(sorted({r["source_entity"] for r in rows if
                                                r["source_entity"]})) or None,
            # `local` wins a disagreement, the same tie-break `windowing.windows.locality_verdict`
            # applies one time scale down: a host one tap placed inside the estate IS inside it,
            # and the other tap simply had no local evidence for it.
            "locality": "local" if "local" in localities else (
                localities.pop() if len(localities) == 1 else None),
            "segment": best_segment["segment"],
            "segment_confidence": best_segment["segment_confidence"],
            "segment_evidence_class": best_segment["segment_evidence_class"],
            "role": best_role["role"], "role_confidence": best_role["role_confidence"],
            "role_evidence_class": best_role["role_evidence_class"],
            # max across taps (the same windows, seen twice) vs sum across spans (disjoint
            # windows) — the axis distinction this whole module exists for.
            "windows_seen": max(seen_counts) if axis == "vantage" else sum(seen_counts),
            "first_seen": _min_opt(r["first_seen"] for r in rows),
            "last_seen": _max_opt(r["last_seen"] for r in rows),
            "addresses": json.dumps(sorted({a for r in rows for a in _json_list(r["addresses"])})),
            "identifiers": json.dumps(
                sorted({i for r in rows for i in _json_list(r["identifiers"])})),
            # The column multi-tap exists for: "what does tap B see that tap A does not" is a
            # question about this list, and without it a merged PIT could not answer it at all.
            "vantages": json.dumps(sorted(vantages)) if vantages else None,
        })
    return sorted(out, key=lambda r: r["entity_key"])


def _merge_segments(raw: dict[str, dict], axis: str, entities: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for tables in raw.values():
        for row in tables["ir_segment"]:
            grouped.setdefault(row["cidr"], []).append(row)
    population: dict[str, int] = {}
    for entity in entities:
        if entity["segment"]:
            population[entity["segment"]] = population.get(entity["segment"], 0) + 1
    out = []
    for cidr, rows in grouped.items():
        if axis == "vantage":
            # BEST coverage across taps (D4), keeping that row's numerator AND denominator
            # together: a ratio whose halves came from different PITs would describe no PIT.
            best = max(rows, key=lambda r: ((r["windows_present"] or 0) / r["windows_total"]
                                            if r["windows_total"] else 0.0,
                                            r["windows_present"] or 0))
            present, total = best["windows_present"], best["windows_total"]
        else:
            present = sum(r["windows_present"] or 0 for r in rows)
            total = sum(r["windows_total"] or 0 for r in rows)
        out.append({
            "pit_id": None, "cidr": cidr, "name": rows[0]["name"],
            "entities": population.get(cidr, 0),
            "windows_present": present, "windows_total": total,
            # A mixed-provenance figure takes the WEAKER label: one sensor-reported contributor
            # does not make a combined number sensor-reported, and claiming otherwise would be
            # exactly the "read a proxy as a measurement" error `coverage_source` exists to stop.
            "coverage_source": ("inferred_from_traffic"
                               if any(r["coverage_source"] == "inferred_from_traffic"
                                      for r in rows) else rows[0]["coverage_source"]),
        })
    return sorted(out, key=lambda r: r["cidr"])


def _merge_edges(raw: dict[str, dict], axis: str) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for tables in raw.values():
        for row in tables["ir_edge"]:
            grouped.setdefault((row["src_key"], row["dst_key"], row["proto"]), []).append(row)
    out = []
    for (src, dst, proto), rows in grouped.items():
        flows = [int(r["flows"] or 0) for r in rows]
        byts = [int(r["bytes"] or 0) for r in rows]
        out.append({
            "pit_id": None, "src_key": src, "dst_key": dst, "proto": proto,
            # **max, not sum, across vantages.** Two taps on the path of one conversation both
            # report it; adding their counts would double the traffic on every link the taps
            # share, which is the single most misleading number a multi-tap view could produce.
            "flows": max(flows) if axis == "vantage" else sum(flows),
            "bytes": max(byts) if axis == "vantage" else sum(byts),
            "first_ts": _min_opt(r["first_ts"] for r in rows),
            "last_ts": _max_opt(r["last_ts"] for r in rows),
            "evidence_class": _strongest(r["evidence_class"] for r in rows),
        })
    return sorted(out, key=lambda r: (r["src_key"], r["dst_key"], r["proto"]))


def _merge_identity(raw: dict[str, dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for tables in raw.values():
        for row in tables["ir_identity"]:
            grouped.setdefault(
                (row["entity_key"], row["kind"], row["namespace"], row["identifier"]),
                []).append(row)
    out = []
    for (key, kind, namespace, identifier), rows in grouped.items():
        to_values = [r["to_ts"] for r in rows]
        out.append({
            "pit_id": None, "entity_key": key, "kind": kind, "namespace": namespace,
            "identifier": identifier,
            "confidence": _max_opt(r["confidence"] for r in rows),
            "evidence_class": _strongest(r["evidence_class"] for r in rows),
            "from_ts": _min_opt(r["from_ts"] for r in rows),
            # `None` means still open at `observed_to`, so one contributor reporting it open makes
            # the merged binding open — a closing timestamp from the other would claim the binding
            # ended when something else was still watching it hold.
            "to_ts": None if any(v is None for v in to_values) else max(to_values),
        })
    return sorted(out, key=lambda r: (r["entity_key"], r["kind"], r["identifier"] or ""))


def _merge_unseen(raw: dict[str, dict], axis: str, entities: list[dict]) -> list[dict]:
    """The referenced-but-unseen census, unioned and then **corrected**.

    The correction is the whole value of combining views: an address nobody saw at tap A but which
    tap B watched send traffic is **not unseen** in the combined view, and leaving it in the census
    would report a blind spot the merged vantage does not have. Same rule on the time axis — a host
    that was only a DNS answer in January and showed up in February is no longer unseen.
    """
    seen = {a for e in entities for a in _json_list(e["addresses"])}
    grouped: dict[str, list[dict]] = {}
    candidates: set = set()
    for tables in raw.values():
        for row in tables.get("ir_unseen", []):
            candidates.add(row["address"])
            if row["address"] in seen:
                continue
            grouped.setdefault(row["address"], []).append(row)
    out = []
    for address, rows in grouped.items():
        counts = [int(r["observations"] or 0) for r in rows]
        localities = {r["locality"] for r in rows if r["locality"]}
        placed = next((r for r in rows if r["segment"]), rows[0])
        out.append({
            "pit_id": None, "address": address,
            "locality": "local" if "local" in localities else (
                localities.pop() if len(localities) == 1 else None),
            "segment": placed["segment"],
            "reason": placed["reason"], "evidence_class": placed["evidence_class"],
            "identifiers": json.dumps(
                sorted({i for r in rows for i in _json_list(r["identifiers"])})),
            # Same axis rule as an edge: two taps resolving the same name are one set of lookups
            # seen twice, two spans are two sets.
            "observations": max(counts) if axis == "vantage" else sum(counts),
            "first_ts": _min_opt(r["first_ts"] for r in rows),
            "last_ts": _max_opt(r["last_ts"] for r in rows),
        })
    # `(rows, resolved)`: DISTINCT addresses dropped because a contributor observed them — never
    # `sum(input rows) - output rows`, which counts an address listed as unseen by BOTH
    # contributors as one "resolved" while nothing resolved it. Measured on the 2026-08-11
    # baseline's two-window time merge, the naive arithmetic said 540 and the honest figure is
    # what this returns; the difference is overwhelmingly the same internet addresses referenced
    # in both windows.
    return sorted(out, key=lambda r: r["address"]), len(candidates) - len(grouped)


def merge(ir_store, pit_ids: list[str], axis: str) -> dict:
    """Combine PITs along `axis` into one new PIT, ready for `IRStore.write_pit`.

    Returns the same `{table: [rows]}` shape `materialize()` does, plus `_stats`. Raises
    `MergeError` when the combination would be dishonest rather than merely large.
    """
    summaries = {s["pit_id"]: s for s in ir_store.summary()}
    for pit_id in pit_ids:
        if pit_id not in summaries:
            raise MergeError(f"no PIT {pit_id!r} in this store")
    if len(set(pit_ids)) != len(pit_ids):
        raise MergeError(f"a PIT may only contribute once; got {pit_ids}")
    # SORTED, not caller order. A union is commutative, so `merge(ir, [a, b])` and
    # `merge(ir, [b, a])` must produce the identical artifact — and they would not if a tie-break
    # anywhere below (strongest-segment, best-coverage) resolved by which contributor was read
    # first. A comparator subtracts two PITs, so an argument-order dependence would read as drift
    # (`LES-002`). `merged_pit_id` already sorts for the same reason.
    ordered = sorted(pit_ids)
    chosen = [summaries[p] for p in ordered]
    _check_mergeable(chosen, axis)

    raw = {p: ir_store.read_pit(p) for p in ordered}
    pit_vantage = {p: sorted(vantage_set(summaries[p]) or ()) for p in ordered}

    entities = _merge_entities(raw, axis, pit_vantage)
    segments = _merge_segments(raw, axis, entities)
    edges = _merge_edges(raw, axis)
    identity = _merge_identity(raw)
    unseen, unseen_resolved = _merge_unseen(raw, axis, entities)

    as_of = max(s["as_of"] for s in chosen)
    sources = sorted({s["source"] for s in chosen})
    vantages = sorted({v for p in ordered for v in pit_vantage[p]})
    losses = [s["capture_loss_pct_max"] for s in chosen if s["capture_loss_pct_max"] is not None]
    pit = {
        "pit_id": merged_pit_id(ordered, axis, as_of),
        "name": merged_pit_id(ordered, axis, as_of),
        "source": sources[0] if len(sources) == 1 else " + ".join(sources),
        "as_of": as_of,
        "observed_from": min(s["observed_from"] for s in chosen),
        "observed_to": max(s["observed_to"] for s in chosen),
        # Across taps the same windows are seen twice, so the combined view still covers `max`
        # windows; across disjoint spans they add. The denominator of every occupancy figure in
        # this PIT, so getting it wrong would rescale `windows_seen` for every host.
        "windows": (max(s["windows"] for s in chosen) if axis == "vantage"
                    else sum(s["windows"] for s in chosen)),
        "schema_version": SCHEMA_VERSION,
        # The MERGING build, not the materializing one. A merged PIT is a new artifact produced
        # here and now; attributing it to whichever build wrote an input would misreport which
        # code produced these rows (`LES-113` is about exactly this kind of provenance).
        **_build(),
        "capture_loss_pct_max": max(losses) if losses else None,
        "entities": len(entities), "segments": len(segments),
        "vantage": None,           # a merged PIT is not one tap; `vantages` is the honest column
        "network": chosen[0].get("network"),
        "vantages": json.dumps(vantages) if vantages else None,
        "merge_axis": axis,
        "merged_from": json.dumps(ordered),
    }
    out = {"ir_pit": [pit], "ir_entity": entities, "ir_segment": segments,
           "ir_edge": edges, "ir_identity": identity, "ir_unseen": unseen}
    for rows in out.values():
        for row in rows:
            row["pit_id"] = pit["pit_id"]
    out["_stats"] = {
        "axis": axis, "merged_from": ordered,
        "entities": len(entities), "segments": len(segments), "edges": len(edges),
        "unseen_hosts": len(unseen),
        # What the combination actually bought, which is the question multi-tap is asked to
        # answer: hosts only one contributor saw, and hosts a contributor had listed as unseen
        # that another one observed.
        "entities_from_one_vantage_only": sum(
            1 for e in entities if len(_json_list(e["vantages"])) == 1) if axis == "vantage" else None,
        "unseen_resolved_by_merge": unseen_resolved,
    }
    return out


def _build() -> dict:
    from aximprint.provenance import build_stamp
    stamp = build_stamp()
    return {"aximprint_version": stamp.get("aximprint_version"),
            "code_fingerprint": stamp.get("code_fingerprint"),
            "git_commit": stamp.get("git_commit")}
