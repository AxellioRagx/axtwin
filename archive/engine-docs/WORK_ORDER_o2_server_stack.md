# Work Order — O2 server-stack bring-up: dedicated VM + Caldera/Wazuh fixes (unblocks A3/A4)

> **Status: DONE — 2026-07-15.** §2 (dedicated O2-services VM), §3 (Caldera/Wazuh/Prometheus code
> fixes), and §5 (gated live bring-up + A4/A3 re-run) are all complete and live-verified. Opened by
> the 2026-07-15 gated live-verify run (CHANGELOG 2026-07-15 "O2 arc gated live-verify batch"), which
> blessed A1/A2/sensor-agnostic-`te`/SCHED but **deferred A3/A4** because the O2 server stack
> (Prometheus/Loki/Grafana/Caldera/Wazuh) would not come up healthy on the `ragx-dev` orchestrator's
> rootless-podman. Three blockers logged: `docs/PRODUCT_GAPS.md` **GAP-016** (Caldera silent 500),
> **GAP-017** (Wazuh filebeat crash-loop), **GAP-018** (stale rootlessport :9090) — **all three now
> Resolved (live-verified)**, see below and CHANGELOG 2026-07-15 "O2 server-stack live bring-up".
>
> **§3 code-writer pass (2026-07-15, CHANGELOG "O2 server-stack bring-up"):** all three §3 fixes
> landed and were locally build-validated (in-container/podman diagnosis on the author box) — §3.2
> Wazuh filebeat stub, §3.1 prometheus.yml render-artifact revert + healthcheck/port audit (all
> clean), §3.3 Caldera fix. **§3.3's documented root cause was corrected during the fix, not just
> applied:** the original `apispec`/`webargs` version-skew hypothesis was live-diagnosed and
> DISPROVEN; the real cause is an unpinned `yarl` transitive (`yarl>=1.13` rejects a `Host:` header
> containing `:`) — fixed with `yarl==1.9.4` + a build-time HTTP smoke gate probing `GET /`.
>
> **§2/§5 gated live bring-up (2026-07-15, CHANGELOG "O2 server-stack live bring-up + A3/A4 gated
> live-verify: PASSED"):** stood up VMID `102` `o2-services` on `dev45` (rootful Docker CE + compose
> v2, cloned from `img-debian-12-cloud`); all 5 services `Up (healthy)`; snapshot `o2-stack-healthy`.
> **Two follow-on fixes surfaced only on the real VM:** (1) the committed filebeat-**stub** config
> alone did NOT hold — filebeat 8.x rejects a bind-mounted config it doesn't own (uid=0), and a
> git-tracked file can't be owned that way — the durable fix is an s6 `down` file
> (`o2/wazuh/filebeat-down`) disabling the filebeat co-process outright; (2) the VM's default
> `kvm64` CPU lacked the x86-64-v2 baseline Wazuh's glibc needs — fixed with `qm set 102 -cpu host`.
> **A3 and A4 both PASSED** (Sandcat + Wazuh-agent enrollment; both Loki streams present and
> joinable) after a code-side fix to the shared `export.ship: loki` bootstrap (GAP-020 — a
> self-killing `pkill` + a QGA-reaped `nohup`, fixed with a named `systemd-run --unit=` transient).
> **Depends on:** A1 (`axrange/o2control/`, BUILT + live-verified) for the sensor side. **Unblocked:**
> `WORK_ORDER_attack_defense.md` §4 (A3, PASSED) and `WORK_ORDER_measurement.md` §3 (A4, PASSED).

> **For a subagent Pipeline session (`/dev`) + a gated live bring-up.** Authority: user
> (2026-07-15). **Mixed route:** code-writer for the `o2/caldera/Dockerfile` + `o2/compose/`
> edits (§3) → **gated** live-verifier for the VM stand-up + stack bring-up + A4/A3 re-run (§5).
> This is infra/DevOps-shaped, not engine code — the `axrange/` package is **not** touched.
> **Spec context:** `docs/DESIGN_BRIEF_o2.md` (O2 as a standing surface stood up separately),
> `o2/README.md` "Attack/defense servers", `o2/compose/docker-compose.yml`.

