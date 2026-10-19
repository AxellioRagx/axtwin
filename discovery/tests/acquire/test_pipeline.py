"""Tests for aximprint.acquire.harvest.pipeline.

No test here opens a network socket (enforced project-wide by `tests/conftest.py`) or invokes
`podman` — every extraction goes through `_FakeHarvestClient` (below, `client`-shaped: `.request()`
returns a scripted response or raises a scripted exception, keyed by request content rather than
call order) and every Zeek run goes through `aximprint.acquire.harvest.zeek.FakeZeekRunner` (production code,
per its own module docstring) or a small hand-rolled `ZeekRunner` double for the handful of cases
`FakeZeekRunner` itself can't model (a failing `probe()`).

`_FakeHarvestClient` is keyed by request content, not call order, **because pipeline.py issues
concurrent requests** — unlike `tests/acquire/test_runner.py`'s sequential `FakeAdapter` (fine for
`runner.py`'s serial loop), the order concurrent workers' requests arrive here is not
deterministic. Concurrency tests use `threading.Event`s to prove genuine blocking, the same
pattern already established in `tests/acquire/test_spool.py`, never `sleep`.
"""

from __future__ import annotations

import queue
import threading
import types
from pathlib import Path

import pytest

from aximprint.acquire.errors import AuthenticationError, ServerError
from aximprint.acquire.harvest import pipeline
from aximprint.acquire.harvest.spool import Spool
from aximprint.acquire.harvest.zeek import (
    TIMEOUT_RETURNCODE,
    FakeZeekRunner,
    ProbeResult,
    ZeekResult,
)
from aximprint.acquire.timeutil import Duration, Instant, parse_duration, parse_instant
from aximprint.acquire.windows import Window
from tests.acquire.conftest import pcap_body

# --- shared fixtures and fakes ------------------------------------------------------------------


def _d(seconds: int) -> Duration:
    return parse_duration(f"{seconds}s")


def _now() -> Instant:
    return parse_instant("1700000100")


def _window(start: str, end: str, index: int = 1, total: int = 1) -> Window:
    return Window(start=parse_instant(start), end=parse_instant(end), index=index, total=total)


def _config(tmp_path, **overrides):
    kwargs = {
        "streams": "2-4",
        "start_expr": "1700000000",
        "end_expr": "1700000030",
        "interval": _d(10),
        "out_dir": tmp_path / "out",
        "spool_cap_bytes": 10_000_000,
        "extract_concurrency": 2,
        "zeek_workers": 2,
        "stall_timeout_s": 5.0,
    }
    kwargs.update(overrides)
    return pipeline.HarvestConfig(**kwargs)


class _PcapResponse:
    """A `client.request()`-shaped stand-in for a real `requests.Response`, minimal enough for
    `extract.py`'s `_stream_to_part`/`iter_stream_body` (`.headers.get`, `.iter_content`).
    """

    def __init__(self, content: bytes = b"", headers: dict | None = None):
        self._content = content
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        if self._content:
            yield self._content


class _JsonResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeHarvestClient:
    """One fake covering every request `pipeline.py` (directly, or through the modules it calls
    unchanged — `catalog.list_streams`, `runner.resolve_end`, `extract_window`) can issue:
    `GET /axapi/fs/streams` (preflight's credentials probe), `GET /axapi/fs/files` (`--end`
    resolution when omitted), `GET /axapi/pcap` (extraction, scripted per window `start_time`),
    and `DELETE /axapi/task/<ctid>` (best-effort abort on interrupt).

    `pcap_by_start[start_time]` may be a `_PcapResponse`, a `BaseException` instance (raised), or a
    zero-arg callable returning either (evaluated at request time — how the backpressure test below
    blocks a request under test control without a `sleep`).
    """

    #: `Client.host`, for the vantage manifest `run_harvest` writes beside the cold tier
    #: (`docs/WORK_ORDER_visibility_and_vantage.md` D2) — see `test_cli_harvest.py`'s note.
    host = "https://fake-appliance.test"

    def __init__(self, *, pcap_by_start=None, streams_body=None, files_by_stream=None):
        self._pcap_by_start = pcap_by_start or {}
        self._streams_body = streams_body if streams_body is not None else []
        self._files_by_stream = files_by_stream or {}
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str, dict | None]] = []
        self.aborted_ctids: list[str] = []

    def request(self, method, path, *, params=None, json=None, stream=False, expected_status=200):
        with self._lock:
            self.calls.append((method, path, dict(params) if params else None))

        if method == "DELETE":
            ctid = path.rsplit("/", 1)[-1]
            with self._lock:
                self.aborted_ctids.append(ctid)
            return _JsonResponse({})
        if path == "/axapi/fs/streams":
            return _JsonResponse(self._streams_body)
        if path == "/axapi/fs/files":
            stream = params["stream"]
            return _JsonResponse({"files": self._files_by_stream.get(stream, [])})
        if path == "/axapi/pcap":
            item = self._pcap_by_start[params["start_time"]]
            if callable(item) and not isinstance(item, BaseException):
                item = item()
            if isinstance(item, BaseException):
                raise item
            return item
        raise AssertionError(f"unscripted request: {method} {path} {params}")  # pragma: no cover

    def pcap_calls(self) -> list[dict]:
        with self._lock:
            return [c[2] for c in self.calls if c[1] == "/axapi/pcap"]


def _written(content: bytes = b"pcap-bytes") -> _PcapResponse:
    # `pcap_body()`-wrapped: `extract_window` now rejects a 200 body that doesn't start with a
    # real pcap magic (see `tests/conftest.py`'s `pcap_body` docstring) — a bare `content` here
    # would be misclassified as `Failed`, not `Written`.
    return _PcapResponse(content=pcap_body(content), headers={"X-Task-GUID": "guid-1"})


