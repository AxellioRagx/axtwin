"""Tests for `xtract pcap` (aximprint.acquire.cli).

No test here opens a network socket — `_FakeClient` (catalog-shaped, for
`--end` resolution) and a `requests`-level `FakeAdapter` (for the actual
`/axapi/pcap` calls, since `extract_window` drives `requests` directly, same
as `tests/acquire/test_extract.py`) both stand in for the real transport.
`tmp_path` stands in for `--out`.
"""

import json
from urllib.parse import parse_qs, urlsplit

import requests

from aximprint.acquire import cli
from aximprint.acquire.auth import Credential
from aximprint.acquire.cli import _run_mapping_errors, main
from aximprint.acquire.client import Client
from aximprint.acquire.errors import XtractError
from aximprint.acquire.manifest import manifest_path, read_manifest
from aximprint.acquire.timeutil import NS_PER_SECOND
from tests.acquire.conftest import pcap_body

BASE_ARGV = ["--host", "https://corpapcn.example.com"]


# --- fakes -----------------------------------------------------------------------


class FakeAdapter(requests.adapters.BaseAdapter):
    """Same pattern as `tests/acquire/test_extract.py`'s `FakeAdapter`."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self.script) - 1)
        item = self.script[idx]
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        pass


def _resp(status_code, content=b"", headers=None, reason="OK"):
    resp = requests.Response()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers.update(headers or {})
    resp._content = content
    resp._content_consumed = True
    resp.url = "https://fake-host/whatever"
    return resp


class _NullContext:
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, *exc_info):
        return False


def _pcap_client_factory(script, **client_kwargs):
    """A `client_factory` whose `Client` talks to a scripted `FakeAdapter` —
    real `Client`/`requests` machinery, no socket, so `extract_window`'s own
    request-building is exercised exactly as in production.
    """
    adapter = FakeAdapter(script)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    client_kwargs.setdefault("max_retries", 0)
    client = Client("https://host", Credential.from_api_key("sk-test"), session=session, **client_kwargs)
    return lambda args: _NullContext(client), adapter


class _FakeCatalogClient:
    """Stands in for `Client` when `cmd_pcap` calls `catalog.stream_range`
    to resolve an omitted `--end` — request()-shaped, like
    `tests/acquire/test_cli_streams.py`'s `_FakeClient`.
    """

    def __init__(self, files_by_stream: dict[str, list[dict]]):
        self._files_by_stream = files_by_stream
        self.calls = []

    def request(self, method, path, *, params=None, expected_status=200):
        self.calls.append((method, path, params))
        stream = params["stream"]
        return _JsonResp({"files": self._files_by_stream.get(stream, [])})


class _JsonResp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _file(last):
    # time_start/time_end are strings on the wire (measured 2026-08-05 live
    # probe) — stringify the raw epoch-seconds int this fixture is called
    # with, so callers can keep passing a plain int for readability.
    return {"name": "a.pcap", "size": 10, "time_start": str(last), "time_end": str(last), "epoch": 1, "seqnum": 1}


# --- dry run -----------------------------------------------------------------------


def test_dry_run_with_explicit_end_makes_no_client_and_no_pcap_calls(tmp_path, capsys):
    def _factory_should_not_be_called(args):
        raise AssertionError("client_factory must not be called for --dry-run with an explicit --end")

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "3s", "--out", str(tmp_path), "--dry-run"],
        client_factory=_factory_should_not_be_called,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "windows:  4" in out
    assert not manifest_path(tmp_path).exists()  # dry-run never touches the manifest either
    assert list(tmp_path.glob("*.pcap")) == []


def test_dry_run_reports_resume_skip_count(tmp_path, capsys):
    (tmp_path / "0.pcap").write_bytes(b"already-here")

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "3s", "--out", str(tmp_path), "--dry-run"],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "1 already done" in out


def test_dry_run_without_explicit_end_still_resolves_end_via_catalog(tmp_path, capsys):
    catalog_client = _FakeCatalogClient({"2": [_file(1_700_000_100)], "3": [_file(1_700_000_200)], "4": [_file(1_700_000_050)]})

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "1700000000", "--interval", "60s", "--out", str(tmp_path), "--dry-run"],
        client_factory=lambda args: _NullContext(catalog_client),
    )

    assert code == 0
    assert len(catalog_client.calls) == 3  # one fs/files call per expanded stream id
    out = capsys.readouterr().out
    assert "resolved from stored data" in out


# --- a full run: N windows produce N correctly-named files -----------------------


def test_full_run_writes_named_files_and_manifest(tmp_path):
    factory, _adapter = _pcap_client_factory(
        [_resp(200, content=pcap_body(b"one")), _resp(418), _resp(200, content=pcap_body(b"three")), _resp(200, content=pcap_body(b"four"))]
    )

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "3s", "--out", str(tmp_path)],
        client_factory=factory,
    )

    assert code == 0
    assert {p.name for p in tmp_path.glob("*.pcap")} == {"0.pcap", "6.pcap", "9.pcap"}
    data = read_manifest(tmp_path)
    assert data.header.streams == "2-4"
    assert [r.outcome for r in data.records] == ["written", "empty", "written", "written"]


def test_mixed_run_summary_and_exit_code(tmp_path, capsys):
    factory, _adapter = _pcap_client_factory([_resp(200, content=pcap_body(b"one")), _resp(418), _resp(500)])

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "30", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory,
    )

    assert code == 1
    out = capsys.readouterr().out
    assert "written=1" in out
    assert "empty=1" in out
    assert "failed=1" in out


def test_dry_run_issues_zero_pcap_calls_even_with_a_client(tmp_path):
    factory, adapter = _pcap_client_factory([_resp(200, content=pcap_body(b"should-never-be-sent"))])

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "3s", "--out", str(tmp_path), "--dry-run"],
        client_factory=factory,
    )

    assert code == 0
    assert adapter.requests == []


# --- the window-count ceiling requires --yes --------------------------------------


def test_window_count_ceiling_without_yes_is_a_usage_error(tmp_path, capsys):
    code = main(
        [
            *BASE_ARGV,
            "pcap",
            "--streams",
            "0",
            "--start",
            "0",
            "--end",
            "10001",
            "--interval",
            "1s",
            "--out",
            str(tmp_path),
            "--dry-run",
        ],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
    )

    assert code == 2
    assert "10001" in capsys.readouterr().err


def test_window_count_ceiling_with_yes_proceeds(tmp_path, capsys):
    code = main(
        [
            *BASE_ARGV,
            "pcap",
            "--streams",
            "0",
            "--start",
            "0",
            "--end",
            "10001",
            "--interval",
            "1s",
            "--out",
            str(tmp_path),
            "--dry-run",
            "--yes",
        ],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
    )

    assert code == 0
    assert "windows:  10001" in capsys.readouterr().out


# --- resume across separate invocations -------------------------------------------


def test_resumed_run_does_not_refetch_completed_windows(tmp_path):
    factory1, adapter1 = _pcap_client_factory([_resp(200, content=pcap_body(b"one")), _resp(418)])
    code1 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "20", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    assert code1 == 0
    assert len(adapter1.requests) == 2

    # A resumed invocation: the same manifest/files are on disk. Script only
    # has responses for windows that should actually be (re)requested — none,
    # here, since both prior windows are done (one written, one empty).
    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"should-not-be-used"))])
    code2 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "20", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory2,
    )

    assert code2 == 0
    assert adapter2.requests == []  # nothing already-done was refetched


def test_resumed_run_retries_a_failed_window(tmp_path):
    factory1, _adapter1 = _pcap_client_factory([_resp(500)])
    code1 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    assert code1 == 1

    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"now-succeeds"))])
    code2 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory2,
    )

    assert code2 == 0
    assert len(adapter2.requests) == 1
    assert (tmp_path / "0.pcap").exists()


def test_resumed_run_with_a_grown_end_reextracts_the_clipped_final_window(tmp_path, capsys):
    """The exact scenario the reviewer reproduced: RUN1's `--end 25` clips
    the final `[20, 25)` window; RUN2's `--end 40` must re-extract `[20, 30)`
    in full rather than treating `20.pcap`'s mere existence as proof the
    window is already done — `[25, 30)` must not be silently dropped forever.
    """
    factory1, _adapter1 = _pcap_client_factory(
        [_resp(200, content=pcap_body(b"w0")), _resp(200, content=pcap_body(b"w10")), _resp(200, content=pcap_body(b"w20-clipped"))]
    )
    code1 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "25", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    assert code1 == 0
    assert (tmp_path / "20.pcap").read_bytes() == pcap_body(b"w20-clipped")
    capsys.readouterr()  # discard RUN1's own output

    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"w20-full")), _resp(200, content=pcap_body(b"w30"))])
    code2 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "40", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory2,
    )

    assert code2 == 0
    assert "reextracted=1" in capsys.readouterr().out  # surfaced distinctly in the run summary
    # [0, 10) and [10, 20) are genuinely done and must not be refetched;
    # [20, 30) is re-extracted in full (superset of the clipped [20, 25));
    # [30, 40) is a fresh window.
    assert len(adapter2.requests) == 2
    query0 = parse_qs(urlsplit(adapter2.requests[0].url).query)
    assert query0["start_time"] == ["20"]
    # Q22: end_time is inclusive on the wire, so wire_params() sends end - 1ns.
    assert query0["end_time"] == ["29.999999999"]
    assert (tmp_path / "20.pcap").read_bytes() == pcap_body(b"w20-full")  # overwritten, not left clipped
    assert {p.name for p in tmp_path.glob("*.pcap")} == {"0.pcap", "10.pcap", "20.pcap", "30.pcap"}

    outcomes = [r.outcome for r in read_manifest(tmp_path).records]
    assert outcomes.count("written") == 5  # 3 from RUN1 + 2 from RUN2 (the re-extraction + the fresh window)


# --- the collision guard -----------------------------------------------------------


def test_collision_guard_refuses_a_mismatched_second_run(tmp_path, capsys):
    factory1, _adapter1 = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])
    code1 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    assert code1 == 0

    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"should-not-be-sent"))])
    code2 = main(
        [*BASE_ARGV, "pcap", "--streams", "5-6", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory2,
    )

    assert code2 == 2
    assert "streams" in capsys.readouterr().err
    assert adapter2.requests == []  # refused before ever issuing a pcap call


def test_collision_guard_override_proceeds_despite_mismatch(tmp_path, capsys):
    factory1, _adapter1 = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])
    main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )

    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"two"))])
    code2 = main(
        [
            *BASE_ARGV,
            "pcap",
            "--streams",
            "5-6",
            "--start",
            "10",
            "--end",
            "20",
            "--interval",
            "10s",
            "--out",
            str(tmp_path),
            "--allow-manifest-mismatch",
        ],
        client_factory=factory2,
    )

    assert code2 == 0
    assert "mismatch" in capsys.readouterr().err
    assert len(adapter2.requests) == 1


def test_dry_run_refuses_on_a_mismatched_manifest_without_override(tmp_path, capsys):
    """Reproduces the reviewer's finding: the collision guard used to run
    *after* `plan_resume` and not at all under `--dry-run`, so a `--dry-run`
    against a foreign manifest could silently print a "N already done" count
    computed against files that don't belong to this invocation. It must now
    refuse (matching what a real run would do), before ever computing or
    printing resume decisions.
    """
    factory1, _adapter1 = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])
    main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    capsys.readouterr()  # discard RUN1's own output

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "5-6", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path), "--dry-run"],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
    )

    assert code == 2
    out, err = capsys.readouterr()
    assert "streams" in err
    assert "already done" not in out  # the previous bug: a silent skip-count against a foreign manifest


def test_dry_run_warns_but_proceeds_on_a_mismatched_manifest_with_override(tmp_path, capsys):
    factory1, _adapter1 = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])
    main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory1,
    )
    capsys.readouterr()

    code = main(
        [
            *BASE_ARGV,
            "pcap",
            "--streams",
            "5-6",
            "--start",
            "0",
            "--end",
            "10",
            "--interval",
            "10s",
            "--out",
            str(tmp_path),
            "--dry-run",
            "--allow-manifest-mismatch",
        ],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
    )

    assert code == 0
    err = capsys.readouterr().err
    assert "mismatch" in err
    # still no writes at all — dry-run stays dry-run even under override.
    assert manifest_path(tmp_path).read_text().count('"kind": "header"') == 1


# --- all-empty streams when resolving --end ----------------------------------------


def test_all_requested_streams_empty_fails_clearly(tmp_path, capsys):
    catalog_client = _FakeCatalogClient({"2": [], "3": []})

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-3", "--start", "0", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(catalog_client),
    )

    assert code == 4
    err = capsys.readouterr().err
    assert "no" in err.lower() or "empty" in err.lower() or "data" in err.lower()


# --- usage errors: clear message, not a traceback -----------------------------------


def test_malformed_streams_spec_is_a_usage_error(tmp_path, capsys):
    code = main(
        [*BASE_ARGV, "pcap", "--streams", "not-a-spec", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(_FakeCatalogClient({})),
    )

    assert code == 2
    assert "not-a-spec" in capsys.readouterr().err


def test_malformed_interval_is_a_usage_error(tmp_path, capsys):
    code = main(
        [*BASE_ARGV, "pcap", "--streams", "0", "--start", "0", "--end", "10", "--interval", "not-a-duration", "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(_FakeCatalogClient({})),
    )

    assert code == 2


def test_missing_required_flags_is_a_usage_error(capsys):
    code = main([*BASE_ARGV, "pcap"], client_factory=lambda args: _NullContext(_FakeCatalogClient({})))

    assert code == 2


# --- SIGINT: resumable, no traceback -------------------------------------------------


def test_sigint_mid_run_exits_130_and_leaves_a_resumable_state(tmp_path, capsys):
    factory, _adapter = _pcap_client_factory(
        [
            _resp(200, content=pcap_body(b"first-window")),
            KeyboardInterrupt(),
            _resp(200, content=b'{"guid": "ctid"}'),  # DELETE /axapi/task/<ctid> abort response
        ]
    )

    code = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "30", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory,
    )

    assert code == 130
    err = capsys.readouterr().err
    assert "resume" in err.lower()
    # The first window completed and was recorded; nothing crashed as a
    # traceback, and no `.part` file was left behind by the aborted window.
    assert (tmp_path / "0.pcap").exists()
    assert list(tmp_path.glob("*.part")) == []
    assert [r.outcome for r in read_manifest(tmp_path).records] == ["written"]

    # A follow-up invocation with the interrupted window's response queued
    # resumes cleanly rather than re-fetching the already-written window.
    factory2, adapter2 = _pcap_client_factory([_resp(200, content=pcap_body(b"second-window")), _resp(200, content=pcap_body(b"third-window"))])
    code2 = main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "30", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory2,
    )

    assert code2 == 0
    assert len(adapter2.requests) == 2  # only the two not-yet-done windows
    assert {p.name for p in tmp_path.glob("*.pcap")} == {"0.pcap", "10.pcap", "20.pcap"}


# --- verbatim stream_ids passthrough on the wire ------------------------------------


def test_pcap_call_sends_streams_verbatim(tmp_path):
    factory, adapter = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])

    main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--filter", "tcp and port 443", "--out", str(tmp_path)],
        client_factory=factory,
    )

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["stream_ids"] == ["2-4"]
    assert query["filter"] == ["tcp and port 443"]


# --- _run_mapping_errors: a known xtract error vs. an unexpected bug -----------


def test_run_mapping_errors_distinguishes_a_known_xtract_error_from_a_bug(capsys):
    """Every module's error root is now an `XtractError` (finding 10). A
    typed, known xtract error that reaches `_run_mapping_errors` without
    being caught more specifically anywhere else must not be mislabeled as
    "unexpected error" — that label is reserved for a genuine internal bug
    (`TypeError`, `AttributeError`, ...), not a recognized `xtract` failure
    mode nobody happened to catch inline.
    """

    class _SomeXtractError(XtractError):
        pass

    known_code = _run_mapping_errors(lambda: (_ for _ in ()).throw(_SomeXtractError("boom")))
    known_err = capsys.readouterr().err

    unexpected_code = _run_mapping_errors(lambda: (_ for _ in ()).throw(TypeError("not an xtract error")))
    unexpected_err = capsys.readouterr().err

    assert known_code == 4
    assert unexpected_code == 4
    assert "unexpected error" not in known_err
    assert "boom" in known_err
    assert "unexpected error" in unexpected_err


def test_cli_does_not_redeclare_ns_per_second(tmp_path):
    """`cli.py` used to carry its own private `_NS_PER_SECOND` copy of
    `timeutil.NS_PER_SECOND` for display formatting — a duplicate constant
    that could silently drift from the source of truth. This is now the same
    imported name, not a redeclared literal.
    """
    assert not hasattr(cli, "_NS_PER_SECOND")
    assert cli.NS_PER_SECOND is NS_PER_SECOND


def test_manifest_has_no_credential_material(tmp_path):
    factory, _adapter = _pcap_client_factory([_resp(200, content=pcap_body(b"one"))])

    main(
        [*BASE_ARGV, "pcap", "--streams", "2-4", "--start", "0", "--end", "10", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=factory,
    )

    raw = manifest_path(tmp_path).read_text()
    for forbidden in ("sk-test", "corpapcn", "Authorization", "Bearer"):
        assert forbidden not in raw
    for line in raw.splitlines():
        json.loads(line)  # every line is valid, self-contained JSON
