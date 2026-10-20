"""Deploy-round-trip proof that a twin-config's topology is FULLY tap-plan
addressable once deployed -- no author-declared tap intent needed at all
(`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28).

Rewritten from the retired `tap_points[]` materialization proof (`docs/
WORK_ORDER_twin_configs.md` §4, the LES-103 check): a twin config no longer
declares ANY tap-related content (D3), so there is nothing left to
"materialize." What replaces it: `derive_default_tap_points`/
`resolve_tap_point` (`axrange.planner.tappoints`), the tap tier's own
resolution machinery, must resolve EVERY declared segment/boundary/
perimeter against a REAL deployed twin-config-expanded twin's published
topology -- a real `build_plan` + `Executor` round trip against a fake
adapter (LES-068), the same idiom `tests/test_target_environments_
boundary_taps.py` uses for the hand-written archetypes. Covers `sme-flat`
(flat, `segment` scope only) and `corp-enterprise` (segmented, `segment` +
`boundary` scope) -- the minimum the original work order asked for ("at
least sme-flat and one segmented config") -- plus the shipped
`corp-enterprise` tap plans, proving they still resolve post-migration.
"""
from __future__ import annotations

import pytest

_CATALOG = __import__("pathlib").Path(__file__).resolve().parents[2] / "catalog"  # twin content root

from axrange.ir.parser import parse_ir
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.inventory import build_twin_inventory
from axrange.planner.tappoints import derive_default_tap_points, resolve_tap_point
from axrange.twinconfig.expand import materialize_twin_config


class _FakeAdapter:
    """Canned per-op outputs -- distinct vmids, a real bridge_name for every
    create-ovs-bridge. Mirrors
    `tests/test_target_environments_boundary_taps.py::_FakeAdapter`.
    """

    def __init__(self) -> None:
        self._next_vmid = 750100

    def run(self, op, resolved_inputs, ctx):
        if op.op_name == "clone-vm":
            self._next_vmid += 1
            return {"vmid": self._next_vmid}
        if op.op_name == "create-ovs-bridge":
            key = "-".join(str(op.scope.get(k)) for k in sorted(op.scope))
            return {"bridge_name": f"br-{key}"[:32]}
        return {}


