# Proxmox Adapter (v1)

Authoritative spec for the Proxmox adapter. Companion to `docs/PLANNER.md` (which imports the adapter as a library and defines op semantics), `docs/OP_CATALOG.md` (which defines *which* ops the adapter implements), and `docs/IMAGE_MANIFEST.md` (the per-platform binding the adapter executes from). Resolves the design portion of OPEN_QUESTIONS Q5; ratified in the CHANGELOG entry dated 2026-05-28 "Q5 resolved + planner-execution/adapter slice authorized".

**Status:** design ratified for v1; first implementation slice authorized (see §6). The transport split, auth model, and op→backend mapping below are the contract. Op-by-op REST/SSH call details are filled in during implementation and become CHANGELOG-able `implementation (Claude Code)` refinements.

---

## 1. What the adapter is

The adapter is the per-platform translator at stage 5 of the pipeline (`docs/ARCHITECTURE.md` §2). It is **a library the planner imports**, not a separate process or daemon (`docs/PLANNER.md` §6.1): it lives in the ephemeral planner process's address space and shares its state-store handle. The planner calls the adapter to execute each `infra`-class op instance; the adapter performs the external side effect against a real Proxmox cluster and returns the op's typed outputs.

The adapter knows nothing about scenarios, IR, AI, or capture semantics. Its inputs are: an op instance (op type + resolved inputs, per `docs/OP_CATALOG.md`) and the relevant image binding (`docs/IMAGE_MANIFEST.md`). Its outputs are the op's declared `outputs`. This is the same boundary discipline the sensor keeps: a narrow contract, replaceable internals.

## 2. Transport split — REST + SSH

**Resolved (Q5).** The adapter speaks two transports, chosen per op, behind one interface:

- **REST (Proxmox API)** — everything the API supports: SDN zone/vnet definition and apply, VM clone, VM config (hardware, cicustom drive), VM start, guest-agent ping.
- **SSH** — everything the API does **not** expose. Per `docs/ARCHITECTURE.md` §3, Proxmox SDN does not expose OVS port-mirror configuration through its REST API, so `ovs-vsctl` work is unavoidably SSH. The post-start/pre-stop hook-script install (`install-ovs-mirror`) and the GRE tunnel port add (`attach-gre-tunnel`) are SSH ops.

This split is **forced, not preferred** — the mirror plane has no API surface today. If a future Proxmox release exposes OVS mirrors via the API, those ops migrate from the SSH backend to the REST backend with no change to the op catalog or the planner.

### 2.1 One interface, two backends

The planner does not know or care which transport an op uses. The adapter exposes a single op-execution interface:

```
adapter.run(op_instance, binding, ctx) -> outputs   # raises AdapterError on failure
```

Internally the adapter routes each op type to a `rest` backend or an `ssh` backend. Both backends implement the same check-then-act contract (§4). The routing table is the op→backend column in §3.

## 3. Op → capability → backend mapping (v1 infra ops)

Every `infra`-class op in `docs/OP_CATALOG.md` maps to one adapter capability on one backend. `config`-class ops (Ansible-wrapped) are **not** the adapter's concern in the transport sense — they run through the Ansible execution path (`docs/PLANNER.md` §4.3) and are out of scope for the first slice (§6).

| Op (catalog ID) | Adapter capability | Backend |
|---|---|---|
| `create-sdn-zone` (OP-NET-010) | define SDN zone (Simple/VLAN, OVS) | REST |
| `create-ovs-bridge` (OP-NET-020) | define OVS-bridge-backed vnet substrate | REST |
| `create-vnet` (OP-NET-030) | define vnet on VLAN tag | REST |
| `apply-sdn-config` (OP-NET-040) | cluster-wide SDN reload/apply | REST || `clone-vm` (OP-HOST-010) | clone template (linked/full) | REST |
| `configure-vm-hardware` (OP-HOST-020) | set cpu/ram/disk/machine/bios/agent | REST |
| `configure-cloud-init` (OP-HOST-030) | render + attach cicustom drive | REST |
| `attach-vm-to-vnet` (OP-HOST-040) | set VM NIC → vnet | REST |
| `start-vm` (OP-HOST-060) | power on VM | REST |
| `wait-guest-agent` (OP-HOST-070) | guest-agent ping (poll) | REST |
| `wait-os-ready` (OP-HOST-080) | SSH/WinRM reachability probe (poll) | (probe; see note) |
| `attach-gre-tunnel` (OP-MIR-010) | create keyed gretap netdev (`ip link … type gretap`) + assert a deep-FIFO qdisc on it | SSH |
| `install-ovs-mirror` (OP-MIR-020) | write + register post-start/pre-stop hook (`tc` mirror rules) | SSH |

