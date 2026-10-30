# Architecture — design of record

Present tense, current shape only. Root `Claude.md` is the orientation; this
file is the detail behind it. The version-locked contracts are in
`docs/contracts/`; this file summarizes and cross-links them, never restates
them. Names and rules are in `docs/CONVENTIONS.md`; how to run it is in
`docs/OPERATIONS.md`.

## 1. The instrument and its loop

axRange is a personal cyber-range digital-twin instrument for one loop:

| Step | What happens | Code that serves it |
|---|---|---|
| 1. discover | sensor metadata of a real network is baked into an **imprint** (a point-in-time network IR), corrected by operator assertions, and cast into a twin | `discovery/` (`aximprint`), started from the API by `POST /api/discovery/runs`, cast by `POST /api/imprints/{pit}/cast` |
| 2. view and customize | an imprint is renamed, copied and asserted on; a cast or hand-authored twin is inspected and edited | `engine/src/axrange/api` (imprint, twin and topology routes), `ui/` |
| 3. deploy | the twin becomes real VMs and networks on Proxmox | `axrange te deploy`, `POST /api/deploys` |
| 4. drive traffic | personas run on the twin's hosts so it behaves like a populated network | `axrange tfc deploy`, `/api/tfc` |
| 5. tap | the twin's links are mirrored to a sensor | `axrange tap deploy`, `/api/taps` |
| 6. inject an attack | an exercise runs an attack against the standing twin | `axrange exr run`, `/api/exr` |
| 7. confirm | the O2 observability surface shows what the sensor caught | O2 (Grafana over Loki and Prometheus), `axrange.measurement` |

The acceptance test is the loop running end to end with **tapped traffic
reaching a sensor**, confirmed by climbing capture metrics. O2 is stood up out
of band and is not part of that gate.

## 2. Monorepo layout and the one boundary

```
engine/src/axrange     twin plan -> deploy -> destroy, traffic, taps, exercises, O2, the control-plane API
discovery/src/aximprint sensor metadata -> network IR -> cast to a twin bundle
adapters/              deploy-platform plugins, one folder and SPEC.md per platform (proxmox, libvirt)
catalog/               twins, legacy twin-configs, target-environments (scenario IR), personas
config/                adapter.conf (gitignored) + examples
o2/                    the observability stack as deployed assets; o2/tcpdump-sensor; the old console
ui/                    the operator console: Spec.md and the SPA (React + Vite + TypeScript -> ui/dist)
api/Spec.md, ui/Spec.md the two seam specs
docs/                  this set; docs/contracts/ the version-locked specs
```

`engine` must **never import** `aximprint`. `discovery` may import `axrange`.
The API reaches discovery by running the `aximprint` binary as a subprocess
(`axrange.api.recompile`, `axrange.api.discovery`), which is what keeps the
boundary intact while the API still lives inside `engine/`. The API moves into
its own package under `api/` only when a seam needs an in-process call.

Content is data, not code. `axrange.paths` derives the unset defaults for
every content root (`catalog/twins`, `catalog/target-environments`,
`catalog/personas`, `config/adapter.conf`) from the checkout layout; an
explicit flag or `AXRANGE_*` variable always wins.

## 3. Discovery: `aximprint`

aximprint learns a network's **imprint** from sensor metadata and **casts** a
snapshot of it as deployable twin configuration. It is not a sensor: nothing
from `ingest` downward ever sees a packet.

```
appliance / pcap ──(acquire, optional)──▶ sensor artifacts on disk
sensor metadata ──▶ ingest plugin ──▶ events ──▶ windowing ──▶ learners ──▶ sinks
                                                                  │
                                       belief store (evidence log + DuckDB) ◀──┘
                                                                  │ bake
                                              imprint: network IR (a PIT) + assertions
                                                                  │ cast (+ install-cast)
                                       twin: catalog/twins/<twin_id>/ (te + tfc + tap + personas)
```

- **`acquire/`** pulls pcap off an Axellio Xpress appliance over `axapi`
  (`docs/contracts/AXAPI_CONTRACT.md`) and runs a pinned Zeek over it. It
  produces sensor artifacts on disk and nothing else; it imports nothing from
  `events`, and that is enforced by an AST test, not a comment.
