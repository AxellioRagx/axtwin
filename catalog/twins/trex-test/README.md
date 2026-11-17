# trex-test: a TRex ASTF crowd with internal and external services

Phase A of the crowd work (branch `wip/trex-crowd`; Phase B, a first-class
crowd node, is a design note awaiting sign-off: `docs/design/crowd-node.md`).
Existing schema only. Loop steps: **deploy** and **drive realistic traffic**,
proved against the **capture gate**.

```
            INTERNAL (RFC 1918)                         |  EXTERNAL (TEST-NET)
                                                        |
  lan-a 10.70.1.0/24          lan-b 10.70.2.0/24        |  inet 192.0.2.0/24
  trex-client .10             trex-server .10           |  trex-ext .10
   50 crowd clients            fs01 10.250.2.1 (SMB)    |   web 198.51.100.1-.14 (HTTP, TLS)
   10.250.1.1-.50              fs02 10.250.2.2 (SMB)    |   dns 203.0.113.1-.2   (DNS)
        |                     dc01 .20 (AD DS,          |        |
        |                      crowd.axr.lab)           |        |
        +-------- gw .1 ---------- gw .1 ---------------+--- gw .1   (the perimeter:
                                                        |    routes + "no new connection from inet")
  tap: segment lan-a          tap: segment lan-b        |  tap: perimeter inet
```

**The boundary.** The schema has no internal/external flag on a segment, so
the boundary is stated by topology, addressing and policy: lan-a and lan-b
(and the crowd ranges routed to them) are RFC 1918; `inet` and the ranges
behind it are documentation (TEST-NET) space, which nothing real routes. `gw`
is the perimeter. Its inet leg is the one edge between the two sides, and
`scripts/serve-crowd-routes.sh` installs an nftables rule there: replies
pass, and any new connection arriving from inet is dropped. The tap plan
taps that edge as `perimeter` and the internal LANs as `segment`.

| file | what |
|---|---|
| `te.yaml` | 3 segments (gateways declared), router `gw`, the three generator hosts on `trex-astf@v1`, domain controller `dc01` (`windows-server-2022@v1`, `role: adds`), the domain's users |
| `scripts/serve-crowd-routes.sh` | the router's 4 static routes to the emulated ranges and the inet boundary rule (`install: script`; the Debian configure-router installs no routes) |
| `scripts/prepare-dc.ps1` | registers `fs01`/`fs02` in the DC's DNS once the zone is served, and reads them back |
| `scripts/gen-smb-sessions.py` | generates the SMB templates in the client persona (re-run after editing it) |
| `personas/trex-crowd-client.yaml` | `drive: trex-astf`, side client: crowd range, per-template server ranges, templates, cps, schedule (`knobs`) |
| `personas/trex-crowd-server.yaml` | internal server side (trex-server), `profile_from: trex-crowd-client` |
| `personas/trex-crowd-ext.yaml` | external server side (trex-ext), `profile_from: trex-crowd-client` |
| `tfc/default.yaml` | the three assignments, server sides first |
| `tap/default.yaml` | `segment` taps on lan-a and lan-b, a `perimeter` tap on inet, all to XPress-SW-A |

**The traffic** (configured cps at x1):

| template | path | server | cps | what |
|---|---|---|---:|---|
| http | internal -> external | 198.51.100.1-.14 :80 | 20 | replayed HTTP browsing exchange (TRex `avl/`) |
| tls | internal -> external | 198.51.100.1-.14 :443 | 10 | replayed HTTPS session (TRex `avl/`) |
| dns | internal -> external | 203.0.113.1-.2 :53/udp | 40 | replayed A query/response (TRex `avl/`) |
| smb-read | internal -> internal | fs01 10.250.2.1 :445 | 3 | SMB 3.1.1: CROWD\alice downloads `Finance\Q3-forecast.xlsx`, 256 KiB in 4 x 64 KiB reads |
| smb-write | internal -> internal | fs02 10.250.2.2 :445 | 2 | SMB 3.1.1: CROWD\bob uploads `Projects\design-notes.docx`, 96 KiB in 2 writes |

**The SMB sessions** are generated (`scripts/gen-smb-sessions.py`) as the
whole message sequence a Windows 10 client and a Windows Server 2022 file
server exchange:
- NEGOTIATE for 2.0.2-3.1.1, with preauth, encryption, netname and signing contexts.
- SPNEGO, then NTLMSSP NEGOTIATE / CHALLENGE / AUTH (NTLMv2) for the CROWD domain. After that the session is signed.
- TREE_CONNECT, CREATE, then READ or WRITE in 64 KiB chunks.
- CLOSE, TREE_DISCONNECT, LOGOFF.

