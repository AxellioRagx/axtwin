# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. Earlier handoffs: docs/handoffs/<YYYY-MM>.md. -->

**Last updated:** 2026-11-11 (targets and the O2 lab, pushed) · **Branch:**
`wip/design-platforms-canvas` at `7397107`, pushed; `master` fast-forwarded to the same commit
and pushed (local `master` too). `wip/api-discovery-runs` was NOT moved this time (still
`1f3bb08`). Rule: commit each piece as it lands, staged by path; never `git add -A`; push only
when the user asks.

## What this session did

Implemented the design record "axtwin design: targets and the O2 lab" (claude.ai doc
https://claude.ai/artifact/D7NNQhdjQ6ak7isN85j3Nx), following its §5 build order with a browser
review stop after each step. One commit per step: `b46e911` the lab block (engine
`platform/lab.py`, lab sensors merged into EXTERNAL_SENSORS), `19e3d97` API (targets carry
`lab`, `GET /api/targets/{name}/health`, `?target=` preflight on validate/plan, rules
TARGET-SENSOR / TARGET-IMAGE), `c19acb5` the Targets page, `b757605` the Deploy dialog (target
select, preflight rows with Remap, canvas Deploy… and target chip, plus the carry-alongs: tap
drag test and the Destroy wording for applied-but-unpersisted), `7397107` canvas tap Sensors
picked from the target's lab and the Lab overlay. Then moved all three dev45 sensors out of
adapter.conf into the lab block in `~/.axrange/targets.yaml` (host files only, no commit).

## Current state

- **Tests:** ui 551/551 at `7397107` (one `canvas.subtree` flake seen once under load). Engine:
  the new suites `test_targets_lab.py` (24) and `test_targets_api.py` pass. The full suite has
  about 60 failures that are also on clean HEAD, all from the untracked `catalog/twins/*`
  folders (list: `fail_base.txt` in the old session's scratchpad). No new failures.
- **Serve:** :8080 pid 1712222, started 2026-11-09 19:16 with `AXRANGE_TARGETS=~/.axrange/targets.yaml`
  (recipe in memory `axrange-serve-restart-recipe.md`). `ui/dist` built at `7397107`.
- **Live (dev45):** `/api/targets` reports sensors XPress-SW-A, Tcpdump-SW-A, o2-suricata, all
  from the lab, all `protected: true`. Health: XPress-SW-A up; Tcpdump-SW-A (vmid 260) and
  o2-suricata (vmid 883610) unknown, because Proxmox doesn't resolve those VMs and their
  transport IPs aren't routable from the API host. Grafana/Prometheus/Loki/Caldera/Wazuh are
  down because the O2 stack isn't running here. switch-test preflight on dev45 is clean.
  Nothing is deployed by this session.
- **Host files changed:** `config/adapter.conf` has no EXTERNAL_SENSORS line any more (backups
  `~/.axrange/adapter.conf.bak-20261109-pre-lab`, `...-pre-lab2`); `~/.axrange/targets.yaml`
  (backup `targets.yaml.bak-20261109`).
- Untracked, the user's: the `catalog/twins/*` folders (including a new `e2e-disc/`, origin
  unknown to this session), `ui/dist.old/`; mine: `ui/dist.prev/`.

## Work in progress

Nothing half-edited; the tree is clean apart from the untracked folders above.

## Next steps (ordered)

1. User's commit `7055ba4` (`docs/design/targets-and-lab.md` + api/ui Spec lines) is not on the
   remote. This session deliberately didn't create those files, so it should merge cleanly.
2. Browser review of step 5 (tap Sensors checkboxes, Lab overlay via the rail's `rail-lab`
   button) was not signed off.
3. Live acceptance run, if the user approves (it creates VMs): deploy switch-test from the
   Deploy dialog on dev45, move its tap in the inspector, Apply, Destroy.
4. Find out whether VMs 260 and 883610 exist on dev45; bring them up or drop them from the lab.
5. Multi-target gap: the canvas, the Twins page and `/api/taps` read only the default target's
   deploys and store. Harmless with dev45 alone; needed before a second target is real.

## Open questions / decisions pending

- Tcpdump-SW-A / o2-suricata: keep in the lab or remove (step 4 above).
- When to drop the one-release adapter.conf EXTERNAL_SENSORS fallback (the Lab overlay still
  draws such sensors tagged "adapter.conf").
- Carried forward: the serve's auth mode (`none` on 0.0.0.0); corp-apcn-reader-20261028 is
  still invalid (IPv6 /64, CV-012) with no twin.yaml.

## Mental model / gotchas

- A serve or CLI run **without `AXRANGE_TARGETS`** now sees no external sensors at all on
  dev45, since adapter.conf no longer lists them. `cli/main.py` registers labs from that env
  var; `create_app` registers them from the parsed targets.
- Sensor health is ssh :22 on the platform-resolved VM IP, else transport_ip, else unknown with
  the reason. There is no tap metrics route, and transport IPs aren't routable from the API host,
  so "unknown" there is normal. Health is cached for 15 s server-side.
- Lab sensor ids may not also appear in EXTERNAL_SENSORS (`merge_lab_sensors` raises).
- `api/preflight.py` uses `check_platform`, not DEFAULT_PLATFORM: the layering test forbids it.
- `imprint_deploy` refuses an unclean preflight with 409 `{code: "preflight"}` before any write.
- Restarting :8080: find the pid with `ps -eo pid,args | grep '[a]xrange serve --host 0.0.0.0'`
  and kill it by number in a separate command; `kill $(pgrep -f …)` killed the calling shell once.
- React Flow drags can't run in jsdom: drop logic lives in the pure `tapDropSelector`
  (`Canvas.tsx`) and is tested there. `onNodeDragStop` must handle pin-key-less kinds before
  the `pinKeyOf` early return.
- Carried forward: the follow-on chain runs in the serve's thread (a restart mid-chain leaves a
  tier pending in `~/.axrange/state.db.follow-on.json`; delete it by hand); a cast's tap chips
  render only on drawn pills; Start tap / Start tfc never clicked live; corp-twin segments share
  CIDRs (never map host→pill by CIDR); `edit/provenance.ts` stamps `authored` on any shape
  change; a subagent worktree comes from an old base (39fa362), make one by hand.
- UI-only change: build to `ui/dist.new` and swap (never build into the served `ui/dist`);
  engine change needs the restart. `ui/`: `npm ci` only.

## Environment

One venv at `.venv/` (engine + discovery editable), node 22 for `ui/`, live state in
`~/.axrange/` (targets file `~/.axrange/targets.yaml`, schema in
`config/adapter.targets.example.yaml`). Playwright at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright`. dev45 API 10.80.76.108:8006. The serve env
file lives in a session scratchpad and holds secrets; rebuild it from the live env plus
`AXRANGE_TARGETS` and `AXRANGE_IMAGES_ROOT=~/axr-product/deploy-dev45`.
