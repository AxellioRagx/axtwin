*Target-environment content: the layered model an author writes, the four cross-cutting
overlays, twin configs, and the archetype library.*

## Scenario vs. target environment

A **Scenario IR** is the authored spec — a versioned YAML recipe an author writes: layered
infrastructure, apps, activity, and optional overlays. A **target environment (TE)** is the
deployed instance that spec stands up — the running, agent-instrumented world under test. A
scenario *defines* a target environment; `target-environments/` is the library of Scenario IRs,
each defining one. For the full naming rationale, see `docs/CONVENTIONS.md` "Scenario vs. target
environment." For the version-locked schema itself, see `docs/IR_SCHEMA.md`.

## The layered model

Every Scenario IR is built from three layers, in dependency order:

```
layers:
  infra:      # networks + nodes (hosts, routers, switches)
  apps:       # services and first-boot roles bound onto infra nodes
  activity:   # actors, behaviors, and scripted/scheduled steps
```

- **Infrastructure** — the topology: segments (networks), and nodes discriminated by `kind`
  (host / router / switch). This is what the engine's planner and adapter turn into VMs, SDN
  zones, and bridges (see [03-engine.md](./03-engine.md), [04-networking-capture.md](./04-networking-capture.md)).
- **Apps & tools** — services and platform roles (e.g. a domain controller role, a package
  install, a container) bound onto infra nodes at first boot, via cloud-init + pull-mode Ansible.
  This is not a runtime reconfiguration channel — mid-run changes belong in activity.
- **Activity** — actors bound to hosts, running either a named behavior or inline scripted
  steps, one-shot or on a jittered schedule. This is the layer that produces traffic and,
  when the exercise tier drives it, attacker technique execution (see
  [08-exercise-measurement.md](./08-exercise-measurement.md) for where attack/adversary playout
  actually runs from).

A `bindings:` list wires apps and activity to specific nodes. A scenario may also carry a
`sensors:` block, but a twin's own deploy stands up no sensor VM for it — the validator emits a
warning, and capture is the job of standing O2 sensors bound to the twin through a tap plan (see
[06-taps-sensors.md](./06-taps-sensors.md)).

## Overlays — optional, cross-cutting, additive

`overlays:` is an optional top-level block layered across the infra/apps/activity model rather
than folded into it — a scenario declares zero or more of four kinds: **identity**, **data**,
**defense**, and **adversary**. Two rules govern all four:

- **Overlays configure existing nodes; they never add nodes.** An overlay materializes roles,
  config, or data onto infrastructure the `infra:` layer already declared. If a scenario needs a
  dedicated overlay-only node (a domain controller, a SIEM), that node is authored in `infra:`
  like any other, and the overlay configures it.
- **Overlays compile to config-class operations delivered over the same in-guest channel** apps
  and activity use (the qemu-guest-agent exec channel) — they are declarative authoring surfaces,
  not a second execution plane.

For the formal field-by-field contract, see `docs/IR_SCHEMA.md` §8.

### Identity

`overlays.identity` declares principals and, on Windows nodes, a domain. A node with `role: adds`
promotes to an Active Directory forest; identity entries then bind onto that domain:

- **Domain users** — an entry with a `domain:` creates an AD user in that forest.
- **Host joins** — a node's `joins: <fqdn>` field explicitly joins that host to the named domain
  (requires a `role: adds` node providing it).
- **Local users** — an identity entry with a `host:` and no `domain:` creates a local account on
  that host instead (Windows `New-LocalUser` / Linux `useradd`).

Beyond stand-up, identity entries carry a set of **vulnerable-AD weakening primitives** that make
the domain a real, exploitable target rather than an inert directory — the canonical AD
escalation-path model used by the AD-flavored archetypes:

| Primitive | Field | Enables |
|---|---|---|
| Kerberoastable service account | `spn:` | forces RC4, makes the account's TGS crackable via `GetUserSPNs` |
| AS-REP-roastable account | `asrep_roastable: true` | disables pre-auth, crackable via `GetNPUsers` |
| Weak per-account password | `password:` | a deliberately crackable credential |
| OU placement | `ou:` | idempotent organizational-unit creation + placement |
| Dangerous ACLs | `acl: [{right, target}]` | takeover edges — `GenericAll` / `GenericWrite` / `WriteDacl` / `WriteOwner` / `ForceChangePassword` / `AddMember` on a target principal |
| Unconstrained delegation | `delegation: unconstrained` | the account can impersonate any user that authenticates to it |
| Constrained delegation | `delegation_to: [spn, ...]` | the account may delegate to the listed SPNs |

