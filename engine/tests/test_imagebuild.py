"""Tests for the image build pipeline (axrange/imagebuild/, docs/IMAGE_BUILD.md).

Real REST + SSH calls are mocked at the transport level — the pipeline is
verified end-to-end on the live cluster (docs/IMAGE_BUILD.md §8 done-criteria),
exactly as the adapter slice is. These tests cover the orchestration, the
recipe/config/cache/bake/binding logic, and that the generated smoke scenario
is a valid IR that plans without mirror ops.
"""
from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.imagebuild import BuildOptions, ImageBuildError, build_image
from axrange.imagebuild.bake import (
    default_probe_for,
    probe_command,
    render_bake_userdata,
    seal_commands,
)
from axrange.imagebuild.cache import fetch_base_image
from axrange.imagebuild.config import BuildConfig, load_build_config
from axrange.imagebuild.conformance import (
    build_smoke_scenario_yaml,
    run_probes,
    SmokeReport,
)
from axrange.imagebuild.proxmox_build import (
    BuildProxmox,
    GuestExecResult,
    _parse_imported_volid,
)
from axrange.imagebuild.recipe import BaseImage, recipe_from_binding_version
from axrange.imagebuild import binding_io
from axrange.ir.capabilities import ALL_CAPABILITIES
from axrange.manifest.loader import load_registry
from axrange.planner.executor import AdapterTimeout


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeBuildProxmox:
    """Records the build-VM lifecycle calls and returns canned guest-exec
    results. Drives builder.build_image without a cluster."""

    def __init__(self, *, next_vmid=4242, exec_exit_map=None, preexisting=None):
        self._next = next_vmid
        # substring-in-command -> exit code; default 0.
        self.exec_exit_map = exec_exit_map or {}
        self._preexisting = preexisting  # a VMID that "already exists" pre-build
        self.calls: list[tuple] = []

    def _record(self, name, *args):
        self.calls.append((name, *args))

    def next_vmid(self):
        self._record("next_vmid")
        return self._next

    def vm_exists(self, vmid):
        if vmid == self._preexisting:
            return True
        created = any(c[0] == "create_vm" and c[1] == vmid for c in self.calls)
        destroyed = any(c[0] == "destroy_vm" and c[1] == vmid for c in self.calls)
        return created and not destroyed

    def create_vm(self, vmid, name, **kw):
        self._record("create_vm", vmid, name, kw)

    def upload_image(self, local_path, **kw):
        self._record("upload_image", local_path)
        return f"/var/lib/vz/template/cache/{Path(local_path).name}"

    def import_disk(self, vmid, remote, storage, *, scsihw):
        self._record("import_disk", vmid, remote, storage, scsihw)
        return f"{storage}:vm-{vmid}-disk-0"

    def add_cloudinit_drive(self, vmid, storage):
        self._record("add_cloudinit_drive", vmid, storage)

    def resize_disk(self, vmid, disk, size):
        self._record("resize_disk", vmid, disk, size)

    def set_cloudinit(self, vmid, name, content, **kw):
        self._record("set_cloudinit", vmid, name)

    def start_vm(self, vmid):
        self._record("start_vm", vmid)

    def wait_agent(self, vmid, **kw):
        self._record("wait_agent", vmid)

    def wait_cloud_init(self, vmid, **kw):
        self._record("wait_cloud_init", vmid)

    def guest_exec(self, vmid, argv, **kw):
        self._record("guest_exec", vmid, tuple(argv))
        cmd = " ".join(argv)
        for needle, code in self.exec_exit_map.items():
            if needle in cmd:
                return GuestExecResult(code, "", f"fake fail: {needle}")
        return GuestExecResult(0, "", "")

    def shutdown_vm(self, vmid, **kw):
        self._record("shutdown_vm", vmid)

    def clear_build_cloudinit(self, vmid, snippet_name=None):
        self._record("clear_build_cloudinit", vmid, snippet_name)

    def templatize(self, vmid):
        self._record("templatize", vmid)

    def destroy_vm(self, vmid, **kw):
        self._record("destroy_vm", vmid)

    def delete_sdn(self, zone, vnet):
        self._record("delete_sdn", zone, vnet)

    def delete_bridge(self, bridge):
        self._record("delete_bridge", bridge)


def _ok_smoke(*a, **k):
    return SmokeReport(ok=True, deploy_id="smoke-test", ops_ok=15, ops_total=15)


