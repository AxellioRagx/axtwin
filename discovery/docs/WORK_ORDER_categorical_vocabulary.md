# WORK ORDER — Pinned categorical vocabulary in the feature manifest

**STATUS:** LANDED (2026-08-04 — see CHANGELOG "Pinned categorical vocabulary in the feature manifest")
**Depends-on:** `WORK_ORDER_feature_vector_sink.md` (LANDED)
**Closes:** `Q6` (categorical vocabulary artifact)
**Authority:** user (2026-08-04)

## Goal

Make a feature archive **self-describing enough to encode from**, so a tensor assembler can build its
first tensor after reading `manifest.json` alone — never by pre-scanning `nodes.jsonl` — and so two
captures encode to the *same* index map.

Today `proto_mix` and `listen_ports` are open, sensor-discovered vocabularies. Their keys are data,
not a record shape. Measured on the reference archive (`data/features-20260803`, 2,382 node rows):

| field | distinct keys | per row | shape |
|---|---|---|---|
| `proto_mix` | 14 | 0–9 | byte-weighted proportions, keys from Zeek's `service` |
| `listen_ports` | 12 | 0–11 | `"<port>/<proto>"` labels |
| `state_mix` | 6 | — | **closed** table (`FLOW_STATE_CLASSES`) — already stable, the contrast that shows what the other two lack |

Two properties make this more than a convenience problem:

1. **Composite keys fragment the space.** Zeek's `service` is a comma-joined *set*. `gssapi,smb`
   appears in 75% of rows while bare `smb` appears in 0.08%; `krb,gssapi,dce_rpc` in 43% while
   `dce_rpc` in 75%. A model sees `smb` and `gssapi,smb` as unrelated dimensions. Splitting on `,`
   would collapse 14 keys → 13 atoms.
2. **Some vocabulary members are capture-specific.** `listen_ports` contains `49668/tcp` — a Windows
   dynamic RPC port, present in 72/2,382 rows (one host, every window). That number will differ on
   the next capture. So **pinning a vocabulary does not by itself make it transfer**: the design must
   handle keys seen at encode time that are not in the pinned map. Unseen keys are the normal case,
   not the error case.

Vocabularies this small (14, 12) do not need a hashed feature space, and hashing would cost the
interpretability §6 explicitly wants. Pin them explicitly.

## Build

1. **Emit the observed vocabulary** into `manifest.json` at sink close — per categorical field, the
   keys **sorted** (byte-stability, `LES-009`) with the row count each was seen in. Sorted order is
   the index map: position in the list *is* the tensor column.
2. **Accept a pinned vocabulary as input**, so a second capture encodes against the first's map:
   `aximprint features --vocabulary <path to a prior manifest.json or a vocabulary file>`. When
   supplied, the emitted manifest must record which vocabulary it was pinned to, so an archive can
   never silently claim an index map it did not use.
3. **Open-world handling — the part that matters.** A key present in the data but absent from a
   pinned vocabulary must never silently vanish, and must never shift another key's index. Give it an
   explicit out-of-vocabulary path, and **count it**: per field, how many rows and how much mass fell
   outside the pinned map, recorded in the manifest. An archive encoded against a vocabulary that
   covers 60% of its own traffic is data with an asterisk, in exactly the sense §5.4 #5 means — the
   asterisk has to travel (`LES-006`).
4. **Manifest is read-first.** `manifest.json` already writes last but must be *readable* first: a
   consumer reads it, builds its index map, then streams `nodes.jsonl`. Nothing about the encoding
   may require a prior pass over the row files. This is the acceptance criterion — state it as a test.
5. **Record which fields are categorical** rather than hardcoding two names in a consumer. A
   downstream reader should learn from the manifest *that* `proto_mix` and `listen_ports` are
   vocabulary-bearing and `state_mix` is closed, not infer it.

## Boundaries — do NOT do these

- **No encoder, no tensors, no model code.** This work order emits the vocabulary and the metadata
  needed to encode. Building the encoder is the consumer's job and a separate effort.
- **No hashing / feature-hashing fallback.** 14 and 12 keys do not justify it, and it forecloses the
  interpretability the synthesizer needs (§6, §9).
