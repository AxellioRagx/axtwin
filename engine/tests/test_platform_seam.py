"""The deploy-platform seam (`docs/ARCHITECTURE.md` §13, `axrange.platform`).

Three properties, each one the design relies on:

  * the Proxmox implementations satisfy the five protocols -- structurally
    (`runtime_checkable`), not by inheritance, so `adapters/libvirt/` can do
    the same with a module or a class of its own;
  * the factory is a CLOSED set that refuses an unknown platform BY NAME
    (`api/Spec.md` "Not being built": no registry, nothing self-registers);
  * a target carries `platform` (A4): `proxmox` when absent, reported by
    `GET /api/targets`, refused by name in the targets file (serve start).

The planner's own fake-adapter suite (`test_planner*.py`, `test_teardown.py`)
is the fourth property -- the DAG does not know the platform -- and needs no
test here: it passed unchanged through the extraction.
"""
from __future__ import annotations

import pytest

from axrange.adapter_proxmox import config as px_config
from axrange.adapter_proxmox import guest_agent as px_guest_agent
from axrange.adapter_proxmox import naming as px_naming
from axrange.adapter_proxmox import resolve as px_resolve
from axrange.adapter_proxmox.adapter import ProxmoxAdapter
from axrange.adapter_proxmox.teardown import ProxmoxTeardown
from axrange.api.settings import ApiSettingsError
from axrange.planner.executor import Adapter
from axrange.platform import (
    DEFAULT_PLATFORM,
    PLATFORMS,
    ConfigError,
    ExternalSensorSpec,
    GuestExec,
    GuestResult,
    Inventory,
    PlatformAdapters,
    PlatformConfig,
    ResourceNamer,
    Teardown,
    UnknownPlatformError,
    adapter_for,
    check_platform,
    default_namer,
    load_config,
    namer_for,
)

# --- protocol conformance (structural, checked at runtime) --------------------


def test_the_proxmox_classes_satisfy_the_five_protocols():
    cfg = px_config.AdapterConfig()
    assert isinstance(ProxmoxAdapter(cfg), Adapter)
    assert isinstance(px_guest_agent.GuestAgent(object(), "node-a"), GuestExec)
    assert isinstance(px_resolve.ProxmoxInventory(cfg), Inventory)
    assert isinstance(px_naming, ResourceNamer)   # a module satisfies it too
    assert isinstance(ProxmoxTeardown(cfg, client=object()), Teardown)


def test_the_proxmox_value_types_satisfy_the_result_config_and_sensor_shapes():
    assert isinstance(px_guest_agent.GuestExecResult(0, "out", ""), GuestResult)
    assert isinstance(px_config.AdapterConfig(), PlatformConfig)
    assert isinstance(
        px_config.ExternalSensor(id="s1", vmid=42, transport_ip="10.0.0.9"), ExternalSensorSpec,
    )


def test_the_proxmox_config_error_is_the_seams_config_error():
    """`cli/main.py`'s dispatch backstop catches ONE type for every platform."""
    import axrange.adapter_proxmox as px

    assert px.ConfigError is ConfigError
    with pytest.raises(ConfigError):
        px_config.load_config(None, overrides={"EXTERNAL_SENSORS": "not json"})


def test_the_namer_maps_the_platform_identity_through_the_neutral_names():
    """`guest_handle_for_op` / `is_managed_guest_handle` are the neutral
    spellings of the Proxmox VMID functions -- same values, no second
    derivation."""
    n = namer_for("proxmox")
    assert n.guest_handle_for_op("plan-1", "op-1") == px_naming.vmid_for_op("plan-1", "op-1")
    assert n.is_managed_guest_handle(n.guest_handle_for_op("plan-1", "op-1"))
    assert n.is_managed_guest_handle(str(n.guest_handle_for_op("plan-1", "op-1")))
    assert not n.is_managed_guest_handle("not-a-handle")
    assert not n.is_managed_guest_handle(100)   # the pre-engine VMID range
    assert n.tier_predicates() is px_naming.TIER_PREDICATES
    assert n.tier_shape_checks() is px_naming.TIER_SHAPE_CHECKS
    assert default_namer() is n


# --- the closed factory -------------------------------------------------------


def test_the_factory_bundles_the_five_seams_for_proxmox():
    cfg = px_config.AdapterConfig(default_node="pve-a")
    a = adapter_for("proxmox", cfg)
    assert isinstance(a, PlatformAdapters)
    assert a.platform == "proxmox" and a.config is cfg
    assert isinstance(a.adapter, Adapter)
    assert isinstance(a.inventory, Inventory)
    assert isinstance(a.namer, ResourceNamer)
    assert isinstance(a.teardown, Teardown)
    ga = a.guest_exec("pve-b", os_family="windows")
    assert isinstance(ga, GuestExec)
    assert ga.node == "pve-b" and ga.is_windows   # bound to the host asked for
    # One connection per bundle: the inventory and the guest channel share it.
    assert a.inventory.client is a.client
    assert a.guest_exec("pve-a").client is a.client


