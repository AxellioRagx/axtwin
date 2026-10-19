"""Tests for `xtract harvest` (aximprint.acquire.cli).

No test here opens a network socket or invokes `podman`: `_FakeHarvestClient` (below, the same
request-content-keyed shape as `tests/acquire/test_pipeline.py`'s, trimmed to what the CLI-level tests
need) stands in for the real transport via `client_factory`, and `zeek_runner_factory` (the same
injection seam shape, `main()`'s own additional parameter for this command) always returns a
`FakeZeekRunner`. `tmp_path` stands in for `--out`.
"""

from __future__ import annotations

import threading

import pytest

from aximprint.acquire import cli
from aximprint.acquire.errors import AuthenticationError, ServerError
from aximprint.acquire.harvest.zeek import FakeZeekRunner
from aximprint.acquire.timeutil import parse_duration
from tests.acquire.conftest import pcap_body

BASE_ARGV = ["--host", "https://corpapcn.example.com"]


# --- fakes -----------------------------------------------------------------------


class _PcapResponse:
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


def _written(content: bytes = b"pcap-bytes") -> _PcapResponse:
    # `pcap_body()`-wrapped: `extract_window` now rejects a 200 body that doesn't start with a
    # real pcap magic — see `tests/conftest.py`'s `pcap_body` docstring.
    return _PcapResponse(content=pcap_body(content), headers={"X-Task-GUID": "guid-1"})


class _FakeHarvestClient:
    """Trimmed to what `cmd_harvest`'s own tests need: `GET /axapi/fs/streams` (preflight's
    credentials probe — always answers success unless `streams_error` is set) and
    `GET /axapi/pcap` (extraction, scripted per window `start_time`). Thread-safe: pipeline.py
    issues concurrent requests.
    """

    #: `Client.host` — part of the contract this fake stands in for since the vantage manifest
    #: landed (`docs/WORK_ORDER_visibility_and_vantage.md` build step 1): `run_harvest` records
    #: which appliance its pcaps came from, so a fake with no host is an incomplete fake, not a
    #: reason for the pipeline to accept a missing one.
    host = "https://fake-appliance.test"

    def __init__(self, *, pcap_by_start=None, streams_error: BaseException | None = None):
        self._pcap_by_start = pcap_by_start or {}
        self._streams_error = streams_error
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, *, params=None, json=None, stream=False, expected_status=200):
        with self._lock:
            self.calls.append((method, path, dict(params) if params else None))
        if method == "DELETE":
            return _JsonResponse({})
        if path == "/axapi/fs/streams":
            if self._streams_error is not None:
                raise self._streams_error
            return _JsonResponse([])
        if path == "/axapi/pcap":
            item = self._pcap_by_start[params["start_time"]]
            if isinstance(item, BaseException):
                raise item
            return item
        raise AssertionError(f"unscripted request: {method} {path} {params}")  # pragma: no cover

    def pcap_calls(self) -> list[dict]:
        with self._lock:
            return [c[2] for c in self.calls if c[1] == "/axapi/pcap"]


class _NullContext:
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, *exc_info):
        return False


def _zeek_factory(**kwargs):
    return lambda args: FakeZeekRunner(**kwargs)


_ARGV_BASE = [
    "harvest",
    "--streams",
    "2-4",
    "--start",
    "1700000000",
    "--end",
    "1700000010",
    "--interval",
    "10s",
]


# --- dry run -----------------------------------------------------------------------


def test_dry_run_with_explicit_end_makes_no_client_and_no_pcap_calls(tmp_path, capsys):
    def _factory_should_not_be_called(args):
        raise AssertionError("client_factory must not be called for --dry-run with an explicit --end")

    def _zeek_factory_should_not_be_called(args):
        raise AssertionError("zeek_runner_factory must not be called under --dry-run")

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--dry-run"],
        client_factory=_factory_should_not_be_called,
        zeek_runner_factory=_zeek_factory_should_not_be_called,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "windows:      1" in out
    assert not (tmp_path / "spool").exists()
    assert not (tmp_path / "zeek").exists()


def test_dry_run_issues_zero_pcap_calls_even_with_a_client(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written(b"should-never-be-sent")})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--dry-run"],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    assert client.pcap_calls() == []