def _fail_smoke(*a, **k):
    return SmokeReport(
        ok=False, deploy_id="smoke-test", ops_ok=3, ops_total=15, detail="failed"
    )


def _build_cfg(node="pxnode", cache_dir="/tmp/axr-cache", pubkey=""):
    deploy = AdapterConfig(
        default_node=node,
        proxmox_api_token="deploytoken",
        ovs_ssh_host="10.0.0.85",
        ovs_ssh_user="ovs-operator",
        ovs_ssh_key="",
        guest_ssh_pubkey_file=pubkey,
    )
    return BuildConfig(
        deploy=deploy,
        api_token="deploytoken",
        ssh_user="root",
        ssh_key="",
        ssh_host="10.0.0.85",
        node=node,
        storage="local-lvm",
        bridge="vmbr0",
        snippet_storage="local",
        cache_dir=cache_dir,
    )


def _images_root(fixtures_dir: Path, tmp_path: Path) -> Path:
    root = tmp_path / "root"
    shutil.copytree(fixtures_dir / "images", root / "images")
    return root


def _fake_fetch(*args, **kwargs):
    return Path("/nonexistent/debian-12-genericcloud-amd64.qcow2")


# ---------------------------------------------------------------------------
# Recipe parsing
# ---------------------------------------------------------------------------


def test_recipe_parses_from_debian_binding(manifests):
    binding = manifests.get_binding("debian-12-cloud", "proxmox")
    bv = binding.get_version("v1")
    recipe = recipe_from_binding_version(bv)
    assert recipe.base_image.url.endswith("debian-12-genericcloud-amd64.qcow2")
    assert recipe.base_image.checksum_algo == "sha512"
    assert recipe.base_image.filename == "debian-12-genericcloud-amd64.qcow2"
    assert recipe.disk_size == "8G"
    # ansible is baked so config-class clones can run playbooks (SLICE_CONFIG §2).
    assert recipe.provision == [{"pkg": "ansible"}]


def test_recipe_missing_build_section_raises(manifests):
    # ubuntu-server-22 binding has no `build:` section.
    binding = manifests.get_binding("ubuntu-server-22", "proxmox")
    bv = binding.get_version(binding.versions[0].id)
    with pytest.raises(ImageBuildError) as exc:
        recipe_from_binding_version(bv)
    assert "build" in str(exc.value).lower()


def test_recipe_rejects_missing_checksum(manifests):
    from axrange.manifest.schema import BindingVersion

    bv = BindingVersion.model_validate(
        {"id": "v1", "build": {"base_image": {"url": "http://x/y.qcow2"}}}
    )
    with pytest.raises(ImageBuildError) as exc:
        recipe_from_binding_version(bv)
    assert "checksum" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def test_default_probes_cover_linux_capability_subset():
    # Every closed capability keyword that can appear in a Linux `provides:`
    # has a default probe (the OS-family `linux` + host:* + kernel:*).
    for cap in ALL_CAPABILITIES:
        if cap in ("windows", "bsd"):
            continue
        assert default_probe_for(cap) is not None, f"no default probe for {cap}"


def test_probe_command_override_wins():
    assert probe_command("linux", {}) == "uname -s | grep -qi linux"
    assert probe_command("linux", {"linux": "true"}) == "true"


def test_run_probes_all_pass():
    bp = FakeBuildProxmox()
    report = run_probes(bp, 100, ["linux", "kernel:ebpf", "kernel:audit"])
    assert report.ok
    assert len(report.results) == 3


def test_run_probes_reports_failure():
    bp = FakeBuildProxmox(exec_exit_map={"/sys/fs/bpf": 1})
    report = run_probes(bp, 100, ["linux", "kernel:ebpf"])
    assert not report.ok
    assert [r.capability for r in report.failures] == ["kernel:ebpf"]


def test_run_probes_missing_probe_is_failure():
    bp = FakeBuildProxmox()
    # A capability with no default probe and no override → conformance gap.
    report = run_probes(bp, 100, ["totally-unknown-cap"])
    assert not report.ok
    assert report.failures[0].command is None


# ---------------------------------------------------------------------------
# Bake render + seal
# ---------------------------------------------------------------------------


def test_bake_userdata_installs_and_autostarts_qga():
    ud = render_bake_userdata(user="debian")
    assert ud.startswith("#cloud-config")
    assert "qemu-guest-agent" in ud
    # Auto-start a static unit via add-wants (the load-bearing line).
    assert "add-wants" in ud and "multi-user.target" in ud


