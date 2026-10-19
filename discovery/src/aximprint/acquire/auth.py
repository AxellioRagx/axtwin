"""auth — the `Credential` type and the search order that resolves one.

**No secret ever reaches `argv`.** There is no `--password`/`--api-key` flag
anywhere in `xtract`, on purpose: a value in `argv` is visible in `ps` output
and shell history to every user on the box. Flags take *paths*
(`--api-key-file <path>`), never secret values.

Resolution order, first hit wins (`docs/WORK_ORDER_client.md` Phase 1):

  1. `--api-key-file <path>` (read here; the CLI layer only passes the path)
  2. `AXIMPRINT_API_KEY` environment variable — falls back to the deprecated
     `XTRACT_API_KEY` when the new name is unset (2026-08-10 user ruling:
     retire the `xtract` name from the user-visible surface, with a fallback
     for the credential locations). A warning names both, fired only when
     the deprecated name is what actually resolved — never when the new one
     did, and never when neither is set.
  3. `~/.config/aximprint/config.toml`, profile-keyed (`[profile.<name>]`,
     plus an optional top-level `default_profile`) — falls back to the
     deprecated `~/.config/xtract/config.toml` if the new location does not
     exist, same warning discipline as step 2. **Read-only fallback**: this
     module never copies, moves, `chmod`s, or rewrites the deprecated file —
     the operator relocates it when they choose.
  4. an interactive `getpass` prompt for the API key — **only** when stdin is
     a TTY; never prompts and never hangs in a non-interactive run
  5. none of the above resolved anything → `CredentialResolutionError`,
     naming every location searched — steps 2 and 3 each name BOTH the
     current and the deprecated name/location, regardless of which one (if
     either) actually held something, so the message is never misleading
     about where credentials could have come from

A `[profile.<name>]` section resolves in this order, first hit wins:

  a. `api_key_file` — a path, read the same as flag #1.
  b. `user` + `password_file` — a username paired with a path to a file
     holding the password. This is CorpAPCN's only unattended path: the
     appliance is username/password-only (no API keys), so a batched or
     cron'd run needs a way to authenticate with no TTY at all — unlike
     step 4/d below, this path is **not** TTY-gated. `password_file` with
     no `user` in the same profile is a config error, named by profile.
  c. `user` alone (no `password_file`) — the matching password is never
     stored and is prompted for at resolution time, TTY-gated the same as
     step 4.

Scope, deliberately narrow: no network I/O (the resolved `Credential` is
handed to `client.py`, which is the only module that talks to `axapi`).
"""

from __future__ import annotations

import getpass
import os
import stat
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from aximprint.acquire.errors import XtractError

__all__ = [
    "Credential",
    "CredentialError",
    "CredentialResolutionError",
    "InsecureCredentialFileError",
    "resolve_credential",
    "resolve_host",
]

_REDACTED = "Bearer ****"

# The current API-key env var name and config directory, and their DEPRECATED
# read-only fallbacks (2026-08-10 user ruling: retire the `xtract` name from the
# user-visible surface, with a fallback for the credential locations — this is
# a deliberate, ratified behavior change, not a rename-in-place: the old names are
# read, never migrated, and a real credential file may exist at the old location on
# a host that has not moved it yet). See `_resolve_default_config_path` and
# `resolve_credential`'s env-var precedence for where these are actually used.
_API_KEY_ENV = "AXIMPRINT_API_KEY"
_DEPRECATED_API_KEY_ENV = "XTRACT_API_KEY"
_CONFIG_DIR_NAME = "aximprint"
_DEPRECATED_CONFIG_DIR_NAME = "xtract"


class CredentialError(XtractError):
    """Base class for every error this module raises."""


class InsecureCredentialFileError(CredentialError):
    """An API key file or password file is group- or world-readable — the
    same check `ssh` performs on a private key. Refused rather than read,
    because a long-lived credential in a world-readable file is a standing
    leak, not a hypothetical one.
    """


class CredentialResolutionError(CredentialError):
    """No credential could be resolved from any configured source."""


