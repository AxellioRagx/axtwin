"""conftest — the hermetic-suite safety net: no test under `tests/acquire/` may open a
real socket.

`xtract`'s test suite is supposed to be entirely offline — every test that
would otherwise talk to `axapi` injects a fake `Client`/`requests.Response`
instead (see the module docstrings in `tests/acquire/test_client.py`,
`tests/acquire/test_cli_streams.py`, `tests/acquire/test_cli_pcap.py`). This file makes that
an enforced property, not a convention: any test that reaches a real
`socket.connect`/`socket.create_connection` fails loudly with
`SocketUsedInTest`, naming the address it tried to reach, instead of hanging
or silently reaching a real host (`docs/WORK_ORDER_packaging_runbook.md` WO-7
§2 Phase 2).

An **autouse, function-scoped fixture using `monkeypatch`** — deliberately NOT
`pytest_configure` (fixed during `docs/WORK_ORDER_merge_xtract_acquire.md`'s merge round,
item 4: an AUTHORIZED behavior change, reported before being made). `pytest_configure` is a
*historic* hook: a subdirectory `conftest.py`'s implementation fires the moment that
directory is collected and applies **session-wide, with no teardown** — so the original
`socket.socket.connect = _deny_connect` (a bare, un-restored class-attribute assignment)
patched the class for the rest of the whole `pytest` process the instant `tests/acquire/`
was collected. `tests/acquire` sorts before `tests/test_*.py` alphabetically, so it collects
first — meaning every OTHER test in the merged suite silently ran with a dead socket stack
too, and the suite was order-dependent (`pytest tests/test_smoke.py` differed from `pytest
tests/`). `monkeypatch` restores whatever it patched at the *requesting test's* own
teardown, and a fixture defined in a directory's own `conftest.py` is visible only to tests
collected from that directory and below — so `_deny_real_sockets` protects every test under
`tests/acquire/` exactly as before, and is provably invisible outside it: a test under
`tests/` (not `tests/acquire/`) never requests it (see `tests/test_acquire_conftest_socket_
scope.py`).

**That fixing this leak reopened a real gap is documented in `tests/conftest.py`**, added
in a later round of the same fix: this file's own directory-scoped denial was never going
to protect the FOUR root-level test files that import and exercise acquire code, on a host
that holds real credentials (`~/.config/xtract/config.toml`, mode 0600) for a live
appliance — and during THIS fix round, a test simulating a missing dependency briefly let a
real request reach that appliance for real before the gap was closed. `tests/conftest.py`
now provides the repo-wide baseline (deny non-loopback, autouse, `monkeypatch`-restored,
same mechanism as this file); THIS file remains the STRICTER inner layer for its own
subtree — it denies loopback too, matching `xtract`'s original "entirely offline, no
exceptions" test philosophy stated above, which the repo-wide guard's looser, loopback-
permitting default does not by itself provide. A test collected under `tests/acquire/`
gets BOTH fixtures (pytest instantiates the root conftest's autouse fixture first, this
directory's own second — confirmed empirically, not assumed — so this file's stricter,
unconditional denial is the one actually in effect during the test body).

Patches `socket.socket.connect`/`connect_ex` and `socket.create_connection`
only — never the `socket.socket` class itself. Libraries (including
`requests`' own transport stack) subclass `socket.socket` at import time;
replacing the class after that has already happened doesn't affect those
subclasses and, worse, can break collection for any test module that imports
something socket-adjacent before this fixture runs. Patching the two call
sites that actually open a connection is sufficient and doesn't disturb
anything that merely constructs a `socket.socket` instance (binding,
`getsockname()`, etc. all still work).
"""

import socket

import pytest


def pcap_body(payload: bytes = b"") -> bytes:
    """A fixture body that starts with a real pcap magic (classic pcap, little-endian,
    microsecond — one of `aximprint.acquire.extract.PCAP_MAGICS`), followed by `payload`.

    Every test fixture across `tests/acquire/test_runner.py`, `tests/acquire/test_extract.py`,
    `tests/acquire/test_cli_pcap.py`, and `tests/acquire/test_pipeline.py` used to hand `extract_window` an
    arbitrary body (`b"pcap-bytes"`, `b"bytes-1"`, `b"three"`, ...) with no valid magic at all.
    Those 34 tests all passed *before* `extract.py` learned to check the leading bytes — which is
    the finding, not a coincidence: they proved the plumbing moved bytes from a response to a
    file, never that the bytes were a genuine capture. The live appliance defect this project
    shipped (`GET /axapi/pcap` returning `200` with `b"Error starting listen thread, shutting
    down thread"` instead of a pcap) would have sailed straight through every one of them. Use
    this helper for any fixture whose outcome should be `Written`, so the suite keeps that gap
    closed instead of quietly reopening it the next time a body literal gets typed by hand.
    """
    return b"\xd4\xc3\xb2\xa1" + payload


class SocketUsedInTest(Exception):
    """Raised instead of letting a test open a real socket connection."""


def _deny_connect(self, *a, **k):
    raise SocketUsedInTest(f"test attempted a real socket connect to {a!r}")


def _deny_create_connection(*a, **k):
    raise SocketUsedInTest(f"test attempted socket.create_connection{a!r}")


@pytest.fixture(autouse=True)
def _deny_real_sockets(monkeypatch):
    """Autouse: every test collected under `tests/acquire/` gets this without asking for
    it by name (conftest fixture visibility is directory-scoped — see the module
    docstring). `monkeypatch` reverts all three patches when THIS test tears down, so the
    denial never survives past the test that needed it, let alone past the whole session.
    """
    monkeypatch.setattr(socket.socket, "connect", _deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_connect)
    monkeypatch.setattr(socket, "create_connection", _deny_create_connection)
