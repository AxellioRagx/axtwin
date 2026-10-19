# Unified Console — API contract (as implemented)

*Status: matches the code on `wip/unified-console` after fix round 4 (live write-path run, 2026-09-30). Implements `00-design-ruling.md`
§3.1–§3.3. DTOs: `axrange/api/dtos.py` (section "Unified Console run-time reads"); routes:
`axrange/api/app.py`; glue: `axrange/api/service.py` (end of file); engine:
`axrange/o2control/traffic.py` (section "Per-host persona apply"); targets:
`axrange/api/targets.py`. Tests: `tests/test_unified_console.py`,
`tests/test_unified_console_fixes.py`, `tests/test_unified_console_fixes2.py`,
`tests/test_unified_console_fixes3.py`, `tests/test_unified_console_fixes4.py`.*

Conventions (unchanged from today):
- Errors: `{"error": "<message>", "detail": <any|null>}` with the HTTP status.
- Reads are open. Writes need `Authorization: Bearer <token>` when the server has a token (401 otherwise).
- Timestamps are unix epoch **seconds** (float), as every existing deploy/op field already is.
- **Recorded vs live:** every value read from the cluster sits in an object that carries
  `checked_at`. Anything without one is recorded (state store).
- **Unknown is never zero/false:** a live value that could not be read is
  `{"state": "unknown", "reason": "<why>", "checked_at": <float|null>}`.

Shared live-value shape (`LiveValueDTO`):
```json
{"state": "reachable", "checked_at": 1790700000.1, "reason": null, "detail": null}
```

## Fix round 4 (live write-path run) — changes at a glance

- **Host-edit job history is kept.** `GET /api/deploys/{tfc_deploy_id}` `host_edits[]` lists EVERY
  per-host apply job, newest first, even after the rows it touched are re-driven by later edits or a
  whole-profile apply (each row carries an append-only `host_edit_history`; records are restored after
  every executor run).
- **Superseded jobs are marked, not omitted.** A successful whole-profile apply marks every earlier
  job record `"superseded": true, "superseded_at": <epoch s of that apply>`. `/hosts` `host_edit` still
  shows the host's newest job, carrying those two fields when a whole-profile apply has since taken
  over (the personas then read `origin: "profile"`). A job with no later whole-profile apply has no
  `superseded` key.

## Fix round 3 (review of the round-2 commit) — changes at a glance

- **Read-only opens no longer use `immutable`** (it read torn pages under a committing writer,
  LES-359). A read may leave empty `-wal`/`-shm` files beside a WAL-mode store; it never writes data.
- **`POST /api/deploys`, `POST /api/imprints/{id}/deploy`** search every store of the target for a
  non-destroyed deploy of the same scenario or plan and run THERE (resume, `update`, lease and
  protection included) — never a second row in the primary. The same scenario standing in two stores
  → `409 ambiguous-store`. `POST /api/deploys/{id}/run` takes `?target=` and finds the deploy in any
  store (it only reads; it creates nothing).
- **Startup:** every store is opened read-only at startup; an unreadable store, or an extra store that
  does not exist, fails `axrange serve` by name. Implicit-target extra paths are resolved before
  comparing (`./state.db` is the primary, not a second store).
- **At request time a store that became unreadable is skipped with a warning:** `GET /api/deploys`
  gains `warnings: [str]`; `/api/range` shows it `ok: false`.
- **`unrecorded`:** a VM also counts as recorded when its vmid is the deterministic clone vmid of any
  `clone-vm` op of a non-destroyed deploy (any op status); `EXTERNAL_SENSORS` vmids are never listed;
  `state` is `known | partial | unknown` — `partial` (with a reason) when a store could not be read;
  a non-empty list's `reason` is "no attached store records these VMs".
- **`params` values must be strings** on `/api/plan` and `/api/validate` (400 otherwise, as on deploy).
- A 0-byte or schema-less store file reads as an empty store.
- Twin `params` are read from `twin_config:`'s own top-level `params:` block only. A count param
  defaulting to `0` means zero hosts, in the imprint summary and the cast summary alike.
- Store labels use the fewest trailing path components that make each one unique
  (`state.db`, then `x/state.db`, …).

## Fix round 2 (live verification Part A) — new/changed fields at a glance

