"""Windows bake layer — the autounattend.xml, the Windows provisioning
translation, the sysprep seal, and the Windows capability probes.

The Windows analog of `bake.py` (Linux cloud-init). docs/SLICE_WINDOWS_AD.md
W0/§4: a Windows template is built from an **install ISO driven by an
autounattend.xml** (not a cloud qcow2 + cloud-init), with virtio-win drivers
injected, `qemu-guest-agent` (+ optionally cloudbase-init) installed in
FirstLogonCommands, then **sysprep /generalize** seals it.

Everything here is a pure function (deterministic string/list output), so the
render is unit-testable without a Windows guest. The orchestration that attaches
the media and runs sysprep lives in `proxmox_build.py` / `builder.py`.

Two known live-bring-up gotchas, surfaced here so they're greppable
(docs/SLICE_WINDOWS_AD.md §7 "resolve by building"):

  - **virtio drive letter.** Setup must find the virtio-scsi storage driver in
    WinPE or it cannot see the target disk. The letter the virtio ISO mounts as
    (DEFAULT_VIRTIO_LETTER) depends on the CD attach order pinned in
    proxmox_build; a wrong letter is the first thing to check on a failed build.
  - **completion marker.** FirstLogonCommands touch WINDOWS_BUILD_COMPLETE_MARKER
    last; `wait_windows_ready` polls it (Windows has no `cloud-init status`).
"""
from __future__ import annotations

import base64
import re
import xml.sax.saxutils as _sx
from typing import Any

from axrange.imagebuild.errors import ImageBuildError
from axrange.imagebuild.recipe import WindowsRecipe

# The in-guest file FirstLogonCommands write last; wait_windows_ready polls it.
WINDOWS_BUILD_COMPLETE_MARKER = r"C:\axr-build-complete.txt"

# Default virtio-win ISO mount letter in WinPE/OOBE (see module docstring).
DEFAULT_VIRTIO_LETTER = "E:"

# Standard virtio-win ISO layout. Server 2022 uses the `2k22` driver dirs.
_VIRTIO_VIOSCSI_DIR = r"vioscsi\2k22\amd64"
_VIRTIO_NETKVM_DIR = r"NetKVM\2k22\amd64"
_VIRTIO_QGA_MSI = r"guest-agent\qemu-ga-x86_64.msi"
_VIRTIO_GUEST_TOOLS = r"virtio-win-guest-tools.exe"

# The virtio ISO mounts as an unpredictable drive letter in WinPE (and a
# different one again in the installed OS), so the WinPE driver-injection lists
# every plausible optical-drive letter and Setup searches each, ignoring the
# ones that don't exist. live-verify dev45 2026-06-16: the ISO landed on D:,
# not the E: originally assumed (the autounattend ISO was F:).
_VIRTIO_LETTERS = ("C:", "D:", "E:", "F:", "G:")

_CLOUDBASE_INIT_URL = (
    "https://cloudbase.it/downloads/CloudbaseInitSetup_Stable_x64.msi"
)

_CHOCO_BOOTSTRAP = (
    "powershell -NoProfile -ExecutionPolicy Bypass -Command "
    "\"Set-ExecutionPolicy Bypass -Scope Process -Force; "
    "[System.Net.ServicePointManager]::SecurityProtocol = 3072; "
    "iex ((New-Object System.Net.WebClient).DownloadString("
    "'https://community.chocolatey.org/install.ps1'))\""
)


def _esc(s: Any) -> str:
    return _sx.escape(str(s))


def _netbios_name(hostname: str) -> str:
    """A valid NetBIOS computer name (<=15 chars, alnum + hyphen) for the
    autounattend's specialize ComputerName. The build VM's name is longer than
    15 chars, and Windows rejects an over-length ComputerName as an invalid
    answer file (live-verify, dev45 2026-06-16). The build name is throwaway —
    sysprep /generalize resets it; W1 sets the real name per-deploy."""
    s = re.sub(r"[^A-Za-z0-9-]", "-", hostname).strip("-")[:15].strip("-")
    return s or "axr-build"


def _ps_encoded(body: str) -> str:
    """A PowerShell -EncodedCommand string (UTF-16LE + base64), so arbitrary
    script survives the autounattend XML + cmd layering."""
    enc = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {enc}"


# ---------------------------------------------------------------------------
# Provisioning translation (IR §4.1 install actions -> Windows commands)
# ---------------------------------------------------------------------------


