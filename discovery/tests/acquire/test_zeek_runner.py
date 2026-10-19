"""Tests for aximprint.acquire.harvest.zeek.

No test here invokes a real `podman`, pulls an image, or opens a real socket (the socket half is
enforced, for every test under `tests/acquire/`, by `tests/acquire/conftest.py`'s autouse
`_deny_real_sockets` fixture). Every `PodmanZeekRunner` test replaces
`aximprint.acquire.harvest.zeek.subprocess.run` with a scripted stand-in that inspects the argv it was called
with and returns/raises a canned result — the real container runtime is never touched.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from aximprint.acquire.harvest import zeek

# The live reference capture (not committed — see `tests/acquire/data/reference_conn_header.log` for the
# hermetic, checked-in fixture derived from it). Only present on the host this was captured on;
# tests that use it directly are `skipif`-guarded so the suite stays hermetic everywhere else.
REFERENCE_CONN_LOG = Path("/home/rocky/aximprint/metadata/zeek/conn.log")
REFERENCE_CONN_HEADER = Path(__file__).parent / "data" / "reference_conn_header.log"

# A stem guaranteed never to be a real Zeek log type, used everywhere below a test needs "some
# stem outside KNOWN_ZEEK_STEMS". `x509` served this role until `docs/WORK_ORDER_zeek_stem_coverage.md`
# D2 recommended promoting it to parsed -- at which point every test hardcoding it as the
# canonical "unrecognized" example would break with no hint why. This name can never be promoted
# out from under these tests the same way.
_UNKNOWN_STEM = "__not_a_zeek_stem__"


def _header_tags(path: Path) -> list[str]:
    """The ordered `#tag` tokens of a Zeek log's header block (everything before the first
    non-`#`-prefixed line).
    """
    tags = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        tags.append(line.split()[0].split("\t")[0])
    return tags


def _make_pcap(tmp_path: Path, size: int = 128) -> Path:
    pcap = tmp_path / "in.pcap"
    pcap.write_bytes(b"\x00" * size)
    return pcap


# --------------------------------------------------------------------------------------------
# Phase 1 — the protocol, ZeekResult, and FakeZeekRunner
# --------------------------------------------------------------------------------------------


def test_fake_runner_conn_log_header_matches_reference_shape(tmp_path):
    """`FakeZeekRunner`'s `conn.log` must carry the same header tag sequence, in the same order,
    as a real captured Zeek `conn.log` — downstream work orders parse on that shape. Checked
    against `tests/acquire/data/reference_conn_header.log`, the checked-in 8-line header this was
    captured from — not the live host path, which only exists on the one host it was captured on
    (finding 25: a hard-coded `/home/rocky/aximprint/...` path breaks the hermetic suite anywhere
    else, exactly the host-dependence H-8 packaging is meant to eliminate).
    """
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"

    result = zeek.FakeZeekRunner().run(pcap, out_dir)

    assert _header_tags(result.logs["conn"]) == _header_tags(REFERENCE_CONN_HEADER)


@pytest.mark.skipif(
    not REFERENCE_CONN_LOG.exists(), reason="live aximprint reference capture not present on this host"
)
def test_fake_runner_conn_log_header_matches_the_live_reference_capture(tmp_path):
    """Bonus cross-check, live-host-only: confirms `tests/acquire/data/reference_conn_header.log` (the
    fixture the hermetic test above actually asserts against) hasn't drifted from the full live
    capture it was extracted from.
    """
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"

    result = zeek.FakeZeekRunner().run(pcap, out_dir)

    assert _header_tags(result.logs["conn"]) == _header_tags(REFERENCE_CONN_LOG)


def test_fake_runner_conn_log_is_tab_separated_and_parses(tmp_path):
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner(rows_per_log=2).run(pcap, tmp_path / "out")

    lines = result.logs["conn"].read_text(encoding="utf-8").splitlines()
    fields_line = next(line for line in lines if line.startswith("#fields"))
    types_line = next(line for line in lines if line.startswith("#types"))
    fields = fields_line.split("\t")[1:]
    types = types_line.split("\t")[1:]
    assert len(fields) == len(types)

    data_lines = [line for line in lines if not line.startswith("#")]
    assert len(data_lines) == 2
    for row in data_lines:
        assert len(row.split("\t")) == len(fields)


def test_fake_runner_separator_line_is_literal_not_a_real_tab(tmp_path):
    """Zeek's own `#separator` line writes its value as the literal text `\\x09`, not an actual
    tab byte (confirmed against the reference log with `od -c`) — every other header line is a
    real tab. Getting this backwards breaks any parser that splits header lines on a literal tab.
    """
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner().run(pcap, tmp_path / "out")
    first_line = result.logs["conn"].read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#separator \\x09"
    assert "\t" not in first_line


def test_zeek_result_carries_every_field(tmp_path):
    pcap = _make_pcap(tmp_path, size=256)
    result = zeek.FakeZeekRunner(version="5.1.0-fake").run(pcap, tmp_path / "out")

    assert result.version == "5.1.0-fake"
    assert set(result.logs) == {"conn"}
    assert result.duration_s >= 0.0
    assert result.returncode == 0
    assert result.stderr_tail == ""
    assert result.bytes_in == 256
    assert result.unrecognized == ()
    assert result.ok is True


def test_zeek_result_ok_false_on_nonzero_returncode():
    result = zeek.ZeekResult(
        version="5.0.0", logs={}, duration_s=0.1, returncode=1, stderr_tail="boom", bytes_in=0
    )
    assert result.ok is False


def test_fake_runner_satisfies_zeek_runner_protocol():
    assert isinstance(zeek.FakeZeekRunner(), zeek.ZeekRunner)


def test_fake_runner_extra_stems_are_written(tmp_path):
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner(extra_stems=["dns", "ssl"]).run(pcap, tmp_path / "out")
    assert set(result.logs) == {"conn", "dns", "ssl"}


def test_fake_runner_probe_is_always_ok():
    probe = zeek.FakeZeekRunner(version="5.9.9-fake").probe()
    assert probe.ok is True
    assert probe.podman_available is True
    assert probe.image_present is True
    assert probe.zeek_version == "5.9.9-fake"


# --------------------------------------------------------------------------------------------
# Phase 2 — the podman runner
# --------------------------------------------------------------------------------------------


def _fixed_name(name: str = "xtract-zeek-test"):
    return lambda: name


class ScriptedSubprocess:
    """A `subprocess.run` stand-in for `PodmanZeekRunner` tests.

    `version_result`/`main_result` are `subprocess.CompletedProcess`-shaped responses (or an
    exception instance/class to raise) returned for the `zeek --version` call and the main
    `zeek -r` run call, respectively, matched by argv shape. Every call is recorded in `.calls` for
    exact-argv assertions.
    """

    def __init__(self, *, version_result=None, main_result=None, kill_result=None):
        self.version_result = version_result
        self.main_result = main_result
        self.kill_result = kill_result
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "--version" in argv:
            outcome = self.version_result
        elif "rm" in argv and "-f" in argv:
            outcome = self.kill_result or subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        else:
            outcome = self.main_result

        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome()
        return outcome


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/zeek/zeek:latest",
        "zeek/zeek",  # no tag at all -> implicitly ":latest"
        "localhost:5000/zeek/zeek",  # registry port colon, still no image tag
    ],
)
def test_podman_runner_refuses_unpinned_image(image):
    with pytest.raises(zeek.PinnedImageRequiredError):
        zeek.PodmanZeekRunner(image)


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/zeek/zeek:5.0.9",
        "zeek/zeek@sha256:" + "a" * 64,
        "localhost:5000/zeek/zeek:5.0.9",
    ],
)
def test_podman_runner_accepts_pinned_image(image):
    zeek.PodmanZeekRunner(image)  # must not raise


def test_run_constructs_expected_argv(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "--version" in argv:
            return _completed(argv, 0, stdout="zeek version 5.0.9-105\n")
        # Simulate the real `zeek -r` invocation having written its logs into `out_dir`.
        zeek.FakeZeekRunner().run(pcap, out_dir)
        return _completed(argv, 0)

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner(
        "docker.io/zeek/zeek:5.0.9", container_name_factory=_fixed_name("xtract-zeek-test")
    )
    result = runner.run(pcap, out_dir)

    assert calls[0] == [
        "podman",
        "run",
        "--rm",
        "--pull=never",
        "docker.io/zeek/zeek:5.0.9",
        "zeek",
        "--version",
    ]
    assert calls[1] == [
        "podman",
        "run",
        "--rm",
        "--userns=keep-id",
        "--pull=never",
        "--name",
        "xtract-zeek-test",
        "-v",
        f"{pcap}:/in.pcap:ro,Z",
        "-v",
        f"{out_dir}:/out:Z",
        "-w",
        "/out",
        "docker.io/zeek/zeek:5.0.9",
        "zeek",
        # `-C` — ignore checksum errors. Mandatory for any mirrored/virtual capture, whose
        # checksums are placeholders left for NIC offload; see DEFAULT_IGNORE_CHECKSUMS.
        "-C",
        "-r",
        "/in.pcap",
        # DEFAULT_SCRIPT_POLICY loads Zeek's site policy; without it Zeek emits only 5 of
        # aximprint's 16 parsed log types (see test_default_policy_loads_local_...).
        "local",
    ]
    assert result.version == "5.0.9-105"
    assert result.returncode == 0
    assert result.ok is True
    assert "conn" in result.logs


def test_script_policy_scripts_appended_to_argv(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner(
        "zeek/zeek:5.0.9",
        script_policy=zeek.ScriptPolicy(scripts=("local.zeek", "extra-policy.zeek")),
        container_name_factory=_fixed_name(),
    )
    runner.run(pcap, out_dir)

    assert scripted.calls[1][-2:] == ["local.zeek", "extra-policy.zeek"]


def test_default_script_policy_appends_local(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    runner.run(pcap, out_dir)

    assert scripted.calls[1][-1] == "local"
    assert scripted.calls[1][-5:-1] == ["zeek", "-C", "-r", "/in.pcap"]


def test_version_cached_across_multiple_runs(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    runner.run(pcap, tmp_path / "out1")
    runner.run(pcap, tmp_path / "out2")

    version_calls = [c for c in scripted.calls if "--version" in c]
    assert len(version_calls) == 1


def test_nonzero_exit_returns_failed_result_not_raise(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 1, stderr="malformed pcap: truncated packet"),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == 1
    assert "malformed pcap" in result.stderr_tail


def test_version_fetch_failure_returns_failed_result_not_raise(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(version_result=_completed(["x"], 1, stderr="no such image"))
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.version == ""
    assert "no such image" in result.stderr_tail
    assert result.logs == {}
    # a failed version fetch must never be cached as a false "success"
    assert runner._version is None


def test_timeout_returns_failed_result_and_kills_container(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=subprocess.TimeoutExpired(cmd=["zeek"], timeout=1.0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner(
        "zeek/zeek:5.0.9", timeout_s=1.0, container_name_factory=_fixed_name("xtract-zeek-test")
    )
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.TIMEOUT_RETURNCODE
    assert "timed out" in result.stderr_tail
    kill_calls = [c for c in scripted.calls if "rm" in c and "-f" in c]
    assert kill_calls == [["podman", "rm", "-f", "xtract-zeek-test"]]


def test_kill_failure_after_timeout_does_not_mask_the_timeout_result(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=subprocess.TimeoutExpired(cmd=["zeek"], timeout=1.0),
        kill_result=OSError("podman itself is wedged"),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", timeout_s=1.0, container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.TIMEOUT_RETURNCODE


# --------------------------------------------------------------------------------------------
# Phase 3 — log-set policy: report, never trim
# --------------------------------------------------------------------------------------------


def test_unrecognized_stems_reports_only_stems_outside_known_set():
    logs = {"conn": Path("conn.log"), _UNKNOWN_STEM: Path(f"{_UNKNOWN_STEM}.log"), "dns": Path("dns.log")}
    assert zeek.unrecognized_stems(logs) == (_UNKNOWN_STEM,)


def test_unrecognized_stems_empty_when_all_known():
    logs = {"conn": Path("conn.log"), "telemetry": Path("telemetry.log")}
    assert zeek.unrecognized_stems(logs) == ()


def test_known_parsed_and_skipped_stems_are_disjoint_and_their_union_is_known_zeek_stems():
    """`docs/WORK_ORDER_zeek_runner.md` §2 pinned exact counts here (16/4) when these lists were
    first introduced. Dropped (not reframed): `tests/test_acquire_zeek_stem_sync.py` now checks
    the far stronger property that actually matters -- exact agreement with
    `ingest.zeek.plugin`'s own `_PARSERS`/`_SKIP` -- so a bare count here was pure friction
    against `docs/WORK_ORDER_zeek_stem_coverage.md` D2/D3, which change these sizes on purpose.
    """
    assert zeek.KNOWN_PARSED_STEMS & zeek.KNOWN_SKIPPED_STEMS == frozenset()
    assert zeek.KNOWN_ZEEK_STEMS == zeek.KNOWN_PARSED_STEMS | zeek.KNOWN_SKIPPED_STEMS


def test_known_zeek_stems_all_have_a_fake_runner_schema():
    """`FakeZeekRunner`'s `_write_zeek_log` (via `_SCHEMAS.get(stem, _FALLBACK_SCHEMA)`) falls
    back, silently, to a two-column `ts uid` schema for any stem `_SCHEMAS` has no entry for.
    Demonstrated hazard: add a stem to `KNOWN_PARSED_STEMS` without adding a matching `_SCHEMAS`
    entry, and the fake emits two-column logs a real parser cannot usefully consume, while
    `unrecognized` stays EMPTY (the stem is "known" now) -- so every test/fixture built on
    `FakeZeekRunner`, including the harvest-to-synth gate
    (`tests/test_acquire_ingest_chain.py`), keeps passing without ever exercising the new
    parser. The exact failure `zeek.py`'s own comment above `_SCHEMAS["dhcp"]` already warns
    against: "a fake whose output silently yields zero events is worse than no fake at all."
    """
    assert zeek.KNOWN_ZEEK_STEMS <= set(zeek._SCHEMAS)


def test_fake_runner_flags_unrecognized_extra_stem(tmp_path):
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner(extra_stems=[_UNKNOWN_STEM]).run(pcap, tmp_path / "out")
    assert result.unrecognized == (_UNKNOWN_STEM,)


def test_fake_runner_reports_none_when_all_produced_stems_are_known(tmp_path):
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner(extra_stems=["dns", "notice", "telemetry"]).run(pcap, tmp_path / "out")
    assert result.unrecognized == ()


def test_default_policy_loads_local_because_bare_zeek_omits_half_the_parsed_set():
    """Measured 2026-08-06 against the pinned image: `zeek -r <pcap>` alone produces only 5 of
    `aximprint`'s 16 parsed log types; `zeek -r <pcap> local` produces 10. The five gained are
    capture_loss, known_hosts, known_services, notice and stats — and capture_loss/stats are the
    ONLY sources of SensorHealthEvent. Running bare would yield an archive with no sensor-health
    evidence at all, unrecoverably, since the cold tier is the evidence tier.

    So "no scripts" is itself a trim, in the other direction. This asserts the default is not that.
    """
    assert zeek.DEFAULT_SCRIPT_POLICY.scripts == ("local",)


def test_an_explicitly_empty_policy_still_adds_nothing(tmp_path, monkeypatch):
    """The bare-base-scripts option remains reachable for a caller who genuinely wants it."""
    pcap = _make_pcap(tmp_path)
    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)
    runner = zeek.PodmanZeekRunner(
        "zeek/zeek:5.0.9",
        script_policy=zeek.ScriptPolicy(scripts=()),
        container_name_factory=_fixed_name(),
    )
    runner.run(pcap, tmp_path / "out")
    argv = scripted.calls[-1]
    assert argv[-4:] == ["zeek", "-C", "-r", "/in.pcap"]


# --------------------------------------------------------------------------------------------
# Phase 4 — availability probe
# --------------------------------------------------------------------------------------------


def test_probe_fails_distinctly_when_podman_missing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no such file or directory: 'podman'")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()

    assert probe.ok is False
    assert probe.podman_available is False
    assert probe.image_present is False
    assert probe.zeek_version is None


def test_probe_fails_distinctly_when_podman_version_nonzero(monkeypatch):
    def fake_run(argv, **kwargs):
        return _completed(argv, 1, stderr="podman broken")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()

    assert probe.ok is False
    assert probe.podman_available is False


def test_probe_fails_distinctly_when_image_absent(monkeypatch):
    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            return _completed(argv, 1)  # podman image exists: 1 == absent
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()

    assert probe.ok is False
    assert probe.podman_available is True
    assert probe.image_present is False
    assert probe.zeek_version is None


def test_probe_fails_distinctly_when_zeek_version_fails(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["podman", "--version"]:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            return _completed(argv, 0)
        if "--version" in argv:  # zeek --version, inside the image
            return _completed(argv, 1, stderr="exec format error")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()

    assert probe.ok is False
    assert probe.podman_available is True
    assert probe.image_present is True
    assert probe.zeek_version is None
    assert "exec format error" in probe.detail


def test_probe_succeeds_when_everything_available(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["podman", "--version"]:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            return _completed(argv, 0)
        if "--version" in argv:
            return _completed(argv, 0, stdout="zeek version 5.0.9-105\n")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()

    assert probe.ok is True
    assert probe.podman_available is True
    assert probe.image_present is True
    assert probe.zeek_version == "5.0.9-105"


def test_probe_never_calls_podman_pull(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        if argv[:2] == ["podman", "--version"]:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            return _completed(argv, 0)
        return _completed(argv, 0, stdout="zeek version 5.0.9\n")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    zeek.PodmanZeekRunner("zeek/zeek:5.0.9").probe()

    assert not any("pull" in call and "never" not in " ".join(call) for call in seen)
    assert all("pull" not in call or "--pull=never" in call for call in seen)


# --- fake-output fidelity (regression: caught by cross-checking against the real consumer) -------


def test_every_schema_has_matching_field_and_type_counts():
    """A `#fields` / `#types` count disagreement is invisible without `strict=True` on the zip —
    it silently truncates the row instead. The transcribed `stats` schema had 27 fields and 28
    types when this check was added.
    """


    mismatched = {
        stem: (len(fields), len(types))
        for stem, (fields, types) in zeek._SCHEMAS.items()
        if len(fields) != len(types)
    }
    assert mismatched == {}


@pytest.mark.parametrize(
    "stem, field, predicate",
    [
        # `aximprint`'s `_dns` binds an answer only when it parses as a real IP; a type-plausible
        # `-` for `vector[addr]` yields a structurally perfect log worth zero events.
        ("dns", "answers", lambda v: v.count(".") == 3 and v.replace(".", "").isdigit()),
        ("dns", "query", lambda v: "." in v and not v[0].isdigit()),
        # `_dhcp` needs all three of ts, an address and a mac before it emits anything.
        ("dhcp", "assigned_addr", lambda v: v.count(".") == 3),
        ("dhcp", "mac", lambda v: v.count(":") == 5),
    ],
)
def test_semantically_required_fields_are_usable_not_merely_well_typed(tmp_path, stem, field, predicate):
    pcap = tmp_path / "in.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1" + b"\x00" * 20)
    out = tmp_path / "out"
    zeek.FakeZeekRunner(extra_stems=(stem,)).run(pcap, out)

    lines = (out / f"{stem}.log").read_text().splitlines()
    fields = next(ln for ln in lines if ln.startswith("#fields")).split("\t")[1:]
    row = next(ln for ln in lines if not ln.startswith("#")).split("\t")
    assert len(row) == len(fields), "row width must match #fields"
    value = row[fields.index(field)]
    assert predicate(value), f"{stem}.{field} = {value!r} is well-typed but not usable"


# --------------------------------------------------------------------------------------------
# Phase 5 — finding 3: `run()`/`probe()` must never raise on an ordinary production path
# --------------------------------------------------------------------------------------------


def test_run_missing_pcap_returns_failed_result_not_raise(tmp_path, monkeypatch):
    def _unexpected(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when the pcap is missing")

    monkeypatch.setattr(zeek.subprocess, "run", _unexpected)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    missing = tmp_path / "does-not-exist.pcap"

    result = runner.run(missing, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.RUNNER_FAILED_RETURNCODE
    assert result.logs == {}
    assert "FileNotFoundError" in result.stderr_tail
    # LES-039: the exception's type name is fine to record; the full path it would otherwise
    # embed is not.
    assert str(tmp_path) not in result.stderr_tail


def test_run_podman_not_on_path_returns_failed_result_not_raise(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    def _raise(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(zeek.subprocess, "run", _raise)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.RUNNER_FAILED_RETURNCODE
    assert "FileNotFoundError" in result.stderr_tail


def test_run_permission_error_from_the_main_invocation_returns_failed_result_not_raise(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=PermissionError(13, "Permission denied"),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.RUNNER_FAILED_RETURNCODE
    assert "PermissionError" in result.stderr_tail


def test_version_fetch_oserror_returns_failed_result_not_raise(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)

    def _raise(argv, **kwargs):
        # errno 11 (EAGAIN) — Python's OSError constructor maps this to BlockingIOError, its own
        # OSError subclass; this is exactly the "fork() failure under memory pressure" case
        # finding 3 names, and `type(exc).__name__` (not a hardcoded "OSError") is what must show
        # up in stderr_tail either way.
        raise OSError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(zeek.subprocess, "run", _raise)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == zeek.RUNNER_FAILED_RETURNCODE
    assert "BlockingIOError" in result.stderr_tail
    assert "Resource temporarily unavailable" not in result.stderr_tail  # LES-039: type name only


def test_probe_handles_permission_error_from_podman_version_without_raising(monkeypatch):
    def _raise(argv, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(zeek.subprocess, "run", _raise)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()  # must not raise

    assert probe.ok is False
    assert probe.podman_available is False


def test_probe_handles_oserror_from_image_exists_without_raising(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["podman", "--version"]:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            raise OSError(11, "Resource temporarily unavailable")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9")
    probe = runner.probe()  # must not raise

    assert probe.ok is False
    assert probe.podman_available is True
    assert probe.image_present is False


# --------------------------------------------------------------------------------------------
# Phase 6 — finding 7: FakeZeekRunner can now fail, produce no logs, and shares a conformance
# suite with PodmanZeekRunner so the two can never silently diverge again
# --------------------------------------------------------------------------------------------


def test_fake_runner_missing_pcap_returns_failed_result_not_ok_true(tmp_path):
    """The pre-fix divergence: the fake used to return `ok=True, bytes_in=0` for a missing pcap —
    silently passing every test built against it while the real runner raised. Both must now
    agree: a missing pcap is a failed `ZeekResult`, never a fabricated success.
    """
    missing = tmp_path / "does-not-exist.pcap"
    result = zeek.FakeZeekRunner().run(missing, tmp_path / "out")

    assert result.ok is False
    assert result.logs == {}


def test_fake_runner_can_be_configured_to_fail():
    """Pre-fix, `returncode` was hardcoded to `0` — H-4's single most important behavior (a failed
    Zeek run must not release its pcap) could never be exercised against the fake at all.
    """
    runner = zeek.FakeZeekRunner(returncode=1, stderr_tail="malformed pcap")
    assert runner.returncode == 1


def test_fake_runner_failed_run_result_reports_failure(tmp_path):
    pcap = _make_pcap(tmp_path)
    runner = zeek.FakeZeekRunner(returncode=1, stderr_tail="malformed pcap: truncated packet")

    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert result.returncode == 1
    assert result.stderr_tail == "malformed pcap: truncated packet"


def test_fake_runner_failed_run_can_still_report_partial_output(tmp_path):
    """A real Zeek run can crash mid-way and still have written some logs before it died — the
    fake must be able to model that combination too, not just all-or-nothing.
    """
    pcap = _make_pcap(tmp_path)
    runner = zeek.FakeZeekRunner(stems=("conn",), returncode=1)

    result = runner.run(pcap, tmp_path / "out")

    assert result.ok is False
    assert set(result.logs) == {"conn"}


def test_fake_runner_stems_empty_produces_no_logs_at_all():
    """Real Zeek over a quiet window writes no logs at all — the pre-fix fake always wrote
    `conn.log` regardless, so `result.logs["conn"]` passed every test built against it and would
    `KeyError` in production on the common case (`LES-035`'s sibling: a quiet window is ordinary,
    not exceptional, at every layer of this pipeline).
    """
    runner = zeek.FakeZeekRunner(stems=())
    result = runner.run(Path("/dev/null"), Path("/tmp"))  # unused for this assertion's purpose
    assert result.logs == {}


def test_fake_runner_default_stems_is_still_just_conn(tmp_path):
    """Backward compatibility: every existing caller that never passes `stems=` must keep seeing
    exactly `{"conn"}` by default."""
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner().run(pcap, tmp_path / "out")
    assert set(result.logs) == {"conn"}


def test_fake_runner_extra_stems_still_combine_with_the_default_stem(tmp_path):
    """`extra_stems` (the older knob) must keep working exactly as before, now implemented on top
    of `stems`."""
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner(extra_stems=["dns", "ssl"]).run(pcap, tmp_path / "out")
    assert set(result.logs) == {"conn", "dns", "ssl"}


# --- shared conformance suite: FakeZeekRunner and PodmanZeekRunner must agree ------------------


def _real_runner_for_conformance(monkeypatch, *, main_result=None, write_logs=None):
    """A `PodmanZeekRunner` wired to a scripted `subprocess.run` so the conformance suite can drive
    it through the same scenarios as `FakeZeekRunner` without touching a real `podman`.
    `write_logs`, if given, is invoked right before the main call returns — standing in for Zeek
    actually having written files, since the scripted stand-in never touches the filesystem
    itself.
    """

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return _completed(argv, 0, stdout="zeek version 5.0.9-105\n")
        if "rm" in argv and "-f" in argv:
            return _completed(argv, 0)
        if write_logs is not None:
            write_logs()
        return main_result if main_result is not None else _completed(argv, 0)

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)
    return zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())


@pytest.mark.parametrize("kind", ["fake", "real"])
def test_conformance_missing_pcap(tmp_path, monkeypatch, kind):
    runner = zeek.FakeZeekRunner() if kind == "fake" else _real_runner_for_conformance(monkeypatch)
    result = runner.run(tmp_path / "missing.pcap", tmp_path / "out")
    assert result.ok is False
    assert result.logs == {}


@pytest.mark.parametrize("kind", ["fake", "real"])
def test_conformance_empty_output_dir_on_a_quiet_window(tmp_path, monkeypatch, kind):
    pcap = _make_pcap(tmp_path)
    runner = (
        zeek.FakeZeekRunner(stems=())
        if kind == "fake"
        else _real_runner_for_conformance(monkeypatch)  # writes nothing by default
    )
    result = runner.run(pcap, tmp_path / "out")
    assert result.ok is True
    assert result.logs == {}


@pytest.mark.parametrize("kind", ["fake", "real"])
def test_conformance_nonzero_exit(tmp_path, monkeypatch, kind):
    pcap = _make_pcap(tmp_path)
    if kind == "fake":
        runner = zeek.FakeZeekRunner(returncode=1, stderr_tail="boom")
    else:
        runner = _real_runner_for_conformance(monkeypatch, main_result=_completed(["x"], 1, stderr="boom"))
    result = runner.run(pcap, tmp_path / "out")
    assert result.ok is False
    assert result.returncode == 1


@pytest.mark.parametrize("kind", ["fake", "real"])
def test_conformance_unrecognized_classification(tmp_path, monkeypatch, kind):
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"
    if kind == "fake":
        result = zeek.FakeZeekRunner(stems=(_UNKNOWN_STEM,)).run(pcap, out_dir)
    else:

        def _write():
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{_UNKNOWN_STEM}.log").write_text("#separator \\x09\n#fields\tts\n#types\ttime\n")

        result = _real_runner_for_conformance(monkeypatch, write_logs=_write).run(pcap, out_dir)
    assert result.unrecognized == (_UNKNOWN_STEM,)


# --------------------------------------------------------------------------------------------
# Phase 7 — findings 9-16: the smaller ALSO FIX items
# --------------------------------------------------------------------------------------------


def test_timeout_preserves_stdout_and_stderr_in_stderr_tail(tmp_path, monkeypatch):
    """Finding 9: `TimeoutExpired.stdout`/`.stderr` carry Zeek's last words before it was killed —
    the pre-fix code discarded both.
    """
    pcap = _make_pcap(tmp_path)
    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=subprocess.TimeoutExpired(
            cmd=["zeek"], timeout=1.0, output=b"partial stdout line", stderr=b"partial stderr line"
        ),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", timeout_s=1.0, container_name_factory=_fixed_name())
    result = runner.run(pcap, tmp_path / "out")

    assert "partial stdout line" in result.stderr_tail
    assert "partial stderr line" in result.stderr_tail


def test_signal_killed_client_process_also_kills_the_container(tmp_path, monkeypatch):
    """Finding 10: a signal-killed client (e.g. the OOM killer) never raises `TimeoutExpired`, so
    without this the container it started would leak — `--rm` only removes it on a normal exit.
    """
    pcap = _make_pcap(tmp_path)
    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], -9, stderr="killed"),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)

    runner = zeek.PodmanZeekRunner(
        "zeek/zeek:5.0.9", container_name_factory=_fixed_name("xtract-zeek-test")
    )
    result = runner.run(pcap, tmp_path / "out")

    assert result.returncode == -9
    kill_calls = [c for c in scripted.calls if "rm" in c and "-f" in c]
    assert kill_calls == [["podman", "rm", "-f", "xtract-zeek-test"]]


def test_probe_uses_a_short_timeout_not_the_full_run_timeout(monkeypatch):
    """Finding 11: `probe()`'s own preconditions must not be able to hang for `DEFAULT_TIMEOUT_S`
    (900s) — that would defeat the entire purpose of a startup check.
    """
    seen_timeouts = []

    def fake_run(argv, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        if argv[:2] == ["podman", "--version"]:
            return _completed(argv, 0, stdout="podman version 4.9.0\n")
        if "exists" in argv:
            return _completed(argv, 0)
        return _completed(argv, 0, stdout="zeek version 5.0.9\n")

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)
    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", timeout_s=zeek.DEFAULT_TIMEOUT_S)
    runner.probe()

    assert seen_timeouts[0] == zeek.PROBE_TIMEOUT_S
    assert seen_timeouts[1] == zeek.PROBE_TIMEOUT_S
    assert zeek.PROBE_TIMEOUT_S < zeek.DEFAULT_TIMEOUT_S


def test_concurrent_first_runs_only_fetch_version_once(tmp_path, monkeypatch):
    """Finding 12: H-4 runs M worker threads sharing one `PodmanZeekRunner`; without a lock around
    the lazy fetch-and-cache, N threads reaching `run()` on the very first pcap concurrently could
    each spawn their own `zeek --version` container.
    """
    pcap = _make_pcap(tmp_path)
    call_count = {"version": 0}
    version_call_started = threading.Event()
    proceed = threading.Event()

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            call_count["version"] += 1
            version_call_started.set()
            assert proceed.wait(timeout=5), "test itself stalled"
            return _completed(argv, 0, stdout="zeek version 5.0.9\n")
        return _completed(argv, 0)

    monkeypatch.setattr(zeek.subprocess, "run", fake_run)
    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())

    results = []

    def _run(out_dir):
        results.append(runner.run(pcap, out_dir))

    t1 = threading.Thread(target=_run, args=(tmp_path / "out1",))
    t1.start()
    assert version_call_started.wait(timeout=5), "first run() never reached _fetch_version"

    # Started only once thread 1 is already inside the locked fetch (blocked on `proceed`) — if
    # the lock is working, thread 2 must block acquiring it rather than also calling --version.
    t2 = threading.Thread(target=_run, args=(tmp_path / "out2",))
    t2.start()
    proceed.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive()
    assert call_count["version"] == 1


def test_default_zeek_image_is_pinned_and_matches_the_live_measured_tag():
    """Finding 14: a ready-made, already-pinned default so a caller doesn't have to hand-type
    (and risk mistyping) the tag."""
    assert zeek.DEFAULT_ZEEK_IMAGE == "docker.io/zeek/zeek:8.0"
    zeek.PodmanZeekRunner(zeek.DEFAULT_ZEEK_IMAGE)  # must not raise PinnedImageRequiredError


def test_pinned_image_required_error_docstring_explains_a_minor_tag_is_still_a_moving_pin():
    assert "moving pin" in zeek.PinnedImageRequiredError.__doc__.lower()


def test_zeek_result_logs_is_not_mutable_through_the_result(tmp_path):
    """Finding 15: a plain `dict` field on an otherwise-`frozen=True` dataclass is still mutable
    through `result.logs["x"] = ...` — the proxy closes that."""
    pcap = _make_pcap(tmp_path)
    result = zeek.FakeZeekRunner().run(pcap, tmp_path / "out")
    with pytest.raises(TypeError):
        result.logs["conn"] = Path("/tmp/evil")


def test_zeek_result_is_hashable_despite_the_mapping_field():
    """Finding 15: the dataclass-generated `__hash__` must not raise just because `logs` (a
    mapping — proxied or not, mappings are unhashable) is one of the fields."""
    result = zeek.ZeekResult(
        version="5.0.0", logs={}, duration_s=0.1, returncode=0, stderr_tail="", bytes_in=0
    )
    hash(result)  # must not raise


def test_run_refuses_a_stale_output_dir_with_leftover_logs_fake(tmp_path):
    """Finding 16: reusing an `out_dir` across two different pcaps must never report a previous
    run's stale logs as this run's."""
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "conn.log").write_text("stale from a previous pcap\n")

    result = zeek.FakeZeekRunner().run(pcap, out_dir)

    assert result.ok is False
    assert result.logs == {}


def test_run_refuses_a_stale_output_dir_with_leftover_logs_real(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "conn.log").write_text("stale from a previous pcap\n")

    def _unexpected(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called against a stale out_dir")

    monkeypatch.setattr(zeek.subprocess, "run", _unexpected)

    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    result = runner.run(pcap, out_dir)

    assert result.ok is False
    assert result.returncode == zeek.RUNNER_FAILED_RETURNCODE


# --------------------------------------------------------------------------------------------
# GAP-L3 (`docs/JOURNAL_imprint_loop.md`) — checksum validation must be off by default.
#
# Without `-C`, a capture mirrored off a virtual interface is analyzed as zero established
# connections (`history=C`, `orig_pkts=0`, no `service`), the listen census stays empty, no role is
# ever inferred, and `cast` emits an empty twin — with every stage reporting success. These two
# tests pin the flag and its escape hatch so that cannot silently regress.
# --------------------------------------------------------------------------------------------
def test_ignore_checksums_is_on_by_default(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)
    runner = zeek.PodmanZeekRunner("zeek/zeek:5.0.9", container_name_factory=_fixed_name())
    runner.run(pcap, tmp_path / "out")
    argv = scripted.calls[-1]
    assert "-C" in argv, "checksum validation must be disabled by default (GAP-L3)"
    # ordering matters: -C is a Zeek flag, so it must precede the -r it modifies
    assert argv.index("-C") < argv.index("-r")


def test_ignore_checksums_can_be_disabled(tmp_path, monkeypatch):
    pcap = _make_pcap(tmp_path)
    scripted = ScriptedSubprocess(
        version_result=_completed(["x"], 0, stdout="zeek version 5.0.9\n"),
        main_result=_completed(["x"], 0),
    )
    monkeypatch.setattr(zeek.subprocess, "run", scripted)
    runner = zeek.PodmanZeekRunner(
        "zeek/zeek:5.0.9", ignore_checksums=False, container_name_factory=_fixed_name()
    )
    runner.run(pcap, tmp_path / "out")
    assert "-C" not in scripted.calls[-1]
