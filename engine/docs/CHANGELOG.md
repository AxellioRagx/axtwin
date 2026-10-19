# Design Changelog

Track **material design decisions** for the cyber-range project. Implementation fixes (typos, minor parameter tuning, small corrections) don't need entries here — they live in git history. This changelog is for decisions that future me, Claude (chat), and Claude Code should know about.

Format: reverse chronological. Each entry follows the template defined in `docs/WORKFLOW.md`.

**Archive:** entries dated 2026-01-01 through 2026-06-30 (102 entries, 2026-06-30 → 2026-05-21) were moved out verbatim on 2026-09-16 to keep this file to a manageable size — see [`docs/changelog/2026-H1.md`](changelog/2026-H1.md). Entries dated 2026-07-01 through 2026-09-16 (129 entries, 2026-09-16 → 2026-07-01) were moved out verbatim on 2026-09-30, same reason — see [`docs/changelog/2026-H2-part1.md`](changelog/2026-H2-part1.md). This file holds 2026-09-19 onward.

---

## Entry Template

```markdown
## YYYY-MM-DD — Short descriptive title

**Authority:** design (chat session) | implementation (Claude Code) | user | retrofit

**What changed:**
- Concrete, specific statement of the change.

**Why:**
- Rationale; what motivated this decision.

**Affected docs:**
- `path/to/file.md` — which section

**Supersedes:** [optional — if this reverses or replaces an earlier entry]

**Links:** [optional]
```

## Authority Markers — Meaning

| Marker | Meaning |
|---|---|
| `design (chat session)` | Decision made during a Claude chat session |
| `implementation (Claude Code)` | Discovered/decided during Claude Code implementation |
| `user` | User-authored decision (outside any AI session) |
| `retrofit` | Retrospective entry; lowest confidence |

---

<!-- BEGIN CHANGELOG ENTRIES — newest first -->

## 2026-10-06 — IPv6 fidelity gap recorded (GAP-124); dual-stack segments work order opened

**Authority:** user (the ruling: "record the gap and open a work order for dual-stack segments"; the
IPv6 persona-emit piece explicitly NOT in it) / implementation (Claude Code) for the source analysis,
the work order's design and its recommendations. Design decision; no code changed.

**What changed:**
- `docs/PRODUCT_GAPS.md` **GAP-124**: twins carry no IPv6. Live on `corp-twin-3dae3188`, 20 s on
  three gretaps (~248,000 frames) held one IPv6 frame (an RS). The source bundle carries 218 v6
  nodes / 291 v6 edges (1.9 % of flows, 0.1 % of bytes) but no v6 segment — the cast's segmenter is
  IPv4-only, every v6 node is unplaced, and every layer downstream faithfully builds IPv4-only.
- New `docs/WORK_ORDER_dual_stack_segments.md` (OPEN): optional `networks[].cidr6`/`gateway6` +
  `nets[].ip6` (STR-020 widened, NET-120..160 — specified, not yet applied to the version-locked
  specs), v6 host addresses derived from the v4 host offset, a new `configure-ipv6` op
  (OP-HOST-110, QGA) so a standing twin adopts it as Added-only via `te deploy --update`, radvd on
  the Debian router (`service router-advert` on VyOS), and v6 containment (WAN `disable_ipv6`, an
  `ip6` egress table, off-twin v6 REJECTed with ICMPv6 admin-prohibited) as a required live-verify
  gate, devlab first. Open for the user: D1 prefix (recommend a deterministic ULA over the real
  prefix), D2 placement (recommend VLAN20 only, as the source shows), D3 delivery, D5 version,
  D6 RA flags, D7 standing-router upgrade.