def test_bake_userdata_includes_provision_packages():
    ud = render_bake_userdata(provision=[{"pkg": ["auditd", "curl"]}])
    assert "auditd" in ud and "curl" in ud


def test_bake_userdata_pinned_pkg_uses_runcmd():
    ud = render_bake_userdata(provision=[{"pkg": "nginx", "version": "1.22"}])
    assert "nginx=1.22" in ud


def test_bake_unsupported_provision_kind_raises():
    with pytest.raises(ImageBuildError):
        render_bake_userdata(provision=[{"role": "adds"}])


def test_seal_commands_reset_identity():
    cmds = seal_commands()
    flat = " ".join(" ".join(c) for c in cmds)
    assert "cloud-init" in flat and "clean" in flat
    assert "machine-id" in flat
    assert "ssh_host_" in flat


def test_bake_userdata_is_valid_cloud_config_yaml():
    """The renderer hand-builds YAML; confirm it round-trips through a parser
    (a #cloud-config with broken indentation silently fails at boot)."""
    from ruamel.yaml import YAML

    y = YAML(typ="safe")
    ud = render_bake_userdata(
        user="debian",
        ssh_pubkey="ssh-ed25519 AAAA op@host",
        provision=[
            {"pkg": ["auditd", "curl"]},
            {"pkg": "nginx", "version": "1.22"},
            {"script": {"inline": "#!/bin/sh\necho hi\nid\n"}},
        ],
    )
    doc = y.load(io.StringIO(ud))
    assert "qemu-guest-agent" in doc["packages"]
    assert "auditd" in doc["packages"] and "curl" in doc["packages"]
    assert any("nginx=1.22" in " ".join(map(str, r)) for r in doc["runcmd"])
    assert doc["write_files"][0]["path"].endswith("provision-2.sh")


# ---------------------------------------------------------------------------
# Cache (checksum verify)
# ---------------------------------------------------------------------------


def test_fetch_base_image_verifies_and_caches(tmp_path):
    payload = b"fake-qcow2-bytes" * 1000
    digest = hashlib.sha512(payload).hexdigest()
    sums = f"{digest}  debian-12-genericcloud-amd64.qcow2\n".encode()
    urls = {
        "http://x/debian-12-genericcloud-amd64.qcow2": payload,
        "http://x/SHA512SUMS": sums,
    }

    def opener(url):
        return io.BytesIO(urls[url])

    base = BaseImage(
        url="http://x/debian-12-genericcloud-amd64.qcow2",
        filename="debian-12-genericcloud-amd64.qcow2",
        checksum_algo="sha512",
        checksum_url="http://x/SHA512SUMS",
    )
    out = fetch_base_image(base, tmp_path, opener=opener)
    assert out.read_bytes() == payload
    # Second call is a cache hit (no opener needed for the image itself).
    out2 = fetch_base_image(base, tmp_path, opener=opener)
    assert out2 == out


def test_fetch_base_image_checksum_mismatch_raises(tmp_path):
    payload = b"data"
    wrong = "0" * 128
    urls = {"http://x/y.qcow2": payload, "http://x/SUMS": f"{wrong}  y.qcow2\n".encode()}

    def opener(url):
        return io.BytesIO(urls[url])

    base = BaseImage(
        url="http://x/y.qcow2", filename="y.qcow2",
        checksum_algo="sha512", checksum_url="http://x/SUMS",
    )
    with pytest.raises(ImageBuildError) as exc:
        fetch_base_image(base, tmp_path, opener=opener)
    assert "checksum mismatch" in str(exc.value).lower()


def test_fetch_base_image_inline_checksum(tmp_path):
    payload = b"abc"
    digest = hashlib.sha256(payload).hexdigest()
    base = BaseImage(
        url="http://x/z.qcow2", filename="z.qcow2",
        checksum_algo="sha256", checksum=digest,
    )

    def opener(url):
        return io.BytesIO(payload)

    out = fetch_base_image(base, tmp_path, opener=opener)
    assert out.is_file()


# ---------------------------------------------------------------------------
# Config fallback
# ---------------------------------------------------------------------------


