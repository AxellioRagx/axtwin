# corp-twin capture-rate ladder — XPress-SW-A (2026-10-01, running log)

Running log, written as the work happens so a successor can resume. Implementation (Claude Code),
user away; the authorizations quoted below are the user's (2026-10-01). Raw evidence (samples,
per-step JSON, correctness JSON, scripts) lives in the session scratchpad
`/tmp/claude-1000/-home-rocky-axRange/2ac78199-a062-422d-8bbf-b2db42ef8269/scratchpad/capture-rate/`
(not committed; the tables below carry the numbers).

## Scope and rules (from the task)

- Twin `corp-twin-07cfcdb2` (gate 0.9, 106 VMs), tap `tap-corp-twin-07cfcdb2-8176c5ad` (24
  gretaps, 80 tapped ports → XPress-SW-A 10.255.251.21 over `o2v7dc98`), tfc
  `tfc-corp-twin-07cfcdb2-785b1a06` (84 persona instances, profile `tfc/gate90.yaml`).
- Allowed writes: traffic profile re-applies; tap re-apply; mirror path tuning on the twin's own
  gretaps/bridges and the transport legs serving them; sensor tuning over SSH (`fxadmin`). Never:
  destroy / redeploy / `te deploy --update` / power-cycle; never touch `svc-engines-24131435`, its
  taps (`tapg_84e687`, `tapg_14167b`, `tapg_d1b207`, `tapg_6413a9`, bridges `tevea261`,
  `tev69fc2`) or VM 102.
- Abort a rung on ANY counted drop in the corp-twin chain, worse-than-noise correctness, dev45 >
  80 % CPU (1-min avg), PSI mem some > 10 % or swap growth, or any dead persona heartbeat.

## Tooling (scratchpad)

| Script | What it does |
|---|---|
| `sampler.py` | Every 10 s: one SSH read of dev45 (`/proc/net/dev`, `tc -s filter` on the 87 tapped ports, gretap qdiscs, `/proc/stat`, PSI, swap, `/proc/net/snmp`, softnet) and one of the sensor (`/proc/net/dev`, `ethtool -S ens19`, pxswcapture's `axcapture-stats.json`, `/proc/net/snmp`, softnet, `/proc/stat`). Each host read is time-stamped separately (the 2026-08-14 harness lesson). Appends `samples.jsonl`. |
| `analyze.py` | Window → per-stage rates + every drop counter delta + CPU/mem verdict. |
| `pcapsample.sh` + `correctness.py` | `pxextract` a 60 s window of the sensor's OWN stored capture (minus svc-engines' 10.66/16), one tshark pass, ratios. |
| `heartbeats.py` | The engine's own persona heartbeat (`o2control.traffic.heartbeat_instances`) over all live `drive-persona` rows. |

**Counters in the verdict** (corp-twin chain only): tapped VM ports rx_drop/rx_errs; the 24 twin
bridges rx/tx drop+errs; tc mirred `dropped`/`overlimits` on the 80 corp-twin ports; the 24
corp-twin gretaps tx_errs/tx_drop + qdisc dropped; dev45 `IpFragFails` and softnet dropped;
`o2v7dc98` drops; sensor vhost `tap104i1` tx_drop; sensor `ens19` rx_drop/rx_fifo/rx_errs and
`ethtool` rx_drops/rx_xdp_drops; `gre-cap` rx_drop/rx_errs; sensor softnet dropped;
`IpReasmFails`; **pxswcapture `dropped` (all reasons)**; axfsd `SegmentsDiscarded`.

**Excluded, with reason:** svc-engines' own gretaps `tapg_84e687`/`tapg_d1b207` lose ~12k
frames/s (`tx_errs`, pre-DF-fix gretaps, GAP-112) — that is svc-engines' unhealed tap, hard-limited
as untouchable; its surviving frames do reach the same sensor and are part of the sensor's load,
and are filtered out of the correctness pcaps (`not net 10.66.0.0/16`).

### Correctness method — why not tshark's `tcp.analysis.*`

The twin is tapped at every hop (host ports, router legs, 802.1Q trunks between the switch VMs)
and the sensor strips GRE (`gre_strip = true`), so one TCP segment appears 2–4× in the merged
capture. tshark keys TCP by IP 5-tuple and called **48 %** of baseline TCP frames
"retransmissions" and 3.8 % dup-ACKs — all of them cross-port copies. `correctness.py` instead
tracks TCP per **observation point** (eth.src, eth.dst, VLAN, 5-tuple direction), drops an exact
copy (same IP ID/seq/len/ack/flags — a frame switched across two tapped trunks keeps MACs, tag and
IP ID; a real retransmission carries a fresh IP ID), and keeps a hole list per point so a
**late** segment (re-ordered) is told apart from a real retransmission, and a hole never filled
by the end of the sample (= a frame the capture never got) is counted on its own (`tcp_holes`).
Handshakes, DNS query/response pairing and TLS ClientHello/ServerHello are deduplicated at the IP
4-tuple level. Truncation: `frame.cap_len < frame.len` and `tcpdump -r … | grep truncated`;
oversize: `frame.len > 1514 + 4/tag` (a GRO/GSO artefact on the capture path).

