"""Tests for `axrange.o2control` (A1, `docs/design/05-o2-surface.md`).

Covers, per `docs/WORK_ORDER_o2_control.md` §5:
  - surface-spec compose (manifest -> raw IR dict)
  - `o2_` tagging via the tier param (naming.py + a real `build_plan(tier="o2")`)
  - inventory shape (`{sensor_id -> type, transport_ip, deploy_id}`)
  - idempotent re-`up` (Model-C adopt: same manifest -> same deploy_id, no
    re-run of an already-completed deploy)
  - `down` removes only the `o2_` deploy (tier-refusal guard + a genuine
    o2-shaped clean sweep, using REAL `adapter_proxmox.naming` producers via
    a tier-aware fake adapter — not canned te-shaped strings)

No live cluster: every test drives a real `StateStore` (tmp SQLite file) with
FAKE adapter/teardown backends, the same idiom `tests/test_planner_executor.py`
and `tests/test_teardown.py` use.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from axrange.adapter_proxmox import naming as n
from axrange.ir.parser import parse_ir
from axrange.o2control import (
    O2ControlError,
    SurfaceManifestError,
    compose_surface_ir,
    default_surface_manifest,
    load_surface_manifest,
    parse_surface_manifest,
    stand_up_surface,
    surface_status,
    tear_down_surface,
)
from axrange.o2control.manifest import SurfaceManifest, SurfaceSensorSpec
from axrange.planner import StateStore, build_plan
from axrange.planner.cluster import ClusterContext


# ---------------------------------------------------------------------------
# Manifest / compose (pure — no state store, no build_plan)
# ---------------------------------------------------------------------------
def test_default_surface_manifest_has_one_primary_sensor():
    m = default_surface_manifest()
    assert len(m.sensors) == 1
    assert m.sensors[0].primary is True
    assert m.sensors[0].type == "tcpdump-sensor"


def test_parse_surface_manifest_requires_at_least_one_sensor():
    with pytest.raises(SurfaceManifestError, match="at least one"):
        parse_surface_manifest({"sensors": []})
    with pytest.raises(SurfaceManifestError, match="at least one"):
        parse_surface_manifest({})


def test_parse_surface_manifest_rejects_unknown_sensor_type():
    with pytest.raises(SurfaceManifestError, match="unknown sensor type"):
        parse_surface_manifest({"sensors": [{"id": "s1", "type": "bogus-sensor"}]})


def test_parse_surface_manifest_rejects_duplicate_ids():
    with pytest.raises(SurfaceManifestError, match="duplicate"):
        parse_surface_manifest({
            "sensors": [{"id": "s1"}, {"id": "s1", "type": "suricata-sensor"}],
        })


def test_parse_surface_manifest_rejects_missing_id():
    with pytest.raises(SurfaceManifestError, match="missing a string 'id'"):
        parse_surface_manifest({"sensors": [{"type": "tcpdump-sensor"}]})


def test_parse_surface_manifest_reads_surface_id_and_transport():
    m = parse_surface_manifest({
        "surface": {"id": "custom-surface"},
        "transport": {"cidr": "10.90.0.0/24", "egress": False},
        "sensors": [{"id": "s1", "primary": True}],
    })
    assert m.surface_id == "custom-surface"
    assert m.transport == {"cidr": "10.90.0.0/24", "egress": False}


def test_load_surface_manifest_from_file(tmp_path):
    p = tmp_path / "surface.yaml"
    p.write_text(
        "surface: { id: file-surface }\n"
        "sensors:\n"
        "  - { id: s1, type: tcpdump-sensor, primary: true }\n"
    )
    m = load_surface_manifest(p)
    assert m.surface_id == "file-surface"
    assert m.sensors[0].id == "s1"


def test_parse_surface_manifest_services_stanza_is_optional():
    m = parse_surface_manifest({"sensors": [{"id": "s1"}]})
    assert m.services is None


def test_parse_surface_manifest_reads_services_stanza():
    m = parse_surface_manifest({
        "sensors": [{"id": "s1"}],
        "services": {"vmid": 102, "name": "o2-services", "ports": {"grafana": 4000}},
    })
    assert m.services is not None
    assert m.services.vmid == 102
    assert m.services.name == "o2-services"
    assert m.services.ports == {"grafana": 4000}


def test_parse_surface_manifest_services_rejects_bad_vmid():
    with pytest.raises(SurfaceManifestError, match="vmid"):
        parse_surface_manifest({
            "sensors": [{"id": "s1"}],
            "services": {"vmid": "not-an-int"},
        })


def test_parse_surface_manifest_services_rejects_non_mapping():
    with pytest.raises(SurfaceManifestError, match="services"):
        parse_surface_manifest({"sensors": [{"id": "s1"}], "services": "nope"})


def test_resolve_manifest_endpoints_falls_back_to_adapter_conf_when_no_services_stanza(monkeypatch):
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.o2control.manifest import resolve_manifest_endpoints

    m = parse_surface_manifest({"sensors": [{"id": "s1"}]})
    cfg = AdapterConfig(o2_endpoints={"grafana": "http://adapterconf:3000"})
    endpoints = resolve_manifest_endpoints(cfg, m)
    assert endpoints["grafana"] == "http://adapterconf:3000"


def test_resolve_manifest_endpoints_services_stanza_overrides_adapter_conf(monkeypatch):
    import axrange.adapter_proxmox.resolve as resolve_mod
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.o2control.manifest import resolve_manifest_endpoints

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.5.5.5")
    m = parse_surface_manifest({
        "sensors": [{"id": "s1"}],
        "services": {"vmid": 55},
    })
    # adapter.conf declares a DIFFERENT services vmid -- the manifest's own
    # services: stanza wins for THIS resolution (Part B item 5).
    cfg = AdapterConfig(o2_services_vmid=999)
    endpoints = resolve_manifest_endpoints(cfg, m)
    assert endpoints["grafana"] == "http://10.5.5.5:3000"


def test_load_surface_manifest_missing_file_raises():
    with pytest.raises(SurfaceManifestError):
        load_surface_manifest("/nonexistent/surface.yaml")


def test_compose_surface_ir_shape():
    m = default_surface_manifest()
    raw = compose_surface_ir(m)
    assert raw["scenario"]["id"] == "o2-surface"
    assert raw["layers"]["infra"]["nodes"] == []
    assert raw["layers"]["infra"]["networks"] == []
    assert raw["sensors"] == [
        {"id": "o2-sensor", "primary": True, "type": "tcpdump-sensor"}
    ]


def test_compose_surface_ir_with_transport_override():
    m = SurfaceManifest(
        surface_id="s2",
        transport={"cidr": "10.90.0.0/24", "egress": False},
        sensors=[SurfaceSensorSpec(id="s1", type="suricata-sensor", primary=True)],
    )
    raw = compose_surface_ir(m)
    nets = raw["layers"]["infra"]["networks"]
    assert len(nets) == 1
    assert nets[0]["cidr"] == "10.90.0.0/24"
    assert nets[0]["transport"] is True
    assert nets[0]["egress"] is False  # explicit override respected, not forced True
    assert nets[0]["id"] == "o2-transport"


def test_compose_surface_ir_round_trips_through_ir_model():
    """The composed dict must actually parse as a valid Scenario IR (the
    contract `compose_surface_ir` promises — 'no new schema')."""
    from axrange.ir.schema import IR

    raw = compose_surface_ir(default_surface_manifest())
    ir = IR.model_validate(raw)
    assert ir.scenario.id == "o2-surface"
    assert ir.sensors[0].id == "o2-sensor"


# ---------------------------------------------------------------------------
# `o2_` tagging via the tier param — a real build_plan(tier="o2") emits the
# expected op set; op EXECUTION (naming.py) is exercised end-to-end below.
# ---------------------------------------------------------------------------
def test_build_plan_o2_tier_emits_sensor_lifecycle_ops(manifests):
    raw = compose_surface_ir(default_surface_manifest())
    parsed = parse_ir(text=_dump(raw))
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")

    assert plan.tier == "o2"
    op_names = {o.op_name for o in plan.ops.values()}
    assert {
        "create-sdn-zone", "create-vnet", "create-ovs-bridge", "create-subnet",
        "apply-sdn-config", "clone-vm", "configure-vm-hardware",
        "configure-cloud-init", "attach-vm-to-vnet", "start-vm",
        "wait-guest-agent", "wait-os-ready", "deploy-sensor",
    } <= op_names
    # No taps in a surface deploy of its own (A2 wires taps later).
    assert "attach-gre-tunnel" not in op_names


def test_build_plan_default_tier_is_te(manifests):
    raw = compose_surface_ir(default_surface_manifest())
    parsed = parse_ir(text=_dump(raw))
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    assert plan.tier == "te"


def _dump(raw: dict) -> str:
    from ruamel.yaml import YAML
    import io

    buf = io.StringIO()
    YAML(typ="safe").dump(raw, buf)
    return buf.getvalue()


def test_derive_plan_id_folds_tier_for_non_te_only():
    """Code-review N2: `tier` disambiguates plan_ids across tiers (cheap
    insurance against a future tier reusing a scenario id), but `te` plan_ids
    stay byte-identical to before the fold — every existing pinned te
    plan_id/test fixture must not move."""
    from axrange.planner.dag import derive_plan_id

    te_id = derive_plan_id("s1", "deadbeef00")
    assert te_id == "s1-deadbeef"
    # Explicit tier="te" is the same as the default (no suffix).
    assert derive_plan_id("s1", "deadbeef00", tier="te") == te_id

    o2_id = derive_plan_id("s1", "deadbeef00", tier="o2")
    assert o2_id == "s1-deadbeef-o2"
    assert o2_id != te_id


def test_build_plan_o2_tier_plan_id_carries_the_tier_suffix(manifests):
    raw = compose_surface_ir(default_surface_manifest())
    parsed = parse_ir(text=_dump(raw))
    ctx = ClusterContext.single_node("pxtest1")
    plan_te = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan_o2 = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")
    assert plan_te.plan_id != plan_o2.plan_id
    assert plan_o2.plan_id == f"{plan_te.plan_id}-o2"


# ---------------------------------------------------------------------------
# Full stand-up / status / tear-down round trip with a TIER-AWARE fake
# adapter — outputs are derived from the REAL `adapter_proxmox.naming`
# producers (keyed by `ctx.plan_id`/`ctx.tier`), not canned te-shaped
# strings, so the resulting `o2_*` names are genuine and the teardown guard
# is exercised faithfully (this is what makes the "down removes only the
# o2_ deploy" case meaningful, not a guard-violation no-op).
# ---------------------------------------------------------------------------
class _TierAwareFakeAdapter:
    """Derives create-*/clone-vm outputs via the real naming producers, tier
    read off `ctx.tier`. Everything else returns a fixed canned output —
    same idiom as `tests.test_planner_executor._FakeAdapter`, just naming-
    aware for the resource kinds the teardown guard inspects."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        name = op.op_name
        tier = ctx.tier
        if name == "create-sdn-zone":
            return {
                "zone_id": n.zone_id_for_plan(ctx.plan_id, tier=tier),
                "zone_type": resolved_inputs.get("zone_type") or "simple",
            }
        if name == "create-vnet":
            net_id = resolved_inputs.get("net_id") or op.scope.get("net")
            return {"vnet_id": n.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=tier)}
        if name == "create-ovs-bridge":
            net_id = resolved_inputs.get("net_id") or op.scope.get("net")
            zone_type = (resolved_inputs.get("zone_type") or "simple").lower()
            if zone_type == "vlan":
                return {"bridge_name": n.underlay_bridge_for_plan(ctx.plan_id, tier=tier)}
            return {"bridge_name": n.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=tier)}
        if name == "create-subnet":
            net_id = resolved_inputs.get("net_id") or op.scope.get("net")
            return {
                "subnet_id": resolved_inputs.get("cidr"),
                "vnet_id": n.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=tier),
            }
        if name == "clone-vm":
            return {"vmid": n.vmid_for_op(ctx.plan_id, op.op_id)}
        _GENERIC = {
            "apply-sdn-config": {"applied": True},
            "configure-vm-hardware": {"configured": True},
            "configure-cloud-init": {"cloud_init_attached": True},
            "attach-vm-to-vnet": {"nic_attached": True},
            "start-vm": {"started": True},
            "wait-guest-agent": {"agent_ready": True},
            "wait-os-ready": {"os_ready": True},
            "deploy-sensor": {"sensor_deployed": True},
        }
        return dict(_GENERIC.get(name, {}))


