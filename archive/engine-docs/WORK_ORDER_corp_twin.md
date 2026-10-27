# Work Order — corp-twin: the corporate network as one contained, device-shaped twin (the first `twins/` citizen)

> **STATUS (2026-09-26): S0 (loader), S1 (the twin) and the DEVICE-PARITY round (P0 engine
> enablers, P1 9K VRRP pair, P2 L2 fabric, the `corp-twin-devlab` variant, `te egress`) BUILT,
> offline-verified.** `te validate` + `te plan` clean for both twins — **corp-twin
> `corp-twin-21eefe88`: 147 VMs, 1,264 ops** (was `-b00fca2e`, 135 VMs, 1,083 ops);
> **corp-twin-devlab `corp-twin-devlab-881da2e4`: 19 VMs, 267 ops**. Every `tap`/`tfc` file
> static-OK. Every OTHER shipped plan (every `twin-configs/`, `twin-configs-cast/`, `twins/`
> config and every `target-environments/` scenario) is byte-identical to HEAD `3f3492a1` — ops,
> inputs, planner hints, edges and `plan_id` — measured, §10 P0. Device parity is delivered as an
> **implementation substitution** (§4.1, §12 D1): Debian `kind: host` device VMs running role
> recipes, NOT VyOS — no version-locked contract changed.
>
> **UPDATE (2026-09-28): §11 live-verified through run 4.** Devlab runs 1–2 (D0–D8) and the full
> twin's runs 3–4 (gate 0.9, `corp-twin-07cfcdb2`, 106 VMs) are complete — device parity, egress
> containment (all 25 LAN legs CONTAINED), mirror taps (0 loss after the gretap DF fix) and traffic
> mix (rebalanced to the discovered profile) are all live-measured. Full record: §11 live runs
> 1–4, CHANGELOG 2026-09-28 (four entries). The full 147-VM twin at the default (0.8) gate has
> **not** been deployed — gate 0.9 thins endpoints by ~1/3; that remains the user's call (§12).
>
> **Authority:** **user** (2026-09-25) for §2's rulings — full device parity with VyOS VMs, the two
> real mirror points, the `twins/corp-twin/` location, the minimal Phase 2 loader.
> **implementation (Claude Code)** for everything else in this file: the source reconciliation
> (§3, §5), the addressing and relocation rule (§6), the S1 shape the engine can build today (§4),
> the mirror mapping (§9) and the slicing (§10) — and, 2026-09-26, the device-parity
> **implementation substitution** (§4.1: Debian device VMs instead of VyOS, reversible, recorded
> against the ruling in §12 D1), the engine enablers (§10 P0) and `te egress`.
>
> **Extends:** `WORK_ORDER_twin_consolidation.md` (the layout, §2; this twin is its first citizen
> and implements a minimal slice of its Phase 2 items 2–4) · `WORK_ORDER_protocol_engines.md`
> (E1 egress phase, E2 sink — reused, not restated) · `design/13`/`14` (evidence precedence) ·
> `design/06-taps-sensors.md` (tap scopes).

## 1. Goal

A twin of the **real corporate network** — the one `CORP APCN` captures — that is shaped like the
real devices (edge MX85, MS120 core, 3850, 9K TOR pair, 8325h VSX pair, access switches), carries
the hosts and traffic the axquery discovery observed, is mirrored at the **same two points** the
real capture is (External TAP on the WAN, MS120 SPAN), and **cannot reach the real internet**.

## 2. Rulings already made (Authority: **user**, 2026-09-25)

1. **Full device parity with VyOS VMs** — MX85 edge (every VLAN gateway, WAN1 active, WAN2
   standby), MS120 core (L2 VLAN-aware), 3850 distribution, 9K01/9K02 as a VRRP pair (HSRP
   analogue) with vPC-like dual links, eng-8325h left/right VSX pair (L2), access switches that
   carry discovered hosts; SFP26 STP-blocked and hwlab2960s DOWN modelled faithfully if the IR can
   express it, else recorded as a gap. ~6–8 VyOS VMs, sized minimally.
2. **Mirror points match reality** — WAN External TAP (outside the MX85) and the MS120 SPAN, both
   delivered to the sensor side (o2-suricata and/or XPress-SW-A; "Aggregator" = the fan-out).
3. **Location** — `twins/corp-twin/` per the consolidation layout.
4. **Minimal Phase 2 loader** — `twin.yaml` parse/validate; `te … --config corp-twin` finds
   `twins/corp-twin/te.yaml` (a `twins` catalog root beside `twin-configs/`, old catalogs keep
   working); tap/tfc take the files directly and, cheaply, `--twin corp-twin [--variant x]`.
5. **Hazards** — never touch the standing `svc-engines-24131435`; avoid IP/VNet/VLAN/VMID
   collisions with it; the twin declares containment; its WAN never reaches the real internet.
   Enforced, not just stated: `tap deploy` / `tfc deploy` refuse a target whose egress phase reads
   back (the `te info` read-back — `configure-router`'s recorded `egress_phase`, refreshed live by
   `axrange te egress`, 2026-09-26) as anything but `measure`, unless `--allow-uncontained` is
   passed; a twin with no `egress:` is unaffected (§11).

## 3. Sources, and which one wins

| Source | What it is | Used for |
|---|---|---|
| `/home/rocky/work/docs/topology/image.png` | device/VLAN diagram (MX85 → MS120 → access + 3850 → 9K pair → 8325h pair) | the **device and VLAN skeleton** — evidence class `asserted` (design/14 precedence: asserted > imported > observed > referenced > imputed > compiler) |
| `/home/rocky/work/docs/topology/image (1).png` | physical capture diagram (Comcast → External TAP → MX85; MS120 SPAN; both → Aggregator → CORP APCN) | the **mirror points** (§9) |
| `twin-configs-cast/corpapcn-disco/` | aximprint cast of the axquery discovery loop, as-of 1780405200, 9 windows, vantage `axellio-hq/corp-apcn-rdr1:12`, `--relocate 10.200.0.0/16`, **2026-09-08** | **hosts, roles, listen sets, observation blocks, conduits, server recipes, personas, traffic profile** — class `observed` |
| `twin-configs-cast/corpapcn-golden/` (2026-09-06) | the Console's golden fixture | not used — older cast of the same network |
| `twin-configs-cast/corpapcn-twin/` (2026-08-27) | scorecard run 1's subject | not used — oldest cast |

**Why `corpapcn-disco` is authoritative:** it is the newest of the three casts of the same network
(2026-09-08 vs 09-06 vs 08-27), the only one produced by the axquery discovery loop against the live
CORP APCN, the one the protocol-engine round measured its target mix from
(`WORK_ORDER_protocol_engines.md` §1), and the source `svc-engines`' sink corpus is generated from.
The consolidation triage's D5 keeps all three for now; this twin reads only `disco`.

## 4. The engine vs the ruling — the finding that shapes S1

Ruling 1 and hazard 5 **cannot both be met by today's engine**. Measured in source, not assumed:

1. **A VyOS router in a twin that declares `egress:` is refused at plan time**
   (`planner/pass1_emit.py`, the `is_vyos` branch under `egress_policy`; `egress_render.py`
   "VyOS — NOT implemented": VyOS 1.3/1.4 firewall syntax differs and a half-applied containment
   rule set is the worst failure the slice can have).
2. **Every `kind: router` gets a real WAN NIC on `vmbr0` with DHCP + source-NAT**
   (`ROUTER_WAN_BRIDGE`, `_wan_nic_index`; the VyOS branch also *manages* the router over that
   WAN). So six VyOS routers without `egress:` would be six independent real-internet exits — the
   2026-09-21 breach (GAP-101) times six.
3. **No L2 device kind** — `kind: switch` is refused by `pass1_emit` ("switch not implemented");
   a segment *is* its SDN vnet bridge. `configure-router`'s VyOS inputs are interface addresses +
   SNAT + static routes only: **no bridge/`vif`, no VRRP, no STP, no link admin-state**.
4. **No inter-router routing for Debian routers** — the Linux `configure-router` branch installs no
   static routes, and `TwinConfigNode` has no `static_routes:` passthrough; dynamic routing
   (`routing:`/`fabric:`) is VyOS-only. Two Debian routers cannot reach each other's subnets.
5. **VLAN tags are not authorable** — a VLAN zone auto-assigns tags (`expand.py` `_VLAN_BASE`);
   there is no trunk (tagged multi-VLAN) link. Every segment is its own untagged vnet.

**Implementation call (Claude Code):** build what can be **contained today** as S1 and record the
parity work as S3–S6, gated on the user (§12 D1) because they change `IR_SCHEMA` (VRRP/L2/link
state) and `OP_CATALOG` OP-HOST-090's input contract:

- **One Debian router, `hq-mx`, stands in for the MX85** — which *is* the real network's L3
  gateway for every VLAN. It carries the twin's `egress:` posture.
- **The L2 fabric (MS120, 3850, 8325h pair, access switches) is the SDN vnets** — one segment per
  real VLAN broadcast domain. Their device boundaries are not VMs (§8 gaps G1–G4).
- **The 9K pair's L3 role is folded into `hq-mx`** (VLAN935/953 are `hq-mx` legs) — the extra
  routed hop to VLAN953's servers is absent (G5).

The alternative — build VyOS parity now and leave the twin uncontained — violates hazard 5 and
repeats GAP-101; refused.

### 4.1 Device parity without a contract change (implementation substitution, 2026-09-26)

**Authority: implementation (Claude Code), orchestrator's call, reversible, content-level.** The
user was away; the ruling (§2.1: VyOS VMs) cannot be met without the locked-contract work in
§12 D1, so parity is built with what the engine already runs — **every internal network device
is a Debian `kind: host` VM whose role recipe builds the device**:

- **L2 switches** (MX85 switch side `mx-sw`, MS120, SGS-5240, AxeProdSW, cos-sw-r32e01,
  labswitch, hwlab2960s, 3850, 8325h-L/R): `twins/corp-twin/scripts/serve-l2switch.sh` — a
  VLAN-aware Linux bridge (`vlan_filtering 1`, `vlan_default_pvid 0`), 802.1Q trunks with the
  real VLAN IDs (VLAN1 native/untagged), kernel STP (mx-sw root; SFP26 blocks at the MS120),
  admin-down ports (MS120 P17 → hwlab2960s), the MS120's management IP on a VLAN20 SVI. One
  script, a per-device port table keyed by hostname, iproute2 only (no apt), boot-persistent.
- **9K01/9K02**: `scripts/serve-vrrp-l3.sh` — keepalived VRRP (the HSRP analogue), two instances
  in one sync group (VLAN935 VIP `10.180.93.254`, VLAN953 VIP `10.180.34.1` = the servers'
  gateway), forwarding on, default route via hq-mx.
- **hq-mx stays the ONLY `kind: router`**, the only WAN NIC, the only `egress:` carrier — so
  containment still lives on one box (hazard 5 unchanged). Its return route to VLAN953 is a
  recipe (`scripts/serve-hq-mx-routes.sh`), because the Debian `configure-router` installs no
  static routes and OP-HOST-050 has no handler.

