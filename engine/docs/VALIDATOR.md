# Validator — Rule Catalog (v1)

Authoritative spec for the validator. Companion to `docs/IR_SCHEMA.md` (input), `docs/IMAGE_MANIFEST.md` (other half of input), and `docs/PLANNER.md` (consumer). CHANGELOG entry dated 2026-05-26 "Validator: rule catalog v1" captures the rationale; this document captures the surface.

**Status:** ratified for v1. Changes to rule IDs, severities, or pass assignments require a CHANGELOG entry. Adding new rules is a CHANGELOG-able addition; retiring a rule means marking it deprecated and never reusing the ID.

---

## 1. What the validator does

The validator consumes:

- A scenario IR (per `docs/IR_SCHEMA.md`), with any `includes:` already spliced.
- Image manifests and per-platform bindings (per `docs/IMAGE_MANIFEST.md`).
- *Optionally* a Proxmox cluster snapshot (live capabilities, free capacity, current VLAN usage). Required only for Pass 4.

It produces:

- A list of **findings**, each tagged with a rule ID, severity, IR location, and a templated message.
- A terminal disposition: `pass` (no errors), `pass-with-warnings`, or `fail`.

The validator never modifies the IR. It does not produce a plan, does not touch any platform state. Plan construction is the planner's job; the validator's contract is "is this IR sound enough to plan against."

---

## 2. The validation passes

Rules execute in four passes. Each pass either halts on first error (Passes 1–2) or accumulates findings (Passes 3–4).

| Pass | Categories | Halt? | Runs in |
|---|---|---|---|
| **Pass 1** | structural (`STR`), include consistency (`INC`) | Halt on first error | `axrange validate`, `axrange deploy` |
| **Pass 2** | reference integrity (`REF`) | Halt on first error | `axrange validate`, `axrange deploy` |
| **Pass 3** | capability (`CAP`), lifecycle (`LIF`), network sanity (`NET`), overlay (`OVR`), activity (`ACT`), defense (`DEF`) | Accumulate | `axrange validate`, `axrange deploy` |
| **Pass 4** | cluster (`CLU`) | Accumulate | `axrange deploy` only |

**Why halt early.** Pass 2 rules read the structural shape — if a node has no `kind:` field, a reference-integrity check that examines node kinds will throw a `KeyError`, not a useful finding. Pass 3 rules read references — if a binding's `app:` ref is dangling, capability intersection on that binding has no image to compare against. Halt-on-error in Passes 1–2 is correctness, not policy.

