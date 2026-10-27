# WORK ORDER — Pluggable feature-vector sink (windows → files)

**STATUS:** LANDED (2026-08-03 — see CHANGELOG "Pluggable feature-vector sink")
**Depends-on:** `WORK_ORDER_streaming_zeek_ingest.md` (needs the event stream at scale) — landed
**Authority:** user (2026-08-03) — "spit out the feature vectors which for now are saved to a file(s)"

## Goal

Give the windowing layer a **pluggable output sink** so a closed window's artifacts land on disk as
feature vectors the next-stage models can consume, without going through the belief store.

Today the only consumer of `WindowResult` is `BeliefStore` (DuckDB), wired directly into
`cli.cmd_ingest`. Model training and the GNN need plain feature files, and the belief store is the
wrong shape for that: it is a *belief* projection, not a feature archive.

## Build

1. **Sink contract + registry**, mirroring the ingest-plugin pattern in `ingest/base.py` (that
   symmetry is deliberate — reuse the shape, do not invent a second one):
   - `name` — sink id (`jsonl` first).
   - `open(destination, run_meta)` / `write_window(WindowResult)` / `close() -> summary`.
   - `@register` decorator + `get_sink(name)`, failing loud on an unknown name.
   - Lives in a new `src/aximprint/features/` layer. **Import direction:** `features` imports
     `windowing` outputs and `events`; nothing imports `features` except the CLI (CONVENTIONS §3).
2. **`jsonl` sink — the five window artifacts of DESIGN_BRIEF §5.4**, one file each, one JSON object
   per line, in a single output directory:

   | File | One row per | Carries |
   |---|---|---|
   | `nodes.jsonl` | (window_start, entity) | the node feature vector — the GNN's node input and the behavioral fitter's observation |
   | `edges.jsonl` | (window_start, src, dst, proto) | the typed conversation graph for the window |
   | `windows.jsonl` | window | the health record — event counts, inherited loss, entities active, resolver ambiguity |
   | `identity.jsonl` | binding delta | new/changed bindings with evidence + intervals |
   | `manifest.json` | the run | source, window params, code build, per-file row counts, event totals, wall-clock |

   `manifest.json` is not optional bookkeeping: the reconciliation loop's first invariant is *the
   same aximprint build on both sides* (§10), and a feature file with no build stamp cannot satisfy
   it. Include enough to reproduce the run.
3. **Streaming write.** Sinks write on window close and hold nothing across windows — a 72-window
   run must not accumulate. Files are opened once, appended per window, closed at the end.
4. **CLI verb** `aximprint features --source <zeek dir> --out <dir> [--sink jsonl] [--window 3600]
   [--subbuckets 12]`. It does ingest → window → sink, and **no belief store**. Existing
   `ingest`/`status`/`synth` verbs keep working unchanged.
5. **Row shape.** Every row carries its `window_start` and its key, so a file is self-describing and
   joinable without positional assumptions (§11 "named fields, not positions" — the reasoning is
   sound now even though the versioning it belongs to is inert).

## Boundaries — do NOT do these

- **No models, no learners, no training.** This work order ends at files on disk. Feature *design*
  is already settled in §5.4 — emit those artifacts, do not invent new ones.
- **No belief-store changes.** `BeliefStore` and the `ingest` verb are untouched. This is an
  additional path, not a replacement.
- **No Parquet, no Arrow, no new dependency.** JSONL only, stdlib `json`. Parquet is a plausible
  second sink later — the registry exists so it can be added without touching consumers, which is
  the point. Do not add it now.
- **No schema versioning** on the row shapes (`LES-001`).
- **No capture contents** in code, tests, docs, or hand-off. Tests use synthetic fixtures
  (`LES-003`).

## Validation

- `scripts/verify_tier.sh` passes.
- **Determinism:** two `aximprint features` runs over the same source produce byte-identical
  `nodes.jsonl` / `edges.jsonl` / `windows.jsonl` / `identity.jsonl`. `manifest.json` may differ
  only in wall-clock fields — and those must be the *only* differing keys. Extend
  `scripts/verify_determinism.py` to cover the sink path, or state why it belongs in unit tests.
- **Real capture:** a full run over `metadata/zeek` produces 72 window rows; report row counts per
  file, output size, and wall-clock. Output goes to a gitignored path — never into the repo.
- Unit tests: registry fail-loud on unknown sink name; all five artifacts written; rows carry their
  keys; a window with zero entities still emits a health row (the asterisk must travel even when
  there is nothing to say); streaming — no cross-window accumulation.

## Findings to report

- Whether the node feature vector as emitted is actually sufficient for a GNN node input, or whether
  §5.4's list has a gap that only shows up once the file exists.
- Output size per window-hour, extrapolated to a week and a month of capture.
- Whether `windows.jsonl` health rows make a degraded window recognizable downstream *without*
  reading the node rows — the "asterisk travels" claim, tested against real data.

## Outcome (2026-08-03)

**Delivered.** `src/aximprint/features/` with the sink contract + registry mirroring
`ingest/base.py`, the `jsonl` sink writing all five §5.4 artifacts, the `aximprint features` verb
(no belief store), a third determinism check in `verify_determinism.py`, and `verify_tier.sh` step 3
exercising it. Real capture: **22,147 rows / 5.5 MB, 31.3 s / 72.7 MiB peak**, 0 of 15,307 edges
dangling, 72 window rows.

**Findings, answered:**

1. **The node vector is *nearly* sufficient — 37 fixed floats** (13 scalars + 3×4 quantiles + 12
   sub-buckets) plus `state_mix`. Two gaps only visible once the file existed: §5.4's "OS hints
   (TTL mode, SMB dialect)" has no field at all, and §5.2's packet-size quantiles are absent
   (`pkts_out`/`pkts_in` reach `FlowEvent` and are never accumulated). Both are upstream in
   `NodeAcc`, not in the sink → `Q7`. A third gap is not a *field* problem: `proto_mix` and
   `listen_ports` are open vocabularies with no pinned index map, so a tensor assembler cannot
   build its first tensor without a full pass over the archive → `Q6`.
2. **Size: ≈ 75 KiB per window-hour** → 12.9 MB/week, 56 MB/month — but that number is an artifact
   of this vantage's 44 entities. The real axis is **~1.9 KB per entity-hour**, so 1,000 entities
   is ≈ 2.1 GB/week worst case. 17 % of the archive is `identity.jsonl` restatement (98.4 % of its
   rows) → `Q9`.
3. **The asterisk travels — and it had to be repaired to.** `windows.jsonl` makes a degraded window
   recognizable without touching node rows. Two fixes were needed to make that true on real data:
   `capture_loss_pct_max` reported `0.0` when *no* evidence had arrived (now `None`), and late
   `SensorHealthEvent`s were attributed to the open window rather than the window they describe.
   `LES-006`. Real-capture result: **every one of the 72 windows carries loss** (5.87 %–24.92 %,
   median 9.17 %) — so downstream cannot simply exclude degraded windows → `Q10`.

**One boundary was tightened, not crossed:** the sink writes a `.gitignore` containing `*` into its
own output directory. A feature archive is capture-derived and the repo's `*.jsonl` rule misses
`manifest.json`. `LES-011`.
