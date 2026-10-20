# svc-engines — the throwaway twin for the **protocol-engine** round

Everything needed to prove `docs/WORK_ORDER_protocol_engines.md` engine by engine, in one folder.
Eight VMs, deployed once, probed per engine at the sensor, destroyed.

> [!IMPORTANT]
> **This has NOT been deployed yet.** It validates and plans clean
> (`ops=85 barriers=12`, plan id `svc-engines-87885275`); everything below the "step by step"
> line is a runbook written ahead of the run, not a record of one. `twins/svc-verify/`
> is the same bundle *after* its gated run — read its README for the shape of the corrections a
> first run produces.

§5 E8, verbatim, is the specification of this bundle:

> `twins/svc-engines/`, modelled on `twins/svc-verify/` (a `.yaml`, `tap-*.yaml`,
> `tfc-*.yaml`, `personas/`, `scripts/`, `README.md`). Nodes: **resolver** (bind9) · **web**
> (nginx 80+443) · **file server** (samba 445) · **DC** (`windows-server-2022@v1`,
> `install: [{role: adds}]` + `provides: [{domain: …}]`) · **Linux client** (E4+E5+`scripted` —
> the E0 proof) · **Windows client** (`win11-finance@v4`, `joins:`) · **sink** (E2) · **router** (E1).

**What this twin is for.** `svc-verify` proved real **servers** answer. This one proves real
**clients** put protocol *semantics* on the wire — a query name, a TLS SNI, an RC4 TGS-REP, a UNC
path — so the sensor records `dns`/`tls`/`krb5`/`ldap`/`smb`/`rdp` instead of bare `tcp`/`udp`.

> [!WARNING]
> **Never run any of this against a standing twin.** `te destroy` here always takes an explicit
> deploy id (§9 E9: *"every destroy must name an explicit deploy id — never a bare default"*).

---

## Files

| File | Tier | What it is |
|---|---|---|
| `te.yaml` | `te` | the twin config — 3 segments, 8 nodes, the declared `listen:` sets, the AD identity overlay, the **E1 seam** and the **E3 name-service chain** |
| `scripts/build-sink-payload.sh` | tool | builds `scripts/provision-sink.sh` = generated sink corpus + the shared `serve-sink.sh`. **Run it before `te plan`.** |
| `scripts/provision-sink.sh` | `te` | **GENERATED** — do not hand-edit, do not treat as source |
| `scripts/serve-twin-resolver.sh` | `te` | bind9: `svc.axr.lab` authoritative · `corp.axr.lab` → the DC · everything else → the sink |
| `scripts/serve-web-tier.sh` | `te` | nginx on 80 + 443, self-signed cert whose CN/SAN is `web.svc.axr.lab` |
| `scripts/serve-file-server.sh` | `te` | Samba (445, two shares, ~1 GiB of files) + snmpd (161) + the local `axr` account the ssh engine logs in as (22) |
| `scripts/prepare-dc.ps1` | `te` | the DC's forwarder → the twin resolver (E3 hop 3), retrying past the promotion reboot; plus the 4 MiB SYSVOL probe file the AD persona's SMB emit fetches |
| `scripts/prepare-engine-client.sh` | `te` | `ntpdate` under either Debian package name, `/etc/krb5.conf` (closes GAP-074 for this twin), a fail-loud census of tools **and mechanisms**, then — **last, deliberately** — the resolver pointer |
| `scripts/enable-rdp.ps1` | `te` | turns on 3389 on the joined member — copied **byte-identical** from `svc-verify` |
| `tap/default.yaml` | `tap` | binds **`svc` and `ext`** to the standing `o2-suricata` |
| `tfc/default.yaml` | `tfc` | four persona instances on one host — the **E0** acceptance case |
| `personas/svc-engines-browse.yaml` | `tfc` | `drive: browser` — DNS + real-SNI TLS + HTTP/1.1 + HTTP/2 + QUIC |
| `personas/svc-engines-ad-client.yaml` | `tfc` | `drive: command` — kerberos + ldap + smb + rpc at the DC |
| `personas/svc-engines-file-client.yaml` | `tfc` | `drive: command` — SMB byte volume out of the file server |
| `personas/svc-engines-mgmt-client.yaml` | `tfc` | `drive: command` — rdp + snmp + ssh + the DNS/NTP chain test |

## Roster

Segments: **`svc` 10.66.10.0/24** (servers) · **`cli` 10.66.20.0/24** (clients) · **`ext`
10.66.30.0/24** (the "external world" — the sink alone). Gateway is `.1` of each, on the `gw`
router. Every host IP is **pinned in the config**, so every command on this page is literal.