def test_the_factory_is_lazy_so_a_destroy_builds_no_ops_adapter(monkeypatch):
    import axrange.adapter_proxmox as px

    monkeypatch.setattr(px, "ProxmoxAdapter", lambda cfg: (_ for _ in ()).throw(AssertionError("built")))
    a = adapter_for("proxmox", px_config.AdapterConfig())
    assert isinstance(a.teardown, Teardown)   # never touches ProxmoxAdapter


def test_the_factory_looks_proxmox_names_up_at_call_time(monkeypatch):
    """The suite's seams monkeypatch `axrange.adapter_proxmox.ProxmoxAdapter`
    / `ProxmoxTeardown` / `load_config`; the factory must keep honouring them
    (it binds nothing at import)."""
    import axrange.adapter_proxmox as px

    sentinel_adapter, sentinel_teardown, sentinel_cfg = object(), object(), object()
    monkeypatch.setattr(px, "ProxmoxAdapter", lambda cfg: sentinel_adapter)
    monkeypatch.setattr(px, "ProxmoxTeardown", lambda cfg: sentinel_teardown)
    monkeypatch.setattr("axrange.adapter_proxmox.load_config", lambda _p=None: sentinel_cfg)
    assert load_config("/nowhere/adapter.conf") is sentinel_cfg
    a = adapter_for("proxmox", sentinel_cfg)
    assert a.adapter is sentinel_adapter and a.teardown is sentinel_teardown


def test_the_closed_set_is_proxmox_and_libvirt_and_refuses_anything_else_by_name():
    assert PLATFORMS == frozenset({"proxmox", "libvirt"})
    assert DEFAULT_PLATFORM == "proxmox"
    for bad in ("vmware", "vsphere", "PROXMOX", "LIBVIRT", "", None, 7):
        if bad in ("", None):
            continue  # those mean "absent" for `check_platform`, below
        with pytest.raises(UnknownPlatformError) as ei:
            adapter_for(bad, px_config.AdapterConfig())
        assert repr(bad) in str(ei.value) and "proxmox" in str(ei.value)
        with pytest.raises(UnknownPlatformError):
            namer_for(bad)
        with pytest.raises(UnknownPlatformError):
            load_config(None, platform=bad)
        with pytest.raises(UnknownPlatformError):
            check_platform(bad)
    assert issubclass(UnknownPlatformError, ValueError)


def test_check_platform_defaults_an_absent_value_and_passes_a_known_one():
    assert check_platform(None) == "proxmox"
    assert check_platform("") == "proxmox"
    assert check_platform("   ") == "proxmox"
    assert check_platform("proxmox") == "proxmox"
    assert check_platform("libvirt") == "libvirt"


# --- libvirt: the second platform through the same seams ---------------------


def _lv_cfg(**kw):
    from axrange.adapter_libvirt import config as lv_config

    base = dict(libvirt_uri="qemu:///system", storage_pool="default",
                guest_ssh_pubkey_file="/nowhere/id.pub", default_node="kvm-a")
    base.update(kw)
    return lv_config.LibvirtConfig(**base)


def test_the_libvirt_classes_satisfy_the_five_protocols():
    from axrange.adapter_libvirt import naming as lv_naming
    from axrange.adapter_libvirt.adapter import LibvirtAdapter
    from axrange.adapter_libvirt.guest import LibvirtGuestExec
    from axrange.adapter_libvirt.inventory import LibvirtInventory
    from axrange.adapter_libvirt.teardown import LibvirtTeardown

    cfg = _lv_cfg()
    assert isinstance(LibvirtAdapter(cfg, conn=object()), Adapter)
    assert isinstance(LibvirtGuestExec(object(), "kvm-a"), GuestExec)
    assert isinstance(LibvirtInventory(cfg, client=object()), Inventory)
    assert isinstance(lv_naming, ResourceNamer)
    assert isinstance(LibvirtTeardown(cfg, client=object()), Teardown)
    assert isinstance(cfg, PlatformConfig)


def test_the_libvirt_config_error_is_the_seams_config_error():
    import axrange.adapter_libvirt as lv

    assert lv.ConfigError is ConfigError
    with pytest.raises(ConfigError):
        load_config("/nowhere/adapter.conf", platform="libvirt")   # required keys missing


def test_the_factory_bundles_the_five_seams_for_libvirt():
    cfg = _lv_cfg()
    a = adapter_for("libvirt", cfg)
    assert a.platform == "libvirt" and a.config is cfg
    assert isinstance(a.adapter, Adapter)
    assert isinstance(a.inventory, Inventory)
    assert isinstance(a.namer, ResourceNamer)
    assert isinstance(a.teardown, Teardown)
    ga = a.guest_exec("kvm-a", os_family="windows")
    assert isinstance(ga, GuestExec) and ga.node == "kvm-a" and ga.is_windows
    # One lazily-opened connection per bundle, shared by inventory + guest channel;
    # nothing opened libvirt (there is no `libvirt` module on this box).
    assert a.inventory.client is a.client and ga.client is a.client
    assert a.client._raw is None
    assert namer_for("libvirt") is a.namer and namer_for("libvirt") is not namer_for("proxmox")


