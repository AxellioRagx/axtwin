# Work Order — sensor-agnostic `te_` substrate; A2 owns all tap/sensor wiring

> **For a subagent Pipeline session (`/dev`), not this design session.** Authority for the
> direction: **user (2026-07-13)** — the O2 direction (`docs/DESIGN_BRIEF_o2.md`: "taps become a
> separate config playout") already ratified retiring in-deploy tap emission; this work order
> executes it and drops `SENSOR_*` from `adapter.conf`. **Route:** Pipeline mode
> (`docs/WORKFLOW.md`) — code-writer → tester → code-reviewer → doc-writer → **gated**
> live-verifier. Interrupt the design session only on a real decision.

> **Status: DONE (build) — 2026-07-13. §5 gated `dev45` live-verify PASSED — 2026-07-15.** Design
> decided + built same day (CHANGELOG 2026-07-13 "sensor-agnostic `te_` substrate" design + BUILD
> entries; commit `8a91e92`). New op **`OP-MIR-005 record-tap-intent`**; a `te` deploy no longer emits
> `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture` or its own `mirror-ready`; A2 is now the
> sole emitter of the three OP-MIR install ops and discovers the tappable set from
> `record-tap-intent`; `SENSOR_*` removed from `adapter.conf`. Implemented + suite-verified.
> **Live-verify evidence (dev45, 2026-07-15, folded into the A2 live-verify —
> `docs/WORK_ORDER_taps.md` §3):** the tapped-TE deploy `tap-verify-c82bd2b5`'s op list was exactly
> 2× `record-tap-intent` + host/network ops with **ZERO** `attach-gre-tunnel`/`install-ovs-mirror`/
> `verify-capture`/`mirror-ready` ops — proves live that the `te` installs no mirrors of its own. A2's
> subsequent `o2 tap apply` wired capture from A1's inventory with no `SENSOR_SSH_*` set anywhere —
> **GAP-012 is now Resolved (live-verified)**, not just Resolved (build) — see
> `docs/PRODUCT_GAPS.md`. **Depends on** A1 (`o2control`, BUILT + live-verified) + A2
> (`o2control/taps.py`, BUILT + live-verified) — this work order removed A2's dependency on inline
> emission, it did not build A2. See CHANGELOG 2026-07-15.

## 1. Goal

A `te_` deploy stands up **only its own components** (the O2 principle). It carries **no** knowledge
of sensors: no `install-ovs-mirror` / `attach-gre-tunnel` / `verify-capture`, no `SENSOR_*` read from
`adapter.conf`, no `--sensor-ip`. All tap→sensor binding + capture verification is the **A2 tap
playout** (`axrange o2 tap apply <te>`), which already discovers the sensor's `transport_ip` **and**
`vmid` from **A1's standing inventory** and reads the sensor over **QGA** (never `SENSOR_SSH_*`).

End state (the model the user chose, 2026-07-13):
```
axrange o2 up            # sensors standing (o2_ deploy; A1 inventory is the discovery source)
axrange deploy te.yaml   # te_ substrate ONLY -- records tap-INTENT, installs no mirrors
axrange o2 tap apply te  # A2 discovers sensor.transport_ip + vmid from A1, wires OP-MIR, verify/QGA
# adapter.conf: no SENSOR_TRANSPORT_IP / SENSOR_SSH_* / NETWORK_SENSOR_*
```

## 2. The one non-obvious dependency (the additive piece)

Today A2's `_scan_te_inventory` (`axrange/o2control/taps.py`) discovers *what is tappable* by reading
the te deploy's **own `install-ovs-mirror` ops** (`orig_entries` ← `inputs_resolved["tapped_nics"]`).
So A2 currently **rides on top of** inline emission — the te installs taps against a default/config
sensor and A2 *re-points* them ("relabel, not re-plumb"). Removing inline emission therefore strands
A2's discovery source. The fix is **additive**: the te deploy must still **persist its resolved
tap-intent** — the `(host, net_id, nic_index)` tappable set from the dynamic-tap resolver
(`docs/DESIGN_BRIEF_dynamic_tap.md` par.4) — as a **no-adapter-action** state artifact A2 can read.

**Primary approach (keep A2's "derive from persisted ops, not IR" posture):** add a lightweight op
**`OP-MIR-005 record-tap-intent`** (proposed id) — `idempotency_class` pure/declarative, **no adapter
call**, `outputs` = the resolved tapped set (`[{host, net_id, nic_index, ...}]`) persisted to
`state.db`. One per te deploy (or per host). A2 reads it instead of `install-ovs-mirror.tapped_nics`.
- *Alternative considered & rejected for now:* have A2 re-parse the TE's IR for `tap:` intent. Rejected
  to preserve the deliberate "no IR, no manifest — derive from persisted state" posture A2/A1 share
  (`taps.py` header; survives IR drift, matches A1's `_inventory_from_ops`). Implementer may revisit if
  the record-op proves heavier than re-parse, but default to the record op.

## 3. Code changes

- **`axrange/planner/pass1_emit.py`** — stop emitting `install-ovs-mirror`, `attach-gre-tunnel`, and
  `verify-capture` for te taps (`_emit_verify_capture` + the OP-MIR emission around par.1205). Emit
  `record-tap-intent` instead (the resolved tapped set). The `mirror-ready` barrier in the te deploy
  drops (no mirrors to gate) — confirm nothing else in the te plan orders on it.
- **`axrange/ops/` + `docs/OP_CATALOG.md`** — add `OP-MIR-005 record-tap-intent` (39th op) with full
  `inputs`/`outputs`/`after`/`cleanup_strategy: none`/`idempotency_class`/`resource_categories: []`
  signature. **Version-locked schema — bump `OP_CATALOG` version + confirm** (per "Design Document
  Maintenance"). `verify-capture` (OP-MIR-040) stays in the catalog but is now **emitted only by A2**,
  not the te planner — record that emission-context change in its entry.
- **`axrange/o2control/taps.py`** — repoint `_scan_te_inventory` to read the `record-tap-intent`
  output for the tappable set (`orig_entries` / `_tapped_iface_set`) instead of `install-ovs-mirror`.
  `qga_hosts` preference signal: derive from the te's `wait-os-ready`/`clone-vm` Linux hosts (it no
  longer has a te-side `verify-capture` to read). A2 already emits its own `verify-capture` — unchanged.
- **`axrange/adapter_proxmox/config.py`** — remove `sensor_transport_ip`, `sensor_ssh_*`,
  `SENSOR_CAPTURE_IFACE`, `NETWORK_SENSOR_*` loading + the `sensors` map seeded from `SENSOR_*`. The
  cluster's sensor identity now comes solely from A1's inventory at tap-apply time.
- **`axrange/adapter_proxmox/ssh.py` + `axrange/config_exec/backend.py`** — delete the `SENSOR_SSH_*`
  SSH `verify-capture` branch (the "no `sensor_vmid` ⇒ SSH fallback"). Under the standing-O2 model a
  sensor always has a vmid; the QGA path is the only path. (If a truly-external, non-O2 sensor must be
  supported later, that is a *separate* explicit escape hatch, not the default — do not keep it "just
  in case.")
- **`axrange/cli/main.py` + `axrange/api/service.py`/`dtos.py`** — remove `--sensor-ip` /
  `sensor_transport_ip` from the te deploy path (validate/plan/deploy + the API deploy DTO). Keep A2's
  own inventory-driven wiring untouched.
- **`axrange/validator/catalog.py`** — revisit the rule at ~line 348 (the verify-capture-injector
  requirement) now that `verify-capture` leaves the te deploy; a tapped te needs a *tappable*
  interface recorded, not a te-side heartbeat host. **Version-locked schema — bump `VALIDATOR` version
  + confirm** if a rule is added/removed/retargeted.
- **`axrange/imagebuild/conformance.py`** — its smoke-test deploy passes `sensor_transport_ip`; it runs
  an **untapped** deploy, so just drop the arg (no capture in the smoke test).

## 4. Validation

- Update tests asserting inline tap/verify-capture emission in the te plan
  (`test_planner_executor.py`, `test_sensor_routing.py`, `test_sensor_exec.py`, dynamic-tap tests) to
  the new shape: te emits `record-tap-intent` + **no** mirror/gretap/verify-capture; A2
  (`test_o2_taps.py` or equivalent) discovers the tappable set from `record-tap-intent`.
- Add: (a) a tapped te deploy emits `record-tap-intent` and **zero** OP-MIR install ops; (b) A2
  `o2 tap apply` against that te discovers the same tappable set it used to get from `install-ovs-mirror`
  and wires mirrors/gretap/verify-capture correctly; (c) no code path reads `SENSOR_*` from config.
- Full suite green (currently 794; expect a small net change).

## 5. Live cluster (dev45) — gated live-verifier only

**PASSED — 2026-07-15.** `axrange o2 up` (stand sensors) → `axrange deploy <tapped-te>` (confirmed: te
VMs + SDN + **no** gretap/mirror/verify-capture; `record-tap-intent` present in state) → `axrange o2
tap apply <te>` (confirmed: A2 discovers the sensor from A1, installs `tapg_*`/`tapm-*`,
`verify-capture` passes over QGA with **no** `SENSOR_SSH_*` set anywhere) → `axrange o2 tap destroy` /
`axrange destroy <te>` (cascade confirmed). Idempotent re-runs clean. Full run detail in
`docs/WORK_ORDER_taps.md` §3 (folded, same tapped TE) and CHANGELOG 2026-07-15.
- **This is the proper close of GAP-012** (verify-capture no longer depends on `SENSOR_SSH_HOST`) —
  `docs/PRODUCT_GAPS.md` GAP-012 flipped to Resolved (live-verified). GAP-015 (default-ON exporter
  egress) was hit and mitigated by setting `default_agents: false` on the verify TEs (they don't
  exercise agent enrollment) — GAP-015 itself remains open, see `docs/PRODUCT_GAPS.md`.

## 6. Docs to update (doc-writer stage)

`docs/OP_CATALOG.md` (+version bump), `docs/VALIDATOR.md` (+version bump if rules change),
`docs/DESIGN_BRIEF_dynamic_tap.md` (inline emission **retired**; te records tap-intent only),
`docs/DESIGN_BRIEF_o2_taps.md` (A2 is now the **sole** tap installer, not an over-the-top re-point),
`docs/DESIGN_BRIEF_o2.md` §6 seam note, `docs/ADAPTER.md` (capture plane; `SENSOR_*` removed),
`adapter.conf.example` (drop the `SENSOR_*`/`NETWORK_SENSOR_*` block), `CLAUDE.md` (Operational Notes
"Capture is merged per sensor" + the SDN/capture bullets), `docs/PRODUCT_GAPS.md` GAP-012 → Resolved,
a `LES-NNN` if a gotcha emerges, and a CHANGELOG entry.

## 7. Out of scope

- Building A1/A2 themselves (already BUILT). This only removes A2's reliance on inline emission.
- A truly-external / non-O2 sensor escape hatch — deliberately dropped; reintroduce only on explicit
  request as a named opt-in, not a silent fallback.
- The standalone `destroy taps` verb already exists (A2); no change here.
