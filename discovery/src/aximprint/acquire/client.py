"""client — auth, session, and the only module in `xtract` that imports
`requests`.

`Client` authenticates to an `axapi` host (by API key or by JWT
username/password), issues read-only requests with correct parameter
encoding, maps `axapi` status codes to the typed exceptions in `errors.py`,
and retries only the failures that are actually transient. It knows nothing
about streams, windows, or pcap bytes — those live in `catalog.py` and
`extract.py` (`docs/WORK_ORDER_client.md` §1, "Not a goal").

Every request goes through `params=`/`json=` on `requests`, never a
string-formatted URL (`LES-033`) — a BPF filter containing spaces, parens,
and quotes must round-trip byte-for-byte.
"""

from __future__ import annotations

import contextlib
import time
import warnings
from collections.abc import Collection, Iterator, Mapping
from typing import Any

import requests

from aximprint.acquire.auth import Credential
from aximprint.acquire.errors import (
    AuthenticationError,
    ConnectionFailedError,
    ForbiddenError,
    MalformedResponseError,
    TlsVerificationError,
    UnauthorizedError,
    XtractAPIError,
    exception_for_status,
)

__all__ = [
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "Client",
]

# Conservative defaults (work order §2 Phase 4: "retry budget and backoff are
# constructor parameters with conservative defaults"). `DEFAULT_MAX_RETRIES`
# is the number of *retries* after the first attempt, so a request that never
# succeeds is attempted `DEFAULT_MAX_RETRIES + 1` times in total.
DEFAULT_TIMEOUT = 30  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5  # seconds; doubles each retry (0.5s, 1s, 2s, ...)

# `iter_stream_body`'s default chunk size, exported so `extract.py` (the only
# other module that streams a response body) can reuse this exact value
# instead of carrying its own private, silently-driftable copy.
DEFAULT_CHUNK_SIZE = 65536

# Statuses `requests` itself might raise ConnectionError/Timeout for aren't
# in this set — those are caught as exceptions, not read off `status_code`.
_RETRYABLE_STATUS = 500


def silence_insecure_tls_warnings() -> None:
    """Stop `--insecure` from burying real output under repeated warnings.

    Two separate sources shout on every request: `urllib3`'s
    `InsecureRequestWarning`, and this module's own one-shot warning at
    `Client` construction. Passing `--insecure` is already an explicit,
    documented opt-in, so repeating the warning per request tells the operator
    nothing new while making the actual table hard to read.

    The CLI calls this **only** when `--insecure` was passed, and prints one
    concise line to stderr in its place — so the risk is still stated once,
    exactly as loudly as it needs to be. Library callers who never invoke this
    keep the full default warning behavior.

    `urllib3` is imported here rather than in `cli.py` to keep the
    "`client.py` is the only module that imports the HTTP stack" rule intact.
    """
    warnings.filterwarnings("ignore", message=".*TLS verification is disabled.*")
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except (ImportError, AttributeError):  # pragma: no cover - urllib3 always ships with requests
        pass


def _normalize_host(host: str) -> str:
    if "://" not in host:
        host = f"https://{host}"
    return host.rstrip("/")



def _is_insufficient_role(exc: XtractAPIError) -> bool:
    """True when a 403's body names a missing ROLE, as opposed to any other denial.

    Reads the appliance's structured envelope (`errors.reason == "Insufficient Role"`) rather
    than substring-matching the human message, which is prose and may be reworded.
    """
    body = exc.body or {}
    errors = body.get("errors")
    return isinstance(errors, dict) and errors.get("reason") == "Insufficient Role"

