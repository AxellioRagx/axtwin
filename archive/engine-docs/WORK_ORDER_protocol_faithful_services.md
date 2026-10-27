# Work Order — Protocol-faithful services: real servers on the twin, configured from observed data

> **Status:** **BUILT 2026-09-05** — **S1** (the declared listen set), **S2** producer-side (the
> `serve-*.sh` real-daemon recipes, aximprint), **S3** both halves' **authoring** proven (the
> `ad-cast` fixture here, the cast's DC authoring there — planned and validated, **not deployed**)
> and **S5** (the honest-gap register, GAP-061..064). **S4 is NOT built** — its observed protocol
> content is gated on aximprint's `observations` carrier for TLS/HTTP/SMB (aximprint GAP-019, a
> decision queued for the user), and now also on **GAP-076** (no `tls` eve records ⇒ no SNI
> evidence channel). **§9.2's gated `dev45` live-verify RAN and PASSED 2026-09-05/06** on the
> purpose-built `twin-configs/svc-verify/` twin — all six steps, real daemons answering real
> clients, the sensor naming the protocol, twelve gaps opened (GAP-067..GAP-078). Result: **§9.2.1**.
> Built as: **§10.2**. CHANGELOG 2026-09-05 "Protocol-faithful services BUILT (row 5's server half,
> both repos)" and 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED".
> Drafted 2026-09-04. Authority: **implementation (Claude Code)** for this
> draft; the ruling it serves is **Authority: user, 2026-09-03** —
> `docs/design/13-golden-twin-fidelity.md` §8.2, verbatim: *"implement realistic protocol clients
> and servers based on the observed data. avoid using synthetic data generation where possible."*
> Row 5 (protocol, behavioral) **gates the first golden** (§3.3), so this is not optional polish.
> **This is the SERVER half of row 5.** The client half —
> `docs/WORK_ORDER_protocol_faithful_traffic.md`, the `drive: command` driver — is **BUILT
> (2026-09-02)** and left closed as the record; this is its sibling, not an edit to it. The two meet
> on the wire: a real `dig` needs a real resolver on the other end.
> **Version-locked, as built:** the one proposed touch was **ruled D2 (b) by the user** and landed —
> the already-present-but-inert `App.listen` field (`axrange/ir/schema.py` `class Listen`;
> `docs/IR_SCHEMA.md` §4's `apps[]` table) has its first consumer. **Nothing moved**: the field's
> shape and optionality are unchanged and no shipped content set it before, so every pre-existing IR
> hashes byte-identically. No new op type (**40** stays 40); no
> `PLANNER`/`ADAPTER`/`OP_CATALOG`/`IMAGE_MANIFEST` change; no `scenario_schema_version` or
> state-store bump; **`VALIDATOR` stays 114** — D2 added no `listen:`-shape rule, because shape
> enforcement lives at the **twin-config** layer (`twinconfig.expand._check_listen`), where the
> author writes it and where a refusal can name `twin_config.nodes[i].listen[j].<field>`.
> **Spec:** `docs/design/13-golden-twin-fidelity.md` §3.1 row 5, §3.3, §5.6, §7, §8.2 · this file.
> **Meets in parallel:** `docs/WORK_ORDER_fidelity_scorecard.md` (drafted concurrently; §6.3 names
> the seam overlaps to reconcile — **do not edit that file from here**).
> **Companion (aximprint, NOT this repo):** the cast must *emit* what §6 specifies; the internet
> sink's own service set is aximprint's call (§7.4).
> **Route:** Pipeline (`/dev`) if accepted — code-writer → tester → code-reviewer → doc-writer →
> **gated `dev45` live-verify** (mandatory, §9.2). **Never against the standing
> `te corpapcn-twin-0e4f7d58`**: it is do-NOT-destroy, it is scorecard run 1's subject, and touching
> it would trip `--update`'s mutation refusal anyway (§7.2).

## 1. The problem, measured

Row 5 passes when *"each observed (host, port, protocol) returns a protocol-valid response to a
protocol-aware probe."* Today most of the twin returns nothing, and where it answers it echoes.

From the committed export `twin-configs-cast/corpapcn-twin/corpapcn-twin.topology.json`
(`schema_version` 1.3, 786 entities): **190** local (twin-placed) entities, of which **16** are
observed listening on anything at all; 349 of 596 non-local ones listen. The cast deploys **138 VMs
/ 62 node groups / 27 segments**. So the server half is **not** 138 servers — it is **16 twin hosts
plus the internet sink**, and that is what makes it tractable.

What the cast (`corpapcn-twin.yaml`) actually provisions:

- **`domain-controller` ×1** (`windows-server-2022@v1`) — **no `install:` key at all**; it answers
  nothing on any of its 12 observed ports. **`corp-workstation` ×6** (`win11-finance@v4`) — same
  silence: Windows nodes are skipped outright because the responder is a POSIX heredoc that dies as
  PowerShell (`synth/responder.py:132 is_windows_image`). aximprint's own
  `docs/WORK_ORDER_windows_responder.md` §§1-2 (diagnosis stands; only its listener fix is
  superseded) measures the cost — *"A silent host is not merely unmeasured; it is measured as
  something else"*: **11 local edge keys are permanently unreachable, roughly a third of the
  source's local edge mass**, and each dead port draws an ICMP unreachable that Zeek scores as a
  *fabricated reverse local edge* (220 rows from 228 dead-port emits, iteration 17).
- **`dns-resolver` ×4** — real `bind9` **plus** an echo on `53/udp 123/udp 389/tcp 389/udp`; `named`
  wins :53, so DNS is genuinely real there and 123/389 are echoes.
- **`web-tier` ×2** — `{pkg: nginx}` only (stock defaults: port 80); `responder-web-tier.sh` is on
  disk but **referenced by no node**, so 443 and 22 are unserved. **`monitoring-collector` ×1** —
  `{pkg: prometheus}` + an echo on 9997/tcp. **`internet-sink-1/2/4/8` ×15** — echo only.
  **`generic-endpoint` ×102, `network-device` ×4, `log-shipper`, `hypervisor`, `gw`** — nothing.

Every echo is `scripts/responder-*.sh` writing one `/usr/local/lib/axr/responder.py` that binds a
port list and reflects bytes — precisely the "synthetic filler" §8.2's ruling names, and it defeats a
protocol-aware probe by construction: `dig` gets its own query back, `curl -sk https://` fails the
handshake.

## 2. The observed listen set — the whole inventory

From `nodes[].listen_ports` where `locality == "local"`. `role` is `evidence_class: imputed`
throughout (inferred from behaviour); **the ports themselves are observed**. This table is the scope.

| host | inferred role | observed listen set |
|---|---|---|
| `ent-0008` (entity key **`hostname:axdc01`**) | domain-controller | 53/tcp+udp, 88/tcp+udp, 123/udp, 135/tcp, 389/tcp+udp, 445/tcp, 3268/tcp, 49669/tcp, 49682/tcp |
| `10.80.20.5` | *dns-resolver* (mis-inferred) | 53/tcp+udp, 67/udp, 88/tcp, 135/tcp, 389/tcp+udp, 445/tcp, 49667/tcp |
| `10.20.196.1` | dns-resolver | 53/udp, 123/udp |
| `10.21.161.1`, `10.80.64.2` | dns-resolver | 53/udp |
| `10.80.72.247` / `10.80.75.171` | web-tier | 443/tcp / 80/tcp |
| `10.80.76.108` | hypervisor | 8006/tcp |
| `10.20.194.240` | monitoring-collector | 9997/tcp |
| `ent-0005`, `ent-0006`, `10.80.20.191`, `10.80.72.30` | corp-workstation | 135/tcp, 137/udp, 3389/tcp+udp, 7680/tcp |
| `ent-0007`, `192.168.0.160` | generic-endpoint | 135/tcp, 137/udp, 55390/tcp, 63153/tcp |
| `10.80.34.10` | log-shipper | 57086/tcp |

**Three readings, each of which changes the build.**

1. **`ent-0008` is a textbook Windows DC profile, and axRange already reproduces all twelve ports
   with machinery that is built and live-verified.** `install: [{role: adds}]` +
   `provides: [{domain: …}]` promotes a `windows-server-2022@v1` node to the first DC of a forest
   (`axrange/config_exec/backend.py:298` onward, `_promote_dc_windows`, reboot-tolerant;
   `docs/SLICE_WINDOWS_AD.md`), and AD DS brings DNS, the KDC, LDAP + CLDAP, SMB, the Global
   Catalog, the RPC endpoint mapper and its dynamic ports, plus W32Time on 123, **as a consequence
   of being a DC** — not as twelve provisioning steps. The authoring cost is two lines
   (`twin-configs/corp-enterprise/corp-enterprise.yaml:51-52` is the shipped precedent).
2. **`10.80.20.5` is a second DC that the catalog labelled `dns-resolver`.** 88 + 389 + 445 + 135 +
   a dynamic RPC port is not a resolver, and the belief store agrees: `ir_identity` binds
   `axdc04.axellio.dom` to that address, and the wire names two DCs (`axdc01`, `axdc04`). Serving it
   as bind9 reproduces 6 of its 9 ports and none of its Kerberos — Decision **D5**.
3. **The echo responder's port list is the wrong input.**
   `synth/responder.py:112 listener_ports(personas)` derives each role's bound set from *every
   `(port, transport)` some persona **dials** at that role*, not from the observed listen census.
   That is why `internet-sink-1` binds 88/tcp and 135/tcp while the `web-tier` nodes bind nothing.
   **The server half must be driven by the observed listen set** (this table), with the dial set as
   a cross-check only — otherwise a host serves what it is asked for rather than what it was.

## 3. What "configured from the observed data" means

The ruling has two halves pulling the same way: *real servers*, and *not synthetic data*. The line:

> **Names and structure come from observation. Bytes and secrets never do.**

A response body, a TLS key, a password — synthetic always (spec §9 puts payload content out of
scope; §5's *"inject posture, never secrets"* is absolute). What must stop being synthetic is the
**protocol** and the **names it carries**, because that is what a detection rule matches.

Already exported and usable now: the per-host **listen set** (`nodes[].listen_ports`) and the
per-edge **protocol actually spoken** (`edges[].proto` — `dns`, `ssl`, `ntp`, `http`, `ssh`, `krb`,
`ldap_tcp/udp`, `dce_rpc`, `gssapi,smb,krb`, `ntlm,dce_rpc`, `dhcp`, `quic,ssl`). In the belief store
but **not exported**: 158 distinct DNS query names and the Kerberos realm plus a small principal set
(§5). On the wire but **partly dropped at ingest**: SPNs, SMB share paths, LDAP base DNs, TLS SNI,
HTTP `Host`/URI (§5.2). §6 is the seam that closes the gap; §4's last column is the fallback where
no observation exists.

## 4. Protocol inventory → server

| observed ports | server | delivery | fallback when nothing was observed (always reported `imputed`) | slice |
|---|---|---|---|---|
| 88, 389, 445, 3268, 135+dyn, 53, 123 | **a real AD DC** (`install: [{role: adds}]`) | Windows, `provision-app` → `_promote_dc_windows` over QGA (built, live-verified) | realm → lab default `corp.axr.lab`; users → lab defaults | **S3** |
| 3389/tcp+udp, 135, 137/udp | Windows built-ins on a **domain-joined** `win11-finance` member (`joins:`) | `join-host-to-domain` (built) + one `run-script` to enable RDP | — | **S3** |
| 53/udp+tcp | **bind9**, authoritative + forwarder | Linux `{pkg: bind9}` + `{script: …}` | authoritative for the twin's own domain only; **never invent corporate-looking names** | **S2** |
| 80, 443/tcp | **nginx** (+ self-signed cert) | Linux `{pkg: nginx}` + `{script: …}` | one default vhost; bodies synthetic, sized to the observed flow where one exists | **S2/S4** |
| 123/udp | **chrony** (`allow`) | Linux `{pkg: chrony}` | local reference clock, stratum imputed | **S2** |
| 22/tcp | **sshd** — already running on every Debian/Ubuntu cloud image | assert only | — | **S2** |
| 67/udp | **isc-dhcp-server** | Linux | scope from the segment CIDR (already exported) | **S2** |
| 8006/tcp | **nginx TLS on 8006** — protocol-valid HTTPS, *not* Proxmox | Linux | — | **S2**, D6 |
| 9997/tcp | **no free real server** (Splunk's forwarder protocol is proprietary) | — | honest gap | D6 |
| 443/udp (QUIC) | **no cheap real server** without an HTTP/3 bake | — | honest gap | D7 |
| 49667/49669/49682, 55390, 57086, 63153, 39563, 7680 | **not services** — ephemeral sockets and Windows Delivery Optimization | — | **observed-but-not-reproduced**; report in that state, never fake a listener | — |

**Every Linux entry is a package in Debian main.** LES-024 ("bake fragile / external-repo tools;
don't apt-install in the isolated guest") targets external repos and fragile tooling; these are
neither, and the standing twin already installs bind9, nginx and prometheus at runtime — reliably,
since the 2026-08-14 fix that makes `render_playbook` refresh a cloned guest's stale apt index first
(`axrange/config_exec/render.py:647`). See **D3**.

**The package choice is largely already made, on the aximprint side.** Every
`/home/rocky/aximprint/src/aximprint/catalog/roles.yaml` row carries an `install:` list
(`dns-resolver → bind9`, `web-tier → nginx`, `file-server → samba`, `mail-server → postfix`,
`egress-proxy → squid`, `db-tier → postgresql`, `monitoring-collector → prometheus`), and
`domain-controller` is the one row whose `install:` is `[]` — the reverted `{role: adds}` (§5.1). So
**the missing half is configuration, not package selection**: packages install and then serve their
stock defaults, which is exactly why nginx on a `web-tier` node answers 80 and nothing else. Two
catalog facts worth handing back: `file-server` (445/tcp, `{pkg: samba}`, 2 source hosts) produces
**no node in the twin at all**, and `dns-resolver`/`hypervisor` carry `persona: null`.

## 5. Wire identity: what a real DC can actually be given today

Spec §5.6 names *"wire identity, strengthened"* as the source that lets a real DC exist **before**
the LDIF reference ingest — which §7 sequences *after* the first golden and which therefore must not
become a dependency. It is feasible, and further along than the aximprint repo's own comments claim.

### 5.1 A premise this build depends on is wrong, and should be corrected there

`roles.yaml:80-94` (the GAP-C8 anchor) and `docs/JOURNAL_corpapcn_fidelity.md:693-702` both justify
reverting `{role: adds}` the same way: axRange pairs `role: adds` with `provides: [{domain: <fqdn>}]`,
and *"nothing in a capture supplies an AD forest name — `ir_identity` on the CorpAPCN run carries
only external DNS (`apple.com`, `meraki.net`)."*

**That is false against every IR snapshot on disk today.** Read-only against
`/home/rocky/twin-fidelity-corpapcn-2026-08-31/capcn-ir6.duckdb` (reproduced in
`src-ir-rematerialized-20260902.duckdb` and `t-ir.duckdb`), `ir_identity` holds 730 rows,
`evidence_class = observed` on every non-DNS one:

| kind | rows | distinct | yields |
|---|---|---|---|
| `kerberos` | 10 | 4 | **the realm `AXELLIO.DOM`** (inside the identifier); 2 user principals (`matthew.admin`, `Guest`); 2 machine accounts (`BW-L$`, `desktop-21n0pq1$`) |
| `ntlm` | 8 | 4 | 4 computer FQDNs, incl. **`axdc01.Axellio.dom`** |
| `dhcp` | 4 | 2 | 1 hostname + 1 MAC |
| `dns` | 708 | **158** | query names; 11 internal, incl. `axdc01.axellio.dom`, `axdc04.axellio.dom` |

Three independent stems carry the forest name: `kerberos.log` (`BW-L$/AXELLIO.DOM` →
`krbtgt/AXELLIO.DOM`), `ntlm.log` (`domainname=Axellio.dom`), and `ldap_search.log`'s CLDAP netlogon
filter (`(&(DnsDomain=Axellio.dom)(NtVer=0x00000014))`). Whether the claim held when written I cannot
establish; it does not now. **Follow-up for aximprint (not this build):** correct `roles.yaml:80-94`,
the journal's GAP-C8 paragraph, and the two exception strings at
`scripts/check_role_fixed_point.py:72-75`.

### 5.2 On the wire but not in the store — three precise aximprint changes

`ingest/zeek/plugin.py:305`'s `_kerberos()` reads `ts`/`client`/`id.orig_h` and **drops the
`service` column** — which carries `ldap/axdc01.axellio.dom`, the cleanest single statement in the
capture that `axdc01` is the domain's LDAP server, plus `krbtgt/AXELLIO.DOM`.
`windowing/entities.py:213`'s `kerberos_identifier()` takes `components = parts[:-1]` (*"the LAST
component is the realm"*), so `BW-L$/AXELLIO.DOM` survives as `bw-l` and the realm remains only in
`Identifier.raw`, which has no column at the IR boundary — a first-class `realm` fact is a parser +
schema change, **not** a capture problem. And SMB share paths (`smb_mapping.log`'s
`path=\\axdc01.Axellio.dom\IPC$`, collected but absent from `SERVICE_PROJECTIONS["smb"]`) plus LDAP
`base_object`/`filter` (declined by name in `_SERVICE_NOT_PROJECTED`, pending a subject for
name-only entities) are the S4 inputs for a share list and a base DN.

### 5.3 Honest scope

> **Ruled otherwise, 2026-09-04 (D4, Authority: user; §10.1) and built that way 2026-09-05.** The
> paragraph below is the *argument that was put*, kept as the record — it is not what the twin does.
> **The twin's forest is the lab realm `corp.axr.lab`**, `imputed`; the observed `AXELLIO.DOM` is
> **recorded** (the §5.1 finding stands and went back to aximprint) and **not reproduced**. Only the
> realm is replaced: the DC keeps its observed short hostname `axdc01`, which is on the wire in
> every Kerberos, LDAP and SMB frame the twin emits.

The **realm** is genuinely observed and is the most valuable single fact: it makes `axellio.dom`
(not `corp.axr.lab`) the twin's forest, so every Kerberos and LDAP frame on the twin's wire carries
the source's own realm name — precisely what a detection rule reads. **Computer** accounts are
partly observed. The **user population is not**: two principals, one of them `Guest`, and nothing
observed carries passwords, groups, OUs or SIDs — that is the directory-export channel the spec
sequences later. So the DC's users are **lab defaults, marked `imputed`**, in the shape
`twin-configs/corp-enterprise/corp-enterprise.yaml:147` already ships.

## 6. The file-contract extension

The repos connect **only** by files — twin config + provenance sidecar + topology export, copied into
`twin-configs-cast/<id>/` by `/home/rocky/aximprint/scripts/install_cast_as_twin.py`. No imports.

### 6.1 aximprint side — `<id>.topology.json` under a new `bundle_schema` version key (additive) — **BUILT 2026-09-05**

> Carrier corrected 2026-09-04: the export has no version of its own today — it republishes the
> IR's `schema_version` — so "1.3 → 1.4" would either bump the IR or mean nothing. aximprint's
> `docs/WORK_ORDER_reference_ingest.md` claims a `bundle_schema` key as the carrier; the `services[]`
> and `observations` content below stays specified here.
>
> **AS BUILT (2026-09-05), and the sketch below is amended in place rather than rewritten.** The
> carrier is **`bundle_schema: "axr-topology/1.4"`** (`synth/imprint_bundle.BUNDLE_SCHEMA`). Two
> fields in the sketch **do not exist at the producer's IR boundary** and were reported back rather
> than fabricated to satisfy this file:
>
> 1. **~~`"windows_seen": 11`~~ on a `services[]` row — there is no per-listen occupancy, and there
>    is no producer for one.** `ir_entity.listen_ports` is a bare `["<port>/<proto>"]` array; the
>    entity's own `windows_seen` is a **host's** occupancy, not a **port's**. Publishing a host
>    number under a per-listen name would attach a plausible figure to a fact nothing measured
>    (aximprint LES-127). **Consequence, and it lands on this side of the seam:** the reconciliation
>    note below promised row 4 a per-listen occupancy; **row 4's per-listen occupancy therefore has
>    no producer**, and the in-guest `ss -lnH -tu` check stays its only source.
> 2. **DNS rows are `evidence_class: "referenced"`, not ~~`"observed"`~~**, and the sketch's
>    ~~`"server"`~~ / ~~`"qtype"`~~ / ~~`"count"`~~ fields do not exist at the IR boundary either.
>    The capture saw a **name in an answer**, not the host — which is exactly the distinction note
>    14 §2 draws between `observed` and `referenced`, applied here rather than argued.
>
> Three sub-blocks are **absent**, for two different reasons. `identity.realm` and `identity.spns`
> need aximprint's reference-ingest S5 (the realm survives only inside `Identifier.raw`; the
> Kerberos parser drops the `service` column — §5.2). **`tls.sni` / `http.vhosts` / `smb.shares` are
> one carrier short, and the facts already exist**: the Zeek parsers project all three into the
> belief store (`SERVICE_PROJECTIONS`), measured on the CorpAPCN store as real content — 8 distinct
> `serves_ssl` names, and `serves_http` binding `test-api.swlab.axellio.dom` to the twin's own
> `10.80.75.171` — but `ir/materialize.py` reads only the `listens_on`/`dials_port`/`in_segment`/
> `has_role` predicates, so `serves_*` never crosses into a PIT. Adding a carrier (recommendation: a
> new `ir_service` table) is an **additive IR minor bump** on aximprint's one ratified schema, i.e. a
> **decision, not an implementation call** — **aximprint GAP-019, queued for the user**, and it is
> what **S4** is gated on.

```jsonc
// bundle_schema: "axr-topology/1.4"   <- the carrier, as built
// per node, alongside listen_ports/dial_ports:
"services": [{"port": 88, "proto": "tcp", "app": "krb", "evidence_class": "observed",
              "app_evidence_class": "observed"}],   // NO per-listen `windows_seen` -- see above
// new top level -- the protocol CONTENT the servers are configured from:
"observations": {
  "dns":      {"names": [{"name": "axdc04.axellio.dom", "evidence_class": "referenced"}]},
  "identity": {"principals": [{"name": "axdc01", "kind": "computer",
                               "fqdn": "axdc01.axellio.dom"}]}
  // ABSENT as built, each for a stated reason:
  //   identity.realm, identity.spns   -- aximprint reference-ingest S5 (parser + schema)
  //   tls.sni, http.vhosts, smb.shares -- one IR carrier short (aximprint GAP-019); S4 waits here
}
```

`services[]` is `listen_ports` joined to the protocol actually spoken — what lets a consumer pick
*bind9* rather than *"something on 53"*. **Reconciled 2026-09-04: this block is the single specifier
of `services[]`; the scorecard (`docs/WORK_ORDER_fidelity_scorecard.md` §8.5) consumes it for rows 4
and 5 instead of re-deriving protocol from `edges[].proto`, and ~~`windows_seen` is what gives row 4
a per-listen occupancy~~** — struck 2026-09-05: no such field exists or can, per correction (1)
above. `observations.dns` and the `principals` half are a straight
projection of `ir_identity`, which already holds them (§5.1) — **no new parsing**. `realm`, `spns`
and `smb.shares` need §5.2's changes, so they are S4 inputs, not slice-1 ones. **As built, the app
token in `services[]` is spelled in the SENSOR's vocabulary** (`krb`, `ssl`, `ldap_tcp`), never the
persona schema's, so a consumer joining it against `edges[].proto` joins to something.

**Also missing on this seam:** `twin-configs-cast/corpapcn-twin/` contains **no `*.provenance.json`
at all**, though `corpapcn-twin.yaml:5-6` refers to one and the spec names it as one of the three
contract files. The scorecard reads it; whoever touches the seam first should make
`install_cast_as_twin.py` copy it. **Closed 2026-09-05 by aximprint's reference-ingest S0** (its
CHANGELOG 2026-09-05: "the installer copies the sidecar", `schema: axr-cast-provenance/1`) — not by
this build, but by the sibling one that landed the same day; a freshly installed cast now carries
it, and the standing `twin-configs-cast/corpapcn-twin/` predates that and still does not.

### 6.2 axRange side — what the cast writes (Decision **D2**) — **ruled (b), BUILT 2026-09-05**

**(a) script-only, zero schema change.** aximprint keeps synthesizing one `scripts/serve-<role>.sh`
per role, but the script now installs *and configures* a real daemon (writes `/etc/bind/db.<zone>`,
an nginx vhost, `chrony.conf`) instead of binding an echo; the twin config changes only in that
`{script: scripts/responder-*.sh}` becomes `{script: scripts/serve-*.sh}` beside its `{pkg: …}`
entries. **This is the only mechanism `--reapply` can update on a standing twin** (§7.2).

**(b) (a) plus a declared listen set.** Additionally wire the **already-present, entirely inert**
`App.listen` (`axrange/ir/schema.py:184`+`:193`; `docs/IR_SCHEMA.md:362` calls it *"informational for
network policy generation"*; **zero consumers** in `axrange/` and **zero shipped content sets it**,
so wiring it is behaviourally free), plus a `listen:` passthrough on `TwinConfigNode`
(`axrange/twinconfig/schema.py` is `extra="forbid"` — a code change, not a version-locked one). The
twin then *declares* what it serves, `axrange te inventory` publishes it, and the scorecard probes a
declaration instead of parsing a shell script.

### 6.3 Overlaps to reconcile with the scorecard work order

Flagged for the main session rather than pre-decided: (1) the **observed** listen set (§6.1) — both
read it, one should specify it; (2) the **declared** listen set (§6.2b) — if the scorecard proposes
its own carrier, take one; (3) **the probe** — row 5's rule is a protocol-aware probe and the probe
belongs to the scorecard; §9.2 uses the same tools and must not grow a second, drifting
implementation (LES-200, LES-205). **Reconciled 2026-09-04:** the probe is implemented once, in the
scorecard's `axrange/fidelity/probe.py` + `runner.py`, and its protocol set follows the observed
listen set — dns, http, https, ntp **and** krb (AS-REQ → AS-REP/KRB-ERROR), ldap (rootDSE search),
smb (SMB2 NEGOTIATE), rdp (X.224 connection request), all stdlib-only renders — plus a bare
`connect` that is labelled as not protocol-aware and can never count as protocol-valid. §9.2 step
3's tool commands are the operator's cross-check; the automated row-5 evidence is that probe.

## 7. Composition with the standing machinery

**7.1 Fresh cast first — the spec's own sequence.** §7 puts the fresh cast after scorecard run 1, and
§8.2 rules that the standing twin *"is destroyed when the fresh cast deploys."* Design for a fresh
`te deploy`; treat incremental application as secondary.

**7.2 On a standing twin, exactly one of these applies — and it shapes the design.** Adding a *node*
works (`te deploy --update`, built 2026-08-11, live-verified 2026-08-14). Adding `{pkg: bind9}` to an
*existing* node does not: it mutates `provision-app`'s persisted `install_actions`, `plandiff`
classifies the op **Mutated**, and `--update` refuses by design
(`docs/WORK_ORDER_twin_incremental_update.md` §3.1). Changing a `run-script`'s **content** does:
`--update --reapply` (CHANGELOG 2026-09-02). **Consequence, and the reason §6.2(a) is recommended:**
put daemon *configuration* in the script and *packages* in `pkg:`/the image, and re-deriving a zone
file or a vhost from a fresh imprint becomes a supported `--reapply`, while adding a brand-new
protocol to a host stays honestly a redeploy. LES-096 applies to the live-verify: an
already-`completed` op never sees a new input, so proving anything new needs `--fresh` or a new twin.

**7.3 Image bake vs. runtime install.** `docs/IMAGE_BUILD.md` §5.1 puts the recipe in the binding's
`build.provision:` (`deploy-dev45/images/bindings/proxmox/debian-12-cloud.yaml`), and
`axrange/imagebuild/bake.py:29` accepts `pkg`, `pkg@version` and inline `script` there — so a bake
can install *and* pre-configure. Two distinct cases: **client tools** (`dnsutils`, `curl`,
`ntpdate`), where GAP-056 is open and is why the client half's live-verify is still PENDING; and
**server packages**, which work at runtime today. Decision **D3**.

**7.4 The internet sink is a boundary, not a scope item.** The 15 `internet-sink-*` hosts (buckets
1+2+4+8, on their own `10.200.254.0/24` segment) stand in for 349 externally-observed listeners — 191
on 443/tcp, 105 on 123/udp. Their scripts are synthesized by aximprint
(`synth/compiler.py`/`synth/responder.py`), and the power-of-two buckets exist because `peer_scope`
yields either *one* peer or *all* of them and never a distribution — cheap only while a sink host
runs one echo process on `cpu: 1 / ram: 1Gi`. **axRange ships the reference recipes S2 produces and
nothing more.** Note that the sink is *already* a role-inference participant, so giving it real
services will move role inference deliberately. GAP-048 is unaffected: the sink is reached by the
`internet-sink` **role**, not by the reserved `internet`/`egress` tokens.

## 8. Build slices

**S1 — the seam (small). — BUILT 2026-09-05.** Consume §6.1's `services[]` and, under D2b, carry a declared listen set
from twin config → IR → `axrange te inventory` / `GET /api/deploys/{id}/inventory`. Nothing installs
yet. *Acceptance:* a cast carrying `services[]` expands, validates and plans unchanged; the published
inventory shows each host's declared set; **every shipped `twin-configs/` config keeps a
byte-identical `plan_id` and `ir_hash`** (the permanent regression pin — `plan_id` keys VMIDs, VM
names, zone, vnets and finds a standing deploy; LES-126).

**S2 — real Linux servers, driven by the observed listen set (medium). — BUILT 2026-09-05, producer-side (aximprint); NOT yet deployed.** Reference `serve-*.sh`
recipes committed alongside the responders they replace: bind9 (authoritative zone + forwarder),
nginx (80 + 443, generated self-signed cert), chrony (`allow`), isc-dhcp-server, and an *assert* for
sshd. Each fail-loud (`systemd-run --unit=… --collect` + `systemctl is-active`; **never
`nohup … &`** — LES-079/LES-086) and idempotent. Retire the echo from every port a real server now
covers; keep it only as a labelled `imputed` fallback (**D8**), never on a port a real daemon owns —
`responder-dns-resolver.sh`'s `EADDRINUSE` branch is the measured proof of why. Close GAP-056 here if
D3 says bake. *Acceptance:* `ss -lnH -tu` matches the declared set exactly; `dig @host` → NOERROR
with an ANSWER; `curl -sk https://host/` completes a handshake and returns 200; `ntpdate -q host` →
an offset; a second `te deploy` is a clean no-op.

**S3 — a real DC, and Windows members that answer (medium; mostly content). — BUILT 2026-09-05, AUTHORING ONLY; the acceptance below is §9.2's and has NOT run.** The cast authors
`install: [{role: adds}]` + `provides: [{domain: <observed realm, lowercased>}]` on the
`domain-controller` node, an `identity:` block (realm-derived where §5 allows, lab-default and
labelled otherwise), and `joins:` on the `corp-workstation` nodes. axRange-side: prove the
twin-config path end to end (the shipped precedent is `corp-enterprise`, not a cast); one
`run-script` on the members to **enable RDP** (3389 is observed on four hosts and is off by
default); make the DC the twin's resolver so joins resolve; settle **D5**. Budget for GAP-051 (joins
~1-2 min each, effectively serialized) and the promotion reboot. *Acceptance:* the DC answers on
53/88/389/445/3268 and passes §9.2's LDAP/SMB/Kerberos probes; a joined workstation shows
`PartOfDomain=True`; at the sensor, `eve.json` carries `krb5`, `ldap` and `smb` event types.

> **One acceptance criterion that is easy to miss and is the point of the slice.** A DC that
> *answers* is not automatically a DC that is *re-inferred*. `roles.yaml`'s `domain-controller` row
> requires **88/tcp AND 389/tcp together** in the observed listen census, and a listen fact is
> written only from a flow the host **accepted** (`windowing/windows.py:925`, gated on
> `FLOW_ACCEPTED`). So the twin's personas must actually **dial 88/tcp and 389/tcp at the DC**. They
> currently do not: 389/tcp is dialled at `dns-resolver`, because `synth/personas.py:737`'s `serves`
> vote resolved port 389 on a **2-2 tie**, broken by `Counter.most_common`'s insertion order — i.e.
> by DuckDB row order, a determinism hazard in its own right. Fix the tie-break on the aximprint
> side or pin 389/tcp at the DC in the personas; without one, S3 makes the DC answer and the role
> fixed point still does not close.

**S4 — observed protocol content (medium, cross-repo). — NOT BUILT; gated on aximprint GAP-019** (the TLS/HTTP/SMB carrier, §6.1's amendment — an additive IR minor bump, a decision queued for the user). Once §6.1's `observations` block ships:
bind9 zones from the observed names; nginx vhosts and paths from observed SNI/`Host`/URI. Personas
gain matching `knobs.dns_qname` (`axrange/personas/drivers/command.py:485` already reads it and
`knobs` is a free-form dict, so **no persona schema change**), so a `drive: command` client asks for
a name the twin's own resolver is authoritative for. This is where the client half stops being
provable in principle and becomes provable in fact.

**S5 — the honest-gap register (small). — BUILT 2026-09-05** (GAP-061 QUIC · GAP-062 9997/tcp + 3389/udp · GAP-063 8006-as-plain-HTTPS · GAP-064 ephemeral + 7680, each cross-referenced from `axrange/fidelity/probe.classify_listen_port`). QUIC, 9997/tcp, 8006-as-Proxmox and the ephemeral ports go
into `docs/PRODUCT_GAPS.md` as observed-but-not-reproduced, with reasons, so the scorecard reports
them in that state rather than as failures or silent omissions (LES-089).

*Sizing:* S1 small · S2 medium · S3 medium (content-heavy, slow on-cluster) · S4 medium, gated on
aximprint · S5 small. S1 lands before the scorecard needs it; S2 and S3 are independent; S4 is last.

> **As built, the acceptance criteria split cleanly into two halves, and only one of them ran.**
> Everything provable without a cluster is proven — S1's regression pin (every shipped
> `twin-configs/*` keeps a byte-identical `plan_id`/`ir_hash`), the shape refusals, the
> determinism of `render_serve_script`, the D8 render-time contention guard, the twin-config
> `role: adds` path emitting `provision-app` + `wait-domain-ready` + the barrier, and the cast
> planning through axRange's own validator and planner (695 ops). Everything that requires a guest
> to actually answer — `ss -lnH -tu` matching the declared set, `dig` returning NOERROR, a completed
> TLS handshake, `PartOfDomain=True`, `eve.json` carrying `krb5`/`ldap`/`smb` rather than bare
> `tcp` — is **§9.2's gated live-verify and has not run**. Do not read a green suite as either
> slice's acceptance.

## 9. Validation

**9.1 Unit / suite.** (1) Every shipped `twin-configs/*` keeps a byte-identical `plan_id`/`ir_hash`.
(2) A cast carrying `services[]` (and, under D2b, `listen:`) expands → validates → plans, while an
unknown key still fails loudly under `extra="forbid"` + `_reject_removed_keys` (LES-111, LES-172).
(3) Each `serve-*.sh` renders deterministically, byte-identical for the same input. (4) A
`serve-*.sh` can never bind a port a real daemon on that host owns — asserted at render, not
discovered at runtime; and no echo is emitted for a port a real server covers (D8). (5) `role: adds`
emitted from a **twin config** produces `provision-app` + `wait-domain-ready` + the `domain-ready`
barrier, and errors loudly without `provides: [{domain}]` (`axrange/planner/pass1_emit.py:1096`).
(6) Fail-loud paths hold: an unsupported install kind still raises in `render_playbook`; a Windows
`role:` other than `adds` still raises `windows-role-unsupported` (`config_exec/backend.py:300`).
(7) No test reaches a real cluster — inject every backend seam (LES-208, GAP-055).

**9.2 Gated `dev45` live-verify (mandatory) — PASSED 2026-09-05/06 (result in §9.2.1).**
**Never against `te corpapcn-twin-0e4f7d58`**: it is do-not-destroy, it is scorecard run 1's subject, and giving it a
declared listen set would trip `--update`'s mutation refusal by this build's own design (§7.2, and
the conditional-carrier call in §10.2). A small purpose-built throwaway twin: a `gw` router (apt
egress), one bind9 resolver, one nginx web-tier, one chrony host, one `windows-server-2022` DC, one
`win11-finance` member with `joins:`, one Linux client carrying a `drive: command` persona, and a
`tap` plan binding the segment to a standing O2 `suricata-sensor`.

> **BUILT AS `twin-configs/svc-verify/` (commit `fbe70f87`) and RUN 2026-09-05/06 — see §9.2.1.**
> The paragraph below is the pre-run reasoning, kept because it explains why the run used a
> purpose-built seven-VM twin rather than either of the two inputs it describes.
>
> **The two live inputs already exist and neither has been deployed.** The **Windows/AD half** is
> `tests/fixtures/twin-configs/ad-cast/` — the `ad-cast` fixture built for S3: cast-shaped
> (`observation:` blocks, a `count: 2` group, declared listen sets), one `axdc01` with
> `install: [{role: adds}]` + `provides: [{domain: corp.axr.lab}]` declaring the ten observed
> service ports, two joined members with `scripts/enable-rdp.ps1`. It is a **test fixture**, not
> shipped content: for a live run, copy it under `twin-configs/` (or point `--twin-configs-dir` at
> `tests/fixtures/twin-configs`) and add the `gw` router the fixture omits — it has no egress, so
> nothing in it apt-installs today. The **Linux-server half** is the freshly installed **CorpAPCN
> cast** (`corpapcn-scope-check`, regenerated from aximprint, with the deploy gate narrowing it),
> whose 14 `serve-*.sh` recipes are the real bind9/nginx/chrony/dhcpd content — but it is a
> **138-VM-class** deploy, so the honest sequence is: prove the recipes on a hand-cut throwaway twin
> carrying two or three of them first, then let the fresh cast's own gated deploy (spec 13 §7, the
> first golden) be the full-scale exercise. Steps 4 and 5 are the ones that cannot be faked in-guest
> and are the reason this gate exists at all.

