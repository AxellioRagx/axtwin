# `twins/_shared/scripts/` — recipes shared by every twin

Per-twin recipes -- hand-written, or a cast's generated ones -- live in
`twins/<id>/scripts/`. This folder is for in-guest recipes that are the
**same file for every twin** — their per-twin variation arrives as *data*, not as a
regenerated script.

Today that is one thing: the **sink**.

## The sink

One VM that stands in for the whole observed external world.

A twin's hosts run **real protocol clients**, and a real client needs a real server on
the far end of every destination the cast observed (ruling 2026-09-21: *a proxy that
all external IPs are routed to, which responds as if the remote servers were
responding*). Egress to the real internet stays **denied** during measurement, so this
box **is** the internet as far as the twin is concerned: the router sends every
external prefix to it, and it answers.

| Port | Server | Notes |
|---|---|---|
| 53/udp,tcp | bind9 | authoritative for `.` over the cast's observed names; **NXDOMAIN** outside the corpus; nothing is forwarded |
| 443/tcp | Caddy | TLS with a certificate minted **per SNI on the fly** from the lab CA — **HTTP/1.1 + HTTP/2** |
| 443/udp | Caddy | **HTTP/3 (QUIC)** — on by default in Caddy 2.6 |
| 80/tcp | Caddy | plain HTTP |
| 123/udp | chrony | `local stratum 8`: serves time although it can never sync (no upstream) |
| 22/tcp | sshd | as shipped |
| anything else | socat | generic TCP/UDP responder, so an unclaimed port **completes a connection instead of answering RST** |

**Tool choices.** DNS is **bind9 over a root zone**, because "authoritative for
everything, NXDOMAIN outside the corpus" *is* a master zone for `.` — dnsmasq would
have to reach the same answer via a catch-all `local=/#/` that turns "I have no
upstream" (SERVFAIL) into NXDOMAIN as a side effect of a forwarding rule. TLS is
**Caddy 2.6.2** (`bookworm`, `caddy` package) with on-demand issuance from Caddy's
internal PKI **rooted in our lab CA**. **mitmproxy** (`bookworm` ships 8.1.1-2) was
evaluated and rejected: 8.1.1 has no QUIC — QUIC landed upstream in mitmproxy 10 — so
HTTP/3 would have been impossible, and in reverse mode it needs a separate origin
server behind it anyway. **HTTP/3 is therefore served, from Debian packages**; the
recipe asserts the udp/443 listener rather than trusting the release notes.

### Generating the corpus

```
.venv/bin/python scripts/gen_sink_corpus.py \
    --topology twins/<cast>/generated/<cast>.topology.json \
    --out twins/<twin>/scripts
```

Against `twins/pit-395a61f04e-1780405200/generated/pit-395a61f04e-1780405200.topology.json` that is **613 names / 2254 A
records / 2226 external addresses / 1270 CIDRs** (`--aggregate-min 0` for the lossless
set: 2059 CIDRs). It writes:

| File | What |
|---|---|
| `db.axr-sink-root` | the bind9 root zone — every observed external name at its really-observed address |
| `external-cidrs.txt` | the external address set, collapsed, for the router and the sink |
| `install-sink-corpus.sh` | a `run-script` payload that lays both down in `/etc/axr-sink/` |
| `sink-corpus-manifest.json` | counts + cast provenance + per-file digests |

Same topology in, byte-identical files out. IPv6 answers are **skipped** by default
(`--include-aaaa` keeps them): the twins are v4-only, and an AAAA pointing at a real
external v6 address that nothing routes to the sink turns a v6-preferring client into
a timeout. Multicast, broadcast and RFC1918 addresses that carry an `external` label
are dropped — no unicast sink can stand in for SSDP's `239.255.255.250`.

### Declaring it on a node

`install-sink-corpus.sh` must run **before** `serve-sink.sh`, which fails loud if the
corpus is missing (an empty corpus NXDOMAINs every client in the twin, and the twin
then looks broken in a way that reads like a client bug):

