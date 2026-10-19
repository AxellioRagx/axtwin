# Journal — axquery Twin-Discovery Loop

**Purpose.** Durable, shareable record of the twin-discovery loop run **through axquery** (the
Axellio Xpress / PacketXpress metadata query engine) instead of Zeek. Each loop iteration's
**outcome** is captured here as it happens; the **final report to share with others** is generated
from this log. This is the report source of record — the session scratchpad holds only working
mechanics (commands, agent dispatches).

**Audience.** A reader who knows the platform but was not in the session. Acronyms expanded on first
use; each iteration stands on its own.

**Relation to prior work.** This is the axquery counterpart of the Zeek-based CorpAPCN fidelity loop
(aximprint `docs/JOURNAL_corpapcn_fidelity.md`, closed at iteration 19). Same source network, same
"deploy a twin and measure how faithfully it reproduces the original" goal — different discovery
sensor. Direction set by the golden-twin fidelity work (`docs/design/13-golden-twin-fidelity.md`).

---

## The loop

```
axquery(v29_test metadb)  ->  aximprint ingest -> materialize (PIT-1) -> cast
        -> discovered twin_config (axRange bundle)
        -> axRange te deploy  (dev45)                         [Stage C]
        -> tap-plan binds every segment -> XPress-SW-A         [Stage D]
        -> tfc traffic (personas) drives the twin              [Stage D]
        -> XPress-SW-A captures the mirror on gre-cap
        -> axanalytics -T <window> -> fresh .axmeta metadb     [Stage E]
        -> axquery serves the fresh metadb (:19091)            [Stage F]
        -> aximprint ingest -> materialize (PIT-2)             [Stage F]
        -> aximprint drift(PIT-1, PIT-2)  = the loop OUTCOME   [Stage G]
        -> fix the twin/discovery; repeat
```

**Two repos, one loop.** axRange (`/home/rocky/axRange`) deploys and scores; aximprint
(`/home/rocky/aximprint`, golden work on the `wip/golden-twin` worktree
`/home/rocky/aximprint-axrange`) discovers and casts; fx-cap (`/home/rocky/fx-cap`) is the
PacketXpress/axquery/analytics engine.

## What it takes to run the loop via axquery (the driving question)

- **Already present, no setup:** the source metadb `/net/devsrv/home/data/v29_test/` (16 GB, 53
  `pxDb-*.axmeta.lz4` chunks of the real `corp-apcn-rdr1` capture); its prebuilt `.axi` index
  (`/home/rocky/axq-index/`); a running axquery server on `127.0.0.1:19090` over both; the fx-cap
  prebuilt binaries (`axquery`, `axanalytics`). axRange's deploy+scorecard side is
  **sensor-source-agnostic** and needs nothing new.
- **The gap that had to be closed:** aximprint's axquery ingest plugin only emitted 2 of 6 event
  types (Flow + SensorHealth) — no UDP listeners, no identity, no rich services — so a naive
  discovery produced an impoverished twin. axquery in this build actually exposes far more
  (role-classified devices, discovered subnets/VLANs, typed client-vs-server apps, per-protocol
  event detail for TCP **and** UDP, identity/LDAP).
- **Built for this loop:** extend the axquery plugin (UDP-listener fix + identity + richer
  services); fix an aximprint `cast` node-id collision that the axRange validator rejected; cast
  from the `wip/golden-twin` branch so the bundle carries the golden `axr-topology/1.5` shape
  (observations block + per-node `services[]` + six-class provenance) the scorecard reads.
- **Reused as-is:** aximprint `materialize`/`cast`/`drift`; axRange `te deploy`/`validate`/`plan`/
  `scorecard`; the proven tap-to-`XPress-SW-A` plan; XPress-SW-A's on-demand
  `axanalytics -> .axmeta -> axquery` for the re-discovery half.

## Environment

- Cluster **dev45** (PVE 9.2); baseline ~151 VMs incl. the 138-VM standing `corpapcn-twin`.
- axquery source server: `axquery 19090 --db-dir /net/devsrv/home/data/v29_test --axi-dir
  /home/rocky/axq-index --auth-disabled` (already running).
- Re-discovery sink: **XPress-SW-A** = external PacketXpress sensor, Proxmox vmid 104, mgmt
  `10.80.72.43` (ssh `fxadmin`, key `~/.ssh/id_ed25519`, passwordless sudo), capture transport
  `10.255.251.21`, decap iface `gre-cap`. Platform `axellio-xpress-platform-6.0.0`.
- venvs: aximprint `/home/rocky/aximprint*/.venv`; axRange `/home/rocky/axRange/.venv`.

---

## Iteration log

### Iteration 0 — Setup & feasibility (2026-09-08)

**Outcome: the loop is mechanically feasible end-to-end; no blocker needs the operator.**

