# Work Order — A2: the tap config playout (`tap_` tier)

> **STATUS: DONE (build) — 2026-07-11. §3 gated `dev45` live-verify PASSED — 2026-07-15.**
> Implemented + code-reviewed + suite-verified (794 pytest tests at build time, 792 current).
> `axrange/o2control/taps.py` realizes the tap-plan as a separate `tap-<te>` deploy; CLI
> `axrange o2 tap apply|destroy`; API `/api/o2/taps`; a `te` destroy cascades its tap-plan. **Still
> open:** `axrange o2 tap status` (deferred, not in scope); packetXpress (separate design).
> **Live-verify evidence (dev45, 2026-07-15):** tapped TE `tap-verify-c82bd2b5` deployed with zero
> mirror/gretap/verify-capture ops (confirms `docs/WORK_ORDER_sensor_agnostic_te.md`); tap-plan
> deploy `tap-tap-verify-c82bd2b5-9c0c7097` applied plan1 (fan-out h-a→[o2-tcpdump,o2-suricata],
> exclusive h-b→[o2-suricata]) — 2× `attach-gre-tunnel` + 2× `install-ovs-mirror` + 2× `verify-capture`
> all completed, with **no `SENSOR_SSH_*`/`SENSOR_TRANSPORT_IP` anywhere in `adapter.conf` or env**
> (closes GAP-012, see `docs/PRODUCT_GAPS.md`); plan2 re-point idempotent; `o2 tap destroy` removed
> the 2 `tapg_` gretaps while the TE + both sensors survived (confirmed via `ovs-vsctl`: no `tapm_`
> mirror left on `tebr_869293`); re-applying plan1 then `axrange destroy tap-verify-c82bd2b5` swept 7
> te resources AND auto-cascaded the tap-plan's 2 gretaps (LES-058 confirmed live). See CHANGELOG
> 2026-07-15.

> **For a subagent Pipeline session (`/dev`).** Authority: user (2026-07-10). **Spec:**
> `docs/DESIGN_BRIEF_o2_taps.md`. Route: code-writer → tester → code-reviewer → doc-writer →
> **gated** live-verifier. **Depends on A1** (`WORK_ORDER_o2_control.md`) — the sensor inventory.

## 1. Goal

Build the `tap_` tier's own lifecycle: a **tap-plan** binds a deployed TE's tappable interfaces to
standing O2 sensors, realized by **reusing the OP-MIR ops** via an O2-control playout — independently
**apply/destroy/redeployable**, without touching `te_`/`o2_`.

## 2. Build

- **Tap-plan spec** — `{ target: <te-deploy-id>, bindings: [ {tap, sensors: [...]} ] }` (F3);
  `sensors:` resolve via the **A1 inventory** to transport IPs; the tappable set defaults from the
  IR `tap:` markers (`docs/DESIGN_BRIEF_dynamic_tap.md`).
- **Playout run** (in `o2control`, reuse the executor + adapter, Model-C): per (bridge, sensor) a
  keyed gretap `tapg_<6hex>` (`attach-gre-tunnel`, remote = sensor transport IP); per tap a `tc`
  clsact + **chained `mirred`** `tapm-…` to that tap's sensor gretaps (`install-ovs-mirror`; append,
  never replace — LES-011); `verify-capture` per destination. All resources **`tap_`-tagged**.
  - **Fan-out (1:N)** = N mirred on one tap → N gretaps. **Exclusivity** = each tap → its one sensor.
- **Verbs:** CLI `axrange o2 tap apply --plan FILE | destroy [--target TE]`; API
  `POST/DELETE /api/o2/taps`. `destroy taps` removes **only** `tap_` resources (the standalone verb
  that `WORK_ORDER_resource_tags.md` §7 deferred to here). Re-apply a new plan re-points.

## 3. Validation

- Unit: fan-out emits N gretaps/mirred; exclusivity routes per-tap; distinct GRE key per
  (bridge,sensor) (no collision); `tap_` tagging; idempotent re-apply; `destroy taps` leaves
  `te_`/`o2_`. Full suite green.
- **Gated live-verify (dev45):** stand up 2 sensors (A1) + a tapped TE → `tap apply` a plan with one
  **fan-out** tap (both sensors) + one **exclusive** tap → both/one verify-capture pass → `destroy
  taps` (TE + sensors survive) → re-apply a different plan → re-points. A `te_` destroy still cascades
  its `tap_`.

## 4. Notes

Fan-out **duplicates** mirrored packets on the transport wire (capacity/MTU — source-side). VM sensor
types are interchangeable gretap endpoints (packetXpress is a separate design — **not in scope here**).
Depends on A1 inventory + the shipped `tap_` naming.
