# cyber-range

## What This Project Is

**axRange** — an on-premises cyber range: **design** layered network/host/activity topologies →
**deploy** them to a virtualization backend (Proxmox first; pluggable adapters) → **capture**
mirrored traffic to a sensor → **simulate attacks** and replay them after defenses are added →
**use AI** for scenario planning, red/blue agents and telemetry analysis. Primary purpose:
validate cyber-defense solutions against repeatable attacks in realistic, disposable environments.

## Current State

The v1 engine (IR → validator → planner → Proxmox adapter → destroy) and the surfaces around it
(image builds, config execution, router/fabric, sensors, API, O2, the five-tier twin model) are
built and live-verified on `dev45` (PVE 9.2, default target) and `pxtwin1` (PVE 8.4). The unified
Console (`docs/design/16-unified-console.md`) is built, pending its own live-verify pass. CLI:
`axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}` plus `te inventory|scorecard`,
`list-deploys`, `serve [--targets <file>]`, `image build`.
**Today's system: [`docs/design/README.md`](docs/design/README.md).** History (slice narratives,
bring-up fixes, direction notes, the full pre-trim `CLAUDE.md`) is archived verbatim in
[`docs/DIRECTION_LOG.md`](docs/DIRECTION_LOG.md); decisions in `docs/CHANGELOG.md`; the live
thread in `docs/SESSION_STATE.md`. Quote no test/op/rule counts here — they drift every commit.

## Architecture at a Glance

Five planes, each independently swappable:

```
Design: hand-authored Scenario IR/twin-config YAML, or the axRange Console's Design area
   (an assertions editor over an aximprint cast; layers/overlays stay an IR concept, not a canvas)
   | save -> Scenario IR (platform-agnostic YAML)
   v
Validator -> Planner (DAG) -> Adapter  (Proxmox v1; VMware/cloud later)
   | deploy
   v
Runtime: scenario zones = SDN zone + bridge + VMs/CTs + per-tap tc mirrors
   | mirror: GRE over a dedicated, isolated vnet-transport
   v
Capture sensor: tcpdump on decapsulated GRE -> rotating pcaps -> disk watchdog
   | post-hoc analysis
   v
AI core: Planner / Red-team / Blue-team / Telemetry analyst / Traffic gen
```

Deep dive: `docs/ARCHITECTURE.md`, `docs/design/`.

## Tech Stack (Locked In So Far)

| Layer | Choice |
|---|---|
| Virtualization (v1) | Proxmox VE (VMs + LXC, scriptable API, OVS-capable) |
| Scenario networks | Proxmox SDN, Simple/VLAN zones |
| Mirror plane | per-tap `tc` mirror on the Linux vnet bridge → keyed gretap → flow-based sensor |
| Transport vnet | dedicated isolated vnet — sensor invisible to scenario traffic |
| Capture / retention | `tcpdump` rotating pcaps; systemd-timer disk watchdog (two thresholds, oldest-first prune) |
| Guest IaC | cloud-init + Ansible, delivered into the guest over qemu-guest-agent |
| Scenario IR | YAML + JSON schema, versioned in git |
| Workflow | Claude + Claude Code (`docs/WORKFLOW.md`) |

## How to Read These Docs

1. `docs/design/README.md` — current design of record.
2. `docs/WORKFLOW.md` — workflow protocol; `docs/CONVENTIONS.md` — naming, doc genres, size budgets.
3. `docs/CHANGELOG.md` — decision log (Authority-marked, newest first).
4. Version-locked specs: `ARCHITECTURE`, `IR_SCHEMA`, `IMAGE_MANIFEST`, `PLANNER`, `VALIDATOR`, `OP_CATALOG`, `ADAPTER`, `IMAGE_BUILD` (all `docs/*.md`).
5. `docs/SLICE_*.md`, `docs/WORK_ORDER_*.md` — per-slice designs / active specs (built records: `docs/archive/work_orders/`).
6. Registers: `docs/OPEN_QUESTIONS.md`, `docs/PRODUCT_GAPS.md`, `docs/LESSONS.md`.

## Immediate Next Actions for Claude Code

No gate — the user routes work per thread; current focus is in `docs/SESSION_STATE.md`. Standing threads (detail in `docs/DIRECTION_LOG.md`):
1. Golden-twin fidelity / Console canvas (`wip/golden-twin`; `docs/design/13`–`15`).
2. `OP-OVL` overlays + `OP-ACT` activity for the `target-environments/` archetypes (delegated to Claude Code).
3. Per-rule firewall policy (promoted 2026-09-03; nftables via QGA + VyOS via vbash; no work order yet).
4. Provision-app ordering: apt-on-host waits for `configure-router`.
5. Twin-model Stage 6: `sme-flat`.
6. Refinement bucket: planner registries/parallel executor/§9.2 redo rules; adapter SSH pooling; imagebuild Ubuntu + `zeek-sensor`.

## Conventions to Follow