def test_the_libvirt_namer_mints_a_uuid_handle_and_shares_the_engine_shapes():
    """The shapes are the engine's (byte-identical across platforms); the
    handle is libvirt's own (a UUID5 string, never an int)."""
    lv, px = namer_for("libvirt"), namer_for("proxmox")
    for fn, args in (
        ("zone_id_for_plan", ("plan-1",)), ("underlay_bridge_for_plan", ("plan-1",)),
        ("vm_name_for_host", ("plan-1", "h-a")), ("img_vm_name_for_image", ("debian-12-cloud",)),
        ("gre_port_for_bridge_sensor", ("tebr_abc", "s1")),
    ):
        assert getattr(lv, fn)(*args) == getattr(px, fn)(*args)
    assert lv.vnet_id_for_net("corp", plan_id="plan-1") == px.vnet_id_for_net("corp", plan_id="plan-1")
    assert lv.tier_predicates() is px.tier_predicates() and lv.tier_shape_checks() is px.tier_shape_checks()
    h = lv.guest_handle_for_op("plan-1", "op-1")
    assert isinstance(h, str) and h == lv.guest_handle_for_op("plan-1", "op-1")
    assert h != lv.guest_handle_for_op("plan-1", "op-2")
    assert lv.is_managed_guest_handle(h)
    assert not lv.is_managed_guest_handle(px.guest_handle_for_op("plan-1", "op-1"))   # a VMID is not ours
    assert not px.is_managed_guest_handle(h)                                           # and vice versa


def test_there_is_no_registry_surface():
    """`api/Spec.md`: a platform plugin REGISTRY is not being built. The
    factory module exposes no register/unregister/entry-point hook, and the
    set is a frozenset derived from one private table."""
    import axrange.platform.factory as f

    assert not [n for n in dir(f) if "register" in n.lower()]
    assert isinstance(f.PLATFORMS, frozenset)
    assert set(f._BUILDERS) == set(f.PLATFORMS)


# --- A4: the target's `platform` ---------------------------------------------


def _targets_yaml(tmp_path, entries: str) -> str:
    p = tmp_path / "targets.yaml"
    p.write_text("targets:\n" + entries)
    return str(p)


def test_a_target_without_platform_is_proxmox_and_one_naming_it_keeps_it(tmp_path):
    from axrange.api.targets import load_targets_file

    f = _targets_yaml(tmp_path, (
        "  - name: a\n    adapter_config: /etc/axrange/a.conf\n    db: " + str(tmp_path / "a.db") + "\n"
        "  - name: b\n    adapter_config: /etc/axrange/b.conf\n    db: " + str(tmp_path / "b.db") + "\n"
        "    platform: proxmox\n"
    ))
    a, b = load_targets_file(f)
    assert a.platform == "proxmox" and b.platform == "proxmox"


def test_an_unknown_target_platform_is_refused_by_name_at_load(tmp_path):
    """The targets file is parsed by `create_app` -- this IS the serve-start
    refusal (`api/Spec.md` A4: "an unknown value is refused by name ... at
    serve start")."""
    from axrange.api.targets import load_targets_file

    f = _targets_yaml(tmp_path, (
        "  - name: a\n    adapter_config: /etc/axrange/a.conf\n    db: " + str(tmp_path / "a.db") + "\n"
        "    platform: vsphere\n"
    ))
    with pytest.raises(ApiSettingsError) as ei:
        load_targets_file(f)
    msg = str(ei.value)
    assert "targets[0] (a)" in msg and "'vsphere'" in msg and "proxmox" in msg


def test_get_api_targets_reports_each_targets_platform(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    conf = tmp_path / "adapter.conf"
    conf.write_text("PROXMOX_API_URL=https://pve:8006/api2/json\nDEFAULT_NODE=pve-a\n")
    f = _targets_yaml(tmp_path, (
        f"  - name: a\n    adapter_config: {conf}\n    db: {tmp_path / 'a.db'}\n    platform: proxmox\n"
        f"  - name: b\n    adapter_config: {conf}\n    db: {tmp_path / 'b.db'}\n"
    ))
    # Two targets may not share one API URL (M1); the test only needs the
    # DTO shape, so the duplicate-URL identity check is bypassed here.
    monkeypatch.setattr("axrange.api.targets.validate_targets", lambda targets, **_k: None)
    app = create_app(ApiSettings(db_path=str(tmp_path / "a.db"), targets_file=f))
    with TestClient(app) as c:
        body = c.get("/api/targets").json()
    assert [(t["name"], t["platform"]) for t in body["targets"]] == [("a", "proxmox"), ("b", "proxmox")]


def test_the_implicit_single_target_reports_the_default_platform(tmp_path):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    conf = tmp_path / "adapter.conf"
    conf.write_text("PROXMOX_API_URL=https://pve:8006/api2/json\nDEFAULT_NODE=pve-a\n")
    app = create_app(ApiSettings(db_path=str(tmp_path / "s.db"), adapter_config=str(conf)))
    with TestClient(app) as c:
        body = c.get("/api/targets").json()
    assert len(body["targets"]) == 1 and body["targets"][0]["platform"] == "proxmox"