1. `te deploy` → all ops complete; `te info` shows the access facts.
2. **In-guest:** `ss -lnH -tu` on each server host matches its declared set exactly.
3. **Protocol-aware probes from the client** (the row-5 pass evidence): `dig @<resolver>
   <observed-qname>` → NOERROR + ANSWER; `curl -sk --resolve <name>:443:<ip> https://<name>/` → 200
   over a completed handshake; `ntpdate -q <ntp>` → an offset; `ldapsearch -x -H ldap://<dc> -s base`
   → rootDSE; `smbclient -L //<dc> -N` → a share list; `kinit` → a TGT.
4. **At the sensor** — the half that cannot be faked in-guest: `/var/log/suricata/eve.json`
   (`axrange/config_exec/render.py:1691`) records `event_type` of `dns`, `tls`, `ntp`, `krb5`,
   `ldap`, `smb` for those flows. **Bare `tcp`/`udp` where a protocol is expected is a FAIL** — the
   exact symptom this work order exists to remove.
5. `tfc deploy` with a `drive: command` persona → the same protocols appear unattended, from the
   persona's own cadence.
6. `te destroy` → clean; the standing twin and every `o2`/`img` resource untouched.

**9.2.1 Result — PASSED, all six steps, 2026-09-05/06.** Subject `twin-configs/svc-verify/`
(commit `fbe70f87`): seven VMs on two segments, deployed once, probed, destroyed. Authority:
**implementation (Claude Code)** for the measurement; the **user gave the go-ahead for every
mutating step** and personally ran the two `o2 deploy` commands, which the permission classifier
blocks for agents. Everything ran in a **dedicated scratch state DB** — `~/.axrange/state.db` and
`/home/rocky/axr-product/deploy-dev45/state.db` were never opened for write. Full record: CHANGELOG
2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED".

