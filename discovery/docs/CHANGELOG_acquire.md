# Design Changelog — `acquire` (formerly `xtract`)

> **Landed here 2026-08-10** from the standalone `xtract` project's own repository
> (`docs/WORK_ORDER_merge_xtract_acquire.md`, D6) when it merged into aximprint as `src/aximprint/acquire/`.
> **Structurally unmodified, per D6** — not restructured, not interleaved into `docs/CHANGELOG.md`'s
> own narrative, so "read this project's history end to end" stays true of both files independently.
> Its `LES-NNN` citations **were** mechanically renumbered (`LES-001`–`029` → `LES-033`–`061`, D5) so
> they resolve against this repo's `docs/LESSONS.md` instead of colliding with it. Its `Q1`–`Q4`
> citations were **not** renumbered — historically accurate as authored (the same discipline that
> lets any dated CHANGELOG entry quote a number that was true then, not now); they refer to xtract's
> own numbering, not this repo's live `docs/OPEN_QUESTIONS.md` `Q1`–`Q18`. See that file's
> "Historical — inherited from `xtract`" section for the `Q1`→`Q19`, `Q3`→`Q21`, `Q4`→`Q22` map
> (`LES-062`). Everything below this line is exactly as `xtract` wrote it.

# Design Changelog

Track **material design decisions** for this project. Implementation fixes (typos, minor parameter tuning, small corrections) don't need entries here — they live in git history. This changelog is for decisions that future me, Claude (chat), and Claude Code should know about.

Format: reverse chronological. Each entry follows the template below.

---

## Entry Template

```markdown
## YYYY-MM-DD — Short descriptive title

**Authority:** design (chat session) | implementation (Claude Code) | user | retrofit

**What changed:**
- Concrete, specific statement of the change.
- Another bullet if needed.

**Why:**
- Rationale; what motivated this decision.

**Affected docs:**
- `path/to/file.md` — which section
- `path/to/another.md`

**Affected code:** [optional — the modules/files this landed in, so a future session can find the implementation from the decision]

**Supersedes:** [optional — if this reverses or replaces an earlier entry, link to it]

**Links:** [optional — issue, PR, chat URL if any]
```

## Where this file sits among the registers

- **`docs/CHANGELOG.md` (this file)** — what *was decided/built*, chronological and Authority-marked. The record of record; don't rewrite history.
- `docs/LESSONS.md` — *actionable rules* extracted from experience (`LES-NNN`), auto-injected into the agents that need them. Entries link back here via `Origin:`.
- `docs/PRODUCT_GAPS.md` — what's *built but incomplete or brittle* (`GAP-NNN`).
- `docs/OPEN_QUESTIONS.md` — *design* topics not yet locked into the specs.

In Pipeline mode the `doc-writer` subagent produces the entry for every change, along with the `LES`/`GAP` entries it earned. A `git commit` is **blocked** while a new entry here has no `**Authority:**` line — the marker is what makes a decision auditable later.

## Keeping this file usable as it grows

This is an append-only decision log, and on a long-running project it gets big — hundreds of KB is normal after a year, at which point nobody (human or model) reads it end to end, and it will no longer fit in a chat export.

**Never rewrite history.** Instead:

- **Archive by year.** Move closed years into `docs/CHANGELOG_ARCHIVE_<year>.md` and link them from the top of this file. The trail is preserved; only the working window stays here.
- **Export the recent window.** `CLAUDE_CHANGELOG_ENTRIES=N` in `.claude-workflow.env` makes `claude-export.sh` bundle only the newest N entries for a chat session (and say so in the opening prompt, so chat knows to ask rather than assume).
- **Don't let counters here be policed.** Dated entries are *supposed* to quote the numbers that were true then — keep this file out of `doc-stats`' `files` list.

## Authority Markers — Meaning

| Marker | Meaning | When to use |
|---|---|---|
| `design (chat session)` | Decision made during a Claude chat session | Architectural, scope, cross-cutting decisions |
| `implementation (Claude Code)` | Discovered/decided during Claude Code implementation | Technical constraints found while building |
| `user` | User-authored decision (outside any AI session) | Manual edits, external inputs |
| `retrofit` | Retrospective entry inferred from prior docs/code/history | Used during initial adoption of the workflow on an existing project; lower confidence than live decisions |

The authority marker tells future-Claude (chat) which changes are settled implementation facts (preserve them) versus design proposals (open for revision). `retrofit` entries are the weakest form — they reflect the author's best guess at what was decided, and can be superseded by any later entry with a stronger authority.

---

<!-- BEGIN CHANGELOG ENTRIES — newest first -->

## 2026-08-07 — H-4: the concurrent extract → Zeek → reap pipeline

**Authority:** implementation (Claude Code)

**What changed:**

- `xtract/harvest/pipeline.py` (new) wires H-1/H-2/H-3 into a working tool, and `xtract harvest` is
  now a real CLI subcommand: plan windows (`timespec` + `windows.plan`) → N concurrent extract
  workers (`spool.admit` → `extract_window` → `spool.commit`) → M Zeek workers (`spool.release(...,
  delete=True)` on `ZeekResult.ok`, and *only* then) → a `RunReport`. Preflight (Phase 1) fails fast
  on `zeek_runner.probe()`, an unwritable spool root, unresolved credentials, or an uncomputable
  window plan — before any extraction starts. SIGINT stops admitting, lets in-flight Zeek runs
  finish, orphans no containers, and flushes the report. Phase 5 re-drives orphaned pcaps a prior
  interrupted run left resident through Zeek rather than reaping them, since extraction is the
  expensive half of the pipeline.
- `check_drift()` compares `spool.verify()`'s accounted-vs-on-disk totals once, at the very end of a
  run, and raises `SpoolDriftError` on disagreement — not on a fixed interval, because the two totals
  are expected to disagree while anything is `RESERVED`, which is most of a concurrent run (see
  `GAP-013`, filed below, for the consequence).
- The live run also exposed a compounding failure the design hadn't bounded: three independently
  correct behaviors (small garbage pcaps → the estimator believed windows were cheap → admission kept
  granting; every Zeek failure correctly retained its pcap) accumulated 79.6 MB of unreleasable
  garbage against a 60 MB cap. Retained-failure bytes are now bounded to a configurable fraction of
  the spool cap (`HarvestConfig.retained_failure_fraction`, default 50%); exceeding it raises
  `RetainedFailureLimitExceededError` instead of silently wedging the run for its remainder.
