# Work Order — The experiment spine: one run, one clock, one evidence trail, scored at the network sensor

> **STATUS: DRAFT — not started.**
> **Authority:** *user, 2026-09-21* for the framing and the two rulings (§1); *implementation
> (Claude Code)* for this draft — its shapes, slices and acceptance criteria.
> **Extends:** `docs/design/08-exercise-measurement.md` (the design of record this extends — §16
> names six places it contradicts the framing), `13-golden-twin-fidelity.md` §3.1/§6 (that scorecard
> certifies *structure*; this attests *behaviour*), `06-taps-sensors.md` (fan-out, external sinks),
> `07-traffic-personas.md` (the perturbation vocabulary).
> **Version-locked:** **none proposed** — §14 per contract, plus three confirm-first items.
> **Route:** Pipeline (`/dev`) → **gated `dev45` live-verify** (§13), the last slice.
> **Parallel, not yet written:** the **protocol-engine** order (real browsers, real Windows clients,
> deeper server stacks) — the expensive axis, **not** built here. This is the spine it is measured by.

## 1. The framing

**The topology-discovery control loop's job ends once the twin's topology is settled** — discovered
from the wire, then manually edited on the canvas. From there the twin converges toward nothing. It
is a **standing baseline emulation of the real network, with protocol correctness**, held still on
purpose and used as an **instrument**: *extend* the baseline (more nodes, more protocols), *inject
behavioural drift* (persona changes), *inject cyber attacks*, and **evaluate the sensors' ability to
identify these anomalies**. **The twin is not a traffic-generation product**, and **"record once /
replay many" is rejected as the operating model**: traffic is generated live by personas, and what
repeats is not a recording but the **attested baseline** (§6).

**The two rulings (Authority: user, 2026-09-21).** (1) **The NETWORK sensor is the primary device
under test**; host-side detection (Wazuh, host audit) is secondary — collected and scored, never the
thing the experiment is built around, so every metric in §7 must be computable from a network
sensor's alert stream alone. (2) **Drift and attack share ONE evidence spine** — one run id, one
marker format, one time base; two *kinds* of one object (a perturbation), measured by one scorer
against one baseline in one evidence bundle.

**The one thing this must never do: report a detection number whose time base is unmeasured.** The
join today calls a technique *covered* when a detection lands within a Δ of a truth marker — truth ts
from a guest's `date -u`, detection ts from the sensor's clock, **nobody measuring the offset**. A Δ
narrower than the unmeasured skew is a coin flip that reads like a measurement. §4.2 publishes the
skew and derives a Δ floor; a scorer that cannot state its floor emits `not-computed` with a reason,
never a percentage.

## 2. What exists, and the four gaps

Exists: the `exr` attack playout; the truth marker `[axr-truth] run= technique= host= ts=`
(`config_exec/render.py::render_activity_runner` → Loki); `measurement/detections.py`; the
recall-only join in `measurement/coverage.py`; `playout_hash`; the standing `te`/`tfc`/`tap` tiers;
structural certification (`fidelity/`, `te scorecard`). **Four gaps: (A)** nothing binds a run — an
experiment is four unrelated deploy rows; **(B)** no phases and no clock — no declared baseline
window, no statement of when deploy-time noise stops, and nothing measures offset between guest,
sensor and orchestrator; **(C)** recall only — an alert matching no truth marker is *"excluded from
the join, not a false miss"*; **(D)** drift has no representation at all.

## 3. The experiment record

**Recommendation: a file artifact**, on the scorecard's precedent, at
`<twin-config-dir>/experiments/<experiment_id>/experiment.json`; **no state-store table, no
`SCHEMA_VERSION` bump** (§14). `experiment_id` = `exp-<8hex>` over (twin deploy id, spec hash, start
time). An experiment spans four tiers' deploys, so it is not a deploy: a `deploys`-adjacent table
would be a fifth id space with its own lifecycle, teardown-matrix row and schema bump — a
version-locked risk trigger bought for a lookup a directory scan answers. The cost is **no
cross-process guard**, mitigated by the per-twin lease already gating any apply plus a **refusal to
open an experiment whose DUT set intersects a live one's** (§9) — advisory, and said so.

