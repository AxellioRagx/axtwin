*The standalone engine — IR → validator → planner → adapter → destroy — and its module
boundaries.*

## What the engine is

The engine is the `axrange/` Python package: a standalone pipeline that turns an authored
Scenario IR into running infrastructure and back down again. It is a **library-and-CLI
monorepo**, not a service — a deploy is an ephemeral process that runs to completion (or a
terminal failure) and exits, with all durable state in a SQLite state store rather than in
process memory.

The engine never imports from the O2 surface layer, the AI scenario research sidecar, or the
`target-environments/` content library — those depend on the engine, not the other way around.
See [../CONVENTIONS.md](../CONVENTIONS.md) and `CLAUDE.md`'s "Conventions to Follow" for the
full one-thing-per-directory rule.

## The pipeline at a glance

```
Scenario IR (YAML)
      │
      ▼
  Validator ── rule catalog, four passes (structural → reference → semantic → cluster)
      │
      ▼
  Planner ── DAG construction (5 passes) + state store + lease/locks + executor
      │
      ▼
  Adapter ── one interface, REST + SSH backends against Proxmox
      │
      ▼
  Running twin  ──(reverse)──▶  Destroy
```

Each stage is a clean boundary: the validator has no dependency on the planner; the planner
imports the adapter as a library through a narrow protocol; the adapter knows nothing about
scenarios or IR. A reader can study each stage in isolation.

## Scenario IR

The input to everything is the **Scenario IR** — a versioned, git-friendly YAML document: a
`scenario` header, `layers` (`infra` / `apps` / optional `activity`), `bindings`, and optional
`overlays` (identity / data / defense / adversary). It is the single hand-off between the design
surface (canvas) and execution — platform-agnostic, so the same IR can in principle target a
future VMware or cloud adapter. For the formal contract, see `docs/IR_SCHEMA.md`.

## Validator

The validator is a **shared library**, not a service, exposed as four entry points
(`validate_structural`, `validate_full`, `validate_with_cluster`, `explain_rule`) so the same
rule code runs identically whether called from `axrange validate`, a canvas keystroke, or the
first phase of `axrange deploy`. It has no dependency on the planner — the planner depends on
it.

Rules execute in four passes, the first two halting on first error (structural shape has to be
sound before reference checks make sense), the last two accumulating findings:

1. **Structural** + **include consistency** — is the YAML shaped correctly; do includes resolve
   without collision.
2. **Reference integrity** — do every binding, alias, image ref, and sensor/tap reference
   resolve to something that actually exists.
3. **Semantic** — capability intersection (does the image provide what the node requires),
   lifecycle (deprecated/withdrawn image or behavior refs), network sanity (CIDR overlap, VLAN
   collisions, tap shape), overlay/activity/defense consistency, and the fabric routing block.
4. **Cluster** — only when a live Proxmox snapshot is available (so only at deploy time):
   placement satisfiability, RAM/CPU/disk headroom, VLAN-in-use conflicts, template existence.

Findings carry a rule ID, a severity (`error` blocks deploy; `warning` proceeds but flags;
`info` is observational), and a templated message. A `reproducible: true` scenario promotes
certain warnings to errors (unpinned image/behavior refs, missing seeds). The validator never
modifies the IR and never touches platform state — "is this IR sound enough to plan against" is
its entire contract; DAG-shape correctness (cycles, reachability, type consistency) is the
planner's job instead. The rule catalog spans structural, reference, capability, lifecycle,
network, overlay, activity, defense, fabric, cluster, and persona-catalog categories. For the
full rule catalog and pass assignments, see `docs/VALIDATOR.md`.

## Planner

The planner consumes a validated IR, image manifests/bindings, and (for placement) a snapshot of
the target cluster's capabilities. It produces a **DAG of operations** — a deploy plan — and
then executes it.

### DAG construction (five passes)

1. **Emit** — walk the IR and emit one op instance per externally-observable side effect (a VM
   clone is one op; attaching its NIC is another), plus synthetic **barriers**
   (`network-ready`, `host-ready`, `domain-ready`, `mirror-ready`, `overlay-ready`,
   `fabric-ready`) that give cross-entity dependencies a single, stable target.
2. **Dataflow** — wire data-producing outputs (a `vmid`, a resolved IP) to the ops that consume
   them as typed inputs.
3. **Ordering** — add non-dataflow ordering edges: an op's declared `after:` patterns, and
   IR-level bindings landing on barriers rather than on specific implementation ops (so op
   composition can change without rewiring consumers).
4. **Placement** — pick a Proxmox node per host node, from the nodes whose advertised
   capabilities satisfy the union of the node's `requires:` and the image binding's
   `requires_host_caps:`; least-loaded by default, sticky across re-plans.
