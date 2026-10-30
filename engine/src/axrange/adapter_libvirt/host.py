"""HostBackend -- commands on the KVM host.

`adapters/libvirt/SPEC.md`: the host backend runs "commands on the KVM host:
local subprocess when the URI is local, SSH otherwise". It serves three ops
and the teardown: the NoCloud seed ISO build (`configure-cloud-init`), the
keyed gretap (`attach-gre-tunnel`) and the per-tap `tc` mirror hook
(`install-ovs-mirror`) -- the same `ip link`/`tc`/`ethtool` lines the
Proxmox SSH backend issues, built by the pure builders in
`axrange.adapter_proxmox.ssh` so the two platforms cannot drift.

`runner` is the one subprocess seam (argv in, `(rc, stdout, stderr)` out);
the suite injects a recorder, production uses `subprocess.run`. File
delivery goes over the command's stdin (`cat > path`), local and remote
alike, so no `scp`, no temp file on the orchestrator, and the content never
touches argv.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from axrange.adapter_proxmox.ssh import _SSH_OPTS
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = ["HostBackend", "ISO_TOOLS", "Runner", "iso_build_cmd"]

Runner = Callable[..., tuple[int, str, str]]

#: Probe order for the seed-ISO builder, each with the flags that produce a
#: `cidata` ISO9660+Joliet+RockRidge image cloud-init's NoCloud datasource
#: mounts. `xorriso -as mkisofs` speaks the mkisofs flag set.
ISO_TOOLS: tuple[tuple[str, str], ...] = (
    ("genisoimage", "genisoimage -quiet -output {iso} -volid cidata -joliet -rock {dir}"),
    ("xorriso", "xorriso -as mkisofs -quiet -output {iso} -volid cidata -joliet -rock {dir}"),
    ("mkisofs", "mkisofs -quiet -output {iso} -volid cidata -joliet -rock {dir}"),
)


def iso_build_cmd(tool: str, iso_path: str, src_dir: str) -> str:
    for name, tmpl in ISO_TOOLS:
        if name == tool:
            return tmpl.format(iso=shlex.quote(iso_path), dir=shlex.quote(src_dir))
    raise AdapterError(f"unknown ISO tool {tool!r}", error_class="adapter-config")


def _subprocess_runner(argv: list[str], *, input: str | None = None, timeout_s: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv, input=input, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise AdapterTimeout(f"host command timed out after {timeout_s}s: {argv[-1][:80]!r}") from e
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class HostBackend:
    """Shell access to the KVM host, local or over SSH per the URI."""

    def __init__(self, cfg: Any, *, runner: Optional[Runner] = None) -> None:
        self.cfg = cfg
        self._runner: Runner = runner or _subprocess_runner
        self._iso_tool: str | None = None

    @property
    def remote(self) -> bool:
        return bool(getattr(self.cfg, "is_remote", False))

    @property
    def label(self) -> str:
        return f"{self.cfg.host_ssh_user}@{self.cfg.host_ssh_host}" if self.remote else "localhost"

    def _argv(self, cmd: str) -> list[str]:
        if not self.remote:
            return ["sh", "-c", cmd]
        if not self.cfg.host_ssh_host:
            raise AdapterError(
                "HOST_SSH_HOST not set for a remote LIBVIRT_URI", error_class="adapter-config",
            )
        argv = ["ssh", *list(_SSH_OPTS)]
        if self.cfg.host_ssh_key:
            argv.extend(["-i", self.cfg.host_ssh_key])
        user = self.cfg.host_ssh_user or "root"
        argv.append(f"{user}@{self.cfg.host_ssh_host}")
        argv.append(cmd)
        return argv

    def run(self, cmd: str, *, timeout_s: int = 60, check: bool = True, input: str | None = None) -> str:
        """Run `cmd` on the host; stdout. `check=True` raises `AdapterError`
        on non-zero exit (`AdapterTimeout` on the time budget)."""
        rc, out, err = self._runner(self._argv(cmd), input=input, timeout_s=timeout_s)
        if check and rc != 0:
            raise AdapterError(
                f"host {self.label} {cmd[:120]!r}: exit {rc}; stderr={err.strip()[:200]}",
                error_class="host-cmd-failed",
            )
        return out

    def put_file(self, content: str, path: str, *, mode: str | None = None, timeout_s: int = 30) -> None:
        """Write `content` at `path` on the host (parents created). Local or
        remote, the bytes ride the command's stdin (`cat > path`): one code
        path, nothing on argv, and the suite's recording runner sees every
        file the adapter delivers."""
        q = shlex.quote(path)
        parent = shlex.quote(str(Path(path).parent))
        cmd = f"mkdir -p {parent} && cat > {q}"
        if mode:
            cmd += f" && chmod {shlex.quote(mode)} {q}"
        self.run(cmd, input=content, timeout_s=timeout_s)

    def remove_files(self, *paths: str, timeout_s: int = 20) -> None:
        if not paths:
            return
        self.run("rm -f " + " ".join(shlex.quote(p) for p in paths), timeout_s=timeout_s)

    # ---- seed ISO -----------------------------------------------------
    def iso_tool(self) -> str:
        """The first of `ISO_TOOLS` present on the host, probed once."""
        if self._iso_tool is None:
            probe = " || ".join(f"command -v {name}" for name, _ in ISO_TOOLS)
            out = self.run(f"({probe}) 2>/dev/null || true", timeout_s=15).strip().splitlines()
            found = Path(out[0]).name if out and out[0].strip() else ""
            if found not in {n for n, _ in ISO_TOOLS}:
                raise AdapterError(
                    f"no ISO builder on {self.label}: install one of "
                    + ", ".join(n for n, _ in ISO_TOOLS)
                    + " (the NoCloud seed for cloud-init is an ISO9660 image)",
                    error_class="host-tool-missing",
                )
            self._iso_tool = found
        return self._iso_tool

    def build_seed_iso(self, iso_path: str, files: dict[str, str], *, timeout_s: int = 60) -> str:
        """Write `files` (name -> text) into a scratch dir on the host, build
        the `cidata` ISO at `iso_path` with the probed tool, remove the
        scratch dir. Returns the tool used."""
        tool = self.iso_tool()
        tmp = self.run("mktemp -d /tmp/axr-seed.XXXXXX", timeout_s=15).strip()
        if not tmp:
            raise AdapterError(f"mktemp on {self.label} returned nothing", error_class="host-cmd-failed")
        try:
            for name, text in files.items():
                self.put_file(text, f"{tmp}/{name}", timeout_s=timeout_s)
            self.run(iso_build_cmd(tool, iso_path, tmp), timeout_s=timeout_s)
        finally:
            self.run(f"rm -rf {shlex.quote(tmp)}", timeout_s=15, check=False)
        return tool
