# SPEC — the network IR: a point-in-time structural representation

**STATUS:** **VERSIONED — schema `1.4`.** This is the only versioned schema **in this project's
data plane**, and the only place an `ir`-owned version field is permitted (`LES-001`, amended).
Everything else — the event model, feature shapes, the plugin contract, the belief store — stays
**incubating** and carries no version. Do not read this document as precedent for versioning any of
them; read `LES-001`'s amendment and DESIGN_BRIEF §0.2's for the discriminator that put this
artifact on the other side of the line, and nothing else with it.
**Authority:** **user**, 2026-08-14 — the §0.2 ratification. Schema design and this document:
Claude Code, from `docs/WORK_ORDER_network_ir.md` D1–D9 + D4a/D4b, **ratified as drafted** the same
day. **`1.4` (2026-09-04): user**, `docs/WORK_ORDER_reference_ingest.md` §11 (D1/D2/D-a taken as
recommended).
**Genre:** a **formal spec** (CONVENTIONS §2), the same footing as `AXAPI_CONTRACT.md`: a
version-locked description this project's code must satisfy. **This document is the authority**;
`src/aximprint/ir/schema.py` is its executable half, and `tests/test_ir_schema.py` fails if the two
disagree. A schema defined only in prose is a schema nothing checks (`LES-014`).

**Three narrow exceptions to "the only versioned schema" exist, and none of them is an IR
version.** Two were added 2026-09-04 (Authority: user, `docs/WORK_ORDER_reference_ingest.md`
D6/D-b and D-e): the cast **provenance sidecar** declares `axr-cast-provenance/1` and the
**topology export** declares `axr-topology/…` (at **`1.5`** since 2026-09-05). The third is the
**operator assertions file**, `axr-assertions/1` (2026-09-05, S2; ratified in axRange
`docs/design/14-operator-assertions.md` §3), which is an INPUT rather than a cast output and is
versioned for the same reason. All three cross the repo boundary on a lifecycle that does not move
with this build, which is `LES-001`'s own amended discriminator applied honestly rather than a
widening of this ratification. The concrete reason each exists: without a declared sidecar version,
2026-09-04's `defaulted` → `compiler` rename is indistinguishable at that reader from a bug in
its own parsing; the topology export previously republished *this* document's `schema_version`,
so "the export went to 1.4" would either have bumped the IR or meant nothing; and the assertions
file is written by a consumer this repo cannot import, so neither side could tell a schema change
from a parser bug. `tests/test_ir_schema.py` still fails the suite if the IR's own constant name
appears outside `aximprint.ir`, and all three strings are deliberately spelled differently so that
guard keeps meaning exactly what it says; a second test pins each to one declaring module and a
third asserts the discovered set of version-shaped literals is exactly the ratified three plus
this document's own (`LES-141`).

### The three cast-output files, and what each one carries

A cast writes one directory; `scripts/install_cast_as_twin.py` and `aximprint install-cast` copy
it into axRange's twin-config catalog under a stable id. What travels:

