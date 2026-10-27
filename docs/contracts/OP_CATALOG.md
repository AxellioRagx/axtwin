# Op Catalog (v1)

> **Terminology (2026-06-23, `docs/design/06-taps-sensors.md`, ratified):** capture receivers
> throughout this doc are typed, **in-scenario `sensor`s** (`tcpdump-sensor` / `suricata-sensor` /
> `zeek-sensor`) declared in the IR `sensors:` block and deployed as VM(s) with the scenario. The
> capture **mechanics** (per-tap `tc` mirror → keyed gretap → flow-based decap) are unchanged from
> the earlier standing-appliance design — only the *receiver* moved from a standing appliance to
> an **in-scenario sensor VM** (Phase B **live-verified on dev45 2026-06-23**: deploy →
> verify-capture → destroy, all green). The former standing "Suricata network-sensor" is now the
> **suricata-sensor** type.
>
> **Target direction (O2, `docs/design/05-o2-surface.md`, 2026-07-08):** this in-scenario-VM model —
> `OP-MIR-050 deploy-sensor` and everything it implies below — is **superseded in direction** by
> standing `o2_` sensors stood up separately from the deploy. **It remains the current
> implementation** until that refactor lands; this catalog documents what the engine emits today.

Authoritative enumeration of the planner's operation types. Companion to `docs/PLANNER.md` (which defines op *semantics* — kinds, idempotency, failure, concurrency, re-planning) and consumer of `docs/IR_SCHEMA.md` + `docs/IMAGE_MANIFEST.md` (which define the operands). Where `docs/PLANNER.md` says "the op catalog (§11), not free-form," this is that catalog.

**Status:** ratified for v1. The op `name` values, their `inputs`/`outputs` signatures, `after:` ordering, `cleanup_strategy`, and `idempotency_class` are the contract the adapter implements against. Adding an op is a CHANGELOG-able addition; changing an existing op's signature, removing an op, or renaming one requires a CHANGELOG entry. Catalog IDs (`OP-<CAT>-<NNN>`) are stable across versions: never renumber, never reuse a retired ID.

**Relationship to PLANNER.md.** PLANNER.md §2.3 defines the op-definition field set. This catalog instantiates that field set for every v1 op type and adds three catalog-side fields (`id`, `redo_on`, `description`) plus one field promoted into PLANNER.md §2.3 by the same CHANGELOG entry that ratifies this catalog (`resource_categories`). See §3 below for the full field list and which fields live where.

---

## 1. What this catalog is

The planner's DAG-construction Pass 1 (`docs/PLANNER.md` §3.1) walks the IR and emits **op instances**. Each instance is one application of an **op type** from this catalog to a specific IR scope (`clone-vm[h-fin]` is an instance of the `clone-vm` op type applied to node `h-fin`). This document is the closed set of op types for v1.

The catalog answers three questions the planner can't answer on its own:

1. **What ops exist** — the closed list of op types Pass 1 may emit.
2. **What each op consumes and produces** — typed `inputs`/`outputs`, which drive the data-flow edges built in Pass 2 (`docs/PLANNER.md` §3.2).
3. **What each op must run after** — `after:` barrier/op patterns, which drive the ordering edges built in Pass 3 (`docs/PLANNER.md` §3.3).

The catalog does **not** define DAG-construction logic, retry/backoff numbers, the state-store schema, or concurrency limits — those are PLANNER.md's. The clean line: PLANNER.md says *how ops behave*; this catalog says *which ops exist and what they're wired to*.

---

## 2. Definitional anchors

Three distinctions are decided once here and applied uniformly across the catalog, so they are not re-litigated per op.

### 2.1 `action` vs. `wait` vs. `barrier`

`docs/PLANNER.md` §2.2 defines the three kinds. The catalog applies them by this rule:

- **`action`** — the op mutates external state (Proxmox API, OVS, kernel networking, an Ansible play). It has typed `inputs`, typed `outputs`, a `cleanup_strategy`, an `idempotency_class`, and `resource_categories`. One attempt runs to completion or errors; retry is governed by idempotency (§4 of PLANNER.md).

- **`wait`** — the op polls for **someone else's side effect that the planner does not control directly** to complete. The load-bearing distinction: a `wait` op exists when the thing being awaited is produced *inside* a guest or *by* an external subsystem we can only observe (cloud-init finishing inside a VM; a domain controller's LDAP starting to answer; a guest agent coming online). A `wait` op has `inputs` (what to poll, and from where) and typically one output: a readiness signal other ops can depend on. It has **no** `cleanup_strategy` and **no** `idempotency_class` — polling is read-only and idempotent by construction; PLANNER.md §4.5 governs its timeout behavior.

- **`barrier`** — a synthetic milestone with no execution (`docs/PLANNER.md` §2.2, §3.3). It has no `inputs`, no `outputs`, no `cleanup_strategy`, no `idempotency_class`, no `resource_categories`, no `implementation`, no `timeout`. Its only catalog content is its `id`, `name`, `description`, and the **predecessor set** it gathers (documented per barrier in §5.8). Downstream ops depend on barriers, not on the specific implementation ops behind them, so that op composition can change without rewiring consumers (`docs/PLANNER.md` §3.3).

**The line between a `wait` op and an `action` op's check-then-act.** Both perform an observable check. They differ by *whose state* they check. A `wait` op checks **another party's** side effect (the guest, an external service). An `action` op's check-then-act on entry (Model C, `docs/PLANNER.md` §4.2) checks **its own** prior partial state ("did a previous attempt of *this* op already create this OVS bridge?"). Same observation mechanism; different responsibility; different op kind.

### 2.2 `idempotency_class` — the two ratified values

`docs/PLANNER.md` §2.3 defines `idempotency_class` with exactly two values, and the catalog conforms:

- **`infra`** — Model C (`docs/PLANNER.md` §4.2): deterministic resource naming from `{plan_id, op_id}`, check-then-act on entry, cleanup-on-partial-state before redo. Used by every `action` op that touches Proxmox API / OVS / kernel networking directly.
- **`config`** — Ansible-native idempotency (`docs/PLANNER.md` §4.3): the op wraps a playbook; Ansible's task-level idempotency does the work; the adapter re-runs the playbook on retry.

`wait` and `barrier` ops **omit** `idempotency_class` entirely — it is N/A for them, determined by `kind`. The catalog does not invent per-kind idempotency pseudo-classes; `kind` already carries that information.

### 2.3 `resource_categories` — the closed set (with one addition)

Each `action` and `wait` op declares the concurrency-semaphore categories it occupies, per `docs/PLANNER.md` §8.1. v1 categories:

| Category | Scope | Serializes because |
|---|---|---|
| `proxmox-cluster` | Cluster-wide | Proxmox API rate limits. |
| `proxmox-node:<name>` | One Proxmox node | Local clone/disk-I/O pressure. |
| `ovs:<name>` | One node's OVS instance | OVS command interactions. |
| `vm:<vmid>` | One VM | Two ops mutating the same VM must serialize. |
| `ansible-pool` | Orchestrator-wide | Cap on concurrent playbooks. |
| `gretap:<sensor>` | One sensor's transport iface | **Added by this catalog.** Kernel netlink ops attaching/detaching GRE tunnels on a sensor's gretap interface serialize; two concurrent `OP-MIR` tunnel-attach ops to the same sensor would race on the kernel side. |

The `gretap:<sensor>` category is a design-Authority addition to `docs/PLANNER.md` §8.1, recorded in the CHANGELOG entry that ratifies this catalog. Default limit: **1** (fixed — serialization is correctness, not tuning), matching the `ovs:<name>` and `vm:<vmid>` pattern.

---

## 3. Op definition schema

Every op type is defined by the following fields. Fields marked **(PLANNER §2.3)** are inherited verbatim from the ratified planner op-definition field set; **(promoted)** is moved into PLANNER §2.3 by this catalog's CHANGELOG entry; **(catalog)** fields live only here, with a pointer from PLANNER §2.3.

```yaml
- id: OP-HOST-010                    # (catalog) stable handle: OP-<CAT>-<NNN>, never renumbered
  name: clone-vm                     # (PLANNER §2.3) canonical op-type identifier; used in op_id
  kind: action                       # (PLANNER §2.3) action | wait | barrier
  description: |                     # (catalog) one-paragraph human summary
    What this op does, in prose.
  inputs:                            # (PLANNER §2.3) map[name → type]; sources: IR, manifest, prior outputs
    template_volid: volid:str
    target_node: node-name:str
  outputs:                           # (PLANNER §2.3) map[name → type]; consumed downstream via data-flow edges
    vmid: vmid:int
  after:                             # (PLANNER §2.3) barrier/op patterns this op orders after
    - network-ready[$net]
  cleanup_strategy: redo             # (PLANNER §2.3) redo | escalate (action ops only)
  idempotency_class: infra           # (PLANNER §2.3) infra | config (action ops only; omitted for wait/barrier)
  resource_categories:               # (promoted → PLANNER §8.1) semaphores this op occupies
    - proxmox-cluster
    - proxmox-node:$target_node
  implementation: proxmox.clone_template   # (PLANNER §2.3) adapter function ref (illustrative; adapter spec is Q5)
  timeout: 600s                      # (PLANNER §2.3) per-attempt wall-clock timeout
  redo_on:                           # (catalog) IR field paths whose mutation redoes this op (feeds redo rules table)
    - nodes[*].image
```

Field-presence by kind:

| Field | `action` | `wait` | `barrier` |
|---|---|---|---|
| `id`, `name`, `kind`, `description` | ✓ | ✓ | ✓ |
| `inputs` | ✓ | ✓ | — |
| `outputs` | ✓ | ✓ (readiness signal) | — |
| `after` | ✓ | ✓ | ✓ (predecessor set) |
| `cleanup_strategy` | ✓ | — | — |
| `idempotency_class` | ✓ | — | — |
| `resource_categories` | ✓ | ✓ | — |
| `implementation` | ✓ | ✓ | — |
| `timeout` | ✓ | ✓ | — |
| `redo_on` | ✓ | rare | — |

**On `redo_on` and the redo rules table.** `docs/PLANNER.md` §9.2 defers the redo rules table as a sibling deliverable. Rather than maintain that table as a second source of truth, each op carries a `redo_on:` list naming the IR field paths whose mutation forces this op to redo. The redo rules table (`docs/REDO_RULES.md`, when written) becomes a **derived view** — group ops by `redo_on` field path — not an independently-authored table. This keeps a single source of truth and matches the drift-avoidance discipline in `docs/WORKFLOW.md`. The illustrative rows already in PLANNER §9.2 (`nodes[*].image` → `clone-vm`, `nodes[*].resources` → `configure-vm-hardware`, etc.) are exactly the `redo_on` assignments this catalog formalizes.

**On `implementation`.** The adapter function references shown per op are **illustrative**. The Proxmox adapter is OPEN_QUESTIONS Q5 (still in design); its concrete function names are not ratified here. The catalog commits to *which adapter capability* each op needs, not to the adapter's internal API. When Q5 ratifies, the `implementation` refs become real; until then they document intent.

---

## 4. Category list

Eight op categories. Ordered roughly by execution sequence for a typical host bring-up.

| Prefix | Name | Covers | Op count |
|---|---|---|---|
| `OP-NET` | Network creation | SDN zone / vnet / subnet / OVS bridge per scenario network | (§5.1) |
| `OP-HOST` | Host lifecycle | Clone, configure hardware, attach NICs, render+inject cloud-init, start, wait for guest agent, wait for OS | (§5.2) |
| `OP-MIR` | Mirror plumbing | Per-tap `tc` mirror hook install, GRE tunnel attach on sensor, deploy-time capture heartbeat (`verify-capture`) | (§5.3) |
| `OP-APP` | App install dispatch | One op per `install:` action kind (`pkg`, `pkg@version`, `config`, `role`, `container`, `script`) | (§5.4) |
| `OP-OVL` | Overlay materialization | Identity (domain create, user create, host join), Data (dataset seed, secret inject), Defense (host-agent install, network observer setup) | (§5.5) |
| `OP-ACT` | Activity actor | Resolve actor identity, deploy behavior agent, start activity, wait for actor-ready | (§5.6) |
| `OP-TFC` | Traffic-tier persona driving | (Re)start one benign-persona instance on an already-deployed twin host — `docs/WORK_ORDER_traffic_tier.md`, the `tfc` tier | (§5.7) |
| `OP-BAR` | Synthetic barriers | `network-ready`, `host-ready`, `domain-ready`, `mirror-ready`, `overlay-ready`, `fabric-ready` | (§5.8) |

