*UI history and decisions — research snapshot taken 2026-09-29 at commit `b3c3745e` (branch `wip/corp-twin`); read-only research, not a design-of-record.*

# axRange UI history: research report

Paths are relative to `/home/rocky/axRange`. "H1" means `docs/changelog/2026-H1.md`, the archive of the June changelog. "CL" means `docs/CHANGELOG.md`. Line numbers come from the working tree on `wip/corp-twin` @ `b3c3745e`. Where I infer rather than read, the text says **[inference]**.

---

## 1. Timeline of every UI effort

| # | Effort | Dates | Why it was started | Decision record | What it delivered | Status now |
|---|---|---|---|---|---|---|
| 0 | **Free-hand layered design canvas** (the "canvas" in CLAUDE.md and ARCHITECTURE) | 2026-05-21 ratified conceptually; 2026-05-28 shelved | Entry point of the platform: operator drops nodes and wires them, and the canvas emits Scenario IR | H1:3807 "Three primary layers, with optional overlays"; H1:3784 "Canvas-to-deploy pipeline"; H1:3224 "v1 scope: canvas (Q1) … declared out of v1"; gate retired H1:1898 (2026-06-11) | Nothing. No layered editor was ever built. | **Never built.** It is still described as live design in `docs/ARCHITECTURE.md` §1 (l.62–94), `docs/design/01-overview.md` l.8, 27 and 60, and `OPEN_QUESTIONS.md` Q1 (l.9–29, stale). |
| 1 | **Viewer SPA**, a port of the `axDigitalTwin` shell (`ui/`, branch `wip/ui-viewer-spa`) | Designed 2026-06-19; built 2026-06-25 (`7d92b550`); retired the same day | The operator "get started" loop: author in Claude → view topology → validate → pick target → deploy → run → see alerts and pcaps. Reuse aDT's mature React/@xyflow shell. | H1:1027 "design: UI control surface … (convergence B)" (Authority: user); the deleted `DESIGN_BRIEF_ui.md` §1–11 (recover with `git show 252e7ae8^:docs/DESIGN_BRIEF_ui.md`) | Topology, deploy monitor and control shell | **Retired 2026-06-25.** Kept only as tag `retired/ui-viewer-spa`. Its layout came back 2026-07-08 as "renderer #7". |
| 2 | **Grafana telemetry dashboards** ("Phase 1") | 2026-06-21 authored; 2026-06-23 D2 fold-in (dashboards read the API); stack stood up 2026-07-16 | The cheapest, highest-ROI half: Suricata→Loki, capture health→Prometheus, deploy/op table | H1:966, H1:796, H1:845; `o2/README.md` "Decisions (Phase 1)" | `o2/grafana/dashboards/`: suricata-alerts, capture-health, deploy-op-status, pcap-browser (the pcap API is a stub and download is deferred), attack-coverage (O2 A4, 2026-07-12), control-console | **Active but degraded.** The standing Suricata sensor has been silently dead since 2026-07-27 (GAP-067, `SESSION_STATE.md` "Gotchas"), so the marquee dashboard has had no feed. `o2/README.md` still says "NOT stood up" (stale). |
| 3 | **Grafana control-panel plugin** `axellio-axrange-control-panel` (single-pane Grafana) | Decided and built 2026-06-25 (`896d63d6`); live-verified in a browser the same day | Replace the two-app hybrid with one surface, copying `pxAnalytics/grafana`, which showed a Grafana panel can drive writes through the datasource proxy | H1:275 "design(ui): single-pane Grafana supersedes the SPA+Grafana hybrid" (Authority: user; **Supersedes** option B and brief §11); H1:240/212/185/163 build and fix entries | Hierarchical topology tree with op-status overlay, node inspector, Validate/Plan/Deploy/Run/Destroy through the datasource proxy | **Active, frozen at the `te` tier.** `design/11-api-ui.md` l.165–174: "the console lags the tier model … reaching them today means the CLI". The "Run scenario" button calls a stub that always answers "not implemented". |
| 3a | **Topology renderer bake-off** inside the plugin | 2026-06-26 (six renderers); 2026-07-07/08 (seventh, `canvas`, the old SPA layout) | Choose a layout by evaluating side by side | Commits `1a65b55e` and `5b1da59e`; CL:9618 and CL:9385; brief §12.5.1 | tree, sugiyama, cluster, interactive, nodegraph, honeycomb, canvas; generated dashboards in `o2/grafana/dashboards/topology-eval/` | **Never concluded.** No winner is recorded anywhere; the panel option still defaults to `tree`. `ARCHITECTURE.md` l.634 still calls "the topology-renderer bake-off" the canvas UI. |
| 4 | **Track C "Imprints Console"** screen inside the plugin (aximprint integration) | 2026-08-25 | Turn a learned aximprint imprint into a deployed twin: browse the belief store, see confidence and provenance, "deploy as twin" | `docs/WORK_ORDER_aximprint_console.md` (Authority: *proposal (Claude Code)* → implementation; "acted on, not separately ratified as a whole"); design canvas in `docs/design/aximprint-integration/` (Console.dc.html artboard); CL:4765 | Stat tiles, confidence-shaded learned topology, provenance table, Deploy-as-twin. **Built against a hand-written fixture belief store** (WO §3). | **Deleted 2026-09-16** (decision D5). On real cast bundles it "renders unmeasured values as measured zeros" (GAP-083). Its §4 live-verify never ran. Only the "Deploy as twin" action survives. |
| 5 | **Twin Topology screen** in the plugin (Deployed / Logical / Physical, then + Structure) | 2026-08-27 (`72992826`, `fa0520d4`) | User report: "The topology view doesn't show anything in it. I was expecting it to show the logical, physical & deployed views for a given twin — be it a discovered and yet to cast IR or te config or deployed te." Reference: `~/apcn/topology`. | `docs/WORK_ORDER_console_selector_and_stale_deploys.md` plus its "SCOPE EXPANSION" (l.~76–144, Authority: user); CL:4747, CL:4649, CL:4519 | Deployed tab: SVG of WAN → router → segment columns → host chips, with a live status dot. Logical/Physical: **tables** over the aximprint topology export (only `corpapcn-twin` had one). Structure: the twin config plus inventory. | **Active, half-built.** Logical/Physical are tabular only, and non-empty only for bundles with an export. CL:4747 says it "coexists with the old bake-off 'Topology' tab for now (consolidation TBD)"; that consolidation never happened. |
| 6 | **Static HTML topology reports** | 2026-08-14 | One-off hand-drawn SVGs of `corp-enterprise`, as-deployed vs. blueprint | `docs/reports/README.md` | Two dated HTML reports (a "config-vs-deployed pair") | Snapshot artifacts, not a UI. They are the only "what is deployed where, including the O2 and capture plane" picture ever produced **[inference: none of the live UIs draws O2/tap placement]**. |
| 7 | **Console canvas SPA** (`o2/console/`) | Ratified 2026-09-04; S1–S7 built 2026-09-05→09-16; iteration 11 on 2026-09-19 | Golden-twin fidelity: an operator must add what the tap could not see and correct what it inferred, "by looking at the network rather than by writing YAML" (`design/14` §1). Plus "several single-diagram UI attempts had failed to scale" (`design/15` l.35). | `docs/design/14-operator-assertions.md` §4, §7; `docs/design/15-console-canvas.md` (ruled 2026-09-04, Authority: user); CL:3991 "the Console SPA starts clean"; `docs/WORK_ORDER_console_canvas.md` (+ `docs/archive/work_orders/WORK_ORDER_console_canvas.built.md`); `docs/WORK_ORDER_console_level0_rendering.md` | Three semantic-zoom levels plus a matrix, deterministic layout, pins, seven edit gestures emitting `axr-assertions/1` records, save / apply (recompile via aximprint) / diff-highlight, clone-and-drag, generated hosts, browser drafts. Reads **only** `/api/imprints/*` (verified in `o2/console/src/api/`). | **Active; "every slice built".** It is the current "canvas" the user calls confusing. Open follow-ups: column wrapping (the map doesn't fit 1600 px: 2030×834), GAP-096, GAP-097, GAP-098. The whole Grafana embed is unverified against a live Grafana (WO §21.5). No UI work has happened since 2026-09-19. |
| 7a | **Grafana embed of the Console (S7)** | 2026-09-16 | D5: "one surface per job" | WO `console_canvas` §8.1 D5 (Authority: user, 2026-09-11); CL:760 | The plugin's "Console" view is an **iframe** of the SPA at its own origin (forced because the API has no CORS). "Deploy as twin" is kept as a pick→preview→confirm view. | Built, **never live-verified** (§21.5). |

**What an operator sees today.** The plugin shows four views: Topology (bake-off), Twin (four tabs), Console (iframe), and Deploy as twin (`AxrangeControlPanel.tsx` l.51, 368–371). Next to it sit about six provisioned dashboards plus seven bake-off dashboards, the standalone Console SPA on `:8080`, the CLI, and static HTML reports.

**Source-kind coverage differs per surface:**
- The plugin Topology tab covers scenarios and twin-configs.
- The Twin tab covers scenarios, twin-configs and imprints.
- The Console covers **imprints with a topology export only**. In `twins/`, only 3 of 12 folders have one (`corpapcn-golden`, `corpapcn-twin`, `pit-395a61f04e-…`). For `corp-twin`, the current flagship, the Console answers "no topology export for this bundle." (`o2/console/src/views/MapPane.tsx` l.196–214). That result is verified from the folder listing plus the code.

---

## 2. Original requirements per UI, and whether they were met

**Viewer SPA (brief §1, §4, §7; H1:1027)**
- 8-step loop (author outside the UI, view, validate, target, deploy, run, alerts, pcaps + download): partly met, then retired within hours.
- "Canvas = viewer-only, no IR emit-back": met.
- WS log tail: never built anywhere.
- Hover card with desired + observed state via QGA and `?live=true`: the live tier is still **not implemented** (`design/11` l.57–58).

**Single-pane Grafana plugin (H1:275; brief §12.3–12.8)**
- The whole 9-step loop inside Grafana: **not met.**
  - Run is a stub (`design/11` l.170).
  - pcap download is deferred; `/api/pcaps` is a stub.
  - Alert telemetry works in principle, but the standing sensor is dead (GAP-067).
- Topology as a deterministic hierarchical tree, "not Grafana Node Graph": met. It was then diluted into a seven-way bake-off with no conclusion, and one of the seven (`nodegraph`) is the rejected Node Graph style.
- Live op overlay, inspector, Validate/Plan/Deploy/Destroy via the proxy: met (browser-verified 2026-06-25).
  - Destroy was broken until GAP-005 (DELETE body dropped by the proxy).
  - Destroy was also unreachable once the selection was lost, until GAP-006 (both resolved 2026-06-29).
- Brief §12.6 trade-off, stated at the time: "same interactive React relocated, plus plugin scaffolding, not less of it." It also said the decision "is reversible … a future standalone SPA could be re-introduced … if Grafana chafes."
- Later tiers (tap, tfc, exr, twin-config catalog, inventory, access facts): **not met** (`design/11` l.165–170).

**Track C Imprints screen (`WORK_ORDER_aximprint_console.md` §1–4)**
- Browse the belief store, per-field confidence and provenance, drafted `twin_config`/`tfc`/`tap` validating, deploy as twin.
- Only `twin_config` drafts were ever supported (§2 status); `tfc`/`tap` drafts were never built.
- Done-when (a real imprint rendering plus a live deploy on dev45): **never run.**
- It was built on a fixture. On real data it showed fabricated zeros (GAP-083) and was deleted.

**Twin Topology (3x3 expansion; `WORK_ORDER_console_selector_and_stale_deploys.md` "Done when" 1–6)**
- The user wanted logical, physical and deployed views for any of the three source kinds, modelled on apcn's tiered diagram with arcs, zone drill-down and addressable routes.
- Delivered: the selector fix and the `?status=` filter.
  - Logical/Physical ended up as **tables** ("a deliberate scope call", CL:4519 item 2).
  - Physical means the aximprint census, not vmid/node placement.
- Constraints "say why a view is empty" and "never invent physical facts": met.
- The WO's "architectural lesson": *"ONE json model and ONE renderer … or the panel grows three code paths that drift"* (commit `12dd9536`). It was **not honoured afterwards**: the Console added a third model and renderer, and the plugin's bake-off and twin tabs remain.

**Console canvas (`design/15` §1–§3, §11; `design/14` §4)**

Met:
- Bounded element counts per level; layout as a pure function of identity (golden-tested).
- Edits as assertion records, save/apply/refresh, pending/saved/applied states, drafts.
- Clean start with no port from the plugin.

Partly met or not met:
- The level-0 "tiered path diagram" was **ruled but not initially built**. The live review of 2026-09-11 found F1–F9 (no path, no WAN box, diagonal lines crossing tiles, uniform tiles, ~60% scale with illegible 11 px type, no tier rails, no tap; archive `WORK_ORDER_console_level0_rendering.built.md` §2). Fixed by R1–R7.
- The map still doesn't fit a 1600 px window (`design/15` l.603–604).
- "Search is the primary navigation": built.
- Grafana "must stay a small task": the iframe is built, but the live checks are open (§21.5).
- Seed a twin from a design document without a capture: **explicitly not possible in v1** (`design/14` §8: "An imprint with no capture cannot be materialized from assertions alone"). This contradicts the optimistic `docs/reference/twin-from-topology-diagram.md` §1, which calls the Console "exactly the tool" for diagram→twin; its own limit 3 concedes that aximprint needs a PIT.
- The S5 editing model as built was "confusing in use" (user, 2026-09-11; CL:2309). It was reworked as S5.1.

---

## 3. Ratified decisions that constrain a consolidation

"Supersedes?" says whether reversing the decision would need a `Supersedes:` entry.

| Decision | Source | Supersedes? |
|---|---|---|
| **Three primary layers (Infra / Apps & Tools / Activity) plus overlays** (Identity / Data / Defense, later + Adversary). "The overlay model is the v1 ceiling on the number of planes." | H1:3807 (Authority: design chat); `ARCHITECTURE.md` §1 l.68–86 "Ratified" | **Yes**, if a UI abandons the layer model. Note that **no UI ever implemented it**; the Console's levels are network/segment/host zoom, not layers. |
| **Canvas → IR contract**: "The canvas is 'just an editor' — its only output is the Scenario IR … No hidden canvas state." | `ARCHITECTURE.md` l.88–92 ("ratified at conceptual level"); `OPEN_QUESTIONS` Q1 | **Yes.** The Console already departs from it: it emits assertion records rather than IR, and `layout.json` pins are canvas state kept outside the IR. No Supersedes was ever written. |
| **The API is a separate, UI-agnostic control plane** with purpose-shaped DTOs; the UI never touches engine internals; headless-capable; AI and CI are later consumers | H1:1027; `design/11` l.1–25, 72–84 | Yes, but consolidation shouldn't need to reverse it. |
| **UI = "viewer + control + telemetry — not an authoring tool"**; "The UI reads the IR and drives the engine; it never emits IR" | H1:1027 (user); brief §1; `design/11` l.176–181 (still current text) | **Already de facto reversed** by `design/14` §7 ("the canvas as the authoring surface", user 2026-09-04) with **no Supersedes entry**. `design/11` and `design/14/15` contradict each other today. A consolidation must pick one and write the Supersedes. |
| **Node detail = config + state store + on-demand live facts over QGA; no per-node agent** | H1:1027; `design/11` l.50–63 | Yes. |
| **Single-pane Grafana**: all UI inside Grafana via provisioned dashboards plus one panel plugin, datasource proxy, credentials held server-side; topology as a hierarchical tree, not Node Graph | H1:275 (user, Supersedes option B); `design/11` l.138–163; `design/05` l.50–62 ("Grafana … is the single operator-facing surface") | **Already de facto reversed** by `design/15` ("a SPA, not Grafana", user 2026-09-04) with **no Supersedes entry**: `grep single-pane` in CL finds nothing. `design/README.md` l.38 and `design/11` l.1 still say single-pane Grafana. The S7 iframe partly reconciles the two. |
| **Console: SPA, not a Grafana plugin; starts clean** (nothing ported from the plugin); React + TS + Vite, plain SVG, **no react-flow / d3-force / dagre**; served by `axrange serve --spa-dir`; the API base URL is its only configuration; a later Grafana integration "must stay a small task" | `design/15` l.35–39, §9, §12 (user 2026-09-04); CL:3991 | **Yes** to fold it back into Grafana, or to reuse plugin code in it. |
| **CLAUDE.md**: "`o2/console/` (Vite/React SPA …) independent of the Grafana plugin"; `o2/console/README.md`: neither imports the other; `axrange/` never imports `o2/` | CLAUDE.md "Conventions"; console README "Boundaries" | Edit CLAUDE.md (a living spec); record it in CL. |
| **Console principles** (`design/15` §2): zoom swaps representation; layout is a pure function of identity; edges leave the diagram (bundles plus matrix); the map is one of several coordinated projections; the URL is the state; the canvas is an assertions editor, "not a drawing tool"; evidence class is the colour channel (color "belongs to evidence class") | `design/15` l.60–73 | Yes. |
| **Deterministic, prefix-grouped layout with optional pins** (a fully pinned layout lost); hosts never pinnable | `design/15` §4, §12 (user) | Yes. |
| **Arcs, not orthogonal lanes**, for east-west links | `WORK_ORDER_console_level0_rendering.md` §4.1 (user 2026-09-11); CL:2329 | Yes. |
| **The canvas edits the imprint, never the deployed twin**; the twin follows by `te deploy --update` or a fresh deploy | `design/14` §4 and §8; `design/15` §11 l.571; console README l.7–8 | **Yes.** This is the single biggest design-time/run-time wall. |
| **Save and Apply are two actions; drift is flag-only; first edit set is fixed** (add segment/host, change role, suppress, image/sizing override, conduit; `route` refused); **the YAML is the contract, "the UI is thin over it"** | `design/14` §4, §7 (user 2026-09-04) | Yes. |
| **Six-class evidence model and precedence** (asserted > imported > observed > referenced > imputed > compiler) | `design/14` §2, §7 (user) | Yes (it is an aximprint contract). |
| **D5**: "the Console is the one surface that renders a belief store"; the plugin keeps only "Deploy as twin"; "one surface per job" | WO `console_canvas` §8.1 (user 2026-09-11); `design/15` l.484–490 | Yes. |
| **D6**: observed CIDR on tiles and routes, deployed CIDR in the inspector; routes spell CIDRs as `10.80.20.0-24` | WO §8.1 (implementation call, open to veto) | Implementation call; record only. |
| **User UI constraints, 2026-08-27**: say why a view is empty; never invent physical facts; reuse published endpoints; one model and one renderer; a render audit that fails on a blank canvas | `WORK_ORDER_console_selector_and_stale_deploys.md` SCOPE EXPANSION | They bind any consolidation; they read as direction rather than a CL decision. |
| **Logical/Physical semantics are user-authored**: Logical = the conversation graph; Physical = the full observed census; "a shipped `twin_config` is explicitly NOT an acceptable substitute" | CL:4649 correction and CL:4519 (Authority: user) | **Yes.** Redefining these once without confirmation was already flagged as a violation (CL:4649). |
| **Auth**: reads open on loopback; writes need a bearer token; `?secrets=true` is 401 without a token; masked by default; the Console write token lives in memory only (never in localStorage, cookies or the bundle); Grafana credentials are held server-side in datasource `secureJsonData` | `design/11` "Auth posture" l.89–100; `design/15` l.508–511 | Yes, for any change of posture. There is **no CORS**, which is why the embed had to be an iframe (`design/15` l.466–471). |
| **Role and persona are `tfc`-owned and runtime-swappable; no sixth tier** | DIRECTION_LOG l.475–500; `WORK_ORDER_role_persona_tier.md` (DONE, live-verified 2026-08-04) | Yes. |
| **User sequencing instruction**: "UI layout and view polish come later" | WO `console_canvas` header (2026-09-04) | Direction only. |
| **Canvas / AI out of v1** (2026-05-28), later lifted | H1:3224 → H1:1898 | Historical. |

---

## 4. Recorded complaints, gaps and open questions about UI/UX

**User complaints, quoted**
- 2026-06-29 (GAP-005/006): Destroy from Grafana failed. After that, "clicking around the panel lost the active-deploy selection; the Destroy button then greyed out 'as if nothing was deployed'" while 5 VMs were running. The user fell back to the CLI (`PRODUCT_GAPS.md` l.235, l.267).
- 2026-08-04: "an operator finding that changing a deployed node's persona appeared to require a redeploy" (DIRECTION_LOG l.478).
- 2026-08-27: "the topology & twin views in both expect a scenario to be specified — which looks to be a hardcoded path where nothing is listed. Also, Topology tab shows stale deployed twins" and "The topology view doesn't show anything in it. I was expecting … logical, physical & deployed views for a given twin" (`WORK_ORDER_console_selector_and_stale_deploys.md` l.13–16, l.~87).
- 2026-09-04: "several single-diagram UI attempts had failed to scale"; "every element competed for one plane, so the diagram became a hairball; and the layout was not a function of the data, so every recompile reshuffled positions" (`design/15` l.35, l.46–51).
- 2026-09-11: "S5 as built is confusing in use (user, on the live canvas): forms unrelated to the selection, add-host one at a time from the inspector only, no pending state on the level-0 map, drag meaning only 'pin'. The operator's actual task — a new segment with hosts behind it — had no path" (CL:2309).
- 2026-09-11: the live level 0 "did not read as a network (no trunk, no rails, tie-lines and lanes crossing tiles, illegible scale)" (`design/15` l.17–18). The findings table is in the archive (`WORK_ORDER_console_level0_rendering.built.md` §2, F1–F9). "The half-day that fixes 'I can't read it'" (R1).

**Product gaps (UI)**
- GAP-005 and GAP-006: resolved.
- GAP-079 (Console couldn't draw conduits): resolved by R5.
- GAP-080 and GAP-081: pin-key asymmetry; a suppress re-stage flips to applied.
- GAP-082: resolved.
- GAP-083 (confident zeros): resolved by deletion.
- GAP-094 (edits lost on navigation): resolved with drafts.
- **Open:**
  - **GAP-095**: a persona emit has no served-vs-dialed direction, so the Console's generated listen sets are wrong for five roles. The UI discloses this.
  - **GAP-096**: a clone's pin is written to the server while it is still pending.
  - **GAP-097**: `schema_version` is rendered unchecked by both plugin and SPA.
  - **GAP-098**: the plan confirmed in the UI is not pinned; the server re-plans.
- Operational gotchas that make the UI fragile:
  - LES-315: a stale `:8080` listener serves an old build.
  - LES-338: browser probes write into checked-in content.
  - LES-341: a cross-origin iframe cannot report its own failure.
  - LES-042: the plugin carries a ~118-error typecheck baseline.

**Open questions**
- `OPEN_QUESTIONS.md` Q1 "Canvas UX and serialization": still lists rendering tech, per-layer visibility, auto-layout, layer bindings, versioning UX and the overlay toggle as open. It says "We have not even decided whether v1 has a canvas at all" (l.27). It has never been updated for any of the UIs above.
- Q11 "Operator interface for run management": "Does v1 have a web UI, or CLI-only? … follow a single op's logs in real time … design needed" (l.246–249). Also never updated.
- `design/15` §12 open items with v1 defaults: the site bucket rule, the palette ("starting point, not verdict"), partial apply (no), and the highlight duration.
- WO `console_canvas` §21.5: the embed's rendering, `/api` access from inside the frame, storage partitioning of drafts, dashboard persistence, the proxy 401, and a real deploy. All unconfirmed.
- The level-0 **column wrapping** slice is unwritten.

**UI-heavy lessons**
LES-320 to LES-342 are almost entirely *implementation-correctness* lessons about the Console: focus handling, reducers, drafts, `waitFor`, jsdom styles, iframes. **None records operator usability feedback [inference from titles].**

---

## 5. Intended user workflows, and where the docs admit the CLI is needed

**Documented workflows**
- **Original 8/9-step loop** (brief §1 and §7; H1:1027): author in Claude → view → validate → select target → deploy → run → alerts → pcaps, plus monitor and teardown.
- **Golden-twin loop** (`design/14` §4 diagram; `design/13` §6): discover → compile → bundle → canvas display/edit → save/apply (recompile) → refresh → (twin follows by `te deploy --update` or fresh deploy) → certify (scorecard) → drift.
- **Five-tier runtime model** (`design/02`, `design/11` CLI grid): `te` deploy → `tap` bind → `tfc` persona playout → `exr` exercise → observe in Grafana/Loki → destroy with cascade.

**Admissions that the CLI is needed**
- `design/11` l.165–170: tap, traffic and exercise playouts, the twin-config catalog, inventory and access facts "have no panel affordance yet; reaching them today means the CLI." The Run button is a stub.
- `design/11` table: `POST /api/validate` and `/api/plan` accept scenarios only, not twin-config or imprint ids. Tap has no GET: "read back through `te inventory`/`tap` CLI status."
- `design/14` §4, `design/15` §8 and §11, console README l.7–8: the canvas edits the imprint only. The change list merely *tags* each edit as "`te deploy --update`" or "fresh deploy". Carrying an edit to a standing twin is a CLI step: the plugin's Deploy-as-twin (`DeployTwinAction.tsx`) has no update mode **[verified: no "update" in the file]**.
- `design/05` l.118–127: the O2 server half is a manual `docker compose` on a dedicated host, separate from `axrange o2 deploy`.
- GAP-006: an orphaned deploy was cleaned up "out-of-band via `axrange destroy … --allow-destroy` from the CLI."
- `design/11` l.57–58: the node live tier (`?live=true`) is not implemented; `te info` / the access endpoint is the live-facts path.
- `design/14` §4: "The YAML-first path stays: it is what the tests drive and what scripting uses; the UI is thin over it."
- The whole current operating thread (corp-twin, `SESSION_STATE.md`) is run from the CLI and YAML: `te deploy`, `tap deploy`, `tfc deploy`, `te egress`. The Console was explicitly out of scope for it (`WORK_ORDER_corp_twin.md` §13: "The Console canvas layout for this twin … is not written").

---

## 6. Twin casting, personas and per-node changes from a UI standpoint

**Personas**
- In the Console, persona appears only as a **read-only fact** on the level-2 host inspector (`design/15` l.81).
- The persona library is used only to **prefill listen ports** for generated hosts (`design/15` l.342–354; GAP-095).
- **No assertion record kind covers persona.** The closed set is add segment/host/conduit, override role/image/resources, and suppress (`design/14` §4; `design/15` §8 table).
- The plugin has no persona or `tfc` UI at all. `grep` finds only a type comment in `io/api.ts` l.195 ("a tfc assignment wins over the twin's own baseline").

**"Edit one node's persona and apply"**
- Designed and built **at the engine/CLI level only**. W1 (`WORK_ORDER_role_persona_tier.md`, DONE, live-verified 2026-08-04) made role and persona `tfc`-owned and runtime-swappable with **no VM touched**: a `roles:` block or per-host assignment in the traffic profile, then `tfc deploy` re-apply, with a de-scope pass that stops orphaned instances (`design/07` l.~245–258). `POST /api/tfc` exists.
- **No UI has ever been designed for it.**

**Mismatch [inference, from the cited texts]**
- The Console's "Change role" gesture goes assertions → recompile → the change list classifies it as "a mutation of an existing node — a role change, hence an image change — is a fresh deploy" (`design/14` §4).
- So the one per-node change the engine can do cheaply at runtime (a role/persona swap via `tfc`) is only reachable through the CLI.
- The UI's only role edit routes to the most expensive path (a fresh deploy of the imprint).

**Casting in the UI**
- The Console's Apply runs `aximprint cast` + `install-cast` as a subprocess (`design/15` §10). It works only for bundles carrying a `cast_invocation` block (422 otherwise).
- It covers aximprint-cast imprints only. Hand-authored twins (`corp-twin`, `sme-flat`, `svc-*`, `corp-enterprise`, and so on) have no export, so there is no map.

**"What is deployed where" overview**
- Never designed as a first-class view. Fragments:
  - `list-deploys [--tier …]`, described as "a cross-tier recent-deploys view" (`design/11` l.122), CLI only.
  - `te inventory` / `GET /api/deploys/{id}/inventory`.
  - The plugin's deploys list (te only).
  - The Twin screen's Deployed and Structure tabs (per twin; te structure plus op status; no tap/tfc/exr overlay).
  - The first-cut "Physical" tab (vmid / PVE node / bridge / NIC), which was **reverted** to mean the aximprint census (CL:4649 → CL:4519).
  - The 2026-08-14 hand-SVG HTML reports (deployed vs. blueprint, including the capture plane).
- `design/05` "Production topology" is design-only. Nothing shows twins × tiers × sensors × cluster nodes in one place **[inference]**.

---

## 7. Assessment: recurring failure patterns **[inference, grounded in the citations above]**

1. **Restart instead of converge.** Seven surfaces in roughly 13 weeks: viewer SPA → Grafana plugin → seven-renderer bake-off → Imprints screen → Twin screen → Console SPA ("starts clean, nothing ported") → iframe embed. Each reboot was triggered by a newly found reference artifact rather than by operator-task analysis: axDigitalTwin (06-19), pxAnalytics (06-25), the apcn topology report (08-27, 09-11 "the reference is the floor"). Old surfaces were rarely removed: the bake-off, the Twin tabs, the deploy toolbar and the dashboards all persist. CL:4747's "consolidation TBD" was never done.

2. **Different surfaces for different source kinds and tiers, with no shared object model.** There are three input kinds (scenario IR, twin-config, imprint) and five tiers (te/o2/tap/tfc/exr), and each UI covers a different, partial subset. The user's own 2026-08-27 direction ("ONE json model and ONE renderer … or the panel grows three code paths that drift") was recorded and then contradicted by the next build.

3. **A wall between design-time and run-time.** The newest and richest UI (the Console) is by rule "never an editor of the deployed twin" and reads only `/api/imprints/*`. The run-time UI (the plugin) froze at the June `te`-only surface. Every real operating action since August (tap binds, traffic re-applies, `--update`, egress, persona rebalancing) is CLI and YAML. Cheap runtime edits have no UI; the UI's edits route to fresh deploys.

4. **Editors before viewers; depth before breadth.** S4–S5.1 built pins, batches, clone-and-drag, drafts with reconciliation, a plane-epoch stamp and whole-gesture undo while the basic questions stayed unanswered in any one place: which twins are up, on which node, with which taps and personas, and whether traffic flows. It is also the one UI that shows nothing for the flagship twin.

5. **Fixture-built, test-green, live-wrong.**
   - Track C was built on a hand-written fixture and rendered confident zeros on real data (GAP-083, LES-277).
   - The Console's level 0 was ruled as a path diagram but shipped as a grid of identical tiles; this was found only on a live review (F1–F9).
   - The Twin-screen handoff landed on a blank canvas (LES-196).
   - The embed has 1018 passing Vitest tests but has never run in a live Grafana (§21.5).
   - The user's "polish later" instruction plus pipeline-driven slices favoured provable invariants over looking at the screen.

6. **Engineer-facing concepts and vocabulary on screen.** Six evidence classes with confidence saturation, ghosts, override and drift badges, pending/saved/applied, Save vs Apply, fingerprints and etags (412 vs 409), plane stamps, "cast", "imprint", "belief store", `?occupancy_lt=`, and hyphenated CIDR routes. The Console README alone is many screens of dense status prose. This plausibly explains "user unfriendly and confusing"; no usability testing or task walkthrough is recorded anywhere.

7. **Decision drift without reconciliation.** The docs now carry live contradictions:
   - "single-pane Grafana" (`design/11`, `design/05`, `design/README` l.38) vs. "SPA, not Grafana" (`design/15`);
   - "not an authoring tool" (`design/11` l.176) vs. "the authoring surface" (`design/14` §7);
   - the layered free-hand IR canvas (`ARCHITECTURE` §1, `design/01`) vs. an assertions editor.

   Neither reversal carries a `Supersedes:` entry. `OPEN_QUESTIONS` Q1/Q11, `o2/README.md` ("NOT stood up") and the console README ("S7 … is the only slice left") are also stale.

8. **Unfinished evaluations become permanent features.** The renderer bake-off has no verdict, Logical/Physical became tables "for now", the "Run" button is a stub, and pcap download is deferred. Each leaves a visible control that does little, adding modes without adding capability.

9. **Telemetry half neglected.** The one part of the original loop Grafana was chosen for (alerts and pcaps) has had no live data from the standing Suricata sensor since 2026-07-27 (GAP-067), and pcap download was never built.

Implication for a consolidation proposal:
- It would need explicit `Supersedes:` entries against:
  - the 2026-06-25 single-pane Grafana ruling, or else against `design/15`'s "SPA, not Grafana / starts clean";
  - the 2026-06-19 "viewer, not authoring" ruling, or else `design/14` §7;
  - `design/14` §8 / `design/15` §11 "never edits the deployed twin", if run-time editing is wanted;
  - possibly `ARCHITECTURE` §1's layered canvas and its Canvas→IR contract.
- It must keep:
  - the API/DTO boundary, the auth posture, the evidence model, and D5's "one surface per job";
  - the user's 08-27 constraints (say why a view is empty, never invent facts, one model and one renderer).
