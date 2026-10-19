# Work Order — Protocol engines: real clients, a universal sink, containment as a network property

> **STATUS (2026-09-21): E0–E6 and E8 BUILT + gated `dev45` live-verified (97/97 ops COMPLETED,
> twin `svc-engines-24131435`). E7 DEFERRED (tools + the lab CA installed at provisioning instead —
> see E7's status line). E9 PARTIAL PASS — three of four proof points hold, the fourth (the sink
> answering an external destination) is open as GAP-100. Full build record, the live-verify numbers
> and the findings list: CHANGELOG 2026-09-21 "Protocol engines: real clients, a containment
> reversal, the any-IP sink, and a live-verify with two open findings". Two gaps opened
> (GAP-100, GAP-101); GAP-099 was opened mid-round; GAP-067 gained a recurrence note.
>
> **Authority:** **user** (2026-09-21) for §2's four rulings — the containment reversal, the
> purpose-built target twin, Windows engines in scope, builder's-call ordering.
> **implementation (Claude Code)** for this draft's slicing, contracts and acceptance criteria, and
> for the build.
>
> **Extends:** `WORK_ORDER_protocol_faithful_services.md` (the **SERVER** half, S2/S3/S4 — not
> restated) · `WORK_ORDER_protocol_faithful_traffic.md` (`drive: command` — §2.2's containment rule
> is **superseded** by ruling 1 below, CHANGELOG 2026-09-21) ·
> `design/07-traffic-personas.md` · `SLICE_ROUTER.md` · `WORK_ORDER_traffic_tier.md`.

## 1. Goal

Twin nodes must emit **real protocol traffic produced by real applications** — not `replay`, not
`scripted` shape-only filler. A detection rule matches protocol *semantics* (a query name, an RC4
TGS-REP, a TLS SNI), which filler bytes at a port can never carry.

Measured target (corp-apcn cast): **DNS 70% of flows** · **TLS 11% of flows and 6.3 of 8.9 GB**
(needs real SNI/JA3) · **SMB 2.2 GB internal** · **Kerberos/LDAP/RPC/RDP <1% of flows** but carrying
the **detection value** · **~6% unlabelled internal**, almost certainly Windows RPC on dynamic ports
(49667/49669/49677 are in the dial census — they resolve once a real Windows member exists).

## 2. Rulings already made (Authority: **user**, 2026-09-21)

1. **Containment changes.** `command`'s ratified *"IP literals only, never guest-side name
   resolution"* rule is **REVERSED**. New model: guests **resolve names normally**, but only the
   **twin's own DNS** answers; a transparent **any-IP proxy/sink** (the user's own proposal)
   impersonates every external server; the **router denies real egress** while measuring —
   containment becomes a **network** property, not a render-time argv check. Needs a
   `**Supersedes:**` line when built; it reverses `WORK_ORDER_protocol_faithful_traffic.md` §2.2.
2. **Target twin = a small purpose-built twin** (`svc-verify`-style), **not** the cast — the standing
   138-VM cast twin is being torn down to free resources.
3. **Windows engines are IN SCOPE** — a real domain member (join + logon + Group Policy) driving
   Kerberos/LDAP/SMB/RPC/DNS/NTP incl. dynamic-port RPC.
4. **Engine order is the builder's call**; engines land one after another.

## 3. Structural findings

**3.1 Mixed engines per host DO need a change — slice E0.** A host cannot carry two persona
assignments: `o2control/traffic.py:969,1007-1014` keeps a `claimed_hosts` map and refuses the second
— *"a host may carry exactly one persona instance"* (same guard on `replay[]`, `:1045`). Instance
**identity** is fine: `instance_id = f"{host_id}-{persona.id}"` (`:1016`) is unique per
`(host, persona)` and unit names, in-guest paths and op scope `{"instance": …}` key off it — so the
blocker is one deliberate refusal, not the identity model. **E0 blocks E4/E5/E6.**

