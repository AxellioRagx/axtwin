"""The libvirt adapter (`axrange.adapter_libvirt`, `adapters/libvirt/SPEC.md`)
against a FAKE libvirt connection -- this box has no libvirt packages and no
`libvirt` python module, and nothing here may need them.

The fake (`FakeConn` + the domain/network/pool/volume objects) implements the
`connection.Connection` method set over in-memory XML, records every define /
update / agent exchange, and scripts the guest agent's replies; the host
backend gets a recording runner in place of `subprocess`. The SPEC's
Acceptance / Automated list, in order: every op in the mapping table, the
deterministic UUID/name derivation, check-then-act adoption of an existing
domain or network, the logical ops' outputs, the teardown guard refusing `o2`
and `img`, plus the config loader's refusals, the factory selecting
`libvirt`, and one two-host plan run end to end through the real planner and
executor (the DAG does not know the platform).
"""
from __future__ import annotations

import base64
import json
import re
import uuid as _uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from axrange.adapter_libvirt import (
    AdapterError,
    AdapterTimeout,
    ConfigError,
    LibvirtAdapter,
    LibvirtConfig,
    LibvirtGuestExec,
    LibvirtInventory,
    LibvirtTeardown,
    load_config,
)
from axrange.adapter_libvirt import naming
from axrange.adapter_libvirt.adapter import GUEST_OPS, HOST_OPS, LIBVIRT_OPS, LOGICAL_OPS, _OP_TO_METHOD
from axrange.adapter_libvirt.bindings import LibvirtBindings
from axrange.adapter_libvirt.config import KNOWN_KEYS, example_conf_text
from axrange.adapter_libvirt.connection import (
    VIR_DOMAIN_XML_INACTIVE,
    VIR_NETWORK_SECTION_PORTGROUP,
    VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
    VIR_NETWORK_UPDATE_COMMAND_DELETE,
    Connection,
    LibvirtConnection,
)
from axrange.adapter_libvirt.domain_xml import disk_sources, interfaces, metadata_of
from axrange.adapter_libvirt.host import ISO_TOOLS, HostBackend
from axrange.adapter_libvirt.teardown import ProtectedResource
from axrange.adapter_proxmox import naming as px_naming
from axrange.ops.registry import IdempotencyClass, OpKind
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"
LV_BINDINGS = FIXTURES / "images" / "bindings" / "libvirt"
PLAN = "plan-lv-1"
HOST = "kvm1"
POOL_PATH = "/var/lib/libvirt/images"


# ===========================================================================
# the fake libvirt
# ===========================================================================


def _text(root: ET.Element, path: str) -> str | None:
    el = root.find(path)
    return (el.text or "").strip() if el is not None else None


class FakeVol:
    def __init__(self, pool: "FakePool", name: str, capacity: int, backing: str | None = None) -> None:
        self.pool, self._name, self.capacity, self.backing = pool, name, capacity, backing
        self.resized: list[int] = []

    def name(self) -> str:
        return self._name

    def path(self) -> str:
        return f"{self.pool.target}/{self._name}"

    def info(self) -> list[int]:
        return [0, self.capacity, self.capacity]

    def resize(self, capacity: int, flags: int = 0) -> None:
        self.resized.append(capacity)
        self.capacity = capacity

    def delete(self, flags: int = 0) -> None:
        self.pool.vols.pop(self._name, None)


class FakePool:
    def __init__(self, name: str, target: str = POOL_PATH) -> None:
        self._name, self.target = name, target
        self.vols: dict[str, FakeVol] = {}
        self.created: list[tuple[str, str]] = []   # (how, xml)
        self.refreshed = 0

    def name(self) -> str:
        return self._name

    def add(self, name: str, capacity: int = 8 << 30) -> FakeVol:
        v = FakeVol(self, name, capacity)
        self.vols[name] = v
        return v

    def _from_xml(self, xml: str) -> FakeVol:
        root = ET.fromstring(xml)
        v = FakeVol(self, _text(root, "name") or "", int(_text(root, "capacity") or 0), _text(root, "backingStore/path"))
        self.vols[v.name()] = v
        return v

    def createXML(self, xml: str, flags: int = 0) -> FakeVol:
        self.created.append(("createXML", xml))
        return self._from_xml(xml)

    def createXMLFrom(self, xml: str, src: FakeVol, flags: int = 0) -> FakeVol:
        self.created.append(("createXMLFrom", xml))
        return self._from_xml(xml)

    def refresh(self, flags: int = 0) -> None:
        self.refreshed += 1

    def XMLDesc(self, flags: int = 0) -> str:
        return f"<pool type='dir'><name>{self._name}</name><target><path>{self.target}</path></target></pool>"


class FakeDomain:
    def __init__(self, xml: str, conn: "FakeConn | None" = None) -> None:
        self.xml = xml
        self.conn = conn
        self.active = False
        self.created = self.destroyed = 0
        self.undefined: list[int] = []
        self.addresses: dict[int, dict] = {}   # by interfaceAddresses source

    def _root(self) -> ET.Element:
        return ET.fromstring(self.xml)

    def name(self) -> str:
        return _text(self._root(), "name") or ""

    def UUIDString(self) -> str:
        return _text(self._root(), "uuid") or ""

    def isActive(self) -> bool:
        return self.active

    def create(self) -> None:
        self.created += 1
        self.active = True

    def destroy(self) -> None:
        self.destroyed += 1
        self.active = False

    def undefineFlags(self, flags: int) -> None:
        self.undefined.append(flags)
        if self.conn is not None:   # as libvirt: a lookup after undefine fails
            self.conn.domains.pop(self.UUIDString(), None)

    def undefine(self) -> None:
        self.undefineFlags(0)

    def XMLDesc(self, flags: int = 0) -> str:
        return self.xml

    def interfaceAddresses(self, source: int, flags: int = 0) -> dict:
        if source not in self.addresses:
            raise RuntimeError("libvirtError: no addresses")
        return self.addresses[source]


class FakeNetwork:
    def __init__(self, xml: str, conn: "FakeConn | None" = None) -> None:
        self.xml = xml
        self.conn = conn
        self.active = False
        self.autostart = False
        self.updates: list[tuple[int, int, str, int]] = []
        self.undefined = self.destroyed = 0

    def name(self) -> str:
        return _text(ET.fromstring(self.xml), "name") or ""

    def isActive(self) -> bool:
        return self.active

    def create(self) -> None:
        self.active = True

    def destroy(self) -> None:
        self.destroyed += 1
        self.active = False

    def undefine(self) -> None:
        self.undefined += 1
        if self.conn is not None:
            self.conn.networks.pop(self.name(), None)

    def setAutostart(self, on: bool) -> None:
        self.autostart = bool(on)

    def XMLDesc(self, flags: int = 0) -> str:
        return self.xml

    def update(self, command: int, section: int, idx: int, xml: str, flags: int) -> None:
        self.updates.append((command, section, xml, flags))
        assert section == VIR_NETWORK_SECTION_PORTGROUP
        root = ET.fromstring(self.xml)
        pg = ET.fromstring(xml)
        if command == VIR_NETWORK_UPDATE_COMMAND_ADD_LAST:
            root.append(pg)
        elif command == VIR_NETWORK_UPDATE_COMMAND_DELETE:
            for old in root.findall("portgroup"):
                if old.get("name") == pg.get("name"):
                    root.remove(old)
        self.xml = ET.tostring(root, encoding="unicode")


class FakeAgent:
    """Scripted QGA: `handlers[execute] -> reply or callable(arguments)`;
    every exchange recorded verbatim (the JSON the real agent would see)."""

    def __init__(self) -> None:
        self.exchanges: list[dict[str, Any]] = []
        self.handlers: dict[str, Any] = {
            "guest-ping": {"return": {}},
            "guest-exec": {"return": {"pid": 42}},
            "guest-exec-status": {"return": {"exited": True, "exitcode": 0, "out-data": "", "err-data": ""}},
            "guest-file-open": {"return": 7},
            "guest-file-write": lambda a: {"return": {"count": len(base64.b64decode(a["buf-b64"])), "eof": False}},
            "guest-file-close": {"return": {}},
        }

    def __call__(self, dom: FakeDomain, command: dict[str, Any], timeout_s: int | None) -> dict[str, Any]:
        self.exchanges.append(json.loads(json.dumps(command)))
        if not dom.isActive():
            raise AdapterError("domain is not running", error_class="qga-unreachable")
        h = self.handlers[command["execute"]]
        return h(command.get("arguments", {})) if callable(h) else h

    def of(self, execute: str) -> list[dict[str, Any]]:
        return [e for e in self.exchanges if e["execute"] == execute]


