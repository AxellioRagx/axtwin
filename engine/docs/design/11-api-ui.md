*The control-plane API and the CLI verb grid — the surfaces an operator or another program uses to
drive axRange. The operator UI is the axRange Console (`docs/design/16-unified-console.md`); Grafana
is telemetry only.*

## The control-plane API

`axrange/api/` is a UI-agnostic **control plane** — a FastAPI service (`axrange serve`) sitting
over the engine exactly the way `axrange/cli/` does: it calls the same library entry points the
CLI calls, never a shelled-out copy of the CLI, and it never leaks engine internals upward. Its
contract is a set of clean, purpose-shaped DTOs (`axrange/api/dtos.py`) — raw IR objects and raw
Proxmox/adapter shapes never cross the boundary.

`axrange serve` runs **headless** (no UI attached — the AI core, CI, and scripts consume it that
way) and can also co-serve a single-page app if one is present. Either way the API is the same
surface: the console described below is its first consumer, not its owner. The AI core
(see [12-ai-scenario-research.md](./12-ai-scenario-research.md)) and CI/automation are later
consumers of the identical endpoints.

## The endpoint surface

The API mirrors the engine's own shape: read/observe endpoints over scenarios, topology, and
deploy state; a validate/plan pair with no side effects; an async, lease-guarded deploy plus a
guarded destroy; and one GET/POST/DELETE trio per standing-surface or playout tier.

