# WORK ORDER — Multi-window Zeek log-directory read

**STATUS:** LANDED (2026-08-10 — see CHANGELOG "Multi-window Zeek log-directory read")
**Depends-on:** none (extends `src/aximprint/ingest/zeek/sources.py` / `plugin.py`, the streaming
k-way merge from `docs/WORK_ORDER_streaming_zeek_ingest.md` and the Parquet transport from the
metadata-archive round, CHANGELOG 2026-08-05)
**Closes:** the gap `SESSION_STATE.md` (2026-08-07) named as "Next steps #1" — the
`ParquetArchiveSource._read()` partition-concat bug — and makes a tree of per-window Zeek log
directories a first-class aximprint source, closing that half of `Q2`'s "streaming ingest at scale"
concern for the harvest cold tier specifically.
**Authority:** drafted by Claude Code at user request (2026-08-10). The goal (make a tree of
per-window Zeek log dirs a first-class source, k-way merged on ts) was ratified by the user in the
task that produced this file. Individual decisions below — the detection rule, the stream-count
consolidation, where the health-signal ownership sits, the `--from`/`--to` refusal being kept — are
proposals, not independently ratified.

## Goal

`../xapi-pcap-extract` (`xtract harvest`) writes its cold tier as N per-window Zeek log directories
— one per pcap window, reusing a directory across two pcaps is an explicit error on the producer
side — rather than one flat directory. aximprint could not read that shape without an intermediate
Parquet conversion. Close that gap so the harvest cold tier ingests directly.

The one trap the goal exists to avoid: a per-window directory's name is not evidence of the `ts`
range its rows carry. The producer can write a late-closing flow into a *later* window's directory
than the one its own `ts` belongs to (a long-running connection, still open when its own window
closes, logged only once it finally does). Sorting directories by name and concatenating their
streams launders that into what looks like sensor disorder aximprint invented, or — worse — misses
it silently. `ParquetArchiveSource._read()` already had a version of this exact bug (see D5), so this
round fixes both through one shared merge helper rather than leaving a known-wrong concat in place
next to a newly-correct one.

## Decisions this draft makes

