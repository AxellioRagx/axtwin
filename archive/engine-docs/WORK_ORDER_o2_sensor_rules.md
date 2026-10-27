# Work Order — standing O2 sensors need per-binding/per-scenario detection rulesets

> **For a subagent Pipeline session (`/dev`), not a design session.** Authority: **user
> (2026-07-13)**. **Route:** Pipeline mode (`docs/WORKFLOW.md`) — code-writer → tester →
> code-reviewer → doc-writer → **gated** live-verifier. Interrupt the design session only on a
> real decision.

> **Status: OPEN — 2026-07-13.** Not yet designed in detail — this work order sketches the scope;
> the exact mechanism (tap-plan extension vs. surface-manifest extension, delivery channel, apply
> semantics) is Claude Code's call during the build, per "Collaboration & Design Authority" in
> `CLAUDE.md`. **Depends on** A1 (`axrange/o2control/`, BUILT, `docs/design/05-o2-surface.md`) +
> A2 (`axrange/o2control/taps.py`, BUILT, `docs/design/06-taps-sensors.md`) — both already exist; this
> work order extends A2's binding surface, it does not rebuild A1/A2.

## 1. Goal

A **standing O2 suricata/zeek sensor** (A1) that a target environment's taps get bound to (A2,
`axrange o2 tap apply`) must be able to carry the **detection ruleset the target environment's
author intended for it** — the same way an in-scenario sensor used to carry its own `rules:` file
before the sensor-agnostic te build retired in-scenario `sensors:` for `te`-tier deploys (CHANGELOG
2026-07-13 "sensor-agnostic te — BUILD"). Without this, the standing sensor A2 wires a tap to runs
with whatever ruleset it happened to be started with (its own defaults, or another scenario's rules),
not the target environment's.

## 2. Why this is now a real gap, not a theoretical one

`ad-domain-lab` and `purple-team-range` (`target-environments/`) both declare a `suricata-sensor`
entry with `rules: ./scripts/suricata_ad.rules` — a hand-authored AD-attack detection ruleset (LLMNR
poisoning, SMB/LDAP enumeration, AS-REP roasting, Kerberoasting, and critically the **DCSync DRSUAPI
bind, Priority 1** — CHANGELOG 2026-06-24 "AD DEFENSE overlay"). That ruleset was live-verified on
`dev45` catching the full AD kill chain when the sensor was synthesized in-scenario.

The sensor-agnostic te build (CHANGELOG 2026-07-13) gated all in-scenario sensor synthesis
(`sensors:` → a VM + `deploy-sensor`) on `plan.tier == "o2"` — a `te`-tier deploy of either archetype
now emits **zero** sensor VMs and **zero** `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture`
for its declared `sensors:` block (validator: `REF-250`, a WARNING, not an error — the archetypes
still validate and deploy). Capture is now `axrange o2 up` (stand a generic sensor) +
`axrange o2 tap apply` (bind the te's taps to it) — but **the standing sensor A1 stands up has no
concept of `suricata_ad.rules`**, and A2's tap-plan binds a `tap` to a `sensor_id`, not to a ruleset.
**The purple-team AD detection loop is therefore dark** until this lands — capture happens, but
nothing is watching for the specific attack signatures the archetype's blue-team overlay was built to
catch.

## 3. Scope to sketch (Claude Code's call on the exact mechanism)

- **(a) Association surface.** The O2 tap-plan (`docs/design/06-taps-sensors.md`, `tap → [sensors]`
  bindings) or the O2 surface-manifest (A1's sensor inventory, `docs/design/05-o2-surface.md`)
  needs a way to associate a target environment's sensor `rules:` content with the **standing**
  sensor its taps bind to. Candidates worth weighing: (i) the tap-plan binding carries an optional
  `rules:` path resolved at `apply` time from the TE's own IR/manifest directory; (ii) A2 reads the
  TE's original `sensors[].rules` straight off the deployed `te`'s IR/state (mirrors how
  `record-tap-intent`, OP-MIR-005, already persists resolved intent for A2 to read — reuse that
  "derive from persisted state, not a live IR re-parse" posture per `taps.py`'s header if possible).
  Either way: **one standing sensor may receive rules from more than one bound TE** (fan-out,
  `docs/design/06-taps-sensors.md`) — decide how conflicting/overlapping rulesets compose (append?
  namespace by TE? last-bind-wins? reject?).
- **(b) Delivery.** `axrange o2 tap apply` (or a sibling `o2control` verb) delivers the resolved
  ruleset to the standing suricata/zeek sensor over QGA (the same channel `deploy-sensor`,
  OP-MIR-050, already uses for the in-scenario case — reuse its render/delivery code where possible
  rather than re-implementing) and triggers the sensor to reload/restart against it. Consider
  idempotency (Model-C-style: same ruleset ⇒ no-op; changed ⇒ redeliver+reload) and what happens on
  `axrange o2 tap destroy` / a TE's taps being unbound (does the standing sensor's ruleset revert, or
  does it need explicit teardown too — likely yes, symmetric with apply).
- **(c) Live-verify.** Restore the **AD purple-team detection loop** on `dev45`: `axrange o2 up` →
  `axrange deploy ad-domain-lab` (or `purple-team-range`) → `axrange o2 tap apply` (now delivering
  `suricata_ad.rules` to the standing sensor) → run (or replay) the AD kill chain → confirm the
  standing sensor's alerts include the DCSync DRSUAPI bind (Priority 1) and the rest of the chain,
  matching the pre-sensor-agnostic-te live-verify (CHANGELOG 2026-06-24 "AD DEFENSE overlay"). This
  is the proof the gap is actually closed, not just plumbed.

## 4. Non-goals / explicitly out of scope

- Rebuilding A1 or A2 themselves (both BUILT).
- The **packetXpress** VM sensor type — its own separate, not-yet-designed sensor type
  (`docs/design/06-taps-sensors.md`).
- Any change to the in-scenario sensor model for `tier == "o2"` deploys (A1's own standing-surface
  synthesis is unaffected by the sensor-agnostic te build and needs no change here).
- `export.ship: loki` / Loki live-verify (separate, pre-existing follow-on).

## 5. Validation

- Unit-test the association + delivery mechanism against a fixture ruleset, independent of a live
  cluster.
- The live-verify in §3(c) is the acceptance gate: it is not "done" until the standing sensor's
  alerts on `dev45` demonstrably include the AD ruleset's signatures again.

## 6. Docs to update (doc-writer stage, once built)

`docs/design/06-taps-sensors.md` (the new association/delivery mechanism, once decided),
`docs/design/05-o2-surface.md` (if the surface-manifest gains a rules-association field),
`docs/OP_CATALOG.md` (if a new op or an extended `deploy-sensor`/A2-composed-op signature results —
version-locked schema, bump + confirm per "Design Document Maintenance"), `CLAUDE.md` (a summary
note once this closes the AD-detection-loop gap it reopened), `docs/PRODUCT_GAPS.md` (this gap is
currently only recorded here and in the sensor-agnostic-te CHANGELOG entry's Follow-up line — add a
GAP-NNN if it isn't closed promptly), and a CHANGELOG entry (Authority: implementation (Claude Code),
unless a design fork needs a user call — see §3's "Claude Code's call" note above).

## 7. References

- CHANGELOG 2026-07-13 "sensor-agnostic te — BUILD" (the change that created this gap as a
  side effect of closing a bigger one — see its Follow-up line).
- `docs/design/06-taps-sensors.md` (A2, the tap-plan this work order extends).
- `docs/design/05-o2-surface.md` (A1, the standing-sensor inventory).
- CHANGELOG 2026-06-24 "AD DEFENSE overlay" (the original live-verify this work order needs to
  restore).
- `target-environments/ad-domain-lab/scenario.yaml`, `target-environments/purple-team-range/scenario.yaml`
  (both carry the `suricata_ad.rules` ruleset this work order needs to get back onto a standing
  sensor).
