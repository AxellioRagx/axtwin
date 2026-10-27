# Conventions — canonical names & doc genres

> Project-wide naming + documentation taxonomy. Companion to `CLAUDE.md` "Conventions to
> Follow" (code structure / module boundaries) — this doc covers **what things are called**
> and **what kind of doc goes where**. Authority: user, 2026-07-08.

## 1. Canonical product names

One concept, one spelling per context. Do not invent new variants.

| Form | Where it's used | Rule |
|---|---|---|
| **`axRange`** | prose / docs / UI titles / display | The product **display name**. Capital R, no space. |
| **`axrange`** | Python package, `import axrange`, the CLI command, the dist name (`pyproject` `name`) | All-lowercase. The **code/identifier** form. |
| **`axr-product`** | the git repo / working-dir name | Repo only. Not used in code or prose. |
| **`axellio-…`** | externally-published artifacts (e.g. the Grafana plugin `axellio-axrange-control-panel`) | **Org namespace** — Axellio is the company. Use `axellio-axrange-<thing>` for anything published into a foreign ecosystem (Grafana, PyPI-if-ever, etc.). |
| **`axr`** *(short prefix)* | **RETIRED** as a resource-name prefix | Superseded by the tier tags (§2). It survives **only** inside the compound proper nouns `axr-product` / `axrange` and grandfathered transient names (`axr-build` — the imagebuild VM). **Do not** introduce new `axr`-prefixed names. |

## Scenario vs. target environment (the authored spec vs. the deployed instance)

Two related terms, kept **distinct on purpose** — decided 2026-07-09 (**not** renamed; option C).

- **Scenario (IR)** — the **authored spec**: the versioned YAML recipe (`infra` · `apps` · `overlays`
  = identity/data/defense/adversary · `activity`). The thing an author writes; the **version-locked
  contract** term stays **"Scenario IR"** (`docs/IR_SCHEMA.md`), and the code/API keep `scenario`
  identifiers (`/api/scenarios`, `scenario_id`, `scenarios_dir`).
- **Target environment (TE)** — the **deployed instance**: the running, agent-instrumented *world* a
  scenario stands up and that you attack. It's the runtime concept behind the **`te_` resource tier**
  and the **`target-environments/`** folder.

So: **a scenario *defines* a target environment**; `target-environments/` is the library of scenario
IRs, each defining a TE. Why not rename "Scenario IR" → "Target Environment IR"? Because (a) the IR is
a *designed exercise* — it carries `activity` (behavior + scripted attacks) and `overlays.adversary/
defense` (agents), not just a static world; (b) "scenario" isn't a superseded term (unlike `sink`/
`sidecar`); and (c) a full rename is ~1,400 occurrences and would break the version-locked schema +
the `/api/scenarios` contract the SPA consumes — cost far above the clarity gain. See CHANGELOG
2026-07-09.

## 2. Resource-name tier tags (deployed infra)

Every engine/control-created Proxmox resource carries a **tier tag** in its deterministic name
(`axrange/adapter_proxmox/naming.py`); the tag *is* the blast radius. Full model +
`destroy`/cascade semantics: `docs/design/02-twin-model.md`. Quick reference:

| Tier | Prefix shapes | Lifecycle |
|---|---|---|
| **`te`** | `te<6hex>` zone · `tev<5hex>` vnet · `tebr_*` bridge · `te-<6hex>-<host>` VM | a target-environment deploy; `destroy <te>` sweeps it **+ cascades to its standalone `tap-<te>` plan** |
| **`tap`** | `tapg_<6hex>` gretap · `tapm-<vmid>` mirror | the tap-binding (mirror + source-gretap), realized as its own `tap-<te>` deploy (A2, BUILT 2026-07-11; tap-point vocabulary BUILT 2026-07-24, `docs/WORK_ORDER_tap_points.md`); **independently** apply/destroy/redeployable via `axrange o2 tap apply\|destroy` / `POST\|DELETE /api/taps`, without touching `te_`/`o2_`; also swept by a `te` destroy's cascade. **`tap` is a resource name-shape tag, not a `_VALID_TIERS` deploy-level tier** (decided 2026-07-24, not merely deferred — see below) |
| **`o2`** | `o2z<5hex>` SDN zone · `o2v*` vnet · `o2br_*` bridge · `o2-*` VM | the standing surface (A1, BUILT 2026-07-11, `axrange/o2control/`); stood up via `axrange o2 up` / `POST /api/o2/surface` as a **persistent** deploy; **protected** — never swept by a `te`/`tap` destroy |
| **`img`** | `img-<image-slug>` | golden templates / base images; never destroy-swept |
| **`tfc`** *(reserved, `docs/WORK_ORDER_tier_split.md`, 2026-07-23)* | `tfc<5hex>` zone · `tfcv<4hex>` vnet · `tfcbr_*` bridge · `tfc-<6hex>-<host>` VM | the **Traffic** tier (`docs/design/02-twin-model.md`): a benign traffic-profile deploy binding personas onto a stood-up twin's published hosts; self-only on destroy. Tag/name-shapes/teardown-guard are built; no live emission path produces `tfc_` resources yet — that's `persona_library`/`traffic_tier`'s job |
| **`exr`** *(reserved, `docs/WORK_ORDER_tier_split.md`, 2026-07-23)* | `exr<5hex>` zone · `exrv<4hex>` vnet · `exrbr_*` bridge · `exr-<6hex>-<host>` VM | the **Exercise** tier: an ephemeral attack/defense/measurement run against an instrumented twin; self-only on destroy, same posture as `tfc`. Reserved ahead of `docs/WORK_ORDER_exercise_tier.md` (a scope-shift work order, user-gated before it lands) |

