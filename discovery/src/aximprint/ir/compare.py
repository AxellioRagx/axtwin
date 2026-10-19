"""Structural drift between two PITs (`docs/WORK_ORDER_network_ir.md` D8, build step 5).

**This is the step that tests whether D4 and D4a were designed right.** Everything before it wrote
fields; this is the first thing that has to answer a question with them, and the question is the
one the whole artifact exists for: *what changed, and how much of that is real?*

Three properties, in order of how easy they are to get wrong:

  * **The IR records presence; this classifies absence** (D4). An entity missing from the later PIT
    is not "gone" — it is *observed absent* if its segment was well covered and *not observed* if it
    was not. An IR row could never make that call, because it cannot see a future PIT.
  * **It refuses** (D4a.4). Two PITs over wildly different observation windows are not comparable,
    and a comparator that always answers is not measuring anything.
  * **It partitions by locality** (schema `1.1`). Internet peers churn constantly — CDN addresses
    rotate for reasons that have nothing to do with the monitored network — so itemizing them would
    bury the signal under noise that is not drift. They are counted, never listed, and the count is
    published so nobody has to wonder whether they were dropped.

D8 puts this in aximprint: it owns the temporal store, the resolver, and the coverage metadata the
classification depends on.
"""
from __future__ import annotations

from typing import Optional

from aximprint.ir.schema import parse_version, vantage_set

#: A segment must be observed in at least this fraction of the later PIT's windows before a host's
#: absence from it counts as evidence rather than as blindness (D4a).
#:
#: **What `observed_absent` does and does not claim.** It says: the sensor saw this segment, and
#: this host was not in it. It does **not** say the host is gone — a host that talks once a day
#: will be absent from a three-hour PIT whose coverage is perfect. Evidence of absence, never proof
#: of it, and the ratio is published beside every verdict so a consumer can re-decide with a
#: different floor rather than inherit this one.
#:
#: Half is a judgment, not a measurement. Below it, "the host did not appear" and "we were not
#: really watching" become hard to tell apart, which is the exact confusion D4 exists to prevent.
COVERAGE_FLOOR = 0.5

#: Two PITs whose observation windows differ by more than this factor are refused (D4a.4).
#:
#: Run-length and presence statistics are meaningless across a 3-hour PIT and a 71-hour one: nearly
#: every host will look "added" in the longer one for no reason but that there was more time to see
#: it. Measured on the 2026-08-11 baseline, the two PITs a naive workflow produces first — as-of the
#: first window versus as-of the last — differ by exactly 3x and are correctly refused.
MAX_DURATION_RATIO = 2.0

#: Absence verdicts. `None` is not among them: every removed entity gets one of these two, because
#: "we did not classify it" would be a third state nobody asked for.
ABSENCE_CLASSES = ("observed_absent", "not_observed")

#: Per-entity fields whose change is structural drift. Declared rather than diffed wholesale: most
#: columns move for reasons that are not drift (`windows_seen` differs because the PITs are
#: different lengths; `first_seen`/`last_seen` move with the window range; `source_entity` is
#: run-scoped by `Q1` D3 and is *expected* to differ between two runs of the same network).
#: Diffing every column would report all of that as change.
COMPARED_FIELDS = ("locality", "segment", "role")


class IncommensurableError(Exception):
    """The two PITs cannot honestly be compared. Refusing is the feature (D4a.4)."""


def _duration(pit: dict) -> float:
    return float(pit["observed_to"]) - float(pit["observed_from"])


def _check_same_view(a: dict, b: dict) -> None:
    """Same network, same set of taps — `docs/WORK_ORDER_visibility_and_vantage.md` D3/D4.

    **This replaces the source-equality refusal**, which had two failure modes and the second was
    the dangerous one: re-harvesting ONE tap into a new directory was falsely refused (the path
    changed, the network did not), and two DIFFERENT taps writing to the same path were falsely
    accepted, with their view difference reported as network drift.

    Network first. When both PITs declare one, that declaration decides and the source paths are
    irrelevant — which is exactly the re-harvest case. When either does not, there is nothing that
    could assert they describe one network, so the old source-equality rule still applies: it is
    conservative and wrong only in the direction of refusing too much.

    Vantage second, and it is an independent question. Two PITs of one network from different tap
    points are not comparable *as drift*, because everything one tap sees and the other does not
    would be reported as change. Same rule for a one-tap PIT against a two-tap merged one — that is
    a view change wearing drift's clothes. Two PITs that both record no vantage are permitted:
    nothing knows they differ, which is the state every capture was in before this existed, and
    refusing there would make the tool unusable on ordinary log directories.
    """
    net_a, net_b = a.get("network"), b.get("network")
    if net_a and net_b:
        if net_a != net_b:
            raise IncommensurableError(
                f"different networks ({net_a!r} vs {net_b!r}): these describe two networks, so "
                f"every difference between them is trivially 'drift'.")
    elif a["source"] != b["source"]:
        raise IncommensurableError(
            f"different sources ({a['source']!r} vs {b['source']!r}) and no common declared "
            f"network: these may describe two networks, or one network captured twice — nothing "
            f"here can tell. Re-ingest both with `--network NAME` if they are the same network.")
    van_a, van_b = vantage_set(a), vantage_set(b)
    if van_a != van_b:
        raise IncommensurableError(
            f"different vantages ({sorted(van_a) if van_a else None} vs "
            f"{sorted(van_b) if van_b else None}): a PIT is what one tap point saw, so everything "
            f"visible from one and not the other would be reported as network change. Merge the "
            f"PITs to a common vantage set first (`aximprint merge --axis vantage`), then compare "
            f"the merged views.")


