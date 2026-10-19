# Scenario IR — Formal Schema (v1)

Authoritative spec for the Scenario IR. Companion to `docs/ARCHITECTURE.md` §2 (high-level picture) and `docs/IMAGE_MANIFEST.md` (image side of the contract). The CHANGELOG entries dated 2026-05-21 — "IR concrete schema", "IR capability taxonomy", "Image manifest + binding" — capture the rationale; this document captures the surface.

**Status:** ratified for v1. Changes to this spec must bump `scenario_schema_version` and add a CHANGELOG entry.

> **"Scenario" vs "target environment" (naming — decided 2026-07-09, not renamed):** a **scenario (IR)**
> is this *authored spec* — the versioned recipe; the term "Scenario IR" and the `scenario` code/API
> identifiers are **retained**. A **target environment (TE)** is the *deployed instance* a scenario
> stands up (the `te_` resource tier + the `target-environments/` library). A scenario **defines** a
> target environment. Full rationale: `docs/CONVENTIONS.md` "Scenario vs. target environment".

---

## 1. File and document conventions

### 1.1 Canonical file

A scenario is a single file conventionally named `scenario.yaml`. Encoding is UTF-8; YAML 1.2.

### 1.2 Includes

Optional. The `scenario.includes:` field lists relative paths to YAML fragments that are spliced into the scenario at parse time.

```yaml
scenario:
  includes:
    - ./fragments/std-dmz.yaml
    - ./fragments/std-ad-forest.yaml
```

**Strict-no-override semantics.** A fragment and the main file (or two fragments) may not define the same ID anywhere in the document. ID collision is a validator error, not a silent merge. The IR you read is the IR that runs.

(If override-by-design becomes a real need, a deliberate `patches:` mechanism will be introduced via CHANGELOG. Include-merge is not that mechanism.)

### 1.3 Top-level structure

```yaml
scenario_schema_version: 1            # IR spec version (this document)
scenario: { ... }                     # scenario header
layers:                               # required
  infra: { ... }
  apps: [ ... ]
  activity: [ ... ]
bindings: [ ... ]                     # required (may be empty)
sensors: [ ... ]                        # optional; logical capture sensors (§6, per-tap sensor routing, 2026-06-03)
overlays:                             # optional
  identity: [ ... ]
  data: [ ... ]
  defense: [ ... ]
fabric:                               # optional; dynamic routing + overlay (fabric slice, §3.4)
  regions: [ ... ]
```

Sections `layers:` and `bindings:` are required (though `bindings:` may be an empty list). `sensors:` and `overlays:` are optional; if `sensors:` is absent, all taps route to the platform's single/primary sensor (the v1-original behavior).

### 1.4 Identifiers

All IDs are human-meaningful strings matching `^[a-z0-9][a-z0-9_-]{0,62}$`.

Renames are supported via optional `aliases:` on any node:

```yaml
- id: h-finance-workstation
  aliases: [h-fin]
```

The validator catches dangling refs. References within the IR are by primary ID; `aliases:` are accepted as cross-document references in older saved IRs but resolve to the primary.

### 1.5 Versioning

Two distinct fields exist:

| Field | Meaning | Bumped when |
|---|---|---|
| `scenario_schema_version` | The IR spec version (this document) | The IR spec changes (additive or breaking) |
| `scenario.version` | Per-scenario monotonic int | The scenario content changes |

The validator rejects an IR whose `scenario_schema_version` it does not recognize. Major-version bumps may require migration.