| file | schema | what it is |
|---|---|---|
| `<id>.yaml` | (axRange's `twin_config`) | the deployable projection |
| `<id>.provenance.json` | `axr-cast-provenance/1` | per-field evidence class, signal, confidence + the `population` block |
| `<id>.topology.json` | `axr-topology/1.5` | the observed SUPERSET: full node census, conversation graph, `services[]`, `observations` — and, since `1.5`, `cast_invocation` |
| `tfc-cast.yaml` / `tap-cast.yaml` / `personas/` / `scripts/` | (axRange's own) | the traffic profile, the tap plan, and what they reference |

**`cast_invocation` (`axr-topology/1.5`, 2026-09-05) is what makes a bundle reproduce its own
cast.** Found on axRange's Console apply path: `--relocate` survived only as per-field
`transformation:` records inside the *sidecar* — a different file, recording what CHANGED rather
than what was ASKED FOR — and `--max-count` / `--no-chatter` / `--assertions` survived nowhere at
all, so a recompile driven from the bundle silently reproduced the SOURCE network's CIDRs. The
block is `{pit_id, imprint_id, relocate, max_count, chatter, assertions, code_fingerprint}`, every
value the EFFECTIVE one rather than the switch (`chatter` is `not --no-chatter`; `imprint_id` is
the resolved id). Two deliberate omissions: the **store path is not recorded** — a lab-local
filesystem path is neither portable to the consumer nor a property of the cast, and the consumer
supplies the store from its own configuration — and **`assertions` travels as a BASENAME**, since
the file's identity matters and its directory does not. `code_fingerprint` is the build that CAST,
which is not the PIT's own (already in the sidecar, and naming the build that MATERIALIZED it);
it therefore moves with any edit to `src/aximprint/**.py`, which is the field working rather than
a determinism break — two casts from ONE tree are byte-identical. The key is **absent**, not
`null`, when a library caller builds a bundle without one: `{}` could not tell "no flags" from
"not recorded", and "no flags" is itself a reproducible invocation.

## What this is

One row-set per **PIT** — a point in time, as-of which the network is described. A PIT is what
`BeliefStore.snapshot(as_of)` knows, frozen, named, and written down.

Four consumers, on independent lifecycles, and the artifact is shaped for none of them in
particular:

| consumer | needs |
|---|---|
| the **twin compiler** (`synth`) | entities, segments, roles, edges — the deployable structure |
| the **drift comparator** | two PITs, joinable on a stable key, with coverage |
| a **GNN evaluation harness** | model output and evidence output in one vocabulary, distinguishable |
| **topology diagram** generation | entities, segments, edges |

It is **the structural half of DESIGN_BRIEF §10's own decomposition** — §10 splits drift into
structural and behavioral, specifies that behavioral drift is compared *in feature space*, and
never named the artifact for the structural half.

## What this is NOT

- **Not a second source of truth.** The append-only evidence log is the source of truth, the belief
  store its rebuildable projection, and a PIT a **frozen, re-derivable view of that projection at
  one instant**. Never edited in place. Never queried in place of the store for a question the
  store can answer.
- **Not a replacement for the belief store**, which stays internal, unversioned, and free to change
  — precisely *because* only this artifact crosses a boundary.
- **Not a behavioral-drift mechanism.** See §10 above.
- **Not a place for compiler defaults.** See "Evidence classes".

## Version history

| version | change | class |
|---|---|---|
| `1.0` | initial | — |
| `1.1` | `ir_entity.locality` added | **additive** → MINOR |
| `1.2` | `ir_pit.vantage`/`.network`/`.vantages`/`.merge_axis`/`.merged_from`, `ir_entity.vantages`, and the `ir_unseen` table added | **additive** → MINOR |
| `1.3` | `ir_traffic_profile` and `ir_capture_profile` tables added | **additive** → MINOR |
| `1.4` | `EVIDENCE_CLASSES` gains `asserted` and `imported` **and becomes a precedence order**; `ir_entity.listen_ports`/`.dial_ports` (added 2026-08-27, never bumped) recorded here | **additive** → MINOR |

**Why `1.4` exists, and why it carries two changes rather than one.**
`docs/WORK_ORDER_reference_ingest.md` D1/D2/D-a.

*The first change.* An operator's hand-authored fact and an out-of-band directory export are both
evidence, and neither is `observed`, `referenced` or `imputed`. `referenced` in particular was
doing two jobs it cannot both do: here it means **second-hand evidence from inside the capture** —
a DNS answer, an SPN, a redirect, an XFF header, the class behind `ir_unseen`'s referenced-but-unseen
census — and that is *weaker* than seeing a host act, while a directory export is *stronger*. One
class cannot hold one rank for both. `asserted` and `imported` join on either side of `observed`,
and **`EVIDENCE_CLASSES` is now declared in precedence order so the tuple IS the ranking** — a
separate rank table could drift from the vocabulary it ranks (`LES-101`), and `evidence_rank()` is
its only reader. Additive by exactly the argument `UNSEEN_REASONS` already records: a value a
consumer does not recognize is ignorable, while a redefined one is not.

**One thing a `1.3` reader must be told rather than protected from.** The tuple now carries a
**rank**, so a reader that hardcoded "`observed` is the strongest class" mis-ranks a `1.4` artifact.
No version check can catch that — the column is the same column — so it is disclosed here, in the
reading rules below, and in the two new classes' own descriptions. `compiler` (renamed from
`defaulted` the same day) stays **out of the IR** on the argument the "Evidence classes" section
already makes, and `evidence_rank()` refuses it: a sidecar-only label must never be ranked against
evidence.

**A third thing `1.4` records without changing a column: `role_evidence_class` became a real
value** (2026-09-05). It was written as `"imputed" if role else None` for as long as the port-signature census
was the only producer of a role. The reference-ingest channel is the second (an operator's
`override: {field: role}` lands in the belief store as `asserted`), so the column is now resolved
per entity, and the reading that lost is still in the store. **No version rule covers this**: the
column, its name and its type are unchanged, and only its *possible values* widened — which is
exactly why `EVIDENCE_CLASSES` is a declared vocabulary rather than prose. A reader that hardcoded
`imputed` for roles reads a `1.4` artifact wrong in the same way one that hardcoded "`observed` is
strongest" does, and it is disclosed here for the same reason.

*The second change is a correction.* `ir_entity.listen_ports` and `.dial_ports` were added on
2026-08-27 (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C9) and documented in the table below, but
**the version was never bumped** — against this document's own additive→MINOR rule. Naming them in
`1.4`'s entry is deliberate over the two alternatives: retro-dating a `1.4` to the columns and
minting `1.5` for the classes would create a version no artifact ever carried, and bumping only for
the classes would leave the omission unrecorded. **The consequence, which is the part a reader
needs:** stores written between 2026-08-27 and this bump say `1.3` **while already carrying those
two columns**, so those two — and only those two — must be detected by **column presence**, never by
version. Verified on disk 2026-09-04: six CorpAPCN IR stores written before 2026-08-27 say `1.3` and
lack the columns; the 2026-09-02 re-materialization of the same capture says `1.3` and carries them.

**Why `1.3` exists.** `docs/WORK_ORDER_traffic_profiles.md`: the IR carried structure — hosts,
segments, edges, roles — and nothing about what a host **did** over the span it was observed.
`drift` could report a host arriving or leaving but not that a host stayed and changed shape,
and `cast`'s `tfc` output carried a role→persona map with no rate, no schedule, no seed — a
function of what a role *is*, never of what the network *did*. `ir_traffic_profile` closes the
per-entity half (a behavioral profile aggregated from `node_features` over the PIT's span, under
the resolved entity — the same `canon` map `ir_entity` already uses); `ir_capture_profile` gives
`profile_facts` (`Q29`'s subject-less channel, previously written and read by nothing but a row
count) its first reader. Both additive: nothing existing changed, removed, or was redefined.

**Why `1.2` exists.** `docs/WORK_ORDER_visibility_and_vantage.md`, ratified 2026-08-17: a PIT is
**one tap point's view**, and nothing recorded which tap. That gap gave the comparator two failure
modes and the second is the dangerous one — re-harvesting one tap into a new directory was falsely
*refused* (`source` differs), and two *different* taps writing to the same path were falsely
*accepted*, with their view difference reported as network drift. `vantage` closes it; `network`
(operator-declared — nothing in traffic identifies it) separates *which network* from *which
capture*; `vantages`/`merge_axis`/`merged_from` describe a PIT built by combining others; and
`ir_unseen` carries the referenced-but-unseen census DESIGN_BRIEF §8's visibility model needs and
which the IR had been dropping — 2,418 addresses on the 2026-08-11 baseline.

Additive throughout, so a `1.1` reader keeps working, which is the point of MINOR existing.

**Why `1.1` exists, and why it was a bump rather than an edit.** `locality` was found missing by
materializing a real PIT and reading the output — not by review. Without it, `segment IS NULL`
conflates two entirely different populations: an internet peer, and a **local** host on nothing
provisionable (IPv6 link-local, multicast). On the 2026-08-11 baseline that is **117 local hosts
against 2,988 external ones**, and a comparator filtering the monitored network on
`segment IS NOT NULL` would have silently dropped all 117 with no way to notice — precisely the
`LES-006` failure this artifact exists to prevent.

Nothing outside a scratchpad had ever read `1.0`, so an in-place edit was available and was **not
taken**. "Nobody has read it yet" is the reasoning that erodes a versioning discipline the first
time it is inconvenient, and the bump costs nothing: additive changes are backward-readable by
construction, so a `1.0` reader still works against a `1.1` artifact.

## Versioning

`MAJOR.MINOR`, applying DESIGN_BRIEF §11's change classes, which became **binding for this artifact
only** on the 2026-08-14 ratification. §11 is still design capture for everything else.

| §11 change class | version effect | why |
|---|---|---|
| **additive** — a new column | **MINOR** | §11's "named fields, not positions": a reader ignores a column it does not know |
| **subtractive** — a column removed or narrowed | **MAJOR** | a reader may depend on it |
| **semantic redefinition in place** | **FORBIDDEN** | not a bump rule — a prohibition |

**Why redefinition is forbidden rather than versioned.** §11: *"changed meaning gets a new field
name (converts the dangerous case into add + deprecate); representation changes are redefinitions
in disguise."* A column whose meaning changes while its name stays is **undetectable by any version
check** — every reader still finds the column, parses it, and is wrong. Renaming converts that
silent case into an additive one, which is a MINOR bump and safe by construction.

**Reading rules** (`ir.can_read`), fail-closed per §11:

- A reader refuses an **unknown MAJOR** outright rather than attempting it.
- Within one MAJOR a reader accepts **any MINOR, including a higher one** — this looks wrong and is
  not. MINOR bumps are additive by definition, so a newer artifact carries columns the reader does
  not know and will ignore.
- **Shape-readable is not the same as comparable.** Two PITs of the same schema version may still
  be incommensurable on observation extent or coverage. That question is answered by the fields in
  "Observation extent and coverage", never by the version.
- **`1.4` added a rank to an existing column's vocabulary, and a version check cannot catch that.**
  A reader written against `1.3` that treats `observed` as the strongest evidence class will
  mis-rank an `asserted` or `imported` row, because the column it reads is the same column. Stated
  here rather than fixed by the version, which is what a MINOR bump can and cannot do.
- **Two columns must be detected by presence, not by version.** `ir_entity.listen_ports` and
  `.dial_ports` exist in stores labelled `1.3` (see "Why `1.4` exists"). Ask the artifact for its
  columns; do not infer them from `schema_version`.

The version is stored **in the artifact** (`ir_pit.schema_version`), not only in the producing
build. An artifact has to state its own version to be readable by a build that did not write it —
which is the whole difference between this and the belief store's build-fingerprint guard, which
correctly *refuses* any mismatch because nothing is ever supposed to read it across builds.

## Identity

**A PIT is identified by `(source, since, as_of)`**, carried as `pit_id`.

- `as_of` is the semantic *"the network as known at time T"* — `snapshot()`'s own parameter.
- `since` bounds the observation from below. **Corrected 2026-08-17**: the identity was
  `(source, as_of)`, from before `since` existed, so a PIT over `[W1, W2]` and one over `[W2, W2]`
  — the same source, the same `as_of`, genuinely different observations — collided on one id.
  Found by D10's own preferred workflow, which materializes both. An unbounded PIT's id is
  unchanged, so nothing already written moves (`LES-113`).
- **Never wall-clock invocation time.** It is non-deterministic (`LES-002`) and says nothing about
  the network.
- A **merged** PIT is identified instead by its axis and its contributing ids
  (`ir/merge.merged_pit_id`), and its id says `pit-merge-…` so a reader can tell the two kinds
  apart without opening the row.

**An entity is identified across PITs by `entity_key`, a natural key** — never by the belief
store's `ent-NNNN`, which is **run-scoped** by `Q1` D3 and therefore names an unrelated host in the
next run. `key_kind` records how the key was derived, as a closed vocabulary, because the kinds are
not equally strong evidence that two PITs are describing the same host:

| `key_kind` | derived from | strength |
|---|---|---|
| `hostname` | the resolver's parsed computer name, normalized across NTLM / Kerberos / DHCP spellings (`LES-107`) | strongest |
| `mac` | a DHCP-observed hardware address | strong |
| `address` | the address itself, for an entity with no host-scoped identity evidence (`Q1` D4) | **weak** — asserts only that the address did not change hands |

A comparator may legitimately weight these differently and **cannot** if the artifact does not say
which was used. `source_entity` preserves the run-scoped id the row came from, so a PIT remains
traceable back to the store that produced it.

**`entity_key` is self-describing: `<key_kind>:<value>`** — `hostname:bw-l`, `mac:a0:d3:c1:f9:b1:40`,
`address:10.80.72.13`. The prefix duplicates `key_kind` deliberately. Consumers will join on
`entity_key` alone, because it is one column and it works; a bare value would let a hostname that
happens to look like an address join to one. Prefixing makes that mistake impossible rather than
documenting it.

**Precedence is `hostname` → `mac` → `address`, first match wins**, and one identifier is chosen,
never a composite of several. A composite would make an entity a *different* entity the moment a
second NIC started or stopped appearing — which is precisely the cross-PIT join this key exists to
support. Measured justification for hostname outranking mac: on the 2026-08-11 baseline one host
carries one computer name and **two MACs across two addresses** (a laptop, wired and wireless);
keyed on mac it splits in two depending on which interface was active.

**Two identifier namespaces may never key an entity**, declined by name in
`src/aximprint/ir/keys.py::NOT_A_KEY`: a Kerberos **user principal** names a person, not a host
(`Q1` D1 one layer downstream — keyed on it, every machine one administrator logged into would
collapse into a single entity), and a **DNS name** binds a third party's address
(`scope=referenced`). A namespace outside both tables is **counted**, never silently used.

**The key inherits the resolver's normalization and does not repeat it.** `LES-107` already parses
`WORKSTN1$`, an FQDN and DHCP option 12 down to one computer name; re-deriving that here would put
one rule in two places. The consequence a reader needs to know: **a change to `computer_name()`
changes every key**, so PITs written either side of such a change will not join despite both being
schema-valid. `ir_pit.code_fingerprint` is what lets a comparator notice — a join across a build
boundary is a warnable event, not a silent one (`LES-113`).

## Evidence classes

`asserted` · `imported` · `observed` · `referenced` · `imputed` — **five, and the order is the
ranking.** Widened at `1.4`; three until then.

`EVIDENCE_CLASSES` (`ir/schema.py`) is declared in **precedence order**: when two classes speak to
one fact, the earlier one wins, and `evidence_rank()` reads its position rather than a second table
that could drift from it (`LES-101`). **Resolution happens on the conclusions side, twice, in the
two places two classes can meet.** `ir/materialize.py::resolve` picks the winning reading of an
entity's `segment` and `role` when the belief store holds several — the store deliberately returns
*every* class's reading, one row per (entity, class), because a reduction there would erase the
loser before anything could name it, and because `evidence_rank` lives in `ir`, which `store` may
not import. `synth/compiler.py::resolve_precedence` then does the same for the cast, recording the
beaten reading in the sidecar under `overrides` — **no class masquerades as a higher one, and an
override is visible, never an erasure: the store keeps both readings and the sidecar names the
loser.**

| class | meaning | typical producer |
|---|---|---|
| `asserted` | an operator's hand-authored fact | the assertions file, through reference ingest |
| `imported` | an out-of-band point-in-time snapshot — a directory export, a host inventory, a device config | reference ingest, time-bounded by the export's own timestamp |
| `observed` | the sensor saw the entity do it, first-hand | the wire |
| `referenced` | the wire carried a **third party's** claim about it — a DNS answer, an SPN, a redirect, an XFF header | the wire, second-hand (`ir_unseen`'s census) |
| `imputed` | a learner inferred it from evidence | the role catalog plus a listen census |

