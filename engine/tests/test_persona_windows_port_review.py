"""Adversarial review tests for the Windows persona-driver port
(2026-07-25, `docs/WORK_ORDER_persona_library.md`): golden-pins the
pre-existing Linux behavior (must stay byte-identical), probes the new
Windows rendering for determinism/escaping/overflow safety, and proves a
known code-review gap (`traffic.py::_stop_instances`'s hardcoded
`os_family="linux"` placeholder) so the fix is verified when it lands.

No live cluster: everything here is pure rendering + fake-QGA dispatch,
matching the idioms in `tests/test_personas.py` / `tests/test_traffic_tier.py`.
"""
from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest

from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec.render import scheduled_task_start_block
from axrange.personas.drivers import RealServiceDriver, ScriptedDriver, get_driver
from axrange.personas.drivers.base import instance_paths
from axrange.personas.drivers.scripted import (
    render_scripted_client,
    render_scripted_client_windows,
    render_scripted_launch,
)
from axrange.personas.registry import PERSONAS
from axrange.personas.schema import Emit, Persona


# ---------------------------------------------------------------------------
# Fake GuestAgent (mirrors tests/test_personas.py's _FakeGA — a raw exec/
# shell_exec recorder, no real QGA/Proxmox).
# ---------------------------------------------------------------------------
class _FakeGA:
    def __init__(self, *, run_exit=0):
        self.run_exit = run_exit
        self.execs: list[tuple] = []
        self.shell_execs: list[str] = []
        self.written: dict[str, str] = {}

    def exec(self, vmid, argv, *, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:1] == ["sh"] and "command -v python3" in " ".join(argv):
            return GuestExecResult(0, "/usr/bin/python3", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return GuestExecResult(0 if self.run_exit == 0 else 1, "", "")
        if argv[:1] == ["pgrep"]:
            return GuestExecResult(0 if self.run_exit == 0 else 1, "", "")
        return GuestExecResult(self.run_exit, "ok", "" if self.run_exit == 0 else "boom")

    def shell_exec(self, vmid, command, *, timeout_s=300):
        self.shell_execs.append(command)
        return GuestExecResult(0 if self.run_exit == 0 else 1, "", "" if self.run_exit == 0 else "boom")

    def write_file(self, vmid, path, content, *, mode=None):
        self.written[path] = content


# ===========================================================================
# 1. Linux paths must be byte-identical — golden/byte pin.
# ===========================================================================

# Computed once from the CURRENT render of a real LINUX scripted catalog
# persona (backup-server) with fixed instance_id/peers/seed. An UNINTENDED
# regression in the Linux branch changes this hash.
#
# Re-pinned 2026-07-28 (was 78c35c96...) — the high-volume persona wave
# deliberately rewrote the client template on BOTH branches: UDP transport,
# volume-derived byte payloads, `peer_scope: all` fan-out (`peer_ip` ->
# `peer_ips`), and a per-fire summary log line instead of a per-message one.
# The original pin guarded the Windows PORT from silently touching the Linux
# branch, which is a different (and still valid) concern — this change alters
# both branches on purpose.
_LINUX_CLIENT_SHA256 = "ccc83dd5745d5defc9dc801e58403c524c4cb747fedccc559b8c8a09692184a5"
# Re-pinned 2026-08-04 (was 6fd6b404...) — `systemd_daemon_start_block` gained
# a leading `mkdir -p $(dirname <log>)` so the unit's stdout/stderr redirect
# can't fail on a plain host with no pre-existing `/var/log/axrange` (W1 dev45
# live-verify found the persona launch dying there; the Loki-shipper block
# already had this line). Deliberate output change; the client render
# (`_LINUX_CLIENT_SHA256`) is untouched.
_LINUX_LAUNCH_SHA256 = "59b4c6a865c3d17ce272dfc70f42f177f65cd3f372326354fa3b137e69aaabd8"


def test_linux_scripted_client_render_golden_bytes():
    persona = PERSONAS["backup-server"]
    assert persona.os_family == "linux"
    client = render_scripted_client(
        persona, instance_id="bkp01-backup-server",
        peers={"file-server": "10.0.0.6"}, seed=42,
    )
    got = hashlib.sha256(client.encode()).hexdigest()
    assert got == _LINUX_CLIENT_SHA256, (
        "Linux scripted-client render changed -- the Windows port's own "
        "docstring claims this path is UNCHANGED/byte-identical; if this "
        "diff is intentional, update the pinned hash deliberately"
    )


def test_linux_scripted_launch_render_golden_bytes():
    persona = PERSONAS["backup-server"]
    launch = render_scripted_launch(
        "bkp01-backup-server", persona,
        script_path=instance_paths("bkp01-backup-server")["script"],
    )
    got = hashlib.sha256(launch.encode()).hexdigest()
    assert got == _LINUX_LAUNCH_SHA256
    # Explicit shape assertions (not just an opaque hash) — the Linux launch
    # path must still be a systemd transient unit, never a Scheduled Task /
    # nohup / Start-Process.
    assert "systemd-run --unit=" in launch
    # W1 live-verify regression (2026-08-04): the log dir MUST be created before
    # the unit redirects stdout/stderr into it, or the launch dies "No such
    # file or directory" on any host that never incidentally created
    # /var/log/axrange. Guarded explicitly, not just via the opaque hash.
    assert "mkdir -p $(dirname /var/log/axrange/" in launch
    assert launch.index("mkdir -p") < launch.index("systemd-run"), (
        "the log-dir mkdir must precede the unit launch"
    )
    assert "Register-ScheduledTask" not in launch
    assert "nohup" not in launch
    assert "Start-Process" not in launch


def test_linux_scripted_driver_start_dispatches_via_bash_never_powershell():
    """End-to-end: `ScriptedDriver.start` on a LINUX persona must never touch
    `ga.shell_exec` (the Windows-only PowerShell channel) at all."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    result = driver.start(
        ga, 101, persona, instance_id="bkp01-backup-server",
        peers={"file-server": "10.0.0.6"}, seed=42,
    )
    assert result["started"] is True
    assert ga.shell_execs == []
    assert any(argv[:1] == ("bash",) for argv in ga.execs)


def test_linux_real_service_check_command_shape_unchanged():
    """The Linux `real-service` assertion is still exactly `systemctl
    is-active --quiet <service>` over a raw `ga.exec` argv -- pinned
    word-for-word, not just "it passed"."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["file-server"]
    assert persona.os_family == "linux"
    driver = RealServiceDriver()
    result = driver.start(
        ga, 103, persona, instance_id="fs01-file-server", service_name="smbd",
    )
    assert result["asserted"] is True
    assert ga.shell_execs == []
    assert ("systemctl", "is-active", "--quiet", "smbd") in ga.execs


def test_linux_real_service_process_match_check_command_shape_unchanged():
    """`vpn-concentrator`'s `process_match: charon` binding -- pinned exact
    `pgrep -f <pattern>` argv shape."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["vpn-concentrator"]
    assert persona.os_family == "linux"
    driver = RealServiceDriver()
    result = driver.start(
        ga, 104, persona, instance_id="vpn01-vpn-concentrator",
    )
    assert result["asserted"] is True
    assert ga.shell_execs == []
    assert ("pgrep", "-f", "charon") in ga.execs


def test_linux_scripted_driver_stop_uses_systemctl_never_scheduled_task():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    driver.stop(ga, 101, persona, instance_id="bkp01-backup-server")
    assert ga.shell_execs == []
    assert any(argv[:1] == ("sh",) and "systemctl stop" in " ".join(argv) for argv in ga.execs)


# ===========================================================================
# 2. Windows scripted client determinism + escaping.
# ===========================================================================

def test_windows_client_render_byte_stable_given_seed():
    persona = PERSONAS["corp-workstation"]
    assert persona.os_family == "windows"
    kwargs = dict(
        instance_id="ws01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=42,
    )
    a = render_scripted_client_windows(persona, **kwargs)
    b = render_scripted_client_windows(persona, **kwargs)
    assert a == b


def test_windows_seed_mask_prevents_int32_overflow(monkeypatch):
    """The `effective_seed()` fold can return any full unsigned 32-bit value
    (up to `0xFFFFFFFF`), which OVERFLOWS .NET's `Int32`-typed
    `[System.Random]::new(...)` constructor on roughly half of all seeds.
    Force a high-bit-set 32-bit value out of `effective_seed` and confirm
    the rendered `$Seed = ...` literal is masked into valid `Int32` range
    (`<= 0x7FFFFFFF`), not embedded raw."""
    from axrange.personas.drivers import scripted as scripted_mod

    overflowing = 0xFFFFFFFF  # > Int32.MaxValue (0x7FFFFFFF) — WOULD overflow.
    monkeypatch.setattr(scripted_mod, "effective_seed", lambda seed, instance_id: overflowing)

    persona = PERSONAS["corp-workstation"]
    src = scripted_mod.render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers={}, seed=1,
    )
    m = re.search(r"\$Seed = (\d+)\s", src)
    assert m, f"no numeric $Seed literal found in:\n{src[:400]}"
    seed_val = int(m.group(1))
    assert seed_val <= 0x7FFFFFFF, (
        f"rendered $Seed literal {seed_val} exceeds Int32.MaxValue -- "
        "[System.Random]::new([int]$Seed) would throw OverflowException "
        "at guest runtime"
    )
    assert seed_val == (overflowing & 0x7FFFFFFF)


def test_windows_seed_mask_is_a_noop_for_an_already_valid_int32(monkeypatch):
    """Sanity companion: a seed already within Int32 range is NOT altered by
    the mask (it's a safety ceiling, not a hash)."""
    from axrange.personas.drivers import scripted as scripted_mod

    monkeypatch.setattr(scripted_mod, "effective_seed", lambda seed, instance_id: 42)
    persona = PERSONAS["corp-workstation"]
    src = scripted_mod.render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers={}, seed=1,
    )
    assert re.search(r"\$Seed = 42\s", src)


def test_windows_client_escapes_single_quote_in_instance_id():
    persona = PERSONAS["corp-workstation"]
    src = render_scripted_client_windows(
        persona, instance_id="ws'01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=1,
    )
    assert "ws''01-corp-workstation" in src
    # And it must not otherwise break the PS literal it's embedded in — the
    # rest of the script must still parse as the expected shape.
    assert "$Instance = 'ws''01-corp-workstation'" in src


def test_windows_client_escapes_single_quote_in_peer_ip_and_persona_id():
    """LES-036, defense-in-depth: an unusual peer_ip/persona_id containing a
    literal `'` must not break out of the single-quoted PS literal."""
    persona = Persona(
        id="weird'persona", os_family="windows", drive="scripted",
        emits=(
            Emit(proto="dns", ports=(53,), peer_role="dns-resolver"),
        ),
    )
    src = render_scripted_client_windows(
        persona, instance_id="ws01-x",
        peers={"dns-resolver": "10.0.0.9'"}, seed=1,
    )
    assert "weird''persona" in src
    assert "10.0.0.9''" in src


def test_scheduled_task_start_block_label_escapes_dollar_and_backtick():
    src = scheduled_task_start_block(
        "axr-persona-test", "powershell.exe", "-File test.ps1",
        label="persona x $HOME `backtick` end",
    )
    assert "`$HOME" in src
    assert "``backtick``" in src
    assert "$HOME" not in src.replace("`$HOME", "")  # no UN-escaped $HOME left


def test_scheduled_task_start_block_has_unbounded_execution_time_limit():
    """SHOULD-FIX (review, 2026-07-25): with no `-Settings`,
    `Register-ScheduledTask` inherits the default `ExecutionTimeLimit=PT72H`
    -- Task Scheduler would kill the persona client's infinite-loop process
    after 3 days, with no relaunch until the next reboot (`AtStartup`). Must
    pass `-Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit
    ([TimeSpan]::Zero) ...)` for parity with the Linux `systemd-run
    --collect` unit, which has no such cap. `-RestartCount` must NOT be set
    (a deliberate, separate parity decision -- the Linux unit is
    `Restart=no` too)."""
    src = scheduled_task_start_block(
        "axr-persona-test", "powershell.exe", "-File test.ps1", label="persona x",
    )
    assert "New-ScheduledTaskSettingsSet" in src
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in src
    assert "-Settings $AxrSettings" in src
    assert "-RestartCount" not in src
    assert "-RestartInterval" not in src
    # LES-022: this is a cmdlet call (not manual object construction passed
    # as a method argument), so New-Object is correctly NOT used here either.
    assert "New-Object" not in src


def test_no_new_object_anywhere_in_windows_persona_rendered_powershell():
    """LES-022: `[Type]::new(...)`, never `New-Object`, across every
    Windows-persona-rendered PowerShell surface (client + launch block)."""
    persona = PERSONAS["corp-workstation"]
    client = render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=1,
    )
    launch = scheduled_task_start_block(
        instance_paths("ws01-corp-workstation", os_family="windows")["unit"],
        "powershell.exe", '-File "x.ps1"', label="persona x",
    )
    for name, src in (("client", client), ("launch", launch)):
        # Ignore comment lines (a docstring/comment may legitimately
        # mention "New-Object" while explaining why it's NOT used, e.g.
        # "[Type]::new(...), not New-Object (LES-022)."); only a real
        # invocation (the token followed by an argument) is a violation.
        code_lines = [
            line for line in src.splitlines()
            if not line.strip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        assert not re.search(r"New-Object\s+\S", code_only), (
            f"{name} invokes New-Object (LES-022 violation)"
        )
    assert "[System.Net.Sockets.TcpClient]::new(" in client
    assert "[System.Random]::new(" in client


# ===========================================================================
# 3. os_family dispatch, both drivers, both ways.
# ===========================================================================

def test_scripted_driver_windows_persona_never_touches_bash_or_systemd():
    """The Windows path's ONE `ga.exec` call is the `powershell -File
    launch.ps1` invocation itself (there is no raw shell/systemd call
    anywhere in the Windows branch)."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["corp-workstation"]
    driver = ScriptedDriver()
    driver.start(
        ga, 101, persona, instance_id="ws01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=1,
    )
    assert len(ga.execs) == 1
    assert ga.execs[0][0] == "powershell"
    assert not any(argv[:1] == ("bash",) or argv[:1] == ("sh",) for argv in ga.execs)
    assert all("systemd" not in cmd for cmd in ga.shell_execs)


def test_scripted_driver_linux_persona_never_touches_shell_exec():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    driver.start(
        ga, 101, persona, instance_id="bkp01-backup-server",
        peers={"file-server": "10.0.0.6"}, seed=1,
    )
    assert ga.shell_execs == []


def test_real_service_driver_windows_uses_get_service_never_systemctl():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["domain-controller"]
    assert persona.os_family == "windows"
    driver = RealServiceDriver()
    driver.start(ga, 103, persona, instance_id="dc01-domain-controller")
    assert ga.execs == []
    assert any("Get-Service" in c for c in ga.shell_execs)


def test_real_service_driver_linux_uses_systemctl_never_get_service():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["file-server"]
    driver = RealServiceDriver()
    driver.start(ga, 103, persona, instance_id="fs01-file-server")
    assert ga.shell_execs == []
    assert any(argv[:2] == ("systemctl", "is-active") for argv in ga.execs)


# --- FIXED (review, 2026-07-25): os_family dispatch is now fail-loud.
def test_unexpected_os_family_raises_instead_of_silently_taking_the_linux_branch():
    """FLIPPED from `test_GAP_unexpected_os_family_silently_takes_the_linux_branch`
    now that the fix landed: both `ScriptedDriver.start`/`stop`/`heartbeat`
    and `RealServiceDriver._check` dispatch `if "windows": ... elif "linux":
    ... else: raise unsupported_os_family_error(...)` — an unrecognized
    `os_family` (a typo, an unsupported OS, an empty string reaching the
    driver despite PSN-020's catalog-time enum check) must now fail loud,
    never silently take the Linux branch."""
    from axrange.personas.drivers import PersonaDriverError

    ga = _FakeGA(run_exit=0)
    bogus = Persona(id="bogus", os_family="plan9", drive="scripted")
    driver = ScriptedDriver()
    with pytest.raises(PersonaDriverError, match="plan9"):
        driver.start(ga, 101, bogus, instance_id="x01-bogus", peers={})
    assert ga.execs == []
    assert ga.shell_execs == []


def test_unexpected_os_family_raises_on_scripted_stop_and_heartbeat():
    ga = _FakeGA(run_exit=0)
    bogus = Persona(id="bogus", os_family="plan9", drive="scripted")
    driver = ScriptedDriver()
    from axrange.personas.drivers import PersonaDriverError

    with pytest.raises(PersonaDriverError, match="plan9"):
        driver.stop(ga, 101, bogus, instance_id="x01-bogus")
    with pytest.raises(PersonaDriverError, match="plan9"):
        driver.heartbeat(ga, 101, bogus, instance_id="x01-bogus")
    assert ga.execs == []
    assert ga.shell_execs == []


def test_unexpected_os_family_raises_on_real_service_start_and_heartbeat():
    """An unsupported `os_family` is a driver MISCONFIGURATION, not a
    legitimate "service not running" answer -- it raises from BOTH
    `start` and `heartbeat` (the ABC's "heartbeat never raises" promise is
    about liveness, not about os_family validity; `RealServiceDriver.
    heartbeat` propagates `_check`'s raise here exactly like `start` does,
    same as the pre-existing "no service_name/process_match bound" case
    stays a non-raising `{"alive": False}` -- this is a DIFFERENT, harder
    failure class)."""
    ga = _FakeGA(run_exit=0)
    bogus = Persona(id="bogus", os_family="plan9", drive="real-service")
    driver = RealServiceDriver()
    from axrange.personas.drivers import PersonaDriverError

    with pytest.raises(PersonaDriverError, match="plan9"):
        driver.start(ga, 101, bogus, instance_id="x01-bogus", service_name="whatever")
    with pytest.raises(PersonaDriverError, match="plan9"):
        driver.heartbeat(
            ga, 101, bogus, instance_id="x01-bogus", service_name="whatever",
        )
    assert ga.execs == []
    assert ga.shell_execs == []


# ===========================================================================
# 4. Scheduled Task stop/heartbeat + the flagged _stop_instances gap.
# ===========================================================================

def test_windows_scripted_stop_genuinely_unregisters_scheduled_task():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["corp-workstation"]
    driver = ScriptedDriver()
    r = driver.stop(ga, 101, persona, instance_id="ws01-corp-workstation")
    assert r["stopped"] is True
    assert ga.execs == []  # stop must not fall through to the bash/systemctl path
    joined = " ".join(ga.shell_execs)
    assert "Stop-ScheduledTask" in joined
    assert "Unregister-ScheduledTask" in joined
    assert "systemctl" not in joined


def test_windows_scripted_heartbeat_reads_scheduled_task_state():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["corp-workstation"]
    driver = ScriptedDriver()
    hb = driver.heartbeat(ga, 101, persona, instance_id="ws01-corp-workstation")
    assert hb["alive"] is True
    assert any("Get-ScheduledTask" in c for c in ga.shell_execs)
    assert ga.execs == []


def test_stop_instances_recovers_real_persona_os_family_from_catalog(monkeypatch):
    """FLIPPED from `test_GAP_stop_instances_hardcodes_linux_os_family_leaking_windows_tasks`
    now that the fix landed: `axrange.o2control.traffic._stop_instances`'s
    REAL (non-test-seam) fallback branch now looks up `persona_id` in the
    catalog (falling back to a `persona_registry.get(...) or Persona(...,
    os_family=<recovered from the persisted op input>, ...)` placeholder
    only when the id isn't a real catalog persona) BEFORE building the
    driver-facing `Persona`, so a WINDOWS persona (`domain-controller`)
    routed through this path is no longer silently driven down the Linux
    `stop_unit`/`systemctl stop` branch -- its Scheduled Task actually gets
    `Unregister-ScheduledTask`'d. Every existing traffic-tier test drives
    stop/destroy through the `_FakePersonaBackend.stop_instance` seam, which
    bypasses this branch entirely -- so this is the one place that exercises
    it directly."""
    from axrange.o2control.traffic import _stop_instances
    from axrange.personas import registry as persona_registry

    real_persona = persona_registry.get("domain-controller")
    assert real_persona is not None and real_persona.os_family == "windows"

    seen: list[str] = []

    class _CapturingDriver:
        def stop(self, ga, vmid, persona, *, instance_id):
            seen.append(persona.os_family)
            return {"stopped": True}

    monkeypatch.setattr(
        "axrange.o2control.traffic.get_driver", lambda drive: _CapturingDriver()
    )

    cfg = SimpleNamespace(default_node="pxtest1", proxmox_verify_tls=False)
    inps = [{
        "instance_id": "dc01-domain-controller",
        "vmid": 100003,
        "drive": "real-service",
        "persona_id": "domain-controller",
    }]
    logs: list[str] = []
    _stop_instances(inps, cfg=cfg, persona_backend=None, log=logs.append)

    assert not any("stop failed" in m for m in logs), logs
    assert seen == ["windows"]


# ===========================================================================
# 5. GAP-032 real-service bindings.
# ===========================================================================

def test_every_real_service_persona_has_a_binding():
    unbound = [
        pid for pid, p in PERSONAS.items()
        if p.drive == "real-service"
        and not p.knobs.get("service_name") and not p.knobs.get("process_match")
    ]
    assert not unbound


def test_real_service_persona_with_no_binding_fails_loud():
    ga = _FakeGA(run_exit=0)
    persona = Persona(id="unbound", os_family="linux", drive="real-service")
    driver = RealServiceDriver()
    from axrange.personas.drivers import PersonaDriverError

    with pytest.raises(PersonaDriverError):
        driver.start(ga, 999, persona, instance_id="x-unbound")


def test_windows_real_service_bindings_are_bare_process_names_not_pgrep_patterns():
    """A Windows real-service persona's `process_match` (if any) must be a
    bare `Get-Process -Name` process name, not a Linux `pgrep -f`
    full-command-line regex/pattern (spaces, wildcards, pipes)."""
    for pid, persona in PERSONAS.items():
        if persona.drive != "real-service" or persona.os_family != "windows":
            continue
        pm = persona.knobs.get("process_match")
        if pm:
            assert not re.search(r"[\s|*]", pm), (
                f"{pid}: process_match {pm!r} looks like a pgrep -f pattern, "
                "not a bare Get-Process -Name value"
            )


def test_the_one_windows_real_service_persona_uses_service_name_not_process_match():
    """Documents the current catalog shape precisely: `domain-controller`
    (the only Windows real-service persona) is bound via `service_name:
    NTDS` -- there is currently no catalog example of a Windows
    `process_match` binding to exercise the bare-name rule against."""
    dc = PERSONAS["domain-controller"]
    assert dc.os_family == "windows"
    assert dc.knobs.get("service_name") == "NTDS"
    assert not dc.knobs.get("process_match")
