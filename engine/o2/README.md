# axRange Observability — Grafana + Loki + Prometheus (Phase 1)

> **`o2/` is the Observability-&-Orchestration surface layer** (`docs/design/05-o2-surface.md`). This
> folder root holds the **telemetry stack** documented below (Grafana/Loki/Prometheus/Alloy); the
> **`o2/tcpdump-sensor/`** subdir is the standing capture appliance, and **`o2/console/`** is the
> axRange Console, the one operator UI. The engine (`axrange/`) stays standalone, outside `o2/`.

The **telemetry / observe** half of the axRange UI (`docs/design/11-api-ui.md` "Phase 1"). A self-contained component — sibling to `o2/tcpdump-sensor/` — that stands up
**Grafana OSS + Loki + Prometheus** via docker-compose and wires the
**already-existing** axRange telemetry sources into it:

- **Suricata alerts** (the network sensor's `eve.json`) → **Loki**, via Grafana Alloy.
- **Capture health** (`capture-healthcheck`) → **Prometheus**, via a node_exporter
  textfile exporter on the capture host.
- **Attack coverage** (A4 detection scoring: attack ground-truth vs. Suricata detections) →
  **Grafana**, over the **Loki** datasource.

The Suricata + capture-health + attack-coverage dashboards read telemetry stores directly (the
capture appliance and network sensor are appliances; Loki/Prometheus are network-fed). A
**Deploy / op status** dashboard originally read `state.db` directly, then the Phase-2
control-plane API over the **Infinity** datasource (the **D2 fold-in**, CHANGELOG 2026-06-23);
it and its SQLite fallback datasource were **deleted 2026-10-01** (see **Status** below) — the
Infinity datasource (`axr-api`) and `AXRANGE_API_URL`/`AXRANGE_API_TOKEN` stay provisioned as a
telemetry source for a future panel, even though no kept dashboard uses it today.

> **Status, 2026-10-01: stood up and live, degraded; control-panel plugin removed.** The Grafana +
> Loki + Prometheus stack is running with the capture-health, Suricata-alerts and attack-coverage
> dashboards live-fed (`docs/reference/ui-consolidation/03-ui-history-and-decisions.md` row 2). The
> standing Suricata sensor has been silently dead since 2026-07-27 (GAP-067), so the
> Suricata-alerts dashboard has had no feed since — that gap is tracked, not this stack's bring-up.
> The deploy/op-status and pcap-browser dashboards, the `control-console.json` dashboard, the
> `axellio-axrange-control-panel` plugin, and the `topology-eval/` bake-off dashboards — all
> deprecated 2026-09-29 — were **deleted 2026-10-01** (user-approved deletion list,
> `docs/reference/ui-consolidation/05-deletion-candidates.md`). The operator UI is the axRange
> Console (`docs/design/16-unified-console.md`).

---

## Data flow

```
                        ┌───────────────────────────────┐
 NETWORK SENSOR VM      │ CAPTURE HOST                   │
 (adapter NETWORK_      │ (o2/tcpdump-sensor, mgmt NIC)  │
  SENSOR_VMID, 8001)    │                                │
                        │                                │
 Suricata               │ capture-healthcheck            │
   └─ /var/log/         │ └─ capture-health-exporter.sh  │
      suricata/         │        └─ axrange_capture      │
      eve.json          │           .prom (textfile)     │
        │               │              │                 │
   Grafana Alloy        │        node_exporter            │
   (loki.source.file    │        (:9100 /metrics)         │
    → loki.process      │              │                  │
    → loki.write)       │              │                  │
        │               │              │                  │
        ▼ push          │              ▼ scrape           │
   ┌─────────┐          │         ┌────────────┐          │
   │  Loki   │◄─────────┴─────────│ Prometheus │          │
   │  :3100  │                    │  :9090     │          │
   └────┬────┘                    └─────┬──────┘          │
        │                               │                 │
        └───────────────┬───────────────┘                 │
                        ▼                                   │
                 ┌──────────────┐   provisioned datasources + 3 dashboards
                 │ Grafana OSS  │   1. Suricata alerts  (Loki)      ← marquee
                 │  :3000       │   2. Capture health (Prometheus)
                 └──────────────┘   3. Attack coverage (Loki)
       (docker-compose: grafana + loki + prometheus run on the DEPLOY HOST)
```

The three stack services (Grafana, Loki, Prometheus) run together via
docker-compose on one **deploy host**. The two shippers (**Alloy** on the network sensor,
the **textfile exporter** on the capture host) run *on their respective source hosts* and
reach into the stack — Alloy *pushes* to Loki; Prometheus *scrapes* the capture host's
node_exporter. A fourth datasource, **Infinity** (`axr-api`, pointed at the axRange control-plane
API), stays provisioned for a future panel but no kept dashboard reads it today (see **Status**
above).

---

## Decisions (Phase 1)

These were settled before the build; they are implemented here and recorded in
`docs/CHANGELOG.md` (2026-06-21).

1. **Stack via docker-compose: Grafana OSS + Loki + Prometheus, pinned image versions.**
   Docker is acceptable per `CLAUDE.md` ("No exotic kernels…" — Docker is not a kernel
   dependency). Versions are pinned in `observability.conf` (`*_IMAGE` keys); never `:latest`.
2. **Log shipper = Grafana Alloy** on the network sensor, tailing
   `/var/log/suricata/eve.json`, parsing JSON, pushing to Loki with labels
   `event_type` / `severity` (`alert.severity`) / `signature` (`alert.signature`) /
   `src_ip` / `dest_ip` / `proto`. **Promtail is the documented simpler fallback**
   (see "Log shipper" below).
3. **Health → Prometheus via a node_exporter textfile exporter** on the capture host
   (`exporters/capture-health-exporter.sh`) wrapping `capture-healthcheck`. Metrics:
   `capture_up`, `newest_pcap_age_seconds`, `rx_drops_total`, `disk_pct`, `promisc_on`.
   It **complements `o2/tcpdump-sensor/`** (does not modify it) — the tcpdump-sensor README
   already floated a Prometheus textfile exporter; this is it.
4. **Grafana datasources are provisioned:** Loki, Prometheus, and the **axRange API** (Infinity,
   `yesoreyeram-infinity-datasource`, pointed at `AXRANGE_API_URL` — originally the **D2 fold-in**
   source for deploy/op). **Dashboards are provisioned as JSON:** (1) *Suricata alerts* — the
   marquee (alerts over time, top-signatures table, by-severity, a live `eve.json` logs
   panel); (2) *Capture health*; (3) *Attack coverage*. A fourth dashboard, *Deploy / op status*
   (deploys list + by-status + latest-deploy op tree + failed ops, from `GET /api/deploys` /
   `/api/deploys/latest`), and its co-located SQLite fallback datasource were **deleted 2026-10-01**
   (`docs/reference/ui-consolidation/05-deletion-candidates.md`) — superseded by the axRange
   Console's Range/Twin areas. The Infinity datasource stays provisioned as a telemetry source for
   a future panel.
5. **Deploy host is flexible:** since every kept dashboard reads a network-fed store (Loki push,
   Prometheus scrape), the stack runs on the orchestrator **or** a separate worker VM.
   **All targets are parameterized** in `observability.conf`. See **Host options**.
6. **pcap browse/download is DEFERRED to Phase 2** (the API). Not built here — see
   **Deferred**.

---

## What's here

```
o2/
├── README.md                          ← this file
├── observability.conf.example         ← single source of truth for all tunables
├── compose/
│   └── docker-compose.yml             ← Grafana + Loki + Prometheus + Caldera + Wazuh manager
├── caldera/
│   └── Dockerfile                     ← builds Caldera from a pinned git tag (no Hub image)
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/datasources.yml  ← Loki + Prometheus + axRange API (Infinity)
│   │   └── dashboards/dashboards.yml    ← dashboard provider (loads the JSON)
│   └── dashboards/
│       ├── suricata-alerts.json         ← (1) marquee
│       ├── capture-health.json          ← (2)
│       └── attack-coverage.json         ← (3)
├── loki/
│   └── loki-config.yml                ← single-binary filesystem Loki
├── prometheus/
│   └── prometheus.yml                 ← scrapes the capture host's node_exporter (placeholder target)
├── alloy/
│   └── config.alloy                   ← eve.json → Loki (runs on the network sensor)
├── exporters/
│   └── capture-health-exporter.sh     ← capture-healthcheck → textfile (runs on the capture host)
└── systemd/
    ├── alloy-axrange.service                    ← Alloy unit (network sensor)
    ├── axrange-capture-health-exporter.service  ← exporter oneshot (capture host)
    └── axrange-capture-health-exporter.timer    ← fires it every 30s (capture host)
```

Everything tunable lives in `observability.conf` (config-over-flags, same rule as
`o2/tcpdump-sensor/capture.conf`). `observability.conf` is gitignored; `observability.conf.example`
is the reference and stays in git.

---

## Host options — host-agnostic

All three kept dashboards (Suricata alerts, capture health, attack coverage) read Loki/Prometheus,
which are network-fed (Alloy push, node_exporter scrape) — there is no `state.db`-location
constraint on where this stack runs.

- **Orchestrator (simplest):** everything the stack needs is reachable without extra network
  config.
- **A separate worker VM:** also fully supported — point `CAPTURE_HOST`/`NETWORK_SENSOR_HOST` at
  their reachable addresses in `observability.conf`. This is the long-term production shape
  (`docs/design/05-o2-surface.md`).

`DEPLOY_HOST` in `observability.conf` just records your choice; nothing reads it programmatically.
If a future panel is wired to the still-provisioned **axRange API** (Infinity, `axr-api`) datasource,
set `AXRANGE_API_URL` to the API's address as reachable from the Grafana container (see
`observability.conf.example`'s "Control-plane API" section).

---

## Log shipper: Alloy (Promtail fallback)

**Chosen: Grafana Alloy** (`alloy/config.alloy`) — Grafana's current, supported
telemetry collector; `loki.source.file` → `loki.process` (JSON parse + label
promotion) → `loki.write`.

**Fallback: Promtail** — simpler if Alloy isn't available on the network sensor. The
equivalent Promtail `scrape_configs` job:

```yaml
# /etc/promtail/config.yml (fallback — not the chosen path)
clients:
  - url: http://<DEPLOY_HOST>:3100/loki/api/v1/push
scrape_configs:
  - job_name: suricata
    static_configs:
      - targets: [localhost]
        labels:
          job: suricata
          host: <NETWORK_SENSOR_HOST>
          __path__: /var/log/suricata/eve.json
    pipeline_stages:
      - json:
          expressions:
            event_type: event_type
            severity: 'alert.severity'
            signature: 'alert.signature'
            src_ip: src_ip
            dest_ip: dest_ip
            proto: proto
            ts: timestamp
      - labels:
          event_type:
          severity:
          signature:
          src_ip:
          dest_ip:
          proto:
      - timestamp:
          source: ts
          format: RFC3339Nano
```

Same labels, same Loki target. Promtail is in maintenance mode upstream, which is why
Alloy is the default; either works.

---

## Bring-up runbook (parameterized — human-gated, does NOT auto-run)

> Run these **only** when you intend to stand the stack up. Each step is explicit;
> nothing here is executed by the component itself.

### 0. Prerequisites

- **Deploy host:** Docker Engine + the compose plugin (`docker compose version`).
- **Capture host:** the `o2/tcpdump-sensor/` component installed (`capture-healthcheck` present),
  plus `node_exporter`.
- **Network sensor:** Suricata writing `eve.json` (provisioned per
  `docs/SLICE_OVERLAY.md` §4b), plus Grafana Alloy installed.

### 1. Configure

```bash
cd observability
cp observability.conf.example observability.conf
$EDITOR observability.conf      # set ports, GRAFANA_ADMIN_PASSWORD, CAPTURE_HOST,
                                # NETWORK_SENSOR_HOST, LOKI_PUSH_URL, …
```

### 2. Render the Prometheus scrape target

Prometheus does not interpolate env vars inside `static_configs`, so render the
capture-host target once from the conf:

```bash
. ./observability.conf
sed -i -e "s/__CAPTURE_HOST__/${CAPTURE_HOST}/" \
       -e "s/__CAPTURE_NODE_EXPORTER_PORT__/${CAPTURE_NODE_EXPORTER_PORT}/" \
       prometheus/prometheus.yml
```

### 3. Start the control-plane API (OPTIONAL — no kept dashboard needs it)

The *Deploy / op status* dashboard that read the API over the Infinity datasource (the D2
fold-in) was deleted 2026-10-01; none of the three kept dashboards use the API. Skip this step
unless you're standing up a new panel against it. If you do, start `axrange serve` on the
orchestrator (where `state.db` lives), bound so Grafana can reach it:

```bash
pip install 'axrange[api]'      # fastapi + uvicorn (one-time)
axrange serve --host 0.0.0.0 --port 8080 \
    --db <state.db> --scenarios-dir <scenarios> --adapter-config adapter.conf
# verify:  curl -s http://<ORCH>:8080/api/deploys | head
```

Set `AXRANGE_API_URL` in `observability.conf` to the address Grafana reaches this on
(`http://host.docker.internal:8080` if co-located on the docker host, else the orchestrator's
LAN IP). If you pass `--api-token`, set `AXRANGE_API_TOKEN` to the same value. (Reads are open
on a trusted bind, so the token is usually unset.)

### 4. Stand up the stack (on the DEPLOY HOST)

```bash
# Caldera has no published image -- build it locally once (or let `up -d` build it):
docker compose --env-file ./observability.conf -f compose/docker-compose.yml build caldera
docker compose --env-file ./observability.conf -f compose/docker-compose.yml up -d
docker compose --env-file ./observability.conf -f compose/docker-compose.yml ps
```

Grafana is then at `http://<DEPLOY_HOST>:${GRAFANA_PORT}` (default `:3000`); the three
datasources (Loki, Prometheus, axRange API) and the three dashboards (Suricata alerts, capture
health, attack coverage) are already provisioned and need no API step 3 above. Caldera's UI is
at `http://<DEPLOY_HOST>:${CALDERA_UI_PORT}` (default `:8888`); the Wazuh manager API is at
`https://<DEPLOY_HOST>:${WAZUH_API_PORT}` (default `:55000`, self-signed by default). See
"Attack/defense servers (A3 Stage B)" above for how TE-side agents point at these two.

### 5. Capture health exporter (on the CAPTURE HOST)

```bash
sudo install -m 0755 exporters/capture-health-exporter.sh /usr/local/bin/capture-health-exporter
sudo install -d -m 0755 /etc/axrange
sudo cp observability.conf /etc/axrange/observability.conf   # or hand-edit a subset
sudo cp systemd/axrange-capture-health-exporter.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now axrange-capture-health-exporter.timer
# node_exporter must read the textfile dir:
#   node_exporter --collector.textfile.directory=${CAPTURE_TEXTFILE_DIR}
capture-health-exporter --stdout          # eyeball the metrics
```

### 6. Alloy log shipper (on the NETWORK SENSOR)

See `systemd/alloy-axrange.service` header for the exact steps. In short: install Alloy,
drop `alloy/config.alloy` at `/etc/alloy/config.alloy`, write `/etc/alloy/axrange.env`
(`EVE_JSON_PATH`, `LOKI_PUSH_URL`, `NETWORK_SENSOR_HOST` from the conf), enable the unit.

### 7. Verify end-to-end

- Prometheus → Status → Targets: `capture-health` is **UP**.
- Loki: in Grafana Explore, `{job="suricata"}` returns lines (fire an attack so Suricata
  alerts — `docs/SLICE_OVERLAY.md` §4b live-verify proved attack traffic → capture → network sensor →
  alerts).
- Grafana dashboards 1–3 (Suricata alerts, capture health, attack coverage) populate.
- (Optional, step 3 only) API: `curl http://<ORCH>:8080/api/deploys` returns your deploys over
  the **axRange API** Infinity datasource — useful if you're standing up a new panel against it,
  since no kept dashboard reads it.

---

## Healthcheck / validation path

Per the project convention ("Health/metrics are first-class; every long-running component
exposes a `--healthcheck` mode"):

- **Exporter:** `capture-health-exporter.sh --healthcheck` (exit 0/1) — confirms it can read
  the capture config and produce a metrics block; `--stdout` prints them.
- **Stack:** each compose service has a Docker `healthcheck` (Grafana `/api/health`,
  Loki `/ready`, Prometheus `/-/healthy`).
- **Alloy:** `systemctl is-active alloy-axrange` + `curl -s localhost:12345/-/ready`.
- **Config validation (offline):** see **Validation** below.

---

## Validation (what was checked at author time)

Run from the repo root. **Historical note:** several rows below validate the *Deploy / op status*
dashboard and its D2 API data path; that dashboard was deleted 2026-10-01 (**Status** above) — kept
verbatim as the author-time record, not current guidance.

| Check | Command | Result |
|---|---|---|
| docker-compose schema | `docker compose -f o2/compose/docker-compose.yml config -q` | **untested here** — `docker`/compose not installed on the author box. Run on the deploy host. |
| Prometheus config | `promtool check config o2/prometheus/prometheus.yml` | **untested here** — `promtool` not installed. (Ships with Prometheus; run on the deploy host or `docker run --rm -v $PWD:/c prom/prometheus promtool check config /c/o2/prometheus/prometheus.yml`.) |
| Alloy config | `alloy validate o2/alloy/config.alloy` | **untested here** — `alloy` not installed. |
| Dashboard JSON | `python3 -m json.tool <each>.json` | **PASS** — all three dashboards are valid JSON. |
| Datasource UID cross-check | dashboard refs vs. provisioned uids | **PASS** — deploy/op dashboard references only `axr-api` (Infinity), which is provisioned. |
| Exporter shell syntax | `bash -n o2/exporters/capture-health-exporter.sh` | **PASS** |
| **Deploy/op API data path (D2)** | boot `axrange serve` against a seeded `state.db`; curl the endpoints the Infinity dashboard reads | **PASS (live)** — see below. |
| Compose schema (pyyaml, no docker needed) | `tests/test_o2_compose.py` | **PASS** — parses `compose/docker-compose.yml`, asserts `caldera`/`wazuh-manager` (+ the pre-existing three) each declare `image`/`build`, `ports`, and a `healthcheck`, and that every `${VAR}` the compose file references is documented in `observability.conf.example` (A3 Stage B addition). |
| Caldera Dockerfile syntax | eyeballed, not `docker build`-verified (no network egress on the author box) | **untested here** — build it at live bring-up (`docker compose build caldera`); see the Dockerfile header for the pinned-tag + `--insecure` rationale. |

**D2 live test (run at author time).** Docker isn't on the author box, so the Grafana *render*
is human-gated — but the **API data path the dashboard now depends on was live-tested**: a real
`state.db` was seeded by the real planner + executor (fake adapter) with one **completed** deploy
(`slice-demo`, 26 ops) and one **failed** deploy (`app-demo`, failed `clone-vm` + 9 halted), then
`axrange serve` was booted against it and the exact dashboard queries were curled:

- `GET /api/deploys` → `.deploys[]` with `by-status {failed:1, completed:1}` (panels 1/2/4).
- `GET /api/deploys/latest` → `.counts {completed:5, halted-due-to-upstream:9, failed:1}`,
  `.ops[]` (15), the failed `clone-vm` (OP-HOST-010) surfaced for the failed-ops panel, and the
  per-attempt `error_class`/`error_message` available for drill-down (panels 3/5/6).
- `GET /api/deploys/{id}` → per-deploy scoping confirmed (the completed deploy reports
  `{completed:26}`, distinct from the failed one).

Every JSONPath the dashboard references (`deploys`, `status`, epoch-second `started_at`/
`completed_at`, `ops`, `op_name`/`op_catalog_id`/`kind`/`status`, the `failed` filter) resolves
against the live API.

**Still untested until full bring-up (Grafana render half):** `docker compose config -q`,
`promtool check config`, `alloy validate` (none of `docker`/`promtool`/`alloy` on the author
box), Grafana actually rendering the Infinity queries (needs the container + the plugin), and
the Loki/Prometheus live paths (push/scrape).

---

## Deferred (not built in Phase 1)

- **pcap browse / download** → **Phase 2 (the API)** (`docs/design/11-api-ui.md`).
  A Grafana table over a pcap index + a data-link to `/api/pcaps/{id}` (or a tiny
  read-only appliance-side server). Deliberately not added here — it would put a read-only
  surface on the tcpdump-sensor's intentionally-minimal contract; that's a Phase-2 boundary decision.
- **Live bring-up** — human-gated; this component only authors + validates configs.
- **Deploy/op dashboard via the API — DONE then DELETED (D2 fold-in, CHANGELOG 2026-06-23; deletion,
  CHANGELOG 2026-10-01).** The dashboard read the Phase-2 control-plane API
  (`GET /api/deploys`, `/api/deploys/latest`) over the Infinity datasource — host-agnostic, no
  local `state.db` needed (`docs/design/05-o2-surface.md`); its API data path was live-verified
  (see **Validation**). The dashboard and its SQLite fallback datasource were deleted 2026-10-01,
  superseded by the axRange Console's Range/Twin areas; the Infinity datasource stays provisioned
  as a telemetry source for a future panel. (Not deferred — listed here for the record.)
- **Grafana write-back / control buttons** — Phase 1 is read-only telemetry. Control
  (validate/deploy/destroy) is the ported shell + API (UI brief §5).
- **Per-scenario alert attribution** — capture/sensor telemetry is merged (no scenario
  correlation in v1; `o2/tcpdump-sensor/README.md`). Filtering is by Suricata labels (src/dst/sig), not
  by scenario.
- **Wazuh indexer + dashboard** — the OpenSearch-backed Wazuh UI/search plane. Deliberately
  not built here: `wazuh-manager` alone is sufficient for agents to enroll + ship events, and
  the indexer+dashboard pair is a materially heavier addition (OpenSearch memory/disk
  requirements, TLS cert plumbing between the three Wazuh containers) that the current scope
  (agents check in, an operator reads via the manager API or Grafana) does not need. Add it as
  its own follow-on if a Wazuh-native UI becomes worth the weight.
- **Wazuh alerts -> Loki** — Suricata's `eve.json` already ships to Loki via Alloy (above); a
  symmetric Wazuh-alerts shipper (tailing the manager's `alerts.json`, same Alloy
  `loki.source.file` pattern) is a natural follow-on but not added here — A3 Stage B's scope is
  standing the two servers up, not building every telemetry pipe. Measurement/scoring off Wazuh
  detections is A4 (`docs/design/08-exercise-measurement.md`).
- **Live server-address resolution** — `overlays.adversary[].server` and the Wazuh agent's
  `config:` manager address are explicit IR fields; there is no live lookup against this
  stack's `DEPLOY_HOST`/ports yet (tracked in `axrange/planner/pass1_emit.py`'s adversary-
  overlay emission, and A1/A4 in `docs/design/08-exercise-measurement.md`). See "Attack/
  defense servers (A3 Stage B)" above for the current manual procedure.

---

## Single-pane control panel (UI Phase 4) — REMOVED 2026-10-01

> **Deprecated 2026-09-29, deleted 2026-10-01** (`docs/design/16-unified-console.md`;
> `docs/CHANGELOG.md` 2026-09-29 and 2026-10-01; Authority: user, approved the deletion list —
> `docs/reference/ui-consolidation/05-deletion-candidates.md`). Replaced by the axRange Console's
> Catalog (guarded deploy/destroy) and Twin (deploy tree, node/host detail, live status) areas.
> `o2/grafana/plugins/axellio-axrange-control-panel/` (the plugin source), `build-plugin.sh`,
> `gen-topology-dashboards.py`, `control-console.json`, `deploy-op-status.json`,
> `pcap-browser.json`, and `topology-eval/` no longer exist in this repo. See the git history on
> `wip/unified-console` for the build/run instructions this section used to carry.

---

## Attack/defense servers (A3 Stage B)

**Status: AUTHORED + CONFIG-VALIDATED. NOT stood up.** Same posture as the rest of this stack.

Per `docs/design/08-exercise-measurement.md`, each TE node carries three default agents;
two of them (Sandcat, the Wazuh agent) beacon to a matching **O2 server**. Those two servers —
**Caldera** and the **Wazuh manager** — are now services in `compose/docker-compose.yml`,
alongside Grafana/Loki/Prometheus:

| Server | Service name | Port(s) | Agent that reports to it |
|---|---|---|---|
| Caldera | `caldera` | `8888` (UI + HTTP contact channel), `7010`/`7011` (optional TCP/UDP contact) | Sandcat (`overlays.adversary`, `render_sandcat_agent`) |
| Wazuh manager | `wazuh-manager` | `1514/tcp` (events), `1515` (enrollment), `55000` (API) | Wazuh agent (`overlays.defense`, `install-defense-agent`'s `wazuh-agent` tool) |

Caldera has no published Docker Hub image; `caldera/Dockerfile` builds it locally from a
pinned git tag (`CALDERA_VERSION`) — see that file's header, including the **load-bearing**
`--insecure` (plain-HTTP) requirement matching `render_sandcat_agent`'s `http://` origin. The
Wazuh service is **manager-only** (no indexer/dashboard) — deliberately minimal: the substrate
only needs something for the agent to enroll to and ship events to; the full Wazuh
indexer+dashboard stack (OpenSearch-backed, materially heavier) is a documented follow-on if a
Wazuh-native UI is ever wanted (see **Deferred**).

**Manual playout only.** Nothing in this repo drives a Caldera ability or tunes a Wazuh rule —
an operator does that at the Caldera console / Wazuh manager, per
`docs/design/08-exercise-measurement.md`. No engine or `o2control` code calls into either
server.

### How this relates to `axrange o2 up`

**Two separate mechanisms, deliberately kept apart** (per `docs/CONVENTIONS.md`'s reuse-downward
rule and `CLAUDE.md`'s "no leaking platform details up the stack" — an engine deploy must never
shell out to `docker compose`):

- **`axrange o2 up`** (`axrange/o2control/`) stands up the O2 **sensor VMs** (`tcpdump-sensor` /
  `suricata-sensor` / `zeek-sensor`) — it composes a small scenario IR and runs it through the
  *engine* (planner + Proxmox adapter), the same machinery a TE deploy uses, tagged `tier=o2`.
  It has no concept of docker or host-side services.
- **This compose stack** (Grafana/Loki/Prometheus/Caldera/Wazuh) is **host-side infrastructure**,
  brought up by its own existing mechanism — the runbook below (`docker compose up -d`). It has
  always been human-gated (see "Status" throughout this file); Caldera/Wazuh join it on the same
  terms.

An operator doing a full O2 stand-up runs **both**: `axrange o2 up` for the sensor VMs, and
`docker compose ... up -d` (this stack) for the servers. There is no single combined command in
v1; if that friction becomes real, unify it as a documented **operator runbook**, not by making
the engine invoke `docker compose` (that would violate the boundary above) — see the A3 brief's
open item "how O2 control provisions the three servers... folds into the A1 surface spec"
(`docs/design/08-exercise-measurement.md`), still open.

### Pointing agents at the servers (address resolution)

Both `overlays.adversary[].server` (Caldera) and `overlays.defense`'s Wazuh `config:` (the
`ossec.conf` content, which carries the manager address) are **explicit IR fields today** — the
planner has no live O2-inventory lookup (`axrange/planner/pass1_emit.py`'s adversary-overlay
emission raises a loud `EmissionError` if `server:` is missing, by design; see that module's
comment for the tracked TODO). Until that live resolution lands (A1/A4 territory, not this
stage), an operator:

1. Stands this stack up on a host reachable from the TE nodes (`DEPLOY_HOST` above — the same
   reachability requirement the rest of this stack already has for Loki/the API; TE nodes behind
   a `kind: router` egress reach it the same way they reach any WAN service).
2. Sets `overlays.adversary[].server:` to **either** a bare host (`"<DEPLOY_HOST>"`, e.g.
   `"10.0.0.5"`) **or** a full URL (`"http://<DEPLOY_HOST>:${CALDERA_UI_PORT}"`). **Never**
   `"<DEPLOY_HOST>:${CALDERA_UI_PORT}"` (bare `host:port`, no scheme) — `render_sandcat_agent`
   (`axrange/config_exec/render.py`) does `origin = server if "://" in server else
   f"http://{server}:8888"`: a bare host gets `:8888` appended for you (so
   `"10.0.0.5"` → `http://10.0.0.5:8888`, correct), but a bare `host:8888` has no `://`,
   so it becomes `http://10.0.0.5:8888:8888` — the Sandcat fetch fails, and since that
   failure is only a WARNING (not an op failure, by design — the O2 server may not be
   up yet), the **deploy still reports COMPLETED with no agent ever checking in**. If
   Caldera isn't on the default `8888`, use the full-URL form, not host:port.
3. Points the Wazuh agent's `config:` (`ossec.conf`) at `<DEPLOY_HOST>:${WAZUH_AGENT_PORT}`
   (events, `1514/tcp`) / `<DEPLOY_HOST>:${WAZUH_ENROLL_PORT}` (enrollment, `1515/tcp`) —
   the same host, both TCP.

`DEPLOY_HOST` is already the single conf-file source of truth for "where this stack lives"; the
new `CALDERA_*`/`WAZUH_*` port keys in `observability.conf.example` are its Caldera/Wazuh-specific
companions to `GRAFANA_PORT`/`LOKI_PORT`/`PROM_PORT` above.

**Wazuh version compatibility.** The wazuh-agent installed on a TE node (`install-defense-agent`'s
`wazuh-agent` branch, `axrange/config_exec/render.py`) must be **4.9.x, and no newer a minor than
the manager** (`WAZUH_MANAGER_IMAGE=wazuh/wazuh-manager:4.9.2` above — Wazuh requires
agent-version <= manager-version). The agent binary itself is **not pinned by this stack** — it's
installed operator-side (an apt/package step outside this compose file's scope, matching the
"bake fragile external tools, don't blind-apt in an isolated guest" posture elsewhere in this
repo); mismatch it against a newer manager and enrollment/event shipping breaks silently.

## Boundaries (what this component does NOT touch)

- Does **not** modify anything under `axrange/` — it only consumes the sensor/capture outputs
  that already exist, plus (optionally, for a future panel) reads the control-plane API over
  HTTP; there is no `state.db` bind-mount into Grafana anymore.
- Does **not** edit `o2/tcpdump-sensor/` — the exporter **complements** it (the tcpdump-sensor
  README floated exactly this exporter).
- Does **not** build the Phase-2 API (`docs/design/11-api-ui.md`) or the ported frontend.
- Same "no leaking platform details up the stack" rule: Grafana/Loki/Prometheus consume
  telemetry stores; they don't reach engine internals.
