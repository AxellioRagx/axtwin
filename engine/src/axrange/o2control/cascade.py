"""Sibling-tier destroy cascade for a twin (`te`) teardown — the ONE place
that owns the ORDER.

A twin destroy sweeps its own `te` resources and also tears down the
standalone sibling-tier deploys bound to it: the exercise runs (`exr`), the
traffic profile (`tfc`), and the tap plan (`tap`)
(`docs/design/02-twin-model.md`, "The teardown protect/cascade matrix").

**Order is the contract this module exists to hold.** The twin pipeline
stands up `te` → `tfc`/`tap` → `exr`, so teardown is the reverse:

    exr → tfc → tap → te

Every sibling tier binds INTO the twin's LIVE VMs, so a cascade sequenced
after the twin's own VM purge cannot do its job:

  * `tap`'s de-scope pass re-asserts the source mirror via
    `qm set <vmid> --hookscript` (`taps.py`) so it never dangles pointing at
    a torn-down tunnel — that needs the VM's config file to still exist.
  * `tfc`'s persona stop is a QGA `agent/exec` INTO the guest
    (`traffic.py`) — that needs the VM to still exist and be running.

Both of those loops are best-effort by design (they log and swallow, and do
not count into `TeardownResult.errors`), so running them too late did not
fail the destroy — it reported a green `DESTROYED` under a wall of red
"Configuration file … does not exist" warnings, which is strictly worse: a
genuinely stuck de-scope became indistinguishable from a clean teardown.
See LES-171.

This lives in `o2control` (not `planner/teardown.py`) because it is a
cross-tier POLICY over the three playouts this package owns; the engine
never imports `axrange.o2control` (LES-025 boundary). Both transports —
`axrange/cli/main.py` and `axrange/api/service.py` — call this one helper
instead of each re-implementing the sequence, so the order can never drift
between them again (LES-037; the inverted order was duplicated in exactly
those two call sites).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# The cascade order, as data — `cascade_destroy_siblings_for_te` returns one
# entry per tier in exactly this sequence, and a test pins it. Reverse of the
# twin pipeline's stand-up order (`docs/design/02-twin-model.md`).
CASCADE_ORDER: tuple[str, ...] = ("exr", "tfc", "tap")

# Operator-facing label per tier — the wording both transports print and the
# API surfaces on `DestroyResponse.cascade_errors` (kept verbatim from the
# pre-refactor call sites, which tests pin).
CASCADE_LABELS: dict[str, str] = {
    "exr": "exercise",
    "tfc": "traffic-profile",
    "tap": "tap-plan",
}


@dataclass
class SiblingCascade:
    """One sibling tier's cascade outcome.

    `results` holds the `TeardownResult`s actually attempted — empty when no
    deploy of that tier was ever bound to the twin (the common case, and NOT
    an error worth surfacing on a twin destroy). `error` is set ONLY for an
    UNEXPECTED failure (see `cascade_destroy_siblings_for_te`)."""

    tier: str
    label: str
    results: list[Any] = field(default_factory=list)
    error: Optional[str] = None


def _isolated(tier: str, fn: Callable[[], Any]) -> SiblingCascade:
    """Run one sibling's cascade with its own exception isolation (LES-117).

    An UNEXPECTED failure (DB hiccup, lease contention, adapter error) in one
    sibling must never propagate: it would skip every sibling sequenced after
    it AND abort the twin's own teardown, orphaning resources with no error
    surfaced about them specifically. Each typed `*PlanError` the cascade
    helpers already raise for their own "not found"/"wrong tier" cases is a
    SUBSET of `Exception`, so those are caught here too — no behavior change
    for the expected-error path, only for the otherwise-uncaught one.

    Normalizes the three helpers' differing return shapes (`exercise` returns
    a list — a twin can have several ephemeral runs; `traffic`/`tap` return a
    single `TeardownResult` or `None`) into one list."""
    label = CASCADE_LABELS[tier]
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001 -- see docstring
        return SiblingCascade(tier=tier, label=label, error=str(e))
    if out is None:
        results: list[Any] = []
    elif isinstance(out, list):
        results = [r for r in out if r is not None]
    else:
        results = [out]
    return SiblingCascade(tier=tier, label=label, results=results)


def cascade_destroy_siblings_for_te(
    *,
    db_path: str,
    te_deploy_id: str,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    adapter: Any = None,
    persona_backend: Any = None,
    ga_factory: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> list[SiblingCascade]:
    """Tear down every sibling-tier deploy bound to `te_deploy_id`, in
    `CASCADE_ORDER` (`exr` → `tfc` → `tap`).

    **Call this BEFORE the twin's own `planner.destroy_deploy`** — see the
    module docstring for why the reverse cannot work.

    Lease-safe inside a twin destroy's own lease: every sibling deploy is
    keyed `scenario_id == plan_id` (`exr-*`/`tfc-*`/`tap-*`), never the twin's
    `scenario_id`, so each cascade takes its own lease cleanly. Each opens its
    own `StateStore`.

    `adapter`/`persona_backend`/`ga_factory` are the per-tier test seams,
    passed straight through (all default to `None`, which is what production
    passes). Returns one `SiblingCascade` per tier, in order — the caller
    renders them (the CLI prints a summary line per tier; the API maps
    `error` onto `DestroyResponse.cascade_errors`)."""
    # Imports are deliberately LAZY (inside each closure, resolved at call
    # time) rather than module-level `from … import fn`: the cascade-isolation
    # tests monkeypatch the function ATTRIBUTE on the owning module, which a
    # module-level `from … import` would have already bound past.
    def _exr() -> Any:
        from axrange.o2control.exercise import cascade_destroy_exercises_for_te

        return cascade_destroy_exercises_for_te(
            db_path=db_path, te_deploy_id=te_deploy_id,
            adapter_config=adapter_config, node=node,
            teardown_backend=teardown_backend, ga_factory=ga_factory, log=log,
        )

    def _tfc() -> Any:
        from axrange.o2control.traffic import cascade_destroy_tfc_for_te

        return cascade_destroy_tfc_for_te(
            db_path=db_path, te_deploy_id=te_deploy_id,
            adapter_config=adapter_config, node=node,
            teardown_backend=teardown_backend, persona_backend=persona_backend,
            log=log,
        )

    def _tap() -> Any:
        from axrange.o2control.taps import cascade_destroy_for_te

        return cascade_destroy_for_te(
            db_path=db_path, te_deploy_id=te_deploy_id,
            adapter_config=adapter_config, node=node,
            teardown_backend=teardown_backend, adapter=adapter, log=log,
        )

    runners: dict[str, Callable[[], Any]] = {"exr": _exr, "tfc": _tfc, "tap": _tap}
    # Sequential and ordered — `CASCADE_ORDER` is the single source of truth
    # for the sequence, not the textual order of the definitions above.
    return [_isolated(tier, runners[tier]) for tier in CASCADE_ORDER]


def _as_list(row: Any) -> list[Any]:
    return [row] if row is not None else []


def siblings_for_te(store: Any, te_deploy_id: str) -> dict[str, list[Any]]:
    """Every STANDING (non-destroyed) sibling-tier `DeployRow` bound to
    `te_deploy_id`, keyed by tier, in `CASCADE_ORDER` — a read-only lookup,
    the counterpart to `cascade_destroy_siblings_for_te` above (which
    actually tears each one down; this only finds them).

    This is the shared READ-ONLY lookup for each tier's naming convention —
    `tap-<te>`/`tfc-<te>` (exact match, at most one deploy) and
    `exr-<te>-*` (prefix match, a twin can have several concurrent
    exercise runs) — so a caller that just wants "what's bound to this
    twin" (e.g. an incremental update's dangling-host warning, `docs/
    WORK_ORDER_twin_incremental_update.md` §3.5) doesn't hand-roll the same
    convention a 4th time for THIS query shape (code review S3;
    `api/service.py` had its own copy before this). NOT literally the one
    place overall that knows each tier's convention (code review, W2 round
    2) — `axrange/o2control/{exercise,traffic,taps}.py` each still own the
    WRITE-side convention for creating and NAME-resolving their own tier's
    deploys in the first place; this function only centralizes the
    narrower "given a te_deploy_id, find its bound siblings" read.

    Always returns a list per tier, even for the (common) single-row
    tap/tfc case, so a caller iterates uniformly regardless of tier.
    """
    finders: dict[str, Callable[[], list[Any]]] = {
        "exr": lambda: store.deploys_matching(
            tier="exr", plan_id_prefix=f"exr-{te_deploy_id}-", exclude_destroyed=True,
        ),
        "tfc": lambda: _as_list(
            store.find_deploy_for_plan(f"tfc-{te_deploy_id}", exclude_destroyed=True)
        ),
        "tap": lambda: _as_list(
            store.find_deploy_for_plan(f"tap-{te_deploy_id}", exclude_destroyed=True)
        ),
    }
    return {tier: finders[tier]() for tier in CASCADE_ORDER}


def display_tier(store: Any, row: Any) -> str:
    """The user-facing tier of a deploy row: `tap` for a tap-plan playout
    (stored as `tier="te"` with a `tap-` plan_id -- `naming.py` has no
    separate deploy-level tap tier), else the stored tier. The structural
    check (`taps.is_tap_plan_deploy`) only runs for the narrow `te` +
    `tap-`-prefixed candidate set, so ordinary rows cost nothing extra.
    Shared by `list-deploys` and the API's `DeploySummaryDTO.tier`."""
    tier = getattr(row, "tier", None) or "te"
    if tier == "te" and str(getattr(row, "plan_id", "") or "").startswith("tap-"):
        from axrange.o2control.taps import is_tap_plan_deploy

        if is_tap_plan_deploy(store, row):
            return "tap"
    return tier
