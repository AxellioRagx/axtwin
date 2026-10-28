# API — control-plane seams for the operator console

## What it is

The control-plane API (`axrange serve`, code in `engine/src/axrange/api` today)
is the one surface the console (`ui/Spec.md`) talks to; it serves every step of
the loop in root `CLAUDE.md`. This spec owns the **API side** of the console
work: which endpoints exist, which are new, the contract of each new one, and
the edit model behind the twin-editing endpoints. The UI spec consumes these by
seam id (A1–A7) and defines no endpoint of its own.

Direction (agreed 2026-10-20): the API is engine-internal today (it imports the
planner, twinconfig and o2control layers and reaches `discovery/` only by
running the `aximprint` binary). A1 landed the same way — four `aximprint`
verbs as subprocesses — so the engine still imports nothing from `aximprint`
and the one-way boundary holds. The API moves out of `engine/` into its own
package under this folder, depending on both `axrange` and `aximprint`, when a
seam needs an in-process call (plugin schemas without a fork, run records that
outlive the process); nothing forces it yet. The two layering prerequisites
under **Notes** are done.

## Seam

Existing routes, verified against the registered FastAPI routes on 2026-10-19
(taps renamed from `/api/o2/taps` on 2026-10-20). Everything the console uses
that is **not** listed under a seam below already exists:

`GET /api/pcaps`, `GET /api/imprints[/{id}]`, `GET /api/imprints/{id}/cast`,
`GET /api/discovery/plugins`, `POST /api/discovery/runs`, `GET /api/discovery/runs[/{id}]`, `POST /api/discovery/runs/{id}/cancel`, `DELETE /api/discovery/runs/{id}` (A1, built),
`GET|POST /api/discovery/sources`, `GET|PUT|DELETE /api/discovery/sources/{id}`,
`POST /api/discovery/sources/{id}/probe`, `POST /api/discovery/sources/{id}/runs` (A7, built),
`GET /api/twin-configs[/{id}]`, `GET /api/scenarios/{id}/topology`,
`GET /api/deploys[/{id}]`, `GET /api/deploys/{id}/inventory`, `POST /api/validate`,
`GET /api/targets`, `POST /api/deploys`, `POST /api/deploys/{id}/run`,
`DELETE /api/deploys/{id}`, `GET|POST|DELETE /api/tfc`, `GET|POST|DELETE /api/taps`,
`GET|POST|DELETE /api/exr`, `GET /openapi.json`.

The new seams, each a thin layer over working engine code, in critical-path
order:

**A1 — discovery runs** (root `CLAUDE.md` critical path 1). **Built 2026-10-20**
(`engine/src/axrange/api/discovery.py`, tests `engine/tests/test_api_discovery.py`).
- `GET /api/discovery/plugins` — the installed `IngestPlugin`s (`discovery/`
  `aximprint.ingest.base`) with each one's declared `config_schema` (closed:
  `source` required, plus the shared run options `ts_from`, `ts_to`, `vantage`,
  `network`, `window`, `subbuckets`). Read through `aximprint plugins`.
- `POST /api/discovery/runs` `{plugin, config, imprint_id, sensors?}` — start a
  real discovery: `ingest` → `materialize` → `cast` → `install-cast`, landing
  `<AXRANGE_IMPRINTS_DIR>/<imprint_id>/` as a cast bundle the imprint routes
  read. Returns `202` with the run record at once; the chain runs in a thread.
- `GET /api/discovery/runs/{id}` — `status`
  (`queued|running|completed|failed|cancelled`), `stages_done`, the `error` if
  failed, the `pit_id` if completed. The imprint id is the one the caller
  chose, so it resolves through `GET /api/imprints/{id}`. While `ingest` runs,
  `progress_ts` is the source time it has reached (the end of the last window
  it closed, read from the subprocess's own `window <start>:` lines as they
  arrive) and `progress_at` when it said so; both null before the first window.
- `POST /api/discovery/runs/{id}/cancel` (token, `202`) — stop a queued or
  running run: the current stage's process group is killed (SIGTERM, SIGKILL
  after 5 s), the half-written bundle and the run's belief store are removed,
  the record turns `cancelled` and the imprint id is free again. The response
  carries `cancel_requested_at`; the status turns when the stage is dead
  (poll). A finished run is `409` naming its status; unknown is `404`.
