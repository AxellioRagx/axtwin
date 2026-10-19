"""Tests for aximprint.acquire.runner.

No test here opens a network socket: `_FakeCatalogClient`/`_FakeExtractClient`
below are call-order stubs, same discipline as `tests/acquire/test_catalog.py`'s
`_FakeClient` and `tests/acquire/test_extract.py`'s `FakeAdapter`. `tmp_path` stands
in for `--out`.
"""


import pytest

from aximprint.acquire import manifest, runner, windows
from aximprint.acquire.timeutil import (
    Duration,
    format_instant,
    parse_duration,
    parse_instant,
)
from aximprint.acquire.windows import Window, plan
from tests.acquire.conftest import pcap_body


def _d(seconds: int) -> Duration:
    return parse_duration(f"{seconds}s")


def _window(start: str, end: str, index=1, total=1) -> Window:
    return Window(start=parse_instant(start), end=parse_instant(end), index=index, total=total)


# --- Phase 1: --end resolution --------------------------------------------------


class _FakeCatalogClient:
    """Returns a canned `StreamRange.last` per stream id, keyed by the
    `params["stream"]` value `catalog.stream_range` sends.
    """

    def __init__(self, last_by_stream: dict[str, int | None]):
        self._last_by_stream = last_by_stream
        self.calls = []

    def request(self, method, path, *, params=None, expected_status=200):
        self.calls.append((method, path, params))
        stream = params["stream"]
        last = self._last_by_stream[stream]
        files = [] if last is None else [
            {
                "name": "a.pcap",
                "size": 10,
                # time_start/time_end are strings on the wire (measured
                # 2026-08-05 live probe) — this fixture is keyed by a plain
                # int for readability, stringified here.
                "time_start": str(last),
                "time_end": str(last),
                "epoch": 1,
                "seqnum": 1,
            }
        ]
        return _Resp({"files": files})


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_resolve_end_is_union_max_across_streams():
    client = _FakeCatalogClient({"2": 1_700_000_100, "3": 1_700_000_200, "4": 1_700_000_050})

    result = runner.resolve_end(client, [2, 3, 4], _d(60))

    assert result.end.ns == 1_700_000_200 * 1_000_000_000
    assert len(client.calls) == 3


def test_resolve_end_ignores_streams_with_no_data_but_keeps_them_in_report():
    client = _FakeCatalogClient({"2": 1_700_000_100, "3": None})

    result = runner.resolve_end(client, [2, 3], _d(60))

    assert result.end.ns == 1_700_000_100 * 1_000_000_000
    assert result.per_stream_last[3] is None


def test_resolve_end_all_streams_empty_raises_typed_error():
    client = _FakeCatalogClient({"2": None, "3": None})

    with pytest.raises(runner.AllStreamsEmptyError):
        runner.resolve_end(client, [2, 3], _d(60))


def test_resolve_end_divergence_warning_fires_past_one_interval():
    # 200s apart, 60s interval: divergence exceeds one interval.
    client = _FakeCatalogClient({"2": 1_700_000_000, "3": 1_700_000_200})

    result = runner.resolve_end(client, [2, 3], _d(60))

    assert result.warning is not None
    assert "2" in result.warning or "3" in result.warning


def test_resolve_end_no_warning_when_streams_agree_within_one_interval():
    client = _FakeCatalogClient({"2": 1_700_000_000, "3": 1_700_000_030})

    result = runner.resolve_end(client, [2, 3], _d(60))

    assert result.warning is None


def test_resolve_end_no_warning_with_a_single_stream():
    client = _FakeCatalogClient({"2": 1_700_000_000})

    result = runner.resolve_end(client, [2], _d(1))

    assert result.warning is None


# --- Phase 3: the collision guard ------------------------------------------------
#
# `check_collision` (read-only, never writes) and `ensure_manifest_header`
# (write-side, idempotent) are deliberately separate functions — a function
# named `check_*` that also writes as a side effect is a check-then-act
# smell, and the split is what lets `check_collision` run safely under
# `--dry-run` too (`cli.py`), which must never write a manifest.


def test_check_collision_is_read_only_on_a_fresh_manifest(tmp_path):
    mismatches = runner.check_collision(tmp_path, streams="2-4", filter=None, interval=_d(60), override=False)

    assert mismatches == []
    assert manifest.read_manifest(tmp_path).header is None  # never writes


