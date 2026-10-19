*The axRange Console: one run-time-first application over the state store and the live cluster —
areas, the eight screen rules, the deployed-twin model, per-host persona apply, multi-target
serving, and the URL grammar. Companion to `11-api-ui.md` (the control-plane API and CLI grid) and
`15-console-canvas.md` (the Design area's assertions editor, unchanged in function here).*

> **STATUS: DESIGN RATIFIED 2026-09-29, BUILT AND LIVE-VERIFIED 2026-09-30 (Authority: implementation
> (Claude Code), under the user's 2026-09-29 instruction to design and build the consolidation, with
> any prior decision supersedable provided it is recorded).** Full ruling and evidence:
> `docs/reference/ui-consolidation/00-design-ruling.md`, `01-backend-audit.md`,
> `02-ui-surface-inventory.md`, `03-ui-history-and-decisions.md`; API contract as built, including the
> four review/live-verify fix rounds: `04-api-contract.md`. Supersedes five prior decisions — see
> `docs/CHANGELOG.md` 2026-09-29 for the `Supersedes:` list with citations. Live-verify record: a
> read-only pass against the standing `corp-twin-07cfcdb2` (2026-09-29) and a write-path pass against a
> disposable `sme-flat` deploy (2026-09-30) — `docs/CHANGELOG.md` 2026-09-30 "Unified Console live
> verification"; `docs/archive/work_orders/WORK_ORDER_unified_console.built.md`.

## 1. What this is

There is **one operator-facing application**: the axRange Console (`o2/console/`, served by
`axrange serve --spa-dir`). It is **run-time first** — it opens on what is running, not on a design
file — and it is the one surface an operator needs to answer, from a cold browser tab: which twin is
on which cluster, what it looks like, which personas run on which host, how to change one host's
personas, and whether it's healthy.

Grafana stays, narrowed to **telemetry only**: three live dashboards (capture health, Suricata
alerts, attack coverage), linked from the Console. It is no longer where an operator deploys,
inspects a twin, or drives traffic.

## 2. Areas

Four top-level areas, in this order:

| Area | Answers | Source |
|---|---|---|
| **Range** (home) | Which twins are up, on which cluster, with which taps/traffic/exercises, are they healthy | `GET /api/range`: state store + a live per-target reachability probe |
| **Twin** (one deployed twin) | What it looks like (map + host table), personas per host, per-host health and access, change a host's personas, destroy | `GET /api/deploys/{id}/hosts` (the one model) + on-demand `GET /api/deploys/{id}/health` |
| **Catalog** | Every twin in `twins/` and every scenario; validate / plan / deploy (guarded) | the twin-config registry |
| **Design** | The pre-existing assertions canvas (`15-console-canvas.md`), unchanged in function, relabelled | `/api/imprints/*` |

Plus **Telemetry**: link-outs to the three live Grafana dashboards. Grafana is never embedded or
iframed for anything else; the S7 iframe embed of `15-console-canvas.md` §9 is retired along with the
rest of the plugin (§9 below).

## 3. Rules that bind every screen

1. **Viewer before editor.** Every area renders something useful for every twin, including
   hand-authored ones with no aximprint export. An empty view says why it is empty.
2. **One model, one renderer** for a deployed twin: the state-store inventory
   (`GET /api/deploys/{id}/hosts`, built on `planner.inventory.build_twin_inventory`) adapted into
   the Console's existing `src/layout/*` engine. The IR re-parse path
   (`GET /api/scenarios/{id}/topology`) is never used for a deployed twin's view — it can drift from
   what was actually deployed, since only `ir_hash` is stored, not the IR itself.
3. **Recorded vs. live is always labelled.** Anything read from the state store says "recorded";
   anything read from the cluster (Proxmox `/cluster/resources`, QGA, a driver heartbeat) says
   "live" with its age (`checked_at`). A design-time count is never called "live" — this is the
   defect the Design area's canvas had (`docs/reference/ui-consolidation/02-ui-surface-inventory.md`
   §"live" is mislabelled) and the rule this design exists to prevent elsewhere.
4. **Never invent facts.** A value that could not be read is shown as unknown (`{"state": "unknown",
   "reason": …}`), never as zero or false. A host count that cannot be derived is `null` with a
   warning, never `0`.
5. **A table is a first-class view.** The Twin area's host table (host, segment, role, OS, personas,
   status) is filterable and sits beside the map; map and table share selection. A 106-host twin is
   answered by the table as much as by the map.
6. **Every write is preview → confirm → progress → result.** The preview states exactly what will
   start, stop and stay untouched. Destroy requires typing the deploy id.
