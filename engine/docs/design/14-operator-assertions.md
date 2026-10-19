*How an operator adds what a vantage point cannot see and corrects what it inferred — a six-class
evidence model with a precedence order, an assertions file as the contract, and the Console canvas
as its author. Companion to `13-golden-twin-fidelity.md`.*

> **STATUS: DESIGN RATIFIED 2026-09-04 (Authority: user) — nothing built yet.** Ratified in a
> design session on 2026-09-04 (CHANGELOG same date). The evidence model in §2 **supersedes**
> `13-golden-twin-fidelity.md` §3's "aximprint's existing three" sentence and its three per-fact
> states. The assertions channel is `13` §5's item 5 ("operator-asserted facts") promoted to the
> **first** reference ingest to build, ahead of the directory export (`13` §8.4). Cross-repo:
> axRange owns this file, the Console and the API surface (§4); aximprint owns the ingest, the
> compiler's precedence resolver and the recompile (§3, §6) and cites this file from
> `docs/WORK_ORDER_reference_ingest.md` (to be written). Both sides on `wip/golden-twin`.

## 1. Why this exists

A twin is cast from what one tap point saw. On the CorpAPCN imprint that vantage produced a twin
missing six observed segments, five high-occupancy hosts the role census could not name, a second
domain controller inferred as a resolver, every VLAN zone type, and the gateway — not because the
cast was wrong but because nothing at that vantage carries the evidence (`13` §1 and §3.2;
`docs/WORK_ORDER_fidelity_scorecard.md` §3.4). The directory export (`13` §5 item 1, ruled §8.4)
fills the identity half of that gap. This note defines the general mechanism the export is one
instance of: **a reference channel through which an operator adds missing structure and overrides
discovered evidence**, with a class of its own so it never masquerades as observation, a
precedence so it wins where it should, and a canvas so the operator does it by looking at the
network rather than by writing YAML.

## 2. The evidence model: six classes, one order

`13` §3 named three classes; aximprint's IR spec (`docs/SPEC_network_ir.md`, "Evidence classes")
names the same three and deliberately excludes a fourth, `defaulted`, from the IR. Two things were
found on inspection on 2026-09-04 and are corrected here:

- **`referenced` was doing two jobs.** In aximprint it means *second-hand evidence from inside
  the capture* — a DNS answer, an SPN, a redirect, an X-Forwarded-For header: "a claim about a
  host the sensor never saw at that address" (`learners/extractors.py`), the class behind
  `ir_unseen`'s 510 referenced-but-unseen hosts on CorpAPCN. `13` §3 stretched the same word over
  out-of-band imports — directory exports, host inventories, device configs. Those sit on opposite
  sides of `observed` in trust: a DNS server's claim is weaker than seeing the host act; a directory
  export is authoritative for identity. One class cannot hold one rank for both.
- **`defaulted` is a compiler label, not evidence.** It appears only in the cast provenance
  sidecar, never in the IR, and marks nine kinds of compiler choice made where the wire had
  nothing: image and sizing from the role catalog, the VLAN zone type, a CIDR relocated to avoid
  the deployment target's own address space, the synthetic gateway, the synthetic internet-sink
  nodes and their segment, `default_agents: false` on a measurement subject, and each persona's
  seed (`synth/compiler.py`, the `prov(...)` sites). It is the opposite of hand-authored. Its
  category is sound; its name is misleading.

The model, in **precedence order** — higher wins at compile time when two classes speak to one
fact:

| class | meaning | where it lives | confidence | source of truth |
|---|---|---|---|---|
| `asserted` | an operator's hand-authored fact | IR store, via the assertions file (§3) | 1.0 by convention | the operator, named in `source` |
| `imported` | an out-of-band point-in-time snapshot: LDIF directory export, host inventory, device configs, security baselines | IR store, via reference ingest | per source; 1.0 for a directory's own records | the export, time-bounded by its timestamp |
| `observed` | the sensor saw the entity do it, first-hand | IR store | learner-scored | the wire |
| `referenced` | the wire carried a third party's claim about it (meaning unchanged) | IR store | learner-scored | the wire, second-hand |
| `imputed` | a learner inferred it from evidence | IR store | learner-scored | the role catalog plus evidence |
| `compiler` | the cast compiler's own choice where nothing spoke (renamed from `defaulted`) | **sidecar only, never the IR** — aximprint's exclusion rule stands | none | the platform |

