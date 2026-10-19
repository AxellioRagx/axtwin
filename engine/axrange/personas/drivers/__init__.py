"""The persona drivers — the uniform start/stop/heartbeat interface the
future Traffic tier drives (docs/WORK_ORDER_persona_library.md §2).

`get_driver(persona.drive)` is the one branch point; callers never need
their own `if drive == "scripted": ...` chain.
"""
from __future__ import annotations

from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    effective_seed,
    heartbeat_scheduled_task,
    heartbeat_unit,
    instance_paths,
    instance_unit,
    safe_instance_name,
    safe_label,
    start_linux_python_client,
    start_windows_powershell_client,
    stop_scheduled_task,
    stop_unit,
    unsupported_os_family_error,
    write_binary_file,
)
from axrange.personas.drivers.browser import BrowserDriver
from axrange.personas.drivers.command import CommandDriver
from axrange.personas.drivers.real_service import RealServiceDriver
from axrange.personas.drivers.replay import ReplayDriver
from axrange.personas.drivers.scripted import ScriptedDriver

_DRIVERS: dict[str, PersonaDriver] = {
    "scripted": ScriptedDriver(),
    "replay": ReplayDriver(),
    "real-service": RealServiceDriver(),
    # 2026-09-02, docs/WORK_ORDER_protocol_faithful_traffic.md: the
    # protocol-faithful sibling of `scripted`. Adding this entry is the
    # ENTIRE execution-side wiring change -- `o2control.traffic` calls
    # `get_driver` twice and has no `drive`-keyed branch of its own.
    "command": CommandDriver(),
    # 2026-09-21, docs/WORK_ORDER_protocol_engines.md §5 E4: the browser
    # engine -- one driver covering DNS + real TLS (real SNI/JA3/JA4) +
    # HTTP/1.1 + HTTP/2 + QUIC through a real headless Chromium. Adding this
    # entry is again the ENTIRE execution-side wiring change.
    "browser": BrowserDriver(),
}


def get_driver(drive: str) -> PersonaDriver:
    """The driver instance for a persona's `drive:` kind. Raises
    PersonaDriverError on an unknown kind (PSN-030 already flags this at
    catalog-validate time; this is the runtime-side fail-loud counterpart)."""
    driver = _DRIVERS.get(drive)
    if driver is None:
        raise PersonaDriverError(
            f"unknown persona drive {drive!r} (expected one of "
            f"{sorted(_DRIVERS)})"
        )
    return driver


__all__ = [
    "PersonaDriver",
    "PersonaDriverError",
    "BrowserDriver",
    "ScriptedDriver",
    "ReplayDriver",
    "RealServiceDriver",
    "CommandDriver",
    "get_driver",
    "effective_seed",
    "heartbeat_scheduled_task",
    "heartbeat_unit",
    "instance_paths",
    "instance_unit",
    "safe_instance_name",
    "safe_label",
    "start_linux_python_client",
    "start_windows_powershell_client",
    "stop_scheduled_task",
    "stop_unit",
    "unsupported_os_family_error",
    "write_binary_file",
]