`referenced`'s meaning is **unchanged** by the widening, and saying so matters: it was never a name
for out-of-band imports. Those sit on the *opposite* side of `observed` in trust — a DNS server's
claim about a host is weaker than seeing that host act, while a directory export is authoritative
for identity — which is exactly why `imported` is a separate class rather than a second job for
this one.

`compiler` — **renamed from `defaulted` on 2026-09-04**, meaning unchanged — is deliberately
**excluded**, and this resolves a discrepancy that existed in the code: `learners/extractors.py`
documents the IR's classes and `synth/synthesizer.py` documented one more. Both were right about
their own layer. It is what a **compiler** does when a catalog supplies a value discovery never
observed — an `image`, a CPU count — so it is a property of `twin_config`, not of this artifact. It
lives in the cast provenance sidecar, is declared once in the producer that emits it
(`synth/compiler.py::COMPILER_CLASS`), and `evidence_rank()` **refuses** it: a label with no
evidence behind it must never be ranked against evidence.

**The IR carries only what was discovered.** A value nothing discovered is *absent* from a PIT
rather than present-and-defaulted. That is what stops a compiler's inventions from re-entering the
drift comparison as though they were observations — a twin's `image: debian-12-cloud@v1` must never
be able to look like evidence about the monitored network.

**One consequence for a compiler that replaces a value it DID discover.** A CIDR relocated out of
the deployment target's own address space, or a lab realm standing in for an observed one, is a
**declared deployment transformation**: applied after resolution, never competing in the order
above, and always recording the pre-transformation value beside it (axRange
`docs/design/14-operator-assertions.md` §2). It is not a precedence event, and a consumer must not
score it as a mismatch — which is only possible if such a row is shaped differently from a
`compiler` value that was invented from nothing. The IR is untouched either way: it carries the
observed CIDR, and the transformation lives entirely in the cast.

