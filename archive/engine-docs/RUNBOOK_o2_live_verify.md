# Runbook — batched O2-arc live-verify (dev45)

> **For the gated `live-verifier` stage.** Authority: user (2026-07-14). Drives the six
> pending gated `dev45` live-verifies as **one session** on the shared standing surface, in
> dependency order. Every `axrange te deploy`/`destroy`, `axrange o2 deploy|destroy`, `axrange tap
> deploy|destroy`, every `docker compose`, and the `state.db` re-init **STOP and ask for approval
> first** (shared PVE 9.2 node, real VMs). Reads/`validate`/`plan`/`status`/inventory queries run
> unattended.

> **Executed 2026-07-15 (Authority: user — "Full A1→A4", approved banking the core + deferring
> A3/A4):** **A1, A2 + sensor-agnostic-`te`, and SCHED all PASSED** — see each work order's header
> and CHANGELOG 2026-07-15 for full evidence (deploy-ids, resource counts). **A3/A4 DEFERRED**, not
> failed — blocked by three O2-compose-stack bring-up problems on the `ragx-dev` orchestrator host's
> first real podman-rootless run (Caldera silent 500s, Wazuh filebeat crash-loop, a stale
> rootlessport holding port 9090), tracked as `docs/PRODUCT_GAPS.md` GAP-016/017/018 — none of them
> axRange engine/o2control defects. Re-attempt §5/§6 once those are fixed.
>
> **Two operational caveats surfaced during this run, worth reading before the next one:**
> - **A tap-plan's `target:` is a plan-hash-derived deploy-id, not a stable name** (LES-072). Any
>   edit to the target TE's IR — even one unrelated to tapping, like flipping
>   `scenario.default_agents: false` — changes its deploy-id and silently orphans every tap-plan
>   file's hardcoded `target:`. Resync tap-plan `target:` fields by hand after any TE edit; don't
>   assume a previously-working tap-plan file is still pointed at the right deploy.
> - **A verify TE without a router/egress must set `scenario.default_agents: false`** (LES-070,
>   GAP-015) — the default-ON exporter agent apt-installs over egress a minimal/isolated verify TE
>   typically doesn't have, and will hang the deploy for up to 600s per host otherwise. Set the flag
>   up front for any focused verify TE that isn't itself testing agent enrollment.

## What this covers

Six pending live-verifies, all built + suite-verified, gated only on approval + ordering:

| Phase | Work order | Header status | Depends on |
|---|---|---|---|
| **A1** | `WORK_ORDER_o2_control.md` §5 | DONE (build) 2026-07-11 | keystone (state.db re-init) |
| **A2** | `WORK_ORDER_taps.md` §3 | DONE (build) 2026-07-11 | A1 inventory |
| **TE** | `WORK_ORDER_sensor_agnostic_te.md` §5 | DONE (build) 2026-07-13 | A1 + A2 (folded into A2 below) |
| **SCHED** | `WORK_ORDER_schedule.md` §4 | DONE (build) 2026-07-11 | independent (needed by A4) |
| **A3** | `WORK_ORDER_attack_defense.md` §4 | DONE (build) 2026-07-11 | A1 + Stage-B servers |
| **A4** | `WORK_ORDER_measurement.md` §3 | DONE (build) 2026-07-11 | A3 + activity engine |

