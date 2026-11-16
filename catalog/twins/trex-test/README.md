# trex-test: one TRex ASTF generator pair stands in for a crowd

Phase A of the crowd work (branch `wip/trex-crowd`; Phase B, a first-class
crowd node, is a design note awaiting sign-off: `docs/design/crowd-node.md`).
Existing schema only. Loop steps: **deploy** and **drive realistic traffic**,
proved against the **capture gate**.

```
               lan-a 10.70.1.0/24                 lan-b 10.70.2.0/24
  trex-client .10 ──────── gw .1 │ gw .1 ──────── trex-server .10
  emulates 10.250.1.1-.50        │                answers for 10.250.2.1-.14
  (client side, port 0)    static routes          (server side, port 1)
                       10.250.1.0/24 via .1.10
                       10.250.2.0/28 via .2.10
  segment tap lan-a ─┐                        ┌─ segment tap lan-b
                     └──── XPress-SW-A ───────┘
```

| file | what |
|---|---|
| `te.yaml` | 2 segments (gateways declared), router `gw`, the two generator hosts on image `trex-astf@v1` |
| `scripts/serve-crowd-routes.sh` | the router's static routes to the crowd ranges (`install: script`; the Debian configure-router installs no routes) |
| `personas/trex-crowd-client.yaml` | `drive: trex-astf`, side client: crowd range, server range, templates, cps, schedule (`knobs`) |
| `personas/trex-crowd-server.yaml` | side server, `profile_from: trex-crowd-client` (ASTF split mode loads one profile on both sides) |
| `tfc/default.yaml` | the two assignments |
| `tap/default.yaml` | `segment` taps on lan-a and lan-b to XPress-SW-A |

Run (console or API): `POST /api/twins/trex-test/plan?target=dev45`, then
`POST /api/twins/trex-test/deploy?target=dev45` with `{"expect_plan_id": ...,
"follow_on": ["tap", "tfc"]}`. Live stats:
`GET /api/deploys/<id>/health?host=trex-client` -> `personas[].stats`.

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
capture in the repo or the release, so `smb` is an L7 program on TCP/445: a
104-byte SMB2 NEGOTIATE-shaped request (NetBIOS header, `FE 53 4D 42`, 64-byte
header, 36-byte negotiate body) and a 248-byte reply.

## Acceptance (dev45, 2026-11-16, deploy `trex-test-9df9ca02`)

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
- **Throughput:** about 75 cps and 25 Mbit/s mirrored on one low_end core per
  side, with UDP loss at x2 (above).

## Open items

- **Per-client ARP for on-link ranges (TRex EMU).** Not built. The crowd
  ranges here are routed.
- **The DNS loss at x2:** locate it (above).
- **The image build's smoke test** fails for every image: its default
  exporter op is config-class and the build executor has no config backend.
  The failed run left a smoke VM, zone, vnet and bridge, removed with
  `axrange te destroy` against its state DB.
- **The Debian `configure-router` static-route handler.** The route script
  here and corp-twin's `serve-hq-mx-routes.sh` exist because it is missing.
