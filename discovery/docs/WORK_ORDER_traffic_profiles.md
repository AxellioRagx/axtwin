# Work order — traffic profiles from the evidence plane: a behavioral layer in the IR, and the `tfc` projection

**Status:** **DRAFTED 2026-08-27, unratified.** Scope ruled **wide (IR-first)** by the user the same
day: the behavioral layer serves all four IR consumers, and `cast` projects the narrow subset
axRange can consume today.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** nothing. This is additive to `docs/WORK_ORDER_network_ir.md` and does not move the
snapshot boundary (`docs/WORK_ORDER_evidence_plane_snapshots.md` is unaffected — this work order's
producer lives in `resolve()`, on the conclusions side of that boundary wherever it ends up).

## The gap

`tfc` is a declared pipeline output (DESIGN_BRIEF §9). `cast` emits a `traffic-profile` document
today and axRange accepts it — but it carries **role→persona assignments and nothing else**:

```python
assignments.append({"role": role, "persona": rule.persona})
traffic_profile = {"traffic-profile": {"target": twin_id, "assignments": assignments}}
```

No intensity, no schedule, no seed. Meanwhile the evidence plane holds, per entity per window,
`flows_out`/`flows_in`/`bytes_out`/`bytes_in`, `client_ratio`, `proto_mix`, three quantile sketches
(`flow_size_q`/`duration_q`/`interarrival_q`), a 12-bucket `subbucket_flows` array and a computed
`burstiness`. **None of it reaches the twin.** The twin's traffic is currently a function of the
role catalog alone — i.e. of what we decided a role *is*, never of what the network *did*.

`Q29`'s `profile_facts` channel is a separate, smaller gap: written by `StoreSink`, read by nothing
but a `status` row count.

## The measurement that bounds this, and it is on the CONSUMER side

Before scoping the producer, `axrange/o2control/traffic.py::AssignmentSpec` was read. It accepts
exactly three optional fields beyond `role`/`node` and `persona`:

| field | shape | actually consumed? |
|---|---|---|
| `schedule` | closed 2-token enum: `24x7` \| `business-hours` (`_SCHEDULE_ALIASES`) | **yes** — overrides the persona's cadence-derived emit gate |
| `seed` | any | **yes** — folded by `personas/drivers/base.py::effective_seed()` into the traffic-timing RNG |
| `intensity` | float | **no.** Parsed, validated, carried into `api/dtos.py` and `ops/registry.py` — no driver reads it. Their own comment: *"optional, informational (persona.knobs override — follow-on)"* |

**So exactly one measured quantity can change twin behavior today: `business-hours` vs `24x7`.**
There is no field for a rate, a diurnal curve, a burst distribution or a cache lifetime, and
`ocsp_cache_lifetime` — `profile_facts`' only occupant — has **no receiver in this vocabulary at
all**.

This is why the scope is IR-first rather than cast-first. The IR has **four** consumers, not one
(`docs/WORK_ORDER_network_ir.md`): twin compiler, drift comparator, GNN evaluation, topology
diagram. A behavioral layer earns its place on the drift comparator alone — `drift` today reports
structural change only and **cannot see that a host's activity shape changed**. Sizing this work to
axRange's three scalars would build for the one consumer with the narrowest vocabulary, and the
narrow projection is a dozen lines on top of the wide layer either way.

## What this needs from the layers below: nothing

Every input already sits in `node_features` rows, which carry `window_start`. Hour-of-day is
`window_start` arithmetic across a multi-window span; the rest is aggregation over existing keys.
**No ingest change, no windowing change, no new event type, no plugin change** — the same shape as
per-segment coverage (`docs/WORK_ORDER_network_ir.md` step 1b), which was derivable at materialize
time from rows already written.

`PROFILE_PARAMETERS`' own preamble already ruled this channel out for the job, and the ruling is
adopted here rather than revisited:

> *Neither an integer seed nor a diurnal 24-bucket schedule — both named in DESIGN_BRIEF §9's
> "fitted intensities/schedules/seeds" — fits this shape; widening past "one non-negative float,
> four quantiles" is a real design question for whoever adds the second entry, not answered here.*

The answer this work order gives: **a diurnal profile has a subject, so it does not belong in a
subject-less channel.** `profile_facts` keeps its narrow contract (D7 gives it a reader without
widening it).

## Target

