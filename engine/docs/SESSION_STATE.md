# Session State (committed — the cross-session handoff narrative)

<!-- Contract (2026-09-19): this file is REPLACED at every handoff, never appended to. Before
writing, move the whole prior content verbatim to docs/handoffs/<YYYY-MM>.md (newest first) and
carry forward only what is still true and unresolved. Keep it to branch/HEAD + push state, what is
in flight, ordered next steps, open decisions, and the gotchas that bite NOW — history lives in the
archive, the CHANGELOG and the work orders. Budget: 8 KB (scripts/doc-budget.py). -->

**Last updated:** 2026-10-05 · **Branch:** `wip/unified-console` · **HEAD:** `b2a7b350`; local
`origin/` tracking ref the same (no fetch). Uncommitted: this doc pass, `.claude/settings.json`
(step 5), untracked `twin-configs-cast/pit-395a61f04e-1780405200/layout.json`.

## In flight

- **STANDING ON DEV45 — DO NOT DESTROY WITHOUT ASKING** (redeployed 2026-10-05 after the dev45
  reboot, user request "same twin + personas + traffic + taps", target "dev45, go"). Destroy order
  tfc → tap → te, naming each id:
  - te `corp-twin-3dae3188` (plan `corp-twin-7de6524d`, `DEPLOY_MIN_OCCUPANCY=0.9`, 106 VMs, 1118/1118
    ops, 0 failed);
  - tap `tap-corp-twin-3dae3188-2191b611` (variant gate90, 24 segments -> XPress-SW-A);
  - tfc `tfc-corp-twin-3dae3188-1d08cc84` (variant **caprate-max**, 84/84 drive-persona incl. the four
    Windows corp-workstations). Revert: `axrange tfc deploy --twin corp-twin --variant gate90
    --images deploy-dev45`. `te egress` measured CONTAINED before taps/traffic.
