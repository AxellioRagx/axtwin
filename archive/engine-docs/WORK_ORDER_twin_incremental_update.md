# Work Order — Incremental twin update: add and remove nodes on a standing `te` deploy

> **STATUS: DONE — build 2026-08-11, gated `dev45` live-verify PASSED 2026-08-14** (all four §6
> proofs, plus the without-`--allow-destroy` refusal and the S10 tap addendum; see CHANGELOG
> 2026-08-14 "W2 live-verify"). **Closes `docs/PRODUCT_GAPS.md` GAP-040 outright.** Authority: **user (2026-08-04)** — scope
> decided by the user (add + remove only; in-place mutation explicitly out). M1's fix approach (move
> `configure-router`'s `served_hosts` roster out of persisted op inputs into a new
> planner-internal-only field) is Authority: **user, 2026-08-11**. **Route:** Pipeline (`/dev`) —
> code-writer ×3 → tester → code-reviewer ×2 → doc-writer (this pass) → **gated live-verify next**.
> **Version-locked:** none — **no `IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/
> `IMAGE_MANIFEST` schema change, no new op type, no state-store schema bump.** `docs/PLANNER.md` §9
> already specifies this behavior; this work order builds a deliberately reduced subset of it (see
> the note added there). **Closes `docs/PRODUCT_GAPS.md` GAP-040 outright (build 2026-08-11; live-verify PASSED 2026-08-14).**
> **Spec:** this file + `docs/PLANNER.md` §9. Sequenced **after**
> `docs/WORK_ORDER_role_persona_tier.md` (W1).
>
> **Build record:** `axrange/planner/plandiff.py` (new) + changes across
> `planner/{dag,pass1_emit,pass3_ordering,state,teardown,__init__}.py`, `api/{service,dtos}.py`,
> `cli/main.py`, `o2control/cascade.py`; new tests `tests/test_plandiff.py` +
> `tests/test_twin_incremental_update.py`. Took three code rounds, each review round finding real
> defects a green suite had already certified — see CHANGELOG 2026-08-11 and `docs/LESSONS.md`
> LES-178 through LES-187.

## 1. The problem (GAP-040)

Editing a deployed twin config to add or remove a node does not extend the standing twin. Re-running
`te deploy --config <id>` against an edited config computes a **different `plan_id`** and starts a
whole new deploy of every node.

Root cause: `derive_plan_id(scenario_id, ir_hash)` (`planner/dag.py:292`) keys plan identity on the
**content hash of the expanded IR**. Any edit — additive or not — changes that hash. There is no
plan-diff layer to reconcile "this is the same twin, plus one node" against a standing deploy's
persisted state; `docs/PLANNER.md` §9 has specified one since v1 and it has never been built.

The operational cost is real and was paid live: the 2026-07-25 dev45 Stage-5 live-verify needed an
attacker host on a standing 32-host `corp-enterprise` twin, and the only options were a full ~30-VM
redeploy or hand-provisioning outside the twin-config model. The run took the second, and the
authored config and the live topology diverged as a result (LES-126).

## 2. Why it is mechanically available

Three properties of the existing engine make a diff tractable, and one makes it dangerous.

**2.1 Op identity is already entity-scoped and deterministic.** `_op_id(plan_id, op_name, scope)`
produces `<plan_id>:<op-name><scope>` (`dag.py:59-60`). Two IRs that share a `plan_id` and both
contain host `h-a` produce **the same** `clone-vm[h-a]` op id. So "what's new / what's gone" is a set
difference over op ids — not a bespoke entity differ.

**2.2 Half the diff is already implemented.** Resume already skips `completed` ops with no adapter
call and retries `failed`/`halted` ones. That *is* §9.1's **Unchanged** class. And `ops`/`op_attempts`
carry a composite `(deploy_id, op_id)` PRIMARY KEY since `SCHEMA_VERSION=2` (2026-07-07), so new op
rows can be inserted into an existing deploy without aliasing anything.

**2.3 Teardown is outputs-driven.** `planner/teardown.py` sweeps resources recorded in persisted op
**outputs**, so removing one node is a *scoped subset* of the existing destroy sweep rather than new
machinery.

**2.4 The danger: `plan_id` determines VMIDs and VM names.**
`vmid_for_op(plan_id, op_id)` hashes `f"{plan_id}:{op_id}"` (`naming.py:250-262`), and
`vm_name_for_host(plan_id, host_id)` is likewise plan-keyed — as are the SDN zone
(`zone_id_for_plan`), the vnet (`vnet_id_for_net(..., plan_id=...)`, made twin-unique 2026-07-27) and
the underlay bridge. **Minting a new `plan_id` renumbers and renames every resource in the twin.**
This is the single constraint the whole design hangs on.

## 3. The decided model (Authority: user, 2026-08-04)

`axrange te deploy --config <id> --update [--allow-destroy]` (equally `te deploy <scenario.yaml>
--update`).

**Pin the standing deploy's `plan_id`.** Do not derive a new one. Build the DAG for the edited IR
*under the existing plan_id*, so every unchanged op keeps its exact op id, every existing VM keeps its
VMID and name, and the diff reduces to a set comparison.

### 3.1 The four classes (a reduced `docs/PLANNER.md` §9.1)

| Class | Detection | v1 behavior |
|---|---|---|
| **Unchanged** | op id present in both, row `completed`, resolved-inputs fingerprint equal | skip — today's resume path, unchanged |
| **Added** | op id in the new DAG, no row in `ops` for this deploy | execute normally |
| **Removed** | op row persisted for this deploy, op id absent from the new DAG | run cleanup from persisted outputs — **gated behind `--allow-destroy`** |
| **Mutated** | op id present in both, row `completed`, resolved inputs **differ** | **fail loud**, naming the op, the field, and old → new value. Refused in v1. |

**Mutation is refused, not silently redone.** `docs/PLANNER.md` §9.2's redo-rules table (which field
of which entity forces what redo) is the expensive and dangerous half — getting one row wrong
silently corrupts a live twin. It is deferred to a follow-on (`--allow-redo`). The refusal message
must name the specific op and field so the operator can decide between reverting the edit and doing a
full redeploy. **Do not** approximate a redo rule for "easy" fields; a partially-correct redo table is
worse than none.

### 3.2 Removal is a set difference, and that is what makes it safe

Shared resources need **no refcounting layer**. The removed set is `persisted op ids − new-DAG op
ids`. If a network still has surviving nodes, its `create-vnet[<net>]` op is still in the new DAG,
so it is not in the removed set and is never swept. If the last node on a segment goes away and the
segment leaves the IR too, its vnet ops fall out of the new DAG and *are* swept. The existing
per-resource, name-guarded, check-then-act teardown then does the actual work.

Two things this must still get right:
- **Reverse dependency order.** Sweep the removed set in reverse of the new DAG's ordering, exactly as
  `destroy_deploy` does — a VM before the vnet it attaches to.
- **The protect guard still applies.** `teardown.py`'s `_protected()` must run over the removed set
  unchanged. An incremental update is not a licence to touch `o2`/`img`/external-sink resources.

### 3.3 Barriers

Barriers are recomputed from the new DAG. A barrier whose **predecessor set changed** (e.g.
`host-ready[$host]` gained a new host, `network-ready` lost one) must be treated as **Added** and
re-evaluated rather than left `completed` — barriers are cheap, and a stale-satisfied barrier is how a
new node gets released before its network is ready.

### 3.4 What is recorded

On success, update the deploy row's `ir_hash` to the new IR's hash and leave `plan_id` pinned. The
`deploys` table needs no new column. Note the consequence honestly in the docs: after an incremental
update, `deploys.plan_id` no longer equals `derive_plan_id(scenario_id, ir_hash)` — plan id becomes
the twin's **stable identity**, and `ir_hash` becomes its **current generation**. That is the intended
meaning, but it invalidates any code that recomputes a plan id and expects it to match a standing
deploy; sweep for that assumption (`find_deploy_for_plan`'s callers) as part of the build.

### 3.5 Sibling tiers (honest v1 limit)

A standing `tap`/`tfc`/`exr` deploy may reference a host that an update just removed — including, after
W1, that host's `assign-role` row. v1 **warns loudly**, listing every affected sibling deploy and the
dangling hosts, and tells the operator to re-apply. Auto-reconcile is a follow-on. Do not silently
leave a `tfc` deploy driving a persona at a dead host.

## 4. Concrete changes

### 4.1 Planner

- New `planner/plandiff.py` — pure, no I/O: given the new `Plan` and the persisted op rows, return
  `{unchanged, added, removed, mutated}` op-id sets plus, for each mutated op, the differing input
  keys. Keep it a pure function so the whole classification is unit-testable without a DB or cluster.
- Deploy-creation path: when `--update` is set, resolve the standing deploy **by scenario_id**, pin its
  `plan_id` into DAG construction, run the diff, and refuse on any mutated op unless the set is empty.
- A small new state-store lookup is needed — `get_deploy`/`latest_deploys` are the only accessors
  today (`state.py:477,484`) and neither is scenario-scoped. The `deploys_by_scenario` index already
  exists; add the query, do not scan `latest_deploys`.

### 4.2 Teardown

- `destroy_deploy` grows a scoped variant taking an explicit op-id set (the removed set), reusing the
  same reverse-order sweep, the same `_protected()` guard, and the same idempotent check-then-act
  handlers. **Reuse, do not fork** the existing sweep (LES-037).

### 4.3 CLI / API

- `te deploy --update` and `--allow-destroy`. `--update` and `--fresh` are **mutually exclusive** —
  error if both.
- Print the diff **before** executing, in `docs/PLANNER.md` §9.3's shape (counts of unchanged, then
  each added/removed entity), and require confirmation when the removed set is non-empty. `--yes` for
  non-interactive use.
- API peer on the deploy-creation endpoint, with the same refusal semantics.

### 4.4 Lease

Unchanged — the update runs under the same per-scenario lease as any deploy, with heartbeat renewal
across the whole run (including the removal sweep, which follows the 2026-07-29 cascade-ordering fix's
precedent of keeping teardown work inside the heartbeat window).

## 5. Out of scope

- **In-place mutation of an existing node** (`docs/PLANNER.md` §9.2's redo-rules table). Refused
  loudly; deferred to a `--allow-redo` follow-on.
- **Auto-reconciling sibling tiers** (§3.5) — warn only.
- **A twin generation counter / update history.** `ir_hash` carries the current generation; a proper
  history would need a state-store schema bump and is not justified yet.
- **`exr` replay identity.** `playout_hash()` keys on the exercise spec, not the twin, so an
  incremental twin update does not invalidate a replay hash directly. Note in the docs that "the same
  twin" is now a looser claim across an update, and leave the semantics alone.

## 6. Validation

**Unit/suite:**
1. `plandiff` classification: added / removed / unchanged / mutated, including an op present but
   `failed` (must be retried, not classified as unchanged).
2. Pinned `plan_id` ⇒ every surviving op id, VMID, VM name, zone, vnet and bridge name is **identical**
   before and after an update. Pin this hard; it is the safety property of the whole design.
3. Additive edit ⇒ removed set is empty and no existing op re-executes (assert zero adapter calls for
   unchanged ops against a recording backend).
4. Removing the last node on a segment sweeps that segment's vnet; removing one of several does **not**
   (§3.2, both directions).
5. Mutation refusal names op + field + old → new, and nothing executed.
6. Removal without `--allow-destroy` refuses and executes nothing.
7. `_protected()` still refuses `o2`/`img`/external-sink resources inside a removal sweep.
8. A barrier whose predecessor set changed is re-evaluated.
9. `--update` + `--fresh` errors.

**Gated `dev45` live-verify (mandatory — this deletes real VMs):**
Against a standing multi-node twin: (a) add a node → only the new node's ops run, every pre-existing
VM keeps its vmid/uptime, the new node reaches `host-ready` and has network; (b) remove that node with
`--allow-destroy` → its VM and NIC are gone, **every neighbour is untouched**, the shared vnet
survives; (c) re-run the same update → clean no-op; (d) a bound `tap`/`tfc` deploy produces the §3.5
dangling-host warning rather than a silent stale binding.

## 7. Depends on / sequence

- **Depends on:** `docs/WORK_ORDER_role_persona_tier.md` (W1) ships first — its `assign-role` rows are
  part of what §3.5 must report as dangling. Nothing else unbuilt.
- **Enables:** the iterative exercise-authoring workflow GAP-040 blocks — grow a standing twin with an
  attacker host, then tear just that host back out.
- **Follow-on:** `--allow-redo` (§9.2 redo rules), sibling-tier auto-reconcile.