```jsonc
{"experiment_version": "1", "experiment_id": "exp-3f9a21c4", "label": "beacon-drift vs suricata+xpress",
 "twin": {"deploy_id": "te-corpapcn-twin-0e4f7d58", "ir_hash": "…", "plan_id": "…",
          "twin_config": "…/corpapcn-twin.yaml", "scorecard_ref": "…"},
 "baseline": {"attestation_id": "bl-…", "attestation_hash": "sha256:…", "mode": "compare|attest"},
 "bound_deploys": [{"tier": "te|tap|tfc|exr", "deploy_id": "…",  // every tier this run holds still
     "role": "environment|capture|background|perturbation", "profile_hash": "…", "playout_hash": "…"}],
 "duts": [ /* §8 */ ],
 "phases": [{"name": "warmup|baseline|perturbation|cooldown", "start": "…Z", "end": "…Z",
             "measured": false}],                   // four, ordered, non-overlapping
 "perturbations": [{"pid": "p1", "kind": "drift|attack", "drift": "beacon-timing",  // ONE list, §10
                    "technique": "T1558.003", "targets": ["ws01"], "persona_delta": {...},
                    "exr_deploy": "…", "start": "…Z", "end": "…Z"}],
 "clock": { /* §4.2 */ }, "evidence": {"bundle_dir": "…", "manifest": "MANIFEST.json"},
 "results": {"results_ref": "results.json", "verdict_summary": {...}},
 "quiet_check": {"last_bound_deploy_completed_at": "…Z", "baseline_start": "…Z",
                 "settle_s": 900, "ok": true}}      // §4.1
```

`profile_hash` / `playout_hash` make a later re-run provably the same perturbation against the same
background — the claim §16.3 says 08 makes too cheaply today.

**Surface.** `axrange exp {validate,plan,run,status,report,destroy}` — a CLI group, **not** a deploy
tier (it mints no resources, adds no tier tag to `naming.py`) — plus
`axrange te experiments [DEPLOY_ID] [--json]`, a standalone deploy-scoped verb on `te inventory` /
`te scorecard`'s precedent. `exp run` opens the record, drives the phases and **binds** existing
`tfc`/`exr` applies rather than reimplementing them; `exp report` recomputes §7 over a written bundle
with no cluster. API peers: `GET /api/deploys/{id}/experiments`, `GET /api/experiments/{eid}`,
`…/results`, `POST /api/experiments` (async, lease-guarded). CLI and API share **one** helper
(LES-037).

## 4. Phase spine and clock discipline

### 4.1 Four phases; deploy noise excluded by construction

`warmup` (unmeasured) → `baseline` (measured) → `perturbation` (measured) → `cooldown` (unmeasured).

- **warmup** ends only when **all** hold: every `bound_deploys[]` row is `completed`; `settle_s`
  (default 900) has elapsed since the **latest** `completed_at` across them; an optional readiness
  probe passes. That is the by-construction exclusion — installs, domain joins, first boot and any
  `--reapply` land before that `completed_at`, so they sit *structurally* outside the baseline window
  rather than being filtered out after. `quiet_check` publishes the three numbers.
- **baseline** — the measured window of normal (§6). Shorter than one cadence period of the slowest
  bound persona is a **loud refusal**: it measures nothing and would silently set an absurd band.
- **perturbation** — drift and/or attack, each entry carrying its own start/end; overlaps are
  recorded and their attribution marked `ambiguous`. **cooldown** — unmeasured drain, so a late
  detection lands in the bundle without polluting a measured window.

Phase boundaries are **emitted into the same marker stream** (§5), so edges and attack steps order
against each other by one clock in one Loki stream; `phases[]` is written **from the emitted
markers**, so if the marker did not land the phase did not happen. **This is not op-level phase
gating** — `docs/OP_CATALOG.md` §9 keeps `only_in_phase` deferred and the catalog phase-free: no op
gated, no DAG barrier, no catalog entry changed.

