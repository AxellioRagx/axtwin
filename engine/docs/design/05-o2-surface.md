*O2, the standing Observability-&-Orchestration surface layer: the watch/act/steer planes, the
`o2control` thin client, and the `o2/` folder layout.*

## What O2 is

The standalone engine (see [03-engine.md](./03-engine.md)) stands up exactly what a twin's
Scenario IR describes — its hosts, networks, and routers — and nothing else. Everything an
operator needs *around* a twin to observe it, drive it, and steer the whole range is pooled into
a separate, shared component: **O2**.

O2 is stood up once, independent of any twin, and shared across every twin an operator deploys
and destroys over time. A twin's own deploy never creates telemetry storage, attack/defense
servers, or a console — it plugs into O2's standing surface instead. This is the Lab tier (`o2`)
in the five-tier twin model; see [02-twin-model.md](./02-twin-model.md) for how it sits alongside
Twin/Traffic/Tap/Exercise and how its resources are protected from every other tier's teardown.

For the design reasoning trail behind this split, see `docs/CHANGELOG.md` and
`docs/design/README.md`'s retired-brief mapping table.

## Three functional planes

O2's components sort into three functional planes — **watch**, **act**, and **steer**:

```
┌─────────────────────────────── O2 surface ───────────────────────────────┐
│                                                                            │
│  WATCH (observability)        ACT (orchestration)       STEER (console)   │
│  ────────────────────         ────────────────────      ─────────────────│
│  network sensors               Caldera (+ Sandcat)        axRange Console │
│  (tcpdump/suricata/zeek)        — attack                  + control-plane │
│  external packetXpress sinks   Wazuh manager               API + CLI      │
│  host agents (Wazuh agent,      — defense/detection       (Grafana:       │
│  Prometheus exporter, audit)   o2control — surface          telemetry     │
│  activity truth markers          stand-up + tap/traffic/    only, linked) │
│       │                          exercise playout                        │
│       ▼                                                                   │
│  Loki (logs/detections/truth)                                             │
│  Prometheus (metrics)                                                     │
└────────────────────────────────────────────────────────────────────────────┘
```

- **Watch (observability).** Collectors feed pure telemetry stores. Collectors are network
  sensors and external sinks (see [06-taps-sensors.md](./06-taps-sensors.md)), host agents
  (a Wazuh agent, a Prometheus exporter, OS audit logging), and the truth markers an exercise run
  emits (see [08-exercise-measurement.md](./08-exercise-measurement.md)). Everything a collector
  produces lands in **Loki** (logs, detections, truth markers) or **Prometheus** (metrics) — the
  only two components in O2 that are pure telemetry backends.
- **Act (orchestration).** Components that drive a twin or drive the surface itself: **Caldera**
  (with its Sandcat agent) runs attack playout; the **Wazuh manager** holds detection policy and
  drives blue response; **`o2control`** is the launch point for standing the surface up and for
  every playout that binds against it (tap, traffic, exercise).
- **Steer (console) + see (telemetry).** The **axRange Console** (`docs/design/16-unified-console.md`)
  and the control-plane API/CLI *steer* — it is the one operator-facing surface for deploying,
  inspecting and changing a twin. **Grafana** *sees*: it queries Loki and Prometheus and renders
  the capture-health, Suricata-alerts and attack-coverage dashboards, linked from the Console's
  Telemetry area. Grafana no longer drives deploys or any other write.

Two placements are easy to get wrong, so they are called out explicitly:

- **Grafana is telemetry only, not a control surface.** It queries Loki and Prometheus and
  renders dashboards; it stores nothing itself and (since 2026-09-29) drives nothing — the
  `axellio-axrange-control-panel` plugin that used to steer deploys through it was removed
  2026-10-01 (`docs/design/16-unified-console.md` §9).
- **Wazuh is dual-role, split across planes.** Its **agent**, installed on a twin's hosts, is a
  watch-plane collector. Its **manager**, standing in O2, is an act-plane component — it holds
  detection policy and drives the defensive response. Caldera, by contrast, is pure act-plane —
  it only drives attack playout, it collects nothing.

Only Loki and Prometheus are pure telemetry backends. Everything else is a collector, a driver,
or a console — this is what makes the watch/act/steer split useful: it tells you, for any given
O2 component, what it is *for*, not just where its bits happen to live.

## `o2control` — a thin engine client, not a second orchestrator

`axrange/o2control/` is the code that stands the O2 surface up and owns its lifecycle. The
central design choice is that it is **not** a second orchestration engine sitting next to the
standalone engine — it is a **thin client** that reuses the engine's own planner, adapter, and
state store as a generic spec executor.

Concretely: a twin deploy and an O2 surface stand-up run through the exact same
plan → deploy machinery. The only thing that differs is the **tier tag** passed into that
machinery — `te` for a twin, `o2` for the surface — which flows into every deterministic resource
name and into the destroy guard described in [02-twin-model.md](./02-twin-model.md). `o2control`
composes the surface spec (which sensors, on which node, what transport addressing), hands it to
the engine, and the engine executes it precisely as it would any other deploy: idempotent,
resumable, recorded in the same state store.

This keeps the boundary rule intact — the engine stays standalone and never imports `o2control`;
`o2control` imports downward into the engine, the same direction the CLI and API already do.

`o2control` owns four responsibilities:

1. **Compose** a surface spec — which sensors to stand up, on which node, with what transport
   addressing.
2. **Stand up** the surface as an `o2`-tagged, **persistent** deploy — nothing about it auto-expires
   or gets swept by a twin's destroy.
3. **Publish an inventory** — what sensors are standing, each one's type and transport IP. This
   inventory is the discovery source every downstream playout binds against: the tap playout
   ([06-taps-sensors.md](./06-taps-sensors.md)), the traffic tier
   ([07-traffic-personas.md](./07-traffic-personas.md)), and the exercise tier
   ([08-exercise-measurement.md](./08-exercise-measurement.md)) all read it rather than reaching
   into O2 internals directly.
4. **Tear down** the surface explicitly, on its own lifecycle — decoupled from any twin's destroy.

Because the standing surface is just another deploy in the same state store, it inherits
resume-safety for free: re-running the surface stand-up adopts what is already standing instead
of re-creating it.

## Lifecycle and CLI

```
axrange o2 {deploy,status,info,destroy}
```

`o2 deploy` stands up the **sensor half** of the surface — the sensor VMs `o2control` composes
and runs through the engine as an `o2`-tagged deploy (or adopts them, if already standing); `o2
status` and `o2 info` report on it; `o2 destroy` tears it down explicitly. `o2` also participates
in the shared `validate`/`plan` verbs like the other tiers in the CLI grid (see
[02-twin-model.md](./02-twin-model.md) "Lifecycle verbs").

The **server half** — the `o2/compose/` stack described below — is provisioned separately, by
running its docker-compose file directly on a dedicated host, not through `axrange o2 deploy`.
This is a deliberate split, not an oversight: the server stack is heavier infrastructure an
operator stands up once per lab and rarely touches again, while the sensor half is what changes
as tap plans bind and rebind twins to sensors. An operator therefore performs two separate
stand-up steps to get a fully working O2 surface — compose the servers once, then `axrange o2
deploy` the sensors — rather than one command doing both.

`o2 info` resolves live facts rather than making an operator hand-copy them: it looks up the
current IP address of the standing services VM and derives each of the surface's endpoint URLs
(Grafana, Loki, Prometheus, Caldera, the Wazuh manager) from it, falling back to an explicit
override only where one is configured. The same resolution machinery answers `tap info` for a
config-declared external sensor's live name and management IP (see
[06-taps-sensors.md](./06-taps-sensors.md)).

## The `o2/` folder layout

The top-level `o2/` directory holds the non-Python, appliance-style surface components — the
parts of O2 that are not `axrange/o2control/`'s Python client code:

- **`o2/compose/`** — the server stack (Prometheus, Loki, Grafana, Caldera, the Wazuh manager),
  composed on a dedicated docker-host VM.
- **`o2/tcpdump-sensor/`** — the capture-appliance payload: shell scripts and systemd units, not
  Python, so it can run on a host with no Python environment at all. This is the reference recipe
  behind the `tcpdump-sensor` sensor type (see [06-taps-sensors.md](./06-taps-sensors.md)).
- **`o2/grafana/dashboards/`** — the provisioned dashboard set: capture health, attack coverage and
  Suricata alerts are live telemetry. The `axellio-axrange-control-panel` Grafana plugin
  (formerly `o2/grafana/plugins/`), its `control-console` dashboard, the deploy/op-status and
  pcap-browser dashboards, and the topology-eval bake-off were deprecated 2026-09-29 and removed
  2026-10-01 (`docs/design/16-unified-console.md` §9; `docs/reference/ui-consolidation/05-deletion-candidates.md`),
  superseded by the Console's Range and Twin areas.

This split mirrors the engine's own module boundary: appliance-style, non-Python components live
under `o2/`; engine-consuming Python code lives inside the `axrange/` package as `o2control`. See
[03-engine.md](./03-engine.md) for the equivalent boundary rule on the engine side.

## Production topology (design-only, not built)

The long-term production deployment shape is a single host/VM where the control-plane API
(`axrange/api/` + `axrange serve`) and the observability stack (Grafana + Loki + Prometheus) come
up together as local containers under one `docker compose` bring-up, with the dev box and the
prod box interchangeable. The sensor/appliance side of O2 stays out of this container: sensors
need real NIC/raw capture access and keep their own host/VM lifecycle, standing outside the
compose bring-up and shipping their telemetry to it over the network, exactly as they do today.
This is a design target, not a built component — no compose file for the control+observability
plane exists yet.

## Where this fits

- [02-twin-model.md](./02-twin-model.md) — the Lab tier's tag, name shapes, and its place in the
  teardown protect/cascade matrix.
- [06-taps-sensors.md](./06-taps-sensors.md) — the sensor registry and the tap playout that binds
  a twin's tap points onto O2's standing sensors.
- [07-traffic-personas.md](./07-traffic-personas.md) and
  [08-exercise-measurement.md](./08-exercise-measurement.md) — the traffic and exercise playouts,
  both launched against O2's standing servers and both discovering what is standing through
  `o2control`'s published inventory.
- [11-api-ui.md](./11-api-ui.md) — the control-plane API in more depth.
- [16-unified-console.md](./16-unified-console.md) — the axRange Console, the STEER-plane surface;
  Grafana's role here is now telemetry only.

For the design reasoning trail, see `docs/CHANGELOG.md` and `docs/design/README.md`'s
retired-brief mapping table.
