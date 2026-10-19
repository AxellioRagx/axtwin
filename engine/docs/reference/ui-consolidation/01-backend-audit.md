*Backend audit for the consolidated UI — taken 2026-09-29 at commit b3c3745e.*

axRange backend audit for a consolidated UI

This was a read-only audit. No files were edited and no cluster was contacted. The only local reads beyond source code were a `mode=ro` sqlite read of `~/.axrange/state.db` and directory listings. Line numbers are cited as file:line. Anything marked "(inferred)" was reasoned rather than read directly.

## Bottom line

- **(a) What is deployed where:** there is no single source of truth. `state.db` has no cluster column. Which cluster a deploy is on is implied only by whichever `adapter.conf` the process loads.
- **(b) Live topology:** it can be rebuilt from the state store alone via `/api/deploys/{id}/inventory`, with no IR needed.
- **(c) Personas per node:** the data is in the tfc deploy's `drive-persona` op rows. `GET /api/tfc` serves it only as persona → hosts. Callers must pass the concrete te deploy id, because a twin name does not resolve there.
- **(d) Change one node's persona:** does not exist. The smallest unit today is a whole traffic profile. Every apply re-drives every persona instance. An apply that lists only one assignment would **stop every other persona on the twin** (the de-scope pass).
- **(e) Health:** almost everything served is recorded op status, not live. Live VM, QGA and persona liveness primitives exist in code but no endpoint calls them.
- **Long-running jobs:** there is no websocket, SSE or job API. Only `te` deploy is async, and you poll it. tap/tfc/exr/o2 applies and all destroys are synchronous HTTP calls.

---

## 1. HTTP API (`axrange/api/`, `axrange serve`)

### Architecture
- `app.py` holds thin FastAPI routes over `service.py`, which wraps the engine. Responses are DTOs from `dtos.py`.
- Errors come back as `{"error","detail"}` with the status taken from `ApiError` (app.py:107-112).
- Routes are plain `def`, so Starlette runs them in its thread pool.

### Auth
- `_is_authorized` (app.py:44-58) is a constant-time bearer compare against `settings.api_token`, which comes from `AXRANGE_API_TOKEN` or `--api-token` (settings.py:86).
- If the token is empty, writes are open too.
- Write routes use `Depends(write_token)` (app.py:61-68).
- Reads are open. `?secrets=true` on access, o2 info and verbose taps requires the token.
- Default bind is 127.0.0.1:8080 (serve.py:15-18). serve.py:71 warns if you bind non-loopback without a token.
- There is no CORS middleware (grep found none), so the SPA must be same-origin or proxied.
- GAP-084 records the standing `:8080` serve as an unauthenticated write surface.

### Settings
`ApiSettings` (settings.py:53-146):

| Setting | Default / source |
|---|---|
| `db_path` | `AXRANGE_DB` or `~/.axrange/state.db` (:44-50) |
| `scenarios_dir` | — |
| `images_root` | — |
| `adapter_config` | none → `/etc/axrange/adapter.conf`, then `./adapter.conf` |
| `api_token` | — |
| `spa_dir` | — |
| `twin_configs_dir` | — |
| `imprints_dir` | — |
| `aximprint_bin` / `aximprint_store` / `aximprint_timeout_s` | — |

There is **one db and one adapter config per process.**

### Routes (all in app.py)

**Health and catalogs** (all reads)

| Method + path | Line | Backed by |
|---|---|---|
| `GET /api/health` | 115 | `service.health` (service.py:92): opens state.db and returns `{status, version, state_db:bool}`. No cluster check. |
| `GET /api/scenarios` | 119 | Directory glob of `scenarios_dir` |
| `GET /api/twin-configs`, `GET /api/twin-configs/{id}` | 186, 190 | `twinconfig.registry` |
| `GET /api/targets` | 732 | service.py:1601-1618. Returns **at most one** target (`DEFAULT_NODE` plus API URL, redacted) from the single adapter.conf. |
| `GET /api/pcaps` | 737 | Stub returning `implemented=false` (service.py:2297) |

**Topology and nodes** (reads; IR re-parse plus state.db)

- `GET /api/scenarios/{id}/topology?deploy_id=&source=` (137)
  - Resolves the id against scenario → twin-config → imprint, and re-parses the **current catalog IR**.
  - Builds network and host nodes, with edges from `net`/`nets`.
  - Optional per-node "worst op status" overlay from state.db (service.py:949-1047).
  - `deploy_id=latest` means the latest deploy of **any tier** (service.py:1010-1012).
