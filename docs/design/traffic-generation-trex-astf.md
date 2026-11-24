# Traffic generation with TRex ASTF: emulated instances and traffic lines

**Status:** proposed 2026-11-24. The decisions in §9 need the user's sign-off
before anything past the spikes (§10) is built. Phases are in §11.
**Supersedes** `docs/design/crowd-node.md` (D1–D7, never signed off): the
user's call, 2026-11-24. Its `kind: trafficgen` te node is not built; the
Phase A evidence it cites (`catalog/twins/trex-test/README.md`) still counts.

**Loop steps:** **customize** (step 3, canvas) and **discover** (persona and
traffic tiers). The deploy gate is unchanged: tapped traffic reaches a sensor,
confirmed by climbing capture metrics.

**Repo path:** `docs/design/traffic-generation-trex-astf.md`.

**Verified 2026-11-24:** the TRex constraints in §8.4 come from the spikes
(§10.1). Statements elsewhere that §8.4 does not cover are still unverified.

## 1. Problem

Today every traffic source in a twin is a VM running a persona over the
guest agent. That is right for servers of interest and wrong for volume:

- a discovered user LAN of 400 workstations costs 400 VMs, or is cast as a
  handful and loses its shape;
- the internet side is one sink VM with one address;
- `loadgen-*` and `xseg-*` personas exist but are hidden under Advanced and
  are still one process on one VM with one address.

We want load generation that a designer picks on the canvas and that
discovery can assign, with many addresses, real TCP, and payloads a sensor
parses as the real protocol.

## 2. The idea in three sentences

1. Every node group has a split count: **Real N · Emulated M**. Emulated
   instances have a name, a role and an address like any other instance, but
   no VM; a TRex ASTF generator answers for them.
2. Traffic is described as **traffic lines** on a node group: *talks to*
   (peer), *app mix*, *rate*. Client and server are derived from the lines.
3. Generator VMs are planned by the engine, like tap tunnels. The designer
   never places one.

This covers both uses with one setting:

| Use | Setting |
|---|---|
| Replace VMs with emulation | Real 0 · Emulated N |
| Add load on top of real nodes | Real 2 · Emulated 50 |
| Collapse many nodes into one generator | automatic: all emulated instances share planned generators |

## 3. Vocabulary

- **Traffic app:** one application conversation (HTTP browse, REST call, DNS
  lookup, SMB read, SQL query). A catalog entry with a source (a pcap or a
  programmatic ASTF program), a default port, tunables (request and response
  size, think time, keep-alive), and a flag `real_server_ok` (§6).
- **App mix:** a weighted set of traffic apps. This is the thing a user
  picks. It is a persona with `drive: trex-astf`, so `emits`, `requires`,
  `category`, `label` and `blurb` (Add-panel D1) apply unchanged. It replaces
  the hidden `loadgen-*` / `xseg-*` list in the UI.
- **Emulated instance:** an instance of a node group with no VM. It has a
  name (`rack1-web-3`), the group's role, and an address in the group's
  segment.
- **Traffic line:** `{ from: <group>, to: <group | external group>, mix,
  rate_cps, max_concurrent, schedule }`.
- **External group:** an emulated group on the internet segment (§5).
- **Generator:** a VM running TRex in ASTF mode with legs on the segments
  it serves. Planned, not designed.

## 4. Canvas

### 4.1 Selection panel, Traffic section (tfc, live)

For a selected node group:

- **Instances:** `Real [2] · Emulated [0]`. Emulated instances take the next
  free addresses in the segment (existing allocator).
- **Traffic lines** (a small table, add / remove rows):

  | Field | Notes |
  |---|---|
  | Talks to | Peer picker: any node group (real or emulated) or an external group. Search by name or role. |
  | App mix | Filtered to mixes whose `emits` fits the peer's role. |
  | Rate | Connections per second, plus a concurrency cap. Editable while live. |
  | Schedule | Flat, business-hours, or a learned curve (discovery). |

- **Badge** (derived, read-only): client, server, or both; and per line a
  tag **E-W** or **N-S** (§5).
- **Driver per line** (derived, shown as a hint):
  - emulated → emulated: TRex both ends;
  - emulated → real: TRex client-only, only mixes whose apps are all
    `real_server_ok`; others are greyed with "emulate the server end";
  - real → anything: today's guest-agent persona, unchanged.