class _EventLog:
    """Thread-safe ordered event recorder for `on_event` — used to assert cross-stage ordering
    (extract -> zeek), never timing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[tuple[str, str, str]] = []

    def __call__(self, window_id: str, event: str, detail: str) -> None:
        with self._lock:
            self.events.append((window_id, event, detail))


class _ProbeFailingZeekRunner:
    """A `ZeekRunner` whose `probe()` always fails — `FakeZeekRunner` itself always succeeds
    (module docstring: "always available"), so this hand-rolled double is the only way to exercise
    `preflight`'s zeek-probe-failure branch.
    """

    def probe(self) -> ProbeResult:
        return ProbeResult(ok=False, podman_available=False, image_present=False, zeek_version=None, detail="no podman here")

    def run(self, pcap, out_dir):  # pragma: no cover -- must never be reached
        raise AssertionError("run() must never be called when preflight failed")


# --- Phase 1: seed_bytes_for, plan_harvest, preflight --------------------------------------------


def test_seed_bytes_for_matches_measured_baseline_one_stream_300s():
    assert pipeline.seed_bytes_for("0", _d(300)) == 45_700_000


def test_seed_bytes_for_scales_with_stream_count():
    assert pipeline.seed_bytes_for("0-2", _d(300)) == 3 * 45_700_000


def test_seed_bytes_for_scales_with_interval():
    assert pipeline.seed_bytes_for("0", _d(600)) == 2 * 45_700_000
    assert pipeline.seed_bytes_for("0", _d(150)) == 45_700_000 // 2


def test_seed_bytes_for_errs_high_via_ceiling_division():
    # 45_700_000 * 100 / 300 = 15_233_333.33... — must round UP, never down (LES: under-
    # estimating overshoots the cap; over-estimating only costs throughput).
    seed = pipeline.seed_bytes_for("0", _d(100))
    assert seed == 15_233_334
    assert seed * 300 > 45_700_000 * 100  # strictly erring high, never truncating


def test_plan_harvest_explicit_end_needs_no_client():
    config = pipeline.HarvestConfig(
        streams="2-4",
        start_expr="1700000000",
        end_expr="1700000030",
        interval=_d(10),
        out_dir=None,
        spool_cap_bytes=1,
    )
    result = pipeline.plan_harvest(config, client=None, now=_now())
    assert len(result.windows) == 3
    assert result.seed_bytes > 0


def test_plan_harvest_omitted_end_without_client_is_not_computable():
    cfg = pipeline.HarvestConfig(
        streams="2-4", start_expr="1700000000", end_expr=None, interval=_d(10), out_dir=None, spool_cap_bytes=1
    )
    with pytest.raises(pipeline.PlanNotComputableError):
        pipeline.plan_harvest(cfg, client=None, now=_now())


def test_plan_harvest_bad_start_expression_is_not_computable():
    cfg = pipeline.HarvestConfig(
        streams="2-4", start_expr="not a valid expr", end_expr="1700000030", interval=_d(10), out_dir=None, spool_cap_bytes=1
    )
    with pytest.raises(pipeline.PlanNotComputableError):
        pipeline.plan_harvest(cfg, client=None, now=_now())


def test_plan_harvest_bad_streams_spec_is_not_computable():
    cfg = pipeline.HarvestConfig(
        streams="not-a-spec", start_expr="1700000000", end_expr="1700000030", interval=_d(10), out_dir=None, spool_cap_bytes=1
    )
    with pytest.raises(pipeline.PlanNotComputableError):
        pipeline.plan_harvest(cfg, client=None, now=_now())


def test_plan_harvest_too_many_windows_without_yes_is_not_computable():
    cfg = pipeline.HarvestConfig(
        streams="0", start_expr="1700000000", end_expr="1700100000", interval=_d(1), out_dir=None, spool_cap_bytes=1
    )
    with pytest.raises(pipeline.PlanNotComputableError):
        pipeline.plan_harvest(cfg, client=None, now=_now())


def test_plan_harvest_too_many_windows_with_yes_succeeds():
    cfg = pipeline.HarvestConfig(
        streams="0", start_expr="1700000000", end_expr="1700020000", interval=_d(1), out_dir=None, spool_cap_bytes=1, yes=True
    )
    result = pipeline.plan_harvest(cfg, client=None, now=_now())
    assert len(result.windows) == 20_000


def test_plan_harvest_all_streams_empty_propagates_unwrapped(tmp_path):
    client = _FakeHarvestClient(files_by_stream={})  # every stream resolves to no files
    cfg = _config(tmp_path, end_expr=None)
    from aximprint.acquire.runner import AllStreamsEmptyError

    with pytest.raises(AllStreamsEmptyError):
        pipeline.plan_harvest(cfg, client=client, now=_now())


def test_plan_harvest_resolves_end_via_catalog_when_omitted(tmp_path):
    client = _FakeHarvestClient(
        files_by_stream={
            "2": [{"name": "a.pcap", "size": 1, "time_start": "1700000000", "time_end": "1700000010", "epoch": 1, "seqnum": 1}],
            "3": [{"name": "b.pcap", "size": 1, "time_start": "1700000000", "time_end": "1700000020", "epoch": 1, "seqnum": 1}],
            "4": [{"name": "c.pcap", "size": 1, "time_start": "1700000000", "time_end": "1700000005", "epoch": 1, "seqnum": 1}],
        }
    )
    cfg = _config(tmp_path, end_expr=None)
    result = pipeline.plan_harvest(cfg, client=client, now=_now())
    assert result.end == parse_instant("1700000020")


def test_preflight_fails_on_zeek_probe_before_any_extraction(tmp_path):
    client = _FakeHarvestClient()
    cfg = _config(tmp_path)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    with pytest.raises(pipeline.ZeekProbeFailedError):
        pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=_ProbeFailingZeekRunner())

    assert client.pcap_calls() == []


def test_preflight_fails_on_unwritable_spool_root_before_any_extraction(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "spool").write_bytes(b"not a directory")  # blocks Spool's own mkdir(parents=True)

    client = _FakeHarvestClient()
    cfg = _config(tmp_path, out_dir=out_dir)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    with pytest.raises(pipeline.SpoolRootNotWritableError):
        pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert client.pcap_calls() == []


def test_preflight_fails_on_bad_credentials_before_any_extraction(tmp_path):
    class _UnauthorizedClient:
        def request(self, *a, **k):
            raise AuthenticationError("bad credentials")

    cfg = _config(tmp_path)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    with pytest.raises(AuthenticationError):
        pipeline.run_harvest(cfg, plan=plan, client=_UnauthorizedClient(), zeek_runner=FakeZeekRunner())


def test_preflight_failure_modes_are_distinct(tmp_path):
    """The four Phase-1 checks fail with four distinct, individually-catchable types."""
    assert issubclass(pipeline.ZeekProbeFailedError, pipeline.PreflightError)
    assert issubclass(pipeline.SpoolRootNotWritableError, pipeline.PreflightError)
    assert issubclass(pipeline.PlanNotComputableError, pipeline.PreflightError)
    assert pipeline.ZeekProbeFailedError is not pipeline.SpoolRootNotWritableError
    assert pipeline.ZeekProbeFailedError is not pipeline.PlanNotComputableError


# --- Phase 2: the extract stage -------------------------------------------------------------------


def test_admit_commit_handoff_ordering(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())
    events = _EventLog()

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(), on_event=events)

    assert report.extracted == 1
    assert report.zeeked == 1
    assert report.released == 1
    kinds = [e[1] for e in events.events if e[0] == "1700000000"]
    assert kinds == ["extracted", "zeeked"]  # extraction always precedes its own Zeek run


def test_empty_window_releases_reservation_and_never_reaches_zeek(tmp_path):
    from aximprint.acquire.errors import NoDataError

    client = _FakeHarvestClient(pcap_by_start={"1700000000": NoDataError("no data for this window")})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())
    zeek_runner = FakeZeekRunner()

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=zeek_runner)

    assert report.empty == 1
    assert report.extracted == 0
    assert report.zeeked == 0
    assert not (cfg.out_dir / "spool" / "1700000000.pcap").exists()
    assert not (cfg.out_dir / "zeek" / "1700000000").exists()  # run() was never called for it


def test_failed_extraction_does_not_leak_a_reservation(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": ServerError("boom", status_code=500)})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.extract_failed == 1
    assert report.failed_windows == ("1700000000",)
    # No leaked reservation: the spool's own reconciliation, on a fresh construction over the same
    # root, finds nothing resident and nothing on disk.
    spool = Spool(cfg.out_dir / "spool", cap_bytes=1000)
    assert spool.resident_windows() == ()
    assert spool.usage() == 0


def test_backpressure_blocks_the_extract_worker_when_the_spool_is_full(tmp_path):
    """Two windows, a spool that can hold only one reservation at a time: the second worker's
    `admit()` must genuinely block until the first window's pcap lands (frees the estimate, since
    `commit()` replaces it with the tiny real size) — proven deterministically with the same
    `estimate()`-watching technique `tests/acquire/test_spool.py` already established, never a `sleep`.
    Exercises `pipeline._extract_one` directly (the real per-window worker logic `run_harvest`
    dispatches into a thread pool) so the ordering assertion isn't at the mercy of unrelated
    executor scheduling.
    """
    import queue

    from aximprint.acquire.harvest.spool import SizeEstimator

    spool = Spool(tmp_path / "spool", cap_bytes=60, estimator=SizeEstimator(seed_bytes=50))
    estimate_called = threading.Event()
    real_estimate = spool._estimator.estimate

    def _watched():
        value = real_estimate()
        estimate_called.set()
        return value

    spool._estimator.estimate = _watched

    hold_w1 = threading.Event()

    def _blocking_w1_response():
        hold_w1.wait(timeout=5)
        return _written(b"one")

    client = _FakeHarvestClient(
        pcap_by_start={"1700000000": _blocking_w1_response, "1700000010": _written(b"two")}
    )
    cfg = _config(tmp_path, out_dir=tmp_path)
    zeek_queue: queue.Queue = queue.Queue()
    stats = pipeline._RunStats()
    stop = threading.Event()

    w1 = _window("1700000000", "1700000010")
    w2 = _window("1700000010", "1700000020")

    t1 = threading.Thread(target=pipeline._extract_one, args=(client, w1, cfg, spool, zeek_queue, stats, None, stop))
    t1.start()

    estimate_called.clear()
    t2 = threading.Thread(target=pipeline._extract_one, args=(client, w2, cfg, spool, zeek_queue, stats, None, stop))
    t2.start()

    # Fires only once w2's admit() has evaluated (and failed) the capacity check under the spool's
    # own lock — proof it reached admission and is now genuinely parked, not proof of a race.
    assert estimate_called.wait(timeout=5), "background admit() for w2 never started"
    with spool._cond:
        assert "1700000010" not in spool._entries, "w2 admitted before w1 released any room"

    hold_w1.set()  # let w1's extraction complete -> commit() shrinks its footprint -> notifies
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive()
    assert not t2.is_alive()
    assert stats.extracted == 2


# --- Phase 3: the zeek stage and the reaper -------------------------------------------------------


def test_successful_zeek_run_releases_and_deletes(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.zeeked == 1
    assert report.released == 1
    assert report.zeek_failed == 0
    assert not (cfg.out_dir / "spool" / "1700000000.pcap").exists()


def test_failed_zeek_run_does_not_release_pcap_stays_on_disk(tmp_path):
    """THE invariant: a failed Zeek run must never release its pcap."""
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(returncode=1))

    assert report.zeek_failed == 1
    assert report.zeeked == 0
    assert report.released == 0
    assert report.zeek_failed_windows == ("1700000000",)
    assert (cfg.out_dir / "spool" / "1700000000.pcap").exists()  # still resident, retryable


def test_zeek_timeout_is_treated_as_failure_not_success(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(
        cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(returncode=TIMEOUT_RETURNCODE)
    )

    assert report.zeek_failed == 1
    assert report.zeeked == 0
    assert (cfg.out_dir / "spool" / "1700000000.pcap").exists()


def test_unrecognized_stems_reach_the_report(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(
        cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(stems=("conn", "some_future_log_type"))
    )

    assert report.unrecognized_stems == ("some_future_log_type",)
    assert report.zeeked == 1


def test_quiet_window_with_no_zeek_logs_handled_without_keyerror(tmp_path):
    """LES-035's sibling: `FakeZeekRunner(stems=())` models a quiet window Zeek processes
    successfully but that produces no logs at all — the common case on this appliance, not an edge
    case. Must not raise, and must still be released (the run succeeded; there was simply nothing
    to log).
    """
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(stems=()))

    assert report.zeeked == 1
    assert report.released == 1
    assert report.unrecognized_stems == ()


# --- Phase 3b: bounding retained-failure accumulation -----------------------------------------
#
# A failed Zeek run correctly leaves its pcap RESIDENT for retry (THE invariant, above) — these
# tests are for the bound on how many of those a single run tolerates before aborting loudly,
# rather than silently wedging a multi-day run over its spool cap with nothing able to release
# the space (measured 2026-08-07: 3 retained failures totalling 79.6 MB against a 60 MB cap).


def _zeek_result(*, ok: bool) -> ZeekResult:
    return ZeekResult(
        version="0.0.0-fake",
        logs=types.MappingProxyType({}),
        duration_s=0.01,
        returncode=0 if ok else 1,
        stderr_tail="" if ok else "boom",
        bytes_in=0,
    )


def test_check_retained_failure_limit_returns_none_under_threshold(tmp_path):
    cfg = _config(tmp_path, spool_cap_bytes=100, retained_failure_fraction=0.5)  # threshold: 50
    stats = pipeline._RunStats()

    stats.record_zeek_outcome("w1", _zeek_result(ok=False), failed_bytes=40)

    assert stats.check_retained_failure_limit(cfg) is None
    assert stats.zeek_fatal is None


def test_check_retained_failure_limit_trips_and_remembers_the_first_error(tmp_path):
    cfg = _config(tmp_path, spool_cap_bytes=100, retained_failure_fraction=0.5)  # threshold: 50
    stats = pipeline._RunStats()

    stats.record_zeek_outcome("w1", _zeek_result(ok=False), failed_bytes=40)
    assert stats.check_retained_failure_limit(cfg) is None

    stats.record_zeek_outcome("w2", _zeek_result(ok=False), failed_bytes=40)  # cumulative: 80 > 50
    first = stats.check_retained_failure_limit(cfg)

    assert isinstance(first, pipeline.RetainedFailureLimitExceededError)
    assert "80" in str(first)  # retained bytes
    assert "50" in str(first)  # the threshold it exceeded
    assert "w1" in str(first) and "w2" in str(first)
    assert stats.zeek_fatal is first

    # A further failure past the threshold does not replace the remembered error — first past
    # the line wins, matching `zeek_fatal`'s own docstring ("later ones are no-ops").
    stats.record_zeek_outcome("w3", _zeek_result(ok=False), failed_bytes=1000)
    second = stats.check_retained_failure_limit(cfg)
    assert second is None
    assert stats.zeek_fatal is first


def test_successful_zeek_runs_never_count_toward_retained_failure_bytes(tmp_path):
    cfg = _config(tmp_path, spool_cap_bytes=100, retained_failure_fraction=0.5)
    stats = pipeline._RunStats()

    stats.record_zeek_outcome("w1", _zeek_result(ok=True))  # no failed_bytes given: success only

    assert stats.retained_failed_bytes == 0
    assert stats.check_retained_failure_limit(cfg) is None


def test_run_harvest_aborts_when_retained_zeek_failures_exceed_the_configured_fraction(tmp_path):
    """End-to-end: two windows whose Zeek run always fails, sized and capped so the second
    failure crosses `retained_failure_fraction` — `run_harvest` must raise
    `RetainedFailureLimitExceededError` rather than complete the run normally, and neither
    window's pcap is released (both stay RESIDENT, retryable by a future run).
    """
    w1_bytes = pcap_body(b"1234567890")  # 14 bytes
    w2_bytes = pcap_body(b"9876543210")  # 14 bytes
    client = _FakeHarvestClient(
        pcap_by_start={"1700000000": _PcapResponse(content=w1_bytes), "1700000010": _PcapResponse(content=w2_bytes)}
    )
    cfg = _config(
        tmp_path,
        start_expr="1700000000",
        end_expr="1700000020",
        interval=_d(10),
        spool_cap_bytes=40,
        extract_concurrency=1,
        zeek_workers=1,
        retained_failure_fraction=0.5,  # threshold: 20 — first failure (14) doesn't trip it, second (28) does
        stall_timeout_s=5.0,
    )
    plan = pipeline.HarvestPlan(
        start=parse_instant("1700000000"),
        end=parse_instant("1700000020"),
        windows=[_window("1700000000", "1700000010"), _window("1700000010", "1700000020")],
        seed_bytes=10,
    )

    with pytest.raises(pipeline.RetainedFailureLimitExceededError) as exc_info:
        pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(returncode=1))

    assert "20" in str(exc_info.value)  # the threshold
    # Neither window was released — both pcaps stay RESIDENT for a future run's retry, exactly
    # the invariant a failed Zeek run always preserves.
    assert (cfg.out_dir / "spool" / "1700000000.pcap").exists()
    assert (cfg.out_dir / "spool" / "1700000010.pcap").exists()


# --- Phase 4: shutdown, interruption, the run report ----------------------------------------------


def test_sigint_mid_run_leaves_resumable_state_and_no_leaked_reservations(tmp_path):
    client = _FakeHarvestClient(
        pcap_by_start={
            "1700000000": _written(b"one"),
            "1700000010": KeyboardInterrupt(),
            "1700000020": _written(b"three"),  # must never be reached: cancelled while pending
        }
    )
    cfg = _config(tmp_path, start_expr="1700000000", end_expr="1700000030", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.interrupted is True
    assert report.extracted == 1  # only the window before the interrupt completed
    assert report.extract_failed == 0
    assert report.empty == 0
    assert "xtract-harvest-1700000010" in client.aborted_ctids  # extract_window's own abort path
    # No leaked reservation for the interrupted window: a fresh Spool over the same root finds it
    # untracked (fail() ran before extract_window's KeyboardInterrupt was re-raised).
    spool = Spool(cfg.out_dir / "spool", cap_bytes=1000)
    assert spool.state("1700000010") is None
    assert spool.state("1700000020") is None  # the cancelled, never-started window


def test_fail_fast_stops_admitting_after_first_failure(tmp_path):
    client = _FakeHarvestClient(
        pcap_by_start={
            "1700000000": _written(b"one"),
            "1700000010": ServerError("boom", status_code=500),
            "1700000020": _written(b"three"),
        }
    )
    cfg = _config(
        tmp_path, start_expr="1700000000", end_expr="1700000030", extract_concurrency=1, zeek_workers=1, fail_fast=True
    )
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.interrupted is False
    assert report.extract_failed == 1
    assert report.failed == 1
    # The third window was never attempted: cancelled while still pending in the executor.
    assert report.extracted + report.empty + report.extract_failed < report.planned


def test_without_fail_fast_a_failure_does_not_stop_the_run(tmp_path):
    client = _FakeHarvestClient(
        pcap_by_start={
            "1700000000": _written(b"one"),
            "1700000010": ServerError("boom", status_code=500),
            "1700000020": _written(b"three"),
        }
    )
    cfg = _config(
        tmp_path, start_expr="1700000000", end_expr="1700000030", extract_concurrency=1, zeek_workers=1, fail_fast=False
    )
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.extracted + report.empty + report.extract_failed == report.planned == 3
    assert report.extract_failed == 1
    assert report.extracted == 2


def test_report_counts_reconcile_with_a_mixed_run(tmp_path):
    from aximprint.acquire.errors import NoDataError

    client = _FakeHarvestClient(
        pcap_by_start={
            "1700000000": _written(b"written-ok"),  # zeek succeeds -> released
            "1700000010": NoDataError("no data"),  # empty, never reaches zeek
            "1700000020": ServerError("boom", status_code=500),  # extraction failure
        }
    )
    cfg = _config(tmp_path, start_expr="1700000000", end_expr="1700000030", extract_concurrency=2, zeek_workers=2)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.planned == 3
    assert report.extracted == 1
    assert report.empty == 1
    assert report.extract_failed == 1
    assert report.zeeked == 1
    assert report.zeek_failed == 0
    assert report.released == 1
    assert report.bytes_moved == len(pcap_body(b"written-ok"))
    assert report.failed == 1
    assert report.failed_windows == ("1700000020",)
    assert report.interrupted is False
    assert report.elapsed_s >= 0


def test_check_drift_detects_a_deliberate_mismatch(tmp_path):
    from aximprint.acquire.harvest.spool import SizeEstimator

    spool = Spool(tmp_path / "spool", cap_bytes=1000, estimator=SizeEstimator(seed_bytes=100))
    spool.admit("w1")
    (tmp_path / "spool" / "w1.pcap").write_bytes(b"1" * 10)
    spool.commit("w1", actual_bytes=999)  # deliberately wrong vs. the real 10-byte file

    with pytest.raises(pipeline.SpoolDriftError):
        pipeline.check_drift(spool)


def test_check_drift_passes_when_accounting_matches_disk(tmp_path):
    from aximprint.acquire.harvest.spool import SizeEstimator

    spool = Spool(tmp_path / "spool", cap_bytes=1000, estimator=SizeEstimator(seed_bytes=100))
    spool.admit("w1")
    (tmp_path / "spool" / "w1.pcap").write_bytes(b"1" * 10)
    spool.commit("w1")  # measures the real file

    pipeline.check_drift(spool)  # must not raise


# --- Phase 5: inherited orphans --------------------------------------------------------------------


def test_inherited_orphans_are_redriven_through_zeek_and_released(tmp_path):
    out_dir = tmp_path / "out"
    spool_root = out_dir / "spool"
    spool_root.mkdir(parents=True)
    (spool_root / "1699999000.pcap").write_bytes(b"orphan-pcap-bytes")

    client = _FakeHarvestClient()
    cfg = _config(tmp_path, out_dir=out_dir, start_expr="1700000000", end_expr="1700000000")  # empty plan: start == end
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())
    assert plan.windows == []

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.reconciled_orphans == 1
    assert report.zeeked == 1
    assert report.released == 1
    assert not (spool_root / "1699999000.pcap").exists()
    assert (out_dir / "zeek" / "1699999000" / "conn.log").exists()
    assert client.pcap_calls() == []  # never re-extracted — only re-driven through Zeek


def test_inherited_orphan_zeek_failure_leaves_it_resident_for_a_future_retry(tmp_path):
    out_dir = tmp_path / "out"
    spool_root = out_dir / "spool"
    spool_root.mkdir(parents=True)
    (spool_root / "1699999000.pcap").write_bytes(b"orphan-pcap-bytes")

    client = _FakeHarvestClient()
    cfg = _config(tmp_path, out_dir=out_dir, start_expr="1700000000", end_expr="1700000000")
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner(returncode=1))

    assert report.reconciled_orphans == 1
    assert report.zeek_failed == 1
    assert report.released == 0
    assert (spool_root / "1699999000.pcap").exists()  # retained, retryable by a future run


def test_orphan_backlog_does_not_permanently_consume_the_cap(tmp_path):
    """A reconciled orphan must not be permanently unreleasable — this run's own new window,
    admitted against a cap too small to hold both simultaneously, must still be able to make
    progress once the orphan's (successful) Zeek pass frees its share back. This is the hang H-3's
    review found: without Phase-5 re-drive, an inherited RESIDENT entry sits forever with nothing
    able to release it. `stall_timeout_s` (set short in `_config`) turns a regression here into a
    fast, loud test failure instead of a hang.
    """
    out_dir = tmp_path / "out"
    spool_root = out_dir / "spool"
    spool_root.mkdir(parents=True)
    (spool_root / "1699999000.pcap").write_bytes(b"1" * 10)  # orphan: 10 bytes resident

    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written(b"1234567890")})  # 10 bytes
    cfg = _config(
        tmp_path,
        out_dir=out_dir,
        start_expr="1700000000",
        end_expr="1700000010",
        interval=_d(10),
        spool_cap_bytes=15,  # room for one 10-byte resident at a time, not both simultaneously
        extract_concurrency=1,
        zeek_workers=1,
        stall_timeout_s=5.0,
    )
    # Hand-construct the plan with a small, fixed seed rather than routing through
    # `seed_bytes_for`'s real formula — this test only cares about the cap/orphan interaction.
    plan = pipeline.HarvestPlan(start=parse_instant("1700000000"), end=parse_instant("1700000010"), windows=[_window("1700000000", "1700000010")], seed_bytes=10)

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.reconciled_orphans == 1
    assert report.extracted == 1
    assert report.zeeked == 2  # the orphan and this run's own window
    assert report.released == 2
    assert not (spool_root / "1699999000.pcap").exists()
    assert not (spool_root / "1700000000.pcap").exists()


# --- `harvest --from-pcaps` (docs/WORK_ORDER_harvest_from_pcaps.md) -----------------------------
#
# The two properties the work order calls mutation-proof: a `--from-pcaps`-supplied pcap survives
# a run whether Zeek succeeds or fails on it (D3's ownership predicate), and a NORMAL (extracted)
# pcap is still deleted on success exactly as before (proving the ownership check added nothing
# that weakens the pre-existing invariant for `owned=True` items — see `_process_zeek_item`).


def _from_pcaps_config(tmp_path, source, **overrides) -> pipeline.FromPcapsConfig:
    kwargs = {"source": source, "out_dir": tmp_path / "out", "zeek_workers": 2}
    kwargs.update(overrides)
    return pipeline.FromPcapsConfig(**kwargs)


def test_plan_from_pcaps_discovers_and_sorts_windows(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000010.pcap").write_bytes(pcap_body(b"b"))
    (src / "1700000000.pcap").write_bytes(pcap_body(b"a"))

    plan = pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))

    assert [wid for wid, _path in plan.windows] == ["1700000000", "1700000010"]
    assert plan.non_epoch_shaped_window_ids == ()


def test_plan_from_pcaps_nonexistent_source_is_rejected(tmp_path):
    with pytest.raises(pipeline.FromPcapsSourceError):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, tmp_path / "does-not-exist"))


def test_plan_from_pcaps_empty_source_is_a_positive_gate_failure_not_a_zero_window_success(tmp_path):
    """CONVENTIONS §3: an unrecognized/empty shape is rejected, not silently accepted as a valid
    zero-window run.
    """
    src = tmp_path / "src"
    src.mkdir()

    with pytest.raises(pipeline.FromPcapsSourceError, match="no \\*.pcap files"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_rejects_an_empty_source_string(tmp_path):
    """Must-fix 3: `Path("")` normalizes to `Path(".")` before this function would ever see it as
    a `Path` — `FromPcapsConfig.source` stays `str | Path` (never `Path`-wrapped by the caller)
    specifically so this check can see the raw, still-empty string.
    """
    config = pipeline.FromPcapsConfig(source="", out_dir=tmp_path / "out")

    with pytest.raises(pipeline.FromPcapsSourceError, match="must not be empty"):
        pipeline.plan_from_pcaps(config)


def test_plan_from_pcaps_empty_source_string_does_not_silently_resolve_to_cwd(tmp_path, monkeypatch):
    """The exact regression must-fix 3 closes, verified live: `--from-pcaps ""` (e.g.
    `--from-pcaps "$SRC"` with `SRC` unset in a script) used to plan the operator's OWN CURRENT
    DIRECTORY as the source (`Path('')` -> `Path('.')` -> `.is_dir()` is `True`) rather than
    failing loud. A pcap sitting in the cwd must never be silently discovered this way.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "1700000000.pcap").write_bytes(pcap_body(b"should never be discovered"))
    config = pipeline.FromPcapsConfig(source="", out_dir=tmp_path / "out")

    with pytest.raises(pipeline.FromPcapsSourceError):
        pipeline.plan_from_pcaps(config)