**Execution order:** re-init `state.db` → **A1** → **A2 + TE** → **SCHED** → **A3** → **A4** → teardown.
(`sink_rename` §3's remaining gated pass is a separate migration — *not* in this batch.)

---

## 0. Preflight — unattended (no gate)

- [ ] `git branch --show-current` → confirm on the intended branch (`wip/restructure`).
- [ ] Full suite green: `.venv/bin/python -m pytest -q -n auto` (record the count).
- [ ] `grep -E 'DEFAULT_NODE|API_TOKEN' adapter.conf` → confirm target is **dev45**, token present.
- [ ] `axrange te validate` + `axrange te plan` the TEs this run will deploy (see §pick-list) — **plan only, no deploy**. Confirm they plan clean.
- [ ] Pick TEs (below). For any TE used, satisfy **GAP-015**: give it a `kind: router` egress **or**
      set `scenario.default_agents: false`, else the default-ON Prometheus exporter agent assumes
      egress a no-router TE lacks and masks the run.

**TE pick-list**
- **Unrelated/blast-radius TE (A1):** any small archetype, e.g. `soc-detection-lab` — used only to
  prove a TE deploy+destroy leaves the `o2_` surface untouched.
- **Tapped TE (A2 + TE-agnostic):** a TE that declares `tap:` at host/leg/segment. Reuse the prior
  focused `tap-verify`-style TE (small: 2 hosts on one net + a router), or add `tap:` to a small archetype.
- **AD TE (A3 + A4):** `ad-domain-lab` or `purple-team-range` — both carry `overlays.{defense,adversary}`
  + the AD kill-chain `activity`. Needs a `windows-server-2022` DC template on dev45.

---

## 1. GATE 0 — one-time prerequisites (approval required)

**These two must happen before any O2-arc deploy.**

- [ ] **[GATE] Re-initialize `state.db`.** A1 bumped `SCHEMA_VERSION 2→3`; any dev45 `state.db`
      predating 2026-07-11 fails loud (`StateStoreSchemaError`). Move it aside (don't just delete —
      keep it for post-mortem): `mv state.db state.db.pre-schema3.bak`. *Destroys local deploy
      history only; live resources are unaffected. Confirm no in-flight deploy is tracked first
      (`axrange list-deploys`).*
- [ ] **[GATE] Bring up O2 Stage-B servers** (needed by A3/A4; not wired into `o2 up`):
      `docker compose -f o2/compose/docker-compose.yml up -d` → containers
      `axr-prometheus` / `axr-loki` / `axr-grafana` / `axr-caldera` / `axr-wazuh-manager` healthy.
      *(A1/A2/SCHED alone don't need these — bring them up now to avoid a second gate, or defer to before A3.)*

---

## 2. Phase A1 — `o2_control` keystone

- [ ] **[GATE] `axrange o2 deploy`** a 1-sensor surface.
- [ ] Confirm (unattended reads): `o2_`-named resources exist (`o2v*`/`o2br_*`/`o2-*`), a **persistent
      deploy** is in `state.db` (`axrange list-deploys`), and the **inventory exposes the sensor's
      transport IP** (`axrange o2 status` / `/api/o2/surface`).
- [ ] **[GATE]** Deploy the unrelated blast-radius TE → **[GATE]** `axrange te destroy` it → confirm the
      `o2_` surface is **untouched** (sensor VM + `o2_` resources still present; teardown guard fired
      no `guard-violation`).
- [ ] **Leave the surface UP** — A2/A3/A4 ride on it. (Only run `o2 destroy` at final teardown.)

**A1 pass =** `o2_` surface stands, inventory has the transport IP, an unrelated TE destroy left it intact.

---

## 3. Phase A2 + sensor-agnostic-`te` — taps own all wiring

Fold both live-verifies here (same primitives, one tapped TE).

- [ ] Ensure **2 sensors** standing (re-run `o2 deploy` with a 2-sensor surface, or scale) — A2's fan-out
      needs two destinations.
- [ ] **[GATE]** Deploy the **tapped TE**. Confirm the **sensor-agnostic-`te` assertion**: the TE VMs +
      SDN come up but there is **NO** `gretap`/`mirror`/`verify-capture` in the deploy, and a
      **`record-tap-intent`** op is present in `state.db` (grep the deploy's ops). This proves the `te`
      installs no mirrors of its own.
- [ ] **[GATE] `axrange tap deploy <plan.yaml>`** (`target: <te>` in the plan file) with a plan carrying
      **one fan-out tap** (both sensors) + **one exclusive tap** (one sensor). Confirm: A2 discovers each
      sensor's `transport_ip` **and** `vmid` from **A1's inventory** (not `adapter.conf`), installs
      `tapg_*`/`tapm-*`, and **`verify-capture` passes over QGA** with **no `SENSOR_SSH_*` set
      anywhere** → **closes GAP-012**. Fan-out → both verify-captures pass; exclusive → one.
- [ ] **[GATE] `axrange tap destroy`** → TE + sensors **survive**; `tap_` resources gone.
- [ ] **[GATE]** Re-apply a **different** tap plan → re-points correctly (idempotent, no dangling mirror).
- [ ] **[GATE]** `axrange te destroy <te>` → confirm it **cascades its `tap_`** binding (a `te` destroy
      still sweeps taps even though the tap playout is a separate deploy).

**A2/TE pass =** te carries only `record-tap-intent`; A2 wires+verifies from A1 inventory with no
`SENSOR_SSH_*`; fan-out/exclusive both correct; destroy taps leaves TE+sensors; te destroy cascades tap_.

---

## 4. Phase SCHED — activity schedule executor (independent)

- [ ] **[GATE]** Deploy a TE host with **two scheduled activities** (different `interval`) with an
      explicit `seed`.
- [ ] Confirm (reads over QGA / `status`): two independent `act-<id>` timers armed, each **fires within
      its window**, no collision.
- [ ] **[GATE]** Second deploy with the **same `seed`** → reproduces the identical activity sequence
      (the seeded-replay property A4's replay-delta relies on).
- [ ] **[GATE]** `axrange te destroy` the SCHED TE.

**SCHED pass =** two jittered timers fire independently; same-seed redeploy is identical.

---

## 5. Phase A3 — attack/defense (needs Stage-B servers, GATE 0)

- [ ] Confirm `axr-caldera` / `axr-wazuh-manager` / `axr-prometheus` up (GATE 0).
- [ ] **[GATE]** Deploy the **AD TE** with `overlays.{defense,adversary}` (`ad-domain-lab` /
      `purple-team-range`). Confirm on nodes: **Wazuh agent + Sandcat + exporter** present.
- [ ] Confirm agents check in: **Wazuh agent enrolled** (manager UI/API), **Sandcat beacon visible in
      Caldera**, **exporter scraped** by Prometheus.
- [ ] **[GATE]** Run **one manual Caldera ability** + **one engine `activity` attack** → both visible in
      their servers.
- [ ] **Leave this TE UP** — A4 measures against it.

**A3 pass =** both agent→server pairs enroll; a manual Caldera ability and an engine `activity` both land.

---

## 6. Phase A4 — measurement & scoring

- [ ] **[GATE]** Run the **AD kill-chain `activity`** against the A3 TE (`overlays.defense` + standing
      sensors).
- [ ] Confirm both streams reach **Loki**: attack **truth markers** (`[axr-truth] run/technique/host/ts`)
      **and** Wazuh/Suricata **detections**.
- [ ] Confirm the **attack-coverage Grafana dashboard** (`o2/grafana/dashboards/attack-coverage.json`)
      shows the per-technique matrix — e.g. **DCSync detected**, some recon **missed** (join by
      `technique + host + Δ-window`).

**A4 pass =** truth + detections both in Loki; coverage matrix renders per-technique hits/misses.

---

## 7. Teardown — leave dev45 clean

- [ ] **[GATE]** `axrange te destroy` every TE still up (AD TE, any others). Confirm each cascades its `tap_`.
- [ ] **[GATE]** `axrange tap destroy` any lingering tap plans → **[GATE]** `axrange o2 destroy` (sensor
      surface gone; guard left `img-*` templates + unrelated VMs untouched).
- [ ] **[GATE]** (optional) `docker compose -f o2/compose/docker-compose.yml down` if the O2 servers
      were only for this run.
- [ ] `axrange list-deploys` → clean; idempotent re-`destroy` is a no-op.

---

## Gates at a glance

**Stop and ask before each:** `state.db` re-init · `docker compose up/down` · `axrange o2 deploy|destroy` ·
`axrange tap deploy|destroy` · every `axrange te deploy` · every `axrange te destroy` · the manual Caldera
ability. **Unattended:** `te validate`/`te plan`/`te status`/`list-deploys`, `o2 status`, inventory + Loki +
Prometheus + Caldera/Wazuh **reads**, grepping `state.db`.

## After the run (doc-writer)

Per work order, flip each header's **"Still open: §N gated live-verify"** to a live-verify PASS with the
dev45 deploy id + date, and add a CHANGELOG entry per phase (Authority: `implementation (Claude Code)` /
`user` for the run approval). Note the **GAP-012 close** (A2/TE) explicitly. If GAP-015 bit any TE,
record the router-vs-`default_agents:false` choice made.