7. **Plain vocabulary on run-time screens.** Twin, host, segment, persona, tap, sensor, cluster.
   "imprint", "cast", "assertion", "ghost", "occupancy", etags and other aximprint/Design vocabulary
   stay inside the Design area, with its own glossary.
8. **The URL is the state** (kept from `15-console-canvas.md`): see §8 below. Old `/imprints/...`
   URLs redirect to their `/design/...` equivalent.

## 4. The one deployed-twin model

A deployed twin is rendered from exactly one read: `GET /api/deploys/{id}/hosts` (`{id}` is the `te`
deploy id; a tap/tfc/exr sibling id resolves to its parent twin). It is state-store only — no cluster
call — and returns:

- the host roster (`HostInventoryDTO` fields: host_id, vm_name, vmid, pve_node, vm_status, role,
  nets, os, listen), plus `role_origin` (`baseline | tfc | host-edit`);
- each host's non-destroyed persona instances (`personas[]`: instance_id, persona, os_family, drive,
  schedule, peers, status, `origin: profile | host-edit`, `heartbeat`, `last_error`);
- each host's tap bindings (`taps[]`: sensors, directions, status);
- the sibling tfc/tap deploy ids and their status;
- `net_bridge`, `vlan_underlay`, `egress`, and `warnings` (e.g. a tfc row naming a host the inventory
  does not have).

This single DTO is what the Console's `src/layout/*` engine draws as the map, and what the host table
lists — the same data, two coordinated projections, sharing selection (rule 5 above). Nothing about a
deployed twin is drawn from a second model; the IR re-parse path stays reachable only from the Design
area, over an imprint/scenario id, never over a deploy id.

Live facts are a separate, on-demand call: `GET /api/deploys/{id}/health` does one
`/cluster/resources` sweep (5 s timeout) for every host's VM power state, and, with `?host=<id>`,
adds that one host's QGA reachability and persona heartbeats. This is never a whole-twin QGA fan-out
— live per-host detail is fetched only when an operator asks for that host.

## 5. Per-host persona apply

The scalpel this consolidation adds to the engine (`o2control/traffic.py`
`apply_host_personas`): change one host's persona set without touching any other host's traffic.

**What is touched.** Only the target host's persona instances and (if a role is given) its tfc role
row. The desired set is the standing tfc deploy's completed rows with only this host's entries
replaced, resolved through the unchanged `resolve_traffic_bindings` (duplicate/requires/peer/OS
checks all still run), composed the normal way, and `force_reset_ops` runs on this host's instance
ops only — the executor skips every other row, which is already `completed`.

**What is never touched.** Every other host's persona instances and role. This is proven, not
assumed: the API's plan response (`POST .../personas/plan`) lists exactly what will start, stop, and
stay unchanged (counted per rule 6 above), and de-scoping (stopping a dropped instance) is restricted
to this host's own dropped instances.

**Dependents opt-in.** A role change on this host can invalidate the peer picks other hosts resolved
against it (their `peers` map was fixed at their own last apply). The plan lists these dependents and
the stale peer values, but never re-drives them unless the caller opts in
(`update_dependents: true` — "also update N hosts that talk to this one"). Nothing else on the twin
is ever silently re-driven by a host edit.

**Host-edit marking and the whole-profile revert guard.** A per-host apply writes its rows with
`origin: "host-edit"` (as opposed to `"profile"` for a whole-profile apply). A later whole-profile
apply (`POST /api/tfc`, `axrange tfc deploy`) that would revert one of those edited hosts back to the
profile's own assignment is refused (`409`, naming each host and what would change) unless it opts in
explicitly (`allow_revert_host_edits: true` on the API; `--revert-host-edits` on the CLI). This is
never silent: a whole-profile apply with no host edits in its way behaves exactly as before.

**Heartbeat.** After a per-host apply starts an instance, the driver's `heartbeat()` is called once
over QGA and its result recorded on that instance's row (`alive | dead | unknown`, with a reason and
`checked_at`). "Started" is not "running" — the heartbeat is what lets the Twin area show a host's
personas as confirmed-alive rather than merely launched. A whole-profile apply does not take a
heartbeat (unchanged from before this consolidation); only a per-host apply does.

**Export, not write-back.** `GET /api/deploys/{id}/tfc/profile` exports the tfc deploy's running
state (including any host edits) as a traffic-profile YAML. This is read-only: nothing is written
back into `twins/<id>/tfc/` automatically. An operator who wants a host edit to survive the next
whole-profile apply saves the exported YAML into the twin's own `tfc/` directory deliberately.

