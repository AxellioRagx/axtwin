# WORK_ORDER_unified_console — built record (archive)

Authored directly at this archive path on 2026-09-30 (doc-writer pass) — **no live
`docs/WORK_ORDER_unified_console.md` was ever created.** This work did not follow the usual
work-order lifecycle (an active `WORK_ORDER_<topic>.md`, slices built out into this archive one at a
time, `docs/CONVENTIONS.md` §3); it proceeded directly from a design ruling under the user's
2026-09-29 autonomous authorization ("design + build everything", "anything may be superseded,
recorded") — the ruling itself (`docs/reference/ui-consolidation/00-design-ruling.md`) served as the
execution ticket, its own §6 "Build order" as the slice plan. This record exists so a citation of
"the unified-console work order" has one place to land, following the same **ruling → what was
built → verification → open items** shape as every other built record in this directory.

## 1. What was ordered (the ruling)

Full text: `docs/reference/ui-consolidation/00-design-ruling.md`. In summary:

- **Problem:** seven UI efforts in ~13 weeks, none converging (`03-ui-history-and-decisions.md`).
  The richest one (the Console canvas) was design-time only and showed nothing for 10 of ~13 twins,
  including the flagship `corp-twin`, while labelling design-time counts "live". The only run-time
  UI (the Grafana plugin) had frozen at the `te` tier. Every real operating action since August was
  CLI + YAML.
- **Ruling:** one app, the axRange Console (`o2/console/`), **run-time first**. Four areas — Range
  (home), Twin (one deployed twin: map + host table, per-host personas, live health, destroy),
  Catalog (validate/plan/deploy, guarded), Design (the pre-existing assertions canvas, relabelled,
  unchanged in function) — plus a Telemetry area linking to three Grafana dashboards. Eight rules
  bind every screen (§2 of the ruling): viewer before editor; one model/one renderer per deployed
  twin; recorded-vs-live always labelled; never invent facts; a table is first-class; every write is
  preview→confirm→progress→result; plain vocabulary on run-time screens; the URL is the state.
- **Engine/API additions ordered:** cluster identity (a targets file, multi-cluster serving) and a
  per-host persona apply (`apply_host_personas` — change one host's traffic without touching any
  other host's) — the two capabilities genuinely missing from the engine (§3 of the ruling).
- **Five prior decisions ruled superseded** (§5): single-pane Grafana (2026-06-25); "UI is a viewer,
  not an authoring tool" (2026-06-19); "Console starts clean, independent of the plugin"
  (design/15); "the plugin keeps Deploy as twin" (D5); the layered free-hand IR canvas (never
  built).
- **Build order** (§6): 1. backend reads; 2. engine + API per-host apply; 3. Console shell +
  Range + Twin (read-only); 4. Console writes; 5. Design area relabel + glossary; 6. docs; 7. live
  verify (read-only against the standing `corp-twin`, then a disposable twin for the write paths).

## 2. What was built (per commit)

Branch `wip/unified-console`, `c27339f7^..0094d9a2` (11 commits, 2026-09-29 → 2026-09-30; 99 files,
+22,452/-930 lines across `axrange/`, `o2/console/`, `tests/`, `docs/`):

| Commit | What it built |
|---|---|
| `c27339f7` | The design ruling itself + the three research reports it rests on (`00`–`03`). |
| `7eb4efe2` | **Engine + API, step 1–2 of the build order:** the targets file / multi-cluster serving, `GET /api/range`, `GET /api/deploys/{id}/hosts`, `GET /api/deploys/{id}/health`, `GET /api/personas`, and the per-host persona apply engine (`o2control.traffic.apply_host_personas`) with its plan/PUT routes. |
| `99f1023e` | **Console, step 3–4:** the one run-time-first Console — Range, Twin (map + host table + host panel), the persona-change flow, Catalog — built against the contract `7eb4efe2` shipped. |
| `71d01491` | **API fix round 1** (review of the initial build, `06-code-review.md`): target identity hardening, lease-first host edits, the plan-fingerprint pin, protected deploys, persisted job diagnostics, `can_apply`, the GAP-098 catalog deploy pin. |
| `54b159b7` | **Console fix round 1:** plan fingerprint, protected deploys, kept seeds, server-driven Apply state — the Console's side of the same review round. |
| `2178df33` | **Docs, step 6:** `docs/design/16-unified-console.md` (new), `Supersedes:` entries, `docs/design/11-api-ui.md`/`15-console-canvas.md`/`05-o2-surface.md`/`01-overview.md`/`ARCHITECTURE.md`/`OPEN_QUESTIONS.md`/`CLAUDE.md`/`o2/README.md` reconciled, `05-deletion-candidates.md` written. |
| `20f6fc91` | **API fix round 2** (live-verify Part A prep): several state stores per target (`extra_dbs`/`--extra-db`), unrecorded VMs, `POST /api/plan`/`POST /api/validate` for twins and imprints with params. |
| `983dd945` | **Console fix round 2:** locked controls on protected twins, twin params and plan pin, a Map\|Hosts switch, plain persona sentences. |
| `1cc93598` | **API fix round 3** (review of round 2): dropped `immutable=1` read-only opens (LES-359); deploys route to the store that already holds the twin (LES-156 recurrence); `409 ambiguous-store`. |
| `d71771c4` | **API fix round 4** (live write-path run, step 7 continued): host-edit job history survives re-drives; superseded edits are marked, not dropped. |
| `0094d9a2` | **Console fix round 4:** the persona editor fits its panel; superseded host edits read as history. |

Step 5 (Design area relabel + glossary; old `/imprints/...` redirects) landed inside `99f1023e` /
`2178df33` rather than as its own commit — see `docs/design/16-unified-console.md` §8–§9.

## 3. Verification

**Tests added this range** (`git diff --stat c27339f7^..HEAD`): `tests/test_unified_console.py`
(918 lines), `tests/test_unified_console_fixes.py` (607), `_fixes2.py` (426), `_fixes3.py` (286),
`_fixes4.py` (54), plus `o2/console/test/{range,twin,personaChange,personaEditor,personaWords}.ui.test.tsx`
and updates across every existing Console test file touched by the new Range/Twin/Catalog UI.

**Code review** (`docs/reference/ui-consolidation/06-code-review.md`, transcribed from the
code-reviewer's hand-back over `c27339f7..99f1023e`): main invariant ("editing one host never
stops, resets or re-drives another host") traced and held on every normal path; four MUST-FIX
findings before any live write (M1 non-default target could silently write to the default cluster;
M2 the detached path lost stop failures while still marking the row stopped; M3 the PUT planned
before taking the lease; M4 the previewed plan could diverge from the executed one, with the UI
unable to tell) — all four closed by fix round 1 (`71d01491`/`54b159b7`). A follow-up review of
round 2's `immutable=1` read-only open reproduced real corruption (`imm2.py`/`imm3.py` in the
review scratchpad) and was closed by fix round 3.

**Live verification** (`docs/CHANGELOG.md` 2026-09-30 "Unified Console live verification"; GAP-115
resolved): Part A, 2026-09-29, read-only against the standing `corp-twin-07cfcdb2` — 106 hosts, 84
persona instances, 80 tap bindings matching the CLI exactly; live VM power state matched `pvesh`;
per-host heartbeat matched `systemctl is-active` on 2 hosts; every preview wrote nothing; six pages
loaded with 0 console errors, the 106-host Twin page in 0.71 s. Part B, 2026-09-30, write-path
against a disposable `sme-flat` (`N_WORKSTATIONS=1`, deploy `sme-flat-8c4cfbac`, plan
`sme-flat-e30ea4cf`, 4 VMs, 42 ops, deploy 271 s, traffic apply 15 s via a substitute profile — GAP-116):
a per-host persona swap completed in ~6 s while the other two hosts' units kept identical
`InvocationID`/`MainPID`/`ActiveEnterTimestamp` and byte-identical op rows; stale `expect_plan` →
`409`; a Windows persona on a Linux host → `os-mismatch`; a stop-only edit → OK; a whole-profile
re-apply that would revert the host edit → `409` without `allow_revert_host_edits`, `200` with it,
marker superseded; the same edit driven through the Console in headless Chromium (401 → token
prompt → 200 → "Applied … after start: alive"); destroy in 25 s removed 4 VMs, 4 mirrors, the vnet,
the zone; both standing twins and both stores were byte-identical before/after, the cluster's other
127 VMs unchanged. Two defects (D2 job history, D3 superseded marking) surfaced by this run and were
fixed same day by fix round 4; D4 (persona editor clipping) was Console-only, same commit.
Screenshots: `docs/reference/ui-consolidation/shots/`.

A dedicated `docs/reference/ui-consolidation/07-live-verification.md` write was attempted and denied
(tool permission) — the record above, the CHANGELOG entry, and the screenshots stand in for it
(GAP-118).

## 4. Open items

- **GAP-113** — scoped-out-on-purpose absences: SSE/websocket (the Console polls), pcap download,
  an exercise-run UI, multi-node placement, write-back of a host edit into a twin's own
  `tfc/*.yaml` (export-only today).
- **GAP-114** — a per-host apply's `intensity` field is accepted but never persisted or driven.
- **GAP-116** — `sme-flat`'s default traffic profile does not resolve (a Windows persona on Debian
  hosts, GAP-111, plus an unrelated `db-tier`→`web-tier` `requires:` gap); the write-path live
  verify used a substitute profile instead.
- **GAP-117** — a whole-profile `tfc deploy` restarts every unit even when a host's assignment is
  unchanged; not introduced by this consolidation, just newly visible next to the per-host apply's
  scoped behavior.
- **GAP-118** — `07-live-verification.md` itself could not be written; see §3 above.
- **GAP-084** — the standing `:8080` now runs with both a token and a protected-deploys list, by
  operator choice; the underlying gap (an optional token with no forced-non-loopback refusal) is
  still unbuilt.
- **Deletion candidates** (`docs/reference/ui-consolidation/05-deletion-candidates.md`) — verified
  safe to delete: `observability/`, `wip/ui-viewer-spa` (+ remote), the merged
  `.claude/worktrees/agent-ab92e05d08ed71105` worktree + branch, `scenarios/`, the deprecated
  Grafana plugin + 3 dashboards + `topology-eval/`, the `axr-statedb` datasource. **Not yet safe:**
  the `twin-configs-cast/` shim — still the only working Design-area Apply path for a real
  (non-fixture) imprint until the Design area learns to write into the `twins/<id>/` layout.
  **Nothing has been deleted; all of it is the user's approval to give.**

## Still-binding rulings

- **One app, run-time first:** the axRange Console is the operator surface; Grafana is telemetry
  only (three dashboards, linked, never embedded for anything else).
- **The Design area is unchanged in function** — only relabelled and re-rooted at `/design`; it
  still edits only the imprint, never a deployed twin. Run-time persona changes go through the
  `tfc` tier exclusively.
- **Recorded vs. live is always labelled**, and unknown is never rendered as zero — the two rules
  every screen in the Console is built against.

## Where this fits

- `docs/design/16-unified-console.md` — the design of record this order built.
- `docs/design/11-api-ui.md` — the control-plane API/CLI grid this order extended.
- `docs/reference/ui-consolidation/00-design-ruling.md` through `06-code-review.md` — the full
  evidence trail (backend audit, UI-surface inventory, history-and-decisions, the API contract with
  its four fix-round deltas, the deletion-candidate list, the code review).
- `docs/CHANGELOG.md` 2026-09-29 and 2026-09-30 — the decision-log entries this build produced.
