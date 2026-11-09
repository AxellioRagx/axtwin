# UI — operator console

## What it is

A single, professional web console for the whole instrument loop, served by
`axrange serve` as a standalone SPA (not a Grafana plugin). It serves every step
of the loop in root `CLAUDE.md`: configure a discovery source and run discovery →
view and edit the discovered twin → deploy it to a target → leave it running for
traffic → optionally inject an attack → confirm tapped traffic is reaching a
sensor. Telemetry views (O2) are embedded Grafana panels, not rebuilt here.

The console is titled **axTwin** (renamed from axRange 2026-10-31; the engine,
its CLI and the API keep their names). Its top menu is the loop's areas:
**Discovery** (Sources, Runs beneath it), **Imprints**, **Twins** and
**Canvas** -- the design surface, with a drop-down that lists every twin in the
catalog (cast from an imprint or authored) to open on the canvas and a *New
design…* entry, and *Designs* / *New design* beneath it. `/canvas` lists the
twins (and the imprints not yet cast, which have no twin-config to draw),
`/canvas/new` writes a blank twin-config (one empty segment, no nodes: the
smallest document the engine accepts) or a copy of one in the catalog through
A3 and opens it, `/canvas/:id` is the canvas (`/twins/:id/canvas` still reaches
it from a twin's page).

It is a **consumer** of the control-plane API (`api/Spec.md`); it holds no
orchestration logic and defines no endpoint of its own. The console is generic
over what the API reports — it lists whatever discovery adapters, targets,
sensor types and exercise playout kinds the API enumerates, and never names a
specific one in its own code.

Build thin-first: **UI v0** (the acceptance gate, tests T0.x) proves the loop
end-to-end over routes that already exist. Each later capability (C1–C7) lands on
its own branch with its own tests, and most wait on one API seam (A1–A6 in
`api/Spec.md`). The UI does not advance a capability whose seam is not built.

**Ordering, stated so it is not relitigated:** root `CLAUDE.md` puts critical-
path items 1–3 before the UI. T0 deliberately starts earlier because it touches
**no** new seam — it is a tracer over existing routes plus A6. Nothing beyond T0
starts before its seam lands.

## Seam

Served from `AXRANGE_SPA_DIR`, calling the same-origin API. What each capability
consumes (routes are defined and verified in `api/Spec.md`; **A*n*** marks a new
seam, everything else exists today):

| Capability | Consumes |
|---|---|
| T0 — v0 loop | twin-configs list/detail, topology, inventory, targets, deploys create/run/status, tfc, taps, **A6** metrics |
| C1 — discovery | **A7** sources (save / test / run), **A1** plugins + runs, imprints detail. **Built 2026-10-27** (sources, probe, run-and-poll, open the imprint); pcap/`cast` views not yet |
| C2 — view twin | covered by T0. **Built 2026-10-30** (the design canvas, `features/edit-canvas`, phase 1: cast and authored graphs, positions, the traffic overlay) |
| C3 — edit twin | validate, **A2** working copy, **A3** twin-config create/update. **Built 2026-10-30** (canvas phase 2: palette, attaching, the command stack, the side panel, Save / Save-As / Apply / Reset / Persist, promote to assertion); the per-mutation `POST /api/validate` is the canvas's own structural check plus the API's validation on every working-copy `PUT` and on Save, because that route validates catalog ids, not documents |
| C4 — targets | targets list, **A4** create/update/delete |
| C5 — tweak + promote | **A2** apply/reset, **A3** update |
| C6 — inventory | deploys list, **A5** `origin=platform` |
| C7 — attack | exr create/list, `/openapi.json` for the playout kinds |
| C8 — capture confirm | taps, **A6** metrics (covered by T0.4) |

**The editor's model** (defined in `api/Spec.md`, "The edit model"): the editor
works on a deployed twin's server-side **working copy**, never the baseline, and
offers four exits — **Apply**, **Reset**, **Persist**, **Save-As**. An undeployed
config has no working copy and offers Save and Save-As only. A discovered imprint
is cast first and then edited as a twin-config; the imprint assertions path stays
behind the provenance view and is not a second editor.

**Vocabulary** (2026-11-08; also `docs/CONVENTIONS.md` §1), one verb per act,
the same on every page:

| Verb | What it does |
|---|---|
| **Deploy** | stands a twin up on a target (`POST /api/twins/{id}/plan`, then `/deploy`) |
| **Destroy** | brings the standing deploy down: the te with its tfc and tap children, the tap's sensor binding released (`DELETE /api/deploys/{id}`; the engine's own verb, `axrange te destroy`) |
| **Redeploy** / **Apply** / **Persist** | change a standing deploy (a new plan from the baseline; the working copy pushed onto the running twin; the working copy written as the baseline) |
| **Delete** | removes a twin config from the catalog (`DELETE /api/twins/{id}`) |
| **Remove** | takes an element out of the design, on the canvas |

