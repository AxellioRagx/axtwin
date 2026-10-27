# Design Brief — aximprint: learn a network's imprint from sensor metadata, cast it as a twin

> **Design of record for the aximprint project.** Authority: user (2026-08-02, chat session).
> Companion project to **axrange** (see `docs/DESIGN_BRIEF_twin_model.md` there). aximprint is an
> **independent project**: it consumes sensor metadata and produces twin-deployable configuration;
> axrange consumes that configuration and stands up the twin. Read alongside `docs/CONVENTIONS.md`
> here (workflow, imported from axrange with deliberate deltas — see CONVENTIONS §1).

## 0. Two framing decisions (ratified up front; §0.1 amended 2026-08-10, §0.2 amended 2026-08-14)

1. **The discovery pipeline is NOT a sensor, and never sees a packet.** Sensors (Zeek, Suricata,
   packetXpress, …) consume captured pcap and generate metadata; the pipeline consumes that
   metadata through a **per-sensor ingest plugin** that normalizes it into aximprint's internal
   event model. First plugin: **zeek**. The capture layer, NIC tuning, gretap termination, dedup,
   and drop accounting from the design discussion are the *sensor's* problem; the pipeline only
   requires that the plugin surface what the sensor knows about its own health (drops, gaps) so
   window completeness stays honest.

   **Amended 2026-08-10 (user).** As ratified 2026-08-02 this read *"aximprint is NOT a sensor …
   owns no capture path and never sees a packet"* — a claim about the **repository**. The
   acquisition tool `xtract` (`../xapi-pcap-extract`), which pulls pcap off an Axellio Xpress
   appliance over `axapi` and runs a pinned Zeek over it, is merging into this repo as a sibling
   package. At the repo level the old claim becomes false: this project will own a capture path and
   will handle packets.

   **The property that mattered is unchanged — and is now enforced instead of assumed.** What made
   the original decision load-bearing was never the repo boundary; it was that everything from
   `ingest` downward speaks the event model and nothing else. That was guaranteed by an accident of
   packaging — the capture code lived elsewhere — and an accident of packaging evaporates the moment
   the code arrives. Restated as a layering rule it is checkable, and CONVENTIONS §3 already checks
   it:

   - **`acquire/`** owns the capture path — appliance extraction, the pinned Zeek runner, the spool
     and disk governor. It produces **sensor artifacts on disk** and imports nothing from `events`.
   - **`ingest/`** assigns meaning to those artifacts. It imports `events` and knows nothing about
     how they were acquired.
   - The CLI wires the two. **Neither imports the other.**

   **`acquire` must not import `events`.** That one rule is what the old sentence was really
   protecting, and it is why a line-rate metadata source can arrive later as a new `acquire` path
   plus a new parser table, with nothing between them renegotiated. The seam did not disappear when
   the repos merged; it moved from a repo boundary to a package boundary, and got a check.
2. **No schema is versioned yet** *(as ratified 2026-08-02; **amended 2026-08-14 — exactly one now
   is**, see the amendment at the end of this item. The original text is kept intact below because
   its reasoning still governs everything except the network IR).* The event model, feature vectors,
   belief-store shapes, and plugin contract are all **incubating**. Experimental changes are made freely without version bumps.
   Versioning is ratified later, by the user, when results are satisfactory — at that point the
   schema-evolution machinery recorded in §11 becomes binding. Until then §11 is *design capture*,
   not policy. Do not add version fields, bump ceremonies, or compatibility shims prematurely.

   §0.1's amendment strengthens this rather than weakening it. The one force pushing toward early
   ratification was an artifact shape whose **producer lived outside the repo** and could drift
   independently of its reader (`Q15`). With the producer inside, a mismatch is a build error rather
   than a runtime surprise, and there is nobody left to version *for*. Note also that what survives
   longest on disk — the cold tier — is **sensor-native**, not aximprint-shaped, so it is immune to
   event-model churn by construction.

   **Amended 2026-08-14 (user) — the first ratification, and it is exactly one.** The **network IR**
   (`docs/WORK_ORDER_network_ir.md`) is ratified as a versioned schema: a persisted, point-in-time
   structural representation of the monitored network, read by a drift comparator, a topology-diagram
   generator, a GNN evaluation harness, and the twin compiler. §11 becomes **binding for the IR** and
   remains *design capture* for everything else.

   **Everything in the paragraph above still holds for the event model, feature vectors, belief-store
   shapes and the plugin contract.** They stay incubating and change freely, and the reasoning is the
   same one that always applied — it just now has a name for its boundary. **A schema is versioned
   when, and only when, something outside its producer depends on its shape.** The belief store is
   read by the IR materializer and nothing else, both in this repo, both moving together; a mismatch
   is a build error, and `BeliefStore.__init__`'s schema guard already makes it a loud one. The IR is
   read by four consumers with independent lifecycles, at least one of which will be re-read months
   after it was written — that is the condition, and it is now met for one artifact and no others.

   The failure mode to guard against is reading this amendment as a *phase change* rather than as a
   boundary being crossed by one artifact. It is not a step toward versioning the rest, and the
   asymmetry is permanent by design (`LES-001`, `LES-112`).

