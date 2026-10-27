# Gap Analysis — aximprint discovery vs. corp network ground truth

> **STATUS: INPUT FOR WORK-ORDER GENERATION.** Authority: user (2026-09-15, chat session).
> Compares the aximprint discovery output for capture `corp-apcn-rdr1` (UI at
> `http://10.80.72.52:8088/#/` and `#/physical`) against the hand-drawn corp topology.
> Relates to `docs/DISCUSSION_pcap_discovery.md` (§2.1 vantage, §2.4 scanners, §3.2/§3.5
> control plane, §5 belief store, §8 round-trip self-test).
>
> **How to consume this file.** §5 is a list of gap items `G-01 … G-11`. Each item carries
> `tier`, `repo`, `evidence`, `build`, `boundaries`, `validation`, `depends_on`. Generate one
> work order per item (or per §6 bundle) in the house `WORK_ORDER_*.md` shape
> (Goal / Build / Boundaries / Validation / Depends on). Validation targets in §4 are the
> acceptance oracle for every order.

---

## 1. Ground truth (corp diagram, hand-authored)

```yaml
site: axellio-hq
edge:
  device: MX85            # Cisco Meraki, "HQ-MX"
  role: L3 gateway for every VLAN
  vlans: [1, 20, 32, 40, 48, 52, 64, 192, 880, 1010]
  wan1: 50.209.234.65     # active
  wan2: 50.209.234.78     # ready / idle
core:
  device: MS120-24P       # Cisco Meraki, "Telstar-Switch"
  role: L2 access/distribution (no L3)
  mgmt_ip: 10.80.16.15
  ports: 28
  uplinks_to_edge:
    - { port: SFP25, state: forwarding }
    - { port: SFP26, state: STP-blocked }
access_switches:           # all hang off MS120
  - { name: SGS-5240-24T4X,        core_port: P1,  mode: trunk, native_vlan: 20 }
  - { name: AxeProdSW.Axellio,     core_port: P14, mode: trunk, native_vlan: 1 }
  - { name: cos-sw-r32e01.axellio.dom, core_port: P16, mode: trunk, native_vlan: 1 }
  - { name: labswitch,             core_port: P19, mode: trunk, native_vlan: 1 }
  - { name: hwlab2960s.Axellio,    core_port: P17, mode: access, vlan: 32, state: down }
  - { name: "3850",                core_port: P21, mode: trunk, native_vlan: 1 }
l3_tor:
  device: 9K01 / 9K02      # Cisco Nexus, vPC + HSRP
  uplink: { via: "3850", port_channel: Po48, mode: vPC trunk, vlans: all }
  svis:
    - { vlan: 32,  prefix: 10.80.32.0/21 }
    - { vlan: 936 }
    - { vlan: 953 }
l2_vsx:
  device: eng-8325h left/right   # Aruba 8325 VSX pair
  uplink: { via: 9K, port_channel: Po54, vlan: 953 }
  serves: { vlan: 953, prefix: 10.80.34.0/25, hosts: servers/ESX }
switch_count_total: 9      # MX85 excluded; MS120 + 6 access + 9K pair + 8325 pair (pairs count as one)
```

## 2. What aximprint observed (capture `corp-apcn-rdr1`)

