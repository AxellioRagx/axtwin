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

**Unverified:** every statement about TRex behaviour in this doc comes from
recollection of the TRex ASTF manual, not from a test. §10 lists the spikes
that turn them into facts. Do not build on a TRex claim before its spike
passes.

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

### 8.4 TRex constraints that shape the plan (all unverified)

- **Low-end mode on virtual NICs** is the only mode in a Proxmox VM.
- **Ports come in client/server pairs.** A flow leaves a pair's client port
  and must arrive at the same pair's server port. Each distinct
  (client segment, server segment) combination needs a pair, or VLAN
  sub-interfaces on a trunk leg, or a second TRex instance. The planner
  hides this; the spike sizes it.
- **Emulated addresses inside a segment that also has real hosts** must
  answer ARP. If TRex cannot do that in this mode, emulated instances get
  addresses from a per-segment stub pool routed via the generator's leg, and
  "replace at the same address" is not available.
- **Rate can change while running;** profiles can start and stop
  independently. `update-traffic` relies on this.
- **TRex is Apache-2.0** (to confirm), a separate process, so compatible
  with axtwin's proprietary licence.

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
