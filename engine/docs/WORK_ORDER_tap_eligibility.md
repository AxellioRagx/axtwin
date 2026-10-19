# Work Order — The twin substrate becomes tap-agnostic; the tap plan owns all tap semantics

> **STATUS: DONE (code / tests / docs / live-verify).** Authority: user (2026-07-28, decisions
> D1–D6 + STR-200 below); build Authority: implementation (Claude Code). **Gated `dev45` live-verify
> PASSED 2026-07-28** — a fresh focused twin (`tap-elig-verify`: Debian router + WAN + `h-a`/`h-b`
> on a VLAN segment, **zero `tap:` flags** anywhere in the IR) confirmed a `segment` tap point
> resolves every port on the net including the router's own leg, `perimeter: wan` resolves the WAN
> uplink, the `te` deploy emitted zero OP-MIR ops, `te inventory` published no `tap_points` key, and
> on-wire capture showed each frame exactly once with a clean teardown. This required a **freshly
> deployed** twin (LES-096: an already-`completed` `record-tap-intent` op on a pre-existing deploy
> can't retroactively vanish; a `--fresh` redeploy, not a resume, was needed before a tap plan could
> resolve against full topology rather than a stale recorded subset). Two pre-existing, unrelated
> bugs surfaced during the run and are tracked as new gaps, not fixed here: `docs/PRODUCT_GAPS.md`
> GAP-046 / GAP-047. See CHANGELOG 2026-07-28 "Live-verify (dev45, 2026-07-28)" for full detail.
> **Three review rounds** were run (code-writer → tester → code-reviewer, iterated three times)
> before all findings closed: one **must-fix** — `STR-020`'s recognized-`scenario_schema_version`
> set has to be widened in the SAME change as the schema-version-note prose, or `{1,2,3}` "all
> validate" is just a docstring claim with no enforcement behind it — plus a handful of smaller
> cleanups (a production-YAML generator that still emitted `tap:`, `redo_on`/description prose on
> sibling ops left pointing at the retired op, a stale fixture premise). See CHANGELOG "The twin
> substrate becomes tap-agnostic" for what the reviews surfaced. **Verified: 1683 passed / 12
> skipped / 0 failed, 1695 collected; op types 40 → 39; catalog rules 114 → 112.**
> This is a **structural/scope shift**, not a refactor, and it changes **three version-locked
> schemas** (`IR_SCHEMA`, `OP_CATALOG`, `VALIDATOR`) — each bumped + signed off per `CLAUDE.md`
> "Design Document Maintenance," recorded in §5 below and in the CHANGELOG entry.
>
> **Intent refined 2026-07-28 (Authority: user).** The original order (below the fold, "Prior
> framing") proposed *option (a)* — move eligibility to the tap plan while keeping `tap:` in the IR
> as a default hint and `record-tap-intent` as the declared default. The user chose a cleaner,
> more aggressive line: **the te substrate config carries NOTHING tap-related.** No `tap:` flags,
> no `tap_points:` block, no `record-tap-intent` op, no named/labeled catalog. The twin publishes
> only its raw topology; the **tap layer owns 100% of tap semantics** — which interfaces, scopes,
> and everything else. This supersedes the prior framing (kept for the trail).
>
> **Spec:** `docs/design/06-taps-sensors.md` (tap-point scopes + the tap playout). Route:
> code-writer → tester → code-reviewer → doc-writer → **gated** live-verify (dev45). **Sequence
> after** `docs/WORK_ORDER_tap_scopes.md` (DONE) — this consumes the four scopes it established.

## 1. The problem

A tap is deployed and destroyed independently of the twin (the `tap` tier). Yet the twin config
still authors tap-related content, and the tap resolver is still gated on it:

- **`resolve_tap_point` (`axrange/planner/tappoints.py:544`) reads `idx.tapped`** — the tapped
  *subset* recorded by `OP-MIR-005 record-tap-intent`, i.e. from `tap:` flags authored in the
  **twin IR** and frozen at `te` deploy time. So a tap plan can only bind interfaces the twin
  remembered to flag; tapping something you forgot means redeploying the whole twin.
- **The twin-config `tap_points:` block** (`_apply_tap_points`, `axrange/twinconfig/expand.py`)
  is authored te-side but only ever manifests as those boolean `tap:` flags — its `id`/`labels`
  never reach the inventory (`docs/PRODUCT_GAPS.md` GAP-030), so label-based `selector:` matching
  is inert.
- **`build_twin_inventory` (`axrange/planner/inventory.py:268`)** builds the published
  `tap_points[]` *from* `record-tap-intent.tapped_nics` — again the subset, not the topology.

All of this is te-side tap authoring for a decision (which link is captured by which sensor) that
belongs entirely to the tap plan. `segment` cannot *mean* "every port on the net"; it means "every
port someone flagged at deploy time." GAP-028 was "fixed" by hand-editing `tap: true` into eleven
`target-environments/` IRs — the clearest symptom that this lives in the wrong tier.

## 2. Why it is mechanically available

Nothing blocks removing the gate. `install-ovs-mirror` needs only `vmid` + `nic_index`, and the
published inventory already carries the twin's **full** attachment topology unconditionally:
`hosts[].nets[]` is sourced from `attach-vm-to-vnet`, which is emitted for **every** leg regardless
of tap intent (**LES-095** makes exactly this point). The tap playout can already target any port;
the `tap:` flag is **policy, not a mechanical prerequisite.**

## 3. The decided model (Authority: user, 2026-07-28)

Four decisions, all the user's:

| # | Decision | Consequence |
|---|---|---|
| **D1** | **Eligibility = tap plan sole authority over the full topology.** `resolve_tap_point` resolves against the twin's full attachment topology (`hosts[].nets[]`), not `idx.tapped`. Any port a plan names is tappable — no allow-list anywhere. | `segment` finally means "every port on the net." A plan that used to resolve 3 ports may now resolve 40 — an expected posture change, not a regression. |
| **D2** | **Remove the `tap:` flag from `IR_SCHEMA`** (`nodes[].tap`, `nodes[].nets[].tap`, `networks[].tap`). | Version-locked `IR_SCHEMA` change (bump + sign-off). 11 `target-environments/` + 5 twin-configs migrated (§6). |
| **D3** | **Drop the named/labeled catalog entirely.** No `tap_points[].id`/`labels`. A tap plan selects purely by `scope + target` against live topology. | GAP-030 **dissolves** (labels were the only thing it wired). The twin-config `tap_points:` block + `TwinConfigTapPoint` + `_apply_tap_points` are removed. Selector `labels:` matching is removed from the tap-plan grammar. |
| **D4** | **Remove `record-tap-intent` (OP-MIR-005).** | Version-locked `OP_CATALOG` change (bump + sign-off, op count **40 → 39**). A `te` deploy emits **zero** tap-related ops. A2's discovery (`_scan_te_inventory`) re-points to the inventory's full topology. |
| **D5** | **Drop the published inventory `tap_points[]`.** `te inventory` / the inventory DTO / `GET /api/deploys/{id}/inventory` publish only `hosts[].nets[]` (full topology) — no browsable catalog. | `selector: {scope, target}` is the primary addressing mode. `tap_point: <id>` still resolves against **derived** ids computed at bind time (`<net>`, `<net>-perimeter`, `<a>--<b>`, `<host>:<net>:<nic>` — deterministic from topology, just not published). |
| **D6** | **Remove the `tap_host`/`tap_net` → `sensor` binding kind** from `bindings:` (IR_SCHEMA §6). | Forced by D2 — it references "tapped nodes," and it is legacy te-side tap routing the tap plan already replaced. `REF-200` retires with it (§4.4). |

End state:

```
axrange te deploy twin.yaml   # zero tap ops; twin publishes only raw topology
axrange tap deploy plan.yaml  # plan selects by scope+target against FULL topology
```

The te substrate has no concept of "tap" at all. Everything a tap needs — eligibility, scope,
target — is authored in the tap plan and resolved against the twin's published topology at bind
time.

## 4. Concrete changes

### 4.1 Engine
- **`axrange/planner/tappoints.py`** — `resolve_tap_point` (and `perimeter`/`segment` resolution
  at `:498`/`:562`) resolve against the **full attachment topology** from the inventory, not
  `idx.tapped`. `derive_default_tap_points` derives its candidate set from full topology + node
  `kind` (no `tap:` gating, no labels). Retain `_is_router_kind` (load-bearing for
  `perimeter`/`boundary`).
- **`axrange/planner/inventory.py`** — `build_twin_inventory` no longer reads
  `record-tap-intent.tapped_nics`, and **drops the published `tap_points[]` field entirely** (D5):
  the inventory publishes only `hosts[].nets[]` (full topology). Remove `tap_points` from the
  inventory dataclass/DTO, the `te inventory` renderer (`axrange/cli/main.py`), the API DTO
  (`axrange/api/{dtos,service}.py`, `GET /api/deploys/{id}/inventory`), and `twin-config show`.
  `derive_default_tap_points` stays as **internal** resolution machinery (`tap_point:`/`selector:`
  resolve against it at bind time) but is no longer published.
- **`axrange/o2control/taps.py`** — `_scan_te_inventory` / `_tapped_iface_set` discover the
  tappable set from the inventory's full topology instead of `record-tap-intent`. The `__wan__`
  uplink stays a first-class entry (it is already carried in `hosts[].nets[]` via
  `attach-vm-to-vnet`, `net_id: "__wan__"` — the `record-tap-intent` WAN special-case
  `docs/WORK_ORDER_tap_scopes.md` M3 added is **removed**, as that order anticipated). Remove
  selector `labels:` matching; keep `scope`/`target`/`tap_point:`/`tap:` addressing.
- **`axrange/planner/pass1_emit.py`** — stop emitting `record-tap-intent` (the te deploy's entire
  remaining OP-MIR emission surface — so a te deploy now emits **no** OP-MIR op at all). Confirm
  nothing else orders on it.

### 4.2 Op catalog (version-locked — bump + sign-off)
- **`axrange/ops/registry.py` + `docs/OP_CATALOG.md`** — remove `OP-MIR-005 record-tap-intent`
  (op count 40 → 39; §8 summary table + the §5.3 signature + the §5.3/§5.8 "record-tap-intent is
  the te's entire tap-emission surface" notes). The three real OP-MIR install ops
  (`attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture`) are unchanged — still emitted only
  by the A2 tap playout.

### 4.3 IR schema (version-locked — bump + sign-off)
- **`axrange/ir/schema.py` + `docs/IR_SCHEMA.md`** — remove `nodes[].tap`, `nodes[].nets[].tap`,
  `networks[].tap` (§3.1/§3.2). Bump the `scenario_schema_version` recognized set `{1, 2}` →
  `{1, 2, 3}` (STR-020); migrated content keeps its current version number (the removal applies
  regardless of declared version, consistent with how additive fields are parsed). Add the CHANGELOG
  entry.
- **Remove the `tap_host`/`tap_net` → `sensor` binding kind** (`bindings:`, IR_SCHEMA §6) — D6.
  It is te-side tap routing the tap plan already replaced, and it references "tapped nodes," a
  concept D2 removes. `REF-200` (which guards it) retires with it (§4.4).

### 4.4 Validator (version-locked rule-catalog change — CHANGELOG + never reuse IDs)
- **Retire** `STR-190` (`tap:` flag is boolean — no field), `NET-110` (tapped segment ≥1 node — no
  `networks[].tap`), and `REF-200` (tap→sensor binding references a *tapped* node — no `tap:`).
  Mark deprecated, never reuse the IDs (`docs/VALIDATOR.md` §3.3).
- **Add `STR-200`** (Pass 1, structural, error, Authority: user 2026-07-28): a `tap:` field on a
  node / leg / network is **rejected loudly** — *"tap: is removed; author taps in the tap plan
  (scope+target selector)."* Necessary because Pydantic `extra=allow` would otherwise silently drop
  a leftover `tap:`, vanishing an author's capture intent with no signal (LES-111). A stray
  `tap_host`/`tap_net` binding needs **no** new rule — `STR-100`'s closed binding-kind set (with D6's
  removal) already rejects it.
- **Net rule count 114 → 112** (retire 3, add 1).

### 4.5 Twin-config expander
- **`axrange/twinconfig/{schema,expand,errors}.py`** — remove `TwinConfigTapPoint`,
  `_apply_tap_points`, and `TapPointMaterializationError`. A `tap_points:` key in a config is now
  either an error or silently dropped (decide: error, so a stale config fails loudly rather than
  silently ignoring capture intent — LES-111 posture).

## 5. Version-locked schemas touched (the gate)

Three, all needing a bump + explicit sign-off before landing:

1. **`IR_SCHEMA`** — remove `tap:` (+ remove the `tap_host`/`tap_net` → `sensor` binding kind, D6). `scenario_schema_version` bump.
2. **`OP_CATALOG`** — remove `OP-MIR-005`. Op count 40 → 39; catalog version note.
3. **`VALIDATOR`** — retire STR-190 / NET-110 / REF-200; add STR-200 (reject a leftover `tap:` loudly). Rule count 114 → 112.

The user's four §3 decisions are that sign-off in principle; the doc-writer stage records each bump
with its `**Authority:** user` marker.

## 6. Shipped-content migration (measured 2026-07-28)

Removing `tap:` and `tap_points:` requires editing, in the same change:

- **11 `target-environments/` files** carry `tap: true` (~63 occurrences): `optrahealth-twin` (17),
  `enterprise-multisite` (13), `ot-ics-range` (6), `purple-team-range` (6), `fabric-verify` (5),
  `vuln-management-lab` (4), `dfir-hunt-lab` / `ctf-range` / `soc-detection-lab` (3 each),
  `ad-domain-lab` (2), plus `target-environments/README.md` prose. Strip every `tap:` flag.
- **5 twin-configs** (`corp-enterprise`, `ics-ot`, `sme-flat`, `dmz-perimeter`, `datacenter`) —
  remove the `tap_points:` block and any `tap:` flags.
- **Shipped tap plans survive unchanged** — the `twin-configs/corp-enterprise/tap-*.yaml` plans
  (and the untracked `tap-xpress-peri.yaml`) already select by `selector: {scope, target}` with no
  `labels:`/`tap_point:` ids. Audit for any `labels:`/`tap_point:` use and rewrite to `scope+target`.

Capture on a migrated twin is unchanged in practice: `axrange o2 tap apply` against the deployed
twin now resolves against full topology instead of the (previously identical, hand-flagged) subset.

## 7. Capacity guardrail (Authority: user, 2026-07-28)

Full-topology eligibility makes "bind every port to one sensor" trivial, and fan-out **duplicates
each frame on the transport wire**. Add a **soft, non-blocking warning** at `tap plan` / `tap
deploy` when a resolved plan crosses a large port-count / heavy-fan-out threshold (name the
duplication; proceed anyway). Not a hard cap.

## 8. Out of scope (separate cleanup)

The top-level `sensors:` IR block + `REF-210`/`REF-250` are **left as-is**. They are tangled with
the O2 standing-sensor direction, a `te` deploy already stands up nothing for them (`REF-250`
warns), and leaving them warned is fine. A dedicated follow-up retires them — not this order.

(The two boundary sub-decisions this reframe raised — the inventory `tap_points[]` list and the
`tap_host`/`tap_net` binding — are now **decided**: drop and remove, respectively. See D5/D6 in §3.)

## 9. Validation

- **Unit:** `resolve_tap_point` resolves an un-flagged port (proving the gate is gone); `segment`
  resolves every port on a net incl. router legs; `perimeter`/`perimeter: wan`/`boundary` resolve;
  the `(port, sensor)` direction union (from tap_scopes) still holds; A2 discovers the tappable set
  from full topology with no `record-tap-intent`; the capacity warning fires past threshold;
  removed validator rules no longer register; the expander rejects a stale `tap_points:` block.
- **Plan-level:** every migrated `target-environments/*` + `twin-configs/*` validates and plans
  clean at default and non-default params; the three shipped `corp-enterprise` tap plans still
  resolve to the same port sets.
- **Gated live-verify (dev45) — STOP and ask before any deploy/destroy.** On a freshly deployed
  twin (LES-096): confirm a tap plan binds a `segment`, a `perimeter`, and a `perimeter: wan` point
  **without any `tap:` in the twin IR**; a host↔host frame arrives once under `segment`; the WAN
  uplink capture carries real egress. Confirm `te deploy` emits zero OP-MIR ops.

## 10. Depends on / sequence

- **After** `docs/WORK_ORDER_tap_scopes.md` (DONE — the four scopes + derived direction this order
  keeps; it removes the *input set* gate those scopes resolve against).
- Independent of BPF filtering (deferred, no order yet).
- Closes `docs/PRODUCT_GAPS.md` GAP-028 (eligibility no longer needs hand-flagging) and dissolves
  GAP-030 (labels removed, not wired).

---

## Prior framing (SUPERSEDED 2026-07-28 — kept for the trail)

The original work order proposed *option (a)*: move `resolve_tap_point` off `idx.tapped` onto the
full topology, but **keep** `tap:` in the IR as an at-most default hint and re-examine (not remove)
`record-tap-intent`, deferring the "does `tap:` survive" question to the build. The four §3
decisions above supersede it: `tap:` is **removed** (not demoted to a hint), the named/labeled
catalog is **dropped** (not relocated), and `record-tap-intent` is **removed** (not repurposed).
The minimal WAN-only eligibility unblock that `docs/WORK_ORDER_tap_scopes.md` M3 shipped as a
stopgap is retired by D1/D4 here, exactly as that order anticipated ("removed, not layered on").
