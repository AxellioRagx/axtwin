# Operations — install, configure, run

How to run the instrument. Design is in `docs/ARCHITECTURE.md`; what bites in a
real run is at the end of this file and stays only while it still bites.

## 1. Install and test

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ./engine[api,dev] -e ./discovery[dev,acquire]
```

The two test trees are both named `tests/` and cannot run in one pytest
invocation (their `conftest.py` modules collide). Run them per package:

```sh
(cd engine    && ../.venv/bin/python -m pytest -q -n auto)
(cd discovery && ../.venv/bin/python -m pytest -q)
./discovery/scripts/verify_tier.sh          # discovery's cluster-free tier: determinism + CLI round-trip
```

Discovery's suite needs the `[acquire]` extra installed or half of it fails at
import. The engine's API tests inject fake backends and never run the real
`aximprint` binary unless a test chooses to.

## 2. Configure

| What | Where |
|---|---|
| Proxmox adapter: API URL and token, SSH key, SDN, O2 endpoints, `EXTERNAL_SENSORS` | `config/adapter.conf` (gitignored), from `config/adapter.conf.example`. Found at `/etc/axrange/adapter.conf`, then `config/`, then the cwd; `--adapter-config` / `AXRANGE_ADAPTER_CONFIG` override. |
| Named deploy targets, each with its state stores and protected deploys | `config/adapter.targets.example.yaml`, pointed at by `AXRANGE_TARGETS` |
| Observability stack | `o2/observability.conf` (gitignored), from its example beside the compose files |
| Image roots (manifests and bindings) | outside the checkout; `--images <root>` / `AXRANGE_IMAGES_ROOT`, the parent of `images/` |
| Planner state DB and every other live-deploy artifact | with that deployment, `~/.axrange/state.db` by default; `--db` to choose |

Environment variables the API and CLI read:

| Variable | Meaning |
|---|---|
| `AXRANGE_ADAPTER_CONFIG`, `AXRANGE_DB`, `AXRANGE_EXTRA_DBS`, `AXRANGE_TARGETS` | adapter config, primary state store, extra read-only stores, targets file |
| `AXRANGE_IMAGES_ROOT`, `AXRANGE_SCENARIOS_DIR`, `AXRANGE_TWINS_DIR`, `AXRANGE_TWIN_CONFIGS_DIR`, `AXRANGE_PERSONAS_DIR` | content roots; unset defaults come from `axrange.paths` (`catalog/...`) |
| `AXRANGE_IMPRINTS_DIR` | where installed casts live and where a discovery run writes; must be set for a run so it never writes into the package fixtures |
| `AXRANGE_AXIMPRINT_BIN`, `AXRANGE_AXIMPRINT_STORE`, `AXRANGE_AXIMPRINT_TIMEOUT_S` | the `aximprint` executable, the IR store the API materializes into and casts from, the per-verb timeout |
| `AXRANGE_API_TOKEN`, `AXRANGE_PROTECTED_DEPLOYS`, `AXRANGE_SPA_DIR` | the write token, deploy ids no write may touch without `override_protection`, the console bundle to co-serve |

## 3. The loop by CLI

First target: `catalog/twins/corp-enterprise/` (its README has the full
sequence and the teardown). Run from the repo root; on a lab cluster add
`--images <root> --db <state.db>` to every command.

```sh
axrange o2 status                                                        # 0. standing sensors present?
axrange te deploy --config corp-enterprise                               # 1. the twin
axrange tap deploy catalog/twins/corp-enterprise/tap/default.yaml --target corp-enterprise   # 2. capture
axrange tfc deploy catalog/twins/corp-enterprise/tfc/default.yaml --target corp-enterprise   # 3. traffic
axrange exr run    catalog/twins/corp-enterprise/exr/ad-killchain.yaml --target corp-enterprise  # 4. attack
axrange te inventory corp-enterprise; axrange te info; axrange tap status --target corp-enterprise
axrange te destroy corp-enterprise --allow-destroy                       # cascades exr -> tfc -> tap -> te
```

`--target <name>` resolves to the newest live twin of that config. Per-host
traffic edits without a whole-profile re-apply:
`axrange tfc set-host <target> <host> --persona <p> [--role <r>]`.

**The O2 server half** (Grafana, Loki, Prometheus, Caldera, Wazuh manager) is
stood up by hand with docker compose on a host the twin can reach; the runbook
is `o2/README.md`. `axrange o2 deploy` stands up only the sensor VMs.

**The acceptance gate.** The loop is working when tapped traffic reaches a
sensor and its capture metrics climb: `tap status` for the bindings, the
sensor's own counters (`capture-healthcheck` on a `tcpdump-sensor`, the capture
health dashboard once O2 is up). For an external packetXpress sink confirm on
the appliance's own tooling; axRange cannot verify capture there and says so.

## 4. Discovery by CLI

```sh
aximprint plugins                                                   # installed ingest plugins + schemas
aximprint ingest --plugin zeek --source <zeek log dir> --store imprint.duckdb [--vantage <tap>] [--network <name>]
aximprint status --store imprint.duckdb
aximprint materialize --store imprint.duckdb --out ir.duckdb [--as-of <epoch>] [--replace]
aximprint cast --store ir.duckdb --pit <pit id> --out <cast dir> --imprint-id <id> [--assertions <file>]
aximprint install-cast <cast dir> ...                               # -> catalog/twins/<id>/ as a cast bundle
```

A `source` is sensor metadata (a Zeek log tree, an `eve.json`, an `axquery:`
locator), never a pcap. Pulling pcap off an appliance and running Zeek over it
is `aximprint acquire` (needs the `[acquire]` extra; it reaches a live
appliance and moves gigabytes, so it is never run unattended).

## 5. The API

```sh
AXRANGE_API_TOKEN=<token> axrange serve --host 0.0.0.0 --port 8080 \
  --adapter-config config/adapter.conf --images <root> --db <state.db> \
  --twins-dir catalog/twins --scenarios-dir catalog/target-environments \
  --imprints-dir <imprints dir> --spa-dir <console bundle>
