"""BuildProxmox — build-scope transport for the image build pipeline.

Reuses the adapter's transports (docs/IMAGE_BUILD.md §8: "Reuses the adapter's
REST client, SSH backend, and config loading"):

  - REST (`axrange.adapter_proxmox.rest.RestClient`) for `/cluster/nextid` and
    the qemu-guest-agent ping/exec channel — the same channel the deploy
    adapter's wait-guest-agent / wait-os-ready use (adapter_proxmox/rest.py).
  - SSH (`axrange.adapter_proxmox.ssh.run_ssh` / `scp_to_remote`) for the `qm`
    build-VM lifecycle: create, importdisk, set, start, template, destroy. The
    deploy adapter has no clean REST surface for `qm importdisk` / `qm
    template`, and these need the broader build SSH identity (root) anyway —
    docs/IMAGE_BUILD.md §5.2.

Why `qm` over SSH rather than REST for VM-create/import/template: the same
forced-SSH reasoning the adapter uses for snippet upload and `ovs-vsctl`
(docs/ADAPTER.md §3). `qm importdisk` in particular has no portable REST
equivalent across the PVE 7.x/8.x range the slice targets, and shelling `qm`
mirrors exactly what the 2026-05-29 manual template-rebuild runbook did by hand.

All transport callables (`ssh_runner`, `content_scp_runner`, `file_scp_runner`,
`rest_client`) are injectable so tests drive the whole build flow without a
cluster — the same client-stub approach as tests/test_adapter_proxmox.py.
"""
from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestAgent, GuestExecResult
from axrange.adapter_proxmox.rest import RestClient
from axrange.adapter_proxmox.ssh import run_ssh, scp_to_remote
from axrange.imagebuild.bake_windows import WINDOWS_BUILD_COMPLETE_MARKER
from axrange.imagebuild.config import BuildConfig
from axrange.imagebuild.errors import ImageBuildError
from axrange.planner.executor import AdapterError, AdapterTimeout

# `GuestExecResult` re-exported from adapter_proxmox.guest_agent (LES-025 reuse,
# code-review finding #4, 2026-07-07) — imagebuild used to define its own
# byte-for-byte identical dataclass; callers that `from
# axrange.imagebuild.proxmox_build import GuestExecResult` keep working
# unchanged since it's the same type either way.
__all__ = ["BuildProxmox", "GuestExecResult"]


def _scp_local_file(cfg: AdapterConfig, local_path: str, remote_path: str) -> None:
    """Direct binary file scp (the adapter's scp_to_remote is content-based, so
    it can't carry a multi-GB qcow2)."""
    opts = [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
    ]
    argv = ["scp", *opts]
    if cfg.ovs_ssh_key:
        argv += ["-i", cfg.ovs_ssh_key]
    argv += [local_path, f"{cfg.ovs_ssh_user}@{cfg.ovs_ssh_host}:{remote_path}"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=3600, check=False)
    if proc.returncode != 0:
        raise ImageBuildError(
            f"scp {local_path} -> {cfg.ovs_ssh_host}:{remote_path}: "
            f"exit {proc.returncode}; {proc.stderr.strip()[:200]}",
            stage="stage",
        )