- **Do not split the composite service keys in this work order.** Splitting changes what a
  `proto_mix` *value* means (a byte proportion would have to be duplicated across atoms or divided
  between them) — that is a feature-semantics decision, not a vocabulary one. Surface the
  fragmentation in the artifact (mark a key as composite, report the impact) and leave the semantic
  call visible for a follow-up. If you conclude the split cannot be deferred, return
  `DECISION NEEDED:` rather than doing it.
- **Do not modify `ingest/`, `events/`, or `windowing/`.** The vocabulary is observed at the
  persistence boundary. If you believe one of those must change, return `DECISION NEEDED:`.
- **No new runtime dependency** (`duckdb`, `pyyaml` only; stdlib is fine).
- **No schema versioning** on the manifest or the vocabulary shape (`LES-001`).
- **No capture contents** in code, tests, docs, or hand-off. Protocol labels and port numbers are
  structural and may be reported; addresses, hostnames, principals, queries, cert subjects and share
  paths may not (`LES-003`). Tests use synthetic fixtures.

## Validation

- `./scripts/verify_tier.sh` passes.
- **Determinism unchanged:** `verify_determinism.py --source metadata/zeek` — all three invariants
  hold, and the manifest's differing-key set is still exactly `{wallclock}`. The vocabulary is
  derived from data, so it must be identical across two runs of the same source; if it is not, that
  is a determinism break, not an expected difference.
- **The acceptance criterion, as a test:** a consumer that reads only `manifest.json` can construct
  the full index map for every categorical field, and encoding a row requires no knowledge the
  manifest does not carry.
- **Pinning works across captures:** encoding capture B against capture A's vocabulary yields A's
  index map, with B's unseen keys counted as OOV rather than appended, dropped, or renumbered.
- **Real capture:** report the emitted vocabulary per field. Then re-run pinned against a
  deliberately truncated vocabulary and report the OOV rows and mass — proving the counter works
  rather than asserting it.
- Unit tests: sorted/stable index map; an unseen key does not shift an existing index; OOV counted in
  the manifest; a pinned run records what it pinned to; an empty vocabulary field is representable.

## Findings to report

- **The composite-key impact.** How much of the byte mass sits in composite keys, and what splitting
  on `,` would do to the vocabulary and to the per-row values. Enough for a follow-up decision.
- **Vocabulary stability.** Which keys look capture-specific (ephemeral high ports are the known
  case) versus durable. A pinned vocabulary is only useful to the degree its members recur — say what
  fraction of this capture's vocabulary you would expect to transfer.
- **Whether `listen_ports` should be a vocabulary at all**, or whether port and transport are two
  fields that got string-joined. Say which, and why.

## Outcome (2026-08-04)

**Delivered.** `src/aximprint/features/vocabulary.py` with the `categorical` block in
`manifest.json` — per field the index map, **where the index came from** (`observed` / `pinned` /
`closed`), per-key row counts, mass, coverage in mass *and* rows, OOV rows / mass / itemized keys,
composite marking, and `map_sha256` — plus `aximprint features --vocabulary <prior manifest |
vocabulary file>`, a pinned re-run and an empty-pin rejection in `verify_tier.sh` step 3, and
`tests/test_feature_vocabulary.py`. Suite **99 → 168 tests** (129 after the build, 168 after the
review-and-fix round), all passing.

**Build step 1 was amended in flight.** It specified "the keys **sorted**" as the index. Sorting is
right for an *observed* map, but a **pinned** map must be honored verbatim and a **closed** table
keeps its declared order — re-sorting an order you were given renumbers every column after the
insertion point (`LES-013`).

**Determinism: unchanged, and that is the proof.** `verify_determinism.py --source metadata/zeek` —
all three invariants hold, manifest differing-key set exactly `{wallclock}`. Window close
`893fa7ec579fcac7`, synth `5e1c49b1066dcd3e`, feature archive `cb885fe14673579b`: the archive hash
is **identical to the pre-change value**, because `check_features` digests only the four row files
and a manifest-only change cannot move it. All four row files were separately verified
byte-identical to the prior archive.

