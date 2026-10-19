# WORK ORDER — Predefinable tap plans: optional `target`, `--target` override, name resolution

> **Status: DONE — built + reviewed 2026-07-25 (CHANGELOG 2026-07-25 "Predefinable
> tap plans"). Authority: user.** Genre: Pipeline work order (`docs/CONVENTIONS.md`).
> Goal: make tap plans **predefinable** (reusable, not welded to a minted deploy
> id) by (A) letting `target:` resolve a scenario/twin-config **name** to the
> latest live `te` deploy, and (B) a `--target` CLI override so a library plan
> can omit `target:` entirely. Partially addresses `docs/PRODUCT_GAPS.md`
> GAP-030 (name-based, not yet full label-based, selection). **Reviewed:** one
> must-fix (the `exclude_plan_id_prefix="tap-"` over-exclusion, LES-132) + two
> minor findings, all fixed. **Tested:** full suite green — 1618 passed / 12
> skipped / 0 xfailed, 1630 collected. **Dev45 read-only live-verify PASSED
> 2026-07-25** — all 8 sub-checks green against the standing `corp-enterprise`
> twin (scenario-name resolution, `--target` override, no-target error, both
> `tap validate` modes, and the two must-fix guardrails: a tap-plan-playout id
> and a destroyed deploy both rejected with the exact clear messages). No
> mutation run (`tap validate`/`tap plan` only); cluster untouched. The one
> case not exercised live — a genuine `te` *named* `tap-*` resolving by name —
> has no matching twin on dev45, unit-verified only.

## Motivation

A tap plan's `target:` is currently a **required literal deploy id**
(`taps.py` `store.get_deploy(spec.target)`), minted per deploy — so a plan
file is welded to one deploy instance and can't be predefined the way
`twin-configs/` are. The reusable part (the `bindings`/`selector` topology
addresses) is already deploy-agnostic; only `target:` is instance-specific.

## Scope / guardrails

- **No version-locked schema change** (IR/PLANNER/OP_CATALOG/VALIDATOR/
  ADAPTER/IMAGE_MANIFEST) and **no new op type**. The tap-plan YAML is a
  `DESIGN_BRIEF_o2_taps.md` document, not a version-locked schema. If the
  build finds it can't avoid a version-locked change, STOP and ask.
- Keep the CLI thin; resolution logic lives in `axrange/o2control/taps.py`
  (and reuses `StateStore` discovery already there).
- Honor LES-082 (tap-plan deploys are `tier="te"` + `tap-` plan_id — exclude
  them, structurally, when resolving "the te deploy of a scenario") and
  LES-084 (`updated_at` ordering; "live/active" = non-destroyed).

## A — `target:` becomes optional + name-resolvable

1. **`parse_tap_plan` (`taps.py`): `target` is now OPTIONAL** — drop the hard
   "target is required" raise; allow `spec.target = None`. (`TapPlanSpec.target`
   becomes `Optional[str]`.)
2. **New resolver** `resolve_target_deploy(store, target: str) -> str` (raises
   `TapPlanError` with a clear message on miss):
   - If `store.get_deploy(target)` exists → it's a deploy id; use it.
   - Else treat `target` as a **scenario_id** → the newest **live
     (non-destroyed) `te`** deploy of that scenario: `deploys_matching(
     tier="te", exclude_destroyed=True)` filtered FIRST by `scenario_id ==
     target`, THEN by `is_tap_plan_deploy` NOT matching (structural,
     LES-082), newest first (`updated_at`, LES-084). First hit wins.
     **Correction (code review, MUST-FIX):** an earlier draft of this
     prescription ALSO added `exclude_plan_id_prefix="tap-"` to the
     `deploys_matching` call, mirroring `list-deploys --tier te`'s own
     query — this is WRONG here and was dropped. `list-deploys --tier te`
     wants "every genuine te, EXCLUDING tap-plan playouts" and the
     prefix-exclude is a cheap, deliberately-imperfect first pass for that
     (a real te scenario named `tap-*` is a rare, accepted false-negative
     there). This resolver wants something narrower and different — "the
     te deploy whose `scenario_id` equals this exact name" — so filtering
     out every `tap-%` plan_id BEFORE checking `scenario_id == target`
     wrongly 404s a genuine, live te twin whose scenario happens to be
     named `tap-*` (`derive_plan_id` mints `tap-<name>-<hash>` for it), and
     makes the structural `is_tap_plan_deploy` check that follows
     unreachable dead code (nothing with a `tap-` plan_id survives the SQL
     filter to reach it). Filter to `scenario_id == target` on the FULL
     `tier="te"` candidate set, and let `is_tap_plan_deploy` alone decide
     "is this actually a tap-plan playout."
   - Neither → raise `TapPlanError(f"tap-plan target {target!r} is neither a
     deploy id nor a live te deploy of a scenario by that name", 404)`.
   - **Also reject a resolved target that IS a tap-plan playout** (code
     review, SHOULD-FIX): a tap-plan playout deploy is itself `tier="te"`,
     so a literal `--target <tap-plan-playout-deploy-id>` would otherwise
     pass every check here and only fail much later, confusingly, deep in
     bindings resolution. `resolve_live_target_deploy` (the stricter
     `tap validate` variant) and `resolve_tap_plan_bindings`'s own tier
     guard (shared by `apply_tap_plan`/`tap plan`/`tap validate`'s
     live-resolution path) both check `is_tap_plan_deploy` and raise a
     clear, actionable `TapPlanError(400)` instead.
   - The resolved **concrete deploy id** is what everything downstream uses
     (esp. `plan_id = tap-<te_deploy_id>`), so resolution happens BEFORE the
     plan_id is computed.

## B — `--target` CLI override + effective-target rule

3. **Add `--target <deploy-id|scenario-name>`** to `tap validate`, `tap plan`,
   and `tap deploy` (the verbs that load a plan spec and need a target).
4. **Effective target** = `args.target` (`--target`) if set, else
   `spec.target` (plan), else `None`. `--target` OVERRIDES the plan's
   `target:` when both are present.
5. **`tap deploy` / `tap plan`:** effective target `None` → **error out**
   (exit non-zero, stderr: "no tap-plan target: set `target:` in the plan or
   pass `--target`"). A present effective target is resolved via
   `resolve_target_deploy` (name or id) before building/deploying.

## `tap validate` — two modes keyed on target presence

6. **Effective target PRESENT** → resolve it via `resolve_target_deploy`; it
   MUST resolve to a **live, active** (`te`, non-destroyed) twin, else
   validation FAILS with a clear message. Then validate the bindings against
   that twin's REAL published inventory (selectors resolve to actual tap
   points; a selector that matches nothing, or a mode:exclusive collision, is
   an error) — the current live-resolution path.
7. **Effective target ABSENT** → **semantics-only**: structural checks with NO
   live resolution — every binding has a well-formed `selector`
   (valid `scope`, non-empty resolvable keys) or a `tap:`/`tap_point:` id,
   `sensors` non-empty, `mode` ∈ {exclusive, fanout}, and the plan-wide
   `mode: exclusive` consistency check (a tap point can't be exclusively
   claimed twice). No `StateStore`/inventory lookup. Passes if structurally
   sound. This is the mode a **predefined/library plan** (no `target:`) is
   validated in.

## API parity

8. Keep the API (`/api/...` tap endpoints) coherent: `target` optional in the
   request body; the deploy path requires an effective target (body `target`,
   or an equivalent override field if one is natural) and 400s if absent, same
   as the CLI. Don't diverge CLI vs API semantics.

## Done criteria

1. A plan file with **no `target:`** + `tap deploy --plan f --target
   corp-enterprise` deploys, resolving `corp-enterprise` (a scenario name) to
   the newest live `te` deploy.
2. `--target` overrides a `target:` present in the plan.
3. `tap deploy`/`tap plan` with neither → clear error, non-zero exit, no state
   written.
4. `tap validate` with a target → validates against the live twin (and fails
   if the twin isn't live); with no target → passes on a structurally-sound
   plan without touching the cluster/state.
5. Name resolution excludes tap-plan (`tap-*`) and destroyed deploys; picks
   newest live by `updated_at`.
6. Full suite green; `doc-stats --check` clean; `DESIGN_BRIEF_o2_taps.md` +
   `WORK_ORDER_tap_points.md` note the optional-target / `--target` / name-
   resolution behavior; GAP-030 annotated (name-based selection now partially
   closes it; full label-based still open). New tests for the resolver
   (id/name/miss), the effective-target precedence, the deploy no-target
   error, and both validate modes.
