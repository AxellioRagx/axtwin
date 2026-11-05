"""`api/Spec.md` A8 -- who may write: `AXRANGE_AUTH = none | proxy | token`,
`GET /api/whoami`, the startup warning. Beside the other `test_api_*` files;
drives real routes through one dependency, never a stub of it.

The probe write is `DELETE /api/discovery/runs/<unknown>`: a 404 proves the
dependency let the request through, a 401 that it did not, and nothing is
created either way. The conditional read is `?secrets=true` on access facts.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api import auth
from axrange.api.settings import ApiSettingsError

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette not installed", allow_module_level=True)

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"
LOOPBACK = ("127.0.0.1", 40000)
LAN = ("10.0.0.5", 40000)


def _settings(tmp_path: Path, **over) -> ApiSettings:
    kw = dict(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(FIXT_IR.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config="/nonexistent.conf",
        imprints_dir=str(tmp_path / "imprints"),
    )
    (tmp_path / "imprints").mkdir(exist_ok=True)
    kw.update(over)
    return ApiSettings(**kw)


def _client(settings: ApiSettings, peer=LOOPBACK) -> TestClient:
    return TestClient(create_app(settings), raise_server_exceptions=False, client=peer)


PROBE = "/api/discovery/runs/disc-nope"


# ---------------------------------------------------------------------------
# the policy, as pure parsing
# ---------------------------------------------------------------------------
def test_unset_auth_is_token_when_a_token_is_set_else_none():
    assert auth.parse_modes("", "") == ("none",)
    assert auth.parse_modes("", "s3cret") == ("token",)
    assert auth.parse_modes(" Proxy , token ", "s3cret") == ("proxy", "token")
    assert auth.parse_modes("proxy,proxy", "") == ("proxy",)


@pytest.mark.parametrize("raw, token, needle", [
    ("basic", "", "unknown mode"),
    ("none,proxy", "", "`none`"),
    ("token", "", "AXRANGE_API_TOKEN is unset"),
])
def test_a_bad_auth_setting_is_refused_naming_the_key(raw, token, needle):
    with pytest.raises(auth.AuthConfigError) as e:
        auth.parse_modes(raw, token)
    assert "AXRANGE_AUTH" in str(e.value) and needle in str(e.value)


def test_trusted_proxies_default_to_loopback_and_refuse_junk():
    nets = auth.parse_trusted_proxies("")
    assert [str(n) for n in nets] == ["127.0.0.1/32", "::1/128"]
    assert [str(n) for n in auth.parse_trusted_proxies("10.0.0.0/8, 192.168.1.7")] == [
        "10.0.0.0/8", "192.168.1.7/32",
    ]
    with pytest.raises(auth.AuthConfigError) as e:
        auth.parse_trusted_proxies("10.0.0.0/8,proxy.lan")
    assert "AXRANGE_TRUSTED_PROXY_CIDRS" in str(e.value) and "proxy.lan" in str(e.value)


def test_a_bad_setting_reaches_create_app_as_a_settings_error(tmp_path):
    with pytest.raises(ApiSettingsError) as e:
        create_app(_settings(tmp_path, auth="jwt"))
    assert "AXRANGE_AUTH" in str(e.value)
    with pytest.raises(ApiSettingsError):
        create_app(_settings(tmp_path, auth="proxy", trusted_proxy_cidrs="nope"))


def test_the_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("AXRANGE_AUTH", "proxy,token")
    monkeypatch.setenv("AXRANGE_API_TOKEN", "s3cret")
    monkeypatch.setenv("AXRANGE_TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    monkeypatch.setenv("AXRANGE_SIGNIN_URL", "/oauth2/start?rd=")
    policy = ApiSettings().auth_policy()
    assert policy.modes == ("proxy", "token")
    assert [str(n) for n in policy.trusted_proxies] == ["10.0.0.0/8"]
    assert policy.signin_url == "/oauth2/start?rd="


# ---------------------------------------------------------------------------
# none: the dev default
# ---------------------------------------------------------------------------
def test_none_accepts_every_write_and_whoami_says_anonymous(tmp_path):
    c = _client(_settings(tmp_path), peer=LAN)
    assert c.delete(PROBE).status_code == 404  # through the dependency, nothing there
    assert c.get("/api/whoami").json() == {
        "user": "anonymous", "can_write": True, "mode": "none", "signin_url": "",
    }


# ---------------------------------------------------------------------------
# token: the existing check, unchanged
# ---------------------------------------------------------------------------
def test_a_token_implies_token_mode_and_the_bearer_is_checked(tmp_path):
    c = _client(_settings(tmp_path, api_token="s3cret"), peer=LAN)
    r = c.delete(PROBE)
    assert r.status_code == 401 and "API token" in r.json()["detail"]
    assert c.delete(PROBE, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.delete(PROBE, headers={"Authorization": "Bearer s3cret"}).status_code == 404
    # A proxy header means nothing in token mode.
    assert c.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 401
    assert c.get("/api/whoami").json() == {
        "user": None, "can_write": False, "mode": "token", "signin_url": "",
    }
    me = c.get("/api/whoami", headers={"Authorization": "Bearer s3cret"}).json()
    assert me == {"user": "token", "can_write": True, "mode": "token", "signin_url": ""}


def test_the_conditional_reads_use_the_same_policy(tmp_path):
    c = _client(_settings(tmp_path, api_token="s3cret"))
    r = c.get("/api/deploys/none-such/access", params={"secrets": "true"})
    assert r.status_code == 401 and "secrets=true" in r.json()["detail"]
    r = c.get("/api/deploys/none-such/access", params={"secrets": "true"},
              headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 404  # past the gate; no such deploy
    # Reads without the reveal stay open.
    assert c.get("/api/deploys/none-such/access").status_code == 404


# ---------------------------------------------------------------------------
# proxy: an identity header from a trusted peer
# ---------------------------------------------------------------------------
def test_proxy_trusts_the_header_only_from_a_trusted_peer(tmp_path):
    settings = _settings(tmp_path, auth="proxy", signin_url="/oauth2/start?rd=")
    at_loopback = _client(settings, peer=LOOPBACK)
    r = at_loopback.delete(PROBE)
    assert r.status_code == 401 and "sign in required" in r.json()["detail"]
    assert at_loopback.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 404
    assert at_loopback.delete(PROBE, headers={"X-Auth-Request-Email": "ann@example.org"}).status_code == 404
    assert at_loopback.delete(PROBE, headers={"X-Forwarded-User": "   "}).status_code == 401
    assert at_loopback.get("/api/whoami", headers={"X-Forwarded-User": "ann"}).json() == {
        "user": "ann", "can_write": True, "mode": "proxy", "signin_url": "/oauth2/start?rd=",
    }
    assert at_loopback.get("/api/whoami").json() == {
        "user": None, "can_write": False, "mode": "proxy", "signin_url": "/oauth2/start?rd=",
    }
    # The same header straight from the LAN is a forgery: treated as absent.
    on_lan = _client(settings, peer=LAN)
    assert on_lan.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 401
    assert on_lan.get("/api/whoami", headers={"X-Forwarded-User": "ann"}).json()["can_write"] is False


def test_trusted_proxy_cidrs_widen_the_trust(tmp_path):
    settings = _settings(tmp_path, auth="proxy", trusted_proxy_cidrs="10.0.0.0/8")
    assert _client(settings, peer=LAN).delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 404
    # Loopback is no longer listed, so it is no longer trusted.
    assert _client(settings, peer=LOOPBACK).delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 401
    # An IPv4-mapped IPv6 peer is the IPv4 address it maps.
    mapped = _client(settings, peer=("::ffff:10.1.2.3", 1))
    assert mapped.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 404


def test_proxy_and_token_combine(tmp_path):
    settings = _settings(tmp_path, auth="proxy,token", api_token="s3cret")
    on_lan = _client(settings, peer=LAN)
    r = on_lan.delete(PROBE)
    assert r.status_code == 401
    assert "API token" in r.json()["detail"] and "sign in required" in r.json()["detail"]
    assert on_lan.delete(PROBE, headers={"Authorization": "Bearer s3cret"}).status_code == 404
    assert on_lan.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 401
    at_loopback = _client(settings, peer=LOOPBACK)
    assert at_loopback.delete(PROBE, headers={"X-Forwarded-User": "ann"}).status_code == 404
    assert at_loopback.get("/api/whoami").json()["mode"] == "proxy,token"


def test_the_dependency_leaves_the_identity_on_the_request(tmp_path):
    from axrange.api.app import _require_write_token

    dep = _require_write_token(_settings(tmp_path, auth="proxy"))
    req = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"x-forwarded-user": "ann"}, state=SimpleNamespace(),
    )
    dep(req)
    assert (req.state.user, req.state.auth_mode) == ("ann", "proxy")


# ---------------------------------------------------------------------------
# the startup line
# ---------------------------------------------------------------------------
def _serve(monkeypatch, tmp_path, **kw):
    import axrange.api.serve as serve_mod

    seen = {}
    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn",
        type("_U", (), {"run": staticmethod(lambda *a, **k: seen.update(k))})(),
    )
    lines: list[str] = []
    rc = serve_mod.serve(
        db_path=str(tmp_path / "s.db"), adapter_config="/nonexistent.conf",
        log=lines.append, **kw,
    )
    return rc, lines, seen


def test_serve_warns_loudly_in_none_and_names_the_mode_otherwise(monkeypatch, tmp_path):
    monkeypatch.delenv("AXRANGE_AUTH", raising=False)
    monkeypatch.delenv("AXRANGE_API_TOKEN", raising=False)
    rc, lines, seen = _serve(monkeypatch, tmp_path)
    assert rc == 0
    assert any(line.startswith("WARNING") and "AXRANGE_AUTH=none" in line for line in lines)
    # The peer must stay the TCP peer for the proxy CIDR check.
    assert seen["proxy_headers"] is False

    rc, lines, _ = _serve(monkeypatch, tmp_path, api_token="s3cret")
    assert rc == 0
    assert not any(line.startswith("WARNING") for line in lines)
    assert any("AXRANGE_AUTH=token" in line for line in lines)

    monkeypatch.setenv("AXRANGE_AUTH", "proxy")
    rc, lines, _ = _serve(monkeypatch, tmp_path)
    assert any("AXRANGE_AUTH=proxy" in line and "127.0.0.1/32" in line for line in lines)


def test_serve_reports_a_bad_auth_setting_and_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AXRANGE_AUTH", "jwt")
    rc, _lines, _ = _serve(monkeypatch, tmp_path)
    assert rc == 2
    assert "AXRANGE_AUTH" in capsys.readouterr().err