The synthetic personas carry tagged filler bytes, not real TLS (`axrange-persona <id> https …`
then zeros), so "TLS handshake completion" has no ClientHellos to measure (0 in every sample);
reported for completeness, not used as a gate.

## A. Baseline (B0) — 2026-10-01 05:42–05:55 UTC, 730 s

The "~6 kpps" is pxswcapture's own `packetRate` in the sensor's `axcapture-stats.json`
(`swcapture_gre-cap`), read 3,740–6,786 pps at the first reads; over the 730 s window it is
**6,541 pps / 48.1 Mbit/s**.

| Stage | pps | Mbit/s |
|---|---:|---:|
| 80 tapped VM ports, rx skbs (GSO counted once) | 3,101 | 46.2 |
| tc mirred (wire segments; tc counts `gso_segs`) — **offered** | 6,267 | — |
| 24 corp-twin gretaps tx (+ 7,458/s outer IP fragments created) | 4,540 | 46.4 |
| sensor vhost `tap104i1` tx (outer, incl. fragments + svc-engines) | 10,256 | 51.3 |
| sensor `ens19` rx (+ 3,708 reassemblies/s) | 10,251 | 51.3 |
| sensor `gre-cap` rx (decap) | 6,543 | 48.1 |
| **pxswcapture captured** | **6,541** | **48.1** |

Every verdict counter delta **0** over 730 s. dev45 CPU 25.5 % avg (1-min max 29.2 %), PSI mem
0, swap 0, MemAvailable ≥ 104 GiB. Sensor CPU 10.7 % avg; one core (cpu26) is pinned 100 % by
pxswcapture's `afxdp_q0` busy-poll thread at every load (not a saturation signal by itself).
Heartbeats: **84/84 alive**.

Sensor facts read at baseline: pxswcapture `capture_mode afxdp-copy`, `xdp_attach_mode skb`
(generic XDP on `gre-cap`), **one** XSK queue bound (`rx_queues_bound 1`), `capture_mtu 1500`,
`gre_strip true`; cumulative since its start: 25,077 drops, all reason `queue` (none in our
window). `ens19`: virtio, 8 combined queues, rx ring 1024 (max 1024), GRO/GSO/TSO **on**,
`maxmtu 1500`. Transport `o2v7dc98` MTU 1500 → every full-size mirrored frame becomes two outer
fragments (≈ 1.6 outer packets per captured frame).

Correctness, two 60 s samples (B0-s1 05:48:34–05:49:34, B0-s2 05:53:08–05:54:08; ~370 MB,
~380 k frames each; svc-engines filtered out):

| Ratio | B0-s1 | B0-s2 |
|---|---:|---:|
| malformed | 0.02421 | 0.02363 |
| expert_error | 0.01929 | 0.01915 |
| truncated / oversize / tcpdump "truncated" | 0 / 0 / 0 | 0 / 0 / 0 |
| inner IP fragments / reasm fail | 0 / 0 | 0 / 0 |
| tcp_retrans | 0.00113 | 0.00129 |
| tcp_reorder (late segment, capture-path reordering) | 0.0546 | 0.0540 |
| tcp_holes (never filled) | 0 | 0 |
| tcp_dupack / zero_window | 0 / 0 | 0 / 8e-6 |
| hs_incomplete (window edges) | 0.0187 | 0.0199 |
| dns_unanswered / orphan responses | 0.0220 / 0 | 0.0187 / 0 |

Byte mix by top dissector: `data` (persona filler on 443/80) ~70 %, NBSS/SMB ~14 %, raw TCP ~8 %,
TLS-labelled ~5–6 %, SSH 0.75 %, CLDAP 0.5 %, DNS 0.4 %.

**Noise rule.** A ratio passes when `step ≤ max(B0) + max(|B0-s1 − B0-s2|, 0.15 × mean(B0),
floor)`; floor 1e-4 for ratios that are 0 at baseline, except `truncated`, `oversize`,
`ip_reasm_fail` and `tcp_holes` which must stay **0** (they are direct capture-damage signals).

**Reading of the baseline.** Nothing in the chain is loaded: the 6.5 kpps is simply what the 84
scripted personas offer. The first limit is the traffic generator itself, so the ladder must
raise the offered load; the capture path's known limits (one XSK queue / one `afxdp_q0` thread on
generic XDP; 1500-MTU transport forcing fragmentation; the per-gretap single-queue pfifo) only
show up under load.

## B. Ladder design (decided 2026-10-01 05:56, evidence-driven)

- **Traffic is the only lever that moves the first limit** (nothing in the chain is loaded at
  6.5 kpps), so sensor/mirror levers are applied when a counter or a correctness ratio points at
  them, one at a time, rather than blind.