## Observation extent and coverage

The fields that make absence interpretable (`WORK_ORDER_network_ir.md` D4, D4a). **Load-bearing,
not decorative:** without them every diff conflates *changed* with *we were blind*, and for drift
detection absence is the signal.

**The IR records presence and coverage. It never records absence.** An IR row cannot know about a
future PIT, so it must not encode "gone". The three-way classification — *present*, *observed
absent*, *not observed* — happens at comparison time, from these fields.

**Absence is graded over two time scales, and both are here:**

- **within a PIT**, at window granularity: `ir_entity.windows_seen` against `ir_pit.windows`.
  "Present in 38 of 40 windows" is presence *shape*, not a boolean.
- **across PITs**: the comparator's scale, from a sequence of PITs.

**Coverage is per segment, not global**, and this is the field that decides whether the grading
works at all. A host absent for a run of windows *with good coverage of its segment* is a strong
decommission signal; the same run *with the tap down on that VLAN* is **zero** signal, not weak
signal — and a consumer fed the second case as "absent, N consecutive" will learn a decommission
pattern from a tap outage.

`ir_segment.coverage_source` is a **vocabulary, not a boolean**, so it can grow without a semantic
redefinition (which would be forbidden, above):

| value | meaning |
|---|---|
| `inferred_from_traffic` | traffic was seen from this segment in `windows_present` of `windows_total`. **A proxy, not a measurement** — Zeek reports no per-segment loss. Must never be read as measured (`LES-006`). |
| `sensor_reported` | the sensor reported coverage for this segment directly. No current sensor does; the value exists so one can be expressed without a schema change. |

