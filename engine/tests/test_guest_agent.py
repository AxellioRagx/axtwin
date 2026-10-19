"""Tests for the OS-aware QGA channel (`GuestAgent`) — W1.1, the Windows
substrate's in-guest shell/file-write (docs/SLICE_WINDOWS_AD.md §5).

The config backend drives `GuestAgent` through a fake in its own tests; here we
exercise the real channel against a fake RestClient, asserting the Linux path is
unchanged and the Windows path uses PowerShell.
"""
from __future__ import annotations

import base64

from axrange.adapter_proxmox.guest_agent import GuestAgent


class _FakeRest:
    """Records agent REST calls; canned exec → pid 1, exec-status → exit 0."""

    def __init__(self, exitcode: int = 0):
        self.calls: list[tuple] = []
        self.exitcode = exitcode

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if method == "POST" and path.endswith("/agent/exec"):
            return {"pid": 1}
        if method == "GET" and path.endswith("/agent/exec-status"):
            return {"exited": True, "exitcode": self.exitcode, "out-data": "", "err-data": ""}
        return {}

    def execs(self) -> list[dict]:
        return [c[3] for c in self.calls if c[0] == "POST" and c[1].endswith("/agent/exec")]


class _GuestRest(_FakeRest):
    """A fake that EMULATES the guest side of a chunked write: it decodes each
    request's chunks into a virtual file (truncating or appending as the
    redirect says) and answers `wc -c` from it. That makes the round trip —
    not just the request shape — the thing under test."""

    def __init__(self) -> None:
        super().__init__()
        self.file = b""
        self._out = ""

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if method == "POST" and path.endswith("/agent/exec"):
            argv = body["command"]
            script = argv[2] if len(argv) > 2 else ""
            if "base64 -d" in script:
                blob = base64.b64decode("".join(argv[4:]))
                self.file = self.file + blob if " >> " in script else blob
            self._out = f"{len(self.file)}\n" if "wc -c" in script else ""
            return {"pid": 1}
        if method == "GET" and path.endswith("/agent/exec-status"):
            return {
                "exited": True,
                "exitcode": self.exitcode,
                "out-data": self._out,
                "err-data": "",
            }
        return {}


def test_linux_write_file_base64_in_command():
    import base64

    rest = _FakeRest()
    ga = GuestAgent(rest, "n1")  # default os_family=linux
    ga.write_file(7, "/tmp/f.sh", "echo hi", mode="0755")
    execs = rest.execs()
    # The content is delivered as base64 EMBEDDED IN THE COMMAND (decoded in
    # guest with `base64 -d`), NOT via input-data — sidesteps the PVE 9.2
    # agent/exec encode_base64 "Wide character" regression (Agent.pm:135).
    # It rides in the argv TAIL (`sh -c '…"$@"…' sh <chunk…>`), never inside the
    # command text: one argument may not exceed 131071 bytes, so a large payload
    # has to be splittable (see the chunking tests below).
    argv = execs[0]["command"]
    assert argv[:2] == ["sh", "-c"]
    assert "base64 -d > /tmp/f.sh" in argv[2]
    assert argv[3] == "sh"  # $0, so "$@" is exactly the chunks
    assert argv[4:] == [base64.b64encode(b"echo hi").decode()]
    assert "input-data" not in execs[0]  # the failing param is gone
    # ...followed by a chmod.
    assert any(e["command"][:2] == ["chmod", "0755"] for e in execs)


def test_linux_write_file_chunks_a_payload_over_the_argument_ceiling():
    """A single `command` argument over 131071 bytes is refused mid-send by
    PVE (`HTTP 596 Broken pipe`, measured on dev45/PVE 9.2 2026-09-21). Every
    chunk must therefore stay under that, and each must be base64-aligned so
    the per-request `base64 -d` is byte-exact."""
    from axrange.adapter_proxmox import guest_agent as ga_mod

    rest = _GuestRest()
    ga = GuestAgent(rest, "n1")
    content = "x" * 300_000  # ~400 KB of base64 — several requests' worth
    ga.write_file(7, "/tmp/big", content)
    assert rest.file.decode() == content  # the emulated guest got it whole
    execs = rest.execs()
    writes = [e for e in execs if "base64 -d" in e["command"][2]]
    assert len(writes) > 1, "a 300 KB payload must not be one request"
    rebuilt = ""
    for n, e in enumerate(writes):
        argv = e["command"]
        for chunk in argv[4:]:
            assert len(chunk) <= ga_mod._MAX_ARG_BYTES < 131072
            rebuilt += chunk
        # first request truncates, the rest append
        assert (" > " in argv[2]) == (n == 0)
        assert (" >> " in argv[2]) == (n > 0)
        assert len("".join(argv[4:])) % 4 == 0  # decodable on its own
    assert base64.b64decode(rebuilt).decode() == content