**Note on `wait-os-ready`.** It is a `wait` op that probes the *guest* (SSH for Linux, WinRM for Windows) from the orchestrator — it is neither a Proxmox-REST nor an `ovs-vsctl`-SSH call. It uses the orchestrator's own network reachability to the guest's management IP, not the adapter's Proxmox SSH credentials. Treat it as a third, trivial "probe" path.

**Note on the OP-NET capabilities (`create-sdn-zone`, `create-vnet`, `apply-sdn-config`).** The capability column describes the SDN-native model, which is **the v1 model** (ratified 2026-06-03, CHANGELOG; ADAPTER §8.3 route 1) and is now **implemented** (CHANGELOG 2026-06-03 "SDN-native OP-NET ops implemented"). Zone type is auto-derived per `OP-NET-010`: **Simple** zone when no network declares a VLAN (one OVS bridge per network — `create-ovs-bridge` once per network), **VLAN** zone when VLANs are declared (one underlay bridge per zone, networks as VLAN-vnets — `create-ovs-bridge` once per zone). `create-ovs-bridge` runs **before** `create-sdn-zone` so a VLAN zone can reference the existing underlay; the VM NIC attaches to the vnet bridge **without** a tag (the vnet carries it). The op→capability→backend *contract* (which op, which backend, `inputs`/`outputs`) is unchanged. The slice-MVP direct-bridge bypass that previously implemented these ops against `/nodes/<node>/network` is **retired** — see §8, kept as a historical record. SDN-native infra is **live-verified on `pxtwin1`** (2026-06-03); the per-VM OVS mirror it broke is resolved by re-mechanizing capture onto a per-tap `tc` mirror (**Option 1, ratified 2026-06-04** — CHANGELOG; `docs/ARCHITECTURE.md` §3), which captures on the SDN-materialized Linux vnet bridge **regardless of zone type** (the earlier "Simple-zone-only limitation / VLAN is OVS-clean" framing was wrong — it hit both). The `tc` mirror itself is live-verify-pending.

**Note on the gretap qdisc (2026-08-14, CHANGELOG).** `attach-gre-tunnel` also asserts
`txqueuelen N` + `tc qdisc replace … root pfifo limit N` on the netdev (N =
`MIRROR_GRETAP_TXQUEUELEN`, default 20000; `0` opts out). Without it the gretap inherits the
**node's default qdisc** — `fq_codel` on PVE 9.x, an AQM that *drops* to hold latency down, which is
right for a congested uplink and wrong for a capture path where the mirrored copy is
fire-and-forget and a drop is lost evidence. Measured on dev45: 9,349 frames lost in a 90 s window
at ~2.38 Gbit/s before, zero after. The assert lives in the op's **idempotent** section, not its
create branch, so an *adopted* pre-existing gretap (§2 check-then-act) heals on the next run rather
than keeping `fq_codel` forever. No contract change — same op, same backend, same inputs/outputs.

