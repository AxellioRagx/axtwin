"""Tests for aximprint.acquire.auth.

No test here touches a real TTY, a real environment variable, or the real
`~/.config/xtract/config.toml` OR `~/.config/aximprint/config.toml` — `resolve_
credential`'s `env=`, `stdin_isatty=`, `prompt_api_key=`, and `prompt_password=`
parameters exist exactly so these tests can substitute fakes for all of them, and
every test in the "default config-path resolution" section below (`monkeypatch.
setenv("HOME", str(tmp_path))`) redirects `Path.home()` itself before calling
`resolve_credential`/`resolve_host` with `config_path=None` — the one case those
injection points don't already cover, since resolving the DEFAULT path is precisely
what's under test there. Every other test in this file passes `config_path=`
explicitly, which bypasses `Path.home()` entirely and needs no redirection.
"""

import os
import stat
from pathlib import Path

import pytest

from aximprint.acquire.auth import (
    Credential,
    CredentialError,
    CredentialResolutionError,
    InsecureCredentialFileError,
    resolve_credential,
    resolve_host,
)

# --- Credential: never leaks the raw secret via formatting ------------------


def _api_key_cred():
    return Credential.from_api_key("sk-super-secret-value-12345")


def _password_cred():
    return Credential.from_password("alice", "hunter2-the-password")


@pytest.mark.parametrize("cred_factory", [_api_key_cred, _password_cred])
def test_repr_str_format_are_all_redacted(cred_factory):
    cred = cred_factory()
    assert repr(cred) == "Bearer ****"
    assert str(cred) == "Bearer ****"
    assert format(cred, "") == "Bearer ****"
    assert f"{cred}" == "Bearer ****"


@pytest.mark.parametrize("cred_factory", [_api_key_cred, _password_cred])
def test_no_formatting_output_contains_the_raw_secret(cred_factory):
    cred = cred_factory()
    secrets = ["sk-super-secret-value-12345", "hunter2-the-password"]
    outputs = [repr(cred), str(cred), format(cred, ""), f"{cred}", "%s" % (cred,)]  # noqa: UP031
    for output in outputs:
        for secret in secrets:
            assert secret not in output


def test_reveal_is_the_only_way_to_get_the_api_key():
    cred = _api_key_cred()
    assert cred.is_api_key is True
    assert cred.reveal() == "sk-super-secret-value-12345"


def test_reveal_is_the_only_way_to_get_the_password_pair():
    cred = _password_cred()
    assert cred.is_api_key is False
    assert cred.reveal() == ("alice", "hunter2-the-password")


def test_credential_rejects_both_api_key_and_password():
    with pytest.raises(ValueError):
        Credential(api_key="k", username="u", password="p")


def test_credential_rejects_neither():
    with pytest.raises(ValueError):
        Credential()


# --- resolve_credential: precedence table -----------------------------------
#
# Each test supplies every source at and below the one under test, and
# asserts the highest-precedence one wins — proving the order, not just that
# each source individually works.


def test_api_key_file_flag_wins_over_everything(tmp_path):
    key_file = tmp_path / "flag.key"
    key_file.write_text("flag-key\n")
    key_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text('default_profile = "prod"\n\n[profile.prod]\nuser = "bob"\n')

    cred = resolve_credential(
        api_key_file=key_file,
        env={"XTRACT_API_KEY": "env-key"},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
        prompt_password=lambda _: pytest.fail("prompt_password must not be called"),
    )
    assert cred.is_api_key
    assert cred.reveal() == "flag-key"


def test_env_wins_over_config_and_prompt(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_profile = "prod"\n\n[profile.prod]\nuser = "bob"\n')

    cred = resolve_credential(
        api_key_file=None,
        env={"XTRACT_API_KEY": "env-key"},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
        prompt_password=lambda _: pytest.fail("prompt_password must not be called"),
    )
    assert cred.is_api_key
    assert cred.reveal() == "env-key"


def test_config_wins_over_prompt(tmp_path):
    key_file = tmp_path / "profile.key"
    key_file.write_text("profile-key\n")
    key_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "prod"\n\n[profile.prod]\nhost = "10.0.0.5"\n'
        f'api_key_file = "{key_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
    )
    assert cred.is_api_key
    assert cred.reveal() == "profile-key"


