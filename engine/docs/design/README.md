*Index and reading order for axRange's current design of record.*

This is axRange's **single** design-of-record set — it describes the system as it is today and
is kept current-only, no build-status blockquotes, no dates, no test/rule/op counts. The former
`docs/DESIGN_BRIEF_*.md` files were a second, parallel design-doc set; they have been retired
(2026-07-27) and their content folded in here — see the mapping table below for where each one's
material now lives. Design history and the reasoning trail (why decisions were made, what
alternatives were considered, how the design evolved, the Authority marker behind each decision)
now live in `docs/CHANGELOG.md`, `docs/LESSONS.md`, and git history — not in a second doc set.
`docs/SLICE_*.md` and `docs/WORK_ORDER_*.md` files remain as-is (implementation-scoped build
records, some still open). Version-locked formal contracts (`docs/IR_SCHEMA.md`,
`docs/PLANNER.md`, `docs/VALIDATOR.md`, `docs/OP_CATALOG.md`, `docs/ADAPTER.md`,
`docs/IMAGE_MANIFEST.md`) remain the authoritative specs implementations must satisfy; the docs
here summarize and cross-link them rather than restate them.

Read in order on a first pass; use as a reference afterward.

1. [01-overview.md](./01-overview.md) — what axRange is, the five planes, the five-tier twin
   model at a glance, and the tech stack.
2. [02-twin-model.md](./02-twin-model.md) — the five tiers (Lab/Twin/Traffic/Tap/Exercise),
   their resource-name tags, the teardown protect/cascade matrix, and the CLI lifecycle verbs.
3. [03-engine.md](./03-engine.md) — the standalone engine: IR → validator → planner → adapter
   → destroy, and its module boundaries.
4. [04-networking-capture.md](./04-networking-capture.md) — SDN networking, routers and
   fabric routing, the tc/gretap capture mechanics, and cloud-init/QGA config delivery.
5. [05-o2-surface.md](./05-o2-surface.md) — the O2 surface layer: watch/act/steer planes,
   Loki/Prometheus/Grafana/Caldera/Wazuh, the `o2control` thin client, and the `o2/` layout.
6. [06-taps-sensors.md](./06-taps-sensors.md) — the typed sensor registry, standing sensors,
   the tap-point vocabulary, tap playout, name-targeted tap plans, and external sinks.
7. [07-traffic-personas.md](./07-traffic-personas.md) — the persona library and drivers, the
   Traffic tier, and host roles.
8. [08-exercise-measurement.md](./08-exercise-measurement.md) — the Exercise tier, dual-mode
   attack, replay via `playout_hash`, and ATT&CK-coverage measurement.
9. [09-content-overlays.md](./09-content-overlays.md) — layered content (infra/apps/activity),
   overlays (identity/data/defense/adversary), twin configs, and the archetype library.
10. [10-images.md](./10-images.md) — the image build pipeline, the two-tier image catalog, and
    guest-agent preinstall.
11. [11-api-ui.md](./11-api-ui.md) — the control-plane API and the CLI verb grid. The operator UI
    is the axRange Console, [16-unified-console.md](./16-unified-console.md); Grafana is telemetry
    only.
12. [12-ai-scenario-research.md](./12-ai-scenario-research.md) — AI scenario research (corpus
    ingestion and Claude-Code-driven scenario design) and the AI-core roles.
13. [13-golden-twin-fidelity.md](./13-golden-twin-fidelity.md) — golden-twin fidelity: the
    eleven-row specification (observation channels, evidence classes, experiment weighting),
    the reference-ingest channel, and the imprint → certify → drift lifecycle. Ratified
    2026-09-03; supersedes the fidelity loop's convergence objective.
14. [14-operator-assertions.md](./14-operator-assertions.md) — operator assertions and the canvas
    loop: the six-class evidence model with its precedence order (`asserted` / `imported` /
    `observed` / `referenced` / `imputed` / `compiler`), the assertions file as the contract, and
    the Console canvas as its author. Ratified 2026-09-04; supersedes 13's three-class sentence.