The op-type `name` values already cited in ratified PLANNER.md text are used **verbatim** here: `create-vnet`, `clone-vm`, `configure-vm-hardware`, `attach-vm-to-vnet`, `install-ovs-mirror`, `provision-app`, `start-activity`, `wait-os-ready`, and the barriers `network-ready` / `host-ready` / `domain-ready`. The catalog extends this set; it does not rename any member of it.

---

## 5. The op catalog

**Scope-pattern convention.** Op instances are identified by an op type plus a scope. In `after:` clauses and instance references, scope is written in brackets after the op/barrier name:

- `[$var]` — a **bound scope variable** resolved from the emitting context. `network-ready[$net]` in a host op means "the network this host is on." `create-sdn-zone[$scenario]` means "this scenario's zone."
- `[<x>@<y>]` — a **compound scope**, used when one IR entity yields multiple instances. `attach-vm-to-vnet[<host>@<net>]` is one instance per (host, network) pair; it collapses to `[<host>]` for single-homed nodes.
- `[*]` — **all instances in the current scenario** of that op type. `apply-sdn-config after: create-vnet[*]` means "after every vnet op in this scenario." `[*]` never crosses scenario boundaries.
- `[$entity@*]` — **all sub-instances of a bound entity.** `attach-vm-to-vnet[$host@*]` means "every NIC-attach op for this host." Combines a bound variable with the all-instances wildcard, scoped to that entity rather than the whole scenario.

This convention is catalog-side notation for the `after:` patterns that `docs/PLANNER.md` §3.3 describes as "scope-substitution patterns"; it does not change planner semantics.

### 5.1 `OP-NET` — Network creation

A scenario gets one Proxmox SDN Zone (`docs/ARCHITECTURE.md` §250). **Zone type is auto-derived (ratified 2026-06-03, CHANGELOG "SDN-native networking ratified"; see `OP-NET-010` `zone_type`):** if no network declares a `vlan`, a **Simple** zone is used and each `infra.networks[]` entry gets **its own OVS bridge** (vnet = bridge); if any network declares a `vlan`, a **VLAN** zone is used — **one underlay OVS bridge per zone**, with each network a VLAN-tagged vnet on it. All definitions are staged and committed by a single cluster-wide SDN apply.

**Decomposition notes:**

- PLANNER §3.1 sketched "each network entry → 1 op (`create-vnet`)." This catalog **refines that to two ops per network** (`create-ovs-bridge` + `create-vnet`), because bridge creation is a distinct side effect (one op per side effect, `docs/PLANNER.md` §2.1). The bridge was historically required to be an OVS bridge so the port-mirror could bind; under **Option 1 (2026-06-04, CHANGELOG)** capture is a per-tap `tc` mirror and no longer depends on the bridge type, so that requirement is retired (whether `create-ovs-bridge` still creates an OVS bridge vs. a Linux bridge is an open reconciliation item).
- `create-sdn-zone` emits **once per scenario**; `create-vnet` emits **once per network entry**; `apply-sdn-config` emits **once per scenario** as the commit/synchronization point. `create-ovs-bridge` cardinality is **zone-type-dependent (2026-06-03):** **once per network** in a Simple zone (vnet = bridge), **once per scenario/zone** in a VLAN zone (the single underlay bridge). The earlier "once per network entry, regardless" wording was the slice-MVP direct-bridge assumption (retired with the bypass).
- OVS bridges materialize **cluster-wide** on SDN apply. `create-ovs-bridge` therefore declares only `proxmox-cluster` (not `proxmox-node:*`): a bridge present on a node that ends up hosting no scenario VMs is harmless and free, and this sidesteps the ordering tension that bridge placement would otherwise have with Pass-4 VM placement (`docs/PLANNER.md` §3.4).
- **There is no `create-transport-vnet` op.** `vnet-transport` is cluster-level platform infrastructure (`docs/ARCHITECTURE.md` §332), created once at platform-install time, not per scenario. The catalog assumes it pre-exists; its absence is a Pass-4 cluster-rule failure (`CLU`, `docs/VALIDATOR.md` §5), not an op.
- **Op-def reconciliation (done 2026-06-04, implementation).** The `OP-NET` YAML blocks below are reconciled to the shipped registry (CHANGELOG 2026-06-03 "SDN-native OP-NET ops implemented"): `create-ovs-bridge` runs **before** `create-sdn-zone` (a VLAN zone references the pre-created underlay), `create-sdn-zone` takes the underlay `bridge_name` input, and `create-vnet` carries **no** `bridge_name` input (an SDN vnet needs only its zone + tag). The earlier pre-SDN-native ordering/inputs are gone.

```yaml
- id: OP-NET-010
  name: create-sdn-zone
  kind: action
  description: |
    Create the per-scenario Proxmox SDN Zone, type auto-derived (Simple when no
    network declares a VLAN; VLAN otherwise). A VLAN zone references the
    pre-created OVS underlay bridge, so this runs AFTER create-ovs-bridge.
  inputs:
    scenario_id: scenario-id:str
    zone_type: sdn-zone-type:str        # "simple" | "vlan"; planner-derived from whether any network sets vlan
    bridge_name: ovs-bridge-name:str    # the VLAN underlay; absent/ignored for Simple zones
  outputs:
    zone_id: sdn-zone-id:str
  after:
    - create-ovs-bridge[*]              # VLAN zone references the pre-created underlay
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
  implementation: proxmox.sdn_create_zone   # illustrative; adapter is Q5
  timeout: 60s
  redo_on: []                           # zone identity is the scenario; no IR field redoes it in isolation

- id: OP-NET-020
  name: create-ovs-bridge
  kind: action
  description: |
    Create the bridge backing the scenario networks. VLAN zone: ONE underlay
    bridge per zone (emitted once); Simple zone: one per network (the vnet
    materializes it). Runs FIRST, before the SDN zone (a VLAN zone references this
    underlay). Historically required to be an OVS bridge so the port-mirror could
    bind; under Option 1 (2026-06-04) capture is a per-tap tc mirror and no longer
    depends on the bridge type. Materializes cluster-wide on SDN apply.
  inputs:
    net_id: net-id:str
    zone_type: sdn-zone-type:str        # "simple" | "vlan"; governs cardinality (per-net vs per-zone)
  outputs:
    bridge_name: ovs-bridge-name:str
  after: []                             # runs first; precedes the SDN zone
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
  implementation: proxmox.sdn_create_ovs_bridge   # illustrative; adapter is Q5
  timeout: 60s
  redo_on: []                           # bridge name derives from scenario ID; not redone by an IR field change alone

- id: OP-NET-030
  name: create-vnet
  kind: action
  description: |
    Create one SDN vnet inside the scenario zone — VLAN-tagged on the underlay for
    a VLAN zone, or the network's own bridge for a Simple zone. PVE materializes a
    bridge named after the vnet that VMs attach to. Carries the network's
    CIDR/gateway/DHCP/DNS metadata where the SDN layer represents it. No
    bridge_name input: an SDN vnet needs only its zone + tag.
  inputs:
    zone_id: sdn-zone-id:str
    net_id: net-id:str
    cidr: cidr:str
    vlan: vlan-tag:int                   # optional; absent for untagged Simple-zone nets
    gateway: ip:str                      # optional
    dns: ip-list:list                    # optional
    dhcp: dhcp-config:obj                # optional {enabled, range}
  outputs:
    vnet_id: vnet-id:str
  after:
    - create-sdn-zone[$scenario]
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
  implementation: proxmox.sdn_create_vnet   # illustrative; adapter is Q5
  timeout: 60s
  redo_on:
    - infra.networks[*].vlan
    - infra.networks[*].cidr
    - infra.networks[*].gateway
    - infra.networks[*].dns
    - infra.networks[*].dhcp

- id: OP-NET-040
  name: apply-sdn-config
  kind: action
  description: |
    Commit all staged SDN definitions (zone, bridges, vnets) with one cluster-wide
    SDN reload. This is the single synchronization point that makes the scenario's
    networks usable; all OP-NET create-* ops must complete before it runs.
  inputs:
    zone_id: sdn-zone-id:str
  outputs:
    applied: bool
  after:
    - create-sdn-zone[$scenario]
    - create-ovs-bridge[*]              # all bridges in this scenario
    - create-vnet[*]                    # all vnets in this scenario
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
  implementation: proxmox.sdn_apply         # illustrative; adapter is Q5
  timeout: 120s
  redo_on: []                           # re-applied whenever any contained create-* op redoes
```

**Barrier wiring.** `network-ready[<net-id>]` (§5.8) depends on `apply-sdn-config[$scenario]` — once the SDN config is applied, every vnet in the scenario is usable. Per-network granularity of the barrier is preserved for downstream clarity even though the apply is scenario-wide: a host on `corp` depends on `network-ready[corp]`, which depends on the single `apply-sdn-config`.

### 5.2 `OP-HOST` — Host lifecycle

One node (`infra.nodes[]`, any `kind:`) becomes a running, OS-ready VM through this category. The spine is a serial chain (clone → hardware → cloud-init → attach → start → wait-agent → wait-os) with two deliberate parallelism points noted below.

**Decomposition notes:**

- **Mirror install is not here.** PLANNER §3.1 listed "install mirror" inline in host bring-up; this catalog places it in `OP-MIR` (§5.3). The `host-ready` barrier still depends on the mirror op (`docs/ARCHITECTURE.md` §288 makes the mirror hook mandatory), but the op is categorized under mirror plumbing. Behavior unchanged; categorization only.
- **`attach-vm-to-vnet` is per-NIC.** A multi-homed node (`nets: [...]`) emits one attach op per network, scoped `attach-vm-to-vnet[<host>@<net>]`; a single-homed node collapses to `attach-vm-to-vnet[<host>]`. Refines PLANNER §9.2's `attach-vm-to-vnet[<host>]`.
- **`configure-cloud-init` is one op.** The render (compute user-data / unattend.xml from IR + binding) is an orchestrator-side input computation with no external state to be idempotent about; it folds into the single inject op that sets the `cicustom` drive (the only externally-observable side effect).
- **Two waits.** `wait-guest-agent` polls the QEMU guest agent (VM is alive); `wait-os-ready` polls SSH/WinRM reachability against the binding's `first_boot_budget`. Distinct readiness levels; both retained per PLANNER §3.1/§3.3.
- **`configure-static-routes` is a catalog addition.** IR §3.2 puts `static_routes` on routers with no op to apply them. It is a `config`-class op (Ansible into the guest, e.g. VyOS), emitted only for `kind: router` nodes that declare `static_routes`. Recorded as a design-Authority addition in the catalog CHANGELOG entry.

> **`clone-vm` carries informational passengers on `inputs_resolved` that are NOT enumerated in the
> signature below, and that is deliberate rather than an omission.** `clone-vm` is the one op emitted
> for **every** host regardless of apps, taps or overlays, which makes its recorded inputs the only
> complete per-host record in the state store — so the planner rides a small set of read-only facts on
> it that the published inventory (`axrange/planner/inventory.py` → `axrange te inventory`,
> `GET /api/deploys/{id}/inventory`) reads back: **`image_id`** (→ `os` family via the manifest),
> **`node_role`** (the clone-time baseline a `tfc` `assign-role` may later override),
> **`node_kind`**, and **`node_listen`** (the declared listen set, `App.listen`, 2026-09-05 —
> `docs/WORK_ORDER_protocol_faithful_services.md` S1). None of them changes what the adapter does:
> the adapter reads `template_vmid`/`clone_mode`/`target_node`/`node_id` and nothing else, so this is
> a **doc note, not a signature change** and the version-locked contract below is unmoved. Two rules
> a future passenger must follow: emit it **only when it has a value** (an unconditional `None` is a
> key absent from every standing deploy's persisted row, which `plandiff` classifies as **Mutated**
> and `te deploy --update` then hard-refuses), and read it back **defensively** (`.get(...)`,
> degrading to `None`), because a resumed or pre-existing deploy's `completed` `clone-vm` never
> re-runs and so never gains the key (LES-096).

