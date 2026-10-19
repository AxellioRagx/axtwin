# Cyber Range — Architecture

This document is the canonical architecture deep-dive supporting `CLAUDE.md`. **§0 below is the
current top-level frame (2026-07); §1–§7 are the v1 *engine* deep-dive** — their IR → validator →
planner → adapter → capture **mechanics remain accurate**, read through §0's frame. Sections still
mark **ratified** (locked) vs **open** (`docs/OPEN_QUESTIONS.md`).

---

## §0. Current architecture — three lifecycles (2026-07)

**Ratified direction** (`docs/design/05-o2-surface.md`; supersedes the earlier "engine + 5 planes, sensors
in-scenario" framing). The system is three separately-triggered lifecycles:

```
┌ DESIGN PLANE ─ target-environments/ (Scenario IRs) + scenario_ai_research/ (corpus→design)
│     a *scenario* is the authored spec; deploying it yields a *target environment* (TE)
├ ENGINE (standalone, axrange/) ─ a generic spec-executor: plan → deploy → destroy
│     a scenario deploy stands up the TE substrate (te_); O2 control REUSES the same
│     executor to stand up the surface (o2_). The engine imports nothing from O2.
└ O2 — Observability-&-Orchestration surface (o2/, stood up separately on request)
      sensors + capture appliance (tcpdump-sensor) + telemetry (Grafana/Loki/Prometheus)
      + Caldera server + Wazuh server + UI/control component
```

**Sensors are STANDING `o2_` components** — this supersedes the in-scenario-sensor model. The per-tap
`tc` → keyed-gretap → flow-decap **mechanics (§3/§5) are unchanged**; only *who owns the receiver and
when it's stood up* moved (engine→O2). See `docs/design/05-o2-surface.md` (A1 — the control
component is a thin engine *client*, not a second orchestrator).

**The TE substrate is a living, agent-instrumented world** (`docs/design/08-exercise-measurement.md`, A3):

| Substrate (`te_`) | O2 surface (`o2_`) |
|---|---|
| infra · apps | sensors · capture appliance · Grafana/Loki/Prometheus |
| **overlays = identity + data + defense + adversary** | **Prometheus server** ← Prometheus exporter (default) |
| **activity** = behavior (benign **and** scripted attacks) + schedule (seeded jitter) + tools | **Caldera server** ← **Sandcat** (`overlays.adversary`) |
| default agents: Prometheus exporter · Sandcat · Wazuh agent | **Wazuh server** ← **Wazuh agent** (`overlays.defense`) |

Each default agent pairs with an O2 server. **Attack is dual-mode** — internal `activity` (engine-run,
deterministic; the AD kill chain lives here) or external **Caldera** (manual, interactive). **Defense**
= the host agents + O2 network sensors, tuned manually. **Measurement (A4,
`docs/design/08-exercise-measurement.md`)** = an ATT&CK-technique **coverage matrix** per attack run
(truth markers + detections joined in Loki). Red/blue **playout is operator-driven (manual now**;
syncing automation is future). It all serves the **replay loop** — the product's primary purpose:

```
deploy TE → attack → measure coverage → add/tune defense → REPLAY attack (identical, via seed) → measure delta
```

**Blast-radius tiers** (the tag is embedded in each deterministic name, `adapter_proxmox/naming.py`;
`docs/design/05-o2-surface.md`): **`te_`** (TE deploy) · **`tap_`** (per-tap mirror + source gretap) ·
**`o2_`** (standing surface) · **`img_`** (golden templates). A `te_` destroy cascades to its `tap_`
and **structurally cannot** touch `o2_` / `img_` (a `_protected()` guard in `planner/teardown.py`).

**Design trail:** `docs/design/05-o2-surface.md` · `_o2_control.md` · `_o2_attack_defense.md` ·
`_o2_measurement.md` · `docs/CONVENTIONS.md` (scenario-vs-TE, canonical names, doc genres) ·
`docs/SLICE_ACTIVITY.md` §11 (the seeded schedule executor).

---

## §1. Canvas and layers

> **Status note, 2026-09-29 (implementation, Claude Code; no schema version bump — a status note
> only).** The free-hand layered canvas described in this section was never built (`docs/
> OPEN_QUESTIONS.md` Q1, `docs/reference/ui-consolidation/03-ui-history-and-decisions.md` §1 row 0).
> The layer/overlay model below remains a live IR concept (`layers:`/`overlays:` in Scenario IR,
> `docs/IR_SCHEMA.md`), but it is not a UI commitment — the current design of record for the
> operator UI is `docs/design/16-unified-console.md`. See that entry's `docs/CHANGELOG.md`
> 2026-09-29 record for the `Supersedes:`.

### What this is

The canvas is a free-hand UX where an operator designs a scenario by dropping nodes onto a canvas, wiring them, and annotating them. It is the entry point to the platform.

### Three primary layers + optional overlays

**Ratified.**

- **Layer 1 — Infrastructure:** hosts, vSwitches, vRouters, VLANs, subnets, DMZ/OT/corp segments. The wiring.
- **Layer 2 — Apps & Tools:** what runs on the infrastructure. Web stacks, DBs, AD, mail, CI/CD; also defensive tools (EDR, SIEM, IDS, WAF, firewall agents). What's running.
- **Layer 3 — Activity:** user behavior simulators, scheduled jobs, attacker TTPs, beacons. What's happening.