**3.2 `knobs` is free-form** — `Persona.knobs: dict[str, Any]` (`schema.py:179`) is opaque, so
per-engine content (browse list, qname, share path, base DN, SPN) rides there as `knobs.dns_qname`
does (`command.py:485`). **No schema change for content.**

**3.3 `drive:` is a closed enum in two places** — `DRIVE_KINDS` (`schema.py:41`) and `PSN-030`
(`VALIDATOR.md:399`).

## 4. Version-locked contracts

- **`VALIDATOR.md` — YES, confirm first.** `PSN-030`'s enum gains `browser` (E4), plus
  `windows-domain` if E6 is its own kind. Rule *count* unchanged (114); only its *text* changes.
  Version-locked ⇒ CLAUDE.md's confirm trigger applies.
- **`OP_CATALOG.md` — No if E1 extends `OP-HOST-090 configure-router`; YES if E1 mints a new op.**
  Prefer reuse.
- **No:** `IR_SCHEMA.md` (the persona catalog is not IR; E8 uses existing
  `nodes`/`install`/`provides`/`joins`) · `PLANNER.md` (E0 lifts an `o2control` runtime refusal, not
  planner machinery) · `ADAPTER.md` (existing QGA + SSH only) · `IMAGE_MANIFEST.md` (additive).
  Non-locked edits: `design/07-traffic-personas.md`, `personas/schema.py`,
  `personas/drivers/__init__.py:_DRIVERS`, `PRODUCT_GAPS.md`.

## 5. Slices

**Sequence:** **E0** → **E1 + E2 + E3** (the containment triangle — nothing is measurable without
all three) → **E7** (images gate the clients) → **E8** → **E4 / E5 / E6** in any order (ruling 4) →
**E9** per engine.

### E0 — Multiple persona instances per host — **small**

**STATUS: BUILT + live-verified** (`svc-engines` E0 proof: `linux-client` carries `browser` + `command` + `scripted` as three distinct units).

- Lift the `claimed_hosts` refusal on the `assignments[]` path, but **keep** one for the same
  `(host, persona)` twice — it collides on `instance_id` and the unit name. "One persona per host"
  becomes "one **instance** of a persona per host".
- `replay[]` keeps its exclusivity (a whole pcap should not share a host) — record the call.
- *Acceptance:* one host carries `browser` + `command` + `scripted` as three distinct units;
  heartbeat reports all three; stopping one leaves the others running.

### E1 — Egress phase policy — **small/medium**

**STATUS: BUILT + live-verified on dev45.** Router counters confirmed zero WAN-forwarded packets in `measure` phase. See the ordering finding below (install before adopting the resolver) — measured live, not anticipated.

Provisioning needs **real** egress (Chromium, `smbclient`, `krb5-user`, `freerdp` are large apt
pulls); measurement must have **none**.

- **Where:** the router's own ruleset — `OP-HOST-090 configure-router`'s nftables (Debian) /
  `vyos-ssh-set` (VyOS). **Extend `configure-router` with a phase input, don't mint an op** — a new
  op costs an `OP_CATALOG.md` bump, an input does not.
- **Phases:** `provision` (masquerade to WAN, as today) → `measure` (WAN forward **DROP**; the sink
  is the only route off-twin). Entered/left by a `config_exec` step at the router.
- **Fail-safe: default CLOSED** — a never-phased twin is in `measure`, a failed transition stays in
  `measure`, never fail open. Record the phase a capture window ran in (§6): a `provision`-phase
  measurement is uncontained and must not be scored.
- *Acceptance:* in `measure`, a guest `curl https://1.1.1.1/` reaches the **sink**, router counters
  show zero WAN-forwarded packets, and a guest `apt` fails loud.

