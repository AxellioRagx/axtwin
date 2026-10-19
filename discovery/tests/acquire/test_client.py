"""Tests for aximprint.acquire.client.

No test here opens a network socket: `FakeAdapter` is a `requests.adapters.
BaseAdapter` mounted onto a real `requests.Session`, so every request still
goes through `requests`' real URL-building/encoding machinery (proving the
`params=` round trip is real, not just "we passed a dict") while `send()`
returns a canned `requests.Response` instead of touching a socket.
"""

import json
import time
import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from aximprint.acquire.auth import Credential
from aximprint.acquire.client import Client
from aximprint.acquire.errors import (
    AuthenticationError,
    ConnectionFailedError,
    ForbiddenError,
    InvalidPayloadError,
    MalformedResponseError,
    NoDataError,
    NotCompleteError,
    NotFoundError,
    ServerError,
    TlsVerificationError,
    UnauthorizedError,
)

# --- Test doubles: a socket-free requests transport -------------------------


class FakeAdapter(requests.adapters.BaseAdapter):
    """`script` is consumed one item per `send()` call (the last item repeats
    once exhausted, so a "keeps failing" scenario doesn't need N copies).
    Each item is either a `requests.Response` or an `Exception` (instance or
    class) to raise, for connection-error/timeout scenarios.
    """

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.requests = []  # captured PreparedRequest objects, call order

    def send(self, request, **kwargs):
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self.script) - 1)
        item = self.script[idx]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("fake transport error")
        return item

    def close(self):
        pass


class _FakeRaw:
    """Just enough of a urllib3-response shape for `Response.iter_content`'s
    `hasattr(self.raw, "stream")` branch to pull chunks from — so a
    `stream=True` test can prove the body is read lazily, not slurped into
    `_content` up front.
    """

    def __init__(self, data, chunk_size=16):
        self._data = data
        self._chunk_size = chunk_size

    def stream(self, chunk_size, decode_content=True):
        size = chunk_size or self._chunk_size
        for i in range(0, len(self._data), size):
            yield self._data[i : i + size]


def make_response(status_code, json_body=None, content=b"", headers=None, reason="OK", raw=None):
    resp = requests.Response()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers.update(headers or {})
    if json_body is not None:
        content = json.dumps(json_body).encode()
        resp.headers.setdefault("Content-Type", "application/json")
    if raw is not None:
        resp.raw = raw
    else:
        resp._content = content
        resp._content_consumed = True
    resp.url = "https://fake-host/whatever"
    return resp


def make_session(script):
    adapter = FakeAdapter(script)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, adapter


def api_key_client(script, **kwargs):
    session, adapter = make_session(script)
    client = Client("https://host", Credential.from_api_key("sk-test-key"), session=session, **kwargs)
    return client, adapter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff must never slow the test suite down."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


# --- Phase 2: session and request plumbing ----------------------------------


def test_filter_param_round_trips_through_params_never_string_formatted():
    tricky_filter = 'tcp and port 443 (test) "quoted value"'
    client, adapter = api_key_client([make_response(200, {"data": {}})])

    client.request("GET", "/axapi/task", params={"filter": tricky_filter})

    sent_url = adapter.requests[0].url
    parts = urlsplit(sent_url)
    assert parts.path == "/axapi/task"
    assert parse_qs(parts.query)["filter"][0] == tricky_filter


def test_bearer_header_present_on_every_request():
    client, adapter = api_key_client([make_response(200, {"a": 1}), make_response(200, {"b": 2})])

    client.request("GET", "/axapi/task")
    client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 2
    for req in adapter.requests:
        assert req.headers["Authorization"] == "Bearer sk-test-key"


def test_ca_bundle_threaded_through_session():
    client, _adapter = api_key_client([], ca_bundle="/etc/ssl/corp-ca.pem")
    assert client._session.verify == "/etc/ssl/corp-ca.pem"


def test_verify_true_by_default_no_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        api_key_client([])
    assert len(caught) == 0


def test_verify_false_warns_exactly_once():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client, _adapter = api_key_client([make_response(200, {})], verify=False)
        client.request("GET", "/axapi/task")
        client.request("GET", "/axapi/task")
    warned = [w for w in caught if issubclass(w.category, UserWarning)]
    assert len(warned) == 1
    assert "unverified" in str(warned[0].message)