- **Bootstrap ready.** Source metadb mounted, index prebuilt, axquery server serving it. axquery
  data model confirmed rich: `/api/subnets/discover`, `/api/devices`, `/api/entities` (typed
  server/client apps), `/api/events?type=` (TCP+UDP per-protocol), `/api/identity/users`,
  `/api/graph`. The "no UDP listeners" limit is the aximprint plugin, not the data.
- **Source network shape (live probe):** 1.42 M total entities / **1,638 local hosts**; ~12-15 real
  internal segments under a noisy 2,792-row subnet list (external IPv6 /64s + artifacts); 925
  devices with role classification inflated by the router-mirror vantage (null MACs, empty
  `/api/scope/discover`); 505 identities in the AXELLIO realm.
- **Baseline pipeline works.** Thin-plugin `ingest -> materialize -> cast` over a 2-hour window
  produced a deployable twin (26 segments, 146 hosts, 1,412 planner ops) that **passed axRange
  validate and plan**. Full 26-day ingest ~5 h; iterate with bounded windows.
- **Stage E feasibility: READY-WITH-CAVEATS.** XPress-SW-A reachable + sudo; capture live (the
  standing twin's mirror is flowing into `gre-cap`); `axanalytics`/`axquery` are on-demand (a
  windowed `axanalytics -T <start> <end>` then `axquery :19091` produces + serves a fresh metadb).
  Caveats: manual run; allocate hugepages first; 5.8 TB capture ring retains ~days-to-2-weeks so
  analyze within days; serve on the box to avoid cross-build risk.
- **Tap path proven + reusable** (segment->`XPress-SW-A` selector plan).
- **Capacity call:** a ~146-host discovered twin cannot coexist with the 138-VM standing twin, so
  the standing `corpapcn-twin-0e4f7d58` (+ tap/tfc siblings) must be destroyed to deploy — which
  also cleans `gre-cap` to the discovered twin only. User-authorized; taken as the late step.
- **In flight at journal creation:** the rich-plugin extension build (UDP + identity + cast fix) on
  the golden worktree. Its result opens Iteration 1.

---

## Per-iteration outcome template (copy one per loop)

```
### Iteration N — <short title> (YYYY-MM-DD)

**Outcome (one line): <what converged / what the drift showed / what was fixed>.**

- Discovered twin: source PIT <id>, cast params (window <from..to>, --min-occupancy X,
  --relocate P); size = <segments> segs / <hosts> hosts / <services incl. UDP?> / <identities>.
- Deploy: <deploy_id>, <op count> ops, result <COMPLETED/…>; VMs <n>.
- Tap: plan <file>, sensor XPress-SW-A, segments bound <n>; capture confirmed (<frames> frames on gre-cap).
- Traffic: tfc profile <id>, window epochs <START>..<END>, volume <pkts/bytes>.
- Analytics: metadb <dir> (<chunks> chunks), served axquery :19091.
- Re-discovery: PIT-2 <id>.
- DRIFT (PIT-1 vs PIT-2) — the outcome: segments +/- <>, hosts +/- <>, services +/- <>,
  identities +/- <>; notable structural deltas: <...>.
- Fidelity read: <what the twin reproduced faithfully / where it diverged and why>.
- Fix applied: <twin-config / plugin / cast change>, and its effect (deferred to iteration N+1's drift).
- Next: <...>.
```

---

## Final report (generated from the iteration log)

Produced at the end from the iterations above. Plan: a shareable page (Artifact) titled for the
effort, covering — what it takes to run the loop via axquery, the per-iteration outcomes, the
fidelity trend, and the residual gaps. Running summary to fill as iterations complete:

| Iter | Twin (segs/hosts) | Drift (Δ segs/hosts/svc/id) | Fidelity read | Fix |
|------|-------------------|-----------------------------|---------------|-----|
| 0    | setup + tooling   | —                           | loop feasible; rich plugin built | UDP+identity ingest, cast fixes (aximprint ca5165c) |
| 1    | 149-host / 29-seg  | hosts 161/149, segs 30/29; svc partial | topology faithful, services under-exercised | personas must exercise all declared services; trace 3000/tcp |
| 2    | exercise fix       | HTTPS 1→10, HTTP 14→23, DNS 10→14, SMB 1→2; edges 843→7778 | loop CONVERGES for Linux services; Windows Firewall blocks Kerberos/RPC/RDP | open Windows FW or Windows-capable persona; more NTP listeners |
| 3    | Windows persona + RPC FW | RPC 135 **1→17** (>intended 15); Kerberos/LDAP/GC flat | RPC recovered; the residual is NOT the firewall (connects establish) — it is **filler-byte RST**: the DC's KDC/LDAP reset the persona's junk, and axquery classes established-then-RST as REJ not server | protocol-faithful DC exercise (drive: command Kerberos/LDAP); keep baseline + exercise (don't de-scope) |

### Iteration 0 (cont.) — Tooling built: the rich axquery plugin (2026-09-08)

**Outcome: the axquery ingest now produces a faithful twin (UDP services + identity), in the golden
bundle shape.** Extension committed on aximprint `wip/golden-twin` (`ca5165c`); Supersedes the
`/api/events`-decline ruling for the identity half (Authority: user).

- **UDP-listener fix** — a UDP flow whose responder replied is now `established`, so UDP listeners
  reach `services[]` (ICMP stays `unknown`).
- **Identity** — `/api/events?type=dhcp|dns` → `IdentityEvent` (DHCP binds hostname/MAC/IP — the
  anchor a null-MAC router mirror otherwise lacks; DNS → `observations.dns.names`). The address-less
  `/api/identity/users` census stays declined (no IR carrier yet; that is reference-ingest S5).
- **Cast fixes** — a node-id suffix bug (`10.20.200.0` collapsing to collide with `.201/.204`) and a
  duplicate `domain-ready` barrier when a segment held >1 DC (now clamped to one forest root/realm,
  observed count kept in the sidecar).
- Tests +15 (all stub HTTP), suite green; doc-stats clean.

**Rich vs thin twin (same 2h window `1780398299..1780405499`, PIT `pit-395a61f04e-1780405200`):**
`ir_identity` 0→**5302**; UDP services none→**53/67/88/123/137/161/389/546**; dns-resolver nodes
0→**30**; segments/node-specs 26/65→**40/109**; bundle pre-golden→**axr-topology/1.5** + observations
+ six-class provenance. axRange **validate PASS + plan rc0** (149 VMs @0.8, 162 @0.5).

**Iteration 1 subject:** this rich 2h twin. A wider-window twin is a later iteration.

### Iteration 1 — deploy the axquery-discovered twin, capture, re-discover, drift (2026-09-08) [IN PROGRESS]

**Subject:** the rich 2h-window twin, PIT `pit-395a61f04e-1780405200`, relocated to `10.200.0.0/16`.
Bundle preserved at `twin-configs-cast/corpapcn-disco/`.

- **Discovered twin @0.8 = 149 VMs**: 44 generic-endpoint, 36 heavy, 15 internet-sink, 12 unroled,
  12 corp-workstation, 10 light, 9 web-tier, 4 dns-resolver, 3 ws-light, 1 router (gw), 1
  file-server, 1 domain-controller, 1 ws-heavy. Plan rc0: 1227 ops / 180 barriers / 2958 edges.
- **Pre-deploy cluster:** dev45 = 151 VMs (143 running), ~138 of them the standing `corpapcn-twin`.
- **Decision — destroy the standing `corpapcn-twin-0e4f7d58` (+ cascade tap/tfc) before deploy.**
  Required for capacity (13 baseline + 149 fits; cluster has run 196) AND to clean `gre-cap` so the
  re-discovery captures only the discovered twin. User-authorized; supersedes the handoff's "do NOT
  destroy" (ends scorecard run-1's live subject — its numbers are already recorded).
- _Outcomes below as they land: destroy, deploy, tap, traffic window, analytics, re-discovery
  (PIT-2), DRIFT(PIT-1,PIT-2), fix._

- **Destroy DONE (rc0, 16:44Z):** Result DESTROYED, **304 resources removed, 0 not cleared**, no
  guard violation. Cluster **151 → 13 VMs**. Standing twin + its tap + tfc gone; O2 surface, XPress
  sinks, and templates untouched (tier-tag protection held).

- **Deploy DONE (rc0, 17:56Z):** deploy_id `pit-395a61f04e-1780405200-3caf94a1`, Result COMPLETED, **1407 ops ok / 0 failed / 0
  halted**. Cluster 13 → **162 VMs** (154 running) — all 149 twin VMs up + provisioned (incl. DC
  promotion, web-tier provision/run-script).

- **Discovered twin shape (PIT-1, the drift baseline):** 149 hosts / 29 emitted segments (biggest
  seg-10-80-72-0 ×22, seg-10-80-20-0 ×19, seg-10-20-193-0 ×15). Declared services span the identity
  + UDP surface the thin plugin missed: DNS 36 hosts (53 tcp+udp), NTP 17 (123/udp), Kerberos 12
  (88/tcp), RDP 15, HTTPS 24 (443), HTTP 17 (80), plus SMB/LDAP/DHCP; 55/149 declare a listen set.
  (inventory `os` field unpublished; the DC is Windows per the deploy.)
- **Tap plan:** twin-configs-cast/corpapcn-disco/tap-xpress.yaml = 29 emitted segments → XPress-SW-A
  (built from live inventory, so it matches the deployed twin exactly).

- **Traffic-profile finding + fix:** the cast's `tfc.yaml` keys 145/147 assignments on pre-fan-out
  node-group names (e.g. `corp-workstation-10-80-20-core`) that the deploy renames to suffixed
  host ids (`core01..NN`), so they fail to resolve ("not a host in target twin"); only the 2
  role-based assignments worked. Worked around with a ROLE-based profile `tfc-roles.yaml`
  (corp-workstation / dns-resolver / domain-controller / file-server / generic-endpoint /
  unroled-endpoint / web-tier → personas), driving **133 hosts** (15 internet-sinks + router
  skipped). GAP for the report: cast tfc node-naming does not survive count fan-out.

- **Tap DONE (rc0, 18:06Z):** `tap-pit-395a61f04e-1780405200-3caf94a1-b08c6d60`, 29 segments
  mirrored → XPress-SW-A. The 29 "capture UNVERIFIED (external sink, no QGA heartbeat)" warnings are
  by-design; capture confirmed out-of-band on gre-cap.

- **Traffic live (18:25Z):** tfc-roles deployed rc0, personas driving 133 hosts. gre-cap capture
  rate **13,631 pkts/s (~147 Mbit/s)** — healthy. Capture window START=1788892041
  (2026-09-08T18:27:21Z); soaking 20 min for periodic personas to cycle + inter-segment
  flows to accumulate.

- **Comparison method (key loop finding).** aximprint `drift` keys entities on **hostname/MAC, not
  IP** (`ir/keys.py`), so relocation to 10.200.x is fine — BUT the twin's VMs carry **synthetic**
  hostnames/MACs unrelated to the source, so `drift(PIT-1, PIT-2)` joins nothing (all churn). `drift`
  is a TEMPORAL same-network comparator, not source-vs-synthetic-twin. The prior Zeek loop compared
  capture **SHAPE** via a structural fingerprint (`JOURNAL_corpapcn_fidelity.md`) — address- and
  identity-agnostic, the right model here. **Stage G = a STRUCTURAL comparison** of PIT-1 (source)
  vs PIT-2 (re-discovered twin): #segments, hosts-per-role, service/port distribution, role edges.
  `drift` is run too, reported honestly. (Golden-twin direction already deprecated traffic-shape
  fidelity for the closed-spec `te scorecard` — the fuller tool, an optional bonus this iteration.)

- **Soak DONE (20 min):** gre-cap captured **18,024,366 pkts / 24.4 GB** at sustained 15,020 pps.
  Analytics window `[1788892041, 1788893292]`.
- **Stage E:** `axanalytics -T <window> --output-dir /scratch/disco-metadb` on XPress-SW-A.

- **Stage E snag (finding):** `axanalytics -T <past-start> <past-end>` with NO input-source flag
  does NOT read the historical pxfs window — it wedged (0 CPU, no pxfs fds, no chunks) and became
  unkillable (D-state on a hung axfs read). Agent 2's documented `-T START END` recipe was untested
  and is wrong. axanalytics prod mode is a LIVE FOLLOWER (`-T -1 -1`, reads axfs_device from
  px.ini). Also observed: persona traffic tapered 15k→~1.1k pps after the initial burst. Delegated
  the correct-invocation fix (historical read via --axfs-device/--stream-ids, or live re-capture
  after re-boosting tfc) to a focused agent. Loop-lesson for the report: producing the fresh metadb
  is a live-follow capture, not a historical-window replay.

- **Stage E+F DONE (Route B, live follow).** Route A (historical `-T START END`) is BLOCKED:
  axanalytics unconditionally pins its main thread to **CPU 29** (the live afxdp capture core) whose
  kernel migration path is deadlocked, so every start hangs in `affine_move_task` and survives
  `kill -9` (that was the wedged 911149). **Fix:** `systemd-run -p AllowedCPUs=0-23` makes the pin
  fail fast (EINVAL) and axanalytics rolls back gracefully; `--stream-ids 0` is required for the
  disk read. Captured a fresh **12-min live window** (tfc-roles re-boosted): metadb
  `corpapcn-disco-1788896340000.axmeta.lz4` (24 MB, **10.9M pkts, 827 entities, 396k events**).
  axquery serves it (persistent unit `axr-disco-axquery`, survives the session) at
  **http://10.80.72.43:19091** — /api/info OK, **121 local hosts**, DNS/NTP/HTTP/SSH server types
  present (Kerberos/LDAP/SMB sparse in the low-rate window). Report findings: (1) the analytics
  engine has a CPU-pinning deadlock needing a cpuset workaround; (2) the fresh metadb is a live
  re-capture, not a historical replay of the soak window.

### Iteration 1 — OUTCOME (Stage G): the axquery loop closes end-to-end; twin topology is faithful, its service surface is under-exercised

**One line: the twin-discovery loop runs end-to-end through axquery instead of Zeek. The
re-discovered twin reproduces the deployed twin's TOPOLOGY faithfully, but its own synthetic traffic
exercises only a fraction of its declared SERVICES — the concrete fidelity gap the loop exists to find.**

**`aximprint drift` REFUSED (rc=2)** — "different vantages (corp-apcn-rdr1 vs corpapcn-disco)": a PIT
is one tap point's view, so cross-vantage source-vs-twin would report every vantage difference as
change. It suggests `merge --axis vantage` first. This confirms `drift` is a same-vantage TEMPORAL
comparator; source-vs-twin fidelity needs the STRUCTURAL comparison below.

**Structural comparison — re-discovered twin (PIT-2, 12-min axquery capture) vs deployed twin
(intended) vs source (PIT-1, 2h):**

| dimension        | re-discovered PIT-2 | deployed twin (intended) | source PIT-1 |
|------------------|--------------------:|-------------------------:|-------------:|
| local hosts      | 161                 | 149                      | 248          |
| segments         | 30                  | 29                       | 39           |
| corp-workstation | 16                  | 16                       | 16           |
| domain-controller| 1                   | 1                        | 2            |
| web-tier         | 11                  | 9                        | 10           |
| generic-endpoint | 103                 | 90                       | 145          |

- **TOPOLOGY recovered well:** host count (161 vs 149), segment count (30 vs 29), and the major roles
  (corp-workstation exact, DC exact, web-tier/generic close) all reproduce.
- **SERVICE fidelity PARTIAL** (hosts observed serving / hosts intended):
  recovered — HTTP 80 (14/17), DNS 53 (tcp 10/16, udp 9/20), NetBIOS 138/udp (3/3);
  MISSED / undersampled — **HTTPS 443 (1/24), RDP 3389 (0/15), Kerberos 88 (1/9), RPC 135 (1/15),
  NTP 123/udp (1/17)**, SMB 445 (1/3).
- **Anomaly:** 3000/tcp observed on **16 hosts**, in neither the intended twin nor the source — an
  undeclared persona artifact.

**Root cause of the service gap:** a declared listen port is invisible to a traffic-based
re-discovery unless a CLIENT actually connects to it. The role personas emit only a subset of client
traffic, so RDP/Kerberos/HTTPS/NTP/RPC listeners are never exercised on the wire. Confound: PIT-2 is
a 12-min low-rate window vs PIT-1's 2h — under-sampling amplifies the gap.

**Fixes identified ("fix the twin" — for Iteration 2):**
1. **Traffic completeness (primary):** personas must emit CLIENT traffic to every declared service
   (an RDP client to the RDP hosts, Kerberos auth, HTTPS/NTP/RPC clients), not just a subset. The
   `drive: command` protocol-faithful driver is the vehicle; peer/service coverage is the gap.
2. **The 3000/tcp artifact:** trace which persona puts undeclared traffic on 3000 and reconcile.
3. **Capture window:** a longer, higher-rate soak (the persona traffic tapered 15k→1k pps after its
   burst) reduces the under-sampling confound; and axanalytics is live-follow only (no historical
   replay, CPU-29 deadlock), so the capture must run concurrently with sustained traffic.

**VERDICT: Iteration 1 PASSED — the axquery-driven discovery loop is proven end-to-end** (discover →
deploy → capture → re-analyze → re-discover → structurally compare), and it produced an actionable
fidelity finding on its first run.

### Iteration 2 — fix: exercise the twin's internal services (2026-09-08) [re-loop running]

**Root cause (from Iteration 1's emit model):** the cast personas only dial `internet-sink` roles
(northbound), so the twin's internal listeners never received a client connection and were invisible
to a traffic-based re-discovery. The internet-sink services were exercised; the internal ones
(web-tier, dns-resolver, DC, file-server, workstation) were not.

**Fix (committed `cda245ed`):** the `disco-exercise` persona makes **127 client hosts dial each
internal serving role on its declared ports** — web-tier 80/443, dns-resolver 53(tcp+udp)/123, DC
88/389/445/3268, file-server 445, corp-workstation 3389/135 — east-west, `peer_scope: all`,
`optional: true`. Assigned via `tfc-exercise.yaml` (127 drive-persona ops, plans rc0), deployed as a
**second tfc** alongside `tfc-roles` (the twin stays up). The internal servers with real listeners
(dns-resolver bind9/chrony, web-tier nginx, DC AD services, file-server) should now be re-discovered;
RDP/RPC on the Linux workstations have no real listener, so those stay refused-flow only.

**Re-loop:** deploy exercise traffic → sustain → re-capture `disco-metadb2` (live-follow, CPU-29
cgroup workaround) → serve axquery `:19092` → re-discover PIT-3 → structural compare vs Iteration 1.
- _Outcome (service-recovery delta) recorded when the re-loop reports._

- **Iteration 2 deploy note:** the exercise deploy resumed the SAME tfc deploy_id (260 ops = 133
  baseline + 127 exercise). **16 `disco-exercise` ops FAILED on the corp-workstation hosts** — they
  are **Windows**, and `disco-exercise` is `os_family: linux` (QGA "No such file or directory"
  running a Linux command). Non-blocking: the 90+ Linux generic-endpoints run `disco-exercise` and
  dial ALL internal servers — including the Windows workstations' own RDP/RPC/SMB listeners (real
  Windows services) — so every internal service still receives client traffic. Finding: a persona's
  `os_family` must match the target host OS, or the exercise persona needs a Windows branch.

### Iteration 2 — OUTCOME: the loop CONVERGES for Linux services; Windows Firewall is the next-layer blocker

**One line: exercising the internal services recovered every Linux-served one (HTTPS 1→10, HTTP
14→23, DNS 10→14, SMB 1→2) — the fidelity loop demonstrably converges — while the Windows-served
ones (Kerberos, RPC, RDP) stayed flat because Windows Firewall blocks the Linux clients' inbound
connections.**

**Edges: 843 (Iter 1) → 7,778 (Iter 2), ~9x** — the exercise traffic (111 Linux hosts dialing the
internal servers) created the internal-service connections that were missing.

**Service recovery (hosts observed serving), Iteration 1 → Iteration 2 / intended:**

| service        | Iter 1 | Iter 2 | intended | verdict |
|----------------|-------:|-------:|---------:|---------|
| HTTPS 443/tcp  | 1      | **10** | 24       | recovered (real nginx) |
| HTTP 80/tcp    | 14     | **23** | 17       | recovered (exceeds intended) |
| DNS 53/tcp     | 10     | **14** | 16       | improved (real bind9) |
| SMB 445/tcp    | 1      | **2**  | 3        | improved (Samba/AD) |
| DNS 53/udp     | 9      | 9      | 20       | flat |
| Kerberos 88/tcp| 1      | 1      | 9        | FLAT — Windows Firewall |
| RPC 135/tcp    | 1      | 1      | 15       | FLAT — Windows Firewall |
| RDP 3389/tcp   | 0      | 0      | 15       | FLAT — Windows Firewall |
| NTP 123/udp    | 1      | 1      | 17       | flat — one Linux listener |

**Diagnosis of the residual:** the recovered services are all served by **Linux** hosts (web-tier
nginx, dns-resolver bind9/chrony, file-server Samba) whose listeners **accept** the exercise
connects, so they are re-discovered as servers. The flat services live on **Windows** hosts (DC:
Kerberos/LDAP; workstations: RPC/RDP), and Windows Firewall blocks inbound from the Linux exercise
clients on other subnets — the connects are refused, so no established server is observed. The 16
`disco-exercise` failures (os_family: linux on Windows workstations) were the first sign of the same
Windows boundary. Also: the `corp-workstation` role classification jumped to 118 as the new dial
pattern re-shaped the flow fingerprint, and the 3000/tcp artifact persists.

**VERDICT: Iteration 2 PASSED — the fidelity loop converges.** A targeted fix (exercise the internal
services) measurably improved service fidelity on the next run, and the residual is precisely
diagnosed rather than mysterious.

**Fix for Iteration 3 (Windows boundary):** (1) open Windows Firewall on the DC + workstations for
the exercised ports (Kerberos 88, LDAP 389, RPC 135, RDP 3389, SMB 445), OR give `disco-exercise` a
Windows os_family branch so Windows hosts run it (native RDP/RPC among workstations bypass the
inbound-from-elsewhere block); (2) more Linux NTP listeners; (3) trace the 3000/tcp artifact.

### Iteration 3 — fix: exercise the Windows services natively + reachability truth (2026-09-09)

**Root cause carried in (from Iteration 2):** the Windows-served ports (Kerberos 88, LDAP 389/3268,
RPC 135, RDP 3389) stayed flat. Iteration 2 attributed this to Windows Firewall blocking the Linux
exercise clients. **Iteration 3 tested that hypothesis on the wire and found it WRONG for the DC** —
see the reachability finding below.

**Fix applied (working tree, committed this iteration):**
1. **`disco-exercise-win`** — a WINDOWS-branch persona (`os_family: windows`, scripted PowerShell
   `TcpClient` driver) assigned to `corp-workstation` in `tfc-exercise.yaml`. It makes the twin's own
   16 domain-joined Windows workstations dial the DC (88/389/3268/445 TCP) and each other (135/3389)
   as native Windows-to-Windows domain traffic. This SUPERSEDES Iteration 2's removal of
   `corp-workstation` from the profile (that removal existed only because `disco-exercise` is
   `os_family: linux` and a `/bin/sh` launch over the Windows QGA failed — the 16 failed ops).
2. **A scoped, reversible firewall opening** (`axr-disco-rpc-135`, inbound TCP 135, `RemoteAddress
   10.200.0.0/16`, `Profile Any`) applied via QGA PowerShell (`New-NetFirewallRule`) to all 16
   workstations — see the reachability finding for why this ONE opening, and only this one.

**Phase A — deploy.** The stuck Iteration-2 `tfc` (`…-6858cee6`, 62/260 ops, 1 orphaned RUNNING,
stale lease + resource-lock both expired ~7.2 h) needed **no manual clearing**: `acquire_lease` /
`acquire_resource_locks` reclaim expired rows automatically, and `reset_incomplete_ops` reset the
orphan. Re-applying `tfc-exercise.yaml` force-reset the 127 east-west ops and de-scoped the `cast-*`
baseline (27 rows → `destroyed`). Result **COMPLETED, 127/127 exercise ops, 0 failed** — and **all 16
`disco-exercise-win` Windows ops COMPLETED** (Iteration 2's 16 failures are fixed; the os_family
mismatch is gone).

**Phase B — reachability truth on the wire (the crux of this iteration).** Observed directly on the
twin's tap ports on dev45 (gre-cap itself is un-`tcpdump`-able: the PacketXpress `pxcap_afxdp_f` XDP
program owns it, which is why axanalytics reads the afxdp ring, not AF_PACKET):
- **DC (10.200.20.9) AD ports ESTABLISH.** 30 s on the DC tap: **88 → 181 SYN-ACK, 389 → 171, 3268
  → 80, 445 → 128** (each with an equal RST count — the DC replies SYN-ACK, then RSTs after the
  persona's filler). The DC's NIC is classified **Public** (an NLA-timing artifact — NLA probes
  before AD/DNS is ready at boot), yet AD ports are reachable anyway. **So the connects are NOT
  firewall-blocked** — Iteration 2's diagnosis was wrong for the DC. **No DC firewall change made.**
- **Workstation RPC 135 was DROPPED.** Pre-fix, 47 inbound SYN → **0 SYN-ACK, 0 RST** (silently
  dropped by the workstation's DomainAuthenticated-profile firewall, even though 135 has a listener).
  The SOURCE observed workstations serving 135, so making it reachable (as a corp GPO would) is
  fidelity-correct → applied `axr-disco-rpc-135`. Post-fix: **8 inbound SYN → 8 SYN-ACK, 0 RST** —
  RPC now establishes cleanly.
- **RDP 3389 has no listener** (the workstation image runs no RDP; it listens on 135/139/445 only) and
  the SYNs are dropped. **Left off, per the runbook** — an image-level fidelity truth, not forced.
- Correction to the record: the `corp-workstation` hosts are **Windows Server 2022** (DomainRole 3,
  member servers), not win11; the DC is a real PDC (`corp.axr.lab`, DomainRole 5). Both are
  domain-joined.

**Phase C — capture, re-discover, compare.**
- Deploy: same twin `pit-395a61f04e-1780405200-3caf94a1` (149 VMs), tap unchanged
  (`tap-…-b08c6d60`, 29 seg → XPress-SW-A).
- Analytics: `axanalytics -T -1 -1 --stream-ids 0` live-follow (`systemd-run -p AllowedCPUs=0-23`
  CPU-29 workaround) → **`/scratch/disco-metadb3/corpapcn-disco3-1788929980002.axmeta.lz4`** (42.7 MB,
  **971 s ≈ 16.2 min** window — longer than Iteration 2's 12.7 min; **2.25 M pkts / 747 k events**).
  Fewer packets than Iter2's 12 M but MORE events, because the `cast-*` baseline (high-volume
  northbound bulk) was de-scoped, leaving pure east-west exercise (the connects that classify
  servers). Served **axquery :19093** (`axr-disco-axquery3`).
- **Ingest finding — the exercise now overruns the plugin's page size.** `aximprint ingest` refused
  at the default `PAGE_ROWS=5000`: the 127 synchronized personas plus long-lived half-open RDP/dropped
  connects hold **>5000 concurrent connections/second**. Raised to 100000 to ingest (a build constant,
  reverted after). Iter2 never tripped this (its 5000-ingest succeeded), so Iter2 captured every
  concurrent connection too and the comparison stays apples-to-apples — but the overrun is itself an
  Iteration-3 signal: the east-west exercise is now genuinely dense.
- Re-discovery: **PIT-4 `pit-89859f772a-1788930000`** (144 entities, 143 local, 30 segments; 2 hourly
  windows straddling the capture boundary). Artifacts: `/home/rocky/axr-disco-artifacts/{imprint3.duckdb,ir3.store}`.

**Structural comparison (PIT-4 Iter3 vs PIT-3 Iter2):** segments 30 = 30 (stable), DC 1 = 1,
file-server 1 = 1 (topology faithful). Hosts 144 vs 245 and the role mix shifted (corp-workstation
118→71, generic-endpoint 0→35) — the de-scoped baseline drove fewer distinct talkers and re-shaped the
flow fingerprint the role classifier keys on.

**SERVICE-RECOVERY TABLE (hosts observed serving; metric = PIT `ir_entity.listen_ports`, identical
for both iterations):**

| service         | Iter 2 | Iter 3 | intended | verdict |
|-----------------|-------:|-------:|---------:|---------|
| RPC 135/tcp     | 1      | **17** | 15       | **RECOVERED — exceeds intended** |
| Kerberos 88/tcp | 1      | 1      | 9        | FLAT — filler-byte RST (see below) |
| LDAP 389/tcp    | 1      | 1      | 1        | flat (at intended) |
| GC 3268/tcp     | 1      | 0      | 1        | down — filler-byte RST |
| SMB 445/tcp     | 2      | 2      | 3        | flat (real Samba only) |
| RDP 3389/tcp    | 0      | 0      | 15       | flat — no listener (image truth) |
| HTTP 80/tcp     | 23     | 13     | 17       | down — baseline de-scope |
| HTTPS 443/tcp   | 11     | 9      | 24       | down — baseline de-scope |
| DNS 53/tcp      | 14     | 4      | 16       | down — baseline de-scope |
| DNS 53/udp      | 10     | 5      | 20       | down — baseline de-scope |
| NTP 123/udp     | 47     | 0      | 17       | down — was a symmetric-NTP over-count in the baseline |

**Fidelity read.** The Windows persona + RPC firewall opening **recovered RPC 135 (1→17), the first
Windows service ever recovered in this loop** — filler-byte traffic works there because the RPC
endpoint mapper accepts the connection and closes it cleanly (post-fix wire: 8 SYN-ACK, **0 RST**),
so axquery classifies the workstations as servers. **Kerberos/LDAP/GC did NOT recover** even though
the DC clearly accepts hundreds of connects: the DC's KDC/LDAP read the persona's junk payload and
**RST** it, and axquery classifies an established-then-RST flow as **rejected, not server**. Confirmed
directly: on :19093 the DC (10.200.20.9) is still just `NicPort`, while the real-daemon file-server is
`SMBServer`. **This corrects Iteration 2's diagnosis** — the residual is not Windows Firewall (the
connects establish); it is **protocol-faithfulness** — filler bytes cannot reproduce a service whose
protocol validates its input. This is exactly `CLAUDE.md`'s protocol-faithful-traffic gap, now proven
on the wire against a real AD DC. The Linux-service regression (HTTP/HTTPS/DNS/NTP down) is the cost
of de-scoping the `cast-*` baseline per the runbook: Iter2 measured baseline + exercise, Iter3 measured
exercise-only (2.25 M vs 12 M pkts); part of it is de-inflation (Iter2's HTTP 23 exceeded the 17 real
web-tier hosts; Iter3's 13 ≈ truth), part is genuine under-sampling.

**Residual + diagnosis.** (1) **Kerberos/LDAP/GC on the DC — the primary residual — need
protocol-faithful clients** (a real Kerberos/LDAP exchange, e.g. the `drive: command` driver), not any
filler-byte persona; no firewall change can fix it because the connects already establish. (2) **RDP
3389** is an image truth (no listener) — correctly left off. (3) The **baseline de-scope** cost the
Linux-service counts — run baseline + exercise together next time. (4) The `3000/tcp` artifact (16 in
Iter2) is gone in Iter3 (also a baseline effect).

**Fix for Iteration 4.** (a) **Protocol-faithful DC exercise** — give the DC-directed emits a
`drive: command` Kerberos/LDAP path (or a real domain auth loop) so the DC's AD flows COMPLETE instead
of RST; this is the only thing that can recover Kerberos 88 / LDAP 389 / GC 3268. (b) **Do NOT
de-scope the `cast-*` baseline** — apply the exercise ALONGSIDE it so the Linux services stay
recovered while the Windows ones are exercised. (c) A longer / higher-rate window to cut the
under-sampling confound.

**VERDICT: Iteration 3 PARTIAL PASS.** The fix **measurably moved Windows-service fidelity** — RPC
135 went 1→17 (exceeding intended), the first Windows service recovered in the loop, and the 16
Windows persona ops that failed in Iteration 2 all succeeded. More valuably, it **replaced Iteration
2's incorrect "Windows Firewall blocks" diagnosis with the true one**: the connects establish, and the
Kerberos/LDAP residual is filler-byte RST classification — a protocol-faithfulness gap, not a
reachability one. Full Windows-service recovery is now a well-defined Iteration-4 target (real AD
protocol traffic), not a mystery.