def test_plan_from_pcaps_a_directory_with_non_pcap_files_only_is_still_empty(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "readme.txt").write_bytes(b"not a pcap")

    with pytest.raises(pipeline.FromPcapsSourceError):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


@pytest.mark.parametrize("bad_stem", ["", ".", "..", ".hidden"])
def test_plan_from_pcaps_rejects_a_hostile_window_id(tmp_path, bad_stem):
    """`spool.validate_window_id` pointed at a user-controlled directory for the first time (build
    step 2) — the same rejection set `tests/acquire/test_spool.py`'s
    `test_admit_rejects_unsafe_window_ids` proves for `Spool.admit`. A path separator or NUL can
    never survive as an actual filename stem from a real `Path.glob()` result (the OS forbids `/`
    and NUL in a filename), so this proves the REACHABLE subset of that rejection set — the
    unreachable two (`/`, NUL) are proven directly against `validate_window_id` itself below.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{bad_stem}.pcap").write_bytes(b"hostile")

    with pytest.raises(pipeline.FromPcapsSourceError, match="not a safe window id"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_a_hostile_filename_poisons_the_whole_batch_not_just_itself(tmp_path):
    """CONVENTIONS §3 ("an unrecognized shape is rejected, not passed through") applied to the
    whole directory listing, not just the offending file: `plan_from_pcaps` validates every
    candidate stem before returning ANY plan (it raises on the first bad one it finds, in sorted
    order, rather than collecting the good ones and skipping the bad). A directory holding one
    ordinary, valid pcap next to one hostile-stemmed one must reject the run in full — silently
    processing the valid pcap while skipping the hostile one would let an operator's run complete
    "successfully" short of what was actually on disk, with no signal that a window was dropped.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"valid")
    (src / "..pcap").write_bytes(b"hostile")  # stem "." -- rejected by validate_window_id

    with pytest.raises(pipeline.FromPcapsSourceError, match="not a safe window id"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_fresh_zeek_out_dir_refuses_a_window_id_that_escapes_the_zeek_root(tmp_path):
    """Defense-in-depth on the OTHER path that deletes files. `plan_from_pcaps` validates every
    stem before any of this runs, so this is unreachable today — it guards the day a second
    producer of `_ZeekWorkItem`s forgets to, which is precisely the role `Spool._path_for`'s
    assertion already plays for the spool.

    The canary is the point. `_fresh_zeek_out_dir` sweeps stale `*.log` files out of the directory
    it returns, so an escaping `window_id` doesn't merely compute a wrong path — it *deletes* in
    the wrong directory. This test fails loudly in both halves if the guard regresses: no raise,
    and a file the pipeline has no business touching is gone.

    Note this must be checked against the RESOLVED path. `zeek_root / ".."` has `.parent ==
    zeek_root`, so the unresolved parent comparison that suffices for `Spool._path_for` (whose
    `f"{window_id}.pcap"` suffix keeps `..` from ever being the final component) would pass `..`
    straight through to the sweep below. This test is what pins that distinction.
    """
    zeek_root = tmp_path / "zeek"
    zeek_root.mkdir()
    canary = tmp_path / "not-ours.log"
    canary.write_text("belongs to whatever else lives beside zeek_root")

    with pytest.raises(AssertionError, match="escaped zeek root"):
        pipeline._fresh_zeek_out_dir(zeek_root, "..")

    assert canary.exists(), "the stale-*.log sweep escaped zeek_root and deleted a file in its parent"


def test_fresh_zeek_out_dir_still_clears_stale_logs_for_an_ordinary_window_id(tmp_path):
    """The guard above must not cost the function its actual job (`zeek.py` rejects an out_dir that
    already holds `*.log`, so the sweep is what makes a retry possible at all)."""
    zeek_root = tmp_path / "zeek"
    stale_dir = zeek_root / "1700000000"
    stale_dir.mkdir(parents=True)
    (stale_dir / "conn.log").write_text("stale, from a previous failed attempt")
    (stale_dir / "keep.txt").write_text("not a *.log, must survive")

    out_dir = pipeline._fresh_zeek_out_dir(zeek_root, "1700000000")

    assert out_dir == stale_dir
    assert not (out_dir / "conn.log").exists()
    assert (out_dir / "keep.txt").exists()


@pytest.mark.parametrize("bad_id", ["", ".", "..", "/etc/passwd", "a/b", "..outside", ".hidden", "\x00null"])
def test_validate_window_id_rejects_the_full_set_directly(bad_id):
    """The two shapes no real filename stem can carry (a path separator, a NUL byte) are proven
    here, directly against the promoted `validate_window_id`, rather than through a `--from-pcaps`
    fixture a real filesystem cannot construct.
    """
    from aximprint.acquire.harvest.spool import SpoolError, validate_window_id

    with pytest.raises(SpoolError):
        validate_window_id(bad_id)


def test_plan_from_pcaps_ceiling_without_yes_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.windows, "DEFAULT_MAX_WINDOWS", 2)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"170000000{i}.pcap").write_bytes(pcap_body(b"x"))

    with pytest.raises(pipeline.FromPcapsSourceError, match="exceeding the default ceiling"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_ceiling_with_yes_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.windows, "DEFAULT_MAX_WINDOWS", 2)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"170000000{i}.pcap").write_bytes(pcap_body(b"x"))

    plan = pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src, yes=True))
    assert len(plan.windows) == 3