class FakeConn:
    """`connection.Connection` over dicts."""

    def __init__(self) -> None:
        self.domains: dict[str, FakeDomain] = {}
        self.networks: dict[str, FakeNetwork] = {}
        self.pools: dict[str, FakePool] = {}
        self.agent = FakeAgent()
        self.defined: list[str] = []
        self.network_defined: list[str] = []
        self.facts = {"hostname": HOST, "hypervisor_version": 8002000, "libvirt_version": 10000000}

    # domains
    def domain_by_uuid(self, uuid: str) -> FakeDomain | None:
        return self.domains.get(str(uuid))

    def domain_by_name(self, name: str) -> FakeDomain | None:
        return next((d for d in self.domains.values() if d.name() == name), None)

    def all_domains(self) -> list[FakeDomain]:
        return list(self.domains.values())

    def define_domain(self, xml: str) -> FakeDomain:
        self.defined.append(xml)
        u = _text(ET.fromstring(xml), "uuid") or ""
        if u in self.domains:
            self.domains[u].xml = xml
        else:
            self.domains[u] = FakeDomain(xml, self)
        return self.domains[u]

    def add_domain(self, name: str, uuid: str, *, active: bool = False, disk: str | None = None) -> FakeDomain:
        d = FakeDomain(f"<domain type='kvm'><name>{name}</name><uuid>{uuid}</uuid><devices>"
                       + (f"<disk type='file' device='disk'><source file='{disk}'/></disk>" if disk else "")
                       + "</devices></domain>", self)
        d.active = active
        self.domains[uuid] = d
        return d

    # networks
    def network_by_name(self, name: str) -> FakeNetwork | None:
        return self.networks.get(name)

    def all_networks(self) -> list[FakeNetwork]:
        return list(self.networks.values())

    def define_network(self, xml: str) -> FakeNetwork:
        self.network_defined.append(xml)
        n = FakeNetwork(xml, self)
        self.networks[n.name()] = n
        return n

    # storage
    def pool_by_name(self, name: str) -> FakePool | None:
        return self.pools.get(name)

    def volume_by_name(self, pool_name: str, vol_name: str) -> FakeVol | None:
        p = self.pools.get(pool_name)
        return p.vols.get(vol_name) if p else None

    def pool_path(self, pool_name: str) -> str:
        p = self.pools.get(pool_name)
        if p is None:
            raise AdapterError(f"pool {pool_name!r} absent", error_class="adapter-config")
        return p.target

    # agent + facts
    def agent_command(self, domain: FakeDomain, command: dict[str, Any], timeout_s: int | None) -> dict[str, Any]:
        return self.agent(domain, command, timeout_s)

    def host_facts(self) -> dict[str, Any]:
        return dict(self.facts)