- `intensity:` on an assignment is informational only (`o2control/traffic.py` never applies it),
  so the knobs are the persona emits. Generator `gen_variant.py K` (scratchpad) writes
  `personas/<id>-capratex<K>.yaml` + `tfc/caprate-x<K>.yaml` from `tfc/gate90.yaml`: same 84
  assignments; per persona, cadence × c_p with c_p = min(K, max(1, 0.75/B_p)) where B_p is the
  share of time the single-threaded client loop is already busy (messages × gap / interval); the
  rest of the factor (K/c_p) goes into message size of the byte-carrying TCP emits only
  (https/http/smb). So TLS/HTTP/SMB bytes/s scale by exactly K everywhere; DNS/NTP/Kerberos/LDAP
  keep realistic message sizes and scale by c_p ≤ K. Three personas are already loop-saturated at
  K=1 (`cast-domain-controller-local` B=2.2, `cast-corp-workstation-light-local` 1.4,
  `cast-corp-workstation-local` 0.94) — their small-message emits cannot speed up at all.
- Apply = `axrange tfc deploy twins/corp-twin/tfc/caprate-x<K>.yaml --images deploy-dev45` (the
  whole-profile apply; ~9 min for 84 instances, GAP-117 restarts every unit).

## C. Ladder

### R1 — traffic ×2 (`tfc/caprate-x2.yaml`) — 05:56:52 apply, 06:06:07 completed, soak to 06:16:57 (650 s)

Command: `axrange tfc deploy twins/corp-twin/tfc/caprate-x2.yaml --images deploy-dev45` → status
completed, 84/84 heartbeats alive.

| | B0 | R1 ×2 |
|---|---:|---:|
| offered (tc wire segs) pps | 6,267 | 10,807 |
| captured pps / Mbit/s | 6,541 / 48.1 | **11,056 / 87.0** |
| outer frags created/s (dev45) | 7,458 | 13,745 |
| counted drops | 0 | **0** |
| dev45 CPU avg / 1-min max | 25.5 / 29.2 % | 27.0 / 29.4 % |

Correctness (R1x2-s1, 06:15:57–06:16:57, 655 k frames): every ratio inside the band **except
`tcp_reorder` 0.0661 vs limit 0.0627** (B0 0.0546/0.0540). (A first sample flagged one DNS
response at the window's first second as "orphan" — an edge artefact; `correctness.py` now
ignores exchanges cut by the window's first/last 2 s and the baseline was recomputed with the
same rule; the B0 numbers above are unchanged except `hs_incomplete` 0.0182/0.0197.)

**Reading.** The reorder is a capture-path artefact, not twin behaviour: full-size frames
(1514/1518 B) encapsulate to 1542/1546 B on the 1500-MTU transport and travel as two outer
fragments that the sensor must reassemble, while small frames (≤ ~1450 B) go whole — so a small
segment overtakes the big ones sent just before it (seen directly in the baseline: a 1200 B
segment with IP ID n+2 captured ahead of the two 1448 B segments n, n+1 of the same flow at the
same port). The share of full-size frames grows with K (bulk TCP scales with message size), so the
artefact grows with load. **Verdict: counters clean, correctness one ratio over → do not climb on
it; apply the lever the evidence names (transport fragmentation) at the same ×2 load.**

### R2 — mirror path: stop fragmenting on the transport (at ×2 load) — 06:19–

**R2-try1 (06:19:08–06:20:17) — ABORTED, reverted in 70 s.** Change, scoped to corp-twin's own
tunnels only (svc-engines' gretaps to the same sensor stay on the unmarked 1500 path, verified with
an unmarked `ping -M do -s 1572` → "Message too long"):

```
# dev45 (root)
ip link set tap104i1 mtu 1600                                   # sensor's transport leg (host side)
ip route add 10.255.251.21/32 dev o2v7dc98 src 10.255.251.1 mtu 1600 table 251
ip rule add fwmark 0x5ca1 lookup 251 pref 2510
ping -c3 -M do -s 1572 -m 23713 10.255.251.21                   # 1600 B marked: 3/3 replies (guest ens19 accepts > its 1500 MTU)
for g in <the 24 corp-twin tapg_*>; do ip link set $g type gretap fwmark 0x5ca1; ip link set $g mtu 1500; done
#   (the changelink re-derives the gretap MTU to 1458 -- set it back to 1500 every time)
```

