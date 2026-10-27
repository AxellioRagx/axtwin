# Work Order — Dual-stack segments: an IPv6 prefix beside each segment's IPv4 CIDR, advertised and contained

> **STATUS (2026-10-06): OPEN — nothing built; S0a done.** S1 changes version-locked contracts
> (`IR_SCHEMA`, `VALIDATOR`, `OP_CATALOG`, `ADAPTER`): confirm §8 D1–D3 with the user first.
>
> **Authority:** **user** (2026-10-06) for §2: record the gap, open this work order, dual-stack
> segments only. **implementation (Claude Code)** for everything else here: the source finding (§4),
> the design (§5), the slicing (§6), the runbook (§7), and the recommendations in §8.
>
> **Gap:** `docs/PRODUCT_GAPS.md` GAP-124. **Extends:** `WORK_ORDER_corp_twin.md` (the first
> adopter; §6 addressing, §11 runbook shape) · `WORK_ORDER_twin_incremental_update.md` (what
> `te deploy --update` can carry) · `adapter_proxmox/egress_render.py` (the containment engine).

## 1. Goal

Each emitted segment can carry an **IPv6 prefix next to its IPv4 CIDR**. The router advertises it,
hosts take addresses, and the twin's wire shows realistic dual-stack *host* behaviour: RA/RS,
NS/NA and DAD, MLD reports, SLAAC, and AAAA lookups from dual-stack resolvers. **No IPv6 persona
traffic yet** (§9). Containment must hold for v6 exactly as it does for v4, and the proof is a gate.

## 2. Ruling (Authority: **user**, 2026-10-06)

The user asked why corp-twin carries no IPv6 when the source captures carry a lot of it, and ruled:
*"record the gap and open a work order for dual-stack segments."* This order covers the segments
piece only. **IPv6 persona emits** are explicitly a **separate future work order** (§9).

## 3. Sources

| Source | Used for |
|---|---|
| Live, `corp-twin-3dae3188` on dev45, 2026-10-06 | **Symptom:** 20 s on three gretaps (vlan20, sfp25 trunk, wan1; ~248,000 frames) held **one** IPv6 frame, an RS (`fe80::… > ff02::2`) on the trunk |
| `twins/pit-395a61f04e-1780405200/generated/…topology.json` | **The source:** the cast bundle for `corpapcn-disco`'s as-of (1780405200), which `twins/corp-twin/te.yaml` came from |
| `docs/JOURNAL_axquery_discovery_loop.md` l.83 · `docs/reference/GAP_ANALYSIS_aximprint_corp_discovery.md` G-03 | Corroboration: "external IPv6 /64s" treated as noise; RA prefixes listed as unused segment evidence |

## 4. Finding — where IPv6 is lost, and what the source supports

**Lost at the cast, reproduced faithfully downstream.** None of the bundle's 39 `segments` has an
IPv6 cidr; every IPv6 node has `segment: null` (the segmenter infers IPv4 broadcast domains only;
the cast builds from placed nodes). Downstream is IPv4-shaped throughout: `te.yaml` CIDRs;
`configure-router` sets `net.ipv4.ip_forward` only (`adapter_proxmox/rest.py` ~l.1168); one
`cidr` per segment in `twinconfig/expand.py` (`_IpAlloc` keyed by CIDR ~l.744, lone router leg =
`network+1` ~l.785); `_build_ipconfig_body` (`rest.py` ~l.1523) never emits `ip6=`/`gw6=`; the
tfc driver's `_host_ip` (`o2control/traffic.py` ~l.730) returns the first leg's v4 `ip`; the sink
answers `local 0.0.0.0/0` only (`twins/_shared/scripts/serve-sink.sh` ~l.535). The IR and validator
already **accept** an IPv6 `cidr` (`IR_SCHEMA` §3.1, NET-020) but no network can hold both families.
`serve-l2switch.sh` disabling IPv6 on bridge *ports* (l.392) is correct and not the cause.

**The source, measured (2026-10-06, this order).** 218 of 2,641 nodes have an IPv6 address, and 291
of 4,157 edges touch one: 1.89 % of flows and 0.12 % of bytes. By protocol: other 187, icmp 56,
quic 31, dns 12, smb 2, and rdp/rpc/krb 1 each. **Correction to the brief:** the 218 are *not* all
corp addresses under `2001:489a::/32`. By class:

| Class | Nodes | What it is |
|---|---|---|
| link-local `fe80::/10`, `locality: local` | 55 | Dual-stack *inside* hosts on every kind of segment. 44 have EUI-64 IIDs (23 VMware `00:50:56`). 14 listen on DHCPv6-client 546/udp, 7 send mDNS and 4 send LLMNR. 32 edges are RS to `ff02::2` |
| **`2603:300b:64a:ba00::/64`** (tagged `external`) | **13** | **The corp's own global prefix**: the hosts behind the MX85. 11 use random (privacy) IIDs, 2 use low static IIDs (`::abbe`, `::cbe8`), 1 is EUI-64. Roles: generic-endpoint, a corp-workstation, and two "file-servers". It fits the Comcast uplink: these hosts query `2001:558:feed::1/::2`, Comcast's resolvers |
| `2001:489a::/32`, `external` | 27 | **A remote destination**, not the corp's prefix. DNS names `*.sharepoint.us`, `*.privatelink.msidentity.us`. Corp hosts reach it over v6 (41 edges) |
| other global, `external` | 117 | Internet peers (Apple `2620:149::/32`, Microsoft `2603:10xx`, Akamai `2600:1404`, …) |
| multicast `ff02::…` | 6 | `ff02::1`, `::2`, `::1:2`, `::1:3`, `::fb`, one solicited-node |

**Prefix-to-segment correlation (the D1 question).** **MAC: 0 hits** — nodes carry no MAC field
(one `mac:` node in the bundle); the 45 EUI-64 IIDs match none of the 13 DHCP identity principals'
MACs. **DNS names: 2 of 13** in-house globals tie to IPv4 nodes — `axdc01`/`AXDC04.Axellio.dom`
resolve to `2603:300b:64a:ba00:dfd3:…`/`…:a99e:…` and to `10.80.20.4`/`.5`, the domain controllers
on `10.80.20.0/24` (the cast mislabels their v6 nodes `file-server`) = corp-twin's `mx-vlan20`
(relocated `10.180.16.0/20`) and its `isolated:` extension `vlan20`. The other 11 are privacy IIDs
and unplaceable, but all 13 share one /64, i.e. one broadcast domain.

**Reading:** the real network advertises **one** global /64, on VLAN20; every other VLAN shows
link-local IPv6 only (stacks up, no global address). Two upstream defects also show: aximprint
tags the corp's own v6 hosts `external` (IPv4-private-shaped locality), and the segmenter drops v6
(G-03). Both upstream (§9).

**Containment is v4-only by construction — no hole today (S0a, verified 2026-10-06).**
`egress_render`'s rules are `table ip axregress` (IPv4 only). On hq-mx (vmid 305605,
`corp-twin-3dae3188`) over QGA: `ip -6 addr show scope global` empty, `ip -6 route show default`
empty, `net.ipv6.conf.all.forwarding=0` — dev45's vmbr0 advertises no prefix, so nothing v6 can
leave. It holds only while that is true: the build must add the `ip6` table (§5.4) before any
segment gets a prefix, and S0a is re-checked whenever the uplink changes.

**Not established:** whether the Windows templates (`win11-finance@v4`, `windows-server-2022@v1`)
have IPv6 disabled — Windows is normally chatty (LLMNR, DHCPv6) and was silent. An S0 question.

## 5. Design

### 5.1 IR and twin-config (S1–S2)

- **`networks[].cidr6`** (optional IPv6 CIDR) and **`networks[].gateway6`** (optional, inside
  `cidr6`) beside `cidr`/`gateway`; `cidr` stays required and is the IPv4 CIDR of a dual-stack
  network (a v6-only network stays `cidr: <v6>`). Not `cidrs: [v4, v6]`: `cidr` is read by the
  validator, planner, adapter, Console and tests — a sibling field keeps every consumer and plan
  byte-identical. **`nodes[].nets[].ip6`** (optional pin) mirrors `ip`.
- `TwinConfigSegment` gains `cidr6`/`gateway6`, legs gain `ip6`; an `isolated:` extension carries
  its anchor's `cidr6` or none (as for `cidr`).
- **Schema version:** widen `STR-020` to `{1,2,3,4}`; the fields are additive and parsed whatever
  the declared version (the 1→2 `role:` precedent) — `4` only marks "authored dual-stack" (D5).
- **New validator rules** (next free ids after NET-110):

  | Rule | Severity | Checks |
  |---|---|---|
  | NET-120 | error | `cidr6` is a valid IPv6 CIDR, and `cidr` is IPv4 when `cidr6` is set |
  | NET-130 | error | `gateway6` lies within `cidr6` |
  | NET-140 | error | `cidr6` is exactly `/64` on any network with a router leg (SLAAC needs /64) |
  | NET-150 | error | No two `cidr6` overlap, except `isolated:` extensions (mirrors NET-010) |
  | NET-160 | error | `nets[].ip6` needs the network's `cidr6` and lies within it (mirrors NET-040) |

