# Work order — move the PIT snapshot boundary from the conclusions plane to the evidence plane

**Status:** **RATIFIED 2026-08-20 by the user — the full reversal, D1–D5 as proposed.**
**Build step 1 LANDED 2026-08-20** (`8550f2f`); **steps 2–5 remain**, and step 2 is where behaviour
first changes. This is the project's **second** reversal of a ratified decision and the first to
reverse a §0.2 schema ratification's boundary (the IR schema itself is untouched — see
`Supersedes` below).
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** the *snapshot boundary* chosen by `docs/WORK_ORDER_network_ir.md` D1–D9 (ratified
by the user 2026-08-14). It does **not** supersede the IR schema, its versioning, `cast`, `drift`,
`visibility`, or `merge --axis vantage`. Authority for the original placement: Claude Code proposed,
user ratified. The reversal is driven by a measurement neither of us had at the time.

## The user's framing, which is the design

> *"metadata ingest → evidence from beliefs → [PIT_IR] → cast twin; ML models will ingest derived
> evidence and output new evidence to be updated into beliefs; evidence MUST be PIT snapshotted -
> the window per snapshot is user specified."*

The insight underneath it: **PIT snapshots make sense in the evidence plane, not the conclusions
plane** — because evidence composes and conclusions do not.

## The measurement that makes this necessary

Entity resolution is a function **over evidence**. `BeliefStore.snapshot()` is the resolve
boundary — its own docstring says *"Entity merges are applied HERE."* Everything before it is
bounded evidence; everything after is conclusions. Today `materialize()` calls it and keeps going
in one pass, so snapshotting and resolving are welded together.

Slice **before** resolving and the linking evidence is stranded. Measured on the ten-day
corp-apcn capture (241 one-hour windows, all 12 streams):

| | |
|---|---|
| Host-named entities across the whole span | **105** |
| Windows in which each is **named** | median **2** of 241 |
| Named in ≤3 windows | **70%** |
| Windows in which each is **active** | median **124** |
| **Activity : naming ratio** | **48×** |

A typical named host is active ~124 hours and announces its name in **2** of them. Per-window PITs
put the name in ~2 slices while the host appears in ~122 others as a bare address, and `merge`
cannot join them — it unions rows that were already keyed, with no evidence to re-key from
(`LES-118`: 3,237 entities merged vs 3,231 materialized over the same span).

Corollary, same capture: **22 addresses are claimed by more than one entity, and one address served
12 different hosts** over ten days. Address-keyed identity is not sound here.

## Target pipeline

```
sensor metadata
      │ ingest --window W_ingest
      ▼
EVIDENCE PLANE — beliefs.duckdb          append-only, provenance-marked, live working set
      ▲                    │
      │ append assertions  │ read (filtered by evidence_class + generation)
      │ source=model:..@v  │
   ┌──┴──────────┐         │
   │  ML round(s)│◀────────┘
   └─────────────┘
      │ snapshot --window W_snap        ◀── NEW, user-specified
      ▼
EVIDENCE PIT SERIES                      frozen, id'd, COMPOSABLE
      │ compose(contiguous run)          plain union — no resolution yet
      ▼
   resolve + aggregate                   today's snapshot() + materialize()
      ▼
   IR (conclusions, versioned 1.2)
      │ cast
      ▼
   twin_config + tfc + tap
```

The load-bearing change is **splitting `materialize()` at the resolve boundary**. Compose evidence
PITs then resolve, and the answer equals materializing the whole range — the 48× loss disappears
because resolution still sees all the evidence at once.

## Decisions needed

**D1 — does the IR stay persisted, or become derived-on-demand?** If the durable artifact is the
evidence PIT, the IR is reproducible from (evidence PITs + code fingerprint + model id). Note the
cost: this ten-day IR took **50 minutes**. Cost is O(range), so hourly IRs are cheap and wide ones
are not. Proposed: IR stays *persistable* but is no longer the snapshot unit; add a cache keyed on
that triple, invalidated when any term changes.

**D2 — does the IR stay versioned?** Yes if external consumers still read it (twin compiler, drift
comparator, GNN harness, diagram generator) — the `LES-001` discriminator is unchanged: *a schema
is versioned when something OUTSIDE its producer depends on its shape.* Proposed: unchanged at
`1.2`.

**D3 — what is an evidence PIT, physically?** A partition of the belief store bounded to
`[t0, t1)`, with an id, a vantage, and a code fingerprint — a **projection of rows, not of
conclusions**. Proposed: a new store analogous to `IRStore`, write-once per id.

**D4 — `merge` semantics.** `--axis time` should merge **evidence** PITs (a union of rows, exact)
rather than IRs (fragments identity). `--axis vantage` remains an IR-level operation and remains
necessary — separate taps produce separate stores. Proposed: split the verb by plane.

**D5 — snapshot window constraint.** `W_snap` must be ≥ `W_ingest` and ideally a multiple of it;
a snapshot finer than the store's own windowing cannot be honoured. Proposed: refuse, do not round.

## Build steps

1. ~~Split `materialize()` into `snapshot_evidence()` and `resolve()`.~~ **LANDED 2026-08-20**
   (`8550f2f`). `EvidenceSnapshot` is a frozen dataclass of ROWS holding no live connection, and
   `materialize()` is now exactly `resolve(snapshot_evidence(...))`. Proved byte-identical **on
   the real ten-day store**, not a fixture — same SHA-256 over 58,563 entities / 109,173 edges /
   755,567 identity rows / 38,252 unseen. Three properties are pinned by tests: the composition
   is identical, `resolve()` is pure, and **resolving still works after the store connection is
   CLOSED** (stronger than inspecting fields — a connection could hide inside any of them).
   `_unseen()` became pure (its query moved up to `_referenced_bindings()`) and the empty-span
   refusal moved to the evidence side. **`BeliefStore.snapshot()` still applies `same_entity_as`
   internally, so resolution is NOT yet past the boundary** — that is step 2, and step 1 was kept
   a pure refactor so the next diff stays readable.
2. Evidence PIT store + `aximprint snapshot --window W`.
3. `compose()` over a contiguous run of evidence PITs; property test **compose→resolve ≡
   materialize(span)** on the real ten-day store, not a fixture (`LES-118` rule 2).
4. Re-home `merge --axis time` onto evidence PITs; keep `--axis vantage` on IRs.
5. IR cache keyed on (evidence PIT set, code fingerprint, model id).

**Acceptance:** `compose(snapshots(W1..Wn)) → resolve` equals `materialize(since=W1, as_of=Wn)`
**exactly** on the ten-day store — same entity count, same key kinds, no fragmentation.

## What does not change

`cast`, `drift`'s absence classification (D4/D4a), `visibility`, the `ir_unseen` census, the schema
itself, and the vantage/network commensurability gates. This moves *where a snapshot is taken*, not
what a conclusion means.