### 4.2 Measuring the clock

Three domains, none measured today: **orchestrator** (reference), each **twin host** (mints `ts=` via
`date -u`), each **sensor**. **Method — NTP-style four-timestamp over channels that already exist:**
record `t1`, run `date -u +%s.%N` in the target over QGA (the channel the scorecard probe and
`verify-capture` use), read `t2`, record `t3`; `offset ≈ t2 − (t1+t3)/2`, `rtt = t3 − t1`. Taken per
target at **warm-up end and cooldown start**, so intra-run drift is the difference between the pair,
not assumed zero. `clock` publishes `reference`, `probes[]` of `{target, kind, offset_s, rtt_s,
method, at}`, `max_abs_offset_s`, `max_rtt_s`, `unmeasured_targets[]` and
**`delta_window_floor_s = 2 × (max_abs_offset_s + max_rtt_s)`**. **The scorer refuses a Δ below the
floor** and never reports TTD finer than it.

An **external sensor is unmeasurable by construction** — axRange has no channel into an
operator-owned box and never attempts one — so its probe is `offset_s: null` with a `reason` and
every metric from its timestamps carries `clock: unmeasured` (LES-089's loud-skip posture, the one
`verify-capture` takes for those sinks). An out-of-band offset is `method: operator-supplied`.

## 5. One marker family

The existing marker is consumed by `coverage._TRUTH_MARKER_RE`, by the Grafana coverage dashboard's
LogQL and by every shipped truth log. **Its shape does not change.** The family gains members; the
existing member gains a suffix.

```
attack step   [axr-truth] run=<run> technique=<T> host=<h> ts=<iso>  exp=<eid> phase=<p> pid=<pid>
phase edge    [axr-mark] kind=phase run=<run> exp=<eid> phase=<p> edge=start|end host=<h> ts=<iso>
drift         [axr-mark] kind=drift run=<run> exp=<eid> phase=<p> pid=<pid> drift=<kind> host=<h> ts=<iso>
```

Three properties, each pinned by a test. **(1)** The attack marker's first four keys keep their order
and spelling: `_TRUTH_MARKER_RE` uses `search`, is unanchored and ends in `ts=(\S+)`, so appending
` exp=… phase=… pid=…` leaves it matching with identical captures. **(2)** Non-attack kinds take a
different tag — they carry no `technique=`, and forcing them through `[axr-truth]` would break the
regex or need a sentinel technique `join_coverage` scores as a permanent *miss*; `[axr-mark]` is
invisible to the old parser by construction. **(3)** One stream, one shipper, one run id (ruling 2).

**The run id must be injected, not minted.** `render_activity_runner` mints `RUN_ID` per *execution*,
so two fires are two run ids and nothing can name the run beforehand. The runner gains an injected
`RUN_ID` (minting one when absent, so every existing caller is unaffected) and the experiment passes
`run=<experiment_id>` plus a per-fire `fire=<n>`. One change inside `config_exec/render.py`; §14
flags it confirm-first.

**`coverage.py` does nothing breaking.** Every existing name keeps its signature and semantics.
Added alongside: `parse_marker(line) -> TruthMarker | PhaseMarker | DriftMarker | None`, dispatching
on tag and reusing `_parse_ts` unchanged so the naive-UTC contract holds for all three kinds; the two
new frozen dataclasses; and a note that `join_coverage` stays the **host-agent** join — the
network-sensor join lives in the scorer (§7), its host-matching rule being genuinely different.

## 6. Baseline attestation — the twin's behavioural identity

The scorecard certifies **structure**; the attestation certifies **behaviour** — what the twin sounds
like on the wire when nothing is wrong, measured **at the sensor**, frozen and hashed. Neither
subsumes the other (§16.5).

