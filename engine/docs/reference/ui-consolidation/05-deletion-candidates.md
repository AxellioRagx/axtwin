*Deletion candidates from the Unified Console consolidation, for the user's approval. Nothing is
deleted in this pass (`docs/reference/ui-consolidation/00-design-ruling.md` §4, §6.6). Each claim
below was independently verified (a grep, a git command, or a file listing) on 2026-09-29 at commit
`71d01491` (branch `wip/unified-console`), not copied from the earlier research reports.*

> **Executed 2026-10-01 (Authority: user — "approve the deletion list and delete them").** Items
> 1, 2, 3, 5, 6, 7, 8 below were deleted/removed as listed (`docs/CHANGELOG.md` 2026-10-01). **Item
> 4 (`twin-configs-cast/`) was kept** — its precondition (the Design area's Apply writing into the
> `twins/<id>/` layout) was never met, so it remains the one live Apply path for a real imprint, per
> its own entry's "NOT yet safe" finding. The sections below are the original, unedited proposal;
> this note records the outcome, it does not change the analysis.

## 1. `observability/` — stale pre-rename plugin build, with its `node_modules`

- **Path:** `observability/grafana/plugins/axellio-axrange-control-panel/`
- **Size:** 40,559 tracked files (`git ls-files observability | wc -l`); this is a `git`-tracked
  copy, so its on-disk footprint is carried in every clone/checkout.
- **Why safe:** `git ls-files observability | wc -l` confirms it is tracked (all 40,559 of them —
  matching the original `02-ui-surface-inventory.md` claim exactly). `grep -rl "observability/grafana"
  -- axrange/ o2/ scripts/` (excluding the directory itself) returns **no hits**: nothing in the
  engine, the `o2/` tree, or `scripts/` references this path. It is a pre-rename built copy
  (`module.js` dated 2026-07-08) that landed in commit `d5f037ea` alongside an unrelated persona
  change — accidental, per `02-ui-surface-inventory.md` Surface 5.
- **Command:** `git rm -r observability/`

## 2. Branch `wip/ui-viewer-spa` (and its remote)

- **Path:** local branch `wip/ui-viewer-spa` (2 commits: `7d92b550`, `2322004a`); also
  `remotes/origin/wip/ui-viewer-spa`.
- **Size:** 2 commits, ~1.9k LOC across 24 files (not on disk today — no `ui/` exists on any
  checked-out branch).
- **Why safe:** `git tag` lists `retired/ui-viewer-spa` pointing at the **same commit**
  (`2322004a`) as the branch tip — `git merge-base --is-ancestor` confirms the branch and the tag
  are mutually ancestors, i.e. identical history. The branch was retired by user decision
  2026-06-25 (`docs/CHANGELOG.md` ~:9620); its layout already lives on as the plugin's `canvas`
  renderer (topology renderer registry), which is itself a deletion candidate (§6).
- **Command:** `git branch -D wip/ui-viewer-spa` (local); `git push origin --delete
  wip/ui-viewer-spa` (remote — **user-authorized only**, per `CLAUDE.md` Git Policy; do not run
  unprompted). The tag `retired/ui-viewer-spa` is untouched either way and remains the historical
  pointer.

## 3. `.claude/worktrees/agent-ab92e05d08ed71105` and its branch

- **Path:** `.claude/worktrees/agent-ab92e05d08ed71105/` (a git worktree); branch
  `worktree-agent-ab92e05d08ed71105` (tip `f068f478`).
- **Size:** 522 MB on disk (`du -sh`) — almost entirely a second `node_modules` checkout duplicating
  `o2/console` and the plugin.
- **Why safe:** `git merge-base --is-ancestor worktree-agent-ab92e05d08ed71105 HEAD` succeeds — its
  tip is already an ancestor of the current `HEAD`, i.e. fully merged. `git worktree list` confirms
  it is a live worktree (not just a stray directory), so removal must go through `git worktree
  remove`, not a bare `rm -rf`, or git's own worktree bookkeeping is left dangling.