class Credential:
    """An `axapi` credential: either a bearer API key, or a username/password
    pair to exchange for a JWT via `POST /axapi/login`.

    `repr()`, `str()`, and `"{}".format(...)` (and therefore any f-string,
    `logging` call, or `pprint` of a container holding one of these) all
    return the literal string `"Bearer ****"` — never the secret, never even
    a hint of its length. The raw value is reachable through exactly one
    accessor, `reveal()`, named so a grep for "reveal" finds every place a
    secret can leave this object. `client.py` calls `reveal()` exactly at
    header-construction / login-payload-construction time and nowhere else.
    """

    __slots__ = ("_api_key", "_is_api_key", "_password", "_username")

    def __init__(self, *, api_key: str | None = None, username: str | None = None, password: str | None = None):
        if api_key is not None:
            if username is not None or password is not None:
                raise ValueError("Credential takes api_key OR username+password, not both")
            if not api_key:
                raise ValueError("api_key must be non-empty")
        elif username is not None and password is not None:
            if not username or not password:
                raise ValueError("username and password must both be non-empty")
        else:
            raise ValueError("Credential requires either api_key= or username=+password=")
        self._is_api_key = api_key is not None
        self._api_key = api_key
        self._username = username
        self._password = password

    @classmethod
    def from_api_key(cls, api_key: str) -> Credential:
        return cls(api_key=api_key)

    @classmethod
    def from_password(cls, username: str, password: str) -> Credential:
        return cls(username=username, password=password)

    @property
    def is_api_key(self) -> bool:
        """True for an API-key credential, False for a username/password one.

        Not a secret itself — used to pick the auth flow (client.py's login
        vs. no-login path), never to reconstruct the credential value.
        """
        return self._is_api_key

    def reveal(self) -> str | tuple[str, str]:
        """The one accessor for the raw secret material.

        Returns the bare API key string for an API-key credential, or the
        `(username, password)` tuple for a password credential. Every other
        method on this object is deliberately incapable of exposing either.
        """
        if self._is_api_key:
            return self._api_key
        return (self._username, self._password)

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __format__(self, format_spec: str) -> str:
        return _REDACTED


def _moved_config_hint(missing: Path) -> str:
    """Extra context for a secret file left behind when the config dir was moved.

    `password_file` / `api_key_file` are read exactly as written in
    `config.toml`; nothing resolves them relative to the config's own directory.
    So an operator who takes `_warn_deprecated_config_path`'s advice and moves
    `~/.config/xtract/` to `~/.config/aximprint/` breaks every absolute path
    written inside the file they just moved. What they see next is the least
    informative moment in the whole flow: the config now resolves at its new
    home, so the deprecation warning that had been explaining things falls
    silent, and the run fails on a bare `No such file or directory` naming a
    directory that no longer exists.

    Deliberately narrow — fires only when the missing path is under the old
    directory, the old directory is GONE, and the new one exists. That is
    precisely the "already moved it" state; a host that simply mistyped a path,
    or that has not migrated at all (old directory still present, still
    warning), gets nothing added.
    """
    home = Path.home()
    old_dir = home / ".config" / _DEPRECATED_CONFIG_DIR_NAME
    new_dir = home / ".config" / _CONFIG_DIR_NAME
    if old_dir.exists() or not new_dir.exists():
        return ""
    try:
        tail = missing.relative_to(old_dir)
    except ValueError:
        return ""
    return (
        f" — {old_dir} no longer exists but {new_dir} does, so this looks like a "
        f"config that moved while the paths written inside it did not; try "
        f"{new_dir / tail}"
    )


def _read_secret_file(path: str | os.PathLike, *, what: str) -> str:
    """Read a secret from `path`, refusing a group-/world-readable file.

    Shared by the API-key-file and password-file readers — `what` (e.g.
    `"API key"`, `"password"`) names the secret in every error message so
    each caller's errors stay specific without duplicating the check.

    The permission check is POSIX-only (skipped on platforms with no
    meaningful owner/group/other mode bits, e.g. Windows) — mirrored by
    `tests/acquire/test_auth.py`'s use of `skipif(os.name != "posix")`.
    """
    resolved = Path(path).expanduser()
    try:
        st = resolved.stat()
    except OSError as exc:
        raise CredentialResolutionError(
            f"cannot read {what} file {resolved}: {exc}{_moved_config_hint(resolved)}"
        ) from exc

    if os.name == "posix":
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise InsecureCredentialFileError(
                f"{what} file {resolved} is group- or world-readable (mode "
                f"{oct(mode)}); required mode is 0600 (owner read/write only) — "
                f"run `chmod 600 {resolved}`"
            )

    value = resolved.read_text().strip()
    if not value:
        raise CredentialResolutionError(f"{what} file {resolved} is empty")
    return value


def _read_key_file(path: str | os.PathLike) -> str:
    return _read_secret_file(path, what="API key")


def _read_password_file(path: str | os.PathLike) -> str:
    return _read_secret_file(path, what="password")


def _warn_deprecated_env_var() -> None:
    print(
        f"aximprint acquire: warning — the {_DEPRECATED_API_KEY_ENV} environment "
        f"variable is deprecated; the current name is {_API_KEY_ENV}. Support for "
        f"{_DEPRECATED_API_KEY_ENV} will be removed — rename it when convenient.",
        file=sys.stderr,
    )


