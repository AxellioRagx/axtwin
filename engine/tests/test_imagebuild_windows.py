"""Tests for the Windows branch of the image build pipeline (W0 —
docs/SLICE_WINDOWS_AD.md §4).

The autounattend render, the Windows provisioning translation, the probe table,
and the Windows recipe parsing are pure functions and fully checked here. The
builder Windows orchestration is driven by a fake (mirroring
tests/test_imagebuild.py); the real ISO-driven build is the W0 live-verify step
(needs a Windows Server ISO + virtio-win ISO + a cluster run).
"""
from __future__ import annotations

import xml.dom.minidom as minidom
from pathlib import Path

import pytest

from axrange.imagebuild import BuildOptions, ImageBuildError, build_image, binding_io
from axrange.imagebuild.bake_windows import (
    SETUP_COMPLETE_PATH,
    WINDOWS_BUILD_COMPLETE_MARKER,
    default_windows_probe_for,
    render_autounattend,
    render_setup_complete_cmd,
    render_sysprep_unattend,
    windows_probe_command,
    windows_provision_to_commands,
    windows_sysprep_command,
)
from axrange.imagebuild.conformance import run_probes
from axrange.imagebuild.proxmox_build import BuildProxmox
from axrange.imagebuild.recipe import (
    BaseImage,
    WindowsRecipe,
    recipe_from_binding_version,
)
from axrange.manifest.schema import BindingVersion
from tests.test_imagebuild import (
    FakeBuildProxmox,
    _SequencedGuestRest,
    _build_cfg,
    _fake_fetch,
    _images_root,
)


def _win() -> WindowsRecipe:
    return WindowsRecipe(
        virtio_iso=BaseImage(
            url="http://x/virtio-win.iso", filename="virtio-win.iso",
            checksum="a" * 64, checksum_algo="sha256",
        ),
        edition="Windows Server 2022 SERVERSTANDARD",
        image_index=2,
        admin_password="Pw1!",
        locale="en-US",
        timezone="UTC",
    )


def _decoded_first_logon(xml: str) -> str:
    """Concatenate the decoded -EncodedCommand payloads (FirstLogonCommands
    base64-encode their PowerShell, so install steps aren't plaintext)."""
    import base64
    import re

    return "\n".join(
        base64.b64decode(b).decode("utf-16-le")
        for b in re.findall(r"-EncodedCommand (\S+)", xml)
    )


class WinFakeBuildProxmox(FakeBuildProxmox):
    """The Linux fake + the Windows build-VM methods, all recording calls."""

    def create_windows_vm(self, vmid, name, **kw):
        self._record("create_windows_vm", vmid, name, kw)

    def upload_iso(self, local_path, **kw):
        self._record("upload_iso", local_path)
        return f"local:iso/{Path(local_path).name}"

    def make_autounattend_iso(self, vmid, xml, **kw):
        self._record("make_autounattend_iso", vmid)
        return (
            f"local:iso/axr-unattend-{vmid}.iso",
            f"/var/lib/vz/template/iso/axr-unattend-{vmid}.iso",
        )

    def attach_windows_media(self, vmid, **kw):
        self._record("attach_windows_media", vmid)

    def press_boot_key(self, vmid, **kw):
        self._record("press_boot_key", vmid)

    def wait_windows_ready(self, vmid, **kw):
        self._record("wait_windows_ready", vmid)

    def stage_sysprep_unattend(self, vmid, xml, **kw):
        self._record("stage_sysprep_unattend", vmid, xml)
        return r"C:\Windows\System32\Sysprep\axr-unattend.xml"

    def stage_setup_complete(self, vmid, content, **kw):
        self._record("stage_setup_complete", vmid, content)
        return SETUP_COMPLETE_PATH

    def sysprep_seal(self, vmid, argv, **kw):
        self._record("sysprep_seal", vmid, tuple(argv))

    def detach_windows_media(self, vmid, **kw):
        self._record("detach_windows_media", vmid)

    def wait_vm_stopped(self, vmid, **kw):
        self._record("wait_vm_stopped", vmid)


# ---------------------------------------------------------------------------
# Recipe parsing
# ---------------------------------------------------------------------------


def test_windows_recipe_parses(manifests):
    bv = manifests.get_binding("windows-server-2022", "proxmox").get_version("v1")
    r = recipe_from_binding_version(bv)
    assert r.windows is not None
    assert r.windows.edition == "Windows Server 2022 SERVERSTANDARD"
    assert r.windows.image_index == 2
    assert r.windows.virtio_iso.filename == "virtio-win.iso"
    assert r.base_image.filename == "windows-server-2022-eval.iso"
    assert r.disk_size == "48G" and r.memory == 4096


