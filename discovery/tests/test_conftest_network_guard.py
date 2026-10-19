"""Proves `tests/conftest.py`'s repo-wide, non-loopback network guard actually
discriminates: denies a real, non-loopback destination with a message naming the
cause and the destination, and permits loopback. `LES-031`'s point applies here as
much as anywhere — this file's own tests are re-verified by mutation (temporarily
disabling the guard and confirming the denial tests fail, see the fix-round report),
not trusted merely because their assertions read correctly.

Uses TEST-NET-1 (`192.0.2.0/24`, RFC 5737) as the "denied" address throughout —
reserved specifically for documentation/example use, globally unassigned and
unroutable, so even in a hypothetical world where this guard failed to intercept the
call, the connection attempt could not reach anything real; every socket used here
also carries a short, explicit timeout, so a guard failure mode never blocks the
suite waiting on a doomed connection.

Does NOT exercise `@pytest.mark.allow_network` on a real, pytest-collected test —
declaring it (`pyproject.toml`, for `--strict-markers`) is the structural requirement
item 4 of the fix round asks for; nothing in this suite needs it APPLIED anywhere
today, including here, and reaching for it on a real test was judged closer to "using
the escape hatch" than the task's own repeated caution intends. Its DECISION LOGIC
(`_network_allowed_for`, extracted from the fixture body for exactly this reason) is
still unit-tested below, directly, with a minimal stand-in `request` — no `pytester`
sub-session, no real pytest collection, and no socket of any kind.
"""
from __future__ import annotations

import socket

import pytest

from tests.conftest import RealNetworkEgressDenied, _network_allowed_for

# RFC 5737 TEST-NET-1 — reserved for documentation/example use, globally unassigned
# and unroutable. Never a real host, on this network or any other. A literal IP (not
# a hostname) so nothing about resolving it could itself touch the network either.
_NON_ROUTABLE_ADDRESS = ("192.0.2.1", 80)


def test_a_non_loopback_connect_is_denied_and_names_the_cause():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(2)
        with pytest.raises(RealNetworkEgressDenied) as exc_info:
            s.connect(_NON_ROUTABLE_ADDRESS)
    finally:
        s.close()
    message = str(exc_info.value)
    assert "192.0.2.1" in message
    assert "80" in message
    assert "network egress" in message.lower()


def test_a_non_loopback_connect_ex_is_also_denied():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(2)
        with pytest.raises(RealNetworkEgressDenied):
            s.connect_ex(_NON_ROUTABLE_ADDRESS)
    finally:
        s.close()


def test_create_connection_to_a_non_loopback_address_is_also_denied():
    """`socket.create_connection` is patched separately from `connect`/`connect_ex`
    (see `tests/conftest.py`'s module docstring for why: it resolves its argument via
    `getaddrinfo` BEFORE calling `.connect()`, so denying only at the inner `.connect()`
    call would still let a real DNS query through for a hostname this guard would deny
    anyway). This is the direct proof that surface is covered too.
    """
    with pytest.raises(RealNetworkEgressDenied):
        socket.create_connection(_NON_ROUTABLE_ADDRESS, timeout=2)


def test_loopback_connect_is_permitted_by_this_same_guard():
    """The complement of the denial tests above, proving the SAME repo-wide guard's
    loopback branch directly — independent of `test_acquire_conftest_socket_scope.py`,
    which proves a different, narrower thing (that `tests/acquire/`'s STRICTER,
    unconditional denial specifically does not leak here).
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        addr = listener.getsockname()
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(5)
            client.connect(addr)
        finally:
            client.close()
    finally:
        listener.close()


# --- _network_allowed_for: the opt-out predicate, unit-tested directly, no socket ----
#
# A minimal stand-in for the one thing `_network_allowed_for` actually touches on its
# `request` argument — `request.node.get_closest_marker(name)` — rather than a real
# `pytest.FixtureRequest` (which needs real collection to construct) or a `pytester`
# sub-session (a real, if isolated, second pytest process). This is deliberately NOT a
# `unittest.mock.Mock()`: a bare `Mock()` would let ANY attribute/method access
# silently succeed and return another `Mock`, so a typo'd attribute name on the
# PRODUCTION side (e.g. `request.node.get_closest_marker`) would still "pass" here
# with a `Mock` object that is truthy — exactly the kind of shape-not-property proxy
# `LES-008`/`LES-030` warn about. This stub raises `AttributeError` on anything it
# doesn't explicitly define, the same as a real object would.


class _StubNode:
    def __init__(self, marker: object | None) -> None:
        self._marker = marker

    def get_closest_marker(self, name: str):
        assert name == "allow_network", f"unexpected marker name requested: {name!r}"
        return self._marker


class _StubRequest:
    def __init__(self, marker: object | None) -> None:
        self.node = _StubNode(marker)


def test_network_allowed_for_is_true_when_the_marker_is_present():
    # `get_closest_marker` returns a real `pytest.Mark`-shaped object when present;
    # a bare sentinel is enough here, since `_network_allowed_for` only ever checks
    # "is not None", never inspects the marker's own attributes.
    marker = object()
    assert _network_allowed_for(_StubRequest(marker)) is True


def test_network_allowed_for_is_false_when_the_marker_is_absent():
    # `get_closest_marker` returns `None`, not a falsy sentinel of some other type,
    # when a test carries no such marker — matching real pytest behavior exactly.
    assert _network_allowed_for(_StubRequest(None)) is False
