*UI surface inventory — taken 2026-09-29 at commit `b3c3745e` (branch `wip/corp-twin`); read-only research, citations are file:line at that commit.*

# axRange UI surface inventory

## Summary

There are three live UI surfaces, one retired SPA on a branch, one stale committed build copy, and a set of static HTML reports.
- **Console SPA** only edits design-time files.
- **Grafana plugin** is the only UI that deploys or destroys, and it reads state.db only.
- **Grafana dashboards** are read-only.

No UI can answer "which twin is on which cluster" or "what persona does node X have", and none can change one node's persona. The API already exposes most of the needed reads (`/api/tfc`, `/api/o2/taps`, `/api/o2/surface`, `/api/deploys/{id}/inventory`), but no UI calls them.

---

## 0. The shared backend: `axrange serve` (FastAPI)

This is not a UI, but every UI surface goes through it.

**Where it lives and how it starts**
- Code: `axrange/api/app.py` (873 LOC), `service.py` (3,888), `dtos.py` (1,512). The whole package is about 11.5k LOC.
- Launch: `axrange serve [--host 127.0.0.1 --port 8080 --spa-dir DIR --twins-dir DIR --imprints-dir DIR]` (`axrange/api/serve.py:17-86`).
- Env vars: `AXRANGE_DB` (default `~/.axrange/state.db`), `AXRANGE_SPA_DIR`, `AXRANGE_TWINS_DIR`, `AXRANGE_IMPRINTS_DIR`, `AXRANGE_API_TOKEN`, `AXRANGE_ADAPTER_CONFIG` (`settings.py:45-145`).
- SESSION_STATE says a standing instance runs on `:8080` in tmux `axrange-serve` (`docs/SESSION_STATE.md:98`).

**Routes**
- Full list: `app.py:115-739`. The static SPA is mounted at `/` with a history fallback (`app.py:824-873`).

**One cluster per server**
- `/api/targets` returns exactly one target, `cfg.default_node` (`service.py:1601-1618`).
- The `deploys` table has no cluster or target column (`axrange/planner/state.py:216-232`). There is only `placement_json`, which maps IR node to PVE node name, and `tier`.
- `DeploySummaryDTO` (`dtos.py:141-148`) carries no cluster or placement at all.
- To see two clusters you need two state.db files and two servers. `deploy-dev45/` holds 10 ad-hoc `state*.db` files, and `scenarios/fabric-verify/state.db` is another.

**Stubs**
- `POST /api/deploys/{id}/run` always returns `not_implemented` (`service.py:2138-2148`).
- `GET .../nodes/{n}?live=true` returns `{"available": false, "note": "live tier (QGA/Proxmox) not implemented"}` (`service.py:1292-1297`).
- `/api/pcaps` is a stub that returns `implemented:false`.

**Default imprint catalog is a fixture**
- With `--imprints-dir` unset, the imprint catalog is the packaged fixture `axrange/api/fixtures/imprints/corp-enterprise` (`imprints.py:224`, `settings.py:109-118`).

**Persona changes are whole-profile only**
- `POST /api/tfc` takes a whole profile (`TrafficApplyRequest`, `dtos.py:619-631`).
- The engine force-resets and re-launches every persona instance on each apply. An unchanged instance is re-rendered byte-identically, so it is a no-op in effect, and dropped or swapped instances are stopped (`axrange/o2control/traffic.py:1520-1545`).
- There is no endpoint or verb that says "set persona P on host H only". The per-node effect can only be had by re-POSTing the whole profile with one line changed.

---

## Surface 1: Console SPA, `o2/console/` ("axRange Console · assertions canvas")

**1. Basics**
- Stack: React 18.3, react-router 7, Vite 8, TypeScript strict, Vitest. There is no state or graph library.
- Size:
  - `src/`: 59 files, about 20k LOC.
  - `test/`: 65 files, about 21k LOC of code plus about 19k LOC of golden JSON.
  - Largest files: `views/ElementActions.tsx` 1988, `views/Level0.tsx` 1721, `App.tsx` 1499.