A line whose *from* group is all real and whose mix is `trex-astf` is
refused with the reason ("TRex cannot send from a real VM's address; add
emulated instances").

### 4.2 On the canvas

- Emulated instances render as part of their group, hatched, with a count
  chip (`2 + 50`). A group with Real 0 renders fully hatched.
- Traffic lines render as thin directed arcs between groups, off by default
  behind a "Traffic" view toggle, reusing the glow overlay. Live, the arc
  width follows the generator's counters.
- **Generators are hidden.** A "Show generators" toggle reveals the planned
  VMs and their legs, read-only.
- **Design health line** gains: total planned rate against the twin's
  traffic budget, and unmet lines ("web → db: no server end").

### 4.3 Add panel

- Tools → **Internet** gains two options: *Internet users* and *Internet
  services* (§5).
- App mixes appear under each card's role list as "traffic only" roles, and
  in search. The Advanced-only "Traffic generators" category goes away once
  the old `loadgen-*` personas are migrated or retired.

## 5. East-west and north-south

Direction is derived from where the two ends sit; nothing extra is
configured.

- **East-west:** both ends internal. Same segment, or across routers.
- **North-south:** one end is an external group on the `internet` segment:
  - **Internet users:** emulated clients with a public-looking address pool;
    lines go inward (to web, VPN, mail).
  - **Internet services:** emulated servers with a public-looking pool;
    internal groups have lines outward (browsing, SaaS, updates, DNS).

  Pools come from a reserved documentation or operator-chosen range and are
  routed through the generator's leg on the internet segment. The existing
  internet sink stays for real-VM traffic.

Example on `three-tier-dc-manual` (9 real VMs untouched):

| Line | From | To | Type |
|---|---|---|---|
| 1 | Internet users ×2000 | `rack{1,2,3}-web` (real nginx) | N-S, client-only HTTP |
| 2 | `rack1-web` +20 emulated | `rack1-db` +1 emulated | E-W in rack, TRex both ends |
| 3 | `rack1-web` emulated | `rack3-db` emulated | E-W across cores |
| 4 | `rack2-web` emulated | Internet services | N-S outbound |

## 6. Limits to state in the UI and docs

- **No traffic from a real VM's address.** "Add on top" of a real client
  means emulated siblings; of a real server means emulated clients aimed at
  it.
- **Client-only against a real service** works only for conversations the
  real service answers the same way every time (plain HTTP GET to nginx).
  Anything with a server nonce or negotiated crypto (SQL logins, TLS, SMB
  auth) needs TRex at both ends. `real_server_ok` on the traffic app records
  this; default false.
- **TLS between two emulated ends** is a replayed capture: a sensor sees a
  well-formed handshake, SNI and certificate, but nothing is decrypted or
  validated. Good for sensor metadata, not for testing a TLS-inspecting
  device.
- **Lines are independent.** An emulated request to a real web server does
  not cause a database call. Chains are not correlated.
- **Scale is virtual-NIC scale.** The budget is connections per second and
  Mbps per twin, bounded by the vnets, the routers and the mirrored tap
  path. Not millions of flows.

## 7. Discovery

Persona synthesis gains one decision per discovered group: **real or
emulated**.

- Default **real:** infrastructure and servers of interest (DC, DNS, file,
  database, anything with Windows-role evidence), and anything an attack
  step may land on.
- Default **emulated:** high-cardinality endpoint groups (workstations,
  IoT, phones) above a threshold, and all external peers, which become
  Internet users or Internet services.
- The operator can flip any group on the canvas (§4.1).

Traffic lines are synthesized from observed edges
(`src group, dst group, service`) using features the learners already
produce:

| Line field | From |
|---|---|
| mix (apps and weights) | `service` / `dst_port` shares per edge |
| rate | flows per window |
| app tunables | `flow_size_q`, `duration_q`, `interarrival_q` |
| schedule | `subbucket_flows` |

Sources with payloads (pcap) may later yield site-specific traffic apps
(§11 phase 5). Flow-only sources (SIEM) map service and port to the
built-in app library.

**Fidelity check:** tap the running twin, run discovery on that capture,
and diff the imprint against the original. This is the acceptance for
discovered traffic.

## 8. Storage and engine

### 8.1 Where it is written

The te schema does not change. Emulated instances and traffic lines live in
the tfc profile. Sketch only; fit it to the real tfc schema:

```yaml
tfc:
  target: three-tier-dc-manual
  budget: { cps: 500, mbps: 200 }
  emulated:
    - { group: rack1-web, count: 20 }                 # addresses allocated at plan
    - { group: internet-users, external: clients, count: 2000, pool: 198.18.0.0/20 }
  lines:
    - { from: internet-users, to: role:web-tier, mix: web-browse, rate_cps: 50 }
    - { from: rack1-web, to: rack1-db, mix: sql-oltp, rate_cps: 20 }
```

Open point for §9: a group with Real 0 still needs to exist in te for its
name, role and segment. Either te allows `count: 0`, or the group is
declared only in tfc. Prefer the first if the validator allows it.

### 8.2 Planning

At tfc plan time the engine:

1. resolves each line to client addresses, server addresses and a driver
   (§4.1);
2. groups TRex lines into generators, respecting the ASTF port pairing
   (§8.4), and emits the generator VMs and their legs;
3. compiles one JSON per generator for a single generic ASTF profile (no
   per-twin Python generation);
4. emits any router routes the address plan needs (§8.4).

New ops, alongside `drive-persona`: `provision-generator`,
`load-traffic-profile`, `start-traffic`, `update-traffic` (rate, live),
`stop-traffic`. Destroy removes generators with the tfc tier.

### 8.3 Driver

A `trex-astf` persona driver beside the existing ones. TRex runs as a
service in the generator VM; a small wrapper in the VM talks to it locally
and is invoked over the guest agent, so there is no new management
transport. Status and per-line counters come back the same way and feed the
tfc status and the canvas overlay.

A `generator` image profile is added to `catalog/image-profiles.yaml`, with
a manifest and Proxmox binding per `docs/contracts/IMAGE_MANIFEST.md`.

### 8.4 TRex constraints that shape the plan (verified 2026-11-24, §10.1)

Measured on dev45 with TRex v3.08 (template 102) on `three-tier-dc-manual`.
Replaces the unverified list this section held before.

- **Low-end mode, af_packet, one core.** `low_end: true` with Linux interface
  names (`net_af_packet`) works on virtio legs. One data-path thread per port
  pair, all on the low-end core. 200 cps / 56 Mbps of the stock HTTP profile
  used under half a core on the generator and up to 14 % of one VyOS router's
  single vCPU (A1).
- **The guest's GRO/GSO/TSO must be off** on every TRex leg. With GRO on,
  af_packet hands TRex merged super-frames, TRex drops them (`err_len_err`)
  and the server end retransmits (27,170 retransmitted packets in one 200 s
  ramp; 0 with GRO off). The shipped `trex_astf` driver does not do this.
- **In-segment emulated addresses work, in `stack: linux_based` only, and
  only when the planner hands the addresses to the port's namespace.** Legacy
  stack and bare `linux_based` answer ARP for the port IP alone; the gateway's
  entry for an emulated address goes `FAILED`. Adding each emulated address as
  a `/32` on the port's namespace veth (`trex-a-<port>-0-L`) makes the kernel
  answer ARP with the port MAC, and every flow establishes. No router route is
  needed. Routed stub pools (the `trex-test` precedent) remain a fallback, not
  the plan (A2).
- **Ports pair for numbering only.** A client port can reach any server port
  in the same TRex, not just its partner (rack1 port 0 → internet port 5:
  130/130). So the cost is one leg per (segment, role): one client leg per
  segment that has emulated clients, one server leg per segment that has
  emulated servers, padded with `dummy` to an even count. `client_mask` bit n
  = TRex port n (A3).
- **Same-segment lines need their own client leg.** TRex sends every packet
  to its port's next-hop MAC. A client leg whose next hop is the router
  hairpins same-segment traffic through it; a client leg whose `default_gw` is
  the server leg's IP sends it on L2 directly (A3).
- **Pools are split across data-path threads.** With N port pairs there are N
  threads, every pool must hold more than N addresses (else the start is
  refused: "Number of IPs in each pool must exceed the number of Data path
  threads"), and a profile limited by `client_mask` to one port uses only that
  thread's 1/N share. Planner rule: one pool per (line, leg), sized > N, or
  one generator process per client segment (A3, A4).
- **Rate changes and independent profiles work live** through one
  `ASTFClient`: `update(mult, pid_input=)` takes effect within 5 s; a second
  profile starts and stops beside running ones with 0 drops on the others;
  the daemon keeps its PID. `stop()` cuts that profile's own in-flight flows.
  Two clients that both `acquire(force=True)` steal each other's context, so
  one controller per TRex process owns all profiles (A6).
- **Identical profiles under two ids are refused** ("Fragment already added on
  other profile_id"); a profile must be removed (`stop(is_remove=True)`), not
  just stopped, before an identical one loads.
- **Leg names are not predictable past five NICs.** On i440fx the sixth leg
  came up as `enp1s1`, not `ens24`. Map legs by MAC.
- **Licence:** TRex is Apache-2.0 (upstream `LICENSE`, client headers). The
  release bundles third-party Python under its own licences (scapy 2.4.3 is
  GPL-2), used only inside the generator VM as separate processes.

## 9. Decisions for sign-off

- **T1:** persona `drive: trex-astf` and a traffic-app catalog
  (`catalog/traffic/apps/`), served through the existing persona and a new
  read-only apps endpoint.
- **T2:** tfc profile gains `emulated`, `lines` and `budget` (§8.1).
- **T3:** the tfc tier may plan and deploy generator VMs.
- **T4:** how a Real 0 group is declared (te `count: 0`, or tfc-only).
- **T5:** a `generator` image (TRex baked in) and its profile.
- **T6:** address policy for emulated instances after spike S2: in-segment,
  or routed stub pools.
  **Proposal after A2 (not signed off):** in-segment. Emulated instances take
  free addresses in their group's segment (the existing allocator), and the
  generator's wrapper adds them as `/32`s on its port namespace under
  `stack: linux_based`. No `configure-routing` change. Routed stub pools only
  where a segment has no free addresses.
- **T7:** external pools: which ranges, and that they are routed at the
  internet segment.
- **T8:** discovery default threshold for "emulate this group".
- **T9:** retire or migrate `loadgen-*` / `xseg-*`.

## 10. Spikes (before any sign-off beyond T5's image)

Run on dev45 against `three-tier-dc-manual`. Each ends with numbers or a
pasted output.

- **S1 TRex in a VM:** ASTF low-end mode on Proxmox SDN vnets. Max stable
  connections per second and Mbps for the stock HTTP profile across
  rack1 → rack3; CPU on the generator, the routers and the Proxmox host;
  with and without the tap tier.
- **S2 Addressing:** emulated client and server addresses inside
  `rack1-lan` next to real hosts. Does the gateway resolve them? If not,
  prove the routed stub pool.
- **S3 Port pairing:** one generator serving three (client, server) segment
  combinations. Pairs, VLAN sub-interfaces, or multiple instances; which
  works and what it costs.
- **S4 Sensor fidelity:** Zeek over the tapped capture of S1 produces
  `conn` with sane `conn_state`, plus `http`; repeat for a DNS and a
  replayed-TLS app (`dns`, `ssl` with SNI).
- **S5 Client-only:** TRex client against the real nginx on `rack1-web01`;
  and confirm the expected failure against mariadb.
- **S6 Live rate change** without restarting flows.

### 10.1 Results (dev45, 2026-11-24, TRex v3.08, spike VM 9701)

Scripts and raw output: `~/.axrange/spikes/trex-astf/` (outside the repo).

| Spike | Result | Evidence |
|---|---|---|
| A0 image | Reused template 102 (`img-trex-astf`, volid `axrpool:base-102-disk-0`, Debian 12, TRex v3.08). Apache-2.0. | `/opt/trex/VERSION`; upstream `LICENSE` |
| S1 throughput | 201.5 cps, 55.3 Mbps, 0 drops, 0 retransmits (GRO off), rack1→rack3 via agg-1, tap attached. agg-1 ≤ 14 % of 1 vCPU, cores < 1 %, generator 35–52 % of one core, host +2–3 % over a 31 % baseline. No-tap run not done (tap destroy not permitted in the session). | `out/a1-tap-*` |
| S2 addressing | Legacy and bare `linux_based`: gateway ARP `FAILED`. `linux_based` + namespace `/32`s: agg-1 resolves 10.200.2.100 → port MAC, 299/299 flows. | `out/a2-*` |
| S3 pairing | One TRex, 6 legs, 3 pairs, one core: rack1→rack3, rack1→rack1 (L2 direct), rack2→internet, 263–265 flows each, 100 % established; cross-pair works. VLAN sub-interfaces do not apply here: each rack segment is its own OVS bridge. Intermittent server retransmits on the rack2→internet path on a first run after idle (0 on repeat runs); no drop counter moved; not root-caused. | `out/a3-*` |
| S4 sensor | Each rack span mirrors only packets *from* the te ports present when it was installed: one span alone sees half flows (`SH`/`SHR`, `possible_split_routing`). Both spans merged: HTTP 316 `SF`, `http.log` GET 200; DNS 190 `dns.log` NOERROR; TLS 1.3 replay of a captured session 126 `ssl.log` established, SNI `portal.corp.example`. Replayed bytes show: `Host: 22.0.0.3`, fixed DNS answer `100.100.100.100`. | `out/a4/` |
| S5 client-only | Programmatic GET to the real nginx on rack1-web01 from 20 emulated rack2 addresses: 599/599, nginx logs 599 × 200. mariadb on rack1-db: 100/100 refused, because it listens on 127.0.0.1 only (Debian default; the twin's `listen: 3306` is not honoured). Its greeting carries a fresh 20-byte scramble per connection, so a replayed login cannot succeed even when reachable. | `out/a5`, nginx access.log |
| S6 live control | `update` ×1→×2→×1 tracked within 5 s; a second profile started and stopped beside two running ones; 0 drops on 182 long (20 s) flows; daemon PID unchanged. | `out/a6.txt` |

**What this changes beyond §8.4:**
- **T3 and the tap tier:** a generator added after the tap tier is invisible
  to the rack spans except via router legs, and generator↔generator traffic
  on one segment is never mirrored. Provisioning a generator must be followed
  by re-applying the tap plan with the generator's legs selected.
- **T1 apps:** replayed captures carry their original Host headers, URIs and
  DNS answers. Built-in apps should be programmatic (`ASTFProgram`) with
  templated names; replay stays for TLS-shaped sessions.
- **§6 limits:** `real_server_ok` held for plain GET (100 %). A twin's real
  databases may not listen on the LAN at all.

## 11. Phases

1. **Spikes** (§10) and the generator image.
2. **One generator, hand-written tfc.** T1–T3, T5: driver, ops, generic
   profile, 5–6 built-in apps and mixes, TRex both ends. No canvas work.
   Gate: line 3 of §5 runs and the sensor's counters climb.
3. **Canvas.** Instances split, traffic lines table, badges, health line,
   Internet users / services, traffic view toggle.
4. **Client-only and live.** `real_server_ok`, live rate edit, per-line
   counters on the overlay, schedules.
5. **Discovery.** Real / emulated decision, line synthesis, the
   rediscover-and-diff check. Later: pcap-derived apps with payload
   scrubbing.

## 12. Acceptance

- **Automated (phase 2):** the planner turns a tfc with lines into
  generators and a profile JSON deterministically; the validator refuses a
  TRex line from an all-real group, a non-`real_server_ok` mix aimed at a
  real server, and a plan over budget.
- **Automated (phase 3):** setting Emulated on a group writes tfc only; te
  is byte-identical; a line's badge and E-W / N-S tag follow its ends.
- **Live (eyes):** on `three-tier-dc-manual`, with no YAML, add the four
  lines of §5, deploy, change line 1's rate while running, and see the
  sensor's counters follow.
- **Discovery:** a twin cast from a pcap with a 200-host user LAN deploys
  with that LAN emulated, and the rediscovered imprint matches the original
  on role census and per-edge flow counts within an agreed tolerance.

## 13. Not in this design

TRex stateless mode, EMU, GTP-U, SIP/RTP and MQTT emulation, physical
generators, a metrics stack, correlated multi-hop transactions, and
impairment. Real protocol services stay real-service personas on VMs.
