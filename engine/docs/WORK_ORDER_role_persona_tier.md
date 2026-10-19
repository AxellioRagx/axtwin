# Work Order — Role and persona become `tfc`-owned; the twin substrate becomes behavior-agnostic

> **STATUS: DONE (code / tests / docs / live-verify). Gated `dev45` live-verify PASSED 2026-08-04** (all 8 proofs; 4 bugs found + fixed during the run, see CHANGELOG 2026-08-04). Authority: **user (2026-08-04)** — the decoupling and the "no sixth tier" call
> were proposed by implementation (Claude Code) in-session and accepted by the user; the inert-field
> sweep was requested by the user directly. **Route:** Pipeline (`/dev`) — code-writer → tester →
> code-reviewer → doc-writer → **gated** live-verify (a standing twin is required to prove a
> role/persona swap touches no VM). **Version-locked:** `OP_CATALOG` only (39 → 40 op types, new
> `OP-TFC-020 assign-role`) — sign-off required. **No `IR_SCHEMA` change, no
> `scenario_schema_version` bump, no `VALIDATOR` rule-count change, no state-store schema bump.**
> **Spec:** this file + `docs/design/07-traffic-personas.md`. Sequenced **before**
> `docs/WORK_ORDER_twin_incremental_update.md` (independent, but this one is small and unblocks the
> operator workflow on its own).

## 1. The problem

An operator who wants to change what a deployed host *does* — or what it *is*, for peer-resolution
purposes — has to destroy and redeploy the node. Three separate causes, none of them the one the
authoring surface suggests:

**1.1 The twin config's `persona:` field is inert.** `twin-configs/*/­*.yaml` puts `persona: <id>` on
26 nodes across the 5 shipped configs. That field is read by exactly one thing — a catalog typo
warning (`twinconfig/expand.py:155-177`, `_persona_warnings`). It is **never written into the IR**:
`_build_ir`'s `node_entry` emits `id/kind/image/role/requires/resources/nets/joins/routing` and
nothing else (`expand.py:340-363`). Editing it changes nothing, anywhere. This is the same class of
trap as the retired `tap_points[].labels` (GAP-030, dissolved) and the `tap:` flags
(`docs/WORK_ORDER_tap_eligibility.md`).

**1.2 The published role is baked into a provisioning op's persisted inputs.** `pass1_emit.py:626`
writes `"node_role": node.role` into **`clone-vm`'s inputs**, and `planner/inventory.py:71` reads
`hosts[].role` back out of that op row. The role is stored *inside the clone record* — which is
literally why changing a role today requires re-cloning the node. It was never a provisioning fact;
it is a label that happened to be parked in a provisioning op.

**1.3 Traffic profiles are not predefinable.** `resolve_traffic_bindings` does
`store.get_deploy(spec.twin)` (`o2control/traffic.py:591`) — a **literal deploy-id lookup** — and
`twin:` is a required field (`traffic.py:241-246`). There is no name resolution and no `--target`
flag on `tfc validate|plan|deploy`. A checked-in profile is therefore welded to one minted deploy id
(`corp-enterprise-f525eed4`), and every fresh twin deploy invalidates every profile the operator
owns. **Tap plans had this exact trap fixed on 2026-07-25**
(`docs/WORK_ORDER_tap_target_resolution.md`); `tfc` was built one day earlier (2026-07-24) and never
received the same treatment. `taps.resolve_target_deploy` (`taps.py:1417`) already exists and is
unused by traffic.

## 2. Why it is mechanically available

**`Node.role` has exactly one functional consumer: the traffic tier.** Verified by sweeping every
`.role` reference in `axrange/`:

| Reference | What it actually is |
|---|---|
| `o2control/traffic.py:409,635` | node role — `_hosts_by_role` (peer resolution + assignment addressing). **The one real consumer.** |
| `planner/inventory.py:71`, `api/service.py:690`, `cli/main.py:1547` | display/publication of the same value |
| `pass1_emit.py:1528,1568,1585` | `install[].role: adds` — a **different field** (app install action) |
| `validator/checks/fabric.py`, `FAB-030` | `routing.role` (spine/leaf/border) — a **different field** |
| `manifest/schema.py:49,57` | image-manifest role — a **different field** |