`ir_pit.capture_loss_pct_max` remains the **global**, genuinely measured figure. `NULL` means
unaudited, never zero.

## Tables

Column lists are **normative** and are checked against `src/aximprint/ir/schema.py::IR_TABLES` by
`tests/test_ir_schema.py`. Every table is keyed on `pit_id` so many PITs share one database and a
drift comparison is a join, not N file attaches.

**Nullability is semantic throughout.** A `NULL` means *no evidence*, never zero and never false
(`LES-006`). A consumer reading `role IS NULL` as "role unknown" is correct; reading
`capture_loss_pct_max IS NULL` as a clean bill of health is not.

### `ir_pit` — one row per PIT

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | derived from `(source, as_of)` |
| `source` | VARCHAR | what was ingested |
| `as_of` | DOUBLE | the semantic point in time |
| `observed_from` | DOUBLE | extent — earliest evidence backing this PIT |
| `observed_to` | DOUBLE | extent — latest |
| `windows` | INTEGER | window count; the denominator for `ir_entity.windows_seen` |
| `schema_version` | VARCHAR | this document's version, in the artifact |
| `aximprint_version` | VARCHAR | producing build |
| `code_fingerprint` | VARCHAR | producing build |
| `git_commit` | VARCHAR | producing build |
| `capture_loss_pct_max` | DOUBLE | global, measured. `NULL` = unaudited |
| `entities` | INTEGER | row count in `ir_entity` for this PIT |
| `segments` | INTEGER | row count in `ir_segment` for this PIT |
| `vantage` | VARCHAR | **the tap point**, `<appliance>:<streams>`. `NULL` = unknown, **or** the PIT is merged — see `vantages`. **Added at `1.2`** |
| `network` | VARCHAR | which network this is OF, as distinct from where it was tapped and what it was written to. **Operator-declared**; nothing in traffic identifies it. `NULL` = undeclared. **Added at `1.2`** |
| `vantages` | JSON | every tap a **merged** PIT combines, sorted. `NULL` on a materialized one. **Added at `1.2`** |
| `merge_axis` | VARCHAR | `vantage` / `time`. `NULL` = materialized from a belief store rather than merged. **Added at `1.2`** |
| `merged_from` | JSON | contributing `pit_id`s, sorted. `NULL` on a materialized PIT. **Added at `1.2`** |

**A PIT is one tap point's view.** `source` is a filesystem path and says nothing about where the
tap sat; `vantage` is the identity of the tap itself, and the two are not substitutes. The identity
is `<appliance>:<streams>` because on this appliance a link is a *stream*: one host capturing
streams `1-4` and the same host capturing `5-8` are two vantages, not one.

