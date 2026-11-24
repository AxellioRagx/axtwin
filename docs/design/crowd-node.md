# Crowd node: one generator pair stands in for a class of hosts

**Status:** SUPERSEDED 2026-11-24 by `docs/design/traffic-generation-trex-astf.md`
(the user's call); D1–D7 were never signed off. Was: DESIGN NOTE, awaiting sign-off (Phase B0 of the crowd brief,
2026-11-16). Nothing in Phase B is built. Phase A (the `trex-astf` persona
driver, the `trex-astf` golden template and the `trex-test` twin, all on
existing schema) is built and was run live on dev45. Its numbers are in
`catalog/twins/trex-test/README.md`, and they are the evidence this note
leans on.

**Loop step:** **deploy** and **drive realistic traffic**. A large twin
deploys in fewer VMs, and its tapped traffic still carries the population's
address count and protocol mix to the sensor (the capture gate).

**Decisions asked for:** §2 D1 to D4, §4 D5, §5 D6, §6 D7. Each one says what
it costs to say no.

## 1. What Phase A proved, and what it could not express

Phase A proved it on dev45. One TRex v3.08 ASTF client VM (low_end,
af_packet, one virtio NIC, 2 vCPU / 4 GiB) emulated 50 clients, and one server
VM answered for 14 server addresses. Four templates (HTTP, DNS/UDP, TLS
replay, an SMB-shaped L7 program) ran through a Debian router with segment
taps on both LANs. The sink's counter climbed (about 3,150 pps, 25.6 Mbit/s
for both taps together). Each template's established rate held within ±6% of
configured cps × mult over 5-minute windows, and a ×2 schedule step showed up
within 10 s. The exception is UDP at ×2: the server received only 50 of the
80 DNS queries per second the client sent, about 20% loss, not yet located. Discovery on a pcap of the taps found 50 clients and
14 servers listening on exactly the four template ports, with the
configured flow mix to within about one percentage point.

The existing schema could express all of that, but only by hand. These are
the parts a first-class node should own:

1. **Addressing is invisible to the twin.** The crowd prefix (10.250.1.0/24)
   is in a persona knob, the twin config does not know it exists, and the
   router learns it from a hand-written `install: script`
   (`serve-crowd-routes.sh`) that hard-codes both the prefix and the
   generator's IP. Change the crowd range in the persona and the route goes
   stale with no validator noticing.
2. **The generators' IPs must be pinned** because that script names them, and
   pinning a router leg instead silently drops the segment gateway (seen live,
   fixed by declaring `gateway:` on the segment).
3. **The twin's inventory says 3 hosts.** The canvas, the tap plan, the
   host-count gate and the cast round trip all see two generator VMs, not
   a 50-client and a 14-server population.
4. **Split mode needs two personas sharing one profile** (`profile_from:`),
   and nothing checks that the server's ranges match the client's.

## 2. Schema

**D1. A first-class `kind: trafficgen` node, not `role` + persona.** Argument
for the kind: the crowd's address range and its route are **topology**. They
must exist at deploy time and be validated against the segments and the
router, which is the te tier's job. A persona is tfc-tier behaviour,
swappable on a standing twin, and a tfc swap must never need a route change.
`role` + persona (Phase A) remains the escape hatch for a one-off and stays
supported. *Cost of no:* every crowd is a hand-written route script plus
pinned IPs, exactly §1's four problems, multiplied by the class count.

```yaml
nodes:
  - name: ws-crowd-vlan64
    kind: trafficgen
    image: trex-astf@v1
    net: vlan64                       # or nets: for a multi-segment generator (§3)
    emulates:
      count: 68                       # hosts this node stands for
      ip_range: 10.250.64.0/24        # its own prefix, NOT on-link (D3)
      peer: crowd-servers             # the trafficgen (or anchor role) it talks to
      templates:                      # defaults; tfc may override (D2)
        - { source: pcap:avl/delay_10_http_browsing_0.pcap, cps: 20 }
        - { source: program:smb-negotiate, port: 445, cps: 5 }
      schedule: [ { diurnal: { peak: 3, trough: 0.3, peak_at: "14:00" } } ]
```

**D2. Templates and schedule default in te, override in tfc.** `count`,
`ip_range` and `peer` are te only (topology). `templates` and `schedule` are
behaviour, so te holds the default and a traffic profile's assignment may
replace them on a standing twin, the way it replaces a persona today. *Cost of
no* (te only): changing a crowd's mix means a redeploy.