Within 10 s pxswcapture logged **`dropped` reason `queue`: 1,071, then 12,319 and 10,072 per
10 s** (24,227 counted drops in total) and the captured rate fell to 38–54 k/10 s. The 18 s of
capture from that window (extracted afterwards) holds frames up to **3,728 B** (~3 % of frames
> 1518 B): with whole 1546 B outer packets now reaching the sensor, `ens19`'s **GRO** (on, as is
`gre-cap`'s) coalesced GRE-encapsulated TCP segments into super-frames — wire-fidelity damage AND
an XSK queue overrun. Fragmentation had been masking it (IP fragments are never GRO'd).
Revert (06:20:17): `for g in …; do ip link set $g type gretap fwmark 0; ip link set $g mtu 1500; done`
→ clean again from the 06:20:26 sample on (the rule/route/tap MTU stay; unmarked traffic does not
use them).

**R2a (06:21:45) — sensor: capture offloads off (the ratified tuning-ladder rule 2).**

```
# XPress-SW-A (fxadmin, sudo)
ethtool -K ens19 gro off rx-gro-hw off       # revert: ethtool -K ens19 gro on rx-gro-hw on
ethtool -K gre-cap gro off                   # revert: ethtool -K gre-cap gro on
```

190 s soak at ×2, still fragmenting: 11,080 pps captured, every counter 0, sensor CPU unchanged.

**R2b (06:25:28) — re-apply the fwmark/MTU change on top of GRO-off.** First minute: dev45
`IpFragCreates` **0/s** (was 13,745/s), sensor `IpReasmOKs` 0/s, `ens19` 11,682 pps (was 17,914 —
−35 % outer packets for the same captured rate), captured 11,693 pps, every counter 0.

**R2b result — soak 06:25:33–06:35:45 (612 s), ×2 load: CLEAN.** Captured **11,297 pps / 89.3
Mbit/s**, every counter 0, dev45 CPU 26.3 % (1-min max 27.9 %), 84/84 alive. Correctness
(R2b-s1, 670 k frames): **`tcp_reorder` 0.0000** (B0 0.054, R1 0.066) — the capture is now in
wire order; every other ratio within the band (retrans 0.0007, holes 0, truncated/oversize 0,
dns_unanswered 0.0116, hs_incomplete 0.0204). **Kept:** GRO off on `ens19`/`gre-cap`; the
corp-twin-only fwmark → table 251 → MTU-1600 path to the sensor. The new baseline for the
reorder ratio is 0, which makes the noise band for it [B0] conservative (any return of
fragmentation-driven reorder still shows).

Revert of the whole R2 change set (if ever needed):
```
# dev45
for g in <24 corp-twin tapg_*>; do ip link set $g type gretap fwmark 0; ip link set $g mtu 1500; done
ip rule del pref 2510; ip route flush table 251; ip link set tap104i1 mtu 1500
# XPress-SW-A
sudo ethtool -K ens19 gro on rx-gro-hw on; sudo ethtool -K gre-cap gro on
```

### R3 — traffic ×4 (`tfc/caprate-x4.yaml`) on the R2b path — 06:36:53 apply, 06:46:09 completed, soak 06:46:14–06:56:29 (615 s): CLEAN, but the generator stalls

| | B0 | R2b ×2 | R3 ×4 |
|---|---:|---:|---:|
| offered (tc wire segs) pps | 6,267 | 11,025 | 15,356 |
| captured pps / Mbit/s | 6,541 / 48.1 | 11,297 / 89.3 | **15,635 / 129.0** |
| `ens19` outer pps | 10,251 | 11,311 | 15,621 |
| counted drops | 0 | 0 | **0** |
| dev45 CPU avg / 1-min max | 25.5 / 29.2 | 26.3 / 27.9 | 26.1 / 27.7 % |

Correctness (R3x4-s1, 940 k frames): all within the band (malformed 0.0177, retrans 0.00047,
reorder 0, holes 0, hs_incomplete 0.0186, dns_unanswered 0.0109). 84/84 alive.

**Why ×4 offered only ×2.4 the packets — a twin finding, not a capture one.** The persona logs
(`/var/log/axrange/persona-*.log`, read over QGA) show the byte-carrying emits are cut short by
the servers: the scripted client sends tagged filler, the sink's **caddy** on 443 and the file
server's **smbd** on 445 read it, reject it and close with a **RST** (`[Errno 104] Connection
reset by peer`; sink `TcpOutRsts` 38.5 M, file server 1.3 M since boot; no listen overflows). On
`generic-endpoint-heavy-10-80-20-core01`: the original `cast-generic-endpoint-heavy` logged
182,657 failed messages over 42,587 firings since 2026-09-28, and **`corp-file-share-client` has
sent 524,288 bytes in total since 2026-09-28 with 494,783 failed messages** — every SMB
connection is reset after the first window of data. The SMB/TLS bytes on the wire are those
first windows. So growing the message size (the R1/R3 rule) mostly grows the part that is never
sent; only **more connections** raise the load. (Run 4's "SMB to the file server is real" in
WORK_ORDER_corp_twin §11 is true of the wire — bidirectional 445 flows — but not of the
transfers the persona authors; recorded here, not fixed: persona/server content is out of scope.)

**Generator v2 (decided 06:59).** Keep every message's size and count; scale the firing rate
only, and remove the intra-firing gap so a firing costs only its I/O time: every emit's
`cyclic:Ns` → `burst:(N/K)s` (the driver's `burst:` = "fire every <dur>, messages back-to-back",
`scripted._resolve_emit_schedule`). The client loop sleeps 1 s per pass, so an emit cannot fire
faster than about once per 1–2 s: emits whose N/K would fall below 2 s are clamped to 2 s and
reported (they scale by N/2 instead of K). Mix effect: per-connection shape unchanged; messages
within a firing are back-to-back instead of 0.05–0.5 s apart.

### R4 — traffic burst ×4 (`tfc/burst-x4.yaml`, generator v2, min interval 1 s) — 06:59:48 apply, 07:09:03 completed, soak 07:09:08–07:19:21 (613 s): capture CLEAN, traffic health NOT

| | R3 ×4 (size) | R4 burst ×4 |
|---|---:|---:|
| offered (tc wire segs) pps | 15,356 | 23,877 (34.5 k in the first minute) |
| captured pps / Mbit/s | 15,635 / 129.0 | **24,161 / 182.3** |
| counted drops | 0 | **0** |
| dev45 CPU avg / 1-min max | 26.1 / 27.7 | 27.6 / 29.3 % |

Capture-fidelity signals (two samples, 1.46 M frames each): holes 0, reorder 0, truncated 0,
oversize 0, malformed 0.0245/0.0244, expert 0.0189/0.0188 — all inside the band. 84/84 alive.

But the first judge flagged `tcp_retrans` 0.00148 (limit 0.00147), `tcp_dupack` 1.1–1.3e-4
(baseline 0) and one DNS orphan. Attribution (per-flow, `dbg3.py`):
- **Baseline "retransmissions" were all SYN retries** to endpoints that never answer — six
  workstations → `10.101.10.2:3000`, the DC → a workstation's 135/3389. They scale with the
  connection-attempt rate, not with loss, so the metric was confounded by the mix. **Method
  correction (07:30):** SYN retries are now their own ratio, `tcp_syn_retry` per SYN-opened
  4-tuple (B0 0.0470/0.0542, R4 0.0481 — unchanged), and `tcp_retrans` counts data
  retransmissions only (B0 0 / 9e-6). Baseline recomputed from the kept B0 pcaps.
- With that split, R4 shows **real twin-side TCP stress**: `tcp_retrans` (data) **3.1e-4** and
  18 dup-ACKs, **all between the Windows corp-workstations and the Windows DC 10.180.16.3 on
  88/389/3268** (DC CPU not high). The scaled Windows workstation personas (Kerberos/LDAP/GC
  bursts, 8 × 4 KiB back-to-back) are what the DC starts failing to keep up with. The capture
  shows it faithfully; it is the traffic that lost protocol health.

**Verdict R4: not clean (traffic health).** Next rung keeps the three Windows corp-workstation
personas (`cast-corp-workstation-{local,light-local,heavy-local}`, 10 instances) **unscaled**
(their original ids and gate90 behaviour) and scales everything else — generator `--keep`;
files `tfc/burstw-x<K>.yaml` + `personas/*-burstwx<K>.yaml`. The mix consequence: workstation
Kerberos/LDAP/SMB-to-DC stay at baseline volume while everything else grows.

### R5 — burst ×8, Windows workstations unscaled (`tfc/burstw-x8.yaml`) — 07:36:27 apply, 07:45:42 completed, soak 07:45:47–07:55:53 (606 s): CLEAN

Captured **35,944 pps / 299.1 Mbit/s** (first minute after the apply 51.8 k / 425 Mbit/s — the
84 restarted loops fire in step at first, then de-phase). Every counter 0; dev45 CPU 26.6 %
(1-min max 29.8 %); sensor 11.1 %; softnet `squeezed` 107 over the soak (budget exhaustion, not
a drop). Correctness (R5bwx8-s1, 2.17 M frames): all in band — data retrans 5.9e-5, dup-ACK
1.1e-5, syn_retry 0.011, reorder/holes/truncated/oversize 0, hs_incomplete 0.0193,
dns_unanswered 0.0047. 84/84 alive. Byte mix: filler-on-443/80 ("data") 76.8 %, NBSS/SMB 16.3 %,
raw TCP 5.2 %, TLS-labelled 0.8 %, SSH 0.5 %, DNS 0.25 %.

### R6 — burst ×16, Windows workstations unscaled (`tfc/burstw-x16.yaml`) — 07:58:56 apply, 08:07:50 completed, soak 08:07:55–08:18:06 (611 s): capture CLEAN, traffic health NOT

Captured **60,430 pps / 524.5 Mbit/s** sustained (first minute 85.9 k / 746 Mbit/s), every
counter 0 over 36.3 M packets; dev45 CPU 28.9 % (1-min max 29.7 %); sensor 11.6 %; 84/84 alive.
Capture-fidelity ratios all clean (3.67 M frames: holes/reorder/truncated/oversize 0, malformed
0.0120). **But data retransmissions 2.7e-4 (limit 1.1e-4)** — attributed: clients → **sink
10.250.234.66:443** (and the sink's own retransmits back). The sink (2 vCPU, caddy) reads load
average ~5 with 75 % system time at this rung: the single external sink every TLS/HTTP emit
converges on is now the twin's limit. Not fixable within the authorization (VM resources are not a
traffic/tap/mirror knob). **Verdict: not clean → back off; bisect at ×12.**

### R7 — burst ×12, Windows workstations unscaled (`tfc/burstw-x12.yaml`, fractional intervals) — 08:39:08 apply, 08:47:35 completed, soak 08:47:40–08:57:46 (606 s): capture CLEAN, traffic health marginal → NOT clean

(A first ×12 file rounded intervals to whole seconds, which made the dominant 16 s emits 1 s =
identical to ×16; it was regenerated with 0.1 s resolution before this apply — the first apply
08:29:30–08:38:22 was superseded unmeasured. Note the client loop sleeps 1 s per pass, so
intervals between 1 and 2 s are quantized by the pass time.)

Captured **43,396 pps / 367.9 Mbit/s**, every counter 0, dev45 CPU 26.8 %, 84/84 alive.
Capture-fidelity clean in both samples. Data retransmissions (client ↔ sink:443): sample 1
**9.6e-5** (limit 1.09e-4, pass), sample 2 **1.39e-4** (fail) + dup-ACK 2.3e-5. The sink is at
its edge here. **Verdict: not clean (traffic health, one of two samples).** Ceiling candidate for
the run-4 mix = **R5 ×8** (35.9 kpps / 299 Mbit/s), to be re-applied and soaked ≥ 30 min.

### P — capture-chain probes beyond the twin's sink limit (NOT mix candidates)

Purpose: R6 showed the chain clean at 60 kpps sustained / 86 kpps bursts while the twin's sink
was already failing; the probes measure the chain's own headroom under the NEW sensor settings
(GRO off costs per-packet CPU) by adding load that bypasses the sink: temporary persona
`caprate-probe-echo-b<N>` (bulk TCP, 24 × 64 KiB to EVERY web tier's 9080/tcp echo responder,
`burst:<N>s`) on the 18 `cast-generic-endpoint` hosts + the 9 unassigned unroled endpoints (no
host gets a second copy of a persona), on top of `burstw-x16`. Files `personas/caprate-probe-echo-b{8,2}.yaml`,
`tfc/probe-b{8,2}.yaml` — temporary, deleted after the probes.

**P1 — `tfc/probe-b8.yaml` (burstw-x16 + 27 probe instances, burst:8s) — 09:05:21 apply,
09:16:30 completed, soak 09:16:35–09:26:42 (607 s): CAPTURE CHAIN CLEAN at ~1 Gbit/s.**
Captured **102,351 pps / 907.2 Mbit/s** sustained (first minute 143 k pps / 1.26 Gbit/s) —
**61.4 M packets, every drop counter 0** (tc mirred, 24 gretaps, transport, vhost, `ens19`,
`gre-cap`, softnet, pxswcapture, axfsd). dev45 CPU 31.2 % (1-min max 31.7 %), sensor 12.5 %,
softnet `squeezed` 758 (budget, not a drop). Capture fidelity over 6.23 M frames: holes 0,
reorder 0, truncated 0, oversize 0, malformed 0.0071. 111/111 heartbeats alive. Traffic health
NOT kept, as expected for a probe: `hs_incomplete` **0.277** — the web tiers' echo responders
(listen backlog 128, 1–2 vCPU) do not complete a quarter of the probe's handshakes.

**P2 — `tfc/probe-b2.yaml` (probe at burst:2s) — 09:35:32 apply, 09:46:25 completed, soak
09:46:30–09:56:3x (600 s): CAPTURE CHAIN CLEAN at ~2 Gbit/s.** Captured **218,443 pps /
1,963.5 Mbit/s** sustained (first minute 254 k pps / 2.29 Gbit/s) — **131.0 M packets, every
drop counter 0**. dev45 CPU 40.3 % (1-min max 41.0 %), sensor 15.6 % (softnet `squeezed` 4,112 —
NAPI budget exhaustion, not loss), PSI 0. Fidelity over a 15 s / 3.53 M-frame sample: holes,
reorder, truncated, oversize all 0. Probe traffic health: `hs_incomplete` 0.44 (web-tier echo
responders swamped). 111/111 alive.

**Sensor persistence (09:52):** GRO-off made reboot-proof on XPress-SW-A —
`nmcli connection modify "Wired connection 2" ethtool.feature-gro off ethtool.feature-rx-gro-hw off`
(saved only, no reapply; runtime was already off) and drop-in
`/etc/systemd/system/gre-cap.service.d/20-gro-off.conf` (`ExecStartPost=/usr/sbin/ethtool -K
gre-cap gro off`) + `systemctl daemon-reload`. Revert: `nmcli connection modify "Wired
connection 2" ethtool.feature-gro "" ethtool.feature-rx-gro-hw ""`; `rm` the drop-in;
`daemon-reload`; `ethtool -K ens19 gro on rx-gro-hw on; ethtool -K gre-cap gro on`.

**P3 — `tfc/probe-b1.yaml` (probe at burst:1s) — 10:03:07 apply, 10:13:58 completed, soak
10:14:03–10:24:08 (600 s): CHAIN LIMIT REACHED.** Sustained **309,969 pps / 2,784.7 Mbit/s**
captured (first minute 366 k pps / 3.10 Gbit/s), 186.0 M packets; **pxswcapture `dropped` 7,141
(reason `queue`) in the single 10 s sample ending 10:23:36** (3.05 M packets in that interval,
0.23 %), then a further **40 `tap104i1` tx_drop** (the sensor's vhost ring) at ~10:24:50 while a
4.6 GB `pxextract` was also reading the store. dev45 CPU 47.5 % (1-min max 48.1 %), sensor 18.6 %
with `afxdp_q0` pinned at 100 % and softnet `squeezed` 8,225. Everything upstream (tc, 24 gretaps,
transport) stayed at 0. Fidelity in a 13 s / 4.09 M-frame sample: holes/reorder/truncated/oversize
0. 111/111 alive. **Reading: the capture chain's own ceiling under these settings is ~300 k pps /
~2.7 Gbit/s, set by pxswcapture's ONE AF_XDP socket on the single-queue, generic-XDP `gre-cap`
(one `afxdp_q0` thread) with the sensor's vhost ring next.** Probes ended here (backed off).

## D. Final candidate soaks

### F1 — ×8 again (`tfc/caprate-max.yaml` = R5's `burstw-x8` body) — 10:32:46 apply (also stops the 27 probe instances), 10:41:56 completed, soak 10:42:01–11:16:51 (2,090 s): capture CLEAN, traffic health NOT (1 of 3 samples)

Counters over **2,090 s / 74.8 M packets: every drop counter 0**; captured **35,785 pps / 297.8
Mbit/s**; dev45 CPU 29.1 % (1-min max 35.3 %), PSI 0, swap 0; sensor 11.4 %; 84/84 alive
(the 27 probe rows retired by the narrowing apply). Three 60 s samples (2.19 M / 2.15 M / 2.17 M
frames): capture fidelity 0/0/0 everywhere; data retransmissions **4.9e-5, 8.6e-5, 1.65e-4**
(limit 1.09e-4) — sample 3 again client ↔ sink 443/22. (Sample 1's two "orphan" DNS responses
were the DC answering SERVFAIL for `settings-win.data.microsoft.com` 7.2 s into the window — the
queries pre-date the sample by the Windows DNS recursion timeout. Method correction: an orphan
response is counted only ≥ 10 s into a sample; B0 recomputed, still 0; sample 1 re-judged within
noise.) **×8 sits on the sink's edge intermittently → back off one notch: ×6.**

**Independent event observed (not caused here, not touched):** svc-engines' two Windows VMs
powered themselves off — `864812` (te-cbd9a1-dc) at **07:45:31 UTC** and `506017`
(te-cbd9a1-win-member) at **07:57:04 UTC**: guest-initiated (no PVE task, no OOM line; the
`.scope` deactivated normally, `qmeventd` cleanup). Nothing in this work addressed those vmids
(tfc applies target corp-twin rows only; host changes were limited to corp-twin's 24 gretaps,
`tap104i1`, one policy rule and table 251). Left stopped per the hard limit; for the user.

### F2 — ×6 (`burstw-x6`, now the body of `tfc/caprate-max.yaml`) — 11:22:49 apply, 11:31:42 completed, soak 11:31:47–12:04:37 (1,970 s): CLEAN — THE CEILING

| | B0 baseline | **F2 ceiling (×6)** |
|---|---:|---:|
| captured at XPress-SW-A (pxswcapture) | 6,541 pps / 48.1 Mbit/s | **29,945 pps / 244.2 Mbit/s** |
| offered (tc mirred wire segments) | 6,267 pps | 29,690 pps |
| sensor `ens19` outer packets | 10,251 pps (1.57/frame, fragmented) | 29,944 pps (1.00/frame) |
| packets over the soak / counted drops | 4.77 M / 0 | **59.0 M / 0** |
| dev45 CPU avg / 1-min max; PSI; swap | 25.5 / 29.2 %; 0; 0 | 26.6 / 31.5 %; 0; 0 |
| sensor CPU avg | 10.7 % | 11.1 % |
| heartbeats | 84/84 | 84/84 |

Correctness, three 60 s samples (1.80 M / 1.83 M / 1.79 M frames), all **within noise**: holes,
reorder, truncated, oversize, inner fragments 0 in all three; data retransmissions 1.5e-5 /
5.2e-5 / 1.07e-4 (limit 1.09e-4 — the margin to the sink's edge is thin); dup-ACK 0 / 1.3e-5 /
1.3e-5; syn_retry 0.012; hs_incomplete 0.019; dns_unanswered 0.005–0.006 (B0 0.019–0.022);
malformed 0.0175 (B0 0.024). Left running.

## E. Result

**Ceiling for corp-twin's run-4 mix: 29,945 pps / 244.2 Mbit/s at XPress-SW-A (4.6× / 5.1× the
6.5 kpps / 48 Mbit/s baseline), zero drops end to end over 59 M packets, protocol health within
baseline noise.**

Bottleneck chain, in the order met:
1. **Persona generator shape** (B0→R3): the scripted client's single loop with 0.05–0.5 s
   inter-message gaps, and caddy/smbd resetting the filler streams after the first window (so
   message size does not move load). Lever: `burst:` cadence (gap 0) + firing rate.
2. **Transport fragmentation** (R1): 1500-MTU transport → two outer fragments per full-size frame →
   5–6 % of TCP captured out of order. Lever (kept): corp-twin-only 1600-byte path
   (`twins/corp-twin/scripts/mirror-mtu-path.sh`), which first required —
3. **Sensor GRO** (R2-try1): `ens19`/`gre-cap` GRO merged GRE-carried TCP into ≤3.7 KB frames →
   24,227 pxswcapture `queue` drops. Lever (kept, persisted): GRO off on both.
4. **Windows DC** (R4): scaled Windows workstation Kerberos/LDAP/GC bursts → data retransmissions
   and dup-ACKs on 88/389/3268. Lever: those three personas stay unscaled.
5. **The single external sink** (R6/R7/F1: ×16, ×12, ×8): 2 vCPU caddy at load ~5 → client ↔
   sink:443 retransmissions. **This is what sets the ceiling.** Not changeable within the
   authorization (VM resources).
6. **Capture chain itself** (probes, not mix candidates): clean at 102 k pps / 0.91 Gbit/s (P1) and
   **218 k pps / 1.96 Gbit/s for 10 min (P2, 131 M packets)**; first loss at **~305 k pps / 2.7
   Gbit/s** (P3): pxswcapture `queue` 7,141 in one 10 s interval (single AF_XDP socket on
   single-queue generic-XDP `gre-cap`, `afxdp_q0` at 100 %), then 40 `tap104i1` tx_drop.

To go beyond 30 kpps with this mix: more sink capacity (vCPUs) or more sinks (the cast's four
`internet-sink-N`, D3 in the work order), real protocol clients instead of filler (so servers
don't reset), and a DC that keeps up. The capture path has ~7× headroom (to ~200 k pps) before
the next limit, which needs multi-queue capture on the sensor.

### Changes kept (state left)

| Where | Change | Revert |
|---|---|---|
| dev45 | `ip rule … fwmark 0x5ca1 lookup 251 pref 2510`; table 251 `10.255.251.21 dev o2v7dc98 src 10.255.251.1 mtu 1600`; `tap104i1` MTU 1600; 24 corp-twin gretaps `fwmark 0x5ca1` (MTU 1500, `nopmtudisc ignore-df` kept) — runtime only | `sh twins/corp-twin/scripts/mirror-mtu-path.sh revert` |
| XPress-SW-A | `ethtool -K ens19 gro off rx-gro-hw off`; `ethtool -K gre-cap gro off`; persisted: NM profile "Wired connection 2" `ethtool.feature-gro off`, `ethtool.feature-rx-gro-hw off`; `/etc/systemd/system/gre-cap.service.d/20-gro-off.conf` | see R2a + P2 above |
| corp-twin tfc | `tfc/caprate-max.yaml` applied (84 instances: 74 on `*-burstwx6` personas, 10 Windows workstations on their gate90 personas) | `axrange tfc deploy --twin corp-twin --variant gate90 --images deploy-dev45` |
| repo (uncommitted) | `twins/corp-twin/tfc/caprate-max.yaml` (registered in `twin.yaml`), 13 `personas/*-burstwx6.yaml`, `scripts/mirror-mtu-path.sh`, `o2/tcpdump-sensor/README.md` section, this log | — |

Reverted / removed: R2-try1 fwmark (06:20:17); all intermediate ladder files (`caprate-x{2,4,8}`,
`burst-x4`, `burstw-x{8,12,16}`, probe personas/profiles) deleted after their rungs; sensor
scratch pcaps deleted. `tfc/default.yaml` and `tfc/gate90.yaml` untouched. Tap plan never
re-applied (not needed). svc-engines: gretaps/bridges/rows untouched (state DB mtime
2026-09-25, its gretaps have no fwmark); its Windows VMs `864812`/`506017` powered themselves
off at 07:45/07:57 UTC (see F1) and were left as they are.

### CHANGELOG-ready summary (not added to CHANGELOG)

> **2026-10-01 — corp-twin capture-rate ceiling measured on XPress-SW-A; mirror path unfragmented,
> sensor GRO off** (Authority: implementation (Claude Code), user-authorized scope). Live ladder on
> `corp-twin-07cfcdb2`: 6.5 kpps / 48 Mbit/s → **29.9 kpps / 244 Mbit/s** with zero drops over 59 M
> packets and protocol health within baseline noise (`tfc/caprate-max.yaml`, new variant). The
> ceiling is the twin's single 2-vCPU sink (and the Windows DC for workstation traffic), not
> capture: probes held 218 kpps / 1.96 Gbit/s drop-free and first lost packets at ~305 kpps
> (pxswcapture's single AF_XDP queue). Two capture-path fixes kept: GRO off on the sensor's
> transport NIC and decap device (with whole outer packets, GRE GRO built ≤3.7 KB frames and
> overran the XSK queue), and a corp-twin-only 1600-byte mirror path (fwmark → table 251), which
> takes TCP captured out of wire order from 5–6 % to 0. Method: tshark per-observation-point TCP
> analysis (the multi-hop tap duplicates every segment 2–4×, which tshark reads as 48 %
> "retransmissions"). Finding: the scripted personas' filler is reset by caddy/smbd after the first
> window — `corp-file-share-client` has delivered 512 KiB in total since 2026-09-28.
> Record: `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`.
