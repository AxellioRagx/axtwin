# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. Earlier handoffs: docs/handoffs/<YYYY-MM>.md. -->

**Last updated:** 2026-11-20 (discovery round trip on pace-test converged to a fixed point) ·
**Branch:** `wip/design-platforms-canvas`, pushed to origin at the user's request (2026-11-20,
through this handoff's commit; rule: commit locally, push only when asked). Note: the sessions of 2026-11-18/19
(source Test connection, TLS toggle, run pre-fill, imprint versioning, axquery `/api/flows`,
merge of `wip/trex-crowd` at `5d91500`) left no handoff; their story is in `git log` only.

## What this session did

The user asked to replace the standing twin with a simple, brisk one, tap it to the sensor,
discover an imprint through `XPress-SW-Reader` (axquery), cast it, compare with the original and
fix the pipeline until it converges. Destroyed `trex-test-cba811f5` (clean, 14 resources);
built and deployed `catalog/twins/pace-test` (router, bind9/nginx/Samba, 4 clients on `drive:
command`, tap on `srv`); ran the round trip several times; fixed 8 defects (commits `89c6e68`
`95695ff` `b837754` `bb6911b` `2d5f4f8`, comparators `f328ea4`); deployed the cast live
(relocated) and re-discovered it. Full table: `catalog/twins/pace-test/README.md`.

## Current state

- **Converged.** Structure: the cast equals pace-test (2 segments, 4 clients, dns-resolver 53,
  web-tier 80/443, file-server 445, all real daemons, lan→srv conduit). The re-discovered cast's
  imprint has the same hosts/roles/listen sets. Traffic, post driver fix (hour 23:00–24:00):
  both twins deliver their declared rates within 0.5%; re-casting that hour gives exactly
  `personas/pace-client.yaml`'s cadences (5s med / 10s / 10s / 30s / 15s).
- **Tests:** discovery 2505 pass, 1 skip. Engine 4514 pass, 3 fail — all from untracked
  `catalog/twins/*` folders (the user's, plus this session's `pace-cast`, `pace-cast-r73`);
  pre-existing class (registry/bundle-layout tests).
- **dev45 now: two STANDING twins, on purpose, both traffic on, both tapped to XPress-SW-A:**
  `pace-test-1f4a6c96` (tap `tap-pace-test-1f4a6c96-2bf5e29a`, tfc re-applied 23:01 on the fixed
  driver) and `pace-cast-r73-7b02f2b7` (10.73.0/1.0/24 + sink 10.200.254.0/24; tap on its server
  segment only, `tap-pace-cast-r73-7b02f2b7-211bde6e`). trex-test is gone; template 102
  `img-trex-astf` is still kept.
- **Serve NOT restarted** (pid 2614679 runs pre-`2d5f4f8` engine). tfc applied through the CLI
  renders the fixed client; tfc applied through the API/console still renders the OLD loop until
  a user-asked restart. aximprint is a subprocess from the editable install, so API casts already
  use the new cast code.

## Work in progress

None half-edited. `catalog/twins/pace-cast/` and `pace-cast-r73/` are API cast output (untracked,
like `test1`): keep or delete is the user's call.

## Next steps (ordered)

1. Ask the user what to do with the two standing twins; teardown when told:
   `.venv/bin/axrange te destroy <id> --allow-destroy --db ~/.axrange/state.db` (cascades tap/tfc).
2. Serve restart (only when asked) so console-applied traffic gets the anchored client — recipe
   in memory `axrange-serve-restart-recipe`.
3. Optional live 1.0 proof: re-cast from a post-fix, original-only imprint (the reader mixes both
   twins in one PIT — suppress 10.73.* by assertions or stop the cast's tfc for an hour), re-apply
   the cast's tfc, compare an hour with `discovery/scripts/compare_imprints.py --map
   10.72.1=10.73.0,10.72.2=10.73.1`.
4. Carried from 2026-11-17 (trex): DNS loss at ×2 (needs trex-test redeployed); gw
   `axr-crowd-routes.service` enabled but inactive; TRex EMU for on-link ranges; image-build
   smoke test (exporter op is config-class, no config backend); Debian `configure-router`
   static-route handler.

## Open questions / decisions pending

- Merge of `wip/design-platforms-canvas` (pushed): the user's call.
- Cast tap plan binds EVERY segment; the source vantage was one segment, so a re-capture of a
  cast double-counts routed flows. Should the cast tap only where the source was observed? Not
  decided; worked around by tapping manually.
- `_cadence_for`/`pace_for` clamp at 600 s over-offers rare background emits (cast file-server →
  sink TLS 16/h vs 0.5/h observed). Relax the clamp? Not decided.
- The cast does not reproduce the source resolver's recursion to root servers (~20 flows/h).
- Carried: Phase B sign-off of `docs/design/crowd-node.md` D1–D7 (status since the merge
  unknown); serve auth mode `none` on 0.0.0.0; `corp-apcn-reader-20261028` still invalid.

## Mental model / gotchas

- **Reader timing:** XPress-SW-Reader (`src-69243c4e`, https://10.80.72.40/axquery) indexes
  HOURLY chunks, available ~1 min after the hour. Rate quantiles in a PIT need ≥4 windows
  (`_RATE_MIN_WINDOWS`), so a 1–3 h imprint has none — the cast now paces from `ir_edge.flows`.
- **axquery labels TLS `tls`**, Zeek `ssl`; both are in `_IR_PROTO_ALIASES["https"]` now.
- **Lab sensors:** `axrange tap deploy` (CLI) does not read `AXRANGE_TARGETS` lab sensors
  ("unknown sensor 'XPress-SW-A'"); apply taps through `POST /api/taps`. `te destroy` takes no
  `--images`. CLI `tfc deploy <path>` loads the bundle's own `personas/`.
- **CLI discovery by hand:** `. ~/.axrange/pace-rt/env.sh` (0600; pulls the reader API key from
  `~/.axrange/discovery.db` and sets TLS-verify off), then `aximprint ingest --plugin axquery
  --source axquery:https://10.80.72.40/axquery --from/--to ...`. Scratch stores under
  `~/.axrange/pace-rt/`.
- **Gateway fold:** an entity at network+1 with role None/`generic-endpoint` and no listen set
  is folded into `gw` (axRange gives router legs network+1). A role-bearing `.1` is kept.
- **Persona clients** (scripted/command/browser) are held to a jitter-free anchor; the scripted
  golden hashes in two engine tests were re-pinned for that on purpose.
- Carried: router legs — declare `gateway:` on the segment, leave legs unpinned; `requires:` is
  a closed vocabulary; image build fails at smoke test (build with `--skip-smoke`); tap GRE runs
  at MTU 1600 (dev45 zone `o2zbe2d1`); ASTF needs port+range association for two templates on
  one port. Build `ui/` to `dist.new` and swap; engine changes need the serve restart.

## Environment

One venv at `.venv/` (engine + discovery editable), node 22 for `ui/`, live state in
`~/.axrange/` (targets `~/.axrange/targets.yaml`; logs `~/.axrange/pace-*-{te,tap,tfc}-*.log`).
Images root `~/axr-product/deploy-dev45`. dev45 API 10.80.76.108:8006; sink XPress-SW-A mgmt
10.80.72.43 (`fxadmin`; `ip -s link show gre-cap` for the RX counter).