tshark decodes every message of the release's `astf-sim` output with no
malformed frame. The names match the real AD in the twin: domain
`crowd.axr.lab`, users from `identity:`, and fs01/fs02 registered in the
DC's DNS. **Not real:**
- The cryptography: challenges, proofs, keys, MICs and signatures are
  deterministic random bytes, and every flow of a template replays the same
  bytes.
- The file bodies: an OOXML/ZIP header, then a repeated 256-byte block.
- Kerberos: the sessions use NTLM, because a Kerberos login would need a TGS
  exchange with the real DC from each emulated client, which TRex cannot do.
  Windows uses NTLM itself whenever Kerberos is not available.

**Why two file servers:** ASTF tells two server templates on one port apart
only by an association rule, so each SMB session is associated by port 445
plus its server's address.

Run (console or API): `POST /api/twins/trex-test/plan?target=dev45`, then
`POST /api/twins/trex-test/deploy?target=dev45` with `{"expect_plan_id": ...,
"follow_on": ["tap", "tfc"]}`. Live stats:
`GET /api/deploys/<id>/health?host=trex-client` -> `personas[].stats`.

## Live acceptance (dev45, 2026-11-17, deploy `trex-test-cba811f5`)

Plan `trex-test-e84255e5`; te, tap and tfc completed (70/70 ops, including
provision-app, run-script and domain-ready on dc01, and create-domain-user
for alice and bob). Sampled about 35 min in (x1, after the x2 window).

- **Each flow reaches the right server** (generator stats, 5-minute rates):

  | template | client cps | answered by | accepts/s | bytes per session |
  |---|---|---|---|---|
  | smb-read | 3.0 | trex-server (fs01) | 2.98 | 264 KB down (256 KiB file) |
  | smb-write | 2.0 | trex-server (fs02) | 1.99 | 100 KB up (96 KiB file) |
  | http | 20.2 | trex-ext | 20.2 | |
  | tls | 9.9 | trex-ext | 9.9 | |
  | dns | 39.9 | trex-ext | 39.9 | |

  trex-server accepted no web or DNS flows, and trex-ext accepted no SMB.
- **Sensor:** XPress-SW-A `gre-cap` rose by about 5,150 pps, 44 Mbit/s.
  A 45 s GRE capture on dev45's `o2v7dc98` shows **three keys**, one per tap:
  - **lan-a:** every flow.
  - **lan-b:** SMB to 10.250.2.1 and .2 only.
  - **inet perimeter:** HTTP, TLS and DNS to 198.51.100.x and 203.0.113.x
    only, with no SMB.

  Flow counts agree across the taps (SMB on lan-a = lan-b, web/DNS on
  lan-a = perimeter, DNS 1,788 queries and 1,788 replies on both), and
  tshark reports 0 malformed frames.
- **SMB on the wire (lan-b), one session of each kind:**
  - **Read:** Negotiate, SessionSetup (MORE_PROCESSING_REQUIRED), then
    SessionSetup as `CROWD\alice` from `WS-FIN-017`. TreeConnect
    `\\fs01.crowd.axr.lab\shared`, Create `Finance\Q3-forecast.xlsx`,
    4 x Read 65536, Close, TreeDisconnect, Logoff. All replies are
    STATUS_SUCCESS apart from the expected MORE_PROCESSING.
  - **Write:** the same sequence as `CROWD\bob` from `WS-ENG-042` on fs02,
    Create `Projects\design-notes.docx`, then Write 65536 and Write 32768.
- **Boundary:** every SYN on the perimeter tap came from inside
  (10.250.1.x, 1,343 in 45 s); none came from outside. On gw, `nft list
  table inet axr_boundary` shows the rule on the inet leg (eth2), and the
  four static routes are installed. The drop counter read 788 packets and
  stayed there over 60 s: a one-time burst during bring-up, not ongoing
  loss.
- **AD:** on dc01, `Resolve-DnsName` against its own DNS returns fs01 ->
  10.250.2.1 and fs02 -> 10.250.2.2. `Get-ADUser` lists
  `CN=alice,OU=Finance` and `CN=bob,OU=Engineering`, both enabled, in the
  domain with NetBIOS name `CROWD`.
- **DNS loss:** the client logged 16,353 UDP flows with no reply out of
  108,655 sent. Live rates match at x1, so the loss is from the x2 window,
  the same as in the first topology (open items).

## The driver and the template

- **Driver:** `engine/src/axrange/personas/drivers/trex_astf.py` (tests
  `engine/tests/test_trex_astf.py`; the real-release check runs with
  `TREX_HOME=<v3.08 dir>`). Two transient units per side: the TRex daemon
  (`t-rex-64 -i --astf --astf-server-only` / `--astf-client-mask 0x1`,
  `low_end`, the NIC through af_packet, `dummy` on the other port) and a
  controller that loads the profile, applies the schedule with
  `ASTFClient.update(mult)` and writes `stats.json`. The NIC is handed to TRex
  (kernel address flushed) and given back on stop.