**Why Pass 4 is planner-side only.** Cluster rules need a live Proxmox snapshot. `axrange validate` runs without one (it's the "I'm authoring a scenario at my desk" entry point); the cluster rules are listed here for completeness but execute only when `axrange deploy` invokes the validator with a snapshot in hand.

**Persona (`PSN`) rules are not a fifth pass.** They validate persona-catalog *content*
(`axrange/personas/catalog/*.yaml`), not a Scenario IR, so there is no `ValidatorContext` to run
them against and no `axrange validate`/`axrange deploy` entry point invokes them. See §5.12.

---

## 3. Rule schema

Every rule is defined by 13 fields:

```yaml
- id: REF-014
  category: reference-integrity
  severity: error                        # error | warning | info
  reproducible_promotes_to_error: false  # default
  pass: 2                                # 1 | 2 | 3 | 4
  title: "One-line summary"
  description: |
    What the rule checks.
  rationale: |
    Why the rule exists.
  input:
    - layers.apps.apps[*].id
    - bindings[*]
  message_template: |
    Templated string with {placeholders} for runtime values.
  fixable: false
  examples:
    fails: |
      <YAML snippet that triggers the rule>
    passes: |
      <YAML snippet that does not>
  related: [REF-013, REF-015]
```

Field semantics:

- **`id`** — `<CAT>-<NNN>`. Stable across versions; never renumber, never reuse a retired ID. Numbering in tens (`REF-010`, `REF-020`, …) leaves room for insertion without renumbering.
- **`category`** — human-readable form of the prefix; one of `structural`, `reference-integrity`, `capability`, `lifecycle`, `network-sanity`, `include-consistency`, `overlay`, `activity`, `defense`, `cluster`, `persona`.
- **`severity`** — `error` blocks deploy. `warning` proceeds but flags. `info` is observational (alias resolution, redundant declarations); CI suppresses by default.
- **`reproducible_promotes_to_error`** — when `true` and the scenario has `reproducible: true`, severity is promoted to `error`. Used for unpinned refs and missing seeds.
- **`pass`** — execution pass per §2.
- **`input`** — JSONPath-style notation naming the IR (or manifest) fields the rule reads. Documentation, not executable.
- **`message_template`** — operator-facing string with `{placeholder}` substitution. Implementation responsible for type-correct substitution.
- **`fixable`** — `true` if the canvas can offer a one-click fix. CLI ignores this field.
- **`examples.fails` / `examples.passes`** — small YAML snippets. Doubles as test data.
- **`related`** — IDs of conceptually adjacent rules. Documentation only; no runtime semantics.

### 3.1 Severity ladder

Three levels:

- **`error`** — blocks deploy. Validator exits non-zero. Canvas surfaces as a red mark.
- **`warning`** — proceeds but flags. Validator exits zero but reports the finding. Canvas surfaces as a yellow mark.
- **`info`** — observational only. Validator surfaces if `--verbose`; CI suppresses by default. Examples: "alias `oldname` resolved to canonical ID `newname`", "actor `alice` resolved to overlay identity".

### 3.2 The `reproducible_promotes_to_error` mechanism

When a scenario has `reproducible: true` at its top level, every rule whose definition includes `reproducible_promotes_to_error: true` has its severity promoted by one step under that scenario. `info` → `warning`, `warning` → `error`. `error` is already at the ceiling and unchanged.

The pattern this captures: rules that are advisory in casual authoring but must be enforced under strict reproducibility. Unpinned image refs (`REF-130`), unpinned behavior refs (`REF-150`), and missing seeds (`LIF-060`) are the canonical examples.

Modeling these as conditional promotion rather than separate rule IDs keeps the catalog smaller and means an operator searching for "the unpinned image rule" finds one ID, not two.

### 3.3 Rule IDs are stable

A rule ID, once assigned, never changes. If a rule's behavior must change incompatibly:

- The old ID is marked deprecated (severity unchanged; description updated to point at the replacement).
- A new ID is allocated.
- A CHANGELOG entry records both.

A rule's behavior can change *compatibly* — tightened message templates, added related-rule links, clarified descriptions — without an ID change. Severity changes are not compatible; they require an ID change or a CHANGELOG entry.

---

## 4. Implementation: shared library, four entry points

The validator is a **shared library** (`validator` package), not a service. It exposes four entry points:

| Entry point | Caller | Passes run |
|---|---|---|
| `validate_structural(ir)` | `axrange validate`, canvas | Passes 1, 2 |
| `validate_full(ir, manifests)` | `axrange validate` | Passes 1, 2, 3 |
| `validate_with_cluster(ir, manifests, cluster_snapshot)` | `axrange deploy` (first phase) | Passes 1, 2, 3, 4 |
| `explain_rule(rule_id)` | `axrange validate --explain REF-014`, canvas help | Returns the rule definition |

The validator package depends on the IR schema and manifest schema (data formats) and on an optional cluster-snapshot interface. **It has no dependency on the planner.** The planner depends on the validator (it calls `validate_with_cluster` first), not the other way around.

This means:

- `axrange validate` is fast — no cluster connection required, no DAG construction.
- The canvas can call `validate_structural` on every keystroke without leaving the operator's machine.
- CI can run `axrange validate` on every commit; cluster checks are deferred to deploy time.
- The same rule code path runs in all four entry points; an operator never sees inconsistent results between validator and planner.

### 4.1 What the validator does *not* check (delegated to the planner)

After Pass 4, the planner builds the DAG. DAG-shape errors (per `docs/PLANNER.md` §3.5) are the planner's domain:

- Cycle detection across ops.
- Reachability of every op from a root.
- Type consistency of op inputs and outputs.
- Resource-semaphore deadlock detection.

These are **post-DAG-construction** errors, not IR errors. They cannot fire until ops have been emitted. Clean line: validator checks the IR; planner checks the DAG.

---

## 5. Rule catalog

96 IR-side rules across 10 categories (plus 7 cluster rules executed by the planner, plus 11 catalog-side persona rules = 114 total). The per-category counts are: STR 20, INC 5, REF 25, CAP 6, LIF 6, NET 10, OVR 4, ACT 7, DEF 5, FAB 8 (= 96 IR-side), plus CLU 7, plus PSN 11. The per-category tables in §5.1–§5.12 are the authoritative rule list; this lead paragraph and §1 must match their sum. (Ratified 2026-05-28; REF 19→20 / 86→87 with REF-200 per-tap sensor routing 2026-06-03; STR 19→20 + NET 10→11 / 87→89 with STR-190 (`tap:` shape) + NET-110 (tapped-bridge capturable), dynamic taps 2026-06-19; REF 20→21 / 89→90 with REF-210 typed-sensor rule, sensors design 2026-06-23; +FAB 8 / 90→98 with the fabric slice (`FAB-010..080`, Pass 3 — router `routing:` / `fabric:` validation; `docs/design/04-networking-capture.md`), 2026-06-29; +ACT 2 (`ACT-060`/`ACT-070`, interval-shape + seed-recommended) / 98→100 with the activity schedule executor (`docs/SLICE_ACTIVITY.md` §11), 2026-07-10; +REF 3 (`REF-220`/`REF-230`/`REF-240`, `overlays.adversary` reference checks) / 100→103 with the A3 attack/defense build (Stage A default agents, `docs/design/08-exercise-measurement.md`), 2026-07-11; +REF 1 (`REF-250`, a declared `sensors:` block isn't stood up by a `te` deploy) / 103→104, and `NET-110` retargeted to drop its now-obsolete "reachable sensor" clause (subsumed by `REF-250`), with the sensor-agnostic te build (`docs/WORK_ORDER_sensor_agnostic_te.md`, code-review fixes F1/F2), 2026-07-13; +PSN 9 (`PSN-010..090`, persona-catalog validation — §5.12; catalog-side, not part of the IR 4-pass `Check` pipeline, the same "listed for completeness" posture as CLU) / 104→113 with the persona library (`docs/WORK_ORDER_persona_library.md`), 2026-07-24; +REF 1 (`REF-260`, a `te`-tier IR declaring `activity:`/`overlays.adversary:` that a `te` deploy no longer emits) / 113→114 with the Exercise tier build (`docs/WORK_ORDER_exercise_tier.md`), 2026-07-24; **-STR-190 +STR-200 (net 0) - REF-200 (-1) - NET-110 (-1) / 114→112 with the twin substrate becoming tap-agnostic (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) — `tap:` is REMOVED from the IR entirely (D2): STR-190 (the old shape-check, "every `tap:` is a boolean") RETIRES outright (never reused), superseded by the NEW id STR-200 ("any `tap:` presence at all is an error" — a stricter successor rule, not a reuse of the old slot); REF-200 (the `tap_host`/`tap_net`→`sensor` binding kind's reference check) and NET-110 (the tapped-segment-has-≥1-node check) both RETIRE outright too, with no replacement — their premises are removed along with the binding kind and the `tap:` field they validated, 2026-07-28**; +PSN 2 (`PSN-100` transport enum, `PSN-110` peer_scope enum — the two new `emits[]` fields the high-volume persona wave added: UDP transport and `peer_scope: all` fan-out) / 112→114, 2026-07-28; supersedes the earlier "53 rules" headline — see CHANGELOG.)

### 5.1 STR — Structural (Pass 1, halt on error)

| ID | Severity | Title |
|---|---|---|
| `STR-010` | error | YAML parses |
| `STR-020` | error | `scenario_schema_version` present and recognized (`{1, 2}` — widened 2026-07-24, `docs/WORK_ORDER_traffic_tier.md` Part 1; see note below) |
| `STR-030` | error | `scenario.version` present and is a non-negative int |
| `STR-040` | error | `scenario:` header present with required fields (`id`, `version`) |
| `STR-050` | error | `layers:` present with `infra` and `apps` sub-keys (`activity` optional) |
| `STR-060` | error | `bindings:` present (may be an empty list) |
| `STR-070` | error | Every node has exactly one of `net:` or `nets:`; never both, never neither |
| `STR-080` | error | Every node has a `kind:` ∈ {`host`, `router`, `switch`} |
| `STR-090` | error | Every node ID is unique within its layer |
| `STR-095` | error | Every network ID is unique within the scenario |
| `STR-100` | error | Every binding has a `kind:` ∈ the closed set of binding kinds |
| `STR-110` | error | Every `requires:` keyword is in the closed 16-keyword set |
| `STR-120` | error | Every node has exactly one OS-family keyword (`windows` \| `linux` \| `bsd`) in `requires:` |
| `STR-130` | error | Every `install:` action kind is in the closed 6-kind set (`pkg`, `pkg@version`, `config`, `role`, `container`, `script`) |
| `STR-140` | error | Every `overlays:` sub-section is one of {`identity`, `data`, `defense`} |
| `STR-150` | error | `scenario.seed:` is a string or int if present |
| `STR-160` | error | `scenario.reproducible:` is a bool if present (defaults `false`) |
| `STR-170` | info | `aliases:` on a node used during reference resolution — surfaced for transparency |
| `STR-180` | info | Unknown top-level keys observed — informational for forward compatibility |
| `STR-200` | error | Any `tap:` field anywhere (`nodes[].tap`, `nodes[].nets[].tap`, `networks[].tap`) is REJECTED — the field is removed from the IR entirely (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28); author taps in the tap plan (scope+target selector) against the deployed twin's published topology instead. Supersedes the retired `STR-190` (which only checked the flag was a boolean) — this checks for its bare PRESENCE, any value. |

**Notes:**

- `STR-180` is intentionally lenient (info, not error). The IR is git-friendly YAML and forward compatibility favors permissive parsing of unknown keys. Comments use YAML `#` syntax; structured comment-like fields are tolerated.
- `STR-170` fires when an alias resolves to a canonical ID — purely transparency.
- **`STR-130` (fixed 2026-07-07):** now also flags an install action carrying **two or more** of the five action keys (`pkg`, `config`, `role`, `container`, `script`) at once — e.g. `{pkg: nginx, script: setup.sh}`. Previously `install_action_kind()` would silently resolve one key and the double-key malformation went undetected.
- **`STR-020` recognized set widened `{1}` → `{1, 2}` (Authority: user, 2026-07-24, `docs/WORK_ORDER_traffic_tier.md` Part 1).** The IR gains an optional, additive `node.role` field (`docs/IR_SCHEMA.md` §3.3, the Traffic-tier persona-assignable role tag); `role:` is parsed regardless of the declared version, so no `target-environments/*`/`twin-configs/*` content needed to change. `2` is accepted so an author may mark an IR as "authored with `role:` in mind," never required. No rule-count change (STR stays 20 rules; the catalog total stays 113).

### 5.2 INC — Include Consistency (Pass 1, halt on error)

| ID | Severity | Title |
|---|---|---|
| `INC-010` | error | An ID defined in an included fragment is also defined in the main file (or in another fragment) — strict-no-override |
| `INC-020` | error | Included file path resolves (exists, readable, parses as YAML) |
| `INC-030` | error | No circular includes |
| `INC-040` | error | Included fragment's `scenario_schema_version` matches the main file's |
| `INC-050` | info | Fragment is included but defines nothing the main scenario references (unused include) |

**Note (fixed 2026-07-07):** two bugs in the include-merge, both in `axrange/ir/parser.py`:
1. The `fabric:` block's scalar sub-keys (`fabric_asn`, `tenant`) were being **dropped** when set only
   in a fragment — `fabric.regions` (list-mergeable) was folded in correctly, but the scalar keys
   weren't spliced at all. Fixed with the same strict-no-override semantics as other scalar sections
   (a fragment may *set* a scalar key only if the main file hasn't already; a genuine conflicting
   override is `INC-010`).
2. `_collect_ids` (the duplicate-ID scanner backing `INC-010`) used to recurse into **every** nested
   dict looking for a key literally named `id`, so a free-form value-map field that happens to use
   `id` as a key (e.g. `activity[].inline.steps[].id`) could false-trip `INC-010` against an unrelated
   entity elsewhere in the doc. It's now scoped to the IR's known ID-bearing sections (IR_SCHEMA §1.4:
   the scenario header + every section in `_MERGEABLE_LIST_PATHS`) and runs **per-fragment before
   that fragment is merged**, so a real collision is attributed to the actual fragment file that
   introduced it rather than always to the main file.

### 5.3 REF — Reference Integrity (Pass 2, halt on error)

| ID | Severity | Title | `rptoe`* |
|---|---|---|---|
| `REF-010` | error | Binding `app→host` references a defined app | — |
| `REF-020` | error | Binding `app→host` references a defined host | — |
| `REF-030` | error | Binding `activity→host` references a defined activity | — |
| `REF-040` | error | Binding `activity→host` references a defined host | — |
| `REF-050` | error | Binding `activity→targets` references a defined activity | — |
| `REF-060` | error | Binding `activity→targets` targets resolve (each is a defined host, app, or actor) | — |
| `REF-070` | error | Binding `defense→host` references a defined defense tool and host | — |
| `REF-080` | error | Binding `defense→network` references a defined defense tool and network | — |
| `REF-090` | error | Binding `identity→host` references a defined identity and host | — |
| `REF-100` | error | Binding `data→host` references a defined data fixture and host | — |
| `REF-110` | error | Every `net:` / `nets:` ref on a node resolves to a defined network | — |
| `REF-120` | error | Every `image:` ref on a node resolves to a manifest entry | — |
| `REF-130` | warning | Image ref without version pinning (`win11-finance` instead of `win11-finance@v3`); resolves to manifest `current` version | **yes** |
| `REF-140` | error | Every behavior library ref in activity resolves | — |
| `REF-150` | warning | Behavior ref without version pinning; resolves to library `current` version | **yes** |
| `REF-160` | error | Activity `actor:` resolves to either an overlay identity or an implicit local host user | — |
| `REF-170` | warning | Activity references an actor missing from a present `overlays.identity` overlay — resolves to local host user but flagged | — |
| `REF-180` | error | Every `alias:` is unique across the whole IR | — |
| `REF-190` | error | Every binding's `phase:` references a defined phase in `scenario.phases:` (dormant: phases not in IR v1; reserved for forward compatibility) | — |
| `REF-210` | error | A `sensors[]` entry's `type` is a known sensor type (`axrange.sensors` registry: `tcpdump-sensor` / `suricata-sensor` / `zeek-sensor`; absent ⇒ `tcpdump-sensor`), each `captures` entry names a defined network or node, and `transport` names a network with `transport: true`. (sensors design, 2026-06-23.) | — |
| `REF-220` | error | Every `overlays.adversary[]` entry has an `id` (A3 Stage A, 2026-07-11; mirrors `REF-090` for `overlays.identity`) | — |
| `REF-230` | error | Every `overlays.adversary[].targets` entry references a defined host — a fan-out (one entry can plant the Sandcat agent on several hosts), so every target is checked individually (LES-034), not just the first | — |
| `REF-240` | error | Every `overlays.adversary[].targets` entry is a `kind: host` node — the Sandcat agent is planted over QGA, so a non-host target (router/switch) would hang at deploy rather than fail at validate; sibling to `REF-230` (existence), this checks shape/kind, every fan-out binding | — |
| `REF-250` | warning | A declared `sensors:` block is present, but a `te`-tier deploy does not stand it up — no in-scenario sensor VM is synthesized and no `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture` is emitted for it (sensor-agnostic te, 2026-07-13, `docs/WORK_ORDER_sensor_agnostic_te.md`, review fix F1). Sensors-design Phase B synthesis is now `tier == "o2"`-gated — it fires only for `axrange.o2control.control.stand_up_surface`'s standing-surface deploy. WARNING, not error: the shipped `target-environments/` archetypes that still author `sensors:` (`ad-domain-lab`, `purple-team-range`, etc.) keep validating; capture on a deployed `te` is `axrange o2 deploy` (stand the sensor) + `axrange tap deploy` (bind the te's tappable interfaces to it, `docs/WORK_ORDER_tap_eligibility.md`), not this block. | — |
| `REF-260` | warning | A `te`-tier deploy's IR declares `activity:` and/or `overlays.adversary:` — a `te` deploy no longer emits `OP-ACT` or the adversary branch of `OP-OVL-070 install-agent` at all; attack playout, adversary agents, and measurement are now the standalone Exercise tier's job (`exr`, 2026-07-24, `docs/WORK_ORDER_exercise_tier.md`). WARNING, not error: the shipped `target-environments/` archetypes that still author `activity:`/`overlays.adversary:` (`ad-domain-lab`, `purple-team-range`, `soc-detection-lab`) keep validating; they become `exr`-consumed content, not edited. Sibling to `REF-250`'s "declared but not emitted by `te`" shape, for activity/adversary instead of sensors. | — |

