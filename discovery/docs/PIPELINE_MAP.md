# PIPELINE MAP — what is built, what is a placeholder, and where the gaps are named

**Genre:** a **state map** — a genre `docs/CONVENTIONS.md` §2 now declares (2026-08-14, user
ruling; the first genre this project added rather than inherited). Named by subject, same footing
as `SESSION_STATE.md` and `RUNBOOK_CORPAPCN.md`. It is not design of record (that is
`DESIGN_BRIEF_aximprint.md`) and not a decision record (that is `CHANGELOG.md`), and it holds no
authority of its own: where this map and the document governing a claim disagree, that document
wins. It answers one question the other docs answer only in pieces: **at a glance, how far along
is each layer, and what does it hand the next one?**

**As of:** 2026-08-26 — **the ingest layer has three sensors and one gate between them.**
`axquery` landed 2026-08-20 (`docs/WORK_ORDER_axquery_ingest.md`) and Track B of
`docs/WORK_ORDER_axrange_integration.md` landed a **Suricata `eve.json` plugin** on 2026-08-25,
and with it a **fourth tier-2 gate leg**: sensor equivalence. That gate is **Zeek-vs-Suricata only** —
the two sensors that read the same plane — and `axquery`, which reads an appliance's already-inferred
entity database over HTTP, has no automated counterpart at all (`Q37`). Read the `ingest/` box below,
and note the `!` markers in it — `arp` is parsed and deliberately anchors nothing (`Q32`), a Suricata
run has no capture-loss signal at all (`Q35`), and axquery declares no locality so every segment
under it is `imputed` at 0.6. The finding that outweighs the plugin is `LES-123`: **the cross-sensor
gate passed while both sensors were broken identically**, because those two plugins differ only above
`events/` while `windowing/` is one body of code serving all three.

**Previously, 2026-08-19** — **the whole chain ran on ten days of real capture for the first time**
(corp-apcn, all twelve streams): 6.17 TB harvested through a host that never held more than 60 GB
of it, 1,440 windows, zero failures; a 0.32 GB belief store; and the first real PIT — **58,563
entities, 82 segments, 109,173 edges, 38,252 unseen**, of which **430 are placed** and **85 are
placed-and-roled**. That run drafted three work orders, **none ratified, none started**, and one of
them proposes reversing a boundary this map currently draws — see "What is proposed to change"
below before trusting the evidence→IR arrow.

**Previously, 2026-08-17** — `docs/WORK_ORDER_visibility_and_vantage.md` ratified by the user (D1–D12
as drafted) and **built end to end the same day**: a PIT now records **which tap** it is
(`ir_pit.vantage`, schema `1.2`), the referenced-but-unseen census reaches the IR (`ir_unseen`,
2,418 rows on the baseline), `merge` combines PITs across taps or across spans on an **explicit
axis**, `visibility` reports what a vantage saw and missed, and the twin holds **role-bearing
nodes only** (197 hosts → 19). The `synth/` box below is no longer a STUB in any sense.

**Previously, 2026-08-14** — `docs/WORK_ORDER_zeek_stem_coverage.md` complete end to
end (steps 1, 2, 2b, 3, 4, 5), `Q29`'s subject-less channel landed, and **entity resolution v1
landed the same day** (`Q1`/`Q9` closed): the pivot below is no longer a placeholder. **Also that
day, `docs/WORK_ORDER_synth_v1.md` phases 1 and 2** — hosts get a network (`windowing/segments.py`)
and a role (`catalog/`), and `imputed` gets the first producer it has ever had. **And, latest of
all that day, the user ratified the network IR** (`docs/WORK_ORDER_network_ir.md`), which reverses
that work order's D1, re-scopes its phase 3 into two steps, and is **the project's first §0.2
schema ratification** — read the synth box's own note on how narrowly that is scoped.
Numbers and status labels below are true at that commit — see "What drifts here" at the bottom
before trusting any of them at a later one.

**Revision, same day:** the first drawing of this map showed the ML learner layer as a **terminal
box** hanging off the feature archive, with no path back. That was wrong, and the two sections
after the diagram say why — it is the correction most worth reading here, because the earlier
shape made the learner layer look like an optional side quest rather than the thing the belief
store exists to be written into.

