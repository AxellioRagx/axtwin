# Work Order — The deploy gate: `DEPLOY_MIN_OCCUPANCY` on the twin-config expander

> **STATUS: BUILT 2026-09-04** — S1–S3 complete, **S4 partial** (`scenario.labels` + the
> `confirm_deploy_gate` recovery helper built; the optional `deploy_gate` key on
> `TwinInventory`/`TwinInventoryDTO` **deferred**, §11.2 ruling 4). See **§11.2 "Built as"** for the
> rulings and corrections the build made, and CHANGELOG "2026-09-04 — Deploy gate BUILT (spec 13
> §3.4, the axRange half)". Drafted 2026-09-04. Authority: **user** for the policy —
> `docs/design/13-golden-twin-fidelity.md` §3.4 "Deployment scope: the cast describes, the deploy
> selects" (CHANGELOG "2026-09-04 (later)"); **implementation (Claude Code)** for this draft's
> mechanism choices, each marked and open to veto. **Version-locked: none intended, and none
> changed as built** (op types stay 40, catalog rules stay 114) — no
> `IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no new op type,
> no `scenario_schema_version` bump, no state-store `SCHEMA_VERSION` bump. `axrange/twinconfig/` is
> not version-locked: this is a code change, the numeric cousin of the existing `when:` gate.
> **Cross-repo:** the producing half is aximprint's `docs/WORK_ORDER_deployment_scope.md`
> (emit-all + the observation block); §10 is the seam. **Sequence:** parallel with
> `docs/WORK_ORDER_fidelity_scorecard.md` and `docs/WORK_ORDER_protocol_faithful_services.md`;
> **required before** the fresh cast that becomes the first golden — spec 13 rows 1 and 2 are
> closed by this change and by nothing else.

## 1. The problem

The cast decides at compile time which observed hosts become twin nodes: it drops role-less hosts
and applies a `--min-occupancy` threshold. The drop is invisible downstream — axRange receives a node
list with no record of what was discarded — and it makes rows 1 and 2 unjudgeable, since "is every
observed host above the bar placed?" is vacuous once the cast has already removed everything below
its own bar.

§3.4 moves the decision: **the cast annotates, the deploy selects.** Every emitted node and segment
carries an observation block; the expander gates on it at a threshold the operator sets, defaulting
to **0.8 — the same bar row 1 uses**, so the deployable set and the set row 1 expects are one set by
construction. One cast then serves every threshold.

## 2. What exists today (verified on this branch)

- `axrange/twinconfig/schema.py` — `TCBase` is `extra="forbid"` (2026-08-04, Authority: user).
  `TwinConfigSegment` and `TwinConfigNode` each already carry a **`when: Optional[str]`** gate;
  `TwinConfig.params: dict[str, Any]`.
- `axrange/twinconfig/expand.py` — `_included(when, params)` is that gate, applied to segments
  (line 167) and nodes (line 174) inside `_build_ir(tc, params)`, which errors when no segment
  survives. `_reject_removed_keys` runs first, on the raw pre-substitution dict.
- `axrange/twinconfig/params.py` — resolution order `--param` → `params:` → inline
  `${NAME:default}` → `TwinConfigParamError`; `_coerce_like` types an override against a declared
  default, `coerce_scalar` an undeclared one.
- **`--param` already ships.** `_add_config_flags` (`axrange/cli/main.py:105`) puts
  `--config`/`--param`/`--twin-configs-dir` on `te validate|plan|deploy` (lines 142, 157, 205);
  `_resolve_scenario_or_config` (line 940) parses them. API peer: `CreateDeployRequest.params`
  (`axrange/api/dtos.py:240`). **No new deploy-path flag is needed.**
- **The warnings channel exists with no producer.** `expand_twin_config_raw(..., warnings=)` is
  plumbed to `_resolve_scenario_or_config` (prints `[warn] …`) and to
  `CreateDeployResponse.config_warnings` (`axrange/api/dtos.py:266`); its docstring says it "has NO
  producer" since the persona check was removed. **This gate is its producer.**
- `hash_ir(raw_ir)` / `derive_plan_id(scenario_id, ir_hash[:8], tier)` (`planner/dag.py`).
  `planner/state.py` — `SCHEMA_VERSION = 3`; a `deploys` row persists `plan_id`, `ir_hash`,
  `placement_json`, `tier` and **not** the scenario path, config id, or params.
- `axrange/ir/schema.py:446` — **`Scenario.labels: Optional[dict[str, Any]]`**, documented at
  `docs/IR_SCHEMA.md:134` as *"Free-form k/v … Not validated."* Zero consumers. Two shipped scenario
  IRs (`target-environments/fabric-verify`, `optrahealth-twin`) set it as free-form tags — a
  passthrough this work order leaves untouched — and no twin config can (the model has no
  `labels` field), so the expander becomes its first producer: the additive, non-version-locked
  carrier §6 recommends. Emission must merge into, never overwrite, a labels map.
- The cast (`twin-configs-cast/corpapcn-twin/`): 27 segments, 62 node specs, 138 VMs after `count:`
  fan-out, one `kind: router` `gw`, four compiler-invented `internet-sink-*` specs. Its
  `.topology.json` carries 786 entities, `windows: 11`, per-node `windows_seen` and
  `role_confidence` — but **segments carry no observation fields**, and the config itself carries
  none. That is the aximprint hand-back.

## 3. The `observation:` block

### 3.1 Shape

One optional block on `TwinConfigNode` and `TwinConfigSegment` — the same two models `when:` sits on:

```yaml
nodes:
  - name: file-server-10-80-64
    net: seg-10-80-64-0
    count: 3
    observation: {windows_seen: 11, windows_total: 11, occupancy: 1.0, role_confidence: 0.92}