## 1. Goal

Get the five O2 **server** components healthy and **standing** so the A3 (attack/defense agent
enrollment) and A4 (truth+detections in Loki, coverage dashboard) live-verifies can run. Two of the
three blockers are **image/config defects that travel with the container** (Caldera, Wazuh) and must
be fixed regardless of host; the third (GAP-018) is a **host defect** cured by moving off `ragx-dev`'s
rootless podman. Design-fit: the sensors already stand as `o2_`-tagged VMs on the Proxmox cluster;
only the servers are currently squatting ad-hoc on the orchestrator dev box. This work order makes the
server surface actually *standing*.

## 2. Approach — one dedicated "O2 services" VM (NOT VM-per-component)

Stand up **a single dedicated VM** running **rootful** docker/podman + the **existing**
`o2/compose/docker-compose.yml`, snapshotted once healthy. Rationale (decided with the user
2026-07-15):

- **Fixes GAP-018's whole class.** Rootful port publishing uses real iptables — no `rootlessport`
  proxy to orphan, no port contention with the dev box's other processes. (The Caldera restart
  exit-137 was plausibly a rootless cgroup/memory quirk too — rootful removes that.)
- **Architecturally correct** (`DESIGN_BRIEF_o2`): the standing surface should not live on the
  orchestrator. Keep the **single compose file** — Prometheus scrape targets, Grafana datasources,
  Alloy→Loki, and agent→server ports are already wired there; per-component VMs would re-implement all
  of that by hand for no benefit (**rejected: VM-per-component** — wrong unit of isolation, 5×
  build/patch/monitor surface; the only component a native install would help is Wazuh, and §3.2
  fixes that in-container instead).
- **Reproducible:** build once → healthy → **snapshot the VM** = a rollback-able known-good surface;
  Caldera rebuild iteration is also cleaner on a rootful-docker host.

**Reachability constraint (load-bearing):** in-TE agents beacon *to* these servers (Sandcat→Caldera
:8888, Wazuh-agent→manager :1514/:1515, exporter→Prometheus). `a3-verify` points them at the
orchestrator's LAN IP. The O2-services VM must therefore sit on a network **routable from the TE SDN
egress** — same LAN as `dev45`, or a `dev45` guest with a routable address. Set the agent origin
(`render_sandcat_agent` / `install-defense-agent` / exporter config, and `observability.conf`
`DEPLOY_HOST`) to the VM's reachable IP. Confirm a TE guest on an egress-enabled subnet can reach the
VM's published ports before running A3.

## 3. The three fixes

### 3.1 GAP-018 — resolved by §2 (the move)
Rootful docker on the dedicated VM eliminates the stale-rootlessport-on-:9090 failure. Operational
hygiene to bake in regardless: **do not detach `… up` with `&`** (that hang left the orphan port);
bring services up foreground-with-timeout or one-at-a-time, gating each on its healthcheck.
**Reconcile the port mismatch:** `o2/observability.conf` sets `PROM_PORT=9091` but the bind failure
was on `9090` — make the compose host-mapping and the conf agree, and confirm nothing else owns 9090.

### 3.2 GAP-017 — Wazuh manager filebeat (config, host-independent)

> **DONE + live-verified 2026-07-15 — the fix EVOLVED past the stub-only plan below.** A stub
> `filebeat.yml` alone was NOT sufficient on the real O2-services VM: filebeat 8.x refuses to start
> on a bind-mounted config file it doesn't own (a uid=0 ownership check), and a git-tracked file
> can't be made to satisfy that. The durable fix mounts BOTH the stub (kept for intent/documentation)
> **and** an empty s6 `down` file (`o2/wazuh/filebeat-down` → `/etc/services.d/filebeat/down:ro`)
> that disables the filebeat service at the supervisor level — the "or disable the filebeat s6
> service" alternative named below, which turned out to be the one that actually holds. Verified
> live: manager `Up (healthy)`, all daemons up, `:55000` responding 401, a real Wazuh agent enrolled
> (ID `001`, Active). A second, unrelated blocker on the same VM (`kvm64` lacking x86-64-v2) is
> recorded in the top-of-file status block and `docs/PRODUCT_GAPS.md` GAP-017.