```
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ APPLIANCE  —  Axellio Xpress / PacketXpress          NOT aximprint       │
     │ stores packets · axapi REST is the only way in                           │
     │ version-locked wire contract · vendor defects measured, documented       │
     └──────────────────────────────────────────────────────────────────────────┘
                                           │  pcap — multi-GB, gated on cost + blast radius
                                           ▼
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ acquire/                                     LANDED 2026-08-10  DONE     │
     │ was the standalone `xtract` repo — 13 modules + harvest/'s 4             │
     │   axapi client/auth · disk-governed spool · pinned Zeek 8.0.9            │
     │   in a container · harvest = extract → Zeek → reap, concurrent           │
     │   harvest --from-pcaps SRC — Zeek stage alone, no appliance  08-11       │
     │                                                                          │
     │ ! THE REPO NOW OWNS A CAPTURE PATH.  The old claim "aximprint            │
     │   never sees a packet" was about PACKAGING and is dead.  What            │
     │   survives is a LAYERING RULE, and it is checked, not asserted:          │
     │      acquire  ──x──►  events · ingest · windowing · store · sinks        │
     │   produces sensor artifacts ON DISK ONLY · AST-walk test,                │
     │   mutation-proven · `requests` isolated in the [acquire] extra           │
     └──────────────────────────────────────────────────────────────────────────┘
                                           │  metadata log tree — the COLD TIER, one dir per window
                                           │  (gitignored: repo hygiene, not privacy — LES-003)
                                           ▼
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ ingest/                                                        DONE      │
     │ registry + THREE SENSORS: zeek · axquery · suricata                      │
     │ TRANSPORT SPLIT FROM SEMANTICS, all three:                               │
     │   <sensor>/sources.py  locates, fetches + decodes                        │
     │   <sensor>/plugin.py   owns the parser/endpoint table, order, merge      │
     │ NO FALL-THROUGH anywhere (Q24's rule) — but the CENSUS UNIT differs:     │
     │   zeek = a log STEM · suricata = an event_type FIELD                     │
     │   axquery = an ENDPOINT.  These are not interchangeable.                 │
     │ reorder.py sits BELOW every plugin — reorder_by_key/in_order/is_ip;      │
     │   the HORIZON CONSTANTS did not move (each plugin measures its  LES-119  │
     │   own; suricata's is honestly labeled UNMEASURED)                        │
     │ STREAMING: per-stream reorder buffer (seconds) → heapq.merge             │
     │ peak mem O(streams), not O(events) · disorder counted + emitted          │
     │                                                                          │
     │ zeek      TSV / JSON-lines, gzipped · flat dir · WINDOW TREE             │
     │           (auto-detected) · parquet:<root>                               │
     │           36 log types parsed · 7 skipped by name  ← wide vantage, 08-20 │
     │           43 = 36 + 7, every stem a TWELVE-stream    Q24 built  LES-120  │
     │           vantage produces (was 35 = 28 + 7, one tap)                    │
     │ axquery   HTTP over stdlib urllib (NOT requests) — an appliance's        │
     │           already-inferred ENTITY DB, not records off disk               │
     │           5 endpoints in table · 3 declined BY NAME on measurement       │
     │           NOTHING PAGES → /api/connections read by TIME-BISECTION        │
     │           (500 rows naive vs 50,796 bisected) · no total* is a total     │
     │           measurements observed · conclusions IMPUTED (D1 carve-out)     │
     │           no MAC · NO LOCALITY → segments RFC1918 @ 0.6      ! imputed   │
     │           s0_count/rej_count published 0 for 5 days → DELETED Q34 CLOSED │
     │           NO EQUIVALENCE GATE — different plane, no A-vs-B    ! Q37      │
     │ suricata  eve.json — ONE stream, discriminated on the event_type FIELD,  │
     │           so a directory listing is NEVER a census here     ! weaker     │
     │           18 parsed · 25 declined = 43, disjoint at import,   per-window │
     │           covering all 35 of `eve-log: types:`               census      │
     │           kind spellings REUSE zeek's → projections inherited            │
     │           arp parsed but UNSCOPED — counted, anchors nothing  ! Q32      │
     │           _stats declines every counter → NO loss signal      ! Q35      │
     └──────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ events/            the contract both sides speak               DONE      │
     │ Flow · Identity · Service · Alert   +   SensorHealth                     │
     │ 8-class flow-state vocabulary · unmapped counted, never bucketed         │
     │ the ONE thing that stays stable when Xpress replaces Zeek                │
     └──────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ windowing/         THE PIVOT — 1 h window × 12 sub-buckets               │
     │                                                                          │
     │       addresses  ───────────────►  entities                              │
     │                                                                          │
     │   entities.py           v1 — evidence CLASSIFIED, then       DONE       │
     │                         weighted. host/user/referenced;                 │
     │                         intervals + generations; merges                 │
     │                         forward-only.  Closes Q1 + Q9                   │
     │   sketches.py           exact stand-ins, O(input) not O(output)  Q8      │
     │   windows.py            top-64 peers + __longtail__ spill        Q11     │
     │   segments.py           address → twin segment; declines      DONE       │
     │                         what is not a network       LES-110              │
     │   SERVICE_PROJECTIONS   per-kind durable fact + predicate +              │
     │                         confidence, declared.  Closes Q27                │
     │   health record         unparsed_log_types/unrecognized_log_types  D5    │
     └──────────────────────────────────────────────────────────────────────────┘
                                           │  WindowResult — nodes/edges/identity_delta/health
                                           ▼
     ┌──────────────────────────────────────────────────────────────────────────┐
     │ ANNOTATOR SLOT                        CLOCK 1 — per window close         │
     │ §6: "All of them — heuristic and learned alike — are ANNOTATORS          │
     │ WRITING ASSERTIONS INTO THE BELIEF STORE, one uniform confidence         │
     │ scheme."   The two kinds of learner are PEERS, not stages.               │
     │                                                                          │
     │   learners/   deterministic extractors                 v0  BUILT         │
     │     listens_on 0.95 · bound_<kind> · talks_<proto> · services            │
     │     NOT ML on purpose — §6: "rule-shaped facts with rule-shaped          │
     │     evidence; ML adds uncertainty where none is needed"                  │
     │                                                                          │
   ┌►│   ML inference    GNN roles · Hawkes/Fourier/Markov    UNBUILT           │
   │ │                   fits · isolation forest / autoencoder                  │
   │ │     reads the SAME WindowResult, emits the SAME assertion shape          │
   │ │     ! this is where evidence_class=`imputed` would come from,            │
   │ │       IN VOLUME.  segments/ gave it a first producer 08-14,              │
   │ └──────────────────────────────────────────────────────────────────────────┘
   │                                       │  assertions — ONE shape, ONE scheme, either origin
   │                                       ▼
   │ ┌──────────────────────────────────────────────────────────────────────────┐
   │ │ sinks/             the WindowSink contract + registry          DONE      │
   │ │ the driver computes assertions once, hands (win, assertions) to          │
   │ │ every sink · shape: (subject, predicate, object, evidence_class,         │
   │ │ confidence, first_seen, last_seen, count, source)  §7                    │
   │ │ StoreSink · EvidenceSink · jsonl                                         │
   │ │ ! SPLIT REGISTRY: `import aximprint.sinks` alone yields a registry       │
   │ │   that LOOKS complete and is not (LES-020)                               │
   │ └──────────────────────────────────────────────────────────────────────────┘
   │                   │                                      │
   │                   ▼                                      ▼
   │ ┌───────────────────────────────────┐  ┌───────────────────────────────────┐
   │ │ store/                       DONE │  │ feature archive (files)           │
   │ │ evidence_log.jsonl                │  │ nodes · edges · identity ·        │
   │ │   = source of truth, append-only  │  │ windows + manifest.json           │
   │ │ belief_store (DuckDB)             │  │ features/  jsonl sink       DONE  │
   │ │   = rebuildable projection        │  │   vocabulary.py — pinned index    │
   │ │ node_features · edge_features ·   │  │   map · OOV · MAX_KEYS=512        │
   │ │ assertions · identity_bindings ·  │  │                                   │
   │ │ window_health · profile_facts     │  │ the archive is the TRAINING       │
   │ │ + runs   (Q29: no subject col)    │  │ corpus, not an inference input —  │
   │ │ (D5) unparsed/unrecognized counts │  │                                   │
   │ │ §7: beliefs DECAY — time-weighted │  │ which is the distinction the      │
   │ │ against last_seen, so a           │  │ old drawing of this map           │
   │ │ decommissioned host fades         │  │ collapsed                         │
   │ └───────────────────────────────────┘  └───────────────────────────────────┘
   │                   │                                  │  training input
   │                   │                                  ▼
   │ ┌──────────────────────────────────────────────────────────────────────────┐
   │ │ TRAINING / model fitting          CLOCK 2 — periodic / offline           │
   │ │ §6: "Training need not run where ingest runs — export features,          │
   │ │ train centrally, ship weights back; a MODEL REGISTRY records which       │
   │ │ build produced every assertion."  May be another host, another day.      │
   │ │                                                                          │
   │ │   reads   the feature archive (files), never the live stream             │
   └─│   emits   weights + model registry   ────────────────► (W)               │
 ┌──►│   needs   LABELS  ◄────────────────────────────────── (L)                │
 │   │                                                                          │
 │   │   also here: decay sweeps · multi-day diurnal fits ·                     │
 │   │              visibility-model estimation                                 │
 │   └──────────────────────────────────────────────────────────────────────────┘
 │
 │     the store's own path to the synthesizer, independent of all that:
 │                                         │  snapshot as-of T — consistent read of the store
 │                                         ▼
 │   ┌──────────────────────────────────────────────────────────────────────────┐
 │   │ ir/  →  synth/               deterministic compiler        BUILT        │
 │   │                                                                          │
 │   │ ! A NETWORK IR SITS BETWEEN snapshot() AND THIS BOX, AND IS BUILT.       │
 │   │   ratified 2026-08-14, built the same day, extended 08-17                │
 │   │   versioned (schema 1.2) · persisted · point-in-time.  THE               │
 │   │   PROJECT'S FIRST AND ONLY §0.2 SCHEMA RATIFICATION; everything          │
 │   │   else stays incubating (LES-001).   Four readers, not one:              │
 │   │      drift comparator · GNN eval · topology diagram · this               │
 │   │   snapshot() ALREADY returned an undesigned one — a dict that            │
 │   │   grew a key per phase (segments 08-14, roles 08-14).  The               │
 │   │   choice was designed-or-accidental, never schema-or-none                │
 │   │   (LES-112).                                                             │
 │   │                                                                          │
 │   │   materialize → PIT   merge --axis {vantage,time}   drift                │
 │   │   visibility  → coverage + the 2,418-row unseen census                   │
 │   │   cast        → the three documents + a provenance sidecar               │
 │   │                                                                          │
 │   │ ! A PIT IS ONE TAP POINT'S VIEW, and now says which (08-17).             │
 │   │   ir_pit.vantage comes from a manifest `harvest` writes beside           │
 │   │   the cold tier — metadata that used to be DISCARDED at the              │
 │   │   disk boundary, not something a model could recover (LES-116).          │
 │   │   drift refuses a mismatched network or vantage-set; merge is            │
 │   │   how two taps become one comparable view.                               │
 │   │                                                                          │
 │   │ ! THE TWIN HOLDS ROLE-BEARING NODES ONLY (D5).  197 placed               │
 │   │   hosts → 19 emitted, 38 segments → 9, everything dropped is             │
 │   │   disclosed and the sidecar's population block reconciles to             │
 │   │   197.  The IR keeps the COMPLETE view (D9) — filtering lives            │
 │   │   at compile time or drift starts measuring compiler policy.             │
 │   │ same snapshot → same YAML · per-field provenance:                        │
 │   │ evidence-backed vs model-inferred vs defaulted, carrying §7's            │
 │   │ evidence classes — "or the twin's fidelity claim is untestable"          │
 │   │                                                                          │
 │   │ ! the tfc half is GATED ON ROLES, not on fitted intensities:             │
 │   │   axRange's AssignmentSpec already takes intensity/schedule/             │
 │   │   seed AND ALL THREE ARE OPTIONAL.  Q29 closed 08-13.                    │
 │   └──────────────────────────────────────────────────────────────────────────┘
 │                                         │
 │                                         ▼
 │   ┌──────────────────────────────────────────────────────────────────────────┐
 │   │ twin_config   +   tfc traffic profile   +   tap plan                     │
 │   ├──────────────────────────────────────────────────────────────────────────┤
 │   │ axrange (sibling project) deploys the twin                               │
 │   └──────────────────────────────────────────────────────────────────────────┘
 │                                         │
 │                                         ▼
 │   ┌──────────────────────────────────────────────────────────────────────────┐
 │   │ §10  RECONCILIATION LOOP                          later phase            │
 │   │ deploy the synthesized config → capture the twin's traffic →             │
 │   │ feed a SECOND aximprint instance → measure drift → tune → repeat         │
 │   │                                                                          │
 │   │ "a feedback control loop with expensive evaluations, NOT backprop"       │
 │   │ — no ∂drift/∂config exists.  Compare in FEATURE space, never             │
 │   │ packet space.  Invariants: same build both sides, matched vantage.       │
 │   │                                                                          │
 └───│   ► roles are KNOWN BY CONSTRUCTION here — you wrote them.               │
     │     That is the label source §6 names for the GNN, alongside             │
     │     self-supervised contrastive pretraining (same host in                │
     │     adjacent windows = a positive pair).  NOT the deterministic          │
     │     extractors — those are peers, not a labeling stage.                  │
     │   ► residual drift is information: a persona-coverage gap report         │
     └──────────────────────────────────────────────────────────────────────────┘
     THE TWO FEEDBACK LOOPS, neither of which the earlier map showed
     (W)  WEIGHTS.   Training reads the ARCHIVE on clock 2 and ships weights
          back.  Inference then runs on clock 1 and writes assertions like
          any other annotator — that is what closes ML -> store -> synth.
     (L)  LABELS.    The twin's roles are known by construction, so §10 is
          not only drift measurement: it is the supervised label source.
          Labels do NOT come from the deterministic extractors.

     WHY ONE BOX WOULD BE WRONG.  "ML learners" run on two clocks in two
     places: offline TRAINING off the file archive, and per-window-close
     INFERENCE off the live WindowResult.  Drawing only the first and
     stopping is what made the layer look like a dead end.

     CLI   aximprint ingest -> store  ·  features -> files (no store)
           status  ·  synth  ·  acquire {streams, pcap, harvest}
           --plugin {zeek, axquery, suricata} · --sink NAME=DEST (repeatable)
           --source <dir>|parquet:<root>|axquery:<base-url>

     LEGEND  DONE = real   v0 / STUB = runs, placeholder semantics
             UNBUILT = designed in §6, no code   ! = known gap, named
             Q<n> = open question

     CLOSED SINCE THE FIRST DRAWING   Q1 Q2 Q6 Q9 Q18 Q24 Q27 Q29
     OPENED SINCE                     Q23 Q25 Q26 Q28 Q30 Q31
                                      Q32 Q33 Q34 Q35 Q36   (08-25, suricata)
                                      Q37                   (08-26, per-sensor gate policy)
```