**Parallelism points.** (1) `clone-vm` does **not** depend on `network-ready`; only `attach-vm-to-vnet` does. Cloning (the slowest infra op) overlaps SDN apply. (2) `configure-vm-hardware` and `configure-cloud-init` both follow clone and could be ordered either way; the catalog orders cloud-init after hardware (cloud-init drive is hardware) but they touch disjoint VM config and may be merged by the adapter.

```yaml
- id: OP-HOST-010
  name: clone-vm
  kind: action
  description: |
    Clone the bound template (linked or full per binding.clone_mode) into a new VM
    on the target node. Deterministic VM naming from {plan_id, op_id} per Model C.
  inputs:
    template_vmid: vmid:int             # from image binding (storage.template_vmid)
    clone_mode: clone-mode:str          # linked | full
    target_node: node-name:str          # from binding storage.node or planner placement
    node_id: node-id:str
  outputs:
    vmid: vmid:int
  after: []                             # NOT gated on network-ready; overlaps SDN apply
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - proxmox-node:$target_node
  implementation: proxmox.clone_template    # illustrative; adapter is Q5
  timeout: 600s                         # tune from binding.clone_estimate
  redo_on:
    - nodes[*].image
    - nodes[*].requires

- id: OP-HOST-020
  name: configure-vm-hardware
  kind: action
  description: |
    Set CPU/RAM/disk and machine/BIOS/agent flags on the cloned VM from IR resources
    and image-binding hardware. Resizes disk to nodes[*].resources.disk.
  inputs:
    vmid: vmid:int
    cpu: int
    ram: mem-size:str
    disk: disk-size:str
    machine: machine-type:str           # q35 | i440fx (from binding hardware)
    bios: bios-type:str                 # seabios | ovmf (from binding hardware)
    agent: bool                         # from binding hardware
  outputs:
    configured: bool
  after:
    - clone-vm[$host]
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - vm:$vmid
  implementation: proxmox.set_vm_config    # illustrative; adapter is Q5
  timeout: 120s
  redo_on:
    - nodes[*].resources

- id: OP-HOST-030
  name: configure-cloud-init
  kind: action
  description: |
    Render the per-host cloud-init / sysprep payload (user-data, network-config,
    or unattend.xml for sysprep_required Windows images) from IR + binding, and
    attach it to the VM as the cicustom drive. Render is folded in; the cicustom
    set is the observable side effect.
  inputs:
    vmid: vmid:int
    node_id: node-id:str
    ip_config: ip-config:obj            # derived from net/nets + cidr/gateway/dns
    sysprep_required: bool              # from binding cloud_init
    hostname: str
  outputs:
    cloud_init_attached: bool
  after:
    - configure-vm-hardware[$host]
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - vm:$vmid
  implementation: proxmox.set_cicustom     # illustrative; adapter is Q5
  timeout: 120s
  redo_on:
    - nodes[*].net
    - nodes[*].nets

- id: OP-HOST-040
  name: attach-vm-to-vnet
  kind: action
  description: |
    Attach one VM NIC to one scenario vnet. One instance per (host, network):
    multi-homed nodes emit several. This is the only OP-HOST op gated on the
    network being usable.
  inputs:
    vmid: vmid:int
    vnet_id: vnet-id:str
    net_id: net-id:str
    ip: ip:str                          # the host's IP on this net
    nic_index: int                      # 0 for single-homed; assigned per nets[] order
  outputs:
    nic_attached: bool
  after:
    - configure-cloud-init[$host]
    - network-ready[$net]               # the parallelism join: needs the vnet usable
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - vm:$vmid
    - ovs:$target_node
  implementation: proxmox.set_vm_net       # illustrative; adapter is Q5
  timeout: 60s
  redo_on:
    - nodes[*].net
    - nodes[*].nets

- id: OP-HOST-050
  name: configure-static-routes
  kind: action
  description: |
    Apply router static routes (IR static_routes) into the guest via Ansible.
    Emitted only for kind: router nodes that declare static_routes. config-class:
    Ansible task idempotency governs retries.
  inputs:
    host_id: node-id:str
    mgmt_ip: ip:str                     # reachable address (post wait-os-ready)
    static_routes: route-list:list      # [{to, via}, ...]
  outputs:
    routes_applied: bool
  after:
    - wait-os-ready[$host]              # guest must be booted and reachable
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:static_routes   # illustrative; adapter is Q5
  timeout: 300s
  redo_on:
    - nodes[*].static_routes

- id: OP-HOST-060
  name: start-vm
  kind: action
  description: |
    Power on the VM. The Proxmox post-start hook (installed by OP-MIR) fires here
    to install the per-tap tc mirror; that is an OP-MIR concern, but start-vm is
    its trigger.
  inputs:
    vmid: vmid:int
  outputs:
    started: bool
  after:
    - attach-vm-to-vnet[$host@*]        # all of this host's NICs attached
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - vm:$vmid
  implementation: proxmox.start_vm         # illustrative; adapter is Q5
  timeout: 120s
  redo_on: []                           # start is not redone by an IR field; redoes follow upstream

- id: OP-HOST-070
  name: wait-guest-agent
  kind: wait
  description: |
    Poll the QEMU guest agent until it responds — confirms the VM kernel is up and
    the agent channel is live. Read-only; no cleanup. Skipped for images whose
    binding sets hardware.agent: false (rare in v1).
  inputs:
    vmid: vmid:int
  outputs:
    agent_ready: bool                   # readiness signal
  after:
    - start-vm[$host]
  resource_categories:
    - vm:$vmid
  implementation: proxmox.agent_ping       # illustrative; adapter is Q5
  timeout: 180s                         # per-attempt; resets on retry per PLANNER §4.5

- id: OP-HOST-080
  name: wait-os-ready
  kind: wait
  description: |
    Poll SSH (Linux) or WinRM (Windows) reachability until the OS is ready to accept
    configuration, bounded by the binding's first_boot_budget. This is the readiness
    every app/overlay/activity op ultimately waits on via host-ready.
  inputs:
    vmid: vmid:int
    mgmt_ip: ip:str
    os_family: os-family:str            # selects SSH vs WinRM probe
    first_boot_budget: int              # seconds; from binding
  outputs:
    os_ready: bool                      # readiness signal
  after:
    - wait-guest-agent[$host]
  resource_categories:
    - vm:$vmid
  implementation: probe.ssh_or_winrm       # illustrative; adapter is Q5
  timeout: 600s                         # per-attempt ceiling; derived from first_boot_budget
```

