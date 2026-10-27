# aximprint ↔ axRange integration — work-order set

> **Genre:** cross-repo plan / draft ticket set (not yet ratified). **Authority:** proposal
> (Claude Code), for user review on `wip/aximprint-integration`. Companion to the design canvas in
> this folder (`aximprint-axrange-integration.html`). Two repos are in play — `aximprint`
> (`/home/rocky/aximprint`) and `axRange` (`/home/rocky/axRange`) — so this is a **set/index**, not a
> single axRange ticket. The coupling rule holds throughout: aximprint may import axRange's
> validator / planner / op-catalog; **axRange imports nothing from aximprint.**

## 0. The one thing to hold fixed (before the tracks fan out)

**Pin the axRange twin-tier schema version** the integration targets — `twin_config` + `tfc` +
`tap`. Version-locked on the axRange side (`docs/IR_SCHEMA.md`, `scenario_schema_version`). If
axRange bumps it mid-cycle, Track A re-emits and Track C re-parses. Agree once, record here:

- `scenario_schema_version`: **3** (recognized set `{1,2,3}`).
- provenance carrier: **sidecar** `<name>.provenance.json` (NOT inline — axRange `twin_config` is
  `extra="forbid"`; already handled in aximprint `synth/compiler.py`).
- `tap.sensors` binding: **left unfilled by aximprint** — an `o2`/`tap`-tier fact axRange fills at
  tap-plan apply time.

## Track A — synth → deployable · aximprint · seam ②  ·  **NEAR-COMPLETE**

**State (2026-08-14).** aximprint `docs/WORK_ORDER_synth_v1.md` is **COMPLETE**; phase 3 landed in
its `WORK_ORDER_network_ir.md`. axRange's expander already **accepts the compiled config — 38
networks, 197 nodes, zero warnings**. So the front door is effectively built at the *artifact* level.
- **Remaining:** the **live deploy round-trip** — take a synthesized config all the way through
  `axrange validate → plan → deploy` on a cluster (not just expander-accept). This is **cluster-gated**,
  so it queues with the Phase-3 live-verify rather than being fresh parallel work.
- **No new aximprint ticket cut here** — synth_v1/network_ir already own it, and aximprint has
  in-flight work on `wip/evidence-plane-step2`. The live round-trip is a dev45 verify item, below.

## Track B — ingest breadth · aximprint · seam ①

**Goal.** Extend ingest beyond Zeek-first. Zeek done; Suricata next; packetXpress read side already
covered by `xtract`/`axapi` (`docs/AXAPI_CONTRACT.md`, DONE). Independent of A and C.
- **Done when:** Suricata (and packetXpress) capture normalizes into the same event model
  (`Flow / Identity / Service / Alert / SensorHealth`), determinism check green.
- Owned by aximprint's pipeline; not cut here for the same in-flight-branch reason as A.

## Track C — Console + "deploy as twin" · axRange · UI  ·  **THE PARALLEL GREENFIELD WORK**

**Goal.** Build the operator screen (designed in this canvas) and wire the deploy action. This is the
one net-new track that fans out immediately — see `docs/WORK_ORDER_aximprint_console.md` (the
routable axRange ticket).
- **Depends on:** the §0 pin and the **output shape** only — which already exists (the `twin_config`
  YAML + `provenance.json` sidecar). Build against a fixture belief store until Track A output is on hand.
- **Done when:** the Console renders a real learned imprint (hosts/segments/confidence + per-field
  provenance) and its "deploy as twin" action stands the twin up through the unchanged engine.

## Phase 3 (the serialized tail) — reconcile · seams ③④

Deploy → recapture → a second aximprint → drift & coverage → tune. Needs a real deploy first (the
Track A live round-trip lands here too). Cluster-gated; lands last.

## Sequencing at a glance

```
§0 pin ─┬─► A  synth  (aximprint) ── near-done; live round-trip is cluster-gated ─┐
        ├─► B  ingest (aximprint) ── independent ────────────────────────────────┼─► Phase 3 (③④)
        └─► C  Console (axRange /dev) ── the parallel greenfield build ───────────┘
```
- **Concurrency:** two repos = two independent `/dev` pipelines; within a repo, isolate colliding
  tickets with git worktrees. Track C is where fresh parallel effort actually goes.
- **The real bottleneck** is the gated **dev45 live-verify** — the planner's cross-process lease
  serializes twin deploys and each run needs explicit go-ahead. Code / test / review parallelizes;
  only the on-cluster proof queues.
