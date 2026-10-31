# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-10-31 · **Branch:** `wip/design-platforms-canvas` (twenty-one commits past
`wip/api-discovery-runs`, which is five past `master`). Remote `origin` exists; **never push**,
the user pushes. Rule restated 2026-10-31: **commit each track as it lands**.

## What this session did

Built the design commits of 2026-10-30 as code, one commit per track, then reviewed the two
unreviewed tracks and fixed what the reviews found:

1. `8527ad0` **pcap ingest plugin**; `f286837`+`d3774ca` **A3/A2 edit model**; `a41456a`+`508fba9`
   **canvas phase 1**; `ce610c3`+`cc3ddeb` **platform seams**; `2fb3e27` **Splunk + Elastic**;
   `69bab7a` **libvirt adapter**; `03503fc`+`39f2cfb` **canvas phase 2**; `94efa09` **platform
   travels with the config**; `2eda68d` **`POST /api/validate {document}` + layout annotations**.
2. `2744358` **SIEM plugins hardened after review**: bisection bounds floor/ceil (int-truncation
   lost rows; also closed in axquery), each leaf sorted before the merge (Splunk is newest-first),
   ISO `ts` on Corelight rows normalised in `rows.zeek_events`, OpenSearch tiebreaker `_id`,
   Elastic health events only on a consumed leaf + `timed_out` + PIT id refresh, Splunk WARN →
   `partial_results`, `index=` validated, CIM `src`/`dest` must be IPs, shared `rows.flow_state`
   / `start_from` / `http.request`. 180 tests in the four SIEM/axquery files.
3. `52485f3` **libvirt adapter hardened after review**: only VIR_ERR_NO_* codes mean "absent"
   (a transport error no longer reports a guest gone), teardown guard fails closed (positive
   tier-shape match; `default` network refused), q35 CD-ROM target no longer collides with a
   sata boot disk, `snat`/`dhcp` refused `adapter-unsupported` instead of silently dropped,
   apply-sdn-config scoped to this plan's networks, agent timeout `-1`, `PLATFORM` never read
   from the environment, `HOST_SSH_HOST` cross-checked with the URI, raw `libvirtError` wrapped,
   `cloud_init_user`/`nic_model` wired, per-import `adapter_libvirt → adapter_proxmox` allowlist.
4. `a2240c7` **promote-to-assertion keyed by the cast roster** — the handoff's open question,
   verified: a twin-config node name is a cast GROUP name and the assertions seam resolves only
   entity keys / observed CIDRs. The sidecar's `nodes[i].count` row now carries `members`;
   `GET /api/twins/{id}/cast` publishes `members` per node and `observed_cidr` per segment; the
   canvas writes one override per member and disables promote, with the reason, where the
   roster cannot key it.
5. `387edc3` **console titled axTwin; Canvas a top-menu area** (user request 2026-10-31): the
   brand and page title read axTwin (engine, CLI and API keep their names); Canvas joins the
   top menu with a drop-down of every twin in the catalog plus "New design…", and Designs /
   New design beneath it. `/canvas` lists the twins and the uncast imprints; `/canvas/new`
   writes a blank twin-config (one empty segment, no nodes -- the engine refuses a config with
   no segment; checked through `validate_document`) or a copy of one in the catalog through A3
   and opens it; `/canvas/:id` is the canvas (`/twins/:id/canvas` still reaches it).
6. **The layout side-car is a draft** (user rule 2026-10-31: no write per drag): moves, aligns,
   placements and annotation changes count as unsaved on the canvas; **Save layout** is the one
   PUT (also run by a document Save / Persist), **Discard** returns to the last loaded or saved
   layout, a 412 keeps the draft and takes the fresh etag. `edit/useLayoutDraft.ts`, hook-tested
   (`canvas.layout.test.ts`) because jsdom cannot drive a React Flow drag, align or drop.

## Current state

- **Tests at HEAD:** engine 4164 passed / 19 skipped / 2 failed (the untracked
  `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028}` folders without a manifest —
  pre-existing, decide commit-or-remove); discovery 2422 passed / 1 skipped; ui 156 passed,
  typecheck + build clean; `ui/dist` rebuilt from HEAD.
