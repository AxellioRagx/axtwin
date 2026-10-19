"""The uniform persona-driver interface (docs/WORK_ORDER_persona_library.md §2).

A driver makes one persona's `emits[]` (or, for `real-service`, the node's
own ambient daemon) actually produce traffic on a deployed twin's node. All
five drivers (scripted/replay/real-service/command/browser) share this
start/stop/heartbeat shape so the Traffic tier can drive any of them
uniformly without a `drive`-keyed branch of its own — the branch lives here,
once, in `get_driver()`.

**No profile/binding logic lives here** (docs/WORK_ORDER_persona_library.md
§3): a driver's `start()` takes an already-resolved `peers` map (peer_role ->
IP) and an already-assigned `vmid`/`instance_id` — resolving WHICH twin host
gets WHICH persona, and WHICH peer role resolves to WHICH IP, is the future
Traffic tier's job (`docs/design/07-traffic-personas.md`).

**Transport reuse (LES-025, `docs/WORK_ORDER_persona_library.md` boundary
note):** drivers call `GuestAgent.exec`/`write_file` directly — the SAME
dependency `config_exec` already has on `adapter_proxmox` — rather than
opening a second QGA client. No planner/adapter import: nothing here
constructs an `OpInstance`/`ExecutionContext`; a driver is handed a live
`GuestAgent`-shaped object (or the tests' fake) and a `vmid`, exactly like
`config_exec.backend.ConfigBackend`'s private `_deploy_sensor`-style helpers
are handed one, just without the op-catalog/executor wrapper around it (no
ops are wired for personas yet — that's the traffic tier's job too).

**Never `nohup ... &` (LES-086, GAP-020/GAP-025):** every driver that starts
a long-running in-guest process launches it as a detached, deterministically
named transient systemd unit via `axrange.config_exec.render.
systemd_daemon_start_block` — the exact shared, tested primitive the
standing-sensor capture fix introduced for precisely this failure class (a
`nohup`'d background process is reaped the instant the QGA exec session that
launched it ends). Reused here verbatim, not reimplemented.

**Windows counterpart (2026-07-25, `docs/WORK_ORDER_persona_library.md`
Windows port):** a Windows persona/daemon must equally never be a bare
`Start-Process` child of the QGA exec session (the same LES-086 failure
class — see `axrange.config_exec.render.scheduled_task_start_block`'s
docstring for the full "why not `Start-Process`" reasoning). It launches as a
detached, deterministically-named Windows **Scheduled Task** instead
(`scheduled_task_start_block`), reused here via `stop_scheduled_task`/
`heartbeat_scheduled_task` — the Windows analog of this module's own
`stop_unit`/`heartbeat_unit`.
"""
from __future__ import annotations

import hashlib
import re
import zlib
from abc import ABC, abstractmethod
from typing import Any, Callable

from axrange.personas.schema import Persona

_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")
_PERSONA_DIR = "/var/lib/axrange/personas"
_LOG_DIR = "/var/log/axrange"
_WIN_PERSONA_DIR = r"C:\axrange\personas"


class PersonaDriverError(Exception):
    """A driver operation failed loudly (install/launch/assert failure)."""


