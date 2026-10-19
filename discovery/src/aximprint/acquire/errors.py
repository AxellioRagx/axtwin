"""errors — typed exceptions for `axapi` response status codes.

Every status code `client.py` maps comes from `docs/AXAPI_CONTRACT.md`. The
one status that matters most for callers is `418`: it is a *stable answer*
("this window/stream produced no data"), not a transient failure, so it gets
its own type (`NoDataError`) and `client.py` never retries it (`LES-035`).

Scope, deliberately narrow, same discipline as `timeutil.py`:
  * No I/O, no `requests` import — this module only defines exception types
    and the pure function that builds their message from a parsed response
    body. `client.py` is the only module that touches the network and that
    imports this module to raise these types.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "ConnectionFailedError",
    "ForbiddenError",
    "InvalidPayloadError",
    "MalformedResponseError",
    "NoDataError",
    "NotCompleteError",
    "NotFoundError",
    "ServerError",
    "TlsVerificationError",
    "UnauthorizedError",
    "XtractAPIError",
    "XtractError",
]


class XtractError(Exception):
    """Common root for every error type `xtract` raises.

    `XtractAPIError` (below) and every other module's own error root
    (`TimeUtilError`, `WindowsError`, `StreamSpecError`, `CatalogError`,
    `ManifestError`, `CredentialError`, `RunnerError`) are reparented under
    this, alongside whatever more specific base each already had (most are
    also a `ValueError` subclass) — so an existing `except ValueError` call
    site keeps working unchanged, while a new `except XtractError` becomes
    possible.

    Exists so a caller — `cli.py`'s `_run_mapping_errors` — can tell "a known
    xtract error, from any module" apart from "an unexpected internal bug",
    instead of lumping both under one bare `except Exception` last resort.
    """


class XtractAPIError(XtractError):
    """Base class for every error `client.py` raises from an `axapi` response.

    `status_code` is the HTTP status that produced this error (`None` for
    errors synthesized locally, e.g. connection failures). `body` is the
    parsed JSON error envelope when the response carried one and it parsed
    cleanly as a JSON object; `None` otherwise. Both are set on every
    subclass below, not just this base, so callers never have to guess which
    layer of the hierarchy a status arrived at.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class UnauthorizedError(XtractAPIError):
    """HTTP 401 — missing/invalid token or credential mismatch."""


class ForbiddenError(XtractAPIError):
    """HTTP 403 — authenticated, but access denied (e.g. not the key's owner)."""


class NotFoundError(XtractAPIError):
    """HTTP 404 — the named resource does not exist."""


class InvalidPayloadError(XtractAPIError):
    """HTTP 415 — invalid/missing request payload."""


class NoDataError(XtractAPIError):
    """HTTP 418 — the extraction ran and produced no data for the requested
    window/stream. Not a failure: see `LES-035` and `docs/AXAPI_CONTRACT.md`
    §6.1. Callers must treat this as a first-class outcome, never retry it,
    and never lump it in with `UnauthorizedError`/`ServerError` handling.
    """


class NotCompleteError(XtractAPIError):
    """HTTP 425 — the requested (async) task is not complete yet."""


class ServerError(XtractAPIError):
    """HTTP 500 — an error occurred on the appliance. Retried up to the
    client's retry budget before this is raised (LES-035 does not apply
    here: unlike 418, a 500 is a transient-failure signal).
    """


class ConnectionFailedError(XtractAPIError):
    """A connection error or timeout persisted past the retry budget.

    `status_code` is always `None` here — the request never got a response
    to map a status from.
    """


class TlsVerificationError(XtractAPIError):
    """TLS certificate verification failed. Never retried.

    A sibling of `ConnectionFailedError`, not a subclass: that type means "a
    transient fault persisted past the retry budget," and this one is the
    opposite claim — the budget was never spent, because a rejected
    certificate is a *stable answer*. Same reasoning as `NoDataError` and
    `LES-035`, one layer below HTTP: the appliance's answer will not differ
    on attempt four.

    `requests` models this as `SSLError`, a **subclass of its own
    `ConnectionError`**, which is what let it inherit the transient-error
    retry path unnoticed. `client.py` therefore catches it first, before the
    broader tuple — order is load-bearing there, not stylistic.

    `status_code` is always `None`: TLS fails below HTTP, so there is no
    response and never was one.
    """


class AuthenticationError(XtractAPIError):
    """A `401` persisted after one refresh attempt and one re-login attempt
    (JWT mode only). Distinct from `UnauthorizedError` so callers can tell
    "the session recovered" apart from "the session is unrecoverable and the
    run should stop."
    """


class MalformedResponseError(XtractAPIError):
    """A response arrived with the expected (or an otherwise-successful)
    status code, but its body wasn't valid JSON, wasn't a JSON object, or was
    missing a field the caller needed — e.g. `POST /axapi/refresh`'s response
    body, which `docs/AXAPI_CONTRACT.md` §1.2 explicitly declines to assert a
    shape for.

    Raised instead of letting a bare `KeyError`/`ValueError` escape from deep
    inside response parsing: those aren't `XtractAPIError` subclasses, so
    `Client.request()`'s 401-recovery loop (which only catches
    `XtractAPIError`) would miss them entirely, and a caller expecting a
    typed error from this module would see an untyped exception instead.
    """


# Every status this module maps to a distinct exception type. Any status not
# in this table (including an unexpected 2xx, like a 200 from a login
# endpoint that should return 201) falls back to the generic `XtractAPIError`
# base — still typed, still carries status_code/body, just not one of the
# specifically-documented cases.
_STATUS_EXCEPTIONS: dict[int, type[XtractAPIError]] = {
    401: UnauthorizedError,
    403: ForbiddenError,
    404: NotFoundError,
    415: InvalidPayloadError,
    418: NoDataError,
    425: NotCompleteError,
    500: ServerError,
}


def exception_for_status(
    status_code: int, reason: str | None, body: object | None
) -> XtractAPIError:
    """Build the typed exception for `status_code`, using `body` (already
    JSON-decoded by the caller, or `None`/non-dict if decoding failed or the
    body wasn't a JSON object) to compose the message.

    Parses the documented error envelope
    (`{"code", "message", "errors": [{"domain", "reason", "message",
    "instance"}]}`) when present, and the simpler `{"message": ...}` /
    `{"error": ...}` shapes used by a few endpoints (§1.1, §2.2). Degrades
    gracefully to a generic "axapi returned <status> <reason>" message when
    the body is missing, isn't JSON, or isn't a JSON object — a malformed
    error body must never mask the status code itself.
    """
    exc_cls = _STATUS_EXCEPTIONS.get(status_code, XtractAPIError)
    fallback = f"axapi returned {status_code}" + (f" {reason}" if reason else "")

    if not isinstance(body, dict):
        return exc_cls(fallback, status_code=status_code, body=None)

    parts: list[str] = []
    top_message = body.get("message") or body.get("error")
    if top_message:
        parts.append(str(top_message))
    for err in body.get("errors") or []:
        if not isinstance(err, dict):
            continue
        detail = ": ".join(str(err[k]) for k in ("domain", "reason", "message") if err.get(k))
        if detail:
            parts.append(detail)

    message = "; ".join(parts) if parts else fallback
    return exc_cls(message, status_code=status_code, body=body)
