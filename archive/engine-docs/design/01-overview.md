*What axRange is, the five planes it's built from, and the five-tier twin model at a glance.*

## What this is

axRange is an on-premises cyber-range platform for designing, deploying, and exercising
synthetic sandbox environments end-to-end. It lets an operator:

1. **Design** twin content by hand-authored Scenario IR/twin-config YAML, or by editing an
   aximprint-cast twin's assertions in the axRange Console's Design area
   ([16-unified-console.md](./16-unified-console.md)). The free-hand layered canvas described below
   was the original v1 entry-point concept; it was never built, and the layer/overlay model it
   would have exposed stays an IR concept rather than a live UI commitment
   (`docs/OPEN_QUESTIONS.md` Q1, `docs/ARCHITECTURE.md` §1).
2. **Deploy** those designs to a virtualization backend (Proxmox first; VMware and cloud later
   via pluggable adapters).
3. **Capture** mirrored network traffic from inside a deployed environment to a central sensor.
4. **Simulate attacks** against the deployed environment, and replay them after defensive
   controls are added, to prove the controls work.
5. **Use AI** to plan scenarios, drive red-team and blue-team agents, and analyze captured
   telemetry.

**Primary purpose:** validate cyber-defense solutions against repeatable attack scenarios in
realistic, disposable environments.

**Secondary uses:** training, detection-engineering experimentation, and scenario-library
curation.

## Architecture at a glance — five planes

```
┌───────────────────────────────────────────────────────────────────────┐
│ Design: hand-authored Scenario IR/twin-config YAML, or the Console's  │
│ Design-area assertions editor over an aximprint cast                  │
│ (the free-hand layered canvas below was never built — see note above) │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ save → Scenario IR (platform-agnostic YAML)
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Validator → Planner (DAG) → Adapter                                   │
│ Adapter: Proxmox (v1), VMware/cloud later                             │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ deploy
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Runtime: target-environment zones                                     │
│ Each zone = SDN zone + bridge + VMs/CTs + per-tap tc mirrors           │
│ Mirrored frames exit via a keyed GRE tunnel over a dedicated transport │
│ vnet                                                                   │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ mirror
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Capture sensors (standing, in the O2 surface layer)                   │
│ typed sensor registry (tcpdump / suricata / zeek); rotating pcaps,     │
│ disk-watchdog retention                                                │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ telemetry + control
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ AI core (five roles)                                                  │
│ Planner · Red-team · Blue-team · Telemetry analyst · Traffic gen      │
└───────────────────────────────────────────────────────────────────────┘
```

Design content is "just an editor away" from the **Scenario IR**, a platform-agnostic YAML
document that is the single hand-off between design and execution — hand-written directly, or
produced by the Console's Design-area assertions editor recompiling an aximprint cast
([16-unified-console.md](./16-unified-console.md)). The IR flows through a **validator** (schema,
cross-layer, and network-sanity checks), a
**planner** (builds an ordered operation DAG and drives a state-tracked, resumable execution),
and an **adapter** (translates the DAG into calls against a real virtualization backend —
Proxmox in v1). See [03-engine.md](./03-engine.md) for the full pipeline.

Once deployed, a target environment is a living, agent-instrumented world: hosts run identity,
data, and defensive (blue-team) agents. Benign persona traffic is driven onto it by the **Traffic
tier**, and attacker technique execution by the ephemeral **Exercise tier** — neither is part of
the twin's own deploy, and the adversary overlay a scenario authors is planted by the Exercise
tier, not the twin. Every tappable link can mirror its traffic out to a sensor over a keyed GRE
tunnel. Sensors, telemetry storage, and the attack/defense control plane live in a separate **O2
surface layer** that is
stood up independently and shared across many deployed environments — see
[05-o2-surface.md](./05-o2-surface.md).

An AI core plans scenarios, drives red-team and blue-team agents, and analyzes captured
telemetry, closing the loop from design through measurement.

## The five-tier twin model at a glance

axRange organizes everything it deploys into five tiers, each independently deployable and
independently torn down:

| Tier | Tag | What it is |
|---|---|---|
| **Lab** | `o2` | The standing observability/orchestration surface — sensors, telemetry storage, and the attack/defense server stack — stood up once and shared across environments. |
| **Twin** | `te` | A deployed target environment: the infrastructure, apps, and identity/data/defense overlays a scenario describes, running as real VMs/networks. |
| **Traffic** | `tfc` | Benign persona traffic bound onto an already-deployed twin, so the environment behaves like a populated network rather than an idle one. |
| **Tap** | `tap` | The capture bindings connecting a twin's tappable links to standing Lab sensors. |
| **Exercise** | `exr` | An ephemeral attack/defense/measurement run against an instrumented twin. |

Each tier has its own deterministic resource-naming scheme, its own lifecycle verbs, and its own
place in a teardown protect/cascade matrix that keeps tiers from stepping on each other. The
full model — name shapes, cascade rules, and lifecycle verbs — is in
[02-twin-model.md](./02-twin-model.md).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Virtualization | Proxmox VE | Mixes VMs and LXC on one cluster; free; scriptable API; OVS-capable |
| Scenario networks | Proxmox SDN, Simple/VLAN zones | Cluster-wide programmable networks; pairs with OVS for mirroring |
| Mirror plane | per-tap `tc` mirror → keyed gretap → sensor | Mirrors traffic on the SDN-materialized Linux vnet bridge where the tap lives; GRE crosses hosts |
| Transport vnet | Dedicated isolated vnet | Sensor traffic stays invisible to scenario traffic; no broadcast-domain leakage |
| Capture sensors | typed registry (`tcpdump-sensor` / `suricata-sensor` / `zeek-sensor`), rotating pcaps | Interchangeable capture/detection endpoints behind one keyed-gretap contract |
| Config delivery | cloud-init + Ansible, delivered over the qemu-guest-agent exec channel | First-boot config that works even inside isolated scenario networks that defeat push-SSH |
| Scenario IR | YAML + JSON schema, versioned in git | Human-editable, diffable, git-friendly |

For the engine pipeline in depth, see [03-engine.md](./03-engine.md). For the O2 surface layer,
see [05-o2-surface.md](./05-o2-surface.md).