**Ordering rule — install everything BEFORE the twin's name service is adopted** (measured on dev45
2026-09-21, twin `svc-engines`). The egress phase is not the only thing that closes a guest off
from the internet: **E3's resolver does too, and it does it one hop earlier.** Once
`/etc/resolv.conf` points at the twin resolver — authoritative for the observed names, with no
upstream to forward to — `apt-get install` fails with `Could not resolve 'deb.debian.org'` even
while the router is still masquerading in the `provision` phase. That is *correct* measure-phase
behaviour arriving in the middle of provisioning, and the last acceptance bullet above ("a guest
`apt` fails loud") is then satisfied by the wrong mechanism.

So, for every recipe on a twin with its own name service:

> **Install everything first. Repoint DNS last.** A `run-script` that apt-installs *after* writing
> `/etc/resolv.conf` cannot work, and its failure will read as a broken mirror rather than as an
> ordering bug.

`twin-configs/svc-engines/scripts/prepare-engine-client.sh` is the worked example (step 0 apt →
step 2 census → **step 3 repoint**, in that order, with the rule stated at the repoint), and
`tests/test_svc_engines_recipes.py` pins the ordering so it cannot silently invert. To install
something on a deployed guest afterwards, repoint at the lab resolver, install, repoint back — and
treat any capture window that overlapped the detour as uncontained.

### E2 — The sink / any-IP responder — **large**, the keystone

**STATUS: BUILT, PARTIAL live-verify — GAP-100 open.** DNS/NTP/SSH responders proven; TLS/HTTP dispatch reaches Caddy for an internal SNI but **the sink never sends a SYN-ACK for an external destination** even though the SYN arrives at its `eth0` and every checked layer (routing, nftables, rp_filter, the listening socket) looks correct — undiagnosed beyond that (GAP-100). The lab-CA per-SNI issuance did work (proven against `1-courier.push.apple.com`) for the internal path.

- **Any-IP:** one VM. `ip route add local 0.0.0.0/0 dev lo` + nftables/TPROXY to per-port listeners; the
  router's off-twin default route points here. **Detection:** port first, first-bytes sniff as
  tiebreak only (TLS `0x16 0x03`, HTTP verb, SSH banner), plus a fail-loud "unknown protocol on
  port N" counter E9 reads.
- **Responders:** **DNS** authority from the cast's `observations.dns` (**2,638 names → their
  observed IPs**) · **TLS + HTTP/1.1 + HTTP/2 + HTTP/3 (QUIC)** via **Caddy** with a **lab CA**,
  issuing on demand for whatever SNI arrives (closing GAP-061) · **NTP** · **SSH** (banner + real
  KEX) · **SMTP-ish fallback** · generic-TCP last resort.
- **Honest limit — record in `PRODUCT_GAPS.md`:** no recorded HTTP bodies, no SNI evidence
  (**GAP-076**: Suricata emits no `tls` records; **aximprint GAP-019**: the TLS/HTTP/SMB carrier is
  not built) ⇒ **real names + real certs + generic bodies.** **Pinned endpoints** (Apple, Microsoft)
  abort after the handshake against a lab CA — label those flows **handshake-only**, never success.
- *Acceptance:* from a guest, `curl -sv https://<observed-name>/` completes a real TLS 1.3 handshake
  with that SNI on the cert; `dig` → NOERROR + ANSWER; `ntpdate -q` → an offset.

### E3 — Twin name service — **small/medium**