class BuildProxmox:
    """Build-scope Proxmox operations over REST + SSH."""

    def __init__(
        self,
        build_cfg: BuildConfig,
        *,
        rest_client: RestClient | None = None,
        ssh_runner=run_ssh,
        content_scp_runner=scp_to_remote,
        file_scp_runner=_scp_local_file,
    ) -> None:
        self.cfg = build_cfg
        self.node = build_cfg.node
        self._ssh_cfg: AdapterConfig = build_cfg.ssh_config()
        self._rest = rest_client or RestClient(build_cfg.rest_config())
        self._run_ssh = ssh_runner
        self._content_scp = content_scp_runner
        self._file_scp = file_scp_runner
        # QGA channel (LES-025 reuse): the same GuestAgent the deploy adapter
        # and config_exec use for ping/exec/write_file, rather than
        # reimplementing the poll loops and PVE REST shape here. Linux
        # `wait_agent`/`guest_exec` are OS-agnostic (argv passed straight
        # through), so one shared instance covers both build flows; the
        # Windows-only file-write helpers below use the `windows` flavor so
        # `.write_file` takes the PowerShell/base64 branch.
        self._guest = GuestAgent(self._rest, self.node)
        self._win_guest = GuestAgent(self._rest, self.node, os_family="windows")

    # ---- low-level helpers -------------------------------------------------

    def _ssh(self, remote_cmd: str, *, timeout_s: int = 60, stage: str = "build") -> str:
        try:
            return self._run_ssh(self._ssh_cfg, remote_cmd, timeout_s=timeout_s)
        except (AdapterError, AdapterTimeout) as e:
            raise ImageBuildError(str(e), stage=stage) from e

    def _qm(self, *args: Any, timeout_s: int = 60, stage: str = "build") -> str:
        cmd = "qm " + " ".join(shlex.quote(str(a)) for a in args)
        return self._ssh(cmd, timeout_s=timeout_s, stage=stage)

    def _rest_call(
        self, method: str, path: str, *, params=None, body=None, stage: str = "build"
    ) -> Any:
        try:
            return self._rest.request(method, path, params=params, body=body)
        except (AdapterError, AdapterTimeout) as e:
            raise ImageBuildError(str(e), stage=stage) from e

    # ---- VMID allocation ---------------------------------------------------

    def next_vmid(self) -> int:
        data = self._rest_call("GET", "/cluster/nextid", stage="stage")
        try:
            return int(data)
        except (TypeError, ValueError) as e:
            raise ImageBuildError(
                f"/cluster/nextid returned non-numeric {data!r}", stage="stage"
            ) from e

    def vm_exists(self, vmid: int) -> bool:
        try:
            data = self._rest.request(
                "GET", f"/nodes/{self.node}/qemu/{vmid}/status/current"
            )
        except (AdapterError, AdapterTimeout):
            return False
        return bool(data)

    # ---- build VM staging --------------------------------------------------

    def create_vm(
        self,
        vmid: int,
        name: str,
        *,
        cores: int,
        memory: int,
        machine: str,
        bios: str,
        scsihw: str,
        bridge: str,
        ostype: str = "l26",
    ) -> None:
        self._qm(
            "create", vmid,
            "--name", name,
            "--cores", cores,
            "--memory", memory,
            "--machine", machine,
            "--bios", bios,
            "--scsihw", scsihw,
            "--net0", f"virtio,bridge={bridge}",
            "--ostype", ostype,
            "--agent", "enabled=1",
            "--serial0", "socket",
            "--vga", "serial0",
            timeout_s=120,
            stage="stage",
        )

    def upload_image(
        self, local_path: str, *, dest_dir: str = "/var/lib/vz/template/cache"
    ) -> str:
        """scp the cached qcow2 to the node (skipping when an identical copy is
        already there) and return its remote path. A node-side checksum-equal
        copy is reused so repeat builds don't re-transfer multi-GB images."""
        local = Path(local_path)
        remote_path = f"{dest_dir}/{local.name}"
        self._ssh(f"mkdir -p {shlex.quote(dest_dir)}", timeout_s=15, stage="stage")

        local_sha = hashlib.sha256(local.read_bytes()).hexdigest()
        try:
            remote_sha = self._ssh(
                f"sha256sum {shlex.quote(remote_path)} 2>/dev/null | cut -d' ' -f1",
                timeout_s=300,
                stage="stage",
            ).strip()
        except ImageBuildError:
            remote_sha = ""
        if remote_sha == local_sha:
            return remote_path

        self._file_scp(self._ssh_cfg, str(local), remote_path)
        return remote_path

    def import_disk(
        self, vmid: int, remote_image: str, storage: str, *, scsihw: str
    ) -> str:
        """`qm disk import` the cloud image, attach as scsi0, set boot order.

        `qm disk import` is the PVE 8.x form (the old `qm importdisk` is an
        alias that may be removed); confirmed on pxtwin1 (PVE 8.4). Returns the
        imported disk volid. The "Successfully imported disk as '<volid>'" line
        may land on stderr, so we merge streams and parse; on a parse miss we
        fall back to the conventional LVM name `<storage>:vm-<vmid>-disk-0` (the
        lab's infra-lvm / local-lvm lvmthin shape — the import is the VM's first
        disk, so index 0). CONSTRAINT (finding #6, code-review 2026-07-07):
        this fallback is lvm-thin-specific — a `dir`-backed target storage
        names its volid `<storage>:<vmid>/vm-<vmid>-disk-0.<fmt>` instead, so
        the fallback would be WRONG there. It's only a last resort when the
        primary path (parsing the real `qm disk import` output) misses; the
        lab's `build_cfg.storage` is lvm-thin (`local-lvm` / `infra-lvm`) on
        both live-verified clusters. A `dir`-storage build target isn't
        supported by this fallback path — parse-and-fail loud rather than
        guess if that's ever needed."""
        out = self._ssh(
            f"qm disk import {vmid} {shlex.quote(remote_image)} "
            f"{shlex.quote(storage)} 2>&1",
            timeout_s=3600,
            stage="import-disk",
        )
        volid = _parse_imported_volid(out) or f"{storage}:vm-{vmid}-disk-0"
        self._qm("set", vmid, "--scsihw", scsihw, "--scsi0", volid, stage="import-disk")
        self._qm("set", vmid, "--boot", "order=scsi0", stage="import-disk")
        return volid

    def add_cloudinit_drive(self, vmid: int, storage: str) -> None:
        self._qm("set", vmid, "--ide2", f"{storage}:cloudinit", stage="stage")

    def resize_disk(self, vmid: int, disk: str, size: str) -> None:
        # `qm resize` is stable across PVE 7/8; rejects shrink, no-ops same size.
        try:
            self._qm("resize", vmid, disk, size, stage="stage")
        except ImageBuildError as e:
            if "shrink" in str(e).lower() or "not bigger" in str(e).lower():
                return
            raise

    def _assert_local_storage(self, storage: str) -> str:
        """Return the filesystem path backing `storage`, after validating
        it's one this pipeline actually knows how to reach.

        `set_cloudinit` / `upload_iso` / `make_autounattend_iso` all scp
        content to a hardcoded `/var/lib/vz/...` path on the node, then build
        the *returned* PVE volid from a CONFIGURABLE storage name
        (`self.cfg.snippet_storage`, or a caller-supplied override) — the two
        only agree when that storage is `local` (PVE's conventional default,
        whose real path IS `/var/lib/vz`). For any other storage name they
        silently diverge: the file lands on `/var/lib/vz/...` but the volid
        points PVE at a different storage's path, and `qm set
        --cicustom`/attach-media fails to find it (finding #5, code-review
        2026-07-07). Fail loud here instead of writing to a real path that
        doesn't back the volid we're about to hand PVE.

        A future non-local snippet/ISO storage needs this to resolve the
        real path (e.g. `pvesh get /storage/<s>` or `pvesm path`) rather than
        assume `/var/lib/vz`; not implemented since every live-verified
        cluster uses `local`."""
        if storage != "local":
            raise ImageBuildError(
                f"storage {storage!r} is not supported for snippets/ISOs: "
                "the build pipeline hardcodes PVE's `local` storage's "
                "filesystem path (/var/lib/vz), and only `local` is "
                "guaranteed to resolve there. Use `local` (BUILD_SNIPPET_"
                "STORAGE / content_storage), or extend BuildProxmox to "
                "resolve the real path for other storage types.",
                stage="bake",
            )
        return "/var/lib/vz"

    def set_cloudinit(
        self,
        vmid: int,
        snippet_name: str,
        snippet_content: str,
        *,
        ipconfig: str = "ip=dhcp",
    ) -> None:
        base = self._assert_local_storage(self.cfg.snippet_storage)
        snippets_dir = f"{base}/snippets"
        self._ssh(f"mkdir -p {snippets_dir}", timeout_s=15, stage="bake")
        try:
            self._content_scp(
                self._ssh_cfg,
                snippet_content,
                f"{snippets_dir}/{snippet_name}",
                timeout_s=30,
            )
        except (AdapterError, AdapterTimeout) as e:
            raise ImageBuildError(str(e), stage="bake") from e
        self._qm(
            "set", vmid,
            "--cicustom", f"user={self.cfg.snippet_storage}:snippets/{snippet_name}",
            "--ipconfig0", ipconfig,
            stage="bake",
        )

    # ---- run / wait / guest exec ------------------------------------------

    def start_vm(self, vmid: int) -> None:
        self._qm("start", vmid, timeout_s=120, stage="bake")

    def wait_agent(self, vmid: int, *, timeout_s: int = 300) -> None:
        """Poll the guest agent until it answers. Delegates to the shared
        `GuestAgent.wait_ready` (POST, not GET — see
        adapter_proxmox/guest_agent.py / LES-018) instead of reimplementing
        the ping-retry loop here (LES-025 reuse, finding #4)."""
        try:
            self._guest.wait_ready(vmid, timeout_s=timeout_s)
        except AdapterTimeout as e:
            raise ImageBuildError(
                f"guest agent for build vm {vmid} did not answer in "
                f"{timeout_s}s ({e})",
                stage="bake",
            ) from e

    def guest_exec(
        self, vmid: int, argv: list[str], *, timeout_s: int = 300
    ) -> GuestExecResult:
        """Run `argv` in the guest via qemu-guest-agent exec; return exit code +
        captured output. Delegates to the shared `GuestAgent.exec`
        (adapter_proxmox/guest_agent.py) rather than reimplementing the
        exec/exec-status polling here (LES-025 reuse, finding #4) — same
        semantics, same PVE REST shape."""
        try:
            return self._guest.exec(vmid, argv, timeout_s=timeout_s)
        except AdapterError as e:
            raise ImageBuildError(
                f"guest exec {argv!r} on vm {vmid} failed: {e}", stage="bake"
            ) from e
        except AdapterTimeout as e:
            raise ImageBuildError(
                f"guest exec {argv!r} on vm {vmid} did not finish in "
                f"{timeout_s}s ({e})",
                stage="bake",
            ) from e

    def wait_cloud_init(self, vmid: int, *, timeout_s: int = 600) -> None:
        """Block until the guest's cloud-init finishes, THEN assert it
        finished clean. exit 0 == done; exit 2 == cloud-init 'recoverable
        error' (benign warning) — both accepted, per
        adapter_proxmox/rest.py:op_wait_os_ready.

        `status --wait`'s exit code alone is not a correctness signal for the
        BAKE: cloud-init treats a failed `runcmd`/`write_files`/package-install
        step as non-fatal to its own top-level status (still exits 0/2), so a
        provisioning failure (e.g. `apt-get install` of a bad/unavailable
        package in the recipe's `provision:`) silently produces a template
        whose only backstop is the capability probes — and those cover ONLY
        the `provides:` keywords, not arbitrary provisioning (finding #3,
        code-review 2026-07-07). Follow up with a structured status read and
        fail loud on any reported error.
        """
        res = self.guest_exec(
            vmid, ["cloud-init", "status", "--wait"], timeout_s=timeout_s
        )
        if res.exit_code not in (0, 2):
            raise ImageBuildError(
                f"cloud-init on build vm {vmid} exited {res.exit_code}: "
                f"{(res.stdout or res.stderr)[:200]}",
                stage="bake",
            )
        self._assert_cloud_init_clean(vmid)

    def _assert_cloud_init_clean(self, vmid: int) -> None:
        """Fail loud if `cloud-init status --format json` reports any
        `errors[]` — the backstop for what `wait_cloud_init`'s bare exit-code
        check misses (module/runcmd failures are non-fatal to `status
        --wait`, per its docstring above)."""
        import json

        res = self.guest_exec(
            vmid, ["cloud-init", "status", "--format", "json"], timeout_s=60
        )
        if not res.ok:
            # The status query itself failing isn't grounds to fail the
            # build — `status --wait` above already confirmed cloud-init
            # finished; treat an unreadable structured status as
            # "can't confirm clean" rather than a hard failure.
            return
        try:
            data = json.loads(res.stdout or "{}")
        except (ValueError, TypeError):
            return
        errors = data.get("errors") or []
        if errors:
            raise ImageBuildError(
                f"cloud-init on build vm {vmid} reported provisioning "
                f"errors: {errors}",
                stage="bake",
            )

    # ---- seal / template / teardown ---------------------------------------

    def stage_sysprep_unattend(
        self, vmid: int, xml: str,
        *, path: str = r"C:\Windows\System32\Sysprep\axr-unattend.xml",
    ) -> str:
        """Write the clone-side sysprep unattend into the build guest so the seal
        can reference it via `/unattend:` (docs/SLICE_WINDOWS_AD.md §4). Without it
        the sysprepped clone halts at OOBE (live-verify dev45 2026-06-17).
        Delegates to the shared `GuestAgent.write_file` Windows branch (LES-025
        reuse, finding #4) — same base64-in-command delivery (LES-002) this
        method used to hand-roll; `System32\\Sysprep` always exists, no mkdir
        needed. Returns the path."""
        try:
            self._win_guest.write_file(vmid, path, xml)
        except AdapterError as e:
            raise ImageBuildError(
                f"staging sysprep unattend on vm {vmid} failed: {e}",
                stage="seal",
            ) from e
        return path

    def stage_setup_complete(
        self, vmid: int, content: str,
        *, path: str = r"C:\Windows\Setup\Scripts\SetupComplete.cmd",
    ) -> str:
        """Write a `SetupComplete.cmd` into the build guest before sysprep so the
        sealed clone runs it once as SYSTEM at the end of Setup (the W1 os-ready
        signal, decoupled from logon/cloudbase-init — docs/SLICE_WINDOWS_AD.md §4 /
        §6.1). Creates `C:\\Windows\\Setup\\Scripts` if absent (a plain guest_exec —
        `GuestAgent.write_file` only writes bytes, it doesn't mkdir), then
        writes the file with its exact bytes (CRLF preserved) via the shared
        `GuestAgent.write_file` Windows branch (LES-025 reuse, finding #4).
        Returns the path."""
        parent = path.rsplit("\\", 1)[0]
        mkdir_ps = (
            f"if(-not(Test-Path '{parent}')){{"
            f"New-Item -ItemType Directory -Path '{parent}' -Force | Out-Null}}"
        )
        res = self.guest_exec(
            vmid,
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", mkdir_ps],
            timeout_s=60,
        )
        if not res.ok:
            raise ImageBuildError(
                f"creating {parent} on vm {vmid} failed "
                f"(exit {res.exit_code}): {(res.stderr or res.stdout)[:200]}",
                stage="seal",
            )
        try:
            self._win_guest.write_file(vmid, path, content)
        except AdapterError as e:
            raise ImageBuildError(
                f"staging SetupComplete.cmd on vm {vmid} failed: {e}",
                stage="seal",
            ) from e
        return path

    def shutdown_vm(self, vmid: int, *, timeout_s: int = 120) -> None:
        try:
            self._qm(
                "shutdown", vmid, "--timeout", timeout_s,
                timeout_s=timeout_s + 30, stage="seal",
            )
        except ImageBuildError:
            # ACPI shutdown can hang on a minimal cloud image; force it.
            self._qm("stop", vmid, timeout_s=60, stage="seal")

    def clear_build_cloudinit(self, vmid: int, snippet_name: str | None = None) -> None:
        """Strip the build-time cloud-init customization from the VM config
        before templatizing.

        `set_cloudinit` injects a `cicustom` snippet (QGA preinstall + bake
        provisioning) and an `ipconfig0`. If left on the template, every clone
        — including manual `qm clone` ones — inherits the build snippet, which
        silently overrides the operator's per-deploy cloud-init (the deploy
        adapter sets its own per-VM `cicustom`, so deployed scenario VMs are
        unaffected, but a hand-cloned VM gets the build's hostname/user/keys).
        A pipeline-built template must be as clean as a hand-built one. Also
        deletes the snippet file so build snippets don't accumulate on the
        cluster. Best-effort: an already-absent key/file is fine.
        """
        try:
            self._qm("set", vmid, "--delete", "cicustom,ipconfig0", stage="seal")
        except ImageBuildError:
            pass
        if snippet_name:
            try:
                self._ssh(
                    f"rm -f /var/lib/vz/snippets/{shlex.quote(snippet_name)}",
                    timeout_s=15, stage="seal",
                )
            except ImageBuildError:
                pass

    def templatize(self, vmid: int) -> None:
        self._qm("template", vmid, timeout_s=120, stage="templatize")

    def destroy_vm(self, vmid: int, *, purge: bool = True) -> None:
        """Best-effort teardown: stop (ignore errors), then destroy."""
        try:
            self._qm("stop", vmid, timeout_s=60, stage="cleanup")
        except ImageBuildError:
            pass
        args: list[Any] = ["destroy", vmid]
        if purge:
            args.append("--purge")
        self._qm(*args, timeout_s=120, stage="cleanup")

    def delete_sdn(self, zone: str, vnet: str) -> None:
        """Best-effort removal of the throwaway SDN vnet + zone the smoke-test
        deploy created (SDN-native, 2026-06-03), followed by a cluster-wide
        apply so the change materializes. Delete the vnet before its zone."""
        for path in (f"/cluster/sdn/vnets/{vnet}", f"/cluster/sdn/zones/{zone}"):
            try:
                self._rest.request("DELETE", path)
            except (AdapterError, AdapterTimeout):
                pass
        try:
            self._rest.request("PUT", "/cluster/sdn")
        except (AdapterError, AdapterTimeout):
            pass

    def delete_bridge(self, bridge: str) -> None:
        """Best-effort removal of a throwaway OVS bridge created during the
        smoke-test deploy (adapter creates via POST /nodes/<node>/network)."""
        try:
            self._rest.request("DELETE", f"/nodes/{self.node}/network/{bridge}")
            self._rest.request("PUT", f"/nodes/{self.node}/network")
        except (AdapterError, AdapterTimeout):
            try:
                self._ssh(
                    f"ovs-vsctl --if-exists del-br {shlex.quote(bridge)}",
                    timeout_s=30,
                )
            except ImageBuildError:
                pass

    # ---- Windows build-VM staging (docs/SLICE_WINDOWS_AD.md W0) ------------
    #
    # The Linux flow imports a cloud qcow2 + bakes via cloud-init. Windows has
    # no generic cloud image: it installs from an ISO driven by an
    # autounattend.xml, with virtio-win drivers injected, onto a UEFI/GPT
    # virtio-scsi disk. These methods are the Windows analogs of create_vm /
    # import_disk / set_cloudinit / wait_cloud_init / seal. Implemented +
    # argv-tested; end-to-end live-verify is the W0 cluster step (needs the ISOs).

    def create_windows_vm(
        self,
        vmid: int,
        name: str,
        *,
        cores: int,
        memory: int,
        machine: str,
        bios: str,
        scsihw: str,
        bridge: str,
        storage: str,
        disk_size: str | None,
        tpm: bool = True,
    ) -> None:
        """Create a UEFI Windows build VM: an EFI disk, an empty virtio-scsi
        target disk (Setup installs onto it), a virtio NIC, the guest agent
        enabled, and (optionally) a vTPM. `--ostype win11` covers Server 2022."""
        disk_gb = re.sub(r"[^0-9].*$", "", str(disk_size or "48")) or "48"
        self._qm(
            "create", vmid,
            "--name", name,
            "--cores", cores,
            "--memory", memory,
            "--machine", machine,        # q35
            "--bios", bios,              # ovmf
            "--efidisk0", f"{storage}:1,efitype=4m,pre-enrolled-keys=0",
            "--scsihw", scsihw,          # virtio-scsi-single
            "--scsi0", f"{storage}:{disk_gb}",
            "--net0", f"virtio,bridge={bridge}",
            "--ostype", "win11",
            "--cpu", "host",
            "--agent", "enabled=1",
            timeout_s=120,
            stage="stage",
        )
        if tpm:
            self._qm(
                "set", vmid, "--tpmstate0", f"{storage}:1,version=v2.0",
                stage="stage",
            )

    def upload_iso(self, local_path: str, *, content_storage: str | None = None) -> str:
        """scp an ISO to the node's iso storage; return its PVE volid
        (`<storage>:iso/<file>`). Reuses upload_image's checksum-skip transfer."""
        storage = content_storage or self.cfg.snippet_storage
        self._assert_local_storage(storage)
        remote = self.upload_image(local_path, dest_dir="/var/lib/vz/template/iso")
        return f"{storage}:iso/{Path(remote).name}"

    def make_autounattend_iso(
        self, vmid: int, xml_content: str, *, content_storage: str | None = None
    ) -> tuple[str, str]:
        """Write the rendered autounattend.xml to the node and pack it into a
        tiny ISO (Windows Setup auto-searches removable media for
        autounattend.xml). Returns (volid, remote_iso_path)."""
        storage = content_storage or self.cfg.snippet_storage
        self._assert_local_storage(storage)
        workdir = f"/tmp/axr-unattend-{vmid}"
        iso_name = f"axr-unattend-{vmid}.iso"
        iso_path = f"/var/lib/vz/template/iso/{iso_name}"
        self._ssh(
            f"mkdir -p {shlex.quote(workdir)} /var/lib/vz/template/iso",
            timeout_s=15, stage="bake",
        )
        try:
            self._content_scp(
                self._ssh_cfg, xml_content,
                f"{workdir}/autounattend.xml", timeout_s=30,
            )
        except (AdapterError, AdapterTimeout) as e:
            raise ImageBuildError(str(e), stage="bake") from e
        # genisoimage ships via cdrkit/xorriso on PVE; -J Joliet, -r Rock Ridge.
        # The live bring-up confirms the tool name (xorriso is the fallback).
        self._ssh(
            f"genisoimage -quiet -J -r -V AUTOUNATTEND -o "
            f"{shlex.quote(iso_path)} {shlex.quote(workdir)}",
            timeout_s=60, stage="bake",
        )
        return f"{storage}:iso/{iso_name}", iso_path

    def attach_windows_media(
        self,
        vmid: int,
        *,
        install_iso_volid: str,
        virtio_iso_volid: str,
        unattend_iso_volid: str,
    ) -> None:
        """Attach the three install media as CD-ROMs and boot the Windows ISO
        first. virtio on ide0 (low) to bias its drive letter early — the exact
        letter is confirmed at live bring-up (bake_windows.DEFAULT_VIRTIO_LETTER)."""
        self._qm("set", vmid, "--ide2", f"{install_iso_volid},media=cdrom", stage="bake")
        self._qm("set", vmid, "--ide0", f"{virtio_iso_volid},media=cdrom", stage="bake")
        self._qm("set", vmid, "--ide3", f"{unattend_iso_volid},media=cdrom", stage="bake")
        self._qm("set", vmid, "--boot", "order=ide2;scsi0", stage="bake")

    def press_boot_key(
        self, vmid: int, *, presses: int = 12, interval_s: int = 1, key: str = "ret"
    ) -> None:
        """Satisfy the UEFI "Press any key to boot from CD or DVD" prompt on the
        Windows install ISO. An unattended VM has no one to press it, so OVMF
        falls through past the CD to the empty disk ("No bootable option or
        device was found") and Setup never starts (live-verify, dev45 2026-06-16).
        Send a keypress burst across the early-boot window (~0-25s) so one lands
        in the ~5s prompt. **First boot only** — later reboots must NOT be keyed
        (they should boot the now-installed disk, not re-run Setup from CD; the
        burst is long over by the time Setup's first reboot happens). Best-effort:
        stray keys during WinPE load are ignored, and a monitor hiccup on any
        single send is non-fatal."""
        for _ in range(presses):
            try:
                self._ssh(
                    f"printf 'sendkey {key}\\n' | qm monitor {vmid}",
                    timeout_s=15, stage="bake",
                )
            except ImageBuildError:
                pass
            time.sleep(interval_s)

    def wait_windows_ready(
        self,
        vmid: int,
        *,
        marker: str = WINDOWS_BUILD_COMPLETE_MARKER,
        timeout_s: int = 3600,
        poll_s: int = 15,
    ) -> None:
        """Poll (over QGA) until FirstLogonCommands write the completion marker.
        Windows has no `cloud-init status --wait`; this is its analog. Call
        wait_agent first (QGA only answers once guest tools install, late in
        Setup)."""
        ps = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"if (Test-Path '{marker}') {{ exit 0 }} else {{ exit 1 }}",
        ]
        deadline = time.time() + timeout_s
        last = "marker absent"
        while time.time() < deadline:
            try:
                if self.guest_exec(vmid, ps, timeout_s=60).exit_code == 0:
                    return
            except ImageBuildError as e:
                last = str(e)
            time.sleep(poll_s)
        raise ImageBuildError(
            f"windows build vm {vmid} did not signal complete ({marker}) in "
            f"{timeout_s}s ({last})",
            stage="bake",
        )

    def wait_vm_stopped(
        self, vmid: int, *, timeout_s: int = 900, poll_s: int = 10
    ) -> None:
        """Block until the VM reports `stopped` (sysprep /shutdown powers it off
        asynchronously)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                data = self._rest.request(
                    "GET", f"/nodes/{self.node}/qemu/{vmid}/status/current"
                )
                if isinstance(data, dict) and data.get("status") == "stopped":
                    return
            except (AdapterError, AdapterTimeout):
                pass
            time.sleep(poll_s)
        raise ImageBuildError(
            f"build vm {vmid} did not stop within {timeout_s}s after sysprep",
            stage="seal",
        )

    def sysprep_seal(self, vmid: int, argv: list[str], *, stop_timeout_s: int = 900) -> None:
        """Run sysprep /generalize (the Windows seal). sysprep itself shuts the
        VM down, so the guest-exec call may return as QGA dies mid-response —
        that TRANSPORT error (an ImageBuildError raised by guest_exec itself,
        e.g. a timeout waiting on exec-status) is benign; we then wait for the
        VM to actually stop.

        But if guest_exec DOES return a result (QGA answered before dying),
        its exit code must be checked: sysprep can fail to /generalize (a bad
        answer file, pending Windows Update, an already-generalized image) and
        STILL power the VM off on its way out — `guest_exec` itself never
        raises on a non-zero exit, so a silently-failed generalize was
        becoming a template whose clones all share one SID (finding #2,
        code-review 2026-07-07), breaking AD domain-join on more than one
        clone. Capture + surface sysprep's stderr so the failure is
        diagnosable rather than only showing up as a downstream stop-timeout.
        """
        result = None
        try:
            result = self.guest_exec(vmid, argv, timeout_s=900)
        except ImageBuildError:
            pass
        if result is not None and not result.ok:
            raise ImageBuildError(
                f"sysprep /generalize on vm {vmid} failed (exit "
                f"{result.exit_code}): {(result.stderr or result.stdout)[:500]}",
                stage="seal",
            )
        self.wait_vm_stopped(vmid, timeout_s=stop_timeout_s)

    def detach_windows_media(
        self, vmid: int, *, unattend_iso_remote: str | None = None
    ) -> None:
        """Strip the install CD-ROMs + reset boot to the disk before templatize,
        so clones don't re-run Setup; delete the generated autounattend ISO.
        Best-effort, mirroring clear_build_cloudinit."""
        for slot in ("ide2", "ide0", "ide3"):
            try:
                self._qm("set", vmid, "--delete", slot, stage="seal")
            except ImageBuildError:
                pass
        try:
            self._qm("set", vmid, "--boot", "order=scsi0", stage="seal")
        except ImageBuildError:
            pass
        if unattend_iso_remote:
            try:
                self._ssh(
                    f"rm -f {shlex.quote(unattend_iso_remote)}",
                    timeout_s=15, stage="seal",
                )
            except ImageBuildError:
                pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_IMPORTED_RE = re.compile(r"imported disk as '?(?:unused\d+:)?([^'\s]+)'?", re.IGNORECASE)


def _parse_imported_volid(output: str) -> str | None:
    m = _IMPORTED_RE.search(output or "")
    return m.group(1) if m else None