Never "undeploy", and never Delete for a deploy. The Deploy control is
state-aware: **Deploy** while nothing stands on the twin, **Destroy…** once a
deploy does (Deploy is not shown, not merely disabled), on the Twins list, the
twin's page and the canvas bar (exits group). The three share one confirm that
names the twin, the target, the deploy id, the tiers coming down (te · tfc ·
tap) and that the tap's sensor binding is released; on the canvas a dirty working
copy must be acknowledged with a checkbox ("unapplied changes will be
discarded"), or persisted first. Progress reads as Apply's (`destroying… te ·
12 s`), the tiers tick off as they come down, and the canvas returns to the
undeployed edit mode without a reload. Delete… on a deployed twin says "destroy
the deploy first" and offers Destroy… instead of surfacing the 409. Per-tier
Stop / Restart for tfc and tap is **not** offered: `/api/tfc` and `/api/taps`
have apply and destroy only (no stop or restart verb), so there is nothing for
the twin page to call. *(`ui/test/destroy.test.tsx`.)*

**Deploy stands the whole twin up** (2026-11-09): te, then the twin's tap plan,
then its traffic profile, as one action (the CLI's `te deploy` → `tap deploy` →
`tfc deploy`; `POST /api/twins/{id}/deploy {follow_on: [tap, tfc]}`). Each
tier's outcome rides on the te row (`follow_on`): queued, running, completed,
failed with its reason, or skipped (`—`: the twin has no taps / no traffic
profile); a tier fails on its own and the next still runs. The twin page's
deploy line and the canvas pill poll until every tier is terminal. Where a tier
has no standing deploy, the canvas bar offers **Start tap** / **Start tfc**
(`POST /api/deploys/{id}/tiers/{tier}`, from the twin's catalog default).
*(`ui/test/twins.deploy.test.tsx`, `ui/test/canvas.exits.test.tsx`,
`engine/tests/test_deploy_follow_on.py`.)*

The console must **not** embed, hold or ask for credentials — neither adapter
config nor its own write credential. Authentication is the reverse proxy's or
the host application's; the API chooses whom to trust per deployment (A8,
`docs/design/ui-shell-and-auth.md`). The console sends same-origin requests
with `credentials: "include"`, reads `GET /api/whoami` for its read-only state
and the operator's name, and reacts to `401` (sign in: the served
`signin_url`) and `403` (read-only) after attempting a write, never before.
It must render correctly against an API that reports zero of any list (empty,
not broken).

**Shell.** Two kinds of route. List and detail pages live in the centred
`.main` column. Canvas routes are the page: the drawing fills the window
under the single top bar, and every control floats over it (tool rail left,
collapsible inspector right, mini-map) — see "Shell and chrome" in
`ui/src/features/edit-canvas/SPEC.md`.

## Config

Served by `axrange serve` when `AXRANGE_SPA_DIR` points at the built SPA. Grafana
base URL for embedded telemetry panels is a single configurable value (env/served
config), empty = telemetry panels hidden. The same served config carries
`signin_url` (`AXRANGE_SIGNIN_URL`, e.g. `/oauth2/start?rd=`), empty = a bare
"sign in required" on `401`. No other UI config; everything else is read from
the API at runtime.

**Stack (recorded 2026-10-27, not revisited):** React 18 + Vite 8 +
TypeScript (strict from zero) + Vitest/Testing Library over jsdom, in `ui/`
(`npm ci && npm run build` → `ui/dist`; `npm run typecheck`, `npm run test`).
The same toolchain as the frozen `o2/console`, so its pure `model/` and
`layout/` code can be lifted when C2 needs a map. The browser tests run as
their own gate step beside `pytest`, not inside it. Served by
`axrange serve --spa-dir ui/dist`; `npm run dev` proxies `/api` to `:8080`.

## Acceptance

Browser tests against a seeded backend, **(auto)** in the node gate step unless
marked **(live)**. API behaviour itself is asserted by `api/Spec.md`'s A-tests;
the tests here assert what the operator sees. A capability is not "done" until
its listed tests pass. T0.x is the release gate for UI v0.

**T0 — v0 end-to-end loop (the gate):**
- T0.1 (auto) Given a seeded `corp-enterprise` twin-config, the console lists it,
  renders its topology (segments and nodes), and selecting a node shows its
  role/persona/traffic-profile attributes from the inventory/config.
- T0.2 (auto) Selecting a target and deploying drives deploy create + run; the
  console reflects deploy status transitions to a terminal state.
- T0.3 (auto) Starting traffic and a tap to a dummy `tcpdump` node succeeds and
  both show as running.
- T0.4 (live) On dev45, the above sequence ends with the tap's metrics panel
  showing packets and bytes **increasing** across two polls (rate > 0). This is
  the capture-flow gate from root `CLAUDE.md`, shown in the UI.
- T0.5 (auto) The console renders with every API list empty and an undeployed
  state without errors (no blank screen, no crash).

**C1 — discovery** (`ui/test/*.test.tsx`, 30 tests, green 2026-10-27):
- C1.1 (auto) The UI renders one config form per installed adapter from the
  schema A1 reports; adding an adapter changes the forms with no UI code change.
  *(`editor.test.tsx`, `run.flow.test.tsx`: a made-up `foo-http` plugin gets its
  form and its own `shard` run option from its schema alone.)*
- C1.2 (auto) Submitting a source starts a run; the UI polls and shows it
  reach a completed imprint, then opens it. *(`run.flow.test.tsx`; a source is
  sensor metadata reached over the network, not a pcap — see A1.)*
- C1.3 (auto) An invalid source config shows the API's reason, not a generic error.
  *(`editor.test.tsx`: the server's refusal verbatim; `run.flow.test.tsx`: a
  refused run, a failed run's own error.)*
- C1.4 (auto) Every list empty renders and says why; zero endpoint plugins is a
  sentence, not a crash. *(`empty.test.tsx`.)*
- C1.5 (live) Against `corp-apcn-reader` through the SSH tunnel: save, test
  (span + census shown), run ten days, poll to completion, open the imprint.
  Done by API on 2026-10-27 (`docs/SESSION_STATE.md` has the run).
- C1.6 (auto) A `401` on a write is shown as "sign in required" (linking the
  served `signin_url` when set) and a `403` as "read-only for this account";
  neither is confused with a source's own credential failure, which keeps its
  own wording. The console has no credential field and sends no
  `Authorization` header. *(`source.page.test.tsx`, `client.test.ts`;
  rewritten 2026-11-05 for A8 — the old test asserted the write-token
  wording.)* **Built 2026-11-05**: the frame reads `whoami` on load and shows
  the name, a read-only pill and the sign-in link; `ErrorLine` links the
  sign-in URL on a 401 (`api/whoami.ts` holds the context and `signinHref`).
- C1.7 (auto) The census reads as figures (`16.9 G (16,893,421,675)`,
  `15.5 TB`), the sensor's sites as a table, never as raw JSON; the access line
  carries the credential state (`password stored` / `no password stored`).
  *(`source.page.test.tsx`, `sources.model.test.ts`.)*
- C1.8 (auto) A run page draws its window as a bar: the bounds at the ends,
  the point the ingest has reached (`progress_ts`) as the fill, the covered
  days out of the total, and the remainder at the rate so far; full once
  ingest is done. The runs list carries the same figure as a column.
  *(`run.page.test.tsx`.)*
- C1.9 (auto) A queued or running run offers "Cancel run" behind a
  confirmation; the badge reads `cancelling` from the request until the next
  poll finds the stage dead, then `cancelled` with what was cleaned up. A
  finished run offers delete instead (C1.12). *(`run.page.test.tsx`.)*
- C1.10 (auto) The stage a failed run died in is red; the ones it never reached
  stay plain. A completed run offers its imprint only after the catalog
  confirms the bundle is there; a bundle removed since says so, with no link.
  *(`run.page.test.tsx`.)*
- C1.11 (auto) The run window is "last N days|hours" (hours sent as fractional
  `days`) or epoch-second bounds; the sites table gives first/last as epoch
  seconds beside the span's UTC reading. *(`run.flow.test.tsx`,
  `source.page.test.tsx`.)*
- C1.12 (auto) A run has one write, behind a confirmation: cancel while queued
  or running, delete once finished. The run page offers "Cancel run" or
  "Delete run" accordingly, and a deleted run's page returns to the list; the
  runs table carries the same as an icon per row (× cancel, bin delete), the
  row leaving once the delete is accepted. Deleting forgets the record only;
  the imprint stays. *(`run.page.test.tsx`.)*
- C1.13 (auto) The top bar has Discovery (Sources and Runs as a sub-menu
  while in that area), Imprints and Twins. *(`imprints.page.test.tsx`.)*
- C1.14 (auto) Imprints are what discovery produced: the page lists every
  PIT in the IR store by name with as-of, sizes, what it was derived from,
  whether it has assertions and the twins cast from it; a bin per row deletes
  behind a confirmation. An imprint's page renames it (the id never changes),
  saves it as a new imprint (a copy with `derived_from`, moving to the copy),
  edits the operator's assertions as the three JSON lists (the etag rides in
  the body), and casts it into a twin: twin id (defaulting to the name),
  sensors, replace. The cast's result names the twin and its caveats; a taken
  id is refused until replace is ticked. While a bake holds the IR store the
  list is the last one read, and says so. *(`imprints.page.test.tsx`.)*
- C1.15 (auto) Twins are the catalog: the page lists every deployable twin
  with as-of, hosts, segments, confidence (its source on hover), what it was
  cast from (a link to the imprint, or "authored") and whether apply is
  possible, plus the folders the listing could not read; a bin per row and
  "Delete twin" on its page delete behind a confirmation, the API's refusal
  (a live deploy) shown in place. *(`imprints.page.test.tsx`.)*
- C1.16 (auto) A run's stages are ingest and bake. A completed run names the
  imprint it baked, links to it once the store confirms it is still there
  (a deleted one says so), and shows its name with a pencil to rename it.
  The run form asks for an imprint name, not a catalog id.
  *(`run.page.test.tsx`, `run.flow.test.tsx`.)*

**C2 — view discovered twin:** covered by T0.1. **Built 2026-10-30** (the
design canvas renders the cast and the authored graph with every segment,
host and router, positions stable across renders of one layout document;
*`canvas.test.tsx`*).

**C3 — edit twin (working copy):** **Built 2026-10-30** (*`canvas.edit.test.tsx`*,
*`canvas.edit.model.test.ts`*; the design canvas's phase 2). **2026-10-31:** the
Canvas area -- the top-menu entry with its drop-down of designs, `/canvas`,
`/canvas/new` (blank or copied design through A3), `/canvas/:id`
(*`canvas.menu.test.tsx`*).
- C3.1 (auto) Add a node and a segment; the editor shows the config valid.
  **Built 2026-10-30.**
- C3.2 (auto) Duplicate a section and place it onto a target segment; placed nodes
  receive segment-appropriate addressing automatically; still valid.
  **Built 2026-10-30** (the lifted `allocate.ts`: `.10` upward, skipping pinned
  addresses and the gateway).
- C3.3 (auto) Save-As under a new name, from both an undeployed config and a
  deployed twin's working copy, appears in the list and reopens identical; a
  name that already exists is refused with the reason shown. **Built 2026-10-30.**
- C3.4 (auto) An edit that would produce an invalid config is blocked with the
  validator's reason surfaced in the editor. **Built 2026-10-30** (the canvas's
  structural check blocks in place; the API's refusal of a working-copy `PUT`
  reverts the edit and is shown verbatim).
- C3.5 (auto) Editing a **deployed** twin shows a dirty marker and leaves the
  baseline view unchanged until Persist. **Built 2026-10-30.**
- C3.6 (auto) Reset clears the marker and shows baseline; a second console open
  on the same deploy shows the same state. **Built 2026-10-30** (two mounted
  consoles over one fake control plane; the second polls and has a Refresh).

**C4 — deploy targets:**
- C4.1 (auto) Add/edit/remove a target; the list reflects it and never shows a
  credential.
- C4.2 (auto) A saved/discovered config can be selected and deployed to a chosen
  target.
- C4.3 (auto) A standing deploy is destroyed from the Twins list, the twin page or
  the canvas bar through one confirm; the row and the canvas read undeployed after,
  Deploy offered again. *(`destroy.test.tsx`. Live on dev45 2026-11-08: canvas-test
  deployed from the Twins list (4 VMs, ~270 s), destroyed from the canvas bar in ~27 s
  (te only: the console's Deploy stands no tfc or tap, so the cascade is proven by
  `engine/tests/test_api.py`, not live); `/api/deploys` then lists no canvas-test row.)*

**C5 — tweak + promote:**
- C5.1 (auto) Apply on a traffic/tap change reaches the running twin without a
  redeploy and the UI shows it applied.
- C5.2 (auto) Apply on a non-ephemeral change shows the API's refusal naming the
  offending paths; nothing changes.
- C5.3 (auto) Reset after an Apply shows the twin back at baseline.
- C5.4 (auto) Persist clears the dirty marker; reopening shows the persisted
  config.

**C6 — steady-state inventory:**
- C6.1 (auto) The deploys view lists running twins with tier/status/target.
- C6.2 (live) A console opened on a **different host** than the one that deployed
  enumerates the same live twins.

**C7 — attack injection:**
- C7.1 (auto) The playout form offers exactly the kinds the exercise request
  schema declares, read from `/openapi.json`, not hardcoded; a new kind in the
  schema appears with no UI code change.
- C7.2 (auto) Submitting a playout runs it; the twin is **not** destroyed.
- C7.3 (auto) A twin is destroyed only via an explicit destroy action.

**C8 — capture confirm:** covered by T0.4.

## Notes

- Thin-first: ship T0 (v0) before any of C1–C7. Each capability is its own branch
  and merges independently; none of them edit root `CLAUDE.md`. When a capability
  starts, it gets its own short `SPEC.md` beside its code per `SPEC.template.md`;
  this file stays the map, not the ledger.
- Generic over the API: adding a discovery adapter, a sensor type or a playout
  kind must require **zero** UI code changes (C1.1 and C7.1 pin this).
- No backend work is specified here. Anything the console needs from the API is
  a seam in `api/Spec.md`, or does not happen.