- `GET /api/scenarios/{id}/nodes/{node}?deploy_id=&live=&source=` (147)
  - Returns IR facts, placement, vmid (from `clone-vm` outputs), and declared apps/activities/overlays.
  - Does **not** include tfc personas (`_declared_for_node`, service.py:1313).
  - `live=true` returns `{"available": false, "note": "... not implemented"}` (service.py:1293-1297).
  - If `deploy_id` is unset it falls back to the newest deploy of any tier (service.py:1252-1255). That can be a tfc or tap row (a latent bug).
- `GET /api/scenarios/{id}/topology-export?source=` (168)
  - File read of `<id>.topology.json` beside the twin-config or imprint: aximprint's observed segments, nodes and edges. 404 if absent.

**Imprints and the Console canvas** (read unless noted)

| Method + path | Line | Notes |
|---|---|---|
| `GET /api/imprints` | 201 | |
| `GET /api/imprints/{id}` | 205 | |
| `GET /api/imprints/{id}/provenance` | 209 | |
| `GET /api/imprints/{id}/topology` | 221 | Alias of topology-export |
| `GET /api/imprints/{id}/cast` | 232 | Compiled bundle summary: segments, node_specs, rosters, fingerprints |
| `GET /api/imprints/{id}/assertions` | 236 | ETag |
| `PUT /api/imprints/{id}/assertions` | 250 | Write, If-Match or body etag, 412 on stale |
| `GET /api/imprints/{id}/layout` | 266 | |
| `PUT /api/imprints/{id}/layout` | 273 | Write |
| `POST /api/imprints/{id}/apply` | 287 | Write. Synchronous save + aximprint recompile subprocess, with a per-imprint lock. Refused 422 for `twins/<id>/` folders (service.py:3497-3511). |
| `GET /api/imprints/{id}/diff?since=` | 305 | |
| `POST /api/imprints/{id}/plan` | 320 | Pure, no auth |
| `POST /api/imprints/{id}/deploy` | 331 | Write. Pass-through to `create_deploy`. |

**Validate and plan** (pure, no auth, `scenario` only)
- `POST /api/validate` (347) and `POST /api/plan` (354) take body `{scenario, target_node?}`.

**Twin (te) deploys**

- Reads:
  - `GET /api/deploys?limit=&status=` (372). The default excludes `destroyed`. `DeploySummaryDTO` is `{deploy_id, scenario_id, scenario_version, status, started_at, updated_at, completed_at}` (dtos.py:141-148). It has **no `tier`, `plan_id` or cluster field**, so tfc and tap rows are indistinguishable from te rows except by id prefix. Tap rows are stored with `tier="te"`.
  - `GET /api/deploys/{id|latest}` (384): `{deploy, plan_id, placement, ops[{op_id, op_name, op_catalog_id, kind, status, started_at, completed_at, attempts[]}], counts}`. It runs one attempts query per op (service.py:1412-1434).
  - `GET /api/deploys/{id}/inventory` (391): host roster (host_id, vm_name, vmid, pve_node, vm_status, role, role_baseline, kind, nets[], os, listen) plus net_bridge and egress. Roles include tfc `assign-role` overrides (service.py:1457-1531).
  - `GET /api/deploys/{id}/scorecard?probe=&include_deferred=&reference=` (400): fidelity scorecard. `probe=true` writes a script into every guest.
  - `GET /api/deploys/{id}/access?secrets=` (434): per-host ip, user, masked password and ssh/rdp hint, plus subnets and egress. It does a live Proxmox/QGA IP lookup only for hosts with no recorded static IP.
- Writes:
  - `POST /api/deploys` (454). Body is `CreateDeployRequest`: exactly one of `scenario`/`config`/`imprint`, plus `params`, `target_node`, `fresh`, `update`, `allow_destroy`, `reapply`. It is **async**: it takes the per-scenario lease (409 on contention), writes the deploy row, starts a daemon thread and returns `{deploy_id, status:"running", detached:true, config_warnings}` (service.py:1633-1849).
  - `POST /api/deploys/{id}/run` (466): always returns `not_implemented` (service.py:2132-2150).
  - `DELETE /api/deploys/{id}?allow_destroy=` (474): **synchronous**. It cascades exr → tfc → tap and then sweeps te, with a lease heartbeat (service.py:2171-2291). It returns an actions list plus `cascade_errors`.

**O2 surface** (the standing Lab tier)
- `GET /api/o2/info?secrets=` (504) resolves endpoint URLs and external-sensor facts **live**.
- `GET`/`POST`/`DELETE /api/o2/surface` (521/527/538). GET reads state.db. POST and DELETE are synchronous.

