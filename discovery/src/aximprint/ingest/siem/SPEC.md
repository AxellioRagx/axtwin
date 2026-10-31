# SIEM ingest plugins

## What it is

A family of `http` discovery sources that pull connection-level records out
of a SIEM or log platform over its search API: Splunk, Microsoft Sentinel,
Elastic (which also serves OpenSearch, the Wazuh indexer and Graylog's
store), then IBM QRadar, with Google SecOps and Datadog once a multi-key
auth mode exists. They serve the **discover** step of the loop for sites
that have no sensor aximprint can reach but already ship their network
telemetry (Zeek via Corelight or Filebeat, Suricata, firewall traffic logs,
NetFlow) into a SIEM.

Each plugin is axquery's shape with a vendor leaf fetcher. One package,
`ingest/siem/`, holds the shared scaffolding (`http.py`: the locator parser,
env-to-header auth, time-bisected paging lifted from `axquery/sources.py`,
leaf coverage to `SensorHealthEvent`) and one module per vendor.

## Seam

`IngestPlugin`, one registration per vendor (`splunk`, `sentinel`,
`elastic`, `qradar`, …), all `source_kind = "http"`, so each is a stored
source the console can create and probe with no UI change while its auth is
`basic` or `api_key`.

Shared across the family:

| Aspect | Rule |
|---|---|
| locator | `<name>:<absolute http(s) URL>[?<vendor options>]`; a bare host is refused — no scheme is guessed |
| credentials | only `AXIMPRINT_SOURCE_USER` / `_PASSWORD` / `_API_KEY`; never `argv`, never the locator; the plugin translates them into the vendor header (`Bearer`, `ApiKey`, `SEC:`, a client-credentials token exchange) |
| transport | stdlib `urllib`; `requests` stays in the optional `[acquire]` extra |
| `probe` | returns the data span (`first_ts`, `last_ts`), `reachable`, the indexes/tables/datasets the plugin will read under `extra`, so the control plane turns "days to read" into bounds |
| `events` | honours `[ts_from, ts_to)`; an unbounded call resolves bounds from the probe span |
| paging | time-bisected leaves: split an interval when a page fills, down to a one-second floor, then widen the leaf's row limit; emit only rows whose own start falls in the leaf — the same discipline under every vendor's cap |
| default pull | raw per-flow rows; a server-side aggregate mode is an explicit, labelled fallback that sets `counters_imputed` and emits a `SensorHealthEvent` naming what cadence, size and duration features it silences |
| declines | alert and incident tables are named into `unparsed_stems`, never mapped (`AlertEvent` has no second producer yet) |
| `local_*`, `vlan_*` | `None` — the sensor said nothing; segments impute locality as they do for axquery |
| `state_class` | Zeek-sourced rows keep `conn_state` and reuse the Zeek table; firewall rows map allowed-with-return-bytes → `established`, allowed-with-none → `attempted`, blocked → `unknown` (a drop proves nothing about the host); unpaired NetFlow/IPFIX → `unknown`, `counters_imputed` when per-direction counters are missing |
| Zeek-fed rows | recognised by dataset or sourcetype (`zeek.*`, Corelight add-on, the Corelight ASIM parser) and routed through the Zeek field mapping, so a Zeek-fed SIEM yields the Zeek reader's fidelity |

Per vendor:

| Plugin | Locator options | Env → auth | Query per leaf | Paging | Flow fields | Identity / Service |
|---|---|---|---|---|---|---|
| `splunk` | `index`, `datamodel` | USER/PASSWORD → Basic; `_API_KEY` → Bearer | SPL over `tag=network tag=communicate` with `earliest`/`latest`, or `tstats` from `Network_Traffic` | `search/v2/jobs/export`, streamed, no result cap | CIM `src_ip, src_port, dest_ip, dest_port, transport, app, bytes_in/out, packets_in/out, duration, _time` | `Network_Resolution` → `dns`; Corelight sourcetypes → identity kinds; `Web`, `Certificates` → `http`, `ssl` |
| `sentinel` | `tenant`, workspace in the path | USER = client id, PASSWORD = secret → client-credentials token, cached in the plugin | KQL `_Im_NetworkSession(starttime, endtime)` | single-shot per leaf under the row cap; respects the per-user concurrency and per-30-s limits | ASIM `SrcIpAddr, SrcPortNumber, DstIpAddr, DstPortNumber, NetworkProtocol, NetworkApplicationProtocol, SrcBytes, DstBytes, SrcPackets, DstPackets, EventStartTime, NetworkDuration` | `_Im_Dns` → `dns`; `_Im_WebSession` → `http` |
| `elastic` | `index` pattern | USER/PASSWORD → Basic; `_API_KEY` → `ApiKey` | DSL range on `@timestamp` with `event.category: network`, or ES\|QL | point-in-time + `search_after`; never `from`+`size` | ECS `source.*`, `destination.*`, `network.transport/protocol`, `event.duration`, `@timestamp` | `dns.*`, `tls.*`, `http.*`; `zeek.*` datasets natively |
| `qradar` | `version` | `_API_KEY` → `SEC:`; USER/PASSWORD → Basic | AQL over `flows` with `START`/`STOP` | Ariel search, poll, `Range` paging; patient on busy codes | `sourceIP, sourcePort, destinationIP, destinationPort, protocolId, applicationId, sourceBytes, destinationBytes, sourcePackets, destinationPackets, firstPacketTime, lastPacketTime` | `events` DNS/DHCP by QID, per site |
| `secops`, `datadog` | — | service-account JSON / two keys → **need a multi-key auth mode** in the stored-source model and the console | UDM / logs search | async search / cursor | UDM `principal.*`, `target.*`, `network.*` / Datadog attributes | later |

Order of build: `splunk` (both auth modes exist; export streams; Corelight
add-on reproduces Zeek fidelity), `elastic` (three platforms for one),
`sentinel` (token exchange inside the plugin), `qradar`.

## Config

No configuration beyond the locator and the three environment variables;
`config_schema` stays the closed shared schema. The control plane's stored
source carries the endpoint, transport (`direct` or `ssh_tunnel`) and auth
mode; a pull is long, and runs under the existing discovery timeout. There
is no checkpoint channel in the contract: leaves stay small enough that a
restarted pull is tolerable rather than inventing a cursor outside the
contract.

## Acceptance

- **Automated:** per vendor, `tests/test_<name>_ingest.py` stubs
  `urllib.request.urlopen` the way `test_axquery_ingest.py` does, with
  recorded vendor responses: listing with the closed schema; locator refusal
  of a bare host; env-only credentials (a locator or argv credential is
  refused); the auth header per env combination; a filled page splits the
  leaf; a floor-width leaf widens; `state_class` derivation per source
  family, including a Zeek-fed row keeping its `conn_state`; alert tables
  named in `unparsed_stems`; the aggregate fallback emits its health event;
  axquery's own tests unchanged after the lift into `http.py`.
- **Live:** a Corelight-fed Splunk index and a Filebeat-fed Elastic index
  over the same site yield casts whose roles and segments match the Zeek
  reader's cast of that site's logs.

## Notes

- Asset and CMDB sources (NetBox, ServiceNow) are snapshot-shaped and
  credential-free by contract; they belong under the reference registry as
  an export-file reader, not here.
- Kafka, Zeek's WebSocket event API and Broker are not transports for this
  family or any plugin: no `source_kind` admits them, and the latter two
  carry events, not logs.
- The two auth modes beyond `basic` and `api_key` are a control-plane and
  console change and a separate sign-off.
- Behaviour notes from the review of the first build. Time bounds reach a second-granular
  server as `floor(lo)`/`ceil(hi)` and a whole-second window is split on a whole second, so
  no fractional edge drops rows; a leaf's kept rows are sorted by start before they reach the
  merge (Splunk answers newest-first). A Zeek row's string `ts` (Corelight and JSON shippers
  write ISO-8601) is converted to epoch in `rows.zeek_events`, falling back to the leaf's
  start, never dropped. axquery now refuses a locator carrying `?` options or userinfo, as
  the shared parser does for every vendor. The Splunk probe span is all events per index; the
  Elastic one is filtered to `event.category: network`.
