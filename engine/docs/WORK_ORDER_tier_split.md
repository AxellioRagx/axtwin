# Work Order — Tier split: add `tfc` (traffic) + `exr` (exercise) tiers + twin inventory

> **STATUS: DONE/BUILT (2026-07-23).** Landed via the subagent Pipeline (`/dev`):
> code-writer → tester → code-reviewer → doc-writer, all stages complete. **Spec:**
> `docs/DESIGN_BRIEF_twin_model.md` (§2, §4, §8; built-status header added at the top). No live-verify
> gate needed (naming/guard/inventory only; no new cluster op) — full suite **1056 passed, 12
> skipped, 0 xfailed**. See `docs/CHANGELOG.md` 2026-07-23 "Tier split KEYSTONE" for the full build
> record. **This was the KEYSTONE** — `persona_library`, `tap_points`, `twin_configs`,
> `traffic_tier`, `exercise_tier` now unblock and build on top of this substrate (`naming.py`'s
> `TIER_PREDICATES`/`TIER_SHAPE_CHECKS`, `planner/teardown.py`'s six-tier matrix, `axrange te
> inventory` / `GET /api/deploys/{id}/inventory`).

## 1. Goal

Extend the tier machinery from four tags to six, and give the twin a **published inventory** the new
instrumentation tiers bind against. No behavior moves yet — this is the substrate the other five orders
land on.

## 2. Build

- **`adapter_proxmox/naming.py`:** add tier tags **`tfc`** (traffic) and **`exr`** (exercise) to the
  tier-tag set alongside `te`/`tap`/`o2`/`img`. Follow the exact pattern `o2`/`tap` use — a
  deploy-scoped `tier` param already threads through `naming.py` producers; the new tags need name
  shapes (`tfc<6hex>` / `exr<6hex>` zones, `tfcv*`/`exrv*` vnets if any, `tfc-*`/`exr-*` VM names) and
  `is_tfc`/`is_exr` predicates.
- **`planner/teardown.py`:** extend `_protected()` so the guard is correct for all six tiers per the
  brief §2.1 matrix. Specifically: an `exr` destroy touches only `exr`; a `tfc` destroy touches only
  `tfc`; a `tap` destroy touches only `tap`; a `te` destroy cascades to its `tfc`/`tap`/`exr` **but
  still can never touch `o2`/`img`**. Each new tag is a **positive name-shape gate on the VM path too**
  (LES-088 — fail *closed*, not just the range/`_protected` pair).
- **`state.py`:** the `deploys.tier` column already exists (SCHEMA_VERSION 3); confirm `tfc`/`exr` are
  accepted values and `latest_deploy_for_tier`/`deploys_by_tier` work for them. **No schema bump** if
  the column is already free-form; if it's constrained, bump + guard per house rule.
- **Twin inventory (generalize `OP-MIR-005 record-tap-intent`):** the `te` deploy already emits a
  declarative tappable set. Extend the persisted output to the brief §4 `twin_inventory` shape —
  `hosts[]` (role, node, segment, ip, os — the persona-assignable roster) **and** `tap_points[]`
  (id, direction EW|NS, location, iface). Expose it via a read path (`te info --inventory --json` or a
  new `axrange te inventory <deploy>`), since `tfc`/`tap` apply will consume it.

## 3. Boundaries

Naming/guard/state/inventory only. Do **not** move attack/activity out of `te` yet (that's
`exercise_tier`), and do **not** add traffic/tap logic (those orders). Keep `o2control` and the
existing `tap-<te>` playout working unchanged.

## 4. Validation

- Unit: `tfc`/`exr` name shapes + predicates; `_protected()` matrix for all six tiers incl. the VM
  path (assert an `exr` destroy leaves `tfc`/`tap`/`te`/`o2`/`img` untouched, and vice-versa);
  inventory output shape (hosts + tap_points) round-trips through state. Full suite green.
- No cluster run required.

## 5. Depends on / sequence

Depends on the shipped `o2`/`tap` tier machinery. **Blocks** `persona_library`, `traffic_tier`,
`tap_points`, `twin_configs`, `exercise_tier`. Docs: `DESIGN_BRIEF_twin_model.md` (mark
substrate built), `CONVENTIONS.md` §2 (add the two tags to the table), CHANGELOG, LES if warranted.
