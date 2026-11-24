# Traffic generation with TRex ASTF: emulated instances and traffic lines

**Status:** phase 2 in build. What is signed off and built lives in
`engine/src/axrange/trafficgen/SPEC.md`; this doc keeps the decisions still open (§9), the
spike record (§10) and the later phases (§11). Retire it when §9 is closed
and phases 3–5 are built or dropped.
**Supersedes** the crowd-node note (D1–D7, never signed off; removed
2026-11-24, in git history at `61764a0:docs/design/crowd-node.md`). Its
`kind: trafficgen` te node is not built; the Phase A evidence it cited
(`catalog/twins/trex-test/README.md`) still counts.

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

## 8. Storage and engine (built 2026-11-24, phase 2)

The contract of what was built is `engine/src/axrange/trafficgen/SPEC.md`;
this section records the shape and where it lives.

### 8.1 Where it is written

As proposed: the te schema does not change. `budget`, `emulated` and
`lines` live in the tfc profile (`trafficgen/profile.py`, hooked into
`parse_traffic_profile`; unknown keys inside the blocks are refused).
External groups are tfc-only (`external: true`, `segment`, `role`). The
gate's profile is `catalog/twins/three-tier-dc-manual/tfc/trafficgen.yaml`.

### 8.2 Planning

`trafficgen/plan.py`, pure and deterministic (a golden plan pins it):

1. addresses: each emulated group takes the first contiguous block of free
   addresses in its segment (ASTF pools are ranges), read from a static
   view of the te file's expansion (`trafficgen/twin.py`); each generator
   leg then takes one address;
2. one generator VM per distinct (client segment, server segment), one TRex
   port pair each (A1, A3, A5); an emulated group on two generators is
   refused in this phase;
3. one document per generator for the one generic ASTF profile
   (`trafficgen/astf_profile.py`); programs rendered to hex at plan time;
4. no router routes (in-segment addresses, A2).

