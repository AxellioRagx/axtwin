# Work Order — aximprint learned-twin Console + "deploy as twin" (integration Track C)

> **STATUS: BUILT — code/UI/tests/review-fixes complete; gated `dev45` live-verify PENDING.**
> Authority: implementation (Claude Code) for the two builds below; the original design-canvas
> proposal was Authority: proposal (Claude Code), for user review — acted on, not separately
> ratified as a whole. All three §2 scope items are done: **read + provenance** (slice 1, CHANGELOG
> 2026-08-25 "…Track C slice 1", committed `ed79cdc`) and **deploy action** + **UI** (slices 2+3,
> PART A/PART B, CHANGELOG 2026-08-25 "…Track C slices 2+3", uncommitted on
> `wip/aximprint-integration`) are all unit/build-verified (`pytest` 1918 collected, 0 failed;
> plugin `npm run build`/`test:ci` clean). **The one remaining §4 "Done when" gate — a real learned
> imprint rendering in the Console AND its "deploy as twin" action standing a twin up, verified on
> `dev45` behind the standard gated live-verify — has NOT run** ("deploy as twin" creates real VMs,
> so this is deliberately held for the gate, not skipped). Route: Pipeline (`/dev`) — code-writer →
> tester → code-reviewer → doc-writer → **gated live-verify (the one remaining step)**. **Not a
> version-locked change** on the axRange side — it consumes the existing `te`/`tfc`/`tap` deploy path
> and reads aximprint output; no IR/OP_CATALOG/VALIDATOR/ADAPTER *schema* change; op count (40) and
> rule count (114) unchanged. **Design:** the canvas + set at `docs/design/aximprint-integration/`
> (`WORK_ORDERS.md` Track C, the "O2 Console" + "Rollout" artboards). **Companion tracks** (aximprint
> repo, not this one): A synth→deployable (near-done), B ingest breadth.
>
> **Known residuals (deliberately left during the slices 2+3 review round, not bugs):**
> (1) the Console's plan-preview shows an "✗ invalid" label when the only issue is an unset
> `DEFAULT_NODE` (a target-node-selection gap, not a genuinely invalid twin_config) — cosmetic,
> matches existing `config`-sourced plan-preview behavior. (2) "Deploy as twin" is gated on
> provenance-sidecar validity even though provenance is read-only metadata never consumed by the
> deploy path itself — defensible ("an imprint is one unit"), not slated for a fix.

## 1. Goal

Give the O2 Console an operator screen that turns a **learned imprint** into a deployed twin:
browse the belief store, read per-field **confidence + provenance**, see the drafted
`twin_config`/`tfc`/`tap` validate, and **"deploy as twin"** through the unchanged engine. This is
the one net-new, greenfield track of the integration and the one that parallelizes freely — it
reads an output shape that **already exists**.

## 2. Scope (axRange side only)

- **UI** — `o2/grafana/plugins/` (the `axellio-axrange-control-panel` plugin): the imprint browser,
  the stat tiles, the learned-topology preview (confidence-shaded), the drafted-config cards, and
  the provenance table. Designed already — see the "O2 Console" artboard.
- **Read + provenance** — `axrange/api/`: an endpoint that reads a belief store's summary
  (hosts/segments/services/confidence) and the per-field provenance **sidecar**
  (`<name>.provenance.json`; provenance is a sidecar because `twin_config` is `extra="forbid"`).
- **Deploy action** — a "deploy as twin" call that hands the three drafted docs to the existing
  `te`/`tfc`/`tap` deploy verbs. No new op type; no new tier.

**Status of each item, 2026-08-25:** Read + provenance — BUILT (slice 1). UI — BUILT (slices 2+3,
PART B; only `twin_config`-shaped drafts render/deploy today — the "three drafted docs" phrasing
above envisions `tfc`/`tap` drafts too, not yet built). Deploy action — BUILT (slices 2+3, PART A;
currently hands off only the `twin_config` to `te`-class deploy, via the existing `imprint` source
on `create_deploy` — not yet a combined `te`+`tfc`+`tap` multi-verb handoff).

## 3. Depends on / parallel-safe

- **Depends only on the §0 schema pin** (`scenario_schema_version 3`) and the **output shape** —
  both already fixed. Build against a **fixture belief store** (the canvas mockup's sample data is
  essentially that fixture) until Track A's real output is on hand; never blocks on Track A finishing.
- **Coupling rule:** axRange must **not** import anything from aximprint. The Console reads
  aximprint's emitted **artifacts** (YAML + provenance JSON), not its code.

## 4. Done when

The Console renders a real learned imprint (hosts/segments/confidence + per-field provenance) and
its "deploy as twin" action stands the twin up through the unchanged engine, verified on dev45
behind the standard gated live-verify.
