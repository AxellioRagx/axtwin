"""Discovery sources (`api/Spec.md` A7): `/api/discovery/sources[...]`, the probe, and a
source-driven run.

Everything runs against a fake backend and a fake tunnel: the suite never runs
`aximprint` or `ssh`. The store is a temp SQLite file, so the one thing that IS real
here is persistence: a saved source survives a second `create_app` over the same path,
and a secret never appears in any read.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api import discovery as disc
from axrange.api import discovery_sources as ds

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette not installed", allow_module_level=True)

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"

HTTP_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "description": "an axquery instance"},
        "ts_from": {"type": "number"}, "ts_to": {"type": "number"},
        "vantage": {"type": "string"}, "network": {"type": "string"},
        "window": {"type": "integer"}, "subbuckets": {"type": "integer"},
    },
    "required": ["source"],
    "additionalProperties": False,
}
PATH_SCHEMA = {**HTTP_SCHEMA, "properties": {"source": {"type": "string"}}}

SPAN = {"first_ts": 1788723036.0, "last_ts": 1791315036.0}


def _settings(tmp_path: Path, **over) -> ApiSettings:
    (tmp_path / "imprints").mkdir(exist_ok=True)
    (tmp_path / "disco").mkdir(exist_ok=True)
    kw = dict(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(FIXT_IR.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config="/nonexistent.conf",
        aximprint_bin="/usr/bin/false-aximprint",
        aximprint_store=str(tmp_path / "disco" / "ir.duckdb"),
        imprints_dir=str(tmp_path / "imprints"),
        discovery_db=str(tmp_path / "axrange-home" / "discovery.db"),
    )
    kw.update(over)
    return ApiSettings(**kw)


class _FakeDiscovery:
    def __init__(self, *, probe=None, run=None):
        self.plugins = [
            {"name": "axquery", "source_kind": "http", "config_schema": HTTP_SCHEMA},
            {"name": "zeek", "source_kind": "path", "config_schema": PATH_SCHEMA},
        ]
        self.probes: list[tuple[str, str, dict]] = []
        self.requests: list[disc.DiscoveryRunRequest] = []
        self._probe = probe
        self._run = run

    def list_plugins(self):
        return list(self.plugins)

    def probe(self, plugin, source, *, env):
        self.probes.append((plugin, source, dict(env)))
        if self._probe is not None:
            return self._probe(plugin, source, env)
        return {"supported": True, "reachable": True, "error": None, **SPAN,
                "vantage": "axellio-hq/corp-apcn-rdr1:12", "extra": {"chunks": 69}}

    def run(self, request, *, on_stage, on_progress=None, handle=None):
        self.requests.append(request)
        if self._run is not None:
            return self._run(request, on_stage)
        for s in disc.STAGES:
            on_stage(s)
        return disc.DiscoveryRunResult(pit_id="pit-fake", stages=list(disc.STAGES))


class _FakeTunnel:
    """Forwards nowhere; records that it was opened and closed, in order."""

    log: list[str] = []

    def __init__(self, ssh, target):
        self.ssh, self.target = ssh, target
        self.local_port = 45678

    def __enter__(self):
        _FakeTunnel.log.append(f"open {self.ssh.user}@{self.ssh.host}:{self.ssh.port} -> {self.target}")
        return self

    def __exit__(self, *exc):
        _FakeTunnel.log.append("close")


def _client(settings, *, backend=None, background="sync", tunnel=_FakeTunnel):
    app = create_app(settings)
    app.state.discovery_backend = backend
    app.state.discovery_tunnel = tunnel
    if background == "sync":
        app.state.discovery_background = lambda job: job()
    elif background is not None:
        app.state.discovery_background = background
    return TestClient(app, raise_server_exceptions=False)


def _tunnel_source(**over):
    body = {
        "name": "corp-apcn-reader", "plugin": "axquery", "endpoint": "http://127.0.0.1:9090/",
        "access": {"transport": "ssh_tunnel", "auth": "none",
                   "ssh": {"host": "reader.example", "user": "fxadmin", "key_path": "~/.ssh/id_ed25519"}},
        "defaults": {"days": 10, "network": "corp"},
    }
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def _clear_tunnel_log():
    _FakeTunnel.log = []
    yield


# ---------------------------------------------------------------------------
# A7.1 -- create / read / update / delete, no secret ever read back
# ---------------------------------------------------------------------------
def test_create_lists_and_reads_back_without_the_secret(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="s3cret",
        endpoint="https://sensor.example:9443"))
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["id"].startswith("src-") and doc["name"] == "corp-apcn-reader"
    assert doc["endpoint"] == "https://sensor.example:9443"  # trailing slash trimmed
    assert doc["access"] == {"transport": "direct", "auth": "basic", "user": "ro", "ssh": None}
    assert doc["has_password"] is True and doc["has_api_key"] is False
    assert "s3cret" not in r.text
    listed = c.get("/api/discovery/sources").json()["sources"]
    assert [s["id"] for s in listed] == [doc["id"]]
    assert "s3cret" not in json.dumps(listed)
    assert c.get(f"/api/discovery/sources/{doc['id']}").json() == doc


def test_the_store_is_a_0600_file_outside_the_checkout_and_persists(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    path = Path(settings.discovery_db)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # A second app over the same file sees the record.
    c2 = _client(settings, backend=_FakeDiscovery())
    assert c2.get(f"/api/discovery/sources/{sid}").status_code == 200


def test_the_secret_is_in_the_file_but_only_for_the_runner(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "api_key"}, api_key="K-1")).json()["id"]
    store = ds.SourceStore(settings.discovery_db)
    assert store.secrets(sid) == {"api_key": "K-1"}
    assert store.get(sid).has_api_key is True


@pytest.mark.parametrize("over, needle", [
    ({"plugin": "nope"}, "unknown discovery plugin"),
    ({"plugin": "zeek"}, "reads a path, not an endpoint"),
    ({"endpoint": "10.0.0.1:9090"}, "absolute http(s) base URL"),
    ({"access": {"transport": "carrier-pigeon"}}, "access.transport must be one of"),
    ({"access": {"transport": "direct", "auth": "basic"}}, "access.user (required for basic auth)"),
    ({"access": {"transport": "direct", "auth": "basic", "user": "ro"}}, "basic auth needs a password"),
    ({"access": {"transport": "direct", "auth": "api_key"}}, "api_key auth needs an api_key"),
    ({"access": {"transport": "ssh_tunnel", "auth": "none"}}, "access.ssh {host, user, key_path, port?} is required"),
    ({"access": {"transport": "ssh_tunnel", "auth": "none",
                 "ssh": {"host": "h", "user": "u", "key_path": "k", "port": 70000}}}, "access.ssh.port must be an integer port"),
    ({"defaults": {"days": -1}}, "defaults.days must be a positive number"),
    ({"defaults": {"colour": "blue"}}, "unknown defaults key 'colour'"),
])
def test_a_malformed_source_is_refused_by_name_before_anything_is_written(tmp_path, over, needle):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post("/api/discovery/sources", json=_tunnel_source(**over))
    assert r.status_code == 400, r.text
    assert needle in r.text
    assert c.get("/api/discovery/sources").json() == {"sources": []}


def test_a_duplicate_name_is_a_409(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources", json=_tunnel_source()).status_code == 201
    r = c.post("/api/discovery/sources", json=_tunnel_source())
    assert r.status_code == 409 and "already exists" in r.text


def test_update_keeps_an_unsent_secret_clears_an_empty_one_and_drops_the_unused_one(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="pw1")).json()["id"]
    store = ds.SourceStore(settings.discovery_db)
    # Rename only: the password stays.
    r = c.put(f"/api/discovery/sources/{sid}", json={"name": "renamed"})
    assert r.status_code == 200 and r.json()["name"] == "renamed" and r.json()["has_password"]
    assert store.secrets(sid) == {"password": "pw1"}
    # Switch to api_key: the password is dropped, the key kept.
    r = c.put(f"/api/discovery/sources/{sid}", json={
        "access": {"transport": "direct", "auth": "api_key"}, "api_key": "K"})
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is False and r.json()["has_api_key"] is True
    assert store.secrets(sid) == {"api_key": "K"}
    # Clearing the key under api_key auth is refused: the mode needs one.
    r = c.put(f"/api/discovery/sources/{sid}", json={"api_key": ""})
    assert r.status_code == 400 and "api_key auth needs an api_key" in r.text
    # Switching to none and clearing is fine.
    r = c.put(f"/api/discovery/sources/{sid}", json={
        "access": {"transport": "direct", "auth": "none"}, "api_key": ""})
    assert r.status_code == 200 and r.json()["has_api_key"] is False
    assert store.secrets(sid) == {}


def test_delete_removes_and_404s_after(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 204
    assert c.get(f"/api/discovery/sources/{sid}").status_code == 404
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 404
    assert c.put(f"/api/discovery/sources/{sid}", json={"name": "x"}).status_code == 404


def test_writes_carry_the_token_reads_do_not(tmp_path):
    c = _client(_settings(tmp_path, api_token="T"), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources", json=_tunnel_source()).status_code == 401
    assert c.get("/api/discovery/sources").status_code == 200
    h = {"Authorization": "Bearer T"}
    sid = c.post("/api/discovery/sources", json=_tunnel_source(), headers=h).json()["id"]
    assert c.post(f"/api/discovery/sources/{sid}/probe").status_code == 401
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x"}).status_code == 401
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 401


def test_no_sources_is_an_empty_list_and_no_file_is_created_by_a_read(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    assert c.get("/api/discovery/sources").json() == {"sources": []}
    # The store file now exists (opened on first use) but holds nothing.
    assert sqlite3.connect(settings.discovery_db).execute("SELECT count(*) FROM sources").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# A7.2 -- the probe
# ---------------------------------------------------------------------------
def test_probe_opens_the_tunnel_rewrites_the_endpoint_and_remembers_the_answer(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["reachable"] is True and doc["error"] is None
    assert (doc["first_ts"], doc["last_ts"]) == (SPAN["first_ts"], SPAN["last_ts"])
    assert doc["vantage"] == "axellio-hq/corp-apcn-rdr1:12" and doc["extra"] == {"chunks": 69}
    assert doc["via"] == "ssh fxadmin@reader.example:22 -> 127.0.0.1:9090 via 127.0.0.1:45678"
    # The plugin was asked through the tunnel's local end, with no credential env.
    assert be.probes == [("axquery", "axquery:http://127.0.0.1:45678", {})]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]
    # Remembered on the record.
    rec = c.get(f"/api/discovery/sources/{sid}").json()
    assert rec["last_probe"]["reachable"] is True and rec["last_probe"]["probed_at"] == doc["probed_at"]


def test_probe_direct_with_basic_auth_passes_the_credential_in_env_only(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        endpoint="https://sensor.example:9443",
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="pw")).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200 and r.json()["via"] == "direct"
    assert be.probes == [("axquery", "axquery:https://sensor.example:9443",
                          {ds.ENV_USER: "ro", ds.ENV_PASSWORD: "pw"})]
    assert "pw" not in r.text and _FakeTunnel.log == []


def test_probe_reports_a_dead_tunnel_as_unreachable_with_sshs_reason(tmp_path):
    class _Dead(_FakeTunnel):
        def __enter__(self):
            raise ds.AccessError("ssh tunnel to fxadmin@reader.example:22 exited (255): Permission denied")

    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be, tunnel=_Dead)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200
    assert r.json()["reachable"] is False and "Permission denied" in r.json()["error"]
    assert be.probes == []


def test_probe_passes_the_plugins_own_unreachable_answer_through(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: {
        "supported": True, "reachable": False, "error": "axquery unreachable at http://127.0.0.1:45678: refused",
        "first_ts": None, "last_ts": None, "vantage": None, "extra": {}})
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    doc = c.post(f"/api/discovery/sources/{sid}/probe").json()
    assert doc["reachable"] is False and "refused" in doc["error"]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]


def test_probe_of_an_unknown_source_is_404_and_unconfigured_discovery_is_503(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources/src-nope/probe").status_code == 404
    c2 = _client(_settings(tmp_path, aximprint_bin=""), backend=None)
    # The store is real; the backend is the real (unconfigured) one -> 503 naming the key.
    sid = None
    r = c2.get("/api/discovery/sources")
    assert r.status_code == 200
    r = c2.post("/api/discovery/sources", json=_tunnel_source())
    assert r.status_code == 503 and disc.SETTING_BIN in r.text
    assert sid is None


# ---------------------------------------------------------------------------
# A7.3 -- a source-driven run
# ---------------------------------------------------------------------------
def test_run_last_n_days_resolves_bounds_from_the_span_and_runs_the_chain_over_the_tunnel(tmp_path):
    be = _FakeDiscovery()
    settings = _settings(tmp_path)
    c = _client(settings, backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "corp-10d", "days": 10})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "completed" and run["source_id"] == sid
    assert run["stages_done"] == list(disc.STAGES) and run["pit_id"] == "pit-fake"
    assert run["ts_to"] == SPAN["last_ts"] + 1.0
    assert run["ts_from"] == run["ts_to"] - 10 * 86400
    assert run["via"].startswith("ssh fxadmin@reader.example:22")
    assert run["started_at"] and run["finished_at"]
    # The chain saw the tunnel's local end, the defaults, and the bounds; the tunnel
    # outlived the whole chain.
    req = be.requests[0]
    assert req.config == {"source": "axquery:http://127.0.0.1:45678", "network": "corp",
                          "ts_from": run["ts_from"], "ts_to": run["ts_to"]}
    assert req.env == {} and req.sensors == []
    assert req.belief_store == Path(settings.aximprint_store).parent / "corp-10d.belief.duckdb"
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]
    assert be.probes[0][1] == "axquery:http://127.0.0.1:45678"
    # Listed, newest first, and readable by id.
    assert [x["id"] for x in c.get("/api/discovery/runs").json()["runs"]] == [run["id"]]
    assert c.get(f"/api/discovery/runs/{run['id']}").json()["status"] == "completed"


def test_run_explicit_bounds_skip_the_probe_and_override_the_defaults(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "api_key"}, api_key="K",
        endpoint="http://sensor.example:9090",
        defaults={"days": 10, "network": "corp", "window": 3600, "sensors": ["s1"]})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "imprint_id": "corp-slice", "ts_from": 100.0, "ts_to": 200.0, "network": "lab",
        "sensors": [], "vantage": "tap-1"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "completed" and r.json()["via"] == "direct"
    assert be.probes == []
    req = be.requests[0]
    assert req.config == {"source": "axquery:http://sensor.example:9090", "network": "lab",
                          "vantage": "tap-1", "window": 3600, "ts_from": 100.0, "ts_to": 200.0}
    assert req.env == {ds.ENV_API_KEY: "K"} and req.sensors == []


def test_days_is_clamped_to_the_sources_first_ts(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "all", "days": 365}).json()
    assert run["ts_from"] == SPAN["first_ts"] and run["ts_to"] == SPAN["last_ts"] + 1.0


@pytest.mark.parametrize("body, needle", [
    ({"imprint_id": "x", "days": 5, "ts_from": 1.0, "ts_to": 2.0}, "not both"),
    ({"imprint_id": "x", "ts_from": 5.0, "ts_to": 2.0}, "ts_to > ts_from"),
    ({"imprint_id": "x", "ts_from": 1.0}, "both be set"),
    ({"imprint_id": "../x", "days": 1}, "not a usable catalog id"),
    ({"imprint_id": "x", "days": 1, "sensors": [""]}, "non-empty names"),
])
def test_a_bad_run_request_is_refused_before_the_tunnel_opens(tmp_path, body, needle):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json=body)
    assert r.status_code == 400, r.text
    assert needle in r.text
    assert _FakeTunnel.log == [] and be.requests == []


def test_no_days_anywhere_is_refused(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(defaults={})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x"})
    assert r.status_code == 400 and "no default days" in r.text


def test_an_existing_bundle_or_active_run_is_a_409(tmp_path):
    settings = _settings(tmp_path)
    (Path(settings.imprints_dir) / "taken").mkdir()
    started: list = []
    c = _client(settings, backend=_FakeDiscovery(), background=started.append)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "taken", "days": 1}).status_code == 409
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "busy", "days": 1}).status_code == 202
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "busy", "days": 1}).status_code == 409
    # A source with a run in progress cannot be deleted.
    r = c.delete(f"/api/discovery/sources/{sid}")
    assert r.status_code == 409 and "run in progress" in r.text
    assert len(started) == 1


def test_a_missing_ssh_key_is_refused_at_post_time(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "ssh_tunnel", "auth": "none",
                "ssh": {"host": "h", "user": "u", "key_path": str(tmp_path / "no-such-key")}})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x", "days": 1})
    assert r.status_code == 400 and "is not a file on the server" in r.text


def test_a_dead_tunnel_fails_the_run_on_the_record_and_leaves_no_bundle(tmp_path):
    class _Dead(_FakeTunnel):
        def __enter__(self):
            raise ds.AccessError("ssh tunnel exited (255): Connection timed out")

    be = _FakeDiscovery()
    settings = _settings(tmp_path)
    c = _client(settings, backend=be, tunnel=_Dead)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "dead", "days": 1}).json()
    assert run["status"] == "failed" and "Connection timed out" in run["error"]
    assert be.requests == [] and not (Path(settings.imprints_dir) / "dead").exists()


def test_an_unreachable_probe_fails_the_run_before_ingest(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: {"supported": True, "reachable": False,
                                              "error": "axquery unreachable: refused"})
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x", "days": 1}).json()
    assert run["status"] == "failed" and "source not reachable" in run["error"]
    assert be.requests == [] and _FakeTunnel.log[-1] == "close"


def test_a_failed_stage_closes_the_tunnel_and_discards_the_partial_bundle(tmp_path):
    def boom(request, on_stage):
        on_stage("ingest")
        (request.catalog_root / request.imprint_id).mkdir()
        raise disc.DiscoveryError("materialize failed (exit 1): no events")

    be = _FakeDiscovery(run=boom)
    settings = _settings(tmp_path)
    c = _client(settings, backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "half", "days": 1}).json()
    assert run["status"] == "failed" and run["stages_done"] == ["ingest"]
    assert "materialize failed" in run["error"]
    assert not (Path(settings.imprints_dir) / "half").exists()
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]


# ---------------------------------------------------------------------------
# the module itself: env names, the tunnel argv, the window arithmetic
# ---------------------------------------------------------------------------
def test_env_names_match_the_aximprint_transport():
    # `discovery/src/aximprint/ingest/axquery/sources.py` spells the same three.
    assert (ds.ENV_USER, ds.ENV_PASSWORD, ds.ENV_API_KEY) == (
        "AXIMPRINT_SOURCE_USER", "AXIMPRINT_SOURCE_PASSWORD", "AXIMPRINT_SOURCE_API_KEY")


def test_tunnel_argv_is_batch_mode_with_the_forward_and_never_a_secret():
    t = ds.SshTunnel(ds.SshSpec(host="reader", user="fx", key_path="/k", port=2222), ("127.0.0.1", 9090))
    argv = t.argv(40000)
    assert argv[:2] == ["ssh", "-N"]
    assert "BatchMode=yes" in argv and "ExitOnForwardFailure=yes" in argv
    assert argv[argv.index("-L") + 1] == "127.0.0.1:40000:127.0.0.1:9090"
    assert argv[argv.index("-p") + 1] == "2222" and argv[argv.index("-i") + 1] == "/k"
    assert argv[-1] == "fx@reader"


def test_a_real_tunnel_with_a_missing_key_fails_without_running_ssh(tmp_path):
    t = ds.SshTunnel(ds.SshSpec(host="reader", user="fx", key_path=str(tmp_path / "nokey")), ("127.0.0.1", 1))
    with pytest.raises(ds.AccessError, match="is not a file"):
        t.__enter__()


def test_rewrite_endpoint_keeps_scheme_and_path():
    assert ds.rewrite_endpoint("https://sensor.example:9443/axq", 5) == "https://127.0.0.1:5/axq"
    assert ds.endpoint_target("https://sensor.example/axq") == ("sensor.example", 443)
    assert ds.endpoint_target("http://127.0.0.1:9090") == ("127.0.0.1", 9090)


def test_window_from_probe_needs_a_last_ts():
    with pytest.raises(ds.AccessError, match="no last_ts"):
        ds.window_from_probe({"first_ts": 1.0}, 1)
    assert ds.window_from_probe({"first_ts": 0.0, "last_ts": 99.0}, 1) == (0.0, 100.0)
    assert ds.window_from_probe({"last_ts": 1_000_000.0}, 1) == (1_000_000.0 + 1 - 86400, 1_000_001.0)
