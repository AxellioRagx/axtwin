# Fabric slice — live verification runbook

**Why this exists.** The entire fabric slice (`docs/design/04-networking-capture.md`) — IS-IS, OSPF,
iBGP-EVPN, eBGP, VXLAN, BFD, redistribution, and symmetric **IRB + egress** — is **implemented and
unit/plan-verified but has NOT been run on hardware**. The base routing templates were ported from
the live-verified **dtDataCenter** lab, but **IRB and egress have no dtDataCenter reference and are
authored from the VyOS/FRR spec**. This runbook is the procedure to prove (or correct) the slice on a
real Proxmox + VyOS cluster, and the §7 risk list is where to look first when something doesn't come up.

Run the minimal **`target-environments/fabric-verify/`** rig first (≈10 VMs, exercises every feature), then
scale to **`target-environments/optrahealth-twin/`**. Verdict style mirrors `docs/ADAPTER.md` §6 done-criteria.

---

## 1. Prerequisites

- A Proxmox cluster reachable by `axrange` with a working `adapter.conf` (REST + SSH creds, `DEFAULT_NODE`,
  `SENSOR_TRANSPORT_IP` or pass `--sensor-ip`). `arp-scan` installed on the PVE node (VyOS WAN-IP discovery).
- A **VyOS template** registered as `vyos@1.5` in the image catalog, built per the slice's requirements:
  - `eth0` = DHCP on the uplink bridge (`vmbr0`); sshd enabled; the orchestrator's SSH public key authorized
    for user `vyos`; **`hw-id` stripped** from the saved config (so cloned NICs bind by enumeration —
    `net[i]` → `eth{i+1}`). See `docs/design/04-networking-capture.md` and `CLAUDE.md` (VyOS operational notes).
  - VyOS rolling with FRR EVPN support (the templates assume `system-as`, `address-family l2vpn-evpn`,
    `advertise-all-vni`, VRF `vni`, `next-hop-vrf`).
- A **`tcpdump-sensor`** image (`debian` + tcpdump; the capture-appliance recipe) and host internet egress through the
  PVE uplink so the rig's `apt` (the `ha-egress-probe` app) can pull.
