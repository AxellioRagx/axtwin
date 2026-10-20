# OptraHealth — multi-site healthcare datacenter twin

A long-running **digital twin** of a fictional Optum-like healthcare company's datacenter
estate, spread across **three sites** on a **real network fabric** (the axRange fabric slice —
`docs/design/04-networking-capture.md`). Authored per `docs/SCENARIO_AUTHORING.md` (profile: twin,
focus: traffic-capture).

This is a **twin**, not a range: deploy it and leave it up. The capture plane records both the
**network control-plane** (real routing/overlay convergence) and the **clinical application
protocols** — the point is faithful, always-available traffic to capture and analyze. An
attack range can later be layered on as an `includes:` fragment (see `SCENARIO_AUTHORING.md` §3.1).

## Topology

```
                         ┌──────────── WAN backbone (eBGP full-mesh) ────────────┐
                         │              wan-core 10.255.255.0/24                  │
              border-e (AS 65010) ───────── border-w (AS 65020) ───────── abr-c (AS 65030)
                    │                              │                            │
   ┌────────────────┴───────────┐   ┌─────────────┴──────────┐    ┌────────────┴───────────┐
   │ DC-EAST (primary)          │   │ DC-WEST (DR)            │    │ CORP-HQ (campus)        │
   │ IS-IS + iBGP-EVPN/VXLAN    │   │ IS-IS + iBGP-EVPN/VXLAN │    │ OSPFv2 multi-area       │
   │ spine-e1/e2 (RR)           │   │ spine-w1 (RR)           │    │ abr-c (area 0) +        │
   │ leaf-e1/e2 (VTEP, BFD)     │   │ leaf-w1 (VTEP)          │    │ area-c1 (area 1)        │
   │ e-clin/e-dmz/e-data/e-infra│   │ w-data/w-infra          │    │ c-user                  │
   └────────────────────────────┘   └─────────────────────────┘    └─────────────────────────┘
```

- **DC-East (primary, AS 65010):** IS-IS L2 underlay (area 49.0001) + iBGP-EVPN (spines = route
  reflectors) + VXLAN VTEP leaves (BFD on the leaves). Tiers as EVPN L2VNI segments: clinical
  (EHR / HL7 integration / PACS-DICOM / clinical DB), member/provider DMZ (portal / FHIR), data
  (claims+pharmacy DB / EDW), infra (AD DC / jump).
- **DC-West (DR, AS 65020):** smaller IS-IS spine-leaf; DB replication standby + DR DC + backup.
- **Corp-HQ (campus, AS 65030):** OSPFv2 multi-area (area 0 backbone + area 1); clinician
  workstation (domain-joined) + helpdesk.
- **WAN backbone:** the three site borders peer **eBGP** full-mesh; each site redistributes its
  IGP into BGP and advertises its supernet. abr-c does OSPF↔BGP redistribution; the DC borders do
  IS-IS↔BGP.

## Protocol coverage (what the capture plane sees)

- **Control-plane:** IS-IS, OSPFv2 multi-area, iBGP + route-reflectors, eBGP, BGP-EVPN
  (`l2vpn-evpn` + `advertise-all-vni`), VXLAN, BFD, mutual IGP↔BGP redistribution.
- **Application-plane:** HTTPS (portal/FHIR), HL7 MLLP, DICOM (Orthanc), SQL + replication
  (PostgreSQL), LDAP/Kerberos/SMB/DNS (AD: `dc1` promotes `corp.optra.lab`; `ws1` joins it).
- **Crown-jewel data:** synthetic PHI on the EDW + claims DB (`overlays.data`, deterministic from
  `scenario.seed`).

## Run

```bash
# validate (no cluster)
axrange validate scenarios/optrahealth-twin/scenario.yaml --images tests/fixtures

# plan the DAG
axrange plan scenarios/optrahealth-twin/scenario.yaml --images tests/fixtures \
    --node <pve-node> --sensor-ip <transport-ip>
```

Both pass today (validate clean; plan = a ~322-op DAG: `configure-routing` for all 10 routers,
`fabric-ready`, AD `wait-domain-ready` + `join-host-to-domain`, `deploy-sensor[pcap]`).

## Status & known limitations (honest)

**Implemented + validate/plan-verified.** Live deploy is **not** yet possible end-to-end because
the fabric slice (v1) is control-plane-complete but data-plane-partial:

- **Symmetric IRB / L3VNI + egress — implemented (2026-06-30), live-verify pending.**
  `fabric.tenant: optra` + the anycast gateways on the EVPN segments give **inter-segment L3 routing**
  across the fabric (tiers reach each other), and **egress** (tenant VRF → internet, for `apt`) works
  via a per-leaf default leaked to the global table (the WAN gateway is discovered by
  `configure-router`). The VyOS IRB+egress config is authored from the FRR symmetric-IRB recipe and
  needs a cluster to confirm.
- **Cross-site EVPN stretch** (one L2 across sites) and **replica-DC AD replication** (`dc2` is a
  DR-DC *substrate* here) likewise need follow-ons.
- **VyOS-only** fabric; **Windows app provisioning** beyond DC promotion + domain join is deferred
  (Windows hosts deploy as substrate).
- **Continuous ambient traffic generators** (systemd timers per host) are a follow-on; the IR
  carries a one-shot `baseline-traffic` activity to exercise the capture plane at deploy.

These are the next fabric increments — this scenario captures the full intended architecture and is
the driving use-case for them.