`wazuh/wazuh-manager:4.9.2`'s s6 init auto-starts **filebeat** (normally ships alerts to a
wazuh-**indexer**); this stack is deliberately **manager-only** (no indexer — alerts read via the
manager API :55000 / local `alerts.json`), so filebeat is unconfigured *and* unneeded and hard-exits
`code=1`, tearing the container down. **Fix:** make filebeat a no-op — mount a **stub
`/etc/filebeat/filebeat.yml`** (empty inputs + file/console output, no indexer) so it idles, **or**
disable the filebeat s6 service. Do it in `o2/compose/docker-compose.yml` (volume mount or command
override), not by switching to a native install. **Verify:** container stays `Up (healthy)`;
:1514/:1515/:55000 listen; a Wazuh agent enrolls and reports.

### 3.3 GAP-016 — Caldera silent aiohttp 500 (image, host-independent — the hard one)

> **DONE + live-verified 2026-07-15 — the `apispec`/`webargs` hypothesis below was DISPROVEN, not
> confirmed.** A live in-container diagnosis found the real cause was an unpinned `yarl` transitive
> (`yarl>=1.13` rejects a `Host:` header containing `:`, which every `Host: <host>:<port>` header
> is). Fixed with `pip install yarl==1.9.4` + the step-3 build smoke gate below (probing `GET /`,
> not `/health` — see the new note at the end of this section). Re-validated on the real
> O2-services VM (build smoke-gate → 200, `Up (healthy)`) and PROVEN by A3's Sandcat enrollment.
> Full account: `docs/PRODUCT_GAPS.md` GAP-016, CHANGELOG 2026-07-15 "O2 server-stack bring-up" +
> "O2 server-stack live bring-up + A3/A4 gated live-verify: PASSED". The step-by-step plan below is
> kept as the historical diagnosis trail, not a still-open TODO.

The `axrange/caldera:5.0.0` image boots ("All systems ready", binds 0.0.0.0:8888, passes its own
`/system/ready` :7012 check) but **every** HTTP path (incl. static `/assets/`, `/api/v2/health`)
returns `500 "Server got itself in trouble"` with **no traceback** even at `--log DEBUG` — a global
**aiohttp middleware-chain** failure. `requirements.txt` pins `aiohttp==3.9.3` + `marshmallow==3.20.1`
but leaves **`apispec` / `webargs` unpinned** → they resolved to `apispec 6.10.0` / `webargs 8.7.1`
(2023+), too new for the pinned **`aiohttp-apispec==3.0.0b2`** (~2020 beta) → its per-request
validation middleware breaks. (Not the earlier magma-UI crash — `dist/assets` present. The `builder`
plugin also fails to load — missing `go≥1.19` + python `docker` — but it isn't enabled, so it's not
the cause.)
**Fix (in `o2/caldera/Dockerfile`):**
1. **Confirm cause cheaply first** (~5 min): `pip check` in the container + reproduce one request
   in-process with a `try/except` to print the swallowed traceback. Don't guess pins blind — my
   in-container `apispec==5.2.2`/`webargs==8.0.1` guess ended in an inconclusive exit-137.
2. **Then either** pin `apispec`/`webargs` (+ any other unpinned `aiohttp-apispec` transitives) to an
   `aiohttp-apispec==3.0.0b2`-compatible set (constraints file or post-requirements `pip install`),
   **or** bump `CALDERA_VERSION` → **5.3.0** (latest 5.x; 5.0.0-era dep rot likely fixed upstream,
   reqs pinned tighter — validate its own resolution). Prefer whichever `pip check` + a rebuild proves
   clean.
3. **Add a build-time smoke gate** (the Dockerfile header already promises "build + smoke-test"):
   final build step asserts `curl localhost:8888/` returns non-500, so a broken image fails at *build*
   time, not at A3 time.