## The correction: the ML layer is not a terminal box

The design of record is explicit, and the first drawing of this map contradicted it —
`DESIGN_BRIEF_aximprint.md` §6:

> All of them — heuristic and learned alike — are **annotators writing assertions into the belief
> store** (`role=db-tier, conf 0.87, evidence: gnn@window-14`), one uniform confidence scheme.

§2's pipeline table says the same in one line: *"Learners | deterministic extractors + ML
annotators (§6) | **both write assertions, never raw truth**."* And `learners/extractors.py`'s own
docstring already reserves the seat — *"ML annotators land later and use the same shape."*

So `learners/` and the ML layer are **peers writing into one store**, not stages of one pipeline.
Three consequences the old drawing hid:

1. **The loop closes through the store, not around it.** ML inference → assertions → belief store
   → snapshot as-of T → `synth`. There is no separate ML→synth path, and there does not need to
   be: the assertion shape and confidence scheme are already uniform.
2. **The deterministic/ML split is not labeler-vs-learner.** §6's rationale is narrower and
   sharper: *"Rule-shaped facts with rule-shaped evidence; **ML adds uncertainty where none is
   needed**."* A listening-service census is not a weak label for a model — it is a fact a model
   would degrade.
3. **Labels come from the twin, not from the extractors.** §6 names the source: *"labels from
   **twin-generated traffic where roles are known by construction**, plus self-supervised
   contrastive pretraining — same host in adjacent windows = positive pair."* That makes §10 do
   double duty: it is the drift-measurement loop *and* the only supervised label source the design
   names.