def test_windows_recipe_requires_virtio():
    bv = BindingVersion.model_validate(
        {
            "id": "v1",
            "build": {
                "base_image": {"url": "http://x/w.iso", "checksum": "a" * 64, "checksum_algo": "sha256"},
                "windows": {"edition": "X", "admin_password": "p"},
            },
        }
    )
    with pytest.raises(ImageBuildError) as e:
        recipe_from_binding_version(bv)
    assert "virtio" in str(e.value).lower()


def test_windows_recipe_requires_edition_or_index():
    bv = BindingVersion.model_validate(
        {
            "id": "v1",
            "build": {
                "base_image": {"url": "http://x/w.iso", "checksum": "a" * 64, "checksum_algo": "sha256"},
                "windows": {
                    "admin_password": "p",
                    "virtio_iso": {"url": "http://x/v.iso", "checksum": "b" * 64, "checksum_algo": "sha256"},
                },
            },
        }
    )
    with pytest.raises(ImageBuildError) as e:
        recipe_from_binding_version(bv)
    assert "edition" in str(e.value).lower() or "image_index" in str(e.value).lower()


def test_windows_recipe_requires_admin_password():
    bv = BindingVersion.model_validate(
        {
            "id": "v1",
            "build": {
                "base_image": {"url": "http://x/w.iso", "checksum": "a" * 64, "checksum_algo": "sha256"},
                "windows": {
                    "edition": "X",
                    "virtio_iso": {"url": "http://x/v.iso", "checksum": "b" * 64, "checksum_algo": "sha256"},
                },
            },
        }
    )
    with pytest.raises(ImageBuildError) as e:
        recipe_from_binding_version(bv)
    assert "admin_password" in str(e.value).lower()


def test_windows_server_manifest_supports_role_adds(manifests):
    cv = manifests.get_manifest("windows-server-2022").current_version()
    assert cv.install_support.supports("role")
    assert "adds" in cv.install_support.valid_role_names()


# ---------------------------------------------------------------------------
# autounattend.xml
# ---------------------------------------------------------------------------


def test_autounattend_deterministic_and_wellformed():
    a = render_autounattend(hostname="h", win=_win(), provision=[{"pkg": "git"}])
    b = render_autounattend(hostname="h", win=_win(), provision=[{"pkg": "git"}])
    assert a == b
    minidom.parseString(a)  # raises on malformed XML


def test_autounattend_selects_edition_by_name():
    x = render_autounattend(hostname="h", win=_win())
    assert "/IMAGE/NAME" in x and "Windows Server 2022 SERVERSTANDARD" in x


def test_autounattend_selects_by_index_when_no_edition():
    w = WindowsRecipe(virtio_iso=_win().virtio_iso, edition=None, image_index=4, admin_password="P")
    x = render_autounattend(hostname="h", win=w)
    assert "/IMAGE/INDEX" in x and "<Value>4</Value>" in x


def test_autounattend_bakes_qga_marker_drivers_and_name():
    x = render_autounattend(hostname="dc01", win=_win())
    # The guest-tools install (QGA + virtio drivers) is an encoded FirstLogonCommand.
    assert "virtio-win-guest-tools" in _decoded_first_logon(x)
    assert WINDOWS_BUILD_COMPLETE_MARKER in x  # completion marker FirstLogonCommand
    assert "<ComputerName>dc01</ComputerName>" in x
    assert "vioscsi" in x  # virtio storage driver injected in windowsPE (multi-letter)


def test_autounattend_can_skip_cloudbaseinit():
    x = render_autounattend(hostname="h", win=_win(), install_cloudbaseinit=False)
    assert "cloudbase" not in x.lower()


def test_autounattend_computer_name_is_netbios_valid():
    # The build VM name is >15 chars; the specialize ComputerName must be <=15
    # (NetBIOS) or Windows rejects the answer file for the specialize pass
    # (live-verify, dev45 2026-06-16).
    import re

    x = render_autounattend(hostname="axr-build-windows-server-2022-v1", win=_win())
    cn = re.search(r"<ComputerName>([^<]*)</ComputerName>", x).group(1)
    assert 1 <= len(cn) <= 15, f"ComputerName {cn!r} is {len(cn)} chars"


