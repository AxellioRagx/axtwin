# WORK ORDER — a channel for facts with no subject: close `Q29`

**STATUS:** ✅ **LANDED 2026-08-13** (CHANGELOG same date) — `Q29` **CLOSED**. Build steps 1–6 below
all shipped in one pass, plus three review-round fixes (the determinism gate never actually compared
the new channel; a negative delta could reach the sketch with every health counter clean, `LES-005`
recurring; the declared `unit` was discarded before reaching the store, `LES-015` recurring) — see
"What review found" and "Validation" below for what changed from the design as drafted.
**Depends-on:** build step 3 of `docs/WORK_ORDER_zeek_stem_coverage.md` — **not for correctness, for
the file.** Both edit `src/aximprint/windowing/windows.py`, and step 3 deliberately excluded `ocsp`
(the first occupant of this channel) because this design did not exist when it was dispatched.
**Closes:** `Q29`. Does **not** close `Q1`, the symmetric other half — a subject with no address.
**Authority:** **user ruling, 2026-08-13.** Asked how step 3 should handle `ocsp` — admitted to parse
by `Q24`'s third amendment, with no landing shape and an explicit prohibition on inventing one — the
user chose **"answer `Q29` now, build the subject-less channel"** over three alternatives (declare
`ocsp` skipped citing `Q29`; leave it unrecognized; give it a subject via the `files` join). The
design below was proposed by Claude Code the same day and **approved by the user before any code**.
That makes this `Q29`'s option 2, and it makes option 1 (the `files` join) declined rather than
merely unbuilt — see Boundaries.

## The gap, concretely

Six tables in the belief store. Four key on an entity — `node_features.entity`,
`edge_features.src`/`dst`, `assertions.subject`, `identity_bindings.ip` — and the two that do not are
both already spoken for:

- `window_health` is about **sensor completeness**: what the pipeline did and did not see. Putting a
  fitted behavioral parameter there conflates "what did we observe" with "what is the traffic like",
  which is the one distinction that record exists to keep clean.
- `runs` is **per-run** and is the sole member of `NONDETERMINISTIC_TABLES`. A fitted parameter is a
  deterministic function of the data; filing it in the table that is exempt from byte-equality would
  make the determinism gate structurally unable to check it.

So there is no home for a fact that is true of the **capture** rather than of any host in it. Every
ingress requires a subject: `ServiceEvent` returns early on `if not ev.ip`, `IdentityEvent` binds an
address, `FlowEvent` has two, and the assertion tuple leads with `subject`.

**This is bigger than `ocsp`.** DESIGN_BRIEF §9 declares `tfc` a first-class output whose content is
"fitted **intensities/schedules/seeds**", and most of those are population-scoped by nature: a
per-protocol request rate, a diurnal shape, a think-time distribution, an RNG seed. Until this
channel exists, the `tfc` half of the synthesizer has nowhere to write, whatever the learner layer
eventually fits.

## Build

1. **`events/model.py` — `ProfileEvent(ts, parameter, value)`.** A measurement with a timestamp and
   **deliberately no subject field**. Not an optional-subject variant of an existing class: the
   absence is the point, and a nullable `ip` would reproduce exactly the ambiguity `Q29` names.
2. **`ingest/zeek/plugin.py` — `_ocsp`.** Yields `ProfileEvent(ts, "ocsp_cache_lifetime",
   nextUpdate − thisUpdate)`. `ocsp` joins `_PARSERS`, `_COLUMNS`, `KNOWN_PARSED_STEMS` and
   `_SCHEMAS`, and gets **no** `SERVICE_PROJECTIONS` entry — it is not a service fact.
   Verified header, do not re-derive: `ts id hashAlgorithm issuerNameHash issuerKeyHash serialNumber
   certStatus revoketime revokereason thisUpdate nextUpdate`. **No addresses at all.**
3. **`windowing/windows.py` — `PROFILE_PARAMETERS` + `WindowResult.capture_profile`.** (Built as
   `capture_profile`, not the `profile` this section originally named — see "What review found"
   below.) A declared table, one entry per parameter: what it means, its accumulator, its unit. Same
   idiom `SERVICE_PROJECTIONS` established one round earlier and for the same reason — **a parameter
   is declined by name, never by omission**, and an undeclared parameter is a counted health fact
   rather than a silent drop. `WindowResult` gains a fourth channel beside
   `nodes`/`edges`/`identity_delta`/`health`.
4. **Accumulation reuses `sketches.Quantiles`.** A cache lifetime is a distribution, which is what
   the sketch bank is for. It inherits `Q8`'s O(input)-not-O(output) caveat honestly rather than
   growing a second, differently-caveated accumulator.
5. **`store/belief_store.py` — `profile_facts(window_start, parameter, stat, value, unit)`.** (Built
   with a fifth column, `unit`, this section did not originally specify — see "What review found"
   below.) **No subject column.** Not in `NONDETERMINISTIC_TABLES`.
6. **`sinks/store_sink.py`** writes it, inside the existing single-transaction window write.

## What review found, before landing

Three gaps, all fixed in the same pass rather than left for a follow-up:

1. **The determinism gate had two halves, and only one of them grew with this channel.**
   `scripts/verify_determinism.py`'s `canonical_windows()` — the fixture rendering both determinism
   passes are diffed against — omitted `capture_profile` entirely, while `belief_store.py`'s own
   module docstring already claimed the table was byte-deterministic. That claim was never actually
   being checked. Fixed by adding the key to the rendered payload, and by growing the fixture with
   real `ocsp.log` rows so the channel is exercised, not merely parsed.