**Barrier wiring.** `host-ready[<host-id>]` (§5.8) depends on `wait-os-ready[$host]` alone — a `te` deploy has no mirror concept of its own at all any more (`docs/WORK_ORDER_tap_eligibility.md`, 2026-07-28; `install-ovs-mirror` was already never a `te`-side predecessor of `host-ready` since the 2026-07-13 sensor-agnostic-te change — see §5.3). `configure-static-routes`, when present, is also a predecessor of `host-ready` (a router isn't "ready" until its routes are in). Downstream categories (apps, overlays, activities) depend only on `host-ready[<host>]`, never on the individual OP-HOST ops.

### 5.3 `OP-MIR` — Mirror plumbing

Traffic capture for mirrored interfaces (`docs/ARCHITECTURE.md` §264–311). The architecture makes the mirror itself **not** a deploy-time op: per §288 the `tc` mirror rules (Option 1, 2026-06-04 — formerly `ovs-vsctl create mirror`) run inside a Proxmox `post-start`/`pre-stop` hook script, rebuilt from the plan on every VM boot (stateless, self-healing across reboots/restores). So the deploy-time side effects are (1) installing that hook on the VM and (2) creating the cross-node gretap netdev that carries mirrored frames to the sensor. A further op, **`verify-capture` (OP-MIR-040, dynamic-tap slice 2026-06-19)**, then proves a frame actually reaches the sensor (the capture heartbeat).

> **The `te` planner emits NO OP-MIR op at all (2026-07-13 sensor-agnostic te,
> `docs/WORK_ORDER_sensor_agnostic_te.md`; 2026-07-28 full-topology tap eligibility, `docs/
> WORK_ORDER_tap_eligibility.md`, Authority: user — this order's own change).** `attach-gre-tunnel` /
> `install-ovs-mirror` / `verify-capture` **stay in the catalog with unchanged signatures**, but a `te`
> deploy's own DAG never contains any of them — not conditionally, not for a bridge a network sensor
> observes, not for anything. They are composed **entirely by hand** by the **A2 tap playout**
> (`axrange/o2control/taps.py`, `docs/design/06-taps-sensors.md`) as a *separate* `tap-<te>` deploy,
> driven by a standalone **tap plan** resolved against an already-deployed te's FULL published
> attachment topology (`hosts[].nets[]`, sourced unconditionally from `attach-vm-to-vnet` — every
> interface, no IR-side gate of any kind) and a standing O2 sensor. There is also no more intermediate
> bookkeeping op between the two: **`record-tap-intent` (OP-MIR-005), added 2026-07-13 to persist the
> te's then-resolved tapped-interface set for A2 to discover, is REMOVED** (2026-07-28) along with the
> `tap:` field it recorded — A2 now discovers what it may bind directly from the twin's own published
> topology, with nothing te-side left to persist a subset of it. A `te` deploy therefore installs **no
> mirror, no gretap, and no capture heartbeat of its own**; capture liveness is proven only after
> `axrange tap deploy` runs.

**Full-topology eligibility (2026-07-28, `docs/WORK_ORDER_tap_eligibility.md`).** There is no more
`tap:` field anywhere in the Scenario IR — `nodes[].tap` (whole host), `nodes[].nets[].tap` (one leg),
and `networks[].tap` (whole segment) are all REMOVED (`docs/IR_SCHEMA.md` §3.1/§3.2; the validator
rejects a leftover one loudly, `STR-200`). The twin substrate is entirely tap-agnostic: EVERY interface
a twin publishes (`hosts[].nets[]`, unconditional, sourced from `attach-vm-to-vnet`) is capture-eligible.
The tap plan alone decides which ones to actually mirror and to which sensor, via a
`port`/`segment`/`perimeter`/`boundary` scope+target selector (`axrange.planner.tappoints`) resolved
against that published topology at tap-apply time — never at `te` emission time, and never gated on an
author-declared flag. A **soft, non-blocking capacity-guardrail warning** (§7 of the work order) fires
at `tap plan`/`tap deploy` when a resolved plan's port count or mirrored-copy (fan-out) count crosses a
large threshold — full-topology eligibility makes "bind everything" trivial to author, and every
fan-out duplicates frames on the shared transport wire; not a hard cap.

**Decomposition notes:**

- **`install-ovs-mirror` installs the hook, it does not create the mirror.** Per §288 the mirror is (re)built on every `post-start`. The op writes the hook script and registers it on the VM (`infra`-class, deterministic hook path, check-then-act). PLANNER §9.2 already uses this op name. It takes a per-host **`tapped_nics`** list (the NIC indices to mirror, entirely composed by A2 from a tap plan's resolved bindings — never derived from any IR field), and mirrors each NIC into **its own** `(net, sensor)` gretap. **Each entry carries a `direction` (tap scopes, 2026-07-27):** `"rx"` installs only the ingress `mirred` filter on that NIC, `"both"` installs both ingress and egress — `rx` for multi-port scopes (`segment`, `boundary`) so a shared segment is captured exactly once, `both` for single-port scopes (`port`, `perimeter`). An **absent `direction` defaults to `"both"`**, which is what makes every pre-existing persisted op (and any resumed tap-plan apply that predates this key) behave identically to before — a resumed apply must never silently narrow to rx-only (LES-096 posture).
- **`attach-gre-tunnel` is per-(bridge, sensor).** A keyed **gretap netdev** is created (Option 1; formerly an OVS `gre-out` port) with `remote_ip` = the sensor's transport-vnet IP (`docs/ARCHITECTURE.md` §299–311). This is the op that occupies the `gretap:$sensor` semaphore (§2.3). Composed by A2 for exactly the `(bridge, sensor)` pairs its tap plan's resolved bindings need — one tunnel per distinct sensor a bridge's bound interfaces route to, shared across however many interfaces on that bridge route to it. `deploy-network-sensor`'s Option C network sensor (§5.5, `overlays.defense[].tap_from: bridge`) does **not** need its own `attach-gre-tunnel` — it reads a mirror of the STANDING capture appliance's own already-aggregated capture feed, not a fresh gretap of its own (§5.5's own decomposition notes). Under `tc` a tap with multiple consumers gets one `mirred` mirror action per target — per-tap mirror, N consumers.
- **v1 attribution is one gretap per zone** (`docs/ARCHITECTURE.md` §434, v1 decision recorded 2026-05-27). Attribution is implicit in which gretap received the frames, so v1 has **no** `register-mirror-source` op. The shared-gretap-with-metadata-keys variant is deferred; adopting it later adds a third op here (`OP-MIR-030`, reserved — see §9 follow-ups). Recorded in the catalog CHANGELOG entry.

```yaml
# OP-MIR-005 record-tap-intent REMOVED (`docs/WORK_ORDER_tap_eligibility.md`,
# Authority: user, 2026-07-28) -- it existed only to persist a te deploy's
# then-resolved tapped-interface set for A2 to discover; full-topology tap
# eligibility means A2 now discovers directly from the twin's own published
# `hosts[].nets[]` (sourced from `attach-vm-to-vnet`, unconditional), so there
# is nothing left for a te-side bookkeeping op to persist. Op count 40 -> 39.
# ID never reused.

- id: OP-MIR-010
  name: attach-gre-tunnel
  kind: action
  description: |
    Create a keyed **gretap netdev** (`ip link … type gretap`, Option 1 / 2026-06-04
    — formerly an OVS GRE port) that the per-tap `tc` mirror targets, tunneling
    mirrored frames to a sensor's transport-vnet IP (functional ERSPAN II per
    ARCHITECTURE §299). The sensor decapsulates via a flow-based gretap and sees the
    scenario frames (VLAN-tag preservation through a tap-level mirror is a live-verify
    item; it does not affect v1's merged, no-correlation capture). **Per-(bridge, sensor)
    (2026-06-03, per-tap sensor routing):** a bridge emits one tunnel per *distinct sensor*
    that its tapped hosts route to (taps all sharing one sensor → one tunnel; taps split
    across sensors → one each). The per-bridge GRE key generalizes to a per-(bridge, sensor)
    key so coexisting tunnels don't collide.
  inputs:
    bridge_name: ovs-bridge-name:str
    net_id: net-id:str
    sensor_id: sensor-id:str            # logical sensor (a standing O2 sensor, or the IR `sensors:` block — out of this order's scope; a tap plan's own `bindings:` names it, not an IR tap→sensor binding — that kind is REMOVED, `docs/WORK_ORDER_tap_eligibility.md` D6)
    sensor_transport_ip: ip:str         # resolved from platform/cluster config at deploy time
    target_node: node-name:str          # node whose OVS instance hosts the bridge
  outputs:
    gre_port: ovs-port-name:str         # gre_<bridge-hash> (per-bridge, per-sensor)
  after:
    - network-ready[$net]               # bridge must exist and be applied
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - ovs:$target_node
    - gretap:$sensor_id                 # serializes kernel netlink ops per target sensor (DB-backed lock cross-process, 2026-06-03)
  implementation: ovs.add_gre_port        # illustrative; adapter is Q5
  timeout: 60s
  redo_on: []                           # derived from tap→sensor topology + cluster sensor config, not a single IR field

- id: OP-MIR-020
  name: install-ovs-mirror
  kind: action
  description: |
    Install the Proxmox post-start/pre-stop hook script on a VM with >=1 mirrored
    NIC. The hook rebuilds the per-tap `tc` mirror (clsact + matchall mirred on
    each tap<vmid>i<n>, output to that NIC's (net,sensor) gretap netdev — Option 1,
    2026-06-04) on every boot and tears it down on stop. Composed entirely by the
    A2 tap playout from a tap plan's resolved bindings (2026-07-28, `docs/
    WORK_ORDER_tap_eligibility.md`) — never emitted by the `te` planner, and never
    derived from any IR field. Installs the hook; does not itself create the mirror.
    Each tapped_nics entry's direction (tap scopes, 2026-07-27) selects which
    mirred filter(s) the hook installs on that NIC: "rx" -> ingress only (exactly-
    once capture on a multi-port scope); "both" -> ingress + egress (unchanged
    prior behavior, single-port scopes). An entry with no gretap targets in a
    given direction omits that direction's filter add entirely rather than
    emitting an action-less one.
  inputs:
    vmid: vmid:int
    node_id: node-id:str
    bridge_name: ovs-bridge-name:str    # first tapped NIC's bridge (legacy single-NIC)
    gre_port: ovs-port-name:str         # first tapped NIC's gretap (legacy single-NIC)
    tapped_nics: nic-index-list:list    # per-NIC {nic_index, net_id, sensor_id, direction} to
                                        # mirror, each into its own (net,sensor) gretap (dynamic
                                        # taps); direction: "rx"|"both" is an ADDITIVE key (tap
                                        # scopes, 2026-07-27, docs/design/06-taps-sensors.md --
                                        # Authority: user, additive key within an existing input,
                                        # NO version bump). Absent direction defaults to "both",
                                        # preserving every pre-existing persisted op's behavior
                                        # (LES-096 posture: a resume must never silently narrow
                                        # to rx-only).
  outputs:
    hook_installed: bool
  after:
    - configure-cloud-init[$host]       # VM config exists; install before first start
    - attach-gre-tunnel[$net]           # mirror output target must exist
  cleanup_strategy: redo
  idempotency_class: infra
  resource_categories:
    - proxmox-cluster
    - vm:$vmid
  implementation: proxmox.install_vm_hook  # illustrative; adapter is Q5
  timeout: 60s
  redo_on: []                           # A2-composed only, driven by a tap plan's resolved
                                        # bindings; no IR path re-derives this any more

- id: OP-MIR-040
  name: verify-capture
  kind: action
  description: |
    Deploy-time capture heartbeat (dynamic taps, 2026-06-19,
    `docs/design/06-taps-sensors.md`). One per (bridge, sensor) with >=1 mirrored
    interface: inject a uniquely-keyed sentinel frame (ICMP echo whose id/payload
    encodes deploy_id+bridge) from a QGA-capable Linux guest on the bridge (the
    injector), then confirm it at the sensor by reading the sensor's NEWEST capture
    over the mgmt NIC (a short tcpdump/tshark sentinel filter) within a timeout.
    Fails the deploy (loudly) when no sentinel arrives — closing the
    silent-zero-capture failure mode (25/25 COMPLETED, zero frames; CHANGELOG
    2026-06-03). Injector selection: a Linux-QGA host on the bridge that the
    CURRENT tap-plan binding actually mirrors (deterministic lowest id), else any
    Linux-QGA host on the bridge (unbound by this binding is fine — it still emits
    frames a bound port sees), else SKIP with a loud warning (e.g. the bridge's
    only hosts are VyOS router legs with no QGA). NO change to the sensor's
    black-box contract (it reads existing capture output, adds no sensor interface).
    EMISSION CONTEXT (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md; 2026-07-28,
    docs/WORK_ORDER_tap_eligibility.md): the `te` planner no longer emits this op (a
    `te` deploy stands up only its own components) -- it is emitted exclusively by
    the A2 tap playout (axrange.o2control.taps._compose_ops) against an
    already-deployed te's tap-plan-resolved bindings (discovered from the twin's
    published attach-vm-to-vnet topology, not a te-side tap op) and a standing O2
    sensor.
  inputs:
    vmid: vmid:int                      # the injector's vmid (from clone-vm)
    injector_host_id: node-id:str
    net_id: net-id:str
    sensor_id: sensor-id:str
    bridge_name: ovs-bridge-name:str
    gre_port: ovs-port-name:str
  outputs:
    capture_verified: bool
  after:
    - attach-gre-tunnel[$net@$sensor]   # this bridge,sensor tunnel must exist
    - install-ovs-mirror[*]             # every tap on the bridge live before inject
    - host-ready[$host]                 # the injector must be OS-ready
  cleanup_strategy: redo
  idempotency_class: config             # in-guest inject over QGA + sensor read over mgmt-SSH;
                                        # holds only vm:$vmid, NOT the ansible-pool slot
  resource_categories:
    - vm:$vmid
  implementation: config_exec.verify_capture  # illustrative; ConfigBackend + adapter SSH
  timeout: 120s
  redo_on: []                           # derived from tap topology, not a single IR field
```

**Conditional predecessor edges — RETIRED for the `te` deploy DAG (2026-07-13, sensor-agnostic te).**
This described the earlier in-deploy model: for a tapped host, `start-vm[$host]` (§5.2) gained two
predecessors in Pass 3, `install-ovs-mirror[$host]` and `attach-gre-tunnel[$net]`. That conditional-edge
mechanism no longer exists in `pass1_emit.py` — a `te` deploy never emits those two ops (nor, as of
2026-07-28, the `record-tap-intent` bookkeeping op that briefly stood in for them — REMOVED,
`docs/WORK_ORDER_tap_eligibility.md`), so there is nothing for `start-vm` to gate on and nothing left in
the `te` DAG that orders relative to it at all. **In the A2 tap playout's own composed plan** (a
*separate* `tap-<te>` deploy, `docs/design/06-taps-sensors.md`), there is no `start-vm` op at all — the
target VM is already running — so `install-ovs-mirror`'s adapter call (re)applies the hook to the
**already-running** VM directly rather than waiting for a future first boot; the "hook must exist before
first boot" constraint this paragraph used to describe is moot once tap wiring is decoupled from VM
lifecycle.

**Barrier wiring — RETIRED for the `te` deploy DAG, same change.** `host-ready[<host-id>]` no longer
includes `install-ovs-mirror[$host]` as a predecessor (a `te` host reaches `host-ready` without any
mirror concept), and the `te` deploy emits **no `mirror-ready[<sensor>]` barrier of its own** — there
is nothing left to gate on it (§5.6/§5.8's `mirror-ready` consumers, e.g. `start-activity`, simply see
zero predecessor ops and the gate resolves to a no-op for a `te`-only deploy). `attach-gre-tunnel` and
`verify-capture` still feed a `mirror-ready[<sensor>]` barrier, but only **within A2's own composed
playout plan** — the "sentinel provably traversed tap → gretap → sensor" upgrade this barrier expresses
is now proven by `axrange tap deploy`, not by `axrange te deploy`.

### 5.4 `OP-APP` — App install dispatch

Materializes `apps[]` onto their hosts via the closed six-kind install-action set (IR §4.1). All execution is first-boot, pull-mode Ansible into the guest (IR §4.235 hard rule); none of it is a runtime channel.

**Decomposition notes:**

- **One `provision-app` op per app, not one op per install action.** PLANNER §9.2 names `provision-app[<app>]` (per app), and IR §4.235 describes install actions as a single first-boot cloud-init + pull-mode Ansible convergence per host. The pkg/version/config/role actions are dispatched **inside** the op by an Ansible role that branches on action kind; Ansible's task-level idempotency handles intra-app action ordering. This **deviates from PLANNER §2.1's "one op per externally-observable side effect"** — `provision-app` deliberately bundles several side effects into one convergence unit, because the execution model (one Ansible run per host) and the natural retry unit ("re-converge this app") both argue for it. Recorded as a design-Authority deviation in the catalog CHANGELOG entry.
- **`pkg` and `pkg@version` are one op path, not two.** They share the `{pkg: ...}` form; `version` is an optional parameter, not a distinct side effect. Both are handled inside `provision-app`. The pinned/unpinned distinction matters only to the validator's `reproducible:` rule, not to the op.
- **`container` and `script` are split out.** `run-container` has a different idempotency story (is the container already running?) and `run-script` is explicitly last-resort opaque (IR §4.233) with **no** idempotency guarantee — folding either into the Ansible-idempotent `provision-app` would misrepresent its retry semantics. Three ops total. The split is exactly what the `idempotency_class` field exists to surface.

```yaml
- id: OP-APP-010
  name: provision-app
  kind: action
  description: |
    Converge one app's pkg / pkg@version / config / role install actions onto its
    host via pull-mode Ansible at first boot. An Ansible role dispatches on action
    kind and preserves the IR-declared action order. config-class: Ansible task
    idempotency governs retries; re-running re-converges.
  inputs:
    app_id: app-id:str
    host_id: node-id:str
    mgmt_ip: ip:str
    install_actions: install-action-list:list   # ordered; pkg/pkg@version/config/role only
    env: env-map:obj                            # optional; cross-app values resolved by planner
  outputs:
    provisioned: bool
  after:
    - host-ready[$host]
    - provision-app[$dep]               # one per app referenced via env: (data-flow edge)
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:provision_app    # illustrative; adapter is Q5
  timeout: 900s
  redo_on:
    - apps[*].install
    - apps[*].env

- id: OP-APP-020
  name: run-container
  kind: action
  description: |
    Start an OCI container (podman/docker) on the host for a {container: <oci-ref>}
    install action. Requires the image binding to declare install_support.container.
    Idempotency: deterministic container name; check-then-act (already running?).
  inputs:
    app_id: app-id:str
    host_id: node-id:str
    mgmt_ip: ip:str
    oci_ref: oci-ref:str
    env: env-map:obj                    # optional
  outputs:
    container_running: bool
  after:
    - host-ready[$host]
    - provision-app[$app]              # if the same app also has pkg/config actions, they land first
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:run_container    # illustrative; adapter is Q5
  timeout: 600s
  redo_on:
    - apps[*].install

- id: OP-APP-030
  name: run-script
  kind: action
  description: |
    Execute a {script: <path>} install action once at first boot. Last-resort,
    opaque shell blob (IR §4.233) with NO idempotency guarantee. cleanup_strategy
    escalate, not redo: a partially-applied opaque script cannot be safely
    auto-cleaned or re-run, so a failure escalates to the operator rather than
    silently retrying (see PLANNER §4.4).
  inputs:
    app_id: app-id:str
    host_id: node-id:str
    mgmt_ip: ip:str
    script_path: path:str
  outputs:
    script_ran: bool
  after:
    - host-ready[$host]
    - provision-app[$app]
    - run-container[$app]              # scripts run after declarative actions of the same app
  cleanup_strategy: escalate           # opaque + non-idempotent ⇒ do not auto-redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:run_script       # illustrative; adapter is Q5
  timeout: 600s
  redo_on:
    - apps[*].install
```

**On `run-script`'s `cleanup_strategy: escalate`.** This is the first op in the catalog to use `escalate` rather than `redo`. It is the correct strategy precisely because `run-script` carries `idempotency_class: config` *nominally* (it runs through the Ansible channel) but has no real idempotency — Ansible can re-invoke the script, but the script's own effects are unknown. Rather than let the planner auto-redo an unknown blob, a failure stops and asks the operator. This is the catalog honoring PLANNER §4.4's escalate path for ops that can't guarantee safe replay.

**Intra-app ordering.** When one app declares several install kinds, the order is: `provision-app` (declarative pkg/config/role) → `run-container` → `run-script`. Declarative convergence first, containers next, opaque scripts last. These edges are added in Pass 3 keyed on shared `app_id`.

**Barrier wiring.** Apps do not gate a dedicated barrier in v1; downstream activities depend on `host-ready[$host]` and, where an activity needs a specific service, on the relevant `provision-app[$app]` via an explicit binding (resolved by the planner from the activity's actor/target references, §5.6).

### 5.5 `OP-OVL` — Overlay materialization

The three optional overlay dimensions (IR §8). Each is materialized only when the corresponding `overlays.*` section is present. Identity is the richest: it has an ordering chain (domain must serve before users are created or hosts join) and an inferred op (`join-host-to-domain`, not declared in the IR).

**Identity (IR §8.1) decomposition notes:**

- **Domain creation is not an OVL op.** An AD domain is created by the domain controller's `provision-app` with `role: adds` (an `OP-APP` op). OVL identity ops depend on the domain *serving*, gated by `domain-ready[<fqdn>]` (§5.8), which in turn depends on a `wait-domain-ready` op.
- **`wait-domain-ready` is a wait op** — it polls the DC until AD/LDAP/Kerberos answers (someone else's side effect, inside the DC guest, per the §2.1 rule). It is the predecessor of `domain-ready[<fqdn>]`.
- **`join-host-to-domain` is planner-inferred (decision I1=infer).** The IR has no explicit "host joins domain" field. The planner infers a join: **a host joins domain D when any activity places a D-domain identity as actor on that host.** This is the catalog's single largest "the IR doesn't say it" inference; it is necessary because a domain user that can't authenticate on the host it acts from is a silently broken scenario. The inference resolves through activity.actor → identity.domain → the activity's host (§5.6 resolution). Recorded as a design-Authority decision in the CHANGELOG. **— SUPERSEDED 2026-06-16 (Authority: user; CHANGELOG):** for the Windows-AD path, host→domain join becomes an **explicit IR field** (`node.joins: <fqdn>`), not this inference — deterministic and with no activity-layer coupling. The `join-host-to-domain` op signature is unchanged; only its trigger moves from inferred to declared. `I1=infer` is retired for join; the §5.6 actor→host resolution still feeds `deploy-behavior-agent`'s identity predecessors. **IMPLEMENTED 2026-06-17** via the explicit `node.joins:` field (emission + render/backend; unit/plan-verified — docs/SLICE_WINDOWS_AD.md §6.3, CHANGELOG).
- **`external: true` identities are op-less (decision I2).** An identity that exists outside the scenario's identity system (e.g. an attacker) has nothing to create inside the range. The activity layer references it as an actor; the activity's behavior agent supplies the credentials/tooling. No OVL op is emitted for it.

**Data (IR §8.2) decomposition notes:**

- **One op, `seed-dataset` (decision D1).** IR §8.2 has only `path` / `classification` / `synthetic_size`; there is no secrets field, so there is no separate secret-injection op. `classification` and `crown_jewel` are scoring metadata, not separate materializations.

**Defense (IR §8.3) decomposition notes:**

- **Two modes, two ops.** `install-defense-agent` for each `installed_on:` host (host-agent tools like EDR); `deploy-network-sensor` for each entry with `observes:` (network tools like Suricata/Zeek). An entry with both modes emits both.
- **`deploy-network-sensor` with `tap_from: bridge` reuses the OVS mirror infra (decision DEF1).** It does not stand up parallel mirror machinery; it adds the sensor as an **additional output/consumer of the bridge's existing mirror** (the same mirror `install-ovs-mirror` builds for the primary tcpdump-sensor). It therefore depends on `attach-gre-tunnel[$observed_net]`, and its presence is one of the two triggers that cause that op to be emitted (§5.3 widened emission rule). `tap_from: per-vm` instead consumes the per-VM mirrors, depending on the relevant `install-ovs-mirror[$observed_host]` ops. `tap_from: external-mirror` is operator-wired and emits no mirror dependency.

```yaml
# --- Identity ---
- id: OP-OVL-010
  name: wait-domain-ready
  kind: wait
  description: |
    Poll a domain controller until its AD directory services answer (LDAP bind +
    Kerberos). Gates all domain-scoped identity ops. Read-only; no cleanup.
  inputs:
    domain_fqdn: domain-fqdn:str
    dc_host_id: node-id:str
    dc_mgmt_ip: ip:str
  outputs:
    domain_serving: bool                # readiness signal
  after:
    - provision-app[$dc_app]            # the role: adds provisioning on the DC
  resource_categories:
    - vm:$dc_vmid
  implementation: probe.ldap_kerberos     # illustrative; adapter is Q5
  timeout: 600s                         # per-attempt; AD first promotion is slow

- id: OP-OVL-020
  name: create-domain-user
  kind: action
  description: |
    Create one identity (type: user) in its AD domain, with groups and privilege
    tags. For identities with no domain:, creates a local user on the relevant host
    instead. Not emitted for external: true identities.
  inputs:
    identity_id: identity-id:str
    domain_fqdn: domain-fqdn:str         # absent ⇒ local user
    target_host_id: node-id:str          # the DC for domain users; the host for local users
    groups: str-list:list                # optional
    privileges: str-list:list            # optional
  outputs:
    user_created: bool
  after:
    - domain-ready[$domain]             # for domain users; local users use host-ready[$host]
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$target_vmid
  implementation: ansible.play:create_user   # illustrative; adapter is Q5
  timeout: 300s
  redo_on:
    - overlays.identity[*].domain
    - overlays.identity[*].groups
    - overlays.identity[*].privileges

- id: OP-OVL-030
  name: join-host-to-domain
  kind: action
  description: |
    Join a host to an AD domain so domain identities can authenticate on it.
    Planner-INFERRED (not an IR field): emitted when an activity places a domain
    user as actor on this host. One op per (host, domain) pair.
  inputs:
    host_id: node-id:str
    domain_fqdn: domain-fqdn:str
    mgmt_ip: ip:str
  outputs:
    host_joined: bool
  after:
    - domain-ready[$domain]
    - host-ready[$host]
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:domain_join   # illustrative; adapter is Q5
  timeout: 300s
  redo_on: []                           # inferred from activity placement; redoes when that topology changes

# --- Data ---
- id: OP-OVL-040
  name: seed-dataset
  kind: action
  description: |
    Materialize synthetic data of synthetic_size at path on the host, tagged with
    classification. One per overlays.data entry. Deterministic content from the
    scenario seed when reproducible.
  inputs:
    data_id: data-id:str
    host_id: node-id:str
    path: path:str
    classification: str
    synthetic_size: size:str             # optional; k8s-style
    seed: int                            # from scenario seed; deterministic generation
  outputs:
    dataset_seeded: bool
  after:
    - host-ready[$host]
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:seed_dataset   # illustrative; adapter is Q5
  timeout: 600s                         # large synthetic_size can be slow
  redo_on:
    - overlays.data[*].path
    - overlays.data[*].synthetic_size
    - overlays.data[*].classification

# --- Defense ---
- id: OP-OVL-050
  name: install-defense-agent
  kind: action
  description: |
    Install a host-agent defense tool (EDR etc.) on one installed_on: host. One op
    per (defense-entry, host) pair. Capability requirements (e.g. kernel:edr-hooks)
    are validator-checked before planning.
  inputs:
    defense_id: defense-id:str
    tool: str
    host_id: node-id:str
    config: path:str                     # optional
    ruleset: str                         # optional
  outputs:
    agent_installed: bool
  after:
    - host-ready[$host]
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:install_defense_agent   # illustrative; adapter is Q5
  timeout: 600s
  redo_on:
    - overlays.defense[*].installed_on
    - overlays.defense[*].config
    - overlays.defense[*].ruleset

- id: OP-OVL-060
  name: deploy-network-sensor
  kind: action
  description: |
    SUPERSEDED (sensors design, docs/design/06-taps-sensors.md) by the in-scenario
    `suricata-sensor` type — a network IDS is now a sensor declared in `sensors:`
    with `captures:` (deployed as a VM, OP-MIR-050 deploy-sensor), not an
    overlays.defense `observes:` network sensor. Kept for back-compat with existing
    defense overlays (e.g. soc-detection-lab); not extended.

    Bring up a network-mode defense tool (Suricata/Zeek) on its host, observing
    one or more nets. For tap_from: bridge, adds the sensor as an additional output
    of the observed net's existing OVS mirror (reuses the primary tcpdump-sensor's
    mirror infra, decision DEF1); for per-vm, consumes the per-VM mirrors; for
    external-mirror, assumes operator-wired delivery.
  inputs:
    defense_id: defense-id:str
    tool: str
    sensor_host_id: node-id:str          # the host: field
    observes: observe-list:list          # [{net, mode}, ...]
    tap_from: tap-from:str               # bridge | per-vm | external-mirror
    ruleset: str                         # optional
    config: path:str                     # optional
  outputs:
    sensor_live: bool
  after:
    - host-ready[$sensor_host]
    - network-ready[$observed_net]      # one per observed net
    - attach-gre-tunnel[$observed_net]  # only when tap_from: bridge
    - install-ovs-mirror[$observed_host] # only when tap_from: per-vm (per observed host)
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$sensor_vmid
    - ovs:$sensor_node                  # when adding a mirror output (bridge mode)
  implementation: ansible.play:deploy_network_sensor   # illustrative; adapter is Q5
  timeout: 600s
  redo_on:
    - overlays.defense[*].observes
    - overlays.defense[*].tap_from
    - overlays.defense[*].ruleset

# --- Adversary / default agents (A3 Stage A, 2026-07-11) ---
- id: OP-OVL-070
  name: install-agent
  kind: action
  description: |
    Install a non-defense agent (Prometheus node-exporter, or the Sandcat/Caldera
    adversary agent) on one host, config-class over QGA. Mirrors
    install-defense-agent's render/backend pattern for the exporter/adversary agent
    kinds so install-defense-agent itself stays defense-only. `agent_kind` selects
    the render branch; `adversary` additionally requires `server` (the Caldera
    server the agent beacons to — an O2 Stage-B server, not yet stood up, so the
    plan carries it as an explicit input, not an adapter-resolved default).
    Emitted either from a declared `overlays.adversary[].targets:` entry
    (agent_kind=adversary) or from the default-agent-set sweep when
    `scenario.default_agents` is not `false` (agent_kind=exporter; per-node
    `default_agents_off` opts a host out). One op per (agent, host) pair.
  inputs:
    agent_id: agent-id:str
    agent_kind: str                      # "exporter" | "adversary"
    host_id: node-id:str
    agent_entry: obj
    server: str                          # adversary only
  outputs:
    agent_installed: bool
  after:
    - host-ready[$host]
    - provision-app[$host@*]             # star-tail; same class as install-defense-agent
    - run-script[$host@*]
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: config_exec.render:install_agent   # QGA-mediated, like install-defense-agent
  timeout: 600s
  redo_on:
    - scenario.default_agents
    - nodes[*].default_agents_off
    - overlays.adversary[*].targets
    - overlays.adversary[*].server
    - overlays.adversary[*].group
```

**Conditional `after:` edges.** `create-domain-user` uses `domain-ready[$domain]` for domain users and `host-ready[$host]` for local (no-`domain:`) users — the planner selects per entry. `deploy-network-sensor`'s mirror-related predecessors (`attach-gre-tunnel` / `install-ovs-mirror`) are present only for the matching `tap_from` mode; `external-mirror` has neither.

**Barrier wiring.** `domain-ready[<domain-fqdn>]` (§5.8) depends on `wait-domain-ready[$domain]`. `overlay-ready[<kind>:<id>]` (§5.8) is the per-overlay completion barrier activities depend on — e.g. an activity needing seeded data depends on `overlay-ready[data:pii-records]`. Identity-dependent activities depend on `create-domain-user[$actor]` and `join-host-to-domain[$host@$domain]` through the activity-resolution edges built in §5.6.

> **Implemented (2026-06-16, W2a — `role: adds` DC promotion + `wait-domain-ready`, docs/SLICE_WINDOWS_AD.md §6.1).** Domain stand-up is live in `axrange/config_exec` (`render_windows_adds_promotion` + the reboot-tolerant `_promote_dc_windows`) and `pass1_emit` (`role: adds` lifted through `provision-app`; `wait-domain-ready` + `domain-ready` emitted from the DC app's `provides:[{domain}]`). **`wait-domain-ready` is config-class** — the executor routes its in-guest `Get-ADDomain` poll to the ConfigBackend over QGA (like `wait-actor-ready`); it holds only `vm:$dc_vmid`, never `ansible-pool`. Two **star-tail corrections** landed with it (same class as the OP-ACT ones — the single-scope forms never resolve against multi-scope ops): `wait-domain-ready`'s `after` is `provision-app[$dc_app@*]`, and `domain-ready`'s predecessor is `wait-domain-ready[$domain@*]`. `create-domain-user` (domain branch) + the identity overlay are W2b (live-verified). **`join-host-to-domain` is live-verified (2026-06-24, dev45). The `create-domain-user` LOCAL branch is implemented + live-verified (2026-06-24): a no-`domain_fqdn` entry (explicit `host:` on a no-`domain:` identity) renders `New-LocalUser` on Windows / `useradd` on Linux on `target_host_id` after `host-ready[$host]` — `after = (domain-ready[$domain], host-ready[$host])`, the planner selecting per scope-presence.** See docs/SLICE_WINDOWS_AD.md §6.6.

### 5.6 `OP-ACT` — Activity actor

An activity (IR §5) becomes a running behavior agent on its bound host (Activity→host binding, IR §6), acting on its targets (Activity→targets binding). The agent generates the synthetic traffic and host activity the scenario exists to produce.

**Decomposition notes:**

- **Actor resolution is not an op (decision: planner-internal).** The `actor:` → identity resolution (IR §5.4: overlay-identity entry vs. implicit local user) is a planning-time computation with no external side effect. It is done in Pass 1/2 while building the DAG, and it is what feeds the §5.5 `join-host-to-domain` inference (a domain-resolved actor on a host triggers that host's join). No `resolve-actor` op exists.
- **Two ops: `deploy-behavior-agent` then `start-activity`** (adds one op beyond PLANNER §9.2's single `start-activity`, recorded in the CHANGELOG). They have different retry and timing semantics: deploying the runner + resolved behavior definition is heavy and idempotent (re-runnable Ansible), while starting is the moment traffic generation begins and is the op gated on capture being live. Splitting lets deploy happen early while start waits on the full capture path.
- **Scheduling is delegated to the agent (decision).** `start-activity` starts the agent *process*, which then honors its own `schedule:` window at runtime. It does **not** block the deploy DAG until a schedule's `start:` wall-clock time — a one-shot `schedule.start: 10:30` activity must not hold the DAG open for hours. The DAG completes by deploying and starting the agent with its schedule registered.
- **`wait-actor-ready` confirms the agent is healthy** — polls the agent's health/registration endpoint (someone else's side effect inside the guest, per §2.1). Without it, `start-activity` returning does not guarantee the agent is actually running and ready to generate load.
- **`start-activity` depends on `mirror-ready` (decision).** When the scenario has any capture, `start-activity` waits on `mirror-ready[$sensor]` so the capture path is live before any traffic is generated. This makes capture completeness a structural property of the DAG rather than a timing accident.

```yaml
- id: OP-ACT-010
  name: deploy-behavior-agent
  kind: action
  description: |
    Install the behavior-runner and the resolved behavior definition (library ref
    resolved + overrides applied, or inline steps) onto the activity's bound host.
    Idempotent Ansible. Registers the schedule but does not start generation.
  inputs:
    activity_id: activity-id:str
    host_id: node-id:str                 # Activity→host binding
    mgmt_ip: ip:str
    actor_resolved: resolved-actor:obj   # planner-resolved identity (domain user | local user)
    behavior_def: behavior-def:obj       # resolved library behavior+overrides, or inline steps
    targets: node-id-list:list           # Activity→targets binding; optional
    intensity: float                     # optional
    schedule: schedule:obj               # optional; registered with the agent
  outputs:
    agent_deployed: bool
  after:
    - host-ready[$host]
    - create-domain-user[$actor]        # if actor resolves to a domain identity
    - join-host-to-domain[$host@$domain] # if actor is a domain user on this host
    - provision-app[$host@*]            # all apps on the activity's host (tools the steps use)
    - run-script[$host@*]               # ...and their first-boot scripts
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:deploy_behavior_agent   # illustrative; adapter is Q5
  timeout: 600s
  redo_on:
    - activity[*].behavior
    - activity[*].overrides
    - activity[*].inline
    - activity[*].actor

- id: OP-ACT-020
  name: start-activity
  kind: action
  description: |
    Start the deployed behavior agent's generation process. The agent then honors
    its registered schedule at runtime. Gated on the capture path being live so no
    intended traffic is generated before mirroring is ready.
  inputs:
    activity_id: activity-id:str
    host_id: node-id:str
    mgmt_ip: ip:str
  outputs:
    activity_started: bool
  after:
    - deploy-behavior-agent[$act@*]     # `@*`: deploy is multi-scope ({act,host})
    - mirror-ready[$sensor]             # when the scenario has any capture
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  implementation: ansible.play:start_activity   # illustrative; adapter is Q5
  timeout: 120s
  redo_on: []                           # restart follows upstream redo (agent redeploy)

- id: OP-ACT-030
  name: wait-actor-ready
  kind: wait
  description: |
    Poll the behavior agent until it reports healthy and registered. Confirms the
    activity is live before the scenario is declared ready. Read-only; no cleanup.
  inputs:
    activity_id: activity-id:str
    host_id: node-id:str
    mgmt_ip: ip:str
  outputs:
    actor_ready: bool                   # readiness signal
  after:
    - start-activity[$act@*]            # `@*`: start is multi-scope ({act,host,sensor})
  idempotency_class: config             # routes to ConfigBackend (polls the marker over QGA)
  resource_categories:
    - vm:$vmid                          # NOT ansible-pool: a poll must not hold the throughput slot
  implementation: probe.agent_health       # illustrative; adapter is Q5
  timeout: 180s                         # per-attempt; resets on retry
```

> **Implemented (2026-06-12, inline-steps MVP — docs/SLICE_ACTIVITY.md).** The
> three ops above are live in `axrange/config_exec` (render + backend) and
> `axrange/planner/pass1_emit.py` (`_emit_activity`). MVP scope: `inline.steps`
> only (rendered to a shell runner over the QGA channel), local/external actors,
> one-shot (no `schedule:`). Library `behavior:`/`overrides:` and domain actors
> stay a clear deferred error. Two catalog corrections landed with it: the
> cross-op `after:` patterns use the **star-tail** form (`deploy-behavior-agent[$act@*]`,
> `provision-app[$host@*]`) because the pass-3 resolver matches on the *full*
> scope-value set — the single-scope `[$act]`/`[$target_app]` forms never resolved
> against multi-scope ops (latent: these ops were never emitted before); and
> `wait-actor-ready` is **config-class** so the executor routes its marker-poll to
> the ConfigBackend (it holds only `vm:$vmid`, not `ansible-pool`).

**Conditional `after:` edges.** `deploy-behavior-agent`'s identity predecessors (`create-domain-user`, `join-host-to-domain`) are present only when the actor resolves to a domain identity; for an implicit local user, neither is present (the local user is created as part of agent deploy or pre-exists on the image). `provision-app[$host@*]` / `run-script[$host@*]` gate the activity on its host's app installs (so a recon step never runs before its tools exist); they drop to nothing on a host with no apps. `start-activity`'s `mirror-ready` predecessor is present only when the scenario has capture.

**Barrier wiring.** `wait-actor-ready[$act]` feeds the scenario-level readiness the run controller uses to declare the whole environment live; there is no per-activity barrier in v1 (activities are leaves of the DAG — nothing depends on an activity except the global "scenario ready" assertion).

### 5.7 `OP-TFC` — Traffic-tier persona driving

**Added 2026-07-24 for the Traffic tier (`docs/WORK_ORDER_traffic_tier.md`, `docs/design/02-twin-model.md`); extended 2026-08-04 with `OP-TFC-020 assign-role` for the role/persona decoupling (`docs/WORK_ORDER_role_persona_tier.md`, Authority: user).** Two ops, **neither emitted by the `te` planner** — like OP-MIR-010/020/040, both are composed **by hand** by a standalone playout (`axrange.o2control.traffic._compose_ops`) against an **already-deployed** `te`-tier twin, not built from an IR walk. The `tfc` playout owns BOTH halves of what a host is and does: `assign-role` writes the host→role map the twin's published inventory then prefers over its `clone-vm` baseline (§OP-HOST-010's `node_role` input → `hosts[].role`), and `drive-persona` binds the profile's persona→host-role assignments onto concrete hosts and drives each assignment via the persona drivers (`axrange.personas.drivers`, `docs/design/02-twin-model.md`).

```yaml
- id: OP-TFC-010
  name: drive-persona
  kind: action
  description: |
    (Re)start one persona instance on an already-deployed twin host via its
    driver (scripted/replay/real-service/command, axrange.personas.drivers). Stable
    per-instance scope (no content hash); the Traffic-tier playout force-
    resets this op to pending on every apply so a changed profile is
    re-asserted, not silently skipped (mirrors install-ovs-mirror's own
    re-assert posture).
  inputs:
    persona_id: persona-id:str
    drive: drive-kind:str
    instance_id: instance-id:str
    host_id: node-id:str
    vmid: vmid:int
    peers: peer-map:obj
    seed: seed:any
  outputs:
    started: bool
  after: []
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories:
    - ansible-pool
    - vm:$vmid
  timeout: 120s
  redo_on: []                           # force-reset by the tfc playout on every apply, not IR-field-driven
```

**Decomposition notes:**

- **Scope is `{instance: instance_id}`, `instance_id = f"{host_id}-{persona_id}"`.** A persona swap on the same host is a different op_id (a different systemd unit name); the tfc playout's own drop-diff pass (not this catalog) stops the instance the new plan no longer contains — see the module docstring in `axrange/o2control/traffic.py`.
- **Adapter-side execution is NOT `axrange.config_exec.ConfigBackend`.** `axrange.personas.drivers` already imports `config_exec.render`'s `systemd_daemon_start_block` primitive, so `config_exec` importing `axrange.personas` back would be a real import cycle. The `tfc` playout supplies its own small `Adapter`-protocol backend (`PersonaBackend`, `axrange/o2control/traffic.py`) instead, reached the same way every other config-class op reaches its backend (`idempotency_class: config` routes the executor to `config_backend`).
- **No barrier feeds from or into this op.** `drive-persona` has no `after:` (it targets an already-live host, not one still coming up) and produces no `overlay-ready`/`mirror-ready`-class barrier of its own — the Traffic tier is a sibling playout to the twin, not a stage within it.

```yaml
- id: OP-TFC-020
  name: assign-role
  kind: action
  description: |
    Record the tfc-owned SEMANTIC ROLE of one already-deployed twin host.
    Pure metadata: no adapter call, nothing on the wire, no guest exec. The
    twin's published inventory prefers this over clone-vm's recorded
    node_role baseline, which is what makes a role re-assignable without
    re-cloning the node. Force-reset to pending by the tfc playout on every
    apply, same posture as drive-persona.
  inputs:
    host_id: node-id:str
    role: role:str
  outputs:
    assigned: bool
  after: []
  cleanup_strategy: redo
  idempotency_class: config
  resource_categories: []               # touches no cluster resource at all
  timeout: 10s
  redo_on: []                           # force-reset by the tfc playout on every apply
```

**Decomposition notes:**

- **Scope is `{host: host_id}`** — one assignment per host, so a re-applied
  profile that changes a host's role reuses the same `op_id` and re-asserts it,
  while a host dropped from the `roles:` block falls out of the plan and is
  retired by the playout's de-scope pass (the same drop-diff shape
  `drive-persona` uses, LES-112/LES-113).
- **It is its own op rather than an input on `drive-persona`** because a host
  may carry a role while running **no** persona: a persona's `emits[].peer_role`
  target (the DC, the file server) needs to be *findable* by role without
  necessarily emitting anything itself.
- **Empty `resource_categories` — the only op in the catalog with none.** It
  acquires no throughput slot and no resource lock because it touches nothing:
  giving it the `ansible-pool` slot to match the other `config`-class actions
  would burn a real slot on a no-op. `config` here is a *routing* class (it
  reaches the `tfc` playout's own `PersonaBackend` via
  `idempotency_class: config`), not a claim about delivery.
- **Why not rewrite `clone-vm`'s persisted `node_role` input instead?** That
  would have one tier mutating another tier's op rows (`tfc` writing into
  `te`'s state), breaking the ownership boundary every tier's teardown and
  protect-guard depends on. The assignment gets its own `tfc`-owned row, which
  a `tfc destroy` retires for free.

### 5.8 `OP-BAR` — Synthetic barriers

Barriers are milestones with no execution (§2.1, PLANNER §2.2/§3.3). Each gathers a **predecessor set** of ops; downstream ops depend on the barrier, not on the ops behind it, so op composition can change without rewiring consumers. All predecessor sets below are now complete — every op that can feed a barrier has been enumerated in §5.1–5.6 (OP-TFC, §5.7, feeds no barrier — see above).

A barrier instance exists only when its predecessor set is non-empty (e.g. `domain-ready[corp.local]` exists only if the scenario has a `corp.local` domain). Barriers carry no `inputs`/`outputs`/`cleanup_strategy`/`idempotency_class`/`resource_categories`/`implementation`/`timeout` — only `id`, `name`, `description`, and the predecessor set.

```yaml
- id: OP-BAR-010
  name: network-ready
  scope: net-id                          # one per infra.networks[] entry
  description: |
    The network's vnet/bridge exist and the SDN config is applied — the network is
    usable for NIC attachment and sensor observation.
  predecessors:
    - apply-sdn-config[$scenario]        # scenario-wide apply; per-net barrier for downstream clarity
  consumed_by:
    - attach-vm-to-vnet                  # §5.2
    - attach-gre-tunnel                  # §5.3
    - deploy-network-sensor              # §5.5

- id: OP-BAR-020
  name: host-ready
  scope: host-id                         # one per infra.nodes[] entry
  description: |
    The host is cloned, configured, attached, started, and the OS is reachable;
    for routers, static routes are applied. No mirror concept at all any more
    (`docs/WORK_ORDER_tap_eligibility.md`, 2026-07-28 — a `te` deploy emits no
    OP-MIR op, so there is nothing tap-related left to gate on here). The host
    can now receive apps, overlays, and activity agents.
  predecessors:
    - wait-os-ready[$host]               # §5.2 — always
    - configure-static-routes[$host]     # §5.2 — only for kind: router with static_routes
  consumed_by:
    - provision-app, run-container, run-script         # §5.4
    - create-domain-user (local), join-host-to-domain, seed-dataset,
      install-defense-agent, deploy-network-sensor     # §5.5
    - deploy-behavior-agent                            # §5.6

- id: OP-BAR-030
  name: mirror-ready
  scope: sensor-id                       # one per capture sensor
  description: |
    The capture path is PROVEN live: every GRE tunnel to the sensor is attached,
    every mirrored host's mirror hook is installed, and (dynamic-tap heartbeat,
    2026-06-19) a sentinel frame was confirmed at the sensor for each (bridge, sensor)
    with >=1 mirrored interface. Upgraded from "mirror commands issued" to "a frame
    provably reached the sensor." NEVER emitted by a `te` deploy (`docs/
    WORK_ORDER_tap_eligibility.md`, 2026-07-28 -- a `te` deploy emits none of its
    predecessors either, §5.3) -- exists only WITHIN the A2 tap playout's own
    composed `tap-<te>` plan, gating that playout's own consumers.
  predecessors:
    - attach-gre-tunnel[*]               # §5.3 — all tunnels this tap-plan apply needs
    - install-ovs-mirror[*]              # §5.3 — all bound hosts' mirror hooks
    - verify-capture[*]                  # §5.3 — every (bridge,sensor) heartbeat passed (2026-06-19)
  consumed_by:
    - start-activity                     # §5.6 — catalog-declared predecessor; whether the two
                                        # ops ever coexist in one plan is an OP-ACT/exercise-tier
                                        # emission question, out of this barrier's own scope

- id: OP-BAR-040
  name: domain-ready
  scope: domain-fqdn                      # one per AD domain present in the scenario
  description: |
    The domain controller's AD directory services answer (LDAP + Kerberos). Domain
    users can be created and hosts can join.
  predecessors:
    - wait-domain-ready[$domain]         # §5.5
  consumed_by:
    - create-domain-user (domain users)  # §5.5
    - join-host-to-domain                # §5.5

- id: OP-BAR-050
  name: overlay-ready
  scope: kind:id                          # compound: data:<id>, identity:<id>, defense:<id>
  description: |
    A specific overlay entry is fully materialized. Lets an activity depend on a
    precise overlay (e.g. seeded data it reads, a defense tool it must evade)
    without depending on every overlay in the scenario.
  predecessors:
    # exactly one, by kind:
    - seed-dataset[$id]                  # overlay-ready[data:<id>]            §5.5
    - create-domain-user[$id]            # overlay-ready[identity:<id>]        §5.5
    - install-defense-agent[$id] / deploy-network-sensor[$id]  # overlay-ready[defense:<id>]  §5.5
  consumed_by:
    - deploy-behavior-agent              # §5.6 — when an activity targets/needs a specific overlay
```

**Predecessor-set completeness.** Every `after:` barrier reference in §5.1–5.6 resolves to one of the five barriers above, and every barrier's predecessor set is drawn only from enumerated ops. There are no forward references left open.

**Why `overlay-ready` is per-entry, not per-overlay-section.** A scenario-wide `overlays-ready` would force every activity to wait on *all* overlays (all data seeded, all defense deployed) before any activity starts — serializing things that are independent. Per-entry (`overlay-ready[data:pii-records]`) lets an activity depend only on what it actually uses, preserving parallelism between unrelated overlays and activities.

---

## 6. Worked example: `ransomware-hospital` op emission

Tracing the canonical scenario from IR_SCHEMA §10 through Pass-1 op emission shows how the catalog composes. The scenario has two networks (`corp`, `dmz`), two tapped hosts (`h-fin`, `h-corp-emr`), a router (`r-edge`), one containerized app (`emr-app` on `h-corp-emr`), a domain identity (`user-fin` in `corp.local`) and an external one (`attacker-1`), seeded PII data, a bridge-mode Suricata sensor, and two scheduled activities.

**A note the catalog surfaces about the example.** The defense entry `suricata-perimeter` sets `host: h-dmz-ids`, but no node `h-dmz-ids` is declared in `infra.nodes`. That is a dangling reference the validator's REF rules catch *before* planning (VALIDATOR §5, reference integrity); the catalog never sees it because validation gates planning. For this trace we assume the scenario is corrected to put the sensor on a declared dmz host; the point stands that op emission presupposes a validated IR.

**Also note `corp.local` has no declared domain controller.** `user-fin` has `domain: corp.local`, but no app in this particular example provides `role: adds`. A domain user with no domain controller is a validator concern (CAP/REF-class). For the trace we assume a DC app `ad-dc-1` on a host `h-corp-dc` was added, as the IR_SCHEMA §4 example itself shows.

**Emitted ops** (with the corrections above):

*Network (OP-NET):* one `create-sdn-zone[ransomware-hospital]`; per network `create-ovs-bridge` and `create-vnet` for `corp` and `dmz` (4 ops); one `apply-sdn-config`. `network-ready[corp]` and `network-ready[dmz]` follow the apply.

*Hosts (OP-HOST):* for each of `h-fin`, `h-corp-emr`, `h-corp-dc`, and the sensor host — `clone-vm` (free, parallel with network), `configure-vm-hardware`, `configure-cloud-init`, `attach-vm-to-vnet` (one per NIC), `start-vm`, `wait-guest-agent`, `wait-os-ready`. `r-edge` is multi-homed: two `attach-vm-to-vnet` ops (`r-edge@corp`, `r-edge@dmz`) plus one `configure-static-routes`. `clone-vm[*]` overlaps `apply-sdn-config`; each `attach-vm-to-vnet` joins on its net's `network-ready`.

*Mirror (OP-MIR):* `h-fin` and `h-corp-emr` are tapped, both on `corp` → `attach-gre-tunnel[corp]` (carries `gretap:$sensor`), plus `install-ovs-mirror[h-fin]` and `install-ovs-mirror[h-corp-emr]`. The Suricata sensor observes `dmz` with `tap_from: bridge` → `attach-gre-tunnel[dmz]` is emitted *even though no host on dmz is tapped* (the widened §5.3 rule), so the sensor can consume the dmz bridge mirror. A `verify-capture[corp@$sensor]` heartbeat injects a sentinel from `h-fin` (the lowest-id tapped Linux host on `corp`) and confirms it at the sensor. `mirror-ready[$sensor]` depends on both tunnels, both host mirror hooks, **and the verify-capture heartbeat**.

*Apps (OP-APP):* `emr-app` has one `container:` action → `run-container[emr-app]` on `h-corp-emr`, `after: host-ready[h-corp-emr]`. The DC's `role: adds` → `provision-app[ad-dc-1]` on `h-corp-dc`.

*Overlays (OP-OVL):* `wait-domain-ready[corp.local]` after the DC's provisioning; `create-domain-user[user-fin]` after `domain-ready[corp.local]`; `attacker-1` is `external: true` → no op. `seed-dataset[pii-records]` on `h-corp-emr`. `deploy-network-sensor[suricata-perimeter]` after `host-ready[sensor]`, `network-ready[dmz]`, and `attach-gre-tunnel[dmz]` (bridge mode). The inferred join: `act-fin-workday`'s actor `user-fin` (domain `corp.local`) is bound to host `h-fin` → `join-host-to-domain[h-fin@corp.local]` is emitted.

*Activities (OP-ACT):* `act-fin-workday` → `deploy-behavior-agent[act-fin-workday]` on `h-fin`, after `host-ready[h-fin]`, `create-domain-user[user-fin]`, `join-host-to-domain[h-fin@corp.local]`; then `start-activity` after `deploy-behavior-agent` and `mirror-ready[$sensor]`; then `wait-actor-ready`. `act-attacker-phish` → actor `attacker-1` is external (no identity ops); its agent host comes from an Activity→host binding (the corrected scenario binds it to an attacker host). Its `start-activity` likewise waits on `mirror-ready` so the phish traffic is captured. Both activities honor their `schedule:` at runtime; the `09:00` and `10:30` start times do **not** stall the deploy DAG.

**The critical-path observation.** The longest chain is: `clone-vm[h-corp-dc]` → hardware → cloud-init → attach → start → wait-agent → wait-os → `provision-app[ad-dc-1]` (AD promotion, slow) → `wait-domain-ready[corp.local]` → `create-domain-user[user-fin]` / `join-host-to-domain[h-fin@corp.local]` → `deploy-behavior-agent[act-fin-workday]` → `start-activity` (also gated on `mirror-ready`). Domain bring-up dominates; everything domainless (the sensor, the data seed, the EMR container) completes in parallel well before the domain chain clears. This is the kind of insight the catalog's explicit `after:` wiring gives the planner's critical-path estimator (PLANNER §7).

## 7. Non-goals

What the op catalog deliberately does **not** define:

- **DAG-construction logic.** How instances are emitted, how edges are built, topological ordering — PLANNER §3.
- **Retry/backoff numbers, escalation thresholds.** PLANNER §4. The catalog says `cleanup_strategy: redo|escalate`; the planner says how many times and how long.
- **The state-store schema.** PLANNER §6.
- **Concurrency limits (the numbers).** The catalog names `resource_categories`; PLANNER §8 sets the semaphore counts.
- **Adapter internals.** The `implementation:` refs are illustrative until the Proxmox adapter (OPEN_QUESTIONS Q5) ratifies; the catalog commits to *which capability* each op needs, not the adapter's API.
- **The behavior library and behavior-agent internals.** What `office-workday@v2` *does* is OPEN_QUESTIONS Q8 (AI core / behavior agents); the catalog only places and starts the agent.
- **Image build.** How a template comes to exist is OPEN_QUESTIONS Q6; the catalog consumes a bound template (`clone-vm` input).

## 8. Op summary table

| ID | name | kind | cleanup | idem | per |
|---|---|---|---|---|---|
| OP-NET-010 | create-sdn-zone | action | redo | infra | scenario |
| OP-NET-020 | create-ovs-bridge | action | redo | infra | network |
| OP-NET-030 | create-vnet | action | redo | infra | network |
| OP-NET-040 | apply-sdn-config | action | redo | infra | scenario |
| OP-NET-050 | create-subnet | action | redo | infra | network |
| OP-HOST-010 | clone-vm | action | redo | infra | host |
| OP-HOST-020 | configure-vm-hardware | action | redo | infra | host |
| OP-HOST-030 | configure-cloud-init | action | redo | infra | host |
| OP-HOST-040 | attach-vm-to-vnet | action | redo | infra | host@net |
| OP-HOST-050 | configure-static-routes | action | redo | config | router |
| OP-HOST-060 | start-vm | action | redo | infra | host |
| OP-HOST-070 | wait-guest-agent | wait | — | — | host |
| OP-HOST-080 | wait-os-ready | wait | — | — | host |
| OP-HOST-090 | configure-router | action | redo | infra | host |
| OP-HOST-100 | configure-routing | action | redo | infra | host |
| OP-MIR-010 | attach-gre-tunnel | action | redo | infra | net(bridge) |
| OP-MIR-020 | install-ovs-mirror | action | redo | infra | host |
| OP-MIR-040 | verify-capture | action | redo | config | bridge@sensor |
| OP-MIR-050 | deploy-sensor | action | redo | config | sensor |
| OP-APP-010 | provision-app | action | redo | config | app |
| OP-APP-020 | run-container | action | redo | config | app |
| OP-APP-030 | run-script | action | **escalate** | config | app |
| OP-OVL-010 | wait-domain-ready | wait | — | — | domain |
| OP-OVL-020 | create-domain-user | action | redo | config | identity |
| OP-OVL-030 | join-host-to-domain | action | redo | config | host@domain |
| OP-OVL-040 | seed-dataset | action | redo | config | data |
| OP-OVL-050 | install-defense-agent | action | redo | config | defense@host |
| OP-OVL-060 | deploy-network-sensor | action | redo | config | defense |
| OP-OVL-070 | install-agent | action | redo | config | agent@host |
| OP-ACT-010 | deploy-behavior-agent | action | redo | config | activity |
| OP-ACT-020 | start-activity | action | redo | config | activity |
| OP-ACT-030 | wait-actor-ready | wait | — | — | activity |
| OP-TFC-010 | drive-persona | action | redo | config | instance |
| OP-TFC-020 | assign-role | action | redo | config | host |
| OP-BAR-010 | network-ready | barrier | — | — | net |
| OP-BAR-020 | host-ready | barrier | — | — | host |
| OP-BAR-030 | mirror-ready | barrier | — | — | sensor |
| OP-BAR-040 | domain-ready | barrier | — | — | domain |
| OP-BAR-050 | overlay-ready | barrier | — | — | kind:id |
| OP-BAR-060 | fabric-ready | barrier | — | — | scenario |

**34 action/wait ops + 6 barriers = 40 op types** (OP-NET 5, OP-HOST 10, OP-MIR 4, OP-APP 3, OP-OVL 7, OP-ACT 3, OP-TFC 2, OP-BAR 6). *(Router slice, `docs/SLICE_ROUTER.md`: OP-NET-050 `create-subnet` — Phase 1 SDN SNAT egress, 2026-06-09; OP-HOST-090 `configure-router` — Phase 2 router-node egress NAT + inter-segment routing, 2026-06-10. Dynamic-tap slice, `docs/design/06-taps-sensors.md`: OP-MIR-040 `verify-capture` — the deploy-time capture heartbeat, 2026-06-19. Sensors design, `docs/design/06-taps-sensors.md` Phase B: OP-MIR-050 `deploy-sensor` — provision a typed capture sensor (tcpdump/suricata/zeek + in-guest flow-based gretap decap) inside its in-scenario VM over QGA, config-class, 2026-06-23; **emission is now `tier == "o2"`-gated (2026-07-13, `docs/WORK_ORDER_sensor_agnostic_te.md`) — a `te`-tier deploy with `sensors:` emits none of it (`REF-250` warns instead), only the O2 standing-surface deploy does.** Fabric slice, `docs/design/04-networking-capture.md`: OP-HOST-100 `configure-routing` — VyOS dynamic routing + overlay (OSPF/IS-IS/iBGP/eBGP/BGP-EVPN/VXLAN/redistribution/BFD), INFRA, after `host-ready` + `configure-router` (reuses its `wan_ip`); OP-BAR-060 `fabric-ready`, 2026-06-29. Sensor-agnostic te slice, `docs/WORK_ORDER_sensor_agnostic_te.md`: OP-MIR-005 `record-tap-intent` was added 2026-07-13 as the te planner's ENTIRE tap-emission surface (declarative, no adapter call), replacing `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture` in a `te` deploy's own DAG — those three stayed in the catalog, unchanged, emitted ONLY by the A2 tap playout. Full-topology tap eligibility, `docs/WORK_ORDER_tap_eligibility.md` (Authority: user): OP-MIR-005 itself is REMOVED 2026-07-28 (op count 40 → 39) — the `tap:` field it recorded is gone from the IR entirely, so a `te` deploy now emits NO OP-MIR op of any kind; A2 discovers what it may bind directly from the twin's published `attach-vm-to-vnet` topology instead. Traffic tier, `docs/WORK_ORDER_traffic_tier.md`: OP-TFC-010 `drive-persona` — (re)start one benign-persona instance on an already-deployed twin host via its driver, composed by hand by the standalone `tfc` playout (`axrange.o2control.traffic`), never emitted by the `te` planner, 2026-07-24. Exercise tier, `docs/WORK_ORDER_exercise_tier.md`: OP-ACT-010/020/030 (`deploy-behavior-agent`/`start-activity`/`wait-actor-ready`) and the adversary branch of OP-OVL-070 `install-agent` (`agent_kind=adversary`) are no longer emitted by a `te` deploy's own DAG — they stay in the catalog, unchanged, but are now composed by hand by the standalone `exr` playout (`axrange.o2control.exercise`), the same reuse pattern as `taps.py`/`traffic.py`; the exporter branch of `install-agent` (`agent_kind=exporter`, the default-agent sweep) is unaffected and stays `te`-emitted, 2026-07-24. Role/persona decoupling, `docs/WORK_ORDER_role_persona_tier.md` (Authority: user): OP-TFC-020 `assign-role` — record a host's tfc-owned semantic role as pure metadata, no adapter call and no `resource_categories` at all, so a role becomes re-assignable on a standing twin without re-cloning the node (op count 39 → 40), 2026-08-04.)* `run-script` is the only `escalate` op (opaque, non-idempotent). Five wait ops, all polling someone else's side effect. `infra`-class ops are the Proxmox/OVS/kernel/guest-OS-config ones (NET, HOST minus static-routes, MIR-010/020). Everything else in-guest is `config` — including `verify-capture`, whose sentinel inject rides QGA and sensor read rides mgmt-SSH (it holds only `vm:$vmid`, never the ansible-pool slot), and `drive-persona` (OP-TFC-010), which likewise holds only `vm:$vmid` alongside the `ansible-pool` slot its `config`-class QGA delivery needs.

## 9. Open follow-ups (deferred from v1)

- **`OP-MIR-030 register-mirror-source`** — needed only if the sensor moves from one-gretap-per-zone to shared-gretap-with-metadata-keys (ARCHITECTURE §434, v1 chose per-zone 2026-05-27). Reserved ID.
- **`tap_from` delivery beyond bridge/per-vm.** `external-mirror` is operator-wired in v1 (no op). If the platform should manage external-mirror delivery, it needs ops.
- **`bindings[].conditions.only_in_phase` (phase-gating).** IR §6 accepts and ignores `conditions:`; the catalog has no phase concept. When phases become first-class (IR §11 follow-up), ops gain phase scoping and the DAG gains phase-transition barriers.
- **Deploy-time parameters (`${N_FIN_WORKSTATIONS:5}`).** IR §11 follow-up. Op emission would expand parameterized node sets; the catalog op types are unaffected but Pass-1 emission multiplicity changes.
- **`requires-preset:` / capability negation / confidential-compute keywords.** IR §11 follow-ups; affect `clone-vm`/`configure-vm-hardware` inputs (e.g. requesting SEV/TDX), not the op set.
- **Pre-installed behavior agents (`installed_agents:` or `behavior-agent:*` capability).** IR §11 follow-up; would let `deploy-behavior-agent` short-circuit when the image already carries the runner.
- **Multi-sensor sharding/fan-out (OPEN_QUESTIONS Q10) — IMPLEMENTED.** Routing different taps to different sensors and one bridge fanning out to several sensors is live (sensors design Phase C1, 2026-06-23, CHANGELOG; `mirror-ready[$sensor]` is already per-sensor scoped, §5.8). **Still open (Q10):** sensor HA / standby / cross-site replication. **Host-side telemetry (Q9)** beyond the defense-overlay agents (`install-defense-agent`, OP-OVL-050) remains deferred in v1.
- **Doc-hygiene gap (found during the 2026-07-13 sensor-agnostic-te doc pass, not fixed this pass;
  the `OP-MIR-005` example below is now moot -- that op is REMOVED, `docs/WORK_ORDER_tap_eligibility.md`
  D4, 2026-07-28, but the underlying gap for the other five ops is unrelated and still open):**
  `OP-NET-050 create-subnet`, `OP-HOST-090 configure-router`, `OP-HOST-100 configure-routing`,
  `OP-MIR-050 deploy-sensor`, and `OP-BAR-060 fabric-ready` have §8 summary-table rows (now correctly
  re-summed to 39, LES-050) and are documented in prose elsewhere in this file, but have **no full
  YAML signature block** in their §5.x sections the way every other op does. A future doc pass should
  backfill the five missing blocks from `axrange/ops/registry.py`'s real definitions for consistency.