- Branch `wip/corp-twin` (also on master's lineage). Last commit `d840ccf5` on 2026-09-19 ("iteration 11 — apply-proof follow-ups"). It was built S2 → S7 between 2026-09-08 and 09-19.
- Tooling gotcha: `src/model/graph.ts`, `layout/matrix.ts` and `layout/arcs.ts` are detected as binary. The default ugrep `-I` skips them, so use `grep -a`.

**2. Launch**
- Dev: `cd o2/console && npm ci && npm run dev` serves `127.0.0.1:5173`, proxying `/api` to `AXRANGE_DEV_API` (default `:8080`) (`vite.config.ts:575-592`).
- Prod: `npm run build`, then `axrange serve --spa-dir o2/console/dist --imprints-dir twins`.
- Prerequisites:
  - **No deploy is needed. It never touches state.db or Proxmox.**
  - It needs cast bundles with a `*.topology.json` export. Only `twins/corpapcn-twin` and `twins/pit-395a61f04e-1780405200` have one, plus `twin-configs-cast/pit-…`.
  - The branch's own `corp-twin`, `corp-twin-devlab`, `sme-flat`, `datacenter` and the rest render "no topology export for this bundle".
  - The README quick-start paths (`README.md:252-254`) point at the pre-consolidation `twin-configs-cast/` and are stale.
- `twin-configs-cast/` today is just a legacy-layout shim: symlinks into `twins/pit-…` plus an untracked `layout.json` holding `{"pins":{}}`.

**3. Views**
- Routing: a single catch-all route. The URL grammar is in `model/zoom.ts` (`zoomToPath` :197, `parseZoomPath` :218).
  - `/` is the catalog.
  - `/imprints/<id>` is Level 0.
  - `/imprints/<id>/segments/<cidr>` is Level 1.
  - `…/hosts/<key>` is Level 2.
  - `…/matrix` is the matrix.
  - Filters: `?ghosts=1`, `?class=`, `?occupancy_lt=`, `?changed=1`, `?summary=1`, `?ew=all`, `?order=cidr`, `?expand=`.
- Shell (`App.tsx:317-426`): three panes, Outline | MapPane | Inspector. It also has a breadcrumb, a "drafts on this browser" notice and a leave guard.
- Outline (`views/Outline.tsx`): the imprint list, and a tree of sites and segments with `deployedCount/hostCount` and a role histogram. It has "+ segment" and per-site "+" controls.
- Level 0 (`views/Level0.tsx`), a tiered SVG:
  - Internet → capture-vantage badge → WAN → infra → segment tiles in site columns (6 per site, then "+N more"), with east-west arcs.
  - Pin drag, Reset pins, tile order, 1:1 or fit scale.
  - Tile click drills down; the ✎ control selects; Alt-drag clones.
- Level 1 (`views/Level1.tsx`): segment spine, router leg, role bands of host chips, conduit peers, and hatched pending chips.
- Level 2 (`views/Level2.tsx:66-150`): a host facts table. It shows address, locality, role with evidence and confidence, occupancy, listen set, dial set, edges by peer, and the assertions affecting this host.
- Matrix (`views/Matrix.tsx`): role×role or host×host. It refuses above 40×40.
- Inspector (`views/Inspector.tsx:53-140`) contains:
  - `ElementActions`: add host, clone segment, add conduit, suppress hosts, change role, override image/resources, suppress host, site-add form, clone form. Every gesture requires a note.
  - `EditPanel`: source / as_of / write token, Save, "Apply (recompile)", Reload.
  - `ChangeList`: per-gesture undo, and a tag naming which CLI deploy each record implies.
  - Evidence legend.

**4. Data**
- All reads go to `/api/imprints` and then `/{id}/provenance|topology|cast|assertions|layout|diff` (`src/api/client.ts:212-258`; hooks in `api/hooks.ts`). `getImprint` (`/{id}`) is dead code.
- **Everything is design-time files on disk:**
  - the topology export (observed capture)
  - the twin config (the "cast")
  - the provenance sidecar
  - `<id>.assertions.yaml`
  - `layout.json`
- No mock data runs in the app. Fixtures are used by tests only. The server's default catalog is itself a fixture, though.

**5. Writes**
- `PUT /api/imprints/{id}/assertions` (etag If-Match) on Save.
- `PUT /api/imprints/{id}/layout` on pin drag, clone drop, and Reset pins. Reset writes immediately with no confirmation (`Level0.tsx:827-835`).
- `POST /api/imprints/{id}/apply`: save, then a synchronous aximprint recompile, then a diff and a 5 s highlight (`App.tsx:1083-1188`).
- Staged gestures are held in `localStorage` under `axrange.console.draft.<imprint>` (`edit/draft.ts:57`).
- **There is no deploy, destroy, tfc or tap action.** README `:9-10` says: "The canvas edits the imprint, never the deployed twin. The twin follows by `axrange te deploy --update`."

**6. Intent vs reality, and UX problems**
- The intent is an operator-assertion editor over an aximprint cast (`docs/design/14`, `15`). It delivers that, but only for aximprint-cast bundles, which today is two of ~13 twins.
- **"live" is mislabelled.**
  - The segment row reads `"{deployedCount} live · {ghostCount} ghost"` (`Level0.tsx:1637`, `ElementActions.tsx:507`, `Outline.tsx:246`).
  - `deployedCount` actually means "hosts at or above the gate that a deploy *would* stand up" (`model/graph.ts:255-256`). Nothing in it is live.
  - Likewise `deployedCidr` is the design config's CIDR.
- **Apply is refused for every consolidated twin.**
  - The server returns 422 for any `twins/<id>/` folder (`service.py:3497-3510`, "until the installer learns the twin layout").
  - It returns 503 unless `AXRANGE_AXIMPRINT_BIN` and `AXRANGE_AXIMPRINT_STORE` are set.
  - The button is still enabled (`EditPanel.tsx:190-198`), so the user only learns this after clicking.
- **Save is disabled by default.**
  - `source` is seeded as `"operator: "`, which fails the `^operator:\s*\S` check, so Save stays off until a name is typed (`App.tsx:662-667`, `records.ts:292`).
- **The write token is hard to find.**
  - It sits only under Save and is not persisted.
  - Pin drags need it too, so a drag before the token is entered fails (inferred).
- **Jargon** throughout: imprint, cast, conduit, assertion, sidecar, provenance, ghost, occupancy, "plane stamp", the evidence classes, N-S/E-W, gate, and a raw etag shown in the Inspector.
- **Hidden state:** localStorage drafts with reconciliation-on-restore, the `?changed=` set held in memory only, and a selection that is not in the URL.
- **Stale text:** `Level2.tsx:141-143` says "none — S5 authors these".
- **Mode overload:** click drills, ✎ selects, drag pins, Alt-drag clones.
- **Round trip to the real twin is four steps and leaves the UI:** stage → save → apply → run `axrange te deploy --update` on the CLI.

**7. Overlap**
- The Grafana plugin's Twin → Logical/Physical tabs render the same topology export via `/api/scenarios/{id}/topology-export`.
- Imprint browsing moved here from the plugin in S7.

---

## Surface 2: Grafana panel plugin, `o2/grafana/plugins/axellio-axrange-control-panel/`

**1. Basics**
- Stack: React 18, Grafana 11.4 SDK (`@grafana/ui`), `@xyflow/react` 12, d3-force, dagre, webpack/swc, Jest.
- Size: 35 source files, 6,781 LOC, plus 5 test files, 1,031 LOC.
- Branch `wip/corp-twin`. Last commit `6afda9d3` on 2026-09-16 (S7).
- History: moved into `o2/` on 2026-07-08; Track C Imprints screen added 08-27; the 4-tab Twin screen added 09-02.

**2. Launch**
- Build: `o2/grafana/build-plugin.sh` (`npm ci && npm run build && cp dist/* .`).
- Run: `o2/compose/docker-compose.yml`, Grafana 11.4 `axr-grafana` on `:${GRAFANA_PORT:-3000}`, login admin/`axrange-change-me`. It is unsigned and allow-listed (compose:89).
- Open the dashboard "Control console" (uid `axr-control-console`), plus 7 generated "topology-eval" bake-off dashboards (`axr-topo-{tree,sugiyama,cluster,interactive,nodegraph,honeycomb,canvas}`).
- Prerequisites:
  - `axrange serve` must be reachable from the container at `AXRANGE_API_URL` (default `host.docker.internal:8080`), via the Infinity datasource `axr-api`.
  - Writes need `AXRANGE_API_TOKEN` in the datasource. Otherwise they return 401.
  - The Console view needs the `consoleBaseUrl` option, which is empty by default.
- Build state: the `dist/` built on Sep 16 is **not staged into the plugin root** (no root `module.js`). The S7 commit says the Console embed is "unverified: it needs a live Grafana".

**3. Views**
The top switcher has four views, **Topology | Twin | Console | Deploy as twin** (`AxrangeControlPanel.tsx:363-373`).
- **Topology** (native):
  - Canvas: one of seven renderers, chosen by panel option (`topology/registry.ts:39-65`). Click selects; nodes are coloured by op status.
  - `ControlToolbar`: a source picker mixing scenarios, twin-configs and imprints; a target picker; and Validate / Plan / Deploy / Run / Destroy.
  - `NodeInspector`: image, placement (the PVE node), vmid, resources, networks, and declared-vs-applied apps and overlays. It has a "Refresh live" button.
  - `DeployTree`: the deploy list with per-row trash, and the op list with last error.
- **Twin** (`twin/TwinTopologyScreen.tsx`) has four sub-tabs and its own native `<select>` picker:
  - **Logical**: segments and the conversation graph (`TopologyExportView.tsx`).
  - **Physical**: the observed census.
  - **Structure**: a declared SVG of WAN → routers → segment columns → host chips (`DeployedTopology.tsx`).
  - **Deployed**: the same SVG plus op-status dots, and role and IP from the inventory.
- **Console**: an iframe of the Console SPA (`console/ConsoleEmbed.tsx`).
- **Deploy as twin** (`deploy/DeployTwinAction.tsx`): pick imprint and target → preview plan → a "This creates real VMs…" confirm → deploy → "Watch it on the Topology view".

**4. Data**
- All requests go through the Grafana datasource proxy (`io/api.ts:479-509`).
- Reads: `/api/scenarios`, `/twin-configs`, `/imprints`, `/scenarios/{id}/topology`, `/nodes/{n}`, `/topology-export`, `/deploys`, `/deploys/{id}`, `/deploys/{id}/inventory`, `/targets`, `/imprints/{id}/cast`.
- "Live" data here means **state.db op records only**:
  - Inventory is a "pure state-store derivation … no cluster call" (`axrange/planner/inventory.py:323-329`).
  - `vm_status` is the clone-op status, not the VM's power state.
- **No Proxmox or QGA reads anywhere.** No mock data at runtime.

**5. Writes**

| Action | Endpoint | Confirmation |
|---|---|---|
| Validate | `POST /api/validate` | none |
| Plan | `POST /api/plan` | none |
| **Deploy** | `POST /api/deploys` | **none: one click creates VMs** (`ControlToolbar.tsx:456`) |
| Run | `POST /deploys/{id}/run` | none; always `not_implemented` |
| **Destroy** | `DELETE /api/deploys/{id}?allow_destroy=true` | `window.confirm`; cascades to tap and tfc server-side (`app.py:474-496`) |
| Imprint plan preview | `POST /api/imprints/{id}/plan` | none (pure) |
| Imprint deploy | `POST /api/imprints/{id}/deploy` | preview, then confirm |

It never calls tap, tfc, exr, o2 surface, scorecard or access.

**6. UX problems**
- **Dead controls:** Run, Refresh live, and clicking a node in Twin → Structure or Deployed (`selectedId={null}`, `TwinTopologyScreen.tsx:167,464`).
- **Deploy is unguarded in the toolbar but guarded in "Deploy as twin".** On top of that, the first source is auto-selected, so Deploy is armed on load (`AxrangeControlPanel.tsx:177-192`).
- **Validate and Plan are greyed out** for twin-configs and imprints, which are most content today (`ControlToolbar.tsx:249-258`).
- **Selected deploys are not polled.** Only deploys launched from the toolbar are polled.
- **The same topology is drawn twice** (Topology view vs Twin → Structure/Deployed), with two different pickers. Imprints appear in three pickers.
- **Seven renderers and seven bake-off dashboards are all enabled.** The renderer choice was never settled.
- **Twin → Deployed matches the first live deploy with the same `scenario_id`** (`:261`), which is ambiguous when two exist.
- **NodeInspector omits `source`,** and falls back to the latest deploy of *any* scenario for placement (`service.py:1251-1254`). It may show the wrong data (inferred).
- Jargon: imprint, twin-config, barriers, halted-due-to-upstream, evidence_class, census, "datasource axr-api".
- Fixed 380 px rail with hard-coded hex colours.

**7. Overlap**
- It is the only UI for deploy lifecycle and op status.
- Its Twin → Logical/Physical tabs duplicate part of the Console.
- The Console view is just an iframe of Surface 1.

---

## Surface 3: Grafana dashboards, `o2/grafana/dashboards/*.json`

All are read-only. Provisioned from folder "axRange" with `allowUiUpdates: true`, so edits made in the UI are not written back to git.

| Dashboard (uid) | Datasource | Shows | Live? |
|---|---|---|---|
| Attack coverage (`axr-attack-coverage`) | Loki | technique attempts vs Suricata alerts, technique×host matrix | yes (logs) |
| Capture health (`axr-capture-health`) | Prometheus | `capture_up`, newest pcap age, disk %, promisc, rx_drops | yes, the only live sensor health anywhere |
| Suricata alerts (`axr-suricata-alerts`) | Loki | alerts over time, by severity, top signatures, top src→dst pairs, eve.json | yes |
| Deploy / op status (`axr-deploy-op-status`) | Infinity→API | deploy counts, table, latest-deploy ops, failed ops | state.db |
| Pcap browser (`axr-pcap-browser`) | Infinity→`/api/pcaps` | always empty (stub) | dead |
| Control console + 7× topology-eval | the plugin | Surface 2 | — |

The SQLite datasource `axr-statedb` is provisioned but unused. There are no Caldera or Wazuh datasources.

---

## Surface 4 (retired, other branch): viewer SPA `ui/` on `wip/ui-viewer-spa`

**Basics**
- Branch `wip/ui-viewer-spa`, 2 commits ahead of its 2026-06-25 base (`7d92b550`, `2322004a`). Also on `origin/wip/ui-viewer-spa`. It was never merged; there is no `ui/` on HEAD.
- Also tagged as `retired/ui-viewer-spa`.
- Stack: React 19, Vite 6, @xyflow/react, Zustand, Tailwind 4. About 1.9k LOC in 24 UI files (`git diff --stat HEAD...wip/ui-viewer-spa`).
- Launch: dev `:8081` proxying to `:8080`; build `--spa-dir ui/dist` (`ui/README.md`).

**Views** (`ui/src/app/App.tsx`)
- A toolbar with scenario and target pickers and Validate → Plan → Deploy → Run → Destroy.
- A results strip.
- The topology canvas with op-status overlay.
- A right rail with "inspector" and "deploys" tabs. The deploys tab polls every 2.5 s.

**Endpoints**
- `/api/health`, `scenarios`, `topology`, `nodes`, `validate`, `plan`, `deploys` (CRUD), `run`, `targets`, `pcaps` (`ui/src/io/api.ts:264-311`).

**Status and overlap**
- Retired by user decision; see `docs/CHANGELOG.md` 2026-07-07 (around :9620-9634): "Grafana is the project's front-end UI… The untracked `ui/` … is to be removed".
- Its layout was ported as the plugin's `canvas` renderer.
- Functionally it is a strict subset of the plugin's Topology view.

## Surface 5 (stale artefact): `observability/grafana/plugins/axellio-axrange-control-panel/`

- This is a pre-rename built copy of the plugin, with `module.js` dated 2026-07-08, **plus its entire `node_modules`**.
- About **40,559 tracked files**, committed in `d5f037ea` on 2026-08-27 (the "feat(personas): AXRANGE_PERSONAS_DIR" commit, which changed 40,579 files). This looks accidental (inferred).
- Nothing references it: grepping `o2/`, `axrange/` and `scripts/` for `observability/grafana` returns no hits.
- Retire it.

## Surface 6 (non-interactive): static HTML reports

These are dated, point-in-time snapshots, not live UIs.
- `docs/reports/*.html`: capture-rate reports, and two corp-enterprise topology diagrams, deployed vs config. The topology ones are hand-SVG generated by `*.gen.py` with **hard-coded data** (for example `2026-08-14-corp-enterprise-topology.gen.py:9-30`).
- `reports/chasing-the-golden-twin.html` and `reports/axquery-discovery-loop.html`.
- `docs/design/aximprint-integration/*.dc.html`: design canvases, including a Console mock and a 2.5 MB integration page.
- `docs/archive/twin-runs/…/capture-rate.html`.

They are worth keeping as docs only. The deployed-vs-config topology pair is the closest thing anywhere to "what the deployed twin looks like", and it is frozen.

## Not UI surfaces (checked)

- **No TUI.** Nothing in `axrange/`, `scripts/` or `scenario_ai_research/` imports rich, textual or curses. The CLI (`axrange/cli/main.py`, 4,638 LOC) is argparse with plain-text output.
- `scenarios/` (untracked) is just a `fabric-verify/state.db`.
- `demo/` holds 4 YAML files.
- `.claude/worktrees/agent-ab92…` is a git worktree on branch `worktree-agent-ab92e05d08ed71105` (f068f478, 2026-09-26), already merged into HEAD. It is a stale duplicate of `o2/console` and the plugin, with nothing unique.
- No other branch has unmerged UI files. Every local and remote branch was checked for unmerged `.tsx`/`.html`/`ui`/`console`/`grafana`/`dashboard` paths; only `wip/ui-viewer-spa` has any (24).

---

## The five user questions

**(a) Which twin is cast/deployed on which cluster?**
- **Grafana plugin: partial, 3+ clicks.**
  - The deploy list shows scenario_id, status and age, but no cluster (`DeployTree.tsx:303-318`; `DeploySummaryDTO`, `dtos.py:141-148`).
  - The PVE node name appears only after selecting a deploy, clicking a node, and reading `placement` in the NodeInspector (`NodeInspector.tsx:239`).
  - One panel sees exactly one API, meaning one state.db and one cluster (`service.py:1601-1618`).
- **Deploy/op-status dashboard:** lists deploys, same gap.
- **Console:** no; it has no deploy concept at all.
- **CLI:** `axrange list-deploys` / `te status`, run per state.db and adapter.conf. The cluster is implicit in which `--db` and `--adapter-config` you run with.
- **Root cause:** the schema stores no cluster identity (`state.py:216-232`). Placement node names like `dev45` and `pxtwin1` are PVE node names, which serve as cluster ids only because both are single-node (inferred).

**(b) What does the deployed twin's topology look like, live?**
- **Grafana plugin: partial.** Topology view with a deploy selected (about 2 clicks), or Twin → Deployed (about 3 clicks).
- Both draw the **design-time IR** overlaid with **state.db op status** and inventory role and IP. There is no hypervisor or guest reality: `live=true` is not implemented (`service.py:1292-1297`).
- Twin → Deployed picks the first matching deploy (`TwinTopologyScreen.tsx:261`).
- **Console: no.** Its "live" and "deployed" labels are design-time (`graph.ts:255-256`, `Level0.tsx:1637`).

**(c) Which personas are assigned to which nodes?**
- **No UI answers this.**
- The plugin shows only the inventory's effective `role` string on Twin → Deployed chips, where a tfc assignment wins (`inventory.py:335-345`, `DeployedTopology.tsx:355`). That is a role, not a persona list.
- The Console uses the persona catalog only to prefill listen ports (`model/roles.ts:210-256`). `TopologyExportNode` has no persona field (`types.ts:162-175`).
- **The API does answer it:** `GET /api/tfc?target=<twin>` returns `TrafficPlanStatusDTO.assignments[]` with persona and hosts (`dtos.py:634-639`, `app.py:641-646`). The CLI equivalent is `axrange tfc status|info`. No UI calls either.

**(d) Change one node's persona and apply only that change**
- **No surface can, and neither can the backend as a targeted operation.**
- The only path is `POST /api/tfc` (or `axrange tfc deploy`) with the whole edited profile.
- The engine re-launches every instance (byte-identical ones as effective no-ops) and stops the dropped or swapped instance (`traffic.py:1520-1545`). So the effect is per-node, but the gesture is whole-profile, and the cost scales with host count.
- The Console's "Change role" edits an aximprint role assertion, not a persona binding. It would need Save → Apply (refused for `twins/`) → CLI redeploy.

**(e) Live health of nodes, taps and sensors**
- **Nodes:** only op status from state.db (plugin and Deploy/op-status dashboard). There is no power state, QGA, or guest-service health.
- **Taps:** no UI. `GET /api/o2/taps` exists (`app.py:570-598`) but is uncalled.
- **Sensors:**
  - The Capture-health dashboard (Prometheus: `capture_up`, pcap age, drops, disk) is the only live health surface, and it covers the tcpdump sensor exporters only.
  - `GET /api/o2/surface` (`app.py:521-525`) is uncalled.
  - SESSION_STATE warns that `o2 status: present` does not mean capturing (GAP-067, LES-247).
- **Scorecard:** `GET /api/deploys/{id}/scorecard` exists but is uncalled.

---

## Capability matrix

| Capability | Console SPA | Grafana plugin | Grafana dashboards | ui-viewer-spa (retired) | CLI |
|---|---|---|---|---|---|
| Browse twin/scenario catalog | partial: imprints only; needs a topology export (2 of ~13 twins) | yes: scenarios + twin-configs + imprints in one picker | no | scenarios only | yes (`twin-config list`) |
| Design-time topology drawing | yes: rich L0/L1/L2/matrix, golden-tested layout | yes: 7 renderers + Structure SVG | no | yes (basic) | text only |
| Observed-capture view (evidence, census, edges) | yes | partial: Logical/Physical tables | no | no | no |
| Edit design (assertions, pins) | yes, but Apply is refused for `twins/` (422) | no | no | no | aximprint CLI |
| Validate / plan | no | partial: scenario-sourced only; imprint plan via Deploy as twin | no | yes | yes |
| Deploy | no | yes: unguarded toolbar + guarded imprint flow | no | yes | yes |
| Destroy | no | yes (confirm; cascades tap/tfc) | no | yes | yes |
| Which twin on which cluster | no | partial: PVE node per node, via inspector | partial: deploy list, no cluster | no | partial: per db/conf |
| Live deployed topology | no ("live" is mislabelled) | partial: IR + state.db op status | no | partial: same | `te status` / `te inventory` (state.db) |
| Personas per node | no | minimal: effective role on chips | no | no | yes (`tfc status/info`) |
| Change one node's persona and apply | no | no | no | no | whole-profile re-apply only |
| Tap status | no | no | no | no | yes (`tap status/info`) |
| Sensor / capture health | no | no | yes: Prometheus capture health | no | `o2 status` (unreliable, LES-247) |
| Node live health (power / QGA) | no | no ("Refresh live" is a stub) | no | no (same stub) | no |
| Alerts / attack coverage | no | no | yes (Loki) | no | no |
| Exercise (exr) run | no | no ("Run" is a stub) | no | no ("Run" is a stub) | yes (`exr run`) |
| Access facts / credentials | no | no | no | no | yes (`te info`) |
| Fidelity scorecard | no | no | no | no | yes (`te scorecard`) |

The capabilities marked "no" for every UI (tap, tfc and surface status, access, scorecard, exr) all already exist as API endpoints. They are unwired, not unbuilt.

---

## Keep vs retire

**Worth keeping (reusable assets)**

- **Console `src/layout/*`**: `level0`, `level1`, `matrix`, `trunk`, `infra`, `arcs`, `columns`, `sites`, `tiers`, `pins`, `scale`, `plane`, `viewport`, `geometry`.
  - Pure, React-free, golden-tested and deterministic. It is the best layout engine in the repo.
  - It currently takes the topology-export plus cast graph. It would need an adapter to take a deploy's inventory or IR graph (inferred).
- **Console `src/model/zoom.ts`**: the URL-is-the-state grammar, with round-trip tests.
- **Console `src/model/`**: `keys.ts`, `search.ts`, `evidence.ts` and `graph.ts` (the join model). Keep `graph.ts` but rename its "deployed"/"live" semantics.
- **Console `src/api/client.ts` + `hooks.ts` + `context.ts`**:
  - Typed errors by status, etag/If-Match, runtime token, and injectable client.
  - Generic `useResource` with staleness guards.
  - Easy to extend to `/api/deploys`, `/api/tfc`, `/api/o2/*`.
- **Console `src/edit/*`**: the store/records/draft reducer. Keep only if assertion editing stays in the product.
- **Console `usePinDrag.ts`, `mapParts.tsx`, `theme/tokens.css`**: the light/dark palette.
- **Plugin `io/api.ts` deploy-side DTOs**: deploys, ops, inventory, targets, plan and validate types. Merge them into the Console client instead of keeping two hand-mirrored DTO sets.
- **Plugin `io/errors.ts` and `io/targets.ts`**: the 401 message and the `(unset)` guard.
- **Plugin `deploy/DeployTwinAction.tsx`**: the guarded preview → confirm → deploy pattern with stale-preview discard and double-click guard (502-LOC test). Make it the only deploy flow.
- **Plugin `DeployTree.tsx`**: the op list with last error, and per-row destroy.
- **Plugin `twinViewState.ts`**: pure "why this is empty" states, tested.
- **Plugin `DeployedTopology.tsx` + `topology/layouts/layered.ts`**: the deterministic WAN → router → segment → host SVG for a *deployed* twin, and `canvas/theme.ts` for status colours.
- **Grafana dashboards**: Capture health, Suricata alerts and Attack coverage are genuinely live telemetry. Keep them as a telemetry pane, linked or embedded, rather than re-implemented.
- **API**: keep it as-is, as the single backend. Add a cluster/target column to `deploys` and to `DeploySummaryDTO`, and add a per-host tfc assignment endpoint (both inferred needs).

**Retire**

- `observability/` (the 40k-file stale plugin build plus node_modules committed in `d5f037ea`).
- `wip/ui-viewer-spa` / `ui/`. Already retired; the tag preserves it.
- The six non-default topology renderers and the 7 `topology-eval` dashboards plus `gen-topology-dashboards.py` / `topology-eval.manifest.json`. Keep one layout.
- Plugin view duplication:
  - The Topology view and Twin → Structure/Deployed are the same thing twice. Keep one.
  - The ConsoleEmbed iframe becomes unnecessary once there is one app.
  - Dead Run, Refresh live and `getPcaps`/`health` wiring.
- The Pcap-browser dashboard (a stub endpoint) and the unused `axr-statedb` datasource.
- `.claude/worktrees/agent-ab92e05d08ed71105` (merged, stale).
- `twin-configs-cast/` shim once Console Apply learns the `twins/` layout. SESSION_STATE LES-338 also warns against pointing a browser at it.
- Stale README quick-start in `o2/console/README.md:244-260` and the "S5 authors these" text in `Level2.tsx:141-143`.

**Structural recommendation (inferred from the evidence)**

Today the only real deploy/lifecycle UI lives inside Grafana (a plugin build, an iframe-configured option, and a token wired through the datasource). Meanwhile the strongest front-end engineering (layout, URL model, client) lives in the standalone Console, which is design-time only.

The lowest-friction consolidation is one standalone SPA on `axrange serve --spa-dir`, built on the Console's layout, model and client. It would:
- absorb the plugin's deploy tree, guarded deploy and inventory drawing;
- wire the unused `/api/tfc`, `/api/o2/taps`, `/api/o2/surface`, `/api/deploys/{id}/inventory|access|scorecard` endpoints;
- link out to the three live Grafana telemetry dashboards.

The two engine gaps no UI can paper over are cluster identity per deploy, and a targeted per-host persona apply.
