"""Discovery runs (`api/Spec.md` A1): `GET /api/discovery/plugins`,
`POST /api/discovery/runs`, `GET /api/discovery/runs/{id}`.

A1.1 and A1.3 run against a fake backend (the suite never runs `aximprint`
by accident). A1.2 runs the REAL chain -- ingest -> bake -> cast ->
install -- through the `aximprint` executable installed beside this
interpreter, on a synthetic Zeek capture, and reads the installed imprint back
through `GET /api/imprints/{id}`. It is skipped when the binary is not there.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api import discovery as disc

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette not installed", allow_module_level=True)

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"

ZEEK_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "description": "a Zeek log directory"},
        "ts_from": {"type": "number"},
        "window": {"type": "integer"},
        "vantage": {"type": "string"},
    },
    "required": ["source"],
    "additionalProperties": False,
}


def _settings(tmp_path: Path, **over) -> ApiSettings:
    (tmp_path / "imprints").mkdir(exist_ok=True)
    (tmp_path / "disco").mkdir(exist_ok=True)
    kw = dict(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(FIXT_IR.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config="/nonexistent.conf",
        aximprint_bin="/usr/bin/false-aximprint",  # never executed by the fakes
        aximprint_store=str(tmp_path / "disco" / "ir.duckdb"),
        imprints_dir=str(tmp_path / "imprints"),
    )
    kw.update(over)
    return ApiSettings(**kw)


class _FakeDiscovery:
    """Records every request; `run` does what `behaviour` says."""

    def __init__(self, plugins=None, behaviour=None):
        self.plugins = plugins if plugins is not None else [
            {"name": "zeek", "config_schema": ZEEK_SCHEMA},
            {"name": "suricata", "config_schema": {**ZEEK_SCHEMA, "properties": {
                "source": {"type": "string", "description": "an eve.json"}}}},
        ]
        self.behaviour = behaviour
        self.requests: list[disc.DiscoveryRunRequest] = []

    def list_plugins(self):
        return list(self.plugins)

    def run(self, request, *, on_stage, on_progress=None, handle=None):
        self.requests.append(request)
        if self.behaviour is not None:
            return self.behaviour(request, on_stage)
        for s in disc.STAGES:
            on_stage(s)
        return disc.DiscoveryRunResult(pit_id="pit-fake", stages=list(disc.STAGES))

    # The PIT table the fake "store" holds: `{pit_id: name}`.
    pits = {"pit-fake": "pit-fake"}

    def list_pits(self, store):
        return [{"pit_id": p, "name": n, "as_of": 100.0} for p, n in self.pits.items()]

    def rename_pit(self, store, pit, name):
        hit = [p for p, n in self.pits.items() if p == pit or n == pit]
        if not hit:
            raise disc.DiscoveryError(f"no PIT {pit!r}")
        if any(n == name or p == name for p, n in self.pits.items() if p != hit[0]):
            raise disc.DiscoveryError(f"{name!r} is already the name or id of another PIT")
        self.pits[hit[0]] = name
        return {"pit_id": hit[0], "name": name, "as_of": 100.0}


def _client(settings, *, backend=None, background="sync"):
    app = create_app(settings)
    app.state.discovery_backend = backend
    if background == "sync":
        app.state.discovery_background = lambda job: job()
    elif background is not None:
        app.state.discovery_background = background
    return TestClient(app, raise_server_exceptions=False)


def _run_body(**over):
    body = {"plugin": "zeek", "config": {"source": "/captures/one"}, "imprint_id": "site-a"}
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# A1.1 -- the plugin listing
# ---------------------------------------------------------------------------
def test_plugins_lists_every_installed_plugin_with_its_schema(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.get("/api/discovery/plugins")
    assert r.status_code == 200, r.text
    plugins = r.json()["plugins"]
    assert [p["name"] for p in plugins] == ["zeek", "suricata"]
    assert plugins[0]["config_schema"] == ZEEK_SCHEMA


def test_zero_plugins_is_an_empty_list_not_an_error(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery(plugins=[]))
    r = c.get("/api/discovery/plugins")
    assert r.status_code == 200
    assert r.json() == {"plugins": []}


@pytest.mark.parametrize("unset, key", [
    ("aximprint_bin", disc.SETTING_BIN),
    ("aximprint_store", disc.SETTING_STORE),
    ("imprints_dir", disc.SETTING_IMPRINTS_DIR),
])
def test_an_unconfigured_discovery_refuses_naming_the_setting(tmp_path, unset, key):
    c = _client(_settings(tmp_path, **{unset: ""}))
    r = c.get("/api/discovery/plugins")
    assert r.status_code == 503
    assert key in r.json()["error"]
    r = c.post("/api/discovery/runs", json=_run_body())
    assert r.status_code == 503
    assert key in r.json()["error"]


def test_a_binary_that_cannot_run_is_503_and_a_broken_listing_is_502(tmp_path):
    settings = _settings(tmp_path, aximprint_bin=str(tmp_path / "missing-aximprint"))
    c = _client(settings)  # the real SubprocessDiscovery
    r = c.get("/api/discovery/plugins")
    assert r.status_code == 503
    assert disc.SETTING_BIN in r.json()["error"]

    bogus = tmp_path / "bogus-aximprint"
    bogus.write_text("#!/bin/sh\necho not-json\n")
    bogus.chmod(0o755)
    c = _client(_settings(tmp_path, aximprint_bin=str(bogus)))
    r = c.get("/api/discovery/plugins")
    assert r.status_code == 502
    assert "plugin listing" in r.json()["error"]


# ---------------------------------------------------------------------------
# A1.3 -- refusals at POST, nothing written
# ---------------------------------------------------------------------------
def _catalog_untouched(settings):
    return sorted(p.name for p in Path(settings.imprints_dir).iterdir()) == []


def test_an_unknown_plugin_is_refused_by_name_and_nothing_runs(tmp_path):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    r = c.post("/api/discovery/runs", json=_run_body(plugin="pcap"))
    assert r.status_code == 400, r.text
    assert "'pcap'" in r.json()["error"]
    assert r.json()["detail"]["plugins"] == ["suricata", "zeek"]
    assert be.requests == []
    assert _catalog_untouched(settings)


@pytest.mark.parametrize("config, words", [
    ({}, ["missing required key 'source'"]),
    ({"source": "/x", "pcap": "/y"}, ["unknown key 'pcap'"]),
    ({"source": 7}, ["'source' must be string"]),
    ({"source": "/x", "window": "1h"}, ["'window' must be integer"]),
    ({"source": "/x", "window": True}, ["'window' must be integer"]),
    ({"source": "/x", "ts_from": "now", "bogus": 1}, ["'ts_from' must be number", "unknown key 'bogus'"]),
])
def test_an_invalid_config_is_refused_with_the_schema_s_reason(tmp_path, config, words):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    r = c.post("/api/discovery/runs", json=_run_body(config=config))
    assert r.status_code == 400, r.text
    for w in words:
        assert w in r.json()["error"], r.json()
    assert r.json()["detail"]["problems"]
    assert be.requests == []
    assert _catalog_untouched(settings)


def test_an_unsafe_or_taken_imprint_id_is_refused(tmp_path):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    r = c.post("/api/discovery/runs", json=_run_body(imprint_id="../escape"))
    assert r.status_code == 400
    assert "catalog id" in r.json()["error"]

    (Path(settings.imprints_dir) / "site-a").mkdir()
    r = c.post("/api/discovery/runs", json=_run_body(imprint_id="site-a"))
    assert r.status_code == 409
    assert "never overwrites" in r.json()["error"]
    assert be.requests == []


def test_a_second_run_for_an_in_progress_imprint_is_refused(tmp_path):
    settings = _settings(tmp_path)
    parked: list = []
    c = _client(settings, backend=_FakeDiscovery(), background=parked.append)
    r = c.post("/api/discovery/runs", json=_run_body())
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    r2 = c.post("/api/discovery/runs", json=_run_body())
    assert r2.status_code == 409
    assert "in progress" in r2.json()["error"]
    # A different id is fine.
    assert c.post("/api/discovery/runs", json=_run_body(imprint_id="site-b")).status_code == 202
    assert len(parked) == 2


def test_the_write_carries_the_token_and_the_reads_do_not(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, backend=_FakeDiscovery())
    assert c.get("/api/discovery/plugins").status_code == 200
    assert c.post("/api/discovery/runs", json=_run_body()).status_code == 401
    assert c.post("/api/discovery/runs", json=_run_body(),
                  headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.post("/api/discovery/runs", json=_run_body(),
               headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 202, r.text
    assert c.get(f"/api/discovery/runs/{r.json()['id']}").status_code == 200


def test_an_unknown_run_is_404(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    assert c.get("/api/discovery/runs/disc-nope").status_code == 404


# ---------------------------------------------------------------------------
# the run record
# ---------------------------------------------------------------------------
def test_a_completed_run_reports_its_stages_and_pit(tmp_path):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    r = c.post("/api/discovery/runs", json=_run_body(
        config={"source": "/captures/one", "window": 600, "vantage": "core-tap"},
        sensors=["XPress-SW-A"]))
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "completed"  # synchronous background
    assert run["stages"] == ["ingest", "bake", "cast", "install"]
    assert run["stages_done"] == run["stages"]
    assert run["pit_id"] == "pit-fake" and run["error"] is None
    assert run["created_at"].endswith("Z") and run["finished_at"]
    assert c.get(f"/api/discovery/runs/{run['id']}").json() == run

    (req,) = be.requests
    assert req.plugin == "zeek" and req.imprint_id == "site-a"
    assert req.config == {"source": "/captures/one", "window": 600, "vantage": "core-tap"}
    assert req.sensors == ["XPress-SW-A"]
    assert req.ir_store == Path(settings.aximprint_store)
    assert req.belief_store == Path(settings.aximprint_store).parent / "site-a.belief.duckdb"
    assert req.catalog_root == Path(settings.imprints_dir)
    assert not req.scratch_dir.exists(), "scratch is removed after the run"


def test_a_failed_stage_lands_on_the_record_and_the_partial_bundle_is_removed(tmp_path):
    settings = _settings(tmp_path)

    def _fails(request, on_stage):
        on_stage("ingest")
        on_stage("bake")
        on_stage("cast")
        half = request.catalog_root / request.imprint_id
        half.mkdir()
        (half / f"{request.imprint_id}.yaml").write_text("twin_config: {}\n")
        raise disc.DiscoveryError("install-cast failed (exit 1): 1 assignment(s) reference a persona")

    c = _client(settings, backend=_FakeDiscovery(behaviour=_fails))
    r = c.post("/api/discovery/runs", json=_run_body())
    assert r.status_code == 202
    run = r.json()
    assert run["status"] == "failed"
    assert run["stages_done"] == ["ingest", "bake", "cast"]
    assert "install-cast failed" in run["error"]
    assert run["pit_id"] is None
    assert _catalog_untouched(settings), "the half-written bundle is gone"
    # The id is free again.
    assert c.post("/api/discovery/runs", json=_run_body()).status_code == 202


def test_an_unexpected_exception_in_the_chain_still_fails_the_run(tmp_path):
    def _boom(request, on_stage):
        raise RuntimeError("duckdb went away")

    c = _client(_settings(tmp_path), backend=_FakeDiscovery(behaviour=_boom))
    run = c.post("/api/discovery/runs", json=_run_body()).json()
    assert run["status"] == "failed"
    assert "duckdb went away" in run["error"]


# ---------------------------------------------------------------------------
# the subprocess backend's argv, pinned
# ---------------------------------------------------------------------------
def _request(tmp_path, **config):
    return disc.DiscoveryRunRequest(
        plugin="zeek", config={"source": "/cap", **config}, imprint_id="site-a",
        belief_store=tmp_path / "site-a.belief.duckdb", ir_store=tmp_path / "ir.duckdb",
        scratch_dir=tmp_path / "scratch", catalog_root=tmp_path / "imprints",
        sensors=["S1", "S2"],
    )


def test_the_argv_of_each_stage(tmp_path):
    be = disc.SubprocessDiscovery("/opt/aximprint")
    r = _request(tmp_path, ts_from=1.5, ts_to=9.0, vantage="tap0", network="corp",
                 window=600, subbuckets=6)
    assert be.build_ingest_argv(r) == [
        "/opt/aximprint", "ingest", "--plugin", "zeek", "--source", "/cap",
        "--store", str(tmp_path / "site-a.belief.duckdb"),
        "--from", "1.5", "--to", "9.0", "--vantage", "tap0", "--network", "corp",
        "--window", "600", "--subbuckets", "6",
    ]
    assert be.build_ingest_argv(_request(tmp_path)) == [
        "/opt/aximprint", "ingest", "--plugin", "zeek", "--source", "/cap",
        "--store", str(tmp_path / "site-a.belief.duckdb"),
    ]
    assert be.build_bake_argv(r) == [
        "/opt/aximprint", "bake", "--store", str(tmp_path / "site-a.belief.duckdb"),
        "--out", str(tmp_path / "ir.duckdb"), "--replace",
    ]
    assert be.build_cast_argv(r, "pit-1") == [
        "/opt/aximprint", "cast", "--store", str(tmp_path / "ir.duckdb"), "--pit", "pit-1",
        "--out", str(tmp_path / "scratch"), "--imprint-id", "site-a",
    ]
    assert be.build_install_argv(r) == [
        "/opt/aximprint", "install-cast", str(tmp_path / "scratch"),
        str(tmp_path / "imprints"), "site-a", "--sensors", "S1", "S2",
    ]


def test_check_config_names_every_problem_at_once():
    schema = {"type": "object", "properties": {"source": {"type": "string"}, "n": {"type": "integer"}},
              "required": ["source"], "additionalProperties": False}
    assert disc.check_config(schema, {"source": "/x", "n": 3}) == []
    assert disc.check_config(schema, "nope") == ["config must be an object"]
    problems = disc.check_config(schema, {"n": 1.5, "extra": 1})
    assert problems == [
        "missing required key 'source'", "unknown key 'extra' (allowed: n, source)",
        "'n' must be integer, got float",
    ]
    # An open schema accepts extra keys.
    assert disc.check_config({"type": "object", "properties": {}}, {"anything": 1}) == []


# ---------------------------------------------------------------------------
# A1.2 -- the real chain, on a synthetic Zeek capture
# ---------------------------------------------------------------------------
_CONN_HEADER = "\n".join([
    "#separator \\x09",
    "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
    "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts",
])


def _write_zeek_capture(out: Path) -> None:
    """Four hours of a small office: two workstations, a domain controller
    and a file server, every roled host originating traffic (a role with no
    outbound flows gets no synthesized persona, and the installer refuses a
    profile that names one)."""
    out.mkdir(parents=True, exist_ok=True)
    t0 = 1_754_000_000
    rows: list[str] = []

    def flow(ts, src, sp, dst, dp, svc, dur, ob, rb, st="SF", op=5, rp=5):
        rows.append(f"{ts}\t{src}\t{sp}\t{dst}\t{dp}\ttcp\t{svc}\t{dur}\t{ob}\t{rb}\t{st}\t{op}\t{rp}")

    for i in range(24):
        ts = t0 + i * 600
        flow(ts, "10.0.0.21", 51000 + i, "10.0.0.5", 88, "krb", 0.2, 500, 1500)
        flow(ts + 30, "10.0.0.22", 52000 + i, "10.0.0.5", 88, "krb", 0.2, 500, 1500)
        flow(ts + 60, "10.0.0.21", 53000 + i, "10.0.0.7", 445, "smb", 3.0, 20000, 900000, op=40, rp=700)
        flow(ts + 90, "10.0.0.22", 54000 + i, "10.0.0.7", 445, "smb", 2.0, 10000, 400000, op=20, rp=300)
        flow(ts + 120, "10.0.0.7", 55000 + i, "10.0.0.5", 88, "krb", 0.2, 500, 1500)
        flow(ts + 150, "10.0.0.7", 56000 + i, "10.0.0.5", 389, "ldap", 0.5, 800, 2500)
        flow(ts + 180, "10.0.0.5", 57000 + i, "10.0.0.7", 445, "smb", 1.0, 3000, 50000)
        flow(ts + 210, "10.0.0.21", 58000 + i, "93.184.216.34", 443, "ssl", 5.0, 4000, 120000)
    rows.sort(key=lambda line: float(line.split("\t", 1)[0]))
    (out / "conn.log").write_text(_CONN_HEADER + "\n" + "\n".join(rows) + "\n")
    dhcp = [
        {"ts": t0 + 10, "mac": "aa:bb:cc:dd:ee:01", "assigned_addr": "10.0.0.21",
         "host_name": "ws01", "lease_time": 86400},
        {"ts": t0 + 20, "mac": "aa:bb:cc:dd:ee:02", "assigned_addr": "10.0.0.22",
         "host_name": "ws02", "lease_time": 86400},
    ]
    (out / "dhcp.log").write_text("".join(json.dumps(d) + "\n" for d in dhcp))


def _real_aximprint() -> Path | None:
    # NOT `.resolve()`d: the venv's python is a symlink to the system one, and
    # the console script lives beside the LINK.
    cand = Path(sys.executable).parent / "aximprint"
    if cand.is_file():
        return cand
    found = shutil.which("aximprint")
    return Path(found) if found else None


@pytest.mark.skipif(_real_aximprint() is None, reason="aximprint is not installed beside this python")
def test_a_real_discovery_run_lands_an_imprint_the_catalog_serves(tmp_path):
    capture = tmp_path / "zeek"
    _write_zeek_capture(capture)
    settings = _settings(tmp_path, aximprint_bin=str(_real_aximprint()))
    c = _client(settings)  # the real SubprocessDiscovery, run synchronously

    plugins = c.get("/api/discovery/plugins").json()["plugins"]
    assert "zeek" in {p["name"] for p in plugins}

    r = c.post("/api/discovery/runs", json={
        "plugin": "zeek", "config": {"source": str(capture)}, "imprint_id": "office-a",
        "sensors": ["XPress-SW-A"],
    })
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "completed", run
    assert run["stages_done"] == ["ingest", "bake", "cast", "install"]
    assert run["pit_id"].startswith("pit-")

    # The imprint resolves through the catalog routes, with real numbers.
    detail = c.get("/api/imprints/office-a")
    assert detail.status_code == 200, detail.text
    assert detail.json()["hosts"] > 0 and detail.json()["segments"] >= 1
    listed = c.get("/api/imprints").json()
    assert [i["id"] for i in listed["imprints"]] == ["office-a"] and listed["skipped"] == []
    assert c.get("/api/imprints/office-a/cast").status_code == 200

    # Where it all landed: the bundle in the catalog, the belief store beside
    # the IR store, and the installer filled the sensors in.
    bundle = Path(settings.imprints_dir) / "office-a"
    assert (bundle / "office-a.yaml").is_file()
    assert (bundle / "office-a.provenance.json").is_file()
    assert (bundle / "office-a.topology.json").is_file()
    assert "XPress-SW-A" in (bundle / "tap-cast.yaml").read_text()
    assert (Path(settings.aximprint_store).parent / "office-a.belief.duckdb").is_file()
    assert Path(settings.aximprint_store).is_file()

    # A second run under the same id is refused; the bundle is untouched.
    before = (bundle / "office-a.yaml").read_bytes()
    r = c.post("/api/discovery/runs", json={
        "plugin": "zeek", "config": {"source": str(capture)}, "imprint_id": "office-a"})
    assert r.status_code == 409
    assert (bundle / "office-a.yaml").read_bytes() == before
    # ...and re-discovering the SAME capture under another id is not blocked
    # by the IR store's duplicate-PIT guard.
    r = c.post("/api/discovery/runs", json={
        "plugin": "zeek", "config": {"source": str(capture)}, "imprint_id": "office-b"})
    assert r.status_code == 202 and r.json()["status"] == "completed", r.text


# ---------------------------------------------------------------------------
# A1.4 -- progress and cancel
# ---------------------------------------------------------------------------
def test_a_window_line_is_the_point_the_ingest_has_reached():
    assert disc.progress_from_line("window 1790452800: 240 entities, 900 edges", 3600) == 1790456400.0
    assert disc.progress_from_line("window 100.0: x", 60) == 160.0
    assert disc.progress_from_line("store sink complete: 3 window(s)", 3600) is None
    assert disc.progress_from_line("", 3600) is None


_SLOW_CHILD = (
    "import sys, time\n"
    "for i in range(200):\n"
    "    print(f'window {i * 60}: 1 entities', flush=True)\n"
    "    time.sleep(0.05)\n"
)


def test_a_stage_streams_its_stdout_and_a_cancel_kills_it_mid_way():
    """The real mechanism: a child in its own session, read line by line, killed as a
    group when the handle is cancelled, reported as `DiscoveryCancelled` not as a
    failure."""
    be = disc.SubprocessDiscovery(sys.executable, timeout_s=30)
    handle = disc.RunHandle()
    seen: list[str] = []

    def on_line(line: str) -> None:
        seen.append(line)
        if len(seen) == 3:
            handle.cancel()

    import time as _t
    t0 = _t.monotonic()
    with pytest.raises(disc.DiscoveryCancelled, match="ingest cancelled"):
        be._run([sys.executable, "-c", _SLOW_CHILD], stage="ingest", on_line=on_line, handle=handle)
    assert _t.monotonic() - t0 < 5, "the cancel killed the child; it did not run to the end"
    assert seen[:3] == ["window 0: 1 entities", "window 60: 1 entities", "window 120: 1 entities"]
    assert len(seen) < 200


def test_a_cancel_that_lands_before_the_stage_starts_kills_it_on_attach():
    be = disc.SubprocessDiscovery(sys.executable, timeout_s=30)
    handle = disc.RunHandle()
    handle.cancel()
    with pytest.raises(disc.DiscoveryCancelled):
        be._run([sys.executable, "-c", _SLOW_CHILD], stage="ingest", handle=handle)


def test_a_stage_past_its_budget_is_killed_and_reported_as_a_timeout():
    be = disc.SubprocessDiscovery(sys.executable, timeout_s=0.5)
    with pytest.raises(disc.DiscoveryError, match="timed out after 0.5s"):
        be._run([sys.executable, "-c", _SLOW_CHILD], stage="ingest")


def test_the_chain_reports_ingest_progress_from_window_lines(tmp_path):
    """`run()` turns the ingest's `window N:` lines into `on_progress(N + window)` and
    passes the handle to every stage; the other stages are stubbed by argv."""
    be = disc.SubprocessDiscovery(sys.executable, timeout_s=30)
    r = _request(tmp_path, window=600)
    r.scratch_dir.mkdir(parents=True, exist_ok=True)
    say = "import sys; print(sys.argv[-1])"
    be.build_ingest_argv = lambda rq: [sys.executable, "-c", "print('window 1200: 1 entities'); print('window 1800: 2 entities'); print('store sink complete')"]  # type: ignore[method-assign]
    be.build_bake_argv = lambda rq: [sys.executable, "-c", say, '{"pit_id": "pit-x"}']  # type: ignore[method-assign]
    be.build_cast_argv = lambda rq, pit: [sys.executable, "-c", say, "cast"]  # type: ignore[method-assign]
    be.build_install_argv = lambda rq: [sys.executable, "-c", say, "install"]  # type: ignore[method-assign]
    progress: list[float] = []
    stages: list[str] = []
    result = be.run(r, on_stage=stages.append, on_progress=progress.append, handle=disc.RunHandle())
    assert progress == [1800.0, 2400.0]
    assert stages == ["ingest", "bake", "cast", "install"]
    assert result.pit_id == "pit-x"


def test_cancel_over_the_api_stops_a_queued_run_and_clears_what_it_wrote(tmp_path):
    """A run held in the queue (the job is captured, not run) is cancelled over the API:
    202 with `cancel_requested_at`, and when the job then runs its first stage sees the
    handle already cancelled, so the record turns `cancelled`, the half bundle and the
    belief store are gone, and the imprint id is free again."""
    settings = _settings(tmp_path)
    jobs = []

    def _behaviour(request, on_stage, on_progress=None, handle=None):
        on_progress(1790456400.0)
        half = request.catalog_root / request.imprint_id
        half.mkdir()
        request.belief_store.write_bytes(b"partial")
        if handle is not None and handle.cancelled:
            raise disc.DiscoveryCancelled("ingest cancelled by the operator")
        on_stage("ingest")
        return disc.DiscoveryRunResult(pit_id="pit-fake", stages=["ingest"])

    be = _FakeDiscovery()
    be.run = lambda request, *, on_stage, on_progress=None, handle=None: _behaviour(  # type: ignore[method-assign]
        request, on_stage, on_progress, handle)
    c = _client(settings, backend=be, background=jobs.append)
    run = c.post("/api/discovery/runs", json=_run_body()).json()
    assert run["status"] == "queued" and run["progress_ts"] is None

    r = c.post(f"/api/discovery/runs/{run['id']}/cancel")
    assert r.status_code == 202, r.text
    assert r.json()["cancel_requested_at"] is not None
    assert r.json()["status"] == "queued", "the status turns only when the stage is dead"

    (job,) = jobs
    job()
    done = c.get(f"/api/discovery/runs/{run['id']}").json()
    assert done["status"] == "cancelled"
    assert done["progress_ts"] == 1790456400.0 and done["progress_at"] is not None
    assert "cancelled by the operator" in done["error"]
    assert _catalog_untouched(settings)
    assert not (Path(settings.aximprint_store).parent / "site-a.belief.duckdb").exists()
    # The id is free again, and a second cancel of a finished run is a 409 naming its state.
    assert c.post(f"/api/discovery/runs/{run['id']}/cancel").status_code == 409
    assert "already cancelled" in c.post(f"/api/discovery/runs/{run['id']}/cancel").json()["error"]
    assert c.post("/api/discovery/runs/disc-nope/cancel").status_code == 404


def test_cancel_is_a_write(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, backend=_FakeDiscovery())
    run = c.post("/api/discovery/runs", json=_run_body(), headers={"Authorization": "Bearer s3cret"}).json()
    assert c.post(f"/api/discovery/runs/{run['id']}/cancel").status_code == 401
    assert c.post(f"/api/discovery/runs/{run['id']}/cancel",
                  headers={"Authorization": "Bearer s3cret"}).status_code == 409


def test_a_finished_run_can_be_deleted_from_the_list_but_an_active_one_cannot(tmp_path):
    """The console's runs table offers a delete on a finished run and a cancel on an active
    one; the API holds the same line. Deleting forgets the record only -- the imprint it
    installed is still in the catalog."""
    settings = _settings(tmp_path)
    jobs = []
    c = _client(settings, backend=_FakeDiscovery(), background=jobs.append)
    run = c.post("/api/discovery/runs", json=_run_body()).json()
    r = c.delete(f"/api/discovery/runs/{run['id']}")
    assert r.status_code == 409 and "cancel it first" in r.json()["error"]
    (job,) = jobs
    job()
    assert c.get(f"/api/discovery/runs/{run['id']}").json()["status"] == "completed"
    # The fake backend installs nothing; stand the bundle in by hand to show the delete
    # forgets the RECORD and leaves the catalog alone.
    bundle = Path(settings.imprints_dir) / "site-a"
    bundle.mkdir()
    assert c.delete(f"/api/discovery/runs/{run['id']}").status_code == 204
    assert c.get(f"/api/discovery/runs/{run['id']}").status_code == 404
    assert c.get("/api/discovery/runs").json()["runs"] == []
    assert bundle.is_dir(), "the bundle is not the record"
    assert c.delete("/api/discovery/runs/disc-nope").status_code == 404


def test_delete_is_a_write(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, backend=_FakeDiscovery())
    run = c.post("/api/discovery/runs", json=_run_body(), headers={"Authorization": "Bearer s3cret"}).json()
    assert c.delete(f"/api/discovery/runs/{run['id']}").status_code == 401
    assert c.delete(f"/api/discovery/runs/{run['id']}",
                    headers={"Authorization": "Bearer s3cret"}).status_code == 204


def test_a_stale_belief_store_from_an_earlier_failed_run_is_removed_before_ingest(tmp_path):
    settings = _settings(tmp_path)
    stale = Path(settings.aximprint_store).parent / "site-a.belief.duckdb"
    stale.write_bytes(b"left by a failed run")
    Path(f"{stale}.wal").write_bytes(b"wal")
    seen = {}

    def _behaviour(request, on_stage, **kw):
        seen["present_at_ingest"] = request.belief_store.exists()
        for s in disc.STAGES:
            on_stage(s)
        return disc.DiscoveryRunResult(pit_id="pit-fake", stages=list(disc.STAGES))

    be = _FakeDiscovery()
    be.run = lambda request, *, on_stage, on_progress=None, handle=None: _behaviour(request, on_stage)  # type: ignore[method-assign]
    c = _client(settings, backend=be)
    assert c.post("/api/discovery/runs", json=_run_body()).json()["status"] == "completed"
    assert seen["present_at_ingest"] is False
    assert not Path(f"{stale}.wal").exists()


# -- the run table survives a restart ----------------------------------------------------


def test_run_records_survive_a_restart_and_an_active_one_is_marked_failed(tmp_path):
    """Run records used to live only in the process: a restart emptied the list and the
    Imprints page lost which run produced which bundle. They are now written through to
    the discovery DB. A run the old process left active cannot finish -- its thread died
    with it -- so the next open marks it failed and says why, instead of showing a run
    that is `running` forever with nothing driving it."""
    settings = _settings(tmp_path)
    db = Path(settings.discovery_db)
    jobs = []
    c = _client(settings, backend=_FakeDiscovery(), background=jobs.append)
    done = c.post("/api/discovery/runs", json=_run_body(imprint_id="done-a")).json()
    jobs.pop()()
    stuck = c.post("/api/discovery/runs", json=_run_body(imprint_id="stuck-b")).json()
    assert c.get(f"/api/discovery/runs/{done['id']}").json()["status"] == "completed"
    assert c.get(f"/api/discovery/runs/{stuck['id']}").json()["status"] == "queued"
    assert db.is_file() and oct(db.stat().st_mode & 0o777) == "0o600"

    # The "restart": a second app over the same settings.
    c2 = _client(settings, backend=_FakeDiscovery())
    runs = {r["id"]: r for r in c2.get("/api/discovery/runs").json()["runs"]}
    assert set(runs) == {done["id"], stuck["id"]}
    assert runs[done["id"]]["status"] == "completed"
    assert runs[done["id"]]["pit_id"] == "pit-fake"
    assert runs[done["id"]]["stages_done"] == list(disc.STAGES)
    assert runs[stuck["id"]]["status"] == "failed"
    assert "restarted while this run was queued" in runs[stuck["id"]]["error"]
    assert runs[stuck["id"]]["finished_at"] is not None
    # Deleting through the new process removes the row for good.
    assert c2.delete(f"/api/discovery/runs/{stuck['id']}").status_code == 204
    c3 = _client(settings, backend=_FakeDiscovery())
    assert [r["id"] for r in c3.get("/api/discovery/runs").json()["runs"]] == [done["id"]]


def test_a_run_record_carries_no_secret_into_the_db(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    c.post("/api/discovery/runs", json=_run_body())
    import sqlite3
    (doc,) = sqlite3.connect(settings.discovery_db).execute("SELECT record_json FROM runs").fetchone()
    assert "password" not in doc.lower() and "api_key" not in doc.lower()
    assert '"env"' not in doc


# -- deleting an imprint -------------------------------------------------------------------


def test_an_imprint_can_be_deleted_unless_a_run_or_a_deploy_still_uses_it(tmp_path):
    """The Imprints page's bin. The bundle directory goes, and so does the belief store a
    run under that id left beside the IR store; the run record stays. Refused while a run
    for the id is active (it is writing there) and while a live deploy stands on it
    (its destroy resolves the config by this id)."""
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    auth = {"Authorization": "Bearer s3cret"}
    jobs = []
    c = _client(settings, backend=_FakeDiscovery(), background=jobs.append)
    run = c.post("/api/discovery/runs", json=_run_body(imprint_id="site-a"), headers=auth).json()
    # Still queued: the id is claimed and the bundle not even there yet -- refused,
    # pointing at cancel, before any directory check could say 404.
    r = c.delete("/api/imprints/site-a", headers=auth)
    assert r.status_code == 409 and "cancel it first" in r.json()["error"], r.text
    jobs.pop()()
    bundle = Path(settings.imprints_dir) / "site-a"
    bundle.mkdir()
    (bundle / "site-a.yaml").write_text(
        "twin_config:\n  id: site-a\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    stale = Path(settings.aximprint_store).parent / "site-a.belief.duckdb"
    stale.write_bytes(b"left over")
    assert c.get("/api/imprints/site-a").status_code == 200

    # A live deploy on the id: refused, naming it.
    from axrange.planner import StateStore
    store = StateStore(settings.db_path)
    try:
        store.create_deploy(deploy_id="dep-1", scenario_id="site-a", scenario_version=None,
                            scenario_schema_version=None, plan_id="plan-1", ir_hash="h",
                            placement={})
    finally:
        store.close()
    r = c.delete("/api/imprints/site-a", headers=auth)
    assert r.status_code == 409 and "dep-1" in r.json()["error"], r.text
    store = StateStore(settings.db_path)
    try:
        store.mark_destroyed("dep-1")
    finally:
        store.close()

    assert c.delete("/api/imprints/site-a").status_code == 401, "a write"
    assert c.delete("/api/imprints/site-a", headers=auth).status_code == 204
    assert not bundle.exists() and not stale.exists()
    assert c.get("/api/imprints/site-a").status_code == 404
    assert c.get(f"/api/discovery/runs/{run['id']}").json()["status"] == "completed", \
        "the record outlives the bundle"
    assert c.delete("/api/imprints/site-a", headers=auth).status_code == 404
    assert c.delete("/api/imprints/../etc", headers=auth).status_code in (400, 404)


# -- PITs: listed by id and name, renamed by either ------------------------------------------


def test_a_pit_is_listed_with_its_name_and_a_rename_follows_onto_the_runs(tmp_path):
    """A PIT's id is its identity and never moves; its name is a label the operator may
    change. The run that produced it carries both, and a rename reaches the record."""
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    auth = {"Authorization": "Bearer s3cret"}
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    run = c.post("/api/discovery/runs", json=_run_body(), headers=auth).json()
    assert (run["pit_id"], run["pit_name"]) == ("pit-fake", "pit-fake")
    assert c.get("/api/discovery/pits").json() == {"pits": [
        {"pit_id": "pit-fake", "name": "pit-fake", "as_of": 100.0, "observed_from": None,
         "observed_to": None, "source": None, "network": None, "vantage": None, "windows": None,
         "entities": None, "segments": None, "merge_axis": None}]}

    assert c.patch("/api/discovery/pits/pit-fake", json={"name": "day one"}).status_code == 401
    r = c.patch("/api/discovery/pits/pit-fake", json={"name": "day one"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "day one" and r.json()["pit_id"] == "pit-fake"
    got = c.get(f"/api/discovery/runs/{run['id']}").json()
    assert (got["pit_id"], got["pit_name"]) == ("pit-fake", "day one")
    # By name too; a blank name and an unknown PIT are refused with the right codes.
    assert c.patch("/api/discovery/pits/day%20one", json={"name": "day two"}, headers=auth).json()["name"] == "day two"
    assert c.patch("/api/discovery/pits/pit-fake", json={"name": "  "}, headers=auth).status_code == 400
    assert c.patch("/api/discovery/pits/pit-nope", json={"name": "x"}, headers=auth).status_code == 404
    be.pits["pit-2"] = "taken"
    assert c.patch("/api/discovery/pits/pit-fake", json={"name": "taken"}, headers=auth).status_code == 409
    # The name survives a restart with the record.
    c2 = _client(settings, backend=_FakeDiscovery())
    assert c2.get(f"/api/discovery/runs/{run['id']}").json()["pit_name"] == "day two"
