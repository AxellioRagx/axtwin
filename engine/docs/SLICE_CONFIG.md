# Slice proposal — `config`-class execution (the Ansible path) + `OP-APP` provisioning

> **STATUS: ✅ RATIFIED 2026-06-02 — slice AUTHORIZED.**
> Ratified by the user with the recommended options: execution channel
> **B (QGA-mediated Ansible)**; Ansible **baked into the template via the
> imagebuild `provision:` path**; scope **`OP-APP-010 provision-app` for
> `pkg` / `pkg@version` / `script`** only. This is now the authorized next
> implementation slice (`axrange/config_exec/`) — see `docs/CHANGELOG.md`
> 2026-06-02 and `docs/PLANNER.md` §4.3. Overlays (`OP-OVL`), activity
> (`OP-ACT`), the other `install:` kinds (`config` / `role` / `container`),
> and the deferred planner machinery (concurrency / leases / plan-diff /
> escalate) were **out of this slice's scope** (the project-wide scope gates
> were later removed 2026-06-11; several have since landed — see CHANGELOG).
>
> **Implemented 2026-06-02 (Claude Code):** `pkg` / `pkg@version` built +
> unit-tested (`axrange/config_exec/`, GuestAgent, pass-1 emission, executor
> routing). The **`script` fast-follow is also implemented (2026-06-03)** —
> `run-script` / OP-APP-030, see §11 — so the full ratified scope
> (pkg/pkg@version/script) is built; 162 tests. **Live-verified on `pxtwin1`
> (2026-06-02):** built an ansible-baked template, deployed the app fixture
> (install `ca-certificates`), `provision-app` ran the playbook in-guest over
> QGA (idempotent `ok=1 changed=0`). The `run-script` path is unit-tested; its
> live verification is pending. See CHANGELOG 2026-06-02 / 2026-06-03.

Companion to `docs/PLANNER.md` §4.3 (config-op idempotency / "run the playbook"),
`docs/OP_CATALOG.md` §OP-APP (the op signatures), and `docs/IR_SCHEMA.md` §4.1
(the closed `install:` action set). Picks up where `docs/ADAPTER.md` §6 and
`docs/IMAGE_BUILD.md` §8 left the implementation: the infra + capture + image
foundation is built and live-verified; this is the first `config`-class slice.

---

## 1. What this slice is, and why it's next

The shipped slices are **`infra`-only** — clone/configure/start VMs, wire OVS
mirrors, capture pcaps, build templates. The IR, validator, and op catalog
already *model and validate* apps/overlays/activity, but nothing **executes**
them: `axrange/planner/pass1_emit.py` (line ~262) explicitly rejects any IR with
`layers.apps`. This slice builds the missing **`config`-class execution path**
and the first consumer, **`OP-APP-010 provision-app`** — i.e. "install software
on a deployed host."

It is the largest remaining v1 implementation gap (see `docs/OPEN_QUESTIONS.md`
state summary) and unblocks everything downstream: overlays (`OP-OVL`) and
activity (`OP-ACT`) are also `config`-class and reuse the same execution path.

## 2. The pivotal decision — the execution *channel*

`docs/PLANNER.md` §4.3 says a `config` op is "run an Ansible playbook" with
Ansible-native idempotency, and the tech stack says **"cloud-init + pull-mode
Ansible."** But the infra slice already proved the binding constraint:
**deployed guests sit on isolated scenario SDN networks the orchestrator cannot
reach** — which is exactly why `wait-os-ready` pivoted from an SSH probe to a
**qemu-guest-agent (QGA) exec** (`adapter_proxmox/rest.py`, CHANGELOG 2026-05-29).
Pull-mode Ansible hits the *same wall in reverse*: the guest can't reach an
orchestrator-served playbook repo either.

| Option | Mechanism | Assessment |
|---|---|---|
| **A. Pull-mode (literal tech-stack words)** | guest cloud-init runs `ansible-pull` from an orchestrator-served repo | ❌ Needs a management path the isolated scenario nets don't have. Same reachability wall as push-SSH. |
| **B. QGA-mediated Ansible** *(recommended)* | orchestrator pushes the rendered playbook + files into the guest via the **adapter's existing QGA exec channel**, runs `ansible-playbook` locally in the guest, reads exit/output | ✅ No scenario-net reachability needed. Reuses the channel the infra slice already proved. Boundary stays clean: the Ansible *render* is platform-agnostic; the *delivery* is the Proxmox adapter's (a future VMware adapter swaps the channel, keeps the render). |
| **C. QGA shell (MVP-minimal)** | for `pkg`/`pkg@version`, QGA-exec `apt-get install -y …` directly | ✅ Lightest; proves the thread. Approximates "Ansible-native idempotency" with apt's own idempotency; defers real Ansible. |