def _deploy_twin_config(tmp_path, manifests, config_id: str, *, param_overrides=None, deploy_id: str):
    materialized = materialize_twin_config(
        config_id, param_overrides=param_overrides, out_path=tmp_path / f"{config_id}.yaml",
    )
    parsed = parse_ir(path=materialized)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(materialized.parent),
    )
    store = StateStore(str(tmp_path / f"{deploy_id}.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement, tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(
        plan, store, adapter, ctx, config_backend=adapter,
        deploy_id=deploy_id, sleep_fn=lambda _: None,
    )
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    twin = build_twin_inventory(store, deploy_id, manifests=manifests)
    store.close()
    return twin


def test_sme_flat_segment_point_resolves_every_workstation(tmp_path, manifests):
    twin = _deploy_twin_config(
        tmp_path, manifests, "sme-flat",
        param_overrides={"N_WORKSTATIONS": "2"}, deploy_id="sme-flat-materialize",
    )
    points = derive_default_tap_points(twin)
    seg = next((p for p in points if p.scope == "segment" and p.id == "lan"), None)
    assert seg is not None, "sme-flat should derive exactly one 'lan' segment point"
    refs = resolve_tap_point(seg, twin)
    hosts = {r.host_id for r in refs}
    # gw + 2 workstations + file-server + db-tier, ALL on the one flat net --
    # no author declaration needed for any of them (D1).
    assert hosts == {"gw", "corp-workstation01", "corp-workstation02", "file-server", "db-tier"}
    # No boundary at all -- a single-net twin has no north-south/east-west
    # split (unchanged from the pre-D1 worked example). `gw` is still a
    # router though, so it still earns a `wan-perimeter` point (EVERY
    # router gets an adapter-injected WAN leg, `pass1_emit.py` line ~682 --
    # not gated on being multi-homed) -- the flat-twin worked example is
    # "no boundary," not "no perimeter."
    assert not any(p.scope == "boundary" for p in points)
    wan_perim = next(p for p in points if p.scope == "perimeter")
    assert wan_perim.id == "wan-perimeter"
    assert {r.host_id for r in resolve_tap_point(wan_perim, twin)} == {"gw"}


def test_corp_enterprise_boundary_and_segment_points_resolve(tmp_path, manifests):
    twin = _deploy_twin_config(
        tmp_path, manifests, "corp-enterprise",
        param_overrides={"N_WORKSTATIONS": "2"}, deploy_id="corp-enterprise-materialize",
    )
    points = derive_default_tap_points(twin)
    by_id = {p.id: p for p in points}

    seg = by_id["server-vlan"]
    seg_hosts = {r.host_id for r in resolve_tap_point(seg, twin)}
    assert seg_hosts == {"gw", "domain-controller", "file-server"}

    boundary = by_id["corp-lan--dmz"]
    b_refs = resolve_tap_point(boundary, twin)
    b_hosts = {r.host_id for r in b_refs}
    # ONLY the joining router carries a corp-lan<->dmz boundary point -- no
    # author needed to flag it (D1's headline behavior change).
    assert b_hosts == {"gw"}

    # The router's WAN uplink is unconditionally eligible too (`docs/
    # WORK_ORDER_tap_scopes.md` M2/M3, carried through D1 unchanged) --
    # `perimeter: wan` resolves with zero authoring.
    wan_perim = next(p for p in points if p.id == "wan-perimeter")
    wan_refs = resolve_tap_point(wan_perim, twin)
    assert {r.host_id for r in wan_refs} == {"gw"}


@pytest.mark.parametrize("config_id", ["ics-ot", "dmz-perimeter", "datacenter"])
def test_remaining_configs_derive_a_resolvable_boundary_or_perimeter(
    tmp_path, manifests, config_id,
):
    """Bonus coverage beyond the minimum two configs -- proves full-topology
    eligibility holds across the WHOLE starter catalog, not just the two
    called out above. `datacenter` needs FABRIC=true so its fabric add-on
    (a router<->router transit link) is actually part of the deployed IR."""
    overrides = {"FABRIC": "true"} if config_id == "datacenter" else None
    twin = _deploy_twin_config(
        tmp_path, manifests, config_id, param_overrides=overrides,
        deploy_id=f"{config_id}-materialize",
    )
    points = derive_default_tap_points(twin)
    assert points, f"{config_id} derived NO tap points at all"
    joining = [p for p in points if p.scope in ("boundary", "perimeter")]
    assert joining, f"{config_id} derived no boundary/perimeter point -- expected >=1"
    for p in joining:
        refs = resolve_tap_point(p, twin)
        assert refs, f"{config_id} tap point {p.id!r} resolved to an EMPTY interface set"


# ---------------------------------------------------------------------------
# Shipped tap plans (`twins/corp-enterprise/tap/*.yaml`) still resolve
# to the same port sets post-migration (work order §9's own validation
# guidance): both plans select purely by `scope`/`target` (no `labels:`/
# `tap_point:` needing a rewrite -- confirmed by inspection during the D3
# migration). Drives the exact `_selector_from_dict` -> `select_tap_points`
# -> `resolve_tap_point` chain `_resolve_bindings` uses internally, without
# needing a standing O2 sensor surface (`resolve_tap_plan_bindings`'s
# `sensor_by_id`/`surface_status` half is sensor-ASSIGNMENT machinery,
# orthogonal to this work order's ELIGIBILITY scope -- `test_o2_taps.py`
# already covers it end to end).
# ---------------------------------------------------------------------------
def test_shipped_corp_enterprise_tap_plans_still_resolve_the_same_port_sets(
    tmp_path, manifests,
):
    from axrange.o2control.taps import _selector_from_dict, load_tap_plan, select_tap_points

    twin = _deploy_twin_config(
        tmp_path, manifests, "corp-enterprise",
        param_overrides={"N_WORKSTATIONS": "2"}, deploy_id="corp-enterprise-shipped-plans",
    )
    points = derive_default_tap_points(twin)
    expected = {
        "segment": {"gw", "domain-controller", "file-server"},
        "boundary": {"gw"},
    }
    for plan_file in (
        str(_CATALOG / "twins/corp-enterprise/tap/default.yaml"),
        str(_CATALOG / "twins/corp-enterprise/tap/reshard.yaml"),
    ):
        spec = load_tap_plan(plan_file)
        assert len(spec.bindings) == 2
        for b in spec.bindings:
            selector = _selector_from_dict(b.selector or {})
            matched = select_tap_points(points, selector)
            assert matched, f"{plan_file}: selector {b.selector!r} matched no tap point"
            hosts: set[str] = set()
            for p in matched:
                hosts |= {r.host_id for r in resolve_tap_point(p, twin)}
            assert hosts == expected[selector.scope], (
                f"{plan_file}: selector {b.selector!r} resolved to {hosts}, "
                f"expected {expected[selector.scope]}"
            )