- **Pin: TRex v3.08** (upstream `cisco-system-traffic-generator/trex-core`,
  tag v3.0.8, 2025-11-05, the newest release on 2026-11-16), tarball
  `https://trex-tgn.cisco.com/trex/release/v3.08.tar.gz`, sha256
  `d2ac73627906c4ce01127c1c6954464fab373463966ea9592509446e74972b5f`. Every
  API name in the driver was read from that release's `trex.astf.api`.
- **Golden template `trex-astf`:** manifest and binding next to the other
  golden templates, in `~/axr-product/deploy-dev45/images/`
  (`manifests/trex-astf.yaml`, `bindings/proxmox/trex-astf.yaml`; that
  folder is not under version control, so the recipe is restated here).
  Debian 12 genericcloud; packages `curl ca-certificates python3 pciutils
  iproute2 tcpdump`; one provision script downloads the tarball, checks the
  sha256, unpacks to `/opt/trex/v3.08`, links `/opt/trex/current`, writes
  `vm.nr_hugepages = 256` to `/etc/sysctl.d/80-trex.conf` (low_end allocates
  `20 + 40 * ports` 2 MiB pages) and `/opt/trex/VERSION`. Probe
  `traffic:trex-astf`: the binary, the version, and
  `from trex.astf.api import ASTFClient, ASTFProfile`. Build:
  `axrange image build trex-astf --images ~/axr-product/deploy-dev45
  --adapter-config config/adapter.conf --skip-smoke`. On dev45 it is template
  **102** (`img-trex-astf`), probes 2/2. Why `--skip-smoke`: the build's
  smoke test cannot run for any image (open items).

**Template provenance:** TRex v3.08's bundled `avl/` sample captures:
`delay_10_http_browsing_0.pcap` (HTTP GET, TCP/80, 37 packets),
`delay_10_dns_0.pcap` (one A query/response, UDP/53), and
`delay_10_https_0.pcap` (one TLS session, TCP/443, 96 packets). There is no SMB
capture in the repo or the release, so the SMB templates are generated L7
programs (above).