def safe_instance_name(instance_id: str) -> str:
    """A filesystem/unit-name-safe form of an instance id (persona bound to
    one specific twin node — e.g. `"ws01-corp-workstation"`).

    Character-sanitizing alone (`_SAFE.sub("-", ...)`) is NOT injective —
    two distinct instance ids that differ only in a sanitized-away
    character (`"a/b"` and `"a b"`) both collapse to `"a-b"`, so a bare
    sanitize can silently alias two different persona instances onto the
    SAME systemd unit/dir/log path (code-review fix — the docstrings here
    used to claim units "never collide" without this). Fixed the same way
    `config_exec.render._truncate_unique` dedupes a truncated
    SamAccountName/username (LES-025 reuse of that pattern, adapted: this
    is a COLLISION guard, not a length-truncation guard, so the hash suffix
    is always appended, not only past a length limit): append a short,
    deterministic SHA1 prefix of the FULL, un-sanitized `instance_id`, so
    two instance ids that sanitize identically still get different names.
    Engine-generated instance ids are expected to already be
    `[a-zA-Z0-9_.-]`-only in practice, making this a defense-in-depth
    guard, not the common case."""
    sanitized = _SAFE.sub("-", instance_id)
    h = hashlib.sha1(instance_id.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{h}"


def instance_unit(instance_id: str) -> str:
    """The deterministic systemd transient-unit name for this persona
    instance — stable across redeploys of the SAME instance (so stop/
    reset-failed target the right unit), unique per instance (so two
    personas on one node, or the same persona on two nodes, never collide)."""
    return f"axr-persona-{safe_instance_name(instance_id)}"


def safe_label(label: str) -> str:
    """Escape a string for safe embedding inside a bash DOUBLE-quoted
    string — the exact shape `config_exec.render.systemd_daemon_start_block`
    interpolates its `label` argument into (`echo "[axrange] {label} ..."`,
    both the FATAL-on-failure line and the running-unit confirmation line).
    Bash still expands `$var`/`$(...)`/backticks and honors `\\` escapes
    INSIDE double quotes (only single quotes suppress all of that), so a
    `label` built from an `instance_id` containing any of those characters
    would either break the quoting or get shell-expanded rather than
    printed literally (code-review fix — cheap defense-in-depth; engine-
    generated instance ids aren't expected to carry shell metacharacters in
    practice, but nothing enforced that before this). Order matters:
    backslash first, so escaping the other three characters doesn't
    double-escape itself."""
    return (
        label.replace("\\", "\\\\")
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace('"', '\\"')
    )


def instance_paths(instance_id: str, *, os_family: str = "linux") -> dict[str, str]:
    """Deterministic in-guest paths for one persona instance.

    `os_family="windows"` mirrors the Linux shape under the existing Windows
    provisioning-dir convention (`config_exec/backend.py`'s `_WIN_PROVISION_DIR`,
    `render.py`'s `_WIN_CAPTURE_DIR` — both `C:\\axrange\\<component>`), with a
    `.ps1` script extension and the log kept INSIDE the instance's own dir
    (Windows has no `/var/log`-equivalent shared log-root convention this
    module's Linux `_LOG_DIR` mirrors). `unit` is the same deterministic name
    either way — a systemd transient-unit name on Linux, a Scheduled Task name
    on Windows; both call sites (`stop_unit`/`heartbeat_unit` vs.
    `stop_scheduled_task`/`heartbeat_scheduled_task`) just treat it as the
    instance's launch-mechanism identifier."""
    safe = safe_instance_name(instance_id)
    unit = instance_unit(instance_id)
    if (os_family or "linux").lower() == "windows":
        d = f"{_WIN_PERSONA_DIR}\\{safe}"
        return {
            "dir": d,
            "script": f"{d}\\client.ps1",
            "log": f"{d}\\persona.log",
            "unit": unit,
        }
    return {
        "dir": f"{_PERSONA_DIR}/{safe}",
        "script": f"{_PERSONA_DIR}/{safe}/client.py",
        "log": f"{_LOG_DIR}/persona-{safe}.log",
        "unit": unit,
    }


def effective_seed(seed: Any, instance_id: str) -> int | None:
    """Fold a raw seed with the persona instance id into a stable 32-bit RNG
    seed — same fold shape as `config_exec.render.activity_effective_seed`
    (LES-025 reuse), so two persona instances sharing one raw scenario seed
    still draw independent traffic-timing sequences. `None` when no seed is
    supplied (non-reproducible timing, by design)."""
    if seed is None:
        return None
    try:
        base = int(seed)
    except (TypeError, ValueError):
        import hashlib

        base = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16)
    fold = zlib.crc32(str(instance_id).encode()) & 0xFFFFFFFF
    return (base ^ fold) & 0xFFFFFFFF


def stop_unit(ga: Any, vmid: int, instance_id: str) -> dict[str, Any]:
    """Stop + reset-failed a persona instance's transient unit. Idempotent —
    `stop`/`reset-failed` on a not-yet-existing or already-stopped unit are
    harmless no-ops, so this is safe to call whether or not the instance was
    ever started."""
    unit = instance_unit(instance_id)
    cmd = (
        f"systemctl stop {unit} 2>/dev/null || true; "
        f"systemctl reset-failed {unit} 2>/dev/null || true"
    )
    ga.exec(vmid, ["sh", "-c", cmd], timeout_s=30)
    return {"stopped": True, "unit": unit}