- **Mirror-MTU path applied, runtime-only** (GAP-119): `twins/corp-twin/scripts/mirror-mtu-path.sh
  apply` (rule 2510, table 251, `tap104i1` 1600, all 24 gretaps). The 24 gretap names were identical
  under the new ids, so the script needed no edit (its comment still names the old tap id). Sensor
  GRO off verified on `ens19`/`gre-cap`. Mirror measured clean: gretap tx errors/drops 0, `gre-cap`
  rx ~29.7 kpps / 244 Mbit/s, zero drops (kernel `gre-cap`, NOT the same measurement point as 2026-10-01's pxswcapture figure).
- **CAPTURE TO DISK IS OFF — decision pending the user.** On XPress-SW-A `pxswcapture.service` is
  inactive (clean stop 17:13:21 UTC, before this run). Someone else's work runs there
  (`pxcapture-looptest.service`, `fxadmin` sessions), so it was not touched. Per-protocol mix /
  correctness ratios were therefore NOT measured, nor hq-mx WAN drops or the lab-CA cert curl.
- **`:8080` Console server is DOWN** (died with the reboot; needs `AXRANGE_API_TOKEN`, which only the
  user has). Restart: `axrange serve --host 0.0.0.0 --port 8080 --adapter-config adapter.conf
  --images deploy-dev45 --scenarios-dir target-environments --twins-dir twins --imprints-dir twins
  --spa-dir o2/console/dist --extra-db /home/rocky/axr-svcengines.state.db` with
  `AXRANGE_API_TOKEN` (and optionally `AXRANGE_PROTECTED_DEPLOYS=corp-twin-3dae3188`) in the env.
  Log: `~/.axrange/serve-8080.log`.
- **Absent still:** O2 services VM (102), suricata sensor (883610), Tcpdump-SW-A (260) — destroyed
  2026-10-05; left on host `/var/lib/axrange/o2-pcaps/Tcpdump-SW-A` and dir mapping `tcpdump-sw-a`.
  O2-dependent work needs `axrange o2 deploy` first. svc-engines is destroyed (GAP-123 moot).
- **Personas at the application layer (~20:13 UTC):** heartbeat 84/84, but 14.1 % of messages failed
  (34,935 of 248,510); `corp-file-share-client` is **100 % failed, 0 bytes** (4352/4352, SMB
  `ECONNRESET` from 10.180.64.21:445); heavy generic endpoints 44-48 % (caddy resetting the https
  filler). GAP-120. dev45: 117 GiB RAM free, 108 VMs running.
- **Disk:** twin disks are linked clones on `axrpool`, not `local-lvm`.

## Next steps (ordered) / open decisions (pending the user)

1. **Decide: restart `pxswcapture` on XPress-SW-A?** Needed before any mix/correctness measurement
   on the standing twin; coordinate with whoever runs `pxcapture-looptest`.
2. **Restart `:8080`** (user holds the token) if the Console is wanted.
3. **Fix `sme-flat`'s default traffic profile** (GAP-116, GAP-111 family) — Windows persona on
   Debian hosts, plus a `db-tier`->`web-tier` `requires:` gap.
4. **Make the mirror-MTU path durable** (GAP-119) — fold into the tap op/adapter.
5. **Restore `.claude/settings.json`'s 2026-09-21 opened gates?** — unresolved.
6. **Fix the file-share persona / https filler** (GAP-120) and the apt-daily race (GAP-109, now seen
   in `serve-vrrp-l3.sh` too); raising the ceiling (GAP-121/122) is the user's call, none urgent.
7. **`docs/reference/ui-consolidation/07-live-verification.md`** — denied by the permission system
   (GAP-118); record lives in the CHANGELOG + built work order + `.../shots/`.
8. **Idempotent whole-profile apply** (GAP-117) — `tfc deploy` restarts every unit.
8a. **(added 2026-10-06, user ruling) IPv6:** GAP-124 recorded; `docs/WORK_ORDER_dual_stack_segments.md`
   OPEN — decide D1 (prefix: ULA recommended), D2 (placement: VLAN20 only), D3 (added op), D5–D7
   before S1 (version-locked specs); S0 read-only checks need no decision. v6 emits = a future WO.
9. Carried forward: WO corp-twin §12 D1/D2/D6; GAP-111; WO twin-consolidation §7 D1/Phase 4;
   unscheduled GAP-100, GAP-095/096/097, sme-flat Stage 6, level-0 column wrapping, GAP-084,
   GAP-113, GAP-114; on a future svc-engines deploy GAP-110/112.

## Gotchas that bite now

- **te deploy can retry once on the apt-daily race:** `run-script[9k02-app]` attempt 1 exit 100
  (`dpkg lock-frontend` held by unattended-upgr), attempt 2 passed (GAP-109, LES-353). Expect it.
- **Don't use mirror-mtu `revert` after a `tap destroy`** (`set -eu` exits on the first missing
  gretap); remove by hand (`ip rule del pref 2510`, `ip route flush table 251`, `ip link set
  tap104i1 mtu 1500`). Re-apply after any reboot/NIC replug/gretap re-create.
- **Judge a persona at the application layer, not the wire** (LES-347) — SMB reads "real" on the wire
  while 100 % of its sends are reset.
- **tshark's flat `tcp.analysis.*` is meaningless on multi-hop-tapped captures** (LES-361).
- **Never build into `o2/console/dist` while it serves** (LES-315).
- **Reads open every store read-only**; empty `-wal`/`-shm` files may appear (never `immutable=1`, LES-359).
- **`axrange serve` fails loud** if a targets file is combined with cluster-identity env vars, or a
  named `extra_db` path is missing.
- **A whole-profile `tfc deploy` restarts every unit** (GAP-117). A deploy id in two stores of one
  target is refused (`409 ambiguous-store`). A protected deploy needs `override_protection=<te id>`
  on every write route.
- An interrupted `tfc destroy` blocks its retry ~7 min (lease); wait. Plain `te deploy` after a destroy
  gives a new deploy id (resume lookup skips destroyed deploys; no `--fresh`).
- Per-host traffic edits: `axrange tfc set-host <target> <host> --persona P [--role R]` or the
  Console's Twin -> host panel.

## Archive

Prior handoffs, verbatim, newest first: [`docs/handoffs/2026-10.md`](handoffs/2026-10.md) (the
2026-10-05 entry holds the empty-range / capture-ceiling handoff this one replaced),
further back [`docs/handoffs/2026-09.md`](handoffs/2026-09.md).
