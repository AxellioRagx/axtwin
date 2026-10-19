# Work Order — Tap tier: semantic tap points + decouple from `te`

> **STATUS: DONE — gated live-verify (dev45) PASSED 2026-07-25.** Code, tests, and docs landed
> (CHANGELOG 2026-07-24 "Tap points BUILT"; suite: 1155 passed / 12 skipped / 0 xfailed,
> 1167 collected). The §4 "Gated live-verify (dev45)" step ran as Stage 3 of
> `docs/RUNBOOK_twin_model_live_verify.md` (CHANGELOG 2026-07-25 "Twin-model arc: dev45 live-verify
> Stages 1–4 PASSED") against a real `corp-enterprise` twin: `segment` + `boundary` scopes both bound
> and isolated correctly (the first real `boundary` tap point on real content), heartbeats confirmed
> from both a Linux and a Windows injector, and a live re-shard re-pointed capture in place without a
> twin redeploy. **Two fixes were driven by this stage**, both landed: the boundary sentinel had to
> move off the router onto a leaf (LES-121), and a Windows verify-capture sentinel path was added
> (LES-122). **The re-shard also exposed and drove the fix for a capture-plane data-loss bug**
> (shared-gretap de-scope diffing at the wrong granularity — LES-118, the flagship lesson of the
> session) — root-caused and fixed in the same pass, then re-verified clean. Authority: user
> (2026-07-23, design) / implementation (Claude Code) (2026-07-24 build, 2026-07-25 live-verify).
> **Spec:** `docs/DESIGN_BRIEF_twin_model.md` §2/§3/§4/**§4.1**. Route: code-writer → tester →
> code-reviewer → doc-writer → **gated** live-verify (dev45). Evolves the existing `tap` tier
> (`axrange/o2control/taps.py`, A2) — do not rebuild it.
>
> **Renamed from `WORK_ORDER_tap_directional.md` (2026-07-23, Authority: user).** The original order
> modelled tap points as a **directional EW/NS taxonomy**; that is superseded by the topological
> **tap-point** model in brief §4.1. Direction is now at most a `labels[]` value, never the schema's
> organizing axis. See the CHANGELOG entry of the same date.

## 1. Goal

Give the tap plan a **semantic tap-point vocabulary** — named capture places (`corp-lan`,
`idmz-conduit`) scoped to a segment / boundary / host / leg — so a tap plan binds *places* to Lab
sensors, and a re-apply can **re-shard capture without disturbing running traffic**. Sensors stay
standing in the Lab (`o2`).

This also closes **GAP-028**: `tier_split` publishes `tap_points[].id` as a mechanical interface id
(`host:net:nic`); this order introduces the semantic namespace that groups those interfaces.

## 2. Build

- **Tap-point model (brief §4.1).** A tap point is `{ id, scope, target, labels[] }`:
  - `scope: segment` — all traffic on one network (the common case); `target` = net id.
  - `scope: boundary` — a conduit between two segments, or segment↔WAN; `target` = the segment pair.
  - `scope: host` — every leg of one host; `target` = host id.
  - `scope: leg` — one specific host↔net interface; `target` = `{host, net}`.

  **`direction` is NOT a field.** EW/NS may appear in `labels[]` when it genuinely describes a point,
  and selectors can match on it — but nothing in the schema, planner, or teardown may key off it. A
  flat twin (`sme-flat`) has no direction split and must still tap cleanly; a multi-homed router leg's
  direction is ambiguous depending on which neighbour you reason from.
- **Resolution.** Each tap point resolves to a concrete set of tappable interfaces from the twin
  inventory (`axrange/planner/inventory.py`, `build_twin_inventory`) — the semantic layer *groups* the
  mechanical `host:net:nic` ids that already exist. One tap point → N interfaces is the normal case
  (a `segment` tap point covers every host leg on that net).
- **Tap-plan spec** gains tap-point binding: `{ twin, bindings: [{ tap_point|selector, sensor, mode:
  fanout|exclusive }] }`, where `selector` matches on `id`, `scope`, `target`/segment, or `labels`.
  Reuse the existing per-tap sensor routing (1:N fan-out / per-tap exclusivity, gretap keying) — this
  adds the **vocabulary**, not a new capture mechanism.
- **Decouple from `te` emission:** the twin (`te`) emits `record-tap-intent` only (already true post
  sensor-agnostic-te); the `tap` playout discovers tap points from the inventory and binds them to
  standing `o2` sensors. Confirm a `tap` re-apply re-points/re-shards **in place** without touching the
  `tfc` tier or the twin.
- **Decide explicitly whether `tap` becomes a real deploy tier.** `tier_split` deliberately left this
  open: `_VALID_TIERS` has no `"tap"`, so `o2control/taps.py` stamps its playout deploy `tier="te"`
  and the six-tier guard relies on the documented (load-bearing) `te → tap` cascade exception.
  `naming.py` already exports `TIER_PREDICATES` with a `tap` entry. If you promote it, thread it
  through `_VALID_TIERS`/`TIER_SHAPE_CHECKS`/`underlay_bridge_for_plan` and re-verify the full
  teardown matrix; if you don't, say why in the docs so the next reader stops re-asking. **Either way
  `destroy_deploy` must keep failing closed on an unrecognized tier (LES-098).**

  **Decided (2026-07-24, build): NOT promoted.** `tap` stays a resource name-shape tag; the
  `tap-<te>` playout deploy keeps `tier="te"` + the load-bearing `te → tap` cascade (LES-094) + its
  own structural discriminator (its ops are always a subset of `attach-gre-tunnel`/
  `install-ovs-mirror`/`verify-capture`). Reasoning: scope discipline (promotion is its own
  full-matrix re-verification, not a side effect of adding binding vocabulary), no behavior gap the
  promotion would close, and the fail-closed posture (LES-098) is unaffected either way. See
  `axrange/o2control/taps.py`'s module docstring, `docs/DESIGN_BRIEF_o2_taps.md`, and
  `docs/CONVENTIONS.md` §2 for the full writeup.
- CLI/API unchanged in shape (`axrange tap apply|destroy` / `/api/o2/taps`), extended to accept
  tap-point selectors.

## 3. Boundaries

Capture wiring only. Sensors are Lab (`o2`) resources — never stand them up here (bind to them). Do not
touch traffic generation. Keep the existing gretap/`tc`-mirror mechanics; only the tap-point
selection/routing vocabulary is new.

## 4. Validation

- Unit: tap-point resolution against a mock inventory (`segment` → every host leg on that net;
  `boundary` → the router legs joining two segments; `leg` → exactly one interface); selector matching
  by id / scope / target / label; fan-out vs exclusive; re-apply re-shards without re-deploying the
  twin; `tap` destroy touches only `tap`. **Include a flat/no-segmentation twin** as a first-class
  case — it is the one §4.1 exists to make work.
- **Gated live-verify (dev45):** on an `ics-ot` or `corp-enterprise` twin with ≥2 Lab sensors, bind a
  `segment` tap point to `sensor-a` and the `boundary` tap point to `sensor-b`; confirm each sensor's
  pcap contains that place's traffic and not the other's; then **re-shard** (move the segment tap point
  to a third sensor) and confirm capture follows without redeploying the twin. Re-run the
  "background traffic kept running throughout" assertion after `traffic_tier` lands.

## 5. Depends on / sequence

Depends on `tier_split` (the published inventory + `tap_points[]`). Parallel with `persona_library` +
`twin_configs`. The re-shard-without-disturbing-traffic live-verify needs `traffic_tier` up (run that
assertion after `traffic_tier` lands). Docs: `DESIGN_BRIEF_twin_model.md` §4.1,
`DESIGN_BRIEF_o2_taps.md` (tap-point extension), `CONVENTIONS.md` §2, `PRODUCT_GAPS.md` (close
GAP-028), CHANGELOG, LES.

## 6. Follow-on — predefinable tap plans (`docs/WORK_ORDER_tap_target_resolution.md`, BUILT 2026-07-25)

This order's tap-point vocabulary (§2) made a binding's *addressing* (which interfaces a tap point
covers) reusable across redeploys; it did NOT make the tap-plan's `target:` itself reusable — `target:`
was still a literal, per-deploy-minted `te` deploy id, so a tap plan naming even the semantically
richest tap-point selector was still welded to one specific deploy instance (the exact staleness trap
LES-072 documents: any edit to the target TE's IR mints a new deploy id and silently orphans every
tap-plan file hardcoding the old one). `docs/WORK_ORDER_tap_target_resolution.md` closes that
remaining gap: `target:` is now OPTIONAL, and either a literal deploy id OR a scenario/twin-config
NAME (resolved to the newest live `te` deploy of that scenario, `resolve_target_deploy`); a
`--target`/API-body override lets a plan omit `target:` entirely, becoming a genuinely predefinable
library artifact. See `docs/DESIGN_BRIEF_o2_taps.md` §11 for the full writeup.
