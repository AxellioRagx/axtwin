# Work Order — Twin configs: parameterized IR templates + starter catalog

> **STATUS: PARTIAL PASS — `corp-enterprise` deploy-verified on dev45 2026-07-25; `sme-flat` still
> PENDING (Stage 6 of the runbook, next session).** Built 2026-07-24 (CHANGELOG 2026-07-24
> "Twin configs BUILT"). Code (`axrange/twinconfig/` + `twin-configs/*.yaml` + the `--config`/
> `twin-config` CLI), tests (`tests/test_twinconfig_{catalog,cli,expand,params,persona,registry,
> tap_materialize}.py`), and docs (`docs/design/02-twin-model.md`,
> `docs/OPEN_QUESTIONS.md` Q2, `docs/CONVENTIONS.md`, `docs/PRODUCT_GAPS.md` GAP-030/GAP-031) are
> all complete — full suite 1529 passed / 12 skipped / 0 xfailed (1541 collected) as of the live-verify
> pass; all 5 configs expand + validate + plan at default and non-default params.
> **Live-verify, `corp-enterprise` half (Stage 2 of `docs/RUNBOOK_twin_model_live_verify.md`,
> CHANGELOG 2026-07-25 "Twin-model arc: dev45 live-verify Stages 1–4 PASSED"):** `te deploy --config
> corp-enterprise` reached 401/401 (after 3 resumes; two transient apt-churn retries plus the
> dataset-size-guard fix, 100 MiB → 2 GiB, user-authorized). `te inventory` published all 32 hosts
> carrying **`role`** and their tap points — the `role:` field + twin-config expander + inventory
> proven end-to-end for the first time on real content. **Content finding, not a code bug:**
> `corp-workstation01..25` NetBIOS-truncate into a single AD computer object (GAP-036).
> **GAP-031 resolved for dev45 specifically** (bindings for `windows-server-2022`/`win11-finance`/
> `ubuntu-server-22` now resolve — with two acknowledged substitutions, see the GAP-031 entry — not a
> general-purpose repo-wide images catalog). **Outstanding:** `sme-flat` (Stage 6, `debian-12-cloud` +
> `ubuntu-server-22`) has not run yet. For a subagent Pipeline session (`/dev`). Authority: user
> (2026-07-23).
> **Spec:** `docs/design/02-twin-model.md`. Route: code-writer → tester → code-reviewer →
> doc-writer → **gated** live-verify (deploy 2 of the 5 configs on dev45).

## 1. Goal

A **twin-config** mechanism: parameterized IR templates that expand into a `te`-tier Scenario IR, plus
a starter catalog (`corp-enterprise`, `datacenter`, `ics-ot`, `dmz-perimeter`, `sme-flat`). Each config
declares role→persona defaults and the candidate `tap_points` the twin publishes.

## 2. Build

- **Template mechanism:** `twin_config` schema (brief §6.1) + an expander that renders a config +
  `params` into a valid Scenario IR the existing validator/planner consume unchanged. Params are
  deploy-time (`N_WORKSTATIONS`, `DOMAIN`, `OT_PROTOCOL`, `FABRIC`, …) — reuse/extend the deferred
  `${N:default}` idea (OPEN_QUESTIONS Q2) rather than a new templating language if practical.
- **`tap_points` emission:** each config declares EW/NS candidate points (brief §6.1); the expander
  wires them into the twin's published inventory (`tier_split` §2) so `tap` can bind them.
- **Persona defaults:** each node role carries a default `persona` (overridable by a traffic profile).
- **Starter catalog** under `twin-configs/` (content, not code — like `target-environments/`): the five
  configs in brief §6.2. Keep `datacenter`'s `FABRIC` param gating the fabric slice so it's opt-in.
- CLI: `axrange te deploy --config <id> --param k=v ...` (or expand-then-deploy); `axrange twin-config
  list|show <id>`.

## 3. Boundaries

Expansion produces standard Scenario IR — do **not** bypass the validator/planner. Twin content only
(infra/apps/identity/data); no attack/activity (that's `exr`), no traffic drivers (that's `tfc`).
Existing hand-written `target-environments/` IRs remain valid twins alongside configs.

## 4. Validation

- Unit: each of the 5 configs expands to an IR that **validates** (all four passes) at default params
  and at a non-default param set; `tap_points` land in the inventory; persona defaults present on roles.
- **Gated live-verify (dev45):** deploy `sme-flat` (cheap) and `corp-enterprise` from configs; confirm
  the twin stands up, publishes its inventory (hosts + EW/NS tap_points), and a `tap`+`tfc` can bind to
  it. `te destroy` clean.

## 5. Depends on / sequence

Depends on `tier_split` (inventory/`tap_points`). Persona defaults reference `persona_library` ids
(soft dep — validate names, don't require drivers). Parallel with `tap_points`. Docs:
`docs/design/02-twin-model.md`, `IR_SCHEMA.md` (if params formalize — version-locked, **confirm + bump**),
`OPEN_QUESTIONS.md` Q2 (close the `${N:default}` follow-up), CHANGELOG, LES.