## 1. What aximprint is

**Goal:** ingest continuous network metadata (derived from pcap streams, a day or more), **discover
the network** — hosts, topology, services, endpoint behavior — and persist that knowledge so a
separate utility can **generate the configuration to deploy a twin** representing the learned
structure and behavior *as of a chosen point in time*.

In the axrange twin pipeline this is the missing front door: today `twin_config`s are hand-authored;
aximprint derives one from a real network's traffic, plus the traffic profile (`tfc`) that makes the
twin *behave* like the original, plus a tap plan reflecting the capture vantage.

```
sensor (zeek/suricata/packetXpress) → [ingest plugin] → normalize → window/features
      → learners → belief store (DB) ── snapshot as-of T ──> synthesizer util
      → twin_config + tfc profile + tap plan  ──> axrange deploys the twin
```

Central metaphor: the network leaves an **imprint** in its traffic; aximprint takes the imprint;
the synthesizer **casts** it into a runnable replica.

## 2. Pipeline overview

| Stage | Owns | Key property |
|---|---|---|
| Ingest plugin | one per sensor type; parses that sensor's output into normalized events | thin, stateless, replaceable |
| Event model | four event classes all plugins emit (§4) | sensor-agnostic; the contract everything downstream sees |
| Windowing + features | time-bucketing, entity resolution, statistical compression (§5) | bounded memory at any rate; idempotent window close |
| Learners | deterministic extractors + ML annotators (§6) | both write *assertions*, never raw truth |
| Belief store | append-only evidence log + queryable projection (§7) | event-sourced; re-derivable; decays |
| Snapshot | consistent as-of-T read of the belief store | what the synthesizer consumes |
| Synthesizer util | compiles a snapshot into twin_config + tfc + tap YAML | deterministic compiler; per-field provenance |

Progressive compression is the design spine: events → windowed features → beliefs, each stage
dropping what the next doesn't need. **Never a packet database** — nothing at or below the ingest
plugin ever sees a packet (§0.1). Acquisition handles pcap and discards it; what crosses into this
table is metadata.

## 3. Ingest plugins

A plugin adapts one sensor's output format to the normalized event model. Contract (incubating):

- `name` — plugin id (`zeek`, `suricata`, `packetxpress`).
- `events(source, ts_from=None, ts_to=None) -> iterator[Event]` — the CLI driver
  (`cli._pipeline`) always calls with all three positional arguments; `ts_from`/`ts_to` bound a
  half-open `[ts_from, ts_to)` slice in epoch seconds, `None` meaning unbounded on that side, and a
  plugin that cannot slice by time may accept and ignore both (2026-08-06,
  `WORK_ORDER_egress_sink_unification.md`). Widened from `events(source)` to match the zeek plugin's
  own signature, which already carried it — parse the sensor's artifacts (log directory, JSON stream,
  socket, …) and yield normalized events in timestamp order (best effort; the window layer tolerates
  bounded disorder). **"Bounded" is a number a plugin owes, not a hope**: a sensor log is generally
  *not* internally ts-sorted (Zeek keys a row on connection start and writes it at close), so the
  plugin measures its own worst displacement and states the bound in **seconds**. Residual disorder
  that exceeds the bound is surfaced as a `SensorHealthEvent`, never swallowed (2026-08-03,
  `LES-004`).
- Surface sensor health where the sensor exposes it (Zeek `capture_loss.log`, stats) as
  `SensorHealthEvent`s so the window-health record can inherit drop/gap information.
- Plugins are *lossy by design*: they map what the sensor provides onto the event model and drop the
  rest. Enriching the event model to carry more is an event-model change, not a plugin hack.

