# UI — operator console

## What it is

A single, professional web console for the whole instrument loop, served by
`axrange serve` as a standalone SPA (not a Grafana plugin). It serves every step
of the loop in root `CLAUDE.md`: configure a discovery source and run discovery →
view and edit the discovered twin → deploy it to a target → leave it running for
traffic → optionally inject an attack → confirm tapped traffic is reaching a
sensor. Telemetry views (O2) are embedded Grafana panels, not rebuilt here.

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
| C2 — view twin | covered by T0 |
| C3 — edit twin | validate, **A2** working copy, **A3** twin-config create/update |
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

The console must **not** embed credentials; it calls the API, which holds adapter
config. It must render correctly against an API that reports zero of any list
(empty, not broken).

## Config

Served by `axrange serve` when `AXRANGE_SPA_DIR` points at the built SPA. Grafana
base URL for embedded telemetry panels is a single configurable value (env/served
config), empty = telemetry panels hidden. No other UI config; everything else is
read from the API at runtime.

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
- C1.6 (auto) A 401 names WHICH token is missing: the console's write token
  (the API's `AXRANGE_API_TOKEN`), never the source's credential, and says
  whether none is set or the one set was refused. *(`source.page.test.tsx`.)*
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
- C1.13 (auto) The top bar has Discovery, with Sources and Runs as a
  sub-menu shown while in that area, and Imprints. The Imprints page lists
  every bundle in the catalog with as-of, hosts, segments, mean confidence
  (its source on hover), the newest run this API process ran for that id
  with its status and PIT, whether apply is possible, and the folders the
  listing could not read with the reason. *(`imprints.page.test.tsx`.)*
- C1.14 (auto) An imprint can be deleted behind a confirmation: a bin per
  row on the Imprints page (the row leaves once accepted) and "Delete
  imprint" on its page, which returns to the list. The API's refusal (an
  active run, a live deploy, no token) is shown in place. Run records
  persist across API restarts, so the Imprints page's Run column and the
  runs list survive one; a run the old process left active shows `failed`
  with the restart named. *(`imprints.page.test.tsx`.)*

**C2 — view discovered twin:** covered by T0.1.

**C3 — edit twin (working copy):**
- C3.1 (auto) Add a node and a segment; the editor shows the config valid.
- C3.2 (auto) Duplicate a section and place it onto a target segment; placed nodes
  receive segment-appropriate addressing automatically; still valid.
- C3.3 (auto) Save-As under a new name, from both an undeployed config and a
  deployed twin's working copy, appears in the list and reopens identical; a
  name that already exists is refused with the reason shown.
- C3.4 (auto) An edit that would produce an invalid config is blocked with the
  validator's reason surfaced in the editor.
- C3.5 (auto) Editing a **deployed** twin shows a dirty marker and leaves the
  baseline view unchanged until Persist.
- C3.6 (auto) Reset clears the marker and shows baseline; a second console open
  on the same deploy shows the same state.

**C4 — deploy targets:**
- C4.1 (auto) Add/edit/remove a target; the list reflects it and never shows a
  credential.
- C4.2 (auto) A saved/discovered config can be selected and deployed to a chosen
  target.

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