def _check_commensurable(a: dict, b: dict) -> None:
    """Refuse pairs whose difference would be reported as drift when it is not.

    Deliberately **not** a caveat: each of these makes the whole report wrong rather than merely
    uncertain, and a report a consumer has to know to distrust is worse than no report.
    """
    _check_same_view(a, b)
    if parse_version(a["schema_version"])[0] != parse_version(b["schema_version"])[0]:
        raise IncommensurableError(
            f"different schema MAJOR ({a['schema_version']} vs {b['schema_version']}): a "
            f"subtractive change may have removed a field this comparison depends on.")
    if a["as_of"] == b["as_of"]:
        raise IncommensurableError(
            f"both PITs are as-of {a['as_of']}: a PIT compared with itself has no direction, and "
            f"two different PITs at one instant describe the same moment twice.")
    da, db = _duration(a), _duration(b)
    if da <= 0 or db <= 0:
        raise IncommensurableError("a PIT with a zero-length observation window cannot be compared")
    ratio = max(da, db) / min(da, db)
    if ratio > MAX_DURATION_RATIO:
        raise IncommensurableError(
            f"observation windows differ by {ratio:.1f}x ({da / 3600:.1f}h vs {db / 3600:.1f}h), "
            f"beyond the {MAX_DURATION_RATIO}x limit: nearly every host would read as 'added' in "
            f"the longer PIT for no reason but that there was more time to see it. Materialize "
            f"two PITs over comparable spans.")


def _caveats(a: dict, b: dict, rows_b: list[dict]) -> list[str]:
    """Things that weaken the report without invalidating it.

    The split from `_check_commensurable` is the point: a caveat says *read this with care*, a
    refusal says *this report would be false*. Collapsing them in either direction is a mistake —
    refusing on a caveat makes the tool unusable, and downgrading a refusal to a caveat produces
    confident nonsense.
    """
    out = []
    if a["code_fingerprint"] != b["code_fingerprint"]:
        # `LES-113`: entity keys are the resolver's parsed computer name, so a change to that
        # normalization silently re-keys every host. Both PITs stay schema-valid and simply stop
        # joining. This is the only signal that it may have happened.
        out.append(
            f"produced by different builds ({a['code_fingerprint']} vs {b['code_fingerprint']}): "
            f"entity keys derive from the resolver's normalization, so a change to it would make "
            f"hosts appear to be replaced rather than persist (`LES-113`).")
    weak = sum(1 for r in rows_b if r["key_kind"] == "address")
    if rows_b and weak:
        out.append(
            f"{weak} of {len(rows_b)} local entities join on an `address` key, which asserts only "
            f"that the address did not change hands. On a DHCP network these joins are the least "
            f"trustworthy part of this report.")
    for pit in (a, b):
        if pit["capture_loss_pct_max"] is None:
            out.append(f"PIT {pit['pit_id']} has no capture-loss figure: nothing audited it, "
                       f"which is not the same as it being clean (`LES-006`).")
        elif pit["capture_loss_pct_max"] > 1.0:
            out.append(f"PIT {pit['pit_id']} reports up to "
                       f"{pit['capture_loss_pct_max']:.2f}% capture loss.")
    return out


def _classify_absence(row: dict, coverage: dict[str, tuple[int, int]]) -> dict:
    """Why is this entity missing from the later PIT — real change, or blindness? (D4)

    Keyed on the segment the entity was in *when it was last seen*, since that is the only place
    the later PIT could have found it. An entity with no segment — an internet peer, or a local
    host on nothing provisionable — gets `not_observed`, and that is honest rather than lazy:
    there is no coverage figure for "the internet", so no claim about absence from it is
    supportable.
    """
    segment = row["segment"]
    present, total = coverage.get(segment, (0, 0)) if segment else (0, 0)
    ratio = (present / total) if total else None
    covered = ratio is not None and ratio >= COVERAGE_FLOOR
    return {
        "entity_key": row["entity_key"], "key_kind": row["key_kind"],
        "segment": segment, "role": row["role"],
        "classification": "observed_absent" if covered else "not_observed",
        "segment_coverage": round(ratio, 4) if ratio is not None else None,
    }