class Client:
    """A session against one `axapi` host.

    JWT-mode credentials must be used as a context manager
    (`with Client(...) as client:`) so `POST /axapi/logout` is guaranteed to
    run on the way out — including on an exception or `KeyboardInterrupt`,
    since `__exit__` runs for any exception propagating out of a `with`
    block, not just `Exception` subclasses. API-key-mode credentials need no
    login/logout; using `Client` outside a `with` block is fine for them.

    `session=` is an injection point for tests (`tests/acquire/test_client.py`
    mounts a fake transport adapter so no test ever opens a socket); it
    defaults to a real `requests.Session()`.
    """

    def __init__(
        self,
        host: str,
        credential: Credential,
        *,
        verify: bool | str = True,
        ca_bundle: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        session: requests.Session | None = None,
    ):
        self._host = _normalize_host(host)
        self._credential = credential
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._token: str | None = None
        self._session = session if session is not None else requests.Session()

        if ca_bundle is not None:
            self._session.verify = ca_bundle
        else:
            self._session.verify = verify
        if verify is False:
            warnings.warn(
                "aximprint acquire: TLS verification is disabled (verify=False) — a bearer "
                "credential will be sent over an unverified channel",
                stacklevel=2,
            )

        if credential.is_api_key:
            self._set_token(credential.reveal())

    @property
    def host(self) -> str:
        """The normalized appliance URL this session talks to.

        Read-only and public because a harvest has to write down **which appliance** its pcaps came
        from (`aximprint.vantage`, `docs/WORK_ORDER_visibility_and_vantage.md` D2): before that, the
        appliance identity `acquire` knew was discarded at the disk boundary, and no model can
        recover metadata nobody wrote down. Exposing the already-normalized value keeps
        `_normalize_host`'s spelling the single one anything records — a caller re-normalizing the
        raw `--host` string would produce a second, silently different vantage id for one tap.

        Carries no credential: `Credential` is a separate object and is never part of this string.
        """
        return self._host

    # --- context manager: JWT login/logout lifecycle -----------------------

    def __enter__(self) -> Client:  # noqa: PYI034 -- `Self` needs py311+; target-version is py39
        if not self._credential.is_api_key:
            self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._credential.is_api_key and self._token is not None:
            # Fire-and-forget: a crashed run still *attempts* to give its
            # token back, but a failed logout must never mask the original
            # exception (or itself become one on a clean exit). Deliberately
            # broader than `except XtractAPIError`: a non-JSON logout body
            # raises `requests`' `JSONDecodeError` (a `ValueError`, not an
            # `XtractAPIError`), and that must be swallowed here too, not
            # just an `axapi`-mapped status.
            with contextlib.suppress(Exception):
                self.logout()
        return False

    # --- auth lifecycle ------------------------------------------------

    def _set_token(self, token: str) -> None:
        self._token = token
        self._session.headers["Authorization"] = f"Bearer {token}"

    def login(self) -> None:
        """`POST /axapi/login`. Accepts **200 or 201**.

        Both vendor PDFs document 201, but CorpAPCN (PacketXpress 5.1.0)
        was observed returning **200** on 2026-08-05 during live
        verification. Accept either rather than picking a side: the docs
        and the appliance disagree, and a login that succeeded must not be
        rejected over which 2xx it chose (`LES-034`, amended).
        """
        username, password = self._credential.reveal()
        resp = self._do_request(
            "POST",
            "/axapi/login",
            json={"username": username, "password": password},
            expected_status=(200, 201),
        )
        self._set_token(_token_from_response(resp, "/axapi/login"))

    def _refresh(self) -> None:
        """`POST /axapi/refresh`. Accepts **200 or 201**, like login.

        The response body is undocumented (`docs/AXAPI_CONTRACT.md` §1.2 —
        `{"token": ...}` is only a plausible inference, not an asserted
        fact), so a missing `token` field here is expected, not exceptional —
        `_token_from_response` raises `MalformedResponseError`, which
        `request()`'s 401-recovery loop already catches and falls through to
        a full re-login on, never a bare `KeyError`.
        """
        resp = self._do_request("POST", "/axapi/refresh", json=None, expected_status=(200, 201))
        self._set_token(_token_from_response(resp, "/axapi/refresh"))

    def logout(self) -> None:
        """`POST /axapi/logout`. This is the one auth endpoint that really
        does return 200, not 201.

        Fire-and-forget: nothing in `xtract` consumes the response body, so
        it is never parsed. A non-JSON body here must not raise — `logout()`
        is called from `__exit__`, and a parse failure there must never
        replace whatever exception (if any) was already propagating out of
        the `with` block.
        """
        token = self._token
        self._do_request("POST", "/axapi/logout", json={"token": token}, expected_status=200)
        self._token = None
        self._session.headers.pop("Authorization", None)

    # --- request plumbing ------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        stream: bool = False,
        expected_status: int | Collection[int] = 200,
    ) -> requests.Response:
        """Issue one logical request, with 401 recovery layered on top of
        `_do_request`'s retry/status-mapping.

        On `401` from a JWT-mode credential: try `POST /axapi/refresh` once
        and retry the original request; if still `401`, try a full
        `POST /axapi/login` once and retry again; if still `401`, raise
        `AuthenticationError`. API-key-mode credentials have no refresh/login
        path, so a 401 there is just `UnauthorizedError`.
        """
        try:
            return self._do_request(
                method, path, params=params, json=json, stream=stream, expected_status=expected_status
            )
        except ForbiddenError as exc:
            # GAP-L1 (`docs/JOURNAL_imprint_loop.md`). An axapi API key carries NO role
            # metadata: the appliance's own `_session_roles_for_token` returns an empty role
            # list for any token beginning `axapi_`, and its docstring says so outright —
            # "those callers get an empty role list, which means any role-gated endpoint will
            # deny them. Document that explicitly if it ever bites someone."
            #
            # Every endpoint this client calls is role-gated, so an API key cannot drive a
            # single one of them. Without this branch the operator sees a bare `Forbidden` and
            # reasonably concludes the ROLE is missing — it is not; two accounts holding
            # `Extractor` in the appliance's own `user_roles` table failed identically. The
            # credential MODE is what is wrong, and nothing in the response says so.
            #
            # Raised, not warned: this is the failure, not a caveat about it. Detection is on
            # the appliance's own `reason` field rather than on the status alone, so an
            # ordinary 403 (genuinely not this key's resource) keeps its own message.
            if self._credential.is_api_key and _is_insufficient_role(exc):
                raise ForbiddenError(
                    f"{exc} — an axapi API key carries no role metadata, so it is refused by "
                    "every role-gated endpoint, which is all of them. This is the credential "
                    "MODE, not a missing role: re-run with a username/password profile "
                    "(`user` + `password_file`) to authenticate via JWT instead.",
                    status_code=exc.status_code, body=exc.body,
                ) from exc
            raise
        except UnauthorizedError:
            if self._credential.is_api_key:
                raise
            for recover in (self._refresh, self.login):
                try:
                    recover()
                except XtractAPIError:
                    continue
                try:
                    return self._do_request(
                        method,
                        path,
                        params=params,
                        json=json,
                        stream=stream,
                        expected_status=expected_status,
                    )
                except UnauthorizedError:
                    continue
            raise AuthenticationError(
                "401 persisted after one refresh attempt and one re-login attempt"
            ) from None

    def _do_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        stream: bool = False,
        expected_status: int | Collection[int] = 200,
    ) -> requests.Response:
        """The low-level request: no 401 recovery, but retries connection
        errors/timeouts/500 up to `max_retries`, and maps every other
        non-`expected_status` response to a typed exception. `418` (and every
        other 4xx) is never retried — it's a stable answer, not a transient
        failure (`LES-035`).

        Neither is a TLS verification failure, for the same reason and one
        layer lower: it raises `TlsVerificationError` on the first attempt.
        That case needs its own `except` clause *ahead* of the connection-error
        tuple, because `requests.exceptions.SSLError` subclasses
        `requests.exceptions.ConnectionError` and is otherwise swallowed by it.
        """
        url = f"{self._host}{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._session.request(
                    method, url, params=params, json=json, stream=stream, timeout=self._timeout
                )
            except requests.exceptions.SSLError as exc:
                # FIRST, before the tuple below: `requests` models this as a
                # SUBCLASS of its own `ConnectionError`, so for as long as this
                # branch did not exist, every TLS verification failure was
                # treated as a transient fault and retried the full budget —
                # four handshakes and 3.5 s of backoff to re-learn that a
                # certificate the appliance will present again is still not
                # verifiable. A rejected certificate is a stable answer, the
                # same reasoning `LES-035` applies to 418 and every 4xx one
                # layer up. Order here is load-bearing, not stylistic.
                #
                # `exc` is deliberately not interpolated, for the reason the
                # branch below spells out — and the fix is named instead,
                # because the bare class name alone ("SSLError") covers a
                # no-SAN certificate, an untrusted CA, an expired certificate
                # and a protocol mismatch, which do not share a remedy.
                raise TlsVerificationError(
                    f"{method} {path} failed: SSLError — TLS certificate verification "
                    "failed; not retried, because a rejected certificate is a stable "
                    "answer rather than a transient fault. `--ca-bundle` is the fix "
                    "for an untrusted or self-signed CA; a certificate with no Subject "
                    "Alternative Name cannot be verified under any trust anchor and "
                    "needs `--insecure`."
                ) from exc
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                if attempt > self._max_retries:
                    # Deliberately not `f"...: {exc}"` — `requests`' own
                    # exception message embeds the full request URL
                    # (appliance hostname *and* query string, e.g. a `filter`
                    # value), and this message flows into `extract.py`'s
                    # `Failed.detail` and from there into `manifest.jsonl`,
                    # which must never contain either (manifest.py's own
                    # stated invariant). Only the exception's type and the
                    # bare path are operator-facing here; `exc` is still
                    # chained via `from exc` for anyone inspecting the
                    # exception object directly (never printed by cli.py).
                    raise ConnectionFailedError(
                        f"{method} {path} failed after {attempt} attempt(s): {type(exc).__name__}"
                    ) from exc
                time.sleep(self._backoff_base * (2 ** (attempt - 1)))
                continue

            accepted = (
                {expected_status} if isinstance(expected_status, int) else set(expected_status)
            )
            if resp.status_code in accepted:
                return resp

            if resp.status_code == _RETRYABLE_STATUS and attempt <= self._max_retries:
                resp.close()
                time.sleep(self._backoff_base * (2 ** (attempt - 1)))
                continue

            body = _try_parse_json(resp)
            exc = exception_for_status(resp.status_code, resp.reason, body)
            resp.close()
            raise exc

    # --- API-key hygiene (§2 Phase 5; the only endpoint-specific logic
    # client.py owns — everything else lives in catalog.py/extract.py) -----

    def list_api_keys(self, *, include_revoked: bool = False) -> list[dict]:
        """`GET /axapi/apikeys`. Only prefixes come back — `axapi` never
        returns a full key after creation, and neither does `xtract`.
        """
        params = {"include_revoked": "true"} if include_revoked else None
        resp = self.request("GET", "/axapi/apikeys", params=params, expected_status=200)
        body = _json_object_or_raise(resp, "/axapi/apikeys")
        keys = body.get("keys")
        if not isinstance(keys, list):
            raise MalformedResponseError(
                f"/axapi/apikeys: expected 'keys' to be a list, got {type(keys).__name__}: {keys!r}",
                status_code=resp.status_code,
                body=body,
            )
        return keys

    def revoke_api_key(self, key_id: int) -> dict:
        """`DELETE /axapi/apikeys/<key_id>`."""
        resp = self.request("DELETE", f"/axapi/apikeys/{key_id}", expected_status=200)
        return _json_object_or_raise(resp, f"/axapi/apikeys/{key_id}")