- **Working tree clean** apart from the three untracked catalog folders. No agents running.
- The running `axrange serve` (the 2026-10-28 process on `0.0.0.0:8080`) serves `ui/dist` from
  disk: the served bundle carries the canvas route (checked with curl), so a reload shows it at
  `/twins/<id>/canvas`. Nothing deployed, destroyed or restarted on dev45 / pxtwin1.
- **Existing cast bundles predate the `members` row**: their promote actions show disabled
  until the imprint is re-cast (`POST /api/imprints/{pit}/cast {replace:true}`).

## Next steps (ordered)

1. Eyes on the Canvas area and the canvas against the largest cast twin (element budgets are
   measured, not assumed); create one blank design from `/canvas/new` with the write token;
   re-cast one imprint and exercise promote end to end.
2. Libvirt end to end is blocked by the confirm-first `vmid` → guest-handle bump (the `int(vmid)`
   readers in `config_exec/backend.py`, `planner/{teardown,inventory,access}.py`,
   `o2control/containment.py`, `api/service.py`; `planner/teardown.py:72,99` default namer) and
   by `planner/pass1_emit.py:149 ROUTER_WAN_BRIDGE="vmbr0"` (every router twin fails on libvirt).
3. Then the live libvirt two-host slice (recipe: `config/adapter.conf.libvirt.example`); the
   `importorskip("libvirt")` constants test runs there for the first time.
4. Live Splunk/Elastic against a Corelight-fed index (fixtures are authored, not recorded;
   the OpenSearch `_id` tiebreaker and the Corelight ISO-`ts` default are from memory).

## Decisions pending the user

- **Confirm-first contract bumps:** neutral op ids / resource categories (`OP_CATALOG.md`);
  `vmid`/`pve_node` → opaque guest handle + `host` (`PLANNER.md`, DTOs, state store);
  `zone_type: simple|vlan` → an L2 mode; `ADAPTER.md` beside the Proxmox adapter + a neutral
  `PLATFORM.md`; `template_vmid` as the Proxmox binding's key; a neutral router-uplink marker
  from the planner (libvirt WAN network config key).
- **libvirt NAT/DHCP:** create-subnet/create-vnet stay logical and refuse `snat`/`dhcp`; implement
  `<forward mode='nat'>` + DHCP networks, or keep refusing? Per-segment DNS and apply-sdn scope
  live on the adapter instance (a resumed deploy in a new process heals only the underlay).
- **Layering moves held:** `ingest/siem/http.py` → `ingest/http.py` and a public zeek parser
  table (SIEM S4, `# TODO(layering)` in `rows.py`); hoisting the Windows-readiness, arp-scan and
  router flows out of `adapter_proxmox/rest.py` into a neutral `axrange/platform` module (the
  libvirt allowlist pins today's crossings); adapter code placement under `adapters/<platform>/`.
- **Edit model:** Persist of a never-applied working copy marks the deploy clean (today yes)?
  keep the `inert` kind for `exr/` and top-level prose? document on reads only with the token?
- Merge of this branch and `wip/api-discovery-runs` to `master`; fate of the untracked folders.

## Gotchas

- Shared API files were edited by several tracks; per-track commits were split by hunk
  (`scratchpad/tools/stage_hunks.py`, session-local) — stage by path, never `git add -A`.
- `_HOST_KEY_RE` in `console_files.py` is the schema's entity-key pattern; the host PIN regex is
  `_HOST_PIN_RE`. The working-copy sidecar is `<state>.edits.db` beside the planner DB.
- `engine/tests/test_api_console.py::_MIRRORED_DTO_FIELDS` pins every console DTO's field set by
  hand; `o2/console/src/api/types.ts` is the mirror it names, `ui/src/api/types.ts` the live one.
- Run the two Python `tests/` trees separately; engine with `-n auto -p no:cacheprovider`.
- `ui/`: `npm ci` only; `@xyflow/react` pinned 12.12.0. In jsdom React Flow never marks a
  chip `selected`, a palette drop and a drag are inert: test canvas state machines as hooks.

## Environment

Unchanged from 2026-10-28: one venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`.
Podman + `docker.io/zeek/zeek:8.0` on this box; no libvirt, no docker, no Splunk/Elastic.