### 5.2 Addressing (S2)

- **Every leg's static v6 address embeds its v4 host offset** in the low 32 bits:
  `10.180.16.37` in `10.180.16.0/20` (offset 37) → `<prefix>::25`. Deterministic, collision-free
  by construction (the v4 allocator already made the offsets unique, one pool per CIDR across an
  isolated extension), readable beside the v4 address. A lone router leg at `network+1` therefore
  lands on `<prefix>::1` with no special case; `gateway6`/`ip6` pins override, and a pin that
  collides with a derived address is refused loudly (as `_IpAlloc.reserve` does for v4). Hosts take
  their v6 default route from RA (the router's link-local, as real hosts do) — no `gw6` on hosts.
- **Static + SLAAC (the tradeoff).** A static address is what inventory, `tfc` and taps can know
  at plan time; a SLAAC-only address is unknown until read back and differs every redeploy (EUI-64
  follows PVE's random clone MAC, stable-privacy a per-VM secret). **Recommend both:** the static
  address *and* RA `A=1`, so hosts also form their OS-default SLAAC/temporary addresses — the real
  network's privacy-IID picture (11 of 13). Inventory records the static `ip6`; the new op reads
  back every global v6 address over QGA (`guest-network-get-interfaces`) into its outputs.

### 5.3 Delivery: an added op, not a mutated one (S1/S3; D3)

`te deploy --update` refuses any **Mutated** op (`planner/plandiff.py`), and putting `ip6=` into
`configure-cloud-init`'s `ipconfigN` would mutate every host on a dual-stack segment (cloud-init
also applies network config at first boot only). **Recommended: a new op, OP-HOST-110
`configure-ipv6`** — config class, over QGA, idempotent, `redo`; one per dual-stack host plus a
router branch; inputs per leg `{nic_index, cidr6, ip6}` (+ RA parameters on the router); outputs
the read-back address list. Existing ops' inputs never see `cidr6`. **Done-criterion:** adding
`cidr6` to one segment of a standing deploy classifies as **Added-only** in `plandiff` (a test),
and every plan without `cidr6` is byte-identical to HEAD — ops, inputs, hints, edges, `plan_id`
(the corp-twin P0 method).

- **Linux host:** `ip -6 addr replace`; `accept_ra=1`, `autoconf=1`, `disable_ipv6=0` per leg;
  persisted by a oneshot unit (LES-013 pattern, as `serve-hq-mx-routes.sh`).
- **Windows host:** `New-NetIPAddress` over QGA PowerShell (persistent by default); fails loud if
  the `ms_tcpip6` binding is disabled or `DisabledComponents` is set (§4).
- **Debian router** (`hq-mx`): `net.ipv6.conf.all.forwarding=1`, `accept_ra=0` on LAN legs,
  `<prefix>::1` per dual-stack leg; **radvd**, one stanza per dual-stack leg (`AdvAutonomous on`,
  `AdvManagedFlag off`, `AdvOtherConfigFlag off`, `RDNSS` only when the segment's resolver has an
  `ip6`). radvd is an apt install, so the op runs in `provision` like `serve-vrrp-l3.sh`'s
  keepalived, and fails loud if absent; baking it into the image is the refinement. Rejected:
  systemd-networkd `IPv6SendRA` (debian-12-cloud does not run networkd).
- **VyOS router:** `set interfaces ethernet ethN address <prefix>::1/64` + `set service
  router-advert interface ethN prefix <prefix>/64` — only for twins *without* `egress:` (a VyOS
  router with `egress:` stays refused at plan time).

### 5.4 Containment (S4) — the v6 posture, and the choice for v6 egress

Applied on **every** router with an egress policy, dual-stack or not, and in place before any
segment gets a prefix (§4: today's absence of v6 is the uplink's, not the engine's):

- **WAN leg:** `disable_ipv6=1` on the router's vmbr0 NIC in both phases — no v6 surface toward
  the real network, no v6 default route.
- **`table ip6 axregress`** beside the `ip` table (or one `inet` table; build's choice): drop and
  count forwarded v6 in/out of the WAN and the router's own v6 output on it — in **both** phases
  (v6 has no upstream in either).
- **v6 to an off-twin destination: REJECT, not sink (D4).** LAN-ingress v6 to anything outside
  every twin `cidr6` gets ICMPv6 administratively-prohibited, counted. *Why:* the sink is IPv4-only
  and making v6 *succeed* end to end is the emits order's job; a reject makes dual-stack clients
  fall back to v4 at once (RFC 8305), where a silent drop would add connect delays and shift the v4
  mix's timing. It answers and counts — not a black hole.
- **Healthcheck / `te egress --show`** report the v6 posture (WAN `disable_ipv6`, ip6 table, no v6
  default); a missing piece fails `--healthcheck`, so `measure-unverified` fails closed and the
  tap/tfc `_containment_gate` refuses, as for v4.
- **Standing routers:** the engine is installed at `configure-router` time; S4 adds a re-install
  path so `te egress --phase measure` can upgrade a standing router (D7).

### 5.5 Mirror and sensor path — no change, one tooling note

- Taps are per-tap `tc` mirrors at L2 into keyed gretap over the IPv4 transport vnet — v6 frames
  mirror like any other.
- **Tooling:** the capture-rate analysis (`pcapsample.sh` + `correctness.py`, `docs/reference/
  capture-rate/2026-10-01-corp-twin-ladder.md`; not in this repo) keys on `ip.src`/`ip.dst`;
  dual-stack measurement needs `ipv6.src`/`ipv6.dst` columns or a per-family count.
- **Bridge MLD snooping:** PVE vnet bridges and `serve-l2switch.sh` bridges may run
  `multicast_snooping=1` with no MLD querier, which can break NDP once MDB entries age out. S0
  reads it; S5 turns snooping off or adds a querier in the twin's L2 recipes.

## 6. Slices

| Slice | What | Done when |
|---|---|---|
| **S0** — read-only checks, no code | (a) **DONE 2026-10-06** — hq-mx has no global v6, no v6 default, forwarding 0 (§4); re-check whenever the uplink changes. (b) Windows templates: `Get-NetAdapterBinding -ComponentID ms_tcpip6`, `DisabledComponents`; (c) debian-12-cloud `disable_ipv6`/`accept_ra` defaults; (d) `multicast_snooping` on a corp-twin vnet bridge and an l2switch bridge; (e) RAs on dev45 vmbr0 (`tcpdump -i vmbr0 'icmp6 and ip6[40]==134'`, 5 min) | Answers (b)–(e) recorded here |
| **S1** — contracts (confirm-first) | `IR_SCHEMA` §3.1 (`cidr6`, `gateway6`, `nets[].ip6`; STR-020), `VALIDATOR` NET-120..160, `OP_CATALOG` OP-HOST-110, `ADAPTER` handler, `docs/design/04-networking-capture.md`; CHANGELOG per bump | User confirms D1–D3, D5; specs edited, versions bumped |
| **S2** — schema, expander, planner, inventory | `ir/` + `twinconfig/schema.py` fields; v4-offset v6 derivation + pin/collision checks in `expand.py`; `pass1_emit` emits `configure-ipv6` after `wait-os-ready` (router: after `configure-router`); `HostNet.ip6` / `HostNetDTO.ip6`; validator checks | Tests per NET rule, isolated-extension sharing, pins, collisions; **byte-identity of every shipped plan without `cidr6`**; `plandiff` Added-only for an added `cidr6` |
| **S3** — adapter handlers | `configure-ipv6` Linux / Windows / Debian-router (radvd) / VyOS branches; QGA read-back | Golden render tests per branch; missing radvd or a disabled Windows v6 binding fails loud (LES-001) |
| **S4** — containment | `egress_render` `ip6` table, WAN `disable_ipv6`, off-twin REJECT, healthcheck, `--show`, engine re-install; `containment.py` reads the v6 posture | Render tests both phases; healthcheck fails per missing piece; gate refuses a dual-stack router without the v6 posture |
| **S5** — twin content | `corp-twin-devlab` + `corp-twin` `te.yaml`: `cidr6` on `mx-vlan20`/`vlan20` (per D2); resolver recipes bind `[::]:53` where they gain `ip6`; l2switch snooping/querier per S0(d) | `te validate`/`plan` clean; the only plan delta is the Added `configure-ipv6` set |
| **S6** — gated live-verify | §7 | Every check passes; results here + CHANGELOG |

## 7. Live-verify runbook (S6 — gated; the orchestrator runs it, not the code-writer)

Confirm the target cluster (dev45) and get the user's go before any deploy or `--update`.
**Never destroy the standing `corp-twin-3dae3188`** (or its tap/tfc) without the user's explicit
order.

1. **Devlab first** — `corp-twin-devlab` fresh `te deploy` (19 VMs); `te egress --phase measure`;
   `--show` healthy and reporting the v6 posture.
2. **Router** — radvd active; `net.ipv6.conf.all.forwarding=1`; `<prefix>::1/64` on each
   dual-stack leg; WAN leg `disable_ipv6=1`, no global v6; no v6 default route.
3. **Hosts** (one Debian, one Windows on vlan20) — static `ip6` + a SLAAC address; default route
   via the router's `fe80::`; `configure-ipv6` outputs list every address; `te inventory` shows `ip6`.
4. **Wire** (vlan20 tap, 60 s) — periodic RAs, NS/NA, MLDv2 reports, AAAA beside A queries; record
   counts per type (`tcpdump ip6` filters). `ping -6` host↔host; NDP still resolves after 5 min.
5. **v6 containment — REQUIRED GATE; stop on any failure, leave `measure`.** From a dual-stack
   host `ping -6 2001:4860:4860::8888` and a TCP connect to `[…]:443` → ICMPv6 admin-prohibited
   from the router, reject counter up; ip6 WAN drop counters unmoved by host traffic;
   `tcpdump -i <hq-mx WAN tap> ip6` on the PVE host for 60 s sees **zero** frames. Repeat in
   `provision` (same result), then back to `measure`, healthy.
6. **v4 regression** — `te egress` CONTAINED on every LAN leg; apply the devlab tfc; v4 mix and
   persona heartbeat unchanged.
7. **Full twin** (after devlab, with the user's go) — under D3 (a): `te deploy --update --config
   corp-twin` against `corp-twin-3dae3188`; the diff must be **Added `configure-ipv6` only** (no
   Mutated, no Removed) — flip `te egress --phase provision` first if radvd is still an apt
   install, and back to `measure` before taps/traffic. Under D3 (b) it needs a fresh deploy, i.e.
   a destroy — the user's call.
   Repeat 2–6 on `mx-vlan20`/`vlan20` and re-take the 2026-10-06 20 s three-gretap sample (expect
   RA/NS/NA/MLD on vlan20, tagged on the trunk).

Devlab destroy order: tfc → tap → te, naming each id.

## 8. Open decisions (user)

- **D1 — prefix source.** (a) the real `2603:300b:64a:ba00::/64` — the only prefix the source
  ties to a segment, and only to VLAN20 (§4); (b) deterministic ULA, `fd` + 40 bits of
  `sha256(twin id)` as a /48, one /64 per dual-stack segment. **Recommend (b):** the twin already
  relocates its IPv4 (`10.80` → `10.180`, corp-twin §6) and ULA is the v6 analogue; (a) puts a real
  ISP allocation inside the lab. Behaviour-keyed defenses (RA/NDP/SLAAC/dual-stack DNS) lose
  nothing — fidelity comes from *where* v6 is enabled (D2), not the prefix bytes.
- **D2 — placement.** (a) evidence only: `mx-vlan20` + `vlan20`, matching the source's one /64;
  (b) every HQ VLAN; (c) every segment. **Recommend (a)** for corp-twin — every other segment keeps
  link-local-only v6, which is also what the source shows. The engine supports any of them.
- **D3 — delivery.** (a) the added `configure-ipv6` op, `--update`-able (**recommended**, §5.3);
  (b) `ipconfigN ip6=` — fresh deploys only, fewer ops.
- **D4 — v6 egress in `measure`: REJECT** (this order's call, §5.4); override if v6 should succeed
  now (that needs the v6 sink from the emits order).
- **D5 — schema version.** Widen STR-020 to `4` as an optional marker (recommended; 1→2
  precedent), or no widening (`default_agents` precedent).
- **D6 — RA flags.** `A=1,M=0,O=0`, no DHCPv6 server (recommended), or `O=1` so Windows sends
  (unanswered) DHCPv6 Information-Requests — the source has 14 hosts listening on 546/udp.
- **D7 — standing-router upgrade.** `te egress` re-installs the engine when its rendered hash
  differs (recommended), or the v6 posture reaches new deploys only.

## 9. Out of scope

- **IPv6 persona emits** — v6 peers in persona emits, the driver resolving a v6 peer (`_host_ip`
  is v4), a v6 sink (`local ::/0`), AAAA sink-corpus content. A **separate future work order**
  (working title `WORK_ORDER_ipv6_emits.md`); it also owns D4's sink path.
- **Upstream aximprint** — the segmenter emitting v6 segments from RA prefixes (G-03); the locality
  rule tagging the corp's own `2603:300b:…` hosts `external`; the cast emitting `cidr6`.
- v6-only twin segments, a DHCPv6 server, NAT66/NPTv6, VRRPv3/v6 on the 9K pair, OSPFv3 in the
  fabric slice (the last two only if D2 (b)/(c) puts v6 behind the 9K pair); the mirror transport
  (GRE over IPv4) unchanged; Console rendering of `cidr6`/`ip6` beyond what the DTOs carry.