\* `rptoe` = `reproducible_promotes_to_error`

**Note (fixed 2026-07-07, corrected 2026-07-07):** `REF-120` skips only when the context's
`skip_unknown_image_lifecycle` flag is set — which `validate_structural` (Pass 1+2 only: the canvas
on every keystroke, shape-only checks) sets on its own context, because that entry point
intentionally runs with an empty `ManifestRegistry()` before manifests are available. The first cut
of this fix gated the skip on "registry is empty" instead of on entry-point *mode*, which
over-applied: `validate_full` (the normal `axrange validate`/`deploy` path) handed an empty registry
by mistake — e.g. a wrong `--images` path — also skipped, so `axrange validate` PASSED over a
completely unloaded image set and the deploy failed cryptically much later (`clone-vm:
template_vmid missing`). `validate_full` never sets the flag, so an unresolvable image ref (whether
from a genuinely-missing manifest entry or a wholesale-unloaded registry) still errors as before.

### 5.4 CAP — Capability (Pass 3, accumulate)

| ID | Severity | Title |
|---|---|---|
| `CAP-010` | error | For every host node: image's `provides:` ⊇ node's `requires:` (kernel and OS-family keywords) |
| `CAP-020` | error | For every `install:` action on a bound app: action kind ∈ image's `install_support:` |
| `CAP-030` | error | For every node: image's OS family matches the node's OS-family keyword |
| `CAP-040` | info | Node `requires:` includes a keyword the image already provides via its OS-family declaration — redundant |
| `CAP-050` | warning | Node `requires:` lists a `host:*` keyword no manifest binding's `requires_host_caps:` declares — operator may have intended a kernel capability |
| `CAP-060` | error | For nodes with `kind: router` or `kind: switch`: capability requirements consistent with kind |