4. While rebuilding, optionally add `golang (≥1.19)` + `pip install docker` so the `builder` plugin
   loads cleanly (removes startup noise; not required for A3).
5. **Audit all five compose healthchecks** for LES-064 (a `wget`/`curl` healthcheck on a slim/
   distroless base is invalid — Caldera's already uses `python3`; confirm the others use an
   interpreter actually present in their base image). **DONE 2026-07-15: all five already valid**
   — prometheus/loki/grafana use busybox `wget` (`grafana/loki:3.3.2`'s `distroless/static:debug`
   base DOES bundle busybox incl. `wget`), wazuh uses `wazuh-control`+grep, caldera uses python3
   `urllib`. No changes needed.

**Post-fix finding — do not "fix" the probe to `/health`:** unauthenticated `GET /api/v2/health`
returns 500 on Caldera 5.0.0 even after the `yarl` fix — a separate, unrelated upstream app bug
(`docs/PRODUCT_GAPS.md` GAP-019). The step-3 smoke gate above and any future compose healthcheck
must keep probing `GET /` (anonymous-safe), not `/health`.

## 4. Sub-task ordering + the A4-early milestone

Ordered by effort × unblocking value:

| # | Task | Effort | Unblocks |
|---|---|---|---|
| 1 | §2 stand up the O2-services VM (rootful docker + compose); §3.1 clean bring-up of prometheus/loki/grafana | Low–med | GAP-018; **A4** (mostly) + the exporter half of A3 |
| 2 | §3.2 stub/disable Wazuh filebeat | Medium | Wazuh half of A3 |
| 3 | §3.3 Caldera pin-or-bump + build-smoke-gate | High | Sandcat half of A3 |

**A4-early milestone:** A4 needs only **Loki + Grafana + the standing Suricata sensor** — no Caldera,
no Wazuh. So after task 1, run the **A4** live-verify (`WORK_ORDER_measurement.md` §3) *before* the
Caldera fix. Then run **A3** (`WORK_ORDER_attack_defense.md` §4) once tasks 2–3 land.

## 5. Validation / gated live bring-up (live-verifier)

> **DONE — PASSED 2026-07-15.** VMID `102` `o2-services` on `dev45`, all 5 services `Up (healthy)`,
> snapshot `o2-stack-healthy` taken. A4 and A3 both re-run against this standing stack and PASSED
> (see CHANGELOG 2026-07-15 "O2 server-stack live bring-up + A3/A4 gated live-verify: PASSED" for
> the full evidence trail). GAP-016/017/018 flipped to Resolved (live-verified); LES-078..081 record
> what the bring-up taught.

- **Per-component health (unattended reads):** each of the five is `Up (healthy)`; endpoints answer —
  Prometheus `/-/healthy` 200, Loki `/ready` 200, Grafana `/api/health` 200, **Caldera `/` non-500**,
  Wazuh manager :55000 up + an agent enrolls on :1515.
- **Snapshot** the VM once all five are green.
- **[GATE] Re-run A4** then **A3** per their work orders' §-verify steps against this standing stack;
  flip those two headers to PASSED on success. Update GAP-016/017/018 → Resolved with evidence.
- Record whatever the bring-up teaches as `LES-NNN` (e.g. rootful-vs-rootless publishing; the
  Caldera pin set that actually works).

## 6. Out of scope / notes

- **No `axrange/` engine changes.** The sensor side (`o2control` → `o2_` sensor VMs) is already
  built + live-verified; this is purely the server surface.
- **VM-per-component is explicitly rejected** (§2) — do not split the compose stack into 5 VMs.
- **Whether the O2-services VM lives on `dev45` or a separate host** is an operator call, gated only
  by the §2 reachability constraint. A `dev45` guest with a routable IP is the path of least surprise.
- A follow-on hardening nicety (not required to unblock A3/A4): a `make o2-up` / bring-up script that
  gates each service on its healthcheck + an `o2/README.md` "Attack/defense servers" runbook, so
  future bring-ups fail loud per-component instead of silently half-up.