| step | result |
|---|---|
| **1** `te deploy` | `svc-verify-92fd7a79` (plan `svc-verify-14da1c1a`) — **82/82 op rows** (72 action + 10 barriers) in **17.3 min**; the DC path 8.6 min of it (`wait-domain-ready` 332 s, ADDS install 182 s, join 53 s). One Windows first-boot QGA `guest-exec` timeout **absorbed by the retry**. |
| **2** in-guest `ss` | every declared listen pair present on a **non-loopback** address on all five server hosts; `systemd-resolved` displaced on both bind9 hosts; `win-member`'s `137/udp` present. **One delta:** `svc-web` also exposes image-supplied **LLMNR `5355/tcp+udp`** on the wildcard, so the *"nothing beyond declared + tcp/22"* exclusivity clause cannot hold on a stock Debian cloud image — **GAP-075**. |
| **2b** `tap deploy` | `tap-svc-verify-92fd7a79-55c62f75` — **7/7 ops in 20.7 s**, gretap `tapg_1295ea` → `10.255.252.10`, mirrors on the five `svc` ports with the `gw`'s other legs de-scoped, **`verify-capture` TRUE** on a real sentinel. |
| **3** probes | **all six PASS.** `dig` → NOERROR/`aa`/`A 10.66.10.11`; `curl --resolve` → **200 over a completed handshake** (CN/SAN `web-tier-10-80-72-core.corp.axr.lab`); `ntpdate -q` → offset at **stratum 3** (chrony synced through the router, not the recipe's orphan 10); `ldapsearch -s base` → rootDSE `DC=corp,DC=axr,DC=lab`; `smbclient -L` → **anonymous session accepted, empty share table** (Server 2022 denies anonymous enumeration; NETLOGON/SYSVOL listed authenticated as `alice`); `kinit alice@CORP.AXR.LAB` → a TGT; `win-member` `PartOfDomain: True`. |
| **4** at the sensor | histogram for the twin's subnets: **alert 49, dns 30, flow 24, smb 15, krb5 5, dcerpc 3**. **DNS / Kerberos / SMB PASS** on their own event types. **NTP PASS via the documented fallback** — no `ntp` event because the render's `eve-log.types` omits it (**GAP-077**); `flow app_proto ntp` on UDP/123. **TLS PASS on substance** — 9 flows `app_proto tls` on 443, definitively not bare `tcp` — but **zero `tls` records** despite `tls` being enabled with `extended: yes` (**GAP-076**). **LDAP** `app_proto=failed`: unclassified by Suricata **7.0.10** (the `ldap` type is 8.0), reported and **not scored**, per this section's own version caveat. **DCERPC** classified on 135 and a dynamic port, a bonus. **The FAIL condition — bare `tcp`/`udp` where classifiable — did not occur.** |
| **5** `tfc deploy` | `tfc-svc-verify-92fd7a79-a5adee95` in **6 s**; the persona ran as a transient `systemd-run` unit; **32 invocations across four cycles, `fails=0 missing_tool=0`**. Then a **278 s window with no operator traffic**: 28 flows / 16 dns / 16 alerts from `probe-client`, `app_proto` **tls 10, ntp 10, dns 8**, plus ICMP — all four emits arrive **classified**, from the persona's own cadence. |
| **6** `te destroy` | by explicit ids with `--allow-destroy`: tfc (persona stopped), tap (5 mirror de-scopes, gretap removed), te (**17 resources, 0 not cleared, 54 s**) → **152 VMs** (151 baseline + the dedicated sensor); the standing twin's **138 untouched**; gretaps back to **26**; no leftover tap/mirror/zone; `img`/`o2`/`XPress-SW*`/`Tcpdump-SW-A` untouched; the standing sensor identical. |

**One disclosed guest write:** `/etc/krb5.conf` on `probe-client`, exactly as the bundle README
prescribes — client-side probe setup, not a repair. It is also a finding: **no automated consumer
can get a ticket until realm configuration becomes twin content** (**GAP-074**).

**The sensor saga, in brief — the run's most consequential finding.** Preflight found the standing
`o2-suricata` **up with Suricata DEAD since 2026-07-27** (launched as a bare `-D` daemon; the
2026-07-17 supervision fix covered tcpdump and zeek and **never Suricata**), invisible to
`o2 status` for **40 days** — **GAP-067**. The standing surface's deploy row lives in a *different*
state DB, so the sensor id did not resolve and `tap deploy` failed with an error naming a command
that, run against that other DB, contradicts it — **GAP-069**; the surface manifest is checked in
nowhere and had to be reconstructed from op inputs — **GAP-068**. The user's first choice, a
**re-assert in place**, failed at `clone-vm` (`template_vmid missing` — the `deploy-dev45` images
root has no `suricata-sensor` binding; **GAP-071**, **GAP-072**, LES-248) **after five SDN ops had
created a second vnet with the same CIDR in the shared zone**, because vnet names became
plan-unique one day *after* the standing surface was deployed and current code can no longer compute
its name — **GAP-070**, the one with real teeth (a re-run that got further would have moved the
standing sensor's NIC onto an address-less bridge). `tap validate` returned PASS against that
`failed` surface deploy — **GAP-073**. The user's second choice, a **dedicated sensor**, worked:
`o2-svcverify-90f643c9`, 15/15 ops in 1.21 min, VM 665273 at `10.255.252.10`, Suricata 7.0.10
`Engine started` — and the standing sensor's `eve.json` stayed frozen at 2026-07-27 to the
nanosecond. *(Observation, not a defect: the suricata sensor image has no `tshark`, so
`verify-capture` always takes its tcpdump fallback there.)*

**Two operator commands are still pending on `dev45`** — both the operator's to run (`o2 destroy`
is classifier-blocked for agents; the second needs `pvesh`):

```sh
axrange o2 destroy o2-svcverify-90f643c9 --allow-destroy --adapter-config adapter.conf --db <scratch>
#   blast radius verified from its 15 op rows: zone o2zda56b, vnet o2vd958f,
#   subnet 10.255.252.0/24, VM 665273 -- no row names any standing object.

pvesh delete /cluster/sdn/vnets/o2vb904f && pvesh set /cluster/sdn
#   the inert stray vnet from the failed re-assert (no IP, no subnet, no VM).
```

The failed deploy row `o2-surface-5274be37` is **deliberately left undestroyed**: its rows include
the **adopted shared zone**, so a destroy would sweep it out from under the standing surface.

**Deltas from the bundle README, all recorded rather than smoothed:** wall time 17.3 min against a
30–45 min budget; stratum 3, not 10; anonymous `smbclient -L` lists no shares (LES-244 — the
expected output had been written against the protocol, not the server's posture); `svc-web`'s 5355;
and the README's `--db deploy-dev45/state.db` convention is **wrong for this cluster** (a stale June
copy; the live twin DB is `~/.axrange/state.db` and the live surface is in
`/home/rocky/axr-product/deploy-dev45/state.db`). The README's DB guidance and its sensor recipe are
rewritten accordingly.

**Lessons:** LES-244..LES-250. **Gaps opened:** GAP-067..GAP-078 (GAP-078 came from the bundle
author, not the run: an explicit `ip:` on a lone router leg suppresses the segment's auto-gateway
and validation still passes).

## 10. Decisions for the user

None of these is this draft's to take.

**D1 — A real DC now, or deferred?** (a) Promote now, observed realm + lab-default users marked
`imputed`. (b) Defer to the LDIF ingest — which the spec sequences *after* the first golden, so
row 5 would fail for 12 of the 16 hosts on the golden run. (c) Only if aximprint exports principals
first. **→ (a).** §8.2 explicitly returns the responder problem to the critical path *"as a real DC
answering real Kerberos/LDAP/SMB"* and names §5.6 as the source that avoids the export; the
machinery is built and live-verified; the realm is genuinely observed; labelled imputed users are
permitted. The one recorded objection (GAP-C8's *"nothing in a capture supplies an AD forest name"*)
is **measurably wrong today** (§5.1).

**D2 — Version-locked: give `App.listen` its first consumer?** (a) §6.2(a) only, nothing in the IR
changes. (b) §6.2(b), wire the inert field + a twin-config `listen:` passthrough. (c) A new IR block.
**→ (b), with (a) as the mechanism underneath.** The field exists, its documented purpose covers
this, no shipped content sets it, and `IR_SCHEMA` needs **no version bump** — but it is
version-locked, so it needs the user's nod, not Claude Code's. (c) exceeds the problem.

**D3 — Bake, runtime install, or both?** (a) Bake servers *and* client tools. (b) Servers stay
runtime `pkg:`; bake **only** the client tools, closing GAP-056. (c) Runtime for everything.
**→ (b).** The servers are Debian-main packages that already install at runtime; a bake changes
`template_vmid` and re-clones every host. GAP-056 is different — it blocks the client half's own
live-verify, which needs a purpose-built image regardless.

**D4 — Observed realm (`axellio.dom`) or lab default (`corp.axr.lab`)?** **→ the observed realm.**
It is `evidence_class: observed` and the most detection-relevant name on the wire; the lab default
would put an imputed value on a fact the wire gave us. Users and passwords stay lab defaults.

**D5 — `10.80.20.5`, the second DC labelled `dns-resolver`.** (a) Linux equivalents (bind9 + slapd +
samba + dhcpd) — 6 of 9 ports, no Kerberos, no new machinery. (b) Re-image as `windows-server-2022`
and join the forest as a **second DC** — all 9 ports, but `Install-ADDSDomainController` is machinery
axRange has never run (only `Install-ADDSForest`) and adds a second slow promotion. (c) Leave it a
plain resolver. **→ (a) for the first golden, (b) as a follow-on.** Worth a note back: a catalog
inferring `dns-resolver` for a host serving 88/389/445 is a catalog issue, not a twin one.

**D6 — 8006/tcp and 9997/tcp.** (a) Serve 8006 as real HTTPS (it *is* HTTPS, just not Proxmox);
record 9997 as an honest gap. (b) Echo both. (c) Neither. **→ (a)**; (b) reintroduces the filler the
ruling removes. aximprint's role-fixed-point checker already carries both as permanent declared
exceptions (`scripts/check_role_fixed_point.py:70-78`), so this aligns the repos rather than
inventing a stance.

**D7 — QUIC (443/udp).** (a) Honest gap for v1. (b) Bake an HTTP/3-capable nginx. **→ (a)** —
443/udp is overwhelmingly an *external* listen (33 of 34), so it lands on the sink, aximprint's.

**D8 — Retire the echo, or keep it as a labelled fallback?** (a) Keep, only on ports no real server
covers, reported `imputed`. (b) Remove entirely; an unserved port is simply unserved. **→ (a)**,
with §9.1 test 4's hard guard — an unserved port draws an ICMP unreachable that Zeek scores as a
*fabricated reverse local edge*, so silence is not free either.

### 10.1 Rulings and calls, 2026-09-04

**Ruled by the user (CHANGELOG 2026-09-04):** **D1 (a)** — a real DC now. **D2 (b)** — wire
`App.listen` plus the twin-config `listen:` passthrough; no `scenario_schema_version` bump; every
shipped twin config keeps a byte-identical `plan_id`/`ir_hash` (§8 S1's pin). **D4 — the lab
realm `corp.axr.lab`**, not the observed `axellio.dom`: the forest name is `imputed`; users and
passwords are lab defaults marked `imputed`. Implementation reading: the DC's short hostname may
still follow the observed `axdc01`; only the realm is replaced. §5.1's finding stands as a
hand-back to aximprint regardless of D4.

**Taken as implementation calls (Claude Code), open to veto:** **D3 (b)** — servers stay runtime
`pkg:`; bake only the client tools (closes GAP-056). **D5 (a)** now, **(b)** as a follow-on — and
note that `docs/design/14-operator-assertions.md` gives `10.80.20.5`'s role a cleaner path: an
`override` assertion rather than a catalog edit. **D6 (a)**, **D7 (a)**. **D8 (a)** — the echo
survives only on ports no real server covers, labelled with the `compiler` evidence class of
design note 14 (it is a compiler choice, not an observation), behind §9.1 test 4's render-time
guard.

**Reconciled with the scorecard (§6.3):** the probe is the scorecard's and its protocol set follows
the observed AD profile; `services[]` is specified once, here in §6.1.

### 10.2 Built as, 2026-09-05

**Both halves landed the same day, in two repos, uncommitted at the time of writing.** CHANGELOG
2026-09-05 "Protocol-faithful services BUILT (row 5's server half, both repos)" is the record; this
block is the work order's own account of where the build **deviated from or resolved** what §1–§9
specified. Full suite here: **2318 passed / 12 skipped / 0 failed, 2330 collected** (44 new cases in
`tests/test_protocol_faithful_services.py`). aximprint: 2101 tests, fixed points 24/24.

**1. `clone-vm` carries `node_listen` ONLY when non-empty — and that is load-bearing, not tidiness.**
An unconditional `"node_listen": None` would appear on the NEW side of `plandiff._diff_fields` for
every host of every standing twin, whose persisted op rows have no such key. ABSENT vs `None` is a
difference, so **every** `clone-vm` would classify **Mutated** and `te deploy --update` would
hard-refuse on 100% of already-deployed content — LES-185's exact failure shape, one build later.
**The accepted cost, pinned by a test:** *changing* a declared set on a standing twin IS a named
`--update` refusal. That is the honest answer (§7.2 already calls a brand-new protocol on an existing
host a redeploy), but it makes the declared set a **deploy-time** fact rather than a
runtime-swappable one — the distinction `OP-TFC-020 assign-role` exists to provide for `role`.
Generalized as **LES-224**.

**2. `listen: []` is refused at the author.** Every layer downstream reads ABSENT as *"this twin
makes no claim about this host"*; there is no second consumer behaviour for *"claims to serve
nothing"*, so an empty list silently collapses into the first and is far more likely a half-finished
edit. Refused in `twinconfig.expand._check_listen`, naming the field — the `tap_points: []`
precedent. Generalized as **LES-226**.

**3. The multi-bound-app refusal is NARROW, deliberately.** A first cut refused any app bound to two
hosts and was caught **by shipped content**, not by a fixture:
`target-environments/enterprise-multisite/scenario.yaml` binds three apps to two hosts each, on
purpose, with author comments. `_emit_apps` now refuses **only** a multi-bound app that declares
`listen:` — the case this build makes *wrong* rather than merely incomplete, since the declared set
is joined over every binding and would publish ports for a host that installs nothing. The
pre-existing silent first-binding-wins drop is **GAP-065**, filed with both candidate fixes and left
as **the user's decision** (fan out, or refuse and split the shipped content — each moves a shipped
scenario's op set or its `ir_hash`/`plan_id`).

**4. The declared set is published beside the measured one and is barred from every verdict.**
`fidelity.rows.declared_vs_observed_listen()` lands in rows 4 and 5's `measured` block only, never a
clause and never a gate — a declaration is not evidence, and letting one decide row 5 would reinstate
exactly the substitution this work order removes. **All four (declared × observed) present/absent
cells are handled explicitly**, each absence reported as an absence: without the "no observed side"
guard, every declared port would surface as `declared_not_observed` — a manufactured finding that
*scales with how thoroughly the twin was authored*. Every branch publishes the same key set (the
document is meant to be diffed), and a strip-and-compare test pins that removing the declared keys
changes no verdict. Generalized as **LES-225**.

**5. The QUIC exclusion, and a derived denominator note.** `(443, "udp")` joined
`fidelity.probe._EXCLUSIONS` as *no protocol-aware probe* (**D7 (a)**, GAP-061); left in, it would
sit permanently `inconclusive` and block row 5 from ever passing for a reason that has nothing to do
with the twin. Row 5's published `denominator_note` is now **derived from that table** instead of
restating it — the hand-written copy had already gone stale inside the same review round that added
QUIC.

**6. Shape enforcement lives at the twin-config layer, not in the validator.** No catalog rule
references `App.listen`; `VALIDATOR` stays **114**. The refusal belongs where the author writes the
field, where it can name `twin_config.nodes[i].listen[j].<field>` and print the accepted set
(LES-211) — the IR-side model stays permissive like every other field in that version-locked module,
and `_declared_listen_by_host` **skips** a malformed hand-authored entry rather than taking a deploy
down over an informational fact.

**7. `proto:` is the TRANSPORT, and the vocabulary is closed to `{tcp, udp}`.** The application
protocol is derived from `(port, transport)` by the probe, in one place (LES-037) — an author who
could write both could declare `443/tcp` as `ntp`. A test asserts the declared vocabulary agrees with
the probe that consumes it.

**8. Two corrections went back to §6.1's seam sketch** rather than being implemented as specified:
there is **no per-listen `windows_seen`** (so **row 4's per-listen occupancy has no producer**, and
in-guest `ss` stays its only source) and **DNS rows are `referenced`, not `observed`**. Both are
struck through in §6.1. Generalized as **LES-228**.

**9. aximprint: one script per node GROUP, not per role.** This source's four `dns-resolver` hosts
answer on 1, 2 and 9 ports respectively, so a role-keyed script would make three of them serve six
ports nothing ever saw them serve — manufacturing the very listeners this work removes. A generated
artifact keyed coarser than the fact it carries must over- or under-claim for some members;
generalized as **LES-227**.

**10. What did NOT get built, and why.** **S4** — observed protocol content — is gated on aximprint
**GAP-019**, a carrier for the TLS server names / HTTP hosts / SMB shares the belief store already
holds but never crosses into a PIT; adding one is an additive IR minor bump, i.e. a decision queued
for the user. **D5 (b)** (the second DC promoted as a real Windows DC) stays a follow-on; **D5 (a)**'s
slapd/samba recipes were not written for this slice either, so `10.80.20.5`'s 135/389/445 fall to the
labelled echo. **Windows nodes get no service recipe** (aximprint GAP-018) beyond the DC's own
promotion and the members' `enable-rdp.ps1`. ~~And **§9.2's live-verify has not run** — nothing here
has stood a real server up on a cluster.~~ **Superseded 2026-09-06: §9.2 RAN and PASSED** on the
purpose-built `twin-configs/svc-verify/` twin — a real bind9, nginx and chrony answered real
clients, a real DC issued a real TGT, and the sensor recorded `dns`/`krb5`/`smb` event types with
**no bare `tcp`/`udp` on any classifiable flow**. See §9.2.1 for the result and the twelve gaps it
opened.

## 11. Out of scope / honest limits

- **Payload realism.** Response bodies are synthetic. Row 5's rule is *protocol-valid*, not
  *equivalent*; spec §9 already excludes payload content and this does not reopen it.
- **TLS certificates.** Always self-signed lab certs — a real certificate, chain or key is never
  reproduced; SNI/CN names may be observed.
- **Secrets of any kind.** No real hashes, passwords or key material — spec §5, absolute.
- **A realistic user population.** Two observed principals is not one (§5.3); the LDIF ingest fixes
  this and nothing here pretends to.
- **Firewall policy.** Row 7's clause cannot pass in v1 by the spec's own §3.2 — a twin host that
  answers still sits behind a permissive default-forward router.
- **The internet sink's service set** (§7.4) — aximprint's.
- **Windows beyond the DC and joined members.** No bespoke Windows service work; a Windows
  `run-script` arrives as PowerShell and a POSIX heredoc dies there — the failure that killed the
  superseded responder approach.
- **Proprietary protocols.** 9997/tcp has no free real server; saying so is the honest outcome.
- **`scripted` persona driver.** Unchanged — still the right tool for offered-load work.
- **Consuming what this build publishes.** S2 emits `bundle_schema: axr-topology/1.4` with per-node
  `services[]` (a `{port, proto}` joined to the **observed application**, plus a per-listen
  `evidence_class`) and a top-level `observations` block — and **no axRange consumer reads any of
  it**. Measured on the first golden's candidate cast, 2026-09-05: 403 `services[]` rows across 365
  of 786 nodes, 354 DNS names, 11 identity principals, all published and unread. The scorecard's
  probe still selects its method from a **port table** and still counts every row-5 fact
  `unclassified`, because both were written against `1.3`. Nothing breaks — `listen_ports` survives
  in 1.4 and is what row 5's denominator reads — so this is an **unconsumed seam, filed as GAP-066**
  and sequenced with S4 rather than fixed in the window scorecard run 1 was measured in (§12).

## 12. Depends on / sequence

- **Depends on:** nothing unbuilt on the axRange side for S1-S3 (the DC path is built and
  live-verified; its inputs are already in `ir_identity` and need only export). S4 depends on §6.1's
  `observations` block **plus** §5.2's aximprint parser changes. None of those is a capture problem.
- **Parallel with:** `docs/WORK_ORDER_fidelity_scorecard.md`, meeting at §6 (overlaps in §6.3).
- **Hands back to aximprint** (not blocking, must not be lost): correct GAP-C8's premise (§5.1)
  — **done 2026-09-05**, `roles.yaml`'s row and the fixed-point checker's two DC exceptions;
  fix the port-389 `serves` tie-break, or pin 389/tcp at the DC, so a real DC is actually re-inferred
  (S3) — **done 2026-09-05** by reference-ingest S0 (the tie broken by catalog declaration order,
  LES-136, never row order; `ldap [389] -> domain-controller` in a real cast; fixed points 24/24) —
  it is what decides whether the DC merely *answers* or is also *re-inferred*; ship the missing `*.provenance.json` sidecar (§6.1) — **done 2026-09-05** by the
  sibling reference-ingest build.
- **Unblocks:** the client half's still-pending live-verify — its §5.1 blocker is exactly "a real
  client needs a real server." **Partly discharged 2026-09-06:** §9.2's run drove a `drive: command`
  persona against real daemons for 32 invocations with `fails=0 missing_tool=0`, and all four emits
  were classified at the sensor. What remains for the client half's own gate is GAP-056 (the tool
  set baked into an image rather than installed at run time), which a throwaway twin sidesteps and a
  standing one cannot.
- **Followed by:** the fresh cast certified as the first golden (spec §7), then reference ingest and
  the firewall-policy extension.
- **Closes on landing:** **GAP-056** (if D3 bakes the client tools); materially reduces **GAP-039**.
  Does **not** close GAP-048 or GAP-054.
- **As built 2026-09-05:** **GAP-056 stays OPEN** — D3 (b) rules the client tools baked, and no bake
  was done in this build, so `drive: command`'s own live-verify stays blocked on it. **GAP-039** is
  materially reduced on the producer side only (14 real-daemon recipes replacing the echo), and
  nothing is proven on a cluster yet. **Opened instead:** GAP-061..065 here, aximprint GAP-018/GAP-019
  there.
- **Seam follow-on, opened 2026-09-05 by the first golden's candidate cast: GAP-066** — the `1.4`
  payload this build ships (`services[].app`, the per-listen `evidence_class`, `observations`) is
  **published and unconsumed** on the axRange side. It is deliberately *not* fixed here: reading it
  changes row 5's method selection and its evidence mix, the two things **scorecard run 1 was just
  measured against** (`docs/WORK_ORDER_fidelity_scorecard.md` §7.1), so doing it in the same window
  would make run 1 and its successor incomparable for a reason unrelated to the twin. It shares a
  consumer with **S4** and is gated by the same aximprint **GAP-019** decision — sequence the three
  together.
- **Live-verify status, 2026-09-06: §9.2 PASSED** (§9.2.1). **GAP-039** is now materially reduced
  on *both* sides — the recipes are proven on a cluster, not only rendered. **GAP-056 still open**
  and still blocking the client half's own gate. **Twelve gaps opened by the run:** GAP-067..GAP-078
  — most of them about the **standing O2 surface** (a dead-but-green Suricata, no re-assert path, an
  unrecorded manifest, DB-local sensor resolution, an un-adoptable vnet name) rather than about this
  work order's own subject, plus three sensor/criterion findings that *are* this subject's:
  GAP-075 (image-supplied LLMNR defeats listen-set exclusivity), GAP-076 (Suricata emits no `tls`
  records although the flow is classified `tls` — the record where SNI lives, so **S4 has no
  evidence channel without it**) and GAP-077 (`ntp` missing from the rendered `eve-log.types`).
  Sequence GAP-076 with S4.
