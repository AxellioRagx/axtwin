# UI — operator console

## What it is

A single, professional web console for the whole instrument loop, served by
`axrange serve` as a standalone SPA (not a Grafana plugin). It serves every step
of the loop in root `CLAUDE.md`: configure a discovery source and run discovery →
view and edit the discovered twin → deploy it to a target platform → leave it
running for traffic → optionally inject an attack → confirm tapped traffic is
reaching a sensor. Telemetry views (O2) are embedded Grafana panels, not rebuilt
here.

It is a **consumer** of the control-plane API and the plugin registries; it holds
no orchestration logic of its own. The console is generic over the registries —
it lists whatever discovery adapters, deploy platforms, sensors, and attack
frameworks are installed, and never names a specific plugin in its own code.

Build thin-first: **UI v0** (the acceptance gate, tests T0.x) proves the loop
end-to-end over seams that already exist. Each later capability (C1–C8) lands on
its own branch with its own tests, and most need a small new API seam, listed
below. The UI does not advance a capability whose API seam is not yet built.

## Seam

The console plugs into the existing control-plane API (`engine/axrange/api`) and
is served from `AXRANGE_SPA_DIR`. It depends on these API endpoints; **(exists)**
are present today, **(new)** are the thin backend seams this feature requires and
must be built with it (each new one is a critical-path item, not UI chrome):

- Discovery (C1): `GET /api/pcaps` (exists), `GET /api/imprints`,
  `GET /api/imprints/{id}` (exists, fixture today), `GET /api/discovery/plugins`
  (new — enumerate installed `IngestPlugin`s + their config schema),
  `POST /api/discovery/runs` (new — run a real discovery; replaces the fixture),
  `GET /api/discovery/runs/{id}` (new — status + checkpoints for continuous mode).
- View/edit twin (C2/C3): `GET /api/twin-configs`, `GET /api/scenarios/{id}/topology`,
  `GET /api/deploys/{id}/inventory` (exists), `POST /api/validate` (exists),
  `POST /api/twin-configs` + `PUT /api/twin-configs/{id}` (new — save / save-as an
  edited config).
- Deploy platforms (C4): `GET /api/targets` (exists),
  `POST|PUT|DELETE /api/targets` (new — manage platform configs),
  `POST /api/deploys` + `POST /api/deploys/{id}/run` (exists).
- Tweak + promote (C5): `GET|POST|DELETE /api/tfc|tap|exr` (exists — ephemeral
  tweaks); "promote to permanent" writes back via `PUT /api/twin-configs/{id}`
  (new, shared with C3).
- Steady-state inventory (C6): `GET /api/deploys` (exists);
  `GET /api/deploys?source=platform` (new — reconstruct live twins by querying the
  platform's tier-tagged resources, so a browser on any host sees them without a
  shared local state.db).
- Attack frameworks (C7): `GET /api/attacks` (new — enumerate installed attack
  plugins for a selectable list), `POST /api/exr` (exists — run the chosen one).
- Capture confirm (C8): `GET /api/tap` (exists); `GET /api/tap/{id}/metrics` (new —
  pkts/bytes/rate from the sensor or a dummy `tcpdump` node); dummy-sensor stand-up
  reuses the existing tap-verify path.

The console must **not** embed credentials; it calls the API, which holds adapter
config. It must render correctly against a registry with zero plugins of a type
(empty, not broken).

## Config

Served by `axrange serve` when `AXRANGE_SPA_DIR` points at the built SPA; the SPA
calls the same-origin API. Grafana base URL for embedded telemetry panels is a
single configurable value (env/served config), empty = telemetry panels hidden.
No other UI config; everything else is read from the API at runtime.

## Acceptance

Validation tests the feature must pass. Each is **(auto)** — runs in the normal
gate (API/integration test, or a Playwright component/E2E test against a seeded
backend) — or **(live)** — run once on the dev cluster, recorded. A capability is
not "done" until its listed tests pass. T0.x is the release gate for UI v0.

**T0 — v0 end-to-end loop (the gate):**
- T0.1 (auto) Given a seeded `corp-enterprise` twin-config, the console lists it,
  renders its topology, and shows each node's role/persona/traffic attributes.