**D3. The crowd prefix is its own prefix, routed, never on-link.** One MAC per
generator port answers for its port IP. The router gets a static route
`ip_range -> generator port IP`. On-link ranges need per-client ARP (TRex EMU),
which stays an **open item** (§8). *Cost of no:* nothing until EMU is proven.

**IR expansion** (`twinconfig/expand.py`). A `kind: trafficgen` node becomes:

- one IR host node per generator VM (image `trex-astf`, its legs, its port IPs
  allocated like any host), carrying an `emulates` passthrough for inventory;
- one `StaticRoute{to: ip_range, via: <generator port IP>}` on each router
  that has a leg on the generator's segment. The IR already has
  `Node.static_routes`, and VyOS applies them today. **The engine work this
  needs:** the Debian `configure-router` path installs no static routes
  (corp-twin and trex-test both work around it with an `install: script`).
  Phase B adds that handler (OP-HOST-050 has none). It is the same seam, not
  a new one;
- a generated tfc assignment (`drive: trex-astf`, side from the topology: a
  `peer` that is another trafficgen makes that one the server side), so the
  twin's default traffic profile needs no hand-written personas.

**D4. Validation (new rules, numbers assigned at build):** an `ip_range` must
not overlap any segment's CIDR or another crowd's range; `count` must fit the
range; `peer` must resolve; a router must join the generator's segment (else
no return path); a trafficgen and its peer must load the same templates (the
split-mode invariant `profile_from` carries informally today). *Cost of no:*
§1's stale-route failure class comes back.

## 3. Placement

- **One client/server generator pair per segment group.** A group is the
  segments a class occupies above the threshold (§4). The client generator
  sits on the class's segment. The server generator sits where the class's
  peers are, or on its own segment behind the router when the peers are
  synthetic.
- **A few segments per VM, by NIC, not by VLAN.** TRex pairs ports (0,1),
  (2,3), and a client-only VM fills each client port with a real NIC and its
  partner with `dummy` (Phase A's `[eth0, dummy]`, extended to
  `[eth0, dummy, eth1, dummy, ...]`). Per-port VLAN on one NIC would need a
  guest-tagged leg, which axtwin does not model (a host's links are access
  only, CV-045), so v1 is multi-NIC. The ceiling is the low_end single core:
  Phase A ran about 75 cps and 25 Mbit/s mirrored per pair on one core. A
  4-segment VM is the proposed starting cap, to be measured, not assumed.
- **Cross-segment flows go through the router**, so both segments' taps see
  them: a client on segment A and its server on segment B are routed, never
  bridged, which Phase A's two-tap capture showed (each flow seen once per
  tap, two GRE keys at about 101,930 frames each in 60 s).

## 4. Cast: collapsing a class to a crowd

**D5. `aximprint cast` gains "collapse class to crowd" behind a threshold.**

- **Classes** are the cast's existing equivalence classes: the role plus the
  persona variant it already synthesizes (light / heavy / chatter / local),
  per segment.
- A class with **size >= threshold** (default 5) on one segment collapses to
  a trafficgen with `count = size`. Below it, the hosts stay as they are.
- **cps per template** comes from the class's observed flows: new
  connections per second per (dst port, transport) over the PIT's span,
  summed over the class, divided by the PIT span. The template's source is the
  closest available capture for that port, from the release's `avl/` set or
  the source capture itself (flagged as replayed payload, §7).
- **What stays real:**
  - **Anchor services:** any host that serves (`install:` or `listen:`) or is
    domain-joined (`joins:`). The crowd can talk to them only where the
    protocol survives a scripted client (UDP DNS does, Kerberos and SMB
    sessions do not; §7).
  - **Exemplars:** a configurable number of real hosts per collapsed class
    (default 1), so the class still has one host with a real OS stack, real
    background chatter and a real fingerprint for an analyst to pivot on.
  - **Network devices and bridge VMs:** they are topology, not population.

*Cost of no:* crowds are authored by hand per twin, and a cast twin never
gets smaller.

## 5. Canvas

**D6.** A trafficgen draws as the existing `×N` fold chip with N =
`emulates.count`, on its segment, with its prefix as the chip's subtitle. The
inspector shows `ip_range`, `peer`, the template table (source, port, cps),
the schedule and the **live stats** the heartbeat already returns
(`GET /api/deploys/{id}/health?host=` -> `personas[].stats`: per-template cps
and the 5-minute mean, accepts, drops, bytes, the multiplier in force). No new
UI dependency. *Cost of no:* a crowd reads as one host on the canvas.