Naming, tier tags and doc genres: **`docs/CONVENTIONS.md`**. Code structure:
- **One thing per top-level directory.** `axrange/` — the standalone Python engine (`ir/ manifest/ ops/ validator/ planner/ adapter_proxmox/ imagebuild/ config_exec/ sensors/ api/ cli/`); never imports `o2/`, `scenario_ai_research/`, `target-environments/`. `o2/` — observability surface, incl. `o2/tcpdump-sensor/` (shell + systemd, not Python) and `o2/console/` (Vite/React SPA; `npm ci && npm run build`, served by `axrange serve --spa-dir`) — the axRange Console, the one operator UI (`docs/design/16-unified-console.md`); Grafana is telemetry-only, the control-panel plugin removed 2026-10-01. `target-environments/` — Scenario IR content. `scenario_ai_research/` — may import axRange, never the reverse. Plus `docs/`, `tests/`, `scripts/`.
- **Don't mix concerns across submodules.** `planner/` talks to the adapter only via the `Adapter` protocol; `adapter_proxmox/` knows nothing of IR/scenarios (it runs `OpInstance`s). `cli/` and `imagebuild/` may wire planner + adapter. `config_exec/` handles `config`-class ops over the adapter's QGA channel. Op-internal REST→SSH hops (e.g. snippet SCP) are fine.
- **No leaking platform details up the stack.** The sensor doesn't know scenarios; the adapter doesn't know AI.
- **Config files over flags.** **Health is first-class:** `--healthcheck` + structured logs, no silent failures.
- **No exotic kernels or DKMS drivers in v1** — stock Debian/Ubuntu.

## Operational Notes

- Cloud-init uses `cicustom` (snippet from `_render_userdata`, SCP'd to `/var/lib/vz/snippets/`; its `runcmd` starts QGA). VyOS routers are configured over SSH `vbash` instead.
- Templates need QGA baked in (image pipeline does it; manual runbook in `docs/IMAGE_BUILD.md`).
- Capture is merged per sensor; route taps to another sensor for a separate capture.
- An old-schema `state.db` fails loud — re-initialize it. Confirm the target cluster before a live deploy.

## Git Policy

This project is tracked in a local git repo with a remote (`origin`). **Outbound push is user-authorized (2026-06-09); inbound sync stays manual.**

**Allowed:** `git status`, `git log`, `git diff`, `git show`, `git add`, `git commit`, `git branch`, `git checkout`, `git switch`, `git stash`, `git restore`, `git merge`, `git rebase`, **`git push`** (on the user's explicit request — e.g. pushing a `wip/*` branch).

**Forbidden:** `git fetch`, `git pull`, `git clone` (from remote), `git remote add`, `git remote set-url`. Any *inbound* sync or remote-reconfiguration command.

If asked to **pull/fetch/clone** from the remote, respond: *"Inbound remote sync is your manual responsibility. Run pull/fetch/clone yourself from the terminal."* Push is fine when the user asks.

**Commit style:** `type: subject`, where `type` names either the *kind* of change or the *area* of the codebase. Both axes are valid; pick whichever describes the commit best, and bias toward the area prefix when a commit is entirely scoped to one area.

Kind prefixes:

- `feat` — new functionality
- `fix` — bug fix
- `docs` — implementation-driven doc corrections
- `design` — deliberate design changes (confirm first only when the risk trigger in Design Document Maintenance applies)
- `refactor` — code restructure, no behavior change
- `test` — test-only changes
- `chore` — tooling, build, dependency bumps

Area prefixes (use when the entire commit is scoped to one area):

- `o2` — the observability & orchestration surface layer (`o2/`), including the capture appliance (`o2/tcpdump-sensor/`)
- `axrange` — the Python package (`axrange/`, covering `ir/`, `manifest/`, `ops/`, `validator/`, `cli/`, and future `planner/`, `adapter_proxmox/` submodules)
- `canvas`, `ai` — reserved for when those planes come online

## Design Document Maintenance

`CLAUDE.md` and `docs/` are a living spec — edit freely for errors, tuned defaults, small unanticipated components, better comments. **Confirm with the user first only when a change:** reverses a ratified CHANGELOG decision (add `**Supersedes:**`); changes a version-locked schema (`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` — bump its version); or shifts iteration scope/direction. Otherwise advance the design, make the change, and record it — the record (Authority marker, `Supersedes:`) plus the user's diff review is the safety net. Authority markers: `user` / `implementation (Claude Code)` / `design (chat session)` (only for an invoked chat-bridge session; Claude Code's own design calls are `implementation (Claude Code)`) / `retrofit`. Commit prefixes: `docs:` for implementation-driven corrections, `design:` for deliberate design changes.

## Session Handoff

`docs/SESSION_STATE.md` is the committed handoff. Read it at session start and restate it; at wrap **replace** it (prior content moved verbatim to `docs/handoffs/<YYYY-MM>.md`; ≤ 8 KB) and commit it with the work (the `doc-writer` stage does this in `/dev`). Mid-flight work lives on a named `wip/*` branch. A stale handoff is worse than none — fix or empty it.

## Collaboration & Design Authority

Any surface may make design decisions; Claude Code advances design by default and records it (`docs/WORKFLOW.md`). Chat-bridge is optional — the user's per-thread call, for greenfield or cross-cutting design; pause-and-confirm only on the risk triggers above. Never silently overwrite a ratified decision — reconcile via `Supersedes:`. Chat-bridge only: the user tags `chat-YYYYMMDD` and pastes `NEXT_SESSION_PROMPT.md`. Pipeline mode: `/dev <task>` (code → test → review → docs → gated live-verify).

## Starting Work

No pre-flight questionnaire: pick a thread above or the user's task; confirm the cluster before a live deploy.
