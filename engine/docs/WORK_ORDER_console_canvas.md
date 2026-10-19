# Work Order — The Console canvas: an assertions-editing SPA over a cast twin

> **STATUS: COMPLETE — every slice is built (S1–S7 and S5.1 a–e, 2026-09-05 → 2026-09-16).** This
> file now keeps only what still constrains future work: the goal, per-slice status, the module
> layout, the layout contract, the §7 v1 defaults, the decisions, the cross-repo seams, the limits,
> and **§21.5 — what only a live Grafana can confirm (still open)**. The original status narrative,
> §2 (the inventory this order was drafted against), the per-slice specs (§3 S1–S7 and the S5.1
> amendment), §11 (sequencing) and every "built as" record (§12–§21) moved **verbatim** on
> 2026-09-19 to [`archive/work_orders/WORK_ORDER_console_canvas.built.md`](archive/work_orders/WORK_ORDER_console_canvas.built.md) — same headings, so a citation like "§18.3" or "§3 S5.1.b" is found
> there by its heading. The shipped behavior is described present-tense in
> `docs/design/15-console-canvas.md`; rulings recorded inside the archived specs and the "built as"
> *calls* subsections (§12.2, §18.2, §19.2, §20.2, §21.2 — e.g. S5.1.b's exact-versus-overlap CIDR
> ruling) still bind — read them there.
> Authority: **user** for the design (`docs/design/15-console-canvas.md`, `14-operator-assertions.md`
> §2–§4, `13-golden-twin-fidelity.md` §3.4); **implementation (Claude Code)** for the slicing, file
> layout, endpoint mechanics and every call marked as one. **Version-locked: none.**
> Follow-ups outside this order: level-0 **column wrapping** (an unwritten slice —
> `docs/WORK_ORDER_console_level0_rendering.md`), and the apply-proof gaps **GAP-087..GAP-090**.
>
> **Sequencing (user instruction, 2026-09-04): UI layout and view polish come later.** The slices
> front-load the **read-only** path — API, scaffold, levels 0–2, matrix — so the display half of the
> loop runs against the real cast bundle long before an assertion is authored. S1–S4 must be correct
> and stable, not finished-looking; the `dataviz` palette pass lands with S3's first screen.
>
> **Cross-repo:** axRange owns the SPA, the API and the assertions file's *writer*; aximprint owns
> the precedence resolver, the recompile, the sidecar and the `cast --assertions` reader
> (`docs/WORK_ORDER_reference_ingest.md`, to be written). §9 names every seam. Both sides on
> `wip/golden-twin`.

## 1. Goal

Build the operator surface `14` §4 names: a canvas that displays a twin's discovered structure at
three levels, lets an operator correct it with the first edit set, saves those corrections as
assertion records, applies them (recompiles the cast) and refreshes on the new fingerprint. The
canvas edits the **imprint**, never the deployed twin; the twin follows by `te deploy --update` or a
fresh deploy. Three properties are the point, and each is a test rather than an aspiration:

1. **Bounded elements per level** — an aggregate becomes its members, a member becomes a facts
   table; 786 entities and 1437 edges never share one plane.
2. **Layout is a pure function of identity** — the same export yields the same coordinates on every
   machine with nothing saved, and adding one segment moves only that segment. Without this the loop
   is unusable: after every apply the operator has to find everything again.
3. **Evidence is visible everywhere** — six classes, confidence, ghosts, overrides and drift,
   encoded the same way on the map, in the outline tree and in the inspector.

## 2. What exists today — archived

The as-of-drafting inventory (§2, "verified on this branch", 2026-09-04) moved verbatim to
[`archive/work_orders/WORK_ORDER_console_canvas.built.md`](archive/work_orders/WORK_ORDER_console_canvas.built.md).


## 3. Slices

Independently shippable and verifiable. Sizes: **S** ≈ one focused pipeline run, **M** ≈ two or
three, **L** ≈ a week of rounds. "Gated" means it cannot be *finished* without an aximprint
hand-back; the rest need only what is on disk today.