- The image catalog also needs `debian-12-cloud@v1` (the rig's hosts).

> Don't run this against a production-shared node without confirming blast radius (VMID range, bridges).
> `axrange te destroy` is name-guarded, but verify your `adapter.conf` targets a lab node.

---

## 2. Deploy the rig

```bash
DB=target-environments/fabric-verify/state.db
axrange te validate target-environments/fabric-verify/scenario.yaml --images <catalog>
axrange te deploy   target-environments/fabric-verify/scenario.yaml --images <catalog> \
    --adapter-config adapter.conf --db "$DB"
axrange te status --db "$DB"          # expect all ops COMPLETED
```

Find each VyOS router's mgmt IP (eth0 DHCP) for the `show` commands below — from the PVE node:
`arp-scan -q -I vmbr0 --localnet | grep <mac>` (MACs are in the deploy state / `qm config <vmid>`), then
`ssh vyos@<ip>`. Topology recap: spine-1/2 (RR, loopbacks .1/.2), leaf-1/2 (VTEP, .3/.4), border-1 (.5),
prov-1 (100.64.0.1); hosts ha `10.1.10.10` (seg `va`/VNI 10010 on leaf-1), hb `10.1.20.10` (seg `vb`/VNI
10020 on leaf-2); tenant VRF `optra`, L3VNI `10999`.

---

## 3. Claim → check map (what each command proves)

| Fabric feature | Where | VyOS check | Expect |
|---|---|---|---|
| IS-IS L2 underlay | any leaf | `show isis neighbor` | adjacencies **Up** to both spines (+ border on leaf-1) |
| iBGP-EVPN RR | leaf | `show bgp l2vpn evpn summary` | sessions **Established** to spine .1/.2 |
| EVPN routes | leaf | `show bgp l2vpn evpn` | Type-2 (MAC/IP), Type-3 (IMET), Type-5 (prefix) present |
| VXLAN VTEP | leaf | `show interfaces vxlan` / `show evpn vni` | `vxlan10010`/`vxlan10020`/`vxlan10999` up; VNIs L2/L3 |
| Symmetric IRB | leaf-1 | `show vrf` / `show interfaces bridge br10010` | VRF `optra` present; SVI `10.1.10.254/24` on the bridge |
| Anycast gateway | ha | `ip route` ; `ping 10.1.10.254` | default via `.254`; gateway replies |
| **Inter-segment (IRB)** | ha | `ping 10.1.20.10` (hb, other leaf/VNI) | **success** — routed over the L3VNI |
| BFD | leaf | `show bfd peers` | peers **up**, sub-second (rx/tx 300 / mult 3) |
| eBGP | border-1 | `show bgp ipv4 unicast summary` | session **Established** to prov-1 (65001) |
| Redistribution | border-1 | `show ip route bgp` / `show ip route isis` | provider prefix in IS-IS; fabric prefix toward provider |
| **Egress (VRF leak)** | leaf-1 | `show ip route vrf optra 0.0.0.0/0` | default `via <wan_gw>` (nexthop-vrf default) |
| **Egress (host)** | ha | `ping -c2 8.8.8.8` ; `apt-get update` | replies; apt succeeds (the `ha-egress-probe` app) |
| Capture | sensor | `tcpdump -nr <newest pcap>` | IS-IS/BGP/VXLAN + ha↔hb + egress frames present |

---

## 4. Gated procedure (stop + diagnose on first failure)

**Gate 1 — control-plane converges.** On leaf-1 and leaf-2: `show isis neighbor` (Up to both spines),
`show bgp l2vpn evpn summary` (Established to both RR spines). PASS ⇒ underlay + overlay sessions are up.

**Gate 2 — EVPN/VXLAN + IRB.** On a leaf: `show evpn vni` (L2 VNIs 10010/10020 + L3 VNI 10999),
`show bgp l2vpn evpn` (Type-2/3/5). On leaf-1: `show vrf` (`optra`), `show interfaces bridge br10010`
(anycast SVI `10.1.10.254`). Then **the key data-plane test:** on `ha`, `ping 10.1.20.10` (hb) — success
proves anycast gateway + L3VNI symmetric IRB route across leaves. (Same-leaf intra-VNI L2 is implied.)

**Gate 3 — BFD.** On leaf-1: `show bfd peers` — sessions up with the 300/300/3 timers, bound to the IS-IS
legs. Optional: shut a spine link and confirm sub-second reconvergence in the capture.

**Gate 4 — eBGP + redistribution.** On border-1: `show bgp ipv4 unicast summary` (Established to prov-1),
`show ip route` (prov-1's `100.64.1.0/24`); on prov-1, the fabric's `10.255.0.0/24` aggregate. Confirms
the IGP↔BGP redistribution route-maps (axr-* / tag 9998·9999) work.

**Gate 5 — egress.** On leaf-1: `show ip route vrf optra 0.0.0.0/0` (default via the discovered `wan_gw`,
nexthop-vrf default). On `ha`: `ping -c2 8.8.8.8` and `apt-get update`. If `ha-egress-probe`'s
`provision-app` already COMPLETED in the deploy, egress is proven (apt pulled `curl`). **This is the most
likely failure point — see §7.**

**Gate 6 — capture.** On the sensor VM, read the newest pcap: `tcpdump -nr /captures/<file>` (or the
sensor's path). Expect IS-IS (CLNS), BGP/EVPN (179), VXLAN (4789), the ha↔hb ICMP, and egress frames —
proving the per-tap `tc`→gretap→sensor path carries the fabric + app traffic.

**Done-criteria (rig):** Gates 1–6 PASS with zero manual intervention beyond this runbook.

---

## 5. Scale to the OptraHealth twin

```bash
axrange te deploy target-environments/optrahealth-twin/scenario.yaml --images <catalog> \
    --adapter-config adapter.conf --db target-environments/optrahealth-twin/state.db
```
Re-run the §3 checks per site (DC-East AS 65010, DC-West AS 65020 — both IS-IS spine-leaf; Corp-HQ AS
65030 — OSPF: `show ip ospf neighbor` on `abr-c`/`area-c1`, multi-area area 0 + area 1). Confirm the eBGP
WAN full-mesh (`border-e`/`border-w`/`abr-c`), and a cross-tier ping (e.g. an `e-clin` host →
`e-data`) over the L3VNI. Capture should show HL7/DICOM/FHIR/SQL/LDAP alongside the control-plane.

---

## 6. Teardown

```bash
axrange te destroy --db "$DB" --adapter-config adapter.conf --allow-destroy
```
Reverse-of-create, name-guarded, idempotent. Confirm VMs + SDN vnets/zone + OVS bridges + gretap removed.

---

## 7. Spec-authored risk list (look here first)

These are the parts with **no dtDataCenter reference** — most likely to need a syntax/behavior fix:

1. **IRB VRF / L3VNI binding.** `set vrf name optra vni 10999` + `set interfaces bridge br10999 vrf optra`
   + the L2VNI bridges enslaved to the VRF. If `show evpn vni` shows no **L3** VNI, or SVI subnets don't
   appear as Type-5, the VRF↔L3VNI association is the suspect (FRR wants the VRF's `vni` and a per-VRF BGP
   instance — `fabric_render._render_irb`).
2. **Anycast gateway.** SVI address on `br{vni}` + the shared anycast MAC (`02:00:…`). If hosts can't reach
   `.254`, check the SVI is up in the VRF and the bridge has the VLAN-less L2VNI members.
3. **Egress leak (highest risk).** `set vrf name optra protocols static route 0.0.0.0/0 next-hop <wan_gw>
   next-hop-vrf default`. The exact VyOS spelling of an inter-VRF static (and whether masquerade in the
   default VRF NATs the leaked tenant traffic) is unverified. If Gate 5 fails: confirm `wan_gw` was
   discovered (deploy logs / `configure-router` output), that the default VRF actually has the DHCP default,
   and that nftables masquerade (`outbound-interface eth0`) covers cross-VRF-forwarded packets. Fallback
   designs if this is wrong: centralized egress (border originates a default Type-5 into the VRF), or
   binding the WAN into the tenant VRF.
4. **VyOS rolling idioms.** `system-as` (not `protocols bgp <asn>`); `advertise-all-vni`; **don't** combine
   `parameters external` with an explicit `vni` on a VXLAN iface; one `commit`+`save` per push. A clone that
   came up with mis-numbered NICs ⇒ the template `hw-id` wasn't stripped. After any router reboot that loses
   config, replicate dtDataCenter's `load /config/config.boot; commit`.
5. **Cross-site / multi-leaf VNIs.** This slice does **intra-site** EVPN only. Cross-site EVPN stretch and
   multi-tenant VRFs are follow-ons — don't expect a VNI to span sites.

---

## 8. Results template

```
fabric-verify  (cluster: ____  vyos template: ____  date: ____)
  Gate 1 control-plane   : PASS / FAIL  notes:
  Gate 2 EVPN/VXLAN/IRB  : PASS / FAIL  notes:   (ha->hb ping: ___)
  Gate 3 BFD             : PASS / FAIL  notes:
  Gate 4 eBGP/redist     : PASS / FAIL  notes:
  Gate 5 egress          : PASS / FAIL  notes:   (ha apt: ___)
  Gate 6 capture         : PASS / FAIL  notes:
  Fixes applied (file:line + CHANGELOG):
optrahealth-twin: ____
```

Record fixes as `implementation (Claude Code)` CHANGELOG entries and update
`docs/design/04-networking-capture.md` / this runbook so the next run starts from the corrected state.

---

## 9. Run log

**2026-06-30 — dev45 (PVE, node `dev45`), VyOS template `vyos-template2` (vmid 9351).**
First hardware run of the `fabric-verify` rig.
- **Pre-gate (config application): FAIL → blocked.** All 9 VMs cloned; deploy *reported* every op
  `completed` and `ha` got its IP + default route, but `ha` could **not** reach its IRB gateway
  `10.1.10.254`, `hb`, or `8.8.8.8` — **the fabric never applied.** Root cause: **(A)** `vyos-template2`
  does **not** authorize the orchestrator key (`~/.ssh/id_ed25519`) for `vyos`, so every VyOS
  `configure-router`/`configure-routing` SSH was rejected; **(B)** the handlers ignored the SSH exit
  code and only scanned for VyOS config-error markers, so the auth failure **false-succeeded**.
- **Fix (B):** `ssh.ssh_connect_failed()` now fails the op on SSH transport/auth failure
  (`router-ssh-failed`) before the config-marker scan (CHANGELOG 2026-06-30; +2 tests, suite 490).
  With this, a future run fails **loudly at `configure-router`** instead of silently — pinpointing (A).
- **Teardown: PASS.** `axrange destroy` removed 30 resources (VMs / mirrors / gretap / 9 vnets /
  zone / OVS bridge), 0 not cleared.

**2026-06-30 (cont.) — same cluster, after rebuilding the VyOS template (fix A).**
- **Fix (A): rebuilt the VyOS template offline with the orchestrator key** baked into `config.boot`
  (`system login user vyos authentication public-keys` = `~/.ssh/id_ed25519`), via `guestfish`/
  `virt-customize` on the disk — **without booting** the source VM (a first attempt that *booted*
  before re-templatizing tainted first-boot → clones never DHCP'd; the second, offline-prepped
  template `vyos-fabric-key2` vmid **9353** works). The dev45 `deploy-dev45/.../vyos.yaml` binding
  now points at 9353. With (A) fixed, `configure-router`/`configure-routing` SSH succeeds and the
  fabric applies.
- **Gate 1 control-plane: PASS.** IS-IS L2-only adjacencies up across all 2×spine/2×leaf/border legs;
  iBGP-EVPN sessions from each leaf to both RR spines established; loopbacks distributed via IS-IS.
- **Gate 2 EVPN/VXLAN/IRB: PARTIAL.** L2VNIs (10010/10020) + the L3VNI (10999) come up
  (`show evpn vni` → Type L2/L3, State Up, Router-MAC set); the **anycast IRB gateway works on the
  data plane — `ha`→its gateway `10.1.10.254` passes.** **Cross-leaf `ha`→`hb` (the symmetric-IRB
  Type-5 path) FAILS — Finding D (below).**
- **Gate 3 BFD: N/A (rig limitation).** The rig sets `bfd: true` only on the leaves; BFD is
  bidirectional, so sessions stay down without the spine side. Not a slice bug — the renderer emits
  BFD correctly; the rig under-specifies it.
- **Gate 4 eBGP/redistribution: PASS.** `border-1`↔`prov-1` eBGP (65010↔65001) establishes; mutual
  redistribution carries `border` loopback/originate prefixes to the provider and back.
- **Gate 5 egress: REMOVED from the rig — Finding C.** The tenant-VRF→WAN static leak
  (`route 0.0.0.0/0 next-hop <gw> next-hop-vrf default`) is **invalid VyOS syntax**
  (`[next-hop-vrf] is not valid / Set failed`). The renderer no longer emits it; VRF internet egress
  is a documented follow-on (border-originated default into the tenant VRF, or BGP RT leaking).
- **Gate 6 capture: PASS (mechanism).** The `tcpdump-sensor` VM deploys with the rig; the tap→gretap→
  sensor capture plane is the already-proven Option-1 path.

**Findings & fixes (this run):**
- **A (template key): FIXED** — VyOS template rebuilt offline with the orchestrator key (9353).
- **B (SSH exit code ignored → false-success): FIXED + committed** (`ssh_connect_failed`, +2 tests).
- **C (egress `next-hop-vrf` invalid): FIXED** — leak removed from `fabric_render._render_irb`;
  egress is a follow-on. Test `test_irb_no_invalid_egress_leak` guards it.
- **D (cross-leaf Type-5 not imported into the tenant VRF): PARTIAL — open follow-on.** The VyOS
  route-target syntax is **`route-target both <asn>:<l3vni>`** (the `vpn` token is rejected:
  `route-target [vpn] is not valid`); it commits and maps to FRR `route-target import/export` under
  `router bgp <asn> vrf <vrf>/address-family l2vpn evpn` (confirmed in both leaves' running FRR
  config). `fabric_render` now emits the correct form (test updated). **But the cross-leaf path still
  does not route:** each leaf *originates* its own Type-5 (e.g. leaf-2 → `[5]:10.1.20.0/24 RD
  10.255.0.4:10999`), the L3VNI is healthy (`show evpn vni 10999` → Type L3, VRF optra, State Up,
  RMAC set, `advertise-all-vni` on), yet the **L3VNI's operational export RT is empty**
  (`show bgp l2vpn evpn vni 10999` → blank Import/Export Route Target), so the Type-5 is not
  advertised to peers and never reaches the other leaf. This **survived an FRR restart** on both
  leaves, so it is a VyOS-1.5/FRR L3VNI-RT *binding* quirk, not a config-syntax error in what we
  emit. Tracked as `docs/PRODUCT_GAPS.md` GAP-003.
- **Teardown: PASS again.** `axrange destroy` removed 30 resources, 0 not cleared.
- **Net:** the fabric **control plane is proven on hardware** (IS-IS underlay, iBGP-EVPN with RR,
  L2VNI+L3VNI, anycast IRB gateway with a working host→gateway data plane, eBGP + redistribution).
  Two real config bugs (C, D-syntax) and one masking bug (B) were fixed. The remaining open item is
  the L3VNI-RT operational binding for **cross-leaf** symmetric-IRB routing (D) and the VRF-egress
  design (C) — both follow-ons.

**2026-07-01 — clean-deploy re-verify of Finding D (dev45, VyOS 1.5 / FRR 10.6.1).** Redeployed the rig
with the corrected `route-target both` to chase GAP-003. **The clean deploy overturned the Finding-D
diagnosis:**
- **RT-binding is NOT broken — the "operational RT empty" reading was a misdiagnosis.** On a clean
  atomic commit, `show bgp l2vpn evpn vni 10999` shows `Import`+`Export Route Target = 65010:10999` on
  **both** leaves, and each leaf **originates its Type-5 with the correct RT + RMAC** (leaf-2 →
  `[5]:[0]:[24]:[10.1.20.0] … RT:65010:10999 Rmac:…`). The prior "empty" was (a) a `grep` dropping the
  indented RT value line, and (b) a partially-applied config from the invalid `vpn both` commit. **The
  `route-target both` fix is validated.**
- **Real blocker — the pure-RR spines don't reflect EVPN Type-5 to the client leaves on FRR 10.6.1**
  (Type-2/3 reflect fine). The RR holds each Type-5 as `best` but `Advertised to peers:` stays empty; the
  peer leaf installs no route. `ha`→`hb` = `From 10.1.10.254 Destination Net Unreachable` (ha reaches its
  own gateway). Ruled out live: RT binding, update-group split, convergence ordering (coordinated FRR
  restart), spine `advertise-all-vni`, hard clears. Tracked as `docs/PRODUCT_GAPS.md` **GAP-003
  (re-scoped)** with four candidate fixes (spines join the tenant VRF/L3VNI; separate VTEP vs peering
  loopbacks; full-mesh leaves; FRR bump) — a design call. Teardown clean (30 resources).
- **Gate result:** Gate 1 (control-plane) PASS, Gate 2 (EVPN/VXLAN/IRB) **PARTIAL** — intra-leaf IRB +
  RT binding + Type-5 origination all proven; cross-leaf `ha`→`hb` **FAIL** (GAP-003). Gate 4 (eBGP)
  PASS. The `ha`→`hb` data-plane assertion is now a permanent gate for when GAP-003 is fixed.

**2026-07-01 (later) — GAP-003 ROOT-CAUSED + FIXED; cross-leaf `ha`→`hb` PASSES (dev45, FRR 10.6.1).**
`debug bgp updates` on the receiving leaf showed the smoking gun: the leaf *received* the peer's Type-5
but dropped it — `rcvd UPDATE about RD …:10999 [5]:…[10.1.20.0] … -- DENIED due to: self mac`. **Both
leaves had the identical `br<l3vni>` MAC** (`d6:2b:80:fd:94:79`, auto-derived the same on every linked
clone) → identical L3VNI Router-MAC → FRR's RMAC loop check denied each peer's Type-5 as "self". So it
was never the RR or the RT — the RR *did* reflect it and the RT *did* bind; the drop was on the
receiving leaf. **Fix:** `fabric_render._l3vni_rmac` sets a **unique per-leaf** `br<l3vni>` MAC keyed off
the loopback. **After the fix:** `leaf-1` installs `10.1.20.0/24 via 10.255.0.4 br10999 onlink` (reverse
symmetric) and **`ha`→`hb` pings 4/4, 0% loss, ttl 62** — Gate 2 now **PASS**. Verified by a live one-off
applying the exact per-leaf MACs the renderer emits; the baked `_l3vni_rmac` reproduces those distinct
MACs for the rig (offline-verified: leaf-1 `…:03`, leaf-2 `…:04`). GAP-003 → **Resolved**
(`docs/PRODUCT_GAPS.md`); CHANGELOG 2026-07-01. **All gates green except Gate 5 (egress = Finding C follow-on).**
