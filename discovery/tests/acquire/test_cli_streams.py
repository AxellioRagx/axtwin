"""Tests for `xtract streams` (aximprint.acquire.cli).

No test here opens a network socket. `_FakeClient` stands in for
`aximprint.acquire.client.Client` (same request(method, path, *, params=,
expected_status=) surface catalog.py calls) and is wrapped in a trivial
context manager injected via `client_factory=`, so `main()`'s real
credential-resolution/`requests` path is never exercised here — that's
`tests/acquire/test_client.py`'s and `tests/acquire/test_auth.py`'s job.
"""

import argparse
import json

import pytest

from aximprint.acquire import __version__
from aximprint.acquire.cli import (
    _human_bytes,
    _resolve_host_or_fail,
    _stream_sort_key,
    main,
)
from aximprint.acquire.errors import AuthenticationError, ServerError, UnauthorizedError

_BASE = 1_700_000_000  # a plausible raw fs-field value; see tests/acquire/test_catalog.py


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """`script` is consumed one item per `request()` call, in order. Each
    item is either a JSON-able body (wrapped in `_FakeResponse`) or an
    exception instance to raise, so auth/server failures can be simulated
    without a real HTTP stack.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, path, *, params=None, expected_status=200):
        self.calls.append((method, path, params))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeResponse(item)


class _NullContext:
    """Wraps a `_FakeClient` so `client_factory(args)` returns something
    usable in `with ... as client:`, mirroring how the real `Client` context
    manager (login/logout) is used, without either behavior here.
    """

    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, *exc_info):
        return False


def _factory(client):
    return lambda args: _NullContext(client)


def _listing(name):
    return {
        "name": name,
        "permissions": 0o755,
        "user": "root",
        "group": "root",
        "size": 4096,
        "date": "2026-08-01",
        "type": "d",
    }


def _streams_body(names):
    return {"stream": [_listing(n) for n in names]}


def _file(name, start_offset, end_offset, size=1000):
    # time_start/time_end are strings on the wire (measured 2026-08-05 live
    # probe against CorpAPCN) — stringified here so call sites keep passing a
    # plain int offset for readability.
    return {
        "name": name,
        "permissions": 0o644,
        "user": "root",
        "group": "root",
        "size": size,
        "date": "2026-08-04",
        "time_start": str(_BASE + start_offset),
        "time_end": str(_BASE + end_offset),
        "epoch": 1,
        "seqnum": 1,
        "type": "f",
    }


def _files_body(files):
    return {"files": files}


BASE_ARGV = ["--host", "https://corpapcn.example.com"]


# --- table output --------------------------------------------------------------


def test_streams_table_default_lists_every_stream(capsys):
    client = _FakeClient(
        [
            _streams_body(["0", "1"]),
            _files_body([_file("a.pcap", 0, 1000)]),
            _files_body([_file("b.pcap", 0, 500)]),
        ]
    )

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["stream", "first", "first", "(utc)", "last", "last", "(utc)", "duration", "files", "bytes", "gaps"]
    assert lines[1].startswith("0")
    assert lines[2].startswith("1")
    assert "1700000000" in lines[1]
    assert "2023-" in lines[1]  # human-readable UTC alongside the wire form
    # Both fs/streams and one fs/files call per discovered stream.
    assert client.calls[0] == ("GET", "/axapi/fs/streams", None)
    assert client.calls[1] == ("GET", "/axapi/fs/files", {"stream": "0"})
    assert client.calls[2] == ("GET", "/axapi/fs/files", {"stream": "1"})


def test_streams_table_empty_stream_shows_dashes_not_an_error(capsys):
    client = _FakeClient([_streams_body(["0"]), _files_body([])])

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 0
    out = capsys.readouterr().out
    data_line = out.splitlines()[1]
    assert data_line.split()[0] == "0"
    assert "-" in data_line
    assert data_line.strip().endswith("0  0  0") or "0" in data_line


def test_streams_wire_timestamp_round_trips_into_parse_instant(capsys):
    """The printed wire-form timestamp must be exactly what `xtract pcap`'s
    `--start`/`--end` accepts — round-trip it through `parse_instant`.
    """
    from aximprint.acquire.timeutil import parse_instant

    client = _FakeClient([_streams_body(["0"]), _files_body([_file("a.pcap", 0, 1000)])])

    main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    out = capsys.readouterr().out
    first_wire = out.splitlines()[1].split()[1]
    assert parse_instant(first_wire).ns == _BASE * 1_000_000_000


# --- JSON output ---------------------------------------------------------------


def test_streams_json_is_machine_readable(capsys):
    client = _FakeClient([_streams_body(["0"]), _files_body([_file("a.pcap", 0, 1000)])])

    code = main([*BASE_ARGV, "streams", "--json"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "stream": "0",
            "first": "1700000000",
            "first_utc": payload[0]["first_utc"],
            "last": "1700001000",
            "last_utc": payload[0]["last_utc"],
            "duration_ns": 1000 * 1_000_000_000,
            "files": 1,
            "bytes": 1000,
            "gaps": {"count": 0},
        }
    ]
    assert payload[0]["first_utc"].endswith("Z")


def test_streams_json_reflects_no_data(capsys):
    client = _FakeClient([_streams_body(["0"]), _files_body([])])

    code = main([*BASE_ARGV, "streams", "--json"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "stream": "0",
            "first": None,
            "first_utc": None,
            "last": None,
            "last_utc": None,
            "duration_ns": None,
            "files": 0,
            "bytes": 0,
            "gaps": {"count": 0},
        }
    ]


# --- --streams restricts the set ------------------------------------------------


def test_streams_flag_restricts_to_requested_ids(capsys):
    client = _FakeClient(
        [
            _streams_body(["0", "1", "2", "3", "4"]),
            _files_body([_file("a.pcap", 0, 1000)]),  # stream 2
            _files_body([_file("b.pcap", 0, 1000)]),  # stream 3
            _files_body([_file("c.pcap", 0, 1000)]),  # stream 4
        ]
    )

    code = main([*BASE_ARGV, "streams", "--streams", "2-4", "--json"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["stream"] for r in payload] == ["2", "3", "4"]
    fs_files_calls = [c for c in client.calls if c[1] == "/axapi/fs/files"]
    assert {c[2]["stream"] for c in fs_files_calls} == {"2", "3", "4"}


def test_streams_flag_zero_resolves_to_the_stream00_directory(capsys):
    """`--streams 0` must select the real `streamNN`-named directory
    `stream00` (Q21, resolved 2026-08-05 live probe) — not a literal `"0"`
    stream that doesn't exist on the appliance.
    """
    client = _FakeClient(
        [
            _streams_body(["stream00", "stream01"]),
            _files_body([_file("a.pcap", 0, 1000)]),
        ]
    )

    code = main([*BASE_ARGV, "streams", "--streams", "0", "--json"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["stream"] for r in payload] == ["stream00"]
    fs_files_calls = [c for c in client.calls if c[1] == "/axapi/fs/files"]
    assert {c[2]["stream"] for c in fs_files_calls} == {"stream00"}


def test_streams_flag_dotdot_separator_also_restricts(capsys):
    client = _FakeClient([_streams_body(["0", "1", "2"]), _files_body([_file("a.pcap", 0, 1000)])])

    code = main([*BASE_ARGV, "streams", "--streams", "1..1", "--json"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["stream"] for r in payload] == ["1"]


# --- unknown stream / malformed spec: clear error, not a traceback ---------


def test_streams_unknown_stream_id_is_a_clear_usage_error(capsys):
    client = _FakeClient([_streams_body(["0", "1"])])

    code = main([*BASE_ARGV, "streams", "--streams", "9"], client_factory=_factory(client))

    assert code == 2
    err = capsys.readouterr().err
    assert "9" in err
    assert "unknown" in err.lower()


def test_streams_malformed_spec_is_a_clear_usage_error(capsys):
    client = _FakeClient([_streams_body(["0", "1"])])

    code = main([*BASE_ARGV, "streams", "--streams", "not-a-spec"], client_factory=_factory(client))

    assert code == 2
    err = capsys.readouterr().err
    assert "not-a-spec" in err


# --- --gaps breakdown ------------------------------------------------------------


def test_gaps_flag_adds_table_breakdown(capsys):
    client = _FakeClient(
        [
            _streams_body(["0"]),
            _files_body([_file("a.pcap", 0, 1000), _file("b.pcap", 5000, 6000)]),
        ]
    )

    code = main([*BASE_ARGV, "streams", "--gaps"], client_factory=_factory(client))

    assert code == 0
    out = capsys.readouterr().out
    assert "1 gap(s)" in out
    assert "stream 0:" in out


def test_gaps_flag_adds_json_breakdown_fields(capsys):
    client = _FakeClient(
        [
            _streams_body(["0"]),
            _files_body([_file("a.pcap", 0, 1000), _file("b.pcap", 5000, 6000)]),
        ]
    )

    code = main([*BASE_ARGV, "streams", "--json", "--gaps"], client_factory=_factory(client))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    gaps = payload[0]["gaps"]
    assert gaps["count"] == 1
    assert gaps["total_duration_ns"] == 4000 * 1_000_000_000
    assert gaps["largest_duration_ns"] == 4000 * 1_000_000_000


def test_gaps_flag_absent_omits_breakdown_fields(capsys):
    client = _FakeClient([_streams_body(["0"]), _files_body([_file("a.pcap", 0, 1000)])])

    main([*BASE_ARGV, "streams", "--json"], client_factory=_factory(client))

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["gaps"] == {"count": 0}


# --- exit codes: auth, transport/server, usage -----------------------------


def test_exit_code_3_on_authentication_error(capsys):
    client = _FakeClient([AuthenticationError("401 persisted")])

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 3
    assert "authentication" in capsys.readouterr().err.lower()


def test_exit_code_3_on_unauthorized_error(capsys):
    client = _FakeClient([UnauthorizedError("bad key", status_code=401)])

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 3


def test_exit_code_4_on_server_error(capsys):
    client = _FakeClient([ServerError("boom", status_code=500)])

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 4
    assert "boom" in capsys.readouterr().err


def test_exit_code_4_on_malformed_catalog_response(capsys):
    client = _FakeClient([{"stream": "not-a-list"}])  # wrong JSON type — §5.1/§5.2 ambiguity

    code = main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    assert code == 4


def test_exit_code_3_on_credential_resolution_failure(capsys):
    from aximprint.acquire.auth import CredentialResolutionError

    def _raising_factory(args):
        raise CredentialResolutionError("no credential found")

    code = main([*BASE_ARGV, "streams"], client_factory=_raising_factory)

    assert code == 3
    assert "no credential" in capsys.readouterr().err.lower()


def test_exit_code_2_when_no_host_from_flag_or_profile(capsys, monkeypatch):
    # --host is optional (it can come from the config profile), so argparse no
    # longer rejects its absence; the real client factory does, before any
    # credential resolution or network call. Patch the profile lookup to "no
    # host configured" so this doesn't depend on the developer's own config.
    monkeypatch.setattr("aximprint.acquire.cli.resolve_host", lambda **_: None)

    code = main(["streams"])

    assert code == 2
    assert "no host" in capsys.readouterr().err


def test_host_falls_back_to_the_profile_when_flag_is_omitted(monkeypatch):
    monkeypatch.setattr("aximprint.acquire.cli.resolve_host", lambda **_: "profile-host.example")
    args = argparse.Namespace(host=None, profile="prod")

    assert _resolve_host_or_fail(args) == "profile-host.example"


def test_explicit_host_flag_overrides_the_profile(monkeypatch):
    # A profile can be reused against a standby appliance without editing it.
    monkeypatch.setattr("aximprint.acquire.cli.resolve_host", lambda **_: "profile-host.example")
    args = argparse.Namespace(host="https://flag-host.example", profile="prod")

    assert _resolve_host_or_fail(args) == "https://flag-host.example"


def test_exit_code_2_when_no_subcommand(capsys):
    code = main(BASE_ARGV, client_factory=_factory(_FakeClient([])))

    assert code == 2


def test_help_exits_zero(capsys):
    # argparse's own -h/--help raises SystemExit(0) from inside parse_args;
    # main() translates that into a normal return rather than letting the
    # exception escape to the caller.
    code = main([*BASE_ARGV, "--help"])
    assert code == 0


def test_version_exits_zero_and_prints_the_packaged_version(capsys):
    # `--version` short-circuits argparse before `--host`/subcommand are
    # required, so it works with no other arguments at all (packaging work
    # order §2 Phase 1).
    code = main(["--version"])
    assert code == 0
    assert __version__ in capsys.readouterr().out


# --- human-readable sizes, sorting, and warning suppression --------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.00 kB"),
        (2_866_041, "2.87 MB"),
        (2_277_843_753_946, "2.28 TB"),
    ],
)
def test_human_bytes_uses_decimal_units(raw, expected):
    # Decimal, not binary: the appliance reports its own capacity in decimal TB,
    # so an operator comparing the two gets figures that reconcile.
    assert _human_bytes(raw) == expected


def test_streams_table_shows_human_readable_sizes(capsys):
    client = _FakeClient(
        [_streams_body(["0"]), _files_body([_file("a.pcap", 0, 1000, size=2_866_041)])]
    )

    main([*BASE_ARGV, "streams"], client_factory=_factory(client))

    out = capsys.readouterr().out
    assert "2.87 MB" in out
    assert "2866041" not in out


def test_streams_json_keeps_the_exact_byte_count(capsys):
    # The table is for humans; --json is the machine-readable path and must not
    # lose precision to a rounded display string.
    client = _FakeClient(
        [_streams_body(["0"]), _files_body([_file("a.pcap", 0, 1000, size=2_866_041)])]
    )

    main([*BASE_ARGV, "streams", "--json"], client_factory=_factory(client))

    assert json.loads(capsys.readouterr().out)[0]["bytes"] == 2_866_041


def test_streams_output_is_sorted_by_stream_id(capsys):
    # The appliance returns streams in arbitrary order (observed: 08, 04, 10, ...).
    client = _FakeClient(
        [
            _streams_body(["stream08", "stream04", "stream10", "stream02"]),
            *[_files_body([_file("a.pcap", 0, 10)]) for _ in range(4)],
        ]
    )

    main([*BASE_ARGV, "streams", "--json"], client_factory=_factory(client))

    payload = json.loads(capsys.readouterr().out)
    assert [r["stream"] for r in payload] == ["stream02", "stream04", "stream08", "stream10"]


def test_stream_sort_key_orders_numerically_not_lexically():
    # stream10 must sort after stream9, which a plain string sort gets wrong.
    names = ["stream10", "stream9", "stream1"]
    assert sorted(names, key=_stream_sort_key) == ["stream1", "stream9", "stream10"]


def test_stream_sort_key_puts_unparseable_names_last_deterministically():
    names = ["zzz", "stream01", "aaa"]
    assert sorted(names, key=_stream_sort_key) == ["stream01", "aaa", "zzz"]