def test_ensure_manifest_header_writes_a_fresh_header_on_a_new_manifest(tmp_path):
    header = runner.ensure_manifest_header(tmp_path, streams="2-4", filter=None, interval=_d(60), tool_version="0.0.1")

    assert header.streams == "2-4"
    assert manifest.read_manifest(tmp_path).header == header


def test_ensure_manifest_header_never_rewrites_an_existing_header(tmp_path):
    first = runner.ensure_manifest_header(tmp_path, streams="2-4", filter=None, interval=_d(60), tool_version="0.0.1")

    second = runner.ensure_manifest_header(tmp_path, streams="2-4", filter=None, interval=_d(60), tool_version="9.9.9")

    assert second == first  # the header on disk is from the first call, not silently updated


def test_check_collision_passes_on_a_matching_repeat_invocation(tmp_path):
    runner.ensure_manifest_header(tmp_path, streams="2-4", filter="tcp", interval=_d(60), tool_version="0.0.1")

    mismatches = runner.check_collision(tmp_path, streams="2-4", filter="tcp", interval=_d(60), override=False)

    assert mismatches == []


@pytest.mark.parametrize(
    "field, first, second",
    [
        ("streams", {"streams": "2-4"}, {"streams": "5-6"}),
        ("filter", {"filter": "tcp"}, {"filter": "udp"}),
        ("interval", {"interval": _d(60)}, {"interval": _d(30)}),
    ],
)
def test_check_collision_fires_on_each_mismatch(tmp_path, field, first, second):
    base = {"streams": "2-4", "filter": None, "interval": _d(60)}
    runner.ensure_manifest_header(tmp_path, tool_version="0.0.1", **{**base, **first})

    with pytest.raises(runner.ManifestCollisionError) as exc_info:
        runner.check_collision(tmp_path, override=False, **{**base, **second})
    assert field in str(exc_info.value)


def test_check_collision_none_and_empty_string_filter_are_not_a_mismatch(tmp_path):
    runner.ensure_manifest_header(tmp_path, streams="2-4", filter=None, interval=_d(60), tool_version="0.0.1")

    mismatches = runner.check_collision(tmp_path, streams="2-4", filter="", interval=_d(60), override=False)

    assert mismatches == []


def test_check_collision_override_returns_mismatches_instead_of_raising(tmp_path):
    runner.ensure_manifest_header(tmp_path, streams="2-4", filter=None, interval=_d(60), tool_version="0.0.1")

    mismatches = runner.check_collision(tmp_path, streams="9-9", filter=None, interval=_d(60), override=True)

    assert mismatches != []
    # a header already on disk is never rewritten, even under override — the
    # files already on disk correspond to it, not to the new (mismatched) request.
    header = runner.ensure_manifest_header(tmp_path, streams="9-9", filter=None, interval=_d(60), tool_version="0.0.1")
    assert header.streams == "2-4"


# --- Phase 3: resume --------------------------------------------------------------


def test_resume_skips_a_window_whose_pcap_file_already_exists(tmp_path):
    (tmp_path / "1000.pcap").write_bytes(b"data")
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is True
    assert "already present" in decisions[0].reason


def test_resume_skips_a_window_recorded_empty(tmp_path):
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="1000", end="1010", outcome="empty", bytes=0, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is True
    assert "empty" in decisions[0].reason


def test_resume_retries_a_window_recorded_failed(tmp_path):
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="1000", end="1010", outcome="failed", bytes=0, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False


def test_resume_uses_the_latest_record_when_a_window_was_retried(tmp_path):
    for outcome in ("failed", "empty"):
        manifest.append_window_record(
            tmp_path,
            manifest.WindowRecord(
                start="1000", end="1010", outcome=outcome, bytes=0, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
            ),
        )
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is True  # the later "empty" record wins over the earlier "failed" one


def test_resume_part_file_never_satisfies_resume(tmp_path):
    (tmp_path / "1000.pcap.part").write_bytes(b"stale")
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False


def test_resume_no_record_no_file_is_a_fresh_attempt(tmp_path):
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False
    assert decisions[0].reason is None