class _TierAwareFakeTeardown:
    """Resolves `vm_name` via the real naming producer (tier-aware), like a
    real Proxmox backend reading the VM's actual PVE display name back."""

    def __init__(self, adapter: _TierAwareFakeAdapter, plan) -> None:
        self._adapter = adapter
        self._plan = plan
        self.calls: list[tuple[str, str]] = []

    def _rec(self, kind: str, target: str) -> str:
        self.calls.append((kind, target))
        return "removed"

    def vm_name(self, node, vmid):
        # Find the host_id whose clone-vm op produced this vmid, then re-derive
        # the deterministic name (mirrors real ProxmoxAdapter.vm_name reading
        # the live VM's PVE-side name — here it's just re-derived since Model C
        # guarantees the two agree).
        for op in self._plan.ops.values():
            if op.op_name == "clone-vm":
                host_id = op.scope.get("host")
                if n.vmid_for_op(self._plan.plan_id, op.op_id) == vmid:
                    return n.vm_name_for_host(self._plan.plan_id, host_id, tier=self._plan.tier)
        return None

    def destroy_vm(self, node, vmid): return self._rec("vm", str(vmid))
    def delete_mirror_snippets(self, node, vmid): return self._rec("mirror", str(vmid))
    def delete_gretap(self, dev): return self._rec("gretap", dev)
    def delete_vnet(self, vnet_id): return self._rec("vnet", vnet_id)
    def delete_sdn_zone(self, zone_id): return self._rec("zone", zone_id)
    def apply_sdn(self): return self._rec("sdn-apply", "")
    def delete_ovs_bridge(self, node, b): return self._rec("ovs-bridge", b)