def test_autounattend_driver_paths_cover_multiple_letters():
    # The virtio ISO's WinPE drive letter isn't predictable, so the driver
    # injection lists several candidate letters (live-verify: it was D:, not E:).
    x = render_autounattend(hostname="h", win=_win())
    assert "D:\\vioscsi" in x and "E:\\vioscsi" in x


def test_autounattend_escapes_special_chars():
    w = WindowsRecipe(virtio_iso=_win().virtio_iso, edition="Std & <Eval>", admin_password="p&w<>")
    x = render_autounattend(hostname="h", win=w)
    minidom.parseString(x)  # the & / < / > must be XML-escaped
    assert "Std &amp; &lt;Eval&gt;" in x


# ---------------------------------------------------------------------------
# Provisioning translation
# ---------------------------------------------------------------------------


def test_windows_provision_bootstraps_choco_once_for_pkgs():
    cmds = windows_provision_to_commands([{"pkg": "git"}, {"pkg": "curl"}])
    assert sum("chocolatey.org/install.ps1" in c for c in cmds) == 1
    assert any(c.endswith("git") for c in cmds)
    assert any(c.endswith("curl") for c in cmds)


def test_windows_provision_pinned_version():
    cmds = windows_provision_to_commands([{"pkg": "nginx", "version": "1.2"}])
    assert any("--version=1.2 nginx" in c for c in cmds)


def test_windows_provision_inline_script_encoded_no_bootstrap():
    cmds = windows_provision_to_commands([{"script": {"inline": "Write-Host hi"}}])
    assert any("EncodedCommand" in c for c in cmds)
    assert not any("chocolatey" in c for c in cmds)  # no pkg -> no choco bootstrap


def test_windows_provision_role_is_loud_error():
    with pytest.raises(ImageBuildError) as e:
        windows_provision_to_commands([{"role": "adds"}])
    assert "not supported" in str(e.value)


# ---------------------------------------------------------------------------
# Probes + sysprep
# ---------------------------------------------------------------------------


def test_windows_probes_cover_server_provides():
    for cap in ("windows", "host:uefi", "kernel:powershell-5plus", "kernel:dotnet-clr"):
        assert default_windows_probe_for(cap) is not None


def test_windows_probe_override_wins():
    assert windows_probe_command("windows", {"windows": "exit 0"}) == "exit 0"


def test_run_probes_windows_uses_powershell():
    bp = WinFakeBuildProxmox()
    rep = run_probes(bp, 9, ["windows", "host:uefi"], os_family="windows")
    assert rep.ok
    last = [c for c in bp.calls if c[0] == "guest_exec"][-1]
    assert last[2][0] == "powershell"


def test_run_probes_windows_reports_failure():
    bp = WinFakeBuildProxmox(exec_exit_map={"winload.efi": 1})
    rep = run_probes(bp, 9, ["windows", "host:uefi"], os_family="windows")
    assert not rep.ok
    assert [r.capability for r in rep.failures] == ["host:uefi"]


def test_sysprep_command_generalizes_and_shuts_down():
    argv = windows_sysprep_command()
    assert "/generalize" in argv and "/shutdown" in argv and "/oobe" in argv


def test_sysprep_command_appends_unattend():
    argv = windows_sysprep_command(unattend_path=r"C:\x\u.xml")
    assert r"/unattend:C:\x\u.xml" in argv


# ---------------------------------------------------------------------------
# BuildProxmox Windows file-write staging (finding #4, code-review 2026-07-07)
#
# `stage_sysprep_unattend` / `stage_setup_complete` now delegate to the shared
# adapter_proxmox.GuestAgent (LES-025 reuse) instead of hand-rolling the
# base64/[IO.File]::WriteAllBytes PowerShell — exercise the real methods
# against a fake REST client (WinFakeBuildProxmox only stubs them out).
# ---------------------------------------------------------------------------


def test_stage_sysprep_unattend_writes_via_guest_agent():
    cfg = _build_cfg()
    rest = _SequencedGuestRest([{"exitcode": 0}])
    bp = BuildProxmox(cfg, rest_client=rest)

    path = bp.stage_sysprep_unattend(7, "<unattend/>")
    assert path == r"C:\Windows\System32\Sysprep\axr-unattend.xml"

    execs = [c for c in rest.calls if c[0] == "POST" and c[1].endswith("/agent/exec")]
    assert len(execs) == 1
    argv = execs[0][3]["command"]
    assert argv[0] == "powershell"
    assert "WriteAllBytes" in argv[-1] and "axr-unattend.xml" in argv[-1]
    import base64
    assert base64.b64encode(b"<unattend/>").decode() in argv[-1]


