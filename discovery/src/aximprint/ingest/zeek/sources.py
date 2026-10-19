"""Zeek transports (INCUBATING): where records come from, not what they mean.

One seam, drawn deliberately: a **source** locates a sensor's records and decodes
them into dict rows; the **plugin** (`plugin.py`) decides what those rows *mean*.
Sources know nothing about `_PARSERS`, the event model, or which log types matter
— they are handed the columns to project and hand back rows. That is why adding a
second transport costs no change to a parser, and why a parser fix costs no change
to a transport.

  LogDirSource         a directory of Zeek logs, TSV or JSON-lines, optionally
                       gzipped — **two shapes, auto-detected**. A *flat* directory
                       (files directly inside it, at any depth via a recursive
                       walk) is one stream per FILE, so a rotated conn.log is
                       several streams that merge — unchanged since this
                       source's first version. A *window tree* (root holds only
                       subdirectories, none of them log files themselves — the
                       shape `xtract harvest`'s cold tier writes, one directory
                       per pcap window) is one stream **per log stem**, mirroring
                       ParquetArchiveSource below: every window directory's file
                       for that stem is a sub-stream, reorder-buffered on its own
                       and then merged with its siblings on each row's own `ts`
                       — never on the window directory's name. Naming *looks*
                       chronological (`window_id` is an epoch-seconds string) but
                       is not evidence of it: the producer can write a
                       late-closing flow into a *later* window's directory than
                       the one its `ts` belongs to, so a directory-name sort is
                       exactly the mistake `LES-013` names — it derives an order,
                       it does not repair one.
  ParquetArchiveSource the metadata archive specified in
                       `docs/SPEC_zeek_metadata_archive.md` — written once by an
                       external converter, read many times here. One stream per
                       LOG TYPE. Each partition FILE is individually exactly
                       sorted (checked at open: `sorted_by == "ts"`), but that
                       does **not** make date order ts order across an archive
                       that grows incrementally: `OVERWRITE_OR_IGNORE` lets a
                       later conversion batch add a `part-NNN.parquet` to an
                       *earlier* date's directory (the same late-closing-flow
                       fact as above, one layer down), so `_read` merges parts on
                       their rows' own `ts` rather than concatenating them in
                       path order.

**Streams are the unit of reorder buffering**, so their count is a memory axis:
peak is O(streams x reorder_rows) (`LES-010`). The Parquet source deliberately
emits one stream per log type rather than one per partition file for exactly this
reason — 296 date partitions x 16 log types would be 4,736 buffers.

`LogDirSource`'s window-tree mode applies the same reasoning to the STREAM COUNT
`plugin.py`'s own top-level merge holds open — one stream per stem, not one per
(window, stem) pair — but consolidating to one `ZeekStream` per stem does **not**
by itself bound memory (`Q18`, closed): the buffers `plugin.py` no longer sees
still exist, one per window directory, INSIDE that one stream, and
`heapq.merge`'s own initialization primes every one of them (pulls their first
item) regardless of how many there are. Measured: 670 MB at 300 window
directories x 2,000 rows each, because a pcap window's own `ts` span typically
sits inside the 600 s horizon, so nothing ever ages a buffer out — only
end-of-file does, meaning every inner buffer swallows its whole file before
`heapq.merge` can even begin comparing heads. So `LogDirSource._merged_rows`
gives the STEM an **aggregate** row budget instead of handing `reorder_rows` to
every sub-stream — `max(reorder_rows // len(paths), 64)` each — which bounds the
same corpus to 39 MB at the cost of a real, disclosed repair-quality
degradation: with enough window directories, the per-substream cap binds before
the 600 s horizon can, and disorder that would have been silently repaired
before now surfaces as a counted, reported cap-forced flush instead
(`source_capped`, `M2`) — never as silently-widened memory.

Discovery order is sorted and therefore reproducible. The merge in `plugin.py`
breaks ts ties on stream position, so a non-deterministic discovery order would
reach an output byte (`LES-009`) — and the same discipline applies one layer
down, inside `LogDirSource._window_tree_streams` and
`ParquetArchiveSource._read`, wherever *this* module now does its own k-way
merge across sub-streams instead of handing many streams up to `plugin.py`.

`reorder_by_key` below is that one shared piece of machinery: a bounded,
horizon-in-seconds reorder buffer generalized over any item and any float `key`
— the same shape `plugin.py`'s per-stream smoothing has always used (`LES-004`),
factored out so a second, independent merge implementation is never written
alongside it (`Q2`).
"""
from __future__ import annotations

import gzip
import heapq
import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional, Protocol

from aximprint.ingest.reorder import (  # re-exported: see below
    ReorderStats, reorder_by_key,
)
from aximprint.vantage import Vantage, read_vantage

#: Rows pulled from DuckDB per round trip, including inside `ParquetArchiveSource
#: ._read`'s cross-partition merge. One constant, not two (`S1`): a swept
#: comparison on a 400-partition archive found the dominant memory term is
#: DuckDB's own per-cursor state (~175-215 KB/cursor — 1200 simultaneous cursors
#: at 4 stems x 300 partitions is +68 MB on its own), which a `fetchmany` batch
#: size does not touch either way — and a SMALLER batch measured MORE memory and
#: 39x the round trips (239-303 MB across 32..10000, non-monotonic, i.e. noise
#: around the dominant term) rather than less. A separate, smaller
#: "MERGE_FETCH_BATCH" existed here through the round that first wrote this
#: file's k-way merge, reasoned from "heapq.merge primes every argument at once"
#: without being measured — the `LES-019` shape (a confident memory story that
#: turned out not to be the one that mattered) — and was removed once it was.
FETCH_BATCH = 10_000

MANIFEST = "manifest.json"
PARQUET_DIR = "parquet"

#: How far back in time a reorder buffer can repair disorder, and the row-count
#: memory backstop for that buffer — moved here (from `plugin.py`) because
#: `LogDirSource`'s window-tree mode now needs the identical bound one layer
#: lower than the plugin, to reorder-buffer each window's file *before* the
#: cross-window merge; re-exported by `plugin.py` so `zp.REORDER_ROWS` etc. keep
#: resolving. Values and reasoning are `plugin.py`'s original: on the reference
#: capture the worst measured displacement is 169.6 s of ts (~3.5x headroom
#: under the 600 s horizon), and REORDER_ROWS is a memory backstop only — see
#: `reorder_by_key`'s docstring for what "backstop, not the guarantee" means.
REORDER_HORIZON_SECONDS = 600.0
REORDER_ROWS = 8192


# -- the counting surface this module needs from a caller's stats object --------
#
# `plugin.py`'s `_LogStats` dataclass is the one real thing that ever satisfies
# these, but nothing here imports it (`S6`): a `TYPE_CHECKING`-only forward
# reference used to stand in for the type, which is exactly the upward edge
# CONVENTIONS §3 forbids on a real import and still shouldn't exist as a
# type-only one — `_LogStats` satisfies each `Protocol` below PURELY
# STRUCTURALLY (it has the right attributes; nothing declares it as one), so
# the reference to the upper layer disappears rather than being papered over.
# Segregated by what each function actually touches, not lumped into one:

class _RowStats(Protocol):
    """What `_rows()` needs from its `stats` argument: one int counter,
    incremented via `+=`."""
    malformed: int


#: What `reorder_by_key` needs from its `stats` argument, hoisted with the function
#: itself to `ingest/reorder.py` (code review `S1`) and aliased here under this
#: module's own private name so every existing annotation below keeps resolving.
_ReorderStats = ReorderStats