- **Live-verified end to end** against the real appliance and the pinned Zeek image: 5 windows
  planned/extracted/zeeked/released, zero failures, peak spool 158 MB against a 250 MB cap, 238.8 MB
  of pcap reduced to 2.1 MB of Zeek metadata, no pcaps left on disk. Feeding that cold tier to
  `aximprint`'s real `ZeekPlugin` parser yields 5,052 events, ts-ordered across all five windows, zero
  malformed rows.
- Test count 620 → 679 (see also the entry immediately below — same commit `1ec6131`, same final
  count; the concurrency defect fix and this pipeline landed together).

**Why:**

- H-4 is the point where H-1/H-2/H-3 stop being three separately-tested components and become a tool
  that actually walks a time range. The live run is what proves the invariant the whole design exists
  to protect — "delete a pcap only after its consumer reports success" — holds under real concurrency
  and real appliance behavior, not just under `FakeZeekRunner`.

**Affected docs:** `docs/WORK_ORDER_pipeline.md` (marked DONE below), `docs/PLAN_metadata_harvester.md`
§5/§6, `docs/SESSION_STATE.md`

**Affected code:** `xtract/harvest/pipeline.py` (new), `xtract/cli.py` (`harvest` subcommand),
`tests/test_pipeline.py` (new), `tests/test_cli_harvest.py` (new)

## 2026-08-07 — `GET /axapi/pcap` returns 200 with an error body under concurrency; extraction is single-flight

**Authority:** implementation (Claude Code)

**What changed:**

- A repeat of the H-4 concurrency benchmark, this time validating every response's leading 4 bytes
  against the known capture-container magic numbers before counting it as payload, found that the
  appliance serves **exactly one extract at a time**. Every concurrent `GET /axapi/pcap` request
  beyond the first still returns HTTP **200**, but with a body of `"Error starting listen thread,
  shutting down thread"` repeated to fill the response — one observed instance was 57 MB of it, with
  no HTTP-level signal distinguishing it from a real capture. Measured, validating each response:
  concurrency 1/2/4/8 → 1/1/1/1 valid pcaps and 0/1/3/7 corrupt responses.
- `xtract/extract.py`'s `extract_window` now validates the leading 4 bytes of every 200 response
  against `PCAP_MAGICS` (the five legitimate capture-container magic numbers). A mismatch is recorded
  as `Failed`, **never** `Empty` — conflating the two would let a broken extract be recorded as a
  quiet window and never retried. The zero-byte-body check runs first: an empty body has no magic
  bytes to find, and is the genuine, ordinary no-data case (`LES-035`), not an error.
- `DEFAULT_EXTRACT_CONCURRENCY` drops from **8 to 1** — concurrency does not parallelize this API at
  all; requesting more than one extract at a time only manufactures corrupt output, at a rate matching
  `(concurrency − 1) / concurrency`.
- 34 pre-existing tests were passing against fixture response bodies like `b"pcap-bytes"` — real bytes
  moving through the plumbing, never validated as an actual capture. All now build their bodies
  through one shared magic-prefixed helper in `tests/conftest.py`.
