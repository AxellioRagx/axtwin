# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. Earlier handoffs: docs/handoffs/<YYYY-MM>.md. -->

**Last updated:** 2026-11-16 (TRex crowd, Phase A done; Phase B note awaiting sign-off) ·
**Branch:** `wip/trex-crowd`, cut from `wip/design-platforms-canvas` at `083d78c` (the user's
choice: the repo has no `main`; `master` and `wip/api-discovery-runs` are older). Local commits
only, not pushed. Rule: commit each piece as it lands, staged by path; push only when asked.

## What this session did

Earlier the same day, on `wip/design-platforms-canvas` (pushed): investigated a corp-twin-native
destroy that looked stuck (it was clean; the canvas then read the twin invalid), fixed the
canvas checks (CV-040 group-member link ends, parked-through-hosts) and added live destroy
progress (`GET /api/deploys/{id}/destroy-progress`), commit `083d78c`.

Then the TRex crowd brief, Phase A, on `wip/trex-crowd`:
- `614784f` the `trex-astf` persona driver (`personas/drivers/trex_astf.py`), pinned to TRex
  **v3.08** after reading the release's own `trex.astf.api`; DRIVE_KINDS/PSN-030 widened.
- `a930d7d`, `b41ff98` the `catalog/twins/trex-test` bundle (gateways declared on segments).
- `7225247` the cps-ratio nudge (a TRex v3.08 `KxuNuRand` abort, reproduced with astf-sim)
  and `stats` carried through `GET /api/deploys/{id}/health?host=`.
- Golden template `trex-astf` (manifest and binding in `~/axr-product/deploy-dev45/images/`,
  untracked there; recipe restated in the twin README), built on dev45 as template **102**.
- `docs/design/crowd-node.md`: the Phase B0 design note. **Stop here for sign-off.**

## Verified, and how

- **Live on dev45, deploy `trex-test-9df9ca02`:** acceptance 1, 2, 4 and 5 pass. Acceptance 3
  passes for every template on established cps (TCP) and on sends (UDP), at both ×1 and ×2,
  and the schedule step shows within 10 s. DNS at ×2 loses about 20% of queries before the
  server (50.4 of 79.5/s arrive). Numbers are in `catalog/twins/trex-test/README.md`; raw data
  is in `~/.axrange/trex-test-acceptance/`.
- **Seen directly:** the sink's `gre-cap` counter, the dev45 GRE capture (two keys, one per
  tap), and the guests after the tfc destroy (no units, NIC restored). The discovery round
  trip ran on that capture.
- **Engine:** `test_trex_astf.py` 40 pass (the real-release check needs `TREX_HOME`). The full
  suite is 4443 pass and 3 fail, all from the user's untracked `catalog/twins/*`; with those
  set aside, the catalog suites pass 42/42. **UI:** not touched; no rebuild needed.
- **Serve:** :8080 restarted twice for engine changes, now pid 2202820 (recipe in memory).
- **dev45 now: trex-test is STANDING** (the user asked to keep it up, 2026-11-16 ~20:05 UTC):
  deploy `trex-test-7758a202`, te/tap/tfc completed, feeding XPress-SW-A (`gre-cap` +3,133
  pps / 25 Mbit/s, both tap GRE keys seen). It is not a leftover. Template 102 `img-trex-astf`
  is kept on purpose. The acceptance deploy `trex-test-9df9ca02` was destroyed and swept clean.

## Work in progress

None half-edited. Phase B is not started, by the brief's rule.

## Next steps (ordered)

1. The user signs off (or amends) `docs/design/crowd-node.md` D1–D7 before any Phase B code.
2. Locate the DNS loss at ×2: capture at ×2 and compare the lan-a and lan-b tap counts of
   UDP/53 (server low_end core or af_packet RX, router, or bridge).
3. Open items carried in the note: TRex EMU for on-link ranges; the image build's smoke test
   (its exporter op is config-class, and the build executor has no config backend); the
   Debian `configure-router` static-route handler.

## Open questions / decisions pending

- Phase B sign-off (above). Merge or push of `wip/trex-crowd`: the user's call.
- Carried forward: the serve's auth mode (`none` on 0.0.0.0); corp-apcn-reader-20261028 is
  still invalid, with no twin.yaml.

## Mental model / gotchas

- **Router legs:** a pinned router-leg `ip:` records NO segment gateway, so hosts get no
  default route. Declare `gateway:` on the segment and leave the router legs unpinned.
- **Capability vocabulary:** `requires:` keywords are a closed set (STR-110); an image may
  `provides:` anything.
- **Image build:** it fails at its smoke test for every image (leaving a VM, zone, vnet and
  bridge; clean with `axrange te destroy <id> --db /tmp/axr-imgsmoke-*/smoke-state.db
  --allow-destroy`). Build with `--skip-smoke`.
- **TRex in the guest:** the daemon and the controller are two transient units per side, and
  the NIC is flushed for TRex and restored on stop. The persona schedule lives in knobs,
  because assignment `schedule:` takes only 24x7 / business-hours.
- **Tap GRE:** full-size mirrored frames travel as two outer IPv4 fragments. Decapsulate a dev45
  capture with reassembly (the session's scratchpad `gre_decap.py`; restate it if needed).
- Carried forward: build `ui/` to `dist.new` and swap; engine changes need the serve restart;
  find the serve pid with `pgrep -af 'axrange serve' | grep -v bash`.

## Environment

One venv at `.venv/` (engine + discovery editable), node 22 for `ui/`, live state in
`~/.axrange/` (targets file `~/.axrange/targets.yaml`). Images root
`~/axr-product/deploy-dev45`. TRex v3.08 tarball sha256 `d2ac7362…4972b5f`. dev45 API
10.80.76.108:8006; sink XPress-SW-A management 10.80.72.43 (`fxadmin`).