```yaml
capture:
  window: 2026-05-24T01:11Z → 2026-06-19T01:11Z   # 26 d
  bytes: 17.8 TB   packets: 19B   streams: 12   axmeta_chunks: 53
  endpoints: 488k  internal_24s: 45  vlan_tags_observed: [untagged, 20, 48, 64, 192]
edge:
  nat_egress: 50.209.234.65   # correct = WAN1
  wan2: not observed          # correct, idle
core_router:
  mac: 08:f1:b3:88:42:3a      # Cisco Meraki OUI
  gateway_ips: [10.20.196.1 (my.meraki.net, dhcp+dns+gw+rtr), 10.80.64.1, 192.168.0.105, 192.168.0.253, 192.168.0.47]
  routes_for: 811 internal addresses
  identified_as: "inter-segment router"      # correct = MX85
tap_placement_inference: "SPAN/mirror on the Meraki itself"   # right conclusion, wrong argument (see G-06)
redundant_gateways:                            # HSRPv2 virtual MACs — correct = 9K01/9K02
  - { vmac: 00:00:0c:9f:f0:20, group: 32,  segment: 10.80.32.0/24 }
  - { vmac: 00:00:0c:9f:f3:b9, group: 953, segment: 10.80.34.0/24 }
other_routers:
  - { mac: 08:92:04:6a:60:ca, oui: Dell Inc., role: "inter-segment router, none fingerprinted",
      fronts: [10.20.194/24, 10.20.195/24, 10.80.33/24, 10.80.34/24], addresses: 567 }   # structurally the 9K tier; unnamed
  - { mac: 40:0f:c1:3c:72:35, oui: Vantiva, ips: [10.1.10.1, 10.1.10.191] }               # ISP CPE upstream of MX85; not on diagram, real
switches_identified: 0 of 9
switch_ports_identified: 0
stp_lldp_cdp: "parsed but only counted — no neighbour records"
arp_events: none queryable (/api/events?type=arp empty)
ttl: conn.orig_ttl / conn.resp_ttl == 0 throughout
l2_evidence_sampling: "/api/search/rows caps at 10k rows; union of 32 queries"
segments_of_note:
  - { prefix: 10.80.32.0/24, hosts: 4,   macs: 4  }   # truth: part of 10.80.32.0/21
  - { prefix: 10.80.33.0/24, hosts: 100, macs: 3  }   # 100 addr on 3 MACs → scan-inflated
  - { prefix: 10.80.34.0/24, hosts: 98,  macs: 4  }   # truth: 10.80.34.0/25 VLAN953
  - { prefix: 10.20.194.0/24, hosts: 200, macs: 4 }   # scan-inflated
  - { prefix: 10.20.195.0/24, hosts: 200, macs: 21 }  # scan-inflated
  - { prefix: 10.20.205.0/24, hosts: 254, macs: 255, vendor: VMware ×252 }  # lab/twin range, real
  - { prefix: 10.20.206.0/24, hosts: 256, macs: 256, vendor: VMware ×256 }  # lab/twin range, real
  - { prefix: 10.80.16.0/24, observed: false }        # MS120 mgmt subnet never seen
vlan_labels: "/16-level labels (10.80/16 → 'VLAN 20/64', 10.20/16 → 'VLAN 192') contradict per-segment observed tags"
```

## 3. Accuracy scorecard

| Layer | Truth | Discovered | Verdict |
|---|---|---|---|
| WAN edge / NAT | WAN1 .65 active, WAN2 .78 idle | .65 egress; .78 absent | **Correct** |
| Core L3 gateway | MX85 gateways every VLAN | Meraki MAC `…42:3a`, 811 addr, dhcp/dns/gw | **Correct** (unnamed) |
| Inter-segment routing | via MX85 (MS120 is L2) | 3.4 TB EW all via Meraki MAC | **Correct** |
| Conversation / service graph | — | full, with protocol mix | **High** (as predicted in DISCUSSION §3.1) |
| Redundant L3 TOR (9K vPC/HSRP) | groups 32, 953 | HSRPv2 vMACs found, members unnamed | **Partial** |
| 9K device identity | Cisco Nexus pair | "Dell Inc., none fingerprinted" | **Wrong/unknown** |
| Prefix boundaries | 10.80.32.0/21, 10.80.34.0/25 | three /24s | **Wrong** (no ARP/DHCP-mask evidence) |
| VLAN inventory | 10 VLANs | 5 tags | **50%** |
| VLAN 936 / 953 / 32 as tags | on 9K side | not observed | **Missing** |
| L2 switches | 9 | 0 | **Blind** |
| Switch ports / trunk native VLANs | per diagram | none | **Blind** |
| MS120 mgmt (10.80.16.15) | exists | segment not seen | **Missing** |
| Host census | — | 200/200/100-addr segments on 3–21 MACs | **Inflated** by scans |
| Tap placement | (SFP25 trunk or MX SPAN) | "SPAN on Meraki", inline excluded by false argument | **Over-claimed** |
| Extras not on diagram | — | Vantiva CPE 10.1.10.x; VMware lab /24s | **Real, benign** |

