# Work Order — CLI lifecycle unification (`te` / `o2` / `tap` × uniform verbs)

> **STATUS: DONE — built 2026-07-16.** See CHANGELOG 2026-07-16 "CLI lifecycle unification" for the
> as-built record.
>
> **As-built note:** the grid (§3.1), the `info` verb + `--json` (§3.2), the `O2_*` config keys (§3.4,
> D2), full symmetry with one-cycle deprecation shims (§3.5, D1), `list-deploys --tier` (§3.6), and
> the `te` vs. `tap-<te>` structural read-path discriminator all landed as specified. **§3.7 API
> parity is DEFERRED, as the work order allowed** — `axrange/api/` was left untouched; that remains
> follow-on work, not folded into this build. Full suite green at **867 pytest tests** (up from 797
> at the time this work order was drafted). Two durable lessons from code review are recorded at
> `docs/LESSONS.md` LES-082 (the `te`/`tap-<te>` structural discriminator generalization) and LES-083
> (`--json` quiet-threading).
>
> **Follow-up as-built note (2026-07-16):** the optional §4 live smoke WAS subsequently run on
> `dev45` (`te deploy → te info → te status → te destroy` against
> `deploy-dev45/scenarios/blast-check.yaml`) — the `info` card is **verified correct on-cluster**
> (resolved config + runtime access facts matched the live guest over QGA, confirming the smoke's
> original positive intent). The same run also caught a **HIGH-severity bare-default deploy-targeting
> bug** ("latest deploy" ordered by `started_at` instead of `updated_at`, plus a `destroyed` row
> revivable in place, plus — found in review of the fix — a just-destroyed tombstone outranking a
> live deploy on a destroy bare-default): a bare `te destroy` could report `Result: DESTROYED`
> against a stale/unrelated deploy while the just-created VM stayed live. Fixed in two rounds, no
> schema-version bump — see CHANGELOG 2026-07-16 "CLI bare-default deploy-targeting safety fix" and
> `docs/LESSONS.md` LES-084 for the full record. STATUS remains **DONE**; this is a fix on top of the
> original build, not a reopening of it.

> **For a subagent Pipeline session (`/dev`).** Authority: user (2026-07-16). **Spec:** this file.
> Route: code-writer → tester → code-reviewer → doc-writer → (no live-verify required — CLI surface
> change, unit/integration-provable; a smoke `deploy`/`destroy` on `dev45` is optional polish, not a
> gate). Touches `axrange/cli/main.py`, `axrange/o2control/{control,taps}.py`,
> `axrange/adapter_proxmox/config.py`, `adapter.conf.example`, `axrange/api/` (parity, optional in
> phase 1). No schema-version bump (no IR / state-store contract change).

## 1. Goal

The CLI grew by accretion: `te` verbs are bare + top-level (`deploy`/`status`/`destroy`), `o2` is
noun-grouped with different verbs (`up`/`down`), and `tap` is buried under `o2` with yet another verb
set (`apply`/`destroy`). Three names for one concept ("idempotently reconcile a spec to the cluster")
and no `validate`/`plan`/`info` surface for `o2` or `tap` at all.

Unify on **three deployable object types, one verb set**, matching the engine's own tier model
(`te` / `o2` / `tap` resource tags already exist — `adapter_proxmox/naming.py`):

```
axrange {te,o2,tap} {validate, plan, deploy, status, info, destroy}
                      └─ spec ─┘ └────── id / latest ──────┘  └guard┘
```

Cross-cutting commands stay **outside** the grid (they are not per-tier deploys): `list-deploys`
(add `--tier`), `serve`, `image build`.

This is a surface/UX change over machinery that **already exists** — `o2` and `tap` both run a spec
through the same executor + state store today. The work is (a) regrouping verbs, (b) wiring the
missing `validate`/`plan`/`info` entry points to internals that are already there, (c) a positional
spec arg, and (d) back-compat aliases.

## 2. Why each verb applies to each tier (grounding)

- **`te`** — full pipeline today (validator → `build_plan` → executor → state). Reference shape.
- **`o2`** — has a **surface manifest** spec; `o2control.control.compose_surface_ir()` turns it into
  an IR run through the *same* engine (`build_plan_and_ctx(..., tier="o2")`). So `validate`/`plan`
  are just "run the validator / dry `build_plan` on the composed IR" — internals exist, no entry
  point.
- **`tap`** — has a **tap-plan** spec (`TapPlanSpec`); deliberately composes a `Plan` **by hand**
  (`taps._compose_ops`) rather than via `build_plan`, because it binds already-existing resources.
  `deploy` == today's `apply_tap_plan`; `destroy` == `destroy_taps`. `validate`/`plan`/`info` are new
  entry points over `parse_tap_plan` / `resolve_tap_plan` / `_compose_ops` (all present).

## 3. Build

### 3.1 Verb grid + spec-as-positional

