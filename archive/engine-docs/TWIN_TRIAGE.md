# Twin-config triage — Phase 1 of the twin consolidation (2026-09-25)

Read-only, offline triage of every file under `twin-configs/`, `twin-configs-cast/`,
`target-environments/` and `scenarios/`, for
[`docs/WORK_ORDER_twin_consolidation.md`](WORK_ORDER_twin_consolidation.md). Nothing was moved,
deployed or written to a cluster. Reproduce (≈2 min):

```
.venv/bin/python scripts/twin_triage.py                  # all four roots, markdown table
.venv/bin/python scripts/twin_triage.py --root twin-configs/svc-engines \
    --db /home/rocky/axr-svcengines.state.db             # plan tap/tfc against a standing twin
.venv/bin/python scripts/twin_triage.py --json > rows.json  # for Phase 2 re-run diffs
```

**Method.** te configs: `axrange te validate|plan --config <id> --twin-configs-dir <root> --images
deploy-dev45`; te scenarios: `te validate|plan <file> --images deploy-dev45`. tap/tfc: `axrange
tap|tfc validate <file>` (static lint + live-target resolution), `plan` only when a target
resolves; the file's sibling `personas/` is put on `AXRANGE_PERSONAS_DIR`, as the runbooks do.
exr and personas have **no CLI validate verb and no offline plan path** — checked with the loaders
the CLI uses (`load_exercise_spec`; `load_persona` + `validate_persona`, errors only, no role
universe). The default state DB (`~/.axrange/state.db`) has **no live deploy**; the standing
`svc-engines-24131435` twin lives in `/home/rocky/axr-svcengines.state.db`.

## 1. Summary

**281 files** (278 regular + 3 symlinks): `twin-configs/` 94, `twin-configs-cast/` 132,
`target-environments/` 52, `scenarios/` 3.

| Class | Count | Meaning |
|---|---|---|
| `valid` | 25 | te validates **and** plans (10 twin configs, 10 archetype scenarios, the maxrate `EXPANDED-IR`); 2 roles fragments (static OK, no target by design); svc-engines tap + tfc plan against the standing twin |
| `plan-needs-live-target` | 31 | tap/tfc static OK; the target twin is not deployed |
| `validates-loader-only` | 82 | 81 personas + 1 exercise spec load cleanly; no offline plan path exists |
| `unreferenced-stale-variant` | 12 | the maxrate ramp copies in `corp-enterprise/` (static OK; byte-identical to the dated record folder; referenced only by CHANGELOG 2026-08-14) |
| `target-pinned-dead-deploy` | 3 | static OK, but `target:` pins the destroyed deploy `pit-395a61f04e-1780405200-3caf94a1` |
| `validate-fails` | 1 | `corpapcn-disco/tap.yaml` |
| `plan-fails` | 0 | — |
| `generated-artifact` | 7 | topology / provenance / scorecard / layout JSON |
| `symlink` | 3 | the `pit-395a61f04e-1780405200/` alias folder → `corpapcn-disco/` |
| asset (not a tier file) | 117 | scripts, READMEs, image bindings, reports, runtime state |

| Disposition | Count |
|---|---|
| **migrate** to `twins/<id>/` (incl. `twins/_shared/`) | 177 |
| **archive** (the maxrate record folder) | 34 |
| **delete** | 18 — 12 maxrate duplicates, 3 alias symlinks, 3 stray `state.db*` |
| **pending D1** (`target-environments/`, work order §7) | 52 |

Notable failures (all four in `twin-configs-cast/corpapcn-disco/`, first error line quoted):
- `tap.yaml` — `validate-fails`: *"bindings[0] ('selector'): 'sensors' must be a non-empty list
  of sensor-id strings"*. It is aximprint's emitted template (no sensors chosen); `tap-xpress.yaml`
  is its hand-completed form (JOURNAL_axquery_discovery_loop.md:191).
- `tap-xpress.yaml` — *"tap-plan target 'pit-395a61f04e-1780405200-3caf94a1' resolved to deploy …
  which is not a live, active te deploy"*.
- `tfc-roles.yaml`, `tfc-exercise.yaml` — *"role 'corp-workstation' matches no host in target twin
  'pit-395a61f04e-1780405200-3caf94a1'"* (same pinned id; without `AXRANGE_PERSONAS_DIR` the first
  error is *"unknown persona 'cast-corp-workstation-local'"* instead). Retargeted to
  `pit-395a61f04e-1780405200` all three drop to `plan-needs-live-target`.