def test_linux_chunked_write_verifies_the_byte_count_in_guest():
    import pytest

    from axrange.planner.executor import AdapterError

    class _ShortRest(_FakeRest):
        """Every write 'succeeds' but the file ends up short — the silent
        truncation a multi-request delivery is the only shape that can hit."""

        def request(self, method, path, *, params=None, body=None):
            self.calls.append((method, path, params, body))
            if method == "POST" and path.endswith("/agent/exec"):
                self._last = body["command"]
                return {"pid": 1}
            if method == "GET" and path.endswith("/agent/exec-status"):
                out = "12\n" if "wc -c" in self._last[2] else ""
                return {"exited": True, "exitcode": 0, "out-data": out, "err-data": ""}
            return {}

    ga = GuestAgent(_ShortRest(), "n1")
    with pytest.raises(AdapterError, match="did not land whole"):
        ga.write_file(7, "/tmp/big", "x" * 300_000)

    # ...and the happy path passes the same check.
    GuestAgent(_GuestRest(), "n1").write_file(7, "/tmp/big", "x" * 300_000)


def test_linux_write_file_of_empty_content_still_writes_the_file():
    rest = _FakeRest()
    GuestAgent(rest, "n1").write_file(7, "/tmp/empty", "")
    argv = rest.execs()[0]["command"]
    assert "base64 -d > /tmp/empty" in argv[2]
    assert argv[4:] == [""]


def test_windows_write_file_over_the_argument_ceiling_fails_loud():
    import pytest

    from axrange.planner.executor import AdapterError

    ga = GuestAgent(_FakeRest(), "n1", os_family="windows")
    with pytest.raises(AdapterError, match="single-argument ceiling"):
        ga.write_file(7, r"C:\axr\big.ps1", "x" * 120_000)  # ->160 KB of base64


def test_windows_write_file_uses_powershell():
    rest = _FakeRest()
    ga = GuestAgent(rest, "n1", os_family="windows")
    assert ga.is_windows
    ga.write_file(7, r"C:\axr\f.ps1", "Write-Host hi")
    execs = rest.execs()
    assert len(execs) == 1  # no chmod on Windows
    argv = execs[0]["command"]
    assert argv[0] == "powershell"
    # base64-in-command (PVE-9.2 wide-char fix), WriteAllBytes, no input-data param
    assert "WriteAllBytes" in argv[-1] and r"C:\axr\f.ps1" in argv[-1]
    assert base64.b64encode(b"Write-Host hi").decode() in argv[-1]
    assert "input-data" not in execs[0]


def test_windows_write_file_handles_non_ascii():
    """Non-ASCII content (e.g. a Sysmon XML, em-dash comments) is base64'd into the
    command — it must NOT go via input-data (PVE-9.2 wide-char 500)."""
    rest = _FakeRest()
    ga = GuestAgent(rest, "n1", os_family="windows")
    ga.write_file(7, r"C:\axr\f.xml", "audit — Kerberos 4769")
    ex = rest.execs()[0]
    assert "input-data" not in ex
    assert base64.b64encode("audit — Kerberos 4769".encode("utf-8")).decode() in ex["command"][-1]


def test_windows_write_file_escapes_single_quote_in_path():
    rest = _FakeRest()
    ga = GuestAgent(rest, "n1", os_family="windows")
    ga.write_file(7, r"C:\o'brien\f.txt", "x")
    ps = rest.execs()[0]["command"][-1]
    assert "o''brien" in ps  # PowerShell single-quote escape


def test_shell_exec_branches_on_os():
    for osf, expected in (("linux", "sh"), ("windows", "powershell")):
        rest = _FakeRest()
        ga = GuestAgent(rest, "n1", os_family=osf)
        ga.shell_exec(7, "Get-Date")
        argv = rest.execs()[0]["command"]
        assert argv[0] == expected
        assert argv[-1] == "Get-Date"


def test_os_family_defaults_and_normalizes():
    assert GuestAgent(_FakeRest(), "n").os_family == "linux"
    assert GuestAgent(_FakeRest(), "n", os_family="Windows").is_windows
    assert GuestAgent(_FakeRest(), "n", os_family=None).os_family == "linux"
