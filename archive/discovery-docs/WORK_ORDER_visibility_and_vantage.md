# WORK ORDER — vantage, visibility, and what the tap could not see

**STATUS:** **RATIFIED 2026-08-17 by the user — D1–D12 as drafted, D5a included.** Drafted
2026-08-14; the decisions below were **proposals by Claude Code** from a direction the user stated
that session (D10–D12 folded in from its second half — the time axis) and are now rulings, in the
`WORK_ORDER_entity_resolution.md` idiom. **BUILT 2026-08-17**, all ten build steps; three additions
the build discovered are marked **[build]** in the decision table and in `docs/CHANGELOG.md` —
each additive to a ratified decision, none a reversal of one.
**Depends-on:** `WORK_ORDER_network_ir.md` (COMPLETE 2026-08-14 — this extends its schema and its
comparator), `WORK_ORDER_synth_v1.md` (COMPLETE).
**Blocks:** honest drift comparison across captures, every ML use that needs to reason about what
was *not* seen, and any claim that a twin faithfully represents behavior on a cadence longer than
the capture it came from.
**Authority:** **direction is the user's** (this session): a pcap is only the tap point's view;
several taps combined give a richer picture of what is present *and what is missing*; most IR nodes
will legitimately have no role; the twin contains only role-bearing nodes, scaled down to fit a
cluster; and drift evaluation must account for all of that. **Extended in the same session:** a PIT shows
only the nodes *active* during its window, so depending on the windowing interval and the network's
own traffic schedule a narrow PIT is a partial view **in time** as well as in vantage — combining
snapshots across a time range gives a fuller picture from one tap point, and how much to combine
should be tunable. Everything below "What this actually is" is Claude Code analysis.
**Supersedes:** `ir/materialize.py::_run_context`'s mixed-source refusal (D3 replaces it), and
`synth/compiler.py`'s emit-every-placed-node behavior (D5).

---

## What this actually is

**DESIGN_BRIEF §8, which the project has never built.** The user's framing restates it almost
exactly, and quoting §8 is the fastest way to see that this is the design of record catching up
rather than a new direction:

> **Partial visibility** inverts the problem: estimate the blind spots *first*. Half-flows,
> referenced-but-unseen hosts (DNS answers, SPNs, redirects, XFF), and TTL inconsistencies yield a
> per-segment **visibility model** (observed fraction). The GNN becomes an imputation engine …
> link prediction fills expected edges; role composition + referenced-host census extrapolates
> counts … The visibility model is an *actionable output*: a tap-placement recommendation.

**One thing the user's framing settles, worth recording because it was argued the other way
earlier today.** Drift is compared at the **IR** — the full tap-point view — and *not* at the twin,
which is filtered and scaled down. Comparing twins would read a scale-down cap as network change.
That the IR sits between them is what makes this workflow expressible at all, and it is the
strongest post-hoc argument for `WORK_ORDER_network_ir.md`'s D1 reversal.

## Two axes of partial view, and they are not the same operation

The user's framing names both, and conflating them is the easiest mistake available here:

| axis | what is partial | union semantics |
|---|---|---|
| **vantage** | one tap sees one slice of the topology | coverage takes the **best** across taps — if either saw the segment well, the combined view did |
| **time** | one window sees only what was *active* in it | windows **accumulate**; occupancy survives as a fraction of a larger denominator |

They compose (several taps, each over a span) but they are not interchangeable, and a single
`merge` that ignored which axis it was on would compute coverage wrongly for one of them.

**The time axis pulls against drift detection, and that tension is the reason the tunable must be
explicit.** Union is exactly what destroys the absence signal: a PIT spanning 40 windows says every
host that ever appeared exists, and can no longer say who is there *now*. Widen far enough and
decommissioned hosts are in the twin. So:

| purpose | wants | because |
|---|---|---|
| **twin building** — what exists | **wide** | a host absent from a narrow window is missing from the twin entirely |
| **drift detection** — what is here now | **narrow** | a wide window makes everything look present |

One global window setting cannot serve both. D10–D12 resolve this by separating *how much was
captured* from *how much of it reaches the twin*.

## What already caters to this, unchanged

Stated so the build does not re-do it:

- **Per-segment coverage** (`ir_segment.windows_present`/`windows_total`, D4a) is already a
  visibility signal, recorded per PIT.
- **Absence classification** already refuses to claim what the vantage could not see — the
  `not_observed` class, which fired correctly on real data (4 of 16 removals, all in the one
  segment that stopped being observed).
- **Role-less nodes are a supported state**, not an error path. `role` is nullable end to end, and
  the IR is *supposed* to be full of them.
