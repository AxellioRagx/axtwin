# Work Order — Twin configs consolidate into self-contained `twins/<id>/` folders

> **STATUS: Phase 1 (triage) DONE 2026-09-25** — read-only, offline; results in
> [`docs/TWIN_TRIAGE.md`](TWIN_TRIAGE.md), reproducible with `scripts/twin_triage.py`.
> **Phase 2 DONE 2026-09-26, Phase 3 PARTIAL** (record at the end: "Phase 2 + Phase 3 (partial)
> record"); D2–D6 taken as implementation calls (§7), D1 still open. Phase 4 NOT STARTED — its
> deletions are held for the user.
> Authority: **user (2026-09-25)** — every twin's per-tier configs live in ONE self-contained
> folder. **Route:** Pipeline (`/dev`) per phase — code-writer → tester → code-reviewer →
> doc-writer. No phase deploys or destroys; Phase 2's live check is a gated `te/tap/tfc plan`
> against a standing twin, never a deploy.

## 1. Goal

Today one twin's configs are scattered across up to four roots — `twin-configs/<id>/` (te + tier
variants), `twin-configs-cast/<id>/` (aximprint-cast bundles, their personas/scripts and
generated sidecars, the Console's layout), a symlink-alias folder
(`twin-configs-cast/pit-395a61f04e-1780405200/` → `../corpapcn-disco/*`), and a dated snapshot
folder (`twin-configs/corp-enterprise-maxrate/`). Personas load only when an operator remembers to
`export AXRANGE_PERSONAS_DIR=…` (the README runbooks do it by hand), and tier files pin concrete
deploy ids that die with the deploy. The goal: **one folder per twin**, holding every tier's
default and variants, its non-YAML assets, and its machine-written artifacts — so a twin is
understood, validated, copied and deployed as one unit.

## 2. The decision (Authority: user, 2026-09-25)

```
twins/
  _shared/                 cross-twin shared assets ONLY (e.g. scripts/serve-sink.sh)
  <id>/
    twin.yaml              root manifest (below)
    te.yaml                the twin_config (what <id>.yaml / twin_config.yaml is today)
    o2.yaml                optional — only if the twin carries its own O2 surface
    tap/<variant>.yaml     tap plans
    tfc/<variant>.yaml     traffic profiles (+ their roles_from fragments)
    exr/<name>.yaml        exercise specs
    personas/              twin-local personas
    scripts/               install/provision recipes (install: [{script: scripts/…}])
    generated/             machine-written: topology.json, provenance.json, scorecards, layout.json
```

`twin.yaml` (illustrative — the exact shape is Phase 2's to settle, §4):

```yaml
twin:
  id: corp-enterprise
  description: AD workhorse enterprise twin …
  cluster: dev45                 # the adapter.conf target this twin is meant for
  containment: contained         # see §7 D3 — must not restate te.yaml's egress block
  tiers:
    te:  { default: te.yaml }
    tap: { default: tap/default.yaml, variants: [tap/reshard.yaml, tap/xpress.yaml, …] }
    tfc: { default: tfc/default.yaml, variants: [tfc/busy.yaml, tfc/xseg.yaml, …] }
    exr: { variants: [exr/ad-killchain.yaml] }    # no default: an exercise is run on demand
```

Rules that fall out of it:
- A tier file inside `twins/<id>/` **defaults its target to the twin id**. A pinned deploy id
  (`target: <id>-<8hex>`) is the failure mode Phase 1 found three times (§5); new files must not
  pin one.
- **Single tier files stay deployable standalone** (`axrange tap deploy twins/<id>/tap/x.yaml`) —
  `twin.yaml` adds a default wiring, it does not become mandatory.
- `generated/` stays **git-tracked** — the operator's `layout.json` is a checked-in, diffable
  contract (D4, `docs/WORK_ORDER_console_canvas.md` §8.1; `.gitignore` keeps only `.history/` out).
- A twin never reaches into another twin's folder. Cross-twin assets go to `twins/_shared/`.

## 3. Rejected alternative — one YAML with per-tier sections

A single `twin.yaml` holding `te:`, `tap:`, `tfc:` … as sections was rejected (user, 2026-09-25):
- **Variant explosion.** `corp-enterprise` alone has 8 tap and 11 tfc variants (plus 2 roles
  fragments); every variant would be a section-of-a-section, or a copy of the whole file.
- **Non-YAML assets need a folder anyway** — scripts, personas, Suricata rules, generated JSON.
- **Tiers have separate lifecycles and separate writers** — the Console canvas and aximprint write
  some files, operators hand-edit others; one file means every writer rewrites everyone's content
  and every diff touches the lot.

## 4. Phased plan

### Phase 1 — triage (DONE 2026-09-25)

Every file under `twin-configs/`, `twin-configs-cast/`, `target-environments/`, `scenarios/`
inventoried and classified by the real offline CLI (`te validate|plan`, `tap|tfc validate`, the
exercise/persona loaders for tiers without a CLI validate verb). Table, dispositions and the
cross-reference list: [`docs/TWIN_TRIAGE.md`](TWIN_TRIAGE.md). Re-run:
`.venv/bin/python scripts/twin_triage.py [--root DIR]… [--db DB] [--json]` (read-only, subprocess).

### Phase 2 — migrate valid twins + the manifest loader

1. `git mv` each `migrate` row of the triage into `twins/<id>/` under the §2 names (the per-file
   target path is in the triage). Keep every twin **id** unchanged (deploy names, state-DB history
   and Console imprint ids key off it) — the folder name is the id (§7 D2 for the one exception).
2. **Loader** (`axrange/twinconfig/`): `twin.yaml` parse + validation; `resolve_twin_config_path`
   learns `<id>/te.yaml` (and `twin.yaml`'s `tiers.te.default`) ahead of today's
   `<id>/<id>.yaml` / flat `<id>.yaml` fast paths; `twin_config_source_dir` needs no change (it is
   the directory of the resolved file, so `scripts/` still resolves beside `te.yaml`).
3. **Personas:** resolving a tier through a twin adds `twins/<id>/personas/` to the persona search
   path automatically — the hand `export AXRANGE_PERSONAS_DIR` in the svc-engines/svc-verify
   runbooks becomes unnecessary (keep the env var as an override).
4. **Target default:** a `tap`/`tfc`/`exr` file under `twins/<id>/` with no `target:` resolves to
   `<id>`; retarget the three pinned-dead-deploy files to the twin id during the move.
5. **`roles_from:`** is resolved relative to the profile file — move `roles*.yaml` into `tfc/`
   beside the profiles that include them (or rewrite the relative path; the move is simpler).
6. **Idea, `axrange twin deploy <dir>`:** bring up each tier's `default` in order — `te` → (`o2`
   if the twin declares one) → `tap` → `tfc`; `exr` is never auto-run. Each step is the existing
   tier command on the default file, so it inherits every gate. `twin validate|plan <dir>` first —
   they are the offline half and the cheaper, earlier deliverable.
7. Repoint every test that reads a real bundle (triage §4) **and assert those tests do not skip**:
   `test_api_console.py` guards `REAL_GOLDEN`/`REAL_SIDECARLESS`/the pit bundle with `skipif(not
   …is_dir())`, so a move without a repoint turns them silently green.

### Phase 3 — repoint defaults and path references

- CLI `--twin-configs-dir` default `./twin-configs` → `./twins`: `axrange/cli/main.py` ~116–125
  (te `--config`), ~903–958 (`serve`, `twin-config list|show`), ~1032; `twin-config list`'s
  "No twin-configs found." (~3791).
- Scorecard `--reference` default `./twin-configs-cast/<scenario-id>` (`cli/main.py` ~328) and
  `axrange/fidelity/reference.py` `_DEFAULT_REFERENCE_ROOT` (~66) → `twins/<id>/` with the
  sidecars under `generated/`.
- Serve catalog: `axrange/api/settings.py` ~96 (`AXRANGE_TWIN_CONFIGS_DIR`), `axrange/api/serve.py`
  ~47, the imprints dir (`--imprints-dir twin-configs-cast` in runbooks), `axrange/api/app.py`
  ~417 error text; `axrange/api/console_files.py` writes `<bundle>/layout.json` — repoint to
  `generated/layout.json`.
- `axrange/twinconfig/registry.py` `_DEFAULT_CATALOG_DIR` (~41).
- Generator scripts with hard paths: `twin-configs/svc-engines/scripts/build-sink-payload.sh`
  (reads `twin-configs-cast/corpapcn-disco/topology.json` and `twin-configs/_shared/scripts/
  serve-sink.sh`), `scripts/gen_sink_corpus.py`, `scripts/gen_egress_policy.py` (usage docs).
- Code-comment / doc path references: `grep -rn "twin-configs\|target-environments"` — ~21 files
  under `axrange/` (mostly docstrings, incl. `adapter_proxmox/egress_render.py:71`,
  `planner/egress.py:21,56`), ~43 live docs, `o2/console/src/views/Level0.tsx:226`.
- **aximprint** (external repo) installs casts as `<bundle>/<id>.yaml` + `<id>.topology.json` +
  `<id>.provenance.json`. Its install target and names must follow (§7 D4) — until then a
  compatibility read of the old names stays in the loader.

### Phase 4 — retire the old roots

Remove `twin-configs/` and `twin-configs-cast/` once the loader has no fallback reader left and
every row of the triage is migrated, archived or deleted; move the archive rows; delete the
`delete` rows. The loader's `<id>/<id>.yaml` compatibility path goes last, with a CHANGELOG entry.

## 5. What Phase 1 found (headline — detail in the triage)

- **Every te config and every target-environment scenario validates and plans** (`--images
  deploy-dev45`). No te file is broken.
- **Tap/tfc files are statically sound;** nothing is live in the default state DB, so they
  classify `plan-needs-live-target`. The standing `svc-engines-24131435` twin lives in a separate
  DB (`/home/rocky/axr-svcengines.state.db`) — against it, `tap-svc-engines`/`tfc-svc-engines`
  plan cleanly.
- **Four real findings, all in `twin-configs-cast/corpapcn-disco/`:** `tap.yaml` is the
  aximprint-emitted template with no `sensors:` (fails validate — superseded by the hand-completed
  `tap-xpress.yaml`); `tap-xpress.yaml`, `tfc-roles.yaml`, `tfc-exercise.yaml` pin the destroyed
  deploy `pit-395a61f04e-1780405200-3caf94a1`.
- **Twin-local personas are invisible to `tfc validate` without `AXRANGE_PERSONAS_DIR`** — a
  pinned-target profile reports "unknown persona" until the env var is set (Phase 2 item 3).
- **The maxrate series is duplicated byte-for-byte** between `twin-configs/corp-enterprise/` and
  the dated record `twin-configs/corp-enterprise-maxrate/` (12 files), and referenced only by the
  2026-08-14 CHANGELOG entries.
- **Cross-twin coupling:** `svc-engines/scripts/build-sink-payload.sh` reads `corpapcn-disco`'s
  `topology.json`; `svc-verify/scripts/` holds copies of `corpapcn-golden`'s generated resolver
  and web-tier scripts. Neither is `_shared` material — see §7 D5.
- `scenarios/fabric-verify/state.db*` is a stray, pre-schema-v3 state DB (unreadable by today's
  code) — delete.

## 6. Validation per phase

- Phase 2: `scripts/twin_triage.py --root twins` reproduces every Phase 1 class (a `migrate` row
  that was `valid` stays `valid`); `pytest -n auto` green with **no new skips** in the
  real-bundle tests; `axrange twin-config list --twin-configs-dir twins` lists each id exactly
  once (the maxrate snapshot rename exists because recursive discovery double-listed an id).
- Phase 3: `grep -rn "twin-configs"` in `axrange/`, `scripts/`, `o2/` returns only the
  compatibility reader and CHANGELOG/archive history.
- Phase 4: the old roots are gone and the full suite is green.

## 7. Open decisions (user)

- **D1 — `target-environments/`.** These are IR *archetypes* (scenario content, per `CLAUDE.md`),
  not twins: a `scenario.yaml` + `scripts/`, no tier variants, no personas. Options: (a) fold into
  `twins/<id>/` with `te.yaml` = the scenario (uniform, one catalog); (b) keep a separate
  `target-environments/` root (archetypes ≠ twins; they are templates to instantiate); (c) fold
  only the ones used as deployable twins (`optrahealth-twin`, `fabric-verify`), keep the rest.
  Tests read `target-environments/` directly (triage §4). **Not decided here.**
- **D2 — the discovered twin's id.** `corpapcn-disco`'s `twin_config.id` is
  `pit-395a61f04e-1780405200`; the readable name lives only in the folder. Keep
  `twins/pit-395a61f04e-1780405200/` (id-true, symlink folder dissolves), or rename the id to
  `corpapcn-disco` (breaks the state-DB history and Console imprint id).
- **D3 — containment in `twin.yaml`.** Containment is declared today in `te.yaml`'s
  `twin_config.egress:` (GAP-101). `twin.yaml`'s `containment:` should be either derived/displayed
  from it or the single declaration moved up — not a second hand-typed copy
  (`planner/egress.py`'s "resolved, not re-typed" rule).
- **D4 — aximprint coordination.** The cast installer writes `<id>.yaml` and id-stemmed sidecars
  into the bundle root; the new layout wants `te.yaml` + `generated/`. Change aximprint's
  installer, or keep an import step in axRange that files a cast into the layout.
- **D5 — the three `corpapcn` casts.** `corpapcn-twin` (2026-08-27, a scorecard), `corpapcn-golden`
  (2026-09-06, the Console's golden fixture), `corpapcn-disco` (2026-09-08, the discovery-loop
  twin) are casts of the same network. Tests use the first two as real fixtures. Migrate all
  three, or archive the older two once tests are repointed? Triage proposes *migrate all three*.
- **D6 — the maxrate series.** Archive `twin-configs/corp-enterprise-maxrate/` as a dated record
  (e.g. `docs/archive/twin-runs/2026-08-13-corp-enterprise-maxrate/`) and drop the 12 duplicate
  files from `corp-enterprise`, or keep the ramp as live `tap/`/`tfc/` variants of
  `twins/corp-enterprise/`. Triage proposes *archive the folder, keep no live copies*.

### §7 decisions taken for Phase 2 (2026-09-26; Authority: implementation (Claude Code), orchestrator call)

- **D1 — not touched.** `target-environments/` stays where it is; the user's decision is pending.
- **D2 — keep the id.** `twins/pit-395a61f04e-1780405200/` (the `corpapcn-disco` content). The
  folder name is the real `twin_config.id`, so state-DB history and the Console imprint id are
  unchanged. Its `twin.yaml` description records the old folder name.
- **D3 — derived, not re-typed** (as the corp-twin S0 loader already does): each `twin.yaml`'s
  `containment:` is checked against `te.yaml`'s `egress:` block. Only `svc-engines` declares one,
  so it alone is `contained`.
- **D4 — aximprint installer unchanged.** The readers learned the twin layout instead
  (`axrange/twinconfig/bundle.py`: `te.yaml` via the manifest, machine-written files under
  `generated/` with their aximprint id-stemmed names). Console **apply** (`install-cast`) into a
  twin folder is refused with a 422 naming D4 until an import step exists — **future work**:
  an axRange step that files a cast's `--out` into `twins/<id>/` (or an installer flag).
- **D5 — migrate all three casts** (`corpapcn-twin`, `corpapcn-golden`, the pit twin).
- **D6 — archive the record, hold the duplicates.** `twin-configs/corp-enterprise-maxrate/` →
  `docs/archive/twin-runs/2026-08-13-corp-enterprise-maxrate/` (34 files, `git mv`, re-triaged
  identically). The 12 byte-identical ramp copies stay in `twin-configs/corp-enterprise/` for the
  user's Phase 4 deletion call; so do the three alias symlinks (`twin-configs-cast/<pit>/`, now
  re-pointed into `twins/<pit>/` so they do not dangle) and `scenarios/`.

## 8. Out of scope

- Changing any tier's file **schema** (`tap-plan:`, `traffic-profile:`, `exercise:`, `persona:`,
  `twin_config:`) — the move is a relocation plus a new manifest, not a format change.
- `tests/fixtures/twin-configs/` and `tests/fixtures/cast-bundles/` — self-contained fixture
  catalogs; they follow in Phase 3 only if the loader's discovery rule changes under them.
- Deploy/destroy of anything; the standing `svc-engines-24131435` twin is untouched.

## Note (2026-09-25) — `corp-twin` is the first `twins/` citizen

`twins/corp-twin/` (`docs/WORK_ORDER_corp_twin.md`) is the first folder in the §2 layout, and it
landed with a **minimal** slice of Phase 2 items 2–4, not the phase: `axrange/twinconfig/manifest.py`
(`twin.yaml` parse + validation — `containment:` is checked against `te.yaml`'s `egress:` block,
the D3 "derived, not re-typed" reading), `resolve_twin_config_path` learning `<id>/twin.yaml` →
`tiers.te.default` then `<id>/te.yaml`, the default search path becoming `twin-configs/` then
`twins/`, and `tap|tfc validate|plan|deploy --twin ID [--variant NAME]` plus the two folder
defaults (twin `personas/` on the persona path, target = twin id). No existing twin was moved;
the migration, `twin validate|plan|deploy`, the API/serve side and Phase 3's repointing are
still this work order's.

## Phase 2 + Phase 3 (partial) record (2026-09-26)

**Moved** (`git mv`, 210 renames, 11 `twin.yaml` added): `corp-enterprise`, `datacenter`,
`dmz-perimeter`, `ics-ot`, `sme-flat`, `svc-engines`, `svc-verify` (from `twin-configs/`);
`corpapcn-golden`, `corpapcn-twin`, `pit-395a61f04e-1780405200` (from `twin-configs-cast/`); shared
assets → `twins/_shared/`. Names follow the triage: `te.yaml`, `tap/<v>.yaml`, `tfc/<v>.yaml`,
`exr/<name>.yaml`, `generated/` (topology/provenance/scorecard kept id-stemmed; the pit's
aximprint `tap.yaml` template → `generated/tap.emitted.yaml`; its untracked Console `layout.json`
→ `generated/layout.json`, now tracked). The three pinned-dead-deploy tier files now target the
twin id. `roles*.yaml` sit in `tfc/` beside the profiles that `roles_from:` them.

**Proof.**
- Full plans (every op + params, barriers, edges) byte-identical before/after for all ten twins —
  same `plan_id`s (e.g. `corp-enterprise-86423faa`, `svc-engines-915403f1`,
  `pit-395a61f04e-1780405200-2a1a25f1`); only the script source directory moved.
- `scripts/twin_triage.py --json` on old vs new roots: every migrated row keeps its class except
  the three retargeted files (`target-pinned-dead-deploy` → `plan-needs-live-target`, as the
  triage predicted) and `corp-enterprise/tfc/roles.yaml` (`valid` → `plan-needs-live-target`:
  inside a twin folder a no-target file now defaults to the twin id — Phase 2 item 4 working). The
  archived maxrate record re-triages identically (34/34). The one `validate-fails` row is still
  the aximprint template, now `generated/tap.emitted.yaml`.
- The real-bundle tests do not skip: `test_the_real_bundles_are_present` (API) and
  `test_the_real_pit_twin_the_symlink_tests_rebuild_from_is_present` fail rather than let the
  `skipif` guards go green; the Console's real-bundle vitest files read `twins/` too.

**Phase 3 done here:** `--twins-dir` is the flag (old `--twin-configs-dir` kept as an alias, one
`dest`) on `te --config`, `serve`, `twin-config list|show`; `AXRANGE_TWINS_DIR` wins over
`AXRANGE_TWIN_CONFIGS_DIR`; `registry.default_catalog_dir()` is `twins/` (the default SEARCH PATH
keeps the legacy `twin-configs/` first as the compatibility reader until Phase 4); the scorecard's
default reference root is `twins/` (a twin folder counts as a reference only when it carries a
cast's generated export/sidecar — `sme-flat` is not scored against itself); imprint / Console-file
/ recompile / topology-export / fidelity readers share `axrange/twinconfig/bundle.py`
(`layout.json` and `.history/` under `generated/`; the operator's `<id>.assertions.yaml` stays at
the twin root); `svc-engines/scripts/build-sink-payload.sh`, `gen_sink_corpus.py`,
`gen_egress_policy.py`, the twin READMEs (the hand `export AXRANGE_PERSONAS_DIR` step is gone),
`CONVENTIONS.md` and design docs 09/13/14/15.

**Phase 3 still open:** code comments in files another agent was editing concurrently
(`twinconfig/{schema,expand}.py`, `planner/pass1_emit.py`); historical work orders / CHANGELOG /
LESSONS keep their old paths as history; tier-file comments inside moved twins (content, left
byte-identical); `twins/corp-twin/scripts/build-sink-payload.sh:46-47` still reads the old cast and
`_shared` paths (not touched here — corp-twin was being edited concurrently; repoint to
`twins/pit-395a61f04e-1780405200/generated/pit-395a61f04e-1780405200.topology.json` and
`twins/_shared/scripts/serve-sink.sh`). **G15** (the API/serve path does not add a twin's
`personas/`): not small — the API takes inline traffic profiles, so it needs a per-request persona
overlay on a process-global registry; left open.
