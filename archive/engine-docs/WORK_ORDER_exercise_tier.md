# Work Order — Exercise tier (`exr`): move attack/defense/measurement out of `te`

> **STATUS: DONE, including the gated live-verify (PASSED 2026-07-25).** Authority: user (2026-07-23)
> to start the scope shift; implementation (Claude Code) built + documented it 2026-07-24 (CHANGELOG
> "Exercise tier (`exr`) BUILT", with the required `Supersedes:` reconciliation against the CHANGELOG
> entries that put attack/activity/adversary emission inside the `te` deploy). Unit/suite-verified —
> 1423 passed / 12 skipped / 0 xfailed (1435 collected) at build time, **unchanged at 1529 passed / 12
> skipped / 0 xfailed (1541 collected) through the live-verify** (Stage 5 was content + cluster ops, no
> engine code change); catalog rules 113→114 (`REF-260`). **This completes the five-tier twin model**
> — all six `docs/WORK_ORDER_*` twin-model orders are now code/docs-complete, and five of six
> (including this one) are live-verified (`docs/DESIGN_BRIEF_twin_model.md` §9; only `twin_configs`'
> `sme-flat` half, Stage 6, remains open). **Gated `dev45` live-verify (§4 below) PASSED 2026-07-25**
> (`docs/RUNBOOK_twin_model_live_verify.md` Stage 5; CHANGELOG "Twin-model arc: dev45 live-verify Stage
> 5 (`exercise_tier` capstone) PASSED") — the AD kill chain ran end-to-end against the standing
> `corp-enterprise` twin (recon → AS-REP roast → Kerberoast → **DCSync: real `krbtgt` + AES keys
> dumped**), `[axr-truth]` markers landed in Loki joinable by technique+host+window, `exr destroy` was
> clean and touched only `exr` (twin/`tfc`/`tap` stayed up), and a replay reproduced a byte-identical
> `playout_hash 2be5d3ae0008bec7`. The same-host overlap guard correctly refused a second concurrent
> run on the same host. **Caveat:** the run used the twin's existing `egress-proxy` host as the
> attacker rather than a dedicated attacker node, because twin-configs have no incremental-add path
> (editing the deployed `corp-enterprise` config to add an attacker node computes a new `plan_id` and
> would trigger a whole fresh deploy — see `docs/PRODUCT_GAPS.md` GAP-040, `docs/LESSONS.md` LES-126);
> the vulnerable AD accounts and the impacket toolkit were hand-provisioned onto `egress-proxy` over
> QGA as a one-time workaround. The LLMNR-poisoning step stayed attempt-only for lack of a
> script-staging primitive (GAP-041).
> **Spec:** `docs/DESIGN_BRIEF_twin_model.md` §2/§8. Route: code-writer → tester → code-reviewer →
> doc-writer → **gated** live-verify (dev45). **Scope-shift order** — confirm with user before landing
> (it re-homes ratified behavior); per house rule this is the one order with a design-authority gate.

## 1. Goal

Pull **attack playout + defense tuning + measurement** out of the `te` deploy into an ephemeral,
replayable **`exr` tier** run against an already-instrumented twin. The twin becomes purely *what the
environment is*; the exercise is *what happens to it*.

## 2. Build

- **Exercise spec** — `{ twin: <deploy>, attack: <activity|caldera-ref>, defense: [tuning],
  measurement: {technique labels, run-id}, seed }`. Reuses the existing `activity` engine (inline
  steps / AD kill chain), `overlays.adversary` (Sandcat→Caldera), and A4 measurement
  (`[axr-truth]` markers + Loki join) — this order **re-homes** them, it does not reinvent them.
- **`exr` playout** (standalone tier-tagged deploy binding to a `te`, model on the `tap`/`tfc`
  playouts): stand up the run's agents/activity as `exr`-tagged **ephemeral** state; `destroy` (or
  auto-reap on completion) touches only `exr`; twin destroy cascades it. **Replay** = re-run the same
  spec+seed against the standing twin → identical playout (the product's core loop).
- **Twin decoupling:** remove attack/activity/`overlays.adversary` emission from the `te` deploy path;
  a bare `te deploy` now stands up **only** the environment (matches the sensor-agnostic-te precedent
  where taps left `te`). Validator: warn (not error) when a Scenario IR still carries `activity:`/
  `overlays.adversary:` under a `te` deploy (like `REF-250` for `sensors:`), so existing archetypes
  keep validating.
- **Measurement over the run:** a `run-id`-scoped coverage view (truth markers vs detections joined by
  technique+host+window) — reuse A4; the win here is that traffic (`tfc`) + seed make replays
  identical, so run1-vs-run2 **delta** is meaningful.
- CLI/API: `axrange exr run <twin> [--spec FILE] | status | destroy`; `POST/GET/DELETE /api/exr`.

## 3. Boundaries

Exercise = attacker + defense-tuning + measurement only. Benign background is `tfc`; capture is `tap`;
environment is `te`. Do not re-implement the activity/Caldera/Wazuh/A4 machinery — bind to it. Keep the
standing Lab servers (`o2`) as the attack/defense/telemetry backends.

## 4. Validation

- Unit: `exr_` tagging; `exr` destroy touches only `exr`; twin cascade; the `te`-path no longer emits
  attack/activity; the new validator warning fires on a legacy IR; replay determinism (same spec+seed →
  same playout hash).
- **Gated live-verify (dev45) — PASSED 2026-07-25:** on the standing `corp-enterprise` twin + `tfc`
  (busy) + `tap` (EW/NS) + Lab, ran an `exr` (the AD kill chain) → attack succeeded through DCSync (real
  `krbtgt` + AES keys), `[axr-truth]` markers landed in Loki joinable by technique+host+window, and
  `exr destroy` was clean, leaving twin/`tfc`/`tap` up. **Replay** reproduced an identical
  `playout_hash` (`2be5d3ae0008bec7`). The overlap guard correctly refused a second concurrent run on
  the same host. **Not exercised:** the measurable post-defense-tuning delta (no defense change was
  applied between the run and its replay in this pass — the replay-identity claim was proven, the
  tuning-delta claim was not); attack traffic was run from the twin's `egress-proxy` host rather than a
  dedicated attacker node (see the caveat in the status header above — GAP-040).

## 5. Depends on / sequence

Depends on `tier_split`; strongest paired with `traffic_tier` + `tap_points` (the "attack against
realistic background, cleanly measured" demo needs both). **Last in the arc.** Because it re-homes
ratified attack/measurement behavior, add a CHANGELOG `Supersedes:` line and **confirm with the user**
before landing. Docs: `DESIGN_BRIEF_twin_model.md`, `DESIGN_BRIEF_o2_attack_defense.md` +
`_o2_measurement.md` (re-home note), `SLICE_ACTIVITY.md`, VALIDATOR (new warning), CHANGELOG, LES.