def write_binary_file(
    ga: Any, vmid: int, path: str, data: bytes, *, mode: str | None = None
) -> None:
    """Write raw BYTES to a Linux guest path over QGA, base64-in-command
    (LES-002) — like `GuestAgent.write_file`'s Linux branch, but for
    arbitrary binary content (a captured pcap template) rather than text.

    `GuestAgent.write_file` is TEXT-only: it does `content.encode("utf-8")`
    before base64-ing, which is LOSSY for a byte string that isn't valid
    UTF-8 (e.g. any non-ASCII byte in a pcap's binary body round-trips
    through `bytes.decode("latin1")` -> `str.encode("utf-8")` as a DIFFERENT
    byte sequence — 1 latin1 codepoint > 0x7F becomes 2+ UTF-8 bytes). The
    replay driver needs true byte fidelity for a staged pcap, so this
    variant base64-encodes the raw bytes directly instead of routing them
    through `write_file`'s str/UTF-8 path — same LES-002 mechanism (payload
    as pure-ASCII base64 in the command args, decoded with `base64 -d`,
    never PVE's `input-data`), adapted for `bytes` input. Linux only (no
    replay-driver consumer targets Windows today)."""
    import base64
    import shlex

    b64 = base64.b64encode(data).decode("ascii")
    deliver = f"base64 -d > {shlex.quote(path)}"
    res = ga.exec(
        vmid, ["sh", "-c", f"printf %s {shlex.quote(b64)} | {deliver}"], timeout_s=120
    )
    if not res.ok:
        raise PersonaDriverError(
            f"writing {path} in vmid={vmid} failed (exit {res.exit_code}): "
            f"{(res.stderr or res.stdout)[:200]}"
        )
    if mode:
        res = ga.exec(vmid, ["chmod", mode, path], timeout_s=30)
        if not res.ok:
            raise PersonaDriverError(
                f"chmod {mode} {path} in vmid={vmid} failed (exit {res.exit_code})"
            )


def heartbeat_unit(ga: Any, vmid: int, instance_id: str) -> dict[str, Any]:
    """Liveness check via `systemctl is-active` — systemd tracks the
    daemon's real pid directly (stronger than an argv/comm `pgrep` match,
    same rationale as `systemd_daemon_start_block`'s own fail-loud check)."""
    unit = instance_unit(instance_id)
    res = ga.exec(vmid, ["systemctl", "is-active", "--quiet", unit], timeout_s=30)
    return {"alive": bool(res.ok), "unit": unit}