**STATUS: BUILT + live-verified on dev45.** Name chain proven end-to-end (internal + the sink's own-address answer for an external observed name). The reverse-DNS/SASL-SPN finding below was hit and fixed live.

- The twin resolver (bind9, from services S2) is authoritative for internal names and **forwards
  everything else to the sink's DNS**, which answers with sink addresses.
- **Windows composition, exactly:** a member's DNS **must** be the DC, so the chain is *member → DC
  (AD-integrated DNS) → forwarder = twin resolver → sink*. Set the **DC's forwarder**; never point a
  joined member at bind9 — joins and SRV lookups break.
- **Adopting the resolver is a one-way door for package installation** — see E1's ordering rule
  above. The twin resolver has no upstream, so the moment a guest resolves through it, `apt` is
  over for that guest. Every recipe installs first and repoints last; anything needed later
  requires a deliberate, recorded detour back to the lab resolver.
- **A twin serves no PTR records, and tools canonicalize hosts through reverse DNS.** Nothing in
  this chain answers `in-addr.arpa`, which is fine until a client silently depends on it: OpenLDAP
  reverse-resolves the SASL host name before building the SPN it asks the KDC for, so
  `ldapsearch -Y GSSAPI` asked for a principal that does not exist and failed with
  *`Server not found in Kerberos database`* — a Kerberos-shaped error with a DNS cause, from a
  ccache in which `kvno ldap/dc.corp.axr.lab` succeeded (dev45, 2026-09-21). The fix is per-client
  (`ldapsearch -N`; MIT krb5's own `rdns = false` is the same idea in `krb5.conf`), not per-zone —
  serving synthetic PTRs would be inventing observations. Expect this class again for any client
  that derives an identity from a name.
- *Acceptance:* a Linux guest and a Windows member each resolve an internal name, and an external
  observed name to a sink address.

### E4 — Browser engine (`drive: browser`) — **medium/large**

**STATUS: BUILT + live-verified on dev45**, with one fix mid-round: the success counter judged process exit (Chromium exits 0 on an error page), now judged from the DOM; Chromium's own background networking is disabled (it was polluting the twin's DNS census).

Headless Chromium on Linux hosts visiting observed names on a seeded schedule. One engine covers
**DNS + real TLS (real SNI, real JA3/JA4) + HTTP/1.1 + HTTP/2 + QUIC** — 81% of measured flows, 71%
of bytes.

- Mechanics match the existing drivers: detached **transient systemd unit**
  (`systemd_daemon_start_block`; **never `nohup … &`**, LES-086); **fail loud** on a missing binary
  (`CommandDriver._start_linux`'s probe pattern); **deterministic render** (same persona + peers +
  seed + instance_id → byte-identical); `heartbeat_unit`/`stop_unit`. Plus fixed window size,
  `--disable-dev-shm-usage`, a pinned user-data dir under `instance_paths`, seeded jitter. Browse
  list in `knobs.browse_names` — no schema change (§3.2).
- **Fallback containment lever if E3 slips:** `--host-resolver-rules=MAP * <sink-ip>` pins every
  name at the sink with no resolver at all — a bridge, not the design.
- *Acceptance:* E9's sensor proof, plus JA3/JA4 present and stable across runs.

### E5 — Linux real clients (extend `command`) — **medium**

**STATUS: BUILT + live-verified on dev45.** kerberos ok=4/4, ldap ok=2/2, ssh ok=2/2, smb ok=8/8 (dialect 3.11, 133 TREE_CONNECTs), snmp/dns/ntp ok. `rdp` is authored but unproven — see GAP-099 (`xfreerdp` exits 1 with no output against an open 3389, undiagnosed).

Extend `_COMMAND_TEMPLATES` (today `dns`/`http`/`https`/`ntp`/`icmp`); each new entry declares its
explicit transports, as existing rows do: `smb` → `smbclient` (pkg `smbclient`, tcp) · `ldap` →
`ldapsearch` (`ldap-utils`, tcp +udp CLDAP) · `kerberos` → `kinit`/`kvno` (`krb5-user`, tcp+udp) ·
`dcerpc` → `rpcclient` (`smbclient`, tcp) · `rdp` → `xfreerdp +auth-only` (`freerdp2-x11`, tcp) ·
`ssh` → `ssh` (`openssh-client`, tcp) · `snmp` → `snmpget` (`snmp`, udp).

- **Rewrite `_check_containment`** (ruling 1): it must no longer refuse a hostname. Keep checks 1
  (every declared address appears in the argv) and 3 (no **undeclared** endpoint hides in the argv);
  **check 2, the IP-literal requirement, is what is reversed**. Keep the `_bound_hosts` parser
  (`--resolve` stays a legitimate pin), no longer load-bearing. `smbclient`/`kinit`/`ldapsearch`
  **require** names — a ticket is issued against an SPN, not an IP — so the reversal is not optional
  for E5 to exist. `kinit` needs a realm-bearing `krb5.conf` (E7).
- *Acceptance:* each new proto classifies as itself at the sensor and **completes**, not RSTs.

### E6 — Windows domain-member engine — **large**

**STATUS: BUILT + live-verified on dev45.** `PartOfDomain=True`, domain `corp.axr.lab`; the DC was re-inferred as a DC from its listen census (88/tcp + 389/tcp together).

- **PowerShell built-ins only**, nothing to bake beyond the template — `gpupdate /force`,
  `nltest /dsgetdc:`, `klist get <spn>`, `net use \\dc\SYSVOL`, `w32tm /resync`, `Resolve-DnsName`,
  `Invoke-WebRequest` — driving Kerberos/LDAP/SMB/RPC/DNS/NTP **and** the dynamic-port RPC behind
  the unlabelled 6%.
- Reuse the existing Windows mechanics (`scheduled_task_start_block`, `heartbeat_scheduled_task`,
  `ga.shell_exec`) — the `persona.os_family` dispatch `ScriptedDriver`/`RealServiceDriver` have.
- **Budget for slowness:** GAP-051 — joins ~1–2 min each, effectively **serialised**, plus the DC's
  promotion reboot. Size E8 for **one** Windows client, not four.
- **Services order S3's warning applies:** a DC that *answers* is not a DC that is *re-inferred*.
  `roles.yaml`'s `domain-controller` row needs **88/tcp AND 389/tcp together** in the listen census,
  and a listen fact is written only from an **accepted** flow — so this engine must actually **dial
  88/tcp and 389/tcp at the DC** (`klist get` + an LDAP query over 389 close it).
- *Acceptance:* E9, plus `PartOfDomain=True`, plus the DC re-inferred as a DC.

### E7 — Images — **medium**

**STATUS: DEFERRED.** Not built this round — tools (the E5 toolset, Chromium) and the lab CA are installed at provisioning instead (`twin-configs/svc-engines/scripts/prepare-engine-client.sh`), per E1's ordering finding (install before the resolver is adopted). Cost: every deploy re-pays the apt install that a bake would pay once; a future `twin --update` on a standing twin still cannot add `install:` in place (`WORK_ORDER_twin_incremental_update.md`), so the deferral is a real, recorded cost, not a free pass. Fair game for a follow-up round.

- **Linux client:** bake the E5 toolset + Chromium + the **lab CA** — into the system store *and*
  Chromium's NSS db; they are not the same store.
- **Windows:** the two existing templates (`windows-server-2022@v1` VMID **9102**,
  `win11-finance@v4` VMID **9103**, per `deploy-dev45/images/`) already provide everything E6 needs —
  PowerShell built-ins, the join stack, RDP (off by default; S3's `enable-rdp.ps1` enables it).
  **Must be added: the lab CA in the machine trust store**, or `Invoke-WebRequest` fails validation.
- **Image-side, not `install:`** — `install:` on a **standing** twin trips `--update`'s in-place
  mutation refusal (`WORK_ORDER_twin_incremental_update.md`), and LES-024 says bake fragile tooling
  rather than apt-install it in an isolated guest. Declare new capabilities as `provides:`.

### E8 — The purpose-built twin — **small/medium**

**STATUS: BUILT + live-verified on dev45** — `twin-configs/svc-engines/`, deploy `svc-engines-24131435`, 97/97 ops COMPLETED, 8 nodes.

`twin-configs/svc-engines/`, modelled on `twin-configs/svc-verify/` (a `.yaml`, `tap-*.yaml`,
`tfc-*.yaml`, `personas/`, `scripts/`, `README.md`). Nodes: **resolver** (bind9) · **web** (nginx
80+443) · **file server** (samba 445) · **DC** (`windows-server-2022@v1`, `install: [{role: adds}]` +
`provides: [{domain: …}]`) · **Linux client** (E4+E5+`scripted` — the E0 proof) · **Windows client**
(`win11-finance@v4`, `joins:`) · **sink** (E2) · **router** (E1). Watch **GAP-078**: never pin an
explicit `ip:` on a lone router leg — it silently suppresses the segment's gateway.

### E9 — Gated dev45 live-verify — **medium**

**STATUS: PARTIAL PASS.** (1) protocol classification at the sensor — PASS (dns/smb/snmp/ssh; only 0.16% of flows unclassified). (2) TLS real SNI — not reached: GAP-100 blocks the sink from completing an external TLS handshake at all; the internal Caddy path did issue a real per-SNI cert. (3) Kerberos/LDAP/SMB complete rather than RST — PASS for smb/ssh/kerberos-engine-side; the **sensor's own** verdict reads Kerberos PARTIAL (KRB_ERR_RESPONSE_TOO_BIG forced a TCP retry Suricata logged as unclassified) and LDAP ABSENT (Suricata 7.0.10 has no LDAP parser until 8.0) — both recorded as sensor limits, not engine failures (GAP-102). (4) the DC's listen census re-infers it as a DC — PASS.

Per engine the proof is **at the sensor**, never the guest's own log: (1) traffic **classified as
the protocol claimed** — Zeek/Suricata/axquery label parity (`dns`, `ssl`/`tls`, `http`, `quic`,
`krb5`, `ldap`, `smb`, `dcerpc`, `ssh`, `ntp`), not bare `tcp`/`udp`; (2) **TLS shows the real SNI**
(read it from Zeek `ssl.log` or the pcap — GAP-076 means Suricata may emit no `tls` record);
(3) **Kerberos/LDAP/SMB complete** rather than RST; (4) the **DC's listen census re-infers it as a
DC**. STOP-and-ask is **not** required this round (the gates are open for an unattended run), but
**every destroy must name an explicit deploy id** — never a bare default.

## 6. Labelling — so a fidelity read never counts shape as protocol-real

- Every resolved assignment records its **engine** (`drive` kind) and a **`fidelity_class`** —
  `protocol-real` (browser/command/real-service) · `shape-only` (scripted) · `replayed` (replay).
- Publish it where a reader already looks — `te inventory` / `GET /api/deploys/{id}/inventory`,
  beside S1's per-host `listen[]` — plus, per capture window, the **egress phase** (E1) and any
  **handshake-only** marker (E2).
- `axrange/fidelity/` must read `fidelity_class` and refuse to score a `shape-only` flow as protocol
  fidelity. Today it cannot tell them apart.

## 7. Cost, risks and honest limits

- **Cost.** Sizings are on the slice headings. **Chromium (≈2 GB + 2 vCPU/client) and Windows (4 GB
  + 2 vCPU, slow serialised joins) are the two expensive axes** — one of each in the twin,
  deliberately. E2 is 1 VM (2 vCPU/4 GB); E8 ≈ 20 vCPU/28 GB over 8 nodes.
- **Riskiest unknown: the sink's protocol detection against real client diversity.** Chromium tries
  QUIC on 443/udp, falls back to TCP and retries with a different ALPN; `kinit` speaks Kerberos on
  88/udp with TCP fallback on a large TGS-REP; `smbclient` negotiates dialects. Mis-routing any of
  these is a **silent wrong answer** (LES-001). Mitigation: port-first dispatch, sniff as tiebreak,
  fail-loud unknown-protocol counter.
- **No SNI/body evidence exists** (GAP-076 + aximprint GAP-019) ⇒ generic bodies; never claim body
  fidelity. Pinned endpoints stay handshake-only without a real CA.
- Reversing containment trades a **structural** guarantee for an **operational** one: if E1's
  fail-safe is ever wrong-way-round, the twin talks to the real internet. Hence default-closed, E1
  before E4/E5/E6.
