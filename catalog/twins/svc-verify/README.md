# svc-verify — the throwaway twin for the row-5 **server half** live-verify

Everything needed to run `docs/WORK_ORDER_protocol_faithful_services.md` **§9.2**, in one folder.
Seven VMs, deployed once, probed, destroyed.

> [!IMPORTANT]
> **This ran on `dev45` on 2026-09-05/06 and PASSED — all six steps** (result:
> `docs/WORK_ORDER_protocol_faithful_services.md` §9.2.1; CHANGELOG 2026-09-06 "Services §9.2 gated
> `dev45` live-verify PASSED"). Everything below is the runbook **as corrected by that run**: the
> DB guidance was wrong for this cluster, the sensor path needed rebuilding, and three expected
> outputs did not match reality. Each correction is marked **[run 2026-09-06]** where it lands.
> Twelve gaps came out of the run — `docs/PRODUCT_GAPS.md` GAP-067..GAP-078 — and the two most
> likely to bite a re-run are **GAP-067** (the standing `o2-suricata`'s Suricata was dead for 40
> days with `o2 status` reporting nothing) and **GAP-069** (sensor resolution is state-DB-local).

§9.2, verbatim, is the specification of this bundle:

> A small purpose-built throwaway twin: a `gw` router (apt egress), one bind9 resolver, one nginx
> web-tier, one chrony host, one `windows-server-2022` DC, one `win11-finance` member with `joins:`,
> one Linux client carrying a `drive: command` persona, and a `tap` plan binding the segment to a
> standing O2 `suricata-sensor`.

> [!WARNING]
> **Never run any of this against `te corpapcn-twin-0e4f7d58`.** It is do-not-destroy, it is the
> fidelity scorecard's run-1 subject, and giving it a declared listen set would trip `--update`'s
> mutation refusal by the server half's own design (§7.2). That is *why* this twin exists.

**What only a cluster can prove** (§8's "as built" note): `ss -lnH -tu` matching the declared set,
`dig` returning NOERROR with an ANSWER, a completed TLS handshake, `PartOfDomain=True`, and — the
half that cannot be faked in-guest — `eve.json` naming a real protocol instead of bare `tcp`/`udp`.
Everything provable offline is already proven; a green pytest run is **not** this gate.

---

## Files

| File | Tier | What it is |
|---|---|---|
| `te.yaml` | `te` | the twin config — 2 segments, 7 nodes, the declared `listen:` sets, the AD identity overlay |
| `scripts/serve-dns-resolver-10-80-64-core.sh` | `te` | **bind9** — copied byte-identical from the golden cast |
| `scripts/serve-dns-resolver-10-20-196-core.sh` | `te` | **bind9 + chrony** — copied byte-identical from the golden cast |
| `scripts/serve-web-tier-10-80-72-core.sh` | `te` | **nginx + self-signed TLS on 443** — copied byte-identical from the golden cast |
| `scripts/enable-rdp.ps1` | `te` | turns on 3389 on the joined member — copied byte-identical from the `ad-cast` fixture |
| `scripts/install-ntpdate.sh` | `te` | the one hand-written script: installs the `ntpdate` **binary** under either Debian package name, fail-loud |
| `tap/default.yaml` | `tap` | binds the `svc` segment to the standing `o2-suricata` |
| `tfc/default.yaml` | `tfc` | the `drive: command` persona onto the probe client |
| `personas/svc-verify-probe.yaml` | `tfc` | the bundle-local persona (dns / https / ntp / icmp), loaded from the twin folder's `personas/` by the tier CLI (`AXRANGE_PERSONAS_DIR` still overrides) |

## Roster

Segments: **`svc` 10.66.10.0/24** (servers, gw = `.1`) and **`cli` 10.66.20.0/24** (clients,
gw = `.1`). Every host IP below is **pinned in the config**, so every command on this page is
literal. Confirm with `axrange te info` after deploy.

| host | image | role | seg | IP | serves (declared `listen:`) |
|---|---|---|---|---|---|
| `gw` | `debian-12-cloud@v1` | `gw` | both | `10.66.10.1`, `10.66.20.1` | — (router; WAN NIC on `vmbr0`) |
| `svc-dns` | `debian-12-cloud@v1` | `dns-resolver` | svc | `10.66.10.11` | `53/udp`, `53/tcp` |
| `svc-web` | `debian-12-cloud@v1` | `web-tier` | svc | `10.66.10.12` | `443/tcp` |
| `svc-ntp` | `debian-12-cloud@v1` | `ntp-server` | svc | `10.66.10.13` | `53/udp`, `53/tcp`, `123/udp` |
| `axdc01` | `windows-server-2022@v1` | `domain-controller` | svc | `10.66.10.21` | `53/tcp+udp`, `88/tcp+udp`, `123/udp`, `135/tcp`, `389/tcp+udp`, `445/tcp`, `3268/tcp` |
| `win-member` | `win11-finance@v4` | `corp-workstation` | cli | `10.66.20.21` | `135/tcp`, `137/udp`, `3389/tcp` |
| `probe-client` | `debian-12-cloud@v1` | `probe-client` | cli | `10.66.20.11` | — (client only) |

> [!NOTE]
> **On `dev45`, `win11-finance@v4` is a documented lab substitution**: its binding points at
> template **101**, the built `windows-server-2022` image, because the original Win-11 template and
> its storage no longer exist (`deploy-dev45/images/bindings/proxmox/win11-finance.yaml`, 2026-08-13).
> So `win-member` boots Server 2022 wearing the `win11-finance` id. Nothing here depends on the
> difference — it domain-joins and answers RDP either way — but the observed port profile differs
> slightly from a real Win 11 client (no Delivery Optimization on 7680, which this twin does not
> declare anyway). `137/udp` is the one declared member port that depends on NetBIOS-over-TCP being
> enabled; if it is absent, record it as an observation, not a blocker.

Domain: **`corp.axr.lab`** (the LAB realm — D4, Authority: user; the observed `AXELLIO.DOM` is
recorded, never reproduced). Users: `alice` (Domain Users, password **`Axr4nge-Lab!`**, set
explicitly so step 3's `kinit` is exact) and `bob-admin` (Domain Admins, lab-default password).

### The three recipes, and what they answer

Copied **byte-identical** (`cmp` clean) from `twins/corpapcn-golden/scripts/` — the
freshly-installed golden cast, i.e. aximprint's real-daemon output. **No adaptation was needed and
none was made**: each recipe discovers its own address (`axr_self_ip`) and forwarder
(`axr_gateway`) at run time, so nothing in them is host-specific. Original filenames are kept so the
provenance (which cast node group each came from) stays readable.

| recipe | on host | daemon(s) | the probe target it creates |
|---|---|---|---|
| `serve-dns-resolver-10-80-64-core.sh` | `svc-dns` | bind9 | zone **`corp.axr.lab`**, authoritative |
| `serve-dns-resolver-10-20-196-core.sh` | `svc-ntp` | bind9 **+ chrony** | same zone, plus `allow all` / `local stratum 10` |
| `serve-web-tier-10-80-72-core.sh` | `svc-web` | nginx + openssl | vhost **`web-tier-10-80-72-core.corp.axr.lab`** on 443, self-signed (CN = SAN = that name) |

**The DNS names the bind9 zone answers** (every one an `A` to the resolver's own IP, plus a
wildcard so any other name under the zone also answers):

```
ns   axdc01   axdc04   axe-7440-1   axehyperv-01   axellio-lt1   bw-l   bw-mini   desktop-21n0pq1   my   *
```

Two of those carry a name the source capture actually saw — `axdc04.axellio.dom`
(`evidence_class: referenced`, `locality: local`, bound to `10.80.20.5`) and `axdc01.axellio.dom`
(`referenced`, and `external` in the export because the entity it is bound to there is an IPv6
address), both from `observations.dns` in `corpapcn-golden.topology.json` — re-homed under the lab
realm by D4. Note `referenced`, not `observed`: the capture saw the NAME in an answer, not the host
(§6.1's correction 2). **The probes below use
`axdc04.corp.axr.lab`**, deliberately not `axdc01`: `axdc01` is also this twin's real DC, and a
name two servers answer differently makes a green result unreadable.

**The vhost name is NOT observed.** `web-tier-10-80-72-core.corp.axr.lab` is the cast compiler's own
node-group name: the observed TLS SNI / HTTP `Host` names exist in aximprint's belief store but have
no IR carrier yet (§6.1's amendment, aximprint **GAP-019**), which is exactly why **S4 is not
built**. Read the vhost as `compiler`-class, not as reproduction evidence.

---

## Before you start

### Which state DB — **[run 2026-09-06], and the single biggest correction on this page**

The `--db deploy-dev45/state.db` convention this README originally carried is **wrong for `dev45`**:
that file is a **stale June copy**. Three rules, learned the hard way:

1. **Use a dedicated scratch DB for a throwaway twin.** This bundle deploys and destroys seven VMs;
   there is no reason for its rows to land beside the standing `corpapcn-twin`, and a bare
   `te destroy` in a shared DB targets the newest non-destroyed `te` deploy — which on this cluster
   is a twin you must not touch.
2. **Sensor resolution is DB-LOCAL** (GAP-069). `tap deploy` resolves its sensor from the **o2
   deploy row in the same state DB**, not from the cluster. A sensor that is live, capturing and
   visible in `o2 status` **against another DB** does not exist as far as your tap plan is
   concerned, and the error you get names a command that will contradict it:
   `unknown sensor 'o2-suricata' -- not in the standing O2 inventory (axrange o2 status)`.
3. **Know where each thing actually lives on `dev45`:** the live twin DB is **`~/.axrange/state.db`**;
   the standing O2 surface is in **`/home/rocky/axr-product/deploy-dev45/state.db`**; the
   `suricata-sensor` image binding exists **only** under the `axr-product` images root (GAP-072 —
   a missing binding is silent at plan time and fails on the cluster, after SDN ops have run).

**The working sensor recipe, until the standing sensor is repaired (GAP-067):** stand up a
**dedicated scratch surface** with one `suricata-sensor` and point the tap plan at it.

```sh
# a scratch o2 manifest -- one sensor, its own transport net, no export:
#   surface: o2-svcverify
#   transport: 10.255.252.0/24
#   sensors: [{id: svc-suricata, type: suricata-sensor}]
axrange o2 deploy <scratch-manifest>.yaml         --images /home/rocky/axr-product/deploy-dev45         --db "$SCRATCH_DB"
#   -> o2-svcverify-<hash>, 15 ops, ~1.2 min; the sensor VM comes up on 10.255.252.10
#      with Suricata 7.0.10 "Engine started". Note the --images root: it is NOT the
#      twin's (LES-248 -- an images root is verified per IMAGE, not once per root).
```

Then edit a scratch copy of `tap/default.yaml` to name **`svc-suricata`** instead of
`o2-suricata`, and run every tier's verb with the same `--db "$SCRATCH_DB"`.

> [!NOTE]
> **`axrange o2 deploy` and `o2 destroy` are the operator's to run.** The permission classifier
> blocks those two verbs for agents, so in a pipeline run the surface steps are hand-offs, not
> agent steps. Every other verb on this page (`te`/`tap`/`tfc` deploy, status, info, destroy) is
> agent-runnable.

> [!WARNING]
> **Do NOT try to re-assert the standing surface in place.** `stand_up_surface` returns early on a
> `completed` deploy (GAP-067), and a *fresh* deploy of the standing manifest **cannot adopt** its
> vnet — vnet names became plan-unique on 2026-07-27, one day after that surface was deployed
> (GAP-070). The 2026-09-06 attempt created a parallel vnet with the same CIDR in the shared zone
> before failing for an unrelated reason, and a run that got further would have moved the standing
> sensor's NIC onto an address-less bridge.

```sh
# 0a. Whichever sensor you use, confirm it is EXISTS + RUNNING + FRESH before you rely on it
#     (LES-247; "present" is not "capturing" -- the standing one was dead for 40 days):
axrange o2 status --db <the DB the surface was deployed into>
#     ... then, at the sensor VM, that eve.json's newest record is SECONDS old, not months.
```

`--images` is the directory *containing* `images/`, never `images/` itself (LES-139) — a wrong root
silently loads zero bindings and every clone fails `template_vmid missing`. The commands below are
written out in full once, then abbreviated as `$AX`:

```sh
AX="axrange"                       # or `.venv/bin/python -m axrange.cli.main`
SCRATCH_DB="$HOME/axr-svcverify.state.db"   # a dedicated DB for this throwaway
DB="--db $SCRATCH_DB"
IMG="--images deploy-dev45"        # the TWIN's images root; the sensor needs the other one
TC="--twins-dir twins"

# 0b. The persona is BUNDLE-LOCAL. A tier file inside twins/svc-verify/ puts
#     the twin's personas/ on the persona path itself (AXRANGE_PERSONAS_DIR
#     still overrides); before the twins/ move this step was a hand export.
```

**Budget:** two Windows VMs, an AD forest promotion (with its reboot) and a domain join dominate the
wall clock — allow ~30-45 min for step 1 (GAP-051: joins are ~1-2 min each and effectively
serialized). The five Linux hosts are quick.

---

## §9.2 step by step

### 1. `te deploy` → all ops complete; `te info` shows the access facts

```sh
$AX te deploy --config svc-verify $TC $IMG $DB
$AX te info $IMG $DB
```

**Expect:** `Result: COMPLETED`, **82/82 op rows** (72 action ops + 10 barriers — the plan prints
`ops=72 barriers=10`; the executor counts all 82), plan id `svc-verify-14da1c1a` (pinned by the
config's content — a different id means the config was edited). **[run 2026-09-06]** measured
**17.3 min**, well inside the budget below; the DC path is 8.6 min of it (`wait-domain-ready`
332 s, the ADDS install 182 s, the join 53 s), and one Windows first-boot QGA `guest-exec` timeout
was absorbed by the retry — that is normal, not a failure. `te info` prints
per-node ip / login user / effective password / a paste-ready `ssh` or `rdp` hint; `--no-secrets`
masks the passwords for screen-sharing.

Ops worth confirming by name in the run log:

```
provision-app[svc-dns-app@svc-dns]        run-script[svc-dns-app@svc-dns]
provision-app[svc-web-app@svc-web]        run-script[svc-web-app@svc-web]
provision-app[svc-ntp-app@svc-ntp]        run-script[svc-ntp-app@svc-ntp]
provision-app[probe-client-app@probe-client]  run-script[probe-client-app@probe-client]
provision-app[axdc01-app@axdc01]          wait-domain-ready[axdc01-app@corp.axr.lab]
domain-ready[corp.axr.lab]                join-host-to-domain[corp.axr.lab@win-member]
create-domain-user[corp.axr.lab@axdc01@alice]      run-script[win-member-app@win-member]
configure-router[gw]
```

Every `provision-app`/`run-script` gates on `configure-router[gw]` (apt egress must exist before
anything installs), and each `run-script` gates on its own `provision-app` (packages before
configuration). **No OP-MIR op appears** — a `te` deploy emits none since 2026-07-28; capture is
step 2b's job entirely.

> If a Linux host's `provision-app` fails on `apt-get`, check `configure-router[gw]` really
> completed and that the host has a default route — the twin's whole egress story is the router.

### 2. In-guest: `ss -lnH -tu` on each server host matches its declared set

From the PVE node (over QGA), or by `ssh` using the credentials `te info` printed:

```sh
# on each of svc-dns / svc-web / svc-ntp --

# (i) the full record, addresses included (paste this into the run log):
ss -lnH -tu | awk '{print $1, $5}' | sort -u

# (ii) the check: NON-LOOPBACK listeners only, as port/proto
ss -lnH -tu | awk '$5 !~ /^(127\.|\[::1\])/ {split($5,a,":"); print $1"/"a[length(a)]}' | sort -u
```

| host | **declared — every one MUST be present** | also expected (image-supplied, not declared — informational, never a failure) |
|---|---|---|
| `svc-dns` | `udp/53`, `tcp/53` | `tcp/22` (sshd), `tcp/953` (rndc, loopback only) |
| `svc-web` | `tcp/443` | `tcp/22`, `udp/53` + `tcp/53` **on 127.0.0.53 only** (systemd-resolved — not disabled here) |
| `svc-ntp` | `udp/53`, `tcp/53`, `udp/123` | `tcp/22`, `tcp/953`, `udp/323` (chrony cmdmon, loopback only) |

**The pass criterion, stated exactly** (§9.2 says "matches its declared set exactly"; an exact set
equality is not achievable because the *image* supplies sshd and a few loopback-only helper
sockets, so this is the operational form):

* every declared `(port, proto)` has a listener on a **non-loopback** address, **and**
* the non-loopback listener set contains nothing beyond the declared set + `tcp/22` **+ the image's
  own baseline** — see immediately below.

> [!IMPORTANT]
> **[run 2026-09-06] The exclusivity half of that criterion cannot hold as written on a stock
> Debian cloud image** (GAP-075). `svc-web` serves exactly its declared `443/tcp` **and also** binds
> **LLMNR on `5355/tcp+udp` on the wildcard**, put there by the image's `systemd-resolved`. That
> listener belongs to the **image**, not to the twin (LES-250), and no amount of authoring removes
> it. Record it as an image-baseline listener and pass the step; the two honest fixes — a published
> per-image listener baseline, or an image bake that turns LLMNR off — are GAP-075's, not this
> runbook's. The two bind9 hosts are unaffected: their recipes displace `systemd-resolved` outright.

The two bind9 recipes disable `systemd-resolved` before starting `named` (it owns `:53` on a stock
cloud image); on `svc-dns`/`svc-ntp` a `127.0.0.53:53` listener therefore means the recipe's
displacement step did **not** run — treat that as a failure of this step, not a detail.

Windows hosts have no `ss`; the check is "every declared port is listening", not set equality
(Windows also listens on WinRM, the RPC dynamic range and more):

```powershell
# axdc01 -- expect 53, 88, 123(udp), 135, 389, 445, 3268 among them
Get-NetTCPConnection -State Listen | Select-Object -Expand LocalPort | Sort-Object -Unique
Get-NetUDPEndpoint            | Select-Object -Expand LocalPort | Sort-Object -Unique
# win-member -- expect 135, 3389 (tcp) and 137 (udp)
```

### 2b. Bind capture, before you generate the traffic step 4 has to see

```sh
$AX tap deploy twins/svc-verify/tap/default.yaml --target svc-verify $DB
#   -> prints the tap deploy id, e.g. tap-svc-verify-<hash>. KEEP IT.
$AX tap info twins/svc-verify/tap/default.yaml $DB   # ports, GRE keys, sensor transport IP
$AX tap status <tap-deploy-id> $DB
```

> `tap info` takes a plan file or a **tap deploy id** — it has no `--target`. `tap status --target`
> wants the *twin's deploy id*, not the config name (`taps.tap_status` looks for a plan named
> `tap-<target>`), so pass the id `tap deploy` printed.

**Expect:** one binding — `selector:{scope: segment, target: svc} [rx] -> o2-suricata` — resolving
to **every port on `svc`**: `svc-dns`, `svc-web`, `svc-ntp`, `axdc01` **and the `gw` router's own
`svc` leg** (which is how all client-side traffic enters the segment), each mirrored `rx` so no
frame is captured twice. `verify-capture` must pass (LES-001: a green mirror op proves nothing;
only a sentinel that arrived does).

### 3. Protocol-aware probes from the client — the row-5 pass evidence

All six from `probe-client` (`10.66.20.11`). Every one targets a literal IP; nothing here depends on
the guest resolving a name.

```sh
# a. DNS -- an OBSERVED qname the recipe's zone is authoritative for
dig @10.66.10.11 axdc04.corp.axr.lab A +noall +comments +answer
#    PASS: "status: NOERROR", "ANSWER: 1", and the answer line
#          axdc04.corp.axr.lab. 300 IN A 10.66.10.11
#    (the zone maps every name to the resolver's own address -- that is the recipe, not a bug)

# b. HTTPS -- a REAL TLS handshake against the recipe's self-signed cert
curl -sk -o /dev/null -w '%{http_code}\n' \
     --resolve web-tier-10-80-72-core.corp.axr.lab:443:10.66.10.12 \
     https://web-tier-10-80-72-core.corp.axr.lab/
#    PASS: 200   (a handshake failure prints nothing and exits non-zero)

# c. NTP
ntpdate -q -u 10.66.10.13
#    PASS: a line naming the server with a stratum and an offset.
#    [run 2026-09-06] the observed stratum is 3, not the recipe's orphan `local
#    stratum 10`: chrony synchronizes upstream through the router's NAT, so it
#    serves a real stratum. Any stratum plus an offset is a pass.

# d. LDAP rootDSE from the DC
ldapsearch -x -H ldap://10.66.10.21 -s base -b '' namingContexts
#    PASS: "result: 0 Success" and namingContexts: DC=corp,DC=axr,DC=lab

# e. SMB -- an anonymous session, NOT a share list  [corrected, run 2026-09-06]
smbclient -L //10.66.10.21 -N
#    PASS: the anonymous session is ACCEPTED (no NT_STATUS_* error) and the share
#          table comes back EMPTY -- Server 2022 denies anonymous enumeration by
#          default, so "a share list including NETLOGON and SYSVOL" was written
#          against the protocol, not against the server's posture (LES-244).
#    The stronger, authenticated check, if you want the share names:
smbclient -L //10.66.10.21 -U 'CORP\alice%Axr4nge-Lab!'
#    PASS: NETLOGON and SYSVOL listed.

# f. Kerberos -- a real TGT. krb5-user has no realm config on a fresh host, so
#    write a minimal one first (literal KDC address, no SRV lookup -- the twin's
#    resolver serves no SRV records and the client does not point at the DC):
sudo tee /etc/krb5.conf >/dev/null <<'KRB5'
[libdefaults]
  default_realm = CORP.AXR.LAB
  dns_lookup_kdc = false
  dns_lookup_realm = false
[realms]
  CORP.AXR.LAB = {
    kdc = 10.66.10.21
    admin_server = 10.66.10.21
  }
KRB5
#    [run 2026-09-06] this file write is DISCLOSED, prescribed probe setup -- and it
#    is also a finding: no automated consumer can obtain a ticket until realm config
#    becomes twin content (GAP-074). A `drive: command` persona can do dns/http/ntp/
#    icmp unattended and CANNOT do Kerberos for exactly this reason.
kinit alice@CORP.AXR.LAB      # password: Axr4nge-Lab!
#    (no tty, e.g. over `qm guest exec`?  echo 'Axr4nge-Lab!' | kinit alice@CORP.AXR.LAB)
klist
#    PASS: klist shows a krbtgt/CORP.AXR.LAB@CORP.AXR.LAB ticket
#    NOTE a KRB_AP_ERR_SKEW here is CLOCK SKEW between client and DC, not a broken
#    KDC (LES-194) -- sync the client to the DC and retry.
```

Also worth one line, since the member is the only thing proving the join:

```powershell
# on win-member
Get-CimInstance Win32_ComputerSystem | Select-Object PartOfDomain, Domain
#    PASS: PartOfDomain = True, Domain = corp.axr.lab
```

### 4. At the sensor — the half that cannot be faked in-guest

Read `/var/log/suricata/eve.json` on the standing `o2-suricata` (its vmid is in `axrange o2 status`;
its capture interface is `gre-cap`):

```sh
# from the PVE node, over the guest agent (substitute the sensor's vmid).
# Outer quotes single, inner double -- no escaping games:
qm guest exec <sensor-vmid> --timeout 60 -- /bin/sh -c \
  'tail -n 5000 /var/log/suricata/eve.json | sed -n "s/.*\"event_type\":\"\([a-z0-9_]*\)\".*/\1/p" | sort | uniq -c | sort -rn'
```

**PASS:** `dns`, `tls`, `smb` and `krb5` appear as `event_type`s for the flows step 3 produced.
**FAIL:** those flows show up only as bare `tcp`/`udp` — that is the exact symptom this work order
exists to remove.

> **[run 2026-09-06] What this actually returned**, on a dedicated Suricata 7.0.10 sensor:
> `alert 49, dns 30, flow 24, smb 15, krb5 5, dcerpc 3`. **DNS / Kerberos / SMB passed on their own
> event types.** **NTP passed via the flow fallback** — there is no `ntp` event because the sensor
> render's `eve-log.types` list omits it (**GAP-077**, a one-line fix). **TLS passed on substance
> but not on records:** nine flows carry `app_proto: tls` on 443 — definitively not bare `tcp` —
> while **zero `tls` event records** were written, although `tls` is enabled with `extended: yes`
> (**GAP-076**; the record that is missing is where SNI, subject and JA3 live, so it also blocks S4).
> **LDAP** came back `app_proto=failed`: unclassified by 7.0.10, as the caveat below predicts —
> reported, not scored. **DCERPC** classified on 135 and on a dynamic port, unasked for.
> **The FAIL condition never occurred on any classifiable flow.**

Two version caveats, both honest rather than convenient — the shipped sensor image bakes Suricata
from **bookworm-backports (7.x)**:

* **`ldap` is a Suricata 8.0 event type.** On 7.x the LDAP flow will not produce one. Check the flow
  record instead: `"event_type":"flow"` with `"app_proto":"ldap"`, and if even that is absent, record
  LDAP as *not classified by this sensor version* — do not score it as a filler-traffic failure.
* **`ntp`** may or may not be in the sensor's `eve-log.types:` list. Same fallback: `app_proto` on
  the flow record.

To pin a single flow rather than a histogram:

```sh
# every eve record naming the DC (Kerberos/LDAP/SMB all terminate there):
qm guest exec <sensor-vmid> --timeout 60 -- /bin/sh -c \
  'grep -c "10.66.10.21" /var/log/suricata/eve.json'
```

### 5. `tfc deploy` — the same protocols, unattended, from the persona's cadence

```sh
# step 0b: no AXRANGE_PERSONAS_DIR needed -- a tier file inside twins/svc-verify/ puts its personas/ on the path
$AX tfc deploy twins/svc-verify/tfc/default.yaml --target svc-verify $DB
#   -> prints the tfc deploy id. `tfc status --target` wants the TWIN's deploy id, not the
#      config name, so use the printed id here too:
$AX tfc status <tfc-deploy-id> $DB
```

**Expect:** one assignment, `role:probe-client -> persona:svc-verify-probe`, one instance on
`probe-client`. The rendered in-guest client fires **every 60 s**, two invocations per emit, with
these exact argv (peer addresses baked in by the orchestrator — the guest never resolves anything):

```
dig @10.66.10.11 -p 53 -r +tries=1 +time=2 +nosearch +noall +answer axdc04.corp.axr.lab. A
curl -q -sS -k --noproxy * -o /dev/null --max-time 5 https://10.66.10.12:443/
ntpdate -q -u -p 1 -t 2 10.66.10.13
ping -n -c 1 -W 2 -s 256 10.66.10.12
```

In-guest evidence: the transient unit is `systemd-run`-launched (never `nohup`, LES-079/LES-086) and
the client logs under `/var/log/axrange/`; a non-zero `fails=` counter or a `MISSING TOOL` line means
a client binary is absent. **Read those counters even on a green run** — they are the positive
control that separates "the persona did 32 things" from "the persona did nothing and nothing
complained" (LES-245). Then **re-run step 4** with no operator traffic at all: the same
`event_type`s must keep appearing.

> **[run 2026-09-06]** `tfc deploy` completed in **6 s**; the persona logged **32 invocations across
> four cycles with `fails=0 missing_tool=0`**. In a **278 s window with no operator probe at all**,
> the sensor recorded 28 flows / 16 dns / 16 alerts from `probe-client` — `app_proto` **tls 10,
> ntp 10, dns 8**, plus ICMP. All four emits arrive classified, from the persona's own cadence.

### 6. `te destroy` → clean; the standing twin and every `o2`/`img` resource untouched

```sh
# ALWAYS the explicit deploy id `te deploy` printed -- never the bare default.
$AX te destroy svc-verify-<deploy-hash> --allow-destroy $DB   # cascades exr -> tfc -> tap -> te
$AX te destroy svc-verify-<deploy-hash> --allow-destroy $DB   # idempotent: a clean no-op
```

> [!CAUTION]
> **`te destroy` takes a literal deploy id — it does not resolve a scenario/twin-config name**
> (`cmd_destroy` -> `store.get_deploy(...)`; a name prints `deploy not found`). A **bare**
> `te destroy` targets the newest non-destroyed `te` deploy in the state DB, and on `dev45` that
> database also holds the standing `corpapcn-twin` / `corp-enterprise` twins. Pass the id.
> (`list-deploys --tier te` prints it if you lost it.)

**Expect:** `Result: DESTROYED`. The cascade tears the bound `tfc` and `tap` deploys down **before**
the twin's own sweep (LES-171), so the persona is stopped and the mirrors de-scoped while their VMs
still exist.

> **[run 2026-09-06]** tfc (persona stopped), tap (5 mirror de-scopes, gretap removed), te
> (**17 resources, 0 not cleared, 54 s**). Cluster afterwards: **152 VMs** (the 151 baseline plus
> the dedicated scratch sensor, which is destroyed separately by the operator), the standing twin's
> **138 untouched**, gretaps back to **26**, no leftover tap/mirror/zone of the throwaway, and
> `img` / `o2` / `XPress-SW*` / `Tcpdump-SW-A` all untouched. **If you stood up a scratch surface**
> (the recipe in "Which state DB"), destroy it too — that command is the operator's:
> `axrange o2 destroy <o2-deploy-id> --allow-destroy --adapter-config adapter.conf --db "$SCRATCH_DB"`.
> Verify its blast radius from its own op rows first: it should name only its zone, vnet, subnet and
> sensor VM, and no standing object.

Then confirm the blast radius, which is the point of the last step:

```sh
$AX list-deploys $DB                  # svc-verify rows destroyed; corpapcn-twin-0e4f7d58 untouched
$AX o2 status --db <the surface's DB> # the sensor still standing, still capturing
qm list | grep -E 'img-|o2-'          # golden templates + surface VMs present and unchanged
```

> [!IMPORTANT]
> **Take the BEFORE census from the store or the cluster, not from `list-deploys`' default view**
> (LES-249): it prints the newest **20** rows, so a baseline captured with it silently excludes
> everything older and the after-comparison compares two windows instead of two populations. Use
> `qm list | wc -l` for the VM count and record the absolute number before you deploy.

`te destroy` structurally cannot touch an `o2`/`img` resource (`planner/teardown.py`'s `_protected`
guard refuses and records a `guard-violation`), and the external sinks are protected the same way —
this step confirms it rather than assuming it.

---

## What this twin does **not** prove

* **Nothing about the observed protocol CONTENT (S4).** The bind9 zone carries observed *names*;
  TLS SNI, HTTP vhosts and SMB shares have no IR carrier yet (aximprint GAP-019), so the nginx vhost
  name is the compiler's, not the capture's.
* **Nothing about scale.** Seven VMs. The fresh cast's own gated deploy (spec 13 §7, the first
  golden) is the 138-VM-class exercise; this is the "prove the recipes first" step §9.2's own
  blockquote asks for.
* **Row 7 (security) still cannot pass.** These hosts answer from behind a permissive
  default-forward router — spec 13 §3.2's own limit, untouched here.
* **The client tool set is installed at run time, not baked** (GAP-056 stays open). Legitimate only
  because this twin is a throwaway: the same `install:` list on a *standing* twin trips `--update`'s
  mutation refusal, which is the whole reason the gap exists.
* **`svc-ntp` also runs bind9.** There is no chrony-only recipe among aximprint's fourteen (no source
  host was seen serving 123 and nothing else), and the recipes were copied whole rather than cut
  down. The redundancy is free; a hand-cut recipe would not have been the real one.
* **Nothing about a REPAIRED standing sensor.** The 2026-09-06 run used a **dedicated scratch
  surface**, because the standing `o2-suricata`'s Suricata had been dead since 2026-07-27
  (GAP-067) and its deploy row lives in a different state DB (GAP-069). A re-run against a repaired
  standing sensor is a different, and untested, path.
* **Nothing about TLS record content.** Row 5 passed TLS on `app_proto`, not on a `tls` event
  record — Suricata wrote none (GAP-076). Any claim about SNI, certificate subject or JA3 needs
  that gap closed first.
* **The bind9 hosts forward to the `gw` router, which is not a resolver.** Names *inside*
  `corp.axr.lab` resolve (that is what the probes use); anything else SERVFAILs. No twin host points
  at them — cloud-init gives every Linux guest `1.1.1.1` through the router's NAT, and the domain
  join points the Windows member at the DC.
