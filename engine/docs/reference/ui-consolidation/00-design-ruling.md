# Unified Console — design ruling (2026-09-29)

**Authority:** implementation (Claude Code), under the user's 2026-09-29 instruction: "design + build
everything", "anything may be superseded, recorded", read-only live access plus Console restart,
live-verify on a fresh small disposable deploy. Evidence: `01-backend-audit.md`,
`02-ui-surface-inventory.md`, `03-ui-history-and-decisions.md` in this folder.

## 1. The problem, in one paragraph

Seven UI efforts in ~13 weeks, each restarted from a new reference instead of converging. The
richest one (the Console canvas) is design-time only, shows nothing for 10 of ~13 twins including
the flagship `corp-twin`, and labels design-time counts "live". The only run-time UI (the Grafana
plugin) froze at the `te` tier in June. Every real operating action since August is CLI + YAML. No
UI answers: what is deployed where, what does it look like, which personas run on which node, how
do I change one node and apply just that. Most of the reads already exist as API endpoints nobody
calls; two things are genuinely missing in the engine: cluster identity and a per-host persona apply.

## 2. The ruling

**One app: the axRange Console** (`o2/console/`, served by `axrange serve --spa-dir`). It is
**run-time first**: it opens on what is running, not on a design file.

Four top-level areas, in this order, in plain words:

| Area | Answers | Source |
|---|---|---|
| **Range** (home) | Which twins are up, on which cluster, with which taps/traffic/exercises, are they healthy | state store + live cluster read |
| **Twin** (one deployed twin) | What it looks like (map + host table), personas per host, per-host health and access, change a host's personas, destroy | inventory + tfc rows + live reads |
| **Catalog** | Every twin in `twins/` and every scenario; validate / plan / deploy (guarded) | twin-config registry |
| **Design** | The existing assertions canvas, unchanged in function, relabelled | `/api/imprints/*` |

Plus **Telemetry**: link-outs to the three live Grafana dashboards (capture health, Suricata alerts,
attack coverage). Grafana stays for telemetry only.

### Rules that bind every screen

1. **Viewer before editor.** Every area must render something useful for every twin, including
   hand-authored ones with no aximprint export. An empty view says why it is empty.
2. **One model, one renderer** for a deployed twin: the state-store inventory
   (`/api/deploys/{id}/inventory`) adapted into the Console's existing `src/layout/*` engine. The IR
   re-parse path (`/api/scenarios/{id}/topology`) is not used for deployed views — it can drift.
3. **Recorded vs. live is always labelled.** Anything from the state store says "recorded"; anything
   read from the cluster says "live" with its age. Never call a design-time count "live".
4. **Never invent facts.** Unknown is shown as unknown, never as zero.
5. **A table is a first-class view.** A 106-VM twin needs a filterable host table (host, segment,
   role, OS, personas, status) beside the map. Map and table share selection.
6. **Every write is preview → confirm → progress → result.** The preview states exactly what will
   start, stop and stay untouched. Destroy requires typing the deploy id.
7. **Plain vocabulary on run-time screens.** Twin, host, segment, persona, tap, sensor, cluster.
   "imprint", "cast", "assertion", "ghost", "occupancy", etags stay inside Design, with a glossary.
8. **The URL is the state** (kept from design/15): `/range`, `/twin/<deploy_id>`,
   `/twin/<deploy_id>/hosts/<host_id>`, `/catalog`, `/design/...` (existing grammar re-rooted;
   old `/imprints/...` URLs redirect).

## 3. Engine and API additions

No version-locked schema changes. `state.db` stays at schema v3 — the standing `corp-twin` deploy
must keep working untouched.

### 3.1 Cluster identity and multi-cluster (need a)
- A **targets file** (config over flags): named targets, each `{name, adapter_config, db}`.
  `axrange serve --targets <file>`; with none given, today's single adapter.conf + db is the one
  target, named after its `DEFAULT_NODE`. `/api/targets` returns all of them.
- A deploy's cluster = the target whose store it was read from, cross-checked against
  `placement_json` node names; a mismatch is surfaced as a warning, never hidden. tap/tfc/exr rows
  inherit the parent te deploy's cluster.
- Every read that takes a deploy id accepts `?target=<name>` (default: the only/first target).
- `twin.yaml cluster:` is finally consumed: Catalog pre-selects it and warns when deploying elsewhere.

### 3.2 Reads
- `GET /api/range` — per target: reachability, and each standing twin with its sibling tap / tfc /
  exr deploys (via `cascade.siblings_for_te`), host count, persona-instance count, status, started
  time, source twin id. One call paints the home page.
- `DeploySummaryDTO` gains `tier` (display tier, tap shown as tap), `plan_id`, `target`.
- `GET /api/deploys/{id}/hosts` — the inventory roster joined per host with persona instances
  (persona, intensity, schedule, peers, op status), tap bindings, and last op error. This is the one
  model behind the Twin area.
- `GET /api/personas?twin=<id>` — catalog plus that twin's local personas, with `os_family` and a
  short emit summary, for the picker. Twin-local personas are resolved **per request** as an overlay,
  never by mutating the process-global registry (closes GAP-105 for the API).
- `GET /api/deploys/{id}/health` — live, on demand: one `/cluster/resources` sweep for VM power
  state; `?host=<id>` adds QGA reachability and persona `heartbeat` for that host only. Expensive
  reads are per-host and user-initiated, never a whole-twin QGA fan-out.
- Fix the latent "latest deploy of any tier" fallbacks (`service.py:1010`, `:1252`) and bare
  `GET /api/tfc` returning destroyed rows; `?target=<twin name>` resolves.