def windows_provision_to_commands(provision: list[dict[str, Any]] | None) -> list[str]:
    """Translate IR §4.1 install actions into ordered Windows command strings
    to run as FirstLogonCommands.

    Supported in the W0 bake: `pkg` / `pkg@version` (chocolatey — bootstrapped
    on first use, the analog of apt being present on Linux), `script` (inline
    PowerShell). `config` / `role` / `container` are NOT bake actions (`role` is
    W2's deploy-time DC promotion), so an unsupported kind is a loud error —
    never a silent drop, matching `bake._provision_to_cloud_config`.
    """
    cmds: list[str] = []
    needs_choco = any("pkg" in a for a in (provision or []))
    if needs_choco:
        cmds.append(_CHOCO_BOOTSTRAP)

    for idx, action in enumerate(provision or []):
        if "pkg" in action:
            pkg = action["pkg"]
            version = action.get("version")
            names = [pkg] if isinstance(pkg, str) else list(pkg)
            for n in names:
                if version:
                    cmds.append(
                        f"choco install -y --no-progress --version={version} {n}"
                    )
                else:
                    cmds.append(f"choco install -y --no-progress {n}")
        elif "script" in action:
            script = action["script"]
            if isinstance(script, dict) and "inline" in script:
                body = str(script["inline"])
            elif isinstance(script, str) and "\n" in script:
                body = script
            else:
                raise ImageBuildError(
                    f"provision[{idx}].script must be inline for the Windows "
                    "bake ({script: {inline: '...'}} or a multi-line string)",
                    stage="bake",
                )
            cmds.append(_ps_encoded(body))
        else:
            kind = next(iter(action), "<empty>")
            raise ImageBuildError(
                f"provision[{idx}] kind {kind!r} is not supported by the Windows "
                "imagebuild bake (pkg / pkg@version / script; `role` is W2's "
                "deploy-time DC promotion, not a bake step)",
                stage="bake",
            )
    return cmds


def _bake_commands(
    win: WindowsRecipe,
    provision: list[dict[str, Any]] | None,
    *,
    virtio_letter: str,
    install_qga: bool,
    install_cloudbaseinit: bool,
) -> list[tuple[str, str]]:
    """The ordered (description, command) pairs that become FirstLogonCommands.

    Implicit bakes first (the Windows analog of Linux's implicit
    `qemu-guest-agent` apt-install): the QGA MSI + virtio guest tools so the
    pipeline/deploy can talk to the guest, then cloudbase-init (W1's first-boot
    specialization channel), then the per-image provisioning, then the
    completion marker last.
    """
    cmds: list[tuple[str, str]] = []

    if install_qga:
        # The virtio ISO's drive letter in the installed OS isn't predictable
        # (differs from WinPE), so search every volume for the guest-tools
        # installer — it bundles the QEMU guest agent + virtio drivers and starts
        # the QGA service. live-verify dev45 2026-06-16.
        gt = _VIRTIO_GUEST_TOOLS
        find_gt = (
            "$ErrorActionPreference='SilentlyContinue';"
            "foreach($v in (Get-CimInstance Win32_LogicalDisk).DeviceID){"
            'if(Test-Path "$v\\' + gt + '"){'
            'Start-Process "$v\\' + gt + "\" -ArgumentList '/install','/passive','/norestart' -Wait;break}}"
        )
        cmds.append(("install virtio guest tools + QGA", _ps_encoded(find_gt)))

    if install_cloudbaseinit:
        # W1's leading first-boot mechanism; must be present in the template.
        # Configuration (which cloud-init drive to read) is W1's concern.
        dl = (
            "$ErrorActionPreference='Stop'; "
            "$m=\"$env:TEMP\\cbinit.msi\"; "
            f"(New-Object System.Net.WebClient).DownloadFile('{_CLOUDBASE_INIT_URL}',$m); "
            "Start-Process msiexec -ArgumentList \"/i $m /qn /norestart\" -Wait"
        )
        cmds.append(("install cloudbase-init", _ps_encoded(dl)))

    for c in windows_provision_to_commands(provision):
        cmds.append(("provision", c))

    cmds.append(
        (
            "signal build complete",
            f'cmd /c echo done> {WINDOWS_BUILD_COMPLETE_MARKER}',
        )
    )
    return cmds


# ---------------------------------------------------------------------------
# autounattend.xml
# ---------------------------------------------------------------------------


