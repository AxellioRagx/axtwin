# Planner — Formal Spec (v1)

Authoritative spec for the planner: how a validated IR becomes an executable DAG, how the DAG is executed, how failures propagate, and how the planner process is structured. Companion to `docs/IR_SCHEMA.md` (input) and `docs/IMAGE_MANIFEST.md` (the other half of the planner's input). CHANGELOG entry dated 2026-05-21 "Planner: DAG construction + runtime + concurrency + re-planning" captures the rationale; this document captures the surface.

**Status:** ratified for v1. Changes to this spec require a CHANGELOG entry. Operation alphabet, redo rules table, and validator rule catalog are sibling deliverables not yet written.

---

## 1. What the planner does

The planner consumes:

- A validated IR (per `docs/IR_SCHEMA.md`).
- Image manifests and per-platform bindings (per `docs/IMAGE_MANIFEST.md`).
- A snapshot of the target Proxmox cluster's capabilities (which nodes exist, which `host:*` capabilities they advertise, current load).

It produces:

- A **DAG of operations** (a deploy plan).
- A **placement decision** for each scenario node (which Proxmox node hosts it).

It then executes the DAG, honoring retry, escalation, and halt policies, recording per-op state to the state store.

The planner is platform-agnostic in its algorithm; the adapter is what knows about Proxmox specifically. The planner imports the adapter as a library (see §6).

---

## 2. The operation model

### 2.1 Granularity

**One op per externally-observable side effect.** A clone of a VM is one op. Attaching that VM's NIC to a vnet is another op. Installing a single `pkg:` action from an app's `install:` list is another op.

A typical AD ransomware scenario with ~8 hosts, ~5 apps, ~3 defenses, ~6 activities produces a DAG of ~150–250 ops.

Rationale: fine granularity enables surgical retry (re-run one failed op, not the whole VM provision) and surgical partial redeploy (re-run one op when a single field of the IR changes). The cost — more ops to model and track — is acceptable because most ops are mechanical.

### 2.2 Op kinds

Every op has a `kind` field that determines its execution and retry semantics:

| Kind | Purpose | Execution | Retry semantics |
|---|---|---|---|
| `action` | Mutate external state (Proxmox API call, OVS command, Ansible play). | One attempt = run-to-completion or error. | Idempotent on retry (see §4). |
| `wait` | Poll an external condition with a timeout. | Loop until condition met or timeout. | Per-attempt timeout; retry resets the timeout. |
| `barrier` | Synthetic milestone; does nothing. | Always succeeds when all predecessors succeed. | N/A. |

Barriers carry no Proxmox-side work. They exist to give cross-entity dependencies a single, stable target (see §3.3).

### 2.3 Op definition fields

Each op type (e.g. `clone-vm`, `attach-vm-to-vnet`) has a definition that declares:

| Field | Type | Purpose |
|---|---|---|
| `name` | string | Unique op type identifier. |
| `kind` | enum | `action` \| `wait` \| `barrier`. |
| `inputs` | map[name → type] | Typed inputs the op consumes. Sources: IR fields, image manifest fields, prior ops' outputs. |
| `outputs` | map[name → type] | Typed outputs the op produces; available to downstream ops. |
| `after` | list[barrier-name-pattern] | Non-data-flow ordering: barriers this op must run after. (E.g. `wait-os-ready` declares `after: [install-ovs-mirror[host=$host]]` so the mirror is up before the OS is reachable.) |
| `cleanup_strategy` | enum | `redo` (default) \| `escalate`. See §4.4. |
| `idempotency_class` | enum | `infra` (Model C — adapter ensures via deterministic naming) \| `config` (Ansible-native idempotency). See §4. |
| `resource_categories` | list[category] | Concurrency-semaphore categories this op occupies while running. See §8.1 for the closed category set. (Added 2026-05-27 with the op catalog; `wait` ops declare them too, `barrier` ops do not.) |
| `implementation` | adapter reference | The adapter function that executes one attempt. |
| `timeout` | duration | Wall-clock per-attempt timeout. |

The set of op types is the **op catalog** (`docs/OP_CATALOG.md`), a sibling deliverable to this spec. It instantiates the field set above for every v1 op type and adds two catalog-side fields not tracked here — `id` (a stable `OP-<CAT>-<NNN>` cross-reference handle) and `redo_on` (the IR field paths whose mutation redoes the op; see §9.2). The catalog is authoritative for *which ops exist*; this spec remains authoritative for *how ops behave*.

### 2.4 Op instances vs op definitions

An op type is a definition (e.g. `clone-vm`). An op instance is a definition applied to specific scope (e.g. `clone-vm[h-fin]` for one IR node).

Every op instance has a deterministic `op_id` of the form `<plan-id>:<op-type>:<scope>`, where `scope` encodes the IR entity (or entities) the op acts on. The `op_id` is stable across re-runs of the same plan — this is what enables deterministic deployment naming (see §4.2).

---

## 3. DAG construction

The planner builds the DAG in three deterministic passes. Same IR + same manifests + same cluster snapshot → same DAG, every time.

### 3.1 Pass 1 — Emit op instances

Walk the IR and emit op instances per entity:

- **Each `network` entry** → 1 op (`create-vnet[<net-id>]`) plus dependencies on cluster-level setup if needed.
- **Each `host` node** → ~7-8 infrastructure ops (clone, configure hardware, configure cloud-init, attach NIC(s), install mirror, start, wait for guest agent, wait for OS) plus configuration ops per binding.
- **Each `router` / `switch` node** → similar to host with kind-specific differences.
- **Each `app` binding** → 1 op per `install:` action (apps with multi-action installs produce multiple ops, strictly sequenced in IR order — see §3.2).
- **Each `activity` binding** → 1 op (`start-activity[<activity-id>@<host-id>]`).
- **Each `overlays.defense` entry** → 1-2 ops depending on binding mode (`observes:` and/or `installed_on:`).
- **Each `overlays.identity` entry** → 1 op against the identity provider (AD provision, local user create).
- **Each `overlays.data` entry** → 1 op (synthetic data seed against the bound host).

Also emit **synthetic barriers** for each entity that downstream work depends on:

- `network-ready[<net-id>]` — depends on all ops needed to make the network usable.
- `host-ready[<host-id>]` — depends on clone, configure, start, waits, mirror install.
- `domain-ready[<domain-fqdn>]` — depends on AD provision + DNS for that domain.

The specific predecessor sets of each barrier are part of the op catalog (§11), not free-form.

### 3.2 Pass 2 — Add data-flow edges

For each op instance, examine its declared `inputs:`. For each input, find the op (or barrier) whose `outputs:` provides the matching typed name + scope. Emit an edge from producer to consumer.

Type matching is strict: an input of type `vmid:int` matches only an output declared as `vmid:int` on a compatible scope. Mismatches are planner bugs, not runtime errors — they surface during plan validation before any execution.

**Data-flow edges may bypass barriers.** If `provision-app[emr-app@h-fin]` needs `vmid:int` from `clone-vm[h-fin]`, the edge goes directly between those two ops, even if there's also a barrier (`host-ready[h-fin]`) between them. The barrier carries ordering; data flows on its own edges. The result is more edges, but each one means something.

### 3.3 Pass 3 — Add ordering edges

Two sources of non-data-flow ordering:

**(a) `after:` annotations on op definitions.** Each entry in `after:` names a barrier or op-type with scope-substitution patterns. For each matching predecessor in the DAG, emit an edge.

**(b) IR-level bindings.** Walk the IR's `bindings:` block and `overlays:` block. Each binding implies an ordering: the bound entity depends on its host (or domain, or network). These edges always land on synthetic barriers — `provision-app[emr-app@h-fin]` depends on `host-ready[h-fin]`, not on a specific implementation op.

The barrier abstraction means downstream consumers are insensitive to upstream op composition. If we later split `wait-os-ready` into two ops, nothing outside the host's op subtree changes — `host-ready` just gains a new predecessor.

### 3.4 Pass 4 — Placement

For each host node, the planner picks a Proxmox node. The constraint is:

```
ir_node.requires (host:* keywords) ∪ binding.placement_constraints.requires_host_caps  ⊆  proxmox_node.advertised_caps
```

The union model: IR-author-declared `host:*` requirements and binding-declared `requires_host_caps:` are both authoritative; both contribute (see `docs/IMAGE_MANIFEST.md` §4).

Among Proxmox nodes that satisfy the union and aren't in `binding.placement_constraints.avoid_nodes:`, the planner picks one using a stable heuristic — default: least-loaded (lowest count of VMs currently running). Ties broken by node name (alphabetic).

The choice is recorded in the state store on the host's first deploy and reused on every subsequent re-plan. Partial-redeploy lands the VM on the same Proxmox node it was on before, unless the constraint set has changed and the previous node no longer satisfies it.

### 3.5 Validation of the constructed DAG

After all four passes, the planner runs DAG-level validation:

- **Cycle detection.** A cycle is a planner bug (op definitions or IR structure created a contradiction). Fails closed.
- **Reachability.** Every op must be reachable from some root (op with no predecessors). Unreachable ops indicate a bug.
- **Type consistency.** Every input must be satisfied by exactly one output of matching type and scope.
- **Placement satisfiability.** Every host node has at least one Proxmox node that satisfies its constraints.

DAG validation failures halt the deploy before any external state is touched.

---

## 4. Idempotency model

Every action op is idempotent on retry from the planner's perspective. The *means* of idempotency differ by op category, but the planner doesn't know or care.

### 4.1 Two layers

| `idempotency_class` | Examples | Mechanism |
|---|---|---|
| `infra` | `clone-vm`, `configure-vm-hardware`, `attach-vm-to-vnet`, `install-ovs-mirror`, `create-vnet` | **Model C** (see §4.2). The adapter implementation uses deterministic resource naming and check-then-act on entry. |
| `config` | `install-app-action` (any kind), `start-activity`, `install-defense-agent` | **Ansible-native idempotency.** The adapter just re-runs the playbook. Each Ansible task is idempotent by virtue of Ansible module semantics (`apt: state=present`, `copy:` checking file hashes, etc.). |

### 4.2 Model C (for `infra` ops)

The contract every `infra` op implementation honors:

1. **Use deterministic resource names.** Each op produces resources whose names are derived from `{plan_id, op_id}`. For example, `clone-vm[h-fin]` produces a VM whose Proxmox `vmid` is derived from `hash(plan_id + "clone-vm" + "h-fin")` mapped to the available vmid range, or whose `name` field is `te-<plan-id>-<host-id>` (the `te` blast-radius tier tag — `docs/CONVENTIONS.md` §2, `docs/ADAPTER.md` §4; the illustrative `axr-` prefix used before the 2026-07-08 tier-tag naming is retired).

2. **Check before acting.** Every `infra` op begins by querying "does this resource exist in the expected end-state?" Possible outcomes:
   - **Resource exists with expected end-state** → no-op success; record output if not already recorded.
   - **Resource partially exists** (e.g. VM was cloned but never configured) → cleanup, then redo.
   - **Resource does not exist** → do the work.

3. **Self-clean partial state.** If a previous attempt left partial state, the op's implementation cleans up before retrying. The adapter's responsibility, not the planner's.

4. **Return outputs.** On success, the op writes its declared `outputs:` to the state store.

### 4.3 Ansible-native (for `config` ops)

For `config` ops, the adapter implementation is "run this Ansible playbook." No deterministic naming, no explicit cleanup logic. The Ansible playbook's own idempotency (tasks declare `state:`; modules check before acting) handles the contract.

The planner contract is identical to `infra`: re-running a completed `config` op is a fast no-op (every task reports `changed=false`); re-running a partially-completed op picks up where it left off (failed task tries again, others no-op).

**v1 delivery channel — QGA-mediated (ratified 2026-06-02).** *Where* the playbook runs is a delivery sub-choice the op model leaves open. Deployed guests sit on isolated scenario SDN networks the orchestrator cannot reach, so the original "pull-mode Ansible" intent (`docs/ARCHITECTURE.md` §147) does not work in v1 — the guest can't reach an orchestrator-served playbook repo any more than the orchestrator can SSH in (the same reachability wall `wait-os-ready` hit, `docs/ADAPTER.md` §3, which pivoted to qemu-guest-agent exec). v1 therefore delivers via the **qemu-guest-agent (QGA) exec channel**: the orchestrator pushes the rendered playbook + files into the guest and runs `ansible-playbook` locally there, reading the result back over the agent — reusing the channel the infra slice proved. The Ansible *render* stays platform-agnostic; the *delivery* is the Proxmox adapter's (a future VMware adapter swaps the channel, keeps the render). Ansible is present in the guest because the image build pipeline bakes it in (`docs/IMAGE_BUILD.md`, a `provision:` recipe step). The first `config`-class slice — `OP-APP-010 provision-app` for `pkg` / `pkg@version` / `script` — and its full scope are in `docs/SLICE_CONFIG.md`; ratified in `docs/CHANGELOG.md` 2026-06-02.

### 4.4 `cleanup_strategy`

Two values:

| Value | Meaning |
|---|---|
| `redo` (default) | Op can self-clean its partial state. Planner retries up to `max_attempts`. |
| `escalate` | Op cannot self-clean. Failure escalates to the preceding synthetic barrier; planner re-runs the whole subtree leading to that barrier. |

`escalate` is the right marker for ops whose external effects can't be undone (e.g. `script:` install actions running arbitrary shell, network packets sent during failed boot, etc.). It's also right for ops where the cleanup cost approaches the redo cost — better to start fresh than reconstruct partial state.

### 4.5 Wait-op timeouts

Wait ops have a per-attempt timeout. Retry resets the timeout — each attempt gets a fresh budget.

Rationale: tracking cumulative wait time conflates "the system is slow today" (might succeed on retry) with "the system is broken" (won't succeed regardless). Per-attempt timeouts make each retry an independent attempt; the total wall-clock spent across retries is observable in logs.

---

## 5. Failure semantics

### 5.1 Retry policy (for `redo` ops)

Default platform policy:

- **Max attempts:** 3 (one initial + two retries).
- **Backoff:** Exponential — 5s → 15s → 45s (3× multiplier, ±10% jitter on each delay).

Operator overrides at deploy time:

- `--max-retries N` overrides max attempts.
- `--retry-backoff TYPE` overrides backoff strategy. Forms: `fixed:5s` (constant delay), `exp:5s:3x:45s` (start, multiplier, cap).

### 5.2 Escalation policy (for `escalate` ops)

When an `escalate`-marked op fails:

1. **First escalate-failure** — the planner walks back to the preceding synthetic barrier, marks all ops in that subtree as needing redo, and re-emits the subtree.
2. **Second escalate-failure on the same subtree** — the planner halts the subtree and requires operator intervention. The CLI reports: "subtree for `host-ready[h-fin]` escalated twice; halting. Inspect manually."

Operator override: `--no-auto-escalate` disables automatic escalation entirely. First escalate-failure halts the subtree immediately.

### 5.3 Halt scope on terminal failure

When an op fails terminally (max retries exhausted, or second escalation, or `--no-auto-escalate` first-escalate-failure), the planner halts:

- **The failing subtree** — descendants of the failed op via the DAG.
- **Transitive dependents** — any op that depends on a halted op, recursively.

Independent subtrees continue. The deploy ends with a mix of completed, failed, and `halted-due-to-upstream` ops; the planner exits with non-zero status.

Operator overrides:

- `--halt-scope subtree` (default) — as described above.
- `--halt-scope nothing` — log the failure, continue running every other op (best-effort deploy).
- `--halt-scope everything` — halt all in-flight work on first terminal failure. Useful for CI builds wanting fast-fail semantics.

### 5.4 Partial deploys are first-class

A partial deploy is a normal state, not a weird one. The state store records per-op status (`completed | failed | not-started | halted-due-to-upstream`). Re-running `axrange deploy` resumes from where the previous attempt left off:

- `completed` ops are fast no-ops (idempotency contract; see §4).
- `failed` and `halted-due-to-upstream` ops re-run.
- `not-started` ops run normally.

This means "the deploy partially failed" is not a recovery scenario — it's just a deploy that needs another invocation after the underlying problem is fixed.

### 5.5 Operator-facing failure output

On deploy end, the planner writes a tree view of the result:

```
✓ network/corp
✓ network/dmz
✓ host/h-dmz-web (ready)
✗ host/h-fin (failed: clone-vm escalated twice)
   ⊘ activity/act-fin-workday (halted: depends on h-fin)
✓ host/h-corp-dc (ready)
   ⊘ overlay/defense/edr-corp-fleet (halted: requires h-fin)
```

Status symbols: ✓ completed, ✗ failed, ⊘ halted-due-to-upstream, ⋯ in-progress (during deploy).

---

## 6. Planner runtime

### 6.1 Process model

**Ephemeral process per deploy.** Each `axrange deploy scenario.yaml` invocation starts a planner process that builds the DAG, executes to completion or terminal failure, writes final state, and exits.

By default, the process is **attached** — the CLI shows live progress and exits when the deploy ends. With `--detach`:

1. The CLI forks the planner into the background.
2. The CLI returns immediately, printing the `deploy_id`.
3. The detached planner runs to completion; the operator queries via `axrange status <deploy-id>`.

The state store is authoritative. Nothing important lives only in process memory; the planner can crash at any time and a new invocation can resume.

### 6.2 Lease/lock model

When a planner process starts a deploy of scenario `<scenario-id>`, it takes a lease in the state store:

```
lease:
  scenario_id: ransomware-hospital
  deploy_id: 01HGXR5K...
  process_info:
    host: orchestrator-1
    pid: 4827
    started_at: 2026-05-21T18:30:00Z
  expires_at: 2026-05-21T18:35:00Z  # heartbeat-renewed every minute
```

Behavior:

- A planner process **heartbeats** every minute, extending `expires_at` to (now + 5min).
- A second invocation against the same scenario **refuses to start** while a valid lease exists (returns "deploy already in progress, deploy_id=...").
- If the planner process crashes, the lease expires after 5 minutes; a new invocation can take the lease and resume from state-store status.

This is single-writer per scenario by construction. Multiple deploys can run concurrently if they target different scenarios.

> **Generalized to resources (2026-06-03).** The same lease mechanism (DB row, atomic takeover, heartbeat, expiry-on-crash) is reused for **cross-process resource locks** on the concurrency model's "correctness" categories — see §8.4 and §7.2's `resource_locks` table. The per-scenario lease here protects against double-deploying *one* scenario; resource locks protect *shared* resources against *different* concurrent scenarios. **Implementation status (2026-06-03, implementation Authority):** both are now **built** — `axrange/planner/state.py` (the `leases` + `resource_locks` tables, atomic acquire/renew/release) and `axrange/planner/locks.py` (`acquire_blocking` poll-until-held, `Heartbeat` renewal thread). The CLI takes the lease before mutating deploy state and refuses a second concurrent deploy of the same scenario; the executor acquires each op's correctness locks before running and releases them on any terminal outcome, holding them across retries. The executor stays internally sequential.
>
> **Split-brain abort on lease loss (fixed 2026-07-07, CHANGELOG).** `Heartbeat.lost` is a
> `threading.Event` that is set **on lease-renewal failure alone** — the one race-free signal that
> this process may no longer be the single writer. The executor polls `lost` between ops and aborts
> the deploy (rather than continuing to act while a second process may have taken over). This is
> wired at the sole `Executor` construction site (`run_deploy_executor`), so the guard is always live
> wherever a deploy actually executes. An earlier version additionally tried to detect split-brain
> from a **per-resource-key holder diff** (did some other deploy now hold a lock this process
> expected to hold?) — that check was **dropped as racy**: it false-positived whenever a legitimate
> concurrent *different-scenario* deploy took a shared correctness key it was entitled to, aborting a
> perfectly healthy deploy. Lease-loss is the only signal that's actually specific to *this*
> process's own writer status.

### 6.3 The CLI surface

Implied by the design across Q4(a), Q4(b), and prior decisions:

| Command | Purpose |
|---|---|
| `axrange deploy <scenario.yaml>` | Run a deploy. Builds DAG, executes. |
| `axrange status <deploy-id>` | Show tree view of a deploy's current state. |
| `axrange list-deploys` | List recent deploys with status. |
| `axrange cancel <deploy-id>` | Mark deploy for graceful shutdown (current ops finish, no new ops start). |
| `axrange validate <scenario.yaml>` | Run validator only; no deploy. |
| `axrange plan <scenario.yaml>` | Build the DAG and show it, but don't execute. Same as `deploy --dry-run`. |

Deploy-time flags (full set):

| Flag | Source | Default | Purpose |
|---|---|---|---|
| `--detach` | Q4(b) | off | Fork to background. |
| `--dry-run` | Q4(a) | off | Build plan; don't execute. |
| `--max-retries N` | Q4(a) | 3 | Per-op retry attempts. |
| `--retry-backoff TYPE` | Q4(a) | `exp:5s:3x:45s` | Backoff strategy. |
| `--no-auto-escalate` | Q4(a) | off | Disable automatic escalation. |
| `--halt-scope SCOPE` | Q4(a) | `subtree` | Halt scope on terminal failure: `subtree` \| `nothing` \| `everything`. |
| `--allow-destroy` | Q4(b) | off | Permit decommission ops (VM destroy, vnet destroy) for entities removed from the IR. |

---

## 7. State store

### 7.1 Backing

**SQLite file** on the orchestrator host. One file per axRange installation; default path `/var/lib/axrange/state.db` (configurable).

Rationale: transactional, queryable, zero deployment overhead, one-file backups, single-writer fits our model. Migration to PostgreSQL later remains a clean port if scale demands.

### 7.2 Schema sketch

(Implementation will likely refine; this is the conceptual model.)

**`deploys`** — one row per `axrange deploy` invocation:

| Column | Type | Notes |
|---|---|---|
| `deploy_id` | text PK | ULID or UUID. |
| `scenario_id` | text | From IR. |
| `scenario_version` | int | From IR. |
| `scenario_schema_version` | int | From IR. |
| `ir_hash` | text | SHA-256 of the full resolved IR (post-includes). Used by re-planning to detect changes. |
| `status` | enum | `running` \| `completed` \| `failed` \| `cancelled`. |
| `placement` | JSON | Map from host node ID → Proxmox node name. |
| `started_at` | timestamp | |
| `updated_at` | timestamp | |
| `completed_at` | timestamp | NULL while running. |

**`leases`** — one row per active deploy:

| Column | Type | Notes |
|---|---|---|
| `scenario_id` | text PK | One lease per scenario. |
| `deploy_id` | text FK | The active deploy. |
| `process_info` | JSON | host, pid. |
| `acquired_at` | timestamp | When the lease was taken. |
| `expires_at` | timestamp | Heartbeat-renewed. |

**`resource_locks`** — one row per held correctness-resource lock (added 2026-06-03; see §8.4):

| Column | Type | Notes |
|---|---|---|
| `resource_key` | text PK | E.g. `ovs:node-3`, `vm:1204`, `gretap:sensor2`. The lock identity. |
| `deploy_id` | text FK | Holder. |
| `op_id` | text FK | The op currently holding it (released on op completion). |
| `process_info` | JSON | host, pid. |
| `acquired_at` | timestamp | When the lock was taken. |
| `expires_at` | timestamp | Heartbeat-renewed; crashed holder's lock reclaimable on expiry. |

Only the §8.4 **correctness** categories (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`) take rows here; throughput limits stay in-process. Acquisition is the same atomic conditional-insert/UPDATE pattern as lease takeover (§7.3): a single transaction checks **all** requested keys are free (absent, expired, or already this deploy's) and only then writes them all — all-or-nothing, so a process never holds a partial set while waiting (no cross-process deadlock, §8.3). The implementation is `StateStore.acquire_resource_locks` (`axrange/planner/state.py`).

**`ops`** — one row per op instance:

| Column | Type | Notes |
|---|---|---|
| `op_id` | text | Deterministic; same across replays of the same plan. |
| `deploy_id` | text FK | |
| `op_type` | text | E.g. `clone-vm`. |
| `kind` | enum | `action` \| `wait` \| `barrier`. |
| `scope` | JSON | The IR entity(ies) this op targets. |
| `inputs_resolved` | JSON | Inputs after resolution from upstream outputs. |
| `outputs` | JSON | Populated on completion. |
| `cleanup_strategy` | enum | `redo` \| `escalate`. |
| `status` | enum | `pending` \| `running` \| `completed` \| `failed` \| `halted-due-to-upstream`. |
| `started_at`, `completed_at` | timestamp | |

**PRIMARY KEY is composite `(deploy_id, op_id)`, not `op_id` alone (fixed 2026-07-07, CHANGELOG).**
`op_id` is deterministic *per plan* (§4.2/§10) — it does not vary across two separate deploys of the
same plan. A bare `op_id` PK therefore made two deploys of one plan **alias the same row**: `--fresh`
mints a new `deploy_id` but the old resume check (`get_op(op_id)`) found the prior deploy's
`completed` row and skipped re-running it — and a `destroy` after such a `--fresh` swept nothing
(no rows uniquely belonged to the new deploy) yet still reported DESTROYED, a live-resource leak.
The composite key gives every deploy its own independent op rows; `StateStore` methods
(`get_op`/`set_op_running`/`complete_op`/`fail_op`/`halt_op`/`start_attempt`/`attempts_for_op`) are
now all `deploy_id`-scoped. This closes LES-007.

**`op_attempts`** — many per op (typically 1, up to `max_attempts`):

| Column | Type | Notes |
|---|---|---|
| `attempt_id` | int PK | Per-op sequence. |
| `deploy_id` | text FK | Added alongside the `ops` composite-PK fix — attempts are scoped the same way. |
| `op_id` | text FK | |
| `started_at`, `ended_at` | timestamp | |
| `result` | enum | `success` \| `error` \| `timeout` \| `cancelled`. |
| `error_message` | text | NULL on success. |
| `error_class` | text | E.g. `proxmox-api-error`, `ssh-connection-error`. For grep-friendly debugging. |

**Schema version guard (added 2026-07-07).** A module-level `SCHEMA_VERSION` constant (currently `2`,
for the composite-PK change) is stamped into the DB via `PRAGMA user_version` at init. On open,
`StateStore` compares the stored `user_version` against `SCHEMA_VERSION`: a match proceeds normally; a
mismatch against an older (pre-2) DB raises `StateStoreSchemaError` — fail loud rather than running
degraded logic against an incompatible schema. **Migration:** there is no in-place migration path for
v1 → v2; re-initialize (delete/move) an existing `state.db` predating this change. Fresh-DB
initialization is atomic: the table/index DDL and the `PRAGMA user_version` stamp are issued as one
`executescript()` call (which auto-commits any pending transaction as part of running the script), so
a concurrent opener never observes a half-created schema with `user_version` still `0`.

### 7.3 Atomicity guarantees

- **Op completion is atomic with output persistence.** A single transaction updates `ops.status = completed`, `ops.completed_at`, and `ops.outputs`. Crash-then-restart cannot read "this op is complete but its outputs are missing."
- **Lease takeover is atomic.** Acquiring a lease when the previous one has expired is a single conditional UPDATE.
- **Heartbeat updates are non-blocking.** A heartbeat is a single row update; it never blocks op completion writes.
- **Schema init is atomic.** DDL + `PRAGMA user_version` are one `executescript()` transaction (§7.2) — no window where a fresh DB has tables but an unstamped version.

### 7.4 Inspection

Operators can inspect the state store directly with SQLite tooling. Sample queries:

```sql
-- Failed ops in the latest deploy
SELECT op_type, scope, error_class
FROM ops
WHERE deploy_id = (SELECT deploy_id FROM deploys ORDER BY started_at DESC LIMIT 1)
  AND status = 'failed';

-- Average clone time across all deploys
SELECT AVG(julianday(completed_at) - julianday(started_at)) * 86400 AS avg_secs
FROM ops
WHERE op_type = 'clone-vm' AND status = 'completed';
```

This is intentional — the state store doubles as an operations diagnostic surface.

---

## 8. Concurrency model

The DAG constrains ordering. Among the ops that *could* run in parallel at any moment, the planner picks how many actually run, subject to per-resource semaphores.

> **Scope (2026-06-03):** the semaphores in §8.1–§8.3 are **in-process** — they govern parallelism *within one deploy's planner process*. Because §6.1 runs an ephemeral process per deploy and §6.2 allows concurrent deploys of *different* scenarios, the categories below marked "serialization is correctness" do **not** serialize across concurrent deploy processes by themselves. §8.4 closes that gap with DB-backed cross-process locks for the correctness categories. The **numbers** in §8.2 remain in-process throughput caps (and remain empirical guesses to tune on a real cluster).

### 8.1 Resource categories

Each op declares the resources it uses. Categories:

| Category | Scope | Reason for serialization |
|---|---|---|
| `proxmox-cluster` | Cluster-wide | Proxmox API rate limits. |
| `proxmox-node:<name>` | One Proxmox node | Local resource pressure (clones, disk I/O). |
| `ovs:<name>` | One Proxmox node's OVS instance | OVS commands on the same OVS should serialize (avoid weird interactions). |
| `vm:<vmid>` | One VM | Two ops mutating the same VM must serialize. |
| `ansible-pool` | Across the orchestrator | Cap on Ansible playbook concurrency from the orchestrator. |
| `gretap:<sensor>` | One sensor's gretap transport interface | Kernel netlink ops attaching/detaching GRE tunnels on a sensor's gretap iface serialize; concurrent mirror-plumbing ops to the same sensor would race kernel-side. (Added 2026-05-27 with the op catalog; used by `OP-MIR` ops.) |

### 8.2 Default limits

| Category | Default | Operator override |
|---|---|---|
| `proxmox-cluster` | 16 | `--limit-cluster N` |
| `proxmox-node:<name>` | 4 | `--limit-per-node N` |
| `ovs:<name>` | 1 | (fixed at 1; serialization is correctness, not tuning) |
| `vm:<vmid>` | 1 | (fixed at 1; serialization is correctness) |
| `ansible-pool` | 8 | `--limit-ansible N` |
| `gretap:<sensor>` | 1 | (fixed at 1; serialization is correctness) |
| (global cap) | 32 | `--max-ops-in-flight N` |

The global cap is defense-in-depth: even if per-category limits sum to 100, no more than `max_ops_in_flight` ops run concurrently. Protects against misconfiguration.

### 8.3 Semaphore acquisition

An op runs when:

1. All its DAG predecessors are `completed`, AND
2. It can acquire a slot in **every** resource category it declares, simultaneously.

Acquisition is FIFO per category. The planner doesn't optimize for op cost or priority in v1 — first eligible op wins. (Priority is a CHANGELOG-able addition if it ever matters.)

If an op holds two semaphores and is waiting for a third, it blocks but does not release the held ones. This can in principle deadlock if two ops circularly wait. The planner detects this at DAG construction time (cycle detection across resource dependencies) and refuses to plan.

### 8.4 Cross-process resource locking (ratified 2026-06-03)

The §8.1–§8.3 semaphores are per-process; the target operational profile runs **many concurrent deploys** (different scenarios) as separate processes. To make the "correctness" serializations hold *across* those processes, the categories split:

| Class | Categories | Mechanism |
|---|---|---|
| **Correctness** | `ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>` | **DB-backed cross-process locks** (required). |
| **Throughput** | `proxmox-cluster`, `proxmox-node:<name>`, `ansible-pool`, global `max-ops-in-flight` | **per-process semaphores** (§8.2). Aggregate over-subscription across deploys is a rate-limit/perf risk the cluster API absorbs, not a correctness bug. |

**Mechanism — the §6.2 lease, generalized to resources.** A `resource_locks` table in the state store (§7.2) reuses the scenario-lease machinery: before acting on a correctness resource, a planner process acquires that resource's DB lock (insert-if-absent keyed by holder `deploy_id`; released on op completion; heartbeat + expiry identical to the scenario lease, so a crashed holder's lock is reclaimable). Serialization holds across processes because the lock lives in the authoritative store, not process memory. SQLite's single-writer model makes this native, and contention is low because these resources are "1"-limited by definition.

**Why correctness, not just throughput.** The per-scenario lease (§6.2) stops two deploys of the *same* scenario; it does nothing for *different* scenarios contending on a *shared* resource — a node's OVS, a sensor's gretap, or the cluster-wide `apply-sdn-config` (sharpened by the 2026-06-03 SDN-native decision). Capture-plane races in particular can be **silent** (lost frames, not a clean error retry would catch).

**Sequencing.** The lease + resource-lock layer is what makes running many deploys at once *safe*, and is independent of *intra-deploy* parallelism. It lands **first**; each deploy can keep executing **internally sequentially** (the current slice executor, unchanged). A parallel intra-deploy executor + tuned throughput semaphores (which make each deploy *faster*) is separable and deferred until deploy latency on a large twin warrants it.

**Detecting a lost lease mid-deploy (2026-07-07):** holding a resource lock is necessary but not
sufficient for correctness if this process's *scenario lease itself* silently expired (e.g. a
transient DB/network blip missed a heartbeat renewal) — see §6.2's `Heartbeat.lost` abort. The
executor checks `lost` between ops and aborts rather than continuing to mutate cluster state as a
possible second writer.

See CHANGELOG 2026-06-03 "cross-process resource locking" and CHANGELOG 2026-07-07 (code-review fix
batch) for the lease-loss abort.

**Implementation (2026-06-03, implementation Authority).** Built in `axrange/planner/`: `state.py` holds the `leases`/`resource_locks` tables + atomic acquire/renew/release; `locks.py` holds `correctness_lock_keys` (resolves an op's correctness `resource_categories` `$var` templates against scope + resolved inputs + `cluster.sensor_id`, returning a *sorted* key list so every process acquires in one global order), `acquire_blocking` (poll-until-held with a timeout — a contended op fails cleanly rather than hanging, since a crashed holder releases via expiry), and `Heartbeat` (a daemon thread on its own DB connection renewing the lease + held locks each minute). The executor (`executor.py`) acquires an op's correctness locks before its first attempt and releases them on any terminal outcome; the CLI (`cli/main.py`) takes the per-scenario lease before mutating deploy state, runs the heartbeat for the deploy's lifetime, and releases locks + lease in a `finally`. Throughput categories are not locked. Covered by 22 unit tests (`tests/test_planner_locks.py`, `tests/test_planner_executor.py`). Live-verify (two concurrent deploys on a real cluster) pending.

---

## 9. Re-planning

When the IR changes between deploys (you edit `scenario.yaml` and rerun `axrange deploy`), the planner detects this via `ir_hash` and runs a **plan diff**.

> **A reduced subset of this section is now IMPLEMENTED** (`axrange te deploy --update`,
> `docs/WORK_ORDER_twin_incremental_update.md`, CHANGELOG 2026-08-11; code/tests/docs complete,
> gated `dev45` live-verify pending): whole-node **Added**/**Removed**/**Unchanged** (§9.1) against a
> deploy whose `plan_id` is explicitly PINNED (never freshly re-derived), with **Mutated** (§9.1)
> always a hard refusal naming the op/field/old→new value — §9.2's per-field redo-rules table below
> is **NOT** implemented, so a genuine field-level Mutated change is refused, never redone. §9.3's
> confirmation UX is implemented in reduced form (grouped by entity, added/removed only — no `↺`
> mutation rows, since Mutated never reaches "apply," only "refuse"). §9.4 (first-deploy semantics)
> is unchanged, pre-existing behavior. Sibling-tier (`tap`/`tfc`/`exr`) reconciliation on a removed
> host is warn-only, not automatic. See `docs/PRODUCT_GAPS.md` GAP-040 and `docs/LESSONS.md` LES-126.
>
> **Narrow content-only carve-out, 2026-09-02** (`axrange te deploy --update --reapply`,
> `docs/WORK_ORDER_responder_op_row_drift.md` option A, CHANGELOG 2026-09-02, Authority: user): the
> ONE `apps[*].install` row of §9.2's table below is now implemented, but only for exactly
> `run-script` (`OP-APP-030`) when the ONLY differing resolved input is `script_content` — an
> opt-in `--reapply` flag re-executes it and persists the new content, instead of refusing. Every
> other field, on `run-script` or any other op, remains the hard refusal described above.
> `provision-app`/`pkg:` (the same-shaped sibling) is deliberately out of scope. The eligibility
> gate and its refusal message are a single function (`plandiff.reapplyable_ops` /
> `reapply_ineligibility_reason`) so they cannot drift apart. `docs/PRODUCT_GAPS.md` GAP-054 tracks
> the general defect this closes one instance of: a standing deploy's persisted op inputs can
> silently stop describing its guests after an out-of-band content push, and `--reapply` only
> repairs the state store going forward from its own use, never retroactively.

### 9.1 Change classification

For each IR entity (node, app, activity, defense, identity, data):

| Class | Trigger | Result |
|---|---|---|
| **Unchanged** | Same ID, same fingerprint of relevant fields | Skip all ops for this entity; reuse prior state. |
| **Mutated** | Same ID, different fingerprint | Redo a subset of ops per the redo rules table (§9.2). |
| **Added** | New ID not in prior IR | Run all ops normally. |
| **Removed** | Prior ID not in new IR | Run decommission ops (gated behind `--allow-destroy`). |

Fingerprint = stable hash of the entity's IR fields that affect deployment behavior. Some fields (`description`, `labels`) don't affect deployment and don't contribute to the fingerprint.

### 9.2 The redo rules table

A sibling deliverable to this spec (not yet written; see §11). Maps "which field of which entity, when changed, forces what redo." Examples (illustrative, not exhaustive):

| Entity field | Redo from |
|---|---|
| `nodes[*].image` | `clone-vm[<host>]` — full host rebuild |
| `nodes[*].requires` | Revalidate; may force `clone-vm` if new caps require a different image |
| `nodes[*].resources` | `configure-vm-hardware[<host>]` only — no clone |
| `nodes[*].tap` | `install-ovs-mirror[<host>]` only |
| `nodes[*].net` / `nets` | `attach-vm-to-vnet[<host>]` redo; may force VM restart |
| `apps[*].install` | Redo affected install action ops — **`run-script`/`script_content`-only slice IMPLEMENTED behind `--reapply`, 2026-09-02** (see the §9 blockquote above); `provision-app`/`pkg:` and every other field/op combination still hard-refuses |
| `apps[*].listen`, `apps[*].env` | Redo `provision-app[<app>@<host>]` configuration only |
| `bindings[<app>→<host>]` change | Add/remove `provision-app` ops |
| `overlays.defense[*].observes` / `installed_on` | Redo defense provisioning |
| `overlays.identity[*]` | Redo identity provisioning; may cascade to dependent activities |

Some fields force a full subtree redo via `escalate` semantics — e.g. changing `nodes[*].image` is treated as "the prior VM is no longer the correct artifact; destroy and re-clone."

### 9.3 Confirmation UX

Default behavior is "show diff, require confirmation." Operator sees:

```
Plan diff for ransomware-hospital v7 → v8:

  Unchanged: 6 entities (47 ops, all `completed` — will skip)

  ↺ host/h-fin
     configure-vm-hardware  (resources changed: ram 4Gi → 8Gi)
     + install-app-action   (new install: pkg postgres-exporter)

  + activity/act-attacker-recon-v2
     start-activity         (new activity)

  - overlay/defense/snort-perimeter
     uninstall-defense-tap  (requires --allow-destroy)

Apply? [y/N]
```

The display surfaces every redo and every add/remove. Removals that need `--allow-destroy` are flagged inline.

### 9.4 First-deploy semantics

If no prior deploy exists for this scenario (no row in the `deploys` table for `scenario_id`), the planner treats every entity as `Added` — every op runs. No diff is shown; the deploy proceeds normally.

---

## 10. Determinism and reproducibility

Key invariants:

- **DAG construction is deterministic.** Same IR + manifests + cluster snapshot → same DAG, same `op_id`s, same placement decisions.
- **Op IDs are stable.** Re-running the same plan produces the same `op_id`s; idempotency works because the state store can find prior runs by ID.
- **Plan diff is reproducible.** Same prior state + same new IR → same diff.
- **The DAG is hashable.** A plan can be hashed (e.g. SHA-256 of the topologically-sorted op-id + edge list); two scenarios that produce the same DAG hash are deployment-equivalent.

This supports:

- **Reproducible builds** for compliance / audit scenarios (`reproducible: true` IR + same manifests + same cluster snapshot → byte-identical DAG).
- **Plan caching** as a future optimization (re-validate quickly by comparing hashes).
- **Test reproducibility** — planner unit tests can assert "this IR produces this DAG."

---

## 11. Open follow-ups (sibling deliverables)

Tracked here for visibility; will become their own docs / CHANGELOG entries:

- **Op catalog.** Closed list of op types with full signatures (inputs, outputs, `after:`, `cleanup_strategy`, `idempotency_class`, implementation sketch). Mostly mechanical now that the framework is set. ~30-50 op types expected.
- **Redo rules table.** Per-IR-field redo specifications (§9.2). Smaller than the op catalog. Adjacent.
- **Plan-diff confirmation UX details.** Display format, interaction model, what's actionable. Touched in §9.3 but needs operator-experience iteration.
- **Validator rule catalog.** OPEN_QUESTIONS Q3. Likely shares DAG-construction code with the planner.
- **Concurrency-limit empirical defaults.** Current values (§8.2) are guesses; real numbers come from operational experience. Tune via CHANGELOG.
- **Cancel/pause/resume semantics** beyond the single `axrange cancel`. E.g. "cancel deploy_id, then resume from same point" — not v1.
- **Priority and op cost models** for FIFO acquisition. CHANGELOG-able when it matters.
- **PostgreSQL migration path** if SQLite scale becomes a limit.