**The census — per class, at the sensor.** The unit is a traffic class, not a flow:
`{src_role, dst_role, proto, dst_port, transport, direction(ew|ns), segment, flows, bytes, pkts,
iat_p50_s, iat_cv, distinct_pairs, band:{flows:[lo,hi], bytes:[lo,hi]}}`. Source, in preference
order: a Zeek `conn.log` / Suricata `eve.json` flow record when the DUT produces one, else a reducer
over the §9 pcap slice — a **pure leaf over a normalized flow record**, swappable and fixture-
testable. **Role attribution needs IP → host → role**, the same join the alert normalizer needs (§8):
build it **once** as a shared resolver over `build_twin_inventory`'s `hosts[].nets[]`.

**Bands.** ≥3 windows: observed min/max widened by a declared policy factor. One window: that window
±50%, stamped `band_policy: "single-window-default"` — the same ±50% the fidelity spec's row 6 uses,
so the two artifacts do not invent competing tolerances; every comparison carries the policy string.
**Freezing:** `attestation_hash = sha256` over canonical JSON of sorted `classes[]` (bands included),
`band_policy`, `window_len_s`, `windows_n`, `ir_hash`, `persona_set_hash`, the sorted DUT id set and
`attestation_version`, stored at `<twin-config-dir>/<id>.baseline.<attestation_id>.json`, in git
beside the twin config and its scorecard.

**Comparing.** A later window classifies each class `within` / `deviation` (signed magnitude + which
metric) / `missing` / `novel`. **`novel`/`deviation` is the experiment's own ground truth for a drift
injection** — if the catalog says a perturbation should produce a new class and the census says
`novel`, the injection worked, whether or not any sensor noticed. A comparison whose `ir_hash` or
`persona_set_hash` differs is **refused by default**, naming the field (`--allow-mismatch` records
rather than hides it). Whether a persona change *should* invalidate one is **D2** (§15).

## 7. The scorer — network-sensor first

New module: pure, unit-testable from fixtures, **no Loki client and no cluster in the pure layer**
(I/O in one thin reader, as `fidelity/runner.py` isolates its QGA holder).

| Metric | Definition | Today |
|---|---|---|
| **recall** | truth markers with ≥1 matching alert within Δ / all — per perturbation, technique, DUT | yes, host-keyed |
| **precision** | alerts matched to a perturbation / all alerts in the measured window, per DUT | **no** |
| **FP floor** | alert rate/min in the **baseline** phase, per DUT per signature — the sensor's own noise | **no** |
| **excess alert rate** | perturbation-phase rate − FP floor, per DUT per signature | **no** |
| **TTD** | `alert.ts − marker.ts`, offset-corrected, p50/p90/max, never finer than the Δ floor | **no** |
| **breakdowns** | every metric by protocol, segment, direction (`ew`/`ns`), perturbation kind, **DUT** | **no** |

**The FP floor is what makes precision honest.** A sensor that alerts constantly scores high recall
trivially; its baseline-phase rate against the *same* twin, subtracted, is the only comparable
number — what Gap C names and the existing join discards by design.

**DUT comparability.** The tap fan-out feeds **identical mirrored traffic** to every bound DUT, so a
per-DUT rollup over one perturbation set is a direct comparison: same traffic in, different alerts
out. `results.json` publishes the per-DUT table plus `traffic_identical: true|false`, **checked
against the tap plan's resolved bindings**, not assumed.

**The host-matching rule a network alert needs.** A network alert has `src_ip`/`dst_ip`, not a
hostname. A truth marker matches when `marker.host ∈ {alert.src_host, alert.dst_host}` after §6's
resolver maps both endpoints, **and** the technique matches **or** the marker is a drift marker (no
technique — matched by window, endpoint and class). A **new join in the scorer**.

## 8. Sensors under test — registry and alert normalization

**Beside `axrange/sensors/`, not inside it.** That registry is **engine vocabulary** — the validator
imports it (REF-210) and the sensor lifecycle resolves types and images through it. An alert channel,
field map and normalizer are **O2-side analysis**; putting them there drags alert parsing into the
validator's import graph, a straight LES-025 violation. The DUT record therefore sits
**beside** it and *references* the registry for the type vocabulary (`resolve_type`); an
unknown/vendor type resolves to `vendor` with an explicit normalizer.