## 6. corp-twin, worked

From `GET /api/twin-configs/corp-twin` and its `te.yaml` (2026-11-16):
**204 nodes**: 1 router and 203 hosts in 117 entries.

| class | hosts | collapsible | why not |
|---|---:|---:|---|
| generic-endpoint (no service, single-homed) | 125 | 125 | |
| generic-endpoint (serves: install/listen) | 13 | 0 | anchor (serves) |
| unroled-endpoint | 19 | 19 | |
| corp-workstation | 16 | 0 | domain-joined (AD/Kerberos must be real) |
| web-tier | 10 | 0 | serves |
| l2-switch (bridge VMs) | 10 | 0 | topology |
| dns-resolver | 5 | 0 | serves (crowd DNS can still hit it, UDP) |
| l3-switch (9K pair) | 2 | 0 | topology |
| domain-controller, file-server, sink | 3 | 0 | anchors |
| **total** | **203** | **144** | |

The 144 collapsible hosts sit on **26 segments**, unevenly: `vlan64` 68,
`lab-10-20-193` 16, `lab-10-20-200` 13, `vlan20` 8, `lab-10-20-196` 5,
`lab-10-20-204` 5. Those six hold 115. The other 29 are spread one to four
per segment over 20 segments.

**D7. With threshold 5 and one exemplar per collapsed segment:**

- **Collapsed:** 115 hosts on 6 segments become crowds of 67 / 15 / 12 / 7 /
  4 / 4 emulated hosts plus 6 real exemplars.
- **Generators:** at 4 client segments per VM, 2 client VMs. 1 server VM
  answers for the synthetic peers they dial (corp-twin's endpoints mostly
  talk to the internet sink, which is already synthetic). Total 3.
- **Stays real:** the 29 below-threshold endpoints, the 59 hosts in the
  table that are not collapsible, and the router.
- **Node count:** 204 -> 204 - 115 + 6 + 3 = **98 VMs** (about 52% fewer).
- **Threshold 3 instead:** 122 collapse on 8 segments (2 client VMs + 1
  server VM, 8 exemplars), giving 204 - 122 + 8 + 3 = **93 VMs**.

**Which hosts stay real, and why:**

- **The 16 domain-joined workstations:** Kerberos, LDAP and SMB against a real
  DC cannot be scripted.
- **The DC, file-server, 5 resolvers, 10 web-tier and the 13 serving
  endpoints:** they answer real protocols other hosts depend on.
- **The 12 bridge/L3 VMs and the router:** topology.
- **The sink:** the capture path.
- **Real exemplars:** one per collapsed segment.

## 7. Fidelity limits (documented, not fixed)

From Phase A, and inherent to the approach:

- **One MAC per generator port.** Every emulated client on a segment shares
  its generator's MAC; a sensor keying on MAC sees one host.
- **One TCP/IP fingerprint:** TRex's own stack (window 32768, its option set,
  TTL), identical for every emulated host; the JA3/JA4 of a replayed TLS
  session is the capture's.
- **No background chatter** (ARP, DHCP, mDNS, NetBIOS, LLMNR, NTP) unless it
  is templated. A crowd host is silent between flows.
- **Replayed payloads are identical byte for byte**, TLS ciphertext and
  certificates included (discovery saw one certificate, 14 servers).
- **No real stateful service on the far side.** A crowd cannot authenticate to
  AD/Kerberos, hold a real SMB session or recurse through a real resolver;
  those stay real VMs (§4). UDP request/response (DNS) to a real server does
  work.
- **TRex v3.08 scheduler bug:** some exact cps ratios abort the daemon
  (`KxuNuRand`; the builder nudges every template's cps by ~1e-6 to break
  them). Watch for it on a version bump.

## 8. Open items

- **Per-client ARP for on-link ranges (TRex EMU).** Not built (brief). Needed
  before a crowd can live inside its segment's own CIDR.
- **The image build's smoke test cannot run** on any image: its default
  exporter op is config-class and the build executor wires no config backend.
  `trex-astf` was built with `--skip-smoke` after a smoke run left a VM, zone,
  vnet and bridge behind (torn down with the guarded destroy).
- **The Debian `configure-router` static-route handler** (§2): needed by B,
  useful to corp-twin today.
- **UDP loss at ×2** (about 20% of DNS queries never reach the server):
  locate it before sizing generators by cps (§3's 4-segment cap depends on
  it).
