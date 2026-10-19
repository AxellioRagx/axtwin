# Work Order — A4: measurement & scoring (ATT&CK coverage)

> **STATUS: DONE (build) — 2026-07-11.** Implemented + code-reviewed + suite-verified (794 pytest
> tests). `technique:` labels on inline steps + `AdversaryEntry.techniques`; the
> `[axr-truth]` ground-truth marker shipped to Loki (shared `_render_loki_tail_shipper` helper);
> `axrange/measurement/` (`join_coverage`, `technique_for_detection`); the Grafana coverage
> dashboard (`o2/grafana/dashboards/attack-coverage.json`). **Still open:** the §3 gated `dev45`
> live-verify; the automated scorer + replay-delta + ATT&CK-Navigator export (out of scope, §4). See
> CHANGELOG 2026-07-11 and `docs/DESIGN_BRIEF_o2_measurement.md` for the built-status record.
>
> **§3 live-verify PASSED — 2026-07-15** (dev45, O2-services VM 102; `docs/WORK_ORDER_o2_server_stack.md`
> §5). The AD kill-chain-style verify TE shipped truth markers (`{job="axr-truth"} technique=T1595
> host=atk1`) and a suricata detection (`{job="axrange-sensor"} sid=9000001`) both into Loki,
> joinable by technique+host+window; the "Attack coverage (A4)" Grafana dashboard renders against
> the Loki datasource. **Two bugs found and fixed in the same run, tracked as `docs/PRODUCT_GAPS.md`
> GAP-020 and GAP-021:** (1) the shared `export.ship: loki` bootstrap (`_render_loki_tail_shipper`,
> `axrange/config_exec/render.py`) self-killed its own delivering wrapper via a `pkill -f` pattern
> that was a substring of the wrapper's own filename, and a plain `nohup ... &` was reaped once the
> QGA exec session ended — fixed with a named `systemd-run --unit=` transient (no `pkill` anywhere);
> 5 new regression tests. (2) the truth-marker shipper runs on the TE host and needs egress to reach
> the O2 Loki, which the verify TE initially lacked — fixed for this run via `egress: true`; whether
> truth markers should instead route via the always-present transport/sensor plane (so an
> egress-isolated TE still gets A4 coverage) is an **open design question**, not decided here. See
> CHANGELOG 2026-07-15 "O2 server-stack live bring-up + A3/A4 gated live-verify: PASSED".

> **For a subagent Pipeline session (`/dev`).** Authority: user (2026-07-10). **Spec:**
> `docs/DESIGN_BRIEF_o2_measurement.md`. Route: code-writer → tester → code-reviewer → doc-writer →
> **gated** live-verifier. **Depends on A3** (attack produces truth) + the `activity` engine.

## 1. Goal

Fill the missing **Tier-4 detection scoring**: an **ATT&CK-technique coverage matrix** per attack
`run-id`, correlating attack **ground truth** vs O2 **detections** — both in **Loki**, joined by
`technique + host + Δ-window`. **Manual-first (Grafana view)**; automated scorer is a follow-on.

## 2. Build (the one new stream + the view)

- **`technique:` label** on activity steps (small IR field on inline steps / `overlays.adversary`).
- **Ground-truth marker** — the **activity runner** (`config_exec/render.py::render_activity_runner`)
  emits one `[axr-truth] run=<id> technique=<T> host=<h> ts=<..>` per step → shipped to **Loki**
  (reuse the Alloy pipeline / a direct push). Caldera/Atomic supply native ATT&CK via their operation
  report → same marker stream. **This is the only new mechanism.**
- **`run-id`** stamped on every attack run (so runs are comparable → the replay-delta later).
- **Detection→technique** — self-label where the Wazuh/Suricata rule carries an ATT&CK id; else a
  small rule→technique fallback map.
- **Grafana coverage dashboard** (`o2/grafana/dashboards/`) — attack timeline (truth) vs detections +
  a **technique-coverage matrix** panel, per `run-id`, over Loki.

## 3. Validation

- Unit: markers render with the right run/technique/host/ts; the coverage join (truth × detections on
  technique+host+Δ) computes covered/missed; run-id stamping. Full suite green.
- **Gated live-verify (dev45):** run the AD kill-chain `activity` against a TE with `overlays.defense`
  (A3) + standing sensors → truth markers + Wazuh/Suricata detections both land in Loki → the coverage
  dashboard shows the per-technique matrix (e.g. DCSync detected, some recon missed).

## 4. Out of scope / deps

Automated scorer (`o2control`) + the **replay-delta** (run1 vs run2) + ATT&CK-Navigator export are
follow-ons. No `te_`/engine change beyond the `technique:` field + the marker emit — A4 is an
O2-side read. **Load-bearing:** the delta relies on **seeded activity** (`WORK_ORDER_schedule.md` /
`SLICE_ACTIVITY.md` §11) for identical replays. Depends on A3.