**Overlays (per-scenario, optional):** — the four are `identity + data + defense + adversary` (§0):
- **Identity** — users, roles, group memberships, trust relationships.
- **Data** — where crown-jewel data sits, classification, expected data flows.
- **Defense** — blue **host** agents on TE nodes (default **Wazuh agent**) → the O2 **Wazuh server** (§0/A3). Network detection is the O2 sensors.
- **Adversary** — the red **host** agent (**Sandcat**) on TE nodes → the O2 **Caldera server**; server-side adversary profiles are the (manual) attack playout.

### Why three, not more

Three layers map to three mental questions: *what's wired? · what's running? · what's happening?* Adding planes beyond three forces designers to reason about empty layers in scenarios that don't need them. Identity/Data/Defense matter for serious scenarios but not for every scenario — making them toggleable overlays gives precision where it earns its keep without taxing every design.

The overlay model is the v1 ceiling on the number of planes. If a class of scenarios in the future needs a new overlay (e.g. supply-chain trust relationships for an attack on a CI/CD pipeline), add it as an overlay, not as a primary layer.

### Canvas → IR contract

**Ratified at conceptual level; implementation open.**

The canvas is "just an editor" — its only output is the **Scenario IR**. Every property visible in the UI corresponds to an IR field. No hidden canvas state. This rule keeps the canvas from drifting from its serialized form and lets non-canvas tools (CLI, AI scenario planner) produce the same IR.

---

## §2. Canvas-to-deploy pipeline

### Six stages

**Ratified.**

```
1. Canvas       — designer's UX, serializes layered topology + overlays.
2. Scenario IR  — YAML + JSON schema; platform-agnostic vocabulary.
3. Validator    — schema, cross-layer bindings, capacity, image availability,
                  network sanity (CIDR collisions, VLAN conflicts).
4. Planner      — DAG of ordered operations. Enables partial redeploys.
5. Adapter      — per-platform translator: Proxmox (v1), VMware/cloud (later).
6. Runtime      — live VMs/CTs/networks on the target platform.

State store    — versioned IR snapshots + deploy state + drift detection.
```

### The IR is the platform-agnostic boundary

**Ratified.**

The IR uses neutral vocabulary: `host`, `network`, `switch`, `router`. Never `qemu vm`, `lxc container`, `ec2 instance` — those are adapter decisions. This indirection is the one thing that lets us add VMware/cloud adapters later without touching the canvas, the AI agents, or any saved scenarios.

### IR concrete shape

**Ratified.** See `docs/IR_SCHEMA.md` for the full field-level spec. Summary here.

A scenario is a single canonical `scenario.yaml` (with optional `includes:` for reusable fragments, strict-no-override semantics) containing four sibling top-level sections plus a header:

```yaml
scenario_schema_version: 1

scenario:
  id: ransomware-hospital
  version: 7                           # per-scenario monotonic int
  description: |
    EMR server compromise via phishing, lateral movement to PACS.
  seed: 42                             # top-level; per-actor seeds derive from this
  reproducible: true                   # default false; when true, unpinned refs error
  labels:                              # free-form k/v for filtering
    domain: healthcare
  includes:                            # optional; strict-no-override
    - ./fragments/std-dmz.yaml

layers:
  infra:
    networks:
      - { id: corp, cidr: 10.10.0.0/24, vlan: 10, gateway: 10.10.0.1 }
    nodes:
      - id: h-fin
        kind: host                     # host | router | switch
        net: corp                      # or `nets: [{net, ip}, ...]` if multi-homed
        image: win11-finance@v3        # version-pinned ref
        requires: [windows]            # capability taxonomy; see below
        resources: { cpu: 2, ram: 4Gi, disk: 60Gi }
        tap: true                      # whole-host tap (also: nets[].tap leg, networks[].tap segment)
  apps:
    - id: emr-app
      role: medical-records
      install:                          # closed set: pkg | config | role | container | script
        - container: hospital/emr@1.4
      listen:
        - { port: 8443, proto: tcp }
  activity:
    - id: act-fin-workday
      actor: user-fin                  # resolves to overlays.identity if present,
                                       # else implicit host-local user
      behavior: office-workday@v2      # library reference; alternatives are
                                       # `overrides:` (library + tweaks) or `inline:` (one-off)
      intensity: 0.6
      schedule: { start: 09:00, end: 17:00, days: [mon, tue, wed, thu, fri] }

bindings:                              # explicit; never `on:` shorthand
  - { app: emr-app, host: h-corp-emr }
  - { activity: act-fin-workday, host: h-fin }
  - { activity: act-attacker-recon, targets: [h-corp-dc, h-corp-emr] }

overlays:                              # optional per scenario
  identity:
    - { id: user-fin, type: user, domain: corp.local, groups: [finance] }
  data:
    - id: pii-records
      host: h-corp-emr
      path: /var/lib/emr/records
      classification: pii
      crown_jewel: true
  defense:
    - id: suricata-perimeter
      tool: suricata
      host: h-dmz-ids
      observes:
        - { net: dmz, mode: ids }     # network mode
      ruleset: emerging-pro@v2026.05
      tap_from: bridge                 # bridge | per-vm | external-mirror
    - id: edr-corp-fleet
      tool: crowdstrike-falcon
      installed_on: [h-fin, h-corp-emr, h-corp-dc]   # host-agent mode
```

Key structural rules (all ratified):