def test_resume_zero_byte_pcap_file_does_not_satisfy_resume(tmp_path):
    """A `touch`ed (or otherwise zero-byte) `.pcap` must not be mistaken for
    a completed window — `extract_window` never produces one (a zero-byte
    `200` response is recorded as `Empty`, never renamed into place), so a
    zero-byte file here can only be leftover debris, not a real result.
    """
    (tmp_path / "1000.pcap").touch()
    windows = [_window("1000", "1010")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False


def test_resume_reextracts_a_window_whose_recorded_end_is_shorter_than_planned(tmp_path):
    """Reproduces the reviewer's finding: `out/<startTs>.pcap` names only the
    window's *start*, so a window clipped by a smaller `--end` on an earlier
    run is indistinguishable, by filename alone, from the full-length window
    a later, larger `--end` plans for that same start. RUN1's `[20, 25)`
    (clipped by `--end 25`) must not satisfy RUN2's `[20, 30)`.
    """
    (tmp_path / "20.pcap").write_bytes(b"clipped-data")
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="20", end="25", outcome="written", bytes=12, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("20", "30")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False
    assert decisions[0].reextract is True


def test_resume_does_not_reextract_when_the_recorded_end_matches(tmp_path):
    (tmp_path / "20.pcap").write_bytes(b"full-data")
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="20", end="30", outcome="written", bytes=9, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("20", "30")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is True
    assert decisions[0].reextract is False


def test_resume_survives_a_q22_constant_flip(tmp_path, monkeypatch):
    """A manifest record written under one `Q22_END_TIME_INCLUSIVE` value must
    still be recognized as done after the constant flips — because the
    manifest stores the window's *logical* end (`format_instant(window.end)`),
    never the wire-encoded `end_time` that Q22 adjusts. Flipping the constant
    changes only what's sent to `GET /axapi/pcap`, never the resume key.
    """
    (tmp_path / "20.pcap").write_bytes(b"full-data")
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="20", end="30", outcome="written", bytes=9, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    plan_windows = [_window("20", "30")]

    monkeypatch.setattr(windows, "Q22_END_TIME_INCLUSIVE", not windows.Q22_END_TIME_INCLUSIVE)
    decisions = runner.plan_resume(tmp_path, plan_windows)

    assert decisions[0].skip is True
    assert decisions[0].reextract is False


def test_resume_reextracts_a_clipped_empty_record_too(tmp_path):
    """The same clipping bug applies to an `"empty"` outcome: a window
    recorded empty for a shorter `--end` might have data once `--end` grows,
    since 418 only means "no data in *that* window", not "no data ever".
    """
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="20", end="25", outcome="empty", bytes=0, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("20", "30")]

    decisions = runner.plan_resume(tmp_path, windows)

    assert decisions[0].skip is False
    assert decisions[0].reextract is True


def test_cleanup_stale_part_files_removes_them(tmp_path):
    (tmp_path / "1000.pcap.part").write_bytes(b"stale")
    (tmp_path / "1010.pcap.part").write_bytes(b"stale")
    (tmp_path / "1020.pcap").write_bytes(b"real")

    removed = runner.cleanup_stale_part_files(tmp_path)

    assert removed == 2
    assert list(tmp_path.glob("*.part")) == []
    assert (tmp_path / "1020.pcap").exists()