**Taps**
- `GET /api/o2/taps?target=&deploy_id=&verbose=&secrets=` (570). Concise mode returns `TapPlanStatusDTO`. Verbose mode returns `TapInfoDTO` with per-binding host, net, nic, sensors, gre_ports and directions.
- `POST /api/o2/taps` (600) is a synchronous apply. `DELETE` (612) is synchronous.

**Traffic (tfc)**
- `GET /api/tfc?target=&deploy_id=` (641) returns `TrafficPlanStatusDTO{deploy_id, twin, status, assignments[{persona, hosts[]}], warnings}` (service.py:2559-2617). It reads state.db only.
- `POST /api/tfc` (648) is a **synchronous** full-profile apply. Body is `TrafficApplyRequest{target|twin, roles[], assignments[], replay[], target_node}` (dtos.py:619-631).
- `DELETE /api/tfc` (658) is synchronous.

**Exercise (exr)**
- `GET`/`POST`/`DELETE /api/exr` (687/694/704). POST and DELETE are synchronous.

### Serving the SPA
- `_maybe_mount_spa` (app.py:824-873) mounts `StaticFiles(html=True)` at `/` when `spa_dir` is set and exists.
- It adds a history fallback to `index.html`, except for `api/*` paths and asset suffixes (:767-791).
- Cache policy (:794-821): `index.html` is `no-cache`; `assets/*` is immutable.
- Flag: `axrange serve --spa-dir` (cli/main.py:984). Headless otherwise.

### Progress and jobs
- There is no websocket, SSE, `StreamingResponse` or job resource (grep of `axrange/api` found none).
- The only async path is te deploy. You poll `GET /api/deploys/{id}` for per-op status and attempts.
- All tier applies (tap/tfc/exr/o2) and every destroy block the request for the whole executor run. For example, the live tfc has 84 `drive-persona` QGA ops, each with a 120 s timeout (ops/registry.py:1073).
- Imprint apply is synchronous by design (doc 15 §10).

---

## 2. Deployment state

### `state.db`
- SQLite in WAL mode, with busy_timeout 5000 (planner/state.py:326-337). Schema is v3 via `PRAGMA user_version`. An old-schema file raises `StateStoreSchemaError` (:71, :353-378).
- The file path is `--db`, `AXRANGE_DB`, or `~/.axrange/state.db` (api/settings.py:44-50).
- It is one file per operator install, not per cluster. Stray copies also exist: `deploy-dev45/state*.db` and an untracked `scenarios/fabric-verify/state.db`.

Tables (state.py:214-311):
- `deploys`: deploy_id PK, scenario_id, scenario_version, scenario_schema_version, plan_id, ir_hash, status, placement_json (host → PVE node), started/updated/completed_at, and `tier` (te/o2/tfc/exr; **tap playouts are stored as `te`**).
  - It has **no cluster, API URL, source path, params, variant or profile-hash column.**
- `leases`: scenario_id PK → deploy_id, TTL 300 s, heartbeat-renewed.
- `resource_locks`: e.g. `vm:<vmid>`.
- `ops`: composite PK (deploy_id, op_id). Columns: op_name, catalog id, kind, scope_json, inputs_resolved_json, inputs_from_outputs_json, outputs_json, cleanup_strategy, idempotency_class, status, times.
- `op_attempts`.

Status enums (:94-118):
- Deploy: running / completed / failed / cancelled / destroyed.
- Op: pending / running / completed / failed / halted-due-to-upstream / destroyed.

What the live `~/.axrange/state.db` holds today (read-only query):
- `corp-twin-07cfcdb2` (te, completed; plan_id `corp-twin-7de6524d`)
- `tap-corp-twin-07cfcdb2-8176c5ad` (tier te, completed)
- `tfc-corp-twin-07cfcdb2-785b1a06` (tfc, completed, 84 `drive-persona` ops)
- No live o2 row. Sensors are `EXTERNAL_SENSORS` in adapter.conf.

### Sibling naming convention
- tap: plan_id `tap-<te_deploy_id>`
- tfc: plan_id `tfc-<te_deploy_id>` (traffic.py:1616)
- exr: `exr-<te>-*`
- `o2control/cascade.siblings_for_te` is the one place that knows this.