Each of `te` / `o2` / `tap` gets an argparse sub-subparser group with the six verbs. `deploy`,
`validate`, `plan` take the **spec positionally** (replacing `o2`'s `--surface` flag and `tap`'s
`--plan` flag):

| verb | te | o2 | tap |
|---|---|---|---|
| `validate <spec>` | exists | **new** — validator on composed IR | **new** — parse + resolve bindings |
| `plan <spec>` | exists | **new** — dry `build_plan_and_ctx` | **new** — dry `_compose_ops`, render |
| `deploy <spec>` | exists (`deploy`) | rename `up` → `deploy` | rename `apply` → `deploy` |
| `status [id]` | exists | exists | **new** — `TapPlanStatus` (was deferred) |
| `info [id\|spec]` | **new** | **new** | **new** |
| `destroy [id] --allow-destroy` | exists | rename `down` → `destroy` | exists |

`status`/`info`/`destroy` take an **optional id**, defaulting to the latest deploy **of that tier**
(reuse `StateStore.latest_deploy_for_tier`, already used by `o2 status`). `o2`'s "one standing
surface" assumption stays as-is for now; the positional-spec shape is what makes plural `o2` specs
possible later without another CLI change.

### 3.2 `info` — the new verb (config + access facts)

Distinct from `status`: `status` answers "did it deploy / where did it fail" (the op tree); `info`
answers "what is this and how do I reach it" (resolved config + access facts). `info` prints a
one-line status rollup at top (`completed · 25/25 ops`) so it subsumes a quick health glance.

**Derivation, not a new data plane.** All access facts come from persisted op rows — the pattern is
already proven by `o2control.control._inventory_from_ops` (sensor transport IP ← `attach-vm-to-vnet`
inputs; type ← `deploy-sensor`). Generalize the same read for each tier:

- **`te info`** — nodes + roles, assigned IP per segment, subnets, router WAN IP, SSH user (from the
  binding's `cloud_init.user`), and declared taps (`record-tap-intent` output). Derive IPs from
  `attach-vm-to-vnet` / cloud-init op inputs.
- **`o2 info`** — standing sensors (id / type / transport_ip, reuse `surface_status`) **plus** the
  observability endpoints (Grafana / Loki / Prometheus / Caldera / Wazuh) read from config (§3.4).
- **`tap info`** — resolved bindings (`tap → [sensors]`), per-(bridge,sensor) GRE key, destination
  sensor transport IPs, fan-out capacity note. Reuse `resolve_tap_plan` + the persisted
  `attach-gre-tunnel` scopes.

**Two-tier behavior (mirrors validate):** `info <spec>` with nothing live → resolved config (what it
*would* be). `info [id]` / bare `info` on a live deploy → config **+** runtime access facts. Degrade
gracefully: config always, access-when-live.

**`--json` on `info` (and `status`).** Access info (IPs, endpoints) is the thing operators/agents
pipe — the live-verifier, the API, wrapper scripts. Human table by default; `--json` for a stable
machine shape (define a small dataclass → dict per tier, don't hand-roll dict literals).

### 3.3 `tap validate` is a two-tier resolve (the one honest asymmetry)

`te`/`o2` validate are pure-spec (no cluster, no DB). `tap` validate inherently needs the state DB —
it resolves `target:` (a live TE deploy id) and `sensors:` (standing O2 sensors) against inventory.
Build it in two tiers so an offline lint still works:

- **Static** (always): tap-plan is well-formed — `parse_tap_plan` (bindings shape, no dup taps).
- **Resolve** (when a DB is reachable): `resolve_tap_plan` — target TE exists + is `completed`, every
  named sensor is standing (in A1 inventory), every `tap` maps to a tappable interface.

Print resolve findings as warnings (not hard errors) when the DB is absent/unreadable, so
`tap validate plan.yaml` degrades to a static lint rather than erroring out.

### 3.4 `O2_*` config keys for the observability endpoints

The sensor transport IPs are derivable from state, but the **server-stack endpoints** (Grafana etc.)
live on the dedicated O2 server-stack VM (`docs/WORK_ORDER_o2_server_stack.md`), which is **not** in
the deploy state store. `o2 info` needs an authoritative source. `adapter.conf` is a flat
`KEY=VALUE` file (not INI), so add flat `O2_*` keys alongside the existing `BUILD_*` /
`NETWORK_SENSOR_*` conventions, all optional:

```
# --- O2 surface (observability endpoints; consumed by `axrange o2 info`) ---
O2_GRAFANA_URL=http://10.0.0.90:3000
O2_LOKI_URL=http://10.0.0.90:3100
O2_PROMETHEUS_URL=http://10.0.0.90:9090
O2_CALDERA_URL=http://10.0.0.90:8888
O2_WAZUH_URL=https://10.0.0.90:443
```

Extend `adapter_proxmox/config.py` (`AdapterConfig` + `load_config`) with an optional `o2_endpoints`
map. `o2 info` prints whichever are set; prints "(not configured — see adapter.conf `O2_*`)" for the
rest. **Do not** invent an endpoint from a guessed sensor IP — an unconfigured endpoint is unknown,
not derived.

### 3.5 Full symmetry — no bare verbs (D1 resolved: full symmetry)

**DECISION D1 (RESOLVED — user, 2026-07-16: full symmetry).** The bare top-level verbs are
**removed**. There is exactly one way to invoke every lifecycle op: `axrange <tier> <verb>`.
`axrange deploy scenario.yaml` no longer exists — it is `axrange te deploy scenario.yaml`.

- **No permanent `te` aliases.** Bare `deploy` / `validate` / `plan` / `status` / `destroy` are gone
  from the top-level parser (they move under `te`). `info` is new (never existed bare). `list-deploys`
  stays top-level (cross-cutting, §3.6), as do `serve` and `image build`.
- **Deprecated shims (ONE cycle, then removed — print a one-line stderr notice, then run):** the verbs
  that *change spelling* get a temporary shim so in-flight runbooks don't hard-break mid-cycle:
  `deploy`/`validate`/`plan`/`status`/`destroy` (bare) → `te <verb>`; `o2 up` → `o2 deploy`;
  `o2 down` → `o2 destroy`; `o2 tap apply` → `tap deploy`; `o2 tap destroy` → `tap destroy`. Notice
  form: `note: 'axrange deploy' is deprecated; use 'axrange te deploy' (removed next release)`.
- The shims are **transitional, not permanent** — this is full symmetry with a soft landing, not
  dual-surface-forever. Mark them clearly in code (`# DEPRECATED shim — remove after <cycle>`) so the
  removal is a trivial follow-up.

Implement shims as argparse dispatch, not copy-pasted subparsers — one handler per (tier, verb); a
shim parser just sets `args.tier` / `args.verb`, prints the notice, and calls through. **Update every
in-repo caller to the canonical form in the same change** (docs runbooks, `docs/*` command examples,
the live-verifier agent's flow in `.claude/agents/`, `adapter.conf.example` comments) so the repo
itself never depends on a shim.

### 3.6 `destroy` guard + `list-deploys --tier`

- `destroy` uniformly requires `--allow-destroy` on all three tiers (already true on te/o2/tap —
  keep, document, and make the missing-guard error message identical across tiers).
- `list-deploys` gains `--tier {te,o2,tap}` (filter via `latest_deploy_for_tier` / a tiered query);
  no-flag lists all, as today.

### 3.7 API parity (optional in phase 1, note if deferred)

The API (`axrange/api/app.py`) has `/api/validate`, `/api/plan`, `/api/deploys`, `/api/o2/surface`,
`/api/o2/taps`. Bring it to grid parity where cheap: `GET /api/o2/surface` already ≈ `o2 info`; add
an `info`-shaped projection for `te`/`tap` if the DTOs are close. If it balloons scope, **defer and
say so in the CHANGELOG** — the CLI is the deliverable; API parity is a follow-on.

## 4. Validation

- **Unit:** every (tier, verb) parses and dispatches; positional spec accepted for
  validate/plan/deploy; `status`/`info`/`destroy` default to latest-of-tier; `--allow-destroy`
  enforced on all three; bare aliases dispatch to `te`; deprecated `o2 up`/`down`/`tap apply`/`destroy`
  shims print the notice **and** run. `info --json` emits the defined shape; `info` on nothing-live
  shows config only; `info` on a live deploy adds access facts (drive with a seeded fake state store,
  no cluster). `tap validate` static-only when DB absent; full resolve when present. `o2 info` prints
  configured `O2_*` endpoints and "(not configured)" for the rest.
- **Suite:** full pytest green (currently 797). Add cases; do not regress.
- **Live (optional, not a gate):** a real `axrange te deploy … && te info && te destroy
  --allow-destroy` smoke on `dev45` to eyeball the `info` access card. Reuse the standing
  live-verify runbook; skip if the unit coverage above is convincing.

## 5. Out of scope / notes

- **Not** in the grid: `image build` (`img` tier, different lifecycle), `serve` (daemon). Leave as-is.
- No IR / state-store / validator schema change — no version bump. `info` is a read-only projection;
  `validate`/`plan` reuse existing internals.
- **Decisions (both RESOLVED — user, 2026-07-16):**
  - **D1 — full symmetry.** No bare verbs; `axrange <tier> <verb>` is the only form. Transitional
    deprecation shims for one cycle (§3.5), then removed.
  - **D2 — `O2_*` keys in `adapter.conf`.** Confirmed no existing source: `o2/compose/docker-compose.yml`
    holds only ports (env-overridable), not the server-stack host address, and that `.env` lives on the
    server VM, not the operator machine. `adapter.conf` (already the operator→cluster access file) is the
    home. Not derived from a guessed IP (§3.4).
- Plural `o2` specs are *enabled* by the positional-spec shape but **not built here** — the "one
  standing surface" assumption in `control.py` stays; lifting it is separate work.