def _image_install_xml(win: WindowsRecipe) -> str:
    """The <ImageInstall>/<InstallFrom> image selector (by name or index)."""
    if win.edition:
        meta = (
            "                            <Key>/IMAGE/NAME</Key>\n"
            f"                            <Value>{_esc(win.edition)}</Value>\n"
        )
    else:
        meta = (
            "                            <Key>/IMAGE/INDEX</Key>\n"
            f"                            <Value>{int(win.image_index)}</Value>\n"
        )
    return (
        "                <InstallFrom>\n"
        "                    <MetaData wcm:action=\"add\">\n"
        f"{meta}"
        "                    </MetaData>\n"
        "                </InstallFrom>\n"
    )


def _first_logon_xml(commands: list[tuple[str, str]]) -> str:
    rows = []
    for order, (desc, cmd) in enumerate(commands, start=1):
        rows.append(
            "                    <SynchronousCommand wcm:action=\"add\">\n"
            f"                        <Order>{order}</Order>\n"
            f"                        <CommandLine>{_esc(cmd)}</CommandLine>\n"
            f"                        <Description>{_esc(desc)}</Description>\n"
            "                        <RequiresUserInput>false</RequiresUserInput>\n"
            "                    </SynchronousCommand>\n"
        )
    return "".join(rows)


def _driver_paths_xml() -> str:
    """The windowsPE PnP DriverPaths — vioscsi + NetKVM for every candidate
    drive letter (the virtio ISO's WinPE letter isn't predictable). Setup
    searches each path and silently ignores the non-existent ones."""
    rows = []
    kv = 1
    for letter in _VIRTIO_LETTERS:
        for d in (_VIRTIO_VIOSCSI_DIR, _VIRTIO_NETKVM_DIR):
            rows.append(
                f'                <PathAndCredentials wcm:action="add" wcm:keyValue="{kv}">\n'
                f"                    <Path>{letter}\\{d}</Path>\n"
                "                </PathAndCredentials>\n"
            )
            kv += 1
    return "".join(rows)