**Transport is separated from semantics** (2026-08-05). A plugin's `source` locator selects a
*transport* — where records come from and how bytes become rows — while the parser table, the
ordering guarantee, and the merge are shared by all of them. Adding a transport costs no parser
change; fixing a parser costs no transport change. For zeek: `<dir>` reads text logs — either a
flat directory (one stream per file) or a tree of per-window log directories (one subdirectory per
pcap window, auto-detected, merged per log type on each row's own `ts`, never on directory name,
2026-08-10) — and `parquet:<root>` reads the metadata archive.

**pcap extraction and the Zeek run are a separate, run-once step outside this project.** They are
expensive; aximprint re-reads their output constantly while extractors and features are iterated on.
So the metadata is persisted in two tiers, mirroring §7's own evidence-log/projection split one layer
earlier: Zeek's native output as immutable **cold** evidence, and a Parquet **hot** tier that is
regenerated from it whenever the layout improves — without ever touching a pcap again.
`docs/SPEC_zeek_metadata_archive.md` is the contract. Because its producer lives *outside* this repo,
the reader validates rather than assumes: a missing required column, an undeclared file, or an
archive that never measured its own pre-sort disorder is refused, not read as a smaller valid
capture (`Q15` asks whether that boundary eventually wants versioning).

**zeek plugin (first).** Zeek's `conn/dns/dhcp/kerberos/ssl/x509/smb/ntp` logs already carry ~80% of
the discoverability matrix (§6.1) in structured form — the reason it's the first-class citizen.
Parses both TSV (`#fields` header) and JSON-lines formats; `conn.log` → FlowEvents;
`dns/dhcp/kerberos/ntlm` → IdentityEvents; `ssl/x509/smb/http` → ServiceEvents; `notice/weird` →
AlertEvents (best-effort). Suricata (EVE JSON → mostly AlertEvents + some flow) and packetXpress
plugins follow the same contract later.

## 4. Event model — four classes, three consumption modes

All plugins normalize into (fields indicative, incubating):

1. **FlowEvent** (~95% of volume) — 5-tuple, direction/originator, bytes/packets both ways,
   duration, start ts, transport state (attempted `S0` vs answered-refused `REJ` vs established
   `SF` — the scanner-vs-real-host discriminator), L7 protocol label. *Consumed by accumulators
   only; never stored individually.*
2. **IdentityEvent** — address-binding facts: DHCP lease (mac↔ip, lease interval), ARP, Kerberos
   principal↔ip, DNS name↔ip, NetBIOS/mDNS names. *Consumed statefully by the entity resolver.*
3. **ServiceEvent** — service/metadata facts: TLS cert CN/SAN/issuer, JA3, SMB shares/dialect,
   HTTP banners, SNMP sysDescr. *Consumed as facts by extractors; a few land in features.*
4. **AlertEvent** — IOC/scanner/anomaly flags from the sensor. *Consumed as contamination markers:
   annotate windows (`tainted`) so benign models can exclude them; never change accumulation.*

Plus **SensorHealthEvent** (drops, gaps, restarts) and the implicit clock (window boundaries).

## 5. Windowing + feature layer

The pivot of the system: everything above speaks *addresses and events*, everything below speaks
*entities and features*. Three simultaneous transformations: time-bucketing (tunable window, 1 h
default), identity-folding (addresses → stable host entities), statistical compression (streams →
fixed-size vectors).

### 5.1 Entity resolution first, keying second
Every flow's addresses pass through the resolver *before* touching an accumulator, so statistics
accrue to `ent-0043`, not `10.62.0.21`. Bindings are interval-valued —
`(addr, entity, valid_from, valid_to, evidence, confidence)` — because over a day-plus capture host
identity churns (DHCP reassignment, Wi-Fi roaming, MAC randomization, IPv6 privacy addresses, VPN
pools). This is the core algorithmic problem and it gets *worse* as capture length buys coverage.
Evidence: DHCP client-ids, hostnames, Kerberos principals, and (later) behavioral-embedding
similarity. ~~v0 ships with a trivial resolver (entity = ip) explicitly marked as placeholder.~~
**Superseded 2026-08-14 (`Q1`, `docs/WORK_ORDER_entity_resolution.md`): the real resolver is
built.** Two things this section said need correcting rather than merely updating.