| Area | Endpoints | Notes |
|---|---|---|
| Health / catalog | `GET /api/health`, `GET /api/scenarios` | liveness + the known scenario catalog (scenarios only — twin-configs have their own endpoints, below) |
| Twin configs | `GET /api/twin-configs`, `GET /api/twin-configs/{config_id}` | the parameterized-IR-template catalog: a summary list and a per-config detail (params, segments, nodes, tap points, identity/data summary) |
| Topology + inspector | `GET /api/scenarios/{id}/topology`, `GET /api/scenarios/{id}/nodes/{node}` | the bipartite host↔network graph DTO; a per-node detail DTO merging config, state-store, and (on request) live hypervisor/guest facts. Both resolve `{id}` against scenario → twin-config → imprint in that order (the "own endpoints, below" twin-config/imprint catalogs are read-time sources too, not just deploy-time ones); `?source=scenario\|twin-config\|imprint` pins resolution to exactly one, since a cross-catalog id collision otherwise silently resolves whichever is tried first (`docs/WORK_ORDER_console_selector_and_stale_deploys.md` M1, 2026-08-27) |
| Topology export | `GET /api/scenarios/{id}/topology-export?source=twin-config\|imprint` | the aximprint cast-topology-export artifact (`<id>.topology.json`, shipped beside the twin-config/imprint it casts from) — segments/edges (the conversation graph) + the full observed node census. 404 (not an error) when no such file exists, the ordinary case for content never cast from a capture. Added 2026-08-27, `docs/CHANGELOG.md` |
| Validate / plan | `POST /api/validate`, `POST /api/plan` | pure — findings and a DAG summary, no cluster mutation. `scenario`-sourced only today; a twin-config/imprint id (unlike `POST /api/deploys`, below) is not yet accepted here |
| Twin lifecycle | `GET /api/deploys`, `GET /api/deploys/{id}`, `POST /api/deploys`, `DELETE /api/deploys/{id}` | the op-tree DTO; deploy is async and lease-guarded (below); destroy requires an explicit confirm flag, and is refused `423` on a protected deploy without `override_protection` (see Auth posture). `POST /api/deploys` accepts `scenario`, `config`+`params`, or `imprint`+`params` (mutually exclusive — a twin-config/imprint id expanded through the same catalog as `te deploy --config`), an optional `expect_plan_id` pin, and its response carries `config_warnings` (expansion-time advisories, always empty on the `scenario` path). A target with several state stores (below) resolves an existing deploy in whichever store holds it — a write against it goes there, never the primary by default. `GET /api/deploys` defaults to excluding `destroyed` deploys (`?status=` to filter explicitly, or `?status=all` for full history — `docs/WORK_ORDER_console_selector_and_stale_deploys.md`, 2026-08-27); `GET /api/deploys/latest` is deliberately unfiltered (the newest row regardless of status, matching the CLI's own bare-default posture) |
| Twin inventory | `GET /api/deploys/{id}/inventory` | a deployed twin's published binding contract — its host roster (placement, role, OS, nets); the same contract `te inventory` prints. `GET /api/deploys/{id}/hosts` (above) joins this same roster with tfc personas and tap bindings in one call for the Console |
| Twin access facts | `GET /api/deploys/{id}/access` | the per-node "how do I get in" sheet `te info` shows — ip/user/effective password/connection hint; passwords are masked by default, `?secrets=true` reveals them (see Auth posture) |
| Twin fidelity | `GET /api/deploys/{id}/scorecard?probe=&include_deferred=&reference=` | the fidelity scorecard `te scorecard` prints; `probe=true` writes a probe script into every guest |
| Lab surface | `GET /api/o2/surface`, `POST`/`DELETE` on the same path | stand up, inspect, and tear down the standing O2 surface |
| O2 endpoint resolution | `GET /api/o2/info` | resolved observability endpoint URLs (Grafana/Loki/Prometheus/Caldera/Wazuh) and external-sensor facts; same masked-by-default/`?secrets=true` posture as the access-facts endpoint |
| Tap playout | `GET /api/o2/taps` (concise + `?verbose=`), `POST`/`DELETE /api/o2/taps` | bind a deployed twin's taps onto standing sensors; the GET serves both the CLI's `tap status`/`tap info` and the Console's Twin area |
| Traffic playout | `GET /api/tfc`, `POST`/`DELETE` | bind persona traffic onto a deployed twin's host roles; `POST` gains `allow_revert_host_edits` (see the per-host apply below) |
| Exercise playout | `GET /api/exr`, `POST`/`DELETE` | run and tear down an attack/defense/measurement exercise against a deployed twin |
| Targets / range / pcaps | `GET /api/targets`, `GET /api/range`, `GET /api/pcaps` | every configured cluster target with live reachability; the Console's one home-page read (every target's standing twins, siblings, host/persona counts, and `unrecorded` — axRange-named VMs no attached store records); capture-file listing (deferred: download). A target's `stores: [{label, path, primary}]` lists every state store it reads (2026-09-30: `extra_dbs:`/`--extra-db`, all opened read-only, never `immutable=1` — LES-359); a deploy id in two stores of one target answers `409 ambiguous-store` rather than picking one |
| Twin hosts / per-host persona apply | `GET /api/deploys/{id}/hosts`, `GET /api/deploys/{id}/health`, `GET /api/personas`, `POST .../personas/plan`, `PUT .../personas`, `GET .../tfc/profile` | the Console Twin area's one model (recorded host+persona+tap roster), on-demand live health, the persona picker, and a scoped one-host persona apply that never touches another host's traffic. `GET /api/deploys/{id}` `host_edits[]` keeps every past job (append-only `host_edit_history`; a superseded job is marked, not dropped) — full contract in `docs/reference/ui-consolidation/04-api-contract.md` and semantics in `docs/design/16-unified-console.md` §5 |

Each tier's GET/POST/DELETE trio is the API mirror of that tier's CLI verbs
(see [02-twin-model.md](./02-twin-model.md) for the tier model those verbs operate on) — a UI or
script never has to shell out to the CLI to drive a playout.

## DTO discipline

Every response is purpose-shaped JSON rather than a pass-through of an internal object:

- **Topology DTO** — one node per IR node (kind, image, requirements) plus one per network, with
  edges from each node's network attachments; it optionally carries a live op-status overlay
  joined from the state store, so the same graph doubles as a deploy monitor.
- **Node-detail DTO** — a layered merge, cheapest source first: declared config (IR + manifest),
  then state-store facts (placement, whether an op actually applied), then — only when a caller
  asks for the live tier (`?live=true`) — hypervisor facts over the Proxmox API and in-guest facts
  over the qemu-guest-agent channel the engine already uses for config delivery. No separate
  per-node agent is meant to exist: QGA is the thin per-node API. **Current limitation:** the first
  two (config + state-store) tiers are implemented; the live tier is designed but not yet wired —
  `?live=true` returns an explicit "not implemented in this slice" marker rather than silently
  omitting the fields. The access-facts endpoint (`/api/deploys/{id}/access`) independently
  resolves some of the same live facts (effective password, reachable IP) through a separate
  best-effort resolver, so an operator already has a live-facts path for the twin-lifecycle case
  even though the generic node-detail live tier is still open. `GET /api/deploys/{id}/health`
  (2026-09-29) adds a second, deploy-scoped live path — one cluster sweep for every host's VM power
  state, plus per-host QGA reachability and persona heartbeats on request — without waiting on the
  generic per-node live tier; see `docs/design/16-unified-console.md` §4/§6.
