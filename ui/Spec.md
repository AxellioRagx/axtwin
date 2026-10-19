# UI — operator console

## What it is

A single, professional web console for the whole instrument loop, served by
`axrange serve` as a standalone SPA (not a Grafana plugin). It serves every step
of the loop in root `CLAUDE.md`: configure a discovery source and run discovery →
view and edit the discovered twin → deploy it to a target → leave it running for
traffic → optionally inject an attack → confirm tapped traffic is reaching a
sensor. Telemetry views (O2) are embedded Grafana panels, not rebuilt here.

It is a **consumer** of the control-plane API; it holds no orchestration logic
of its own. The console is generic over what the API reports — it lists whatever
discovery adapters, targets, sensor types, and exercise playouts the API
enumerates, and never names a specific one in its own code. Two of those lists
are backed by real registries today (discovery `IngestPlugin`s, the sensor-type
catalog); the other two are **not** registries and this spec does not invent
them: targets are the named entries the API already serves from the adapter
targets file (one Proxmox adapter exists), and attacks are the two playout kinds
the exercise request schema already declares (`activity`, `adversary`). A plugin
registry for either is a new abstraction and needs explicit sign-off first.

Build thin-first: **UI v0** (the acceptance gate, tests T0.x) proves the loop
end-to-end over seams that already exist. Each later capability (C1–C7) lands on
its own branch with its own tests, and most need a small new API seam, listed
below. The UI does not advance a capability whose API seam is not yet built.

**Ordering, stated so it is not relitigated:** root `CLAUDE.md` puts critical-
path items 1–3 (live discovery, inject+confirm, edit endpoint) before the UI.
T0 deliberately starts earlier because it touches **no** new seam — it is a
tracer over existing endpoints. Nothing beyond T0 starts before its seam lands.

## Seam

The console plugs into the existing control-plane API (`engine/axrange/api`) and
is served from `AXRANGE_SPA_DIR`. It depends on these API endpoints; **(exists)**
are present today (verified against the registered routes, 2026-10-19), **(new)**
are the thin backend seams this feature requires and must be built with it (each
new one is a critical-path item, not UI chrome):

- Discovery (C1): `GET /api/pcaps` (exists), `GET /api/imprints`,
  `GET /api/imprints/{id}` (exists, fixture today), `GET /api/discovery/plugins`
  (new — enumerate installed `IngestPlugin`s + their config schema),
  `POST /api/discovery/runs` (new — run a real discovery; replaces the fixture),
  `GET /api/discovery/runs/{id}` (new — status of a run).
- View twin (C2): `GET /api/twin-configs`, `GET /api/twin-configs/{id}`,
  `GET /api/scenarios/{id}/topology`, `GET /api/deploys/{id}/inventory` (exists).
- Edit twin (C3) — **the edit model: baseline + working copy.** The editable
  object is the twin-config. Its persisted catalog entry is the **baseline**. A
  deployed twin carries a server-side **working copy** of its config beside the
  deploy (the way an editor keeps a swap file beside an open file), and the
  editor operates on that copy, never on the baseline directly. Three exits:
  **Apply** pushes the working copy's change onto the running twin; **Reset**
  discards the working copy (and anything applied from it) back to baseline;
  **Persist** overwrites the baseline with the working copy. An undeployed
  config has no working copy — the editor saves it straight to baseline.
  Endpoints: `POST /api/validate` (exists); `GET|PUT /api/deploys/{id}/config`
  (new — read / write the working copy; a PUT never touches the baseline);
  `POST /api/deploys/{id}/config/reset` (new); `POST /api/twin-configs` +
  `PUT /api/twin-configs/{id}` (new — save-as / persist; the console sends the
  working copy as the body). Apply is C5's seam below. A discovered imprint is
  cast to a twin-config first (`GET /api/imprints/{id}/cast`, exists) and then
  edited as one.
  Why the imprint-side write path that already exists is **not** this:
  `PUT /api/imprints/{id}/assertions` + `POST .../apply` rewrite the operator
  assertions, re-cast the twin from the discovered source and install the result
  over the catalog bundle (keep-5 history, `GET .../diff`). That edits the
  *derivation* of a twin, persistently, with no notion of a running deploy or a
  reset-to-baseline. The console keeps it behind the provenance view.