```yaml
- id: sink
  image: debian-12-cloud@v1
  nets: [{segment: wan-sink}]
  listen: ["53/udp", "53/tcp", "80/tcp", "443/tcp", "443/udp", "123/udp", "22/tcp"]
  install:
    - script: install-sink-corpus.sh
    - script: serve-sink.sh
```

The twin's router routes `external-cidrs.txt` (or simply its default route) at this
host; the twin's clients resolve through it, directly or via the twin's own resolver.

### The any-IP trick

The sink must accept packets addressed to IPs it does not own. `serve-sink.sh` adds
`local 0.0.0.0/0 dev lo` to **routing table 100**, selected by an `ip rule` that
matches on the **input interface**:

* packets arriving on a LAN NIC hit table 100 and are delivered locally, whatever
  their destination;
* packets the sink generates have `iif lo`, miss the rule, and route normally through
  `main` — so replies reach the client and apt still works.

Putting that route in the main `local` table instead would make *every* destination
local for locally generated traffic too, and the sink could not answer anyone.
`rp_filter` must be `0` (reverse-path validation re-runs the rule set on the incoming
interface, hits the same local default, and drops the packet as martian), and
`ip_forward` stays `0` — the sink terminates the external world, it does not relay it.
Set `AXR_SINK_ROUTE_MODE=cidrs` to accept only the generated set instead of anything
that arrives. The rule, route, sysctls and nftables ruleset are re-applied at boot by
`axr-sink-netsetup.service`.

### The lab CA

`/etc/axr-sink/ca/axr-sink-ca.crt` — a stable, well-known path. Caddy signs an
intermediate with it and mints every leaf underneath, so a client needs exactly this
one certificate. The image bake installs it into client trust stores; until it does,
clients see an untrusted chain (a real TLS handshake with a real certificate for the
real name — just not a trusted one).

### Health

`serve-sink.sh --healthcheck` exits 0 only when every unit it started is active **and**
every listener is up **and** the any-IP rule is present. The provisioning run proves
itself in-guest before it returns: the corpus answers its own first name, a name
outside the corpus gets NXDOMAIN, `:80` answers, `:443` presents a certificate for the
requested SNI verified against the lab CA, and udp/443 is listening.

## Limits — read these before reading a capture

* **No recorded HTTP bodies and no SNI evidence exist.** The cast never captured
  response bodies, and Suricata emitted no `tls` records at all on the mirrored sensor
  (GAP-076, aximprint GAP-019), so subject/issuer/SNI/JA3 were never observed. The
  sink therefore serves **real names with real certificates and GENERIC bodies**. The
  protocol is reproduced; the content is not, and no body served here is a claim about
  what the original server sent.
* **Pinned endpoints complete a handshake and then abort.** Apple push
  (`*.push.apple.com`), Microsoft connectivity checks
  (`*.connectivity-test.static.microsoft`) and anything else doing certificate pinning
  or signed-response validation will finish the TLS handshake against the lab CA and
  then drop the connection. **That is expected.** Label those flows
  **handshake-only** — never count them as a success, and never chase them as a sink
  defect.
* **A TLS dial with no SNI fails.** On-demand issuance has no name to mint for when a
  client connects to a bare IP without SNI. (mitmproxy's one advantage: it falls back
  to the destination IP. Revisit if the capture shows this mattering.)
* **HTTP/3 is served, but nothing about HTTP/3 was observed.** Serving it means a
  QUIC-capable client does not silently fall back to TCP; it does not mean the twin
  reproduces a measured HTTP/3 exchange.
* **Unknown ports get an echo, not a protocol.** The generic responder completes the
  connection and echoes bytes. A client expecting a server greeting (SMTP, FTP) sees a
  connection that opens and then does nothing useful. Promote a port to a real server
  here when the capture shows it matters.
* **apt must be reachable when the recipe runs** (provisioning time). The any-IP route
  is armed last, and after that this host resolves only the corpus — so a re-run that
  needs to install a package fails loudly rather than quietly. Measurement-time egress
  denial is a later slice.