def test_config_value_containing_a_hash_is_not_treated_as_a_comment(tmp_path):
    # Regression: the original hand-rolled parser stripped everything after the
    # first `#` on a line, including inside a quoted string, so a legitimate
    # path or value containing `#` was rejected. tomllib parses it correctly.
    key_file = tmp_path / "key#1.key"
    key_file.write_text("hash-key\n")
    key_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "prod"\n\n[profile.prod]\napi_key_file = "{key_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
    )
    assert cred.reveal() == "hash-key"


def test_trailing_comment_after_a_value_is_still_stripped(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_profile = "prod"  # the one we use\n\n[profile.prod]\nuser = "bob"\n')

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_password=lambda _: "pw",
    )
    assert cred.reveal() == ("bob", "pw")


def test_malformed_toml_names_the_file(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("this is not = = valid toml\n")

    with pytest.raises(CredentialResolutionError) as exc:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=config_path,
            stdin_isatty=lambda: False,
        )
    assert str(config_path) in str(exc.value)
    assert "invalid TOML" in str(exc.value)


def test_config_user_profile_prompts_for_password_only_on_tty(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_profile = "prod"\n\n[profile.prod]\nuser = "alice"\n')

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: True,
        prompt_password=lambda prompt: "typed-password",
    )
    assert not cred.is_api_key
    assert cred.reveal() == ("alice", "typed-password")


def test_prompt_is_last_resort_and_only_fires_on_tty(tmp_path):
    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=tmp_path / "does-not-exist.toml",
        stdin_isatty=lambda: True,
        prompt_api_key=lambda prompt: "prompted-key",
    )
    assert cred.is_api_key
    assert cred.reveal() == "prompted-key"


def test_no_tty_no_credential_raises_typed_error_no_prompt_no_hang(tmp_path):
    def _fail_if_called(*_a, **_kw):
        pytest.fail("no prompt may be invoked when stdin is not a TTY")

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=tmp_path / "does-not-exist.toml",
            stdin_isatty=lambda: False,
            prompt_api_key=_fail_if_called,
            prompt_password=_fail_if_called,
        )
    # The error names every location searched, so a user can act on it.
    message = str(exc_info.value)
    assert "--api-key-file" in message
    assert "XTRACT_API_KEY" in message
    assert "config profile" in message
    assert "interactive prompt" in message