- **`ingest/`** holds the `IngestPlugin` registry. Three sensor plugins ship:
  `zeek` (log directories, per-window log trees, or a `parquet:` archive),
  `axquery` (an appliance's already-inferred entity database over HTTP) and
  `suricata` (`eve.json`). Each plugin separates transport from semantics and
  declares a closed `config_schema` (`source` plus the shared run options);
  `aximprint plugins` lists them and the API serves that list. A second plugin
  kind, **reference ingest** (`ingest/reference/`), reads out-of-band documents
  such as the operator assertions file and is kept out of the sensor registry.

  Every new source lands as a plugin that ends in one of the two readers that
  exist, the Zeek log reader or the axquery-shaped HTTP puller, because the
  learners are shaped by them: a listen port needs a sensor-reported
  acceptance state, and Windows roles need the Kerberos, NTLM, SMB and
  DCE-RPC records only Zeek structures. So a **pcap** is read by running the
  pinned Zeek over it into a temporary log directory and handing that to the
  Zeek reader (`ingest/pcap/SPEC.md`), never by parsing packets in Python; a
  **running Zeek** is read where its logs already land — a log directory on a
  reachable host through the `zeek` plugin, or a SIEM through that SIEM's
  plugin (Elastic for Filebeat-shipped `zeek.*`, Splunk for the Corelight
  add-on, Sentinel for its ASIM parser), never through Zeek's Broker, its
  WebSocket event API or a Kafka writer; and a **SIEM** is an `http` plugin
  that copies axquery's locator, env-only credentials and time-bisected
  paging with a vendor leaf fetcher (`ingest/siem/SPEC.md`). The runner that
  executes Zeek is shared by `acquire` and the `pcap` plugin from a module
  below both, which keeps the acquire layering wall intact.
- **`events/`** is the contract both sides speak: `Flow`, `Identity`,
  `Service`, `Alert`, plus `SensorHealth`. Disorder and capture loss are
  surfaced, never swallowed.
- **`windowing/`** is the pivot: one-hour windows with twelve sub-buckets,
  address-to-entity resolution with classified evidence, exact sketches, and
  the address-to-segment mapping. Window close is a pure function.
- **`learners/`** are deterministic extractors (listens, binds, talks, services)
  that write assertions. The ML annotator slot reads the same `WindowResult`
  and emits the same assertion shape; it is not built.
- **`sinks/`** and **`features/`** fan one computed assertion set out to the
  store, the evidence log and the JSONL feature archive (the training corpus,
  not an inference input).
- **`store/`** is an append-only evidence log with a rebuildable DuckDB
  projection; beliefs decay against `last_seen`.
- **`ir/`** bakes a belief store as-of a time into a **PIT**, the
  network IR (`docs/contracts/SPEC_network_ir.md`): entities, segments, edges,
  the unseen census, and the vantage that observed it. `merge` combines PITs
  across vantages or time; `visibility` and `drift` read them.
- **`synth/`** is the deterministic compiler from a PIT to an axRange twin
  bundle: the twin config, a traffic profile and a tap plan, with per-field
  provenance. Operator assertions (`operator_assertions.py`, the
  `axr-assertions/1` document the console writes) are resolved against the
  PIT at compile time. `install-cast` lands the bundle in `catalog/twins/`
  under the twin id (the API's cast action runs both verbs as one).

Every assertion carries an **evidence class**, in precedence order
`asserted > imported > observed > referenced > imputed` (`aximprint.ir.schema`):
what an operator asserted beats what a directory export imported, which beats
what the wire showed, which beats second-hand evidence from inside the capture,
which beats inference. Imputed never masquerades as observed.

## 4. Engine: `axrange`

The engine turns an authored **Scenario IR** into running infrastructure and
back down. It is a library and CLI, not a service: a deploy is a process that
runs to completion with all durable state in a SQLite state store.

```
Scenario IR ──▶ Validator ──▶ Planner (DAG + state store + executor) ──▶ Adapter (Proxmox) ──▶ running twin ──▶ Destroy
```

A twin is platform-independent up to and including its cast: images by
manifest id, abstract resources, topology as segments and attachments. The
platform binds at **plan**, not at cast and not inside the deploy run: a plan
is `plan(twin, target)` — placement, image bindings from
`images/bindings/<platform>/`, per-op identity and names — persisted in the
target's state store under its `(twin, target)` key, deterministic, and
inspectable on its own. A deploy runs a plan. One twin therefore keeps one id
across platforms; what a small target cannot hold is a capacity profile
applied at cast (occupancy, count scaling) and a fit finding from the
validator's cluster pass, never a second cast.

- **IR** (`ir/`, `docs/contracts/IR_SCHEMA.md`): a versioned YAML document with
  `infra`, `apps`, optional `activity`, `bindings` and optional `overlays`
  (identity, data, defense, adversary). Twin configs (`twinconfig/`) are
  parameterized IR templates expanded through the catalog.
- **Validator** (`validator/`, `docs/contracts/VALIDATOR.md`): a shared library
  with four passes, structural and reference halting on first error, semantic
  and cluster accumulating findings. It never modifies the IR and never touches
  platform state.
- **Planner** (`planner/`, `docs/contracts/PLANNER.md`): five passes (emit,
  dataflow, ordering, placement, validate) build a deterministic DAG of ops with
  synthetic barriers; the same inputs always give the same DAG, op ids and
  placement. The state store holds deploys (tagged by tier), leases, resource
  locks, ops and attempts. Every op is idempotent on retry (`infra` ops by
  deterministic naming and check-then-act, `config` ops by Ansible's own
  idempotency); a re-run resumes, `--fresh` mints a clean deploy. Failures
  follow retry-then-escalate to the preceding barrier.
- **Op catalog** (`ops/`, `docs/contracts/OP_CATALOG.md`): `OP-NET`, `OP-HOST`,
  `OP-MIR`, `OP-APP`, `OP-OVL`, `OP-ACT`, `OP-TFC`, `OP-BAR`.
- **Adapter** (`adapter_proxmox/`, `docs/contracts/ADAPTER.md`; plugin index
  under `adapters/`): one interface, two backends. REST covers what the Proxmox
  API exposes; SSH covers the rest (the `tc` mirror hook, the keyed gretap,
  VyOS `vbash`). Every resource name carries a tier tag from one naming module.
  A deploy target names its platform and the platform factory picks the
  adapter (§13); the op catalog and the planner's DAG do not know which.
- **Config execution** (`config_exec/`): the `config`-class counterpart; Ansible
  rendered platform-agnostically and delivered into the guest over the
  qemu-guest-agent exec channel. In-guest file content rides base64 inside the
  exec command.
- **Destroy**: state-driven, name-guarded, idempotent, cascading from a twin to
  its own tap, traffic and exercise deploys and never into the standing O2
  surface or golden images.
- Leaf registries the engine's vocabulary is read from: `sensors/`,
  `personas/`, `overlays/`, `manifest/` (`docs/contracts/IMAGE_MANIFEST.md`).
  `o2control/`, `measurement/`, `fidelity/`, `imagebuild/`, `api/` and `cli/`
  import downward into the engine; the engine imports none of them.

## 5. The five tiers

Every resource the engine creates carries a tier tag in its deterministic name.
The tag is the blast radius.

| Tier | Tag | What | Lifecycle |
|---|---|---|---|
| Lab | `o2` | the standing observability and orchestration surface | persistent; `axrange o2 deploy|destroy`; never swept by another tier |
| Twin | `te` | a deployed target environment | `axrange te deploy|destroy`; destroy cascades `exr -> tfc -> tap -> te` |
| Traffic | `tfc` | personas bound onto a deployed twin's host roles | its own deploy; self-only destroy; converge-in-place re-apply |
| Tap | `tap` | mirror and gretap bindings from a twin's links to sensors | a name-shape tag on a `te`-tiered `tap-<te>` deploy, not a sixth tier; independent apply and destroy |
| Exercise | `exr` | an ephemeral attack, defense and measurement run | every apply mints a new deploy; no automatic reap |

`img-<slug>` marks golden templates; no tier's destroy ever touches them. An
unrecognized name shape fails closed. Four tiers share one verb grid,
`axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}`; `te` adds
`inventory`, `tfc` adds `set-host`, `exr` uses `{deploy,run,status,destroy}`.

## 6. Networking and capture

Each twin gets its own Proxmox SDN zone: a **Simple** zone (one OVS bridge per
network) when no network declares a VLAN, a **VLAN** zone (one underlay bridge,
tagged vnets) when any does. Routers are `kind: router` nodes in two flavours,
Debian with nftables (configured over the guest agent) and VyOS (configured over
SSH, discovered by MAC). A VyOS-only `fabric:` block adds IS-IS or OSPF,
iBGP with route reflection, eBGP at borders and BGP-EVPN/VXLAN overlay with
symmetric IRB; a tenant VRF has no internet egress yet.

Capture is a SPAN session: a per-tap `tc` mirror on the VM's own tap interface
sends frames into a keyed gretap over a dedicated transport vnet to the sensor,
which decaps by flow. It works identically in either zone type because it
mirrors at the VM interface, not the bridge.

## 7. O2: the standing surface

O2 is stood up once and shared by every twin. Its components sort into
**watch** (sensors, external packetXpress sinks, host agents, truth markers,
feeding Loki and Prometheus), **act** (Caldera with Sandcat for attack, the
Wazuh manager for defense, `o2control` for stand-up and playout) and **steer**
(the control-plane API and CLI; Grafana is telemetry only and drives nothing).

`axrange o2 deploy` stands up the **sensor half** through the engine as an
`o2`-tagged deploy; `o2control` is a thin engine client, not a second
orchestrator. The **server half** (`o2/compose/`: Grafana, Loki, Prometheus,
Caldera, Wazuh manager) is brought up by hand with docker compose on a host
the twin can reach; the engine never shells out to compose. `o2control`
publishes an inventory of standing sensors that every playout binds against.

## 8. Sensors and taps

Sensor types are a registry (`sensors/`): `tcpdump-sensor` (rotating pcaps,
installed at deploy), `suricata-sensor` and `zeek-sensor` (pre-baked images).
Capture runs as a detached transient systemd unit with an `is-active` check, so
a dead capture fails loud. A hand-provisioned appliance is declared as an
**external sensor** in `adapter.conf`; it is auto-protected from every
teardown, and `verify-capture` is skipped for it with an explicit warning.

A **tap point** is `scope` plus `target`, never a direction: `port` (one host's
leg on one net, both directions), `segment` (every port on a net, routers
included, rx only), `perimeter` (every router port on a net, both directions;
`perimeter: wan` is the twin's edge) and `boundary` (the joining router's port
on each of two nets, rx each side). Direction is derived so every frame is
captured once. Tap points are derived from the twin's published topology at
bind time; the twin substrate is tap-agnostic and the tap plan is the sole
authority on what is captured. A tap plan binds `tap | tap_point | selector`
to one or more sensors (fan-out duplicates frames on the transport wire;
`mode: exclusive` claims a point for one sensor). Apply is idempotent with a
de-scope pass; a plan may name a twin by scenario or config name and resolves
to the newest live deploy.

## 9. Personas and traffic

A **persona** is a twin-agnostic behavioral archetype: id, `os_family`, a
`drive`, and `emits[]` naming protocol, peer role, cadence, volume and
transport, with hard `requires` and `optional` peers. Five drivers share one
`start | stop | heartbeat` interface: `scripted` (shape and load), `replay`
(a staged template), `real-service` (a real daemon already on the host),
`command` (real client tools such as `dig` and `curl` on Linux, the built-in
domain-member engine on Windows) and `browser` (headless Chromium for real TLS,
HTTP/2 and QUIC). Peer addresses are resolved in the orchestrator and baked as
literals, so a guest never resolves a name unless a persona opts in. Every
driver launches its in-guest process as a detached transient unit. The persona
catalog is data in `catalog/personas/`.

A **traffic profile** assigns personas onto a twin's published host roles.
`tfc deploy` composes one `drive-persona` op per assignment and runs it through
the engine executor; re-apply is idempotent with a de-scope pass. Dedicated
`PSN` validator rules check the catalog.

## 10. Exercise and measurement

An **exercise spec** names a twin and an attack in one or both modes:
**internal activity** (the engine's inline-steps runner over the guest agent,
deterministic and replayable; the AD kill chain is the running example) and
**external Caldera** (plant a Sandcat agent, the operator drives the operation).
Both reuse existing ops. Each run computes a `playout_hash` over what it does,
so two runs with the same hash are provably the same attack. Two concurrent runs
on one twin must target disjoint hosts.

Measurement joins attack **truth markers** (one per technique-labelled step,
shipped to Loki) with Suricata, Wazuh and host-audit **detections** on
(technique, host, time window) into per-run coverage. `axrange.measurement` is
a standalone leaf the engine never imports.

## 11. Images

Every deployable image is a platform-independent **manifest** (capabilities it
`provides:`, lifecycle `current -> deprecated -> withdrawn`) plus a per-platform
**binding** (storage, cloud-init, hardware defaults, the build recipe).
`axrange image build <image>` bakes a template from an upstream base image with
the guest agent preinstalled, runs capability probes and a smoke deploy, seals
it, and rewrites the binding's `template_vmid`. Image roots live outside the
checkout and are passed with `--images`.

## 12. The control plane

`axrange serve` is a FastAPI service over the same library entry points the CLI
calls. Every response is a purpose-shaped DTO (`api/dtos.py`); raw IR and
Proxmox shapes never cross the boundary. Reads are open on a trusted loopback
bind; every write carries a bearer token; secrets-bearing reads are masked
unless `?secrets=true` with a token. A target's `protected_deploys` list refuses
every write without `override_protection=<id>`; a plan pin
(`expect_plan_id`) refuses a deploy whose plan moved under the preview.

Route families: catalog reads (pcaps, imprints and their cast and provenance,
twin configs, scenarios and topology); validate and plan (pure); the twin
lifecycle (`/api/deploys`, inventory, hosts, health, access facts, scorecard);
the per-tier playout trios (`/api/o2/surface`, `/api/taps`, `/api/tfc`,
`/api/exr`); targets and range; per-host persona apply; the console canvas
files; discovery runs and discovery sources. The full route list and the seam
contracts are in `api/Spec.md`.

**Discovery runs (seam A1).** `GET /api/discovery/plugins` lists the installed
ingest plugins with their schemas (through `aximprint plugins`).
`POST /api/discovery/runs {plugin, config, name}` validates the config
against the plugin's closed schema, refuses an unknown plugin, an unusable or
already-present imprint name or one another run is baking, then runs
`ingest -> bake` in a thread and answers `202` with the run record.
`GET /api/discovery/runs/{id}` reports `queued | running | completed | failed`,
the stages done, the error or the `pit_id` and `pit_name`. The imprint is
then listed, renamed, copied and asserted on under `/api/imprints`, and
`POST /api/imprints/{pit}/cast` lands a twin under `AXRANGE_IMPRINTS_DIR/<twin_id>/`
which resolves through the twin routes. Run records persist in the discovery
DB. A `source` is sensor metadata, never a pcap.

**Discovery sources (seam A7).** A saved way to reach a sensor's query API:
`{name, plugin, endpoint, access, secrets, defaults}` in one SQLite file,
`AXRANGE_DISCOVERY_DB` (`~/.axrange/discovery.db`, 0600, outside the checkout;
not a table in the planner's `state.db`, whose schema is its own). `access` is
`direct | ssh_tunnel` × `none | basic | api_key`; the endpoint is the plugin's
base URL as seen from where the access path lands (an appliance's loopback
`:9090` behind the tunnel). Only an `http`-kind plugin (`source_kind` in the
listing) can back one. `probe` opens the path and runs `aximprint probe`
(`IngestPlugin.probe`, `/api/info` for axquery) to learn the span and census;
a run from a source opens the path for the whole chain (`ssh -N -L` in
BatchMode, torn down with it), rewrites `source` to the local end, carries the
credential in the subprocess environment (`AXIMPRINT_SOURCE_*`), and resolves
"the last N days" from the source's own span at start. Reads never return a
secret. The console (`ui/`) is the consumer: save, test, run, poll, open the
imprint.

## 13. Seams and what is frozen

The core holds contracts; functionality lives in plugins that self-register:
`IngestPlugin` (discovery adapters), the `Adapter` protocol (deploy platforms),
and the sensor, persona and attack registries. A new capability is a new plugin
with its own `SPEC.md` beside its code and its own tests or verifier; the
filesystem is the index.

**The deploy-platform seam** is wider than `Adapter.run`. The engine reaches a
platform through five surfaces, and a platform plugin implements all five:

| Seam | Protocol | What the engine asks |
|---|---|---|
| ops | `Adapter` | run one catalog op, return its typed outputs |
| guest exec | `GuestExec` | ping, exec, file in, file out, against a guest by its platform handle |
| inventory | `Inventory` | guest → address and host, host facts, the platform's live tier-tagged resources |
| naming | `ResourceNamer` | tier-tagged names and the platform's stable per-op identity (Proxmox VMID, libvirt UUID) |
| teardown | `Teardown` | sweep by tier-tag shape under the protect guard |

Today the Proxmox modules are imported directly for the last four; the
protocols are extracted from them before a second platform is built, after
which each adapter's code lives under `adapters/<platform>/` as its own
package and the engine imports only the protocols. Selection is a closed
factory keyed by the target's `platform` (`api/Spec.md` A4) — `proxmox`,
`libvirt`, VMware when built — that fails by name on anything else; it is not
a registry and does not become one until the set is open. Each platform's
`SPEC.md` under `adapters/` carries its op mapping, config and acceptance.

Platform-specific is adapter-only (`CONVENTIONS.md` §4): the op catalog, the
IR and cast, the planner's resource categories, the state store, the API's
DTOs and the tracked config examples use the engine's neutral vocabulary, and
a platform's config schema and image-binding schema live with its adapter.
Where the version-locked contracts still carry one platform's words, the
Proxmox adapter's `SPEC.md` lists them; each is a confirm-first contract
bump, sequenced with the seam extraction above.

Kept in tree, not maintained, not on the critical path: the golden-twin and
fidelity-scorecard direction (`axrange.fidelity`, `aximprint drift`), the
AI-scenario-research pipeline (`ai-crafted/`), the CorpAPCN fidelity loop, and
the old Grafana-era console under `o2/console/`.