def render_autounattend(
    *,
    hostname: str,
    win: WindowsRecipe,
    provision: list[dict[str, Any]] | None = None,
    virtio_letter: str = DEFAULT_VIRTIO_LETTER,
    install_qga: bool = True,
    install_cloudbaseinit: bool = True,
) -> str:
    """Render a deterministic `autounattend.xml` for an unattended Windows
    Server install onto a UEFI/GPT virtio-scsi disk.

    Passes: windowsPE (disk layout + image select + virtio driver inject),
    specialize (computer name + timezone), oobeSystem (admin password +
    autologon + FirstLogonCommands: QGA/cloudbase-init/provisioning/marker).
    Same params -> byte-identical output.
    """
    v = virtio_letter.rstrip("\\")
    product_key_xml = (
        f"                <ProductKey>{_esc(win.product_key)}</ProductKey>\n"
        if win.product_key
        else ""
    )
    commands = _bake_commands(
        win,
        provision,
        virtio_letter=v,
        install_qga=install_qga,
        install_cloudbaseinit=install_cloudbaseinit,
    )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<unattend xmlns="urn:schemas-microsoft-com:unattend"'
        ' xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">\n'
        # ---- windowsPE: disk + image + drivers ----
        '    <settings pass="windowsPE">\n'
        '        <component name="Microsoft-Windows-International-Core-WinPE"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
        ' xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">\n'
        f"            <SetupUILanguage><UILanguage>{_esc(win.locale)}</UILanguage></SetupUILanguage>\n"
        f"            <InputLocale>{_esc(win.locale)}</InputLocale>\n"
        f"            <SystemLocale>{_esc(win.locale)}</SystemLocale>\n"
        f"            <UILanguage>{_esc(win.locale)}</UILanguage>\n"
        f"            <UserLocale>{_esc(win.locale)}</UserLocale>\n"
        "        </component>\n"
        '        <component name="Microsoft-Windows-PnpCustomizationsWinPE"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        "            <DriverPaths>\n"
        f"{_driver_paths_xml()}"
        "            </DriverPaths>\n"
        "        </component>\n"
        '        <component name="Microsoft-Windows-Setup"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        "            <DiskConfiguration>\n"
        '                <Disk wcm:action="add">\n'
        "                    <CreatePartitions>\n"
        '                        <CreatePartition wcm:action="add">\n'
        "                            <Order>1</Order><Type>EFI</Type><Size>260</Size>\n"
        "                        </CreatePartition>\n"
        '                        <CreatePartition wcm:action="add">\n'
        "                            <Order>2</Order><Type>MSR</Type><Size>16</Size>\n"
        "                        </CreatePartition>\n"
        '                        <CreatePartition wcm:action="add">\n'
        "                            <Order>3</Order><Type>Primary</Type><Extend>true</Extend>\n"
        "                        </CreatePartition>\n"
        "                    </CreatePartitions>\n"
        "                    <ModifyPartitions>\n"
        '                        <ModifyPartition wcm:action="add">\n'
        "                            <Order>1</Order><PartitionID>1</PartitionID>\n"
        "                            <Format>FAT32</Format><Label>System</Label>\n"
        "                        </ModifyPartition>\n"
        '                        <ModifyPartition wcm:action="add">\n'
        "                            <Order>2</Order><PartitionID>3</PartitionID>\n"
        "                            <Format>NTFS</Format><Label>Windows</Label>\n"
        "                        </ModifyPartition>\n"
        "                    </ModifyPartitions>\n"
        "                    <DiskID>0</DiskID>\n"
        "                    <WillWipeDisk>true</WillWipeDisk>\n"
        "                </Disk>\n"
        "            </DiskConfiguration>\n"
        "            <ImageInstall>\n"
        "                <OSImage>\n"
        f"{_image_install_xml(win)}"
        "                    <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>\n"
        "                </OSImage>\n"
        "            </ImageInstall>\n"
        "            <UserData>\n"
        "                <AcceptEula>true</AcceptEula>\n"
        f"                <FullName>{_esc(win.owner)}</FullName>\n"
        f"                <Organization>{_esc(win.org)}</Organization>\n"
        f"{product_key_xml}"
        "            </UserData>\n"
        "        </component>\n"
        "    </settings>\n"
        # ---- specialize: identity ----
        '    <settings pass="specialize">\n'
        '        <component name="Microsoft-Windows-Shell-Setup"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        f"            <ComputerName>{_esc(_netbios_name(hostname))}</ComputerName>\n"
        f"            <RegisteredOrganization>{_esc(win.org)}</RegisteredOrganization>\n"
        f"            <RegisteredOwner>{_esc(win.owner)}</RegisteredOwner>\n"
        f"            <TimeZone>{_esc(win.timezone)}</TimeZone>\n"
        "        </component>\n"
        "    </settings>\n"
        # ---- oobeSystem: admin pw + autologon + first-logon commands ----
        '    <settings pass="oobeSystem">\n'
        '        <component name="Microsoft-Windows-Shell-Setup"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        "            <AutoLogon>\n"
        "                <Enabled>true</Enabled>\n"
        "                <LogonCount>5</LogonCount>\n"
        f"                <Password><Value>{_esc(win.admin_password)}</Value><PlainText>true</PlainText></Password>\n"
        "                <Username>Administrator</Username>\n"
        "            </AutoLogon>\n"
        "            <FirstLogonCommands>\n"
        f"{_first_logon_xml(commands)}"
        "            </FirstLogonCommands>\n"
        "            <OOBE>\n"
        "                <HideEULAPage>true</HideEULAPage>\n"
        "                <HideLocalAccountScreen>true</HideLocalAccountScreen>\n"
        "                <HideOnlineAccountScreens>true</HideOnlineAccountScreens>\n"
        "                <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>\n"
        "                <NetworkLocation>Work</NetworkLocation>\n"
        "                <ProtectYourPC>3</ProtectYourPC>\n"
        "            </OOBE>\n"
        "            <UserAccounts>\n"
        "                <AdministratorPassword>\n"
        f"                    <Value>{_esc(win.admin_password)}</Value>\n"
        "                    <PlainText>true</PlainText>\n"
        "                </AdministratorPassword>\n"
        "            </UserAccounts>\n"
        "        </component>\n"
        "    </settings>\n"
        "</unattend>\n"
    )


# Must match WINDOWS_OSREADY_MARKER in adapter_proxmox/rest.py (W1 os-ready
# signal). Dropped once the clone is OUT of OOBE + specialized; wait-os-ready
# polls it. Kept as a literal here to avoid coupling the render layer to the adapter.
_OSREADY_MARKER = r"C:\Windows\Temp\axr-osready"