5. **Validate** — cycle detection, reachability, type consistency, and placement
   satisfiability on the constructed DAG; failures halt before any external state is touched.

Same IR + same manifests + same cluster snapshot always produce the same DAG, the same op IDs,
and the same placement — determinism that makes plan diffing, plan hashing, and idempotent
re-runs all possible.

### Idempotency and resume

Every action op is idempotent on retry. `infra`-class ops (Proxmox API / OVS / kernel
networking) use **Model C**: deterministic resource naming from `{plan_id, op_id}`, check-then-
act on entry, and self-cleanup of partial state before redo. `config`-class ops lean on
Ansible's own task-level idempotency instead. Re-running `axrange te deploy` against the same
scenario resumes from the prior deploy's state — completed ops are fast no-ops, failed/halted
ops retry, not-yet-started ops run normally; `--fresh` opts out and mints a clean deploy with
its own independent op rows.

Failures follow a retry-then-escalate ladder: `redo`-marked ops get N retries with exponential
backoff; `escalate`-marked ops (ops whose external effects can't cleanly be undone) walk back to
the preceding barrier and redo the whole subtree, halting the subtree after a second escalation
for operator inspection. A halted subtree doesn't stop independent work elsewhere in the DAG.

### State store

A single SQLite file holds `deploys` (one row per invocation, tagged by tier — see
[02-twin-model.md](./02-twin-model.md)), `leases` (the per-scenario single-writer gate),
`resource_locks` (DB-backed cross-process locks on correctness-sensitive resource categories —
one Proxmox node's OVS instance, one VM, one sensor's gretap transport interface), `ops` (one
row per op instance, keyed by `(deploy_id, op_id)` so two deploys of the same plan never alias
each other's rows), and `op_attempts`. Op completion, output persistence, and the lease-takeover
check are all atomic — a crash never leaves "this op is complete but its outputs are missing."
Operators can query the store directly with plain SQL; it doubles as an operations diagnostic
surface.

### Concurrency

A per-scenario **lease** stops two deploys of the *same* scenario from racing; DB-backed
**resource locks** on the correctness categories (one node's OVS, one VM, one sensor's gretap)
make concurrent *different-scenario* deploys serialize on shared resources across processes,
even though the executor inside any one deploy stays internally sequential. A lost lease (a
missed heartbeat renewal) aborts the deploy rather than risking a second writer.

For the full spec — retry/backoff numbers, escalation policy, state-store schema, concurrency
categories, and re-planning semantics — see `docs/PLANNER.md`.

## The op catalog at a glance

The planner's Pass 1 draws from a closed catalog of typed op definitions, grouped into
categories roughly ordered by a host's bring-up sequence:

| Category | Covers |
|---|---|
| `OP-NET` | SDN zone / OVS bridge / vnet / cluster-wide apply per scenario network |
| `OP-HOST` | Clone, hardware, cloud-init, NIC attach, start, guest-agent wait, OS-ready wait, static routes |
| `OP-MIR` | Tap-intent recording and (in the tap playout) mirror hook install, GRE tunnel attach, capture heartbeat |
| `OP-APP` | One op per app install-action kind (`pkg`, `config`, `role`, `container`, `script`) |
| `OP-OVL` | Overlay materialization — identity, data, defense, adversary-agent install |
| `OP-ACT` | Activity actor resolution, behavior-agent deploy, activity start |
| `OP-TFC` | Traffic-tier persona driving on an already-deployed twin |
| `OP-BAR` | Synthetic barriers (`network-ready`, `host-ready`, `domain-ready`, `mirror-ready`, `overlay-ready`, `fabric-ready`) |

Every op type declares typed `inputs`/`outputs` (which drive the dataflow-edge pass), `after:`
ordering patterns, a `cleanup_strategy` (`redo` or `escalate`), an `idempotency_class` (`infra`
or `config`), the concurrency-semaphore categories it occupies, and the IR field paths that
force it to redo on a re-plan. For the full catalog — every op's signature — see
`docs/OP_CATALOG.md`.

## Adapter

The Proxmox adapter is a library the planner imports; it lives in the planner's own process and
shares its state-store handle rather than running as a separate service. Its job is narrow: take
one op instance plus the relevant image binding, perform the external side effect, and return the
op's declared outputs.

**One interface, two backends.** The planner never knows which transport an op uses:

- **REST** (the Proxmox API) covers everything the API supports — SDN definition/apply, VM
  clone/configure/start, guest-agent polling.
- **SSH** covers what the API doesn't expose — the per-tap `tc` mirror hook install, the keyed
  gretap netdev create, and (for VyOS routers) config delivery over `vbash`.

The split is forced by what Proxmox exposes today, not a design preference; an op migrates
backends transparently if a future Proxmox release grows API coverage for it, with no change to
the op catalog or the planner. A few ops cross backends internally (e.g. the REST-side
cloud-init op SCPs a snippet file over SSH because Proxmox has no REST upload endpoint for it) —
these hops are op-internal and don't break the planner-facing protocol.

Every `infra` op honors Model C (§ above): deterministic naming, check-then-act, self-clean
partial state. Every produced resource name carries a tier-tag prefix (`te`/`tap`/`o2`/`img`/
`tfc`/`exr` — see [02-twin-model.md](./02-twin-model.md)) from one source-of-truth naming module,
so a destroy can sweep by tag rather than by shape-matching alone. Credentials (a scoped Proxmox
API token; a dedicated SSH key for the mirror-plane account) live in an adapter config file,
never in the IR or the state store.

For the full transport-split contract, op→backend mapping, and auth model, see
`docs/ADAPTER.md`. For the networking and capture mechanics the adapter implements, see
[04-networking-capture.md](./04-networking-capture.md).

## Destroy

Teardown is the reverse of create: state-driven (it reads the deploy's own op rows to know what
it created), name-guarded (a resource must match the deploying tier's name shape before it's
touched, and a hard protect-guard refuses any match against a foreign tier regardless), and
idempotent (a partial or repeated destroy finishes cleanly rather than erroring). It cascades to
a twin's own Tap/Traffic/Exercise sibling deploys but never reaches into the standing Lab surface
or golden image templates.

## Module boundaries

`axrange/`'s submodules enforce the same boundaries structurally, not just by convention:

- **`ir/`** — IR parsing + schema.
- **`manifest/`** — image manifest/binding loader.
- **`ops/`** — the op-type registry.
- **`validator/`** — the rule catalog + four-pass engine.
- **`planner/`** — DAG construction, the SQLite state store, lease/resource-locks, the
  sequential executor, teardown.
- **`adapter_proxmox/`** — the one-interface/two-backend adapter, deterministic naming.
- **`config_exec/`** — the `config`-class counterpart to `adapter_proxmox/`. The executor routes
  `config`-class ops here by `idempotency_class`; it reuses the adapter's qemu-guest-agent exec
  channel to deliver Ansible **into** the guest — the Ansible render stays platform-agnostic,
  only the delivery channel is Proxmox-specific. In-guest file writes ride the same convention
  everywhere in the codebase: content is base64-encoded and embedded directly in the QGA exec
  command (not passed as separate `input-data`), which sidesteps a guest-agent code path that is
  unreliable with certain byte sequences.
- **`sensors/`** — the sensor-type registry (engine vocabulary consumed by the validator and the
  tap playout).
- **`personas/`** — the persona library (engine vocabulary consumed by the Traffic tier).
- **`twinconfig/`** — the parameterized-IR-template expander behind twin configs.
- **`measurement/`** — ATT&CK-technique coverage joins for the Exercise tier.
- **`o2control/`** — the thin engine client that stands up the O2 surface and drives the Tap/
  Traffic/Exercise playouts, reusing the planner+adapter+state as a spec executor rather than
  running a second orchestrator.
- **`imagebuild/`** — the `axrange image build` pipeline; reuses `adapter_proxmox/`'s REST/SSH/
  config and runs a real planner+adapter deploy for its smoke-test.
- **`api/`** — the control-plane API (clean DTOs, never raw IR/Proxmox shapes).
- **`cli/`** — the CLI.

The planner talks to the adapter only through an `Adapter` protocol and never knows Proxmox; the
adapter operates on op instances and never knows IR or scenarios. This module split is itself the
boundary enforcement — see `CLAUDE.md`'s "Conventions to Follow" for the full directory-layout
rule.

## See also

- [04-networking-capture.md](./04-networking-capture.md) — the SDN networking and capture
  mechanics the adapter implements.
- [02-twin-model.md](./02-twin-model.md) — the tier model, resource tags, and teardown
  protect/cascade matrix this section's "tier-tagged naming" and "destroy" summaries draw from.
- [10-images.md](./10-images.md) — the image build pipeline `imagebuild/` implements.
- [11-api-ui.md](./11-api-ui.md) — the control-plane API and CLI verb grid built on top of this
  engine.
- Formal contracts: `docs/IR_SCHEMA.md`, `docs/PLANNER.md`, `docs/OP_CATALOG.md`,
  `docs/VALIDATOR.md`, `docs/ADAPTER.md`.