## What the first ML evaluation actually found (2026-08-19) — negative

The seat above is still reserved and the shape above is still right. What changed is that somebody
finally measured whether a model can *do* the first job, and the answer was no.

**Task:** infer host identity — link two addresses that are one machine — unsupervised, validated
against 421 true pairs from `entity_roster` with no identity leakage and no new dependencies.
**Result after three iterations:** AUC **0.741**, and **precision@1, @2, @3, @5 = 0.00**. The
highest-confidence predictions are all wrong, which is exactly where a fail-closed design acts.

**Root cause is evidence density, not model quality.** Of 131 addresses needing linkage, **41 have
zero** behaviour rows and the rest a **median of 3 windows** — the hosts concerned are
DHCP-ephemeral by nature, so a longer capture does not help. Meanwhile `radius` and `rdp`, two of
the eight stems this build does not parse, **state** MAC→address and computer-name outright.
**Parse what is declared before modelling it** (`LES-122`).

**Where the data IS dense, and nothing has been tried:** 1,641 entities observed ≥100 windows,
5,777 edges persisting ≥24 windows, 514 segment-placed hosts of which only **85 carry a role**.
Role discovery, segment inference, absence/link prediction and — the one most worth naming —
**`tfc` parameter learning** all sit in that dense region. `tfc` is a declared pipeline output that
has never been built, made of subject-less temporal facts, with no identity dependency at all.
Caveat that cuts the other way: identity was the task with *free* ground truth; the dense tasks
have little or none, so success criteria must be defined before building. Full record:
`docs/WORK_ORDER_ml_layer.md`.