def render_sysprep_unattend(*, win: WindowsRecipe) -> str:
    """A minimal `specialize`+`oobeSystem` unattend for the CLONE's first boot,
    referenced by `sysprep /generalize /oobe /unattend:` (docs/SLICE_WINDOWS_AD.md
    §4 / §6.1).

    **Why it exists:** without it the sysprepped clone halts at the OOBE "Hi there"
    screen and never specializes — cloudbase-init (which runs *post*-OOBE) then
    never executes, and the guest is not AD-promotable (live-verify dev45
    2026-06-17, the W2a blocker). This auto-completes OOBE and drives it all the way
    to IMAGE_STATE_COMPLETE so the clone reaches a *specialized*, QGA-answering,
    AD-promotable state deterministically, independent of whether cloudbase-init's
    datasource is wired. Per-deploy hostname/password remains cloudbase-init's job
    (W1.3) when present; ComputerName is deliberately left unset here (the
    generalized name stands, or W1 renames it).

    **Two things make OOBE actually auto-complete** (live-verify dev45 2026-06-17 —
    the Hide* flags alone left ImageState=IMAGE_STATE_UNDEPLOYABLE, stuck on the
    "Hi there" region screen):
      - a `Microsoft-Windows-International-Core` component (Input/System/UI/UserLocale)
        in the oobeSystem pass — Server's first OOBE screen is region/keyboard, which
        is governed by International-Core, NOT the <OOBE><Hide*> flags; and
      - `<SkipMachineOOBE>` + `<SkipUserOOBE>` alongside the Hide* flags, which forces
        OOBE to skip straight through to completion.

    The os-ready marker is NOT dropped here (FirstLogonCommands need an interactive
    logon that doesn't reliably happen); a SYSTEM-context `SetupComplete.cmd`
    (`render_setup_complete_cmd`, baked into the template before sysprep) drops it
    after Setup finishes, with no logon dependency.

    Same `win` -> byte-identical output.
    """
    pw = _esc(win.admin_password)
    loc = _esc(win.locale)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<unattend xmlns="urn:schemas-microsoft-com:unattend"'
        ' xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">\n'
        # ---- specialize: time zone only (no ComputerName — W1/cloudbase-init names it) ----
        '    <settings pass="specialize">\n'
        '        <component name="Microsoft-Windows-Shell-Setup"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        f"            <TimeZone>{_esc(win.timezone)}</TimeZone>\n"
        "        </component>\n"
        "    </settings>\n"
        # ---- oobeSystem: locale + skip OOBE entirely + autologon ----
        '    <settings pass="oobeSystem">\n'
        # International-Core governs the "Hi there" region/keyboard screen; without
        # it OOBE stalls there and never reaches IMAGE_STATE_COMPLETE.
        '        <component name="Microsoft-Windows-International-Core"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        f"            <InputLocale>{loc}</InputLocale>\n"
        f"            <SystemLocale>{loc}</SystemLocale>\n"
        f"            <UILanguage>{loc}</UILanguage>\n"
        f"            <UserLocale>{loc}</UserLocale>\n"
        "        </component>\n"
        '        <component name="Microsoft-Windows-Shell-Setup"'
        ' processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        "            <AutoLogon>\n"
        "                <Enabled>true</Enabled>\n"
        "                <LogonCount>2</LogonCount>\n"
        f"                <Password><Value>{pw}</Value><PlainText>true</PlainText></Password>\n"
        "                <Username>Administrator</Username>\n"
        "            </AutoLogon>\n"
        "            <OOBE>\n"
        "                <HideEULAPage>true</HideEULAPage>\n"
        "                <HideLocalAccountScreen>true</HideLocalAccountScreen>\n"
        "                <HideOnlineAccountScreens>true</HideOnlineAccountScreens>\n"
        "                <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>\n"
        "                <SkipMachineOOBE>true</SkipMachineOOBE>\n"
        "                <SkipUserOOBE>true</SkipUserOOBE>\n"
        "                <NetworkLocation>Work</NetworkLocation>\n"
        "                <ProtectYourPC>3</ProtectYourPC>\n"
        "            </OOBE>\n"
        "            <UserAccounts>\n"
        "                <AdministratorPassword>\n"
        f"                    <Value>{pw}</Value>\n"
        "                    <PlainText>true</PlainText>\n"
        "                </AdministratorPassword>\n"
        "            </UserAccounts>\n"
        "        </component>\n"
        "    </settings>\n"
        "</unattend>\n"
    )