def test_dry_run_without_explicit_end_still_resolves_end_via_the_catalog(tmp_path, capsys):
    class _EndResolvingClient(_FakeHarvestClient):
        def request(self, method, path, *, params=None, json=None, stream=False, expected_status=200):
            if path == "/axapi/fs/files":
                stream_id = params["stream"]
                last = {"2": 1_700_000_030, "3": 1_700_000_020, "4": 1_700_000_010}.get(stream_id)
                files = [] if last is None else [
                    {"name": "a.pcap", "size": 1, "time_start": str(last), "time_end": str(last), "epoch": 1, "seqnum": 1}
                ]
                return _JsonResponse({"files": files})
            return super().request(method, path, params=params, json=json, stream=stream, expected_status=expected_status)

    client = _EndResolvingClient()
    code = cli.main(
        [*BASE_ARGV, "harvest", "--streams", "2-4", "--start", "1700000000", "--interval", "10s", "--out", str(tmp_path), "--dry-run"],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "windows:      3" in out


# --- flags map onto HarvestConfig -----------------------------------------------------


def test_flags_map_onto_harvest_config(tmp_path, monkeypatch):
    captured: dict = {}
    real_plan_harvest = cli.pipeline.plan_harvest

    def _spy(config, *, client, now):
        captured["config"] = config
        return real_plan_harvest(config, client=client, now=now)

    monkeypatch.setattr(cli.pipeline, "plan_harvest", _spy)

    code = cli.main(
        [
            *BASE_ARGV,
            "harvest",
            "--streams",
            "2-4",
            "--start",
            "1700000000",
            "--end",
            "1700000030",
            "--interval",
            "10s",
            "--out",
            str(tmp_path / "out"),
            "--spool-cap",
            "50GB",
            "--concurrency",
            "3",
            "--zeek-workers",
            "5",
            "--filter",
            "tcp",
            "--fail-fast",
            "--yes",
            "--dry-run",
        ],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("no client needed")),
        zeek_runner_factory=lambda args: (_ for _ in ()).throw(AssertionError("no zeek runner needed under --dry-run")),
    )

    assert code == 0
    config = captured["config"]
    assert config.streams == "2-4"
    assert config.start_expr == "1700000000"
    assert config.end_expr == "1700000030"
    assert config.interval == parse_duration("10s")
    assert config.out_dir == tmp_path / "out"
    assert config.spool_cap_bytes == 50_000_000_000
    assert config.extract_concurrency == 3
    assert config.zeek_workers == 5
    assert config.filter == "tcp"
    assert config.fail_fast is True
    assert config.yes is True


# --- a full (non-dry-run) run ------------------------------------------------------------


def test_full_run_extracts_and_zeeks_and_exits_zero(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    assert not (tmp_path / "spool" / "1700000000.pcap").exists()  # released after Zeek succeeded
    assert (tmp_path / "zeek" / "1700000000" / "conn.log").exists()


def test_mixed_run_with_a_failure_exits_one(tmp_path, capsys):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": ServerError("boom", status_code=500)})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 1
    out = capsys.readouterr().out
    assert "extract_failed=1" in out


def test_run_with_a_failed_zeek_pass_exits_one_and_retains_the_pcap(tmp_path):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(returncode=1),
    )

    assert code == 1
    assert (tmp_path / "spool" / "1700000000.pcap").exists()  # never released


# --- usage errors: clear message, exit 2, never a traceback -----------------------------


