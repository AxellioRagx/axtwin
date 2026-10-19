# Work order — the ML layer: where inference belongs, and what the first evaluation found

**Status:** DRAFTED 2026-08-19, **not started**. **D2 RULED 2026-08-20 by the user: Tier A
(numpy/scipy/scikit-learn) as a CORE runtime dependency**, not an optional extra — the deliberately
thin `duckdb`+`pyyaml` floor is deliberately raised. D1 and D3–D6 still need a ruling, and **D6
remains blocking for D1**. Contains a **completed
negative evaluation** (§4) whose result should be read before scoping any identity work.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Authority:** design discussion between the user and Claude Code, 2026-08-19. Nothing ratified.

## 1. The framing that settles the architecture

The user's question — *"once we add ML into the pipeline, where does that inference plane fit?"* —
has a smaller answer than it looks, because **inference is not a new plane. It is a new `source` in
the evidence plane.**

`assertions` already carries `evidence_class`, `confidence` and a namespaced `source`. The live
ten-day store shows the pattern in use:

```
observed 1,810,538 · referenced 503,723 · imputed 273,010
extractor:role-census → 273,010 imputed assertions
```

`extractor:role-census` **is** a rule-based inference engine writing `imputed` assertions.
`model:identity-gnn@v3` is the same shape with different internals. This is why the ML layer needs
no new plumbing: because merges are applied at `snapshot()` read time, a model that emits
`same_entity_as` assertions flows through machinery that already exists.

## 2. Three ML roles, three insertion points

| role | writes to | what it does |
|---|---|---|
| **Resolver** | evidence plane (appends assertions) | infers identity/role/segment; composes like any other evidence |
| **Rival predictor** | conclusions plane (a *parallel* IR) | GNN emits a predicted IR; `compare()` diffs predicted vs observed. Never writes into the observed IR — that separation is why a versioned artifact exists |
| **Judge** | nothing structural | reads a PIT series, weights absence vs coverage noise (DESIGN_BRIEF §8, D8) |

## 3. Invariants any of them must satisfy

**Append, never update.** The store is append-only and that is what makes `snapshot(as_of)`
reproducible. ML **appends** assertions with `source=model:<name>@<version>`, exactly as
`extractor:role-census` does. Nothing mutates; a later assertion supersedes by being later.

**The determinism tuple gains a term.** Window close is a ratified pure function of
`(events, resolver state, code build)`, pinned by `runs.code_fingerprint`/`git_commit` and checked
by `scripts/verify_determinism.py`. A model adds **model identity + weights hash**. Without it two
runs differing only by model version are indistinguishable in the store.

**`compare()` must gate on model identity.** This is the exact mirror of `D4`. D4 taught the
comparator to separate `observed_absent` from `not_observed` — *"the network changed"* vs *"we were
blind."* ML introduces a third: ***"we now think differently."*** A model upgrade that relabels
15,000 hosts must not read as network drift. The gate exists (network identity, vantage-set); this
is one more term in it.

**Do not add a fourth `evidence_class`.** The vocabulary is closed and lives in the **versioned**
schema, so widening it is a real schema event. `source` already carries the distinction. What
`imputed` genuinely conflates is *volatility* — the RFC1918 segment fallback never changes unless
code does, model output changes whenever weights do — and that is a `source` + fingerprint
question, not a class question.

**A generation marker, or feedback contamination.** If round N+1 reads round N's output
undifferentiated, the model trains on its own guesses and confidence inflates with no new
observation behind it. Models must declare what they read (`observed`-only, or `imputed` up to
generation k). Nothing enforces this today.

## 4. First evaluation — identity linking. **Negative. Do not skip this section.**

**Question:** can unsupervised behavioural fingerprinting recover entity groupings that *declared*
identity evidence (dhcp/kerberos/ntlm) found?

**Ground truth (free, no labelling):** `entity_roster` — 105 named entities, **38 span ≥2
addresses**, giving **421 true positive pairs**. Fingerprints used **no** identity evidence, no
`same_entity_as`, no `bound_dns`. Pure Python + duckdb, **no dependencies added**.

**Three iterations, each correcting the last:**

| v | features | result |
|---|---|---|
| 1 | activity stats (flows, bytes, proto_mix, subbucket profile) | AUC 0.695 |
| 2 | + peer-set Jaccard, stratified by data volume | AUC 0.437 (≥1 win) → 0.992 (≥10 win, **but only 4 positive pairs — meaningless**) |
| 3 | machine-identity features (`runs_software`, `listens_on`, `serves_known_service`), segment blocking | AUC 0.741 (richness ≥2), **precision@1,2,3,5 = 0.00**, best precision anywhere **0.36** |

**The decisive result: the five highest-confidence predictions are all wrong.** AUC ~0.7 says
"better than chance across the ranking"; precision@5 = 0 says "the pairs it is most sure about are
false." A fail-closed design operates at exactly that end. Mechanism: small-set Jaccard saturates —
two addresses each with one listen port and one peer, matching, score 1.0.

**And a false merge is unrecoverable.** A missed merge leaves two address-keyed entities (honest,
the status quo). A false merge fabricates a host that never existed and corrupts every downstream
drift claim. The operating point must be high precision, and there isn't one.

