# Slice scope — router / firewall support (PROPOSED)

**Status:** **Phase 1 RATIFIED + IMPLEMENTED 2026-06-09** (user-authorized; SDN subnet+SNAT
egress — `OP-NET-050 create-subnet`, IR `network.egress`; unit-tested). **Live-verify on dev45:
egress works on SIMPLE zones (verified, ping 8.8.8.8 via SNAT) but NOT on VLAN zones** — PVE SDN
does not materialize a gateway/SNAT on VLAN zones (they reject `exitnodes` and create no gateway
IP / SNAT rule, confirmed on dev45). So VLAN-zoned scenarios (incl. all four Stage-2 archetypes)
need Phase 2 for egress. Capture-to-sensor verified unaffected by the new subnet.
**Phase 2 RATIFIED + IMPLEMENTED 2026-06-10** (user-authorized): `kind: router` nodes deploy —
multi-homed LAN + an injected WAN NIC (uplink DHCP) + `OP-HOST-090 configure-router` (forwarding
+ LAN→WAN masquerade in-guest), giving **real VLAN-zone egress NAT + inter-segment routing**.
Built on the **Debian** template (nftables). NET-080 relaxed to ≥1 net. All four archetypes use a
router. **Live-verified on dev45:** VLAN host → internet via router NAT + cross-segment + sensor
captured both.