- **Command:** `git worktree remove .claude/worktrees/agent-ab92e05d08ed71105` then
  `git branch -D worktree-agent-ab92e05d08ed71105`.

## 4. `twin-configs-cast/` shim — **NOT yet safe; kept open pending a code change**

- **Path:** `twin-configs-cast/pit-395a61f04e-1780405200/` (`pit-….yaml`, `.topology.json`,
  `.provenance.json` symlinks into `twins/pit-…/generated/`, plus the untracked `layout.json` pin
  document).
- **Size:** 4.0 KB on disk (3 tracked files, symlinks/small JSON); trivial by itself, but see below.
- **Why NOT safe today (verified, contradicting the ruling's "list for deletion" framing):**
  `grep -rn "twin-configs-cast" axrange/` finds it is still a live read path —
  `axrange/twinconfig/registry.py` and `axrange/fidelity/reference.py` both resolve through it — and
  the Design area's `POST /api/imprints/{id}/apply` **still refuses** any `twins/<id>/` folder with a
  422 (`axrange/api/service.py:3758-3769`, `is_twin_folder(d)` check, unchanged by this round: "apply
  re-runs aximprint's install-cast, which writes the legacy cast-bundle layout, not `te.yaml` +
  `generated/`"). `twin-configs-cast/pit-395a61f04e-1780405200` is therefore the **only** bundle
  directory where the Design area's Apply currently works end-to-end for a real (non-fixture)
  imprint — deleting it today would remove the one live Apply path, not just a stale shim.
- **Command (do not run until the precondition below is met):** `git rm -r twin-configs-cast/` —
  **only after** the Design area's Apply learns to write into the `twins/<id>/` layout (the
  precondition `02-ui-surface-inventory.md` itself names), which is unscoped work, not part of this
  round (`docs/design/16-unified-console.md` §10 lists it under "not in this round" by implication —
  write-back to `twins/` is explicitly deferred there too).

## 5. `scenarios/` — leftover from the `target-environments/` rename

- **Path:** `scenarios/fabric-verify/state.db` (+ `-wal`/`-shm`), untracked.
- **Size:** 180 KB, 3 files, all untracked (`git ls-files scenarios` returns nothing).
- **Why safe:** Untracked (confirmed), so `git rm` does not apply — this is a plain filesystem
  removal. `grep -rln "\"scenarios\"\|'scenarios'\|scenarios/fabric-verify"` across `axrange/`,
  `o2/`, `scripts/` finds no reference to this specific path (only the unrelated, still-live
  `scenarios_dir`/`AXRANGE_SCENARIOS_DIR` config concept, which points at `target-environments/` in
  every real invocation). It is a leftover `state.db` from a fabric-verification run against the
  pre-rename `scenarios/` directory name (commit `b70972b8` renamed it to `target-environments/`).
- **Command:** `rm -rf scenarios/` (not a git operation — nothing here is tracked).

## 6. The deprecated Grafana surfaces

All already marked deprecated in place (banners added this pass; `docs/design/16-unified-console.md`
§9); listed here for the user's deletion approval, not deleted.

| Path | Size | Replaced by |
|---|---|---|
| `o2/grafana/plugins/axellio-axrange-control-panel/` | 794 MB on disk, but only **80** tracked files (`git ls-files … \| wc -l`) — the rest is gitignored `node_modules`/`dist`; `git rm -r` only removes the 80 | The Console's Catalog (guarded deploy) and Twin (topology, node/host detail, deploy tree) areas |
| `o2/grafana/dashboards/deploy-op-status.json` | 15.3 KB | Console Range (deploy list) + Twin (per-op status) |
| `o2/grafana/dashboards/pcap-browser.json` | 7.0 KB | none yet — was already a stub (`GET /api/pcaps` `implemented=false`); tracked as GAP-113 |
| `o2/grafana/dashboards/control-console.json` | 1.4 KB | the Console app itself (this dashboard only hosted the plugin panel) |
| `o2/grafana/dashboards/topology-eval/*.json` (7 files) | ~10.6 KB total | the Console's `src/layout/*` engine (deterministic, golden-tested; no renderer bake-off needed) |

**Why safe:** `grep -rn "deploy-op-status\|pcap-browser\|control-console\|topology-eval"
o2/compose/` finds no reference outside the dashboards' own provisioning folder — Grafana's
dashboard provisioner (`o2/grafana/provisioning/dashboards/dashboards.yml`) globs the directory, so
removing a JSON file needs no provisioning-config edit. The plugin directory's 80 tracked files are
independent of `observability/`'s 40,559 (different paths, confirmed by `git ls-files`, no overlap).

**Command:**
```
git rm -r o2/grafana/plugins/axellio-axrange-control-panel/
git rm o2/grafana/dashboards/deploy-op-status.json o2/grafana/dashboards/pcap-browser.json \
       o2/grafana/dashboards/control-console.json
git rm -r o2/grafana/dashboards/topology-eval/
```
(Deleting the plugin directory subsumes item 7 below — the six non-default renderers live inside it.)

## 7. The six unused (non-default) topology renderers, individually

- **Path:** inside the plugin (§6): `src/topology/layouts/{sugiyama,nodegraph,canvas}.ts`,
  `src/topology/renderers/{cluster,interactive,honeycomb}.tsx` (+ `HexNode.tsx`). The default,
  `tree` (`src/topology/layouts/tree.ts`), is the one `RENDERER_OPTIONS`/panel default in
  `registry.ts`.
- **Size:** 7 files, part of the plugin's 80 tracked files already counted in §6.
- **Why safe:** Confirmed present and wired only through `registry.ts`'s `RENDERERS` map (verified
  by reading it); the bake-off never chose a winner (`docs/reference/ui-consolidation/
  03-ui-history-and-decisions.md` row 3a). Listed separately only because the ruling names them
  individually — **in practice this is subsumed by §6**: deleting the whole plugin directory removes
  these seven files (six non-default renderers, `tree` too) at once. There is no reason to delete
  them piecemeal while keeping the rest of a now-deprecated plugin.