**Root cause — evidence density, not model quality.** Of 131 roster addresses: **41 have zero**
address-keyed behaviour rows; the other 90 have a **median of 3 windows**; only 27 have software
evidence, 36 have listen ports. The hosts needing linkage are DHCP-ephemeral **by nature** — a
longer capture does not help, because each lease is still short.

**Conclusion:** unsupervised behavioural fingerprinting cannot solve identity on this data. Not
"needs a better model" — the information is not there to infer from. **Parse `radius` and `rdp`
first** (`docs/WORK_ORDER_zeek_stems_wide_vantage.md`): they *state* MAC→address and computer-name
deterministically, at 0 dependencies and 0 model-versioning cost.

## 5. Where the data IS dense — and these are untested

The sparsity above is a property of **address fragments**, not of hosts.

| population | count | median windows | ≥100 windows |
|---|---|---|---|
| All entities | 58,667 | 1 | 1,641 |
| **Segment-placed** | **514** | **21** (mean 89) | **171** |
| Distinct edges | 98,360 | 1 | **5,777 at ≥24** |

| task | data | verdict |
|---|---|---|
| Identity linking | 131 addrs, median 3 windows | **starved — tested, failed** |
| Role discovery | 514 placed, only **85 roled** | dense, untested |
| Segment inference | 98 K edges; **323 local hosts unplaceable** | dense, untested |
| Absence / link prediction (D4) | 5,777 stable edges | dense, untested |
| Behavioural changepoint | 1,641 entities × 241 windows | dense, untested |
| **`tfc` parameter learning** | 241-window series, all hosts | dense, untested, **fills an unbuilt output** |

**`tfc` is the under-weighted one.** It is a declared pipeline output that has never been built —
rates, schedules, cache lifetimes, seeds — and those are subject-less capture-wide temporal facts.
`profile_facts` holds **1,205 rows** from a single occupant (`Q29`). Learning diurnal profile,
periodicity and burst structure from a 241-point series is dense-data unsupervised work with **no
identity dependency**, and it fills a hole in the deliverable rather than improving something that
already works.

**Counterpoint that cuts the other way:** identity was the task with *free ground truth*. Role
discovery has 85 catalog-matched examples; segment inference has 430 placed hosts; changepoint and
`tfc` have **none**. The tasks with the best data have the worst evaluation story, so success
criteria must be defined **before** building, not after.

## 6. Candidate methods, all unsupervised, all seedable

- **Role discovery** — RolX (recursive structural features + NMF), HDBSCAN on service+degree
  vectors, fan-in/fan-out asymmetry
- **Segment inference** — community detection on the conversation graph (Louvain/Leiden;
  **Louvain is order-sensitive and needs a deterministic variant** — determinism is ratified)
- **Absence** — truncated SVD or VGAE link prediction: `P(edge exists)` gives a principled prior
  for whether an absence is meaningful, which D4 currently answers from coverage alone
- **Changepoint / anomaly** — BOCPD or CUSUM per host series; Isolation Forest / LOF
- **`tfc`** — periodicity detection, seasonal decomposition, burst modelling

## 7. Dependency tiers — a **gated** decision (CLAUDE.md risk gate: "add a runtime dependency")

| tier | packages | size | covers |
|---|---|---|---|
| **A** | numpy, scipy, scikit-learn | ~100 MB | every task in §5 |
| **B** | + torch (+PyG) | ~2–3 GB | learned embeddings, DGI, VGAE proper, temporal GNNs |

Current runtime deps are **`duckdb` + `pyyaml`** — deliberately thin. At 58 K nodes / 109 K edges
this is small: Tier A runs in minutes on CPU, no GPU. Honest read: **Tier A covers all six tasks at
useful quality**; torch earns its place only for the GNN-vs-observed comparison the IR was designed
to support.

## 8. Decisions needed

- **D1** — Which task first? Proposed: **`tfc` parameter learning**, then role discovery.
- **D2** — **RULED 2026-08-20 (user): Tier A, as a CORE dependency.** Not the `[ml]` extra that
  was offered and not `[acquire]`'s pattern — `numpy`/`scipy`/`scikit-learn` join `duckdb`/`pyyaml`
  as required. Consequence to honour when this builds: a bare `pip install -e .` gets ~100 MB it
  may never use, and the "deliberately thin" note beside the dependency list in `CLAUDE.md` and
  `DESIGN_BRIEF` must be updated rather than left contradicting the manifest.
- **D3** — `source` grammar for model output. Proposed: `model:<name>@<version>` + weights hash in
  `runs`.
- **D4** — Generation marker to prevent feedback contamination. Proposed: models declare the
  maximum generation they read.
- **D5** — `compare()` gains a model-identity gate. Proposed: yes, refuse across model versions.
- **D6** — Success criteria for tasks with no ground truth. **Unresolved and blocking for D1.**

## 9. Reproduce the evaluation

Store: `/home/rocky/harvest-corpapcn-10d/beliefs.duckdb` (241 windows, 2.59 M assertions).
Method is fully described in §4; the prototype scripts were scratchpad-only and are **not**
committed (`LES-003` — they are throwaway, the numbers are the artifact). Re-deriving them from
§4 is a half-hour of work and a useful check on whether the finding survives a second author.
