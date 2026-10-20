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
| C1 — discovery | pcaps, **A1** plugins + runs, imprints list/detail/cast |
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

**Stack:** not chosen yet. The first T0 session records the framework and the
E2E runner here in one line and does not revisit it. Whatever it is, it adds a
node toolchain the repo does not have today (no `package.json` anywhere); the
browser tests run as their own gate step beside `pytest`, not inside it.

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

**C1 — discovery:**
- C1.1 (auto) The UI renders one config form per installed adapter from the
  schema A1 reports; adding an adapter changes the forms with no UI code change.
- C1.2 (auto) Submitting a pcap source starts a run; the UI polls and shows it
  reach a completed imprint, then opens it.
- C1.3 (auto) An invalid source config shows the API's reason, not a generic error.

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