All of this materializes over the qemu-guest-agent PowerShell channel during deploy — domain
promotion, user/OU creation, ACL/delegation grants, and the weakening flags are applied as part of
the twin's own build-out, not as a separate step.

**Still open:** identity today models individual users and computers, not first-class, nestable
*groups* (BloodHound-style privilege-path graphs), machine/`computer` objects with their own
delegation flags, gMSA, or a certificate-based (ADCS) identity — real future growth directions on
the same principal, not yet built. Deception identities (honey accounts/honeytokens that alert on
touch) are likewise a natural pairing with the Defense overlay that hasn't been built. A
Samba/Linux-native directory realm was considered and deliberately set aside in favor of Windows AD
for this increment.

### Data

`overlays.data` seeds a **deterministic synthetic dataset** at a path on a host — the `seed-dataset`
operation. Each entry names an `id`, a `host`, a `path`, a `classification` tag (free-form — `pii`,
`phi`, `secret`, and so on), an optional `crown_jewel` flag for scenario scoring, and an optional
`synthetic_size`. The dataset materializes in-guest from a seeded generator keyed off the
scenario's `seed`, so `reproducible: true` scenarios produce byte-identical data across redeploys —
verified by comparing SHA-256 across runs. This is the mechanism behind a scenario's "crown jewel"
— a labeled, sized artifact an attacker exfiltrates and a defender is scored on protecting.

**Current limitation:** the generator produces labeled random bytes, not realistic structured
content — a `customers.csv` entry is a header plus random filler, not actual CSV rows. Authored or
specific content (a CTF flag string, a real config file) and structured/realistic generation (fake
but well-formed PII/PHI rows) are both still out of scope; archetypes that want exact content today
fall back to placing it via a `script:` install action instead of the data overlay.

### Defense

`overlays.defense` declares the blue-team half of a scenario — an entry binds via `installed_on:`
(host-agent mode) and/or `observes:` (network mode); at least one must be present.

- **Host agents** — `install-defense-agent` drops a small tool config onto each listed host and
  enables the service. Default host agents (a Wazuh agent and a Prometheus node-exporter) are
  applied automatically to every `kind: host` node unless a node opts out
  (`node.default_agents_off`) or the scenario opts out at the top level
  (`scenario.default_agents: false`); these default agents pair with the standing O2 Wazuh manager
  and Prometheus — see [05-o2-surface.md](./05-o2-surface.md). A Windows host additionally gets an
  AD-audit branch that enables the Kerberos (4768/4769) and DCSync (4662) audit subcategories once
  the domain is up, so a promoted DC's own event log becomes a detection source.
- **Network detection** — network-based detection is provided by standing O2 **suricata** sensors,
  bound to the twin through a tap plan so mirrored traffic reaches them. Attaching a
  scenario-specific detection ruleset to a standing sensor is an area still being built out. See
  [06-taps-sensors.md](./06-taps-sensors.md) for the sensor registry and how mirrored traffic
  reaches a sensor.

The blue half only ever *declares agents*. The servers those agents report to — Wazuh manager,
Prometheus, Loki, Grafana — are standing O2 components, not part of any individual twin's own
deploy; cross-link [05-o2-surface.md](./05-o2-surface.md).

**Current limitation: detect only, no prevent.** Every defensive component described here is
passive — a host agent enrolls and ships telemetry, a network sensor reads a mirror of the traffic,
neither sits in the data path. There is no in-path blocking (an inline IPS, firewall policy, a WAF,
an EDR quarantine action) anywhere in the stack today; a preventive control would need a genuinely
different placement (on the router, on the host itself, or as a bump-in-the-wire), not just a new
tool. Detection-coverage scoring — correlating what an exercise's attack actually did against what
the defense actually alerted on — is the Exercise tier's job; see
[08-exercise-measurement.md](./08-exercise-measurement.md).

### Adversary

