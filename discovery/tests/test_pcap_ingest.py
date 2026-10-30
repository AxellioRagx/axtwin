"""`ingest/pcap/SPEC.md`, "Automated acceptance", on the `FakeZeekRunner` — podman is absent
in CI and `conftest.py` forbids network, so no test here runs a real Zeek. Every claim below is
one bullet of that section, plus the refusals the plugin's own docstring names.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aximprint.ingest import get_plugin, list_plugins
from aximprint.ingest.pcap import PcapPlugin
from aximprint.ingest.pcap import plugin as pp
from aximprint.ingest.zeek import ZeekPlugin
from aximprint.ingest.zeek.sources import _looks_like_window_id
from aximprint.zeekrun import (
    DEFAULT_ZEEK_IMAGE,
    FakeZeekRunner,
    NativeZeekRunner,
    PinnedImageRequiredError,
    PodmanZeekRunner,
    ScriptPolicy,
)

_STEMS = ("conn", "dns", "known_services")


def _pcap(tmp_path: Path, name: str = "cap.pcap", size: int = 64) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xd4\xc3\xb2\xa1" + b"\x00" * size)
    return p


class _RecordingFake(FakeZeekRunner):
    """`FakeZeekRunner` that remembers every `(pcap, out_dir)` it was asked to run."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: list[tuple[Path, Path]] = []

    def run(self, pcap, out_dir):
        self.calls.append((pcap, out_dir))
        return super().run(pcap, out_dir)


def _zeek_events_over(fake: FakeZeekRunner, pcaps: list[Path], root: Path) -> list:
    """What the `zeek` plugin yields over the directory the same fake writes directly —
    flat for one capture, a `<root>/<i>/` window tree for several."""
    if len(pcaps) == 1:
        fake.run(pcaps[0], root)
    else:
        for i, p in enumerate(pcaps):
            fake.run(p, root / str(i))
    return list(ZeekPlugin().events(str(root)))


# --- registry -----------------------------------------------------------------------------

