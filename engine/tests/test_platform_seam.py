"""The deploy-platform seam (`docs/ARCHITECTURE.md` §13, `axrange.platform`).

Four properties, each one the design relies on:

  * both adapters' implementations satisfy the five protocols -- structurally
    (`runtime_checkable`) AND by signature (`inspect.signature`: the protocol
    call-site parameters are positional-only, so an implementation may name
    them in its own vocabulary, but every keyword-only name and default is the
    contract);
  * the factory is a CLOSED set that refuses an unknown platform BY NAME
    (`api/Spec.md` "Not being built": no registry, nothing self-registers);
  * the platform TRAVELS WITH THE LOADED CONFIG: `load_config` reads the
    conf's `PLATFORM=` key, stamps `cfg.platform`, and `adapter_for_config`
    reads it back -- one `/api/range` request builds its facts probe and its
    sweep from the same bundle;
  * a target's declared `platform` (A4) is reported by `GET /api/targets`,
    refused by name in the targets file (serve start) and on a programmatic
    `Target`, and cross-checked against the conf's key.

The planner's own fake-adapter suite (`test_planner*.py`, `test_teardown.py`)
is the fifth property -- the DAG does not know the platform -- and needs no
test here: it passed unchanged through the extraction.
"""
from __future__ import annotations

import inspect
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
    adapter_for_config,
    check_platform,
    default_namer,
    load_config,
    namer_for,
    platform_of,
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


# --- libvirt: the second platform through the same seams ---------------------


def _lv_cfg(**kw):
    from axrange.adapter_libvirt import config as lv_config

    base = dict(libvirt_uri="qemu:///system", storage_pool="default",
                guest_ssh_pubkey_file="/nowhere/id.pub", default_node="kvm-a")
    base.update(kw)
    return lv_config.LibvirtConfig(**base)


def _lv_classes():
    from axrange.adapter_libvirt import naming as lv_naming
    from axrange.adapter_libvirt.adapter import LibvirtAdapter
    from axrange.adapter_libvirt.guest import LibvirtGuestExec
    from axrange.adapter_libvirt.inventory import LibvirtInventory
    from axrange.adapter_libvirt.teardown import LibvirtTeardown

    return lv_naming, LibvirtAdapter, LibvirtGuestExec, LibvirtInventory, LibvirtTeardown


def test_the_libvirt_classes_satisfy_the_five_protocols():
    lv_naming, LibvirtAdapter, LibvirtGuestExec, LibvirtInventory, LibvirtTeardown = _lv_classes()
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


# --- signatures: positional-only call sites, keyword-only names are the contract


def _protocol_methods(proto: type) -> list[str]:
    return sorted(
        n for n, v in vars(proto).items()
        if callable(v) and not n.startswith("_")
    )


def _call_shape(fn, *, drop_self: bool) -> tuple[tuple[object, ...], dict[str, object]]:
    """(positional parameter defaults in order, {keyword-only name: default}) --
    the two things a protocol caller can observe. Positional parameters may
    be POSITIONAL_ONLY or POSITIONAL_OR_KEYWORD on the implementation (the
    protocol only ever calls them positionally); a keyword-only parameter
    must exist by NAME with the same default."""
    params = list(inspect.signature(fn).parameters.values())
    if drop_self and params and params[0].name == "self":
        params = params[1:]
    positional = tuple(
        p.default for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    )
    kw_only = {p.name: p.default for p in params if p.kind is p.KEYWORD_ONLY}
    assert not any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params)
    return positional, kw_only


def _implementations() -> list[tuple[type, object, str]]:
    lv_naming, _LibvirtAdapter, LibvirtGuestExec, LibvirtInventory, LibvirtTeardown = _lv_classes()
    return [
        (GuestExec, px_guest_agent.GuestAgent, "proxmox"),
        (GuestExec, LibvirtGuestExec, "libvirt"),
        (Inventory, px_resolve.ProxmoxInventory, "proxmox"),
        (Inventory, LibvirtInventory, "libvirt"),
        (ResourceNamer, px_naming, "proxmox"),
        (ResourceNamer, lv_naming, "libvirt"),
        (Teardown, ProxmoxTeardown, "proxmox"),
        (Teardown, LibvirtTeardown, "libvirt"),
    ]