Why not VyOS-as-host: VyOS has no QGA/cloud-init in the generic image, so it cannot run a
`run-script` recipe; an internal VyOS **router** needs a no-WAN mode and bridge/VRRP inputs on
`configure-router` — `IR_SCHEMA` + `OP_CATALOG` changes (§12 D1). Reversal: swap each device's
image + recipe for VyOS once D1 lands; the segment/leg topology below is already device-shaped.

The engine enablers this needed are twin-config/planner-internal, no contract bump (§10 P0):
L2 legs (`nets[].ip: none` / `l2: true`), `segments[].isolated: true` (the ratified NET-010
exemption, restricted to "same subnet as exactly one anchor segment"), per-CIDR IP allocation,
gateway inheritance, the planner's first-leg gateway rule + positional ipconfig placeholders,
the served-hosts closure through forwarders, and forwarder ordering edges.

What §4's five findings now read as: (1) still true — no VyOS under `egress:` (none is used);
(2) still true for `kind: router`, and exactly why the devices are `kind: host` (no WAN NIC);
(3) worked around — the L2 devices are bridge VMs; (4) worked around — one router plus a recipe
route, the 9K pair is an L3 `kind: host` forwarder; (5) worked around — the real VLAN IDs ride
802.1Q trunks between the bridge VMs (the SDN vnets stay untagged Simple-zone links).

## 5. Reconciliation — device / VLAN / subnet → twin segment

Relocation rule (§6): `10.80.X.Y → 10.180.X.Y`; other observed `10.A.B.0/24 → 10.(A+100).B.0/24`;
`172.16.0.0/24 → 10.172.16.0/24`; `192.168.0.0/24 → 10.192.168.0/24`; the WAN /28
`50.209.234.64/28 → 10.250.234.64/28` (WAN2 on `10.251.234.64/28`). Host parts preserved.

### 5.1 HQ VLANs (existence `asserted` by the diagram — carry no `observation:`, never gated)

