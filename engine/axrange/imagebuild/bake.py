"""Bake step: the cloud-init `#cloud-config` that provisions the build VM,
the seal commands, and the default per-capability conformance probes.

docs/IMAGE_BUILD.md §3 step 4 ("Bake"): boot the build VM with a cloud-init
config that **preinstalls and enables `qemu-guest-agent`** plus the image's
declared provisioning steps. §3 step 7 ("Seal") scripts the 2026-05-29 manual
template-rebuild runbook's seal sequence. §4.1 ("Capability probes") runs one
probe per `provides:` keyword inside the booted VM.

QGA auto-start nuance (from the 2026-05-29 adapter bring-up, see
adapter_proxmox/rest.py:_render_userdata): on Debian/Ubuntu cloud images
`qemu-guest-agent.service` is a *static* unit (no `[Install]` section), so
`systemctl enable` is a no-op. We therefore use `systemctl add-wants
multi-user.target qemu-guest-agent.service`, which creates the want symlink
even for static units — this is what makes the **template's clones** auto-start
QGA on every boot, satisfying the docs/IMAGE_BUILD.md §8 done-criterion
("qemu-guest-agent preinstalled + auto-start enabled"). The extra `start` keeps
QGA up on the build VM's own boot so the pipeline can talk to it.
"""
from __future__ import annotations

from typing import Any

from axrange.imagebuild.errors import ImageBuildError

# IR_SCHEMA §4.1 install-action kinds the slice's bake understands. `config`,
# `role`, and `container` provisioning grow in later slices; for now an
# unsupported kind is a loud error, never a silent drop.
_SUPPORTED_PROVISION_KINDS = {"pkg", "pkg@version", "script"}


def _provision_to_cloud_config(
    provision: list[dict[str, Any]],
) -> tuple[list[str], list[Any], list[dict[str, Any]]]:
    """Translate IR §4.1 install-action dicts into cloud-init fragments.

    Returns (packages, runcmd_entries, write_files). `qemu-guest-agent` is
    NOT added here — the pipeline always bakes it (see render_bake_userdata).
    """
    packages: list[str] = []
    runcmd: list[Any] = []
    write_files: list[dict[str, Any]] = []

    for idx, action in enumerate(provision):
        if "pkg" in action:
            pkg = action["pkg"]
            version = action.get("version")
            if version:
                names = [pkg] if isinstance(pkg, str) else list(pkg)
                for n in names:
                    runcmd.append(
                        ["apt-get", "install", "-y", f"{n}={version}"]
                    )
            elif isinstance(pkg, str):
                packages.append(pkg)
            elif isinstance(pkg, list):
                packages.extend(str(p) for p in pkg)
            else:
                raise ImageBuildError(
                    f"provision[{idx}].pkg must be a string or list",
                    stage="bake",
                )
        elif "script" in action:
            script = action["script"]
            # Slice supports inline scripts: {script: {inline: "..."}} or a
            # raw string treated as an inline shell snippet. Path-referenced
            # scripts (reading a file from the binding bundle) come later.
            if isinstance(script, dict) and "inline" in script:
                body = str(script["inline"])
            elif isinstance(script, str) and "\n" in script:
                body = script
            else:
                raise ImageBuildError(
                    f"provision[{idx}].script must be inline for the slice "
                    "({script: {inline: '...'}} or a multi-line string)",
                    stage="bake",
                )
            path = f"/var/lib/axrange/provision-{idx}.sh"
            write_files.append(
                {
                    "path": path,
                    "permissions": "0755",
                    "content": body,
                }
            )
            runcmd.append(["/bin/sh", path])
        else:
            kind = next(iter(action), "<empty>")
            raise ImageBuildError(
                f"provision[{idx}] kind {kind!r} is not supported by the "
                f"imagebuild slice (supported: {sorted(_SUPPORTED_PROVISION_KINDS)})",
                stage="bake",
            )

    return packages, runcmd, write_files


