# WORK ORDER — Entity resolution v1: addresses → time-bounded host entities

**STATUS:** **LANDED — COMPLETE 2026-08-14.** All eight build items and all eight validation cases
landed; `Q1` and `Q9` are CLOSED. See CHANGELOG 2026-08-14 for the measured results, and the
**Findings** section at the bottom of this file, which is now answered rather than asked.
**Depends-on:** `WORK_ORDER_streaming_zeek_ingest.md` (LANDED), `WORK_ORDER_feature_vector_sink.md` (LANDED)
**Closes:** `Q1` (entity-resolution v1). `Q9` (identity delta vs restatement) falls to the same file and is folded in.
**Authority:** drafted by Claude Code at user request (2026-08-04) — **not ratified to build.** The
choices in "Decisions this draft makes" are proposals; overrule any of them before the build starts.
**Built 2026-08-14 (Claude Code, at user request "build `Q1`") with D1–D4 ratified as drafted** —
none was overruled.

> ## ⚠ READ THIS BEFORE THE MEASUREMENTS BELOW — the draft's premise was stale
>
> **This work order was drafted against a capture with no `dhcp.log`, and shipped against one that
> has 455 rows of it.** Every "the evidence §5.1 names as primary is not present at this vantage"
> claim below — the table, the "null test not a validation" framing, finding #1's *"if none is,
> this vantage cannot anchor a single entity by identity evidence"* — describes the **2026-08-03
> reference capture**, not the **2026-08-11 baseline** (`/tmp/aximprint-zeek-test/`) the build was
> verified on. On that baseline:
>
> | | draft's vantage | the vantage it shipped against |
> |---|---|---|
> | `dhcp.log` | **absent** | **455 rows** (183 with both mac and address) |
> | `ntlm.log` | not yet parsed | **158 rows**, 10 distinct (address, name) pairs |
> | Kerberos machine accounts | unknown | **86 rows** (to 9 user-principal rows) |
> | entities resolved | 0, by construction | **25** |
>
> The prose below is kept **unedited** as the record of what was reasoned at drafting time — the
> same discipline a CHANGELOG entry follows when it quotes a number later superseded. Its
> *arguments* survive intact (classify before weighting; DNS is dangerous; a user principal is a
> person); only its claim about what this network contains does not. **The general lesson is
> `LES-006`'s, applied to a work order rather than a health field: a premise measured once is
> evidence about the capture it was measured on, and re-measuring before building is what tells
> "still true" from "was true".**

## Goal

Replace the v0 placeholder (`entity == ip`, `src/aximprint/windowing/entities.py`) with a resolver
that folds churning addresses into stable host entities, so a node feature row describes *a host*
over 71 hours rather than *an address* that may have been two hosts. §5.1 calls this "the core
algorithmic problem"; it is the hinge of the pipeline — everything above the resolver speaks
addresses, everything below speaks entities, and the synthesizer casts one twin host per entity.

**The evidence §5.1 names as primary is not present at this vantage.** Measured on the reference
capture (`docs/WORK_ORDER_streaming_zeek_ingest.md`, CHANGELOG 2026-08-03):

| Zeek source | rows | binds | can it anchor a *host* identity? |
|---|---|---|---|
| `dhcp.log` | **absent** | mac ↔ ip, with a lease interval | yes — and it is not here |
| `kerberos.log` | 459 | `client` principal ↔ `id.orig_h` | **only for machine accounts** — a user principal is a person, not a host |
| `dns.log` | ~175 k | query name ↔ answer address | **no** — a third party's address, and many-to-many |
| `known_hosts.log` | — | an address, with nothing to bind it to | no — `observe()` records nothing from it today |

So the work is not "weight the evidence", as `Q1` frames it. It is **classify the evidence first**:
most identity events at this vantage cannot anchor a host entity at all, and one of them — DNS — is
actively dangerous, because a name with several A records would merge unrelated servers into one
entity the moment a resolver unions on shared identifiers.

Three further facts set the shape of the build:

1. **The reference capture is a null test, not a validation.** 44 distinct addresses (34 originators
   / 12 responders) over 71 h with no `dhcp.log`: there is probably nothing here to merge. It can
   prove a resolver does no harm. Only synthetic churn fixtures can prove it works.
2. **Late evidence versus idempotent close.** §5.1 wants late evidence to retroactively strengthen
   bindings; §5.4 requires window close to be a pure function of (events, resolver state, code
   build); and `Q2` bought a streaming ingest that a second pass would spend. These conflict, and the
   conflict must be resolved deliberately rather than discovered.