- **Single file + includes**, strict-no-override.
- **Explicit `bindings:`** — not `on:` inline.
- **Human string IDs** with optional `aliases:` for rename continuity.
- **Two version fields:** `scenario_schema_version` (the IR spec, monotonic int) and `scenario.version` (per-scenario monotonic int) bump independently.
- **Routers and switches are nodes** with `kind: router` / `kind: switch`; one uniform iteration target for validator, planner, adapter.
- **`install:` is a closed action set** (`pkg`, `pkg@version`, `config`, `role`, `container`, `script`) executed at first-boot only via Ansible. Not a runtime configuration channel. **v1 delivery channel = QGA-mediated** (ratified 2026-06-02): the orchestrator pushes + runs the rendered playbook in the guest over the qemu-guest-agent exec channel, because isolated scenario SDN networks defeat both push-SSH and the originally-intended *pull-mode* Ansible (same reachability wall `wait-os-ready` hit). See `docs/PLANNER.md` §4.3 + `docs/SLICE_CONFIG.md`.
- **`reproducible: true`** promotes unpinned image refs, unpinned behavior refs, and missing `seed:` from warnings to errors.
- **Activity DSL has three forms:** library reference (dominant), library + `overrides:`, or pure `inline:`.
- **Defense overlays bind via `observes:` (network) or `installed_on:` (host-agent), or both.**

### Capability taxonomy

**Ratified.** See `docs/IR_SCHEMA.md` for per-keyword semantics.

The `requires:` field on a node is a flat list of prefixed keywords drawn from a closed v1 set of sixteen:

| Category | Prefix | Keywords | Dispatch |
|---|---|---|---|
| OS family | (bare; exactly one per node) | `windows`, `linux`, `bsd` | validator → image catalog |
| Host | `host:*` | `host:nested-virt`, `host:tpm`, `host:secure-boot`, `host:uefi`, `host:gpu`, `host:cpu-aes-ni`, `host:hugepages` | planner → live Proxmox cluster |
| Kernel/runtime | `kernel:*` | `kernel:edr-hooks`, `kernel:ebpf`, `kernel:audit`, `kernel:container-runtime`, `kernel:dotnet-clr`, `kernel:powershell-5plus` | validator → image `provides:` |

Rules:

- **Exactly one OS-family keyword per node** (validator-enforced).
- **No implicit inheritance** — `windows` does not imply `host:uefi`. Every required capability is named explicitly.
- The set is **strictly closed for v1**. Adding a 17th keyword requires a CHANGELOG entry and schema-version review.
- Unknown keywords are validator **errors**, not warnings.
- Image manifests declare `provides:` from the same keyword set; the validator's check is image-`provides:` ⊇ node-`requires:`.
- **`requires:` is yes/no facts.** `resources:` is amount-requests (CPU, RAM, disk). They do not overlap.

This taxonomy replaces the earlier ad-hoc usage (`requires: kernel`, etc.) that appeared in pre-ratification sketches.

### Image manifests and bindings

**Ratified.** See `docs/IMAGE_MANIFEST.md` for the full field-level spec. Summary here.

Every image is described by **two files**: a platform-independent manifest and one per-platform binding. v1 ships Proxmox bindings only; the structure exists so AWS/Azure bindings slot in later without touching the manifest schema.

```
images/
  manifests/
    win11-finance.yaml        # one file per logical image; all versions inline
    ubuntu-server-22.yaml
    ...
  bindings/
    proxmox/
      win11-finance.yaml      # per-image, per-platform binding
      ...
    aws/                       # future
```

**Manifest** (platform-independent) declares the image's identity, OS family, and per-version `provides:` (using the 16-keyword taxonomy), `install_support:` (which `install:` action kinds the image accepts), `first_boot_budget` (seconds, planner hint), and `size_estimate`. Versions have a three-state lifecycle: `current` → `deprecated` (validator warning) → `withdrawn` (validator error with replacement pointer). Deprecated versions remain in the manifest so older IRs can still validate for reproducibility.

**Binding** (Proxmox-specific) declares per-version Proxmox specifics: `storage` (template volid, vmid, node constraints), `clone_mode` (linked | full), `cloud_init` (default user, sysprep flag for Windows), `hardware` (machine type, BIOS, NIC/disk models, TPM), `placement_constraints.requires_host_caps:` (which `host:*` keywords the deployed image needs from the physical node), and `clone_estimate` (seconds, planner hint).

**Consumer split:**

- **Validator** reads the manifest. Confirms version exists and is not `withdrawn`. Checks `provides:` ⊇ node-`requires:`. Cross-checks `install:` actions against `install_support:`.
- **Planner** reads the binding. Takes the **union** of IR-node `host:*` requirements and `placement_constraints.requires_host_caps:`. Filters live Proxmox nodes by that union; picks one.
- **Adapter** reads the binding. Clones the indicated `template_vmid` on the selected node, configures hardware per the binding, pushes cloud-init.

