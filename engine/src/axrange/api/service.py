"""Service layer — wraps the axRange engine for the API.

Calls the SAME library entry points `axrange/cli/main.py` calls (validator,
planner, adapter, state store) and maps engine objects → DTOs (`dtos.py`). No
`subprocess`; structured results in, structured DTOs out. Errors raise
`ApiError` (mapped to HTTP status in `app.py`).

This module holds NO FastAPI imports — it is pure engine glue, unit-testable on
its own.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from axrange import __version__
from axrange.api import dtos
from axrange.api.settings import ApiSettings

# First stdlib `logging` use in this module (code-review follow-on, CLI/API
# twin-config-expansion-warning parity): `create_deploy`'s `config` path
# always returns its expansion warnings on `CreateDeployResponse.
# config_warnings` too, so this is belt-and-suspenders (a caller that reads
# the response body never needs the log), not the primary channel -- unlike
# `destroy_deploy`'s sibling-cascade `cascade_errors`, which deliberately
# stayed response-only (no stdlib logging precedent there, 2026-07-25
# review), this IS a fire-and-forget synchronous expansion step whose
# caller might reasonably ignore the field, so a server-side log line is
# the backstop.
logger = logging.getLogger(__name__)


class ApiError(Exception):
    """A service-layer failure with an HTTP-ish status hint."""

    def __init__(self, status: int, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------------------
# Scenario resolution
# ---------------------------------------------------------------------------
def _resolve_scenario_path(settings: ApiSettings, scenario: str) -> Path:
    """A scenario ref is either a known id (under scenarios_dir) or a path."""
    p = settings.scenario_path(scenario)
    if p is not None:
        return p
    # Allow an explicit path (CLI-parity for headless/CI use).
    cand = Path(scenario)
    if cand.is_file():
        return cand
    raise ApiError(404, f"scenario not found: {scenario}")


def _is_safe_catalog_ref(ref: str) -> bool:
    """THE catalog-id guard, shared with `axrange.api.imprints` rather than
    restated (M1).

    An id indexes files under a TRUSTED catalog root only, never an
    operator-controlled path segment (S8, code review: the twin-config/imprint
    catalog fallbacks in `_resolve_read_ir_path` called
    `materialize_twin_config` directly with the raw `ref`, bypassing every
    guard — `axrange.twinconfig.registry` has none of its own; it trusts its
    caller).

    It is a POSITIVE match now, and the reason is a live finding: the
    previous deny-list (`/`, `\\`, `..`) accepted a lone `.`, which reaches
    the handler percent-encoded as `%2e` and resolves to the catalog ROOT —
    `PUT /api/imprints/%2e/layout` wrote `layout.json` into it."""
    from axrange.api.imprints import is_safe_catalog_id

    return is_safe_catalog_id(ref)


def _images_root_for(settings: ApiSettings, scenario_path: Path) -> Path:
    if settings.images_root:
        return Path(settings.images_root)
    return scenario_path.parent


# ---------------------------------------------------------------------------
# Health / scenarios
# ---------------------------------------------------------------------------
def health(settings: ApiSettings) -> dtos.HealthDTO:
    state_ok = False
    try:
        from axrange.planner import StateStore

        # A read: it opens the store read-only and never creates it (fix
        # round 2), so a store that does not exist yet is reported false.
        if not Path(settings.db_path).is_file():
            raise FileNotFoundError(settings.db_path)
        store = StateStore(settings.db_path, readonly=True)
        store.latest_deploys(limit=1)
        store.close()
        state_ok = True
    except Exception:  # noqa: BLE001
        state_ok = False
    return dtos.HealthDTO(status="ok", version=__version__, state_db=state_ok)


def list_scenarios(settings: ApiSettings) -> dtos.ScenarioListDTO:
    out = []
    for sid in settings.list_scenarios():
        p = settings.scenario_path(sid)
        out.append(dtos.ScenarioSummaryDTO(id=sid, path=str(p) if p else ""))
    return dtos.ScenarioListDTO(scenarios=out)


# ---------------------------------------------------------------------------
# Validate (pure; no cluster mutation) — §3
# ---------------------------------------------------------------------------
def _load_manifests(images_root: Path):
    from axrange.manifest.loader import ManifestError, load_registry

    try:
        return load_registry(root=images_root)
    except ManifestError as e:
        raise ApiError(400, f"manifest load failure: {e}")


def _one_source(scenario: Optional[str], config: Optional[str], imprint: Optional[str],
                params: Optional[dict[str, str]]) -> str:
    """Exactly one of scenario/config/imprint (the `create_deploy` rule)."""
    given = [k for k, v in (("scenario", scenario), ("config", config), ("imprint", imprint)) if v]
    if len(given) != 1:
        raise ApiError(400, "specify exactly one of 'scenario', 'config', or 'imprint'")
    if params and given[0] == "scenario":
        raise ApiError(400, "'params' only makes sense with 'config' or 'imprint'")
    return given[0]


def validate(settings: ApiSettings, scenario: Optional[str] = None, *,
             config: Optional[str] = None, imprint: Optional[str] = None,
             params: Optional[dict[str, str]] = None) -> dtos.ValidateResultDTO:
    """Pure. A `config`/`imprint` is expanded (with `params`) exactly as a
    deploy would expand it, validated, and its tempfile removed (fix round 2)."""
    from axrange.validator.engine import validate_full

    kind = _one_source(scenario, config, imprint, params)
    tmp: Optional[Path] = None
    if kind == "scenario":
        sp = _resolve_scenario_path(settings, scenario)
        images_root = _images_root_for(settings, sp)
    elif kind == "config":
        sp, images_root, _src = _resolve_config_scenario_path(settings, config, params)
        tmp = sp
    else:
        sp, images_root, _src = _resolve_imprint_scenario_path(settings, imprint, params)
        tmp = sp
    try:
        manifests = _load_manifests(images_root)
        result = validate_full(str(sp), manifests=manifests)
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    findings = [
        dtos.FindingDTO(
            rule_id=f.rule_id,
            severity=f.severity.value,
            message=f.message,
            location=f.location,
        )
        for f in (list(result.errors) + list(result.warnings) + list(result.infos))
    ]
    return dtos.ValidateResultDTO(
        disposition=result.disposition.value,
        exit_code=result.exit_code,
        passes_run=list(result.passes_run),
        errors=len(result.errors),
        warnings=len(result.warnings),
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Plan (pure) — §3
#
# The shared load-config / build-ClusterContext / parse-IR / build-Plan
# sequence (code-review finding #3, 2026-07-07: it used to be duplicated
# between this module and `cli.main._load_planning_inputs`) now lives in
# `axrange.planner.entry` together with the executor run/release halves
# (2026-10-20): the o2control playouts import all three, and a domain layer
# must not depend on this one. They are re-exported here so the existing
# import sites (`cli.main`, tests that monkeypatch these names on this
# module) keep working; `build_plan_and_ctx` below is the API-flavored
# wrapper that turns `PlanBuildError` into `ApiError`. Each surface still
# owns its OWN scenario-resolution and validation step (the API resolves an
# id via `scenarios_dir` + returns a simplified findings list on failure, the
# CLI takes a literal path + renders the full validator tree to stdout).
# ---------------------------------------------------------------------------
from axrange.planner.entry import (  # noqa: E402  (re-exports)
    PlanBuildError, PlanBuildResult, release_deploy_resources, run_deploy_executor,
)


def build_plan_and_ctx(scenario_path: Path, manifests: Any, *,
                       adapter_config: Optional[str],
                       target_node: Optional[str] = None,
                       tier: str = "te",
                       scenario_dir: Optional[str] = None) -> PlanBuildResult:
    """`axrange.planner.entry.build_plan_and_ctx`, with `PlanBuildError`
    translated to this surface's `ApiError` (HTTP 400). `_build_plan_and_ctx`
    below and `cli.main._load_planning_inputs` catch the `ApiError`."""
    from axrange.planner import entry

    try:
        return entry.build_plan_and_ctx(
            scenario_path, manifests, adapter_config=adapter_config,
            target_node=target_node, tier=tier, scenario_dir=scenario_dir,
        )
    except PlanBuildError as e:
        raise ApiError(e.status, e.message) from e


# ---------------------------------------------------------------------------
# Incremental update resolution (`docs/WORK_ORDER_twin_incremental_update.md`)
# — a deliberately REDUCED subset of `docs/PLANNER.md` §9 (re-planning); see
# that work order for the full decision record. Shared by `cli.main.
# cmd_deploy` and `create_deploy` below (LES-037): both need identical
# "resume vs. --update vs. --fresh vs. first deploy" semantics, and the
# pinned-plan_id-after-an-update subtlety (§3.4) makes this meaningfully more
# than the one-line `find_deploy_for_plan` lookup either surface used before.
# ---------------------------------------------------------------------------
@dataclass
class DeployResolution:
    """Which standing deploy (if any) a deploy-creation call should target,
    and the `Plan` to actually persist+execute against it.

    `plan` is `plan` (the caller's already-built, freshly-plan_id'd Plan)
    UNLESS a standing deploy's `plan_id` had to be PINNED into a rebuild — an
    `--update`, or a same-generation resume of a twin a PRIOR `--update`
    pinned away from what a fresh `derive_plan_id` would now produce (§3.4).
    `existing` is the standing `DeployRow` to resume/update, or `None` (a
    first deploy, or `--fresh`). `diff` (a `plandiff.PlanDiff`) is set ONLY
    when this resolution is an `--update`.

    `reapply_ops` (`docs/WORK_ORDER_responder_op_row_drift.md` option A) is
    the subset of `diff.mutated` an opt-in `--reapply` is allowed to
    re-execute in place -- empty unless `--update --reapply` was passed AND
    `plandiff.reapplyable_ops` matched. Both callers force-reset exactly
    these ids to `pending` before running the executor, so the resume
    short-circuit doesn't skip an op whose persisted row is `completed` but
    whose content the operator just changed.
    """

    plan: Any
    existing: Any = None
    diff: Any = None
    reapply_ops: frozenset[str] = frozenset()


def _rebuild_plan_at(
    ir: Any, manifests: Any, ctx: Any, *, plan_id: str, raw: Any,
    scenario_dir: str, tier: str,
) -> Any:
    """`build_plan(..., plan_id=plan_id, ...)`, translating any failure into
    an `ApiError` (code review S9) the SAME way `build_plan_and_ctx` already
    does for the caller's FIRST `build_plan` call — `resolve_deploy_plan`'s
    two internal rebuild call sites (the `--update` pin, and the same-
    generation resume heal) used to let a bare `PlanBuildError` escape
    uncaught: an API 500 instead of a 4xx, and an unhandled CLI traceback
    instead of the clean `except ApiError` `cmd_deploy` already has for
    every OTHER refusal this function raises."""
    from axrange.planner import build_plan

    try:
        return build_plan(
            ir, manifests, ctx, plan_id=plan_id, raw_ir=raw,
            scenario_dir=scenario_dir, tier=tier,
        )
    except Exception as e:  # noqa: BLE001 — mirrors build_plan_and_ctx's own catch
        raise ApiError(400, f"plan build failed: {e}")


def resolve_deploy_plan(
    store: Any, plan: Any, *, ir: Any, raw: Any, manifests: Any, ctx: Any,
    scenario_dir: str, tier: str = "te", fresh: bool, update: bool,
    allow_destroy: bool, reapply: bool = False,
) -> DeployResolution:
    """Decide which deploy (if any) `plan` resumes or updates.

    Raises `ApiError` for every `--update`-specific refusal: `--update` +
    `--fresh` together (400); `--allow-destroy` given without `--update`
    (400 — code review S5, matching this module's existing `--param only
    makes sense with --config` precedent); `--update` with no standing
    deploy for this scenario (400, "deploy it once first"); more than one
    standing deploy for this scenario (409, code review M5 — refuses rather
    than silently picking one via `ORDER BY updated_at DESC LIMIT 1`, since
    `--fresh`/a failed partial/an edit-without-`--update` can all trivially
    leave two live — this refusal fires ONLY inside the `update:` branch,
    MUST-FIX 2 code review: a PLAIN, non-`--update` deploy call must never
    be blocked by an ambiguous store, it simply mints a fresh deploy_id as
    it always has); `--reapply` given without `--update` (400, same
    precedent as `--allow-destroy`); any Mutated op NOT covered by
    `--reapply` (409, naming the op/field/old→new, plus why `--reapply`
    didn't cover it when the flag was passed); and a non-empty removed set
    without `allow_destroy` (409). Both the CLI and the API translate the
    SAME `ApiError` to their own surface's error shape, exactly like every
    other refusal already routed through this module.

    `reapply` (`docs/WORK_ORDER_responder_op_row_drift.md` option A) is the
    ONE narrow exception to "Mutated is always a hard refusal": with it set,
    a mutated op that `plandiff.reapplyable_ops` matches (a `run-script`
    whose ONLY differing input is `script_content`) is returned in
    `DeployResolution.reapply_ops` for the caller to force-reset + re-execute
    instead of refused. Everything else in `diff.mutated` still refuses --
    including the rest of `docs/PLANNER.md` §9.2's redo-rules table, which
    remains unimplemented.

    `plan` MUST already be built (fresh `plan_id`, i.e. NOT `--update`-pinned
    yet) — this function pins/rebuilds it internally when needed, using
    `ir`/`raw`/`manifests`/`ctx`/`scenario_dir`/`tier` (the SAME inputs the
    caller's own `build_plan_and_ctx` call already produced — no re-parse).
    """
    from axrange.planner.plandiff import diff_plan, reapplyable_ops

    if fresh and update:
        raise ApiError(400, "--update and --fresh are mutually exclusive")
    if allow_destroy and not update:
        raise ApiError(400, "--allow-destroy only makes sense with --update")
    if reapply and not update:
        raise ApiError(400, "--reapply only makes sense with --update")

    if fresh:
        return DeployResolution(plan=plan)

    # The ordinary resume lookup — unchanged from before this feature: an
    # unpinned scenario (never `--update`d) always resolves here, exactly
    # as it always has.
    direct = store.find_deploy_for_plan(plan.plan_id, exclude_destroyed=True)
    if direct is not None and not update:
        return DeployResolution(plan=plan, existing=direct)

    # `direct` missed, or `--update` was explicitly requested: find the
    # standing deploy by SCENARIO_ID instead of a recomputed plan_id
    # (`deploys_matching`'s `scenario_id` filter, `docs/
    # WORK_ORDER_twin_incremental_update.md` §4.1) — the only thing that
    # still identifies "the standing deploy for this scenario" once
    # `plan_id` has drifted away from `derive_plan_id(scenario_id, ir_hash)`
    # by a prior update (§3.4: plan_id becomes the twin's STABLE IDENTITY,
    # ir_hash its CURRENT GENERATION).
    #
    # UNBOUNDED (no `limit=1`, code review M5) — two standing `te` deploys
    # of one scenario are trivially reachable (`--fresh` while the prior
    # deploy is still live, a `failed` partial, or the LES-126 edit-
    # without-`--update` path below).
    standing_rows = store.deploys_matching(
        scenario_id=plan.scenario_id, tier=tier, exclude_destroyed=True,
    )

    if update:
        # The ambiguity refusal below is `--update`-ONLY (MUST-FIX 2, code
        # review). It used to run unconditionally ABOVE this `if update:`
        # branch, so a PLAIN (no `--update`) deploy call that reaches this
        # far — `direct` missed because the content was genuinely edited —
        # ALSO tripped it whenever two standing deploys already existed for
        # this scenario_id (trivially reachable via the LES-126 edit-
        # without-`--update` path itself: two ordinary edits, deployed back
        # to back with no `--update`, leave exactly this shape). Its advice
        # ("destroy the stale one(s) first") is actively wrong for an
        # ordinary deploy — it tells the operator to destroy two LIVE
        # twins just to mint a third. Only `--update`'s destructive,
        # single-target semantics actually need "exactly one candidate or
        # refuse"; silently binding a DESTRUCTIVE update to whichever
        # standing row happens to have the newest `updated_at` risks
        # running it against the wrong twin with no indication to the
        # operator which one was picked, so THIS path still refuses and
        # lists every candidate instead of guessing.
        if len(standing_rows) > 1:
            ids = [r.deploy_id for r in standing_rows]
            raise ApiError(
                409,
                f"{len(standing_rows)} standing {tier} deploys found for "
                f"scenario {plan.scenario_id!r} — refusing to guess which one "
                f"to target: {ids}. Destroy the stale one(s) first "
                "(`axrange te destroy <deploy_id>`), then retry.",
                detail={"deploy_ids": ids},
            )
        standing = standing_rows[0] if standing_rows else None
        if standing is None:
            raise ApiError(
                400,
                f"--update requires a standing deploy for scenario "
                f"{plan.scenario_id!r}; none found — deploy it once "
                "without --update first",
            )
        # Pin plan_id into the DAG build (the safety property, work order
        # §2.4/§3): every surviving op keeps its exact op_id/vmid/name.
        pinned_plan = plan if standing.plan_id == plan.plan_id else _rebuild_plan_at(
            ir, manifests, ctx, plan_id=standing.plan_id, raw=raw,
            scenario_dir=scenario_dir, tier=tier,
        )
        diff = diff_plan(pinned_plan, store.ops_for_deploy(standing.deploy_id))
        # `--reapply` (opt-in) carves the ONE narrow exception out of the
        # Mutated refusal -- a `run-script` whose only differing input is
        # `script_content` (`docs/WORK_ORDER_responder_op_row_drift.md`
        # option A). Without the flag `reapply_ops` is empty, so `refused`
        # is all of `diff.mutated` and this is byte-for-byte the pre-existing
        # hard refusal.
        reapply_ops = reapplyable_ops(pinned_plan, diff) if reapply else frozenset()
        refused = diff.mutated - reapply_ops
        if refused:
            raise ApiError(
                409, _mutation_refusal_message(pinned_plan, diff, refused, reapply=reapply),
                detail=_mutation_refusal_detail(diff, refused),
            )
        if diff.removed and not allow_destroy:
            raise ApiError(
                409,
                f"--update would remove {len(diff.removed)} op(s); pass "
                "--allow-destroy to confirm",
                detail={"removed": sorted(diff.removed)},
            )
        return DeployResolution(
            plan=pinned_plan, existing=standing, diff=diff, reapply_ops=reapply_ops,
        )

    # Not `--update`: only auto-heal the EXACT-same-generation case (content
    # byte-identical to what's already standing, AND that standing row's
    # `plan_id` was genuinely pinned away from the fresh one just derived —
    # i.e. an earlier `--update` moved it). Anything else (genuinely edited
    # content, no `--update`) keeps today's documented dead-end behavior
    # (LES-126): mint a brand-new deploy_id/plan_id, a SEPARATE deploy
    # alongside the standing one. Silently redirecting an unflagged edit
    # onto the pinned plan_id would run it through NONE of the diff/refusal
    # logic above.
    #
    # An AMBIGUOUS store (>1 standing row) simply skips this narrow heal
    # (MUST-FIX 2, code review) — `standing` is `None` below and the
    # fall-through mints a fresh id exactly as it always has; resolving the
    # ambiguity is `--update`'s job (above), not this path's.
    standing = standing_rows[0] if len(standing_rows) == 1 else None
    #
    # The `standing.plan_id != plan.plan_id` clause (code review M4) is
    # load-bearing, not redundant with the `direct` miss above: `direct`
    # can ALSO miss when `plan.plan_id` matches a row that is `destroyed`
    # (`find_deploy_for_plan(..., exclude_destroyed=True)` deliberately
    # treats a destroyed top match as no-match, never reviving it) while an
    # OLDER, still-standing row happens to share the exact same plan_id
    # (produced by `deploy` → `--fresh` → `destroy` the newer one — both
    # deploys of UNEDITED content hash to the identical plan_id). Without
    # this clause, that older row's `ir_hash` trivially still equals the
    # fresh one just computed (nothing was ever edited), so the heal fired
    # and silently rebound onto it — reviving a deploy_id `--fresh`/destroy
    # had deliberately moved past, instead of minting the fresh id every
    # other unflagged-edit case gets.
    if (
        standing is not None
        and standing.plan_id != plan.plan_id
        and standing.ir_hash == plan.ir_hash
    ):
        healed_plan = _rebuild_plan_at(
            ir, manifests, ctx, plan_id=standing.plan_id, raw=raw,
            scenario_dir=scenario_dir, tier=tier,
        )
        return DeployResolution(plan=healed_plan, existing=standing)

    return DeployResolution(plan=plan, existing=direct)  # direct is None here


# A refused mutation's old/new values are rendered through `render_value(...,
# max_len=)` rather than a bare `repr` (`docs/WORK_ORDER_responder_op_row_
# drift.md`): `script_content` is now a routinely-diffed field, and a
# `run-script` mutation `--reapply` declines to cover would otherwise dump
# two entire shell scripts into stderr / an HTTP error body. Anything shorter
# than this renders exactly as it always did.
# Caveat: 160 is a LENGTH heuristic, not a redaction guarantee -- a
# `script_content` (or any other field) whose `repr` is `<= 160` chars still
# prints VERBATIM in the refusal, same as every other mutated field always
# has.
_REFUSAL_VALUE_MAX = 160


def _mutation_refusal_message(
    plan: Any, diff: Any, refused: Any = None, *, reapply: bool = False,
) -> str:
    """`refused` defaults to ALL of `diff.mutated` (the pre-`--reapply`
    behavior). With `--reapply` on, the caller passes the ids the re-apply
    predicate did NOT cover, and each gets an extra line saying WHY
    (`plandiff.reapply_ineligibility_reason` -- the same function the
    predicate itself is defined in terms of, so the stated reason is always
    the reason actually tested)."""
    from axrange.planner.plandiff import reapply_ineligibility_reason, render_value

    ids = sorted(diff.mutated if refused is None else refused)
    tail = (
        "--reapply covers ONLY a content-only `run-script` change "
        "(docs/WORK_ORDER_responder_op_row_drift.md option A); the op(s) "
        "below are not covered — revert the edit, or use a full redeploy "
        "(--fresh) instead:"
        if reapply else
        "docs/PLANNER.md §9.2's redo-rules table is not implemented — "
        "revert the edit, or use a full redeploy (--fresh) instead:"
    )
    lines = [f"--update refused: {len(ids)} op(s) mutated. {tail}"]
    for op_id in ids:
        node = plan.get(op_id)
        label = node.render() if node is not None else op_id
        fields = diff.mutations.get(op_id, {})
        for field, (old, new) in sorted(fields.items()):
            lines.append(
                f"  {label}: field {field!r} changed "
                f"{render_value(old, max_len=_REFUSAL_VALUE_MAX)} -> "
                f"{render_value(new, max_len=_REFUSAL_VALUE_MAX)}"
            )
        if reapply and node is not None:
            reason = reapply_ineligibility_reason(node.op_name, fields)
            if reason:
                lines.append(f"      not covered by --reapply: {reason}")
    return "\n".join(lines)


def _mutation_refusal_detail(diff: Any, refused: Any = None) -> dict[str, Any]:
    from axrange.planner.plandiff import render_value

    ids = set(diff.mutated if refused is None else refused)
    return {
        "mutated": sorted(ids),
        "mutations": {
            op_id: {
                field: [
                    render_value(old, max_len=_REFUSAL_VALUE_MAX),
                    render_value(new, max_len=_REFUSAL_VALUE_MAX),
                ]
                for field, (old, new) in fields.items()
            }
            for op_id, fields in diff.mutations.items()
            if op_id in ids
        },
    }


def _validated_plan_for_path(settings: ApiSettings, sp: Path, images_root: Path, *,
                             target_node: Optional[str] = None,
                             scenario_dir: Optional[str] = None):
    """Validate an already-resolved scenario file, then delegate to
    `build_plan_and_ctx`. Returns (plan, cfg, ctx, ir, raw, scenario_path,
    manifests).

    `scenario_dir` is where RELATIVE `script:`/asset paths resolve
    (`pass1_emit._read_script`). `None` keeps `build_plan_and_ctx`'s
    default, the IR file's own parent -- right for a scenario file, WRONG for
    a materialized twin config, whose IR sits in a tempdir; `_plan_materialized`
    passes the twin config's real directory instead (GAP-082 review, M2).

    Factored out of `_build_plan_and_ctx` (docs/WORK_ORDER_twin_configs.md's
    API-parity follow-on, `create_deploy`'s `config` support below) so a
    twin-config-materialized path can share the SAME validate+plan sequence
    a literal scenario path uses, instead of a second copy (LES-037) --
    `_build_plan_and_ctx` is now a thin wrapper over this for the ordinary
    scenario-id/path case.

    `manifests` (added `docs/WORK_ORDER_twin_incremental_update.md`) rides
    back so a caller that needs to REBUILD the plan under a different
    `plan_id` (an `--update`'s pin, or the same-generation resume heal --
    `resolve_deploy_plan`) can call `build_plan(...)` again without a second
    `_load_manifests` — `plan()`'s existing call site is unaffected by the
    wider tuple (it already discards everything but `plan` via `_`-prefixed
    names, and gains one more `_manifests` alongside them).
    """
    from axrange.validator.engine import validate_full

    manifests = _load_manifests(images_root)

    result = validate_full(str(sp), manifests=manifests)
    if result.exit_code != 0:
        raise ApiError(
            422,
            "validation failed; refusing to plan",
            detail=[
                {"rule_id": f.rule_id, "severity": f.severity.value, "message": f.message}
                for f in list(result.errors) + list(result.warnings)
            ],
        )

    r = build_plan_and_ctx(
        sp, manifests, adapter_config=settings.adapter_config,
        target_node=target_node, scenario_dir=scenario_dir,
    )
    return r.plan, r.cfg, r.ctx, r.ir, r.raw, sp, manifests


def _plan_materialized(
    settings: ApiSettings, sp: Path, images_root: Path, source_dir: Path, *,
    target_node: Optional[str] = None,
):
    """Validate + plan a MATERIALIZED twin-config/imprint IR against the twin
    config's REAL directory, then delete the tempfile. Returns
    `_validated_plan_for_path`'s tuple. The one planning step the `config`
    and `imprint` deploy sources and the imprint plan preview share.

    `source_dir` (GAP-082 review, M2): relative `install: [{script: ...}]`
    paths resolve against the twin config's own directory, never the
    tempdir its expansion landed in -- which is how every real cast bundle
    failed to plan ("cannot read script 'scripts/...'"): they all ship
    `scripts/`.

    The unlink (GAP-082 review, S3): the tempfile is read by the validator
    and the planner and by NOTHING after them. `build_plan_and_ctx` parses
    it once into the in-memory `ir`/`raw`; `resolve_deploy_plan` (resume,
    `--update`'s pinned rebuild, `--reapply`) replans from those objects plus
    `scenario_dir`; the executor is handed the in-memory `plan`; no op
    input, state row or runner argument carries the path. So it goes here,
    on success and failure alike -- every plan preview and every deploy left
    one behind before (13-56 KB each), the leak `_parse_ir_only` closed for
    the read path."""
    try:
        return _validated_plan_for_path(
            settings, sp, images_root, target_node=target_node,
            scenario_dir=str(source_dir),
        )
    finally:
        try:
            sp.unlink(missing_ok=True)
        except OSError:
            pass


def _build_plan_and_ctx(settings: ApiSettings, scenario: str, *,
                        target_node: Optional[str] = None):
    """Validate → delegate to `build_plan_and_ctx`. Returns
    (plan, cfg, ctx, ir, raw, scenario_path, manifests).
    """
    sp = _resolve_scenario_path(settings, scenario)
    images_root = _images_root_for(settings, sp)
    return _validated_plan_for_path(settings, sp, images_root, target_node=target_node)


def _resolve_config_scenario_path(
    settings: ApiSettings, config_id: str, params: Optional[dict[str, str]], *,
    warnings: Optional[list[str]] = None,
) -> tuple[Path, Path, Path]:
    """`config` (a twin-config id) -> a materialized IR file, its effective
    images root and the twin config's SOURCE directory -- the API peer of
    `cli.main._resolve_scenario_or_config` (`docs/WORK_ORDER_twin_configs.md`
    §2), reusing the SAME `materialize_twin_config` expander the CLI's `te
    deploy --config` calls (LES-037: one expansion implementation, not a
    second).

    `source_dir` (GAP-082 review, M2) is where the config's relative
    `install: [{script: ...}]` paths resolve: the directory of the EXACT file
    the expander read, symlinks resolved -- `registry.twin_config_source_dir`,
    the one helper the CLI's `--config` uses too. The materialized IR lands
    in a system tempdir, so planning against ITS parent resolved every script
    to `<tmp>/scripts/...` and failed (the CLI fixed that for itself; this
    path never had).

    `images_root` mirrors the CLI's own default-when-unset logic: the
    materialized IR always lands in a system tempdir (never co-located with
    a real `images/` catalog), so an explicit `settings.images_root` wins;
    absent that, fall back to the twin-configs catalog dir's parent (or
    cwd, for the built-in catalog) -- the natural place a repo-root
    invocation's co-located `images/` catalog would live.

    `warnings`, when given, collects expansion-time advisory findings --
    `expand_twin_config_raw`'s own out-parameter -- exactly like
    `cli.main._resolve_scenario_or_config`'s `config_warnings` list does:
    code-review follow-on (parity gap), since a caller who drops this
    silently loses them, unlike the CLI, which always prints them. Its
    producer is the DEPLOY GATE (`axrange.twinconfig.gate`,
    `docs/WORK_ORDER_deploy_gate.md` §4.5): effective thresholds, counts, and
    one line per gated-out node/segment, dropped conduit, pruned router leg
    and exempted router. (The original producer, an unknown-persona
    cross-check, was removed in 2026-08-04 with the `persona:` field it
    read.)
    """
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.expand import materialize_twin_config
    from axrange.twinconfig.registry import twin_config_source_dir

    catalog_dir = settings.twin_configs_dir or None
    try:
        # The source directory FIRST: it re-reads the same file raw, and a
        # failure between the two calls must not strand the tempfile
        # `materialize_twin_config` creates.
        source_dir = twin_config_source_dir(config_id, catalog_dir=catalog_dir)
        sp = materialize_twin_config(
            config_id, catalog_dir=catalog_dir, param_overrides=params or None,
            warnings=warnings,
        )
    except TwinConfigError as e:
        raise ApiError(400, f"config {config_id!r}: {e}")

    if settings.images_root:
        images_root = Path(settings.images_root)
    elif catalog_dir:
        images_root = Path(catalog_dir).resolve().parent
    else:
        images_root = Path.cwd()
    return sp, images_root, source_dir


def _resolve_imprint_scenario_path(
    settings: ApiSettings, imprint_id: str, params: Optional[dict[str, str]], *,
    warnings: Optional[list[str]] = None,
) -> tuple[Path, Path, Path]:
    """`imprint` (an imprint-catalog id) -> a materialized IR file, its
    effective images root and the twin config's SOURCE directory -- the
    imprint-sourced sibling of `_resolve_config_scenario_path` immediately
    above (`docs/WORK_ORDER_aximprint_console.md` PART A). Reuses the exact
    same `materialize_twin_config` expander and `twin_config_source_dir`
    helper (LES-037): an imprint's `<id>.yaml` is byte-for-byte a
    `twin_config` document, and `<fixtures_dir>/<id>/<id>.yaml` is the
    identical subfolder shape `axrange.twinconfig.registry` already
    discovers, so the only difference from an ordinary `config:` deploy is
    which catalog directory the id resolves against.

    The pre-check is `axrange.api.imprints.check_imprint_addressable`, the
    NARROW one (GAP-082 review, S2): the traversal guard, the 404 (no such
    imprint) vs 400 (a malformed twin config, or a `twin_config.id` that
    disagrees with the directory name, LES-195) split, and nothing else. A
    deploy reads the twin config and its scripts; it never reads the
    provenance sidecar or the topology export, so neither's health may block
    it -- until the review this ran the whole summary, and an undecodable
    sidecar refused a deploy that never opens it. A caller hitting
    deploy/plan directly, without `GET /api/imprints/{id}` first, still gets
    the established 404 shape rather than `materialize_twin_config`'s own
    `TwinConfigNotFoundError`, which `_resolve_config_scenario_path` folds
    into a flat 400.

    What the plan runs showed (GAP-082 review, M2): R7 lifted the summary's
    baked-id refusal, and every real cast bundle then failed one step later,
    at PLAN BUILD, because its relative `scripts/...` installs resolved
    against the tempdir the twin config was materialized into. With
    `source_dir` threaded through, `POST /api/imprints/{id}/plan` answers 200
    for `corpapcn-twin`, `corpapcn-golden` and `pit-395a61f04e-1780405200`
    (pinned by `tests/test_api_console.py`). A deploy shares every step up to
    the executor; a live "deploy as twin" of a cast bundle has not been run.

    `images_root` mirrors `_resolve_config_scenario_path`'s own
    default-when-unset logic, against `imprints_dir` in place of
    `twin_configs_dir`.
    """
    from axrange.api.imprints import (
        ImprintDataError,
        ImprintNotFoundError,
        check_imprint_addressable,
        default_fixtures_dir,
    )
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.expand import materialize_twin_config
    from axrange.twinconfig.registry import twin_config_source_dir

    catalog_dir = settings.imprints_dir or None
    fixtures_dir = Path(catalog_dir) if catalog_dir else default_fixtures_dir()

    try:
        check_imprint_addressable(imprint_id, fixtures_dir=fixtures_dir)
    except ImprintNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e

    try:
        source_dir = twin_config_source_dir(imprint_id, catalog_dir=fixtures_dir)
        sp = materialize_twin_config(
            imprint_id, catalog_dir=fixtures_dir, param_overrides=params or None,
            warnings=warnings,
        )
    except TwinConfigError as e:
        raise ApiError(400, f"imprint {imprint_id!r}: {e}") from e

    if settings.images_root:
        images_root = Path(settings.images_root)
    elif catalog_dir:
        images_root = Path(catalog_dir).resolve().parent
    else:
        images_root = Path.cwd()
    return sp, images_root, source_dir


def plan(settings: ApiSettings, scenario: Optional[str] = None, *,
         target_node: Optional[str] = None, config: Optional[str] = None,
         imprint: Optional[str] = None,
         params: Optional[dict[str, str]] = None) -> dtos.PlanSummaryDTO:
    """Pure preview (no state write, no lease). Fix round 2: a twin config or
    an imprint is planned exactly as `POST /api/deploys` would plan it, so its
    `plan_id` can be pinned with `expect_plan_id` (GAP-098)."""
    kind = _one_source(scenario, config, imprint, params)
    if kind == "imprint":
        return imprint_plan(settings, imprint, target_node=target_node, params=params)
    if kind == "scenario":
        p, _cfg, _ctx, _ir, _raw, _sp, _manifests = _build_plan_and_ctx(
            settings, scenario, target_node=target_node
        )
        return _plan_summary_dto(p)
    warnings: list[str] = []
    sp, images_root, source_dir = _resolve_config_scenario_path(
        settings, config, params, warnings=warnings,
    )
    p, _cfg, _ctx, _ir, _raw, _sp, _manifests = _plan_materialized(
        settings, sp, images_root, source_dir, target_node=target_node,
    )
    return _plan_summary_dto(
        p, config_warnings=warnings,
        params=_resolved_params(settings.twin_configs_dir or None, config, params),
    )


def _plan_summary_dto(p: Any, *, config_warnings: Optional[list[str]] = None,
                      params: Optional[dict[str, Any]] = None) -> dtos.PlanSummaryDTO:
    return dtos.PlanSummaryDTO(
        plan_id=p.plan_id,
        scenario_id=p.scenario_id,
        scenario_version=p.scenario_version,
        ir_hash=p.ir_hash,
        ops=len(p.ops),
        barriers=len(p.barriers),
        edges=len(p.edges),
        tree=p.render_tree(),
        config_warnings=list(config_warnings or []),
        vm_count=sum(1 for op in p.ops.values() if op.op_name == "clone-vm"),
        params=dict(params or {}),
    )


def _resolved_params(catalog_dir: Any, twin_id: str,
                     overrides: Optional[dict[str, str]]) -> dict[str, Any]:
    """The parameter values a plan of `twin_id` was built with: declared
    defaults + inline `${NAME:default}` defaults, then the caller's
    overrides (coerced like the expander coerces them)."""
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.params import resolve_params
    from axrange.twinconfig.registry import resolve_twin_config_path

    try:
        path = resolve_twin_config_path(twin_id, catalog_dir=catalog_dir)
    except TwinConfigError:
        return dict(overrides or {})
    base = {p["name"]: p["default"] for p in _twin_params(path)}
    try:
        return resolve_params(base, overrides)
    except TwinConfigError:
        return {**base, **(overrides or {})}


# ---------------------------------------------------------------------------
# Topology DTO — §4 (IR → bipartite graph, + live status overlay from state.db)
# ---------------------------------------------------------------------------
def _node_kind(node) -> str:
    k = node.kind
    if k is None:
        return "host"
    val = getattr(k, "value", k)
    return str(val)


def _parse_ir_only(settings: ApiSettings, scenario: str, *, source: Optional[str] = None):
    from axrange.ir.parser import parse_ir

    sp, is_temp = _resolve_read_ir_path(settings, scenario, source=source)
    try:
        return parse_ir(path=str(sp)).ir, sp
    finally:
        # S2, code review: `materialize_twin_config` (the twin-config/
        # imprint branches below) never deletes its own tempfile -- every
        # topology/node-detail read against a twin-config or imprint id
        # leaked one (13 KB, ~150ms to create) forever. A `scenario`-
        # sourced path is a REAL catalog file and must never be touched.
        if is_temp:
            try:
                sp.unlink(missing_ok=True)
            except OSError:
                pass


_VALID_TOPOLOGY_SOURCES = ("scenario", "twin-config", "imprint")


def _resolve_read_ir_path(
    settings: ApiSettings, ref: str, *, source: Optional[str] = None,
) -> tuple[Path, bool]:
    """Resolve `ref` to an IR file for a READ-ONLY graph parse (topology /
    node-detail), returning `(path, is_temp)` — `is_temp=True` marks a
    THROWAWAY materialized tempfile the caller must clean up (see
    `_parse_ir_only`), `False` a REAL catalog file (scenarios) that must
    never be deleted.

    Beyond the scenarios catalog / explicit path this ALSO resolves a
    TWIN-CONFIG id, then an IMPRINT id -- mirroring `create_deploy`'s
    `scenario`/`config`/`imprint` three-way source split (LES-196's own
    generalization note: "any new deploy source ... needs the identical
    fallback wired into `_resolve_read_ir_path`, or its own UI handoff will
    silently blank the canvas the same way").

    `source` (M1, code review, `docs/WORK_ORDER_console_selector_and_stale_
    deploys.md`): pins resolution to EXACTLY one catalog --
    `"scenario"`/`"twin-config"`/`"imprint"` -- skipping the chain-fallback
    below entirely. This exists because the id-only chain has a real, silent
    failure mode: `corp-enterprise` exists in BOTH the twin-configs and
    imprints catalogs, so the chain (twin-config tried before imprint)
    always resolved the SHIPPED twin-config's 37-node graph even when a
    caller specifically meant the imprint's 47-node one -- worse than a 404,
    since the wrong graph renders with no error at all, right next to a
    correct, imprint-sourced op-tree. Every NEW caller (the Console's
    `TwinTopologyScreen`, which already tracks which catalog an id came
    from) should pass `source` explicitly; it stays optional so every
    PRE-EXISTING caller (the plain scenario picker, `handleImprintDeployed`'s
    "deploy as twin" handoff) keeps today's back-compat chain behavior
    unchanged.

    Base-graph only, every branch: expansion happens WITHOUT param overrides
    and WITHOUT a provenance-sidecar load, so a canvas render never depends
    on sidecar health or a specific `--param` set (unlike the deploy paths,
    `_resolve_config_scenario_path`/`_resolve_imprint_scenario_path`, which
    take real params; the imprint one also runs the narrow addressing check,
    `imprints.check_imprint_addressable`).
    """
    if source is not None and source not in _VALID_TOPOLOGY_SOURCES:
        raise ApiError(
            400,
            f"unknown source {source!r}; expected one of {list(_VALID_TOPOLOGY_SOURCES)}",
        )

    if source == "scenario":
        return _resolve_scenario_path(settings, ref), False
    if source == "twin-config":
        return _materialize_twin_config_for_read(settings, ref), True
    if source == "imprint":
        return _materialize_imprint_for_read(settings, ref), True

    # No `source` pinned -- the original chain, unchanged for back-compat.
    # Only a scenarios 404 falls through to try twin-configs, and only a
    # twin-configs miss falls through to try imprints; a match in neither
    # catalog re-raises the ORIGINAL scenarios 404 (not a twin-config- or
    # imprint-specific one) -- the most informative miss for a ref that
    # isn't found anywhere.
    try:
        return _resolve_scenario_path(settings, ref), False
    except ApiError as scenario_miss:
        if scenario_miss.status != 404:
            raise
        try:
            return _materialize_twin_config_for_read(settings, ref), True
        except ApiError:
            pass
        try:
            return _materialize_imprint_for_read(settings, ref), True
        except ApiError:
            raise scenario_miss from None


def _materialize_twin_config_for_read(settings: ApiSettings, ref: str) -> Path:
    """The twin-config branch of `_resolve_read_ir_path`, factored out so
    the explicit-`source` and chain-fallback callers share ONE
    implementation. Raises `ApiError(404)` on any miss (unknown id,
    catalog error, or a path-traversal attempt in `ref` -- S8, code
    review)."""
    if not _is_safe_catalog_ref(ref):
        raise ApiError(404, f"twin-config not found: {ref}")
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.expand import materialize_twin_config

    twin_configs_catalog = settings.twin_configs_dir or None
    try:
        return materialize_twin_config(ref, catalog_dir=twin_configs_catalog)
    except TwinConfigError as e:
        raise ApiError(404, f"twin-config not found: {ref}: {e}") from e


def _materialize_imprint_for_read(settings: ApiSettings, ref: str) -> Path:
    """The imprint branch of `_resolve_read_ir_path` — see
    `_materialize_twin_config_for_read`'s docstring; same shape, the
    imprints catalog dir instead of the twin-configs one."""
    if not _is_safe_catalog_ref(ref):
        raise ApiError(404, f"imprint not found: {ref}")
    from axrange.api.imprints import default_fixtures_dir
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.expand import materialize_twin_config

    imprints_catalog = (
        Path(settings.imprints_dir) if settings.imprints_dir
        else default_fixtures_dir()
    )
    try:
        return materialize_twin_config(ref, catalog_dir=imprints_catalog)
    except TwinConfigError as e:
        raise ApiError(404, f"imprint not found: {ref}: {e}") from e


def topology(settings: ApiSettings, scenario: str, *,
             deploy_id: Optional[str] = None,
             source: Optional[str] = None) -> dtos.TopologyDTO:
    ir, _sp = _parse_ir_only(settings, scenario, source=source)
    scenario_id = (ir.scenario.id if ir.scenario else None) or scenario

    nodes: list[dtos.TopologyNodeDTO] = []
    edges: list[dtos.TopologyEdgeDTO] = []

    infra = ir.layers.infra if ir.layers else None
    networks = infra.networks if infra else []
    ir_nodes = infra.nodes if infra else []

    net_ids = set()
    for net in networks:
        if not net.id:
            continue
        net_ids.add(net.id)
        nodes.append(dtos.TopologyNodeDTO(
            id=net.id, type="network", cidr=net.cidr, vlan=net.vlan,
        ))

    # op-status overlay: map node id -> a representative op status from state.db
    status_by_node = _node_status_overlay(settings, scenario_id, deploy_id) \
        if deploy_id else {}

    for node in ir_nodes:
        if not node.id:
            continue
        nodes.append(dtos.TopologyNodeDTO(
            id=node.id,
            type=_node_kind(node),
            image=node.image,
            requires=node.requires,
            status=status_by_node.get(node.id),
        ))
        # edges: single `net` + multi-homed `nets`
        if node.net:
            edges.append(dtos.TopologyEdgeDTO(source=node.id, target=node.net))
        for nr in (node.nets or []):
            if nr.net:
                edges.append(dtos.TopologyEdgeDTO(
                    source=node.id, target=nr.net, ip=nr.ip
                ))

    return dtos.TopologyDTO(
        scenario_id=scenario_id, deploy_id=deploy_id, nodes=nodes, edges=edges
    )


def _node_status_overlay(settings: ApiSettings, scenario_id: str,
                         deploy_id: Optional[str]) -> dict[str, str]:
    """Best-effort node→status from a deploy's ops. The op scope carries the
    node id; we surface the 'worst' status per node (failed > running > pending
    > completed) so a node goes red on any failure, green only when all clear.
    """
    from axrange.planner import StateStore

    store = StateStore(settings.db_path)
    try:
        did = deploy_id
        if did == "latest":
            # The newest LIVE te deploy OF THIS SCENARIO -- never "the newest
            # row of any tier", which painted a tfc/tap row's op status onto
            # an unrelated scenario (Unified Console ruling §3.2 bug fix).
            did = _latest_live_te_for(store, scenario_id)
        if not did:
            return {}
        ops = store.ops_for_deploy(did)
    finally:
        store.close()

    rank = {
        "failed": 4, "halted-due-to-upstream": 3, "running": 2,
        "pending": 1, "completed": 0,
    }
    out: dict[str, str] = {}
    for op in ops:
        node_id = _op_node_id(op)
        if not node_id:
            continue
        cur = out.get(node_id)
        st = op.status.value
        if cur is None or rank.get(st, 0) > rank.get(cur, 0):
            out[node_id] = st
    return out


def _latest_live_te_for(store: Any, scenario_id: str) -> Optional[str]:
    """The newest live, non-tap-playout `te` deploy of `scenario_id`, or
    None -- `taps.resolve_target_deploy`'s name rule (one rule, LES-037),
    minus its "an existing deploy id passes through" branch: a scenario id
    is never a deploy id here."""
    from axrange.o2control.taps import is_tap_plan_deploy

    for row in store.deploys_matching(
        scenario_id=scenario_id, tier="te", exclude_destroyed=True,
    ):
        if not is_tap_plan_deploy(store, row):
            return row.deploy_id
    return None


def _op_node_id(op) -> Optional[str]:
    """Pull a node id out of an op's scope_json, if present."""
    try:
        scope = json.loads(op.scope_json) if op.scope_json else {}
    except (ValueError, TypeError):
        return None
    if not isinstance(scope, dict):
        return None
    for key in ("node", "host", "node_id"):
        v = scope.get(key)
        if isinstance(v, str) and v:
            return v
    return None


# ---------------------------------------------------------------------------
# Topology EXPORT (the aximprint cast-topology-export artifact, `docs/
# CHANGELOG.md` 2026-08-27) -- Logical (`segments`+`edges`) and Physical
# (`nodes`, the full observed census) both read THIS, never the twin-config/
# imprint's own `TopologyDTO` graph above, which is a lossy, edge-less
# projection (`docs/WORK_ORDER_console_selector_and_stale_deploys.md`'s
# decision record -- restoring the ORIGINAL Logical/Physical semantics after
# an earlier, incorrect substitution of the twin-config's own structure).
# ---------------------------------------------------------------------------
_VALID_EXPORT_SOURCES = ("twin-config", "imprint")


def topology_export(
    settings: ApiSettings, ref: str, *, source: Optional[str] = None,
) -> dtos.TopologyExportDTO:
    """`<ref>.topology.json`, shipped BESIDE the twin-config/imprint
    `<ref>.yaml` it casts from (`install_cast_as_twin.py`) -- resolution
    piggybacks on `axrange.twinconfig.registry.load_twin_config_raw`'s
    REAL (non-materialized) source path, since `materialize_twin_config`'s
    tempfile has no sibling anything. `source="scenario"` is refused (a raw
    scenario was never cast from a capture, so it can never have an
    export); absent `source`, twin-config is tried before imprint, mirroring
    `_resolve_read_ir_path`'s own chain order. 404 when nothing is found --
    both "unknown id" and "known id, but no export yet" (the ordinary case
    for almost every twin-config/imprint today) land here; the caller
    (`TwinTopologyScreen`) is expected to treat a 404 as "not applicable
    yet", never fall back to `TopologyDTO`.
    """
    from pydantic import ValidationError

    from axrange.api.imprints import (
        ImprintDataError,
        ImprintNotFoundError,
        check_imprint_addressable,
    )
    from axrange.api.topology_export import TopologyExportError, load_topology_export
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.registry import load_twin_config_raw

    if source is not None and source not in _VALID_EXPORT_SOURCES:
        raise ApiError(
            400,
            f"unknown source {source!r}; expected one of {list(_VALID_EXPORT_SOURCES)}",
        )
    if not _is_safe_catalog_ref(ref):
        raise ApiError(404, f"no topology-export for {ref!r}")

    catalogs: list[tuple[str, Any]] = []
    if source in (None, "twin-config"):
        catalogs.append(("twin-config", settings.twin_configs_dir or None))
    if source in (None, "imprint"):
        from axrange.api.imprints import default_fixtures_dir

        imprints_dir = (
            Path(settings.imprints_dir) if settings.imprints_dir
            else default_fixtures_dir()
        )
        catalogs.append(("imprint", imprints_dir))

    for kind, catalog_dir in catalogs:
        if kind == "imprint":
            # The imprint catalog's ADDRESSING guard -- the one every other
            # imprint route runs (GAP-082 review, C). The registry's
            # filename fast path below never compares `twin_config.id` with
            # the directory, so a renamed bundle's export was served 200
            # while its summary, cast, plan and deploy all refused it.
            try:
                bundle_dir = check_imprint_addressable(ref, fixtures_dir=catalog_dir)
            except ImprintNotFoundError:
                continue
            except ImprintDataError as e:
                raise ApiError(400, str(e)) from e
        else:
            try:
                _raw, real_path = load_twin_config_raw(ref, catalog_dir=catalog_dir)
            except TwinConfigError:
                continue
            bundle_dir = real_path.parent
        try:
            export = load_topology_export(ref, bundle_dir)
        except TopologyExportError as e:
            raise ApiError(400, str(e)) from e
        if export is None:
            # Resolved fine, but no sibling export file -- keep trying the
            # other catalog when `source` wasn't pinned; otherwise this IS
            # the answer (fall through to the 404 below).
            continue
        try:
            return _topology_export_dto(export)
        except ValidationError as e:
            # A nested value the parser passes through (a node's
            # `role_confidence`, `windows_seen`, ...) that the published
            # type rejects is producer-damaged content: a 400 naming it,
            # never a 500 (M1).
            raise ApiError(400, _dto_type_refusal(f"{ref}.topology.json", e)) from e

    raise ApiError(404, f"no topology-export for {ref!r}")


def _topology_export_dto(export: Any) -> dtos.TopologyExportDTO:
    return dtos.TopologyExportDTO(
        imprint_id=export.imprint_id,
        pit_id=export.pit_id,
        as_of=export.as_of,
        network=export.network,
        vantage=export.vantage,
        windows=export.windows,
        schema_version=export.schema_version,
        segments=[
            dtos.TopologyExportSegmentDTO(
                cidr=seg.cidr, name=seg.name, entities=seg.entities,
                coverage_source=seg.coverage_source,
            )
            for seg in export.segments
        ],
        nodes=[
            dtos.TopologyExportNodeDTO(
                id=n.id, address=n.address, locality=n.locality,
                segment=n.segment,
                segment_evidence_class=n.segment_evidence_class,
                role=n.role, role_confidence=n.role_confidence,
                role_evidence_class=n.role_evidence_class,
                windows_seen=n.windows_seen,
                listen_ports=n.listen_ports, dial_ports=n.dial_ports,
            )
            for n in export.nodes
        ],
        edges=[
            dtos.TopologyExportEdgeDTO(
                src=e.src, dst=e.dst, proto=e.proto, bytes=e.bytes,
                flows=e.flows, evidence_class=e.evidence_class,
            )
            for e in export.edges
        ],
        census=dtos.TopologyExportCensusDTO(
            entities_total=export.census.entities_total,
            with_role=export.census.with_role,
            placed=export.census.placed,
            local=export.census.local,
        ),
    )


def _dto_type_refusal(what: str, e: Any) -> str:
    """The 400's words for a value a reader passed through that its
    published DTO rejects: WHERE (the pydantic location) and WHAT (the
    rejected input), so the author is sent to the right field."""
    errors = e.errors() if hasattr(e, "errors") else []
    first = errors[0] if errors else {}
    where = ".".join(str(p) for p in first.get("loc", ())) or "?"
    return (
        f"{what}: {len(errors)} value(s) do not have their published type -- "
        f"first at `{where}`: {first.get('msg', e)} (got {first.get('input')!r})"
    )


# ---------------------------------------------------------------------------
# Node-detail DTO — §4.1 (config + state.db tiers; live tier is a follow-on)
# ---------------------------------------------------------------------------
def node_detail(settings: ApiSettings, scenario: str, node_id: str, *,
                deploy_id: Optional[str] = None,
                live: bool = False,
                source: Optional[str] = None) -> dtos.NodeDetailDTO:
    ir, _sp = _parse_ir_only(settings, scenario, source=source)
    infra = ir.layers.infra if ir.layers else None
    node = None
    for n in (infra.nodes if infra else []):
        if n.id == node_id:
            node = n
            break
    if node is None:
        raise ApiError(404, f"node not found: {node_id}")

    nets: list[dtos.TopologyEdgeDTO] = []
    if node.net:
        nets.append(dtos.TopologyEdgeDTO(source=node.id, target=node.net))
    for nr in (node.nets or []):
        if nr.net:
            nets.append(dtos.TopologyEdgeDTO(source=node.id, target=nr.net, ip=nr.ip))

    resources = None
    if node.resources is not None:
        resources = {
            "cpu": node.resources.cpu,
            "ram": node.resources.ram,
            "disk": node.resources.disk,
        }

    # declared apps/scripts/activities/overlays for this node, from bindings + overlays
    declared = _declared_for_node(ir, node_id)

    # state.db tier: placement + applied? statuses
    placement = None
    vmid = None
    op_status_by_id = {}
    scenario_id = (ir.scenario.id if ir.scenario else None) or scenario
    did = deploy_id
    try:
        from axrange.planner import StateStore

        store = StateStore(settings.db_path)
        try:
            if did == "latest" or did is None:
                # Scenario-scoped newest live te deploy (ruling §3.2 bug fix;
                # see `_node_status_overlay`).
                did = _latest_live_te_for(store, scenario_id)
            if did:
                d = store.get_deploy(did)
                if d is not None:
                    try:
                        pm = json.loads(d.placement_json) if d.placement_json else {}
                        placement = pm.get(node_id)
                    except (ValueError, TypeError):
                        placement = None
                ops = store.ops_for_deploy(did)
                for op in ops:
                    if _op_node_id(op) == node_id:
                        op_status_by_id[op.op_name] = op.status.value
                        # Single canonical identifier (code-review finding #8,
                        # 2026-07-07): match on `op_name` alone — the same
                        # convention `planner/teardown.py` already uses to find
                        # a VM's vmid from recorded op outputs. The prior tuple
                        # mixed a made-up catalog id ("OP-VM-010", which doesn't
                        # exist — the real one is OP-HOST-010) with the op name,
                        # so the catalog-id half never matched anything.
                        if op.op_name == "clone-vm" and op.outputs_json:
                            try:
                                outs = json.loads(op.outputs_json)
                                vmid = vmid or (str(outs.get("vmid")) if outs.get("vmid") else None)
                            except (ValueError, TypeError):
                                pass
        finally:
            store.close()
    except Exception:  # noqa: BLE001
        pass

    # annotate declared entries with applied status (best-effort name match)
    for d_entry in declared:
        for op_name, st in op_status_by_id.items():
            if d_entry.id and d_entry.id in op_name:
                d_entry.applied = st
                break

    live_fields = None
    if live:
        # Tier 3 (live, QGA/Proxmox) is a documented follow-on (§4.1). We return
        # an explicit marker rather than silently pretending.
        live_fields = {"available": False, "note": "live tier (QGA/Proxmox) not implemented in this slice"}

    return dtos.NodeDetailDTO(
        id=node.id,
        kind=_node_kind(node),
        image=node.image,
        requires=node.requires,
        networks=nets,
        resources=resources,
        placement=placement,
        vmid=vmid,
        declared=declared,
        live=live_fields,
    )


def _declared_for_node(ir, node_id: str) -> list[dtos.NodeAppliedDTO]:
    out: list[dtos.NodeAppliedDTO] = []
    for b in (ir.bindings or []):
        if b.host == node_id and b.app:
            out.append(dtos.NodeAppliedDTO(kind="app", id=b.app))
        if b.host == node_id and b.activity:
            out.append(dtos.NodeAppliedDTO(kind="activity", id=b.activity))
        if b.targets and node_id in b.targets and b.activity:
            out.append(dtos.NodeAppliedDTO(kind="activity", id=b.activity))
    ov = ir.overlays
    if ov:
        for d in (ov.data or []):
            if d.host == node_id and d.id:
                out.append(dtos.NodeAppliedDTO(kind="overlay-data", id=d.id))
        for de in (ov.defense or []):
            hosts = list(de.installed_on or [])
            if de.host == node_id or node_id in hosts:
                if de.id:
                    out.append(dtos.NodeAppliedDTO(kind="overlay-defense", id=de.id))
    return out


# ---------------------------------------------------------------------------
# Deploys (read) — §3
# ---------------------------------------------------------------------------
def _deploy_summary(d, *, store: Any = None, target: Optional[str] = None,
                    ) -> dtos.DeploySummaryDTO:
    """`store` (open) lets the DISPLAY tier tell a tap playout (stored as
    `te`) from a twin; without it the stored tier is reported."""
    tier = d.tier
    if store is not None:
        from axrange.o2control.cascade import display_tier

        tier = display_tier(store, d)
    return dtos.DeploySummaryDTO(
        deploy_id=d.deploy_id,
        scenario_id=d.scenario_id,
        scenario_version=d.scenario_version,
        status=d.status.value,
        started_at=d.started_at,
        updated_at=d.updated_at,
        completed_at=d.completed_at,
        tier=tier,
        plan_id=d.plan_id,
        target=target,
    )


def _target_of(settings: ApiSettings) -> Any:
    """The `targets.Target` `settings` points at (matched by db path against
    EVERY store of each target -- fix round 2)."""
    from axrange.api.targets import resolve_targets

    targets = resolve_targets(settings)
    for t in targets:
        if settings.db_path in t.stores:
            return t
    return next((t for t in targets if t.default), targets[0])


def _store_label(settings: ApiSettings) -> str:
    from axrange.api.targets import store_labels

    return store_labels(_target_of(settings)).get(
        settings.db_path, Path(settings.db_path).name,
    )


def _in_store(settings: ApiSettings, path: str) -> ApiSettings:
    import dataclasses

    return settings if path == settings.db_path else dataclasses.replace(settings, db_path=path)


def _existing_stores(settings: ApiSettings) -> list[tuple[str, str]]:
    """`[(path, label)]` of the target's stores that exist (a read never
    creates one), primary first."""
    from axrange.api.targets import store_labels

    t = _target_of(settings)
    labels = store_labels(t)
    return [(p, labels[p]) for p in t.stores if Path(p).is_file()]


def _ambiguous(settings: ApiSettings, what: str, hits: list[str]) -> ApiError:
    from axrange.api.targets import store_labels

    labels = store_labels(_target_of(settings))
    names = [labels.get(h, h) for h in hits]
    return ApiError(
        409,
        f"{what} is recorded in {len(hits)} stores of target "
        f"{_target_name(settings)!r}: {', '.join(names)} -- refusing to pick one",
        detail={"code": "ambiguous-store", "stores": names},
    )


def _route_to_owning_store(settings: ApiSettings, scenario_id: str, plan_id: str) -> ApiSettings:
    """`settings` aimed at the store of this target that holds a
    NON-destroyed deploy with this `scenario_id` or `plan_id`. None -> the
    primary (a genuinely new deploy); hits in two stores -> 409."""

    stores = _existing_stores(settings)
    if not any(p != settings.db_path for p, _l in stores):
        return settings  # only the primary (or nothing) exists: nothing to route
    hits: list[str] = []
    for path, _label, st in _open_stores(stores):
        try:
            if st.deploys_matching(scenario_id=scenario_id, exclude_destroyed=True, limit=1) or (
                st.find_deploy_for_plan(plan_id, exclude_destroyed=True) is not None
            ):
                hits.append(path)
        finally:
            st.close()
    if len(hits) > 1:
        raise _ambiguous(settings, f"scenario {scenario_id!r}", hits)
    return _in_store(settings, hits[0]) if hits else settings


def _open_stores(stores: list[tuple[str, str]], warnings: Optional[list[str]] = None):
    """Yield `(path, label, read-only StateStore)` for each store that opens;
    a broken one is SKIPPED with a warning (S1: never a 500 for the whole
    request because one attached store is unreadable). The caller closes."""
    from axrange.planner import StateStore

    for path, label in stores:
        try:
            st = StateStore(path, readonly=True)
        except Exception as e:  # noqa: BLE001 -- reported, never fatal
            msg = f"store {label} ({path}) is unreadable and was skipped: {e}"
            logger.warning("%s", msg)
            if warnings is not None:
                warnings.append(msg)
            continue
        yield path, label, st


def locate(settings: ApiSettings, *, deploy_id: Optional[str] = None,
           twin: Optional[str] = None) -> ApiSettings:
    """`settings` aimed at the store of this target that holds `deploy_id`
    (or, failing that, the twin `twin` names -- a deploy id or twin name),
    so the unchanged service/engine code reads -- and writes, and takes its
    lease and sibling lookups -- in THAT store (fix round 2). No hit ->
    `settings` unchanged (the primary; the route's own 404 follows). A hit
    in two stores is a 409 naming both, never a silent pick."""
    from axrange.o2control.taps import TapPlanError, resolve_target_deploy

    stores = _existing_stores(settings)
    if not (deploy_id or twin) or not any(p != settings.db_path for p, _l in stores):
        return settings
    if deploy_id and deploy_id != "latest":
        hits = []
        for path, _label, st in _open_stores(stores):
            try:
                if st.get_deploy(deploy_id) is not None:
                    hits.append(path)
            finally:
                st.close()
        if len(hits) > 1:
            raise _ambiguous(settings, f"deploy {deploy_id}", hits)
        if hits:
            return _in_store(settings, hits[0])
    if twin:
        hits = []
        for path, _label, st in _open_stores(stores):
            try:
                try:
                    resolve_target_deploy(st, twin)
                    hits.append(path)
                except TapPlanError:
                    pass
            finally:
                st.close()
        if len(hits) > 1:
            raise _ambiguous(settings, f"twin {twin!r}", hits)
        if hits:
            return _in_store(settings, hits[0])
    return settings


def _protected_ids(settings: ApiSettings) -> set[str]:
    return set(_target_of(settings).protected_deploys)


def _check_protection(settings: ApiSettings, deploy_id: str,
                      override: Optional[str], *, also: Optional[str] = None) -> None:
    """423 when `deploy_id` (a twin) -- or `also`, the id the request named
    -- is protected on this target and the request did not name it in
    `override_protection` (server-side protection, fix round 1)."""
    prot = _protected_ids(settings)
    for did in dict.fromkeys(x for x in (deploy_id, also) if x):
        if did in prot and override != did:
            raise ApiError(
                423,
                f"deploy {did} is protected on target {_target_name(settings)!r}: API "
                f"writes to it or to its tap/tfc/exr deploys are refused. Pass "
                f"override_protection={did} to do it anyway.",
                detail={"code": "protected", "deploy_id": did},
            )


def guard_protected(
    settings: ApiSettings, *, deploy_id: Optional[str] = None,
    twin: Optional[str] = None, override: Optional[str] = None,
) -> None:
    """Refuse (423) a write aimed at a protected twin or any of its sibling
    tap/tfc/exr deploys. `deploy_id` is any deploy id (a sibling resolves to
    its twin); `twin` is a te deploy id or twin NAME (the tier routes'
    `target`). Unresolvable ids are left to the route's own 404."""
    if not _protected_ids(settings) or not Path(settings.db_path).is_file():
        return
    from axrange.o2control.taps import TapPlanError, resolve_target_deploy
    from axrange.planner import StateStore

    store = StateStore(settings.db_path)
    try:
        checks: list[tuple[str, Optional[str]]] = []
        if deploy_id:
            d = store.get_deploy(deploy_id)
            checks.append((_parent_te_id(store, d) if d is not None else deploy_id, deploy_id))
        if twin:
            try:
                checks.append((resolve_target_deploy(store, twin), twin))
            except TapPlanError:
                checks.append((twin, None))
    finally:
        store.close()
    for te_id, named in checks:
        _check_protection(settings, te_id, override, also=named)


def _target_name(settings: ApiSettings) -> str:
    """The name of the target `settings` points at (matched by db path --
    `targets.settings_for_target` swaps exactly that), else the default's."""
    from axrange.api.targets import resolve_targets

    targets = resolve_targets(settings)
    for t in targets:
        if settings.db_path in t.stores:
            return t.name
    return next((t.name for t in targets if t.default), targets[0].name)


def list_deploys(
    settings: ApiSettings, limit: int = 50, status: Optional[str] = None,
) -> dtos.DeployListDTO:
    """`docs/WORK_ORDER_console_selector_and_stale_deploys.md` (defect 2):
    a torn-down deploy's cluster resources no longer exist, so the DEFAULT
    (no `status` given) excludes `destroyed` -- an unfiltered Console shows
    only live deploys, matching every OTHER "latest"/bare-default read path
    in this codebase (`te status`/`te info`/`o2 tap`'s bare-defaults) that
    already treats a terminal row as "not the answer" rather than noise the
    caller has to filter out itself (LES-084). `list-deploys` (the CLI) and
    `?status=destroyed`/`?status=all` here keep the full history reachable
    -- the state store itself is never touched; this filters what one READ
    returns, nothing more.

    `status`: one of `DeployStatus`'s values (exactly that status), or the
    sentinel `"all"` (no filtering at all -- the same rows `list-deploys`
    shows). Filtering happens in SQL via `deploys_matching` (`limit` is
    applied AFTER the filter, not before -- a destroyed row's `updated_at`
    advances every time one is torn down, so filtering post-`LIMIT` would
    silently under-return whenever recent destroys crowd the most-recent
    page).
    """
    from axrange.planner.state import DeployStatus

    valid = {s.value for s in DeployStatus}
    if status is not None and status != "all" and status not in valid:
        raise ApiError(
            400,
            f"unknown status {status!r}; expected 'all' or one of {sorted(valid)}",
        )

    target = _target_name(settings)
    # Every store of the target, merged newest-first (fix round 2); the
    # primary alone when none exists yet (an empty read, never a created file).
    stores = _existing_stores(settings) or [(settings.db_path, _store_label(settings))]
    rows: list[tuple[float, dtos.DeploySummaryDTO]] = []
    warnings: list[str] = []
    for path, label, store in _open_stores(stores, warnings):
        try:
            if status is None:
                deploys = store.deploys_matching(exclude_destroyed=True, limit=limit)
            elif status == "all":
                deploys = store.deploys_matching(limit=limit)
            else:
                deploys = store.deploys_matching(status=status, limit=limit)
            for d in deploys:
                dto = _deploy_summary(d, store=store, target=target)
                dto.store = label
                rows.append((d.updated_at or d.started_at or 0.0, dto))
        finally:
            store.close()
    rows.sort(key=lambda r: r[0], reverse=True)
    return dtos.DeployListDTO(deploys=[dto for _t, dto in rows[:limit]], warnings=warnings)


def deploy_detail(settings: ApiSettings, deploy_id: str) -> dtos.DeployDetailDTO:
    from axrange.planner import StateStore

    store = StateStore(settings.db_path)
    try:
        did = deploy_id
        if did == "latest":
            latest = store.latest_deploys(limit=1)
            if not latest:
                raise ApiError(404, "no deploys recorded")
            did = latest[0].deploy_id
        d = store.get_deploy(did)
        if d is None:
            raise ApiError(404, f"deploy not found: {deploy_id}")
        ops = store.ops_for_deploy(did)
        op_dtos: list[dtos.OpDTO] = []
        counts: dict[str, int] = {}
        for op in ops:
            counts[op.status.value] = counts.get(op.status.value, 0) + 1
            attempts = store.attempts_for_op(did, op.op_id)
            op_dtos.append(dtos.OpDTO(
                op_id=op.op_id,
                op_name=op.op_name,
                op_catalog_id=op.op_catalog_id,
                kind=op.kind,
                status=op.status.value,
                started_at=op.started_at,
                completed_at=op.completed_at,
                attempts=[
                    dtos.OpAttemptDTO(
                        attempt_id=a.attempt_id,
                        started_at=a.started_at,
                        ended_at=a.ended_at,
                        result=a.result.value if a.result else None,
                        error_class=a.error_class,
                        error_message=a.error_message,
                    )
                    for a in attempts
                ],
            ))
        try:
            placement = json.loads(d.placement_json) if d.placement_json else {}
        except (ValueError, TypeError):
            placement = {}
        summary = _deploy_summary(d, store=store, target=_target_name(settings))
        summary.store = _store_label(settings)
        host_edits: list[dict[str, Any]] = []
        if (d.tier or "") == "tfc":
            from axrange.o2control.traffic import host_edit_jobs

            host_edits = host_edit_jobs(store, did)
    finally:
        store.close()
    return dtos.DeployDetailDTO(
        host_edits=host_edits,
        deploy=summary,
        plan_id=d.plan_id,
        placement=placement,
        ops=op_dtos,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Twin inventory (`docs/WORK_ORDER_tier_split.md` review SHOULD-FIX #10) — an
# API peer of `axrange te inventory` (`cli/main.py::cmd_te_inventory`), NOT a
# second implementation: both call `axrange.planner.inventory.
# build_twin_inventory` and map its result to DTOs, same as every other
# service function here maps engine objects to `dtos.py` shapes.
# ---------------------------------------------------------------------------
def deploy_inventory(settings: ApiSettings, deploy_id: str) -> dtos.TwinInventoryDTO:
    from axrange.planner import StateStore
    from axrange.o2control.traffic import effective_roles_for_twin
    from axrange.planner.inventory import InventoryError, build_twin_inventory

    # `--images`/manifests is best-effort here too (CLI parity, `cmd_te_
    # inventory`'s docstring): `os` just stays `None` per host if manifest
    # loading fails or no images_root is configured — never a hard failure,
    # and never a fabricated default.
    manifests = None
    if settings.images_root:
        try:
            manifests = _load_manifests(Path(settings.images_root))
        except ApiError:
            manifests = None

    store = StateStore(settings.db_path)
    try:
        did = deploy_id
        if did == "latest":
            latest = store.latest_deploys(limit=1)
            if not latest:
                raise ApiError(404, "no deploys recorded")
            did = latest[0].deploy_id
        try:
            # Effective roles: a tfc-assigned role wins over the twin's own
            # baseline (`docs/WORK_ORDER_role_persona_tier.md` §4.2). Looked up
            # HERE, not inside the engine -- `planner.inventory` must not import
            # `o2control` (LES-025), so the tier knowledge lives on this side.
            inv = build_twin_inventory(
                store, did, manifests=manifests,
                role_overrides=effective_roles_for_twin(store, did),
            )
        except InventoryError as e:
            raise ApiError(404, str(e))
    finally:
        store.close()

    return dtos.TwinInventoryDTO(
        deploy_id=inv.deploy_id,
        deploy_status=inv.deploy_status,
        hosts=[
            dtos.HostInventoryDTO(
                host_id=h.host_id,
                vm_name=h.vm_name,
                vmid=h.vmid,
                pve_node=h.pve_node,
                vm_status=h.vm_status,
                role=h.role,
                role_baseline=h.role_baseline,
                kind=h.kind,
                nets=[
                    dtos.HostNetDTO(
                        net_id=n.net_id, ip=n.ip, nic_index=n.nic_index,
                        external=n.external,
                    )
                    for n in h.nets
                ],
                os=h.os,
                listen=(
                    [dtos.HostListenDTO(port=li.port, proto=li.proto) for li in h.listen]
                    if h.listen is not None else None
                ),
            )
            for h in inv.hosts
        ],
        net_bridge=dict(inv.net_bridge),
        vlan_underlay=inv.vlan_underlay,
        # GAP-101 -- mapped from the ONE `EgressPosture.to_dict()` shape the
        # CLI's `--json` also publishes, so the two can never diverge.
        egress=(
            dtos.EgressPostureDTO(**inv.egress.to_dict())
            if inv.egress is not None else None
        ),
    )


# ---------------------------------------------------------------------------
# Fidelity scorecard (`docs/WORK_ORDER_fidelity_scorecard.md` §4.2) — an API
# peer of `axrange te scorecard` (`cli/main.py::cmd_te_scorecard`), NOT a
# second implementation: both call `axrange.fidelity.runner.
# scorecard_for_deploy` and render its ONE `Scorecard.to_dict()` (LES-037 —
# the `te inventory`-vs-endpoint divergence `planner/inventory.py` records as
# a past review finding must not be repeated here).
#
# `?probe=false` serves the artifact-only rows without touching a guest.
# Probing is OPT-IN on the API and default-on in the CLI: an HTTP GET that
# quietly writes a temporary file into 30 twin guests is a surprise, while an
# operator typing the verb has asked for it.
# ---------------------------------------------------------------------------
def deploy_scorecard(
    settings: ApiSettings, deploy_id: str, *, probe: bool = False,
    include_deferred: bool = False, reference_dir: Optional[str] = None,
) -> dtos.ScorecardDTO:
    from axrange.fidelity import ReferenceError
    from axrange.fidelity.runner import ProbeRunError, scorecard_for_deploy
    from axrange.o2control.traffic import effective_roles_for_twin
    from axrange.planner import StateStore
    from axrange.planner.inventory import InventoryError

    # Manifests are best-effort here too (CLI parity): `os` stays `None` per
    # host if loading fails, never a hard failure and never a fabricated
    # default.
    manifests = None
    if settings.images_root:
        try:
            manifests = _load_manifests(Path(settings.images_root))
        except ApiError:
            manifests = None

    store = StateStore(settings.db_path)
    try:
        did = deploy_id
        if did == "latest":
            latest = store.latest_deploys(limit=1)
            if not latest:
                raise ApiError(404, "no deploys recorded")
            did = latest[0].deploy_id
        try:
            card = scorecard_for_deploy(
                store, did,
                reference_dir=reference_dir,
                manifests=manifests,
                role_overrides=effective_roles_for_twin(store, did),
                probe=probe,
                include_deferred=include_deferred,
                adapter_config=settings.adapter_config,
            )
        except InventoryError as e:
            raise ApiError(404, str(e))
        except (ReferenceError, ProbeRunError) as e:
            raise ApiError(400, str(e))
    finally:
        store.close()

    # `model_validate` over the SAME dict the CLI prints, rather than a
    # hand-mapped field list: a new `measured` key added in `rows.py` reaches
    # both surfaces at once instead of only one.
    return dtos.ScorecardDTO.model_validate(card.to_dict())


# ---------------------------------------------------------------------------
# Targets (multi-PVE picker) — §3, redacted
# ---------------------------------------------------------------------------
def list_targets(settings: ApiSettings) -> dtos.TargetListDTO:
    """Every cluster target this process serves (Unified Console ruling
    §3.1, `axrange.api.targets`). A target whose adapter.conf fails to load
    is listed with `error`, never dropped.

    Back-compat for the implicit single target (no targets file): the old
    shape is kept exactly -- an unloadable adapter.conf is a 400 and an
    unconfigured one (no node, no API URL) is an empty list -- so the
    Grafana plugin's reading of this route does not change."""
    from axrange.api.targets import resolve_targets, store_labels, target_facts

    out: list[dtos.TargetDTO] = []
    for t in resolve_targets(settings):
        f = target_facts(t)
        if t.implicit:
            if f.error:
                raise ApiError(400, f.error)
            if not f.node and not f.api_url:
                return dtos.TargetListDTO(targets=[])
        out.append(dtos.TargetDTO(
            node=f.node or ("(unset)" if not f.error else ""),
            api_url=f.api_url, verify_tls=f.verify_tls,
            has_token=f.has_token, has_ssh_key=f.has_ssh_key,
            name=t.name, default=t.default, nodes=list(f.known_nodes),
            db_path=t.db_path, error=f.error,
            stores=[
                {"label": lbl, "path": p, "primary": p == t.db_path}
                for p, lbl in store_labels(t).items()
            ],
        ))
    return dtos.TargetListDTO(targets=out)


# ---------------------------------------------------------------------------
# Deploy (write, async) — §5. Rides the existing lease/state machinery.
# The actual executor run is delegated to a `DeployRunner` so tests can inject a
# fake adapter (no Proxmox needed). Production uses ProxmoxAdapter in a thread.
# ---------------------------------------------------------------------------
# (settings, plan, cfg, ctx, deploy_id) — code-review finding #6 (2026-07-07)
# dropped the trailing `is_resume`/`_unused` params: `create_deploy` above
# already handles resume (existing-row reset) before the runner ever starts,
# so the runner never consumed either value.
DeployRunner = Callable[[ApiSettings, Any, Any, Any, str], None]


def create_deploy(settings: ApiSettings, req: dtos.CreateDeployRequest, *,
                  runner: Optional[DeployRunner] = None,
                  background: Callable[[Callable[[], None]], None] | None = None,
                  override_protection: Optional[str] = None,
                  ) -> dtos.CreateDeployResponse:
    """Kick a deploy. Builds the plan, acquires the per-scenario lease (409 on
    contention), records the deploy row, then runs the executor in the
    background (default: a daemon thread). Returns the deploy_id immediately.

    `runner` defaults to the real Proxmox executor; tests pass a fake.
    `background` defaults to a daemon thread; tests pass a synchronous runner.

    `req.scenario` / `req.config` / `req.imprint` are mutually exclusive
    (`docs/WORK_ORDER_twin_configs.md` §2's `te deploy --config` CLI parity,
    plus `docs/WORK_ORDER_aximprint_console.md` PART A's "deploy as twin"
    action, mirroring `cli.main._resolve_scenario_or_config`'s own
    both/neither 400s generalized to three sources) -- `config`/`imprint`
    are each expanded through the SAME `materialize_twin_config` expander
    (`_resolve_config_scenario_path` / `_resolve_imprint_scenario_path`,
    LES-037 -- an imprint fixture IS a twin_config document, just resolved
    against the imprints catalog dir instead of the twin-configs one), then
    handed to the SAME validate+plan sequence a literal scenario uses
    (`_validated_plan_for_path`). Any expansion-time advisory finding (e.g.
    an unknown persona id) rides back on `CreateDeployResponse.
    config_warnings` -- code-review follow-on (CLI/API parity gap): the CLI
    always collects + prints these (`config_warnings` in `cli.main.
    _resolve_scenario_or_config`); this endpoint used to silently drop them.
    Always `[]` on the `scenario` path (nothing to collect).

    `imprint_deploy` (below) is a thin wrapper over THIS function -- there is
    no second lease/resume/`--update`/executor-dispatch implementation for
    the imprint source; only WHICH file gets materialized differs.
    """
    from axrange.planner import StateStore
    from axrange.planner.state import DeployStatus, default_process_info

    has_scenario = bool(req.scenario)
    has_config = bool(req.config)
    has_imprint = bool(req.imprint)
    if sum((has_scenario, has_config, has_imprint)) > 1:
        raise ApiError(400, "specify exactly one of 'scenario', 'config', or 'imprint'")
    if not (has_scenario or has_config or has_imprint):
        raise ApiError(400, "must specify one of 'scenario', 'config', or 'imprint'")
    if req.params and not (has_config or has_imprint):
        raise ApiError(400, "'params' only makes sense with 'config' or 'imprint'")

    if req.update and req.fresh:
        raise ApiError(400, "'update' and 'fresh' are mutually exclusive")

    config_warnings: list[str] = []
    if has_scenario:
        plan, cfg, ctx, ir, raw, sp, manifests = _build_plan_and_ctx(
            settings, req.scenario, target_node=req.target_node
        )
        # A scenario is a REAL catalog file: its relative paths resolve
        # beside it, exactly as `build_plan_and_ctx` already assumed.
        source_dir = sp.parent
    elif has_config:
        sp, images_root, source_dir = _resolve_config_scenario_path(
            settings, req.config, req.params, warnings=config_warnings,
        )
        for w in config_warnings:
            logger.warning("config %r: %s", req.config, w)
        # Plans against the twin config's own directory and deletes the
        # materialized tempfile -- nothing below reads it (S3).
        plan, cfg, ctx, ir, raw, sp, manifests = _plan_materialized(
            settings, sp, images_root, source_dir, target_node=req.target_node
        )
        # `twin.yaml cluster:` is finally consumed (ruling §3.1): deploying a
        # twin somewhere other than the cluster it names is allowed, and said.
        mismatch = _twin_cluster_mismatch(settings, req.config, cfg, req.target_node)
        if mismatch:
            config_warnings.append(mismatch)
            logger.warning("config %r: %s", req.config, mismatch)
    else:
        sp, images_root, source_dir = _resolve_imprint_scenario_path(
            settings, req.imprint, req.params, warnings=config_warnings,
        )
        for w in config_warnings:
            logger.warning("imprint %r: %s", req.imprint, w)
        plan, cfg, ctx, ir, raw, sp, manifests = _plan_materialized(
            settings, sp, images_root, source_dir, target_node=req.target_node
        )

    if req.expect_plan_id and plan.plan_id != req.expect_plan_id:
        # GAP-098: the plan the operator previewed is pinned -- a re-plan
        # that comes out different (the bundle changed on disk in between)
        # is refused rather than deployed.
        raise ApiError(
            409,
            f"the plan changed since it was previewed (previewed {req.expect_plan_id}, "
            f"now {plan.plan_id}) -- preview again before deploying",
            detail={"code": "plan-changed", "plan_id": plan.plan_id,
                    "expected": req.expect_plan_id},
        )

    # A deploy of a scenario that is ALREADY standing in another store of
    # this target runs THERE -- resume, `--update`, the lease and protection
    # included (fix round 3, M2). Minting a second row in the primary would
    # adopt the standing twin's VMs through the deterministic vmid/name
    # formulas and a later DELETE would destroy them (LES-156 recurrence).
    settings = _route_to_owning_store(settings, plan.scenario_id, plan.plan_id)

    store = StateStore(settings.db_path)
    try:
        # `resolve_deploy_plan` (docs/WORK_ORDER_twin_incremental_update.md)
        # replaces the plain `find_deploy_for_plan` lookup this used to be —
        # it ALSO handles ordinary resume/`--fresh` (unchanged behavior for
        # every pre-existing caller) alongside `--update`'s resolve-by-
        # scenario_id/pin/diff/refuse sequence. Raises `ApiError` for every
        # `--update` refusal (mutation / removal-without-allow_destroy / no
        # standing deploy), which propagates out of this function exactly
        # like every other 4xx here.
        # `source_dir`, never `sp.parent`: for a `config`/`imprint` source
        # `sp` is the (already deleted) tempfile, and an `--update`'s pinned
        # rebuild re-reads every relative script from here (M2).
        resolution = resolve_deploy_plan(
            store, plan, ir=ir, raw=raw, manifests=manifests, ctx=ctx,
            scenario_dir=str(source_dir), tier="te",
            fresh=req.fresh, update=req.update, allow_destroy=req.allow_destroy,
            reapply=req.reapply,
        )
        plan = resolution.plan
        existing = resolution.existing
        diff = resolution.diff

        if existing is not None and existing.status == DeployStatus.COMPLETED and diff is None:
            # Idempotent: a completed deploy of an unchanged scenario is a
            # no-op. NOT short-circuited for `--update` (`diff is not None`)
            # even when the diff turns out to be a no-op re-apply — the
            # sibling-dangling-host check + generation bookkeeping still
            # need to run, and a `completed` status doesn't mean "identical
            # to what --update just resolved" the way it does for the plain
            # resume path.
            store.close()
            return dtos.CreateDeployResponse(
                deploy_id=existing.deploy_id, status="completed", detached=False,
                config_warnings=config_warnings, plan_id=existing.plan_id,
            )
        if existing is not None:
            # Resuming/updating a standing twin IS a write to it.
            _check_protection(settings, existing.deploy_id, override_protection)

        deploy_id = existing.deploy_id if existing is not None else (
            f"{plan.scenario_id}-{secrets.token_hex(4)}"
        )

        lease = store.acquire_lease(
            plan.scenario_id, deploy_id, process_info=default_process_info()
        )
        if not lease.acquired:
            held = lease.held_by_deploy_id
            store.close()
            raise ApiError(
                409,
                f"another deploy of scenario '{plan.scenario_id}' is in progress",
                detail={"held_by_deploy_id": held},
            )

        if existing is not None:
            store.reset_incomplete_ops(deploy_id)
            # An --update's barriers whose predecessor set gained an Added
            # op must be re-evaluated even though their row may already be
            # `completed` (`docs/WORK_ORDER_twin_incremental_update.md`
            # §3.3) -- `reset_incomplete_ops` above only touches non-
            # completed rows, so this is a SEPARATE, explicit force-reset.
            # Extended past barriers (MUST-FIX 1, code review) to non-
            # barrier AGGREGATE ops (e.g. apply-sdn-config's bare-star
            # `create-vnet[*]`/`create-subnet[*]` after-clause) -- see
            # `plandiff.ops_to_reexecute`'s docstring for why.
            if diff is not None:
                from axrange.planner.plandiff import (
                    barriers_to_reevaluate, ops_to_reexecute,
                )

                stale_barriers = barriers_to_reevaluate(plan, diff.added)
                stale_ops = ops_to_reexecute(plan, diff.added)
                # `--reapply`'s content-only redo set (`docs/WORK_ORDER_
                # responder_op_row_drift.md` option A) joins the SAME
                # force-reset: its rows are `completed` with the OLD
                # `script_content`, so without this the resume
                # short-circuit skips them. The executor then refreshes
                # each row's `inputs_resolved_json` at attempt start
                # (`set_op_running`, GAP-044/LES-157), which is what makes
                # a subsequent `--update` classify them Unchanged. No
                # sibling-tier reconciliation is needed for these -- a
                # content-only change touches no host identity a
                # `tap`/`tfc`/`exr` binding depends on.
                stale = stale_barriers | stale_ops | set(resolution.reapply_ops)
                if stale:
                    store.force_reset_ops(deploy_id, stale)
            store.update_deploy_status(deploy_id, DeployStatus.RUNNING)
        else:
            store.create_deploy(
                deploy_id=deploy_id,
                scenario_id=plan.scenario_id,
                scenario_version=plan.scenario_version,
                scenario_schema_version=1,
                plan_id=plan.plan_id,
                ir_hash=plan.ir_hash,
                placement=plan.placement,
            )
    finally:
        # We deliberately close THIS connection; the runner opens its own
        # (a deploy is long-running; each StateStore owns one sqlite connection).
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass

    actual_runner = runner or _proxmox_runner
    if diff is not None and runner is None:
        # `--update`'s tail (the scoped removal sweep + generation bookkeeping
        # + sibling-dangling warning, `apply_update_tail`) must run INSIDE
        # the SAME heartbeat-renewed window the added-ops executor pass
        # used (work order §4.4, mirroring the 2026-07-29 cascade-ordering
        # fix's precedent: teardown work stays inside the heartbeat, never
        # after it's released) -- `functools.partial` binds `post_run`
        # without changing `_proxmox_runner`'s call signature (`DeployRunner`
        # stays a plain 5-positional-arg callable; every existing `runner=`
        # test injection is unaffected). Only applied to the DEFAULT
        # production runner -- a caller that injects its own `runner=` for
        # testing is responsible for its own tail if it wants one.
        import functools

        actual_runner = functools.partial(
            _proxmox_runner,
            post_run=lambda store_, cfg_: apply_update_tail(
                store_, cfg_, deploy_id, plan, diff, node=req.target_node,
                log=lambda m: logger.info("update %s: %s", deploy_id, m),
            ),
        )

    def _job() -> None:
        actual_runner(settings, plan, cfg, ctx, deploy_id)

    if background is not None:
        background(_job)
    else:
        t = threading.Thread(target=_job, name=f"deploy-{deploy_id}", daemon=True)
        t.start()

    return dtos.CreateDeployResponse(
        deploy_id=deploy_id, status="running", detached=True,
        config_warnings=config_warnings, plan_id=plan.plan_id,
    )


# ---------------------------------------------------------------------------
# The runner's inner "build + run" and "release" halves (`run_deploy_executor`
# / `release_deploy_resources`) are `axrange.planner.entry`'s, shared with
# `cli.main.cmd_deploy` and the playouts. Each surface keeps its OWN outer
# try/except/return shape: the API swallows an executor crash into
# `deploys.status=FAILED` because it runs in a background thread with no
# caller to propagate to; the CLI lets it surface as an uncaught traceback.
# ---------------------------------------------------------------------------
# Incremental update TAIL (`docs/WORK_ORDER_twin_incremental_update.md`
# §3.2/§3.4/§3.5) -- runs AFTER a successful added-ops executor pass, shared
# by `cli.main.cmd_deploy` and `_proxmox_runner`'s `post_run` hook (LES-037).
# ---------------------------------------------------------------------------
@dataclass
class UpdateTailResult:
    """`apply_update_tail`'s own clean/dirty signal (code review M3).

    Before this, `apply_update_tail` returned `None` unconditionally, so a
    NON-clean removal sweep (a protect-guard refusal, a VM that wouldn't
    stop) was invisible to both callers: `cli.main.cmd_deploy` reported the
    EXECUTOR's `result.succeeded` (oblivious to the tail — a test asserted
    `rc == 0` while a VM was refused by the protect guard and left running),
    and `_proxmox_runner`'s `post_run` left `deploys.status = completed`
    even though real resources were still standing. `clean=True` when the
    removal sweep (if any) had zero `.errors` — an empty `diff.removed` is
    trivially clean. `errors` carries the `TeardownAction`s that failed
    (`guard-violation`/`error`) so a caller can report a count or render
    them.
    """

    clean: bool
    errors: list[Any] = field(default_factory=list)


def apply_update_tail(
    store: Any, cfg: Any, deploy_id: str, plan: Any, diff: Any, *,
    node: Optional[str] = None, log: Callable[[str], None] = lambda _m: None,
) -> UpdateTailResult:
    """After the executor has finished an `--update`'s added/retried ops, do
    the REST of the incremental-update apply:

      1. The scoped removal sweep (§3.2) over `diff.removed`, reusing
         `planner.teardown.destroy_ops_scoped` (LES-037) — a no-op when
         `diff.removed` is empty.
      2. On a CLEAN sweep (or nothing to remove), record the deploy row's
         new `ir_hash` + `placement` with `plan_id` still PINNED (§3.4).
         Deliberately skipped on a partial sweep failure — a retried
         `--update` must recompute the SAME removed set against the SAME
         (old) placement, not a truncated one missing the still-not-fully-
         torn-down host.
      3. The sibling-tier dangling-host warning (§3.5) — logged, never
         raised; auto-reconcile is an explicit follow-on, not this build's
         job.

    Never RAISES for an ordinary per-resource teardown failure (mirrors
    `destroy_deploy`'s own "record and continue" posture) — the returned
    `UpdateTailResult.clean` is how a caller learns "did this actually
    finish," not an exception. Call this BEFORE releasing the deploy's
    lease/heartbeat (§4.4) — see `_proxmox_runner`'s `post_run` docstring
    and `cli.main.cmd_deploy`'s inline call.
    """
    from axrange.adapter_proxmox import ProxmoxTeardown
    from axrange.planner import destroy_ops_scoped

    clean = True
    errors: list[Any] = []
    if diff.removed:
        backend = ProxmoxTeardown(cfg)
        result = destroy_ops_scoped(
            store, backend, deploy_id, diff.removed, node=node, log=log,
            protected_names=cfg.protected_external_names,
            protected_vmids=cfg.protected_external_vmids,
        )
        clean = not result.errors
        errors = result.errors

    if clean:
        store.update_deploy_generation(
            deploy_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
    else:
        log(
            "  --update: removed resource(s) not fully cleared; ir_hash/"
            "placement left at the prior generation so a retried --update "
            "finishes the removal sweep"
        )

    removed_hosts = _removed_hosts(store, deploy_id, diff.removed)
    for warning in _dangling_sibling_warnings(store, deploy_id, removed_hosts):
        log(f"  ! {warning}")

    return UpdateTailResult(clean=clean, errors=errors)


def _removed_hosts(store: Any, deploy_id: str, removed_op_ids: Any) -> set[str]:
    """`{host_id}` for every removed `clone-vm[<host>]` op -- the definitive
    "this scenario node is entirely gone" signal, read from the OLD
    (pre-sweep) op rows' `scope_json` (stable regardless of status)."""
    from axrange.planner.opview import op_fields

    wanted = set(removed_op_ids)
    if not wanted:
        return set()
    hosts: set[str] = set()
    for op in store.ops_for_deploy(deploy_id):
        if op.op_id not in wanted:
            continue
        op_name, scope, _inputs, _outputs = op_fields(op)
        if op_name != "clone-vm":
            continue
        host = scope.get("host")
        if host:
            hosts.add(host)
    return hosts


def _hosts_referenced_by_ops(ops: Any) -> set[str]:
    """Best-effort host attribution across a sibling deploy's op rows: any
    op whose scope or resolved inputs carry a `host`/`host_id` key (`docs/
    WORK_ORDER_twin_incremental_update.md` §3.5). Not exhaustive -- a tap
    binding scoped purely to a net carries no single host -- but enough to
    catch the work order's own flagship case: a `tfc` deploy's per-host
    `assign-role`/`drive-persona` rows."""
    from axrange.planner.opview import op_fields

    hosts: set[str] = set()
    for op in ops:
        _name, scope, inputs, _outputs = op_fields(op)
        for d in (scope, inputs):
            for key in ("host", "host_id"):
                v = d.get(key)
                if isinstance(v, str) and v:
                    hosts.add(v)
    return hosts


def _dangling_sibling_warnings(
    store: Any, te_deploy_id: str, removed_hosts: set[str],
) -> list[str]:
    """§3.5 (an honest v1 limit, warn-only): a standing `tap`/`tfc`/`exr`
    deploy bound to `te_deploy_id` that still references any of
    `removed_hosts` gets a loud warning naming the deploy and the dangling
    host(s) — no auto-reconcile (a follow-on).

    `cascade.siblings_for_te` (code review S3) is the ONE place that knows
    each tier's `tap-<te>`/`tfc-<te>`/`exr-<te>-*` naming convention — this
    used to hand-roll it a 4th time (alongside `o2control.{exercise,traffic,
    taps}.py`, each for their OWN tier)."""
    if not removed_hosts:
        return []
    from axrange.o2control.cascade import siblings_for_te

    warnings: list[str] = []
    for tier, rows in siblings_for_te(store, te_deploy_id).items():
        for row in rows:
            touched = (
                _hosts_referenced_by_ops(store.ops_for_deploy(row.deploy_id))
                & removed_hosts
            )
            if touched:
                warnings.append(
                    f"{tier} deploy {row.deploy_id} still references removed "
                    f"host(s) {sorted(touched)} — re-apply it (`axrange {tier} "
                    "deploy`) to drop the dangling binding"
                )
    return warnings


def _proxmox_runner(settings: ApiSettings, plan, cfg, ctx, deploy_id: str, *,
                    post_run: Optional[Callable[[Any, Any], None]] = None) -> None:
    """Default deploy runner: the real Proxmox executor, lease-heartbeated.

    Mirrors cli.cmd_deploy's executor block via `run_deploy_executor` +
    `release_deploy_resources`. Runs in a background thread; an executor
    exception is swallowed into state.db (the executor already records
    per-op failures; a hard crash here still releases the lease/locks via the
    finally, unlike a bare `store.release_lease` call with no guard would).

    `post_run(store, cfg)` (`docs/WORK_ORDER_twin_incremental_update.md`
    §4.4), when given, runs AFTER a SUCCESSFUL executor pass and BEFORE the
    lease/lock release — the hook `create_deploy`'s `--update` path uses
    (via `functools.partial`, so this signature never changes for any other
    caller) to run its scoped-removal-sweep + generation-bookkeeping tail
    (`apply_update_tail`) INSIDE the same heartbeat-renewed window the
    added-ops execution used, instead of letting the lease drop first
    (mirrors the 2026-07-29 cascade-ordering fix's "teardown work stays
    inside the heartbeat" precedent). Only called when the executor run
    itself succeeded — a partially-failed added-ops pass must not also
    trigger the removal sweep; a retried `--update` naturally redoes the
    failed ops and only then reaches the tail. Any exception `post_run`
    raises is caught by the SAME `except Exception` below as an executor
    crash (`apply_update_tail` itself is designed not to raise for an
    ordinary per-resource teardown failure — see its docstring). If
    `post_run` instead returns a non-clean `UpdateTailResult` (code review
    M3 — a protect-guard refusal or a resource that wouldn't clear), the
    deploy is downgraded to `FAILED` here rather than left at the
    `COMPLETED` the executor's OWN successful pass already wrote — a
    `--update` whose removal half is still standing is not a green deploy.
    """
    from axrange.adapter_proxmox import ProxmoxAdapter
    from axrange.config_exec import ConfigBackend
    from axrange.planner import StateStore
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import DeployStatus

    store = StateStore(settings.db_path)
    heartbeat = Heartbeat(
        settings.db_path, deploy_id=deploy_id, scenario_id=plan.scenario_id
    )
    try:
        adapter = ProxmoxAdapter(cfg)
        config_backend = ConfigBackend(cfg)
        result = run_deploy_executor(
            store, adapter, config_backend, plan, ctx, deploy_id, heartbeat
        )
        if post_run is not None and result.succeeded:
            tail = post_run(store, cfg)
            if tail is not None and not tail.clean:
                store.update_deploy_status(deploy_id, DeployStatus.FAILED)
    except Exception:  # noqa: BLE001
        try:
            store.update_deploy_status(deploy_id, DeployStatus.FAILED)
        except Exception:  # noqa: BLE001
            pass
    finally:
        release_deploy_resources(store, plan.scenario_id, deploy_id, heartbeat)
        store.close()


# ---------------------------------------------------------------------------
# Run scenario (step 6) — re-fire activities. Deferred semantics: the engine has
# no re-run-activities affordance yet (UI brief §9 leaves it "decide at build").
# We expose the endpoint and return a clear not-yet status rather than silently
# doing nothing, so the UI can hide/disable the button.
# ---------------------------------------------------------------------------
def run_scenario(settings: ApiSettings, deploy_id: str) -> dtos.RunResponse:
    from axrange.planner import StateStore

    # It only READS the row: never create a missing store for it.
    store = StateStore(settings.db_path, readonly=True)
    try:
        d = store.get_deploy(deploy_id)
    finally:
        store.close()
    if d is None:
        raise ApiError(404, f"deploy not found: {deploy_id}")
    return dtos.RunResponse(
        deploy_id=deploy_id,
        status="not_implemented",
        message=(
            "Re-firing activities on an already-deployed range (start-activity "
            "re-run) is not yet an engine affordance; activities fire at deploy "
            "time today (UI brief §9). Deferred."
        ),
    )


# ---------------------------------------------------------------------------
# Destroy (§8) — reverse-of-create teardown driven by recorded state.
# The actual teardown is delegated to a `TeardownBackend` so tests inject a fake
# (no Proxmox). Production builds a ProxmoxTeardown from adapter.conf. The
# planner orchestrator (`planner/teardown.py`) owns ordering + name-guards + the
# state transition; this wraps it with the auth gate, the lease guard, and the
# DTO mapping. Synchronous in v1 (the CLI verb is too) — long teardowns block the
# request; an async/detached destroy is a follow-on if it bites.
# ---------------------------------------------------------------------------
TeardownFactory = Callable[[ApiSettings], Any]


def _proxmox_teardown(settings: ApiSettings):
    from axrange.adapter_proxmox import ProxmoxTeardown, load_config

    return ProxmoxTeardown(load_config(settings.adapter_config))


def destroy_deploy(settings: ApiSettings, deploy_id: str,
                   req: dtos.DestroyRequest, *,
                   teardown_factory: Optional[TeardownFactory] = None,
                   cascade_adapter: Any = None,
                   cascade_persona_backend: Any = None,
                   ) -> dtos.DestroyResponse:
    from axrange.planner import StateStore
    from axrange.planner import destroy_deploy as run_teardown
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info

    store = StateStore(settings.db_path)
    try:
        d = store.get_deploy(deploy_id)
        if d is None:
            raise ApiError(404, f"deploy not found: {deploy_id}")
        if not req.allow_destroy:
            raise ApiError(
                400, "destroy requires allow_destroy: true in the request body"
            )

        backend = (teardown_factory or _proxmox_teardown)(settings)

        from axrange.adapter_proxmox import load_config

        cfg = load_config(settings.adapter_config)

        # Lease guard: refuse to tear down a scenario that has a deploy in flight
        # (409, same posture as create). A completed deploy holds no lease, so
        # this takes it cleanly; the orchestrator releases it at the end.
        lease = store.acquire_lease(
            d.scenario_id, deploy_id, process_info=default_process_info()
        )
        if not lease.acquired:
            raise ApiError(
                409,
                f"another deploy of scenario '{d.scenario_id}' is in progress; "
                "refusing to destroy",
                detail={"held_by_deploy_id": lease.held_by_deploy_id},
            )

        # Heartbeat the lease for the life of the teardown (code-review finding
        # #2, 2026-07-07): a multi-VM sweep can outrun the lease's fixed TTL
        # (300s) with no renewal, letting a concurrent deploy race in mid-
        # teardown. Same pattern the deploy path already uses (_proxmox_runner
        # above); the heartbeat opens its own StateStore connection.
        heartbeat = Heartbeat(
            settings.db_path, deploy_id=deploy_id, scenario_id=d.scenario_id
        )
        heartbeat.start()
        try:
            # Sibling-tier cascades run BEFORE the twin's own sweep:
            # `exr` → `tfc` → `tap` → `te`. Every sibling binds into the twin's
            # LIVE VMs (tap's de-scope re-asserts a per-VM hookscript, tfc's
            # persona stop is a QGA `agent/exec` into the guest), so a cascade
            # sequenced after the VM purge can only fail — and because both of
            # those loops are best-effort/swallowed by design, the old order
            # reported a green `DESTROYED` under a wall of red rather than
            # failing outright (LES-171). Order + per-sibling exception
            # isolation (LES-117) live in `o2control/cascade.py` — ONE helper
            # both this and `cli/main.py:cmd_destroy` call, so the sequence
            # can't drift between the two transports (LES-037).
            #
            # Lease-safe inside the twin's lease taken above: sibling deploys
            # are keyed `scenario_id == plan_id` (`exr-*`/`tfc-*`/`tap-*`),
            # never the twin's `scenario_id`. Running them inside the heartbeat
            # window also keeps the lease renewed across the whole teardown,
            # not just the twin's own sweep.
            #
            # An `error` is recorded on the response's `cascade_errors` (not
            # logged — no stdlib `logging` precedent introduced here): a
            # programmatic API caller must SEE that a bound sibling failed, not
            # just an operator watching CLI stdout ("no silent failures").
            from axrange.o2control.cascade import cascade_destroy_siblings_for_te

            cascade_errors = [
                f"{c.label} cascade: {c.error}"
                for c in cascade_destroy_siblings_for_te(
                    db_path=settings.db_path, te_deploy_id=deploy_id,
                    adapter_config=settings.adapter_config,
                    teardown_backend=backend,
                    adapter=cascade_adapter,
                    persona_backend=cascade_persona_backend,
                )
                if c.error is not None
            ]

            result = run_teardown(
                store, backend, deploy_id,
                protected_names=cfg.protected_external_names,
                protected_vmids=cfg.protected_external_vmids,
            )
        finally:
            heartbeat.stop()
            # Belt-and-suspenders (finding #5): `run_teardown` releases the
            # lease at its own tail UNCONDITIONALLY on the success path, but
            # not inside a try/finally there — an exception raised before
            # that point (e.g. a bug in `store.mark_destroyed`) would leave
            # this lease held until TTL expiry. `release_lease` is a plain
            # DELETE, idempotent, so releasing it again here on the normal
            # path is a harmless no-op, not a double-release bug.
            try:
                store.release_lease(d.scenario_id, deploy_id)
            except Exception:  # noqa: BLE001
                pass

        return dtos.DestroyResponse(
            deploy_id=result.deploy_id,
            destroyed=result.destroyed,
            removed=result.removed,
            errors=len(result.errors),
            actions=[
                dtos.DestroyActionDTO(
                    kind=a.kind, target=a.target, status=a.status, detail=a.detail
                )
                for a in result.actions
            ],
            cascade_errors=cascade_errors,
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Pcaps — deferred to a later Phase-2 increment (download home open; UI brief §7)
# ---------------------------------------------------------------------------
def list_pcaps(settings: ApiSettings) -> dtos.PcapListDTO:
    return dtos.PcapListDTO(
        pcaps=[],
        implemented=False,
        note=(
            "pcap browse/download is deferred (docs/design/11-api-ui.md). "
            "Capture telemetry is on the sensor; the download surface (orchestrator "
            "mgmt read vs. sensor-side server) is an open Phase-2 increment."
        ),
    )


# ---------------------------------------------------------------------------
# O2 surface (A1, `docs/design/05-o2-surface.md`) — a thin DTO wrapper
# over `axrange.o2control`. ALL logic lives there (`docs/CONVENTIONS.md`
# code-structure rule); this module only resolves settings and maps engine
# objects -> DTOs, exactly like every other function in this file.
# ---------------------------------------------------------------------------
def _o2_images_root(settings: ApiSettings) -> Any:
    from pathlib import Path

    return Path(settings.images_root) if settings.images_root else Path.cwd()


def _surface_status_dto(status: Any) -> dtos.SurfaceStatusDTO:
    return dtos.SurfaceStatusDTO(
        deploy_id=status.deploy_id,
        status=status.status,
        sensors=[
            dtos.SensorInventoryDTO(
                sensor_id=s.sensor_id, type=s.type,
                transport_ip=s.transport_ip, deploy_id=s.deploy_id,
            )
            for s in status.sensors
        ],
    )


def o2_surface_up(settings: ApiSettings, req: dtos.SurfaceUpRequest, *,
                  adapter: Any = None,
                  config_backend: Any = None) -> dtos.SurfaceStatusDTO:
    from axrange.o2control import O2ControlError, stand_up_surface

    try:
        status = stand_up_surface(
            surface_path=req.surface_path,
            db_path=settings.db_path,
            images_root=_o2_images_root(settings),
            adapter_config=settings.adapter_config,
            target_node=req.target_node,
            adapter=adapter,
            config_backend=config_backend,
        )
    except O2ControlError as e:
        raise ApiError(e.status, e.message) from e
    return _surface_status_dto(status)


def o2_surface_status(settings: ApiSettings,
                      deploy_id: Optional[str] = None) -> dtos.SurfaceStatusDTO:
    from axrange.o2control import O2ControlError, surface_status

    try:
        status = surface_status(db_path=settings.db_path, deploy_id=deploy_id)
    except O2ControlError as e:
        raise ApiError(e.status, e.message) from e
    return _surface_status_dto(status)


def o2_surface_down(settings: ApiSettings, req: dtos.SurfaceDownRequest, *,
                    teardown_backend: Any = None) -> dtos.SurfaceDownResponse:
    from axrange.o2control import O2ControlError, tear_down_surface

    if not req.allow_destroy:
        raise ApiError(
            400, "destroy requires allow_destroy: true in the request body"
        )
    try:
        result = tear_down_surface(
            db_path=settings.db_path, deploy_id=req.deploy_id,
            adapter_config=settings.adapter_config,
            teardown_backend=teardown_backend,
        )
    except O2ControlError as e:
        raise ApiError(e.status, e.message) from e
    return dtos.SurfaceDownResponse(
        deploy_id=result.deploy_id,
        destroyed=result.destroyed,
        removed=result.removed,
        errors=len(result.errors),
        actions=[
            dtos.DestroyActionDTO(
                kind=a.kind, target=a.target, status=a.status, detail=a.detail
            )
            for a in result.actions
        ],
    )


# ---------------------------------------------------------------------------
# Tap-plan playout (A2, `docs/design/06-taps-sensors.md`) -- a thin DTO wrapper
# over `axrange.o2control.taps`. ALL logic lives there (`docs/CONVENTIONS.md`
# code-structure rule); this module only resolves settings and maps engine
# objects -> DTOs, exactly like the O2 surface functions above.
# ---------------------------------------------------------------------------
def _tap_status_dto(status: Any) -> dtos.TapPlanStatusDTO:
    return dtos.TapPlanStatusDTO(
        deploy_id=status.deploy_id,
        target=status.target,
        status=status.status,
        bindings=[
            dtos.TapBindingDTO(
                tap=b.tap, sensors=list(b.sensors),
                direction=getattr(b, "direction", None),
            )
            for b in status.bindings
        ],
        warnings=list(getattr(status, "warnings", None) or []),
    )


# `GET /api/taps` -- the read peer of `axrange tap status`/`tap info`, both
# calling the SAME `axrange.o2control.taps.{tap_status,tap_info}` resolvers the
# CLI verbs use (LES-037), mapped to DTOs. `target`/`deploy_id` resolve
# identically to the CLI (a twin id -> its applied tap deploy).
def o2_tap_status(
    settings: ApiSettings, *, target: Optional[str] = None,
    deploy_id: Optional[str] = None,
) -> dtos.TapPlanStatusDTO:
    from axrange.o2control.taps import tap_status

    return _tap_status_dto(
        tap_status(db_path=settings.db_path, target=target, deploy_id=deploy_id)
    )


def o2_tap_info(
    settings: ApiSettings, *, target: Optional[str] = None,
    deploy_id: Optional[str] = None, reveal_secrets: bool = False,
) -> dtos.TapInfoDTO:
    from axrange.adapter_proxmox import load_config
    from axrange.adapter_proxmox.resolve import external_sensor_facts
    from axrange.o2control.taps import tap_info

    cfg = load_config(settings.adapter_config)
    info = tap_info(
        db_path=settings.db_path, target=target, deploy_id=deploy_id,
        external_sensors=cfg.external_sensors,
    )
    ext = external_sensor_facts(cfg)
    return dtos.TapInfoDTO(
        target=info.target,
        target_status=info.target_status,
        deploy_id=info.deploy_id,
        status=info.status,
        bindings=[
            dtos.TapBindingInfoDTO(
                host=b.host, net_id=b.net_id, nic_index=b.nic_index,
                sensors=list(b.sensors), gre_ports=dict(b.gre_ports),
                sensor_transport_ips=dict(b.sensor_transport_ips),
                capture_verified=dict(b.capture_verified),
                directions=dict(getattr(b, "directions", None) or {}),
            )
            for b in info.bindings
        ],
        warnings=list(info.warnings),
        fanout_note=info.fanout_note,
        external_sensors=[
            dtos.ExternalSensorAccessDTO(
                id=e["id"], type=e["type"], vmid=e["vmid"],
                transport_ip=e["transport_ip"], capture_iface=e["capture_iface"],
                resolved_name=e["resolved_name"], resolved_ip=e["resolved_ip"],
                access_user=e["access_user"],
                access_password=_masked(e["access_password"], reveal=reveal_secrets),
                access_hint=e["access_hint"],
            )
            for e in ext
        ],
        secrets_revealed=reveal_secrets,
    )


def o2_tap_apply(settings: ApiSettings, req: dtos.TapApplyRequest, *,
                 adapter: Any = None,
                 config_backend: Any = None) -> dtos.TapPlanStatusDTO:
    from axrange.o2control.taps import TapPlanError, apply_tap_plan, parse_tap_plan

    # M1 (code review, `docs/WORK_ORDER_tap_points.md`): route the REQUEST
    # through the EXACT SAME `parse_tap_plan` a file-loaded tap-plan uses --
    # ONE validation implementation for file and API (LES-037), not a
    # second, weaker one. The PRIOR shape here built `TapBindingSpec`
    # directly from the DTO, bypassing every parse-time invariant
    # `parse_tap_plan` enforces: exactly one of `tap`/`tap_point`/`selector`
    # per binding (a binding with NONE of the three silently matched EVERY
    # derived tap point in the twin -- a real MUST-FIX, not hypothetical),
    # both `tap` AND `tap_point` set together, an unknown selector key,
    # `mode: exclusive` with >1 sensor, and a duplicate binding.
    # `exclude_none=True` drops each DTO's unset optional fields so the
    # reconstructed dict looks exactly like a hand-authored YAML binding
    # would (absent keys, not `null`-valued ones) -- `parse_tap_plan`
    # distinguishes "key present" from "key absent", not merely falsy.
    try:
        spec = parse_tap_plan({
            "tap-plan": {
                "target": req.target,
                "bindings": [
                    b.model_dump(exclude_none=True) for b in req.bindings
                ],
            }
        })
    except TapPlanError as e:
        raise ApiError(e.status, e.message) from e

    try:
        status = apply_tap_plan(
            spec=spec, db_path=settings.db_path,
            adapter_config=settings.adapter_config,
            target_node=req.target_node, adapter=adapter,
            config_backend=config_backend,
        )
    except TapPlanError as e:
        raise ApiError(e.status, e.message) from e
    return _tap_status_dto(status)


def o2_tap_destroy(settings: ApiSettings, req: dtos.TapDestroyRequest, *,
                   teardown_backend: Any = None,
                   adapter: Any = None) -> dtos.TapDestroyResponse:
    from axrange.o2control.taps import TapPlanError, destroy_taps

    if not req.allow_destroy:
        raise ApiError(
            400, "destroy requires allow_destroy: true in the request body"
        )
    try:
        result = destroy_taps(
            db_path=settings.db_path, target=req.target, deploy_id=req.deploy_id,
            adapter_config=settings.adapter_config,
            teardown_backend=teardown_backend, adapter=adapter,
        )
    except TapPlanError as e:
        raise ApiError(e.status, e.message) from e
    return dtos.TapDestroyResponse(
        deploy_id=result.deploy_id,
        destroyed=result.destroyed,
        removed=result.removed,
        errors=len(result.errors),
        actions=[
            dtos.DestroyActionDTO(
                kind=a.kind, target=a.target, status=a.status, detail=a.detail
            )
            for a in result.actions
        ],
    )


# ---------------------------------------------------------------------------
# Traffic-profile playout (the Traffic tier, `docs/WORK_ORDER_traffic_tier.md`)
# -- a thin DTO wrapper over `axrange.o2control.traffic`, same posture as the
# tap-plan wrapper above (ALL logic lives there; this module only resolves
# settings and maps engine objects -> DTOs).
# ---------------------------------------------------------------------------
def _traffic_status_dto(status: Any) -> dtos.TrafficPlanStatusDTO:
    return dtos.TrafficPlanStatusDTO(
        deploy_id=status.deploy_id,
        twin=status.twin,
        status=status.status,
        assignments=[
            dtos.TrafficAssignmentDTO(persona=a.persona, hosts=list(a.hosts))
            for a in status.assignments
        ],
        warnings=list(getattr(status, "warnings", None) or []),
    )


def tfc_apply(settings: ApiSettings, req: dtos.TrafficApplyRequest, *,
             persona_backend: Any = None) -> dtos.TrafficPlanStatusDTO:
    from axrange.o2control.traffic import (
        TrafficPlanError, apply_traffic_profile, parse_traffic_profile,
    )

    # Route the REQUEST through the EXACT SAME `parse_traffic_profile` a
    # file-loaded profile uses (LES-037) -- one validation implementation
    # for file and API, not a second, weaker one. `exclude_none=True` drops
    # each DTO's unset optional fields so the reconstructed dict looks
    # exactly like a hand-authored YAML entry would.
    try:
        body: dict[str, Any] = {
            "roles": [r.model_dump(exclude_none=True) for r in req.roles],
            "assignments": [a.model_dump(exclude_none=True) for a in req.assignments],
            "replay": [r.model_dump(exclude_none=True) for r in req.replay],
        }
        # Forward whichever target key the client used, verbatim, so
        # `parse_traffic_profile` -- not this function -- owns the
        # both-given/malformed rules (one validation implementation, LES-037).
        if req.target is not None:
            body["target"] = req.target
        if req.twin is not None:
            body["twin"] = req.twin
        spec = parse_traffic_profile({"traffic-profile": body})
    except TrafficPlanError as e:
        raise ApiError(e.status, e.message) from e

    # The target twin's local personas, per request (fix round 1: closes
    # GAP-105 for this route too). A profile that names no twin-local
    # persona resolves exactly as before -- the overlay only ADDS ids the
    # catalog/env dirs do not already define.
    from axrange.personas import registry as persona_registry

    overlay = _tfc_twin_overlay(settings, spec.target)
    try:
        with persona_registry.overlay(overlay):
            status = apply_traffic_profile(
                spec=spec, db_path=settings.db_path,
                adapter_config=settings.adapter_config,
                target_node=req.target_node, persona_backend=persona_backend,
                allow_revert_host_edits=req.allow_revert_host_edits,
            )
    except TrafficPlanError as e:
        raise ApiError(e.status, e.message, detail=e.detail) from e
    return _traffic_status_dto(status)


def _tfc_twin_overlay(settings: ApiSettings, target: Optional[str]) -> dict:
    """The personas overlay of the twin a tier request targets (a te deploy
    id or twin name); `{}` when it cannot be resolved -- the engine then
    reports the target error itself."""
    if not target or not Path(settings.db_path).is_file():
        return {}
    from axrange.o2control.traffic import TrafficPlanError, resolve_traffic_target
    from axrange.planner import StateStore

    store = StateStore(settings.db_path)
    try:
        try:
            te = store.get_deploy(resolve_traffic_target(store, target))
        except TrafficPlanError:
            return {}
    finally:
        store.close()
    if te is None:
        return {}
    overlay, _w, _f = _twin_personas_overlay(settings, te.scenario_id)
    return overlay


def tfc_status(settings: ApiSettings, *,
               target: Optional[str] = None,
               deploy_id: Optional[str] = None) -> dtos.TrafficPlanStatusDTO:
    from axrange.o2control.traffic import traffic_status

    status = traffic_status(db_path=settings.db_path, target=target, deploy_id=deploy_id)
    return _traffic_status_dto(status)


def tfc_destroy(settings: ApiSettings, req: dtos.TrafficDestroyRequest, *,
                teardown_backend: Any = None,
                persona_backend: Any = None) -> dtos.TrafficDestroyResponse:
    from axrange.o2control.traffic import TrafficPlanError, destroy_traffic_profile

    if not req.allow_destroy:
        raise ApiError(
            400, "destroy requires allow_destroy: true in the request body"
        )
    try:
        result = destroy_traffic_profile(
            db_path=settings.db_path, target=req.target, deploy_id=req.deploy_id,
            adapter_config=settings.adapter_config,
            teardown_backend=teardown_backend, persona_backend=persona_backend,
        )
    except TrafficPlanError as e:
        raise ApiError(e.status, e.message) from e
    return dtos.TrafficDestroyResponse(
        deploy_id=result.deploy_id,
        destroyed=result.destroyed,
        removed=result.removed,
        errors=len(result.errors),
        actions=[
            dtos.DestroyActionDTO(
                kind=a.kind, target=a.target, status=a.status, detail=a.detail
            )
            for a in result.actions
        ],
    )


# ---------------------------------------------------------------------------
# Exercise playout (the Exercise tier, `docs/WORK_ORDER_exercise_tier.md`) --
# a thin DTO wrapper over `axrange.o2control.exercise`, same posture as the
# tap-plan/traffic-profile wrappers above (ALL logic lives there; this module
# only resolves settings and maps engine objects -> DTOs).
# ---------------------------------------------------------------------------
def _exercise_status_dto(status: Any) -> dtos.ExerciseStatusDTO:
    return dtos.ExerciseStatusDTO(
        deploy_id=status.deploy_id, twin=status.twin, status=status.status,
        playout_hash=status.playout_hash, activity_host=status.activity_host,
        adversary_targets=list(status.adversary_targets),
        defense_notes=list(status.defense_notes), measurement=dict(status.measurement),
        warnings=list(getattr(status, "warnings", None) or []),
    )


def _exercise_attack_dict(req: dtos.ExerciseApplyRequest) -> dict[str, Any]:
    """Reconstruct the exercise-spec's `attack:` dict from the DTO's typed
    fields -- routed through the EXACT SAME `parse_exercise_spec` a
    file-loaded spec uses (LES-037: one validation implementation for file
    and API), mirroring `o2_tap_apply`/`tfc_apply`'s `model_dump`-into-parse
    pattern."""
    attack: dict[str, Any] = {}
    if req.activity is not None:
        activity: dict[str, Any] = {
            "id": req.activity.id, "host": req.activity.host,
            "inline": {"steps": [dict(s) for s in req.activity.inline_steps]},
        }
        if req.activity.schedule is not None:
            activity["schedule"] = dict(req.activity.schedule)
        if req.activity.seed is not None:
            activity["seed"] = req.activity.seed
        attack["activity"] = activity
    if req.adversary is not None:
        adversary: dict[str, Any] = {
            "id": req.adversary.id, "targets": list(req.adversary.targets),
            "server": req.adversary.server,
        }
        if req.adversary.group is not None:
            adversary["group"] = req.adversary.group
        attack["adversary"] = adversary
    return attack


def exr_apply(settings: ApiSettings, req: dtos.ExerciseApplyRequest, *,
              config_backend: Any = None) -> dtos.ExerciseStatusDTO:
    from axrange.o2control.exercise import ExercisePlanError, apply_exercise, parse_exercise_spec

    try:
        exercise_body: dict[str, Any] = {
            "attack": _exercise_attack_dict(req),
            "defense": list(req.defense), "measurement": dict(req.measurement),
        }
        # `twin:` is OPTIONAL in the spec now (name-targeting parity, 2026-08-04);
        # forward it only when the client supplied one, so a request relying on
        # `target` alone doesn't inject a null the parser would reject.
        if req.twin is not None:
            exercise_body["twin"] = req.twin
        spec = parse_exercise_spec({"exercise": exercise_body})
    except ExercisePlanError as e:
        raise ApiError(e.status, e.message) from e

    try:
        status = apply_exercise(
            spec=spec, db_path=settings.db_path,
            adapter_config=settings.adapter_config, target_node=req.target_node,
            target=req.target, config_backend=config_backend,
        )
    except ExercisePlanError as e:
        raise ApiError(e.status, e.message) from e
    return _exercise_status_dto(status)


def exr_status(settings: ApiSettings, *,
                target: Optional[str] = None,
                deploy_id: Optional[str] = None) -> dtos.ExerciseStatusDTO:
    from axrange.o2control.exercise import exercise_status

    status = exercise_status(db_path=settings.db_path, deploy_id=deploy_id, twin=target)
    return _exercise_status_dto(status)


def exr_destroy(settings: ApiSettings, req: dtos.ExerciseDestroyRequest, *,
                 teardown_backend: Any = None) -> dtos.ExerciseDestroyResponse:
    from axrange.o2control.exercise import (
        ExercisePlanError, destroy_exercise, latest_exercise_deploy,
    )
    from axrange.planner import StateStore

    if not req.allow_destroy:
        raise ApiError(
            400, "destroy requires allow_destroy: true in the request body"
        )

    deploy_id = req.deploy_id
    if deploy_id is None:
        # Same bare-default posture as `axrange exr destroy` (LES-084 --
        # prefer the newest NON-destroyed run; a twin can have several
        # independent exr runs bound to it, so `target` alone picks the
        # latest, not a unique one).
        store = StateStore(settings.db_path)
        try:
            latest_any = latest_exercise_deploy(store, twin=req.target)
            latest_active = (
                latest_exercise_deploy(store, twin=req.target, exclude_destroyed=True)
                if latest_any is not None else None
            )
        finally:
            store.close()
        if latest_any is None:
            raise ApiError(404, "no exercise deploys found")
        if latest_active is None:
            raise ApiError(
                404,
                f"latest exercise deploy {latest_any.deploy_id} is already destroyed",
            )
        deploy_id = latest_active.deploy_id

    try:
        result = destroy_exercise(
            db_path=settings.db_path, deploy_id=deploy_id,
            adapter_config=settings.adapter_config, teardown_backend=teardown_backend,
        )
    except ExercisePlanError as e:
        raise ApiError(e.status, e.message) from e
    return dtos.ExerciseDestroyResponse(
        deploy_id=result.deploy_id,
        destroyed=result.destroyed,
        removed=result.removed,
        errors=len(result.errors),
        actions=[
            dtos.DestroyActionDTO(
                kind=a.kind, target=a.target, status=a.status, detail=a.detail
            )
            for a in result.actions
        ],
    )


# ---------------------------------------------------------------------------
# Access facts (`docs/WORK_ORDER_access_facts.md` Part D) -- an API peer of
# `axrange te info` (`cli/main.py::cmd_te_info`), NOT a second implementation:
# both call `axrange.planner.access.te_info_from_ops`/`fill_access_facts` and
# map the SAME `TeInfo`/`TeHostInfo` onto their own render (mirrors
# `deploy_inventory`'s "one derivation, two thin renderers" posture above).
# `GET /api/deploys/{deploy_id}/access`.
# ---------------------------------------------------------------------------
def _masked(value: Optional[str], *, reveal: bool) -> Optional[str]:
    if value is None or reveal:
        return value
    return "********"


def deploy_access_facts(settings: ApiSettings, deploy_id: str, *,
                        reveal_secrets: bool = False) -> dtos.AccessFactsDTO:
    from axrange.adapter_proxmox import load_config
    from axrange.planner import StateStore
    from axrange.planner.access import fill_access_facts, te_info_from_ops

    cfg = load_config(settings.adapter_config)
    store = StateStore(settings.db_path)
    try:
        did = deploy_id
        if did == "latest":
            latest = store.latest_deploys(limit=1)
            if not latest:
                raise ApiError(404, "no deploys recorded")
            did = latest[0].deploy_id
        d = store.get_deploy(did)
        if d is None:
            raise ApiError(404, f"deploy not found: {deploy_id}")
        info = te_info_from_ops(
            store.ops_for_deploy(did), scenario_id=d.scenario_id, plan_id=d.plan_id,
        )
        info.deploy_id = did
        info.status = d.status.value
    finally:
        store.close()

    # Best-effort live-IP fallback (same as `te info`'s bare/`[id]` path);
    # never raises (`resolve_vm_ip` degrades to `None`).
    fill_access_facts(info, cfg)

    return dtos.AccessFactsDTO(
        deploy_id=info.deploy_id,
        scenario_id=info.scenario_id,
        status=info.status,
        ops_total=info.ops_total,
        ops_done=info.ops_done,
        hosts=[
            dtos.HostAccessDTO(
                host_id=h.host_id, kind=h.kind, vmid=h.vmid, os_family=h.os_family,
                ips=dict(h.ips), wan_ip=h.wan_ip,
                ip=h.access_ip or "(unresolved)",
                user=h.access_user,
                password=_masked(h.access_password, reveal=reveal_secrets),
                hint=h.access_hint,
            )
            for h in info.hosts
        ],
        subnets=[
            dtos.SubnetAccessDTO(
                net_id=s.net_id, cidr=s.cidr, gateway=s.gateway, snat=s.snat,
            )
            for s in info.subnets
        ],
        # GAP-101 -- the API peer of `te info`'s `egress:` line. `info.egress`
        # is already `EgressPosture.to_dict()`'s shape (see `TeInfo.egress`).
        egress=(dtos.EgressPostureDTO(**info.egress) if info.egress else None),
        secrets_revealed=reveal_secrets,
    )


# ---------------------------------------------------------------------------
# O2 endpoint resolution + external-sensor facts (`docs/WORK_ORDER_access_
# facts.md` Part B/C) -- an API peer of `axrange o2 info`'s endpoint block
# (`cli/main.py::cmd_o2_info`). Reuses `AdapterConfig.resolve_o2_endpoints`
# and `axrange.adapter_proxmox.resolve.external_sensor_facts` -- the SAME two
# calls the CLI verb makes, mapped to DTOs instead of stdout text (LES-037).
# `GET /api/o2/info`.
# ---------------------------------------------------------------------------
def o2_info(settings: ApiSettings, *, reveal_secrets: bool = False) -> dtos.O2InfoDTO:
    from axrange.adapter_proxmox import load_config
    from axrange.adapter_proxmox.resolve import external_sensor_facts

    cfg = load_config(settings.adapter_config)
    endpoints = cfg.resolve_o2_endpoints()
    ext = external_sensor_facts(cfg)
    return dtos.O2InfoDTO(
        endpoints=endpoints,
        external_sensors=[
            dtos.ExternalSensorAccessDTO(
                id=e["id"], type=e["type"], vmid=e["vmid"],
                transport_ip=e["transport_ip"], capture_iface=e["capture_iface"],
                resolved_name=e["resolved_name"], resolved_ip=e["resolved_ip"],
                access_user=e["access_user"],
                access_password=_masked(e["access_password"], reveal=reveal_secrets),
                access_hint=e["access_hint"],
            )
            for e in ext
        ],
        secrets_revealed=reveal_secrets,
    )


# ---------------------------------------------------------------------------
# Twin configs (`docs/WORK_ORDER_twin_configs.md`) -- an API peer of
# `axrange twin-config list|show` (`cli/main.py::cmd_twin_config_list`/
# `cmd_twin_config_show`), calling the SAME `axrange.twinconfig.registry`
# entry points and mapping the result to clean DTOs (never `TwinConfig`
# directly, the `api/` boundary rule) instead of a second catalog reader.
# `GET /api/twin-configs`, `GET /api/twin-configs/{config_id}`.
# ---------------------------------------------------------------------------
def list_twin_configs(settings: ApiSettings) -> dtos.TwinConfigListDTO:
    from axrange.twinconfig.registry import list_twin_configs as _list_twin_configs

    catalog_dir = settings.twin_configs_dir or None
    entries = _list_twin_configs(catalog_dir)
    return dtos.TwinConfigListDTO(
        twin_configs=[
            dtos.TwinConfigSummaryDTO(
                id=e.id, description=e.description, error=e.error,
                cluster=_twin_cluster_for_file(e.path),
                **_param_summary(e.path),
            )
            for e in entries
        ]
    )


def _twin_params(path: Any) -> list[dict[str, Any]]:
    """`twinconfig.params.describe_params` over a te.yaml; `[]` when the file
    cannot be read (the entry's own `error` says why)."""
    from axrange.twinconfig.params import declared_params_from_text, describe_params

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    # Only the `params:` block is parsed (cheap, and no second full parse on
    # the plan path, SF1).
    return describe_params(text, declared_params_from_text(text))


def _param_summary(path: Any) -> dict[str, Any]:
    names = [p["name"] for p in _twin_params(path)]
    return {"parameterized": bool(names), "param_names": names}


def _twin_cluster_for_file(path: Any) -> Optional[str]:
    """`twin.yaml cluster:` for the twin folder `path` sits in (ruling §3.1:
    the field is finally consumed). None when there is no manifest or it
    does not load -- the entry's own `error` already says so."""
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file

    try:
        d = twin_dir_for_file(path)
        return load_twin_manifest(d).manifest.cluster if d is not None else None
    except (TwinConfigError, OSError):
        return None


def _twin_cluster_mismatch(
    settings: ApiSettings, twin_id: str, cfg: Any, target_node: Optional[str],
) -> Optional[str]:
    """A warning when `twins/<id>/twin.yaml` names a `cluster:` that is
    neither the target being deployed to nor its PVE node; else None."""
    loaded = _loaded_twin(settings, twin_id)
    want = loaded.manifest.cluster if loaded is not None else None
    if not want:
        return None
    here = {_target_name(settings), target_node or getattr(cfg, "default_node", "") or ""}
    if want in here:
        return None
    return (
        f"twin {twin_id!r} names cluster {want!r} (twin.yaml) but is being "
        f"deployed to target {_target_name(settings)!r} "
        f"(node {target_node or getattr(cfg, 'default_node', '') or '?'})"
    )


def _loaded_twin(settings: ApiSettings, twin_id: str) -> Any:
    """The `twins/<id>/` folder's loaded manifest, or None (no folder, a
    legacy catalog entry, or a manifest that fails to load)."""
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import find_twin_dir, load_twin_manifest
    from axrange.twinconfig.registry import search_dirs

    if not twin_id or not _is_safe_catalog_ref(twin_id):
        return None
    roots = search_dirs(settings.twin_configs_dir or None)
    try:
        return load_twin_manifest(find_twin_dir(twin_id, roots, warnings=[]))
    except (TwinConfigError, OSError):
        return None


def twin_config_detail(settings: ApiSettings, config_id: str) -> dtos.TwinConfigDetailDTO:
    from axrange.twinconfig import TwinConfigNotFoundError
    from axrange.twinconfig.errors import TwinConfigError
    from axrange.twinconfig.registry import load_twin_config

    catalog_dir = settings.twin_configs_dir or None
    try:
        tc = load_twin_config(config_id, catalog_dir=catalog_dir)
    except TwinConfigNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except TwinConfigError as e:
        raise ApiError(400, str(e)) from e

    loaded = _loaded_twin(settings, config_id)
    from axrange.twinconfig.registry import resolve_twin_config_path

    try:
        tc_path = resolve_twin_config_path(config_id, catalog_dir=catalog_dir)
        described = _twin_params(tc_path)
    except TwinConfigError:
        described = [
            {"name": k, "default": v, "description": None, "declared": True}
            for k, v in (tc.params or {}).items()
        ]
    return dtos.TwinConfigDetailDTO(
        id=tc.id,
        description=tc.description,
        cluster=loaded.manifest.cluster if loaded is not None else None,
        params=[dtos.TwinConfigParamDTO(**p) for p in described],
        segments=[
            dtos.TwinConfigSegmentSummaryDTO(
                name=s.name, cidr=s.cidr, zone_type=s.zone_type, when=s.when,
            )
            for s in tc.segments
        ],
        nodes=[
            dtos.TwinConfigNodeSummaryDTO(
                name=n.name, kind=n.kind, image=n.image, count=n.count,
                role=n.role, when=n.when,
            )
            for n in tc.nodes
        ],
        identity_domain=tc.identity.domain if tc.identity else None,
        identity_user_count=(
            len(tc.identity.users) if tc.identity and tc.identity.users else 0
        ),
        data_count=len(tc.data),
    )


# ---------------------------------------------------------------------------
# Imprints (aximprint-integration Track C, `docs/WORK_ORDER_
# aximprint_console.md`) -- a read-only belief-store/provenance surface over
# a checked-in FIXTURE imprint. Same "list + detail" shape as the
# twin-configs section above; `axrange.api.imprints` is this endpoint's OWN
# reader module (not `axrange.twinconfig.registry`) because an imprint's
# provenance sidecar has no twin-config-vocabulary counterpart. `GET
# /api/imprints`, `GET /api/imprints/{imprint_id}`, `GET /api/imprints/
# {imprint_id}/provenance`.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Discovery runs (`api/Spec.md` A1) -- the ingest -> materialize -> cast ->
# install chain over `aximprint`'s CLI (`axrange.api.discovery`). The
# engine never imports `aximprint`; `backend` is the seam.
# ---------------------------------------------------------------------------
def _discovery_backend(settings: ApiSettings, backend: Any, *, long: bool = False):
    """`long=True` is the SOURCE-driven run's budget (`discovery_timeout_s`, hours):
    an ingest of days over a tunnel; the default is sized for one cast."""
    from axrange.api import discovery as disc

    if backend is not None:
        return backend
    missing = disc.unset_settings(settings)
    if missing is not None:
        raise ApiError(
            503, f"discovery is not configured: {missing} is unset",
            detail={"setting": missing},
        )
    return disc.SubprocessDiscovery(
        settings.aximprint_bin,
        timeout_s=settings.discovery_timeout_s if long else settings.aximprint_timeout_s,
    )


def _discovery_plugins(backend: Any) -> list[dict[str, Any]]:
    from axrange.api import discovery as disc

    try:
        return backend.list_plugins()
    except disc.DiscoveryUnavailableError as e:
        raise ApiError(503, str(e)) from e
    except disc.DiscoveryError as e:
        raise ApiError(502, str(e)) from e


def list_discovery_plugins(settings: ApiSettings, *, backend: Any = None) -> dtos.DiscoveryPluginListDTO:
    """A1.1: every installed `IngestPlugin` with its schema; a stripped
    install with none lists `[]`."""
    plugins = _discovery_plugins(_discovery_backend(settings, backend))
    return dtos.DiscoveryPluginListDTO(
        plugins=[
            dtos.DiscoveryPluginDTO(
                name=str(p.get("name", "")), config_schema=dict(p.get("config_schema") or {}),
                source_kind=str(p.get("source_kind") or "path"),
            )
            for p in plugins
        ]
    )


def _iso(ts: Optional[float]) -> Optional[str]:
    import datetime as _dt

    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _discovery_run_dto(run: Any) -> dtos.DiscoveryRunDTO:
    from axrange.api import discovery as disc

    return dtos.DiscoveryRunDTO(
        id=run.id, status=run.status, plugin=run.plugin, imprint_id=run.imprint_id,
        stages_done=list(run.stages_done), stages=list(disc.STAGES),
        pit_id=run.pit_id, error=run.error,
        created_at=_iso(run.created_at) or "", finished_at=_iso(run.finished_at),
        source_id=run.source_id, started_at=_iso(run.started_at),
        ts_from=run.ts_from, ts_to=run.ts_to, via=run.via,
    )


def create_discovery_run(
    settings: ApiSettings, req: dtos.DiscoveryRunRequest, *, runs: Any,
    backend: Any = None,
    background: Callable[[Callable[[], None]], None] | None = None,
) -> dtos.DiscoveryRunDTO:
    """A1.2/A1.3: refuse everything refusable BEFORE a stage runs -- an
    unknown plugin, a config the plugin's schema rejects, an imprint id that
    is unsafe, already in the catalog, or already being written by another
    run -- so a refused POST has written nothing. Then queue the chain in
    the background (a daemon thread; tests pass a synchronous runner) and
    return the run record at once."""
    from axrange.api import discovery as disc
    from axrange.api.imprints import is_safe_catalog_id

    be = _discovery_backend(settings, backend)
    plugins = {str(p.get("name")): p for p in _discovery_plugins(be)}
    if req.plugin not in plugins:
        raise ApiError(
            400, f"unknown discovery plugin {req.plugin!r}",
            detail={"plugins": sorted(plugins)},
        )
    problems = disc.check_config(dict(plugins[req.plugin].get("config_schema") or {}), req.config)
    if problems:
        raise ApiError(
            400, f"config rejected by plugin {req.plugin!r}'s schema: " + "; ".join(problems),
            detail={"problems": problems},
        )
    if not is_safe_catalog_id(req.imprint_id):
        raise ApiError(400, f"imprint_id {req.imprint_id!r} is not a usable catalog id")
    for name in req.sensors:
        if not isinstance(name, str) or not name.strip():
            raise ApiError(400, "sensors must be non-empty names")
    catalog_root = Path(settings.imprints_dir)
    if (catalog_root / req.imprint_id).exists():
        raise ApiError(
            409, f"imprint {req.imprint_id!r} already exists in {catalog_root}; "
            "a discovery run never overwrites a bundle -- choose another id",
        )
    if req.imprint_id in runs.active_imprint_ids():
        raise ApiError(409, f"a discovery run for imprint {req.imprint_id!r} is already in progress")

    import tempfile

    ir_store = Path(settings.aximprint_store)
    belief_store = ir_store.parent / f"{req.imprint_id}.belief.duckdb"
    scratch = Path(tempfile.mkdtemp(prefix=f"axrange-discovery-{req.imprint_id}-"))
    request = disc.DiscoveryRunRequest(
        plugin=req.plugin, config=dict(req.config), imprint_id=req.imprint_id,
        belief_store=belief_store, ir_store=ir_store, scratch_dir=scratch,
        catalog_root=catalog_root, sensors=[s.strip() for s in req.sensors],
    )
    run = runs.new(req.plugin, req.imprint_id, req.config)

    def _job() -> None:
        disc.execute_run(runs, run.id, be, request)

    if background is not None:
        background(_job)
    else:
        threading.Thread(target=_job, name=f"discovery-{run.id}", daemon=True).start()
    return _discovery_run_dto(runs.snapshot(run.id))


def discovery_run_detail(run_id: str, *, runs: Any) -> dtos.DiscoveryRunDTO:
    run = runs.snapshot(run_id)
    if run is None:
        raise ApiError(404, f"no discovery run {run_id!r}")
    return _discovery_run_dto(run)


def list_discovery_runs(*, runs: Any) -> dtos.DiscoveryRunListDTO:
    return dtos.DiscoveryRunListDTO(runs=[_discovery_run_dto(r) for r in runs.list()])


# ---------------------------------------------------------------------------
# Discovery sources (`api/Spec.md` A7)
# ---------------------------------------------------------------------------
def _source_dto(rec: Any) -> dtos.DiscoverySourceDTO:
    acc = rec.access
    return dtos.DiscoverySourceDTO(
        id=rec.id, name=rec.name, plugin=rec.plugin, endpoint=rec.endpoint,
        access=dtos.SourceAccessDTO(
            transport=acc.transport, auth=acc.auth, user=acc.user,
            ssh=dtos.SshAccessDTO(**acc.ssh.to_json()) if acc.ssh else None,
        ),
        has_password=rec.has_password, has_api_key=rec.has_api_key,
        defaults=dict(rec.defaults), last_probe=rec.last_probe,
        created_at=_iso(rec.created_at) or "", updated_at=_iso(rec.updated_at) or "",
    )


def _source_or_404(store: Any, source_id: str) -> Any:
    from axrange.api import discovery_sources as ds

    try:
        return store.get(source_id)
    except ds.SourceNotFound:
        raise ApiError(404, f"no discovery source {source_id!r}") from None


def _http_plugin_or_400(settings: ApiSettings, backend: Any, plugin: str) -> dict[str, Any]:
    plugins = {str(p.get("name")): p for p in _discovery_plugins(_discovery_backend(settings, backend))}
    if plugin not in plugins:
        raise ApiError(400, f"unknown discovery plugin {plugin!r}", detail={"plugins": sorted(plugins)})
    if (plugins[plugin].get("source_kind") or "path") != "http":
        raise ApiError(
            400, f"plugin {plugin!r} reads a path, not an endpoint; a stored source needs an "
            "http plugin", detail={"source_kind": plugins[plugin].get("source_kind") or "path"},
        )
    return plugins[plugin]


def list_discovery_sources(*, store: Any) -> dtos.DiscoverySourceListDTO:
    return dtos.DiscoverySourceListDTO(sources=[_source_dto(r) for r in store.list()])


def discovery_source_detail(source_id: str, *, store: Any) -> dtos.DiscoverySourceDTO:
    return _source_dto(_source_or_404(store, source_id))


def create_discovery_source(
    settings: ApiSettings, req: dtos.DiscoverySourceWrite, *, store: Any, backend: Any = None,
) -> dtos.DiscoverySourceDTO:
    """A7.1: refuse every malformed field by name (plugin, endpoint, access, secret,
    defaults) before anything is written; the created record carries no secret."""
    from axrange.api import discovery_sources as ds

    _http_plugin_or_400(settings, backend, req.plugin)
    try:
        endpoint = ds.validate_endpoint(req.endpoint)
        access = ds.validate_access(req.access.model_dump())
        defaults = ds.validate_defaults(req.defaults)
        rec = store.create(
            name=req.name, plugin=req.plugin, endpoint=endpoint, access=access,
            secrets={"password": req.password, "api_key": req.api_key}, defaults=defaults,
        )
    except ds.SourceError as e:
        raise ApiError(400, str(e)) from e
    except ds.SourceConflict as e:
        raise ApiError(409, str(e)) from e
    return _source_dto(rec)


def update_discovery_source(
    settings: ApiSettings, source_id: str, req: dtos.DiscoverySourceUpdate, *, store: Any,
    backend: Any = None,
) -> dtos.DiscoverySourceDTO:
    from axrange.api import discovery_sources as ds

    _source_or_404(store, source_id)
    sent = req.model_fields_set
    if req.plugin is not None:
        _http_plugin_or_400(settings, backend, req.plugin)
    try:
        kw: dict[str, Any] = {}
        if req.name is not None:
            kw["name"] = req.name
        if req.plugin is not None:
            kw["plugin"] = req.plugin
        if req.endpoint is not None:
            kw["endpoint"] = ds.validate_endpoint(req.endpoint)
        if req.access is not None:
            kw["access"] = ds.validate_access(req.access.model_dump())
        if req.defaults is not None:
            kw["defaults"] = ds.validate_defaults(req.defaults)
        secrets = {k: getattr(req, k) for k in ds.SECRET_KEYS if k in sent}
        rec = store.update(source_id, secrets=secrets, **kw)
    except ds.SourceError as e:
        raise ApiError(400, str(e)) from e
    except ds.SourceConflict as e:
        raise ApiError(409, str(e)) from e
    return _source_dto(rec)


def delete_discovery_source(source_id: str, *, store: Any, runs: Any) -> None:
    from axrange.api import discovery_sources as ds

    _source_or_404(store, source_id)
    active = runs.active_for_source(source_id)
    if active:
        raise ApiError(
            409, f"discovery source {source_id!r} has a run in progress ({', '.join(active)})",
            detail={"runs": active},
        )
    try:
        store.delete(source_id)
    except ds.SourceNotFound:
        raise ApiError(404, f"no discovery source {source_id!r}") from None


def _access_spec(store: Any, rec: Any) -> Any:
    from axrange.api import discovery_sources as ds

    return ds.AccessSpec(endpoint=rec.endpoint, access=rec.access, secrets=store.secrets(rec.id))


def probe_discovery_source(
    settings: ApiSettings, source_id: str, *, store: Any, backend: Any = None,
    tunnel_factory: Any = None,
) -> dtos.DiscoveryProbeDTO:
    """A7.2: open the access path, ask the plugin what is there, remember the answer
    on the record. Not reachable is an ANSWER (200, `reachable: false` + the reason --
    a closed tunnel, a refused key, a wrong port each read differently), not a 5xx;
    only "discovery is not configured" is."""
    import time as _time

    from axrange.api import discovery as disc
    from axrange.api import discovery_sources as ds

    rec = _source_or_404(store, source_id)
    be = _discovery_backend(settings, backend)
    spec = _access_spec(store, rec)
    answer: dict[str, Any] = {"reachable": False, "error": None, "first_ts": None,
                              "last_ts": None, "vantage": None, "extra": {}, "via": None}
    try:
        with ds.open_access(spec, tunnel_factory=tunnel_factory) as opened:
            answer["via"] = opened.via
            probe = be.probe(rec.plugin, f"{rec.plugin}:{opened.base_url}", env=opened.env)
            for k in ("reachable", "error", "first_ts", "last_ts", "vantage", "extra"):
                if k in probe:
                    answer[k] = probe[k]
            answer["reachable"] = bool(answer["reachable"])
    except (ds.AccessError, disc.DiscoveryError) as e:
        answer["error"] = str(e)
    answer["probed_at"] = _iso(_time.time()) or ""
    store.record_probe(source_id, answer)
    return dtos.DiscoveryProbeDTO(source_id=source_id, **answer)


def create_discovery_source_run(
    settings: ApiSettings, source_id: str, req: dtos.DiscoverySourceRunRequest, *,
    store: Any, runs: Any, backend: Any = None,
    background: Callable[[Callable[[], None]], None] | None = None,
    tunnel_factory: Any = None,
) -> dtos.DiscoveryRunDTO:
    """A7.3: a run whose `source` and credential come from the stored record. The
    window is `days` (resolved from the source's span when the run starts) or explicit
    bounds, never both and never neither. Everything refusable is refused before the
    access path is opened."""
    import tempfile

    from axrange.api import discovery as disc
    from axrange.api import discovery_sources as ds
    from axrange.api.imprints import is_safe_catalog_id

    rec = _source_or_404(store, source_id)
    be = _discovery_backend(settings, backend, long=True)
    plugin = _http_plugin_or_400(settings, backend, rec.plugin)

    days = req.days if req.days is not None else rec.defaults.get("days")
    explicit = req.ts_from is not None or req.ts_to is not None
    if explicit and req.days is not None:
        raise ApiError(400, "pass either days or ts_from/ts_to, not both")
    if explicit and (req.ts_from is None or req.ts_to is None or req.ts_to <= req.ts_from):
        raise ApiError(400, "ts_from and ts_to must both be set, with ts_to > ts_from")
    if not explicit and (days is None or days <= 0):
        raise ApiError(400, "pass days (> 0) or ts_from/ts_to; the source has no default days")

    config: dict[str, Any] = {"source": f"{rec.plugin}:{rec.endpoint}"}
    for key in ("network", "vantage", "window", "subbuckets"):
        value = getattr(req, key)
        if value is None:
            value = rec.defaults.get(key)
        if value not in (None, ""):
            config[key] = value
    if explicit:
        config["ts_from"], config["ts_to"] = float(req.ts_from), float(req.ts_to)
    problems = disc.check_config(dict(plugin.get("config_schema") or {}), config)
    if problems:
        raise ApiError(
            400, f"config rejected by plugin {rec.plugin!r}'s schema: " + "; ".join(problems),
            detail={"problems": problems},
        )
    sensors = req.sensors if req.sensors is not None else list(rec.defaults.get("sensors") or [])
    for name in sensors:
        if not isinstance(name, str) or not name.strip():
            raise ApiError(400, "sensors must be non-empty names")
    if not is_safe_catalog_id(req.imprint_id):
        raise ApiError(400, f"imprint_id {req.imprint_id!r} is not a usable catalog id")
    catalog_root = Path(settings.imprints_dir)
    if (catalog_root / req.imprint_id).exists():
        raise ApiError(
            409, f"imprint {req.imprint_id!r} already exists in {catalog_root}; "
            "a discovery run never overwrites a bundle -- choose another id",
        )
    if req.imprint_id in runs.active_imprint_ids():
        raise ApiError(409, f"a discovery run for imprint {req.imprint_id!r} is already in progress")
    if rec.access.transport == "ssh_tunnel" and rec.access.ssh is not None:
        key = Path(rec.access.ssh.key_path).expanduser()
        if not key.is_file():
            raise ApiError(
                400, f"ssh key {rec.access.ssh.key_path!r} is not a file on the server",
            )

    ir_store = Path(settings.aximprint_store)
    scratch = Path(tempfile.mkdtemp(prefix=f"axrange-discovery-{req.imprint_id}-"))
    request = disc.DiscoveryRunRequest(
        plugin=rec.plugin, config=config, imprint_id=req.imprint_id,
        belief_store=ir_store.parent / f"{req.imprint_id}.belief.duckdb", ir_store=ir_store,
        scratch_dir=scratch, catalog_root=catalog_root, sensors=[s.strip() for s in sensors],
    )
    spec = _access_spec(store, rec)
    run = runs.new(rec.plugin, req.imprint_id, config, source_id=source_id)

    def _job() -> None:
        disc.execute_source_run(
            runs, run.id, be, request, access_spec=spec,
            lookback_days=None if explicit else float(days), tunnel_factory=tunnel_factory,
        )

    if background is not None:
        background(_job)
    else:
        threading.Thread(target=_job, name=f"discovery-{run.id}", daemon=True).start()
    return _discovery_run_dto(runs.snapshot(run.id))


def list_imprints(settings: ApiSettings) -> dtos.ImprintListDTO:
    """The tolerant catalog listing, WITH what it could not publish (S5).

    A browsing endpoint that silently drops a bundle it cannot read leaves
    the operator with an incomplete Console and no way to find out which
    bundle vanished or why -- `skipped[]` is that channel."""
    from axrange.api.imprints import list_imprints_with_skips

    fixtures_dir = settings.imprints_dir or None
    entries, skipped = list_imprints_with_skips(fixtures_dir)
    return dtos.ImprintListDTO(
        imprints=[
            dtos.ImprintListItemDTO(
                id=e.id, hosts=e.hosts, segments=e.segments,
                as_of=e.as_of, mean_confidence=e.mean_confidence,
                has_provenance=e.has_provenance,
                confidence_source=e.confidence_source,
                has_topology_export=e.has_topology_export,
                **_apply_capability(settings, e.id),
            )
            for e in entries
        ],
        skipped=[
            dtos.ImprintSkippedDTO(id=i, reason=r) for i, r in sorted(skipped)
        ],
    )


def imprint_summary(settings: ApiSettings, imprint_id: str) -> dtos.ImprintSummaryDTO:
    from axrange.api.imprints import ImprintDataError, ImprintNotFoundError, load_imprint_summary

    fixtures_dir = settings.imprints_dir or None
    try:
        s = load_imprint_summary(imprint_id, fixtures_dir=fixtures_dir)
    except ImprintNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e

    return dtos.ImprintSummaryDTO(
        **_apply_capability(settings, imprint_id),
        id=s.id,
        as_of=s.as_of,
        store=s.store,
        days_observed=s.days_observed,
        windows=s.windows,
        hosts=s.hosts,
        segments=s.segments,
        services=s.services,
        personas=s.personas,
        mean_confidence=s.mean_confidence,
        out_of_vocab_pct=s.out_of_vocab_pct,
        topology_segments=[
            dtos.ImprintSegmentDTO(
                name=seg.name, cidr=seg.cidr, host_count=seg.host_count,
                confidence=seg.confidence,
            )
            for seg in s.topology_segments
        ],
        # R7 (GAP-082): what was parsed, where the confidence came from, and
        # every statement the reader publishes instead of refusing.
        sidecar_shape=s.sidecar_shape,
        confidence_source=s.confidence_source,
        warnings=list(s.warnings),
    )


def imprint_provenance(settings: ApiSettings, imprint_id: str) -> dtos.ImprintProvenanceDTO:
    """S1.4: BOTH sidecar shapes, plus which one was read.

    `load_provenance` detects `canonical` / `normalized` / `unrecognized` /
    `absent` and reads each with the reader that owns it (the canonical one
    through `axrange.fidelity.reference`, never a third parser; an
    unrecognized `schema` is present and NOT consumed). An ABSENT sidecar is
    a 200 with zero rows and `sidecar_shape: "absent"`, not a 400 -- a real
    cast bundle installed before the sidecar was copied legitimately has
    none, and the Console must render it while saying so.
    """
    from axrange.api.imprints import (
        ImprintDataError,
        ImprintNotFoundError,
        load_provenance,
    )

    fixtures_dir = settings.imprints_dir or None
    try:
        doc = load_provenance(imprint_id, fixtures_dir=fixtures_dir)
    except ImprintNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e

    return dtos.ImprintProvenanceDTO(
        imprint_id=doc.imprint_id,
        sidecar_shape=doc.sidecar_shape,
        sidecar_schema=doc.sidecar_schema,
        evidence_source=doc.evidence_source,
        code_fingerprint=doc.code_fingerprint,
        population=doc.population,
        warnings=doc.warnings,
        rows=[
            dtos.ImprintProvenanceRowDTO(
                field=r.field, signal=r.signal, evidence_class=r.evidence_class,
                confidence=r.confidence, evidence=r.evidence,
                has_confidence=r.has_confidence, source=r.source, note=r.note,
                overrides=(
                    dtos.ProvenanceOverrideDTO(
                        evidence_class=r.overrides.evidence_class,
                        value=r.overrides.value,
                        confidence=r.overrides.confidence,
                        drift=r.overrides.drift,
                    )
                    if r.overrides else None
                ),
            )
            for r in doc.rows
        ],
    )


# ---------------------------------------------------------------------------
# Imprints -- "deploy as twin" (aximprint-integration Track C slice 2,
# `docs/WORK_ORDER_aximprint_console.md` PART A). `POST /api/imprints/
# {imprint_id}/plan` (pure preview) and `POST /api/imprints/{imprint_id}/
# deploy` (the real thing). NEITHER function below re-implements any part of
# the validate/plan/lease/executor sequencing -- `imprint_plan` calls the
# SAME `_validated_plan_for_path` the ordinary `plan()`/`create_deploy`
# functions above call, and `imprint_deploy` is a pass-through to
# `create_deploy` itself (LES-037). The only new code on this path is
# `_resolve_imprint_scenario_path`, which resolves an imprint id against the
# imprints fixture catalog instead of the twin-configs catalog -- everything
# downstream of "here is a materialized IR file" is 100% shared.
# ---------------------------------------------------------------------------
def imprint_plan(settings: ApiSettings, imprint_id: str, *,
                 target_node: Optional[str] = None,
                 params: Optional[dict[str, str]] = None) -> dtos.PlanSummaryDTO:
    """Validate + plan an imprint's drafted twin_config -- PURE (no state.db
    write, no lease, no execution): the Console's "preview before you
    commit" step, and what makes PART A unit-testable with no cluster.
    Mirrors `plan()` exactly, just sourced from an imprint id instead of a
    scenario id -- same `ApiError` shapes (404 unknown imprint via
    `_resolve_imprint_scenario_path`; 422 on a validation failure, via
    `_validated_plan_for_path`, matching this module's own established
    validate-failure convention -- see `plan()`/`create_deploy`'s identical
    use of it above; never a 500/traceback for an author-shaped input
    error)."""
    # `config_warnings`: an imprint's `<id>.yaml` IS a twin_config, so its
    # expansion runs the deploy gate -- and a PREVIEW that reports a
    # gated-down op count with no word about the gate is precisely the
    # silent-skip this project refuses (LES-089). The ordinary `plan()` above
    # is scenario-sourced and has no expansion step, so it leaves the field
    # at its `[]` default.
    config_warnings: list[str] = []
    sp, images_root, source_dir = _resolve_imprint_scenario_path(
        settings, imprint_id, params, warnings=config_warnings,
    )
    # Relative scripts resolve against the twin config's own directory, and
    # the materialized tempfile is deleted on the way out (GAP-082 review,
    # M2 and S3) -- a preview leaves nothing behind.
    p, _cfg, _ctx, _ir, _raw, _sp, _manifests = _plan_materialized(
        settings, sp, images_root, source_dir, target_node=target_node,
    )
    from axrange.api.imprints import default_fixtures_dir

    return _plan_summary_dto(
        p, config_warnings=config_warnings,
        params=_resolved_params(
            settings.imprints_dir or str(default_fixtures_dir()), imprint_id, params,
        ),
    )


def imprint_deploy(settings: ApiSettings, imprint_id: str, req: dtos.ImprintDeployRequest, *,
                   runner: Optional[DeployRunner] = None,
                   background: Callable[[Callable[[], None]], None] | None = None,
                   override_protection: Optional[str] = None,
                   ) -> dtos.CreateDeployResponse:
    """"Deploy as twin": hand the imprint's drafted twin_config to the
    EXISTING async lease-guarded deploy path -- exactly as an ordinary
    `te deploy --config <id>` would, via the SAME `create_deploy` every
    other deploy source (`scenario`/`config`) already goes through (no
    second planner/executor wiring; `docs/WORK_ORDER_aximprint_console.md`
    PART A's hard constraint). `runner`/`background` are threaded straight
    through for the SAME test-seam reasons `create_deploy` documents them."""
    return create_deploy(
        settings,
        dtos.CreateDeployRequest(
            imprint=imprint_id,
            params=req.params,
            target_node=req.target_node,
            fresh=req.fresh,
            update=req.update,
            allow_destroy=req.allow_destroy,
            expect_plan_id=req.expect_plan_id,
        ),
        runner=runner,
        background=background,
        override_protection=override_protection,
    )


# ---------------------------------------------------------------------------
# The Console canvas (`docs/WORK_ORDER_console_canvas.md` S1; `docs/design/
# 15-console-canvas.md` §10) -- the imprint-scoped read + edit surface the
# assertions SPA binds to.
#
# Two rules hold across all seven functions. (1) Every path into a bundle
# goes through `_imprint_bundle_dir` -> `axrange.api.imprints.
# check_imprint_addressable`, so ONE traversal guard and ONE addressing guard
# cover them all (`../../etc` 404s everywhere, and a renamed bundle 400s
# everywhere, not just where someone remembered). (2) axRange VALIDATES and
# WRITES the operator's files; it never interprets precedence, and it never
# reads the layout file for anything but serving it back.
# ---------------------------------------------------------------------------
def _apply_capability(settings: ApiSettings, imprint_id: str) -> dict[str, Any]:
    """`{can_apply, apply_blocked_reason}` for an imprint -- the SAME checks
    `imprint_apply` refuses on, in the same order, evaluated read-only so
    the Console can say why Apply is unavailable instead of guessing (fix
    round 1). The cheap settings checks run before the one file read."""
    from axrange.api import recompile as rc
    from axrange.twinconfig.bundle import is_twin_folder

    try:
        d = _imprint_bundle_dir(settings, imprint_id)
    except ApiError as e:
        return {"can_apply": False, "apply_blocked_reason": e.message}
    if is_twin_folder(d):
        why = ("a twins/ folder: apply re-runs aximprint's install-cast, which writes "
               "the legacy cast-bundle layout, not te.yaml + generated/")
    elif not settings.aximprint_bin:
        why = f"recompile is not configured: {rc.SETTING_BIN} is unset"
    elif not settings.aximprint_store:
        why = f"recompile is not configured: {rc.SETTING_STORE} is unset"
    else:
        try:
            rc.read_cast_invocation(d, imprint_id)
            why = None
        except rc.RecompileInvocationError as e:
            why = str(e)
    return {"can_apply": why is None, "apply_blocked_reason": why}


def _imprint_bundle_dir(settings: ApiSettings, imprint_id: str) -> Path:
    """THE funnel every Console route that reads or writes a bundle by id
    goes through: the traversal guard AND the addressing guard
    (`imprints.check_imprint_addressable`) -- 404 for no such imprint, 400
    for a directory whose `twin_config.id` names another twin (LES-195).
    Until the GAP-082 review (C) this resolved the directory alone, so the
    assertions and layout routes (and apply, and diff) served -- and wrote
    -- a renamed bundle's operator files under an id every other route
    refused."""
    from axrange.api.imprints import (
        ImprintDataError,
        ImprintNotFoundError,
        check_imprint_addressable,
    )

    try:
        return check_imprint_addressable(
            imprint_id, fixtures_dir=settings.imprints_dir or None,
        )
    except ImprintNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e


def _console_file_error(e: Exception) -> ApiError:
    """One mapping for both operator files.

    412 for a stale/missing etag (**a deviation from the work order §3's 409,
    taken on the task instruction of 2026-09-05**): the guard IS an HTTP
    precondition, and it is accepted in the body as well as in `If-Match` so
    a header-stripping proxy cannot silently turn a guarded write into an
    unguarded one -- which was §3's actual concern, and is preserved.

    503, not 400, for a WRITE failure (M5): a read-only bundle directory or a
    full disk is the server's environment, not the operator's document, and
    telling them their edit was malformed sends them to fix the wrong thing.
    """
    from axrange.api.console_files import (
        ConsoleFileConflictError,
        ConsoleFileShapeError,
        ConsoleFileWriteError,
    )

    if isinstance(e, ConsoleFileConflictError):
        return ApiError(412, str(e), detail={"etag": e.on_disk_etag})
    if isinstance(e, ConsoleFileWriteError):
        return ApiError(503, str(e))
    if isinstance(e, ConsoleFileShapeError):
        return ApiError(400, str(e))
    return ApiError(400, str(e))


#: Where an etag may legitimately arrive, in the order a message names them.
_ETAG_PLACES = ("If-Match", "the body's `etag`", "the document's `etag`")


def _etag_from(if_match: Optional[str], *bodies: Any) -> Optional[str]:
    """THE etag resolution, shared by both PUTs and by apply (S8/N4).

    THREE placements, all equal: the `If-Match` header (§3's
    header-stripping-proxy argument is why the header alone is not enough),
    the request body's `etag`, and -- on apply, whose body wraps the document
    -- the DOCUMENT's own `etag`. That last one is N4: both PUTs accepted a
    document carrying its etag inline, because a GET payload carries one and
    the SPA's save is "PUT what you read"; apply did not, so the identical
    payload was guarded through one route and unguarded through the other.

    Supplying more than one with DIFFERENT values is a refusal, not a silent
    preference -- guessing which the caller meant is exactly the ambiguity
    the guard exists to remove."""
    from axrange.api.console_files import normalize_etag

    def _of(source: Any) -> Optional[str]:
        if isinstance(source, dict):
            raw = source.get("etag")
        else:
            raw = getattr(source, "etag", None)
        return normalize_etag(raw) if isinstance(raw, str) else None

    found: list[tuple[str, str]] = []
    header = normalize_etag(if_match)
    if header:
        found.append((_ETAG_PLACES[0], header))
    for i, body in enumerate(bodies):
        value = _of(body)
        if value:
            found.append((_ETAG_PLACES[min(i + 1, len(_ETAG_PLACES) - 1)], value))
    distinct = {v for _p, v in found}
    if len(distinct) > 1:
        named = ", ".join(f"{place} ({value!r})" for place, value in found)
        raise ApiError(
            400,
            f"the etags disagree -- {named}. Send one, or the same value in "
            "each; guessing which you meant is what the guard exists to avoid",
        )
    return found[0][1] if found else None


def _assertions_dto(doc: Any) -> dtos.AssertionsDocDTO:
    return dtos.AssertionsDocDTO(
        imprint_id=doc.imprint_id,
        path=str(doc.path),
        exists=doc.exists,
        etag=doc.etag,
        schema=doc.schema,
        imprint=doc.imprint,
        as_of=doc.as_of,
        source=doc.source,
        add=[dtos.AssertionRecordDTO(**r) for r in doc.add],
        override=[dtos.AssertionRecordDTO(**r) for r in doc.override],
        suppress=[dtos.AssertionRecordDTO(**r) for r in doc.suppress],
        warnings=list(doc.warnings),
    )


def _layout_dto(doc: Any) -> dtos.LayoutDocDTO:
    return dtos.LayoutDocDTO(
        imprint_id=doc.imprint_id, path=str(doc.path), exists=doc.exists,
        etag=doc.etag, schema=doc.schema, imprint=doc.imprint, pins=doc.pins,
        plane=doc.plane,
    )


def imprint_topology(settings: ApiSettings, imprint_id: str) -> dtos.TopologyExportDTO:
    """S1.1 / decision **D1 (a)**: a thin ALIAS of
    `GET /api/scenarios/{id}/topology-export?source=imprint`, which already
    serves this DTO from this reader. No second reader, no second service
    function -- the route exists because `15` §10's table names it and the
    SPA should not have to know that an imprint is reachable through the
    scenarios path (LES-025/LES-037)."""
    return topology_export(settings, imprint_id, source="imprint")


def imprint_cast(settings: ApiSettings, imprint_id: str) -> dtos.CastSummaryDTO:
    """S1.2: the compiled bundle summary."""
    from pydantic import ValidationError

    from axrange.api.imprints import ImprintDataError, ImprintNotFoundError, load_cast_summary

    try:
        s = load_cast_summary(imprint_id, fixtures_dir=settings.imprints_dir or None)
    except ImprintNotFoundError as e:
        raise ApiError(404, str(e)) from e
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e
    from axrange.api import recompile as rc

    try:
        return _cast_summary_dto(
            s,
            # R4's change key, computed off the directory the summary itself
            # resolved -- the value an apply reports as `fingerprint_after`.
            bundle_fingerprint=rc.bundle_fingerprint(Path(s.bundle_dir), s.id),
        )
    except ValidationError as e:
        # The last line of M1: a bundle value the reader passed through that
        # the published type rejects is a 400 naming it, never a 500.
        raise ApiError(400, _dto_type_refusal(f"imprint {imprint_id!r}", e)) from e


def _cast_summary_dto(
    s: Any, *, bundle_fingerprint: Optional[str] = None,
) -> dtos.CastSummaryDTO:
    return dtos.CastSummaryDTO(
        id=s.id, bundle_dir=s.bundle_dir, twin_config_id=s.twin_config_id,
        description=s.description, pit_id=s.pit_id, bundle_schema=s.bundle_schema,
        topology_schema_version=s.topology_schema_version,
        code_fingerprint=s.code_fingerprint,
        bundle_fingerprint=bundle_fingerprint, params=s.params,
        deploy_min_occupancy=s.deploy_min_occupancy,
        deploy_min_occupancy_source=s.deploy_min_occupancy_source,
        segments=s.segments, node_specs=s.node_specs, vms=s.vms, hosts=s.hosts,
        conduits=s.conduits, observed_nodes=s.observed_nodes,
        observed_edges=s.observed_edges, observed_segments=s.observed_segments,
        census=s.census, has_provenance=s.has_provenance,
        has_topology_export=s.has_topology_export,
        has_assertions=s.has_assertions, has_layout=s.has_layout,
        provenance_schema=s.provenance_schema, provenance_path=s.provenance_path,
        sidecar_shape=s.sidecar_shape, evidence_source=s.evidence_source,
        warnings=s.warnings,
        # R5 (GAP-079): the roster behind the `conduits` count + every
        # router with its legs (per router, the GAP-082 review's S5), for
        # the Console's east-west band and WAN box.
        conduit_roster=[
            dtos.ConduitDTO(from_=c.from_, to=c.to) for c in s.conduit_roster
        ],
        routers=[
            dtos.RouterDTO(name=r.name, segments=list(r.segments)) for r in s.routers
        ],
        # Console S5.1.a: the roster behind the `segments` count -- the
        # deployed CIDR and zone type per segment, and (by name) which
        # observed segments the cast pruned.
        segment_roster=[
            dtos.CastSegmentDTO(name=g.name, cidr=g.cidr, zone_type=g.zone_type)
            for g in s.segment_roster
        ],
        # Unified Console: the node specs, for the declared-design map.
        node_roster=[
            dtos.CastNodeDTO(
                name=n.name, kind=n.kind, role=n.role, count=n.count,
                segments=list(n.segments), node_ids=list(n.node_ids),
            )
            for n in s.node_roster
        ],
    )


def imprint_assertions(settings: ApiSettings, imprint_id: str) -> dtos.AssertionsDocDTO:
    """S1.3 GET. A bundle with no assertions file yet is a 200 with empty
    lists and `etag: null` -- the first save creates it."""
    from axrange.api.console_files import ConsoleFileError, read_assertions

    d = _imprint_bundle_dir(settings, imprint_id)
    try:
        return _assertions_dto(read_assertions(d, imprint_id))
    except ConsoleFileError as e:
        raise _console_file_error(e) from e


def put_imprint_assertions(
    settings: ApiSettings, imprint_id: str, body: dict[str, Any], *,
    if_match: Optional[str] = None,
) -> dtos.AssertionsDocDTO:
    """S1.3 PUT: validate the closed record set, refuse a stale etag, write.

    Refused with 409 while an APPLY is running (round 3 nit): a plain save
    landing between an apply's own save and its recompile would leave the
    apply reporting an assertions document that is no longer the one on disk,
    and the recompile would read the other operator's file. 409 rather than
    the etag guard's 412 because nothing is stale -- the resource is busy.

    It is a CHECK, not a competing acquire: two plain PUTs must still answer
    each other with the etag guard's recoverable 412 (which names the on-disk
    etag), not with "try later". See `recompile.apply_in_progress` for the
    TOCTOU window that buys."""
    from axrange.api import recompile as rc
    from axrange.api.console_files import ConsoleFileError, write_assertions

    d = _imprint_bundle_dir(settings, imprint_id)
    if not isinstance(body, dict):
        raise ApiError(400, "body must be an assertions document (a JSON object)")
    etag = _etag_from(if_match, body)
    if rc.apply_in_progress(imprint_id):
        raise ApiError(
            409,
            f"an apply of imprint {imprint_id!r} is running -- this save is "
            "refused rather than landing between that apply's own save and "
            "its recompile, which would make the apply report a document that "
            "is no longer on disk. Retry when the apply finishes",
        )
    try:
        return _assertions_dto(write_assertions(d, imprint_id, body, if_match=etag))
    except ConsoleFileError as e:
        raise _console_file_error(e) from e


def imprint_layout(settings: ApiSettings, imprint_id: str) -> dtos.LayoutDocDTO:
    """S1.6 GET -- the Console's pins. Never evidence."""
    from axrange.api.console_files import ConsoleFileError, read_layout

    d = _imprint_bundle_dir(settings, imprint_id)
    try:
        return _layout_dto(read_layout(d, imprint_id))
    except ConsoleFileError as e:
        raise _console_file_error(e) from e


def put_imprint_layout(
    settings: ApiSettings, imprint_id: str, body: dict[str, Any], *,
    if_match: Optional[str] = None,
) -> dtos.LayoutDocDTO:
    """S1.6 PUT. Same etag discipline as the assertions file; a `host:` pin
    key is refused (hosts are never pinnable)."""
    from axrange.api.console_files import ConsoleFileError, write_layout

    d = _imprint_bundle_dir(settings, imprint_id)
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a layout document (a JSON object)")
    etag = _etag_from(if_match, body)
    try:
        return _layout_dto(write_layout(d, imprint_id, body, if_match=etag))
    except ConsoleFileError as e:
        raise _console_file_error(e) from e


def imprint_apply(
    settings: ApiSettings, imprint_id: str, req: dtos.ImprintApplyRequest, *,
    if_match: Optional[str] = None, backend: Any = None,
) -> dtos.ImprintApplyResponse:
    """S1.5 / decision **D2 (a)**: apply IS save + recompile + install.

    Sequence, and the order matters: save FIRST (so a compile that fails
    still leaves the operator's edits on disk -- `14` §4's save/apply split
    exists precisely so work is never lost to a compiler), snapshot the
    bundle under its CURRENT content fingerprint (S1.7's diff input), then
    run both compiler stages into a SCRATCH directory and let the install
    stage put the result back under `<id>.*` (**R3** -- a cast writes generic
    stems, so a one-stage apply pointed at the bundle changed nothing this
    API could see).

    Every failure AFTER the save carries `detail.etag` (**S9**): the file on
    disk moved, so an SPA holding the pre-save etag would be refused on its
    next write with no way to recover but a reload.

    The route is a plain `def`, so Starlette runs this in a worker thread and
    the subprocesses never block the event loop; a per-imprint lock refuses a
    second concurrent apply (409) rather than letting two compilers write one
    bundle directory. `backend` is the test seam AND the D2 (b)/(c) door: a
    helper process or the aximprint watcher replaces `SubprocessRecompile`
    with nothing else here changing.
    """
    import shutil
    import tempfile

    from axrange.api import recompile as rc
    from axrange.api.console_files import ConsoleFileError, write_assertions
    from axrange.api.imprints import (
        ImprintDataError,
        ImprintNotFoundError,
        cast_summary_from_bundle,
        load_reference,
    )

    d = _imprint_bundle_dir(settings, imprint_id)
    from axrange.twinconfig.bundle import is_twin_folder

    if is_twin_folder(d):
        # Refused BEFORE the save: `aximprint install-cast` writes the legacy
        # layout (`<id>.yaml` + id-stemmed sidecars at the root), so an apply
        # into a `twins/<id>/` folder would land its output beside `te.yaml`
        # where no reader looks -- a "success" that changed nothing
        # (`docs/WORK_ORDER_twin_consolidation.md` §7 D4). The plain save
        # route still writes the assertions document.
        raise ApiError(
            422,
            f"imprint {imprint_id!r} is a twins/ folder; apply re-runs aximprint's "
            "install-cast, which writes the legacy cast-bundle layout, not te.yaml + "
            "generated/ -- recompile into a legacy bundle directory and file it in, "
            "until the installer (or an axRange import step) learns the twin layout "
            "(docs/WORK_ORDER_twin_consolidation.md §7 D4)",
        )
    document = req.document if isinstance(req.document, dict) else None
    if document is None:
        raise ApiError(
            400,
            "body must carry `document` -- the assertions document this apply "
            "saves before recompiling (apply IS save + recompile, docs/design/"
            "14-operator-assertions.md §4)",
        )
    etag = _etag_from(if_match, req, document)
    saved = None
    scratch: Optional[Path] = None

    def _fail(status: int, message: str, detail: Optional[dict[str, Any]] = None) -> ApiError:
        """S9: every post-save failure hands back the etag of the file that
        IS now on disk, so the SPA is not left holding a stale one."""
        payload = dict(detail or {})
        if saved is not None:
            payload.setdefault("etag", saved.etag)
        return ApiError(status, message, detail=payload or None)

    try:
        with rc.imprint_apply_lock(imprint_id):
            # ---- save (S1.3's writer, unchanged) -----------------------
            try:
                saved = write_assertions(d, imprint_id, document, if_match=etag)
            except ConsoleFileError as e:
                raise _console_file_error(e) from e

            # ---- what the bundle IS right now (ONE load, S4) -----------
            try:
                bundle = load_reference(imprint_id, settings.imprints_dir or None)
            except ImprintNotFoundError as e:  # pragma: no cover -- resolved above
                raise _fail(404, str(e)) from e
            except ImprintDataError as e:
                raise _fail(400, str(e)) from e
            before = cast_summary_from_bundle(bundle, imprint_id)
            fingerprint_before = rc.bundle_fingerprint(d, imprint_id)

            try:
                # The export is already parsed on `bundle`; hand the raw
                # block over rather than re-reading it (round 3 nit).
                invocation = rc.read_cast_invocation(
                    d, imprint_id, block=bundle.cast_invocation,
                )
            except rc.RecompileInvocationError as e:
                raise _fail(422, str(e)) from e
            if not settings.aximprint_bin:
                raise _fail(
                    503,
                    "recompile is not configured: set "
                    f"{rc.SETTING_BIN} to the aximprint executable",
                )
            if not settings.aximprint_store:
                raise _fail(
                    503,
                    "recompile is not configured: set "
                    f"{rc.SETTING_STORE} to the imprint's belief store "
                    "(ir.duckdb)",
                )

            # Bookkeeping, NOT part of the apply's contract: a snapshot that
            # cannot be written degrades the post-apply highlight (S1.7) and
            # must never fail the recompile the operator asked for. Reported
            # in `warnings`, never swallowed.
            history_written = False
            history_error: Optional[str] = None
            if fingerprint_before:
                try:
                    rc.write_history_snapshot(
                        d, fingerprint_before,
                        rc.snapshot_bundle(bundle, fingerprint=fingerprint_before),
                    )
                    history_written = True
                except Exception as e:  # noqa: BLE001 -- bookkeeping, never fatal
                    history_error = (
                        f"could not record a pre-apply bundle snapshot for "
                        f"fingerprint {fingerprint_before!r}: {e} -- "
                        "`GET .../diff?since=` cannot answer for this apply"
                    )

            # ---- recompile + install -----------------------------------
            impl = backend or rc.SubprocessRecompile(
                settings.aximprint_bin, timeout_s=settings.aximprint_timeout_s,
            )
            scratch = Path(tempfile.mkdtemp(prefix=f"axr-cast-{imprint_id}-"))
            sensors, sensor_warnings = rc.sensors_from_tap_plan(d)
            request = rc.RecompileRequest(
                imprint_id=imprint_id, bundle_dir=d, catalog_root=d.parent,
                assertions_path=saved.path, store=settings.aximprint_store,
                out_dir=scratch, invocation=invocation, sensors=sensors,
            )
            try:
                result = impl.recompile(request)
            except rc.RecompileUnavailableError as e:
                raise _fail(503, str(e)) from e
            except rc.RecompileShapeError as e:
                raise _fail(
                    422, str(e), {"stderr": rc.stderr_tail(e.result.stderr)},
                ) from e
            except rc.RecompileFailedError as e:
                raise _fail(
                    502, str(e),
                    {"stderr": rc.stderr_tail(e.result.combined_stderr) if e.result else ""},
                ) from e
            fingerprint_after = rc.bundle_fingerprint(d, imprint_id)
            rc.check_output_landed(
                request, result, before=fingerprint_before, after=fingerprint_after,
            )

            # ---- what the bundle IS now (one more load) ----------------
            # INSIDE the lock, beside `fingerprint_after`: read after the
            # release, a concurrent apply of this imprint could finish in the
            # gap and leak ITS build / PIT stamp into THIS response (the
            # fingerprints would describe one apply and the summary another).
            try:
                # The bundle is kept, not only its summary: GAP-087's
                # comparison needs the raw `cast_invocation` off the SAME load
                # (S4 -- one read per artifact per request). Both calls stay
                # inside this `except`: the summary itself raises
                # `ImprintDataError` on a malformed
                # `params.DEPLOY_MIN_OCCUPANCY`, which is a 400, not a 500.
                after_bundle = load_reference(
                    imprint_id, settings.imprints_dir or None,
                )
                after = cast_summary_from_bundle(after_bundle, imprint_id)
            except ImprintDataError as e:
                raise _fail(400, str(e)) from e

            # GAP-087: did the COMPILER change under this apply? Read from the
            # RAW `cast_invocation` block on both sides -- the same lenient
            # rule for each, so a coercion difference can never fabricate a
            # "changed" -- and reported rather than refused. `bundle` is the
            # pre-apply load; nothing else in this function may re-read it,
            # or the "before" side would be the file the recompile just
            # replaced. `bundle_changed` lets an undetermined verdict drop its
            # "a population difference may be the compiler's" clause when
            # nothing moved at all.
            compiler = rc.compare_compiler_builds(
                bundle.cast_invocation, after_bundle.cast_invocation,
                bundle_changed=fingerprint_before != fingerprint_after,
            )
    except rc.ApplyInProgressError as e:
        raise ApiError(409, str(e)) from e
    finally:
        # LES-199: the scratch directory is this call's to delete, on the
        # success and the failure path alike -- a cast bundle is megabytes,
        # and an apply that failed still wrote one.
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    warnings = list(sensor_warnings) + list(result.warnings) + rc.caveats_from(
        result.document
    )
    # The compiler verdict is NOT folded into `warnings`: it is published on
    # its own `compiler_note` field, and a client printing both would say it
    # twice.
    if history_error:
        warnings.append(history_error)
    elif not history_written:
        warnings.append(
            "the bundle carried none of its canonical files before this "
            "apply, so no content fingerprint and no history snapshot could "
            "be written -- `GET .../diff?since=` has nothing to compare"
        )
    return dtos.ImprintApplyResponse(
        imprint_id=imprint_id,
        fingerprint_before=fingerprint_before,
        fingerprint_after=fingerprint_after,
        changed=fingerprint_before != fingerprint_after,
        # GAP-088: the PIT row's build stamp (the CAPTURE's identity), under
        # the name it has always had a value for; and, beside it, the build
        # that actually ran each cast.
        pit_code_fingerprint_before=before.code_fingerprint,
        pit_code_fingerprint_after=after.code_fingerprint,
        cast_code_fingerprint_before=compiler.before,
        cast_code_fingerprint_after=compiler.after,
        compiler_changed=compiler.changed,
        compiler_note=compiler.note,
        assertions=_assertions_dto(saved),
        record_counts=rc.record_counts_from(result.document),
        cast_counts=rc.cast_counts_from(result.document),
        stdout_json=_capped_document(result.document),
        stderr=result.combined_stderr,
        returncode=result.returncode,
        argv=result.argv,
        install_argv=result.install_argv,
        install_returncode=result.install_returncode,
        history_written=history_written,
        warnings=warnings,
    )


def _capped_document(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """S7/LES-206: the compiler's stdout document is republished verbatim,
    but a producer that prints a 40 MB `caveats[]` must not put 40 MB in a
    JSON response body. Capped by serialized size, with the elision stated in
    the document itself rather than silently."""
    from axrange.api.recompile import MAX_STREAM_CHARS

    if doc is None:
        return None
    try:
        text = json.dumps(doc)
    except (TypeError, ValueError):  # pragma: no cover -- it came from json
        return None
    if len(text) <= MAX_STREAM_CHARS:
        return doc
    return {
        "_elided": (
            f"the recompiler's stdout document is {len(text)} characters, "
            f"over the {MAX_STREAM_CHARS}-character cap this API republishes; "
            "the derived `record_counts`/`cast_counts` are unaffected"
        ),
        **{k: v for k, v in doc.items() if isinstance(v, (str, int, float, bool))},
    }


def imprint_diff(
    settings: ApiSettings, imprint_id: str, *, since: str,
) -> dtos.ImprintDiffDTO:
    """S1.7: the current bundle against a snapshot an APPLY recorded.

    **Both populations** (G2). `observed.*` is what the vantage saw;
    `twin.*` is what the cast emitted. An assertion cannot move the observed
    side -- a `suppress` removes a host from the TWIN while every observed
    node stays -- so a one-sided diff answered "nothing changed" for the
    first edit the Console's edit set leads with, and then explained the
    silence with a claim about WHICH kind of change it was. This endpoint
    reports what it measured and nothing else.

    A section either snapshot cannot supply is OMITTED with a warning, never
    reported as an empty diff (N3): an older, unversioned snapshot genuinely
    cannot say whether the twin moved.

    `suppressed_keys` is the third section and the one an EDITOR reads: the
    authored keys whose suppression state moved, which is how the Console
    joins a twin-side host removal back to the record that caused it (§12.6,
    settled in the Console's S2 -- `rc.suppressed_key_delta`).

    The honest limit, stated in the 404 rather than papered over: **only
    apply-produced fingerprints are diffable**. Nothing else writes
    `<bundle>/.history/`, and inventing a "nothing changed" answer for an
    unknown fingerprint would let the SPA highlight nothing and call it a
    successful comparison. A snapshot that EXISTS but cannot be parsed says
    so in its own words (S3) rather than reporting as absent while being
    listed as known."""
    from axrange.api import recompile as rc
    from axrange.api.imprints import ImprintDataError, load_reference

    d = _imprint_bundle_dir(settings, imprint_id)
    if not since:
        raise ApiError(400, "`since` is required: the fingerprint to compare against")
    try:
        snapshot = rc.load_history_snapshot(d, since)
    except ValueError as e:
        raise ApiError(400, str(e)) from e
    except rc.HistorySnapshotUnreadable as e:
        raise ApiError(
            404,
            f"imprint {imprint_id!r}: the bundle snapshot for fingerprint "
            f"{since!r} EXISTS but cannot be read ({e}) -- it is listed as "
            "known and cannot be compared; delete it, or re-apply to record a "
            "fresh one",
        ) from e
    if snapshot is None:
        known = rc.list_history(d)
        raise ApiError(
            404,
            f"imprint {imprint_id!r}: no bundle snapshot for fingerprint "
            f"{since!r} -- only fingerprints produced by an apply on THIS "
            f"host are diffable (known: {known or 'none'})",
        )

    try:
        current = load_reference(imprint_id, settings.imprints_dir or None)
    except ImprintDataError as e:
        raise ApiError(400, str(e)) from e
    fingerprint = rc.bundle_fingerprint(d, imprint_id)

    after = rc.snapshot_bundle(current, fingerprint=fingerprint)
    warnings: list[str] = []
    recorded = rc.snapshot_version(snapshot)
    if recorded != rc.SNAPSHOT_SCHEMA:
        # N3: an unknown version is UNKNOWN. Comparing anyway would report
        # every key of every section as added or removed -- a diff that looks
        # like a catastrophic change and is a schema mismatch.
        return dtos.ImprintDiffDTO(
            imprint_id=imprint_id, since=since, current_fingerprint=fingerprint,
            snapshot_schema=recorded,
            source=str(snapshot.get("source") or "absent"),
            observed=None, twin=None, twin_config_changed=False,
            warnings=[
                f"the recorded snapshot declares "
                f"{recorded or 'no schema (it predates snapshot versioning)'}, "
                f"which this axRange cannot read -- it speaks "
                f"{rc.SNAPSHOT_SCHEMA}. Nothing is compared: reporting an "
                "empty diff for a snapshot in an unknown shape would be a "
                "claim, not a measurement. Re-apply to record a fresh one"
            ],
        )

    diff, section_warnings = rc.diff_snapshots(snapshot, after)
    warnings.extend(section_warnings)
    # The authored-key join (§12.6, settled in S2): the twin side names twin
    # node ids, so a `suppress` the operator keyed on `address:10.1.10.222`
    # shows there as some `generic-endpoint-*` host disappearing. This is the
    # only faithful correspondence the data supports -- see
    # `rc.suppressed_key_delta` for why a per-removal column is not.
    suppressed, suppressed_warnings = rc.suppressed_key_delta(snapshot, after)
    warnings.extend(suppressed_warnings)
    source = str(snapshot.get("source") or "absent")
    if source != "topology export" or after["source"] != "topology export":
        warnings.append(
            "one side of this diff has no topology export, so its OBSERVED "
            "entity maps are empty -- the result is not a statement that "
            "nothing changed"
        )

    before_hash = snapshot.get("twin_config_sha256")
    after_hash = after.get("twin_config_sha256")
    twin_config_changed = bool(
        before_hash and after_hash and before_hash != after_hash
    )
    moved = any(
        section["added"] or section["removed"] or section["changed"]
        for side in diff.values() for section in side.values()
    ) or bool(
        # The suppressed-key set counts as movement: a `suppress` that
        # matched nothing leaves both populations untouched, and telling the
        # canvas there is "nothing to highlight" while `suppressed_keys`
        # names its key is the same silence G2 fixed for the twin side.
        suppressed and (suppressed["added"] or suppressed["removed"])
    )
    if twin_config_changed and not moved:
        # Says WHAT WAS MEASURED and stops. The earlier wording -- "this
        # apply moved a field (an image, a size, a role) rather than the
        # population" -- was a deduction from a one-sided snapshot, and it
        # was false the first time a real suppress hit it.
        warnings.append(
            "the twin config changed (its bytes moved) but no entity was "
            "added, removed or altered on either side, so there is nothing "
            "for the canvas to highlight -- a field-level edit, or a change "
            "this snapshot does not model. Which of the two, this endpoint "
            "does not know and does not guess"
        )
    return dtos.ImprintDiffDTO(
        imprint_id=imprint_id, since=since, current_fingerprint=fingerprint,
        snapshot_schema=recorded,
        source=source,
        observed=(
            dtos.ObservedDiffDTO(
                nodes=_diff_section_dto(diff["observed"]["nodes"]),
                segments=_diff_section_dto(diff["observed"]["segments"]),
            )
            if "observed" in diff else None
        ),
        twin=(
            dtos.TwinDiffDTO(
                hosts=_diff_section_dto(diff["twin"]["hosts"]),
                segments=_diff_section_dto(diff["twin"]["segments"]),
                conduits=_diff_section_dto(diff["twin"]["conduits"]),
            )
            if "twin" in diff else None
        ),
        suppressed_keys=(
            _diff_section_dto(suppressed) if suppressed is not None else None
        ),
        twin_config_changed=twin_config_changed,
        warnings=warnings,
    )


def _diff_section_dto(section: dict[str, Any]) -> dtos.DiffSectionDTO:
    return dtos.DiffSectionDTO(
        added=section["added"], removed=section["removed"],
        changed=[dtos.DiffEntryDTO(**c) for c in section["changed"]],
    )


# ---------------------------------------------------------------------------
# Unified Console run-time surface (`docs/reference/ui-consolidation/
# 00-design-ruling.md` §3.1-§3.3; wire contract `04-api-contract.md`). Thin
# glue: tier knowledge lives in `o2control` (cascade/traffic/taps), target
# knowledge in `axrange.api.targets`, live cluster reads in
# `adapter_proxmox.resolve`. Recorded values come straight from the state
# store; every LIVE value carries `checked_at`, and a read that fails is
# `unknown` with its reason -- never a zero or a false (ruling rules 3-4).
# ---------------------------------------------------------------------------
def _now() -> float:
    import time

    return time.time()


class LiveCaches:
    """Min-interval caches for the LIVE reads (code review S8): an open,
    unauthenticated GET must not be able to drive unbounded cluster or
    guest load. One instance per app (`app.state.live_caches`).

    * `/version` and the `/cluster/resources` sweep: ~5 s per target;
    * `/health?host=`: ~10 s per (target, deploy, host), and at most ONE
      computed at a time (a second caller waits up to `host_wait_s`, then
      gets 429).
    A cached answer keeps its original `checked_at` -- its age stays honest."""

    def __init__(self, *, sweep_ttl: float = 5.0, host_ttl: float = 10.0,
                 host_wait_s: float = 30.0) -> None:
        self.sweep_ttl, self.host_ttl, self.host_wait_s = sweep_ttl, host_ttl, host_wait_s
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.host_gate = threading.Semaphore(1)

    def get(self, key: tuple, ttl: float) -> Any:
        import time

        with self._lock:
            hit = self._data.get(key)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    def put(self, key: tuple, value: Any) -> None:
        import time

        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def remember(self, key: tuple, ttl: float, fn: Callable[[], Any]) -> Any:
        """`fn()`'s outcome -- a value, or the exception it raised, which is
        re-raised -- served from cache within `ttl`."""
        hit = self.get(key, ttl)
        if hit is None:
            try:
                hit = ("ok", fn())
            except Exception as e:  # noqa: BLE001 -- cached, then re-raised
                hit = ("err", e)
            self.put(key, hit)
        if hit[0] == "err":
            raise hit[1]
        return hit[1]


def _open_existing_store(db_path: str) -> Any:
    """A `StateStore` on an EXISTING file only: `StateStore()` creates a
    missing db, and a read must never mint an empty store for a mistyped
    target path. Raises `ApiError(503)` naming the problem."""
    from axrange.planner import StateStore

    if not Path(db_path).is_file():
        raise ApiError(503, f"no state store at {db_path}")
    try:
        return StateStore(db_path, readonly=True)
    except Exception as e:  # noqa: BLE001 -- schema/lock errors, reported
        raise ApiError(503, f"state store {db_path} unusable: {e}") from e


def _parent_te_id(store: Any, row: Any) -> str:
    """A sibling-tier row's twin (`tfc-<te>`, `tap-<te>`, `exr-<te>-<run>`),
    else the row's own id."""
    from axrange.o2control.cascade import display_tier

    tier = display_tier(store, row)
    plan_id = row.plan_id or ""
    if tier in ("tfc", "tap") and plan_id.startswith(f"{tier}-"):
        return plan_id[len(tier) + 1:]
    if tier == "exr" and plan_id.startswith("exr-"):
        rest = plan_id[len("exr-"):]
        # exr-<te>-<run>: the te id is the longest prefix naming a deploy.
        parts = rest.split("-")
        for i in range(len(parts) - 1, 0, -1):
            cand = "-".join(parts[:i])
            if store.get_deploy(cand) is not None:
                return cand
    return row.deploy_id


def _resolve_te_row(store: Any, deploy_id: str) -> Any:
    d = store.get_deploy(deploy_id)
    if d is None:
        raise ApiError(404, f"deploy not found: {deploy_id}")
    te_id = _parent_te_id(store, d)
    te = d if te_id == d.deploy_id else store.get_deploy(te_id)
    if te is None or (te.tier or "te") != "te":
        raise ApiError(
            400, f"{deploy_id} is not a twin (te) deploy and names none",
        )
    return te


def _siblings_dto(store: Any, te_id: str, target: str) -> dict[str, list[dtos.DeploySummaryDTO]]:
    from axrange.o2control.cascade import siblings_for_te

    return {
        tier: [_deploy_summary(r, store=store, target=target) for r in rows]
        for tier, rows in siblings_for_te(store, te_id).items()
    }


def _completed_persona_count(store: Any, tfc_id: str) -> int:
    from axrange.planner.state import OpStatus

    return sum(
        1 for op in store.ops_for_deploy(tfc_id)
        if op.op_name == "drive-persona" and op.status == OpStatus.COMPLETED
    )


def _probe_reachability(facts: Any, probe: Optional[Callable[[Any], Any]],
                        caches: Optional[LiveCaches] = None) -> dtos.LiveValueDTO:
    from axrange.adapter_proxmox.resolve import cluster_version
    from axrange.planner.executor import AdapterError

    if facts.error:
        return dtos.LiveValueDTO(state="unknown", reason=facts.error)
    if not facts.api_url:
        return dtos.LiveValueDTO(state="unknown", reason="adapter.conf has no PROXMOX_API_URL")
    def _read() -> dtos.LiveValueDTO:
        try:
            ver = (probe or (lambda cfg: cluster_version(cfg, timeout_s=3)))(facts.cfg)
        except (AdapterError, OSError) as e:
            return dtos.LiveValueDTO(state="unreachable", checked_at=_now(), reason=str(e))
        v = ver.get("version") if isinstance(ver, dict) else None
        return dtos.LiveValueDTO(
            state="reachable", checked_at=_now(), detail=f"pve {v}" if v else None,
        )

    if caches is None:
        return _read()
    return caches.remember(("version", facts.target.name), caches.sweep_ttl, _read)


def _sweep_outcome(
    caches: Optional["LiveCaches"], target_name: str, cfg: Any,
    sweep: Optional[Callable[[Any], list[dict[str, Any]]]],
) -> tuple[float, Optional[list[dict[str, Any]]], Optional[str]]:
    """ONE `/cluster/resources` sweep per target per cache window, shared by
    `/api/range` (unrecorded VMs) and `/health` (power): `(at, rows, error)`.
    The outcome -- success OR failure, with its time -- is what is cached,
    so a cached answer repeats its ORIGINAL `checked_at` (fix round 2 nit)."""
    from axrange.adapter_proxmox.resolve import cluster_vm_resources
    from axrange.planner.executor import AdapterError

    def _do() -> tuple[float, Optional[list[dict[str, Any]]], Optional[str]]:
        try:
            rows = (sweep or (lambda c: cluster_vm_resources(c, timeout_s=5)))(cfg)
            return _now(), list(rows), None
        except (AdapterError, OSError) as e:
            return _now(), None, str(e)

    if caches is None:
        return _do()
    return caches.remember(("sweep", target_name), caches.sweep_ttl, _do)


#: At most this many unrecorded VMs are listed (the count is always exact).
UNRECORDED_LIMIT = 50


def _is_axrange_vm_name(name: str) -> bool:
    from axrange.adapter_proxmox.naming import (
        is_exr_vm_name, is_o2_vm_name, is_te_vm_name, is_tfc_vm_name,
    )

    return any(f(name) for f in (is_te_vm_name, is_o2_vm_name, is_tfc_vm_name, is_exr_vm_name))


def _recorded_vmids(store: Any) -> set[int]:
    """Every vmid any NON-destroyed deploy in `store` owns: any op's
    `outputs.vmid`, UNION the deterministic `vmid_for_op(plan_id, op_id)` of
    every `clone-vm` op whatever its status (fix round 3, S3) -- a clone
    that failed after creating the VM, or is still running, owns its vmid
    too, and would otherwise be listed as unrecorded."""
    from axrange.adapter_proxmox.naming import vmid_for_op

    out: set[int] = set()
    for d in store.deploys_matching(exclude_destroyed=True):
        for op in store.ops_for_deploy(d.deploy_id):
            if op.op_name == "clone-vm":
                out.add(vmid_for_op(d.plan_id, op.op_id))
            if not op.outputs_json or '"vmid"' not in op.outputs_json:
                continue
            try:
                v = json.loads(op.outputs_json).get("vmid")
                out.add(int(v))
            except (ValueError, TypeError, AttributeError):
                continue
    return out


def _unrecorded(facts: Any, recorded: set[int], outcome: Any,
                failed_stores: Optional[list[str]] = None) -> dict[str, Any]:
    """axRange-named VMs no attached store records. `partial` (with the
    reason) when a store could not be read -- its deploys' VMs may be listed
    here wrongly. External sensors are operator-managed, never listed."""
    at, rows, err = outcome
    if rows is None:
        return {"state": "unknown", "checked_at": at,
                "reason": f"cluster sweep failed: {err}", "count": None, "vms": []}
    external = {
        int(x.vmid) for x in (getattr(facts.cfg, "external_sensors", None) or [])
        if getattr(x, "vmid", None) is not None
    }
    vms = []
    for r in rows:
        name = str(r.get("name") or "")
        if r.get("template") in (1, "1", True) or not _is_axrange_vm_name(name):
            continue
        try:
            vmid = int(r.get("vmid"))
        except (TypeError, ValueError):
            continue
        if vmid in recorded or vmid in external:
            continue
        vms.append({"vmid": vmid, "name": name, "status": r.get("status"),
                    "node": r.get("node")})
    vms.sort(key=lambda v: v["vmid"])
    if failed_stores:
        return {"state": "partial", "checked_at": at,
                "reason": "no attached store records these VMs, but store(s) "
                          f"{', '.join(failed_stores)} could not be read -- some may "
                          "belong to deploys recorded there",
                "count": len(vms), "vms": vms[:UNRECORDED_LIMIT]}
    return {"state": "known", "checked_at": at,
            "reason": "no attached store records these VMs" if vms else None,
            "count": len(vms), "vms": vms[:UNRECORDED_LIMIT]}


def range_view(
    settings: ApiSettings, *, live: bool = True,
    probe: Optional[Callable[[Any], Any]] = None,
    caches: Optional[LiveCaches] = None,
    sweep: Optional[Callable[[Any], list[dict[str, Any]]]] = None,
) -> dtos.RangeDTO:
    """`GET /api/range`: every target, its reachability (one cheap `/version`
    read each, `live=False` skips it) and its standing twins -- from EVERY
    state store of the target (fix round 2) -- with their sibling
    tap/tfc/exr deploys, plus the axRange-named VMs on the cluster no store
    records (`unrecorded`, from the cached sweep; read-only, never acted on).
    The home page in one call."""
    from axrange.api.targets import (
        placement_warnings, resolve_targets, store_labels, target_facts,
    )
    from axrange.o2control.cascade import display_tier
    from axrange.planner.inventory import InventoryError, build_twin_inventory

    out: list[dtos.RangeTargetDTO] = []
    for t in resolve_targets(settings):
        facts = target_facts(t)
        reach = (
            _probe_reachability(facts, probe, caches) if live
            else dtos.LiveValueDTO(state="unknown", reason="not checked (live=false)")
        )
        labels = store_labels(t)
        twins: list[dtos.RangeTwinDTO] = []
        store_status: list[dict[str, Any]] = []
        target_warnings: list[str] = []
        seen: dict[str, str] = {}   # deploy_id -> store label
        recorded: set[int] = set()
        failed: list[str] = []      # stores that exist but could not be read
        for path in t.stores:
            label = labels[path]
            try:
                store = _open_existing_store(path)
            except ApiError as e:
                store_status.append({"label": label, "path": path,
                                     "primary": path == t.db_path, "ok": False,
                                     "reason": e.message})
                if Path(path).exists():
                    failed.append(label)
                continue
            store_status.append({"label": label, "path": path,
                                 "primary": path == t.db_path, "ok": True, "reason": None})
            try:
                if live:
                    recorded |= _recorded_vmids(store)
                for d in store.deploys_matching():
                    if d.deploy_id in seen:
                        target_warnings.append(
                            f"deploy {d.deploy_id} is recorded in two stores: "
                            f"{seen[d.deploy_id]}, {label} -- reads of it are refused"
                        )
                    else:
                        seen[d.deploy_id] = label
                for row in store.deploys_matching(tier="te", exclude_destroyed=True):
                    if display_tier(store, row) != "te":
                        continue
                    warnings: list[str] = []
                    try:
                        placement = json.loads(row.placement_json) if row.placement_json else {}
                    except (ValueError, TypeError):
                        placement = {}
                        warnings.append("recorded placement is unreadable")
                    warnings.extend(placement_warnings(placement, facts))
                    try:
                        host_count: Optional[int] = len(
                            build_twin_inventory(store, row.deploy_id).hosts
                        )
                    except InventoryError as e:
                        host_count = None
                        warnings.append(f"host count unavailable: {e}")
                    siblings = _siblings_dto(store, row.deploy_id, t.name)
                    for rows_ in siblings.values():
                        for sdto in rows_:
                            sdto.store = label
                    tfc_rows = siblings.get("tfc") or []
                    persona_count = (
                        _completed_persona_count(store, tfc_rows[0].deploy_id)
                        if tfc_rows else None
                    )
                    twins.append(dtos.RangeTwinDTO(
                        deploy_id=row.deploy_id, twin_id=row.scenario_id,
                        status=row.status.value, plan_id=row.plan_id,
                        started_at=row.started_at, updated_at=row.updated_at,
                        completed_at=row.completed_at, cluster=t.name,
                        host_count=host_count, persona_instance_count=persona_count,
                        siblings=siblings, warnings=warnings,
                        protected=row.deploy_id in t.protected_deploys,
                        store=label,
                    ))
            finally:
                store.close()
        twins.sort(key=lambda tw: tw.updated_at, reverse=True)
        primary = next((st for st in store_status if st["primary"]), None)
        unrecorded = None
        if live:
            if facts.cfg is None or not facts.api_url:
                unrecorded = {"state": "unknown", "checked_at": None,
                              "reason": facts.error or "adapter.conf has no PROXMOX_API_URL",
                              "count": None, "vms": []}
            else:
                unrecorded = _unrecorded(
                    facts, recorded, _sweep_outcome(caches, t.name, facts.cfg, sweep),
                    failed,
                )
        out.append(dtos.RangeTargetDTO(
            name=t.name, default=t.default, node=facts.node or None,
            api_url=facts.api_url or None, reachability=reach,
            store=dtos.StoreStateDTO(
                ok=bool(primary and primary["ok"]),
                reason=(primary or {}).get("reason"),
            ),
            twins=twins, error=facts.error, stores=store_status,
            warnings=target_warnings, unrecorded=unrecorded,
        ))
    return dtos.RangeDTO(generated_at=_now(), targets=out)


def _best_effort_manifests(settings: ApiSettings) -> Any:
    if not settings.images_root:
        return None
    try:
        return _load_manifests(Path(settings.images_root))
    except ApiError:
        return None


def _failed_attempt(store: Any, deploy_id: str, op: Any, tier: str) -> Optional[dict[str, Any]]:
    attempts = store.attempts_for_op(deploy_id, op.op_id)
    for a in reversed(attempts):
        if a.error_message or a.error_class:
            return {
                "deploy_id": deploy_id, "tier": tier, "op_id": op.op_id,
                "op_name": op.op_name, "error_class": a.error_class,
                "error_message": a.error_message, "at": a.ended_at or a.started_at,
            }
    return {
        "deploy_id": deploy_id, "tier": tier, "op_id": op.op_id, "op_name": op.op_name,
        "error_class": None, "error_message": f"op status {op.status.value}",
        "at": op.completed_at or op.started_at,
    }


def deploy_hosts(settings: ApiSettings, deploy_id: str) -> dtos.TwinHostsDTO:
    """`GET /api/deploys/{id}/hosts`: the recorded inventory roster joined per
    host with its persona instances (every non-destroyed `drive-persona`
    row, any status), tap bindings and newest op error -- the one model
    behind the Console's Twin area. State store only; no cluster call."""
    from axrange.o2control.taps import tap_info_from_deploy
    from axrange.o2control.traffic import _better_job, effective_roles_for_twin, job_records
    from axrange.planner.inventory import InventoryError, build_twin_inventory
    from axrange.planner.opview import op_fields
    from axrange.planner.state import OpStatus

    target = _target_name(settings)
    manifests = _best_effort_manifests(settings)
    store = _open_existing_store(settings.db_path)
    try:
        te = _resolve_te_row(store, deploy_id)
        te_id = te.deploy_id
        roles = effective_roles_for_twin(store, te_id)
        try:
            inv = build_twin_inventory(store, te_id, manifests=manifests, role_overrides=roles)
        except InventoryError as e:
            raise ApiError(404, str(e)) from e
        tfc = store.find_deploy_for_plan(f"tfc-{te_id}", exclude_destroyed=True)
        tap = store.find_deploy_for_plan(f"tap-{te_id}", exclude_destroyed=True)
        warnings: list[str] = []

        last_error: dict[str, dict[str, Any]] = {}

        def _note_error(host: Optional[str], err: Optional[dict[str, Any]]) -> None:
            if not host or err is None:
                return
            cur = last_error.get(host)
            if cur is None or (err.get("at") or 0) >= (cur.get("at") or 0):
                last_error[host] = err

        bad = (OpStatus.FAILED, OpStatus.HALTED)
        for op in store.ops_for_deploy(te_id):
            if op.status in bad:
                _note_error(_op_node_id(op), _failed_attempt(store, te_id, op, "te"))

        personas: dict[str, list[dtos.HostPersonaInstanceDTO]] = {}
        role_edit_hosts: set[str] = set()
        host_edit: dict[str, dict[str, Any]] = {}
        if tfc is not None:
            # ONE pass over the tfc rows (S11): persona rows, host-edit role
            # markers and the newest persisted host-edit job per host (M2).
            for op in store.ops_for_deploy(tfc.deploy_id):
                _n, scope, inp, outs = op_fields(op)
                host = str(inp.get("host_id") or scope.get("host") or "")
                for job in job_records(outs):
                    if job.get("host_id") != host:
                        continue
                    cur = host_edit.get(host)
                    if cur is None or (job.get("started_at") or 0) > (cur.get("started_at") or 0):
                        host_edit[host] = job
                    elif job.get("job_id") == cur.get("job_id"):
                        host_edit[host] = _better_job(cur, job)
                if op.op_name == "assign-role":
                    if op.status == OpStatus.COMPLETED and inp.get("origin") == "host-edit":
                        role_edit_hosts.add(host)
                    continue
                if op.op_name != "drive-persona" or op.status == OpStatus.DESTROYED:
                    continue
                err = _failed_attempt(store, tfc.deploy_id, op, "tfc") if op.status in bad else None
                _note_error(host, err)
                personas.setdefault(host, []).append(dtos.HostPersonaInstanceDTO(
                    instance_id=str(inp.get("instance_id") or scope.get("instance") or ""),
                    persona=str(inp.get("persona_id") or "unknown"),
                    os_family=inp.get("os_family"), drive=inp.get("drive"),
                    schedule=inp.get("schedule"), seed=inp.get("seed"),
                    peers=dict(inp.get("peers") or {}),
                    peer_sets=dict(inp.get("peer_sets") or {}),
                    status=op.status.value, op_id=op.op_id,
                    started_at=op.started_at, completed_at=op.completed_at,
                    origin=str(inp.get("origin") or "profile"),
                    heartbeat=(outs or {}).get("heartbeat"),
                    last_error=err,
                    stop_error=(outs or {}).get("stop_error"),
                ))

        taps: dict[str, list[dtos.HostTapDTO]] = {}
        if tap is not None:
            info = tap_info_from_deploy(
                store, tap.deploy_id, target=te_id, target_status=te.status.value,
                sensor_by_id={},
            )
            for b in info.bindings:
                taps.setdefault(b.host, []).append(dtos.HostTapDTO(
                    tap_deploy_id=tap.deploy_id, net_id=b.net_id, nic_index=b.nic_index,
                    sensors=list(b.sensors), directions=dict(b.directions),
                    status=info.status,
                ))
    finally:
        store.close()

    hosts: list[dtos.TwinHostDTO] = []
    for h in inv.hosts:
        if h.host_id in role_edit_hosts:
            origin = "host-edit"
        elif h.host_id in roles:
            origin = "tfc"
        else:
            origin = "baseline"
        hosts.append(dtos.TwinHostDTO(
            host_id=h.host_id, vm_name=h.vm_name, vmid=h.vmid, pve_node=h.pve_node,
            vm_status=h.vm_status, role=h.role, role_baseline=h.role_baseline,
            kind=h.kind,
            nets=[
                dtos.HostNetDTO(net_id=n.net_id, ip=n.ip, nic_index=n.nic_index,
                                external=n.external)
                for n in h.nets
            ],
            os=h.os,
            listen=(
                [dtos.HostListenDTO(port=li.port, proto=li.proto) for li in h.listen]
                if h.listen is not None else None
            ),
            role_origin=origin,
            personas=sorted(personas.pop(h.host_id, []), key=lambda p: p.instance_id),
            taps=taps.pop(h.host_id, []),
            last_error=last_error.get(h.host_id),
            host_edit=host_edit.get(h.host_id),
        ))
    for orphan in sorted(personas):
        warnings.append(
            f"tfc rows name host {orphan!r}, which the twin inventory does not have"
        )
    return dtos.TwinHostsDTO(
        deploy_id=te_id, twin_id=te.scenario_id, deploy_status=inv.deploy_status,
        target=target,
        store=_store_label(settings),
        tfc=dtos.SiblingRefDTO(deploy_id=tfc.deploy_id, status=tfc.status.value) if tfc else None,
        tap=dtos.SiblingRefDTO(deploy_id=tap.deploy_id, status=tap.status.value) if tap else None,
        protected=te_id in _protected_ids(settings),
        hosts=hosts, net_bridge=dict(inv.net_bridge), vlan_underlay=inv.vlan_underlay,
        egress=(
            dtos.EgressPostureDTO(**inv.egress.to_dict()) if inv.egress is not None else None
        ),
        warnings=warnings,
    )


def _power_state(raw: Any) -> str:
    st = str(raw or "").lower()
    return st if st in ("running", "stopped", "paused") else (st or "unknown")


def deploy_health(
    settings: ApiSettings, deploy_id: str, *, host: Optional[str] = None,
    sweep: Optional[Callable[[Any], list[dict[str, Any]]]] = None,
    qga_ping: Optional[Callable[[Any, int, str], None]] = None,
    persona_backend: Any = None,
    caches: Optional[LiveCaches] = None,
) -> dtos.DeployHealthDTO:
    """`GET /api/deploys/{id}/health`: LIVE, on demand. ONE
    `/cluster/resources` sweep gives every host's power state; `host=` adds
    that one host's QGA reachability and a driver heartbeat per persona
    instance on it -- never a whole-twin QGA fan-out. `sweep`/`qga_ping`/
    `persona_backend` are test seams."""
    from axrange.adapter_proxmox import load_config
    from axrange.planner.inventory import InventoryError, build_twin_inventory
    from axrange.planner.opview import op_fields
    from axrange.planner.state import OpStatus

    target = _target_name(settings)
    store = _open_existing_store(settings.db_path)
    try:
        te = _resolve_te_row(store, deploy_id)
        try:
            inv = build_twin_inventory(store, te.deploy_id)
        except InventoryError as e:
            raise ApiError(404, str(e)) from e
        if host is not None and host not in {h.host_id for h in inv.hosts}:
            raise ApiError(404, f"host {host!r} is not a host of {te.deploy_id}")
        host_inps: list[dict[str, Any]] = []
        if host is not None:
            tfc = store.find_deploy_for_plan(f"tfc-{te.deploy_id}", exclude_destroyed=True)
            if tfc is not None:
                for op in store.ops_for_deploy(tfc.deploy_id):
                    if op.op_name != "drive-persona" or op.status != OpStatus.COMPLETED:
                        continue
                    _n, _s, inp, _o = op_fields(op)
                    if inp.get("host_id") == host:
                        host_inps.append(inp)
    finally:
        store.close()

    try:
        cfg = load_config(settings.adapter_config)
        cfg_error = None
    except Exception as e:  # noqa: BLE001
        cfg, cfg_error = None, f"adapter config load failed: {e}"

    by_vmid: dict[int, dict[str, Any]] = {}
    sweep_at = _now()
    if cfg is None:
        cluster = dtos.LiveValueDTO(state="unknown", reason=cfg_error)
    else:
        # The cached OUTCOME carries its own time: a cache hit repeats it.
        sweep_at, rows, sweep_err = _sweep_outcome(caches, target, cfg, sweep)
        if rows is None:
            cluster = dtos.LiveValueDTO(state="unknown", checked_at=sweep_at, reason=sweep_err)
        else:
            for r in rows:
                try:
                    by_vmid[int(r.get("vmid"))] = r
                except (TypeError, ValueError):
                    continue
            cluster = dtos.LiveValueDTO(state="reachable", checked_at=sweep_at)

    hosts: list[dtos.HealthHostDTO] = []
    node_of: dict[str, Optional[str]] = {}
    for h in inv.hosts:
        if cluster.state != "reachable":
            power = dtos.HealthPowerDTO(
                state="unknown", checked_at=cluster.checked_at,
                reason=f"cluster sweep failed: {cluster.reason}",
            )
        elif h.vmid is None:
            power = dtos.HealthPowerDTO(
                state="unknown", checked_at=sweep_at, reason="no recorded vmid",
            )
        elif h.vmid not in by_vmid:
            power = dtos.HealthPowerDTO(
                state="absent", checked_at=sweep_at,
                reason=f"vmid {h.vmid} is not on the cluster",
            )
        else:
            r = by_vmid[h.vmid]
            power = dtos.HealthPowerDTO(
                state=_power_state(r.get("status")), checked_at=sweep_at,
                node=r.get("node"),
            )
        node_of[h.host_id] = power.node or h.pve_node
        hosts.append(dtos.HealthHostDTO(
            host_id=h.host_id, vmid=h.vmid, recorded_status=h.vm_status, power=power,
        ))

    detail = None
    if host is not None:
        def _detail() -> dtos.HealthHostDetailDTO:
            return _host_health_detail(
                inv, host, node_of, cfg, cfg_error, host_inps,
                qga_ping=qga_ping, persona_backend=persona_backend,
            )

        if caches is None:
            detail = _detail()
        else:
            key = ("host", target, te.deploy_id, host)
            detail = caches.get(key, caches.host_ttl)
            if detail is None:
                # Concurrency cap 1 for live guest reads (S8).
                if not caches.host_gate.acquire(timeout=caches.host_wait_s):
                    raise ApiError(
                        429, "another host health read is running; retry shortly",
                    )
                try:
                    detail = caches.get(key, caches.host_ttl)
                    if detail is None:
                        detail = _detail()
                        caches.put(key, detail)
                finally:
                    caches.host_gate.release()

    return dtos.DeployHealthDTO(
        # The sweep's own time (original on a cache hit) -- never "now".
        deploy_id=te.deploy_id, target=target, checked_at=sweep_at,
        cluster=cluster, hosts=hosts, host_detail=detail,
    )


def _host_health_detail(
    inv: Any, host: str, node_of: dict[str, Optional[str]], cfg: Any,
    cfg_error: Optional[str], host_inps: list[dict[str, Any]], *,
    qga_ping: Optional[Callable[[Any, int, str], None]], persona_backend: Any,
) -> dtos.HealthHostDetailDTO:
    """One host's QGA reachability + persona heartbeats (live)."""
    from axrange.adapter_proxmox.resolve import qga_ping as _qga_ping
    from axrange.o2control.traffic import heartbeat_instances
    from axrange.planner.executor import AdapterError

    hinfo = next(h for h in inv.hosts if h.host_id == host)
    node = node_of.get(host) or (cfg.default_node if cfg is not None else None)
    if cfg is None or hinfo.vmid is None or not node:
        why = cfg_error or ("no recorded vmid" if hinfo.vmid is None else "no PVE node")
        qga = dtos.LiveValueDTO(state="unknown", reason=why)
    else:
        try:
            (qga_ping or (lambda c, v, n: _qga_ping(c, v, node=n, timeout_s=5)))(
                cfg, int(hinfo.vmid), node,
            )
            qga = dtos.LiveValueDTO(state="reachable", checked_at=_now())
        except (AdapterError, OSError) as e:
            qga = dtos.LiveValueDTO(state="unreachable", checked_at=_now(), reason=str(e))
    personas: list[dtos.HealthPersonaDTO] = []
    if qga.state == "reachable":
        beats = heartbeat_instances(
            host_inps, cfg=cfg, persona_backend=persona_backend, node=node,
        )
    else:
        beats = {}
    for inp in sorted(host_inps, key=lambda i: str(i.get("instance_id"))):
        iid = str(inp.get("instance_id") or "")
        rec = beats.get(iid) or {
            "state": "unknown", "alive": None, "checked_at": qga.checked_at,
            "reason": f"QGA {qga.state}: {qga.reason or 'not checked'}",
        }
        personas.append(dtos.HealthPersonaDTO(
            instance_id=iid, persona=str(inp.get("persona_id") or "unknown"),
            state=rec["state"], alive=rec.get("alive"), unit=rec.get("unit"),
            checked_at=rec.get("checked_at"), reason=rec.get("reason"),
        ))
    return dtos.HealthHostDetailDTO(host_id=host, qga=qga, personas=personas)


def _twin_personas_overlay(settings: ApiSettings, twin_id: Optional[str]) -> tuple[dict, list[str], bool]:
    """(overlay, warnings, twin_found) -- `traffic.twin_persona_overlay`, the
    one helper the CLI shares (S10); the global registry is never touched
    (ruling §3.2, GAP-105)."""
    from axrange.o2control.traffic import twin_persona_overlay

    if twin_id and not _is_safe_catalog_ref(twin_id):
        return {}, [], False
    return twin_persona_overlay(twin_id, settings.twin_configs_dir or None)


def _persona_summary(p: Any) -> str:
    parts = []
    for e in p.emits:
        ports = ",".join(str(x) for x in e.ports) or "*"
        peer = e.peer_role or "?"
        parts.append(f"{e.effective_transport()}/{ports} -> {peer}"
                     + (" (optional)" if e.optional else "")
                     + (" (all)" if e.peer_scope == "all" else ""))
    return "; ".join(parts)


def list_personas(settings: ApiSettings, *, twin: Optional[str] = None) -> dtos.PersonaListDTO:
    """`GET /api/personas?twin=`: the catalog plus that twin's local personas
    (overlay wins on an id collision), for the Console's picker."""
    from axrange.personas.registry import PERSONAS

    overlay, warnings, found = _twin_personas_overlay(settings, twin)
    if twin and not found:
        raise ApiError(404, f"unknown twin {twin!r} (no twins/<id>/twin.yaml)")
    merged: dict[str, tuple[Any, str]] = {pid: (p, "catalog") for pid, p in PERSONAS.items()}
    for pid, p in overlay.items():
        merged[pid] = (p, "twin")
    out = []
    for pid in sorted(merged):
        p, source = merged[pid]
        out.append(dtos.PersonaDTO(
            id=p.id, os_family=p.os_family, drive=p.drive, source=source,
            requires=list(p.requires), summary=_persona_summary(p),
            emits=[
                dtos.PersonaEmitDTO(
                    proto=e.proto, ports=list(e.ports), peer_role=e.peer_role,
                    transport=e.effective_transport(), cadence=e.cadence,
                    volume=e.volume, optional=e.optional, peer_scope=e.peer_scope,
                )
                for e in p.emits
            ],
        ))
    return dtos.PersonaListDTO(twin=twin, personas=out, warnings=warnings)


def _host_assignments(
    settings: ApiSettings, te_id: str, host_id: str, req: dtos.HostPersonasRequest,
) -> list[Any]:
    """The request's persona set; `assignments: null` keeps the host's
    running set (read from the store) -- only `[]` means "stop them all"."""
    from axrange.o2control.traffic import HostPersonaAssignment, current_host_assignments

    if req.assignments is None:
        store = _open_existing_store(settings.db_path)
        try:
            return current_host_assignments(store, te_id, host_id)
        finally:
            store.close()
    return [
        HostPersonaAssignment(
            persona=a.persona, schedule=a.schedule, seed=a.seed, intensity=a.intensity,
        )
        for a in req.assignments
    ]


def _plan_dto(plan: Any, target: str) -> dtos.HostPersonasPlanDTO:
    return dtos.HostPersonasPlanDTO(target=target, **plan.to_dict())


def _te_for_persona_edit(settings: ApiSettings, deploy_id: str) -> Any:
    store = _open_existing_store(settings.db_path)
    try:
        return _resolve_te_row(store, deploy_id)
    finally:
        store.close()


def host_personas_plan(
    settings: ApiSettings, deploy_id: str, host_id: str, req: dtos.HostPersonasRequest,
) -> dtos.HostPersonasPlanDTO:
    """PURE preview (no state write): start / stop / unchanged / dependents /
    refusals for one host's persona set."""
    from axrange.o2control.traffic import TrafficPlanError, plan_host_personas
    from axrange.personas import registry as persona_registry

    te = _te_for_persona_edit(settings, deploy_id)
    overlay, warnings, _found = _twin_personas_overlay(settings, te.scenario_id)
    store = _open_existing_store(settings.db_path)
    try:
        with persona_registry.overlay(overlay):
            plan = plan_host_personas(
                store, te.deploy_id, host_id,
                _host_assignments(settings, te.deploy_id, host_id, req), role=req.role,
                update_dependents=req.update_dependents,
                manifests=_best_effort_manifests(settings),
                allow_role_orphaning=req.allow_role_orphaning,
            )
    except TrafficPlanError as e:
        raise ApiError(e.status, e.message, detail=e.detail) from e
    finally:
        store.close()
    plan.warnings = warnings + plan.warnings
    return _plan_dto(plan, _target_name(settings))


def _thread_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="tfc-host-edit", daemon=True).start()


def host_personas_apply(
    settings: ApiSettings, deploy_id: str, host_id: str, req: dtos.HostPersonasRequest, *,
    persona_backend: Any = None,
    background: Callable[[Callable[[], None]], None] | None = None,
    containment_ga: Any = None,
    override_protection: Optional[str] = None,
) -> dtos.HostPersonasApplyResponse:
    """`PUT .../hosts/{host}/personas`: validate + gate + lease + reset
    synchronously (refusals are immediate 4xx), then run the executor in the
    background and return at once -- poll `GET /api/deploys/{tfc_deploy_id}`
    or `.../hosts`. Parity with `tfc deploy`: containment gate, image-manifest
    OS check (`images_root`), the twin's local personas."""
    from axrange.o2control.traffic import TrafficPlanError, apply_host_personas
    from axrange.personas import registry as persona_registry

    te = _te_for_persona_edit(settings, deploy_id)
    _check_protection(settings, te.deploy_id, override_protection)
    overlay, warnings, _found = _twin_personas_overlay(settings, te.scenario_id)
    try:
        with persona_registry.overlay(overlay):
            res = apply_host_personas(
                db_path=settings.db_path, target=te.deploy_id, host_id=host_id,
                assignments=_host_assignments(settings, te.deploy_id, host_id, req),
                role=req.role,
                update_dependents=req.update_dependents,
                adapter_config=settings.adapter_config, target_node=req.target_node,
                persona_backend=persona_backend,
                manifests=_best_effort_manifests(settings),
                allow_uncontained=req.allow_uncontained,
                allow_role_orphaning=req.allow_role_orphaning,
                expect_plan=req.expect_plan,
                containment_ga=containment_ga,
                background=background or _thread_background,
                log=lambda m: logger.info("%s", m),
            )
    except TrafficPlanError as e:
        raise ApiError(e.status, e.message, detail=e.detail) from e
    res.plan.warnings = warnings + res.plan.warnings
    return dtos.HostPersonasApplyResponse(
        deploy_id=res.te_deploy_id, host_id=res.host_id,
        tfc_deploy_id=res.tfc_deploy_id, status=res.status, detached=res.detached,
        plan=_plan_dto(res.plan, _target_name(settings)),
        warnings=warnings + list(res.warnings),
    )


def tfc_profile(settings: ApiSettings, deploy_id: str) -> dtos.TfcProfileDTO:
    """`GET /api/deploys/{id}/tfc/profile`: the running persona state as a
    traffic-profile document, so a host edit can be saved back to
    `twins/<id>/tfc/` deliberately (export only; nothing is written)."""
    from axrange.o2control.traffic import export_traffic_profile

    store = _open_existing_store(settings.db_path)
    try:
        te = _resolve_te_row(store, deploy_id)
        tfc = store.find_deploy_for_plan(f"tfc-{te.deploy_id}", exclude_destroyed=True)
        if tfc is None:
            raise ApiError(404, f"twin {te.deploy_id} has no traffic-profile (tfc) deploy")
        doc = export_traffic_profile(store, tfc.deploy_id, target=te.scenario_id)
    finally:
        store.close()
    return dtos.TfcProfileDTO(
        deploy_id=te.deploy_id, tfc_deploy_id=tfc.deploy_id, twin_id=te.scenario_id,
        **doc,
    )
