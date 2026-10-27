# Work order — review fixes for the traffic-profile tranche

**Status:** **DRAFTED 2026-08-27, unratified, NOTHING BUILT.** These are the findings of a
`code-reviewer` pass over `docs/WORK_ORDER_traffic_profiles.md` build steps 1–4. The review
**landed after that tranche was committed** (`4188bb2`), and the user ruled: **record the findings
as a work order, do not fix them in that commit.** So `4188bb2` ships with everything below still
present.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** nothing.

**Two of the four must-fixes were measured on the real ten-day baseline**
(`/home/rocky/harvest-corpapcn-10d/beliefs-v2.duckdb`, read-only copy: 241 windows, 633,465
`node_features` rows, 58,577 canonical entities). They are not theoretical.

---

## M1 — `windows_active` counts ROWS, not distinct windows

`src/aximprint/ir/materialize.py:474` (`a.windows_active += 1`), and the same defect in the
per-window sample lists at `:469-473`.

**Why it happens.** `node_features` is **pre-merge**. `Q1` D2 makes entity resolution
**forward-only**, so a resolved host has rows under *both* its bare address and its `ent-NNNN` id —
and because a merge is learned *inside* a window, those ranges **overlap by at least one window**.
`BeliefStore.snapshot()` was already fixed for exactly this (`belief_store.py:490-526` —
`count(DISTINCT window_start)` plus a second targeted query for merged spellings, with its own
comment recording "20 of 24 resolved entities reported `windows_seen` ABOVE the store's own window
count"). `_entity_traffic` **re-implemented that count one layer up and reproduced the defect.**

**Measured:**

| | |
|---|---|
| entities with inflated `windows_active` | **66** |
| entities where `windows_active > windows_total` | **42** |
| worst case | `ent-0020`: **418 of 241 windows — 173% occupancy** |

**Three silent consequences.**
1. D2's own stated invariant `windows_active == ir_entity.windows_seen` is violated for exactly the
   entities resolution worked hardest on — the host-named ones the twin compiler places and roles.
2. **D4's floors are crossed by ROW count**, so a merged entity clears the 48-window diurnal floor
   on fewer than 48 real windows. The floor's whole purpose is defeated where it matters most.
3. `flows_per_window` / `bytes_per_window` get **two half-sized samples** for the overlap window,
   biasing `flows_per_window_q` low — which is `intensity`'s input.

**Fix.** Aggregate per `(canonical_key, window_start)` first, then reduce. That fixes all three at
once: `windows_active = len(per_window)`, one flow/byte sample per window (summed across
spellings), `hod`/`dow` unchanged. **Do not merely swap a `set` in for the counter** — the
per-window sample lists are wrong too.

**Test gap that let it through.** `test_windows_active_equals_ir_entitys_windows_seen` passes only
because its fixture has no merge; `test_traffic_aggregates_under_the_resolved_entity_across_a_merge`
puts the binding on a **window boundary**, so it never produces the overlap. Both need the
**mid-window merge** case — that is the only shape that fails.

This is the **fourth** instance of `LES-114`, in a function whose own docstring cites `LES-114`.

---

## M2 — `ir/merge.py` stamps `schema_version = "1.3"` and emits 6 of 8 tables

`src/aximprint/ir/merge.py:420-421` (and `:407`). Verified at runtime:

```
IR_TABLES        : ir_capture_profile ir_edge ir_entity ir_identity ir_pit ir_segment
                   ir_traffic_profile ir_unseen        (8)
materialize emits: all 8
merge       emits: ir_edge ir_entity ir_identity ir_pit ir_segment ir_unseen   (6)
merged schema_version claimed: 1.3
read back: ir_traffic_profile 0 rows, ir_capture_profile 0 rows
```

**The IR has TWO producers** and only one was taught about the new tables. `write_pit` skips empty
tables, `read_pit` returns `[]`, and `compile_twin`'s `pit.get("ir_traffic_profile") or []` degrades
to "no evidence" — so `cast` on a **merged** PIT emits assignments with no `schedule` and no
`intensity`, **indistinguishable from "no entity cleared the floor."** `LES-006` at the artifact
level. The drift comparator and diagram generator get the same silent hole.

**Nothing catches it:** `test_the_spec_declares_every_table_the_code_does` checks SPEC ↔ `IR_TABLES`
— declaration against declaration. **No test asserts a PRODUCER emits every table.**

**Fix, and it carries a decision — see D1 below.** Either way, land the completeness test now:
`{k for k in out if k.startswith("ir_")} == set(IR_TABLES)` for **both** `materialize()` and
`merge()`.

---

## M3 — `intensity` is stamped `observed`, but its denominator is compiler policy

`src/aximprint/synth/compiler.py:369`.

`pit_median_rate` (`:342-343`) is the median over **`roles_with_persona` only**. Edit
`catalog/roles.yaml` and **every intensity in the twin changes with nothing on the wire having
moved.** A value a compiler's own catalog can move is not an observation.

The project's operational test is already written down, at `ir/schema.py:51-58` and
`compiler.py:227` for `role`: *nobody observed "this is a domain controller", only that it answered
on 88 and 389.* Likewise nobody observed "this role is 1.7× the median role."

**Fix.** `imputed`, with the normalization population named in the `prov()` extras
(`denominator_roles=len(_measured_rates)`). Update
`tests/test_synth_compiler.py::test_schedule_and_intensity_provenance_land_in_the_sidecar_not_inline`.

**Two aggravating details** found in the same pass: with **one** persona-bearing role the median is
that role's own rate, so `intensity` is **always exactly 1.0** — a tautology wearing the costume of
a measurement, with nothing in the caveat saying so. And the **absolute** rate never reaches any
twin artifact (the sidecar carries only a `note`), so a consumer holding the twin cannot recover
flows-per-window at all — which cuts against the forensic-completeness argument used to justify
emitting the field. Put `flows_per_window_p50` and `denominator_roles` in the sidecar extras.

---

## M4 — `classify_schedule` returns bare literals; `resolve()` writes `schedule` with no membership gate

`src/aximprint/ir/materialize.py:419-420` return `"business-hours"` / `"24x7"` as string literals.
`SCHEDULE_CLASSES` lives at `ir/schema.py:87` and **is never consulted by the producer**.
`resolve()` (`materialize.py:764`) writes the result straight into the persisted artifact.

This is the hazard `WORK_ORDER_traffic_profiles.md` **D1 itself names** — *"two copies of one fact
with nothing checking they agree"* — reproduced inside the change that cites it. The only
membership check is an `assert` at `compiler.py:358`, one layer **downstream**, after the value is
already persisted, and removed under `python -O`.

**Fix.** `materialize.py` already imports from `ir.schema`: return `SCHEDULE_CLASSES[1]` / `[0]`, or
assert membership **inside** `classify_schedule` so both callers inherit the gate. `ir/schema.py`
cannot import `materialize` (guarded by `test_the_ir_schema_module_imports_nothing_from_aximprint`),
so the direction is fixed and correct. Landing S8 below fixes this for free.

---

## Decisions needed

### D1 — Scope of the `merge()` fix (M2)

Making a merged PIT genuinely `1.3` means declaring **per-axis pooling** for `ir_traffic_profile`,
and the honest answers are not obvious: `hod_flows` wants a `windows_active`-weighted mean;
`windows_active` **sums** on the time axis and **maxes** on the vantage axis; and
`flows_per_window_q` **cannot be honestly re-pooled from quantiles at all**, so it is arguably
`None` in any merged PIT — which is `LES-118`'s *"a union of finished artifacts cannot redo the work
that produced them"* arriving again.

- **(a)** Land the per-axis rules now, widening the tranche.
- **(b) — reviewer's recommendation.** Make `merge()` **fail loud**, or emit both tables empty with
  an explicit `_stats` entry and a caveat naming the drop; route the pooling rules to
  `WORK_ORDER_traffic_profiles.md` **step 5**, beside D9's drift work which will consume them.

(b) closes the honesty hole immediately, keeps the tranche boundary, and puts the pooling question
next to its consumer.

### D2 — `ir_capture_profile` pooling, revisited

Already flagged as unratified in the parent work order. The review adds a measured argument:
`profile_facts` carries **`n` per window**, and today a window with `n=1` (where p10 == p50 == p90
== p99 == that one value) counts **equally** with a window of `n=1000`. On the baseline
`profile_facts` has 1,205 rows across 241 windows — small enough that this is currently harmless,
large enough that it will matter as `PROFILE_PARAMETERS` grows. An `n`-weighted quantile is
strictly more defensible and is a small change. **Take it, or record why not.**

Note also: the choice `BeliefStore.snapshot()` deferred to *"whoever builds the `tfc` compiler"* was
taken by the **IR materializer**, and D7 says `ir_capture_profile` is never projected into `tfc` —
so it was taken by a component the `tfc` compiler cannot see. Not wrong (the IR is the right home
for a persisted artifact), but the deferral's terms changed; S6 and S9 are what make that honest.

---

## Should-fix

- **S1 — `_capture_profile` dispatches on a string literal** where a declared reducer table belongs
  (`materialize.py:548`: `sum(values) if stat == "n" else statistics.median(values)`). An
  unrecognized `stat` falls through to `median` **silently**. The project already solved this with
  `windowing.SENSOR_HEALTH_AGGREGATION` (*"never a blanket choice"*). Import
  `_PROFILE_QUANTILE_POINTS` function-locally and fail loud outside `{labels} ∪ {"n"}`.
- **S2 — three determinism/latency hazards in `_profile_facts`, all fixed by one `ORDER BY`**
  (`materialize.py:504-515`, no ordering). `unit_of[k] = unit` (`:543`) is **last-write-wins over an
  unordered DuckDB result**, and a store accumulates rows across builds; `sum(values)` (`:548`) is
  an order-dependent float sum. Add `ORDER BY window_start, parameter, stat`, and **fail loud on a
  unit disagreement** rather than picking one.
- **S3 — the determinism gap is WIDER than previously reported.** Not only does
  `verify_determinism.py:852` import `synth.synthesizer` (never `synth.compiler`) — it also runs
  **both passes in one interpreter**, and `verify_pipeline_gates.sh` gate 2 never invokes
  `materialize` or `cast` at all. **So a `PYTHONHASHSEED`-salted value would survive every gate this
  project has.** Three steps, cheapest first: (1) make
  `test_seed_never_uses_pythons_own_salted_hash` real — two `subprocess.run` calls under different
  `PYTHONHASHSEED` is four lines, and its own docstring currently admits it does not check what its
  name says; (2) run gate 3's two **already byte-compared** `features` invocations under
  `PYTHONHASHSEED=0` and `=1`, converting an existing comparison into a whole-pipeline salted-hash
  null test for free; (3) add a check 4 to `verify_determinism.py`:
  `materialize → write_pit → compile_twin → to_yaml` across two subprocesses, byte-compared.
- **S4 — `IRStore` promises additive-MINOR readability it does not deliver.** Verified: a genuinely
  older file (`ir_segment` without `coverage_source` — exactly the shape `1.1`'s `locality` took)
  **opens fine** (`can_read('1.2','1.3')` is `True`) and then `read_pit` dies with
  `BinderException: Referenced column "coverage_source" not found in FROM clause!`.
  `CREATE TABLE IF NOT EXISTS` covers a new *table* and does nothing for a new *column*. Under §11,
  additive → MINOR → readable, so the correct implementation is an `ALTER TABLE ... ADD COLUMN`
  sweep on open (NULL = "the writing build did not have this"), or a **loud, versioned refusal
  naming the gap** — not an opaque Binder error. Pre-existing; surfaced, not caused, by this change.
- **S5 — `_json_value` is the third copy of one idiom** (`compiler.py:70-75`, whose own docstring
  names the other two). **Reuse `ir/merge.py::_json_list`** rather than adding a third spelling;
  hoist it into `ir/schema.py` if a shared home is wanted (it imports nothing from aximprint).
- **S6 — `BeliefStore.snapshot()`'s docstring is now factually false** (`belief_store.py:460-467`:
  *"as of this snapshot the channel has somewhere to WRITE and no way to READ"*). `_profile_facts`
  now reads it. Point it at `ir/materialize.py::_capture_profile` as the place that took the
  deferred decision.
- **S7 — `zip(*hods)` silently truncates to the shortest member** (`compiler.py:333`). The project's
  own convention is explicit (`windowing/windows.py:409-412`): use `strict=True`.
- **S8 — `synth/compiler.py` imports the materializer for one pure function** (`compiler.py:35`).
  Move `classify_schedule` + `_HOUR_BUCKETS`/`_BAND_HOURS`/`_BUSY_BAND_MIN`/`_QUIET_BAND_MAX` next
  to `SCHEDULE_CLASSES` in `ir/schema.py`; leave the two floors in `materialize.py` where they are
  applied. Lands M4's fix for free.
- **S9 — the SPEC under-discloses which numbers are placeholders.** `SPEC_network_ir.md:396` labels
  the **two floors** unmeasured; the `schedule` row describes the 8-hour band rule as **settled**,
  and `_BAND_HOURS`/`_BUSY_BAND_MIN`/`_QUIET_BAND_MAX` are never marked provisional there — nor in
  `cast`'s caveats (`compiler.py:381-387` discloses the timezone hazard and not the thresholds).
  Also `materialize.py:321-326` says *"All four below are proposed, UNMEASURED defaults"* above
  **six** constants, two of which (`_HOUR_BUCKETS=24`, `_DOW_BUCKETS=7`) are facts about clocks, not
  choices. Fix all three places.
- **S10 — three full scans + three full JSON parses of `node_features`.** Measured on the baseline:
  `_segment_windows` 9.3 s, `_entity_locality` 10.0 s, `_entity_traffic` **14.0 s**; peak RSS
  216 MB → **1,191 MB**. All three are `SELECT ... FROM node_features {cond}` with the same bound.
  One pass feeding all three reducers cuts ~20 s of 33 s and one full parse pass. Scales linearly:
  a 41-day capture is ~4× both numbers.
- **S11 — one caveat worth adding to the role histogram.** The unweighted mean (parent D2 choice
  (ii)) is judged **correct** — the output is a binary class, not a rate, and volume-weighting would
  make a role's schedule equal its busiest member's. But a member barely over the floor (48 windows)
  carries the same weight as one active 1,440 windows, so with 2–3 members one sparse member can
  flip the class. The sidecar already records `members_with_diurnal_evidence` / `members_total`; add
  a caveat when `members_with_diurnal_evidence == 1` — a role's schedule resting on one host is a
  materially weaker claim, and the twin YAML cannot currently tell that apart from twelve.

---

## Lesson candidates

Recorded here rather than in `docs/LESSONS.md`, so the count stays at 133 until they are ratified.
Numbering continues from `LES-133`.

- **`LES-134` — A count of ROWS is not a count of WINDOWS once resolution is forward-only;
  re-derive the distinct-window rule, never re-implement it.** Canonicalizing the KEY (`LES-114`) is
  only half the guard: it stops rows being *lost* and does nothing about rows being
  *double-counted*. Reduce per `(key, window)` before reducing per key. Any new field expressing
  occupancy owes a cross-field assertion against the field that already does (`LES-133`), with a
  fixture whose merge is learned **mid-window** — the only shape that fails.
- **`LES-135` — A stamped schema version is a promise about the whole artifact; adding a table is
  not done until every PRODUCER emits it.** Every hand-listed output dict over a declared table set
  is a duplication bug awaiting the next additive change. The test that holds is **per-producer**,
  not per-schema. A producer that legitimately cannot fill a table must say so loudly — an empty
  table and an absent one are the same bytes.
- **`LES-136` — A closed vocabulary and the function producing its values are two copies of one fact
  unless the producer draws from the constant.** The declaration is only load-bearing if the
  producer returns `VOCAB[i]`, or asserts membership *inside itself* so every caller inherits the
  gate. Test the **producer** against the vocabulary, not only the vocabulary against the SPEC.
- **`LES-137` — A derived ratio is only as observed as its DENOMINATOR.** Aggregating observations
  keeps them observed; **normalizing** them does not, when the denominator is a population the
  compiler selected. Mechanical test: *can this number change without the network changing? Then it
  is not `observed`.* A ratio published without its denominator is not disclosed, and emitting a
  ratio does not discharge forensic completeness for the absolute value behind it.
- **`LES-138` — An in-process double-call cannot detect a per-process salted hash.** `LES-002`'s
  argument one level down: a single **process** always agrees with itself about `hash()`. A test
  whose *name* asserts a property it does not check is worse than no test. Vary `PYTHONHASHSEED`
  across the two sides of any byte-comparison already being run. And a determinism gate covers the
  code path it **imports**, not the verb it is named after.
- **`LES-139` — A reduction baked into a versioned artifact's value column, with no column naming
  it, can never be changed.** §11 forbids semantic redefinition in place, so a choosable reduction
  (median vs `n`-weighted vs pooled-raw) persisted without a column recording which was used is
  **frozen on write**. The IR already has the precedent: `COVERAGE_SOURCES` is *"a vocabulary, not a
  boolean, because it has to be able to grow."* Add a `pooling` column while it is still additive —
  one VARCHAR today, a prohibition tomorrow. Same test for a threshold pinned by a later build step:
  that is a semantic redefinition **scheduled in advance**, so disclose it in the SPEC and in every
  downstream caveat.

---

## Build steps

1. **M1** — the `(key, window)` pre-aggregation, plus mid-window-merge fixtures for both tests.
2. **M3**, **M4** (with **S8**), **S7** — each contained to one function.
3. **D1** — rule, then **M2** plus the per-producer completeness test.
4. **S1**, **S2**, **S6**, **S9** — the fail-loud and disclosure fixes.
5. **S3**, **S4** — the two pre-existing gaps, deferred to integration testing by the user.
6. **S5**, **S10**, **S11**, **D2** — reuse, performance, and the remaining pooling call.

## Acceptance

- The M1 fix is **verified against the real baseline**, not a fixture: `windows_active <=
  windows_total` for **all** 58,577 entities, and `windows_active == ir_entity.windows_seen`.
- A per-producer completeness test covering `materialize()` **and** `merge()`.
- Tier 1 green; `verify_tier.sh` exit 0; axRange's `expand_twin_config_raw` +
  `parse_traffic_profile` still accept the output.