def start_linux_python_client(
    ga: Any,
    vmid: int,
    persona: Persona,
    instance_id: str,
    client_src: str,
    *,
    pre_launch: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The shared Linux `_start_linux` body for every driver that ships its
    client as a python3 script (`scripted`/`command` today — code-review
    fix, docs/WORK_ORDER_protocol_faithful_traffic.md review item 7:
    `CommandDriver._start_linux` used to duplicate `ScriptedDriver.
    _start_linux` almost verbatim, LES-025's "reuse downward" applied to a
    same-package sibling rather than a cross-package one).

    The caller renders its OWN `client_src` FIRST (`render_scripted_client`
    vs. `render_command_client` — the one genuine difference between the two
    drivers) so every authoring error surfaces before anything is written
    into the guest, then hands the already-rendered text here. This writes
    it, probes for `python3`, optionally runs a driver-specific PRE-LAUNCH
    check (`pre_launch` — the command driver's protocol-client tool probe,
    work order §3.4; the scripted driver passes none), then writes + runs
    the launch bootstrap and returns `{"started": True, "unit": ...}`.

    `render_scripted_launch` is imported LAZILY (call-time, not module
    level): `scripted.py` imports FROM this module at ITS OWN top level, so
    a module-level `from .scripted import ...` here would try to import a
    `scripted.py` that is still mid-initialization the first time either
    module loads -- a real circular-import failure, not a hypothetical one.
    Deferring the import to call time (long after both modules have
    finished loading) sidesteps it entirely -- the same pattern
    `planner/state.py`'s `create_deploy` already uses for the identical
    reason."""
    from axrange.personas.drivers.scripted import render_scripted_launch

    paths = instance_paths(instance_id)
    ga.exec(vmid, ["mkdir", "-p", paths["dir"]], timeout_s=30)
    ga.write_file(vmid, paths["script"], client_src, mode="0755")

    # python3 is baked into the base image (like every other config_exec
    # in-guest python path) — fail loudly if it's absent rather than
    # silently never starting.
    check = ga.exec(vmid, ["sh", "-lc", "command -v python3"], timeout_s=30)
    if not check.ok:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): python3 not found in "
            f"vmid={vmid}; bake it into the template (docs/IMAGE_BUILD.md)"
        )

    if pre_launch is not None:
        pre_launch()

    launch_path = f"{paths['dir']}/launch.sh"
    ga.write_file(
        vmid, launch_path,
        render_scripted_launch(instance_id, persona, script_path=paths["script"]),
        mode="0755",
    )
    res = ga.exec(vmid, ["bash", launch_path], timeout_s=60)
    if not res.ok:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): launch failed (exit "
            f"{res.exit_code}) in vmid={vmid}: {(res.stdout or res.stderr)[:300]}"
        )
    return {"started": True, "unit": paths["unit"]}


def start_windows_powershell_client(
    ga: Any,
    vmid: int,
    persona: Persona,
    instance_id: str,
    client_src: str,
    *,
    pre_launch: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The shared Windows `_start_windows` body for every driver that ships
    its client as a PowerShell script (`scripted` and the Windows half of
    `command` today) — the exact Windows counterpart of
    `start_linux_python_client`, and factored out for the same reason
    (LES-025: the `command` driver's Windows branch would otherwise duplicate
    `ScriptedDriver._start_windows` almost verbatim, the way its Linux branch
    once duplicated `ScriptedDriver._start_linux`).

    The caller renders its OWN `client_src` FIRST (`render_scripted_client_
    windows` vs. `render_command_client_windows` — the one genuine difference
    between the two drivers) so every authoring error surfaces before anything
    is written into the guest, then hands the already-rendered text here. This
    creates the instance directory (LES-174 — the client writes its log and
    its redirected stdout/stderr INTO that directory, so it must exist before
    the task starts), writes the client, optionally runs a driver-specific
    PRE-LAUNCH check (`pre_launch` — the command driver's built-in-command
    probe; the scripted driver passes none), then writes + runs the launch
    bootstrap and returns `{"started": True, "unit": ...}`.

    `render_scripted_launch_windows` is imported LAZILY for the same reason
    `start_linux_python_client` imports its Linux counterpart lazily:
    `scripted.py` imports FROM this module at its own top level, so a
    module-level import here would be a real circular-import failure."""
    from axrange.personas.drivers.scripted import render_scripted_launch_windows

    paths = instance_paths(instance_id, os_family="windows")
    dir_sq = paths["dir"].replace("'", "''")
    ga.shell_exec(
        vmid,
        f"New-Item -ItemType Directory -Force -Path '{dir_sq}' | Out-Null",
        timeout_s=30,
    )
    ga.write_file(vmid, paths["script"], client_src)

    if pre_launch is not None:
        pre_launch()

    launch_path = f"{paths['dir']}\\launch.ps1"
    ga.write_file(
        vmid, launch_path,
        render_scripted_launch_windows(
            instance_id, persona, script_path=paths["script"]
        ),
    )
    res = ga.exec(
        vmid,
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launch_path],
        timeout_s=60,
    )
    if not res.ok:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): launch failed (exit "
            f"{res.exit_code}) in vmid={vmid}: {(res.stdout or res.stderr)[:300]}"
        )
    return {"started": True, "unit": paths["unit"]}