class _StreamStats(_RowStats, Protocol):
    """What a `ZeekStream.rows` factory's `stats` argument satisfies in
    practice — `_RowStats`, plus `source_capped` (`M2`): the channel
    `LogDirSource._merged_rows`'s inner, per-file reorder pass uses to
    disclose its own cap-forced flushes without also double-reporting
    `unordered` through the same real object (see `_PerFileTally`)."""
    source_capped: int


@dataclass
class ZeekStream:
    """One independently-ordered record stream, tagged with its Zeek log type.

    `rows` is a factory rather than an iterator so that discovery stays cheap and
    nothing is read until the merge actually pulls from this stream — the
    streaming property the ingest layer is built on (`Q2`).
    """
    stem: str
    label: str                                     # diagnostics only; never a feature
    rows: Callable[[_StreamStats], Iterator[dict]]


class ZeekSource(ABC):
    """Contract: discover record streams, and say what was found but not read."""

    name: str = ""

    #: what one inventory unit is, for reporting. A count with the wrong noun
    #: reads as a fact about the wrong thing.
    unit: str = "stream"

    #: stems present in the source, mapped to how many stream units carry them.
    #: The plugin decides which are parsed, skipped by name, or unrecognized —
    #: a source does not know the difference.
    inventory: dict[str, int]

    #: Whether `inventory` is a COMPLETE census of every stem the underlying capture
    #: actually produced (`True`), or only of whatever subset this transport's own
    #: discovery mechanism happens to be ABLE to see (`False`) — the source's own
    #: honest answer to "did I look at everything, or only at what I already know how
    #: to represent". This is what `ingest/zeek/plugin.py::events` reads to decide
    #: whether `unparsed_stems` may be set to a real dict at all, or must stay `None`
    #: (unaudited) instead of asserting a false "everything accounted for" the moment
    #: a transport cannot actually back that claim (`docs/WORK_ORDER_zeek_stem_
    #: coverage.md` D5: "a field that cannot say 'I did not look' reproduces the D5
    #: gap in a quieter form" — this field exists so that gap cannot reopen one layer
    #: down, inside a transport rather than inside the plugin).
    #:
    #: `False` BY DEFAULT (code review `M1`, reversing this field's own first
    #: landing) — the SAME direction `inventory` itself takes one line up, just
    #: expressed as a safe default instead of a no-default annotation: `inventory`
    #: has no genuinely safe default value to fall back to (an empty dict would be
    #: just as misleading as a wrong one), so it fails loud via `AttributeError` on
    #: a source that forgets it; `inventory_is_complete` DOES have a safe default —
    #: "I do not know" — so a transport author who never thinks about this field
    #: inherits the HONEST answer instead of a silent, false "I audited everything
    #: and it is all accounted for", which is the exact clean bill this field
    #: exists to refuse. A transport whose discovery mechanism genuinely IS a
    #: complete census (walking a filesystem, e.g.) must say so explicitly — see
    #: `LogDirSource`, which sets this PER INSTANCE inside `streams()` rather than
    #: declaring one blanket class-level value, because completeness varies WITHIN
    #: that one class depending on which locator shape `streams()` finds (a single
    #: log file cannot be a complete census of a whole directory's stems, even
    #: though a flat directory or a window tree can be).
    inventory_is_complete: bool = False

    @abstractmethod
    def streams(self, columns: dict[str, list[str]]) -> list[ZeekStream]:
        """Discover streams, in a deterministic order.

        `columns` maps log stem -> the columns the caller will actually read, so a
        columnar transport can project instead of materializing whole records. A
        transport that cannot project is free to ignore it.
        """

    def notes(self) -> list[str]:
        """Lines to report once before reading. Never reaches a feature."""
        return []


# -- shared k-way merge machinery ------------------------------------------------
#
# One reorder buffer, one bounded-disorder guarantee, used at two layers: here,
# to merge a stem's several window-directory files into one stream, and in
# `plugin.py`, to merge a plugin's several discovered streams into one event
# stream. Factored out so the second use is a call, not a rewrite (`Q2`).

# `reorder_by_key` itself now lives in `ingest/reorder.py` (code review `S1`): it is
# about TIME, not about Zeek, and `ingest/suricata/sources.py` had been importing it
# from here — a same-layer edge that made one plugin depend on another staying
# importable. Re-exported under this module's own name (`from .sources import
# reorder_by_key` in `plugin.py`, `zs.reorder_by_key` in the tests) so nothing that
# already reads it had to change. The horizon/row CONSTANTS above deliberately did NOT
# move: 600 s is defensible because a Zeek `conn.log` was measured to displace rows by
# up to 169.6 s, which is a fact about this sensor, not about time (`LES-119`).


class _PerFileTally:
    """The counting sink `LogDirSource._merged_rows`'s inner, per-file
    `reorder_by_key` pass writes to — and the two counters it exposes are
    treated DIFFERENTLY on purpose (`M2`), not both discarded the way a first
    draft of this class did (nothing here is fully "discarded"; only
    `unordered`'s READING is, hence this name rather than that one).

    `unordered` stays purely LOCAL, read by nobody. The OUTER, per-stem
    `_in_order` pass (`plugin.py`, unchanged) re-judges the FULLY merged,
    cross-file sequence and is the sole place `stats.unordered` is decided for
    this transport — `heapq.merge` preserves each sub-stream's own internal
    order exactly, so the outer pass's re-judgment is a genuine second look at
    the same evidence, and a second, independent count here would double
    -report the same underlying displaced row (see `_merged_rows`'s own
    docstring, and `tests/test_zeek_ingest.py
    ::test_window_tree_disorder_reporting_is_exactly_once_and_lossless`).

    `capped` is different: it has NO second observer. `_LogStats.capped` (the
    OUTER pass's own count) is read in exactly one place,
    `ZeekPlugin._summary`'s stderr line, and that line only ever sees the
    outer pass's own cap-forced flushes — an inner-pass cap event has nothing
    downstream watching for it at all, so silencing it does not avoid a
    double-report, it just loses the fact. Every increment is therefore
    forwarded to the REAL, shared `_LogStats` object's `source_capped` field
    (a field named for what it means — *this window's own source file* forced
    an early flush — not for which pass counted it), so `LES-004`'s "count it
    and disclose it as a health fact, never as a debug line" still holds under
    `M1`'s aggregate budget, where cap-forced flushes stop being a rare edge
    case and become the normal way disorder gets traded for bounded memory.
    """
    __slots__ = ("_real", "unordered", "_capped")

    def __init__(self, real: _StreamStats) -> None:
        self._real = real
        self.unordered = 0
        self._capped = 0

    @property
    def capped(self) -> int:
        return self._capped

    @capped.setter
    def capped(self, value: int) -> None:
        # `reorder_by_key` only ever does `stats.capped += 1`, i.e. a read of
        # the CURRENT value followed by a write of `current + 1` — computing
        # the delta rather than assuming "+1" keeps this correct regardless of
        # how the caller increments, without `reorder_by_key` needing to know
        # this sink is not a plain counter.
        self._real.source_capped += value - self._capped
        self._capped = value


# -- text log directory ---------------------------------------------------------

_TEXT_SUFFIXES = (".log", ".gz")


def _is_log_file(p: Path) -> bool:
    """The one predicate for "this path is a Zeek text log", used everywhere
    that decides (`LogDirSource.streams`, `._window_tree_streams`) — written
    three times with the same literal before this round's fix round, plus a
    fourth, hand-inlined copy in `scripts/make_zeek_archive.py` (`S3`); one
    definition here, imported there, closes both at once.
    """
    return p.is_file() and (p.suffix in _TEXT_SUFFIXES or ".log" in p.name)


def _stem(p: Path) -> str:
    """The log TYPE a file belongs to, e.g. `conn.log` / `conn.gz` /
    `conn.05:00:00-06:00:00.log` -> `"conn"`. One definition (`S3`), reused
    by both `LogDirSource` modes and by `scripts/make_zeek_archive.py`.
    """
    return p.name.split(".")[0]