def test_every_protocol_call_site_parameter_is_positional_only():
    """`runtime_checkable` compares attribute names only; the protocols make
    their call-site parameters positional-only so an implementation's own
    vocabulary (`node`/`vmid` vs `host`/`guest`) can never be reached by a
    keyword that works on one platform and TypeErrors on the other."""
    for proto in (GuestExec, Inventory, ResourceNamer, Teardown):
        for name in _protocol_methods(proto):
            params = list(inspect.signature(getattr(proto, name)).parameters.values())[1:]
            for p in params:
                assert p.kind in (p.POSITIONAL_ONLY, p.KEYWORD_ONLY), (
                    f"{proto.__name__}.{name}: parameter {p.name!r} is "
                    f"{p.kind.name}; protocol call-site parameters must be positional-only"
                )


@pytest.mark.parametrize("proto, impl, platform", _implementations(),
                         ids=lambda x: getattr(x, "__name__", str(x)))
def test_each_implementation_matches_the_protocols_call_shape(proto, impl, platform):
    is_module = not isinstance(impl, type)
    for name in _protocol_methods(proto):
        assert hasattr(impl, name), f"{platform} {proto.__name__}: missing {name}"
        want = _call_shape(getattr(proto, name), drop_self=True)
        got = _call_shape(getattr(impl, name), drop_self=not is_module)
        assert got == want, (
            f"{platform} {getattr(impl, '__name__', impl)}.{name}: "
            f"signature {got} does not match protocol {want}"
        )


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

    sentinel_adapter, sentinel_teardown = object(), object()
    sentinel_cfg = px_config.AdapterConfig(default_node="x")
    monkeypatch.setattr(px, "ProxmoxAdapter", lambda cfg: sentinel_adapter)
    monkeypatch.setattr(px, "ProxmoxTeardown", lambda cfg: sentinel_teardown)
    monkeypatch.setattr("axrange.adapter_proxmox.load_config", lambda _p=None: sentinel_cfg)
    assert load_config("/nowhere/adapter.conf") is sentinel_cfg
    a = adapter_for_config(sentinel_cfg)
    assert a.adapter is sentinel_adapter and a.teardown is sentinel_teardown


def test_the_closed_set_is_proxmox_and_libvirt_and_refuses_anything_else_by_name():
    assert PLATFORMS == frozenset({"proxmox", "libvirt"})
    assert DEFAULT_PLATFORM == "proxmox"
    for bad in ("vmware", "vsphere", "PROXMOX", "LIBVIRT", "", "  ", 7):
        with pytest.raises(UnknownPlatformError) as ei:
            adapter_for(bad, px_config.AdapterConfig(platform=bad) if isinstance(bad, str) else object())
        assert repr(bad) in str(ei.value) and "proxmox" in str(ei.value)
        with pytest.raises(UnknownPlatformError):
            namer_for(bad)
        with pytest.raises(UnknownPlatformError):
            load_config(None, platform=bad)
        with pytest.raises(UnknownPlatformError):
            check_platform(bad)
    assert issubclass(UnknownPlatformError, ValueError)


def test_check_platform_defaults_only_a_truly_absent_value():
    """`None` is absent (a YAML `platform:` with no value, a conf without
    `PLATFORM=`); an empty or blank string is a VALUE and is refused by name."""
    assert check_platform(None) == "proxmox"
    assert check_platform("proxmox") == "proxmox"
    assert check_platform("libvirt") == "libvirt"
    for blank in ("", " ", "\t"):
        with pytest.raises(UnknownPlatformError) as ei:
            check_platform(blank)
        assert repr(blank) in str(ei.value)


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


# --- the platform travels with the loaded config ------------------------------

_PX_CONF = "PROXMOX_API_URL=https://pve:8006/api2/json\nDEFAULT_NODE=pve-a\n"
_LV_CONF = (
    "LIBVIRT_URI=qemu:///system\nLIBVIRT_STORAGE_POOL=default\n"
    "GUEST_SSH_PUBKEY_FILE=/nowhere/id.pub\nDEFAULT_NODE=kvm1\n"
)


