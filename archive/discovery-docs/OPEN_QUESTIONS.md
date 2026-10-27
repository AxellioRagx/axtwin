# Open Questions — aximprint

- **Q1** — ~~Entity-resolution v1 design~~ **CLOSED 2026-08-14** by
  `docs/WORK_ORDER_entity_resolution.md`, built as drafted: its four forks are ratified (D1 only
  host-scoped evidence may merge, D2 causal and forward-only, D3 sequential run-scoped ids, D4 an
  unanchored address resolves to itself). The question asked for **evidence weighting**; the answer
  is that weighting was the wrong frame — evidence has to be **classified by what it can anchor**
  first (`windowing.entities.IDENTITY_SCOPES`: `host` / `user` / `referenced`), because a user
  principal names a person and a DNS answer names a third party's address, and no weight makes
  either safe to merge on. Interval merge semantics: bindings are intervals with generations,
  supersession closes a generation, and a merge learned later is a forward `same_entity_as`
  assertion the snapshot reader applies, never a rewrite. See CHANGELOG 2026-08-14 for the measured
  results and the six findings, and note **the work order's own premise was stale** — it was drafted
  against a capture with no `dhcp.log`, and the baseline it actually shipped against has three
  independent host-scoped sources. `Q9` closed with it, in the same file.
  **Still open, and separated from `Q1` on purpose:** cross-capture entity pinning (the
  `--vocabulary` analogue for entity ids, work-order finding #5) and whether an address seen only
  as an external peer should be *marked* rather than resolved (finding #6) — neither is entity
  resolution v1, and both were explicit non-goals.
- **Q2** — ~~Streaming ingest vs batch~~ **CLOSED 2026-08-03** by
  `docs/WORK_ORDER_streaming_zeek_ingest.md`: the in-memory `out.sort()` is replaced by a per-file
  bounded reorder buffer feeding `heapq.merge` (peak RSS 926.6 MB → 67.4 MB). The buffer's
  guarantee is stated in **seconds**, not rows, and a cap-forced flush is disclosed as a health
  fact — see CHANGELOG 2026-08-03 and `LES-004`. Live tail is still unbuilt but is now a plugin
  detail, not an architecture question. **More so since 2026-08-05:** transport is separated from
  semantics in `ingest/zeek/sources.py`, and a second transport (the Parquet metadata archive) was
  added without touching a parser. A live source would be a third `ZeekSource`, not a redesign.
- **Q3** — Sub-bucket count and window size defaults (1 h × 12×5 min is a guess). Real data has
  now landed and the evidence exists to settle this — 72 hourly windows over a 71.0 h span — but
  no retune was attempted. Decide against a learner that actually consumes the sub-bucket series.
- **Q4** — Where GNN training runs (central vs local) and label sourcing from axrange
  twin-generated traffic. (brief §6)
- **Q5** — Belief decay half-lives per predicate class. (brief §7)
- **Q6** — ~~Categorical vocabulary artifact~~ **CLOSED 2026-08-04** by
  `docs/WORK_ORDER_categorical_vocabulary.md`: pinned vocabulary **in the manifest** (not a separate
  artifact, not a hashed space). `manifest.json` carries a `categorical` block — index map, index
  provenance (`observed`/`pinned`/`closed`), per-key row counts, mass, coverage in mass and rows,
  OOV rows/mass/keys, composite marking, and `map_sha256` — and `aximprint features --vocabulary`
  pins a second capture to the first's columns. The observer is capped at insert (`MAX_KEYS`) with
  the truncation disclosed. See CHANGELOG 2026-08-04 and `LES-013`–`LES-017`. The composite-set
  split is measured and deliberately **not** performed (feature semantics, not vocabulary); the
  `listen_ports` key shape moved upstream as `Q12`.
- **Q7** — **Node-vector gaps against §5.4.** "OS hints (TTL mode, SMB dialect)" has no field at
  all, and §5.2's named packet-size quantiles are absent — `pkts_out` / `pkts_in` reach `FlowEvent`
  and are never accumulated. Both gaps are upstream in `NodeAcc`, not in the sink. Decide whether
  §5.4's list is the contract (and close the gaps) or whether the list was aspirational and the
  brief should say what is actually consumed.
- **Q8** — **When and how the exact sketch stand-ins get swapped.** `Quantiles` is a plain
  `list[float]` retaining every value (3 per flow per window), so the accumulator is O(input) where
  §5.2/§5.3 claim O(output) — "bounded at any line rate" is not true of the v0 stand-ins, and the
  edge dict is the *smallest* term, not the offender. Decide the families (t-digest / HLL /
  Count-Min class), the error budget each feature can absorb, and the trigger for the swap. Note
  the swap changes feature values, so it interacts with backfill (§11 "representation changes are
  redefinitions in disguise"). (`LES-010`)
- **Q9** — ~~`identity.jsonl` restates rather than deltas~~ **CLOSED 2026-08-14** with `Q1`, in the
  file this question already predicted: `EntityResolver.observe()` returns records on **change** —
  a binding opening or being superseded — and never on restatement. "Changed" is defined as those
  two transitions plus a third record, `final`, published once per still-open binding at end of run
  (`flush_open`): without it the reduction would have been bought by discarding each binding's
  observation extent, which is most of what a binding record is for. Measured on the 2026-08-11
  baseline: **145,319 rows → 12,914 (−91.1 %)**, and the feature archive 34 MB → 9.6 MB. The
  question's own 4,386-row figure was from the older, narrower reference capture; the effect is
  larger at this vantage, not smaller.
- **Q10** — **Window usability policy under inherited capture loss.** Every window of the reference
  capture carries 5.87 %–24.92 % loss (median 9.17 %); there is no clean window, so "exclude
  degraded windows" is not an available strategy at this vantage. §5.4 #5 says the asterisk
  travels — decide what a consumer *does* with it: a hard usability threshold, loss-weighted
  confidence, a per-feature sensitivity model, or explicit acceptance that loss is the normal case
  and models must be trained under it. (`LES-012`)
- **Q11** — **`__longtail__` consumer contract.** The top-K spill entity appears as an edge `dst`
  with no corresponding node row — a dangling adjacency every GNN loader must special-case. It has
  no unit test anywhere and never fired on the reference capture (44 entities < `TOP_PEERS = 64`),
  so it is untested code on the path a wide-vantage capture takes first. Decide whether it gets a
  synthetic node row, an explicit exclusion flag in the manifest, or a documented loader contract —
  then test it.
- **Q12** — **`listen_ports` key shape.** It is not a vocabulary: it is a `(port, transport)` tuple
  string-joined on `/` (12 labels = 10 ports × 2 transports on the reference capture), and the
  tuple already exists upstream as a genuine `Counter[(port, proto)]`, joined only to be
  JSON-representable. Its unbounded tail comes from exactly one dimension, so reshaping the key to
  `(port, binned_port_class, transport)` would drive the dynamic-port OOV to zero at the same order
  of magnitude of width. The pairing carries information (two ports appear under both transports),
  so it does **not** decompose into two marginal one-hots. Belongs in
  `src/aximprint/windowing/windows.py`, deliberately untouched by the vocabulary work order.
  (`LES-016`, CHANGELOG 2026-08-04)
- **Q13** — **`NodeAcc.listen_ports` grows unbounded upstream.** Pre-existing: any `FLOW_ACCEPTED`
  state admits a `(dst_port, proto)` pair, so the accumulator's key count is bounded only by the
  traffic. The `MAX_KEYS` cap added at the features layer bounds the **manifest**, not the
  accumulator — the memory is still spent upstream before anything is capped. Same family as `Q8`'s
  sketch problem (O(input) accumulators behind an O(output) claim) and should probably be decided
  with it. (`LES-010`, `LES-014`)
- **Q14** — **`TopK.normalized()` breaks count ties on insertion order.**
  `src/aximprint/windowing/sketches.py` uses `most_common`, which is insertion-ordered among equal
  counts. Harmless while the output was a per-window float mix; it now decides **membership of a
  published, pinnable index map**, so a tie broken differently renumbers columns across captures.
  Promoted in severity by the vocabulary change, not introduced by it. The fix is one line
  (`most_common` → sort on `(-weight, key)`) but sits in the layer the work order was forbidden to
  touch, and it changes emitted feature values — so it wants deciding, not sneaking in. (`LES-009`,
  `LES-017`)
- **Q15** — **DISSOLVED 2026-08-10** (not answered). The premise was that the archive's **producer
  lives outside the repo** and can drift independently of its reader — "exactly the condition
  versioning exists to manage." The user ruled the two projects merge with no archive seam
  (CHANGELOG 2026-08-10, DESIGN_BRIEF §0.1), so the producer is inside, a mismatch is a build error
  rather than a runtime surprise, and there is nobody left to version *for*. `LES-001` holds
  unchallenged. Note what survives longest on disk is the **cold tier** — sensor-native Zeek logs,
  not an aximprint-shaped artifact — so it is immune to event-model churn by construction. Should a
  second team ever maintain a converter again, the original question below returns intact.
  <details><summary>Original question, kept for the reasoning</summary>

  **Should the metadata-archive contract be versioned?** `docs/SPEC_zeek_metadata_archive.md`
  carries **no version field**, per `LES-001` and CLAUDE.md: schemas here are incubating and
  ratifying one is the user's call. But this is the first artifact in the project whose **producer
  lives outside the repo** — an external converter that can drift independently of the reader — and
  that is exactly the condition versioning exists to manage. Today's answer is read-time validation:
  six checks that fail loud at the boundary (missing manifest, undeclared columns, missing
  provenance, unmeasured disorder, undeclared files, zero-events-from-N-rows). That covers drift the
  reader can *detect*; it does not cover a converter changing a field's **meaning** while keeping its
  name and type. Decide before a second team maintains the converter. (`LES-001`, `LES-015`)
  </details>
- **Q16** — **Is the feature archive supposed to be the belief store's equal?** §5.4 lists
  deterministic assertions as artifact #4; the store path has an `assertions` table, and the feature
  archive has four files, none of them assertions — a specified artifact is simply missing from one
  of the two egress paths. `WORK_ORDER_egress_sink_unification.md` widened the sink contract to
  `write_window(win, assertions)` specifically so both paths *could* have them — `extract_assertions`
  is computed once by the driver and handed to every open sink, and `JsonlSink.write_window` receives
  it and discards it on one visible line (`del assertions`). Adding `assertions.jsonl` is therefore a
  small diff, deliberately **not** made in that round because it changes the archive's bytes, which
  that round's own acceptance criterion (byte-identical output) forbade. Decide whether the archive
  is meant to be the store's equal — in which case `assertions.jsonl` is a small, overdue follow-up —
  or a deliberately narrower artifact for a different consumer, in which case §5.4 #4 should say so
  explicitly rather than list an artifact the jsonl path never intends to produce. (CHANGELOG
  2026-08-06)
- **Q17** — **Run→window attribution is not answerable from the belief-store schema.** The new `runs`
  table (`WORK_ORDER_egress_sink_unification.md` build step 6) stamps what a run *was* and what build
  produced it, satisfying §10's "same aximprint build on both sides" — but it records a run's window
  **count**, not the `window_start` values it wrote, so "which run produced this `window_health`
  row" has no answer today. Three facts compound this rather than making it a narrow gap: there is no
  belief decay/forgetting yet (`Q5`), so old runs' windows are never pruned; re-ingesting the same
  source appends a **second** set of rows at the same `window_start` values rather than replacing the
  first (no upsert/idempotency key); and `snapshot()` **sums** `flows`/`bytes` across every row at a
  given `window_start`, so a store that accumulated two overlapping runs silently inflates every
  downstream number the synthesizer compiles, with no query able to say which run(s) contributed. A
  fix wants deciding together: a `run_id` foreign key from `window_health` (and every other per-window
  table) back to `runs`, an idempotent re-ingest policy (reject/replace/append-and-attribute), and
  `Q5`'s forgetting policy, since attribution and decay are both "which of several runs' evidence for
  this window wins" questions wearing different clothes. (CHANGELOG 2026-08-06, `Q5`)
- **Q18** — ~~Cross-stream merge fan-out at real harvest/archive scale is reasoned, not measured~~
  **CLOSED 2026-08-10** by `docs/WORK_ORDER_multi_window_log_read.md`'s consolidated fix round, which
  measured both halves independently rather than reasoning about either — and found the opening claim
  wrong on one side, right for the wrong reason on the other. **Window-tree side, wrong as reasoned:**
  the module docstring claimed consolidating to one `ZeekStream` per stem bounded memory the way it
  bounds the STREAM COUNT `plugin.py` sees; it does not, because `heapq.merge`'s own initialization
  primes every sub-stream regardless of how many there are, and a pcap window's own `ts` span typically
  sits inside the 600 s horizon, so only end-of-file (not the horizon) ever releases a buffer — measured
  **670 MB at 300 window directories x 2,000 rows** (two independent measurements agreed: 508 MB at 150
  x 3,000, 417 MB for the identical corpus read flat), because every inner buffer swallowed its whole
  file before the merge could even begin comparing heads. `M1` (user ruling: bound the stem's AGGREGATE,
  `max(reorder_rows // len(paths), 64)` per sub-stream rather than `reorder_rows` each) measured **39 MB
  on the same 300 x 2,000 corpus** — the accepted, disclosed trade is repair quality: on a harsh
  120-directory / 25%-displaced-to-900s corpus, unrepaired inversions went from 0 (unbounded memory, an
  accident of the old design, never a guarantee) to 3,976, now visible via `source_capped` (`M2`) rather
  than silently paid for in RAM. **Archive side, right as reasoned, wrong about which constant
  mattered:** a swept comparison on a 400-partition archive confirmed the merge genuinely costs more
  than the old (buggy) concatenation — 218 MB / 3.15 s concat vs 239-303 MB / 3.7-4.0 s merged, an
  18-26% wall-clock cost worth a CHANGELOG line — but the separate, smaller `MERGE_FETCH_BATCH`
  constant this round first introduced to bound that cost (reasoned from "heapq.merge primes every
  argument", the identical `LES-010` shape as the window-tree side) measured WORSE, not better, at 256
  than at the original, larger `FETCH_BATCH` (10,000): a smaller batch cost more memory and 39x the
  round trips, because the dominant term is DuckDB's own per-cursor state (~175-215 KB/cursor; 1,200
  simultaneous cursors at 4 stems x 300 partitions is +68 MB on its own) — a term no `fetchmany` batch
  size touches either way; `S1` deleted the separate constant and reused `FETCH_BATCH`. Both findings
  are the `LES-019` shape (a confident memory story, measured rather than assumed, that turned out not
  to be the one that mattered) applied to itself. No real harvest tree or multi-hundred-partition
  archive was read (privacy-gated, producer repo out of scope) — every number above is from a synthetic
  corpus sized to be measurable, not production scale — so the specific floor (64) and divisor remain a
  documented, revisitable choice, not a claim that this is the only correct trade-off; re-open a NEW
  question if a real-scale measurement disagrees with the synthetic one. (`LES-010`, `LES-019`,
  `docs/WORK_ORDER_multi_window_log_read.md`)

---

## Historical — inherited from `xtract` at the 2026-08-10 merge (all CLOSED before landing here)

`xtract`'s own `Q1`–`Q4` were all closed wire-contract facts (not design preferences — see
`docs/WORK_ORDER_merge_xtract_acquire.md` D5) by the time it merged in as `acquire/`. They are kept
here, renumbered, per this file's own rule ("resolving one means... marking the entry resolved here
with a link") — not deleted, so the reasoning trail survives, and **never renumbered again** once
landed (same discipline as `docs/LESSONS.md` IDs).

**Renumbering map — `Q1`→`Q19`, `Q3`→`Q21`, `Q4`→`Q22`. `Q20` is deliberately unused.** `Q2`
("does `/axapi/pcap` accept both integer and sub-second timestamp strings?") never had its own
standalone code citation — the one live-probe measurement that resolved it also resolved `Q4`, and
xtract's own original text pointed from `Q2` to `Q4` ("see Q4 below, which is where this interacts
with window planning") — so its content is folded into `Q22` below rather than minted as a fourth,
separate number nothing would ever cite. This was audited against the merged code: `grep` for
`Q19`/`Q20`/`Q21`/`Q22` across `src/aximprint/acquire/` and `tests/acquire/` finds citations of
`Q19`, `Q21` and `Q22` only — zero for `Q20`, confirming the gap is deliberate, not a dropped
citation (`LES-062`). **If you are looking for a `Q<n>` citation inside a document that moved here
"as-is" from `xtract`** (`docs/AXAPI_CONTRACT.md`, `docs/RUNBOOK_CORPAPCN.md`,
`docs/CHANGELOG_acquire.md`, any of the 13 `docs/WORK_ORDER_*.md` that joined from that side) —
those documents were **not** rewritten and still say bare `Q1`/`Q2`/`Q3`/`Q4`, correct as historical
narrative for what was numbered at authoring time (the same discipline a CHANGELOG entry uses). Use
this map to translate; do not read a bare `Q1` inside one of those documents as this file's own
live `Q1` above — it is `Q19`.

### Q19 (was `xtract` Q1). Units of `time_start` / `time_end` in `GET /axapi/fs/files`

**Status:** ✅ RESOLVED 2026-08-05 (in `xtract`, before the merge). Unix epoch **seconds** — the
assumed unit was correct — but delivered as **strings**, not the documented `integer` (a separate
defect, see `docs/VENDOR_API_DEFECTS.md` §C1/§C2). Confirmed by cross-checking `"1781795842"`
against the same record's own `date` field (`"Jun 18 15:17"`).
**Resolved by:** `docs/VENDOR_API_DEFECTS.md`; code: `acquire/timeutil.py`
`FS_FIELD_NS_PER_UNIT`.

### Q20 — unused

Deliberately not minted; see "Renumbering map" above (`xtract`'s `Q2` folds into `Q22`).

### Q21 (was `xtract` Q3). Do `fs/streams` directory names map to numeric `stream_ids`?

**Status:** ✅ RESOLVED 2026-08-05 (in `xtract`, before the merge) — **NO**, not directly.
`GET /axapi/fs/streams` returns names `stream00`…`stream11`, not bare integers. The apparent
mapping is `streamNN` ↔ `N`, consistent with observation but not exhaustively proven per-stream.
**Resolved by:** `docs/VENDOR_API_DEFECTS.md`; code: `acquire/catalog.py`
`stream_id_for_name`.

### Q22 (was `xtract` Q4, folding in `xtract` Q2's sub-second-precision finding). Is `/axapi/pcap`'s `end_time` inclusive or exclusive, and how is a zero fractional second treated?

**Status:** ✅ RESOLVED 2026-08-05 (in `xtract`, before the merge) — **INCLUSIVE**, and an
`end_time` with a **zero** fractional part covers the entire second, not just the instant (this is
`xtract`'s original `Q2`: sub-second precision is honored precisely, but only when the fraction is
non-zero). Measured, same window start `1785934195`: `end=…225` → last packet `…225.986821`;
`end=…225.000000000` → same; `end=…225.500000000` → bounded precisely at `…225.487038`. Sending
`end_time` unadjusted duplicated a full second of packets across adjacent windows (7,058 vs.
4,328+2,653=6,981 — a 77-packet difference exactly matching the over-collected second).
**Resolved by:** `docs/VENDOR_API_DEFECTS.md`; code: `acquire/windows.py`
`Q22_END_TIME_INCLUSIVE`.

## Q23. Should `IdentityEvent.kind` (and any other event field with an open, sensor-supplied vocabulary) classify and count an unrecognized value, the way `FlowEvent.state`/`state_class` already does?

**Status:** 🟡 OPEN. Surfaced while checking whether the (now-dissolved) `xapi-pcap-extract` brief's
four requests to `aximprint` (`docs/BRIEF_for_aximprint_event_model.md`, not landed in this repo —
see the merge's "Findings to report") were actually resolved by the merge, as
`docs/SESSION_STATE.md`'s 2026-08-07 handoff claimed. Three of four are: the publish protocol and
late-row policy (its requests 1–2) dissolved with `H-6`'s deletion (nothing to rule on once there is
no atomic-publish producer); who writes `SPEC_metadata_archive.md` (request 3) is answered by fact —
`docs/SPEC_zeek_metadata_archive.md` already exists, aximprint-authored; and half of request 4
("stamp two version numbers") is superseded outright by the standing no-versioning-during-incubation
rule (`LES-001`, CONVENTIONS §1.2). But the other half of request 4 — "if extensions are allowed,
say what a consumer does with a `kind` it doesn't recognize: carry it, or reject it" — is **not**
actually answered anywhere in code today: `IdentityEvent.kind` (`src/aximprint/events/model.py`) is
a bare `str = ""` with a comment listing the Zeek-observed values, no validation, no companion
classified/counted field, and no rejection path — so today's *de facto* behavior is "carry anything
silently," which is neither "fail loud, fail closed" (CONVENTIONS §3) nor a deliberate "extensions
allowed" ruling; it is simply unvalidated. The 2026-08-07 handoff's claim that this was "answered ...
by `_CONN_STATE_CLASS` precedent" identified the **right pattern** — `FlowEvent.state` (raw sensor
token) paired with `FlowEvent.state_class` (`FLOW_STATE_CLASSES`, a closed, normalized vocabulary;
unmapped values counted via `unmodelled_health_kinds`-style accounting, never silently bucketed with
a neighbour) — but that pattern has not actually been **applied** to `IdentityEvent.kind`,
`ServiceEvent.kind`, or `SensorHealthEvent.kind`; only described as the thing that should happen.
Decide: apply the raw-value-plus-classified-companion pattern to every event field with an open,
sensor-supplied vocabulary (making `arp`/`netbios`/`mdns` — real values a future line-rate metadata
source can emit that Zeek never will — a normal "unmapped, counted" case rather than untested code),
or rule explicitly that these fields stay bare strings and say why the `FlowEvent` precedent doesn't
generalize.
**Not resolved by:** anything currently in `docs/DESIGN_BRIEF_aximprint.md` or `Q1`–`Q22` above —
checked; none of them cover this. (`LES-001`, `docs/events/model.py` `FLOW_STATE_CLASSES`)

## Q24. Real traffic produces 35 Zeek log types; the plugin's parser table covers 16 and declares 4 skipped. What happens to the other 15?

**Status:** 🟢 **CLOSED 2026-08-12 by ruling, then AMENDED TWICE the same day.** The user delegated
the ruling ("rule on the rest of `Q24`"), then corrected it twice — first that the test ignored
*referenced* entities (hosts named in data but never seen by the tap), then by **retiring privacy as
a project principle** in favour of forensic completeness (CHANGELOG 2026-08-12).

**Final tally: eleven of the fourteen parse, three are declared skipped, no parser refuses a field,
and "unrecognized" reaches the store.** After the build, `KNOWN_PARSED_STEMS` is **28** and
`KNOWN_SKIPPED_STEMS` is **7** — 28 + 7 = 35, every stem this network produces accounted for by
name. Skipped: `pe`, `analyzer`, `reporter`. Read the amendment box at the head of "The ruling"
before the table below it, which is superseded in five rows.

Opened 2026-08-11, from **evidence rather than reasoning** — the first live
`acquire pcap` (CHANGELOG 2026-08-11) produced 9.45 GB of real capture from a production network,
and running harvest's pinned Zeek 8.0.9 over all 12 windows produced **35 distinct log stems**.
`KNOWN_PARSED_STEMS` covers 16, `KNOWN_SKIPPED_STEMS` names 4, and the remaining **15 are
unrecognized** — produced by real traffic, neither parsed nor deliberately declined.

Two findings worth separating, because they have different answers.

**The parser table is validated.** All 16 parsed stems appeared, in real traffic, on the first try —
nothing in `KNOWN_PARSED_STEMS` is speculative or dead. That is a genuinely good result and was not
knowable from the synthetic fixtures.

**The unrecognized 15 are not one category.** They split at least three ways:
- **Discovery-relevant traffic logs, present in 12/12 windows**: `http` (0.9 MB), `ssh` (0.8 MB),
  `x509` (0.3 MB), `ocsp` (0.2 MB), `quic` (0.2 MB), `ldap` (0.1 MB), `ntlm`, `software`. Every one
  of these speaks directly to §1's stated goal — hosts, services, endpoint behavior. `software` and
  `http` are endpoint fingerprinting; `ssh` is admin-access topology; `x509`/`ocsp` are identity;
  `quic` increasingly carries what `http` used to, so parsing `http` alone decays over time.
- **Intermittent protocol logs**: `smtp` (8/12), `snmp` (4/12), `sip` (3/12), `tunnel` (10/12),
  `pe` (2/12). Real services, sampled — `tunnel` in particular is topology evidence.
- **Zeek's own diagnostics, not traffic**: `analyzer` (9/12), `reporter` (12/12). These look like
  `KNOWN_SKIPPED_STEMS` members (`loaded_scripts`, `packet_filter`, `telemetry`) that nobody has
  gotten around to declaring, not like discovery signal.

**One concrete inconsistency to resolve either way:** `ldap_search` **is** parsed and `ldap` **is
not**, though both appear in 12/12 windows. Whatever the general ruling, that split is almost
certainly not deliberate.

Decide: (a) which of the 15 join `KNOWN_PARSED_STEMS`, (b) which are declared in
`KNOWN_SKIPPED_STEMS` so they stop being reported as unrecognized, and (c) whether "unrecognized"
should remain a per-run report or become a health-record field that reaches the belief store — today
the information exists only in `ZeekResult.unrecognized` and dies with the process, so a network
whose most interesting traffic is entirely unparsed looks identical to one that is fully covered.
*(All three answered — see "The ruling", below.)*

**Not resolved by:** `Q11` (top-N peer spill) or anything in `Q1`–`Q23`. The cold tier keeps the raw
logs regardless, so nothing is lost on disk — this is about what reaches the event model, and
therefore what the synthesizer can ever cast. (`docs/DESIGN_BRIEF_aximprint.md` §1,
`src/aximprint/acquire/harvest/zeek.py` `KNOWN_PARSED_STEMS`/`KNOWN_SKIPPED_STEMS`,
`src/aximprint/ingest/zeek/plugin.py`)

**Update 2026-08-11:** the 35/16/15 split and the exact 15-name unrecognized set above were
independently reproduced on a second harvest of the same 12 pcaps (`harvest --from-pcaps` tier 3,
CHANGELOG 2026-08-11) — the stem-name set diff between the two runs is empty. Still open; this is a
second data point confirming the first, not new information changing the decision. See also `Q25`,
below, which the same tier-3 run opened — a different, log-*content* (not stem-*set*) finding.

### The ruling (2026-08-12) — **partly superseded the same day, read the amendment first**

> **AMENDED 2026-08-12, after two user interventions.** The table below stands on its *structural*
> reasoning and is **wrong wherever it declines a stem or refuses a field on privacy grounds**, because
> privacy was retired as a project principle hours later (CHANGELOG 2026-08-12, "forensic
> completeness"), and wrong wherever it tested "does another parsed stem carry this fact?" without
> asking *about which entities* — a stem naming hosts the tap never saw is not redundant with anything.
> Net effect, both amendments applied:
>
> - **`smtp` and `sip` now parse.** Their only remaining objection was privacy. `smtp`'s `path` and
>   `x_originating_ip` carry addresses of hosts the sensor never saw; `sip`'s URIs are endpoint
>   identifiers. Both are evidence.
> - **`ldap` now parses** — its DNs name computer accounts, users and OUs, i.e. entities that may
>   have no observed address at all. This also reopens `ldap_search`'s projection decline
>   (`_SERVICE_NOT_PROJECTED["ldap"]`, a user ruling of 2026-08-11) on the same basis.
> - **`x509` now parses** for its **SANs**, which list many hostnames per certificate. The
>   redundancy argument was correct for `subject` (already on `ssl`) and wrong for SANs.
> - **No parser refuses any field.** Every "refused, deliberately" list below is void: `http`'s
>   `username`/`password`/`uri`, `snmp`'s `community`, `ssh`'s `host_key`, `ntlm`'s
>   `username`/`domainname` are all collected.
> **THIRD AMENDMENT, same round — the test was also scoped to the wrong *output*.** All fourteen were
> judged against `twin_config` (does this name an entity or a durable property), and never once
> against **`tfc`, the traffic profile**, which DESIGN_BRIEF §9 declares a first-class artifact wanting
> "fitted **intensities/schedules/seeds**." A stem can name nothing new and still be load-bearing for
> what the twin must *emit* to look real.
>
> - **`ocsp` now parses.** Its `nextUpdate − thisUpdate` is the response's **cache lifetime** — a
>   schedule parameter that determines when the next check is due, and one `conn` structurally cannot
>   supply: `conn` sees that a connection happened, never the validity window explaining the interval.
>   A twin that re-checks per handshake instead of honoring the cache window gets OCSP volume wrong by
>   a large factor, invisibly. Also the OCSP-vs-stapling fork, which decides how the twin's TLS clients
>   are configured. **The earlier decline was right on the facts and wrong on the question.**
> - **This reopens `ntp`'s projection decline** (`Q27`, a user ruling) — see that section.
> - **`analyzer`/`reporter` are unaffected**: they describe the *sensor's* parsing, not network
>   behavior, so a traffic profile has nothing to fit from them. `Q28` stays their home.
>
> - **Still declined, all three on grounds that survive all three amendments:** `pe` (executable
>   metadata, no entity — **contested, `Q26` candidate 3**), `analyzer`/`reporter` (Zeek's own
>   diagnostics — `Q28`). **Every remaining decline now has a named path back**; none is declined
>   outright, which is the honest end state for an evidence collector.

**Headers verified against the baseline, 2026-08-13.** The structural claims above were documented
Zeek shapes; `#fields` lines from `/tmp/aximprint-zeek-test/` (ungated since CONVENTIONS §3's
2026-08-12 change) now confirm or correct them:

- **`ocsp` — confirmed file-keyed.** `ts id hashAlgorithm issuerNameHash issuerKeyHash serialNumber
  certStatus revoketime revokereason thisUpdate nextUpdate`. **No addresses.** Per-host attribution
  needs the `files` join; the cadence parameter does not. See `Q29`.
- **`analyzer` — confirmed attributable, as `Q28` hoped.** `ts analyzer_kind analyzer_name uid fuid
  id.orig_h id.orig_p id.resp_h id.resp_p proto failure_reason failure_data`.
- **`reporter` — confirmed to have no connection fields.** `ts level message location`. `Q28`'s
  sub-question (c) is genuinely a separate design, not the same one applied twice.
- **`ntp` — `poll` confirmed present.** `_COLUMNS["ntp"]` dropped it at the time this block was
  written; build step 2b (landed later the same day, CHANGELOG 2026-08-13) fixed the column, so the
  field is collected now. The *projection* stays declined regardless — see `Q27`, which also gained
  a keying objection review surfaced (`_ntp` keys the server; `poll` on a mode-3 row is the client's).
- **`files` — confirmed** to carry the full 4-tuple, `is_orig` and `mime_type` (`Q26` candidate 3).
- **`x509` — one real correction.** The key column is **`fingerprint`, not `id`**, and the SANs are
  four separate columns — `san.dns`, `san.uri`, `san.email`, `san.ip` — alongside `certificate.*`
  dotted names and `host_cert`/`client_cert` booleans. This **validates admitting `x509` for SANs**
  and gives the build the real `_SCHEMAS` entry, which nobody would have guessed correctly.
- **`pe` — no longer unread.** The 2026-08-13 header-verification pass above sampled a window where
  `pe` is absent, consistent with its 2/12 presence, and left it the one stem in `KNOWN_ZEEK_STEMS`
  with no observed header. Closed the same day: build step 2's `_SCHEMAS["pe"]`
  (`acquire/harvest/zeek.py`) is transcribed from a real `#fields`/`#types` header read out of one
  of the two windows `pe` actually appears in — not the documented-shape guesswork `dhcp` is stuck
  with, the only entry in that table now without a captured reference.
> - **A new blocker is now the binding constraint**, and it is not privacy or parse cost:
>   `windowing/entities.py` drops any binding without an IP (`if not (ev.ip and other): return None`),
>   and `resolve()` is address-in/address-out, so **a host known only by name has no subject**. The
>   `referenced` evidence class exists in the design (`learners/extractors.py`, DESIGN_BRIEF §7) and
>   **no code path emits it.** `smtp`'s addresses are usable today; `ldap`/`sip` names are not, and
>   that work belongs to `Q1`.



**The organizing principle: a stem earns a parser when it carries a durable fact about a host or a
link that no already-parsed stem carries.** Not "is it on-mission" — every one of the fourteen is
on-mission by topic. Presence in 12/12 windows says a stem *appears*; it says nothing about whether
its content is new, attributable, or safe to keep. That distinction is what the `software` round
learned the hard way (`Q26`) and it moves three stems off the recommended-eight list and adds two
that were not on it.

| stem | windows | ruling | event | why |
|---|---|---|---|---|
| `http` | 12/12 | **parse** | `ServiceEvent(kind="http")`, responder-keyed | `host` is the cleartext analogue of `ssl.server_name` — which vhost names an address answers for. Nothing else carries it. **Never** `uri`, `referrer`, `user_agent`, `username`, `password`: Zeek's `http.log` carries basic-auth credentials in the last two, and a URI is the highest-PII field in any Zeek log (`LES-003`) |
| `ssh` | 12/12 | **parse** | `ServiceEvent(kind="ssh")`, responder-keyed | the `server` version banner is admin-surface *and* the OS hint `software` failed to deliver. `client` is not taken — `software.log`'s `SSH::CLIENT` already has it. **Never** `host_key`: a host-key fingerprint is the same class as a TLS certificate fingerprint, which `LES-003` names as sensitive identity |
| `quic` | 12/12 | **parse** | `ServiceEvent(kind="quic")`, responder-keyed | mirrors `ssl` field for field (`server_name`, `version`). The one stem whose value *grows*: `ssl` coverage decays as traffic migrates to QUIC, so this is not additive detail, it is holding station |
| `ntlm` | 12/12 | **parse** | `IdentityEvent(kind="ntlm")` | the only new **identity** source in the fourteen. `server_dns_computer_name` (fallback `server_nb_computer_name`) binds an address to a Windows host name *at the address itself*, which a DNS answer cannot do (`docs/WORK_ORDER_entity_resolution.md`: at this vantage DNS answers bind a third party's address). **Never** `username`/`domainname` |
| `tunnel` | 10/12 | **parse** | `ServiceEvent(kind="tunnel")`, responder-keyed | which addresses terminate a VXLAN/GRE/Teredo tunnel is a fact `conn` structurally cannot express, and a twin that misses the overlay has the wrong topology, not merely a thinner one |
| `snmp` | 4/12 | **parse** | `ServiceEvent(kind="snmp")`, responder-keyed | `display_string` is sysDescr — a **first-class** device/OS description, exactly what `software` was picked for and did not deliver (`Q26`), and it lands on infrastructure (switches, routers) which `software.log` never sees. Admitted on content at the *lowest* presence of any admit, which is the point: frequency did not decide this. **Never** `community` — an SNMP community string is a credential (`LES-003`) |
| `x509` | 12/12 | **skip** | — | **the server certificate's `subject`/`issuer` already reach the event model on `ssl`** (`_COLUMNS["ssl"]`) — they are parsed today and simply not projected. If certificate identity is ever wanted, add `subject` to `SERVICE_PROJECTIONS["ssl"]`: one line, no new stem. Secondary: `x509.log` is keyed by certificate file id, carries no address, and would need a cert-chain join to attribute anything to a host |
| `ocsp` | 12/12 | ~~skip~~ **PARSE** (third amendment) | profile parameter — see `Q29` | *Original reason, correct on the facts:* file-keyed, and the content is a certificate's revocation status, a property of a CA's certificate, not of any host here. **Overturned by the `tfc` lens**: `nextUpdate − thisUpdate` is a cache lifetime, i.e. a schedule parameter, and `conn` sees the connection but never the validity window that explains its interval |
| `ldap` | 12/12 | **skip** | — | **D4.** Resolved *downward*: see below |
| `smtp` | 8/12 | **skip** | — | every identifying field is mail content (`mailfrom`, `rcptto`, `subject`, `to`, `cc`) — the highest-PII log Zeek writes. What survives redaction is `helo`, self-reported, and "serves SMTP", which `conn`/`known_services` already give |
| `sip` | 3/12 | **skip** | — | the identifying fields are SIP URIs — extensions and DIDs, i.e. PII (`LES-003`) — and the durable fact ("this address serves SIP") arrives from `conn`/`known_services` |
| `pe` | 2/12 | **skip** | — | file-analysis family. `files` is already declared skipped for precisely this reason ("no event-model home today") and `pe` is a refinement of it; declaring it separately keeps the two consistent rather than letting one hide behind the other. **Contested 2026-08-12 — see `Q26` candidate 3**: `files` carries the connection 4-tuple and `is_orig` *directly*, which defeats the "no entity" reason, and `files.mime_type` + `pe.machine` is a candidate `image:`/architecture source. The reopening is `Q26`'s to make, not this table's |
| `analyzer` | 9/12 | **skip** | — | Zeek's own diagnostics, not traffic. **Skipped is not "worthless"** — see `Q28`, opened by this ruling: a disabled or violating analyzer is direct evidence of what the sensor *could not see*, which is the same honesty gap D5 closes one layer up |
| `reporter` | 12/12 | **skip** | — | Zeek's own script errors and warnings. Same family as `loaded_scripts` |

**D2 — six parse.** `http`, `ssh`, `quic`, `ntlm`, `tunnel`, `snmp`. This **supersedes the work
order's own recommendation** of the eight 12/12 stems: `x509` and `ocsp` come off it (redundant and
unattributable respectively), and `tunnel` and `snmp` — neither recommended — go on, for content.
Every admit names the fields it takes *and* the fields it refuses; a parser that quietly carries a
credential or a URI into the event model would be a privacy break no downstream projection can undo.

**D3 — none of the remaining intermittent stems parse, and all five are declared.** `tunnel` and
`snmp` were promoted into D2 on their content, so what D3 is left ruling is `smtp`, `sip`, `pe`,
`analyzer` and `reporter` — all skipped, all by name. "Leave unrecognized" is rejected as an answer
for anything: an unrecognized stem is a decision nobody has made, and after this ruling the
unrecognized set is **empty on this baseline**, which is what makes a *future* unrecognized stem
mean something.

**D4 — `ldap` is skipped; `ldap_search` stays parsed.** The asymmetry was real and is resolved
downward, not upward. `ldap.log`'s durable content (`opcode`, `result`, `version`, and `argument` —
a bind DN, credential-adjacent) is strictly **thinner** than `ldap_search.log`'s, and
`ldap_search`'s own facts were declined from projection one day earlier by the `Q27` ruling:
*"scope/result is one query's outcome, not something the entity IS; the fact that it serves LDAP
already arrives via known_service and listen_ports"* (`_SERVICE_NOT_PROJECTED["ldap"]`). That reason
applies verbatim, and with more force, to `ldap.log`. Parsing it would add a parser whose output the
store has already ruled it does not want.

Named rather than left implicit: this leaves `ldap_search` parsed with **no fact reaching the
store** — `_add_service` returns early for a declined kind, so only its event count survives, in
`health["event_counts"]`. The consistent end-state is to retire that parser too. Not done here,
because it would reverse a user ruling made the previous day; recorded as a cleanup candidate.

**D5 — yes, "unrecognized" reaches the belief store, split by what is honestly per-window.**
- **Stem *names* go in the window health record** as `unparsed_log_types` — a sorted list, per
  window, of stems present in the input and not parsed. A name set is idempotent under union, so an
  operator aggregating across windows cannot double-count it.
- **Row/file *counts* go in the `runs` table**, once per run. Counts are not per-window data at the
  layer that knows them (`plugin.events()` reads `src.inventory`, which is source-level), and
  mirroring them onto every window would invite exactly the double-counting the name list avoids.
- **`None` means unaudited, not clean** — the same discipline `ingest_unordered` already carries in
  that dict. A transport that reports no inventory yields `None`; an inventory that reported and
  found everything accounted for yields `[]`. A field that cannot say "I did not look" would turn
  the D5 gap into a quieter version of itself.
- Two traps for the build, both consequences of `unparsed` being source-level: do **not** mint a
  synthetic `ts` to carry this as an event (a `ts=0` health event creates a window at the epoch),
  and the fact must reach **every** window of the run, not just the first one open when it is
  discovered.

**What this ruling does not do.** It does not vocabularize any new key (`NODE_CATEGORICAL` stays
opt-in, per `Q27`), does not touch `SERVICE_MAX_KEYS`, and does not claim `snmp` closes `Q26` —
`display_string`'s population on the baseline was **unmeasured** when this ruling was made, and the
build step was to confirm it before anyone reads it as an `image:` feed. **Now measured — see
"Content measured 2026-08-13" below; `Q26` candidate 2 is updated to match.** That is the `software`
lesson applied to the one admit that could repeat it. The five other admits are ruled on structure,
not on hoped-for content.

### Content measured 2026-08-13 (`docs/WORK_ORDER_zeek_stem_coverage.md` build step 3's review round)

The parser table this ruling specified is now fully built (28 parsed + 7 skipped = 35). The build's
own tier-3 Verification section named five things the real capture must answer before any admit's
*content* rationale — not just its parse-ability — is taken as confirmed; answered here rather than
duplicated in the work order:

- **`snmp.display_string` — measured EMPTY in 7/7 real baseline rows**, across all 12 windows. This
  trips the ruling's own stated reopen condition ("if `display_string` is empty across all 4
  windows, `snmp` reduces to `("version",)`, thin enough to reconsider the admit entirely" — the
  actual population was 7 rows across 12 windows, not 4, but the finding is the same: nothing but
  `version` publishes today). **User-ruled to keep the admit as built**: an unset field never enters
  `attrs` (`_attrs()`'s own positive gate), so nothing false publishes; the tuple activates
  automatically the moment a future capture populates the field; and 7 rows is too small a sample to
  reverse an admit on. Recorded here — and in `windowing/windows.py::SERVICE_PROJECTIONS`'s own
  comment — so the next reader inherits the measurement, not the assumption that this field was ever
  confirmed informative. Updates `Q26` candidate 2 below.
- **`http`'s per-entity `host` cardinality**: max **3** distinct hosts per entity on this baseline.
  `services_truncated["http"]` never fires — the forward-proxy cardinality risk this admit's own
  rationale worried about is not observed at this vantage. `SERVICE_MAX_KEYS` stays as-is; nothing to
  revisit with this baseline's numbers.
- **`ntlm`'s hostname yield**: 158 rows → **10** distinct `(ip, hostname)` bindings, **0** already
  supplied by `dns.log`. The affirmative answer to this admit's own argument — a second, independent
  source of a binding nothing else provides — and the evidence behind moving its identity-binding
  confidence to `0.9` (`windowing/windows.py::IDENTITY_PROJECTIONS`, corrected from an inline literal
  that had silently kept it at `0.7`).
- **`ssh.server` banner quality**: **2717 of 2864** rows are one opaque token, `SSH-2.0-7f27de7`, and
  every responder observed sits in GitHub's own documented SSH IP range — outbound git-over-ssh
  traffic, not diverse internal admin servers. The evidence-quality rationale behind confidence `0.9`
  stands (the banner is genuinely observed off the handshake, not a spoofable self-report); the
  *content* rationale (admin-surface topology, an OS hint) is not borne out at this vantage. Recorded
  as a content finding for a future re-ruling, not acted on unilaterally here.
- **The unrecognized set is empty**, confirmed over the full baseline with all 28 parsers and 7 skips
  in place — `ZeekResult.unrecognized == ()`.

One more, not in the work order's original five but found in the same review round: **`x509`: 610
events, `ip` empty on every one** — confirms `_add_service`'s own `if not ev.ip: return` guard makes
this kind structurally inert for node/edge features today, provably never creating a phantom entity,
exactly as the admit's own reasoning predicted.

## Q25. The Zeek cold tier is not byte-reproducible across a re-harvest of the same pcap — does anything downstream depend on it being so, and should Zeek's UID generation be pinned?

**Status:** 🟡 OPEN. Opened 2026-08-11, from a **second, independent tier-3 measurement**: the
`harvest --from-pcaps` acceptance run (CHANGELOG 2026-08-11, `docs/WORK_ORDER_harvest_from_pcaps.md`)
re-Zeeked the same 12 pcaps a prior session had already Zeeked once, and diffed the two output sets
byte-for-byte against each other — the recorded baseline sitting at `/tmp/aximprint-zeek-test/`.

**What was found, characterized rather than hand-waved.** With the `uid` field stripped and records
sorted, `dns.log` and `ssl.log` are byte-identical between the two runs. The observed byte
differences are fully explained by two things: (a) Zeek's randomly generated connection UIDs, which
vary in length (15–18 characters observed) run to run over the identical pcap, and (b) record
ordering. `conn.log` additionally differs by 2 records out of 15,037 (0.013%), likely a
connection-flush edge case at a pcap-window boundary. Record **counts** are identical everywhere
checked — nothing lost or duplicated between the two runs, only relabeled and reordered.

**Why this is a question and not a defect, filed as one anyway.** aximprint's own determinism claim
(CONVENTIONS §3, `LES-002`) is "same input logs → same outputs": window close is a pure function of
the events it is handed, and the pipeline gates verify exactly that, reading a fixed set of logs.
That claim still holds — nothing found here touches it. What was never claimed, and does not hold,
is "same pcap → same logs → same outputs across a re-harvest": the cold tier itself is not
byte-reproducible, because Zeek isn't, by default. That distinction did not matter while the cold
tier was written once and read many times; `harvest --from-pcaps` (this same round) turns
re-harvesting the same pcap into a routine, easy operation, which is exactly when the distinction
starts to matter.

**Open sub-questions, not yet answered:**
- Does Zeek's `uid` field (or record ordering) reach any feature value, vocabulary key, or
  belief-store assertion? If it is dropped before any of those layers, this is moot for aximprint's
  own outputs regardless of what Zeek does upstream. If it survives into a vocabulary key, this is
  the same shape `Q14` already describes for `TopK` tie-breaking — a re-harvest could silently
  renumber a published column. Not traced yet.
- Is `conn.log`'s 2-record difference specific to how pcaps get sliced into per-window files (by
  `pcap`, or by whatever external tool produced a `--from-pcaps` source), or would it occur even
  Zeeking one unsplit pcap twice? Not isolated.
- Zeek has a documented seed mechanism (`--set-seed` / the `Seed` script-level constant) that would
  make UID generation deterministic. Pinning it is a real decision with a real cost — one more
  pinned value to keep in step with the pinned container image tag — not an obvious "just do it,"
  and it would not by itself address the record-ordering or the `conn.log` boundary-record findings.

Decide: (a) whether `uid`/ordering ever reach a place where non-reproducibility is observable in
aximprint's own outputs, traced through `ingest/zeek/plugin.py`'s parser table and the
feature/vocabulary layers, before assuming either way; (b) whether to pin Zeek's seed, and if so
where that pin is recorded; (c) whether the `conn.log` boundary-record difference needs its own,
separate investigation.

**Not resolved by:** `Q14` (the adjacent case for `TopK` tie-breaking — not yet known to be
triggered here; this question is upstream of it, at the log layer, and only becomes the same problem
if sub-question 1's answer is "yes"). `Q18` (a different non-determinism, in aximprint's own
merge/reorder code, already closed and unrelated to Zeek's own UID generation — that one is about
this project's code; this one is about the sensor's). (`LES-002`, `docs/WORK_ORDER_harvest_from_pcaps.md`
Verification, CHANGELOG 2026-08-11)

---

## Q26. Where does a twin's `image:` actually come from? Three candidate sources, none measured, and the field still has no evidence behind it

**Status:** 🟡 OPEN. Opened 2026-08-11 on landing the `software` parser (CHANGELOG 2026-08-11) as a
narrow question about mining `unparsed_version`. **Widened 2026-08-12** into the single home for the
`image:` sourcing problem, because `Q24`'s ruling and the conversation after it produced two more
candidates and the same question was about to be asked three times.

**The three candidates, and what is known about each:**

| source | what it yields | coverage | measured? |
|---|---|---|---|
| `software.unparsed_version` | OS token buried in a User-Agent / SSH banner | 58 hosts, **~1%** | **yes** — 187 rows, **zero `OS`-typed rows** |
| `snmp.display_string` (sysDescr) | first-class device description, hits infrastructure `software` never sees | 4/12 windows | **yes, 2026-08-13 — empty in 7/7 rows**; kept as built (`R2`), not currently contributing |
| `files.mime_type` (+ `pe.machine`) | executable-transfer inference: OS family, and architecture | unknown | **no** |

**Updated 2026-08-13: two of three are now measured, and both came back thin.** `software` delivers
zero `OS`-typed rows; `snmp.display_string` is empty in 7/7 baseline rows. Candidate 3
(`files`/`pe`) remains the one genuinely unmeasured path — worth stating plainly, since `image:`
still has no source that has actually produced OS content on this network.

### Candidate 1 — `software.unparsed_version` (the original question, 2026-08-11)

**Why it was opened.** The stem was parsed specifically to feed axRange's `nodes[].image:` field
(the user's stated purpose). Measured on the 2026-08-11 12-window baseline, it does not do that:
**187 rows, 58 distinct hosts, and zero `OS`-typed rows** — 149 `HTTP::BROWSER`, 26 `SSH::CLIENT`,
12 `HTTP::SERVER`. Zeek's software framework reads banners, not OS fingerprints. So what landed is
a genuine *installed-software* census and only an indirect OS hint.

**The indirect signal is real but buried.** An OS token routinely appears inside
`unparsed_version` — `Windows NT 10.0` in a User-Agent, `Ubuntu-3ubuntu0.1` in an OpenSSH banner.
`unparsed_version` is carried on the `ServiceEvent` today and deliberately *not* published as a
node feature: a full User-Agent is near-unique per client, so it would enter the categorical
vocabulary as an unbounded dimension (`LES-017`). Mining it means parsing a hostile,
high-cardinality, self-reported string into a small closed OS vocabulary.

**What needs deciding.**
1. Is a *self-reported, trivially spoofable* banner acceptable evidence for a twin's `image:`
   field at all, or does that field want a different source entirely?
2. **58 of 5,624 observed entities is ~1% coverage.** Even a perfect OS extractor leaves 99% of
   hosts needing a catalog default. Does 1% change any decision downstream?
3. If mined, the extracted OS must land as its own bounded vocabulary — never the raw banner —
   and at a confidence *below* the `0.7` `runs_software` already carries, since it is an
   inference on top of a self-report.

### Candidate 2 — `snmp.display_string` (added 2026-08-12 by `Q24`'s ruling)

sysDescr is a **first-class device description**, not a token mined out of a banner — and it lands
where `software.log` structurally cannot: switches, routers, printers, the infrastructure that has no
browser and no SSH client to fingerprint. That is a different *population*, not more of the same one,
which is why `snmp` was admitted at 4/12 presence when `smtp` at 8/12 was not.

**MEASURED 2026-08-13 — empty in 7/7 real baseline rows, across all 12 windows.** It was admitted on
what sysDescr *is*, not on what this capture contains, and that is the exact move that produced this
question in the first place; the measurement confirms the worry rather than resolving it. **User
ruled to keep `snmp`'s admit as built anyway** (`docs/WORK_ORDER_zeek_stem_coverage.md` build step
3's review round, `R2`): an unset field never enters `attrs`, so nothing false publishes as an
`image:` feed today; the tuple activates automatically the moment a future capture populates the
field; and 7 rows is too small a sample to reverse an admit on. So candidate 2 is **not eliminated,
but not currently contributing** — `snmp` reduces to `("version",)` in practice on this baseline,
exactly the thin case this section warned about, and remains open pending a capture where
`display_string` is actually populated. See `Q24`'s "content measured 2026-08-13" for the full
finding.

### Candidate 3 — executable transfers (added 2026-08-12, from a user observation)

**The inference:** a host that downloads a Windows executable is probably a Windows host. Not always —
but at population scale, and especially over repeated observations, it is real signal.

Three things had to be corrected before this is usable, and each changes the design:

1. **`files.log` is the stem, not `pe.log`.** Verified against this repo's own documented shape
   (`_SCHEMAS["files"]`, `acquire/harvest/zeek.py`): `files.log` carries `uid`, the full
   `id.orig_h`/`id.orig_p`/`id.resp_h`/`id.resp_p` tuple, `is_orig`, and `mime_type` — **directly
   attributable, no join.** `pe.log` has only a file id and cannot name a host at all. So the
   attributable version of this inference is `mime_type = application/x-dosexec` on `files`, and `pe`
   is a refinement on top. **`files` is currently declared skipped** ("no event-model home today") —
   a value judgment made before this use case existed. Reopening it is the actual ask here.
2. **`files.mime_type` is three-way; `pe` is one-way.** PE is the Windows executable format and Zeek
   has no ELF or Mach-O analyzer, so `pe` can only ever emit "Windows" and its **absence must never be
   read as evidence against Windows.** `mime_type` distinguishes `application/x-dosexec`,
   `application/x-executable` (ELF) and `application/x-mach-binary`. Strictly more informative.
3. **Take `pe.machine`, never `pe.os`.** `os` is the PE header's *minimum* target version — developers
   set it low for compatibility, so a binary targeting "Windows XP" runs fine on Windows 11 and
   reading it as the host's OS would put dead operating systems into twin configs. **This is `Q26`'s
   own failure mode repeating**: a field whose name promises what its content does not deliver.
   `machine` (`I386`/`AMD64`/`ARM64`) is a hard constraint — an AMD64 binary cannot run on a 32-bit
   host — and architecture is a twin config field in its own right, independent of the OS question.

**`is_orig` is the correctness hinge.** A file flowing *to* a host is evidence about that host; a file
flowing *from* one is not — a Linux web server, an SMB share, or a patch mirror serves Windows
binaries all day. Getting the direction backwards inverts the inference on precisely the hosts (file
servers) where it does the most damage.

**Independence check (`LES-089`).** Be honest that this mostly **corroborates rather than discovers**:
a host pulling Windows executables is almost certainly also doing NTLM, SMB or DCE-RPC, all now
admitted and all stronger. Its value is the narrow case — a host that only ever does HTTP through a
proxy, where a downloaded `x-dosexec` may be the only OS evidence there is. Same shape as the LDAP
argument: the worth is in the hosts the other sources do not reach.

### What this costs that the project does not have yet

**This would be the project's first `imputed` assertion.** Grepped and confirmed: `imputed` appears in
`src/` only as `FlowEvent.counters_imputed` and window-health counters; `_assert()` defaults to
`observed` and **no assertion is ever emitted with `evidence_class="imputed"`.** Like `referenced`
(`Q24`'s amendment), the class is in the design and has no emitter. Candidate 3 is a clean first case
because the split is unambiguous:

- **observed** — "an `x-dosexec` file was transferred to `10.1.2.3` at T." A fact.
- **imputed** — "`10.1.2.3` runs Windows." An inference, with a real confound: a host can download a
  binary it never runs (scanners, backup agents, an admin staging installers for other machines).

**And it wants confidence to scale with observation count**, which is a mechanism that does not exist.
One download is weak; forty over a week is strong. `SERVICE_PROJECTIONS` declares a **fixed**
confidence per kind — every kind gets one number regardless of how often it was seen. Candidate 3 is
the first fact that wants otherwise, and that is a new mechanism to decide deliberately, not a
parameter tweak. Whatever it gets should sit **below `software`'s 0.7**, since it is an inference
layered on an observation rather than a self-report — 0.5 is the working proposal.

### What needs deciding, consolidated

1. **Measure before building, all three.** Candidate 1 is measured and failed. Candidates 2 and 3 are
   not measured at all. The baseline at `/tmp/aximprint-zeek-test/` answers both cheaply, and reading
   it is no longer gated (CONVENTIONS §3, 2026-08-12): is `snmp.display_string` populated, and how
   many hosts *receive* an executable mime type with `is_orig` resolved correctly?
2. **Is `image:` a single-source field or a fused one?** Three weak sources over disjoint populations
   (browsers ~1%, infrastructure via SNMP, executable-receivers) may cover more together than any one
   does alone — but fusing them needs a rule for disagreement, and nothing in the store does fusion
   today. This is the question the other two hang off.
3. **Does the catalog default win anyway?** Even all three combined will leave most hosts with no OS
   evidence. If `image:` falls back to a catalog default for 90% of nodes regardless, the honest
   answer may be that the *provenance marking* matters more than the coverage — `role: defaulted`
   already exists in the synthesizer for exactly this.
4. **Reopen `files` (and `pe`)?** `Q24` declared both skipped, `pe` explicitly as "file-analysis
   family, no entity." Candidate 3 is the counter-argument, and `files`' direct 4-tuple is what
   defeats the "no entity" reason. That reopening belongs to this question, not to `Q24`.

**Not urgent, and nothing is blocked.** The `runs_software` census is useful on its own (service/role
signal, and the only current input to `install:`).

---

## Q27. Every `ServiceEvent` kind except `software` is parsed and then dropped — what bounded shape lets the high-volume ones reach the belief store?

**Status:** 🟢 **CLOSED 2026-08-12 by user ruling** — option **A** (generalize the `software`
per-entity capped-set pattern to every admitted kind) **with C's per-kind projection table**
(each kind declares what its *durable fact* is, since that is a judgment, not an algorithm).
Opened 2026-08-11, measured on the real-capture step-3 run (CHANGELOG 2026-08-11).

**What the measurement changed.** The question was opened assuming volume implied cardinality —
"78,890 ssl events in a single window" needing a sketching or probabilistic design. Measured, the
volume is **repetition, not distinct facts**: collapse ratios of 45x (`ssl`), 38x (`ntp`), 113x
(`ldap_search`), 49x (`dce_rpc`), and — the number that decided the design — **no entity anywhere
exceeded 13 distinct facts for any kind.** The `software` round's existing cap of 32 already
covered every kind observed. No new bounding machinery was needed; the simple pattern was
sufficient, and the original framing was wrong.

Also checked, because it was the real cardinality risk: `NODE_CATEGORICAL` is an **explicit opt-in
list** (`proto_mix`, `listen_ports`, `state_mix`), so node features do not auto-enter the
categorical vocabulary. Landing service facts costs nothing against `MAX_KEYS = 512`.
Vocabularizing them later is a separate decision with a real cost (`ssl` alone is 1,764 distinct
globally) and stays deferrable.

**What landed.** `ssl` → `(server_name, version, ja3s)`, `known_service` → `(port_proto, service)`,
`dce_rpc` → `(endpoint, operation)`, `smb` → `(share_type, service)`, `software` →
`(software_type, name, version.major, version.minor)`. Declined **by name with reasons**, not by
omission: `ntp` (protocol trivia, no synthesizer field), `ldap` (one query's outcome, not a
property; the serving fact already arrives via `known_service`), `smb_files` (per-action, and
`path` carries share paths — refused on privacy grounds, `LES-003`, for 3 distinct facts from 278
rows). A kind in **neither** table is counted as `unmodelled_service_kinds` in the window health
record rather than silently dropped.

**Two of those three declines are void as of 2026-08-12** (CHANGELOG 2026-08-12, forensic
completeness + the referenced-entity correction), and this is recorded here rather than silently
re-ruled because both were **user rulings** made the day before:
- **`smb_files`** — the privacy half of its reason is gone outright, and so is the parser's own
  filename drop. What survives is a *value* argument ("3 distinct facts from 278 rows") and a new
  *cardinality* one — but not "once `name` **and** `path` are collected in full," which overstates
  it: **`path` was already collected** (`_attrs(r, ("action", "path"))`, live before build step 2b),
  so the 278→3 measurement was taken with `path` already in hand, and the entire cardinality delta
  once `name` joins it (landed, build step 2b, CHANGELOG 2026-08-13) belongs to `name` alone. This
  kind becomes high-cardinality on `name`'s account, and its bound, not its disclosure, is the
  question. Needs re-ruling on those terms.
- **`ldap`** — reopened by the referenced-entity argument. `base_object`/`filter` name computer
  accounts, users and OUs, i.e. entities that may have no address the tap ever saw. "The serving fact
  already arrives via `known_service`" is true only for hosts already observed, which is the flaw
  `Q24`'s amendment names.
- **`ntp` is now the third void decline** (added 2026-08-13, by `Q24`'s third amendment). Its stated
  reason — *"version/mode/stratum describes the protocol exchange, not a durable property of the host
  — sensor trivia the synthesizer has no field for"* — is wrong twice under the `tfc` lens, and there
  is a third reason review surfaced that survives fixing the first two. **(a)** `ntp.log` carries a
  **`poll`** field, verified present on the baseline, and a poll interval is precisely the "fitted
  schedule" DESIGN_BRIEF §9 says the traffic profile wants; the synthesizer does have a field for it,
  in the artifact this reason did not consider. **(b)** `_COLUMNS["ntp"]` took only
  `version`/`mode`/`stratum`, so the parser **dropped `poll` before anything could weigh it** — a
  forensic-completeness violation in its own right, **fixed in build step 2b** (`poll` now flows
  through `_COLUMNS["ntp"]` and `_ntp`'s `attrs`, CHANGELOG 2026-08-13). **(c) — new, and not fixed by
  (b): a keying mismatch.** `_ntp` keys the `ServiceEvent` on `id.resp_h`, the server, but `poll` on a
  mode-3 row is the **client's own** chosen polling interval — projecting it as-is would attribute a
  client's schedule to the server it happens to poll. This is a reason distinct from "no channel," and
  it **survives `Q29` closing**: even once the store can hold a subject-less fact, `poll` still cannot
  simply become a property of *this* event's `ip`. The *projection* decline itself is a user ruling of
  2026-08-11 and stays flagged here, not re-decided; re-ruling it needs both `Q29` (a channel for a
  population-level parameter) and a resolution to (c) (whose subject a client-keyed fact gets), not
  just (b)'s field-collection fix, which is done.

**Verified on the real capture:** 2,955 service assertions over 1,177 distinct subjects (was 100
over 58, `software` alone); confidence banded 0.95 / 0.9 / 0.7 by evidence quality; the cap never
fired; zero unruled kinds. Two follow-ons deliberately NOT taken: vocabularizing these keys, and
the `smb_files`/`ntp`/`ldap` reversals — each would need its own ruling.

**What was found.** `WindowResult` carries `nodes`, `edges`, `identity_delta` and `health` — and no
channel a service fact can ride. A comment in `windowing/windows.py` claimed ServiceEvents "pass
through to extractors"; they could not, and did not. Measured on 2 hours of real traffic:
**~93,000 ServiceEvents parsed and discarded** — `ssl` 78,890, `ntp` 10,783, `ldap_search` 1,127,
`dce_rpc` 1,084, `known_services` 856, `smb_files` 278, `smb_mapping` 8. Every assertion in the
resulting belief store traced to a Flow, Identity or Alert event; **none to a Service one.**

`software` was given a home in the same round because it is naturally bounded per entity (a host
runs a handful of identifiable programs). The rest are not, and that is the whole question.

**Why it is not simply "add a services list."** 78,890 `ssl` events land in a single window. An
unbounded per-window list reproduces `Q9`'s shape — where `bound_dns` alone is 93% of the belief
store — one layer over. Each kind needs its own answer about what the *durable* fact is:
`ssl` probably wants a per-entity server-name/JA3 census, not one row per handshake; `known_services`
is already a census and may be near-free; `smb_files` is per-action and may not belong at all.

**Why it matters.** These are the richest service facts the pipeline parses, and they are exactly
what a synthesizer would need for `install:` and for role classification. Today the cost of parsing
them is paid and none of the value is collected. Related: `Q8` (sketches are O(input), not
O(output)), `Q9` (emit-on-observation), `Q14` (TopK tie-breaking can renumber a published column).

---

## Q28. `analyzer.log` says which protocol analyzers Zeek disabled or saw violated — is that a `SensorHealthEvent`, i.e. evidence of what the sensor *could not* see?

**Status:** 🟡 OPEN. Opened 2026-08-12 by `Q24`'s ruling, which declared `analyzer` skipped as
"Zeek's own diagnostics, not traffic" — a correct classification that is nevertheless not the same
statement as "of no use to this pipeline."

**Why it is a question at all.** `Q24`/D5 closes half of a gap: after it, the belief store can say
*"log type `smtp` was present and this build does not parse it."* `analyzer.log` is the other half,
one layer earlier — Zeek's own report of analyzers it **disabled** after repeated protocol
violations. When that happens, a protocol is running on the wire and no log stem for it is produced
at all, so D5's inventory-based mechanism cannot see it: there is no unparsed stem to name. The
window health record would read clean.

That is precisely the "could not have seen" case `docs/WORK_ORDER_network_ir.md` D4 names as
missing — the third state alongside "not seen this run" and "seen absent" — and it is the one state
the pipeline currently cannot represent anywhere.

**What is not yet known, and should be measured before any design.** `analyzer.log` appeared in 9/12
windows of the 2026-08-11 baseline, and **its contents have never been looked at.** Zeek writes both
routine per-analyzer confirmations and genuine violation/disable records to it; if this network's
9/12 is entirely the former, the honest answer is "declared skipped, correctly, and that is the end
of it." The measurement is cheap (tier 3, `/tmp/aximprint-zeek-test/`, `LES-003`) and it comes before
the decision, not after — the ordering `Q26` exists to enforce.

**Decide, in this order:** (a) does the baseline's `analyzer.log` contain any `disabled` or violation
record, or only confirmations; (b) if it does, is the durable fact a `SensorHealthEvent` kind (say
`analyzer_disabled`, `detail` = analyzer name) inheriting the existing health-record path, or does it
belong beside D5's `unparsed_log_types` as a second completeness field; (c) whether `reporter.log`
needs the same look — it carries Zeek's script errors, which are a different failure mode with the
same consequence.

**One thing that makes (b) easier than this question assumed when it was opened.** `analyzer.log` in
Zeek 6+ carries `uid` and the full `id.orig_h`/`id.orig_p`/`id.resp_h`/`id.resp_p` tuple alongside
`cause`, `analyzer_kind`, `analyzer_name` and `failure_reason`. So "this analyzer failed" is
**attributable to a specific connection and host**, not a run-level statement — which means it could
ride the existing per-window `SensorHealthEvent` path with a real subject, rather than needing D5's
run-level machinery. `reporter.log` is the opposite (`ts`, `level`, `message`, `location`, no
connection fields), so (c) genuinely is a separate design, not the same one applied twice. Both
claims are from Zeek's documented shapes and unverified against the baseline — check them in (a),
since the same read answers all of it.

**Not resolved by:** `Q24`/D5, which covers stems that were *produced* and not parsed — the disjoint
case. (`src/aximprint/events/model.py` `SensorHealthEvent`,
`src/aximprint/windowing/windows.py` `health`, `docs/WORK_ORDER_network_ir.md` D4)

---

## Q29. The belief store has no channel for a fact with no subject — and `tfc`, a declared output, is made of exactly those

**Status:** 🟢 **CLOSED 2026-08-13 by user ruling** — option **2** (admit subject-less facts as a new
channel), over declaring `ocsp` skipped citing this question, leaving it unrecognized, or giving it
a subject via the `files` join (which becomes option 1, **declined**, not merely unbuilt — see
below). Design proposed by Claude Code the same day and approved by the user before any code
(`docs/WORK_ORDER_subject_less_facts.md`, LANDED). Opened 2026-08-13 by `Q24`'s third amendment,
which admitted `ocsp` for a traffic-profile parameter and then could not say where the parameter
goes.

**What landed.** `ProfileEvent(ts, parameter, value)` (`events/model.py`) — deliberately **no
subject field**, not a nullable-`ip` variant of `ServiceEvent`: a reserved sentinel entity
(`__capture__`) would only re-encode the same ambiguity as a *value* every consumer must know to
filter, rather than a property the schema itself states (`LES-006`'s "no evidence vs. evidence of
none" distinction, applied here to a subject rather than a field). `windowing/windows.py` gains
`PROFILE_PARAMETERS` (parameter → accumulator + unit — same declared-table discipline
`SERVICE_PROJECTIONS` established: a parameter is declined by name, never by omission, and one
absent from the table is counted (`unmodelled_profile_parameters`), never silently accumulated) and
a fourth `WindowResult` channel, `capture_profile` (named that, not `profile`, so a generic
`for k, v in win.____.items()` can never read identically for the per-entity channel and this
subject-less one). A negative value is refused and counted (`profile_values_refused`), never
accumulated. `store/belief_store.py` gains `profile_facts (window_start, parameter, stat, value,
unit)` — **no subject column**, per-window (not per-run: DESIGN_BRIEF §10's overfitting guard needs
the finer resolution), and deliberately **not** in `NONDETERMINISTIC_TABLES` — a window's profile
facts are as pure a function of (events, resolver state, code build) as any other window-scoped
table. `ingest/zeek/plugin.py::_ocsp` is the first occupant, yielding parameter
`ocsp_cache_lifetime` as `nextUpdate − thisUpdate`, read through `_num` (never `_f`) so an unset
timestamp is never coerced into a manufactured delta.

**What review found before landing, three gaps:** (1) the determinism gate's `canonical_windows()`
omitted the new channel entirely while `belief_store.py`'s own docstring already claimed it was
byte-deterministic — a claim nothing was checking; fixed by adding the key. (2) A negative delta
could reach the sketch with every health counter reporting clean — `LES-005` recurring verbatim,
through the sensor's own payload this time rather than the ingest reorder buffer — fixed with the
`>= 0` gate above. (3) The declared `unit` was computed and discarded before reaching the store —
`LES-015` recurring — fixed by carrying it on `profile_facts` itself, looked up from
`PROFILE_PARAMETERS` at flatten time rather than mixed into the per-stat dict.

**What was measured, per this channel's own stated validation requirement.** `ocsp_cache_lifetime`:
1031 events, three distinct deltas overall — but segmented by `issuerNameHash`, **each issuing CA
emits a single constant value**. This is a **CA policy constant, not a network-behavioral signal**,
materially weaker justification than the ruling assumed when `ocsp` was admitted. The channel itself
remains justified (a place for a subject-less fact to exist, independent of any one parameter's
content); this is a finding about the first *occupant*, not the design.

**What this deliberately does not do.** Write the `tfc` compiler (`synth/` stays a stub — this lands
the store *shape*, and `tfc` is no longer *architecturally* blocked without becoming buildable — the
compiler and the learner layer that would fit its parameters remain unbuilt). The `files`-join
alternative (attributing an OCSP response to the client that fetched it) — declined, not deferred,
since it costs a stateful cross-log join and "arguably invents per-host precision the evidence does
not have." Give `ProfileEvent` a subject of any kind, sentinel included. Read `profile_facts` back
in `snapshot()` — aggregating a per-window quantile summary across windows is a real design question
left to whoever builds the `tfc` compiler.

**Opens, unnumbered so far:** which of `tfc`'s declared contents (DESIGN_BRIEF §9) are
population-scoped, belonging on this channel, and which are per-entity after all — nobody has
enumerated §9's fields against that split. Worth a `Q<n>` when someone picks it up.

**Resolved by:** `docs/WORK_ORDER_subject_less_facts.md`; CHANGELOG 2026-08-13.

<details><summary>Original question, kept for the reasoning</summary>

**The gap, concretely.** Every path into the store is keyed on an entity. `ServiceEvent` requires an
`ip` — `_add_service` returns early on `if not ev.ip` — `IdentityEvent` binds an address, `FlowEvent`
has two, and the assertion shape is `(subject, predicate, object, …)`. There is no subject for a fact
that is **true of the capture rather than of any host in it**.

`ocsp` is the first admitted stem in that shape. Its verified header carries no addresses at all
(`ts id hashAlgorithm issuerNameHash issuerKeyHash serialNumber certStatus revoketime revokereason
thisUpdate nextUpdate`), and the thing worth having — `nextUpdate − thisUpdate`, a cache lifetime —
is a **population-level cadence**, not a property of a machine. Two ways to land it, and the choice
is the question:

1. **Give it a subject by joining.** `files.log` carries `fuid` plus the full connection tuple, so an
   OCSP response can be attributed to the client that fetched it, yielding "this host validates certs
   and honors N-second cache windows." Costs a stateful `fuid` join across logs, with its own
   ordering and window-boundary problems, and arguably invents per-host precision the evidence does
   not have.
2. **Admit subject-less facts.** A new channel for run- or population-scoped parameters, which is
   what `tfc` actually consumes.

**Why this is bigger than `ocsp`.** DESIGN_BRIEF §9 declares `tfc` a first-class artifact whose
content is "fitted **intensities/schedules/seeds**." Most of those are population-level by nature —
a per-protocol request rate, a diurnal shape, a think-time distribution, an RNG seed. If every fact
must be keyed on an entity, **the traffic-profile arc has nowhere to put its output**, and the
synthesizer's `tfc` half is blocked on a store shape nobody has designed. `ocsp` is the first stem to
hit it; it will not be the last.

Note the symmetry with `Q1`, which is the *other* half of the same deficiency: `Q1` is a fact whose
subject exists but cannot be represented (a host known only by name); this is a fact with no subject
at all. Both surfaced in the same round, from opposite directions, and both currently block real
evidence from reaching the store.

**Decide:** (a) does `tfc` get its own store shape — a `profile_parameters` table beside
`node_features`/`edge_features`, run-scoped like `runs` — or is every parameter forced onto an
entity; (b) if a channel is added, what is its provenance shape, given that a fitted intensity is
`imputed` by construction and `evidence_class` currently rides per-assertion; (c) whether `ocsp` in
particular is worth the `files` join for per-host attribution *in addition*, or whether the
population-level parameter is the whole of its value.

**Not resolved by:** `Q1` (adjacent, opposite direction — see above), `Q27` (bounded *per-entity*
service facts, which presumes an entity), or `Q24`/D5 (`unparsed_log_types` is run-scoped but lands
in the per-window health record, which is a precedent worth reading before designing this — it is
the closest thing the store has to a subject-less fact today).
(`src/aximprint/windowing/windows.py` `_add_service`, `src/aximprint/store/belief_store.py`,
`docs/DESIGN_BRIEF_aximprint.md` §9)

</details>

---

## Q30. The Parquet archive's own converter never records a stem it declines to convert — so this transport's completeness gap is structural, not just today's honest default

**Status:** 🟡 OPEN. Opened 2026-08-13, while building `docs/WORK_ORDER_zeek_stem_coverage.md` build
step 4 (D5's `unparsed_log_types`/`unrecognized_log_types`). `ZeekSource.inventory_is_complete`
(`False` on `ParquetArchiveSource`, unconditionally) is what keeps this honest **today** — a run over
the archive transport correctly reports `None` (unaudited) rather than a false `{}` (audited, clean)
for these two health fields. That default is the right answer to the *symptom*; this question is
about the *cause* underneath it, which the default works around rather than closes.

**The gap, concretely.** `scripts/make_zeek_archive.py`'s own stem loop gates on `if stem in
zp._COLUMNS: ...` — `_COLUMNS`'s key set is exactly `_PARSERS`'s (checked by an import-time assert in
`plugin.py`) — so a stem this build declines by name (`_SKIP`) or has never heard of at all gets **no
partition directory and no `logs` entry in `manifest.json` at all**. There is nothing for
`ParquetArchiveSource` to discover — not "the archive audited and found nothing," but "the archive
was never given the chance to look," a distinction `inventory_is_complete=False` names correctly but
cannot close. An archive built today from a capture that includes such a stem drops that fact from
the manifest permanently, even though the flat-directory or window-tree transport reading the
identical underlying logs can see it directly.

**Why it is the structural sibling of `Q28`.** `Q28` is the sensor's own blind spot — a disabled
Zeek analyzer produces no log stem at all, so no downstream mechanism can see what was never written.
This is one layer downstream of that: the log stem **was** written, the cold tier **does** carry it,
and a filesystem-walking transport reading the identical bytes can audit it completely — but the
*converter* interposed between the cold tier and this transport silently narrows what survives
conversion. Two different "could not have seen" states, at two different layers, each
un-representable today by the mechanism nearest to it until designed.

**Why it is not urgent.** `CLAUDE.md`: since the 2026-08-10 merge, "the cold tier of Zeek logs is the
primary read path" and this archive is "an optional internal read-cache," not an external contract.
The gap this question names sits entirely inside that optional cache — the primary path
(`LogDirSource`, flat or window-tree) has no equivalent hole. Nothing is blocked; this is recorded so
the limitation is named rather than rediscovered as a surprise the day someone rebuilds an archive and
expects its `unparsed_log_types` to read like the flat transport's.

**Decide:** (a) should `make_zeek_archive.py` write a manifest entry for a stem it declines to
convert — recording that the stem existed, and how many rows/files, without converting any of its
columns — so a rebuilt archive's own `inventory` could include it and `inventory_is_complete` become
`True` again, per-instance, the way `LogDirSource` already is (not a blanket class constant); (b) if
so, does a stem later promoted from skipped/unrecognized to parsed require the archive to be
**rebuilt**, not merely re-read, to gain real columns for it — a producer-side dependency on
`plugin.py`'s own parser table that does not exist today, and does not exist for the flat/window-tree
transports either; (c) or is `inventory_is_complete=False` the correct **permanent** answer for this
transport — "defer completeness questions to the transport that can actually audit them" — rather
than a stopgap to be engineered away.

**Not resolved by:** `Q28` (the sibling gap, one layer earlier — sensor-side, not converter-side).
`Q25` (a different Parquet-adjacent question — Zeek's own non-reproducibility across a re-harvest,
unrelated to what the converter chooses to write). `Q15` (dissolved) — adjacent in subject (archive
completeness) but about compatibility across an external producer/reader pair, not about what a
stem-declining converter records.

`docs/SPEC_zeek_metadata_archive.md` says nothing about this today — its "Columns" table and
`manifest.json`'s "logs" description both assume every declared stem is fully converted, with no
provision for a present-but-declined one. A short pointer to this question now sits in that spec's
own `manifest.json` section.

(`scripts/make_zeek_archive.py`, `src/aximprint/ingest/zeek/sources.py::ParquetArchiveSource
.inventory_is_complete`, `docs/WORK_ORDER_zeek_stem_coverage.md` D5 and build step 4,
`docs/SPEC_zeek_metadata_archive.md`)

---

## Q31. `role` is assigned to internet peers, and only one of the IR's four consumers filters them out

**Status:** 🟡 OPEN. Opened 2026-08-19, from the first materialization of a real ten-day, twelve-stream
capture. Not covered by any work order, deliberately — it is a question about what a field *means*,
and answering it wrongly is cheap to do and expensive to undo.

**The measurement.** Of **58,563** entities in `pit-bf1f8c233b-1786978566`, **15,741 carry a role**
— but only **430 are placed** in a segment and only **85 are both**. So **15,215 external hosts are
labelled `web-tier`** and **439 `dns-resolver`**. Every one of those is an internet server this
network merely talked to.

**Why it is not simply a bug.** `has_role` is `imputed`, inferred from observed ports, and an
internet web server genuinely does answer on 443. The classifier is not lying; it is answering a
question nobody meant to ask it. `catalog/roles.yaml` has no notion of *whose* estate a host belongs
to, and `locality` — which does — is computed elsewhere and never consulted.

**Why it matters.** `synth/compiler.py` intersects role with **placed**, so `cast` is unaffected: it
emitted 85 candidates, not 15,741. But the IR has **four** consumers on independent lifecycles (twin
compiler, drift comparator, GNN evaluation harness, topology-diagram generator), and only the first
applies that filter. A comparator or diagram generator reading `ir_entity.role` directly gets 15,656
roles describing the internet rather than the estate — and for drift specifically, the external
population is the one that churns most (a naive external diff was already measured at **97% noise**,
`docs/WORK_ORDER_network_ir.md` step 5).

**The three candidate answers, none obviously right.**
1. **Do not assign a role to a non-local entity.** Cleanest to read, and it destroys information: an
   external host's role is a real observation, and §8's tap-placement work may want it.
2. **Keep it, and make every consumer filter.** Preserves information, and repeats the filter in four
   places on four lifecycles — exactly the duplication that `LES-001`'s versioned-schema reasoning
   says a shared artifact exists to prevent.
3. **Split the field** — `role` for the estate, something else for observed-service-of-a-peer. Most
   honest, most invasive: `ir_entity` is in the **versioned** schema, so this is a schema event.

**What would settle it:** whether a drift comparator run over two real PITs produces materially
different answers under (1) and (2). That is measurable now — the store and the PIT both exist —
and nobody has run it.

(`src/aximprint/catalog/roles.py`, `src/aximprint/ir/materialize.py`, `src/aximprint/synth/compiler.py`,
`docs/SPEC_network_ir.md` `ir_entity.role`/`.locality`, `docs/WORK_ORDER_network_ir.md` D4/step 5)

## Q32. ARP is parsed and emitted but anchors nothing — what scope and confidence does it get, and what guards it against proxy-ARP / VRRP MAC fan-out?

**Status:** 🟡 OPEN. Opened 2026-08-25 by the Suricata ingest plugin (Track B,
`docs/WORK_ORDER_axrange_integration.md`). **The cross-branch caveat is spent** (2026-08-26): this
question was opened saying the parser lived on `wip/axrange-integration` while the two files that
would answer it — `src/aximprint/windowing/entities.py` (`IDENTITY_SCOPES`) and
`src/aximprint/windowing/windows.py` (`IDENTITY_PROJECTIONS`) — were owned by `wip/axquery-ingest`.
That branch merged to `master` and this one was rebased onto it, so parser and tables now sit in
one tree and the row can land beside the parser that motivates it. Nothing else below changed.

**The current state, and it is the honest one.** `ingest/suricata/plugin.py::_arp` parses ARP
sender fields into `IdentityEvent(kind="arp")` — mac↔ip, gated on a declared opcode, declared
hardware/protocol address families, a claimable (non-probe, non-multicast, non-broadcast) sender
address and a parseable unicast MAC. Neither `IDENTITY_SCOPES` nor `IDENTITY_PROJECTIONS` has an
`arp` row, so the resolver **counts** every one of them in `unscoped_identity_kinds` and binds
nothing. (It is *not* also counted in `unmodelled_identity_kinds` — that counter fires per opened
binding, and an unscoped kind opens none.) `scripts/verify_suricata_equivalence.py` asserts exactly
this three-part state and carries a **trip-wire**: the day an `arp` row lands in `IDENTITY_SCOPES`,
the gate fails loudly rather than silently re-classifying an axis it compares.

**Why it is not a one-line edit** (`LES-129`). ARP's sender fields are a self-assertion only when
one interface answers for its own address. Two ordinary deployments break that:
- **proxy ARP** puts a router's MAC beside an address belonging to some host behind it;
- **VRRP / HSRP** share one virtual MAC (`00:00:5e:00:01:xx`, `00:00:0c:07:ac:xx`) across every
  member of a redundancy group.

Host-scoped ARP with no many-addresses-per-MAC guard therefore collapses N distinct hosts into one
entity, and `Q1` D2 makes merges **forward-only** — no later observation undoes it. A false merge
fabricates a host that never existed and corrupts every downstream drift claim; a missed merge
leaves two honest address-keyed entities. There is no recall argument that buys back that precision
(`LES-121`'s asymmetry, arriving from a non-ML direction).

**What needs deciding, together — none of these is separable from the others:**
1. **Scope:** `host` (it may merge two addresses) or `referenced` (it may not)? `host` is what the
   evidence *is* when the assumption holds and what makes ARP the highest-value record type this
   plugin has for the resolver; `referenced` is the safe answer that makes parsing it nearly
   pointless.
2. **The fan-out guard**, if `host`: a threshold on distinct addresses per MAC within a window (and
   what happens *retroactively* to bindings already opened when the threshold is crossed — a
   forward-only merge cannot be withdrawn, so the guard must fire **before** the merge, which
   argues for holding ARP bindings until the guard has seen enough of the window), and whether the
   two virtual-MAC ranges above are refused **by name** as a separate, cheap first cut.
3. **Confidence** in `IDENTITY_PROJECTIONS`: ARP is unauthenticated and trivially spoofed, so it is
   not obviously the same confidence as a declared DHCP lease.
4. **The cross-sensor consequence**, which is why the gate trip-wires rather than tolerates: Zeek
   ships **no `arp.log`**, so a scoped `arp` makes Suricata able to anchor hosts Zeek structurally
   cannot. That is a real capability difference and it must be asserted as one, not discovered as a
   drift result.

**What would settle it:** count distinct addresses per sender MAC in a real capture's ARP, on a
vantage that includes a first-hop router. The shape of that distribution decides both the threshold
and whether a threshold is even the right mechanism.

(`src/aximprint/ingest/suricata/plugin.py::_arp`, `src/aximprint/windowing/entities.py`
`IDENTITY_SCOPES`, `src/aximprint/windowing/windows.py` `IDENTITY_PROJECTIONS`,
`scripts/verify_suricata_equivalence.py` section 5, `Q1` D1/D2, `LES-129`)

## Q33. `IdentityEvent.principal` has no declared grammar, so an unparseable principal is silently classified as a user

**Status:** 🟡 OPEN. Opened 2026-08-25. **This is the root cause of the finding behind `LES-123`**,
and it survives that finding's fix: the equivalence gate now *detects* the symptom, but nothing in
the event model prevents the next producer from re-creating it.

**What happened.** The Zeek fixture rendered a Kerberos principal as `ws000$@CORP.AXR.LAB` and the
Suricata plugin rendered the same. `windowing.entities.kerberos_identifier` parses an RFC 4120
principal as `component[/component...]` with the **realm last**, splitting on `/` — Zeek's own
`krb.log` `client` field joins principal and realm with `/`, not `@`. So on an `@`-joined value the
"primary" is the whole string, it does not end with `$` and it is not `host`, and the parser takes
its third branch: **a USER principal**, kept verbatim, host scope denied, anchoring nothing. No
error, no counter, no health field — the machine account silently became a person.

**Why the field is the right place to fix it.** `IdentityEvent.principal` is consumed by exactly
one parser with exactly one grammar in mind, and **two independent producers** wrote a value that
grammar cannot read. Neither producer was told what shape was expected, because the field does not
say. The two candidate answers are mutually exclusive and both are cheap:

1. **The field declares its grammar** — "RFC 4120 `component[/component...]`, realm last" — and
   every plugin normalizes to it at the boundary, which is where a sensor's own spelling should
   stop mattering. This is the `events/` layer doing the job it exists for.
2. **`kerberos_identifier` refuses an unparseable shape loudly** — counted by name, the way
   `FlowEvent.state`/`state_class` already counts an unmapped state — instead of falling through
   to `user`. The fall-through is the defect: `user` is not a neutral default, it is a positive
   claim that this principal names a person, and it is the claim that costs the resolver a host
   anchor.

They are not really alternatives: (2) is what makes (1) enforceable, and without (2) a declared
grammar is a comment. **Note the interaction with `Q23`** — that question asks whether
`IdentityEvent.kind` should classify-and-count an unrecognized *value* the way `FlowEvent.state`
does; this is the same shape one field over, applied to a *structured* value rather than an
enumerated one, and the two should probably be ruled together.

**What it costs today, measured:** on the synthetic plan, **20 of 20** Kerberos machine accounts
resolved at `user` scope and anchored nothing, on **both** sensors, and every cross-sensor
comparison agreed. A real capture's Zeek `krb.log` uses `/` and parses correctly, so this is a
fixture-and-plugin defect rather than a live-data one — which is precisely why it needs a
structural answer rather than a fixture fix.

(`src/aximprint/events/model.py` `IdentityEvent.principal`, `src/aximprint/windowing/entities.py
::kerberos_identifier`, `src/aximprint/ingest/suricata/plugin.py::_krb5`, `Q23`, `LES-123`)

## Q34. `s0_count` / `rej_count` key on Zeek-verbatim `"S0"` / `"REJ"`, so they are structurally 0 for every non-Zeek sensor

**Status:** 🟢 **CLOSED 2026-08-26 — both counters DELETED, not repaired and not renamed.**
Opened 2026-08-25. **Pre-existing** — it predated the Suricata plugin by the whole life of
`windowing/windows.py`; a non-Zeek sensor was simply the first thing that could expose it. Nothing
was ever wrong on the Zeek path, and nothing moved there: because the answer was deletion rather
than re-keying, no existing Zeek value shifted (a repair would have widened `s0_count` from `S0`
alone to `S0 ∪ SH ∪ RSTOS0`).

**What was measured before choosing.** (a) **Consumers:** none in `src/` outside the emitter —
`learners/extractors.py` reads only `tainted` from the node bundle; `synth/`, `ir/`, `store/` and
`features/jsonl.py` pass the dict through wholesale. Exactly **two** test assertions read the two
names. So the "which consumer wants Zeek-exactly-`S0`?" question below has an empirical answer:
**no consumer wants either reading**, because no consumer reads them at all. (b) **The published
damage:** `axquery` had shipped `s0_count: 0` beside **2,037** `attempted` flows on every node since
2026-08-20, and `refused` was never emitted on that sensor at all — a confident `0` reading as
*observed none* where the truth was *not measured*, `LES-006` exactly inverted. Five days, unnoticed,
until a different sensor's gate landed.

**Why deletion beat repair-and-rename.** Keying on `state_class` makes the two scalars **exactly**
`state_mix["attempted"]` and `state_mix["refused"]` — same row, same dict, same window.
`features/vocabulary.py` declares `CategoricalField("state_mix", "dict", "count",
closed=FLOW_STATE_CLASSES)`, so both classes are guaranteed index-map columns whether or not a window
observes one, and an absent class is a **proven** zero because `state_mix`'s keys sum to `flows_out`.
A repaired scalar would therefore have been a second name for a number already published in the same
row, with nothing checking the two agree (`LES-101`). A *rename* was worse still, and that is the
finding worth more than the fix: `BeliefStore._check_schema_matches_this_build` — the project's
fail-loud answer to "an old store disagrees with this build" — compares **table columns**, and these
fields live inside `node_features.features`, a **JSON** column. The guard is structurally blind to
every key inside `node_features.features`, `window_health.health` and `entity_roster`'s JSON columns.
So a rename would have left a store holding `s0_count` rows (Zeek semantics) *and* `attempted_count`
rows (normalized semantics), indistinguishable, with the guard that exists to prevent exactly that
mixture unable to see either. Deletion leaves an old store's `s0_count` **silently absent** from new
windows — not loud, but unambiguous, which is the best available outcome and is now `LES-131`.
**Whether the guard is extended to cover blob keys is `Q38`, opened by this close and unruled** — it
is deliberately a separate question, because closing it means declaring a key manifest for an
incubating surface, which is the user's call (`LES-001`).

**The reopen condition, recorded so it is not lost.** If a consumer ever genuinely needs the finer
**sensor-native** split — Zeek's `S0` as distinct from `SH` and `RSTOS0`, which is a real
distinction the normalized `attempted` discards — the right shape is a **per-sensor `state_raw_mix`
beside `state_mix`**, a declared census over the sensor's own vocabulary, documented as **not
cross-sensor comparable**. It is never two scalars named for one sensor's spellings in an artifact
every sensor writes (`LES-132`).

**Verified at close:** 1,728 tests; `bash scripts/verify_tier.sh` exit 0; `grep -rn
"s0_count\|rej_count" src/` → 0 hits. See CHANGELOG 2026-08-26, `LES-131`/`LES-132`/`LES-133`.

---

*The original question, kept for the record:*

**Amended 2026-08-26, and the amendment is the more interesting half.** It was not Suricata that
first exposed this. `axquery` landed **2026-08-20**, five days earlier, and its `FlowEvent.state` is
a derived raw pair (`O:<flags>/R:<flags>` — `ingest/axquery/plugin.py::_state_class`), never the
strings `"S0"`/`"REJ"`. Every axquery run since has published `s0_count: 0` / `rej_count: 0` on
every node, confidently, and **nobody noticed for five days**. A second implementation *creates* the
exposure; only something that **compares** implementations converts it into a finding — which is
`LES-123` arriving from the opposite direction, and an argument that `Q37`'s per-sensor gate policy
is not bookkeeping.

**The defect.** `WindowManager._add_flow` accumulates two node counters on the **verbatim sensor
string**:

```
if ev.state == "S0":   s.s0  += 1
elif ev.state == "REJ": s.rej += 1
```

`FlowEvent.state` is by design each sensor's **own** vocabulary, carried through unmodified;
`FlowEvent.state_class` is the normalized 8-value one (`FLOW_STATE_CLASSES`) that exists precisely
so downstream code never has to know which sensor produced a row. These two lines read the wrong
one. A Suricata-sourced run classifies flows into `attempted` and `refused` correctly — the gate
asserts it against the seeded plan — and then publishes `s0_count: 0` and `rej_count: 0` for every
node, because Suricata never emits the strings `"S0"` or `"REJ"`. The values are not missing; they
are confidently zero, which under `LES-006` is a positive claim of "no such flows".

**Why it is not obviously a two-line fix.** Keying on `state_class` (`attempted` / `refused`) is
almost certainly right and changes **what the two columns mean on the Zeek path too**: Zeek's `S0`
maps to `attempted` but so do other conn states, and `REJ` maps to `refused` but so does at least
one other, so the counts would **move** on existing data. `node_features` is in the belief store
(incubating, so the change is free — `LES-001`), but the feature archive's bytes are pinned by the
tier-2 transport-equivalence gate, and any comparison against a previously-written store would
shift. The rename question rides along: `s0_count`/`rej_count` are Zeek spellings in a
sensor-agnostic artifact, and if the semantics change the names should stop naming one sensor's
vocabulary.

**What would settle it:** which consumer actually reads these two columns, and whether any of them
wants "Zeek said exactly S0" rather than "the flow was attempted and unanswered". If the answer is
the latter everywhere — the likely case — this is a rename plus a keying change plus one CHANGELOG
line about the value shift.

*(That last paragraph is where the question was wrong: it anticipated two options, repair or
repair-plus-rename, and the measurement produced a third. Nothing read the two columns, so there was
nothing to preserve.)*

(`src/aximprint/windowing/windows.py::_add_flow` and `_close` — both counters now gone, with the
reasoning left in `NodeAcc.state_mix`'s docstring so it is not re-added,
`src/aximprint/events/model.py` `FLOW_STATE_CLASSES`, `docs/DESIGN_BRIEF_aximprint.md` §5.2/§5.4 —
whose S0/REJ wording was updated to name `state_mix`, flagged in CHANGELOG 2026-08-26 as a wording
change made under a "subsumed, not withdrawn" reading and reversible in one edit)

## Q35. A Suricata run has no sensor-loss signal at all — recording the one measurement that would reopen it

**Status:** 🟡 OPEN. Opened 2026-08-25, deliberately, as a **reopen condition rather than a
question needing design** — the design is settled, the measurement is not taken, and the whole
point of this entry is that the condition not be lost.

**The state.** `ingest/suricata/plugin.py::_stats` parses `stats` records and mints **nothing**.
The fixture carries `stats.capture.kernel_drops` (the same two readings Zeek's `stats.log` carries)
and the parser reads them and declines. The reason is real and not squeamishness: **Suricata's
counters are cumulative-since-engine-start unless the deployment set `deltas: yes` in its
`eve-log` stats config**, and a single JSON record cannot prove which mode produced it. The
`SensorHealthEvent` kind `pkts_dropped` means "packets the capture layer discarded **in this
interval**"; emitting a cumulative counter under it would be a per-window number that silently
grows monotonically, and `SENSOR_HEALTH_AGGREGATION` reduces the `pkts_dropped` kind with `sum`,
so a cumulative source would inflate it quadratically. Declining reports `None` — "not observed" —
which `LES-006` makes the correct answer, and the equivalence gate asserts the Zeek-set /
Suricata-`None` asymmetry explicitly rather than tolerating a difference.

**What it costs.** A Suricata-sourced run currently has **no capture-loss signal whatsoever**:
`capture_loss_pct_max` is Zeek-only (Zeek computes it; Suricata has no analogue), and now
`sensor_pkts_dropped_max` is too. Window completeness for a Suricata run rests on nothing from the
sensor. On the ten-day Zeek run `capture_loss_pct_max` reached **39.27%** — at least one window
lost two-fifths of its packets — so this is not a theoretical gap: it is exactly the signal that
told us the most important thing about that capture.

**The one measurement that closes it** (either is sufficient):
- `stats.uptime` across **two consecutive** `stats` records from one run, compared against the
  delta in a counter that is known to be monotonic. If the counters reset per interval, they are
  deltas.
- The deployment's own `eve-log` configuration: `stats: { deltas: yes | no }`, read directly.

**On resolution**, `_stats` mints `SensorHealthEvent(kind="pkts_dropped")` from
`capture.kernel_drops` (differencing consecutive records if the mode is cumulative, which is a
per-file stateful read and therefore interacts with the reorder buffer — worth checking, not
assumed), and the gate's asymmetry assertion becomes a plain equality. `stats.decoder.pkts` /
`.bytes` are declined on the same unproven premise and are settled by the same measurement.

(`src/aximprint/ingest/suricata/plugin.py::_stats`, `src/aximprint/windowing/windows.py`
`SENSOR_HEALTH_AGGREGATION`, `scripts/verify_suricata_equivalence.py` exception 1, `LES-006`)

## Q36. Does `FlowEvent.service` want cross-sensor normalization, or is it correctly each sensor's own vocabulary?

**Status:** 🟡 OPEN. Opened 2026-08-25. The current answer is **"no normalization"**, chosen
deliberately and **asserted as intentional** by the equivalence gate rather than left as an
uncompared divergence — but it was chosen by the plugin author, not ruled, and it is the one place
the event model's "sensor-agnostic contract" claim is knowingly not honored.

**The measurement.** For the identical Kerberos conversation, Zeek's `conn.log` `service` says
`"krb"` and Suricata's `app_proto` says `"krb5"`. **And there is a third vocabulary** (noted
2026-08-26): `axquery`'s `protocol` field (`ingest/axquery/plugin.py::_flow`) is its own L7 label
space — `dns`/`tls`/`ssh`/`other` — which is both coarser than the other two and carries an explicit
`"other"` catch-all. That matters to option (2) below: `other` is a *label*, not an absent label, so
a `service_class` table would have to decide whether an incoming `other` maps to unknown or is
counted as unmapped, and under `LES-006` those are different claims. No cross-sensor gate compares
axquery's labels to anything today (`Q37`). `scripts/verify_suricata_equivalence.py`
therefore compares edges **not keyed on the proto label** — it compares the label sets as a
*vocabulary* (expected to differ, with the `krb`/`krb5` pair named explicitly) rather than as a
*key* (expected to match). Every other axis of the gate is an exact comparison; this one is the
sole declared vocabulary exception.

**The case for leaving it.** `FlowEvent.service` is documented as the sensor's own open L7 label,
the same footing as `FlowEvent.state` — and the project already built the right machinery for
exactly this shape one field over: `state` stays verbatim while `state_class` carries the
normalized 8-value vocabulary, with unmapped values **counted, never bucketed**. By that precedent
`service` is correct as-is and what is missing is its `state_class` sibling, not a rewrite.

**The case for normalizing.** Unlike `state`, `service` has **no** normalized sibling, so every
consumer downstream of it is reading raw sensor vocabulary whether it knows that or not. Concretely:
`vocabulary.py`'s categorical index map takes `service` as a key, so a `--vocabulary`-pinned
second capture from a *different sensor* would score `krb5` as **OOV** against a `krb`-trained map
— the pinning mechanism silently degrades across sensors in a way it does not across transports.
Whether anything else keys on it (edge labels reaching the IR, role inference) needs checking, not
assuming.

**The three candidate answers:**
1. **Leave it verbatim, add nothing** — and then say so in `events/model.py`, because "sensor's own
   vocabulary" is currently a docstring line and not a decision anyone made about *consumers*.
2. **Add `service_class`**, a declared, by-name mapping table with unmapped values counted — the
   `state`/`state_class` pattern, applied identically. Most consistent with everything else here,
   and the only one that makes the pinned-vocabulary case work across sensors.
3. **Normalize in each plugin** at the boundary. Cheapest, and wrong for the reason (1) is right:
   it destroys what the sensor actually said, which is a forensic-completeness loss (CONVENTIONS
   §3) and unrecoverable downstream.

**What would settle it:** enumerate the consumers of `FlowEvent.service` and check whether any of
them compares or joins across sensors. If none do, (1) with a doc line. If the pinned-vocabulary
path does — and it appears to — (2).

(`src/aximprint/events/model.py` `FlowEvent.service`, `src/aximprint/ingest/suricata/plugin.py`'s
`app_proto` handling, `src/aximprint/features/vocabulary.py`,
`scripts/verify_suricata_equivalence.py::aggregate_edges`, `Q23`)

## Q37. Does every new sensor owe a ground-truth-anchored equivalence gate, or only sensors that read the same plane?

**Status:** 🟡 OPEN. Opened 2026-08-26, reconciling `wip/axrange-integration` onto `master` after
the axquery arc merged. **A policy question this project's own evidence implies but has not
settled** — nobody has ruled it, and the current state is an accident of what was cheap to build.

**The asymmetry.** There are **three** ingest plugins and **one** equivalence gate. Tier 2's gate 4
(`scripts/verify_suricata_equivalence.py`) renders one seeded traffic plan as Zeek logs *and* as
Suricata `eve.json`, runs both through the real plugins and the real `WindowManager`, and compares
structurally — with every axis also anchored to the plan, because `LES-123`. `axquery` has nothing
of the kind. What it got instead, on 2026-08-20, was a **one-off manual twin comparison** (CHANGELOG
2026-08-20, *"two sensors, one network"*): three twins cast, tabulated by hand, on **captures 49.6
days apart** and therefore not commensurable as an equality check. It was genuinely valuable — it is
how we know the evidence classes invert completely (axquery 0:181 `observed`:`imputed` against Zeek
195:0) while the topologies agree — but it is a measurement, not a gate. It does not run again, it
cannot fail, and nothing re-runs it when a projection table changes.

**Why the gap is not obviously a defect.** The two cases are not alike:
- Zeek and Suricata read **the same plane** — a sensor's own records off disk, derived from the same
  wire. One seeded plan can be rendered into both, so "identical input" is constructible and
  structural equality is the right expectation.
- axquery reads a **different plane**: a query layer over an appliance's already-inferred entity
  database, reached over HTTP. There is no file to render. Constructing identical input means
  standing up a fake API server whose responses are derived from the same plan — buildable, but a
  substantially larger fixture, and much of what it would compare is *known* to differ by ruling
  (no locality, no MAC identity, conclusions `imputed` by the D1 carve-out).

**What the evidence says anyway.** `Q34`'s 2026-08-26 amendment is the case for the gate: `axquery`
published a confidently-wrong `s0_count: 0` / `rej_count: 0` on every node for **five days** and it
took a *different* sensor's gate to notice. A second implementation creates the exposure; only a
comparison converts it into a finding. On that evidence the marginal value of a gate is highest
exactly for the sensor that is structurally *least* like the others.

**The three candidate answers:**
1. **Every registered plugin owes a gate leg** — a sensor without one is an untested claim that the
   event model is sensor-agnostic. Most consistent with "fail loud, fail closed"; most expensive,
   and the expense is per-sensor fixture work, not per-sensor logic.
2. **A gate is owed per *plane*, not per sensor** — one gate for file-reading sensors (Zeek vs
   Suricata, which exists), one for query-layer sensors (nothing to compare against until a second
   one exists), and a *declared, named* exemption for a sensor that is alone on its plane. Cheapest
   answer that is still a decision rather than an omission.
3. **A weaker universal obligation:** no cross-sensor comparison, but every plugin must have a
   **plan-anchored self-check** — one seeded input, one set of positive assertions about what the
   emitted events must claim. That catches `Q34`'s class (a confidently-zero counter) without
   needing a second sensor at all, and it is the half of `LES-123` that does not depend on there
   being an A and a B.

**What would settle it:** cost the axquery fixture. If a plan-driven fake `/api/connections` is a
day's work, (1) is affordable and the question is closed by building it. If it is not, the choice is
between (2) and (3), and (3) is probably the better rule for a project that expects more sensors
(`packetXpress` is named in DESIGN_BRIEF §0.1 and does not exist yet) — a per-plugin self-check
scales linearly, a pairwise gate matrix does not.

(`scripts/verify_pipeline_gates.sh` gate 4, `scripts/verify_suricata_equivalence.py`,
`src/aximprint/ingest/axquery/`, CHANGELOG 2026-08-20 *"two sensors, one network"*, `Q34`,
`LES-123`, DESIGN_BRIEF §0.1)

## Q38. The belief store's fail-loud schema guard compares COLUMNS, so four JSON columns are unpoliced — does it get a declared key manifest, a narrower refusal, or nothing?

**Status:** 🟡 OPEN. Opened 2026-08-26 by `Q34`'s close (`LES-131`). **Needs a user ruling**, not a
build: every candidate that actually closes the gap is a step toward declaring a schema for an
incubating surface, which DESIGN_BRIEF §0.2 and `LES-001` put with the user.

**The state.** `BeliefStore._check_schema_matches_this_build` is this project's fail-loud answer to
"an old store disagrees with this build" — it refuses to touch a mismatched store before any write,
naming the table, and it deliberately does **not** migrate or shim (`LES-001`). It compares
`_live_columns()` (this connection's own `information_schema.columns`) against `_canonical_columns()`
(derived by running `_SCHEMA` against a throwaway in-memory connection), for **every** table
`_SCHEMA` declares — generic over tables and columns by construction, which is exactly what its
docstring claims.

**What it cannot see.** It has **no path into a JSON value**. Four columns are therefore entirely
unpoliced, and they are the **richest and fastest-moving surfaces in the store**:

| Column | What lives inside it |
|---|---|
| `node_features.features` | the whole per-entity window bundle — volume, cardinalities, quantiles, `state_mix`, `services`, `tainted`, sub-buckets |
| `window_health.health` | the whole health record — completeness, disorder, `unparsed_log_types`/`unrecognized_log_types`, the projection-table decline counters |
| `entity_roster.identifiers` / `.addresses` / `.evidence` / `.merged_from` | the resolver's entire output shape |

Add, remove or **redefine** a key in any of them and the guard is silent — there is no column to
disagree about. "The store fails loud on a schema change" is true of columns and false of blob keys,
and blob keys are where most change in this project happens.

**This is a realized failure, not a hypothesis.** `Q34` is the worked example: `s0_count`, a key
inside `node_features.features`, gated on the sensor-native `FlowEvent.state` against Zeek's verbatim
`"S0"`, so its meaning silently changed with the sensor that produced the row — a confident `0` on
`axquery` beside 2,037 `attempted` flows, published on every node for **five days**, with nothing in
the store layer flagging it. It was found by a *different* sensor's equivalence gate, not by the
guard whose whole job is disagreement detection. The same blindness is what settled `Q34` as a
**deletion** rather than a rename: a rename would have left one store holding `s0_count` rows under
Zeek semantics *and* `attempted_count` rows under normalized semantics, indistinguishable, with the
guard unable to see either key.

**The tension, stated honestly on both sides — this is the part that makes it a ruling.**

- **Against acting:** a per-column *declared key manifest*, checked the way `_SCHEMA` is, is
  **precisely the versioning ceremony `LES-001` forbids on an incubating schema**. The node bundle
  changes shape most weeks; a manifest turns every one of those into a two-file edit plus a
  guard-refusal event for anyone holding a prior store. That is the axRange habit this project
  deliberately inverted, and the cost lands on exactly the surfaces that are supposed to be free.
- **For acting:** the guard **already exists and already makes a completeness claim it cannot
  honor**. Its docstring says "generic over all seven, so a future column addition to ANY of them is
  covered by construction" — true, and read by every subsequent author as *the store fails loud on
  schema drift*, full stop. So the status quo is not a neutral default; it is a **half-truth that
  reads as coverage**, and `LES-131` exists only because the gap had to be discovered by hand. A
  guard that is silent about the surfaces most likely to drift is arguably worse than no guard, for
  the same reason `LES-128` gives about dead telemetry.

**Three candidate answers, costed:**

1. **Leave it, and say so.** Rely on the row-level **cross-field consistency assertion** `LES-133`
   recommends — no published field may contradict the census in the same row — enforced in the test
   suite rather than at the store boundary. **Cost:** near zero, and `LES-131` + this entry already
   document the limit so it is a *declared* gap rather than a silent one. **Buys:** catches `Q34`'s
   class at build time on every sensor. **Does not buy:** anything at all for a **pre-existing store
   on disk**, which is the case the guard is for. Under this option `_check_schema_matches_this_build`'s
   docstring must be narrowed to say "columns", so the claim stops over-reaching.
2. **A per-column declared key manifest**, validated on read the way `_SCHEMA` is. **Cost:** the full
   `LES-001` objection above, on the fastest-moving surfaces. **Buys:** genuine parity — a key added,
   removed or reshaped fails loud before any write. **Note:** this is the option that requires
   ratification; the other two do not.
3. **Something narrower: declared-and-REMOVED keys only.** The guard refuses when an old store
   carries a key this build has *deliberately retired* — a short, append-only list of retired blob
   keys (`s0_count`, `rej_count`), not an enumeration of live ones. **Cost:** one small list, touched
   only on a removal, which is rare; no ceremony on additions, which are common. **Buys:** exactly
   `Q34`'s shape, including the rename hazard that made deletion the right call. **Does not buy:** a
   key whose *meaning* changed while its name stayed — the harder half, and the one `LES-131` says to
   avoid creating rather than to detect.

**What would settle it:** whether the guard's promise is meant to be **"this store was written by
this build's schema"** (then (2), and it needs ratifying) or **"this store will not crash this
build's writes"** (then (1) or (3), and the docstring is what needs fixing). Note that (3) is
buildable today without ratification, since an append-only list of *retired* names declares nothing
about the live shape — it is closer to a deprecation record than to a schema.

(`src/aximprint/store/belief_store.py::_check_schema_matches_this_build`, `::_canonical_columns`,
`::_SCHEMA`; `LES-131`, `LES-001`, `LES-006`, `LES-128`, `LES-133`; `Q34` — closed 2026-08-26, the
worked example; CHANGELOG 2026-08-26; DESIGN_BRIEF §0.2)