**Capture-plane note — the `te` deploy no longer calls `attach-gre-tunnel`/`install-ovs-mirror`
itself (2026-07-13, `docs/WORK_ORDER_sensor_agnostic_te.md`; updated 2026-07-28,
`docs/WORK_ORDER_tap_eligibility.md`).** The two capability rows above (`attach-gre-tunnel`
OP-MIR-010, `install-ovs-mirror` OP-MIR-020) are unchanged as *op→capability→backend* mappings —
both still route to the SSH backend exactly as shown — but a `te` deploy's own DAG never invokes
them: it emits **no OP-MIR op of any kind**. (`OP-MIR-005 record-tap-intent`, the declarative
bookkeeping op that briefly stood in for them from 2026-07-13, is itself REMOVED 2026-07-28 — a
`te` deploy's tap-emission surface is now empty, not a single declarative op.) The two SSH-backed
ops are invoked exclusively by the **A2 tap playout**
(`axrange/o2control/taps.py`) as a separate `tap-<te>` deploy against a te's already-running VMs, using
the SAME adapter/backends via the SAME executor (LES-037) — the adapter itself has no A2-specific code
path. Sensor identity (transport IP, vmid) for that call now comes from **A1's standing O2 sensor
inventory**, resolved by `o2control` at tap-apply time, never from `adapter.conf`. `AdapterConfig`
(`axrange/adapter_proxmox/config.py`) no longer carries `SENSOR_TRANSPORT_IP` /
`SENSOR_<ID>_TRANSPORT_IP` / `SENSOR_SSH_HOST` / `SENSOR_SSH_USER` / `SENSOR_SSH_KEY` /
`SENSOR_CAPTURE_IFACE` or the `sensors` map — `verify-capture` (OP-MIR-040) reads the sensor over QGA
via its `sensor_vmid` input only, with no SSH fallback (the `run_ssh_sensor` SSH-to-sensor helper is
deleted, not merely unreachable). `NETWORK_SENSOR_VMID` / `NETWORK_SENSOR_IFACE` (Option C, the
appliance-adjacent network sensor, `deploy-network-sensor` OP-OVL-060) are a **separate, unrelated**
mechanism and are unaffected — they still resolve from `adapter.conf`.

## 4. Idempotency contract (Model C for infra ops)

The adapter signs up to `docs/PLANNER.md` §4.2 Model C for every `infra` op:

1. **Deterministic naming.** Resource names derive from `{plan_id, op_id}` — the same op instance always targets the same Proxmox resource name / VMID. (VMIDs: the adapter derives a stable VMID per host op from `{plan_id, op_id}`; collision handling is an implementation detail of the slice.)
2. **Check-then-act on entry.** Before creating, the op checks whether *its own* prior attempt already created the resource (a VM with the deterministic name exists; the vnet is already defined; the keyed gretap netdev already exists). If so, it adopts the existing resource rather than erroring or duplicating.
3. **Cleanup-on-partial before redo.** If a prior attempt left a half-built resource (cloned VM with no NIC; bridge with no gre port), the redo path cleans the partial state first, then re-creates.

All v1 adapter ops use `cleanup_strategy: redo` — none of the slice ops are `escalate` (the only `escalate` op in the catalog is `run-script`, which is `config`-class and out of slice scope).

**Deterministic naming — tier-tagged (`docs/design/05-o2-surface.md`, `docs/WORK_ORDER_resource_tags.md`; implemented 2026-07-08).** Single source of truth is `axrange/adapter_proxmox/naming.py`. Every produced name carries a literal blast-radius tier-tag prefix so `destroy` can sweep by tag instead of by shape-matching alone:

| Resource | Producer | Shape | Tier |
|---|---|---|---|
| SDN zone | `zone_id_for_plan` | `te<6hex>` (8 chars) | `te` |
| SDN vnet | `vnet_id_for_net` | `tev<5hex>` (8 chars) | `te` |
| underlay/per-net OVS bridge | `underlay_bridge_for_plan` / `bridge_name_for_net` | `tebr_<6hex>` / `tebr_<sanitized-id>` (≤15 chars) | `te` |
| VM display name | `vm_name_for_host` | `te-<6hex>-<host>` (≤63 chars) | `te` |
| VM VMID | `vmid_for_op` | 100000–999999 (secondary guard; doubles as the `img`-template exclusion range) | `te` |
| SDN zone (standing) | `zone_id_for_plan(..., tier="o2")` | `o2z<5hex>` (8 chars) | `o2` |
| source gretap netdev | `gre_port_for_bridge` / `gre_port_for_bridge_sensor` | `tapg_<6hex>` (≤15 chars) | `tap` |
| per-tap OVS mirror | `mirror_name_for_vmid` | `tapm-<vmid>` | `tap` |
| standing O2 vnet/bridge/VM | `o2_vnet_id_for_component` / `o2_bridge_name_for_component` / `o2_vm_name_for_component` (called via `tier="o2"`) | `o2v<5hex>` / `o2br_<6hex>` / `o2-<slug>` — **now called by `axrange/o2control/`'s standing-surface deploy (A1, BUILT 2026-07-11, `docs/design/05-o2-surface.md`)** | `o2` |
| golden template VM name | `img_vm_name_for_image` (also used by `imagebuild/builder.py`) | `img-<image-slug>` (≤63 chars), e.g. `img-debian-12-cloud` | `img` |

Mutual exclusivity across tiers is structural (fully-anchored regexes, disjoint prefixes/positions — see the `naming.py` module docstring). `planner/teardown.py` imports `is_te_*`/`is_tap_*` as the positive shape-match for what a `te` destroy may touch, and `is_o2`/`is_img` as a hard **protect guard** checked first (refuse + record a `guard-violation` on a match) so a `te`/`tap` destroy structurally cannot remove a standing O2 surface resource or a golden template even if a positive shape-match ever had a bug — **except an `o2`-tiered destroy against its own `o2_*` resources** (`axrange o2 down`), which the guard deliberately does not block. This retires the earlier `axr`-prefix / `is_axr_*` scheme (CHANGELOG 2026-07-08 "resource ownership tags: implementation").

**`tier` as a deploy parameter (A1, `docs/design/05-o2-surface.md`, CHANGELOG 2026-07-11).** Every `naming.py` producer above takes `tier: str = "te"` (`_VALID_TIERS = ("te", "o2")`); it is threaded from `Plan.tier` via `ExecutionContext.tier` through plan/deploy/naming/state/executor/teardown — the adapter itself stays tier-agnostic (it names resources through these producers, never hardcodes a prefix). `state.py`'s `deploys` table gained a `tier` column (`SCHEMA_VERSION` 2→3) so a deploy's tier is a durable, queryable attribute, not just an emission-time naming choice. **Operator action:** re-initialize (delete/move) any `state.db` that predates this change — it fails loud (`StateStoreSchemaError`), same guard pattern as the earlier v1→v2 migration.

## 5. Auth model

**Resolved (Q5), v1:**

- **REST:** a Proxmox **API token** with a limited role scoped to the operations in §3 (SDN, VM lifecycle, VM config). Not a root password; not a full-admin token.
- **SSH:** a dedicated **SSH key** for an `ovs-operator` account on each Proxmox node, used only for the `ovs-vsctl` ops and hook-script install.
- **Where credentials live:** on the orchestrator host, in an adapter config file (e.g. `/etc/axrange/adapter.conf`) or environment, read by the planner process at deploy time. Credentials are **never** in the IR, never in a scenario file, never in the state store.

**Explicitly out of v1 scope (flagged, not designed):** credential rotation, secret-manager integration, per-scenario credential scoping. These are CHANGELOG-able additions when operational need arises.

## 6. First implementation slice (authorized 2026-05-28)

The first slice is the **smallest end-to-end thread that demonstrates tap + capture**: a hand-written IR with two tapped Linux hosts on one network, validated, deployed to a single Proxmox node, with mirrored traffic reaching the sensor. The operator generates traffic manually (`ping`/`iperf` between the two hosts) and confirms pcaps land in `/captures/`.

**Ops in the slice (16 of 31):**

- **OP-NET (all 4):** `create-sdn-zone`, `create-ovs-bridge`, `create-vnet`, `apply-sdn-config`.
- **OP-HOST (7 of 8):** `clone-vm`, `configure-vm-hardware`, `configure-cloud-init`, `attach-vm-to-vnet`, `start-vm`, `wait-guest-agent`, `wait-os-ready`. (Excludes `configure-static-routes` — no router in the slice.)
- **OP-MIR (both):** `attach-gre-tunnel`, `install-ovs-mirror`. This is the capture demonstration.
- **OP-BAR (3 of 5):** `network-ready`, `host-ready`, `mirror-ready`.

**Out of the slice:** all OP-APP, OP-OVL, OP-ACT (no apps, no overlays, no activity simulation), and `domain-ready` / `overlay-ready`. Consequence: **every op in the slice is `infra`-class**, so the slice needs no Ansible execution path at all.

**Planner-execution subset for the slice (the rest of `docs/PLANNER.md` §6–§9 is deferred):**

- **Sequential execution.** `max-ops-in-flight: 1` effectively; the semaphore-per-resource machinery (`docs/PLANNER.md` §8) is **deferred** — a single-node ~16-op DAG demonstrates the thread without concurrency.
- **No lease/lock** (`docs/PLANNER.md` §6.2) in the slice — single operator, single deploy. Deferred.
- **No plan-diff / re-planning** (`docs/PLANNER.md` §9) — deploy-from-scratch only. Deferred.
- **No escalate machinery** — every slice op is `redo`; basic N-retry with the §4 backoff is in scope, the escalate-walk-back path is deferred.
- **State store: minimal but real** — `deploys` + `ops` + `op_attempts` tables with atomic op-completion (`docs/PLANNER.md` §7.3). This is the `axrange status` surface and the resume substrate; it stays.
- **Placement: trivial** — one node, so placement is "the node." The least-loaded heuristic (`docs/PLANNER.md` §3.4) is deferred.

**Package home.** New submodules `axrange/planner/` (DAG build for the slice op subset, sequential executor, minimal state store) and `axrange/adapter_proxmox/` (the `rest` + `ssh` backends and the §3 mapping), per the `axrange/` monorepo layout in `CLAUDE.md`. The planner consumes the already-built op registry (`axrange/ops/`) and the validator's `validate_full` entry point.

**Done-criteria for the slice:**

1. `axrange validate <slice-scenario.yaml>` passes on a hand-written two-host scenario.
2. `axrange plan <slice-scenario.yaml>` prints the ~16-op DAG with correct ordering (`clone-vm` parallel-eligible with SDN; `attach-vm-to-vnet` joining on `network-ready`; tapped-host `start-vm` gated on `install-ovs-mirror` + `attach-gre-tunnel`).
3. `axrange deploy <slice-scenario.yaml>` against a real (or nested) Proxmox node brings up both VMs, attached and mirrored.
4. Manual `ping`/`iperf` between the two hosts produces pcaps in the sensor's `/captures/` tree.
5. `axrange status <deploy-id>` shows the per-op tree from the state store.

## 7. Open within Q5 (resolved by building the slice)

These are answered empirically during slice implementation, not in further design chat:

- Exact REST endpoints / parameters per op (the §3 capabilities → concrete API calls).
- `ovs-vsctl` command forms for `attach-gre-tunnel` and the hook-script body for `install-ovs-mirror` (the catalog and `docs/ARCHITECTURE.md` §3 give the shape; the slice produces the working commands).
- Stable VMID derivation + collision handling under Model C.
- SSH connection management (per-op connect vs. pooled) — a performance refinement, not a correctness question for the slice.

## 8. SDN bypass (slice-MVP implementation note — SUPERSEDED 2026-06-03)

**Status:** ⚠️ **SUPERSEDED + RETIRED IN CODE.** This section recorded the slice-MVP direct-bridge shortcut that diverged from the SDN-native model of §3. As of **2026-06-03** (CHANGELOG "SDN-native networking ratified"), §8.3 **route 1** was chosen, and the SDN-native re-implementation **landed the same day** (CHANGELOG "SDN-native OP-NET ops implemented") — the bypass is gone from the adapter. The text below is kept only as a historical record of what the slice MVP did and why, and of the decision that ended it (§8.3). It no longer describes current behavior. **⚠️ LIVE-VERIFY FINDING (2026-06-03), bigger than expected:** a clean SDN-native deploy on `pxtwin1` brought the infra up correctly (real VLAN zone + vnet, VMs connected, GRE tunnel up) but **capture is broken — and not only on the Simple path.** PVE SDN materializes *every* vnet (VLAN zones included) as a **Linux** bridge holding the VM taps, linked to the OVS underlay via an internal port. The per-VM `ovs-vsctl` mirror therefore has no OVS port to bind (`ovs-vsctl port-to-br <tap>` → "no port named"), so **no mirror is created and zero frames reach the sensor**, silently — while the deploy still reports 25/25 COMPLETED. The "VLAN path is OVS-clean" assumption made at implementation is **false on real hardware**. This is a **design conflict between SDN-native and the per-VM OVS-mirror capture plane** (`docs/ARCHITECTURE.md` §3) that needs a design decision — see CHANGELOG 2026-06-03 "LIVE-VERIFY FINDING: SDN-native networking breaks the per-VM OVS mirror". **RESOLVED 2026-06-04: Option 1** — keep SDN-native, re-mechanize capture onto a per-tap `tc` mirror on the Linux vnet bridge (CHANGELOG 2026-06-04; `docs/ARCHITECTURE.md` §3). The bypass stays retired; capture no longer requires an OVS bridge.

### 8.1 What the slice does

The slice bypasses Proxmox SDN entirely for the OVS bridges:

- `create-sdn-zone` (OP-NET-010) and `create-vnet` (OP-NET-030) are **logical no-ops** that return synthetic ids (`zone_id`; `vnet_id` = the bridge name). No SDN zone or vnet is defined in `/cluster/sdn`.
- `create-ovs-bridge` (OP-NET-020) posts the bridge directly to `/nodes/<node>/network` (an OVS-type bridge named `vbr_<net>`), not as an SDN vnet.
- `apply-sdn-config` (OP-NET-040) `PUT`s the node network config (`/nodes/<node>/network`) to commit the bridge, then SSH-re-asserts each running scenario VM's tap on its `vbr_*` bridge (PVE's `ifreload -a` momentarily detaches them; see the 2026-05-29 CHANGELOG entry).
- The per-network VLAN is applied at `attach-vm-to-vnet` time as `tag=<vlan>` on the VM NIC, not at zone-definition time.