def test_load_config_picks_the_loader_from_the_confs_platform_key(tmp_path):
    from axrange.adapter_libvirt.config import LibvirtConfig

    px = tmp_path / "px.conf"
    px.write_text("PLATFORM=proxmox\n" + _PX_CONF)
    lv = tmp_path / "lv.conf"
    lv.write_text("PLATFORM=libvirt\n" + _LV_CONF)
    silent = tmp_path / "silent.conf"
    silent.write_text(_PX_CONF)

    a, b, c = load_config(px), load_config(lv), load_config(silent)
    assert isinstance(a, px_config.AdapterConfig) and a.platform == "proxmox"
    assert isinstance(b, LibvirtConfig) and b.platform == "libvirt"
    assert isinstance(c, px_config.AdapterConfig) and c.platform == "proxmox"   # absent -> default
    assert platform_of(a) == "proxmox" and platform_of(b) == "libvirt"
    assert adapter_for_config(b).platform == "libvirt"
    assert adapter_for_config(a).platform == "proxmox"
    # The declared platform selects the loader when the file is silent...
    assert isinstance(load_config(silent, platform="proxmox"), px_config.AdapterConfig)
    # ...and `overrides` carries the key too.
    assert load_config(silent, overrides={"PLATFORM": "proxmox"}).platform == "proxmox"


def test_load_config_refuses_a_platform_mismatch_and_a_blank_or_unknown_key(tmp_path):
    lv = tmp_path / "lv.conf"
    lv.write_text("PLATFORM=libvirt\n" + _LV_CONF)
    with pytest.raises(ConfigError) as ei:
        load_config(lv, platform="proxmox")
    assert "PLATFORM=libvirt" in str(ei.value) and "'proxmox'" in str(ei.value)
    # The libvirt loader refuses the key itself when it names another platform.
    from axrange.adapter_libvirt.config import load_config as lv_load
    with pytest.raises(ConfigError):
        lv_load(tmp_path / "px_named.conf") if (tmp_path / "px_named.conf").write_text("PLATFORM=proxmox\n" + _LV_CONF) else None
    for bad in ("PLATFORM=\n", "PLATFORM=vsphere\n"):
        f = tmp_path / "bad.conf"
        f.write_text(bad + _PX_CONF)
        with pytest.raises(UnknownPlatformError):
            load_config(f)


def test_adapter_for_cross_checks_the_configs_platform_and_a_bare_config_is_the_default():
    from types import SimpleNamespace

    with pytest.raises(ConfigError):
        adapter_for("libvirt", px_config.AdapterConfig())
    with pytest.raises(ConfigError):
        adapter_for("proxmox", _lv_cfg())
    # The suite's bare stand-ins carry no `platform`: absent, like a conf
    # without the key -- the default platform, never a crash.
    assert adapter_for_config(SimpleNamespace(default_node="x")).platform == "proxmox"
    with pytest.raises(UnknownPlatformError):
        adapter_for_config(SimpleNamespace(platform="vsphere"))


def test_the_platform_config_slice_is_filled_by_both_adapters():
    px = px_config.AdapterConfig(proxmox_api_url="https://pve:8006", proxmox_api_token="t",
                                 ovs_ssh_key="/k", proxmox_verify_tls=False)
    assert (px.platform, px.endpoint, px.verify_tls, px.has_token, px.has_ssh_key) == \
        ("proxmox", "https://pve:8006", False, True, True)
    lv = _lv_cfg(libvirt_uri="qemu+tls://kvm1/system", host_ssh_key="/k")
    assert (lv.platform, lv.endpoint, lv.verify_tls, lv.has_token, lv.has_ssh_key) == \
        ("libvirt", "qemu+tls://kvm1/system", True, False, True)
    assert lv.loki_url == "" and lv.network_sensor_vmid == 0
    for cfg in (px, lv):
        assert isinstance(cfg, PlatformConfig)
        for member in ("platform", "endpoint", "verify_tls", "has_token", "has_ssh_key",
                       "default_node", "default_password", "external_sensors",
                       "protected_external_names", "protected_external_vmids",
                       "loki_url", "network_sensor_vmid"):
            assert hasattr(cfg, member), member