- **Command:** none needed beyond §6's `git rm -r` of the plugin directory.

## 8. The unused `axr-statedb` datasource

- **Path:** `o2/grafana/provisioning/datasources/datasources.yml` lines 76-86 (the `axr-statedb`
  block, `frser-sqlite-datasource`, `isDefault: false`) — not a standalone file; this is an edit,
  not a file deletion.
- **Size:** 11 lines.
- **Why safe:** `grep -rn "axr-statedb" o2/grafana/dashboards/*.json o2/grafana/dashboards/
  topology-eval/*.json` returns no hits — no dashboard queries it. It was a co-located fallback for
  the deploy/op-status dashboard's Infinity-over-API read (`o2/README.md` "Data flow"), which is
  itself deprecated (§6).
- **Command:** remove the `axr-statedb` entry from `o2/grafana/provisioning/datasources/
  datasources.yml` (a doc/config edit, not a `git rm`); leave the SQLite plugin install
  (`GF_INSTALL_PLUGINS`) alone if anything else in the compose file still needs it — verify before
  touching that line, it was not checked as part of this pass.

## Summary for approval

| # | Item | Verified safe now? |
|---|---|---|
| 1 | `observability/` | Yes |
| 2 | `wip/ui-viewer-spa` (+ remote) | Yes (tag preserves it) |
| 3 | `.claude/worktrees/agent-ab92e05d08ed71105` + branch | Yes (merged) |
| 4 | `twin-configs-cast/` shim | **No** — still the only working Design-area Apply path; keep until `twins/` write support lands |
| 5 | `scenarios/` | Yes |
| 6 | Deprecated plugin + 3 dashboards + topology-eval | Yes |
| 7 | Six non-default renderers | Yes, subsumed by #6 |
| 8 | `axr-statedb` datasource | Yes (config edit) |