| # | Decision | Choice | Rejected, and why |
|---|---|---|---|
| D1 | How a window tree is detected | **Auto-detected, no new locator prefix.** `LogDirSource` stays flat (unchanged, one stream per file, recursive walk) whenever `root` holds a matching log file directly at depth one; only when it holds none, but does hold subdirectories, does window-tree mode (`_window_tree_streams`) engage. | A `windowtree:` prefix mirroring `parquet:` — rejected because the shape is unambiguously detectable from the filesystem, and a bare `--source <dir>` continuing to work for every existing flat-directory caller (including every test fixture and `verify_pipeline_gates.sh`'s synthetic capture) is worth more than an explicit-is-better-than-implicit label nobody asked for. |
| D2 | Stream granularity for a window tree | **One `ZeekStream` per log stem**, each internally merging that stem's file from every window directory that has one — mirroring `ParquetArchiveSource`'s existing "one stream per log type, not one per partition" reasoning (296 partitions × 16 types would be 4,736 buffers) applied one layer up (N windows × M stems would be exactly as bad). | Exposing one stream per (window, stem) pair and letting `plugin.py`'s existing flat top-level merge absorb it — the low-risk, zero-new-code option. Rejected: it is *correct* (nothing is silently lost — `_in_order`, unconditionally applied per stream, always flags a backward step wherever it came from) but *dishonest under the trap scenario specifically* — a naive per-window-file stream fed straight into a single horizon-based smoothing pass reports a manufactured `ingest_unordered` for a row that was never actually disordered, only discovered late because of how aximprint chose to read the tree (see D3's second bullet, and the reproduction in `tests/test_zeek_ingest.py::test_window_tree_naive_concatenation_would_have_failed_this_one`). |
| D3 | How the cross-window merge is built | **Two layers of one shared helper, `reorder_by_key`** (moved out of `plugin.py`'s private `_in_order`, generalized over any item and a `key`): each window directory's own file is smoothed on the SAME bounded horizon `LES-004` already uses for a single flat directory (a window directory is just as much a real Zeek run as any other, with the identical write-at-close/key-on-start pathology), and the smoothed per-file streams are then combined with a plain `heapq.merge`, keyed on the row's own parsed `ts`. | A single flat `heapq.merge` across raw, un-smoothed per-window files. `heapq.merge` only combines already-sorted inputs correctly; without the per-file smoothing pass first, ordinary within-file jitter (LES-004's own pathology) would surface as cross-window disorder it never was. |
| D4 | Where the health signal is decided | **`plugin.py`'s existing, unchanged per-stem `_in_order` pass remains the sole place `stats.unordered`/`SensorHealthEvent(ingest_unordered)` is decided** for this transport. The new per-file pass inside `sources.py` reuses `reorder_by_key` for its REPAIR behavior but is handed a purpose-built counting sink (`_PerFileTally`, renamed from `_DiscardedTally` — `M2`), so `unordered` is computed and discarded rather than reported. | Having BOTH the inner (per-file) and outer (per-stem, in `plugin.py`) passes report through the shared `_LogStats` object — the first design tried. It double-counts: a row the inner pass's horizon could not repair is reported once there, and then — since the outer pass sees the same still-displaced row in the fully-merged sequence, using the same horizon — reported again, turning one underlying disorder incident into two `SensorHealthEvent`s. `tests/test_zeek_ingest.py::test_window_tree_disorder_beyond_horizon_is_flagged_exactly_once` is the regression guard. **Refined 2026-08-10 (`M2`):** `unordered`'s reasoning does not extend to `capped` — it has no second observer (`_LogStats.capped` is read in exactly one place, the stderr summary), so silencing it the same way LOSES the fact rather than avoiding a double-report. `_PerFileTally` now forwards `capped` to a new field, `_LogStats.source_capped`, while keeping `unordered` local — see "Outcome" below. |
| D5 | Whether to fix `ParquetArchiveSource._read()` in the same round | **Yes — same k-way merge, one shared helper.** `_read()` assumed partitions are cut on `ts` so date order IS ts order, and concatenated them; an archive that grows incrementally can add a `part-NNN.parquet` to an EARLIER date's directory once a later conversion batch discovers a row that belongs there (`OVERWRITE_OR_IGNORE`, SPEC "Layout"), so a later-sorting part file is not evidence it is chronologically later. `SESSION_STATE.md` (2026-08-07) had already flagged this as "cheap to fix now, expensive once archives exist." Fixed with a plain `heapq.merge` across every partition's own cursor, opened simultaneously — **no reorder buffer needed here**, unlike the text path: each partition file is contractually EXACTLY sorted (checked at open), so the merge is exact, not best-effort. Tie-break is `(ts, _zeek_run, _row_no)`, the SPEC's own stated total order. | Leaving the concat bug in place and only fixing the text path — rejected per the task's own stated preference, and because it is the identical shape one layer down; fixing one and not the other would leave a known-wrong implementation next to a freshly-correct one in the same file. |
| D6 | Per-partition fetch size for the archive's cross-partition merge | ~~A new, smaller constant, `MERGE_FETCH_BATCH = 256`~~ **REVERSED 2026-08-10 (`S1`): deleted.** A 400-partition sweep measured 256 costing MORE memory than the original `FETCH_BATCH = 10,000` (239-303 MB, non-monotonic across 32..10,000 — noise around a dominant term the batch size never touched: DuckDB's own per-cursor state, ~175-215 KB/cursor). `_read` now reuses `FETCH_BATCH`. | *(originally)* Keeping one `FETCH_BATCH` for both — rejected once the memory-per-partition-times-partition-count arithmetic was worked through, without measuring it; see `Q18` (now closed) for the measurement that reversed this. |
| D7 | `--from`/`--to` for a window tree | **Refusal kept, extended to name the window-tree shape explicitly in the message.** A per-window directory's name is not a sound index to prune on for the identical reason D2/D3 exist — pruning which directories to even open could silently drop a row the caller asked for, and a row-granularity full-scan-with-filter would be sound but would silently become a full scan wearing a slice's name, which is not what `--from`/`--to` promises against the Parquet archive it was built for. | Lifting the refusal with a full-scan-then-filter fallback — technically correct (nothing is silently dropped, since every window is still fully read) but misleading: a caller reasonably expects `--from`/`--to` to be the cheap, index-backed slice it is against an archive, and a log directory (flat or tree) has no index to give it that. |

## Build