| Real VLAN | Real device / gateway | Real subnet (evidence) | Twin segment | Twin CIDR | Discovered /24s folded in |
|---|---|---|---|---|---|
| 1 | MX85 | 10.80.0.0/20 (*inferred*: contiguous plan) | `vlan1` | 10.180.0.0/20 | 10.80.0 |
| 20 | MX85 | 10.80.16.0/20 (*inferred*; holds MS120 mgmt 10.80.16.15 and the DC) | `vlan20` | 10.180.16.0/20 | 10.80.16, 10.80.20, 10.80.31 |
| 32 | MX85 + 9K SVI/HSRP | 10.80.32.0/21 (**asserted**, diagram "VLAN32 .32.0/21") | `vlan32` | 10.180.32.0/**23** (C1) | 10.80.32 |
| 40 | MX85 | 10.80.40.0/21 (*inferred*) | `vlan40` | 10.180.40.0/21 | 10.80.40 |
| 48 | MX85 | 10.80.48.0/21 (*inferred*) | `vlan48` | 10.180.48.0/21 | 10.80.48 |
| 52 | MX85 | 10.80.56.0/21 (*imputed*: the last free /21 below VLAN64) | `vlan52` | 10.180.56.0/21 | 10.80.63 |
| 64 | MX85 | 10.80.64.0/20 (*inferred*; **anchor**: the orchestrator's own DHCP lease is 10.80.72.52/20) | `vlan64` | 10.180.64.0/20 | 10.80.64–79 (≈80 hosts, the office) |
| 192 | MX85 | 192.168.0.0/24 (*inferred*: ID ↔ prefix) | `vlan192` | 10.192.168.0/24 | 192.168.0 |
| 880 | MX85 | none observed (*imputed*) | `vlan880` | 10.180.88.0/24 | — (router leg only) |
| 1010 | MX85 | none observed (*imputed*) | `vlan1010` | 10.180.101.0/24 | — (router leg only) |
| 935 | 9K pair | none observed (*imputed*) | `vlan935` | 10.180.93.0/24 | — (router leg only) |
| 953 | 9K pair → 8325h → servers/ESX | 10.80.34.0/25 (**asserted**) | `vlan953` | 10.180.34.0/25 | 10.80.34 (3× web-tier 443/9080 + 1 heavy) |

**Device-parity round (2026-09-26): every VLAN is now TWO segments with one subnet.** The CIDR
above is anchored on `mx-vlanN` (hq-mx's gateway leg ↔ mx-sw's untagged port); the hosts sit on
`vlanN` — an `isolated: true` access segment carrying the same subnet on the far side of the
MS120/access-switch bridges (the name every tfc/tap file used is unchanged). VLAN880/1010/935
have no access segment (no hosts); VLAN935 continues as `x3850-9k01`/`x3850-9k02` (the 3850's
links to the 9Ks). VLAN953 is anchored on `vlan953` itself (gateway = the 9K VRRP VIP `.1`,
hq-mx has no leg there any more), extended by `x9k01-8325l`, `x9k02-8325r`, `isl-8325`. Trunks
(`sfp25`, `sfp26`, `t-ms120-*`) carry no address; each has a placeholder `/29` from
`10.199.0.0/24` because a segment must validate with a CIDR.

Conflicts, recorded:
- **C1 — VLAN32 `/21` contains VLAN953's `10.80.34.0/25`.** Both are asserted by the diagram, but
  the validator refuses overlapping networks (**NET-010**, measured), so the twin narrows VLAN32 to
  `10.180.32.0/23` — the largest aligned block holding the one observed 10.80.32 host that does not
  touch VLAN953. The real /21 is recorded here and in `te.yaml`. (A real 9K may hold both SVIs
  longest-prefix; the IR cannot say that.)
- **C2 — MS120 mgmt `10.80.16.15` is on a native-VLAN-1 trunk, but the contiguous plan puts
  10.80.16/20 in VLAN20.** The plan wins (it is the only reading in which every observed 10.80 /24
  lands in exactly one listed VLAN); the MS120's management VLAN is unobserved either way.
- **C3 — VLAN 52's range is imputed.** The alternative reading (VLAN48 = /20, VLAN52 unplaced) is
  equally consistent; the twin picks the one that gives every listed VLAN a range.
- **C4 — the cast re-addressed every /24 into `10.200.N.0/24` in discovery order** (aximprint
  `--relocate`), losing the real third octet; this twin relocates by a readable rule instead (§6).

### 5.2 Off-map subnets (only evidence is traffic — keep their `observation:`, gated)

Observed at the HQ vantage but on no listed VLAN — lab/remote networks reached through the HQ
(routed via the lab TORs or AutoVPN; the diagram does not say). Each stays its own `/24` and its own
`hq-mx` leg, so their routed flows cross the MS120-SPAN mirror as they did in the capture:
`lab-10-0-21`, `lab-10-1-10`, `lab-10-110-240`, `lab-10-112-140`, `lab-10-128-215`,
`lab-10-20-{192,193,195,196,198,200,201,203,204,207}`, `lab-10-21-161`, `lab-10-55-10`,
`lab-10-64-7`, `lab-172-16` (relocated per §6). At the default `DEPLOY_MIN_OCCUPANCY=0.8` six of
them gate out (occupancy 0.22–0.78).

### 5.3 Devices

| Real device | Twin (device-parity round, 2026-09-26) | Still missing |
|---|---|---|
| MX85 "HQ-MX" (edge, every VLAN gateway, WAN1 active / WAN2 ready) | `hq-mx` Debian router (L3 engine: legs on every `mx-vlanN` + `wan1-isp` + `wan2-isp` + the lab /24s) **+ `mx-sw`** (its switch side: one untagged port per VLAN, SFP25/SFP26 uplinks, STP root) | VyOS (D1); WAN2 failover (G6) |
| Comcast ISP | the any-IP `sink` on `wan1-isp` (10.250.234.66) | — |
| MS120-24P "Telstar-Switch" (core L2, 28 ports) | `ms120` bridge VM: SFP25/SFP26 trunks to mx-sw, trunks to every access switch + the 3850, VLAN20/VLAN1 access ports, mgmt `10.180.16.15` on a VLAN20 SVI | only 10 of 28 ports modelled |
| SFP25 forwarding / SFP26 STP-blocked | segments `sfp25`/`sfp26`, 802.1Q trunks; SFP26 costs 200 vs 100 → **blocks at the MS120** | 802.1D kernel STP, not Meraki's RSTP (G20) |
| SGS-5240, AxeProdSW, cos-sw-r32e01, labswitch | bridge VMs on `t-ms120-*` trunks, carrying `vlan64` / `vlan48`+`vlan52` / `vlan40` / `vlan192` | which VLAN sits on which switch is **imputed** (G17) |
| hwlab2960s (P17 access 32, DOWN) | `hwlab2960s` bridge VM carrying `vlan32`; its MS120 port `p17-hwlab2960s` is **admin-down** | — |
| Catalyst 3850 | `sw-3850` bridge VM: trunk from the MS120, one VLAN935 access link to each 9K | vPC = two access links, no MLAG/LACP (G19) |
| 9K01 / 9K02 (L3 TOR, vPC + HSRP) | `9k01`/`9k02` Debian L3 VMs, keepalived VRRP sync group (VIPs `10.180.93.254`, `10.180.34.1`), pinned `.2`/`.3` | HSRP → VRRP; no SVI for VLAN32 (C1) |
| eng-8325h left/right (L2 VSX, VLAN953) | `8325h-l`/`8325h-r` bridge VMs joined by the `isl-8325` link; servers on `vlan953` off the left member | VSX sync/keepalive not modelled (G21) |
| External TAP, MS120 SPAN, Aggregator, CORP APCN | tap tier (§9) — the SPAN now binds the `ms120` VM's own ports | — |

## 6. Addressing, relocation, and collision avoidance

- **Why relocate at all:** `dev45` and this orchestrator live in the real `10.80.0.0/16` (the
  orchestrator's own lease is `10.80.72.52/20`). A twin on the real addresses would shadow real
  hosts on any path that touches the host network (design/14: "a CIDR relocated to avoid the
  deployment target's own address space").
- **The rule** (§5) keeps the host part and the third octet, so `10.180.72.x` reads as `10.80.72.x`.
  Checked: **no address observed in the cast (2,641 entities, 4,157 edges) falls in any relocated
  range**, and none overlaps `svc-engines` (`10.66.0.0/16`) or the capture transport
  (`10.255.251.0/24`).
- **VNet / zone / VM names** are derived from this deploy's `plan_id` (`adapter_proxmox/naming.py`,
  Model C) — cannot collide with `svc-engines-24131435`'s.
- **VLAN tags:** every segment is `zone_type: simple` (an isolated Simple zone, no tag on any
  SDN bridge). Since the device-parity round the **real VLAN IDs are on the wire** anyway: the
  switch VMs tag them onto the 802.1Q trunk segments (`sfp25`, `t-ms120-*`, …), VLAN1 native.
  A tagged 1500-byte frame is 1522 bytes on the vnet; Linux bridges forward it (`VLAN_HLEN`
  allowance) — the runbook still measures it (§11 D3).
- **NIC ceiling:** `hq-mx` has one leg per emitted segment + its WAN NIC. Since the device-parity
  round: 2 WAN + 11 `mx-vlanN` + the lab /24s the gate keeps (13 at 0.8) = **26 legs + WAN = 27
  NICs** at the default gate; `mx-sw` 13, `ms120` 10, every other device ≤ 3. At
  `DEPLOY_MIN_OCCUPANCY=0` all 19 lab /24s survive → hq-mx **33, over PVE's 32** (`net0`–`net31`),
  and nothing in the validator/planner catches it. Keep the gate at ≥ 0.8 (G10); a test pins
  ≤ 32 NICs per VM at the default for both twins (`tests/test_corp_twin_device_parity.py`).

## 7. Host inventory and traffic profile sources

- **Hosts:** every `corpapcn-disco` node except its synthetic `gw` and the four `internet-sink-N`
  groups, **names unchanged** (they encode the source /24 — `generic-endpoint-10-80-72-core` is on
  real `10.80.72.0/24`), each moved onto its §5 segment, with its image, resources, install,
  listen, `joins:`, `provides:` and `observation:` verbatim. 190 hosts before the gate; **133 after
  it** at 0.8 (17 Windows: the DC + 16 `win11-finance@v4` workstations joining `corp.axr.lab`;
  the plan carries 16 `join-host-to-domain` ops).
  Addresses are auto-allocated inside the VLAN supernet (G8).
- **Server recipes:** the cast's generated `serve-*.sh`, copied into `scripts/`; the one address
  literal (`serve-dns-resolver-10-20-196-core.sh`'s DHCP scope, `10.200.8.0/24`) is rewritten to the
  §6 segment (`10.120.196.0/24`).
- **Sink:** one any-IP sink (the E2 design), not the cast's four `internet-sink-N` groups —
  `egress:` requires exactly one `role: sink`. Its payload `scripts/provision-sink.sh` is generated
  by `scripts/build-sink-payload.sh` from the disco `topology.json` + `twin-configs/_shared/scripts/
  serve-sink.sh` (the same corpus svc-engines serves).
- **Traffic profile:** the cast's own `tfc.yaml` (147 assignments: 2 role-level with the cast's
  seeds and intensities, 145 node-level) → `tfc/default.yaml`, **121 assignments**: a node entry
  naming a `count > 1` group stem is fanned out to its clones (32 added — the cast's own file named
  stems, which `o2control/traffic.py` refuses as "not a host"), and 45 entries naming hosts the
  default gate removes are dropped (a missing `node:` is a hard error at apply). A test pins every
  `node:`/`role:` in all three profiles against the default expansion; `tfc-roles.yaml` → `tfc/roles.yaml`; the discovery loop's
  Iteration-3 `tfc-exercise.yaml` → `tfc/exercise.yaml`. Personas copied into `personas/`, with
  `peer_role: internet-sink-{1,2,4,8}` → `peer_role: sink` (one sink).
- **The measured mix the profile is judged against** (disco `topology.json`, 9 windows): external
  TLS **6.26 GB / 12.8k flows** of ~8.95 GB total; internal SMB **2.2 GB**; DNS **~103k flows
  (the flow majority)**; HTTP external 220 MB; SSH external 29 MB; QUIC, NTP, SSDP, LDAP, Kerberos,
  RPC, RDP each < 1% of bytes. 1,578 edges touch the WAN1 address `50.209.234.65` (mostly inbound
  internet background: `other`/TLS/HTTP/ICMP/SSH/telnet).

## 8. Fidelity gaps

**Status column** — CLOSED rows were closed by the device-parity round (2026-09-26, §4.1,
offline only: none is live-verified until §11 runs).

| ID | Gap | Why | Status |
|---|---|---|---|
| G1 | MS120, 3850, access switches are not VMs; no MAC-learning/L2 hop, no trunk | no `kind: switch`; no VyOS bridge input | **CLOSED** — bridge VMs + 802.1Q trunks (`serve-l2switch.sh`) |
| G2 | SFP26 STP-blocked redundant uplink absent; no STP BPDUs on the wire | no L2 device, no STP, no link state in IR | **CLOSED** — `sfp25`/`sfp26`, kernel STP, SFP26 blocks at the MS120 |
| G3 | hwlab2960s DOWN access link absent (its VLAN32 port) | no link admin-state in IR | **CLOSED** — `p17-hwlab2960s`, admin-down in the MS120 recipe (link state lives in the recipe, not the IR) |
| G4 | eng-8325h VSX pair collapsed into the `vlan953` vnet | as G1 | **CLOSED** — `8325h-l`/`8325h-r` + `isl-8325` (see G21) |
| G5 | 9K01/9K02 L3 hop + HSRP/VRRP hellos absent; VLAN935/953 routed by `hq-mx` | Debian routers cannot reach each other; VyOS refused under `egress:`; no VRRP | **CLOSED** — `9k01`/`9k02` keepalived VRRP sync group (HSRP → VRRPv2 on the wire); hq-mx routes the /25 via the 935 VIP (recipe) |
| G6 | WAN2 is a present-but-idle leg, no failover behaviour | Meraki uplink failover not modelled | open |
| G7 | "external" destinations are the sink's own address for scripted personas (`peer_role: sink`), not public IPs; real public IPs only for DNS-resolving engines in `measure` | scripted driver resolves a peer role to a twin host | open |
| G8 | host addresses are auto-allocated inside each VLAN supernet — the real host part (and the /24 it sat in) is not reproduced | a `count > 1` node cannot pin per-clone IPs | open |
| G9 | VLAN IDs appear in names only; segments are untagged | tags not authorable (§4.5) | **CLOSED on the trunks** — the switch VMs tag the real IDs; the SDN vnets and access segments stay untagged, as real access ports are |
| G10 | `DEPLOY_MIN_OCCUPANCY` < ~0.7 overflows `hq-mx`'s 32-NIC ceiling | one router carries every leg | open — 27 NICs at 0.8, 33 at 0 (§6) |
| G11 | `win11-finance@v4` is the Server 2022 template on dev45 — no consumer-Windows chatter (7680 DO, SSDP) | image binding (svc-engines' KNOWN LIMIT) | E7 image bake |
| G12 | the cast resolvers forward to the router (not a resolver): external names SERVFAIL there | the cast's generated recipe; svc-engines' `serve-twin-resolver.sh` is the fixed shape | follow-on |
| G13 | sink answering external destinations is still unproven live | GAP-100 | GAP-100 |
| G14 | asserted VLANs **1, 32, 40 vanished at the default gate** | the empty-segment cascade had nothing left on them | **CLOSED** — a switch VM is a non-router occupant, so the cascade no longer fires (pinned by a test); no `asserted` flag was needed |
| G15 | the API/serve path does not add a twin folder's `personas/` (only the CLI does) | S0 is CLI-only (LES-275 names this class) | Phase 2 proper |
| G16 | a bridge VM maps NIC → port BY POSITION, ordering NICs by PCI path (PVE: net0–5 at bus 0 slots 0x12–0x17, net6+ behind the pci.1 bridge) — an assumption about PVE's layout, checked only by the NIC-count guard | an L2 leg has no address to find it by | live-verify §11 D2 (compare the logged MAC map with `qm config`) |
| G17 | which VLAN sits on which access switch is **imputed** (the diagram names switches, not port maps): vlan64→SGS-5240, vlan48/52→AxeProdSW, vlan40→cos-sw-r32e01, vlan192→labswitch, vlan20/vlan1→MS120, vlan32→hwlab2960s | no source | a switch config export (D2) |
| G18 | NICs with no `ipconfigN` are assumed to be left UNCONFIGURED by PVE's cloud-init network config (no DHCP), so a bridge port boots link-down and the recipe brings it up; if PVE instead emits DHCP for them, boot waits on DHCP timeouts per port | PVE-side behaviour, not verifiable offline | live-verify §11 D1 |
| G19 | the 3850 ↔ 9K "vPC" is two independent VLAN935 access links, no MLAG/LACP bundle | no bonding in the recipe | open (cosmetic on the wire) |
| G20 | kernel STP is IEEE 802.1D (30 s-class convergence, here `forward_delay` 4 s), not the RSTP/MSTP the Meraki/Cisco gear runs | iproute2-only recipe; `mstpd` is not on the base image | open |
| G21 | the 8325h VSX pair is two bridges + an ISL link; no VSX keepalive/sync, no active-active MAC | out of scope for an L2 stand-in | open |
| G22 | trunk segments carry a placeholder `/29` (10.199.0.0/24) — published in the topology, never addressed | a segment must validate with a CIDR | open (cosmetic) |
| G23 | only the MS120 has a management IP; the other devices are unaddressed except the 9Ks' routed legs | nothing in the capture needs them | open |
| G24 | no WAN-inbound internet background (§7: 700 inbound edges to WAN1 — `other`/TLS/ICMP/HTTP/SSH/RDP/telnet) | a persona dials a ROLE; `gw` resolves to hq-mx's first addressed leg in attach order (lab-10-0-21, `_host_ip`), not its wan1-isp leg — no persona field selects a leg | open (§11 live run 3 mix) — needs a leg/segment-scoped peer selector |

## 9. Mirror points

| Real | Twin (`tap/default.yaml`) |
|---|---|
| **External TAP** on the Comcast uplinks, outside the MX85 (two taps: WAN1, WAN2) | `segment` bindings on `wan1-isp` and `wan2-isp` — the ISP side of `hq-mx`. In `measure` every external flow crosses `wan1-isp` to the sink, so this *is* the WAN view. **Not `perimeter: wan`** (LES-143: `vmbr0` is shared by every twin's router, and after `measure` nothing crosses it). |
| **MS120 SPAN** | `segment` bindings on **every segment the `ms120` VM has a port on** (`vlan20`, `vlan1`, `sfp25`, `sfp26`, the five `t-ms120-*` trunks, `p17-hwlab2960s`) + the 13 off-map /24s the default gate keeps (still direct hq-mx legs) — 25 bindings with the two WAN ones. Trunk frames carry their **real 802.1Q tag**; a frame is captured once per segment it crosses (a vlan64 → hq-mx frame: `t-ms120-sgs5240` + `sfp25`); intra-switch traffic below the MS120 is not seen — all as a real SPAN. Variant `tap/uplink-span.yaml`: `segment: sfp25` (= a SPAN of the SFP25 uplink: every MX↔MS120 frame, tagged, no east-west below the MS120) + `perimeter` on hq-mx's lab legs. |
| **Aggregator → CORP APCN** | both points' bindings deliver to **XPress-SW-A** (the packetXpress/axquery appliance — the CORP APCN analogue). Variant `tap/fanout-suricata.yaml`: every binding `mode: fanout` to `[XPress-SW-A, o2-suricata]` (the 0160712f shape). |

## 10. Slices

- **S0 — loader (BUILT).** Record moved to
  [`docs/archive/work_orders/WORK_ORDER_corp_twin.built.md`](archive/work_orders/WORK_ORDER_corp_twin.built.md) §10
  (2026-09-28, doc-size rule). `axrange/twinconfig/manifest.py` + `registry.py` resolve a
  `twins/<id>/twin.yaml`; `tap|tfc validate|plan|deploy --twin ID`.
- **S1 — the twin (BUILT).** Record moved to the same archive file, §10. `twins/corp-twin/{twin.yaml,
  te.yaml, tap/, tfc/, personas/, scripts/, generated/}`; `te`/`tap`/`tfc validate`+`plan` clean.
- **S2 — gated live-verify on `dev45`** (§11). *Acceptance:* every step's check passes; the
  capture-window mix is compared against §7 and the deltas recorded.
- **P0 — engine enablers (BUILT, 2026-09-26).** All twin-config/planner-internal; no
  version-locked contract touched (`Network.isolated` and an IP-less `NetRef` already exist in
  IR_SCHEMA; no op input added):
  - `twinconfig/schema.py` + `expand.py`: **L2 legs** (`nets[].ip: none` or `l2: true`) — no
    address, skipped by the allocator, emitted with no `ip:`; refused on a router and with an
    explicit address. **`segments[].isolated: true`** → `Network.isolated`, allowed ONLY when the
    CIDR equals exactly one non-isolated sibling's (the anchor; several extensions may share
    one). **IP allocation is keyed by CIDR**, not segment (one pool across an L2 extension).
    **Extensions inherit the anchor's gateway.** Forwarder hosts already counted as segment
    occupants in the gate cascade (every non-router spec does) — verified and pinned, which is
    what closes G14.
  - `planner/pass1_emit.py`: a multi-homed HOST gets a gateway only on its first addressed leg
    whose segment has one; an address-less leg AHEAD of an addressed one keeps an
    `{"ip": None}` placeholder so `ipconfigN` stays aligned with `netN`
    (`adapter_proxmox/rest._build_ipconfig_body` already numbered by position — documented and
    pinned); `served_hosts` is closed through forwarders (non-router nodes with ≥ 2 segments).
  - `planner/pass3_ordering.py` `_add_forwarder_edges`: forwarders are layered by BFS distance
    from the routers' segments and grouped into components (joined by shared NON-router
    segments); a forwarder's recipes wait on those of every strictly-nearer forwarder of its
    component; a host behind the fabric waits on every forwarder of its component and on the
    recipes of the routers the component hangs off (hq-mx's return route). Acyclic by
    construction (strict layer order; hosts are sinks) — tested.
  - **Byte-identity, measured:** every plan of every `twin-configs/`, `twin-configs-cast/`,
    `twins/` config (plus `datacenter` FABRIC=true, `dmz-perimeter` N=1, corp-twin at
    occupancy 0, `corpapcn-disco`'s file and the maxrate expanded IR) and every
    `target-environments/` scenario was planned at HEAD `3f3492a1` (a git worktree) and after
    the change, and compared op-by-op (inputs, planner hints), edge-by-edge and by `plan_id`:
    **identical** — the only differences are corp-twin's own content change and the new devlab.
  - **`axrange te egress [ID|TWIN] --show | --phase measure|provision`**
    (`o2control/containment.py`, `StateStore.merge_op_outputs`): runs the router's own
    `axr-egress-phase.sh` over QGA (flip, then `--show` + `--healthcheck`) and merges the live
    result into the `configure-router` row's outputs (`egress_phase`, plus `egress_live:
    {phase, since, healthy, read_at, via}`). `te info` and the tap/tfc gate read it through
    `egress_posture_from_ops` unchanged. **Fail closed:** a `measure` whose healthcheck fails
    is recorded `measure-unverified` (not contained). The gate's refusal now names the command.
    `--allow-uncontained` unchanged.
  - **Review fix round (2026-09-26, Authority: implementation (Claude Code)):**
    - *Invalidate before acting.* `egress_live` rewrites the row to `<action>-pending-unverified`
      (flip) / `unknown-unverified` (show) BEFORE any QGA exec; a non-zero `--show` or any exec
      exception records `unknown-unverified` + `egress_live.error` and raises. No failure path
      can leave an earlier `measure` standing. `merge_op_outputs` returning False, or refusing a
      corrupt `outputs_json` (it now raises instead of coercing to `{}`), is a `ContainmentError`.
      `te egress` also catches `ConfigError`/`AdapterError` cleanly.
    - *The gate probes live* — **supersedes** the P0 call "the gate reads the recorded row, not
      a second probe". `_containment_gate` runs `egress_live(..., "show")` itself (one
      `--show` + `--healthcheck` over QGA, 60 s timeout, recorded like any `te egress --show`)
      whenever the target declares an `egress:` posture, and passes only on an asserted live
      `measure`. An unreadable router (no QGA, bad `adapter.conf`, cluster down) refuses;
      `--allow-uncontained` still overrides (warning names the read error). Reason: a recorded
      `measure` says what the router was in when last read, not now. `te info` / `te inventory`
      print `(live read <UTC>)` and the posture carries `live_read_at` (`EgressPostureDTO` too).
    - `pass3`: a forwarder component with no layered member (reaches no router segment) gets no
      host rule; `_RECIPE_OPS` shared with `_add_router_egress_edges`; `_plan_topology` read
      once. `pass1`: a DHCP leg never claims the multi-homed host's one static default route.
    - `segments[].reserved: [ip, ...]` (twin-config only, never in the IR): out of the CIDR's
      pool and refused as a pinned leg address. corp-twin/devlab reserve the 935 VIP
      `10.180.93.254` (`10.180.34.1` is already `vlan953`'s `gateway:`, i.e. reserved). Content
      now spells L2 legs `l2: true` (IR identical); an explicit `ip: null` logs a warning that it
      means "allocate".
    - `serve-l2switch.sh`: per port — dhclient killed, IPv6 off (persisted in
      `/etc/sysctl.d/90-axr-l2switch.conf`), the ifupdown stanza / netplan entry rewritten to
      "no address", cloud-init network re-render disabled; OK only after every non-admin-down
      port is `forwarding`/`blocking` (≤ 45 s) and no port has any address.
      `serve-vrrp-l3.sh`: logs each VRRP instance's state (keepalived `notify` →
      `/run/axr-vrrp/<inst>.state`), pings the peer on both legs (WARN if unreachable), ARP-probes
      each VIP (`iputils-arping`) and warns loudly on a split pair (MASTER with a second
      answerer, or BACKUP with none).
  - **Fix round 2 (2026-09-26, Authority: implementation (Claude Code)):**
    - `serve-l2switch.sh`: ifupdown has no end-of-line comments — the rewritten stanza is a
      bare `iface <nic> <fam> manual` with `# axr-l2switch: bridge port` on its OWN line before
      it (the earlier inline form is rewritten on the next apply). Any rewrite is checked in the
      same apply by the tool's own parser (`ifquery -a --list`, else `ifup --no-act -a`;
      `netplan generate` + `networkctl reload` when netplan is present) and dies on failure.
      A port whose STP state is `disabled` with carrier 0 (peer down) is accepted and logged.
    - `pass3` forwarder→forwarder rule, by address rather than layer alone (addressed = any
      leg with an `ip` in the plan's `configure-cloud-init` `ip_config`; a host with no such op
      counts as addressed): an address-less forwarder (every leg L2) is never a destination —
      the bridges build in parallel; an addressed one waits on every address-less forwarder of
      its component plus every addressed one strictly nearer the router. Acyclic by
      construction. devlab: 9k01/9k02 now after 8325h-l/r, sw-3850, every bridge and ms120
      (the reverse 9k→8325h edges are gone); ms120 (mgmt SVI) after every bridge.
      `serve-vrrp-l3.sh` waits for the instance states to settle (both known, agreeing,
      unchanged > 4 s — the master-down interval; ≤ 30 s), re-reads after the peer pings, and
      re-checks the on-box sync-group split for ~8 s before dying.
    - `te egress`/gate: the router's node is resolved from its vmid
      (`adapter_proxmox.resolve.resolve_vm_node`, `/cluster/resources`), `--node`/
      `DEFAULT_NODE` only as the fallback when that lookup is unavailable. `timeout_s` is ONE
      budget across every exec of an `egress_live` call (the gate's 60 s spans `--show` +
      `--healthcheck`). `measure` is recorded only when the healthcheck's own `phase=` line
      matches `--show` (`egress_live.health_phase`); a destroyed deploy is refused; a failed
      flip under `--json` prints the complete read-back plus `error` (rc 1).
    - Twin-config: two legs pinning one address in a CIDR (anchor + `isolated:` extension
      included) are refused; `l2: false` + `ip: none` is refused as a contradiction.
    - Every non-corp-twin shipped plan is byte-identical to `010a0f29` (ops, inputs, edges);
      corp-twin/devlab plan_ids are unchanged (IR hash) while their recipe content and ordering
      edges changed (devlab 800 → 780 edges).
- **P1 — 9K01/9K02 VRRP L3 pair (BUILT).** `9k01`/`9k02` (first leg VLAN935 → default via
  hq-mx `.1`; second leg VLAN953 pinned `.2`/`.3`), `scripts/serve-vrrp-l3.sh` (apt
  keepalived, `ip_forward`, one sync group: VIP `10.180.93.254` on 935 + VIP `10.180.34.1` on
  953, persistent default via hq-mx). `vlan953`'s gateway is the VIP; hq-mx dropped its 953 leg
  and carries `scripts/serve-hq-mx-routes.sh` (`10.180.34.0/25 via 10.180.93.254`, a boot
  unit).
- **P2 — L2 fabric as bridge VMs (BUILT).** §5.3's table; `scripts/serve-l2switch.sh` (one
  script, per-device port table in leg order — pinned against both twins' leg order by a test;
  STP on and the bridge up BEFORE any port is enslaved). Hosts moved to `isolated` access
  segments that keep the `vlanN` names. `tap/*.yaml` rewritten (§9).
- **D — `twins/corp-twin-devlab/` (BUILT).** hq-mx, sink, all 12 device VMs, and one host per
  distinct PATH (19 VMs): `ep-vlan20` (MS120 access), `ep-vlan64` (via the SGS-5240 trunk),
  `ep-vlan48` (a two-VLAN trunk), `ep-vlan32-cut` (behind the admin-down P17 — the negative
  test), `web-vlan953` (nginx over apt through the VRRP pair). Recipes are referenced as
  `../corp-twin/scripts/…` (no copy — they cannot drift; §12 D5); one persona copied. Minimal
  `tap/default.yaml` (wan1-isp, sfp25, sfp26) and `tfc/default.yaml` (the three reachable
  endpoints). Purpose: the first live verify (§11, devlab first).
- **Remaining VyOS path (was S3–S6), each gated on §12 D1:** S3 VyOS egress phase (vbash
  `egress_render` branch + drop the plan-time refusal); S4 an IR-level switch kind / bridge
  intent; S5 IR-level link state; S6 VRRP + inter-router routing on `configure-router` and a
  per-router "no WAN" option. The device-parity round delivers their wire behaviour without
  them; they remain the route to real VyOS devices.

## 11. Live-verify runbook (S2 — gated; the orchestrator runs it, not the code-writer)

0. **Preconditions.** Target `dev45` (`adapter.conf` points at it). `svc-engines-24131435` is
   standing — do not touch it. Capacity: ~147 VMs (133 hosts + `hq-mx` + `sink` + 12 device VMs, ~7 GiB RAM for the devices); the disco twin ran
   149 on this cluster. Regenerate the sink payload if the corpus or `serve-sink.sh` changed:
   `sh twins/corp-twin/scripts/build-sink-payload.sh`.
1. **Deploy te:** `axrange te plan --config corp-twin --images deploy-dev45` (record `plan_id`),
   then `axrange te deploy --config corp-twin --images deploy-dev45`. The twin deploys in
   `egress: provision` (apt needs it).
2. **Contain before anything else:** `axrange te egress corp-twin --phase measure` — flips
   hq-mx (`axr-egress-phase.sh measure` over QGA), reads back `--show` + `--healthcheck`, and
   records the live phase on the configure-router row. It must print **`recorded
   egress_phase=measure -- CONTAINED`**, and `axrange te info <deploy>` then prints `measure --
   CONTAINED (declared: provision)`. Nothing below runs until it does. After any hand flip on the
   router, `axrange te egress corp-twin --show` re-reads and re-records. **Gate:** steps 6–7's
   `tap deploy` / `tfc deploy` re-read the router LIVE (`--show` + `--healthcheck` over QGA,
   recorded on the same row) and refuse unless it is an asserted `measure`
   (`measure-unverified` = a failed healthcheck, `unknown-unverified` = unreadable — both NOT
   contained); `--allow-uncontained` overrides. (The 2026-09-25 caveat — a hand flip never reached the record — is closed by
   `te egress`.)
3. **Status:** `axrange te status <deploy>` — every op COMPLETED; note any gated-out warnings.
4. **Router / VLAN gateway checks** (on `hq-mx`): `ip -br addr` shows one leg per emitted
   segment (26 at the default gate: `wan1-isp`/`wan2-isp`, the 11 `mx-vlanN`, 13 lab /24s) with
   the `.1` gateway and `10.250.234.65` on `wan1-isp`, `10.251.234.78` on `wan2-isp`;
   `ip route show 10.180.34.0/25` → `via 10.180.93.254`. From one host per VLAN, `ping -c3 <its
   gateway>` and a cross-VLAN ping (a `vlan64` host → the DC on `vlan20`; a `vlan953` web tier
   → a `vlan64` host). The device checks are D2–D6 below (run them on the devlab first).
5. **Containment proof:** from any host, `curl -sk https://github.com -o /dev/null -w '%{ssl_verify_result}'`
   and read the served certificate issuer — it must be the **lab CA**, never a public CA (the
   2026-09-21 Sectigo check). WAN drop counters on `hq-mx` must not climb from twin traffic.
6. **Taps:** `axrange tap validate --twin corp-twin` → `axrange tap plan --twin corp-twin` →
   `axrange tap deploy --twin corp-twin`. Per mirror point, count frames: on XPress-SW-A the gretap
   counters per key (one per bound segment — `wan1-isp`/`wan2-isp` = External TAP, the rest = MS120
   SPAN); `tap info` for the binding → key map. Expected: `wan1-isp` > 0 once traffic runs,
   `wan2-isp` ≈ 0 (standby), every populated VLAN > 0.
7. **Traffic:** `axrange tfc validate --twin corp-twin --images deploy-dev45` → `tfc deploy --twin corp-twin`
   (`--variant gate90` for both at the 0.9 gate); `tfc status`
   — every persona instance running. After one capture window, compare the pcap/axquery mix against
   §7 (DNS flow share, TLS byte share, SMB internal volume, Kerberos/LDAP present) and record deltas.
8. **Destroy or leave:** the user's call. `axrange tfc destroy` → `tap destroy` → `te destroy
   <deploy>` (cascades tap). Never `destroy` without naming the corp-twin deploy id.

### 11.D Devlab first (`corp-twin-devlab`, 19 VMs) — run BEFORE the full twin

The device fabric has never run. Prove it on 19 VMs, then deploy the 147-VM twin. Same
preconditions as step 0 (sink payload generated; `svc-engines-24131435` untouched).

- **D0 Deploy:** `axrange te plan --config corp-twin-devlab --images deploy-dev45` (expect
  `corp-twin-devlab-881da2e4`, 320 ops live — the plan grew past the 267 first written here, 19 VMs), then `te deploy`. Every op COMPLETED (the
  switch recipes print `AXR_L2SWITCH_OK`, the 9Ks `AXR_VRRP_OK`, hq-mx `AXR_HQ_MX_ROUTES_OK`).
- **D1 Boot shape (G18):** on `mx-sw`, `ip -br link` — the bridge ports are UP and enslaved to
  `br0`, `ip -4 addr` shows NO address on any of them; `journalctl -b | grep -i dhcp` shows no
  per-port DHCP wait. If PVE did emit DHCP for the address-less NICs, record it (G18). The
  switch images are Debian 12 with **netplan** (rendered to systemd-networkd), not ifupdown:
  `ifquery` does not apply. The recipe validates its rewrite with `netplan generate`, and its
  log carries that result.
- **D2 Port map (G16):** each recipe's log (`run-script` output) prints `netN ifname MAC ->
  segment`; compare against `qm config <vmid> | grep ^net` on the PVE host for `mx-sw` (13 NICs,
  spans the pci.1 bridge) and `ms120`. Any mismatch is a stop.
- **D3 STP:** on `ms120`, `bridge -d link show` → the `sfp26` port `state blocking`, `sfp25`
  `forwarding`; on `mx-sw` both `forwarding` (root: `cat /sys/class/net/br0/bridge/root_id` ==
  its own `bridge_id`). Loop guard: on the PVE host `tcpdump -nei <sfp26 vnet bridge> not stp`
  shows a one-way flood only (mx-sw's forwarding end floods broadcast/unknown-unicast in) and
  no echo back out of the blocking end. A frame returning on sfp26 is a loop.
- **D4 802.1Q on the trunk:** on the PVE host, `tcpdump -nei <sfp25 vnet bridge> vlan` while
  `ep-vlan64` pings its gateway → frames tagged **`vlan 64`**; `ep-vlan48` → `vlan 48`; VLAN1
  untagged. MTU: from `ep-vlan64`, `ping -M do -s 1472 10.180.64.1` succeeds (a full 1500-byte
  packet crosses two tagged trunks).
- **D5 VRRP:** exactly one 9K holds BOTH VIPs (`ip -br addr` on each: `10.180.93.254` and
  `10.180.34.1` on the same box — `9k01` while healthy). Start `ping 10.250.234.66` (or a
  `curl` loop to the sink) from `web-vlan953`; `systemctl stop keepalived` on the master →
  both VIPs move to the peer within ~3 s and the ping loses ≤ a few packets; restart → preempt
  back. VRRP adverts (`tcpdump -ni <x3850-9k01 bridge> vrrp`) show VRID 35/53.
- **D6 Down port + reachability:** `ep-vlan32-cut` cannot ping `10.180.32.1` (P17 is admin-down
  on the MS120); `ep-vlan20`, `ep-vlan64`, `ep-vlan48` reach their gateways, each other, and
  `web-vlan953` (its `nginx` answered on 443 — its apt already proved the 953 → VIP → hq-mx →
  NAT path in `provision`).
- **D7 Containment:** `axrange te egress corp-twin-devlab --phase measure` → `CONTAINED`;
  `te info` agrees; from `ep-vlan64`, `curl -sk https://github.com` reaches the SINK (lab-CA
  certificate) — through the whole fabric; hq-mx WAN drop counters do not climb. Only then
  `tap deploy --twin corp-twin-devlab` (wan1-isp, sfp25, sfp26 → XPress-SW-A; sfp25 frames
  tagged) and `tfc deploy --twin corp-twin-devlab`.
- **D8 Destroy** the devlab (`tfc destroy` → `tap destroy` → `te destroy <devlab deploy>`)
  before the full twin, unless the user says otherwise.

**Live run 1 — 2026-09-26, dev45, deploy `corp-twin-devlab-f2cbf88e` (scripts from `010a0f29`,
before fix round 2). FAILED at D0.** Two `run-script` ops failed after 3 attempts each; all 12
downstream ops halted (the 9 other switch recipes, the 2 VRRP recipes, web-vlan953). The
diagnosis below came from the state DB and read-only QGA reads in the guests.
Implementation (Claude Code).

- **`run-script[ms120-app]`, exit 1 (vmid 225873).** The recorded message was
  `(stdout or stderr)[:300]`, i.e. the first 300 characters of the port log. The `FAIL`
  line was on stderr and was dropped. Guest state after the third attempt: every port
  enslaved and STP converged (`sfp26`/ens20 `blocking`, the rest `forwarding`, P17
  admin-down), the mgmt SVI `br0.20` held 10.180.16.15/20, **and the bridge port `eth0`
  also held 10.180.16.15/20.** So the recipe's final no-address assertion died. Cause:
  Debian 12 renders netplan into systemd-networkd, and networkd keeps the boot-time
  `.network` (static address) in memory. The recipe rewrote the netplan file but never
  made networkd reload it. It then flushed eth0 and enslaved it (down, master, up). The
  carrier-up made networkd re-apply the old static address. The journal shows
  `eth0: Configuring with /run/systemd/network/10-netplan-eth0.network` after the br0
  teardown, and `networkctl` shows eth0 `configuring`. `/run` did hold the new
  address-less file, but only because the recipe's `systemctl daemon-reload` re-ran
  netplan's generator. networkd never re-read it. Round 2's `networkctl reload` would
  probably have covered a first apply, but it ran only when the file changed and
  swallowed its own failure (`|| true`).
  **Fix** (`twins/corp-twin/scripts/serve-l2switch.sh`): pass 1 rewrites every port's
  config. Then one unconditional `netplan generate` + `networkctl reload` runs and fails
  loud. Only then does pass 2 flush and enslave the ports. The reload is unconditional
  because an earlier apply may have rewritten the file and died, exactly this guest's
  state. Netplan ports are also marked `optional: true`, so wait-online never waits on an
  address-less port. The netplan loader now reads an all-digit MAC as a string: YAML 1.1
  parsed `52:54:00:00:00:01` as a base-60 int, and the re-dump would have corrupted
  another NIC's match. The address assertion now names networkd.
- **`run-script[sink-app]`, exit 100 on the 3rd attempt (vmid 399142).** The three
  attempts failed differently, for two reasons:
  1. *Attempt 1 timed out at 600 s. Attempt 2 was orphaned* (`exec-status: Invalid
     parameter 'pid'`). The shared recipe's nftables heredoc is **unquoted** (`$AXR_IP`
     must expand), and a comment in it quoted `` `tcpdump` ``, `` `nstat …` ``,
     `` `ip route get` ``, `` `local` `` and `` `ip daddr != $AXR_IP` `` in backticks. The
     shell ran each one as a command substitution, so writing `nftables.conf` ran
     **tcpdump, forever**. The evidence: journal `eth0 entered promiscuous mode` right
     after the ssh restart, AppArmor `profile="tcpdump"` denials, no further unit starts,
     and a 0-byte `nftables.conf`. The bug came in with `f20b9825` (2026-09-23, the
     served-port redirect comment). That fix was measured live by hand, and the edited
     recipe was never re-run end to end until now.
  2. *Attempt 3 hit apt exit 100.* `apt-daily-upgrade.timer` (Persistent, last run at
     image bake) fired at 11:36:14, about 15 min after boot. unattended-upgrades upgraded
     27 packages, including **qemu-guest-agent**. The QGA restart at 11:37:35 orphaned
     attempt 2. It also upgraded `ca-certificates` at 11:37:50–55, which overlapped
     attempt 3 (11:37:52–56). The recipe's `axr_pkg` saw it as not `install ok installed`
     and ran `apt-get update` into the held lock, which exits 100.

  Egress and DNS were **not** at fault. The ansible `provision-app` step installed
  bind9/caddy/chrony/nftables over hq-mx's `provision` egress at 11:25:42–59, and the
  sink resolves `deb.debian.org` via 1.1.1.1. Containment was not touched.
  **Fix** (`twins/_shared/scripts/serve-sink.sh`, corp-twin's `provision-sink.sh` rebuilt
  with `build-sink-payload.sh`, the corpus byte-identical): the backticks are gone from the
  heredoc, with a warning beside it. A new `axr_apt_quiesce` disables
  `apt-daily{,-upgrade}.timer` and waits up to 900 s for a run already in flight before the
  first package step. Both `apt-get` calls carry `-o DPkg::Lock::Timeout=300`.
- **Engine (`axrange/config_exec/backend.py`):** a failed `run-script` or `provision-app`
  now records the stderr tail plus the stdout tail, not the stdout head. The "shebang"
  hint now appears only for exit 126/127. No plan changes.
- **Tests:** `tests/test_twin_recipe_heredocs.py` fails on an unescaped backtick in an
  unquoted heredoc in any twin or target-environment recipe. It also checks that corp-twin's
  payload embeds the current shared recipe, and that the sink quiesces apt before its first
  package step. `tests/test_corp_twin_device_parity.py` covers the netplan rewrite and the
  rewrite → reload → flush/enslave order. `tests/test_config_exec.py` covers the stderr tail.
- **Open:** `twins/svc-engines/scripts/provision-sink.sh` still embeds the old shared recipe
  with the tcpdump heredoc. It is left as built for the live `svc-engines-24131435` and
  excluded from the heredoc test. Rebuild it (`sh twins/svc-engines/scripts/build-sink-payload.sh`)
  before svc-engines' next deploy. The ansible `provision-app` apt step races apt-daily the
  same way on every twin, and this run won that race only by timing. The general fix is to
  mask the timers in the cloud-init userdata or in the image bake. That is an engine and
  image change, not made here.
- **Rerun:** destroy and redeploy from scratch. Both guests carry state from the failed
  attempts: ms120 is half-applied, and the sink has had its timers and packages changed by
  unattended-upgrades. The plan's script content changed as well, so a resume would not
  match the plan anyway.

**Live run 2 — 2026-09-28, dev45, deploy `corp-twin-devlab-f13c3cca` (`e2fe96ac`). D0
COMPLETED; D7 recorded `measure-unverified`.** `te egress corp-twin-devlab --phase measure`
applied the policy, but hq-mx's (vmid 254827) `--healthcheck` printed
`FAIL 203.0.113.9 from eth0 does NOT route to the sink 10.250.234.66`. The run stayed fail-closed
(WAN dropped). Diagnosed from read-only QGA reads. Implementation (Claude Code).

- **The policy was right; the probe was wrong.** hq-mx carried rules 999/1000/1001 on all 12
  LAN legs (eth0–eth11; the WAN is eth12). Table 210 held `default via 10.250.234.66 dev eth0`.
  eth0 is `wan1-isp`, the sink's own /28. The probe ran
  `ip route get 203.0.113.9 iif <first LAN leg> from <that leg's router address>`. On this
  kernel (6.1, `accept_local=0`) a packet arriving with one of the router's OWN addresses as
  its source is a martian, so the command answers `RTNETLINK answers: Invalid argument` on
  **every** leg (measured: eth0, eth1 and eth11 all EINVAL). With a foreign source on the same
  leg (`from 10.250.234.70 iif eth0`) it answers `via 10.250.234.66 dev eth0 table 210`. So
  neither the forwarder changes, the `serve-hq-mx-routes.sh` return route, nor eth0 being the
  sink segment played any part. The assertion could never pass on a Linux router. svc-engines
  hit the same thing (PRODUCT_GAPS, "Also note"); it had been read as a kernel-portability
  risk.
- **Fix** (`axrange/adapter_proxmox/egress_render.py`, the constant in-guest engine): the new
  `axr_probe_src <leg>` returns the lowest host address on the leg's connected prefix that is
  neither a router address nor the sink (rule 999 legitimately exempts the sink). A /31
  counts both ends; a /32 yields nothing. `axr_assert_measure` now probes **every** LAN leg,
  where it used to probe only the first. It prints `ok`/`FAIL` per leg and `skip` for a /32
  leg, and zero probeable legs is a FAIL. The containment claim got stronger: one leg
  missing its rules was invisible before. The unused `axr_lan_ip` was removed. The new
  assertion was run read-only on hq-mx (functions only, the WAN cache pointed at
  `/dev/null`): all 12 legs `ok … -> sink 10.250.234.66`, rc 0.
- **Tests** (`tests/test_egress_phase.py`): the assertion is now executed against a fake
  `ip` that returns EINVAL for a local source, the way the kernel does. The tests cover a
  steered router passing, the sink segment and /31 source choice, the /32 skip, the WAN
  excluded, one unsteered leg failing, and no probeable leg failing. Full suite 3779 passed /
  13 skipped. `docs/SLICE_ROUTER.md`'s verifying paragraph was updated to match.
- **Plans unchanged.** The engine text is rendered in the adapter (`op_configure_router`),
  not carried in any op input, so neither corp-twin's nor svc-engines' plan changes. The
  standing `svc-engines-24131435` gateway still runs the OLD engine, so its `--healthcheck`
  in `measure` would give the same false FAIL until its engine is replaced. It was left
  untouched.
- **Applying to the running devlab, no redeploy.** `te egress` only calls the installed
  engine, and `configure-router` is completed and will not re-run. Replace the engine file
  (conf, state and phase stay as they are), then re-run the flip. The flip re-applies
  `measure` idempotently and records the read-back:

      .venv/bin/python -c 'import argparse; from axrange.cli.main import _egress_guest_agent; from axrange.adapter_proxmox.guest_agent import GuestAgent; from axrange.adapter_proxmox.egress_render import EGRESS_ENGINE, render_engine; g=_egress_guest_agent(argparse.Namespace(adapter_config=None, node=None)); GuestAgent(g.client, g.node_for(254827)).write_file(254827, EGRESS_ENGINE, render_engine(), mode="0755")'
      axrange te egress corp-twin-devlab --phase measure    # expect CONTAINED

**Live run 2 — PASS (2026-09-28, dev45, `corp-twin-devlab-f13c3cca`).** After the engine
replacement above (fix `ccb404b5`), D1–D7 all passed. Implementation (Claude Code).

- **D1/D2 fabric:** every switch recipe completed; bridge ports enslaved and address-less; the
  port maps matched `qm config`.
- **D3 STP:** `sfp26` blocking on the MS120, `sfp25` forwarding.
- **D4 802.1Q:** frames on the `sfp25` vnet carried their real VLAN tags. MTU: `ping -M do -s
  1472` crossed both tagged trunks.
- **D5 VRRP:** `9k01` MASTER holding both VIPs. Failover: 60/60 pings, zero loss, and preempt
  back after the restart.
- **D6:** reachability as specified; `ep-vlan32-cut` isolated (P17 admin-down).
- **D7 containment:** `CONTAINED`, with the healthcheck `ok` on all 12 LAN legs. From the
  fabric, `https://github.com` was served by the sink (issuer `axRange sink lab CA - ECC
  Intermediate`). With the tap plan and three personas running, the XPress-SW-A gretap deltas
  per 60 s were sfp25 125, sfp26 95, wan1-isp 22 packets.
- **D8:** the devlab was destroyed afterwards to free RAM for the full twin. dev45 cannot hold
  the full twin at the default gate (about 147 VMs, about 250 GiB), so it is deployed at
  `--param DEPLOY_MIN_OCCUPANCY=0.9` (964 ops, 106 VMs). Apply `--variant gate90` for its tap
  and tfc tiers (`tap/gate90.yaml`, `tfc/gate90.yaml`), because the defaults name hosts and a
  segment that the 0.9 gate removes.

**Live run 3 — full twin at gate 0.9 (2026-09-28, dev45, `corp-twin-07cfcdb2`, tfc
`tfc-corp-twin-07cfcdb2-785b1a06`, profile `tfc/gate90.yaml`): 79/83 `drive-persona` ops
completed, 4 failed.** Diagnosed read-only (state DB, QGA reads); implementation (Claude Code).

- **Symptom.** All four failures were Windows corp-workstations (win11-finance@v4 — on dev45
  the Server 2022 template, `ver` = 10.0.20348, G11): `corp-workstation-heavy-10-80-20-core`
  (`cast-corp-workstation-heavy-local`) and `corp-workstation-light-10-80-{64,78,79}-core`
  (`cast-corp-workstation-light-local`). Every attempt: `agent/exec: HTTP 500 Agent error:
  Failed to execute child process (No such file or directory)`. A QGA `sh -c true` on vmid
  298496 reproduces the exact error; `where sh python3 bash` finds nothing.
- **Root cause: persona content.** The cast shipped the heavy/light corp-workstation variants
  with `os_family: linux`, while the plain `cast-corp-workstation(-local)` is `windows` (which is
  why the six hosts running it succeeded). Every driver renders for `persona.os_family`, never
  for the host OS, so the scripted driver launched its Linux client (`sh`/`python3`) over the
  Windows QGA. Nothing checked the binding: the inventory's `os` is `None` unless manifests are
  passed, and no tfc verb passed them. `twins/pit-395a61f04e-1780405200/` carries the same four
  personas with the same bug (its `tfc/cast.yaml` binds them the same way); the upstream cause is
  aximprint's cast compiler, which emits `linux` for the heavy/light variants of a Windows role.
- **Fix, content.** `cast-corp-workstation-{heavy-local,light-local,heavy,light}` are
  `os_family: windows` in both twins (the non-local two are unbound today but describe the same
  Windows role). The Windows scripted client renders all their emits (UDP, multicast, SSH-port
  TCP included); PSN validation is clean.
- **Fix, engine.** `resolve_traffic_bindings` refuses, with one message naming every host, any
  assignment whose persona `os_family` differs from the host OS — the manifest OS when `--images`
  is given, else the twin plan's own recorded `wait-os-ready` `os_family` when it is `windows`
  (never fabricated: pass1_emit's fallback is `linux`, so a recorded `linux` is not trusted).
  That covers `tfc validate`/`plan`/`deploy` against a live twin with no extra flag. Offline,
  `tfc validate --images <root>` on a profile inside a twin folder checks every assignment
  against the image OS of the hosts `te.yaml` declares (no gate, no state DB) and returns 1 on a
  mismatch; without `--images` it warns that the check was skipped. `--images` was added to
  `tfc validate|plan|deploy`.
- **Verified.** Before the content fix, `tfc validate --twin corp-twin --variant gate90` refused
  exactly the four bindings, both live and offline (`--images deploy-dev45 --db /nonexistent`).
  After it: static check OK over 204 declared hosts, resolve PASS. `tests/test_tfc_persona_os.py`
  (35), including a strict guard over every shipped twin's tfc profiles with the fixture
  manifests. That guard found GAP-111: sme-flat and ics-ot bind the Windows catalog
  `corp-workstation` persona onto Debian hosts. Full suite 3817 passed / 13 skipped.
- **Re-applying.** `tfc deploy` re-asserts every op (all are force-reset to `pending`), so
  re-running the same profile is idempotent and finishes the four; no `tfc destroy` is needed.
  Do NOT apply a profile holding only the four: a narrowed re-apply stops every dropped
  instance (`_stop_dropped_instances`). Contain first (the gate re-reads hq-mx), then
  `axrange tfc deploy --twin corp-twin --variant gate90 --images deploy-dev45`.

**Live run 3 — mirror loss on the tap tier (2026-09-28, tap `tap-corp-twin-07cfcdb2-8176c5ad`,
24 segments → `XPress-SW-A` at `10.255.251.21` over `o2v7dc98`).** Diagnosed read-only on dev45;
fixed in the adapter. Implementation (Claude Code).

- **Symptom.** gretap TX loss after the mirror: `tx_errors` on the untagged segments (wan1-isp
  `tapg_ceecb9` ~30%, vlan20 `tapg_9a661b`, lab-10-20-200 `tapg_c904f2`, lab-10-20-193
  `tapg_f203d3`, …) and `tx_dropped` on the trunk segments (sfp25 `tapg_c51e81` ~17%,
  t-ms120-sgs5240 `tapg_b1be78`, -labsw `tapg_b69f6b`, -3850 `tapg_d9837a`, -axeprod
  `tapg_852660`). The gretaps' pfifo qdiscs dropped **0** — qdisc `Sent` = link TX packets +
  `tx_dropped` — so the loss is inside the tunnel's xmit, not the queue (qlen 20000 is fine).
  GSO/TSO/GRO were already off (not a GSO issue).
- **Root cause: outer DF over a 1500-MTU transport.** `o2v7dc98` and the sensor's `tap104i1` are
  MTU 1500; a full-size frame encapsulates to 1542 B (1546 B tagged). The gretaps had the kernel
  default `pmtudisc` (outer DF). Inner IPv4 with DF (TCP bulk, e.g. 1514 B segments to
  `10.250.234.66:443` on wan1-isp) fails `tnl_update_pmtu` → E2BIG → `tx_errors`. 802.1Q frames
  (sfp25 carries vlan 64/935 tagged 1518 B frames) skip that check, then the outer-DF packet fails
  `ip_fragment` → `tx_dropped`. Proof: over 20 s the summed `tx_dropped` delta (44,040) equalled
  the host's `IpFragFails` delta (43,987 + ICMP to self); `ip route get 10.255.251.21` → `dev
  o2v7dc98`, neighbour REACHABLE (not ARP). The frag-needed ICMP has no sender to reach — a mirror
  is fire-and-forget — so the loss was silent.
- **Reproduced off-cluster.** A netns rig (veth "VM" → clsact mirred → gretap → veth → `gretap
  external` sensor, 1500 MTU): 50 untagged DF + 100 tagged full-size frames → gretap `tx_errors`
  50, `tx_dropped` 100, sensor 0 of them. With `nopmtudisc ignore-df`: all 150 delivered.
- **Fix.** `attach-gre-tunnel` creates the gretap `… key <k> nopmtudisc ignore-df` and, on the
  adopt path, asserts `ip link set <dev> type gretap nopmtudisc ignore-df` (changelink: same
  ifindex, so the tc mirred filters are untouched; remote/key preserved; idempotent; fatal on
  failure). The transport fragments; the sensor's kernel reassembles before `gre-cap` decap, so
  capture still sees whole frames. No plan change (op inputs identical → same plan id / JSON).
  Tests: `tests/test_adapter_proxmox.py::test_attach_gre_tunnel_{creates_with_outer_df_off,heals_df_policy_on_adopted_netdev}`.
  Residual: the transport is still 1500, so full-size frames cost two transport packets
  (GAP-112).
- **Apply to the standing tap.** Re-apply the same plan: `axrange tap deploy --twin corp-twin
  --variant gate90` (after containment, as before). Every tap apply force-resets all composed ops
  to `pending`, so each `attach-gre-tunnel` re-runs, adopts its gretap and changelinks it; no
  `tap destroy` needed. `install-ovs-mirror` also re-runs (tc re-install: a sub-second capture
  blip per NIC). Do NOT apply a narrower profile — a narrowing apply reverts dropped hosts.
- **Re-measure.** On dev45: `ip -d link show tapg_ceecb9 | grep -o 'nopmtudisc ignore-df'` for
  every `tapg_*` of the deploy; then a 20–60 s window of per-gretap `tx_packets/tx_errors/
  tx_dropped` deltas (`/sys/class/net/<g>/statistics`) with `nstat -n` before and `nstat` after.
  Expect `tx_errors` and `tx_dropped` deltas **0** on all 24, `IpFragFails` 0, and `IpFragCreates`
  / `IpFragOKs` rising with full-size traffic. On XPress-SW-A: `gre-cap` RX rising, `nstat`
  `IpReasmOKs` rising, `IpReasmFails` ≈ 0. Also watch `tap104i1` `tx_dropped` (the sensor VM's
  virtio ring, a separate pre-existing loss point: 4.39M cumulative, 0 in the diagnosis window);
  fragmentation raises its packet rate.

**Live run 3 — traffic mix vs §7 (2026-09-28, 60 s capture of five segments, all 83 personas
running on `corp-twin-07cfcdb2`).** Measured off §7; fixed in persona content. Implementation
(Claude Code).

- **Measured.** NTP + NetBIOS-DGM (udp/138) were 48–72 % of the bytes on wan1-isp, sfp25 and
  t-ms120-sgs5240 (§7: < 1 %). The top talker everywhere was `10.180.64.21`, the file server, at
  ~37 Mbit/s. SMB had one flow (§7: 2.2 GB, ~25 %). On wan1, TLS was 17.8 % and HTTP 17.3 % (§7:
  ~70 % and 2.5 %, about 28:1), and QUIC was 15.5 % (§7: 0.15 %). DNS frames averaged ~737 B, and
  vlan20 was 49 % 1400 B "NTP" frames to the DC.
- **Root cause: persona content.** The scripted driver offers `messages × bytes × peers` per
  firing, and the cast's `volume:` levels are the whole lever. `cast-file-server` was two `bulk`
  UDP emits (ntp/123 and udp/138 to the sink, 64 MiB each every 32 s). Every `ntp`/`dns` emit
  sent the volume tier's payload (256 B to 64 KiB, chunked into 1400 B datagrams). The heavy
  endpoints drove HTTP, HTTPS and QUIC all at `high`. The only SMB emit went to the sink, where
  nothing serves 445. A static model of the old profile reproduced the capture: NTP 23 %,
  udp/138 16 %, HTTP 20 %, TLS 20 %, QUIC 12 %. It also predicted 638 MB/min external against
  571 MB measured on wan1.
- **Fix, content** (`twins/corp-twin/personas/`, each file's header names its change):
  - `cast-file-server` is one 48 B NTP poll to the site resolver every 64 s.
  - The file server's bytes are its clients' SMB. The new persona `corp-file-share-client`
    dials `file-server:445` at 64 × 512 KiB every 30 s. It is assigned to
    `generic-endpoint-heavy-10-80-20-core01`, standing in for §7's one 10.80.20 SMB client, and
    this adds one assignment to `tfc/default.yaml` (122) and `tfc/gate90.yaml` (84).
  - Every NTP message is 48 B at `low`, and every DNS message is 64 B (volume kept, so the flow
    count is kept). `cast-generic-endpoint` DNS goes low → med, which makes DNS the flow
    plurality.
  - QUIC (`https`/udp) is `low` at 1200 B. HTTP is `low`, except `med` on the heavy
    endpoints/workstations and the web tier. Workstation HTTPS goes med → high.
  - The workstation's SMB is repointed from the sink to the DC (§7's workstation SMB target).
  - Workstation segment chatter goes med → low, and web-tier SSH uses 1 KiB messages.
- **Modelled mix after the fix, gate 0.9** (`tests/test_corp_twin_mix.py`): TLS 70.1 %, SMB
  25.6 %, HTTP 2.4 %, LDAP 0.8 %, SSH 0.5 %, DNS 0.15 % of bytes but 30.5 % of flows (the
  plurality), QUIC 0.05 %, NTP 0.01 %. That is ~280 MB/min offered (~37 Mbit/s, down from ~111),
  73 % of it external. At the default gate: TLS 77 %, SMB 19 %. The test pins these within
  tolerance for both profiles. It also pins that NTP messages are 48 B, DNS messages are ≤ 512 B,
  `bulk` is used only for SMB/HTTPS, and the file server is dialled on 445 and emits only NTP.
- **Unverified.** The SMB bytes rely on the file server's **smbd** owning 445 (read-only `ss
  -ltnp` on vmid 799034 shows it; the generated echo responder skipped the port with
  EADDRINUSE). They also rely on smbd draining a tagged, non-NBSS-session stream instead of
  dropping the connection. Run 4 must see SMB at ~20–30 % of bytes on the vlan64 and transit
  taps. If smbd drops the connection, the fallback is the `command` driver's real `smbclient`
  against a share, which needs a redeploy.
- **Not fixed: WAN-inbound background** (§7: 700 inbound edges to WAN1, mostly `other`, then
  TLS/ICMP/HTTP/SSH/RDP/telnet, ~0.03 % of bytes). There is no persona for it. A persona on the
  sink can only dial a role, and `peer_role: gw` resolves to hq-mx's first addressed leg in
  attach order (`10.100.21.1`, lab-10-0-21; `o2control.traffic._host_ip`), not WAN1
  (`10.250.234.65`). The wan1-isp leg is recorded in the inventory, but no persona field can
  select it. Emitting to the lab address would fabricate an external → lab edge (LES-001), so
  this is recorded as G24.
- **Other twin.** `twins/pit-395a61f04e-1780405200/personas/` carries the same cast bug but is
  not byte-identical: its personas dial `internet-sink-{1,2,4,8}`. It is left unchanged and
  needs the same rebalance if that twin is ever measured.
- **Re-apply.** Contain first, then `axrange tfc deploy --twin corp-twin --variant gate90
  --images deploy-dev45`. This is a whole-profile re-apply: it re-renders every instance and
  adds the one new instance. `tfc validate` with the same arguments passes (live resolve, 84
  assignments).

**Live run 4 — rebalanced mix (2026-09-28, dev45, `corp-twin-07cfcdb2`, gate 0.9, 106 VMs, all
tiers completed; tap `tap-corp-twin-07cfcdb2-8176c5ad`, tfc `tfc-corp-twin-07cfcdb2-785b1a06`,
84 personas).** Re-measured after the persona rebalance (CHANGELOG 2026-09-28 "corp-twin personas
rebalanced…") and the gretap DF fix. Implementation (Claude Code).

- **Containment.** `te egress` CONTAINED all 25 LAN legs.
- **wan1-isp**, 110 MB / 60 s: TLS 93.2% bytes / 38.7% flows, HTTP 5.1%, SSH 1.1%, DNS 0.3% bytes
  / **27.1% flows**, QUIC 0.1%, NTP ~0.
- **sfp25**, 97.6 MB: TLS 62.6%, SMB 26.7%, HTTP 4.6%, LDAP 1.9%, GC-LDAP 1.2%, Kerberos 1.1%,
  SSH 1.2%, DNS 0.3% bytes / 22.6% flows, NTP 0.1%.
- **t-ms120-sgs5240**, 50.6 MB: TLS 65.3%, SMB 25.8%, HTTP 5.5%. **vlan20**, 30.3 MB: TLS 49.5%,
  SMB 41.4%, LDAP 3.3%, Kerberos 1.9%. **lab-10-0-21**: DNS 94% of flows.
- **SMB to the file server is real**: bidirectional, 129–137 flows, ~26 MB/min; the top talker is
  `10.180.16.4` (`corp-file-share-client`) — the run 3 "unverified" concern (smbd owning 445
  actually draining the client's stream) is now confirmed live, not just at the socket layer.
- **Mirror loss**: corp-twin's own gretaps read **0 tx_errors / 0 tx_dropped** after the DF fix
  (before: ~30% on wan1, ~17% on sfp25 — CHANGELOG 2026-09-28 "Mirror gretaps carry outer DF
  off").
- **dev45 memory** with the full twin up: ~145 GiB available of 376.
- **Divergences from §7 remaining**: DNS is not the flow plurality on wan1 — TLS is (§7 predicted
  DNS would be, from the flow-count model alone; the byte-weighted mix now matches better than the
  flow-count one). No WAN-inbound background traffic (G24, unfixed — no persona field selects a
  specific leg to dial from). Gate 0.9 thins endpoints by ~1/3 versus the full 147-VM/default-gate
  twin, which has still not been deployed (§12, user's call).
- **Not corp-twin's own regression, but adjacent**: `svc-engines-24131435`'s two gretaps
  (`tapg_84e687`, `tapg_d1b207`) still lose ~1.49M frames/min each (`tx_errors`) — that twin
  predates the DF fix and has not had its tap re-applied. See GAP-112 ("standing twins do not
  self-heal") — healing is a `tap deploy` on that standing twin, the user's call, since it is
  otherwise live and also carries the stale sink recipe (GAP-110).

**Live run 5 — capture-rate ceiling (2026-10-01, dev45, `corp-twin-07cfcdb2`, gate 0.9, 106 VMs,
tap `tap-corp-twin-07cfcdb2-8176c5ad`, tfc `tfc-corp-twin-07cfcdb2-785b1a06`).** User-authorized
live ladder from run 4's rebalanced mix up to the highest offered load that stayed zero-drop and
protocol-healthy end to end. Implementation (Claude Code). Full record:
`docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`.

- **Baseline (B0, run 4's mix, re-measured):** 6,541 pps / 48.1 Mbit/s captured at XPress-SW-A,
  every drop counter 0, 84/84 heartbeats.
- **Ceiling (new variant `tfc/caprate-max.yaml`, ×6 the mix's offered rate):** **29,945 pps /
  244.2 Mbit/s**, 1,970 s soak, 59.0 M packets, every drop counter 0, 84/84 heartbeats, dev45 CPU
  ~27 %. Correctness within baseline noise across three 60 s samples; data retransmissions worst
  sample 1.07e-4 against a 1.09e-4 limit — a thin margin, not headroom.
- **Bottleneck chain, in the order met:** the persona generator's own loop cadence; transport
  fragmentation at the 1500 MTU transport vnet (fixed, corp-twin-only, by
  `twins/corp-twin/scripts/mirror-mtu-path.sh` — runtime state on dev45, lost on reboot/NIC
  replug/gretap re-create); fixing that exposed sensor GRO merging GRE-carried TCP into ≤3.7 KB
  frames and overrunning pxswcapture's single AF_XDP queue (fixed: GRO off on `ens19`/`gre-cap`,
  persisted); the Windows DC retransmitting once its three workstation personas scale (left
  unscaled, `gate90` ids kept in `tfc/caprate-max.yaml`); **the twin's single external sink (2
  vCPU caddy) sets the ceiling** — ×8 of the offered rate failed 1 of 3 samples, ×6 held clean.
  Probe traffic (not a mix candidate) proved the capture chain itself clean to 218 kpps / 1.96
  Gbit/s, first loss ~305 kpps (the single AF_XDP queue's limit).
- **Findings about the twin's traffic (not a regression — these were always true, newly
  measured):** caddy and smbd reset the personas' filler streams after the first window, so
  raising message size does nothing — only more/faster connections raise load;
  `corp-file-share-client` has 494,783 failed messages recorded since 2026-09-28 (run 4's "SMB is
  real" holds at the connection/socket layer, not for the intended multi-window transfer); the
  personas send tagged filler bytes on `https`, never a real TLS ClientHello, so TLS handshake
  completion is unmeasurable (0 in every sample, by design).
- **Independent event, not caused by this run, nothing touched:** svc-engines' two Windows VMs
  powered themselves off guest-initiated — `864812` (te-cbd9a1-dc) at 07:45:31 UTC, `506017`
  (te-cbd9a1-win-member) at 07:57:04 UTC (no PVE task, no OOM). VM 102 `o2-services` is also
  stopped (unknown since when). Left stopped, for the user.
- **Left running:** `tfc/caprate-max.yaml` is corp-twin's current applied traffic profile. Revert:
  `axrange tfc deploy --twin corp-twin --variant gate90 --images deploy-dev45`.

**Live run 6 — redeploy after the dev45 reboot (2026-10-05, dev45, te `corp-twin-3dae3188`, plan
`corp-twin-7de6524d`, gate 0.9, 106 VMs; tap `tap-corp-twin-3dae3188-2191b611`; tfc
`tfc-corp-twin-3dae3188-1d08cc84`).** User-requested restore of the destroyed twin with personas,
taps and the `caprate-max` profile. Implementation (Claude Code). No code changed.

- **Deploy:** te 1118/1118 ops (964 + 154 barriers), 0 failed; tap gate90 81/81; tfc caprate-max
  84/84 drive-persona including the four Windows corp-workstations. Plain `te deploy` gave a new
  deploy id (the resume lookup skips destroyed deploys; no `--fresh`). `te egress` CONTAINED, 25 LAN
  legs ok, before taps/traffic.
- **Retry:** `run-script[9k02-app]` attempt 1 exit 100 (dpkg lock held by unattended-upgr), attempt 2
  passed — the apt-daily race (GAP-109/LES-353), now in `serve-vrrp-l3.sh`, not only the sink.
- **Mirror path:** `mirror-mtu-path.sh apply` re-run; the 24 gretap names were identical under the new
  ids so the script held unedited (runtime-only, GAP-119). 60 s window: gretap tx errors/drops 0, no
  fragmentation, `gre-cap` rx ~29,720 pps / 244.4 Mbit/s, zero drops. Not the same measurement
  point as run 5's 29,945 pps (pxswcapture).
- **Not verified:** `pxswcapture` on XPress-SW-A was inactive (stopped 17:13 UTC, someone else's work
  on that VM, untouched), so capture to disk, the per-protocol mix and correctness ratios, hq-mx WAN
  drops and the lab-CA curl were not measured.
- **Application layer (~20:13 UTC):** 84/84 heartbeats; 34,935 of 248,510 messages failed (14.1 %,
  3.72 GB sent); 49 instances with 0 failures. `corp-file-share-client` 4352/4352 failed, 0 bytes
  (smbd `ECONNRESET`); heavy generic endpoints 44-48 % (caddy resetting the https filler) — GAP-120.
- **Left running:** all three deploys; revert traffic with `tfc deploy --variant gate90`.

## 12. Open decisions (user)

- **D1 — device parity vs containment.** Options were: (a) build S3 (VyOS egress in vbash)
  first, then S4–S6 — parity with containment, the most engine work, contract bumps on
  `IR_SCHEMA` + `OP_CATALOG`; (b) keep S1's single contained edge and add only S6's "no-WAN
  internal router + static routes" for the 9K pair as Debian routers; (c) accept S1 as the corp
  twin. **Interim, 2026-09-26 (implementation (Claude Code), the orchestrator's call while the
  user was away — reversible, content-level): a fourth option, (d), is BUILT** — every
  internal device is a Debian `kind: host` VM with a role recipe (§4.1), hq-mx the only router.
  It delivers the wire behaviour of S4–S6 (bridges, 802.1Q trunks, STP, a down port, VRRP, a
  routed hop) with no contract change, but NOT the ruling's letter ("VyOS VMs"). **Still for
  the user:** accept (d) as parity, or order (a) — (d)'s topology is already device-shaped, so
  (a) becomes an image + recipe swap per device plus the contract work.
- **D5 — where shared recipes live.** The devlab references `../corp-twin/scripts/` (no copy,
  no drift). The consolidation work order's Phase-2 `_shared/` layout is the natural home for
  `serve-l2switch.sh`/`serve-vrrp-l3.sh` once a third twin wants them; decide with Phase 2.
- **D6 — the access-switch VLAN placement (G17)** is imputed; a switch config export (with D2)
  would replace it.
- **D2 — VLAN ↔ subnet plan (§5.1).** The contiguous 10.80.0.0–79.255 reading is inferred from
  two anchors; a switch/MX config export (an `imported` source) would settle VLANs 1/20/40/48/52/
  192/880/1010/935 and override the inference.
- **D3 — the cast's `peer_role: internet-sink-N` → `sink` rewrite** is a twin-local persona edit;
  aximprint's compiler still emits the four-sink shape. **Now has a measured reason (2026-10-01
  capture-rate run, §11 live run 5):** the twin's single external sink (2 vCPU caddy) is what caps
  this mix at ~30 kpps / 244 Mbit/s — ×8 the offered rate already failed 1 of 3 samples on sink
  retransmissions, while the capture chain itself held clean to 218 kpps. More sinks (the cast's
  original four `internet-sink-N`) or more sink vCPUs would move the ceiling, not another capture
  or mirror-path change. See `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`,
  `docs/PRODUCT_GAPS.md` GAP-121.
- **New divergence (2026-10-01 capture-rate run): the personas' traffic is not protocol-faithful
  under load.** caddy and smbd reset the scripted personas' filler streams after the first window
  — `corp-file-share-client` has 494,783 failed messages recorded since 2026-09-28, so run 4's
  "SMB is real" (§11) holds on the wire (a real smbd owns 445 and drains the stream), not for the
  intended multi-window transfer. The `https` personas never send a real TLS ClientHello (tagged
  filler bytes instead), so TLS handshake completion is unmeasurable, not just unmeasured. See
  `docs/PRODUCT_GAPS.md` GAP-120 and `docs/WORK_ORDER_protocol_faithful_traffic.md` (the built
  `drive: command` driver is the fix path, not yet applied to corp-twin's personas).

## 13. Out of scope

The Console canvas layout for this twin (`generated/layout.json` is not written); Phase 2's
migration of the other twins; an `o2.yaml` (the twin uses the standing sensors); an `exr/` exercise.
