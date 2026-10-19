"""Proves `tests/acquire/conftest.py`'s socket-denial fixture (`_deny_real_sockets`,
`docs/WORK_ORDER_merge_xtract_acquire.md` fix round item 4, an AUTHORIZED behavior
change) is scoped to `tests/acquire/` and does not leak into the rest of the suite the
way the `pytest_configure` hook it replaced used to.

That hook was *historic*: it fired once, session-wide, the moment `tests/acquire/` —
which sorts before `tests/test_*.py` alphabetically — was collected, and bare-assigned
`socket.socket.connect`/`connect_ex`/`socket.create_connection` with no teardown. So
every OTHER test in the merged suite (this repo's own, pre-merge tests included) ran
with a dead socket stack too, order-dependently.

This file lives OUTSIDE `tests/acquire/` on purpose: a directory's own `conftest.py`
fixtures — autouse or not — are visible only to tests collected from that directory and
below (pytest's fixture-discovery rule), so nothing here ever requests
`_deny_real_sockets` directly.

**Narrowed by a later round of the same fix** (the credentials-on-host finding,
`tests/conftest.py`): this file no longer proves the socket stack outside
`tests/acquire/` is fully UNPATCHED — it is not, and should not be. `tests/conftest.py`
installs its own, repo-wide, loopback-PERMITTING guard everywhere, this file included,
which is a real and intentional patch, not a leak. What this file still proves, and the
only thing it ever needs to: `tests/acquire/conftest.py`'s OWN, STRICTER,
unconditional-deny functions specifically are not what is installed here — checked by
identity against `_deny_connect`/`_deny_create_connection` themselves, never by a type
check (a type check would now trip on the repo-wide guard's own, equally-real,
plain-Python-function patch, regardless of whether the leak this file actually guards
against ever happened). `tests/test_conftest_network_guard.py` covers the repo-wide
guard's own behavior directly — the non-loopback denial and its message.
"""
from __future__ import annotations

import socket

from tests.acquire.conftest import _deny_connect, _deny_create_connection


def test_acquires_own_denial_function_is_not_installed_outside_tests_acquire():
    """`tests/acquire/conftest.py`'s `_deny_connect`/`_deny_create_connection` are ITS
    OWN module-private functions, installed only by ITS OWN autouse fixture — identity
    against them is decisive regardless of whatever else may legitimately be patched
    here by the repo-wide guard (`tests/conftest.py`).
    """
    assert socket.socket.connect is not _deny_connect, (
        "socket.socket.connect is tests/acquire/conftest.py's OWN denial function "
        "outside tests/acquire/ — that fixture has leaked past its own directory"
    )
    assert socket.socket.connect_ex is not _deny_connect
    assert socket.create_connection is not _deny_create_connection


def test_a_real_local_socket_connect_is_not_intercepted():
    """End-to-end version of the check above: actually call `connect` against a real,
    local, throwaway listening socket. Under the OLD `pytest_configure` mutation this
    would have raised `tests.acquire.conftest.SocketUsedInTest` (session-wide, no matter
    which test file asked). Under `tests/acquire/conftest.py`'s CURRENT, directory-
    scoped fixture it does not apply here at all. Under `tests/conftest.py`'s repo-wide
    guard (which DOES apply here) loopback is the one thing explicitly permitted — so
    this must succeed either way: connecting to a socket this test itself opened and
    closing both ends, never touching a real remote host.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        addr = listener.getsockname()

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(5)
            client.connect(addr)  # denied (either guard) if either fixture misbehaved here
        finally:
            client.close()
    finally:
        listener.close()
