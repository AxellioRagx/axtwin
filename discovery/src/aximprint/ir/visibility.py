"""What this vantage saw, and what it knows it missed (`WORK_ORDER_visibility_and_vantage.md` D7).

Build step 7. **The inputs to DESIGN_BRIEF §8's visibility model, with no inference at all** — §8's
own actionable output, a *tap-placement recommendation*, is explicitly out of scope here and would
be a report over this one.

Two things, and neither is new evidence:

  * **per-segment coverage**, already recorded per PIT since D4a and used until now only as an
    absence gate inside `compare`. Read as a visibility signal it answers *"how much of this
    segment did we actually see"* — and reading it that way costs nothing, which is exactly why D7
    calls it minimal.
  * **the referenced-but-unseen census** (`ir_unseen`), which answers *"who do we know we missed"*.

Ordered worst-first, because the useful end of a coverage report is the badly-covered end. Every
figure is `inferred_from_traffic` today and says so: Zeek reports no per-segment loss, so coverage
here is a proxy and must never be read as a measurement (`LES-006`).

**Nothing here filters, scores or recommends.** A `visibility` report that quietly dropped a
segment would be the same failure as a twin that quietly drops a host.
"""
from __future__ import annotations

import json

from aximprint.ir.compare import COVERAGE_FLOOR
from aximprint.ir.schema import vantage_set


def report(ir_store, pit_id: str) -> dict:
    """The visibility report for one PIT. Raises `KeyError` if the store has no such PIT."""
    summaries = {s["pit_id"]: s for s in ir_store.summary()}
    if pit_id not in summaries:
        raise KeyError(f"no PIT {pit_id!r} in this store")
    pit = summaries[pit_id]
    raw = ir_store.read_pit(pit_id)

    segments = []
    for row in raw["ir_segment"]:
        total = row["windows_total"] or 0
        ratio = (row["windows_present"] / total) if total else None
        segments.append({
            "cidr": row["cidr"], "name": row["name"], "entities": row["entities"],
            "windows_present": row["windows_present"], "windows_total": row["windows_total"],
            # `None`, never 0.0, when there is no denominator: a segment nothing could measure and
            # a segment measured at zero are different findings.
            "coverage": round(ratio, 4) if ratio is not None else None,
            "coverage_source": row["coverage_source"],
            # The same floor `compare` classifies absence with, published here so the two agree by
            # construction and an operator can see which segments are about to make absence
            # uninterpretable.
            "below_floor": ratio is not None and ratio < COVERAGE_FLOOR,
        })
    # Worst first; `cidr` breaks ties so the order is a function of content alone (`LES-002`).
    segments.sort(key=lambda s: (s["coverage"] if s["coverage"] is not None else -1.0, s["cidr"]))

    unseen = raw.get("ir_unseen", [])
    by_locality: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in unseen:
        key = row["locality"] or "unjudged"
        by_locality[key] = by_locality.get(key, 0) + 1
        by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + 1
    # Itemized only where it is actionable. A host referenced but unseen INSIDE a segment this
    # vantage watches is the blind spot proper — something in the estate this tap cannot see. The
    # rest of the census is mostly the internet (2,409 of 2,418 on the 2026-08-11 baseline), and
    # listing every CDN address a resolver ever answered with would bury the nine that matter.
    inside = sorted((r for r in unseen if r["segment"]), key=lambda r: r["address"])
    return {
        "pit": {"pit_id": pit_id, "source": pit["source"], "network": pit.get("network"),
                "vantages": sorted(vantage_set(pit) or ()) or None,
                "windows": pit["windows"],
                "observed_from": pit["observed_from"], "observed_to": pit["observed_to"],
                "capture_loss_pct_max": pit["capture_loss_pct_max"]},
        "segments": segments,
        "segments_below_floor": sum(1 for s in segments if s["below_floor"]),
        "unseen": {
            "total": len(unseen),
            "by_locality": dict(sorted(by_locality.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "in_observed_segments": [
                {"address": r["address"], "segment": r["segment"],
                 "names": json.loads(r["identifiers"]) if isinstance(r["identifiers"], str)
                 else r["identifiers"],
                 "observations": r["observations"]}
                for r in inside],
            "note": ("hosts referenced by DNS and never observed in a flow at this vantage. "
                     "Only those inside a segment this PIT observed are itemized; the rest are "
                     "counted, being predominantly internet addresses."),
        },
        "note": ("visibility inputs only. A tap-placement recommendation (DESIGN_BRIEF §8's "
                 "actionable output) is a report OVER this one and is deliberately not made here."),
    }