# --- A4: the target's `platform` ---------------------------------------------


def _targets_yaml(tmp_path, entries: str) -> str:
    p = tmp_path / "targets.yaml"
    p.write_text("targets:\n" + entries)
    return str(p)


def test_a_target_without_platform_declares_none_and_one_naming_it_keeps_it(tmp_path):
    from axrange.api.targets import load_targets_file

    f = _targets_yaml(tmp_path, (
        "  - name: a\n    adapter_config: /etc/axrange/a.conf\n    db: " + str(tmp_path / "a.db") + "\n"
        "  - name: b\n    adapter_config: /etc/axrange/b.conf\n    db: " + str(tmp_path / "b.db") + "\n"
        "    platform: libvirt\n"
        "  - name: c\n    adapter_config: /etc/axrange/c.conf\n    db: " + str(tmp_path / "c.db") + "\n"
        "    platform:\n"
    ))
    a, b, c = load_targets_file(f)
    assert a.platform is None and b.platform == "libvirt" and c.platform is None


def test_an_unknown_or_blank_target_platform_is_refused_by_name_at_load(tmp_path):
    """The targets file is parsed by `create_app` -- this IS the serve-start
    refusal (`api/Spec.md` A4: "an unknown value is refused by name ... at
    serve start")."""
    from axrange.api.targets import load_targets_file

    for value in ("vsphere", '""'):
        f = _targets_yaml(tmp_path, (
            "  - name: a\n    adapter_config: /etc/axrange/a.conf\n    db: " + str(tmp_path / "a.db") + "\n"
            f"    platform: {value}\n"
        ))
        with pytest.raises(ApiSettingsError) as ei:
            load_targets_file(f)
        msg = str(ei.value)
        assert "targets[0] (a)" in msg and "proxmox" in msg


def test_a_programmatic_target_is_refused_by_name_too():
    from axrange.api.targets import Target

    with pytest.raises(UnknownPlatformError):
        Target(name="a", adapter_config=None, db_path="/x/s.db", platform="vsphere")
    assert Target(name="a", adapter_config=None, db_path="/x/s.db").platform is None
    assert Target(name="a", adapter_config=None, db_path="/x/s.db", platform="libvirt").platform == "libvirt"


def test_settings_for_target_carries_the_declared_platform(tmp_path):
    from axrange.api.settings import ApiSettings
    from axrange.api.targets import load_targets_file, settings_for_target

    f = _targets_yaml(tmp_path, (
        f"  - name: a\n    adapter_config: {tmp_path / 'a.conf'}\n    db: {tmp_path / 'a.db'}\n"
        f"  - name: b\n    adapter_config: {tmp_path / 'b.conf'}\n    db: {tmp_path / 'b.db'}\n"
        "    platform: libvirt\n"
    ))
    settings = ApiSettings(db_path=str(tmp_path / "a.db"), targets_file=f)
    settings.targets = load_targets_file(f)
    assert settings.platform is None
    assert settings_for_target(settings, "a").platform is None
    assert settings_for_target(settings, "b").platform == "libvirt"
    assert settings_for_target(settings, "b").adapter_config == str(tmp_path / "b.conf")