def test_streaming_response_not_materialized_eagerly():
    body = b"pcap-bytes" * 1000
    client, _adapter = api_key_client([make_response(200, raw=_FakeRaw(body, chunk_size=16))])

    resp = client.request("GET", "/axapi/pcap", stream=True)

    # `requests.Session.send` only reads `r.content` eagerly when
    # `stream=False`; if this is still False here, client.py really did pass
    # `stream=True` through rather than reading the whole body into memory.
    assert resp._content_consumed is False

    chunks = list(resp.iter_content(chunk_size=16))
    assert b"".join(chunks) == body


# --- Phase 3: auth lifecycle -------------------------------------------------


def test_login_success_is_201_not_200():
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session([make_response(201, {"token": "jwt-token-1"})])
    client = Client("https://host", cred, session=session)

    client.login()

    assert client._token == "jwt-token-1"
    assert session.headers["Authorization"] == "Bearer jwt-token-1"
    req = adapter.requests[0]
    assert req.method == "POST"
    assert urlsplit(req.url).path == "/axapi/login"
    assert json.loads(req.body) == {"username": "alice", "password": "hunter2"}


@pytest.mark.parametrize("status", [200, 201])
def test_login_accepts_both_200_and_201(status):
    """Both vendor PDFs document 201, but the live CorpAPCN appliance
    (PacketXpress 5.1.0) was observed returning 200 on 2026-08-05 — `login()`
    must accept either rather than rejecting a login that actually succeeded
    (`LES-034`, amended).
    """
    cred = Credential.from_password("alice", "hunter2")
    session, _adapter = make_session([make_response(status, {"token": "jwt-token-1"}, reason="OK")])
    client = Client("https://host", cred, session=session)

    client.login()
    assert client._token == "jwt-token-1"


def test_login_response_missing_token_field_raises_typed_error_not_key_error():
    """A bare `KeyError` isn't an `XtractAPIError`, so `request()`'s 401
    recovery loop (`except XtractAPIError: continue`) would never catch it —
    it would escape `login()`/`_refresh()` entirely and surface to the
    operator as `unexpected error: 'token'` (cli.py's last-resort handler),
    skipping the intended re-login recovery path.
    """
    cred = Credential.from_password("alice", "hunter2")
    session, _adapter = make_session([make_response(201, {"unexpected": "shape"})])
    client = Client("https://host", cred, session=session)

    with pytest.raises(MalformedResponseError):
        client.login()


def test_refresh_response_missing_token_field_raises_typed_error():
    """`POST /axapi/refresh`'s response body is explicitly undocumented
    (`docs/AXAPI_CONTRACT.md` §1.2) — `{"token": ...}` is an inference, not an
    assertable fact, so a body without it must raise a typed error the
    401-recovery loop can catch and fall through to a full re-login, not a
    bare `KeyError`.
    """
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session(
        [
            make_response(401, {"message": "token expired"}),  # original request
            make_response(201, {}),  # refresh succeeds (201) but has no "token"
            make_response(201, {"token": "relogged-in-token"}),  # full re-login
            make_response(200, {"ok": True}),  # retry after re-login
        ]
    )
    client = Client("https://host", cred, session=session)
    client._set_token("stale-token")

    resp = client.request("GET", "/axapi/task")

    assert resp.json() == {"ok": True}
    # the malformed refresh body was recovered from by falling through to a
    # full re-login, exactly like a refresh that failed with a real 401 would.
    assert [(r.method, urlsplit(r.url).path) for r in adapter.requests] == [
        ("GET", "/axapi/task"),
        ("POST", "/axapi/refresh"),
        ("POST", "/axapi/login"),
        ("GET", "/axapi/task"),
    ]


def test_401_triggers_refresh_then_retry_succeeds():
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session(
        [
            make_response(401, {"message": "token expired"}),
            make_response(201, {"token": "refreshed-token"}),
            make_response(200, {"ok": True}),
        ]
    )
    client = Client("https://host", cred, session=session)
    client._set_token("stale-token")

    resp = client.request("GET", "/axapi/task")

    assert resp.json() == {"ok": True}
    assert [r.method for r in adapter.requests] == ["GET", "POST", "GET"]
    assert urlsplit(adapter.requests[1].url).path == "/axapi/refresh"
    assert client._token == "refreshed-token"