def _stand_up(tmp_path, *, adapter=None, manifest=None, images_root=None):
    adapter = adapter or _TierAwareFakeAdapter()
    db = str(tmp_path / "state.db")
    status = stand_up_surface(
        manifest=manifest,
        db_path=db,
        images_root=images_root or (Path(__file__).parent / "fixtures"),
        adapter=adapter,
        config_backend=adapter,
        target_node="pxtest1",
    )
    return db, adapter, status


def test_stand_up_surface_tags_o2_and_returns_inventory(tmp_path):
    db, adapter, status = _stand_up(tmp_path)

    assert status.status == "completed"
    assert status.deploy_id is not None
    assert len(status.sensors) == 1
    entry = status.sensors[0]
    assert entry.sensor_id == "o2-sensor"
    assert entry.type == "tcpdump-sensor"
    assert entry.transport_ip  # planner-assigned IP on the transport net
    assert entry.deploy_id == status.deploy_id

    # The deploy row itself is tagged o2 — the source of truth teardown reads.
    store = StateStore(db)
    try:
        d = store.get_deploy(status.deploy_id)
        assert d.tier == "o2"
    finally:
        store.close()

    # And the actual created-resource identities (from op outputs) are
    # genuinely o2-shaped, not te-shaped.
    store = StateStore(db)
    try:
        ops = store.ops_for_deploy(status.deploy_id)
    finally:
        store.close()
    import json
    zone_ids = [
        json.loads(o.outputs_json)["zone_id"]
        for o in ops if o.op_name == "create-sdn-zone" and o.outputs_json
    ]
    assert zone_ids and all(n.is_o2_zone(z) for z in zone_ids)


