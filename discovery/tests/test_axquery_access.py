"""axquery transport access (`api/Spec.md` A7): the credential reaches the request from
the ENVIRONMENT, never from the locator; `probe` answers reachability and span without
pulling an event."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from aximprint import cli
from aximprint.ingest import get_plugin
from aximprint.ingest.axquery import sources
from aximprint.ingest.axquery.sources import (
    ENV_API_KEY, ENV_PASSWORD, ENV_USER, AxQueryError, AxQuerySource, auth_headers,
)


def test_no_credential_means_no_header():
    assert auth_headers({}) == {}


def test_basic_auth_is_rfc7617_and_wins_over_a_bearer_key():
    h = auth_headers({ENV_USER: "ro", ENV_PASSWORD: "s3cret", ENV_API_KEY: "k"})
    assert h == {"Authorization": "Basic cm86czNjcmV0"}


def test_api_key_is_a_bearer_token():
    assert auth_headers({ENV_API_KEY: "tok"}) == {"Authorization": "Bearer tok"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body: bytes, *, status: int | None = None, seen: list | None = None):
    def fake_urlopen(req, timeout=None):
        if seen is not None:
            seen.append(req)
        if status is not None:
            raise urllib.error.HTTPError(req.full_url, status, "nope", {}, None)
        return _Resp(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_get_sends_the_environment_credential(monkeypatch):
    seen: list = []
    monkeypatch.setenv(ENV_API_KEY, "tok")
    _serve(monkeypatch, b'{"ok": true}', seen=seen)
    AxQuerySource("axquery:http://127.0.0.1:1/").get("/api/info")
    assert len(seen) == 1 and isinstance(seen[0], urllib.request.Request)
    assert seen[0].get_header("Authorization") == "Bearer tok"
    assert seen[0].full_url == "http://127.0.0.1:1/api/info"


def test_a_401_names_the_credential_settings(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    _serve(monkeypatch, b"", status=401)
    with pytest.raises(AxQueryError) as e:
        AxQuerySource("axquery:http://127.0.0.1:1").get("/api/info")
    assert "HTTP 401" in str(e.value) and ENV_API_KEY in str(e.value)


INFO = {
    "chunks": 69, "packets": 10, "bytes": 20, "entities": 3, "events": 4,
    "first_ts": 1788723036, "last_ts": 1791315036,
    "sites": [{"location": "axellio-hq",
               "systems": [{"name": "corp-apcn-rdr1", "streams": 12}]}],
}


def test_probe_reports_span_vantage_and_census(monkeypatch):
    _serve(monkeypatch, json.dumps(INFO).encode())
    got = get_plugin("axquery").probe("axquery:http://127.0.0.1:1")
    assert got["supported"] and got["reachable"] and got["error"] is None
    assert (got["first_ts"], got["last_ts"]) == (1788723036.0, 1791315036.0)
    assert got["vantage"] == "axellio-hq/corp-apcn-rdr1:12"
    assert got["extra"]["chunks"] == 69 and got["extra"]["sites"] == INFO["sites"]


def test_probe_unreachable_is_an_answer_not_a_crash(monkeypatch):
    def down(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    got = get_plugin("axquery").probe("axquery:http://127.0.0.1:1")
    assert got["supported"] and got["reachable"] is False
    assert "unreachable" in got["error"] and got["first_ts"] is None


def test_probe_refuses_a_bare_host_like_the_locator_does():
    got = get_plugin("axquery").probe("axquery:10.0.0.1:9090")
    assert got["reachable"] is False and "absolute http(s) base URL" in got["error"]


def test_the_probe_verb_prints_the_answer(monkeypatch, capsys):
    _serve(monkeypatch, json.dumps(INFO).encode())
    assert cli.main(["probe", "--plugin", "axquery", "--source", "axquery:http://127.0.0.1:1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reachable"] is True and out["last_ts"] == 1791315036.0
    assert out["plugin"] == "axquery"


def test_sources_module_exports_the_setting_names():
    assert sources.ENV_USER == "AXIMPRINT_SOURCE_USER"
    assert sources.ENV_PASSWORD == "AXIMPRINT_SOURCE_PASSWORD"
    assert sources.ENV_API_KEY == "AXIMPRINT_SOURCE_API_KEY"