**Plan fingerprint.** `POST .../personas/plan` returns `fingerprint`, a hash of the plan it
computed; the PUT body carries that value back as `expect_plan`. The PUT takes the tfc lease
**before** it re-plans (lease-first, closing the review-found window where two concurrent host
edits could both preview against a stale plan) and re-derives the plan under the lease — if it no
longer matches `expect_plan`, the write is refused (`409`, code `plan-changed`, with the fresh plan
attached) rather than silently executing a plan the operator never saw. The same pin closes GAP-098
for the Catalog's guarded deploy: `POST /api/plan`/`POST /api/validate` accept a twin config or
imprint id (with `params`) as well as a scenario, and `POST /api/deploys` /
`POST /api/imprints/{id}/deploy` accept the matching `expect_plan_id` from that preview, refusing
`409 plan-changed` on a mismatch instead of re-planning blind. `GET /api/imprints` and
`GET /api/imprints/{id}` carry `can_apply`/`apply_blocked_reason` so the Catalog can grey out a
deploy action it already knows will refuse.

**Persisted diagnostics and history.** Every host-edit job (per-host or whole-profile) writes a job
record to the state store, carrying `diagnostics: [{level, message}]` — a stop that failed or a
post-stop heartbeat that did not say `dead` is recorded there rather than lost when the request
returns. `GET /api/deploys/{id}` `host_edits[]` lists **every** job, newest first, even after a
later apply re-drives the rows it touched: each row's `host_edit_history` is append-only. A
whole-profile apply that supersedes an earlier per-host job marks that job
`"superseded": true, "superseded_at": <epoch>` rather than dropping it — `/hosts`' `host_edit`
still shows a host's newest job, carrying those two fields once a whole-profile apply has taken
over (the personas then read `origin: "profile"`).