**One-line summary:** L3 and conversation graph are right; prefix/VLAN slicing is guessed; the entire L2 fabric is blind. Most of the L2 blindness is *structural to the vantage* (MS120 sits between the tap and every access switch), but a meaningful slice is *evidence the pipeline already has and discards*.

## 4. Acceptance oracle (use in every work order's Validation)

Re-run discovery on the same 53 axmeta chunks after each order and score against §1:

```yaml
oracle:
  switches_named:           { now: 0/9,  floor_after_G01: 2/9 (MS120, MX85), target_after_G01: 5/9 }
  switch_ports_named:       { now: 0,    target_after_G01: "MS120 SFP25+SFP26; 3850/2960s/9K ports toward MS120 if PVST+ leaks" }
  vlans_enumerated:         { now: 5/10, target_after_G01+G02: 10/10 }
  prefixes_correct:         { now: 0/2,  target_after_G03: "10.80.32.0/21 and 10.80.34.0/25 emitted as single segments" }
  hsrp_members_bound:       { now: 0/2,  target_after_G04: "both vMACs bound to two physical chassis IDs" }
  scan_inflated_segments:   { now: 3,    target_after_G05: 0 (10.20.194, 10.20.195, 10.80.33 host counts ≤ answered hosts) }
  ttl_populated:            { now: 0%,   target_after_G07: ">95% of conn rows non-zero" }
  arp_queryable:            { now: false, target_after_G07: true }
  l2_evidence_census:       { now: "32×10k sampled", target_after_G08: "unsampled or explicitly complete" }
  tap_placement_claim:      { now: "inferred SPAN", target_after_G06: "declared from tap plan; inference downgraded to 'consistent with'" }
  provenance_on_fabric:     { now: none, target_after_G09: "every switch/port/VLAN fact tagged asserted|inferred with source protocol" }
```

## 5. Gap items

Field key: `tier` = which part of the pipeline (`decoder` = packet/TLV parsing in aximprint; `belief` = belief store / entity resolution; `api` = axQuery endpoints; `ui` = topology views; `capture` = o2 tap plan / sensor placement; `enrich` = out-of-band management-plane sources; `test` = oracle/self-test).

### G-01 — Parse STP/LLDP/CDP into neighbour records (stop counting)

