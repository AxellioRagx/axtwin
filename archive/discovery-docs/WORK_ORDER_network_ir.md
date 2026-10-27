# WORK ORDER — the network IR: a point-in-time structural representation, persisted and comparable

**STATUS:** **COMPLETE 2026-08-14** — every build step landed the day it was ratified. D1–D9 and D4a/D4b were **ratified as drafted** by
the user ("ratify D1-D9 and D4a/D4b as drafted, start build step 1"); none was overruled.
**Build steps 1 and 2 are LANDED** — step 1: `docs/SPEC_network_ir.md` (schema of record, version
`1.0`), `src/aximprint/ir/schema.py`, `tests/test_ir_schema.py` (23 tests incl. spec↔code
agreement). Step 2: `src/aximprint/ir/keys.py`, `tests/test_ir_keys.py` (24 tests) — cross-PIT
natural keys, **3,302/3,302 keyed with zero collisions, but only 12% of placed hosts on strong
evidence** (see the step 2 RESULT). **Step 3 + 1b** (materialize; schema → `1.1`) and **step 5**
(the comparator, `aximprint drift`) landed the same day — **D4's absence classification works on
real data**, and step 5 exposed and fixed the cumulative-PIT gap that would have made drift
unusable. **Step 4 landed too — the work order is COMPLETE end to end** (steps 1, 1b, 2, 3, 4, 5), and
axRange's own expander accepted the compiled config: 38 networks, 197 nodes, zero warnings. Originally REDRAFTED 2026-08-14 from the 2026-08-11 draft of this file, which was named `WORK_ORDER_axrange_handoff_ir.md` — the rename is the point: an axRange handoff is
**one of four** uses, and framing the artifact by its consumer is what let it be traded away for a
build-sequencing convenience. D1–D9 below are **drafted by Claude Code at user delegation** ("go
with your recommendations") and are **proposals**: overrule any of them before the build starts, in
the `WORK_ORDER_entity_resolution.md` idiom. **D4a is the exception — its premise is the user's**
(absence weighted over consecutive windows rather than treated as a per-PIT boolean); only its four
consequences are analysis.
**Depends-on:** `WORK_ORDER_synth_v1.md` phases 1 and 2 (LANDED 2026-08-14 — segments and roles are
what give the IR anything structural to say).
**Blocks:** ~~`WORK_ORDER_synth_v1.md` phase 3~~ — landed here as steps 3 and 4.
**Followed by:** `docs/WORK_ORDER_visibility_and_vantage.md` (DRAFTED 2026-08-14, not started) —
a PIT is one **tap point's** view and nothing in this schema records which tap, so two captures
from different vantages compare as though they were the same view. That work order adds vantage
and network identity, multi-vantage merge, and a census of what the tap could NOT see.
**Authority:** **user**, this session — both the direction (a point-in-time network representation
persisted with a timestamp, for drift analysis, GNN comparison, diagram generation and twin
compilation) and the **§0.2 ratification** that makes it a versioned schema. The five requirements
in "What the user specified" are the user's, restated from 2026-08-11 and extended this session.
Everything below "What this actually is" is Claude Code analysis.
**Supersedes:** `WORK_ORDER_synth_v1.md` **D1** ("direct compiler", ruled 2026-08-14 earlier the
same day). See "The reversal, and what was wrong with the original argument".

---

## The reversal, and what was wrong with the original argument

`WORK_ORDER_synth_v1.md` D1 ruled that `aximprint synth` would compile axRange YAML **directly**,
with the IR route not taken. The user chose that from a recommendation Claude Code made, and the
recommendation was wrong. Recorded plainly because the failure mode is reusable:

**The drift purpose was presented as a tiebreaker in a build-sequencing decision, when it is a
separate product requirement that the direct route structurally cannot satisfy.** The 2026-08-11
ruling named two purposes — build a point-in-time twin, and track drift across runs — and the
recommendation optimized around the first while listing the second as "the argument on the other
side". A capability that only one option can provide is not a tiebreaker; it decides the question.
The asymmetry that should have settled it: the ratification cost the recommendation was avoiding
(§0.2) is **paid once**, while the capability it traded away is **foregone permanently**.

**The strongest argument was already in the code and went unnoticed.** `BeliefStore.snapshot()`
returns `{as_of, entities, listens, segments, roles, edges, bindings}` — `segments` added by phase
1 and `roles` by phase 2, one key per phase. **That is already an IR**: a point-in-time structural
representation of the network, undesigned, unnamed, unversioned, in-memory only, growing by
accretion. The choice was never "IR or no IR". It was "designed IR or accidental one", and an
accidental one has all the coupling of a schema with none of the guarantees.

---

## What the user specified

From 2026-08-11, restated and extended this session:

1. An **intermediate representation** carrying the knowledge aximprint gained from traffic
   analysis — not an axRange config, and not aximprint's internal belief store either.
2. **Persisted to a database, with a timestamp identifying the point in time.** Point-in-time is a
   first-class property, not metadata bolted on.
3. **aximprint owns the schema definition.**
4. **A compiler transforms that schema into `twin_config`**, so the IR and `twin_config` can
   **develop independently with new enhancements**.
5. Uses, deliberately several — the artifact is not defined by any one of them:
   - **build a point-in-time twin** from a chosen PIT;
   - **track drift across PITs** — structure and characteristics — to flag anomalies;
   - **a target schema for GNN-derived output**, so model output and evidence-derived output are
     in one vocabulary and the diff between them can be minimized;
   - **topology diagram generation**;
   - others, unenumerated — which is itself a requirement: the IR must not be shaped to one
     consumer.

## What this actually is

**The structural half of DESIGN_BRIEF §10's own decomposition.** §10 splits drift into *structural*
("a one-pass discovery problem") and *behavioral* ("what the loop minimizes"), and specifies that
behavioral drift is compared **in feature space, never packet space**. It never named the artifact
for the structural half. This is it. The IR does not compete with §10's feature-space comparison; it
completes it.

**And it is where the project's honesty claim stops being discarded at the exit.** `twin_config` has
no confidence, no `evidence_class`, no observation window and no coverage caveats; it collapses N
hosts into `count: N` and defaults `image`/`resources`. So today every piece of provenance machinery
— what DESIGN_BRIEF calls the central honesty claim — is thrown away at precisely the boundary where
the artifact leaves the project. The IR is the last shape that still carries it, which is what makes
it the right altitude for GNN comparison too: the assertion table is too low (per-window,
per-predicate) and `twin_config` is too high (already collapsed and defaulted).

## What phases 1 and 2 changed since the 2026-08-11 draft

That draft's gap table is stale in three rows and its premise in one. Re-measured 2026-08-14:

| axRange needs | 2026-08-11 draft said | **actually, now** |
|---|---|---|
| `nodes[].name` | "`entity == ip` (`Q1` placeholder); 5,624 entities from 2 h" | **`Q1` closed.** Real entities; 24 resolved ids + 283 unanchored = **197 placed** |
| `segments[].{name,cidr}` | "nothing — synth's own caveat says not extracted" | **38 segments**, `observed`, `windowing/segments.py` |
| `nodes[].role` | "`unknown`, `defaulted`, 100% of nodes" | **19 of 197 roled**, `imputed`, from `catalog/roles.yaml` |
| `nodes[].{image,resources}` | "not recoverable — catalog must supply" | unchanged, and the catalog now exists |
| conduits | "4,394 distinct pairs — have it" | **39 segment pairs**, derivable from `in_segment` ⋈ edges |

**The draft's own conclusion — "what aximprint produces today cannot express what axRange requires"
— is now substantially false**, which is why this file needed a redraft rather than a status flip.

---

## Decisions this draft makes (all overrulable)

| # | Decision | Ruling |
|---|---|---|
| **D1** | Ratify the IR as a versioned schema? | **YES — user, this session.** This is the §0.2 gate, and it is the first genuine ratification in the project. **Scope it narrowly: ONLY the IR is versioned.** The event model, feature shapes, plugin contract and belief-store schema stay incubating and free to change (`LES-001`). That asymmetry is not a compromise, it is the design: the belief store can stay free *because* it stays internal, and only the IR crosses a boundary. |
| **D2** | Separate store, or a view over the belief store? | **Separate DuckDB database**, accumulating one row-set per PIT. Not a view: a versioned read surface over an unversioned, freely-changing store would inherit every change the store is allowed to make. Not one file per PIT either — drift comparison wants many PITs queryable together, and N files means N attaches. Note the concrete incompatibility that settles it: `BeliefStore.__init__`'s `_check_schema_matches_this_build` **fails loud on any schema mismatch**, which is correct for an internal projection and exactly wrong for a versioned artifact whose whole purpose is that an older one stays readable. |
| **D3** | What identifies a PIT? | **`as_of`** — the semantic "the network as known at time T", already `snapshot()`'s parameter — **plus a mandatory observation extent** (`observed_from`, `observed_to`, window count) and the coverage block of D4. Never wall-clock invocation time: it is non-deterministic (`LES-002`) and says nothing about the network. A PIT with no extent is uninterpretable — a 3-hour capture and a 71-hour one are not the same claim, and comparing them yields "drift" that is only coverage. |
| **D4** | How is absence represented? | **The IR records what it saw plus its own coverage; the COMPARATOR classifies absence.** An IR row cannot know about a future PIT, so it must not try to encode "gone". The three states the project already distinguishes are resolved at comparison time: *observed present*; *observed absent* (in a prior PIT, this PIT's coverage of its segment is good, not here → real change); *not observed* (coverage insufficient → no claim). This makes the coverage block **load-bearing, not decorative** — without it every diff conflates "changed" with "we were blind", and for drift detection absence **is** the signal. See D4a, which is the reason this decision is not merely tidy. |
| **D4a** | Absence is **graded, over two time scales** (user, this session) | The user's framing: a consumer will eventually weight absence *"across multiple consecutive sample windows vs missing once and reappearing"*. Four consequences, and the first is why D4 is shaped as it is. **(1) The IR must never pre-collapse absence into a verdict.** The moment a row says `absent`, the run-length is gone and there is nothing left to weight — so "record presence + coverage" is a requirement of the downstream evaluation, not just a statement about what a row can know. **(2) Two time scales, and one is free.** Within a PIT, `snapshot()["entities"]` already carries `windows_seen` — "present in 38 of 40 windows" is presence shape at window granularity, available today at no cost, and it must survive into the IR. Across PITs is the comparator's scale. A PIT that records only "these entities existed" discards the finer one for nothing. **(3) Coverage must be PER SEGMENT.** A host absent for 12 consecutive windows with good coverage of its segment is a strong decommission signal; the same 12 windows with the tap down on that VLAN is **zero** signal, not weak signal — and a model fed the second case as "absent, 12 consecutive" learns a decommission pattern from a tap outage. Today's health record is global (`capture_loss_pct_max` is per-sensor), so this is a real gap. **Honest caveat:** per-segment coverage is partly *inferred* — traffic-appeared-from-this-segment against a running expectation — since Zeek reports no per-segment loss. Mark it as inferred (`LES-006`), never as measured. **(4) Cadence becomes an emission discipline, not only a comparison check.** Run-length statistics are meaningless across a 3-hour PIT and a 71-hour one, so PITs should be emitted at a regular cadence and the comparator's refusal path (D3) becomes the backstop rather than the plan. |
| **D4b** | Who does the weighting? | **Deterministic first, ML on top — not ML from the start.** §6's own rule applies: *"rule-shaped facts with rule-shaped evidence; ML adds uncertainty where none is needed."* A presence run-length is rule-shaped, and DESIGN_BRIEF §7 already specifies the deterministic version — **principled forgetting**, time-weighted decay against `last_seen` so decommissioned hosts fade. That is `Q5`, still open, and `docs/PIPELINE_MAP.md` already slots decay sweeps as a clock-2 offline job, so the seat exists. What ML genuinely earns is the harder question above it: not *"is this host gone"* but **"is this pattern of appearing and disappearing normal for this host"** — a laptop present 9-to-5 on weekdays is healthy and a domain controller with the identical pattern is an incident. That is anomaly detection over presence shape, inexpressible as a decay constant. **One consequence to decide deliberately rather than inherit:** once a model's judgment about whether a host still exists can reach the twin, that is an `imputed` fact with unusually high leverage — it decides whether a node appears at all, which is a larger claim than `role=web-tier`. |
| **D5** | Entities, or archetype+parameters? | **Entities are the record; archetype is a derived, optional annotation.** Diagrams, GNN comparison and drift all need real nodes. DESIGN_BRIEF §8's "the twin-config catalog is a prior over network shapes" stays true and is served by *deriving* an archetype guess as a field, not by shaping the IR around one. An archetype-shaped IR could not answer "which host changed". |
| **D6** | Cross-PIT entity identity | **A natural key, not the run-scoped id.** `Q1` D3 made `ent-NNNN` **run-scoped** — two runs produce unrelated `ent-0001`s, so today two snapshots cannot be joined at all. The IR's node key is the resolver's own parsed identifier where one exists (computer name, MAC — `LES-107` already normalizes these across NTLM/Kerberos/DHCP spellings), falling back to the address. **This is the prerequisite, not a detail:** without it, drift analysis does not work, and it is the one item here that is genuinely unbuilt rather than merely unassembled. |
| **D7** | Where does the compiler live? | **In aximprint, as the `synth` verb, over the IR.** This *reverses* the 2026-08-11 ruling that put the transformer in the axRange repo, and the reasoning is worth stating because it is not a retreat from that ruling's intent: **the IR is what buys independent evolution, not the compiler's repo.** With an IR in between, axRange's `extra="forbid"` vocabulary no longer dictates aximprint's schema — which was the whole concern. Given that, the location becomes a much smaller question, and one implementation beats two: a compiler in each repo would drift, and `LES-111` is precisely the lesson that a cross-repo mapping has **no test that can protect it**, since neither repo may import the other. Keeping it here also keeps the free acceptance gate (axRange's own expander + four-pass validator, run as an opt-in script). |
| **D8** | Where does drift comparison live? | **aximprint.** It owns the temporal store, the resolver, and the coverage metadata D4 depends on. A comparator elsewhere would need all three. |
| **D9** | Does the IR carry provenance per field? | **Yes, and this is the point of it.** `evidence_class` + confidence travel on every derived fact (role, segment, binding). A twin_config compiled from it marks fields `observed`/`imputed`/`defaulted` because the IR said so, rather than because the compiler guessed. |

## Build steps (do not start before D1–D9 are ratified or overruled; D4a/D4b included)

1. **Define the IR schema** around what is *stable across PITs*, not what is convenient to emit
   today. Entities (natural-keyed, D6), segments, edges, roles, identity bindings — each with
   provenance (D9) — plus the observation extent and coverage block (D3/D4), the latter **per
   segment** and carrying each entity's within-PIT window occupancy (D4a).
   - **Per-segment coverage does not exist yet**, and D4a is what makes it necessary. The window
     health record is global today (`capture_loss_pct_max` is per-sensor). Deriving a per-segment
     proxy — traffic observed from a segment against a running expectation — is new work, and its
     output is **inferred, not measured**, and must say so (`LES-006`). Without it, absence
     weighting cannot separate a decommission from a tap outage on one VLAN.
2. **Solve cross-PIT entity identity** (D6). Prerequisite for anything comparative.
   **LANDED 2026-08-14** — `src/aximprint/ir/keys.py`, 24 tests. Pure: it takes identifiers and
   addresses and returns `(entity_key, key_kind)`; wiring it to the store is step 3.

   ### Step 2 RESULT — measured on the real baseline

   Every entity keys, and nothing collides: **3,302 of 3,302 keyed, 0 collisions**. Precedence
   `hostname` → `mac` → `address`, one identifier chosen rather than a composite, keys
   self-describing (`hostname:bw-l`).

   **But the number that matters for the drift purpose is smaller, and it is the honest headline:**

   | | placed entities (the twin-side population) |
   |---|---|
   | `hostname`-keyed | 19 |
   | `mac`-keyed | 5 |
   | **strong-evidence joins** | **24 of 197 — 12%** |
   | `address`-keyed | 173 — 88% |

   D6 already labels `address` the weak kind ("asserts only that the address did not change
   hands"), and `key_kind` is exactly what lets a comparator weight it — so the design handles
   this. What the measurement says is **how much of the twin sits on the weak path**, and on a
   DHCP network 88% is a real limit on cross-PIT drift claims about client hosts. Two things
   temper it: server and infrastructure addresses are stable in practice, and they are
   disproportionately the placed hosts that matter; and **anchor coverage should grow with capture
   duration relative to DHCP lease time**, since a host anchors when it renews a lease or
   authenticates — this baseline is ~3 hours. That last point is a **hypothesis, not a
   measurement** — it needs a longer capture to test, and it must not be quoted as a finding.

   **Two identifier namespaces are refused by name** (`NOT_A_KEY`): a Kerberos **user principal**
   (it names a person — keyed on it, every machine one administrator logged into would collapse
   into one entity, and worse the more diligent the administrator) and a **DNS name**
   (`scope=referenced`, a third party's address). Neither appears in the baseline's roster, so
   the refusal is currently exercised only by tests — stated so nobody reads "0 declined" as
   evidence the rule never has to fire.
3. **Materialize:** `snapshot(as_of)` → IR rows, written to the separate store (D2). Deterministic
   and re-derivable from the evidence log; **never edited in place, and never a second source of
   truth** — the evidence log remains the source of truth, the belief store its rebuildable
   projection, and the IR a frozen, versioned view of that projection at one instant.
   **LANDED 2026-08-14** — `ir/materialize.py`, `ir/store.py`, `aximprint materialize`, 21 tests.
   **Step 1b landed with it** rather than in the windowing layer: per-segment coverage turned out
   fully derivable from `node_features`, which already records each entity's segments per window,
   so building an accumulator upstream would have computed what the store could already answer.

   ### Step 3 RESULT — the first real PIT

   `aximprint materialize --store … --out …` on the 2026-08-11 baseline: **3,302 entities, 38
   segments, 4,893 edges, 4,796 identity bindings**, one PIT row carrying `as_of`, the observation
   extent, `capture_loss_pct_max = 4.57`, and the build fingerprint. Two materializations of one
   store are byte-identical. Re-writing an existing `pit_id` is **refused** unless `--replace` —
   the recommendation on record, taken, because something may already have compared against it.

   **The schema went to `1.1` because materializing found a hole in `1.0`.** Reading the first real
   PIT showed `segment IS NULL` conflating two entirely different populations: **117 local hosts on
   nothing provisionable** (IPv6 link-local, multicast) and **2,988 internet peers**. A comparator
   filtering the monitored network on `segment IS NOT NULL` would have silently dropped all 117 —
   the exact `LES-006` failure this artifact exists to prevent. `ir_entity.locality` closes it;
   additive, so MINOR. Nothing outside a scratchpad had read `1.0` and an in-place edit was
   available; it was **not taken**, because "nobody has read it yet" is the reasoning that erodes a
   versioning discipline the first time it is inconvenient, and an additive bump is
   backward-readable anyway.

   **And a bug found by the same reading, which is worth recording because it was pre-documented.**
   `_entity_locality`/`_entity_extents` read `node_features` **without canonicalizing across
   merges**, so every merged entity silently lost its rows — one entity came out with a segment and
   no locality at all. `node_features` is pre-merge (`Q1` D2's forward-only design) while
   `snapshot()` is post-merge; **`WORK_ORDER_synth_v1.md`'s phase-1 finding names this exact
   hazard**, and this walked into it anyway. Both readers now take the alias map, and
   `_entity_extents` requires it as a positional rather than defaulting it.

   Two things the materializer **refuses** rather than guesses at, both because a PIT that cannot
   say what it describes is not a PIT: a store nothing ever cleanly closed into (no `runs` row, so
   no source), and a store several runs from *different* sources wrote into (no single answer, and
   picking one would be false for the rest).
4. **Compile:** IR → `twin_config` + `tfc` + `tap` (D7). This is `WORK_ORDER_synth_v1.md` phase 3,
   now reading the IR instead of `snapshot()` directly. Its own findings still apply — the
   `extra="forbid"` sidecar constraint, node emission from the *placed* set, node collapse and D3
   of that work order. **LANDED 2026-08-14** — `synth/compiler.py`, `aximprint cast`, 23 tests.

   ### Step 4 RESULT — axRange expanded it

   **The acceptance gate was run and it passed.** The compiled `twin_config` went through
   axRange's own `expand_twin_config_raw` **in their venv**, and their expander produced
   **38 networks and 197 nodes**, with per-host IPs auto-allocated, per-network gateways
   auto-assigned at `network+1`, and the synthetic `gw` multi-homed across all 38 legs. Zero
   warnings. That is the question — *would this deploy* — answered without deploying.

   **The whole chain reconciles, which is the part worth checking rather than assuming:**

   | stage | count | |
   |---|---|---|
   | entities in the store | 3,302 | |
   | placed (local + provisionable) | **197** | phase 1's gate |
   | collapsed node groups + `gw` | **50** | this step |
   | axRange nodes after `count:` fan-out | **197** | their expander |
   | nodes carrying a role | **19** | phase 2's measurement, unchanged |

   **Five caveats the compiler emits about its own output**, each tracing to an earlier
   measurement rather than to caution: 117 local entities are not nodes (they are on nothing
   provisionable, and each is already present under its routable address); `gw` is synthetic
   because this vantage has neither of §8's gateway signals; `dns-resolver` has no axRange
   persona and so generates no traffic; **37 of 49 node groups carry no role**, so most of the
   twin is inert until client-side inference lands (phase 2, finding 2); and the tap plan is
   **incomplete by design** — every binding omits `sensors`, which names Lab (`o2`) sensors this
   project cannot observe, and a plan that fails their validator with "sensors missing" is a
   better artifact than one passing with a fabricated sensor name.

   **324 provenance fields in the sidecar**, not inline: axRange's vocabulary is `extra="forbid"`,
   so the v0 stub's `{value, provenance}` wrappers are *rejected by their schema*. This is also
   where `defaulted` legitimately enters — it is not an IR evidence class, because it is what a
   compiler does when a catalog supplies what discovery never observed. Roles arrive `imputed`,
   segments `observed` with their coverage, images and `gw` `defaulted`.

   **D3 remains open and the default is uncapped.** `--max-count` exists and a cap that bites is
   reported as a caveat naming the true host count, never silently applied.
5. **Compare:** two PITs → a structural drift report (D8), including the refusal path when the two
   are not commensurable. **LANDED 2026-08-14** — `ir/compare.py`, `aximprint drift`, 19 tests.

   ### Step 5 RESULT — D4 and D4a hold up, and step 5 found the gap that made them unusable

   Run on two real single-window PITs from the 2026-08-11 baseline:

   | | |
   |---|---|
   | local entities | **+43 / −16 / 2 changed / 258 unchanged** |
   | removed, classified | **12 `observed_absent`, 4 `not_observed`** |
   | segments | +1 / −1, 7 population changes |
   | edges | +1,693 / −1,679 / 1,421 unchanged |
   | external peers | +1,031 / −932 — **counted, never itemized** |

   **D4's classification does real work on the first try.** The 4 `not_observed` are exactly the
   hosts of `10.112.140.0/24`, the one segment that stopped being observed between the two PITs —
   the comparator declines to claim they left the network, because nothing was watching where they
   were. The 12 `observed_absent` sit in segments with coverage `1.0`. That is the distinction the
   whole coverage block exists for, and it separated 16 removals into two genuinely different
   claims without any tuning.

   **Partitioning by locality (schema `1.1`) is what makes the report legible at all.** External
   churn is **1,963 changes against 61 local ones** — a naive diff would be 97% noise from CDN
   addresses rotating for reasons that have nothing to do with the monitored network.

   **The honest caveat the report raises about itself:** 279 of 303 local entities join on an
   `address` key. Step 2 measured that as 88% and this is what it looks like in use.

   **The gap step 5 exposed, and the reason it is worth having built the comparator before the
   compiler.** `as_of` is *cumulative* — "everything known up to T" — so **any two PITs from one
   store have nested extents, differ in duration, and are refused as incommensurable.** The refusal
   was correct; the problem was that a single store could then never produce a comparable pair at
   all, which would have made the whole artifact unusable for its second stated purpose. Fixed by
   adding a **`since` bound** to `materialize()` and to `BeliefStore.snapshot()`: the two purposes
   want different shapes and both are legitimate — building a twin wants everything known,
   measuring drift wants what was observed during a span. `as_of` keeps its meaning as the upper
   bound; `since` makes explicit a lower bound that was previously implicit at the store's
   beginning. Aliases stay bounded by `as_of` alone: a merge is knowledge the resolver holds, not
   an event inside the span, so a host identified before `since` is still that host during it.

   **Refusals implemented** (each makes the report *wrong*, not merely uncertain, which is why
   none is a caveat): different sources, differing schema MAJOR, a PIT compared with itself, a
   zero-length window, and observation windows differing by more than `MAX_DURATION_RATIO` (2×).
   **Caveats implemented** (weaken without invalidating): a build change between the two PITs
   (`LES-113` — entity keys derive from the resolver's normalization), weak `address` joins, and
   capture loss or its absence.
6. The other consumers — topology diagram, GNN evaluation harness — are then ordinary readers of a
   documented schema and need no further design here. That is the test of whether step 1 was done
   right.

## Boundaries

- **Not** a replacement for the belief store, which stays internal, unversioned and free to change.
- **Not** a second source of truth. See build step 3.
- **Not** a behavioral-drift mechanism. DESIGN_BRIEF §10 compares behavior in *feature space*; this
  is the structural half, and the two are complementary, not alternatives.
- **Does not** ratify versioning for anything but itself (D1).

## A seam this creates in `catalog/roles.yaml`, recorded now

Phase 2's catalog puts two different things in one row: the **evidence** that infers a role
(`requires`, `confidence`) and the **twin content** that role implies (`image`, `resources`,
`install`, `persona`). Under the IR boundary those fall on opposite sides — the first is discovery
and belongs upstream of the IR, the second is compilation and belongs downstream of it. Joining
them in one row was right for phase 2 (it is what stops a role being inferred with no image) and it
will be wrong once the IR exists. **Do not duplicate the file when splitting it**; move the content
half. Flagged here so whoever writes build step 4 finds it as a decision rather than a surprise.

## Verification

The 2026-08-11 draft's question is still exactly right, and D1–D9 have to be judged against it:

> Take two IR PITs over the same network at different times. **Can you both deploy either one, and
> say what changed between them?** A representation that supports the first and not the second has
> met half the requirement — and it is the half that was already nearly possible.

Two additions this redraft makes to that test, both from D4:

- **Can it refuse?** Given two PITs whose observation extents are not commensurable, the comparator
  must decline rather than report drift. A comparator that always answers is not measuring anything.
- **Can it tell "gone" from "unobserved"?** Both look identical in an IR that records only presence.
- **Can it grade absence rather than assert it (D4a)?** The test is a sequence, not a pair: an
  entity missing from one PIT and back in the next must be distinguishable from one missing across
  a run of them, and **both must be distinguishable from a segment whose coverage lapsed over the
  same interval**. An IR that cannot express the third case will teach whatever consumes it to read
  a tap outage as a decommission.