`overlays.adversary` declares the red-team counterpart: an entry names `targets:` (existing
`kind: host` nodes) and a Caldera `server:`. It is **authored content**, not a twin-deploy action
— a twin's own deploy plants nothing (the validator warns if a scenario declares it). The
standalone **Exercise tier** is what plants a **Sandcat** agent on each target and points it at the
server, binding against an already-deployed twin. Planting and pointing the agent is all the
overlay describes; it does not compile or run an operation. Optional `group`, `adversary`, and `techniques`
fields are informational tags for the Caldera console and for a future operation-report ingestion
path, not executed values.

The agent this overlay plants pairs with the standing O2 Caldera server, and the operation itself
is driven manually at the Caldera console or, for the scripted inline-steps path, by the standalone
**Exercise tier**, which binds to an already-deployed twin rather than running from inside a twin's
own deploy. See [08-exercise-measurement.md](./08-exercise-measurement.md) for the full
attack/defense/measurement playout model, including where technique-level ground truth comes from.

## Twin configs — parameterized IR templates

A **twin config** is a parameterized Scenario-IR template: the same YAML shape as a hand-authored
scenario, but with `${NAME:default}`-style parameter placeholders substituted at expansion time.
Expansion produces a plain Scenario-IR dict that the unchanged validator and planner consume — a
twin config is not a new schema, just a templating layer in front of the existing one.

The catalog lives under `twins/`, one self-contained folder per twin
(`docs/WORK_ORDER_twin_consolidation.md` §2): a `twin.yaml` manifest naming each tier's default
and variants, the config as `te.yaml`, and the twin's own portable, name-targeted tier files
(`tap/<variant>.yaml` — see [06-taps-sensors.md](./06-taps-sensors.md) for how a tap plan resolves
a name to a live deploy — `tfc/`, `exr/`), plus `personas/`, `scripts/` and `generated/`. The
legacy `twin-configs/<id>/<id>.yaml` and flat `<id>.yaml` layouts are still read (back-compat).

```
twins/
├── _shared/scripts/            cross-twin recipes (the sink)
├── corp-enterprise/
│   ├── twin.yaml
│   ├── te.yaml
│   ├── tap/{default,reshard,xpress,xpress-peri,gw-span}.yaml
│   ├── tfc/{default,busy,xseg,roles}.yaml
│   └── exr/ad-killchain.yaml
├── corpapcn-golden/            an aximprint cast: te.yaml + generated/<id>.{topology,provenance}.json
├── datacenter/  dmz-perimeter/  ics-ot/  sme-flat/    te.yaml + tfc/default.yaml
└── ...
```

Wired at the CLI: `axrange te {validate,plan,deploy} --config <id> [--param k=v ...]` expands the
named config in place of a scenario positional; `axrange twin-config list|show <id>` browses the
catalog. Once deployed, a twin config's expanded IR is an ordinary deployed twin — the Traffic tier
binds personas onto its published host `role:`s ([07-traffic-personas.md](./07-traffic-personas.md)),
and the Tap tier binds sensors onto its published tap points
([06-taps-sensors.md](./06-taps-sensors.md)).

## The archetype library — `target-environments/`

`target-environments/` is the deployable Scenario IR library — content, not code. It holds:

- A set of archetype scenarios — `soc-detection-lab`, `ad-domain-lab`, `ctf-range`,
  `ot-ics-range`, `dfir-hunt-lab`, `vuln-management-lab`, `purple-team-range` — each VLAN-zoned
  and multi-segment, each with a `kind: router` node providing inter-segment routing and WAN NAT.
  These are the vehicles for the layered model and overlays described above: identity (AD forests,
  vulnerable-AD weakening) on the AD-flavored archetypes, data (crown-jewel PII) on
  `purple-team-range`, defense (host agents + suricata-sensor rules) on the detection-flavored
  archetypes, and adversary (Sandcat + Caldera targets) where a red-team loop is modeled.
- Verification and customer environments (for example `fabric-verify`) exercising specific engine
  capabilities end to end.

Every archetype is versioned YAML the engine consumes directly via `--scenarios-dir`, validated and
planned through the same `axrange te validate|plan|deploy` path as any hand-authored scenario — see
[03-engine.md](./03-engine.md) for that pipeline, [02-twin-model.md](./02-twin-model.md) for how a
deployed archetype becomes a tier-tagged twin, and
[12-ai-scenario-research.md](./12-ai-scenario-research.md) for how the archetype set itself is
authored from corpus research. For the formal IR contract every archetype satisfies, see
`docs/IR_SCHEMA.md`.