**Evidence is CLASSIFIED before it is weighted.** "Evidence weighting" is the wrong first move:
`windowing.entities.IDENTITY_SCOPES` sorts every identity kind into what it can *anchor* — `host`
(a mac, a computer name), `user` (a Kerberos user principal: a person, not a machine), or
`referenced` (a DNS answer: a third party's address, many-to-many) — and only host-scoped evidence
may ever merge two addresses. No weight makes a user principal or a DNS answer safe to merge on; a
resolver that unioned on shared identifiers would fold a CDN's A records into one entity the first
time a round-robin answer arrived. A kind outside the table anchors nothing and is counted.
Because different sensors name one host in different formats, the resolver keys on the **parsed**
computer name rather than the raw string, and the two are both kept.

**"Late evidence can retroactively strengthen bindings" is now qualified: it strengthens them
FORWARD.** Retroactive rewriting conflicts with §5.4's requirement that window close be a pure
function of (events, resolver state, code build), and would re-spend the streaming win `Q2` bought.
A merge learned in window 40 is emitted as a `same_entity_as` assertion and applied by the snapshot
reader at read time — bounded by the same `as_of`, so a snapshot never uses evidence that postdates
it. Windows 1–39 keep the ids they closed with. Event-sourcing (§7) is still what makes this work;
it is what lets the join be recorded rather than performed in place.

### 5.2 Sketch bank — per (entity, window)
Fixed-size bundle of streaming estimators: distinct-count (peers in/out, ports) — HyperLogLog class;
quantiles (flow sizes, durations, inter-arrivals, packet sizes) — t-digest class; heavy hitters
(bytes per proto, flows per port) — Count-Min/top-K; scalar counters (flows initiated vs accepted,
broadcast) plus a **per-class transport-outcome census** over the normalized `FLOW_STATE_CLASSES`
(`state_mix`). Memory = entities × bundle size (few KB each) — bounded at any line rate.
v0 uses exact stand-ins (sets/reservoirs) behind the same API; swap for probabilistic
implementations when scale demands, without changing consumers. **"Bounded at any line rate"
describes the probabilistic target, not the stand-ins** — the exact quantile stand-in retains every
value (3 per flow per window), so it is O(input) where this paragraph promises O(output). Peak
accumulator memory is unbounded until the swap; top-K eviction at close bounds *output*, not memory
(`Q8`, `LES-010`).

**Wording amended 2026-08-26 (implementation), and flagged for the user's reversal.** This paragraph
and §5.4 #1 used to name `S0/REJ/SF` scalar hygiene counters. The code only ever built **two** of
those three, only for Zeek, keyed on that sensor's verbatim state strings, so on any other sensor
they published a confident `0` — deleted, `Q34`. `state_mix` names the capability that replaced them:
**all eight** normalized classes, on **all three** sensors, over a closed vocabulary
(`features/vocabulary.py`), so `attempted`/`refused`/`established` are guaranteed columns and an
absent class is a proven zero. The amendment is made under the reading that hygiene counting is
**subsumed, not withdrawn**. If §5.2 was instead a commitment to *sensor-native* hygiene counters —
Zeek's `S0` as distinct from `SH`/`RSTOS0` — then this is a scope change and the wording should be
reverted; `Q34` records the shape that would honor it (a per-sensor `state_raw_mix`, declared not
cross-sensor comparable).

### 5.3 Sub-buckets and edges
Each window nests fine sub-buckets (12×5 min inside 1 h; cheap scalars only) preserving burstiness
and within-window variance — some behaviors are invisible at exactly one timescale. Edge
accumulators per (entity-pair, protocol): bytes, flows, direction ratio, first/last seen; bounded by
top-K eviction per entity (heaviest ~64 peers exact, tail spills to an aggregate + cardinality
sketch) — the monitoring-collector-talks-to-everything protection.

### 5.4 Outputs per window close (five artifacts)
1. **Node feature vectors** — per active entity: volume/orientation (flows/bytes in/out,
   client_ratio), cardinalities, distribution quantiles, protocol mix, listen-port profile,
   sub-bucket series + burstiness, hygiene (`state_mix`, tainted flag), OS hints (TTL mode, SMB
   dialect). The GNN's node input *and* the behavioral fitter's observation — designed once,
   consumed by both.
2. **Edge feature records** — the typed conversation graph for the window.
3. **Identity map delta** — new/changed/expired bindings with evidence + intervals.
4. **Deterministic assertions** — extractor facts, emitted as they occur (not at close).
5. **Window health record** — event counts, inherited sensor drop %, load-shedding state,
   entity-resolution ambiguity rate. One per window; makes every downstream number interpretable —
   a feature vector from a 40%-drop window is data with an asterisk, and the asterisk travels.

**Idempotent close:** a window's outputs are a pure function of (events, resolver state, code
build). Replay the same events through the same build → identical features. This is what makes
reprocessing and the round-trip self-test honest.