The house rule generalizes: **no class masquerades as a higher one, and an override is visible,
never an erasure.** When `asserted` overrides `observed`, the store keeps both, the sidecar records
`asserted` with `overrides: {class: observed, value: …}`, and the scorecard shows the conflict.
The scorecard's per-fact states become *observed-*, *referenced-*, *imported-* and
*asserted-and-reproduced*, *imputed*, and *compiler (not scored)*, and **every row publishes its
evidence mix** — how many facts of each class it rests on — so a twin that passes because it was
hand-written is distinguishable from one that passes because the wire proved it. That mix is the
guardrail that lets `asserted` carry full confidence and top precedence without turning the
scorecard into a document that certifies its own inputs.

**Declared deployment transformations are not precedence events (added 2026-09-04, from the
reference-ingest draft).** A value the compiler replaces for deployability — a CIDR relocated away
from the deployment target's own address space, the lab realm `corp.axr.lab` standing in for an
imported or observed realm — is applied after resolution, never competes in the order above, and
always records the pre-transformation value beside it in the sidecar. The scorecard scores such a
fact as *reproduced under a declared transformation*, not as a mismatch. The escape hatch is an
ordinary `override`, which moves the choice into a class that outranks the evidence.

## 3. The assertions file: the contract

`<imprint>.assertions.yaml`, kept **beside the cast bundle** (`twins/<id>/`, was
`twin-configs-cast/<id>/`; the Console's root and what is checked in) and handed to aximprint's `cast --assertions` by path on
apply; aximprint's CLI has no separate imprint directory (corrected 2026-09-04), and its YAML-first
default may stay beside the IR store — the same file-drop door the LDIF export uses (`13` §8.4),
git-versioned and diffable. `layout.json` (`15` §4) sits in the same place (under `generated/` in a `twins/<id>/` folder). The first
compile of an imprint has no such file; the first save creates it; every later compile reads it;
every later save merges into it.

```yaml
schema: axr-assertions/1
imprint: corpapcn-twin
as_of: 2026-09-04T18:00:00Z          # the snapshot's time bound
source: "operator: <name>"           # carried into every fact's `source`
add:
  - kind: segment
    key: "10.80.31.0/24"             # stable keys: a CIDR, address:<ip>, hostname:<name>
    name: seg-10-80-31
    zone_type: vlan
    note: "pruned by the cast (seen in 2 of 11 windows); it is the OT lab"
  - kind: host
    key: "address:10.80.20.5"
    segment: "10.80.20.0/24"        # an address alone names no segment
    role: domain-controller
    listen: [{port: 88, proto: tcp}, {port: 389, proto: tcp}, {port: 445, proto: tcp}]
    note: "the DC; it answers nothing on the wire, so no learner ever saw it"
  - kind: conduit
    key: "10.80.20.0/24|10.80.31.0/24"
    note: "the OT lab reaches the DC"
override:
  - key: "address:10.80.20.5"
    field: role
    value: domain-controller          # the store keeps the overridden `imputed: dns-resolver`
    note: "inferred dns-resolver from port 53 alone; it is the DC"
suppress:
  - key: "address:203.0.113.9"
    note: "out of scope: a cloud CDN edge"
```

> **Correction, 2026-09-15 — the `add: segment` example above describes an effect the compiler does
> not have.** Its note (*"pruned by the cast (seen in 2 of 11 windows); it is the OT lab"*) implies the
> record brings a pruned segment back. It does not. `10.80.31.0/24` is an **observed** CIDR, and an
> `add: segment` of an observed CIDR **confirms rather than creates**: the compiler marks the record
> matched, applies its `name`/`zone_type` to the observed segment, and **keeps that segment only when a
> host is placed on it** — only an *unobserved* CIDR joins the asserted set that survives the
> emptiness prune (aximprint `src/aximprint/synth/compiler.py:1097-1113`), while an added host resolves
> against the observed segments plus this file's adds (`:882`) and so is what keeps an observed segment
> in the twin. What the example's record really supplies is `zone_type`, which no vantage can observe.
> Recorded first by aximprint's reference-ingest S2 (its `docs/WORK_ORDER_reference_ingest.md` §8,
> "What S2 measured", finding 1: the example "confirms rather than creates", the illustration
> predating the deployment-scope change that stopped the compiler pruning thinly-covered segments), and
> settled for the Console by running the real `compile_twin` during S5.1.a's review
> (`docs/WORK_ORDER_console_canvas.md` §18.2 call 1). The example is kept as written, since the record's
> shape is correct; only its note's claim was wrong. The case where such an add has **no** host — a
> silent no-op the sidecar reports neither `ineffective` nor `unmatched` — is `docs/PRODUCT_GAPS.md`
> GAP-093.

