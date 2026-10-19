"""`aximprint acquire {streams,pcap,harvest}` — the D3 wiring in `cli._dispatch_acquire`
(`docs/WORK_ORDER_merge_xtract_acquire.md`).

Deliberately black-box for the "verbatim" claims: a subprocess through `python -m
aximprint.cli acquire ...` is compared against the identical invocation through `python -m
aximprint.acquire.cli ...` directly, rather than calling `cli.main()` in-process and
trusting that nothing about argv handling differs from a real console invocation.
"""
from __future__ import annotations

import re
import subprocess
import sys

import pytest


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", *args], capture_output=True, text=True, timeout=30, check=False,
    )


def test_acquire_pcap_dry_run_is_byte_identical_through_either_entry_point(tmp_path):
    """D3: "preserve each verb's existing flags and exit codes verbatim." `--dry-run`
    with an explicit `--end` needs no credentials and makes no network call (per
    `acquire/cli.py`'s own `_pcap_needs_client`), so this is safe to actually run.
    """
    out = tmp_path / "pcap-out"
    flags = ["pcap", "--streams", "0", "--start", "0", "--end", "60",
             "--interval", "60s", "--dry-run", "--out", str(out)]

    via_aximprint = _run("aximprint.cli", "acquire", *flags)
    via_acquire_direct = _run("aximprint.acquire.cli", *flags)

    assert via_aximprint.returncode == 0, via_aximprint.stderr
    assert via_aximprint.returncode == via_acquire_direct.returncode
    assert via_aximprint.stdout == via_acquire_direct.stdout
    assert via_aximprint.stderr == via_acquire_direct.stderr
    assert not out.exists(), "a dry run must not create --out (neither entry point should)"


@pytest.mark.parametrize("bad_args,expected_code", [
    (["acquire", "notaverb"], 2),          # argparse usage error: unrecognized command
    (["acquire"], 2),                       # required subparser, none given
])
def test_acquire_usage_errors_keep_their_own_exit_code(bad_args, expected_code):
    """`acquire`'s own exit-code table (module docstring: 0/1/2/3/4/130) passes through
    this wrapper unchanged — `_dispatch_acquire` never re-maps anything.
    """
    result = _run("aximprint.cli", *bad_args)
    assert result.returncode == expected_code, result.stderr


def test_acquire_help_shows_acquires_own_flags_verbatim():
    """Not rewritten to say "aximprint acquire" — `_dispatch_acquire` hands the raw argv
    straight to `acquire.cli.main`, which builds its own `argparse.ArgumentParser`
    unchanged; this is what "verbatim" (D3) means structurally, and is checked here so a
    future edit that starts re-parsing/re-declaring acquire's flags in `cli.py` itself
    (the thing `_dispatch_acquire`'s docstring says not to do) would visibly change this.
    """
    result = _run("aximprint.cli", "acquire", "--help")
    assert result.returncode == 0
    assert "{streams,pcap,harvest}" in result.stdout
    assert "--host HOST" in result.stdout


def test_top_level_help_lists_acquire_among_the_choices():
    """`acquire` must appear in the brace list `aximprint --help` prints, not only in the
    epilog prose beneath it.

    This reverses the original wiring, and the reason is worth keeping. `acquire` is
    intercepted before this parser exists, so a subparser for it was judged decorative
    dead code and the verb was mentioned in the epilog instead. A user then ran
    `aximprint --help`, read `{ingest,features,status,synth}`, and reported `acquire` as
    an unlisted subcommand — which is the correct reading of that output. The choice list
    is where a CLI says what its verbs are; prose underneath does not substitute for
    membership in it.
    """
    result = _run("aximprint.cli", "--help")
    assert result.returncode == 0
    # Matched against the brace list's CONTENTS rather than the whole literal string. The
    # literal was `{ingest,features,status,synth,acquire}` until `materialize` landed
    # (2026-08-14), at which point this test failed for a reason that had nothing to do with
    # what it is about — a new verb is not a regression in acquire's wiring. Asserting
    # membership keeps the claim and drops the coupling to an unrelated list's length.
    #
    # The character class gained `-` on 2026-09-05, when `install-cast` became the first
    # hyphenated verb and the pattern stopped matching the brace list AT ALL — so this failed
    # with `no rendered choice list`, i.e. the same wrong-reason failure the paragraph above
    # describes, arriving through the CHARACTER SET instead of through the list's length. A
    # membership assertion has to be able to spell every verb the CLI can have.
    choices = re.search(r"\{([a-z,\-]+)\}", result.stdout)
    assert choices, f"no rendered choice list at all:\n{result.stdout}"
    assert "acquire" in choices.group(1).split(","), (
        "acquire must be one of argparse's rendered choices, not epilog-only:\n"
        + result.stdout
    )