def test_401_falls_through_refresh_to_relogin_then_fails():
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session(
        [
            make_response(401, {"message": "expired"}),  # original request
            make_response(401, {"message": "expired"}),  # refresh attempt fails
            make_response(201, {"token": "relogged-in-token"}),  # re-login succeeds
            make_response(401, {"message": "still no"}),  # retry after re-login still 401
        ]
    )
    client = Client("https://host", cred, session=session)
    client._set_token("stale-token")

    with pytest.raises(AuthenticationError):
        client.request("GET", "/axapi/task")

    methods_and_paths = [(r.method, urlsplit(r.url).path) for r in adapter.requests]
    assert methods_and_paths == [
        ("GET", "/axapi/task"),
        ("POST", "/axapi/refresh"),
        ("POST", "/axapi/login"),
        ("GET", "/axapi/task"),
    ]


def test_api_key_mode_401_never_attempts_refresh_or_relogin():
    client, adapter = api_key_client([make_response(401, {"message": "bad key"})])

    with pytest.raises(UnauthorizedError):
        client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 1


def test_logout_fires_on_exception_path():
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session(
        [
            make_response(201, {"token": "jwt-token-1"}),
            make_response(200, {"token": "jwt-token-1"}),
        ]
    )
    client = Client("https://host", cred, session=session)

    with pytest.raises(RuntimeError), client:
        raise RuntimeError("boom mid-run")

    assert [r.method for r in adapter.requests] == ["POST", "POST"]
    assert urlsplit(adapter.requests[1].url).path == "/axapi/logout"


def test_logout_non_json_body_does_not_replace_the_original_exception():
    """Reproduces the reviewer's finding: a `RuntimeError` raised inside
    `with client:` must propagate unchanged, even when `POST /axapi/logout`
    answers 200 with a body that isn't JSON — `logout()` must not call
    `resp.json()` (which raises `requests.exceptions.JSONDecodeError`, a
    `ValueError`, not an `XtractAPIError`) and `__exit__` must not let a
    logout failure of any kind mask the exception that was already
    propagating.
    """
    cred = Credential.from_password("alice", "hunter2")
    session, adapter = make_session(
        [
            make_response(201, {"token": "jwt-token-1"}),
            make_response(200, content=b"<html>not json</html>"),  # logout's body
        ]
    )
    client = Client("https://host", cred, session=session)

    with pytest.raises(RuntimeError, match="boom mid-run"), client:
        raise RuntimeError("boom mid-run")

    assert [r.method for r in adapter.requests] == ["POST", "POST"]
    assert urlsplit(adapter.requests[1].url).path == "/axapi/logout"


def test_logout_return_type_matches_its_none_annotation():
    cred = Credential.from_password("alice", "hunter2")
    session, _adapter = make_session(
        [
            make_response(201, {"token": "jwt-token-1"}),
            make_response(200, {"ignored": "body"}),
        ]
    )
    client = Client("https://host", cred, session=session)
    client.login()

    assert client.logout() is None


def test_logout_non_json_body_on_a_clean_exit_never_raises():
    cred = Credential.from_password("alice", "hunter2")
    session, _adapter = make_session(
        [
            make_response(201, {"token": "jwt-token-1"}),
            make_response(200, content=b"not json at all"),
        ]
    )
    client = Client("https://host", cred, session=session)

    with client:
        pass  # a clean exit still calls logout(); must not raise


def test_logout_not_attempted_for_api_key_credential():
    session, adapter = make_session([])
    client = Client("https://host", Credential.from_api_key("sk-key"), session=session)

    with client:
        pass

    assert adapter.requests == []


def test_no_filesystem_write_in_jwt_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cred = Credential.from_password("alice", "hunter2")
    session, _adapter = make_session(
        [
            make_response(201, {"token": "jwt-token-1"}),
            make_response(200, {"keys": [], "count": 0}),
            make_response(200, {"token": "jwt-token-1"}),
        ]
    )
    client = Client("https://host", cred, session=session)

    with client:
        client.list_api_keys()

    assert list(tmp_path.iterdir()) == []