Ops (`trafficgen/compose.py`): the catalog's clone / hardware / cloud-init
/ attach (literal inputs from the te deploy's recorded bridges and vnets) /
start / wait-guest-agent, then `provision-generator`, `load-traffic-profile`,
`start-traffic` (OP-TFC-030..050); `update-traffic` (OP-TFC-060) and
`stop-traffic` (OP-TFC-070) run outside apply. Validator rules PSN-120..140
and TGN-010..070 (`trafficgen/validate.py`). Destroy removes the generators
through their `clone-vm` rows.

### 8.3 Driver

`personas/drivers/trex_generator.py` beside the per-VM `trex_astf.py`:
pushes the in-VM wrapper (`trafficgen/guest_tg.py`), the generic profile,
the document and replay files over the guest agent, legs found by MAC. In
the VM one controller per TRex owns the ASTF client (A6), reconciles a
desired state, re-asserts the namespace addresses, holds each line under
`max_concurrent` and the budget's Mbps by lowering its multiplier, and
writes per-line stats; `GET /api/tfc` and `axrange tfc status
--adapter-config` read them. Live rate: `axrange tfc update-traffic`,
`POST /api/deploys/{id}/traffic-lines/{line}`.

Not built in phase 2 (open): lines in `POST /api/tfc`'s request body (lines
come from the twin's tfc file: CLI `tfc deploy`, deploy follow-on); a
narrowing re-apply that drops a whole generator leaves its VM until a tfc
destroy (the dropped lines on a kept generator are removed); the tap tier
does not select generator legs (T3 §8).

### 8.4 TRex constraints that shape the plan

Verified by the spikes (§10.1); the list is kept in `engine/src/axrange/trafficgen/SPEC.md`
("TRex constraints the plan must respect").

## 9. Decisions for sign-off

Outcome 2026-11-24: **T1, T2, T3, T5 signed off** and built (phase 2).
T4 is decided for external groups only (tfc-only, part of T2); internal
Real 0 groups stay open. **T6** is proposed (in-segment) and built that way
for phase 2, not yet signed off on its own. **T7, T8, T9 open.**

- **T1:** persona `drive: trex-astf` and a traffic-app catalog
  (`catalog/traffic/apps/`), served through the existing persona and a new
  read-only apps endpoint.
  **Signed off 2026-11-24**, amended by the user: the feature gets its own
  `engine/src/axrange/trafficgen/SPEC.md` (the apps registry moves to
  `axrange.trafficgen.apps`). A mix is a `trex-astf` persona with a `mix:`
  list; apps live in `catalog/traffic/apps/<id>/app.yaml`;
  `GET /api/personas` adds `mix` and `real_server_ok`;
  `GET /api/traffic-apps[/{id}]` is read-only. Contract: the spec ("Config").
- **T2:** tfc profile gains `emulated`, `lines` and `budget` (§8.1).
  **Signed off 2026-11-24** ("approved as written"). Fitted to the real
  parser (`o2control/traffic.py: parse_traffic_profile`). Three optional
  top-level keys beside `target`, `roles`, `assignments`, `replay`; nothing
  existing changes. A profile with only the new keys is valid. The parser
  ignores unknown top-level keys today, so inside the new blocks every
  unknown key is refused (fail loud).

  ```yaml
  traffic-profile:
    target: three-tier-dc-manual
    assignments:                     # unchanged: the twin's real personas
      - { role: web-tier, persona: web-tier }
      - { role: db-tier,  persona: db-tier }
    budget: { cps: 100, mbps: 60 }   # required when lines[] is present
    emulated:
      - { group: rack1-web, count: 20 }   # a te node group: role and segment from te
      - { group: rack3-db,  count: 1 }
      - { group: rack2-web, count: 5 }
      - { group: internet-users, external: true, segment: internet,
          role: internet-user, count: 200 }
    lines:
      - { from: rack1-web, to: rack3-db, mix: sql-oltp, rate_cps: 20, max_concurrent: 100 }
      - { from: internet-users, to: rack2-web, mix: web-browse, rate_cps: 30 }
  ```

  | key | type | rule |
  |---|---|---|
  | `budget.cps` | number > 0 | ceiling on the sum of `lines[].rate_cps`, at validate and on every `update-traffic` |
  | `budget.mbps` | number > 0 | ceiling on estimated Mbps (rate × app bytes) at validate; enforced live by the generator's controller (below) |
  | `emulated[].group` | string | a te node group name (`nodes[].name`), or an external group's name; each at most once |
  | `emulated[].count` | int ≥ 1 | instances added to the group, named `<group>-e01`… |
  | `emulated[].external` | bool, default false | true: a tfc-only group, not in te |
  | `emulated[].segment` | te segment name | required iff `external` |
  | `emulated[].role` | string | required iff `external`; else the te group's role |
  | `emulated[].start` | IPv4, optional | first address to try; default: the segment's lowest free address |
  | `lines[].id` | string, optional | default `<from>--<to>--<mix>`; unique; the handle `update-traffic` takes |
  | `lines[].from` / `to` | group name | must appear in `emulated[]` (this phase: both ends emulated) |
  | `lines[].mix` | persona id | a `trex-astf` persona with `mix:` |
  | `lines[].rate_cps` | number > 0 | new connections per second for the whole line, split over the mix by share |
  | `lines[].max_concurrent` | int ≥ 1, optional | default `rate_cps × 10` |

  - **Addresses** are allocated at plan time, deterministically: ascending
    from `start` (or the lowest host address) in the group's segment,
    skipping the gateway, every router leg, every te host address (pinned in
    te or recorded in the deployed inventory) and addresses already handed
    out. The policy is T6's proposal (in-segment, spike A2).
  - **Groups → hosts:** the planner reads the twin config's `nodes[]`
    (read-only; group name → instance ids, as `static_hosts_from_twin_config`
    does) and the deployed inventory (ids → IPs). The te file is never
    written.
  - **Caps are enforced in the generator, not only at validate.** Rate:
    each app's template `cps` is the line's rate times the app's share, and
    TRex's scheduler holds it (A1, A6). Concurrency and Mbps: TRex ASTF has no
    per-profile cap, so the in-VM controller reads each line's active flows
    and the generator's tx rate every few seconds and lowers that line's
    `mult` while it is over `max_concurrent` or the budget's Mbps, and
    `update-traffic` refuses a rate whose sum exceeds `budget.cps`.
  - **This phase refuses**, with a reason: a line whose `from` or `to` has no
    emulated instances, any line to a real server, and a `from` group that is
    all real.
  - **Externals are tfc-only groups.** This decides T4 for external groups
    only; an internal Real 0 group stays open under T4. Their pool is free
    addresses of the named segment (A2), not a public-looking range; T7 stays
    open.
  - Not in T2: schedules, per-line port override, client-only lines.

  *Cost of no* (lines on te instead): every traffic change becomes a te
  redeploy, against the design's "te does not change".
- **T3:** the tfc tier may plan and deploy generator VMs.
  **Signed off 2026-11-24** ("approved as written").

  1. **Model: hand-composed ops, like the tfc and tap tiers, not the O2
     surface's IR route.** The O2 surface builds an IR and runs
     `build_plan_and_ctx(tier="o2")`, but that emits the networks too. A
     generator joins the te's *existing* bridges, so, like `taps.py`, the tfc
     tier reads the te deploy's recorded outputs (bridge names, VLAN tags,
     vnet ids) and composes `OpInstance`s by hand. They run through the same
     executor and are torn down by `planner.teardown.destroy_deploy`. Naming
     and teardown already support it: `vm_name_for_host(tier="tfc")` gives
     `tfc-<6hex>-<host>`, and the tfc teardown gate accepts that shape.
  2. **Ops per generator,** reusing catalog ops with literal inputs:
     `clone-vm` (template from the `generator` image profile → binding),
     `configure-vm-hardware` (cores and memory per T5),
     `attach-vm-to-switch-port` (OVS segments: `bridge_name`, `vlan_tag`) or
     `attach-vm-to-vnet` (SDN segments: `vnet_id`) per leg, with deterministic
     MACs (`mac_for_nic`); then `start-vm`, `wait-guest-agent`. Then five new
     config-class ops over the guest agent (catalog `OP-TFC-020`…`060`), run
     by the tfc tier's existing guest-agent backend beside `drive-persona`:

     | op | does | retry |
     |---|---|---|
     | `provision-generator` | push the wrapper, generic profile and `trex_cfg.yaml`; offloads off, hugepages, `/32`s on the port namespaces; (re)start the TRex unit | converges: re-renders everything, restarts the unit only if `trex_cfg` changed, adds addresses idempotently |
     | `load-traffic-profile` | one ASTF profile per line (profile id = line id) from the generator's profile JSON | remove the profile id, then load |
     | `start-traffic` | start each line's profile with its client port mask | no-op if already transmitting |
     | `update-traffic` | `update(mult)` for one line, live (not in the apply plan; §5 below) | idempotent (sets, not adds) |
     | `stop-traffic` | stop and remove a line's profile (a line dropped on re-apply) | no-op if absent |

     The wrapper is the one controller per TRex process (A6): it owns the
     `ASTFClient`, enforces `max_concurrent` and the budget's Mbps (T2), and
     writes per-line stats for `heartbeat`. One tfc deploy holds both the
     existing `drive-persona` ops and the generator ops, with no dependency
     between them, so the real personas run as today.
  3. **Grouping: one generator VM per distinct (client segment, server
     segment) of the lines.** Lines that share a combination share its VM.
     Each VM runs one TRex process with one port pair: client leg on the
     `from` segment, server leg on the `to` segment (a same-segment
     combination: client leg's `default_gw` = the server leg's IP, §8.4).
     Reason: in a single-pair process any pool size works (A1, A5); with N
     pairs every pool needs more than N addresses (A3), and the gate's
     `rack3-db +1` is a 1-address pool. Packing several pairs into one VM is
     a later planner optimisation behind the same output, not phase 2.
     Gate: `rack1-lan → rack3-lan` and `internet → rack2-lan` → **2 VMs**,
     each 2 legs, 2 vCPU, 2.2 GiB. Planned names `gen-<from-seg>--<to-seg>`;
     leg IPs are allocated like emulated addresses (T2), one per leg.
  4. **Images root:** tfc apply now needs the images root (`--images`, the
     API's existing setting), but only when the profile has lines. A profile
     without lines plans exactly as today.
  5. **Live rate change:** `axrange tfc update-traffic <tfc-deploy> --line
     <id> --rate <cps>` and `POST /api/deploys/{id}/traffic-lines/{line}`
     `{rate_cps}`. Both check `budget.cps`, run `update-traffic` on that
     line's generator, and record the new rate on the deploy, so status and a
     later re-apply agree. Re-applying the tfc file with only a rate changed
     plans `update-traffic` instead of a reload.
  6. **Status:** per-line counters (connections opened, established,
     errors, bytes) come from the wrapper's stats through `heartbeat`, and
     appear in the tfc status the API already serves, keyed by line id.
  7. **Destroy:** tfc destroy removes the generator VMs (`clone-vm`'s cleanup)
     with everything else in the tier. A te destroy cascades as today.
  8. **The tap tier is unchanged in phase 2,** a documented limit. The spans
     select the te's ports, so a routed line is still mirrored (once per
     direction, on the router legs of its two segments), which is what the
     gate's two lines are. A same-segment generator↔generator line is
     invisible to the sensor until the tap tier also selects generator legs.
     That is a follow-up to sign off separately, not part of T3.

  *Cost of no* (generators as te nodes instead): a traffic edit becomes a te
  redeploy, and te would carry tfc-only VMs.
- **T4:** how a Real 0 group is declared (te `count: 0`, or tfc-only).
- **T5:** a `generator` image (TRex baked in) and its profile.
  **Signed off 2026-11-24** ("approved as written"): no new image; the
  generator is `trex-astf@v1` behind a Proxmox-only `generator` image
  profile. Contract: `engine/src/axrange/trafficgen/SPEC.md` ("Generator image").
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

### 10.2 Phase 2 deploy gate (dev45, 2026-11-24)

Profile `catalog/twins/three-tier-dc-manual/tfc/trafficgen.yaml` (by hand)
applied with `axrange tfc deploy` from this checkout onto
`three-tier-dc-manual-e6429691`; evidence in `~/.axrange/gate-trafficgen/`.

- **Ops:** every tfc op completed: 9 real `drive-persona` instances (web-tier
  x6, db-tier x3) and both generators' 10 ops each
  (`gen-rack1-lan--rack3-lan`, `gen-internet--rack2-lan`; 2 vCPU, 2253 MiB,
  legs on the te's bridges, VLAN tags 70/90 and vnet + tag 80).
- **Counters climb** (`axrange tfc status --adapter-config`, 30 s apart):
  web-browse 909 -> 1821 established (30.1 cps), sql-oltp 282 -> 930
  (19.8 cps), 0 errors.
- **Sensor:** XPress-SW-A's capture NIC received 19,161 packets in 21 s; of
  the four tap feeds' 17,693 frames in 20 s, 17,524 carry emulated addresses.
- **Zeek 8.0 over the merged feeds:** 948 `SF` flows from 220 distinct
  emulated clients to 6 emulated servers; `http.log` 385 x GET /index.html
  200 Host rack2-web (10.250.0.x -> 10.200.1.5-9); `mysql.log` 380 x login
  app / SELECT / UPDATE / quit (10.200.0.11-30 -> 10.200.2.5); `ssl.log` 185
  x TLS 1.3 SNI portal.corp.example.
- **update-traffic** 30 -> 60 cps on web-browse: 59.6 cps measured
  (5408 -> 7355 in 32 s), TRex PID 538 before and after, 0 errors, the other
  line unchanged.
- **Destroy** removed both generator VMs after one fix (the tfc teardown had
  no node for a placement-less deploy, `d36df1d`). The twin's original
  `tfc/default.yaml` was re-applied after.

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