**Notes:**

- `host:*` keywords are validated for *membership* in the closed set (STR-110) and *image-binding awareness* (CAP-050) at this pass, but not for **cluster satisfiability** — that's CLU-010 at Pass 4.
- `CAP-040` is purely advisory and easy to dismiss.
- **`CAP-020` (fixed 2026-07-07):** checks **every** host an app is bound to, not just the last one. The app→host mapping used to be a `dict[app_id, host_id]` built by iterating bindings — a second `app→host` binding for the same app silently overwrote the first, so a multi-host app's install-action support was checked against only its last-bound host and an unsupported action on an earlier host false-passed. The mapping is now `dict[app_id, list[host_id]]` and every host in the list is checked.

### 5.5 LIF — Lifecycle (Pass 3, accumulate)

| ID | Severity | Title | `rptoe` |
|---|---|---|---|
| `LIF-010` | error | No node references a `withdrawn` image version; message includes replacement pointer from manifest | — |
| `LIF-020` | warning | Node references a `deprecated` image version; message includes replacement pointer if present | — |
| `LIF-030` | warning | Activity references a `deprecated` behavior version | **yes** |
| `LIF-040` | error | Activity references a `withdrawn` behavior version | — |
| `LIF-050` | warning | Pinned-but-deprecated: a pinned image/behavior ref's version status changed to `deprecated` after authoring — operator should decide whether to update | — |
| `LIF-060` | warning | Scenario `reproducible: true` and missing top-level `seed:` | **yes** |