def test_malformed_streams_spec_is_a_usage_error(tmp_path, capsys):
    # `--streams`/`--interval` are validated inline in `cmd_harvest` *after* the client is already
    # constructed (same ordering `cmd_pcap` already uses) — a working factory is supplied, same as
    # `tests/acquire/test_cli_pcap.py`'s equivalent test, not one asserting it's never called.
    client = _FakeHarvestClient()
    code = cli.main(
        [*BASE_ARGV, "harvest", "--streams", "not-a-spec", "--start", "1700000000", "--end", "1700000010", "--interval", "10s", "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
    )

    assert code == 2
    assert "not-a-spec" in capsys.readouterr().err


def test_malformed_interval_is_a_usage_error(tmp_path, capsys):
    client = _FakeHarvestClient()
    code = cli.main(
        [*BASE_ARGV, "harvest", "--streams", "0", "--start", "1700000000", "--end", "1700000010", "--interval", "not-a-duration", "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
    )

    assert code == 2


def test_malformed_spool_cap_is_a_usage_error(tmp_path, capsys):
    code = cli.main([*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--spool-cap", "not-a-size", "--dry-run"])

    assert code == 2
    assert "not-a-size" in capsys.readouterr().err


def test_nonpositive_concurrency_is_a_usage_error(tmp_path, capsys):
    code = cli.main([*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--concurrency", "0", "--dry-run"])

    assert code == 2


def test_nonpositive_zeek_workers_is_a_usage_error(tmp_path, capsys):
    code = cli.main([*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--zeek-workers", "-1", "--dry-run"])

    assert code == 2


def test_unpinned_zeek_image_is_a_usage_error(tmp_path, capsys):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path), "--zeek-image", "docker.io/zeek/zeek:latest"],
        client_factory=lambda args: _NullContext(client),
    )

    assert code == 2


def test_missing_required_flags_is_a_usage_error(capsys):
    code = cli.main([*BASE_ARGV, "harvest"])

    assert code == 2


# --- auth failure: exit 3, via the same typed-error routing every other command uses -----------


def test_bad_credentials_at_preflight_exits_three(tmp_path, capsys):
    client = _FakeHarvestClient(streams_error=AuthenticationError("bad credentials"))

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 3


# --- SIGINT: resumable, exit 130 --------------------------------------------------------------


def test_sigint_mid_run_exits_130(tmp_path, capsys):
    client = _FakeHarvestClient(pcap_by_start={"1700000000": KeyboardInterrupt()})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 130
    err = capsys.readouterr().err
    assert "interrupted" in err.lower()


def test_missing_required_flags_never_touches_a_client_or_a_zeek_runner(capsys):
    """Regression companion to `test_missing_required_flags_is_a_usage_error`: proves the ordering
    fix in `_dispatch_harvest` (docs/WORK_ORDER_harvest_from_pcaps.md build step 1 made
    `--streams`/`--start`/`--interval` conditionally required, which could have let a missing-flags
    run fall through to real client construction before `cmd_harvest`'s own check ran). No
    `client_factory`/`zeek_runner_factory` override could ever be reached without raising.
    """
    code = cli.main(
        [*BASE_ARGV, "harvest"],
        client_factory=lambda args: (_ for _ in ()).throw(AssertionError("client_factory must not be called")),
        zeek_runner_factory=lambda args: (_ for _ in ()).throw(AssertionError("zeek_runner_factory must not be called")),
    )

    assert code == 2
    assert "--streams" in capsys.readouterr().err


# --- `harvest --from-pcaps` (docs/WORK_ORDER_harvest_from_pcaps.md) -----------------------------
#
# No test below supplies `--host`, and every `client_factory` raises if called — from-pcaps mode
# touches no network and needs no credentials at any point (D4: `--streams` is rejected, so there
# is no stream id anywhere below `acquire` for a request to carry — see the work order's own
# verification note).


def _no_client_factory(args):
    raise AssertionError("client_factory must not be called for --from-pcaps")


def _asserting_zeek_factory(args):
    raise AssertionError("zeek_runner_factory must not be called")


def test_from_pcaps_runs_zeek_only_with_no_client_and_leaves_pcaps_in_place(tmp_path, capsys):
    """The work order's own acceptance shape: supplied pcaps survive a successful run. Mutation-
    proof test #1 (build step 5) — if the `item.owned` gate in `_process_zeek_item` were ever
    dropped or inverted, this pcap would be deleted.
    """
    src = tmp_path / "src"
    src.mkdir()
    pcap = src / "1700000000.pcap"
    content = pcap_body(b"the-operators-own-bytes")
    pcap.write_bytes(content)
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out)],
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    assert pcap.exists()
    assert pcap.read_bytes() == content
    assert (out / "zeek" / "1700000000" / "conn.log").exists()
    assert not (out / "spool").exists()
    out_text = capsys.readouterr().out
    assert "released=0" in out_text  # must-fix 1: nothing was ever released in this mode
    assert "zeek ok — source pcap retained" in out_text  # the distinct event, not "released"


def test_from_pcaps_leaves_pcaps_in_place_even_when_zeek_fails(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    pcap = src / "1700000000.pcap"
    content = pcap_body(b"the-operators-own-bytes")
    pcap.write_bytes(content)
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out)],
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(returncode=1),
    )

    assert code == 1
    assert pcap.exists()
    assert pcap.read_bytes() == content
    assert "zeek_failed=1" in capsys.readouterr().out


def test_normal_harvest_still_deletes_downloaded_pcaps_after_success(tmp_path):
    """Mutation-proof test #2 (build step 5) — the sibling of the test above, at the CLI level:
    proves the ownership check `--from-pcaps` added did not weaken the pre-existing behavior for
    an ordinary (appliance-extracted) run, where `_extract_one` still constructs its
    `_ZeekWorkItem` with `owned=True` (a required field since should-fix 6, not a default).
    """
    client = _FakeHarvestClient(pcap_by_start={"1700000000": _written()})

    code = cli.main(
        [*BASE_ARGV, *_ARGV_BASE, "--out", str(tmp_path)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    assert not (tmp_path / "spool" / "1700000000.pcap").exists()


def test_streams_start_interval_are_not_required_when_from_pcaps_is_given(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--streams", "2-4"),
        ("--start", "1700000000"),
        ("--end", "1700000010"),
        ("--interval", "10s"),
        ("--filter", "tcp"),
        ("--concurrency", "2"),
        ("--spool-cap", "50GB"),
    ],
)
def test_from_pcaps_rejects_each_d4_flag_individually_by_name(tmp_path, capsys, flag, value):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"x")

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), flag, value],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    err = capsys.readouterr().err
    assert flag in err
    assert "--from-pcaps is incompatible" in err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--host", "https://appliance.example.com"),
        ("--profile", "default"),
        ("--api-key-file", "/tmp/key"),
        ("--ca-bundle", "/tmp/ca.pem"),
    ],
)
def test_from_pcaps_rejects_each_ruling_b_credential_flag_individually(tmp_path, capsys, flag, value):
    """Ruling B: five credential/transport flags extend D4 to twelve — `--from-pcaps` never
    builds a client at all, so none of these has a receiver either. These are TOP-LEVEL parser
    flags (unlike the seven ratified ones, which belong to the `harvest` subparser), so they must
    precede the subcommand name on the command line — argparse itself, not this check, is what
    enforces that ordering.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    code = cli.main(
        [flag, value, "harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    err = capsys.readouterr().err
    assert flag in err
    assert "--from-pcaps is incompatible" in err


def test_from_pcaps_rejects_insecure(tmp_path, capsys):
    """`--insecure` is the twelfth D4-rejected flag, and the one `store_true` flag among them: no
    value argument, so its "supplied" test is plain truthiness, not the `is not None` sentinel
    trick the value flags need.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    code = cli.main(
        ["--insecure", "harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "--insecure" in err
    assert "--from-pcaps is incompatible" in err


def test_from_pcaps_accepts_an_empty_filter_since_it_is_a_no_op_anyway(tmp_path):
    """should-fix 8: `--filter ''` is already a no-op WITHOUT `--from-pcaps` (`args.filter or
    None` in `cmd_harvest`'s ordinary branch) — the D4 conflict check must treat it exactly the
    same way, or an empty `--filter` becomes a hard error only with `--from-pcaps` and a silent
    no-op without it, for the identical input.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), "--filter", ""],
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0


def test_from_pcaps_empty_source_string_is_rejected(tmp_path, capsys):
    """Must-fix 3, at the CLI level: `--from-pcaps ""` must fail loud, never silently resolve to
    the current directory.
    """
    code = cli.main(
        ["harvest", "--from-pcaps", "", "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "must not be empty" in capsys.readouterr().err


def test_from_pcaps_fail_fast_stops_after_first_zeek_failure(tmp_path, capsys):
    """Ruling A, at the CLI level: `--fail-fast` genuinely stops a `--from-pcaps` run."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"one"))
    (src / "1700000010.pcap").write_bytes(pcap_body(b"two"))
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--zeek-workers", "1", "--fail-fast"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(returncode=1),
    )

    assert code == 1
    out_text = capsys.readouterr().out
    assert "planned=2" in out_text
    assert "zeek_failed=1" in out_text
    assert (out / "zeek" / "1700000000").exists()
    assert not (out / "zeek" / "1700000010").exists()


def test_from_pcaps_names_every_offending_flag_when_several_are_given(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"x")

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), "--streams", "2-4", "--interval", "10s"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "--streams" in err
    assert "--interval" in err
    assert "--start" not in err  # only the flags actually supplied are named


