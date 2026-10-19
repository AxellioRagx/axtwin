"""Sensor-neutral ingest machinery every plugin needs and no plugin owns.

Three things live here, all of them previously duplicated across two plugins:

  `reorder_by_key`  the bounded, horizon-in-seconds reorder buffer (`LES-004`, `Q2`)
  `in_order`        its `Event`-level wrapper, which turns an unrepaired displacement
                    into a `SensorHealthEvent(kind="ingest_unordered")`
  `is_ip`           "is this string a real address" — parse, never pattern-match
                    (`LES-008`)

**Why they moved here rather than being imported across plugins.** `reorder_by_key`
was `ingest/zeek/sources.py`'s, and `ingest/suricata/sources.py` imported it from
there — a same-layer edge CONVENTIONS §3's downward-only rule does not forbid, but one
that makes `suricata` depend on `zeek` staying importable for a helper that is about
*time*, not about Zeek. `provenance.py`/`vantage.py` set the precedent this follows: a
format/mechanism module sits BELOW the layers that use it rather than inside whichever
one happened to write it first. `ingest/zeek/sources.py` re-exports `reorder_by_key`,
so `zs.reorder_by_key` and `zeek/plugin.py`'s own import keep resolving unchanged.

**What did NOT move: the horizon and row-cap CONSTANTS.** Zeek's
`REORDER_HORIZON_SECONDS = 600.0` is defensible because a *Zeek* `conn.log` on a *real*
capture was measured to displace rows by up to 169.6 s. That is a measurement of one
sensor's write behavior, not a property of time, and hoisting it here would turn one
plugin's evidence into an unmarked constant every other plugin inherits without ever
measuring its own (`LES-119`). Each plugin therefore declares its own pair, beside its
own note on where the number came from — including "not measured yet", which is a
disclosure this module must not let a shared constant hide.

Imports `events` only (downward, CONVENTIONS §3): `in_order` constructs a
`SensorHealthEvent`, which is exactly why `reorder_by_key` itself stays ignorant of the
event model — it is also used one layer lower, over raw dict rows, by
`ingest/zeek/sources.py::LogDirSource`.
"""
from __future__ import annotations

import heapq
import ipaddress
from collections.abc import Callable, Iterable, Iterator
from typing import Protocol

from aximprint.events import Event, SensorHealthEvent


class ReorderStats(Protocol):
    """What `reorder_by_key` needs from its `stats` argument: two int counters, both
    incremented via `+=`. Structural only — nothing here imports the dataclasses that
    satisfy it (`ingest/zeek/plugin.py::_LogStats`, `ingest/suricata/plugin.py
    ::_FileStats`), which would be the upward edge CONVENTIONS §3 forbids.
    """
    unordered: int
    capped: int


