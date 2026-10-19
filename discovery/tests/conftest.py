"""conftest — repo-wide safety net: no test may reach real, non-loopback network.

`~/.config/xtract/config.toml` exists on a host that also runs `aximprint acquire`
against a live appliance, at mode 0600, holding real credentials (CONVENTIONS §3
Privacy; step 9 of `docs/WORK_ORDER_merge_xtract_acquire.md` — "the merged repo
handles credentials... categorically more sensitive than what `LES-003` was drafted
against"). A test suite that can construct a real `aximprint.acquire.client.Client`
and let it run uncontrolled can authenticate against PRODUCTION for real — not a
hypothetical: exactly this happened once, mid-development of the fix round this file
belongs to. A test simulating a missing `requests` package, in a full-suite run,
let `_dispatch_acquire`'s import succeed from an already-warm `sys.modules` cache and
fall through to a genuine `acquire_main(["streams"])` call with no injected client —
producing an actual `SSLError` from a real `POST /axapi/login` attempt, caught in
review before landing (see the fix-round report; the test itself is fixed in
`tests/test_cli_acquire_dispatch.py` to evict the cache first).

**Deny by default, repo-wide, autouse, using `monkeypatch`** — deliberately NOT
`pytest_configure`, for the identical reason `tests/acquire/conftest.py` itself no
longer uses it: that hook is session-wide and never restores (see that file's own
docstring for the full mechanism, discovered and fixed earlier in this same merge
round). This file applies the SAME fix at the repo root rather than one subdirectory,
which is what actually closes the gap `tests/acquire/conftest.py` alone could not:
a fixture defined in a directory's own `conftest.py` is visible only to tests
collected from that directory and below (pytest's fixture-visibility rule), so that
file's own autouse fixture was NEVER going to protect anything outside
`tests/acquire/` — including the five root-level files that import and exercise
acquire code (`test_cli_acquire_dispatch.py`, `test_acquire_ingest_chain.py`,
`test_acquire_layering.py`, `test_acquire_conftest_socket_scope.py`,
`test_acquire_zeek_stem_sync.py`) and every other test in the repo besides.

**Loopback is permitted; everything else is denied.**
`test_acquire_conftest_socket_scope.py` performs a genuine local connect on purpose
(proving `tests/acquire/conftest.py`'s OWN denial does not leak outside its own
directory), and a future test may reasonably bind a local test server — neither is
"real network egress" in the sense this guard cares about; the appliance this
project's credentials point at is never loopback. Permitted hosts are matched as an
exact, case-insensitive STRING against the address a caller actually passed —
`127.0.0.1`, `::1`, `localhost` — never resolved via this module's own DNS lookup:
doing that would itself be a network call, defeating the point, and would let a
hostname that merely happens to resolve to loopback (a custom `/etc/hosts` entry, an
unusual DNS zone) sneak past a check that is supposed to be conservative. An AF_UNIX
address (a filesystem path, i.e. a `str`, not a `(host, port)` tuple) is always
permitted — that is local IPC, not network egress, regardless of the path.

Patches the same three call sites `tests/acquire/conftest.py` does, for the same
reason (never the `socket.socket` class itself — see that file's docstring for why):
the real, UNPATCHED versions are captured once, at MODULE IMPORT time — i.e. during
pytest's collection phase, before any fixture in either conftest has ever run and
patched anything — so the loopback branch below can always call straight through to
the genuine implementation. `socket.create_connection` is patched SEPARATELY from
`connect`/`connect_ex`, even though its own stdlib implementation calls `.connect()`
internally (which the patched `connect` would already intercept): `create_connection`
resolves its `(host, port)` argument via `getaddrinfo` BEFORE it ever calls
`.connect()`, and that resolution is itself a real network call (a DNS query) that
must never happen for a destination this guard would deny anyway — checking
`create_connection`'s own, unresolved argument first denies before any lookup is
attempted, rather than after.

`tests/acquire/conftest.py`'s own fixture is NOT redundant with this one and is kept:
it is a strictly STRICTER inner layer for its own subtree (denies loopback too — that
suite's own documented philosophy is "entirely offline," a stronger claim than "no
real egress," since every `axapi` interaction under `tests/acquire/` is faked via an
injected `Client`/`requests.Response`/`subprocess.run` stand-in and none of them
legitimately need a socket at all, loopback included). Confirmed empirically
(`pytest -s` fixture-order probe, root-then-subdirectory) that pytest instantiates
this file's autouse fixture FIRST for a test under `tests/acquire/`, and that
directory's own fixture SECOND — so the stricter, unconditional deny is the one in
effect for the test body, and this file's own loopback allowance never weakens it.

An explicit, narrow opt-out exists for a test that must reach a real, non-loopback
destination on purpose: `@pytest.mark.allow_network` (declared in `pyproject.toml`,
required under `--strict-markers`). Nothing in this suite needs it today — reaching
for it is itself a signal to stop and report why, not a routine escape hatch.

One boundary worth stating plainly: this guard protects the pytest PROCESS itself. A
test that shells out via `subprocess.run` (as several `tests/acquire/` and
`tests/test_cli_acquire_dispatch.py` tests do, to exercise a real console-script
invocation) spawns a fresh interpreter with an unpatched `socket` module — this file
cannot and does not reach into it. Every such test in this suite is independently
verified network-free by construction (a `--dry-run` with an explicit `--end`, an
injected `client_factory`, or an argument shape argparse rejects before any client is
built) rather than by relying on this guard to cover it.
"""
from __future__ import annotations