def test_listed_with_the_closed_shared_schema():
    rows = {r["name"]: r for r in list_plugins()}
    assert "pcap" in rows
    assert rows["pcap"]["source_kind"] == "path"
    schema = rows["pcap"]["config_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["source"]
    assert "image" not in schema["properties"]  # options ride in the locator, not the schema
    assert "pcap:" in schema["properties"]["source"]["description"]
    assert isinstance(get_plugin("pcap"), PcapPlugin)


# --- one file: the same events as the zeek plugin over the fake's output ------------------

def test_one_file_locator_yields_the_zeek_plugins_events(tmp_path):
    cap = _pcap(tmp_path)
    expected = _zeek_events_over(FakeZeekRunner(stems=_STEMS), [cap], tmp_path / "direct")

    got = list(PcapPlugin(runner=FakeZeekRunner(stems=_STEMS)).events(f"pcap:{cap}"))
    assert got and got == expected


def test_stem_inventory_is_the_delegates(tmp_path):
    cap = _pcap(tmp_path)
    plugin = PcapPlugin(runner=FakeZeekRunner(stems=("conn", "telemetry", "__odd__")))
    assert plugin.unparsed_stems is None and plugin.unrecognized_stems is None
    list(plugin.events(f"pcap:{cap}"))
    assert plugin.unparsed_stems == {"telemetry": 1, "__odd__": 1}
    assert plugin.unrecognized_stems == {"__odd__": 1}


# --- a directory: a window tree -------------------------------------------------------------

def test_directory_locator_runs_each_capture_into_a_window_tree(tmp_path):
    caps = tmp_path / "caps"
    caps.mkdir()
    b = _pcap(caps, "b.pcapng")
    a = _pcap(caps, "a.pcap")
    (caps / "notes.txt").write_text("ignored: not a capture\n")
    (caps / "nested").mkdir()
    _pcap(caps / "nested", "deep.pcap")  # not recursive: never run

    fake = _RecordingFake(stems=_STEMS)
    got = list(PcapPlugin(runner=fake).events(f"pcap:{caps}"))

    assert [c[0] for c in fake.calls] == [a, b]  # name-sorted, immediate children only
    roots = {c[1].parent for c in fake.calls}
    assert len(roots) == 1  # one temporary directory for the whole run
    root = roots.pop()
    assert [c[1].name for c in fake.calls] == ["0", "1"]
    assert all(_looks_like_window_id(c[1].name) for c in fake.calls)  # the reader's own gate
    expected = _zeek_events_over(FakeZeekRunner(stems=_STEMS), [a, b], tmp_path / "direct")
    assert got == expected
    assert len(got) == 2 * len(_zeek_events_over(FakeZeekRunner(stems=_STEMS), [a],
                                                 tmp_path / "one"))
    assert not root.exists()


def test_directory_with_no_capture_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(pp.PcapError, match="no .*file directly under it"):
        PcapPlugin(runner=FakeZeekRunner()).events(f"pcap:{tmp_path / 'empty'}")


# --- refusals -------------------------------------------------------------------------------

@pytest.mark.parametrize("bounds", [(1.0, None), (None, 2.0), (1.0, 2.0)])
def test_a_time_slice_is_refused(tmp_path, bounds):
    cap = _pcap(tmp_path)
    with pytest.raises(ValueError, match="no partition index"):
        PcapPlugin(runner=FakeZeekRunner()).events(f"pcap:{cap}", *bounds)


def test_probe_vantage_is_none(tmp_path):
    cap = _pcap(tmp_path)
    assert PcapPlugin(runner=FakeZeekRunner()).probe_vantage(f"pcap:{cap}") is None
    assert get_plugin("pcap").probe_vantage(f"pcap:{cap}") is None


@pytest.mark.parametrize("image", ["zeek/zeek:latest", "docker.io/zeek/zeek"])
def test_a_latest_or_tagless_image_is_refused_by_name(tmp_path, image):
    cap = _pcap(tmp_path)
    with pytest.raises(PinnedImageRequiredError, match="latest"):
        PcapPlugin(runner=FakeZeekRunner()).events(f"pcap:{cap}?image={image}")
    answer = PcapPlugin(runner=FakeZeekRunner()).probe(f"pcap:{cap}?image={image}")
    assert answer["supported"] is True and answer["reachable"] is False
    assert "latest" in answer["error"]


def test_a_digest_pinned_image_is_accepted(tmp_path):
    cap = _pcap(tmp_path)
    loc = pp.parse_locator(f"pcap:{cap}?image=docker.io/zeek/zeek@sha256:{'0' * 64}")
    assert loc.image.endswith("0" * 64)


@pytest.mark.parametrize("source, why", [
    ("/caps/x.pcap", "must be 'pcap:"),
    ("pcap:", "names no file"),
    ("pcap:/x.pcap?tmp=/elsewhere", "option 'tmp' is not one of"),
    ("pcap:/x.pcap?scripts=all", "scripts='all' is not one of"),
    ("pcap:/x.pcap?checksums=maybe", "checksums='maybe' is not one of"),
    ("pcap:/x.pcap?scripts=local&scripts=bare", "given twice"),
    ("pcap:/x.pcap?image=", "image= is empty"),
])
def test_locator_refusals_name_the_reason(source, why):
    with pytest.raises(pp.PcapError, match=why):
        pp.parse_locator(source)


def test_locator_defaults_and_options():
    loc = pp.parse_locator("pcap:/caps/site.pcap")
    assert (loc.path, loc.image, loc.scripts, loc.checksums) == (
        Path("/caps/site.pcap"), DEFAULT_ZEEK_IMAGE, "local", "skip")
    assert loc.script_policy.scripts == ("local",) and loc.ignore_checksums is True
    loc = pp.parse_locator("pcap:/caps?scripts=bare&checksums=verify&image=zeek/zeek:7.0")
    assert loc.script_policy == ScriptPolicy(scripts=()) and loc.ignore_checksums is False
    assert loc.image == "zeek/zeek:7.0"


def test_a_missing_path_is_refused_before_anything_runs(tmp_path, monkeypatch):
    plugin = PcapPlugin(runner=_RecordingFake())
    with pytest.raises(pp.PcapError, match="no such file or directory"):
        plugin.events(f"pcap:{tmp_path / 'gone.pcap'}")
    assert plugin._runner.calls == []
    answer = plugin.probe(f"pcap:{tmp_path / 'gone.pcap'}")
    assert answer["reachable"] is False and "no such file" in answer["error"]


def test_a_failed_zeek_run_is_refused_not_read_as_quiet(tmp_path):
    cap = _pcap(tmp_path)
    plugin = PcapPlugin(runner=FakeZeekRunner(stems=("conn",), returncode=1,
                                              stderr_tail="fatal error: bad pcap"))
    it = plugin.events(f"pcap:{cap}")
    with pytest.raises(pp.PcapError, match=r"exit 1.*bad pcap"):
        next(it)


# --- the temporary directory ------------------------------------------------------------

def _temp_root_of(fake: _RecordingFake) -> Path:
    assert fake.calls, "the fake was never run"
    out = fake.calls[0][1]
    return out if out.name != "0" else out.parent


def test_temporary_directory_is_gone_after_the_iterator_is_exhausted(tmp_path):
    cap = _pcap(tmp_path)
    fake = _RecordingFake(stems=_STEMS)
    events = list(PcapPlugin(runner=fake).events(f"pcap:{cap}"))
    assert events
    root = _temp_root_of(fake)
    assert "aximprint-pcap-" in root.name
    assert not root.exists()


def test_temporary_directory_exists_during_iteration_and_is_gone_on_close(tmp_path):
    cap = _pcap(tmp_path)
    fake = _RecordingFake(stems=_STEMS, rows_per_log=5)
    it = PcapPlugin(runner=fake).events(f"pcap:{cap}")
    first = next(it)
    root = _temp_root_of(fake)
    assert root.is_dir() and (root / "conn.log").is_file()
    it.close()  # early close, most of the events never pulled
    assert not root.exists()
    assert first is not None


def test_temporary_directory_is_gone_after_a_failed_run(tmp_path):
    cap = _pcap(tmp_path)
    fake = _RecordingFake(stems=("conn",), returncode=2)
    with pytest.raises(pp.PcapError):
        list(PcapPlugin(runner=fake).events(f"pcap:{cap}"))
    assert not _temp_root_of(fake).exists()


def test_temporary_directory_honors_tmpdir(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    import tempfile
    tempfile.tempdir = None  # make `gettempdir()` re-read the environment
    try:
        cap = _pcap(tmp_path)
        fake = _RecordingFake()
        list(PcapPlugin(runner=fake).events(f"pcap:{cap}"))
        assert _temp_root_of(fake).parent == scratch
    finally:
        tempfile.tempdir = None


# --- probe --------------------------------------------------------------------------------

def test_probe_counts_files_and_bytes_and_names_the_runner(tmp_path):
    caps = tmp_path / "caps"
    caps.mkdir()
    _pcap(caps, "a.pcap", size=10)
    _pcap(caps, "b.pcap", size=20)
    answer = PcapPlugin(runner=FakeZeekRunner()).probe(f"pcap:{caps}")
    assert answer == {"supported": True, "reachable": True, "error": None, "files": 2,
                      "bytes": 2 * 4 + 30, "runner": "FakeZeekRunner", "vantage": None}


# --- runner choice is a probe ---------------------------------------------------------------

def _which(available: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_no_runner_on_path_is_refused_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", _which(set()))
    cap = _pcap(tmp_path)
    with pytest.raises(pp.PcapError, match="none of zeek, podman, docker is on PATH"):
        PcapPlugin().events(f"pcap:{cap}")
    answer = PcapPlugin().probe(f"pcap:{cap}")
    assert answer["reachable"] is False and answer["runner"] is None
    assert "none of zeek, podman, docker" in answer["error"]


@pytest.mark.parametrize("available, expected_runtime, expected_cls", [
    ({"zeek", "podman", "docker"}, "zeek", NativeZeekRunner),
    ({"podman", "docker"}, "podman", PodmanZeekRunner),
    ({"docker"}, "docker", PodmanZeekRunner),
])
def test_runner_choice_order_native_then_podman_then_docker(tmp_path, monkeypatch, available,
                                                           expected_runtime, expected_cls):
    monkeypatch.setattr(shutil, "which", _which(available))
    built: list = []

    def _probe_ok(self):
        built.append(self)
        from aximprint.zeekrun import ProbeResult
        return ProbeResult(ok=True, podman_available=True, image_present=True,
                           zeek_version="8.0.9", detail="ok")

    monkeypatch.setattr(NativeZeekRunner, "probe", _probe_ok)
    monkeypatch.setattr(PodmanZeekRunner, "probe", _probe_ok)
    loc = pp.parse_locator(f"pcap:{_pcap(tmp_path)}?scripts=bare&checksums=verify")
    runtime, runner = PcapPlugin()._choose_runner(loc)
    assert runtime == expected_runtime and isinstance(runner, expected_cls)
    assert runner.script_policy == ScriptPolicy(scripts=()) and runner.ignore_checksums is False
    if expected_cls is PodmanZeekRunner:
        assert runner.podman_bin == expected_runtime
        assert runner.image == DEFAULT_ZEEK_IMAGE
        argv = runner._run_argv(Path("/in.pcap"), Path("/out"), "c")
        if expected_runtime == "docker":
            assert "--userns=keep-id" not in argv and "--user" in argv
        else:
            assert "--userns=keep-id" in argv
        assert "-C" not in argv and "local" not in argv


def test_a_found_runtime_that_cannot_run_is_refused_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", _which({"podman"}))
    from aximprint.zeekrun import ProbeResult
    monkeypatch.setattr(PodmanZeekRunner, "probe", lambda self: ProbeResult(
        ok=False, podman_available=True, image_present=False, zeek_version=None,
        detail=f"{DEFAULT_ZEEK_IMAGE} not present locally"))
    cap = _pcap(tmp_path)
    with pytest.raises(pp.PcapError, match="'podman' cannot run: .* not present locally"):
        PcapPlugin().events(f"pcap:{cap}")
    answer = PcapPlugin().probe(f"pcap:{cap}")
    assert answer["reachable"] is False and "not present locally" in answer["error"]
    assert answer["files"] == 1  # the capture itself was found; only the runner is missing