def render_bake_userdata(
    *,
    hostname: str = "axr-build",
    user: str | None = None,
    ssh_pubkey: str = "",
    provision: list[dict[str, Any]] | None = None,
) -> str:
    """Render the `#cloud-config` user-data for the build VM bake.

    Always: refresh apt, install `qemu-guest-agent`, wire it to auto-start on
    every boot, and start it now. Then any per-image provisioning steps.
    """
    provision = provision or []
    pkgs, prov_runcmd, prov_write_files = _provision_to_cloud_config(provision)

    lines: list[str] = ["#cloud-config", f"hostname: {hostname}", "manage_etc_hosts: true"]

    if user:
        lines += [
            "users:",
            f"  - name: {user}",
            "    sudo: ALL=(ALL) NOPASSWD:ALL",
            "    shell: /bin/bash",
            "    groups: [sudo]",
            "    lock_passwd: true",
        ]
        if ssh_pubkey:
            lines += ["    ssh_authorized_keys:", f"      - {ssh_pubkey}"]

    lines += ["package_update: true", "packages:", "  - qemu-guest-agent"]
    for p in pkgs:
        lines.append(f"  - {p}")

    if prov_write_files:
        lines.append("write_files:")
        for wf in prov_write_files:
            lines += [
                f"  - path: {wf['path']}",
                f"    permissions: '{wf['permissions']}'",
                "    content: |",
            ]
            for body_line in str(wf["content"]).splitlines():
                lines.append(f"      {body_line}")

    lines.append("runcmd:")
    # Auto-start QGA on every future boot (template clones) + this boot.
    lines.append(
        "  - [ systemctl, add-wants, multi-user.target, qemu-guest-agent.service ]"
    )
    lines.append("  - [ systemctl, start, qemu-guest-agent ]")
    for entry in prov_runcmd:
        # Render argv-style runcmd entries as YAML flow lists.
        rendered = ", ".join(_yaml_flow_scalar(a) for a in entry)
        lines.append(f"  - [ {rendered} ]")

    return "\n".join(lines) + "\n"


def _yaml_flow_scalar(s: Any) -> str:
    text = str(s)
    if any(c in text for c in ",[]{}:#&*!|>'\"%@`") or " " in text:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def seal_commands() -> list[list[str]]:
    """The seal sequence (docs/IMAGE_BUILD.md §3 step 7), as guest-exec argv
    lists run as root before shutdown. Scripts the 2026-05-29 manual runbook:
    cloud-init clean, reset machine-id, reset SSH host keys.
    """
    return [
        ["cloud-init", "clean", "--logs", "--seed"],
        ["sh", "-c", "truncate -s 0 /etc/machine-id || true"],
        [
            "sh",
            "-c",
            "rm -f /var/lib/dbus/machine-id && "
            "ln -sf /etc/machine-id /var/lib/dbus/machine-id || true",
        ],
        ["sh", "-c", "rm -f /etc/ssh/ssh_host_*"],
        ["sync"],
    ]


# ---------------------------------------------------------------------------
# Default per-capability probes (docs/IMAGE_BUILD.md §4.1)
# ---------------------------------------------------------------------------
#
# The Linux `provides:` subset is closed (axrange.ir.capabilities) so this
# table is finite and does not drift: the OS-family keyword, the host:* set,
# and the kernel:* set. Each probe is a minimal `sh -c` test returning 0 (pass)
# or non-zero (fail), run inside the booted VM via the guest agent BEFORE seal.
# Best-effort by design — a probe asserts the capability is *plausibly present*,
# not a deep functional test (docs/IMAGE_MANIFEST.md §5 items 3-4 are honest
# targets the pipeline grows into). Operators override per-image via the
# binding's `build.probes:` map.

_DEFAULT_PROBES: dict[str, str] = {
    # OS family
    "linux": "uname -s | grep -qi linux",
    # host:* — physical-platform features surfaced into the guest
    "host:uefi": "test -d /sys/firmware/efi",
    "host:secure-boot": "test -e /sys/firmware/efi/efivars && "
    "ls /sys/firmware/efi/efivars/SecureBoot-* >/dev/null 2>&1",
    "host:tpm": "test -c /dev/tpm0 -o -c /dev/tpmrm0",
    "host:nested-virt": "grep -qE '(vmx|svm)' /proc/cpuinfo",
    "host:gpu": "test -e /dev/dri/card0",
    "host:cpu-aes-ni": "grep -q aes /proc/cpuinfo",
    "host:hugepages": "grep -q HugePages_Total /proc/meminfo",
    # kernel:* — kernel/runtime features
    "kernel:ebpf": "test -d /sys/fs/bpf",
    "kernel:audit": "test -e /proc/self/loginuid",
    "kernel:container-runtime": "test -e /sys/fs/cgroup/cgroup.controllers "
    "-o -d /sys/fs/cgroup/unified",
    # On Linux, EDR hooks == eBPF + audit both functional.
    "kernel:edr-hooks": "test -d /sys/fs/bpf && test -e /proc/self/loginuid",
    # Cross-platform runtimes that may ship on Linux images that claim them.
    "kernel:dotnet-clr": "command -v dotnet >/dev/null 2>&1",
    "kernel:powershell-5plus": "command -v pwsh >/dev/null 2>&1",
}


def default_probe_for(capability: str) -> str | None:
    """The built-in probe command for a `provides:` keyword, or None if the
    keyword has no default (then the binding must supply one in `build.probes`)."""
    return _DEFAULT_PROBES.get(capability)


def probe_command(capability: str, overrides: dict[str, str]) -> str | None:
    """Resolve the probe command for a capability: binding override wins, else
    the built-in default."""
    if capability in overrides:
        return overrides[capability]
    return _DEFAULT_PROBES.get(capability)