- Retained Zeek-failure bytes (pcaps kept resident because Zeek failed on them, correctly, per the
  spool's one invariant) are now bounded to a configurable fraction of the spool cap — see the H-4
  entry above for what this closes.
- Recorded for the vendor as `docs/VENDOR_API_DEFECTS.md` §S2 (severity: security/data-integrity — no
  HTTP-level client can detect this without inspecting the payload).

**Why:**

- The earlier concurrency benchmark (`docs/PLAN_metadata_harvester.md` §5, recorded 2026-08-06)
  counted response bytes without validating them and reported near-linear scaling to 50.3 MB/s at
  concurrency 8 — it was measuring the throughput of an error message. The real figure is **~13.9
  MB/s, single-flight**, and the 30-day all-stream harvest estimate moves from ~3 days back to ~11.
  Finding this by running the real pipeline against the real appliance, rather than trusting the
  earlier benchmark, is the same discipline `LES-048` names for a vendor doc — applied here to a
  self-authored measurement instead.

**Affected docs:** `docs/PLAN_metadata_harvester.md` §5 (throughput figure and 30-day estimate
corrected), `docs/VENDOR_API_DEFECTS.md` (§S2, new), `docs/SESSION_STATE.md`

**Affected code:** `xtract/extract.py` (`PCAP_MAGICS`, `extract_window` check ordering),
`xtract/harvest/pipeline.py` (`DEFAULT_EXTRACT_CONCURRENCY`, `RetainedFailureLimitExceededError`,
`retained_failure_fraction`), `tests/conftest.py`, `tests/test_extract.py`, `tests/test_cli_pcap.py`,
`tests/test_runner.py`

**Supersedes:** the concurrency benchmark recorded in `docs/PLAN_metadata_harvester.md` §5 (landed in
commit `02d4743`, 2026-08-06 — "1 → 0.8 MB/s, 4 → 19.3 MB/s, 8 → 50.3 MB/s aggregate," and the ~3-day
30-day-all-stream estimate it implied), and the `DEFAULT_EXTRACT_CONCURRENCY = 8` default that
benchmark set. That earlier figure was never itself ratified via its own CHANGELOG entry — it lived
only in the plan document — so there is nothing to mark superseded there beyond the plan text itself,
which now carries this correction in place.

## 2026-08-06 — harvest foundations: `timespec`, containerized Zeek runner, disk governor

**Authority:** implementation (Claude Code)

**What changed:**

- Work orders H-1 (`docs/WORK_ORDER_timespec.md`), H-2 (`docs/WORK_ORDER_zeek_runner.md`), and H-3
  (`docs/WORK_ORDER_disk_governor.md`) landed together in commit `62013a0` — built in parallel, since
  none of the three depends on the others in-tree, then code-reviewed (see "Code review and fix pass
  over H-1/H-2/H-3" below), then live-verified against a real Zeek container. 620 tests, `ruff check .`
  clean, no test opens a socket or invokes `podman`.
- **H-1 — `xtract/timespec.py`.** A deliberately small, closed grammar: absolute epoch, `now`,
  `<duration> back from now`/`ago`, and `<duration> from the start` (valid for `--end` only — using it
  for `--start` is a named-circularity error, not a silent fallback). Spelled-out duration forms
  (`"30 days"`, `"2 hours"`) delegate to `timeutil.parse_duration` rather than reimplementing unit
  arithmetic. No module in `xtract/` reads the clock: `now` is a required `Instant` keyword argument
  the CLI resolves once, at startup.
- **H-2 — `xtract/harvest/zeek.py`.** `ZeekRunner` protocol, `ZeekResult`, `FakeZeekRunner` (shipped
  as production code, not a test helper — every later stage needs a testable Zeek on a host where
  Zeek and the pinned `podman` image aren't guaranteed present), and `PodmanZeekRunner`, pinned to
  `docker.io/zeek/zeek:8.0` (Zeek **8.0.9**, digest `sha256:51a0d106…`; a bare `:latest`/tagless image
  is refused outright). `probe()` gives `harvest` a startup preflight instead of a first-pcap-in
  failure an hour into a run.
- **H-3 — `xtract/harvest/spool.py`.** `Spool` reconciles resident bytes from disk at startup (a
  resumed run doesn't start from zero), tracks a `RESERVED → RESIDENT → released` state machine,
  blocks in `admit()` under backpressure with two explicit deadlock guards, and estimates the next
  window's size conservatively (erring high, since under-estimating overshoots the cap and
  over-estimating only costs throughput).
- See "Zeek pinned to 8.0.9 with `local` loaded" below for the log-set policy decision this build
  produced, and "Code review and fix pass over H-1/H-2/H-3" for the seven defects the review found and
  fixed before this landed.

**Why:**

- `docs/PLAN_metadata_harvester.md` §6 sequences H-1/H-2/H-3 first specifically because none needs
  live appliance access or the other two to build — landing all three together clears the foundation
  for H-4 (the concurrent pipeline, wiring these to the existing extraction path) without spending any
  gated/live budget on components that are pure or mockable.

**Affected docs:** `docs/WORK_ORDER_timespec.md`, `docs/WORK_ORDER_zeek_runner.md`,
`docs/WORK_ORDER_disk_governor.md` (all marked DONE below), `docs/PLAN_metadata_harvester.md`

**Affected code:** `xtract/timespec.py` (new), `xtract/harvest/__init__.py` (new),
`xtract/harvest/zeek.py` (new), `xtract/harvest/spool.py` (new), `xtract/timeutil.py` (small
addition), `tests/test_timespec.py` (new), `tests/test_zeek_runner.py` (new), `tests/test_spool.py`
(new), `tests/data/reference_conn_header.log` (new)

## 2026-08-06 — Zeek pinned to 8.0.9 with `local` loaded

**Authority:** implementation (Claude Code)

**What changed:**

- `PodmanZeekRunner` invokes the pinned image (`docker.io/zeek/zeek:8.0`, Zeek 8.0.9) with Zeek's
  `local` script set loaded, not Zeek's bare default. `_SCHEMAS` in `xtract/harvest/zeek.py` was
  re-derived field-for-field from that image's real output — 6 stems directly (`conn`, `dns`, `ssl`,
  `ntp`, `weird`, `packet_filter`), the remaining 13 shared stems cross-checked against it, and `dhcp`
  still transcribed from Zeek's documented default shape (no DHCP traffic in the verification capture
  — see `GAP-014` below for the stems still unobserved against the pinned image specifically).
- The version question resolved differently than expected: `aximprint`'s reference capture had *more*
  `ssl` fields (19) than the pinned image's bare output (18), which looked like a newer Zeek. It
  wasn't — `ssl.validation_status` comes from a policy script, and loading `local` reproduced the
  reference capture's shape exactly.

**Why:**

- `docs/WORK_ORDER_zeek_runner.md` §3 Phase 3 instructed defaulting to Zeek's default script set, on
  the reasoning that anything else is a lossy trim relative to the cold tier's job as the evidence
  tier. Measured against the pinned image, Zeek's *bare* default is itself a trim: `zeek -r <pcap>`
  produces 5 of `aximprint`'s 16 parsed log types; `zeek -r <pcap> local` produces 10. The five
  gained — `capture_loss`, `known_hosts`, `known_services`, `notice`, `stats` — matter beyond the
  count: `capture_loss` and `stats` are the **only** sources of `SensorHealthEvent`. Measured end to
  end through `aximprint`'s own parser: **0 health events before, 2 after**. An archive built to the
  original instruction would have carried zero sensor-health evidence, permanently, because the cold
  tier is the evidence tier and a log not generated at capture time is unrecoverable.

**Affected docs:** `docs/WORK_ORDER_zeek_runner.md` §3 Phase 3 (marked superseded in place, not
rewritten — see that file), `docs/PLAN_metadata_harvester.md` §5 (already reflects `local`-loaded, all
13 shared log schemas re-derived)

**Affected code:** `xtract/harvest/zeek.py` (`DEFAULT_ZEEK_IMAGE`, `DEFAULT_SCRIPT_POLICY`,
`_SCHEMAS`)

**Supersedes:** `docs/WORK_ORDER_zeek_runner.md` §3 Phase 3's "default to Zeek's default script set"
instruction — see that file for the in-place annotation pointing back to this entry.

## 2026-08-06 — Code review and fix pass over H-1/H-2/H-3

**Authority:** implementation (Claude Code)

**What changed:**

- A full code review of the H-1/H-2/H-3 build reproduced seven must-fix defects, four of them by
  execution, and fixed all seven with regression tests. Test count went from 549 to 620.
- **A resumed run hung forever.** `Spool` reconciles pre-existing pcaps against the cap on startup but
  exposed no way to enumerate what it had inherited, so nothing could ever release those bytes.
  Because the governor deliberately keeps the spool near its cap, every interrupted run restarts
  near-full, and the first `admit()` parked on an untimed wait neither existing deadlock guard could
  break. Fixed with `resident_windows()` (an enumerable accessor), a third guard ("nothing changed and
  nothing in flight" is as dead as "nothing tracked"), and a stall timeout.
- **`release()` freed accounting before deleting, outside the lock.** A woken waiter could re-admit
  the same window id, receive the same path back from a subsequent extract, and start writing before
  the original unlink landed — deleting a live extraction mid-write. Delete now happens first, under
  the lock; a failed unlink leaves the entry `RESIDENT` and retryable instead of silently forfeiting
  the bytes.
- **`admit("../outside/important")` escaped the spool root**, and `release()` deleted the file there —
  reproduced by execution. Window ids are now validated (no path separators / `..` segments), and the
  constructed path is asserted to resolve directly under `root` before any delete.
- **The Zeek runner raised on three ordinary paths** (missing pcap, `podman` absent from `PATH`,
  `OSError(EAGAIN)` from `fork()` under memory pressure) despite a stated contract of "a failed run is
  a value, never an exception" — each would have aborted a multi-day pipeline thread. All external I/O
  now returns a failed `ZeekResult` carrying only the exception's type name and the bare path, never
  `str(exc)` (`LES-039` — embeds filesystem paths into what becomes manifest/report content).
- **`resolve_start("30000 days ago")` produced a negative epoch that formatted cleanly onto the
  wire.** `parse_instant` already rejects an out-of-range value on input, but the grammar was closed
  on syntax and open on value for anything produced by *arithmetic*. Now range-checked against
  `timeutil`'s existing plausibility window rather than a second one.
- **Four bare `ValueError`s escaped the `XtractError` hierarchy** — fixed so `cli.py`'s single
  `except XtractError` handler keeps catching every known `xtract` error, consistent with `LES-041`.
- **`FakeZeekRunner` could not fail at all** — `returncode` was hardcoded `0` and `conn.log` was
  always written. H-4's single most important behavior (a failed Zeek run must not release its pcap)
  was therefore untestable, and any consumer doing `result.logs["conn"]` would have passed every test
  and `KeyError`'d on the first quiet window — which `LES-035` establishes is the common case here.
  The fake now models failure, timeout, partial and empty output; a shared, parametrized conformance
  suite runs both `FakeZeekRunner` and `PodmanZeekRunner` through identical assertions so they cannot
  drift apart again.
- Two tests were also found passing vacuously (fixed alongside, not counted among the seven): the
  backpressure test's assertions held even if `admit()` ignored the cap entirely, and an 8-thread
  stress test peaked at 800 bytes against a 4000-byte cap, so it never actually contended or exercised
  concurrent unlink.

**Why:**

- Every one of the seven defects is a silent-success or resource-leak class — none would fail a naive
  smoke test, only a real multi-day batch run (a resumed run after an interruption, a path-traversal
  window id, a transient `fork()` failure, an extreme relative-time expression, a genuinely failing
  Zeek run). Catching them now, against mocks and targeted execution, is strictly cheaper than
  catching them live.

**Affected docs:** `docs/LESSONS.md` (`LES-052`–`LES-061`, below)

**Affected code:** `xtract/harvest/spool.py`, `xtract/harvest/zeek.py`, `xtract/timespec.py`,
`xtract/errors.py`, plus regression tests across `tests/test_spool.py`, `tests/test_zeek_runner.py`,
`tests/test_timespec.py`

## 2026-08-05 — `xtract streams` output: human-readable sizes, stable sort, quiet `--insecure`

**Authority:** user

**What changed:**
- The table's `bytes` column renders as `2.28 TB` / `858.08 GB` instead of a raw count.
  **`--json` still carries the exact integer** — the table is for humans, the JSON is the
  machine-readable path and must not lose precision to a rounded display string.
- Sizes use **decimal** (1000-based) units, not binary. The appliance reports its own
  capacity in decimal TB ("20.538 TB of 20.958 TB available"), so an operator comparing
  `xtract streams` against the box's own figures gets numbers that reconcile rather than
  ones ~10% adrift.
- Rows are sorted by numeric stream id. The appliance returns streams in arbitrary order
  (observed: `stream08`, `stream04`, `stream10`, …), which made the table tedious to scan
  and two runs hard to diff. `stream10` sorts after `stream9`, not lexically; unparseable
  names sort last but deterministically.
- `--insecure` now prints **one** concise stderr line and suppresses the per-request repeats
  (`urllib3`'s `InsecureRequestWarning` plus our own construction-time warning), which were
  burying the table. New `client.silence_insecure_tls_warnings()`, called only when
  `--insecure` was passed; library callers keep full default warning behavior. `urllib3` is
  imported inside `client.py` to keep "only `client.py` imports the HTTP stack" intact.

**Why:**
- Reported from real use against CorpAPCN: 12 streams in arbitrary order, byte counts up to
  13 digits, and three warning blocks per invocation.
- The risk `--insecure` carries is still stated — once, on stderr, where it belongs. Passing
  the flag is already an explicit opt-in; repeating the warning per request tells the
  operator nothing new.

**Affected docs:** `README.md` — `xtract streams` example output

**Affected code:** `xtract/cli.py` (`_human_bytes`, `_stream_sort_key`, sort in `cmd_streams`,
insecure notice), `xtract/client.py` (`silence_insecure_tls_warnings`), `tests/test_cli_streams.py`

## 2026-08-05 — `--host` is optional when the config profile supplies one

**Authority:** user

**What changed:**
- `--host` is no longer a required flag. `xtract` takes the host from the selected config
  profile's `host` key when the flag is omitted; an explicit `--host` still wins, so one
  profile can be aimed at a standby appliance without editing it.
- New `auth.resolve_host(config_path=, profile=)` — deliberately separate from
  `resolve_credential`, since a host is not a secret and needs none of that function's
  mode-checking, redaction or TTY-gating.
- New `cli.UsageError`, mapped to exit `2`. Host resolution happens inside the client
  factory (a host is only needed when a client is actually built), which is deeper than the
  inline `return 2` sites, so it needed a typed error to carry the exit code back.

**Why:**
- The profile already had a `host` key and it was purely decorative — `resolve_credential`
  read the profile for credentials only and the CLI never consulted it, so every invocation
  repeated an address the config already knew. Reported from real use.
- `--host` is deliberately *not* marked `required` in argparse: argparse's own message could
  only ever mention the flag, never the profile route. The error now names both.
- Host resolution stays inside the client factory rather than moving into `main()`, so
  `xtract pcap --dry-run` with an explicit `--end` keeps working with no host, no config and
  no credentials — that path is a deliberate feature and an unconditional check would break it.

**Affected docs:**
- `README.md` — new "Choosing the appliance" section; examples now use `--profile`
- `docs/RUNBOOK_CORPAPCN.md` — invocations no longer need `--host`

**Affected code:** `xtract/auth.py` (`resolve_host`), `xtract/cli.py` (`_resolve_host_or_fail`,
`UsageError`), `tests/test_cli_streams.py`

## 2026-08-05 — Gap-detection threshold defaults to 2s

**Authority:** user

**What changed:**

- `catalog.stream_range`'s gap detector now floors what counts as a storage gap at 2 seconds
  (`catalog.DEFAULT_GAP_THRESHOLD`), instead of flagging any discontinuity greater than zero. Callers
  can still pass `gap_threshold=0` to see the raw, unfiltered view.

**Why:**

- `fs/files` timestamps are second-resolution (Q1/Q2, below). An hourly file ending at second `T` and
  its successor starting at `T+1` read as a 1-second gap from rounding alone, with no real
  discontinuity in the underlying capture. Measured live on `stream00`: with `gap_threshold=0`, the
  scan reported **125 gaps totalling 4876s** — 124 of them exactly 1s (rounding artifacts) and one
  real ~4752s outage. With the 2s floor, the same stream reports **1 gap of 4752s** — the one
  discontinuity that actually matters to an operator. A threshold of zero technically isn't wrong,
  but it drowns the one real gap in 124 false ones.

**Affected docs:** `docs/AXAPI_CONTRACT.md` §5.3, `CLAUDE.md` (no change needed — gap detection isn't
described at that level of detail there)

**Affected code:** `xtract/catalog.py` (`DEFAULT_GAP_THRESHOLD`, `stream_range`)

## 2026-08-05 — Manifest records the logical window end, not the wire `end_time`

**Authority:** user

**What changed:**

- `manifest.jsonl`'s per-window `end` field now stores the window's **logical** end — the value
  `windows.plan()` computed, formatted by `format_instant` — never the transport-adjusted `end_time`
  string actually sent to `GET /axapi/pcap` (which, per the Q4 entry below, may be `end − 1ns` when
  `Q4_END_TIME_INCLUSIVE` is true). The Q4 adjustment is applied only at request-construction time,
  in one place, and never leaks into the record that resume logic reads back.

**Why:**

- Without this separation, flipping `Q4_END_TIME_INCLUSIVE` from its prior default (assumed
  exclusive) to `True` (measured inclusive, see below) would have changed what "end" meant in every
  future manifest record relative to the meaning baked into pre-existing ones — every resume against
  an old manifest would misread its own prior windows as clipped short and silently trigger a full
  re-extract (`LES-037`'s "clipped-window resume" path, firing on a transport-encoding change instead
  of a real `--end` change). Decoupling the resume key from the wire encoding closes that off
  structurally, not just for this particular Q4 flip.
- This touches the manifest format, which `CLAUDE.md` "Design Document Maintenance" names as one of
  the two version-locked contracts this project owns (alongside the output naming scheme) — normally
  a risk-gated, confirm-first change. No deployed manifest existed anywhere (iteration 1 had never run
  live before this session), so there was nothing to migrate and the change was free to make now,
  before the first real manifest is written for keeps.

**Affected docs:** `CLAUDE.md` "Design Document Maintenance" (no wording change needed — this stays
inside the existing version-locked-contract rule), `docs/WORK_ORDER_runner_pcap.md` §2 Phase 2

**Affected code:** `xtract/manifest.py` (`WindowRecord.end`), `xtract/runner.py` (writes the logical
end, not the wire `end_time`)

**Supersedes:** nothing ratified — no manifest schema had shipped to a real run before this.

## 2026-08-05 — Q1–Q4 resolved by live probe; six vendor-contract corrections applied

**Authority:** implementation (Claude Code)

**What changed:**

- All four wire-contract questions `docs/OPEN_QUESTIONS.md` opened on 2026-08-04 are now answered by
  direct measurement against CorpAPCN (PacketXpress / Axellio Xpress Platform 5.1.0):
  - **Q1** — `fs/files` `time_start`/`time_end` are unix epoch **seconds**, delivered as **strings**
    (the documented `integer` type was wrong; the assumed unit was right). Confirmed by cross-checking
    `"1781795842"` against the same record's own `date` field.
  - **Q2** — `/axapi/pcap` accepts sub-second timestamp strings and honors them precisely, but a
    **zero** fractional part is treated as covering the entire second rather than as an exact bound.
  - **Q3** — `fs/streams` directory names are **not** bare decimal stream IDs; they are `streamNN`
    (`stream00`…`stream11`). Apparent mapping `streamNN` ↔ `N`, consistent with observation but not
    exhaustively proven per-stream.
  - **Q4** — `end_time` is **inclusive**, and (per Q2) a zero fractional part widens that inclusivity
    to the whole second. `windows.Q4_END_TIME_INCLUSIVE` is now `True`. Verified live, twice: first
    that the boundary duplication predicted by "inclusive" actually occurred (a 60s extract with
    `end_time` sent unadjusted returned 7,058 packets; the same range split into two adjacent
    30s windows sent with the correction returned 4,328 + 2,653 = 6,981 packets — the 77-packet
    difference is exactly the previously-duplicated final second); second, after applying the fix,
    that two adjacent 30s windows (`…195.002029→224.993575` then `…225.025223→254.998116`) no longer
    share any timestamp.
- Alongside the four questions, the probe surfaced **six defects in both vendor PDFs**, all measured
  and worked around in code — full evidence in the new `docs/VENDOR_API_DEFECTS.md`:
  1. **`POST /axapi/login` returns 200, not the documented 201.** `LES-034` amended in place (see
     below) rather than superseded outright — client now accepts both 200 and 201.
  2. **`fs/streams` and `fs/files` return bare JSON arrays**, not the documented
     `{"stream": [...]}` / `{"files": [...]}` wrapper objects.
  3. **Stream directory names are `streamNN`**, not bare integers (Q3, above).
  4. **`time_start`/`time_end`/`epoch`/`seqnum`/`permissions` are strings**, not the documented
     integers (`permissions` is additionally formatted inconsistently between the two endpoints:
     `"0444"` from `fs/streams` vs. `"444"` from `fs/files`).
  5. **A disk-usage footer line is returned as a fake, field-shifted file record** (`time_start:
     "available,"`) on `fs/files`, and as a phantom stream named `TB` on `fs/streams` — a naive
     conforming client parsing the documented types crashes on it; `catalog.py` now filters it
     structurally (see `LES-049`).
  6. **`end_time` inclusivity** — Q4, above.
- `docs/AXAPI_CONTRACT.md` is updated section-by-section to describe **observed** behavior where it
  disagrees with the vendor PDFs, with the documented (wrong) shape kept alongside and marked, and a
  pointer to `docs/VENDOR_API_DEFECTS.md` for the evidence. Its version/status line now says it is
  partly measured, not purely transcribed.

**Why:**

- Every one of the six defects above passed the full 360-test mock suite built against the vendor
  PDFs — mocks encode the spec's own errors, not reality (see `LES-048`). The only way to close
  Q1–Q4 responsibly, and the only way any of the six defects could have been found, was a live probe.

**Affected docs:** `docs/OPEN_QUESTIONS.md` (Q1–Q4 marked resolved, linked here),
`docs/AXAPI_CONTRACT.md` (§1.1, §5.1–§5.3, §6.1, §10 — sections corrected; version/status line
bumped), `docs/VENDOR_API_DEFECTS.md` (new — the evidence record), `docs/LESSONS.md` (`LES-034`
amended; `LES-048`–`LES-051` added), `docs/WORK_ORDER_catalog_streams.md` (WO-3, marked DONE),
`docs/WORK_ORDER_runner_pcap.md` (WO-6, marked DONE), `docs/WORK_ORDER_live_probe.md` (marked DONE),
`docs/PRODUCT_GAPS.md` (`GAP-001` reclassified, `GAP-002` closed, new gap filed for untested
endpoints)

**Affected code:** none in this entry beyond what's already applied — `timeutil.py`
(`FS_FIELD_NS_PER_UNIT`), `catalog.py` (`stream_id_for_name`, footer-junk filtering), `windows.py`
(`Q4_END_TIME_INCLUSIVE`), `client.py` (login accepts 200 or 201) all carry the resolved constants
and the code changes that this entry documents.

**Supersedes:** `LES-034`'s original "201, not 200" rule — amended in place below, not deleted, per
`docs/LESSONS.md`'s never-renumber/never-delete discipline. The underlying assumption traces back to
`docs/AXAPI_CONTRACT.md` §1.1 as transcribed in the 2026-08-04 "`xtract` design ratified" and
"iteration-1 build complete" entries; those entries are not rewritten (they correctly recorded what
the PDFs said at the time), but the runtime behavior they informed is now corrected.

## 2026-08-05 — Live verification against CorpAPCN — `xtract streams` and `xtract pcap` both work

**Authority:** implementation (Claude Code)

**What changed:**

- Both `xtract` commands were run against the real CorpAPCN appliance (PacketXpress / Axellio Xpress
  Platform 5.1.0) for the first time. Both work:
  - `xtract streams` reported 12 streams (`stream00`…`stream11`), each holding on the order of ~48
    days of capture data.
  - `xtract pcap` extracted a 60-second window: 2,866,041 bytes, 7,058 packets, a valid
    nanosecond-resolution classic pcap (linktype 1), exit code 0, in ~3.2s. The manifest was checked
    and confirmed free of hostname and credential material, and `X-Task-GUID` was captured on the
    response.
- This closes the live-acceptance criterion both WO-3 and WO-6 were explicitly gated on (see their
  own entries below and their updated Status lines) — iteration 1 is now proven end-to-end on real
  hardware, not only against mocks.

**Why:**

- This was the single remaining next step recorded in `docs/SESSION_STATE.md` since the 2026-08-04
  build. Everything in the `xtract` package had been implementation-complete and mock-green since
  then; only the live probe and the two live-acceptance criteria stood between "built" and "done."

**Affected docs:** `CLAUDE.md` (Current State), `docs/WORK_ORDER_catalog_streams.md` (WO-3),
`docs/WORK_ORDER_runner_pcap.md` (WO-6), `docs/WORK_ORDER_live_probe.md`,
`docs/WORK_ORDER_packaging_runbook.md` (WO-7, partial), `docs/RUNBOOK_CORPAPCN.md`,
`docs/SESSION_STATE.md`

**Affected code:** none — this entry records a verification run; the six defect fixes and Q1–Q4
constant flips it depends on are recorded in the entry immediately below.

## 2026-08-04 — `xtract` iteration-1 build complete (mock-verified only — never run against CorpAPCN)

**Authority:** implementation (Claude Code)

**What changed:**

- Eight work orders (`docs/WORK_ORDER_*.md`) executed in dependency order, then a full code review
  and a fix pass. The `xtract` package now exists: `timeutil` (int-ns instants, wire-string
  formatting), `errors` (the `XtractError` hierarchy every other module's exceptions reparent
  under), `auth` (credential resolution: flag path → env → profiled `config.toml` → TTY prompt →
  loud failure), `client` (the only module importing `requests`: session, login/refresh/logout,
  retries, status-to-exception mapping, `list_api_keys`/`revoke_api_key`), `streamspec`
  (`stream_ids` grammar validate/expand), `catalog` (`fs/streams` + `fs/files` → per-stream ranges
  and gap detection), `windows` (pure `[t, t+interval)` planner), `extract` (one window → one
  `.pcap.part` → `rename()`), `manifest` (`manifest.jsonl` header + append-only records), `runner`
  (collision guard, resume planning, the batch loop), and `cli` (`xtract streams`, `xtract pcap`,
  `argparse`-based, no float ever touches a wire timestamp).
- `README.md`, `docs/AXAPI_CONTRACT.md` (the version-locked `axapi` 5.1.0 transcription, WO-0), and
  `docs/RUNBOOK_CORPAPCN.md` (the gated live-verification procedure) written. `tests/conftest.py`
  added late (see the fix-pass entry below) to start absorbing shared test fixtures.
- 360 tests pass, `ruff check .` is clean, `bash scripts/verify_tier.sh` is green,
  `python3 scripts/doc-stats.py --check` is clean. Nothing was committed by the pipeline run itself.
- **`docs/WORK_ORDER_live_probe.md` was NOT run** — no CorpAPCN hostname, credentials, or
  permission were available in this run, and none of it was invented. Consequently:
  - `docs/OPEN_QUESTIONS.md` Q1–Q4 remain **OPEN**.
  - WO-3 (`catalog`/`xtract streams`) and WO-6 (`runner`/`xtract pcap`) are built and tested against
    mocks but stay **NOT DONE** — both depend on Q1–Q4 per their own acceptance criteria.
  - WO-0, WO-1, WO-2, WO-4, WO-5 are marked **DONE**. WO-7 is marked **DONE for phases 1–3**
    (packaging, README, runbook); its phase 4 (this documentation pass) is this entry, and its live
    acceptance criterion is deferred to the live probe along with WO-3/WO-6.
  - Every wire-contract assumption Q1/Q3/Q4 would settle is isolated behind exactly one named
    constant, each carrying a `# Qn:` comment: `timeutil.FS_FIELD_NS_PER_UNIT` (Q1),
    `catalog.stream_id_for_name` (Q3), `windows.Q4_END_TIME_INCLUSIVE` (Q4). Closing any one of them
    is a one-line change, not an audit. Q2 (whether `/axapi/pcap` accepts both integer and
    sub-second timestamp strings) has no code-side assumption to isolate — `xtract` always sends
    whatever precision the caller supplied, so Q2 only affects what an operator may pass on the
    command line, not internal logic.

**Why:**

- The work orders were written, in the prior CHANGELOG entry, specifically so the bulk of the build
  (timeutil/client/windows/extract/runner/cli, all pure-logic-or-mocked-HTTP) could proceed without
  CorpAPCN access, while the four facts nobody could answer from the vendor PDFs stay fenced off
  behind a single gated probe rather than guessed at piecemeal across five modules.

**Affected docs:**

- `CLAUDE.md` — Current State (code now exists; update "not written yet" list)
- `docs/WORK_ORDER_*.md` — WO-0, WO-1, WO-2, WO-4, WO-5, WO-7 (phases 1–3) marked DONE; WO-3, WO-6
  left NOT-DONE with the Q-dependency called out
- `docs/SESSION_STATE.md` — rewritten for this handoff

**Affected code:** `xtract/` (all modules), `tests/` (all test modules + `conftest.py`),
`README.md`, `docs/AXAPI_CONTRACT.md`, `docs/RUNBOOK_CORPAPCN.md`, `pyproject.toml`

## 2026-08-04 — Code review + fix pass: five must-fix defects, six quality findings

**Authority:** implementation (Claude Code)

**What changed:**

- A full code review of the WO-0…WO-7 build reproduced five must-fix defects and six quality
  findings; every one was fixed with a regression test written first. Test count went from 324 to
  360 as a direct result.
- **Resume compared only a window's `start`, not the full record.** `out/<startTs>.pcap` names only
  a window's start, so a window an earlier run clipped short (a smaller `--end` at the time) was
  indistinguishable by filename from the full-length window a later, larger `--end` now plans for
  that same start. Fixed in `runner.plan_resume` — see the "clipped-window resume" entry below for
  the user-ratified behavior this produced.
- **`_refresh()` no longer assumes an undocumented response body.** `docs/AXAPI_CONTRACT.md` §1.2
  records that neither vendor PDF prints a response body for `POST /axapi/refresh` — `{"token":
  ...}` is inference by symmetry with login, not a transcribed fact. `client._refresh` now treats a
  missing `token` field as the expected, non-exceptional case (`MalformedResponseError`, which
  `request()`'s 401-recovery loop already catches and falls through to a full re-login on) rather
  than a bare `KeyError`.
- **Transport exception text no longer reaches the manifest.** `requests`' own connection/timeout
  exception messages embed the full request URL — appliance hostname and query string, including a
  `filter` value — and that text was flowing through `extract.py`'s `Failed.detail` into
  `manifest.jsonl`, which must never contain either. `client._do_request` now raises
  `ConnectionFailedError` with only the exception's type name and the bare `axapi` path; the
  original exception is still chained via `from exc` for anyone inspecting the exception object
  directly, but nothing prints it.
- **Gap detection now tracks a running maximum, not the previous file's `time_end`.** An
  out-of-order or overlapping file in `fs/files` (an earlier, longer file after a later, shorter
  one) was producing a phantom gap. `catalog`'s gap scan now measures each file's start against the
  running maximum `time_end` seen so far.
- **Every `xtract` error now has a single root.** `XtractError` (`errors.py`) is the common base
  every module's own error type (`TimeUtilError`, `WindowsError`, `StreamSpecError`, `CatalogError`,
  `ManifestError`, `CredentialError`, `RunnerError`, `XtractAPIError`) reparents under, alongside
  whatever more specific base it already had — so `cli.py`'s top-level handler can catch "any known
  xtract error" in one `except XtractError` instead of enumerating every module's exception type.
- Six further quality findings fixed alongside these, including: `client.py` now uses
  `contextlib.suppress(Exception)` rather than a bare `except Exception: pass` (keeps `ruff` S110
  clean without hiding the intent); `windows.py`/`streamspec.py`'s module docstrings were updated
  the moment they started importing the shared `xtract.errors` root, so their stated "stdlib only"
  scope stays accurate; and `tests/conftest.py` was introduced to start absorbing test fixtures that
  had been copy-pasted across multiple test modules (not yet complete — see `GAP-003`).

**Why:**

- All five must-fix defects were silent-success or credential/PII-leak classes — none of them would
  fail a naive smoke test, they'd only surface on a real batch run against CorpAPCN (a resumed run
  after a growing `--end`, a `refresh()` call, a transient network blip, an out-of-order file
  listing). Catching them now, against mocks, is strictly cheaper than catching them live.

**Affected docs:** `docs/LESSONS.md` (LES-037 onward, below)

**Affected code:** `xtract/runner.py`, `xtract/client.py`, `xtract/catalog.py`, `xtract/errors.py`,
`xtract/windows.py`, `xtract/streamspec.py`, `tests/conftest.py`, plus regression tests across
`tests/test_runner.py`, `tests/test_client.py`, `tests/test_catalog.py`

## 2026-08-04 — Clipped-window resume: re-extract and overwrite, reported as `reextracted`

**Authority:** user

**What changed:**

- On resume, when a manifest record's `end` for a given `start` is shorter than the current run's
  planned `end` for that same start (the earlier run's `--end` clipped the final window short), the
  window is **re-extracted and its output file overwritten** — `out/<startTs>.pcap` is a strict
  superset of what was there before, so nothing already captured is lost. `runner.execute` reports
  this outcome distinctly as `reextracted` in both the per-window progress callback and the run
  summary, never folded into ordinary `written`.
- Two alternatives were considered and rejected:
  - **Refuse, like the collision guard.** Rejected because this would fire on every resume of a
    stream that is still capturing (`--end` omitted, resolved to "latest stored data" each run) —
    i.e. the normal, supported, repeated-extraction workflow this tool exists for, not an edge case.
  - **Write to a new filename** (e.g. embed the attempt or the actual `end` in the name). Rejected
    because it breaks the version-locked `out/<startTs>.pcap` naming contract (`CLAUDE.md` "Design
    Document Maintenance" — output naming is one of the two contracts we own and gate on).

**Why:**

- The code review found that resume decisions keyed only on a window's `start` (the filename), which
  cannot distinguish "already fully captured" from "captured short, under a since-grown `--end`" —
  see the fix-pass entry above. Resolving that ambiguity is a design decision about resume semantics,
  not a bug fix, so it's recorded here with its own Authority marker.

**Affected docs:** `CLAUDE.md` (Conventions — resume/manifest section, if not already accurate),
`README.md` (`xtract pcap` resume description)

**Affected code:** `xtract/runner.py` (`WindowDecision.reextract`, `plan_resume`, `execute`)

## 2026-08-04 — Python floor raised to `>=3.11`; stdlib `tomllib` adopted for credential config

**Authority:** user

**What changed:**

- `pyproject.toml`'s `requires-python` is `>=3.11`, and `auth.py`'s `config.toml` reader uses the
  stdlib `tomllib` (added in 3.11) directly. The ~60-line hand-rolled TOML subset parser WO-2 wrote
  to satisfy an earlier `>=3.9` floor is deleted.

**Why:**

- WO-1 set `requires-python = ">=3.9"` without flagging it as a decision — it was simply the
  lowest Python version the work order's author assumed was safe. WO-2 then had to hand-roll a
  minimal TOML parser to read `~/.config/xtract/config.toml` under that floor, since `tomllib` isn't
  available before 3.11 and taking a third-party TOML dependency wasn't in scope. That parser
  stripped `#` as a start-of-comment marker even inside a quoted string value, so a valid TOML value
  containing a literal `#` (plausible in, say, a profile name or a path) was silently rejected or
  corrupted. Given the choice between hardening a hand-rolled parser or raising the floor to get a
  battle-tested stdlib one, the user chose to raise the floor — this box and the target ops hosts
  are already on 3.11+, and a hand-rolled parser for a config format this project doesn't otherwise
  need to support broadly is not worth maintaining.

**Affected docs:** `README.md` (Install — states the 3.11 requirement and why), `CLAUDE.md` (Tech
Stack, if it names a Python floor)

**Affected code:** `pyproject.toml`, `xtract/auth.py` (TOML parser deleted, `tomllib` import added)

**Supersedes:** the 2026-08-04 "`xtract` design ratified" entry below, which locked in "Python 3"
with no floor stated (WO-1 then filled in `>=3.9` implicitly, never ratified as a decision in its
own right). This entry is the first explicit ratification of a minimum version.

## 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

**Authority:** user

**What changed:**

- **Tool named `xtract`** (CLI binary and Python package). Repo name `xapi-pcap-extract` unchanged.
  Two subcommands: `xtract streams` (per-stream data ranges) and `xtract pcap` (windowed extraction).
- **Primary spec source switched** to `Axellio Xpress Platform REST API.pdf` (84 pp, `axapi` 5.1.0).
  The 7-page `PacketXpress Interface Control 5.1.0.pdf` is demoted to secondary — it is still the
  only worked example and the only statement of the `stream_ids` grammar.
- **Stream time ranges are assembled, not fetched.** No endpoint returns a stream's range. It is
  `min(time_start)`/`max(time_end)` over `GET /axapi/fs/files?stream=<s>`, which also yields gap
  detection for free.
- **All `axapi` time fields are typed `string`** (confirmed on the `POST /axapi/extract` payload,
  p.53–54). Internally time is `int` nanoseconds; strings are formatted at the boundary. The
  caller's precision is preserved rather than normalized.
- **`stream_ids` is a spec string passed through verbatim**; a separate `expand()` yields integers
  for local cataloguing only. A multi-stream extract returns **one merged, time-ordered pcap**, so
  `--streams` never multiplies the window count.
- **Output is `out/<startTs>.pcap`** — the filename is the exact `start_time` string sent on the
  wire. No stream IDs in the name. `.part`→`rename()` for atomicity.
- **The manifest is load-bearing**, not a nicety: with no stream IDs in the filename it is the only
  record that a 418 window was attempted, and the only guard against two runs with different
  `--streams`/`--filter` colliding in one `--out` directory.
- **Windows are half-open `[t, t+interval)`**, final window clipped to `--end`. `--end` omitted
  defaults to the *latest stored data* (union max across the requested streams), not wall clock.
- **418 is a first-class outcome**, not a failure — distinct in the manifest and the exit code.
- **Auth: API key preferred, JWT login supported.** Resolution order flag-path → env → profiled
  config → TTY prompt → loud failure. No secret ever in `argv`. Tokens never persisted.
  `list-keys`/`revoke-key` are in scope; **`create-key` is not**.
- **Transport: synchronous `GET /axapi/pcap` for iteration 1**, behind a pluggable seam. The async
  `POST /axapi/extract` → poll → `/output` path takes the identical five fields, so the swap is an
  envelope change if long-window timeouts force it.
- **Deferred:** `archive=` / `servers=` multi-extract, the async transport, `create-key`, parallelism.
- Filed `docs/WORK_ORDER_*.md` WO-0…WO-7 plus a gated live probe; opened Q1–Q4.

**Why:**

- The 84-page REST API doc revealed the surface is ~50 endpoints, not the three in the ICG — and
  that it includes service-stop and data-delete operations. Scoping `xtract` to an explicit
  read-only subset is a safety property, not tidiness.
- `ctid` + the `X-Task-GUID` response header (absent from the ICG) make every extract trackable and
  abortable, which is what makes SIGINT handling honest instead of orphaning work on the appliance.
- Every time field being `string` makes the nanosecond-integer internal representation structural
  rather than defensive: there is no point in the pipeline where a `float` is even tempting.
- Four wire facts cannot be answered from either PDF (Q1–Q4). They are isolated behind a gated
  read-only probe so the rest of the build proceeds unblocked.

**Affected docs:**

- `CLAUDE.md` — What This Project Is, Current State, Architecture, Tech Stack, How to Read These
  Docs, Immediate Next Actions, Conventions, What NOT to Do, Design Document Maintenance
- `docs/OPEN_QUESTIONS.md` — Q1–Q4 opened
- `docs/WORK_ORDER_*.md` — created

**Supersedes:** the initial `CLAUDE.md` fill-in of the same date, which described the `axapi`
surface as three endpoints and named the CLI `xapi-pcap-extract`. Both were correct readings of the
7-page ICG and are wrong for the platform API.

## 2026-08-04 — Initial Claude workflow setup

**Authority:** user

**What changed:**
- Initial project setup following the Claude + Claude Code workflow template.
- Created `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/CHANGELOG.md`, `scripts/claude-export.sh`, `scripts/claude-import.sh`.
- Tagged initial state as `chat-<date>-init`.

**Why:**
- Establish consistent workflow for design collaboration between Claude chat and Claude Code from day one, preventing drift.

**Affected docs:**
- All files — initial creation