- **Several stores per target.** A target is one cluster with one or more state stores. Targets
  file: `db:` (primary — new deploys are written there) plus optional `extra_dbs: [path, …]`;
  implicit target: `AXRANGE_EXTRA_DBS=<path>:<path>` or `axrange serve --extra-db PATH` (repeatable).
  - Every `DeploySummaryDTO` gains `store` (a short label, e.g. `"state.db"`,
    `"axr-svcengines.state.db"`). `/api/range` twins carry `store`; `/hosts` carries `store`.
  - `GET /api/targets` items gain `stores: [{"label", "path", "primary": bool}]`.
  - `/api/range` target items gain `stores: [{"label", "path", "primary", "ok", "reason"}]`
    (`store` stays = the primary's status).
  - Reads that take a deploy id or twin (`/api/deploys/{id}` and its sub-routes, `/hosts`, `/health`,
    `/tfc/profile`, the persona plan, `GET /api/tfc`, `GET /api/o2/taps`) find it in whichever store
    holds it; `GET /api/deploys` merges every store (newest first). Writes against an existing
    deploy go to the store that holds it. A deploy id present in two stores of one target →
    `409 {"error": "deploy X is recorded in two stores of target T: a, b", "detail": {"code": "ambiguous-store", "stores": ["a", "b"]}}`.
  - Duplicate resolved Proxmox API URLs are still refused ACROSS targets; list one cluster once,
    with `extra_dbs`.
- **Reads never write.** Read routes open stores read-only (no DDL, no `user_version` stamp, never a
  created store); no wire change. (Round 3: `-wal`/`-shm` may appear beside a WAL-mode store.)
- **`POST /api/plan` and `POST /api/validate`** accept exactly one of `scenario` / `config` /
  `imprint`, plus `params: {NAME: "value"}` and `target_node`. `PlanSummaryDTO` gains
  `vm_count: int` and `params: {NAME: <resolved value>}` (`{}` for a plain scenario).
- **Twin parameters.** `GET /api/twin-configs/{id}` `params[]` items are
  `{"name", "default", "description": str|null, "declared": bool}` (declared in `params:` vs an
  inline `${NAME:default}` only). List items gain `parameterized: bool` and `param_names: [str]`.
  `/api/imprints` no longer skips a bundle whose `count:` is a `${NAME:default}` token (defaults are
  resolved for display); `skipped[].reason` remains for bundles that truly cannot be read.
- **Unrecorded VMs.** `/api/range` target items gain `unrecorded`:
  `{"state": "known|unknown", "checked_at", "reason", "count", "vms": [{"vmid", "name", "status", "node"}]}`
  (at most 50 `vms`) — axRange-named VMs (`te-…`, `o2-…`, `tfc-…`, `exr-…`, not templates) on the
  cluster that no non-destroyed deploy in any of the target's stores records. From the cached
  sweep; `null` with `?live=false`.
- **Cached `checked_at`.** A cached health answer repeats its original `checked_at` everywhere,
  including the top-level one.

## Fix round 1 (code review `06-code-review.md`) — new/changed fields at a glance

Details are folded into each section below; this list is the delta.
- **Plan fingerprint.** `POST …/personas/plan` returns `fingerprint` (string). The PUT body takes
  `expect_plan` (that fingerprint). When the plan the server computes UNDER THE LEASE differs, the PUT
  answers `409 {"error": "the plan changed since it was previewed …", "detail": {"code": "plan-changed", "plan": <fresh plan>}}`.
  The PUT response's `plan` (with its `fingerprint`) is the plan actually executed.
- **Persisted job record.** Every host edit writes a job record into the state store. `/hosts` exposes it
  per host as `host_edit` and `GET /api/deploys/{tfc_deploy_id}` exposes all of them as `host_edits[]`:
  `{"job_id", "host_id", "status": "running|completed|failed", "started_at", "finished_at",
  "fingerprint", "plan": <executed plan>, "diagnostics": [{"level": "error|warning|info", "message"}]}`.
- **Stops that fail stay visible.** A persona whose stop raised, or whose post-stop heartbeat does not
  say `dead`, is NOT retired: its row stays `completed` and gets `stop_error`
  (`{"message", "at", "heartbeat"}`), surfaced as `personas[].stop_error` on `/hosts`.
- **Protected deploys.** `/api/range` twins and `/hosts` carry `protected: bool`. Every write route
  aimed at a protected twin or any of its tap/tfc/exr siblings (`DELETE /api/deploys/{id}`,
  `POST /api/deploys` resuming/updating it, `PUT …/personas`, `POST/DELETE /api/tfc`,
  `POST/DELETE /api/o2/taps`, `POST/DELETE /api/exr`) is refused
  `423 {"error": "deploy corp-twin-07cfcdb2 is protected …", "detail": {"code": "protected", "deploy_id": "corp-twin-07cfcdb2"}}`
  unless the request carries the query parameter `override_protection=<that te deploy id>`.
- **Role orphaning.** A role change that leaves a role other running personas hard-need with no holder
  is refused (`refusals[].code = "last-holder"`) unless the body sets `allow_role_orphaning: true`;
  every `dependents[]` entry carries `no_new_holder: bool` (and each affected `stale_peers[role]`
  carries `"no_new_holder": true`).
- **Instance collision.** Refusal code `instance-collision` (host `a` + persona `b-c` vs host `a-b` +
  persona `c` both mint instance `a-b-c`).
- **Imprints.** `GET /api/imprints` items and `GET /api/imprints/{id}` gain `can_apply: bool` and
  `apply_blocked_reason: str|null`.
- **Catalog deploy pin (GAP-098).** `POST /api/deploys` and `POST /api/imprints/{id}/deploy` accept
  `expect_plan_id` (the `plan_id` from the matching `/plan`); a mismatch is
  `409 {"detail": {"code": "plan-changed", "plan_id": "<actual>"}}`. `CreateDeployResponse` gains `plan_id`.
- **Looser DTO fields.** `personas[].peers` values and `taps[].nic_index` may be `null`.
- `personas[].seed` on `/hosts` is the recorded seed (send it back unchanged to keep a persona as is).
- Health reads are cached (`/health?host=` ~10 s per target/deploy/host, one at a time; the cluster
  sweep and `/version` ~5 s per target); a cached answer keeps its original `checked_at`.

## Cluster targets — `?target=<name>`

These routes accept `?target=<target name>` (default: the default target):
`GET /api/deploys`, `GET /api/deploys/{id}`, `/inventory`, `/access`, `/scorecard`, `/hosts`,
`/health`, `/tfc/profile`, `POST .../personas/plan`, `PUT .../personas`,
`GET /api/scenarios/{id}/topology`, `GET /api/scenarios/{id}/nodes/{node}`,
`POST /api/deploys` (which target to deploy to), `DELETE /api/deploys/{id}`.

An unknown name → `404 {"error": "unknown target 'gamma'", "detail": {"targets": ["alpha", "beta"]}}`.

NOT on `/api/tfc`, `/api/o2/taps`, `/api/exr`, `/api/range`, `/api/targets`, `/api/personas`,
imprints, validate/plan: on the tier routes `?target=` already means *the twin* (a te deploy id or
a twin name) and keeps that meaning; they always use the default target.

### Targets file (`axrange serve --targets <file>` or `AXRANGE_TARGETS=<file>`)

```yaml
targets:
  - name: dev45                       # required, unique, no '/'
    adapter_config: /etc/axrange/adapter.conf   # optional (default search if absent)
    db: ~/.axrange/state.db           # required; each target needs its OWN db (shared db = refused)
    nodes: [dev45]                    # optional; PVE node names for the placement cross-check
    protected_deploys: [corp-twin-07cfcdb2]   # optional; API writes refused 423 without override
    extra_dbs: [/home/rocky/axr-svcengines.state.db]  # optional; more stores, SAME cluster
  - name: pxtwin1
    adapter_config: /etc/axrange/adapter-pxtwin1.conf
    db: ~/.axrange/state-pxtwin1.db
```
The first entry is the default target; its db + adapter.conf also become the process's own, so
routes without `?target=` read it. A bad file fails `axrange serve` at startup (exit 2), including:
`adapter_config` missing on any entry of a file with more than one target; any of
`PROXMOX_API_URL`, `PROXMOX_API_TOKEN`, `DEFAULT_NODE`, `OVS_SSH_HOST`, `OVS_SSH_USER`,
`OVS_SSH_KEY` set in the serve environment (env overrides every target's file); two targets whose
adapter.conf resolves to the same Proxmox API URL; two targets on one db (paths compared after
`expanduser().resolve()`); an unloadable adapter.conf. Target facts are read once at startup.
Protected deploys for the implicit single target: `AXRANGE_PROTECTED_DEPLOYS=<id>[,<id>…]`
(`ApiSettings.protected_deploys`).

**Stores.** A target is one cluster identity with one or more state stores: `db` is the primary
(new deploys — `POST /api/deploys` — are written there) and `extra_dbs` are further stores of the
same cluster (implicit target: `AXRANGE_EXTRA_DBS=<path>:<path>` or `axrange serve --extra-db PATH`,
repeatable). Every store belongs to exactly one target, once (refused at startup otherwise).
Reads search every store; a request about an existing deploy (or twin) runs in the store that
holds it — writes, the `tfc-<te>` lease and sibling lookups included. A deploy id (or twin name)
found in two stores of one target is `409 {"detail": {"code": "ambiguous-store", "stores": [...]}}`.
Store labels are the fewest trailing path components that make each store's label unique within
its target (usually the file name). A missing store is reported (`/api/range` `stores[].ok = false`), never created.

**Reads never write.** Every read route (all GETs, `POST /api/plan`, `POST /api/validate`,
`POST /api/imprints/{id}/plan`, `POST …/personas/plan`, `POST /api/deploys/{id}/run`) opens stores
read-only (`mode=ro` + `query_only`, never `immutable`): no schema DDL, no `user_version` stamp,
never a created file; SQLite may leave empty `-wal`/`-shm` files beside a WAL-mode store. A 0-byte or
schema-less file reads as an empty store. `GET /api/health` reports `state_db: false` when the store
file does not exist yet.

**Startup and broken stores.** `axrange serve` opens every store read-only at startup and refuses to
start, naming it, when a store cannot be read or an extra store does not exist (a missing PRIMARY is
fine: the first deploy creates it). A store that becomes unreadable later is skipped per request:
`GET /api/deploys` lists it in `warnings`, `/api/range` marks it `ok: false`, lookups ignore it.

**Deploys and stores.** `POST /api/deploys` / `POST /api/imprints/{id}/deploy` first look in every
store of the target for a non-destroyed deploy of the same scenario id or plan id; one found in a
non-primary store routes the whole request there (resume/idempotent no-op, `update`, lease,
protection). Found in two stores → `409 ambiguous-store`. Only a genuinely new scenario is written
to the primary. With no
file: one implicit target = `--adapter-config` + `--db`, named after that adapter.conf's
`DEFAULT_NODE` (`"default"` when it has none). Example file: `adapter.targets.example.yaml`
(repo root, beside `adapter.conf.example`).

## GET /api/targets  (changed: lists every target)

```json
{
  "targets": [
    {
      "node": "dev45",
      "api_url": "https://10.80.76.108:8006/api2/json",
      "verify_tls": false,
      "has_token": true,
      "has_ssh_key": true,
      "name": "dev45",
      "default": true,
      "nodes": ["dev45"],
      "db_path": "/home/rocky/.axrange/state.db",
      "error": null,
      "stores": [
        {"label": "state.db", "path": "/home/rocky/.axrange/state.db", "primary": true},
        {"label": "axr-svcengines.state.db", "path": "/home/rocky/axr-svcengines.state.db", "primary": false}
      ]
    }
  ]
}
```
`node`/`api_url`/`verify_tls`/`has_token`/`has_ssh_key` keep their old meaning (the Grafana plugin
reads them). `nodes` = the file's `nodes:` or `[DEFAULT_NODE]`. From a targets file, a target whose
adapter.conf fails to load is listed with `error` set and blank facts. The implicit single target
keeps the old behaviour exactly: an unloadable adapter.conf is a 400, an unconfigured one (no node,
no URL) returns `{"targets": []}`.

## GET /api/range  (new) — the home page in one call

Query: `live` (bool, default `true`) — `false` skips the reachability probe (`reachability` is then
`{"state": "unknown", "reason": "not checked (live=false)"}`).

```json
{
  "generated_at": 1790700000.12,
  "targets": [
    {
      "name": "dev45",
      "default": true,
      "node": "dev45",
      "api_url": "https://10.80.76.108:8006/api2/json",
      "reachability": {"state": "reachable", "checked_at": 1790700000.1, "reason": null,
                       "detail": "pve 9.2.0"},
      "store": {"ok": true, "reason": null},
      "twins": [
        {
          "deploy_id": "corp-twin-07cfcdb2",
          "twin_id": "corp-twin",
          "status": "completed",
          "plan_id": "corp-twin-7de6524d",
          "started_at": 1790500000.0,
          "updated_at": 1790510000.0,
          "completed_at": 1790510000.0,
          "cluster": "dev45",
          "host_count": 106,
          "persona_instance_count": 84,
          "siblings": {
            "exr": [],
            "tfc": [{"deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06", "scenario_id": "tfc-corp-twin-07cfcdb2",
                     "scenario_version": null, "status": "completed", "started_at": 1790500000.0,
                     "updated_at": 1790500100.0, "completed_at": 1790500100.0,
                     "tier": "tfc", "plan_id": "tfc-corp-twin-07cfcdb2", "target": "dev45"}],
            "tap": [{"deploy_id": "tap-corp-twin-07cfcdb2-8176c5ad", "tier": "tap", "...": "..."}]
          },
          "warnings": [],
          "protected": true,
          "store": "state.db"
        }
      ],
      "error": null,
      "stores": [
        {"label": "state.db", "path": "/home/rocky/.axrange/state.db", "primary": true, "ok": true, "reason": null},
        {"label": "axr-svcengines.state.db", "path": "/home/rocky/axr-svcengines.state.db", "primary": false, "ok": true, "reason": null}
      ],
      "warnings": [],
      "unrecorded": {"state": "known", "checked_at": 1790700000.1, "reason": null, "count": 1,
                     "vms": [{"vmid": 4990, "name": "te-cafe01-old-ws", "status": "running", "node": "dev45"}]}
    }
  ]
}
```
- `twins` come from EVERY store of the target, newest first; each twin (and each sibling
  `DeploySummaryDTO`) carries `store`.
- `stores[]`: per-store status (`ok: false` + `reason` when missing/unusable). `store` (singular) is
  the primary's status, as before.
- `warnings`: target-level, e.g. `"deploy X is recorded in two stores: a, b -- reads of it are refused"`.
- `unrecorded`: VMs on the cluster with an axRange name shape (`te-<6hex>-…`, `o2-…`,
  `tfc-<6hex>-…`, `exr-<6hex>-…`; templates excluded) whose vmid no non-destroyed deploy in any of
  the target's stores records. `count` is exact, `vms` holds at most 50 (by vmid). From the same
  cached `/cluster/resources` sweep as `/health` (~5 s). A vmid is "recorded" when any op output of a
  non-destroyed deploy names it OR it is the deterministic clone vmid (`vmid_for_op(plan_id, op_id)`)
  of any `clone-vm` op of such a deploy, whatever that op's status. `EXTERNAL_SENSORS` vmids are
  never listed. `state`: `known` (`reason` "no attached store records these VMs" when any are
  listed), `partial` (a store could not be read — `reason` names it; some listed VMs may belong to
  deploys recorded there), `unknown` (`{"reason": "cluster sweep failed: …", "count": null, "vms": []}`).
  `null` with `?live=false`. Read-only: nothing acts on them.
- `protected`: the twin is in this target's protected list (writes need `override_protection`).
- `reachability` is cached ~5 s per target (the cached value keeps its `checked_at`).
- `reachability.state`: `reachable` (one `GET /version`, 3 s timeout; `detail` = `"pve <version>"`),
  `unreachable` (with `reason`), or `unknown` (config unloadable / no API URL / not checked).
- `twins` = standing (non-destroyed) `te` deploys that are not tap playouts, newest first.
- `siblings` values are `DeploySummaryDTO`s (below), keys always `exr`, `tfc`, `tap`. Tap rows show
  `tier: "tap"` although the store records them as `te`.
- `host_count` = recorded inventory hosts (`null` + a `warnings` entry if it cannot be derived).
  `persona_instance_count` = completed `drive-persona` rows of the standing tfc sibling; `null` when
  the twin has no tfc deploy (never a fabricated 0).
- `warnings`: the cluster cross-check — placement names a PVE node the target does not list:
  `"placement names PVE node(s) nodeb but target 'beta' runs on nodex -- this deploy may belong to another cluster"`.
- `store.ok=false` (with `reason`, e.g. `"no state store at /path"`) when that target's db is
  missing or unusable; `twins` is then `[]`. A missing db is never created by a read.
- `error`: the target's adapter.conf load error, if any.

## DeploySummaryDTO (changed: + `tier`, `plan_id`, `target`)

Used by `GET /api/deploys`, `GET /api/deploys/{id}` (`.deploy`), and `/api/range` siblings.
```json
{"deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06", "scenario_id": "tfc-corp-twin-07cfcdb2",
 "scenario_version": null, "status": "completed", "started_at": 1790500000.0,
 "updated_at": 1790500100.0, "completed_at": 1790500100.0,
 "tier": "tfc", "plan_id": "tfc-corp-twin-07cfcdb2", "target": "dev45"}
```
`tier` is the display tier: `te | tap | tfc | exr | o2`. `target` is the target the row was read from;
`store` the label of the store that records it (`GET /api/deploys` merges every store of the target,
newest first).

## GET /api/deploys/{id}/hosts  (new) — the Twin area's one model

`{id}` is the te deploy id; a tap/tfc/exr sibling id resolves to its parent twin. Query: `target`.
Recorded only (state store); no cluster call. 404 unknown deploy; 400 when the id names no twin.
```json
{
  "deploy_id": "corp-twin-07cfcdb2",
  "twin_id": "corp-twin",
  "deploy_status": "completed",
  "target": "dev45",
  "store": "state.db",
  "tfc": {"deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06", "status": "completed"},
  "tap": {"deploy_id": "tap-corp-twin-07cfcdb2-8176c5ad", "status": "completed"},
  "protected": true,
  "hosts": [
    {
      "host_id": "ws-01", "vm_name": "te-corp-twin-ws-01", "vmid": 4101, "pve_node": "dev45",
      "vm_status": "completed", "role": "corp-workstation", "role_baseline": "corp-workstation",
      "kind": "host",
      "nets": [{"net_id": "corp-lan", "ip": "10.80.20.31", "nic_index": 0, "external": false}],
      "os": "linux",
      "listen": null,
      "role_origin": "baseline",
      "personas": [
        {"instance_id": "ws-01-corp-workstation", "persona": "corp-workstation",
         "os_family": "linux", "drive": "scripted", "intensity": null, "schedule": "24x7",
         "seed": null, "peers": {"domain-controller": "10.80.20.10"}, "peer_sets": {},
         "status": "completed", "op_id": "tfc-…-drive-persona-…", "started_at": 1790500010.0,
         "completed_at": 1790500020.0, "origin": "profile",
         "heartbeat": null, "last_error": null, "stop_error": null}
      ],
      "taps": [{"tap_deploy_id": "tap-corp-twin-07cfcdb2-8176c5ad", "net_id": "corp-lan",
                "nic_index": 0, "sensors": ["xpress-sw-a"], "directions": {"xpress-sw-a": "both"},
                "status": "completed"}],
      "last_error": null,
      "host_edit": null
    }
  ],
  "net_bridge": {"corp-lan": "vcorp01"},
  "vlan_underlay": null,
  "egress": null,
  "warnings": []
}
```
- All `HostInventoryDTO` fields (same as `/inventory`), plus `role_origin`, `personas`, `taps`, `last_error`.
- `personas[]` = this host's non-destroyed `drive-persona` rows, any op status
  (`pending|running|completed|failed|halted-due-to-upstream`). `intensity` is never recorded by the
  engine, so it is always `null`.
- `origin`: `"profile"` (whole-profile apply) or `"host-edit"` (per-host apply).
- `heartbeat`: the post-start heartbeat a per-host apply recorded:
  `{"alive": true|false|null, "state": "alive|dead|unknown", "checked_at": <float>, "reason": <str|null>, "unit": "axr-persona-…"}`;
  `null` when none was taken (whole-profile applies do not take one).
- `role_origin`: `baseline` (no tfc role) | `tfc` (a profile `roles:` entry) | `host-edit`.
- `last_error` (host and persona): the newest error of an op currently `failed`/`halted`, scoped to
  this host (te ops by scope, tfc ops by `host_id`):
  `{"deploy_id", "tier": "te|tfc", "op_id", "op_name", "error_class", "error_message", "at"}`.
- `taps[]`: completed `install-ovs-mirror` bindings of the tap sibling on this host
  (`nic_index` may be `null` for an odd recorded row).
- `personas[].peers` values may be `null`, `peer_sets` values are passed through as recorded.
- `personas[].seed`: the recorded seed. Send it back unchanged to keep a persona byte-identical.
- `personas[].stop_error`: set when a per-host apply tried to stop this instance and could not confirm
  it — the stop raised, or the post-stop heartbeat was not `dead`. The row then stays as it was:
  `{"message": "post-stop heartbeat reads alive", "at": <float>, "heartbeat": {…}|null}`.
- `host_edit`: the newest per-host apply job on this host (persisted, survives the request and later
  re-drives):
  `{"job_id", "host_id", "status": "running|completed|failed", "started_at", "finished_at",
  "fingerprint", "plan": <the executed plan>, "diagnostics": [{"level": "error|warning|info", "message"}]}`,
  plus `"superseded": true, "superseded_at": <epoch s>` once a later whole-profile apply has taken
  over the host (absent otherwise); `null` when the host was never edited. `status: failed` whenever any `error` diagnostic exists
  (an op failed, a stop was not confirmed, the job crashed or could not start).
- `protected`: see `/api/range`.
- `warnings`: e.g. tfc rows naming a host the inventory does not have.

## GET /api/deploys/{id}/health  (new) — live, on demand

Query: `target`; `host=<host_id>` (optional) adds that one host's QGA + persona liveness.
One `/cluster/resources` sweep (5 s timeout) for all VMs; never a whole-twin QGA fan-out.
404 unknown deploy / unknown host.
```json
{
  "deploy_id": "corp-twin-07cfcdb2",
  "target": "dev45",
  "checked_at": 1790700000.5,
  "cluster": {"state": "reachable", "checked_at": 1790700000.4, "reason": null, "detail": null},
  "hosts": [
    {"host_id": "ws-01", "vmid": 4101, "recorded_status": "completed",
     "power": {"state": "running", "checked_at": 1790700000.4, "reason": null, "detail": null,
               "node": "dev45"}}
  ],
  "host_detail": {
    "host_id": "ws-01",
    "qga": {"state": "reachable", "checked_at": 1790700001.0, "reason": null, "detail": null},
    "personas": [
      {"instance_id": "ws-01-corp-workstation", "persona": "corp-workstation",
       "state": "alive", "alive": true, "unit": "axr-persona-ws-01-corp-workstation",
       "checked_at": 1790700002.0, "reason": null}
    ]
  }
}
```
- `cluster.state`: `reachable` or `unknown` (sweep failed / config unloadable, with `reason`).
- `power.state`: `running | stopped | paused` (from the sweep), `absent` (sweep succeeded, vmid not on
  the cluster), `unknown` (sweep failed, or no recorded vmid). Never guessed.
- `recorded_status` is the recorded `clone-vm` op status (not power).
- Caching (S8): the sweep ~5 s per target; `?host=` ~10 s per (target, deploy, host), computed one at
  a time — a second concurrent `?host=` read waits up to 30 s, then `429 {"error": "another host health
  read is running; retry shortly"}`. Cached values keep their original `checked_at`.
- The top-level `checked_at` is the sweep's own time (the original one on a cache hit), never "now".
- `host_detail` is `null` without `?host=`. `qga.state`: `reachable | unreachable | unknown`
  (one agent ping, 5 s). Persona heartbeats (driver `heartbeat()` over QGA) run only when QGA is
  reachable; otherwise each persona is `unknown` with reason `"QGA unreachable: …"`.

## GET /api/personas?twin=<twin id>  (new)

Catalog personas plus that twin's local `twins/<id>/personas/*.yaml`, resolved per request (the
process-global registry is never mutated; twin A's personas never show for twin B).
```json
{
  "twin": "corp-twin",
  "personas": [
    {"id": "corp-workstation", "os_family": "linux", "drive": "scripted", "source": "catalog",
     "requires": ["domain-controller"],
     "summary": "tcp/445 -> file-server; udp/53 -> domain-controller (optional); tcp/443 -> egress",
     "emits": [{"proto": "smb", "ports": [445], "peer_role": "file-server", "transport": "tcp",
                "cadence": "business-hours", "volume": "low", "optional": false,
                "peer_scope": "one"}]}
  ],
  "warnings": []
}
```
- `source`: `catalog | twin`. A twin persona overrides a same-id catalog one; an id an
  `AXRANGE_PERSONAS_DIR` directory also defines keeps the env-var body (CLI precedence).
- `summary`: `"<transport>/<ports> -> <peer_role>"` per emit, `; `-joined, with ` (optional)` /
  ` (all)` suffixes; ports `*` when none.
- Without `twin` → catalog only (`twin: null`). An unknown twin (no `twins/<id>/twin.yaml` in the
  serve's twins dir) → 404.

## Per-host persona request body

Used by both the plan and the PUT:
```json
{
  "assignments": [{"persona": "corp-workstation", "schedule": "24x7", "seed": null, "intensity": null}],
  "role": null,
  "update_dependents": false,
  "allow_uncontained": false,
  "allow_role_orphaning": false,
  "target_node": null,
  "expect_plan": null
}
```
- `assignments`: the host's COMPLETE desired persona set. `[]` stops every persona on the host;
  **omitted / `null` keeps the host's running set** (use it for a role-only change).
  `schedule`: `24x7 | 24/7 | always | business-hours | business_hours | null` (normalized; anything
  else is a 400). `intensity` is accepted and not persisted (same as a profile).
- `role`: omitted/`null` = keep; `""` = drop the host's tfc role (back to the twin baseline);
  a string = set it.
- `update_dependents`: re-drive the other hosts whose peer picks a role change invalidates.
- `allow_uncontained` (PUT only): proceed past the containment gate with a warning.
- `allow_role_orphaning`: allow a role change that leaves a role other running personas hard-need
  (`requires[]` or a non-optional emit) with no holder; refused (`last-holder`) otherwise.
- `target_node` (PUT only): override the adapter.conf `DEFAULT_NODE` (same as `POST /api/tfc`).
- `expect_plan` (PUT only): the plan `fingerprint` the operator previewed. Omitted = no precondition.

## POST /api/deploys/{id}/hosts/{host}/personas/plan  (new, pure — no state writes, no auth)

Body optional (absent = `{}` = keep everything). Query: `target`. 404 unknown deploy/host.
```json
{
  "deploy_id": "corp-twin-07cfcdb2", "host_id": "ws-01", "target": "dev45",
  "tfc_deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06", "creates_tfc_deploy": false,
  "ok": true,
  "role": {"current": "corp-workstation", "baseline": "corp-workstation",
           "new": "corp-workstation", "changed": false},
  "start": [{"host_id": "ws-01", "instance_id": "ws-01-dev-workstation", "persona": "dev-workstation",
             "os_family": "linux", "reason": "new", "peers": {"domain-controller": "10.80.20.10"},
             "schedule": null}],
  "stop": [{"host_id": "ws-01", "instance_id": "ws-01-corp-workstation",
            "persona": "corp-workstation", "op_id": "…"}],
  "unchanged": {"hosts": 82, "instances": 83},
  "dependents": [{"host_id": "ws-07", "instance_id": "ws-07-corp-workstation",
                  "persona": "corp-workstation", "op_id": "…",
                  "stale_peers": {"file-server": {"recorded": "10.80.20.31", "new": "10.80.20.40"}},
                  "no_new_holder": false, "schedule": null, "seed": null}],
  "will_update_dependents": false,
  "refusals": [],
  "warnings": [],
  "fingerprint": "3f0c9a1e5b7d2c4a8e6f1b0d9c7a5e3f"
}
```
- `fingerprint`: 32 hex chars over the plan's decisions (deploy, host, tfc deploy, role, start, stop,
  dependents, will_update_dependents, refusals) AND the tfc deploy's recorded rows (op id, status,
  completed_at, inputs). A re-preview with nothing changed returns the same value; any apply in
  between changes it; heartbeats and health reads do not. Warnings are excluded.
- `start[]`: every instance that will be (re)driven. `reason`: `new` (no running row), `changed`
  (persona/schedule/seed/peers differ from the running row), `re-assert` (identical; starts are
  convergent), `dependent` (another host re-driven because `update_dependents`).
- `stop[]`: this host's non-destroyed instances (any status — a failed or pending one too) not in the
  new set. Other hosts are never stopped.
- `unchanged`: hosts/instances on OTHER hosts that nothing will touch (dependents that are not
  re-driven count here).
- `dependents[]`: only on a role change. `stale_peers[role]` is `{"recorded", "new"}` (single peer)
  or two sorted IP lists (`peer_scope: all`). `new` is the pick a re-drive would make; when no host
  holds the role after the change, `new` is `null` and the entry has `"no_new_holder": true`.
- `refusals[]`: `{"code", "message"}`; `ok` is `false` iff non-empty (still HTTP 200). Codes:
  `os-mismatch`, `unknown-persona`, `resolve` (duplicate / requires / peer errors),
  `replay-host` (the host carries a pcap replay binding; edit it through the full profile),
  `instance-collision` (the persona on this host would mint an instance id another host already
  uses — host `a` + persona `b-c` vs host `a-b` + persona `c`; also raised by a whole-profile apply),
  `last-holder` (see `allow_role_orphaning`).
  Containment is NOT evaluated by the plan (it is a live read that records itself); the PUT checks it.
- Peers for a re-driven instance are resolved node-by-node (round-robin index 0 — the first holder
  of the role other than the host itself), so a `re-assert` of a host that a whole-profile apply had
  fanned out to a later holder shows as `changed`. Visible in the preview; nothing hidden.

## PUT /api/deploys/{id}/hosts/{host}/personas  (new, write token, async)

Same body as the plan. Query: `target`, `override_protection`. Synchronously, before returning:
0. protection — the twin is protected and `override_protection` does not name it →
   `423 {"error": "deploy … is protected …", "detail": {"code": "protected", "deploy_id": "…"}}`;
1. the `tfc-<te>` lease → `409 {"detail": {"held_by_deploy_id": "…"}}` when a whole-profile apply or
   another host edit holds it (taken FIRST; everything below runs under it);
2. plan under the lease — `expect_plan` given and different →
   `409 {"error": "the plan for 'ws-01' changed since it was previewed …", "detail": {"code": "plan-changed", "plan": <fresh plan>}}`;
   any refusal → `400 {"error": "per-host persona apply on 'ws-01' refused: …", "detail": {"refusals": [...], "plan": <plan>}}`;
3. containment gate — only when the plan STARTS something (a stop-only edit is never gated), and only
   for twins declaring `egress:` (one live router read) →
   `409 {"detail": {"refusals": [{"code": "containment", "message": …}]}}` unless `allow_uncontained`;
4. create the tfc deploy if the twin has none; force-reset ONLY the ops about to run; write a
   `running` job record (see `/hosts` `host_edit`).

Then returns at once:
```json
{"deploy_id": "corp-twin-07cfcdb2", "host_id": "ws-01",
 "tfc_deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06", "status": "running", "detached": true,
 "plan": { "…": "the plan response above" },
 "warnings": []}
```
The response `plan` (and its `fingerprint`) is the plan actually executed.

Progress: poll `GET /api/deploys/{tfc_deploy_id}` — ops + attempts, deploy status `running` →
`completed | failed`, and `host_edits[]` (the persisted job records, newest first, each with the
executed `plan`, its `fingerprint`, `status` and `diagnostics`) — or `GET /api/deploys/{id}/hosts`
(this host's `host_edit`, persona rows, `heartbeat`, `stop_error`).

After a successful run: this host's dropped instances are stopped, and each is retired (row
`destroyed`, `outputs.descoped_by: "host-edit"`) ONLY when the stop did not raise and a post-stop
heartbeat reads `dead`; otherwise the row keeps its status, gets `stop_error`, and the job is
`failed`. `role: ""` retires its role row. One driver heartbeat per started instance is recorded
on its row (a non-`alive` one is a `warning` diagnostic). Rows it writes carry `origin: "host-edit"`.
The tfc deploy's status is then recomputed over ALL its non-destroyed rows: `completed` only when
every one is. If the background job cannot even start, the lease is released, the job record says
so, and the PUT answers 500. 401 without the token when one is set.

## GET /api/deploys/{id}/tfc/profile  (new)

The running persona state (completed rows) as a traffic-profile. `?format=yaml` returns the YAML
document (`text/yaml`); default `json`; anything else 400. 404 when the twin has no tfc deploy.
```json
{"deploy_id": "corp-twin-07cfcdb2", "tfc_deploy_id": "tfc-corp-twin-07cfcdb2-785b1a06",
 "twin_id": "corp-twin", "instances": 84, "host_edits": ["ws-01"],
 "yaml": "# Exported from tfc deploy …\n# Save deliberately to twins/<id>/tfc/ …\ntraffic-profile:\n  target: corp-twin\n  assignments:\n  - node: ws-01\n    persona: dev-workstation\n    schedule: 24x7\n",
 "notes": []}
```
Every entry is `node:`-addressed; `roles:` appears only when the tfc deploy has role rows.
`target` is the twin NAME (reusable file). Replay bindings cannot be expressed per node and are
listed in `notes`. Export only — nothing is written anywhere.

## Changed existing routes

- `POST /api/tfc` body gains `allow_revert_host_edits: bool = false`. When the standing tfc deploy has
  host edits (rows with `origin: "host-edit"`, or rows a host edit retired) and the profile would
  change that host's persona set (persona, schedule, seed) or tfc role, the apply is refused
  `409 {"error": "this profile would revert N host edit(s) … (ws-01) …", "detail": {"host_edits": [
  {"host_id": "ws-01", "current": ["t-alt"], "profile": ["t-client"], "current_detail": [...],
  "profile_detail": [...], "role_current": null, "role_profile": null}]}}`. With the flag it proceeds
  and `warnings` names each reverted host. No host edits → behaviour unchanged.
  The target twin's local personas (`twins/<id>/personas/`) are now resolved per request on this
  route too; a profile that names no twin-local persona persists byte-identical op inputs.
  The revert check runs under the `tfc-<te>` lease. After a successful whole-profile apply, the host
  edits it did not keep stop counting (their markers become `host-edit-superseded`), so the next
  profile change needs no flag. A whole-profile apply also stops and retires failed/pending rows the
  profile no longer names.
- Protected deploys (`423`, `override_protection=<twin id>` query param): `DELETE /api/deploys/{id}`
  (a sibling id counts as its twin), `POST /api/deploys` and `POST /api/imprints/{id}/deploy` when they
  would resume/update a protected twin, `PUT …/personas`, `POST /api/tfc` (by `target`/`twin`),
  `DELETE /api/tfc` (by `target` or `deploy_id`), `POST /api/o2/taps` (by `target`),
  `DELETE /api/o2/taps`, `POST /api/exr` (by `target`/`twin`), `DELETE /api/exr`. The CLI is unchanged.
- `GET /api/deploys/{id}` gains `host_edits: []` (tfc deploys: the per-host job records).
- `POST /api/deploys` and `POST /api/imprints/{id}/deploy` accept `expect_plan_id`; a re-plan with a
  different `plan_id` → `409 {"detail": {"code": "plan-changed", "plan_id": "<actual>", "expected": "<sent>"}}`.
  `CreateDeployResponse` gains `plan_id` (the plan deployed).
- `GET /api/imprints` items and `GET /api/imprints/{id}` gain `can_apply` / `apply_blocked_reason`:
  `"a twins/ folder: …"`, `"recompile is not configured: AXRANGE_AXIMPRINT_BIN is unset"`,
  `"… AXRANGE_AXIMPRINT_STORE is unset"`, or the `cast_invocation` refusal text; `null` when
  `can_apply` is true (same checks, same order as `POST …/apply`).
- `GET /api/tfc` bare (no `target`/`deploy_id`) now ignores destroyed tfc deploys;
  `?target=<twin name>` resolves the name to its newest live te deploy (was: never matched).
- `GET /api/scenarios/{id}/topology?deploy_id=latest` and `GET /api/scenarios/{id}/nodes/{node}`
  (no `deploy_id`) now pick the newest live te deploy **of that scenario**, never a tfc/tap row.
- `GET /api/twin-configs` items and `GET /api/twin-configs/{id}` gain `cluster` (from
  `twin.yaml cluster:`, `null` when unset or no manifest). List items also gain
  `parameterized: bool` and `param_names: [str]`; the detail's `params[]` items are
  `{"name": "N_WORKSTATIONS", "default": 25, "description": "How many workstations to stand up.", "declared": true}`
  — declared `params:` entries first (file order; `description` is the comment above or beside the
  entry, `null` if none), then inline-only `${NAME:default}` tokens (`declared: false`).
- `POST /api/plan` and `POST /api/validate` take exactly one of `scenario` / `config` / `imprint`
  (else 400), plus `params: {NAME: "value"}` (config/imprint only; values must be strings, else 400)
  and `target_node` (plan). Pure, no
  auth, no state write; a config/imprint is expanded exactly as `POST /api/deploys` would expand it.
  `PlanSummaryDTO` gains `vm_count` (clone-vm ops) and `params` (the resolved values:
  declared defaults + inline defaults, then the overrides, coerced) — e.g.
  `{"plan_id": "…", "ops": 23, "vm_count": 4, "params": {"N_WORKSTATIONS": 3}, …}`; `{}` for a scenario.
  Its `plan_id` is what `expect_plan_id` pins on `POST /api/deploys` (`config`) and
  `POST /api/imprints/{id}/deploy`.
- `GET /api/imprints` no longer skips a bundle whose node `count:` is a `${NAME:default}` template:
  counts are resolved at their no-override default for display (the cast summary and the fidelity
  reference apply the same rule, with a warning; a param defaulting to `0` counts zero hosts in
  both). `skipped[].reason` remains for a bundle that truly cannot be read.
- `POST /api/deploys` with `config`: adds a `config_warnings` entry when the twin's `twin.yaml`
  names a cluster that is neither the target name nor the PVE node being deployed to:
  `"twin 'corp-twin' names cluster 'dev45' (twin.yaml) but is being deployed to target 'pxtwin1' (node pxtwin1)"`.

## CLI parity

- `axrange tfc set-host <target> <host> (--persona P [--persona Q] | --no-personas)
  [--schedule S] [--role R | --clear-role] [--update-dependents] [--allow-role-orphaning]
  [--plan] [--allow-uncontained]
  [--adapter-config F] [--node N] [--db F] [--images DIR] [--twins-dir DIR] [--json]`.
  `<target>` is a te deploy id or twin name. A role-only change (no `--persona`) keeps the host's set.
  `--plan` prints the preview and writes nothing; a refused plan exits 2.
- `axrange tfc deploy … --revert-host-edits`.
- `axrange serve --targets <file>` (env `AXRANGE_TARGETS`).
