# WORK_ORDER_console_canvas — built record (archive)

Moved here **verbatim** from [`docs/WORK_ORDER_console_canvas.md`](../../WORK_ORDER_console_canvas.md)
on 2026-09-19, when the order was fully built (S1–S7, S5.1 a–e), under the work-order size rule in
`docs/CONVENTIONS.md` §3–§4: a work order keeps the *active* spec; a built slice's record lives here.
Blocks appear in their original order, each preceded by an HTML comment giving its original line
range, and every heading is unchanged — so a citation such as "`WORK_ORDER_console_canvas.md` §18.3"
or "§3 S5.1.b" resolves to the same heading in this file. Nothing was summarized or dropped.

<!-- original lines 3-49 of WORK_ORDER_console_canvas.md -->
> **STATUS: S1 BUILT 2026-09-05/06 (§12); S2 BUILT 2026-09-08 (§13); S3 BUILT 2026-09-09 (§14,
> + §14.6 post-ship drill-down id fix); S4 BUILT 2026-09-10 (§15); S5 BUILT 2026-09-10 (§16);
> S6 BUILT 2026-09-10 (§17) and its **§17.5 acceptance residual CLOSED 2026-09-11** (a real-compiler
> apply through the SPA's own HTTP path; CHANGELOG 2026-09-11 "Console S6 acceptance CLOSED");
> **S5.1.a BUILT 2026-09-15 (§18)** — selection drives editing, iteration 7 of the ten-iteration run
> (CHANGELOG 2026-09-15 "Console S5.1.a BUILT"); **S5.1.b + S5.1.c BUILT 2026-09-16 (§19)** — clone and
> drag, and hosts generated on a fresh segment, over a new all-or-nothing batch path, iteration 8
> (CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"); **S5.1.d + S5.1.e BUILT 2026-09-16 (§20)** — pending
> drawn and counted by gesture, whole-gesture undo, and the **draft that closes GAP-094**, iteration 9
> (CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"); and **S7 BUILT 2026-09-16 (§21)** — the Grafana
> panel now FRAMES the Console at its own origin and **D5 is executed**: the plugin's Track C Imprints
> screen is deleted, "Deploy as twin" survives as a slim confirmed action, and `docs/PRODUCT_GAPS.md`
> GAP-083 is closed (iteration 10; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED").
> **THIS ORDER HAS NO OPEN SLICE: S1–S7 and S5.1 a–e are all built.**
> Two post‑build items opened 2026‑09‑11 from a review of the live canvas: **level‑0 rendering**
> (`docs/WORK_ORDER_console_level0_rendering.md` — **R5 + R7 BUILT 2026‑09‑11** (its §9, closing
> GAP‑079 and GAP‑082), **R1 BUILT 2026‑09‑12** (its §10 — width and tier rails), **R2 BUILT
> 2026‑09‑14** (its §11 — the trunk, the tap badge, the shared volume→width scale, and the
> `axr-layout/1` **plane stamp** that makes S4's absolute pins survive a moving plane), **R3 BUILT
> 2026‑09‑14** (its §12 — each infra device over the column holding its segment with a published
> orthogonal stub, role badges replacing the unlabeled histogram bar, and `PLANE_EPOCH` → 2);
> **R4 BUILT 2026‑09‑15** (its §13 — the east‑west band is volume‑weighted **arcs** anchored at each
> site group's bottom edge, `lanes.ts` **deleted**, and each asserted conduit drawn as its own arc in
> the edit colour, dashed when the wire carried nothing); **R6 BUILT 2026‑09‑15** (its §14 — tiles
> ranked by placed‑host count with a per‑group budget of six and a `+N more` tile that discloses the
> **volume** it hides, the byte figure on the tile, and `PLANE_EPOCH` → 3) — **with which EVERY slice
> of that order is BUILT**; supersedes §14.5's lane plan. One follow‑up is named there rather than
> left implied: the map still does not fit at a 1600 px window, and **column wrapping** is its own
> unwritten slice)
> and **S5.1** (§3 — the
> built S5's context‑free edit forms replaced by selection‑driven editing, clone‑and‑drag, host
> generation: **S5.1.a BUILT 2026‑09‑15 (§18)**, **S5.1.b + S5.1.c BUILT 2026‑09‑16 (§19)**,
> **S5.1.d + S5.1.e BUILT 2026‑09‑16 (§20)** — with which **S5.1 is complete**, the draft its
> acceptance had described since the amendment now exists, and `docs/PRODUCT_GAPS.md` GAP‑094 is
> closed; **S7 BUILT 2026‑09‑16 (§21)**, executing D5).** Drafted
> 2026-09-04. Authority: **user**
> for the design this implements —
> `docs/design/15-console-canvas.md` (ratified 2026-09-04: a clean-start SPA, not a Grafana plugin;
> a deterministic prefix-grouped layout with optional pins), with `14-operator-assertions.md` §2–§4
> (six evidence classes, the assertions file, save vs apply, drift flag-only, the ruled first edit
> set) and `13-golden-twin-fidelity.md` §3.4 (the deploy gate whose sub-threshold hosts are ghosts).
> Authority: **implementation (Claude Code)** for this draft's slicing, file layout, endpoint
> mechanics and every call marked as one — each open to veto. **Version-locked: none intended** — no
> `IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no new op type,
> no `scenario_schema_version` or state-store `SCHEMA_VERSION` bump: additive routes, new DTOs, and
> `o2/console/` as a new component directory under `o2/`.
>

<!-- original lines 76-133 of WORK_ORDER_console_canvas.md -->
## 2. What exists today (verified on this branch)

**API.** `axrange/api/app.py` — `create_app`, `_require_write_token` (bearer gate on writes),
`_is_authorized` (the same check, reusable for a conditionally-gated read, as
`/api/deploys/{id}/access?secrets=true` uses it), `_maybe_mount_spa` (mounts a built SPA at `/` from
`settings.spa_dir`, `html=True`). Shipped imprint routes: `GET /api/imprints`, `…/{id}`,
`…/{id}/provenance`, `POST …/plan` (open), `POST …/deploy` (token-gated).

**Settings and serve.** `axrange/api/settings.py` — `spa_dir`/`AXRANGE_SPA_DIR`,
`imprints_dir`/`AXRANGE_IMPRINTS_DIR` (empty ⇒ the package fixtures dir), `twin_configs_dir`,
`api_token`. `serve.py`'s `serve()` threads `spa_dir` and `twin_configs_dir`; `axrange/cli/main.py`
gives `axrange serve` `--spa-dir` (line 847) and `--twin-configs-dir` — **no `--imprints-dir` flag
exists**, only the environment variable. S1.8 adds it.

**Readers.** `axrange/api/imprints.py` — `list_imprints`, `load_imprint_summary`,
`load_imprint_provenance`, `default_fixtures_dir()`, `_imprint_dir`'s traversal guard,
`_cross_check_ids` (directory name canonical; `twin_config.id` and the sidecar's `imprint_id` must
agree). Its docstring already names the adapter this work order adds: it reads the **Track-C
normalized** sidecar, and the 2026-09-04 ruling (`docs/WORK_ORDER_fidelity_scorecard.md` §9.6) makes
the **real compiler shape canonical with an adapter in this reader**. `topology_export.py` —
`load_topology_export(config_id, source_yaml_dir)` plus the `ExportNode`/`ExportEdge`/
`ExportSegment`/`ExportCensus` dataclasses; `None` for an absent sibling file, an error only for one
that exists and fails to parse.

**The export is already served** — `GET /api/scenarios/{id}/topology-export?source=twin-config|imprint`
(`service.topology_export`, `dtos.TopologyExportDTO`). **§10's `GET /api/imprints/{id}/topology` is
therefore not new machinery** (S1.1, Decision D1). `service._resolve_read_ir_path` resolves
scenario → twin-config → imprint with an optional `?source=` pin (LES-196, LES-198), and
`_parse_ir_only` deletes its temp file in a `finally` gated on `is_temp` (LES-199); new read
endpoints keyed by imprint id follow the same shape.

**The real bundle** — `twin-configs-cast/corpapcn-twin/`, S3's subject:
`corpapcn-twin.topology.json` (`schema_version: "1.3"`; 786 nodes, 1437 edges, 32 segments;
`census {entities_total: 786, local: 190, placed: 141, with_role: 356}`; `windows: 11`; per-node
`windows_seen`, `role_confidence`, `*_evidence_class`, `listen_ports`, `dial_ports`) and
`corpapcn-twin.yaml` (27 segments, 62 node specs, 138 VMs after `count:` fan-out, 22 conduits, one
`kind: router` `gw`), beside `tap-cast.yaml`, `tfc-cast.yaml`, `personas/`, `scripts/`. **Absent:**
any `corpapcn-twin.provenance.json`, `bundle_schema`, `code_fingerprint`, or `observation:` block.
Four measured facts the slices depend on:

- **The imprint reader cannot see this bundle.** `list_imprints("twin-configs-cast")` returns `[]`
  and `load_imprint_summary` raises `ImprintDataError: missing corpapcn-twin.provenance.json` —
  listing requires a loadable YAML + sidecar pair.
- **CIDRs are relocated.** Every twin-config segment CIDR differs from the export's (`seg-10-1-10-0`
  is `10.1.10.0/24` observed, `10.200.0.0/24` deployed). The join key is the segment **name**; the
  difference is a *declared transformation* (`14` §2), not a mismatch.
- **One budget does not hold.** The largest cast group is `count: 15` and no segment carries more
  than 5 role bands — but the largest (segment, role) band in the export is **27 hosts**
  (`10.80.72.0/24`, `generic-endpoint`), over `15` §3's 15-chip budget, and a cast node spec carries
  **no member keys**, so "one chip per group with its `count:`" is not computable (open item O6).
- **Not everything has a home.** 596 nodes are non-local; 49 are local with no segment (O7).

**The plugin.** `o2/grafana/plugins/axellio-axrange-control-panel/` builds with webpack
(`npm run build`) and tests with jest (`npm run test:ci`); `npm run typecheck` carries a ~118-error
pre-existing baseline and is not a regression signal (LES-042). Its `ImprintsScreen.tsx` calls
`summary.mean_confidence.toFixed(2)` and reads `out_of_vocab_pct`, so **relaxing either field to
optional breaks the shipped screen at runtime** — which constrains S1.8. Nothing is ported
(`15` §9, ruled).

<!-- original lines 141-445 of WORK_ORDER_console_canvas.md -->
### S1 — API additions (`axrange/api/`) — **M, mostly ungated**

`15` §10's seven additions plus the plumbing that points them at real bundles. Clean DTOs only
(`docs/design/11-api-ui.md`); mutating routes behind `Depends(write_token)`; new readers beside the
existing ones, never importing `aximprint`.

**S1.1 `GET /api/imprints/{id}/topology`** — `TopologyExportDTO` for an imprint id. **Not a second
reader**: `service.topology_export(settings, id, source="imprint")` under an imprint-shaped route
(LES-025/LES-037). The one addition is resolving the *real* directory the `<id>.yaml` lives in, never
a materialized temp path (`load_topology_export`'s docstring is explicit).

**S1.2 `GET /api/imprints/{id}/cast`** — a new `CastSummaryDTO`: `twin_config_id`, `description`,
segment / node-spec / VM / conduit counts, the `params` map (including `DEPLOY_MIN_OCCUPANCY` where
present), `code_fingerprint`, `bundle_schema`, `sidecar: {present, path}`. Every field aximprint does
not emit yet is `Optional` and reported absent, never defaulted — `code_fingerprint: null` says "this
bundle predates the fingerprint", where `0.0` would say something false (the house rule `imprints.py`
already enforces for `mean_confidence`).

**S1.3 `GET`/`PUT /api/imprints/{id}/assertions`** — the assertions file (`14` §3) as JSON with an
etag. `GET` returns `{schema, imprint, as_of, source, add[], override[], suppress[], etag}` and sets
an HTTP `ETag`; a file that does not exist yet is a 200 with empty lists and `etag: null` (the first
save creates it). `PUT` is token-gated, carries the etag, and refuses a stale one — **409**, naming
the on-disk etag, never a silent merge. The etag is accepted in the body as well as in `If-Match`, so
a header-stripping proxy cannot turn a guarded write into an unguarded one (the reason
`DELETE /api/deploys/{id}` accepts `allow_destroy` twice); 409 rather than 412 follows from that.
Write-time validation is `14` §3's closed set — `add` takes `segment`/`host`/`conduit`,
`override.field` is per-kind closed, `route` is refused by name, a key in both `add` and `suppress`
is a refusal — every refusal naming the accepted set. **axRange validates and writes; it never
interprets precedence.**

**S1.4 The provenance endpoint gains the six classes and the real sidecar shape** — the adapter
`imprints.py`'s docstring already names: read a real compiler sidecar (path-addressed fields plus the
population block, per-field extras including `confidence` and `signal`) and fold it to the published
row shape, with `evidence_class` from the **six**-class vocabulary and an `overrides: {class, value}`
block where an assertion outranks another class. Keep the normalized fixture path working — detect
the shape, do not guess — so the existing imprint tests stay green and the plugin keeps its non-null
fields. **Gated** on seam 2; the adapter and its tests can be written first against a hand-made
real-shape fixture.

**S1.5 `POST /api/imprints/{id}/apply`** — save (S1.3's writer, same validation and etag rule), then
recompile by running aximprint's CLI as a subprocess (`14` §4, v1), then return the new
`code_fingerprint`, exit status and stderr tail. Token-gated. Long-running, so it follows
`11-api-ui.md`'s async-deploy posture: return an `apply_id` immediately, run in the background, let
the SPA poll. The invocation is Decision D2 and seam 4; the endpoint's contract does not change with
the answer, which is what the file-as-contract buys. **Gate lifted 2026-09-05:** aximprint's
reference-ingest S2 shipped `cast --imprint-id <id> --assertions <path>` (a shape error exits 2 with
one line on stderr; success prints JSON with `imprint_id` and record counts), so this slice is
ungated on the aximprint side and waits only on S1's own API work.

**S1.6 `GET`/`PUT /api/imprints/{id}/layout`** — the pins file, `{"schema": "axr-layout/1",
"imprint": …, "pins": {"site:10.80": {x, y}, "segment:10.80.31.0/24": {x, y}}}`. Same etag
discipline, token-gated, **never read by anything but the SPA** — it is not evidence, and no engine
or compiler path may consume it. Keys are `site:<prefix>` and `segment:<cidr>` only; a host key is
rejected (`15` §4: hosts are never pinnable).

> **Amended 2026-09-14 by level-0 R2** (`docs/WORK_ORDER_console_level0_rendering.md` §11.4): the
> document gains an **optional `plane` stamp** — `"plane": "l0-<epoch>-<hash>"`, the layout plane the
> pins were authored against. The API treats it as an **opaque token**: shape-checked (≤64 chars from
> `[A-Za-z0-9._:-]`, bounded because a browser writes this file into a git-tracked bundle),
> round-tripped, **never interpreted** — the plane belongs to the code that draws it, and a server
> deciding whether a stamp is current would be a second opinion about a layout it does not draw.
> **Absent stays absent** (a legacy file read and re-saved unchanged is byte-identical; `plane: null`
> is never invented; an empty string is refused with the reason). The SPA compares it and accepts
> legacy/stale pins **with a note** rather than drawing them wrong or dropping them (LES-289).

**S1.7 `GET /api/imprints/{id}/diff?since=<fingerprint>`** — added / removed / changed segments,
nodes and conduits as stable keys. v1 compares the current bundle against a snapshot the apply
endpoint records under the previous fingerprint; with no snapshot it returns `{known: false}` and the
SPA skips the highlight rather than inventing one. **Partially gated** — meaningful once a
fingerprint exists.

**S1.8 Catalog plumbing** — add `--imprints-dir` to `axrange serve`, threading `imprints_dir` exactly
as `twin_configs_dir` is threaded. Make `list_imprints` tolerate a sidecar-less bundle so
`AXRANGE_IMPRINTS_DIR=twin-configs-cast` lists `corpapcn-twin` — Decision D3, because the naive
relaxation breaks the shipped plugin.

**Acceptance.** Full pytest suite green, with new tests covering at least: each new route against the
**package fixture** *and* against the **real `twin-configs-cast/corpapcn-twin` bundle** (LES-185 — a
hand-written fixture cannot prove this works on what ships; the 786/1437/32 counts and the census
block are the assertions); a `PUT …/assertions` round-trip; a stale-etag `PUT` returning 409 with the
file byte-identical; a rejected record kind naming the accepted set; a traversal id (`../../etc`)
404ing everywhere; token enforcement on all four mutating routes; `GET …/cast` on a sidecar-less,
fingerprint-less bundle returning nulls, not zeros.

### S2 — SPA scaffold (`o2/console/`) — **S, ungated**

Vite + React 18 + TypeScript; the three-pane shell (outline / map / inspector); the router with the
URL as the zoom; a typed API client and hooks; a production build `AXRANGE_SPA_DIR` can mount; a dev
proxy for `/api`. No layout and no data rendering beyond a placeholder — this slice exists so S3
lands on a working frame.

Two mechanical decisions taken here because `15` §3/§7's notation is not directly expressible: a CIDR
in a path segment carries a `/`, so **the prefix length is written with a hyphen** —
`/imprints/corpapcn-twin/segments/10.80.20.0-24` — rather than `%2F`, which some proxies mangle; and
`?occupancy<0.8` is spelled `?occupancy_lt=0.8`.

**Acceptance.** `npm run build` produces `dist/`; `axrange serve --spa-dir o2/console/dist` serves it
at `/` with the API intact at `/api/*`; `npm run dev` proxies `/api` to a local `axrange serve`;
`npm run test` runs a passing placeholder Vitest suite; `npm run typecheck` is **clean from zero** (a
new package — LES-042's tolerated baseline belongs to the plugin and must not be inherited).

### S3 — Read-only levels 0–2 and the matrix — **L, ungated**

- **Layout modules** — pure TypeScript, no React, no `react-flow`/`d3-force`/`dagre`: tier assignment
  for level 0, prefix grouping into sites, tile placement in CIDR order, east-west conduit lanes, the
  level-1 spine with role bands in the fixed role order, and the matrix's row and column ordering.
  Input is export + cast; output is coordinates. §5 is their contract.
- **Level 0** — the tiered path diagram: Internet, WAN edge, infrastructure, segment tiles grouped by
  /16 with single-segment /16s sharing one group, the east-west band. Each tile carries CIDR,
  deployed and ghost counts, occupancy, a role histogram and an evidence-mix ring.
- **Level 1** — the spine drill: router leg on top, role bands off the left (two chips per row, one
  spoke per row), conduit peers and the way off the segment on the right, a *below threshold* band.
- **Level 2** — the facts table: keys and names, role with class / confidence / source, listen set
  with protocols, dial set, edges grouped by peer role and peer segment, persona, every field's
  evidence, assertions affecting the host.
- **Matrix** — roles × roles at level 0 (10 distinct local roles plus `external` in the real data,
  well inside the 40 × 40 budget), hosts × hosts inside a segment; a cell opens its protocols and
  edge list in the inspector.
- **Cross-cutting** — the six-class encoding with confidence as saturation; ghosts as dashed outlines
  (below `DEPLOY_MIN_OCCUPANCY`, dropped by the cast, suppressed, or referenced-but-unseen); the
  outline tree carrying the same encoding as dots; search over name / address / role / CIDR; the
  `?ghosts=`, `?class=`, `?occupancy_lt=` filters.

**Acceptance.** Golden tests per §5, plus: `corpapcn-twin` renders 32 level-0 tiles, 22 conduit lanes
*(**level-0 R4, 2026-09-15**: the same 22 conduits, now drawn as **asserted arcs** — the real-bundle
assertion is renamed with the module it tests, `lanes.ts` having been deleted)* and the six
infrastructure devices *(**level-0 R6, 2026-09-15**: the twin still HAS 32 segments — every one in
the outline, the matrix, search and its own drill-down — but the **map** now draws **18 tiles**, each
site group's busiest six plus a `+N more`, and **32 again** with every group expanded. The
real-bundle assertion is restated as that pair, and the 22 asserted arcs are counted on the expanded
drawing, where one arc per conduit is a claim that can hold)*; `10.80.20.0/24` opens a level-1 spine with its real bands; a host
opens a facts table whose listen set matches the export's `listen_ports` exactly; the matrix's cell
totals sum to the export's edge flows; every element's evidence class is the export's own
`*_evidence_class`, never inferred; the 49 unplaced and 596 non-local entities are reachable and
counted (O7); no view exceeds its stated budget on this bundle.

### S4 — Pins — **S, ungated**

Drag a segment tile or a whole site; persist through `PUT …/layout`; *reset pins* returns to the pure
function. **Acceptance:** a pin survives a reload and an apply; an unpinned neighbor's coordinates are
unchanged by a pin (a golden test); `reset` restores coordinates byte-identical to the no-pins render;
a host pin is refused by the API and unreachable in the UI.

### S5 — Editing and save — **M, ungated**

The first edit set of `14` §4, each gesture producing exactly one record kind (`15` §8's table), with
the three visible states — *pending* (browser only, hatched), *saved* (in the YAML, outlined),
*applied* (in the cast, rendered `asserted`) — and the change-list panel showing the diff between the
current cast and the assertions file, each entry tagged with the kind of change it will be for a
standing twin. **The tag is derivable from the record kind alone** — an `add` or `suppress` flows
through `te deploy --update`, an `override` of an existing node is a fresh deploy
(`docs/WORK_ORDER_twin_incremental_update.md` §3.1) — which is fortunate, since the bundle publishes
no mapping from an entity key to the cast node spec it landed in (§2).

**Acceptance.** Each of the seven gestures produces the exact record `14` §3 specifies, verified by
reading the written YAML; a stale-etag save surfaces a reload prompt and does not write; a no-op save
writes a byte-identical file; removing an assertion in the inspector deletes the record; the change
list's tag matches the rule above for every kind; reducer unit tests cover pending → saved → applied
without a browser.

#### S5.1 — Scope amendment, 2026-09-11 (Authority: user): selection-driven editing, clone-and-drag, host generation

**Status: COMPLETE — S5.1.a BUILT 2026‑09‑15 (§18); S5.1.b + S5.1.c BUILT 2026‑09‑16 (§19); S5.1.d + S5.1.e BUILT 2026‑09‑16 (§20) — a post‑build amendment to the S5 that
shipped 2026‑09‑10 (§16).** Its record layer (`src/edit/records.ts`), reducer, three states, change
list and save path all stand; what changes is the **interaction model** on top of them. *(The acceptance below
assumed a draft that did not exist until 2026‑09‑16: S5.1.d built it as its own module,
`src/edit/draft.ts` rather than `edit/store.ts`, closing `docs/PRODUCT_GAPS.md` GAP‑094 — §20.)*

**Why.** S5 as built is confusing in use (user, 2026‑09‑11, on the live canvas): the
`EditPanel` presents "Add a segment" / "Add a conduit" as **context‑free forms** with no relation to
what is selected on the map; "add host" is reachable only from a segment's inspector and is one host
at a time; a pending edit has no presence on the level‑0 map; and the only drag the map understands
is S4's pin. The operator's actual task — *"add a new segment and a bunch of hosts behind it"* — has
no direct path. This amendment changes how gestures are reached and lets one gesture stage many
records; it does not touch `15` §8's record kinds: everything below still emits only `add` /
`override` / `suppress` records of `axr-assertions/1` through `buildRecord`. **A gesture may emit a
batch of them** (a refinement of `15` §2.5, recorded in `15` §8), never a new kind.

**S5.1.a — Selection drives editing.** ✅ **BUILT 2026‑09‑15 (§18).** Selecting a tile (level 0), a spine (level 1) or a chip
opens that element in the inspector with its facts **and its actions**: on a segment — *Add host
here*, *Clone this segment…*, *Add conduit to…*, *Suppress hosts…*; on a host — *Change role*,
*Override image / resources*, *Suppress*. The free-standing `EditPanel` forms are removed (their record building stays). "Add a segment" moves
to a `+` on each **site header** (and the outline's site row) so a new segment is born inside a
site, prefilled with the next free `/24` in that site's two-octet prefix; a site-less add is still
possible from the outline root and lands in `misc`.

**S5.1.b — Clone and drag.** ✅ **BUILT 2026‑09‑16 (§19).** Two entry points to one gesture (an implementation call, open to veto):
**Alt-drag a tile** drops a hatched *pending* clone tile where the pointer is released; the
inspector's **Clone this segment…** does the same and then asks for a placement click (keyboard and
touch path). The drop position is an S4 pin on the new segment key and nothing else reflows. The
clone form opens on the new tile:

| field | default | record it feeds |
|---|---|---|
| CIDR | next free `/24` in the source's site | `add: segment` `key` |
| name | `seg-<cidr dashed>` | `add: segment` `name` |
| zone type | the source's | `add: segment` `zone_type` |
| hosts to bring | **all placed hosts**; or *by role* (multi-select from the source's role histogram); or *first N by address* | one `add: host` per chosen host |
| address mapping | **same host part** (`address:10.80.20.5` → `address:10.80.31.5`); or *renumber from .10* | `add: host` `key` |
| copy | role, `listen`, `dial` always; `image`, `resources` when the source host has an asserted/overridden value | `add: host` fields |
| mirror conduits | off | one `add: conduit` per conduit the source segment has, re-keyed to the clone |
| note | required, once | propagated to every record as `cloned from <source cidr>: <note>` |

Rules the form enforces, each a refusal with a reason, never a silent skip: a **hostname-keyed**
source host is not clonable (a hostname names one machine) — it is listed under *not cloned* with
its count, and the operator may add such hosts by hand afterwards; a mapped address that already
exists in the export or in a pending add is a collision (offer *renumber*); the CIDR must be the
network address and must not collide with an existing or pending segment; the batch is refused
whole if any record would be refused by the writer's closed sets. **Nothing observed is copied as
evidence:** every cloned record is `asserted` with the operator's note; the source's evidence
classes and confidences do not travel.

*(Asked by S5.1.a, §18.6; **RULED and BUILT 2026‑09‑16** — Authority: implementation (Claude Code),
open to veto, §19.2 call 1.)* **Exact versus overlap, for a clone's CIDR.** The line above ("must not
collide with an existing or pending segment") did not say which of the two it meant, and a `1 + N`
batch refused or accepted on that difference is not the same gesture. **The ruling:** an **exact**
match is **refused** — an observed segment (whatever its standing against the cast), a segment the
cast declares (joined by name through the roster and this file's renames), or a pending/saved/applied
add — because *a clone creates a NEW segment, and reaching an existing one is* Add host here *on it*;
an **overlap only warns**, exactly as S5.1.a's site `+` does (axRange's validator refuses overlapping
networks, NET‑010, unless the cast is relocated, which nothing on this side can tell). **The deliberate
asymmetry with the site `+`:** S5.1.a *allows* an exact add of an observed segment the cast does not
declare, because such an add **confirms** that segment — a clone of it would not. Host-level
collisions are refused per host, with a *renumber* offer. Implemented as `cloneCidrRefusal`
(`src/edit/clone.ts`) over S5.1.a's shared `claimOn`.

**S5.1.c — Generate hosts on a fresh segment.** ✅ **BUILT 2026‑09‑16 (§19).** The add-segment form (from a site's `+`) carries an
optional **hosts** block: count *N*, role, listen set **prefilled from the role catalog**
(`model/roles.ts` gains a per-role default listen set, sourced from the persona library's `emits[]`
where one exists, else empty and said so), addresses allocated from `.10` upward, image/resources
left to the compiler. Emits `add: segment` + N × `add: host` through the same batch path as a clone.

**S5.1.d — Pending is visible on the map, and the change list counts it.** ✅ **BUILT 2026‑09‑16 (§20).** *(S5.1.b, §19, met part of this early: a
**placed** added segment — a clone — was already drawn hatched at level 0 and held when its group was
collapsed. §20 built the rest — unplaced tiles, the level‑1 hatched chips, the per‑gesture change list,
whole‑gesture undo and the draft — and also closed the item this list did not originally have:
**cleaning up a removed clone's pin**, which an undo and a draft discard now drop. What is left of that
thread is `docs/PRODUCT_GAPS.md` GAP‑096, a pin written at STAGE time for a still‑pending segment.)* A pending clone or
generated segment is a hatched tile at level 0 with hatched chips at level 1 (the *pending* state
`15` §8 already names); the change-list panel shows one entry per **gesture** ("clone of
10.80.20.0/24 → 10.80.31.0/24 · 1 segment · 24 hosts · 3 conduits · pending") that expands to its
records, and carries the S5 tag (`te deploy --update`-class for adds). Undo of a gesture removes its
whole batch; a record can also be removed singly in the inspector.

*(Added 2026‑09‑15, from S5.1.a — §18.5/§18.6; ✅ **BUILT 2026‑09‑16, §20**.)* **S5.1.d also owns the
DRAFT.** Until §20, pending edits lived in browser memory only — a reload, the breadcrumb, the outline's
imprint links and the browser's Back all discarded them, with only **Escape** guarded (S5.1.a). S5.1.d
built the per‑imprint draft the acceptance below already assumed — as its own module, `src/edit/draft.ts`
rather than `edit/store.ts`; keyed on the canonical imprint id; **reconciled, never merged** (LES‑251),
running the staging paths' own refusals on the way back in (LES‑335) — plus one guard on every in‑app way
out of an imprint that still holds pending edits, which closes `docs/PRODUCT_GAPS.md` GAP‑094.

**S5.1.e — Level 1 gestures unchanged** ✅ **BUILT 2026‑09‑16 (§20)**, with one addition: *Add host here* on a spine opens the
single-host form prefilled with the segment and the next free address.

**Acceptance (added to S5's).** A clone of the real bundle's `10.80.20.0/24` with defaults emits
exactly `1 + N + 0` records (N = its address-keyed placed hosts; the two hostname-keyed devices, if
any, are reported as not cloned), every host key re-based with the same host part, every record
carrying the propagated note; the written YAML validates against the vendored schema and re-reads
byte-identical after a canonical re-emit; a mirrored-conduits clone adds exactly the source's
conduit count, re-keyed and sorted; a collision (existing address, existing CIDR, hostname key,
duplicate pending) is refused with the specific reason and no partial batch is staged; undo of the
gesture leaves the assertions document byte-identical to before it; the pending clone tile renders
hatched at its drop position, survives a reload of the same URL (pending edits persist in the
store's draft, `edit/store.ts` — *a draft that does not exist yet: S5.1.d builds it, see above*), and
its chips render hatched at level 1; keyboard-only users can complete a clone via the inspector
button + placement; the site `+` prefills the next free `/24` and refuses a taken one (*met by
S5.1.a, §18.3*).

**Sequencing.** S5.1 is a follow‑on to the built S5, sized **M**. Build S5.1.a first (it is what
makes the existing seven gestures discoverable and can ship alone), then S5.1.b–c on the batch
path, then S5.1.d–e. The pending clone tile's *placement* is a pin, so S5.1.b needs
`docs/WORK_ORDER_console_level0_rendering.md` **R1–R4** first (the tile geometry it drops into);
S5.1.a does not. *(**All of that geometry now exists** — that order's R6 closed it 2026-09-15, its
§14. One case S5.1.b did not have to consider when this was written and does now: a clone dropped
**into a group the per-group budget has collapsed**, where a pin is kept but not drawn until
`?expand=` opens the group — the same "held for collapsed tiles" state that order's §8 records.)* Apply (S6, §17) needs no change: a batch is N records in the same file.

### S6 — Apply and refresh — **M, GATED on aximprint**

Apply saves, recompiles and refreshes: the SPA polls the apply job, receives the new
`code_fingerprint`, reloads the bundle, and highlights for five seconds what changed against the
previous fingerprint. Unfinishable until `aximprint cast` reads the assertions file and emits a
fingerprint (seams 3 and 4); until then the UI can be built against a stub apply that recompiles
nothing and returns the current fingerprint — honest scaffolding, not a shippable feature, and the UI
must say so.

**Acceptance.** An apply on a saved file returns a new fingerprint; the SPA reloads and the changed
elements highlight; a compile failure surfaces the compiler's own stderr, never a generic error; an
apply with unsaved pending edits saves them first (apply *is* save + recompile); a no-op apply is a
no-op recompile with the twin's `plan_id`/`ir_hash` unchanged (`14` §3's determinism rule — the check
that catches a compiler that is not actually deterministic).

### S7 — The Grafana embed — **S, ungated, deliberately last** — ✅ **BUILT 2026-09-16 (§21)**

An iframe panel or a thin plugin mounting the same React root (`15` §9). Small by construction: the
SPA touches no Grafana API and authenticates with the existing bearer token. Whether the plugin's
Track C imprints screen retires here is Decision D5.

*As built (§21): **an iframe**, and the choice was forced rather than made — the SPA calls same-origin
`/api` and Grafana's origin implements no CORS, so a shared React root would put every read on an
origin the API refuses. **D5 is executed:** the Imprints screen and its `initialView` option are
deleted (GAP-083 closed), and "Deploy as twin" survives as pick → preview → confirm → deploy, rendering
no belief-store measurement at all.*


<!-- original lines 680-724 of WORK_ORDER_console_canvas.md -->
## 11. Sequence and gating

> **2026-09-06: S1 is BUILT in full — the gates below are discharged** (§12). S1.4, S1.5 and S1.7
> all landed, and **S6's gate is lifted with them**; the remaining sequence is S2 → S3 → S4/S5 →
> S6 → S7, none of it gated on aximprint.
>
> **2026-09-08: S2 is BUILT** (§13, the SPA scaffold). **2026-09-09: S3 is BUILT** (§14, the
> read-only drawing slice — levels 0–2 + the matrix over pure golden-tested layout modules).
> **2026-09-10: S4 is BUILT** (§15, pins — absolute overrides with no reflow, drag + `PUT …/layout`
> persistence + reset). **2026-09-10: S5 is BUILT** (§16, editing and save — seven edit gestures,
> each producing exactly one `axr-assertions/1` record; three visible states + the change-list panel;
> save via `PUT …/assertions` with the 412 reload-not-retry rule; apply was S6, not built there).
>
> **2026-09-10: S6 is BUILT** (§17, apply and refresh — Apply saves any pending edits, POSTs the real
> `POST …/apply`, and on a genuine change reloads the bundle and highlights what changed for five
> seconds; on a byte-identical recompile it says so with no highlight; and — the review must-fix — on
> a changed-but-undiffable apply it renders that honestly too, rather than looking like a no-op).
> **S7 (the Grafana embed, deliberately last) is next and is the last slice in this work order.**
> §13.2's residual (the first live apply through the SPA proving the real compiler writes an authored
> key into the new sidecar) is **still open**, tracked as a standing acceptance item run separately
> from the frontend build's own gate.

```
S1 ─┬─ 1.1 topology · 1.2 cast · 1.3 assertions · 1.6 layout · 1.8 plumbing  [ungated]
    ├─ 1.4 provenance six classes ...................................... [gated: seam 2]
    ├─ 1.5 apply ....................................................... [gated: seam 4]
    └─ 1.7 diff .............................................. [partially gated: seam 4]

S2 scaffold [ungated] ─▶ S3 levels 0-2 + matrix [ungated] ─┬─▶ S4 pins [ungated]
                                                           └─▶ S5 edit + save [ungated]
                                                                  └─▶ S6 apply + refresh [GATED]
                                                                          └─▶ S7 Grafana embed
```

**Start now, with nothing from aximprint:** S1's ungated additions, S2, S3, S4, S5 — the whole
read-only Console plus authoring and saving, enough to exercise the display half of the loop and to
write real assertions against the standing `corpapcn-twin` bundle. **Gated on aximprint's
reference-ingest work** (`docs/WORK_ORDER_reference_ingest.md`, to be written): S1.4, S1.5, S6 and
S1.7's usefulness. Per `13` §7 that work follows the first golden, **with the Console built alongside
it** — exactly what this slicing allows.

No live-verify gate applies here: nothing in this work order deploys, destroys or otherwise touches a
cluster. The one adjacent write path — "deploy as twin", already shipped — is untouched and keeps its
own pending gate (`docs/WORK_ORDER_aximprint_console.md` §4).


<!-- original lines 725-2341 of WORK_ORDER_console_canvas.md -->
## 12. S1 built as, 2026-09-05/06

**S1 is BUILT and its live path is proven against the real compiler.** CHANGELOG 2026-09-06
"Console S1 BUILT" is the record; this section is the work order's own account of where the build
**resolved or deviated from** §3's S1.1–S1.8 — the part §3 cannot tell a later reader. **S2–S7 are
untouched and still open.**

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump — exactly as the status header intended.

**Tests:** `tests/test_api_console.py` — **292 tests**. Full suite **2618 passed / 12 skipped /
0 failed / 0 xfailed (2630 collected)**.

### 12.1 What landed

Two new leaf modules, one vendored schema, eight new routes.

- **`axrange/api/console_files.py`** — the two operator files beside a cast bundle:
  `<id>.assertions.yaml` (`axr-assertions/1`) and `layout.json` (`axr-layout/1`). Content-hash
  etags; a per-file blocking lock; `mkstemp` + `os.replace` preserving the target's mode (0644 for
  a new file); `as_of` coerced from the `datetime` PyYAML resolves a bare ISO timestamp to; `kind`
  stripped on write except on `add` records and derived on read; a non-IP `address:` key **warned**,
  a strict CIDR in an `address:` key **refused** naming the network form; key order preserved on
  dump. **Every closed set is derived at import time from the vendored schema.**
- **`axrange/api/recompile.py`** — a `RecompileBackend` protocol plus `SubprocessRecompile` running
  **both** producer stages (`aximprint cast …`, then `aximprint install-cast …`), with the cast's
  flags read from the bundle's own `cast_invocation` block and the sensors recovered from its
  `tap-cast.yaml`. Content fingerprint (sha256 over `<id>.yaml` / `.topology.json` /
  `.provenance.json`) as change detector and `.history/<fp>.json` key (keep 5, versioned
  `axr-bundle-snapshot/1`, carrying **both** populations plus the sidecar's `population` block).
  stdout parsed **before** bounding (head-kept), stderr tail-bounded. `DEFAULT_TIMEOUT_S` 120 s.
  Typed failures: exit 2 → **422**; other non-zero or timeout → **502** (naming a partially
  installed bundle when stage 2 fails after stage 1); unset/unusable `AXRANGE_AXIMPRINT_BIN` /
  `_STORE` → **503 naming the key**.
- **`axrange/api/schemas/axr-assertions-1.schema.json`** — a byte-identical vendored copy of
  aximprint's published schema, with a **hard** closed-set drift test and a **soft** byte-compare
  test, both resolving the sibling repo via `AXIMPRINT_REPO` or `../aximprint-axrange` and skipping
  when absent. `pyproject.toml` carries package-data for the schema only.
- **Routes** (all `/api/imprints/{id}`): `GET …/topology` (**S1.1**, the D1(a) alias, pinned by an
  identity test), `GET …/cast` (**S1.2**), `GET`/`PUT …/assertions` (**S1.3**), `GET`/`PUT …/layout`
  (**S1.6**), `POST …/apply` (**S1.5**), `GET …/diff?since=` (**S1.7**). **S1.8** added
  `axrange serve --imprints-dir` and the tolerant listing. **S1.4** landed as the six-class
  provenance adapter with `mean_confidence` computed from the canonical sidecar and its coverage
  published in `confidence_source`.
- **One positive id regex** — `[A-Za-z0-9][A-Za-z0-9._-]*` — shared by `resolve_imprint_dir` (the
  **one** traversal guard) and the service.
- **Rider** on `axrange/fidelity/reference.py`: the sidecar-absent warning reworded to describe the
  bundle, plus read-only `bundle_schema` / `code_fingerprint` / `cast_invocation`. No scoring
  effect; `to_dict()["fingerprint"]` untouched.

### 12.2 Four rulings (Authority: implementation (Claude Code))

1. **aximprint's compiler is the reader of record for `axr-assertions/1`.** axRange's writer accepts
   and refuses exactly what it does.
2. **The vocabulary is a vendored artifact the code derives from, never a restated literal.** Three
   axRange-only rules live in code with their reasons attached (`imprint` required; `segment` and
   `name` required on an `add` — the compiler fails both at compile time anyway).
3. **The apply runs both producer stages and reads its argv from the bundle**, refusing by name when
   `cast_invocation` is absent. A cast alone does not update a bundle: it writes generic stems, and
   only `install-cast` names them `<id>.*`.
4. **The change detector is a content fingerprint, never the build hash.** `code_fingerprint` is the
   compiler's build hash — identical across two different casts of the same code — so using it made
   `changed` permanently false and keep-5 into keep-1.

### 12.3 Four deviations from §3, KEPT — this section supersedes those lines

- **§3 S1.3 says 409 on a stale etag; the build returns 412.** 409 became the *apply-in-progress*
  answer, a different situation with a different recovery, and conflating them would leave an
  operator unable to tell "reload and retry" from "wait". The reasoning §3 gives for accepting the
  etag in the body as well as `If-Match` is unchanged and implemented; only the status code moved.
- **§3 S1.7 says `{known: false}` for an unknown `since=`; the build returns 404 naming the known
  fingerprint set.** An unknown fingerprint is a bad request about a resource that does not exist,
  and naming the known set is what lets the SPA (or an operator with `curl`) recover.
- **§3 S1.5 and D2(a) say `apply_id` + poll; the build is a SYNCHRONOUS apply behind a per-imprint
  lock.** D2(a)'s "a background thread inside `axrange serve`" holds structurally — a sync FastAPI
  `def` runs off the event loop — and a real apply measures **2.7 s**, so a poll protocol would be
  ceremony around a request that finishes before the first poll. A concurrent apply is **409**, and
  a `PUT` while an apply runs is refused 409. **Two honest limits:** the lock is **process-local**
  (correct for one `axrange serve`, insufficient for two — contrast the engine's DB-backed
  `resource_locks`), and a reverse proxy needs `proxy_read_timeout` ≥ 120 s. The endpoint contract
  is still written so a helper or the aximprint watcher can replace the backend unseen, which was
  D2's actual requirement.
- **§8.1 D3(a) named only the sidecar-LESS case; the tolerant listing also admits
  canonical-sidecar bundles.** Without the widening, `corpapcn-golden` — the only fully provenanced
  bundle there is — was invisible to the Console, which is the opposite of what D3 was for. The
  listing publishes `skipped: [{id, reason}]`; overrides are published as `evidence_class`.

### 12.4 Three rounds, and what each round's green suite had certified

- **Round 1** (81 tests) passed its unit tests and was **broken end to end**, found by the tester's
  live integration against the real compiler: the written file carried `kind` and the compiler
  refused it (`suppress[0]: unknown key(s) ['kind']`); the apply never updated the bundle; the
  build hash made `changed` always false; and the real stdout has no `record_counts`. The reviewer
  found five must-fix in the same round — a `%2e` id reaching a `PUT` and writing into the catalog
  root; PID-named temp files colliding across threadpool requests (one 500, one **silent
  overwrite**); a hand-authored `as_of` refused because YAML resolves it to a `datetime`;
  `value: null` on an override dropped by the serializer, breaking GET→PUT; and `OSError`s escaping
  as 500.
- **Round 2** fixed those plus fifteen should-fix items.
- **Round 3** fixed the tester's two new live findings — stdout **clipped before parsing** (the real
  cast prints 8356 chars against an 8000 cap, tail-kept, producing a false "not JSON" warning) and a
  `suppress` **invisible to the diff** because only the observed side was snapshotted, with a
  warning asserting a cause it could not know — and the reviewer's four: `mkstemp` narrowing an
  existing file to 0600; `sensors_from_tap_plan` collapsing five degraded states into one silent
  empty tuple; unversioned snapshots read by a later release as "everything added"; and apply not
  accepting the etag inside the document.

Generalized as `docs/LESSONS.md` **LES-230..LES-243**.

### 12.5 The independent proof

On the **real** compiler, not a fake backend: the pinned argv reproduces `--relocate 10.200.0.0/16`
from the bundle (10.200.x output, not source CIDRs); stage 2 lands the `<id>.*` files with
`XPress-SW-A` preserved; the fingerprint moves; `record_counts` reads `{add: 0, override: 0,
suppress: 1}` with `cast_counts` populated; the diff names the suppressed host **on the twin side**
(`generic-endpoint-10-1-1-core` for `address:10.1.10.222`; a different key removes a different id)
with the observed side unchanged and no warning; a second identical apply reports no change; mode
0644 is preserved; wall time **2.7 s per HTTP apply** (the cast alone 0.74 s for 786 nodes); and a
bundle without `cast_invocation` is a **422 naming the block**.

### 12.6 Residual — S2's first item, not a defect

**The diff's twin side is keyed by twin node ids and does not publish the sidecar's
`population.suppressed[]`** (which the snapshot already holds), so the canvas cannot join a removal
back to the **authored key** from the diff alone. S2 either reads `population.suppressed[]` from the
bundle directly or the diff gains an authored-key column; decide it there, with the SPA's needs in
front of you. Everything else the post-apply highlight needs is present.

**SETTLED in S2 — see §13.2:** neither option verbatim. The diff gains
`ImprintDiffDTO.suppressed_keys`, a **set delta** of the authored keys, and a per-removal
`authored_key` column was **refused** as a correlation presented as a join; the SPA also reads
`population.suppressed[]` from `GET …/provenance` for the durable, fingerprint-free answer, where
`matched: false` separates an ineffective record from a real suppression.

### 12.7 Two corrections to the design notes

- `docs/design/14-operator-assertions.md` §3's example omitted `note` on the host `add` and on the
  `override`, while the compiler requires a **non-empty `note` on all three record kinds**. Fixed
  there.
- The sidecar's `population.emitted_hosts` is a **count** (156) and differs from the twin's node
  instances (157) by the synthetic `gw` router — two quantities, one similar name (LES-241).

### 12.8 The producer half

Landed in aximprint as its own commit on `wip/golden-twin`: `cast_invocation` under
`bundle_schema: axr-topology/1.5`; the `install-cast` subcommand (the shipped script becomes a thin
wrapper); the published schema with a parser-agreement test; and a recast of the golden candidate —
only the topology export changed, by the two expected keys.

**§9's seams, restated as they now stand.** **Seam 1** is closed for any bundle cast after
2026-09-04: the export carries `bundle_schema` (`axr-topology/1.4`, then `1.5` with
`cast_invocation`) and the twin config carries the `observation:` blocks
`docs/WORK_ORDER_deploy_gate.md` built the gate on — **but the standing `corpapcn-twin` bundle
predates both**, which is why the Console degrades rather than refuses on it. **Seam 2** is closed
enough to build on: the installer copies the sidecar now, and `confidence`/`signal` and the
`overrides:` record are consumed as they exist. **Seams 3 and 4** are closed — the schema is
published and vendored, and the invocation is recorded in the bundle. **Nothing in S1 is gated any
more, and S6's aximprint gate is lifted with it**: the apply path is built and proven against the
real compiler, so S6 is now ordinary SPA work sitting behind S2/S3/S5.

## 13. S2 built as, 2026-09-08

**S2 is BUILT.** CHANGELOG 2026-09-08 "Console S2 BUILT" is the record; this section is the work
order's own account of where the build **resolved or deviated from** §3's S2 and §4's layout, and
it settles §12.6. **S3–S7 are untouched and still open** — and S6's aximprint gate stays lifted.

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump — exactly as the status header intended.

**Tests:** Vitest **119 passed / 6 skipped** in `o2/console/` (the six are a live-API drift test
that runs only when `AXRANGE_LIVE_API` is set; it ran green against both real bundles). Python
`tests/test_api_console.py` **+75 → 367**. Full suite **2693 passed / 12 skipped / 0 failed
(2705 collected)**.

### 13.1 What landed

A new component directory `o2/console/` (31 checked-in files) and two permitted Python changes.

- **The stack, pinned exactly:** `react`/`react-dom` **18.3.1**, `react-router` **7.18.3**, `vite`
  **8.2.2**, `typescript` **5.9.2** (strict), `vitest` **5.0.0**, `jsdom` **30.0.1**,
  `@testing-library/react` **16.3.3**; `engines: node >=22.12`. `npm run typecheck` is clean **from
  zero** — LES-042's tolerated ~118-error baseline belongs to the Grafana plugin and is explicitly
  not inherited.
- **The three-pane shell** (outline / map / inspector) with the router, wired end to end and
  **nothing drawn**: the outline lists the catalog with `skipped` reasons rendered; the map pane
  shows the live cast summary (counts, the effective deploy-gate threshold that decides which hosts
  are ghosts, the evidence mix) in **three distinct states — loading / failed-with-the-error /
  absent** — where only the third is a statement about the artifact.
- **One URL grammar, defined once in `src/model/zoom.ts` and pure**, behind a **single catch-all
  route**: `/imprints/<id>`, `…/segments/<cidr>` with the prefix length written **with a hyphen**
  (`10.80.20.0-24`, never `%2F`), `…/hosts/<key>` in **both** the flat and the segment-nested
  spelling, and `…/matrix` with `?view=matrix` accepted as an alias and normalized to the path.
  `?ghosts=`, `?class=`, `?occupancy_lt=` parse into typed state, and **every unusable value is
  reported in `problems`** rather than silently dropped; a malformed `%` escape is an error, not a
  fall-back to the catalog root.
- **A typed API client over the eight S1 routes plus the listing** (`api/client.ts`, `context.ts`,
  `hooks.ts`, `types.ts`): one fetch path, abort on unmount, typed error kinds
  (400/401/404/409/412/422/502/503/network/malformed) carrying the server's `detail` **and the etag
  a failed apply returns**. The **write token lives in a module variable for the life of the tab** —
  never compiled in, never in storage — proven by a test grepping the built bundle.
- **`model/keys.ts`** — entity keys (`hostname:`/`address:`/`mac:`), CIDR ↔ route encoding, site
  keys in the hyphen form the server's own id regex accepts. **`model/evidence.ts`** — the six
  classes in precedence order, `compiler` never authorable, **absent confidence is not zero**, and
  `countByClass` reporting unknown classes instead of dropping them.
- **`edit/store.ts`** — pending / saved / applied per `15` §8. **A 412 leaves the pending edit
  intact, REPORTS the server's etag in the conflict record and never adopts it**, and sets
  `mustReload` so a save cannot be re-issued until a real re-read; `applied` is matched by block and
  key; a document belonging to a different imprint is refused on load; `problems[]` is surfaced.
- **`theme/tokens.css`** — light/dark by `prefers-color-scheme` (pinnable with
  `<html data-theme=…>`), the six-class palette with a **fill style per class** so `observed` and
  `referenced` survive monochrome and colour-blindness, plus the pending/saved states. `15` §12's
  `dataviz` pass has its starting point, not its verdict.
- **`axrange/api/app.py` — the SPA history fallback** (`_SpaFiles`, `_spa_wants_index`,
  `_spa_cache_control`). A deep client route gets `index.html`; `/api/*` — tested on the **decoded**
  path, so `%61pi` is excluded too — and asset-suffix misses stay JSON/404s; write methods are 405;
  `index.html` is `Cache-Control: no-cache` and `/assets/` is
  `public, max-age=31536000, immutable`. **Headless is byte-identical when `--spa-dir` is unset.**
  Seven traversal encodings are pinned by test: no file outside `--spa-dir` is ever served.
- **Dev and co-serve.** `npm run dev` binds **`127.0.0.1`** explicitly (Vite 8 defaults to
  `localhost`, which resolves to `[::1]` on this host) and proxies `/api` to `AXRANGE_DEV_API`
  (unprefixed; default `http://127.0.0.1:8080`). Co-serve:
  `axrange serve --spa-dir o2/console/dist --imprints-dir twin-configs-cast`.
- **A cross-language field-set guard.** `tests/test_api_console.py` pins the field sets of the
  **nine Console-read DTOs**, and its failure message names `o2/console/src/api/types.ts` — the
  hand-written mirror it is protecting. It runs in the Python suite, which is the side that runs in
  CI.

### 13.2 §12.6 settled: a suppressed-key SET DELTA, and a per-removal column refused

**Ruling (Authority: implementation (Claude Code)) — (b), server-side, but as a set delta.** The
orchestrator's recommendation was a per-removal `authored_key` column on the twin diff. **The build
refused it as unfaithful.** The sidecar's `population.suppressed` rows are
`{key, note, placed, matched}` and **never name the twin node a suppression removed**; the twin's
group ids are minted by *aximprint's* role × segment × occupancy-rung rule, which axRange must not
reimplement to infer a mapping. With two suppressions and a gate removal inside one interval, such a
column would attribute by correlation and **present the result as a join**.

Published instead, and this is the whole change:

```jsonc
"suppressed_keys": { "added": ["address:10.1.10.222"], "removed": [], "changed": [] }
```

`ImprintDiffDTO.suppressed_keys`, computed by `recompile.suppressed_key_delta` from the
`population.suppressed` accounting **both** snapshots already hold, through one shared
`_suppressed_keys` normalizer. `changed` is always empty by construction — a key is a member of a
set, not a record with fields. The field is **`null`, never empty**, with a warning **stating what
was measured**, when either side carries no accounting (the same null-vs-empty contract
`observed`/`twin` carry, LES-240). **A suppression counts as movement**, so a `suppress` that
matched nothing can no longer report "nothing to highlight" while naming its own key.

The **durable** authority is unchanged and needs no fingerprint: `GET …/provenance`'s
`population.suppressed[]` states which keys the compiled cast honors at any time, and `matched:
false` there separates an **ineffective record** from a real suppression — so an edit that matched
nothing is never reported as applied.

**The one capability given up, stated rather than hidden:** clicking a suppression record and being
shown the specific host it removed, when two or more suppressions land in a single apply.

**Residual, carried to S6:** nothing in the suite proves the **real** compiler writes the key into
the **new** sidecar on an apply. The tests doctor the sidecar at the correct seam and the producer
source confirms the behavior, but **the first live apply through the SPA is that proof** — treat it
as an S6 acceptance item, not as done.

### 13.3 Deviations from §3's S2 and §4, KEPT — this section supersedes those lines

- **`model/zoom.ts` and `api/context.ts` are added to §4's layout.** The URL grammar must be pure
  and round-trip-testable while `routes.tsx` is React; and the client is **provided** rather than
  imported, which is what lets a test render the real shell against a fake client.
- **One catch-all route**, not a route per level — the grammar lives in `zoom.ts` and a second copy
  in `routes.tsx` would be a second grammar.
- **Both host-route spellings** are accepted: the flat `…/hosts/<key>` and `15` §3's
  segment-nested `…/segments/<cidr>/hosts/<key>`.
- **`?view=matrix` is accepted as an alias** for the `/matrix` path (`15` §7 writes the filter
  form), and normalized to the path.
- **Three dev dependencies beyond the named set:** `@testing-library/dom`, `jsdom`, `@types/node`.
- **The SPA history fallback landed in `axrange/api/`** — a Python change inside an SPA slice,
  because `15` §7's "the URL is the zoom" is unimplementable without it: `StaticFiles(html=True)`
  404s every deep route on reload while in-app navigation works perfectly.

### 13.4 Three rounds, and what each round's green suite had certified

- **Round 1** — the scaffold.
- **Round 2** — the reviewer's **three must-fix**, each of which the round-1 suite had passed:
  (1) a **412 that adopted the server's etag**, so a retry would have silently overwritten the other
  writer — last-writer-**refused** inverted into last-writer-**wins**, with the concurrency guard
  still green; (2) `VITE_AXRANGE_API`, meant as the dev server's proxy target, **lifted into the
  bundle** by Vite's `loadEnv`, so the browser bypassed the proxy it was configuring and went
  cross-origin to an API that installs no CORS; (3) a **failed** provenance read rendering as "no
  sidecar on this bundle" — an error reported as a fact about the artifact. Plus ten should-fix.
- **Round 3** — the tester's **four**: a query value parsed with `Number()`, so `occupancy_lt=+`
  became a **silent zero filter**; duplicate query keys silently ignored; `?view=` at the wrong
  level silently ignored; and the store not checking `imprint_id` on load. All closed with
  revert-proven tests. **No residual defect.**

Generalized as `docs/LESSONS.md` **LES-251..LES-259**.

### 13.5 Documented behaviors of the co-serve, not defects

- **A deep route is HTML regardless of `Accept`.** Content negotiation was considered and skipped:
  the rule that actually protects a caller is the `/api/` exclusion, and `Accept`-branching would
  make one URL answer differently to `curl` and to a browser.
- **A missing `.html` path falls back to the app** — `.html` is deliberately absent from
  `_SPA_ASSET_SUFFIXES`. This build ships no secondary HTML pages; if one is ever added, add `html`
  to that set with it.
- **Sourcemaps are served.** The intended trade for an internal, token-gated control plane; a public
  deployment drops `build.sourcemap` or strips the maps at the proxy.

## 14. S3 built as, 2026-09-09

**S3 is BUILT.** CHANGELOG 2026-09-09 "Console S3 BUILT" is the record; this section is the work
order's own account of where the build **resolved or deviated from** §3's S3, §5's layout contract
and §7's O1–O7 defaults. **S4–S7 are untouched and still open**, and S6's aximprint gate stays
lifted (§12.8). The one honest limit S3 carries forward — the conduit lanes are computed but the
live `CastSummary` DTO does not carry the roster to draw them — is §14.5, and is registered as
`docs/PRODUCT_GAPS.md` GAP-079.

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump. The change is **entirely frontend** — `o2/console/`
plus the §5.2 correction below — so the pytest suite is unaffected (2705 collected).

**Tests / the gate (no live-verify — nothing here deploys or touches a cluster):** `npm run
typecheck` exit 0 (strict, clean from zero); `npm run test` **187 passed / 6 skipped** (the six are
the opt-in `AXRANGE_LIVE_API` drift test); `npm run build` exit 0; the coordinate goldens
deterministic (regenerated twice, byte-identical). Full pytest suite **2693 passed / 12 skipped
(2705 collected)**.

### 14.1 What landed

The map pane draws, over a pure layout core the goldens pin.

- **`src/layout/` — pure TypeScript, no React / DOM / API** and no `react-flow`/`d3-force`/`dagre`:
  `geometry.ts`, `tiers.ts`, `sites.ts`, `level0.ts`, `level1.ts`, `matrix.ts`, `lanes.ts`, and a
  `pins.ts` seam left for S4. Input is the joined graph, output is coordinates — the module boundary
  `15` §9 and §4 require, and what makes the layout golden-testable off any one machine.
- **A React- and API-free `model/` layer:** `graph.ts` (the export + cast join into one
  `ConsoleGraph`), `roles.ts` (the fixed role order + infra-device set), `budgets.ts` (per-level
  budgets + the O6 collapse rule), `search.ts` (name / address / role / CIDR → a zoom). The `views/`
  — `Level0` / `Level1` / `Level2` / `Matrix` plus `Outline` / `Legend` / `Filters` / `Search` /
  `mapParts` / `selection` / `useGraph` — compose `model` + `layout` and import nothing else.
- **Renders against the real `twin-configs-cast/corpapcn-twin/` bundle** (786 nodes / 1437 edges /
  32 segments): 32 level-0 tiles, 22 east-west conduit lanes, six infrastructure devices, a level-1
  spine with role bands + rung-collapse + conduit peers, the level-2 host facts table, and the
  matrix. *(**Amended by level-0 R4 and R6, 2026-09-15:** the lanes are **arcs**, and the map draws
  **18 of the 32 tiles** under R6's per-group budget — 32 with every group expanded. The segments
  themselves are unchanged; only what the map draws is a projection.)* **Every element's evidence class is the export's own `*_evidence_class`, never inferred.**
- **Seven committed coordinate goldens** produced from `test/fixtures/trimmed.ts` and regenerated
  **only** by `npm run layout:golden` (never on a plain test run): `level0`, `level0-added-column`
  (the §5.2 column-count baseline, below), three `level1-*`, `matrix0`, and `matrix-seg-*`. Beside
  them, `test/layout.real.test.ts` runs the §3 count checks against the real bundle (32 tiles, 22
  lanes, six devices, the matrix-sum, the 49 unplaced + 596 non-local populations) — a hand fixture
  cannot prove the layout works on what ships. *(**As of level-0 R6, 2026-09-15** that first figure
  is asserted as the budget's own rule — **18 drawn + 14 collapsed = 32**, and 32 drawn when
  expanded — and the goldens are **nine**: the seven below plus `level0-collapsed` and
  `level0-collapsed-cidr`, which pin both tile orders.)*

### 14.2 The §7 v1 defaults O1–O7, as consumed

All seven ship exactly as §7 ruled; two (O3, O6) resolved a "if it costs nothing" / "found in the
data" clause the draft left open.

- **O1** — site bucketing by two octets for RFC1918 /24s, longest common prefix otherwise; a mixed
  site renders split until pinned, stated in the UI.
- **O2** — the six-class evidence palette ships from the mockup's hues as **swappable
  `theme/tokens.css` tokens**, each class carrying a fill style as well as a hue. This is the
  `dataviz` pass's **starting point, not its verdict** (`15` §12).
- **O3** — the matrix at level 0 **and** hosts × hosts inside a segment: the "ships with S3 if it
  costs nothing extra" clause resolved **yes**, it did.
- **O5** — the five-second post-apply highlight is a seam; the apply that drives it is S6.
- **O6** — the level-1 collapse rule for a band over 15 chips, by occupancy rung, is implemented
  (the 27-host `generic-endpoint` band in `10.80.72.0/24` is the regression case, LES-185, and is in
  the fixture).
- **O7** — the 49 unplaced local + 596 non-local entities are counted in the outline root and
  reachable (an *unplaced* tile at the segment tier; the matrix's `external` row/column), so nothing
  is silently invisible.

### 14.3 Three review should-fixes, and two nits

Each should-fix was a real defect the build's first green suite had certified.

- **(A) Order the evidence-class mix by the defined `precedenceOf`, not alphabetically.** For a
  multi-class mix the "dominant" swatch named the **wrong** class: the two orders disagree (`imputed`
  sorts before `observed` alphabetically but ranks *below* it), and the all-`observed` fixture never
  ran the sort, so the bug was invisible until a ≥2-class case existed (LES-262).
- **(B) The §5.2 locality guarantee is honest now** — determinism + locality hold for a
  **column-preserving** edit; a column-count change (a new row, a new/promoted `/16`) reflows the
  width-centered chrome and downstream columns and is **S4-pins territory**. Documented as a
  deliberate consequence of ordered columns, with the `level0-added-column` golden pinning the
  baseline (LES-261). **This is the §5.2 correction — see §14.4.**
- **(C) `cellDetail` no longer rebuilds the O(1437-edge) matrix on every Inspector render** — it
  reuses a memoized `MatrixLayout`, a perf fix on the hot selection path.
- **Two nits:** reuse the existing `compareRole` comparator rather than a second copy, and a single
  `LIVE_CONDUITS` source.

### 14.4 The §5.2 correction, KEPT — this supersedes the original §5.2 lines

The build **corrected §5.2 in place** (the one doc edit riding in this frontend slice). The original
text claimed adding one segment moves only that segment — true only for a **column-preserving**
edit. §5.2 now states both cases: a column-preserving edit leaves every other tile *and the chrome*
byte-identical; a **column-COUNT-changing** edit recenters the width-centered chrome and shifts
downstream columns, which is a deliberate consequence of ordered columns (`15` §4) and is what S4
pins exist to hold stable. A second locality golden (`level0-added-column`) pins the column-changing
baseline. **The chrome keeps its width-centered aesthetic by design** (`15` §4) — anchoring hard-left
would look broken over a wide segment row — so the churn is accepted as pins' job rather than
engineered away here.

Other kept calls, none a deviation from §3/§4 worth a supersede: **O3's hosts × hosts shipped** (it
cost nothing extra); the level-0 rung-collapse and the unplaced/non-local counting landed as O6/O7
ruled. Nothing in §3's S3 slicing or §4's `layout/` boundary changed.

### 14.5 The one honest limit — conduit lanes are computed but not live-drawn (GAP-079)

The east-west conduit lanes are **computed and golden-proven** in `layout/lanes.ts` (22 on the real
bundle), but they **do not render in the live co-served app**: the S1 `CastSummary` DTO
(`GET …/cast`) publishes only a conduit *count*, not the `{from, to}` pairs, so no API response the
SPA reaches carries the roster. S3 degrades honestly — an empty band with an in-UI "conduit list
absent from this API response" note (`src/views/useGraph.ts`) — and the band **lights up with no
view change** the moment a cast endpoint carries the conduits, because `buildGraph` reads them where
present.

This is a **follow-up, not a defect in S3**: the pure layout is correct and proven; only the wire it
reads is missing a field. The fix is a small **additive `CastSummaryDTO.conduits` backend slice**
(publish the `{from, to}` roster the compiler already knows) — outside this frontend-only task, and
registered as `docs/PRODUCT_GAPS.md` **GAP-079** so the "an operator can't yet see conduit lanes for
a deployed twin" gap is not lost. It belongs with S4 or whenever the backend is touched next; it is
not an S4 blocker.

> **Closed, then superseded in geometry.** GAP-079 was resolved by **level-0 R5** (2026-09-11) — the
> cast publishes `conduit_roster` and the band lit up with no view change, exactly as this section
> predicted. **The lane geometry itself is gone as of level-0 R4 (2026-09-15,
> `docs/WORK_ORDER_console_level0_rendering.md` §13):** `layout/lanes.ts` is deleted and the band is
> drawn by `layout/arcs.ts` as volume-weighted arcs anchored at each site group's bottom edge, with
> every asserted conduit drawn in the edit colour and dashed when the pair carried no observed
> traffic. So the "22 lanes" figure above survives as **22 asserted arcs**, and this section's
> prediction — that only the wire was missing, not the layout — held for the *roster*, while the
> *drawing* changed for a reason this section could not see: a lane from a tile's bottom edge drops
> through every tile row beneath it.

### 14.6 Post-ship fix — drill-down navigation uses the CANONICAL id (2026-09-09)

Found in **live use immediately after S3 shipped**. The canvas built every drill-down route (a
segment/host tile click, the outline links, a search hit) off `graph.imprintId`, which `buildGraph`
had derived from the topology export's **baked `imprint_id`** — not the canonical URL id the API is
keyed on (bundle directory name = `twin_config.id` = the id in the page URL). Where the two
disagreed, a drill navigated to a **non-resolving id (400/404)**.

It hit **two of three** imprints — the deployed disco twin `pit-395a61f04e-1780405200` (baked
`corpapcn-disco`), and, **pre-existing and untouched by S3**, `corpapcn-twin` (baked
`pit-63994c9543-…`). `corpapcn-golden` escaped only by being self-consistent (baked == canonical),
which is precisely why the coordinate goldens never caught it.

**Fixed** by making `CastInput.imprintId` a **REQUIRED** canonical id with **no fallback**, threading
the URL `imprintId` through `useGraph` into `buildGraph`, and setting `ConsoleGraph.imprintId` from
it; the baked `imprint_id`/`pit_id` remain **display/provenance** data and drive no route. **4
revert-proven regression tests** in `test/model.test.ts` use a fixture where **canonical ≠ baked** —
the gap the self-consistent goldens missed.

Frontend-only; **nothing version-locked moved** (op types **40**, catalog rules **114**, no schema);
the seven coordinate goldens are **byte-identical** (a navigation-id change, not layout). Vitest
**191 passed / 6 skipped** (was 187/6 at the §14 build). Reviewed clean (no must-fix/should-fix).
Lessons: **LES-263** (the canonical-vs-baked-id trap), **LES-264** (the multibyte-`§` locale-grep
trap that hid the original assignment during investigation), **LES-265** (a stale Vite/Vitest cache
masking a revert proof). Full record: CHANGELOG 2026-09-09 "Console S3 drill-down id fix".

**S4 (pins) followed this fix** (§15, 2026-09-10); §3's S3 slicing, §4's `layout/` boundary and
§5's contract were otherwise unchanged by it.

## 15. S4 built as, 2026-09-10

**S4 is BUILT.** CHANGELOG 2026-09-10 "Console S4 BUILT" is the record; this section is the work
order's own account of what the build landed against §3's S4 acceptance and `15` §4's pins design.
**S5–S7 are untouched and still open**, and S6's aximprint gate stays lifted (§12.8). S4 is where
the §5.2 column-count churn (LES-261) gets its escape hatch, as §14.4 promised.

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump. The change is **entirely frontend** — `o2/console/`
only — so the pytest suite is unaffected (**2705** collected) and the seven layout coordinate
goldens are **byte-identical** (a pin is applied *on top of* the pure layout; the no-pins render is
the same function it always was).

**Tests / the gate (no live-verify — nothing here deploys or touches a cluster):** `npm run
typecheck` exit 0 (strict, clean from zero); `npm run test` **219 passed / 6 skipped** (the six are
the opt-in `AXRANGE_LIVE_API` drift test); `npm run build` exit 0; the coordinate goldens
deterministic (byte-identical, and the lane re-anchoring revert-proven). Two new test files:
`test/pins.test.ts` (the **pure** half — locality / reset / placement / host-guard, `node` env per
LES-260) and `test/pins.ui.test.tsx` (the **jsdom** half — the drag gesture and the affordance).

### 15.1 What landed

Pins are live on level 0, over the pure layout the goldens still pin.

- **A pin is an ABSOLUTE OVERRIDE with NO reflow** (`15` §4). Drag a **segment tile** or a **whole
  site column** and it lands at its pinned top-left; **every unpinned element keeps its computed
  place** — it does not shift to fill the gap or make room. So pinning a sibling never moves its
  neighbour, and *reset pins* is exactly `layout0(graph, NO_PINS)` — **byte-identical** to the pure
  no-pins render and to the goldens. The pin math is pure (`layout/pins.ts`: `pinDelta`,
  `translateBox`; applied by `applyPins` in `layout/level0.ts`); the DOM/drag lives in
  `views/usePinDrag.ts` and `views/Level0.tsx`, so `layout/` still knows nothing about React or the
  API.
- **Persistence to `<imprint>.layout.json` (`axr-layout/1`) via `PUT …/layout`**, in a doc **kept
  separate** from the assertions/edit store (pins are layout, never evidence). The save is
  **optimistic** — the pinned/reset position shows at once, then the PUT's authoritative etag is
  adopted without a re-read (a re-read would flash the pinned tile back to its computed slot for a
  request). A **stale etag** (a concurrent writer) rolls the optimistic move back and triggers a
  fresh **reload** before the next drag, never a silent retry (LES-251). The doc is keyed on the
  **CANONICAL** imprint id — the id the PUT path is keyed on and the same one drill-down carries,
  never a producer-baked one (LES-263) — so pins survive reload and apply.
- **Reset** returns the canvas to the pure function (a `.pin-reset` control shown only when a writer
  is present). **Hosts are never pinnable:** no drag affordance on the infrastructure devices, the
  Internet pill or the *unplaced* tile, and the server `_validate_pin_key` refuses a host key by
  name.

### 15.2 The lane re-anchoring — the load-bearing internal fix

S3 drew the tiles and the east-west conduit lanes, but moving a tile moved only the tile — the
lanes anchored to it stayed put. `applyPins` now **re-derives** a moved tile's lanes by re-running
the **same pure `layoutLanes` primitive** over the **moved** tile positions with a **fixed band
axis** (`laneBandTop`), rather than translating cached polylines. Two properties fall out for free:
a lane whose endpoint a pin moved tracks the tile while the band itself stays put, and **a pair
neither pin touched recomputes byte-identical** (unmoved bottom-centres, same channel order), so the
re-anchoring leaves locality intact and the no-pins goldens unchanged. An infra device's tie-line
follows for free, because the view draws it off the (now-moved) `l0.tiles`. This is the
reuse-the-primitive pattern, generalized as LES-266. *(**Superseded in mechanism by level-0 R3,
2026-09-14** — `docs/WORK_ORDER_console_level0_rendering.md` §12.1: there is no tie-line any more. A
device is placed over the column holding its segment and reaches it with a published orthogonal
**stub**, and `applyPins` re-runs the same pure `layout/infra.ts` primitive over the **moved**
columns — the identical pattern, now applied to the infrastructure band rather than left to the view.
The limit that comes with it: a column pinned over **another** column drags its stubs through that
column's tiles, measured and pinned in that order's §8.)*
*(**And superseded in mechanism again by level-0 R4, 2026-09-15** — that order's §13.1: `lanes.ts`
and `layoutLanes` are **deleted**, so what `applyPins` re-runs over the moved boxes is the pure
`layout/arcs.ts` primitive. Everything this section says about the **pattern** still holds — re-run
the primitive over moved inputs, never translate cached polylines; a pair neither pin touched
recomputes byte-identical — and the arcs are now the **fourth** consumer of it, after the lanes, R2's
trunk and R3's infra band. The band axis that stays fixed is `bandTop` rather than `laneBandTop`, and
the arcs carry the same no-reflow limit as the bus and the stubs: a pinned column drags its arcs
across another column's tiles, swept and pinned in that order's §8.)*
*(**And extended by level-0 R6, 2026-09-15** — that order's §14.1/§14.3: a group's per-group tile
budget means an anchor can now stand for **more than one segment**, so `applyPins` re-runs the same
primitive over moved boxes *and* over the group's `+N more` anchor, with the pairs that land there
**merged** into one curve. Two consequences for pins specifically: a pin on a **collapsed** tile is
**kept but not drawn** — re-applied at its saved coordinates when `?expand=` opens the group, and
disclosed on screen as `· N held for collapsed tiles` rather than left to be inferred — and a **site**
pin carries the `+N more` box with the rest of its column.)*

**viewBox growth stays in the VIEW, never the pure layout.** The canvas viewBox grows to *contain* a
tile/site a **committed** pin pushed past the pure canvas — computed in `Level0.tsx` (`frameFor`),
framed to **`savedPins` only, never the live drag**. So the pure `layout0` output (and the goldens)
is untouched, and empty-pins is an exact no-op; a tile dragged past the current edge clips until
release and the frame grows on the next committed save (an accepted trade for a drag that never
rescales itself — see §15.3). Generalized as LES-268.

### 15.3 Two review-caught drag bugs, fixed with revert-proven tests

No must-fix; two should-fix drag-interaction bugs, each fixed with a test proven to fail against the
pre-fix source.

- **(1) The post-drag click suppressor is now TIME-bounded (250 ms), not "expected-click"-bounded.**
  A drag uses `setPointerCapture`, so in several browsers the compatibility `click` after
  `pointerup` is dispatched to the capturing `<svg>` (no tile `onClick`) — the drag's own trailing
  click never reaches `consumeDragClick`. A flag cleared only when "the awaited click arrives" would
  therefore stay **armed** and swallow a **later** genuine drill click on the same element.
  `isSuppressedClick` is bounded by time instead (LES-269).
- **(2) The client→layout scale is captured ONCE at gesture start, and the viewBox is framed to
  committed pins only.** The level-0 SVG was `max-width:100%`-scaled **when S4 was built** (the
  level-0 rendering order's **R1** replaced that rule on 2026-09-12 with an explicit
  `width`/`height` = units × scale and a 0.8 fit floor, its §10.1), so a viewBox that grows during
  the drag would rescale a live CTM and the tile would **accelerate** away from the cursor. The fix
  is unchanged by R1 and remains load-bearing for the same reason at a different scale: reading the
  scale once off the live CTM is correct whether the browser's user-unit→px factor is 1, 0.8 or a
  fitted fraction.
  `layoutScaleOf` captures the scale once at `begin` (via `getScreenCTM().inverse()` → `1/a`, `1/d`,
  with a boundingRect ÷ viewBox fallback and identity last resort) and every delta reuses it, so the
  tile tracks the cursor 1:1 even when the diagram is wider than its container (LES-267). Both
  `draggedTopLeft` and `layoutScaleOf` are exported pure functions unit-tested directly.

### 15.4 Honest limits, kept

- **Server pin-key CIDR asymmetry — `docs/PRODUCT_GAPS.md` GAP-080 (pre-existing, newly reachable by
  S4).** `axrange/api/console_files.py`'s `_validate_pin_key` accepts a **loose** `segment:` CIDR
  while tiles are keyed on the **strict** network form (a mismatched key silently matches no tile),
  and `_SITE_KEY_RE` rejects `/`, so a `site:raw:<cidr>` key — mintable by `layout/sites.ts` /
  `model/keys.ts` for a **non-RFC1918** twin — would be 400-refused and the optimistic pin silently
  revert. Not reachable with today's all-`/24` RFC1918 content, but **S4 is the first code to emit
  site keys**, which is why it is registered now.
- **Save is not serialized:** a rapid second save during an in-flight PUT can 412 → roll back →
  reload and drop the second edit. It converges via reload and matches the no-queue design; a
  serializing queue is a later refinement, not a defect.
- **Dead pins accumulate** in `layout.json` for since-removed segments/sites — harmless, `applyPins`
  ignores an unmatched key; a compaction pass could prune them if the file ever grows unwieldy.
- **Drag is pointer-only:** the site header has no keyboard activation (a minor a11y gap).
- **A pin is an ABSOLUTE coordinate on an unidentified plane** — registered as a limit by level-0 R1
  (2026-09-12) when the tier-rail gutter shifted the content origin by 128 units, and **closed by R2
  on 2026-09-14**: `axr-layout/1` now carries an optional `plane` stamp (S1.6's amendment,
  `docs/WORK_ORDER_console_level0_rendering.md` §11.4). Pins authored against an older plane are
  accepted and **flagged**, and the stamp only advances when the whole set has been re-placed — one
  drag can never re-certify the pins it did not move.

### 15.5 §3's S4 acceptance, as met

All four acceptance points hold: a pin survives a **reload** and (by construction, keyed on the
canonical id) an **apply**; an unpinned neighbour's coordinates are **unchanged** by a pin
(`pins.test.ts` locality, byte-identical against the golden); **`reset`** restores coordinates
byte-identical to the no-pins render (`pins.test.ts` reset == the golden); and a **host pin is
refused** — by the server `_validate_pin_key` and unreachable in the UI (no affordance).

## 16. S5 built as, 2026-09-10

**S5 is BUILT.** CHANGELOG 2026-09-10 "Console S5 BUILT" is the record; this section is the work
order's own account of what the build landed against §3's S5 acceptance and `15` §8's editing model.
**S6–S7 are untouched and still open** — S6's aximprint gate stays lifted (§12.8), and **S6 (apply +
refresh) is next.** Apply is deliberately **not** built here (§16.5).

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump. The change is **entirely frontend** — `o2/console/`
only — so the pytest suite is unaffected (**2705** collected) and the seven layout coordinate
goldens are **byte-identical** (editing is not layout; the S2 reducer and `src/layout/` are
untouched).

**Tests / the gate (no live-verify — nothing here deploys or touches a cluster):** `npm run
typecheck` exit 0 (strict, clean from zero); `npm run test` **275 passed / 6 skipped** (the six are
the opt-in `AXRANGE_LIVE_API` drift test); `npm run build` exit 0. The tester proved record fidelity
(each gesture → the exact `14` §3 record), the 412 reload-not-retry rule, and the byte-identical
goldens.

### 16.1 What landed

The canvas edits, over the S2 store and a pure record layer.

- **`src/edit/records.ts` — the pure heart, no React / DOM / API.** `buildRecord` (gesture → exactly
  one `axr-assertions/1` record), `deployConsequence` (record kind → the change it is for a standing
  twin), `crossRecordConflict`, `strictCidr`, `metadataReady`. Its client pre-flight **mirrors the
  server's `axrange/api/console_files.py` refusals rule-for-rule** — a non-empty `note` on all three
  kinds, the closed `override.field` set, `suppress` host-keys-only, stable keys, the two
  cross-record conflicts, the canonical conduit-key half-ordering — the LES-271 direction rule (a
  client mirror may be looser than its server but never stricter) checked by a shared-input parity
  fixture.
- **Seven edit gestures** (`15` §8's table): add-segment, add-host (role + listen), override-role,
  suppress-host, override-image, override-sizing (`resources`), add-conduit — plus the inspector's
  remove-assertion, which deletes the record. Each produces **exactly one** record.
- **Three visible states** through S2's **untouched** reducer: *pending* (browser only, hatched),
  *saved* (`PUT …/assertions`, outlined), *applied* (in the cast, `asserted`).
- **A change-list panel** (`src/views/ChangeList.tsx`) tagging each entry with its deploy consequence
  **from the record kind alone** — `add`/`suppress` → `te deploy --update`, an `override` of an
  existing node → a fresh deploy (`docs/WORK_ORDER_twin_incremental_update.md` §3.1). This is what
  the bundle supports: it publishes no authored-key → cast-node-spec map.
- **Save keyed on the canonical imprint id**, the file authoritative on merge, the stale-etag 412
  surfacing a reload prompt and **never** adopting the server etag (LES-251). New views
  `EditPanel.tsx` / `ChangeList.tsx`, with `App.tsx` / `Inspector.tsx` / `theme/shell.css` wired in.

### 16.2 The route reconciliation — no route gesture (a supersession)

`14` §3's normative rule (line 132, **refined 2026-09-04**) **refuses a `route` record by name**: a
twin config has no route object, and conduits are how reachability is authored. §3's earlier "first
cut" edit-set prose (lines 190, 267) still read **"conduits and routes"** — the pre-refinement list,
contradicted by line 132 in the same section. The build follows the refined rule: **there is no route
gesture**, and the "seven gestures" are the no-route set of §16.1. This pass corrects note 14's two
stale prose mentions to "conduits"; `CLAUDE.md`'s golden-twin blockquote "conduits/routes" is left as
a dated historical record (the "record, don't rewrite history" discipline). **This supersedes** §3's
original S5 slice framing to the extent it implied a route gesture, and `docs/design/14-operator-assertions.md`
§3's first-cut prose.

### 16.3 Two review should-fixes and N1, revert-proven

No must-fix. Two should-fixes on the remove path, plus N1, each with a test proven to fail against
the pre-fix source.

- **(S1) Removing a saved row no longer commits unsaved pending edits.** The removal document is
  built from **saved + applied** entries only, holding the "pending = browser only" boundary
  (`15` §8). A "collect every entry" serializer (`documentFrom`) had turned a **scoped** write (a
  removal) into a **whole-store** commit — the write boundary belongs at the call site, not the
  serializer (LES-272).
- **(S2) The remove-that-re-saves path shares Save's `source`/`as_of` metadata gate** (one shared
  `metadataReady` predicate) rather than PUTting metadata the server refuses.
- **(N1) `strictCidr` rejects leading-zero IPv4 octets inline** — matching the server's
  `ip_network(strict=True)` — instead of a delayed server 400.

### 16.4 N1 / N2 / N3 dispositions

- **N1 — fixed** (§16.3): a leading-zero octet is refused client-side.
- **N2 — documented non-issue.** `records.ts` refuses `{cpu: ""}`, the **one** place the client is
  *stricter* than the server (the LES-271 divergence direction the rule forbids), but the form
  pre-filters empty fields, so it is UI-unreachable. Left as-is with the divergence named.
- **N3 — a known limitation, registered as `docs/PRODUCT_GAPS.md` GAP-081 (Low).** A *pending*
  `suppress` re-staged over a key the standing cast **already** suppresses can flip to *applied* on
  the next provenance load, hiding the unsaved indicator (`hasUnsavedEdits` counts only pending).
  Narrow and self-correcting; the clean fix needs editing the deliberately-untouched S2 reducer, and
  the effect-level alternative has a stale-read / re-render-loop problem.

### 16.5 Apply is S6, not S5 — the seam

Apply is deliberately not built here. The **backend** `POST …/apply` exists and is proven against the
real compiler (S1.5, §12.8), and the change-list already tags each edit's consequence and the store
already tracks pending → saved → applied — so S6 is the SPA apply + refresh slice over a **built**
endpoint (poll/return the new fingerprint, reload the bundle, highlight the diff), carrying §13.2's
residual (the first live apply through the SPA is the only proof the real compiler writes the authored
key into the new sidecar) as an acceptance item. Buildable now against the real endpoint or a stub.

### 16.6 §3's S5 acceptance, as met

All acceptance points hold: **each gesture produces the exact `14` §3 record** (verified by reading
the written document); a **stale-etag save surfaces a reload prompt and does not write** (412, the
pending edit intact); a **no-op save** is a byte-identical file (owned by S1's
`tests/test_api_console.py` on the YAML side; the frontend round-trip asserts the client document);
**removing an assertion deletes the record**; **the change list's tag matches the record-kind rule**
for every kind; and **reducer/record unit tests cover pending → saved → applied without a browser**.

## 17. S6 built as, 2026-09-10

**S6 is BUILT.** CHANGELOG 2026-09-10 "Console S6 BUILT" is the record; this section is the work
order's own account of what the build landed against §3's S6 acceptance and `15` §8's apply model.
**S7 is untouched and still open — the last slice in this work order.**

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version` or state-store bump. The change is **entirely frontend** — `o2/console/`
only — so the pytest suite is unaffected (**2705** collected) and the seven layout coordinate
goldens are **byte-identical** (apply drives a bundle reload and a highlight, not a layout change).

**Tests / the gate (no live-verify — nothing in the frontend build deploys or touches a cluster):**
`npm run typecheck` exit 0 (strict, clean from zero); `npm run test` **288 passed / 6 skipped** (the
six are the opt-in `AXRANGE_LIVE_API` drift test); `npm run build` exit 0. The tester proved all three
apply outcomes (real change / no-op / can't-compare) and the goldens byte-identical.

### 17.1 What landed

Apply, over the S1 endpoint the aximprint gate was already lifted from.

- **`src/model/changes.ts` — a new pure module, no React / DOM / API.** Reduces `GET …/diff`'s
  response to the set of map elements it touched, joined on **observed identity** (observed CIDRs,
  observed node ids, and `ImprintDiffDTO.suppressed_keys`, §13.2) rather than the diff's twin-side
  ids, which name no element the map ever draws. Exports (`changeSetSize`, `isNodeChanged`,
  `isSegmentChanged`) are consumed at five call sites across the view layer rather than re-inlined.
- **The apply continuation** (wired through `App.tsx`, `views/EditPanel.tsx`, `views/MapPane.tsx`,
  `views/useGraph.ts`) — save (reusing S5's `PUT …/assertions` gate) → `POST …/apply` → one of three
  renders: a compile failure showing the compiler's own stderr; a byte-identical recompile shown as a
  determinism note with no highlight; or a real change that reloads the cast/provenance/assertions,
  computes the change set, and glows the touched elements for five seconds.
- **A `?changed=1` filter** (`model/zoom.ts`, `views/Filters.tsx`) keeps the touched set reachable
  once the five-second glow fades, per `15` §12's O5 default; the glow itself is rendered in
  `views/Level0.tsx` / `Level1.tsx` / `Level2.tsx` / `mapParts.tsx`, each keyed off the same change
  set so the three levels and the outline agree.
- **`src/edit/store.ts` and `src/layout/` are untouched** — apply reads and refreshes state; it does
  not touch the pending/saved/applied reducer or the pure layout modules, which is why the goldens
  hold byte-identical.
- **New tests:** `test/changes.test.ts` (the pure reduction, no DOM) and `test/apply.ui.test.tsx`
  (the three-outcome flow, jsdom); `test/model.test.ts`, `test/views.test.tsx` and `test/zoom.test.ts`
  gained cases for the new filter and change-aware rendering.

### 17.2 The must-fix, revert-proven — an honest "can't compare" outcome

**Before the fix**, an apply reporting `changed: true` whose diff could not actually be computed —
either `history_written: false` in the apply response's own `warnings`, or a `getDiff` call that
itself failed on the follow-up read — rendered through the **same silent no-highlight path** as a
genuine no-op apply. An operator watching the canvas after such an apply saw nothing move and had no
way to tell "the compiler made no change" from "the compiler changed something and the tool could not
tell you what." This is the same don't-know-vs-no collapse **LES-240** named on the wire (`null` ≠
empty, with the reason in `warnings`) and **LES-253** named on the render side (loading / failed /
absent are three worlds) — surfacing here a third time, at the point an already-fetched apply result
becomes a highlight-or-not decision. **Fixed** by reading the qualifying field explicitly and
rendering a third, honest outcome that carries the server's own `warnings` rather than falling through
to the no-change branch. Generalized as `docs/LESSONS.md` **LES-273**.

### 17.3 Two should-fixes, revert-proven

- **A stale apply's continuation no longer force-navigates the operator.** The apply continuation runs
  after an `await` with no `AbortSignal` to cancel it, so an operator who navigates elsewhere before it
  resolves cannot have the in-flight request cancelled — only the *reaction* to it can be guarded. A
  `liveRef`/`pathnameRef` **still-current** check now gates **only** the trailing `go(...)` navigation
  call; the state refresh (cast/provenance/assertions/diff) still lands wherever the operator has since
  moved, rather than the whole continuation short-circuiting or yanking them back to the imprint they
  applied from. Generalized as `docs/LESSONS.md` **LES-274**.
- **`changes.ts`'s join predicate is reused, not re-inlined.** `changeSetSize`/`isNodeChanged`/
  `isSegmentChanged` are called at all five sites that need "is this element in the change set" instead
  of each writing its own `.has()`/`.size` check — clearing the dead-export class the tester flagged on
  `twinChangeCount` (removed outright during the interrupted build, §17.4, rather than left as an
  orphaned export no call site used).

### 17.4 Process note — the build spanned a model-tier boundary

This slice's implementation was interrupted mid-build by an **Opus session rate-limit**; the tester,
reviewer, fix and doc stages that finished it ran on **Sonnet** as the documented fallback (this
project's working agreement routes implementation to Opus subagents, with Sonnet as the fallback when
Opus is unavailable). Recorded because it is itself worth knowing: a `/dev` pipeline run can cross a
model-tier boundary mid-slice without losing the thread, provided every remaining stage still gets a
full pass — which this one did (288 passed / 6 skipped; one must-fix and two should-fixes found and
fixed, each revert-proven).

### 17.5 The residual — **CLOSED 2026-09-11**: the real compiler writes the authored key into the new sidecar

§13.2's residual — proof that the **real** compiler writes an authored key into the **new** sidecar on
an apply — is **proven**. A real-compiler apply, driven through the **same HTTP calls the SPA client
makes** (`PUT …/assertions` then `POST …/apply`, with the write token, against a co-served
`axrange serve`), PASSED all five checks on the tracked bundle `twin-configs-cast/corpapcn-golden`,
suppressing `address:10.80.34.10`:

1. the **new** sidecar carries the key in `population.suppressed` with **`matched: true`** (not merely
   present — matched, which is the half that distinguishes a real suppression from an ineffective
   record, §13.2);
2. `GET …/provenance` agrees, so the SPA's plain-load path (`suppressedKeysFromPopulation`) sees it;
3. the diff's **`suppressed_keys`** lists it, so the post-apply highlight path sees it too;
4. the twin config no longer places the host — **node_specs 88 → 87**;
5. a **second identical apply is byte-identical** (`fingerprint 1de327650032c7ce`), which is design
   note 14 §3's determinism rule holding on the real producer.

Apply took **2.6–2.8 s** end to end. Evidence (request/response bodies, bundle snapshots at each
stage, and the checksums):
`/tmp/claude-1000/-home-rocky-axRange/99fbb50e-2bf2-481b-a118-41544f6745ac/scratchpad/apply-proof/evidence/summary.json`.
The proof ran against a **scratch copy of the IR store**, because `aximprint cast` opens it
**read-write** (a duckdb connect plus `CREATE TABLE IF NOT EXISTS`) — worth knowing before anyone runs
this against a store they care about.

**Six findings came out of it**, none of which a green suite could have produced, and each registered
rather than folded away (`docs/PRODUCT_GAPS.md`). Four are axRange's, scheduled as **iteration 11** of
the 2026-09-11 Console run: **GAP-087** — a **compiler upgrade rides along silently** on the first
apply (aximprint HEAD had renamed 11 node groups, so the recompile differed by **22 hosts added / 23
removed** while the Console honestly reported "1 element changed", because it joins on *observed*
identity; on a **deployed** twin those renames would replace VMs on `te deploy --update`);
**GAP-088** — the apply response **cannot** reveal that: `code_fingerprint_before/after` is copied
from the PIT row and did not move, while the real build hash (`cast_invocation.code_fingerprint` in
the topology export) did, and two docstrings call the copied field "the compiler's build hash";
**GAP-089** — `app.py`'s `since` parameter description names a value that **404s** (`fingerprint_before`
is the one that works); **GAP-090** — the server's no-op warning is an ambiguous "either … or …" (the
SPA's own branch is fine). Two are aximprint's: **GAP-091** — `install-cast` never deletes stale
files, so superseded scripts and personas accumulate in a bundle (and would be committed with a
tracked one); **GAP-092** — the sidecar records the assertions file's **absolute** path, so two
checkouts applying identical assertions produce different bytes and fingerprints.

**S7 (the Grafana embed) is next**, and is deliberately the last slice in this work order — now with
**D5 ruled** (§8.1): retire the plugin's imprints screen there, keep "Deploy as twin" as a slim plugin
action.

### 17.6 §3's S6 acceptance, as met

All four acceptance points hold: **an apply on a saved file returns a new fingerprint** and the SPA
reloads; **the changed elements highlight** (five seconds, then the `?changed=1` filter keeps them
reachable); **a compile failure surfaces the compiler's own stderr**, never a generic error; **an
apply with unsaved pending edits saves them first** (apply *is* save + recompile, the same `PUT
…/assertions` gate S5 built); and **a no-op apply renders as a no-op** — the determinism check itself
is the server's (`14` §3, unchanged by this slice), and the SPA's job, met here, is to show that
outcome with no highlight rather than inventing a change. The one point this section adds beyond §3's
original wording: a **fifth**, previously unstated outcome — changed-but-undiffable — is now also
rendered honestly (§17.2), rather than defaulting into the no-op branch as the first pass did.

## 18. S5.1.a built as, 2026-09-15

**S5.1.a is BUILT** — iteration 7 of the ten-iteration Console run. CHANGELOG 2026-09-15 "Console
S5.1.a BUILT — selection drives editing (iteration 7 of ten)" is the record; this section is the work
order's own account against §3 S5.1.a and its share of S5.1's acceptance. **S5.1.b–e and S7 are
untouched and still open.** The slice resumed from a rate-limit interruption: its first code-writer was
cut off with two of its own acceptance tests red (selecting an R6-collapsed segment, and the pending-add
half of the next-free-`/24` rule), and a resumed code-writer finished both.

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version`, state-store or `PLANE_EPOCH` bump, and no record kind added — every gesture
still builds its one record through `buildRecord`. **One additive, non-version-locked API field**
(`segment_roster`, §18.1) is the slice's only Python.

### 18.1 What landed

- **The selected element carries its facts and its actions** (`src/views/ElementActions.tsx`, new). A
  level-0 tile is selected through its select control — a stroked SVG pencil with a 24 × 24 hit target in
  the tile's corner (clicking the tile itself still drills) — a level-1 spine is selected in place, and a
  chip drills to its host, whose route *is* the selection. **Segment:** *Add host here*, *Clone this
  segment…* (`aria-disabled`, with the reason and the path that does exist), *Add conduit to…*,
  *Suppress hosts…*. **Host:** *Change role*, *Override image / resources*, *Suppress*. The action list
  is an accordion, one form at a time. `src/views/EditPanel.tsx` keeps only what belongs to the file —
  the change list, the save metadata, Save and Apply; its free-standing gesture forms are removed.
- **Selection** (`src/views/selection.ts`). One `Focus` slot holds either a *detail* view (a matrix
  cell, an O7 drawer) or an **explicit** selection — `{kind: "segment", cidr}` or `{kind: "site-add",
  siteKey}`. `resolveSelection(zoom, focus)` returns the explicit selection when there is one, otherwise
  **the element the URL names** (a host route's host; a segment route's or a segment-scoped matrix's
  segment), so drilling in from the map, the outline or search selects as it opens — which is how a
  segment R6's budget collapsed reaches the same actions with no tile to click.
- **The site `+`** on every map site header and outline site row (`addSegmentLabel` — one accessible
  name for both), plus the outline root's site-less `+`. The allocator is pure (`src/edit/allocate.ts`,
  new): `nextFreeSlash24` over every CIDR in play; `castStandingOf` (carried / renamed / absent /
  unpublished); `segmentClaims` + `claimOn` + `isRefusal` + `claimMessage` (one phrasing for the live
  note and the Stage refusal); `segmentNameClaims` + `nameClaimOn` + `segmentNamePrefill`;
  `segmentLandingNote` (asked of `groupIntoSites` itself, so the sentence and the grouping cannot
  disagree); `defaultZoneType`; `addedSegmentsOf`.
- **Removal with dependents** (`src/edit/dependents.ts`, new, pure): `dependentsOf`,
  `removalConsequences`, `orphanReasons`. `ChangeList.tsx` renders the inline confirmation ("remove all
  N+1" / "keep them") and the orphan flag; `App.tsx`'s `onRemoveEntries` removes the group **together** —
  discarded outright when every member is pending, otherwise one re-save of the file whose discard waits
  for the server's ack, so a 412 leaves every member in place.
- **Lifetime, isolation and keyboard.** `App.tsx` resets the selection on `location.pathname` only and
  handles Escape inside the imprint workspace, where the edit store is. `api/hooks.ts` `useResource`
  returns a value only for the subject (the deps) it was read for. `views/useGraph.ts` holds the last
  graph across a cast re-read only while the topology on hand is the very object it was built from, and
  publishes `topologyAbsent` for a 404 export. `Inspector.tsx` gains an always-present `role="status"`
  live region; `MapPane.tsx` is focusable (`tabIndex={-1}`) as the fallback focus target, and says when a
  route names a segment only this file adds.
- **The API: `CastSummaryDTO.segment_roster: [{name, cidr, zone_type}]`** (`axrange/api/dtos.py`
  `CastSegmentDTO`, `axrange/api/imprints.py` `CastSegment`, `axrange/api/service.py`), read from the
  bundle's own twin config (`bundle.twin_segments` drops `zone_type`, so the config itself is read). In
  config order; `len(segment_roster) == segments`; `cidr` is the **deployed** network — relocated on a
  `--relocate` cast, which is why the Console joins by name — and `zone_type` is `null` where the config
  declares none, never the effective zone. Mirrored as `CastSegment` in `o2/console/src/api/types.ts`
  (the field-set guard extended, LES-258) and passed through as published by `castInputFrom`.
  `model/graph.ts` makes the roster, when present, the **one** source of the inspector's deployed-CIDR
  and zone facts (`deployedCidrByName`/`zoneTypeByName` survive only as the golden fixture's direct
  input) and exposes `castSegments` + `castStatusOf`.
- **Shared pieces.** `src/views/paneParts.tsx` (`Row`, `Fact`, `Field` — one rendering of a fact and a
  form field across the panes); `model/keys.ts` `parseCidr` reads a digits-only prefix length and
  `ipv4NetworkRange` is the one network-mask rule `strictCidr` and the allocator both call;
  `layout/sites.ts` publishes `Site.prefix` and is generic over its members, so the allocator asks the
  same grouping function where a not-yet-existing segment would land.
- **Tests.** New `test/{selection,allocate,dependents}.test.ts`, `test/{selection,fixround,fixround2}.ui.
  test.tsx` and the vendored `test/fixtures/real-segment-rosters.json`; extended `edit.ui`, `apply.ui`,
  `castinput`, `keys` and the opt-in `live-api` test, which now **requires** `segment_roster` and so fails
  against a server that predates it. Python: five new roster test functions (nine collected items) plus
  the `CastSegmentDTO` field-set mirror case in `tests/test_api_console.py` — pytest **3112 → 3122**
  collected.
- **Goldens.** The four level-0 goldens gain **13 `site.prefix` leaves — 0 removed, 0 changed**; the
  level-1 and matrix goldens are byte-identical.

### 18.2 The calls

**Authority: implementation (Claude Code), each open to veto** — except 7, which is the user's.

1. **An observed CIDR the cast does not declare under its name is re-addable, and the note says what
   the compiler really does.** An `add: segment` of an observed CIDR sets that segment's name and zone
   type and *confirms* it; the compiler keeps a segment only when a host is placed on it. The first
   wording ("restores that segment") rested on `docs/design/14-operator-assertions.md` §3's worked
   example and was **wrong**. The reviewer settled it by running aximprint's real `compile_twin`, and
   aximprint's own reference-ingest S2 had already recorded that the example "confirms rather than
   creates" (its `docs/WORK_ORDER_reference_ingest.md` §8, finding 1). Design note 14 §3 now carries a
   dated correction, and the compiler's silent no-op on a host-less add is `docs/PRODUCT_GAPS.md`
   GAP-093. The standing is called **"renamed or pruned"**, never "pruned": the cast joins by name, so a
   segment it carries under another name reads identically unless a rename is on record.
2. **A pending or saved added segment is selectable** — in the outline, by direct URL, in every conduit
   picker — so S5's capability of putting hosts and conduits on an added segment survives the removal of
   the free-standing forms, and S5.1.a ships alone. Drawing it on the map stays **S5.1.d**.
3. **Only a name held by a DIFFERENT network collides.** An add of an observed segment under the name
   the export — or the cast — already gives it names nothing new; a clash with any other network is
   refused, naming STR-095.
4. **The zone default is the site's dominant declared zone → the cast's → `simple`** (a tie goes to
   `simple`; a value outside the closed set is not counted), and the form says which. Never `vlan`:
   `simple` is the twin-config schema's own default (`axrange/twinconfig/schema.py`), one `vlan` segment
   makes the whole scenario a VLAN zone, and a VLAN zone rejects SNAT egress. The S5 form had defaulted
   to `vlan`.
5. **Escape never discards unsaved work.** At the network level with pending edits it stays on the
   imprint and says why; once they are saved it leaves as before.
6. **`segment_roster` is added to the API** (R5's additive precedent) rather than the Console guessing
   from the observed set whether the cast declares a segment.
7. **Commit policy (Authority: user, 2026-09-15):** unattended **local** commits for iterations 7–11; a
   push and any change to the standing `:8080` serve still check back.

### 18.3 §3's S5.1.a, clause by clause — and the test that proves each

| clause | proving test (file › describe › it) |
|---|---|
| Selecting a level-0 tile opens **that segment's** facts and four actions, in place | `edit.ui.test.tsx` › *selecting a tile shows THAT SEGMENT's facts and actions, in place* › *keeps level 0 on screen and offers the segment's facts and its four actions* |
| *Clone this segment…* is visibly unavailable, with its reason | same describe › *states WHY clone is unavailable instead of hiding it -- on screen AND as the control's description* |
| A **spine** is selectable; a **chip** drills, and the host its route names is the selection | `edit.ui.test.tsx` › *at level 1 the SPINE is the selectable subject, and a CHIP drills to its host* (two its) |
| A host carries *Change role* / *Override image / resources* / *Suppress* | `edit.ui.test.tsx` › *a host carries its own three actions* (two its) |
| A segment's actions emit exactly the S5 records | `edit.ui.test.tsx` › *a segment's actions emit exactly the S5 records* (three its); `selection.ui.test.tsx` › *Add conduit to… stages the DEFAULT peer it shows when the operator picks none* |
| The free-standing forms are removed; record building is unchanged | `edit.ui.test.tsx` › *the free-standing forms are GONE* (two its); `selection.ui.test.tsx` › *the seven gestures, reached ONLY through the new paths, write the S5 record layer's document* — byte-identical to `buildRecord → reducer → documentFrom` and to the literal `14` §3 document |
| A **collapsed** segment reaches the same actions | `edit.ui.test.tsx` › *opening an element selects it -- which is how a COLLAPSED segment is reached*; `selection.test.ts` › `resolveSelection` (five its) |
| A `+` on each **site header** and **outline site row**, under one name | `edit.ui.test.tsx` › *the MAP's site header carries the same `+` as the outline's site row*; `fixround.ui.test.tsx` › B5 + C › *the map's site + carries exactly the outline's accessible name, site by site* |
| Prefilled with the **next free `/24`** in the site's two-octet prefix | `edit.ui.test.tsx` › *prefills the next free /24 in that site's two-octet prefix*; `selection.test.ts` › `nextFreeSlash24` (six its); `allocate.test.ts` › the boundaries, and *prefills, under every real site, a free /24 in that site's prefix that stages unchanged* (both real bundles) |
| …and **refuses a taken one** | `fixround.ui.test.tsx` › A2 (three its); `edit.ui.test.tsx` › *refuses a CIDR a PENDING add already claimed*, *a SAVED add in the file is as taken as a pending one*, *with NO roster published, REFUSES an observed CIDR*; `selection.ui.test.tsx` › *an add stays claimed after its file is SAVED and after an APPLY re-reads it*; `allocate.test.ts` › *WITH the REAL roster, refuses exactly the declared segments and allows exactly the undeclared ones -- swept* |
| The prefill never proposes an observed CIDR | `fixround.ui.test.tsx` › A2 › *never PREFILLS a segment the cast does not declare*; `selection.test.ts` › *the allocator never suggests an observed CIDR* |
| An overlap is said, not refused | `edit.ui.test.tsx` › *an OVERLAP is said, not refused*; `selection.test.ts` › *reports an OVERLAP as an overlap* |
| A **site-less** add from the outline root lands in `misc` | `edit.ui.test.tsx` › *a site-LESS add from the outline root lands in misc, and says so*; `selection.ui.test.tsx` › *the MAP's misc + prefills nothing* |
| The CIDR field follows the suggestion until typed; typed input is never overwritten | `edit.ui.test.tsx` › *an UNTOUCHED field follows the suggestion both ways* and *what the operator TYPED is never overwritten by a changed suggestion* |
| One canonical value for the note and for Stage; a digits-only prefix length | `fixround.ui.test.tsx` › B1; `keys.test.ts` › `parseCidr` (three its), `ipv4NetworkRange` (two), `siteKeyForCidr` (one) |
| The name prefill never duplicates a name; a clash is refused (STR-095) | `fixround.ui.test.tsx` › A4; `selection.test.ts` › *names -- no two segments share one* (five its) |
| The zone type starts at site → cast → `simple` | `fixround2.ui.test.tsx` › ZT; `selection.test.ts` › `defaultZoneType` (two its) |
| An added segment is selectable and actionable | `fixround.ui.test.tsx` › A3 (five its); `selection.test.ts` › `addedSegmentsOf` |
| Removing an only-home segment asks and lists, with per-kind outcomes; another home goes alone; orphans are flagged | `fixround2.ui.test.tsx` › SF2 (four its); `dependents.test.ts` (ten its) |
| The inspector's cast facts come from the roster; an absent field reads *not published*; a rename on record reads *carried* | `fixround.ui.test.tsx` › A1 (four its); `castinput.test.tsx` › *castInputFrom -- the SEGMENT roster reaches the graph* (four its) and *useGraph -- the live hook carries the FETCHED segment roster*; `fixround2.ui.test.tsx` › SF1 (two its) |
| A selection lives as long as the pathname | `selection.ui.test.tsx` › *the selection's lifetime* (two its); `fixround.ui.test.tsx` › B2 (three its); `edit.ui.test.tsx` › *a VIEW flag does not drop the selection* (two its) |
| A resource answers only for its own subject | `fixround2.ui.test.tsx` › MF1 (four its) |
| Escape order, focus return, the unsaved guard | `fixround.ui.test.tsx` › B3 (two its) and B5 + C › *clear returns focus to the opener*; `fixround2.ui.test.tsx` › T5 (two its) and the nits (four its) |
| Every affordance is keyboard-reachable; focus moves once per gesture | `edit.ui.test.tsx` › *every new affordance is reachable by keyboard* (six its); `selection.ui.test.tsx` › *keyboard* (four its) |
| A missing export is *absent*, not failed; a host route keeps its actions through an apply | `fixround.ui.test.tsx` › B4 (two its); `fixround2.ui.test.tsx` › SF3; `edit.ui.test.tsx` › *with no graph there is nothing to act on, and the inspector says why* (two its) |
| 24 × 24 path-icon controls; `aria-current` on one element per view | `fixround.ui.test.tsx` › B5 + C (six its) |
| `segment_roster` as written, count-consistent, a `null` zone kept `null`, the fixture drift-pinned | `tests/test_api_console.py` › `test_a_real_cast_publishes_its_segment_roster_as_written` (three bundles), `test_the_segment_roster_agrees_with_the_segment_count_on_every_listed_bundle` (three catalogs), `test_a_bare_config_publishes_a_null_zone_type_as_null_not_the_effective_zone`, `test_the_packaged_fixture_publishes_its_segment_roster`, `test_the_console_segment_roster_fixture_matches_the_real_twin_configs`, the `CastSegmentDTO` mirror case; `o2/console/test/live-api.test.ts` (opt-in) |
| `Site.prefix` is published by the grouping, never re-derived from a label | `selection.test.ts` › *Site.prefix -- the grouping publishes the prefix*; the four level-0 goldens |

The share of S5.1's **acceptance** paragraph that belongs to S5.1.a — *"the site `+` prefills the next
free `/24` and refuses a taken one"* — is met by the rows above. Everything else in that paragraph is
S5.1.b–d's.

### 18.4 The rounds, and what each round's green suite had certified

A resumed code-writer; a **tester and a reviewer in parallel**; fix round 1; a re-verify; fix round 2; a
light re-probe; a tiny fix round 3. **Every fix carries a revert proof.** The tester walked the two real
bundles (`corpapcn-twin`, pit) in headless Chromium against its **own** scratch server, with PID
ownership proven before any screenshot was believed (LES-315); the reviewer ran aximprint's real
`compile_twin` rather than argue from the design note.

- **Fix round 1 — honest facts, honest claims, a selection that lasts.** (A1) the inspector's cast facts
  read "not declared" on **every real segment**, masked by fixture-only maps — the production path never
  supplied them (LES-311) → the roster. (A2) Stage refused every observed CIDR as already "in the twin",
  unable to tell a declared segment from one the cast does not carry. (A3) removing the free-standing
  forms had removed the only path to hosts and conduits on an added segment. (A4) the name prefill could
  duplicate a segment name — a `/23` or `/25` at an observed network address inherited its `/24`'s.
  (B1) the live note and the Stage check read different values, and `Number("")` made a half-typed
  `10.80.31.0/` a `/0` that "overlapped" everything. (B2) `?changed=1`, the view flags and the dimming
  filters reset the selection and its typed input — on **every apply that changed anything**. (B3)
  Escape left a level before clearing a selection. (B4) a missing export rendered as a failed read. (B6)
  the landing note promised a map placement the map cannot draw, and counted staged adds as siblings.
  (B8) a prefix re-derived from a site label, and a network-mask rule written twice. (B5/C) text-glyph
  controls without 24 × 24 targets, the map's and outline's `+` under different names, focus falling to
  `body`, and one site's typed value reaching another site's form.
- **Fix round 2 — isolation, and saying what really happens.** **(MF1, the must-fix)** switching imprint
  A → B — directly, or through the catalog — drew **A's tiles under B's heading and let a gesture stage
  A's segments into B's file**: `useResource` cleared the previous subject's data in an effect, which
  leaves one render holding it, and round 1's graph hold — keyed on the imprint **id label** — turned
  that one render into the whole of B's load. (SF1) a segment renamed by a saved add read as "pruned".
  (SF2) removing an added segment silently orphaned the hosts and conduits staged on it. (SF3) an apply's
  cast re-read turned a no-export bundle into a failed read and unmounted the host's typed input. (T5)
  Escape at the network level discarded pending edits. (ZT) the zone type defaulted to `vlan`. Four nits:
  Escape spending a press on the URL's own subject, `body` recorded as an opener, a re-staged add
  replacing silently, a vanished conduit peer still staged. And a **circular test**: the real-bundle
  sweep expected back the very pruned list it had been built from (LES-187's class) — it is now fed from
  the drift-tested roster fixture.
- **Fix round 3 — the dependents' key space.** The re-probe's case: SF2 counted an add of an **observed**
  segment as the home of the hosts on it (removing a zone assertion would have taken an unrelated saved
  host with it), and counted the cast's relocated roster as a home when flagging orphans (which hid real
  ones). Both now follow the compiler's own resolution set — the observed segments plus this file's adds
  (aximprint `known_cidrs`).

### 18.5 Honest limits and residuals — recorded, not fixed

- **Navigation other than Escape discards pending edits.** The breadcrumb, the outline's imprint links,
  the browser's Back and a reload all leave the imprint workspace, which is keyed by imprint (LES-259), and
  take the pending edits with it — no prompt. **`edit/store.ts` has no draft mechanism**, although S5.1's
  acceptance text describes one ("pending edits persist in the store's draft"). It is **S5.1.d's** (§3,
  now noted there) and registered as `docs/PRODUCT_GAPS.md` **GAP-094**. S5.1.a guards Escape only.
- **A rename made through an `override` record** (`field: name` on a segment — reachable by hand-editing
  the file only; the Console has no rename gesture) is not mapped by `castStandingOf`, so such a segment
  honestly reads "renamed or pruned" rather than "carried".
- **MF1's input-keyed hold is a second wall no test can isolate.** With `useResource` withholding another
  subject's data outright, no graph can be built from the wrong subject, so no test can make the hold's
  keying matter; the hold is kept as defence in depth, and its necessity is argued, not proven (LES-313).
- **Cosmetic:** the remove-confirmation's two buttons are crowded; Escape's focus return scrolls the map
  pane sideways when the opener sits off-screen at 1:1.
- **`corpapcn-twin`'s six "pruned" observed segments** (`10.5.0.0/24`, `10.20.205.0/24`, `10.64.7.0/24`,
  `10.80.31.0/24`, `10.80.63.0/24`, `172.16.0.0/24`) come from an **older** compiler. The current one
  prunes none on a real PIT (it emits the whole observed population), so any apply recompiles them back
  regardless of an add — GAP-087's class. The "renamed or pruned" allowance bites today only on such an
  older cast.
- **The compiler half of call 1 is aximprint's:** an `add: segment` on an observed-but-uncarried CIDR
  with no host is a **silent no-op** — matched, reported neither `ineffective` nor `unmatched` — and the
  compiler's conduit remedy text ("add them with `kind: segment`") points at that same gesture
  (`docs/PRODUCT_GAPS.md` GAP-093, owner aximprint).
- **Code-comment wording, not behaviour:** the `segment_roster` docstrings in `axrange/api/dtos.py`,
  `axrange/api/imprints.py` and `o2/console/src/api/types.ts`, the `_REAL_PRUNED` block in
  `tests/test_api_console.py` and `fixround.ui.test.tsx`'s A2 header still say an observed name absent
  from the roster is one the cast "PRUNED". The UI's wording — "renamed or pruned" — is the accurate one
  (call 1); align the comments when those files are next touched.

### 18.6 Carried forward

- **S5.1.b (iteration 8).** ✅ **Done 2026-09-16 (§19).** The **exact-versus-overlap** question was
  ruled and recorded in §3 S5.1.b itself (§19.2 call 1: an exact match is refused, an overlap warns),
  and a **clone dropped into a collapsed group** is held, disclosed and drawn on `?expand=`
  (§19.1, §19.3).
- **S5.1.c (iteration 8).** ✅ **Done 2026-09-16 (§19).** The call taken: a **generated, checked-in
  table** (`scripts/gen_role_listen.py` → `src/model/roleListen.generated.ts`) with a Python drift
  test, rather than an API route — and the derivation turned out to need the persona's **`drive`**,
  not just `emits[].peer_role`, plus a disclosure for what the catalog cannot say (GAP-095).
- **S5.1.d (iteration 9).** Pending segments drawn as hatched tiles — *partly done by S5.1.b, for a
  **placed** one (§19.1); the level-1 chips remain* — the per-gesture change list and whole-gesture
  undo; the **draft** — per-imprint persistence plus a guard on every way out of an imprint that holds
  pending edits (§3 S5.1.d, GAP-094); and, added by S5.1.b, **dead-pin cleanup** (§19.5).

### 18.7 Gates

No live-verify — nothing here deploys or touches a cluster. Vitest **822 passed / 0 failed / 8 skipped
(830)** (R6's committed baseline was 647 / 8; the eight skips are the opt-in `AXRANGE_LIVE_API` drift
test); `npm run typecheck` clean from zero; `npm run build` to a **scratch** `--outDir`, exit 0 (never
`o2/console/dist` while `:8080` serves it); pytest **3110 passed / 12 skipped, 3122 collected**; op
types 40, catalog rules 114. `doc-stats.py --check` clean.

## 19. S5.1.b–c built as, 2026-09-16

**S5.1.b (clone and drag) and S5.1.c (generated hosts) are BUILT** — iteration 8 of the ten-iteration
Console run. CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT" is the record; this section is the work
order's own account against §3 S5.1.b/c and their share of S5.1's acceptance. **S5.1.d–e and S7 are
untouched and still open.**

**Version-locked: nothing moved.** Op types **40**, catalog rules **114**, no
`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST` change, no
`scenario_schema_version`, state-store or `PLANE_EPOCH` bump, **and no new record kind** — a batch is N
records of the ruled kinds, each still from `buildRecord`. The Python side is a generator, its drift
test and two acceptance tests (pytest **3122 → 3131** collected). **The four level-0 goldens are
byte-identical**: an added segment takes no grid slot and reflows nothing.

### 19.1 What landed

- **`src/edit/batch.ts` (new, pure) — the batch path.** `buildBatch(gestures, existing)` checks the
  whole batch before a record is staged: `buildRecord` per record (the writer's closed sets, LES-271),
  then the store's identity rule, then `crossRecordConflict` against what is staged **and** the batch's
  own earlier records. The first refusal refuses all of them, naming the offending record by position
  and gesture (`record 4 of 17 (add host address:…): …`).
- **`stageBatch` + `batchRefusal` (`src/edit/store.ts`).** One reducer action stages the batch or
  none of it, recording the reason in `problems`; `batchRefusal(entries, records)` is the same rule as
  a **pure, exported** function, so the caller's side effects — the pin, the session placement, the
  selection — hang off **the same decision the reducer makes**. Entries carry a **`GestureTag`**
  (`{id, label, kind: "clone" | "generate", source?}`), browser knowledge only: it survives a save and
  a re-read only while the file's record is still, **by value**, the record the gesture staged
  (LES-296's value rule), and a page reload drops it.
- **`src/edit/clone.ts` (new, pure) — the clone's whole rule set.** `cloneDefaults` (the next free
  `/24` in the source's site, the collision-free conventional name, **the source's** declared zone
  type), `chooseHosts` (*all* / *by role* / *first N by address*, with what cannot come listed),
  `assertedHostValues`, `sourceConduits`, `conduitKeyFor`, `cloneCidrRefusal`, `planClone`. A cloned
  record carries the source host's role and its listen/dial census — **never an evidence class or a
  confidence** — and its note is always `cloned from <source cidr>: <note>`.
- **Clone by pointer, keyboard and touch** (`src/views/usePinDrag.ts`, `src/views/ElementActions.tsx`,
  `src/App.tsx`). **Alt-drag** a tile: the source stays, a ghost follows the pointer
  (`cloneGhost`/`onCloneDrop`), and nothing is pinned by the drag itself. *Clone this segment…* opens
  a **placement** step with nudges and *Place here*; a **tap** on the map places it where it lands.
  `layoutPointOf` maps a client point into layout units through the inverted screen CTM, degrading to
  the bounding rect and then 1:1 (jsdom implements no SVG geometry). On Stage the placement is
  committed as an **S4 pin on the clone's final CIDR**, through the same writer a drag uses.
- **`src/layout/added.ts` (new, pure) — where an added segment is drawn.** `placeAddedTiles` returns a
  box for each **placed** added segment (a pin on file, else this session's placement), **held** entries
  for one whose group the per-group budget collapsed (R6's rule, disclosed and drawn on `?expand=`),
  and `covers[]` — every drawn tile or `+N more` box the placement overlaps, named as the map names it.
  `proposedClonePlacement` is the source column's next grid slot, clamped to **the column's own bottom
  edge** (§19.4). `segmentPinKey` is the S4 key.
- **S5.1.c — generated hosts.** The site `+` form's hosts block: count, role, and a listen set from
  `defaultListenFor` (`src/model/roles.ts`), with addresses from `allocateHostAddresses` —
  **`HOST_NUMBERING_START = 10`**, skipping every address the export carries and every `address:` key
  the file holds **in any block**. `portsFromCensus` + `canonicalPorts` (`src/edit/records.ts`) put
  ports in the compiler's canonical order.
- **`scripts/gen_role_listen.py` → `src/model/roleListen.generated.ts` (new).** The per-role default
  listen set, generated from `axrange/personas/catalog/*.yaml` and **drift-tested from Python**
  (`tests/test_console_role_listen.py`), with an independent cross-check that every entry is an emit
  the catalog names on the **schema's own** transport rule (`Emit.effective_transport()`, never
  restated). Drive-aware: a `real-service` persona **serves** its emits, every other drive **dials**.
  `loadgen-*` excluded; reserved peer tokens skipped on the dialing side only.
  `ROLES_WITH_DIALED_EMITS` + `check_dialed_caveat()` carry the assumption to the UI and guard it.
- **The map and the panes.** `src/views/Level0.tsx` draws placed added tiles (hatched *pending*,
  outlined *saved*), the Alt-drag ghost, the placement overlay and the proposal; `src/theme/shell.css`
  gains `.added-tile` and the hatch pattern — **deliberately not `.tile`**, because any stylesheet
  `fill` overrides the presentation attribute the pattern is set with (§19.4). `ChangeList` shows each
  entry's gesture label; the pin bar names orphan pins; a failed `PUT …/layout` raises a visible alert
  and the tile says "placed in this browser only".
- **A drag no longer sweeps text.** `selectstart` is cancelled **only while a gesture this hook began
  is active** — measured 364 characters before, 0 after — and the `pointerdown` is deliberately not
  `preventDefault`ed, so focus-on-press still reaches the pinnable keyboard controls.
- **Test infrastructure worth naming:** `test/fixtures/realBundles.ts` builds both real bundles' graphs
  **through the live seam** (`buildGraph(topology, castInputFrom(cast))`) with two vendored,
  Python-drift-tested rosters (`real-segment-rosters.json`, and the new
  `real-conduit-rosters.json`), and `test/helpers/jsonschema.ts` is a small draft-2020-12 evaluator that
  validates a written document against the **vendored schema file itself** and **fails loudly on a
  keyword it does not implement**.

### 19.2 The calls

**Authority: implementation (Claude Code), each open to veto.** Call 1 is the ruling §3 S5.1.b asked
for, carried into this iteration by §18.6 and now recorded in the slice text itself.

1. **Exact versus overlap for a clone's CIDR** — an **exact** match with an observed segment (whatever
   its standing against the cast), a cast-declared segment, or a pending/saved/applied add is
   **refused**; an **overlap only warns** (NET-010, moot on a relocated cast). The asymmetry with
   S5.1.a's site `+` is deliberate: an add of an observed segment **confirms** it, a clone of it would
   not.
2. **The drive-aware listen rule**, the `loadgen-*` exclusion, and the **reserved-token asymmetry**:
   a reserved peer token (`internet`, `egress`, `broadcast`, `multicast`) is skipped where it is the
   *listener* being derived, and kept where the persona *serves* — so `dmz-web` keeps 443 for internet
   clients.
3. **Disclosure, not a heuristic, for direction.** Five roles' tables hold ports the role **dials** —
   `web-tier` (3306), `db-replica` (3306, 5432), `protocol-gateway` (502, 4840), `egress-proxy`
   (53/udp), `monitoring-collector` (9100, 161) — because a `real-service` persona's emits are filed
   under its own id with no served-versus-dialed split. The generated header names the five, the form
   repeats the caveat **where the default is offered** (naming the typed role first when it is one of
   them), and `check_dialed_caveat()` fails loudly if one stops being a `real-service` persona of the
   catalog. **The real fix belongs in the persona catalog — an emit has no direction —**
   `docs/PRODUCT_GAPS.md` **GAP-095**.
4. **A batch never replaces an existing record**, where single-record staging deliberately does
   (S5's re-stage rule, said aloud by the add forms). One record of sixteen silently overwriting a
   saved host is not a gesture anyone meant.
5. **Hostname- and MAC-keyed hosts are not clonable** — a hostname names one machine — and a selection
   naming only such hosts is **refused**, rather than staging a bare segment and dropping the
   operator's stated intent.
6. **Ports use the compiler's canonical order**, and **`image`/`resources` travel only from an
   asserted or overridden value**: what nothing asserted is the compiler's to choose for the new host
   too.
7. **The placement clamp tests the column's own bottom edge**, not the east-west rail (§19.4).

### 19.3 §3's S5.1.b–c, clause by clause — and the test that proves each

*(The tests number S5.1's acceptance clauses 1–7 in their own describes; two files use "acceptance 5"
for different clauses — undo, and the keyboard path — so this table is keyed on the clause's text.)*

| clause | proving test (file › describe › it) |
|---|---|
| A default clone of the real bundle's `10.80.20.0/24` emits exactly `1 + N + 0` records, each host key re-based on the same host part, every record carrying the propagated note | `clone.test.ts` › *acceptance 1 — a DEFAULT clone of `<bundle>`'s 10.80.20.0/24* › *the raw export holds the counts the acceptance names* + *emits exactly 1 + N + 0 records…* (both real bundles; N and the not-cloned count read from the raw export, never from the planner) |
| Nothing observed travels as evidence | `clone.test.ts` › same describe › *copies role, listen and dial from the source host — and NOTHING observed as evidence* |
| The written YAML validates against the vendored schema and re-reads byte-identical after a canonical re-emit | `batch.roundtrip.test.ts` › *acceptance 2 — a batch's file validates, and re-reads byte-identical…* (a clone with an asserted image and sizing; a generated segment), with the evaluator's own refusals pinned by *the vendored-schema evaluator refuses what the schema refuses* |
| A mirrored-conduits clone adds exactly the source's conduit count, re-keyed and sorted | `clone.test.ts` › *acceptance 3 — a mirrored-conduits clone…* (four its: the fixture's two, a peer with no CIDR listed as **not mirrored**, the self-peer case, and a conduit this file adds) |
| A collision is refused with its specific reason and **no partial batch** is staged | `clone.test.ts` › *acceptance 4 — each collision is refused with its OWN reason…* (seven its: an export address, a pending add's address, an exact CIDR in three standings, a hostname key, a duplicate pending clone, an overlap that only warns, a non-network CIDR) — each read **off the store**, not off the planner's answer |
| The batch is all-or-nothing | `clone.test.ts` › *the batch path is all or nothing* (six its, including a refusal at the very **end** of the batch, the reducer's own refusal, and the one-pure-question check) |
| Undo of the gesture leaves the assertions document byte-identical to before it | `batch.roundtrip.test.ts` › *acceptance 5 — undoing a SAVED clone leaves the file byte-identical to before it* (compared on the bytes against a document captured before the clone existed) |
| A keyboard-only operator can complete a clone (inspector button + placement) | `clone.ui.test.tsx` › *acceptance 5 — a keyboard-only operator completes a clone…* › *Enter on the tile's ✎, Clone this segment…, a nudge, Place here, a note, Stage — no pointer anywhere*; and *Escape cancels a clone being placed* |
| Alt-drag drops a clone where the pointer is released; the source does not move | `clone.ui.test.tsx` › *an Alt-drag of a tile drops a clone where the pointer is released* |
| A tap places it (touch) | `clone.ui.test.tsx` › *by touch: a tap on the map places the proposed clone where it lands* |
| The pending clone tile renders **hatched** at its drop position and is selectable | `clone.ui.test.tsx` › *acceptance 6 — the pending clone tile* › *renders HATCHED at its drop position…*; `placement.test.ts` › *the pending hatch is not overridden by the stylesheet* (which also proves its own parser is not vacuous) |
| A drop into a **collapsed** group is held, disclosed, and drawn on `?expand=` | `clone.ui.test.tsx` › *a drop into a COLLAPSED group is held…*; `placement.test.ts` › *HOLDS a placed tile whose group the per-group budget collapsed* and *heldAddedClause* (three its) |
| An added tile reflows nothing, and names what it covers | `placement.test.ts` › *changes NO coordinate of the pure layout* and *NAMES the drawn tile a placement covers* (+ the `+N more` case); the four level-0 goldens, byte-identical |
| The clone's pin goes through S4's writer and keeps the plane stamp's rule | `clone.ui.test.tsx` › *the clone's pin goes through S4's writer and keeps the plane stamp's rule* (an empty layout is stamped current; a stale one stays stale) |
| The proposal never leaves its own column | `placement.test.ts` › *proposedClonePlacement — the next slot, never outside its own column* (five its, including **the measured `site:misc` case** and *no arc crosses the proposal, from any group of either real bundle, at either budget*) |
| The site `+` generates `add: segment` + N × `add: host` from `.10`, with the role's listen set | `clone.ui.test.tsx` › *acceptance 7 — the site + generates hosts on a fresh segment* (four its: N=3 at .10/.11/.12 with the listen prefilled; skipping taken addresses; an unserved role prefilling **empty** and saying so; no count ⇒ one record) |
| Addresses skip every key the file holds, in any block | `clone.test.ts` › *S5.1.c — host addresses from .10…* › *counts as taken every address the export carries … and every `address:` key the FILE holds* + *a generated host is never numbered onto a key the file SUPPRESSES* |
| The listen prefill says what the table cannot know | `clone.ui.test.tsx` › *the prefill says what the table cannot know (M1)*; `clone.test.ts` › *FLAGS a role whose default holds a port it dials* |
| The generated table matches the catalog, drive-aware, `loadgen-*` excluded | `tests/test_console_role_listen.py` — `test_the_console_role_listen_table_matches_the_persona_catalog`, `test_every_entry_is_an_emit_the_catalog_names_on_the_schemas_own_transport`, `test_a_real_service_persona_listens_on_what_it_serves_and_its_clients_do_not`, `test_the_load_generator_personas_are_left_out_of_the_table`, `test_the_caveat_names_every_role_whose_default_holds_a_port_it_dials`, `test_the_caveats_role_list_is_guarded_against_a_catalog_rename`, `test_the_skip_rules_on_a_catalog_built_to_hit_each` |
| A drag selects no text, and only during its own gesture | `pins.ui.test.tsx` › *a drag suppresses TEXT SELECTION for its own duration only* (four its: a pin drag, an Alt-drag, outside a gesture, and after unmount) |
| A failed layout write is said, and the pin bar counts honestly | `clone.ui.test.tsx` › *a failed layout write is SAID, and the pin bar counts honestly (S2, S3)* (three its) |
| A conduit roster fixture cannot drift from the real twin configs | `tests/test_api_console.py` › `test_the_console_conduit_roster_fixture_matches_the_real_twin_configs`; the batch's file half by `test_a_clone_shaped_batch_re_saves_byte_identical` |

The remaining acceptance clauses are **S5.1.d's**, and are named as such in §3: a pending clone's
**chips at level 1**, its survival across a **reload** (the draft, GAP-094), and the **per-gesture**
change list with whole-gesture undo. Undo *of the records* is proven above through the change list's
existing remove-with-dependents path; a single "undo this gesture" control is S5.1.d.

### 19.4 The rounds — and two measurement disputes that changed the code

Code-writer → **tester and reviewer in parallel** → one combined fix round (**1 must-fix, 7 should-fix,
5 nits**) → a bounded re-probe → a final two-line fix. **Every fix carries a revert proof.** The tester
walked both real bundles in headless Chromium with PID ownership proven (LES-315).

- **The must-fix: a stylesheet `fill` silently beat the hatch.** The pending tile sets its `fill` to an
  SVG pattern as a **presentation attribute**, and any CSS `fill` rule — `.tile`'s — overrides a
  presentation attribute. **jsdom applies no stylesheet at all**, so every unit test saw the correct
  attribute and the browser drew a flat tile. Fixed by giving the added tile its own class that sets no
  `fill`, and pinned by a test that **reads the stylesheet** and asserts no rule matching the tile sets
  one (with its own not-vacuous check). Generalized as **LES-327**.
- **Dispute 1 — the clone proposal and the arcs. Both parties were right.** The reviewer measured a
  proposal crossing **six asserted arcs**; the code-writer could not reproduce it and measured **38
  units of clearance**. Five of `corpapcn-twin`'s six columns carry a `+N more` box, which reserves a
  grid row, so their box bottom is **680** and the proposal (572..668) sits inside them. `site:misc`
  has exactly six segments and therefore **no** `+N more`: its box ends at **568**, the same proposal
  falls 100 units below its own column, and it crosses the corridor the arcs anchored on that group
  drop through. The first clamp tested the **east-west rail** — which calls that case clear (668 < 706)
  — and the fix tests **the column's own bottom edge**, moving the escape to the right of the column.
  Generalized as **LES-331**.
- **Dispute 2 — the withdrawn reading.** The tester's first band measurement used **arc bounding
  boxes** rather than the rail, and it withdrew the reading rather than filing it.
- **What hid the case from both sides at first:** a layout probe that does not build its subject through
  the live seam (`realGraph` + `level0OptionsFor`) is measuring a map no operator can open — LES-311's
  class, one iteration on, now extended with the probe rule.
- **The should-fixes and nits, by class:** a side effect (pin, placement, selection) committed beside a
  reducer dispatch that refused the batch (**LES-330**); an allocator that skipped only `add` keys and
  could number a host onto a key the file **suppresses** (**LES-143**, extended); a grouping over
  observed + added that counted a CIDR named twice and pulled a singleton out of `misc` (**LES-328**);
  a kept-mounted form whose identity had to become explicit when a second clone source reused it
  (**LES-322**, extended); a dead pin left by a removed clone (**LES-289**, extended); `"got null"` for
  a "first N" the operator typed as text (now quoted back); a self-peer conduit dropped silently (now
  listed as *not mirrored*); and the three disclosures in §19.1.

### 19.5 Honest limits and residuals — recorded, not fixed

- **The placement escape can overlap the next column** on a narrow plane where the source is not the
  last group. Accepted: it is a **proposal** the placement step exists to move, and a placed tile
  **names what it covers** (`AddedTilePlacement.covers`), so the overlap is disclosed rather than
  silent.
- **A proposal for a column the operator pinned below the band follows its column.** The rule reads the
  box wherever the box is, so a site header dragged into the band takes the proposal with it — sitting
  beside a column the operator put there is not the defect the clamp guards.
- **A removed clone's pin lingers in `layout.json`.** It is inert (nothing draws a tile for a segment
  no record adds) and now **disclosed in the pin bar** as naming no segment in play; cleaning it up is
  **S5.1.d** (§3, now noted there).
  *(✅ **Closed 2026‑09‑16 by §20**: a whole‑gesture undo drops the segment's pin with its records, and
  discarding a restored draft drops its pins too. The remaining case is the other end of the same
  thread — a pin written at STAGE time for a segment that is still pending, `docs/PRODUCT_GAPS.md`
  GAP‑096.)*
- **Still S5.1.d:** hatched **chips at level 1**, the **per-gesture** change list, a whole-gesture undo
  control, and **draft persistence** — pending edits still do not survive a reload or any navigation
  out of the imprint except Escape (`docs/PRODUCT_GAPS.md` GAP-094).
- **The listen prefill can name a port the role dials** (call 3) — disclosed at the point of use, and
  `docs/PRODUCT_GAPS.md` **GAP-095** carries the real fix. A prefill is a starting point to prune, never
  a claim that the host serves the port.
- **Test-label nit:** the test files number S5.1's acceptance clauses, and "acceptance 5" is used for
  two different clauses (undo in `batch.roundtrip.test.ts`, the keyboard path in `clone.ui.test.tsx`).
  §19.3 is keyed on the clause text for that reason.

### 19.6 Carried to S5.1.d (iteration 9)

Hatched **chips at level 1** for a pending clone's hosts; the **per-gesture** change-list entry
("clone of 10.80.20.0/24 → 10.80.31.0/24 · 1 segment · 14 hosts · 2 conduits · pending") expanding to
its records, with **undo of the whole batch** as one control (the records already carry their
`GestureTag`); **draft persistence** (GAP-094) so a pending clone survives a reload of the same URL;
and **dead-pin cleanup** — dropping a `segment:<cidr>` pin when the last record adding that segment is
removed, which this slice deliberately left inert and disclosed rather than deleting a pin behind a
`PUT` an operator did not ask for.

### 19.7 Gates

No live-verify — nothing here deploys or touches a cluster. Vitest **924 passed / 0 failed / 8 skipped
(932)** (iteration 7 ended at 822 / 8; the eight skips are the opt-in `AXRANGE_LIVE_API` drift test);
`npm run typecheck` clean from zero; `npm run build` to a **scratch** `--outDir`, exit 0 (never
`o2/console/dist` while `:8080` serves it); pytest **3119 passed / 12 skipped, 3131 collected**; op
types 40, catalog rules 114; the four level-0 goldens byte-identical. `doc-stats.py --check` clean.

## 20. S5.1.d–e built as, 2026-09-16

**S5.1.d (pending is visible, and the change list counts it) and S5.1.e (the level-1 add-host prefill)
are BUILT** — iteration 9 of the ten-iteration Console run — **and with them the S5.1 amendment is
complete, a–e**. CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT" is the record; this section is the work
order's own account against §3 S5.1.d/e and the rest of S5.1's acceptance. **S7, the Grafana embed, is
now the only open slice in this order.**

**Version-locked: nothing moved.** Frontend-only — no Python was touched, so op types **40**, catalog
rules **114** and pytest **3131 collected / 3119 passed / 12 skipped** are all unchanged, as are the
four level-0 coordinate goldens (**byte-identical**: an added tile takes no grid slot). No new record
kind, no `PLANE_EPOCH` bump.

### 20.1 What landed

- **Pending is drawn (S5.1.d).** `src/layout/added.ts` grows three cases beside the placed one: an
  **unplaced** added segment is **appended to its own site column** through `slotAfterColumn` — the
  same slot rule a clone's proposal uses, so the two can never disagree about where "after this column"
  is (LES-025) — a column with no room **parks** its tile in a lane right of **every box the plane
  draws**, and a group the per-group budget collapsed **holds** it exactly as it holds a placed one.
  `UnplaceableAddedTile` reports a segment whose group the map draws no column for, rather than dropping
  it silently.
- **Level 1 draws it too.** `src/views/Level1.tsx` draws a pending segment as a **hatched spine** with
  its staged hosts as **hatched chips** in a band reading `added here · N · pending (browser only)`, and
  a host added to an **observed** segment as a hatched chip beside that segment's observed bands.
- **The change list counts gestures.** `src/edit/gestures.ts` (new, pure): `groupByGesture` (one row per
  `GestureTag`, at the position of its **first** record), `gestureCounts` / `gestureHeadline` (§3's own
  shape, naming only what the gesture staged), and the two rules that keep it readable — **a solitary
  record is not a group** (`solo`), and **a group's state is the weakest of its records'**.
  `newGestureId()` mints a **session-unique** id (`crypto.randomUUID()`, random-prefixed counter
  fallback), because the id is now persisted.
- **Whole-gesture undo** (`src/views/ChangeList.tsx` + `src/App.tsx`): removes exactly that batch,
  **drops the pin** of a segment it takes out of play (and this session's placement with it),
  **re-saves once** when saved records were involved, asks first when the undo would **strand** a record
  outside the gesture, and needs the save metadata **only where a saved record is actually involved**.
  A single record inside a gesture stays individually removable.
- **S5.1.e** (`src/views/ElementActions.tsx`): *Add host here* on a spine prefills the segment and the
  **next free address**, following the suggestion as records are staged and removed and never
  overwriting a typed value (LES-322's rule, reused).
- **The draft (`src/edit/draft.ts`, new) — GAP-094 closed.** One `localStorage` key per imprint
  (`axrange.console.draft.<id>`, LES-259's rule applied to storage), holding only **pending** entries
  with their gesture tags, written on every pending change and **cleared on save**. Every access is
  wrapped: on a hardened profile the property access itself throws, and a full quota throws on write —
  both are **said once**, name what is at risk, and never take the app down. `reconcileDraft` is the
  restore: a record whose entry id the file now holds is **dropped** (identically = redundant,
  **differently = said as a divergence**), the writer's **shape check** (`invalidStoredRecord`) and
  `14` §3's **cross-record refusals** both run — a draft is the only way a record reaches the store
  without passing a gesture — and a moved etag or `code_fingerprint` is **reported beside** the restored
  set. `restoreNotice` is the one sentence both the visible notice and the announcement come from
  (LES-307). `listDrafts` enumerates the keys, which is what lets the **catalog** speak on a cold start.
- **The guards (`src/App.tsx`).** One guard on **`go`**, firing only when a navigation leaves the
  imprint; **Escape** keeps its own refusal (no one-click exit, LES-324); **`beforeunload`** only while
  something is unsaved. The prompt **dismisses itself** when its reason goes and never navigates on its
  own. Around un-intercepted Back: a **destination notice** naming the imprint left behind, its count
  and the way back, and the **catalog's** "N imprints on this browser have unsaved drafts".
- **A clean `localStorage` per test** (`test/setup.ts`): jsdom's storage is per **worker**, not per
  test, so without it one test's staged records restore into the next test that opens the same imprint
  — the feature working correctly and the harness lying.

### 20.2 The calls

**Authority: implementation (Claude Code), each open to veto.**

1. **Two different escapes, one geometry source.** A clone **proposal** may overlap and discloses what
   it covers; an **appended** tile is drawn until the operator places it and therefore parks **clear of
   everything the plane draws**. One `drawnBoxes` set feeds both the lane and the coverage report, with
   the rule **you do not cover the box you are inside**.
2. **A stale draft is restored-and-disclosed, never merged** (LES-251 holds). A **world-dependent**
   refusal keeps the key — the operator can resolve it and expect their copy back — and a **permanent**
   one drops it.
3. **One guard on `go`**, not one per link, so a new link cannot forget it; **Back stays
   un-intercepted** (nothing is lost) with the destination and catalog notices as the honest substitute.
4. **A gesture's deploy tag is the weaker of the two** — a batch holding any `override` reads as the
   fresh-deploy tag.
5. **The fingerprint stamp advances only in `performSave`'s success continuation**, never in an effect
   keyed on an etag.
6. **`invalidStoredRecord` is a shape check, not a re-validation** — it refuses what this app could not
   have produced and lets the rest through to the server, which stays the authority (LES-271's
   direction).

### 20.3 §3's S5.1.d–e, clause by clause — and the test that proves each

| clause | proving test (file › describe › it) |
|---|---|
| A pending clone or generated segment is a **hatched tile** at level 0 | `s51d.ui.test.tsx` › *acceptance 1* › *an UNPLACED generated segment is drawn in its site column, hatched, and selectable* + *a PLACED clone keeps drawing at its pin, and an unplaced one appears beside it* |
| …with **hatched chips at level 1** | `s51d.ui.test.tsx` › *acceptance 1* › *the hosts of a PENDING segment are HATCHED CHIPS at level 1…* + *a host added to an OBSERVED segment is a hatched chip beside its observed bands* |
| The hatch survives a real browser (no stylesheet override) | `pending.test.ts` › *the hatch survives a browser — the stylesheet cannot override it (LES-327)* (three its, including the not-vacuous check and "legible without colour") |
| Pending tiles disturb **nothing** that is drawn | `pending.test.ts` › *acceptance 2 — the budget and the goldens are untouched by pending tiles* (the goldens byte-identical; no drawn tile moves; a full plane parks **covering nothing, and never each other**; a collapsed group holds; a segment with no column is **reported**) |
| One change-list entry **per gesture**, in §3's shape, expanding to its records | `s51d.ui.test.tsx` › *acceptance 3* (two its); `gestures.test.ts` › *groupByGesture* (six its) and *a group's state is the WEAKEST of its records'* |
| The entry carries the S5 **deploy tag** | `s51d.ui.test.tsx` › *acceptance 3* › *shows the label, the counts, the state and the tag…* |
| **Undo removes its whole batch**, and its pin with it | `s51d.ui.test.tsx` › *acceptance 4* (five its: all 18 records + the pin; only its own batch; **re-saves once** and leaves the document as it was; the strand confirmation; and that a gesture stranding nothing asks nothing) |
| Undo leaves the document **byte-identical** to before the gesture | `batch.roundtrip.test.ts` › *acceptance 5 — undoing a SAVED clone leaves the file byte-identical to before it* (S5.1.b's, still the bytes half), with the UI half in *acceptance 4* above |
| A record can still be removed **singly** inside a gesture | `s51d.ui.test.tsx` › *undo of a PENDING-only gesture needs no save metadata* › *a per-record remove inside a pending gesture is enabled too* |
| **S5.1.e:** *Add host here* on a spine prefills the segment and the next free address | `s51d.ui.test.tsx` › *acceptance 5* (three its: it follows as records are staged and removed; a typed value is never overwritten; on an **added** segment it prefills that segment's own first address) |
| The **draft** survives a reload, an imprint switch, the breadcrumb, the outline and Back | `s51d.ui.test.tsx` › *acceptance 6* (survives a RELOAD; survives leaving and coming back **and is not carried into the other imprint**; a draft for another imprint is never rehydrated) |
| …and is **discarded on save** | `s51d.ui.test.tsx` › *acceptance 6* › *is DISCARDED when its records are saved* |
| A **stale** draft is reconciled visibly | `s51d.ui.test.tsx` › *acceptance 6* › *a STALE draft is reconciled VISIBLY…*; `draft.test.ts` › *restoring is RECONCILIATION, never a replay* (six its) |
| The back door runs the **front door's refusals** | `draft.test.ts` › *the back door runs the front door's refusals (S2, S3, S8)* (five its: a key the file now suppresses; a conflict the draft makes with itself; same-versus-different record; a record no gesture could have made; and that a well-formed record still comes back) |
| The recompile check has **three** outcomes | `draft.test.ts` › *the recompile check has THREE outcomes, not two (M3)* (four its); `s51d.ui.test.tsx` › *the recompile warning waits for the CAST* (three its, including *the warning is not ONE-SHOT*) |
| The fingerprint advances **only on a save** | `draft.test.ts` › *the draft's fingerprint advances on a SAVE, and on nothing else* — including *is not written by any effect keyed on an etag — a **STRUCTURAL** guard* |
| A blocked or full store degrades visibly and never throws | `s51d.ui.test.tsx` › *acceptance 6* › *a BLOCKED store says so once, names what is at risk, and does not take the app down*; `draft.test.ts` › *a blocked or full store degrades VISIBLY and never throws* (three its) |
| The **write token is never persisted** | `draft.test.ts` › *the write token is never persisted* (a grep of every `setItem` in the source, plus the shape argument) |
| Every way out of an imprint with pending edits is **guarded** | `s51d.ui.test.tsx` › *acceptance 7* (seven its: the breadcrumb; *Leave anyway* goes exactly where the click was going; the outline's imprint links; Escape refuses **without** a one-click exit; `beforeunload` only while there is something to lose; a navigation **within** the imprint is never guarded; and with the edits saved every exit is quiet again) |
| Leaving by the browser's own navigation is **said at the destination** | `s51d.ui.test.tsx` › *leaving an imprint by the browser's own navigation is SAID at the destination (S9)*; the cold start by *the CATALOG says which imprints hold drafts* (two its) |
| A restored batch and a new one stay **separate gestures** | `s51d.ui.test.tsx` › *a restored batch and a new one stay SEPARATE gestures (M2)* › *restore, then stage a second clone: two rows, two undos, two pins*; `draft.test.ts` › *a gesture id is unique to the SESSION, not to a mount* |
| Discarding a restored draft **drops its pins too** | `s51d.ui.test.tsx` › *discarding a restored draft drops its pins too (S1)* |
| The guard **dismisses**, it never acts | `s51d.ui.test.tsx` › *the leave guard DISMISSES, it never acts (S4)* |
| An undo that cannot re-save is **disabled with the true reason** | `s51d.ui.test.tsx` › *a removal that cannot re-save is DISABLED, with the reason (S5)* and *undo of a PENDING-only gesture needs no save metadata* (three its) |
| A **world-dependent** refusal keeps the draft; a permanent one drops it | `s51d.ui.test.tsx` › *a WORLD-dependent refusal keeps the draft (fix round D)* (two its) |
| The restore notice's count is the store's **own delta** | `s51d.ui.test.tsx` › *the restore notice's count is the store's own delta (N2)* |
| A parked tile is **visible** (the viewBox grows for it) | `pins.ui.test.tsx` › *grows for a PARKED added tile, which sits outside the pure canvas by design* |

### 20.4 The rounds — three must-fixes, and what each round's green suite had certified

Code-writer → **tester and reviewer in parallel** → a combined fix round (**3 must-fix, 9 should-fix,
3 nits**) → a bounded re-probe that found **a regression and a half-closed item** → a four-item round →
a one-line round. **Every fix carries a revert proof.**

- **M2 — the gesture-id collision, found independently by BOTH verifiers.** Ids came from a `useRef(0)`
  counter that resets on every workspace remount, and the draft persists each record's whole tag: a
  clone staged, reloaded, then followed by a second clone produced **two batches under one id** — one
  row labelled with the first, counting both, whose single undo removed **all 47 records and both
  pins**. The id also keys the grouping, the expander state and `data-gesture`, so one collision was
  wrong in four places (**LES-332**).
- **M1 — the parking lane landed on the O7 unplaced tile while reporting `covers: []`.** The lane
  cleared the *columns*; the coverage report was computed from a *different* set — so the one box it
  always hit was the one it never named. One `drawnBoxes` set now feeds both (**LES-334**).
- **M3 — the recompile check was skipped by a race, and its evidence re-stamped.** The restore compared
  against a cast that had not arrived, which is indistinguishable from a bundle that publishes no
  fingerprint; the draft-write effect then stamped the **new** fingerprint over the draft, destroying
  the comparison for every later restore too. Fixed by waiting for the cast to settle, passing **three**
  outcomes (a value, `null`, `"unknown"`), and advancing the stamp **only** in `performSave`'s success
  continuation (**LES-333**).
- **The should-fixes and nits, by class:** a restored draft's discard left its **pins** behind (S1); the
  back door skipped the front door's **refusals** (S2, **LES-335**); "the file has this record" and "the
  file has a **different** one" read alike (S3); the leave prompt **acted** instead of dismissing when
  its reason went (S4, **LES-336**); an undo was **disabled with an untrue reason** — a pending-only
  gesture re-saves nothing, so `metadataReady` is now required only where a **saved** record is involved
  (S5); a hand-edited draft could carry a record no gesture could make (S8); leaving by Back said
  nothing at the **destination** (S9); a **kept** draft offered no **discard** control (D); a cold start
  said nothing though drafts sat in storage (E); the viewBox did not grow for a **parked** tile (C); the
  restore notice's count was not the store's own delta (N2); and the undo's accessible name claimed a
  flat count when the confirmed path can remove more (N3).

### 20.5 Honest limits and residuals — recorded, not fixed

- **A kept draft is replaced by the next pending write in that imprint.** The stored draft *mirrors*
  what is pending — which is the whole of its self-healing property — so "kept so you can resolve it"
  holds until other work is staged there. The discard offer stands throughout, and nothing is lost
  silently.
- **A tile dragged onto another still overlaps by design** (an S4 pin is absolute and nothing reflows
  around it) and **names what it covers**. Only the *appended* tile parks clear of everything.
- **Browser Back is not intercepted.** Nothing is lost — the draft holds the work — and the guard's
  copy, the destination notice and the catalog list are the substitute. Intercepting history is a
  bigger commitment than this slice wanted to make on the operator's behalf.
- **The two behavioural tests for the fingerprint race are decisive only in isolation.** In a
  full-suite run the timing that produced M3 is not reproducible on demand; the **structural** guard —
  no effect keyed on an etag may stamp the draft — is what holds, and the test says so in its own name
  rather than implying more than it proves (LES-337).
- **A placement pin is written to the server at STAGE time**, while its segment is only pending and the
  change list says *pending (browser only)*. Undo retracts it and discarding a restored draft drops it,
  but abandoning the draft or clearing browser storage leaves a pin naming a segment that never
  existed, and an empty `layout.json` survives an undo — `docs/PRODUCT_GAPS.md` **GAP-096** (Low), with
  the cleaner option (carry pending pins in the draft, write them on save) recorded there.

### 20.6 What remains

*(Corrected 2026-09-16: this read "**S7 — the Grafana embed — is the only open slice**", which was true
for one day. **S7 was BUILT the same day, §21**, executing D5 — the plugin's Track C imprints screen is
retired and "Deploy as twin" kept as a slim action, closing GAP-083. **This order now has no open
slice.**)* Outside this order: the level-0 rendering order's one named follow-up (**column
wrapping**, unwritten), and the apply-proof follow-ups GAP-087..GAP-090.

### 20.7 Gates

No live-verify — nothing here deploys or touches a cluster. Vitest **1018 passed / 0 failed / 8 skipped
(1026)** (iteration 8 ended at 924 / 8; the eight skips are the opt-in `AXRANGE_LIVE_API` drift test);
`npm run typecheck` clean from zero; `npm run build` to a **scratch** `--outDir`, exit 0; pytest **3119
passed / 12 skipped, 3131 collected** — unchanged, and the one failing run during the round was a stray
`twin-configs-cast/corpapcn-twin/layout.json` written by a browser probe (removed; **LES-338**). The
four level-0 goldens are byte-identical. `doc-stats.py --check` clean.

## 21. S7 built as, 2026-09-16 — and D5 executed

**S7 is BUILT, and with it this work order has no open slice: S1–S7 and S5.1 a–e are all built.**
Iteration 10 of the ten-iteration Console run. CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED"
is the record; this section is the work order's own account against §3's S7 and **decision D5**
(§8.1, Authority: **user**, 2026-09-11 — retire the plugin's Track C Imprints screen here, keep "Deploy
as twin" as a slim plugin action).

**Version-locked: nothing moved, and there is no API change.** Op types **40**, catalog rules **114**,
pytest **3119 passed / 12 skipped, 3131 collected** — unchanged: the only Python edit in the slice is
**three DTO comments** (`axrange/api/dtos.py`) recording that the fields they describe no longer have a
UI consumer. Nearly everything is inside
`o2/grafana/plugins/axellio-axrange-control-panel/`, a **separate npm package** with its own build,
tests and typecheck baseline (LES-042).

### 21.1 What landed

- **The Imprints screen is deleted (D5).** `src/components/imprints/ImprintsScreen.tsx` (**646 lines**)
  and `ImprintTopologyPanel.tsx` (**183**) are gone, along with the **`initialView`** panel option and
  its in-panel toggle, and the imprint reads and types that existed only for them (`src/io/api.ts`,
  `src/types.ts`, `src/module.ts`, `src/components/AxrangeControlPanel.tsx`,
  `src/components/ControlToolbar.tsx`). **Verified by a sweep of the whole `o2/grafana/` tree** —
  provisioning and dashboards included — that no reference survives. The screen is deleted for what it
  rendered: a tolerant API's placeholder-plus-qualifier shown as a confident `0`
  (`docs/PRODUCT_GAPS.md` **GAP-083**, now Resolved).
- **`src/components/console/consoleUrl.ts` (new, pure).** `buildConsoleUrl(base, imprintId?)` → `unset`
  / `invalid(problem)` / `ok(href)`. Strict on purpose, because everything it refuses would otherwise
  fail **silently** inside a cross-origin frame: a relative base, a non-http(s) scheme, a query or a
  fragment, embedded credentials — each refused **with the reason** rather than dropped, since dropping
  produces a URL the operator did not write and cannot see is different. A base **path** is kept (the
  SPA can be co-served under a prefix) and the imprint id is percent-encoded. **It does not restate the
  SPA's URL grammar** — levels, the `10.80.20.0-24` CIDR spelling, the filter keys — which has one home
  in `o2/console/src/model/zoom.ts`; the plugin builds `<base>` and `<base>/imprints/<id>` and lets the
  frame navigate itself for anything deeper.
- **`src/components/console/ConsoleEmbed.tsx` (new).** A panel view that frames the Console **at its own
  origin**, with the new `consoleBaseUrl` (required) and `consoleImprintId` (optional) panel options. It
  **claims no load state** — a cross-origin frame's `onLoad` fires for a blocked frame as readily as for
  a rendered one — always offers an **open-in-a-new-tab** escape, and names what to check in likelihood
  order (§21.4).
- **`src/components/deploy/DeployTwinAction.tsx` (new) — the surviving action.** pick → **preview** →
  **confirm** → deploy: the preview runs the open, **pure** `POST /api/imprints/{id}/plan` (validate +
  whole-DAG build, no `state.db` write, no cluster call); the confirmation restates the imprint, the op
  count and the **target's `api_url`** (not just its node name); and only *Confirm deploy* calls
  `POST …/deploy`. Changing the imprint **or** the target retracts the preview. It renders **no
  belief-store measurement in any state**.
- **`src/io/errors.ts` (new) — one 401 message**, used by every write surface and by the four read
  surfaces that had their own copies. Through the datasource proxy a 401 has exactly one cause here
  (reads open, writes token-gated), so the message names the datasource, the `Authorization: Bearer`
  header and `AXRANGE_API_TOKEN` instead of saying "Unauthorized" and stopping.
- **`src/io/targets.ts` (new) — the `(unset)` placeholder is not data.** `GET /api/targets` publishes
  `node: "(unset)"` for an adapter config with an API URL and no default node; both pickers now render
  it **disabled with its reason**, so it cannot be sent as `{"target_node": "(unset)"}` on a real
  deploy. One rule, two widgets (`@grafana/ui`'s `Select` takes `isDisabled`, a native `<select>` takes
  `disabled`) — which is the only part they cannot share.

### 21.2 The calls

**Authority: implementation (Claude Code), each open to veto.** D5 itself is the user's.

1. **An iframe, not a shared React root — forced, not chosen.** §3 offered both shapes. The SPA calls
   **same-origin `/api`** and knows nothing of the datasource proxy; mounted inside the plugin its
   fetches would land on **Grafana's** origin, where the axRange API implements no CORS, so every read
   would fail at the browser. Recorded in the component, not just here, because the next reader will
   reach for the "cleaner" shape.
2. **`initialView` is deleted with no replacement.** A dashboard can no longer *open* on the Console and
   needs one click. Re-adding the pin under a new name would be re-adding what D5 deleted.
3. **Console and Deploy are two views**, so the frame gets the whole panel rather than sharing it with
   chrome that would shrink the thing being embedded.
4. **An in-panel confirmation, never `window.confirm`** — a browser dialog can be suppressed by the
   user or the browser, and it cannot show the destination's `api_url`.
5. **The preview is a required gate:** the deploy affordance does not exist until a plan for *that*
   imprint and *that* target does.
6. **A preview that lands after the picker moved is discarded and announced**, rather than locking the
   pickers for the seconds a real plan takes (and which nothing can cancel). Trapping the operator is
   worse than discarding a result — provided the discard is **said**.
7. **The 401 helper covers read surfaces too** — same gate, same cause, same fix, one wording.

### 21.3 §3's S7 and D5, clause by clause — and the test that proves each

*(The plugin's suite is jest; 68 cases across 5 suites, and **42 of 42** of this slice's cases show a
visible `1 passed` when run alone — see LES-339 for why that check is not ceremony.)*

| clause | proving test (file › describe › it) |
|---|---|
| The panel frames the Console, and **never** routes it through the datasource proxy | `ConsoleEmbed.test.tsx` › *frames the configured base URL, and never routes it through the Grafana datasource proxy* |
| A pinned imprint deep-links through the SPA's own route | `ConsoleEmbed.test.tsx` › *deep-links to a pinned imprint using the SPA route*; `consoleUrl.test.ts` › *appends the SPA-side imprint route, and never doubles the slash…* + *treats a blank imprint id as "no imprint"* + *percent-encodes an imprint id rather than interpolating it raw* |
| A base URL that cannot work is refused **with the reason**, not framed anyway | `consoleUrl.test.ts` (eleven its: unset, a relative base, a non-http(s) scheme, a query or fragment, credentials, a kept base path, a port and an IPv6 literal, trimming); `ConsoleEmbed.test.tsx` › *states the reason an unusable base URL was refused…* and *with no base URL configured, says which option to set and frames nothing* |
| The panel **claims no load success**, and always offers a new tab | `ConsoleEmbed.test.tsx` › *claims no load-success state, because a cross-origin frame cannot report one* + *offers a new-tab escape hatch…* |
| The blank-frame note names the **right** knobs, in likelihood order | `ConsoleEmbed.test.tsx` › *names the causes that can actually blank the frame, in likelihood order* + *does NOT blame Grafana's allow_embedding, which governs Grafana being embedded, not Grafana embedding* |
| **D5:** no Imprints screen, and no reference to it anywhere | the deletion itself (`ImprintsScreen.tsx`, `ImprintTopologyPanel.tsx`, the `initialView` option and its toggle), plus the tree-wide sweep of `o2/grafana/` recorded in §21.1 — there is no test for a file that does not exist, and the sweep is the evidence |
| **D5:** "Deploy as twin" survives, and renders **no** measurement | `DeployTwinAction.test.tsx` › *renders the picker with ids and the selected bundle's description, and no belief-store measurement at all (GAP-083)* + *renders no belief-store measurement in ANY state — picked, previewed or deployed (GAP-083)* |
| A preview runs the **pure** plan route and shows what would be created | `DeployTwinAction.test.tsx` › *previews through the PURE plan route and shows what would be created*; a failed one by *a failed preview is shown and leaves nothing to confirm* |
| Nothing deploys on one click; the confirmation names the **destination cluster** | `DeployTwinAction.test.tsx` › *never deploys on one click: the deploy button only OPENS a confirmation naming the destination cluster* + *deploys only after the explicit confirmation…* + *cancelling the confirmation deploys nothing* + *a double-click on the confirmation deploys once, not twice* + *a failed deploy keeps the confirmation open so it can be retried* |
| With no target picked, the panel does not present a guess as the destination | `DeployTwinAction.test.tsx` › *with no target picked, says the API chooses and does not present a guess as the destination* |
| A changed imprint **or** target retracts the preview — including a target arriving as a **prop** | `DeployTwinAction.test.tsx` › *changing the imprint retracts the preview…* + *changing the target retracts the preview too…* + *retracts the preview when the target arrives as a PROP, with no picker gesture at all* |
| A preview landing **after** the selection moved is discarded, and said | `DeployTwinAction.test.tsx` › *discards a plan that lands after the imprint changed…* + *discards a plan that lands after the TARGET changed, so no deploy reaches a cluster the plan never saw* + *says an in-flight preview is about to be discarded, rather than finishing silently* |
| A 401 names the datasource header as the fix | `DeployTwinAction.test.tsx` › *a 401 on the deploy names the datasource header as the fix, never a bare "Unauthorized"* |
| The `(unset)` target cannot become deploy data | `DeployTwinAction.test.tsx` › *shows the (unset) target placeholder disabled, with the reason, so it cannot become deploy data*; and the read failure by *says the target list could not be READ, rather than blaming a missing entry* |
| Loading and empty states are not claims nobody measured | `DeployTwinAction.test.tsx` › *says the catalog is loading before it settles, never "no imprints found"…* + *says a bundle declares no description instead of rendering an empty line* + *reports catalog entries the server could not list, with the reason* |
| A finished deploy hands off to the Topology view and can be re-run | `DeployTwinAction.test.tsx` › *deploys only after the explicit confirmation, and hands the new deploy to the Topology view* + *a re-preview after a deploy clears the finished-deploy line and offers the action again* |

### 21.4 The round — three findings, and what each would have cost

Code-writer → tester and reviewer → a fix round. The three that matter:

- **The stale preview, on the one screen whose whole job is to state the blast radius.** A plan
  resolving **after** a selection change overwrote the shown plan, so the confirmation could state one
  imprint's op count while deploying another — **measured at 695 ops from the wrong cast**, and in the
  sharper case naming a cluster the plan had never been built against, one click before real VMs are
  created. Fixed with a **live-pair gate on the side-effecting tail** (LES-274's shape): the pair a plan
  was built for is captured at the start and compared against a **ref** the render cannot stale, and the
  invalidation is keyed on the **state** rather than on a picker's `onChange` — because `targetNode` is a
  **prop**, so a target arriving from a route, a dashboard variable or another panel has no `onChange`
  to hang a rule on (LES-330's family).
- **The framing note named the wrong knob.** Grafana's `[security] allow_embedding` sets
  `X-Frame-Options` on **Grafana's own** responses: it governs Grafana *being embedded*, never Grafana
  *embedding another origin*. An operator would have flipped it, seen no change, and wrongly ruled the
  cause out. The note now names, in likelihood order: **(1)** whether the **browser** can reach the
  Console origin at all — this frame is the one call in the plugin the *browser* makes, where every
  other goes server-side through the datasource proxy, so operators have been configuring hosts
  reachable from the **Grafana host**; **(2) mixed content**, an https Grafana framing an http Console,
  which is the shape of the example URL and is blocked silently; **(3)** Grafana's
  `content_security_policy` `frame-src`; **(4)** last, the Console origin's own `X-Frame-Options` /
  `frame-ancestors`, marked **inert** because `axrange serve` sends neither (**LES-341**).
- **A placeholder string in an identifier field** was selectable and would have been sent as deploy
  **placement** data — the mirror image of GAP-083, and worse, because it is not merely displayed
  (**LES-342**).


<!-- original lines 2376-2385 of WORK_ORDER_console_canvas.md -->
### 21.6 Gates

No live-verify — nothing here deploys or touches a cluster (§21.5 lists what a live Grafana would add).
Plugin `npm run build` exit 0; plugin `npm run test:ci` **68 passed / 5 suites**; plugin `npm run
typecheck` **339 → 244** errors, **all** of the pre-existing TS2686 baseline (LES-042) — the drop is
**exactly the two deleted files**, **no surviving file's per-file count moved**, and **none of the 244
is in a file this slice created or touched**, which is the form that claim has to take in a package with
a tolerated baseline (**LES-340**). No cross-damage: Console `npm run typecheck` clean, Console Vitest
**1018 passed / 8 skipped**, `pytest -q` **3119 passed / 12 skipped, 3131 collected**. `doc-stats.py
--check` clean.