- Deploy targets (C4): `GET /api/targets` (exists), `POST|PUT|DELETE /api/targets`
  (new — manage target configs), `POST /api/deploys` + `POST /api/deploys/{id}/run`
  (exists).
- Tweak + promote (C5): the **Apply** exit of the edit model.
  `POST /api/deploys/{id}/config/apply` (new — push the working copy's delta onto
  the running twin without a redeploy). Underneath it drives what already exists:
  `GET|POST|DELETE /api/tfc` and `GET|POST|DELETE /api/o2/taps` (exist — note taps
  live under the `o2` prefix, there is no `/api/tap`). v0 scope of Apply is
  exactly what those two can change ephemerally; a delta they cannot apply
  (topology, addressing, images) is refused by name, never silently skipped.
  "Promote to permanent" is the **Persist** exit (`PUT /api/twin-configs/{id}`,
  shared with C3).
- Steady-state inventory (C6): `GET /api/deploys` (exists);
  `GET /api/deploys?origin=platform` (new — reconstruct live twins by querying the
  platform's tier-tagged resources, so a browser on any host sees them without a
  shared local state.db). Not `source=`: that name is already a query parameter
  on the topology routes meaning catalog pin (`scenario|twin-config|imprint`).
- Attack injection (C7): `POST /api/exr` + `GET /api/exr` (exist). The console
  builds the playout from the request schema's two kinds — an `activity` (inline
  steps on a host) and/or an `adversary` (Caldera server + target nodes). No
  `GET /api/attacks`, no attack plugin registry (see "What it is").
- Capture confirm (C8): `GET /api/o2/taps` (exists); `GET /api/o2/taps/{id}/metrics`
  (new — pkts/bytes/rate from the sensor or a dummy `tcpdump` node); dummy-sensor
  stand-up reuses the existing tap verify-capture heartbeat path.

The console must **not** embed credentials; it calls the API, which holds adapter
config. It must render correctly against an API that reports zero of any list
(empty, not broken).

## Config

Served by `axrange serve` when `AXRANGE_SPA_DIR` points at the built SPA; the SPA
calls the same-origin API. Grafana base URL for embedded telemetry panels is a
single configurable value (env/served config), empty = telemetry panels hidden.
No other UI config; everything else is read from the API at runtime.

**Stack:** not chosen yet. The first T0 session records the framework and the
E2E runner here in one line and does not revisit it. Whatever it is, it adds a
node toolchain the repo does not have today (no `package.json` anywhere); the
browser tests run as their own gate step beside `pytest`, not inside it.

## Acceptance

Validation tests the feature must pass. Each is **(auto)** — runs in the normal
gate (API/integration test under `pytest`, or a browser test against a seeded
backend in the node step) — or **(live)** — run once on the dev cluster, recorded.
A capability is not "done" until its listed tests pass. T0.x is the release gate
for UI v0.

**T0 — v0 end-to-end loop (the gate):**
- T0.1 (auto) Given a seeded `corp-enterprise` twin-config, the console lists it,
  renders its topology (segments and nodes), and selecting a node shows its
  role/persona/traffic-profile attributes from the inventory/config.
- T0.2 (auto) Selecting a target and deploying drives `POST /api/deploys` +
  `/run`; the console reflects deploy status transitions to a terminal state.
- T0.3 (auto) Starting traffic (`POST /api/tfc`) and a tap to a dummy `tcpdump`
  node (`POST /api/o2/taps`) succeeds via the API.
- T0.4 (live) On dev45, the above sequence ends with `GET /api/o2/taps/{id}/metrics`
  showing packets and bytes **increasing** across two polls (rate > 0). This is
  the capture-flow gate from root `CLAUDE.md`, shown in the UI.
- T0.5 (auto) The console renders with every API list empty and an undeployed
  state without errors (no blank screen, no crash).

**C1 — discovery:**
- C1.1 (auto) `GET /api/discovery/plugins` lists installed adapters; the UI renders
  one config form per adapter from its declared schema.
- C1.2 (auto) Submitting a pcap source starts a run; the UI polls and shows it
  reach a completed imprint (against a seeded/fake backend).