`planner/teardown.py`'s `_protected()` guard generalizes to a **six-tier matrix** (`te`/`tap`/`o2`/
`img`/`tfc`/`exr`): every foreign tier's resources are refused unless `own_tier` matches or an
explicit cascade exception applies (today only `te → tap`, load-bearing because `o2control/taps.py`'s
tap-plan playout deploy is itself `tier="te"`). `img` and any operator-declared protected-external
name are refused unconditionally, by every tier including its own. **Refinement (A1):** this guard
is asymmetric by design — an `o2`-tiered destroy (`axrange o2 down`) **can** sweep its OWN `o2_`
resources (it isn't self-protected), while every OTHER tier's destroy still can never touch a
foreign tier's resources, in either direction (a symmetric positive tier-name-shape gate on the
VM-name path too, not just the range/`_protected` pair). The tier vocabulary itself (which
predicates exist, which shape-checks apply per deploy tier) is owned by a single exported table in
`naming.py` (`TIER_PREDICATES`/`TIER_SHAPE_CHECKS`), not duplicated per-consumer, and an
unrecognized tier fails **closed** (refused everywhere) rather than silently borrowing another
tier's shapes.

**`tap` is a tag, not a sixth deploy tier.** A `tap-<te>` playout deploy (A2, `axrange/o2control/
taps.py`) is stamped `tier="te"`, the same as an ordinary target-environment deploy — it is scoped
to only its own three ops (`attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture`) by a
structural discriminator in `destroy_taps`, not by a distinct tier value. `naming.py`'s
`TIER_PREDICATES` (the "which foreign tiers exist to refuse" table) carries a `tap` row so other
tiers' teardowns can positively recognize `tap`-shaped resources; `TIER_SHAPE_CHECKS` (the "what does
THIS tier's own deploy's resources look like" table) deliberately does **not**, since `deploys.tier`
is never `"tap"`. This was `tier_split`'s (2026-07-23) open question, explicitly decided by
`tap_points` (2026-07-24, `docs/WORK_ORDER_tap_points.md` §2, `docs/design/06-taps-sensors.md`):
promoting it is real, separate work (re-verifying the full teardown matrix) with no behavior gap it
would close, so it stays a tag.

**`tier` is a deploy-scoped parameter**, not a hardcoded constant: `naming.py`'s producers
(`zone_id_for_plan`, `vnet_id_for_net`, `underlay_bridge_for_plan`, `bridge_name_for_net`,
`vm_name_for_host`, …) all take `tier: str = "te"`, threaded from `Plan.tier` via
`ExecutionContext.tier` through plan/deploy/naming/state/executor/teardown. Only `o2control` passes
`tier="o2"`; every other caller (the CLI/API TE deploy path, and the A2 tap-plan playout, itself
`tier="te"` — a `tap-<te>` deploy is TE-tiered, not its own tier) uses the default. `state.py`'s
`deploys.tier` column (added `SCHEMA_VERSION` 2→3) makes tier a first-class, queryable deploy
attribute — see `docs/design/05-o2-surface.md` and `latest_deploy_for_tier` /
`deploys_by_tier`.

## 2.5 Twin-model engine registries & content dirs (2026-07-24)

Two more additions from the twin-model arc, same shape as existing precedents:

- **`axrange/personas/`** — the persona library (`docs/design/07-traffic-personas.md`,
  `docs/WORK_ORDER_persona_library.md`): a new engine-vocabulary registry submodule, structured
  exactly like `axrange/sensors/` (schema + loader + open `catalog/*.yaml` + drivers). Pure leaf —
  imports nothing from `axrange.validator`/`axrange.planner`. Its content is validated the same way
  sensors' typed content is: a `checks/<domain>.py` module (here `axrange/validator/checks/
  personas.py`) reads the registry (validator → registry), never the reverse.
- **`twins/`** (at `<project>/catalog/twins/` since 2026-10-20, with `target-environments/`
  and the legacy `twin-configs/` beside it) — the twin content dir: one
  self-contained folder per twin (`docs/WORK_ORDER_twin_consolidation.md` §2) —
  `twins/<id>/twin.yaml` (the manifest), `te.yaml`, `tap/<variant>.yaml`, `tfc/<variant>.yaml`,
  `exr/`, `personas/`, `scripts/`, `generated/` (machine-written, git-tracked); cross-twin assets
  in `twins/_shared/` (`axrange/twinconfig/` is the code). Hand-authored twins and aximprint casts
  share the root. The legacy `twin-configs/<id>/<id>.yaml` (and flat `<id>.yaml`) layout is still
  read (back-compat, until the consolidation's Phase 4). CLI surface:
  `axrange te {validate,plan,deploy} --config <id> [--param k=v]` (an alternative to the
  `scenario.yaml` positional) and `axrange twin-config list|show <id>` (out-of-grid, alongside
  `list-deploys`/`serve`/`image build`).

## 3. Documentation genres

Six kinds of doc; pick by intent, name by the rule. (Until 2026-07-27, the design-of-record genre
below was instead a set of per-topic design-brief files, a second, parallel design-doc set
alongside `docs/design/`. The two were consolidated that day: `docs/design/` is now the **single**
design-of-record, and the brief genre is retired — see `docs/design/README.md` for the full mapping
of retired briefs to the file that now owns each one's content.)

| Genre | Filename pattern | Intent | Change rule |
|---|---|---|---|
| **Formal spec** | `ALLCAPS.md` (e.g. `IR_SCHEMA`, `PLANNER`, `VALIDATOR`, `OP_CATALOG`, `ADAPTER`, `IMAGE_MANIFEST`, `IMAGE_BUILD`, `ARCHITECTURE`) | The **version-locked contract** implementations must satisfy | Confirm-first + bump the schema version (per `CLAUDE.md` "Design Document Maintenance") |
| **Design of record** | `docs/design/NN-topic.md` | The **current-state design** for a subsystem — present-tense, no build-status/dates/counts; the single place a feature's agreed shape lives | Edit freely to keep current; the *reasoning trail* behind a change lives in `docs/CHANGELOG.md`/`docs/LESSONS.md`, not restated here |
| **Slice** | `SLICE_<topic>.md` | The **how, scoped** — a buildable implementation slice's mini-design, tied to one concrete build increment | Edit freely as the slice is built |
| **Work order** | `WORK_ORDER_<topic>.md` | **Do this now** — the **active** spec: scope, slices, acceptance criteria, open decisions, with validation gates. Target **≤ 30 KB** (warn past 40 KB, `scripts/doc-budget.py`) | Edit freely; mark DONE, don't delete (trail). **When a slice is built, its record moves out** — see "Work orders stay the active spec" below |
| **Living register** | `ALLCAPS.md` — `CHANGELOG` (decision log), `LESSONS` (institutional memory), `OPEN_QUESTIONS` (design-topic index), `PRODUCT_GAPS` (gap register), `WORKFLOW` (collaboration protocol) | Append-mostly project memory / process | Append per their own in-file rules; **don't rewrite history** (CHANGELOG) |
| **Reference note** | `docs/reference/<subject>.md` (added 2026-09-08) | An **explanation recorded from a design discussion** — an option weighed against the platform as it stands, with what would have to be built named; neither a work order nor a design-of-record | Dated + Authority-marked; edit freely; when it turns into work it gets a work order and the note stays as the reasoning trail; indexed in `docs/reference/README.md` |

**Design-of-record vs slice vs work-order** — the usual confusion, sharpened:
- **Design of record** (`docs/design/`) = *the current shape* (declarative: "here's how it works
  today"). A design decision's reasoning trail — why, what alternatives, Authority marker — lives in
  `docs/CHANGELOG.md`, not here.
- **Slice** = *a build unit's plan* (a scoped increment of an agreed direction you can implement +
  verify end-to-end).
- **Work order** = *an execution ticket* for the `/dev` Pipeline (concrete files, tests, gates) —
  narrower and more imperative than a slice, and time-bounded.

**Work orders stay the active spec (2026-09-19).** A work order is re-read by every pipeline stage
that touches its topic, so its size is paid per agent call. It holds only what future work needs:
scope, the slices with their acceptance criteria, open decisions, and the rulings/decisions that
still constrain future work (briefly — a sentence and the section they came from). When a slice is
built, its **"built as" / "what landed" / defect-trail / review-round** record moves **verbatim** to
`docs/archive/work_orders/<WORK_ORDER_name>.built.md` (same headings, blocks in original order with
their original line range noted, so a citation like "§18.3" still resolves there by heading). The
work order keeps a one-line status per slice and a pointer line per moved section, so every
`WORK_ORDER_x.md §N` reference elsewhere in the repo stays findable. Prove the move with a line-set
check (every original line present in the trimmed order or the archive; LES-343). A fully built
order keeps its goal, status table, still-binding rulings/decisions and any still-open residual.
Precedent: `WORK_ORDER_console_canvas.md` and `WORK_ORDER_console_level0_rendering.md` (199 KB →
26 KB and 107 KB → 16 KB).

When in doubt: a new or changed direction → update the relevant `docs/design/*.md` file directly and
record the decision (with Authority marker) in `docs/CHANGELOG.md`; a scoped increment of an agreed
direction → **slice**; a ready-to-hand-off task for subagents → **work order**; a contract others
build against → promote to a **formal spec** (ALLCAPS, versioned).

Verification runbooks (e.g. `FABRIC_VERIFICATION.md`) and the cross-session handoff
(`SESSION_STATE.md`, **committed** since 2026-08-04 — solo mode's gitignored checkpoint/restore
machinery was retired that day, see `CLAUDE.md` "Session Handoff") are one-offs outside this
taxonomy — named by their subject, not a genre prefix.

## 4. Doc size budgets (2026-09-16)

Three docs pay a context cost on **every** session and **every** background pipeline agent run,
whether or not that run needed their historical content: `CLAUDE.md` (auto-loaded at session
start), `docs/SESSION_STATE.md` (loaded by the SessionStart hook), and `docs/CHANGELOG.md` (read by
most doc-writer/reviewer passes). Measured on this project before the split below: `CLAUDE.md`
alone was 145 KB (~36k tokens) × 44 agent runs in one session — a per-run cost that scales with
every future invocation, so shrinking these files is a permanent saving, not a one-time tidy-up.

**The rule this project follows:** these three files hold *current orientation*, not the full
history. When one accumulates dated/historical content past its budget, archive the old material
**verbatim** — never summarized; this project's "record, don't rewrite history" discipline (§3,
Living register) applies to size-driven moves exactly as it does to content ones — into a dated
file under `docs/`, with a short pointer left in the live file (one line per archived entry: date +
subject). See `docs/DIRECTION_LOG.md` (`CLAUDE.md`'s former inline "direction" blockquotes),
`docs/changelog/2026-H1.md` (`docs/CHANGELOG.md`'s first-half-2026 entries), and
`docs/handoffs/2026-09.md` (`SESSION_STATE.md`'s prior handoffs — the live file is replaced at each
handoff since 2026-09-19) and `docs/archive/work_orders/` (built-slice records of work orders, §3)
for the pattern each future split should follow.

**Budgets**, checked by `python3 scripts/doc-budget.py` (stdlib-only, no deps, exits non-zero when
a file is over — edit the numbers in that script, not here; this table records the *reasoning*):

| File | Budget | Actual (2026-09-16, right after the split) | Why this number |
|---|---|---|---|
| `CLAUDE.md` | 12 KB (was 60 KB until 2026-09-19) | ~56.3 KB; ~9.7 KB after the 2026-09-19 trim | The orientation file, loaded into every agent. Trimmed 2026-09-19 to a lean orientation doc with no quoted counters (full pre-trim copy archived verbatim in `docs/DIRECTION_LOG.md`); growth past 12 KB is the signal to move history out again, not to raise the budget |
| `docs/SESSION_STATE.md` | 8 KB (was 60 KB until 2026-09-19) | ~31.5 KB; ~7 KB after the 2026-09-19 replace | **Replaced at every handoff, never appended** (2026-09-19): the whole prior content moves verbatim to `docs/handoffs/<YYYY-MM>.md`, newest first, and the live file holds only branch/HEAD + push state, what is in flight, ordered next steps, open decisions and the gotchas that bite now. Injected at every SessionStart, so every byte is paid per session |
| `docs/WORK_ORDER_*.md` | **warn** > 40 KB (target ≤ 30 KB) | 2026-09-19: `fidelity_scorecard` ~63 KB and `protocol_faithful_services` ~63 KB warn (each has an open slice) | Re-read by pipeline stages on its topic. Soft (warn, exit unaffected) because an order with open slices may legitimately be large; the fix is to archive built-slice records (§3), not to raise the number |
| `docs/CHANGELOG.md` | 800 KB | ~718 KB | **Deliberately well above the ~250 KB a first pass might suggest.** Recent entries (Console/golden-twin-era work) are individually large — the 2026-07-01-onward content alone is ~718 KB even right after moving every 2026-01-01..2026-06-30 entry out. This budget is a regrowth alarm ("time for a 2026-H2 split"), not a claim that 250 KB is reachable while the live file still holds nine months of dense entries |