```

Reads are open on the bind; every POST, PUT and DELETE needs the token. For
discovery runs the environment must also carry `AXRANGE_AXIMPRINT_BIN`,
`AXRANGE_AXIMPRINT_STORE` and `AXRANGE_IMPRINTS_DIR`:

```sh
curl -s :8080/api/discovery/plugins
curl -s -X POST :8080/api/discovery/runs -H "Authorization: Bearer $AXRANGE_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"plugin":"zeek","config":{"source":"/path/to/zeek/logs"},"imprint_id":"corp-imprint"}'
curl -s :8080/api/discovery/runs/<run id>      # queued | running | completed | failed
curl -s :8080/api/imprints/corp-imprint        # resolves once completed
```

## 6. What bites

Durable gotchas from real runs. Delete one when the cause is fixed.

- **A `te deploy` may retry an app op once** on the Debian apt-daily race
  (`dpkg` lock held by unattended-upgrades); attempt 2 passes. Expect it.
- **The mirror-MTU path is runtime only.**
  `catalog/twins/corp-twin/scripts/mirror-mtu-path.sh apply` must be re-applied
  after a reboot, a NIC replug or a gretap re-create. Never run its `revert`
  after a `tap destroy` (it exits on the first missing gretap); remove the rule,
  table and MTU by hand.
- **Judge a persona at the application layer**, by heartbeat and message
  success, not by wire bytes: SMB can look alive on the wire while every send is
  reset.
- **Flat `tshark` `tcp.analysis.*` counters are meaningless** on a capture
  tapped at more than one hop.
- **Never build into `o2/console/dist` while `axrange serve` is serving it.**
- **Every store read opens read-only**; empty `-wal` and `-shm` files may
  appear beside a store, and `immutable=1` must never be used.
- **`axrange serve` fails loud** when a targets file is combined with
  cluster-identity environment variables, or a named extra store is missing.
  A deploy id present in two stores of one target is refused (`409
  ambiguous-store`). A protected deploy needs `override_protection=<id>` on
  every write.
- **A whole-profile `tfc deploy` restarts every persona unit** on the twin; use
  `tfc set-host` or the API's per-host apply for one host.
- **An interrupted `tfc destroy` blocks its retry for about seven minutes**
  (the lease); wait. A plain `te deploy` after a destroy mints a new deploy id.
- **An external sink's capture is never verified by axRange**; confirm it on
  the appliance.
- **Live-deploy data lives with the state DB, never in the checkout.**