## 2. `twin-configs/`

Tier files are listed individually; personas/scripts per folder. Class `pnlt` =
`plan-needs-live-target`. Dates are the last git commit.

### `_shared/` → `twins/_shared/`

| File | Class | Disposition |
|---|---|---|
| `scripts/serve-sink.sh`, `scripts/README.md` | asset | migrate → `twins/_shared/scripts/` (consumed by `svc-engines/scripts/build-sink-payload.sh` by hard path, and cited in `egress_render.py:71`, `gen_sink_corpus.py`) |

### `corp-enterprise/` → `twins/corp-enterprise/`

| File | Class | Disposition |
|---|---|---|
| `corp-enterprise.yaml` | valid (PASS-WITH-WARNINGS) | → `te.yaml` |
| `exr-ad-killchain.yaml` | validates-loader-only | → `exr/ad-killchain.yaml` |
| `roles.yaml` | valid (fragment) | → `tfc/roles.yaml` (`roles_from:` is file-relative) |
| `tap-default.yaml` (2026-08-05) | pnlt | → `tap/default.yaml` (read by `test_twinconfig_tap_materialize.py:180`) |
| `tap-reshard.yaml` (2026-08-05) | pnlt | → `tap/reshard.yaml` (same test, :181) |
| `tap-xpress.yaml` (2026-07-26), `tap-xpress-peri.yaml` (2026-07-28) | pnlt | → `tap/xpress.yaml`, `tap/xpress-peri.yaml` |
| `tap-gw-span.yaml` (2026-08-27) | pnlt | → `tap/gw-span.yaml` — no doc ref, but a deliberate committed variant (693b0539) paired with `tfc-xseg` |
| `tfc-default.yaml`, `tfc-busy.yaml` (2026-08-04) | pnlt | → `tfc/default.yaml`, `tfc/busy.yaml` |
| `tfc-xseg.yaml` (2026-08-27) | pnlt | → `tfc/xseg.yaml` |
| `README.md` | asset | → `README.md` (rewrite paths) |
| `tap-maxrate-{all,dmz,wide}.yaml`, `tfc-maxrate-{l1,l2,l3,l4,l4b,l5,l6,l7}.yaml`, `roles-loadgen.yaml` (all 2026-08-14) | unreferenced-stale-variant | **delete** — byte-identical copies live in the record folder below (D6) |

### `corp-enterprise-maxrate/` → archive (D6)

A dated **record** of the 2026-08-13 max-capture-rate run, "not a live catalog entry" (its own
README). All 34 files → `docs/archive/twin-runs/2026-08-13-corp-enterprise-maxrate/` (or similar).

| File(s) | Class |
|---|---|
| `EXPANDED-IR.deployed.yaml` | valid (PASS-WITH-WARNINGS) |
| `corp-enterprise.yaml.snapshot` | asset — byte-identical to today's `corp-enterprise.yaml` |
| `tap-maxrate-*.yaml` ×3, `tfc-maxrate-*.yaml` ×8 | pnlt |
| `roles-loadgen.yaml` | valid (fragment) |
| `personas/loadgen-*.yaml` ×8 | validates-loader-only — byte-identical to `axrange/personas/catalog/` |
| `lab/` ×4 (2 image bindings, adapter.conf snippet, NIC note), `measure.py` (= `report/measure.py`), `report/` ×6, `README.md` | asset |

### `datacenter/`, `dmz-perimeter/`, `ics-ot/`, `sme-flat/` → `twins/<id>/`

| File | Class | Disposition |
|---|---|---|
| `<id>.yaml` ×4 | valid | → `te.yaml` |
| `tfc-default.yaml` ×4 | pnlt | → `tfc/default.yaml` |

### `svc-engines/` → `twins/svc-engines/` (standing live on dev45 — do not destroy)

| File | Class | Disposition |
|---|---|---|
| `svc-engines.yaml` | valid (PASS-WITH-WARNINGS) | → `te.yaml` |
| `tap-svc-engines.yaml` | valid (plans vs `svc-engines-24131435`, ops=10) | → `tap/default.yaml` |
| `tfc-svc-engines.yaml` | valid (plans vs `svc-engines-24131435`, ops=4) | → `tfc/default.yaml` |
| `personas/` ×4 | validates-loader-only | → `personas/` |
| `scripts/` ×8, `README.md` | asset | → `scripts/`, `README.md`. `build-sink-payload.sh` reads `twin-configs-cast/corpapcn-disco/topology.json` + `_shared/scripts/serve-sink.sh`; `provision-sink.sh` is its generated output. `enable-rdp.ps1` = svc-verify's copy |