> **`scenario_schema_version` widened 1→2 (Authority: user, 2026-07-24, `docs/WORK_ORDER_traffic_tier.md`
> Part 1).** STR-020 now recognizes `{1, 2}`. This is an **additive, backward-compatible** bump, not a
> breaking one: the field it accompanies (`node.role`, §3.3) is parsed regardless of the declared
> version — an existing `scenario_schema_version: 1` IR that adds `role:` gets it with **no version
> change required**, exactly like every other additive field this project has shipped
> (`default_agents`, `routing:`, `schedule.interval`). Every shipped `target-environments/*` and
> `twin-configs/*` IR stays `1` and validates completely unchanged. `2` exists purely so an author may
> explicitly mark an IR as "authored with `role:` in mind" — declaring it is optional, never enforced.
>
> **`scenario_schema_version` widened 2→3 (Authority: user, 2026-07-28, `docs/
> WORK_ORDER_tap_eligibility.md`, STR-020).** The twin substrate becomes tap-agnostic: `networks[].tap`,
> `nodes[].tap`, `nodes[].nets[].tap`, and the `tap_host`/`tap_net` → `sensor` binding kind (§3.1/§3.2/§6
> below, all now historical) are **REMOVED from the schema entirely** — not deprecated-but-tolerated,
> REJECTED. A `tap:` field anywhere now fails validation loudly (`STR-200`: "tap: is removed; author
> taps in the tap plan (scope+target selector)."), and a `tap_host`/`tap_net` binding is caught by the
> pre-existing `STR-100` closed binding-kind set (no recognized kind matches those keys any more). This
> is the **one exception** to the "additive, backward-compatible" framing every prior bump in this
> section used — a genuinely BREAKING removal, hence the version bump (unlike `1→2`, which needed none).
> `3` exists so an author may mark an IR as "authored post-removal"; declaring it is still optional
> (`{1, 2, 3}` all validate; the STR-200 rejection fires regardless of declared version — it is a
> field-shape check, not a version-gated one). All capture intent (which interfaces are mirrored, and
> to which sensor) is now authored entirely in a standalone **tap plan** (`docs/design/06-taps-sensors.md`),
> resolved against the twin's full published topology (`hosts[].nets[]`, §3.2) at tap-apply time —
> `axrange tap plan`/`axrange tap deploy` — never in the Scenario IR. See `docs/WORK_ORDER_tap_eligibility.md`
> for the full rationale.

---

## 2. `scenario:` header

```yaml
scenario:
  id: ransomware-hospital              # required; matches [a-z0-9][a-z0-9_-]*
  version: 7                           # required; monotonic int
  description: |                       # optional; free-form text
    EMR server compromise via phishing, lateral movement to PACS.
  seed: 42                             # optional; int; default unset
  reproducible: false                  # optional; default false
  labels:                              # optional; free-form k/v
    domain: healthcare
    tactic: TA0040
  includes:                            # optional; relative paths
    - ./fragments/std-dmz.yaml
  default_agents: true                 # optional; default ON (unset ⇒ true)
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Matches the ID regex. |
| `version` | yes | int | Monotonic per-scenario counter. |
| `description` | no | string | Multi-line YAML accepted. |
| `seed` | no | int | If absent, runtime selects a random seed and logs it. Per-actor seeds derive from this deterministically. Required when `reproducible: true`. |
| `reproducible` | no | bool | Default `false`. When `true`, the validator promotes unpinned image refs, unpinned behavior refs, and a missing `seed:` from warnings to errors. |
| `labels` | no | map | Free-form k/v for filtering and tooling. Not validated. |
| `includes` | no | list[string] | Strict-no-override semantics (see §1.2). |
| `default_agents` | no | bool | **Additive, A3 Stage A** (`docs/design/08-exercise-measurement.md`; stays `scenario_schema_version: 1`). The baked-in default-agent set — `overlays.defense`'s Wazuh host agent + a Prometheus node-exporter (`OP-OVL-070 install-agent`) — on every `kind: host` TE node. **ON BY DEFAULT** (user decision, 2026-07-11): unset (`None`) or explicit `true` both mean ON; explicit `false` is the scenario-level opt-out. Per-node opt-out: `node.default_agents_off` (§3.2). `overlays.adversary` is never part of this blanket sweep — it stays overlay-declared (opt-in per node via `targets:`). |

---

## 3. `layers.infra:` — networks and nodes

### 3.1 Networks

```yaml
infra:
  networks:
    - id: corp
      cidr: 10.10.0.0/24
      vlan: 10
      gateway: 10.10.0.1               # optional
      dns: [10.10.0.2]                 # optional
      dhcp:                            # optional
        enabled: true
        range: [10.10.0.100, 10.10.0.200]
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | ID regex. |
| `cidr` | yes | string | IPv4 or IPv6 CIDR. |
| `vlan` | no | int | 1–4094 if present. |
| `gateway` | no | string | IP within `cidr`. |
| `egress` | no | bool | Opt-in internet/apt egress. When `true` (and `gateway` is set), the planner emits `create-subnet` — a PVE SDN subnet with the gateway + source-NAT (SNAT). Default off (isolated). Router slice Phase 1 (`docs/SLICE_ROUTER.md`). |
| `dns` | no | list[string] | IPs. |
| `dhcp` | no | object | `{ enabled: bool, range: [start, end] }`. |

> **`tap` REMOVED (Authority: user, 2026-07-28, `docs/WORK_ORDER_tap_eligibility.md` D2, STR-200).**
> Whole-segment capture is no longer authored here — every network's every attached interface is
> unconditionally eligible for capture, and a standalone **tap plan**'s `scope: segment` selector
> (`docs/design/06-taps-sensors.md`) addresses "capture this whole net" at tap-apply time instead. A
> leftover `tap:` field on a `networks[]` entry now fails validation (`STR-200`).

### 3.2 Nodes

A node represents any infrastructure entity: a host, a router, or a switch. There is no separate `routers:` list — `kind:` discriminates.

```yaml
nodes:
  - id: h-fin
    kind: host                         # host | router | switch
    net: corp                          # single-homed: net by ID
    # OR:
    # nets:
    #   - { net: corp, ip: 10.10.0.42 }
    #   - { net: mgmt, ip: 10.99.0.42 }
    image: win11-finance@v3            # image ref; @<version> optional
    requires: [windows]                # capability taxonomy; see §7
    resources:
      cpu: 2
      ram: 4Gi
      disk: 60Gi
    boot: { order: 2 }                 # optional; overrides planner heuristic
    aliases: []                        # optional
    role: corp-workstation             # optional; a persona-assignable role tag
```

A multi-homed node uses `nets:` (a list) instead of `net:` (a single id):

```yaml
  - id: r-edge
    kind: router
    nets:
      - { net: corp, ip: 10.10.0.1 }
      - { net: dmz,  ip: 192.168.50.1 }
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | ID regex. |
| `kind` | yes | enum | `host` \| `router` \| `switch`. |
| `net` | one of | string | Network ID. Use for single-homed nodes. |
| `nets` | one of | list[obj] | `[{net, ip}, ...]`. Use for multi-homed nodes (routers, dual-NIC hosts). The validator enforces exactly one of `net:` or `nets:` per node. |
| `image` | yes | string | `<image-id>[@<version>]`. Unpinned form errors under `reproducible: true`. |
| `requires` | no | list[string] | Capability keywords from the closed 16-keyword set (§7). Validator errors on unknown keywords. Exactly one OS-family keyword required if `requires` is present and the image manifest is unavailable; otherwise the OS family may be inferred from the image manifest. |
| `resources` | no | object | `{ cpu, ram, disk }`. Sizes use k8s-style suffixes (`Gi`, `Mi`). |
| `boot.order` | no | int | Optional planner hint for sequencing within the DAG. |
| `aliases` | no | list[string] | Alternative IDs for cross-document references. |

> **`tap` / `nets[].tap` REMOVED (Authority: user, 2026-07-28, `docs/WORK_ORDER_tap_eligibility.md` D2,
> STR-200).** The former three tap granularities — whole-host (`nodes[].tap`), leg (`nets[].tap`), and
> whole-segment (`networks[].tap`, §3.1) — are all removed from the schema. Every attached interface a
> deployed twin publishes (`hosts[].nets[]` in the twin's published inventory) is now unconditionally
> capture-eligible; a standalone **tap plan** (`docs/design/06-taps-sensors.md`) selects **which**
> interfaces to actually mirror, and to which sensor, entirely outside the Scenario IR, via a
> `port`/`segment`/`perimeter`/`boundary` scope+target selector resolved against the twin's topology at
> tap-apply time. A leftover `tap:` field anywhere on a node (whole-host or per-leg) now fails
> validation (`STR-200`) exactly like a leftover `networks[].tap`. The deploy-time `verify-capture`
> heartbeat (`docs/OP_CATALOG.md` §5.3, OP-MIR-040) that used to prove a sentinel frame reaches the
> sensor before activities run is now composed by the **tap plan's own apply**, not by the twin's
> deploy.
| `default_agents_off` | no | list[string] | **Additive, A3 Stage A** (`docs/design/08-exercise-measurement.md`). Opt THIS node out of one or more baked-in default agents when `scenario.default_agents` is not `false` (§2). Values: `"defense"` \| `"exporter"`. `"adversary"` is accepted too, but only matters if this node is also listed in an `overlays.adversary[].targets:` — it is never a blanket default, so listing it here just drops the node from that entry's fan-out. |
| `role` | no | string | **Additive** (`docs/WORK_ORDER_traffic_tier.md`, `docs/design/02-twin-model.md`; Authority: user, 2026-07-24; parsed regardless of the declared `scenario_schema_version` — a `version: 1` IR with `role:` gets it too, it is not gated on v2). A **persona-assignable role tag** (e.g. `corp-workstation`, `domain-controller`) — what the Traffic tier (`tfc`) binds a persona onto. Sourced from `clone-vm`'s `node_role` input into `hosts[].role` in the published twin inventory (`axrange.planner.inventory.build_twin_inventory`); `None`/absent when undeclared, never fabricated. Unrelated to `kind` (a topology-shape tag: host/router/switch) or to any identity/overlay role. A twin config's `nodes[].role` (`docs/design/02-twin-model.md`) is the same tag, propagated by the expander. |

**Router/switch specifics:**

```yaml
- id: r-edge
  kind: router
  nets:
    - { net: corp, ip: 10.10.0.1 }
    - { net: dmz,  ip: 192.168.50.1 }
  image: vyos@1.5
  requires: [linux]
  resources: { cpu: 1, ram: 512Mi }
  static_routes:
    - { to: 0.0.0.0/0, via: 192.168.50.254 }
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `static_routes` | no | list[obj] | Router-only. `[{to: <cidr>, via: <ip>}, ...]`. |
| `joins` | no | str | AD domain FQDN this (Windows) host joins — the explicit host→domain join field (W2; emits `join-host-to-domain`, superseding the inferred `I1`). Requires a `role: adds` DC providing the domain. `docs/SLICE_WINDOWS_AD.md` §6.3. |

Switches with `kind: switch` are rare in v1; Proxmox SDN handles inter-vnet switching natively. When present, a switch references the networks it bridges via `nets: [<id>, ...]` (string-only list, no per-net IP).

### 3.4 `fabric:` — dynamic routing + overlay (fabric slice)

> **Top-level block** (sibling to `layers:`/`bindings:`, *not* under `layers.infra`), optional.
> Turns `kind: router` (VyOS) nodes into a real DC fabric — OSPF / IS-IS / iBGP / eBGP /
> BGP-EVPN / VXLAN — instead of static routes + NAT only. **VyOS-only** in v1. Full renderer +
> op detail: `docs/design/04-networking-capture.md`. Additive (stays `scenario_schema_version: 1`).

```yaml
fabric:
  fabric_asn: 65000                 # optional default iBGP AS
  regions:
    - { id: dc-east, igp: isis, isis_area: "49.0001" }   # igp: ospf | isis | both | none
    - { id: wan,     igp: none }
```

Per-router **`routing:`** stanza (on `kind: router` nodes; FAB-010 enforces router-only):

```yaml
nodes:
  - id: spine-1
    kind: router
    image: vyos@1.5                 # FAB-080: fabric routers must be VyOS
    nets: [ { net: s1l1, ip: 10.0.1.1 } ]
    routing:
      region: dc-east               # -> fabric.regions[].id (FAB-020)
      role: spine                   # spine|leaf|border|abr|area|provider|edge (FAB-030)
      loopback: 10.255.0.1/32       # dum0; router-id / iBGP update-source / VTEP source
      isis: { net: "49.0001.0000.0000.0001.00" }          # required in isis/both regions (FAB-050)
      ospf: { router_id: 10.255.0.1 }                      # optional; derive from loopback
      bgp:
        asn: 65000
        rr_server: true             # spine = route-reflector server
        rr_clients_of: [spine-1, spine-2]   # leaf -> its RR spines (FAB-040)
        evpn: true                  # l2vpn-evpn AF on the iBGP peer-groups
        vtep: true                  # leaf is a VXLAN VTEP
        ebgp_peers: [ { neighbor: 10.30.1.2, remote_asn: 65001 } ]   # border/provider
        originate: [ 10.255.0.0/24 ]
      redistribute: [ connected ]   # role: border auto-adds the OSPF<->IS-IS + BGP->IGP maps
      bfd: false                    # attach BFD to IGP neighbors
```

Per-**network** fabric attributes (on `infra.networks[]`):

| Field | Type | Notes |
|---|---|---|
| `ospf_area` | int/str | OSPF area for router legs on this net (point-to-point if router↔router). |
| `vni` | int | EVPN L2VNI — VTEP leaves attached to this net bridge it; a shared `vni` ⇒ stretched L2 (FAB-060). |

Interfaces are derived from `nets[]` order (`nets[i]` → `eth{i+1}`, eth0 = WAN); IS-IS runs on
router↔router legs; a `vni` access leg becomes an L2 bridge member (no L3 address). `fabric.regions`
merges across `includes`. Emits `OP-HOST-100 configure-routing` per fabric router + the
`fabric-ready` barrier.

### 3.5 `fabric.tenant:` — symmetric IRB / L3VNI (inter-segment routing)

Optional. Makes EVPN L2VNI segments **L3-routed to each other** — without it, EVPN is L2-only and hosts
on different segments aren't routed. Declares a single tenant VRF bound to an **L3VNI**; VTEP leaves then
create an **anycast distributed gateway** (an SVI on each L2VNI bridge, using that network's `gateway`,
with a shared anycast MAC across leaves) and per-VRF BGP advertises the gateway subnets as **EVPN Type-5**.

```yaml
fabric:
  tenant: { name: optra, l3vni: 10999, table: 1000 }
layers:
  infra:
    networks:
      - { id: e-clin, cidr: 10.10.10.0/24, vlan: 110, vni: 10010, gateway: 10.10.10.254 }
```

Hosts use the segment `gateway` as their default gateway; the leaf routes between segments over the L3VNI.
**Egress** (tenant VRF → internet, e.g. apt) works: each VTEP leaf leaks a tenant-VRF default to the
global table via the WAN gateway `configure-router` discovers, where its WAN + masquerade reach out.
**Limitations (fabric slice):** single tenant; the VyOS IRB+egress config is authored from the FRR
symmetric-IRB recipe and is **live-verify pending**.

---

## 4. `layers.apps:` — services and roles

```yaml
apps:
  - id: web-1
    role: webserver                    # informational; not enforced
    install:                           # closed action set; see §4.1
      - pkg: nginx
      - pkg: php-fpm
      - config: ./configs/nginx-vhost.conf
    listen:                            # optional
      - { port: 80, proto: tcp }
      - { port: 443, proto: tcp }
    env:                               # optional; values may reference other app IDs
      DB_HOST: db-emr

  - id: ad-dc-1
    role: domain-controller
    install:
      - role: adds
    provides:                          # optional; app-level capabilities exposed to others
      - domain: corp.local
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | ID regex. |
| `role` | no | string | Free-form hint (`webserver`, `domain-controller`, `medical-records`); informational. |
| `install` | no | list[obj] | First-boot install actions; closed action set, see §4.1. |
| `listen` | no | list[obj] | `[{port, proto}, ...]` — the **declared listen set**: what the twin says this app serves. **Its first consumer landed 2026-09-05** (`docs/WORK_ORDER_protocol_faithful_services.md` S1; **D2 (b)**, Authority: user, 2026-09-04): a twin config's `nodes[].listen:` passes through to here, `clone-vm` carries it as `node_listen` (only when non-empty), `axrange te inventory` / `GET /api/deploys/{id}/inventory` publish it as `hosts[].listen`, and the fidelity scorecard's rows 4/5 report it against the **observed** listen set. **The field, its shape and its optionality are unchanged** — no `scenario_schema_version` bump, and no shipped content set it before that build, so every pre-existing IR hashes identically. Still not a measurement: a declaration is not evidence that a port is bound (row 4's in-guest `ss -lnH -tu` and row 5's protocol-aware probe remain the measurement of record), and it still generates no network policy — that use is unbuilt. **Shape enforcement lives at the twin-config layer, not in the validator:** no catalog rule references this field (`VALIDATOR` is unchanged at 114 rules), because the refusal belongs where the author writes it — `twinconfig.expand._check_listen` range-checks the port, closes `proto` to the transports `{tcp, udp}` the fidelity probe can dispatch on, refuses a duplicate `(port, proto)` and refuses an empty `listen: []`, naming `twin_config.nodes[i].listen[j].<field>`. This IR-side model stays permissive like every other field in this version-locked schema, and a malformed hand-authored entry is **skipped** by the planner's join rather than failing a deploy over an informational fact. |
| `env` | no | map | Environment exposed to the app. Values may reference other app IDs; the validator resolves; the adapter materializes. |
| `provides` | no | list[obj] | App-level facts other apps/nodes can depend on (e.g. AD domain name). Free-form. |

### 4.1 `install:` — closed action set

Exactly six action kinds are accepted in v1. The validator errors on any other key.

| Action | Form | Meaning |
|---|---|---|
| `pkg` | `{ pkg: <name> }` | OS package via apt/yum/winget. Adapter chooses package manager from image metadata. |
| `pkg@version` | `{ pkg: <name>, version: <ver> }` | Pinned package version. |
| `config` | `{ config: <path> }` | File copied to canonical location for the role. |
| `role` | `{ role: <name> }` | Platform-shorthand. Image's `install_support.role:` declares which role names are valid (e.g. `adds`, `iis`, `docker-host`). |
| `container` | `{ container: <oci-ref> }` | Runs in podman/docker on the host. Image must declare `install_support.container: true`. |
| `script` | `{ script: <path> }` | Last-resort: shell script, run once at first-boot. |

**Hard rule:** `install:` actions execute at first-boot only, via cloud-init + pull-mode Ansible. They are not a runtime configuration channel. Mid-run reconfiguration must be modeled as activity.

Adding a seventh action kind requires a CHANGELOG entry. We do not allow free-form actions.

---

## 5. `layers.activity:` — actors, behaviors, schedules

Three forms accepted. The validator distinguishes by which of `behavior`, `overrides`, `inline` is present.

### 5.1 Library reference (dominant form)

```yaml
activity:
  - id: act-fin-workday
    actor: user-fin                    # resolves per §5.4
    behavior: office-workday@v2        # library reference; @<version> optional
    intensity: 0.6                     # optional; 0.0–1.0
    schedule:                          # optional; if absent, runs for scenario duration
      start: 09:00
      end: 17:00
      days: [mon, tue, wed, thu, fri]
```

### 5.2 Library + overrides

Overrides only the fields named; library's other fields remain.

```yaml
- id: act-fin-workday-custom
  actor: user-fin
  behavior: office-workday@v2
  overrides:
    steps:
      - { open: chrome, urls: [https://internal-intranet] }
      - { open: outlook }
```

### 5.3 Pure inline

No library reference; the steps are inline.

```yaml
- id: act-attacker-recon
  actor: attacker-1
  inline:
    steps:
      - { tool: nmap, target: 10.10.0.0/24, args: "-sS -T2", technique: T1595 }
      - { tool: bloodhound, args: "-d corp.local", technique: T1087 }
  schedule:
    start: 10:30                        # one-shot
```

**`technique:`** — **additive, A4** (`docs/design/08-exercise-measurement.md`; stays `scenario_schema_version: 1`). An optional free-form ATT&CK technique id (e.g. `T1059`) on an inline step. Steps are an unvalidated `dict[str, Any]`, so this needs no schema-class change — the renderer (`config_exec/render.py::render_activity_runner`) emits one `[axr-truth] run=<id> technique=<T> host=<h> ts=<..>` marker per technique-labeled step, shipped to Loki as the ground-truth half of the coverage join (§11 measurement). An unlabeled step renders unchanged, with no marker. `AdversaryEntry.techniques` (§8.4) is the parallel, informational form for a Caldera-driven `overlays.adversary` entry — a forward-looking hint, not a compiled/executed field.

### 5.4 Actor resolution

The `actor:` field resolves in two modes:

1. **If `overlays.identity` contains an entry with `id: <actor>`** → use that identity (domain user, AD-bound, etc.).
2. **Otherwise** → an implicit local user with username `<actor>` on the activity's bound host.

The validator **warns** when an activity references an actor that is missing from a present `overlays.identity` overlay — this forces an explicit decision (add to overlay, or rename).

### 5.5 Fields

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | ID regex. |
| `actor` | yes | string | Resolved per §5.4. |
| `behavior` | one of | string | `<behavior-id>[@<version>]`. Library form. |
| `overrides` | no | object | Only used with `behavior`. |
| `inline` | one of | object | `{ steps: [ ... ] }`. Pure inline form. |
| `intensity` | no | float | `0.0`–`1.0`. Multiplier on event rate. |
| `schedule` | no | object | See §5.6. |
| `seed` | no | int | **Un-reserved 2026-07-10** (activity schedule executor, `docs/SLICE_ACTIVITY.md` §11.2). RNG seed for a jittered `schedule.interval`'s cadence — same seed ⇒ same fire sequence (reproducible replay). In the one-shot MVP this field was accepted-and-silently-ignored (ACT-050 warned unconditionally); ACT-050 now only warns when there's no `schedule.interval` for the seed to seed. |

The validator enforces: exactly one of `behavior` or `inline` is present per activity.

### 5.6 Schedule

```yaml
schedule:
  start: HH:MM                         # 24h UTC, optional
  end: HH:MM                           # optional; with start, defines a daily window
  days: [mon, tue, ...]                # optional; subset of weekday names
  once: true                           # optional; one-shot at `start` instead of daily
  interval: { min: 30s, max: 5m }      # optional; seeded jittered cadence within the window
```

**`interval:`** — **additive, activity schedule executor** (`docs/SLICE_ACTIVITY.md` §11.1/§11.4, 2026-07-10; stays `scenario_schema_version: 1`, same precedent as the fabric-slice `routing:` addition). `{min, max}` duration strings (`"30s"`/`"5m"`/`"2h"`); fires activity steps at `random(min, max)` **within** the schedule window, drawn from a seeded RNG using the activity's `seed:` (above) — same seed ⇒ same schedule, which is what makes a replay-delta (A4 follow-on) meaningful. Mutually exclusive with `once: true` (`ACT-060`). Multiple scheduled activities on one host get independent per-activity runners keyed `act-<id>` — no collision. A jittered activity with no `seed:` is accepted (non-reproducible) but flagged (`ACT-070`, warning).

If `schedule` is absent, the activity runs continuously for the scenario's duration.

---

## 6. `bindings:` — explicit cross-section glue

Bindings are typed dicts. Each entry has a "kind" determined by which keys are present.

```yaml
bindings:
  - { app: web-1, host: h-dmz-web }
  - { app: ad-dc-1, host: h-corp-dc }
  - { activity: act-fin-workday, host: h-fin }
  - { activity: act-attacker-recon, host: h-attacker-kali }
  - { activity: act-attacker-recon, targets: [h-corp-dc, h-corp-emr] }
```

| Binding kind | Keys | Meaning |
|---|---|---|
| App→host | `app`, `host` | Places an app onto a host. |
| Activity→host | `activity`, `host` | The host where the activity's agent runs. |
| Activity→targets | `activity`, `targets` (list) | What the activity targets. Required for activities that act on specific entities (recon, exploit, lateral movement). |
| Overlay bindings | overlay-specific | Defense overlays have their own bindings in `overlays.defense` itself (`observes:`, `installed_on:`); see §8.3. |

The validator enforces:
- All referenced IDs exist somewhere in the document.
- An activity has at most one Activity→host binding.
- An activity may have at most one Activity→targets binding.

> **Tap→sensor binding kind REMOVED (Authority: user, 2026-07-28, `docs/WORK_ORDER_tap_eligibility.md`
> D6).** The former `tap_host`/`tap_net` + `sensor` binding kind (2026-06-03) — which routed a tapped
> host's or network's mirrored traffic to a logical sensor — is gone along with the `tap:` field it
> routed. All tap→sensor routing now lives entirely in a standalone tap plan's `bindings:` (a `sensors:`
> list per binding, `docs/design/06-taps-sensors.md`), not here. A leftover `tap_host`/`tap_net` binding
> needs no dedicated validator rule of its own: `STR-100`'s pre-existing closed binding-kind set already
> rejects it (no recognized kind matches those keys any more) — the same posture `REF-200`, the rule
> that used to validate this kind's references, retired into (never reused).

**`sensors:` — typed capture/analysis sensors (top-level, optional; 2026-06-23, sensors design — unifies the former standing capture appliance + Suricata network-sensor; evolves per-tap sensor routing 2026-06-03).** Declares the sensors a scenario captures to; a scenario may declare several:

```yaml
sensors:
  - { id: pcap, type: tcpdump-sensor, primary: true, captures: [corp, dmz] }
  - { id: ids,  type: suricata-sensor, captures: [dmz], export: { ship: loki } }
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Logical sensor id; referenced by tap→sensor bindings + the per-(bridge,sensor) GRE key. |
| `type` | no | string | The capture/analysis tool, from the `axrange.sensors` registry: `tcpdump-sensor` / `suricata-sensor` / `zeek-sensor` (open). **Absent ⇒ `tcpdump-sensor`** (the former standing capture appliance). REF-210 errors on an unknown type. |
| `primary` | no | bool | Exactly one sensor is primary (the default target for any tap with no tap→sensor binding). If `sensors:` is omitted entirely, the platform's single configured sensor is the implicit primary. |
| `captures` | no | list[string] | Tapped net/host ids this sensor subscribes to (omitted ⇒ all taps). A tap fans out to every subscribing sensor. REF-210 errors on an undefined source. |
| `transport` | no | string | The isolated `transport: true` network this sensor's capture NIC binds to (REF-210 checks it is a transport net). |
| `image` / `resources` / `export` | no | — | **Phase B/C** (`docs/design/06-taps-sensors.md`): base-image override, VM sizing (pcap storage), and artifact export (`ship: loki|none`). Parsed now; materialized when sensors deploy as VMs. |

**Phase A (current):** a logical sensor resolves to a concrete transport endpoint (a `vnet-transport` IP) via **platform/cluster config at deploy time** (`SENSOR_<ID>_TRANSPORT_IP`) — the IR stays platform-agnostic. **Phase B** (`docs/design/06-taps-sensors.md`) materializes each sensor as an **in-scenario VM**, and the gretap `remote` re-points from config to the deployed sensor's transport IP. Each sensor is an independent flow-based-gretap capture node; the no-scenario-correlation boundary holds **per sensor**. See CHANGELOG 2026-06-23 "sensors" and the 2026-06-03 per-tap sensor routing entry.

> **Target direction (O2, `docs/design/05-o2-surface.md`, 2026-07-08):** this `sensors:` block's
> in-scenario-VM model (Phase B/C above) is **superseded in direction** by standing `o2_` sensors —
> sensors move out of the per-scenario deploy into a separately-stood-up O2 surface, and taps become
> a separate config playout binding a deployed target environment to them. **Both remain the current
> implementation** until that refactor lands; this section documents what `axrange deploy` does today.

Bindings may carry optional annotations (forward-compatible with phase-gating):

```yaml
- app: web-1
  host: h-dmz-web
  conditions:
    only_in_phase: [normal-ops]
```

`conditions:` semantics are deferred to a future CHANGELOG entry; the validator currently accepts and ignores them.

---

## 7. Capability taxonomy (the `requires:` field)

The closed v1 set is sixteen keywords, partitioned by prefix.

### 7.1 OS family (bare; exactly one per node)

| Keyword | Meaning |
|---|---|
| `windows` | Any Windows image. |
| `linux` | Any Linux image. |
| `bsd` | FreeBSD / OpenBSD / pfSense / OPNsense. |

OS family does not encode version or distribution — that's the image's job. `win11-finance@v3` is specific; `windows` is just "can a Windows image be selected here."

### 7.2 Host capabilities (`host:*`)

Facts the planner verifies against the live Proxmox cluster when placing a VM.

| Keyword | Meaning |
|---|---|
| `host:nested-virt` | Physical host supports nested virtualization (Intel VT-x / AMD-V exposed to guest). |
| `host:tpm` | vTPM provisioning available (BitLocker, attestation scenarios). |
| `host:secure-boot` | UEFI Secure Boot supported. |
| `host:uefi` | UEFI firmware (not BIOS). Modern Windows installations require this. |
| `host:gpu` | GPU passthrough; the host has a passthroughable GPU. |
| `host:cpu-aes-ni` | AES-NI instruction set (crypto-heavy workloads). |
| `host:hugepages` | Linux hugepages support (DPDK, OVS-DPDK, performance-sensitive workloads). |

### 7.3 Kernel/runtime capabilities (`kernel:*`)

Facts about the running OS provided by the image. Verified by the validator against image manifest `provides:`.

| Keyword | Meaning |
|---|---|
| `kernel:edr-hooks` | ETW (Windows) or eBPF + audit (Linux) — modern EDR tools can install hooks. |
| `kernel:ebpf` | Linux eBPF specifically. |
| `kernel:audit` | Linux auditd or Windows auditing subsystem. |
| `kernel:container-runtime` | podman or docker installed and usable. |
| `kernel:dotnet-clr` | .NET runtime present (PowerShell tooling, .NET-based attack tools). |
| `kernel:powershell-5plus` | PowerShell 5.0+ available (rules out vanilla minimal server-core). |

### 7.4 Rules

- **Exactly one OS-family keyword** per node (validator-enforced).
- **No implicit inheritance.** `windows` does not imply `host:uefi`. Every required capability is named explicitly.
- **Strictly closed for v1.** Adding a 17th keyword requires a CHANGELOG entry and review of `scenario_schema_version` implications.
- **Unknown keywords are errors**, not warnings.
- **`requires:` is yes/no facts; `resources:` is amount-requests.** They do not overlap.

### 7.5 Dispatch model

| Category | Consumer | Check |
|---|---|---|
| OS family | Validator | Image catalog: can an image of this OS family be selected? |
| `kernel:*` | Validator | Image manifest `provides:` ⊇ node `requires:` (kernel subset). |
| `host:*` | Planner | Live Proxmox cluster: does any node satisfy IR-`host:*` ∪ binding `requires_host_caps:`? |

---

## 8. `overlays:` — optional cross-cutting dimensions

Overlays are optional per scenario. If absent, no overlay-dependent features are active.

> **BUILT (A3 Stage A, `docs/design/08-exercise-measurement.md`, 2026-07-11):** the overlay set is
> now `identity + data + defense + **adversary**` — a fourth overlay pairing a red host agent
> (Sandcat) with the O2 Caldera server, symmetric to how `defense` pairs a blue host agent (Wazuh)
> with the O2 Wazuh server. `overlays.adversary` (§8.4) landed as an **additive** field — like the
> fabric-slice `routing:` and schedule-executor `interval:` additions, it did **not** require a
> schema-version bump (`scenario_schema_version` stays `1`). Unit/integration-verified (794 pytest
> tests); gated `dev45` live-verify pending.

### 8.1 `overlays.identity`

```yaml
identity:
  - id: user-fin
    type: user
    domain: corp.local                 # optional; absent = local user
    groups: [finance, accounting]
    privileges: [standard-user]

  - id: attacker-1
    type: user
    external: true                     # actor exists outside the scenario's identity system
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Referenced by `activity.actor`. |
| `type` | yes | enum | `user` (others reserved for future). |
| `domain` | no | string | Maps to an AD domain. If absent, identity is local to the host. |
| `groups` | no | list[string] | Group memberships. |
| `privileges` | no | list[string] | Free-form privilege tags. |
| `external` | no | bool | If `true`, the identity exists outside the scenario's identity system (e.g. an attacker). |

### 8.2 `overlays.data`

```yaml
data:
  - id: pii-records
    host: h-corp-emr                   # ID of the host where the data lives
    path: /var/lib/emr/records
    classification: pii
    crown_jewel: true                  # optional
    synthetic_size: 2Gi                # optional; how much fake data to seed
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | |
| `host` | yes | string | Existing host node ID. |
| `path` | yes | string | Filesystem path on the host. |
| `classification` | yes | string | Free-form tag (`pii`, `phi`, `pci`, `secret`, etc.). |
| `crown_jewel` | no | bool | Flag for scenario scoring. |
| `synthetic_size` | no | string | k8s-style size; how much synthetic data to seed at first-boot. |

### 8.3 `overlays.defense`

Defense entries bind via `observes:` (network mode), `installed_on:` (host-agent mode), or both. The validator enforces that at least one is present.

```yaml
defense:
  - id: suricata-perimeter
    tool: suricata
    host: h-dmz-ids                    # where the tool itself runs
    observes:
      - { net: dmz, mode: ids }
      - { net: corp, mode: ids }
    ruleset: emerging-pro@v2026.05
    tap_from: bridge                   # bridge | per-vm | external-mirror

  - id: edr-corp-fleet
    tool: crowdstrike-falcon
    installed_on:
      - h-fin
      - h-corp-emr
      - h-corp-dc
    config: ./configs/falcon-policy.yaml

  - id: zeek-everywhere
    tool: zeek
    host: h-mon
    observes:
      - { net: corp, mode: passive }
    installed_on:
      - h-corp-emr
    ruleset: ./configs/zeek-scripts/
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | |
| `tool` | yes | string | Free-form tool name. |
| `host` | conditional | string | Required when `observes:` is present (where the network-mode tool runs). |
| `observes` | one of | list[obj] | `[{net, mode}, ...]`. Mode is free-form (`ids`, `passive`, etc.). |
| `installed_on` | one of | list[string] | Host node IDs. Capability checks fire on each: e.g. Falcon implies `requires: [kernel:edr-hooks]`. |
| `ruleset` | no | string | Versioned ref or path. |
| `config` | no | string | Path to tool config. |
| `tap_from` | conditional | enum | Required with `observes:`. `bridge` \| `per-vm` \| `external-mirror`. Declares how the network traffic reaches the tool. |

The validator enforces:
- Every defense entry has `observes:` or `installed_on:` (or both).
- `observes:` references a network ID; the tool's host has a NIC on that network, or a mirror feeds it (validator currently warns when neither is provable at IR time; planner errors when neither is true at deploy time).
- `installed_on:` hosts satisfy the tool's implied capability requirements.

### 8.4 `overlays.adversary`

**Additive, A3 Stage A** (`docs/design/08-exercise-measurement.md`, `docs/design/09-content-overlays.md`; stays `scenario_schema_version: 1`). The red-team counterpart to `overlays.defense`: a declarative overlay that plants a **Sandcat** (Caldera) agent on `targets:` and points it at a Caldera **`server:`**. Execution/playout is **manual** at the Caldera console — this overlay only plants and points the agent; it never compiles or runs an operation itself (the `engine: atomic|caldera` OP-ACT-compiling ladder described in `docs/design/09-content-overlays.md` remains a separate, not-yet-built follow-on).

```yaml
adversary:
  - id: redteam-1
    targets: [h-fin, h-corp-emr]        # kind: host nodes only (QGA-mediated install)
    server: caldera.o2.local:8443       # the O2 Caldera server (explicit for now)
    group: red                          # optional; Caldera agent group tag, default "red"
    adversary: apt-ish-plan             # optional; informational adversary-profile reference
    techniques: [T1003, T1558.003]      # optional; informational ATT&CK ids this plan exercises
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | |
| `targets` | yes | list[string] | Fan-out — plants the agent on every listed node. Every entry is checked individually (`REF-230`) and must be a `kind: host` node (`REF-240`) — the agent installs over QGA, so a router/switch target would hang at deploy rather than fail at validate. |
| `server` | yes | string | Caldera server `host[:port]` or URL the agent beacons to. The O2 Caldera server is Stage B (`docs/design/08-exercise-measurement.md`) — not yet auto-resolved from a live O2 inventory, so this is an explicit field for now (a TODO for A1/`o2control`). |
| `group` | no | string | Caldera agent group tag. Default `"red"`. |
| `adversary` | no | string | Informational adversary-profile/plan reference. Not compiled by the engine. |
| `techniques` | no | list[string] | Informational ATT&CK technique ids this Caldera plan is expected to exercise — a forward-looking hint for a Caldera-operation-report-ingestion adapter (deferred), not ground truth itself. Ground truth for a Caldera-driven run comes from its operation report at playout time; ground truth for an `activity` (inline-steps) run comes from the per-step `technique:` label (§5.3, A4). |

The validator enforces (`REF-220`/`REF-230`/`REF-240`): every entry has an `id`; every `targets:` entry resolves to a defined node; every resolved target is `kind: host`. `overlays.adversary` targets are never part of the `scenario.default_agents` blanket sweep (§2, §3.2) — an adversary agent is always opt-in per node via `targets:`.

---

## 9. Validator behavior

Validation runs in three stages:

1. **Structural.** Parse YAML; check `scenario_schema_version`; field-shape and required-field validation; ID regex; capability-keyword set membership.
2. **Reference integrity.** Every cross-reference resolves: `bindings.app`, `bindings.host`, `bindings.activity`, `activity.actor`, defense `host`/`installed_on`/`observes.net`, data `host`, etc. No dangling refs.
3. **Capability intersection.** With image manifests loaded: for each node, image `provides:` ⊇ node `requires:` (OS-family + kernel subset). For each `install:` action on a bound app, the action kind ∈ image's `install_support:`.

Capacity and live-cluster checks (e.g. is a Proxmox node with `host:tpm` available?) are the **planner's** job, not the validator's. Rationale: the validator should run anywhere — in CI, in a pre-commit hook, on a laptop — without needing cluster access.

Severities: most checks are errors. The reproducibility-warning class only emits when `reproducible: false`; under `reproducible: true` they become errors.

A formal rule catalog with severity codes is forthcoming (see OPEN_QUESTIONS Q3).

---

## 10. Worked example

A minimal but complete scenario. (Corrected 2026-05-28 so it passes validation: `h-corp-dc` + `ad-dc-1` give `user-fin`'s `corp.local` domain a declared controller, and `h-dmz-ids` is declared as the Suricata sensor host. The pre-correction version — dangling `h-dmz-ids` and controller-less `corp.local` — is retained verbatim as a deliberately-failing validator fixture in the `axrange/` test suite. See CHANGELOG 2026-05-28.)

```yaml
scenario_schema_version: 1

scenario:
  id: ransomware-hospital
  version: 7
  description: |
    EMR server compromise via phishing, lateral movement to PACS.
  seed: 42
  reproducible: true
  labels:
    domain: healthcare
    tactic: TA0040

layers:
  infra:
    networks:
      - { id: corp, cidr: 10.10.0.0/24, vlan: 10, gateway: 10.10.0.1 }
      - { id: dmz,  cidr: 192.168.50.0/24, vlan: 50 }

    nodes:
      - id: h-fin
        kind: host
        net: corp
        image: win11-finance@v3
        requires: [windows]
        resources: { cpu: 2, ram: 4Gi, disk: 60Gi }

      - id: h-corp-emr
        kind: host
        net: corp
        image: ubuntu-server-22@v5
        requires: [linux, kernel:container-runtime]
        resources: { cpu: 4, ram: 8Gi, disk: 100Gi }

      - id: r-edge
        kind: router
        nets:
          - { net: corp, ip: 10.10.0.1 }
          - { net: dmz,  ip: 192.168.50.1 }
        image: vyos@1.5
        requires: [linux]
        resources: { cpu: 1, ram: 512Mi }

      - id: h-corp-dc                    # domain controller for corp.local (serves user-fin)
        kind: host
        net: corp
        image: win2022-dc@v2
        requires: [windows]
        resources: { cpu: 2, ram: 4Gi, disk: 80Gi }

      - id: h-dmz-ids                    # sensor host for the perimeter Suricata
        kind: host
        net: dmz
        image: ubuntu-server-22@v5
        requires: [linux]
        resources: { cpu: 2, ram: 4Gi, disk: 40Gi }

  apps:
    - id: emr-app
      role: medical-records
      install:
        - container: hospital/emr@1.4
      listen:
        - { port: 8443, proto: tcp }

    - id: ad-dc-1
      role: adds                          # provides the corp.local domain controller
      install:
        - role: domain-controller

  activity:
    - id: act-fin-workday
      actor: user-fin
      behavior: office-workday@v2
      intensity: 0.6
      schedule: { start: 09:00, end: 17:00, days: [mon, tue, wed, thu, fri] }

    - id: act-attacker-phish
      actor: attacker-1
      inline:
        steps:
          - { tool: send-email, to: user-fin, payload: ./payloads/phish-emr.eml }
      schedule: { start: 10:30, once: true }

bindings:
  - { app: emr-app, host: h-corp-emr }
  - { app: ad-dc-1, host: h-corp-dc }
  - { activity: act-fin-workday, host: h-fin }
  - { activity: act-attacker-phish, targets: [h-fin] }

overlays:
  identity:
    - { id: user-fin, type: user, domain: corp.local, groups: [finance] }
    - { id: attacker-1, type: user, external: true }

  data:
    - id: pii-records
      host: h-corp-emr
      path: /var/lib/emr/records
      classification: pii
      crown_jewel: true
      synthetic_size: 2Gi

  defense:
    - id: suricata-perimeter
      tool: suricata
      host: h-dmz-ids
      observes:
        - { net: dmz, mode: ids }
      ruleset: emerging-pro@v2026.05
      tap_from: bridge
```

---

## 11. Open follow-ups

Tracked here for visibility; will become CHANGELOG entries when needed:

- `requires-preset:` bundles (e.g. `modern-windows` → `[windows, host:uefi, host:secure-boot, host:tpm]`).
- Capability negation (`!host:tpm`) for "must NOT have" requirements.
- Confidential-compute keywords (`host:sev`, `host:tdx`, `host:sgx`).
- `behavior-agent:*` capability prefix or separate `installed_agents:` field for declaring pre-installed activity-simulator agents.
- Phases as a first-class concept (already gestured at via `bindings[].conditions.only_in_phase`).
- Deploy-time parameters (`${N_FIN_WORKSTATIONS:5}`).
- Formal rule catalog with stable severity codes (see OPEN_QUESTIONS Q3).
