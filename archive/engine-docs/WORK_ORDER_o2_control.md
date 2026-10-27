# Work Order — A1: the O2 control component (`axrange/o2control/`)

> **STATUS: DONE (build) — 2026-07-11. §5 gated `dev45` live-verify PASSED — 2026-07-15.**
> Implemented + code-reviewed + suite-verified (794 pytest tests at build time, 792 current).
> `axrange/o2control/` reuses the engine as a spec-executor; `tier` threaded through
> plan/deploy/naming/state/executor/teardown (`state.py` SCHEMA_VERSION 2→3 — re-initialize any
> pre-2026-07-11 `state.db`); CLI `axrange o2 up|status|down`; API `/api/o2/surface`. **Live-verify
> evidence (dev45, PVE 9.2, 2026-07-15):** `axrange o2 up` stood up surface deploy
> `o2-surface-verify-0f9b5169` (2 sensors, `o2-tcpdump`@10.255.251.10 + `o2-suricata`@10.255.251.11,
> transport IPs exposed via `o2 status`); an unrelated blast-radius TE
> (`o2-blast-check-8776ca1c`, vmid 218997) was deployed then destroyed (4 resources removed) leaving
> the `o2_` sensor VMs (135913, 630182) + all `img-*` templates untouched, no `guard-violation`. See
> CHANGELOG 2026-07-15.

> **For a subagent Pipeline session (`/dev`).** Authority: user (2026-07-10). **Spec:**
> `docs/DESIGN_BRIEF_o2_control.md` (read it — this is the *how-to-build*, that is the *what*).
> Route: code-writer → tester → code-reviewer → doc-writer → **gated** live-verifier.
> **KEYSTONE — build this first; A2/A3-servers depend on it.**

## 1. Goal

Build the component that **stands up the standing O2 surface** by **reusing the engine as a
spec-executor** (not a second orchestrator): compose a surface spec → run it through
planner+adapter+state tagged `o2_` → a **persistent deploy** in `state.db` → expose **inventory** →
explicit teardown. Scope = A1 §2 (compose / stand up / inventory / teardown). Out of scope: A2 taps,
A3 playout.

## 2. Prerequisite — tier as a deploy parameter

`naming.py` currently emits `te_` for every deploy. **Thread a `tier` through plan/deploy → naming**
(default `te_`; `o2control` passes `o2_`) so the reused executor emits `o2v*`/`o2br_*`/`o2-*` names
(`docs/DESIGN_BRIEF_o2_control.md` §4; refines `WORK_ORDER_resource_tags.md`). Small, but do it first.

## 3. Build

- **`axrange/o2control/`** (new submodule; engine-consuming, stays in the `axrange/` package):
  - load a **surface manifest** — reuse the ratified `sensors:` IR shape + a transport stanza (F3).
  - compose a "surface deploy" spec (sensors as nodes + transport subnet) and run it via the engine
    executor with `tier="o2"` → an `o2_`-tagged **persistent** deploy row (never auto-destroyed).
  - **inventory:** `{sensor_id → type, transport_ip, deploy_id}` (the A2/A3 discovery source).
  - teardown: run the engine teardown against the `o2_` deploy_id.
- **Thin surfaces:** CLI `axrange o2 up [--surface FILE] | status | down`; API `POST/GET/DELETE
  /api/o2/surface` (clean DTOs only). Mirror the `config_exec`/`api` pattern — logic in `o2control`,
  transports thin. Reuse the shared plan-build/deploy helper (LES-037).

## 4. Boundaries

`o2control` imports `planner`/`adapter_proxmox`/state/`sensors` **downward only**; the engine never
imports `o2control` (LES-025). All created resources carry `o2_` (protected from every `te_`/`tap_`
destroy by `teardown._protected()`).

## 5. Validation

- Unit tests: surface-spec compose; `o2_` tagging via the tier param; inventory shape; idempotent
  re-`up` (Model-C adopt); `down` removes only the `o2_` deploy. Full suite green.
- **Gated live-verify (dev45):** `axrange o2 up` a 1-sensor surface → confirm `o2_`-named resources +
  a persistent deploy + inventory has the transport IP → deploy an unrelated TE, `destroy` it →
  confirm the `o2_` surface is **untouched** → `axrange o2 down` clean.

## 6. Depends on / sequence

Depends on the shipped `o2_` tier naming. **Blocks A2** (inventory) and **A3 servers** (stood up here).
Docs: update `DESIGN_BRIEF_o2_control.md` (mark built), `ADAPTER`/`CLAUDE.md` as needed, CHANGELOG, LES.