15. [15-console-canvas.md](./15-console-canvas.md) — the Design area's assertions canvas: three
    semantic-zoom levels, a deterministic prefix-grouped layout with optional pins, edges as
    bundles and a matrix, and edits as assertion records with save / apply / refresh; plus the API
    additions it needs. Ratified 2026-09-04; built. Since 2026-09-29 this is one area
    (`/design/...`) inside the one Console, not a separate app — see
    [16-unified-console.md](./16-unified-console.md).
16. [16-unified-console.md](./16-unified-console.md) — the axRange Console: run-time-first areas
    (Range / Twin / Catalog / Design, plus Telemetry link-outs), the eight screen rules, the one
    deployed-twin model, per-host persona apply, protected deploys, the plan-fingerprint pin,
    several stores per target, and the URL grammar. Ratified 2026-09-29; supersedes single-pane
    Grafana, "viewer not authoring", the Console's plugin-independence framing, and the plugin
    keeping "Deploy as twin" as its own surface. **Built and live-verified 2026-09-30**
    (`docs/archive/work_orders/WORK_ORDER_unified_console.built.md`).

## Retired-brief mapping

The 19 `docs/DESIGN_BRIEF_*.md` files are deleted. Anything still citing one by name — including
historical `docs/CHANGELOG.md` entries, which are never rewritten — can use this table to find
where that material lives now.

| Former brief | Now lives in |
|---|---|
| `DESIGN_BRIEF_adversary_overlays.md` | [09-content-overlays.md](./09-content-overlays.md) |
| `DESIGN_BRIEF_ai_scenario_research.md` | [12-ai-scenario-research.md](./12-ai-scenario-research.md) |
| `DESIGN_BRIEF_api.md` | [11-api-ui.md](./11-api-ui.md) |
| `DESIGN_BRIEF_data_overlays.md` | [09-content-overlays.md](./09-content-overlays.md) |
| `DESIGN_BRIEF_defense_overlays.md` | [09-content-overlays.md](./09-content-overlays.md) |
| `DESIGN_BRIEF_dynamic_tap.md` | [06-taps-sensors.md](./06-taps-sensors.md) |
| `DESIGN_BRIEF_fabric.md` | [04-networking-capture.md](./04-networking-capture.md) |
| `DESIGN_BRIEF_identity_overlays.md` | [09-content-overlays.md](./09-content-overlays.md) |
| `DESIGN_BRIEF_o2.md` | [05-o2-surface.md](./05-o2-surface.md) |
| `DESIGN_BRIEF_o2_attack_defense.md` | [08-exercise-measurement.md](./08-exercise-measurement.md) |
| `DESIGN_BRIEF_o2_control.md` | [05-o2-surface.md](./05-o2-surface.md) |
| `DESIGN_BRIEF_o2_measurement.md` | [08-exercise-measurement.md](./08-exercise-measurement.md) |
| `DESIGN_BRIEF_o2_taps.md` | [06-taps-sensors.md](./06-taps-sensors.md) |
| `DESIGN_BRIEF_production_topology.md` | [05-o2-surface.md](./05-o2-surface.md) ("Production topology") — Tier-2 ("appliance data plane") was dropped as dead, superseded by the standing-sensors model |
| `DESIGN_BRIEF_sdn_capture.md` | [04-networking-capture.md](./04-networking-capture.md) |
| `DESIGN_BRIEF_sensors.md` | [06-taps-sensors.md](./06-taps-sensors.md) |
| `DESIGN_BRIEF_tap_scopes.md` | [06-taps-sensors.md](./06-taps-sensors.md) |
| `DESIGN_BRIEF_twin_model.md` | [02-twin-model.md](./02-twin-model.md) |
| `DESIGN_BRIEF_ui.md` | [11-api-ui.md](./11-api-ui.md) |