### CLI read verbs
- `list-deploys [--tier] [--limit]` (cli/main.py:2346-2411) prints deploy_id, tier, scenario and status, plus started_at in the text table only. Tap rows are shown as `tap` by a structural check (`_display_tier`, :2414-2427). There is no `--json`.
- `te status [ID]` (:1853-1902) prints the op tree. With `--json` it gives deploy_id, scenario_id, version, status and per-op name/kind/status.
- `te info [ID]` (:1925) prints access facts, the same as `/access`.
- `te inventory` and `te scorecard` match their API peers.
- `o2 status|info` prints surface status and sensor inventory from state, and `info` adds live endpoint/sensor resolution (o2control/control.py:364-386; cli :2743-2816).
- `tap status|info`: bindings from state. `info` adds external-sensor live facts.
- `tfc status` (:3820): persona → hosts from COMPLETED `drive-persona` rows.
- `tfc info` (:3831-3926): per instance it gives instance_id, host_id, persona, vmid and peers. Given a profile file, it shows the resolved-but-unapplied view. **There is no API peer for `tfc info`.**

### Cluster selection
- `load_config` (adapter_proxmox/config.py:411-442) resolves in this order: overrides > env > first file found (explicit path, else `/etc/axrange/adapter.conf`, else `./adapter.conf`) > defaults.
- The repo `adapter.conf` points at `10.80.76.108` with `DEFAULT_NODE=dev45`. `pxtwin1` appears only as the example default (config.py:19-20, 560-570).
- `ClusterContext.single_node(cfg.default_node)` is used everywhere (e.g. traffic.py:1614). GAP-011 records the lack of multi-node placement.
- `twins/<id>/twin.yaml` has `cluster: dev45`, typed in twinconfig/manifest.py:101, but **nothing in `axrange/` reads it** (grep found no consumer).

### Multiple clusters and source of truth
- One process sees one adapter.conf and one state.db.
- You could point two `serve` processes with separate DBs and configs at dev45 and pxtwin1. Nothing correlates them, and nothing stops two configs from sharing one DB, which would mis-attribute rows (inferred).
- The **only** "what is deployed" record is `deploys` rows where status is not destroyed. The cluster is implicit, and there is no reconciliation against Proxmox (no `/cluster/resources` sweep).

---

## 3. Twins, golden twins, casts and PITs

### Five-tier twin model (docs/design/02-twin-model.md:10-16)

| Tier | Tag | Role and lifecycle |
|---|---|---|
| Lab | `o2` | Standing sensors and telemetry |
| Twin | `te` | The target environment. Its destroy cascades exr → tfc → tap → te. |
| Traffic | `tfc` | Personas on the twin's hosts. Own deploy tier. |
| Tap | `tap` | Mirror plus gretap bindings to sensors. Stored as a `te`-tier deploy with a `tap-` plan_id. |
| Exercise | `exr` | Ephemeral attack/measure run |

Resource names carry the tier tag, and teardown guards key off it.

### Terms
- **Imprint:** aximprint's belief-store model of a real network, observed from captures.
- **PIT:** point-in-time. The id `pit-<hash>-<epoch>` is aximprint's snapshot id (twin.yaml description; doc 13 §6).
- **Cast:** aximprint compiling an imprint into a deployable bundle:
  - a `twin_config` YAML (segments, nodes with per-node `observation` occupancy, params such as `DEPLOY_MIN_OCCUPANCY`);
  - `<id>.topology.json` (the observed segments, nodes and edges export);
  - `<id>.provenance.json` (evidence sidecar);
  - optional `generated/tap.emitted.yaml`, `personas/cast-*.yaml` and `scripts/serve-*.sh`.
- **Golden twin:** a cast twin that has been deployed and **certified** by `te scorecard`, then tagged golden vN (doc 13 §6, lines 286-293: imprint → cast → deploy → certify → tag; drift → re-imprint → `te deploy --update/--reapply`).

### Directories
- `twins/<id>/` is the current consolidated layout. It contains `twin.yaml` (id, cluster, containment, per-tier default and variants), `te.yaml` (the twin_config), `tap/`, `tfc/`, `personas/`, `scripts/` and `generated/`.
- `twin-configs-cast/pit-395a61f04e-1780405200/` is a legacy cast-bundle layout kept as a **compatibility shim**:
  - `pit-….yaml` is a symlink to `twins/pit-…/te.yaml`;
  - the `.topology.json` and `.provenance.json` files are symlinks into `twins/pit-…/generated/`;
  - `layout.json` (untracked) is the Console's pin document `{"schema":"axr-layout/1","imprint":…,"pins":{}}`. It is written by `PUT /api/imprints/{id}/layout` and read only to serve back (service.py:3427-3453). It has no deploy meaning.
- `target-environments/` holds hand-authored Scenario IR archetypes (ad-domain-lab, ctf-range, …).
- `scenarios/` is a leftover from the rename to `target-environments/` (commit b70972b8). It contains only an untracked `fabric-verify/state.db`.