- `DELETE /api/discovery/runs/{id}` (token, `204`) — forget a finished run's
  record. The imprint it installed stays in the catalog. A queued or running
  run is `409` pointing at cancel; unknown is `404`.
- A run's belief store (`<store dir>/<imprint_id>.belief.duckdb`) is removed
  before `ingest` if one is already there: it can only be what an earlier
  failed or cancelled run under that id left, and ingesting on top of it would
  merge two reads.
- Refused at POST, nothing written: an unknown plugin (the known ones named), a
  config the plugin's schema rejects (every problem named), an unsafe imprint
  id, an id already in the catalog (`409`, never overwritten), an id another
  run is still writing. A failed chain removes its own half-written bundle.
- A `source` is **sensor metadata** (a Zeek log tree, an `eve.json`, an
  `axquery:` locator), not a pcap: nothing in `discovery/` reads packets. The
  loop's "feed a pcap" step is the sensor's job (`aximprint acquire` for an
  axapi appliance). No continuous mode: nothing in `discovery/` implements
  one; if wanted it is a discovery spec first.
- Run records are in-memory for the life of `axrange serve`; nothing reads
  them after a restart today.

**A2 — per-deploy working copy** (the edit model, below).
- `GET /api/deploys/{id}/config` — the working copy (the baseline's content
  until first edited) plus `dirty: bool`.
- `PUT /api/deploys/{id}/config` — replace the working copy. Validated through
  the same path as `POST /api/validate`; an invalid body is refused with the
  validator's reason and the stored copy is unchanged. Never touches the baseline.
- `POST /api/deploys/{id}/config/apply` — push the working copy's delta onto the
  running twin without a redeploy. v0 scope is exactly what `tfc` and `taps`
  can change ephemerally; a delta they cannot apply (topology, addressing,
  images) is refused with the offending paths named, and nothing is applied.
- `POST /api/deploys/{id}/config/reset` — discard the working copy and revert
  anything applied from it; the running twin matches baseline again.
- The working copy is **server-side state beside the deploy record**, so a
  console on any host sees the same copy (pairs with A5).

**A3 — twin-config write** (root `CLAUDE.md` critical path 3).
- `POST /api/twin-configs` — create a new twin-config under a chosen id from a
  body (Save-As). An id that already exists is refused, never overwritten.
- `PUT /api/twin-configs/{id}` — overwrite the baseline (Persist from a working
  copy, or Save on an undeployed config). After a Persist the deploy's working
  copy equals the baseline and `dirty` is false.
- Both validate before writing; a read after a write returns the written
  document byte-for-byte equivalent.

**A4 — targets management.**
- `POST|PUT|DELETE /api/targets[/{name}]` — create / update / remove a target
  (a named entry of the adapter targets file). Credentials are stored by the
  API, never echoed back in any read.

**A5 — platform-origin deploys.**
- `GET /api/deploys?origin=platform` — reconstruct the live twins by querying
  the platform's tier-tagged resources instead of the local state DB, so a
  browser on another host sees them. The parameter is `origin`, not `source`:
  `source` already means catalog pin (`scenario|twin-config|imprint`) on the
  topology routes.

**A6 — tap metrics** (the capture-flow gate, root `CLAUDE.md`).
- `GET /api/taps/{id}/metrics` — `packets`, `bytes`, `rate` from the sensor
  behind the tap, or from a dummy `tcpdump` node stood up through the existing
  tap verify-capture path.