import socket

import pytest

# Captured at MODULE IMPORT time (pytest's collection phase, before any fixture --
# in this file or any other conftest -- has run), so these are always the genuine,
# unpatched stdlib implementations regardless of fixture/test execution order.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class RealNetworkEgressDenied(Exception):
    """Raised instead of letting a test open a real, non-loopback socket connection."""


def _is_loopback_address(address: object) -> bool:
    """True for a `(host, port[, ...])` tuple whose `host` is exactly one of
    `_LOOPBACK_HOSTS` (case-insensitive), or for an AF_UNIX address (a `str`
    filesystem path — local IPC, not network, always permitted). Any other shape,
    including one this function does not recognize, is NOT loopback: fail closed,
    never open.
    """
    if isinstance(address, str):
        return True  # AF_UNIX path
    if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[0], str):
        return address[0].lower() in _LOOPBACK_HOSTS
    return False


def _denied(kind: str, address: object) -> RealNetworkEgressDenied:
    return RealNetworkEgressDenied(
        f"test attempted a real, non-loopback {kind} to {address!r} — this suite "
        "forbids real network egress by default (tests/conftest.py); loopback "
        "(127.0.0.1 / ::1 / localhost) is allowed. If this is genuinely intentional, "
        "mark the test @pytest.mark.allow_network and say why in the test's own "
        "docstring -- nothing in this suite needs that marker today."
    )


def _guarded_connect(self, address, *a, **k):
    if _is_loopback_address(address):
        return _REAL_CONNECT(self, address, *a, **k)
    raise _denied("socket.connect", address)


def _guarded_connect_ex(self, address, *a, **k):
    if _is_loopback_address(address):
        return _REAL_CONNECT_EX(self, address, *a, **k)
    raise _denied("socket.connect_ex", address)


def _guarded_create_connection(address, *a, **k):
    if _is_loopback_address(address):
        return _REAL_CREATE_CONNECTION(address, *a, **k)
    raise _denied("socket.create_connection", address)


def _network_allowed_for(request: pytest.FixtureRequest) -> bool:
    """The opt-out predicate, extracted as a plain function (rather than inlined in
    the fixture body) specifically so it can be unit-tested by calling it directly
    with a minimal stand-in `request` — no real pytest collection, no `pytester`
    sub-session, and no socket of any kind (`tests/test_conftest_network_guard.py`).
    True exactly when the CURRENT test carries `@pytest.mark.allow_network`.
    """
    return request.node.get_closest_marker("allow_network") is not None


@pytest.fixture(autouse=True)
def _deny_non_loopback_network(request, monkeypatch):
    """Autouse for every test collected anywhere in the repo (a root `conftest.py`
    fixture has no directory boundary). Skips patching entirely for a test marked
    `@pytest.mark.allow_network` — the explicit, narrow opt-out described in the
    module docstring. `monkeypatch` reverts the patch at THIS test's own teardown
    either way, so the denial never survives past the test that needed it, let alone
    past the whole session (`pytest_configure`'s exact failure mode).
    """
    if _network_allowed_for(request):
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)