### How a cast twin maps to a deploy record
- Deploying with `--config <id>`, `POST /api/deploys {config|imprint}` or `/api/imprints/{id}/deploy` materializes the twin_config into IR. It is deployed as a `te` row with `scenario_id = twin_config.id` (e.g. `corp-twin`). The deploy_id is `<scenario_id>-<8hex>`, and `plan_id`/`ir_hash` fix the generation.
- The row does **not** record which bundle directory, params, variant, gate threshold or source file produced it.
- tap, tfc and exr link to the te row only by the plan_id naming convention.

### Where topology lives and whether it can be rebuilt
- Observed topology is `<id>.topology.json`, served by `/topology-export` and `/imprints/{id}/topology`.
- Deployed topology is fully reconstructable **from state alone**. `planner.inventory.build_twin_inventory` (inventory.py:34-36: "No adapter/cluster call") derives it from recorded ops:
  - hosts from `clone-vm`;
  - every NIC leg from `attach-vm-to-vnet`;
  - `net_bridge`, and placement from `placement_json`;
  - role from `clone-vm.node_role`, overridden by tfc `assign-role`;
  - OS from the image manifest when `images_root` is set.
- `/api/scenarios/{id}/topology` instead re-parses the **current** catalog IR. That can drift from what was deployed, since only `ir_hash` is stored, not the IR.
- The Grafana plugin's `DeployedTopology`/`TwinTopologyScreen` already combine `/inventory` with `/topology-export`.

---

## 4. Personas and tfc

### Persona definition
- Schema: `axrange/personas/schema.py`. `Persona{id, os_family, drive, emits[], requires[], knobs}` (:187-195). `Emit{proto, ports, peer_role, direction, cadence, volume, optional, transport, bytes_per_msg, peer_scope}` (:142-179).
- Catalog: 41 files in `axrange/personas/catalog/*.yaml`, plus `AXRANGE_PERSONAS_DIR` extra directories where later ones win (registry.py:70-82).
- The registry is **process-global**, loaded at import (`PERSONAS`, registry.py:87).
- Twin-local personas (`twins/<id>/personas/cast-*.yaml`) are added only by the CLI's `_resolve_twin_tier_spec`. It prepends them to the env var and calls `reload_personas()` (cli/main.py:4520-4529).
- **The API never does this** (GAP-105). `axrange serve` can only drive twin-local personas if `AXRANGE_PERSONAS_DIR` was set before startup, and ids from different twins can collide in the one global dict.

### Binding personas to nodes
A traffic profile has `target`, `roles[]` (node or selector → role), `assignments[]` (exactly one of role/node, plus persona, intensity, schedule and seed) and `replay[]` (traffic.py:141-203, parse at :269).

`resolve_traffic_bindings` (:1058-1308) works as follows:
1. Builds the twin inventory from state.
2. Applies `roles:` first.
3. Resolves each assignment to hosts.
4. Refuses the same persona twice on one host. Different personas can stack on one host; `replay` takes a whole host.
5. Resolves peers round-robin by role, and resolves `peer_sets` for `peer_scope: all`.
6. Checks `requires[]`.
7. Checks persona OS against host OS: `persona_os_mismatch` (:932-947). Host OS comes from the manifest-derived inventory OS, else from `wait-os-ready` recording `windows` (:950-965). All mismatches are collected and raised as one 400 (:1203-1235).

`instance_id` is `f"{host_id}-{persona_id}"` (:1188).

### What `axrange tfc <verb>` does
- **validate** (cli :3652): static parse, then a static OS check against the twin's `te.yaml` if `--images` is given (:3609-3649, `check_persona_os_static` traffic.py:1009), then a live resolve against state.db if reachable.
- **plan** (:3744): resolve, then `compose_traffic_ops` (traffic.py:1350-1394). One `assign-role` op per role entry, scope `{host}`. One `drive-persona` op per instance, scope `{instance}`. Inputs are persona_id, drive, os_family, instance_id, host_id, vmid, peers, seed, schedule and `peer_sets`.
- **deploy** (:3795), which calls `apply_traffic_profile` (traffic.py:1508-1695):
  1. `_containment_gate` does a live read of the router's egress phase over QGA and refuses unless it reads `measure`, overridable with `--allow-uncontained` (:3225-3310). **CLI only.**
  2. `--images` manifests are loaded for the OS check.
  3. Twin personas are loaded (CLI only).
  4. The target resolves to a concrete te deploy id.
  5. `plan_id = tfc-<te>`. The existing deploy is found by plan_id, and the lease is keyed on plan_id with a Heartbeat.
  6. `force_reset_ops` runs on **every** op in the new plan (:1650).
  7. `run_deploy_executor(store, backend, backend, …)` runs with `PersonaBackend` in both slots (:1660).
  8. On success, `_stop_dropped_instances` stops every previously-completed instance **not in the new plan** and marks its row destroyed (:1673-1676, :1790-1831). `_retire_dropped_roles` does the same for role rows (:1834-1863).
  9. Lease and locks are released.