| Slice | Status | Spec (archived, §3) | Built-as record (archived) |
|---|---|---|---|
| **S1** — API additions (`axrange/api/`) | BUILT 2026-09-05/06 | §3 S1 | §12 |
| **S2** — SPA scaffold (`o2/console/`) | BUILT 2026-09-08 | §3 S2 | §13 |
| **S3** — read-only levels 0–2 and the matrix | BUILT 2026-09-09 (+ §14.6 post-ship id fix) | §3 S3 | §14 |
| **S4** — pins | BUILT 2026-09-10 | §3 S4 | §15 |
| **S5** — editing and save | BUILT 2026-09-10 | §3 S5 | §16 |
| **S5.1** — scope amendment (Authority: user, 2026-09-11): a selection-driven editing · b clone-and-drag · c host generation · d pending drawn/counted by gesture + the draft (closes GAP-094) · e level-1 add-host prefill | a BUILT 2026-09-15; b–c and d–e BUILT 2026-09-16 | §3 S5.1 (S5.1.a–e) | §18 (a), §19 (b–c), §20 (d–e) |
| **S6** — apply and refresh | BUILT 2026-09-10; §17.5 acceptance residual CLOSED 2026-09-11 | §3 S6 | §17 |
| **S7** — the Grafana embed | BUILT 2026-09-16 — an iframe; **D5 executed** (Imprints screen deleted, GAP-083 closed) | §3 S7 | §21 |

The slice specs above (headings `### S1 — …` through `### S7 — …`, and `#### S5.1 — Scope
amendment, 2026-09-11`) are in [`archive/work_orders/WORK_ORDER_console_canvas.built.md`](archive/work_orders/WORK_ORDER_console_canvas.built.md) verbatim.

## 4. Module and file layout for `o2/console/` (proposed — implementation call, open to veto)

```
o2/console/
  package.json  vite.config.ts  tsconfig.json  index.html  README.md
  src/
    main.tsx  App.tsx  routes.tsx        # shell + the URL-as-zoom router
    api/   client.ts                     # fetch wrapper, base URL, bearer token, errors
           types.ts                      # hand-written DTO mirrors (seam 1)
           hooks.ts                      # useTopology/useCast/useProvenance/
                                         #   useAssertions/useLayout/useDiff
    model/ keys.ts                       # entity keys, CIDR<->route encoding, the site rule
           evidence.ts                   # six classes, confidence, ghost + override rules
           graph.ts                      # export + cast -> one graph, joined by segment name
           budgets.ts                    # per-level budgets and the collapse rule
    layout/                              # PURE - no React, no DOM, golden-tested (§5)
           tiers.ts sites.ts level0.ts level1.ts matrix.ts lanes.ts pins.ts
    views/ Level0.tsx Level1.tsx Level2.tsx Matrix.tsx
           Outline.tsx Inspector.tsx ChangeList.tsx Legend.tsx Search.tsx Filters.tsx
    edit/  records.ts                    # gesture -> assertion record; the closed kind set
           store.ts                      # pending/saved/applied state + reducers
    theme/ tokens.css                    # the dataviz palette, light and dark
  test/  fixtures/                       # a trimmed real export + cast, checked in
         layout/*.golden.json            # committed coordinate snapshots (§5)
```

Boundaries, in the spirit of `CLAUDE.md`'s "one thing per top-level directory": `layout/` knows
nothing about React or the API; `api/` knows nothing about layout; `views/` compose the two.
`o2/console/` never imports from the Grafana plugin, and the plugin never imports from it.

## 5. The layout contract and its golden tests

1. **Determinism.** `layout0(export, cast)` called twice on one input yields byte-identical JSON.
   Committed golden files per level, produced from the checked-in trimmed fixture; a diff is a
   deliberate act, reviewed like any other.
2. **Locality (column-preserving edits).** A **column-preserving** edit —
   adding/removing a host, or a tile that fits an existing site's current row grid — changes **only**
   that tile's coordinates and leaves every other tile *and all the chrome* byte-identical (the
   chrome is centred on the total width, which a column-preserving edit does not change). This is the
   common edit-loop case and the property the loop rests on; it is asserted by diffing two golden
   outputs and requiring every untouched element's coordinates to be equal.
   **A column-COUNT-changing edit is not local** (S3 build, corrected from the original overclaim): a
   tile that adds a *row* to a site, or a new/promoted `/16` that adds a *column*, changes the site
   row's extent, so the centred chrome recentres and downstream columns shift. This is a deliberate
   consequence of ordered columns (`15` §4) — **S4 pins are the escape hatch** for holding a layout
   stable across such an edit, not a wider guarantee from the pure function. A second locality golden
   pins this column-changing baseline so S4 has a reviewed reference for whatever it commits to. The
   chrome keeps its width-centred aesthetic (leaving it accepts this churn as pins territory) rather
   than anchoring hard-left, which would look broken over a wide segment row.