## Why one ML box would still be wrong: two clocks, two inputs

§6's "three clocks" is the part the flattened drawing lost. The ML layer runs on two of them, in
two places, off two different inputs:

| clock | what runs | reads | writes |
|---|---|---|---|
| **per window close** | GNN **inference**, anomaly scoring, hourly refits — seconds of CPU | the live `WindowResult`, same as `learners/` | assertions, into the store |
| **periodic / offline** | model **retraining**, decay sweeps, multi-day diurnal fits, visibility-model estimation | the **feature archive** (files) | weights + a model registry |

> Training need not run where ingest runs — export features, train centrally, ship weights back; a
> model registry records which build produced every assertion.

The feature archive is therefore the **training corpus**, not an inference input. Drawing only
that edge and stopping is exactly what made the layer look like a dead end.

## The rest of the structure worth knowing

1. **The repo owns a capture path.** `acquire/` landed 2026-08-10 from the standalone `xtract`
   project, so *"aximprint never sees a packet"* — a claim about **packaging** — is dead. What
   replaced it is a **layering rule**: `acquire` must not import `events` (or `ingest`,
   `windowing`, `store`, `sinks`, `features`, `synth`). Enforced by an AST-walk test,
   mutation-proven — checked, not asserted.

2. **`sinks/` is the fan-out.** The driver (`cli._pipeline`) calls `learners.extract_assertions(win)`
   **once** and hands `(win, assertions)` to every registered sink. `learners/` sits in-line above
   the fork, not beside `features/`.