2. **A negative delta could reach the sketch with every health counter reporting clean —
   `LES-005` recurring verbatim, through the sensor's own payload this time rather than the ingest
   reorder buffer.** `_ocsp`'s two timestamps are each individually guarded (`_num`, never `_f`), but
   nothing gated the *sign* of their difference — a responder's own clock skew would have published a
   corrupted cache-lifetime reading into the sketch wearing an observed face. Fixed with a `>= 0` gate
   in `_add_profile`; a refused reading is counted (`profile_values_refused`), never silently dropped
   or clamped.
3. **A declared `unit` was computed and then discarded before it ever reached the store —
   `LES-015` recurring.** Build step 5 as originally drafted (the four-column signature above) never
   carried `PROFILE_PARAMETERS`'s own declared unit past the accumulator. Fixed: `profile_facts`
   gained a fifth column, looked up from `PROFILE_PARAMETERS` at flatten time
   (`belief_store.profile_facts_rows`, the one place this shape is computed) rather than carried on
   `capture_profile` itself, which would let a `stat="unit"` row corrupt a `DOUBLE` value column the
   moment anything iterated the per-stat dict generically.

## Boundaries — what this deliberately does NOT do

- **It does not write the `tfc` compiler.** `synth/` stays a stub. This lands the store *shape*;
  fitted intensities and schedules need the learner layer §6 designs and nothing implements. After
  this, `tfc` stops being **architecturally** blocked without becoming buildable — say it that way
  in the CHANGELOG, because the difference is the whole honesty claim.
- **It does not do the `files` join** (`Q29` option 1: attribute an OCSP response to the client that
  fetched it via `fuid`). Declined, not deferred — it costs a stateful cross-log join with its own
  ordering and window-boundary problems, and `Q29`'s own text says it "arguably invents per-host
  precision the evidence does not have." If someone wants per-host cert-validation behavior later,
  that is a new question, not this one reopened.
- **It does not give `ProfileEvent` a subject**, including a reserved sentinel like `__capture__`.
  A magic string in the entity namespace would collide with `node_features` joins and would re-encode
  as a value the very thing this channel exists to represent structurally.
- **It does not add a sixth feature-archive artifact.** DESIGN_BRIEF §5.4 declares five, and a
  population parameter is not a node or edge feature. Whether the learner layer wants profile facts
  as training input is a real question and is left open — flag it, do not answer it here.
- **It does not touch `SERVICE_PROJECTIONS`** or any existing decline.

## Two decisions recorded with their reasons

**Per-window, not per-run.** `Q29` frames these facts as population-scoped, which argues for `runs`.
Rejected: §10's overfitting guard is *"learn on one window, validate drift on a held-out window"*, and
that is impossible if the profile only exists once per run. Per-window aggregates upward on demand;
per-run does not decompose downward. This is the same shape D5 chose for `unparsed_log_types` and for
the same reason — put the fact at the finest resolution that is **honestly** knowable, and let the
consumer aggregate.

**A new table, not a reserved subject in `assertions`.** Reusing `assertions` with
`subject = "__capture__"` is materially cheaper — no new table, no new write path, and `synth` reads
one place. Rejected because the absence of a subject would become a *value* that every consumer must
know to filter, rather than a property of the schema. `LES-006`'s distinction applies: a sentinel
that is also a valid-looking entity cannot say "no subject" and "an entity named `__capture__`" apart.

## Validation

- Unit: `ocsp` parses on both transports; an undeclared parameter is counted, not dropped; the
  quantile output is byte-stable across two runs of the same input.
- **Tier 2 will not see any of this unless the fixture grows.** `scripts/verify_determinism.py`'s
  `build_synthetic_capture()` emits six log types and none of them is `ocsp`. Extend it, or the
  determinism and transport-equivalence gates pass while exercising nothing — the finding that came
  out of build step 2b and is now the standing caveat on this whole area.
- Tier 3: the baseline at `/tmp/aximprint-zeek-test/` has `ocsp` in 12/12 windows. **Measure the
  actual distribution of `nextUpdate − thisUpdate`** before anyone describes it as a usable `tfc`
  parameter — `Q26`'s lesson, applied to this channel's first occupant. A single-valued distribution
  across all 12 windows would mean the parameter is a CA policy constant, not a property of this
  network, and that would be worth knowing before building a second occupant on the same assumption.
  **MEASURED 2026-08-13: exactly that.** 1031 events, three distinct deltas overall — but segmented
  by `issuerNameHash`, **each issuing CA emits a single constant value**. `ocsp_cache_lifetime` is a
  **CA policy constant**, not a network-behavioral signal, at this vantage. This is materially
  weaker justification for the channel than the ruling assumed when `ocsp` was admitted — recorded
  here, in `Q29`'s closure, and in `windowing/windows.py::PROFILE_PARAMETERS`'s own comment, so
  whoever adds this channel's second parameter inherits the measurement rather than the assumption.
  Does not reopen anything: the *channel* (a place for a subject-less fact to live) is justified
  independent of whether any one parameter turns out to vary by network — but a `tfc` compiler that
  someday reads `ocsp_cache_lifetime` expecting a network signal should read this first.

## What opens if this lands

`Q29` closes and `ocsp` becomes the channel's first occupant. The open follow-on is not named as a
`Q<n>` yet and should be if this lands: **which of `tfc`'s declared contents are population-scoped,
and which are per-entity after all?** A per-protocol request rate probably belongs here; a per-host
think-time distribution probably belongs on the node. Nobody has enumerated `tfc`'s fields against
that split, and the answer decides how much of §9 this channel actually serves.
