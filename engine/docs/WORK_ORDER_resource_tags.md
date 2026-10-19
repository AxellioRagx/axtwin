# Work Order — resource ownership tags (`te_` / `tap_` / `o2_` / `img_`)

> **For a subagent Pipeline session (`/dev`), not this design session.** This is an
> actionable implementation + validation spec. The design/contract is in
> `docs/DESIGN_BRIEF_o2.md` §6 (blast-radius tiers); this doc is *how to build it*.
> **Authority for the scheme:** user (2026-07-08). **Route:** Pipeline mode
> (`docs/WORKFLOW.md`) — code-writer → tester → code-reviewer → doc-writer →
> **gated** live-verifier. Interrupt the design session only on a real decision.

> **Status: DONE — 2026-07-13.** §1–§4 (build + tests) landed 2026-07-08; §5's gated `dev45`
> live-verify **PASSED 2026-07-13** (CHANGELOG). A tapped `tap-verify` deploy produced correctly
> tier-tagged `te_`/`tap_` resources; `axrange destroy` swept all 8 of them and left `img-*`
> templates + unrelated VMs untouched (idempotent re-destroy clean). The `axr_*`→`img-*` template
> rename (§5) was a **non-event** — dev45's templates were already born `img-*` by `axrange image
> build`, so no `qm set --name` migration was needed, and the cluster carried zero old-named live
> resources (no migration sweep required either). **Two orthogonal, non-tag findings** surfaced
> during the run (neither blocks this work order): `verify-capture` needs `SENSOR_SSH_HOST` set in
> `adapter.conf` (unset ⇒ heartbeat fails), and the default-ON exporter agent (`install-agent`)
> assumes egress that a minimal no-router scenario lacks (timed out + retried) — logged as
> `docs/PRODUCT_GAPS.md` GAP-NNN.

## 1. Goal

Replace today's inconsistent `axr`/`vn`/`vbr_`/`gre_`/`mir-` names + `is_axr_*`
shape-matching with **tier-tagged** deterministic names, so `destroy` blast-radius is
*intrinsic* (sweep by tier tag + `deploy_id`) and the seam between a target-environment,
its taps, and the standing surface is explicit. Four tiers:

| Tier | What | Lifecycle / blast radius |
|---|---|---|
| **`te_`** | target-environment deploy resources | removed by `destroy <deploy>`; **cascades to `tap_`** |
| **`tap_`** | the tap-binding (mirror + source gretap) | own tier; swept by a `te_` destroy; (future) independently destroy/redeploy |
| **`o2_`** | standing O2 surface resources (when the O2 control layer creates them) | **never** touched by a `te_`/`tap_` destroy |
| **`img_`** | golden templates / base images used for cloning | build-time, standing, **never** destroy-swept |

Single source of truth stays `axrange/adapter_proxmox/naming.py`; `planner/teardown.py`
imports its predicates (do not re-derive shapes).

## 2. Naming scheme (concrete — refine slugs within the constraints, keep the tier tag literal)

Constraints (unchanged, `naming.py` header): SDN zone/vnet id ≤ 8 chars `[a-zA-Z0-9]`;
bridge ifname ≤ 15 chars `[a-zA-Z][a-zA-Z0-9_]+` (no hyphen); VM name ≤ 63; te-VMID range
100000–999999; templates ~9000–9999.

| Resource | Today | New | Note |
|---|---|---|---|
| SDN zone | `axr<5hex>` | **`te<6hex>`** (8) | |
| SDN vnet | `vn<6hex>` | **`tev<5hex>`** (8) | `tev` ≠ `te<hex>` (index-2 is `v`, not hex) so regexes stay disjoint |
| underlay/per-net bridge | `vbr_<6hex>` / `vbr_<san>` | **`tebr_<6hex>`** / `tebr_<san>` (≤15) | |
| VM name | `axr-<6hex>-<host>` | **`te-<6hex>-<host>`** (≤63) | tag lives in the name |
| te VMID | 100000–999999 | *unchanged* | range guard stays a secondary check |
| gretap (source) | `gre_<6hex>` | **`tapg_<6hex>`** (≤15) | `gre_port_for_bridge[_sensor]` |
| per-tap mirror | `mir-<vmid>` | **`tapm-<vmid>`** | OVS name, length-flexible |
| standing O2 vnet/bridge/VM | — | **`o2v<5hex>`** / `o2br_<6hex>` / `o2-<…>` | only when O2 control stands them up (future) |
| golden template VM name | `axr-…` (dev45) | **`img-<image-slug>`** | e.g. `img-debian-12-cloud`, `img-suricata-sensor`, `img-win-server-2022` |

Add predicates mirroring the shapes: `is_te_zone/vnet/bridge/vm_name/vmid`,
`is_tap_gretap/mirror`, `is_o2_*`, `is_img_*`. Remove/rename `is_axr_*`.

## 3. Code changes

- **`adapter_proxmox/naming.py`** — rename the producers to emit the new names
  (`zone_id_for_plan`→`te…`, `vnet_id_for_net`→`tev…`, `underlay_bridge_for_plan` /
  `bridge_name_for_net`→`tebr_…`, `vm_name_for_host`→`te-…`, `gre_port_for_bridge[_sensor]`
  →`tapg_…`, `mirror_name_for_vmid`→`tapm-…`). Replace `is_axr_*` with `is_te_*` / `is_tap_*`;
  add `is_o2_*` / `is_img_*` (used as **protect** guards). Update docstrings + the header
  comment. Function *names* can stay (only their output changes) to minimize caller churn —
  implementer's call.
- **Callers** (grep-confirmed): `adapter_proxmox/rest.py`, `adapter_proxmox/ssh.py`,
  `imagebuild/conformance.py`, `planner/teardown.py`, and any pass-1 precompute. They call the
  producers by function name, so they update automatically if names are kept.
- **`planner/teardown.py`** — swap the guards (`_vmid_ok`/`_vnet_ok`/`_zone_ok`/`_gretap_ok`)
  to the `is_te_*`/`is_tap_*` predicates. The `te→tap` **cascade is already structural** (tap
  ops are ops of the te deploy, so `ops_for_deploy` includes them) — keep that. **Add a hard
  protect guard:** if a resource about to be touched matches `is_o2_*` or `is_img_*`, refuse
  and record a `guard-violation` (defense-in-depth; a te destroy must never remove standing
  surface or a template). Keep the te-VMID range guard as the secondary template exclusion.

## 4. Validation

- **Update** the tests asserting old shapes: `test_adapter_proxmox.py`, `test_teardown.py`,
  `test_planner_executor.py`, `test_sensor_routing.py`, `test_sensor_exec.py`,
  `test_imagebuild.py`.
- **Add** tests: (a) a `te` destroy sweeps `te_`+`tap_` resources of the deploy; (b) an
  `o2_`/`img_`-named resource is **refused** by the teardown protect guard; (c) predicate
  round-trips (every producer's output matches its `is_*` predicate, and the tiers are
  mutually exclusive — no name matches two tiers).
- Full suite must stay green (currently **576**; expect the count to rise with the new tests).

## 5. Live cluster (dev45) — gated live-verifier only

- **Migration hazard:** deploys already on a cluster under `axr`/`vn`/… names are **not**
  recognized by the new `te_`/`tap_` guards. Before deploying this change, **destroy any
  existing old-named deploys with the *current* code**, or use a clean cluster. Record what was
  swept.
- **Template rename:** the `axr_*` golden templates on dev45 (VMIDs in the template range) →
  `img-<slug>` via `qm set <vmid> --name img-<slug>` (or the REST config endpoint). Bindings
  reference templates by **`template_vmid`** (VMID, not name), so the rename is cosmetic and
  **does not** require binding edits — confirm each binding's `template_vmid` still resolves.
- **Proof run (gated):** deploy a small tapped scenario → confirm resources are `te-*`/`tev*`/
  `tebr_*`/`tapg_*`/`tapm-*` → `axrange destroy` → confirm every `te_`/`tap_` resource of that
  deploy is gone, **and** the templates (`img-*`) + any standing `o2_*` + unrelated deploys are
  untouched → idempotent re-destroy clean.

## 6. Docs to update (doc-writer stage)

`docs/ADAPTER.md` §4 (Model-C naming table), `naming.py` docstrings/header, `CLAUDE.md`
naming references, `docs/DESIGN_BRIEF_o2.md` §6 (mark the tags **implemented**), a `LES-NNN`
if a gotcha emerges (e.g. the migration hazard), and a CHANGELOG entry (Authority: user for the
scheme; implementation for the build).

## 7. Out of scope (do NOT build here)

- The **standalone `destroy taps` / redeploy-taps verb** and independent `tap_` lifecycle —
  blocked on the **tap config playout** (not designed yet; `docs/DESIGN_BRIEF_o2.md` §7). This
  work only *tags* taps and cascades them under `te` destroy.
- The engine→O2 refactor that actually makes the O2 control layer *create* `o2_` resources —
  separate effort. Here, `o2_`/`img_` are defined + **protected**, not yet produced by the engine.