def test_plan_from_pcaps_ceiling_uses_the_shared_max_windows_for_helper(tmp_path, monkeypatch):
    """should-fix 11: `windows.max_windows_for` is the one formula behind the ceiling here, in
    `plan_harvest`, and in `cli.py`'s `cmd_pcap` — this pins that `plan_from_pcaps` actually calls
    through it rather than a re-derived local copy of the same arithmetic.
    """
    calls = []
    real = pipeline.windows.max_windows_for

    def _spy(yes):
        calls.append(yes)
        return real(yes)

    monkeypatch.setattr(pipeline.windows, "max_windows_for", _spy)
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src, yes=True))

    assert calls == [True]


# --- must-fix 4: reusing the three guards this package already has for a *.pcap that isn't a
# genuine, non-empty, real capture -------------------------------------------------------------


def test_plan_from_pcaps_rejects_a_directory_named_pcap(tmp_path):
    """`Spool._reconcile`'s own `is_file()` guard, reused — but as a rejection, not a silent skip
    (`source` is untrusted input, not already-known-good managed state).
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").mkdir()  # a DIRECTORY matching *.pcap, not a file

    with pytest.raises(pipeline.FromPcapsSourceError, match="non-file entry"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_rejects_a_zero_byte_file(tmp_path):
    """`runner.plan_resume`'s own zero-byte rule ("never a completed capture"), reused."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"")

    with pytest.raises(pipeline.FromPcapsSourceError, match="zero-byte"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_rejects_a_file_with_no_pcap_magic(tmp_path):
    """The real-world case this closes: this host has `*.pcap` files from the 2026-08-07
    concurrency measurement whose bodies are the appliance's own error text, repeated, with no
    signal other than the bytes themselves distinguishing them from a real capture —
    `extract.PCAP_MAGICS`'s own reasoning, reused (build step 4, the scope boundary's "one
    permitted inspection").
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"Error starting listen thread, shutting down thread" * 5)

    with pytest.raises(pipeline.FromPcapsSourceError, match="not a recognized pcap"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


def test_plan_from_pcaps_a_bad_magic_file_poisons_the_whole_batch_not_just_itself(tmp_path):
    """Same "reject the whole run, not just the offending file" discipline as the hostile-filename
    test above, applied to a content rejection instead of a naming one.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"valid"))
    (src / "1700000010.pcap").write_bytes(b"not a capture at all")

    with pytest.raises(pipeline.FromPcapsSourceError, match="not a recognized pcap"):
        pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))