**Note on `LIF-050`:** deliberately not promoted under `reproducible: true`. The whole point of reproducibility is that a pinned ref stays valid through its lifecycle. The warning exists so the operator knows the underlying image is no longer the preferred version.

### 5.6 NET — Network Sanity (Pass 3, accumulate)

| ID | Severity | Title |
|---|---|---|
| `NET-010` | error | No two networks in the same scenario have overlapping CIDRs (excluding networks marked `isolated: true`) |
| `NET-020` | error | Every network's `cidr:` is a valid IPv4 or IPv6 CIDR string |
| `NET-030` | error | Every multi-homed node's `nets:` entries reference distinct networks |
| `NET-040` | error | Every multi-homed node's `nets:` IP (if specified) is within the corresponding network's CIDR |
| `NET-050` | error | No two networks in the same scenario have the same `vlan:` ID |
| `NET-060` | warning | Two networks in different scenarios share a VLAN ID (fires only when validator is given a multi-scenario context, e.g. `axrange validate --scenarios target-environments/*.yaml`; dormant by default) |
| `NET-070` | error | Defense `observes:` references a network the defense tool's host cannot see (host's `net:` / `nets:` doesn't include the observed network, and `tap_from:` is not `external-mirror`) |
| `NET-080` | error | Router node has at least one network attachment (`net:` singular or `nets:`) |
| `NET-090` | error | Switch node has at least one `nets:` entry and routes only within a single broadcast domain |
| `NET-100` | warning | A network has no nodes attached (orphan network — usually an authoring leftover) |

**Note (fixed 2026-07-07):** `NET-080`'s attachment count now **unions `node.net` (the singular-NIC
field) with `node.nets`** — previously it counted only `nets:`, so a valid single-LAN `kind: router`
that used the singular `net:` field had 0 counted attachments and false-failed with "0 network
attachments; a router needs ≥ 1 LAN net."