3. **Budget.** Each level stays inside `15` §3's budget on the real bundle, or the collapse rule
   fires. The 27-host `generic-endpoint` band in `10.80.72.0/24` is the regression case and must be
   in the fixture (LES-185).

Goldens regenerate only through an explicit `npm run layout:golden`, never silently on test run.

## 6. Tests, build, serve, configuration

**Tests.** Vitest for `layout/`, `model/`, `edit/` (pure, no DOM); React Testing Library for the
panes' selection wiring; pytest for every API addition, in `tests/test_api.py` and
`tests/test_imprints_reader.py` beside the existing imprint tests, against **both** the package
fixture and the real `twin-configs-cast/corpapcn-twin/` bundle. The pytest suite gates the API half;
`npm run test` plus `npm run build` gate the SPA half.

```
cd o2/console && npm ci && npm run build          # -> o2/console/dist
axrange serve --spa-dir o2/console/dist \
              --imprints-dir twin-configs-cast \  # S1.8 adds this flag
              --db ~/.axrange/state.db
```

Development: `npm run dev` (Vite on 5173, proxying `/api` to `http://127.0.0.1:8080`) beside a plain
`axrange serve`. **Configuration:** the SPA's only configuration is the API base URL — same-origin by
default when co-served, overridable by `VITE_AXRANGE_API` for the dev proxy. No build-time knowledge
of imprint ids, catalogs, palettes or cluster facts. The write token comes from the deployment
(same-origin session or the Grafana datasource proxy), never compiled in, never in `localStorage`.

## 7. The `15` §12 open items, with their v1 defaults

Each stays open in the design note; the default is what ships unless the user rules otherwise.

- **O1 — the site bucket rule.** Two octets for RFC 1918 /24s, the longest common prefix otherwise. A
  site that mixes prefixes renders split until pinned — stated in the UI, not hidden.
- **O2 — the six-class palette.** A `dataviz` pass before S3's first screen. The mockup's hues (aqua
  solid observed, aqua hollow referenced, violet dotted imputed, orange ringed asserted, orange
  hatched imported, grey compiler, sky-blue hatched pending) are the starting point, not the contract;
  the pass owns light/dark validation.
- **O3 — the matrix in v1.** Yes, level 0 only. The hosts × hosts variant inside a segment ships with
  S3 if it costs nothing extra; otherwise it waits.