# --- ruling C: accept any safe stem, but note when it is not epoch-shaped ----------------------


@pytest.mark.parametrize(
    ("window_id", "expected"),
    [
        ("1700000000", True),
        ("1700000000.000000000", True),
        ("1700000000.5", True),
        ("0", True),
        ("mycapture", False),
        ("my.capture", False),
        ("170000.abc", False),
        ("abc.170000", False),
        ("", False),
    ],
)
def test_looks_epoch_shaped(window_id, expected):
    assert pipeline._looks_epoch_shaped(window_id) is expected


def test_plan_from_pcaps_flags_non_epoch_shaped_window_ids_without_rejecting_them(tmp_path):
    """Ruling A of the work order's D3 table is not this — this is ruling C: a non-epoch-shaped
    stem is accepted (the scope boundary directs operators to cut pcaps externally, and those
    generally will not be epoch-named), only noted.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"epoch-shaped"))
    (src / "mycapture.pcap").write_bytes(pcap_body(b"cut-externally"))

    plan = pipeline.plan_from_pcaps(_from_pcaps_config(tmp_path, src))

    assert plan.non_epoch_shaped_window_ids == ("mycapture",)
    assert len(plan.windows) == 2  # accepted anyway


def test_run_harvest_from_pcaps_supplied_pcaps_still_exist_after_a_successful_run(tmp_path):
    """Mutation-proof test #1 (docs/WORK_ORDER_harvest_from_pcaps.md build step 5): if
    `_process_zeek_item`'s `item.owned` gate around `spool.release(..., delete=True)` were ever
    dropped, this pcap would be deleted — the exact 2026-08-11 near-miss (9.45 GB of supplied
    pcaps) this work order closes.
    """
    src = tmp_path / "src"
    src.mkdir()
    pcap = src / "1700000000.pcap"
    content = pcap_body(b"the-operators-own-bytes")
    pcap.write_bytes(content)

    config = _from_pcaps_config(tmp_path, src)
    plan = pipeline.plan_from_pcaps(config)
    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner())

    assert report.zeeked == 1
    # must-fix 1: `released` counts an ACTUAL spool.release() call, never inferred from a
    # successful Zeek run -- a --from-pcaps pcap is never admitted anywhere to release, so this
    # must be 0 even though the Zeek run itself succeeded.
    assert report.released == 0
    assert pcap.exists()
    assert pcap.read_bytes() == content
    assert not (config.out_dir / "spool").exists()  # no Spool is ever constructed in this mode


def test_run_harvest_from_pcaps_supplied_pcaps_still_exist_after_a_failed_zeek_run(tmp_path):
    """The failure-path sibling of the test above: a `--from-pcaps` pcap is untouched by Zeek
    failing on it too — there was never a `release()` call to gate in the first place for either
    outcome (see `_process_zeek_item`).
    """
    src = tmp_path / "src"
    src.mkdir()
    pcap = src / "1700000000.pcap"
    content = pcap_body(b"the-operators-own-bytes")
    pcap.write_bytes(content)

    config = _from_pcaps_config(tmp_path, src)
    plan = pipeline.plan_from_pcaps(config)
    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner(returncode=1))

    assert report.zeek_failed == 1
    assert report.released == 0
    assert pcap.exists()
    assert pcap.read_bytes() == content


def test_run_harvest_from_pcaps_report_has_no_extraction_or_spool_activity(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    config = _from_pcaps_config(tmp_path, src)
    plan = pipeline.plan_from_pcaps(config)
    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner())

    assert report.extracted == 0
    assert report.empty == 0
    assert report.extract_failed == 0
    assert report.reconciled_orphans == 0
    assert report.bytes_moved == 0
    assert report.peak_spool_bytes == 0
    assert report.retained_failed_bytes == 0
    assert report.failed_windows == ()
    assert report.released == 0  # nothing is ever released in this mode (must-fix 1)


def test_process_zeek_item_owned_false_never_calls_release_even_when_the_spool_has_a_real_entry(tmp_path):
    """The surgical version of the mutation-proof test: calls `_process_zeek_item` directly and
    synchronously (no worker thread), so a broken ownership guard fails as an ordinary assertion
    here rather than as a silent, swallowed exception inside a dead `_zeek_worker_loop` thread
    (which is what happens if the same mutation is exercised only through the full
    `run_harvest_from_pcaps` path with `spool=None` — a `None.release()` `AttributeError` inside a
    worker thread crashes that thread quietly, which happens to also leave the file alone, but for
    the wrong reason).

    Pre-admits a REAL, committed entry under the SAME `window_id` into a real, non-empty `Spool` —
    simulating the one scenario where an ownership bug would be catastrophic rather than merely
    inert (a `window_id` collision with something the spool genuinely tracks, exactly the
    Phase-5-orphan-beside-a-supplied-pcap shape D3 names). If `item.owned=False` were ever ignored
    and `spool.release(window_id, delete=True)` called unconditionally, this spool entry — and its
    own, DIFFERENT, real file — would be deleted. Asserting it survives untouched proves the skip
    is deliberate, not an accident of nothing having been admitted.
    """
    from aximprint.acquire.harvest.spool import SizeEstimator

    spool_root = tmp_path / "spool"
    spool = Spool(spool_root, cap_bytes=1000, estimator=SizeEstimator(seed_bytes=10))
    reservation = spool.admit("1700000000")
    reservation.path.write_bytes(b"a spool-owned pcap under the same window_id")
    spool.commit("1700000000")
    assert spool.state("1700000000") is not None  # genuinely tracked -- deletable if release() ran

    operator_pcap = tmp_path / "operator-src" / "1700000000.pcap"
    operator_pcap.parent.mkdir()
    operator_pcap.write_bytes(b"the-operators-own-bytes")

    zeek_root = tmp_path / "zeek-out"
    stats = pipeline._RunStats()
    item = pipeline._ZeekWorkItem(window_id="1700000000", pcap_path=operator_pcap, owned=False)

    pipeline._process_zeek_item(item, FakeZeekRunner(), spool, zeek_root, stats, None, None, threading.Event())

    assert stats.zeeked == 1  # the Zeek run itself still happened and is still counted
    assert operator_pcap.exists()
    assert operator_pcap.read_bytes() == b"the-operators-own-bytes"
    assert spool.state("1700000000") is not None  # untouched: release() was never called
    assert reservation.path.exists()


def test_run_harvest_from_pcaps_preflight_fails_on_zeek_probe_before_any_zeek_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    config = pipeline.FromPcapsConfig(source=src, out_dir=tmp_path / "out", zeek_workers=1)
    plan = pipeline.plan_from_pcaps(config)

    with pytest.raises(pipeline.ZeekProbeFailedError):
        pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=_ProbeFailingZeekRunner())

    assert not (tmp_path / "out" / "zeek").exists()


def test_run_harvest_from_pcaps_zeek_root_unwritable_is_a_preflight_error(tmp_path):
    """should-fix 9: `run_harvest` gets `_check_spool_root_writable`; before this fix,
    `run_harvest_from_pcaps` had only a bare `zeek_root.mkdir()`, so an unwritable `--out` reached
    the caller as a raw `OSError` (an "unexpected error", exit 4) rather than this typed,
    Phase-1, no-Zeek-run-attempted-yet diagnosis.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "zeek").write_bytes(b"not a directory")  # blocks the zeek root's own mkdir

    config = pipeline.FromPcapsConfig(source=src, out_dir=out_dir, zeek_workers=1)
    plan = pipeline.plan_from_pcaps(config)

    with pytest.raises(pipeline.ZeekRootNotWritableError):
        pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner())