Three tiers supply it, in order, and **nothing guesses** (`WORK_ORDER_visibility_and_vantage.md`
D2): the **harvest manifest** `acquire` writes beside the cold tier, which is authoritative because
that layer is the only one that knows the appliance and the streams; an operator-supplied
`--vantage` on `ingest`, for a log directory of unknown origin; and `NULL`, meaning **unknown** —
never a default string and never the source path (`LES-006`). A `--vantage` that *contradicts* a
manifest is refused rather than silently overridden.

**`vantage` and `vantages` are not redundant.** A merged PIT leaves `vantage` NULL rather than
picking one of its members: *"this PIT is tap A"* and *"this PIT contains tap A among others"* are
different claims, and a comparator must not read the second as the first. `ir.schema.vantage_set`
is the one place that resolves the pair.

### `ir_entity` — one row per (PIT, entity)

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `entity_key` | VARCHAR | the cross-PIT natural key |
| `key_kind` | VARCHAR | `hostname` / `mac` / `address` — how it was derived |
| `source_entity` | VARCHAR | the run-scoped id it came from |
| `locality` | VARCHAR | `local` / `external`. `NULL` = nothing could judge it, **never** "external". **Added at `1.1`** — see below |
| `segment` | VARCHAR | CIDR. `NULL` = unplaced (external, or on nothing provisionable) |
| `segment_confidence` | DOUBLE | |
| `segment_evidence_class` | VARCHAR | |
| `role` | VARCHAR | `NULL` = no role inferred, which is a supported outcome |
| `role_confidence` | DOUBLE | |
| `role_evidence_class` | VARCHAR | The class of the reading that WON, resolved by `evidence_rank` — `imputed` for a port-signature role, `asserted` for one an operator supplied through the assertions channel (`docs/WORK_ORDER_reference_ingest.md` D4/D14). **It stopped being a constant on 2026-09-05**; it is `imputed` on every artifact written before that date and on every one written since whose store carries no reference facts, so a reader must not treat that value as guaranteed. `NULL` iff `role` is |
| `listen_ports` | VARCHAR | JSON array of `"<port>/<proto>"` the entity ANSWERED on. `observed` — unlike the role inferred from it. **Added 2026-08-27, recorded at `1.4`** — stores labelled `1.3` may carry it, so detect it by column presence (see "Why `1.4` exists") |
| `dial_ports` | VARCHAR | JSON array of `"<port>/<proto>"` the entity ORIGINATED to. Read by persona synthesis: a persona generated to drive a twin node must emit what the source was seen doing, or the node cannot exhibit the role it was built for. **Added 2026-08-27, recorded at `1.4`** — same column-presence rule as above |
| `windows_seen` | INTEGER | within-PIT occupancy — presence shape |
| `first_seen` | DOUBLE | |
| `last_seen` | DOUBLE | |
| `addresses` | JSON | every address this entity was seen at |
| `identifiers` | JSON | parsed names / MACs backing the key |
| `vantages` | JSON | which taps saw this host. `NULL` = the PIT records no vantage. **Added at `1.2`** |

**`ir_entity.vantages` is the column multi-tap exists for.** *"What does tap B see that tap A does
not"* is a question about this list, and a merged PIT without it could not answer it at all. On a
single-vantage PIT it is the constant `[vantage]`, which is not redundancy: it is the shape a
merged PIT's per-entity union generalizes, so a consumer asks one question of both.

### `ir_segment` — one row per (PIT, segment)

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `cidr` | VARCHAR | |
| `name` | VARCHAR | the twin-config identifier (`seg-10-80-72-0`) |
| `entities` | INTEGER | placed here |
| `windows_present` | INTEGER | coverage numerator |
| `windows_total` | INTEGER | coverage denominator |
| `coverage_source` | VARCHAR | see "Observation extent and coverage" |

### `ir_edge` — one row per (PIT, src, dst, proto)

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `src_key` | VARCHAR | an `ir_entity.entity_key` |
| `dst_key` | VARCHAR | an `ir_entity.entity_key` |
| `proto` | VARCHAR | |
| `flows` | BIGINT | |
| `bytes` | BIGINT | |
| `first_ts` | DOUBLE | |
| `last_ts` | DOUBLE | |
| `evidence_class` | VARCHAR | `observed` today. **This column is what makes the GNN-comparison use work**: DESIGN_BRIEF §6 names link prediction for imputing unobserved edges, and an imputed edge lands here beside an observed one, distinguishable rather than merged. |

### `ir_identity` — one row per (PIT, entity, kind, identifier)

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `entity_key` | VARCHAR | |
| `kind` | VARCHAR | `dhcp` / `kerberos` / `ntlm` / `dns` |
| `namespace` | VARCHAR | `mac` / `hostname` / `principal` / `dnsname` |
| `identifier` | VARCHAR | the parsed value |
| `confidence` | DOUBLE | |
| `evidence_class` | VARCHAR | `referenced` for a DNS-derived binding |
| `from_ts` | DOUBLE | binding interval |
| `to_ts` | DOUBLE | `NULL` = still open at `observed_to` |

### `ir_unseen` — one row per (PIT, address the vantage knows exists and never saw)