- **tier:** decoder → belief → ui
- **repo:** aximprint
- **priority:** P0 — cheapest win; no new capture, no integration
- **evidence:** Physical view states "STP/LLDP/CDP frames are parsed but only counted, so no neighbour records exist." These are the only frames in the capture where a switch *asserts* its own identity; everything else about the fabric is inferred from host frames.
- **build:**
  - IEEE BPDU (`01:80:c2:00:00:00`, RSTP/MSTP): emit `root_bridge_id`, `sender_bridge_id` (priority+MAC), `port_id` (→ "SFP25"/"SFP26"), `root_path_cost`, `tc_flag`, timers. Keep the 802.1Q tag if present.
  - Cisco PVST+/Rapid-PVST SSTP BPDU (`01:00:0c:cc:cc:cd`, per-VLAN, tagged): separate parser path; emit the same fields plus `vlan_id` from the tag and the SSTP TLV. **Key property:** SSTP address is *not* in the reserved block, so non-Cisco bridges (the MS120) flood it → 3850 / 2960s / 9K BPDUs likely already reach the SFP25 trunk, one per VLAN they trunk. This is how VLANs 32/936/953 become observable without host traffic.
  - LLDP (`01:80:c2:00:00:0e`, link-local, consumed by bridges → only the two ends of the tapped link): emit Chassis ID, Port ID, Port Description, System Name, System Description (model/firmware), System Capabilities, Management Address, 802.1 Port VLAN ID (native VLAN), 802.1 VLAN Name list, Link Aggregation TLV.
  - CDP (`01:00:0c:cc:cc:cc`): emit Device ID (hostname), Port ID, Platform, Software Version, Capabilities, Mgmt Address, Native VLAN, VTP domain, Duplex. Meraki MS speaks CDP so probably consumes it; check empirically.
  - Belief-store assertions in the §5 DISCUSSION shape, e.g. `(chassis X, has_port, "25")`, `(chassis X, system_name, "Telstar-Switch")`, `(chassis X, mgmt_addr, 10.80.16.15)`, `(port X/25, native_vlan, 1)`, `(port X/25, trunks_vlans, [...])`, `(port X/25, adjacent_to, port Y/n, evidence: stp+lldp)`, `(bridge 3850, stp_port_toward, bridge MS120, evidence: pvst)`.
  - Dedup per `(source MAC, TLV set)` with `first_seen/last_seen/count` so 26 days × 2 s BPDUs collapse to one lifetime record. Record STP `tc_flag` transitions as timestamped events (link-flap history), not folded into the record.
  - Join chassis IDs against MACs already in the physical view ("Other carriers on the wire", "Directly adjacent" tables); Meraki bridge/interface MACs are typically a few addresses apart in one OUI block.
  - UI: new **Switches** tier between router and segments in `#/physical`, each fact badged `asserted (lldp|cdp|stp)` vs `inferred (mac-fanout)`.
- **boundaries:** No new capture, no management-plane APIs (that is G-10). Do not change segment/VLAN discovery logic (G-02) beyond consuming the new VLAN evidence.
- **validation:** Oracle §4 `switches_named ≥ 2/9` (MS120 with name, model, mgmt IP, ports 25/26, native VLAN, VLAN list; MX85 chassis named). If SSTP BPDUs are present: 3850, 2960s, 9K bridge IDs + per-VLAN trunk lists; VLAN 32/936/953 appear. Unit tests on canned LLDP/CDP/IEEE-BPDU/SSTP-BPDU frames. **First step is a 10-minute probe:** count distinct source MACs on the existing STP/LLDP/CDP counters; >1 source on STP means PVST+ is leaking and the ceiling is ~5–6 switches, not 2.
- **depends_on:** none. Unblocks G-02, G-04, G-09.

### G-02 — VLAN inventory: reconcile /16-level labels with per-segment observed tags

- **tier:** belief → api → ui
- **repo:** aximprint
- **priority:** P1
- **evidence:** 10.80/16 labelled "VLAN 20/64" while its segments show untagged/20/64/48/192; 10.20/16 labelled "VLAN 192" while most segments are untagged VMware ranges. 5 of 10 VLANs observed. Native VLAN on the tapped trunk makes one VLAN look "untagged".
- **build:** Label VLAN per segment from its own dominant tag; treat "untagged" as `native_vlan_of_tap_link` (a variable resolved from G-01 LLDP Port VLAN ID, else unknown), not as a VLAN. Merge G-01 SSTP per-VLAN BPDUs and LLDP VLAN Name lists into the VLAN inventory so VLANs with no host traffic on the trunk (32, 936, 953, 40, 52, 880, 1010) are still listed with `evidence: control-plane`, `hosts: 0`.
- **boundaries:** Don't invent VLAN→prefix bindings without evidence; a VLAN with no observed hosts is listed, not populated.
- **validation:** Oracle `vlans_enumerated` → 10/10 given G-01 leakage; per-segment labels no longer contradict the /16 label (drop the /16 label or derive it as a set).
- **depends_on:** G-01.

### G-03 — Real prefix boundaries (ARP scoping + DHCP masks + HSRP group hints)

