"""LibvirtGuestExec -- the `GuestExec` seam over `virDomainQemuAgentCommand`.

The same QGA JSON the Proxmox adapter speaks through PVE's `/agent/*`
endpoints, sent straight to the guest agent through libvirt
(`adapters/libvirt/SPEC.md` Seam table, "guest exec"): `guest-ping`,
`guest-exec` + `guest-exec-status` (exit code, base64 out/err), and file-in
as `guest-file-open` / `guest-file-write` (`buf-b64`) / `guest-file-close`
-- base64 is QGA's own wire convention (`docs/CONVENTIONS.md` §4 "file
content rides base64"), so no in-guest `base64 -d` trampoline is needed
here. The builders below are pure dicts, listed so a recorded exchange in
the suite is the exact JSON a real agent would see.

Bound to one `host` and one guest OS family like the Proxmox `GuestAgent`:
`shell_exec` is `sh -lc` on Linux and PowerShell on Windows.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Callable, Optional

from axrange.adapter_proxmox.guest_agent import GuestExecResult  # the `GuestResult` value type, pure
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = [
    "GuestExecResult",
    "LibvirtGuestExec",
    "qga_exec",
    "qga_exec_status",
    "qga_file_close",
    "qga_file_open",
    "qga_file_write",
    "qga_ping",
    "qga_return",
]

#: `guest-file-write` chunk, decoded bytes; QGA caps a buffer at 48 MiB but a
#: modest chunk keeps each JSON exchange small. Multiple of 3 -> base64 aligned.
_FILE_CHUNK = 48 * 1024
_DEFAULT_PING_TIMEOUT_S = 10


# ---- QGA JSON builders ------------------------------------------------------


def qga_ping() -> dict[str, Any]:
    return {"execute": "guest-ping"}


def qga_exec(argv: list[str]) -> dict[str, Any]:
    if not argv:
        raise AdapterError("guest exec: empty argv", error_class="qga-exec")
    return {
        "execute": "guest-exec",
        "arguments": {"path": argv[0], "arg": list(argv[1:]), "capture-output": True},
    }


def qga_exec_status(pid: int) -> dict[str, Any]:
    return {"execute": "guest-exec-status", "arguments": {"pid": int(pid)}}


def qga_file_open(path: str, mode: str = "w") -> dict[str, Any]:
    return {"execute": "guest-file-open", "arguments": {"path": path, "mode": mode}}


def qga_file_write(handle: int, data: bytes) -> dict[str, Any]:
    return {
        "execute": "guest-file-write",
        "arguments": {"handle": int(handle), "buf-b64": base64.b64encode(data).decode("ascii")},
    }


def qga_file_close(handle: int) -> dict[str, Any]:
    return {"execute": "guest-file-close", "arguments": {"handle": int(handle)}}


def qga_return(reply: dict[str, Any], *, what: str) -> Any:
    """The `return` of a QGA reply; an `error` reply raises."""
    if "error" in reply and reply["error"]:
        err = reply["error"]
        desc = err.get("desc") if isinstance(err, dict) else err
        raise AdapterError(f"guest agent {what}: {desc}", error_class="qga-error")
    return reply.get("return")


def _b64_text(v: Any) -> str:
    if not v:
        return ""
    try:
        return base64.b64decode(v).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return str(v)


# ---- the channel ---------------------------------------------------------------


class LibvirtGuestExec:
    """QGA operations against guests on one host over one connection."""

    def __init__(
        self,
        conn: Any,
        node: str,
        *,
        os_family: str = "linux",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.conn = conn
        self.node = node
        self.os_family = (os_family or "linux").lower()
        self._sleep = sleep

    @property
    def client(self) -> Any:
        """The bundle's shared connection (the factory's one-connection test)."""
        return self.conn

    @property
    def is_windows(self) -> bool:
        return self.os_family == "windows"

    def _domain(self, guest: int | str) -> Any:
        dom = self.conn.domain_by_uuid(str(guest))
        if dom is None:
            raise AdapterError(f"guest {guest} is not a defined domain", error_class="qga-unreachable")
        return dom

    def _cmd(self, guest: int | str, command: dict[str, Any], *, timeout_s: int | None) -> Any:
        dom = self._domain(guest)
        reply = self.conn.agent_command(dom, command, timeout_s)
        return qga_return(reply, what=str(command.get("execute")))

    # ---- readiness -----------------------------------------------------
    def ping_once(self, guest: int | str, *, timeout_s: Optional[int] = None) -> None:
        self._cmd(guest, qga_ping(), timeout_s=timeout_s or _DEFAULT_PING_TIMEOUT_S)

    def wait_ready(self, guest: int | str, *, timeout_s: int = 180) -> None:
        deadline = time.time() + timeout_s
        last: Exception | None = None
        while True:
            try:
                self.ping_once(guest)
                return
            except (AdapterError, AdapterTimeout) as e:
                last = e
            if time.time() >= deadline:
                break
            self._sleep(3)
        raise AdapterTimeout(
            f"guest agent for {guest} did not respond in {timeout_s}s" + (f" ({last})" if last else "")
        )

    # ---- exec ----------------------------------------------------------
    def exec(self, guest: int | str, argv: list[str], *, timeout_s: int = 300) -> GuestExecResult:
        started = self._cmd(guest, qga_exec(argv), timeout_s=30)
        if not isinstance(started, dict) or "pid" not in started:
            raise AdapterError(f"guest-exec returned no pid for {argv!r}: {started!r}", error_class="qga-exec")
        pid = int(started["pid"])
        deadline = time.time() + timeout_s
        while True:
            status = self._cmd(guest, qga_exec_status(pid), timeout_s=30)
            if isinstance(status, dict) and status.get("exited"):
                return GuestExecResult(
                    exit_code=int(status.get("exitcode", -1)),
                    stdout=_b64_text(status.get("out-data")),
                    stderr=_b64_text(status.get("err-data")),
                )
            if time.time() >= deadline:
                break
            self._sleep(2)
        raise AdapterTimeout(f"guest exec {argv!r} on {guest} did not finish in {timeout_s}s")

    def shell_exec(self, guest: int | str, command: str, *, timeout_s: int = 300) -> GuestExecResult:
        if self.is_windows:
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = ["sh", "-lc", command]
        return self.exec(guest, argv, timeout_s=timeout_s)

    # ---- file in -------------------------------------------------------
    def write_file(self, guest: int | str, path: str, content: str, *, mode: Optional[str] = None) -> None:
        raw = content.encode("utf-8")
        handle = self._cmd(guest, qga_file_open(path, "w"), timeout_s=30)
        try:
            h = int(handle)
        except (TypeError, ValueError):
            raise AdapterError(f"guest-file-open {path!r} returned no handle: {handle!r}", error_class="qga-write")
        written = 0
        try:
            for i in range(0, len(raw), _FILE_CHUNK) if raw else [0]:
                chunk = raw[i : i + _FILE_CHUNK]
                res = self._cmd(guest, qga_file_write(h, chunk), timeout_s=60)
                written += int(res.get("count", 0)) if isinstance(res, dict) else 0
        finally:
            self._cmd(guest, qga_file_close(h), timeout_s=30)
        if written != len(raw):
            raise AdapterError(
                f"writing {path} in {guest} delivered {written} bytes, expected {len(raw)}",
                error_class="qga-write",
            )
        if mode and not self.is_windows:
            res = self.exec(guest, ["chmod", mode, path], timeout_s=30)
            if not res.ok:
                raise AdapterError(f"chmod {mode} {path} in {guest} failed (exit {res.exit_code})", error_class="qga-write")