```
node_features (per entity, per window)      ← already written, unchanged
      │ resolve()  — aggregate over the span, under the RESOLVED entity
      ▼
ir_traffic_profile   (per entity)     NEW, additive, schema 1.3   ← observed
ir_capture_profile   (subject-less)   NEW, additive               ← observed
      │
      ├─► drift      behavioral drift beside structural (D9)
      ├─► GNN eval   dense per-entity temporal features
      ├─► diagram    (available, no change required)
      └─► cast       aggregate members → role
                     schedule: business-hours | 24x7    imputed
                     seed:     deterministic            defaulted
                     intensity: relative, INERT downstream, disclosed
```

## Decisions

### D1 — Grain: per **entity** in the IR, aggregated to **role** at compile time

The IR stores the finest grain it observed. `cast` aggregates the member entities of a role when it
builds an assignment. A role-level aggregate is **not** also stored: it is derivable, and two copies
of one fact with nothing checking they agree is the hazard this project keeps re-finding
(`_canonical_columns`, `KNOWN_PARSED_STEMS`/`_PARSERS`).

Consistent with `docs/WORK_ORDER_network_ir.md` **D9** — the IR keeps the complete view; collapsing
lives at compile time, or drift starts measuring compiler policy.

### D2 — What a profile contains: a declared, closed set

Every field derivable from `node_features` rows over the span, aggregated under the resolved entity
(the `canon` map, exactly as `_entity_extents`/`_entity_locality` already do). All `observed`.

| field | derivation |
|---|---|
| `hod_flows` | 24-bucket hour-of-day histogram of `flows_out + flows_in`, **UTC**, normalized to sum 1.0 |
| `dow_flows` | 7-bucket day-of-week histogram, UTC, normalized — separates weekday-only from business-hours |
| `windows_active` / `windows_total` | occupancy; `windows_active` is distinct from `ir_entity.windows_seen` only if the latter's definition drifts, so it is derived from the same rows and asserted equal in test |
| `flows_per_window_q` / `bytes_per_window_q` | p10/p50/p90/p99 across active windows |
| `burstiness_p50` | median of the per-window `burstiness` already computed by `windowing` |
| `client_ratio_p50` | median `client_ratio` — initiator vs responder character |
| `interarrival_p50` | median of each window's own interarrival p50 |

`hod_flows` is stored in **UTC and labeled as such**. A capture carries no timezone and nothing in
traffic declares one; storing a clock-aligned local histogram would be inventing a fact
(`LES-006` — never coerce unknown into a value).

### D3 — The schedule classification is a claim about **shape**, not about the clock

Because the timezone is unknown, `business-hours` cannot mean "active 09:00–17:00 local". It means
**activity is concentrated rather than flat**: an entity is `business-hours` when some contiguous
8-hour UTC band of `hod_flows` holds ≥ `_BUSY_BAND_MIN` of its flows *and* the quietest contiguous
8-hour band holds ≤ `_QUIET_BAND_MAX`; otherwise `24x7`.

- The histogram is `observed`; **the classification is `imputed`** — same posture as `role`
  (nobody observed "this is a business-hours host", only when it was busy).
- Thresholds are declared module constants, **measured on the ten-day baseline before pinning**
  (build step 6). A threshold picked without measuring it is exactly the `snmp` `display_string`
  mistake (`R2`).
- `cast` emits a caveat: axRange's business-hours gate uses **its own** clock, so a twin deployed in
  a different timezone shifts the band. That is a fidelity caveat, not a correctness one, and it is
  disclosed rather than silently absorbed.

### D4 — Two evidence floors, separately disclosed

Below a floor a field is `None`, never fabricated:

- **Diurnal claim** (`hod_flows`, `dow_flows`, and therefore `schedule`) needs the span to cover a
  full day at least twice — proposed **≥ 48 active windows**. A host active in 2 windows has no
  diurnal shape, and publishing a 24-bucket histogram from 2 samples is a confident zero of exactly
  the kind `Q34` deleted.
- **Rate/occupancy stats** need far less — proposed **≥ 4 active windows**.

Both floors measured before pinning. An entity below a floor gets `None` for that group and is
**counted** in `_stats`, the way `unkeyable_entities` already is.

### D5 — `seed` is `defaulted` and deterministic, never random

Derived from `(pit_id, role)` by a stable hash. It is a *choice*, not a measurement — so its
provenance class is `defaulted`, the class the compiler already owns and that
`docs/WORK_ORDER_network_ir.md` forbids from re-entering drift comparison as evidence. Two casts of
one PIT must produce the identical seed (`LES-002`; `scripts/verify_determinism.py` covers it).

