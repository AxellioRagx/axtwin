# WORK ORDER — Streaming Zeek ingest at real-capture scale

**STATUS:** LANDED (2026-08-03 — see CHANGELOG "Streaming Zeek ingest at real-capture scale")
**Depends-on:** none (extends `src/aximprint/ingest/zeek/plugin.py` v0)
**Closes:** `Q2` (streaming ingest vs batch) — closed
**Authority:** user (2026-08-03) — scope limited to ingest; feature emission is `WORK_ORDER_feature_vector_sink.md`

## Goal

Make the zeek ingest plugin handle a real capture end-to-end without loading it into memory, and
widen its coverage to the log types and connection states a real capture actually contains.

The reference capture (`./metadata/zeek`, privacy-gated — `LES-003`) is the forcing function:

| Property | Value | Consequence |
|---|---|---|
| span | 71.0 h → 72 hourly windows | multi-window; the window manager's close path runs 72× |
| `conn.log` | 1,535,329 rows / 188.8 MB | the v0 in-memory sort is not viable — this is `Q2`, now real |
| total events | ~1.76 M across 19 logs | must stream |
| distinct addresses | 44 (34 orig / 12 resp) | narrow vantage — few entities, deep history |
| `conn_state` mix | SF 903 k, **RSTR 624 k (41 %)**, RSTO 7 k, S0 715, OTH 234 | v0 counts only S0/REJ — blind to the dominant state |
| `service` | `-` on 95 % of rows | the `proto` fallback carries the protocol mix, not the L7 label |
| `dhcp.log` | **absent** | no DHCP identity evidence; kerberos (459) + dns (175 k) are the identity sources |
| `telemetry.log` | 122.5 MB / 1.09 M rows | Zeek's own metrics — must be **skipped**, not parsed |

All 19 logs are classic TSV with a `#fields` header. No JSON-lines in this capture (keep the JSON
path — other deployments emit it).

## Build

1. **Streaming k-way merge** — replace `out.sort()` in `ZeekPlugin.events()` with a merge over
   per-file row iterators, yielding events in non-decreasing `ts` without materializing them.
   `heapq.merge(*iterators, key=lambda e: e.ts)` over per-file generators is sufficient and stdlib.
   Each Zeek log is already ts-sorted internally, so the merge is exact, not best-effort.
   Peak memory must be O(open files), not O(events).
2. **Connection-state coverage** — extend the `S0`/`REJ`/`SF` discrimination in the event model and
   the window accumulator to carry the states this capture is actually made of. At minimum
   `RSTR`/`RSTO` (reset by responder / originator) must be distinguishable from both "established"
   and "unanswered": 41 % of flows currently land in neither bucket. Keep the semantic split the
   brief names (§4): attempted vs answered-refused vs established.
3. **Log-type coverage** — add the types present here that map cleanly onto the event model:
   - `dce_rpc`, `ldap_search`, `smb_mapping`, `smb_files`, `ntp`, `known_services` → `ServiceEvent`
   - `known_hosts` → `IdentityEvent` (host census)
   - `stats.log` → `SensorHealthEvent` (`pkts_dropped`, `pkt_lag` — richer than `capture_loss` alone)
   - **Skip entirely:** `telemetry`, `loaded_scripts`, `packet_filter`, `files` (Zeek-internal or
     file-analysis; none map to the event model today). Skipping must be *explicit and named*, not
     an accidental fall-through — an unrecognized log type is reported, per "fail loud".
4. **Progress reporting** — a long ingest must not look hung. Report rows/events processed per log
   at a sane interval, to stderr.

## Boundaries — do NOT do these

- **No belief store, no learners, no synth.** This work order stops at the event stream.
- **No entity-resolution work.** The resolver stays `entity == ip` (`Q1` is a separate effort).
  Absence of `dhcp.log` here is an observation for the work order's findings, not a prompt to build
  resolution.
- **No new runtime dependency.** `heapq` is stdlib. Do not reach for pandas/polars/pyarrow.
- **No schema versioning.** The event model changes freely — add fields as needed, no version
  fields, no bump ceremony, no compat shims (`LES-001`).