1. **`reorder_by_key`** (`src/aximprint/ingest/zeek/sources.py`) — the bounded, horizon-in-seconds
   reorder buffer, generalized from `plugin.py`'s private `_in_order` over `Callable[[T], Optional[float]]`
   instead of a hardcoded `Event.ts`. Yields `(item, displacement)` pairs; a `None` key bypasses
   buffering entirely (an unparseable row must not corrupt the heap's total order via `nan`, and
   must not be counted as disorder). `plugin.py`'s `_in_order` becomes a thin wrapper that turns a
   non-`None` displacement into a `SensorHealthEvent` — everything about its own external behavior
   is unchanged, and every pre-existing test in `tests/test_zeek_ingest.py` passes unmodified.
2. **`LogDirSource` dual-mode `streams()`** — `_flat_streams` (today's unchanged behavior, extracted
   verbatim) and `_window_tree_streams` (new: groups every window directory's matching file by stem,
   emits one `ZeekStream` per stem via `_merged_rows`, which reorder-buffers each file with a
   throwaway tally then `heapq.merge`s the results on `_row_ts`).
3. **`ParquetArchiveSource._read()`** — replaced the per-part sequential-cursor loop with
   simultaneously-opened per-part cursors merged via `heapq.merge`, keyed on `_archive_row_key`
   (`ts, _zeek_run, _row_no`, `-inf` on an unparseable/`nan` `ts` rather than a broken heap
   invariant).
4. **`REORDER_HORIZON_SECONDS`/`REORDER_ROWS`** moved from `plugin.py` to `sources.py` (re-exported
   so `zp.REORDER_ROWS` etc. keep resolving) — `LogDirSource`'s window-tree mode needs the same
   configured bound `plugin.py`'s own top-level pass uses, and `open_source()` grew keyword
   parameters to carry it through.
5. **CLI help text** (`src/aximprint/cli.py`) — one line, `--source` now mentions the window-tree
   shape.

## Boundaries — do NOT do these

- **Did not touch or read `../xapi-pcap-extract`.** The repo merge is a separate, later round;
  the verified input layout (`<out>/zeek/<window_id>/conn.log, ...`, `window_id =
  timeutil.format_instant(window.start)`) was given, not re-derived.
- **Did not rewrite `DESIGN_BRIEF_aximprint.md` §0.1** or any framing decision.
- **Did not remove or demote `ParquetArchiveSource` or `docs/SPEC_zeek_metadata_archive.md`.**
  Both transports remain first-class; the archive is still the storage/slicing transport its own
  class docstring describes.
- **No schema versioning** (`LES-001`) — no `windowtree:` prefix ceremony, no version field on
  `ZeekStream`, no compat shim.
- **No capture contents** anywhere (`LES-003`) — every fixture in this round is synthetic, including
  the determinism spot-check against a hand-built window tree with deliberately injected
  cross-window disorder.
- **No new runtime dependency** — `heapq`, `math` (stdlib) only; DuckDB usage in `_read()` is the
  transport's existing dependency, used differently (many simultaneous cursors instead of one
  sequential one), not a new one.

## Validation

- 13 new unit tests (`tests/test_zeek_ingest.py` ×11, `tests/test_zeek_parquet_source.py` ×2); full
  suite 252 → 265, all green.
- Every pre-existing test in both files passes **unmodified** — the flat-directory and archive
  behaviors this round did not intend to change did not change.
- `scripts/verify_pipeline_gates.sh` green on all three steps, including the transport-equivalence
  gate (log directory vs Parquet archive → byte-identical feature files).
- `scripts/verify_determinism.py` run against a hand-built, synthetic six-window tree with ~15% of
  rows deliberately written into a later window directory than their own `ts` belongs to (42 of 400
  rows landed beyond the 600 s horizon and correctly produced `ingest_unordered`) — two passes
  byte-identical across all three invariants (window close, synth compiler, feature archive). Not
  wired into `verify_pipeline_gates.sh` itself in this round (its existing fixtures are flat and
  archive-shaped only); a follow-up could add a third fixture shape there.
- `tests/test_zeek_parquet_source.py::test_partitions_within_one_date_merge_on_ts_not_on_part_filename`
  reproduces the pre-fix bug directly (two hand-built part files in one date directory, mutually out
  of ts order); a standalone script reproducing the OLD concatenation logic against the identical
  fixture was run by hand and confirmed it fails (`[..., T0 - 1000.0]`, not sorted) — this test would
  have caught the bug this round fixes, not just exercised the new code path.

## Findings to report