The union model means IR authors may omit obvious host requirements (`host:uefi` is implied by a modern Windows image's binding); the binding fills them in. Authors can still over-declare for clarity. Either source is authoritative; both contribute.

### Validator

**Conceptually ratified; specific checks open.**

Runs before any deploy. Catches an estimated 80% of deploy failures before any VM is created.

- Schema match against versioned IR spec.
- Cross-layer binding: every app references an existing host; every activity actor has somewhere to run; every defense tool can actually see the network it monitors.
- Capacity: sum of CPU/RAM/disk fits the target cluster with headroom.
- Image availability: every referenced golden-image template exists (or can be built via Packer).
- Network sanity: no CIDR overlaps with existing scenario zones on the same cluster; VLAN IDs available.

### Planner: DAG, not list

**Ratified.** See `docs/PLANNER.md` for the full formal spec; summary here.

The planner consumes a validated IR + image manifests/bindings + a Proxmox cluster snapshot, and produces a **DAG of operations** plus a **placement decision** for each scenario node. It then executes the DAG, honoring retry, escalation, and halt policies, recording per-op state to the state store.

Key properties:

- **Fine-grained ops** — one op per externally-observable side effect (~150–250 ops for a typical 8-host scenario). Three op kinds: `action`, `wait`, `barrier`. Synthetic `*-ready` barriers absorb cross-entity dependencies.
- **Deterministic DAG construction** — three-pass build (emit ops + barriers; data-flow edges from typed I/O; ordering edges from `after:` annotations + IR bindings). Same IR + same manifests + same cluster snapshot → same DAG, hashable.
- **Two-layer idempotency contract** — `infra` ops use Model C (deterministic naming + check-then-act + cleanup-on-partial); `config` ops use Ansible-native idempotency. External contract: every action op is idempotent on retry.
- **Failure semantics with operator overrides** — default 3 retries with exponential backoff, auto-escalate-once with halt-on-second-escalate, halt scope = failing subtree + transitive dependents. All overridable via CLI flags at deploy time.
- **Ephemeral process per deploy** — `axrange deploy <scenario.yaml>` runs to completion or terminal failure, then exits. `--detach` for long deploys; state store is authoritative; per-scenario lease/lock prevents concurrent invocations.
- **Semaphore-per-resource concurrency** — categories `proxmox-cluster`, `proxmox-node:<name>`, `ovs:<name>`, `vm:<vmid>`, `ansible-pool`, plus a global `max-ops-in-flight` cap.
- **Plan-diff re-planning** — every redeploy computes a diff against the prior state-store record; per-entity change classification (Unchanged / Mutated / Added / Removed) drives the redo; default behavior shows the diff and requires operator confirmation. Removals gated behind `--allow-destroy`.

Partial deploys are a first-class state. The state store records per-op status; re-running `axrange deploy` resumes from where the previous attempt left off — `completed` ops are fast no-ops (idempotency contract), `failed` and `halted-due-to-upstream` ops re-run.

---

## §3. Proxmox adapter and the mirror plane

### Workload classification

**Ratified.**

The Proxmox adapter classifies each IR node:
- Windows / kernel-sensitive / EDR-target → full VM (QEMU/KVM).
- Stateless Linux services (nginx, vulnerable apps, sensors) → LXC.

Workload requirements (drawn from the closed 16-keyword capability taxonomy — see §2) live in the IR as facts on each node's `requires:` list, **not inferred by the adapter from image names**. Otherwise the same scenario behaves differently on different platforms and the "deploy anywhere" promise breaks silently.

### SDN: Zone, vnets, OVS bridge

**Ratified.**

Each scenario gets its own **Proxmox SDN Zone**, whose type is **auto-derived (ratified 2026-06-03, CHANGELOG; OP-NET-010)**:

- **Simple zone** when no network declares a VLAN — each network gets **its own OVS bridge** (vnet = bridge), isolated at L2 by separate bridges.
- **VLAN zone** when networks declare VLAN tags — **one underlay OVS bridge per zone**, with networks as VLAN-tagged vnets on it (models real DC VLAN structure and its attack surface).

Inside the zone:

- One or more **production vnets** (corp, dmz, ot, mgmt).
- **OVS bridge(s)** backing those vnets — one per network (Simple) or one per zone (VLAN), per the above.
- A separate **vnet-transport** (cluster-level, not per-scenario) carries GRE tunnel traffic to the sensor(s) (§4).

These are defined in `/cluster/sdn` and committed by a cluster-wide apply (the slice-MVP direct-`/nodes/<node>/network` bypass was retired 2026-06-03; `docs/ADAPTER.md` §8).

**Critical adapter behavior (revised 2026-06-04 — Option 1, see CHANGELOG):** PVE SDN materializes every vnet — Simple *and* VLAN — as a **Linux** (VLAN-aware) bridge that holds the VM taps (for VLAN zones, linked to the OVS underlay via an internal `ln_<vnet>` port). Capture therefore does **not** depend on the scenario bridge being an OVS bridge: the mirror is taken with `tc` directly on the VM tap, wherever PVE places it (see "Per-tap tc mirrors" below). The earlier "every scenario bridge MUST be an OVS bridge" rule — true only while mirroring used `ovs-vsctl` — is **retired**; the live-verify finding (2026-06-03) confirmed it could not hold under SDN-native.

**SDN zone type:** Simple or VLAN. NOT VXLAN/EVPN — those encapsulate frames in UDP at the zone, complicating mirror fidelity; v1 stays on Simple/VLAN.

### Per-tap tc mirrors

**Ratified 2026-06-04 (Option 1, replaces the per-VM OVS mirror — CHANGELOG). Tap-point granularity extended 2026-06-19 (dynamic taps — `docs/design/06-taps-sensors.md`).**

Default capture mode: one `tc` mirror per tapped VM NIC, taken on the VM's tap interface (which PVE SDN places on the Linux vnet bridge). Because the mirror sits on the tap, it captures the VM's traffic in **both** directions and **before** the bridge switches it — so east-west traffic between two VMs on the same vnet is captured (the OVS-underlay approach missed this).

**Three tap granularities (dynamic taps, 2026-06-19).** `tap: true` is declarable at three IR locations, all realized by the *same* per-tap `tc` mechanism: `nodes[].tap` (whole host — every NIC), `nodes[].nets[].tap` (one leg of a multi-homed node — a router link or a dual-homed NIC), and `networks[].tap` (a whole segment). The planner resolves these into a deduped **tapped-interface set** keyed `(host, net, nic_index)` and drives mirror emission from it. **A whole-segment tap expands to per-VM tap mirrors, not a single bridge-level mirror** — a bridge-wide hook would miss same-bridge east-west traffic, the common case; per-VM expansion preserves the east-west guarantee above. Router/link taps are realized host-side on the router VM's tap (**not** an in-guest VyOS mirror — see the dtDataCenter landmines in the design brief §8).

Two side effects, emitted by two different ops (implementation 2026-06-04):

```bash
# 1) attach-gre-tunnel (OP-MIR-010), once per (bridge, sensor) at DEPLOY time —
#    create the keyed gretap netdev the mirror targets:
ip link add gt_${BRIDGE}_${SENSOR} type gretap remote <sensor-ip-on-vnet-transport> key <bridge-sensor-key>
ip link set gt_${BRIDGE}_${SENSOR} mtu 1500 up             # MTU per the GRE math below

# 2) the per-VM post-start hook (installed by install-ovs-mirror, OP-MIR-020),
#    on EVERY boot — a clsact qdisc + matchall mirred on the tap, both
#    directions, into that gretap:
tc qdisc  add dev tap${VMID}i0 clsact
tc filter add dev tap${VMID}i0 ingress matchall action mirred egress mirror dev gt_${BRIDGE}_${SENSOR}
tc filter add dev tap${VMID}i0 egress  matchall action mirred egress mirror dev gt_${BRIDGE}_${SENSOR}
```

Why per-tap, not bridge-wide:
- Granular control — tap any subset, not the whole bridge.
- Add/remove as the scenario evolves (red team pivots → tap the new host).
- Captures east-west (mirrors at the tap, ahead of bridge switching).

The keyed gretap + flow-based sensor model is unchanged from the multi-tunnel / per-tap-sensor-routing work (§4): the per-(bridge, sensor) key now lives on the gretap netdev, and each sensor decaps any key.

### The post-start hook is mandatory

**Ratified.**

A VM's tap interface (`tap<vmid>i0`) is recreated on every VM boot. A mirror referencing the old tap silently captures nothing after a reboot. So:

- The adapter installs the mirror as a **Proxmox VM hook script** that runs on `post-start` (install the `tc` clsact + mirred rules on the tap, into the gretap) and `pre-stop` (delete the clsact qdisc). The keyed gretap netdev itself is created once per (bridge, sensor) at deploy time by `attach-gre-tunnel`, **not** by the hook.
- The hook reads the deploy plan to know what taps this VM should have.
- The tap-side `tc` rules are **stateless and self-healing** — rebuilt on every VM start (the tap is recreated each boot), surviving VM reboots, snapshot restores, and scenario pauses. The hook self-checks (`tc filter show … | grep -q mirred`) so a failed install surfaces in the Proxmox task log instead of silently capturing nothing — the failure mode the 2026-06-03 finding hit.
- **Node-reboot caveat (implementation 2026-06-04):** unlike the retired OVS GRE port (which OVSDB persisted), an `ip link` gretap netdev does **not** survive a Proxmox **node** reboot. A node reboot is healed by a resume-safe `axrange deploy` re-run (it re-creates the gretap via `attach-gre-tunnel`); a boot-time unit to recreate gretaps is a possible follow-up.

### Deploy-time capture heartbeat (verify-capture)

**Ratified 2026-06-19 (dynamic taps — `docs/design/06-taps-sensors.md`).**

The hook's self-check (above) proves the `tc` filter *bound*, but not that a frame actually traverses tap → gretap → sensor. The worst, already-realized failure mode was a *silent* one: 25/25 ops COMPLETED with zero frames at the sensor (CHANGELOG 2026-06-03, the SDN-native per-VM-OVS-mirror break). To close it, a new op **`verify-capture` (OP-MIR-040)** makes capture-liveness a **structural property of the DAG**:

- One instance per `(bridge, sensor)` that has ≥1 tap. It **injects** a uniquely-keyed sentinel (an ICMP echo whose payload encodes `deploy_id+bridge`) over the QGA channel from a tapped, QGA-capable Linux guest on the bridge (the *injector*), and **confirms** it at the sensor by reading the sensor's newest capture over the mgmt NIC (a short `tshark`/`tcpdump` sentinel filter), within a timeout. No change to the sensor's black-box contract — it reads existing pcap output, adds no sensor interface.
- It **feeds `mirror-ready[$sensor]`**, which `start-activity` already gates on — so activities cannot generate traffic until capture is *proven* live end-to-end. `mirror-ready` thus upgrades from "mirror commands were issued" to "a sentinel provably reached the sensor."
- If a bridge's only taps are VyOS router legs (no QGA injector), the heartbeat is **skipped with a loud warning** — the leg is still mirrored host-side, just not deploy-verified. The *ongoing* periodic per-tap frame counter (continuous silent-drop detection) is deferred; this is the deploy-time gate only.

### Cross-host mirroring = keyed gretap = functional ERSPAN

**Ratified (mechanism updated 2026-06-04 — Option 1).**

When the source VM and the sensor are on different Proxmox nodes, the mirror must egress over a tunnel. Under Option 1 the `tc` mirror's target **is** a standalone keyed **gretap netdev** (shown in "Per-tap tc mirrors" above), not an OVS GRE port:

```bash
# Per (bridge, sensor): a uniquely-named, keyed gretap netdev. The key is required —
# two unkeyed gretaps to the same sensor collide at the datapath (surfaced 2026-06-01;
# see §4 + CHANGELOG). The adapter derives the name + key per (bridge, sensor) via
# gre_port_for_bridge_sensor() / gre_key_for_bridge_sensor().
ip link add gt_<bridge>_<sensor> type gretap remote <sensor-ip-on-vnet-transport> key <bridge-sensor-key>
# The tc mirred filters on each tap point at this device (see above).
```

This is functionally ERSPAN type II — same wire format, same role, orchestrated by the adapter rather than Cisco gear. The sensor terminates on a single **flow-based** gretap (`ip link add … type gretap external`) that decapsulates **any** key, so multiple bridges' keyed tunnels coexist into one merged capture (see §4 and `o2/tcpdump-sensor/README.md`). Whether the scenario VLAN tag survives a tap-level `tc` mirror is confirmed at live-verify (CHANGELOG 2026-06-04); it does not affect v1's merged, no-correlation capture either way.

### MTU math

**Ratified.**

A mirrored full-size frame encapsulates to 20 (outer IPv4) + 8 (GRE + key) + 14 (inner Ethernet)
+ 1500 = **1542 B, 1546 B with an 802.1Q tag** (corrected 2026-09-28; this section said "GRE adds 24
bytes", which forgot the inner Ethernet header and the key). Options:
1. **Preferred:** bump `vnet-transport` MTU (and the sensor's transport NIC) to ≥ 1546, e.g. 1550. Clean fix because nothing else uses that vnet. Not yet implemented — `docs/PRODUCT_GAPS.md` GAP-112.
2. Avoid: lower scenario MTU to 1476 — affects attack behavior, some malware probes MTU.

**Floor (built 2026-09-28):** every mirror gretap is created `nopmtudisc ignore-df`, so the outer
header never carries DF and an encapsulation larger than the transport MTU is **fragmented**, then
reassembled by the sensor's kernel before GRE decap. With the kernel default (outer DF) such frames
were dropped silently — `tx_errors` for inner-DF IPv4, `tx_dropped` (host `IpFragFails`) for tagged
or non-DF frames (CHANGELOG 2026-09-28).

### Image management

**Ratified at conceptual level.** Manifest/binding details: see §2 and `docs/IMAGE_MANIFEST.md`.

- Golden images are Proxmox templates, built once with Packer.
- The adapter's "image resolver" maps logical IR names (`win11-finance@v3`) to template VMIDs via the per-image Proxmox **binding** file in `images/bindings/proxmox/`.
- Linked clones are fast (seconds), so per-scenario VM creation is cheap. The binding declares `clone_mode: linked | full` per version.
- Missing template → adapter triggers a Packer build (out of v1 scope; document the hook point). When this happens, the manifest's `provides:` field is the contract the build must satisfy.

---

## §4. Dedicated transport vnet

### What it is

**Ratified.**

A cluster-level isolated vnet — `vnet-transport`, e.g. `172.30.0.0/24` — that carries **GRE outer packets only**. It is not a scenario vnet and never carries scenario traffic directly.

### Routing

Each scenario zone has an OVS GRE port whose `remote_ip` is the sensor's address on `vnet-transport` (e.g. `172.30.0.10`). On the underlay, the encapsulated traffic looks like ordinary IP between the zone's tunnel endpoint and the sensor.

### Properties

- **Isolation from scenario traffic** — scenario hosts cannot reach `172.30.0.0/24`; the sensor is invisible to them.
- **No accidental mixing** — the sensor can't be on a scenario vnet, so there's no way to capture its own GRE-encapsulated copies and loop.
- **Independent capacity** — mirror traffic sometimes equals or exceeds production traffic; sizing it separately makes the math sane.
- **Multi-tenant** — all zones tunnel over the same vnet-transport; adding a new zone is one GRE tunnel. **Multiple sensors (2026-06-03, per-tap sensor routing):** a deploy may route different tap points to different sensors (`docs/IR_SCHEMA.md` §6 `sensors:` + tap→sensor bindings). Multiple sensors are simply multiple endpoints on this one transport vnet — each its own IP, each a flow-based gretap. A bridge emits one keyed GRE tunnel per distinct target sensor among its taps (per-(bridge, sensor) key). Each sensor remains an independent merged black box; the no-scenario-correlation boundary holds per sensor.
- **Cloud-portable** — maps directly to AWS VPC Traffic Mirroring target subnets, Azure vTAP. When the cloud adapter arrives, the design doesn't change.

### Sensor NIC layout

**Ratified.**

- `eth0`: on `vnet-mgmt`. SSH, metrics scrape, log shipping. Reachable by the operator only.
- `eth1`: on `vnet-transport`. Promiscuous. The only way mirrored traffic reaches the sensor.
- **No interface on any scenario vnet.** Ever.

---

## §5. Capture plane (v1 default: tcpdump-sensor black box)

> **Placement (§0):** the tcpdump-sensor is a **standing `o2_` component** (`o2/tcpdump-sensor/`),
> stood up with the O2 surface — not per-scenario. The black-box contract + mechanics below are
> unchanged by that; only its lifecycle/owner moved (engine → O2 control).

### Black-box contract

**Ratified.**

The tcpdump-sensor exposes a tiny surface:
- **Inputs:** the capture NIC; a config file at `/etc/capture/capture.conf`.
- **Outputs:** a directory of rotating pcap files at `/captures/`.
- **No other interfaces.** No API, no DB, no shared filesystem mounts to scenario hosts.

This contract is what lets us replace tcpdump with `netsniff-ng` or PF_RING later without touching anything upstream.

### Default implementation

**Ratified.**

Long-running `tcpdump` under systemd, with these flags:

```bash
tcpdump \
  -i <capture-iface> \                # gretap-decapsulated interface
  -p \                                # don't toggle promisc (set once by udev)
  -n \                                # no DNS lookups
  --immediate-mode \                  # don't batch kernel→userspace
  -B 524288 \                         # 512MB kernel ring buffer
  -s 0 \                              # full snaplen (configurable via SNAPLEN)
  -G ${ROTATE_SECONDS:-300} \         # rotate every 5 min
  -C ${ROTATE_MEGABYTES:-1024} \      # OR every 1 GB
  -W 0 \                              # unlimited file count (watchdog handles retention)
  -z "zstd -1" \                      # post-rotate compress
  -Z capture \                        # drop privileges after open
  -w /captures/cap-%Y%m%d-%H%M%S.pcap \
  ${EXCLUDE_BPF}
```

### The `-W 0` rule

**Ratified — critical.**

`tcpdump -G ... -W <n>` does NOT mean "keep the latest N files." It activates a numeric-counter filename mode that overwrites files in a ring and ignores `%`-strftime in the filename pattern. So:

- Always use `-W 0` to keep the strftime template active.
- Retention is the **watchdog's** responsibility, not tcpdump's.

This is the single most common tcpdump-rotation mistake. Document it loudly.

### Drop privileges

**Ratified.**

`-Z capture` drops the process to a non-root `capture` user after opening the NIC. Pcaps are owned by `capture`. A compromised tcpdump can't easily pivot.

### Storage layout

**Ratified.**

```
/captures/
├── 2026/
│   └── 05/
│       └── 20/
│           ├── zone-a/
│           │   ├── cap-114703.pcap.zst
│           │   └── cap-115203.pcap.zst
│           └── zone-b/
│               └── ...
```

Path is `{YYYY}/{MM}/{DD}/{tunnel-source}/cap-{HHMMSS}.pcap.zst`.

- Year/month/day directories — trivial pruning by date.
- `tunnel-source` directory derived from which gretap interface received the traffic. **v1 capture transport (updated 2026-06-01; generalized 2026-06-03): a flow-based gretap per sensor + a per-(bridge, sensor) GRE key on each tunnel.** Two unkeyed (`key=0`) GRE tunnels to one sensor collide at the OVS datapath, so each tunnel carries a distinct key (`gre_key_for_bridge_sensor`) and each target sensor terminates on its own flow-based gretap (`type gretap external`) that decapsulates any key into one merged stream. **Per-tap sensor routing (2026-06-03):** a deploy may route different taps to different sensors, so one bridge can fan tunnels out to several sensors and many bridges can converge on one (`docs/IR_SCHEMA.md` §6 `sensors:` + tap→sensor bindings). This **supersedes** the original 2026-05-27 "one gretap per zone" model, which never addressed the sender-side collision (see CHANGELOG 2026-06-01 / 2026-06-03). **Per-scenario attribution stays deferred** — capture is still a single time-ordered stream ("no scenario correlation in v1"); attributing frames to a scenario *by key* would add `OP-MIR-030 register-mirror-source` (ID reserved; `docs/OP_CATALOG.md` §5.3).
- Timestamp in filename — `ls` is the index.

The sensor does **not** know about scenarios. Discrimination by run is the user's problem at query time, via time-range filtering. See CHANGELOG entry 2026-05-21 "Defer scenario-state correlation in capture pipeline".

### Disk watchdog

**Ratified.**

Separate systemd timer, runs every 60s.

- **Soft threshold:** 80%. Start pruning oldest pcaps until disk drops to TARGET (70%).
- **Hard threshold:** 92%. Alert (and optionally stop capture per config).
- **MIN_KEEP:** never delete the newest N files (default 10). Don't eat the active write target.
- Prunes oldest-first via `find -printf '%T@ %p\n' | sort -n`.

Two thresholds (not one) prevent ping-ponging. Hard threshold tells the operator the capture rate exceeds the retention policy — a planning failure, not just a disk-full event.

### Tuning ladder (apply in order, stop when drops are zero)

**Ratified.**

1. NIC RX ring depth: `ethtool -G eth1 rx <max>`.
2. NIC offloads OFF for capture: `ethtool -K eth1 gro off lro off tso off gso off`. (Offloads coalesce frames, making pcaps lie about wire reality.)
3. Pin tcpdump to a CPU core distinct from NIC IRQ cores.
4. Kernel ring buffer (`-B`) to 4× your worst-expected burst.
5. Bump `net.core.rmem_max`, `rmem_default`, `netdev_max_backlog` sysctls.
6. Filesystem: XFS or ext4 with `noatime,nodiratime`. **Avoid btrfs/zfs** for the hot tier — CoW kills sustained sequential writes.
7. Past 1–2 Gbps tuned: graduate to `netsniff-ng` (TPACKET_V3). Past 5 Gbps: PF_RING ZC or AF_XDP.

### Health signals (must monitor)

**Ratified.**

- Kernel drops on rotation log (`tcpdump` prints `N captured, M dropped by kernel`).
- NIC RX errors via `ethtool -S eth1`.
- Disk write throughput via `iostat`.
- Capture continuity (no gaps between adjacent files — alert on >50ms gap).
- Disk fill rate (watchdog should oscillate between SOFT and TARGET).
- tcpdump process state + freshness of the newest pcap (mtime within last 30s).

### Failure modes the design must catch

**Ratified.**

| Failure | Symptom | Caught by |
|---|---|---|
| Silent kernel drops | tcpdump runs, missing packets | drop counter on rotation log |
| Writer stall | process exists, no new bytes | newest-pcap mtime check |
| Compressor backpressure | uncompressed `.pcap` piling up | count of `.pcap` vs `.pcap.zst` |
| Watchdog too aggressive | captures only go back N min despite disk free | oldest-pcap age check |
| Watchdog too lenient | disk crosses HARD threshold | df-based alert |
| NIC offloads re-enabled | TCP segments > MTU in pcap | periodic `ethtool -k` check |
| GRE MTU mismatch | fragmentation, drops at tunnel | `ip -s link` counters on gretap |
| Promisc disabled | sensor sees only its own MAC | `ip link show` PROMISC flag |

### Capacity reference

**Ratified as planning guidance.**

| Stack | Sustainable | Bottleneck |
|---|---|---|
| `tcpdump` AF_PACKETv2 defaults | ~300 Mbps | Per-packet kernel copy |
| `tcpdump` tuned (above ladder) | ~1–2 Gbps | Single-thread libpcap |
| `netsniff-ng` TPACKET_V3 | ~3–5 Gbps | Disk write |
| PF_RING ZC | ~10 Gbps | NIC support, license |
| DPDK / AF_XDP custom | ~40 Gbps+ | Storage |

Storage math: 1 Gbps sustained = ~125 MB/s = ~10 TB/day raw, ~3–5 TB/day with zstd. Always size storage from sustained rate × retention window.

---

## §6. AI core (sketched, not implemented)

**Conceptually ratified; implementation deferred to a later iteration.**

Five roles, each with a different prompt, different tools, different evaluation signal:

| Role | Purpose | Inputs | Outputs |
|---|---|---|---|
| **Scenario planner** | Generate scenarios from a natural-language brief | Brief, MITRE/NIST RAG | IR YAML |
| **Red-team agent** | Adaptive attack execution | IR, current range state, ATT&CK | Caldera/Atomic ops, observations |
| **Blue-team agent** | Defense synthesis | Telemetry, defense overlay state | Sigma/Suricata/YARA rules, config changes |
| **Telemetry analyst** | Hunt + summarize | PCAP slices, logs, alerts | Timeline, anomaly report, AAR |
| **Traffic generator** | Benign baseline activity | Scenario, host roles | Realistic flows so attacks have somewhere to hide |

Specifics (model selection, agent loop shape, RAG ingestion) are open until the platform's first complete scenario runs end-to-end. The capture plane is enough to start producing the telemetry these agents will consume.

**How these map today (2026-07):** the roles are partly realized as **non-AI** mechanisms, with the AI *automation* still the future layer:
- **Scenario planner** → `scenario_ai_research/` (Stage-1 OSS corpus ingestion + Stage-2 Claude-Code design → the `target-environments/` archetypes).
- **Red / Blue** → A3 (`docs/design/08-exercise-measurement.md`): the **Caldera** (red) + **Wazuh** (blue) servers in O2, agents baked in the substrate — **operator-driven / manual** now; AI-driven playout is the future automation.
- **Telemetry analyst** → A4 (`docs/design/08-exercise-measurement.md`): ATT&CK **coverage** scoring over the O2 telemetry.
- **Traffic generator** → the `activity` engine (benign behavior + seeded schedule, `docs/SLICE_ACTIVITY.md`).

---

## §7. What's NOT in v1

**Explicit non-goals, ratified** (updated 2026-07 — several original non-goals have since shipped; struck below):

- Scenario correlation in the capture plane (pcaps are time + tunnel-source only). *(still true — per-scenario attribution by GRE key is reserved, not built.)*
- VMware adapter, AWS adapter, Azure adapter — designed-for but not built. *(still true — Proxmox only.)*
- ~~Per-host EDR / endpoint telemetry~~ — **now in** (A3 `overlays.defense` Wazuh host agent + AD audit → the O2 Wazuh server; host + network both covered).
- ~~Database / message bus; filesystem-based~~ — **now** a SQLite **`state.db`** (deploys/ops/leases/resource_locks).
- **AI *automation*** (planner, red, blue, analyst, traffic-gen agents) — still not built; the roles exist as non-AI mechanisms (§6) with manual/scripted playout.
- ~~The canvas UI~~ — **now** the **axRange Console** (`o2/console/`, served by `axrange serve --spa-dir`): one run-time-first operator UI over the control-plane API — Range / Twin / Catalog / Design areas, per-host persona apply (`docs/design/16-unified-console.md`, built and live-verified 2026-09-30). The interim Grafana control-panel plugin and its topology-renderer bake-off (2026-06 → 2026-09) were superseded and removed 2026-10-01; Grafana remains telemetry-only. The free-hand layered canvas of §1 was never built (see the §1 status note).
- ~~Multi-sensor scaling~~ — **now in** (captures-driven multi-sensor fan-out, live-verified). Active-standby sensors: still out.
- Encrypted-at-rest pcaps. Out of scope for v1 (filesystem-level encryption is the operator's choice).
- **Automated red/blue playout syncing** and **AI-driven** attack/defense — the manual playout (A3) is v1; automation is future (`docs/design/08-exercise-measurement.md`).