**Delivered vs. designed** (2026-08-03, once the artifacts existed as files — CHANGELOG "Pluggable
feature-vector sink"): a node row is 37 fixed floats plus a state mix, and artifacts 1–2 and 4–5
are as specified. Three gaps stand open rather than being quietly re-specified. (a) #1's **OS hints
(TTL mode, SMB dialect) have no field**, and §5.2's packet-size quantiles are not accumulated —
`Q7`. (b) #3 is emitted as a **restatement, not a delta** (a record per observation rather than per
change) — `Q9`. (c) ~~The categorical features have no pinned index map~~ **closed 2026-08-04**
(CHANGELOG "Pinned categorical vocabulary in the feature manifest", `Q6`): artifact #1's
`proto_mix` and listen-port profile now publish their index map in `manifest.json`, so a consumer
assembles its first tensor from the manifest alone and two captures can be pinned to the same
columns. #5's "the asterisk travels" holds and is now testable: a health record must distinguish
*no evidence* from *evidence of none*, and evidence is attributed to the window it describes rather
than the window that was open when it arrived (`LES-006`).

**The asterisk travels on the vocabulary too.** A pinned map is an open-world encoding — unseen keys
are the normal case, not the error case — so the manifest reports, per field, the coverage the map
achieved in **mass and in rows**, what fell outside it, and whether the observer's own bound
truncated anything. Truncated mass never counts toward coverage: an index that reports itself
complete while having refused keys is the vocabulary-shaped version of a 40 %-drop window with no
asterisk (`LES-014`). Two shapes are still open at the source of the keys rather than at the
persistence boundary — the listen-port key is a `(port, transport)` tuple that was string-joined
(`Q12`, `LES-016`), and `proto_mix`'s composite service keys fragment the space in a way that cannot
be split without deciding what a *value* means.

## 6. Learner layer — model selection (rationale from design discussion)

The problem decomposes into sub-problems with different natural model families, plus a chunk that
must *not* be ML:

- **Host role / persona classification → inductive GNN (GraphSAGE-style).** Roles are relationally
  defined — a DC *is* "many workstation-shaped neighbors sending Kerberos/LDAP"; aggregation over
  sampled neighborhoods is the right inductive bias, and 2-hop reach captures "talks to something
  DC-shaped." Inductive is non-negotiable: every capture is a new graph with new hosts; train once
  (labels from twin-generated traffic where roles are known by construction, plus self-supervised
  contrastive pretraining — same host in adjacent windows = positive pair), embed any future host in
  a forward pass. Neighbor *sampling* handles hub degree skew. Train with edge-dropout /
  neighborhood-masking so classification degrades gracefully under partial visibility. Temporal
  behavior via the pragmatic hybrid: per-window embeddings + GRU/transformer over the window
  sequence (full temporal GNNs deferred). Embeddings double as: behavioral fingerprints for entity
  resolution; out-of-catalog detection (far from every persona centroid = persona-coverage gap);
  link prediction for imputing unobserved edges.
- **Endpoint behavior → interpretable statistical fitters.** Hawkes/point processes for
  self-exciting flow arrivals; Fourier/seasonal decomposition for diurnal-weekly shape; Markov
  models over protocol states; parametric distribution fits from the quantile sketches. Deliberately
  *not* deep generative traffic models: the synthesizer needs interpretable parameters to write into
  persona knobs (`cadence`, `volume`, `intensity`, `schedule`), not an opaque sampler.
- **Anomaly/attack separation → cheap outlier models** (isolation forest / autoencoder) over the
  same features, so scanners and beaconing don't pollute the benign model; feeds the tainted flag
  and, downstream, the exercise tier's seed material.
- **Deterministic extractors — NOT ML.** Subnet/CIDR + gateway inference (DHCP offers, RAs,
  many-IPs-behind-one-MAC, ARP scoping, TTL binning), domain/realm facts, cert-derived naming,
  listening-service census. Rule-shaped facts with rule-shaped evidence; ML adds uncertainty where
  none is needed.