Rules. Keys are the **stable entity keys** the topology export already uses (`address:`,
`hostname:`, a segment CIDR), never a twin-config index. **Every record carries a non-empty
`note` — all three kinds, no exceptions** (the compiler refuses a record without one; the example
above omitted two until 2026-09-06).
`confidence` is implied 1.0 and never authored. Secrets are never a field (`13` §5, "inject
posture, never secrets"). Ingest: aximprint's reference-ingest plugin kind — non-sensor,
snapshot-shaped — reads the file into the belief store as `asserted` facts with the file's `as_of`
as their time bound. The compiler gains the one thing it lacks today, a **precedence resolver**:
nothing resolves two classes for one fact now because the store has never held two. The sidecar
carries the outcome per field (§2's `overrides:` record). Determinism holds: the same store plus
the same file compiles to the same cast, so a no-op save is a no-op recompile and the twin's
`plan_id` / `ir_hash` are stable (LES-126). Record kinds, refined 2026-09-04 by the reference-ingest
draft: `add` takes `segment` (needs `name`), `host` (needs `segment`) and `conduit`; `override.field`
is a closed, per-kind set — host: `role`, `image`, `resources`, `segment`; segment: `zone_type`,
`name` — and anything else is a refusal naming the accepted set; `route` is refused by name, since a
twin config has no route object and conduits are how reachability is authored; a key in both `add`
and `suppress` is a refusal, not a precedence. **Settled by the build (aximprint reference-ingest S2,
2026-09-05):** an `add` may carry its kind's override fields (plus `listen`/`dial` on a host), and a
`(key, field)` present in both `add` and `override` is a refusal; `suppress` takes host keys only;
`source:` accepts `operator: <name>` and is canonicalized; a canonical re-emit of the file drops
YAML comments, so the mandatory `note` is what survives. In the sidecar an asserted fact carries
`evidence_class: asserted`, `confidence: 1.0`, `source`, `signal`, the operator's `note`, and an
`overrides: {class, value, confidence?, drift?}` record whose `class` may be `compiler` (what the
value would otherwise have been; never ranked); `population.suppressed[]` and
`population.assertions` account for the rest. The recompile the Console's apply runs is
`aximprint cast --store <ir.duckdb> --pit <pit-id> --out <dir> --imprint-id <id> --assertions
<path>`; a shape error exits 2 with one line on stderr.

**The schema artifact is aximprint's, and axRange vendors it (2026-09-06,
`docs/WORK_ORDER_console_canvas.md` §12).** `axr-assertions/1` is published as
`src/aximprint/schemas/axr-assertions-1.schema.json` in the producer repo — the **reader of record**
— and axRange keeps a byte-identical copy at `axrange/api/schemas/axr-assertions-1.schema.json`
from which its writer *derives* the closed sets at import time, with a drift test comparing both
sets and bytes against the sibling checkout. Neither side restates the vocabulary by hand, and
axRange's own extra requirements (`imprint` required; `segment` and `name` required on an `add`)
live in its code with their reasons, never edited into the vendored copy.

**The assertions file publishes FIVE classes, not §2's six.** `compiler` is sidecar-only by
construction — it labels a choice the compiler made where nothing spoke — so it is **never
authorable**: an operator asserting `compiler` would be claiming the platform's own default as
evidence. A provenance *row* still carries all six, since the sidecar is where `compiler` lives.
The asymmetry is deliberate and is enforced at the writer.

## 4. The canvas loop

The operator's surface was originally sketched as the Grafana control-panel plugin's Imprints
screen (`ImprintsScreen.tsx`/`ImprintTopologyPanel.tsx`; API `GET /api/imprints/{id}`,
`…/provenance`, `POST …/plan`, `…/deploy`), which rendered a confidence-shaded learned topology
from a hand-written fixture. That plugin screen is gone (closed by GAP-083, then the whole plugin
removed 2026-10-01 — `docs/reference/ui-consolidation/05-deletion-candidates.md`); the design moved
to a **free-hand canvas** the architecture always reserved a slot for (`CLAUDE.md`, open question
Q1), scoped to this loop. **The canvas itself — levels, layout, panes, the API it needs — is
`15-console-canvas.md` (ratified 2026-09-04): a clean-start SPA, not the Grafana plugin.** An
Imprints screen in the axRange Console (`o2/console/`) has not been built yet. This section keeps
the loop's semantics:

```
discover (wire + imports) ─▶ compile ─▶ bundle (twin config + sidecar + topology export)
        ▲                                                  │
        │                                                  ▼
   assertions.yaml  ◀ save/apply  canvas: display · edit (add / override / suppress)
        │                                                  ▲
        └──────────────── recompile ───────────────────────┘  refresh
```

**Display.** Nodes and segments colored by the six classes and shaded by confidence. The
population block's dropped hosts and pruned segments drawn as **ghosts**, so the operator sees what
the vantage lost, not only what it found. An overridden fact kept visibly underneath its override.
A standing assertion that a later re-imprint contradicts flagged as **assertion drift** — flag
only (ruled 2026-09-04): the assertion keeps winning until the operator changes it; data never
changes precedence. The legend is the six-class model, nothing else.

**Edit.** The first cut (ruled 2026-09-04): add a segment; add a host with a role and a listen
set; change a role; suppress a host; override image and sizing (those facts become `asserted`
and therefore scored, where the compiler's own choice is not); add conduits (a `route` is refused
by name — §3 — since a twin config has no route object and conduits are how reachability is
authored). Later:
identity facts (the LDIF export covers most of them), firewall rules, protocol content. A new
component is given its stable key on the canvas. Before save, the canvas says which kind of change each edit is for a standing twin: **an
add or a remove flows through `te deploy --update`; a mutation of an existing node — a role
change, hence an image change — is a fresh deploy** (`docs/WORK_ORDER_twin_incremental_update.md`
§3.1). The canvas edits the imprint, never the deployed twin; the twin follows by the lifecycle in
`13` §6.

**Save and apply (ruled 2026-09-04).** Two actions, not one. **Save** writes the edits to the
assertions file and nothing else, so an operator can save half-finished work without re-shaping
the twin. **Apply** saves, recompiles and refreshes. Proposed API: `GET` / `PUT
/api/imprints/{id}/assertions` (save; the API merges, the file stays authoritative) and `POST
/api/imprints/{id}/apply` (save + recompile; returns the fresh bundle's content fingerprint as
`fingerprint_after`, on which the Console refreshes, alongside the `pit_code_fingerprint_*` /
`cast_code_fingerprint_*` build-stamp pairs and a `compiler_changed`/`compiler_note` verdict for a
compiler upgrade that rode along on the apply — GAP-087/GAP-088, CHANGELOG 2026-09-19). The
recompile crosses the repo boundary: **v1 runs aximprint's CLI
as a subprocess on the same box** — synchronous, a minute or two, no import in either direction
(an implementation call, open to veto). The cleaner later shape is an aximprint watcher that
recompiles when apply touches a marker, with the Console polling the fingerprint. The file is the
contract either way, so the mechanism can move without the canvas noticing. The YAML-first path stays: it is what the
tests drive and what scripting uses; the UI is thin over it.

## 5. How it composes

- **With the golden-twin lifecycle** (`13` §6): the loop sits inside *imprint*, before *cast*;
  *certify* and *drift* are unchanged and gain the evidence mix per row.
- **With the work sequence** (`13` §7, ruled 2026-09-04): the channel is sequenced **after the
  first golden**. The fresh cast's rows 1 and 2 are closed by the cast's emit-all policy plus
  axRange's deploy gate (`13` §3.4), not by assertions; the canvas is built alongside and does
  not gate the first golden.
- **With the directory export** (`13` §8.4): the same door, the second input. Its facts land as
  `imported` — below `asserted`, above `observed`. The operator can see the export and chooses to
  contradict it, which is why `asserted` outranks it.
- **With the scorecard** (`docs/WORK_ORDER_fidelity_scorecard.md`, its §9.1): the sidecar's real
  compiler shape is the contract — path-addressed fields plus the population block; axRange's
  imprint reader adapts that shape for the Console rather than aximprint emitting a second file;
  aximprint adds `confidence` and `signal` to its per-field extras so the Console's shading needs
  no invented numbers; the six classes replace the three.
- **With the server half** (`docs/WORK_ORDER_protocol_faithful_services.md`): the second DC's
  role and the missing segments arrive as assertions rather than catalog edits, and an `add: host`
  record's `listen:` becomes the twin's declared listen set (`App.listen`, ruled 2026-09-04).
  **The declared listen set is BUILT (2026-09-05)** and the second half of that sentence now has
  somewhere to land: a twin config's `nodes[].listen: [{port, proto}]` flows through to `App.listen`,
  onto `clone-vm`'s recorded inputs, and out as `hosts[].listen` in `te inventory` /
  `GET /api/deploys/{id}/inventory` — so an `add: host` record's `listen:` reaches the deployed
  twin's published contract **without a translation step**, which is why the assertion record and
  the twin-config field were given the same shape rather than two spellings of one fact. Two
  properties this note's model depends on held in the build: the declared set is published **beside**
  the observed and in-guest ones and enters **no verdict rule** (a declaration is not evidence —
  the scorecard's rows 4 and 5 stay the measurement of record), and there is **no machine-readable
  per-fact evidence marker in the twin-config vocabulary** — `observation:` is per-entity occupancy,
  not provenance — so the sidecar (§2) remains the only home for an `asserted`/`imputed` class on a
  declared listen, and the shipped S3 fixture states its imputed identity block in prose for exactly
  that reason.

## 6. What changes in the work

- `docs/WORK_ORDER_reference_ingest.md` (aximprint, to be written): input 1 is the assertions
  file, input 2 the LDIF export; the compiler precedence resolver; the `defaulted → compiler`
  rename; `overrides:` in the sidecar; `confidence` / `signal` extras; the installer copying the
  sidecar into the cast directory (both axRange drafts found it missing).
- The Console (axRange, a Track C follow-on to `docs/WORK_ORDER_aximprint_console.md`): the
  real-bundle reader and its adapter, the six-class legend and shading, ghosts, the first-cut edit
  set, write-back, recompile, refresh.
- `docs/WORK_ORDER_fidelity_scorecard.md` §9.1 is resolved by this note: real shape canonical, six
  classes, the mix per row.
- `13-golden-twin-fidelity.md`: §3's class sentence and its three states are superseded by §2
  here; §5 item 5 is promoted to the first ingest; row 11 (visual) becomes a working tool rather
  than an attested one.

## 7. Decisions

**Ruled 2026-09-04 (Authority: user):** the six-class model and its precedence order, with
`asserted` above `imported`; `defaulted` renamed `compiler`, kept unscored and out of the IR; the
assertions channel as the first reference ingest, ahead of the directory export; the canvas as the
authoring surface with the YAML as the contract; the real compiler sidecar shape as canonical, with
axRange adapting it for the Console; full confidence and top precedence for `asserted`, balanced by
the per-row evidence mix. **Later the same day:** save and apply are two actions (§4); assertion
drift is flag-only; the first edit set is add segment / add host / change role / suppress host /
image and sizing overrides / conduits (a `route` is refused by name — §3), with identity facts
deferred to the LDIF export;
and the channel is sequenced after the first golden (§5).

**Open to veto (implementation call):** the apply recompile mechanism — a subprocess to aximprint's
CLI in v1, a watcher later.

## 8. Non-goals and limits

The canvas never edits a deployed twin; it edits the imprint, and the twin follows by `--update` or
a fresh deploy. It is not a free-form IR editor: it authors assertion records, and only the kinds
§4 lists. An assertion is not an observation and is never scored as one; a twin can be made to pass
on assertions alone, and the evidence mix will say so. Secrets are never assertable. An imprint with no capture cannot be materialized from assertions
alone in v1 — aximprint's snapshot needs at least one window — so `13` §3's "seed a twin from a
design document" waits on that. Nothing here
changes a version-locked axRange contract: the assertions file is aximprint's input, the sidecar is
aximprint's output, and the API endpoints are additive.