# --- Phase 4: status mapping and retries ------------------------------------


@pytest.mark.parametrize(
    "status, exc_type",
    [
        (401, UnauthorizedError),
        (403, ForbiddenError),
        (404, NotFoundError),
        (415, InvalidPayloadError),
        (418, NoDataError),
        (425, NotCompleteError),
    ],
)
def test_status_maps_to_typed_exception(status, exc_type):
    client, adapter = api_key_client([make_response(status, {"message": "boom"})])

    with pytest.raises(exc_type) as exc_info:
        client.request("GET", "/axapi/task")

    assert exc_info.value.status_code == status
    assert len(adapter.requests) == 1  # 4xx is never retried


def test_418_is_a_stable_answer_never_retried():
    client, adapter = api_key_client([make_response(418, {"message": "no data"})] * 5)

    with pytest.raises(NoDataError):
        client.request("GET", "/axapi/pcap")

    assert len(adapter.requests) == 1


def test_500_is_retried_to_budget_then_raises():
    client, adapter = api_key_client(
        [make_response(500, {"message": "internal error"})], max_retries=2
    )

    with pytest.raises(ServerError):
        client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 3  # 1 initial + 2 retries


def test_500_retry_then_success():
    client, adapter = api_key_client(
        [make_response(500, {"message": "transient"}), make_response(200, {"ok": True})],
        max_retries=2,
    )

    resp = client.request("GET", "/axapi/task")

    assert resp.json() == {"ok": True}
    assert len(adapter.requests) == 2


def test_connection_error_is_retried_then_succeeds():
    client, adapter = api_key_client(
        [requests.exceptions.ConnectionError("refused"), make_response(200, {"ok": True})],
        max_retries=2,
    )

    resp = client.request("GET", "/axapi/task")

    assert resp.json() == {"ok": True}
    assert len(adapter.requests) == 2


def test_connection_error_exhausts_budget_and_raises_typed_error():
    client, adapter = api_key_client(
        [requests.exceptions.Timeout("timed out")], max_retries=1
    )

    with pytest.raises(ConnectionFailedError):
        client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 2  # 1 initial + 1 retry


def test_connection_failed_message_never_carries_the_hostname_or_query_string():
    """Reproduces the reviewer's finding: `requests`' own `ConnectionError`
    message embeds the target host and the full request URL (query string
    included, e.g. a BPF `filter` value) — that text must never end up
    verbatim in `ConnectionFailedError`'s message, because that message is
    what `extract.py` puts in `Failed.detail`, which `runner.py` writes into
    `manifest.jsonl`.
    """
    leaky = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='corpapcn.example.com', port=443): Max retries "
        "exceeded with url: /axapi/pcap?ctid=xtract-abc-1&filter=tcp+and+port+443"
        "&start_time=0&end_time=10 (Caused by NewConnectionError(...))"
    )
    client, adapter = api_key_client([leaky], max_retries=0)

    with pytest.raises(ConnectionFailedError) as exc_info:
        client.request("GET", "/axapi/pcap", params={"filter": "tcp and port 443"})

    message = str(exc_info.value)
    assert "corpapcn" not in message
    assert "filter=" not in message
    assert "ConnectionError" in message
    assert "/axapi/pcap" in message  # the path is still useful context
    assert len(adapter.requests) == 1


def test_tls_verification_failure_is_not_retried():
    """`requests.exceptions.SSLError` subclasses its own `ConnectionError`, so before
    `_do_request` grew a dedicated branch it inherited the transient-error retry budget
    — four handshakes and 3.5 s of backoff to re-learn that a certificate the appliance
    will present again is still not verifiable. A rejected certificate is a stable
    answer, exactly like the 418/4xx case in `LES-035`, one layer below HTTP.

    `max_retries=2` (not 0) is the point: a budget exists and must go unspent.
    """
    client, adapter = api_key_client(
        [requests.exceptions.SSLError("certificate verify failed")], max_retries=2
    )

    with pytest.raises(TlsVerificationError):
        client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 1, (
        f"TLS failure must not be retried; made {len(adapter.requests)} attempts"
    )