All of them — heuristic and learned alike — are **annotators writing assertions into the belief
store** (`role=db-tier, conf 0.87, evidence: gnn@window-14`), one uniform confidence scheme.

**Three clocks:** continuous (sketch/resolver updates, immediate deterministic assertions); per
window close (feature finalize, GNN *inference*, anomaly scoring, hourly refits — seconds of CPU);
periodic/offline (model *retraining*, decay sweeps, multi-day diurnal fits, visibility-model
estimation). Training need not run where ingest runs — export features, train centrally, ship
weights back; a model registry records which build produced every assertion.

## 7. Belief store

- **Event-sourced:** append-only evidence log (JSONL/Parquet) is the source of truth; the queryable
  **evidence graph** (DuckDB: nodes, edges, assertions, windows) is a rebuildable projection. Buys
  crash recovery, re-derivation of old captures with improved extractors, per-belief provenance.
- **Assertion shape:** `(subject, predicate, object, evidence_class, confidence, first_seen,
  last_seen, count, source)` where `evidence_class ∈ {observed, referenced, imputed}` — never let
  imputation masquerade as observation; the class propagates into synthesized-config provenance.
- **Principled forgetting:** beliefs decay (time-weighting against `last_seen`) so decommissioned
  hosts fade. Learning forever includes forgetting appropriately.
- **Forensic completeness (ratified 2026-08-12, user — supersedes the earlier "Privacy" bullet):**
  the store records **what was observed, in full**. Real captures carry cleartext credentials,
  filenames, usernames, mail recipients, SIP extensions and directory names, and **all of it is
  collected.** aximprint does not redact, sample, or decline a field on disclosure grounds; it is an
  evidence collector, and the disclosure decision belongs to the consumer (**axrange**), which knows
  what its output is for and who will see it. Redaction at collection time is not a neutral act — a
  field dropped for disclosure reasons is **indistinguishable downstream from a field that was never
  observed**, which is precisely the confusion §7's `evidence_class` exists to prevent. A collector
  that silently withholds evidence cannot make this section's honesty claim. Bounding still applies
  and is a **different** concern: cardinality limits (§5.2, §5.4) are store economics, not
  disclosure, and they are always disclosed when they bite.

## 8. Topology recovery, visibility, and provenance

- **Three graphs, different discoverability:** conversation graph (nearly free), L3 structure
  (inferable with work), L2/physical fabric (mostly invisible; best-effort opportunistic). v1
  promises L3 + service-dependency with confidence scoring.
- **Full-visibility vantage** (core SPAN): conversation graph ~complete; MAC-clustering partitions
  segments spectacularly (every remote host wears its gateway's MAC); TTL bins order segments;
  role embeddings give the partition *meaning* (`segments[].purpose`), catch middleboxes as
  embedding outliers (NAT/proxy/VIP), and role-pair flow matrices become `conduits[].policy`
  allow-floors. Deny rules are structurally unrecoverable; silent hosts don't exist in a pcap.
- **Partial visibility** inverts the problem: estimate the blind spots *first*. Half-flows,
  referenced-but-unseen hosts (DNS answers, SPNs, redirects, XFF), and TTL inconsistencies yield a
  per-segment **visibility model** (observed fraction). The GNN becomes an imputation engine
  (robust-by-sampling, trained with masking; link prediction fills expected edges; role composition
  + referenced-host census extrapolates counts). Structural priors (the twin-config catalog is a
  prior over network shapes) complete what evidence constrains — with `imputed` provenance. The
  visibility model is an *actionable output*: a tap-placement recommendation in axrange's EW/NS
  vocabulary.
- **Discoverability matrix:** the full signal→confidence→twin-field table lives in axrange
  `docs/DISCUSSION_pcap_discovery.md` §3 and is incorporated by reference (confidence is
  conditional on vantage and cleartext; score per fact-instance, not per category).

## 9. Synthesizer utility

A separate util (same repo, `aximprint synth`): takes a **consistent snapshot** of the belief store
as-of time T and compiles it into axrange tier YAML — `twin_config` (structure), `tfc` traffic
profile (behavior: fitted intensities/schedules/seeds land directly in `assignments[]`), `tap` plan
(vantage). Rules: **deterministic compiler** (same snapshot → same YAML); **per-field provenance**
(evidence-backed vs model-inferred vs compiler-supplied, carrying §7 evidence classes and
confidences) — or the twin's fidelity claim is untestable. *(The third name was `defaulted` until
2026-09-04; renamed to `compiler` with its meaning unchanged, and the sidecar now declares
`axr-cast-provenance/1` so a consumer can tell the rename from a reader bug —
`docs/WORK_ORDER_reference_ingest.md` D5/D6.)*