Nothing in provisioning reads node role. It is already free-standing; it simply is not *addressable*
at runtime. Making it so is an inventory-precedence change plus a place to persist the assignment.

Similarly, persona binding **already works** without touching a VM: the `tfc` tier is idempotent on
re-apply, force-resets `drive-persona` so a changed profile is re-asserted rather than skipped
(registry docstring, LES-057), and de-scopes dropped instances so a swap cannot leave the old one
running (`_stop_dropped_instances`, `traffic.py:1105`). Its plan id is `tfc-<twin-deploy-id>`
(`traffic.py:940`) — one traffic deploy per twin, re-applied in place. The tier is sound; only its
*targeting* and its *scope* (behavior but not identity) are short.

## 3. The decided model (Authority: user, 2026-08-04)

**Role and persona are both `tfc`-owned and runtime-swappable. `tfc` is NOT split into a sixth
deploy tier.**

The twin below holds only what is physically true — node names, OS/image, apps, resources, network
placement. The `tfc` tier owns **what these hosts are and what they do**:

```
te  (twin)   nodes: name, image, install, resources, nets      <- physically true, deploy-time
tfc (layer)  roles: host -> role       personas: role/node -> persona   <- swappable, no VM touched
```

### 3.1 Why not a sixth `_VALID_TIERS` tier

Considered and **rejected**, for reasons that should not be re-litigated without new information:

- A role tier would create **zero cluster resources**, so every piece of tier machinery it inherits —
  naming producers, `is_*` predicates, a row in `teardown.py`'s six-tier protect/cascade matrix, the
  resource sweep — is vestigial.
- Its destroyed state is **incoherent**: destroy the role tier and every persona's `emits[].peer_role`
  resolution breaks, so it is not independently destroyable the way the other five tiers are.
- Roles and personas have **one consumer, always used together**. Two specs that must agree buys a
  coordination surface and nothing else.
- **Precedent:** `tap` was deliberately kept a resource name-shape tag rather than promoted to
  `_VALID_TIERS` (`docs/CONVENTIONS.md` §2, decided 2026-07-24) — and `tap` at least produces
  gretaps and mirrors.

The one argument for splitting — not duplicating a role map across `tfc-quiet.yaml` and
`tfc-busy.yaml` — is answered by sharing one role map between profiles.

> **Correction (build, 2026-08-04):** this section originally claimed the loader's existing YAML
> **include** support already did that. It does not. `load_traffic_profile` calls
> `ir.parser.load_yaml`, which loads exactly **one file**; includes are processed only by `parse_ir`,
> against an IR-shaped mergeable-paths table (`layers.infra.nodes`, …) that never applied to a
> profile. Since the rebuttal is load-bearing for the no-sixth-tier decision, the build implemented
> the small thing that makes it true: a profile-scoped **`roles_from: <path>`** key, resolved
> relative to the referring file by `load_traffic_profile`, one level deep, mutually exclusive with
> an inline `roles:`. Shipped and exercised by
> `twin-configs/corp-enterprise/{roles.yaml,tfc-busy.yaml}`.

### 3.2 Why the assignment is a new op and not a rewrite of `clone-vm`'s inputs

GAP-044's fix (2026-07-27) added machinery to refresh persisted op inputs on re-execute, so a `tfc`
apply *could* rewrite `clone-vm`'s recorded `node_role` in place. **Do not do this.** It would have
one tier mutating another tier's op rows — `tfc` writing into `te`'s state — which breaks the
ownership boundary every tier's teardown and protect-guard depends on. The assignment gets its own
`tfc`-owned op row, which `tfc destroy` retires for free.

### 3.3 Role must be assignable to a host that runs no persona

A host can be a **peer target** without emitting anything (`peer_role: dns-resolver` resolves against
a host that may itself drive no persona). So the role assignment cannot be folded into
`drive-persona`'s scope — it needs its own per-host op.

## 4. Concrete changes

### 4.1 Op catalog (version-locked — bump + sign-off)