3. **`ingest/` separates transport from semantics — now three times.** `<sensor>/sources.py`
   locates, fetches and decodes; `<sensor>/plugin.py` owns the parser/endpoint table, ordering and
   merge. Three Zeek transports share every Zeek parser, and the tier-2 gate demands byte-identical
   feature files across all three. Since 2026-08-25 a **fourth** gate leg varies the *sensor* instead
   of the transport and demands **structural** equivalence, which is the most two sensors can be —
   and the finding that built it is the one worth carrying: the gate passed while **both** sensors
   were broken identically, because they differ only above `events/`, and `windowing/` is one body
   of code serving all three. Agreement is half a gate; every axis is now anchored to a seeded plan
   (`LES-123`). **That gate covers two of the three sensors.** `axquery` reads a different plane, so
   no single seeded input renders into both it and Zeek, and it has no automated cross-sensor check —
   only a hand-tabulated twin comparison from 2026-08-20 on captures 49.6 days apart. The cost is
   already measurable (`Q34`, closed 2026-08-26 by deleting both counters: axquery published a
   confident `s0_count: 0` for five days before a different sensor's gate exposed the class), and
   whether every sensor owes a gate is `Q37`.

4. **Two layers carried the same deficiency from opposite ends; both are now fixed, and one
   third of a third gap remains.** `learners/` used to emit `evidence_class="observed"` and nothing
   else — `referenced` and `imputed` existed in the assertion shape with **no code emitting them**.
   `synth/`'s `tfc` half used to have nowhere to write at all, because every path into the store was
   keyed on an entity and a traffic profile is made of subject-less facts — that pair was `Q1` and
   `Q29`: a subject with no address, and a fact with no subject. **`Q29` closed 2026-08-13**
   (`profile_facts` gives `tfc` a channel) and **`Q1` closed 2026-08-14** (the resolver gives an
   address a subject), which also gave **`referenced` its first producer**: a DNS-derived binding
   is now `referenced` at the source, from the binding's own scope. `synth/` still has nothing to
   *compile* — the `tfc` compiler and the learner layer that would fit its parameters are unbuilt —
   but the store shapes it would read from now exist. **`imputed` got its first producer 2026-08-14**
   (synth v1 phase 1): when a sensor declares no `Site::local_nets`, segment membership falls back to
   an RFC1918 inference and every `in_segment` assertion built on it is `imputed`
   (`windowing/segments.py::SEGMENT_EVIDENCE`). So all three classes now have code paths — though
   note what that does and does not mean: the real baseline's Zeek *does* declare `local_nets`, so
   every one of its 787 segment rows is `observed` and the imputed path is exercised only by tests.
   The class the ML layer would fill in volume (§6 names link prediction for imputing unobserved
   edges) still has no producer.

5. **Placeholders are labeled as placeholders, in code.** ~~`entity == ip` (`Q1`)~~ — **that one
   is gone as of 2026-08-14**; ~~a `synth` stub~~ — **gone too**: `synth/compiler.py` compiles a
   real PIT and axRange's own expander accepts the result (`synth/synthesizer.py` remains as the
   superseded v0 draft path, still what tier 2 pins). What remains is exact sketches that are
   O(input) rather than O(output) (`Q8`) and an untested long-tail spill (`Q11` — now at least
   *visible*, since the entity roster declares `__longtail__` as a mint path with a spill count).
   Every remaining v0 placeholder says so in its own docstring.