def test_error_names_every_location_searched_even_with_config_present(tmp_path):
    """A config file that exists but has no usable profile still degrades to
    the next step, and the final error still lists every location tried.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_profile = "empty"\n')  # no matching [profile.empty]

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=config_path,
            stdin_isatty=lambda: False,
        )
    assert str(config_path) in str(exc_info.value)


# --- deprecated-name fallback: XTRACT_API_KEY / ~/.config/xtract -------------------
#
# 2026-08-10 user ruling: retire the `xtract` name from the user-visible surface,
# with a read-only fallback for the credential locations. `~/.config/xtract/
# config.toml` genuinely exists, mode 0600, with real credentials, on hosts that
# already ran `aximprint acquire` before this round — every test below that touches
# DEFAULT config-path resolution redirects `HOME` to `tmp_path` first
# (`monkeypatch.setenv`), so none of them can ever read, write, or even stat the
# real `~/.config/xtract/` or `~/.config/aximprint/` on whatever host runs this
# suite. Tests that only need the ENV VAR precedence pass an explicit, deliberately
# nonexistent `config_path=` so `resolve_credential` never reaches — and therefore
# never needs to redirect — `Path.home()` at all (verified by the LAZY resolution
# order: the config step is checked only after the env var step returns).


def test_new_env_var_resolves_with_no_warning(tmp_path, capsys):
    cred = resolve_credential(
        api_key_file=None,
        env={"AXIMPRINT_API_KEY": "new-env-key"},
        config_path=tmp_path / "unused.toml",
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
        prompt_password=lambda _: pytest.fail("prompt_password must not be called"),
    )
    assert cred.is_api_key
    assert cred.reveal() == "new-env-key"
    assert capsys.readouterr().err == ""


def test_old_env_var_resolves_via_fallback_and_warns(tmp_path, capsys):
    cred = resolve_credential(
        api_key_file=None,
        env={"XTRACT_API_KEY": "old-env-key"},
        config_path=tmp_path / "unused.toml",
        stdin_isatty=lambda: True,
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
        prompt_password=lambda _: pytest.fail("prompt_password must not be called"),
    )
    assert cred.is_api_key
    assert cred.reveal() == "old-env-key"
    stderr = capsys.readouterr().err
    assert "XTRACT_API_KEY" in stderr
    assert "AXIMPRINT_API_KEY" in stderr
    assert "deprecated" in stderr.lower()


def test_new_env_var_wins_over_old_env_var_no_warning(tmp_path, capsys):
    cred = resolve_credential(
        api_key_file=None,
        env={"AXIMPRINT_API_KEY": "new-env-key", "XTRACT_API_KEY": "old-env-key"},
        config_path=tmp_path / "unused.toml",
        stdin_isatty=lambda: True,
    )
    assert cred.reveal() == "new-env-key"
    assert capsys.readouterr().err == ""


def _write_profile_config(path, *, key_file) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'default_profile = "prod"\n\n[profile.prod]\napi_key_file = "{key_file}"\n')


def test_new_config_present_resolves_with_no_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / "new.key"
    key_file.write_text("new-config-key\n")
    key_file.chmod(0o600)
    _write_profile_config(tmp_path / ".config" / "aximprint" / "config.toml", key_file=key_file)

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=None,  # exercises the DEFAULT (HOME-redirected) resolution
        stdin_isatty=lambda: False,
    )
    assert cred.reveal() == "new-config-key"
    assert capsys.readouterr().err == ""


def test_old_config_present_new_absent_resolves_via_fallback_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / "old.key"
    key_file.write_text("old-config-key\n")
    key_file.chmod(0o600)
    # No ~/.config/aximprint/ directory at all -- not even an empty one.
    _write_profile_config(tmp_path / ".config" / "xtract" / "config.toml", key_file=key_file)

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=None,
        stdin_isatty=lambda: False,
    )
    assert cred.reveal() == "old-config-key"
    stderr = capsys.readouterr().err
    new_path = tmp_path / ".config" / "aximprint" / "config.toml"
    old_path = tmp_path / ".config" / "xtract" / "config.toml"
    assert str(old_path) in stderr
    assert str(new_path) in stderr
    assert "deprecated" in stderr.lower()


def test_deprecation_warning_fires_once_per_process_not_once_per_resolution(
    tmp_path, monkeypatch, capsys
):
    """One CLI invocation resolves the config twice; the operator hears once.

    `resolve_host` and `resolve_credential` each resolve the config location
    independently and on purpose, so a single `aximprint acquire ...` run calls
    `_resolve_default_config_path` twice. Before the dedupe in
    `_warn_deprecated_config_path` that printed the identical deprecation
    notice twice on every command.

    Caught by running the real CLI against a real appliance, NOT by this suite:
    every test here redirects `HOME` to its own `tmp_path`, so each one only
    ever saw a single resolution and the duplication was structurally invisible.
    That is why this test drives BOTH entry points, the way the CLI does.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / "old.key"
    key_file.write_text("old-config-key\n")
    key_file.chmod(0o600)
    old_path = tmp_path / ".config" / "xtract" / "config.toml"
    _write_profile_config(old_path, key_file=key_file)

    resolve_host(profile=None)
    resolve_credential(api_key_file=None, env={}, config_path=None, stdin_isatty=lambda: False)

    stderr = capsys.readouterr().err
    assert stderr.lower().count("deprecated") == 1, (
        f"expected exactly one deprecation notice across both resolutions, got:\n{stderr}"
    )
    assert str(old_path) in stderr


def test_new_config_wins_over_old_config_no_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    new_key_file = tmp_path / "new.key"
    new_key_file.write_text("new-config-key\n")
    new_key_file.chmod(0o600)
    old_key_file = tmp_path / "old.key"
    old_key_file.write_text("old-config-key\n")
    old_key_file.chmod(0o600)
    _write_profile_config(tmp_path / ".config" / "aximprint" / "config.toml", key_file=new_key_file)
    _write_profile_config(tmp_path / ".config" / "xtract" / "config.toml", key_file=old_key_file)

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=None,
        stdin_isatty=lambda: False,
    )
    assert cred.reveal() == "new-config-key"
    assert capsys.readouterr().err == ""