def _rows(path: Path, stats: Optional[_RowStats] = None) -> Iterator[dict]:
    """Yield dict rows from one zeek log file, TSV or JSON-lines, optionally gzipped.

    The file's header declares its own vocabulary and all of it is read per file,
    never assumed: `#separator`, plus the `#unset_field` / `#empty_field` markers,
    which are normalized to `""` so the parsers see one absence vocabulary
    (`_UNSET`). Hardcoding Zeek's defaults leaks the literal marker through as a
    value under a non-default config — an unset `service` becoming a protocol label.

    A TSV row whose column count disagrees with `#fields` is **skipped and counted**,
    never zipped short: `zip` stops at the shorter side, so a truncated row — routine
    on the last line of a killed writer — would otherwise yield a complete-looking
    event with real endpoints and manufactured zeros, its `conn_state` quietly unknown.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as fh:
        fields: Optional[list[str]] = None
        sep = "\t"
        markers: set[str] = set()
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                if line.startswith("#separator"):
                    val = line.split(" ", 1)[1].strip()
                    sep = val.encode().decode("unicode_escape") if val.startswith("\\x") else val
                elif line.startswith("#fields"):
                    fields = line.split(sep)[1:]
                elif line.startswith(("#unset_field", "#empty_field")):
                    part = line.split(sep, 1)
                    if len(part) == 2 and part[1]:
                        markers.add(part[1])
                continue
            if line.startswith("{"):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    if stats is not None:
                        stats.malformed += 1
                continue
            if fields is None:
                continue  # TSV data before #fields header: skip, fail-soft per file
            values = line.split(sep)
            if len(values) != len(fields):
                if stats is not None:
                    stats.malformed += 1
                continue
            yield {k: ("" if v in markers else v) for k, v in zip(fields, values)}


def _row_ts(row: dict) -> Optional[float]:
    """Best-effort float `ts` for ORDERING only — never a value a parser trusts.

    `None` (never `nan`/`inf`) signals "cannot be ordered": `reorder_by_key`'s
    heap compares keys with `<`, and `nan` breaks a total order silently rather
    than raising, so a malformed row must never become one. `float()` already
    accepts an archive row's native numeric `ts` unchanged, so this is shared by
    both this module's row-level merges — `LogDirSource`'s window-tree mode and
    `ParquetArchiveSource`'s cross-partition merge alike.
    """
    try:
        v = float(row.get("ts"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _merge_key(row: dict) -> float:
    """`heapq.merge`'s key across several row streams: `_row_ts`, `-inf` when
    unorderable. `-inf` is safe *only* as a merge key (it never feeds a running
    max the way `reorder_by_key`'s `key` does) — it just means "emit this the
    instant it is this stream's turn", which is what an unparseable row's own
    `reorder_by_key` pass already arranged by never buffering it in the first
    place.
    """
    ts = _row_ts(row)
    return ts if ts is not None else float("-inf")


#: The digit shape `timeutil.format_instant` actually emits: ASCII digits,
#: optionally one `.` followed by more ASCII digits — epoch seconds, with the
#: fractional part preserved when the window boundary has one and omitted
#: when it does not. No sign, no exponent, no digit grouping, no non-ASCII
#: digit codepoint. Matched with `fullmatch` (the whole string, not a
#: prefix), so there is no `$`-before-a-trailing-newline gotcha to reason
#: about the way there would be with a hand-anchored `^...$` used with
#: `match`.
_WINDOW_ID_SHAPE = re.compile(r"[0-9]+(\.[0-9]+)?")


def _looks_like_window_id(name: str) -> bool:
    """True when `name` is the digit shape `timeutil.format_instant` actually
    emits (`_WINDOW_ID_SHAPE`) — not merely "parses as a finite float", which
    an earlier version of this function gated on and which is a PROXY for the
    shape, not the shape itself (`LES-008`'s error in a different costume;
    the lesson this file is producing, `LES-030`, exists because of it).
    `float()`'s parser is far more permissive than its one-line summary
    suggests: it accepts a leading sign (`"-1"`, `"+100"` — a window_id is
    never negative, and never redundantly signed), scientific notation
    (`"1e5"`), PEP-515 underscore digit grouping (`"2026_08_10"` — a
    directory name an operator would plausibly pick for a dated backup,
    sitting right next to a genuine harvest tree, and the realistic member of
    this list), surrounding whitespace (`"  100  "`), and Unicode
    decimal-VALUE characters that are not ASCII digits at all (`"٣٤"`,
    Arabic-Indic) — `format_instant` never emits any of these, so accepting
    them widened the gate past the shape it exists to validate.

    A regex-valid digit run can still overflow `float()` to `inf` (a few
    hundred digits, no decimal point, is enough), so `math.isfinite()` on the
    parsed value stays as a SECOND gate after the shape check — dropping it
    would silently readmit that one case even though the shape check alone
    already rejects every other non-finite spelling (`"nan"`, `"inf"`,
    `"1e400"` do not match `_WINDOW_ID_SHAPE` at all, so the second gate is
    reached only by inputs the first one could not have ruled out).

    This VALIDATES a name; it never ORDERS by one (`LES-013`'s distinction,
    made explicit here because it is easy to blur): `LogDirSource.streams`
    calls this to decide whether a tree of subdirectories confidently IS the
    harvest cold-tier shape at all (`M3`) — a positive gate, not a sniff —
    and a name failing it is exactly as informative as one passing: it means
    "this does not look like a window tree", not "this window sorts wrong",
    so the response is to read the tree as an ordinary flat directory
    instead, never to guess at an order from the name.
    """
    if not _WINDOW_ID_SHAPE.fullmatch(name):
        return False
    return math.isfinite(float(name))


class LogDirSource(ZeekSource):
    """A directory (or single file) of Zeek text logs — flat, or a window tree.

    **Flat** (unchanged since this source's first version): every matching file
    under `root`, found by an unbounded recursive walk, is its own stream — a
    rotated `conn.log` is several streams that `plugin.py`'s top-level merge
    combines. Chosen whenever `root` holds a matching file directly, at depth
    one, OR whenever `root` holds subdirectories but they do not confidently
    look like a window tree (see below) — a root is never left ambiguous, and
    flat mode reads either shape correctly either way (the walk is recursive),
    just without window-tree mode's per-stem consolidation.

    **Window tree** (new): `root`'s immediate children are all directories,
    NONE of them a matching log file, AND every one of their names is the
    digit shape `format_instant` actually emits (`_looks_like_window_id`) —
    the shape `xtract harvest`'s cold tier writes, one directory per pcap
    window,
    `window_id = timeutil.format_instant(window.start)`. That last clause is
    a POSITIVE gate, not a structural sniff (`M3`, CONVENTIONS §3 "an
    unrecognized shape is rejected, not passed through"): a root holding
    subdirectories that are NOT all window-id-shaped — an archive/backup/
    scratch subdirectory, a `.git`, Zeek's own native `<date>/` rotation
    layout — reads as flat instead, correctly (nothing is lost; the
    recursive walk still finds every file), with a note naming why window
    -tree mode was NOT chosen rather than silently guessing. `streams()`
    also emits a note on the flat branches for the same reason: every branch
    says what shape it found and why, not only the one that happens to be
    the harvest layout.

    Streams are consolidated to **one per log stem** in window-tree mode
    (`_window_tree_streams`), matching `ParquetArchiveSource`'s own reasoning
    for the identical choice: `plugin.py`'s top-level merge holds one open
    buffer per discovered stream, so a tree of N window directories must not
    turn `plugin.py`'s own view into N times as many streams as a flat
    directory would have. Consolidating the STREAM COUNT `plugin.py` sees is
    not, by itself, a memory bound on the merge INSIDE that one stream,
    though — see `_merged_rows` and `M1`.

    Two independent facts make "sort window directories by name and read them
    in that order" the wrong fix (`LES-013`: a derived order is not evidence of
    one) even though `window_id` looks like a timestamp. First, mixed-width
    names can coexist in one tree — `timeutil.format_instant` preserves a
    fractional part when the window boundary has one and omits it when it does
    not, so `"1785934195"` and `"1785934195.000000000"` are both real, and a
    plain string sort of the two does not even reliably recover *numeric*
    order at that width for every pair. Second, and decisively, even a perfect
    numeric sort of window START times is not row `ts` order: the producer can
    still write a row into a LATER window's directory than the one its own
    `ts` falls in (a long-running flow, still open when its own window closes,
    logged only once it finally does). So `_window_tree_streams` never reads a
    directory-name order as anything more than a deterministic tie-break for
    discovery (`LES-009`) — every row's placement in the merged output comes
    from `_row_ts`, read off the row itself.
    """

    name = "logdir"
    unit = "file"

    def __init__(self, locator: str, *,
                 reorder_horizon_seconds: float = REORDER_HORIZON_SECONDS,
                 reorder_rows: int = REORDER_ROWS):
        self.root = Path(locator)
        self.inventory: dict[str, int] = {}
        self._notes: list[str] = []
        #: NOT a class-level constant here (code review `M1`), unlike `ParquetArchiveSource`'s
        #: single, always-correct answer: completeness varies WITHIN this one class depending
        #: on which locator SHAPE `streams()` finds. Reset to the safe default
        #: (`ZeekSource.inventory_is_complete`'s own `False`) here, then set explicitly in
        #: EVERY branch of `streams()`, below, the same discipline `self.inventory`/
        #: `self._notes` already use for the identical "describes the most recent call only"
        #: reason. A recursive filesystem walk over a real directory (flat, empty, the
        #: not-a-window-tree fallback, or a window tree) sees every file regardless of
        #: whether `plugin.py` knows how to parse its stem, so those branches are `True` —
        #: but the single-log-FILE locator (`self.root.is_file()`) can structurally never
        #: see a second stem: measured, a single-file source over a 20-log-type directory
        #: reported `unparsed_log_types: []` in all 4 windows — a false "every stem
        #: accounted for" for a locator that never looked at the other 17 files sitting
        #: right beside it.
        self.inventory_is_complete = False
        #: window-tree mode's own per-file reorder pass needs the plugin's
        #: configured bound too (tests override `ZeekPlugin.reorder_rows` etc.,
        #: and this source is constructed fresh per run by `open_source`).
        self.reorder_horizon_seconds = reorder_horizon_seconds
        self.reorder_rows = reorder_rows

    def streams(self, columns: dict[str, list[str]]) -> list[ZeekStream]:
        # `columns` is ignored: a text row must be split in full before any field
        # can be addressed, so there is nothing to push down.
        #
        # Reset rather than accumulate: `inventory`/`notes()` describe the
        # MOST RECENT call, not a running total across however many times a
        # caller happens to call `streams()` on one instance — a stale entry
        # from an earlier call must not survive into this one's answer (a
        # nit this round's review caught: flat mode's own per-file `+= 1`
        # would otherwise double-count a stem across two calls, while
        # window-tree mode's `= len(paths)` would only silently overwrite —
        # two different wrong answers from the same unstated assumption).
        # `inventory_is_complete` gets the SAME reset-then-set discipline
        # (code review `M1`), to the safe default rather than a stale verdict
        # from whichever shape an EARLIER call on this instance happened to find.
        self.inventory = {}
        self._notes = []
        self.inventory_is_complete = False
        if not self.root.exists():
            raise FileNotFoundError(f"zeek source not found: {self.root}")  # fail loud
        if self.root.is_file():
            # A note here too, not just on the directory branches below: "every
            # branch says what shape it found and why" (`M3`) means every
            # branch, and a `--source` pointing directly at one log file is a
            # real (if narrow) locator shape, not a special case exempt from
            # the same disclosure the others get.
            #
            # `inventory_is_complete` STAYS `False` here (the reset above already
            # set it; not repeated as a redundant assignment) — a single file is
            # structurally one stem, and every OTHER stem this capture might carry
            # sits outside what this locator could ever see, so this branch can
            # never claim a complete census (code review `M1`).
            self._notes.append(f"{self.root}: a single log file, read directly "
                               f"(not a directory) — window-tree detection does "
                               f"not apply")
            return self._flat_streams([self.root])

        if any(_is_log_file(p) for p in self.root.iterdir()):
            paths = sorted(p for p in self.root.rglob("*") if _is_log_file(p))
            self._notes.append(
                f"{self.root}: flat directory, {len(paths)} log file(s) found "
                f"(recursive walk) — window-tree per-stem consolidation not "
                f"applied, because a log file sits directly under the root")
            # The recursive `rglob` above already walked the WHOLE tree under
            # `root`, so it saw every file regardless of whether `plugin.py`
            # knows how to parse its stem — a genuinely complete census.
            self.inventory_is_complete = True
            return self._flat_streams(paths)

        window_dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
        if not window_dirs:
            self._notes.append(f"{self.root}: empty — no log file, no subdirectory")
            # Audited and found nothing, not unaudited: the walk above DID look.
            self.inventory_is_complete = True
            return []

        not_a_window_id = next(
            (d for d in window_dirs if not _looks_like_window_id(d.name)), None)
        if not_a_window_id is not None:
            self._notes.append(
                f"{self.root}: {len(window_dirs)} subdirectory(ies), but not "
                f"every name parses as a window_id (epoch seconds) — e.g. "
                f"{not_a_window_id.name!r} — reading as a flat directory "
                f"instead of a window tree (`M3`: a directory name is "
                f"validated here, never ordered by)")
            # Same recursive walk as the genuine flat-directory branch above —
            # `rglob` over the whole tree, so this is complete too.
            self.inventory_is_complete = True
            return self._flat_streams(sorted(
                p for p in self.root.rglob("*") if _is_log_file(p)))

        # `_window_tree_streams` (below) walks every window directory with its own
        # `rglob`, so this is complete for the identical reason the flat branches
        # above are.
        self.inventory_is_complete = True
        return self._window_tree_streams(window_dirs)

    def _flat_streams(self, paths: list[Path]) -> list[ZeekStream]:
        out: list[ZeekStream] = []
        for path in paths:
            stem = _stem(path)
            self.inventory[stem] = self.inventory.get(stem, 0) + 1
            out.append(ZeekStream(
                stem=stem, label=path.name,
                rows=lambda stats, _p=path: _rows(_p, stats)))
        return out

    def _window_tree_streams(self, window_dirs: list[Path]) -> list[ZeekStream]:
        """One `ZeekStream` per log stem, each merging that stem's file from
        every window directory that has one.

        Grouping is by filename stem only, exactly like `_flat_streams` — a
        window directory that itself holds a rotated log (unusual, but Zeek's
        own rotation is a per-run setting the harvester does not control) just
        contributes more than one file to that stem's list, handled identically
        to any other file.

        `len(window_dirs)` is `root`'s own immediate-child count — accurate
        for the verified layout (`<root>/<window_id>/<stem>.log`, one level),
        and by construction never wrong here: reaching this method already
        required every one of `window_dirs`'s names to parse as a window_id
        (`streams()`'s gate), so there is no "wrapper" level left to hide an
        undercount behind. A window directory that ITSELF contains further
        nesting is still fully read (`rglob` below is recursive), just not
        separately counted as more "window directories" — nesting under a
        confirmed window id is not a shape this source claims to describe.
        """
        by_stem: dict[str, list[Path]] = {}
        for wdir in window_dirs:
            for p in sorted(q for q in wdir.rglob("*") if _is_log_file(q)):
                by_stem.setdefault(_stem(p), []).append(p)

        self._notes.append(
            f"{self.root}: window tree, {len(window_dirs)} window director"
            f"{'y' if len(window_dirs) == 1 else 'ies'} (every immediate "
            f"subdirectory name parses as a window_id), {len(by_stem)} log "
            f"stem(s) — each stem merged across its window directories on "
            f"every row's own ts, never on window-directory name (`LES-013`)")

        out: list[ZeekStream] = []
        for stem in sorted(by_stem):
            paths = by_stem[stem]
            self.inventory[stem] = len(paths)
            out.append(ZeekStream(
                stem=stem, label=f"{stem} ({len(paths)} window file(s))",
                rows=lambda stats, _p=paths: self._merged_rows(_p, stats)))
        return out

    def _merged_rows(self, paths: list[Path], stats: _StreamStats) -> Iterator[dict]:
        """This stem's rows, one window directory's file at a time, merged.

        Two layers, both reusing `reorder_by_key` (`Q2` — one merge
        implementation, not two): each file is *individually* only
        approximately ts-ordered — the exact same within-run pathology
        `LES-004` names for a single flat directory, since a window directory
        is just as much a real Zeek run as any other — so each is smoothed on
        its own bounded horizon BEFORE the cross-file merge, because
        `heapq.merge` (used for the second layer) only combines already-sorted
        inputs correctly; handing it un-smoothed files would let ordinary
        within-file jitter look like cross-window disorder it never was.

        **`M1` — the per-file cap is an AGGREGATE budget, not `reorder_rows`
        each.** A first draft handed every one of `len(paths)` sub-streams the
        full `reorder_rows`, reasoning that consolidating to one `ZeekStream`
        per stem bounded memory the way it bounds the stream count `plugin.py`
        sees. It does not: `heapq.merge`'s own initialization primes every
        argument (pulls its first item) regardless of how many there are, and
        a pcap window's own `ts` span typically sits inside the 600 s horizon,
        so nothing ages a buffer out — only end-of-file does. Measured: 670 MB
        at 300 window directories x 2,000 rows, because every inner buffer
        swallowed its whole file before the merge could even begin comparing
        heads. Dividing the stem's `reorder_rows` budget across its
        sub-streams (floored at 64, so the divide cannot starve repair to
        zero at very high fan-out) bounds the same corpus to 39 MB — the
        accepted, disclosed trade is REPAIR QUALITY: with enough window
        directories the per-substream cap binds before the horizon can, and
        `source_capped` (`M2`) is where that becomes visible instead of RAM.

        The per-file pass is given `_PerFileTally(stats)`, not the stem's real
        `stats` directly — `unordered` stays purely local (repair-support
        bookkeeping, never reported: reporting stays the sole job of
        `plugin.py`'s own per-stream `_in_order` pass, applied afterward to
        the `Event`s this merged row stream eventually becomes, which already
        sees the FULLY merged, cross-file sequence and is the one place able
        to correctly decide, once, what counts as disorder `reorder_by_key`'s
        horizon could not repair — a second, independent opinion from THIS
        layer would double-count the same displaced row) — while `capped` DOES
        forward, to `stats.source_capped`, because it has no second observer
        to double it (`_PerFileTally`'s own docstring has the full case for
        both halves of this asymmetry).

        `stats` (the real, shared one) still reaches `_rows()` directly, so
        `.rows`/`.malformed` accumulate correctly across every file this stem
        touches, exactly as they would for a rotated log in a flat directory.
        """
        max_rows = max(self.reorder_rows // max(len(paths), 1), 64)
        substreams = [
            (row for row, _displacement in reorder_by_key(
                _rows(p, stats), _row_ts,
                horizon=self.reorder_horizon_seconds, max_rows=max_rows,
                stats=_PerFileTally(stats)))
            for p in paths
        ]
        yield from heapq.merge(*substreams, key=_merge_key)

    def notes(self) -> list[str]:
        return list(self._notes)


# -- parquet metadata archive ---------------------------------------------------

class ArchiveContractError(ValueError):
    """The archive does not satisfy `docs/SPEC_zeek_metadata_archive.md`.

    Its own class because this is the one failure mode aximprint cannot prevent by
    writing correct code: the producer lives outside this repo. Every instance
    names the archive, the log type, and the clause that was violated.
    """


#: Provenance columns the spec requires on every row. `_row_no` is what makes the
#: read order *total*: partitions are sorted by ts, but equal timestamps would
#: otherwise resolve arbitrarily, and a tie broken differently renumbers a
#: published column (`LES-009`). It carries the row's position in the original
#: Zeek file, which is exactly the tie-break the text path uses.
ARCHIVE_PROVENANCE = ("_pcap", "_zeek_run", "_row_no")

#: `_archive_row_key`'s column -> the DuckDB type(s) its native Python value
#: must have for the cross-partition merge to be safe (`S2`). Before this
#: round `_read` never parsed `ts` at all — it trusted `preserve_insertion
#: order` and read tuples straight through — so no manifest type ever reached
#: a comparison. It does now: `_row_ts` returns `None` for anything `float()`
#: refuses, and `_merge_key`'s `-inf` fallback means a `ts` declared e.g.
#: `TIMESTAMP` (a DuckDB `datetime.datetime`, not a `float`) makes EVERY row
#: unorderable — the merge does not raise, it silently degenerates back to
#: `heapq.merge`'s own stream-position tie-break, i.e. path-order
#: concatenation, which is the exact bug this reader exists to refuse (SPEC
#: "`ts` is DOUBLE. Never TIMESTAMP."). `_zeek_run` failing is safer only in
#: that it is loud (comparing `""` against a non-`str` mid-merge raises), but
#: a raise buried three stack frames into a k-way merge is still worse than
#: refusing it here by name, before a row is ever read.
_ARCHIVE_KEY_COLUMN_TYPES: dict[str, frozenset[str]] = {
    "ts": frozenset({"DOUBLE"}),
    "_zeek_run": frozenset({"VARCHAR"}),
    "_row_no": frozenset({
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    }),
}

#: Nullable, and present only where the converter moved a row. Carries the
#: sensor's ORIGINAL disorder across a sort that would otherwise erase it.
DISPLACEMENT_COLUMN = "_displacement_seconds"


def _archive_row_key(row: dict) -> tuple:
    """`heapq.merge`'s key across an archive stem's several partition files:
    `(ts, _zeek_run, _row_no)`.

    This does **not** claim a partition file is written in `(ts, _zeek_run,
    _row_no)` order — only that `sorted_by == "ts"` is (`load_manifest`,
    checked at open). It does not need the richer claim (`S5`): `heapq.merge`
    preserves each INPUT's own items in that input's ABSOLUTE order — the key
    only ever decides which input's CURRENT head is smallest, i.e. how inputs
    interleave with each other — so a partition sorted purely on `ts` can
    never be internally re-ordered by a richer key, no matter what the key
    also compares. `_zeek_run`/`_row_no` earn their place for a narrower
    reason: a TIE on `ts` alone, ACROSS files, would otherwise resolve on
    `heapq.merge`'s own stream-position tie-break (deterministic, but
    unrelated to the rows' actual provenance); these two make that tie-break
    the SPEC's own stated total order instead ("Provenance columns"),
    matching the text path's `(ts, row_index)` convention one layer up.

    `ts` goes through `_row_ts` (`-inf` on `None`/unparseable/non-finite)
    rather than a bare `is not None` check: a tuple whose first element is
    `nan` compares `False` against itself both ways (`nan < nan` and
    `nan > nan` are both `False`), which silently breaks `heapq`'s invariant
    instead of raising. `load_manifest` now refuses a `ts` column that is not
    declared `DOUBLE` before a single row is read (`S2`), so this defends
    against a value, not a type the manifest could have caught; one bad row
    must still not crash a k-way merge every other partition depends on, and
    the parser's own guards handle it from there, exactly as they do on the
    text path.
    """
    ts = _row_ts(row)
    return (ts if ts is not None else float("-inf"),
            row.get("_zeek_run") or "", row.get("_row_no") or 0)


@dataclass
class ArchiveManifest:
    """The archive's read-first artifact, already validated for structure."""
    path: Path
    capture_node: str
    producer: dict
    time_bounds: dict
    logs: dict[str, dict]
    raw: dict = field(repr=False, default_factory=dict)


def load_manifest(root: Path) -> ArchiveManifest:
    """Read and structurally validate `manifest.json`. Fails loud, never soft.

    Structure only — whether a log type's columns satisfy a *parser* is a semantic
    question and belongs to the plugin, which owns the parser table.
    """
    path = root / MANIFEST
    if not path.exists():
        raise ArchiveContractError(
            f"no {MANIFEST} at {root}: an archive without its manifest cannot be "
            f"read, because aximprint reads only what the manifest declares")
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ArchiveContractError(f"{path}: manifest is not valid JSON: {e}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("logs"), dict) or not doc["logs"]:
        raise ArchiveContractError(f"{path}: manifest declares no `logs`")

    for stem, spec in sorted(doc["logs"].items()):
        if not isinstance(spec, dict):
            raise ArchiveContractError(f"{path}: logs.{stem} is not an object")
        if not isinstance(spec.get("columns"), dict) or not spec["columns"]:
            raise ArchiveContractError(f"{path}: logs.{stem} declares no `columns`")
        # The reader does not re-sort — it relies on this. Checked here so the
        # reliance is stated at the boundary rather than buried in a query; an
        # archive that *lies* is still caught downstream, where `_in_order`
        # judges live order and reports what it had to repair.
        if spec.get("sorted_by") != "ts":
            raise ArchiveContractError(
                f"{path}: logs.{stem} declares sorted_by="
                f"{spec.get('sorted_by')!r}, expected 'ts'. The reader consumes "
                f"partitions in file order without re-sorting (SPEC 'The disorder "
                f"contract')")
        disorder = spec.get("disorder")
        # "No evidence" and "evidence of none" are different findings (`LES-006`).
        # An archive that never measured its own disorder must not read as an
        # archive that measured it and found none, because the sort has already
        # erased the only other way to tell.
        if not isinstance(disorder, dict) or disorder.get("measured") is not True:
            raise ArchiveContractError(
                f"{path}: logs.{stem} does not declare `disorder.measured: true`. "
                f"The converter sorts by ts, so unmeasured disorder is unrecoverable "
                f"and every window built from it would read clean (SPEC "
                f"'The disorder contract')")
        # `S2`: a violation here would otherwise restore the partition-concat
        # bug this reader exists to fix — silently, for `ts`, since `_row_ts`
        # returning `None` for a wrong-typed column is indistinguishable from
        # an absent one downstream. Checked only when the column is DECLARED
        # (its presence is a separate concern, checked elsewhere per stem —
        # `_projection`'s `ARCHIVE_PROVENANCE` loop, and required-column
        # checks in `plugin._validate_declared_schema`): this function stays
        # structural, and an undeclared column is a different failure with
        # its own, already-existing message.
        for col, allowed in _ARCHIVE_KEY_COLUMN_TYPES.items():
            declared_type = spec["columns"].get(col)
            if declared_type is not None and declared_type not in allowed:
                raise ArchiveContractError(
                    f"{path}: logs.{stem}.columns[{col!r}] declares "
                    f"{declared_type!r}, expected {'/'.join(sorted(allowed))}. "
                    f"The cross-partition merge (`_read`) keys on this "
                    f"column's native Python type (`_archive_row_key`) — a "
                    f"wrong `ts` type in particular does not raise, it makes "
                    f"every row unorderable and silently degrades the merge "
                    f"back to path-order concatenation (SPEC '`ts` is DOUBLE. "
                    f"Never TIMESTAMP.')")

    return ArchiveManifest(
        path=path,
        capture_node=str(doc.get("capture_node", "")),
        producer=doc.get("producer") or {},
        time_bounds=doc.get("time_bounds") or {},
        logs=doc["logs"],
        raw=doc,
    )


class ParquetArchiveSource(ZeekSource):
    """The metadata archive: written once externally, read many times here.

    Reads through DuckDB, which is already a runtime dependency (`store/`) — so
    this transport adds none. Rows come back via `fetchmany` as tuples on purpose:
    `fetch_arrow` / `fetchdf` would pull in pyarrow or pandas, and a new runtime
    dependency is gated (CLAUDE.md).

    **What this transport is actually for**, measured on 400k synthetic rows
    rather than assumed (CHANGELOG 2026-08-05):

      * storage        9.2 MB vs 53.1 MB of TSV — **5.8x smaller**
      * time slicing   a 10% slice costs 0.50 s against a 4.96 s full scan —
                       **9.9x**, and it scales with selectivity. The text
                       transport cannot slice at all; it reads everything.
      * full scan      **1.02x. No speedup.** Row production is actually
                       *slower* than splitting TSV (1.88 s vs 1.47 s) because
                       building a Python dict per row costs more than the
                       columnar read saves. Typed columns do pay for themselves
                       after that (3.36 s -> 2.83 s, no try/except float() per
                       field), and the two roughly cancel.
      * peak RSS       3x the text path — DuckDB's own read buffers.

    So this is a **slicing and storage** transport, not a faster one. At the scale
    it exists for (a continuously growing archive, read by time range) that is the
    access pattern that matters; on a full scan, prefer whichever is convenient.
    Going faster than 1x means not building a dict per row, which changes the
    shared parser signature — a separate change, and one worth measuring first.
    """

    name = "parquet"
    unit = "partition"
    #: `False`, and this is worth an EXPLICIT declaration even though it now matches
    #: `ZeekSource.inventory_is_complete`'s own default (code review `M1` flipped that
    #: default; before, this line was the one override) — a reader should not have to
    #: infer this class's answer from inheritance, and TWO independent reasons back
    #: it, not one, either of which alone would be enough:
    #:
    #: 1. `self.inventory` here (`streams()`, below) is built from `self.manifest.logs`,
    #:    and the manifest is written once by an EXTERNAL converter
    #:    (`scripts/make_zeek_archive.py` today), not by this reader. That converter's
    #:    own stem loop (`if stem in zp._COLUMNS: ... # skipped/unrecognized types are
    #:    not converted` — and `_COLUMNS`'s key set is EXACTLY `_PARSERS`'s, checked by
    #:    an import-time assert in `plugin.py`) means a stem this build skips BY NAME,
    #:    or one it has never even heard of, is never written into the manifest at all
    #:    — there is no partition directory, no manifest entry, nothing for this reader
    #:    to discover. `self.inventory`'s keys are therefore ALWAYS a subset of
    #:    `_PARSERS`, structurally, regardless of what the capture the archive was
    #:    built from actually contained.
    #: 2. A TIME-SLICED read (`self.ts_from`/`self.ts_to` set) counts only the
    #:    partitions that fall in range — a stem that exists in the manifest but has
    #:    no partition inside the slice does not appear in `self.inventory` for THIS
    #:    read at all, independent of reason 1 and true even for a hypothetical future
    #:    converter that closed that gap. A per-instance flag (matching `LogDirSource`'s
    #:    own reasoning, above) is what a slice-aware answer would need; today reason 1
    #:    alone already makes every instance `False` unconditionally, so this stays a
    #:    class attribute rather than being computed per read — revisit if reason 1 is
    #:    ever closed and reason 2 becomes the only one left.
    #:
    #: Either reason means `unparsed = {s: n for s, n in src.inventory.items() if s
    #: not in _PARSERS}` (`plugin.py::events`) would be `{}` on EVERY archive-transport
    #: run, not because the capture had nothing left over, but because this transport
    #: is structurally unable to find out — the exact false "everything accounted for"
    #: `docs/WORK_ORDER_zeek_stem_coverage.md` D5 exists to refuse. Declaring `False`
    #: here is what makes `unparsed_stems` stay `None` (unaudited) instead.
    #:
    #: Reason 1 is a property of THIS CONVERTER, not an inherent limit of the Parquet
    #: format or of this reader: an archive manifest that recorded present-but
    #: -unconverted stems would close it — flagged as a candidate follow-up question
    #: for the doc stage to open and number; no such entry exists in `docs/
    #: OPEN_QUESTIONS.md` as of this build, so this comment does not claim one does.
    inventory_is_complete = False

    def __init__(self, locator: str, ts_from: float | None = None,
                 ts_to: float | None = None):
        self.root = Path(locator)
        self.ts_from = ts_from
        self.ts_to = ts_to
        self.inventory: dict[str, int] = {}
        self.manifest: ArchiveManifest | None = None
        self._notes: list[str] = []
        self._con = None

    # -- discovery -------------------------------------------------------------

    def _partitions(self, stem: str) -> list[Path]:
        """This log type's partition files, in a deterministic (path-sorted)
        order — a discovery order and a `heapq.merge` tie-break, NOT a claim
        that reading them in this order already yields ts order. See `_read`."""
        base = self.root / PARQUET_DIR / f"log={stem}"
        return sorted(p for p in base.rglob("*.parquet") if p.is_file())

    def streams(self, columns: dict[str, list[str]]) -> list[ZeekStream]:
        if not self.root.exists():
            raise FileNotFoundError(f"zeek archive not found: {self.root}")  # fail loud
        self.manifest = load_manifest(self.root)

        # Declared-vs-present, both directions. A file nobody declared is exactly
        # the "unrecognized shape" case: rejected, not silently included.
        parquet_root = self.root / PARQUET_DIR
        present = {p.name[len("log="):] for p in parquet_root.glob("log=*")
                   } if parquet_root.exists() else set()
        undeclared = sorted(present - set(self.manifest.logs))
        if undeclared:
            raise ArchiveContractError(
                f"{self.root}: parquet/ carries log type(s) the manifest does not "
                f"declare: {undeclared}. aximprint reads only what the manifest "
                f"declares, so this archive is ambiguous rather than merely extra")

        out: list[ZeekStream] = []
        for stem in sorted(self.manifest.logs):
            parts = self._partitions(stem)
            if not parts:
                raise ArchiveContractError(
                    f"{self.root}: manifest declares log type '{stem}' but "
                    f"parquet/log={stem}/ holds no .parquet files")
            self.inventory[stem] = len(parts)
            wanted = columns.get(stem)
            if wanted is None:
                # Not a log type this build parses. Registered so the plugin can
                # report it; never read.
                continue
            projection = self._projection(stem, wanted)
            out.append(ZeekStream(
                stem=stem, label=f"{stem} ({len(parts)} partition(s))",
                rows=lambda stats, _s=stem, _p=parts, _c=projection:
                    self._read(_s, _p, _c, stats)))

        span = self.manifest.time_bounds
        self._notes.append(
            f"archive {self.root} — node '{self.manifest.capture_node}', "
            f"producer {self.manifest.producer.get('tool', '?')} "
            f"{self.manifest.producer.get('version', '?')}, zeek "
            f"{','.join(self.manifest.producer.get('zeek_versions', []) or ['?'])}, "
            f"ts {span.get('min_ts', '?')}..{span.get('max_ts', '?')}")
        if self.ts_from is not None or self.ts_to is not None:
            self._notes.append(
                f"slice: ts >= {self.ts_from if self.ts_from is not None else '-inf'} "
                f"and ts < {self.ts_to if self.ts_to is not None else '+inf'}")
        # "Every branch says what shape it found and why" (`LogDirSource`'s own
        # docstring, `M3`) applies here too (nice-to-have, code review): a run
        # over this transport prints `unparsed_log_types`/`unrecognized_log_types
        # =None` in every window's health record for its whole duration, and
        # WHY is exactly as much this reader's job to disclose as `LogDirSource`'s
        # own shape notes are — not a silent, permanent gap the operator has to
        # already know the reason for (`inventory_is_complete`, above).
        self._notes.append(
            f"{self.root}: archive transport — unparsed_log_types/"
            f"unrecognized_log_types will read None (unaudited) in every window "
            f"this run produces, not [] (audited, clean): this reader's own "
            f"inventory can only ever contain stems its manifest declares, and "
            f"the manifest never names a skipped or unrecognized stem at all "
            f"(inventory_is_complete=False, see this class's own comment on why)")
        return out

    def _projection(self, stem: str, wanted: list[str]) -> list[str]:
        """Columns to SELECT: what the parser reads, plus what the contract requires.

        Explicit rather than `*`, but be clear about what that buys — measured on
        400k rows, not assumed. Projection shrinks the bytes read and the tuple
        DuckDB hands back; it does **not** make a full scan faster overall,
        because materializing a Python dict per row costs more than the columnar
        read saves (see the class docstring). It earns its keep on storage and on
        slices, and `SELECT *` would cost both.
        """
        declared = set(self.manifest.logs[stem]["columns"])
        cols = [c for c in wanted if c in declared]
        for c in ARCHIVE_PROVENANCE:
            if c not in declared:
                raise ArchiveContractError(
                    f"{self.root}: logs.{stem} is missing required provenance "
                    f"column '{c}' (SPEC 'Provenance columns')")
            cols.append(c)
        if DISPLACEMENT_COLUMN in declared:
            cols.append(DISPLACEMENT_COLUMN)
        return cols

    # -- reading ---------------------------------------------------------------

    def _cursor(self):
        import duckdb  # local: keeps the text path importable without duckdb
        if self._con is None:
            self._con = duckdb.connect(":memory:")
            # Explicit, not inherited from a default that could change: the read
            # order below depends on it, and an output byte must never depend on
            # an unstated setting (`LES-009`).
            self._con.execute("SET preserve_insertion_order = true")
        # An independent query context per stream: `plugin.py`'s top-level merge
        # interleaves per-stem streams, `_read`'s own k-way merge interleaves
        # per-partition ones, and a second `execute` on one connection would
        # invalidate the first result mid-iteration either way.
        return self._con.cursor()

    def _read(self, stem: str, parts: list[Path], cols: list[str],
              stats: _StreamStats) -> Iterator[dict]:
        """Yield this log type's rows: every partition file merged on ts.

        EACH partition file is sorted within itself — the contract requires
        only `sorted_by: "ts"` (SPEC), and `preserve_insertion_order` makes a
        single-file scan return rows in that written order, so re-sorting one
        file's own rows here would redo, per read, work the converter already
        did once (measured on 400k rows: 4.9x peak RSS for nothing, `LES-019`).
        That claim about the producer is not taken on trust: `sorted_by` is
        checked at open (`load_manifest`). `_archive_row_key`'s extra
        components are a deterministic tie-break BETWEEN files, never a
        claimed within-file order — `heapq.merge` emits each input in that
        input's own order absolutely (`LES-032`).

        But files ACROSS partitions are not guaranteed mutually ordered just
        because they are cut on the row's own ts (SPEC 'Layout'): an archive
        that grows incrementally can add a `part-NNN.parquet` to an EARLIER
        date's directory once a later conversion batch discovers a row that
        belongs there (the late-closing-flow fact `LogDirSource`'s module
        docstring names, one layer down) — `date=2026-08-01/part-001.parquet`
        can be *older* than `date=2026-08-01/part-000.parquet`'s content despite
        sorting after it by name, and `_partitions`'s path order across dates is
        no better a proxy. So this opens every part file's OWN cursor at once
        and merges them with `heapq.merge`, rather than assuming any path order
        already is ts order and concatenating (`LES-013`) — the identical
        "the same shape, one layer up" merge `LogDirSource._merged_rows` does
        across window directories, reused rather than reinvented (`Q2`).

        No per-file reorder buffer is needed here, unlike the text path: each
        file is exactly (not approximately) sorted by contract, so a plain
        k-way merge of already-sorted runs is exact, not best-effort — the one
        thing the text path's `conn.log` can never claim (`LES-004`).

        Every part's cursor is opened together (`heapq.merge`'s own
        initialization pulls one row from each argument), so peak memory does
        scale with partition count — but NOT through `fetchmany`'s own batch
        size (`S1`, `docs/WORK_ORDER_multi_window_log_read.md` — a DIFFERENT
        `S1` than the one two paragraphs below cites, from an earlier work
        order that happened to number its own findings the same way; both are
        kept as-is rather than renumbered, per the local-numbering discipline
        `LES-062` names): a swept comparison on a 400-partition archive found
        the dominant term is DuckDB's own per-cursor state (~175-215 KB/cursor;
        1200 simultaneous cursors at 4 stems x 300 partitions is +68 MB on its
        own), and a SMALLER batch measured MORE memory, not less (see
        `FETCH_BATCH`'s own comment) — so this reuses the one shared constant
        rather than a second, smaller one reasoned but not measured to help.

        `hive_partitioning=false` on the `read_parquet` call below is load-bearing, not
        cosmetic (`S1`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3 code review — a SECOND,
        unrelated `S1`, same caveat as above). `_partitions` already gives this call an exact,
        literal partition-file path, never a glob, so DuckDB's hive-partitioning inference buys
        nothing here — it only pulls the `log=<stem>/date=<D>` directory components INTO the
        result as synthesized columns, and when a log's own data has a column literally named
        `date` or `log` (`smtp.date`/`sip.date`, both real Zeek fields), that synthesized column
        SILENTLY SHADOWS the file's own — verified empirically against this exact call shape:
        `read_parquet('<one file>', hive_partitioning=true)` (the implicit default) returns the
        partition's own calendar date for a column named `date`, not the row's real content, and
        raises nothing. `hive_partitioning=false` is exact for what this reader actually needs
        (every partition column this reader cares about — `log`, `date` — is already encoded in
        the DIRECTORY STRUCTURE `_partitions`/`ParquetArchiveSource.streams` walk explicitly, never
        read back OUT of a row), free (no columns are pruned differently), and immunizes every
        current and future column name against this collision instead of requiring every log's
        `_COLUMNS` entry to individually avoid the two reserved names forever
        (`docs/SPEC_zeek_metadata_archive.md` "Columns").
        """
        select = ", ".join(f'"{c}"' for c in cols)
        where, params_base = self._predicate()
        sql = f"SELECT {select} FROM read_parquet(?, hive_partitioning=false) {where}"

        def one_part(part: Path) -> Iterator[dict]:
            cur = self._cursor()
            cur.execute(sql, [str(part), *params_base])
            names = [d[0] for d in cur.description]
            while True:
                batch = cur.fetchmany(FETCH_BATCH)
                if not batch:
                    return
                for values in batch:
                    yield dict(zip(names, values))

        yield from heapq.merge(*(one_part(part) for part in parts),
                               key=_archive_row_key)

    def _predicate(self) -> tuple[str, list]:
        clauses, params = [], []
        if self.ts_from is not None:
            clauses.append('"ts" >= ?')
            params.append(self.ts_from)
        if self.ts_to is not None:
            # Half-open, matching how windows are cut: an event at exactly `to`
            # belongs to the next slice, not both.
            clauses.append('"ts" < ?')
            params.append(self.ts_to)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    def notes(self) -> list[str]:
        return list(self._notes)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


# -- locator routing ------------------------------------------------------------

#: Locator prefix -> source. `ingest/base.py` already calls `source` a
#: "plugin-defined locator", so routing on a prefix needs no contract change.
PARQUET_PREFIX = "parquet:"


def open_source(locator: str, ts_from: float | None = None,
                ts_to: float | None = None, *,
                reorder_horizon_seconds: float = REORDER_HORIZON_SECONDS,
                reorder_rows: int = REORDER_ROWS) -> ZeekSource:
    """Pick a transport from the locator. A bare path stays the text directory
    (flat, or a window tree — `LogDirSource` decides which once it can see the
    root; this function does not need to know).

    `reorder_horizon_seconds`/`reorder_rows` reach `LogDirSource` because its
    window-tree mode needs the SAME bound `plugin.py` configures for its own
    top-level pass (tests override `ZeekPlugin.reorder_rows`, and `open_source`
    is called fresh per run) — `ParquetArchiveSource` ignores both, since its
    merge needs no reorder buffer (`_read`'s own docstring says why).
    """
    if locator.startswith(PARQUET_PREFIX):
        return ParquetArchiveSource(locator[len(PARQUET_PREFIX):], ts_from, ts_to)
    if ts_from is not None or ts_to is not None:
        # A slice the transport cannot push down would be silently ignored, and
        # the run would look like it honored a range it never applied. This
        # covers a window tree too, deliberately: a window directory's NAME is
        # not evidence of the ts range its rows actually carry (a late-closing
        # flow can land in a later window than the one its ts belongs to — see
        # `LogDirSource`'s docstring), so pruning which directories to even
        # open could drop a row the caller asked for without ever naming the
        # gap. A row-granularity filter (read every window, keep only rows in
        # range) would be sound but is not what `--from`/`--to` promises
        # elsewhere — it reads as a cheap, index-backed slice, and for a log
        # directory it would silently become a full scan wearing a slice's
        # name. Refusing is the honest answer until a real index exists.
        raise ValueError(
            "--from/--to require a parquet: archive; a log directory (flat or "
            "a window tree) has no partition index to slice on")
    return LogDirSource(locator, reorder_horizon_seconds=reorder_horizon_seconds,
                        reorder_rows=reorder_rows)


def source_vantage(locator: str) -> Optional[Vantage]:
    """The vantage manifest for `locator`, or `None` when nothing recorded one.

    `docs/WORK_ORDER_visibility_and_vantage.md` D2, tier 1. Deliberately **not** a method on
    `ZeekSource`: this reads one small JSON file and opens no stream, so a caller can ask *"where
    did this capture come from"* before committing to a run — which is what lets a conflict with an
    operator-supplied `--vantage` be refused up front instead of after forty days of ingest
    (`cli._resolve_vantage`).

    Three locator shapes, three answers:

      * a **`parquet:` archive** -> `None`, structurally and permanently. The archive's converter
        never wrote a vantage (its manifest has no slot for one), so this transport cannot report a
        tap even when one exists — the same shape as `Q30`, one layer down: a transport that cannot
        audit something must answer "unknown", never "none".
      * a **directory** -> its own `vantage.json`. This is the cold-tier root `harvest` writes into
        and the path `--source` is pointed at.
      * a **single log file** -> its parent directory's, since a file locator names one stream
        inside a tree the manifest describes.

    A malformed manifest raises `VantageError` rather than degrading to `None` (see
    `aximprint.vantage`): "nobody recorded a tap" and "somebody did and this build cannot read it"
    are opposite findings.
    """
    if locator.startswith(PARQUET_PREFIX):
        return None
    path = Path(locator)
    return read_vantage(path if path.is_dir() else path.parent)