- **Correction to the brief, measured:** the v6 nodes are not all corp addresses under
  `2001:489a::/32` — that block is a remote destination (`*.sharepoint.us`). The corp's own prefix
  is `2603:300b:64a:ba00::/64` (13 hosts, mostly privacy IIDs); two of them tie by DNS name to the
  domain controllers on `10.80.20.0/24`. MAC correlation: 0 hits (the bundle's nodes carry no MAC).

**Why:** IPv6 is ~8 % of the source's nodes and ~2 % of its flows; at 0.1 % of bytes it was
invisible to mix and capture-rate work, but a defense keyed on dual-stack, ICMPv6, v6 DNS or QUIC
over v6 sees none of it in the twin. Nothing on record ever ruled IPv6 out — a gap, not a decision.
The emits piece (v6 persona traffic and a v6-serving sink) is a separate future work order.

**Affected docs:** `docs/PRODUCT_GAPS.md` (GAP-124), `docs/WORK_ORDER_dual_stack_segments.md` (new),
`docs/SESSION_STATE.md` (next steps).

**Affected code:** none.

## 2026-10-05 — corp-twin redeployed on dev45 after the reboot (te + gate90 taps + caprate-max traffic); capture-to-disk off

**Authority:** user (request "redeploy the same twin along with personas and traffic profiles and
taps"; target "dev45, go"; profile "caprate-max" incl. re-applying the mirror-MTU path) /
implementation (Claude Code) for execution. No code changed.

**What changed:**
- dev45 was rebooted for maintenance earlier the same day; the range was empty. Redeployed, all ops
  completed, 0 failed: te `corp-twin-3dae3188` (plan `corp-twin-7de6524d`, identical plan id to the
  destroyed `corp-twin-07cfcdb2`; `DEPLOY_MIN_OCCUPANCY=0.9`, 106 VMs, 1118/1118 ops, ~47 min);
  tap `tap-corp-twin-3dae3188-2191b611` (gate90, 81/81, 24 segments -> XPress-SW-A); tfc
  `tfc-corp-twin-3dae3188-1d08cc84` (caprate-max, 84/84 drive-persona). `te egress` measured
  CONTAINED (25 LAN legs ok) before taps/traffic.
- `mirror-mtu-path.sh apply` re-run: the 24 gretap names were identical under the new deploy ids, so
  the script's hardcoded list held and was not edited. Still runtime-only (GAP-119). One 60 s window:
  gretap tx errors/drops 0 on all 24, no IP fragmentation, `gre-cap` rx ~29.7 kpps / 244.4 Mbit/s,
  zero drops at ens19/gre-cap/softnet/reassembly (a kernel-side point; the 2026-10-01 29,945 pps
  figure was pxswcapture's, a different measurement point).
- **Not verified:** `pxswcapture.service` on XPress-SW-A is inactive (clean stop 17:13 UTC, before
  this run) with another party's work on that VM, so it was left alone; capture to disk, per-protocol
  mix/correctness, hq-mx WAN drops and the lab-CA curl were not measured. The `:8080` Console is down
  (needs the user's `AXRANGE_API_TOKEN`).
- One te-deploy retry: `run-script[9k02-app]` attempt 1 exit 100 on the dpkg lock held by
  unattended-upgr, attempt 2 passed (GAP-109/LES-353; now seen in `serve-vrrp-l3.sh` too).
- Application layer: 34,935 of 248,510 messages failed (14.1 %); `corp-file-share-client` 4352/4352
  failed, 0 bytes (smbd resets) — GAP-120 updated.

**Why:** the user wanted the range back after maintenance, at the proven ceiling profile.

**Affected docs:** `docs/SESSION_STATE.md` (replaced; prior verbatim in `docs/handoffs/2026-10.md`),
`docs/WORK_ORDER_corp_twin.md` §11 Live run 6, `docs/PRODUCT_GAPS.md` (GAP-109, GAP-119, GAP-120
updated; none minted).

**Affected code:** none.

## 2026-10-01 — corp-twin capture-rate ceiling measured on XPress-SW-A; mirror path unfragmented, sensor GRO off

**Authority:** user (authorized scope: "analyze with live verify the max throughput you can
achieve on this twin without dropping packets and without losing the protocol correctness of the
traffic" — traffic + taps + mirror plane writes on `corp-twin`; tune the sensor and keep what
works; leave the best verified rate running saved as a variant; 12 h budget, stop-on-any-drop) /
implementation (Claude Code) for the method and results.

**What changed:**
- Live ladder on `corp-twin-07cfcdb2` (dev45, gate 0.9) from baseline to ceiling, every rung
  measured with zero-drop/protocol-health gates:

  | | B0 baseline | Ceiling (`tfc/caprate-max.yaml`, ×6) |
  |---|---:|---:|
  | captured at XPress-SW-A (pxswcapture) | 6,541 pps / 48.1 Mbit/s | **29,945 pps / 244.2 Mbit/s** |
  | packets over the soak / drops | 4.77 M / 0 | **59.0 M / 0** (1,970 s soak) |
  | dev45 CPU avg | 25.5 % | 26.6 % (~27 % typical) |
  | heartbeats | 84/84 | 84/84 |
  | data retransmissions (limit 1.09e-4) | 1.13e-3 avg | 1.07e-4 worst sample — thin margin |

  (The "~6 kpps" baseline figure is pxswcapture's own `packetRate`, not a rounding of 6,541.)
- New twin-local traffic variant `twins/corp-twin/tfc/caprate-max.yaml` (84 instances: 74 scaled
  ×6 on new `*-burstwx6` personas, 10 Windows workstations left at `gate90` unscaled) — left
  running on dev45 as the twin's current applied profile.
- New `twins/corp-twin/scripts/mirror-mtu-path.sh` (`apply|revert|status`, run on dev45 as root):
  gives only corp-twin's 24 gretaps a 1600-byte path to the sensor (`fwmark 0x5ca1` → table 251)
  so full-size mirrored frames stop fragmenting into two outer IP packets. **Runtime-only** — lost
  on a dev45 reboot, a VM-104 NIC replug, or a gretap re-create (not on a plain `tap deploy`
  re-apply, which adopts the existing netdev).
- Sensor (XPress-SW-A) GRO turned off on `ens19` and `gre-cap`, persisted (NetworkManager profile
  + a `gre-cap.service` systemd drop-in) — documented in `o2/tcpdump-sensor/README.md` (written by
  the run, not touched by this doc pass).
- Full method, every rung's numbers, and the bottleneck chain:
  `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`.

**Why:**
- Bottleneck chain, in the order met: (1) the persona generator's own loop cadence; (2) transport
  fragmentation at 1500 MTU (fixed by the corp-twin-only 1600-byte path); (3) fixing (2) exposed
  sensor GRO merging GRE-carried TCP into ≤3.7 KB frames and overrunning pxswcapture's AF_XDP
  queue (fixed: GRO off); (4) the Windows DC retransmitting once its three workstation personas
  scale (left unscaled); (5) **the single external sink (2 vCPU caddy) is what sets the ceiling** —
  ×8 failed 1 of 3 samples, ×6 held clean. The capture chain itself is proven clean to 218 kpps /
  1.96 Gbit/s with probe traffic; first loss at ~305 kpps (single AF_XDP queue). Going past ~30
  kpps on this twin needs more/several sinks, protocol-faithful (not filler) personas, and a DC
  that keeps up with scaled Windows auth traffic — see `docs/PRODUCT_GAPS.md` GAP-120–GAP-122.
- Method note: tshark's flat `tcp.analysis.*` is meaningless on this multi-hop-tapped capture
  (counts the same segment 2-4× as a "retransmission") — correctness was judged per tapped
  observation point instead; see the ladder doc and `docs/LESSONS.md` LES-361.
- Finding, not caused by this run: svc-engines' two Windows VMs (`864812` te-cbd9a1-dc,
  `506017` te-cbd9a1-win-member) powered themselves off guest-initiated at 07:45:31/07:57:04 UTC
  (no PVE task, no OOM); left stopped, for the user. VM 102 `o2-services` is also stopped (unknown
  since when). Nothing in this run touched svc-engines or VM 102.

**Affected docs:**
- `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md` — new, full record (source of
  truth for this entry).
- `docs/WORK_ORDER_corp_twin.md` — §11 new "Live run 5" section; §12 new divergences.
- `docs/PRODUCT_GAPS.md` — GAP-119 through GAP-123 (new).
- `docs/LESSONS.md` — LES-360, LES-361 (new); LES-347 extended with a one-line recurrence.
- `docs/SESSION_STATE.md` — capture-rate state, revert path, the two independent stopped-VM items.

**Affected code:** none (traffic/tap/sensor configuration only — no `axrange/` or `o2/` source
changed); twin content: `twins/corp-twin/tfc/caprate-max.yaml`, 13 `twins/corp-twin/personas/
*-burstwx6.yaml`, `twins/corp-twin/scripts/mirror-mtu-path.sh`, `twins/corp-twin/twin.yaml`
(variant registered), `o2/tcpdump-sensor/README.md` (written by the run).

## 2026-10-01 — Deletion list approved and executed; Grafana stack trimmed to telemetry-only

**Authority:** user — "approve the deletion list and delete them."

**What changed:**
- Deleted, per `docs/reference/ui-consolidation/05-deletion-candidates.md` items 1-3 and 5-8 (item 4,
  `twin-configs-cast/`, kept — see below):
  - `observability/` — 40,559 tracked files (stale pre-rename plugin build + its `node_modules`).
  - Local branch `wip/ui-viewer-spa` (2 commits); the tag `retired/ui-viewer-spa` is kept as the
    historical pointer. The remote never had this branch, so no remote delete was needed.
  - The merged worktree `.claude/worktrees/agent-ab92e05d08ed71105` and its branch
    `worktree-agent-ab92e05d08ed71105`.
  - The untracked `scenarios/` directory (leftover pre-rename `state.db`, 3 files).
  - The Grafana control-panel plugin `o2/grafana/plugins/axellio-axrange-control-panel/` (80 tracked
    files) and its tooling: `o2/grafana/build-plugin.sh`, `o2/grafana/gen-topology-dashboards.py`,
    `o2/grafana/topology-eval.manifest.json`, `o2/grafana/plugins/.gitignore`.
  - Dashboards `o2/grafana/dashboards/{deploy-op-status,pcap-browser,control-console}.json` and the
    `o2/grafana/dashboards/topology-eval/` directory (7 renderer-bakeoff dashboards + README).
  - The `axr-statedb` datasource block from `o2/grafana/provisioning/datasources/datasources.yml`.
- **Kept: item 4, `twin-configs-cast/`.** Its precondition — the Design area's Apply writing into the
  `twins/<id>/` layout — was never met, so it remains the only bundle directory where Apply works
  end-to-end for a real (non-fixture) imprint; deleting it would remove the one live Apply path.
- **Compose/config follow-through:** `o2/compose/docker-compose.yml` drops the plugin bind mount, the
  `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS` env var, `frser-sqlite-datasource` from
  `GF_INSTALL_PLUGINS`, and the `state.db` read-only volume mount + `STATE_DB_PATH`/`STATE_DB_MOUNT`
  vars (nothing left references them). The `yesoreyeram-infinity-datasource` plugin and the `axr-api`
  datasource stay provisioned — the API remains a telemetry source for a future panel, even though
  none of the three kept dashboards (capture health, Suricata alerts, attack coverage) read it today.
- Docs reconciled to stop describing the deleted surfaces as present: `o2/README.md`,
  `o2/grafana/dashboards/README.md`, `o2/observability.conf.example`, `docs/design/05-o2-surface.md`,
  `11-api-ui.md`, `14-operator-assertions.md`, `16-unified-console.md` §9, `CLAUDE.md`, and the
  `GAP-097` entry in `docs/PRODUCT_GAPS.md` (narrowed to the SPA now that the plugin consumer is
  gone). `docs/reference/ui-consolidation/05-deletion-candidates.md` got a dated "Executed" header;
  `docs/SESSION_STATE.md` marks the deletion-list decision done.

**Why:**
- The axRange Console (`docs/design/16-unified-console.md`) supersedes the Grafana control-panel
  plugin and its three dashboards; the plugin's own bake-off closed without a winner. Carrying dead
  source, dead config knobs, and a merged worktree forward serves no one, and a stale doc describing
  a deleted surface as present is actively misleading to the next reader.

**Affected docs:** `o2/README.md`, `o2/grafana/dashboards/README.md`, `o2/observability.conf.example`,
`o2/grafana/provisioning/datasources/datasources.yml`, `docs/design/05-o2-surface.md`,
`docs/design/11-api-ui.md`, `docs/design/14-operator-assertions.md`,
`docs/design/16-unified-console.md`, `CLAUDE.md`, `docs/PRODUCT_GAPS.md`,
`docs/reference/ui-consolidation/05-deletion-candidates.md`, `docs/SESSION_STATE.md`.

**Affected code:** `o2/compose/docker-compose.yml`; deleted: `observability/`,
`o2/grafana/plugins/axellio-axrange-control-panel/`, `o2/grafana/build-plugin.sh`,
`o2/grafana/gen-topology-dashboards.py`, `o2/grafana/topology-eval.manifest.json`,
`o2/grafana/dashboards/{deploy-op-status,pcap-browser,control-console}.json`,
`o2/grafana/dashboards/topology-eval/`, `scenarios/`.

---

## 2026-09-30 — Unified Console live verification: Part A (read-only, `corp-twin`) and Part B (write-path, disposable `sme-flat`) PASSED

**Authority:** implementation (Claude Code); user authorization 2026-09-29/30 (the design ruling's
build order step 7 — `docs/reference/ui-consolidation/00-design-ruling.md` §6 — required exactly
this: read-only against the standing `corp-twin`, then a fresh disposable twin for the write paths).

**What changed — this is a verification record, not a code change:**
- **2026-09-29, Part A, READ-ONLY, on the standing `corp-twin-07cfcdb2`.** All 106 hosts, 84 persona
  instances and 80 tap bindings matched the CLI's own output exactly; the live power state of all
  106 VMs matched `pvesh`; per-host heartbeat matched `systemctl is-active` on 2 sampled hosts;
  every preview call wrote nothing (state store byte-identical before/after); six Console pages
  loaded with 0 browser-console errors; the 106-host Twin page rendered in 0.71 s.
- **2026-09-30, Part B, WRITE-PATH, on a disposable `sme-flat`** (`N_WORKSTATIONS=1`; deploy
  `sme-flat-8c4cfbac`; plan `sme-flat-e30ea4cf`; 4 VMs, 42 ops; deploy 271 s; traffic apply 15 s via
  a substitute traffic profile, because the twin's own default profile does not resolve — GAP-116,
  below):
  - A per-host persona swap on `file-server` completed in ~6 s; the other two hosts' systemd units
    kept **identical** `InvocationID`/`MainPID`/`ActiveEnterTimestamp` and their op rows were
    byte-identical — the "editing one host never touches another" invariant proven live, not just
    read out of the executor's own bookkeeping.
  - A stale `expect_plan` on the PUT answered `409 plan-changed`; a Windows persona assigned to the
    Linux host answered the `os-mismatch` refusal; a stop-only edit (drop a persona, add none)
    applied cleanly.
  - A whole-profile re-apply that would have reverted the host edit was refused `409` without
    `allow_revert_host_edits`, applied `200` with it, and marked the earlier per-host job
    `superseded`.
  - The same host edit driven through the Console itself, in headless Chromium: `401` (no token) →
    token prompt → `200` → "Applied … after start: alive".
  - Destroy took 25 s and removed 4 VMs, 4 mirrors, the vnet and the zone. Both standing twins and
    both state stores were byte-identical before/after the whole run; the cluster's other 127 VMs
    were unchanged.
  - **Defects found and fixed the same day:** D2 (host-edit job history was lost on a later
    re-drive) and D3 (a superseded job was dropped instead of marked) — both closed by fix round 4
    below; D4 (the Console's persona editor clipped inside its panel) — Console-only, same commit.
  - **Observation, recorded as GAP-117 (not a defect of this round):** a whole-profile apply
    restarts every unit even when that host's assignment is unchanged from before — pre-existing
    engine behavior, unchanged by this consolidation.

**Why:** GAP-115 existed precisely to gate this — every "live" claim the round added (cluster
reachability, VM power state, QGA reachability, a persona heartbeat, the per-host apply's isolation
guarantee) was implemented and unit/API-tested but unproven against a real Proxmox cluster. This run
closes it.

**Affected docs:** `docs/PRODUCT_GAPS.md` — GAP-115 resolved, GAP-105 closed in full, GAP-098
resolved for twins/imprints, GAP-084 status note added, GAP-116/GAP-117/GAP-118 added;
`docs/design/16-unified-console.md` status banner ("built and live-verified 2026-09-30");
`docs/design/README.md` entry 16; `docs/archive/work_orders/WORK_ORDER_unified_console.built.md`
(new, the full build+verification record). A dedicated `docs/reference/ui-consolidation/
07-live-verification.md` write was attempted and denied (tool permission, not a design refusal) —
this entry plus the screenshots under `docs/reference/ui-consolidation/shots/` are the record
instead (GAP-118).

**Affected code:** none beyond fix round 4 (below), which this run found the need for.

**Links:** `docs/reference/ui-consolidation/shots/` (`a-*.png`, `b6-*.png`, `after-b3-*.png`).

## 2026-09-30 — Unified Console fix round 4: host-edit job history survives re-drives; superseded edits are marked; the persona editor fits its panel

**Authority:** implementation (Claude Code); found live during the write-path verification run
above (D2/D3/D4), fixed and re-verified the same day.

**What changed:**
- **Host-edit job history is kept.** `GET /api/deploys/{tfc_deploy_id}` `host_edits[]` lists every
  per-host apply job, newest first, even after the rows it touched are re-driven by a later edit or
  a whole-profile apply — each row now carries an append-only `host_edit_history`, restored after
  every executor run instead of being overwritten.
- **Superseded jobs are marked, not omitted.** A successful whole-profile apply now marks every
  earlier per-host job record `"superseded": true, "superseded_at": <epoch of that apply>`.
  `/hosts` `host_edit` still shows a host's newest job, carrying those two fields once a
  whole-profile apply has taken over (the personas then read `origin: "profile"`); a job with no
  later whole-profile apply carries no `superseded` key.
- **Console:** the persona editor panel now fits its container (it was clipping), and a superseded
  host edit renders as history rather than looking like the current state.

**Why:** D2/D3 above — the operator-facing job history was silently forgetting what had actually
run on a host once a later apply touched the same rows, which is exactly the record an operator
reaches for after "did my edit stick."

**Affected docs:** `docs/reference/ui-consolidation/04-api-contract.md` "Fix round 4";
`docs/design/16-unified-console.md` §5.

**Affected code:** `axrange/planner/state.py`, `axrange/api/service.py`, `axrange/api/dtos.py`
(`d71771c4`); `o2/console/src/areas/twin/*` (`0094d9a2`).

## 2026-09-30 — Unified Console fix round 3: the immutable-open reversal (LES-359), and multi-store deploy routing (LES-156 recurrence)

**Authority:** implementation (Claude Code), from a focused review of the round-2 commit.

**What changed:**
- **Read-only opens no longer use `immutable=1`.** It takes no locks and ignores the WAL entirely,
  so a reader mid-scan can read a torn page while a separate connection commits and checkpoints
  under it — reproduced 40/40 in review. Replaced with `mode=ro` + `PRAGMA query_only=ON` +
  `busy_timeout`, which takes proper shared read locks and reads a consistent snapshot. The
  accepted cost: a read-only connection may leave empty `-wal`/`-shm` files beside a WAL-mode store
  it cannot delete; it never writes a data frame into them.
- **`POST /api/deploys` and `POST /api/imprints/{id}/deploy` route to the store that holds the
  twin.** Both now search every store of the target for a non-destroyed deploy of the same scenario
  or plan and run there (resume, `update`, lease and protection all included) — never mint a second
  row in the primary. The same scenario standing in two stores of one target answers
  `409 ambiguous-store`. `POST /api/deploys/{id}/run` takes `?target=` and finds the deploy in any
  store (read-only — it creates nothing).
- **Startup and request-time hardening.** Every store is opened read-only at startup; an unreadable
  store, or a named extra store that does not exist, fails `axrange serve` by name. A store that
  becomes unreadable at request time is skipped with a warning (`GET /api/deploys` gains
  `warnings: [str]`; `/api/range` shows that store `ok: false`) rather than failing the whole call.
  Store labels use the fewest trailing path components that make each one unique.

**Why:**
- The review reproduced the `immutable=1` corruption directly (scratchpad `imm2.py` vs `imm3.py`,
  pinned by `tests/test_unified_console_fixes2.py::test_a_readonly_reader_survives_a_writer_committing_and_closing_under_it`,
  LES-359) and separately traced that a fresh deploy of a scenario already recorded in
  `svc-engines-24131435`'s extra store (round 2's several-stores model) would have minted a second
  row sharing the primary store's deterministic vmid/name formulas — protection never runs, because
  it looks in the wrong store, and the new row's own destroy would then delete the standing twin's
  live VMs. That is a direct recurrence of LES-156's "existence is not ownership" class, caught in
  review before any live write reached a cluster.

**Affected docs:** `docs/LESSONS.md` — LES-359 (new); LES-156 (2026-09-30 recurrence note added).

**Affected code:** `axrange/planner/state.py` (`StateStore._open_readonly`), `axrange/api/service.py`
(`_route_to_owning_store`) (`1cc93598`).

## 2026-09-30 — Unified Console fix round 2 (live verification Part A prep): several stores per target, unrecorded VMs, plan/validate for twins and imprints

**Authority:** implementation (Claude Code), driven by preparing Part A of the live-verify run
above: the two standing twins (`corp-twin-07cfcdb2`, `svc-engines-24131435`) live in **two
different** state stores on the same cluster, which the initial build never modeled.

**What changed:**
- **Several stores per target.** A target is one cluster with one or more state stores: a targets
  file entry gains `db:` (primary — new deploys are written there) plus optional
  `extra_dbs: [path, …]`; the implicit target gains `AXRANGE_EXTRA_DBS=<path>:<path>` /
  `axrange serve --extra-db PATH` (repeatable). Every `DeploySummaryDTO`, `/api/range` twin and
  `/hosts` row gains `store` (a short label, e.g. `"state.db"`, `"axr-svcengines.state.db"`).
  `GET /api/targets` items gain `stores: [{label, path, primary}]`; `/api/range` target items gain
  `stores: [{label, path, primary, ok, reason}]`. Reads that take a deploy id or twin find it in
  whichever store holds it; `GET /api/deploys` merges every store, newest first.
- **Reads never write.** Read routes open every store read-only (no DDL, no `user_version` stamp,
  never a created store).
- **`POST /api/plan` and `POST /api/validate`** now accept exactly one of `scenario` / `config` /
  `imprint`, plus `params` and `target_node` — not only a bare scenario as before.
  `PlanSummaryDTO` gains `vm_count` and `params: {NAME: <resolved value>}`.
- **Twin parameters.** `GET /api/twin-configs/{id}` `params[]` items gain `declared: bool`
  (declared in `params:` vs an inline `${NAME:default}` token); list items gain `parameterized` and
  `param_names`. `/api/imprints` no longer skips a bundle whose `count:` is a `${NAME:default}`
  token — defaults are resolved for display.
- **Unrecorded VMs.** `/api/range` target items gain `unrecorded`: axRange-named VMs (`te-…`,
  `o2-…`, `tfc-…`, `exr-…`, never a template, never an `EXTERNAL_SENSORS` vmid) that the live
  cluster sweep finds but no non-destroyed deploy in any of the target's stores records.
- **Cached `checked_at`** now repeats its original value everywhere a cached health answer is
  reused, including the top-level field.

**Why:** without this, Part A's read-only pass against `corp-twin` would either miss
`svc-engines-24131435` entirely (it is not in the primary store) or — worse, per fix round 1's M1
finding — risk conflating the two clusters' identities; and the Catalog's guarded deploy/validate
path had no way to preview a twin-config or imprint with parameters before this round.

**Affected docs:** `docs/reference/ui-consolidation/04-api-contract.md` "Fix round 2";
`docs/design/16-unified-console.md` §7; `docs/design/11-api-ui.md`.

**Affected code:** `axrange/api/targets.py`, `axrange/api/serve.py`, `axrange/api/service.py`,
`axrange/api/dtos.py`, `axrange/cli/main.py` (`--extra-db`) (`20f6fc91`).

## 2026-09-29 — Unified Console: one run-time-first Console supersedes single-pane Grafana, the "viewer not authoring" ruling, and the seven-surface UI history

**Authority:** implementation (Claude Code), under the user's 2026-09-29 instruction to
autonomously "design + build everything" for a UI consolidation, explicitly authorizing that
"anything may be superseded, [provided it is] recorded." Full ruling and its evidence:
`docs/reference/ui-consolidation/00-design-ruling.md` (the ruling), `01-backend-audit.md`,
`02-ui-surface-inventory.md`, `03-ui-history-and-decisions.md`.

**What changed:**
- There is now **one operator-facing application**: the axRange Console (`o2/console/`, served by
  `axrange serve --spa-dir`). It is run-time first (opens on what is running, not a design file),
  with four areas — Range (home), Twin (one deployed twin: map + host table, per-host personas,
  live health, destroy), Catalog (validate/plan/deploy, guarded), Design (the former "Console
  canvas" of `docs/design/15-console-canvas.md`, relabelled and re-rooted at `/design`, unchanged
  in function) — plus a Telemetry area linking out to three live Grafana dashboards (capture
  health, Suricata alerts, attack coverage). Full design: `docs/design/16-unified-console.md`.
- Grafana is narrowed to **telemetry only**. The `axellio-axrange-control-panel` plugin, the
  seven-renderer topology bake-off, and the deploy/op-status, pcap-browser and control-console
  dashboards are **deprecated in place** (banners added, nothing deleted) — see
  `docs/reference/ui-consolidation/05-deletion-candidates.md` for the deletion list pending the
  user's approval.
- Two engine gaps closed: **cluster identity** (a named-targets file, `axrange serve --targets
  <file>`, each with its own adapter config + state.db; `GET /api/range` and `GET /api/targets`
  list every target with live reachability) and a **per-host persona apply**
  (`o2control.traffic.apply_host_personas`; `POST .../personas/plan`,
  `PUT /api/deploys/{id}/hosts/{host}/personas`) that changes one host's traffic without touching
  any other host's — proven by scoped `force_reset_ops`, a dependents opt-in, and a whole-profile
  revert guard. No version-locked schema changed; `state.db` stays schema v3.

**Why:**
- Seven UI efforts shipped in ~13 weeks, each restarted from a newly found reference instead of
  converging (`03-ui-history-and-decisions.md` §1, §7): a viewer SPA (retired the day it shipped),
  a single-pane Grafana plugin (froze at the `te` tier), a seven-renderer bake-off (never
  concluded), an Imprints screen (deleted, GAP-083), a Twin-topology screen (half-built tables),
  and the richest and newest of them — the Console canvas — was design-time only and showed
  nothing for 10 of ~13 twins including the flagship `corp-twin`, while labelling design-time
  counts "live". Every real operating action since August was CLI + YAML. Most of the missing
  reads already existed as unwired API endpoints; the two genuinely missing engine capabilities
  (cluster identity, per-host persona apply) are listed above.

**Supersedes** (`00-design-ruling.md` §5; each also closes a reversal that happened in practice
without ever being recorded, per `03-ui-history-and-decisions.md` §3/§7):
1. **Single-pane Grafana** (2026-06-25, `docs/changelog/2026-H1.md` "design(ui): single-pane
   Grafana supersedes the SPA+Grafana hybrid", Authority: user) → the axRange Console is the
   operator surface; Grafana is telemetry only. **This entry also retroactively records the
   reversal `docs/design/15-console-canvas.md` (ratified 2026-09-04, "a SPA, not Grafana; starts
   clean") already made in practice against this same 2026-06-25 ruling, with no `Supersedes:`
   written at the time** — `docs/design/README.md` line ~38 and `docs/design/11-api-ui.md` still
   called Grafana "single-pane" until this pass reconciled them.
2. **"UI is a viewer, not an authoring tool; never emits IR"** (2026-06-19,
   `docs/changelog/2026-H1.md` "design: UI control surface — control-plane API +
   Grafana/viewer shell (convergence B)", Authority: user; `docs/design/11-api-ui.md`) → the
   Console views, controls, **and** edits: run-time changes go through the `tfc` tier (already
   runtime-swappable per the 2026-08-04 role/persona ruling), design-time edits stay assertion
   records via the Design area. **This entry also retroactively records the reversal
   `docs/design/14-operator-assertions.md` §7 (ratified 2026-09-04, "the canvas as the authoring
   surface") already made in practice against this same 2026-06-19 ruling, with no `Supersedes:`
   written at the time** — `docs/design/11-api-ui.md` still called the console "not an authoring
   tool" until this pass reconciled it.
3. **"Console starts clean, nothing ported from the plugin; independent of the plugin"**
   (`docs/design/15-console-canvas.md`, CL:3991 "the Console SPA starts clean", 2026-09-04;
   `CLAUDE.md` Conventions) → plugin behaviours (guarded deploy, deploy tree, "why empty" states)
   are absorbed into the Console's Catalog/Twin areas; the plugin itself is deprecated, not kept
   independent.
4. **D5 "the plugin keeps 'Deploy as twin'"** (`docs/WORK_ORDER_console_canvas.md` §8.1, user,
   2026-09-11) → deploy moves to the Console's Catalog; the plugin keeps no action at all once
   deprecated.
5. **The layered free-hand IR canvas** (`docs/ARCHITECTURE.md` §1; `docs/OPEN_QUESTIONS.md` Q1/Q11)
   → recorded as never built and not the current direction; the layer/overlay model stays an IR
   concept (`docs/IR_SCHEMA.md`), not a UI commitment.

**Kept, not reversed:**
- "The canvas edits the imprint, never the deployed twin" (`docs/design/14-operator-assertions.md`
  §4/§8, `docs/design/15-console-canvas.md` §11) — the Design area still does only that; run-time
  persona changes go through the `tfc` tier, per the 2026-08-04 ruling, not through the Design area.
- The API/DTO boundary; the auth posture (write token held in the browser's memory only); the
  six-class evidence model and its precedence order; deterministic, golden-tested layout;
  Logical/Physical semantics (`docs/CHANGELOG.md` 2026-08-27 correction); no `react-flow`/`d3`/
  `dagre` in the Console. All of `docs/design/15-console-canvas.md`'s function is unchanged — only
  its place in the app (relabelled, re-rooted at `/design`) changed.

**Affected docs:**
- `docs/design/16-unified-console.md` (new) — the design of record for the Console.
- `docs/design/README.md` — entry 16 added; entries 11 and 15 reconciled.
- `docs/design/11-api-ui.md` — "single-pane Grafana console" section rewritten; "not an authoring
  tool" removed; endpoint table gains the new routes; `01-backend-audit.md`'s code/doc divergences
  fixed (tap GET, stale "tap points" claim, `/api/targets` now truly multi-cluster, `/scorecard`
  row added).
- `docs/design/15-console-canvas.md` — superseded-in-part note added; function unchanged.
- `docs/design/05-o2-surface.md` — STEER plane reworded to the Console; Grafana-as-console text
  removed; plugin/dashboard references marked deprecated.
- `docs/design/01-overview.md` — the free-hand canvas no longer presented as live design.
- `docs/ARCHITECTURE.md` §1 — a dated status note added pointing to `design/16` (no version bump).
- `docs/OPEN_QUESTIONS.md` Q1, Q11 — marked superseded/answered with pointers; original text kept
  below each for the record.
- `o2/README.md` — stale "NOT stood up" replaced; control-panel section marked deprecated.
- `o2/grafana/plugins/axellio-axrange-control-panel/README.md`,
  `o2/grafana/dashboards/README.md` (new), `o2/grafana/dashboards/topology-eval/README.md` (new) —
  deprecation banners.
- `CLAUDE.md` — the plugin-independence line and the architecture-at-a-glance first line
  reconciled; `axrange serve --targets` mentioned.
- `docs/PRODUCT_GAPS.md` — GAP-105 partially closed; GAP-084 status note added (unaffected in
  kind, widened in surface); GAP-113, GAP-114, GAP-115 added.
- `docs/LESSONS.md` — LES-357, LES-358 added.
- `docs/reference/ui-consolidation/05-deletion-candidates.md` (new) — verified deletion list for
  the user's approval.

**Affected code:** none in this pass (doc-writer stage; see the companion build entry below).

**Links:** `docs/reference/ui-consolidation/00-design-ruling.md` through `04-api-contract.md`.

## 2026-09-29 — Unified Console fix round 1 (code review): protected deploys, the plan-fingerprint pin, persisted job diagnostics, twin-local personas on the whole-profile apply

**Authority:** implementation (Claude Code), under the user's 2026-09-29 "design + build everything"
authorization; the fixes themselves respond to a code-reviewer pass
(`docs/reference/ui-consolidation/06-code-review.md`) run against the initial build before any live
write was attempted (design ruling `00-design-ruling.md` §6 build order).

**What changed:**
- **Plan fingerprint.** `POST .../personas/plan` returns `fingerprint`; the PUT takes `expect_plan`
  and answers `409 plan-changed` (with the fresh plan attached) when the plan it computes under the
  lease differs from what was previewed.
- **Persisted job record.** Every host edit writes a job record to the state store —
  `{job_id, host_id, status, started_at, finished_at, fingerprint, plan, diagnostics[]}` — exposed
  per host as `/hosts` `host_edit` and in full as `/api/deploys/{tfc_deploy_id}` `host_edits[]`.
- **Stops that fail stay visible.** A persona whose stop raised, or whose post-stop heartbeat did
  not say `dead`, keeps its row `completed` and gets `stop_error`, surfaced on `/hosts`
  `personas[].stop_error`, instead of silently vanishing.
- **Protected deploys.** `/api/range` twins and `/hosts` carry `protected: bool`. Every write route
  aimed at a protected twin or any of its tap/tfc/exr siblings — destroy, resume/update, the
  whole-profile or per-host persona apply, tap/exr start/stop — is refused `423` unless the request
  carries `override_protection=<that te deploy id>`.
- **Role orphaning** refused (`refusals[].code = "last-holder"`) unless `allow_role_orphaning: true`;
  **instance collision** gets its own refusal code.
- **Imprints** (`GET /api/imprints`, `GET /api/imprints/{id}`) gain `can_apply: bool` and
  `apply_blocked_reason: str|null`.
- **Catalog deploy pin, closing GAP-098.** `POST /api/deploys` and `POST /api/imprints/{id}/deploy`
  accept `expect_plan_id` (the `plan_id` a matching `/plan` call returned); a mismatch answers
  `409 {"code": "plan-changed", "plan_id": "<actual>"}` instead of silently re-planning from
  whatever is on disk now. `CreateDeployResponse` gains `plan_id`.
- **Twin-local personas on the whole-profile apply, closing GAP-105 in full.** `POST /api/tfc`
  resolves a twin-local persona overlay per request (`_tfc_twin_overlay`), the same mechanism the
  per-host apply and `GET /api/personas` already used — a twin's own `personas/` folder is no
  longer invisible to the one route that still missed it.
- Looser DTO fields (`personas[].peers`, `taps[].nic_index` may be `null`); health reads cached
  (~10 s per target/deploy/host, ~5 s for the cluster sweep/`/version`), a cached answer keeping its
  original `checked_at`.

**Why:**
- The review's MUST-FIX list (`06-code-review.md` M1–M4) found a non-default target able to
  silently write to the default cluster, a detached path that lost stop failures while still
  marking the row stopped, a plan-before-lease race, and a previewed plan the UI had no way to
  compare against what actually executed — all before any real write reached a cluster. This round
  closes the API/engine half of that list ahead of live verification.

**Affected docs:** `docs/reference/ui-consolidation/04-api-contract.md` "Fix round 1"; `docs/design/16-unified-console.md` §5 (plan fingerprint, protected deploys, job history — folded in
2026-09-30, see below); `docs/design/11-api-ui.md` (Auth posture, endpoint table).

**Affected code:** `axrange/api/service.py`, `axrange/api/dtos.py`, `axrange/api/targets.py`,
`axrange/o2control/traffic.py` (`71d01491`); `o2/console/src/*` reflecting protected/plan-pin state
(`54b159b7`).

## 2026-09-29 — Unified Console: engine + API additions built (cluster targets, `/api/range`, the Twin-area host model, per-host persona apply)

**Authority:** implementation (Claude Code)

**What changed:**
- `axrange serve --targets <file>` (or `AXRANGE_TARGETS`): named cluster targets, each with its own
  `adapter_config` and `db` (a shared db across targets refused at startup). `GET /api/targets`
  lists every target; `GET /api/range` is the Console's one home-page read — every target's
  standing twins with sibling tap/tfc/exr deploys, host/persona counts, and a live reachability
  probe, labelled `checked_at`.
- `GET /api/deploys/{id}/hosts` — the Twin area's one model: the recorded host roster joined with
  tfc personas (status, `origin: profile|host-edit`, `heartbeat`), tap bindings, and per-host last
  error. `GET /api/deploys/{id}/health` — live, on demand: one cluster sweep for VM power, plus
  per-host QGA reachability and persona heartbeats behind `?host=`.
- `GET /api/personas?twin=<id>` — catalog plus a twin's local personas, resolved per request as a
  `ContextVar` overlay, never mutating the process-global registry (closes GAP-105 for this path
  and the per-host apply path; the whole-profile `POST /api/tfc` path closes the same way in a
  concurrent fix round, not yet confirmed — GAP-105 updated accordingly).
- `apply_host_personas` (`axrange/o2control/traffic.py`): the per-host persona apply engine.
  Desired set = the standing tfc deploy's completed rows with only the target host's entries
  replaced; resolved through the unchanged `resolve_traffic_bindings`; `force_reset_ops` on that
  host's instance ops only; de-scope restricted to that host's dropped instances; a role change's
  stale dependents are listed but re-driven only on `update_dependents: true`; a driver
  `heartbeat()` is recorded once per started instance. Rows carry `origin: "host-edit"`; a later
  whole-profile apply that would revert one is refused (409) unless `allow_revert_host_edits`.
  `GET /api/deploys/{id}/tfc/profile` exports the running state as a traffic-profile YAML
  (export only — no write-back to `twins/<id>/tfc/`).
- API: `POST .../personas/plan` (pure preview), `PUT .../personas` (write token, async). CLI
  parity: `axrange tfc set-host <target> <host> --persona P [...] [--role R] [--plan]`, `axrange
  tfc deploy ... --revert-host-edits`, `axrange serve --targets <file>`.
- `o2/console/`: Range/Twin/Catalog areas built over the above, sharing the Design area's layout
  engine, URL model, API client and theme; the Grafana iframe embed removed.

**Why:** the two genuinely-missing engine capabilities `01-backend-audit.md` identified: the
per-host scalpel (§4, "the smallest unit of change today [for tfc] is the whole profile") and
cluster identity (§2/§6).

**Affected docs:** `docs/reference/ui-consolidation/04-api-contract.md` (the contract as built);
`docs/design/16-unified-console.md` §5 (per-host apply semantics), §7 (multi-target serving).

**Affected code:** `axrange/api/app.py`, `service.py`, `dtos.py`, `targets.py` (new);
`axrange/o2control/traffic.py`; `axrange/personas/registry.py` (the `ContextVar` overlay);
`axrange/cli/main.py`; `o2/console/src/` (new `areas/range`, `areas/twin`, `areas/catalog`, API
hooks); `tests/test_unified_console.py`, `tests/test_unified_console_fixes.py`.

**Supersedes:** none directly (additive engine/API surface); see the companion design entry above
for the design-level supersessions this build realizes.

**Links:** `docs/reference/ui-consolidation/00-design-ruling.md` §3, §6; `04-api-contract.md`.

## 2026-09-28 — corp-twin personas rebalanced to the discovered mix; static mix-model test

**Authority:** implementation (Claude Code)

**What changed:**
- `twins/corp-twin/personas/` rebalanced against corp-twin live run 3's measured 60 s capture,
  which had diverged wildly from §7's target mix: NTP + NetBIOS-DGM (udp/138) were 48–72% of
  bytes on wan1-isp/sfp25/t-ms120-sgs5240 (target < 1%), SMB was one flow (target ~25%), and TLS
  vs. HTTP was inverted on wan1 (17.8%/17.3% vs. a target ~70%/2.5%, ~28:1). Root cause: the
  cast's `volume:` tier was the only lever, and `cast-file-server` was two `bulk` UDP emits
  (ntp/123, udp/138, 64 MiB every 32 s) with no SMB traffic actually reaching it.
- `cast-file-server` is now one 48 B NTP poll every 64 s; a new persona
  `corp-file-share-client` dials `file-server:445` (64 × 512 KiB every 30 s), assigned to a
  10.80.20 heavy endpoint, standing in for §7's SMB client (added to both `tfc/default.yaml` and
  `tfc/gate90.yaml`). NTP messages are pinned to 48 B, DNS to 64 B (low → med on
  `cast-generic-endpoint`, making DNS the flow plurality by design), QUIC/HTTP volumes lowered,
  workstation HTTPS raised, workstation SMB repointed from the sink to the DC.
- New `tests/test_corp_twin_mix.py` pins a static mix model (no live capture) for both profiles:
  gate 0.9 modelled at TLS 70.1%/SMB 25.6%/HTTP 2.4%/DNS 0.15% bytes but 30.5% of flows, NTP
  messages ≤ 48 B, DNS ≤ 512 B, `bulk` used only for SMB/HTTPS, and the file server dialled only
  on 445 and emitting only NTP.

**Why:**
- The old mix would have made any capture-fidelity comparison against §7 meaningless — the
  dominant protocols on every mirrored segment were backwards. The SMB byte volume specifically
  depends on the file server's own `smbd` owning port 445 (confirmed live: the generic per-port
  echo responder correctly ceded to it with `EADDRINUSE`, by design) — not yet confirmed to
  actually drain the client's stream; live run 4 is the first re-measure against the new mix.

**Affected docs:**
- `docs/WORK_ORDER_corp_twin.md` §11 live run 3 ("traffic mix vs §7") — new run 4 record appended
  this pass; §8 gains fidelity gap **G24** (no WAN-inbound background traffic — a persona can only
  dial a `peer_role`, and `gw` resolves to hq-mx's first addressed leg, not its WAN1 leg; no field
  selects a specific leg).
- `docs/PRODUCT_GAPS.md` — G24 recorded in §8's table (not yet a numbered GAP; tracked as a
  fidelity gap pending a leg/segment-scoped peer selector).

**Affected code:** `twins/corp-twin/personas/*.yaml` (cast + local variants),
`twins/corp-twin/tfc/{default,gate90}.yaml`, `tests/test_corp_twin_mix.py` (new).

## 2026-09-28 — Mirror gretaps carry outer DF off (`nopmtudisc ignore-df`): full-size mirrored frames were silently lost over the 1500-MTU transport

**Authority:** implementation (Claude Code)

**What changed:**
- `attach-gre-tunnel` (`axrange/adapter_proxmox/ssh.py`) creates every mirror gretap with
  `nopmtudisc ignore-df`, and on the adopt path asserts it in place with a changelink
  (`ip link set <dev> type gretap nopmtudisc ignore-df`; same ifindex, remote/key preserved,
  idempotent, fatal on failure). An oversized encapsulation is now fragmented on the transport and
  reassembled by the sensor kernel before GRE decap, instead of dropped.
- The MTU math is corrected: a mirrored full-size frame encapsulates to 20 + 8 (GRE + key) + 14
  (inner Ethernet) + 1500 = **1542 B, 1546 B with an 802.1Q tag** — not "1500 + 24". The
  transport vnet has never been raised above 1500 (the ratified "preferred" option was not
  implemented), so every full-size frame exceeded it.

**Why:**
- corp-twin live run 3 (dev45): up to ~30% of frames lost on wan1-isp (`tx_errors`, inner IPv4 DF
  → `tnl_update_pmtu` E2BIG) and ~17% on sfp25 (`tx_dropped`, 802.1Q trunk frames → outer-DF
  `ip_fragment` failure; summed across gretaps it equalled the host's `IpFragFails` delta). A
  mirror has no sender to act on the ICMP frag-needed, so both were silent. A netns rig reproduced
  both counters exactly (150/150 frames lost) and 0 lost with the flags.
- Fragmenting needs no change on an operator-managed sensor (any Linux `gretap external`
  reassembles); raising the transport MTU to ≥ 1546 end to end remains the cheaper path at high
  rates (GAP-112).

**Plan impact:** none — op inputs are unchanged, so plan ids and plan JSON are byte-identical;
only the adapter's shell commands differ. A standing tap heals on a plain re-apply.

**Affected docs:**
- `docs/ARCHITECTURE.md` §3 MTU math; `docs/design/04-networking-capture.md` MTU;
  `docs/WORK_ORDER_corp_twin.md` §11 live run 3 (mirror loss); `docs/PRODUCT_GAPS.md` GAP-112.

## 2026-09-28 — `tfc` refuses a persona whose `os_family` differs from its host's; two Windows-host cast personas were mislabeled `linux`

**Authority:** implementation (Claude Code)

**What changed:**
- `cast-corp-workstation-{heavy,light}(-local)` in `twins/corp-twin/` were `os_family: linux`
  while the plain `cast-corp-workstation(-local)` is `windows` — every driver renders for the
  **persona's** declared `os_family`, never the host's actual OS, and nothing checked the two
  agreed. Fixed to `windows` in all four files; `pit-395a61f04e-1780405200/` carries the same bug,
  left unchanged (not yet measured).
- `axrange/o2control/traffic.py`'s `resolve_traffic_bindings` now refuses, with one message naming
  every offending host, any assignment whose persona `os_family` differs from the host OS — the
  manifest OS when `--images` is passed, else the twin plan's own recorded `wait-os-ready`
  `os_family` when it is `windows` (a recorded `linux` is never trusted, since `pass1_emit`'s
  fallback is `linux`). Covers `tfc validate`/`plan`/`deploy` against a live twin with no extra
  flag. `--images <root>` (new, `tfc validate|plan|deploy`) also enables an **offline** check
  against a twin folder's `te.yaml`-declared image OS, no state DB or live gate needed.

**Why:**
- corp-twin live run 3: 4 of 83 `drive-persona` ops failed every attempt with
  `Failed to execute child process (No such file or directory)` — the scripted driver launched its
  Linux client (`sh`/`python3`) over a Windows host's QGA. The inventory's `os` field is `None`
  unless manifests are passed, and no tfc verb passed them, so the mismatch was invisible until
  execution.

**Affected docs:**
- `docs/WORK_ORDER_corp_twin.md` §11 "Live run 3 — full twin at gate 0.9" (the diagnosis, fix and
  re-verification record).
- `docs/PRODUCT_GAPS.md` — **GAP-111** opened: `sme-flat` and `ics-ot` bind the Windows catalog
  `corp-workstation` persona onto Debian hosts (pre-existing, caught by the new strict repo-wide
  guard in `tests/test_tfc_persona_os.py`; exempted via `_KNOWN_MISMATCHES` pending a content fix).

**Affected code:** `axrange/cli/main.py`, `axrange/o2control/traffic.py`,
`twins/corp-twin/personas/cast-corp-workstation-{heavy,light}(-local).yaml`,
`tests/test_tfc_persona_os.py` (new).

## 2026-09-28 — corp-twin `gate90` tap/tfc variants for the reduced dev45 deploy; devlab live run 2 PASS

**Authority:** implementation (Claude Code)

**What changed:**
- New `twins/corp-twin/{tap,tfc}/gate90.yaml` variants sized for the ~0.9-gate reduced deploy on
  dev45 (the defaults name hosts/segments the 0.9 gate removes) — `--variant gate90` on
  `tap`/`tfc deploy`/`validate`.
- `corp-twin-devlab` live run 2 (redeployed fresh after live run 1's fixes) **PASSED D0–D8**: port
  map, STP, 802.1Q, VRRP failover, the admin-down port, and containment all verified, clearing the
  devlab-first gate for the full 147-VM twin to proceed (using `ccb404b5`'s egress probe fix).

**Why:**
- The devlab-first runbook (§11.D) exists precisely to catch device-parity issues before the full
  twin deploys; run 2 is the record that it now does, cleanly, after live run 1's three fixes.
  `gate90` exists because the full twin's default footprint needs a smaller variant to deploy
  within dev45's capacity at a 0.9 gate.

**Affected docs:** `docs/WORK_ORDER_corp_twin.md` §11.D (live run 2 record), §10 (gate90 slice).

**Affected code:** `twins/corp-twin/{tap,tfc}/gate90.yaml` (new), `twins/corp-twin/twin.yaml`,
`tests/test_corp_twin_device_parity.py`.

## 2026-09-28 — Egress `measure` healthcheck probes every LAN leg from a foreign (non-router) source; the router's own address is a martian source and always EINVAL

**Authority:** implementation (Claude Code)

**What changed:**
- `axr-egress-phase.sh --healthcheck`'s load-bearing FIB assertion
  (`ip route get <dst> iif <leg> from <addr>`) now probes from a deterministic, non-local host
  address on each LAN leg (`axr_probe_src`, lowest qualifying host on the leg's connected prefix,
  excluding the router's own addresses and the sink) — and asserts **every** LAN leg, not a
  sample of one. A `/32` leg with nothing to probe from is reported `skip`; zero probeable legs is
  a FAIL.

**Why:**
- corp-twin devlab live run 2 found `measure`'s FIB assertion always failing
  (`RTNETLINK answers: Invalid argument`) even when the policy was correctly applied, reading as a
  false alarm — exactly the risk the router slice's E1 finding predicted. Root cause: the probe
  used `from <the router's own address>`, which is a martian source on input (EINVAL on any Linux
  with `accept_local=0`) regardless of the routing policy in effect — the source, not the kernel
  version, was at fault.

**Affected docs:** `docs/SLICE_ROUTER.md` (measure-phase assertion description);
`docs/PRODUCT_GAPS.md` GAP-100 (the fix note recorded there per its own history).

**Affected code:** `axrange/adapter_proxmox/egress_render.py` (`axr_probe_src`, `axr_assert_measure`),
`tests/test_egress_phase.py`.

## 2026-09-28 — corp-twin devlab live run 1: networkd reload ordering, an unquoted heredoc ran `tcpdump`, apt-daily raced provisioning

**Authority:** implementation (Claude Code).

**What changed:**
- First live attempt at `corp-twin-devlab` (deploy `corp-twin-devlab-f2cbf88e`, dev45) **FAILED at
  D0**: two `run-script` ops died after 3 attempts each, halting 12 downstream ops. Root-caused from
  the state DB and read-only QGA reads, not from a rerun.
- **`ms120-app` (exit 1).** `serve-l2switch.sh` rewrote the netplan file but never made
  systemd-networkd reload it; Debian 12 kept the boot-time static address on `eth0` in memory and
  re-applied it once the port carrier came back up, so the recipe's final no-address assertion died
  against a mixed state (every port correctly enslaved/STP-converged, but `eth0` still holding its
  old address). Fix: rewrite every port first, then one **unconditional** `netplan generate` +
  `networkctl reload` that fails loud, then flush/enslave — unconditional because an earlier partial
  apply can leave the file already correct but networkd not yet told. Ports also gain
  `optional: true` so `wait-online` never blocks on an address-less one, and the netplan loader now
  reads an all-digit MAC as a string (YAML 1.1 parsed `52:54:00:00:00:01` as a base-60 int).
- **`sink-app` (exit 100 on attempt 3).** The shared sink recipe's nftables heredoc is unquoted, and
  a comment inside it quoted `` `tcpdump` ``/`` `nstat …` ``/`` `ip route get` ``/`` `local` `` in
  backticks — the shell ran each as a command substitution while writing `nftables.conf`, so writing
  the config launched `tcpdump` with no arguments, forever (introduced in `f20b9825`, 2026-09-23,
  never re-run end to end since). Attempt 3 then hit `apt-daily-upgrade.timer` firing ~15 min after
  boot (Persistent, missed its last run at image bake): it upgraded `qemu-guest-agent` mid-attempt,
  orphaning the exec, and raced a later apt step into the dpkg lock (exit 100). Egress/DNS/containment
  were not at fault. Fix: the backticks are gone from the heredoc; a new `axr_apt_quiesce` disables
  both apt-daily timers and waits (bounded) for any run in flight before the first package step, and
  both `apt-get` calls carry a lock-timeout.
- **Engine:** `axrange/config_exec/backend.py` now records the **stderr tail** (plus the stdout
  tail) on a failed `run-script`/`provision-app`, not the stdout head — the recorded message had
  been `(stdout or stderr)[:300]`, so the `FAIL` line (on stderr) was silently dropped in the
  `ms120-app` diagnosis above. The "shebang" hint now fires only on exit 126/127.
- New tests: `tests/test_twin_recipe_heredocs.py` (fails on any unescaped backtick in an unquoted
  heredoc in any twin/target-environment recipe; also checks corp-twin's payload embeds the current
  shared recipe and that the sink quiesces apt first), `tests/test_corp_twin_device_parity.py`
  additions (netplan rewrite → reload → flush/enslave order), `tests/test_config_exec.py` (stderr
  tail).

**Why:**
- Both failures were invisible to static validation and to a green-looking prior review round —
  they only surfaced against a real Debian 12 boot and a real apt-daily timer, which is exactly what
  the devlab-first runbook (§11) exists to catch before the 147-VM twin deploys.

**Affected docs:**
- `docs/WORK_ORDER_corp_twin.md` §11.D — the live run 1 record.
- `docs/LESSONS.md` — LES-349 (generated network config must be reloaded+validated by the tool's own
  path, unconditionally), LES-351 (unquoted heredoc backticks), LES-353 (apt-daily races
  provisioning) added; LES-346 gains a recurrence pointer (the stderr-tail engine fix).
- `docs/PRODUCT_GAPS.md` — GAP-109 (apt-daily needs an engine/image-level fix, not a per-recipe
  quiesce), GAP-110 (`twins/svc-engines/scripts/provision-sink.sh` still embeds the pre-fix recipe)
  opened.

**Affected code:** `axrange/config_exec/backend.py`; `twins/_shared/scripts/serve-sink.sh`;
`twins/corp-twin/scripts/{provision-sink.sh,serve-l2switch.sh}`; `tests/test_config_exec.py`,
`tests/test_corp_twin_device_parity.py`, `tests/test_twin_recipe_heredocs.py` (new).

## 2026-09-26 — corp-twin device parity as Debian hosts (implementation substitution of the VyOS ruling), planner engine enablers, `te egress` containment gate

**Authority:** **implementation (Claude Code)**, orchestrator's call while the user was away —
**reversible, content-level substitution** of the user's 2026-09-25 ruling (WO §2: full device
parity with **VyOS VMs**); the engine enablers and containment-gate mechanics below are also
implementation (Claude Code). **Still open for the user (WO §12 D1):** accept this substitution as
parity, or order the VyOS path instead (a locked-contract change to `IR_SCHEMA` + `OP_CATALOG`).

**What changed:**
- Every internal corp-twin device (L2 switches MS120/3850/8325h-pair/access switches, the 9K01/9K02
  pair) is built as a Debian `kind: host` VM running a role recipe instead of a VyOS VM: L2 switches
  get a VLAN-aware Linux bridge with real 802.1Q trunk tags, kernel STP, and an admin-down port
  (`scripts/serve-l2switch.sh`); the 9Ks run keepalived VRRP as the HSRP analogue
  (`scripts/serve-vrrp-l3.sh`). **`hq-mx` stays the only `kind: router`, the only WAN NIC, the only
  `egress:` carrier** — containment still lives on one contained edge, unchanged. Reason for the
  substitution: VyOS ships no QGA/cloud-init, so it cannot run a `run-script` recipe, and an
  internal (no-WAN) VyOS device needs `IR_SCHEMA`/`OP_CATALOG` changes not yet built; the
  device/segment topology is already device-shaped, so reversing to real VyOS later is an
  image+recipe swap per device.
- **Planner engine enablers** (twin-config/planner-internal only, no version-locked contract
  touched): L2 legs (`nets[].ip: none` / `l2: true`, no address, refused on a router); segment
  `isolated: true` (the NET-010 exemption, restricted to "same subnet as exactly one anchor
  segment"); per-CIDR IP allocation with gateway inheritance across an L2 extension; the planner's
  first-leg gateway rule with positional `ipconfigN` placeholders (an address-less leg ahead of an
  addressed one keeps an explicit `{"ip": None}` slot so numbering stays aligned with `netN` — PVE's
  `ipconfigN` is positional against `netN`); the served-hosts closure through forwarders; and
  forwarder-ordering edges (BFS-layered by component, acyclic by construction).
  `segments[].reserved: [ip, ...]` (twin-config-only) reserves an address out of a CIDR's pool.
  **Byte-identity measured:** every plan of every other shipped `twin-configs/`/`twin-configs-cast/`/
  `twins/` config and every `target-environments/` scenario is unchanged — same ops, inputs, edges,
  `plan_id` — before and after.
- **`axrange te egress [ID|TWIN] --show | --phase measure|provision`**
  (`axrange/o2control/containment.py`): reads or flips a deployed router's live egress phase over
  QGA and merges the result into the `configure-router` op row so `te info`/`te inventory`/the
  tap-tfc containment gate see the router's actual current state, not just what it reported at
  deploy time. **Invalidate before acting:** the row is rewritten to an `*-unverified` sentinel
  BEFORE the guest is touched, and any exec failure (non-zero `--show`, a QGA timeout, a transport
  exception) leaves that sentinel and raises — no failure path can leave an earlier `measure`
  reading standing. A `measure` is only trusted when its own `--healthcheck` asserts AND names the
  same phase `--show` did. **The containment gate now probes live** — `_containment_gate` runs one
  `egress_live(..., "show")` itself per deploy attempt whenever the target declares an `egress:`
  posture, rather than trusting the last-recorded row; **this supersedes the same round's earlier
  call** ("the gate reads the recorded row, not a second probe"), recorded in WO §10 P0 review
  round 2, not in a prior CHANGELOG entry.
- Fidelity gaps G1–G5, G9 (partial), G14 in WO §8 close under this substitution (offline only —
  nothing here is live-verified; see the 2026-09-28 entry above for the devlab attempt).

**Why:**
- The user was away and the VyOS ruling could not be met without contract-locked engine work; this
  delivers the *wire behaviour* of the ruling (bridges, trunks, STP, a down port, VRRP, a routed
  hop) on what the engine already runs, kept explicitly reversible and named against the ruling it
  deviates from, per `CLAUDE.md` "Design Document Maintenance."

**Affected docs:**
- `docs/WORK_ORDER_corp_twin.md` — status header, §4.1 (the substitution), §8 (gap statuses), §10
  (P0/P1/P2/D slices BUILT), §12 D1 (still open for the user).
- `docs/PRODUCT_GAPS.md` — GAP-101 marked **Resolved** (the `egress:` twin-config key this gate
  depends on landed pre-branch, 2026-09-23, `6b8cc50b`; not previously closed in the register).
  GAP-103–GAP-108 opened (WAN2 failover, hq-mx NIC ceiling unvalidated, API/serve personas path,
  PVE ipconfig-less-NIC boot behavior unverified, SDN VLAN-zone tags/trunks not authorable, internal
  VyOS needs contract changes).
- `docs/LESSONS.md` — LES-348 (invalidate a safety verdict before acting), LES-350 (PVE `ipconfigN`
  is positional) added.

**Affected code:** `axrange/twinconfig/{schema,expand}.py`, `axrange/planner/{pass1_emit,
pass3_ordering,state}.py`, `axrange/o2control/containment.py` (new), `axrange/adapter_proxmox/rest.py`,
`axrange/adapter_proxmox/resolve.py`, `axrange/api/dtos.py`, `axrange/cli/main.py`,
`twins/corp-twin/scripts/{serve-l2switch,serve-vrrp-l3,serve-hq-mx-routes}.sh`,
`twins/corp-twin-devlab/` (new), `tests/test_corp_twin_device_parity.py`, `tests/test_egress_phase.py`.

## 2026-09-26 — Twin consolidation Phase 2 (+ Phase 3 partial): ten twins migrated into `twins/<id>/`, `--twins-dir`

**Authority:** **user** (2026-09-25) for the `twins/<id>/` layout ratified in the prior entry;
**implementation (Claude Code)** for the §7 D2–D6 execution calls below (the orchestrator's call
while the user was away). **D1 (`target-environments/`) and Phase 4's deletions stay open for the
user.**

**What changed:**
- `git mv`'d ten twins into `twins/<id>/` per the ratified layout: `corp-enterprise`, `datacenter`,
  `dmz-perimeter`, `ics-ot`, `sme-flat`, `svc-engines`, `svc-verify` (from `twin-configs/`);
  `corpapcn-golden`, `corpapcn-twin`, `pit-395a61f04e-1780405200` (from `twin-configs-cast/`, **D5**:
  migrate all three casts of the same network); shared assets to `twins/_shared/`. **D2:** keep the
  discovered twin's real id (`pit-395a61f04e-…`) as its folder name rather than renaming to
  `corpapcn-disco`. **D3:** `twin.yaml`'s `containment:` is derived from `te.yaml`'s `egress:` block,
  never re-typed (only `svc-engines` declares one). **D4:** the aximprint installer is unchanged;
  readers (`axrange/twinconfig/bundle.py`) learned the twin layout instead — Console **apply**
  (`install-cast`) into a twin folder is refused with a 422 until an import step exists (future
  work). **D6:** the maxrate series archives to
  `docs/archive/twin-runs/2026-08-13-corp-enterprise-maxrate/`; its 12 in-tree duplicate copies, the
  three now-repointed alias symlinks, and `scenarios/` are held for the user's Phase 4 deletion call.
- **Proof:** every twin's full plan (every op + params, barriers, edges, `plan_id`) is byte-identical
  before/after for all ten twins; `scripts/twin_triage.py --json` re-classifies every moved row
  identically except the three files that had pinned a destroyed deploy id (now
  `plan-needs-live-target`, as predicted) and `corp-enterprise/tfc/roles.yaml` (now defaults its
  target to the twin id inside a twin folder). The real-bundle tests (API and Console vitest) were
  changed to **fail**, not skip, when their fixture is absent — proving a path rename can't go
  unnoticed under a `skipif` guard again.
- **Phase 3 (partial):** `--twins-dir` is now the flag on `te --config`, `serve`,
  `twin-config list|show` (`--twin-configs-dir` kept as an alias, one `dest`); `AXRANGE_TWINS_DIR`
  wins over `AXRANGE_TWIN_CONFIGS_DIR`; `registry.default_catalog_dir()` is `twins/` (the default
  search path keeps `twin-configs/` first, as the Phase-4 compatibility reader); the scorecard's
  default reference root is `twins/`; imprint/Console-file/recompile/topology-export/fidelity
  readers share `axrange/twinconfig/bundle.py`.
- corp-twin's own sink payload/build script and its catalog test were repointed onto the new
  `twins/` paths after the consolidation merge landed underneath it (`010a0f29`).

**Why:**
- One twin was previously scattered across up to four roots with no single owner for
  scripts/personas/generated artifacts; this executes the layout the user ratified 2026-09-25,
  proving byte-identical plans and re-triage results as the acceptance bar for a pure relocation.

**Affected docs:**
- `docs/WORK_ORDER_twin_consolidation.md` — §7 D2–D6 decisions recorded, Phase 2+3(partial) record
  (§ "Phase 2 + Phase 3 (partial) record"), status header.
- `docs/CONVENTIONS.md`, `docs/TWIN_TRIAGE.md`, `docs/design/{09,13,14,15}` — already repointed to
  `twins/<id>/` in-commit (`f068f478`); no further doc change needed here.
- `docs/LESSONS.md` — LES-352 added (a "skip if the fixture is missing" test goes silently green
  once the content moves; keep one non-skippable presence test).

**Affected code:** `axrange/twinconfig/registry.py`, `axrange/adapter_proxmox/egress_render.py`,
`axrange/api/{app,serve,settings}.py`, `axrange/planner/egress.py`, `axrange/cli/main.py`,
`o2/console/src/views/Level0.tsx`, `scripts/{gen_egress_policy,gen_sink_corpus,twin_triage}.py`,
`tests/test_twin_bundle_layout.py` (new), `twins/` (ten twins relocated, `_shared/` new),
`docs/archive/twin-runs/2026-08-13-corp-enterprise-maxrate/` (new).

## 2026-09-25 — Twin configs consolidate into self-contained `twins/<id>/` folders

**Authority:** **user** (2026-09-25) for the layout and the rejected alternative;
**implementation (Claude Code)** for the Phase 1 triage and the phase plan.

**What changed:**
- Every twin's per-tier configs will live in ONE folder, `twins/<id>/`: a `twin.yaml` root manifest
  (id, description, target cluster, containment, `tiers:` → the default file per tier + its
  variants), `te.yaml`, optional `o2.yaml`, `tap/<variant>.yaml`, `tfc/<variant>.yaml`,
  `exr/<name>.yaml`, `personas/`, `scripts/`, and a git-tracked `generated/` for machine-written
  artifacts (topology, provenance, scorecards, layout). Cross-twin shared assets only in
  `twins/_shared/`. Single tier files stay deployable standalone.
- Phase 1 (read-only triage) done: 281 files across `twin-configs/`, `twin-configs-cast/`,
  `target-environments/`, `scenarios/` classified by the real offline CLI —
  `docs/TWIN_TRIAGE.md`, reproducible via the new read-only `scripts/twin_triage.py`. Every te
  config and archetype scenario validates and plans; the only failures are four
  `corpapcn-disco` tier files (an aximprint tap template with no sensors; three files pinning a
  destroyed deploy id). Phases 2–4 (migrate + manifest loader, repoint defaults, retire old roots)
  are planned, not started.

**Why:**
- One twin was scattered across up to four roots (catalog folder, cast bundle, a symlink alias
  folder, a dated snapshot), twin-local personas loaded only via a hand-exported
  `AXRANGE_PERSONAS_DIR`, and tier files pinned deploy ids that die with the deploy.
- **Rejected: one YAML with per-tier sections** — variant explosion (`corp-enterprise` alone has
  8 tap + 11 tfc variants), non-YAML assets (scripts, personas, rules, generated JSON) need a
  folder anyway, and tiers have separate lifecycles and separate writers (Console canvas,
  aximprint, hand edits).

**Affected docs:**
- `docs/WORK_ORDER_twin_consolidation.md` — new (decision, phases, open decisions D1–D6)
- `docs/TWIN_TRIAGE.md` — new (the triage table, dispositions, cross-reference list)

**Links:** open decisions for the user in the work order §7 — incl. whether `target-environments/`
archetypes fold into `twins/` or stay separate (D1), not decided here.

## 2026-09-21 — Protocol engines: real clients, a containment reversal, the any-IP sink, and a live-verify with two open findings

**Authority:** **user** (2026-09-21) for the four rulings this round executes — the containment
reversal, the purpose-built target twin, engine build order, Windows in scope
(`docs/WORK_ORDER_protocol_engines.md` §2); **implementation (Claude Code)** for every slice's
mechanics and the live-verify itself.

**What changed:**
- **E0 — mixed engines per host.** The `claimed_hosts` refusal now allows multiple **distinct**
  persona instances on one host (`(host, persona)` stays unique); `replay[]` keeps its exclusivity.
- **E1 — router egress phases.** `OP-HOST-090 configure-router` gains a `provision`/`measure` phase
  input (no new op): `provision` masquerades to WAN as before, `measure` drops WAN-forwarded
  packets so the sink is the only route off-twin. **Fail-safe default CLOSED** — an unphased or
  failed-transition twin sits in `measure`. Ordering finding, recorded in the work order: **install
  everything before the twin's own name service is adopted** — once a guest resolves through the
  twin resolver (E3), it has no upstream and `apt` fails, one hop earlier than the router phase
  would have caught it.
- **E2 — the any-IP sink.** One VM: `ip route add local 0.0.0.0/0 dev lo` + nftables/TPROXY
  dispatch (port-first, first-bytes sniff as tiebreak) to per-protocol responders — DNS (authority
  from the cast's observed name→IP set), TLS/HTTP/1.1/HTTP/2/HTTP/3 via Caddy with a lab CA issuing
  per-SNI on demand, NTP, SSH, SMTP-ish fallback, generic TCP last resort.
- **E3 — twin name service.** The twin resolver (bind9) is authoritative for internal names and
  forwards everything else to the sink's DNS. Windows composition is strict: a domain member's DNS
  must be the DC (AD-integrated DNS forwards to the twin resolver), never bind9 directly, or joins
  and SRV lookups break. Finding: a twin serves no PTR records, and OpenLDAP reverse-resolves the
  SASL host before building the SPN it asks the KDC for — `ldapsearch -Y GSSAPI` fails with a
  Kerberos-shaped error (`Server not found in Kerberos database`) from a ccache that already holds
  a valid ticket for that exact service (`kvno` succeeds). Fixed per-client (`-N`), not by serving
  synthetic PTRs.
- **E4 — `drive: browser`.** Headless Chromium, one engine covering DNS + real TLS (real SNI/JA3) +
  HTTP/1.1/2/QUIC. Fallback containment lever if E3 is unavailable:
  `--host-resolver-rules=MAP * <sink-ip>`.
- **E5 — real Linux clients, and the containment reversal.** `command`'s `_check_containment` no
  longer refuses a hostname in argv (checks 1 and 3 — declared-address-present, no-undeclared-
  endpoint — are unchanged; only check 2, the IP-literal requirement, is reversed, and stays
  reachable via `require_ip_literals=True`). Seven new client templates: `smb` (`smbclient`),
  `ldap` (`ldapsearch`), `kerberos` (`kinit`/`kvno`), `dcerpc` (`rpcclient`), `rdp`
  (`xfreerdp +auth-only`), `ssh`, `snmp` (`snmpget`).
- **E6 — Windows domain-member engine.** PowerShell built-ins only (`gpupdate`, `nltest`, `klist`,
  `net use`, `w32tm`, `Resolve-DnsName`, `Invoke-WebRequest`) driving
  Kerberos/LDAP/SMB/RPC/DNS/NTP, including the dynamic-port RPC behind the previously-unlabelled 6%.
- **E7 — DEFERRED.** Baking the toolset + Chromium + the lab CA into images was scoped but not
  built this round; tools and the lab CA are installed at provisioning instead
  (`twin-configs/svc-engines/scripts/prepare-engine-client.sh`). Cost: every deploy pays an apt
  install (and must do it before E3's resolver is adopted, per E1's ordering finding above) instead
  of paying it once at bake time; `install:` on a **standing** twin would trip `--update`'s
  in-place-mutation refusal, and LES-024 says bake fragile tooling — so E7 remains fair game, not
  abandoned.
- **E8 — `svc-engines`, the purpose-built proof twin.** `twin-configs/svc-engines/`: resolver, web,
  file server, DC, Linux client (E4+E5+`scripted`, the E0 proof), Windows client (`joins:`), sink
  (E2), router (E1).
- **E9 — gated `dev45` live-verify, PASSED with two open findings (below).** Twin
  `svc-engines-24131435`, **97/97 ops COMPLETED**, 8 nodes. AD promoted; Windows member joined
  (`PartOfDomain=True`, `corp.axr.lab`). `scripts/verify_wire_protocols.py` over a 12-minute window
  (65,067 eve records, 19,917 flows): **PASS dns/smb/snmp/ssh**; SMB negotiated dialect **3.11**
  with **133 successful TREE_CONNECTs**; SSH — 12 records, both banners present on every one.
  **Only 31 of 19,917 flows (0.16%) unclassified** — the shape-only "bare tcp/udp" failure mode
  (measured previously in the axquery discovery loop, where the DC RST the personas' filler bytes)
  is essentially gone. Persona-side: kerberos ok=4/4, ldap ok=2/2, ssh ok=2/2, smb ok=8/8,
  snmp/dns/ntp ok. The twin's own name chain proven from a client:
  `web.svc.axr.lab`→10.66.10.12, `dc.corp.axr.lab`→10.66.10.21, `github.com`→**140.82.113.3, the
  real observed address**, all from the twin's own DNS; the sink minted a per-SNI cert for
  `1-courier.push.apple.com` from the lab CA.

**Findings the live run produced (the durable value of the round):**
1. PVE `agent/exec` refuses a single argument ≥ 131072 bytes, reported as `HTTP 596 Broken pipe`
   (names the transport, not the size) — binary-searched to the byte on PVE 9.2. Fixed by chunked
   `write_file` (splits base64 across arguments and across requests past ~192 KiB, verifying the
   in-guest byte count) and a gzip+base64 sink corpus (161 KB → 54,550 bytes, all 613 observed
   names intact). **Amends LES-002.**
2. A recipe must install what provides every binary **and** every dlopen'd mechanism it uses.
   `samba` does not ship `smbclient`; `snmpd` does not ship `snmpget`; `ldap-utils` does not pull
   the SASL GSSAPI plugin (`libsasl2-modules-gssapi-mit`) — and a mechanism has no binary, so a
   `command -v` census is structurally blind to it. The GSSAPI failure read as "the server refused
   GSSAPI." **New lesson LES-345.**
3. A fail-loud check that discards the tool's stderr names a cause it never observed: the file
   server's SMB check blamed `smb.conf` when the real cause was elsewhere; it now keeps and prints
   the tool's own output. **New lesson LES-346.**
4. `ss -lnt`/`-lnu` omit the Netid column (the local address is `$4`, not `$5`) — the sink's own
   self-check read all seven of its live listeners as FAIL.
5. Debian's `snmp` package ships no MIBs, so `sysDescr.0` fails against a healthy agent; the check
   now asks for the numeric OID (`.1.3.6.1.2.1.1.1.0`), matching what the persona already sends.
6. Our own browser success counter was a false positive: Chromium exits 0 having rendered an error
   page, so `ok=8` meant nothing. Judged from the DOM now. Chromium's background networking also
   polluted the twin's DNS census (`dl.google.com`, 976 queries; 6,250 NXDOMAINs for names the
   twin never observed) — now disabled. **New lesson LES-347.**
7. A host pointed at the twin's authoritative resolver cannot install packages afterward — install
   first, repoint DNS last (E1/E3's ordering finding, restated as the general rule; recorded in
   `twin-configs/svc-engines/scripts/prepare-engine-client.sh` and pinned by
   `tests/test_svc_engines_recipes.py`).
8. A long-lived twin drifts from the repo: the deployed guest's `krb5.conf` lacked
   `[domain_realm]` although the committed recipe carries it.
9. The standing O2 suricata sensor was found dead again (silent since 2026-07-27; a recurrence of
   GAP-067), and its image ships without `python3` or `tcpdump`, so on-sensor verification needs an
   install first (**recorded on GAP-067** below).
10. Suricata 7.0.10 has no LDAP parser (arrives in 8.0) and logs no `ntp`, so an ABSENT ldap
    verdict is a **sensor** limit, not an engine failure; Kerberos scored PARTIAL for the same
    reason (`KRB_ERR_RESPONSE_TOO_BIG` forced a TCP retry that Suricata logged as an unclassified
    flow, while the engine's own counters show kinit+kvno succeeding 4/4). **New gap GAP-102.**

**Two open items, recorded as gaps, not dressed up:**
- **GAP-100** — the sink's any-IP acceptance does not deliver to its listening sockets. SYN packets
  for an external destination arrive at the sink's `eth0` (tcpdump-proven), routing/nftables/rp_filter
  all check out, Caddy listens on `*:443` — and no SYN-ACK is ever sent. Undiagnosed beyond that.
  Consequence: with the measure phase ON, external destinations are correctly contained (they reach
  nothing) but the sink does not yet answer them, so E2's own acceptance criterion is unmet.
- **GAP-101** — the `egress:` key cannot yet be authored in a twin config (`TwinConfigNode` forbids
  extra keys), so E1's phase policy had to be applied by hand for this run — which is why a
  containment breach existed for **part of the run and is now closed**: before the measure phase
  was applied, the router's default route went to the WAN (which has real internet access), and
  `github.com` presented a real **Sectigo** certificate, not the lab CA, proving external traffic
  briefly left the twin. Record prominently: **a twin not explicitly switched to measure phase can
  reach the real internet.**
- Also already filed by the builder mid-round: **GAP-099** (`xfreerdp` exits 1 with no output at
  all against an open 3389).

**Why:**
- A detection rule matches protocol *semantics* — a query name, an RC4 TGS-REP, a TLS SNI — never a
  port number, so `scripted`'s shape-only filler cannot exercise it and cannot produce a realistic
  false-positive rate either, which the measured target's flow mix (DNS 70%, TLS 11%/6.3 of 8.9 GB,
  SMB 2.2 GB internal, Kerberos/LDAP/RPC/RDP <1% of flows but carrying the detection value) makes
  concrete. The IP-literal containment rule was reversed because it did not make Kerberos/LDAP/SMB
  *contained*, it made them *impossible* — a ticket is issued against an SPN, not an address — so
  containment had to move from a render-time argv check to a network property (E1+E2+E3 together).

**Affected docs:**
- `docs/WORK_ORDER_protocol_engines.md` — status header updated (E0–E6/E8 BUILT + live-verified, E7
  DEFERRED, E9 partial-pass); per-slice status lines.
- `docs/PRODUCT_GAPS.md` — GAP-100, GAP-101, GAP-102 opened; GAP-067 gains a 2026-09-21 recurrence
  note.
- `docs/LESSONS.md` — LES-002 amended; LES-345, LES-346, LES-347 added.
- `docs/SESSION_STATE.md` — replaced; the standing `svc-engines-24131435` twin and the open
  `.claude/settings.json` gates recorded as gotchas.

**Affected code:** `axrange/personas/drivers/{command,command_windows,browser,scripted,base,__init__}.py`,
`axrange/personas/schema.py`, `axrange/adapter_proxmox/{guest_agent,rest,egress_render}.py`,
`axrange/o2control/traffic.py`, `axrange/planner/pass3_ordering.py`, `axrange/validator/catalog.py`,
`scripts/{verify_wire_protocols,gen_sink_corpus,gen_egress_policy,gen_role_listen}.py`,
`twin-configs/svc-engines/` (new), `twin-configs/_shared/scripts/serve-sink.sh`,
`docs/VALIDATOR.md` (PSN-030 gains `browser`; rule count unchanged), `docs/design/07-traffic-personas.md`.

**Supersedes:** `docs/WORK_ORDER_protocol_faithful_traffic.md` §2.2's ratified *"IP literals only,
never guest-side name resolution"* containment rule — reversed by ruling 1 above. The reversal's
own rationale is recorded verbatim in `axrange/personas/drivers/command.py`'s module docstring
("Containment is a NETWORK property, not a render-time argv check").

**Links:** `docs/WORK_ORDER_protocol_engines.md`, `docs/WORK_ORDER_protocol_faithful_traffic.md`
§2.2, `docs/WORK_ORDER_protocol_faithful_services.md`.

## 2026-09-19 — Apply-proof follow-ups GAP-087..090: a compiler change is its own outcome, the fingerprint pair is renamed for what it is (BREAKING), and the no-op warning says which reading applies

**Authority:** implementation (Claude Code) — fix direction taken from each gap's own fix sketch in
`docs/PRODUCT_GAPS.md`

**What changed:**
- **GAP-087.** `axrange/api/recompile.py` gains `compare_compiler_builds`, which reads
  `cast_invocation.code_fingerprint` (the build that ran the cast, not the bundle's content) from
  the topology export before and after an apply and reports a **three-valued** `compiler_changed`
  (`true`/`false`/`null` — LES-273/LES-333: "could not tell" is not "no") plus a `compiler_note`
  sentence. Reported, not refused: the recompile is the operator's own request and the new bundle
  is legitimate; an opt-in refusal and pinning the compiler revision were both considered and left
  unbuilt. The Console renders a distinct "compiler changed" outcome only on `compiler_changed ===
  true`; `null` gets its own could-not-tell sentence; an absent field (older server) renders
  nothing — `undefined` and `false` share one branch, deliberately, so a server that predates the
  field cannot look like "unchanged" was measured.
- **GAP-088 (BREAKING API field rename).** The apply response's `code_fingerprint_before/after` —
  described in two docstrings as "the compiler's build hash" but actually copied from the PIT row's
  provenance stamp, which does not move on a re-cast — is renamed to
  **`pit_code_fingerprint_before/after`**. A new **`cast_code_fingerprint_before/after`** pair
  carries the value that actually moves with a compiler upgrade (`cast_invocation.code_fingerprint`
  from the topology export). No aliases: a repo-wide check found no consumer of the old name
  outside the files changed here (not the Grafana plugin, the CLI, or any script).
- **GAP-089.** `GET /api/imprints/{id}/diff`'s `since` parameter description named the wrong field
  (read as "the apply response's `code_fingerprint_before`", which 404s); it now names
  `fingerprint_before` via one constant, `DIFF_SINCE_FIELD` in `axrange/api/app.py`, and
  `tests/test_api_console.py` drives the route with that constant so the prose and the working
  value cannot drift apart again.
- **GAP-090.** A byte-identical recompile's warning now names the specific case it can prove from
  evidence already in hand (install return code + the cast's own record counts) — e.g. "the
  recompile produced no change to the cast (install returned 0) … compiled to the same bytes" —
  without claiming each record took effect (a record naming an entity the cast no longer has
  changes nothing either). The disjunctive "either … or …" wording survives only for the
  genuinely-unknown case (install stage reported no exit code, or no counts could be read).
- **Review fixes landed in the same pass:** `POST /api/imprints/{id}/cast` now publishes an
  additive `bundle_fingerprint` (the same content fingerprint apply publishes as
  `fingerprint_before/after`), and the Console stamps drafts with it instead of the PIT stamp — the
  "recompiled since this draft" check previously keyed on a value that never moves and so could
  never fire; a draft from an earlier Console (no stamp) reads as "could not be checked" (unknown),
  never as "unchanged". The post-apply bundle load + compiler comparison moved inside the apply
  lock (a concurrent apply could otherwise leak its stamp). A malformed build stamp (non-string)
  reads as `null` with a "malformed" reason rather than being coerced. The S4 "read each artifact
  once" test for the `cast` route now allows one extra byte read on the three fingerprinted files
  (parsing is still pinned to once).

**Why:**
- An operator's one suppression was followed by a 22-added/23-removed population change caused by
  an unrelated aximprint upgrade, reported by the Console as "1 element changed" — the map's join
  on observed identity hides exactly this kind of rename, so the compiler-change signal has to be
  surfaced explicitly rather than left for the diff to explain on its own (GAP-087). The field an
  operator would reach for to detect it was itself wrong (GAP-088), the documented workaround for
  diffing since an apply 404s (GAP-089), and the no-op warning left the operator to guess between
  two readings the server already had enough evidence to pick between (GAP-090).

**Affected docs:**
- `docs/PRODUCT_GAPS.md` — GAP-087..090 marked Resolved (built), each with an "as built" line.
- `docs/design/14-operator-assertions.md` §4 — apply's return value.
- `docs/design/15-console-canvas.md` §9/§10 — apply's return value and the `cast` route's fields.
- `docs/LESSONS.md` — LES-333 and LES-273 each gain a one-line example (no new entries).

**Affected code:**
- `axrange/api/app.py`, `axrange/api/dtos.py`, `axrange/api/recompile.py`, `axrange/api/service.py`,
  `axrange/fidelity/reference.py`, `o2/console/src/`, `tests/test_api_console.py`,
  `tests/fixtures/cast-bundles/mini-cast-prov/`.

## 2026-09-19 — Pipeline cost/context: lean hot docs, fresh briefed agents, a lighter default doc pass

**Authority:** user (approved the optimizations); implementation (Claude Code) for the mechanics

**What changed:**
- **Hot docs kept small.** CLAUDE.md trimmed to ~9.7 KB (12 KB budget); compact, once-per-agent
  lesson injection (`scripts/hooks.py`, `scripts/inject_lessons.py`). **Work-order genre rule**
  (`docs/CONVENTIONS.md` §3): a work order is the *active* spec (target ≤ 30 KB, warn > 40 KB);
  built-slice records move verbatim to `docs/archive/work_orders/<name>.built.md` with a pointer line
  per moved section. Applied to `WORK_ORDER_console_canvas.md` (199 KB → 26 KB) and
  `WORK_ORDER_console_level0_rendering.md` (107 KB → 16 KB), line-set-checked (no original line
  lost). **`SESSION_STATE.md` is replaced at every handoff, never appended** (8 KB budget; 32 KB →
  ~7 KB), the prior content moved verbatim to `docs/handoffs/2026-09.md`. `scripts/doc-budget.py`
  gained the SESSION_STATE budget and soft (warn-only) `WORK_ORDER_*.md` budgets.
- **Fresh, tightly-briefed agents** (`.claude/commands/dev.md`, `docs/WORKFLOW.md` "Pipeline mode",
  all four stage agents): one fresh agent per stage from a short brief (task, files, `LES` IDs,
  acceptance tests, trimmed prior hand-off); no whole-file reads of work orders/SESSION_STATE/
  CHANGELOG; a fresh agent per fix round with only the must-fix findings; `SendMessage`-resume only
  for `DECISION NEEDED:`; at most 2 fix rounds before escalating; screenshots downscaled ≤ 1200 px.
  tester and doc-writer run on sonnet.
- **Lighter default doc pass:** every slice → a CHANGELOG entry + a SESSION_STATE replace; specs,
  work orders, PRODUCT_GAPS, LESSONS and CLAUDE.md only when actually affected; a new `LES` only for
  a genuinely new class (a recurrence extends the existing entry). doc-stats/doc-budget/commit-guard
  enforcement unchanged.
- **Fewer stages by default:** the code-writer runs targeted tests itself and fixes its own
  failures; the tester is the **gate only** (one full run + touched Console/plugin suites, writes
  tests only for an untested surface); the code-reviewer runs **only on a review trigger** —
  defined once in `.claude/commands/dev.md` (version-locked contracts, planner/executor/state/locks,
  adapter write paths, deploy/destroy, auth/API write routes, > ~300 changed lines, or on request).
- **Parallel full suite:** `.venv/bin/python -m pytest -q -n auto` (pytest-xdist, now in the `dev`
  extra) — ~56 s vs ~262 s serial on 32 CPUs, identical counts over repeated runs; no test needed
  isolating. The `-m "not slow"` fast subset stays for inner loops.
- **Screenshot cost guard:** `scripts/shrink_image.py` (Pillow, optional) downscales/crops to
  `<name>.small.png`; the `read-guard` hook (`scripts/hooks.py`, PreToolUse `Read`, registered in
  `.claude/settings.json`) denies reading an image > ~300 KB or > 1600 px wide and names the
  command; fails open; `CLAUDE_ALLOW_BIG_IMAGES=1` overrides. Tests: `tests/test_image_guard.py`.
- **The live hooks are the host-global ones.** On this host the hooks that fire are registered in
  `~/.claude/settings.json` and run `~/workflow/pipeline/scripts/hooks.py`, not this repo's
  `scripts/hooks.py` — so the de-duplication above was inert until the global copy was synced
  (`hooks.py`, `shrink_image.py`, and only this change's `inject_lessons.py` diff; the global
  `~/workflow` suite passes 233/233). Measured through the live path: first edit 5.5 KB, repeat 270 B.
  The `read-guard` registration in `.claude/settings.json` stays **local and uncommitted** (that file's
  working copy also drops the tracked hooks block, which would double-fire with the global hooks).

**Why:**
- Full-resolution Console screenshots (2600×1100, 1–1.4 MB) were read 2–3× each per UI check.
- 14 days of transcripts: pipeline agents averaged 230k–450k tokens of context per model call (~3B
  cached tokens re-read); the two Console work orders were re-read 32× and 21×, SESSION_STATE 21×
  (and injected at every SessionStart); resumed code-writers across fix rounds averaged 362k/call.

**Affected docs:**
- `CLAUDE.md`, `docs/CONVENTIONS.md` §3–§4, `docs/WORKFLOW.md`, `docs/SESSION_STATE.md`,
  `docs/handoffs/2026-09.md`, the two Console work orders + `docs/archive/work_orders/`,
  `.claude/commands/dev.md`, `.claude/agents/{code-writer,tester,code-reviewer,doc-writer}.md`,
  `scripts/doc-budget.py`, `scripts/hooks.py`, `scripts/shrink_image.py`, `pyproject.toml` (`dev` extra), `docs/RUNBOOK_o2_live_verify.md`.

**Supersedes:** the SESSION_STATE convention of the 2026-09-16 "Doc archival restructure" entry
(header + two newest dated sections, 60 KB) — now replace-each-time, 8 KB.