A DUT record is `{dut_id, kind: standing|external, sensor_type, host_id, external_sensor_id,
primary, alerts: {channel: loki|file|http, location, normalizer, ts_format, field_map}}`. **DUTs are
declared in the experiment spec, not in `adapter.conf`:** an external sensor's *existence* is already
an `adapter.conf` fact (`EXTERNAL_SENSORS`) and stays there, while being a device under test is a
property of one experiment; an entry naming an `external_sensor_id` inherits its transport IP and
type. This is how the axquery/fx-cap instances that today run entirely outside the model, and the
XPress-SW external sensor, get first-class representation.

**Normalized alert** — `{dut_id, ts, src_ip, src_port, dst_ip, dst_port, proto, transport, signature,
rule_id, severity, technique, src_host, src_role, dst_host, dst_role, segment, raw_ref}`. `technique`
from `detections.technique_for_detection`; host/role/segment from §6's shared resolver. Normalizers:
`suricata-eve`, `zeek-notice`, `wazuh-alert`, `generic-jsonl` — a vendor box is **data** (a
`field_map`), not code. Timestamps convert to UTC **before** tzinfo is stripped, the failure mode
`coverage.Detection`'s docstring warns a future Loki-backed scorer about.

## 9. The evidence bundle

`<twin-config-dir>/experiments/<eid>/` holds `experiment.json` (the §3 record, the index),
`markers.jsonl` (every parsed marker, all three kinds), `alerts.jsonl` (normalized, all DUTs),
`census.jsonl`, `baseline.json` (the attestation, or a ref + its hash), `clock.json`, `results.json`,
`captures/<dut_id>/…` (the time-window pcap slice) and `MANIFEST.json` — sha256 per file plus
`ir_hash`, `plan_id`, bound deploy ids and tool versions, so a bundle can never be silently
re-attributed to a different twin.

**Captures: time-window slice — do not build OP-MIR-030.** Captures are merged per sensor and
organized by time and tunnel source; per-scenario attribution by GRE key is
`OP-MIR-030 register-mirror-source`, **reserved and unimplemented**, kept deferred by
`docs/OP_CATALOG.md` §9. **A time-window slice suffices for v1** — the record publishes exact phase
boundaries on a measured clock, the twin is standing and singular, and its traffic in that window
*is* the experiment's; OP-MIR-030 would change the gretap keying model for every deploy.
**Where it does not suffice:** two concurrent experiments sharing a sensor, and a sensor also
mirroring a *different* twin — both answered with a guard, not a capability: `exp run` refuses an
experiment whose DUT set intersects a still-open one's, and warns loudly when a bound DUT's tap plan
reaches outside this twin. It serializes; it does not isolate.

## 10. Drift taxonomy

The comparable set of persona-expressible perturbations. Each emits one `[axr-mark] kind=drift` per
target at injection, carries a `drift=` kind, and is recorded in `perturbations[]`.

| `drift=` | Persona / profile change | Census effect (§6) | Sensor could key on |
|---|---|---|---|
| `rate-change` | raise/lower an emit's `volume:` (or pin `bytes:`) | `deviation` on flows/bytes | per-class byte-rate threshold |
| `new-peer` | new `peer_role`, or `peer_scope: all` | `novel` class; `distinct_pairs` jump | first-seen pair, lateral movement, east-west baselining |
| `new-protocol` | new `proto:` on an existing host | `novel` class on that host | protocol-on-host anomaly, unexpected-service sigs |
| `beacon-timing` | cadence → `cyclic:<d>`, low jitter | `iat_cv` → 0 | periodicity/beaconing — the canonical C2 heuristic |
| `volume-spike` | an emit → `burst:<d>` or `bulk` for a window | short, sharp `deviation` | exfil-volume and burst-rate rules |
| `off-hours` | `diurnal` → `24x7`, or a burst in the quiet window | class in an hour with no baseline mass | temporal baselining as a detector |
| `new-external-dest` | an emit to `egress`/`internet`, new port | `novel` class, `direction: ns` | egress anomaly, rare destination, DNS/TLS reputation |
| `credential-misuse` | authenticating as a different principal to an unreached peer | `novel` (src_role, dst_role, auth-proto) | impossible pairing, Kerberos/LDAP auth-source anomaly |