- **status / info / destroy**: read state, or stop every persisted instance and then run `planner.destroy_deploy` (:1866-1979).

### How persona config reaches the guest
- `PersonaBackend._drive_persona` (traffic.py:1460-1502):
  1. Looks the persona up by id **from the process registry at run time** (:1475). Only `persona_id` is persisted, not the persona body.
  2. Builds a `GuestAgent` over the Proxmox REST QGA endpoint on `ctx.cluster.default_node` (:1478).
  3. Calls `driver.start(ga, vmid, persona, instance_id, peers, seed, schedule, peer_sets, credentials)`.
- Drivers (`personas/drivers/*.py`) render a client script, write it over QGA and launch it:
  - Linux: a transient systemd unit `axr-persona-<instance>` (base.py:88-93, `systemd_daemon_start_block`).
  - Windows: a Scheduled Task.
- `start()` is convergent: it stops, then relaunches.
- It uses neither Ansible nor cloud-init.
- Credentials come from `adapter.conf DEFAULT_PASSWORD` and are passed at runtime, never persisted.

### Smallest unit of change today
- **tfc:** the whole profile. Every apply force-resets and re-drives all N instances (84 on corp-twin) and de-scopes anything omitted. There is no per-node or per-assignment verb, no `--only`, and no desired-vs-actual diff: every row is simply force-reset.
  - `POST /api/tfc` with only one assignment would **stop the other 83 personas.**
  - The source profile is not recorded anywhere: tfc `ir_hash` is `""` (traffic.py:1641).
  - Re-applying `twins/<id>/tfc/default.yaml` would revert any per-node edit made out of band.
- **te:** `--update` runs `plandiff.diff_plan` against the standing op rows (service.py:291-474).
  - Added ops execute. Removed ops are swept with `--allow-destroy`.
  - Any Mutated op is refused 409, except `--reapply`, which re-runs a `run-script` whose only change is `script_content` (:410-422).
  - The PLANNER §9.2 redo rules are unimplemented.
  - Doc 15 §11 (line 571): the canvas is "not an editor of the deployed twin".
- **Engine primitive that enables a partial run:** `Executor.run` skips every op already `completed` (executor.py:297-307). `StateStore.force_reset_ops(deploy_id, ids)` (state.py:743-776) flips chosen ops back to pending, and `insert_ops` is `INSERT OR IGNORE` (:804-836). So "re-run exactly these ops, skip everything else" already works mechanically, as long as the plan handed to the executor contains the full op set or just the subset.

### Where a "re-apply persona on one node" operation could go
1. **New engine function in `o2control/traffic.py`**, e.g. `apply_host_personas(te_target, host_id, assignments[], …)`:
   - Take the same lease, `plan_id = tfc-<te>` (traffic.py:1625), so it serializes against a full apply.
   - Rebuild the current desired set from the standing tfc deploy's COMPLETED `drive-persona` and `assign-role` rows. `traffic_info_from_deploy` (:2126) already reads them.
   - Replace only that host's entries.
   - Resolve through `resolve_traffic_bindings` (:1058) so the OS, requires and duplicate-instance checks run unchanged.
   - Compose with `compose_traffic_ops` (:1350).
   - `force_reset_ops` **only that host's instance op_ids**. This is the one-line difference from :1650.
   - Run the executor. Other rows are completed and get skipped. `vm:$vmid` locks already exist (ops/registry.py:1072).
   - Call `_stop_dropped_instances` restricted to that host's instances. Today it takes the global live set (:1790-1831), so it needs a host filter.
2. **API:** a `POST /api/tfc/hosts/{host_id}` (or similar) wrapper in service.py next to `tfc_apply` (:2572), with a `GET` peer exposing `traffic_info_from_deploy` so the UI can show per-host instances, vmid and peers.

### What is missing to make it safe
- **Persona content is not persisted.** Only the id is stored, and it is resolved from the process-global registry at run time (:1475). The API also lacks twin-local personas (GAP-105).
  - Either persist a persona snapshot or hash in the op inputs, or do a per-request overlay keyed on the twin.
  - Otherwise a UI re-apply may drive a different persona body than the CLI did, or a fallback persona with no emits.
- **Parity gaps in the API tfc path.**
  - Containment gate: missing. It is CLI-only (cli :3798).
  - OS check: weakened. `service.tfc_apply` passes no manifests (service.py:2600-2605), so a Windows persona on a Linux host is not refused, and Linux-on-Windows is caught only via the recorded `wait-os-ready`.
  - A per-node endpoint must thread `settings.images_root` manifests and a containment check.