3. **`resolve()` is the hottest path in the system** — twice per flow, **3,070,658 calls** on the
   reference capture's 1,535,329 `conn` rows. Whatever interval lookup v1 introduces is paid three
   million times, inside the 39.7 MB / ~32 s ingest+windowing budget already recorded.

Three live defects sit in the code this work order rewrites:

- **`ambiguity_rate()` returns a hardcoded `0.0`**, and that value lands in `resolver_ambiguity` in
  all 72 window health records. "No resolution was attempted" reading as "resolution found no
  ambiguity" is a false clean bill of health in exactly the sense `LES-006` names.
- **`self._bindings` is a dict keyed on data** — `(kind, ip, mac|hostname|principal)` — with no cap.
  68 keys here; bounded only by distinct (name, answer-address) pairs at a wide vantage. `LES-014`
  applies verbatim, and 44 addresses is evidence the vantage is narrow, not evidence the hazard is
  absent (`LES-017`).
- **`evidence_class="referenced"` exists in the vocabulary and in no code path.** Every assertion the
  project emits is `observed` — including `bound_dns` at confidence 0.7, a claim about a host the
  sensor never saw at that address. "Imputed never masquerades as observed" is the project's central
  honesty claim, and it is currently two-thirds implemented.

## Decisions this draft makes

Flag any of these before the build starts; each is a fork the implementation cannot avoid.

| # | Decision | Choice | Rejected, and why |
|---|---|---|---|
| D1 | What may merge two addresses | **Only host-scoped evidence** (mac, DHCP client-id, machine principal). User principals and DNS answers never merge. | Union on any shared identifier — collapses a CDN's A records into one entity and two workstations sharing a user into one host. |
| D2 | Late evidence | **Causal, forward-only.** Resolution uses evidence seen so far; a later merge is emitted as an assertion and applied at snapshot read, never by rewriting a closed window. | A retroactive pass — it re-spends `Q2`'s streaming win, breaks append-only, and makes window close a function of the future. |
| D3 | Entity ids | **Minted sequentially in first-observation order**, stable within a run, published in a roster with each entity's evidence basis. Explicitly **not** stable across captures. | Content-derived ids (an entity's id would change the moment it acquired stronger evidence, breaking joins across windows *within* one run). |
| D4 | An address with no host-scoped evidence | **Resolves to itself**, exactly as today. | Minting an entity per bare address — it would claim identity knowledge the vantage does not have, and it would make the reference capture's output incomparable to the recorded baseline. |

D4 is the safety property that makes this reviewable: on a capture with no host-scoped evidence, v1
must reproduce v0's output byte for byte. Every difference is then a merge with a name attached.

## Build

1. **Classify evidence by what it can anchor, in one table with no fall-through.** Every binding
   carries a `scope`: `host` (mac / DHCP client-id / machine principal), `user` (user principal), or
   `referenced` (DNS answer). The classification is a *resolution policy*, not a sensor fact, so it
   is derived in the resolver, not added to the plugin — but the machine-vs-user principal split
   requires parsing the principal, not pattern-matching it (`LES-008`). An evidence kind in no
   category is `unknown`, counted, and never anchors anything. Model the table on
   `_CONN_STATE_CLASS`: closed vocabulary, unmapped values counted rather than bucketed.
2. **Bindings are intervals, and an address has generations.** `(identifier, addr, valid_from,
   valid_to, scope, evidence, confidence)`. When an address's host-scoped identifier changes, that
   closes the current generation and opens a new one — an address reused by a second host must yield
   two entities, not one entity with a confused history. `resolve(addr, ts)` returns the generation
   open at `ts`. Intervals close on *evidence*, and a DHCP lease bounds validity where present.
   **A DNS TTL does not**: `IdentityEvent.valid_for` currently carries both, and one field answering
   two different questions is the `LES-015` failure — split it or rename it, and say which you did.
3. **Merging is causal and forward-only (D2).** A merge learned in window 40 is emitted as a
   `same_entity_as` assertion, resolved by the snapshot reader; windows 1–39 keep the entity ids they
   were closed with. Window close stays a pure function of (events, resolver state, code build), and
   the resolver's state stays a deterministic function of the event prefix.
4. **Enumerate every path that can mint an entity id, and publish the roster.** The namespace already
   has four mint paths and no declared table: a bound entity, the address fallback (D4), the `"?"`
   sentinel `resolve()` returns for an empty address, and `__longtail__`, which `windows.py::_close`
   mints as an edge `dst` with no node row at all. `LES-017` is the rule — a published namespace is
   the union of every path that can mint a member, including sentinels nobody declared. Emit a
   run-level `entities` block in `manifest.json` (mirroring `categorical`, the precedent for run-level
   state a sink may legitimately hold), giving each entity its mint path, evidence scope, and
   generation count. This does **not** fix `Q11`; it makes `Q11`'s dangling adjacency detectable from
   the manifest instead of by inference.