def test_run_harvest_from_pcaps_multiple_pcaps_all_zeeked_and_all_survive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    pcaps = [src / f"170000000{i}.pcap" for i in range(4)]
    for i, p in enumerate(pcaps):
        p.write_bytes(pcap_body(f"pcap-{i}".encode()))

    config = _from_pcaps_config(tmp_path, src, zeek_workers=3)
    plan = pipeline.plan_from_pcaps(config)
    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner())

    assert report.planned == 4
    assert report.zeeked == 4
    assert report.released == 0  # never released in this mode -- must-fix 1
    assert all(p.exists() for p in pcaps)
    # `vantage.json` sits beside the per-window directories, not inside one — the cold-tier root
    # is what `ingest --source` is pointed at, so that is where the reader looks
    # (`docs/WORK_ORDER_visibility_and_vantage.md` build step 1). Asserted as part of the expected
    # listing rather than filtered out of it: a manifest that stopped being written should fail
    # this test, and a listing that silently ignores unexpected entries would not notice either way.
    assert sorted(p.name for p in (config.out_dir / "zeek").iterdir()) == [
        "1700000000",
        "1700000001",
        "1700000002",
        "1700000003",
        "vantage.json",
    ]


# --- ruling A + should-fix 5: --fail-fast and a SIGINT actually stop the run -------------------
#
# Both share `drain_on_stop` (`_zeek_worker_loop`): once `stop_requested` is set, a worker that
# dequeues a further item drains it (task_done(), never run through Zeek) instead of processing
# it. With `zeek_workers=1`, processing is strictly sequential and `on_event` fires synchronously
# from inside `_process_zeek_item` before that worker ever loops back to dequeue the next item —
# so these tests are deterministic, not timing-dependent.


