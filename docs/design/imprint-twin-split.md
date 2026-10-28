# Imprints and twins: the split

**Status:** decided 2026-10-28 (user), being built on `wip/api-discovery-runs`.
**Supersedes:** the run chain `ingest -> bake -> cast -> install-cast` landing a
"cast bundle the API calls an imprint" (`api/Spec.md` A1 as built 2026-10-20),
and the apply flow that re-casts a bundle in place (`engine/src/axrange/api/
recompile.py`).

## 1. Vocabulary

| word | means | lives in | identity |
|---|---|---|---|
| **imprint** (pit-imprint) | what discovery learned about a network at one point in time, plus the operator's corrections to it | the IR store (`AXRANGE_AXIMPRINT_STORE`, a DuckDB of PITs) and a folder of operator files beside it | `pit_id`, a hash of source and bounds; `name`, a renamable label |
| **twin** | a deployable twin definition: twin config, traffic profile, tap plan, personas, scripts | the twin catalog (`catalog/twins/<id>/`) | the catalog id, chosen when the twin is cast or authored |
| **discovery run** | ingest + bake; produces an imprint | run records in the discovery DB | `disc-…` |
| **bake** | freeze the belief store into a PIT and give it an id and a name | `aximprint bake` | |
| **cast** | compile an imprint (with its assertions) into a twin under a chosen id | `aximprint cast` + `install-cast`, one API action | |
| **deploy / redeploy** | stand a twin up, or push the changed tiers of an edited twin onto its standing deploy | the deploy tiers (`te`, `tfc`, `tap`, `exr`) | |

"Install" is gone as a concept. What `install-cast` does (rewrite the PIT id to
the stable twin id, expand traffic assignments to instances, fill sensors,
check scripts and personas, name the files) is the second half of casting into
the catalog, and the API presents the two aximprint verbs as one action.

## 2. Two kinds of edit, two homes

- **Imprint edits are claims about the network**: this host is the domain
  controller, that segment is a /23, this collector scrapes those exporters.
  They are operator assertions (`axr-assertions/1`), applied by the compiler,
  and every consequence across every tier follows from them. Re-casting is
  idempotent: same imprint + same assertions = same twin.
- **Twin edits are intent about the twin**: a persona's intensity, a role's
  schedule, an exr scenario, an image. They are made on the tier documents
  themselves, which is where the deploy tiers read them, and they are what a
  redeploy diffs.

**The rule that keeps the two from fighting: a re-cast never overwrites an
edited twin.** A cast writes a new twin (or replaces an unedited one on
request). A twin that has been edited by hand keeps its edits; if the operator
fixes an assertion upstream they re-cast to a new id and carry their twin edits
over (a three-way merge is follow-on work, §7).

A hand-crafted twin lands in the twin catalog like any other, with no imprint
lineage. The imprint surface does not apply to it; the catalog shows the
difference as "cast from imprint X" against "authored".

## 3. Data model

### 3.1 The imprint

- `ir_pit` (IR schema `1.5`+): `pit_id`, `name` (unique label), `as_of`,
  bounds, `source`, `network`, `vantage`, sizes. **`1.6` adds
  `derived_from`**: the `pit_id` a save-as copied, `NULL` for a baked PIT.
- Operator files beside the store, keyed by PIT, under
  `<store dir>/pits/<pit_id>/`: `assertions.yaml` (`axr-assertions/1`) and
  `layout.json`. Keyed by id, not name: a rename must not orphan them.
- A save-as (`aximprint pit copy`) copies every row of a PIT under a new id,
  `pit-<sha256(source id | new name)[:10]>-<as_of>`, sets `derived_from`, and
  copies the operator files. The copy is then an imprint in its own right.

### 3.2 The twin

- The catalog entry as `install-cast` writes it today (`<id>.yaml`,
  `tfc-cast.yaml`, `tap-cast.yaml`, `<id>.topology.json`, `<id>.provenance.json`,
  `personas/`, `scripts/`), or a hand-authored one (`twin.yaml` manifest).
- Lineage is what the topology export's `cast_invocation` block already records:
  `pit_id`, the assertions file it was cast with, the compiler build. The
  catalog listing publishes `cast_from` = `{pit_id, pit_name}` or `null`
  (authored).
- Twin-level edit state (`edited_at`, by which tier) is a manifest field added
  when twin editing lands (§7); until then every cast twin is unedited and a
  re-cast with `replace: true` may overwrite it.