- T0.2 (auto) Selecting a target and deploying drives `POST /api/deploys` +
  `/run`; the console reflects deploy status transitions to a terminal state.
- T0.3 (auto) Starting traffic (`tfc`) and a tap to a dummy `tcpdump` node
  (`tap`) succeeds via the API.
- T0.4 (live) On dev45, the above sequence ends with `GET /api/tap/{id}/metrics`
  showing packets and bytes **increasing** across two polls (rate > 0). This is
  the capture-flow gate from root `CLAUDE.md`, shown in the UI.
- T0.5 (auto) The console renders with empty registries and an undeployed state
  without errors (no blank screen, no crash).

**C1 — discovery:**
- C1.1 (auto) `GET /api/discovery/plugins` lists installed adapters; the UI renders
  one config form per adapter from its declared schema.
- C1.2 (auto) Submitting a pcap source starts a run; the UI polls and shows it
  reach a completed imprint (against a seeded/fake backend).
- C1.3 (auto) A continuous-mode run exposes point-in-time checkpoints per its
  configured interval; the UI lists them and lets one be opened as a twin.
- C1.4 (auto) An unrecognized/invalid source config is rejected with a clear
  message (fail-loud, no silent default).

**C2 — view discovered twin:**
- C2.1 (auto) Topology renders segments and nodes; selecting a node shows its
  role, persona, and traffic-profile attributes from the inventory/config.

**C3 — edit twin:**
- C3.1 (auto) Add a node and a segment; the edited config validates clean through
  `POST /api/validate`.
- C3.2 (auto) Duplicate a section and place it onto a target segment; placed nodes
  receive segment-appropriate addressing automatically; result validates clean.
- C3.3 (auto) Save / Save-As persists via the API; reloading returns the edited
  config byte-for-byte equivalent to what was saved.
- C3.4 (auto) An edit that would produce an invalid config is blocked with the
  validator's reason surfaced in the UI.

**C4 — deploy platforms:**
- C4.1 (auto) Add/edit/remove a target platform config round-trips through the API
  and the list reflects it.
- C4.2 (auto) A saved/discovered config can be selected and deployed to a chosen
  target.

**C5 — tweak + promote:**
- C5.1 (auto) A traffic/tap tweak on a running twin applies as an ephemeral change
  (tfc/tap) without redeploying the twin.
- C5.2 (auto) "Save permanently" writes the tweak back into the twin-config
  (`PUT /api/twin-configs/{id}`); a fresh read shows it persisted.

**C6 — steady-state inventory:**
- C6.1 (auto) `GET /api/deploys` lists running twins with tier/status/platform.
- C6.2 (live) A console opened on a **different host** than the one that deployed
  enumerates the same live twins (reconstructed from the platform), proving
  host-independence.

**C7 — attack injection:**
- C7.1 (auto) `GET /api/attacks` lists installed attack plugins; installing/removing
  one changes the selectable list with no UI code change.
- C7.2 (auto) Selecting an attack runs it as an `exr`; the twin is **not** destroyed
  by the run.
- C7.3 (auto) A twin is destroyed only via an explicit destroy action.

**C8 — capture confirm:** covered by T0.4 (real sensor or dummy `tcpdump` node,
climbing pkts/bytes/rate). O2 detection is out of scope for this gate.

## Notes

- Thin-first: ship T0 (v0) before any of C1–C8. Each capability is its own branch
  and merges independently; none of them edit root `CLAUDE.md`.
- The console is generic over registries — adding a discovery adapter, platform,
  sensor, or attack plugin must require **zero** UI code changes (C1.1, C7.1 pin
  this).
- Do not rebuild backend that already exists (see **(exists)** above); the real new
  backend work is the handful of **(new)** seams, and `POST /api/discovery/runs`
  (replacing the fixture imprint reader) is critical-path step 1.
- Known limit going in: `GET /api/deploys?source=platform` reconstruction fidelity
  depends on tier-tagged resource names being present; a hand-made resource won't
  appear. Acceptable — record if it bites.