def test_get_api_targets_reports_the_confs_platform_for_each_target(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    px = tmp_path / "px.conf"
    px.write_text(_PX_CONF)                            # no key -> proxmox
    lv = tmp_path / "lv.conf"
    lv.write_text("PLATFORM=libvirt\n" + _LV_CONF)
    mismatch = tmp_path / "mismatch.conf"
    mismatch.write_text("PLATFORM=libvirt\n" + _LV_CONF)
    f = _targets_yaml(tmp_path, (
        f"  - name: a\n    adapter_config: {px}\n    db: {tmp_path / 'a.db'}\n"
        f"  - name: b\n    adapter_config: {lv}\n    db: {tmp_path / 'b.db'}\n    platform: libvirt\n"
        f"  - name: c\n    adapter_config: {lv}\n    db: {tmp_path / 'c.db'}\n"
        f"  - name: d\n    adapter_config: {mismatch}\n    db: {tmp_path / 'd.db'}\n    platform: proxmox\n"
    ))
    # Three targets share one libvirt URI (M1 would refuse); the test only
    # needs the DTO shape, so the duplicate-endpoint identity check is bypassed.
    monkeypatch.setattr("axrange.api.targets.validate_targets", lambda targets, **_k: None)
    app = create_app(ApiSettings(db_path=str(tmp_path / "a.db"), targets_file=f))
    with TestClient(app) as c:
        body = c.get("/api/targets").json()
    by_name = {t["name"]: t for t in body["targets"]}
    assert (by_name["a"]["platform"], by_name["a"]["api_url"]) == ("proxmox", "https://pve:8006/api2/json")
    assert (by_name["b"]["platform"], by_name["b"]["api_url"]) == ("libvirt", "qemu:///system")
    assert by_name["c"]["platform"] == "libvirt"            # undeclared: the conf's key decides
    assert by_name["d"]["platform"] == "proxmox" and by_name["d"]["error"]
    assert "PLATFORM=libvirt" in by_name["d"]["error"] and "'proxmox'" in by_name["d"]["error"]


def test_the_implicit_single_target_reports_the_default_platform(tmp_path):
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings

    conf = tmp_path / "adapter.conf"
    conf.write_text(_PX_CONF)
    app = create_app(ApiSettings(db_path=str(tmp_path / "s.db"), adapter_config=str(conf)))
    with TestClient(app) as c:
        body = c.get("/api/targets").json()
    assert len(body["targets"]) == 1 and body["targets"][0]["platform"] == "proxmox"


def test_api_range_probes_and_sweeps_a_libvirt_target_through_the_libvirt_seam(tmp_path, monkeypatch):
    """The reviewer's D1: one `/api/range` request used to build the facts
    probe with the target's platform and the sweep with the default one -- a
    500 on a libvirt target. Both now come from the loaded config's own
    platform; proven here with a fake libvirt connection (the adapter's own
    test fakes) and NO Proxmox client in the path."""
    from fastapi.testclient import TestClient

    from axrange.api.app import create_app
    from axrange.api.settings import ApiSettings
    from tests.test_adapter_libvirt import FakeConn

    conn = FakeConn()
    conn.facts = {"hostname": "kvm1", "hypervisor_version": 8002000, "libvirt_version": 10000000}
    conn.add_domain("te-abc123-web", "11111111-2222-5333-8444-555555555555", active=True)
    calls: list[str] = []
    monkeypatch.setattr(
        "axrange.adapter_libvirt.connection.LibvirtConnection",
        lambda cfg: (calls.append(cfg.platform), conn)[1],
    )
    monkeypatch.setattr(
        "axrange.adapter_proxmox.rest.RestClient",
        lambda cfg: (_ for _ in ()).throw(AssertionError("a Proxmox client was built for a libvirt target")),
    )
    lv = tmp_path / "lv.conf"
    lv.write_text("PLATFORM=libvirt\n" + _LV_CONF)
    f = _targets_yaml(tmp_path, (
        f"  - name: kvm\n    adapter_config: {lv}\n    db: {tmp_path / 'kvm.db'}\n    platform: libvirt\n"
    ))
    app = create_app(ApiSettings(db_path=str(tmp_path / "kvm.db"), targets_file=f))
    with TestClient(app) as c:
        r = c.get("/api/range", params={"live": "true"})
    assert r.status_code == 200, r.text
    t = r.json()["targets"][0]
    assert t["name"] == "kvm" and t["api_url"] == "qemu:///system"
    assert t["reachability"]["state"] == "reachable", t["reachability"]
    assert t["reachability"]["detail"] == "libvirt 10000000"
    # The sweep ran through the same seam (the fake connection answered it)...
    assert t["unrecorded"]["state"] == "known", t["unrecorded"]
    # ...and every connection the request opened was the libvirt one.
    assert calls and set(calls) == {"libvirt"}