class RecordingRunner:
    """The host backend's subprocess seam. `respond(substring, (rc, out, err))`
    scripts an answer for commands containing `substring`; default success."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.responses: list[tuple[str, tuple[int, str, str]]] = []

    def respond(self, needle: str, rc: int = 0, out: str = "", err: str = "") -> None:
        self.responses.append((needle, (rc, out, err)))

    def __call__(self, argv: list[str], *, input: str | None = None, timeout_s: int = 60) -> tuple[int, str, str]:
        self.calls.append((list(argv), input))
        cmd = argv[-1]
        for needle, reply in self.responses:
            if needle in cmd:
                return reply
        return 0, "", ""

    @property
    def cmds(self) -> list[str]:
        return [a[-1] for a, _ in self.calls]

    def having(self, needle: str) -> list[str]:
        return [c for c in self.cmds if needle in c]


# ===========================================================================
# scaffolding
# ===========================================================================


def _cfg(**kw: Any) -> LibvirtConfig:
    base = dict(libvirt_uri="qemu:///system", storage_pool="default",
                guest_ssh_pubkey_file="/nowhere/id.pub", default_node=HOST,
                bindings_dir=str(LV_BINDINGS))
    base.update(kw)
    return LibvirtConfig(**base)


def _ctx(*, tier: str = "te", sensor_ip: str = "10.255.255.10") -> ExecutionContext:
    return ExecutionContext(
        plan_id=PLAN, deploy_id="dep-1",
        cluster=ClusterContext.single_node(HOST, sensor_transport_ip=sensor_ip),
        placement={"h-a": HOST, "h-b": HOST}, tier=tier,
    )


def _op(name: str, scope: dict[str, str], inputs: dict[str, Any] | None = None, *, timeout_s: int | None = None) -> OpInstance:
    return OpInstance(
        op_id=f"{PLAN}:{name}[{','.join(f'{k}={v}' for k, v in sorted(scope.items()))}]",
        op_name=name, op_catalog_id="OP-X", kind=OpKind.ACTION, scope=scope,
        idempotency_class=IdempotencyClass.INFRA, inputs_resolved=inputs or {}, timeout_s=timeout_s,
    )


class Rig:
    """One adapter over one fake connection + recording host runner."""

    def __init__(self, **cfg_kw: Any) -> None:
        self.cfg = _cfg(**cfg_kw)
        self.conn = FakeConn()
        self.pool = FakePool("default")
        self.conn.pools["default"] = self.pool
        self.pool.add("img-debian-12-cloud.qcow2", 8 << 30)
        self.runner = RecordingRunner()
        self.runner.respond("command -v", 0, "/usr/bin/genisoimage\n")
        self.runner.respond("mktemp -d", 0, "/tmp/axr-seed.abc\n")
        self.host = HostBackend(self.cfg, runner=self.runner)
        self.adapter = LibvirtAdapter(self.cfg, conn=self.conn, host=self.host, sleep=lambda _s: None)

    def run(self, name: str, scope: dict[str, str], inputs: dict[str, Any] | None = None, *, ctx: ExecutionContext | None = None, **kw: Any) -> dict[str, Any]:
        op = _op(name, scope, inputs, **kw)
        return self.adapter.run(op, dict(op.inputs_resolved), ctx or _ctx())

    def clone(self, host_id: str = "h-a", **extra: Any) -> str:
        out = self.run("clone-vm", {"host": host_id}, {"image_id": "debian-12-cloud", "node_id": host_id, **extra})
        return out["vmid"]


def _vnet(net: str = "corp", *, tier: str = "te") -> str:
    return naming.vnet_id_for_net(net, plan_id=PLAN, tier=tier)


# ===========================================================================
# config
# ===========================================================================


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "adapter.conf"
    p.write_text(text)
    return p


_MIN_CONF = (
    "LIBVIRT_URI=qemu:///system\nLIBVIRT_STORAGE_POOL=default\n"
    "GUEST_SSH_PUBKEY_FILE=~/.ssh/id.pub\nDEFAULT_NODE=kvm1\n"
)


def test_config_loads_the_spec_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_NODE", raising=False)
    p = _write(tmp_path, _MIN_CONF + "LIBVIRT_NETWORK_BACKEND=ovs\nMIRROR_GRETAP_TXQUEUELEN=5000\n"
               "LIBVIRT_BINDINGS_DIR=~/bindings\n")
    cfg = load_config(p)
    assert cfg.libvirt_uri == "qemu:///system" and cfg.storage_pool == "default"
    assert cfg.network_backend == "ovs" and cfg.mirror_gretap_txqueuelen == 5000
    assert cfg.default_node == "kvm1" and not cfg.is_remote
    assert cfg.guest_ssh_pubkey_file.startswith("/") and cfg.bindings_dir.endswith("/bindings")


def test_config_refuses_an_unknown_key_by_name(tmp_path):
    p = _write(tmp_path, _MIN_CONF + "PROXMOX_API_URL=https://pve:8006\n")
    with pytest.raises(ConfigError, match="unknown key.*PROXMOX_API_URL"):
        load_config(p)
    with pytest.raises(ConfigError, match="overrides: unknown key.*OVS_SSH_HOST"):
        load_config(_write(tmp_path, _MIN_CONF), overrides={"OVS_SSH_HOST": "x"})


def test_config_refuses_the_proxmox_example_file():
    """Pointing a libvirt target at the Proxmox adapter.conf fails loud,
    naming the foreign keys, before any op runs."""
    px = Path(__file__).resolve().parents[2] / "config" / "adapter.conf.example"
    with pytest.raises(ConfigError, match="PROXMOX_API_TOKEN"):
        load_config(px)


def test_config_refuses_missing_required_keys(tmp_path):
    with pytest.raises(ConfigError, match="missing required key.*LIBVIRT_STORAGE_POOL.*DEFAULT_NODE"):
        load_config(_write(tmp_path, "LIBVIRT_URI=qemu:///system\nGUEST_SSH_PUBKEY_FILE=/k.pub\n"))
    with pytest.raises(ConfigError, match="missing required"):
        load_config(tmp_path / "absent.conf")


def test_config_remote_uri_requires_the_host_ssh_keys(tmp_path):
    remote = _MIN_CONF.replace("qemu:///system", "qemu+ssh://root@kvm1.lab/system")
    with pytest.raises(ConfigError, match="remote.*HOST_SSH_HOST, HOST_SSH_USER, HOST_SSH_KEY"):
        load_config(_write(tmp_path, remote))
    cfg = load_config(_write(tmp_path, remote + "HOST_SSH_HOST=kvm1.lab\nHOST_SSH_USER=root\nHOST_SSH_KEY=~/.ssh/k\n"))
    assert cfg.is_remote and cfg.host_ssh_key.startswith("/")


def test_config_refuses_a_bad_backend_or_scheme(tmp_path):
    with pytest.raises(ConfigError, match="LIBVIRT_NETWORK_BACKEND"):
        load_config(_write(tmp_path, _MIN_CONF + "LIBVIRT_NETWORK_BACKEND=sriov\n"))
    with pytest.raises(ConfigError, match="scheme 'vpx'"):
        load_config(_write(tmp_path, _MIN_CONF.replace("qemu:///system", "vpx://vc/dc")))


def test_config_precedence_overrides_env_file(tmp_path, monkeypatch):
    p = _write(tmp_path, _MIN_CONF)
    monkeypatch.setenv("LIBVIRT_STORAGE_POOL", "from-env")
    assert load_config(p).storage_pool == "from-env"
    assert load_config(p, overrides={"LIBVIRT_STORAGE_POOL": "from-override"}).storage_pool == "from-override"


def test_config_external_sensors_fold_into_the_protect_lists(tmp_path):
    p = _write(tmp_path, _MIN_CONF + 'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,"transport_ip":"10.255.250.20"}]\n')
    cfg = load_config(p)
    assert cfg.external_sensors[0].vmid == 250
    assert "XPress-SW" in cfg.protected_external_names and 250 in cfg.protected_external_vmids
    with pytest.raises(ConfigError):
        load_config(p, overrides={"EXTERNAL_SENSORS": "not json"})


def test_the_example_conf_loads_with_this_loader(tmp_path):
    cfg = load_config(_write(tmp_path, example_conf_text()))
    assert cfg.libvirt_uri == "qemu:///system" and cfg.default_node == "kvm1"
    documented = set(re.findall(r"^#?\s?([A-Z_]+)=", example_conf_text(), re.M))
    assert documented == set(KNOWN_KEYS)


# ===========================================================================
# naming
# ===========================================================================


def test_guest_handle_is_a_deterministic_uuid5_of_plan_and_op():
    a = naming.guest_handle_for_op("plan-A", "plan-A:clone-vm[host=h-a]")
    b = naming.guest_handle_for_op("plan-A", "plan-A:clone-vm[host=h-a]")
    c = naming.guest_handle_for_op("plan-A", "plan-A:clone-vm[host=h-b]")
    d = naming.guest_handle_for_op("plan-B", "plan-A:clone-vm[host=h-a]")
    assert a == b and len({a, c, d}) == 3
    assert _uuid.UUID(a).version == 5
    assert a == str(_uuid.uuid5(naming.AXRANGE_GUEST_NAMESPACE, "plan-A:plan-A:clone-vm[host=h-a]"))


def test_is_managed_guest_handle_is_the_version_5_check():
    assert naming.is_managed_guest_handle(naming.guest_handle_for_op("p", "o"))
    assert not naming.is_managed_guest_handle(str(_uuid.uuid4()))    # a hand-made domain
    assert not naming.is_managed_guest_handle(100123)                # a Proxmox VMID
    assert not naming.is_managed_guest_handle("not-a-uuid")
    assert not naming.is_managed_guest_handle(None)                  # type: ignore[arg-type]


def test_per_guest_names_derive_from_the_handle_alone():
    g = naming.guest_handle_for_op("p", "o")
    assert naming.overlay_volume_name(g) == f"axr-{g}-disk0.qcow2"
    assert naming.seed_iso_name(g) == f"axr-{g}-seed.iso"
    assert naming.hook_script_path(g) == f"/etc/libvirt/hooks/qemu.d/axrange-mirror-{g}"
    for i in (0, 1, 7, 12):
        tap = naming.tap_name_for_nic(g, i)
        assert len(tap) <= 15 and tap.endswith(f"i{i}") and not tap.startswith(("vnet", "vif", "macvtap"))
    assert naming.tap_name_for_nic(g, 0) != naming.tap_name_for_nic(naming.guest_handle_for_op("p", "o2"), 0)


def test_the_tier_shapes_are_the_engines_not_a_copy():
    """Same functions, not look-alikes: the tables are the one Proxmox-side
    object, so the teardown guard cannot drift between platforms."""
    assert naming.TIER_PREDICATES is px_naming.TIER_PREDICATES
    assert naming.TIER_SHAPE_CHECKS is px_naming.TIER_SHAPE_CHECKS
    assert naming.vm_name_for_host is px_naming.vm_name_for_host
    assert naming.is_img("img-debian-12-cloud") and naming.is_te_vm_name(naming.vm_name_for_host(PLAN, "h-a"))


# ===========================================================================
# the op table
# ===========================================================================


def test_every_catalog_op_in_the_spec_table_is_routed_once():
    assert set(_OP_TO_METHOD) == LOGICAL_OPS | LIBVIRT_OPS | GUEST_OPS | HOST_OPS
    assert set(_OP_TO_METHOD) == {
        "create-sdn-zone", "create-ovs-bridge", "create-vnet", "create-subnet", "apply-sdn-config",
        "clone-vm", "configure-vm-hardware", "configure-cloud-init", "attach-vm-to-vnet", "start-vm",
        "wait-guest-agent", "wait-os-ready", "configure-router", "configure-routing",
        "attach-gre-tunnel", "install-ovs-mirror",
    }
    assert not (LOGICAL_OPS & LIBVIRT_OPS) and not (GUEST_OPS & HOST_OPS)


def test_unknown_op_is_refused_by_name():
    rig = Rig()
    with pytest.raises(AdapterError) as ei:
        rig.run("deploy-sensor", {})
    assert ei.value.error_class == "adapter-unsupported" and "deploy-sensor" in str(ei.value)


# ---- logical ops ----------------------------------------------------------


def test_logical_ops_record_outputs_and_make_no_call():
    rig = Rig()
    zone = rig.run("create-sdn-zone", {"scenario": "s"}, {"scenario_id": "s", "zone_type": "simple"})
    assert zone == {"zone_id": naming.zone_id_for_plan(PLAN), "zone_type": "simple"}
    sub = rig.run("create-subnet", {"net": "corp"}, {"net_id": "corp", "cidr": "10.0.0.0/24", "gateway": "10.0.0.1"})
    assert sub == {"subnet_id": "10.0.0.0/24", "vnet_id": _vnet()}
    simple_bridge = rig.run("create-ovs-bridge", {"net": "corp"}, {"net_id": "corp", "zone_type": "simple"})
    assert simple_bridge == {"bridge_name": _vnet()}   # the segment network IS the bridge
    assert rig.conn.network_defined == [] and rig.conn.defined == [] and rig.runner.calls == []


# ---- networks -------------------------------------------------------------


def test_create_vnet_simple_zone_defines_and_starts_an_isolated_network():
    rig = Rig()
    out = rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "cidr": "10.0.0.0/24"})
    v = _vnet()
    assert out == {"vnet_id": v}
    net = rig.conn.networks[v]
    assert net.active and net.autostart
    root = ET.fromstring(rig.conn.network_defined[0])
    assert root.find("name").text == v and root.find("bridge").get("name") == v
    assert root.find("forward") is None            # isolated: no uplink, no NAT
    assert root.find("ip") is None                 # no libvirt DHCP; addressing is cloud-init's
    assert rig.runner.calls == []                  # linux backend: libvirt owns the bridge


def test_create_vnet_adopts_an_existing_network_and_starts_it():
    rig = Rig()
    v = _vnet()
    existing = rig.conn.define_network(f"<network><name>{v}</name><bridge name='{v}'/></network>")
    rig.conn.network_defined.clear()
    assert rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"}) == {"vnet_id": v}
    assert rig.conn.network_defined == [] and rig.conn.networks[v] is existing
    assert existing.active and existing.autostart   # a defined-but-down network heals


def test_create_vnet_ovs_backend_creates_the_bridge_first():
    rig = Rig(network_backend="ovs")
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"})
    v = _vnet()
    assert rig.runner.having(f"ovs-vsctl --may-exist add-br {v}")
    root = ET.fromstring(rig.conn.network_defined[0])
    assert root.find("virtualport").get("type") == "openvswitch" and root.find("forward").get("mode") == "bridge"


def test_vlan_zone_needs_the_ovs_backend():
    rig = Rig()
    with pytest.raises(AdapterError) as ei:
        rig.run("create-ovs-bridge", {"scenario": "s"}, {"zone_type": "vlan"})
    assert ei.value.error_class == "adapter-config" and "LIBVIRT_NETWORK_BACKEND=ovs" in str(ei.value)


def test_vlan_zone_underlay_network_and_tagged_portgroups():
    rig = Rig(network_backend="ovs")
    underlay = naming.underlay_bridge_for_plan(PLAN)
    assert rig.run("create-ovs-bridge", {"scenario": "s"}, {"zone_type": "vlan"}) == {"bridge_name": underlay}
    net = rig.conn.networks[underlay]
    assert net.active and rig.runner.having(f"add-br {underlay}")
    out = rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "vlan": 10})
    assert out == {"vnet_id": _vnet()}
    cmd, section, xml, flags = net.updates[0]
    assert cmd == VIR_NETWORK_UPDATE_COMMAND_ADD_LAST and section == VIR_NETWORK_SECTION_PORTGROUP
    pg = ET.fromstring(xml)
    assert pg.get("name") == _vnet() and pg.find("vlan/tag").get("id") == "10"
    assert flags == 3   # live + config
    # idempotent: the portgroup is adopted, not added twice
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "vlan": 10})
    assert len(net.updates) == 1
    # re-running the underlay op adopts the network
    rig.conn.network_defined.clear()
    rig.run("create-ovs-bridge", {"scenario": "s"}, {"zone_type": "vlan"})
    assert rig.conn.network_defined == []


def test_vlan_vnet_without_its_underlay_fails_loud():
    rig = Rig(network_backend="ovs")
    with pytest.raises(AdapterError, match="underlay network"):
        rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "vlan": 10})


def test_apply_sdn_config_starts_this_plans_networks_only():
    rig = Rig()
    mine = rig.conn.define_network(f"<network><name>{_vnet()}</name></network>")
    other = rig.conn.define_network(f"<network><name>{naming.vnet_id_for_net('x', plan_id='other-plan', tier='o2')}</name></network>")
    foreign = rig.conn.define_network("<network><name>default</name></network>")
    out = rig.run("apply-sdn-config", {"scenario": "s"}, {"zone_id": naming.zone_id_for_plan(PLAN)})
    assert out["applied"] is True and out["networks"] == 1
    assert mine.active and mine.autostart
    assert not other.active and not foreign.active


# ---- clone-vm --------------------------------------------------------------


def test_clone_vm_makes_a_linked_overlay_and_defines_the_domain():
    rig = Rig()
    op = _op("clone-vm", {"host": "h-a"}, {"image_id": "debian-12-cloud", "node_id": "h-a", "clone_mode": "linked"})
    out = rig.adapter.run(op, dict(op.inputs_resolved), _ctx())
    g = naming.guest_handle_for_op(PLAN, op.op_id)
    assert out == {"vmid": g}
    how, vol_xml = rig.pool.created[0]
    vr = ET.fromstring(vol_xml)
    assert how == "createXML"
    assert vr.find("name").text == naming.overlay_volume_name(g)
    assert vr.find("backingStore/path").text == f"{POOL_PATH}/img-debian-12-cloud.qcow2"
    assert vr.find("backingStore/format").get("type") == "qcow2" and vr.find("target/format").get("type") == "qcow2"
    assert int(vr.find("capacity").text) == 8 << 30
    dom = rig.conn.domains[g]
    xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
    assert dom.name() == naming.vm_name_for_host(PLAN, "h-a") and dom.UUIDString() == g
    assert disk_sources(xml) == [f"{POOL_PATH}/{naming.overlay_volume_name(g)}"]
    assert metadata_of(xml) == {"plan": PLAN, "op": op.op_id, "tier": "te", "pool": "default"}
    root = ET.fromstring(xml)
    assert root.find("./os/type").get("machine") == "pc" and root.find("os").get("firmware") is None
    assert root.find("vcpu").text == "2" and root.find("memory").text == str(2 * 1024 * 1024)
    assert root.find("./devices/channel/target").get("name") == "org.qemu.guest_agent.0"
    assert root.find("./devices/disk/target").get("bus") == "virtio"
    assert not dom.isActive()


def test_clone_vm_is_check_then_act_on_the_uuid():
    rig = Rig()
    g = rig.clone()
    n_defined, n_created = len(rig.conn.defined), len(rig.pool.created)
    assert rig.clone() == g                           # same op id -> same uuid -> adopted
    assert len(rig.conn.defined) == n_defined and len(rig.pool.created) == n_created


def test_clone_vm_refuses_a_foreign_domain_at_its_uuid_or_name():
    rig = Rig()
    op = _op("clone-vm", {"host": "h-a"}, {"image_id": "debian-12-cloud"})
    g = naming.guest_handle_for_op(PLAN, op.op_id)
    rig.conn.add_domain("someone-elses-vm", g)
    with pytest.raises(AdapterError) as ei:
        rig.adapter.run(op, dict(op.inputs_resolved), _ctx())
    assert ei.value.error_class == "vmid-collision-foreign" and "someone-elses-vm" in str(ei.value)
    rig2 = Rig()
    rig2.conn.add_domain(naming.vm_name_for_host(PLAN, "h-a"), str(_uuid.uuid4()))
    with pytest.raises(AdapterError) as ei:
        rig2.adapter.run(op, dict(op.inputs_resolved), _ctx())
    assert ei.value.error_class == "vmid-collision-foreign"


def test_clone_vm_names_what_is_missing():
    rig = Rig()
    rig.pool.vols.clear()
    with pytest.raises(AdapterError) as ei:
        rig.clone()
    assert ei.value.error_class == "template-missing" and "img-debian-12-cloud.qcow2" in str(ei.value)
    with pytest.raises(AdapterError) as ei:
        rig.run("clone-vm", {"host": "h-a"}, {"image_id": "no-such-image"})
    assert ei.value.error_class == "binding-missing" and "no-such-image" in str(ei.value)
    with pytest.raises(AdapterError) as ei:
        rig.run("clone-vm", {"host": "h-a"}, {"template_vmid": 610})   # a Proxmox-only input
    assert ei.value.error_class == "adapter-input" and "template_volume" in str(ei.value)
    rig.conn.pools.clear()
    with pytest.raises(AdapterError, match="storage pool 'default'"):
        rig.clone()


def test_clone_vm_takes_an_explicit_template_volume_without_a_binding():
    rig = Rig(bindings_dir="")
    rig.pool.add("custom.qcow2")
    g = rig.run("clone-vm", {"host": "h-a"}, {"template_volume": "custom.qcow2"})["vmid"]
    assert ET.fromstring(rig.pool.created[0][1]).find("backingStore/path").text == f"{POOL_PATH}/custom.qcow2"
    assert rig.conn.domains[g].name() == naming.vm_name_for_host(PLAN, "h-a")


def test_clone_vm_full_clone_copies_the_template_and_uses_the_bindings_domain_template():
    rig = Rig()
    rig.conn.pools["fast"] = fast = FakePool("fast", "/fast")
    fast.add("img-windows-server-2022.qcow2", 40 << 30)
    g = rig.run("clone-vm", {"host": "dc"}, {"image_id": "windows-server-2022", "node_id": "dc"})["vmid"]
    how, vol_xml = fast.created[0]
    assert how == "createXMLFrom" and ET.fromstring(vol_xml).find("backingStore") is None
    xml = rig.conn.domains[g].xml
    root = ET.fromstring(xml)
    assert root.find("./features/hyperv") is not None            # the binding's own document
    assert root.find("./devices/tpm") is not None
    assert root.find("./devices/channel/target").get("name") == "org.qemu.guest_agent.0"   # asserted after
    assert root.find("os").get("firmware") == "efi" and root.find("./os/type").get("machine") == "q35"
    assert root.find("vcpu").text == "4" and root.find("memory").text == str(8 * 1024 * 1024)
    assert metadata_of(xml)["pool"] == "fast"


# ---- configure-vm-hardware -----------------------------------------------


def test_configure_vm_hardware_redefines_and_grows_the_overlay():
    rig = Rig()
    g = rig.clone()
    out = rig.run("configure-vm-hardware", {"host": "h-a"},
                  {"vmid": g, "cpu": 4, "ram": "4Gi", "disk": "20Gi", "machine": "q35", "bios": "ovmf", "agent": True})
    assert out == {"configured": True}
    root = ET.fromstring(rig.conn.domains[g].xml)
    assert root.find("vcpu").text == "4" and root.find("memory").text == str(4 * 1024 * 1024)
    assert root.find("currentMemory").text == str(4 * 1024 * 1024)
    assert root.find("./os/type").get("machine") == "q35" and root.find("os").get("firmware") == "efi"
    assert root.find("./devices/channel/target").get("name") == "org.qemu.guest_agent.0"
    overlay = rig.pool.vols[naming.overlay_volume_name(g)]
    assert overlay.resized == [20 << 30]
    # Model C: a smaller or equal disk is a no-op, not a shrink
    rig.run("configure-vm-hardware", {"host": "h-a"}, {"vmid": g, "disk": "10Gi"})
    assert overlay.resized == [20 << 30]
    rig.run("configure-vm-hardware", {"host": "h-a"}, {"vmid": g, "bios": "seabios"})
    assert ET.fromstring(rig.conn.domains[g].xml).find("os").get("firmware") is None


def test_hardware_on_an_undefined_domain_fails_by_handle():
    rig = Rig()
    with pytest.raises(AdapterError) as ei:
        rig.run("configure-vm-hardware", {"host": "h-a"}, {"vmid": str(_uuid.uuid4()), "cpu": 1})
    assert ei.value.error_class == "domain-absent"


# ---- configure-cloud-init -------------------------------------------------


def _put_files(runner: RecordingRunner) -> dict[str, str]:
    """path -> content of every file the host backend delivered (last wins)."""
    out = {}
    for argv, inp in runner.calls:
        m = re.search(r"cat > (\S+)", argv[-1])
        if m and inp is not None:
            out[m.group(1).strip("'")] = inp
    return out


def _seed_files(runner: RecordingRunner) -> dict[str, str]:
    """name -> content of every file put under the seed scratch dir."""
    return {Path(p).name: c for p, c in _put_files(runner).items() if "/axr-seed." in p}


def test_configure_cloud_init_builds_a_nocloud_seed_iso_and_attaches_it(tmp_path):
    pub = tmp_path / "id.pub"
    pub.write_text("ssh-ed25519 AAAAtest operator@lab\n")
    rig = Rig(guest_ssh_pubkey_file=str(pub), libvirt_uri="qemu+ssh://root@kvm1.lab/system",
              host_ssh_host="kvm1.lab", host_ssh_user="root", host_ssh_key="/k")
    g = rig.clone()
    ip_config = {"corp": {"ip": "10.66.20.11", "cidr": "10.66.20.0/24", "gateway": "10.66.20.1"}}
    out = rig.run("configure-cloud-init", {"host": "h-a"},
                  {"vmid": g, "node_id": "h-a", "hostname": "h-a", "ci_user": "debian", "ip_config": ip_config,
                   "os_family": "linux"})
    iso = f"{POOL_PATH}/{naming.seed_iso_name(g)}"
    assert out == {"cloud_init_attached": True, "seed_iso": iso, "iso_tool": "genisoimage"}
    files = _seed_files(rig.runner)
    assert set(files) == {"meta-data", "user-data", "network-config"}
    assert f"instance-id: {g}" in files["meta-data"] and "local-hostname: h-a" in files["meta-data"]
    ud = files["user-data"]
    assert ud.startswith("#cloud-config") and "hostname: h-a" in ud and "name: debian" in ud
    assert "ssh-ed25519 AAAAtest" in ud and "qemu-guest-agent" in ud and f"debian:{rig.cfg.default_password}" in ud
    nc = files["network-config"]
    assert "version: 2" in nc and naming.mac_for_nic(PLAN, "h-a", 0) in nc
    assert "addresses: [10.66.20.11/24]" in nc and "via: 10.66.20.1" in nc
    build = rig.runner.having("genisoimage -quiet")
    assert build and f"-output {iso}" in build[0] and "-volid cidata" in build[0] and "/tmp/axr-seed.abc" in build[0]
    assert rig.runner.having("rm -rf /tmp/axr-seed.abc")
    assert rig.pool.refreshed == 1
    root = ET.fromstring(rig.conn.domains[g].xml)
    cd = [d for d in root.findall("./devices/disk") if d.get("device") == "cdrom"]
    assert len(cd) == 1 and cd[0].find("source").get("file") == iso
    assert cd[0].find("target").attrib == {"dev": "hdc", "bus": "ide"} and cd[0].find("readonly") is not None
    # remote URI: everything went over ssh with the configured key and user
    for argv, _ in rig.runner.calls:
        assert argv[0] == "ssh" and "-i" in argv and "/k" in argv and "root@kvm1.lab" in argv


def test_configure_cloud_init_probes_the_iso_tool_and_fails_loud_without_one():
    rig = Rig()
    rig.runner.responses = [r for r in rig.runner.responses if r[0] != "command -v"]
    rig.runner.respond("command -v", 0, "/usr/bin/xorriso\n")
    g = rig.clone()
    out = rig.run("configure-cloud-init", {"host": "h-a"}, {"vmid": g, "ip_config": {"corp": "dhcp"}})
    assert out["iso_tool"] == "xorriso" and rig.runner.having("xorriso -as mkisofs")
    assert "dhcp4: true" in _seed_files(rig.runner)["network-config"]
    bare = Rig()
    bare.runner.responses = [r for r in bare.runner.responses if r[0] != "command -v"]
    g2 = bare.clone()
    with pytest.raises(AdapterError) as ei:
        bare.run("configure-cloud-init", {"host": "h-a"}, {"vmid": g2})
    assert ei.value.error_class == "host-tool-missing" and all(n in str(ei.value) for n, _ in ISO_TOOLS)


def test_configure_cloud_init_windows_and_q35_cdrom():
    rig = Rig()
    rig.conn.pools["fast"] = fast = FakePool("fast", "/fast")
    fast.add("img-windows-server-2022.qcow2")
    g = rig.run("clone-vm", {"host": "dc"}, {"image_id": "windows-server-2022"})["vmid"]
    rig.run("configure-cloud-init", {"host": "dc"}, {"vmid": g, "hostname": "dc", "os_family": "windows"})
    ud = _seed_files(rig.runner)["user-data"]
    assert ud.startswith("<powershell>") and "Rename-Computer -NewName 'dc'" in ud
    root = ET.fromstring(rig.conn.domains[g].xml)
    cd = [d for d in root.findall("./devices/disk") if d.get("device") == "cdrom"][0]
    assert cd.find("target").attrib == {"dev": "sda", "bus": "sata"}
    assert cd.find("source").get("file") == f"/fast/{naming.seed_iso_name(g)}"   # the binding's pool
    # re-run re-points the same CD-ROM, never adds a second one
    rig.run("configure-cloud-init", {"host": "dc"}, {"vmid": g, "hostname": "dc", "os_family": "windows"})
    assert len([d for d in ET.fromstring(rig.conn.domains[g].xml).findall("./devices/disk") if d.get("device") == "cdrom"]) == 1


# ---- attach-vm-to-vnet / start-vm -----------------------------------------


def test_attach_vm_to_vnet_pins_mac_tap_and_network():
    rig = Rig()
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"})
    g = rig.clone()
    out = rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "net_id": "corp", "nic_index": 0})
    assert out == {"nic_attached": True}
    nics = interfaces(rig.conn.domains[g].xml)
    assert nics == [{"index": 0, "mac": naming.mac_for_nic(PLAN, "h-a", 0), "tap": naming.tap_name_for_nic(g, 0),
                     "network": _vnet(), "portgroup": None, "bridge": None}]
    # a second NIC appends; re-attaching NIC 0 replaces it (no duplicate, same MAC)
    rig.run("create-vnet", {"net": "ext"}, {"net_id": "ext"})
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet("ext"), "nic_index": 1})
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 0})
    nics = interfaces(rig.conn.domains[g].xml)
    assert [n["network"] for n in nics] == [_vnet(), _vnet("ext")]
    assert nics[1]["mac"] == naming.mac_for_nic(PLAN, "h-a", 1) and nics[1]["tap"] == naming.tap_name_for_nic(g, 1)
    assert ET.fromstring(rig.conn.domains[g].xml).find("./devices/interface/model").get("type") == "virtio"


def test_attach_vm_to_vnet_uses_the_portgroup_in_a_vlan_zone():
    rig = Rig(network_backend="ovs")
    rig.run("create-ovs-bridge", {"scenario": "s"}, {"zone_type": "vlan"})
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "vlan": 10})
    g = rig.clone()
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 0})
    nic = interfaces(rig.conn.domains[g].xml)[0]
    assert nic["network"] == naming.underlay_bridge_for_plan(PLAN) and nic["portgroup"] == _vnet()
    with pytest.raises(AdapterError, match="neither a network nor a portgroup"):
        rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": "tev00000", "nic_index": 1})


def test_start_vm_is_idempotent():
    rig = Rig()
    g = rig.clone()
    assert rig.run("start-vm", {"host": "h-a"}, {"vmid": g}) == {"started": True}
    assert rig.run("start-vm", {"host": "h-a"}, {"vmid": g}) == {"started": True}
    assert rig.conn.domains[g].created == 1 and rig.conn.domains[g].isActive()


# ---- the guest channel ----------------------------------------------------


def test_wait_guest_agent_polls_guest_ping_to_the_budget():
    rig = Rig()
    g = rig.clone()
    rig.conn.domains[g].active = True
    answers = iter([AdapterError("not yet", error_class="qga-unreachable")] * 2 + [{"return": {}}] * 10)

    def flaky(_args):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    rig.conn.agent.handlers["guest-ping"] = flaky
    assert rig.run("wait-guest-agent", {"host": "h-a"}, {"vmid": g, "first_boot_budget": 120}) == {"agent_ready": True}
    assert len(rig.conn.agent.of("guest-ping")) == 3
    assert rig.conn.agent.of("guest-ping")[0] == {"execute": "guest-ping"}


def test_wait_guest_agent_times_out_on_a_stopped_domain(monkeypatch):
    rig = Rig()
    g = rig.clone()   # never started: the fake agent raises
    clock = iter(range(0, 100000, 50))
    monkeypatch.setattr("axrange.adapter_libvirt.guest.time.time", lambda: next(clock))
    with pytest.raises(AdapterTimeout):
        rig.run("wait-guest-agent", {"host": "h-a"}, {"vmid": g}, timeout_s=100)


def _running(rig: Rig) -> str:
    g = rig.clone()
    rig.conn.domains[g].active = True
    return g


def test_wait_os_ready_linux_runs_cloud_init_status_over_the_agent():
    rig = Rig()
    g = _running(rig)
    assert rig.run("wait-os-ready", {"host": "h-a"}, {"vmid": g, "os_family": "linux", "first_boot_budget": 60}) == {"os_ready": True}
    ex = rig.conn.agent.of("guest-exec")[0]
    assert ex == {"execute": "guest-exec",
                  "arguments": {"path": "cloud-init", "arg": ["status", "--wait"], "capture-output": True}}
    assert rig.conn.agent.of("guest-exec-status")[0] == {"execute": "guest-exec-status", "arguments": {"pid": 42}}
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {"exited": True, "exitcode": 2}}
    assert rig.run("wait-os-ready", {"host": "h-a"}, {"vmid": g})["os_ready"] is True   # recoverable warnings
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {"exited": True, "exitcode": 1,
                                                               "out-data": base64.b64encode(b"boom").decode()}}
    with pytest.raises(AdapterError) as ei:
        rig.run("wait-os-ready", {"host": "h-a"}, {"vmid": g})
    assert ei.value.error_class == "cloud-init-failed" and "boom" in str(ei.value)


def test_wait_os_ready_windows_polls_the_marker_in_powershell():
    rig = Rig()
    g = _running(rig)
    out = rig.run("wait-os-ready", {"host": "h-a"}, {"vmid": g, "os_family": "windows"})
    assert out == {"os_ready": True}
    ex = rig.conn.agent.of("guest-exec")[0]["arguments"]
    assert ex["path"] == "powershell" and "Test-Path" in ex["arg"][-1] and "axr-osready" in ex["arg"][-1]


def test_guest_exec_speaks_the_qga_json_and_decodes_base64_output():
    rig = Rig()
    g = _running(rig)
    ga = LibvirtGuestExec(rig.conn, HOST, sleep=lambda _s: None)
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {
        "exited": True, "exitcode": 3,
        "out-data": base64.b64encode("héllo\n".encode()).decode(), "err-data": base64.b64encode(b"warn").decode(),
    }}
    res = ga.shell_exec(g, "echo hi", timeout_s=30)
    assert (res.exit_code, res.stdout, res.stderr, res.ok) == (3, "héllo\n", "warn", False)
    assert rig.conn.agent.of("guest-exec")[-1] == {"execute": "guest-exec",
                                                    "arguments": {"path": "sh", "arg": ["-lc", "echo hi"], "capture-output": True}}
    win = LibvirtGuestExec(rig.conn, HOST, os_family="windows", sleep=lambda _s: None)
    win.shell_exec(g, "Get-Date")
    assert rig.conn.agent.of("guest-exec")[-1]["arguments"]["path"] == "powershell"
    rig.conn.agent.handlers["guest-exec"] = {"error": {"class": "GenericError", "desc": "Guest agent command failed"}}
    with pytest.raises(AdapterError) as ei:
        ga.exec(g, ["true"])
    assert ei.value.error_class == "qga-error" and "Guest agent command failed" in str(ei.value)


def test_guest_write_file_uses_guest_file_open_write_close_with_base64():
    rig = Rig()
    g = _running(rig)
    ga = LibvirtGuestExec(rig.conn, HOST, sleep=lambda _s: None)
    content = "#!/bin/sh\necho ünïcode\n" * 5000   # several chunks
    ga.write_file(g, "/usr/local/bin/x.sh", content, mode="0755")
    opened = rig.conn.agent.of("guest-file-open")
    assert opened == [{"execute": "guest-file-open", "arguments": {"path": "/usr/local/bin/x.sh", "mode": "w"}}]
    writes = rig.conn.agent.of("guest-file-write")
    assert len(writes) > 1 and all(w["arguments"]["handle"] == 7 for w in writes)
    assert b"".join(base64.b64decode(w["arguments"]["buf-b64"]) for w in writes) == content.encode()
    assert rig.conn.agent.of("guest-file-close") == [{"execute": "guest-file-close", "arguments": {"handle": 7}}]
    assert rig.conn.agent.of("guest-exec")[-1]["arguments"] == {"path": "chmod", "arg": ["0755", "/usr/local/bin/x.sh"], "capture-output": True}
    # a short delivery is refused, never reported as written
    rig.conn.agent.handlers["guest-file-write"] = {"return": {"count": 1, "eof": False}}
    with pytest.raises(AdapterError) as ei:
        ga.write_file(g, "/tmp/y", "hello")
    assert ei.value.error_class == "qga-write"


# ---- routers ----------------------------------------------------------------


def test_configure_router_debian_runs_the_shared_nat_script_over_the_agent():
    from axrange.adapter_proxmox.rest import ROUTER_NAT_SCRIPT

    rig = Rig()
    g = _running(rig)
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {
        "exited": True, "exitcode": 0, "out-data": base64.b64encode(b"AXR_ROUTER_OK wan=eth0 forward=1").decode()}}
    assert rig.run("configure-router", {"host": "gw"}, {"vmid": g, "node_id": "gw"}) == {"configured": True}
    ex = rig.conn.agent.of("guest-exec")[-1]["arguments"]
    assert ex["path"] == "/bin/bash" and ex["arg"] == ["-c", ROUTER_NAT_SCRIPT]
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {"exited": True, "exitcode": 0,
                                                               "out-data": base64.b64encode(b"AXR_ROUTER_FAIL no-WAN").decode()}}
    with pytest.raises(AdapterError) as ei:
        rig.run("configure-router", {"host": "gw"}, {"vmid": g})
    assert ei.value.error_class == "router-config-failed"


def test_configure_router_applies_the_egress_phase_through_write_file():
    rig = Rig()
    g = _running(rig)
    phase_ok = base64.b64encode(b"AXR_ROUTER_OK wan=eth0\nAXR_EGRESS_OK phase=measure\n").decode()
    rig.conn.agent.handlers["guest-exec-status"] = {"return": {"exited": True, "exitcode": 0, "out-data": phase_ok}}
    out = rig.run("configure-router", {"host": "gw"}, {"vmid": g, "egress": {"phase": "measure", "sink_ip": "10.66.30.10"}})
    assert out == {"configured": True, "egress_phase": "measure"}
    assert rig.conn.agent.of("guest-file-open")   # the installer rode file-in
    assert rig.conn.agent.of("guest-exec")[-1]["arguments"]["path"] == "/bin/sh"


def test_configure_routing_without_wan_ip_fails_loud():
    rig = Rig()
    with pytest.raises(AdapterError) as ei:
        rig.run("configure-routing", {"host": "gw"}, {"vmid": "x", "node_id": "gw", "fabric": {}})
    assert ei.value.error_class == "router-config-failed"
    assert rig.run("configure-routing", {"host": "gw"}, {"vmid": "x", "node_id": "gw", "wan_ip": "10.0.0.5", "fabric": {}}) == \
        {"routing_configured": True, "lines": 0}


# ---- the capture plane (host backend) --------------------------------------


def test_attach_gre_tunnel_runs_the_same_commands_as_proxmox():
    from axrange.adapter_proxmox.ssh import gretap_add_cmd, gretap_offload_cmd, gretap_qdisc_cmd, gretap_up_cmd

    rig = Rig()
    out = rig.run("attach-gre-tunnel", {"net": "corp", "sensor": "s1"},
                  {"bridge_name": _vnet(), "sensor_id": "s1", "sensor_transport_ip": "10.255.250.20"})
    dev = naming.gre_port_for_bridge_sensor(_vnet(), "s1")
    key = naming.gre_key_for_bridge_sensor(_vnet(), "s1")
    assert out == {"gre_port": dev} and naming.is_tap_gretap(dev)
    cmds = rig.runner.cmds
    assert cmds == [
        f"ip -o link show {dev} 2>/dev/null || true",
        gretap_add_cmd(dev, "10.255.250.20", key),
        gretap_up_cmd(dev),
        gretap_qdisc_cmd(dev, 20000),
        gretap_offload_cmd(dev),
    ]
    assert f"remote 10.255.250.20 key {key} nopmtudisc ignore-df" in cmds[1]
    assert "pfifo limit 20000" in cmds[3] and "mtu 1500 up" in cmds[2]
    for argv, _ in rig.runner.calls:
        assert argv[:2] == ["sh", "-c"]   # local URI: a local subprocess, no ssh


def test_attach_gre_tunnel_adopts_an_existing_netdev_and_honours_qlen_zero():
    from axrange.adapter_proxmox.ssh import gretap_heal_cmd

    rig = Rig(mirror_gretap_txqueuelen=0)
    dev = naming.gre_port_for_bridge_sensor(_vnet(), "default")
    rig.runner.respond(f"link show {dev}", 0, f"12: {dev}@NONE: <BROADCAST,MULTICAST> mtu 1500\n")
    rig.run("attach-gre-tunnel", {"net": "corp"}, {"bridge_name": _vnet()})
    cmds = rig.runner.cmds
    assert not any("ip link add" in c for c in cmds)
    assert gretap_heal_cmd(dev) in cmds and not any("pfifo" in c for c in cmds)


def test_install_ovs_mirror_writes_a_per_domain_hook_and_applies_in_band():
    rig = Rig()
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"})
    g = rig.clone()
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 0})
    tap = naming.tap_name_for_nic(g, 0)
    gre = naming.gre_port_for_bridge_sensor(_vnet(), "s1")
    inputs = {"vmid": g, "node_id": "h-a", "bridge_name": _vnet(), "gre_port": gre,
              "tapped_nics": [{"nic_index": 0, "gre_port_input": "gre_port", "direction": "both"}]}
    # stopped domain: hook only
    assert rig.run("install-ovs-mirror", {"host": "h-a"}, inputs) == {"hook_installed": True}
    path = naming.hook_script_path(g)
    assert path == f"/etc/libvirt/hooks/qemu.d/axrange-mirror-{g}"
    script = _put_files(rig.runner)[path]
    assert script.startswith("#!/bin/bash") and f"mirror dev {gre}" in script
    assert not rig.runner.having("tc qdisc add")
    # running domain: the hook plus the in-band tc mirror on the deterministic tap
    rig.conn.domains[g].active = True
    out = rig.run("install-ovs-mirror", {"host": "h-a"}, inputs)
    assert out == {"hook_installed": True}
    tc = rig.runner.having("tc qdisc add")
    assert tc and f"dev {tap} clsact" in tc[0] and f"ingress matchall action mirred egress mirror dev {gre}" in tc[0]
    assert f"egress matchall action mirred egress mirror dev {gre}" in tc[0]
    assert rig.runner.having(f"ethtool -K {tap} gso off")


def test_install_ovs_mirror_hook_content_and_revert(tmp_path, monkeypatch):
    from axrange.adapter_libvirt import naming as nm

    monkeypatch.setattr(nm, "HOOK_DIR", str(tmp_path / "qemu.d"))
    monkeypatch.setattr(nm, "hook_script_path", lambda guest: f"{tmp_path}/qemu.d/axrange-mirror-{guest}")
    rig = Rig()
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"})
    g = rig.clone()
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 0})
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 1})
    rig.conn.domains[g].active = True
    gre = "tapg_abcdef"
    inputs = {"vmid": g, "gre_port": gre, "tapped_nics": [
        {"nic_index": 0, "gre_port_input": "gre_port", "direction": "rx"},
        {"nic_index": 1, "gre_port_input": "gre_port", "direction": "both"},
    ]}
    rig.run("install-ovs-mirror", {"host": "h-a"}, inputs)
    hook = Path(nm.hook_script_path(g))
    script = _put_files(rig.runner)[str(hook)]
    name = naming.vm_name_for_host(PLAN, "h-a")
    assert rig.runner.having(f"chmod 0755 {hook}")
    assert f'[ "$1" = "{name}" ] || exit 0' in script and "started)" in script and "stopped|release)" in script
    t0, t1 = naming.tap_name_for_nic(g, 0), naming.tap_name_for_nic(g, 1)
    assert f"tc qdisc add dev {t0} clsact" in script and f"tc qdisc add dev {t1} clsact" in script
    assert f"tc filter add dev {t0} ingress matchall action mirred egress mirror dev {gre}" in script
    assert f"tc filter add dev {t0} egress" not in script       # rx-only NIC: no egress filter
    assert f"tc filter add dev {t1} egress matchall" in script   # both: egress too
    assert f"tc qdisc del dev {t0} clsact" in script
    # narrowing to NIC 1 only clears NIC 0's live mirror and says so
    out = rig.run("install-ovs-mirror", {"host": "h-a"}, {**inputs, "tapped_nics": inputs["tapped_nics"][1:]})
    assert out == {"hook_installed": True, "cleared_ifaces": [t0]}
    assert rig.runner.having(f"tc qdisc del dev {t0} clsact")
    # an empty tapped set is a revert: a no-op hook and every tap cleared
    out = rig.run("install-ovs-mirror", {"host": "h-a"}, {**inputs, "tapped_nics": [], "gre_port": None})
    assert out == {"hook_installed": True, "mirror_cleared": True}
    assert "tc qdisc add" not in _put_files(rig.runner)[str(hook)]
    assert rig.runner.having(f"tc qdisc del dev {t1} clsact")


def test_host_backend_local_put_file_and_remote_argv(tmp_path):
    local = HostBackend(_cfg())   # the default subprocess runner: a real `sh -c`
    local.put_file("hello\n", str(tmp_path / "d" / "f.txt"), mode="0640")
    assert (tmp_path / "d" / "f.txt").read_text() == "hello\n"
    assert (tmp_path / "d" / "f.txt").stat().st_mode & 0o777 == 0o640
    assert local.run("printf %s ok") == "ok"
    runner = RecordingRunner()
    remote = HostBackend(_cfg(libvirt_uri="qemu+ssh://ops@kvm2/system", host_ssh_host="kvm2",
                              host_ssh_user="ops", host_ssh_key="/keys/kvm2"), runner=runner)
    remote.put_file("x", "/etc/libvirt/hooks/qemu.d/h", mode="0755")
    argv, inp = runner.calls[0]
    assert argv[0] == "ssh" and "ops@kvm2" in argv and argv[argv.index("-i") + 1] == "/keys/kvm2"
    assert argv[-1] == "mkdir -p /etc/libvirt/hooks/qemu.d && cat > /etc/libvirt/hooks/qemu.d/h && chmod 0755 /etc/libvirt/hooks/qemu.d/h"
    assert inp == "x"
    runner.respond("false", 1, "", "nope")
    with pytest.raises(AdapterError) as ei:
        remote.run("false")
    assert ei.value.error_class == "host-cmd-failed" and "ops@kvm2" in str(ei.value)


# ===========================================================================
# inventory
# ===========================================================================


def test_inventory_lists_tier_tagged_domains_with_uuid_handles():
    rig = Rig()
    inv = LibvirtInventory(rig.cfg, client=rig.conn)
    te = rig.conn.add_domain(naming.vm_name_for_host(PLAN, "h-a"), naming.guest_handle_for_op(PLAN, "o1"), active=True)
    rig.conn.add_domain("o2-sensor", str(_uuid.uuid4()))
    rig.conn.add_domain("img-debian-12-cloud", str(_uuid.uuid4()))
    rig.conn.add_domain("my-desktop-vm", str(_uuid.uuid4()), active=True)
    rows = inv.live_guests()
    assert {r["name"] for r in rows} == {te.name(), "o2-sensor", "img-debian-12-cloud"}
    row = next(r for r in rows if r["name"] == te.name())
    assert row == {"name": te.name(), "vmid": te.UUIDString(), "node": HOST, "status": "running", "type": "qemu"}
    assert inv.is_configured() and inv.platform_version() == rig.conn.facts
    assert inv.guest_host(te.UUIDString()) == HOST and inv.guest_host(str(_uuid.uuid4())) is None


def test_inventory_guest_address_prefers_the_agent_then_the_lease_and_never_raises():
    rig = Rig()
    inv = LibvirtInventory(rig.cfg, client=rig.conn)
    g = naming.guest_handle_for_op(PLAN, "o1")
    d = rig.conn.add_domain("te-x", g, active=True)
    assert inv.guest_address(g) is None                      # agent and lease both fail
    d.addresses[0] = {"vnet3": {"hwaddr": "02:x", "addrs": [{"addr": "192.168.122.5", "prefix": 24, "type": 0}]}}
    assert inv.guest_address(g) == "192.168.122.5"           # lease fallback
    d.addresses[1] = {"lo": {"addrs": [{"addr": "127.0.0.1", "prefix": 8, "type": 0}]},
                      "eth0": {"addrs": [{"addr": "fe80::1", "prefix": 64, "type": 1},
                                         {"addr": "10.66.20.11", "prefix": 24, "type": 0}]}}
    assert inv.guest_address(g) == "10.66.20.11"             # the agent wins
    assert inv.guest_address(str(_uuid.uuid4())) is None


def test_inventory_raising_reads_and_pings():
    rig = Rig()
    inv = LibvirtInventory(rig.cfg, client=rig.conn)
    g = naming.guest_handle_for_op(PLAN, "o1")
    rig.conn.add_domain("te-x", g, active=True)
    inv.ping_guest(g, timeout_s=2)
    assert rig.conn.agent.of("guest-ping")
    with pytest.raises(AdapterError):
        inv.ping_guest(str(_uuid.uuid4()))
    unconfigured = LibvirtInventory(LibvirtConfig(), client=rig.conn)
    assert not unconfigured.is_configured()
    with pytest.raises(AdapterError):
        unconfigured.platform_version()
    with pytest.raises(AdapterError):
        unconfigured.live_guests()


def test_inventory_external_sensor_facts(tmp_path):
    cfg = load_config(_write(tmp_path, _MIN_CONF + 'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,"transport_ip":"10.255.250.20"}]\n'))
    conn = FakeConn()
    d = conn.add_domain("XPress-SW", str(_uuid.uuid4()), active=True)
    d.addresses[1] = {"ens3": {"addrs": [{"addr": "10.0.0.50", "prefix": 24, "type": 0}]}}
    facts = LibvirtInventory(cfg, client=conn).external_sensor_facts()
    assert facts[0]["resolved_name"] == "XPress-SW" and facts[0]["resolved_ip"] == "10.0.0.50"
    assert facts[0]["access_hint"] == "ssh axrange@10.0.0.50" and facts[0]["vmid"] == 250
    assert LibvirtInventory(cfg, client=conn).external_sensor_facts(only_ids={"other"}) == []


# ===========================================================================
# teardown
# ===========================================================================


def _deployed_rig() -> tuple[Rig, str, LibvirtTeardown]:
    rig = Rig()
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp"})
    g = rig.clone()
    rig.run("attach-vm-to-vnet", {"host": "h-a"}, {"vmid": g, "vnet_id": _vnet(), "nic_index": 0})
    rig.run("configure-cloud-init", {"host": "h-a"}, {"vmid": g, "ip_config": {}})
    rig.pool.add(naming.seed_iso_name(g), 1 << 20)   # the ISO the host built shows up after refresh
    rig.run("start-vm", {"host": "h-a"}, {"vmid": g})
    td = LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host)
    return rig, g, td


def test_teardown_destroy_vm_removes_domain_overlay_and_seed_but_never_the_template():
    rig, g, td = _deployed_rig()
    dom = rig.conn.domains[g]
    assert td.vm_name(HOST, g) == naming.vm_name_for_host(PLAN, "h-a")
    assert td.destroy_vm(HOST, g) == "removed"
    assert dom.destroyed == 1 and dom.undefined == [1 | 2 | 4]
    assert set(rig.pool.vols) == {"img-debian-12-cloud.qcow2"}
    assert td.delete_mirror_snippets(HOST, g) == "removed"
    assert rig.runner.having(f"rm -f {naming.hook_script_path(g)}")
    assert g not in rig.conn.domains            # undefined == gone from the host
    assert td.destroy_vm(HOST, g) == "absent" and td.vm_name(HOST, g) is None


@pytest.mark.parametrize("name,why", [
    ("img-debian-12-cloud", "img tier"),
    ("o2-sensor-01", "o2 tier"),
    ("tfc-abcdef-persona", "tfc tier"),
    ("exr-abcdef-red", "exr tier"),
    ("XPress-SW", "protected external"),
    ("operator-sink", "protected external"),
])
def test_teardown_guard_refuses_img_o2_foreign_and_protected_vms(name, why):
    rig = Rig(protected_external_names=["operator-sink"])
    g = naming.guest_handle_for_op(PLAN, "o1")
    dom = rig.conn.add_domain(name, g, active=True)
    td = LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host)
    with pytest.raises(ProtectedResource) as ei:
        td.destroy_vm(HOST, g)
    assert ei.value.error_class == "teardown-protected" and why in str(ei.value) and name in str(ei.value)
    assert dom.destroyed == 0 and dom.undefined == [] and dom.isActive()


def test_teardown_guard_refuses_a_hand_made_uuid_even_with_our_name():
    rig = Rig()
    g = str(_uuid.uuid4())
    dom = rig.conn.add_domain(naming.vm_name_for_host(PLAN, "h-a"), g)
    with pytest.raises(ProtectedResource, match="version-5"):
        LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host).destroy_vm(HOST, g)
    assert dom.undefined == []


def test_teardown_tier_decides_which_shapes_may_be_swept():
    rig = Rig()
    te_net = rig.conn.define_network(f"<network><name>{_vnet()}</name></network>")
    o2_net = rig.conn.define_network(f"<network><name>{naming.vnet_id_for_net('mon', plan_id='o2-plan', tier='o2')}</name></network>")
    te_td = LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host)
    o2_td = LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host, tier="o2")
    with pytest.raises(ProtectedResource, match="o2 tier"):
        te_td.delete_vnet(o2_net.name())
    with pytest.raises(ProtectedResource, match="te tier"):
        o2_td.delete_vnet(te_net.name())
    assert te_td.delete_vnet(te_net.name()) == "removed" and te_net.undefined == 1
    assert o2_td.delete_vnet(o2_net.name()) == "removed"
    assert te_td.delete_vnet(_vnet("gone")) == "absent"
    with pytest.raises(ProtectedResource, match="img"):
        te_td.delete_sdn_zone("img-x")
    assert te_td.delete_sdn_zone(naming.zone_id_for_plan(PLAN)) == "absent" and te_td.apply_sdn() == "applied"
    with pytest.raises(ValueError):
        LibvirtTeardown(rig.cfg, client=rig.conn, tier="bogus")


def test_teardown_gretap_bridge_and_portgroup_paths():
    rig = Rig(network_backend="ovs")
    td = LibvirtTeardown(rig.cfg, client=rig.conn, host=rig.host)
    dev = naming.gre_port_for_bridge_sensor(_vnet(), "s1")
    assert td.delete_gretap(dev) == "absent"
    rig.runner.respond(f"link show {dev}", 0, f"9: {dev}@NONE: <> mtu 1500\n")
    assert td.delete_gretap(dev) == "removed" and rig.runner.having(f"ip link del {dev}")
    with pytest.raises(ProtectedResource):
        td.delete_gretap("gre0")
    # VLAN zone: the segment is a portgroup; the underlay is the bridge
    rig.run("create-ovs-bridge", {"scenario": "s"}, {"zone_type": "vlan"})
    rig.run("create-vnet", {"net": "corp"}, {"net_id": "corp", "vlan": 10})
    underlay = naming.underlay_bridge_for_plan(PLAN)
    net = rig.conn.networks[underlay]
    assert td.delete_vnet(_vnet()) == "removed"
    assert net.updates[-1][0] == VIR_NETWORK_UPDATE_COMMAND_DELETE and _vnet() not in net.xml
    assert td.delete_vnet(_vnet()) == "absent"
    assert td.delete_ovs_bridge(HOST, underlay) == "removed"
    assert net.undefined == 1 and rig.runner.having(f"ovs-vsctl --if-exists del-br {underlay}")
    assert td.delete_ovs_bridge(HOST, underlay) == "absent"
    with pytest.raises(ProtectedResource):
        td.delete_ovs_bridge(HOST, "o2br_abcdef")


# ===========================================================================
# the connection wrapper (no libvirt installed: the raw layer is injected)
# ===========================================================================


class _RawErr(Exception):
    pass


class _Raw:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.agent_calls: list[tuple[str, int, int]] = []

    def lookupByUUIDString(self, u):
        raise _RawErr("Domain not found")

    def lookupByName(self, n):
        return SimpleDomain(n)

    def listAllDomains(self, flags):
        return [SimpleDomain("a")]

    def storagePoolLookupByName(self, n):
        if n != "default":
            raise _RawErr("pool not found")
        return self

    def storageVolLookupByName(self, n):
        raise _RawErr("vol not found")

    def XMLDesc(self, flags):
        return "<pool><target><path>/var/lib/libvirt/images</path></target></pool>"

    def getHostname(self):
        return "kvm1"

    def getVersion(self):
        return 8002000

    def getLibVersion(self):
        return 10000000

    def getInfo(self):
        return ["x86_64", 65536, 16]


class SimpleDomain:
    def __init__(self, n: str) -> None:
        self.n = n

    def name(self) -> str:
        return self.n


def test_connection_wrapper_opens_lazily_and_maps_not_found_to_none():
    raw = _Raw()
    opened: list[str] = []

    def opener(uri: str) -> _Raw:
        opened.append(uri)
        return raw

    conn = LibvirtConnection(_cfg(), opener=opener, error_type=_RawErr,
                             agent_command=lambda dom, payload, timeout, flags: (raw.agent_calls.append((payload, timeout, flags)), '{"return": {"pid": 5}}')[1])
    assert isinstance(conn, Connection)
    assert opened == []                              # nothing opened at construction
    assert conn.domain_by_uuid("x") is None and opened == ["qemu:///system"]
    assert conn.domain_by_name("web").name() == "web"
    assert conn.pool_by_name("nope") is None and conn.volume_by_name("default", "v") is None
    assert conn.pool_path("default") == "/var/lib/libvirt/images"
    with pytest.raises(AdapterError):
        conn.pool_path("nope")
    assert conn.host_facts()["hostname"] == "kvm1" and conn.host_facts()["cpus"] == 16
    reply = conn.agent_command(SimpleDomain("d"), {"execute": "guest-exec", "arguments": {"path": "true"}}, 30)
    assert reply == {"return": {"pid": 5}}
    payload, timeout, flags = raw.agent_calls[0]
    assert json.loads(payload) == {"execute": "guest-exec", "arguments": {"path": "true"}} and (timeout, flags) == (30, 0)
    assert conn.agent_command(SimpleDomain("d"), {"execute": "guest-ping"}, None) == {"return": {"pid": 5}}
    assert raw.agent_calls[1][1] == -2               # libvirt's "default timeout" sentinel
    assert len(opened) == 1                          # one open per connection


def test_connection_without_a_uri_or_bindings_fails_by_name():
    with pytest.raises(AdapterError, match="LIBVIRT_URI"):
        LibvirtConnection(LibvirtConfig()).raw
    # the real opener needs the python bindings; this box has none, and the
    # failure names the package rather than a bare ImportError.
    with pytest.raises(AdapterError, match="libvirt bindings"):
        LibvirtConnection(_cfg()).raw


# ===========================================================================
# bindings
# ===========================================================================


def test_bindings_resolve_the_fixture_documents():
    b = LibvirtBindings(LV_BINDINGS)
    deb = b.resolve("debian-12-cloud")
    assert (deb.template_volume, deb.clone_mode, deb.machine, deb.firmware, deb.cloud_init_user) == \
        ("img-debian-12-cloud.qcow2", "linked", "pc", "seabios", "debian")
    assert deb.pool is None and deb.vcpu == 2 and deb.memory == "2Gi" and deb.domain_xml_template is None
    win = b.resolve("windows-server-2022", "v1")
    assert win.pool == "fast" and win.clone_mode == "full" and "<hyperv>" in win.domain_xml_template
    with pytest.raises(AdapterError, match="no version 'v9'"):
        b.resolve("windows-server-2022", "v9")
    with pytest.raises(AdapterError, match="LIBVIRT_BINDINGS_DIR unset"):
        LibvirtBindings(None).resolve("debian-12-cloud")


def test_the_manifest_loader_walks_every_platform_directory(fixtures_dir):
    from axrange.manifest.loader import load_registry

    reg = load_registry(root=fixtures_dir)
    assert reg.get_binding("debian-12-cloud", "proxmox") is not None
    assert reg.get_binding("debian-12-cloud", "libvirt") is not None
    assert reg.get_binding("debian-12-cloud", "libvirt").versions[0].storage.template_vmid is None


# ===========================================================================
# end to end: a two-host plan through the real planner + executor
# ===========================================================================

_TWO_HOSTS = (
    "scenario_schema_version: 1\n"
    "scenario: { id: lv-slice, version: 1 }\n"
    "layers:\n"
    "  infra:\n"
    "    networks:\n"
    "      - { id: corp, cidr: 10.66.20.0/24 }\n"
    "    nodes:\n"
    "      - { id: h-a, kind: host, nets: [{ net: corp, ip: 10.66.20.11 }], image: debian-12-cloud@v1, requires: [linux] }\n"
    "      - { id: h-b, kind: host, nets: [{ net: corp, ip: 10.66.20.12 }], image: debian-12-cloud@v1, requires: [linux] }\n"
    "  apps: []\n"
    "  activity: []\n"
    "bindings: []\n"
)


def test_two_host_plan_deploys_end_to_end_through_the_executor(tmp_path, manifests):
    """`adapters/libvirt/SPEC.md` Acceptance, automated half of the first
    slice: the planner's DAG (which does not know the platform) runs every
    infra op against the libvirt adapter; afterwards the fake host holds one
    isolated segment network and two running domains attached to it, each
    with its overlay, seed ISO and agent channel."""
    from axrange.ir.parser import parse_ir
    from axrange.planner import Executor, StateStore, build_plan

    parsed = parse_ir(text=_TWO_HOSTS)
    cluster = ClusterContext.single_node(HOST)
    plan = build_plan(parsed.ir, manifests, cluster, raw_ir=parsed.raw)
    assert {o.op_name for o in plan.ops.values()} >= {
        "create-sdn-zone", "create-ovs-bridge", "create-vnet", "apply-sdn-config", "clone-vm",
        "configure-vm-hardware", "configure-cloud-init", "attach-vm-to-vnet", "start-vm",
        "wait-guest-agent", "wait-os-ready",
    }
    rig = Rig()

    class _ConfigStub:
        """The config-class ops (`install-defense-agent`, ...) go to the
        config-execution backend, not the platform adapter; a stub here keeps
        the test on the infra ops the SPEC table maps."""

        def run(self, op, resolved_inputs, ctx):
            assert op.op_name not in _OP_TO_METHOD, op.op_name
            return {}

    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id="lv-dep-1", scenario_id=plan.scenario_id, scenario_version=plan.scenario_version,
        scenario_schema_version=1, plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement, tier="te",
    )
    ex = Executor(plan, store, rig.adapter, cluster, config_backend=_ConfigStub(),
                  deploy_id="lv-dep-1", sleep_fn=lambda _: None)
    ex.persist_plan()
    try:
        result = ex.run()
    finally:
        store.close()
    assert result.succeeded, {k: (v.final_status, v.error_message) for k, v in result.per_op.items()}

    vnet = naming.vnet_id_for_net("corp", plan_id=plan.plan_id)
    assert set(rig.conn.networks) == {vnet} and rig.conn.networks[vnet].active
    doms = {d.name(): d for d in rig.conn.domains.values()}
    assert set(doms) == {naming.vm_name_for_host(plan.plan_id, "h-a"), naming.vm_name_for_host(plan.plan_id, "h-b")}
    clone_ops = [o for o in plan.ops.values() if o.op_name == "clone-vm"]
    for op in clone_ops:
        g = naming.guest_handle_for_op(plan.plan_id, op.op_id)
        d = rig.conn.domains[g]
        assert d.isActive() and result.per_op[op.op_id].outputs == {"vmid": g}
        nics = interfaces(d.xml)
        assert [n["network"] for n in nics] == [vnet] and nics[0]["tap"] == naming.tap_name_for_nic(g, 0)
        assert disk_sources(d.xml) == [f"{POOL_PATH}/{naming.overlay_volume_name(g)}", f"{POOL_PATH}/{naming.seed_iso_name(g)}"]
        assert ET.fromstring(d.xml).find("./devices/channel/target").get("name") == "org.qemu.guest_agent.0"
    assert len([v for v in rig.pool.vols if v.endswith("-disk0.qcow2")]) == 2
    assert rig.conn.agent.of("guest-ping") and rig.conn.agent.of("guest-exec")
    assert "10.66.20.11/24" in "".join(i or "" for _, i in rig.runner.calls)
