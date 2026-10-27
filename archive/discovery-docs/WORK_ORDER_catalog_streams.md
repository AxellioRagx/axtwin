# Work Order — WO-3: `catalog` and the `xtract streams` command

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and mock-tested (`catalog.py`, `xtract streams` — CHANGELOG 2026-08-04
> "`xtract` iteration-1 build complete"); the live-acceptance gate (§4.3 below) is now satisfied —
> Q1 and Q3 were resolved by the 2026-08-05 live probe (`docs/WORK_ORDER_live_probe.md`, DONE) and
> `xtract streams --json` was run against CorpAPCN, reporting 12 streams with plausible ranges (see
> CHANGELOG 2026-08-05 "Live verification against CorpAPCN"). See `docs/VENDOR_API_DEFECTS.md` for
> the six contract corrections this uncovered along the way (response-wrapper shape, field types,
> the footer-junk record, `streamNN` naming).
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

**This delivers the user's first task.** `xtract streams` reports, for every stream or a named
subset, the first and last timestamp of stored capture data, how many files back that range, how
many bytes, and where the gaps are. After this lands, an operator can answer "what have we got?"
without an appliance login, and `xtract pcap` has a source for resolving an omitted `--end`.

**Not a goal:** extraction. No call to `/axapi/pcap` in this work order.

## 2. Build — phases, in order

### Phase 1 — The stream-spec grammar

- **Files:** `xtract/streamspec.py` (new), `tests/test_streamspec.py` (new)
- **Behavior:**
  - `validate(spec: str) -> None` accepts the API's own grammar: `n[,n][,n-m][,n..m]`, n in
    `0–4294967296`, **both `-` and `..`** as range separators. Rejects malformed input with an
    error naming the offending element.
  - `expand(spec: str) -> list[int]` yields the individual IDs, sorted and deduplicated. Used
    **only** for local cataloguing.
  - `validate`/`expand` are separate on purpose: the extract path sends the caller's original string
    **verbatim**. Nothing in this codebase ever re-emits `2-4` as `2,3,4` — re-spelling a range in
    a different grammar is how an off-by-one gets introduced against someone else's parser.
  - Guard the obvious foot-gun: `expand("0-4294967296")` must not attempt to materialize four
    billion integers. Cap expansion and raise a clear error past a sane ceiling.
- **Tests:** each grammar form; `-` and `..` equivalence; overlapping ranges dedupe; the expansion
  cap fires; a property test that `expand` never reorders or drops a listed ID.

### Phase 2 — Catalog assembly

- **Files:** `xtract/catalog.py` (new), `tests/test_catalog.py` (new)
- **Behavior:**
  - `list_streams(client)` → `GET /axapi/fs/streams`, parsed into records.
  - `stream_range(client, stream)` → `GET /axapi/fs/files?stream=<s>`, returning
    `first = min(time_start)`, `last = max(time_end)`, file count, total bytes. **No endpoint
    returns a stream's range directly** — it is assembled here, and this is the only place that
    assembly happens.
  - Timestamps convert via `timeutil.instant_from_fs_field` (Q1). Never inline.
  - **Gap detection:** sort files by `time_start`; a discontinuity greater than a threshold between
    one file's `time_end` and the next file's `time_start` is a gap. Report count, total gap
    duration, and the largest gap. An empty stream yields no range, not a zero-length one.
  - `Q3`: the mapping from `fs/streams` `name` to numeric stream ID lives behind one function with a
    `# Q3:` comment. If the probe shows names are not decimal IDs, only that function changes.
- **Tests:** ranges from synthetic file listings; out-of-order listings sort correctly; single-file
  stream; empty stream; gap detection finds a known gap and ignores contiguous files;
  malformed/missing `time_start` raises rather than silently skewing the range.

### Phase 3 — The command

- **Files:** `xtract/cli.py` (new), `tests/test_cli_streams.py` (new)
- **Behavior:**
  - `xtract streams [--streams <spec>] [--json] [--gaps]`. With no `--streams`, every stream.
  - Default output is an aligned table: stream, first, last, duration, files, bytes, gaps.
  - `--json` emits machine-readable records — this is what makes the command scriptable, so it is
    a first-class output, not a debug flag.
  - `--gaps` adds a per-stream gap breakdown.
  - Timestamps print in the same string form `xtract pcap` accepts, so an operator can copy a value
    straight from this output into a `--start`. Human-readable UTC is shown alongside, never instead.
  - Exit codes: `0` success, `2` usage error, `3` auth failure, `4` transport/server error. A stream
    with no data is **not** an error.
- **Tests:** table and JSON snapshots against a mocked client; `--streams "2-4"` restricts the set;
  an unknown stream name produces a clear error, not a traceback; exit codes per case.

## 3. Boundaries — explicitly OUT of scope

- No extraction, no windows, no `/axapi/pcap`.
- No caching of catalog results to disk. A stale range silently mis-plans an extraction; if caching
  is wanted later it needs its own invalidation design.
- Do not resolve Q1 or Q3 by inference. Both sit behind one function each, with a `# Q1:` / `# Q3:`
  comment; the probe closes them.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_streamspec.py tests/test_catalog.py tests/test_cli_streams.py`
   green, HTTP mocked. No sockets.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live (gated):** `xtract streams --json` against CorpAPCN returns a plausible range for at least
   one stream, and the reported `first`/`last` are consistent with the raw `fs/files` record
   captured during the live probe. **This is the acceptance evidence** — a range that looks
   plausible but is 1000× off is exactly the Q1 failure mode.
4. **Docs:** CHANGELOG entry (Authority-marked), `AXAPI_CONTRACT.md` cross-checked,
   `doc-stats --check` clean, `LES-NNN` for anything durable

## 5. Depends on / sequence

- **Depends on:** WO-0, WO-1 (`timeutil`), WO-2 (`client`). Q1 and Q3 for DONE status.
- **Unblocks:** WO-6 — `--end` resolution reads this catalog.