**✅ Ratified (2026-06-02): channel B; first slice supports `pkg` /
`pkg@version` / `script`.** This honors the Ansible-native contract while reusing
QGA and keeping the platform boundary clean.

**Sub-decision — where Ansible-in-guest comes from:**
1. **Bake it via imagebuild** — add `provision: [{ pkg: ansible }]` to the
   `debian-12-cloud` build recipe. Clean tie-in: it exercises the imagebuild
   `provision:` path (untested today) *and* feeds this slice. **✅ Ratified.**
2. **Install on demand** — QGA-exec `apt-get install -y ansible` before the first
   playbook run. No imagebuild dependency, slower first boot.
3. **Pure shell (Option C)** — skip Ansible entirely for v1's first cut.

## 3. Scope

**In:**
- New submodule **`axrange/config_exec/`** — renders provisioning from a host's
  IR `install:` actions and runs it in the guest over the QGA channel.
- **`OP-APP-010 provision-app`** for action kinds `pkg`, `pkg@version`, `script`.
  On apt the rendered play opens with a **non-fatal `update_cache` task**
  (2026-08-14, CHANGELOG): a cloned guest inherits its *template's* package
  index, so once the distro rotates its indices `state: present` fails on a
  perfectly installable package — and the error names the package, not the
  stale cache. Non-fatal by design, matching the shell installers'
  `apt-get update … || true`, so the loud failure stays on the install task.
  No `cache_valid_time` — it trusts a timestamp rather than the index actually
  existing, and was observed live skipping the refresh on an emptied cache.
- `pass1_emit` extension: emit `provision-app[$app]` per `layers.apps` entry,
  resolved to its host via the `bindings` app→host block, wired
  `after host-ready[$host]` and `after provision-app[$dep]` (app dependency order).
- Executor dispatch by `op.idempotency_class` (`infra` → `ProxmoxAdapter`,
  `config` → the config backend; both implement the same `run()` shape).
- A 1-host + 1-app fixture, unit tests, and a live `pxtwin1` smoke.

**Out (explicit; follow-on slices):**
- `OP-APP-020 run-container`, the `config` / `role` / `container` action kinds.
- All overlays — `OP-OVL` (identity / data / defense, `domain-ready` /
  `overlay-ready` barriers) and all activity — `OP-ACT`.
- Concurrency semaphores / the `ansible-pool` limit (slice stays sequential; the
  deferred-planner-machinery slice owns concurrency — `docs/PLANNER.md` §8).
- `script:`-as-`escalate` cleanup semantics beyond basic retry (the catalog marks
  `run-script` `escalate`; the slice's `script` support inside `provision-app`
  uses ordinary `redo` retry — escalate walk-back is deferred with the rest of
  the planner machinery, `docs/PLANNER.md` §4.4).

## 4. Op flow

For an app `web` bound to host `h-a`, depending on app `base`:

```
host-ready[h-a]  ─┐
                  ├─►  provision-app[base]  ─►  provision-app[web]  ─► (host stays ready)
provision-app[base] (dep) ─────────────────────┘
```

`provision-app` inputs (from `docs/OP_CATALOG.md` OP-APP-010): `app_id`,
`host_id`, `mgmt_ip`, `install_actions`, `env`. **Note:** under the QGA channel
`mgmt_ip` is vestigial (the backend reaches the guest by `vmid`, exactly as
`wait-os-ready` does) — to be confirmed at build time. Output: `provisioned: bool`.

## 5. Components (grounded in the current tree)

| File | Change |
|---|---|
| `axrange/config_exec/` *(new)* | `ConfigBackend.run(op, inputs, ctx)` mirroring the `Adapter` protocol but delivering via QGA; an Ansible/command renderer for the supported `install:` kinds. |
| `axrange/adapter_proxmox/rest.py` | Factor the QGA exec/exec-status helper out of `op_wait_os_ready` into a shared utility both infra and config use. |
| `axrange/planner/pass1_emit.py` | Replace the `apps`-reject guard (~line 262) with real `provision-app` emission; add `OP-APP-010` to `_OP_CATALOG_IDS`; resolve `bindings` app→host + app deps. |
| `axrange/planner/executor.py` | Route per `op.idempotency_class`: `infra` → Proxmox adapter, `config` → config backend. |
| `axrange/ops/` | No change — `OP-APP-010/020/030` already in the catalog. |
| validator | No change — CAP/REF rules already validate `apps`, `install_support`, and app→host bindings. The *validated* surface is ready; only execution is missing. |
| `tests/` | `test_config_exec.py`; extend `test_planner_dag.py` for the app DAG; a `valid_app` fixture. |

## 6. Open questions (resolve by building, per the `docs/ADAPTER.md` §7 pattern)

- Exact in-guest Ansible invocation: local `ansible-playbook <rendered>.yml`, vs
  `ansible-pull --url file://…`, vs shell-for-`pkg`. Where ansible comes from (§2 sub-decision).