def stop_scheduled_task(ga: Any, vmid: int, instance_id: str) -> dict[str, Any]:
    """Windows counterpart of `stop_unit`: stop + unregister a persona
    instance's Scheduled Task. Idempotent — `Stop-ScheduledTask`/
    `Unregister-ScheduledTask` both tolerate an absent or already-stopped
    task (`-ErrorAction SilentlyContinue`), the same "safe to call whether
    or not the instance was ever started" contract `stop_unit` has. Uses
    `ga.shell_exec` (PowerShell), not `ga.exec` with a raw `schtasks.exe`
    argv — the `ScheduledTasks` module's cmdlets return structured objects
    instead of locale-dependent `schtasks.exe` text columns."""
    unit = instance_unit(instance_id)
    tn_sq = unit.replace("'", "''")
    ps = (
        f"$TaskName = '{tn_sq}'\n"
        "Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue "
        "| Out-Null\n"
        "Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false "
        "-ErrorAction SilentlyContinue | Out-Null\n"
    )
    ga.shell_exec(vmid, ps, timeout_s=30)
    return {"stopped": True, "unit": unit}


def heartbeat_scheduled_task(ga: Any, vmid: int, instance_id: str) -> dict[str, Any]:
    """Windows counterpart of `heartbeat_unit`: `Get-ScheduledTask ...
    .State -eq 'Running'` — the Scheduled Task's own tracked state, the same
    "real state from the launch mechanism, not an argv/comm match" rationale
    `heartbeat_unit`'s `systemctl is-active` has."""
    unit = instance_unit(instance_id)
    tn_sq = unit.replace("'", "''")
    ps = (
        f"$t = Get-ScheduledTask -TaskName '{tn_sq}' -ErrorAction SilentlyContinue\n"
        "if ($t -and $t.State -eq 'Running') { exit 0 } else { exit 1 }\n"
    )
    res = ga.shell_exec(vmid, ps, timeout_s=30)
    return {"alive": bool(res.ok), "unit": unit}


def unsupported_os_family_error(persona: Persona, instance_id: str) -> PersonaDriverError:
    """The shared fail-loud error for an `os_family` dispatch's fall-through
    branch (review fix, 2026-07-25). Both `ScriptedDriver` and
    `RealServiceDriver` dispatch every start/stop/heartbeat on
    `persona.os_family` — a bare `if "windows": ... else: <linux>` silently
    drives ANY other value (a typo, an unsupported OS, or the empty string a
    malformed synthetic `Persona` could carry despite PSN-020's catalog-time
    enum check) down the Linux branch, sending `sh`/`systemctl` at a guest
    that may not even have a POSIX shell. Every dispatch point must instead
    be `if == "windows": ... elif == "linux": ... else: raise` — this is the
    one shared `raise`, so the message stays consistent across both drivers'
    three dispatch points each (LES-001: never a silent wrong-OS default)."""
    return PersonaDriverError(
        f"persona[{instance_id}] ({persona.id}): unsupported os_family "
        f"{persona.os_family!r} (expected 'windows' or 'linux') — refusing "
        "to guess a shell/launch mechanism for an unrecognized OS"
    )


class PersonaDriver(ABC):
    """The uniform interface every `drive:` kind implements."""

    @abstractmethod
    def start(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None = None,
        seed: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Start (or idempotently restart) this persona instance on `vmid`.
        Raises PersonaDriverError on a fail-loud condition (tool missing,
        unit didn't come up, asserted daemon absent).

        `peers` maps each emit's `peer_role` to ONE resolved peer IP.
        `peer_sets` (an optional `**kwargs` entry, 2026-07-28) additionally
        maps a `peer_scope: all` emit's role to EVERY twin host IP carrying
        it — the fan-out a subnet scanner or P2P mesh needs. Only
        `ScriptedDriver` reads it; replay/real-service absorb it via
        `**kwargs` like they already do `schedule`. Both maps are resolved by
        `o2control.traffic` — no driver decides which host holds which role."""

    @abstractmethod
    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        """Stop this persona instance. Idempotent."""

    @abstractmethod
    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str
    ) -> dict[str, Any]:
        """Return `{"alive": bool, ...}` — never raises on "not running";
        that's a legitimate heartbeat answer, not a driver failure.

        Exception: an unsupported `persona.os_family` (see
        `unsupported_os_family_error`) DOES raise from `heartbeat` too — that
        is a driver MISCONFIGURATION, not a liveness answer, and the
        "never raises" promise above is specifically about the daemon/task
        genuinely not running (e.g. no `service_name`/`process_match` bound,
        which still returns `{"alive": False, ...}`)."""