def test_tls_error_names_the_remedy_and_still_never_carries_the_hostname():
    """The privacy constraint (`LES-039`) and the diagnosability one pull in opposite
    directions, and both have to hold. `requests`' own SSLError text embeds the full
    URL, so it cannot be interpolated — but the bare class name it was reduced to
    ("SSLError") covers a no-SAN certificate, an untrusted CA, an expired certificate
    and a protocol mismatch, which do not share a remedy. Naming the flags costs no
    privacy, because a flag name is not a hostname.
    """
    leaky = requests.exceptions.SSLError(
        "HTTPSConnectionPool(host='corpapcn.example.com', port=443): Max retries "
        "exceeded with url: /axapi/login (Caused by SSLError("
        "CertificateError(\"hostname 'corpapcn.example.com' doesn't match 'nothing'\")))"
    )
    client, _adapter = api_key_client([leaky], max_retries=0)

    with pytest.raises(TlsVerificationError) as exc_info:
        client.request("POST", "/axapi/login")

    message = str(exc_info.value)
    assert "corpapcn" not in message, message
    assert "SSLError" in message          # the class is still named
    assert "/axapi/login" in message      # the path is still useful context
    assert "--insecure" in message        # ...and now so is the way out
    assert "--ca-bundle" in message
    # It must not claim a retry it did not make.
    assert "attempt(s)" not in message


def test_a_plain_connection_error_still_exhausts_its_retry_budget():
    """Guard on the other side of the new branch: narrowing SSLError out of the
    transient path must not narrow anything else out with it.
    """
    client, adapter = api_key_client(
        [requests.exceptions.ConnectionError("refused")], max_retries=2
    )

    with pytest.raises(ConnectionFailedError):
        client.request("GET", "/axapi/task")

    assert len(adapter.requests) == 3  # 1 initial + 2 retries


def test_malformed_error_body_does_not_mask_status_code():
    client, adapter = api_key_client(
        [make_response(500, content=b"<html>not json at all</html>")], max_retries=0
    )

    with pytest.raises(ServerError) as exc_info:
        client.request("GET", "/axapi/task")

    assert exc_info.value.status_code == 500
    assert len(adapter.requests) == 1


def test_error_envelope_with_errors_list_is_parsed_into_message():
    body = {
        "code": 400,
        "message": "validation failed",
        "errors": [{"domain": "global", "reason": "invalid", "message": "bad filter", "instance": "x"}],
    }
    client, _adapter = api_key_client([make_response(415, body)])

    with pytest.raises(InvalidPayloadError) as exc_info:
        client.request("GET", "/axapi/task")

    message = str(exc_info.value)
    assert "validation failed" in message
    assert "bad filter" in message


# --- Phase 5: API-key hygiene commands ---------------------------------------


def test_list_api_keys_parses_documented_shape():
    body = {
        "keys": [
            {
                "id": 1,
                "key_prefix": "sk-ab12",
                "user": "alice",
                "description": "ci",
                "created_at": 1700000000,
                "expires_at": 1800000000,
                "last_used_at": 1700000100,
                "revoked": False,
            }
        ],
        "count": 1,
    }
    client, adapter = api_key_client([make_response(200, body)])

    keys = client.list_api_keys()

    assert keys == body["keys"]
    assert urlsplit(adapter.requests[0].url).path == "/axapi/apikeys"
    assert adapter.requests[0].method == "GET"


def test_list_api_keys_include_revoked_param():
    client, adapter = api_key_client(
        [make_response(200, {"keys": [], "count": 0}), make_response(200, {"keys": [], "count": 0})]
    )

    client.list_api_keys(include_revoked=False)
    client.list_api_keys(include_revoked=True)

    query0 = parse_qs(urlsplit(adapter.requests[0].url).query)
    query1 = parse_qs(urlsplit(adapter.requests[1].url).query)
    assert "include_revoked" not in query0
    assert query1["include_revoked"] == ["true"]


def test_revoke_api_key_issues_delete_to_the_right_path():
    client, adapter = api_key_client([make_response(200, {"message": "revoked", "key_id": 42})])

    result = client.revoke_api_key(42)

    assert result == {"message": "revoked", "key_id": 42}
    assert adapter.requests[0].method == "DELETE"
    assert urlsplit(adapter.requests[0].url).path == "/axapi/apikeys/42"