Operators inspect scenario bridges via `pvesh get /nodes/<node>/network`, **not** `/cluster/sdn`.

The op→capability→backend table in §3 is unchanged: the same ops run on the same (REST) backend with the same `inputs`/`outputs` signatures. Only the REST-side *implementation* of the OP-NET plumbing changed — the planner, the op catalog, and the IR are all unaffected.

### 8.2 Why

The OP_CATALOG §5.1 model is "one OVS bridge per scenario network." PVE SDN's VLAN-zone model wants a single underlay bridge specified at zone-creation, with per-network separation expressed as VLAN tags on one shared bridge — a different topology. Reconciling the two at the design layer is real work; the slice MVP could not block on it. Owning the OVS bridges directly via `/nodes/<node>/network` is the smallest implementation that satisfies the catalog's one-bridge-per-network model on a real cluster today.

### 8.3 Path forward (RESOLVED 2026-06-03 — route 1)

Two routes were on the table:

1. **Reconcile the catalog with PVE-SDN** — re-model the catalog's network ops so the one-OVS-bridge-per-network intent maps onto PVE SDN, and move back to SDN-native definitions. Restores `/cluster/sdn` as the operator-visible surface. **← CHOSEN.**
2. ~~**Ratify direct-bridge ownership as the permanent v1 model**~~ — rejected.

**Decision (CHANGELOG 2026-06-03 "SDN-native networking ratified"):** **route 1.** The reconciliation uses PVE's zone types at the two natural points, auto-derived per `OP-NET-010`: a **Simple** zone gives one OVS bridge per network (vnet = bridge); a **VLAN** zone gives one underlay bridge per zone with networks as VLAN-vnets. `create-ovs-bridge` cardinality follows (per-network vs once-per-zone). Driver: logical multi-site twins on one host with high churn and eventual multi-node — exactly the "multi-node bridge-distribution need" route 2 named as its own expiry condition. The bypass is retired; reintroducing direct `/nodes/<node>/network` ownership would now itself be a CHANGELOG-able reversal.