- **O4 — partial apply from the change list.** No. Apply applies the whole file.
- **O5 — highlight duration after apply.** Five seconds, then a `?changed=1` filter keeps it reachable.
- **O6 — the level-1 collapse rule (new, found in the data).** `15` §3's "one chip per group with its
  `count:`" is not computable: a cast node spec carries no member keys. v1 collapses a band over 15
  chips by **occupancy rung** (`13` §3.4's core ≥ 0.8 / regular 0.5–0.8 / transient < 0.5) with a
  count per rung, expanding on click. Revisit if aximprint publishes group membership.
- **O7 — unplaced and non-local entities (new, found in the data).** 49 local nodes with no segment
  and 596 non-local ones have no tile in `15` §3. v1: non-local entities are represented by the
  Internet tier's summary and addressable through the matrix's `external` row/column; unplaced local
  entities get one *unplaced* tile at the segment tier, counted and openable. Both are counted in the
  outline's root row, so nothing is silently invisible.

## 8. Decisions for the user

Genuine forks. **None is decided here.**

**D1 — Is `GET /api/imprints/{id}/topology` a new route or an alias?**
`GET /api/scenarios/{id}/topology-export?source=imprint` already serves exactly this DTO from exactly
this reader. *(a)* a thin alias delegating to `service.topology_export`; *(b)* an independent route
with its own service function; *(c)* skip it and have the SPA call the existing route.
**Recommend (a)** — the design names the route, the reuse rule (LES-025/LES-037) names the
implementation. *Yours because* it is a published API surface, and (c) would leave `15` §10's table
describing a route that does not exist.

**D2 — Does the apply subprocess run inside `axrange serve`'s process, or in a helper?**
*(a)* a background thread in the API process spawning `subprocess.run`, like the existing async
deploy; *(b)* a standalone helper (a systemd unit or one-shot script the API invokes and polls), so a
minutes-long compile never sits inside the web process; *(c)* the aximprint watcher `14` §4 names as
the later shape, built now. **Recommend (a) for v1, with the endpoint contract written so (b) or (c)
can replace it without the SPA noticing** — the whole reason the file is the contract. *Yours because*
`14` §7 lists the recompile mechanism as open to veto, and (a) puts a foreign process's lifetime
inside the API's.

**D3 — What does the API do with a cast bundle that has no provenance sidecar?**
Today `list_imprints("twin-configs-cast")` returns `[]` (verified), so pointing
`AXRANGE_IMPRINTS_DIR` at the real bundles yields an empty Console. *(a)* list sidecar-less bundles
with `has_provenance: false`, keep `GET /api/imprints/{id}` strict so the plugin's non-null
`mean_confidence` is untouched, and let the new endpoints serve without provenance; *(b)* relax
`ImprintSummaryDTO`'s confidence fields to optional and patch the plugin's two call sites;
*(c)* hard-gate the Console on aximprint copying the sidecar first. **Recommend (a)** — unblocks
S1/S3 today, breaks nothing shipped, keeps "no provenance" visible rather than fabricated.
*Yours because* (b) loosens a shipped Track-C contract with a live consumer and (c) changes the
sequence.

**D4 — Where do `<imprint>.assertions.yaml` and `<imprint>.layout.json` live?**
`14` §3 puts the assertions file beside the imprint's **IR store** (aximprint's side, where
`cast --assertions` reads it); the Console's imprint root is the **cast bundle** directory.
*(a)* both beside the cast bundle (`twin-configs-cast/<id>/`), recompile pointed at that path —
git-tracked, diffable, and safe from `install_cast_as_twin.py`, which writes named files and does not
clear the directory; *(b)* assertions beside the IR store, layout beside the bundle, each where its
reader expects; *(c)* both under an axRange state dir (`~/.axrange/console/<id>/`), keeping generated
bundles pristine. **Recommend (a)**, with the recompile passing an explicit path so (b)'s split is
never needed. *Yours because* it fixes a cross-repo file location and the recompile's argument list.