**Acceptance criterion, as a test:** a consumer builds its full index map from `manifest.json`
alone, never by pre-scanning `nodes.jsonl` — enforced by a test that re-derives every published
statistic from the row files and fails if the manifest cannot be reproduced from them.

**Findings, answered:**

1. **The composite-key impact — deferred, and the deferral is now measured.** 2 of 14 `proto_mix`
   keys are composite, carrying **28.52 % of byte mass across 74.56 % of rows**. Splitting on `,`
   yields 13 atoms, but takes the mean per-row sum from **1.0000 to 1.3082** (max 1.8984), pushing
   **91.4 % of rows out of the [0,1] simplex**; dividing the proportion instead asserts a byte split
   that was never measured. Worse, **52.9 % of rows carry a composite *and* one of its own atoms
   bare**, so either policy silently merges two distinct observations into one column. This is a
   feature-semantics decision, exactly as the boundary said — the manifest now ships
   `composite.index_atoms_if_split` so the follow-up needs no re-derivation.
2. **Vocabulary stability — `proto_mix` transfers three ways, not one.** 3 transport fallbacks
   (`tcp` / `udp` / `icmp`) are maximally durable and carry **45.99 % of mass**; 9 bare analyzer
   labels are durable as strings; 2 composites are combinatorial and are **not** expected to
   reproduce — 14 % of the vocabulary but 28.5 % of the mass, so the durable fraction by key count
   badly overstates the durable fraction by weight. `listen_ports`: 11 of 12 labels are
   assigned-range ports; 1 is IANA-dynamic (≥ 49152) and will differ on the next capture and after a
   reboot.
3. **`listen_ports` should not be a vocabulary — it is a `(port, transport)` tuple string-joined on
   `/`.** 12 labels decompose to 10 ports × 2 transports (60 % of the cross-product), and the
   pairing carries real information — two ports appear under both transports — so it does not split
   into two marginal one-hots either. This is **not** the comma-composite defect: a joined *set* has
   variable arity, is combinatorial in the power set, and cannot be decomposed without deciding what
   happens to its value; a joined *tuple* has fixed arity, is bounded by a product, is value-neutral
   to decompose, and its unbounded tail comes from exactly one dimension — which can be binned. The
   tuple **already exists upstream** as a genuine `Counter[(port, proto)]` and is joined purely to
   be JSON-representable, so the fix belongs where the key is minted → `Q12`, `LES-016`.

**One boundary held under pressure, and one premise was wrong.** The composite split was not
performed and no `windowing/` file was touched — the three defects the round surfaced there are
recorded as `Q12`–`Q14` rather than fixed in place. The premise that failed was the work order's
implicit assumption that emitting an observed vocabulary is cheap: it is not. 36,000 rows minting
fresh dynamic-port labels retained **17.6 MiB pinned *or* unpinned** (the per-key OOV counters are
kept regardless), and one wide row produced a manifest **3.8× the `nodes.jsonl` it described** —
defeating the read-the-manifest-then-stream premise the whole work order exists to establish. Hence
`MAX_KEYS = 512` enforced at insert with disclosed spill counters: **17.54 → 0.10 MiB**, and
coverage honestly reporting **0.003556** where the uncapped run emitted a 16,385-column index at
`coverage: 1.0` (`LES-014`).

**Three choices this round made that are worth ratifying** — not defects, recorded so they are
inherited deliberately:

- **`MAX_KEYS = 512` is this round's own number**, defended against three: 36× the reference
  capture's largest vocabulary, the recurring part of the port space, and worst-case tens of KiB
  against MiB row files. Nothing ratifies it.
- **Retention under the cap is first-arrival, and therefore order-dependent** — documented, not
  hidden. Lexicographic-min retention was rejected because it preferentially discards the *durable*
  keys (`"53/udp"` sorts after `"49152/tcp"`); eviction was rejected because a re-arriving evicted
  key makes the spill counter meaningless. Reproducibility is unaffected — upstream row order is
  pinned (`LES-009`).
- **A pin whose index exceeds the cap is rejected rather than truncated** — a new loud failure, on
  the principle that silently encoding against a prefix of an agreed map is worse than not encoding.