- **Stale peers after a role change.** Changing a host's role means other hosts' `peers` maps (resolved at their own apply) point at stale role → IP picks. A persona swap alone does not change roles, but a role edit requires re-resolving and re-asserting the dependents (inferred from :1127-1129 and :1211-1217).
- **No desired-state record or diff.** There is no stored profile or hash for the tfc deploy. Decide whether a UI edit writes back to `twins/<id>/tfc/*.yaml`, or to a persisted overlay that the next full `tfc deploy` honors. Otherwise the next full apply silently reverts it.
- **Synchronous HTTP.** A per-host apply is small, but full applies need a job or async model like te deploy.
- **Liveness.** `driver.heartbeat()` exists (drivers/base.py:450-462 and per driver) but nothing calls it, so there is no post-apply liveness confirmation beyond `started`.

---

## 5. Health and status data

| Data | Source | Cost |
|---|---|---|
| Deploy and op status, attempts, errors | `/api/deploys[/{id}]`, state.db | Cheap. N+1 attempts queries per op. |
| Host roster, nets, placement node, role, OS, listen, egress posture (recorded) | `/inventory`, state.db plus local manifests | Cheap |
| `vm_status` | The recorded `clone-vm` **op** status (inventory.py:106, 236), not the VM power state | Cheap, not live |
| Node overlay on topology | Worst op status per node (service.py:999-1032) | Cheap |
| Persona assignments | `/api/tfc`: COMPLETED `drive-persona` rows (traffic.py:2024-2079) | Cheap. Needs the te deploy id; a twin name does not resolve (:2062). A bare call picks the latest tfc even if destroyed (:2014-2021). |
| Tap bindings | `/api/o2/taps`, state.db. `capture_verified` just means "not a registry sensor with an empty deploy_id" (taps.py:3383), not a live capture check. | Cheap. Verbose mode adds live external-sensor resolution. |
| Access IPs | `/access`. Live `agent/network-get-interfaces`, then VM config, only for hosts lacking a recorded IP (resolve.py:60-94), 5 s timeout each (:34) | Moderate |
| External sensor name and IP | `/api/o2/info`, verbose taps. `/cluster/resources` plus QGA. | Live, moderate |
| Router egress phase (contained/measure) | CLI `te egress` and `_containment_gate` via `o2control.containment.egress_live` (QGA, 60 s budget) | Live, expensive, **no API** |
| Fidelity rows | `/scorecard?probe=true`: in-guest writes and `ss` | Expensive |
| `/api/health` | state.db open only | Cheap, no cluster check |

- Live primitives that exist but have no endpoint: `rest.vm_status` (rest.py:332), `resolve_vm_node` (resolve.py:121), and `/cluster/resources` (resolve.py:141). The persona `heartbeat`, `heartbeat_unit` and `heartbeat_scheduled_task` are in drivers/base.py:220 and :385.
- The node-detail live tier is a stub (service.py:1293-1297).
- The sensor's `--healthcheck` (o2/tcpdump-sensor) is not surfaced.

---

## 6. Gaps against needs (a)–(e)

### Data exists but no endpoint
- **(a)** Deploy `tier` and `plan_id` are in the table but not in `DeploySummaryDTO` (dtos.py:141). Sibling tap/tfc/exr → te linkage (`cascade.siblings_for_te`) has no "twin with its tiers" read.
- **(c)** `tfc info` detail (per-instance host, persona, vmid, peers: traffic.py:2126). Resolving `GET /api/tfc?target=<twin-name>` to a deploy id (needs `resolve_traffic_target`). A per-host persona list; you have to invert persona → hosts client-side.
- **(e)**
  - Live VM power and node (`rest.vm_status`, `resolve_vm_node`).
  - Persona unit liveness (`driver.heartbeat`).
  - Router containment and egress live read (`egress_live`).
  - Latest attempt error per op per host is in `/api/deploys/{id}`, but there is no host-grouped view.
- **Persona catalog:** `GET /api/personas` for picking a persona in the UI (registry `all_ids`/`get`). Not exposed.

### Endpoint exists but no UI uses it
Determined by grepping `o2/console/src` and the Grafana plugin source.
- The Console SPA (`o2/console`) calls **only** `/api/imprints/*` (client.ts:214-254).
- The Grafana plugin uses deploys, inventory, topology, topology-export, nodes, imprints plan/deploy/cast, targets, validate/plan, health, pcaps and twin-configs.
- **No UI calls:**
  - `/api/tfc` (GET/POST/DELETE)
  - `/api/o2/taps`
  - `/api/o2/surface`
  - `/api/o2/info`
  - `/api/exr`
  - `/api/deploys/{id}/access`
  - `/api/deploys/{id}/scorecard`

