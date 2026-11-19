# pace-test — ground truth for the discovery round trip

A small, fully real twin: everything a cast could recover is written down in `te.yaml`. Deploy it,
tap it to XPress-SW-A, discover an imprint from the capture through the `XPress-SW-Reader`
axquery source, cast the imprint, and compare the cast with this bundle.

| segment | CIDR | hosts |
|---|---|---|
| `lan` | 10.72.1.0/24 | `cli` ×4 (10.72.1.2–.5), gw .1 |
| `srv` | 10.72.2.0/24 | `srv-dns` .11 (bind9, 53), `srv-web` .12 (nginx 80/443), `srv-smb` .13 (Samba 445), gw .1 |

Traffic (`tfc/default.yaml`, persona `personas/pace-client.yaml`, `drive: command`): every client
runs `dig` (8 per 5 s), `curl` http and https (2 per 10 s each), `smbclient` fetching a 1 MiB file
(2 per 30 s) and `ping` (2 per 15 s) against the servers, through gw. Tap (`tap/default.yaml`):
the `srv` segment only, so each client→server flow is captured once.

## Live (dev45, 2026-11-19)

- Deploy `pace-test-1f4a6c96`, all ops completed; tap `tap-pace-test-1f4a6c96-2bf5e29a` (applied
  through the API: the CLI's `tap deploy` does not read lab sensors from the targets file);
  traffic `tfc-pace-test-1f4a6c96-76c27269`.
- Capture: XPress-SW-A `gre-cap` +7,180 packets in 30 s (~240 pps); the dev45 GRE dump shows all
  four clients' DNS/HTTP/HTTPS/SMB to .11/.12/.13. Every client emit `ok`, 0 fails.
- Observed through the reader (19:00–20:00 UTC), per client per hour: 4,240 DNS, 550 HTTP,
  548 TLS, 386 ICMP, ~206 SMB (227 MB). Plus what the servers do on their own through gw's real
  egress: bind9 recursion to the root servers, NTP, apt over TLS.

## Round trip

Imprint `pit-7ea828d3fe-1795118400` (run `disc-1ce9965a`, 19:00–21:00 UTC, network
`net-pace-test`), cast as `pace-cast` (in place) and `pace-cast-r73` (relocated to 10.73.0.0/16
so it can stand beside this twin on the same sensor).

| axis | pace-test | cast (before fixes) | cast (after) |
|---|---|---|---|
| segments | lan, srv | lan, srv, sink | lan, srv, sink |
| hosts lan / srv | 4 / 3 | 4 / 4 (gw .1 as a host) | 4 / 3 |
| services | 53, 80+443, 445 | 53/udp, 80+443, 445 (samba **and** an echo on 445) | 53/udp, 80+443, 445 (Samba) |
| sink VMs | — | 15 | 3 (the ones a persona dials) |
| client drive | command | scripted (filler bytes) | command (dig/curl/smbclient/ping) |
| DNS / HTTP / HTTPS / SMB / ICMP per client per min | 96 / 12 / 12 / 4 / 8 nominal (≈70 / 9 / 9 / 3.4 / 6.4 observed) | 0.2 each, no HTTPS rate, no ICMP | 80 / 10 / 10 / 3.6 / 7.1 |

Remaining, by design: 53/tcp is not declared (never observed); the cast's tap plan binds every
segment, where this twin taps only `srv`; the sink stands in for the servers' real internet egress.