def test_from_pcaps_rejects_concurrency_even_when_it_equals_the_ordinary_default(tmp_path, capsys):
    """The sentinel-`None`-default design's own correctness property: an explicitly-typed value
    that happens to match `pipeline.DEFAULT_EXTRACT_CONCURRENCY` must still be treated as
    "supplied", never silently accepted because it looks like "unset".
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"x")
    assert str(cli.pipeline.DEFAULT_EXTRACT_CONCURRENCY) == "1"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), "--concurrency", "1"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "--concurrency" in capsys.readouterr().err


def test_from_pcaps_empty_source_is_a_positive_gate_failure(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "no *.pcap files" in capsys.readouterr().err


def test_from_pcaps_nonexistent_source_is_rejected(tmp_path, capsys):
    code = cli.main(
        ["harvest", "--from-pcaps", str(tmp_path / "does-not-exist"), "--out", str(tmp_path / "out")],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "not a directory" in capsys.readouterr().err


def test_from_pcaps_hostile_filename_is_rejected_before_any_zeek_run(tmp_path, capsys):
    """The hostile-filename test build step 5 calls for: `spool.validate_window_id`, pointed at a
    user-controlled directory for the first time, must reject a bad stem before Zeek — or anything
    else — ever touches it. `zeek_runner_factory` raises if reached, proving it never is.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "..pcap").write_bytes(b"hostile")  # stem "." -- rejected by validate_window_id
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out)],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "not a safe window id" in capsys.readouterr().err
    assert not out.exists()


