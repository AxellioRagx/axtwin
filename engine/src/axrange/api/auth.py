"""Who may write (`api/Spec.md` A8, `docs/design/ui-shell-and-auth.md`).

The console holds no credential; the API decides, per deployment, whom to
trust. `AXRANGE_AUTH` names one or more modes, comma-separated:

* ``none``  -- every write is accepted (the dev default when no token is set;
  `axrange serve` warns at startup).
* ``proxy`` -- a write is accepted when it carries an identity header
  (`X-Forwarded-User`, else `X-Auth-Request-Email`) AND the TCP peer is in
  `AXRANGE_TRUSTED_PROXY_CIDRS` (default loopback only). The CIDR check is
  what makes the header trustworthy: anyone who can reach the bind can set a
  header, so a header from an untrusted peer is treated as absent.
* ``token`` -- the existing `Authorization: Bearer <AXRANGE_API_TOKEN>`
  check, unchanged. Setting the token with `AXRANGE_AUTH` unset selects this
  mode, as it always has.

Pure functions over strings: nothing here imports FastAPI, so the policy is
unit-testable and `app.py`'s dependency stays a thin dispatcher.
"""
from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

SETTING_AUTH = "AXRANGE_AUTH"
SETTING_TRUSTED_PROXY_CIDRS = "AXRANGE_TRUSTED_PROXY_CIDRS"
SETTING_SIGNIN_URL = "AXRANGE_SIGNIN_URL"

MODE_NONE = "none"
MODE_PROXY = "proxy"
MODE_TOKEN = "token"
MODES = (MODE_NONE, MODE_PROXY, MODE_TOKEN)

DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.1/32,::1/128"
#: The headers a trusted proxy names the user in, first match wins.
IDENTITY_HEADERS = ("x-forwarded-user", "x-auth-request-email")
#: The name `whoami` reports when writes need no identity.
ANONYMOUS = "anonymous"
#: The name a bearer-token write is recorded under: the token names nobody.
TOKEN_USER = "token"


class AuthConfigError(ValueError):
    """`AXRANGE_AUTH` / `AXRANGE_TRUSTED_PROXY_CIDRS` is present but unusable.
    Raised while the policy is resolved, i.e. at `create_app`, never on a
    request -- `axrange serve` reports it and exits like any other
    `ApiSettingsError` (it is one; see `settings.py`)."""


@dataclass(frozen=True)
class Identity:
    """Who a request is, once a mode accepted it."""
    user: str
    mode: str


@dataclass(frozen=True)
class AuthPolicy:
    modes: tuple[str, ...]
    api_token: str
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    signin_url: str

    @property
    def mode_label(self) -> str:
        return ",".join(self.modes)

    @property
    def open(self) -> bool:
        return MODE_NONE in self.modes


def parse_modes(raw: str, api_token: str) -> tuple[str, ...]:
    """`AXRANGE_AUTH` -> the modes, validated. Empty -> `token` when a token
    is set, else `none`."""
    names = [m.strip().lower() for m in (raw or "").split(",") if m.strip()]
    if not names:
        return (MODE_TOKEN,) if api_token else (MODE_NONE,)
    unknown = [m for m in names if m not in MODES]
    if unknown:
        raise AuthConfigError(
            f"{SETTING_AUTH}={raw!r}: unknown mode(s) {', '.join(unknown)}; "
            f"the modes are {', '.join(MODES)} (comma-separated to combine)"
        )
    modes = tuple(dict.fromkeys(names))  # order kept, duplicates dropped
    if MODE_NONE in modes and len(modes) > 1:
        raise AuthConfigError(
            f"{SETTING_AUTH}={raw!r}: `none` accepts every write, so combining "
            "it with another mode would silently disable that mode; name one"
        )
    if MODE_TOKEN in modes and not api_token:
        raise AuthConfigError(
            f"{SETTING_AUTH}={raw!r} names `token` but AXRANGE_API_TOKEN is "
            "unset: no write could ever be accepted"
        )
    return modes


def parse_trusted_proxies(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    text = raw if raw and raw.strip() else DEFAULT_TRUSTED_PROXY_CIDRS
    out = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            raise AuthConfigError(
                f"{SETTING_TRUSTED_PROXY_CIDRS}: {item!r} is not an IP network "
                f"(e.g. 127.0.0.1/32 or 10.0.0.0/8; default {DEFAULT_TRUSTED_PROXY_CIDRS})"
            ) from None
    return tuple(out)


def resolve_policy(*, auth: str, api_token: str, trusted_proxy_cidrs: str, signin_url: str) -> AuthPolicy:
    return AuthPolicy(
        modes=parse_modes(auth, api_token),
        api_token=api_token or "",
        trusted_proxies=parse_trusted_proxies(trusted_proxy_cidrs),
        signin_url=(signin_url or "").strip(),
    )


def _peer_is_trusted(policy: AuthPolicy, client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False  # a unix socket or a test client's placeholder name
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr in net for net in policy.trusted_proxies)


def identify(policy: AuthPolicy, *, client_host: str | None, headers: Mapping[str, str]) -> Identity | None:
    """The identity a request carries under the policy, or None (-> 401).

    `headers` is looked up by lower-case name (Starlette's `request.headers`
    is case-insensitive). Token first -- a matching bearer is the strongest
    claim and the CLI sends nothing else -- then the proxy header from a
    trusted peer; `none` stands alone (`parse_modes`), so an open deployment
    answers `anonymous` for everyone.
    """
    if MODE_TOKEN in policy.modes and policy.api_token:
        presented = headers.get("authorization") or ""
        # Constant-time compare (code-review finding #4, 2026-07-07).
        if secrets.compare_digest(presented, f"Bearer {policy.api_token}"):
            return Identity(user=TOKEN_USER, mode=MODE_TOKEN)
    if MODE_PROXY in policy.modes and _peer_is_trusted(policy, client_host):
        for name in IDENTITY_HEADERS:
            user = (headers.get(name) or "").strip()
            if user:
                return Identity(user=user, mode=MODE_PROXY)
    if MODE_NONE in policy.modes:
        return Identity(user=ANONYMOUS, mode=MODE_NONE)
    return None


def refusal(policy: AuthPolicy) -> str:
    """The 401 text: names what the deployment expects, not what it got."""
    parts = []
    if MODE_TOKEN in policy.modes:
        parts.append("missing or invalid API token")
    if MODE_PROXY in policy.modes:
        parts.append("sign in required: no identity header from a trusted proxy")
    return "; ".join(parts) or "writes are refused"
