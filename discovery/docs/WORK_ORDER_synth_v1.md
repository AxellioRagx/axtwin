# WORK ORDER — synth v1: a snapshot becomes a deployable twin

**STATUS:** **COMPLETE 2026-08-14.** Phases 1 and 2 landed here (see each phase's own RESULT
section); **phase 3 landed inside `docs/WORK_ORDER_network_ir.md`** as its build steps 3 and 4,
after the user ratified the IR later the same day and **D1 was REVERSED**. axRange's own expander
accepted the compiled config — 38 networks, 197 nodes, zero warnings. **Nothing in phases 1 or 2 is affected**: segments and roles are
discovery, and feed an IR exactly as well as a direct compiler. **D2 taken as recommended**
(fixed `/24`, disclosed as an assumption) since phase 1 cannot start without it; overrule it and
phase 1 build step 2 is the only thing that changes. D3 is still open and belongs to phase 3.
**Depends-on:** `WORK_ORDER_entity_resolution.md` (LANDED 2026-08-14 — this work order is the first
consumer of a real entity id), `WORK_ORDER_subject_less_facts.md` (LANDED, `Q29`).
**Closes:** nothing on its own. Advances DESIGN_BRIEF §13 step 5 (synthesizer v0 → v1) and gives
`Q26` (`image:`) the fallback its own point 3 already predicts.
**Authority:** Claude Code analysis at user request (2026-08-14: "what is still blocking to get to
the synth part"). The phase split and every D below are **proposals, not ratified.** One of them
(D1) touches a direction the user already ruled on, and that is called out rather than assumed away.

> ## ⚠ DIRECTION CONFLICT — RESOLVED 2026-08-14 AGAINST THIS BOX'S OWN RECOMMENDATION
>
> **The user ratified the IR later the same day (`docs/WORK_ORDER_network_ir.md`), reversing D1.**
> The box is kept unedited below as the record of what was argued, in the same discipline
> `WORK_ORDER_entity_resolution.md`'s amendment box follows. Its error, named in the new work
> order: it treats cross-run drift as "the argument on the other side" — a tiebreaker — when it
> is a separate product requirement the direct route cannot satisfy at all. A capability only one
> option provides does not break a tie; it decides the question.
>
> ### The original box
>
> `docs/WORK_ORDER_axrange_handoff_ir.md` (DRAFTED 2026-08-11) records a **user ruling**: aximprint
> emits an intermediate representation whose schema aximprint owns, and **the transformer that turns
> that IR into axRange config lives in the axRange repo.** Phase 3 of this work order does the
> opposite — it has `aximprint synth` emit axRange YAML directly.
>
> Both cannot be the plan. **Phases 1 and 2 are unaffected either way** — segment extraction and role
> classification are discovery work that feeds an IR and a direct compiler identically, so neither
> phase is wasted whichever way D1 goes. Phase 3 is the only place the conflict bites, and it is
> deliberately last for that reason.
>
> One argument for the direct route that the 2026-08-11 work order could not have made, because it
> pre-dates the measurement: **the direct route does not trip the §0.2 versioning gate.** That work
> order's own "The gate this trips" section is right that an IR two repositories depend on is exactly
> the condition schema-versioning deferral was waiting for. But axRange's `twin_config` vocabulary is
> **already** closed (`extra="forbid"`, `axrange/twinconfig/schema.py`), already versioned, and
> already validated by their four-pass validator. Emitting into *their* schema means the artifact
> crossing the boundary is governed by a contract that exists, owned by the side that consumes it —
> and aximprint ratifies nothing. That is a materially different cost from the IR route, and it is
> the user's call which cost to pay.

## Goal

Take `BeliefStore.snapshot(as_of=T)` and emit three documents axRange can actually deploy —
`twin_config`, a `tfc` traffic profile, and a `tap` plan — replacing the `twin_config_draft` of
aximprint's own invention that `synth/synthesizer.py` emits today. DESIGN_BRIEF §9's two rules hold
throughout: deterministic compiler, per-field provenance.

The finding that shapes this whole work order: **the store already carries most of what a
`twin_config` requires.** Entity names are real as of `Q1`; listen ports, edges and identity
bindings are all there. What is missing is not evidence — it is **one extractor, one catalog, and
one compiler**, in that order.

## What the store would produce today

Measured 2026-08-14 against `/tmp/aximprint-q27-2026-08-12/beliefs.duckdb`.

> **Provenance, and it matters — this is the trap the entity-resolution round paid for**
> (`WORK_ORDER_entity_resolution.md`'s amendment box: *"a premise measured once is evidence about the
> capture it was measured on, and re-measuring before building is what tells 'still true' from
> 'was true'"* — `LES-006` applied to a work order rather than a health field): that store was built
> **2026-08-12, by a pre-`Q1` build** — `entity == ip`, confirmed by 0 of 3,303
> entity values being anything but an address. So "entities" below reads **"addresses."** The
> private/public split is *insensitive* to resolution (it partitions addresses, and resolution never
> merges across it), so the 10.7× reduction is real as stated; the internal node count shifts down by
> the merge count only — on the `Q1` baseline that was 41 addresses → 25 entities, so expect a
> correction of that order, not of that magnitude. **Re-measure on a post-`Q1` store before building
> against these numbers.** That is build step 0 of phase 1, not a footnote.

| | pre-`Q1` store | **post-`Q1`, after `snapshot()`** | what it becomes |
|---|---|---|---|
| total entities | 3,303 | **3,302** | — |
| …resolved ids (`ent-NNNN`) | 0 by construction | **24** | real hosts, multi-address |
| …bare public addresses | 2,995 | **2,995** | the internet. Not twin material. |
| …bare private addresses | 308 | **283** | unanchored hosts (D4) |
| **twin nodes** (resolved + private) | 308 | **307** → **197 after phase 1** | the node population. 307 counted `is_private`, which in Python includes IPv6 link-local; the extractor's own provisionability gate is the honest filter — see the phase 1 result |
| distinct `/24`s among bare private | 42 | **41** → **38 measured** | the twin's segments |
| entities with `listens_on`, twin-side | 30 | **29** | hosts with role-bearing evidence |
| distinct listen-port signatures | 21 | *re-measure in phase 2* | distinct roles before collapse |
| identity bindings | dhcp 171 · kerberos 90 · dns 135,538 | 12,914 rows | first two are twin-relevant |

**Build step 0 is DONE (2026-08-14)** and the provenance box's prediction held: the private/public
split was insensitive to resolution, and the twin-node count moved by the merge count only —
308 → 307, not by an order of magnitude. The 10.7× reduction from partitioning is real as stated.

**One finding the re-measurement produced that the draft did not anticipate, and it matters for
phase 1.** `node_features` post-`Q1` holds **3,326** entities — *more* than the pre-`Q1` store's
3,303, not fewer. That is `Q1`'s forward-only design working as ruled (D2): a host observed as a
bare address in window 1 and as `ent-0001` from window 2 has rows under **both**, and only
`snapshot()`'s `entity_aliases` collapses them (3,326 → 3,302). **So a per-window extractor sees a
namespace containing both spellings of the same host, and any count taken from `node_features` is
pre-merge.** Phase 1's extractor is per-window and therefore lives on the pre-merge side; this is
correct and needs no special handling — `snapshot()` re-aggregates — but a count taken there must
never be reported as a twin-node count.

So: a ~42-segment, ~308-node twin, of which **~10% have role evidence** and the rest are clients
that collapse behind `count:`. That coverage number is a vantage property, not a defect, and it
belongs in the output's caveats rather than being quietly rounded up.

## What axRange requires (measured 2026-08-14 against `../axr-product`)

`axrange/twinconfig/schema.py`, `axrange/o2control/traffic.py`, `twin-configs/*/`. This supersedes
the equivalent table in `WORK_ORDER_network_ir.md` (renamed from `WORK_ORDER_axrange_handoff_ir.md`
2026-08-14), whose own copy is now re-measured there — it was
written 2026-08-11, before `Q1` closed, and still says `nodes[].name` is "`entity == ip`, wrong unit."

| axRange field | required? | recoverable? | phase |
|---|---|---|---|
| `segments[].name` | **yes** | derived from cidr | 1 |
| `segments[].cidr` | **yes** | yes — `/24` aggregation over local addresses | **1** |
| `segments[].zone_type` | no | no | default `simple` |
| `nodes[].name` | **yes** | **yes — have it**, and real since `Q1` | — |
| `nodes[].image` | **yes** | **no** (`Q26`: three weak sources, disjoint, ~1% coverage) | **2, catalog** |
| `nodes[].role` | no | partially — port signature | **2** |
| `nodes[].kind` (`host`/`router`) | no | see gateway note below | 1 |
| `nodes[].net` | no | yes — cidr membership | 1 |
| `nodes[].count` | no | yes — cluster identical signatures | 2, D3 |
| `nodes[].resources` | no | **no** — CPU/RAM/disk are invisible in traffic | **2, catalog** |
| `nodes[].install` | no | inference from `listens_on` + `runs_software` | 2, catalog |
| `conduits[].{from,to}` | no | yes — edges crossing segments | 1 |
| `identity.users[]` | no | yes — Kerberos user principals, `scope=user` since `Q1` | 3, optional |
| `tfc assignments[].role` | — | **the join key**; nothing without phase 2 | **2** |
| `tfc assignments[].persona` | — | catalog lookup, 28 shipped personas | 2, catalog |
| `tfc assignments[].{intensity,schedule,seed}` | **no — optional** | later | deferred |
| `tap bindings[].selector` | — | yes, once segments exist | 3 |
| `tap bindings[].sensors` | — | **not aximprint's to know** — a Lab (`o2`) fact | human |

**The `tfc` half is less blocked than this project's own docs claim.** `CLAUDE.md` and
`PIPELINE_MAP.md` both say it is "not yet buildable end to end" pending a fitting learner layer.
Measured against the actual consumer, that is overstated: `AssignmentSpec`
(`axrange/o2control/traffic.py:142`) already carries `intensity: Optional[float]`, `schedule` and
`seed` — the exact three things DESIGN_BRIEF §9 says should "land directly in `assignments[]`" — and
**all three are optional.** A profile of `{role, persona}` pairs is valid and deployable. `tfc` v1 is
gated on **roles**, not on Hawkes fits. Correct those two docs when phase 2 lands.

---

## Phase 1 — the segment extractor

**The one hard blocker.** DESIGN_BRIEF §13 step 3 named "CIDR/gateway/domain" extractors; only the
listen census was ever built. `segments[].cidr` is a required field with no producer, and
`nodes[].net` refers to a segment by name, so *nothing* places a node without this.

It also pays for itself four times over: one extractor yields `segments[]`, `nodes[].net`,
`conduits[]`, **and** the internal/external partition that cuts 3,303 nodes to 308.

**Build**

0. **Re-measure on a post-`Q1` store.** See the provenance box. Do not skip this; the entity-
   resolution round is the precedent.
1. **Parse `local_orig` / `local_resp` into `FlowEvent`.** Measured on the 2026-08-11 baseline
   (`/tmp/aximprint-zeek-test/1786428962/conn.log`, 15,037 rows): **100% populated, zero unset** —
   7,143 `T T`, 4,165 `F F`, 3,716 `T F`, 13 `F T`. The plugin drops both fields today (grep:
   zero references in `ingest/zeek/plugin.py` and `events/model.py`). This is the sensor's own
   `Site::local_nets` judgment and is strictly better than an RFC1918 heuristic, because it reflects
   how the network is configured rather than what the address space suggests. Free to add — the
   event model is incubating (§0.2), not a gate. Fall back to `is_private` when a source cannot
   supply it, and record which was used; the two must not be silently interchangeable (`LES-006`).
2. **`/24` aggregation over local addresses** → `segments[]`, `nodes[].net`. Prefix length is D2.
3. **Emit `in_segment` assertions** through a declared projection table in the
   `SERVICE_PROJECTIONS` / `IDENTITY_PROJECTIONS` idiom — decline by name, never by omission.
4. **Conduits** from edges whose endpoints sit in different segments. **Scope call, made during
   the build and recorded rather than done silently: phase 1 makes conduits DERIVABLE and phase 3
   emits them.** Once `in_segment` exists, a conduit is a pure join of it against `snapshot()`'s
   edges — verified on the real baseline, **39 segment pairs carry traffic** — so emitting a
   per-window conduit assertion as well would denormalize a fact already recoverable, into a
   channel nothing reads yet. Note the axRange constraint it has to satisfy when phase 3 does emit
   it: `expand.py` **errors** if a declared conduit has no `kind: router` node joining both its
   segments, which is what the synthetic `gw` node below is for.
5. **`snapshot()` carries segments**, with `evidence_class`. An assertion nobody can read is half a
   feature; this is the half that makes phase 3 possible.

### Phase 1 RESULT — landed 2026-08-14, measured on the real baseline

All five build steps done. Tier 1 (1,294 tests, +38 new) and tier 2 (determinism, CLI round-trip,
transport equivalence across flat / window-tree / Parquet, ruff) both pass.

| | |
|---|---|
| entities placed into a segment | **197** |
| distinct segments | **38** |
| conduits (segment pairs carrying traffic) | **39** |
| evidence class of every segment fact | **`observed`** — this Zeek declares `Site::local_nets`, so 0 endpoints needed the RFC1918 fallback |
| entities declined a segment | **117** — 105 IPv6 link-local, 7 multicast, 2 unspecified, 2 reserved, 1 IPv4 link-local |

**The largest thing this phase learned, and it was found by running it, not by reviewing it.**
The first end-to-end run minted **46** segments, and the biggest — 105 hosts, larger than any real
subnet in the capture — was **`fe80::/64`**. IPv6 link-local. Every interface autoconfigures one,
no twin can deploy it, and every host is on it simultaneously. Four more were multicast, IPv4
link-local and the unspecified address. So `segments.is_provisionable` is now a positive gate on
unicast host addressing, and the honest count is **38, not 46**. Two consequences worth carrying
forward: **(a)** the biggest row of an output can be fiction while every test passes, because no
unit test knew what a network is; **(b)** the fix deliberately does not touch the LOCALITY verdict
— a host holding an `fe80::` address genuinely *is* on-link, it is simply not on a provisioned
network, and conflating "inside" with "placeable" would have been the same error one layer up.

**A finding phase 3 must honor.** Those 105 link-local entities are not extra hosts — they are the
same machines already in the twin under their IPv4 addresses, seen at a second address nothing
links back (that would need NDP evidence this vantage does not carry). **Phase 3 must emit nodes
from the PLACED set, not from every `local` entity**, or the twin gains 105 duplicate hosts with
no network. The count reconciles: 314 local entities − 117 declined = 197 placed.

**Gateway detection is NOT in this phase, and the reason is a measurement.** DESIGN_BRIEF §8 leans on
MAC clustering ("every remote host wears its gateway's MAC") and TTL binning. **Neither signal exists
at this vantage:** this baseline's `conn.log` carries no `orig_l2_addr`/`resp_l2_addr` and no TTL
field, and the only MACs in the entire pipeline are 171 `dhcp.log` rows. §8 describes a full-visibility
core-SPAN vantage; this is not one. Recorded here so the next reader does not go looking for a bug.

What unblocks phase 1 anyway: **axRange auto-assigns a segment's gateway to `network+1` when a router
leg needs one** (`TwinConfigSegment.gateway`). So v1 declares **one synthetic `gw` router node**
multi-homed across every segment, marked `provenance: defaulted`, which satisfies the conduit
cross-check without claiming to have found a real router. Discovering the true gateway is a fidelity
improvement, not a blocker — and it is what §8's method would buy on a vantage that has the signal.

---

## Phase 2 — role classifier and catalog

Independent of phase 1; the two can run concurrently.

**Build**

1. **A port-signature → role table**, same declared-table discipline as above. 21 distinct
   signatures on the real capture, against 28 shipped personas. `445/tcp` → `file-server`,
   `88`/`389`/`464` → `domain-controller`, `3389`+`135` → `corp-workstation`, `5432` → `db-tier`,
   `25`/`587` → `mail-server`. Feed it `known_services.log` as well as `listens_on` — it is already
   parsed and gives (address, port, proto, service-name) directly. **That last instruction turned
   out to be wrong and was NOT followed — see finding 1 below.**
   **An unmatched signature yields `role: null`**, which `twin_config` accepts (`role` is `Optional`),
   and which is the honest output. Do not invent a role to fill the column.
2. **A `role → {image, resources, install, persona}` catalog**, as a **data file, not code**
   (CONVENTIONS §5). Every field it supplies is `provenance: defaulted`. This is the answer `Q26`'s
   own point 3 already anticipates — *"if `image:` falls back to a catalog default for 90% of nodes
   regardless, the honest answer may be that the provenance marking matters more than the coverage."*
   Phase 2 is that ruling, applied. It does not close `Q26`; it makes `Q26` non-blocking.
3. **Node collapse** — cluster hosts with identical (role, signature) into one entry with `count: N`.
   Governed by D3. **Same scope call as phase 1's conduits, and for the same reason: phase 2 makes
   it possible, phase 3 emits it.** Collapse produces a `twin_config` structure, not a belief —
   there is no assertion to write and nothing to store, so computing it here would mean computing
   it and having nowhere to put it. Verified derivable, measured below. This also lets D3 be
   answered where the answer changes an output rather than blocking a phase that does not need it.

### Phase 2 RESULT — landed 2026-08-14, measured on the real baseline

Tier 1 (1,325 tests, +31) and tier 2 both pass. `src/aximprint/catalog/` is a new **bottom-of-stack
shared package** — `roles.yaml` (content) plus a validating loader (code) — imported down into by
`learners` today and by `synth` in phase 3.

| role | placed entities |
|---|---|
| `corp-workstation` | 8 |
| `web-tier` | 5 |
| `dns-resolver` | 3 |
| `domain-controller` | 2 |
| `file-server` | 1 |
| **total roled** | **19 of 197 placed (10%)** |

**Collapse preview: 197 hosts → 49 `twin_config` node entries.** The largest is `count: 45`,
role `null`, on `10.20.193.0/24`.

`has_role` is asserted **`imputed`, never `observed`** — nobody observed "this host is a domain
controller"; what was observed is that it answered on 88 and 389. `listens_on` under it stays
`observed`. This is now the class's second producer and its first in volume.

**The 10% is the number the draft predicted** ("role coverage is ~10%… a vantage property, not a
code defect"), so it is a confirmed expectation rather than a shortfall. The composition matters
more than the fraction: **168 of the 197 placed hosts emit no listening ports at all** — they are
pure clients, and no port-signature rule can ever reach them. That is finding 2.

**Finding 1 — build step 1's `known_services.log` instruction was wrong, and is declined with a
path back.** Measured: the `Q27`-ruled `SERVICE_PROJECTIONS` row for `known_service` projects
`("port_proto", "service")` and **drops `port_num`**, so its durable facts read `tcp|DCE_RPC`,
`udp|DNS`, `tcp|SSL` — no port number anywhere. It therefore cannot supply the `<port>/<proto>`
label the matcher needs, and making it able to would mean editing a user-ruled table. The payoff
would be **8 entities** (49 have `known_service` facts; 41 of those already have `listen_ports`).
Declined on that ratio, not skipped silently. **The path back:** add `port_num` to that projection
row. **And a better one worth noting:** those facts carry the SERVICE NAME (`DNS`, `SSL`,
`DCE_RPC`, `NTLM`), which is role evidence on an axis this table does not use at all — matching on
service names rather than port numbers is a real extension, not a workaround.

**Finding 2 — the highest-value next step is CLIENT-side role inference, and it is measurable.**
168 of 197 placed hosts never accept a connection, so they reach the twin with `role: null`, which
means no `tfc` persona, which means an inert host. A twin where 85% of hosts generate no traffic is
a poor twin. But what those hosts DO is exactly what axRange's persona catalog describes from the
other side: `corp-workstation`'s `emits[]` is kerberos→DC, ldap→DC, smb→file-server, https→proxy.
**A host whose outbound edges match that pattern IS a corp-workstation**, and the edges are already
in the store. This is a new mechanism rather than a gap in step 1 (which specified port signatures
and delivered them), so it is recorded here for a decision rather than built unasked.

**One thing that is correct but reads oddly in the numbers:** 1,157 entities get a role overall,
against 19 placed. The rest are internet peers — every public host answering on 443 matches
`web-tier`. They never reach the twin, because phase 3 emits from the placed set (phase 1's own
finding), so this is harmless. Stated because a reader comparing 1,157 to 197 deserves the reason.

**This is not the GNN, and building the GNN first would be a cycle.** §6's rationale cuts against it
here: *"rule-shaped facts with rule-shaped evidence; ML adds uncertainty where none is needed."* A
listening-service census is a fact a model would degrade. And §10 names the GNN's label source as
**twin-generated traffic where roles are known by construction** — so a deployed twin is an *input*
to training it. The GNN is an accuracy upgrade for the ~278 hosts with no listen evidence, and it is
reachable only after this work order, not before it.

---

## Phase 3 — the compiler

**RE-SCOPED 2026-08-14 into two steps, and BLOCKED on `docs/WORK_ORDER_network_ir.md`'s D1–D9
being ratified or overruled.** D1's reversal does not change what this phase emits; it changes
what it reads. Instead of `snapshot()` → axRange YAML in one hop:

- **3a. Materialize the IR** — `snapshot(as_of)` → the versioned, persisted, timestamped network
  representation (that work order's build steps 1–3). This is where phase 1's segments and phase 2's
  roles actually land as a durable artifact, and where their provenance survives.
- **3b. Compile the IR** → `twin_config` + `tfc` + `tap` (its build step 4). Everything below is
  unchanged except its input.

**Two findings from phases 1 and 2 that step 3b still has to honor**, restated here because they are
easy to lose across a re-scope: nodes are emitted from the **placed** set, never from every `local`
entity (or the twin gains 105 duplicate link-local hosts); and node collapse plus **D3** land here.

**Build (3b)**

1. `twin_config` — `segments[]` + `nodes[]` + `conduits[]` against axRange's closed vocabulary.
2. `tfc` — `traffic-profile.assignments[]` of `{role, persona}`, plus a `roles[]` block. Note
   `roles.yaml` **overrides** the twin's own `role:` per host, so a bare-bone twin declaring no roles
   at all, driven entirely from the profile, is a supported authoring shape and may be the better one.
3. `tap` — `bindings[].selector` per segment. **Leave `sensors` unfilled with an explicit caveat:**
   which Lab sensor captures which link is an `o2` fact, and aximprint cannot know it. Emitting a
   guess there would be the exact failure `evidence_class` exists to prevent.
4. **Provenance goes in a sidecar, not in the YAML.** `extra="forbid"` means the `{value, provenance}`
   wrappers the current stub emits (`synthesizer.py:30`) are **rejected** by axRange's own schema.
   Write `<name>.provenance.json` beside each document, keyed by field path. This is a real
   constraint discovered by reading their schema, not a style preference.

---

## Decisions needed (all user)

| # | Decision | Recommendation |
|---|---|---|
| ~~**D1**~~ | ~~Direct compiler, or the IR route?~~ | **REVERSED 2026-08-14 (user), later the same day — see `docs/WORK_ORDER_network_ir.md`.** The morning's ruling was "direct compiler", on a Claude Code recommendation that treated cross-run drift as a tiebreaker when it is a separate product requirement the direct route structurally cannot serve. **The IR is ratified**, and phase 3 below is re-scoped from one step into two. Phases 1 and 2 are unaffected — segments and roles are discovery, and feed either target identically. |
| **D2** | ~~Segment prefix length — fixed `/24`, or inferred?~~ | **Taken as recommended 2026-08-14 (Claude Code, phase 1 could not start without it): fixed `/24`**, disclosed as an assumption in the output. 42 segments on the real capture is already a large twin; inference is a phase-4 refinement. A fixed prefix also keeps segment assignment a **pure per-address function**, needing no global pass — which is what lets it be computed at window close rather than at snapshot time. |
| **D3** | Node-count policy — cap the twin? drop singleton `/24`s? collapse aggressively? | Needs a user call. 308 VMs is a real deployment cost, and this is a scope question, not a technical one. |
| **D4** | Does `synth` emit one document set, or a per-run directory? | Follows from D1; a per-run directory is the shape that keeps the drift purpose reachable. |

## Boundaries — what this work order deliberately does not fix

Named explicitly, because the fastest way to not ship this is to fix them first:

- **`Q8`** (sketches O(input)), **`Q11`** (untested long-tail spill — `synth` already filters
  `__longtail__`), **`Q25`** (Zeek byte-reproducibility), **`Q28`**/**`Q30`** (coverage-audit
  honesty). None reaches a `twin_config` field.
- **`Q5`** (decay). Matters for a store accumulating months; irrelevant for one capture → one twin.
- **`imputed` still has no producer.** Everything unobserved here is `defaulted`, which is an honest
  class and which the stub already marks correctly. `imputed` arrives with the ML layer.
- **`snapshot()` not reading `profile_facts`.** A deliberate decline (`belief_store.py:450`) and the
  right one — that is a `tfc` v2 concern, and the `intensity`/`schedule`/`seed` receivers are optional.
- **Fitted continuous intensities.** Personas carry *categorical* knobs (`volume: high`,
  `cadence: diurnal`). v1 picks a persona; v2 overrides `intensity` with a float. Both work against
  today's axRange, so this is an increment, not a prerequisite.
- **Real gateway discovery.** See phase 1's measurement note.

## Validation

- **Tier 1/2 as usual**, plus: the compiler is a pure function of the snapshot — same snapshot, same
  bytes, in `scripts/verify_pipeline_gates.sh` alongside the existing determinism invariants.
- **The acceptance gate is free and already written.** Pipe the emitted `twin_config` through
  axRange's own `expand_twin_config` and its four-pass validator. That answers *"would this deploy"*
  without deploying anything, and it is decisive in a way a hand-written assertion is not. It runs in
  the sibling repo, so wire it as an opt-in script rather than a tier-2 step — aximprint must not
  import axRange.
- **The honest end-to-end question:** take the real baseline, run the whole chain, and deploy the
  result. Anything short of that is a claim about YAML shape, not about a twin.

## Findings worth keeping regardless of what gets built

1. **`local_orig`/`local_resp` are 100% populated and 100% discarded.** The single highest-value
   two-field parser addition available, and it was invisible because nothing downstream asked for it.
2. **§8's segmentation method does not apply at this vantage** — no L2 addresses, no TTLs in
   `conn.log`. The design brief describes a core-SPAN vantage; this baseline is not one, and §8 should
   say so rather than reading as universally available.
3. **`tfc`'s receiver fields already exist in axRange.** Two docs in this repo say otherwise.
4. **axRange's `extra="forbid"` rules out inline provenance**, which is the shape the current stub
   emits. Anything that ever writes a `twin_config` has to know this.