- C1.3 (auto) An unrecognized/invalid source config is rejected with a clear
  message (fail-loud, no silent default).
- Out of scope here: a continuous-mode run with interval checkpoints. Nothing in
  `discovery/` implements one; if wanted it is a discovery-package spec, not UI.

**C2 — view discovered twin:** covered by T0.1. A discovered imprint appears in
the same view once C1 produces one and it is cast.

**C3 — edit twin (working copy):**
- C3.1 (auto) Add a node and a segment; the edited config validates clean through
  `POST /api/validate`.
- C3.2 (auto) Duplicate a section and place it onto a target segment; placed nodes
  receive segment-appropriate addressing automatically; result validates clean.
- C3.3 (auto) Save-As persists a new twin-config via the API; reloading returns
  the edited config byte-for-byte equivalent to what was saved.
- C3.4 (auto) An edit that would produce an invalid config is blocked with the
  validator's reason surfaced in the UI.
- C3.5 (auto) Editing a **deployed** twin writes only its working copy: a read of
  the baseline is unchanged until Persist; the editor shows a dirty marker.
- C3.6 (auto) Reset discards the working copy and the editor shows baseline. The
  working copy is server-side: a second console on the same deploy sees the same
  edited / reset state (consistent with C6 host-independence).

**C4 — deploy targets:**
- C4.1 (auto) Add/edit/remove a target config round-trips through the API and the
  list reflects it.
- C4.2 (auto) A saved/discovered config can be selected and deployed to a chosen
  target.

**C5 — tweak + promote (Apply / Reset / Persist on a running twin):**
- C5.1 (auto) A traffic/tap change in the working copy, Applied, reaches the
  running twin as an ephemeral change (tfc / o2 tap) without a redeploy.
- C5.2 (auto) A working-copy delta Apply cannot make ephemerally is refused with
  the reason named in the UI; the running twin and the baseline are untouched.
- C5.3 (auto) Reset after an Apply reverts the running twin's tweak and clears
  the working copy; the twin matches baseline again.
- C5.4 (auto) Persist overwrites the baseline with the working copy
  (`PUT /api/twin-configs/{id}`); a fresh read shows it, and the working copy is
  no longer dirty. The baseline is never written by any other action.

**C6 — steady-state inventory:**
- C6.1 (auto) `GET /api/deploys` lists running twins with tier/status/target.
- C6.2 (live) A console opened on a **different host** than the one that deployed
  enumerates the same live twins (reconstructed from the platform), proving
  host-independence.

**C7 — attack injection:**
- C7.1 (auto) The playout form offers exactly the kinds the exercise request
  schema declares, read from the API's OpenAPI document, not hardcoded; adding a
  kind to the schema changes the form with no UI code change.
- C7.2 (auto) Submitting a playout runs it as an `exr`; the twin is **not**
  destroyed by the run.
- C7.3 (auto) A twin is destroyed only via an explicit destroy action.

**C8 — capture confirm:** covered by T0.4 (real sensor or dummy `tcpdump` node,
climbing pkts/bytes/rate). O2 detection is out of scope for this gate.

## Notes

- Thin-first: ship T0 (v0) before any of C1–C7. Each capability is its own branch
  and merges independently; none of them edit root `CLAUDE.md`. When a capability
  starts, it gets its own short `SPEC.md` beside its code per `SPEC.template.md`;
  this file stays the map, not the ledger.
- The console is generic over what the API reports — adding a discovery adapter
  or a sensor type must require **zero** UI code changes (C1.1 pins the former;
  C7.1 pins the playout form the same way).
- Do not rebuild backend that already exists (see **(exists)** above); the real
  new backend work is the **(new)** seams — discovery plugins/runs, the
  per-deploy working copy (read/write, apply, reset), twin-config save/persist,
  targets CRUD, platform-origin deploys, tap metrics — and
  `POST /api/discovery/runs` (replacing the fixture imprint reader) is
  critical-path step 1.
- Known limit going in: `GET /api/deploys?origin=platform` reconstruction fidelity
  depends on tier-tagged resource names being present; a hand-made resource won't
  appear. Acceptable — record if it bites.
