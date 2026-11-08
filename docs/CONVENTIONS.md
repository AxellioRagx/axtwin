# Conventions — names, tags, layering, rules

Companion to root `Claude.md`. This file covers what things are called, which
import directions are allowed, and the engineering and documentation rules that
apply to every change.

## 1. Names

| Form | Where | Rule |
|---|---|---|
| `axRange` | prose, UI titles | the product display name; capital R, no space |
| `axrange` | Python package, CLI command, dist name | the code form, all lowercase |
| `aximprint` | the discovery package, its CLI, its dist name | one spelling everywhere |
| `axellio-…` | anything published into a foreign ecosystem | the org namespace |
| `axr` | retired as a resource-name prefix | survives only inside `axrange` and grandfathered transient names; never introduce a new one |

Two terms stay distinct on purpose. A **scenario (IR)** is the authored spec
(`docs/contracts/IR_SCHEMA.md`; code and API keep `scenario` identifiers). A
**target environment** is the deployed instance, the `te` tier,
`catalog/target-environments/` being the library of scenarios that define one.

In discovery the metaphor (imprint, cast) lives in prose and UX. Identifiers
stay role-shaped: the **learner**, the **belief store**, the **synthesizer**,
a **PIT** (a materialized point-in-time network IR), a **cast** (a compiled twin
bundle), an **imprint id** (the stable catalog name of an installed cast).

The lifecycle verbs (2026-11-08), the same in the console, the API's prose and
the docs: **Deploy** stands a twin up; **Destroy** brings the standing deploy
down (the engine's own verb, `axrange te destroy`, `DELETE /api/deploys/{id}`,
which cascades to the tfc and tap children); **Redeploy**, **Apply** and
**Persist** change a standing deploy; **Delete** removes a twin config from the
catalog; **Remove** takes an element out of the design. Never "undeploy", and
never Delete for a deploy.

## 2. Resource tier tags

Every platform resource the engine creates carries a tier tag in its
deterministic name (the adapter's `ResourceNamer`; `axrange.adapter_proxmox.naming`
today); the tag is the blast radius, and the teardown guard refuses any
foreign tier's shape. The shapes below are the engine's; a platform's own
identity (a VMID, a domain UUID) is the adapter's and never leaves it.

| Tag | Name shapes | Notes |
|---|---|---|
| `te` | `te<6hex>` zone, `tev<5hex>` vnet, `tebr_*` bridge, `te-<6hex>-<host>` VM | a twin; destroy cascades to its `exr`, `tfc` and `tap` deploys, in that order |
| `tap` | `tapg_<6hex>` gretap, `tapm-<vmid>` mirror | a name-shape tag on a `te`-tiered `tap-<te>` deploy, not a deploy tier |
| `o2` | `o2z<5hex>` zone, `o2v*` vnet, `o2br_*` bridge, `o2-*` VM | the standing surface; only its own destroy may sweep it |
| `img` | `img-<image-slug>` | golden templates; refused by every destroy, including its own |
| `tfc` | `tfc<5hex>` zone, `tfcv<4hex>` vnet, `tfcbr_*` bridge, `tfc-<6hex>-<host>` VM | traffic; self-only destroy |
| `exr` | `exr<5hex>` zone, `exrv<4hex>` vnet, `exrbr_*` bridge, `exr-<6hex>-<host>` VM | exercise; self-only destroy |

`tier` is a deploy-scoped parameter threaded from `Plan.tier` through naming,
state, executor and teardown; the predicate and shape tables live in one place
and an unrecognized tier fails closed. A config-declared external sensor is
protected unconditionally.

## 3. Layering

Import direction is the design constraint. Each rule below is checked by a
test where one exists; add the test when you add the rule.

**Across packages**

- `engine` never imports `aximprint`. `discovery` may import `axrange`. The API
  reaches discovery by subprocess until it leaves `engine/`.

**Inside `axrange`**

- `validator` never imports `planner`; `planner` talks to the adapter only
  through the `Adapter` protocol; the adapter knows ops, never IR or scenarios.
- `sensors`, `personas`, `overlays`, `ops`, `manifest`, `ir` are leaves the
  validator and planner read; nothing in them imports upward.
- `o2control`, `measurement`, `fidelity`, `imagebuild`, `api`, `cli` import
  downward into the engine; the engine imports none of them. An engine deploy
  never shells out to `docker compose`.
- The API calls library entry points, never a shelled-out CLI, and exposes
  DTOs only.

**Inside `aximprint`**

- `acquire` imports nothing from `events` or anything downstream, and nothing
  downstream imports `acquire`; the CLI is the only wiring point (AST-walk test).
- Downward only: `ingest` plugins import `events`; `windowing` imports
  `events`; `learners` import windowing outputs; `sinks` and `features` import
  windowing outputs and events and are imported by nothing but the CLI;
  `store` is imported by writers; `ir` reads `store`; `synth` reads `ir`.
- `provenance`, `catalog` and `operator_assertions` sit below every layer and
  may be imported from anywhere, except from `acquire`, which must not know
  what a twin role is.