**Added at `1.2`** (`WORK_ORDER_visibility_and_vantage.md` D7). DESIGN_BRIEF §8's own first input
to the visibility model: *"referenced-but-unseen hosts (DNS answers, SPNs, redirects, XFF)"*. The
DNS half already sat in the belief store and was dropped here — 2,418 addresses on the 2026-08-11
baseline, of which **9** are inside the estate. On a near-full-visibility vantage that is a small
number; on the partial vantage this table exists for, it *is* the blind spot.

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `address` | VARCHAR | the address nothing was ever seen from or to |
| `locality` | VARCHAR | `local` / `external`. `NULL` = nothing could judge it |
| `segment` | VARCHAR | set only when the address falls inside a segment **this PIT observed** — the strong case, and the actionable one. `NULL` otherwise |
| `reason` | VARCHAR | `referenced_by_dns` today; a closed, growable vocabulary |
| `evidence_class` | VARCHAR | `referenced`, always: something named it, nothing observed it |
| `identifiers` | JSON | the names that referenced it |
| `observations` | BIGINT | how many times, per name, at most — never a sum across a binding's own change rows |
| `first_ts` | DOUBLE | |
| `last_ts` | DOUBLE | |

**Unseen means unseen AT THIS VANTAGE**, which is why merging views corrects it: an address tap A
never saw but tap B watched send traffic is not unseen in the combined PIT, and leaving it in the
census would report a blind spot the merged vantage does not have.

### `ir_traffic_profile` — one row per (PIT, entity): the behavioral profile

**Added at `1.3`** (`docs/WORK_ORDER_traffic_profiles.md` D1–D6). The finest grain observed — per
RESOLVED entity, aggregated across the PIT's span under the `canon` map exactly as `ir_entity`
already is. A role-level aggregate is **not** also stored (D1): `cast` derives it from these rows
when it builds a `tfc` assignment, so a role-level pooling choice can never re-enter drift
comparison as though it were observed — consistent with `docs/WORK_ORDER_network_ir.md` D9, "the
IR keeps the complete view; collapsing lives at compile time."

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `entity_key` | VARCHAR | an `ir_entity.entity_key` |
| `windows_active` | INTEGER | occupancy numerator. Published unconditionally, unlike every column below it — it is what a reader needs in order to tell a floor was not cleared, not itself subject to one |
| `windows_total` | INTEGER | occupancy denominator; equals `ir_pit.windows` for this PIT |
| `hod_flows` | JSON | 24-bucket hour-of-day histogram of `flows_out + flows_in`, **UTC**, normalized to sum 1.0. `NULL` below the diurnal floor (D4) — a histogram from too little evidence is a fabrication, not a fact |
| `dow_flows` | JSON | 7-bucket day-of-week histogram, UTC, normalized to sum 1.0, index 0 = Monday (`datetime.weekday()`). `NULL` below the diurnal floor |
| `schedule` | VARCHAR | `24x7` / `business-hours` (`ir.SCHEDULE_CLASSES`) — a claim about SHAPE, not the clock: the capture carries no timezone, so this means "activity concentrated in a contiguous 8-hour UTC band", never "9-5 local" (D3). `NULL` below the diurnal floor |
| `schedule_evidence_class` | VARCHAR | `imputed` when `schedule` is set, else `NULL` — the histogram it rests on is `observed`; the classification drawn from it is not, the same posture as `ir_entity.role` |
| `flows_per_window_q` | JSON | `[p10, p50, p90, p99]` of `flows_out + flows_in` across active windows. `NULL` below the rate floor (D4) |
| `bytes_per_window_q` | JSON | `[p10, p50, p90, p99]` of `bytes_out + bytes_in` across active windows. `NULL` below the rate floor |
| `burstiness_p50` | DOUBLE | median of the per-window `burstiness` windowing already computes. `NULL` below the rate floor |
| `client_ratio_p50` | DOUBLE | median of the per-window `client_ratio`. `NULL` below the rate floor |
| `interarrival_p50` | DOUBLE | median of each window's own interarrival p50. `NULL` below the rate floor |
| `evidence_class` | VARCHAR | `observed` — the histogram and rate fields are measured directly; `schedule`'s own class is carried separately since it alone is `imputed` |

**Two evidence floors (D4), proposed and UNMEASURED — `docs/WORK_ORDER_traffic_profiles.md` build
step 6 pins them against the ten-day baseline.** `windows_active < 48` leaves `hod_flows` /
`dow_flows` / `schedule` / `schedule_evidence_class` `NULL`; `windows_active < 4` leaves the four
rate/occupancy fields `NULL`. An entity below a floor still gets a row — `windows_active` /
`windows_total` are always present — and is counted in `resolve()`'s `_stats`
(`entities_below_diurnal_floor` / `entities_below_rate_floor`), the same discipline
`unkeyable_entities` already uses: below a floor a field is absent, never fabricated (`LES-006`;
`Q34` is the cautionary tale this rule exists to avoid repeating).

### `ir_capture_profile` — one row per (PIT, parameter, stat): subject-less behavioral facts

**Added at `1.3`** (`docs/WORK_ORDER_traffic_profiles.md` D7). `profile_facts`' first reader
(`Q29`'s subject-less channel — a fact true of the capture, not of any host in it; today's only
occupant is `ocsp_cache_lifetime`). Pooled from the per-window `profile_facts` table across the
PIT's span. **Explicitly NOT projected into `tfc`**: `AssignmentSpec` has no field for a
subject-less parameter, and manufacturing one would mean building against a vocabulary this
project does not own. The drift comparator and a topology-diagram generator can read it here.