**A7 — discovery sources** (the console's "configure a discovery source" step,
root `CLAUDE.md` loop step 1). **Built 2026-10-27**
(`engine/src/axrange/api/discovery_sources.py`, tests
`engine/tests/test_api_discovery_sources.py`; `discovery/` side: `source_kind`,
`IngestPlugin.probe`, `aximprint probe`, the environment credential in
`aximprint.ingest.axquery.sources`).
- A **source** is a saved way to reach a sensor's query API:
  `{name, plugin, endpoint, access, password?|api_key?, defaults}`. `endpoint`
  is the plugin's base URL **as seen from where the access path lands**
  (`http://127.0.0.1:9090` for an appliance's loopback behind a tunnel).
  `access` is `transport` (`direct` | `ssh_tunnel`, the latter with
  `ssh {host, port, user, key_path}` — a private-key *path* on the API host,
  never key material) × `auth` (`none` | `basic` + `user` | `api_key`).
  `defaults` pre-fill a run: `days`, `network`, `vantage`, `window`,
  `subbuckets`, `sensors`.
- Store: one SQLite file, `AXRANGE_DISCOVERY_DB` (default
  `~/.axrange/discovery.db`, mode 0600, outside the checkout, **not** a table
  in the planner's `state.db`). **A read never returns a secret** — only
  `has_password` / `has_api_key`. On `PUT` a secret field left out keeps the
  stored value, `""` clears it; the stored set is filtered to what the auth
  mode uses, so switching modes drops the other secret.
- Only a plugin whose listing says `source_kind: "http"` can back a source
  (`GET /api/discovery/plugins` now carries it; `zeek`/`suricata` are `path`).
- `POST .../{id}/probe` (token) — open the access path, run `aximprint probe`
  (`IngestPlugin.probe`: `/api/info` for axquery), record the answer on the
  source (`last_probe`). Not reachable is an **answer** (`200`,
  `reachable: false` + the reason in ssh's or the plugin's own words), never
  a 5xx; `503` only when discovery is unconfigured.
- `POST .../{id}/runs` `{imprint_id, days? | ts_from+ts_to, network?, vantage?,
  window?, subbuckets?, sensors?}` (token, `202`) — an A1 run whose `source`
  and credential come from the record. `days` is resolved **when the run
  starts** from the source's own span (`[last_ts+1 − days·86400, last_ts+1)`,
  clamped to `first_ts`); the record reports the bounds used, `via` (how it
  was reached, never a credential), `started_at`. The tunnel is `ssh -N -L` in
  BatchMode (a passphrase key or a host missing from `known_hosts` fails in
  ssh's words rather than hanging), held open for the whole chain, torn down
  with it. The credential reaches the one `aximprint` subprocess through its
  environment (`AXIMPRINT_SOURCE_USER` / `_PASSWORD` / `_API_KEY`), never
  `argv` or the locator. Budget: `AXRANGE_DISCOVERY_TIMEOUT_S` (default 6 h)
  per stage, since a multi-day ingest is hours; the one-cast
  `AXRANGE_AXIMPRINT_TIMEOUT_S` does not apply.
- `GET /api/discovery/runs` — every run this process started, newest first.
- Refused before the path opens: a bad window (both or neither), an unsafe or
  taken imprint id, an id another run is writing, a missing key file; a source
  with a run in progress cannot be deleted (`409`).
- Measured 2026-10-27 against `corp-apcn-reader` (axquery on the appliance's
  loopback `:9090`, 30 days / 69 chunks, through the tunnel): probe 0.65 s; a
  one-hour slice ingest→install 4.5 min (249 hosts, 42 segments). A ten-day
  read is therefore hours, which is what the long budget and the run page's
  polling are for.

**Not being built (stated so it is not re-proposed):** `GET /api/attacks` and
any attack plugin registry (the exercise request schema's `activity` and
`adversary` kinds are the playout vocabulary, read from `/openapi.json`); a
deploy-platform plugin registry (targets are the named entries A4 manages; one
Proxmox adapter exists). Either is a new abstraction and needs explicit sign-off.

### The edit model: baseline + working copy

The editable object is the **twin-config**. Its catalog entry is the
**baseline**. A deployed twin carries a server-side **working copy** (A2) the
editor operates on, never the baseline directly. Four exits:

| Exit | Endpoint | Effect |
|---|---|---|
| Apply | A2 apply | working-copy delta onto the running twin, no redeploy |
| Reset | A2 reset | discard the working copy and anything applied from it |
| Persist | A3 `PUT` | baseline := working copy; copy becomes clean |
| Save-As | A3 `POST` | new twin-config under a new id; baseline, deploy and copy untouched |

An undeployed config has no working copy: Save (A3 `PUT`) and Save-As only.
A discovered imprint is cast first (`GET /api/imprints/{id}/cast`) and edited
as a twin-config. **The baseline is written by Persist and Save and by nothing
else.**

Why the imprint-side write path that already exists is **not** this:
`PUT /api/imprints/{id}/assertions` + `POST .../apply` rewrite the operator
assertions, re-cast the twin from the discovered source and install the result
over the catalog bundle (keep-5 history, `GET .../diff`). That edits the
*derivation* of a twin, persistently, with no notion of a running deploy or a
reset-to-baseline. It stays as is, behind the console's provenance view.

## Config

No new config for A2–A6. A1 reuses the existing settings that already name
the discovery side (`AXRANGE_AXIMPRINT_BIN`, `AXRANGE_AXIMPRINT_STORE`, spelled
in `api/recompile.py`, and `AXRANGE_IMPRINTS_DIR`, which a run must have set
so it never writes into the package's fixture directory) until the API imports
`aximprint` directly, at which point `BIN` goes away and the others stay. A
run's belief store lands beside the IR store as `<imprint_id>.belief.duckdb`;
`materialize` runs with `--replace` because the IR store is the API's own
working store and nothing in the API compares PITs. `AXRANGE_SPA_DIR` co-serve is already
wired. Writes stay behind `AXRANGE_API_TOKEN` exactly like the existing deploy
writes; every new POST/PUT/DELETE above is token-gated.

## Acceptance

All **(auto)** under `pytest` with the Starlette test client against a seeded
state DB and the fake adapter, unless marked **(live)**.

- A1.1 `GET /api/discovery/plugins` lists every installed `IngestPlugin` with a
  schema; an environment with zero plugins returns an empty list, not an error.
- A1.2 `POST /api/discovery/runs` with a Zeek-log source reaches `completed`
  (the real `aximprint` chain, on a synthetic capture) and the imprint id it
  reports resolves through `GET /api/imprints/{id}`.
- A1.3 An unknown plugin or an invalid config is refused at POST with the reason
  named; nothing is written to the catalog.
- A2.1 A fresh deploy's `GET .../config` equals its baseline and is not dirty.
- A2.2 `PUT .../config` changes only the working copy: the baseline read is
  unchanged, `dirty` is true, and a second client reads the same copy.
- A2.3 An invalid `PUT` is refused with the validator's reason; the stored copy
  is unchanged.
- A2.4 `apply` with a tfc/tap-only delta drives the tfc/taps paths and the
  running twin reflects it without a redeploy.
- A2.5 `apply` with a non-ephemeral delta is refused naming the paths; the twin,
  the copy and the baseline are untouched.
- A2.6 `reset` after an apply reverts the twin's tweak and clears the copy.
- A3.1 `POST /api/twin-configs` under a new id persists; a re-read is
  byte-for-byte equivalent; the source baseline is unchanged.
- A3.2 `POST` under an existing id is refused; nothing is overwritten.
- A3.3 `PUT /api/twin-configs/{id}` from a working copy overwrites the baseline
  and leaves the copy clean. No other call ever changes a baseline (asserted by
  running A1–A6's full sequence and diffing the catalog).
- A4.1 Target create / update / delete round-trips; credentials never appear in
  any read.
- A5.1 `GET /api/deploys?origin=platform` against the fake adapter's tagged
  resources returns the same twins the state DB knows.
- A5.2 (live) A second host with an empty state DB enumerates dev45's live
  twins through `origin=platform`.
- A6.1 `GET /api/taps/{id}/metrics` returns packets/bytes/rate from a dummy
  `tcpdump` node.
- A6.2 (live) On dev45, two polls ten seconds apart show packets and bytes
  increasing (rate > 0) — the capture-flow gate.
- A1.4 `progress_ts` advances from the ingest's own window lines while it runs;
  `POST .../cancel` on a queued run turns it `cancelled` with the bundle and
  belief store gone and the id free; a real child in its own session is killed
  mid-stream and reported as cancelled, not failed; a stage past its budget is
  killed and reported as a timeout. `DELETE` forgets a finished run and is
  `409` on an active one. *(`engine/tests/test_api_discovery.py`.)*

## Notes

- **Order:** A1 first (critical path 1), then A3 + A2 together (the edit model
  is one feature), then A6, A5, A4. The console's T0 gate needs only existing
  routes plus A6.
- **Layering prerequisites for moving the API out of `engine/`:** (1) the
  deploy executor entry points (`run_deploy_executor`, `release_deploy_resources`,
  `build_plan_and_ctx`) live in `api/service.py` and are imported by the
  o2control playouts in six places — a domain layer depending on the API layer.
  They move into the planner before anything else. (2) The persona catalog
  (41 YAML files in `axrange/personas/catalog`) is content, not code; it moves
  to `catalog/personas/` with its default spelled in `axrange.paths`.
- **Known limit (A5):** reconstruction fidelity depends on tier-tagged resource
  names being present; a hand-made resource will not appear. Acceptable; record
  if it bites.
- Nothing here edits root `CLAUDE.md`; each seam lands on its own branch with
  its tests, and a short `SPEC.md` beside its code when it starts.