def _try_parse_json(resp: requests.Response) -> object | None:
    try:
        return resp.json()
    except ValueError:
        return None


def _json_object_or_raise(resp: requests.Response, path: str) -> dict:
    """Parse `resp`'s body as a JSON object, raising a typed
    `MalformedResponseError` — never a bare `ValueError`/`JSONDecodeError` —
    if it isn't valid JSON or isn't a JSON object.

    Every caller that needs a specific field out of an otherwise-successful
    response goes through this first, so a malformed body always surfaces as
    an `XtractAPIError` subclass: `request()`'s 401-recovery loop only
    catches that base, and a bare `KeyError`/`ValueError` escaping from
    inside response parsing would skip recovery entirely.
    """
    body = _try_parse_json(resp)
    if not isinstance(body, dict):
        raise MalformedResponseError(
            f"{path}: expected a JSON object response body, got "
            f"{type(body).__name__ if body is not None else 'unparseable/non-JSON content'}",
            status_code=resp.status_code,
        )
    return body


def _token_from_response(resp: requests.Response, path: str) -> str:
    """Extract the `token` field from a login/refresh response body,
    raising `MalformedResponseError` rather than a bare `KeyError` when it's
    absent or not a string.
    """
    body = _json_object_or_raise(resp, path)
    token = body.get("token")
    if not isinstance(token, str):
        raise MalformedResponseError(
            f"{path}: response body had no string 'token' field: {body!r}",
            status_code=resp.status_code,
            body=body,
        )
    return token


def iter_stream_body(resp: requests.Response, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
    """Chunked iteration helper for a `stream=True` response, so a caller
    (the pcap path in `extract.py`, WO-5) never has to know the
    `requests`-specific incantation. `client.py` itself never materializes a
    streamed body in memory — this just forwards `iter_content`.
    """
    return resp.iter_content(chunk_size=chunk_size)