**The blocker:** several are "add protocol X / add peer Y **to an existing node**" — what
`te deploy --update` refuses (**D1**, §15). Rows expressible as a pure **traffic-profile re-apply**
(`rate-change`, `beacon-timing`, `volume-spike`, `off-hours`, and the three `new-*` rows *when the
emit needs no new in-guest software*) are unblocked now, since `tfc` apply is convergent and mutates
no twin. `credential-misuse` and anything needing a new daemon on a standing host are D1-bound.
**S8 builds only the unblocked rows**, recording the rest as blocked-on-D1.

## 11. Module layout and boundaries

All of it lands in **`axrange/measurement/`**, the existing leaf: `coverage.py` (unchanged surface,
plus `parse_marker` and the two new marker classes, S3), `duts.py` + `alerts.py` + `resolve.py` (S4),
`census.py` + `baseline.py` (S5, pure), `scorer.py` (S6, pure — no Loki client, no cluster),
`bundle.py` (S7), `experiment.py` (S1/S2) and `clock.py` (S2, pure estimator + the one probe driver).

**Import direction.** `measurement/` may import `axrange.planner` (`inventory`, `state`, `opview`)
and `axrange.twinconfig`, and — from `clock.py`'s probe driver and `bundle.py`'s capture fetch
**only** — `axrange.adapter_proxmox` (`GuestAgent`, config): the downward reuse `imagebuild/`,
`config_exec/` and `fidelity/runner.py` already make. It **must not** be imported by `axrange.ir`,
`axrange.validator`, `axrange.planner`, `axrange.adapter_proxmox` or `axrange.ops` — pinned by an
import test. `axrange.cli` and `axrange.api` are the only callers, each through one shared helper.
Changes outside `measurement/`: `render.py`'s injected `RUN_ID` + appended marker keys, the `exp`
CLI group, the four API routes.

## 12. Build slices

Independently landable; **S1–S8's acceptance is checkable without a cluster**, and the on-cluster
proof is S9 alone (§13, gated).

| # | Slice | Size | Cost driver |
|---|---|---|---|
| S1 | Experiment spec + record artifact + `exp` / `te experiments` CLI + API | **medium** | none |
| S2 | Phase spine + `quiet_check` + clock estimator (+ probe driver) | **medium** | 2 QGA round-trips per target |
| S3 | Marker family: additive suffix, `[axr-mark]` emitters, injected `RUN_ID`, `parse_marker` | **small** | none |
| S4 | DUT registry + normalizers + shared IP→host→role resolver | **medium** | none |
| S5 | Flow census + baseline attestation + compare | **large** | the pcap-slice reducer; a long baseline is wall clock |
| S6 | Scorer: recall / precision / FP floor / TTD / breakdowns / per-DUT rollup | **medium** | none |
| S7 | Evidence bundle + MANIFEST + pcap time slice | **small** | disk — a full-rate window is tens of GB |
| S8 | Drift catalog — the unblocked rows as traffic-profile deltas | **medium** | one `tfc` re-apply per injection |
| S9 | Gated `dev45` live-verify: one experiment end to end (§13) | **medium** | **the real cost** — a twin held for hours |

**Applies to every slice:** `--json` emits only JSON on success **and** failure (LES-083); malformed
input fails loud with a typed error naming the field (LES-110); state-store reads filter to
`COMPLETED` and `.get()` each field (LES-113/159/096); an empty denominator yields `not-computed`
**with the denominator** (LES-101). Fixtures first, plus one mandatory regression test over the real
`twin-configs-cast/corpapcn-twin/` bundle and a `StateStore` fixture carrying a non-`COMPLETED` op
row and a deploy missing a recently-added input key (LES-185).

**Per-slice acceptance** — the distinctive check each must pin:

- **S1** a spec fixture round-trips byte-identically, `experiment_id` moves when any input does, a
  record refuses to close with a phase missing an `end`, CLI and API emit byte-identical JSON.
