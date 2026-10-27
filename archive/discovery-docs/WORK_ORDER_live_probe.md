# Work Order — Live probe: answer Q1–Q4 against CorpAPCN

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE — 2026-08-05.** Run against CorpAPCN (PacketXpress / Axellio Xpress Platform
> 5.1.0). All four questions answered (Phase 6 below, and `docs/OPEN_QUESTIONS.md`), all recorded with
> their evidence in `docs/AXAPI_CONTRACT.md` and, in full, in the new `docs/VENDOR_API_DEFECTS.md`
> (which also documents six vendor-doc defects the probe surfaced beyond the four questions this
> work order set out to answer). See CHANGELOG 2026-08-05 "Q1–Q4 resolved by live probe; six
> vendor-contract corrections applied".
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`docs/OPEN_QUESTIONS.md` Q1–Q4 are answered from observed behavior, recorded in
`docs/AXAPI_CONTRACT.md` as facts with the evidence that established them, and closed in the
CHANGELOG. After this lands, WO-3 and WO-6 can be finished instead of stopping at a guess.

**Not a goal:** extracting useful data, or a performance test. This is four questions and the
smallest set of requests that answers them.

## 2. Build — phases, in order

> **Every step is read-only. `live-verifier` STOPS and asks before anything else.** If a step seems
> to require a mutating call, that is a sign the probe is wrong — return `DECISION NEEDED:`.

### Phase 1 — Reachability and identity

- **Command:** `GET /axapi/system/information`
- **Records:** appliance `name`, `identifier`, `time`, software versions. Compare `time` against
  local wall clock and **record the skew** — it silently shifts every window boundary if the
  operator sources timestamps from one clock and reasons about them on the other.
- **Also:** confirm which auth mode works (API key vs. JWT) and the TLS posture (public CA, private
  CA bundle, or self-signed).

### Phase 2 — Q3: stream naming

- **Commands:** `GET /axapi/fs/streams`; then `GET /axapi/minute/<a recent start_time>`
- **Answers:** are `fs/streams` `name` values decimal stream IDs? Do they match the numeric
  `streamId` in the datapoint records? Record the full list of stream names verbatim.

### Phase 3 — Q1: units of `time_start` / `time_end`

- **Command:** `GET /axapi/fs/files?stream=<one stream with data>` (capture one full record)
- **Answers:** interpret `time_start` as s / ms / µs / ns and see which lands in a plausible recent
  date. Cross-check against `epoch`, against the file's `date` string, and against the appliance
  `time` from Phase 1. **Record the raw record verbatim in the contract doc** — the reasoning must
  be auditable later, not just the conclusion.

### Phase 4 — Q2 and Q4: timestamp format and boundary semantics

Three small extracts against a stream known to hold data, each ~1 second:

1. `start_time`/`end_time` as **integer** seconds → does it succeed?
2. The same window as **sub-second** strings (`<s>.000000000`) → does it succeed, and is the output
   byte-identical to (1)?
3. Two **adjacent** windows `[t, t+1s)` and `[t+1s, t+2s)` sent as `end_time = t+1s` for the first
   → does any packet appear in both outputs?

- **Answers:** Q2 (which formats are accepted, and whether they parse identically) and Q4
  (inclusive vs. exclusive `end_time`). For (3), compare packet counts/bytes at the boundary; a
  duplicated boundary packet means `end_time` is inclusive and `windows.py` must subtract 1 ns.
- **Also record:** the `X-Task-GUID` response header, and whether `ctid` round-trips into
  `GET /axapi/task/<ctid>`.

### Phase 5 — Bonus: gap behavior

- **Command:** one extract over a range the Phase 3 file listing shows as a gap.
- **Answers:** 418, or a short pcap? Determines whether "expected empty" windows can be predicted
  from the catalog or must simply be attempted.

### Phase 6 — Record

- **Files:** `docs/AXAPI_CONTRACT.md` (facts + evidence), `docs/OPEN_QUESTIONS.md` (Q1–Q4 marked
  resolved with links), `docs/CHANGELOG.md` (Authority-marked entry), `docs/LESSONS.md` (anything
  durable — a clock skew, a surprising status code, a format that silently succeeds but returns the
  wrong window).
- **Also:** if any answer contradicts an assumption baked into shipped code, file a `GAP-NNN` and
  name the module.

## 3. Boundaries — explicitly OUT of scope

- **No mutating call, under any circumstance.** Not `POST /axapi/clear`, not `DELETE /axapi/fs/*`,
  not service control, not key creation. The appliance stores capture data that cannot be recreated.
- No large extracts. Every probe window is ~1 second. If a request would return more than a few MB,
  stop and ask.
- No credentials, hostnames, or captured bytes recorded in any committed file. Findings are
  described; evidence is quoted with identifying values redacted.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** n/a
2. **Hermetic tier:** `scripts/verify_tier.sh` still clean afterwards
3. **Live (gated — this work order *is* the live step):** each of Q1–Q4 has a recorded answer and
   the observation that produced it. An unanswered question means this work order is not DONE; it
   does not mean the question can be assumed.
4. **Docs:** CHANGELOG entry (Authority-marked), Q1–Q4 closed in `OPEN_QUESTIONS.md`,
   `AXAPI_CONTRACT.md` updated, `doc-stats --check` clean

## 5. Depends on / sequence

- **Depends on:** WO-0 (contract), WO-2 (a client that can make the requests), **and the user** —
  hostname, credentials, and explicit permission to touch CorpAPCN.
- **Unblocks:** completion of WO-3 and WO-6. Both can be *built* before this runs; neither can be
  marked DONE until Q1–Q4 are closed.
