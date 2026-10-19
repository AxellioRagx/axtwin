# Work Order — Traffic tier (`tfc`): traffic profile + standing background traffic

> **STATUS: DONE — gated live-verify (dev45) PASSED 2026-07-25.** Code/tests/docs complete 2026-07-24
> (CHANGELOG "Traffic tier (`tfc`) BUILT"; unit/suite-verified — 1370 passed / 12 skipped / 0 xfailed,
> 1382 collected). Authority: user (2026-07-23) for the work order; Authority: implementation
> (Claude Code) for the build itself, except the `role:` IR field / `scenario_schema_version` 1→2
> bump it depended on, which is Authority: user (2026-07-24, a version-locked schema change,
> confirmed before implementation). **Live-verify (Stage 4 of
> `docs/RUNBOOK_twin_model_live_verify.md`, CHANGELOG 2026-07-25 "Twin-model arc: dev45 live-verify
> Stages 1–4 PASSED"):** `tfc deploy` (28/28) on the `corp-enterprise` twin — standing traffic
> confirmed on the wire (real busy-corp SMB/Kerberos sessions to the DC/file-server, Windows +
> Linux personas) and `tap` re-apply/re-shard independence confirmed (once the Stage-3 gretap
> de-scope bug, LES-118, was fixed — before that fix the independence claim would have been false).
> **Two fixes landed via this stage:** the traffic-profile `schedule` override, previously inert, is
> now wired (LES-124); persona `emits[]` gained an `optional` peer exemption so a flat/minimal twin
> doesn't fail-loud on an absent soft peer (PSN-090 refinement, LES-123). Full suite as of the
> live-verify: 1529 passed / 12 skipped / 0 xfailed (1541 collected). Remaining, out of scope for
> this order: `replay`-drive assignments (GAP-033, no curated pcap ships).
> **Spec:** `docs/DESIGN_BRIEF_twin_model.md` §2/§3/§7. Route: code-writer → tester → code-reviewer →
> doc-writer → **gated** live-verify (dev45).

## 1. Goal

The `tfc` tier: bind personas onto a stood-up twin's host roles and run **standing background
traffic** that **outlives exercise playouts**. Apply/destroy independently of the twin; a twin destroy
cascades it.

## 2. Build

- **Traffic-profile spec** — a named doc: `{ twin: <deploy>, assignments: [{ role|node, persona,
  intensity, schedule, seed }], replay: [{ template, onto_role }] }`. Persona defaults come from the
  twin config (brief §6/§7); the profile overrides per run ("busy corp, quiet OT").
- **`tfc` playout** (new lifecycle, model it on the `tap-<te>` playout in `axrange/o2control/taps.py`
  — a standalone tier-tagged deploy that binds to a `te`, not a second orchestrator):
  - resolve the profile against the twin **inventory** (`tier_split` §2 `hosts[]`) → concrete
    persona→node assignments; reject an assignment whose `requires[]` roles are absent (fail-loud).
  - stand up each assignment via the persona **drivers** (`persona_library`) as `tfc`-tagged standing
    state; **idempotent** apply (Model-C adopt) so re-apply is a no-op; `destroy` stops only `tfc`.
  - seeded jitter (reuse `Schedule`/`Activity.seed`, SLICE_ACTIVITY §11) so replays are identical.
- **Thin surfaces:** CLI `axrange tfc apply <twin> [--profile FILE] | status | destroy`; API
  `POST/GET/DELETE /api/tfc` (clean DTOs). Cascade from a `te` destroy.

## 3. Boundaries

Benign traffic only (attacker activity is `exr`). Consumes `personas/` downward; binds via the
published inventory, never twin internals. Do not stand up sensors or taps here.

## 4. Validation

- Unit: profile resolve against a mock inventory; `requires` rejection; `tfc_` tagging; idempotent
  re-apply; `destroy` removes only `tfc`; twin-destroy cascade; seed determinism.
- **Gated live-verify (dev45):** on a `corp-enterprise` twin with the Lab up, `tfc apply` a profile
  (workstations busy) → confirm `tfc-*` standing units running, background SMB/Kerberos/HTTP on the
  wire at a co-located tap, and that a **separate** `tap` re-apply does **not** disturb it (the §3
  independence claim). `tfc destroy` clean; twin `destroy` cascades it.

## 5. Depends on / sequence

Depends on `tier_split` (inventory + `tfc` tag) **and** `persona_library` (drivers). Parallel-safe with
`exercise_tier` after those land. Docs: `DESIGN_BRIEF_twin_model.md`, `CONVENTIONS.md` §2, CHANGELOG, LES.