### 3.3 The run record

`{id, status, plugin, name, stages: [ingest, bake], pit_id, pit_name, …}`.
`imprint_id` stays on the wire as an alias of `name` for one release. A run
no longer produces a twin; its "result" is the imprint it baked.

## 4. Actions and routes

| action | route | does |
|---|---|---|
| discover | `POST /api/discovery/sources/{id}/runs {name, days…}` | ingest + bake into an imprint named `name`; refused if an imprint of that name exists |
| list imprints | `GET /api/imprints` | every PIT with name, as-of, sizes, `derived_from`, whether it has assertions, the twins cast from it |
| imprint detail | `GET /api/imprints/{pit}` | the same for one, by id or name |
| rename | `PATCH /api/imprints/{pit} {name}` | the label; the id never changes; run records follow |
| save-as | `POST /api/imprints/{pit}/copy {name}` | a new imprint, `derived_from` set, operator files copied |
| assertions | `GET/PUT /api/imprints/{pit}/assertions` | the operator's claims about the network, validated as `axr-assertions/1` on write |
| cast | `POST /api/imprints/{pit}/cast {twin_id, sensors?, replace?, relocate?, max_count?, no_chatter?}` | compile the imprint with its assertions into `catalog/twins/<twin_id>/`; `409` if the id exists and `replace` is not set, or is set on an edited twin |
| delete imprint | `DELETE /api/imprints/{pit}` | the PIT's rows and operator files; the twins cast from it stand |
| list twins | `GET /api/twins` | the catalog, each with `cast_from` or authored |
| twin detail etc. | `GET /api/twins/{id}[/provenance|/topology|/cast|/diff|/plan|/deploy|/layout]` | the bundle routes that were under `/api/imprints/{id}` |
| delete twin | `DELETE /api/twins/{id}` | the catalog entry; refused under a live deploy |
| discovery pits | `GET /api/discovery/pits`, `PATCH …/{pit}` | kept as aliases of the imprint list and rename |

Removed: `POST /api/imprints/{id}/apply` and `PUT …/assertions` on a bundle.
Their job (write assertions, re-cast, reinstall) is now "edit the imprint's
assertions, then cast". The post-apply diff survives as the cast's response:
what changed against the twin it replaced, when it replaced one.

## 5. Console

- **Discovery**: Sources, Runs. A run's stages are ingest and bake; a completed
  run links to its imprint.
- **Imprints**: the IR store. A list (name, id, as-of, size, derived-from, has
  assertions, twins cast from it); a page per imprint with rename, save-as, an
  assertions editor, a cast form (twin id, sensors, replace) and delete.
- **Twins**: the catalog. A list with lineage; a page per twin with the summary,
  segments, the deploy actions, and delete. Twin-tier editing and redeploy are
  §7.

## 6. Build order

1. `aximprint pit copy|delete`, schema `1.6` `derived_from`, operator files
   under `pits/<pit_id>/`, `cast --assertions` pointed there. *(discovery)*
2. Runs stop at bake; `name` on the run; duplicate-name refusal; the cast
   action; imprint routes over the IR store; bundle routes moved to
   `/api/twins`; `cast_from` on the twin listing. *(engine)*
3. The three console areas. *(ui)*
4. Specs, operations, architecture, handoff; the frozen `o2/console` is noted
   as reading the old paths. *(docs)*

Each step lands as its own commit with its suite green.

## 7. Follow-on (not in this build)

- **Twin-tier editing and delta redeploy.** Edit traffic, persona and exr
  documents on a twin, save and save-as at the catalog level, and redeploy the
  changed tiers. `te deploy --update` already diffs the twin environment
  against the standing deploy; a traffic apply re-asserts every assignment
  (an unchanged one is a cluster no-op, a changed one is re-driven) rather than
  computing a delta first; exr has no update path yet. The redeploy action
  will diff the saved documents against the version the standing deploy was
  built from and push per tier.
- **Three-way merge** of a re-cast against a twin's hand edits.
- **Retire `AXRANGE_IMPRINTS_DIR`** in favour of `AXRANGE_TWINS_DIR` (today
  both point at `catalog/twins`), and rename the engine's `imprints.py` /
  `ImprintSummaryDTO` family to twin names.
- **PIT retention**: nothing removes a baked PIT except an explicit delete.