**Protected deploys.** `/api/range` twins and `/hosts` carry `protected: bool` — a twin (or any
tap/tfc/exr sibling of one) named on the target's `protected_deploys` list. Every write route aimed
at a protected deploy (destroy, resume/update, a whole-profile or per-host persona apply, tap/exr
start/stop) is refused `423` (`code: "protected"`) unless the request carries
`override_protection=<that te deploy id>` — naming the id back is what makes the override
deliberate rather than a flag left on by habit. `axrange serve` sets the list from
`AXRANGE_PROTECTED_DEPLOYS` (or a target's `protected_deploys:`) at startup.

## 6. Recorded vs. live, everywhere

Every live value — cluster reachability, VM power state, QGA reachability, a persona heartbeat — is
returned inside a `LiveValueDTO`-shaped object carrying `state`, `checked_at`, `reason`, and
sometimes `detail`. A value the Console has not asked to check yet is `{"state": "unknown", "reason":
"not checked"}`, never a blank or a false "down". A `GET /api/range` reachability sweep is done once
per target per call; a per-host health/heartbeat read happens only when the Twin area's host detail
is opened for that host. The Console never polls a whole-twin live sweep in the background.

## 7. Multi-target serving

`axrange serve --targets <file>` (or `AXRANGE_TARGETS`) serves several clusters from one process, each
with its own `adapter_config` and its own `db` (a shared db across targets is refused at startup).
With no targets file, the single `--adapter-config`/`--db` pair is the one implicit target, named
after its `DEFAULT_NODE`. `GET /api/targets` lists every target with its reachability facts;
`GET /api/range` paints one target per column, each with its own standing twins. Deploy-state routes
accept `?target=<name>` (default: the first/default target); the tier routes (`/api/tfc`,
`/api/o2/taps`, `/api/exr`) keep `?target=` meaning *the twin*, unchanged, and always use the default
target — see `docs/reference/ui-consolidation/04-api-contract.md` for the full split.

A deploy's cluster is the target its store was read from, cross-checked against `placement_json` node
names; a mismatch is a surfaced warning, never a hidden reassignment. `twin.yaml cluster:` is
consulted by the Catalog: it pre-selects the twin's declared cluster and warns when deploying it
elsewhere.

**Several stores per one target.** Orthogonal to serving several clusters: one target (one cluster)
can also read **several state stores** — a targets-file entry's `extra_dbs: [path, …]` list, or the
implicit target's `--extra-db PATH` (repeatable, `axrange serve`) / `AXRANGE_EXTRA_DBS` (paths
joined by `os.pathsep`). The first store stays primary (new deploys are written there); every read
that lists or resolves a deploy by id searches every store of the target and reports which one it
came from (`store`, a short label such as `"state.db"` or `"axr-svcengines.state.db"`, on
`DeploySummaryDTO`, `/api/range` twins, `/hosts`, and each target's `stores: [{label, path,
primary}]` on `GET /api/targets`). A write against an existing deploy goes to the store that holds
it, never the primary by default; the same scenario or plan recorded in two stores of one target is
refused as `409 ambiguous-store` rather than guessed at (a recurrence of LES-156's ownership-not-
existence class — see `docs/LESSONS.md`). Every store is opened **read-only** at startup and at
request time (`mode=ro` + `PRAGMA query_only=ON`, never `immutable=1` — LES-359: a truly-immutable
open takes no locks and can read a torn page mid-checkpoint); an unreadable store degrades that one
read with a `warnings[]`/`ok: false` entry rather than failing the whole call.

**Unrecorded VMs.** Every target's `/api/range` entry also carries `unrecorded`: axRange-named VMs
(`te-…`/`o2-…`/`tfc-…`/`exr-…`, never a template, never an `EXTERNAL_SENSORS` vmid) that the live
cluster sweep finds but no non-destroyed deploy in *any* of the target's stores records — the gap a
several-stores model would otherwise hide silently. `state: known|unknown` (`partial` with a reason
when a store could not be read), up to 50 VMs listed, `null` on `?live=false`.

## 8. URL grammar

The URL is the state, kept from `15-console-canvas.md` §7, re-rooted under the Console's four areas:

| route | area |
|---|---|
| `/range` | Range (home) |
| `/twin/<deploy_id>` | Twin — map + host table |
| `/twin/<deploy_id>/hosts/<host_id>` | Twin — one host's detail and persona edit |
| `/catalog` | Catalog |
| `/design/...` | Design — the existing `15-console-canvas.md` §7 grammar (`/imprints/<id>`,
`/imprints/<id>/segments/<cidr>`, `/imprints/<id>/hosts/<key>`, `/imprints/<id>/matrix`, and their
filters), re-rooted at `/design` in place of `/imprints`. Old `/imprints/...` links redirect to their
`/design/...` equivalent. |

## 9. What happens to each prior surface

- **Console SPA** (`o2/console/`) becomes the one app. Its layout engine, URL model, API client and
  theme are kept; the existing canvas becomes the Design area (§8).
- **Grafana control-panel plugin** (`axellio-axrange-control-panel`) was deprecated: its guarded
  deploy flow, deploy/op tree and "why empty" states are re-implemented in the Console's Catalog
  and Twin areas. Deleted 2026-10-01 (`docs/CHANGELOG.md`; the user approved the deletion list,
  `docs/reference/ui-consolidation/05-deletion-candidates.md`).
- **The seven-renderer topology bake-off and its `topology-eval` dashboards** were closed without a
  winner — the Console's layout engine is the renderer. Deleted 2026-10-01.
- **Capture health, Suricata alerts, attack-coverage dashboards** are kept, as telemetry, linked from
  the Console.
- **Deploy/op-status, pcap-browser, control-console dashboards** were deprecated, then deleted
  2026-10-01 — superseded by Range (deploy/op status) and Twin (per-host status); pcap browse/download
  remains unbuilt either way (§10).

## 10. Not in this round

SSE/websocket (the Console polls, same as the plugin did); pcap download; an exercise-run UI;
multi-node placement; write-back of a per-host edit into a twin's own `tfc/*.yaml` (export only, §5);
Console column wrapping (`15-console-canvas.md` §12's named follow-up, unchanged by this design).

## Where this fits

- [11-api-ui.md](./11-api-ui.md) — the control-plane API and CLI grid this design's new routes extend;
  see `docs/reference/ui-consolidation/04-api-contract.md` for the routes themselves.
- [15-console-canvas.md](./15-console-canvas.md) — the Design area's assertions editor, unchanged in
  function; only its place in the app (relabelled, re-rooted at `/design`) and its "independent of the
  plugin" framing change here.
- [02-twin-model.md](./02-twin-model.md) — the five-tier model (`te`/`tap`/`tfc`/`exr`/`o2`) the Range
  and Twin areas surface.
- [05-o2-surface.md](./05-o2-surface.md) — the O2 telemetry the Console links out to.
- `docs/archive/work_orders/WORK_ORDER_unified_console.built.md` — the build record: ruling →
  commits → the four review/live-verify fix rounds → verification → open items.

For the design reasoning trail — the seven-surface history, what each one delivered, and why this
shape was chosen — see `docs/reference/ui-consolidation/*.md` and `docs/CHANGELOG.md` 2026-09-29 and
2026-09-30.