**Note (retargeted 2026-07-13, sensor-agnostic te, review fix F2, `docs/WORK_ORDER_sensor_agnostic_te.md`;
RETIRED OUTRIGHT 2026-07-28):** `NET-110` used to have a second part — every tapped interface
(host/leg/segment) must have a **reachable sensor** (a `tap_host`/`tap_net` binding's sensor, or the
implicit primary declared `sensors:` entry). That part was **removed** 2026-07-13: it validated a
te-side "which sensor does this tap route to" concern that no longer existed at plan time — a `te`
deploy never routed a tap to any sensor (binding was entirely the A2 tap playout's post-deploy job).
`REF-250` (§5.3) covers the general "a declared `sensors:` block is not stood up by a `te` deploy" case
as a WARNING instead, so nothing was silently dropped and nothing double-reported. **`NET-110` itself is
now RETIRED (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28)** — its remaining first
part ("a `networks[].tap` segment has ≥1 node attached") lost its premise the moment `tap:` was removed
from the IR entirely (D2): there is no more tapped-segment concept in the IR for this rule to check.
ID never reused.

### 5.7 OVR — Overlay (Pass 3, accumulate)

| ID | Severity | Title |
|---|---|---|
| `OVR-010` | error | Overlay binding references an overlay sub-section absent from `overlays:` |
| `OVR-020` | warning | Overlay sub-section is present but no bindings reference it |
| `OVR-030` | error | Identity overlay: every group references defined users; every trust references defined domains |
| `OVR-040` | error | Data overlay: every data fixture references a defined classification level (dormant: classification taxonomy not in IR v1; reserved for forward compatibility) |

### 5.8 ACT — Activity (Pass 3, accumulate)

| ID | Severity | Title |
|---|---|---|
| `ACT-010` | error | Activity in `inline:` mode has all required behavior-DSL fields |
| `ACT-020` | error | Activity in `library + overrides:` mode: every overridden field exists in the referenced behavior library version |
| `ACT-030` | warning | Activity has a `schedule:` shorter than the behavior's minimum-meaningful duration (per behavior library metadata) |
| `ACT-040` | error | Activity targets resolve consistently with the activity's binding kind (`activity→host` and `activity→targets` mutually exclusive on the same activity) |
| `ACT-050` | warning | Per-activity seed is set with no `schedule.interval` to seed — silently ignored (per IR_SCHEMA §2); **lifted for seeded schedules** (activity schedule executor, 2026-07-10) — a `seed:` that seeds a jittered `schedule.interval` is honored, not ignored, so this rule only fires when the seed has nothing to seed |
| `ACT-060` | error | `schedule.interval` is well-formed: both `min`/`max` present, `min` ≥ the 1s floor, `max` ≥ `min`, mutually exclusive with `once: true` (activity schedule executor, 2026-07-10) |
| `ACT-070` | warning | A jittered activity (`schedule.interval` set) has no `seed:` — the fire sequence is non-reproducible across replay runs (activity schedule executor, 2026-07-10) |

### 5.9 DEF — Defense (Pass 3, accumulate)

| ID | Severity | Title |
|---|---|---|
| `DEF-010` | error | Defense tool with `installed_on:` binding: target host's image must support the tool's install action kind (defense-specific message; underlying logic shared with `CAP-020`) |
| `DEF-020` | error | Defense tool with `observes:` binding: `tap_from:` ∈ {`bridge`, `per-vm`, `external-mirror`} |
| `DEF-030` | error | Defense with `tap_from: per-vm`: the observed network must have at least one node attached |
| `DEF-040` | warning | Defense with `tap_from: external-mirror`: requires sensor/capture configuration outside the scenario; flagged so operator confirms sensor is set up |
| `DEF-050` | error | Defense bound via both `observes:` and `installed_on:`: bindings must be consistent (host that runs the tool must have access to the observed network) |

### 5.10 CLU — Cluster (Pass 4, planner-side)

Executed by the planner after taking a Proxmox cluster snapshot. Listed here for completeness; `axrange validate` (without cluster access) skips this pass.

| ID | Severity | Title |
|---|---|---|
| `CLU-010` | error | For every host node: at least one Proxmox node satisfies the union of IR `host:*` requirements and binding `requires_host_caps:` (placement satisfiability) |
| `CLU-020` | error | Sum of `resources.ram` across nodes placed on each Proxmox node ≤ available RAM × headroom factor |
| `CLU-030` | error | Sum of `resources.cpu` across nodes placed on each Proxmox node ≤ available CPU × headroom factor |
| `CLU-040` | error | Sum of `resources.disk` across nodes placed on each Proxmox node ≤ free storage on configured pool |
| `CLU-050` | error | Every `vlan:` ID in the scenario is not currently in use by a different scenario or non-axRange resource |
| `CLU-060` | error | Every image binding's `template_vmid` exists on the cluster (or on shared storage if `storage.node: any`) |
| `CLU-070` | warning | Cluster headroom after this deploy < 20% — flagged so operator confirms the deploy doesn't starve other scenarios |

Default headroom factor is 0.8 (20% retained). Planner-tunable.

### 5.11 FAB — Fabric (Pass 3, accumulate)

The fabric slice's router `routing:` / top-level `fabric:` validation (`docs/design/04-networking-capture.md`), ported from the sibling dtDataCenter lab. IR-side, Pass 3 — listed after CLU here to
avoid renumbering the CLU section's existing cross-references (`docs/PRODUCT_GAPS.md` §5.10); see §2
for the pass assignment.

| ID | Severity | Title |
|---|---|---|
| `FAB-010` | error | A node with `routing:` is `kind: router` |
| `FAB-020` | error | `routing.region` resolves to a `fabric.regions[].id` |
| `FAB-030` | error | `routing.role` ∈ {`spine`, `leaf`, `border`, `abr`, `area`, `provider`, `edge`} |
| `FAB-040` | error | `bgp.rr_clients_of` / `ebgp_peers.peer` references resolve (node ids / reachable IPs) |
| `FAB-050` | error | A region with `igp: isis`/`both` ⇒ each of its routers declares `routing.isis.net` |
| `FAB-060` | error | A `networks[].vni` is used only by leaves that attach it; `vni` is a valid VNI int |
| `FAB-070` | warning | `routing.loopback` set when `bgp`/`evpn` present (needed for update-source/VTEP) |
| `FAB-080` | error | `fabric:` present ⇒ every fabric router's `image` is a VyOS image (VyOS-only slice) |

### 5.12 PSN — Persona (catalog-side, not part of the IR 4-pass pipeline)

The persona library's own content checks (`docs/design/02-twin-model.md`,
`docs/WORK_ORDER_persona_library.md`). A persona is **catalog content**
(`axrange/personas/catalog/*.yaml`), not Scenario-IR content — no `bindings:`/node in any Scenario
IR references a persona yet (that binding is `twin_configs`/the Traffic tier's job), so there is no
`ValidatorContext` to run these against. Registered here (shared `Rule`/`Finding`/`Severity`
vocabulary, so `axrange validate --explain PSN-0xx` and the rule-count audit both work uniformly)
at `pass_=4`, the same "listed for completeness, no `PASS_4_CHECKS` list" posture §5.10's CLU
category already has — but execution is `axrange.validator.checks.personas.validate_persona()` /
`validate_catalog()` called directly (by a future `twin_configs`/Traffic-tier binding step, and by
its own test suite), not the IR engine's Pass 1–4 dispatch. That module reads the
`axrange.personas` registry directly (validator → registry, never the reverse — the same direction
`checks/references.py`'s REF-210 uses for `axrange.sensors`).

| ID | Severity | Title |
|---|---|---|
| `PSN-010` | error | Persona `id` is present and non-empty |
| `PSN-020` | error | Persona `os_family` is `windows` or `linux` |
| `PSN-030` | error | Persona `drive` is `scripted`, `replay`, `real-service`, `command`, or `browser` (`command` added 2026-09-02, `browser` 2026-09-21 — both purely additive enum widenings: no previously valid persona becomes invalid and the rule **count is unchanged**) |
| `PSN-040` | error | Every `emits[]` entry names a known `proto` |
| `PSN-050` | error | Every `emits[]` entry has a `peer_role` set (non-empty) |
| `PSN-060` | error | Every `emits[]` `cadence` is well-formed (`diurnal`/`24x7`/`cyclic:<duration>`/`burst:<duration>`) |
| `PSN-070` | error | Every `emits[]` `volume` is `low`, `med`, `high`, or `bulk` (`bulk` added 2026-07-28 with the volume lever — a level now resolves message count **and** bytes-per-message **and** inter-message gap, not a message count alone) |
| `PSN-080` | warning | `requires[]` is satisfiable against a twin's published role set — a no-op without one supplied (context-free catalog validation), mirroring CLU-060's cluster-context dependency |
| `PSN-090` | error | Every non-reserved, non-`optional` `emits[].peer_role` is declared in the persona's own `requires[]` (the catalog-checkable half of peer_role satisfiability; sibling to PSN-080, which needs an external role set). An `emits[].optional: true` entry — a peer the persona talks to only IF PRESENT (a dedicated DNS resolver, a print server, ...) — is exempt, same as a reserved token (2026-07-25 refinement) |
| `PSN-100` | error | Every `emits[]` `transport` is `tcp` or `udp`. An **omitted** value is derived from `proto` (`personas.schema.default_transport` — udp for dns/ntp/snmp/syslog/netflow/dhcp/mdns/llmnr/nbns/ssdp/coap/rtp/stun/sip/ike/wireguard, tcp otherwise) and is always valid, so this fires only on an explicitly-authored bad value (2026-07-28) |
| `PSN-110` | error | Every `emits[]` `peer_scope` is `one` or `all`. `all` fans the emit out across **every** twin host carrying the role (excluding the instance's own host) — what an authorized subnet-sweeping scanner or a peer-to-peer mesh does; `one` (the default) drives a single resolved peer (2026-07-28) |

---

## 6. Worked example: a single rule

Full schema for one rule, illustrative of the form:

```yaml
- id: CAP-010
  category: capability
  severity: error
  reproducible_promotes_to_error: false
  pass: 3
  title: "Image does not provide all capabilities the node requires"
  description: |
    For every host node, the resolved image's `provides:` capability set
    must be a superset of the node's `requires:` set, restricted to
    OS-family and `kernel:*` keywords. `host:*` keywords are checked
    separately at Pass 4 against live Proxmox node capabilities.
  rationale: |
    The capability intersection is the IR's contract that a scenario
    can be deployed against a given image. Violating it at validate
    time prevents a guaranteed deploy failure at runtime.
  input:
    - layers.infra.nodes[*].requires
    - layers.infra.nodes[*].image
    - manifests[*].versions[*].provides
  message_template: |
    Node "{node_id}" requires capabilities {missing_caps} that image
    "{image_ref}" does not provide. Image provides: {provided_caps}.
    Consider: (a) a different image version that provides the
    missing capabilities; (b) removing the requirement if the
    scenario does not actually need it.
  fixable: false
  examples:
    fails: |
      layers:
        infra:
          nodes:
            - id: h-fin
              kind: host
              requires: [windows, kernel:edr-hooks]
              image: win11-finance@v2   # v2 does not provide kernel:edr-hooks
    passes: |
      layers:
        infra:
          nodes:
            - id: h-fin
              kind: host
              requires: [windows, kernel:edr-hooks]
              image: win11-finance@v4   # v4 added kernel:edr-hooks
  related: [CAP-020, CAP-030, STR-110, STR-120]
```

The full set of 112 rule definitions (96 IR-side + 7 CLU + 9 PSN) follows this schema. Implementation will likely materialize them as YAML at `validator/rules/*.yaml` or as code-defined dataclasses; the spec is the surface, not the storage format.

---

## 7. Non-goals: what the validator deliberately does *not* check

Explicit to prevent scope creep:

1. **Semantic realism of attacker scenarios** — the validator doesn't check that a red-team activity is "realistic" or matches MITRE technique X. AI agents' domain during authoring.
2. **Behavior library content** — the validator confirms a behavior reference resolves; it does not verify the behavior does what its name implies.
3. **Image content beyond manifest claims** — the validator trusts the manifest's `provides:` and `install_support:`. Verifying an image actually provides what it claims is the **image build pipeline's** job (OPEN_QUESTIONS Q6 — "conformance tests").
4. **Runtime behavior** — does the deployed scenario actually work? Smoke-test concern, not validator.
5. **Defensive-tool efficacy** — does Snort actually catch the attack? Out of scope.
6. **CIDR collisions with the operator's home network or Proxmox management network** — validator only checks within-scenario CIDRs. Real-world collision detection is operator responsibility (or a future CLU rule).
7. **License / IP / classification of behaviors, images, activities** — out of scope.
8. **Spelling of IDs** — `host-fin` vs `host_fin` is a typo, not a rule violation. `REF-*` rules catch the result (dangling ref), not the cause.

---

## 8. Operator-facing output

The validator's CLI output format:

```
$ axrange validate scenario.yaml

Validating scenario.yaml against scenario_schema_version=1...

  ✓ Pass 1 (structural + includes): 0 findings
  ✓ Pass 2 (reference integrity):   0 findings
  ! Pass 3 (semantic):              2 findings

CAP-010 [error]  Node "h-fin" requires capabilities [kernel:edr-hooks] that
                 image "win11-finance@v2" does not provide.
                 Image provides: [windows, kernel:powershell-5plus,
                 kernel:dotnet-clr].
                 Consider: (a) a different image version that provides the
                 missing capabilities; (b) removing the requirement.
                 → see also: CAP-020, CAP-030
                 → fix: axrange validate --explain CAP-010

LIF-020 [warn]   Node "h-corp-dc" uses deprecated image
                 "win-server-2019@v1". Replacement: "win-server-2022@v1".

Result: FAIL (1 error, 1 warning).
```

Conventions:

- Errors first, warnings second, info last.
- Each finding shows: rule ID, severity, message (with substituted placeholders), and `→ see also:` / `→ fix:` lines.
- Final summary line: `Result: PASS` / `Result: PASS (N warnings)` / `Result: FAIL (N errors, M warnings)`.
- Exit code: 0 for PASS or PASS-WITH-WARNINGS; non-zero for FAIL.

`axrange validate --explain <id>` prints the full rule definition (description, rationale, examples).

Canvas output is structurally identical — same findings, same IDs, just rendered as inline annotations rather than terminal text.

---

## 9. Open follow-ups (deferred from v1)

Tracked here for visibility; CHANGELOG-able additions when needed:

- **Rule-by-rule severity tuning** — current severities are best-guess; some warnings may need to be errors (or vice versa) once operators run real scenarios through. Re-tuning is CHANGELOG-able and does not require new rule IDs.
- **Phases support** — `REF-190` is reserved. Activates when phases ratify in IR (see IR_SCHEMA §11 open follow-ups).
- **Data classification taxonomy** — `OVR-040` is reserved. Activates when classification taxonomy ratifies.
- **Cross-scenario static analysis** — `NET-060` is the only current rule that operates across multiple scenarios. Expanding to other cross-scenario checks (shared overlays, shared identity domains) is a CHANGELOG-able addition.
- **Validator caching** — re-validating an unchanged IR could short-circuit. Implementation optimization; not part of the spec.
- **Rule disable mechanism** — `axrange validate --disable-rule CAP-040` for noisy advisory rules. Not in v1; if operator experience shows the need, CHANGELOG-able.
- **Severity escalation in CI mode** — `axrange validate --strict` treating warnings as errors. Easy addition once the catalog stabilizes; deferred to avoid premature flag bloat.
- **Localization of error messages** — message templates are English-only in v1.