```

New `TwinConfigObservation(TCBase)` (inherits `extra="forbid"`), four fields, names fixed by the
seam (§10): `windows_seen: int`, `windows_total: int`, `occupancy: float`, `role_confidence:
Optional[float] = None`. **A block describes the whole spec, all `count` members included** — the
cast keeps no per-host identity (spec §3.1 row 1), so the gate is all-or-nothing per spec. That
obliges aximprint to keep a spec occupancy-homogeneous (§10.4).

### 3.2 `occupancy`: carried **and** cross-checked (recommendation)

The gate reads the carried `occupancy`; expansion hard-errors when
`abs(occupancy - windows_seen/windows_total) > 1e-6`. *Carried, not derived*, because `occupancy` is
the compiler's own definition and may stop being a plain ratio (weighted or unequal-length windows
are the natural next step there) — a consumer that re-derived it would gate on a definition nobody
wrote down. *Cross-checked, not trusted*, because three fields where one would do is the redundancy
that rots into a false affordance (LES-172; four retired here already). The check makes both counts
load-bearing as a checksum, so a producer bug surfaces at expansion rather than silently selecting
the wrong node set.

### 3.3 Validation rules

In `_build_ir` (helper `_check_observation`), each raising `TwinConfigShapeError` naming the entity:
(1) `windows_total >= 1`; (2) `0 <= windows_seen <= windows_total`; (3) `0.0 <= occupancy <= 1.0`;
(4) the §3.2 cross-check; (5) `role_confidence` is `None` or in `[0, 1]`; (6) **all-or-nothing** — a
block missing any required field is an error, not a partial block (`extra="forbid"` covers the
reverse). A block on an entry `when:` already excluded is not checked at all (gate order, §4.3).

### 3.4 What carries no block, and is therefore always emitted

**No `observation:` block ⇒ never gated out.** Three populations depend on this:

- **Every shipped `twin-configs/` config** — the five `list_twin_configs()` discovers (`sme-flat`,
  `corp-enterprise`, `datacenter`, `ics-ot`, `dmz-perimeter`; `corp-enterprise-maxrate` holds a
  `.yaml.snapshot`, not a discoverable config). Hand-authored, never observed: the gate is
  structurally a no-op for them, which is what §5's pin proves.
- **Compiler-class entities** in a cast (`gw`, `internet-sink-*`). An `occupancy: 1.0` block would
  assert an observation never made; **no block is the honest mechanical expression of evidence class
  `compiler`** (`docs/design/14-operator-assertions.md` §2). Seam §10.3.
- **`kind: router` nodes, block or no block.** A router is twin plumbing; gating one out disconnects
  surviving segments and turns a threshold change into a topology break. The exemption is
  unconditional and is *printed* when a router carries a sub-threshold block.

## 4. The gate

### 4.1 `DEPLOY_MIN_OCCUPANCY`

An ordinary twin-config param — `params: {DEPLOY_MIN_OCCUPANCY: 0.8}` — resolved by the existing
`resolve_params`, overridable with the shipped `te {validate,plan,deploy} --config <id> --param
DEPLOY_MIN_OCCUPANCY=0.5` and the API's `params`. Read in `_build_ir` (which already receives the
resolved map) via a new `_deploy_thresholds(params)`: absent → **built-in default `0.8`**, so a cast
that forgets to declare it still gates at row 1's bar; non-numeric or outside `[0, 1]` →
`TwinConfigParamError` naming the value (`coerce_scalar("abc")` yields the string `"abc"`, which must
fail loudly, not compare falsy). A cast config **should** declare it anyway, so `twin-config show`
prints it in the existing `params:` line.

### 4.2 The predicate, and the boundary

```
emitted = obs is None
          or spec.kind == "router"
          or (obs.occupancy >= min_occupancy - 1e-9
              and (obs.role_confidence or 0.0) >= min_role_confidence - 1e-9)