| host | image | role | seg | IP | cpu/ram/disk | serves (declared `listen:`) |
|---|---|---|---|---|---|---|
| `gw` | `debian-12-cloud@v1` | `gw` | all 3 | `.1` of each | 1 / 1Gi / 10Gi | — (router; WAN NIC on `vmbr0`) |
| `sink` | `debian-12-cloud@v1` | `sink` | ext | `10.66.30.10` | 2 / 4Gi / 20Gi | `22/t`, `53/u+t`, `80/t`, `443/t+u`, `123/u`, `9099/t`, `9098/u` |
| `resolver` | `debian-12-cloud@v1` | `dns-resolver` | svc | `10.66.10.11` | 1 / 2Gi / 20Gi | `53/u`, `53/t` |
| `web` | `debian-12-cloud@v1` | `web-tier` | svc | `10.66.10.12` | 1 / 2Gi / 20Gi | `80/t`, `443/t` |
| `files` | `debian-12-cloud@v1` | `file-server` | svc | `10.66.10.13` | 2 / 4Gi / 60Gi | `445/t`, `161/u`, `22/t` |
| `dc` | `windows-server-2022@v1` | `domain-controller` | svc | `10.66.10.21` | 2 / 4Gi / 60Gi | `53/t+u`, `88/t+u`, `123/u`, `135/t`, `389/t+u`, `445/t`, `3268/t` |
| `win-member` | `win11-finance@v4` | `corp-workstation` | cli | `10.66.20.21` | 2 / 4Gi / 40Gi | `135/t`, `137/u`, `3389/t` |
| `linux-client` | `debian-12-cloud@v1` | `engine-client` | cli | `10.66.20.11` | 4 / 6Gi / 40Gi | — (client only) |

**Totals: 15 vCPU / 27 GiB / 270 GiB** — the work order's §7 budget for E8 is *"≈ 20 vCPU/28 GB
over 8 nodes"*, with Chromium (~2 GB + 2 vCPU) and Windows (4 GB + 2 vCPU each, slow serialised
joins — GAP-051) named as the two expensive axes. One of each, deliberately.

Domain: **`corp.axr.lab`** (the LAB realm — D4, Authority: user; the observed `AXELLIO.DOM` is
recorded, never reproduced). Users: `alice` (Domain Users) and `bob-admin` (Domain Admins).

> [!IMPORTANT]
> **Neither user carries an explicit password, and that is load-bearing** (unlike `svc-verify`,
> where `alice` did). A `drive: command` persona gets its secret from exactly one place —
> `AdapterConfig.default_password`, i.e. **adapter.conf's `DEFAULT_PASSWORD`**, resolved at run
> time and written into a 0600 in-guest file; a persona document may never carry a credential
> literal (`command._resolve_client_knobs` refuses a secret-looking knob by name). Since
> `create-domain-user` falls back to that same value, leaving `password:` unset is what makes the
> account and the engine agree. **`alice`'s password is your `adapter.conf` `DEFAULT_PASSWORD`** —
> use it in the manual `kinit`/`smbclient` steps too.
> The file server's `corp` share is the one exception: it has a **local** `smbuser` with a literal
> lab password (`serve-file-server.sh`), used only by the manual check, never by a persona.

> [!NOTE]
> **`win-member` is a documented lab substitution and a recorded LIMIT.** On `dev45`
> `win11-finance@v4` binds template **101 — the Windows *Server* 2022 image** (the two Win-11
> templates 9102/9103 no longer exist;
> `deploy-dev45/images/bindings/proxmox/win11-finance.yaml`, 2026-08-13). So the "Windows client"
> is Server 2022 wearing the `win11-finance` id, and **consumer-Windows chatter will not appear** —
> Delivery Optimization on 7680, some SSDP/mDNS, the consumer Store/Update client traffic. The cast
> observed that chatter; this twin cannot reproduce it. **Record it; do not add a synthetic emitter
> to paper over it** — that would be shape dressed as reproduction, which is exactly what §6's
> `fidelity_class` exists to prevent.

---

## The three things that make this twin different from `svc-verify`

### 1. The containment triangle (§5 E1 + E2 + E3) — *nothing is measurable without all three*

| leg | state here |
|---|---|
| **E2 — the sink** | **built and wired.** `sink` runs `_shared/scripts/serve-sink.sh` over a corpus generated from the cast: bind9 authoritative for the **root** (613 observed names → their really-observed addresses, NXDOMAIN outside the corpus, nothing forwarded), Caddy with a **per-SNI certificate** from a lab CA on 80/443/h3, chrony, sshd, and a generic TCP/UDP responder so an unclaimed port **completes** instead of RSTing. |
| **E3 — the name service** | **built and wired, in this bundle.** See the chain below. |
| **E1 — egress phase** | **Built and declared** (GAP-101 closed the authoring seam). The twin-level `egress:` block in `te.yaml` states the posture; the deploy installs the phase engine on `gw` with `sink_ip` already resolved from the `role: sink` node. It deploys in `provision` (this twin apt-pulls hard, and `configure-router` runs long before any `provision-app`), so **step 2 is still a real step** — but it is now one command, and `axrange te info` prints which phase is live. |

### 2. The name-service chain (E3), at every hop

```
  Windows member  ──DNS──▶  the DC (10.66.10.21)          set by the JOIN OP
                                │  AD-integrated DNS, authoritative for corp.axr.lab
                                ▼  forwarder (prepare-dc.ps1, -UseRootHint $false)
  Linux hosts     ──DNS──▶  the resolver (10.66.10.11)     set in each Linux recipe
                                ├─ svc.axr.lab ............ AUTHORITATIVE here
                                ├─ corp.axr.lab ........... FORWARD ONLY → the DC
                                └─ everything else ........ FORWARD ONLY → the sink
                                                                  │
                                                                  ▼
                                                        the sink (10.66.30.10)
                                                        authoritative for '.', the cast corpus
```