### D6 — `intensity` is emitted, and its inertness is disclosed

Emitted as the role's median flows-per-window **normalized against the PIT's own median across
roles** — a relative figure, since an absolute rate is not interpretable by a consumer that never
reads it. `cast` adds a caveat naming that **no axRange driver consumes `intensity` today**, so the
number is never mistaken for something that changed the twin.

Rationale for emitting it at all: the field exists and is validated by their parser, the measurement
is free once D2 lands, and the alternative — withholding a measured value — is the failure mode the
forensic-completeness rule exists to prevent. A value withheld at emit time is indistinguishable
downstream from one never measured.

### D7 — `profile_facts` reaches the IR as `ir_capture_profile`, and stops there

The subject-less channel gets its **first reader** without widening its contract and without
inventing a consumer: capture-wide facts (today: `ocsp_cache_lifetime` quantiles) are carried into
the IR, where the drift comparator and diagram generator can read them.

**Explicitly NOT projected into `tfc`**: no receiver exists in `AssignmentSpec`, and manufacturing
one would be building against a vocabulary we do not own. If axRange later grows a field for it, the
IR already carries the value.

### D8 — Schema `1.2` → `1.3`, additive (MINOR)

Two new tables; **no existing column changed, none removed, nothing redefined**. Per DESIGN_BRIEF
§11 that is additive → MINOR, the same ceremony `1.1` (locality) and `1.2` (vantage + unseen) both
followed. `docs/SPEC_network_ir.md` is the authority and is updated in the same change;
`tests/test_ir_schema.py` fails if the SPEC and `ir/schema.py` disagree.

This is **not** a §0.2 ratification — the IR is already the ratified versioned schema, and this
follows its existing rules rather than extending versioning to anything new (`LES-001`).

### D9 — `drift` gains behavioral comparison

The reason the scope is wide. `compare()` reports:

- **schedule class changes** for local entities, itemized;
- **occupancy deltas** past a declared threshold;
- external peers **counted, not itemized** — the discipline `compare()` already uses (1,963 external
  vs 61 local changes; a naive diff is 97% noise).

Behavioral drift is reported **beside** structural drift, never merged into it: a host that stayed
and changed shape is a different finding from a host that left.

## Build steps

1. **Producer.** `ir/materialize.py`: read per-window `node_features` for the span, aggregate per
   resolved entity into the D2 profile. Store access stays inside `snapshot_evidence()`; `resolve()`
   stays pure.
2. **Schema.** `ir_traffic_profile` + `ir_capture_profile` in `ir/schema.py`, bump to `1.3`, update
   `docs/SPEC_network_ir.md` in the same change.
3. **Persistence.** `ir/store.py` write/read for both tables; deterministic row ordering at the
   persistence boundary (`LES-009`).
4. **Consumer.** `synth/compiler.py`: role aggregation → D3 classification → emit `schedule`, `seed`
   (D5), `intensity` (D6) into each assignment; caveats + sidecar provenance for all three.
5. **Drift.** `ir/compare.py`: D9.
6. **Measure and pin.** Run against `/home/rocky/harvest-corpapcn-10d/beliefs-v2.duckdb` (an
   already-harvested local baseline — reading one is not gated). Pin D3's thresholds and D4's floors
   to measured values; report how many entities clear each floor.

Steps 1–4 are one tranche (producer through consumer, end to end). Steps 5–6 follow.

## Acceptance

- Tier 1 green; new unit tests for the aggregation, both floors, the classifier and the seed.
- **Determinism**: two casts of one PIT byte-identical, including seed and histogram ordering
  (`scripts/verify_determinism.py`, tier 2).
- **`cast` output still validates against axRange's own expander** — the gate `WORK_ORDER_network_ir.md`
  step 4 set, and the only external check this project has.
- `tests/test_ir_schema.py` green at `1.3`.
- Measured on the baseline: how many of the 58,563 entities clear the diurnal floor, how many roles
  get a non-`None` schedule, and the class split.

## What this deliberately does not do

- **No windowing or ingest change.** If a wanted parameter turns out not to be derivable from
  existing `node_features` rows, that is a separate work order, not a scope creep into this one.
- **No fitted intensities in the axRange sense** (Hawkes, Fourier, Markov — DESIGN_BRIEF §6). Those
  are the ML layer's, and `docs/WORK_ORDER_ml_layer.md` already names `tfc` parameter learning as
  the densest unclaimed target. This work order builds the **channel** they would write into.
- **No new runtime dependency.** Everything here is arithmetic over rows already read.
