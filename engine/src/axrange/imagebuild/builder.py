"""ImageBuilder — orchestrates the docs/IMAGE_BUILD.md §3 build flow.

  1. Read inputs (manifest + binding + recipe).
  2. Fetch + verify the base image (cached on the orchestrator).
  3. Stage a build VM.
  4. Bake (Linux: cloud-init QGA + provisioning; Windows: ISO + autounattend).
  5. Wait for bake completion via the guest agent.
  6. Run conformance probes (§4.1) — pre-seal, on the running VM.
  7. Seal (Linux: cloud-init clean + reset identity; Windows: sysprep /generalize).
  8. Templatize (`qm template`) + write the new template_vmid into the binding.
  9. Smoke-test (§4.2) the freshly-built template through the real deploy path.

Steps 2-7 are OS-family-specific and live in `_linux_stage_to_seal` /
`_windows_stage_to_seal` (docs/SLICE_WINDOWS_AD.md W0); steps 1, 8-9 + the
failure-unwind are shared. The Windows smoke-test is skipped pending W1 (the
`wait-os-ready` windows branch), so a Windows build is conformance-checked by
its capability probes only until W1 lands (the §3 M1 interlock).

Conformance failure (probes or smoke) fails the build and unwinds the binding
edit (docs/IMAGE_BUILD.md §4.3); `--ignore-conformance` is the debug-only
escape hatch that proceeds with a loud warning. On any failure the transient
build VM is destroyed unless `--keep-build-vm`.

Conformance functions (`run_probes`, `smoke_test`) and the fetch + BuildProxmox
are injectable so the whole flow is unit-testable without a cluster.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from axrange.platform import default_namer
from axrange.imagebuild import bake as bake_mod
from axrange.imagebuild import bake_windows as bakewin_mod
from axrange.imagebuild import binding_io
from axrange.imagebuild import conformance as conf_mod
from axrange.imagebuild.cache import fetch_base_image
from axrange.imagebuild.config import BuildConfig
from axrange.imagebuild.errors import ImageBuildError
from axrange.imagebuild.proxmox_build import BuildProxmox
from axrange.imagebuild.recipe import recipe_from_binding_version
from axrange.manifest.loader import load_registry


@dataclass
class BuildOptions:
    version: str | None = None  # binding/manifest version id; default = current
    build_vmid: int | None = None  # explicit; default = /cluster/nextid
    refresh_cache: bool = False
    ignore_conformance: bool = False
    keep_build_vm: bool = False
    skip_smoke: bool = False


@dataclass
class BuildReport:
    image: str
    version: str
    template_vmid: int
    prior_template_vmid: int | None
    binding_file: str
    probe_report: conf_mod.ProbeReport | None = None
    smoke_report: conf_mod.SmokeReport | None = None
    conformance_ignored: bool = False

    @property
    def conformance_ok(self) -> bool:
        probes_ok = self.probe_report is None or self.probe_report.ok
        smoke_ok = self.smoke_report is None or self.smoke_report.ok
        return probes_ok and smoke_ok


def _noop(_msg: str) -> None:  # pragma: no cover - trivial
    pass


def _vm_name(image: str, version: str) -> str:
    """Golden template VM name (`img` tier tag, `docs/WORK_ORDER_resource_tags.md`
    §2): `img-<image-slug>`. This VM is never renamed at templatize time — it
    IS the eventual template — so the build-time name is already the final
    `img_*`-tier name. `version` is accepted (existing 4 call sites pass it)
    but not embedded: multiple versions of one image share the golden name;
    they are distinguished by VMID, not name."""
    del version  # unused — see docstring
    return default_namer().img_vm_name_for_image(image)


def _read_pubkey(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _probe_and_check(
    bp,
    build_vmid: int,
    provides: list[str],
    recipe,
    os_family: str,
    options: BuildOptions,
    run_probes: Callable[..., conf_mod.ProbeReport],
    log: Callable[[str], None],
) -> conf_mod.ProbeReport:
    """Step 6 (shared): run the capability probes, fail the build on a miss
    unless --ignore-conformance."""
    log("running capability probes")
    probe_report = run_probes(
        bp, build_vmid, provides, recipe.probes, os_family=os_family, log=log
    )
    if not probe_report.ok:
        failed = ", ".join(r.capability for r in probe_report.failures)
        if not options.ignore_conformance:
            raise ImageBuildError(
                f"capability probes failed: {failed}", stage="conformance"
            )
        log(f"WARNING: probes failed ({failed}); proceeding (--ignore-conformance)")
    return probe_report


def _linux_stage_to_seal(
    bp,
    build_vmid: int,
    *,
    image: str,
    version: str,
    recipe,
    manifest_version,
    provides: list[str],
    machine: str,
    bios: str,
    scsihw: str,
    ci_user: str | None,
    build_cfg: BuildConfig,
    options: BuildOptions,
    fetch: Callable[..., Path],
    run_probes: Callable[..., conf_mod.ProbeReport],
    log: Callable[[str], None],
) -> conf_mod.ProbeReport:
    """Steps 2-7 for a Linux cloud image: import the qcow2, cloud-init bake
    qemu-guest-agent + provisioning, wait on cloud-init, probe, seal."""
    # --- 2. Fetch base image ---
    local_image = fetch(
        recipe.base_image, build_cfg.cache_dir,
        refresh=options.refresh_cache, log=log,
    )

    # --- 3. Stage build VM ---
    log("staging build VM")
    bp.create_vm(
        build_vmid, _vm_name(image, version),
        cores=recipe.cores, memory=recipe.memory,
        machine=machine, bios=bios, scsihw=scsihw, bridge=build_cfg.bridge,
    )
    remote_image = bp.upload_image(str(local_image))
    bp.import_disk(build_vmid, remote_image, build_cfg.storage, scsihw=scsihw)
    bp.add_cloudinit_drive(build_vmid, build_cfg.storage)
    if recipe.disk_size:
        bp.resize_disk(build_vmid, "scsi0", recipe.disk_size)

    # --- 4. Bake ---
    log("baking (qemu-guest-agent + provisioning)")
    userdata = bake_mod.render_bake_userdata(
        hostname=_vm_name(image, version),
        user=ci_user,
        ssh_pubkey=_read_pubkey(build_cfg.deploy.guest_ssh_pubkey_file),
        provision=recipe.provision,
    )
    bp.set_cloudinit(
        build_vmid, f"axr-build-{build_vmid}.yaml", userdata,
        ipconfig=build_cfg.ipconfig,
    )
    bp.start_vm(build_vmid)

    # --- 5. Wait for bake ---
    # Generous timeouts: unlike a deploy (template already has QGA, just
    # `systemctl start`), the build installs qemu-guest-agent from apt during
    # cloud-init, so the agent doesn't answer until that completes.
    log("waiting for guest agent + cloud-init")
    bp.wait_agent(build_vmid, timeout_s=600)
    budget = manifest_version.first_boot_budget or 600
    bp.wait_cloud_init(build_vmid, timeout_s=max(budget, 900))

    # --- 6. Conformance probes (pre-seal) ---
    probe_report = _probe_and_check(
        bp, build_vmid, provides, recipe, "linux", options, run_probes, log
    )

    # --- 7. Seal ---
    log("sealing (cloud-init clean; reset machine-id + ssh host keys)")
    for cmd in bake_mod.seal_commands():
        bp.guest_exec(build_vmid, cmd, timeout_s=120)
    # Strip the build-time cloud-init (cicustom snippet + ipconfig0) so clones
    # don't inherit it — a pipeline template must be as clean as a hand-built
    # one (CHANGELOG 2026-06-09).
    bp.clear_build_cloudinit(build_vmid, f"axr-build-{build_vmid}.yaml")
    bp.shutdown_vm(build_vmid)
    return probe_report


def _windows_stage_to_seal(
    bp,
    build_vmid: int,
    *,
    image: str,
    version: str,
    recipe,
    manifest_version,
    provides: list[str],
    machine: str,
    bios: str,
    scsihw: str,
    tpm: bool,
    build_cfg: BuildConfig,
    options: BuildOptions,
    fetch: Callable[..., Path],
    run_probes: Callable[..., conf_mod.ProbeReport],
    log: Callable[[str], None],
) -> conf_mod.ProbeReport:
    """Steps 2-7 for a Windows image (docs/SLICE_WINDOWS_AD.md §4): fetch the
    install ISO + virtio-win ISO, stage a UEFI/TPM VM with an empty target disk,
    drive an unattended install from a rendered autounattend.xml (QGA +
    cloudbase-init baked in FirstLogonCommands), wait on the completion marker,
    probe via PowerShell, then sysprep /generalize."""
    win = recipe.windows
    if win is None:
        raise ImageBuildError(
            f"image {image!r} is os_family=windows but its build recipe has no "
            "`windows:` block (docs/SLICE_WINDOWS_AD.md §4)",
            stage="read-inputs",
        )

    # --- 2. Fetch install ISO + virtio-win ISO ---
    log("fetching Windows ISO + virtio-win ISO")
    win_iso = fetch(
        recipe.base_image, build_cfg.cache_dir,
        refresh=options.refresh_cache, log=log,
    )
    virtio_iso = fetch(
        win.virtio_iso, build_cfg.cache_dir,
        refresh=options.refresh_cache, log=log,
    )

    # --- 3. Stage build VM (UEFI + TPM + empty virtio-scsi disk) ---
    log("staging Windows build VM")
    bp.create_windows_vm(
        build_vmid, _vm_name(image, version),
        cores=recipe.cores, memory=recipe.memory,
        machine=machine, bios=bios, scsihw=scsihw, bridge=build_cfg.bridge,
        storage=build_cfg.storage, disk_size=recipe.disk_size, tpm=tpm,
    )
    install_volid = bp.upload_iso(str(win_iso))
    virtio_volid = bp.upload_iso(str(virtio_iso))

    # --- 4. Bake (render autounattend, attach install media) ---
    log("rendering autounattend + attaching install media")
    autounattend = bakewin_mod.render_autounattend(
        hostname=_vm_name(image, version), win=win, provision=recipe.provision,
    )
    unattend_volid, unattend_remote = bp.make_autounattend_iso(
        build_vmid, autounattend
    )
    bp.attach_windows_media(
        build_vmid,
        install_iso_volid=install_volid,
        virtio_iso_volid=virtio_volid,
        unattend_iso_volid=unattend_volid,
    )
    bp.start_vm(build_vmid)
    # UEFI "Press any key to boot from CD or DVD": an unattended VM has no one to
    # press it, so OVMF falls through to the empty disk and Setup never starts
    # (live-verify, dev45 2026-06-16). Burst keypresses across the prompt window.
    bp.press_boot_key(build_vmid)

    # --- 5. Wait for Setup + guest agent + FirstLogonCommands ---
    # Windows Setup installs, reboots several times, then runs the QGA +
    # cloudbase-init + provisioning FirstLogonCommands and touches the marker.
    log("waiting for Windows Setup + guest agent + first-logon")
    budget = manifest_version.first_boot_budget or 1200
    bp.wait_agent(build_vmid, timeout_s=max(budget, 1800))
    bp.wait_windows_ready(build_vmid, timeout_s=max(budget * 2, 2400))

    # --- 6. Conformance probes (PowerShell, pre-seal) ---
    probe_report = _probe_and_check(
        bp, build_vmid, provides, recipe, "windows", options, run_probes, log
    )

    # --- 7. Seal (sysprep generalizes AND shuts the VM down) ---
    # Stage a clone-side OOBE unattend FIRST and seal with /unattend: — without it
    # the sysprepped clone halts at the OOBE "Hi there" screen and never specializes
    # (live-verify dev45 2026-06-17, the W2a blocker; docs/SLICE_WINDOWS_AD.md §6.1).
    log("staging clone OOBE unattend + SetupComplete.cmd + sealing (sysprep /generalize /oobe /unattend:)")
    sysprep_unattend = bakewin_mod.render_sysprep_unattend(win=win)
    unattend_path = bp.stage_sysprep_unattend(build_vmid, sysprep_unattend)
    # SetupComplete.cmd runs once as SYSTEM at the end of Setup (no logon needed) and
    # drops the os-ready marker — decoupled from AutoLogon/cloudbase-init, which never
    # ran on the clone (W2a blocker, live-verify dev45 2026-06-17).
    bp.stage_setup_complete(
        build_vmid, bakewin_mod.render_setup_complete_cmd(win=win)
    )
    bp.sysprep_seal(
        build_vmid,
        bakewin_mod.windows_sysprep_command(unattend_path=unattend_path),
    )
    # VM is stopped now; strip the install media so clones don't re-run Setup.
    bp.detach_windows_media(build_vmid, unattend_iso_remote=unattend_remote)
    return probe_report


def build_image(
    image: str,
    *,
    images_root: str | Path,
    build_cfg: BuildConfig,
    options: BuildOptions | None = None,
    bp: BuildProxmox | None = None,
    fetch: Callable[..., Path] = fetch_base_image,
    run_probes: Callable[..., conf_mod.ProbeReport] = conf_mod.run_probes,
    smoke_test: Callable[..., conf_mod.SmokeReport] = conf_mod.smoke_test,
    log: Callable[[str], None] = _noop,
) -> BuildReport:
    """Build a Proxmox template for `image` from its upstream image."""
    options = options or BuildOptions()

    # --- 1. Read inputs --------------------------------------------------
    registry = load_registry(root=images_root)
    manifest = registry.get_manifest(image)
    if manifest is None or manifest.image is None:
        raise ImageBuildError(
            f"no manifest for image {image!r} under {images_root}", stage="read-inputs"
        )
    binding = registry.get_binding(image, "proxmox")
    if binding is None:
        raise ImageBuildError(
            f"no proxmox binding for image {image!r}", stage="read-inputs"
        )

    version = options.version
    if version is None:
        cur = manifest.current_version()
        version = cur.id if cur else None
    if not version:
        raise ImageBuildError(
            f"could not resolve a version to build for {image!r}; "
            "pass --version", stage="read-inputs",
        )

    manifest_version = manifest.get_version(version)
    binding_version = binding.get_version(version)
    if manifest_version is None:
        raise ImageBuildError(
            f"manifest {image!r} has no version {version!r}", stage="read-inputs"
        )
    if binding_version is None:
        raise ImageBuildError(
            f"binding {image!r} has no version {version!r}", stage="read-inputs"
        )

    recipe = recipe_from_binding_version(binding_version)
    provides = list(manifest_version.provides)
    os_family = (manifest.image.os.family if manifest.image.os else None) or "linux"
    is_windows = os_family == "windows"

    hw = binding_version.hardware
    machine = (hw.machine if hw else None) or ("q35" if is_windows else "pc")
    bios = (hw.bios if hw else None) or ("ovmf" if is_windows else "seabios")
    scsihw = (hw.scsi_controller if hw else None) or (
        "virtio-scsi-single" if is_windows else "virtio-scsi-pci"
    )
    tpm_enabled = bool(hw and hw.tpm and hw.tpm.enabled)
    ci_user = (binding_version.cloud_init.user if binding_version.cloud_init else None)

    binding_file = binding_io.binding_path_for(images_root, image)
    log(f"building {image}@{version} (os={os_family}, provides={provides})")

    bp = bp or BuildProxmox(build_cfg)

    # --- VMID ------------------------------------------------------------
    build_vmid = options.build_vmid or bp.next_vmid()
    # Guard BEFORE the try-block: never clobber a pre-existing VMID. If we
    # entered the build and create_vm failed on a collision, the failure
    # cleanup below would `qm destroy` whatever already lives there — which,
    # for an operator-supplied --build-vmid, could be an unrelated VM.
    if bp.vm_exists(build_vmid):
        raise ImageBuildError(
            f"build vmid {build_vmid} already exists on {build_cfg.node}; "
            "pick another --build-vmid or remove the existing VM first",
            stage="stage",
        )
    log(f"build vmid = {build_vmid} on node {build_cfg.node}")

    prior_vmid: int | None = None
    binding_written = False
    probe_report: conf_mod.ProbeReport | None = None
    smoke_report: conf_mod.SmokeReport | None = None

    try:
        # --- 2-7. Stage / bake / wait / probe / seal (OS-family-specific) ---
        if is_windows:
            probe_report = _windows_stage_to_seal(
                bp, build_vmid, image=image, version=version, recipe=recipe,
                manifest_version=manifest_version, provides=provides,
                machine=machine, bios=bios, scsihw=scsihw, tpm=tpm_enabled,
                build_cfg=build_cfg, options=options, fetch=fetch,
                run_probes=run_probes, log=log,
            )
        else:
            probe_report = _linux_stage_to_seal(
                bp, build_vmid, image=image, version=version, recipe=recipe,
                manifest_version=manifest_version, provides=provides,
                machine=machine, bios=bios, scsihw=scsihw, ci_user=ci_user,
                build_cfg=build_cfg, options=options, fetch=fetch,
                run_probes=run_probes, log=log,
            )

        # --- 8. Templatize + write binding ------------------------------
        log("converting to template")
        bp.templatize(build_vmid)
        prior_vmid = binding_io.read_template_vmid(binding_file, version)
        binding_io.write_template_vmid(binding_file, version, build_vmid)
        binding_written = True
        log(
            f"binding {binding_file} updated: template_vmid "
            f"{prior_vmid} -> {build_vmid}"
        )

        # --- 9. Smoke-test ----------------------------------------------
        if is_windows:
            # The smoke-test deploys through the planner+adapter, whose
            # wait-os-ready is Linux cloud-init only until W1; a Windows clone
            # would never report ready. Skip until W1 lands (the §3 M1 interlock).
            log(
                "smoke-test skipped: the windows smoke-test needs W1 "
                "(wait-os-ready windows branch) — docs/SLICE_WINDOWS_AD.md §3 M1"
            )
        elif options.skip_smoke:
            log("smoke-test skipped (--skip-smoke)")
        else:
            log("running one-host deploy smoke-test")
            smoke_report = smoke_test(
                build_cfg, images_root, image, version,
                bp=bp, os_family=os_family, log=log, build_vmid=build_vmid,
            )
            if not smoke_report.ok:
                if not options.ignore_conformance:
                    raise ImageBuildError(
                        f"smoke-test failed: {smoke_report.detail} "
                        f"({smoke_report.ops_ok}/{smoke_report.ops_total} ops ok)",
                        stage="conformance",
                    )
                log(
                    f"WARNING: smoke-test failed ({smoke_report.detail}); "
                    "proceeding (--ignore-conformance)"
                )

    except Exception as exc:  # noqa: BLE001
        # Unwind: revert the binding edit, then destroy the transient build VM.
        #
        # `prior_vmid` doubles as "was there a prior build": for a NEW image's
        # first-ever build there is nothing to revert TO, so the old guard
        # (`prior_vmid is not None`) skipped the unwind entirely while the
        # build VM was destroyed below anyway — leaving the binding pointing
        # at a `template_vmid` for a VM that no longer exists, looking valid
        # in a diff (finding #1, code-review 2026-07-07). Branch on
        # `prior_vmid` instead of gating on it: revert when there was a prior
        # vmid, CLEAR the key entirely when there wasn't.
        if binding_written:
            try:
                if prior_vmid is not None:
                    binding_io.write_template_vmid(binding_file, version, prior_vmid)
                    log(f"reverted binding template_vmid back to {prior_vmid}")
                else:
                    binding_io.clear_template_vmid(binding_file, version)
                    log("cleared binding template_vmid (first build, no prior)")
            except ImageBuildError as e:
                log(f"WARNING: failed to revert binding: {e}")
        if not options.keep_build_vm:
            try:
                bp.destroy_vm(build_vmid)
                log(f"destroyed build vm {build_vmid}")
            except Exception as e:  # noqa: BLE001
                log(f"WARNING: failed to destroy build vm {build_vmid}: {e}")
        else:
            log(f"keeping build vm {build_vmid} (--keep-build-vm)")
        if isinstance(exc, ImageBuildError):
            raise
        raise ImageBuildError(str(exc), stage="build") from exc

    return BuildReport(
        image=image,
        version=version,
        template_vmid=build_vmid,
        prior_template_vmid=prior_vmid,
        binding_file=str(binding_file),
        probe_report=probe_report,
        smoke_report=smoke_report,
        conformance_ignored=options.ignore_conformance,
    )