_warned_deprecated_config_paths: set[Path] = set()


def _warn_deprecated_config_path(old: Path, new: Path) -> None:
    """Warn once per process per deprecated path, not once per resolution.

    `resolve_host` and `resolve_credential` each resolve the config location
    independently and on purpose — a host is not a secret and needs none of the
    credential path's mode-checking or TTY-gating — so a single CLI invocation
    calls `_resolve_default_config_path` twice and, before this dedupe, printed
    the identical deprecation notice twice on every command.

    Found by running the real CLI against a real appliance, NOT by the suite:
    every unit test redirects `HOME` to its own `tmp_path`, so each saw exactly
    one resolution and the duplication was structurally invisible to them.

    Deduping here rather than by threading a resolved `config_path` through the
    CLI is deliberate: passing an explicit `config_path=` puts
    `resolve_credential` on its single-path branch, whose `searched` list names
    ONE location instead of the four it must disclose when nothing resolves.
    Fixing the noise that way would have regressed the error message.

    Keyed by `old` rather than a bare flag so a caller that legitimately
    resolves against two different deprecated paths still hears about both;
    tests stay isolated for free, since each redirects `HOME` elsewhere.
    """
    if old in _warned_deprecated_config_paths:
        return
    _warned_deprecated_config_paths.add(old)
    print(
        f"aximprint acquire: warning — reading config from the deprecated {old}; "
        f"the current location is {new}. Support for {old} will be removed — move "
        "your config.toml there when convenient (it is never copied or modified "
        f"automatically). When you do, check the paths written INSIDE it: "
        f"`password_file` and `api_key_file` are read exactly as written, so "
        f"either one pointing into {old.parent} keeps pointing there after the "
        "move.",
        file=sys.stderr,
    )


def _resolve_default_config_path() -> tuple[Path, Path, Path]:
    """The config.toml location to actually read, and the two candidates it chose
    between: `(path_to_use, new_path, old_path)`.

    `path_to_use` is `new_path` (`~/.config/aximprint/config.toml`) if it exists;
    else `old_path` (`~/.config/xtract/config.toml`) as a DEPRECATED, READ-ONLY
    fallback — this function never copies, moves, `chmod`s, or rewrites either
    file, and never will (2026-08-10 user ruling: the operator relocates their
    config when THEY choose, not automatically); else `new_path` again,
    unresolved, so a caller that checks `.exists()` on the result correctly
    reports "nothing configured" with no third case to handle. Warns to stderr
    (`_warn_deprecated_config_path`) exactly when `old_path` is what gets
    returned — never when the new location resolves, and never when neither
    exists (nothing was actually read, so there is nothing to warn about).

    Returns the two candidates too, not just the winner, so a caller building a
    "searched" list for an error message (`resolve_credential`) can name BOTH
    locations regardless of which one — if either — actually held something;
    naming only the winner would silently omit the other from an error message
    at exactly the moment that error fires because NEITHER location had
    anything.

    Deliberately LAZY at the call site, not memoized or called eagerly at
    import time: `Path.home()` and both `.exists()` checks run fresh on every
    call, so (a) a test can redirect `HOME` and see it take effect immediately,
    and (b) a caller that only invokes this once it actually needs the config
    profile (`resolve_credential`, after `--api-key-file` and the env var have
    both already missed) never fires the deprecation warning for a file that
    was never actually consulted.
    """
    home = Path.home()
    new_path = home / ".config" / _CONFIG_DIR_NAME / "config.toml"
    old_path = home / ".config" / _DEPRECATED_CONFIG_DIR_NAME / "config.toml"
    if new_path.exists():
        return new_path, new_path, old_path
    if old_path.exists():
        _warn_deprecated_config_path(old_path, new_path)
        return old_path, new_path, old_path
    return new_path, new_path, old_path


def _load_config(config_path: Path) -> dict:
    """Parse `config.toml` with the stdlib TOML reader.

    `tomllib` requires the file be opened in binary mode — it decodes UTF-8
    itself. Malformed TOML is re-raised as a `CredentialResolutionError` naming
    the file, so a typo in a config the user wrote reads as a config problem
    rather than an unhandled parser traceback.
    """
    try:
        with config_path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise CredentialResolutionError(f"{config_path}: invalid TOML — {exc}") from exc