- Evidence classes, confidences and caveats are the machinery this all rides on.

## Measured 2026-08-14, before designing anything

| finding | measurement |
|---|---|
| **`ir_pit.source` is a filesystem path, not a vantage** | `/tmp/aximprint-zeek-test` — says nothing about where the tap sat |
| **`harvest` writes no manifest at all** | the cold tier is bare timestamped directories; the appliance host and stream ids `acquire` knew are **discarded at the disk boundary** |
| **Referenced-but-unseen hosts already exist in the store, and the IR drops them** | **2,418** addresses appear as DNS answers and were never observed in any flow (`identity_bindings`, `scope=referenced`) |
| …of those, inside the estate | **9** — this baseline is a near-full-visibility vantage, so its blind spot is genuinely small. On the partial vantage this work order is for, that number *is* the blind spot |
| twin nodes carrying no role | **37 of 49 groups**, 178 of 197 hosts |
| **window width materially changes which hosts a PIT contains** | of 196 placed hosts on a 3-window PIT: **118 in all 3, 32 in 2, and 26 (13%) in only one**. Narrow the window and that 13% is simply absent |
| **axRange's own persona cadences imply a required PIT width** | across 28 personas: **22 emits are `diurnal`, 26 are `24x7`**, plus `cyclic:24h`, `cyclic:2h`, `burst:2h`. A `diurnal` persona cannot have been observed at all in under 24 h |
| …so the current baseline is structurally too short | **3 hours.** It cannot have seen a daily cycle, so any diurnal behavior in it is an accident of when the capture ran. A capture-planning fact, not a code gap |

**The vantage-identity gap is not an inference problem.** `acquire` knows the appliance host and the
streams; nothing carries them past the disk boundary. This is metadata that was thrown away, and no
model can recover it.

---

## Decisions this draft makes (all overrulable)