# SetupComplete.cmd lives here; Windows Setup runs it once, as SYSTEM, at the very
# end of Setup (after specialize + OOBE) — no interactive logon required. Ideal for
# the os-ready signal, which must not depend on AutoLogon/FirstLogonCommands/cloudbase-init.
SETUP_COMPLETE_PATH = r"C:\Windows\Setup\Scripts\SetupComplete.cmd"


def render_setup_complete_cmd(*, win: WindowsRecipe) -> str:
    """The `SetupComplete.cmd` baked into the template before sysprep.

    Runs once as SYSTEM at the end of Setup (post specialize/OOBE), with **no
    interactive logon** — so it's where W2a drops the os-ready marker and makes sure
    QGA is running, decoupled from AutoLogon / FirstLogonCommands / cloudbase-init
    (all of which proved unreliable: the clone never reached an interactive logon, so
    the marker was never created — the W2a blocker, live-verify dev45 2026-06-17).

    Deterministic: same `win` -> byte-identical output (no per-run state).
    """
    # CRLF line endings: this is a .cmd consumed by Windows Setup.
    lines = [
        "@echo off",
        "rem axRange W1 os-ready hook — runs once as SYSTEM at end of Setup.",
        "rem Ensure the QEMU guest agent is up (deploy talks to the guest over it).",
        "sc config QEMU-GA start= auto",
        "net start QEMU-GA",
        "rem Drop the os-ready marker last — wait-os-ready (W1.2) polls it.",
        r"if not exist C:\Windows\Temp mkdir C:\Windows\Temp",
        r"echo done> C:\Windows\Temp\axr-osready",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Seal (sysprep) + probes
# ---------------------------------------------------------------------------


def windows_sysprep_command(*, unattend_path: str | None = None) -> list[str]:
    """The sysprep generalize+shutdown argv (the Windows seal; docs/IMAGE_BUILD.md
    §3 step 7 analog). Unlike Linux seal, sysprep itself **shuts the VM down** —
    there is no separate cloud-init-clean. `/mode:vm` skips the hardware
    re-detect that's pointless on an identical clone target."""
    argv = [
        r"C:\Windows\System32\Sysprep\sysprep.exe",
        "/generalize",
        "/oobe",
        "/shutdown",
        "/mode:vm",
    ]
    if unattend_path:
        argv.append(f"/unattend:{unattend_path}")
    return argv


# Best-effort PowerShell probes (one per Windows `provides:` keyword), each
# `exit 0` (pass) / `exit 1` (fail). Like the Linux probes (bake._DEFAULT_PROBES)
# these assert a capability is *plausibly present*, not a deep functional test;
# operators override per-image via the binding's `build.probes`.
_WINDOWS_PROBES: dict[str, str] = {
    "windows": "if ((Get-CimInstance Win32_OperatingSystem).Caption -match 'Windows') { exit 0 } else { exit 1 }",
    # UEFI boots winload.efi; legacy BIOS boots winload.exe.
    "host:uefi": "if (bcdedit | Select-String -Quiet 'winload.efi') { exit 0 } else { exit 1 }",
    "host:secure-boot": "try { Confirm-SecureBootUEFI | Out-Null; exit 0 } catch { exit 1 }",
    "host:tpm": "try { if ((Get-Tpm).TpmPresent) { exit 0 } else { exit 1 } } catch { exit 1 }",
    "kernel:powershell-5plus": "if ($PSVersionTable.PSVersion.Major -ge 5) { exit 0 } else { exit 1 }",
    "kernel:dotnet-clr": "if (Test-Path \"$env:windir\\Microsoft.NET\\Framework64\") { exit 0 } else { exit 1 }",
    # On Windows, EDR hooks == ETW + the kernel callback surface; assert the
    # WMI/CIM telemetry plane answers (a stand-in, like the Linux ebpf+audit one).
    "kernel:edr-hooks": "try { Get-CimInstance Win32_OperatingSystem | Out-Null; exit 0 } catch { exit 1 }",
}


def default_windows_probe_for(capability: str) -> str | None:
    return _WINDOWS_PROBES.get(capability)


def windows_probe_command(capability: str, overrides: dict[str, str]) -> str | None:
    """Resolve a Windows probe command: binding override wins, else built-in."""
    if capability in overrides:
        return overrides[capability]
    return _WINDOWS_PROBES.get(capability)