New **`OP-TFC-020 assign-role`** — the second Traffic-tier op, composed by hand by the `tfc` playout
exactly like `OP-TFC-010` (never emitted by the `te` planner):

```
id                    OP-TFC-020
name                  assign-role
kind                  ACTION
scope                 [host]
description           Record the tfc-owned semantic role of one already-deployed twin host.
                      Pure metadata: NO adapter call, nothing on the wire, no guest exec.
                      The twin's published inventory prefers this over clone-vm's recorded
                      node_role baseline.
inputs                {"host_id": "node-id:str", "role": "role:str"}
outputs               {"assigned": "bool"}
after                 ()
cleanup_strategy      REDO
idempotency_class     CONFIG
resource_categories   ()          # touches no cluster resource
timeout               10s
```

Force-reset to pending on every apply, same posture as `drive-persona` (LES-057), so a changed role
map is re-asserted rather than silently skipped. **Op count 39 → 40.** Update `docs/OP_CATALOG.md`
and every count citation (`scripts/doc-stats.py --check` polices these).

### 4.2 Inventory precedence (the load-bearing change)

`planner/inventory.py` resolves `hosts[].role` as:

1. a live `tfc` deploy's `assign-role` op output for that host (status `completed`, filtered
   positively — LES-113), else
2. `clone-vm`'s recorded `node_role` (the twin's baseline), else
3. `None`.

Consequences to get right:
- The twin inventory must remain buildable with **no** `tfc` deploy present (step 2 is today's
  behavior, byte-identical). Every existing caller keeps working unchanged.
- `te inventory` / `GET /api/deploys/{id}/inventory` / `te info` should show the **effective** role
  and, when it differs from the baseline, say so (`role=file-server (tfc; baseline: ws)`), so an
  operator can never be confused about why a host answers to a role its twin config didn't declare.

### 4.3 Traffic profile: targeting parity with tap plans

- `twin:` becomes **optional** and is renamed `target:` (accept `twin:` as a back-compat alias —
  the shipped `demo/traffic.demo1.yaml` uses it).
- `target:` accepts a **deploy id OR a scenario/twin-config name**, resolved to the newest live `te`
  deploy of that name. **Reuse `taps.resolve_target_deploy` — do not write a second resolver**
  (LES-037).
- New `--target` flag on `tfc validate`, `tfc plan`, `tfc deploy`. Precedence: `--target` >
  `target:`/`twin:` > error naming both ways to supply it.
- `tfc validate` gets the same **two modes** `tap validate` has: static always; resolve when a target
  is present and the state DB is reachable.
- API parity: the `POST /api/tfc` body accepts the same optional target + override.

### 4.4 Traffic profile: the `roles:` block

```yaml
traffic-profile:
  target: corp-enterprise          # name or deploy id; --target overrides
  roles:
    - node: ws01                   # exactly one of node: / selector:
      role: corp-workstation
    - selector: { net: server-vlan }
      role: file-server
  assignments:
    - role: corp-workstation       # resolves against the roles: block above
      persona: corp-workstation
```