def test_cleanup_stale_part_files_on_nonexistent_dir_is_a_noop(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert runner.cleanup_stale_part_files(missing) == 0


# --- Phase 4: ctid derivation -----------------------------------------------------


def test_run_id_is_deterministic_for_the_same_parameters():
    a = runner.run_id_for("2-4", "tcp", _d(60))
    b = runner.run_id_for("2-4", "tcp", _d(60))
    assert a == b


def test_run_id_changes_with_any_parameter():
    base = runner.run_id_for("2-4", "tcp", _d(60))
    assert runner.run_id_for("5-6", "tcp", _d(60)) != base
    assert runner.run_id_for("2-4", "udp", _d(60)) != base
    assert runner.run_id_for("2-4", "tcp", _d(30)) != base


def test_ctid_stable_across_a_resumed_run():
    run_id = runner.run_id_for("2-4", None, _d(60))
    window = _window("1000", "1060", index=3, total=10)

    ctid1 = runner.ctid_for(run_id, window)
    ctid2 = runner.ctid_for(run_id, window)

    assert ctid1 == ctid2
    assert "3" in ctid1


# --- Phase 4: the batch loop -------------------------------------------------------


def _script_client(script, **client_kwargs):
    import requests

    from aximprint.acquire.auth import Credential
    from aximprint.acquire.client import Client

    class FakeAdapter(requests.adapters.BaseAdapter):
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

    adapter = FakeAdapter(script)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    client_kwargs.setdefault("max_retries", 0)  # a scripted 500 must not be masked by client.py's own retry budget
    client = Client("https://host", Credential.from_api_key("sk-test"), session=session, **client_kwargs)
    return client, adapter


def _resp(status_code, content=b"", headers=None, reason="OK"):
    import requests

    resp = requests.Response()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers.update(headers or {})
    resp._content = content
    resp._content_consumed = True
    resp.url = "https://fake-host/whatever"
    return resp


def test_execute_writes_manifest_and_files_for_each_outcome(tmp_path):
    client, _adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1")), _resp(418), _resp(500)])
    windows = plan(parse_instant("0"), parse_instant("30"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    summary = runner.execute(
        client,
        out_dir=tmp_path,
        windows=windows,
        decisions=decisions,
        streams="2-4",
        filter=None,
        run_id=run_id,
        fail_fast=False,
    )

    assert summary.written == 1
    assert summary.empty == 1
    assert summary.failed == 1
    assert summary.skipped == 0
    assert (tmp_path / "0.pcap").exists()
    data = manifest.read_manifest(tmp_path)
    assert [r.outcome for r in data.records] == ["written", "empty", "failed"]


def test_execute_records_the_logical_end_not_the_wire_end_time(tmp_path):
    """The manifest's `end` field must be `format_instant(window.end)` — the
    window's logical end — never `window.wire_params()['end_time']`, which
    carries the Q22 inclusive-end transport adjustment (end - 1ns). Recording
    the wire form would couple the resume key to a transport quirk (see
    `plan_resume`'s and `WindowRecord`'s docstrings).
    """
    client, _adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1"))])
    windows_ = plan(parse_instant("0"), parse_instant("10"), _d(10))
    assert windows_[0].wire_params()["end_time"] != format_instant(windows_[0].end)  # sanity: Q22 really adjusts it
    decisions = runner.plan_resume(tmp_path, windows_)
    run_id = runner.run_id_for("2-4", None, _d(10))

    runner.execute(
        client, out_dir=tmp_path, windows=windows_, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    record = manifest.read_manifest(tmp_path).records[0]
    assert record.end == format_instant(windows_[0].end) == "10"


def test_execute_fail_fast_stops_after_first_failure(tmp_path):
    client, _adapter = _script_client([_resp(500), _resp(200, content=pcap_body(b"never-reached"))])
    windows = plan(parse_instant("0"), parse_instant("20"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    summary = runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=True
    )

    assert summary.failed == 1
    assert summary.written == 0
    assert len(manifest.read_manifest(tmp_path).records) == 1


def test_execute_without_fail_fast_continues_past_a_failure(tmp_path):
    client, _adapter = _script_client([_resp(500), _resp(200, content=pcap_body(b"second-window"))])
    windows = plan(parse_instant("0"), parse_instant("20"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    summary = runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    assert summary.failed == 1
    assert summary.written == 1


def test_execute_skipped_windows_never_call_extract(tmp_path):
    (tmp_path / "0.pcap").write_bytes(b"already-here")
    client, adapter = _script_client([_resp(200, content=pcap_body(b"second-window"))])
    windows = plan(parse_instant("0"), parse_instant("20"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    summary = runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    assert summary.skipped == 1
    assert summary.written == 1
    assert len(adapter.requests) == 1  # only the non-skipped window issued a request


def test_execute_cleans_up_stale_part_files_before_running(tmp_path):
    (tmp_path / "0.pcap.part").write_bytes(b"stale")
    client, _adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1"))])
    windows = plan(parse_instant("0"), parse_instant("10"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    assert not (tmp_path / "0.pcap.part").exists()
    assert (tmp_path / "0.pcap").exists()


def test_execute_interrupt_stops_cleanly_and_reports_interrupted(tmp_path):
    client, _adapter = _script_client(
        [
            KeyboardInterrupt(),
            _resp(200, content=b'{"guid": "ctid"}'),  # DELETE /axapi/task/<ctid> response
            _resp(200, content=pcap_body(b"never-reached")),
        ]
    )
    windows = plan(parse_instant("0"), parse_instant("20"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    summary = runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    assert summary.interrupted is True
    assert summary.written == 0
    assert manifest.read_manifest(tmp_path).records == []  # the in-flight window never got a record


def test_execute_ctid_matches_run_id_and_window_index(tmp_path):
    client, adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1"))])
    windows = plan(parse_instant("0"), parse_instant("10"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["ctid"] == [runner.ctid_for(run_id, windows[0])]


def test_execute_streams_passed_verbatim_never_expanded(tmp_path):
    client, adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1"))])
    windows = plan(parse_instant("0"), parse_instant("10"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))

    runner.execute(
        client, out_dir=tmp_path, windows=windows, decisions=decisions, streams="2-4", filter=None, run_id=run_id, fail_fast=False
    )

    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["stream_ids"] == ["2-4"]


def test_runner_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(runner.RunnerError, XtractError)


def test_execute_raises_loudly_on_a_windows_decisions_length_mismatch(tmp_path):
    """`execute()` zips `windows` and `decisions` together; a caller bug that
    passes mismatched lengths must raise loudly (`zip(..., strict=True)`),
    not silently truncate to the shorter one and run an incomplete plan.

    Every window here is pre-marked "already done" (skip) so no network call
    is needed before `zip`'s length check fires on the final, dropped entry.
    """
    client, _adapter = _script_client([])
    windows = plan(parse_instant("0"), parse_instant("40"), _d(10))
    for w in windows:
        (tmp_path / f"{w.wire_params()['start_time']}.pcap").write_bytes(b"already-done")
    decisions = runner.plan_resume(tmp_path, windows)[:-1]  # force a length mismatch
    run_id = runner.run_id_for("2-4", None, _d(10))

    with pytest.raises(ValueError):
        runner.execute(
            client,
            out_dir=tmp_path,
            windows=windows,
            decisions=decisions,
            streams="2-4",
            filter=None,
            run_id=run_id,
            fail_fast=False,
        )


def test_execute_on_window_callback_fires_for_every_window(tmp_path):
    client, _adapter = _script_client([_resp(200, content=pcap_body(b"bytes-1")), _resp(418)])
    windows = plan(parse_instant("0"), parse_instant("20"), _d(10))
    decisions = runner.plan_resume(tmp_path, windows)
    run_id = runner.run_id_for("2-4", None, _d(10))
    events = []

    runner.execute(
        client,
        out_dir=tmp_path,
        windows=windows,
        decisions=decisions,
        streams="2-4",
        filter=None,
        run_id=run_id,
        fail_fast=False,
        on_window=lambda w, event, detail: events.append((w.index, event)),
    )

    assert events == [(1, "written"), (2, "empty")]


def test_execute_reports_a_reextracted_window_distinctly_from_written(tmp_path):
    """A window `plan_resume` flagged `reextract=True` (finding 1) is still
    successfully written on disk (the new, full-length pcap overwrites the
    old, clipped one), but `execute()` must surface it as its own, separate
    `reextracted` count/event — not silently folded into an ordinary
    `written` — so an operator can see the re-extraction happened.
    """
    (tmp_path / "20.pcap").write_bytes(b"clipped-data")
    manifest.append_window_record(
        tmp_path,
        manifest.WindowRecord(
            start="20", end="25", outcome="written", bytes=12, task_guid=None, duration_s=0.1, attempt=1, timestamp="t"
        ),
    )
    windows = [_window("20", "30", index=1, total=1)]
    decisions = runner.plan_resume(tmp_path, windows)
    assert decisions[0].reextract is True  # sanity: this is the scenario under test
    client, _adapter = _script_client([_resp(200, content=pcap_body(b"full-data"))])
    run_id = runner.run_id_for("2-4", None, _d(10))
    events = []

    summary = runner.execute(
        client,
        out_dir=tmp_path,
        windows=windows,
        decisions=decisions,
        streams="2-4",
        filter=None,
        run_id=run_id,
        fail_fast=False,
        on_window=lambda w, event, detail: events.append(event),
    )

    assert summary.written == 1  # still counted as written...
    assert summary.reextracted == 1  # ...and separately flagged as a re-extraction
    assert events == ["reextracted"]
    assert (tmp_path / "20.pcap").read_bytes() == pcap_body(b"full-data")  # the clipped file was overwritten