def test_from_pcaps_dry_run_makes_no_zeek_call(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--dry-run"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 0
    out_text = capsys.readouterr().out
    assert "windows:      1" in out_text
    assert not (out / "zeek").exists()


def test_from_pcaps_dry_run_prints_the_non_epoch_shaped_note(tmp_path, capsys):
    """Ruling C + should-fix 12: the note prints for `--dry-run` too, not only a real run."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "mycapture.pcap").write_bytes(pcap_body(b"cut-externally"))
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--dry-run"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 0
    err = capsys.readouterr().err
    assert "not epoch-shaped" in err
    assert "mycapture" in err


def test_from_pcaps_prints_the_non_epoch_shaped_note_on_a_real_run_too(tmp_path, capsys):
    """The other half of should-fix 12, pinned separately from the `--dry-run` case above:
    `_cmd_harvest_from_pcaps` calls `_print_non_epoch_shaped_note` unconditionally, before the `if
    args.dry_run:` branch, so a REAL run must print the same note the plan already carried — the
    note describing the plan but going missing on the run it actually describes is exactly the
    kind of thing that rots silently if only the `--dry-run` half is ever pinned.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "mycapture.pcap").write_bytes(pcap_body(b"cut-externally"))
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out)],  # no --dry-run: a real run
        client_factory=_no_client_factory,
        zeek_runner_factory=_zeek_factory(),
    )

    assert code == 0
    err = capsys.readouterr().err
    assert "not epoch-shaped" in err
    assert "mycapture" in err
    assert (out / "zeek" / "mycapture" / "conn.log").exists()  # the run itself genuinely happened


def test_from_pcaps_epoch_shaped_names_print_no_note(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(pcap_body(b"x"))
    out = tmp_path / "out"

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--dry-run"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 0
    assert "epoch-shaped" not in capsys.readouterr().err


def test_from_pcaps_yes_permits_a_source_over_the_window_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.pipeline.windows, "DEFAULT_MAX_WINDOWS", 2)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"170000000{i}.pcap").write_bytes(pcap_body(b"x"))
    out = tmp_path / "out"

    without_yes = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--dry-run"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )
    assert without_yes == 2

    with_yes = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(out), "--dry-run", "--yes"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )
    assert with_yes == 0


def test_from_pcaps_nonpositive_zeek_workers_is_a_usage_error(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"x")

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), "--zeek-workers", "0"],
        client_factory=_no_client_factory,
        zeek_runner_factory=_asserting_zeek_factory,
    )

    assert code == 2
    assert "--zeek-workers" in capsys.readouterr().err


def test_from_pcaps_unpinned_zeek_image_is_a_usage_error(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "1700000000.pcap").write_bytes(b"x")

    code = cli.main(
        ["harvest", "--from-pcaps", str(src), "--out", str(tmp_path / "out"), "--zeek-image", "docker.io/zeek/zeek:latest"],
        client_factory=_no_client_factory,
    )

    assert code == 2