def test_run_harvest_from_pcaps_fail_fast_stops_after_first_zeek_failure(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"one"))
    (src / "1700000010.pcap").write_bytes(pcap_body(b"two"))

    config = _from_pcaps_config(tmp_path, src, zeek_workers=1, fail_fast=True)
    plan = pipeline.plan_from_pcaps(config)

    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner(returncode=1))

    assert report.planned == 2
    assert report.zeek_failed == 1  # only the first window was actually run through Zeek
    assert report.zeeked == 0
    assert (config.out_dir / "zeek" / "1700000000").exists()  # ran, and failed
    assert not (config.out_dir / "zeek" / "1700000010").exists()  # drained without ever running


def test_run_harvest_from_pcaps_without_fail_fast_processes_every_window_despite_a_failure(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"one"))
    (src / "1700000010.pcap").write_bytes(pcap_body(b"two"))

    config = _from_pcaps_config(tmp_path, src, zeek_workers=1, fail_fast=False)
    plan = pipeline.plan_from_pcaps(config)

    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner(returncode=1))

    assert report.planned == 2
    assert report.zeek_failed == 2
    assert (config.out_dir / "zeek" / "1700000000").exists()
    assert (config.out_dir / "zeek" / "1700000010").exists()


def test_run_harvest_from_pcaps_sigint_drains_un_started_items(tmp_path, monkeypatch):
    """should-fix 5, end to end: a `KeyboardInterrupt` raised while `run_harvest_from_pcaps` is
    blocked in its own `for t in zeek_threads: t.join()` must not be a no-op. The `except
    KeyboardInterrupt` handler sets `stop_requested` and reuses the exact same `drain_on_stop`
    mechanism `--fail-fast` uses above, so every not-yet-started queued item is drained rather than
    run through Zeek. This matters in practice, not just in principle: the verb exists for large
    (hundreds-of-pcaps) `--from-pcaps` directories, where an operator hitting Ctrl-C mid-run is a
    real, expected event.

    Simulated deterministically by monkeypatching `threading.Thread.join`: it waits until the
    worker has genuinely entered `zeek_runner.run()` for the first item (`started_first_item`, so
    the injected interrupt cannot fire before the worker has even been scheduled once — a race that
    an earlier, unsynchronized version of this probe hit and that flipped the measured outcome to
    "everything drained, nothing completes" depending on incidental timing), raises
    `KeyboardInterrupt` exactly once, then delegates to the real `join()`. With that
    synchronization in place this is 100% reproducible (measured: 30/30 across three fresh
    interpreter processes before this was pinned as a test) — the item already in flight when the
    interrupt lands always finishes normally (there is no mid-item cancellation anywhere in this
    pipeline), and every item not yet dequeued is always drained.

    **Deliberately not a real `os.kill(os.getpid(), signal.SIGINT)`** — tried first, and abandoned.
    In this project's own sandboxed tool environment, sending one from a worker thread made the
    *calling* worker thread itself vanish with no catchable Python exception anywhere in its own
    call stack and no `threading.excepthook` firing at all — reproducible with plain stdlib
    `threading` code carrying zero aximprint imports, so it is an artifact of that sandbox's raw
    signal delivery (CPython's own documented behavior restricts a *registered* signal handler's
    execution to the main thread; this sandbox's actual observed behavior didn't match that), not a
    property of `run_harvest_from_pcaps`. An `os.kill`-based version of this test would be testing
    the sandbox, not the code — if you're tempted to "fix" this test by switching it to a real
    signal, don't; you'll trade a deterministic test for a mystery.

    `zeek_workers=1` makes the queue strictly sequential, so which item was "in flight" when the
    interrupt landed is unambiguous.
    """
    src = tmp_path / "src"
    src.mkdir()
    for name in ["1700000000", "1700000010", "1700000020"]:
        (src / f"{name}.pcap").write_bytes(pcap_body(b"x"))

    config = _from_pcaps_config(tmp_path, src, zeek_workers=1)
    plan = pipeline.plan_from_pcaps(config)

    started_first_item = threading.Event()
    real_runner = FakeZeekRunner()

    class _SignalingZeekRunner:
        def probe(self):
            return real_runner.probe()

        def run(self, pcap, out_dir):
            started_first_item.set()
            return real_runner.run(pcap, out_dir)

    real_join = threading.Thread.join
    interrupted_once = {"raised": False}

    def _join_raising_keyboard_interrupt_once(self, timeout=None):
        if not interrupted_once["raised"] and self.name.startswith("xtract-harvest-from-pcaps-zeek-"):
            interrupted_once["raised"] = True
            assert started_first_item.wait(timeout=5), "worker never started the first item"
            raise KeyboardInterrupt
        return real_join(self, timeout)

    monkeypatch.setattr(threading.Thread, "join", _join_raising_keyboard_interrupt_once)

    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=_SignalingZeekRunner())

    assert interrupted_once["raised"]  # the injected interrupt genuinely fired, not a vacuous pass
    assert report.interrupted is True
    assert report.zeeked == 1  # in flight when the interrupt landed: ran to completion, not aborted
    assert report.zeek_failed == 0
    assert (config.out_dir / "zeek" / "1700000000").exists()
    assert not (config.out_dir / "zeek" / "1700000010").exists()  # never started: drained
    assert not (config.out_dir / "zeek" / "1700000020").exists()  # never started: drained


def test_zeek_worker_loop_drains_without_processing_once_stop_requested_is_set(tmp_path):
    """Direct, synchronous proof of the `drain_on_stop` mechanism itself, independent of what sets
    `stop_requested` (fail-fast or SIGINT both just set the same event).
    """
    zeek_root = tmp_path / "zeek"
    stats = pipeline._RunStats()
    stop_requested = threading.Event()
    stop_requested.set()  # already stopped before the loop ever starts

    work_queue: queue.Queue = queue.Queue()
    pcap = tmp_path / "src.pcap"
    pcap.write_bytes(pcap_body(b"x"))
    work_queue.put(pipeline._ZeekWorkItem(window_id="1700000000", pcap_path=pcap, owned=False))
    work_queue.put(pipeline._ZEEK_SENTINEL)

    pipeline._zeek_worker_loop(
        work_queue, FakeZeekRunner(), None, zeek_root, stats, None, None, stop_requested, drain_on_stop=True
    )

    assert stats.zeeked == 0
    assert stats.zeek_failed == 0
    assert not (zeek_root / "1700000000").exists()  # never even reached _fresh_zeek_out_dir


def test_zeek_worker_loop_drain_on_stop_false_still_processes_everything(tmp_path):
    """`run_harvest`'s own Zeek queue must keep its EXISTING "always fully drain" behavior — the
    default (`drain_on_stop=False`) must not change anything for it, even with `stop_requested`
    already set (e.g. by a Phase-5 retained-failure trip mid-run).
    """
    zeek_root = tmp_path / "zeek"
    stats = pipeline._RunStats()
    stop_requested = threading.Event()
    stop_requested.set()

    work_queue: queue.Queue = queue.Queue()
    pcap = tmp_path / "src.pcap"
    pcap.write_bytes(pcap_body(b"x"))
    work_queue.put(pipeline._ZeekWorkItem(window_id="1700000000", pcap_path=pcap, owned=False))
    work_queue.put(pipeline._ZEEK_SENTINEL)

    pipeline._zeek_worker_loop(work_queue, FakeZeekRunner(), None, zeek_root, stats, None, None, stop_requested)

    assert stats.zeeked == 1
    assert (zeek_root / "1700000000").exists()


