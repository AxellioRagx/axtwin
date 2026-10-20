"""The `real-service` driver (docs/design/07-traffic-personas.md,
docs/WORK_ORDER_persona_library.md §2): a no-op emitter. A real-service
persona's own daemon (a DC, a DB, a web server — provisioned by the twin's
`layers.apps`, not by this driver) already generates its own ambient
chatter; there is nothing to render or launch. `start()` only ASSERTS the
expected daemon is present and running — fail-loud if absent, per the work
order ("fail-loud if absent"), since a silently-absent daemon means zero
persona traffic with a green "started" result otherwise (the same
silent-success class LES-001 warns against for capture).

`stop()` is a deliberate no-op: this driver never started anything (the
daemon is the twin's own service, provisioned independently), so there is
nothing for it to stop — it must NEVER issue a `systemctl stop` (or
`Stop-Service`) against the asserted service, which would take down a twin
component this driver doesn't own.

**Windows port (2026-07-25):** the ASSERT mechanism dispatches on
`persona.os_family` — `Get-Service`/`Get-Process` (over `ga.shell_exec`,
PowerShell) on Windows instead of `systemctl is-active`/`pgrep -f` (over
`ga.exec`, a raw Linux argv). `stop()` stays OS-agnostic (still a pure
no-op either way — there is nothing to dispatch, it never touches the
guest at all).
"""
from __future__ import annotations

from typing import Any

from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    unsupported_os_family_error,
)
from axrange.personas.schema import Persona


class RealServiceDriver(PersonaDriver):
    """Asserts (never manages) an already-provisioned daemon."""

    def start(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None = None,
        seed: Any = None,
        service_name: str | None = None,
        process_match: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        service_name = service_name or persona.knobs.get("service_name")
        process_match = process_match or persona.knobs.get("process_match")
        if not service_name and not process_match:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): real-service driver needs "
                "either service_name (systemd unit / Windows service name) or "
                "process_match (pgrep -f pattern on Linux, a Get-Process -Name "
                "pattern on Windows) to assert against — the twin binds this at "
                "provisioning time (docs/design/07-traffic-personas.md, "
                "twin_configs)"
            )
        return self._assert(ga, vmid, persona, instance_id, service_name, process_match)

    def _assert(
        self, ga: Any, vmid: int, persona: Persona, instance_id: str,
        service_name: str | None, process_match: str | None,
    ) -> dict[str, Any]:
        ok, note = self._check(ga, vmid, persona, instance_id, service_name, process_match)
        if not ok:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): {note} on vmid={vmid} — "
                "real-service personas require the daemon to already be running "
                "(fail-loud, not a silent no-op)"
            )
        return {"asserted": True, **({"service_name": service_name} if service_name
                                      else {"process_match": process_match})}

    def _check(
        self, ga: Any, vmid: int, persona: Persona, instance_id: str,
        service_name: str | None, process_match: str | None,
    ) -> tuple[bool, str]:
        """Run the OS-appropriate presence check. Returns (ok, description) —
        `description` is only used to build the fail-loud message above, not
        a general-purpose status string. Fail-loud (review fix, 2026-07-25)
        on any `os_family` other than the two supported values — a bare
        if/else here would silently drive an unrecognized OS down the Linux
        `systemctl`/`pgrep` branch instead of raising."""
        if persona.os_family == "windows":
            return self._check_windows(ga, vmid, service_name, process_match)
        elif persona.os_family == "linux":
            return self._check_linux(ga, vmid, service_name, process_match)
        raise unsupported_os_family_error(persona, instance_id)

    def _check_linux(
        self, ga: Any, vmid: int,
        service_name: str | None, process_match: str | None,
    ) -> tuple[bool, str]:
        if service_name:
            res = ga.exec(
                vmid, ["systemctl", "is-active", "--quiet", service_name], timeout_s=30,
            )
            return res.ok, f"expected service {service_name!r} is not active"
        res = ga.exec(vmid, ["pgrep", "-f", process_match], timeout_s=30)
        return res.ok, f"no process matching {process_match!r}"

    def _check_windows(
        self, ga: Any, vmid: int,
        service_name: str | None, process_match: str | None,
    ) -> tuple[bool, str]:
        """`Get-Service -Name` (a Windows service, e.g. `NTDS`) or
        `Get-Process -Name` (a running executable's base name, e.g. `w3wp` —
        NOTE this is `Get-Process` NAME-MATCH semantics, not `pgrep -f`'s
        full-command-line REGEX semantics; a `process_match` value bound to
        a Windows persona must be a bare process name, not a Linux-style
        `pgrep -f` pattern) over `ga.shell_exec` (PowerShell), matching the
        work order's "Get-Service <name> / Get-Process instead of systemctl
        is-active/pgrep -f" instruction."""
        if service_name:
            svc_sq = service_name.replace("'", "''")
            ps = (
                f"$s = Get-Service -Name '{svc_sq}' -ErrorAction SilentlyContinue\n"
                "if ($s -and $s.Status -eq 'Running') { exit 0 } else { exit 1 }\n"
            )
            res = ga.shell_exec(vmid, ps, timeout_s=30)
            return res.ok, f"expected service {service_name!r} is not Running"
        proc_sq = process_match.replace("'", "''")
        ps = (
            f"$p = Get-Process -Name '{proc_sq}' -ErrorAction SilentlyContinue\n"
            "if ($p) { exit 0 } else { exit 1 }\n"
        )
        res = ga.shell_exec(vmid, ps, timeout_s=30)
        return res.ok, f"no process matching {process_match!r}"

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        # Deliberate no-op (OS-agnostic): this driver never started the
        # daemon (the twin's own provisioning did), so it must never stop it
        # either, on either OS.
        return {"stopped": True, "note": "real-service: nothing started, nothing stopped"}

    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str,
        service_name: str | None = None, process_match: str | None = None,
    ) -> dict[str, Any]:
        service_name = service_name or persona.knobs.get("service_name")
        process_match = process_match or persona.knobs.get("process_match")
        if not service_name and not process_match:
            return {"alive": False, "note": "no service_name/process_match to check"}
        ok, _note = self._check(ga, vmid, persona, instance_id, service_name, process_match)
        return {"alive": bool(ok)}