**Program steps (driver knobs):** `send: <hex>` (client to server),
`reply: <hex>` (server to client, exact bytes) and `recv: <n>` (server to
client, n zero bytes). `send`/`reply` take `size` and `fill`: the step is
padded to `size` by repeating `fill`, which is TRex's own
`ASTFProgram.send(buf, size, fill)`. A template's own `servers: {range,
count}` overrides the profile-wide range, and a program with its own range
is associated by port and range.

## Acceptance of the first topology (dev45, 2026-11-16, deploy `trex-test-9df9ca02`)

This run predates the internal/external split, the AD DC and the SMB
sessions: one server generator answered every template, and `smb` was a
single NEGOTIATE-shaped request and reply.

Raw data: `~/.axrange/trex-test-acceptance/` (`stats.jsonl`: both heartbeats
every 10 s for 23 minutes; `acceptance3.txt`; the GRE capture and its
decapsulated pcap; the discovery store and cast).

**1. Validates and deploys with no manual steps: PASS.** `POST /api/validate
{"config": "trex-test"}`: PASS, passes 1-3, 0 findings. Plan preflight clean
(3/3 images bound). One deploy call with `follow_on: [tap, tfc]`: te, tap and
tfc all `completed`. (The first deploy, `trex-test-5b5fc704`, came up with no
default route on the generators, because a pinned router-leg `ip:` records no
segment gateway; fixed by declaring `gateway:` on the segments. The first tfc
also hit a TRex v3.08 scheduler abort on this exact cps mix; the profile
builder now breaks exact ratios. Both are fixed in the commits that ran this.)

**2. Tapped traffic reaches the sensor on both taps: PASS.**
- **Sink counter:** XPress-SW-A `gre-cap` RX climbed 63,060 packets / 63.9 MB
  in 20 s (about 3,150 pps, 25.6 Mbit/s for both taps).
- **Attribution:** a 60 s capture of the GRE transport on dev45 has two GRE
  keys, one per tap binding, at 101,928 and 101,933 inner frames. The inner
  traffic is 10.250.1.x <-> 10.250.2.x. Full-size frames ride as two outer IP
  fragments (1500 + 62 bytes), which the sink reassembles.

**3. Rates and the schedule step: PASS on established TCP and client-side
UDP; DNS shows server-side loss at x2.** These are 5-minute means of new
connections per second from the controller's counters (`connects`, the
client side), against configured cps x mult:

| template | x1 configured | x1 measured | x2 configured | x2 measured |
|---|---:|---:|---:|---:|
| http | 20 | 19.75 (-1.2%) | 40 | 37.64 (-5.9%) |
| dns | 40 | 40.27 (+0.7%) | 80 | 79.52 (-0.6%) |
| tls | 10 | 10.15 (+1.5%) | 20 | 18.98 (-5.1%) |
| smb | 5 | 4.83 (-3.4%) | 10 | 9.43 (-5.7%) |

- **The step:** at uptime 600 s (x1 -> x2) the first sample after it already
  showed every template above 1.5x its x1 rate, at 10 s (http 18.8 -> 37.5,
  dns 42.1 -> 83.3).
- **The server side** agrees for TCP at x2 (accepts/s: http 37.2, tls 18.8,
  smb 9.3).
- **UDP at x2:** the server accepted only **50.4 DNS/s of the 79.5 sent**.
  The client logged 15,221 UDP flows ending without a reply (keepalive drops)
  by the end of x2, against 574 at the end of x1 (about 1.6% loss at x1,
  about 20% at x2). For UDP a client "connect" is a send, so the strict
  reading (queries that arrived) **fails for DNS at x2 (-37%)**. Where the
  queries are lost (the server's single low_end core and its af_packet RX,
  the router, or the bridge) is not yet known. The capture was taken at x1
  only. Next step: a capture at x2, comparing lan-a and lan-b tap counts.

**4. Discovery round trip: PASS, with the deltas listed.** The 60 s tap
capture (x1), decapsulated, went through `aximprint ingest --plugin pcap`
(Zeek), `bake` and `cast`:

| | configured | discovered |
|---|---|---|
| client IPs | 50 (10.250.1.1-.50) | **50**, all on `10.250.1.0/24` |
| server IPs | 14 (10.250.2.1-.14) | **14**, listening on 53/udp, 80, 443, 445 |
| flow mix http : dns : tls : smb | 26.7 : 53.3 : 13.3 : 6.7 % | 26.2 : 53.1 : 14.4 : 6.3 % (per-tap flow counts 1,175 / 2,387 / 648 / 284 in 60 s) |
| protocols | HTTP, DNS, TLS, SMB-ish | Zeek: http 1,175, dns 4,774 (queries and replies, both taps), ssl 648, 445 as plain TCP |

Deltas:
- Discovery widens the server range to `10.250.2.0/24` (the cast segments by
  /24).
- It names the servers `file-server` (port 445 suggests SMB) and the clients
  `generic-endpoint`.
- It sees one TLS certificate for all 14 servers (replayed payload).
- It adds the cast's standard synthetic `internet-sink` groups, which are not
  observed.
- It does not see the router as a host (the router only forwards).

**5. Teardown leaves nothing: PASS.**
- **tfc destroy:** both generators have no `axr-persona-*` units and no TRex
  process, and `eth0` has its address and default route back.
- **te destroy** (cascading tap): 9 resources removed, 0 errors. dev45 shows
  no trex-test VM, no zone or vnet except the O2 lab's own, no `tapg_`
  gretap, no OVS bridge, no tc mirror; no lease or lock; all seven trex-test
  deploy rows `destroyed`.
- **Router routes:** they lived in the router VM, which is gone.
- **Kept on purpose:** the golden template (102).

## Known fidelity limits (documented, not fixed)

- **One MAC per generator port:** every emulated client shares
  trex-client's MAC.
- **One TCP/IP fingerprint:** TRex's own stack (window 32768, its options,
  TTL) for every emulated host.
- **No host background chatter** (ARP, DHCP, mDNS, NetBIOS, LLMNR) unless it
  is templated.
- **Replayed payloads are identical,** TLS ciphertext and the certificate
  included.
- **No real stateful service on the far side:** no AD/Kerberos, real SMB
  server or recursive resolver. Those stay real VMs.
- **Throughput:** about 75 cps on one low_end core per side, mirrored at
  25 Mbit/s in the first topology and 44 Mbit/s with the SMB transfers. UDP
  loses packets at x2 (above).
- **SMB:** NTLM only, with random cryptography; one user, workstation and
  file per session template, whatever the emulated client IP; and the
  emulated clients never talk to the real DC (above).

## Open items

- **Per-client ARP for on-link ranges (TRex EMU).** Not built. The crowd
  ranges here are routed.
- **The DNS loss at x2:** locate it (above). It appeared again on
  `trex-test-cba811f5`, where DNS answers come from trex-ext beyond the
  router: 16,353 unanswered out of 108,655.
- **`axr-crowd-routes.service` reports `inactive`** on the running gw. The
  install script runs `routes.sh` directly and only enables the unit, so the
  unit next runs at boot. The routes and the rule are in place.
- **The image build's smoke test** fails for every image: its default
  exporter op is config-class and the build executor has no config backend.
  The failed run left a smoke VM, zone, vnet and bridge, removed with
  `axrange te destroy` against its state DB.
- **The Debian `configure-router` static-route handler.** The route script
  here and corp-twin's `serve-hq-mx-routes.sh` exist because it is missing.