def resolve_host(
    *,
    config_path: str | os.PathLike | None = None,
    profile: str | None = None,
) -> str | None:
    """The `host` for a config profile, or `None` if there isn't one.

    Separate from `resolve_credential` on purpose: a host is not a secret, so
    it needs none of that function's mode-checking, redaction or TTY-gating,
    and the CLI resolves the two independently (an explicit `--host` beats the
    profile's, whatever the credential came from).

    Returns `None` — never raises — when the config file is absent, names no
    profile, or the selected profile has no `host` key. A malformed config
    still raises `CredentialResolutionError` via `_load_config`, since that is
    a real error the operator needs to see rather than a missing-value case.
    """
    if config_path is None:
        resolved, _new_path, _old_path = _resolve_default_config_path()
    else:
        resolved = Path(config_path)
    if not resolved.exists():
        return None
    config = _load_config(resolved)
    profile_name = profile or config.get("default_profile")
    if not profile_name:
        return None
    section = config.get("profile", {}).get(profile_name)
    if not isinstance(section, dict):
        return None
    host = section.get("host")
    return host if isinstance(host, str) and host else None


def _credential_from_config(
    config_path: Path,
    profile: str | None,
    prompt_password: Callable[[str], str],
    stdin_isatty: Callable[[], bool],
) -> Credential | None:
    if not config_path.exists():
        return None
    config = _load_config(config_path)
    profile_name = profile or config.get("default_profile")
    if not profile_name:
        return None
    section = config.get("profile", {}).get(profile_name)
    if not section:
        return None

    if "api_key_file" in section:
        return Credential.from_api_key(_read_key_file(section["api_key_file"]))

    if "password_file" in section:
        if "user" not in section:
            raise CredentialResolutionError(
                f"profile {profile_name!r} sets password_file but no user; "
                "password_file requires a matching user"
            )
        username = section["user"]
        password = _read_password_file(section["password_file"])
        return Credential.from_password(username, password)

    if "user" in section:
        if not stdin_isatty():
            # No way to obtain the password without a TTY; fall through to
            # the caller's next step rather than hanging or guessing.
            return None
        username = section["user"]
        password = prompt_password(f"axapi password for {username}: ")
        if password:
            return Credential.from_password(username, password)
        return None

    return None


def resolve_credential(
    *,
    api_key_file: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | os.PathLike | None = None,
    profile: str | None = None,
    stdin_isatty: Callable[[], bool] | None = None,
    prompt_api_key: Callable[[str], str] | None = None,
    prompt_password: Callable[[str], str] | None = None,
) -> Credential:
    """Resolve a `Credential` via the search order documented at module top.

    All the non-value parameters (`env`, `stdin_isatty`, `prompt_api_key`,
    `prompt_password`) default to the real environment/`getpass`/stdin and
    exist as injection points so `tests/acquire/test_auth.py` never touches a real
    TTY, environment, or home directory (redirect `HOME` — e.g.
    `monkeypatch.setenv("HOME", str(tmp_path))` — for the handful of tests that
    exercise the DEFAULT config-path resolution itself; every other test bypasses
    it entirely via an explicit `config_path=`).
    """
    env = os.environ if env is None else env
    stdin_isatty = (lambda: sys.stdin.isatty()) if stdin_isatty is None else stdin_isatty
    prompt_api_key = getpass.getpass if prompt_api_key is None else prompt_api_key
    prompt_password = getpass.getpass if prompt_password is None else prompt_password

    searched: list[str] = []

    searched.append("--api-key-file")
    if api_key_file is not None:
        return Credential.from_api_key(_read_key_file(api_key_file))

    searched.append(f"{_API_KEY_ENV} (or deprecated {_DEPRECATED_API_KEY_ENV}) environment variable")
    if env.get(_API_KEY_ENV):
        return Credential.from_api_key(env[_API_KEY_ENV])
    if env.get(_DEPRECATED_API_KEY_ENV):
        _warn_deprecated_env_var()
        return Credential.from_api_key(env[_DEPRECATED_API_KEY_ENV])

    # Resolved HERE, not eagerly at the top of the function the way the single-
    # path version of this line used to be (see `_resolve_default_config_path`'s
    # own docstring for why): a config-path lookup this late never fires the
    # deprecation warning for a file that a higher-precedence source
    # (--api-key-file / the env var, both checked above) already made irrelevant.
    if config_path is None:
        resolved_config_path, new_config_path, old_config_path = _resolve_default_config_path()
        searched.append(f"config profile ({new_config_path} or deprecated {old_config_path})")
    else:
        resolved_config_path = Path(config_path)
        searched.append(f"config profile ({resolved_config_path})")
    from_config = _credential_from_config(resolved_config_path, profile, prompt_password, stdin_isatty)
    if from_config is not None:
        return from_config

    searched.append("interactive prompt (stdin)")
    if stdin_isatty():
        api_key = prompt_api_key("axapi API key: ")
        if api_key:
            return Credential.from_api_key(api_key)

    raise CredentialResolutionError(
        "no credential found; searched, in order: " + "; ".join(searched)
    )