- **S2** the estimator matches hand-computed `(t1,t2,t3)`; the Δ floor is recomputed, never stored;
  an unmeasured target yields `null` **with a reason**, never `0.0`; a false `quiet_check` refuses.
- **S3** `parse_truth_marker` returns identical `TruthMarker`s for the legacy and extended lines, an
  `[axr-mark]` line returns `None` from it, `join_coverage` over a legacy fixture is byte-unchanged.
- **S4** a tz-aware RFC3339 alert lands on the same instant as its UTC twin; an unmappable alert
  keeps `technique: null` and **counts in the precision denominator**; an unresolvable IP buckets as
  `unresolved` with a count rather than becoming a silent class.
- **S5** census matches hand-computed flows/bytes/`iat_cv`; the hash is stable under key reordering
  and moves when a band does; compare classifies a fixture exactly as hand-computed.
- **S6** recall over a legacy fixture equals `join_coverage`'s number; a zero-alert DUT scores recall
  0 and precision `not-computed` **with a reason**; a golden `results.json` asserted byte-for-byte.
- **S7** MANIFEST hashes verify; a bundle whose `ir_hash` differs is rejected on read; the slice
  window equals the **measured phase window**; no credential in any bundle file.
- **S8** each kind produces the declared marker and census effect; a D1-blocked kind is **refused at
  validate time with the reason**, never half-applied.

**The expensive axis is not in this order.** Real protocol engines per host (a browser, a Windows
client stack, deeper server daemons) cost RAM and vCPU *per host* across a 138-VM twin and are a
**separate, parallel order, not yet written**. Nothing here assumes them.

## 13. Gated `dev45` live-verify (S9)

**STOP-and-ask before anything that touches the cluster.** One experiment end to end against a
standing twin: bind the existing `te`/`tap`/`tfc` deploys read-only; run all four phases with one
drift (`beacon-timing`) and one attack (an existing technique-labeled activity); two DUTs on the same
fan-out (a standing `suricata-sensor` + one external sink). **Proofs:** four phases as ordered markers
on one clock; `quiet_check.ok` true with the baseline starting after the last bound deploy's
`completed_at` + `settle_s`; clock probes non-null for QGA-reachable targets and `unmeasured` **with
a reason** for the external sink; the attestation minted with a hash stable across a recompute; the
drift showing as `deviation`/`novel` in the census **independently of whether any sensor alerted**;
recall, precision, FP floor and TTD per DUT, TTD never finer than the clock floor; the bundle
verifying against its MANIFEST. **The twin is not destroyed, not `--update`d, not added to.**

## 14. Version-locked contracts

| Contract | Touched? |
|---|---|
| `IR_SCHEMA` | **No.** The experiment spec is a standalone YAML document, loaded the way a tap plan / traffic profile / exercise spec is — none of those is IR. |
| `PLANNER` | **No, as recommended.** No state-store table, no `SCHEMA_VERSION` bump (stays 3), no executor change, no new lock category. **Flag:** overruling §3's file artifact for an `experiments` table is a schema change → bump + **user confirmation**. |
| `OP_CATALOG` | **No.** No new op type; markers come from the existing rendered runner and orchestrator-side QGA writes. **Flag:** a first-class `emit-marker` op (for resumable phase edges) would be a **new op type** → bump + **user confirmation**. |
| `VALIDATOR` | **No.** No new rule; the spec validates with typed parse errors, as `parse_exercise_spec` does. |
| `ADAPTER` | **No.** Clock probe and capture fetch use existing REST/SSH/QGA channels. |
| `IMAGE_MANIFEST` | **No.** No image changes. |

**Three confirm-first items outside the six contracts:** (1) **the `[axr-truth]` marker shape**
(§5) — additive-suffix only with a byte-identical-parse test, but a *shipped consumed shape*, so
`o2/grafana/dashboards/attack-coverage.json` must be re-checked against the extended line before S3
lands; (2) **the injected `RUN_ID`**, backwards-compatible by fallback but still a change to a
rendered artifact every existing activity deploy produces; (3) **`exp` as a CLI group that is not a
tier** — no tier tag, no teardown-matrix row; making it a real tier is the `tier_split` machinery.