### 3.3 Per-host persona apply (need d) — the scalpel
Engine: `apply_host_personas(te_deploy, host_id, assignments, role?)` in `o2control/traffic.py`.
- Same lease as a full apply (`tfc-<te>`), so the two serialize.
- Desired set = the standing tfc deploy's completed rows, with **only this host's** entries replaced.
- Resolved through the unchanged `resolve_traffic_bindings` so duplicate / requires / OS checks run.
- `force_reset_ops` on **this host's instance ops only**; the executor skips the rest.
- De-scope restricted to this host's dropped instances. Other hosts' personas are never stopped.
- Parity with the CLI path: containment gate, image-manifest OS check, twin-local personas.
- A role change re-resolves peers; dependents whose peer picks changed are listed in the preview and
  re-driven only if the user opts in ("also update N hosts that talk to this one").
- If no tfc deploy exists yet for the twin, the first host apply creates it.
- After start, the driver `heartbeat` is called once and its result recorded — "started" is not
  "running".
- Host-level edits are marked in op inputs (`origin: host-edit`). A later whole-profile apply that
  would revert one says so and needs confirmation (CLI: a named flag; never silent).
- `GET /api/deploys/{id}/tfc/profile` exports the running state as a traffic-profile YAML, so an
  edit can be saved back to `twins/<id>/tfc/` deliberately.

API: `POST /api/deploys/{id}/hosts/{host}/personas/plan` (pure preview: start / stop / unchanged /
dependents / refusals) and `PUT /api/deploys/{id}/hosts/{host}/personas` (write token; async — returns
at once, progress by polling the tfc deploy's ops, as te deploy does today).
CLI parity: `axrange tfc set-host <target> <host> --persona P [--persona Q] [--role R]`.

### 3.4 Not in this round
SSE/websocket (polling stays), pcap download, exercise run UI, multi-node placement, write-back of
host edits into twin files (export only), Console column wrapping.

## 4. What happens to each existing surface

| Surface | Ruling |
|---|---|
| Console SPA `o2/console/` | **Becomes the one app.** Layout engine, URL model, API client, theme kept. Existing canvas becomes the Design area. |
| Grafana control-panel plugin | **Deprecated.** Its guarded deploy flow, deploy/op tree and "why empty" states are re-implemented in the Console. Left in the repo, marked deprecated, until the user approves deletion. |
| Seven-renderer bake-off + `topology-eval` dashboards | **Closed without a winner**; the Console layout is the renderer. Deprecated, deletion listed for approval. |
| Grafana dashboards: capture health, Suricata alerts, attack coverage | **Kept** as telemetry, linked from the Console. |
| Grafana dashboards: deploy/op status, pcap browser, control console | Deprecated (superseded by Range / stub endpoint). |
| `wip/ui-viewer-spa`, `observability/` (40k-file stale build), `.claude/worktrees/agent-ab92…`, `twin-configs-cast/` shim, `scenarios/` | Listed for the user's deletion approval. **Nothing is deleted in this round.** |
| Static HTML reports | Kept as dated documents. |

## 5. Decisions superseded (each gets a `Supersedes:` CHANGELOG entry)

1. **Single-pane Grafana** (2026-06-25, H1:275) → the Console is the operator surface; Grafana is
   telemetry only. Also reconciles the never-recorded reversal by design/15.
2. **"UI is a viewer, not an authoring tool; never emits IR"** (2026-06-19, H1:1027; design/11) →
   the Console views, controls and edits; design edits are assertion records, run-time edits are
   tfc applies.
3. **"Console starts clean, nothing ported from the plugin; independent of the plugin"**
   (design/15, CL:3991, CLAUDE.md) → plugin behaviours are absorbed; the plugin is deprecated.
4. **D5 "the plugin keeps Deploy as twin"** → deploy moves to the Console's Catalog.
5. **Layered free-hand IR canvas** (ARCHITECTURE §1, OPEN_QUESTIONS Q1/Q11) → recorded as never
   built and not the current direction; the layer model stays an IR concept, not a UI commitment.

**Kept, not reversed:** "the canvas edits the imprint, never the deployed twin" — the Design area
still does only that. Run-time persona changes go through the `tfc` tier, which the 2026-08-04
ruling already made runtime-swappable. API/DTO boundary, auth posture (write token in memory only),
evidence model, deterministic layout, Logical/Physical semantics, no react-flow/d3/dagre in the
Console: all kept.

## 6. Build order

1. **Backend reads** (3.1, 3.2) — unblocks every screen.
2. **Engine + API per-host apply** (3.3), with tests proving other hosts' rows are untouched.
3. **Console shell + Range + Twin (read-only)** — parallel with 1–2 against the contract above.
4. **Console writes**: persona edit, guarded deploy, destroy, tap view.
5. **Design area relabel + glossary**; redirects for old URLs.
6. **Docs**: design/16, Supersedes entries, design/11 + README + CLAUDE.md reconciled, plugin marked
   deprecated, deletion list.
7. **Live verify**: rebuild to a scratch dist, restart `:8080`, check Range/Twin against the standing
   `corp-twin` (read-only); then a fresh small disposable twin on dev45 for the write paths
   (deploy → traffic → change one host's persona → prove the others kept running → destroy).
   The standing `corp-twin-07cfcdb2` and `svc-engines-24131435` are never written to.

## 7. Acceptance — the user's own five questions

From a cold browser tab on the Console URL:
- a. Which twin is on which cluster: visible on the landing page, zero clicks.
- b. What it looks like: one click (the twin).
- c. Personas per host: visible on that same screen (map chips + table column).
- d. Change one host's persona: select host → change → preview → apply; no other host's traffic
  stops, proven by unchanged op rows and unit start times on the other hosts.
- e. Health: recorded status everywhere at a glance; live status one click away, labelled with age.