def test_acquire_is_intercepted_before_the_parser_that_advertises_it():
    """Registering `acquire` as a choice must not put its argv through this parser.

    The stub declares none of acquire's flags (`_dispatch_acquire`'s docstring forbids
    re-declaring them — that is what drifts), so if interception ever regressed and argv
    reached the stub, the giveaway would be `--help` printing the *stub's* usage line
    instead of acquire's own. Assert the verbatim passthrough survives the new choice
    entry: `aximprint acquire --help` renders acquire's parser, flags and all.
    """
    result = _run("aximprint.cli", "acquire", "--help")
    assert result.returncode == 0
    assert "{streams,pcap,harvest}" in result.stdout
    assert "--host HOST" in result.stdout
    # The stub's own usage string, which would appear if argparse had handled this.
    assert "run `aximprint acquire --help`" not in result.stdout


def test_a_plain_ingest_invocation_never_imports_the_acquire_family(tmp_path):
    """D4: `requests` is gated behind the optional `[acquire]` extra, so nothing about
    running the discovery pipeline may require it. Structural, not just "the code looks
    lazy": import `aximprint.cli` fresh (a subprocess, so `sys.modules` starts empty) and
    assert no `aximprint.acquire*` module was pulled in as a side effect.
    """
    probe = (
        "import sys, aximprint.cli\n"
        "hit = [m for m in sys.modules if m.startswith('aximprint.acquire')]\n"
        "print(','.join(hit))\n"
        "raise SystemExit(1 if hit else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, f"acquire-family modules loaded: {result.stdout!r}"


def test_missing_acquire_extra_fails_loud_naming_the_pip_install_command(monkeypatch):
    """D4: "aximprint acquire must fail loud naming the missing extra... catch it and
    emit a message that names `pip install -e ".[acquire]"`." — an ImportError traceback
    is not acceptable.

    Simulated by knocking out `requests` itself (`sys.modules["requests"] = None`,
    documented Python import-system behavior: a `None` entry makes the next `import` of
    that exact name raise `ModuleNotFoundError`) — the REAL shape of a missing
    `[acquire]` extra, since `requests` is the one thing the extra actually supplies
    (`aximprint.acquire` ships INSIDE `aximprint`, always present regardless of the
    extra). This test used to knock out `aximprint.acquire.cli` instead, which turns out
    to simulate a DIFFERENT, `aximprint`-rooted failure — see
    `test_an_aximprint_rooted_modulenotfounderror_propagates_uncaught` below, which
    proves `_dispatch_acquire` must NOT relabel that one as a packaging state.

    Also evicts every already-imported `aximprint.acquire*` module from `sys.modules`
    (`monkeypatch.delitem`, restorable) before patching `requests` — load-bearing, not
    defensive. In a FULL suite run, `tests/acquire/` collects (and imports
    `aximprint.acquire.cli` -> ... -> `client.py`'s own `import requests`) before this
    file ever runs, so `import requests` has ALREADY executed and is cached; patching
    `sys.modules["requests"] = None` at that point has NO effect on the already-completed
    chain — `from aximprint.acquire.cli import main` just returns the cached module
    instantly, `_dispatch_acquire` never raises anything, and `acquire_main(["streams"])`
    runs FOR REAL (this failed exactly this way once, with a real `SSLError` from an
    actual `POST /axapi/login` attempt, in a full-suite run — caught before landing).
    Evicting the whole `aximprint.acquire*` family forces a genuine fresh re-import, so
    `client.py`'s `import requests` genuinely re-executes and genuinely hits the `None`
    sentinel. This test passing in isolation is not evidence it is correct — see
    `LES-031`'s general point about mutation/ordering sensitivity in exactly this shape;
    run the FULL suite, not just this file, to trust this test.
    """
    import sys as _sys

    from aximprint import cli

    for name in [n for n in _sys.modules if n == "aximprint.acquire" or n.startswith("aximprint.acquire.")]:
        monkeypatch.delitem(_sys.modules, name, raising=False)
    monkeypatch.setitem(_sys.modules, "requests", None)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["acquire", "streams"])
    message = str(exc_info.value)
    assert 'pip install -e ".[acquire]"' in message
    assert "acquire" in message.lower()


def test_an_aximprint_rooted_modulenotfounderror_propagates_uncaught(monkeypatch):
    """`aximprint.acquire` ships INSIDE `aximprint` — the `[acquire]` extra supplies only
    `requests`. A `ModuleNotFoundError` whose `.name` is rooted in `aximprint` (a typo'd
    import inside an otherwise fully-installed `acquire` package, for instance) is a real
    bug that `pip install -e ".[acquire]"` cannot fix, and `_dispatch_acquire` must not
    tell the operator it can — it must propagate instead of being relabeled as a missing
    extra.

    Simulated the same way the "missing extra" test above used to be simulated
    (`sys.modules["aximprint.acquire.cli"] = None`) — which is exactly the point: that
    simulation's resulting exception is `aximprint`-rooted (`exc.name ==
    "aximprint.acquire.cli"`), not `requests`-rooted, so under the OLD blanket `except
    ImportError` it was silently mislabeled as "the extra is not installed" even though
    no real absence of `requests` was involved.
    """
    import sys as _sys

    from aximprint import cli

    monkeypatch.setitem(_sys.modules, "aximprint.acquire.cli", None)
    with pytest.raises(ModuleNotFoundError) as exc_info:
        cli.main(["acquire", "streams"])
    assert exc_info.value.name == "aximprint.acquire.cli"