| # | Decision | Ruling |
|---|---|---|
| **D1** | Is vantage first-class on a PIT? | **Yes — `ir_pit.vantage`, additive, schema `1.2`.** Without it the comparator has two failure modes and the second is dangerous: re-harvesting one tap into a new directory is **falsely refused** (`source` differs), and two *different* taps writing to the same path are **falsely accepted** and their view difference reported as network drift. |
| **D2** | Where does vantage identity come from? | **Three tiers, in order, and never guessed.** (1) A **harvest manifest** `acquire` writes beside the cold tier — the authoritative source, since that layer is the only one that knows the appliance and streams. (2) An operator-supplied `--vantage` on `ingest`, for a log directory of unknown origin or a `--from-pcaps` run with no appliance. (3) `None`, meaning **unknown** — never a default string, never the source path (`LES-006`). Layering is unaffected: a manifest is a **sensor artifact on disk**, exactly what `acquire` is for, and `ingest` reading one is reading sensor output. |
| **D3** | Network identity vs capture identity | **Introduce `network` as distinct from `vantage` and `source`.** The model currently has no concept of *the network* separate from *the capture*, which is why `materialize._run_context` refuses a mixed-source store: it conflates *different networks* (refuse — correct) with *different vantages on one network* (**merge — the entire point**). `network` is operator-declared; nothing in traffic identifies it. Replace the source-equality refusal with a network-identity check. |
| **D4** | How do multiple **vantages** combine? | **A separate `merge` operation producing a combined PIT**, not an implicit union inside `materialize`. **Amended by D10: `merge` must name its axis** — this row is the vantage axis only. Entities, segments and edges union; per-segment coverage takes the **best** across vantages; and `ir_entity` gains a **`vantages`** column (additive) recording which vantages saw each host. Without that column the combined PIT could not answer *"what does tap B see that tap A does not"*, which is the question multi-tap exists to answer. **`drift` then requires same network AND same vantage-set** — comparing a one-tap PIT against a two-tap PIT is a view change wearing drift's clothes, and refusing it is the same discipline as the duration limit. Compose by merging first, then drifting the merged PITs. |
| **D5** | Does the twin keep role-less nodes? | **No — filter to role-bearing nodes**, reversing `synth/compiler.py`'s current emit-everything behavior. 178 of 197 hosts would otherwise deploy as inert VMs running a default image and generating no traffic, consuming cluster capacity to represent nothing. Knock-ons that must be handled rather than discovered: segments left with no nodes are dropped, conduits referencing them are dropped, and **every drop is disclosed in the caveats and the provenance sidecar**, never silent. |
| **D5a** | Role-bearing, or role-**and**-persona? | **Role-bearing**, which is the looser of the two, and the reason is `dns-resolver`: it has a role and **no persona** (axRange ships none), yet the workstations that survive the filter structurally need it. Dropping it would produce a twin whose remaining hosts cannot function. Generalizes: a role with no persona is *infrastructure that runs a real service*, not a host with nothing to do. **This one is worth the user's attention specifically** — it is a judgment about what a useful twin is. |
| **D6** | Scale-down must be recorded, not just applied | The compiler's `--max-count` already reports a cap in `caveats`. **Extend it into the provenance sidecar as structured data** (`observed` vs `emitted` count per node group), so a consumer can reconstruct the real population from the twin. A twin that silently under-represents its network is the same failure as a redacted field (CONVENTIONS §3). |
| **D7** | What is the visibility model, minimally? | Two things the IR can carry today, before any model exists. **(a)** Per-segment observed fraction — **already present**, needs only to be read as a visibility signal rather than only as an absence gate. **(b)** A **`ir_unseen`** table (additive): hosts the vantage *knows exist* but never observed — the referenced-but-unseen census, 2,418 rows on this baseline, currently discarded. Together these answer *"how much of this segment did we see, and who do we know we missed"* with no inference at all. §8's tap-placement recommendation is then a report over them, and is **out of scope here**. |
| **D8** | Where does ML enter, and what must precede it? | **Metadata first, model second — and much of what is attributed to ML here is missing metadata, not missing inference.** Vantage identity, the vantage-set of a merged PIT, what the compiler filtered and scaled: no model can recover any of them if nobody wrote them down, and recording them makes the comparator substantially smarter with **no model at all**. What ML then genuinely earns, all of it from §8/§6 and all of it needing D1–D7 as input: **imputation of the unseen** (link prediction for expected edges; role composition + referenced-host census to extrapolate counts — this is where `imputed` finally carries volume), **role inference from client behavior** (the 178 role-less hosts, matching outbound patterns against the persona catalog's `emits[]`), and **anomaly scoring over presence patterns** (D4b of the IR work order: not *"is this host gone"* but *"is this pattern normal for this host"*). |
| **D9** | Does the IR itself ever filter? | **No, and this is the layering the user described — stated here so nobody "optimizes" it later.** The IR is the **complete tap-point view**, role-less hosts and all. Filtering and scale-down happen at compile time, in the twin. Push either upstream into the IR and drift comparison silently starts measuring the compiler's policy instead of the network. |
| **D10** | How is the **time** axis combined? | **Two mechanisms, and the first already exists.** (a) **Materialize over a span** — `materialize(store, since=W1, as_of=W40)`, added by `WORK_ORDER_network_ir.md` build step 5, *is* the union of 40 windows. It is **exact**, because it re-reads the belief store and re-aggregates from source rows. **This is the preferred mechanism and the build should say so**, not add a second way to do the same thing. (b) **Combine existing PIT artifacts**, for the one case (a) cannot serve: the belief store has been pruned (`Q5` decay) or is gone, and only the IR survives. Combination carries a constraint (a) does not: **overlapping PITs double-count** — summing edge flows across PIT(W1–20) and PIT(W10–30) counts W10–20 twice — so it must **refuse overlapping spans** rather than silently produce inflated volumes. `merge` therefore takes an explicit axis (`vantage` per D4, or `time`), because the two compute coverage differently and a single untyped union would get one of them wrong. |
| **D11** | What is the twin's actual tunable? | **A compile-time occupancy threshold, not a window width** — and this is deliberately *not* what the request literally asked for, because the request is better served this way. Since `windows_seen`/`windows` survives into every `ir_entity` row, **one wide PIT serves many thresholds**: `cast --min-occupancy 0.5` deploys hosts present in at least half the windows, and changing the number re-shapes the twin without re-materializing anything. Three things this buys over a width parameter. **(1)** One artifact, many twins — width forces a re-materialize per answer. **(2)** It lands where filtering already lives (D5, D9), rather than pushing a twin-shaping decision up into the IR, which D9 forbids. **(3)** It composes with recency: `last_seen` is already carried, so *"present often, but not lately"* is expressible and a long PIT need not drag decommissioned hosts into the twin. **Default: include everything** (threshold 0), so the behavior is unchanged until somebody chooses otherwise, and **whatever a threshold excludes is disclosed** in the caveats and sidecar exactly as a `--max-count` cap is (D6). Width remains a real decision, but it is an **acquisition** decision (D12), not a compile one. |
| **D12** | How wide does a capture need to be? | **Derivable from the personas being modelled, and it should be stated rather than guessed.** axRange's catalog declares each emit's cadence, and that is a lower bound on useful capture length: a `diurnal` persona (22 emits) or a `cyclic:24h` one cannot have been observed at all in under 24 h; `burst:2h` needs at least 2 h; `24x7` needs almost nothing. So *"is this capture long enough"* is answerable from the intended twin rather than from intuition. **Two consequences.** The compiler should **warn when a PIT's span is shorter than the cadence of a persona it is assigning** — a `diurnal` assignment off a 3-hour PIT is a claim the evidence cannot support. And the current baseline is **3 hours**, so it is structurally too short for the 22 diurnal emits: recorded here as a capture-planning finding, since no amount of code fixes it. **Not in scope:** deriving a recommended capture length automatically. This decision only requires that the mismatch be *visible*. |

## RESULT (built 2026-08-17, all ten steps)

Measured end to end on the 2026-08-11 baseline (`/tmp/aximprint-zeek-test`, 3 windows, 3 hours),
re-ingested with `--vantage corpapcn:1-4 --network corp`. Row counts are **identical** to the
pre-change baseline (22,870 assertions, 12,914 identity bindings, 5,107 node features), which is
the right result: vantage is metadata that was being thrown away, not a change to what is learned.

| claim | measured |
|---|---|
| the referenced-but-unseen census the IR was dropping | **2,418** rows, exactly the figure D7 predicted |
| …of which not on the internet | **9** — and **0** inside an observed segment. All nine are IPv6 link-local mDNS names (`bw-mbp15.local`, `dev06:axfsd…`) or `ipv4only.arpa`'s `192.0.0.170/171`. On a near-full-visibility tap the estate blind spot is genuinely **empty**, which is a stronger statement than "9" and only visible because `locality` and `segment` answer different questions |
| segments below the coverage floor | **2 of 38** (both 1 of 3 windows) |
| the twin, before D5 | 38 segments, 50 node groups, 197 hosts, **178 of them role-less** |
| the twin, after D5 | **9 segments, 12 node groups + `gw`, 19 hosts, 8 conduits, 4 persona assignments** — 29 orphaned segments pruned and named |
| the acceptance gate | axRange's own expander: **9 networks, 20 nodes, zero warnings** |
| D6's reconstruction claim | `placed 197 = 19 emitted + 178 role-less + 0 occupancy-excluded`, and it still balances at every threshold (`0.5`: 19 + 151 + 27; `1.0`: 17 + 119 + 61) |
| D11, one PIT many twins | `--min-occupancy 0/0.5/1.0` → 19 / 19 / 17 hosts, **no re-materialize** |
| D12, fired for real | `corp-workstation` and `file-server` are `diurnal`; the PIT spans 3.0 h. Both warn |
| drift, after D3/D4 | unchanged verdicts (16 removed, 12 `observed_absent` / 4 `not_observed`) and the report now names the vantage and network of each side |

**Three additions the build made, each additive to a ratified decision and none a reversal.**

1. **`ir_pit.vantages` / `.merge_axis` / `.merged_from`** beyond step 2's list. D4 requires a
   merged PIT and D10 requires it to name its axis; an artifact that cannot say *which* taps it
   combines cannot support D4's own "same vantage-set" refusal, and one that cannot say which axis
   it used has two incompatible readings of its own `windows` column.
2. **`vantage` and `since` joined the PIT identity.** Two *different* taps writing to one directory
   shared a `pit_id` and could not coexist in a store at all — D1's own scenario. And `since`,
   added by the previous work order without revisiting identity, made `[W1,W2]` and `[W2,W2]`
   collide, which is exactly the pair D10's preferred workflow produces first. An unbounded,
   vantage-less PIT's id is unchanged (`LES-113`).
3. **`snapshot()` was reading `identity_bindings` unbounded** — a real defect, found because D7's
   census inherited it: a `since`-bounded PIT carried every binding the store ever held.

**And one measured correction to D10.** *"Materializing over the span is preferred"* was reasoned;
it is now a fact with a number. A time merge combines rows that were already **keyed**, so it
cannot re-key them, while entity resolution works across the seam it cannot see: **3,237 entities
merged against 3,231 materialized**, the six extras being hosts whose hostname or MAC evidence fell
in only one window (`10.80.20.178` → `hostname:idrac-c28pf33`, five more). A combined PIT is a
**superset** — it fragments identity, never loses a host — and the fragmentation is proportional to
how much host-scoped evidence lands on one side of a seam.

**Still open, unchanged by this round:** D3 of `WORK_ORDER_synth_v1.md` (the node-count cap), and
§8's tap-placement recommendation, which this work order builds the inputs for and deliberately
does not attempt.

## Build steps (all complete 2026-08-17)

1. **Stop discarding vantage.** ✅ `src/aximprint/vantage.py` (the format, below every layer, imported by both sides), `acquire/harvest/pipeline.py` (writes it, before the first window so an interrupted harvest still records its tap), `ingest/zeek/sources.py::source_vantage` + `IngestPlugin.probe_vantage` (reads it without pulling an event), `cli._resolve_vantage` (the three tiers, and the refusal), `runs.vantage`/`.network`. A harvest manifest written by `acquire` beside the cold tier
   (appliance, streams, time range), read by the zeek plugin, carried through to `ir_pit.vantage`.
   Plus `--vantage`/`--network` on `ingest` for the tiers below it (D2).
2. **Schema `1.2`** ✅ — plus `ir_pit.vantages`/`.merge_axis`/`.merged_from`, see RESULT addition 1. — `ir_pit.vantage`, `ir_pit.network`, `ir_entity.vantages`, and the `ir_unseen`
   table. All additive; a `1.1` reader keeps working (§11's own rule, and the reason MINOR exists).
3. **Populate `ir_unseen`** ✅ — 2,418 rows on the baseline, and it found the unbounded `snapshot()` read (RESULT addition 3). from `identity_bindings` at `scope=referenced` minus everything seen in
   a flow. Pure re-derivation from the store — no pipeline change, the same shape step 1b turned
   out to have.
4. **Replace the mixed-source refusal** ✅ `ir/materialize._run_context`, `ir/merge.py`. with a network-identity check (D3), and add `merge` (D4).
5. **`drift` requires same network and same vantage-set** ✅ `ir/compare._check_same_view`.; a mismatch is a refusal with a message
   naming which, and pointing at `merge`.
6. **Compiler: filter to role-bearing nodes** ✅ 197 → 19 hosts, 38 → 9 segments, all drops disclosed. (D5/D5a), prune orphaned segments and conduits,
   record every drop and every scale-down in the sidecar (D6).
7. **A visibility report** ✅ `ir/visibility.py`, `aximprint visibility`. over per-segment coverage and `ir_unseen` — the input to §8's
   tap-placement recommendation, which is itself out of scope.
8. **`merge` takes an explicit axis** ✅ `aximprint merge --axis {vantage,time}`. (D4/D10): `vantage` takes the best coverage across taps,
   `time` accumulates windows and refuses overlapping spans. Document that materializing over a
   span is the preferred time-union and that artifact combination exists for the pruned-store case.
9. **`cast --min-occupancy`** ✅ measured at three thresholds off one PIT. (D11), defaulting to include everything, with exclusions disclosed in
   the caveats and the provenance sidecar alongside `--max-count`'s.
10. **Cadence-vs-span warning** ✅ `catalog/roles.yaml` gained `persona_cadence`, copied from axRange's catalog and parsed rather than shape-matched. (D12): the compiler warns when it assigns a persona whose cadence
    is longer than the PIT it was compiled from.

## Boundaries

- **Not** the tap-placement recommendation (§8's actionable output). This builds its inputs.
- **Not** any ML. D8 says why the ordering matters; every model listed there wants D1–D7 first.
- **Not** a change to what the IR contains per host — D9. The IR stays the complete view, and the
  occupancy threshold (D11) filters at compile time rather than at materialize.
- **Not** an automatic recommendation of capture length. D12 requires only that a span/cadence
  mismatch be visible, not that the tool choose the span.
- **Not** a new `MAJOR`. Every schema change here is additive.

## Verification

The question this has to answer, which nothing today can:

> Take two captures of one network **from different tap points**. Can you say what each saw, what
> only one saw, and what neither saw but both have reason to believe exists — and then, having
> merged them, compare that against the same merged view a month later without the tap difference
> showing up as network change?

Three narrower checks that fall out of it, each of which fails today:

- **Two PITs from one tap, different directories, must compare.** They are refused today
  (`source` differs).
- **Two PITs from different taps must NOT silently compare.** They do today whenever the paths
  happen to match.
- **A twin compiled from a filtered, scaled PIT must state what it dropped**, in structured form,
  well enough that the original population is reconstructible from the sidecar alone.
- **One wide PIT must produce different twins at different occupancy thresholds** without being
  re-materialized — that is the whole claim of D11, and it fails today because the knob does not
  exist.
- **Two adjacent PITs merged on the `time` axis must equal the single PIT materialized over their
  combined span** for entity membership, and must **refuse** if the spans overlap. The equality is
  what proves the combination is a real union rather than an approximation of one.
- **Assigning a `diurnal` persona off a 3-hour PIT must warn.** It does not today, and it is the
  most common way a twin will quietly claim behavior nothing observed.