## 10. Reconciliation loop (with axrange; later phase)

Deploy the synthesized config → capture the twin's traffic → feed a second aximprint instance →
measure drift → tune → repeat. Design capture (full detail in axrange
`DISCUSSION_pcap_discovery.md` §7–8): it's a **feedback control loop with expensive evaluations,
not backprop** — no ∂drift/∂config exists; use the hand-built structural map feature→knob with
damped corrections, Bayesian optimization where entangled. Decompose drift: structural (one-pass
discovery problem) vs behavioral (what the loop minimizes). Compare in **feature space** (the
learner's own statistics; Wasserstein/JS for distributions), never packet space. Two invariants:
same aximprint build on both sides; matched vantage. Residual drift is information — a
persona-coverage gap report. **Round-trip self-test gates everything:** deploy a config you wrote →
capture → learn → synthesize → recover the original. Overfitting guard: learn on one window,
validate drift on a held-out window.

## 11. Schema-evolution posture (design capture for most shapes; **BINDING for the network IR** as of 2026-08-14 — see §0.2)

Recorded now so the reasoning isn't lost; activated only when the user ratifies versioning:

- **Named fields, not positions**; each model ships a manifest of consumed fields; an input adapter
  assembles tensors by name. Converts most changes from breaking to invisible.
- **Change classes:** additive (old models ignore; adopt via zero-initialized input extension +
  fine-tune — provably no day-one regression); subtractive (impute manifest defaults, flag
  `inputs_degraded`, discount confidence — also handles partial sensors for free); **semantic
  redefinition is forbidden as in-place edit** — changed meaning gets a new field name (converts the
  dangerous case into add + deprecate); representation changes are redefinitions in disguise.
- **Backfill is the escape hatch:** event log + idempotent close → replay history through the new
  code and materialize months of features in the new shape overnight. Retraining is never
  data-starved, only compute. Adapters are conveniences; backfill-retrain is the always-available
  clean path.
- **When activated:** registry pins weights to (schema, build) pairs, fail-closed load,
  reconciliation invariant becomes same-(build, schema)-pair on both sides.

## 12. Glossary

| Term | Meaning |
|---|---|
| imprint | the impression a network leaves in its traffic; what the belief store holds |
| ingest plugin | per-sensor adapter normalizing sensor metadata into the event model |
| entity | a stable host identity; what addresses resolve to |
| window | the tunable time bucket (1 h default) features are computed over |
| assertion | one belief: subject–predicate–object + evidence class + confidence + time bounds |
| snapshot | consistent as-of-T read of the belief store |
| cast / synth | compile a snapshot into deployable twin YAML |

## 13. Build arc (indicative, not work-ordered yet)

**Where this stands (2026-08-03):** step 1 is **done** — the zeek plugin streams a real capture
end-to-end (`docs/WORK_ORDER_streaming_zeek_ingest.md`, `Q2` closed). Step 2 is **half done**: the
window/feature artifacts now land on disk through a pluggable sink
(`docs/WORK_ORDER_feature_vector_sink.md`), while both of the substitutions this line names —
resolver v0 → real entity resolution (`Q1`) and exact sketches → probabilistic (`Q8`) — are still
open. Steps 3–5 have v0 slices; 6–8 are unstarted.

1. **zeek ingest plugin** + event model — parse real Zeek logs end-to-end. *(boilerplate ships a
   working v0 slice)*
2. **windowing/features** — resolver v0 → real entity resolution; exact sketches → probabilistic.
3. **deterministic extractors** — CIDR/gateway/domain/listen census writing assertions.
4. **belief store** — projection + decay + snapshot reads.
5. **synthesizer v0** — snapshot → draft twin_config with provenance; validate against axrange.
6. **round-trip self-test** — against a twin axrange deploys from a hand-written config.
7. **learners** — behavioral fitters, then GNN role classifier (labels from twin-generated traffic).
8. **visibility model**, suricata/packetXpress plugins, reconciler — later, in that order.