- **Deploy-detail DTO** — the same op-tree `te status` prints, as JSON.
- **Findings DTO** — rule id, severity, location, and a templated message per validator finding.

Consumers bind to these DTOs, not to the state-store schema — which keeps the API stable even as
the engine's internal storage evolves.

## Async deploy model

A deploy is long-running, so `POST /api/deploys` returns a `deploy_id` immediately and the work
continues in the background, riding the same per-scenario **lease** the CLI's detached-deploy
path already provides:

1. the request kicks a deploy and returns its `deploy_id`;
2. the deploy takes the scenario's lease, so a second concurrent deploy of the same twin is
   refused rather than racing;
3. a caller polls `GET /api/deploys/{id}` (the state store is authoritative) to watch it
   progress, or reads deploy op status directly.

Destroy follows the same shape: `DELETE /api/deploys/{id}` requires an explicit confirmation
flag in the request body and drives the same reverse-of-create teardown the CLI's `destroy`
verb runs, including its cascade into a twin's tap/traffic/exercise deploys
(see [02-twin-model.md](./02-twin-model.md)).

## Auth posture

Reads are open on a trusted local bind; writes require a token. The default bind is loopback,
with remote exposure an explicit opt-in that should carry a token. The Console holds its write
token in the browser's memory only, for the life of the tab — never in `localStorage`, a cookie, or
the compiled bundle, and it is supplied at runtime, never baked in.

Secrets-bearing reads sit in between: the access-facts and O2-info endpoints default to masked
passwords, which any reader may fetch — the same open-reads posture as everything else. Only the
`?secrets=true` reveal is gated: it is refused with a 401 unless a bearer token is configured and
supplied, so an unauthenticated caller can never pull a real password off a token-protected
deployment. Masking, not the token check, is the default-safe posture — the gate only ever
narrows access further, never widens it.

**Protected deploys (2026-09-30).** A target's `protected_deploys` list (or `AXRANGE_PROTECTED_DEPLOYS`)
names deploy ids no write route may touch — destroy, resume/update, any tap/tfc/exr playout, any
persona apply — without the request repeating the id back as `override_protection=<id>`; an
unauthorized attempt answers `423`, never a silent no-op. **Plan pin (GAP-098, closed).**
`POST /api/plan`/`POST /api/validate` (config/imprint + `params`, not only a scenario) return a
`plan_id`/`fingerprint`; `POST /api/deploys`, `POST /api/imprints/{id}/deploy`, and the per-host
persona `PUT` all accept the matching `expect_plan_id`/`expect_plan` and refuse `409 plan-changed`
— with the plan the server would now run attached — rather than silently re-planning against a
bundle that moved under the preview. Full contract:
`docs/reference/ui-consolidation/04-api-contract.md`; semantics: `docs/design/16-unified-console.md`
§5.

## The CLI verb grid

The CLI is the same control surface as the API, expressed as a uniform grid rather than
endpoints. Four tiers share one verb set:

```
axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}
```

- `validate`/`plan`/`deploy` take a scenario or spec file as a positional argument (or a
  `--config <id>` twin-config reference in place of one, expanded through the twin-config
  catalog described in [02-twin-model.md](./02-twin-model.md) / [09-content-overlays.md](./09-content-overlays.md)).
- `status`/`info`/`destroy` default to the latest deploy of that tier when no id is given.
- `te` additionally exposes `inventory [ID] [--json]` — a deployed twin's published binding
  contract (its host roster and tap points), the same contract the API's
  `/api/deploys/{id}/inventory` endpoint returns.

The **Exercise** tier (`exr`) uses a deliberately narrower verb set — `deploy`/`run` (aliases of
each other), `status`, `destroy` — since it binds a spec to an already-deployed twin rather than
validating and planning infrastructure of its own (see [08-exercise-measurement.md](./08-exercise-measurement.md)).

