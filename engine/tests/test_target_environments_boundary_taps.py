"""Boundary-tap-point verification against REAL `target-environments/` IRs
(`docs/WORK_ORDER_tap_points.md` M4 part (b), content follow-on; superseded
by full-topology eligibility -- `docs/WORK_ORDER_tap_eligibility.md` D1,
Authority: user, 2026-07-28).

M4's original fix (`axrange.planner.tappoints`) made an untapped `boundary`
point fail LOUD instead of silently not existing, gated on an author-
declared `tap:` field. D1 retired that gate entirely: `derive_default_tap_
points`/`resolve_tap_point` now resolve against a twin's FULL attachment
topology, so EVERY multi-homed joining host's boundary point resolves --
tapped or not, since `tap:` no longer exists as an IR field at all (D2).
This file now proves that positive case end to end against real IRs (not
just synthetic fixtures), including the one host this order's `tap: true`
migration stripped a declaration from (`spine-e1`, formerly this file's
negative case, now just another positive one) -- it now resolves too,
since there is no more gate to fail.

Drives a REAL `build_plan` + `Executor` round trip against a fake adapter
(the exact idiom `tests/test_twin_inventory.py::_build_and_run` uses) so
`clone-vm`/`attach-vm-to-vnet`/`create-ovs-bridge`'s outputs are read back
from ACTUALLY PERSISTED op rows (LES-068), not a hand-built fixture
standing in for the real serialization path.
"""
from __future__ import annotations

from pathlib import Path

from axrange.ir.parser import parse_ir
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.inventory import build_twin_inventory
from axrange.planner.tappoints import derive_default_tap_points, resolve_tap_point

_TE_ROOT = Path(__file__).resolve().parents[1] / "target-environments"