6. **The blind spot is now measured rather than assumed** (2026-08-17). `ir_unseen` carries the
   hosts a vantage knows exist and never saw: **2,418** on the baseline, **9** of them not on the
   internet, and **0** inside a segment this tap observed — the nine are IPv6 link-local mDNS names
   and `ipv4only.arpa`. On a near-full-visibility tap the estate blind spot is genuinely empty,
   which is a stronger statement than a small number and is only visible because `locality` and
   `segment` answer different questions. On the partial vantage the visibility model is *for*, that
   column is the whole point. §8's tap-placement recommendation is still unbuilt; its inputs are
   not.

## What is proposed to change (2026-08-19) — drafted, unratified, NOT built

Three work orders came out of the ten-day run. **None is ratified and none is started**, so the
diagram above is still accurate as a description of what exists. One of them, if ratified, moves a
boundary this map draws.

| work order | what it proposes | status |
|---|---|---|
| `docs/WORK_ORDER_zeek_stems_wide_vantage.md` | parse the **8** stems a twelve-stream vantage produces and this build reports as `unrecognized`: `known_certs`, `syslog`, `rdp`, `radius`, `rfb`, `mqtt_connect`, `redis`, `websocket`. `radius` needs the first **per-field** identity scope (`mac`/`framed_addr` host, `username` user) | D1–D3 need a ruling |
| `docs/WORK_ORDER_evidence_plane_snapshots.md` | **move the PIT snapshot boundary from the conclusions plane to the evidence plane.** Snapshot evidence, compose, *then* resolve — because resolution needs evidence and `merge` only has conclusions | D1–D5 need a ruling |
| `docs/WORK_ORDER_ml_layer.md` | inference is a **`source` in the evidence plane**, not a new plane; three roles (resolver / rival predictor / judge); determinism tuple gains model identity; `compare()` gains a model gate. Carries the negative evaluation above | D1–D6 need a ruling, D6 blocking |

**The measurement behind the second one**, because it is the one that changes this map's shape:
host-scoped identity evidence is rare and bursty — **105** named entities over ten days, named in a
median of **2** windows while active in a median of **124**, a **48×** gap. Slice into per-window
PITs and the name is stranded in ~2 slices while the host appears in ~122 others as a bare address;
`merge` unions rows that were already keyed and cannot re-key them (`LES-118`). `BeliefStore.snapshot()`
is already the exact split point — its docstring says *"Entity merges are applied HERE."*
Corollary from the same capture: **22 addresses are claimed by more than one entity, one address
served 12 different hosts.**

**Authority note:** the boundary being questioned was ratified by the user on 2026-08-14
(`docs/WORK_ORDER_network_ir.md` D1–D9). Reversing it is a CHANGELOG entry with a
`**Supersedes:**` marker, not a quiet refactor — and the schema, `cast`, `drift`, `visibility` and
`merge --axis vantage` are **not** in scope.

## What drifts here, and against what

`doc-stats.py` scans **only `CLAUDE.md` and `README.md`**, and only for three claim patterns
(`N pytest tests`, `N ingest plugins`, `N recorded lessons`). **Nothing in this file is
machine-policed.** So each volatile claim names its own authoritative source:

| claim on the diagram | authority | policed? |
|---|---|---|
| the **reference-ingest** kind and its own registry (`ingest/reference/`, 2026-09-05) — non-sensor, snapshot-shaped, deliberately NOT in `ingest/base.py::_REGISTRY`, so the "3 ingest plugins" claim above still means *sensors* and nothing else. Its facts enter the store through the same `(subject, predicate, object, evidence_class, confidence, source)` shape every learner writes, at `evidence_class: asserted` — a THIRD origin for "ONE shape, ONE scheme, either origin" above, not a third scheme | `src/aximprint/ingest/reference/`, `src/aximprint/operator_assertions.py`, `docs/WORK_ORDER_reference_ingest.md` S2 | partly — `tests/test_ir_schema.py`'s closed-set guard polices the file's schema string, and `tests/test_acquire_layering.py` polices the module's layering classification; nothing polices this row's prose |
| `36 log types parsed · 7 skipped` (zeek), `18 parsed · 25 declined = 43` (suricata), `5 endpoints · 3 declined` (axquery) | `_PARSERS` / `_SKIP` in `ingest/zeek/plugin.py` and `ingest/suricata/plugin.py`, the endpoint table in `ingest/axquery/plugin.py` (all built, none a target; zeek as of the wide-vantage stems 2026-08-20, suricata as of 2026-08-25 — and the zeek claim is **vantage-scoped**, `LES-120`: 35 = 28 + 7 was one tap, 43 = 36 + 7 is a twelve-stream one) | no — and `tests/test_acquire_zeek_stem_sync.py` only checks the two *stem lists* agree with each other, never that prose agrees with either |
| the network IR: ratified, unbuilt, four readers | `docs/WORK_ORDER_network_ir.md` (design + D1–D9, unratified proposals) and DESIGN_BRIEF §0.2's 2026-08-14 amendment (the ratification itself, and its scope) | no — and the claim most likely to be MISREAD rather than to go stale: "one schema is versioned" is not "versioning has begun" |
| `segments.py` DONE; `catalog/` DONE; `imputed` has a producer; `tfc` gated on roles | `windowing/segments.py`, `docs/WORK_ORDER_synth_v1.md` "Phase 1 RESULT" — and, for the `tfc` claim, `axrange/o2control/traffic.py`'s `AssignmentSpec` **in the sibling repo**, which is the one authority on this map that nothing here can even see, let alone police | no |
| `profile_facts` / `Q29` closed | `store/belief_store.py`, `docs/WORK_ORDER_subject_less_facts.md` | no |
| `unparsed_log_types`/`unrecognized_log_types` (health + `runs`) | `ingest/base.py`, `windowing/windows.py`, `store/belief_store.py` — D5, `docs/WORK_ORDER_zeek_stem_coverage.md` build step 4 | no |
| `acquire/` = 13 modules + `harvest/`'s 4 | `src/aximprint/acquire/` | no. (`CLAUDE.md` says "17 modules + `harvest/`", which reads as 17 *plus* harvest and is 17 *including* it) |
| `1 h × 12 sub-buckets`, `top-64 peers`, `MAX_KEYS=512` | `windowing/windows.py`, `features/vocabulary.py` | no |
| `DONE` / `v0` / `STUB` / `UNBUILT` per layer | the layer's own module docstring; `UNBUILT` means designed in §6 with no code | no |
| the two loops and the clock split | `DESIGN_BRIEF_aximprint.md` §6, §10 | no — and this is the claim the first drawing got wrong |
| open-question numbers | `docs/OPEN_QUESTIONS.md` | no |
| the ten-day run's numbers (6.17 TB, 58,563 entities, 430 placed, 85 roled, 8 unrecognized stems) | the run's own logs under `/home/rocky/harvest-corpapcn-10d/` and `ir.duckdb` — **outside the repo** (`LES-003`), so unverifiable from a clone | no — and these go stale the moment anything is re-ingested |
| the ML evaluation (AUC 0.741, precision@5 = 0) | `docs/WORK_ORDER_ml_layer.md` §4; prototype scripts were scratchpad-only and are **not** committed — the numbers are the artifact | no |
| the three proposed work orders | each work order's own STATUS header; **`CHANGELOG.md` is the authority on whether any has been ratified** | no — and the claim most likely to be misread as *decided* when it is only *drafted* |

**When editing the ASCII:** full-width box interiors are 74 columns, half-width 35, boxes start at
column 5. **Columns 0–4 are a reserved gutter** carrying the two feedback loops — `(W)` at column 3,
`(L)` at column 1. They are vertically disjoint on purpose, so neither crosses the other; if you
reorder the boxes so their spans overlap, they will.

**The counter most likely to be wrong when you read this** is the parsed/skipped pair — it moved
three times in three days (16 → 17 → 24/24 union → 28/35), then again to 36/43 on 2026-08-20 with a
wider vantage, and prose counters for it went stale in five separate places across two rounds before
build step 5 (`docs/WORK_ORDER_zeek_stem_coverage.md`) finally caught up with itself, twice.

**The claim most likely to be wrong in a different way** is the *sensor count* and every ordinal
attached to it. `doc-stats.py` polices `N ingest plugins` in `CLAUDE.md` and `README.md` and nothing
else — an English ordinal ("the second sensor") is invisible to it, and a doc branch written before
a sibling plugin merges will carry a whole set of them (`LES-130`).
