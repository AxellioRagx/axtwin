"""GuestAgent — the qemu-guest-agent (QGA) exec/ping/file-write channel.

Factored out of `rest.py`'s wait ops so both the infra adapter (`wait-guest-agent`,
`wait-os-ready`) and the config-execution backend (`axrange.config_exec`) share
one QGA implementation. The orchestrator can't reach guests on isolated scenario
SDN networks directly, so QGA is *the* in-guest channel — used here to (a) probe
readiness, (b) push files into a guest, and (c) run commands in a guest and read
their exit code + output (docs/PLANNER.md §4.3, docs/ADAPTER.md §3).

REST shape (PVE API): `POST …/agent/ping`, `POST …/agent/exec` (returns a pid),
`GET …/agent/exec-status?pid=<n>`. `agent/exec` also has an `input-data` (stdin)
parameter which this module deliberately does NOT use — it is broken on PVE 9.2
and capped at 64 KiB besides (LES-002, and `exec`/`write_file` below).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from axrange.planner.executor import AdapterError, AdapterTimeout

if TYPE_CHECKING:
    from axrange.adapter_proxmox.rest import RestClient


# --- the `agent/exec` size ceiling (measured, not guessed) -------------------
#
# Measured on dev45 (PVE 9.2, pve-manager/9.2.2) 2026-09-21 by POSTing
# `/bin/true <arg>` at increasing arg sizes against a running guest:
#
#   * a SINGLE `command` argument of 131071 bytes (128 KiB - 1) is accepted;
#     131072 bytes is NOT — the request dies mid-send with `HTTP 596 Broken
#     pipe` (AnyEvent::HTTP's "request phase" error code, raised inside
#     pveproxy's proxy hop to pvedaemon). No error body, no hint, and it looks
#     exactly like a network fault — which is how a 161 KB `run-script` payload
#     burned a live deploy before this was found.
#   * the TOTAL body is bounded much higher: 2 x 100 KB args (200 KB body) and
#     8 x 60 KB args (480 KB body) both succeed. The ceiling there is
#     pve-http-server's `$limit_max_post = 512 * 1024` (which answers a clean
#     501, not a broken pipe).
#   * `input-data` is capped at 64 KiB by the API schema even where it works —
#     which it does not on PVE 9.2 (see `exec` below).
#
# So: split a large payload across MANY arguments of MODERATE size, in as few
# requests as the body budget allows. Both constants are multiples of 4 so that
# every chunk boundary is base64-aligned and each request's chunks decode on
# their own.
_MAX_ARG_BYTES = 96 * 1024       # per argv element (measured hard limit 131071)
_MAX_B64_PER_EXEC = 256 * 1024   # per request (measured hard limit ~512 KiB)


@dataclass
class GuestExecResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class GuestAgent:
    """QGA operations against one VM on one node. Construct per use; cheap."""

    def __init__(
        self, client: RestClient, node: str, *, os_family: str = "linux"
    ) -> None:
        self.client = client
        self.node = node
        # The in-guest shell + file-write differ by OS: `sh`/`cat >` on Linux,
        # PowerShell on Windows (W1, docs/SLICE_WINDOWS_AD.md §5). Default `linux`
        # keeps every existing caller (rest.py wait ops, the config backend)
        # byte-for-byte unchanged.
        self.os_family = (os_family or "linux").lower()

    @property
    def is_windows(self) -> bool:
        return self.os_family == "windows"

    # ---- readiness --------------------------------------------------------

    def ping_once(self, vmid: int, *, timeout_s: int | None = None) -> None:
        """One agent ping. Raises AdapterError if the agent doesn't answer.

        POST, not GET — GET returns HTTP 501 (surfaced 2026-05-29).
        `timeout_s` None keeps the REST client's default (deploy-time
        callers); a health read passes a short one."""
        if timeout_s is None:
            self.client.request("POST", f"/nodes/{self.node}/qemu/{vmid}/agent/ping")
        else:
            self.client.request(
                "POST", f"/nodes/{self.node}/qemu/{vmid}/agent/ping", timeout_s=timeout_s,
            )

    def wait_ready(self, vmid: int, *, timeout_s: int = 180) -> None:
        """Poll the agent until it answers or the budget runs out."""
        deadline = time.time() + timeout_s
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self.ping_once(vmid)
                return
            except (AdapterError, AdapterTimeout) as e:
                last = e
                time.sleep(3)
        raise AdapterTimeout(
            f"guest agent for vmid={vmid} did not respond in {timeout_s}s"
            + (f" ({last})" if last else "")
        )

    # ---- exec -------------------------------------------------------------

    def exec(
        self,
        vmid: int,
        argv: list[str],
        *,
        timeout_s: int = 300,
    ) -> GuestExecResult:
        """Run `argv` in the guest; block until it exits or `timeout_s`.

        Returns exit code + captured stdout/stderr. Raises AdapterError if the
        agent never returns a pid, AdapterTimeout if the command doesn't finish.

        No `input_data`/stdin parameter, deliberately (LES-002, code-review
        finding #7, 2026-07-07): PVE 9.2's `agent/exec` `input-data` body param
        500s on non-ASCII content (`Wide character ... Agent.pm line 135`). Every
        real caller already delivers file content as base64 embedded in the
        command args instead (see `write_file` below); removing the parameter
        here closes off the dormant trap of a future caller reaching for the
        broken path.
        """
        base = f"/nodes/{self.node}/qemu/{vmid}/agent"
        body: dict[str, Any] = {"command": argv}
        started = self.client.request("POST", f"{base}/exec", body=body)
        if not isinstance(started, dict) or "pid" not in started:
            raise AdapterError(
                f"agent exec returned no pid for {argv!r}: {started!r}",
                error_class="qga-exec",
            )
        pid = int(started["pid"])
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = self.client.request(
                "GET", f"{base}/exec-status", params={"pid": pid}
            )
            if isinstance(status, dict) and status.get("exited"):
                return GuestExecResult(
                    exit_code=int(status.get("exitcode", -1)),
                    stdout=str(status.get("out-data", "") or ""),
                    stderr=str(status.get("err-data", "") or ""),
                )
            time.sleep(2)
        raise AdapterTimeout(
            f"agent exec {argv!r} on vmid={vmid} did not finish in {timeout_s}s"
        )

    def shell_exec(
        self,
        vmid: int,
        command: str,
        *,
        timeout_s: int = 300,
    ) -> GuestExecResult:
        """Run a shell *command string* in the guest's native shell — `sh -lc` on
        Linux, `powershell -Command` on Windows. Use this (not `exec`) when the
        higher-level op is shell text rather than an explicit argv, so the same
        op works on either OS (W1, docs/SLICE_WINDOWS_AD.md §5)."""
        if self.is_windows:
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = ["sh", "-lc", command]
        return self.exec(vmid, argv, timeout_s=timeout_s)

    # ---- file write -------------------------------------------------------

    def write_file(
        self, vmid: int, path: str, content: str, *, mode: str | None = None
    ) -> None:
        """Write `content` to `path` in the guest, base64-embedded in the exec
        command (never `input-data` — LES-002). Linux: `base64 -d > path`, with
        the payload chunked across argv elements and, beyond ~192 KiB, across
        several requests + an in-guest byte-count check. Windows: a PowerShell
        `[Convert]::FromBase64String` -> `WriteAllBytes`, UTF-8 no-BOM (`mode`
        is a Linux concept — ignored). Raises AdapterError on a non-zero exit,
        on a short chunked delivery, or on a Windows payload over the measured
        single-argument ceiling."""
        if self.is_windows:
            import base64 as _b64

            # Base64 the UTF-8 content into the COMMAND (decoded in-guest with
            # [Convert]::FromBase64String -> WriteAllBytes), NOT via `input-data`.
            # Same PVE-9.2 `agent/exec` wide-character regression as the Linux path
            # below (encode_base64 of input-data dies at Agent.pm:135): an input-data
            # write of any non-ASCII content (a Sysmon XML, an em-dash comment) 500s.
            # base64 is pure ASCII in the arg array, sidestepping it; WriteAllBytes
            # writes the exact UTF-8 bytes (no BOM). Surfaced dev45 2026-06-24.
            esc = path.replace("'", "''")  # PowerShell single-quote escape
            b64 = _b64.b64encode(content.encode("utf-8")).decode("ascii")
            ps = (
                f"$p='{esc}';$b='{b64}';"
                "[IO.File]::WriteAllBytes($p,[Convert]::FromBase64String($b))"
            )
            # One argv element, so the same measured ceiling applies (see the
            # module constants). Not chunked here — no Windows payload is near
            # it — but refused LOUDLY rather than left to surface as an
            # unexplained `HTTP 596 Broken pipe` on a live deploy.
            if len(ps) >= 131072:
                raise AdapterError(
                    f"writing {path} in vmid={vmid} (windows): the PowerShell "
                    f"command is {len(ps)} bytes, over the measured "
                    "agent/exec single-argument ceiling of 131071; shrink or "
                    "compress the payload (the Linux path chunks instead)",
                    error_class="qga-write",
                )
            res = self.exec(
                vmid,
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                timeout_s=60,
            )
            if not res.ok:
                raise AdapterError(
                    f"writing {path} in vmid={vmid} (windows) failed "
                    f"(exit {res.exit_code}): {(res.stderr or res.stdout)[:200]}",
                    error_class="qga-write",
                )
            return

        import base64
        import shlex

        # Deliver the content as base64 EMBEDDED IN THE COMMAND (decoded in-guest
        # with `base64 -d`), NOT via the agent's `input-data` parameter.
        #
        # Why: PVE 9.2 regressed `agent/exec` input-data handling — it dies with
        # "Wide character in subroutine entry at PVE/QemuServer/Agent.pm line 135"
        # (`encode_base64($input_data)`) on content the API layer flags as wide,
        # 500-ing the request. Surfaced on dev45 (PVE 9.2) where every
        # verify-capture / config-class file write failed, while the same path
        # works on PVE 8.4 (pxtwin1). Routing the payload through the command
        # `arg` array (base64 is pure ASCII; args don't pass through line 135)
        # sidesteps the regression entirely and is encoding-proof for any content.
        # The base64 is CHUNKED across argv elements (and, for a very large
        # payload, across several requests) because a single argument over
        # 131071 bytes is refused mid-send — see the measured ceiling at the top
        # of this module. `printf %s "$@"` re-concatenates the chunks in the
        # guest before `base64 -d` sees them, so the decode is of the whole
        # stream; every chunk length is a multiple of 4, so a per-request decode
        # is byte-exact too.
        raw = content.encode("utf-8")
        b64 = base64.b64encode(raw).decode("ascii")
        chunks = [
            b64[i : i + _MAX_ARG_BYTES] for i in range(0, len(b64), _MAX_ARG_BYTES)
        ] or [""]  # empty content still writes an empty file
        per_exec = max(1, _MAX_B64_PER_EXEC // _MAX_ARG_BYTES)
        batches = [chunks[i : i + per_exec] for i in range(0, len(chunks), per_exec)]
        q = shlex.quote(path)
        for n, batch in enumerate(batches):
            # `>` for the first request, `>>` after: a retry of the whole write
            # starts from a truncated file rather than appending to a corpse.
            redirect = ">" if n == 0 else ">>"
            res = self.exec(
                vmid,
                ["sh", "-c", f'printf %s "$@" | base64 -d {redirect} {q}', "sh", *batch],
                timeout_s=60,
            )
            if not res.ok:
                where = f" (chunk {n + 1}/{len(batches)})" if len(batches) > 1 else ""
                raise AdapterError(
                    f"writing {path} in vmid={vmid} failed{where} "
                    f"(exit {res.exit_code}): {(res.stderr or res.stdout)[:200]}",
                    error_class="qga-write",
                )
        if len(batches) > 1:
            # A multi-request write is the one shape that can end up silently
            # SHORT (a request that "succeeded" but delivered nothing). Prove
            # the byte count in-guest instead of assuming it (LES-001).
            res = self.exec(vmid, ["sh", "-c", f"wc -c < {q}"], timeout_s=60)
            got = res.stdout.strip() if res.ok else ""
            if got != str(len(raw)):
                raise AdapterError(
                    f"writing {path} in vmid={vmid} delivered {got or '?'} bytes, "
                    f"expected {len(raw)} (chunked write over {len(batches)} "
                    "requests did not land whole)",
                    error_class="qga-write",
                )
        if mode:
            res = self.exec(
                vmid, ["chmod", mode, path], timeout_s=30
            )
            if not res.ok:
                raise AdapterError(
                    f"chmod {mode} {path} in vmid={vmid} failed (exit {res.exit_code})",
                    error_class="qga-write",
                )