def test_stage_sysprep_unattend_fails_loud_on_write_error():
    cfg = _build_cfg()
    rest = _SequencedGuestRest([{"exitcode": 1, "stderr": "access denied"}])
    bp = BuildProxmox(cfg, rest_client=rest)
    with pytest.raises(ImageBuildError) as exc:
        bp.stage_sysprep_unattend(7, "<unattend/>")
    assert exc.value.stage == "seal"
    assert "access denied" in str(exc.value)


def test_stage_setup_complete_creates_parent_dir_then_writes():
    cfg = _build_cfg()
    # First exec = the mkdir precheck (GuestAgent.write_file doesn't mkdir),
    # second exec = the actual write.
    rest = _SequencedGuestRest([{"exitcode": 0}, {"exitcode": 0}])
    bp = BuildProxmox(cfg, rest_client=rest)

    path = bp.stage_setup_complete(7, "@echo off\r\n")
    assert path == SETUP_COMPLETE_PATH

    execs = [c for c in rest.calls if c[0] == "POST" and c[1].endswith("/agent/exec")]
    assert len(execs) == 2
    mkdir_argv = execs[0][3]["command"]
    assert "New-Item" in mkdir_argv[-1] and "Windows\\Setup\\Scripts" in mkdir_argv[-1]
    write_argv = execs[1][3]["command"]
    assert "WriteAllBytes" in write_argv[-1]


# ---------------------------------------------------------------------------
# Clone-side sysprep unattend (the OOBE fix — live-verify dev45 2026-06-17)
# ---------------------------------------------------------------------------


def test_sysprep_unattend_skips_oobe_to_completion():
    x = render_sysprep_unattend(win=_win())
    assert render_sysprep_unattend(win=_win()) == x  # deterministic
    minidom.parseString(x)  # well-formed
    # specialize + oobeSystem only — NO windowsPE (the OS is already installed).
    assert 'pass="oobeSystem"' in x and 'pass="specialize"' in x
    assert 'pass="windowsPE"' not in x
    # International-Core governs the "Hi there" region screen — without it OOBE
    # stalls there and never reaches IMAGE_STATE_COMPLETE (W2a blocker, 2026-06-17).
    assert "Microsoft-Windows-International-Core" in x
    assert "<InputLocale>en-US</InputLocale>" in x
    assert "<SystemLocale>en-US</SystemLocale>" in x
    assert "<UserLocale>en-US</UserLocale>" in x
    # Skip*OOBE force OOBE to auto-complete (alongside the Hide* flags).
    assert "<HideEULAPage>true</HideEULAPage>" in x
    assert "<SkipMachineOOBE>true</SkipMachineOOBE>" in x
    assert "<SkipUserOOBE>true</SkipUserOOBE>" in x
    assert "<AutoLogon>" in x and "<Username>Administrator</Username>" in x
    # The marker is NO LONGER dropped from the unattend — it moved to a SYSTEM-context
    # SetupComplete.cmd (FirstLogonCommands need an interactive logon that never happened).
    assert "FirstLogonCommands" not in x
    assert "axr-osready" not in x


def test_setup_complete_cmd_drops_marker_matching_wait_os_ready():
    """SetupComplete.cmd (SYSTEM context, no logon) drops the os-ready marker
    wait-os-ready polls, and ensures QGA is running."""
    from axrange.adapter_proxmox.rest import WINDOWS_OSREADY_MARKER

    c = render_setup_complete_cmd(win=_win())
    assert render_setup_complete_cmd(win=_win()) == c  # deterministic
    assert c.endswith("\r\n") and "\r\n" in c  # CRLF — it's a .cmd
    assert WINDOWS_OSREADY_MARKER in c
    assert "QEMU-GA" in c
    assert "sc config QEMU-GA start= auto" in c
    assert "net start QEMU-GA" in c
    # baked at the fixed Setup path so Windows runs it once at the end of Setup
    assert SETUP_COMPLETE_PATH == r"C:\Windows\Setup\Scripts\SetupComplete.cmd"


# ---------------------------------------------------------------------------
# Builder Windows flow (full flow, mocked transport)
# ---------------------------------------------------------------------------