Three rules this encodes, each one a thing a reviewer will doubt:

* **A domain member must use the DC as its DNS** (E3, verbatim: *"never point a joined member at
  bind9 — joins and SRV lookups break"*). The join op already does this; nothing here overrides it.
* **The AD zone has exactly one authority** — the DC. The twin resolver *forwards* `corp.axr.lab`
  to it rather than serving a second copy, which is what lets a **Linux** client resolve
  `dc.corp.axr.lab` and get real SPNs and real UNC paths.
* **Nothing ever reaches a real public resolver.** No `1.1.1.1` anywhere; `forward only` (never
  `forward first`, which would fall back to the **root hints** on a sink hiccup);
  `named.conf.default-zones` (Debian's root-hints `zone "."`) is removed on the resolver; the DC's
  root hints are turned off explicitly. If the sink is down, external names **fail**, loudly and
  locally.

> [!IMPORTANT]
> **Adopting this chain ends package installation on that host. Install first, repoint last.**
> The resolver is authoritative for the observed names and has **no upstream**, so the moment a
> guest's `/etc/resolv.conf` points at it, `apt-get install` fails with `Could not resolve
> 'deb.debian.org'` — while the router is still in the `provision` phase and NAT still works.
> Measured on dev45 2026-09-21. This is correct measure-phase behaviour arriving during
> provisioning, and it is an **ordering** constraint, not a bug: every recipe here installs
> everything it needs and repoints DNS as its last act
> (`prepare-engine-client.sh` step 0 apt → step 2 census → step 3 repoint, pinned by
> `tests/test_svc_engines_recipes.py`). To add a package to a live guest afterwards:
>
> ```sh
> printf 'nameserver %s\n' "$LAB_DNS" > /etc/resolv.conf   # the LAB resolver, not the twin's
> apt-get update && apt-get install -y <pkg>
> printf 'nameserver 10.66.10.11\nsearch svc.axr.lab corp.axr.lab\n' > /etc/resolv.conf
> ```
>
> …and treat any capture window that overlapped that detour as **uncontained** — the guest could
> resolve and reach the real internet for its duration (work order §5 E1/E3).

> [!NOTE]
> **There are no PTR records in this twin, and some clients quietly need them.** Nothing here
> answers `in-addr.arpa`. OpenLDAP canonicalizes the SASL host name through **reverse DNS** before
> it builds the SPN it asks the KDC for, so `ldapsearch -Y GSSAPI` asked for a principal that does
> not exist: `Local error (-2) … GSSAPI Error … (Server not found in Kerberos database)`, exit 250
> — from a ccache in which `kvno ldap/dc.corp.axr.lab` returned kvno=3 (dev45, 2026-09-21). The fix
> is `-N` on the client (now in the `ldap` template's GSSAPI argv), not synthetic PTRs, which would
> be inventing observations. MIT krb5's `rdns = false` in `/etc/krb5.conf` is the same idea for the
> Kerberos libraries and is already set.

### 3. Four persona instances on one host (E0)

`tfc/default.yaml` assigns `browser` + three `command` groups to `linux-client`. That is E0's
acceptance case; **if E0 has not landed, `tfc deploy` refuses the second assignment** — that file's
header gives the exact collapse (one persona per window, four sequential windows, highest
detection value first).

---

## Before you start

### 0a. Build the sink payload — **required before `te plan`**

```sh
sh twins/svc-engines/scripts/build-sink-payload.sh
#   -> twins/svc-engines/scripts/provision-sink.sh
#      2255 dns records, 1270 cidrs, ~55 KB (72 KB as base64)
```

The sink needs **two** payloads in-guest, in order (the corpus, then the recipe), and `run-script`
(OP-APP-030) delivers **one script per app** — `pass1_emit._emit_apps` refuses a second. So they
are concatenated at authoring time. Deterministic: same topology in, byte-identical payload out.

### 0b. The persona directory — automatic since the move to `twins/`

```sh
# (no AXRANGE_PERSONAS_DIR needed: twins/svc-engines/personas/ joins the path automatically)
```

Note `tfc validate` does **not** catch a missing persona (the lookup happens on the resolve path).

### 0c. Which state DB, and which sensor

Same three rules `svc-verify`'s README learned the hard way on `dev45` (its "Which state DB"
section is the long version):

1. **Use a dedicated scratch DB for a throwaway twin.** A bare `te destroy` in a shared DB targets
   the newest non-destroyed `te` deploy, which on `dev45` may be a twin you must not touch.
2. **Sensor resolution is DB-local** (GAP-069): `tap deploy` resolves `o2-suricata` from the **o2
   deploy row in the same state DB**, not from the cluster.
3. **`--images` is the directory *containing* `images/`** (LES-139), and the `suricata-sensor`
   binding lives only under the `axr-product` images root (GAP-072).

```sh
AX="axrange"                                   # or `.venv/bin/python -m axrange.cli.main`
SCRATCH_DB="$HOME/axr-svcengines.state.db"
DB="--db $SCRATCH_DB"
IMG="--images deploy-dev45"
TC="--twins-dir twins"
```

> [!IMPORTANT]
> **The standing `o2-suricata` (VMID 883610, capture iface `gre-cap`) was found DEAD and was
> restarted on 2026-09-21** — a recurrence of GAP-067. Before trusting it (LES-247: "present" is
> not "capturing"):
>
> ```sh
> $AX o2 status --db <the DB the surface was deployed into>
> # then at the sensor VM: eve.json's newest record must be SECONDS old, not months.
> ```
>
> **Its `eve.json` is ~720 MB of stale data.** Do **not** truncate it — it is another consumer's
> history and a truncation races the writer. Every read in step 5 below takes a **byte offset**
> first and reads only what was appended after it.

**Budget:** ~30–45 min for step 1. Two Windows VMs, a forest promotion with its reboot, and a
domain join dominate (GAP-051: joins ~1–2 min each, effectively serialised); on top of `svc-verify`'s
17.3 min this twin also apt-pulls Chromium and seven protocol tools onto `linux-client` and ~1 GiB
of Samba/Caddy packages.

---

## Step by step

### 1. `te deploy` → all ops complete

```sh
$AX te deploy --config svc-engines $TC $IMG $DB
$AX te info $IMG $DB
```

**Expect:** `Result: COMPLETED`, **97 op rows** (the plan prints `ops=85 barriers=12`; the executor
counts all 97), plan id **`svc-engines-87885275`** — pinned by the config's content, so a different
id means the config (or the generated sink payload) changed.

Ops worth confirming by name in the run log:

```
configure-router[gw]
provision-app[sink-app@sink]                  run-script[sink-app@sink]
provision-app[resolver-app@resolver]          run-script[resolver-app@resolver]
provision-app[web-app@web]                    run-script[web-app@web]
provision-app[files-app@files]                run-script[files-app@files]
provision-app[linux-client-app@linux-client]  run-script[linux-client-app@linux-client]
provision-app[dc-app@dc]                      run-script[dc-app@dc]        <- prepare-dc.ps1
wait-domain-ready[dc-app@corp.axr.lab]        domain-ready[corp.axr.lab]
join-host-to-domain[corp.axr.lab@win-member]  run-script[win-member-app@win-member]
create-domain-user[corp.axr.lab@dc@alice]
```

Every `provision-app`/`run-script` gates on `configure-router[gw]` (apt egress must exist before
anything installs), and each `run-script` gates on its own `provision-app`. **No OP-MIR op
appears** — a `te` deploy emits none since 2026-07-28; capture is step 4's job entirely.

Three recipes print a `NOTE ... does not resolve yet` line if a peer host's own `run-script` has
not finished (there is no cross-host ordering between apps). That is expected and is **not** a
failure — step 3 is the gate.

### 2. **Enter the measure phase** — the containment step

> [!CAUTION]
> **Until this is done the twin is UNCONTAINED.** The sink answers external names with the cast's
> **real observed addresses**, and `gw` will happily NAT a packet to 140.82.112.3 out to the real
> internet. Do **not** apply `svc-engines-browse` (step 6) before this step. A capture window that
> ran in the `provision` phase is uncontained and **must not be scored** (work order §5 E1, §6).

**Both halves have landed now** (adapter 2026-09-21, authoring GAP-101). `te.yaml`
declares the posture at twin level:

```yaml
twin_config:
  egress:
    phase: provision          # sink_ip resolved from the `role: sink` node
```

so the deploy installs the phase engine on `gw` itself — `/usr/local/sbin/axr-egress-phase.sh`,
persisted phase + `phase-since`, a boot unit that re-applies, `sink_ip` **already baked in** (so
the flip needs no new input and cannot point at a stale address), and a loud refusal on VyOS. The
twin deploys in `provision` on purpose: `configure-router` runs long before any
`provision-app`/`run-script`, and this twin's apt work (Caddy/bind9/chrony, Chromium + seven
protocol tools, Samba) has no package source in `measure`.

**The flip is one command**, at the router, over SSH with the credentials `te info` printed:

```sh
# ---- measure phase: the twin's only off-twin destination is now the sink ----
axr-egress-phase.sh measure
axr-egress-phase.sh --show          # phase, phase-since, sink, mode
axr-egress-phase.sh --healthcheck   # exit 0 ONLY if the LIVE state matches the recorded phase

# ---- back to the provision phase (to apt-install something) ----
axr-egress-phase.sh provision
```

**Confirm from outside the guest** — the question that went unanswered during the 2026-09-21 run:

```sh
axrange te info          # ...  egress: measure -- CONTAINED  sink=10.66.30.10 mode=default
axrange te inventory     # same line, first, above the roster
```

`provision` prints `OPEN (real egress)`, and a declared-but-never-applied phase prints
`NOT APPLIED` rather than the phase that was merely asked for.

> The hand-typed `ip route replace default via 10.66.30.10` + `nft ... forward oifname <wan> drop`
> lever this step used to carry is retired — it is exactly what the engine installs, and
> `scripts/gen_egress_policy.py` writes the same text as a `run-script` payload for a standing
> twin that was deployed before the declaration existed.

**Verify it took** (E1's own acceptance criteria):

```sh
# from linux-client: an "internet" address must reach the SINK, not the internet
curl -sv -k --max-time 10 https://1.1.1.1/ 2>&1 | grep -E 'subject|issuer|HTTP/'
#   PASS: the certificate is issued by "axRange sink lab CA"
# from linux-client: apt must now FAIL LOUD
sudo apt-get -o Acquire::http::Timeout=5 update ; echo "exit=$?"
#   PASS: non-zero
# at gw: zero packets forwarded to the WAN
nft list ruleset | grep -A2 'oifname'
```

Record the phase a capture window ran in (`axr-egress-phase.sh --show` prints `phase-since`, and
`te info` prints the live phase): a `provision`-phase window is uncontained and must not be
scored (§6).

### 3. The name-service chain answers — the E3 acceptance test

E3's acceptance: *"a Linux guest and a Windows member each resolve an internal name, and an
external observed name to a sink address."*

```sh
# --- on linux-client (10.66.20.11) ---
dig +short web.svc.axr.lab            #  PASS: 10.66.10.12   (resolver, authoritative)
dig +short files.svc.axr.lab          #  PASS: 10.66.10.13
dig +short dc.corp.axr.lab            #  PASS: 10.66.10.21   (forwarded to the DC)
dig +short github.com                 #  PASS: 140.82.112.3 or .4 — the OBSERVED address
dig +short no-such-name.invalid       #  PASS: nothing, and `dig` shows NXDOMAIN
#   ^ the last one is the containment proof: outside the corpus there is no answer,
#     because there is no upstream to ask.

# the three-hop path, explicitly:
dig +short @10.66.30.10 github.com    #  the sink itself answers authoritatively
dig +short @10.66.10.11 github.com    #  the resolver forwards to it

# --- on win-member (PowerShell) ---
Resolve-DnsName web.svc.axr.lab       #  PASS: 10.66.10.12  (member -> DC -> resolver)
Resolve-DnsName github.com            #  PASS: an observed address (…-> resolver -> sink)
Get-DnsClientServerAddress            #  PASS: the DC (10.66.10.21), NOT the resolver

# --- on dc (PowerShell) ---
Get-DnsServerForwarder                #  PASS: 10.66.10.11, UseRootHint = False
```

Each Linux recipe also ships a `--healthcheck` mode that re-runs its own share of this:

```sh
/var/lib/axrange/scripts/*serve-twin-resolver.sh --healthcheck   # path per run-script's delivery
```

> **If `dig github.com` SERVFAILs while `dig @10.66.30.10 github.com` succeeds**, the cause is
> named refusing an **authoritative (RA=0)** answer from a forwarder. `serve-twin-resolver.sh`'s
> header carries the one-stanza fix (`zone "." { type static-stub; server-addresses
> { 10.66.30.10; }; };`). This is the single most likely E3 failure and it was recorded before the
> run rather than guessed at during it.

### 3b. The engines, once, by hand — so a persona failure is readable

Each of these is the same conversation the corresponding persona drives, run once, with the
output in front of you. `$PW` is your `adapter.conf` `DEFAULT_PASSWORD` (see the roster note).

```sh
# --- on linux-client ---
# Kerberos: a real TGT, then a real SERVICE ticket (the persona does both; the
# second is where the detection value is).
echo "$PW" | kinit alice@CORP.AXR.LAB && klist
kvno cifs/dc.corp.axr.lab
#   PASS: klist shows krbtgt/CORP.AXR.LAB and then cifs/dc.corp.axr.lab
#   NOTE a KRB_AP_ERR_SKEW is CLOCK SKEW, not a broken KDC (LES-194).

# LDAP over GSSAPI -- the bind the AD persona does, using the ticket above.
# `-N` is NOT optional and not cosmetic: without it OpenLDAP canonicalises the
# SASL host name through reverse DNS, this twin serves no PTRs, and the bind
# asks the KDC for a principal that does not exist -- `Local error (-2) ...
# Server not found in Kerberos database`, exit 250, which reads as a Kerberos
# fault and is a DNS one. (Measured 2026-09-21; the `ldap` template renders it.)
ldapsearch -N -Y GSSAPI -LLL -H ldap://dc.corp.axr.lab \
    -b dc=corp,dc=axr,dc=lab -s sub '(objectClass=user)' dn | head
#   PASS: `SASL SSF: 256`, `SASL username: alice@CORP.AXR.LAB`, and DNs come back
#   FAIL `Unknown authentication method (-6) ... No worthy mechs found` means the
#        SASL GSSAPI MECHANISM is missing, not that the DC refused: install
#        libsasl2-modules-gssapi-mit (ldap-utils does not pull it).

# SMB at the DC, Kerberos-authenticated, fetching the SYSVOL probe file.
smbclient //dc.corp.axr.lab/SYSVOL -k -c 'get axr-probe.bin /dev/null'
#   PASS: ~4 MiB transferred. (`prepare-dc.ps1` put that file there.)

# SMB at the file server, ANONYMOUS, fetching a 256 MiB file -- the volume engine.
smbclient //files.svc.axr.lab/pub -N -m SMB3 -c 'get bulk/bulk-01.dat /dev/null'
#   PASS: ~256 MiB transferred
# and the authenticated share, which uses the LOCAL smbuser, not the domain alice:
smbclient //files.svc.axr.lab/corp -U 'smbuser%Axr4nge-Lab!' -c 'ls'

# DCE/RPC over the 445 named pipe (rpcclient has no port flag).
rpcclient -c srvinfo -A /dev/null -N dc.corp.axr.lab || rpcclient -c srvinfo -U "CORP\alice%$PW" dc.corp.axr.lab

# RDP, auth-only: a real TLS+CredSSP/NLA handshake, then disconnect.
echo "$PW" | xfreerdp /v:10.66.20.21:3389 +auth-only /sec:nla /cert-ignore /u:alice /d:corp /from-stdin

# SSH into the file server, as the mgmt persona does. It must COMPLETE, not
# merely KEX: `serve-file-server.sh` creates the local `axr` account with the
# lab password, and the engine authenticates with `sshpass -f <0600 file>`
# (there is no key in this twin). A `Permission denied (publickey,password)`
# here means the account or sshd's PasswordAuthentication, not the network --
# `serve-file-server.sh --healthcheck` reports both.
SSHPASS="$PW" sshpass -e ssh -o StrictHostKeyChecking=no \
    -o PreferredAuthentications=password axr@files.svc.axr.lab true ; echo "exit=$?"
#   (`-e`, not `-p`: `-p` would put the password in this host's process list.
#    The persona uses `-f <0600 file>`, which is better still.)
#   PASS: exit=0

# SNMP and NTP.
snmpget -v2c -c public 10.66.10.13 1.3.6.1.2.1.1.1.0
ntpdate -q -u 10.66.30.10
```

### 4. `tap deploy` — bind capture before generating anything

```sh
$AX tap deploy twins/svc-engines/tap/default.yaml --target svc-engines $DB
#   -> prints the tap deploy id. KEEP IT.
$AX tap info twins/svc-engines/tap/default.yaml $DB   # ports, GRE keys, sensor IP
$AX tap status <tap-deploy-id> $DB
```

**Expect:** two bindings — `{scope: segment, target: svc}` and `{scope: segment, target: ext}`,
both `[rx] -> o2-suricata` — resolving to every port on each net, including the `gw` router's own
`svc` and `ext` legs. `verify-capture` must pass (LES-001: a green mirror op proves nothing; only
a sentinel that arrived does).

### 5. Per-engine proof **at the sensor** — the half that cannot be faked in-guest

Every engine's proof is at the sensor, never the guest's own log (§5 E9). Take the byte offset
**first**:

```sh
SENSOR=883610                                  # o2-suricata on dev45
OFF=$(qm guest exec $SENSOR --timeout 60 -- /bin/sh -c 'stat -c %s /var/log/suricata/eve.json')
#   ^ keep OFF. Every read below is `tail -c +$((OFF+1))` — never a truncation (720 MB of
#     someone else's history), never a bare `tail -n` (which would read stale records).

# … generate the traffic (step 6, or by hand) …

qm guest exec $SENSOR --timeout 60 -- /bin/sh -c \
  "tail -c +$((OFF+1)) /var/log/suricata/eve.json | sed -n 's/.*\"event_type\":\"\([a-z0-9_]*\)\".*/\1/p' | sort | uniq -c | sort -rn"
```

| engine | persona | PASS at the sensor | FAIL |
|---|---|---|---|
| browser (E4) | `svc-engines-browse` | `dns` records for the browse names; `tls`/`ssl` **or** a `flow` with `app_proto: tls`; `http`; a `flow` with `app_proto` `quic`/`http3` on **udp/443** | the 443 flows show only as `tcp` |
| Kerberos (E5) | `svc-engines-ad-client` | `krb5` events naming `alice` / `cifs/dc.corp.axr.lab` — **both the AS exchange (`kinit`) and the TGS (`kvno`)** | `tcp` on 88 |
| LDAP (E5) | `svc-engines-ad-client` | `ldap` (Suricata **8.0**) or a `flow` with `app_proto: ldap` on 7.x | `app_proto: failed` **is expected on 7.x — report it, do not score it** |
| SMB (E5) | `svc-engines-ad-client`, `svc-engines-file-client` | `smb` events; the file engine's flows carrying **hundreds of MB** | `tcp` on 445, or bytes in the KB |
| DCE/RPC (E5) | `svc-engines-ad-client` | `dcerpc` over the **445 named pipe** (`rpcclient` has no port flag — it negotiates RPC over SMB), and the dynamic high ports a call moves to (the cast's unlabelled ~6%) | only `smb`, or bare `tcp` |
| RDP (E5) | `svc-engines-mgmt-client` | `rdp` events, or a `flow` with `app_proto: rdp` | `tcp` on 3389 |
| SSH (E5) | `svc-engines-mgmt-client` | `ssh` events with a client/server banner, a real KEX and a **completed** session (`sshpass` + the local `axr` account) | `tcp` on 22, or a KEX that always ends in an auth failure |
| SNMP (E5) | `svc-engines-mgmt-client` | `snmp`, or `app_proto: snmp` | `udp` on 161 |
| DNS/NTP | `svc-engines-mgmt-client` | `dns` with the qname; NTP via the flow fallback | — |

Two version caveats, both honest rather than convenient — the sensor image bakes Suricata from
**bookworm-backports (7.x)**:

* **`ldap` is a Suricata 8.0 event type.** On 7.x check `"event_type":"flow"` +
  `"app_proto":"ldap"`; if even that is absent, record LDAP as *not classified by this sensor
  version*. `svc-verify`'s run saw `app_proto=failed` for LDAP.
* **`ntp` may not be in the sensor's `eve-log.types:` list** (GAP-077, a one-line fix). Same
  fallback.
* **Suricata may write NO `tls` records at all** (GAP-076) even with `tls` enabled + `extended:
  yes` — `svc-verify`'s run got nine flows with `app_proto: tls` and zero `tls` events. **That
  record is where SNI, subject and JA3 live**, so for the SNI claim read Zeek's `ssl.log` or the
  pcap instead.

To pin one flow rather than a histogram:

```sh
qm guest exec $SENSOR --timeout 60 -- /bin/sh -c \
  "tail -c +$((OFF+1)) /var/log/suricata/eve.json | grep -c '10.66.10.21'"   # everything at the DC
```

### 6. `tfc deploy` — the engines, unattended

```sh
# step 0b: no AXRANGE_PERSONAS_DIR needed -- a tier file inside twins/svc-engines/ puts its personas/ on the path
$AX tfc deploy twins/svc-engines/tfc/default.yaml --target svc-engines $DB
$AX tfc status <tfc-deploy-id> $DB
```

**Expect (post-E0):** four assignments, four instances on `linux-client`, four distinct units.
**If E0 has not landed**, the second assignment is refused with *"a host may carry exactly one
persona instance"* — collapse to one assignment and run four sequential windows
(`tfc/default.yaml`'s header gives the order).

In-guest evidence, read **even on a green run** — they are the positive control that separates
"the persona did 32 things" from "the persona did nothing and nothing complained" (LES-245):

```sh
# on linux-client
ls /var/log/axrange/                      # one log per instance
grep -c '^run ' /var/log/axrange/*.log    # invocations
grep -E 'fails=|missing_tool=|MISSING TOOL' /var/log/axrange/*.log
systemctl list-units 'axr-*'              # four transient units, one per instance (E0's proof)
```

> **One `fails=` contribution is BY DESIGN — know it before you chase it.** The AD persona's
> first `ldap` firing, if it happens before the first `kinit`: GSSAPI has no ticket yet and it
> self-corrects next cycle. Anything else in that counter is real.
>
> **Retired 2026-09-21:** the ssh emit's permanent auth failure used to be on this list. It was
> not a design property, it was a client that never once did what it claims to (`ssh -o
> BatchMode=yes axr@…` against a host with no `axr` account: `Permission denied`, exit 255, every
> cycle). The account and the credential path are now real and the session COMPLETES — so an
> `ssh` failure here is a finding again, which is the point of a counter (LES-245).
>
> The `browser` persona's counters changed meaning too: `ok=` no longer means "chromium exited 0"
> (it does that after rendering its own error page) but "a non-empty DOM came back and it was not
> the error page", with `errpage=`/`empty=` breaking out the two ways a load can fail.

> **Blocked today:** the AD persona's RPC emit is `proto: dcerpc`, which E5's template table has
> but `personas.schema.KNOWN_PROTOS` does not, so PSN-040 errors on it until that one-line
> addition lands. See "What only a cluster can settle", item 8 — it is the one item there that
> the cluster has nothing to do with.

Then **re-run step 5 with no operator traffic at all**: the same `event_type`s must keep appearing
from the personas' own cadence.

**E6's extra acceptance, if the Windows engine has landed:** `PartOfDomain=True` on `win-member`,
**and the DC re-inferred as a DC** — which needs something to actually dial **88/tcp AND 389/tcp**
at it (the AD persona's kerberos + ldap emits are that something; both derive to TCP).

### 7. `te destroy` — always with the explicit id

```sh
$AX te destroy svc-engines-<deploy-hash> --allow-destroy $DB   # cascades exr -> tfc -> tap -> te
$AX te destroy svc-engines-<deploy-hash> --allow-destroy $DB   # idempotent: a clean no-op
$AX list-deploys $DB
```

> [!CAUTION]
> `te destroy` takes a **literal deploy id** — it does not resolve a twin-config name, and a bare
> `te destroy` targets the newest non-destroyed `te` deploy in the DB. Pass the id
> (`list-deploys --tier te` prints it if you lost it).

Then confirm the blast radius: the standing twins untouched, `qm list | wc -l` back to its
pre-deploy absolute number (take that **before** you deploy, from the cluster, not from
`list-deploys`' newest-20 view — LES-249), gretap count back to baseline, and `img`/`o2` resources
untouched.

---

## What only a cluster can settle

Everything provable offline is already proven (the config validates and plans, the personas load
and pass every PSN rule, every recipe passes `sh -n`). **A green pytest run is not this gate.**
These are the claims that need the cluster, worst-first:

1. **`eve.json` naming the protocol, not the transport.** The entire round reduces to this: `dns`,
   `tls`, `krb5`, `ldap`, `smb`, `dcerpc`, `rdp`, `ssh`, `snmp` — **not** bare `tcp`/`udp`. No test
   can produce it; only a sensor looking at a real client talking to a real server can.
2. **TLS carrying the REAL SNI.** The sink mints a certificate per SNI from a lab CA, so the name
   in the handshake is the cast's observed name. GAP-076 means Suricata may write no `tls` record
   at all — read Zeek's `ssl.log` or the pcap. *No claim about SNI is valid from `app_proto` alone.*
3. **Kerberos / LDAP / SMB COMPLETING rather than being RST.** This is the axquery loop's
   Iteration-3 finding: **the DC reset the persona's filler bytes.** A real server does not tolerate
   a fake client — the flow it produces is a reset, which is exactly the shape a reader would
   otherwise take for "the protocol was there". Completion, not presence, is the criterion.
4. **The DC's listen census re-inferring it as a DC.** `roles.yaml`'s `domain-controller` row needs
   **88/tcp AND 389/tcp together**, and a listen fact is written only from an **accepted** flow —
   so an engine must actually dial both. Answering is not enough (services order S3's warning).
5. **The sink's protocol detection against real client diversity** — the work order's own
   *riskiest unknown* (§7). Chromium tries QUIC on 443/udp, falls back to TCP and retries with a
   different ALPN; `kinit` speaks Kerberos on 88/udp with TCP fallback on a large TGS-REP;
   `smbclient` negotiates dialects. **Mis-routing any of these is a silent wrong answer** (LES-001).
   Read the sink's fail-loud "unknown protocol on port N" counter as part of every window.
6. ~~**The ~161 KB `provision-sink.sh` payload delivered over QGA.**~~ **SETTLED on dev45,
   2026-09-21 — it failed, and the ceiling is now measured.** `run-script` delivers the script
   base64-embedded in a single `agent/exec` COMMAND ARGUMENT, and PVE refuses an argument of
   **131072 bytes or more (128 KiB)** — surfacing as `HTTP 596 Broken pipe` with no error body,
   which reads like a network fault and is not one. (The total request body is bounded far
   higher, at pve-http-server's `$limit_max_post` = 512 KiB; splitting one payload across
   several arguments is what buys room.) Both halves of the fix landed, and the corpus was NOT
   trimmed: the corpus files now travel **gzip+base64 inside the installer** (161 KB → 55 KB,
   72 KB as base64) and `guest_agent.write_file` **chunks** anything larger across arguments and
   requests, with an in-guest byte-count check on a multi-request write.
7. **bind9 accepting an authoritative answer from a forwarder** (step 3's note) — one stanza either
   way, but it decides whether *any* external name resolves in the twin.
8. **One cross-slice mismatch is open RIGHT NOW and is not a cluster question at all.** E5's
   template table is keyed **`dcerpc`**, but `personas.schema.KNOWN_PROTOS` carries `rpc` and not
   `dcerpc`, so `svc-engines-ad-client`'s RPC emit fails **PSN-040** today. This bundle follows the
   driver (the key that decides whether anything renders) rather than authoring `rpc`, which would
   validate and then refuse at render. **One line fixes it** — add `dcerpc` to `KNOWN_PROTOS`.
   Everything else in the four personas validates clean and renders byte-identically today
   (checked: `validate_persona` + `render_command_client` against this twin's peer map).

## What this twin does **not** prove

* **Nothing about consumer Windows.** See the `win-member` note above — Server 2022 under a Win-11
  id, so no Delivery Optimization, no consumer Store/Update chatter.
* **Nothing about HTTP body or SMB file CONTENT.** No recorded bodies exist (GAP-076 + aximprint
  GAP-019), so the sink serves generic bodies and the file server serves sparse files. **Real
  names, real certificates, real volumes — synthetic content**, and never the other claim.
* **Pinned endpoints are HANDSHAKE-ONLY.** `login.microsoftonline.com` is in the browse list on
  purpose: it aborts after the handshake against a lab CA. That flow is a real TLS negotiation with
  a real SNI and must **never** be scored as a successful fetch.
* **Kerberised SMB only at the DC.** `files` is not domain-joined, so its SMB is NTLM/guest. The
  genuinely Kerberised SMB path is the AD persona's SYSVOL emit.
* **Nothing about scale.** Eight VMs. The 138-VM cast is a different exercise.
* **Nothing about a contained twin until step 2 is done.** The twin declares `phase: provision`
  and deploys open, by necessity (apt). The declaration makes the phase installed, readable and
  one command away — it does not make the twin contained on its own, and `te info` saying
  `provision -- OPEN (real egress)` is the honest report of that, not a warning that can be
  skipped.
* **The client tool set is installed at run time, not baked** (GAP-056 stays open; E7 owns it).
  Legitimate only because this twin is a throwaway: the same `install:` list on a standing twin
  trips `--update`'s in-place mutation refusal.
* **The lab CA is not in any trust store.** `svc-engines-browse` sets
  `knobs.browser_ignore_cert_errors: true` until E7 bakes it. TLS fidelity is unaffected — only
  the client's *verification* is skipped — but no claim about certificate validation can be made
  from this twin as it stands.