- **tier:** decoder → belief
- **repo:** aximprint
- **priority:** P1
- **evidence:** Discovery emits 10.80.32/24, 10.80.33/24, 10.80.34/24; truth is 10.80.32.0/21 (VLAN32) and 10.80.34.0/25 (VLAN953). No ARP events queryable; masks never used.
- **build:** (a) ARP scoping — who-ARPs-whom within one broadcast domain vs who-uses-gateway draws the real boundary; (b) DHCP option 1 (subnet mask) + option 3 (router) from offers/acks; (c) IPv6 RA prefixes; (d) HSRP group number as a hint (group 32 ↔ VLAN 32 already noted in UI). Emit `segments[].cidr` with per-field provenance and merge adjacent /24s that share gateway MAC + ARP domain + DHCP mask into the evidenced prefix.
- **boundaries:** Keep /24 as the fallback when no mask evidence exists; never widen on gateway-MAC sharing alone (14 segments share the Meraki MAC because it routes them, not because they're one domain — the UI already warns about this).
- **validation:** Oracle `prefixes_correct` 2/2; no regression on segments that truly are /24.
- **depends_on:** G-07 (ARP events must be queryable / retained).

### G-04 — Bind HSRP/VRRP virtual MACs to physical chassis members

- **tier:** decoder → belief → ui
- **repo:** aximprint
- **priority:** P1
- **evidence:** HSRPv2 vMACs for groups 32 and 953 found; members unnamed. The device fronting 567 addresses across 10.20.194/195 and 10.80.33/34 is labelled "Dell Inc., none fingerprinted" — structurally it is the 9K01/9K02 tier.
- **build:** Decode HSRP hello payload (active/standby router IPs, priority, state, hold timers) and VRRP advertisements; assert `(vmac, served_by, physical_mac)` for both members; cross-reference with G-01 PVST+ bridge IDs from the same chassis. Re-label the "Dell Inc." carrier: either the OUI DB is wrong for `08:92:04` or the frames belong to an intermediate — present both hypotheses with evidence rather than a vendor string.
- **boundaries:** Do not assert Nexus/9K by name from OUI; name only from CDP/LLDP (G-01) or management plane (G-10).
- **validation:** Oracle `hsrp_members_bound` 2/2; "Redundant gateways" table shows both members' MACs and IPs per group.
- **depends_on:** G-01 (chassis IDs).

### G-05 — Separate attempted vs answered vs established hosts (scanner suppression)

- **tier:** belief → api → ui
- **repo:** aximprint
- **priority:** P1
- **evidence:** 10.20.194/24 = 200 addr on 4 MACs; 10.20.195/24 = 200 addr on 21 MACs; 10.80.33/24 = 100 addr on 3 MACs. DISCUSSION §2.4.
- **build:** Per address, classify `attempted` (SYN/probe, no reply), `answered` (RST/ICMP unreachable proves existence of *something* but not a host at that address), `established` (handshake completed / bidirectional payload). Host counts in the overview, "Zones and infrastructure" and physical view use `established` (+ `answered` with a flag); `attempted` shown as a scan overlay with the scanning source(s) identified. Emit `scan_sources[]` as an exercise-tier seed (DISCUSSION §3.4).
- **boundaries:** Don't drop the addresses — keep them as evidence with the lower class; the twin's `nodes[].count` must not include them.
- **validation:** Oracle `scan_inflated_segments` → 0; identified scanner source(s) listed; VMware lab ranges (10.20.205/206, 1 MAC per address) are *not* suppressed.
- **depends_on:** none.

### G-06 — Vantage as declared input, not inference

- **tier:** capture → belief → ui
- **repo:** aximprint + axRange (o2 `tap` plan)
- **priority:** P2
- **evidence:** Physical view claims "the sensor mirrors this router, it is not inline on a link" because 97.7% of MAC pairs include a Meraki interface, and excludes an inline tap with "a tap cut into a single cable would show that cable's two MACs and nothing else." That argument is false for an L2 trunk: the MS120 does not rewrite source MACs, so a tap on the SFP25 link yields the identical signature. SPAN-on-MX85 vs inline-on-SFP25 are indistinguishable from packets.
- **build:** Add a `vantage` record to the capture metadata (`{sensor, attach: span|inline|erspan, device, port(s), direction: EW|NS}`) sourced from the directional `tap` plan (`WORK_ORDER_tap_directional.md`) when the capture is from a twin, or hand-declared for external pcaps. Downgrade the UI wording to "consistent with a SPAN on the router or an inline tap on its trunk"; when `vantage` is declared, state it and show which inferences it changes. Persist which of the 12 streams carried what (currently "not recorded").
- **boundaries:** No change to capture mechanics; metadata + wording only.
- **validation:** Oracle `tap_placement_claim`; stream→bytes attribution present in `/api/info`.
- **depends_on:** `tap_directional` for the vocabulary (already OPEN).

### G-07 — Fix TTL and ARP plumbing in the ingest

- **tier:** decoder → api
- **repo:** aximprint
- **priority:** P0 (defect, blocks G-03 and hop-distance)
- **evidence:** `conn.orig_ttl`/`conn.resp_ttl` are zero throughout; `/api/events?type=arp` returns nothing although an ARP parser exists.
- **build:** Populate TTLs from the first packet of each direction; retain ARP request/reply events (time-bounded IP↔MAC bindings) and expose them via `/api/events?type=arp`. Add TTL-delta hop-distance binning (DISCUSSION §2.1) so segments behind the 9K tier (one extra hop) separate from Meraki-attached ones.
- **boundaries:** Ingest/schema fix; no topology-logic changes beyond the hop-distance column.
- **validation:** Oracle `ttl_populated > 95%`, `arp_queryable = true`; hop-distance column distinguishes 10.80.34/24 (behind 9K) from 10.80.20/24 (Meraki-attached).
- **depends_on:** none.

### G-08 — Remove the 10k-row sampling cap on L2 evidence

- **tier:** api → ui
- **repo:** aximprint
- **priority:** P2
- **evidence:** "/api/search/rows caps each query at 10k rows, so the L2 evidence is the union of 32 queries, not a census."
- **build:** Streaming/paginated `/api/search/rows` or a dedicated `/api/l2/pairs` aggregate computed in the belief store (MAC-pair counts are a natural Count-Min / rollup, DISCUSSION §5). Physical view reads the aggregate.
- **boundaries:** Bounded memory — aggregate, don't dump packets.
- **validation:** Oracle `l2_evidence_census`; MAC-pair table identical between two runs (deterministic, not sample-dependent).
- **depends_on:** none.

### G-09 — Per-fact provenance on the fabric tier (asserted vs inferred)

- **tier:** belief → api → ui
- **repo:** aximprint (feeds axrange synthesizer per DISCUSSION §6)
- **priority:** P1
- **evidence:** Physical view mixes OUI-inferred "routers", MAC-fanout "gateways", and HSRP-asserted pairs without distinguishing confidence. DISCUSSION §2.7/§3 require per-field provenance for the twin's fidelity claim to be testable.
- **build:** Every switch/router/port/VLAN/prefix assertion carries `{source: lldp|cdp|stp|pvst|hsrp|dhcp|arp|mac-fanout|oui|mgmt-api, confidence, first_seen, last_seen}`; `/api/devices` and `/api/subnets/discover` return it; UI badges it. Synthesizer consumes it as `evidence-backed | defaulted`.
- **boundaries:** Schema + surfacing; no new decoders.
- **validation:** Oracle `provenance_on_fabric`; a generated `twin_config` shows provenance on `segments[]` and any fabric nodes.
- **depends_on:** G-01 (first asserted source), G-04.

### G-10 — Management-plane enrichment (Meraki Dashboard API, NX-API, Aruba REST)

- **tier:** enrich → belief
- **repo:** aximprint (new source adapter) — optional o2 service
- **priority:** P2 — only path to SGS-5240, AxeProdSW, labswitch, eng-8325h from this vantage
- **evidence:** Four switches run RSTP/MSTP (consumed by MS120) and sit below it; nothing in the SFP25 capture can name them. MS120 mgmt 10.80.16.15 never even appears as a segment.
- **build:** Read-only adapters: Meraki Dashboard API (switch ports, LLDP/CDP neighbours, VLAN table, clients, MX VLAN/DHCP config); NX-API (`show cdp/lldp neighbors`, `show vpc`, `show hsrp`, `show vlan`); Aruba AOS-CX REST (VSX, LLDP, VLANs). Each fact enters the belief store with `source: mgmt-api` (G-09) and is *reconciled* against pcap-derived facts — disagreements are surfaced, not silently overwritten.
- **boundaries:** Read-only, credentials via o2 secrets, never required for a run (pcap-only mode must keep working). Distinct from capture — it is a second evidence source with its own confidence.
- **validation:** Oracle `switches_named` → 9/9 with adapters enabled; every pcap-derived switch fact from G-01 matches the API fact (this doubles as G-01's ground-truth test).
- **depends_on:** G-09 (provenance), G-01 (something to reconcile against).

### G-11 — Second tap below the core + diagram-as-oracle round-trip test

- **tier:** capture + test
- **repo:** axRange (o2 tap plan) + aximprint (test harness)
- **priority:** P2
- **evidence:** DISCUSSION §3.5 — control-plane visibility is placement-dependent; a SPAN on the MS120 (or its downlinks P1/P14/P16/P19/P21) sees every access switch's LLDP/CDP/STP and per-port native VLANs, which the MX85 trunk never carries. §8 — the round-trip self-test needs a real target.
- **build:** (a) Add an `o2` sensor bound to an MS120 SPAN (directional tap plan, `direction: EW`, `location: core downlinks`) alongside `corp-apcn-rdr1`; tag streams with `vantage` (G-06). (b) Encode §1 of this file as `tests/oracle/corp_axellio_hq.yaml` and a scorer that runs discovery on a capture set and emits the §4 scorecard; wire it as the `learner` acceptance test.
- **boundaries:** Sensor placement is Lab (`o2`) work; do not change production switch config beyond a mirror session. Oracle file is hand-maintained ground truth, not derived.
- **validation:** With the second tap, `switches_named` and `switch_ports_named` reach 9/9 from pcap alone; scorer output is reproducible run-to-run.
- **depends_on:** `tap_directional` (OPEN), G-06, G-01.

## 6. Suggested bundling into work orders

| Work order | Items | Why bundled |
|---|---|---|
| `WORK_ORDER_fabric_neighbours.md` | G-01, G-09 | Same decoder→belief→UI path; provenance schema lands with the first asserted source |
| `WORK_ORDER_ingest_defects.md` | G-07, G-08 | Pure ingest/API fixes; no topology logic; unblock the rest |
| `WORK_ORDER_segment_truth.md` | G-02, G-03, G-04, G-05 | All rewrite the segment/host/gateway inventory from richer evidence; one validation pass against the oracle |
| `WORK_ORDER_vantage_and_oracle.md` | G-06, G-11 | Both are tap-plan + test-harness work in axRange/o2 |
| `WORK_ORDER_mgmt_plane_enrich.md` | G-10 | Separate source adapter; optional; last |

Sequence: `ingest_defects` → `fabric_neighbours` → `segment_truth` → `vantage_and_oracle` → `mgmt_plane_enrich`. The 10-minute PVST+ leakage probe in G-01 should run first of all, because it decides whether the pcap-only ceiling is 2 or ~6 switches and therefore how much of G-10/G-11 is optional.

## 7. Open questions for the user before generating orders

1. Where does `corp-apcn-rdr1` physically attach — MX85 SPAN, MS120 SPAN, or inline on SFP25? (Resolves G-06 for this capture immediately.)
2. Does the existing STP counter show more than one source MAC? (Decides G-01 ceiling.)
3. Is read-only Meraki Dashboard API access acceptable for G-10, or must the pipeline stay pcap-only?
4. Are 10.20.205/206 (≈255 VMware MACs each) a twin/lab range that should be excluded from the corp oracle?