- Package-manager selection from the image manifest (`install_support.pkg: [apt]`).
- `env:` injection into the guest provisioning run.
- How `provision-app` reports convergence through QGA exec-status (exit code +
  captured output → `provisioned`), and `changed=false` no-op semantics on re-deploy.
- Confirm `mgmt_ip` is vestigial under QGA (§4).

## 7. Dependencies & tie-ins

- **imagebuild** (just built): the recommended Ansible-in-guest sourcing adds an
  `ansible` `provision:` step to the `debian-12-cloud` recipe — exercising the
  imagebuild `provision:` path for the first time. Otherwise no hard deps; the
  infra + capture + QGA foundation is all in place.
- **No new design machinery required** beyond the channel decision (§2) — the
  idempotency contract (`PLANNER §4.3`), op signatures (`OP_CATALOG`), and
  validated IR surface already exist.

## 8. Done-criteria (proposed)

1. `axrange validate | plan | deploy` of a 1-host + 1-app scenario (e.g. install
   `nginx` on a Debian host); `plan` shows `provision-app` gated on `host-ready`.
2. Live on `pxtwin1`: deploy clones + boots the host and the app is present
   in-guest (verified via QGA exec).
3. Re-deploy is a no-op for `provision-app` (apt/Ansible reports no change).
4. Full suite green (existing 142 + new).

## 9. Follow-on slices (after this one)

1. `config` / `role` / `container` action kinds + `OP-OVL` overlays
   (identity / data / defense, `domain-ready` / `overlay-ready`).
2. `OP-ACT` activity (deploy-behavior-agent / start-activity / wait-actor-ready),
   gated on `mirror-ready` so capture is live before traffic generation.
3. Deferred planner machinery — concurrency semaphores, lease/lock, plan-diff /
   re-planning, escalate walk-back (`docs/PLANNER.md` §6.2 / §8 / §9 / §4.4).

## 10. Ratification checklist — ✅ DONE 2026-06-02

- [x] Execution channel **B (QGA-mediated Ansible)** + Ansible-in-guest source
      **baked via imagebuild** — ratified by the user.
- [x] Slice scope (§3) authorized — the then-active project scope gates narrowed so
      `OP-APP-010` (`pkg`/`pkg@version`/`script`) was allowed; everything else
      (overlays, activity, other kinds, planner machinery) was deferred at the time
      (those project-wide gates were removed 2026-06-11).
- [x] Channel decision folded into `docs/PLANNER.md` §4.3; `docs/CHANGELOG.md`
      2026-06-02 entry added (`**Authority:** design`); `docs/OPEN_QUESTIONS.md`
      Q13 → resolved; `docs/ARCHITECTURE.md` §147 (pull-mode → QGA-mediated for v1).

---

## 11. Script fast-follow — implemented 2026-06-03

`{script:}` install actions are now supported. **Correction to §3:** scripts emit
**`OP-APP-030 run-script`**, not `OP-APP-010 provision-app` — the op catalog
routes `{script:}` to run-script (gated `after provision-app[$app@*]`, so an
app's packages install before its script). An app can mix `pkg` + `script` →
both ops emit.

- **Emission** (`pass1_emit`): `_classify_install` splits an app's actions into
  pkg dicts (→ provision-app) and script paths (→ run-script). One `script:` per
  app this cut (multiple = trivial fast-follow); `config`/`role`/`container`
  still rejected.
- **Content threading**: `build_plan(scenario_dir=…)` → `emit_ops` reads the
  script file **at emit time** (the backend can't reach the scenario bundle; the
  guest is isolated) and embeds its content in the op inputs. The CLI passes the
  scenario file's directory; a script with no `scenario_dir` is a clear error.
- **Execution** (`ConfigBackend._run_script`): write the script into the guest
  (`/var/lib/axrange/provision/scripts/<app-name>`, `0755`) and exec it directly
  over QGA, honoring the shebang. **Not** Ansible-wrapped (a `script:` is opaque
  shell; provision-app keeps Ansible for pkg idempotency). Runs as root (QGA exec).
- **Catalog**: `OP-APP-030.after` corrected to `provision-app[$app@*]` /
  `run-container[$app@*]` (those ops are `{app,host}`-scoped).

**Limitation carried — escalate-as-redo.** `run-script` is the first
`cleanup_strategy: escalate` op. The slice executor treats escalate as redo
(escalate-walk-back is deferred, `docs/PLANNER.md` §4.4/§8), so a failed script
**retries naively** (up to 3×). Make scripts idempotent, or accept re-run risk.
This makes escalate-walk-back the clearest next planner-machinery target.

**Still pending:** live verification of the `run-script` path (deploy an app with
a `script:`, confirm the side effect in-guest). CHANGELOG 2026-06-03.