def test_build_config_falls_back_to_deploy(monkeypatch, tmp_path):
    for k in (
        "BUILD_API_TOKEN", "BUILD_SSH_USER", "BUILD_SSH_KEY", "BUILD_SSH_HOST",
        "BUILD_NODE", "BUILD_STORAGE", "BUILD_BRIDGE", "BUILD_SNIPPET_STORAGE",
        "BUILD_CACHE_DIR",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = load_build_config(
        tmp_path / "missing.conf",
        overrides={
            "PROXMOX_API_TOKEN": "deploytok",
            "DEFAULT_NODE": "n1",
            "OVS_SSH_HOST": "h1",
            "OVS_SSH_KEY": "/keys/id",
        },
    )
    assert cfg.api_token == "deploytok"  # falls back to deploy token
    assert cfg.node == "n1"  # falls back to DEFAULT_NODE
    assert cfg.ssh_user == "root"  # default
    assert cfg.ssh_host == "h1"  # falls back to OVS_SSH_HOST
    assert cfg.ssh_key == "/keys/id"  # falls back to OVS_SSH_KEY
    assert cfg.storage == "local-lvm"  # default
    assert cfg.bridge == "vmbr0"  # default
    assert cfg.ipconfig == "ip=dhcp"  # default


def test_build_config_build_keys_override(monkeypatch, tmp_path):
    for k in ("BUILD_API_TOKEN", "BUILD_NODE", "BUILD_STORAGE", "BUILD_IPCONFIG"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_build_config(
        tmp_path / "missing.conf",
        overrides={
            "PROXMOX_API_TOKEN": "deploytok",
            "DEFAULT_NODE": "n1",
            "BUILD_API_TOKEN": "buildtok",
            "BUILD_NODE": "bn",
            "BUILD_STORAGE": "infra-lvm",
            "BUILD_IPCONFIG": "ip=10.0.0.90/24,gw=10.0.0.1",
        },
    )
    assert cfg.api_token == "buildtok"
    assert cfg.node == "bn"
    assert cfg.storage == "infra-lvm"
    assert cfg.ipconfig == "ip=10.0.0.90/24,gw=10.0.0.1"
    # The smoke-test still runs through the deploy-scope token.
    assert cfg.deploy.proxmox_api_token == "deploytok"


def test_rest_and_ssh_views_carry_distinct_identities():
    cfg = _build_cfg()
    cfg.api_token = "buildtok"
    cfg.ssh_user = "root"
    assert cfg.rest_config().proxmox_api_token == "buildtok"
    assert cfg.ssh_config().ovs_ssh_user == "root"
    # The deploy config is untouched.
    assert cfg.deploy.proxmox_api_token == "deploytoken"
    assert cfg.deploy.ovs_ssh_user == "ovs-operator"


# ---------------------------------------------------------------------------
# binding_io round-trip
# ---------------------------------------------------------------------------


def test_binding_io_roundtrip_preserves_comments(fixtures_dir, tmp_path):
    src = fixtures_dir / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    dst = tmp_path / "debian-12-cloud.yaml"
    shutil.copy(src, dst)

    assert binding_io.read_template_vmid(dst, "v1") == 610
    result = binding_io.write_template_vmid(dst, "v1", 99123)
    assert result == {"version_id": "v1", "old_vmid": 610, "new_vmid": 99123}
    assert binding_io.read_template_vmid(dst, "v1") == 99123

    text = dst.read_text()
    # The load-bearing i440fx comment survives the round-trip.
    assert "Q35's IDE-on-ICH9" in text
    # The build recipe survives too.
    assert "base_image" in text


def test_binding_io_clear_template_vmid(fixtures_dir, tmp_path):
    """Finding #1: the symmetric undo of write_template_vmid — pops the key
    entirely (not just to some placeholder), preserving comments + layout."""
    src = fixtures_dir / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    dst = tmp_path / "debian-12-cloud.yaml"
    shutil.copy(src, dst)

    assert binding_io.read_template_vmid(dst, "v1") == 610
    binding_io.clear_template_vmid(dst, "v1")
    assert binding_io.read_template_vmid(dst, "v1") is None

    text = dst.read_text()
    assert "Q35's IDE-on-ICH9" in text  # comments survive
    assert "base_image" in text  # the build recipe survives


def test_binding_path_for(tmp_path):
    p = binding_io.binding_path_for(tmp_path, "debian-12-cloud")
    assert p == tmp_path / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"


# ---------------------------------------------------------------------------
# importdisk volid parse
# ---------------------------------------------------------------------------


def test_parse_imported_volid():
    out = "importing disk...\nSuccessfully imported disk as 'unused0:local-lvm:vm-4242-disk-0'\n"
    assert _parse_imported_volid(out) == "local-lvm:vm-4242-disk-0"


def test_parse_imported_volid_miss():
    assert _parse_imported_volid("no match here") is None


def test_smoke_scenario_salt_avoids_id_collision():
    """Finding #7: two concurrent builds of the SAME image must not mint the
    same smoke-scenario id (plan_id, and every SDN/VM name it drives, derives
    from the scenario id)."""
    unsalted = build_smoke_scenario_yaml("debian-12-cloud", "v1", "linux")
    a = build_smoke_scenario_yaml("debian-12-cloud", "v1", "linux", salt=99500)
    b = build_smoke_scenario_yaml("debian-12-cloud", "v1", "linux", salt=99700)
    assert a != b
    assert "id: imgsmoke-debian-12-cloud-99500" in a
    assert "id: imgsmoke-debian-12-cloud-99700" in b
    # salt=None (the default) reproduces the original unsalted id unchanged.
    assert "id: imgsmoke-debian-12-cloud\n" in unsalted


# ---------------------------------------------------------------------------
# Smoke scenario validity
# ---------------------------------------------------------------------------


def test_smoke_scenario_validates_and_plans_without_mirror(fixtures_dir, tmp_path):
    from axrange.ir.parser import parse_ir
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.validator.engine import validate_full

    manifests = load_registry(root=fixtures_dir)
    scenario = build_smoke_scenario_yaml("debian-12-cloud", "v1", "linux")
    path = tmp_path / "scenario.yaml"
    path.write_text(scenario)

    result = validate_full(str(path), manifests=manifests)
    assert result.exit_code == 0, [f.message for f in result.errors]

    ctx = ClusterContext.single_node("pxnode")
    pr = parse_ir(path=str(path))
    plan = build_plan(pr.ir, manifests, ctx, raw_ir=pr.raw)
    op_names = {op.op_name for op in plan.ops.values()}
    # Untapped → no mirror ops, no sensor dependency.
    assert "install-ovs-mirror" not in op_names
    assert "attach-gre-tunnel" not in op_names
    # But the host lifecycle is present.
    assert "clone-vm" in op_names
    assert "start-vm" in op_names


# ---------------------------------------------------------------------------
# Builder orchestration (full flow, mocked transport)
# ---------------------------------------------------------------------------


def test_build_image_forwards_build_vmid_to_smoke_test(fixtures_dir, tmp_path):
    """Finding #7: builder.build_image must forward the just-templatized
    build vmid to smoke_test so it can salt the smoke scenario id."""
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(next_vmid=99550)
    seen = {}

    def _spy_smoke(*a, **k):
        seen.update(k)
        return _ok_smoke(*a, **k)

    report = build_image(
        "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
        fetch=_fake_fetch, smoke_test=_spy_smoke,
    )
    assert seen.get("build_vmid") == report.template_vmid == 99550


def test_build_image_happy_path(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(next_vmid=99500)

    report = build_image(
        "debian-12-cloud",
        images_root=root,
        build_cfg=cfg,
        bp=bp,
        fetch=_fake_fetch,
        smoke_test=_ok_smoke,
    )

    assert report.template_vmid == 99500
    assert report.prior_template_vmid == 610
    assert report.probe_report is not None and report.probe_report.ok
    assert report.smoke_report is not None and report.smoke_report.ok
    assert report.conformance_ok

    # Binding was updated in the working copy.
    binding_file = root / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    assert binding_io.read_template_vmid(binding_file, "v1") == 99500

    # Lifecycle: created, templatized, NOT destroyed (it's the new template).
    names = [c[0] for c in bp.calls]
    assert "create_vm" in names
    assert "templatize" in names
    assert "destroy_vm" not in names

    # Finding #2: the build-time cloud-init (cicustom snippet + ipconfig0) is
    # stripped BEFORE templatize, so clones don't inherit it.
    assert "clear_build_cloudinit" in names
    assert names.index("clear_build_cloudinit") < names.index("templatize")
    clear_call = next(c for c in bp.calls if c[0] == "clear_build_cloudinit")
    assert clear_call[1] == 99500
    assert clear_call[2] == "axr-build-99500.yaml"


def test_smoke_teardown_removes_sdn_native_objects():
    """Finding #4: the smoke deploy is SDN-native (VLAN zone), so teardown must
    remove the SDN vnet + zone + underlay OVS bridge — NOT the obsolete
    pre-SDN-native per-net bridge name, which left everything behind."""
    from types import SimpleNamespace

    from axrange.adapter_proxmox.naming import (
        bridge_name_for_net,
        underlay_bridge_for_plan,
        vmid_for_op,
        vnet_id_for_net,
        zone_id_for_plan,
    )
    from axrange.imagebuild.conformance import _smoke_teardown

    plan = SimpleNamespace(
        plan_id="imgsmoke-deadbeef",
        ops={
            "clone-vm[smoke-h]": SimpleNamespace(
                op_name="clone-vm", op_id="clone-vm[smoke-h]"
            )
        },
    )
    vmid = vmid_for_op(plan.plan_id, "clone-vm[smoke-h]")
    bp = FakeBuildProxmox(preexisting=vmid)

    _smoke_teardown(bp, plan, "smoke-net", lambda _m: None)

    assert ("destroy_vm", vmid) in bp.calls
    assert (
        "delete_sdn",
        zone_id_for_plan(plan.plan_id),
        vnet_id_for_net("smoke-net", plan_id=plan.plan_id),
    ) in bp.calls
    assert ("delete_bridge", underlay_bridge_for_plan(plan.plan_id)) in bp.calls
    # Regression: the obsolete per-net bridge name must NOT be the delete target.
    assert ("delete_bridge", bridge_name_for_net("smoke-net")) not in bp.calls


def test_build_image_explicit_vmid(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox()

    report = build_image(
        "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
        fetch=_fake_fetch, smoke_test=_ok_smoke,
        options=BuildOptions(build_vmid=611, skip_smoke=True),
    )
    assert report.template_vmid == 611
    assert report.smoke_report is None  # skipped
    assert ("next_vmid",) not in bp.calls  # explicit vmid → no nextid call


def test_build_image_probe_failure_aborts_and_cleans_up(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    # Fail the kernel:ebpf probe.
    bp = FakeBuildProxmox(next_vmid=99600, exec_exit_map={"/sys/fs/bpf": 1})

    with pytest.raises(ImageBuildError) as exc:
        build_image(
            "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
            fetch=_fake_fetch, smoke_test=_ok_smoke,
        )
    assert exc.value.stage == "conformance"

    # Build VM destroyed; binding NOT modified (probes fail before the write).
    names = [c[0] for c in bp.calls]
    assert "destroy_vm" in names
    assert "templatize" not in names
    binding_file = root / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    assert binding_io.read_template_vmid(binding_file, "v1") == 610


def test_build_image_smoke_failure_reverts_binding(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(next_vmid=99700)

    with pytest.raises(ImageBuildError) as exc:
        build_image(
            "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
            fetch=_fake_fetch, smoke_test=_fail_smoke,
        )
    assert exc.value.stage == "conformance"

    # Templatized, binding written then REVERTED, build VM destroyed.
    names = [c[0] for c in bp.calls]
    assert "templatize" in names
    assert "destroy_vm" in names
    binding_file = root / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    assert binding_io.read_template_vmid(binding_file, "v1") == 610


def test_build_image_ignore_conformance_proceeds(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(next_vmid=99800, exec_exit_map={"/sys/fs/bpf": 1})

    report = build_image(
        "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
        fetch=_fake_fetch, smoke_test=_ok_smoke,
        options=BuildOptions(ignore_conformance=True),
    )
    # Proceeds despite the failed probe; binding updated.
    assert report.template_vmid == 99800
    assert report.conformance_ignored
    assert not report.conformance_ok
    binding_file = root / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    assert binding_io.read_template_vmid(binding_file, "v1") == 99800


def test_build_image_refuses_to_clobber_existing_vmid(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(preexisting=611)

    with pytest.raises(ImageBuildError) as exc:
        build_image(
            "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
            fetch=_fake_fetch, smoke_test=_ok_smoke,
            options=BuildOptions(build_vmid=611),
        )
    assert exc.value.stage == "stage"
    # Never created or destroyed the pre-existing VM.
    names = [c[0] for c in bp.calls]
    assert "create_vm" not in names
    assert "destroy_vm" not in names


def test_build_image_first_build_smoke_failure_clears_binding(fixtures_dir, tmp_path):
    """Finding #1 (MUST-FIX): a NEW image's first-ever build has no prior
    `template_vmid` (the key is absent, not merely `None` from a stale read).
    On a later-stage failure (here: smoke-test) the old unwind guard
    (`prior_vmid is not None`) skipped reverting entirely — the build VM was
    destroyed but the binding was LEFT pointing at that now-destroyed vmid,
    looking valid in a diff. The fix must leave the binding with NO
    `template_vmid` at all, exactly the pre-build state."""
    root = _images_root(fixtures_dir, tmp_path)
    bfile = root / "images" / "bindings" / "proxmox" / "debian-12-cloud.yaml"
    # Simulate a genuinely first-ever build: strip the key entirely (not just
    # set it to a sentinel), matching binding_io.read_template_vmid -> None.
    text = bfile.read_text()
    assert ", template_vmid: 610" in text
    bfile.write_text(text.replace(", template_vmid: 610", ""))
    assert binding_io.read_template_vmid(bfile, "v1") is None

    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = FakeBuildProxmox(next_vmid=99900)

    with pytest.raises(ImageBuildError) as exc:
        build_image(
            "debian-12-cloud", images_root=root, build_cfg=cfg, bp=bp,
            fetch=_fake_fetch, smoke_test=_fail_smoke,
        )
    assert exc.value.stage == "conformance"

    names = [c[0] for c in bp.calls]
    assert "templatize" in names  # got as far as templatizing
    assert "destroy_vm" in names  # ...then unwound: build vm destroyed

    # The load-bearing assertion: NOT reverted to the (nonexistent) prior
    # vmid, NOT left holding the just-destroyed build vmid — cleared.
    assert binding_io.read_template_vmid(bfile, "v1") is None
    # Comments + the rest of the binding survive the clear round-trip.
    assert "Q35's IDE-on-ICH9" in bfile.read_text()


def test_build_image_unknown_image_raises(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg()
    with pytest.raises(ImageBuildError) as exc:
        build_image("does-not-exist", images_root=root, build_cfg=cfg,
                    bp=FakeBuildProxmox(), fetch=_fake_fetch)
    assert exc.value.stage == "read-inputs"


# ---------------------------------------------------------------------------
# BuildProxmox transport-level tests (findings #2-#5, code-review 2026-07-07)
#
# Unlike the FakeBuildProxmox-driven tests above (which stub the whole
# BuildProxmox interface to drive builder.build_image's orchestration), these
# exercise the REAL BuildProxmox methods against a fake REST client — the
# same client-stub approach as tests/test_guest_agent.py — so the guest_exec /
# wait_cloud_init / sysprep_seal logic itself is under test, not just the
# orchestration around it.
# ---------------------------------------------------------------------------


class _SequencedGuestRest:
    """Fake RestClient that answers successive `agent/exec` calls from a
    queue of canned results, keyed by allocation order (a fresh pid per
    exec). Also answers `agent/ping` (ok) and `status/current` (stopped, so
    sysprep_seal's post-check `wait_vm_stopped` doesn't hang a test)."""

    def __init__(self, exec_results):
        self._queue = list(exec_results)  # list of dicts: exitcode/stdout/stderr
        self._pending: dict[int, dict] = {}
        self._next_pid = 1
        self.calls: list[tuple] = []

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if method == "POST" and path.endswith("/agent/ping"):
            return {}
        if method == "POST" and path.endswith("/agent/exec"):
            pid = self._next_pid
            self._next_pid += 1
            self._pending[pid] = self._queue.pop(0)
            return {"pid": pid}
        if method == "GET" and path.endswith("/agent/exec-status"):
            res = self._pending[params["pid"]]
            return {
                "exited": True,
                "exitcode": res.get("exitcode", 0),
                "out-data": res.get("stdout", ""),
                "err-data": res.get("stderr", ""),
            }
        if method == "GET" and path.endswith("/status/current"):
            return {"status": "stopped"}
        return {}


def test_wait_cloud_init_fails_on_reported_provisioning_errors():
    """Finding #3: `status --wait` alone accepts a cloud-init run whose
    runcmd/provisioning failed (exit 0/2 either way) — the follow-up
    `--format json` read must fail loud on a populated errors[]."""
    cfg = _build_cfg()
    rest = _SequencedGuestRest([
        {"exitcode": 0, "stdout": ""},  # `cloud-init status --wait`
        {  # `cloud-init status --format json`
            "exitcode": 0,
            "stdout": '{"errors": ["apt-get install nginx=1.22 failed"]}',
        },
    ])
    bp = BuildProxmox(cfg, rest_client=rest)

    with pytest.raises(ImageBuildError) as exc:
        bp.wait_cloud_init(42, timeout_s=5)
    assert exc.value.stage == "bake"
    assert "apt-get install nginx=1.22 failed" in str(exc.value)


def test_wait_cloud_init_passes_when_status_clean():
    cfg = _build_cfg()
    rest = _SequencedGuestRest([
        {"exitcode": 0, "stdout": ""},
        {"exitcode": 0, "stdout": '{"errors": []}'},
    ])
    bp = BuildProxmox(cfg, rest_client=rest)
    bp.wait_cloud_init(42, timeout_s=5)  # must not raise


def test_wait_cloud_init_still_fails_on_bad_wait_exit_code():
    """Regression: the pre-existing exit-code gate on `status --wait` itself
    (exit code outside {0, 2}) is unchanged."""
    cfg = _build_cfg()
    rest = _SequencedGuestRest([{"exitcode": 1, "stdout": "cloud-init failed"}])
    bp = BuildProxmox(cfg, rest_client=rest)
    with pytest.raises(ImageBuildError) as exc:
        bp.wait_cloud_init(42, timeout_s=5)
    assert exc.value.stage == "bake"
    assert "exited 1" in str(exc.value)


def test_sysprep_seal_fails_loud_on_nonzero_generalize_exit():
    """Finding #2: sysprep can fail /generalize yet still power the VM off —
    guest_exec never raises on a non-zero exit, so the caller must check it
    explicitly and surface stderr rather than silently templatizing."""
    cfg = _build_cfg()
    rest = _SequencedGuestRest([
        {"exitcode": 1, "stderr": "sysprep failed: a Windows Update is pending"}
    ])
    bp = BuildProxmox(cfg, rest_client=rest)

    with pytest.raises(ImageBuildError) as exc:
        bp.sysprep_seal(42, ["cmd", "/c", "sysprep", "/generalize", "/shutdown"])
    assert exc.value.stage == "seal"
    assert "a Windows Update is pending" in str(exc.value)
    # Must not have gone on to poll for stop — the generalize itself failed.
    assert not any(
        m == "GET" and p.endswith("/status/current") for m, p, *_ in rest.calls
    )


def test_sysprep_seal_succeeds_and_waits_for_stop_on_clean_exit():
    cfg = _build_cfg()
    rest = _SequencedGuestRest([{"exitcode": 0}])
    bp = BuildProxmox(cfg, rest_client=rest)
    bp.sysprep_seal(42, ["cmd", "/c", "sysprep", "/generalize", "/shutdown"])  # no raise
    assert any(m == "GET" and p.endswith("/status/current") for m, p, *_ in rest.calls)


def test_sysprep_seal_swallows_a_benign_transport_failure_mid_shutdown():
    """sysprep's own /shutdown can kill QGA before exec-status answers — that
    TRANSPORT error (not a returned exit code) must stay benign, exactly as
    before finding #2's fix."""
    cfg = _build_cfg()

    class _DyingRest:
        def __init__(self):
            self.calls: list[tuple] = []

        def request(self, method, path, *, params=None, body=None):
            self.calls.append((method, path, params, body))
            if method == "POST" and path.endswith("/agent/exec"):
                raise AdapterTimeout("qga connection reset")
            if method == "GET" and path.endswith("/status/current"):
                return {"status": "stopped"}
            return {}

    bp = BuildProxmox(cfg, rest_client=_DyingRest())
    bp.sysprep_seal(42, ["cmd", "/c", "sysprep"])  # must not raise


def test_assert_local_storage_rejects_non_local():
    """Finding #5: a non-`local` snippet/ISO storage is a conflation bug (the
    scp path is hardcoded to /var/lib/vz while the returned volid names a
    different storage) — fail loud instead of writing to the wrong place."""
    cfg = _build_cfg()
    cfg.snippet_storage = "infra-nfs"
    rest = _SequencedGuestRest([])
    bp = BuildProxmox(cfg, rest_client=rest)

    with pytest.raises(ImageBuildError) as exc:
        bp.set_cloudinit(42, "snippet.yaml", "#cloud-config\n")
    assert exc.value.stage == "bake"
    assert "infra-nfs" in str(exc.value)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_parser_wires_image_build():
    from axrange.cli.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["image", "build", "debian-12-cloud", "--skip-smoke"])
    assert args.command == "image"
    assert args.image_command == "build"
    assert args.image == "debian-12-cloud"
    assert args.skip_smoke is True


def test_cli_image_build_missing_images_root(tmp_path, capsys):
    from axrange.cli.main import main

    rc = main(["image", "build", "debian-12-cloud", "--images", str(tmp_path)])
    assert rc == 2
    assert "no images/manifests" in capsys.readouterr().err