def test_build_windows_happy_path(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = WinFakeBuildProxmox(next_vmid=95000)

    report = build_image(
        "windows-server-2022",
        images_root=root,
        build_cfg=cfg,
        bp=bp,
        fetch=_fake_fetch,
    )

    assert report.template_vmid == 95000
    assert report.probe_report is not None and report.probe_report.ok
    assert report.smoke_report is None  # windows smoke skipped pending W1
    assert report.conformance_ok

    names = [c[0] for c in bp.calls]
    # The Windows staging path ran, NOT the Linux one.
    assert "create_windows_vm" in names and "create_vm" not in names
    assert "import_disk" not in names and "set_cloudinit" not in names
    assert names.count("upload_iso") == 2  # install ISO + virtio ISO
    assert "make_autounattend_iso" in names and "attach_windows_media" in names
    assert "wait_windows_ready" in names
    # The boot-key burst runs after start, before the install wait — it satisfies
    # the UEFI "press any key to boot from CD" prompt (live-verify fix 2026-06-16).
    assert names.index("start_vm") < names.index("press_boot_key") < names.index("wait_windows_ready")
    # The clone OOBE unattend is staged BEFORE sysprep, and the seal references it
    # via /unattend: (the W2a OOBE-blocker fix, live-verify dev45 2026-06-17).
    assert "stage_sysprep_unattend" in names
    assert names.index("stage_sysprep_unattend") < names.index("sysprep_seal")
    # SetupComplete.cmd (the SYSTEM-context os-ready hook) is staged before sysprep too.
    assert "stage_setup_complete" in names
    assert names.index("stage_setup_complete") < names.index("sysprep_seal")
    seal_call = next(c for c in bp.calls if c[0] == "sysprep_seal")
    assert any(str(a).startswith("/unattend:") for a in seal_call[2])
    # sysprep seals BEFORE media is detached, and both BEFORE templatize.
    assert names.index("sysprep_seal") < names.index("detach_windows_media")
    assert names.index("detach_windows_media") < names.index("templatize")

    # The binding template_vmid was written (0 -> the build vmid).
    bfile = root / "images" / "bindings" / "proxmox" / "windows-server-2022.yaml"
    assert binding_io.read_template_vmid(bfile, "v1") == 95000


def test_build_windows_missing_windows_block_raises(fixtures_dir, tmp_path):
    # Point the windows manifest at a binding whose build: has no windows: block.
    root = _images_root(fixtures_dir, tmp_path)
    bfile = root / "images" / "bindings" / "proxmox" / "windows-server-2022.yaml"
    text = bfile.read_text()
    # Drop the whole windows: sub-block (everything from "        windows:" on).
    text = text[: text.index("        windows:")]
    bfile.write_text(text)

    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = WinFakeBuildProxmox(next_vmid=95050)
    with pytest.raises(ImageBuildError) as e:
        build_image("windows-server-2022", images_root=root, build_cfg=cfg, bp=bp, fetch=_fake_fetch)
    assert "windows" in str(e.value).lower()


def test_build_windows_probe_failure_cleans_up(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    # Fail the host:uefi probe (its command contains 'winload.efi').
    bp = WinFakeBuildProxmox(next_vmid=95100, exec_exit_map={"winload.efi": 1})

    with pytest.raises(ImageBuildError) as e:
        build_image("windows-server-2022", images_root=root, build_cfg=cfg, bp=bp, fetch=_fake_fetch)
    assert e.value.stage == "conformance"

    names = [c[0] for c in bp.calls]
    assert "destroy_vm" in names
    assert "templatize" not in names  # never reached the template step
    bfile = root / "images" / "bindings" / "proxmox" / "windows-server-2022.yaml"
    assert binding_io.read_template_vmid(bfile, "v1") == 0  # binding untouched


def test_build_windows_ignore_conformance_proceeds(fixtures_dir, tmp_path):
    root = _images_root(fixtures_dir, tmp_path)
    cfg = _build_cfg(cache_dir=str(tmp_path / "cache"))
    bp = WinFakeBuildProxmox(next_vmid=95200, exec_exit_map={"winload.efi": 1})

    report = build_image(
        "windows-server-2022", images_root=root, build_cfg=cfg, bp=bp,
        fetch=_fake_fetch, options=BuildOptions(ignore_conformance=True),
    )
    assert report.template_vmid == 95200
    assert report.conformance_ignored and not report.conformance_ok
    bfile = root / "images" / "bindings" / "proxmox" / "windows-server-2022.yaml"
    assert binding_io.read_template_vmid(bfile, "v1") == 95200
