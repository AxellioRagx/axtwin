# Work Order — Persona library: schema + loader + catalog + drivers

> **STATUS: DONE — gated live-verify (dev45) PASSED 2026-07-25** for the **scripted** and
> **real-service** drivers (both `os_family`s); the **replay** driver remains unexercised live
> (GAP-033 — no curated pcap template ships with the repo). Built 2026-07-24 (CHANGELOG 2026-07-24
> "Persona library BUILT"). Code (`axrange/personas/` + `axrange/validator/checks/personas.py` +
> `PSN-010..090`), tests (`tests/test_personas.py` + catalog assertions in
> `tests/test_catalog.py`), and docs (`docs/DESIGN_BRIEF_twin_model.md` §5, `docs/VALIDATOR.md`
> §5.12, `docs/CONVENTIONS.md`) are all complete — full suite 1529 passed / 12 skipped / 0 xfailed
> (1541 collected) as of the live-verify pass. **Live-verify (Stage 4 of
> `docs/RUNBOOK_twin_model_live_verify.md`, CHANGELOG 2026-07-25 "Twin-model arc: dev45 live-verify
> Stages 1–4 PASSED"):** 25 `corp-workstation` scripted instances plus the domain-controller
> real-service instance ran on a real 32-host twin; a scripted-driven Windows Scheduled Task survived
> its launching QGA session (drove **two new fixes**: Windows persona drivers —
> `os_family`-dispatched scripted/real-service on Windows, plus a cross-OS `_stop_instances` leak fix,
> LES-119 — and `-ExecutionTimeLimit 0` on the Scheduled Task itself, LES-120); a co-located Stage-3
> tap captured the expected SMB/Kerberos traffic. Two adjacent fixes also landed off this stage:
> optional emit-peers (PSN-090 refinement, LES-123) and the traffic-profile `schedule` override
> wiring (LES-124). Authority: user (2026-07-23).
> **Spec:** `docs/DESIGN_BRIEF_twin_model.md` §5. Route: code-writer → tester → code-reviewer →
> doc-writer → **gated** live-verify (one scripted + one replay persona on dev45).

## 1. Goal

A shared, open **persona library** — benign behavioral archetypes that emit characteristic traffic —
plus the three **drivers** that make code actually produce the traffic. Personas are twin-agnostic and
attack-free (attacker TTPs live in the Exercise tier).

## 2. Build

- **`axrange/personas/`** (new registry submodule, engine vocabulary — mirrors `axrange/sensors/`):
  - persona **schema** + loader (`id`, `os_family`, `drive`, `emits[]`, `requires[]`, `knobs`) per
    brief §5.1; a validator rule set (new `PSN-0xx` category) — dangling `peer_role`, unknown `proto`,
    `drive`/`os_family` enum, `requires` satisfiable against a twin's role set.
  - the **starter catalog** (brief §5.2, ~20 personas) as data files under `personas/catalog/`.
- **Drivers** (`axrange/personas/drivers/` or fold into `config_exec`):
  - **scripted** — render a per-persona in-guest client script from `emits[]` and launch it as a
    **detached, deterministically-named transient systemd unit** (`systemd-run --unit=axr-persona-<safe>
    --collect`) with a fail-loud `is-active` check. **Do not** use `nohup … &` — it is reaped by QGA
    session end (GAP-020/GAP-025, LES on QGA session reaping). Deliver payloads base64-in-command
    (PVE-9.2 wide-char, GAP path).
  - **replay** — stage a captured template pcap, remap addressing to the assigned node's IP, run
    `tcpreplay` as the same transient-unit pattern.
  - **real-service** — a no-op emitter that asserts the node's service daemon is present/running (the
    daemon's own chatter is the traffic); fail-loud if absent.
- All three expose a uniform start/stop/heartbeat the traffic tier drives (this order builds the
  drivers; the traffic tier wires profiles to them).

## 3. Boundaries

`personas/` is engine vocabulary (like `sensors/`), imported downward by the traffic tier — it does
**not** import planner/adapter. No profile/binding logic here (that's `traffic_tier`). No attacker
behavior. Reuse the QGA exec channel via `adapter_proxmox`/`config_exec`, do not open a new transport.

## 4. Validation

- Unit: schema load + `PSN-0xx` rules (each catalog persona validates; a bad persona fails); scripted
  render is deterministic (byte-stable given a seed, LES determinism pattern); driver launch uses a
  transient unit not `nohup`.
- **Gated live-verify (dev45):** on a 1-host twin, run one **scripted** persona (`corp-workstation`
  minimal) and one **replay** persona; confirm each launches as an `axr-persona-*` systemd unit that
  **survives the launching QGA session**, and that a co-located tap sees the expected protocol/ports on
  the wire.

## 5. Depends on / sequence

Depends on `tier_split` (inventory host roster). **Blocks** `traffic_tier` (profiles bind these).
Parallel with `tap_points` + `twin_configs`. Docs: `DESIGN_BRIEF_twin_model.md`, `VALIDATOR.md`
(new `PSN` category — this is a version-locked schema, **confirm + bump** per house rule), CHANGELOG, LES.