| column | type | notes |
|---|---|---|
| `pit_id` | VARCHAR | |
| `parameter` | VARCHAR | a `windowing.windows.PROFILE_PARAMETERS` key |
| `stat` | VARCHAR | `p10` / `p50` / `p90` / `p99` / `n` |
| `value` | DOUBLE | for `p10`/`p50`/`p90`/`p99`: the MEDIAN across windows of that window's own stat value — the same operation `ir_traffic_profile.interarrival_p50` already applies to one quantile point, applied here at all four. For `n`: the SUM across windows, since it is a count of observations |
| `unit` | VARCHAR | carried from `profile_facts.unit`, never re-derived |
| `windows` | INTEGER | how many windows reported this parameter at all, within the PIT's span |
| `evidence_class` | VARCHAR | `observed` |

Pooling the underlying raw values is not available: `profile_facts` stores only each window's own
already-reduced quantile summary, never the observations behind it (`BeliefStore.snapshot`'s own
docstring names this exact ambiguity — "pooling raw values, weighting by `n`, or something else
entirely" — and declines to resolve it, leaving the choice to whoever builds the `tfc` compiler.
This table is that choice, made once, here, rather than silently by whichever caller reads
`profile_facts` first.)

## Combining PITs

Two axes, and conflating them is the easiest mistake available here (D4, D10). `ir/merge.py` owns
the operation; `merge_axis` records which was used, because the two compute coverage and volume
differently and a single untyped union would get one of them wrong.

| | **`vantage`** — several taps, one span | **`time`** — one tap, several spans |
|---|---|---|
| the same window is | seen by BOTH PITs | in exactly ONE PIT |
| segment coverage | takes the **best** across taps | **accumulates** |
| `windows_seen`, edge volume | **max** (both taps may have seen the same flow) | **sum** (disjoint) |
| refused when | networks differ, or one tap appears twice | **spans overlap** |

**Materializing over a span is the preferred time-union**, not this table's `time` axis:
`materialize(store, since=W1, as_of=W40)` re-reads the belief store and re-aggregates from source
rows, so it is exact. Artifact combination exists for the case that cannot serve — the belief store
pruned (`Q5`) or gone, and only the IR surviving.

**A merged PIT is still the complete view.** Filtering and scale-down happen at compile time, never
here (D9): push either upstream and drift comparison silently starts measuring the compiler's
policy instead of the network.

## Determinism

A PIT is a pure function of `(belief store contents, as_of, code build)`. Same inputs, byte-identical
rows. Nothing wall-clock, unseeded, or set-iteration-ordered may reach a row — `LES-002`, and it
matters more here than elsewhere, because a drift comparator subtracts two PITs and **any
nondeterminism reads as drift**.

`ir_pit`'s build columns (`aximprint_version`, `code_fingerprint`, `git_commit`) are the declared
exception: they vary by build on purpose, and a comparator must exclude them from a structural diff
and report a build change separately.

## Comparing two PITs

`src/aximprint/ir/compare.py` (`aximprint drift`) is the reference consumer, and the shape of what
it does is part of what this artifact promises:

- **The IR records presence; the comparator classifies absence** (D4). A removed entity is
  `observed_absent` when its last-known segment had coverage at or above `COVERAGE_FLOOR` in the
  later PIT, and `not_observed` otherwise. `observed_absent` means *the sensor saw the segment and
  this host was not in it* — evidence of absence, **never proof**: a host that talks once a day is
  absent from a perfectly covered three-hour PIT. The coverage ratio is published beside every
  verdict so a consumer can re-decide with its own floor.
- **A comparison can be refused**, and being able to refuse is the feature. Different sources,
  differing schema MAJOR, a PIT against itself, and observation windows differing by more than
  `MAX_DURATION_RATIO` all make a report *wrong* rather than uncertain.
- **A caveat is not a refusal.** A build change between two PITs (`LES-113`), weak `address` joins,
  and capture loss weaken a report without invalidating it. Collapsing the two in either direction
  is a mistake: refusing on a caveat makes the tool unusable, and downgrading a refusal to a caveat
  produces confident nonsense.
- **`locality` partitions the report.** Local entities are itemized; external peers are counted and
  never listed, because CDN and cloud addresses rotate for reasons that are not network drift. On
  the 2026-08-11 baseline that is 1,963 external changes against 61 local ones.

**A PIT intended for comparison should be materialized with a `since` bound.** Without one a PIT is
cumulative — everything known up to `as_of` — so any two from one store have nested extents and are
correctly refused. Both shapes are legitimate: a twin wants everything known, drift wants a span.

## Open, and deliberately not answered here

- **The natural-key derivation** is build step 2 of the work order. This document specifies the
  column and its vocabulary; it does not specify the algorithm.
- **Write semantics** — whether re-materializing an existing `pit_id` replaces or refuses — belong
  to build step 3. Recommendation on record there: **refuse**, since something may already have
  compared against it.
- **Per-segment coverage** (build step 1b) **landed with step 3**, in the materializer rather than
  the windowing layer. It turned out fully derivable from `node_features`, which already records
  each entity's segments per window: *"traffic was observed from segment S in window W"* is exactly
  *"some entity in window W had S among its segments"*. Building it upstream would have added an
  accumulator to compute what the store could already answer.