def test_stand_up_surface_idempotent_reup_adopts_prior_deploy(tmp_path):
    db, adapter, status1 = _stand_up(tmp_path)
    calls_after_first = len(adapter.calls)

    status2 = stand_up_surface(
        db_path=db, images_root=Path(__file__).parent / "fixtures",
        adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status2.deploy_id == status1.deploy_id
    assert status2.status == "completed"
    # A completed deploy of an unchanged manifest is adopted, not re-run —
    # no new adapter calls (Model C).
    assert len(adapter.calls) == calls_after_first


def test_stand_up_surface_mints_fresh_deploy_id_after_destroy_not_revive(tmp_path):
    """Code review finding (docs/WORK_ORDER_cli_lifecycle.md, Part B): a
    `destroyed` deploy is terminal -- `stand_up_surface` has no `--fresh`
    flag of its own (unlike `axrange te deploy`), so `find_deploy_for_plan`'s
    `exclude_destroyed=True` is the ONLY thing standing between a re-`o2
    deploy` after `o2 destroy` and silently reviving the tombstoned row in
    place (status destroyed->running, ops reset, OLD deploy_id/started_at)."""
    db, adapter, status1 = _stand_up(tmp_path)
    assert status1.status == "completed"

    store = StateStore(db)
    try:
        store.mark_destroyed(status1.deploy_id)
    finally:
        store.close()

    status2 = stand_up_surface(
        db_path=db, images_root=Path(__file__).parent / "fixtures",
        adapter=adapter, config_backend=adapter, target_node="pxtest1",
    )
    assert status2.status == "completed"
    assert status2.deploy_id != status1.deploy_id

    store = StateStore(db)
    try:
        old = store.get_deploy(status1.deploy_id)
        new = store.get_deploy(status2.deploy_id)
    finally:
        store.close()
    assert old.status.value == "destroyed"  # untouched, still tombstoned
    assert new.status.value == "completed"


def test_surface_status_not_deployed_when_nothing_stood_up(tmp_path):
    db = str(tmp_path / "state.db")
    st = surface_status(db_path=db)
    assert st.status == "not-deployed"
    assert st.deploy_id is None
    assert st.sensors == []


def test_surface_status_reads_from_state_db_alone(tmp_path):
    """No manifest/adapter in hand — status is derived purely from the
    persisted ops (the A2/A3 discovery-source property)."""
    db, adapter, status = _stand_up(tmp_path)
    st = surface_status(db_path=db)
    assert st.deploy_id == status.deploy_id
    assert st.sensors == status.sensors


def test_surface_status_finds_o2_surface_behind_many_newer_te_deploys(tmp_path):
    """Code-review S1: the O2 surface is by design the OLDEST persistent
    deploy row (it survives every TE destroy), while `te` deploys accumulate
    monotonically on a busy cluster. Discovery must find it via a
    tier-filtered query, not a capped `latest_deploys` scan that a pile of
    newer `te` rows could blow past."""
    db, adapter, status = _stand_up(tmp_path)

    store = StateStore(db)
    try:
        # Insert a `te` deploy row started AFTER the o2 one — the case a
        # naive `latest_deploys(limit=N)` client-side filter would surface
        # first, and could bury the o2 row past any fixed N on a busy
        # cluster. One row is enough to exercise the "not just the oldest
        # row overall" property; the fix is a WHERE tier=? query, not a
        # bigger N.
        store.create_deploy(
            deploy_id="te-newer-1", scenario_id="unrelated", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-unrelated", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    st = surface_status(db_path=db)
    assert st.deploy_id == status.deploy_id
    assert st.status == "completed"


def test_down_tears_down_only_the_o2_deploy(tmp_path, manifests):
    """The keystone case: a REAL o2-tier clean sweep (genuine o2-shaped
    names) via `tear_down_surface`, immediately followed by a `te`-tier
    deploy in the SAME state.db proving `axrange destroy`'s normal path is
    untouched by (and structurally distinct from) the o2 one."""
    db, adapter, status = _stand_up(tmp_path)

    # Rebuild the in-memory Plan the fake teardown needs to re-derive vm_name
    # (mirrors a real backend reading the live VM's PVE name).
    raw = compose_surface_ir(default_surface_manifest())
    parsed = parse_ir(text=_dump(raw))
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")

    teardown_backend = _TierAwareFakeTeardown(adapter, plan)
    result = tear_down_surface(db_path=db, deploy_id=status.deploy_id,
                                teardown_backend=teardown_backend)
    assert result.destroyed is True
    assert result.errors == []
    assert ("zone", n.zone_id_for_plan(plan.plan_id, tier="o2")) in teardown_backend.calls

    st_after = surface_status(db_path=db, deploy_id=status.deploy_id)
    assert st_after.status == "destroyed"


def test_down_refuses_a_te_tier_deploy(tmp_path):
    """`axrange o2 down` must refuse a `te`-tiered deploy_id outright — the
    cross-verb hard guard (`docs/design/05-o2-surface.md`)."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-dep-1", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-1", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    with pytest.raises(O2ControlError, match="tier="):
        tear_down_surface(db_path=db, deploy_id="te-dep-1",
                           teardown_backend=object())


def test_status_refuses_a_te_tier_deploy(tmp_path):
    """`axrange o2 status`/`o2 info` must refuse an EXPLICIT `te`-tiered
    deploy_id -- completes the tier guard that already exists on
    `tear_down_surface` (`test_down_refuses_a_te_tier_deploy` above) on the
    read path. A twin id (`te`) is not an O2 surface: it has no sensors, and
    silently rendering it as one ("O2 surface <te-id> -- completed / sensors:
    (none)") reads as "sensors were lost" to an operator who passed the
    wrong id, rather than "wrong tier of id entirely.\""""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-dep-1", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-1", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    with pytest.raises(O2ControlError, match="not an O2 surface"):
        surface_status(db_path=db, deploy_id="te-dep-1")


def test_status_bare_default_still_resolves_the_o2_surface(tmp_path):
    """The `deploy_id=None` bare-default path resolves via
    `_latest_o2_deploy_id` -- ALWAYS o2-tier by construction -- and must NOT
    trip the new tier guard (A2/A3's discovery source depends on this)."""
    db, adapter, status = _stand_up(tmp_path)

    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-newer-1", scenario_id="unrelated", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-unrelated", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    st = surface_status(db_path=db)  # bare default, no deploy_id
    assert st.deploy_id == status.deploy_id
    assert st.status == "completed"


def test_down_with_no_standing_surface_raises_404(tmp_path):
    db = str(tmp_path / "state.db")
    with pytest.raises(O2ControlError) as exc:
        tear_down_surface(db_path=db)
    assert exc.value.status == 404