def test_list_api_keys_missing_keys_field_raises_typed_error_not_key_error():
    client, _adapter = api_key_client([make_response(200, {"unexpected": "shape"})])

    with pytest.raises(MalformedResponseError):
        client.list_api_keys()


def test_revoke_api_key_non_json_body_raises_typed_error_not_value_error():
    client, _adapter = api_key_client([make_response(200, content=b"not json")])

    with pytest.raises(MalformedResponseError):
        client.revoke_api_key(42)


# --------------------------------------------------------------------------------------------
# GAP-L1 / GAP-L2 (`docs/JOURNAL_imprint_loop.md`) — an API key's 403 must name the credential
# MODE as the cause, because the appliance's own message does not and cannot: it reports a
# missing role, while both accounts tried DID hold that role. The mode is the defect.
# --------------------------------------------------------------------------------------------
_ROLE_403 = {
    "code": 403, "message": "Forbidden",
    "errors": {"domain": "Authorization", "reason": "Insufficient Role",
               "message": "This endpoint requires one of: Extractor. "
                          "Account 'fxadmin' does not have any of them.",
               "instance": "RBAC"},
}


def _client_with(monkeypatch, credential, body):
    from aximprint.acquire import client as client_mod
    c = client_mod.Client("https://host", credential)

    def _boom(*_a, **_kw):
        raise client_mod.ForbiddenError("Forbidden", status_code=403, body=body)

    monkeypatch.setattr(c, "_do_request", _boom)
    return c


def test_an_api_key_403_names_the_credential_mode(monkeypatch):
    from aximprint.acquire.auth import Credential
    from aximprint.acquire.errors import ForbiddenError
    c = _client_with(monkeypatch, Credential.from_api_key("axapi_dead_beef"), _ROLE_403)
    with pytest.raises(ForbiddenError) as ei:
        c.request("GET", "/axapi/fs/files")
    msg = str(ei.value)
    assert "no role metadata" in msg
    assert "password_file" in msg, "must name the fix, not only the cause"


def test_a_jwt_403_is_left_alone(monkeypatch):
    """The note is specific to API keys. A password-mode credential hitting the same 403 is
    genuinely missing a role, and telling it to 'use a password profile' would be nonsense."""
    from aximprint.acquire.auth import Credential
    from aximprint.acquire.errors import ForbiddenError
    c = _client_with(monkeypatch, Credential.from_password("u", "p"), _ROLE_403)
    with pytest.raises(ForbiddenError) as ei:
        c.request("GET", "/axapi/fs/files")
    assert "no role metadata" not in str(ei.value)


def test_a_non_role_403_keeps_its_own_message(monkeypatch):
    """Detection reads the appliance's structured `reason`, so an unrelated denial - a key
    reaching another owner's resource - is not mislabelled as the API-key limitation."""
    from aximprint.acquire.auth import Credential
    from aximprint.acquire.errors import ForbiddenError
    other = {"code": 403, "message": "Forbidden",
             "errors": {"reason": "Not Owner", "message": "not your extract"}}
    c = _client_with(monkeypatch, Credential.from_api_key("axapi_dead_beef"), other)
    with pytest.raises(ForbiddenError) as ei:
        c.request("GET", "/axapi/fs/files")
    assert "no role metadata" not in str(ei.value)


def test_the_cli_surfaces_the_appliance_diagnosis():
    """GAP-L2: the body's `reason`/`message` reach the operator. A bare status word cost a
    40-minute investigation whose answer was in the unread response body."""
    from aximprint.acquire.cli import _api_error_detail
    from aximprint.acquire.errors import ForbiddenError
    text = _api_error_detail(ForbiddenError("Forbidden", status_code=403, body=_ROLE_403))
    assert "Insufficient Role" in text and "Extractor" in text


@pytest.mark.parametrize("body", [None, {}, {"errors": "not-a-dict"}, {"errors": {}}])
def test_the_detail_helper_never_raises_on_an_odd_body(body):
    """A diagnostic helper that raises while reporting a failure hides the failure."""
    from aximprint.acquire.cli import _api_error_detail
    from aximprint.acquire.errors import ForbiddenError
    assert _api_error_detail(ForbiddenError("Forbidden", status_code=403, body=body)) == "Forbidden"