- Addressing mirrors `assignments[]`: exactly one of `node:` / `selector:` per entry, parse-time
  validated (same posture as `traffic.py:264-269` and `taps.py`'s exactly-one-addressing-mode check).
  `roles:` entries **cannot** address by `role:` — that is the chicken-and-egg case and must error.
- **Sequencing is load-bearing:** within one apply, `roles:` is resolved and applied **first**, then
  the role map is rebuilt, then `assignments[]` resolve against it. An `assignments[].role` that only
  exists because of this profile's own `roles:` block must resolve. Pin this with a test.
- De-scope: a role dropped from a re-applied profile must retire its `assign-role` op row
  (`_stop_dropped_instances`' sibling — reuse the same dropped-set pattern, LES-112/LES-113), so the
  host reverts to its twin baseline rather than keeping a stale role forever (the GAP-045 failure
  mode).

### 4.5 Twin-config schema: `name:` / `role:` split

`TwinConfigNode.role: str` (required) is today **both** the semantic role and the node-id stem —
`node_id = spec.role if count == 1 else f"{spec.role}{i:02d}"` (`expand.py:226`). Split it:

- **`name: str`** (required) — the id stem. Purely syntactic.
- **`role: Optional[str]`** — an optional semantic **baseline**, wired to IR `Node.role` exactly as
  today. Now genuinely wired, so it is not a reintroduction of the inert-field trap.

**Migration is `role: X` → `name: X` + keep `role: X`**, which leaves every node id byte-identical —
no VM renames, no op-scope changes, no plan_id churn. Omitting `role:` is then a supported authoring
choice for a bare-bone twin, opt-in per config, not forced by this work order.

### 4.6 Inert-field sweep (user-requested, 2026-08-04)

An audit traced every field in the twin-config vocabulary to a consumer. Six reached nothing:

| Field | Evidence | Shipped occurrences | Status |
|---|---|---|---|
| `TwinConfigNode.persona` | only `_persona_warnings`; never in `_build_ir` | **26** | **THIS work order** |
| `TwinConfigSegment.purpose` | zero references in `expand.py` ("documentation only") | 18 | ✅ landed 2026-08-04 |
| `TwinConfigNode.identity_role` | **zero** references anywhere in `axrange/` (its own comment: "informational only, not wired") | 0 | ✅ landed 2026-08-04 |
| `TwinConfigConduit.policy` | zero references in `expand.py` (the *conduit* is cross-checked at `expand.py:301-307`; `policy` is not) | 0 | ✅ landed 2026-08-04 |
| `TwinConfigIdentity.groups` | `expand.py:395` reads only `u.groups` (per-**user**); the top-level list is never read | 0 | ✅ landed 2026-08-04 |
| `TwinConfigIdentity.ous` | zero references in `expand.py` | 0 | ✅ landed 2026-08-04 |

The five with no replacement to ship **landed ahead of this work order** (CHANGELOG 2026-08-04,
"Inert-field sweep"), together with the `_reject_removed_keys`/`_REMOVED_*_KEYS` tables in
`expand.py` that fail loudly on a leftover. `persona:` was deliberately held back: removing it before
the `tfc-*.yaml` profiles exist would delete 26 nodes' worth of authored intent with nowhere for it
to go. **This work order removes `persona:` and ships those profiles in the same change** — add the
key to `_REMOVED_SECTION_KEYS` with the message "personas are authored in a traffic profile; see
twin-configs/<id>/tfc-*.yaml".

Everything else in the twin-config vocabulary was checked and **is** wired, including every
`TwinConfigIdentityUser` field (`ou`/`password`/`spn`/`asrep_roastable`/`acl`/`delegation`/`host` all
reach the IR at `expand.py:397-410`) and all three `TwinConfigData` metadata fields
(`classification`/`crown_jewel`/`synthetic_size`, `expand.py:421-426`). Do not remove those — and note
the `identity.groups` (removed) vs `identity.users[].groups` (wired) distinction, which is the one an
over-eager sweep would get wrong.

**Recommended, needs sign-off:** flip `TCBase.model_config` from `extra="allow"` to
`extra="forbid"`. This closes the *class* permanently — today a typo'd `presona:` or `instal:` is
silently accepted. The typed passthrough blocks (`fabric`/`routing`/`resources`/`install`/`provides`)
are declared `dict`/`list[dict]`, so their inner keys are unaffected; `count: Any` still absorbs an
unsubstituted `"${N:25}"`. Keep the `_REMOVED_KEYS` table anyway — `forbid` is the catch-all,
the table gives the six known keys an actionable message. Verify the unsubstituted-load path
(`registry.load_twin_config`, used by `twin-config show`) still parses.

### 4.7 Shipped content

- 5 twin configs: `role:` → `name:` + retain `role:`; strip 26 `persona:` and 2 `purpose:`.
- New `twin-configs/<id>/tfc-default.yaml` per config, reproducing exactly the persona set the
  removed `persona:` fields *claimed* — so intent is preserved, not lost, and for the first time
  actually takes effect. Layout parallels the four existing `tap-*.yaml` in
  `twin-configs/corp-enterprise/` (the per-config subfolder reorganization of 2026-07-26 already
  anticipates this shape).
- At least one config also gets a second profile (`tfc-busy.yaml`) sharing `roles.yaml` via include,
  to prove the include path (§3.1) works and is the answer to map duplication.

## 5. Version-locked schemas touched (the gate)

| Contract | Change | Gate |
|---|---|---|
| `OP_CATALOG` | 39 → **40** op types (`OP-TFC-020 assign-role`) | bump + sign-off |
| `IR_SCHEMA` | **none** — `Node.role` is unchanged; the twin config supplies it optionally, `tfc` overrides at inventory-read time | — |
| `scenario_schema_version` | **no bump** | — |
| `VALIDATOR` | **no rule-count change** — twin-config errors are expansion-time (`TwinConfigShapeError`), and traffic profiles are parsed by `traffic.py` (`TrafficPlanError`), neither of which is the IR four-pass pipeline | — |
| state store | **no bump** — `assign-role` is an ordinary op row | — |

## 6. Out of scope

- **Promoting `tap` or anything else to a `_VALID_TIERS` tier.** Explicitly decided against (§3.1).
- **Re-provisioning on role change.** Relabeling a host does not install anything. A `real-service`
  persona still requires its service present in the twin (`install:`), and a role whose apps the twin
  never installed cannot be conjured by a label. Say this plainly in the docs — it is the honest
  boundary of "repurpose without redeploy," and the thing that motivates
  `docs/WORK_ORDER_twin_incremental_update.md`.
- **Auto-reconciling a standing `tfc` deploy when the twin changes underneath it.** That is W2's
  sibling-tier problem.
- **Removing `Activity.adversary` / `Network.isis_area`** — both traced to zero consumers during the
  same sweep, but both live in the **version-locked IR**, so they are reported here and left alone.
  Log them in `docs/PRODUCT_GAPS.md` rather than removing them in this work order.

## 7. Validation

**Unit/suite:**
1. A twin config with `persona:`/`identity_role:`/`purpose:`/`policy:`/top-level `groups:`/`ous:`
   fails expansion with an actionable message naming the replacement — one test per key.
2. A typo'd unknown key fails expansion (the `extra="forbid"` guard), and `twin-config show` still
   loads an unsubstituted config.
3. `role: X` → `name: X` migration produces **byte-identical node ids and plan_id** for all 5 shipped
   configs. Pin this — it is the whole safety argument for the split.
4. Inventory precedence: baseline-only, tfc-override, and tfc-override-then-de-scoped-back-to-baseline.
5. A profile whose `assignments[].role` exists only via its own `roles:` block resolves (§4.4
   sequencing).
6. `roles:` addressing by `role:` errors; a dropped role retires its op row.
7. Target resolution: name → newest live `te`; `--target` beats `target:`; `twin:` alias still parses;
   no target at all errors with both remedies named.

**Gated `dev45` live-verify (the point of the whole work order):**
Against a standing `corp-enterprise` twin — `tfc deploy tfc-default.yaml --target corp-enterprise`,
then `tfc deploy tfc-busy.yaml --target corp-enterprise` swapping both a **role** and a **persona** on
a live host. Prove: the personas actually change on the wire, the role change is visible in `te
inventory`, **no VM is cloned, restarted, or reconfigured** (compare vmids + uptime before/after),
and `tfc destroy` reverts every host to its twin baseline role.

## 8. Depends on / sequence

- **Depends on:** nothing unbuilt. `tfc` (`docs/WORK_ORDER_traffic_tier.md`, live-verified 2026-07-25)
  and `taps.resolve_target_deploy` (`docs/WORK_ORDER_tap_target_resolution.md`, 2026-07-25) both exist.
- **Sequence:** ships **before** `docs/WORK_ORDER_twin_incremental_update.md` (W2). Independent of it —
  W1 is the small change that unblocks the operator workflow; W2 is the planner work.
- **Interacts with:** W2's sibling-tier reconcile. Once roles are `tfc`-owned, a node removed by an
  incremental twin update must invalidate its role assignment too — W2 §"sibling tiers" must account
  for `assign-role` rows, not just `drive-persona` ones.