class _FakeAdapter:
    """Canned per-op outputs for ANY op the real planner might emit across
    these full-featured archetypes (router/fabric/overlay/activity/app ops
    included) -- a distinct vmid per `clone-vm` call, a real `bridge_name`
    for every `create-ovs-bridge` (Simple- or VLAN-zone shaped), and `{}`
    for everything else (Model-C ops don't need a specific output shape to
    succeed)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._next_vmid = 600100

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == "clone-vm":
            self._next_vmid += 1
            return {"vmid": self._next_vmid}
        if op.op_name == "create-ovs-bridge":
            key = "-".join(str(op.scope.get(k)) for k in sorted(op.scope))
            return {"bridge_name": f"br-{key}"[:32]}
        return {}


def _build_real_inventory(tmp_path, manifests, scenario_yaml: Path, *, deploy_id: str):
    parsed = parse_ir(path=scenario_yaml)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw,
        scenario_dir=str(scenario_yaml.parent),
    )

    store = StateStore(str(tmp_path / f"{deploy_id}.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = _FakeAdapter()
    ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                  deploy_id=deploy_id, sleep_fn=lambda _: None)
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    twin = build_twin_inventory(store, deploy_id, manifests=manifests)
    store.close()
    return twin


def _assert_boundary_resolves(twin, *, id_: str, expect_hosts: set[str]):
    points = derive_default_tap_points(twin)
    boundary = {p.id: p for p in points if p.scope == "boundary"}
    assert id_ in boundary, (
        f"expected boundary point {id_!r}, got {sorted(boundary)}"
    )
    refs = resolve_tap_point(boundary[id_], twin)
    assert refs, f"boundary point {id_!r} resolved to an EMPTY interface set"
    hosts = {r.host_id for r in refs}
    assert hosts == expect_hosts, (
        f"boundary {id_!r}: expected hosts {expect_hosts}, resolved {hosts}"
    )
    return refs


# ---------------------------------------------------------------------------
# ot-ics-range -- the flagship case (brief §6.2's `idmz-conduit`). `conduit`
# is 3-homed (it/ot_dmz/ot); tapping the whole node (this order's content
# change) makes all THREE of its boundary pairs resolvable.
# ---------------------------------------------------------------------------
def test_ot_ics_range_conduit_boundaries_resolve(tmp_path, manifests):
    twin = _build_real_inventory(
        tmp_path, manifests, _TE_ROOT / "ot-ics-range" / "scenario.yaml",
        deploy_id="ot-ics-range-boundary-test",
    )
    for id_ in ("it--ot", "it--ot_dmz", "ot--ot_dmz"):
        _assert_boundary_resolves(twin, id_=id_, expect_hosts={"conduit"})


# ---------------------------------------------------------------------------
# enterprise-multisite -- corp-enterprise stand-in (WAN-edge site routers,
# `docs/design/09-content-overlays.md`'s `corp-enterprise`/`dmz-perimeter`
# analog). `hq-core` is 5-homed; `br1-rtr`/`br2-rtr` are 2-homed.
# ---------------------------------------------------------------------------
def test_enterprise_multisite_router_boundaries_resolve(tmp_path, manifests):
    twin = _build_real_inventory(
        tmp_path, manifests, _TE_ROOT / "enterprise-multisite" / "scenario.yaml",
        deploy_id="enterprise-multisite-boundary-test",
    )
    # hq-core's 5 zones -> 10 boundary pairs; spot-check three representative
    # ones (edge<->user, server<->data, data<->mgmt) rather than all 10.
    for a, b in (("hq-edge", "hq-user"), ("hq-data", "hq-server"), ("hq-data", "hq-mgmt")):
        id_ = "--".join(sorted((a, b)))
        _assert_boundary_resolves(twin, id_=id_, expect_hosts={"hq-core"})
    # Branch routers -- one boundary pair each.
    _assert_boundary_resolves(
        twin, id_="br1-server--br1-user", expect_hosts={"br1-rtr"},
    )
    _assert_boundary_resolves(
        twin, id_="br2-server--br2-user", expect_hosts={"br2-rtr"},
    )


# ---------------------------------------------------------------------------
# optrahealth-twin -- datacenter/fabric stand-in (BGP-EVPN spine-leaf across
# two DCs + an OSPF campus). Spot-check the access-segment-joining leaf/
# border/ABR/area routers this order tapped; the pure-P2P-underlay spines
# (spine-e1/e2/w1) are DELIBERATELY untapped (see the work-order report --
# no access-segment leg, low capture value) and so correctly derive NO
# boundary point at all -- asserted here as the negative case.
# ---------------------------------------------------------------------------
def test_optrahealth_twin_fabric_boundaries_resolve(tmp_path, manifests):
    twin = _build_real_inventory(
        tmp_path, manifests, _TE_ROOT / "optrahealth-twin" / "scenario.yaml",
        deploy_id="optrahealth-twin-boundary-test",
    )
    _assert_boundary_resolves(twin, id_="e-clin--e-dmz", expect_hosts={"leaf-e1"})
    _assert_boundary_resolves(twin, id_="e-data--e-infra", expect_hosts={"leaf-e2"})
    _assert_boundary_resolves(twin, id_="e-bl--wan-core", expect_hosts={"border-e"})
    _assert_boundary_resolves(twin, id_="w-data--w-infra", expect_hosts={"leaf-w1"})
    _assert_boundary_resolves(twin, id_="w-bl--wan-core", expect_hosts={"border-w"})
    _assert_boundary_resolves(twin, id_="c-abr-a1--wan-core", expect_hosts={"abr-c"})
    _assert_boundary_resolves(twin, id_="c-abr-a1--c-user", expect_hosts={"area-c1"})

    # Formerly a negative case (pre-D1): the pure-P2P underlay spines
    # (spine-e1/e2) were deliberately left UNTAPPED (no access-segment leg,
    # low capture value, see the work-order report), and M4's gate raised
    # `TapPointResolutionError` resolving their boundary point. D1 (`docs/
    # WORK_ORDER_tap_eligibility.md`) retired that gate: `tap:` no longer
    # exists as an IR field at all (D2), so spine-e1's own P2P pair
    # (e-s1l1<->e-s1l2) is now an ORDINARY resolvable boundary point like
    # every other one above -- a standalone tap plan MAY choose to bind it
    # (low capture value is a plan-authoring judgment call now, not an
    # engine-enforced gap). Asserted as a positive case for symmetry with
    # every other boundary in this file.
    _assert_boundary_resolves(twin, id_="e-s1l1--e-s1l2", expect_hosts={"spine-e1"})