# --- must-fix 2: a Zeek worker thread that raises must not die silently ------------------------


def test_zeek_worker_loop_records_a_raised_exception_and_keeps_draining(tmp_path):
    class _RaisingZeekRunner:
        def probe(self):
            return ProbeResult(ok=True, podman_available=True, image_present=True, zeek_version="x", detail="ok")

        def run(self, pcap, out_dir):
            raise RuntimeError("boom")

    zeek_root = tmp_path / "zeek"
    stats = pipeline._RunStats()
    work_queue: queue.Queue = queue.Queue()
    item = pipeline._ZeekWorkItem(window_id="1700000000", pcap_path=tmp_path / "x.pcap", owned=False)
    work_queue.put(item)
    work_queue.put(pipeline._ZEEK_SENTINEL)

    # Must not raise here -- the whole point is that the WORKER THREAD survives a raised
    # exception; `run_harvest_from_pcaps`/`run_harvest` are what re-raise it later, once every
    # worker has drained.
    pipeline._zeek_worker_loop(work_queue, _RaisingZeekRunner(), None, zeek_root, stats, None, None, threading.Event())

    fatal = stats.worker_fatal_snapshot()
    assert isinstance(fatal, RuntimeError)
    assert str(fatal) == "boom"
    assert stats.zeeked == 0
    assert stats.zeek_failed == 0  # record_zeek_outcome was never reached


def test_zeek_worker_loop_keeps_processing_later_items_after_an_earlier_one_raises(tmp_path):
    """The "keeps draining" half: one bad item must not prevent a LATER, good item in the same
    queue from being processed.
    """
    calls: list[Path] = []

    class _RaisesOnceZeekRunner:
        def probe(self):
            return ProbeResult(ok=True, podman_available=True, image_present=True, zeek_version="x", detail="ok")

        def run(self, pcap, out_dir):
            calls.append(pcap)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return FakeZeekRunner().run(pcap, out_dir)

    zeek_root = tmp_path / "zeek"
    stats = pipeline._RunStats()
    work_queue: queue.Queue = queue.Queue()
    bad_pcap = tmp_path / "bad.pcap"
    good_pcap = tmp_path / "good.pcap"
    good_pcap.write_bytes(pcap_body(b"good"))
    work_queue.put(pipeline._ZeekWorkItem(window_id="1700000000", pcap_path=bad_pcap, owned=False))
    work_queue.put(pipeline._ZeekWorkItem(window_id="1700000010", pcap_path=good_pcap, owned=False))
    work_queue.put(pipeline._ZEEK_SENTINEL)

    pipeline._zeek_worker_loop(
        work_queue, _RaisesOnceZeekRunner(), None, zeek_root, stats, None, None, threading.Event()
    )

    assert isinstance(stats.worker_fatal_snapshot(), RuntimeError)
    assert stats.zeeked == 1  # the second (good) item was still processed
    assert (zeek_root / "1700000010").exists()


def test_run_harvest_from_pcaps_raises_instead_of_silently_dropping_a_window_on_a_worker_crash(tmp_path):
    """The end-to-end shape of must-fix 2: a worker crash must surface as a raised exception from
    `run_harvest_from_pcaps` itself, never a silently-short report with exit 0. Constructs a
    `FromPcapsPlan` directly (bypassing `plan_from_pcaps`'s own upfront `window_id` validation,
    which would already reject this id) to reach a real, independently-reachable trigger for a
    worker crash: `_fresh_zeek_out_dir`'s path-escape assertion — proving the *catching*, not just
    the one call site the assertion happens to guard today.
    """
    src = tmp_path / "src"
    src.mkdir()
    good_pcap = src / "1700000000.pcap"
    good_pcap.write_bytes(pcap_body(b"good"))
    bad_pcap = tmp_path / "bad.pcap"
    bad_pcap.write_bytes(pcap_body(b"bad"))

    config = _from_pcaps_config(tmp_path, src, zeek_workers=1)
    plan = pipeline.FromPcapsPlan(windows=(("1700000000", good_pcap), ("..", bad_pcap)))

    with pytest.raises(AssertionError, match="escaped zeek root"):
        pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=FakeZeekRunner())

    # The good window was still processed before the exception surfaced from the crashed one.
    assert (config.out_dir / "zeek" / "1700000000" / "conn.log").exists()


# --- must-fix 2: the from-pcaps completeness check (check_from_pcaps_accounting) ---------------


def _report(**overrides) -> pipeline.RunReport:
    kwargs = {
        "planned": 0, "extracted": 0, "empty": 0, "extract_failed": 0, "zeeked": 0, "zeek_failed": 0,
        "released": 0, "reconciled_orphans": 0, "bytes_moved": 0, "elapsed_s": 0.0, "peak_spool_bytes": 0,
        "unrecognized_stems": (), "interrupted": False, "failed_windows": (), "zeek_failed_windows": (),
        "retained_failed_bytes": 0,
    }
    kwargs.update(overrides)
    return pipeline.RunReport(**kwargs)


def test_check_from_pcaps_accounting_passes_when_counts_reconcile():
    report = _report(planned=2, zeeked=1, zeek_failed=1)
    pipeline.check_from_pcaps_accounting(report, stopped_early=False)  # must not raise


def test_check_from_pcaps_accounting_raises_on_a_deficit():
    report = _report(planned=3, zeeked=1, zeek_failed=1)
    with pytest.raises(pipeline.FromPcapsAccountingError, match="planned=3"):
        pipeline.check_from_pcaps_accounting(report, stopped_early=False)


def test_check_from_pcaps_accounting_tolerates_a_deficit_when_stopped_early():
    """A SIGINT or --fail-fast tripping both legitimately leave a deficit via `drain_on_stop` —
    `stopped_early=True` must not be flagged as a mystery.
    """
    report = _report(planned=3, zeeked=1, zeek_failed=0)
    pipeline.check_from_pcaps_accounting(report, stopped_early=True)  # must not raise


def test_check_from_pcaps_accounting_ignores_interrupted_flag_directly(tmp_path):
    """`check_from_pcaps_accounting` takes `stopped_early` as an explicit argument rather than
    reading `report.interrupted` itself — an interrupted report with `stopped_early=False` passed
    in (a caller bug) is still flagged, which is the point: the two are related but not the same
    signal (see the function's own docstring).
    """
    report = _report(planned=3, zeeked=1, zeek_failed=0, interrupted=True)
    with pytest.raises(pipeline.FromPcapsAccountingError):
        pipeline.check_from_pcaps_accounting(report, stopped_early=False)


# --- must-fix 1: `released` is measured at the real release, never inferred from a Zeek success -


def test_record_zeek_outcome_success_does_not_increment_released():
    stats = pipeline._RunStats()
    stats.record_zeek_outcome("w1", _zeek_result(ok=True))
    assert stats.zeeked == 1
    assert stats.released == 0


def test_record_released_increments_independently_of_zeek_outcome():
    stats = pipeline._RunStats()
    stats.record_released()
    stats.record_released()
    assert stats.released == 2
    assert stats.zeeked == 0


def test_normal_run_still_deletes_extracted_pcaps_after_zeek_success(tmp_path):
    """Mutation-proof test #2 (docs/WORK_ORDER_harvest_from_pcaps.md build step 5): the ownership
    gate `_process_zeek_item` grew for `--from-pcaps` must not weaken the pre-existing
    delete-only-after-success behavior for a pcap this pipeline itself extracted (`_extract_one`
    constructs its `_ZeekWorkItem` with `owned=True`, unchanged for every existing producer —
    should-fix 6 made `owned` a required field with no default, so a future call site that forgets
    to specify it fails construction with `TypeError` rather than silently inheriting a
    fail-open `True`). Pipeline-level companion to `tests/acquire/test_cli_harvest.py`'s CLI-level
    version of the same property; `test_successful_zeek_run_releases_and_deletes` above already
    covers this shape too — kept here as an explicit, separately-named regression test per the
    work order's "both, or the fix is half a fix."
    """
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})
    cfg = _config(tmp_path, end_expr="1700000010", extract_concurrency=1, zeek_workers=1)
    plan = pipeline.plan_harvest(cfg, client=None, now=_now())

    report = pipeline.run_harvest(cfg, plan=plan, client=client, zeek_runner=FakeZeekRunner())

    assert report.zeeked == 1
    assert report.released == 1
    assert not (cfg.out_dir / "spool" / "1700000000.pcap").exists()