5. **Make `resolver_ambiguity` mean something.** Define it, implement it, and let it be `None` when
   the window carried no identity evidence at all — "no evidence" and "evidence of none" are
   different findings (`LES-006`). Proposed definition, to be confirmed against what a consumer would
   ask it: *the fraction of resolved flow endpoints whose address had more than one candidate
   host-scoped binding open at that ts.* Whatever you choose, write down the question it answers and
   check it can actually answer it (`LES-015`).
6. **Emit on change, not on observation (`Q9`).** `observe()` returns a record per observation today:
   4,386 rows for 68 distinct bindings — 98.4 % restatement, 17 % of the archive. Change-detection
   belongs here, in `entities.py`, so every sink benefits. Define "changed" explicitly: a new binding,
   a changed identifier, a generation boundary, an entity merge, or an interval that *closed*.
   `last_seen` moving forward is **not** a change. Check the emit condition against the noun in the
   artifact's name (`LES-010`).
7. **Bound the resolver's state at insert, and disclose the cap.** The binding map is keyed on data.
   Cap at insert, not at eviction; put the cap and its spill counters in the artifact; and make sure
   no completeness statistic counts truncated mass (`LES-014`). State the number you chose and what
   you defended it against.
8. **Fix the provenance that follows.** `learners/extractors.py` hardcodes `0.9 if kind in ("dhcp",
   "kerberos") else 0.7` and always `evidence_class="observed"`. Confidence and evidence class come
   from the resolver's scope instead: a DNS-derived binding is `referenced`, and `referenced` finally
   has a code path. The belief store's `identity_bindings` and the sink's `identity.jsonl` gain
   `entity` and `scope` (both schemas are incubating and change freely — `LES-001`).

## Boundaries — do NOT do these

- **No behavioral or embedding similarity.** §5.1 marks it "(later)" and it needs a learner layer
  that does not exist. If you conclude deterministic evidence cannot resolve this vantage, that is a
  **finding**, not a prompt to build a model.
- **No second pass, no retroactive rewriting.** Streaming was bought by `Q2`; do not spend it. If you
  believe D2 is wrong, return `DECISION NEEDED:` rather than building a batch resolver.
- **No cross-capture entity pinning.** The `--vocabulary` analogue for entity ids is a real follow-up
  and is not this work order. Say in the artifact that ids are run-scoped, so nobody assumes otherwise.
- **Do not touch `synth/`.** Entity semantics reaching the twin — one twin host per entity, external
  peers included — is its own round.
- **Do not repair `Q7`, `Q8`, `Q11`, `Q12`, `Q13`, or `Q14` in passing.** Record what you see; the
  previous round's boundary held this way and it is why those questions exist as questions.
- **No new runtime dependency** (`duckdb`, `pyyaml` only; stdlib is fine).
- **No schema versioning** on the event model, the binding shape, or the store (`LES-001`).
- **No capture contents anywhere** (`LES-003`). This work order's artifacts are the most PII-dense in
  the project — the roster is literally an identity map of macs, hostnames, and user principals.
  Report shapes, counts, and distributions only; never a value. Tests use synthetic fixtures.

## Validation

- `./scripts/verify_tier.sh` passes.
- **Determinism holds:** `verify_determinism.py --source metadata/zeek`, both passes byte-identical.
  **Expect the feature-archive hash to move off `cb885fe14673579b` only if something actually
  merged** — the invariant is that two passes agree, not that the hash matches history. Under D4 a
  vantage with no host-scoped evidence should reproduce the recorded hash exactly; if it does, say
  so, because that is the strongest available null-test result. Do not edit `src/` mid-run
  (`LES-018`).
- **Synthetic churn fixtures — the actual validation.** Seeded, deterministic, one case each:
  (a) one mac, two addresses in sequence → one entity, two generations;
  (b) one address, two macs in sequence → **two** entities;
  (c) a user principal at two addresses → two entities, never merged;
  (d) a machine principal at two addresses → one entity;
  (e) a name answering with several addresses → no merge, `referenced`;
  (f) a binding arriving out of order within the reorder horizon → the interval never inverts
  (`LES-005` — this exact inversion has been fixed here once already);
  (g) evidence arriving after its window closed → merge asserted forward, the closed window untouched;
  (h) an address with no identity evidence → resolves to itself (D4).
- **The null test on the real capture:** 44 addresses in. Report the entity count, the merge count,
  and the evidence scope behind every merge. A merge you cannot name the evidence for is a bug.