- Only `aximprint.ir` may declare a `SCHEMA_VERSION` (`tests/test_ir_schema.py`).
- Reference-ingest readers (`ingest/reference/`) have their own registry and
  stay out of the sensor registry.
- `import aximprint.sinks` alone yields an incomplete registry; the `jsonl`
  sink registers only once `aximprint.features` is imported too.

## 4. Engineering rules

- **Fail loud, fail closed.** Guards are positive gates; an unrecognized shape,
  plugin, key or tier is refused by name, never passed through or defaulted.
  A dead daemon behind a green result is the failure mode to design against.
- **Determinism is a feature.** Same inputs, same outputs: the planner's DAG and
  placement, window close, the cast compiler, the feature archive. Seeded
  randomness wherever randomness exists. Discovery's determinism tier
  (`discovery/scripts/verify_determinism.py`) runs the pipeline twice and
  compares bytes.
- **Idempotent on retry.** Every engine op, every playout apply, every driver
  `start()`. Re-apply converges and de-scopes; it never errors on what already
  exists or leaves a stale instance running.
- **Platform-specific is adapter-only.** Anything that names a deploy
  platform — its API, resource kinds and identities (a VMID), its CLI tools,
  its zone/vnet/bridge vocabulary, its guest-control channel, its config keys
  — lives only under that platform's adapter (`adapters/<platform>/`, code,
  config example and binding schema alike). Everything else speaks the
  engine's neutral vocabulary: segment, node, attachment, image (manifest
  id), guest handle, host (placement), tap, mirror, first-boot init, guest
  control. That covers the IR, twin configs and the cast, the op catalog,
  planner, validator, state store, API DTOs, UI, catalog content and the
  tracked examples under `config/`. The test: delete an adapter folder and
  nothing left in the repository names that platform except the orientation
  prose that says it exists.
- **Content is data, not code.** Twins, scenarios, personas, image manifests
  and role catalogs are YAML files in `catalog/` or package data, read through
  registries; adding one is adding a file.
- **Provenance everywhere.** Every discovery assertion carries evidence class,
  confidence, source and time bounds. Imputed never masquerades as observed.
- **Forensic completeness.** Discovery collects everything it observes and
  redacts nothing; disclosure is the consumer's decision. Cardinality bounds
  stay (they are store economics, and truncation is always disclosed).
- **Run data never enters the repo.** Captures, Zeek trees, belief stores,
  feature archives, planner state DBs, deploy logs, screenshots and pcaps live
  outside the checkout (`~/.axrange/` by default for engine state). Pass
  `--images` and `--db` explicitly.
- **No secrets in the IR or the state store.** Credentials live in
  `config/adapter.conf` (gitignored), the API token in the environment, and a
  discovery source's password or API key in `~/.axrange/discovery.db` (0600).
  A secret never reaches `argv`, a source locator, a log line or a read
  response: it rides the subprocess environment (`AXIMPRINT_SOURCE_*`), and
  a record says only `has_password` / `has_api_key`. SSH access stores a key
  *path*, never key material.
- **In-guest conventions.** File content rides base64 inside the guest-agent
  exec command, never as separate input data. A long-running in-guest process
  (a capture, a persona) is a detached, deterministically named transient unit,
  never a backgrounded child of the exec session.
- **Containment.** Persona clients get orchestrator-resolved literal addresses;
  a guest resolves nothing unless the persona opts in and the twin owns its DNS.
- **Do not refactor the large modules for aesthetics** (`o2control/taps.py`,
  `cli/main.py`, `api/service.py`); they carry cluster fixes. Touch them only
  when a critical-path seam requires it.

## 5. Documentation rules

- Root `Claude.md` is the orientation and stays at or under two pages.
- `docs/` holds exactly the live set listed in `docs/README.md`. A design
  change edits `ARCHITECTURE.md` or `CONVENTIONS.md` in place.
- A feature or plugin carries its own `SPEC.md` beside its code (from
  `Spec.template.md`): what it is, the seam, the config schema, acceptance.
  Existing code gets one only when you are already editing it for a real reason.
- Git history is the changelog. No CHANGELOG, no work orders, no lessons store,
  no doc-counter hooks, no `docs/WORKFLOW.md` (it would re-arm the host-global
  hooks).
- `docs/SESSION_STATE.md` is replaced at each handoff, never appended, and
  stays under 8 KB; a stale handoff is worse than none because it is believed.
- Docs are present tense and carry no counts (tests, rules, ops, personas are
  not progress).

## 6. Scope and git

- No new scope without explicit sign-off: no new tier, abstraction, schema,
  direction or work order on your own initiative. Name the tempting expansion
  in one line and stop.
- Every task names which step of the loop it advances; if none, it does not
  happen.
- A change to a version-locked contract (`docs/contracts/`) is confirm-first
  and bumps the version.
- One task, one branch; finish before starting the next `wip/*`.
- Plateau rule: a metric flat for about two iterations goes to the user.
- The user reviews every diff before it is committed. Outbound push only on
  explicit request; inbound sync is the user's own manual step.