### Engine capability missing
- **(a)**
  - A cluster identity on deploy rows (the cluster or API URL at deploy time).
  - Multi-target config: one process, several adapter configs. `/api/targets` returns one (service.py:1601).
  - Reconciliation of state against the live cluster.
  - Consuming `twin.yaml cluster:`.
  - Recording the source bundle, params and variant on the deploy.
- **(d)**
  - Per-node or partial tfc apply with scoped de-scope (section 4).
  - A persisted tfc desired state or profile hash.
  - A persisted persona snapshot, or a per-twin persona overlay in the API (GAP-105).
  - Containment gate and manifest OS check in the API path.
  - Peer re-resolution for dependents when roles change.
  - An async job model for tier applies.
- **(e)**
  - Node-detail live tier (QGA/Proxmox), designed in doc 11 but a stub.
  - Persona liveness aggregation.
  - Live capture verification.
  - A health endpoint covering the cluster and QGA.
  - Push updates (SSE/websocket) or a cheap change feed; today it is poll only.

---

## Where code and docs disagree

1. Doc 11 says taps have "POST/DELETE … (no GET)" (11-api-ui.md:36). Code has `GET /api/o2/taps` with concise and verbose modes (app.py:570-598).
2. Doc 11 calls `/api/targets` a "multi-cluster target picker" (:39). Code returns at most one target (service.py:1601-1618).
3. Doc 11 says inventory returns "host roster and tap points" (:32, :115). `tap_points[]` was removed (inventory.py:16-23).
4. Doc 11 says the UI is "entirely inside Grafana" (:138-175). There is also the standalone Vite Console (`o2/console`, doc 15), co-served via `--spa-dir`, which covers only imprints and is not deployed-twin aware (doc 15 §11 line 571).
5. Doc 11 (:21, :41) describes a GET/POST/DELETE trio per tier and presents only deploy as async. Tier applies and all destroys are synchronous, so "poll `/api/deploys/{id}`" applies to te deploy only.
6. Doc 11's endpoint table omits `/api/deploys/{id}/scorecard` and the `/api/imprints/*` Console routes (those are in doc 15 §10).
7. Doc 07 (:101-104, "uniform start/stop/heartbeat") implies liveness. `heartbeat` is never called by the tfc tier, the API or the CLI.
8. Doc 02's lifecycle verbs (:95) say bare status defaults to the latest deploy. The API node detail and topology `latest` pick the newest deploy of any tier (service.py:1010, :1252), and bare `GET /api/tfc` does not exclude destroyed rows.
9. `twin.yaml cluster:` is documented as "the adapter.conf target this twin is meant for" (manifest.py:101, twin.yaml comments), but nothing in `axrange/` reads it.
10. Doc 11 (:55-63) says the node-detail live tier is "designed but not yet wired". That is accurate, and still true.

Key files:
- /home/rocky/axRange/axrange/api/app.py
- /home/rocky/axRange/axrange/api/service.py
- /home/rocky/axRange/axrange/api/dtos.py
- /home/rocky/axRange/axrange/api/settings.py
- /home/rocky/axRange/axrange/api/serve.py
- /home/rocky/axRange/axrange/planner/state.py
- /home/rocky/axRange/axrange/planner/executor.py
- /home/rocky/axRange/axrange/planner/inventory.py
- /home/rocky/axRange/axrange/o2control/traffic.py
- /home/rocky/axRange/axrange/o2control/taps.py
- /home/rocky/axRange/axrange/personas/registry.py
- /home/rocky/axRange/axrange/personas/schema.py
- /home/rocky/axRange/axrange/personas/drivers/base.py
- /home/rocky/axRange/axrange/adapter_proxmox/config.py
- /home/rocky/axRange/axrange/adapter_proxmox/resolve.py
- /home/rocky/axRange/axrange/ops/registry.py
- /home/rocky/axRange/axrange/cli/main.py
- /home/rocky/axRange/axrange/twinconfig/manifest.py
- /home/rocky/axRange/twins/corp-twin/{twin.yaml,tfc/default.yaml,personas/}
- /home/rocky/axRange/twin-configs-cast/pit-395a61f04e-1780405200/
- /home/rocky/axRange/docs/design/{02,07,11,13,15}-*.md
- /home/rocky/axRange/docs/PRODUCT_GAPS.md (GAP-011, GAP-084, GAP-105)