- **Ambiguity rate:** non-`None` exactly where identity evidence exists; hand-checkable on a fixture.
- **Cost, on both axes** (`LES-010`): peak RSS and wall-clock against the recorded 39.7 MB / ~32 s
  baseline, plus the resolver's state size against its cap — 3.07 M `resolve()` calls make a
  per-call interval scan a real risk, not a theoretical one.
- Existing tests pass unchanged, except where an entity key legitimately changed — list those and why.

## Findings to report (record, do not fix) — **ANSWERED 2026-08-14**

Measured on the 2026-08-11 baseline (`/tmp/aximprint-zeek-test/`, 3 hourly windows, 209,381 `conn`
rows), not on the vantage this document was drafted against — read the amendment box at the top
before comparing any number here with one in the body above.

1. **Evidence composition, and is any Kerberos principal a machine account?** **Yes — 86 machine
   accounts to 9 user principals.** Host-scoped evidence arrives on three independent sources
   (DHCP 183 usable rows, NTLM 155, Kerberos-machine 86) and resolves **25 entities from 41
   bindings**. The draft's alternative ending — *"this vantage cannot anchor a single entity by
   identity evidence, and v1 is provably an identity function here"* — does **not** apply, and
   saying so plainly cuts the other way from what the draft expected.
   **The finding worth keeping is the one nobody asked for:** the three sources name the same host
   in three formats (`WORKSTN1$`, `workstn1.corp.example.test`, whatever option 12 said), and
   **7 of the 25 entities carry both an NTLM name and a Kerberos machine principal**. Left as raw
   strings that is 7 hosts split in two. Normalization is not a tidiness step here; it is the
   difference between resolving and appearing to.
2. **Any address reassignment across the capture?** **No REUSE at all** — 14 addresses, 14 macs,
   1:1, no address ever held by two hosts. **One entity does span two addresses**, so roaming is
   present and reassignment is not. The draft's clean "no" is therefore *nearly* right, and the
   consequence it drew stands: the churn the interval-and-generation machinery exists for is still
   essentially unrepresented in the only real data we have, which is why the eight synthetic cases
   in Validation are the acceptance criteria and the real capture is a null test.
3. **What a wide vantage does to the binding map.** 6,414 distinct (answer address, queried name)
   pairs from 144,886 DNS-derived identity events, in ~3 h at a *narrow* vantage — 156× the
   host-scoped count. That extrapolation is why `MAX_NONHOST_BINDINGS` exists, why it is a
   **separate** cap from `MAX_HOST_BINDINGS` (sharing one would let DNS starve the resolver's
   working state), and why its headroom is only 2.6×: it will truncate on a longer capture, which
   is the design working. Refusals are counted and `roster()["state"]["complete"]` goes false.
4. **Is `valid_for` salvageable as one field?** **No — it is two fields**, `lease_seconds` and
   `record_ttl`. A DHCP lease bounds how long the BINDING is true; a DNS TTL bounds how long the
   ANSWER may be cached and says nothing about the mapping's lifetime. The resolver bounds validity
   with the first and never the second, so one field could not be consumed correctly without the
   resolver re-deriving which parser filled it.
5. **Should entity ids become cross-capture pinnable?** **Still open**, deliberately — it was a
   Boundaries item and stayed one. Mac is still the only candidate that survives a reboot, and
   unlike at drafting time **14 of them are present**, so the follow-up is now buildable rather
   than hypothetical. `entity_roster` keys on the run's SOURCE precisely because ids are run-scoped
   (D3) and this store accumulates; note that `node_features`/`edge_features`/`assertions` still
   carry no attribution of their own — a pre-existing, store-wide gap, not one this round made.
6. **Most addresses are external peers.** **445,321 of 530,549 resolutions (84 %)** fell through
   D4's bare-address path. So yes, the vantage is dominated by peers rather than by hosts on the
   monitored network. Whether they should be *marked* rather than resolved, and what the
   synthesizer does with an entity that is a bare address seen only as a peer, is untouched here —
   it needs `synth/`, which this work order's own Boundaries forbid.

### Two more, found while building rather than asked for

7. **A dedup key chosen for "the same fact" erased which sources attested it.** Keying open
   bindings on `(namespace, value)` alone made a Kerberos row confirming an NTLM-derived name look
   like a restatement, so the second source vanished from the record. Corroboration by an
   independent log is a materially different fact from the same log repeating, and the key now
   carries `kind`. Found by a test written for the 7-address case, not by review.
8. **The determinism gate would have narrowed for the second round running.** `entity_merges` is a
   fifth `WindowResult` channel and `canonical_windows()`'s hand-written key list would silently
   not have compared it — the exact failure `LES-098` was recorded for one round earlier, whose own
   prescribed fix (derive from `dataclasses.fields`) was available and had not been taken. It is
   taken now; see that lesson's amendment.