- **No capture contents anywhere.** Report shapes, counts, distributions. No hostname, principal,
  cert subject, filename, query, or IP from the capture in code, tests, docs, or your hand-off
  (`LES-003`). Tests use synthetic fixtures only.

## Validation

- `scripts/verify_tier.sh` passes (fast tests + both determinism invariants + CLI round-trip).
- **Determinism holds on the real capture:** `python scripts/verify_determinism.py --source metadata/zeek`.
  Two full passes over 1.76 M events must produce byte-identical features. This is the real test of
  the merge — an unstable tie-break between equal timestamps would surface here and nowhere else.
- **Memory bound demonstrated:** peak RSS during a full ingest reported, and materially below what
  the v0 sort would need. State the number.
- New unit tests cover: the merge yielding non-decreasing ts across multiple files, tie-breaking
  being deterministic, each newly-parsed log type producing its event, and each skipped type being
  skipped by name rather than by accident.
- Existing tests still pass unchanged.

## Findings to report (not fixes — record them)

The real capture will surface things outside this work order's scope. Record them as `LES-NNN`
candidates or `Q<n>` additions rather than fixing them:

- Whether `heapq.merge` tie-breaking on equal `ts` is stable enough for the determinism invariant.
- The within-window edge-accumulator growth (`windows.py` `self._edges`) — top-K eviction happens at
  close, so peak memory during a window is unbounded by peer cardinality. 44 addresses makes this
  harmless *here*; say so, and say what a wide-vantage capture would do.
- Whether 95 % `service == "-"` makes `proto_mix` degenerate, and what that implies for the GNN's
  protocol-mix feature.

## Outcome (2026-08-03)

**Delivered.** Streaming merge, 8-class flow-state vocabulary (all 13 `conn_state` values present),
+8 log types parsed and 4 skipped by name, progress reporting. Peak RSS at ingest **926.6 MB →
67.4 MB (13.7×)**; full ingest + windowing **39.7 MB / ~32 s**. Suite 2 → 99 tests, all passing;
`verify_tier.sh` green; determinism holds on the real capture over 72 windows (two byte-identical
passes). Listen-port coverage **+69.9 %** from gating the census on "the responder answered"
instead of `state == "SF"`.

**Findings, answered:**

1. **Tie-breaking — not stable as specified; fixed.** `heapq.merge` breaks ties on *argument
   position*, so the merge needed sorted input paths, an intra-file key of `(ts, row_index)`, and a
   comparison that cannot reach the payload. `LES-009`.
2. **`self._edges` is the wrong thing to worry about.** It is bounded by peer cardinality; the
   exact `Quantiles` stand-ins retain every value (3 per flow per window) and are O(input). A
   wide-vantage capture hits the sketches long before the edge dict. `Q8`, `LES-010`.
3. **`proto_mix` is not degenerate** despite ~95 % `service == "-"`: it is byte-weighted and the
   `proto` fallback carries the mix. The real problem is different — Zeek's `service` is a
   comma-joined **set**, so composite keys fragment the space and coexist with their singletons,
   and neither `proto_mix` nor `listen_ports` has a pinned vocabulary. `Q6`.

**One premise of this work order was wrong.** Build step 1 asserted "Each Zeek log is already
ts-sorted internally, so the merge is exact, not best-effort." It is not: Zeek keys a row on
connection start and writes it at close. Measured disorder — `conn.log` 5.2 % of rows below the
running max (worst 878 rows / 169.6 s), `dns.log` 65 % (worst 154 rows / 33.8 s), `known_services`
worst-by-time 294.8 s. Hence the bounded reorder buffer, sized in **seconds** (`LES-004`) — and the
consumer-side hardening that became its own fix round (`LES-005`).

**Also learned:** `stats.log` here carries the loss columns with every cell unset, so
`capture_loss.log` is the only loss evidence, and loss is non-zero in **100 % of windows**
(`LES-012`, `Q10`).
