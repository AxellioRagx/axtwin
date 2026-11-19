# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. Earlier handoffs: docs/handoffs/<YYYY-MM>.md. -->

**Last updated:** 2026-11-17 (trex-test v2: SMB file transfers, AD, internal/external boundary; live on dev45; Phase B note still awaiting sign-off) ·
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

2026-11-17, the user's request (SMB as multi-packet sessions with file transfer, add AD, SMB
server on lan-b, HTTP/TLS/DNS answered by a separate external node behind a boundary):
- `15b074c` driver: per-template `servers:` ranges (port+range association), `reply:` steps
  (server-sent bytes), `size`/`fill` padding on send/reply.
- `506dbcc` twin: `scripts/gen-smb-sessions.py` generates SMB 3.1.1 sessions (NTLMv2/SPNEGO
  login as CROWD\alice / CROWD\bob, tree connect, create, 64 KiB reads or writes, close,
  logoff) into the client persona; new node trex-ext on segment `inet` (192.0.2.0/24, TEST-NET
  ranges behind it); dc01 (Windows 2022, AD DS crowd.axr.lab, users via `identity:`,
  `prepare-dc.ps1` adds fs01/fs02 A records); gw nftables rule drops new connections from inet.
  The boundary is topology + addressing + router policy (no schema field; no schema change).
- Replaced the standing deploy at the user's choice: `trex-test-7758a202` destroyed clean.

## Verified, and how

- **Live on dev45, deploy `trex-test-cba811f5` (2026-11-17):** 70/70 ops; every template
  answered by the intended side (SMB only by trex-server, web/DNS only by trex-ext); sink
  `gre-cap` +5,150 pps / 44 Mbit/s; dev45 capture shows three GRE keys (lan-a all, lan-b SMB
  only, perimeter web/DNS only), 0 malformed; SMB read and write sessions decoded end to end;
  no inbound SYN at the perimeter; DC records and users read back by `qm guest exec`.
  Details in `catalog/twins/trex-test/README.md` (Live acceptance). The first topology's
  acceptance (`trex-test-9df9ca02`) is kept there too.
- **Engine:** `test_trex_astf.py` 48 pass, 1 skip (the real-release check needs `TREX_HOME`; it passed with v3.08 for the SMB profile). The full
  suite is 4443 pass and 3 fail, all from the user's untracked `catalog/twins/*`; with those
  set aside, the catalog suites pass 42/42. **UI:** not touched; no rebuild needed.
- **Serve:** :8080 restarted (user-asked, 2026-11-18) for the MTU change, now pid 2378767 (recipe in memory).
- **dev45 now: trex-test is STANDING** as `trex-test-cba811f5` (te/tap/tfc completed),
  feeding XPress-SW-A. It is not a leftover. Template 102 `img-trex-astf` is kept on purpose.

## Work in progress

None half-edited. Phase B is not started, by the brief's rule.

## Next steps (ordered)

1. The user signs off (or amends) `docs/design/crowd-node.md` D1–D7 before any Phase B code.
2. Locate the DNS loss at ×2 (seen again on v2: 16,353 of 108,655 unanswered): capture during
   ×2 and compare the lan-a and perimeter tap counts of UDP/53.
   Also: gw's `axr-crowd-routes.service` is enabled but `inactive` (script ran routes.sh directly).
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
- **Tap GRE:** since 2026-11-17 the transport runs at MTU 1600 and full-size mirrored frames are ONE outer packet (o2 zone `mtu`, plus the attach-gre-tunnel runtime assert). dev45's o2 zone `o2zbe2d1` was set to `mtu 1600` on 2026-11-18 at the user's go-ahead (legs first, then the zone), so `o2v7dc98` keeps 1600 through SDN reloads. Older captures have two outer IPv4 fragments per full-size frame. Decapsulate a dev45
  capture with reassembly (the session's scratchpad `gre_decap.py`, now with a per-key split; restate it if needed).
- **ASTF:** two server templates on one port need an association by port+IP range (else
  "Two server template with port 445"); the NetBIOS length must include the fill bytes.
- Carried forward: build `ui/` to `dist.new` and swap; engine changes need the serve restart;
  find the serve pid with `pgrep -af 'axrange serve' | grep -v bash`.

## Environment

One venv at `.venv/` (engine + discovery editable), node 22 for `ui/`, live state in
`~/.axrange/` (targets file `~/.axrange/targets.yaml`). Images root
`~/axr-product/deploy-dev45`. TRex v3.08 tarball sha256 `d2ac7362…4972b5f`. dev45 API
10.80.76.108:8006; sink XPress-SW-A management 10.80.72.43 (`fxadmin`).
