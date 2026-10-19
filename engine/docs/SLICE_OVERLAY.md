# Slice design — `OP-OVL` overlay materialization (identity / data / defense)

> **Adversary-overlay trigger moved to the Exercise tier (2026-07-24,
> `docs/WORK_ORDER_exercise_tier.md`, CHANGELOG "Exercise tier (`exr`) BUILT").** The `OP-OVL-070
> install-agent` machinery (render/backend) is unchanged, but a `te` (engine) deploy's own DAG no
> longer emits the **adversary** branch (`agent_kind=adversary`, i.e. `overlays.adversary` → Sandcat)
> at all — the standalone, ephemeral `exr` playout (`axrange/o2control/exercise.py`) composes that op
> by hand against an already-deployed twin instead. The **exporter** branch
> (`agent_kind=exporter`, the default-agent sweep) and **identity/data/defense** overlays below are
> unaffected — they remain `te`-emitted exactly as this slice describes. `REF-260` (new, WARNING)
> fires when a `te`-tier IR still declares `overlays.adversary:`.
>
> **Terminology (2026-06-23, `docs/design/06-taps-sensors.md`, ratified):** the standing capture
> appliance and the standing "Suricata network-sensor" described in this doc predate the
> unification into typed, **in-scenario `sensor`s** (`tcpdump-sensor` / `suricata-sensor` /
> `zeek-sensor`) declared in the IR `sensors:` block and deployed as VM(s) with the scenario. The
> IR contract + validator already use `sensor` (Phase A, done); the capture **mechanics** here
> (per-tap `tc` mirror → keyed gretap → flow-based decap) are **unchanged** by the refactor —
> only the *receiver* moved from a standing appliance to an **in-scenario sensor VM** (Phase B
> **live-verified on dev45 2026-06-23**: deploy → verify-capture → destroy, all green). The
> former standing "Suricata network-sensor" is now the **suricata-sensor** type.
>
> **O2 direction (2026-07-08, `docs/design/05-o2-surface.md`) — supersedes-in-direction, not yet
> built:** the in-scenario `sensor` model above (Phase A–C, live-verified) is the *current
> implementation*; the ratified **target** direction moves sensors to **standing `o2_`
> components** (provisioned once, reused across deploys — `docs/ARCHITECTURE.md` §0). Nothing
> below needs to change until that refactor lands; noted here so the direction isn't lost.
>
> **Overlays are now four kinds** — identity + data + defense + **adversary** (A3,
> `docs/design/08-exercise-measurement.md`, 2026-07-09: the red **host** agent, Sandcat, paired
> with the O2 Caldera server). This slice's scope (below) is the original three — identity / data
> / defense, all implemented; **adversary landed as A3 Stage A (BUILT 2026-07-11,
> `WORK_ORDER_attack_defense.md`) — a separate build outside this slice's original scope, sharing
> its `config_exec` QGA-mediated pattern (`OP-OVL-070 install-agent`, mirroring
> `install-defense-agent`'s render/backend shape). Also landing with it: `scenario.default_agents`
> (default-ON Wazuh + node-exporter sweep on every `kind: host` TE node — user decision,
> 2026-07-11). Unit/integration-verified (794 pytest tests); gated `dev45` live-verify PENDING.**

> **STATUS: DATA ✅ + DEFENSE (agent ✅ + sensor ✅) + IDENTITY ✅ (domain/local users + host-join, `docs/SLICE_WINDOWS_AD.md`) implemented + live-verified on dev45. — All three overlay increments complete.**
>
> **AD DEFENSE (network IDS) LIVE-VERIFIED 2026-06-24 (CHANGELOG):** an in-scenario `suricata-sensor`
> carrying an AD detection ruleset (`Sensor.rules` → `suricata_ad.rules`) — the modern, self-contained
> replacement for the retired standing Suricata network-sensor (Option C). Wired into `ad-domain-lab` +
> `purple-team-range`; the dev45 verify caught the full OP-ACT AD kill chain (recon/LLMNR/SMB/LDAP/
> Kerberos + the DCSync DRSUAPI bind, Priority 1) — the **purple-team loop** closes (attack runs *and is
> measured*). Host-side AD detection (Windows Sysmon/Wazuh) is the remaining follow-on.
> The Data increment (`seed-dataset`) shipped: render + ConfigBackend + planner
> `_emit_overlays`, 252 tests green, `validate`/`plan` PASS on purple-team-range
> (crown-jewel PII). Live on dev45: the baked python3 seeder materialized the
> dataset in-guest at the right size with a SHA256 byte-matching the local render
> (determinism proven — done-criterion #5). Defense + Identity (§3, §8) remain
> design-only — a clear emission error. See CHANGELOG 2026-06-15.
> **Authority:** design (Claude Code, 2026-06-15). The scope-boundary gates were
> removed 2026-06-11 and per-archetype overlays are delegated to Claude Code, so
> this is a build-time design, not a chat-ratification gate. It mirrors
> `docs/SLICE_ACTIVITY.md`: settle the mechanics, ship the smallest increment, defer
> the rest behind clear errors, record what was decided in the CHANGELOG
> (`**Authority:** implementation (Claude Code)`).
>
> Companion to `docs/IR_SCHEMA.md` (the `overlays:` surface — already ratified +
> validated), `docs/OP_CATALOG.md` §5.4 (the six OP-OVL op signatures — already
> ratified), `docs/PLANNER.md` §4.3/§5 (config idempotency, barriers), and
> `docs/SLICE_CONFIG.md` / `docs/SLICE_ACTIVITY.md` (the config-class slices this
> one extends — same QGA delivery channel).

---

## 1. What this slice is, and why it's next

The shipped slices stand up infra, capture, images, install software (`OP-APP`), and
generate traffic (`OP-ACT`). What no slice does yet is lay the **per-scenario overlays**
onto a deployed range: **identity** (an AD domain — DC, domain users, host domain-join),
**data** (synthetic datasets — crown-jewel PII, DFIR evidence, planted artifacts), and
**defense** (blue-team — network sensors and host agents that *observe* the range). These
are what turn the archetypes from "infra + tools + an attack run" into "a measurable
exercise": the defense overlay is the blue half that the OP-ACT red half is scored against.

Unlike OP-ACT, the overlay surface is **already fully scaffolded** — `docs/IR_SCHEMA.md`
defines `overlays.{identity,data,defense}` (parsed + validated), `OP_CATALOG.md` §5.4
defines six OP-OVL ops, and the `domain-ready` / `overlay-ready` barriers exist. So this is
an **implementation slice**, not a from-scratch schema design. Emission is the only missing
piece (pass-1 rejects `overlays:` today), exactly the position OP-APP and OP-ACT started from.

## 2. The pivotal decisions (mechanics — settled by the existing scaffolding + an archetype survey)

A survey of all seven Stage-2 archetypes' intended overlays (against their actual
`layers.infra.nodes`) settles the questions `CLAUDE.md` flagged as open:

- **D1 — Overlays configure *existing* nodes; they never add nodes.** In every archetype,
  the overlay targets (DC, SIEM, sensor, crown-jewel server, workstations) **already exist
  in `infra.nodes`**. `DataEntry.host`, `DefenseEntry.host`/`installed_on`, and identity's
  DC all reference existing node ids. An overlay materializes *roles / config / data onto
  substrate that infra already declared*. (If a future scenario wants an overlay to add a
  node — e.g. an implicit DC — that's a deliberate later extension, not v1.)
- **D2 — Overlays are a separate IR block that *emits* config-class ops, not a
  transformation.** `overlays:` stays its own top-level block (it already is). Materializing
  it emits OP-OVL ops that run over the **same QGA config channel** as OP-APP / OP-ACT. No
  node-graph mutation, no IR rewrite.
- **D3 — Overlay ops emit *after* `host-ready`, as siblings of apps + activity**, and (the
  data/host-agent ops) *after the host's apps* too — a dataset planted into a directory an
  app creates (a web root, a share) must not race the app. This reuses OP-ACT's
  `provision-app[$host@*]` / `run-script[$host@*]` gating.
- **D4 — Render in the backend, thread overlay entries as data** (no planner→`config_exec`
  import — same as OP-APP install actions and OP-ACT inline steps).

These mechanics apply to all three overlay kinds; the increments differ only in *which ops*
and *how heavy the in-guest action* is.

## 3. Increment plan (by dependency depth) — DATA first

| # | Kind | Ops | Hard deps | First archetypes | Status |
|---|---|---|---|---|---|
| **1** | **Data** | `seed-dataset` | `host-ready` (+ host apps) | purple-team (crown-jewel PII), dfir (evidence), vuln | **THIS SLICE** |
| 2 | Defense | `install-defense-agent`, `deploy-network-sensor` | host-ready; sensor also the capture path (`network-ready` + `attach-gre-tunnel` + `install-ovs-mirror`) | soc-detection-lab, ot, purple | follow-on |
| 3 | Identity | `wait-domain-ready`, `create-domain-user`, `join-host-to-domain` | a DC actually serving AD — needs **`role: adds` DC promotion (unbuilt)** + a Samba/Windows AD bring-up | ad-domain, purple | **scoped 2026-06-16 → Windows AD, 3 slices (W0/W1/W2); design-only: `docs/SLICE_WINDOWS_AD.md`** |

Data is the precedent-setter: one config-class op, gated on host-ready, zero dependency on
unbuilt machinery — the direct analog of the OP-ACT inline-steps engine. It establishes
`_emit_overlays`, the overlay scope conventions, and the catalog `after`-pattern fixes that
Defense and Identity will reuse.

## 4. This slice (Data / `seed-dataset`)

### 4.1 What `seed-dataset` does

`OP-OVL-040 seed-dataset` materializes a **synthetic dataset** at `path` on `host`,
deterministic from the scenario seed when `reproducible: true`. The `DataEntry` fields:
`id`, `host`, `path`, `classification`, `crown_jewel`, `synthetic_size`. There is **no
`content:` field** — the data is *generated*, not authored. The MVP generates a
deterministic synthetic file of `synthetic_size` at `path` (a small header recording
`id`/`classification`/`crown_jewel`, then seeded filler to size), produced in-guest so a
large `synthetic_size` isn't embedded in op inputs.

**Determinism:** seed = scenario `seed` (int) folded with `data_id`. Generated in-guest by a
seeded `python3` one-liner (python3 is baked into the Debian cloud template via imagebuild;
a missing-python3 guest is a clear error, like missing-ansible for OP-APP). `reproducible:
true` + same seed → byte-identical output.

**Out of this increment (clear deferred errors, not silent skips):**
- **Specific-content datasets** (e.g. CTF *flag strings*) — `DataEntry` has no `content:`/`from:`
  field. Flag injection is `config`/`script` (app) territory today; a `content:`/`from:` field
  extension is a follow-on. The MVP generates *synthetic* data only.
- **Classification-aware structured generation** (real-looking PII CSVs per a taxonomy) —
  OVR-040's taxonomy is unratified. MVP filler is classification-*labeled* but not
  classification-*shaped*.
- **`overlay-ready[data:$id]` → activity dependency** — the barrier exists (an activity can
  wait for a specific dataset before it runs). MVP emits `seed-dataset` as a leaf; wiring
  `overlay-ready` into `deploy-behavior-agent` is a follow-on (it's how OP-ACT will depend on
  a planted dataset, e.g. exfil-after-seed).

### 4.2 Op flow

For a data entry `crown-jewels` (host `app-srv`, in a scenario with apps on that host):

```
host-ready[app-srv] ─────────►┐
provision-app[*@app-srv] ─────┼─► seed-dataset[crown-jewels@app-srv]
run-script[*@app-srv] ────────┘
```

`seed-dataset` is a **leaf** (nothing depends on it in the MVP). It gates on `host-ready`
(catalog) plus the host's apps (the dataset may land in an app-created dir).

### 4.3 Components (grounded in the current tree, mirroring the OP-ACT slice)

| File | Change |
|---|---|
| `axrange/config_exec/render.py` | `render_dataset_seeder(entry, *, seed)` — `DataEntry` → a deterministic in-guest generator script (mkdir parent; seeded python3 writes `synthetic_size` to `path` with a classification header). `DatasetRenderError` for unsupported shapes (e.g. a `content:` ref). |
| `axrange/config_exec/backend.py` | `seed-dataset` case: render the seeder, write it into the guest, exec it (one-shot, idempotent — re-run regenerates identically), check exit. Reuse `GuestAgent`; **create the target's parent dir first** (the OP-ACT live-verify lesson). |
| `axrange/planner/pass1_emit.py` | Replace the `overlays` reject with `_emit_overlays`; for now emit only `overlays.data` (identity/defense stay a clear "not yet" error). Per data entry: resolve `host` (must be a defined node), thread the entry + scenario seed as inputs, `vmid` from `clone-vm[$host]`. |
| `axrange/ops/registry.py` | `seed-dataset.after`: keep `host-ready[$host]`; **add** `provision-app[$host@*]` + `run-script[$host@*]` (the OP-ACT star-tail fix — gate data on the host's apps). |
| validator | No change required (OVR-* already pass a non-empty data overlay). Emit-time enforces `host` resolves + `path` set (the OVR rules are lenient stubs); a REF-rule for `data.host → node` is an optional hardening follow-on. |
| `tests/` | `test_overlay_exec.py` (render determinism, backend seed, planner emission + the app-gating edges); a `valid_overlay_data` fixture; extend the DAG test. |
| an archetype | Add an `overlays.data` block to the first archetype (candidate: `purple-team-range` crown-jewel PII on `app-srv`, `crown_jewel: true`) — the first real overlay content. |

## 4b. Defense increment design (next — `install-defense-agent` + `deploy-network-sensor`)

Grounded in `soc-detection-lab`: the **apps** already install + stage the tools
(`sensor-stack` → Suricata/Zeek; `siem-stack` → Wazuh manager), and `sensor_setup.sh`
explicitly defers *"the live mirrored-traffic feed to this sensor"* to this overlay. So the
Defense overlay adds the **wiring apps can't express**, in two ops:

**`install-defense-agent` (OP-OVL-050) — host agent.** Per (defense entry × `installed_on`
host): drop the agent `config:` (threaded at emit, like run-script reads its file) pointing
at the SIEM manager, and enable the service (`systemctl enable --now <tool>` / a tool enroll
command). The agent *binary* comes from an app (or the template) — the overlay configures +
enrolls it, egress-free. Gates on `host-ready` + the host's apps (`provision-app[$host@*]` /
`run-script[$host@*]`, the star-tail pattern). Semantically close to run-script; the clean,
low-risk op. A small tool registry maps `tool:` → config path + enable command; unknown tool
→ clear error.

**`deploy-network-sensor` (OP-OVL-060) — network sensor feed.** Deliver the `observes:` nets'
traffic to the sensor host and point the IDS at it. The sensor sits on the SOC segment and
does **not** see dmz/corp directly — this op is the capture-plane coupling, and it's the
actual SOC value (Suricata/Zeek inspecting the real attack traffic so detection can be
scored). The catalog already gates it on the capture ops (`attach-gre-tunnel` +
`install-ovs-mirror`). **The fork is how the feed is delivered:**

- **Option L (live feed)** — reuse the Option-1 capture mechanism: the sensor becomes a
  second mirror target. Either route the observed hosts' taps to the sensor *as a primary
  capture target* (per-tap sensor routing already supports this — set the sensor's transport IP
  as a capture target) and run the flow-based gretap decap + Suricata af-packet on it, or add a per-tap `tc` mirror
  from each observed net's vnet bridge → a keyed gretap on the sensor. High value, reuses
  proven plumbing, but extends the capture plane to a second endpoint — the most complex op
  in the overlay slice so far.
- **Option C (config-first)** — MVP configures the IDS ruleset + `observes` metadata only;
  the live feed is a follow-on. Simple, but the sensor inspects nothing — it doesn't deliver
  the SOC value, so it's a weak MVP.

Recommended sequencing: `install-defense-agent` first (clean, mirrors the Data increment),
then `deploy-network-sensor` via **Option L** (the point of the SOC archetype). First
content: `soc-detection-lab` — a Wazuh host-agent defense entry on the victims, then the
sensor observing dmz+corp.

### Resolved design — `deploy-network-sensor` via Option C (appliance-adjacent network sensor Suricata)

**Chosen 2026-06-16 (user): Option C, network sensor.** Reconsidered A/B away: the capture appliance *already*
decapsulates the entire mirror stream onto one iface (`gtcap0`), so detection at the capture
tier needs **none** of A/B's transport-NIC / tap-rerouting plumbing. A separate Suricata
network sensor sits next to the capture appliance, fed a mirror of the capture appliance's capture iface, and inspects the whole
aggregated stream. The scenario + capture plane are **unchanged**. This maps to the IR's
already-reserved `tap_from: external-mirror` mode (the mirror is wired at the capture tier, not
per-scenario).

**Modeling consequence (accepted):** the network sensor is **capture-tier aggregation infra**,
not a per-scenario scenario-node. Like the capture appliance, the network sensor is **standing infra** axRange
*routes to + configures*, not a node it deploys. `observes:` becomes metadata (the network sensor sees
the merged stream; per-net scoping is a BPF/VLAN filter or a dedicated per-observed-nets sensor).

Pieces:
1. **Standing (provisioned once, alongside the capture appliance — a `o2/tcpdump-sensor/` companion):** a Suricata
   network sensor VM (QGA-reachable, like the capture appliance) + a **mirror feed** from the capture appliance's `gtcap0` to the
   network sensor (a `tc` mirror → gretap to the network sensor, network sensor runs a flow-based decap iface). Set
   up at provisioning time; reused across scenarios.
2. **Per-scenario — `deploy-network-sensor` (OP-OVL, config-class over QGA):** reach the
   configured network sensor (vmid from adapter config, like the capture appliance's), render `suricata.yaml`
   (`af-packet` on the decap iface) + drop the scenario's ruleset, enable + restart Suricata.
   Small, in-guest config — no scenario-graph changes. The `ovs:$sensor_node` resource hint is
   dropped (no host OVS work).

`DefenseEntry`: `host:` = the logical network sensor id (config maps it to a vmid), `tap_from:
external-mirror`, `observes:` = metadata, `ruleset:` = rules to load.

**Build stages:** (1) `deploy-network-sensor` op in `config_exec` (render suricata.yaml + ruleset,
configure the network sensor over QGA); (2) adapter config — network sensor vmid; (3) planner — emit
`deploy-network-sensor` for `observes:` entries (drop the "rejected" stub), resolve the network sensor;
(4) catalog: drop `ovs:$sensor_node`; (5) tests + soc-detection-lab sensor content
(`tap_from: external-mirror`); (6) provision the dev45 network sensor + `gtcap0` mirror, then live-verify
(attack traffic → capture appliance → network sensor → Suricata alerts). **All six done + live-verified 2026-06-16.**

#### Network sensor provisioning runbook (one-time, like the capture appliance — dev45 reference)

The network sensor + capture-appliance mirror are **standing infra** (provision once; reused across deploys). What
was provisioned on dev45 (`axr-network-sensor`, vmid `8001`):

1. **Network sensor VM** — clone the Debian template onto the host-reachable uplink bridge so it has a
   transport IP + internet: `qm clone <tmpl> 8001 --name axr-network-sensor --full 0`;
   `qm set 8001 --net0 virtio,bridge=vmbr0 --ipconfig0 ip=dhcp --ciuser <u> --agent enabled=1`;
   `qm set 8001 --delete cicustom`; `qm start 8001`.
2. **Suricata** — `apt-get install -y -t bookworm-backports suricata` (backports release tag is
   required — stable's `libhtp2` is too old). The packaged `suricata.service` is stopped/disabled
   by `deploy-network-sensor` at run time (it sniffs the wrong iface).
3. **Network sensor decap iface** — `ip link add cap-mirror type gretap external; ip link set cap-mirror up`
   (flow-based, decaps the re-encapsulated mirror; the name in `NETWORK_SENSOR_IFACE`).
4. **Capture appliance → network sensor mirror** (on the capture-appliance VM): `ip link add cap2sensor type gretap remote <network-sensor-ip>
   key 1; ip link set cap2sensor mtu 1462 up`; then `tc qdisc add dev gtcap0 clsact` +
   `tc filter add dev gtcap0 ingress matchall action mirred egress mirror dev cap2sensor` — mirrors
   the capture appliance's decapped feed to the network sensor.
5. **Adapter config** — `NETWORK_SENSOR_VMID=8001`, `NETWORK_SENSOR_IFACE=cap-mirror`.

This belongs in `o2/tcpdump-sensor/` as a packaged companion installer (TODO) — for now it's this runbook.

## 5. Open questions (resolve by building, per the ADAPTER.md §7 pattern)

- **Synthetic generator vocabulary.** What `python3` generator the seeder emits (seeded bytes
  vs. a labeled header + filler), and the `synthetic_size` grammar (`10Mi`/`500Ki` — reuse the
  IR size parser). Lean minimal: header + seeded filler to size.
- **Determinism source.** Confirm `scenario.seed` is the seed (folded with `data_id`); behavior
  when `reproducible: false` (non-deterministic filler is fine).
- **App-gating breadth.** `provision-app[$host@*]` gates on *all* the host's apps. Confirm
  that's the right default (it's the safe superset; a dataset never needs to precede an app).
- **`python3` presence.** Like OP-APP's ansible check — fail loudly if absent; it's baked by
  imagebuild. Confirm at build time.

## 6. Dependencies & tie-ins

- ✅ **Execution channel** — `config_exec` + `GuestAgent` (QGA) ships and routes config-class ops.
- ✅ **Op signature** — `OP_CATALOG.md` §5.4 `seed-dataset` is ratified; this implements it.
- ✅ **IR + validator** — `overlays.data` is parsed + validated; only emission is missing.
- ✅ **No new planner machinery** — `seed-dataset` is `redo` config-class; no escalate/walk-back.
- 🎯 **Concrete targets** — the seven archetypes' data needs (crown-jewel PII, DFIR evidence,
  vuln configs). First: `purple-team-range`.

## 7. Done-criteria (this increment)

1. `axrange validate | plan` of a scenario with an `overlays.data` entry; `plan` shows
   `seed-dataset[$id@$host]` gated on `host-ready` + the host's `provision-app`/`run-script`.
2. The renderer is deterministic (same seed → identical seeder script) and `bash -n` /
   `python3 -c` clean.
3. Backend (fake QGA): renders, mkdir's the parent, writes + execs the seeder; returns
   `{dataset_seeded: true}`. A `content:`-style entry → clear `DatasetRenderError`.
4. identity/defense overlays each produce a clear "not yet implemented" emission error.
5. Live-verify on dev45: deploy seeds the dataset in-guest (verified via QGA: the file exists
   at `path`, right size, deterministic). Full suite green.

## 8. Follow-on increments (after this one)

1. **Defense** (`install-defense-agent` + `deploy-network-sensor`): the blue-team half —
   host EDR agents and network sensors (Suricata/Zeek) configured to `observe:` segments via
   the capture mirror. Targets `soc-detection-lab`. The sensor couples to the capture plane.
2. **Identity** (`wait-domain-ready` + `create-domain-user` + `join-host-to-domain`): an AD
   domain. Needs `role: adds` DC promotion (unbuilt) + a Samba/Windows AD bring-up first.
   **Scoped 2026-06-16 → Windows AD as a three-slice effort (W0 imagebuild / W1 substrate /
   W2 identity); design-only, see `docs/SLICE_WINDOWS_AD.md`.**
3. **`overlay-ready` → OP-ACT dependency**: let an activity wait on a specific overlay
   (`deploy-behavior-agent after overlay-ready[data:$id]`) — e.g. exfiltrate-after-seed.
4. **`content:`/`from:` data field**: authored/specific-content datasets (CTF flags), beyond
   synthetic generation.
5. **Defense scoring**: the measurement loop — did the defense overlay *detect* the OP-ACT
   chain? Turns the archetypes into scored purple-team exercises.