### `svc-verify/` → `twins/svc-verify/`

| File | Class | Disposition |
|---|---|---|
| `svc-verify.yaml` | valid (PASS-WITH-WARNINGS) | → `te.yaml` |
| `tap-svc-verify.yaml`, `tfc-svc-verify.yaml` | pnlt | → `tap/default.yaml`, `tfc/default.yaml` |
| `personas/svc-verify-probe.yaml` | validates-loader-only | → `personas/` |
| `scripts/` ×5, `README.md` | asset | → `scripts/`. Three are byte-identical copies of `corpapcn-golden`'s generated `serve-dns-resolver-10-{20-196,80-64}-core.sh` / `serve-web-tier-10-80-72-core.sh` |

## 3. `twin-configs-cast/`

### `corpapcn-disco/` + alias `pit-395a61f04e-1780405200/` → `twins/pit-395a61f04e-1780405200/` (D2)

The alias folder holds three symlinks (`<id>.yaml`, `<id>.topology.json`, `<id>.provenance.json`
→ `../corpapcn-disco/*`) plus the **untracked** Console `layout.json` (2026-09-11). The config's
real id is `pit-395a61f04e-1780405200`.

| File | Class | Disposition |
|---|---|---|
| `twin_config.yaml` | valid (PASS-WITH-WARNINGS) | → `te.yaml` |
| `tfc.yaml` (aximprint-emitted, 147 assignments) | pnlt | → `tfc/cast.yaml` (default) |
| `tfc-roles.yaml` | target-pinned-dead-deploy | → `tfc/roles.yaml`, retarget |
| `tfc-exercise.yaml` | target-pinned-dead-deploy | → `tfc/exercise.yaml`, retarget |
| `tap-xpress.yaml` | target-pinned-dead-deploy | → `tap/xpress.yaml` (default), retarget |
| `tap.yaml` | validate-fails | → `generated/tap.emitted.yaml` (aximprint template, not a deployable tier file) |
| `topology.json`, `provenance.json` | generated-artifact | → `generated/` |
| `personas/` ×22 (incl. `disco-exercise{,-win}`) | validates-loader-only | → `personas/` |
| `scripts/` ×20 | asset | → `scripts/` |
| alias symlinks ×3 | symlink | **delete** (the folder dissolves) |
| alias `layout.json` (untracked) | generated-artifact | → `generated/layout.json`, **track it** (D4 contract) |

### `corpapcn-golden/` → `twins/corpapcn-golden/` (D5)

