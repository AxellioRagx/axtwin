# Code review — Unified Console (2026-09-29, range c27339f7..99f1023e)

Transcribed by the orchestrator from the code-reviewer's hand-back. CONFIRMED = traced in code;
PLAUSIBLE = reasoned, not reproduced.

## Main invariant

"Editing one host never stops, resets or re-drives another host." Holds on every normal path traced:
the executor only receives `sub_plan` (this host's ops plus opted-in dependents); `force_reset_ops`
resets exactly those ops; `persist_plan` is INSERT OR IGNORE; de-scope is filtered by
`inputs.host_id` (a legacy row with no `host_id` is skipped — the safe direction); the overlay is
captured and re-entered in the worker thread; `origins` does not change op_ids or whole-profile
inputs. What remains is the edges.

## MUST FIX before any live write

**M1. A non-default target can silently write to the default cluster.** CONFIRMED.
`axrange/api/targets.py:129` (`adapter_config` optional), `:143` `_load_cfg` →
`adapter_proxmox/config.py:436-442`.
- Failure 1: a targets entry with no `adapter_config` loads `/etc/axrange/adapter.conf` (dev45's)
  while reading/writing `state-pxtwin1.db`. A `PUT …/personas?target=pxtwin1` or
  `DELETE /api/deploys/X?target=pxtwin1` then drives QGA/teardown on dev45 with pxtwin1's vmids.
- Failure 2: any `PROXMOX_API_URL`, `PROXMOX_API_TOKEN` or `DEFAULT_NODE` in the serve env overrides
  every target's file (`load_config` lets env win). Today only a comment in
  `adapter.targets.example.yaml:14-16`.
- Fix (in `load_targets_file`/`create_app`): require `adapter_config` on every entry once there is
  more than one target; refuse to start with a targets file while any cluster-identity env var is
  set; refuse two targets whose resolved `PROXMOX_API_URL` is the same; compare db paths with
  `Path(...).expanduser().resolve()` (relative vs absolute spellings pass the shared-db refusal at
  `targets.py:119` today).

**M2. Detached path loses stop failures and warnings; the row is still marked stopped.** CONFIRMED.
`traffic.py:2351` (`log=warnings.append`), `:2652-2662`, `:2354-2364`.
- `_run_host_edit` appends to a `warnings` list the PUT response already serialized; nothing stores
  it. `_stop_dropped_instances` stops best-effort, then `mark_op_destroyed` regardless.
- Failure: preview says "will stop X"; the QGA stop fails; the row becomes `destroyed` with
  `descoped_by: host-edit`; the tfc deploy shows `completed`; the UI says "Applied"; X keeps
  running with no trace. Same loss for role-clear notes and "per-host apply crashed" (logger only).
- Fix: store diagnostics in the state store (`merge_op_outputs(..., {"stop_error": ...})` or a
  deploy-level note surfaced by `/hosts`); in host-scoped mode do not mark a row destroyed when its
  stop raised; take a post-stop heartbeat asserting `dead`. Recurrence of LES-001/004.

**M3. The PUT plans before it takes the lease (window up to 60 s).** CONFIRMED.
`traffic.py:2221` (plan) → `:2243` (`containment_verdict`, `timeout_s=60`) → `:2265` (lease).
- Failure: a whole-profile apply or CLI `set-host` completes in the window; the host edit runs a
  `sub_plan` from a stale resolution (role map, peers, `requires` unchecked) and stamps
  `origin: host-edit` over what that apply just wrote.
- Same pattern in the whole-profile path: `host_edit_reverts` (`traffic.py:1653`) runs before the
  lease at `:1671`; a host edit finishing in between is silently reverted.
- Fix: lease first (or re-plan under the lease); run the revert guard under the lease.

**M4. Previewed plan ≠ executed plan; the UI discards the real plan.** CONFIRMED.
`PersonaEditor.tsx:97-101` keeps `phase.plan` and ignores `res.plan`; the PUT has no precondition.
- Failure: another operator changes the twin between Preview and Confirm; the server runs a
  different start/stop/dependents set. Breaks ruling rule 6.
- Fix: `/plan` returns a fingerprint (hash of start/stop/dependents plus the tfc ops' `updated_at`);
  PUT takes `expect_plan`, 409 on mismatch; at minimum the UI diffs `res.plan` against the preview.

## SHOULD FIX

- **S1. Containment gate blocks stop-only edits.** CONFIRMED. `traffic.py:2243` runs the gate
  unconditionally; `assignments: []` on an uncontained twin is refused 409. Skip the gate when
  `plan.start` is empty.
- **S2. The Console changes the seed of every kept persona.** CONFIRMED.
  `o2/console/src/model/personaChange.ts:188` sends `seed: null`. corp-twin profiles use seeds; any
  edit re-drives kept personas with seed null, reads as `changed`, and flags the host for the revert
  guard. Carry the recorded seed like `schedules`.
- **S3. A later successful host edit marks the tfc deploy `completed` while other rows are failed.**
  CONFIRMED. `planner/executor.py:325-331` derives deploy status from the sub-plan only. Recompute
  over all non-destroyed rows after `_run_host_edit`.
- **S4. `instance_id`/op_id collisions across hosts.** PLAUSIBLE (not present in corp-twin).
  `traffic.py:1192` `f"{host}-{persona}"`: host `a` + persona `b-c` and host `a-b` + persona `c`
  both give `a-b-c`. Guard in `plan_host_personas` (refuse `instance-collision` when a recorded row
  with the same `instance_id` has a different `host_id`); consider the same in
  `resolve_traffic_bindings`.
- **S5. Revert-guard markers never expire.** CONFIRMED. `traffic.py:2413`. `DESTROYED` rows with
  `descoped_by: host-edit` are durable; every future whole-profile change to that host needs
  `--revert-host-edits` forever. Re-mark on a successful whole-profile apply
  (`host-edit-reverted`) or ignore markers older than the last whole-profile apply.
- **S6. Orphan rows after a failed host edit.** PLAUSIBLE (LES-113). A failed added instance is
  reset to `pending` by a later whole-profile apply that omits it, and stays pending; a
  half-started unit is never stopped (de-scope sees COMPLETED rows only). De-scope should also stop
  and retire non-completed `drive-persona` rows absent from the new plan.
- **S7. Hard `requires` can be broken without a refusal.** CONFIRMED. A role change can remove the
  only holder of a role others hard-require; `_stale_dependents` lists them with `new: None` but
  the plan is `ok`. Refuse (or warn) unless opted in; the UI text "would keep the old address" is
  misleading when there is no new holder.
- **S8. `GET /api/deploys/{id}/health?host=` runs live guest-execs unauthenticated.**
  `service.py:4329`/`:4439`. Fixed commands, host validated — the risk is load, not injection. Add
  a per-(target, deploy, host) min interval (~10 s) and concurrency cap 1; cache the sweep and
  `/version` ~5 s.
- **S9. The Twin's taps section reads the default cluster.** PLAUSIBLE. `runtime.ts:91` `getTaps`
  hits `/api/o2/taps` (default target only). Render from `/hosts` `taps[]`.
- **S10. Duplication:** role-clear loop in `_run_host_edit` (`traffic.py:2354-2364`) duplicates
  `_retire_dropped_roles`; `cli/main.py:3961 _twin_persona_overlay` vs
  `api/service.py:4463 _twin_personas_overlay` (+ `_loaded_twin`); `resolve.py qga_ping` duplicates
  `GuestAgent.ping_once`; `_containment_gate` reads the egress posture, then `containment_verdict`
  reads it again. No layering violations found.
- **S11. Efficiency:** `_target_name`/`resolve_targets` re-parse adapter.conf per call;
  `twin_overlay` re-reads persona dirs per request; `range_view` builds a full inventory per twin
  to count hosts; `deploy_hosts` scans tfc ops twice.

## NOTES

- `apply_traffic_profile` / `POST /api/tfc` / `tfc deploy`: unchanged for the standing corp-twin
  (no host-edit rows; inputs byte-identical with `origins=None`).
- CLI `containment_verdict` refactor: equivalent.
- `display_tier`: a `tier=None` row now reports `"te"` (cosmetic).
- "latest" fallbacks (`service.py:1010`, `:1269`): now newest live te of this scenario; an o2-tier
  scenario's `latest` now returns nothing. No consumer found.
- Detached daemon thread: a server restart mid-run leaves the tfc deploy `running` and the lease
  held until stale. If `background()` raises after `force_reset_ops` the lease leaks — wrap in
  try/release.
- Auth: every new write route has `Depends(write_token)`; `/plan` is pure. The destroy typed-id
  check is client-side only — consider a server-side protected-deploy list.
- Write token: module variable only; OK. Double submit: guarded; OK.
- Frontend vs DTO: `PersonaChangeRequest` can't express `assignments: null`/`target_node`
  (acceptable); `REFUSAL_HEADLINES` has dead entries `no-host`, `not-te`; `PersonaPlan.dependents[]`
  and `TwinHostsDoc.egress` are subsets; `RangeTwin.twin_id` typed nullable vs required;
  `HostPersonaInstanceDTO.peers: dict[str,str]` and `HostTapDTO.nic_index: int` are strict — a
  recorded `None` would 500 `/hosts`; make Optional.
- UX: toggling "update dependents" invalidates the preview and hides the checkbox until re-preview.

## LES candidates

- **A — Ambient env precedence becomes a cross-target override once one process serves several
  named configs.** Refuse to start if any identity-bearing env var is set; refuse entries that fall
  back to a default search path. (M1)
- **B — A detached job must record its diagnostics in the state store;** a list shared with an
  already-returned response is a silent drop. Never mark a row done/destroyed on a best-effort
  action whose failure was only logged. (M2)
- **C — Preview → confirm writes need a server-checked fingerprint of the previewed plan.**
  Client-side draft keys only protect against the user's own edits. (M3/M4)

Recurrences, not new: LES-113 (S6), LES-037/025 (S10), LES-001/004 (M2, S3).