def reorder_by_key(items: Iterable, key: Callable[[object], float | None], *,
                   horizon: float, max_rows: int,
                   stats: ReorderStats) -> Iterator[tuple]:
    """Smooth an approximately-`key`-ordered stream into a non-decreasing one.

    Generalizes what was `ingest/zeek/plugin.py`'s private `_in_order` (2026-08-03) so
    the identical shape can run one layer lower, over raw rows instead of parsed
    `Event`s — `key` is what decouples the two: `in_order` (below) passes an event's
    `.ts`, `LogDirSource` passes a row's parsed `ts` (`_row_ts`).

    Yields `(item, displacement)` pairs. `displacement` is `None` when the item
    emerges at or after the running max seen so far — because it always was, or
    because the buffer put it back there — and the number of `key`-units it
    trailed the running max by otherwise. This function does not decide what a
    non-`None` displacement *means*: `in_order` turns it into a `SensorHealthEvent`;
    nothing here constructs one, so this stays usable over raw rows.

    The buffer holds an item until it is `horizon` `key`-units behind the
    largest key seen, which is what makes the guarantee "disorder narrower than
    `horizon` is repaired" hold at *any* item rate (`LES-004`) — `max_rows` is a
    memory backstop only, and a cap-forced flush still counts against `stats`
    even though the horizon was not actually honored for that item (`LES-004`'s
    "count it and disclose it as a health fact, never as a debug line").

    `key(item)` returning `None` marks an item this function cannot order at
    all (a row whose own `ts` failed to parse): it is yielded immediately,
    unmodified, with a `None` displacement, and never touches the running max —
    one unparseable item must not make everything already buffered look
    catastrophically displaced, and must not mask genuine disorder in the
    orderable items around it. The item still reaches its own parser unchanged;
    that parser's own `if not ts` guard is what actually discards it.

    Ties among equal keys break on arrival position within *this* call — the
    same `(key, seq)` heap ordering this has always used — so this function's
    own output is deterministic; a caller merging several such outputs still
    owes them a deterministic relative order of their own (`heapq.merge`'s
    argument order), which is `LES-009`'s concern, not this one's.

    `stats` needs only `.unordered` and `.capped` (`ReorderStats`) — a caller
    that wants this function's REORDERING without its full COUNTING (because a
    stream it already owns is the sole place `unordered` is reported, e.g.
    `LogDirSource._merged_rows`) passes a purpose-built sink instead of the
    shared stats object directly, so the same underlying disorder is never
    counted twice (see `ingest/zeek/sources.py::_PerFileTally`, which also shows
    `capped` does NOT have this problem and should not be silenced the same way).
    """
    heap: list[tuple[float, int, object]] = []
    push, pop = heapq.heappush, heapq.heappop
    seq = 0
    last = float("-inf")
    newest = float("-inf")

    def flush() -> Iterator[tuple]:
        nonlocal last
        k, _, it = pop(heap)
        if k < last:
            stats.unordered += 1
            yield it, last - k
        else:
            last = k
            yield it, None

    for it in items:
        k = key(it)
        if k is None:
            yield it, None
            continue
        push(heap, (k, seq, it))
        seq += 1
        newest = max(newest, k)
        while heap:
            aged_out = heap[0][0] < newest - horizon
            if not aged_out and len(heap) <= max_rows:
                break
            if not aged_out:
                stats.capped += 1  # memory, not the horizon, is deciding this one
            yield from flush()
    while heap:
        yield from flush()


def event_ts(ev: object) -> float:
    """`Event.ts` as a plain function — `in_order`'s own `key`, and the `heapq.merge`
    key a plugin merging several already-ordered event streams needs. Public and shared
    because both plugins had their own one-line copy, and a merge key is exactly the
    kind of thing that must be ONE function (`LES-009`: the tie-break is part of the
    output's determinism, not an implementation detail of whoever merges).
    """
    return ev.ts  # type: ignore[attr-defined]


def in_order(events: Iterable, label: str, horizon: float, max_rows: int,
             stats: ReorderStats) -> Iterator[Event]:
    """Yield one stream's events in non-decreasing `ts`, announcing what it could not
    repair — the `Event`-level wrapper over `reorder_by_key`.

    A sensor's log is only *approximately* ts-sorted: a flow record is written when the
    flow CLOSES but keyed on when it STARTED, so a long flow surfaces after shorter
    ones that began later. Both sensors this project reads have that pathology (Zeek's
    `conn.log`, Suricata's `flow` event_type), which is why this wrapper is here and not
    in either plugin.

    Disorder wider than `horizon` cannot be repaired — each unrepairable event is
    counted AND announced as a `SensorHealthEvent(kind="ingest_unordered")` carrying the
    displacement, at the SAME ts as the event it qualifies (so it lands in that event's
    own window and the features built from it carry the asterisk, DESIGN_BRIEF §5.4 #5)
    rather than reading clean. `detail` is the caller's own `label` for the stream: a
    Zeek stem, a Suricata file path — diagnostics, never a feature.
    """
    for ev, displacement in reorder_by_key(events, event_ts, horizon=horizon,
                                           max_rows=max_rows, stats=stats):
        if displacement is not None:
            yield SensorHealthEvent(ts=ev.ts, kind="ingest_unordered",
                                    value=displacement, detail=label)
        yield ev


def is_ip(value: str) -> bool:
    """True only for a real IPv4/IPv6 literal.

    Replaces (in `ingest/zeek/plugin.py`, 2026-08-04) a `count(".") == 3` shape
    heuristic that treated any four-label DNS name as an address: on the reference
    capture 34.4% of dns-derived bindings were a name bound as an `ip`, each reaching
    the store as an `observed` assertion. A binding is an identity claim — parse it,
    never guess at it (`LES-008`, fail closed).
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
