# Plan — `harvest`: a continuous pcap → Zeek → metadata-archive pipeline

> **Status:** H-1–H-4 (§6) are **built and live-verified** — see CHANGELOG 2026-08-06 "harvest
> foundations", 2026-08-06 "Zeek pinned to 8.0.9 with `local` loaded", 2026-08-06 "Code review and fix
> pass over H-1/H-2/H-3", and 2026-08-07 "H-4: the concurrent extract → Zeek → reap pipeline" /
> "`GET /axapi/pcap` returns 200 with an error body under concurrency". Written 2026-08-06, when this
> was a producer-side plan in a separate repository handing decisions to `aximprint` across a repo
> boundary.
>
> **Corrected 2026-08-10 (doc-writer, landing this file in the merged repo —
> `docs/WORK_ORDER_merge_xtract_acquire.md` step 10):** the repo boundary this plan was written
> against is gone. §6/§7 below still describe **H-0 and H-6 as open work and H-5 as blocked on
> `aximprint`'s ruling** — all three are stale. `H-0` (a cross-repo format contract) and `H-6`
> (manifest + atomic publish) are **deleted**, not deferred: the user ruled the two projects merge
> with no archive seam (CHANGELOG 2026-08-10, DESIGN_BRIEF §0.1 amendment), the Zeek cold tier became
> directly readable by `ingest` (CHANGELOG 2026-08-10, closes `Q18`), and the four rulings this
> section asked `aximprint` for evaporated with the seam — see the corrected §6/§7 below rather than
> trusting the original prose above this note. `H-5` (the Parquet converter) is **optional**, not
> blocked; nothing here proposes building it. `docs/BRIEF_for_aximprint_event_model.md` did not move
> into this repo (superseded by the merge itself — its own §1 "Both tools live outside `aximprint` and
> always will" is exactly the claim that stopped being true); `docs/SESSION_STATE.md`'s "Open
> questions" section it pointed to no longer exists in that form either. One item survived and is now
> tracked properly: `docs/OPEN_QUESTIONS.md` `Q23`.
> **Genre:** plan (precedes work orders; see `docs/CONVENTIONS.md` §3).

## 1. What this builds

A long-running tool (a daemon later) that walks a time range, pulls pcaps from CorpAPCN via
`xtract`, runs Zeek over each, and materializes the results as a **metadata archive** that
`aximprint` consumes.

```
                  ┌── pcap → zeek → parquet ──┐   (this tool, today)
                  │                            ├──►  ARCHIVE  ──►  aximprint
                  └── xpress metadata ─────────┘   (a later producer, same format)
```

`--start` accepts an absolute epoch or a relative expression (`"30 days back from now"`);
`--end` accepts an absolute epoch, a relative-to-start expression (`"2 days from the start"`),
or nothing (meaning the latest stored data). `--window` is the pcap granularity in s/m/h.

## 2. The ownership boundary — the decision everything else follows from

**This tool guarantees *shape*. `aximprint` assigns *meaning*.**

| Owned here | Owned by `aximprint` |
|---|---|
| Getting bytes off the appliance | The event model (`FlowEvent`, `IdentityEvent`, …) |
| Running Zeek, pinning its version | Normalization: sensor rows → events (`_PARSERS`) |
| Archive layout, typing, partitioning | Which fields are lossy, and why |
| Provenance, disorder accounting, manifest | Windowing, features, learners |
| Atomic publish | Reading and validating the archive |

The archive therefore carries **raw sensor rows**, typed and well-formed — not normalized events.

Rejected alternative: normalizing in the producer. It reads faster on paper, but the measured
`aximprint` bottleneck was *text decode* (~10 MB/s of TSV parsing), which Parquet removes wherever
normalization happens. Moving normalization out would put semantic authority — what `RSTR` means,
what is safe to drop — in a component with no downstream stake, and would force every producer to
change on every event-model revision. **`aximprint` owns the meaning of its own vocabulary.**

Consequence for `aximprint`'s planned `sources.py`: the source protocol keys on
`(sensor, stream)` rather than a bare `stem`. `ParquetArchiveSource` stays generic and permanent;
Xpress lands later as a second parser table *inside* `aximprint`, exactly as already planned.

## 3. Archive format (this tool's contract)

```
<archive-root>/
  manifest.json
  raw/sensor=zeek/stream=conn/date=2026-08-01/part-000.parquet
                  stream=dns/ date=2026-08-01/part-000.parquet
  cold/sensor=zeek/date=2026-08-01/conn.log.gz        # immutable evidence tier
```

**Partition on the row's own `ts`, never the source pcap's name.** `conn` keys a row on connection
start but writes it at close, so rows routinely belong to an earlier day than the pcap containing
them. Day granularity, not hour: sub-day pruning comes from row-group min/max on `ts`.

**Typing rules**
- `ts` is `DOUBLE`, never `TIMESTAMP`. DuckDB timestamps are microsecond-quantized; a Zeek `ts` is
  an epoch double and quantizing it can move an event across a window boundary.
- Unset fields are **real nulls**, never Zeek's `-`. The unset-vs-zero distinction stops riding on
  string-matching a per-file `#unset_field` marker.
- The `date` partition column is derived for pruning only and is never the source of truth.
- A superset of columns is fine; a missing declared column is fatal on read.

**Provenance columns, every row:** `_source` (the pcap filename — which is the exact `start_time`
string sent on the wire, so it joins back to the extraction manifest), `_run`, `_sensor`,
`_producer_version`, and a nullable `_displacement_seconds` recording how far a row was moved
pre-sort.

That last column is the honesty constraint. Sorting by `ts` is required for row-group pruning, but
Zeek logs are *not* ts-sorted, and a converter that silently sorts destroys the evidence that the
sensor data was disordered. Non-null only for displaced rows, so it costs nothing when there is
nothing to declare.

**Caveats are data, not assumptions.** Boundary-split flows are an artifact of *this* producer's
windowing; a line-rate metadata source has no artificial boundaries. The manifest declares caveats;
the consumer must not hardcode compensation for one producer's artifacts.

**Fidelity is declared.** Per `(sensor, stream)`, the manifest states which columns this producer
populates — so a null is never ambiguous between "the sensor could not see it", "it was genuinely
zero", and "this producer drops it".

## 4. Publish protocol — the open coordination point

`aximprint` reads only what the manifest declares, and an undeclared Parquet file is an error. A
continuously-growing archive written by a long-running producer therefore has a window in which
files exist and the manifest does not know them. **Proposed:** write parts under
`_incoming/`, then publish atomically (rename, then swap `manifest.json` via write-temp-and-rename),
so the archive is always readable at a consistent point.

**Needs `aximprint`'s agreement — this is a contract change on the consumer side.** So does the
late-row policy: a day partition can receive rows from pcaps processed much later (a six-hour flow
starting at 23:00 is written by the pcap covering 05:00 the next day), so partitions accrete
`part-NNN` and per-day row counts in the manifest are as-of-publication, not final.

## 5. Runtime shape

```
   planner ──► extract pool ──► pcap spool ──► zeek pool ──► convert ──► publish
   (windows)   (N concurrent)   (disk cap)     (M workers)   (duckdb)    (atomic)
                     ▲                              │
                     └──────── backpressure ────────┘
```

**Measured, not assumed:**
- Extraction is **latency-bound** (~4 s fixed per request) and **does not parallelize at all**.
  **Corrected 2026-08-07:** an earlier benchmark here claimed near-linear scaling to 50.3 MB/s at
  concurrency 8. That measurement was wrong — it counted bytes without checking they were pcaps,
  and the appliance answers every concurrent request beyond the first with a `200 OK` whose body is
  repeated error text (`docs/VENDOR_API_DEFECTS.md` §S2). Re-measured with validation: concurrency
  1/2/4/8 yields 1/1/1/1 valid pcaps and 0/1/3/7 corrupt ones. **Real throughput is ~13.9 MB/s,
  single-flight.** Extract concurrency is therefore 1, and any future increase must be re-measured
  with the response bytes validated.
- Window size is the second lever: at 60 s windows the fixed per-request cost dominates; at 300 s it
  amortizes ~4.5×. Larger windows also mean fewer boundary-split flows. **Default 300 s.**
- 12 streams hold ~21.5 TB over 47.9 days ≈ **448 GB/day**. A 30-day, all-stream run is ~12.9 TB —
  **~11 days at the real 13.9 MB/s**, against a 168 GB disk. (An earlier revision said ~3 days,
  from the retracted concurrency figure above.) The disk governor is therefore load-bearing, and
  the run duration makes resumability essential rather than convenient.

**Language: Python, deliberately.** Nothing in the data path is Python: extraction is I/O wait,
Zeek is a C++ subprocess, Parquet conversion is DuckDB's C++ engine. Python schedules, governs and
records. The measured ceiling is the appliance API, not the client.

**Therefore, a hard rule: no per-row Python in the data path, ever.** Conversion is
`COPY (SELECT … FROM read_csv(…)) TO … (FORMAT PARQUET)`. A `for line in open(...)` loop in the
converter would rebuild precisely the 10 MB/s bottleneck the Parquet tier exists to remove.

**Zeek runs containerized** (`podman`, already installed; no root needed). Pinned to
`docker.io/zeek/zeek:8.0` (Zeek **8.0.9**), **with `local` loaded** — the manifest must record the
version, and Zeek's log set shifts between releases.

**Superseded (2026-08-06):** this section previously said "only the 16 log types `aximprint` parses
are worth generating." That is wrong twice over. The cold tier is the evidence tier, so a log
discarded at generation time is unrecoverable; and Zeek's *bare default* is itself a trim —
measured against the pinned image, `zeek -r <pcap>` produces 5 of the 16 parsed types while
`zeek -r <pcap> local` produces 10. The five gained are `capture_loss`, `known_hosts`,
`known_services`, `notice` and `stats`, and `capture_loss`/`stats` are the only sources of
`SensorHealthEvent` (measured end-to-end through `aximprint`: 0 health events before, 2 after).
Generate everything; classify, never trim.

## 6. Work orders (proposed sequence)

| # | Work order | Depends on | Status |
|---|---|---|---|
| H-0 | ~~`docs/SPEC_metadata_archive.md` — the format contract, agreed with `aximprint`~~ | — | ❌ **DELETED 2026-08-10** — a cross-repo format contract has nothing left to be a contract *between*; `docs/SPEC_zeek_metadata_archive.md` already exists (aximprint-authored, answering §7.2's old question 2 below by fact) |
| H-1 | Relative time expressions (`"30 days back from now"`, `"2 days from the start"`) | — | ✅ DONE |
| H-2 | Zeek runner: pinned container, log-set policy, one pcap → one log dir | — | ✅ DONE |
| H-3 | Disk governor + spool with backpressure | — | ✅ DONE |
| H-4 | Concurrent pipeline: extract pool → zeek pool → reaper | H-2, H-3 | ✅ DONE, live-verified |
| H-5 | Parquet converter (DuckDB), day repartitioning, `_displacement_seconds` | H-2 | 🟡 **optional, not blocked 2026-08-10** — the four rulings this was blocked on evaporated with the merge (see the status note at the top of this file); the Zeek cold tier is `ingest`'s primary read path today, so this is an internal cache to build if/when it earns its cost, not a coordination point with anyone |
| H-6 | ~~Manifest + atomic publish~~ | ~~H-0, H-5~~ | ❌ **DELETED 2026-08-10** — atomic publish exists to make a continuously-written archive safely readable *by a separate process in a separate repo*; there is no separate repo left. If H-5 is ever built, its internal write discipline is an implementation decision made when it is built, not a cross-repo contract negotiated now |
| H-7 | Resume: an interrupted run continues without re-extracting or re-Zeeking | H-4 (not H-6 — corrected 2026-08-10; H-4's own spool/manifest already gives per-window resume, unrelated to H-6's archive-publish protocol) | 🔴 not started — **note 2026-08-11:** `harvest --from-pcaps` (`docs/WORK_ORDER_harvest_from_pcaps.md`) landed a primitive this can reuse — a per-entry `_ZeekWorkItem.owned: bool` predicate distinguishing pcaps this pipeline may delete from ones it may not, built for exactly H-7's "orphans it owns beside pcaps it doesn't" mixed case. Do **not** model H-7's own non-owned entries by admitting them into a real `Spool` marked non-owning — `Spool.admit()` blocks for backpressure with nothing to release them, which hangs the run rather than degrading it (`LES-081`) |
| H-8 | Packaging, runbook, gated live verification at days-scale | H-1–H-4, optionally H-5 (not "all" — corrected 2026-08-10, H-0/H-6 no longer exist to depend on) | 🔴 not started |

H-1, H-2 and H-3 needed nothing from `aximprint` and were built first. **H-0 and H-6 are not
coordination points any more — they are deleted**, per the status note at the top of this file.

## 7. Open questions — corrected 2026-08-10, see the status note at the top of this file

1. ~~**Publish protocol and late-row policy** (§4) — needs `aximprint`'s ruling.~~ **DISSOLVED.**
   Both questions existed only because `H-6` (atomic publish against a manifest a separate process
   reads) was going to be built. `H-6` is deleted; there is no publish protocol to rule on, and §4
   above is accordingly historical, not a live design surface.
2. ~~**Who writes `SPEC_metadata_archive.md`?**~~ **RESOLVED, by fact.** `aximprint` did:
   `docs/SPEC_zeek_metadata_archive.md` exists in this repo today, demoted from external contract to
   optional internal cache format the day the Zeek cold tier became the primary read path
   (CHANGELOG 2026-08-10).
3. ~~**Where does this tool live?**~~ **RESOLVED.** Neither of the two options this question
   considered — the answer is one repo, `aximprint acquire harvest`, reusing this project's client,
   window planner and time handling exactly as proposed, just not as "a second command alongside
   `xtract`" (there is no more `xtract` command; see CLAUDE.md CLI verbs).
4. **`duckdb` becomes a runtime dependency** here — **still true if H-5 is ever built**, and still
   moot today since H-5 is optional and not being built now. `duckdb` is already a base dependency of
   this repo's discovery pipeline (`pyproject.toml`), so "aligning the version" was never actually a
   coordination problem, just an unexamined assumption that it might be.
5. **First-run scope.** Unchanged, and not currently relevant — applies only once H-5 is picked up.