1. **The double-count risk (D4) was not obvious until traced through by hand.** A first draft of this
   round tagged residual, per-file displacement onto rows via the existing `DISPLACEMENT_COLUMN`
   channel (reusing what `ParquetArchiveSource` already uses to survive its own converter's sort) and
   let `plugin.py`'s unchanged per-stem pass pick it up. That is *safe* for Parquet, because the
   archive is genuinely, fully sorted by the time the reader's own pass sees it — the outer pass never
   finds anything to re-flag. It is *not* safe for the window-tree case, because the inner pass's own
   repair is only bounded by the SAME horizon the outer pass also applies, to the SAME kind of
   still-displaced row, so the outer pass's own detection and the inner pass's tag would usually agree
   and double-report. Fixed by making the outer pass the sole reporter (D4). Worth carrying forward as
   a general caution: reusing a channel designed for "a sort erased evidence I need to carry across it"
   is not automatically safe for "a buffer I own is discarding what it could not repair" — the two look
   similar but the second one has an unchanged downstream pass still watching the same stream.
2. **`Q18` opened** — `MERGE_FETCH_BATCH` (D6) and the window-tree stream count at real harvest/archive
   scale (hundreds to low thousands of window directories or partitions per stem) are reasoned from
   the same "streams are the unit of reorder buffering" principle `LES-010` already states, but neither
   is measured against a real capture the way `LES-019`'s Parquet numbers were — no real harvest tree
   or multi-hundred-partition archive was available this round (privacy-gated, and the producer repo is
   out of scope). Determinism holds on the synthetic fixture used for validation; the constant and the
   round-trip cost at scale do not have a number attached yet. **Superseded 2026-08-10:** both halves
   were measured (on synthetic corpora sized to be measurable) in the consolidated fix round below —
   `Q18` is now closed, and the window-tree finding here turned out to understate the problem (670 MB
   measured, not merely "unmeasured"). See "Outcome — consolidated fix round", M1 and S1.
3. **A minor, pre-existing spec/implementation gap, not fixed here.** The old `_read()`'s own comment
   claimed "the contract requires each file to be written sorted by `(ts, _zeek_run, _row_no)`", but
   `docs/SPEC_zeek_metadata_archive.md` only states `sorted_by: "ts"` (a single key) — the compound tie
   -break is only actually true because `scripts/make_zeek_archive.py`'s reference implementation
   happens to write `ORDER BY "ts", "_zeek_run", "_row_no"` per partition, not because the spec commits
   a real external converter to it. This round's fix makes the reader defensive regardless (ties break
   on `_zeek_run`/`_row_no` explicitly in `_archive_row_key`, and a `nan`/missing `ts` sorts first
   rather than corrupting the merge), so nothing downstream depends on the gap — but the spec's prose
   could be tightened to say what it actually requires. Left to a documentation-focused change since it
   is not a behavior bug.

## Outcome — consolidated fix round (2026-08-10)

Tester and code-reviewer ran against the first draft; four must-fix findings (M1-M4), seven
should-fix findings (S1-S7), and a handful of nits came back. One, M1, needed a user ruling (below);
the rest were code-writer calls, stated here rather than silently taken.

**M1 — the memory-bound claim in the module docstring was FALSE.** Consolidating to one `ZeekStream`
per stem bounds the stream count `plugin.py` sees; it does not bound memory the same way, because
`heapq.merge`'s own initialization primes every sub-stream inside that ONE stream regardless of how
many there are. Measured independently by tester and reviewer: 508 MB at 150 window directories x
3,000 rows; 670 MB at 300 x 2,000 (417 MB for the identical corpus read flat) — because a pcap
window's own `ts` span typically sits inside the 600 s horizon, so only end-of-file, never the
horizon, releases a buffer, and every one fills before the merge can compare heads.
**User ruling: option (b), bound the AGGREGATE per stem** rather than giving every sub-stream
`reorder_rows`. `_merged_rows` now computes `max_rows = max(reorder_rows // len(paths), 64)` and hands
that to every sub-stream's inner `reorder_by_key` pass — the reviewer's own concrete suggestion,
adopted as given; it measured 39 MB on the 300 x 2,000 corpus. The floor (64) exists so the division
cannot starve repair to zero at very high fan-out; the accepted, disclosed cost is degraded repair —
a harsh 120-directory / 25%-displaced-to-900s corpus went from 0 unrepaired inversions (never a
designed guarantee — an accident of unbounded memory) to 3,976, now visible via `source_capped` (M2)
rather than silently paid for in RAM. The module docstring (`sources.py:42`-ish) is corrected to state
the real bound; `Q18` is closed with both this and S1's numbers.