Outside the grid: `list-deploys [--tier {te,o2,tap,tfc,exr}]` (a cross-tier recent-deploys view),
`serve` (starts the control-plane API), `image build` (the image pipeline,
[10-images.md](./10-images.md)), and `twin-config list|show` (the parameterized-IR-template
catalog, [02-twin-model.md](./02-twin-model.md)).

### `info` — access facts, not just config

Every tier's `info` verb shows the resolved configuration for a deploy, and `te info` goes
further: it is a self-service "how do I get in" sheet. For each node it shows the reachable IP,
the login user, the effective password (masked with `--no-secrets`, always present in `--json`),
and a paste-ready `ssh`/`rdp` connection hint. `o2 info` resolves the standing services VM's
live IP into the full set of observability endpoint URLs (Grafana, Loki, Prometheus, Caldera,
the Wazuh manager) rather than requiring an operator to hand-copy them, and the same resolution
path answers `tap info` for a config-declared external sensor's live name and management IP
(see [06-taps-sensors.md](./06-taps-sensors.md) and [05-o2-surface.md](./05-o2-surface.md)).

## The UI: the axRange Console

The operator-facing UI is one bespoke application, the **axRange Console** (`o2/console/`, served by
`axrange serve --spa-dir`) — full design in [16-unified-console.md](./16-unified-console.md). It views,
controls, **and** edits: it reads and drives already-deployed twins (Range and Twin areas), guards
deploy/destroy (Catalog), and authors design-time content (the Design area, the former "Console
canvas" of [15-console-canvas.md](./15-console-canvas.md), unchanged in function, relabelled and
re-rooted at `/design`). It is not a Grafana panel and does not go through Grafana's datasource proxy
— every write carries its own bearer token, held in the browser's memory only, never in Grafana's
datasource config.

**Grafana is telemetry only** (2026-09-29; supersedes the single-pane-Grafana ruling below): three
provisioned dashboards — capture health, Suricata alerts, attack coverage — over Loki/Prometheus
(see [05-o2-surface.md](./05-o2-surface.md)), linked from the Console's Telemetry area. Grafana no
longer hosts a control-panel plugin, a topology renderer, or a deploy/destroy action; the
`axellio-axrange-control-panel` plugin and the deploy/op-status, pcap-browser and control-console
dashboards were deprecated in place 2026-09-29 and deleted 2026-10-01
(`docs/reference/ui-consolidation/05-deletion-candidates.md`).

**Prior shape (historical, kept for the record).** Before 2026-09-29 the operator UI was a
single-pane Grafana console — provisioned dashboards plus one custom React panel plugin
(`axellio-axrange-control-panel`) calling the control-plane API through Grafana's datasource proxy.
It reached browser-verified topology rendering with a live op-status overlay, a node inspector, and
Validate → Plan → Deploy → Destroy, but never grew past the Twin (`te`) tier's own lifecycle: the
Tap/Traffic/Exercise playouts, the twin-config catalog, the twin inventory contract, and the
access-facts sheet had no panel affordance, and its "Run scenario" button called a stub that always
reported "not implemented". A separate, richer SPA (the Console canvas) grew alongside it as a
design-time-only editor. See `docs/reference/ui-consolidation/03-ui-history-and-decisions.md` for the
seven-surface history and why it did not converge.

## Where this fits

- [02-twin-model.md](./02-twin-model.md) — the tiers and lifecycle verbs the CLI grid and the
  API's per-tier endpoints both operate on.
- [03-engine.md](./03-engine.md) — the validator/planner/adapter functions the API and CLI both
  call into.
- [16-unified-console.md](./16-unified-console.md) — the axRange Console, this API's UI consumer.
- [05-o2-surface.md](./05-o2-surface.md) — the Grafana instance the Telemetry area links to.
- [12-ai-scenario-research.md](./12-ai-scenario-research.md) — the scenario-authoring path outside
  the Console (Claude / Claude Code producing Scenario IR), gated through the same validate/plan
  pipeline every other API consumer goes through.

For the design reasoning trail behind the API/UI split, see `docs/CHANGELOG.md` and
`docs/design/README.md`'s retired-brief mapping table.