**VyOS fold-in (2026-06-10) — ✅ RESOLVED via `vyos-ssh-set`; live-verified on dev45.** The
earlier cloud-init approach was a dead end (the VyOS *generic* ISO ships neither cloud-init nor
qemu-guest-agent — no remote-config channel) and the `hw-id` persistence made clones enumerate
`eth1/2/3` non-deterministically. **Both are now solved** (CHANGELOG 2026-06-10 "VyOS router
unblocked"), using the approach the sibling `axDigitalTwin` project uses — config over **SSH**,
since the generic ISO *does* run sshd:
- **Config delivery = `vyos-ssh-set`:** flat `set` commands applied through VyOS's vbash
  `script-template` over SSH. The adapter resolves the router's WAN/mgmt IP from its net0 MAC via
  `arp-scan` on the PVE host (no QGA), then SSHes from the orchestrator and pipes the `set` script
  to `vbash -s`; errors detected by stderr markers, not exit code (`commit` is non-zero on an
  idempotent no-op). `op_configure_router` branches on `router_os` (Debian→QGA, VyOS→SSH).
  **Debian NAT is reboot-persistent (2026-06-24 fix):** forwarding + masquerade are installed as a
  systemd oneshot (`axr-router-nat.service`, After=network-online) that re-detects the WAN and
  re-applies on every boot — a `debian-12-cloud` clone can reboot once on first boot, which
  previously wiped the runtime `ip_forward`/NAT and silently broke LAN egress. The helper prefers
  `nft` if present, else `iptables` (both on the base image); the fragile `apt-get install nftables`
  at configure time is gone. Live-verified on dev45 (router-nat-verify): LAN egress survives a gw
  reboot. CHANGELOG 2026-06-24.
- **Template (dev45 9351, replaces 9302):** `eth0`→`address dhcp` (WAN/mgmt bootstrap so the router
  is SSH-reachable right after boot) + `service ssh` + the operator pubkey baked into the `vyos`
  user; **`hw-id` stripped from `config.boot` as a raw edit with no commit** → deterministic
  `netN → ethN` (WAN=net0=eth0, LAN net i = eth(i+1)). Emission attaches the VyOS WAN on net0,
  emits no cloud-init op, and passes ordered `lan_ifaces` to `configure-router`.
- **Live-verified** (`vyos-router-test`, 2-segment VLAN zone): `--fresh` deploy COMPLETED 36/36;
  router NAT egress (host→8.8.8.8) + cross-segment routing both pass; tapped-host frames captured
  at the sensor. The Debian/nftables router remains a supported peer. **Prereq:** `arp-scan` on the
  PVE host. **WIP branch only**; not yet on `master`.

Follow-on: per-rule firewall policy (IR extension); a cloud-init-capable VyOS image.
**Date:** 2026-06-09.
**Driver:** all four Stage-2 archetypes (`soc-detection-lab`, `ad-domain-lab`, `ctf-range`,
`ot-ics-range`) validate but can't fully deploy because the planner is host-only
(`kind: router` → plan error) and isolated SDN vnets have no internet egress for
`provision-app` package installs.

---

## 1. What already exists (this is mostly wiring, not greenfield)

- **IR models routers fully** (`docs/IR_SCHEMA.md` §3.2): `kind: router`, multi-homed `nets:
  [{net, ip}]`, and `static_routes: [{to, via}]`. The **validator already accepts router
  nodes** (Stage 2 confirmed: `validate` PASS, `plan` FAIL).
- **The op catalog already anticipates VyOS**: `configure-static-routes` (`docs/OP_CATALOG.md`
  L275) — a `config`-class op "Ansible into the guest, e.g. VyOS," emitted only for
  `kind: router` nodes with `static_routes`. Listed in `docs/ADAPTER.md` §3 as the 8th OP-HOST
  op, explicitly **excluded from the slice** ("no router in the slice").
- **VyOS is already a catalog binding**: `vyos@1.5` (`tests/fixtures/images/bindings/proxmox/
  vyos.yaml`), template_vmid 9301, `cloud_init: supported (user: vyos)`, `agent: false`,
  `pkg: false / script: true / config: true`.
- **The gap is one place**: `axrange/planner/pass1_emit.py` L270–275 rejects any node with
  `kind != host`. Routers are gated out of emission; everything downstream (clone/attach/
  start host-ops) is generic and would work for a multi-homed node.

So this slice = **(a)** remove the host-only gate for routers + emit the router's host-ops
across its multiple vnets, **(b)** deliver routing/NAT/firewall config, and **(c)** give
isolated vnets egress. It is not a from-scratch design.

## 2. Two distinct needs (don't conflate them)

| Need | Who needs it | Cheapest correct fix |
|---|---|---|
| **(A) Internet / apt egress** from isolated scenario vnets | **all 4 archetypes** (`provision-app` installs packages) | **SDN subnet + SNAT** — SDN-native, no router VM |
| **(B) Inter-segment routing + firewall policy** (DMZ↔corp, IT↔OT conduit, default-deny + allowlist) | multi-segment archetypes (SOC, OT) | a **router/firewall node** (VyOS) |

(A) is the higher-leverage, lower-risk win and unblocks every archetype. (B) is the explicit
security control the corpus calls for (firewall configuration, VLANs, access lists, the OT
conduit) and is the true "router node" feature.

## 3. Does VyOS suit the needs?

**Yes — VyOS is the right router/firewall image — with one delivery caveat.**

**Fit (pros):**
- Purpose-built router/firewall: static + dynamic routing, stateful firewall, zone-based
  policy, source-NAT/masquerade — exactly DMZ/OT-conduit segmentation.
- **Already wired into the design**: IR `kind: router` + `static_routes`, the catalog's
  `configure-static-routes` op ("e.g. VyOS"), and the `vyos@1.5` binding all exist.
- Maps cleanly to the IR's platform-neutral `router` abstraction (`docs/ARCHITECTURE.md` §62) —
  no adapter leakage.

**Caveat (the real fork): `agent: false` on the VyOS binding.**
- The config slice (`config_exec/`) delivers config over the **QGA** channel. VyOS has no
  guest agent, so `configure-static-routes`-as-"Ansible-into-the-guest" **cannot use that
  path**. VyOS config (interfaces, routing, SNAT, firewall) must instead ride **cloud-init**
  — VyOS 1.3+ supports `vyos_config_commands` in user-data, and the binding already declares
  `cloud_init: supported`. So the router slice introduces a **second config-delivery
  mechanism** (cloud-init `set`/commit) distinct from the QGA path.
- **Alternative to remove the caveat:** bake `qemu-guest-agent` into the VyOS template (VyOS
  can run it) so router config reuses the QGA/run-script path like every other node. That's a
  template/imagebuild change but unifies delivery. **Recommended to evaluate at build time.**

**Alternatives considered:**
- **Linux-host-as-router** (debian + `ip forward` + nftables): reuses the QGA path (debian has
  the agent), no new image; but you hand-roll routing/NAT/firewall in scripts — less clean and
  less auditable than VyOS's purpose-built config. Good fallback if VyOS cloud-init proves
  fiddly.
- **PVE SDN subnet + SNAT**: solves **(A) egress only**, SDN-native, no VM. Not a firewall.
- **pfSense**: not in catalog, BSD, heavier — no.

**Verdict:** VyOS for **(B)**; SDN subnet+SNAT for **(A)**. Adopt VyOS, but decide the config
delivery (cloud-init vs. QGA-baked-template) up front — that's the main implementation risk.

## 4. Proposed scope (phased)

**Phase 1 — egress (SDN subnet + SNAT), unblocks all archetypes.**
- Add/extend an OP-NET op to define a **PVE SDN subnet** on the egress vnet with `gateway` +
  `snat 1` (PVE SDN supports this). New op `create-subnet` (OP-NET-0xx) or extend
  `create-vnet` to emit the subnet from `infra.networks[].gateway`.
- No router VM, no IR change beyond using the existing `gateway:` field.
- Live-verify on dev45: a `provision-app` host on an isolated vnet reaches apt.

**Phase 2 — router/firewall node (VyOS).**
- `pass1_emit`: stop gating `kind: router`; emit `clone-vm` / `configure-vm-hardware` /
  `configure-cloud-init` / `attach-vm-to-vnet` (**once per net** — multi-homed) / `start-vm` /
  `wait-*` for router nodes, ordered so the router comes up before dependent host networking.
- Render VyOS first-boot config (interfaces per vnet, inter-vnet routing, `static_routes`,
  source-NAT for egress, default-deny + allowlist firewall) via **cloud-init
  `vyos_config_commands`** (or QGA if the template is re-baked with the agent).
- Implement/emit `configure-static-routes` (OP-HOST 8th op) for routers with `static_routes`.
- Capture: routers are typically **not** `tap`-ed; no capture-plane change. (A tapped router
  works via the per-tap `tc` mirror like any node if desired.)

**Out of scope (follow-on):**
- **Rich firewall policy as IR.** The IR has `static_routes` but **no firewall-rule fields**.
  Phase 2 ships sane defaults (zone default-deny + egress allow). Expressing per-rule policy
  (the OT conduit allowlist, DMZ rules) needs an IR extension (a `firewall:`/`policy:` block on
  router nodes, or via the Defense overlay `OP-OVL`, now implemented — `docs/SLICE_OVERLAY.md`).
  Still deferred — no `firewall:`/`policy:` IR field exists yet.
- ~~Dynamic routing protocols (OSPF/BGP)~~ → **shipped** as the separate **fabric slice**
  (`docs/design/04-networking-capture.md`, 2026-06-29): OSPF / IS-IS / iBGP / eBGP / BGP-EVPN / VXLAN /
  redistribution / BFD on `kind: router` (VyOS) nodes, via `OP-HOST-100 configure-routing` +
  `OP-BAR-060 fabric-ready` + the top-level `fabric:` block + per-router `routing:`. Implemented
  and live-verified on hardware — control-plane (IS-IS/iBGP-EVPN), L2VNI+L3VNI, anycast IRB
  (incl. cross-leaf), eBGP + redistribution, and capture all PASS; tenant-VRF internet egress is
  a documented follow-on (`docs/FABRIC_VERIFICATION.md` §9, Finding C). HA/VRRP remain unbuilt.

## 5. Changes required (concrete)

- `axrange/planner/pass1_emit.py` — remove the `kind == host` gate for routers; add router
  op emission (multi-homed attach; ordering).
- `axrange/ops/registry.py` + `docs/OP_CATALOG.md` — add `create-subnet` (Phase 1) and wire
  `configure-static-routes` (already cataloged) into emission; CHANGELOG the additions.
- `axrange/adapter_proxmox/rest.py` — SDN subnet create (gateway+SNAT) via `/cluster/sdn`;
  VyOS cloud-init render (a new `_render_vyos_userdata` analogous to `_render_userdata`).
- `docs/IR_SCHEMA.md` / `docs/PLANNER.md` — note router emission is live; firewall-policy IR
  fields flagged as follow-on.
- **VyOS template** must exist on the target cluster (only the Debian template is built on
  dev45 today). VyOS isn't a standard cloud image — obtaining/templatizing it is a prerequisite
  task (not the standard imagebuild cloud-image path); consider whether to re-bake it with QGA.

## 6. Done-criteria (proposal)

1. `soc-detection-lab` (3 segments) **plans with a VyOS `fw` router** restored (no host-only
   error) and **deploys on dev45**: router up, multi-homed across DMZ/corp/SOC.
2. A `provision-app` host on an isolated vnet **installs a package** (apt egress works) — via
   Phase-1 SNAT and/or the router's masquerade.
3. Inter-segment reachability matches policy (attacker in DMZ can/can't reach corp per the
   firewall default), verified by ping/curl through the router.
4. Existing host-only scenarios + the 4 archetypes still validate+plan unchanged (no
   regression); test suite green.

## 7. Risks / open questions

- **VyOS config delivery** (cloud-init `vyos_config_commands` vs. QGA-baked template) — the
  main risk; pick at ratification. Cloud-init for VyOS is finicky (full `set`+`commit` set must
  render correctly).
- **VyOS template provisioning** on real clusters (not a cloud image; the imagebuild pipeline
  doesn't cover it yet).
- **PVE SDN SNAT** behavior across Simple vs. VLAN zones — live-verify which subnet/SNAT form
  PVE applies per zone type.
- **Egress source** for apt — a SNAT subnet needs an uplink (the host's `vmbr0`/internet); on
  dev45 that's available, but document the requirement.

## 8. Effort (rough)

- **Phase 1 (SDN subnet+SNAT):** small — one op + REST SDN-subnet call + live-verify. Highest
  leverage; do first.
- **Phase 2 (VyOS router):** medium — router emission + VyOS cloud-init render + the template
  prerequisite + live-verify. The IR/catalog scaffolding already exists, which de-risks it.

## 9. Egress phase policy (E1, 2026-09-21)

> **Added by `docs/WORK_ORDER_protocol_engines.md` §E1.** Implemented; cluster-free
> (unit-verified only — the live-verify notes are at the end of this section).

The router gained an **egress phase**, because the protocol-engine round makes
containment a property of the *network* rather than a render-time argv check on each
client (ruling 2026-09-21, reversing `WORK_ORDER_protocol_faithful_traffic.md` §2.2):

| phase | forwarding to the WAN | everything external | why |
|---|---|---|---|
| `provision` | masquerade, exactly as §4 Phase 2 | real internet | Chromium, `smbclient`, `krb5-user` and the server packages are apt pulls |
| `measure` | **DROP**, counted | policy-routed to the twin's **sink** | a measured window must contain no real egress at all |

**Where it lives — `OP-HOST-090 configure-router` gained one OPTIONAL input.** Not a
new op (a new op type costs an `OP_CATALOG.md` version bump; an input does not):
`egress: {phase, sink_ip, mode, cidrs}`, plus an `egress_phase` output carrying the
phase the router *actually reached*. **Omitting `egress` entirely keeps today's
permissive behavior**, so every shipped twin config is unaffected; omitting only its
`phase` inside a declared policy means `measure` — **default CLOSED**.

**How a twin ASKS for it — the authoring path (GAP-101, 2026-09-23).** E1 shipped the
mechanism with nowhere to declare it, and that is how a real containment breach went
unnoticed during the 2026-09-21 live run (twin hosts reached the actual internet;
`github.com` presented a Sectigo certificate instead of the lab CA). A twin now
declares its posture **once, at twin level**:

```yaml
twin_config:
  egress:
    phase: measure        # provision | measure; omitted inside a declared block => measure
    # sink_ip: 10.66.30.10   # optional override; resolved from the `role: sink` node otherwise
    # mode: default | cidrs  # + cidrs: [...] for the per-prefix mode
```

which expands to the IR's top-level **`egress:`** block (`axrange.ir.schema.Egress` —
placed and shaped exactly like `fabric:`), and `pass1_emit` attaches it to **every**
`kind: router` node's `configure-router` input via
`axrange.planner.egress.resolve_egress_policy`. Twin-level, not per-node: a twin with
two routers must not be able to declare two different containment postures, since one
router left in `provision` re-opens the real internet for everything behind it.

* **`sink_ip` is resolved, not re-typed** — from the twin's unique `role: sink` node's
  pinned leg address. An explicit `sink_ip:` overrides it; `sink: <node-id>` picks a
  different node. Every ambiguity (no sink, two sinks, a multi-homed sink, a sink with
  no pinned address) is a **plan-time** error naming the fix, never a guess: a
  `measure` phase pointed at nothing black-holes the twin while reporting itself
  contained.
* **Unspecified means UNCHANGED, not closed** — a twin that declares no `egress:` block
  emits no `egress` input at all and keeps today's permissive masquerade. All eight
  shipped twin configs plan byte-identically (`plan_id`/`ir_hash` unmoved); only
  `svc-engines`, which now declares `phase: provision`, moved on purpose.
* **Plan-time refusals:** a VyOS router in a declaring twin (the adapter's own
  `egress-phase-unsupported-os` refusal, hoisted to `te plan`), and a declaring twin
  with no router at all (a posture that would reach nothing).
* **The posture is visible** — `te info` / `te inventory` (and `GET /api/deploys/{id}/
  access` + `.../inventory`) print the phase read back from `configure-router`'s
  `egress_phase` output, so "is this twin contained?" is answerable without an ssh to
  the router. `contained` is derived from the phase the router **applied**, never from
  the declaration; an unapplied declaration renders `NOT APPLIED`.

**Renderer:** `axrange/adapter_proxmox/egress_render.py` (pure text, the
`fabric_render.py` shape). The adapter writes its installer over QGA; the identical
text is also reachable as a `run-script` payload via
`scripts/gen_egress_policy.py --sink <ip> --out twin-configs/<twin>/scripts`, which
writes a self-contained `install-egress-policy.sh` for a single
`install: [{script: ...}]` action (one `script:` per app is the emitter's limit) —
kept for a STANDING twin deployed before the declaration existed, whose
`configure-router` op row is already `completed` and will not re-run (LES-096).

**Sink next-hop routing — the part the sink cannot do itself.** The sink accepts
traffic addressed to *any* IP (an `iif`-scoped `local 0.0.0.0/0` route in a table of
its own, `twin-configs/_shared/scripts/serve-sink.sh`) but it does **not** answer ARP
for foreign addresses. So the router carries the route. In `measure`, per LAN
interface:

```
ip rule  999  from <sink>                                  lookup main   # no loop
ip rule 1000  iif <lan>  lookup main suppress_prefixlength 0             # internal wins
ip rule 1001  iif <lan>  lookup 210                                      # the rest -> sink
ip route (table 210)  default via <sink> dev <lan-of-sink>
```

`suppress_prefixlength 0` is load-bearing: without it, table 210's default route also
swallows inter-segment LAN traffic and the twin routes east-west through the sink.

**Default route, not per-prefix (decision).** `mode: default` is the default: one
route covers every observed external address *and* every address no cast observed —
and a real browser chases names the corpus never saw. `mode: cidrs` installs one
route per prefix from `scripts/gen_sink_corpus.py`'s `external-cidrs.txt` (~1,270
aggregated /24s); precise, and deliberately a black hole for anything unobserved,
which is the right shape only when "did anything reach outside the observed set?" is
the question being asked.

**Fail-safe.** `measure` writes its state *before* applying; `provision` only *after*
a successful apply, and a failed `provision` transition forces `measure` back on and
exits non-zero. The in-guest engine falls back to `measure` on a missing or
unparsable state file, caches the WAN interface name (so a lapsed DHCP lease cannot
leave the twin open at boot), and is re-applied every boot by
`axr-egress-phase.service`. Residual, stated honestly: nothing expires a `provision`
phase — hence `--show`'s `phase` + `phase-since`, which is what a capture window gets
labelled with (§6 of the work order: a `provision`-phase window is uncontained and
must never be scored).

**Verifying in-guest.** `/usr/local/sbin/axr-egress-phase.sh --healthcheck` exits 0
only when the live state matches the persisted phase. In `measure` it asserts the WAN
drop rules, the policy rules, a non-empty table 210, that — on **every** LAN leg —
`ip route get 203.0.113.9 iif <leg> from <host>` answers `via <sink>` (TEST-NET-3 —
an address no cast can have observed, so this *is* the unobserved-address guarantee,
proved from the FIB without sending a packet), and that the router itself cannot
reach that address. `<host>` is the lowest host address on the leg's prefix that is
neither the router's own nor the sink (`axr_probe_src`): the router's own address is a
martian source on input, so the kernel answers EINVAL whatever the policy is (the probe
used it until corp-twin devlab live run 2, 2026-09-28, and so never passed). A `/32` leg
has nothing to probe from and is reported `skip`; zero probeable legs is a FAIL. In `provision` it asserts the masquerade is present and that
neither the drop nor the policy rules are.

**VyOS: not implemented, fails loud.** A VyOS router carrying an `egress` policy
raises `egress-phase-unsupported-os` rather than applying the NAT half and silently
skipping containment (LES-001) — VyOS 1.3 and 1.4 disagree about firewall syntax
(`firewall name` vs `firewall ipv4 forward filter`), and a rule set that `commit`s
clean while containing nothing is the worst failure this slice can have. Debian/
nftables (with an `iptables` fallback, as `axr-router-nat.sh` already has) is the
shipped shape.

**What the live-verify must prove** (unit tests cannot): from a twin guest in
`measure`, `curl https://<an-unobserved-address>/` reaches the **sink** (the sink's
any-IP route and the router's next-hop route actually meet on the wire, ARP and all);
`apt-get update` fails loud; the router's WAN drop counters are non-zero while no
packet is forwarded out the WAN; and `provision` then restores a working
`apt-get update` from that same guest.