def test_both_absent_error_names_all_four_searched_locations(tmp_path, monkeypatch):
    """The detail most likely to be missed (per the fix round's own framing): the
    `searched` list must name BOTH the current and the deprecated form for EACH of
    the env var and the config file, or this message lies to whoever has
    credentials sitting in the location it fails to mention.
    """
    monkeypatch.setenv("HOME", str(tmp_path))  # empty tmp_path -- nothing exists anywhere

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=None,
            stdin_isatty=lambda: False,
        )
    message = str(exc_info.value)
    new_config_path = tmp_path / ".config" / "aximprint" / "config.toml"
    old_config_path = tmp_path / ".config" / "xtract" / "config.toml"
    assert "AXIMPRINT_API_KEY" in message
    assert "XTRACT_API_KEY" in message
    assert str(new_config_path) in message
    assert str(old_config_path) in message


def test_resolve_host_reads_the_new_config_location(tmp_path, monkeypatch):
    """`resolve_host` shares `_resolve_default_config_path` with `resolve_credential`
    — this is the direct proof it inherited the SAME fallback, not just the
    credential path (nothing in the coordinator's own test list names `resolve_host`
    explicitly, but the fallback would be incomplete, and `--host` resolution would
    silently break for an old-config-only user, if only `resolve_credential` got it).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".config" / "aximprint" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('default_profile = "prod"\n\n[profile.prod]\nhost = "10.0.0.9"\n')

    assert resolve_host() == "10.0.0.9"


def test_resolve_host_falls_back_to_the_old_config_location(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".config" / "xtract" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('default_profile = "prod"\n\n[profile.prod]\nhost = "10.0.0.9"\n')

    assert resolve_host() == "10.0.0.9"
    assert "deprecated" in capsys.readouterr().err.lower()


# --- API key file permission check ------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission bits only")
def test_group_readable_key_file_is_rejected(tmp_path):
    key_file = tmp_path / "insecure.key"
    key_file.write_text("secret-key\n")
    key_file.chmod(0o644)

    with pytest.raises(InsecureCredentialFileError) as exc_info:
        resolve_credential(api_key_file=key_file)
    assert "0600" in str(exc_info.value) or "600" in str(exc_info.value)


# --- password_file (config-profile, non-interactive) ------------------------


def test_password_file_resolves_with_no_tty_and_never_prompts(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'default_profile = "corpapcn"\n\n[profile.corpapcn]\n'
        f'user = "svc-extract"\npassword_file = "{password_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        # This is the entire point: no TTY, and yet it resolves.
        stdin_isatty=lambda: False,
        prompt_password=lambda _: pytest.fail("prompt_password must not be called"),
        prompt_api_key=lambda _: pytest.fail("prompt_api_key must not be called"),
    )
    assert not cred.is_api_key
    assert cred.reveal() == ("svc-extract", "hunter2-the-password")


def test_password_file_trailing_newline_is_stripped(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n\n")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\nuser = "svc-extract"\n'
        f'password_file = "{password_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: False,
    )
    _, password = cred.reveal()
    assert password == "hunter2-the-password"


def test_empty_password_file_raises(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\nuser = "svc-extract"\n'
        f'password_file = "{password_file}"\n'
    )

    with pytest.raises(CredentialResolutionError):
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=config_path,
            stdin_isatty=lambda: False,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission bits only")
def test_group_readable_password_file_is_rejected(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n")
    password_file.chmod(0o644)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\nuser = "svc-extract"\n'
        f'password_file = "{password_file}"\n'
    )

    with pytest.raises(InsecureCredentialFileError) as exc_info:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=config_path,
            stdin_isatty=lambda: False,
        )
    message = str(exc_info.value)
    assert str(password_file) in message
    assert "0600" in message or "600" in message


def test_password_file_without_user_is_a_config_error(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\n'
        f'password_file = "{password_file}"\n'
    )

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolve_credential(
            api_key_file=None,
            env={},
            config_path=config_path,
            stdin_isatty=lambda: False,
        )
    assert "corpapcn" in str(exc_info.value)


def test_api_key_file_still_wins_over_password_file_in_same_profile(tmp_path):
    key_file = tmp_path / "corpapcn.key"
    key_file.write_text("api-key-value\n")
    key_file.chmod(0o600)

    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\nuser = "svc-extract"\n'
        f'api_key_file = "{key_file}"\npassword_file = "{password_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: False,
    )
    assert cred.is_api_key
    assert cred.reveal() == "api-key-value"


def test_password_file_credential_never_leaks_in_formatting(tmp_path):
    password_file = tmp_path / "corpapcn.pass"
    password_file.write_text("hunter2-the-password\n")
    password_file.chmod(0o600)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'default_profile = "corpapcn"\n\n[profile.corpapcn]\nuser = "svc-extract"\n'
        f'password_file = "{password_file}"\n'
    )

    cred = resolve_credential(
        api_key_file=None,
        env={},
        config_path=config_path,
        stdin_isatty=lambda: False,
    )
    outputs = [repr(cred), str(cred), format(cred, ""), f"{cred}", "%s" % (cred,)]  # noqa: UP031
    for output in outputs:
        assert "hunter2-the-password" not in output


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission bits only")
def test_owner_only_key_file_is_accepted(tmp_path):
    key_file = tmp_path / "secure.key"
    key_file.write_text("secret-key\n")
    key_file.chmod(0o600)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    cred = resolve_credential(api_key_file=key_file)
    assert cred.reveal() == "secret-key"


def test_missing_key_file_raises_credential_resolution_error(tmp_path):
    with pytest.raises(CredentialResolutionError):
        resolve_credential(api_key_file=tmp_path / "nope.key")


# --- a config that moved while the paths written inside it did not -----------------
#
# Reported from a real host (2026-08-11). The operator took the deprecation warning's
# advice literally — `mv ~/.config/xtract ~/.config/aximprint` — and the next run failed
# on a bare `No such file or directory` for a `password_file` still written as an
# absolute path under the old directory. Nothing resolves those paths relative to the
# config's own directory, so moving the config silently orphans every one of them, and
# the deprecation warning that had been narrating the migration goes quiet at exactly
# that point (the new location now resolves). HOME is redirected in each test below,
# per this module's note above: none may see the host's real config directories.


def _moved_config_home(monkeypatch, tmp_path) -> tuple[Path, Path]:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / ".config" / "xtract", tmp_path / ".config" / "aximprint"


def test_secret_orphaned_by_a_config_move_names_the_path_to_try(monkeypatch, tmp_path):
    old_dir, new_dir = _moved_config_home(monkeypatch, tmp_path)
    new_dir.mkdir(parents=True)  # moved to here...
    assert not old_dir.exists()  # ...and gone from there

    with pytest.raises(CredentialResolutionError) as excinfo:
        resolve_credential(api_key_file=old_dir / "corp.pass")

    message = str(excinfo.value)
    assert str(new_dir / "corp.pass") in message, (
        "the error must name the path the operator should try, not just the one that "
        f"failed:\n{message}"
    )


def test_no_move_hint_while_the_old_config_dir_is_still_in_use(monkeypatch, tmp_path):
    """Not migrated yet — the old directory is still there and still warning on every
    run. A missing secret then means a genuinely wrong path, and volunteering "you
    moved it" would be a confident wrong guess at the one moment the operator is
    already confused.
    """
    old_dir, new_dir = _moved_config_home(monkeypatch, tmp_path)
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)

    with pytest.raises(CredentialResolutionError) as excinfo:
        resolve_credential(api_key_file=old_dir / "corp.pass")
    assert "no longer exists" not in str(excinfo.value)


def test_no_move_hint_for_a_secret_outside_the_config_dirs(monkeypatch, tmp_path):
    _old_dir, new_dir = _moved_config_home(monkeypatch, tmp_path)
    new_dir.mkdir(parents=True)

    with pytest.raises(CredentialResolutionError) as excinfo:
        resolve_credential(api_key_file=tmp_path / "elsewhere" / "corp.pass")
    assert "no longer exists" not in str(excinfo.value)


def test_deprecation_warning_names_the_paths_written_inside_the_config(
    tmp_path, monkeypatch, capsys
):
    """The warning that tells an operator to move the config must also tell them what
    the move will break — otherwise following its advice is what causes the next
    failure, which is exactly how this was reported.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / "old.key"
    key_file.write_text("old-config-key\n")
    key_file.chmod(0o600)
    _write_profile_config(tmp_path / ".config" / "xtract" / "config.toml", key_file=key_file)

    resolve_credential(api_key_file=None, env={}, config_path=None, stdin_isatty=lambda: False)

    stderr = capsys.readouterr().err
    assert "password_file" in stderr
    assert "api_key_file" in stderr
    # Still ONE notice, not two: `test_deprecation_warning_fires_once_per_process...`
    # counts this word, so the added sentence must not reintroduce it.
    assert stderr.lower().count("deprecated") == 1, stderr


# --- shared error hierarchy ----------------------------------------------------


def test_credential_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(CredentialError, XtractError)