def compare(ir_store, from_pit: str, to_pit: str) -> dict:
    """Structural drift from `from_pit` to `to_pit`.

    Raises `IncommensurableError` when the pair cannot honestly be compared. Returns a report
    whose every list is sorted, so two runs of the comparator over the same PITs agree byte for
    byte — a drift report that varied would be indistinguishable from drift.
    """
    summaries = {s["pit_id"]: s for s in ir_store.summary()}
    for pit_id in (from_pit, to_pit):
        if pit_id not in summaries:
            raise IncommensurableError(f"no PIT {pit_id!r} in this store")
    a, b = summaries[from_pit], summaries[to_pit]
    _check_commensurable(a, b)

    raw_a, raw_b = ir_store.read_pit(from_pit), ir_store.read_pit(to_pit)
    # Coverage of the LATER PIT: the question is whether it could have seen what it did not.
    coverage = {s["cidr"]: (s["windows_present"], s["windows_total"]) for s in raw_b["ir_segment"]}

    def _local(rows):
        return {r["entity_key"]: r for r in rows if r["locality"] == "local"}

    local_a, local_b = _local(raw_a["ir_entity"]), _local(raw_b["ir_entity"])
    ext_a = {r["entity_key"] for r in raw_a["ir_entity"] if r["locality"] != "local"}
    ext_b = {r["entity_key"] for r in raw_b["ir_entity"] if r["locality"] != "local"}

    added = sorted(set(local_b) - set(local_a))
    removed = sorted(set(local_a) - set(local_b))
    changed = []
    for key in sorted(set(local_a) & set(local_b)):
        deltas = {f: {"from": local_a[key][f], "to": local_b[key][f]}
                  for f in COMPARED_FIELDS if local_a[key][f] != local_b[key][f]}
        if deltas:
            changed.append({"entity_key": key, "key_kind": local_b[key]["key_kind"],
                            "changes": deltas})

    seg_a = {s["cidr"]: s for s in raw_a["ir_segment"]}
    seg_b = {s["cidr"]: s for s in raw_b["ir_segment"]}

    def _edges(raw):
        return {(e["src_key"], e["dst_key"], e["proto"]) for e in raw["ir_edge"]}

    edges_a, edges_b = _edges(raw_a), _edges(raw_b)

    def _pit_block(pit):
        # `vantages`, resolved through the scalar/set pair, so a reader of the report can see
        # WHICH view each side is — the fact whose absence made two different taps compare
        # silently before D1.
        return {**{k: pit[k] for k in ("pit_id", "as_of", "observed_from", "observed_to",
                                       "windows")},
                "network": pit.get("network"),
                "vantages": sorted(vantage_set(pit) or ()) or None}

    return {
        "from": _pit_block(a),
        "to": _pit_block(b),
        "commensurable": True,
        "caveats": _caveats(a, b, list(local_b.values())),
        "entities": {
            "added": [{"entity_key": k, "key_kind": local_b[k]["key_kind"],
                       "segment": local_b[k]["segment"], "role": local_b[k]["role"]}
                      for k in added],
            "removed": [_classify_absence(local_a[k], coverage) for k in removed],
            "changed": changed,
            "unchanged": len(set(local_a) & set(local_b)) - len(changed),
        },
        "segments": {
            "added": sorted(set(seg_b) - set(seg_a)),
            "removed": sorted(set(seg_a) - set(seg_b)),
            "population_changed": [
                {"cidr": c, "from": seg_a[c]["entities"], "to": seg_b[c]["entities"]}
                for c in sorted(set(seg_a) & set(seg_b))
                if seg_a[c]["entities"] != seg_b[c]["entities"]],
        },
        "edges": {"added": len(edges_b - edges_a), "removed": len(edges_a - edges_b),
                  "unchanged": len(edges_a & edges_b)},
        # Counted, never itemized — and the count is published precisely so nobody has to wonder
        # whether they were silently dropped. CDN and cloud addresses rotate for reasons that have
        # nothing to do with the monitored network; listing them would bury the signal.
        "external_peers": {"added": len(ext_b - ext_a), "removed": len(ext_a - ext_b),
                           "unchanged": len(ext_a & ext_b),
                           "note": "counted, not itemized: external churn is not network drift"},
    }