## 15. Open decisions — recommendations only, **not decided here**

### D1 — In-place mutation: how does "add protocol X to existing node Y" become possible?

`te deploy --update` adds/removes **whole nodes**; field-level mutation is **refused, never redone**
(`docs/WORK_ORDER_twin_incremental_update.md`; `PLANNER` §9.2's redo-rules table is unbuilt). So
drift kinds that add a protocol, daemon or principal to a **standing** host are blocked (§10), and
the twin cannot be *extended* the way the framing's first lever describes without a redeploy — which
destroys the baseline the experiment holds still.

- **(a) Open §9.2's per-field redo rules** — most capable; largest and riskiest, every field needing
  a redo rule with a correct blast radius.
- **(b) Extend-by-adding-nodes only** — already works, and behaviourally wrong for the cases that
  matter: a workstation that starts speaking something new **is** the drift, and moving it to a new
  host changes the asset census too, so the comparison reports `novel` on both the class and the
  host — a confounded experiment.
- **(c) A narrow allowlist of mutable fields** — `install[]`, `App.listen`, persona binding, local
  users — each with a hand-written redo rule; everything else still refused.

**Recommendation: (c)** — unblocks the blocked rows at a fraction of (a)'s cost, keeps the refusal
posture as the default (an unlisted field still fails loud, the property that made `--update` safe),
and each entry is a down payment on (a). *Risk trigger:* `PLANNER` §9.

### D2 — Baseline attestation scope: per twin, or per (twin, persona set)?

- **(a) Per twin** — one attestation per `ir_hash`; a persona change is scored *against* it as drift.
- **(b) Per (twin, persona set)** — the attestation hashes `persona_set_hash` too (§6 assumes this);
  changing personas mints a **new** attestation and the old stays valid for its own set.

**Recommendation: (b), with (a) available as a mode.** The persona set *is* the behaviour being
attested — one surviving a wholesale persona swap attests nothing — so identity must include it. But
the §10 drift levers are *themselves* persona changes, so (b) taken literally mints a fresh baseline
per injection and makes comparison impossible. The resolution is a distinction the spec carries: a
**profile-level** change (swap which personas are bound, change the roster) mints a new attestation;
a change declared in `perturbations[]` is scored against the existing one and does **not** invalidate
it. That needs the user's ruling: it decides whether a change is "a new normal" or "an anomaly", and
no code can decide that.

## 16. What in the existing design contradicts the framing

Named, not silently reconciled — each needs a `docs/design/08-exercise-measurement.md` edit, and one
needs a `Supersedes:`-marked CHANGELOG line.

1. **08's join is host-keyed, which a network sensor cannot satisfy** — it joins on
   `(technique, host, Δ)`, while a network alert carries IP endpoints and often no host attribution,
   so under ruling 1 the *primary* DUT is the one whose alerts do not fit the key. §7's endpoint-set
   rule and §6's resolver reconcile it.
2. **08 discards the false-positive floor by design** (*"excluded from the join, not a false miss"*)
   — right for a recall-only matrix, wrong where the unmatched alerts **are** the measurement.
3. **08 claims comparability from `playout_hash` alone** — same attack, yes; same *measurement*, no,
   once live personas generate a different background. 08 should require **both** a matching
   `playout_hash` and a baseline within band.
4. **"drift" is already taken** — `13-golden-twin-fidelity.md` §6 uses it for *structural/spec* drift
   (a re-imprint at a later PIT). **Proposal:** `perturbation` as the umbrella, `drift` keeps its
   behavioural meaning here, spec 13's box renamed **`spec-drift`** — a rename in a ratified file, so
   it needs the user's nod and a `Supersedes:` line.
5. **The twin's two identities are undistinguished** — structure is certified, behaviour is not, and
   §6's attestation is a *second* certificate; 08 and 13 should each say which question each answers.
6. **08's `measurement.run_label` is informational** — it looks like a run identity and is not one;
   under §3 the experiment owns the run id and `run_label` is the human label.