```

The `1e-9` epsilon is deliberate and must be pinned: "seen in 8 of 10 windows" at `0.8` is **in**,
the plain reading of "≥ 80%", and a float boundary flip here silently changes `plan_id` (§5).

### 4.3 Order of operations, and the cascade

Inside `_build_ir`, in this order:

1. **`when:` gating — unchanged, first.** `when:` is authored intent ("this variant of the config");
   occupancy is observed evidence. A `when:`-excluded entry never reaches the occupancy gate.
2. **Node occupancy gate** — §4.2 over `node_specs`. Excluded specs never reach `count:` fan-out, so
   no instance, IP, app or binding is created.
3. **Segment gate** — ~~emitted when it survives `when:`, survives its own block, **and** at least one
   emitted node has a leg on it. Kept-but-empty → dropped, reason `no emitted node`. Gated out by
   its own block while an emitted node still has a leg on it → **hard error** naming both (the node
   would have a leg into nothing: a cast inconsistency, not an operator choice).~~
   **SUPERSEDED 2026-09-04 by the build (§11.2 rulings 1 and 2, Authority: implementation (Claude
   Code)) — the original text above is kept as the record, not as the spec.** As built, two things
   the draft got wrong on real content:

   - **A router follows its segments.** A router leg counts neither as `legged` (for the hard error)
     nor as `occupied` (for the emptiness test). A segment removed by the gate or by the cascade has
     its router legs **pruned in the same pass**; a router stranded of every leg is dropped and
     reported (`kind: node`, reason "every segment it had a leg on was gated out"); and the "gated
     out from under an emitted node" **hard error fires only for a non-router node**. Reason: a cast
     emits ONE `gw` with a leg on every one of its 27–33 segments and exempt from the node gate, so
     counting its legs made every segment permanently occupied (the cascade could never fire) and
     turned every below-bar segment into that hard error — **every real cast refused**.
   - **The cascade removes a segment only when the gate emptied it.** Not "kept-but-empty → dropped"
     but `occupied_before and not occupied_after`, where both sets count **non-router** occupants
     only, before and after the node gate. A router-only segment — a router↔router transit link, a
     WAN uplink — has no host occupant *by construction*, so a bare emptiness test dropped it at
     **every** threshold including 0.0, took its routers with it as "stranded", and turned a
     threshold change into exactly the topology break the router exemption exists to prevent. Such a
     segment now leaves only via its own `observation:` block. Measured on shipped content:
     `dmz-perimeter` lost its `internet` uplink and `datacenter --param FABRIC=true` lost `fab-link`
     plus both fabric routers, from a single annotated host that gated nothing out.

   The whole empty-drop is additionally scoped to `report.active` (≥1 `observation:` block anywhere):
   with no gate running there is nothing to cascade from, and dropping a deliberately-empty authored
   segment would move a blockless config's `ir_hash` and orphan any standing deploy of it (LES-126).
4. **The existing "no segments left" error** — reused, message widened to say which gate emptied it.
5. **Conduits** — one naming a non-emitted segment is **dropped with a printed reason**, *before*
   the existing "no node attached to BOTH segments" cross-check. A conduit is documentation over the
   node set (its own docstring); its referent is gone by construction, so failing the cross-check on
   a set the operator deliberately chose would make the threshold unturnable. A conduit whose
   segments both survive keeps today's hard cross-check unchanged.
6. **`identity:`/`data:` references — loud error (recommendation, D2).** `identity.users[].host` and
   `data[].on_role` name a node id; when that node gated out, raise `TwinConfigShapeError` naming the
   entry, the node, its occupancy, the effective threshold, and the remedy. These are **authored**
   facts, unlike segments and conduits which are *derived* from the node set; silently dropping a
   local user or a crown-jewel dataset changes the twin's security posture (spec 13 row 7) with no
   signal — the class LES-089 and LES-172 exist to close.
7. **`tfc` role references — no change, and none possible.** A traffic profile is a separate file in
   a separate tier resolved at `tfc deploy` against the published inventory; the expander cannot see
   it. `o2control/traffic.py` already warns loudly on an unresolvable binding ("roles entry … matched
   no host in target twin"). Correct existing behavior, untouched — see §12 for its wording limit.

### 4.4 The optional second gate

`DEPLOY_MIN_ROLE_CONFIDENCE`, default **0.0** (off), same plumbing, folded into §4.2's single
predicate. At the default it can never keep a present host out — §3.4's requirement exactly. A
`null` `role_confidence` is treated as `0.0`, so any threshold above zero excludes role-less hosts:
deliberate, and stated in the printed reason, since "only deploy hosts whose role I trust" is the
only thing an operator setting this can mean. **Recommend building it in this pass** (D3).

### 4.5 Never silently skip (LES-089)

The gate produces into the dormant `warnings` channel: a threshold line, a counts line, one line per
gated-out entity with its occupancy and window counts, and a `kept: router 'gw' (occupancy 0.55,
below threshold; routers are never gated)` line per exempted router. Per-entity lines cap at **50**,
then `… and N more; run 'axrange twin-config show <id>'`. So `te validate|plan|deploy --config` print
them through the existing `[warn]` loop (**no CLI change**), `POST /api/deploys` returns them in
`config_warnings` (**no API change**), and `twin-config show` gains an uncapped **`deploy gate:`**
section — thresholds, counts, full table — honoring `--param` so a threshold can be previewed before
deploying (the one genuinely new flag, D4). The counts print **even when nothing is gated out**
whenever any block exists: "0 gated out at 0.8" is a meaningful answer and its absence is not.

## 5. `plan_id`, and the mandatory regression pin

**Changing the threshold changes the node set, therefore `ir_hash`, therefore `plan_id`** —
correctly, per §3.4: it is a different twin. `plan_id` keys VMIDs, VM names, the SDN zone, vnets and
the underlay bridge, and is how a standing deploy is found (LES-126). So a twin deployed at 0.8 and
re-deployed at 0.5 is a **second, separate deploy**, not a resumption; the supported way to move the
threshold **on a standing twin** is `axrange te deploy --update`, which pins the standing `plan_id`
and reconciles by op-id set difference. Lowering is an `--update` **add**; raising is
`--update --allow-destroy` **remove** — both whole-node changes, exactly the subset `--update`
implements, so a threshold change alone never trips its mutation refusal.

**The pin (blocking).** Every shipped `twin-configs/` config and the standing cast config carry no
`observation:` block, so the gate must leave their expanded IR byte-identical.
`tests/test_twinconfig_catalog.py::test_shipped_config_plan_id_survived_the_name_role_split` already
pins five (`sme-flat-d6ab9fe2`, `corp-enterprise-86423faa`, `datacenter-09528ee1`, `ics-ot-34fd6ef5`,
`dmz-perimeter-dcf875e2`) and must keep passing untouched. Extend it with the cast config, measured
on this branch: `corpapcn-twin` → `plan_id corpapcn-twin-b6aaa3c5`, `ir_hash b6aaa3c5b145cba7…`. Not
cosmetic: the standing `corpapcn-twin-0e4f7d58` (138 VMs, dev45) is the scorecard's first subject,
and a `plan_id` change orphans it.

## 6. Recovering the threshold a twin was deployed with

The scorecard needs the threshold and the gated-out set for the twin it scores
(`docs/WORK_ORDER_fidelity_scorecard.md` §9.6; rows 1–2 gain the per-fact state *observed, below
deploy threshold*). **Recommendation: (c) via `scenario.labels`, with (a) as the confirmation
mechanism rather than the mechanism.** The expander emits, **only when the gate actually ran** (≥1
`observation:` block anywhere): `scenario.labels: {deploy_min_occupancy: "0.8",
deploy_min_role_confidence: "0"}`.

- **No `IR_SCHEMA` change** — `Scenario.labels` already exists, documented free-form and unvalidated;
  nothing reads it; two shipped scenario IRs set it as tags (a passthrough, unaffected) and no twin
  config can, so the expander becomes its first producer at no cost (corrected 2026-09-04: the
  draft first said no shipped content sets it).
- **String values** through one normalization helper, pinned by a test, so the same effective
  threshold always produces the same bytes.
- **Byte-identical for shipped configs**, because the emission is conditional on a block existing —
  that condition is what makes §5's pin hold.
- **The threshold joins `ir_hash`, and that is the point.** Recovery becomes a *proof*: re-expand the
  config at a candidate threshold, `hash_ir`, compare to the deploy row's `ir_hash`. A match confirms
  the threshold **and** makes the gated-out set exactly reproducible. A mismatch reports
  `threshold: UNCONFIRMED (twin config changed since deploy)` — never a number nobody can stand
  behind. Ship it as `axrange.twinconfig.confirm_deploy_gate(config_id, ir_hash, *, catalog_dir,
  param_overrides) -> DeployGateRecord | None` so the scorecard calls one function.

**Not (b), persist in the deploy row:** a new `deploys` column needs `SCHEMA_VERSION` 3→4
(`CREATE TABLE IF NOT EXISTS` never repairs an existing table), a `CLAUDE.md` risk trigger *and* a
forced re-initialization of dev45's `state.db` — orphaning the standing 138-VM twin the scorecard is
about to score. **Not (a) alone:** reading `params:` at scorecard time cannot distinguish the
declared default from a `--param` override (overrides are persisted nowhere) and cannot detect config
drift. (c) subsumes (a) and adds the confirmation.

**`te inventory`/the DTO.** The inventory publishes only what op rows prove (LES-096); op rows carry
no threshold and never will retroactively. So add an optional `deploy_gate` key to
`TwinInventory.to_dict()` and `TwinInventoryDTO`, populated **only** by the helper above when the
caller supplies the config id and the `ir_hash` confirms — `null` otherwise, never fabricated. Slice
S4; deferrable without blocking the first golden.

## 7. Concrete changes

| File | Change |
|---|---|
| `axrange/twinconfig/schema.py` | `TwinConfigObservation(TCBase)`; `observation:` on `TwinConfigNode` + `TwinConfigSegment`. **Coordinate** with the `listen:` passthrough `docs/WORK_ORDER_protocol_faithful_services.md` §6.2(b) adds to `TwinConfigNode` — two independent fields on one `extra="forbid"` model; whichever lands second rebases. |
| `axrange/twinconfig/expand.py` | `_deploy_thresholds`, `_check_observation`, `_gate_included` (§4.2); the §4.3 cascade in `_build_ir`; §4.5 warning production; §6 `scenario.labels` emission. |
| `axrange/twinconfig/__init__.py` | Export `confirm_deploy_gate` / `DeployGateRecord`. |
| `axrange/cli/main.py` | `cmd_twin_config_show`: the `deploy gate:` section + `--param` on that parser. `cmd_te_inventory`: render `deploy_gate` when present (S4). |
| `axrange/api/dtos.py`, `axrange/planner/inventory.py` | Optional `deploy_gate` passthrough (S4). Nothing derived from op rows. |
| `twin-configs-cast/corpapcn-twin/corpapcn-twin.yaml` | **Not edited here** — regenerated by aximprint's half. |

Nothing in `planner/` (beyond S4), `validator/`, `ops/` or `adapter_proxmox/` changes.

## 8. Build slices

| # | Slice | Acceptance | Size |
|---|---|---|---|
| **S1** | Block + validation | Model parses; each §3.3 rule raises naming the entity; unknown inner key rejected by `extra="forbid"`; **all six pinned `plan_id`s unchanged** (five shipped + the cast). | small, ~40 lines |
| **S2** | Gate + cascade | §4.2's predicate; §4.3's seven steps; the boundary case; routers and blockless entities always emitted; identity/data error with the remedy; conduits drop with a reason. A fixture cast config yields exactly the expected sets at 0.0/0.5/0.8/0.9/1.0. | **medium — the substance**, ~120 lines |
| **S3** | Never-silently-skip | Warnings carry summary + capped detail; all three surfaces print them; `twin-config show` prints the uncapped section and honors `--param`. | small, ~50 lines + 1 flag |
| **S4** | Threshold recovery | `labels` emitted iff a block exists; `confirm_deploy_gate` confirms unchanged / unconfirms drifted; optional `deploy_gate` on inventory + DTO. | small–medium, ~80 lines, **separable** |

**Built 2026-09-04:** S1, S2, S3 complete; **S4 partial** — `labels` emission +
`confirm_deploy_gate`/`deploy_gate_report` built, the inventory/DTO `deploy_gate` key
deferred (§11.2 ruling 4).

S1–S3 are what the fresh cast needs. **Route: Pipeline (`/dev`)** — code-writer → tester →
code-reviewer → doc-writer, with §9's live-verify folded into the fresh cast's own gated deploy.

## 9. Tests, and the live-verify question

1. **`tests/test_twinconfig_expand.py`** — the §3.3 table, one case per rule; the §4.2 boundary
   (`8/10` at `0.8` **in**, at `0.81` **out**).
2. **A fixture cast config** (new, under `tests/`) with blocks at `1/11, 2/11, 8/11, 9/11, 11/11`,
   one blockless node, one `kind: router` with a sub-threshold block, one segment that empties, one
   conduit into it, one `data[].on_role` pointing at a gating-out host. **The gating table** —
   expected node ids, segments and conduits at `0.0/0.5/0.8/0.9/1.0` — is the core assertion.
3. **`when:` interaction** — a node with both; a `when:`-excluded node whose `observation:` is
   invalid must **not** raise; a `when:`-kept segment emptied by occupancy drops with a reason.
4. **Shipped-content regression (LES-185, blocking)** — extend the existing `plan_id` pin to the cast
   config at `corpapcn-twin-b6aaa3c5`, and assert `"labels" not in ir["scenario"]` for all six (five shipped + the cast). A
   green hand-fixture suite is not evidence: `--update` shipped with 1802 green tests while refusing
   100% of this repo's real router-bearing content.
5. **Warnings** — summary produced with zero gated out; the 50-line cap and tail; `config_warnings`
   reaches the API response.
6. **Recovery** — `labels` present/absent per the condition; `confirm_deploy_gate` confirms
   unchanged, unconfirms after an edit, confirms a `--param` override that was actually used.
7. **Param typing** — `=abc` and `=1.5` raise `TwinConfigParamError`; `=0.5` coerces to float against
   a declared float default; undeclared and unoverridden yields `0.8`.

**Gated `dev45` live-verify — recommend none of its own.** Pure expander change: no adapter, no op,
no cluster call; its entire output surface is an IR dict the unit suite asserts byte-for-byte, and
§5's `plan_id` pin is a stronger guarantee than a cluster run. The **fresh cast's own deploy**
exercises it end-to-end and is already gated — fold two checks into that run's checklist: (1) the
printed `deploy gate:` summary matches the node count in `te status`/`te inventory`; (2)
`confirm_deploy_gate` returns **confirmed** at the threshold the operator passed. If a dedicated gate
is wanted anyway, the cheapest honest one is a `--dry-run` plan at two thresholds, comparing
`plan_id`s and node counts — no VMs.

## 10. Seam with the aximprint companion

To reconcile against `/home/rocky/aximprint-axrange/docs/WORK_ORDER_deployment_scope.md`:

1. **Block shape — agreed, no deviation requested.** `observation: {windows_seen, windows_total,
   occupancy, role_confidence}`, exactly these names, on **both** `nodes[]` and `segments[]`. Note
   the current topology export carries `windows_seen`/`role_confidence` per node and `windows: 11` at
   top level, but segments carry `{cidr, coverage_source, entities, name}` only — the segment half is
   new work on that side.
2. **`occupancy` is authoritative; the counts are its checksum.** axRange gates on `occupancy` and
   hard-errors on a >1e-6 disagreement with the ratio (§3.2). If aximprint intends `occupancy` to be
   anything other than that ratio, say so and the check becomes a warning.
3. **No block means "always emitted".** Compiler-invented entities (`gw`, `internet-sink-*`) must
   carry **no** block. Do not synthesize `occupancy: 1.0`: that asserts an observation never made,
   and the `compiler` evidence class has no truthful occupancy.
4. **One spec must be occupancy-homogeneous.** The cast collapses hosts into `count: N` specs
   (`generic-endpoint-heavy-…`/`-light-…` shows bucketing already exists) and the gate is
   all-or-nothing per spec. aximprint must split a group whose members straddle a plausible
   threshold, the way it already splits by volume — **or** define the block's `occupancy` as the
   group **minimum**, in which case a mixed group is kept or dropped by its weakest member. axRange
   is identical either way; the choice is aximprint's and must be written down.
5. **Segment blocks must be consistent with their members** — a segment gated out while an emitted
   node still has a leg on it is a hard error here (§4.3 step 3), not a silent repair.
6. **The threshold lives in the config's `params:`.** Emit `params: {DEPLOY_MIN_OCCUPANCY: 0.8}` even
   though 0.8 is axRange's built-in default, so `twin-config show` displays the bar. If a
   `--min-occupancy` compile flag survives, it must no longer **drop** — at most set this param.
7. **Overlap, not seam:** `docs/WORK_ORDER_protocol_faithful_services.md` §6.2(b) adds `listen:` to
   the same `TwinConfigNode`. Independent fields, one `extra="forbid"` model; second to land rebases.
   Both work orders claim "no version-locked change" and both are correct.

## 11. Decisions for the user

**D1 — Where the deployed threshold is recorded.** (a) recompute from config + params at read time;
(b) persist in the `deploys` row (`SCHEMA_VERSION` 3→4); (c) emit into `scenario.labels`, confirmed
against the recorded `ir_hash`. → **Recommend (c)**: no version-locked change, every shipped
`plan_id` stays byte-identical because the emission is conditional, and recovery becomes a proof
rather than a guess. (b) is the only option that survives the config file being deleted, but it is a
`CLAUDE.md` risk trigger and would force re-initializing dev45's `state.db`, orphaning the standing
138-VM twin the scorecard is about to score. **A user decision because (b) touches the state store.**

**D2 — Identity/data references to a gated-out node: error or drop-with-reason?** → **Recommend hard
error** naming the entry, node, occupancy, threshold and remedy (§4.3 step 6): identity and data are
*authored* facts, and silently dropping a local user or crown-jewel dataset changes the twin's
security posture with no signal. The counter-argument is real — an error makes the threshold less
freely turnable on a config mixing cast nodes with hand-authored overlays — so it goes to the user.

**D3 — `DEPLOY_MIN_ROLE_CONFIDENCE` now or later?** → **Recommend now**, default `0.0` (off). One
clause of one predicate, provably behavior-neutral at its default; deferring ships `role_confidence`
parsed-but-unread, the false-affordance shape retired four times already (LES-172). Deferring is
defensible only if the first golden's gate surface should be as small as possible.

**D4 — `--param` on `twin-config show`.** The gate makes that the natural "preview a threshold"
verb, but the parser carries no `--config`-family flags today. → **Recommend adding `--param`
there**. Flagged because it is the one genuinely new CLI surface; everything else reuses shipped flags.

### 11.1 Rulings and calls, 2026-09-04

**Ruled by the user (CHANGELOG 2026-09-04, evening):** the **0.8 default is kept** (about 64 observed
hosts against 122 placed today — the twin is exactly the certified set); role-less hosts are
**`unroled-endpoint`** (an image rule, no persona), not `generic-endpoint`; the cast's group key
gains a **fixed occupancy ladder** (core ≥ 0.8 / regular 0.5–0.8 / transient < 0.5), so the block
on a `count: N` group carries its **minimum member's** `windows_seen` and occupancy and the gate
compares that exact number — seam item 4 of §10 is resolved that way, not by exact `windows_seen`
keys and not by a histogram. `role_confidence` **absent** on segments and `unroled-endpoint` nodes
is read as `None`; no block ⇒ always emitted stands; the cast emits `params:
{DEPLOY_MIN_OCCUPANCY: 0.8}` so `twin-config show` displays the bar.

**Taken as implementation calls (Claude Code), open to veto:** **D1 (c)** — `scenario.labels`,
emitted only when a block exists, confirmed against `ir_hash`; no `SCHEMA_VERSION` bump. **D2** —
hard error naming entry, node, occupancy, threshold and remedy. **D3** — `DEPLOY_MIN_ROLE_CONFIDENCE`
now, default `0.0`. **D4** — `--param` on `twin-config show`.

**Honest limit added:** a re-imprint that moves a host across a ladder rung changes two groups'
`count:`, which `te deploy --update` refuses as an in-place mutation; whole-group adds and removes
update incrementally, count changes do not.

### 11.2 Built as (2026-09-04)

**Authority:** implementation (Claude Code) for everything in this section, except where it names a
§11.1 user ruling it implements. CHANGELOG: "2026-09-04 — Deploy gate BUILT (spec 13 §3.4, the
axRange half)". Delivered as `axrange/twinconfig/gate.py` (new) + the `observation:` block on
`schema.py` + the gate/cascade in `expand.py` + the `deploy gate:` surfaces on the CLI and API.

Seven things the build decided or corrected, recorded so they are not re-litigated:

1. **A router follows its segments** — refines §4.3 step 3, which is amended in place above. Found
   independently by the aximprint deployment-scope build and by code review; without it, every real
   cast refused, because the compiler's single `gw` legs all 27–33 segments and is exempt from the
   node gate.
2. **The emptiness cascade removes a segment only when the gate emptied it** — also §4.3 step 3.
   `occupied_before` vs `occupied_after`, non-router occupants only. Found by the tester against
   *shipped* content (`dmz-perimeter`'s `internet` uplink; `datacenter --param FABRIC=true`'s
   `fab-link` + both fabric routers), not by the hand-built fixture — the LES-185 pattern again.
   Both cases are now pinned as tests.
3. **`gate_verdict` takes `role_confidence_applies` as a REQUIRED keyword**, not a default. §4.2's
   single predicate applied the role-confidence clause to segments as well as nodes; a segment has
   no role, so every observed segment gated out the moment `DEPLOY_MIN_ROLE_CONFIDENCE` rose above
   zero — and then hard-errored for being gated out from under its own emitted nodes. The matching
   producer-side rule is enforced too: `check_observation` **refuses** a `role_confidence` on a
   segment block rather than parsing and ignoring it (§11.1's "absent where it does not apply",
   made mechanical).
4. **S4's `deploy_gate` key on `TwinInventory` / `TwinInventoryDTO` is DEFERRED**, not done. It has
   no producer without a second CLI surface to populate it, and an inventory key nothing fills is
   exactly the inert-field shape LES-172 has retired four times. The fidelity scorecard calls
   `confirm_deploy_gate` directly, which is the §6 contract it actually needs. S4's other half —
   `scenario.labels` emission + `confirm_deploy_gate`/`deploy_gate_report` — **is** built.
5. **The cascade and the `labels` emission are both scoped to `report.active`** (≥1 `observation:`
   block anywhere in the config), so blockless content keeps a byte-identical `plan_id`/`ir_hash`
   (LES-126). Proven two ways: the §5 pin extended to **six** configs (five shipped + the cast at
   `corpapcn-twin-b6aaa3c5`), and the tester rebuilding the pre-change tree and diffing.
6. **Two review-caught defects (M2/M3) fixed:** the printed `--param` remedy now renders at full
   precision via `gate.exact_param_value` (`repr`) — a `%g` render of `8/11` is `0.727273`, which
   sits *below* the producer's own `0.7272727272727273` and still refuses the node when pasted back,
   because the gate's comparison epsilon is `1e-9` while the occupancy checksum tolerates `1e-6`;
   and `gated_by_node_id` is now built **after** `count:` fan-out, minus the emitted id set, so an
   emitted spec named `worker01` sitting beside a gated-out `count: 3` `worker` is no longer a false
   refusal with an un-actionable remedy.
7. **Measured on the real fresh cast** (`corpapcn-scope-check` — 33 segments, 88 node specs, 115
   observation blocks; the aximprint build's output, not a fixture): `0.0` → 157 nodes / 33 nets;
   `0.8` → 80 nodes / 24 nets (64 observed + 15 internet-sink + 1 `gw`); the `gw`'s leg count equals
   the surviving segment count at every threshold; `te validate` passes at 0.8; and under one pinned
   `plan_id`, `0.5 → 0.8` is **removed-only** and `0.5 → 0.0` is **added-only, 0 mutated** — §5's
   claim that a threshold change never trips `te deploy --update`'s in-place mutation refusal, now a
   test rather than an argument.

**Tests.** `tests/test_twinconfig_gate.py` — **87 cases** over a new fixture cast
(`tests/fixtures/twin-configs/gate-cast/gate-cast.yaml`, whose `gw` legs **every** segment precisely
because the shipped cast's does); `tests/test_twinconfig_catalog.py` — the `plan_id` pin extended to
six configs, plus a threshold-invariance test and a `"labels" not in scenario` mechanism assertion;
`tests/test_twinconfig_cli.py` +11; `tests/test_api.py` +3. Suite: **2125 passed / 12 skipped /
0 failed, 2137 collected**.

**Not built, by design:** §9's dedicated gated live-verify (folded into the fresh cast's own gated
deploy, per §9's own recommendation) and S4's inventory key (ruling 4).

## 12. Honest limits

- **The gate cannot re-admit a node after deploy.** Changing the threshold changes `plan_id` (§5). On
  a standing twin the only path is `te deploy --update` — an add when lowering, an `--allow-destroy`
  remove when raising. An unflagged redeploy at a new threshold mints a second twin alongside the
  standing one.
- **A twin deployed at 0.5 and scored at 0.8 must be reported as a threshold mismatch**, never
  silently rescored. §6's `ir_hash` confirmation is what makes the mismatch detectable; the scorecard
  surfaces `threshold: UNCONFIRMED` and declines to score rows 1–2 against a set it cannot reproduce.
- **A `count: N` spec gates as one unit.** If aximprint mixes occupancy bands inside a spec (§10.4),
  the gate keeps or drops all N. axRange cannot split a spec it did not author.
- **`tfc` and `tap` plans are not reconciled.** A profile or tap plan naming a host the threshold
  removed warns at *its own* deploy ("matched no host in target twin") without mentioning the gate —
  same class as `--update`'s sibling-tier limit (`docs/WORK_ORDER_twin_incremental_update.md` §3.5):
  warned, never auto-fixed.
- **Occupancy is a vantage property, not a truth.** A host absent from 10 of 11 windows may be a
  transient, or perfectly real and simply silent from where the sensor sat. The gate selects on what
  was *seen*; the operator channel (`docs/design/14-operator-assertions.md`) is how a known-real,
  rarely-seen host gets back in — as an `asserted` fact, which outranks the observation entirely.
- **The standing `corpapcn-twin-0e4f7d58` gains nothing from this.** Cast under the old drop policy,
  no blocks, no recorded threshold — scorecard run 1 fails rows 1 and 2 there exactly as
  `docs/WORK_ORDER_fidelity_scorecard.md` §3.4 predicts. This change pays off on the fresh cast.