| File | Class | Disposition |
|---|---|---|
| `corpapcn-golden.yaml` | valid (PASS-WITH-WARNINGS) | → `te.yaml` |
| `tap-cast.yaml`, `tfc-cast.yaml` | pnlt | → `tap/cast.yaml`, `tfc/cast.yaml` |
| `corpapcn-golden.{topology,provenance}.json` | generated-artifact | → `generated/` |
| `personas/` ×22 | validates-loader-only | → `personas/` (17 byte-identical to `corpapcn-twin`'s) |
| `scripts/` ×14 | asset | → `scripts/` |

### `corpapcn-twin/` → `twins/corpapcn-twin/` (D5)

| File | Class | Disposition |
|---|---|---|
| `corpapcn-twin.yaml` | valid | → `te.yaml` |
| `tap-cast.yaml`, `tap-all.yaml`, `tfc-cast.yaml` | pnlt | → `tap/cast.yaml`, `tap/all.yaml`, `tfc/cast.yaml` |
| `corpapcn-twin.topology.json`, `corpapcn-twin.scorecard.corpapcn-twin-0e4f7d58.json` | generated-artifact | → `generated/` |
| `personas/` ×24 | validates-loader-only | → `personas/` |
| `scripts/` ×7 (`responder-*.sh`) | asset | → `scripts/` |

## 4. `target-environments/` (pending D1) and `scenarios/`

All ten archetype scenarios are `valid` (validate + plan); `ad-domain-lab`, `enterprise-multisite`,
`fabric-verify`, `optrahealth-twin`, `purple-team-range`, `soc-detection-lab` PASS-WITH-WARNINGS.
Each is `scenario.yaml` + `scripts/` (40 script/rule/conf assets; `suricata_ad.rules` is identical
in `ad-domain-lab` and `purple-team-range`) + the root `README.md` and `optrahealth-twin/README.md`.
Disposition waits on D1; if folded, each becomes `twins/<id>/te.yaml` + `scripts/`.

`scenarios/fabric-verify/state.db*` (untracked shm/wal; `state.db` gitignored, 2026-07-01) — a
pre-schema-v3 state DB today's code refuses to open → **delete**.

## 5. Cross-folder references (what Phase 2/3 will break)

**Code (runtime, not comments):** `axrange/twinconfig/registry.py:41` `_DEFAULT_CATALOG_DIR`;
`axrange/fidelity/reference.py:66` `_DEFAULT_REFERENCE_ROOT` (= `twin-configs-cast`);
`axrange/cli/main.py` ~116–125, ~328, ~903–958, ~3791 (defaults/help); `axrange/api/settings.py`
~96, `axrange/api/serve.py` ~47, `axrange/api/app.py` ~186–191 (`/api/twin-configs` route names —
URL, not a path; renaming is optional) and ~417; `axrange/api/console_files.py` (`layout.json` /
`<id>.assertions.yaml` written at bundle root).

**Docstring/comment path refs:** `adapter_proxmox/egress_render.py:71`, `planner/egress.py:21,56`,
`planner/dag.py:109`, `planner/pass1_emit.py:940,1162`, `twinconfig/{expand,gate}.py`,
`api/{dtos,imprints,service,topology_export}.py`, `validator/catalog.py:278,298` (rule text naming
`target-environments`), `o2/console/src/views/Level0.tsx:226`.

**Scripts with hard paths:** `twin-configs/svc-engines/scripts/build-sink-payload.sh:37-38`;
`scripts/gen_sink_corpus.py` (usage + `_shared` refs); `scripts/gen_egress_policy.py` (usage).

**Personas from another place:** svc-engines/svc-verify runbooks `export AXRANGE_PERSONAS_DIR=
twin-configs/<id>/personas` by hand; the maxrate `loadgen-*` personas resolve from the shipped
catalog, not the folder copy. No twin loads another twin's personas.

**Tests reading real (non-fixture) paths** — repoint in Phase 2, and check none turns into a skip:
- `tests/fidelity_support.py:34` (`REAL_CAST` = `twin-configs-cast/corpapcn-twin`)
- `tests/test_api_console.py:63-74, 1763-1786, 5902, 6102, 6153, 6200, 6247, 6488, 6545-6548`
  (real golden / sidecarless / pit bundles — **`skipif(not …is_dir())`-guarded**)
- `tests/test_imprint_routes_adversarial.py:60` (`REAL_CAST_ROOT`)
- `tests/test_twinconfig_catalog.py:130` (`_CAST_CATALOG_DIR`)
- `tests/test_twinconfig_tap_materialize.py:180-181` (corp-enterprise tap-default/reshard)
- `tests/test_traffic_tier.py:311, 2093, 2103, 2367` (`catalog_dir="twin-configs"`, globs
  `twin-configs/*/tfc-*.yaml`)
- `tests/test_svc_engines_recipes.py:40` (`TWINS = REPO / "twin-configs"`)
- `tests/test_activity_exec.py:850`, `tests/test_target_environments_boundary_taps.py:34`,
  `tests/test_twin_incremental_update.py:1615, 2146`, `tests/test_protocol_faithful_services.py:321`
  (`target-environments/` — only if D1 folds them)
- Also implicit: tests that call the registry's default catalog (`test_twinconfig_registry.py`,
  `test_twinconfig_cli.py`) list today's ids from `twin-configs/`.

`tests/fixtures/twin-configs/` and `tests/fixtures/cast-bundles/` are self-contained fixture
catalogs — unaffected by the move itself.

> **Note (2026-09-25):** `twins/corp-twin/` is the first `twins/` citizen — a new hand-authored
> twin (not a migration of any row above), built on the `corpapcn-disco` cast. See
> `docs/WORK_ORDER_corp_twin.md` and the note at the end of `docs/WORK_ORDER_twin_consolidation.md`.

> **Note (2026-09-26):** Phase 2 moved every `migrate` row into `twins/<id>/` and archived the
> maxrate record; the re-run (`--root twins`) and the per-row diff are recorded at the end of
> `docs/WORK_ORDER_twin_consolidation.md`. The `delete` rows are held for Phase 4.
