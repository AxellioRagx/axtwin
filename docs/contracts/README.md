# contracts — the version-locked specs

These are the formal contracts implementations must satisfy. They were moved
here **verbatim** from the two package docs trees; their internal
cross-references (`docs/CHANGELOG.md`, `docs/ARCHITECTURE.md §2`,
`docs/design/...`, `LES-NNN`, `GAP-NNN`) point into the archived ledgers under
`archive/engine-docs/` and `archive/discovery-docs/`.

| File | Contract | Owner |
|---|---|---|
| `IR_SCHEMA.md` | The Scenario IR: the authored twin spec (`infra` / `apps` / `overlays` / `activity`, bindings, fabric). | engine (`axrange.ir`, `axrange.validator`) |
| `IMAGE_MANIFEST.md` | The two-tier image catalog: platform-independent manifests and per-platform bindings. | engine (`axrange.manifest`) |
| `VALIDATOR.md` | The rule catalog: rule ids, severities, the four passes. | engine (`axrange.validator`) |
| `PLANNER.md` | IR to DAG, the state store, leases and resource locks, retry and escalation, resume. | engine (`axrange.planner`) |
| `OP_CATALOG.md` | Every op type: inputs, outputs, ordering, cleanup strategy, idempotency class. | engine (`axrange.ops`) |
| `ADAPTER.md` | The Proxmox adapter: REST and SSH backends, the op-to-backend map, the auth model. | engine (`axrange.adapter_proxmox`) |
| `IMAGE_BUILD.md` | The image build pipeline and its conformance checks. | engine (`axrange.imagebuild`) |
| `SPEC_network_ir.md` | aximprint's network IR, the point-in-time structural representation a cast compiles from. The **only** versioned schema in discovery; `discovery/tests/test_ir_schema.py` fails if a `SCHEMA_VERSION` appears outside `aximprint.ir`. | discovery (`aximprint.ir`) |
| `AXAPI_CONTRACT.md` | The `axapi` wire surface `aximprint acquire` uses against an Axellio Xpress appliance. | discovery (`aximprint.acquire`) |

## Change rule

A change to any of these is a contract change: confirm with the user first,
bump the schema or contract version the file names, and update the
implementing code and its tests in the same change. Correcting a transcription
error in `AXAPI_CONTRACT.md` is the one exception (edit freely); re-pinning it
to a new appliance release is not.

Every other shape in discovery (the event model, feature shapes, the plugin
contract, the belief store) is deliberately **unversioned** and changes freely;
ratifying one as versioned is what needs the user, not editing it.