**M2 — `capped` was being silenced the same way `unordered` needed to be, and should not have been.**
`_DiscardedTally` (now `_PerFileTally`) exists because `unordered` has a second observer:
`plugin.py`'s outer, per-stem `_in_order` pass re-judges the fully-merged sequence and would otherwise
double-report the same displaced row. `capped` has NO second observer — `_LogStats.capped` is read in
exactly one place (`ZeekPlugin._summary`'s stderr line), which only ever saw the outer pass's own
cap-forced flushes — so discarding the inner pass's `capped` the same way LOST the fact rather than
avoiding a double-report, which is exactly `LES-004`'s "count it and disclose it, never as a debug
line" and `LES-006`'s "no evidence" reading as "evidence of none", now that M1 makes cap-forced
flushes the normal way disorder gets traded for bounded memory rather than a rare edge case. Fixed:
`_LogStats` gained `source_capped: int = 0`; `_PerFileTally.capped` is a property whose setter
forwards every increment's DELTA to `stats.source_capped` (not just "+1", so it stays correct
regardless of how the caller increments) while `unordered` stays a plain, local, unread attribute;
`_summary()` reports `source_capped` alongside `capped` when non-zero. **What `capped` reports for the
tree case, demonstrated (`tests/test_zeek_ingest.py::test_window_tree_disorder_beyond_horizon_is
_flagged_exactly_once`):** a 100-row-prefix-plus-one-late-row fixture, `reorder_rows=10`, produces
exactly 1 `SensorHealthEvent` (from the OUTER pass, unchanged) and `source_capped=37` (from the INNER
pass's own cap-forced flushes, disclosed but not double-counted) — the tester's earlier isolated
reproduction (40,000 dns rows, `reorder_rows=512`) is structurally the same shape: `REPORTED
capped=0 unordered=0 | DISCARDED capped=34,880 unordered=0` before this fix, `source_capped=34,880`
visible after it.

**M3 — the flat/window-tree switch was a structural sniff, not a positive gate — CONVENTIONS §3
violation, fixed.** "No log file directly under root" was the ENTIRE test; nothing checked that the
subdirectories found actually looked like the harvest shape. Measured failure modes: a stray
root-level log flipped 1 stream to 51 with no note at all; `archive`/`backup`/`scratch` subdirectories
were announced as a genuine window tree; Zeek's own native `<date>/<time-range>.log.gz` rotation
layout was silently reinterpreted as a window tree (output stayed correct, but unannounced, for the
single most common real deployment shape); nested wrapper directories caused an undercount with
nothing to reveal it; a `.git`-shaped subdirectory got swept in and its contents reported
UNRECOGNIZED. Fixed with a genuine positive gate, `_looks_like_window_id(name)`: every one of root's
immediate child directories must parse as a finite float (the shape `timeutil.format_instant`
produces) before window-tree mode engages at all; ONE non-conforming name (named in the note, e.g.
`'wrapper'`) falls the WHOLE root back to flat mode — a validated name, never an ordering decision
(`LES-013`'s distinction stays intact: the gate checks whether a name PARSES, never uses it to decide
row order). Every branch of `streams()` — flat via a direct log file, flat via a failed window-tree
gate, genuine window tree, and empty root — now appends a `notes()` line naming the shape found and
why, closing the "observable in exactly the direction that does not matter" gap. Two tests that used
to pin the old, silent behavior as a documented gap
(`test_root_with_direct_log_and_window_dirs_stays_flat_with_no_stream_consolidation`,
`test_nested_window_dirs_are_read_but_the_reported_directory_count_is_wrong`, the latter renamed
`..._fall_back_to_flat_rather_than_miscounting`) now assert the fixed behavior instead.

**M4 — the tier-2 gate never exercised the new source at all.** Added
`scripts/split_zeek_window_tree.py` (test tooling, mirroring `make_zeek_archive.py`'s house shape):
splits the existing flat synthetic fixture's `conn.log` into per-window directories by `ts`, copying
lines VERBATIM (never re-parsed and re-serialized, so the split cannot introduce any byte-level
difference `_rows()` would not already treat as equivalent) — with every 7th row deliberately written
into the NEXT window's directory, exercising the actual late-closing-flow trap rather than a shape a
naive concatenation would also pass. `verify_pipeline_gates.sh`'s step 3 (renamed "transport
equivalence") now builds this window tree alongside the existing flat/archive pair and asserts all
four feature-row files are byte-identical across all three transports — confirmed green, including
the deliberately-displaced rows.

**S1 — `MERGE_FETCH_BATCH` optimized a term that was not the dominant one; deleted.** A swept
comparison on a 400-partition archive found the ORIGINAL concatenation (`FETCH_BATCH=10,000`) at 218
MB / 3.15 s, the merge at 239-303 MB / 3.7-4.0 s across batch sizes 32 through 10,000 — non-monotonic,
i.e. noise, because the dominant term is DuckDB's own per-cursor state (~175-215 KB/cursor; 1,200
simultaneous cursors at 4 stems x 300 partitions is +68 MB on its own), which no `fetchmany` batch
size touches. `_read` now reuses the one `FETCH_BATCH` constant. **Worth a CHANGELOG line:** the merge
itself (correctness fix, not the deleted batch-size tuning) costs a genuine 18-26% more wall-clock
time than the bug it replaces — the honest price of not laundering disorder into a clean-looking
archive read.

**S2 — a wrong-typed archive column would have silently restored the bug this round fixes.**
`_row_ts` returns `None` for anything `float()` refuses; before this round `_read` never parsed `ts`
at all, so no manifest type claim ever reached a comparison. Now it does: an archive declaring
`"ts": "TIMESTAMP"` would make every row unorderable, and `_merge_key`'s `-inf` fallback means the
merge would not raise — it would silently degenerate back to `heapq.merge`'s own stream-position
tie-break, i.e. the exact path-order concatenation this round exists to refuse, discoverable only via
the pre-existing "0 events from >0 rows" tripwire (a stderr note, exit 0). `load_manifest` now checks
`ts == DOUBLE`, `_zeek_run == VARCHAR`, `_row_no` any DuckDB integral type, for every DECLARED
occurrence of these columns (presence is a separate, already-checked concern) — refusing at open,
naming the column and the wrong type, before a single row is read.
`test_archive_key_column_wrong_type_is_refused` (parametrized over all three columns) is the guard.

**S3 — the file/stem predicate had grown four independent copies.** Extracted `_is_log_file(p)` and
`_stem(p)` in `sources.py`; `LogDirSource.streams`/`_flat_streams`/`_window_tree_streams` and
`scripts/make_zeek_archive.py` all call the shared functions now instead of repeating
`(p.suffix in (".log",".gz") or ".log" in p.name)` / `p.name.split(".")[0]`.

**S4 — `_merged_rows`'s `stem` parameter was unused.** Dropped; the lambda closure no longer captures
or passes it.

**S5 — `_archive_row_key`'s docstring overclaimed a contract the SPEC does not state.** It read as
though partition files needed to be written in `(ts, _zeek_run, _row_no)` order for the merge to be
correct. They do not: `heapq.merge` preserves each INPUT's own items in that input's ABSOLUTE order —
the key only ever decides which input's CURRENT head is smallest, i.e. how inputs interleave with
each other — so a partition sorted purely on `ts` (the actual, checked contract) can never be
internally reordered by a richer key. `_zeek_run`/`_row_no` earn their place narrowly: as the
deterministic tie-break for a `ts` collision ACROSS files, matching the SPEC's own stated total order,
not as a claimed write order. Docstring restated to say that instead.
(`docs/SPEC_zeek_metadata_archive.md:133`-138 separately still says "aximprint reads with `ORDER BY`"
— stale prose, doc-writer's fix, not this round's.)

**S6 — the `TYPE_CHECKING`-only upward import to `plugin._LogStats` is gone.** `_PerFileTally` was
becoming a second, hand-maintained clone of `_LogStats`'s counting surface. `sources.py` now declares
three `typing.Protocol`s in the LOWER layer — `_RowStats` (`.malformed`, what `_rows()` needs),
`_ReorderStats` (`.unordered`/`.capped`, what `reorder_by_key` needs), `_StreamStats` (adds
`.source_capped`, what a `ZeekStream.rows` factory's argument satisfies in practice) — and
`_LogStats` (plugin.py) satisfies all three PURELY STRUCTURALLY, no import needed either direction.

**S7 — health-signal attribution now genuinely differs between the two log-directory modes, worth a
CHANGELOG sentence, not a regression.** Flat mode judges order per FILE (unchanged); window-tree mode
judges order across the MERGED PER-STEM sequence, so `SensorHealthEvent.value` (displacement) is now
measured against a running max spanning every window directory that contributed to a stem, not one
file's own. Arguably the more informative number (it reflects what the sensor's disorder actually
looked like once fully assembled) — flagged here so `live-verifier` does not read the difference as a
bug when it eventually runs against a real harvest tree.

**Nits taken:** `LogDirSource.streams()` now tests "any log file present" lazily (`any(...)`) rather
than building the full list just to check it is non-empty; `self.inventory`/`self._notes` reset at
the top of `streams()` so a second call on one instance does not accumulate stale entries from the
first (flat mode's own `+= 1` would have double-counted a stem across two calls; window-tree mode's
`= len(paths)` would only have silently overwritten — two different wrong answers from the same
unstated assumption); `reorder_by_key(items: Iterator)` retyped `Iterable`, matching what it actually
requires.

**Fast suite:** 252 → 271 (tester's 6 additions) → 274 (this round's 3 new S2 parametrize cases), all
green, every pre-existing assertion either untouched or updated to assert the FIXED behavior rather
than weakened. `scripts/verify_pipeline_gates.sh` green on all three steps including the new
three-way transport-equivalence comparison.

## Second fix round (2026-08-10) — the window-id gate itself

Tester's re-verification (322 tests, `verify_tier.sh` green, M1 independently reconfirmed at 48.9 MB
absolute peak — vs 670 MB pre-fix — and holding at 65.5 MB on a 500 x 2,000 / 1M-row synthetic
harvest-scale corpus) surfaced two narrow findings, the naming lesson for the round: **an
auto-detecting gate on a declared shape is a positive gate on that shape, or it is a sniff** —
`_looks_like_window_id`'s old `float(name)` check validated a PROXY (parses as a float) rather than
the shape itself (`LES-008`'s error in a different costume — recorded as `LES-030`).

1. **Real false accepts**, adversarially found: `-1`, `1e5`/`1E5`, PEP-515 underscore grouping
   (`1_000`, `2026_08_10`, `1_2_3`), surrounding whitespace, a redundant leading `+`, and Arabic-Indic
   digits (`٣٤`) all used to pass `_looks_like_window_id`. The realistic one, `2026_08_10` (a plausible
   dated-backup directory name sitting next to a genuine harvest tree), demonstrably swept into
   window-tree mode and misreported its directory count. Fixed: `_WINDOW_ID_SHAPE =
   re.compile(r"[0-9]+(\.[0-9]+)?")`, matched with `fullmatch` (not a hand-anchored `^...$` with
   `match`, which has a `$`-before-trailing-newline exception `fullmatch` does not). `math.isfinite()`
   stays as a genuine second gate — a long enough all-digit run still overflows `float()` to `inf`,
   which the shape regex alone cannot rule out — now with its own dedicated regression test
   (`test_digit_only_overflow_is_rejected_by_isfinite_specifically`) proving it is load-bearing rather
   than dead code post-regex. No shape was tightened away that `format_instant` can genuinely emit:
   all four plausible forms (bare int, fractional with a real fraction, fractional all-zero, degenerate
   `"0"`) still pass, unchanged.
2. **The single-file locator branch was the one silent branch left.** `streams()` returned on
   `self.root.is_file()` before reaching any `self._notes.append(...)` — contradicting "every branch
   says what shape it found and why". Took the preferred option (emit a note; did not narrow the
   docstring's claim instead) — one line, naming the shape ("a single log file, read directly").

Three tests updated to assert the fixed behavior rather than the documented gap: the 9-case false
-accept parametrize (assertion flipped `True`→`False`, renamed
`test_window_id_gate_rejects_the_previously_documented_false_accepts`); the end-to-end
`"2026_08_10"`-collision test (renamed `test_previously_false_accepted_directory_now_falls_back_to
_flat`, now asserts flat-mode fallback with the offending name in the note, not a miscounted window
-tree); `test_single_file_locator_produces_no_notes` → `..._produces_a_note`. One new test added
(`test_digit_only_overflow_is_rejected_by_isfinite_specifically`). Fast suite 322 → 323.
`scripts/verify_pipeline_gates.sh` green — the window-tree gate step's own fixture (integer-string
window ids from `split_zeek_window_tree.py`) still matches the tightened shape unchanged.