**D5 — Is the plugin's Track C imprints screen retired when the SPA lands?**
*(a)* retire it at S7 and let the plugin embed the SPA; *(b)* keep it as a read-only viewer
indefinitely (`15` §9's wording); *(c)* retire it at S3, when the SPA's read-only levels surpass it.
**Recommend (a)** — one surface per job, but not before the replacement is embeddable. *Yours
because* it deletes a shipped, live-verified screen.

**D6 — Does the level-0 tile carry the observed CIDR or the deployed one?**
Verified: they always differ on a relocated cast (`10.1.10.0/24` observed vs `10.200.0.0/24`
deployed), and the route is `/segments/{cidr}`. *(a)* observed everywhere, deployed shown as the
declared transformation in the inspector (the mockup's choice, and the one that matches "the canvas
edits the imprint"); *(b)* deployed everywhere; *(c)* observed on the map, deployed in the route so
the URL matches the twin. **Recommend (a)** — an assertion's key is an observed-address key, so the
operator should be looking at the address space those keys are in. *Yours because* it decides what
every URL, tile and assertion key in the product says.

### 8.1 Calls taken and one decision held, 2026-09-04

**Taken as implementation calls (Claude Code), open to veto:** **D1 (a)** — `GET …/topology` is a
thin alias of the shipped `GET /api/scenarios/{id}/topology-export?source=imprint`; design note 15
§10 corrected. **D2 (a)** — apply runs `subprocess.run` on a background thread inside `axrange
serve` in v1, behind a contract that lets a helper or the aximprint watcher replace it unseen.
**D3 (a)** — tolerant listing with `has_provenance: false`; `GET /{id}` stays strict; the missing
sidecar copy is already aximprint's hand-back. **The "stays strict" half is SUPERSEDED 2026-09-11**
(`docs/WORK_ORDER_console_level0_rendering.md` R7 / its D3, Authority: user; CHANGELOG 2026-09-11):
strictness on this route was refusing **every real cast bundle** (GAP-082), so the summary route now
enforces only the **addressing** id (directory name = `twin_config.id` = URL id) and warns about every
other id-shaped field a producer may legitimately write differently. The tolerant-listing half is
unchanged. **D4 (a)** — `assertions.yaml` and `layout.json`
live beside the cast bundle; the API passes the path to `cast --assertions`; design note 14 §3
reconciled. **D6 (a)** — the observed CIDR everywhere on tiles and in routes, the deployed one in
the inspector as a declared transformation; routes spell a CIDR as `10.80.20.0-24`; design note 15
§3/§4/§7 corrected.

**D5 — RULED 2026-09-11 (Authority: user): (a) with a carve-out.** The Grafana plugin's Track C
**Imprints screen retires at S7**, and **"Deploy as twin" survives as a slim plugin action** — the one
thing an operator wants from inside Grafana, and the one thing the SPA cannot offer as conveniently
there. One surface per job, and the replacement (the co-served SPA) is embeddable by then. Until S7
lands, that screen is **actively wrong** on cast bundles — it renders unmeasured values as measured
zeros now that the GAP-082 refusal no longer stops it loading (`docs/PRODUCT_GAPS.md` GAP-083,
LES-277) — which is a reason to land S7, not to patch the screen. S7's scope therefore gains: delete
the imprints screen, keep and re-point the deploy action, and remove the screen's now-dead
provenance/summary reads.

## 9. Cross-repo seams

Four, each a file or a command, none an import in either direction.

> **Status as of 2026-09-06: all four are closed or closed-enough — see §12.8, which supersedes the
> "not present today" statements below.** They are kept as written because they record what was
> missing when the slicing was decided, and the fourth's argument list is now pinned in the bundle
> itself rather than agreed by prose.

1. **The topology export's schema.** `<id>.topology.json`, `schema_version: "1.3"` today; `15` §10
   names a `bundle_schema` field that **is not present** in the shipped export. Either aximprint adds
   it or axRange keys compatibility off `schema_version`; the Console must refuse a major version it
   does not know rather than render a partial graph. Also unshipped and needed by `13` §3.4's ghosts:
   the per-node/per-segment `observation:` block (`windows_seen`, `windows_total`, `occupancy`,
   `role_confidence`) in the **twin config** — `docs/WORK_ORDER_deploy_gate.md` §10 owns that seam,
   and the Console degrades to the export's `windows_seen`/`windows` while it is absent.
2. **The provenance sidecar.** The real compiler shape is canonical and axRange adapts it
   (`docs/WORK_ORDER_fidelity_scorecard.md` §9.6). Two hand-backs: aximprint adds `confidence` and
   `signal` per-field extras and the `overrides:` record; and **the installer must copy the sidecar
   into the cast directory** — it does not today (`install_cast_as_twin.py` writes `<id>.yaml`,
   `tfc-cast.yaml`, `tap-cast.yaml`, `<id>.topology.json`, no sidecar), which is exactly why
   `list_imprints` cannot see the real bundle.
3. **The assertions file schema.** `axr-assertions/1` as `14` §3 specifies: stable entity keys, the
   closed record kinds, `as_of`, `source`, a `note` per record, implied confidence 1.0, no secrets.
   axRange writes and shape-validates it; aximprint reads it as a reference-ingest plugin and owns
   precedence. Neither side extends the kind set alone.
4. **The recompile invocation.** `aximprint cast --store <ir-store> --pit <PIT_ID> --out <dir>`
   exists; **`--assertions` and `--imprint-id` exist as of 2026-09-05** (aximprint reference-ingest
   S2; the invocation is recorded in design note 14 §3). The seam is the exact
   argument list, who supplies the store path and PIT id (the export carries `pit_id`, so the API can
   read it from the bundle), and what a successful recompile writes back — at minimum a
   `code_fingerprint` the apply endpoint can return.

## 10. Honest limits and non-goals

`15` §11's limits hold unchanged (not an editor of the deployed twin; not a free-form drawing tool;
no L2 or physical layout; hosts never pinned; budgets collapse aggregates rather than the frame
rate). Four this work order adds or sharpens:

- **No multi-user editing beyond the etag.** Two operators on one imprint get last-writer-refused,
  not merged: no locking, no presence, no operational transform. If a second editor becomes real, the
  etag is the wrong tool and the design says so.
- **An assertion is not an observation.** It is scored as `asserted` and shows in the evidence mix
  (`14` §2), so a twin made to pass by hand stays distinguishable from one the wire proved.
- **The apply path is a subprocess in v1** and inherits its failure modes: a compile that hangs holds
  a job slot, and the SPA's only recourse is the reported stderr.
- **Provenance without a sidecar is absent, not zero.** Where aximprint has not shipped a field, the
  Console says so rather than shading a fabricated confidence.

## 11. Sequence and gating — archived

Historical sequencing (all slices now built) — moved verbatim to [`archive/work_orders/WORK_ORDER_console_canvas.built.md`](archive/work_orders/WORK_ORDER_console_canvas.built.md).

## 12–21. "Built as" records — archived

Each slice's record (what landed, the calls, the clause-by-clause proving tests, the review rounds,
honest limits, gates) moved verbatim to [`archive/work_orders/WORK_ORDER_console_canvas.built.md`](archive/work_orders/WORK_ORDER_console_canvas.built.md), same headings:

- **§12. S1 built as, 2026-09-05/06** (§12.1–§12.8)
- **§13. S2 built as, 2026-09-08** (§13.1–§13.5)
- **§14. S3 built as, 2026-09-09** (§14.1–§14.6)
- **§15. S4 built as, 2026-09-10** (§15.1–§15.5)
- **§16. S5 built as, 2026-09-10** (§16.1–§16.6)
- **§17. S6 built as, 2026-09-10** (§17.1–§17.6)
- **§18. S5.1.a built as, 2026-09-15** (§18.1–§18.7)
- **§19. S5.1.b–c built as, 2026-09-16** (§19.1–§19.7)
- **§20. S5.1.d–e built as, 2026-09-16** (§20.1–§20.7)
- **§21. S7 built as, 2026-09-16 — and D5 executed** (§21.1–§21.4)

§21.6 (S7's gates) is archived too. **§21.5 stays here** — it is the one still-open,
operator-facing residual of the whole order:

### 21.5 Honest limits — and what only a live Grafana can confirm

**None of the embed is testable in this repo.** jest with jsdom can prove the panel builds the right
URL, frames it, claims nothing it cannot know and offers the escape hatch — it cannot prove the frame
**renders**. The following need a rebuilt, redeployed plugin against a live Grafana, and are listed
here and in `docs/SESSION_STATE.md` so they are asked rather than assumed:

- that the iframe **renders** rather than being refused (mixed content is the likeliest refusal, and the
  example URL's shape is exactly the risky one);
- that the SPA **inside** the frame reaches its same-origin `/api` from Grafana's page context —
  third-party **storage partitioning** can break the pending-edit draft (`localStorage`, S5.1.d) in a
  cross-site frame **even when the frame renders**;
- that the two new panel options appear and **persist in a saved dashboard**, and that dashboards saved
  with the **removed `initialView`** still load;
- the **datasource-proxy** path end to end, including that a genuinely missing bearer header produces
  the 401 whose wording was only checked here against a **synthetic** error;
- a **real deploy** through the action — the confirmation's `api_url`, the lease, the hand-off to the
  Topology view;
- **plugin signing / loading** at all.

Two further limits, recorded rather than fixed:

- **The previewed plan is not pinned at deploy.** `POST /api/imprints/{id}/deploy` accepts no `plan_id`
  and returns none, so the server **re-plans**, and preview↔deploy agreement rests on identical inputs
  plus a deterministic planner. The divergence window is an on-disk bundle edit between preview and
  confirm. Closing it is an API change — `docs/PRODUCT_GAPS.md` **GAP-098**.
- **`schema_version` is rendered unchecked** by the plugin's `TopologyExportView` *and* by the SPA
  (`o2/console/src/api/types.ts` mirrors the field and nothing reads it), where `15` §9 seam 1 says a
  consumer must refuse a major version it does not know rather than draw a partial graph —
  `docs/PRODUCT_GAPS.md` **GAP-097**, owed by both surfaces.
- **Cosmetic, noted in passing:** `DeployedTopology.tsx:355` collapses `role ?? role_baseline`, which is
  benign today but discards a pair the inventory publishes deliberately (an assigned role and the
  clone-vm baseline are different facts).

