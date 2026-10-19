"""The sink registry, `StoreSink`, `EvidenceSink`, and the shared CLI driver.

`WORK_ORDER_egress_sink_unification.md`: the sink contract + registry moved out
of `features/` into a neutral `aximprint.sinks` package (D1); `cmd_ingest` and
`cmd_features` became thin wrappers over one driver (D3, `aximprint.cli._pipeline`);
the evidence log became a sink of its own instead of a store option (D5). This
file covers what is NEW or MOVED: the registry contract itself (moved from
`tests/test_feature_sink.py` — the contract is no longer a `features` concept),
the two new sink implementations, the `--sink NAME=DEST` CLI surface, and the
driver's multi-sink fan-out + partial-failure behavior.

`JsonlSink`-specific behavior (the five artifacts, streaming, the vocabulary
bound) stays in `test_feature_sink.py`; the categorical-vocabulary contract
stays in `test_feature_vocabulary.py`.

Synthetic events and hand-built windows only — a real capture carries PII and
never enters a test (`LES-003`).
"""
import contextlib
import json
import subprocess
import sys

import duckdb
import pytest

# Ensures "jsonl" is registered into `aximprint.sinks`' registry alongside
# "store"/"evidence" — `aximprint.sinks` alone does not register it (see that
# package's own docstring on why), and this file's tests assume all three are
# available regardless of which other test modules pytest has already loaded.
import aximprint.features  # noqa: F401
from aximprint.events import FlowEvent, IdentityEvent
from aximprint.learners import extract_assertions
from aximprint.sinks import WindowSink, get_sink, register
from aximprint.sinks.base import _REGISTRY
from aximprint.windowing import WindowManager
from aximprint.windowing.windows import WindowResult

T0 = 1_754_000_000.0
META = {"source": "synthetic", "plugin": "zeek", "window_seconds": 3600, "subbuckets": 12}
CONN_HEADER = (
    "#separator \\x09\n"
    "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
    "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts\n")


def _zeek_dir(tmp_path, n_windows=2, service="smb", dport=445):
    """A minimal synthetic conn.log + dhcp.log spanning `n_windows` hourly windows.

    The dhcp.log matters beyond realism: without it `identity.jsonl` is empty in
    EVERY run built from this fixture, and an equality check between two empty
    files agrees trivially — `identity.jsonl` was byte-`b""`-equal on both sides
    of `test_multisink_run_matches_three_separate_single_sink_runs` regardless of
    whether identity bindings actually round-tripped correctly. One DHCP lease
    per window (matching this module's own `_windows()` helper, and Q9: a
    binding is emitted on every observation, not only on change) gives that
    comparison — and every other test built on this fixture — something real to
    compare.
    """
    zdir = tmp_path / "zeek"
    zdir.mkdir(parents=True, exist_ok=True)
    rows = [f"{T0 + w * 3600 + 10:.6f}\t10.0.0.21\t51000\t10.0.0.5\t{dport}\ttcp\t{service}"
            f"\t0.2\t500\t1500\tSF\t5\t5" for w in range(n_windows)]
    (zdir / "conn.log").write_text(CONN_HEADER + "\n".join(rows) + "\n")
    dhcp_rows = [json.dumps({"ts": T0 + w * 3600 + 5, "mac": "aa:bb:cc:dd:ee:01",
                             "assigned_addr": "10.0.0.21", "host_name": "ws01",
                             "lease_time": 86400}) for w in range(n_windows)]
    (zdir / "dhcp.log").write_text("\n".join(dhcp_rows) + "\n")
    return zdir


def _windows(n_windows=2):
    """Drive real windowing over synthetic events, for the in-process sink tests."""
    events = []
    for w in range(n_windows):
        base = T0 + w * 3600
        events += [FlowEvent(ts=base + 10, src_ip="10.0.0.21", dst_ip="10.0.0.5",
                             src_port=51000, dst_port=445, proto="tcp", service="smb",
                             duration=0.2, bytes_out=500, bytes_in=1500, pkts_out=5,
                             pkts_in=5, state="SF", state_class="established"),
                   IdentityEvent(ts=base + 5, kind="dhcp", ip="10.0.0.21",
                                mac="aa:bb:cc:dd:ee:01", hostname="ws01",
                                lease_seconds=86400.0)]
    wm = WindowManager()
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == n_windows, "fixture must produce one window per hour"
    return wins


def _table_rows(path, table):
    """Every row of `table`, in a stable (content-derived) order — for comparing
    two DIFFERENT DuckDB files, where a `sorted(rows)` per table is enough
    (row order across files means nothing; row CONTENT is the question)."""
    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    con.close()
    return sorted(rows, key=lambda r: [(x is None, x) for x in r])


def _comparable_tables(path) -> list[str]:
    """Every table in `path`'s schema EXCEPT `NONDETERMINISTIC_TABLES` (`S3`).

    Derived from the schema itself, not a second hand-maintained literal list
    that could silently omit a newly added table (or silently omit `runs` by
    happening not to mention it) — the same discipline
    `features/jsonl.py`'s `WALLCLOCK_FIELD` applies to its one exempt manifest
    key, imported here rather than re-declared.
    """
    from aximprint.store.belief_store import NONDETERMINISTIC_TABLES

    con = duckdb.connect(str(path), read_only=True)
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables ORDER BY table_name"
    ).fetchall()]
    con.close()
    return [n for n in names if n not in NONDETERMINISTIC_TABLES]


# -- registry (moved from test_feature_sink.py: D1 makes this a `sinks`,
#    not a `features`, concept) --------------------------------------------------

def test_unknown_sink_name_fails_loud():
    """Fail closed: an unrecognized sink is rejected, never defaulted through."""
    with pytest.raises(KeyError) as exc:
        get_sink("parquet")
    assert "parquet" in str(exc.value)


def test_all_three_builtin_sinks_are_registered():
    assert {"evidence", "jsonl", "store"} <= set(_REGISTRY)


def test_duplicate_sink_name_is_rejected():
    with pytest.raises(ValueError, match="duplicate sink name"):
        @register
        class Clash(WindowSink):
            name = "store"

            def open(self, destination, run_meta): ...
            def write_window(self, win, assertions): ...
            def close(self): return {}


def test_unnamed_sink_is_rejected():
    with pytest.raises(ValueError, match="must set a name"):
        @register
        class Nameless(WindowSink):
            def open(self, destination, run_meta): ...
            def write_window(self, win, assertions): ...
            def close(self): return {}


def test_get_sink_error_names_the_cause_when_the_defining_package_was_never_imported():
    """`S10`: `aximprint.sinks` alone registers only `store`/`evidence` — `jsonl`
    is declared in `aximprint.features` and is not registered until that
    package (or a submodule of it) is imported somewhere in the process. Run
    in a FRESH subprocess: this test FILE's own top-level `import
    aximprint.features` already makes `get_sink("jsonl")` succeed in-process,
    so the only way to observe the bare-`sinks`-only failure is an interpreter
    that never imports `aximprint.features` at all.
    """
    r = subprocess.run(
        [sys.executable, "-c",
         "import aximprint.sinks; aximprint.sinks.get_sink('jsonl')"],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "no sink 'jsonl'" in r.stderr
    assert "aximprint.features" in r.stderr, \
        "the error must name WHERE the missing sink is defined, not just that it is missing"


# -- `accepts`: the declared sink-specific-option contract (D4, `M3`) ------------

def test_jsonl_sink_declares_vocabulary_in_accepts():
    from aximprint.features.jsonl import JsonlSink
    assert JsonlSink.accepts == frozenset({"vocabulary"})


def test_store_and_evidence_sinks_declare_no_accepts():
    from aximprint.sinks.evidence_sink import EvidenceSink
    from aximprint.sinks.store_sink import StoreSink
    assert StoreSink.accepts == frozenset()
    assert EvidenceSink.accepts == frozenset()


def test_default_accepts_is_empty_for_a_sink_that_declares_none():
    """The base contract's default (`WindowSink.accepts`) is an empty
    frozenset, not `None` or unset — a sink that forgets to declare `accepts`
    must behave exactly like one that explicitly declares it empty."""
    class _Plain(WindowSink):
        name = "test-plain-accepts"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self): return {}

    assert _Plain.accepts == frozenset()


# -- `_parse_sink_specs`: NAME=DEST parsing, in isolation (D3) -------------------
#
# Unit-level companions to the subprocess CLI tests below: same parser, no
# process-spawn cost, and precise about what each malformed shape looks like
# rather than only checking "the CLI exits non-zero" for all of them alike.

def test_parse_sink_specs_parses_multiple_entries_in_order():
    from aximprint.cli import _parse_sink_specs
    assert _parse_sink_specs(["store=a.duckdb", "evidence=b.jsonl"]) == \
        [("store", "a.duckdb"), ("evidence", "b.jsonl")]


def test_parse_sink_specs_rejects_a_bare_name():
    """No '=' at all — the shape of the retired single-destination `--sink` flag
    — is rejected rather than read as a destination-less sink (guessing a
    destination would be a silent default)."""
    from aximprint.cli import _parse_sink_specs
    with pytest.raises(SystemExit, match="NAME=DEST"):
        _parse_sink_specs(["jsonl"])


def test_parse_sink_specs_rejects_an_empty_name():
    from aximprint.cli import _parse_sink_specs
    with pytest.raises(SystemExit, match="NAME=DEST"):
        _parse_sink_specs(["=dest"])


def test_parse_sink_specs_rejects_an_empty_destination():
    from aximprint.cli import _parse_sink_specs
    with pytest.raises(SystemExit, match="NAME=DEST"):
        _parse_sink_specs(["store="])


def test_parse_sink_specs_splits_only_on_the_first_equals():
    """A destination MAY legitimately contain '=' — `partition`, not `split`, so
    only the FIRST '=' separates name from destination."""
    from aximprint.cli import _parse_sink_specs
    assert _parse_sink_specs(["store=/data/a=b.duckdb"]) == \
        [("store", "/data/a=b.duckdb")]


def test_parse_sink_specs_does_not_dedupe_a_name_given_twice():
    """A sink NAME is a registry lookup key, not a per-run uniqueness
    constraint — the parser has no opinion on whether reusing one is a good
    idea. See `test_store_alias_and_an_explicit_sink_of_the_same_name_both_write`
    below for proof the driver actually opens and writes to both destinations,
    not just that the parser preserves both entries."""
    from aximprint.cli import _parse_sink_specs
    assert _parse_sink_specs(["store=a.duckdb", "store=b.duckdb"]) == \
        [("store", "a.duckdb"), ("store", "b.duckdb")]


def test_parse_sink_specs_validates_every_entry_before_returning_any():
    """Fail closed: a malformed entry anywhere in the list rejects the WHOLE
    batch — a well-formed entry earlier in the list must not have been handed
    back (and possibly acted on by a caller) before the bad one is even seen.
    See `test_cli_a_malformed_sink_spec_opens_no_sink_at_all` for the end-to-end
    version, which confirms nothing was actually opened either."""
    from aximprint.cli import _parse_sink_specs
    with pytest.raises(SystemExit, match="NAME=DEST"):
        _parse_sink_specs(["store=a.duckdb", "bogus"])


# -- `--sink NAME=DEST` CLI surface (D3) -----------------------------------------

def test_cli_rejects_an_unknown_sink(tmp_path):
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path),
                        "--sink", f"parquet={tmp_path / 'o'}"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "no sink 'parquet'" in r.stderr


def test_cli_rejects_a_malformed_sink_spec(tmp_path):
    """The older single-name `--sink jsonl` form is rejected, not reinterpreted."""
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path), "--sink", "jsonl"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "NAME=DEST" in r.stderr


def test_cli_features_requires_at_least_one_sink(tmp_path):
    """Neither `--out` nor `--sink` given: fail loud rather than run and write nothing."""
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0
    assert "no sink configured" in r.stderr


def test_cli_rejects_an_empty_sink_name(tmp_path):
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path), "--sink", f"={tmp_path / 'o'}"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "NAME=DEST" in r.stderr


def test_cli_rejects_an_empty_sink_destination(tmp_path):
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path), "--sink", "jsonl="],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "NAME=DEST" in r.stderr


def test_cli_a_malformed_sink_spec_opens_no_sink_at_all(tmp_path):
    """Fail closed end to end: a WELL-FORMED `--sink` ahead of a malformed one on
    the command line must not have already opened its destination by the time the
    malformed one is rejected — `_parse_sink_specs` validates the whole batch
    before `_pipeline` opens anything (unit-level version above)."""
    would_have_been = tmp_path / "should-not-exist"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(tmp_path),
                        "--sink", f"jsonl={would_have_been}",
                        "--sink", "bogus"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "NAME=DEST" in r.stderr
    assert not would_have_been.exists(), \
        "the well-formed sink ahead of the malformed one must not have opened"


# -- `M1`: a repeated destination is rejected, not silently doubled ---------------

def test_open_sinks_rejects_two_specs_resolving_to_the_same_destination(tmp_path):
    """`--store b.duckdb --sink store=b.duckdb` used to exit 0 and write every
    window TWICE into `b.duckdb` — DuckDB does not refuse a second connection
    to a path already open with the same configuration, so nothing else did
    either. Fixed in `_open_sinks` via `_reject_repeated_destination`."""
    from aximprint.cli import _open_sinks

    dest = tmp_path / "b.duckdb"
    with pytest.raises(SystemExit, match="same destination"):
        _open_sinks([("store", str(dest)), ("store", str(dest))], META)
    assert not dest.exists(), "nothing should have opened before the rejection"


def test_open_sinks_rejects_the_same_path_spelled_differently(tmp_path):
    """Resolved, not compared as raw strings: two different SPELLINGS of the
    same file — a clean path and one with a redundant `.` segment — must
    collide too."""
    from aximprint.cli import _open_sinks

    dest = tmp_path / "b.duckdb"
    redundant = f"{tmp_path}/./b.duckdb"
    with pytest.raises(SystemExit, match="same destination"):
        _open_sinks([("store", str(dest)), ("store", redundant)], META)


def test_open_sinks_rejects_a_repeated_destination_across_different_sink_types(tmp_path):
    """`M1` compares destinations across every sink TYPE at once — two DIFFERENT
    sinks racing to write into the identical path corrupt each other exactly
    as badly as the same sink twice would."""
    from aximprint.cli import _open_sinks

    dest = tmp_path / "x.duckdb"
    with pytest.raises(SystemExit, match="same destination"):
        _open_sinks([("store", str(dest)), ("evidence", str(dest))], META)


def test_open_sinks_allows_the_same_sink_name_at_two_different_destinations(tmp_path):
    """The mirror image, pinned so `M1`'s fix cannot regress into over-rejecting:
    two `store=` specs at genuinely different paths must both open fine (see
    `test_store_alias_and_an_explicit_sink_of_the_same_name_both_write` below
    for proof they both actually get written through the CLI)."""
    from aximprint.cli import _open_sinks

    opened = _open_sinks([("store", str(tmp_path / "a.duckdb")),
                          ("store", str(tmp_path / "b.duckdb"))], META)
    assert [n for n, _, _ in opened] == ["store", "store"]
    for _, _, sink in opened:
        sink.abort()


def test_cli_rejects_a_repeated_destination_end_to_end(tmp_path):
    """The full CLI path: `--store` and an explicit `--sink store=` at the SAME
    path must fail loud rather than double every table."""
    dest = tmp_path / "b.duckdb"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "ingest",
                        "--source", str(_zeek_dir(tmp_path, n_windows=1)),
                        "--store", str(dest), "--sink", f"store={dest}"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "same destination" in r.stderr
    assert not dest.exists(), "a rejected run must not have written anything at all"


# -- `M2`: a sink-specific option that reaches no open sink is a hard error ------

def test_vocabulary_unconsumed_by_any_open_sink_fails_loud(tmp_path):
    """`features --sink store=... --vocabulary M` (no `--out`, no `jsonl`) used
    to print "vocabulary pinned to ..." and exit 0 having pinned nothing at
    all — `store` never declares `vocabulary` in `accepts`, so the option
    reached no open sink. Fixed: `_open_sinks` now rejects a run where an
    `open_kwargs` key was never claimed by any sink that actually opened.
    """
    from aximprint.cli import _open_sinks
    from aximprint.store import BeliefStore

    dest = tmp_path / "s.duckdb"
    with pytest.raises(SystemExit, match=r"vocabulary.*store"):
        _open_sinks([("store", str(dest))], META, open_kwargs={"vocabulary": object()})

    # DuckDB creates the file at connect time regardless (confirmed by hand),
    # so its mere existence proves nothing; what matters is that the store was
    # RELEASED via `abort()`, not completed via `close()` — no `runs` row, and
    # the connection is not left dangling (a fresh connection succeeds).
    reopened = BeliefStore(str(dest))
    assert reopened.status()["runs"] == 0, \
        "abort() must not stamp a runs row for a run rejected before writing anything"
    reopened.close()


def test_vocabulary_consumed_by_jsonl_in_a_multisink_run_does_not_raise(tmp_path):
    """The mirror image: as long as ONE open sink declares the option, it is
    consumed and no error is raised, regardless of how many sinks do not."""
    from aximprint.cli import _open_sinks

    opened = _open_sinks(
        [("jsonl", str(tmp_path / "f")), ("store", str(tmp_path / "s.duckdb"))],
        META, open_kwargs={"vocabulary": None})
    assert [n for n, _, _ in opened] == ["jsonl", "store"]
    for _, _, sink in opened:
        sink.abort()


def test_cli_features_vocabulary_unconsumed_by_any_open_sink_fails_loud_end_to_end(tmp_path):
    """Full CLI path for `M2`'s exact reported repro: pin a real manifest, but
    open only `store=` (no `--out`). Must fail loud rather than exit 0 having
    silently pinned nothing while the CLI's own "vocabulary pinned to ..."
    print already claimed otherwise.
    """
    seed_out = tmp_path / "seed"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(_zeek_dir(tmp_path / "a", n_windows=1)),
                        "--out", str(seed_out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    store_b = tmp_path / "store_b.duckdb"
    r = subprocess.run(
        [sys.executable, "-m", "aximprint.cli", "features",
         "--source", str(_zeek_dir(tmp_path / "b", n_windows=1)),
         "--sink", f"store={store_b}",
         "--vocabulary", str(seed_out / "manifest.json")],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "vocabulary" in r.stderr


def test_store_alias_and_an_explicit_sink_of_the_same_name_both_write(tmp_path):
    """A sink named twice with different destinations: `--store` seeds a
    `("store", ...)` entry (D3) and nothing dedupes a SECOND `store=` entry added
    via `--sink` against it. Today that is strictly ADDITIVE — both get their own
    `StoreSink` instance, both actually get opened and written — rather than the
    second silently overriding the first or being rejected as a likely mistake.
    Pinned here as a finding as much as a test: an operator reasonably could
    expect `--sink store=X` to redirect `--store`'s default instead of adding to
    it, and nothing in the CLI's `--help` says which it is.
    """
    zdir = _zeek_dir(tmp_path, n_windows=2)
    alias_store = tmp_path / "alias.duckdb"
    explicit_store = tmp_path / "explicit.duckdb"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "ingest",
                        "--source", str(zdir), "--store", str(alias_store),
                        "--sink", f"store={explicit_store}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    for path in (alias_store, explicit_store):
        con = duckdb.connect(str(path), read_only=True)
        assert con.execute("SELECT count(*) FROM node_features").fetchone()[0] > 0, \
            f"{path} must have been opened and written independently"
        con.close()
    assert _table_rows(alias_store, "node_features") == _table_rows(explicit_store, "node_features")


# -- StoreSink --------------------------------------------------------------------

def test_store_sink_matches_belief_store_row_counts(tmp_path):
    from aximprint.sinks.store_sink import StoreSink

    wins = _windows(2)
    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in wins:
        sink.write_window(win, extract_assertions(win))
    summary = sink.close()

    assert summary["destination"] == str(path)
    assert summary["windows"] == 2
    assert summary["vocabulary"] == {}, "present and empty, never absent (LES-006 in miniature)"
    assert summary["rows"]["node_features"] == sum(len(w.nodes) for w in wins)
    assert summary["rows"]["window_health"] == 2

    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("SELECT count(*) FROM node_features").fetchone()[0] == \
        summary["rows"]["node_features"], "the summary's 'rows' must match what actually landed"
    con.close()


def test_store_sink_rows_summary_counts_only_this_run_not_the_tables_total(tmp_path):
    """Pins the specific claim in `StoreSink`'s own docstring — and Finding #4 of
    `WORK_ORDER_egress_sink_unification.md` ("does `rows` mean the same thing for
    a DuckDB projection as for a JSONL file?"): unlike a fresh `JsonlSink`
    archive, the belief store ACCUMULATES across runs (no forgetting yet, `Q5`),
    so `close()`'s `rows` must report what THIS invocation inserted, never the
    table's running total, across two separate `StoreSink` instances opened at
    the SAME destination."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    first = StoreSink()
    first.open(str(path), META)
    for win in _windows(2):
        first.write_window(win, extract_assertions(win))
    first_summary = first.close()

    second = StoreSink()
    second.open(str(path), META)
    for win in _windows(1):
        second.write_window(win, extract_assertions(win))
    second_summary = second.close()

    con = duckdb.connect(str(path), read_only=True)
    table_total = con.execute("SELECT count(*) FROM node_features").fetchone()[0]
    con.close()

    assert second_summary["rows"]["node_features"] < table_total, \
        "the second run's summary must not have folded in the first run's rows"
    assert (first_summary["rows"]["node_features"] + second_summary["rows"]["node_features"]
           == table_total), "the two runs' summaries must partition the table's actual total"


def test_store_sink_open_declares_no_vocabulary_parameter():
    """D4: a sink that does not ask for an option never receives it."""
    import inspect

    from aximprint.sinks.store_sink import StoreSink
    assert "vocabulary" not in inspect.signature(StoreSink.open).parameters


def test_store_sink_stamps_a_run_row_with_the_build_and_run_meta(tmp_path):
    """Build step 6: the store gets the same run_meta the feature sink gets."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    meta = {**META, "ts_from": None, "ts_to": None}
    sink = StoreSink()
    sink.open(str(path), meta)
    for win in _windows(2):
        sink.write_window(win, extract_assertions(win))
    sink.close()

    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute(
        "SELECT windows, source, plugin, window_seconds, subbuckets, "
        "aximprint_version, code_fingerprint FROM runs").fetchall()
    con.close()
    assert len(rows) == 1, "one run -> one row"
    windows, source, plugin, window_seconds, subbuckets, version, fingerprint = rows[0]
    assert windows == 2
    assert source == "synthetic" and plugin == "zeek"
    assert window_seconds == 3600 and subbuckets == 12
    assert version and len(fingerprint) == 16


def test_belief_store_status_reports_the_runs_table(tmp_path):
    """`S5`: `status()` reports the `runs` table — count, and the last row's
    build/finish stamp — so an operator does not have to query it separately."""
    from aximprint.sinks.store_sink import StoreSink
    from aximprint.store import BeliefStore

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(2):
        sink.write_window(win, extract_assertions(win))
    sink.close()

    store = BeliefStore(str(path))
    status = store.status()
    assert status["runs"] == 1
    assert len(status["last_run_code_fingerprint"]) == 16
    assert status["last_run_git_commit"] is None or isinstance(status["last_run_git_commit"], str)
    assert status["last_run_finished_at"] is not None
    store.close()


def test_belief_store_status_reports_the_last_runs_unparsed_and_unrecognized_counts(tmp_path):
    """Nice-to-have (code review): `status()` surfaces the counts too, parsed
    back into Python objects rather than left as raw JSON strings — an
    operator should not have to already know `runs` has these columns."""
    from aximprint.sinks.store_sink import StoreSink
    from aximprint.store import BeliefStore

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.note_unparsed_log_types({"reporter": 1, "telemetry": 3})
    sink.note_unrecognized_log_types({})
    sink.close()

    store = BeliefStore(str(path))
    status = store.status()
    assert status["last_run_unparsed_log_types"] == {"reporter": 1, "telemetry": 3}
    assert status["last_run_unrecognized_log_types"] == {}
    store.close()


def test_belief_store_status_last_run_unparsed_log_types_is_none_when_never_told(tmp_path):
    from aximprint.sinks.store_sink import StoreSink
    from aximprint.store import BeliefStore

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.close()  # note_unparsed_log_types/note_unrecognized_log_types never called

    store = BeliefStore(str(path))
    status = store.status()
    assert status["last_run_unparsed_log_types"] is None
    assert status["last_run_unrecognized_log_types"] is None
    store.close()


def test_belief_store_status_runs_fields_are_none_before_any_clean_close(tmp_path):
    """A store with real window rows but zero `runs` rows is a real, valid
    state (`Q5`-style accumulation, or a run that never reached `close()`) —
    `status()` must read it as "no evidence", not crash on an empty table."""
    from aximprint.store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    for win in _windows(1):
        store.write_window(win, extract_assertions(win))
    status = store.status()
    assert status["runs"] == 0
    assert status["last_run_code_fingerprint"] is None
    assert status["last_run_git_commit"] is None
    assert status["last_run_finished_at"] is None
    assert status["windows"] == 1, "the window itself is still visible"
    store.close()


def test_store_sink_run_row_only_appears_on_a_clean_close(tmp_path):
    """Mirrors `features/jsonl.py`'s "no manifest = unfinished" signal: a run
    that never reaches `close()` leaves no `runs` row, even though the window
    rows it already wrote are visible."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    win = _windows(1)[0]
    sink.write_window(win, extract_assertions(win))
    # Deliberately never call sink.close(). We read through the sink's own
    # (still-open) connection rather than opening a fresh one — not because a
    # second connection would be refused: DuckDB happily opens a second
    # connection to the same file with the SAME configuration, and BOTH can
    # write (confirmed by hand; that is exactly `M1`'s duplicate-destination
    # mechanism — see `_reject_repeated_destination` in `cli.py`). DuckDB only
    # refuses a second connection on a CONFIGURATION mismatch (e.g. one
    # `read_only=True` while another read/write connection to the same file is
    # open), never merely because a path is already open.
    con = sink._store.con
    assert con.execute("SELECT count(*) FROM node_features").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    con.close()


def test_store_sink_write_before_open_fails_loud():
    from aximprint.sinks.store_sink import StoreSink
    with pytest.raises(RuntimeError, match="before open"):
        StoreSink().write_window(_windows(1)[0], [])


def test_store_sink_close_before_open_fails_loud():
    from aximprint.sinks.store_sink import StoreSink
    with pytest.raises(RuntimeError, match="before open"):
        StoreSink().close()


def test_store_sink_abort_before_open_is_a_safe_no_op():
    from aximprint.sinks.store_sink import StoreSink
    StoreSink().abort()  # must not raise


def test_store_sink_abort_closes_the_connection_without_a_runs_row(tmp_path):
    """`M4`/`M5`: `abort()` releases the DuckDB connection but never calls
    `write_run` — the store carries no evidence a run touching it finished."""
    from aximprint.sinks.store_sink import StoreSink
    from aximprint.store import BeliefStore

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.abort()
    assert sink._store is None, "abort() must release the sink's own reference too"

    reopened = BeliefStore(str(path))
    assert reopened.status()["node_feature_rows"] > 0, "the windows already written survive"
    assert reopened.status()["runs"] == 0, "no runs row — this is a release, not a close"
    reopened.close()


# -- note_unparsed_log_types (docs/WORK_ORDER_zeek_stem_coverage.md D5) ----------

def test_note_unparsed_log_types_default_is_a_safe_no_op():
    """Most sinks have no run-scoped table for this fact — the base contract's
    default must be callable on any of them, offered or not, without raising."""
    from aximprint.sinks.evidence_sink import EvidenceSink

    EvidenceSink().note_unparsed_log_types({"telemetry": 3})  # must not raise
    EvidenceSink().note_unparsed_log_types(None)  # must not raise


def test_store_sink_note_unparsed_log_types_reaches_the_runs_row(tmp_path):
    """D5's counts half: `StoreSink` is the one override, and it must reach
    `runs.unparsed_log_types` — JSON-encoded like every other dict-shaped
    column in this store."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.note_unparsed_log_types({"reporter": 1, "telemetry": 4})
    sink.close()

    con = duckdb.connect(str(path), read_only=True)
    (raw,) = con.execute("SELECT unparsed_log_types FROM runs").fetchone()
    con.close()
    assert json.loads(raw) == {"reporter": 1, "telemetry": 4}


def test_store_sink_runs_row_unparsed_log_types_is_null_when_never_told(tmp_path):
    """`None` means unaudited — the SQL-NULL spelling of "the driver never
    called `note_unparsed_log_types` at all" (a plugin with nothing to report,
    or — as here — a caller that talks to `StoreSink` directly and never routes
    through `cli._pipeline`), never coerced to an empty object that would claim
    "audited, and clean" instead."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.close()  # note_unparsed_log_types deliberately never called

    con = duckdb.connect(str(path), read_only=True)
    (raw,) = con.execute("SELECT unparsed_log_types FROM runs").fetchone()
    con.close()
    assert raw is None


def test_belief_store_write_run_sorts_unparsed_log_types_keys_at_the_json_boundary(tmp_path):
    """`LES-009`'s "sort at the persistence boundary" applied to a JSON column:
    the dict's own key order comes from `ZeekSource.inventory`, which different
    transports populate differently (`belief_store.write_run`'s own comment) —
    so the raw JSON bytes must not depend on the CALLER's insertion order.
    Handed keys in reverse-alphabetical order on purpose, so a passing test
    could not be explained by the input already happening to be sorted."""
    from aximprint.store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    store.write_run(META, {}, windows=0, started_at=0.0, finished_at=1.0,
                    unparsed_log_types={"zeta": 1, "reporter": 2, "alpha": 3})
    (raw,) = store.con.execute("SELECT unparsed_log_types FROM runs").fetchone()
    store.close()
    assert raw == '{"alpha": 3, "reporter": 2, "zeta": 1}'


def test_belief_store_write_run_persists_unrecognized_log_types_independently(tmp_path):
    """Code review `S4`, additive to D5: a SEPARATE column, sorted the same way
    as `unparsed_log_types` but not derived from it by this method — `write_run`
    just persists whatever it is handed for each."""
    from aximprint.store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    store.write_run(META, {}, windows=0, started_at=0.0, finished_at=1.0,
                    unparsed_log_types={"reporter": 1, "telemetry": 1},
                    unrecognized_log_types={"zeta": 1, "alpha": 3})
    raw_unparsed, raw_unrecognized = store.con.execute(
        "SELECT unparsed_log_types, unrecognized_log_types FROM runs").fetchone()
    store.close()
    assert json.loads(raw_unparsed) == {"reporter": 1, "telemetry": 1}
    assert raw_unrecognized == '{"alpha": 3, "zeta": 1}'


def test_belief_store_write_run_unrecognized_log_types_defaults_to_null(tmp_path):
    """A caller that never passes it (every EXISTING call site before `S4`
    landed) must not silently start claiming "audited, nothing left over" —
    `None` (SQL NULL), the same default `unparsed_log_types` already had."""
    from aximprint.store import BeliefStore

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    store.write_run(META, {}, windows=0, started_at=0.0, finished_at=1.0)
    (raw,) = store.con.execute("SELECT unrecognized_log_types FROM runs").fetchone()
    store.close()
    assert raw is None


def test_store_sink_note_unparsed_log_types_round_trips_the_audited_and_clean_case(tmp_path):
    """`{}` (audited, nothing left over) must not collapse into the `None`
    (unaudited) spelling — `LES-006`'s "no evidence" vs "evidence of none"
    split, one layer down at the JSON-column boundary."""
    from aximprint.sinks.store_sink import StoreSink

    path = tmp_path / "s.duckdb"
    sink = StoreSink()
    sink.open(str(path), META)
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.note_unparsed_log_types({})
    sink.close()

    con = duckdb.connect(str(path), read_only=True)
    (raw,) = con.execute("SELECT unparsed_log_types FROM runs").fetchone()
    con.close()
    assert raw is not None and json.loads(raw) == {}


def test_pipeline_wires_unparsed_log_types_into_every_window_and_into_runs(tmp_path):
    """End to end through `cli._pipeline` — the actual driver, not a hand-built
    scenario. A real zeek log directory with one DECLARED-SKIP stem and one
    GENUINELY UNRECOGNIZED stem, spanning several windows: every window's
    health carries the SAME sorted names for both fields (proving window 1
    gets them too, not only whichever window happened to be open once the
    plugin's own answer settled), `unparsed_log_types` carries BOTH stems while
    `unrecognized_log_types` carries only the unrecognized one (code review
    `S4`'s split, proven through the real CLI, not just the plugin directly),
    and `runs` carries both counts exactly once, not mirrored onto every
    window (D5's own reasoning against that shape — see `windowing.windows`'s
    `unparsed_log_types` comment).
    """
    from aximprint.ingest.zeek import plugin as zp
    from aximprint.cli import _pipeline

    zdir = _zeek_dir(tmp_path, n_windows=3)
    skip_stem = sorted(zp._SKIP)[0]
    (zdir / f"{skip_stem}.log").write_text(
        json.dumps({"ts": T0, "note": "a declared skip, not an unrecognized stem"}) + "\n")
    # A stem no build has ever routed into `_PARSERS`/`_SKIP` (mirrors
    # `tests/test_zeek_ingest.py`'s own `_UNKNOWN_STEM`, `LES-086`) — a real
    # JSON-lines Zeek log is enough; the plugin does not care that it sits
    # beside this fixture's own conn/dhcp files.
    (zdir / "__not_a_zeek_stem__.log").write_text(
        json.dumps({"ts": T0, "note": "unrecognized by every build so far"}) + "\n")

    store = tmp_path / "s.duckdb"
    _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
             subbuckets=12, specs=[("store", str(store))])

    con = duckdb.connect(str(store), read_only=True)
    healths = con.execute(
        "SELECT health FROM window_health ORDER BY window_start").fetchall()
    assert len(healths) == 3, "fixture must produce three windows"
    for (raw,) in healths:
        h = json.loads(raw)
        assert h["unparsed_log_types"] == sorted([skip_stem, "__not_a_zeek_stem__"]), \
            "every window must carry the same run-level fact, including the first"
        assert h["unrecognized_log_types"] == ["__not_a_zeek_stem__"], \
            "the declared skip must be excluded from the unrecognized-only field"
    (raw_unparsed, raw_unrecognized) = con.execute(
        "SELECT unparsed_log_types, unrecognized_log_types FROM runs").fetchone()
    con.close()
    assert json.loads(raw_unparsed) == {skip_stem: 1, "__not_a_zeek_stem__": 1}
    assert json.loads(raw_unrecognized) == {"__not_a_zeek_stem__": 1}


# -- M2 (code review): a schema-drift guard, not a migration ---------------------
#
# `CREATE TABLE IF NOT EXISTS` (`belief_store._SCHEMA`) handles a new TABLE and
# silently does nothing for a new COLUMN on one that already exists — a store a
# PREVIOUS build created binds fewer placeholders than the CURRENT build's INSERT
# supplies. `runs` gaining `unparsed_log_types` is the first column ever added to
# an existing table here, so nothing exercised this failure mode before it.

def test_belief_store_refuses_a_store_whose_runs_table_predates_unparsed_log_types(tmp_path):
    """The exact regression measured in code review: a `runs` table built by a
    build that never had `unparsed_log_types` (12 columns, not 13). Must fail
    LOUD, naming the table, at OPEN — not three statements into `write_run`
    with a raw `BinderException` after an hours-long run already committed
    every window.
    """
    import duckdb as _duckdb

    from aximprint.store import BeliefStore

    path = tmp_path / "old.duckdb"
    con = _duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE runs (
          started_at DOUBLE, finished_at DOUBLE, windows INTEGER,
          source VARCHAR, plugin VARCHAR, window_seconds INTEGER, subbuckets INTEGER,
          ts_from DOUBLE, ts_to DOUBLE,
          aximprint_version VARCHAR, code_fingerprint VARCHAR, git_commit VARCHAR)
    """)  # the pre-D5 shape: 12 columns, no unparsed_log_types
    con.close()

    with pytest.raises(RuntimeError, match="runs") as exc:
        BeliefStore(str(path))
    assert "predates this build's schema" in str(exc.value)
    assert "unparsed_log_types" in str(exc.value), \
        "the missing column should be nameable from the message alone"


def test_belief_store_schema_guard_is_generic_over_every_table(tmp_path):
    """Not special-cased to `runs`: ANY table whose live columns disagree with
    what `_SCHEMA` currently declares must be refused — demonstrated here on
    `node_features`, an unrelated table, so the mechanism is proven general
    rather than hardcoded to the one column that happened to break first.
    """
    import duckdb as _duckdb

    from aximprint.store import BeliefStore

    path = tmp_path / "old.duckdb"
    con = _duckdb.connect(str(path))
    con.execute("CREATE TABLE node_features (window_start DOUBLE, entity VARCHAR)")
    con.close()  # missing `features JSON`

    with pytest.raises(RuntimeError, match="node_features"):
        BeliefStore(str(path))


def test_belief_store_schema_guard_is_silent_on_a_fresh_store(tmp_path):
    """The common case, checked explicitly: a store this same build created
    must open with no complaint — the guard compares against THIS build's own
    schema, so a store it just wrote agrees with it trivially."""
    from aximprint.store import BeliefStore

    store = BeliefStore(str(tmp_path / "fresh.duckdb"))
    store.close()
    BeliefStore(str(tmp_path / "fresh.duckdb")).close()  # reopen: still fine


def test_belief_store_schema_guard_closes_the_connection_before_raising(tmp_path, monkeypatch):
    """Fail closed all the way: a rejected store must not leave a dangling
    DuckDB connection behind it — `self.con` closes before the raise, same
    discipline every other fail-loud path in this store already follows.

    `BeliefStore.__init__` never returns the instance to its own caller on
    this path (the constructor itself raises), so there is nothing to read
    `.con` off afterward — this captures the connection object AS IT IS
    CREATED (wrapping `duckdb.connect`, the one call site) and, after the
    expected raise, confirms using it raises `duckdb.ConnectionException`
    ("Connection already closed!") rather than succeeding — a closed
    connection cannot be silently reused by accident, which is the actual
    property under test, not merely "a second `connect()` call works" (`LES-022`:
    a SECOND, same-config connection to the same path is not refused by
    DuckDB even while a first is genuinely still open, so that would not have
    been a reliable check here).
    """
    import duckdb as _duckdb

    from aximprint.store import belief_store as bs_module
    from aximprint.store import BeliefStore

    path = tmp_path / "old.duckdb"
    con = _duckdb.connect(str(path))
    con.execute("CREATE TABLE node_features (window_start DOUBLE)")
    con.close()

    # Prime `_canonical_columns()`'s own cache BEFORE capturing: it is
    # `@functools.lru_cache`d and opens its OWN throwaway `duckdb.connect(":memory:")`
    # the first time anything calls it in this process — if that happened to be
    # deferred to inside this test (test order is not this test's to assume), it
    # would show up in `captured` too and make the count below flaky.
    bs_module._canonical_columns()

    captured: list = []
    real_connect = bs_module.duckdb.connect

    def _capturing_connect(p):
        c = real_connect(p)
        captured.append(c)
        return c

    monkeypatch.setattr(bs_module.duckdb, "connect", _capturing_connect)

    with pytest.raises(RuntimeError):
        BeliefStore(str(path))

    assert len(captured) == 1, "the guard must run against the one connection it opened"
    with pytest.raises(_duckdb.ConnectionException):
        captured[0].execute("SELECT 1")


# -- EvidenceSink (D5) -------------------------------------------------------------

def test_evidence_sink_writes_exactly_what_cmd_ingest_used_to(tmp_path):
    """Byte-identical to hand-rolling the same `EvidenceLog` calls `cmd_ingest`
    made before this sink existed — introducing the sink must not change the
    log's bytes (Boundaries)."""
    from aximprint.sinks.evidence_sink import EvidenceSink
    from aximprint.store import EvidenceLog

    wins = _windows(2)
    via_sink, by_hand = tmp_path / "a.jsonl", tmp_path / "b.jsonl"

    sink = EvidenceSink()
    sink.open(str(via_sink), META)
    total_assertions = 0
    for win in wins:
        assertions = extract_assertions(win)
        sink.write_window(win, assertions)
        total_assertions += len(assertions)
    summary = sink.close()

    log = EvidenceLog(str(by_hand))
    for win in wins:
        assertions = extract_assertions(win)
        log.append({"type": "window_closed", "window_start": win.window_start,
                    "health": win.health})
        log.append_many({"type": "assertion", **a} for a in assertions)

    assert via_sink.read_bytes() == by_hand.read_bytes()
    assert summary["vocabulary"] == {}
    assert summary["windows"] == 2
    assert summary["rows"] == {"window_closed": 2, "assertion": total_assertions}


def test_evidence_sink_open_declares_no_vocabulary_parameter():
    import inspect

    from aximprint.sinks.evidence_sink import EvidenceSink
    assert "vocabulary" not in inspect.signature(EvidenceSink.open).parameters


def test_evidence_sink_write_before_open_fails_loud():
    from aximprint.sinks.evidence_sink import EvidenceSink
    with pytest.raises(RuntimeError, match="before open"):
        EvidenceSink().write_window(_windows(1)[0], [])


def test_evidence_sink_close_before_open_fails_loud():
    from aximprint.sinks.evidence_sink import EvidenceSink
    with pytest.raises(RuntimeError, match="before open"):
        EvidenceSink().close()


def test_evidence_sink_abort_before_open_is_a_safe_no_op():
    from aximprint.sinks.evidence_sink import EvidenceSink
    EvidenceSink().abort()  # must not raise


def test_evidence_sink_abort_closes_the_handle(tmp_path):
    """`M6` made `open()` hold the file handle open across the whole run;
    `M4`/`M5`'s `abort()` is what releases it on a failure surface."""
    from aximprint.sinks.evidence_sink import EvidenceSink

    path = tmp_path / "e.jsonl"
    sink = EvidenceSink()
    sink.open(str(path), META)
    fh = sink._log._fh
    assert not fh.closed
    for win in _windows(1):
        sink.write_window(win, extract_assertions(win))
    sink.abort()
    assert fh.closed
    assert sink._log is None, "abort() must release the sink's own reference too"
    assert path.stat().st_size > 0, "what was written before the abort survives on disk"


def test_evidence_log_alias_matches_the_explicit_sink_form(tmp_path):
    """D5: `--evidence-log PATH` and `--sink evidence=PATH` must be indistinguishable."""
    zdir = _zeek_dir(tmp_path, n_windows=2)

    alias_log = tmp_path / "alias.jsonl"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "ingest",
                        "--source", str(zdir), "--store", str(tmp_path / "a.duckdb"),
                        "--evidence-log", str(alias_log)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    explicit_log = tmp_path / "explicit.jsonl"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "ingest",
                        "--source", str(zdir), "--store", str(tmp_path / "b.duckdb"),
                        "--sink", f"evidence={explicit_log}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # Non-empty FIRST (mirrors `verify_tier.sh`'s own reasoning): `==` on two
    # absent-or-empty files agrees trivially, so a comparison that skipped this
    # would report an equivalence it never actually established.
    assert alias_log.stat().st_size > 0, "fixture produced no evidence at all"
    assert alias_log.read_bytes() == explicit_log.read_bytes()


# -- degenerate windows: zero flows ------------------------------------------------
#
# CLAUDE.md's own recurring-gap list names "a window with zero flows" — every
# sink must treat one as data with an asterisk (§5.4 #5: "a window that saw
# nothing is itself a finding"), the same guarantee `JsonlSink` already has
# (`tests/test_feature_sink.py::test_empty_window_still_emits_a_health_row`).
#
# The two `store` tests below USED TO fail here (`T1`): `BeliefStore.write_window`
# guarded the `assertions` insert with `if assertions:` but not the
# `node_features` / `edge_features` ones two lines above it, and DuckDB's
# `executemany` raises `InvalidInputException` on an empty parameter-set list —
# so a window with zero entities (a network-quiet hour, or one that saw only
# `SensorHealthEvent`s) crashed the store sink outright, and a window with nodes
# but no edges (a taint with no flow this window) got a `node_features` row
# inserted and THEN crashed on the next statement, leaving a stray row with no
# matching `window_health` row. In the unified driver this took down the ENTIRE
# multi-sink run (fail-loud fan-out), not just the store. Fixed in
# `store/belief_store.py::write_window`: both `executemany` calls are now
# guarded the same way `assertions` already was, AND all five statements run in
# one transaction (`S4`) so a genuine failure (not just an empty list) can no
# longer leave a partial window durable — see
# `test_write_window_failure_leaves_no_partial_row_behind` below for that half.

def test_evidence_sink_accepts_a_window_with_no_entities_or_edges_at_all(tmp_path):
    """Parity check: nothing here touches DuckDB, so nothing should choke on an
    empty `executemany`-shaped input — confirms the (now-fixed) defect below was
    specific to `BeliefStore`, not something every sink shared."""
    win = WindowResult(T0, 3600, {}, [], [], {"event_counts": {"SensorHealthEvent": 1}})
    sink = get_sink("evidence")
    sink.open(str(tmp_path / "e.jsonl"), META)
    sink.write_window(win, extract_assertions(win))
    summary = sink.close()
    assert summary["windows"] == 1
    assert summary["rows"] == {"window_closed": 1, "assertion": 0}


def test_store_sink_accepts_a_window_with_no_entities_or_edges_at_all(tmp_path):
    """Regression test for `T1` — NOT introduced by
    `WORK_ORDER_egress_sink_unification.md`. `BeliefStore.write_window`'s body was
    untouched by that work order's diff, and the OLD hardcoded `cmd_ingest` called
    it exactly the same way, so the defect predated the sink registry entirely.

    Before the fix: a window with zero entities (a network-quiet hour, or one
    that saw only `SensorHealthEvent`s) crashed the store sink outright instead
    of recording a health-only row the way `JsonlSink` and `EvidenceSink`
    (previous test) both already did for the identical input.

    Minimal repro that used to raise (no sink involved at all):
        BeliefStore(path).write_window(WindowResult(t, 3600, {}, [], [], {}), [])
        # -> duckdb.InvalidInputException: executemany requires a non-empty
        #    list of parameter sets to be provided

    Fix: guard the two `executemany` calls in `store/belief_store.py::write_window`
    the same way `assertions` already was.
    """
    win = WindowResult(T0, 3600, {}, [], [], {"event_counts": {"SensorHealthEvent": 1}})
    sink = get_sink("store")
    sink.open(str(tmp_path / "s.duckdb"), META)
    sink.write_window(win, extract_assertions(win))  # used to raise before the T1 fix
    summary = sink.close()
    assert summary["windows"] == 1
    assert summary["rows"]["window_health"] == 1
    assert summary["rows"]["node_features"] == 0
    assert summary["rows"]["edge_features"] == 0


def test_store_sink_accepts_a_tainted_host_with_no_flow_this_window(tmp_path):
    """Same class of defect, a WORSE shape: `windowing/windows.py`'s `_node()` is
    reachable from `AlertEvent` handling independent of `_add_flow`, so
    `win.nodes` can be non-empty (a taint) while `win.edges` stays `[]` — the
    alerting log and `conn.log` need not agree on a window, so this is a
    realistic shape, not a contrived one.

    Confirmed by hand before the fix: `node_features` got ONE row inserted
    (non-empty) before the SECOND `executemany` (`edge_features`, `[]`) raised on
    its own empty list — so a real store hit by this was left with a stray
    `node_features` / `tainted_window`-assertion row and NO `window_health` row
    for that window, which is worse than refusing the window outright: nothing
    downstream could tell the window was incomplete without noticing an absence
    rather than an error. The transaction (`S4`) is what makes THIS window
    genuinely atomic — not merely the empty-list guard — so the assertion below
    (a real, non-empty insert on the first statement) is the meaningful case,
    distinct from the fully-empty window in the test above.
    """
    win = WindowResult(T0, 3600, {"10.0.0.99": {"tainted": True, "listen_ports": []}},
                       [], [], {"event_counts": {"AlertEvent": 1}})
    sink = get_sink("store")
    sink.open(str(tmp_path / "s.duckdb"), META)
    sink.write_window(win, extract_assertions(win))  # used to raise before the T1 fix
    summary = sink.close()
    assert summary["windows"] == 1
    assert summary["rows"]["node_features"] == 1
    assert summary["rows"]["edge_features"] == 0


def test_store_sink_accepts_a_window_with_no_profile_evidence_at_all(tmp_path):
    """Mirrors `test_store_sink_accepts_a_window_with_no_entities_or_edges_at_all` one table
    over (`Q29`): `profile_facts`' `executemany` insert must be guarded exactly like the other
    guarded statements (`LES-026`) — most windows carry no profile evidence at all
    (`win.capture_profile == {}`, the dataclass default), and an unguarded empty-list
    `executemany` raises `duckdb.InvalidInputException`.
    """
    win = WindowResult(T0, 3600, {}, [], [], {"event_counts": {"SensorHealthEvent": 1}})
    assert win.capture_profile == {}, "the dataclass default, not a hand-supplied empty dict"
    sink = get_sink("store")
    sink.open(str(tmp_path / "s.duckdb"), META)
    sink.write_window(win, extract_assertions(win))  # would raise without the guard
    summary = sink.close()
    assert summary["rows"]["profile_facts"] == 0


def test_store_sink_persists_profile_facts_with_no_subject_column(tmp_path):
    """`Q29`: `profile_facts` is the one belief-store table with no entity column at all —
    schema-checked directly, not just "the insert didn't crash", so a future column addition
    that quietly reintroduces a subject cannot slip past a mere row-count assertion. Content
    is checked too: the real `_add_profile` -> `_close` -> `write_window` path, not a
    hand-built row, so a labeling bug in `_PROFILE_QUANTILE_POINTS` or `profile_facts_rows`'s
    `unit` lookup would show up here. `n` (code review `S1`) and `unit` (`M3`) are both
    exercised: `n` must be the sample count, not a fifth quantile, and its own unit must read
    `"count"`, never the parameter's `"seconds"`.
    """
    from aximprint.events import ProfileEvent
    from aximprint.store.belief_store import NONDETERMINISTIC_TABLES

    assert "profile_facts" not in NONDETERMINISTIC_TABLES, \
        "a window's profile facts are as pure a function of (events, code build) as any other"

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        ProfileEvent(ts=T0, parameter="ocsp_cache_lifetime", value=604800.0),
        ProfileEvent(ts=T0 + 1, parameter="ocsp_cache_lifetime", value=601200.0),
    ])) + list(wm.finalize())
    assert len(wins) == 1

    path = tmp_path / "s.duckdb"
    sink = get_sink("store")
    sink.open(str(path), META)
    sink.write_window(wins[0], extract_assertions(wins[0]))
    summary = sink.close()

    con = duckdb.connect(str(path), read_only=True)
    columns = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'profile_facts'").fetchall()}
    assert columns == {"window_start", "parameter", "stat", "value", "unit"}, \
        "no subject-shaped column (entity/subject/ip/src/dst) may ever join this table"
    rows = con.execute(
        "SELECT parameter, stat, value, unit FROM profile_facts ORDER BY stat").fetchall()
    con.close()

    assert rows == [
        ("ocsp_cache_lifetime", "n", 2.0, "count"),
        ("ocsp_cache_lifetime", "p10", 601200.0, "seconds"),
        ("ocsp_cache_lifetime", "p50", 604800.0, "seconds"),
        ("ocsp_cache_lifetime", "p90", 604800.0, "seconds"),
        ("ocsp_cache_lifetime", "p99", 604800.0, "seconds"),
    ]
    assert summary["rows"]["profile_facts"] == 5, \
        "one row per (parameter, stat) pair actually inserted -- 4 quantiles + n"


def test_write_window_failure_leaves_no_partial_row_behind(tmp_path):
    """`S4`: the six statements in `BeliefStore.write_window` are one
    transaction — a genuine failure partway through (not merely an empty list,
    which `T1` alone would fix) must roll back everything, not just skip the
    statement that failed. Forces a real DB-level failure on the FIFTH
    statement (`identity_bindings`, after two real, non-empty inserts already
    landed; `profile_facts`, the fourth, `Q29`, is skipped here — this `win` has
    no profile evidence, so its own guard leaves it un-attempted, same as
    `assertions` above it) by handing it a value DuckDB's schema will reject,
    and checks that NOTHING from this window is visible afterward — not the
    `assertions` row a partial-commit-per-statement design would have left
    behind.
    """
    from aximprint.store import BeliefStore

    win = WindowResult(
        T0, 3600,
        {"10.0.0.21": {"listen_ports": []}},
        [{"src": "10.0.0.21", "dst": "10.0.0.5", "proto": "tcp",
          "flows": 1, "bytes": 100, "first_ts": T0, "last_ts": T0}],
        # `count` is declared BIGINT; a non-numeric string here fails the insert
        # only once DuckDB actually tries to bind it — i.e. AFTER the two
        # `executemany` calls above it have already run against the connection.
        [{"change": "opened", "kind": "dhcp", "ip": "10.0.0.21",
          "bound_to": "aa:bb:cc:dd:ee:01", "namespace": "mac",
          "identifier": "mac:aa:bb:cc:dd:ee:01", "scope": "host", "entity": "ent-0001",
          "from_ts": T0, "to_ts": None, "first_seen": T0, "last_seen": T0,
          "lease_seconds": 3600.0, "record_ttl": None,
          "count": "not-a-number"}],
        {"event_counts": {"FlowEvent": 1}})

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    with pytest.raises(Exception):
        store.write_window(win, [])

    assert store.con.execute("SELECT count(*) FROM node_features").fetchone()[0] == 0, \
        "the node row from the FIRST statement must not survive the later failure"
    assert store.con.execute("SELECT count(*) FROM edge_features").fetchone()[0] == 0, \
        "the edge row from the SECOND statement must not survive the later failure"
    assert store.con.execute("SELECT count(*) FROM window_health").fetchone()[0] == 0, \
        "the window_health row (last statement, never reached) must be absent too"
    store.close()


# -- the shared driver: multi-sink fan-out and D4 ---------------------------------

def test_multisink_run_matches_three_separate_single_sink_runs(tmp_path):
    """Validation's acceptance line: one run writing store + evidence + jsonl
    together produces all three, each identical to a single-sink run of the
    same kind."""
    n_windows = 3
    zdir = _zeek_dir(tmp_path, n_windows=n_windows)
    # `profile_facts` (`Q29`) would otherwise be the only table this test compares while
    # EMPTY on both sides -- an equality check that agrees trivially and, per `M1`, is
    # actually the only place two independent writes of the table are compared at all
    # (code review `S8`). One `ocsp` row per window, mirroring `_zeek_dir`'s own
    # one-row-per-window convention for `conn.log`/`dhcp.log` above.
    (zdir / "ocsp.log").write_text("\n".join(
        json.dumps({"ts": T0 + w * 3600 + 20, "id": f"ocsp-resp-{w}", "certStatus": "good",
                   "thisUpdate": T0 + w * 3600, "nextUpdate": T0 + w * 3600 + 604800})
        for w in range(n_windows)) + "\n")

    multi = tmp_path / "multi"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--out", str(multi / "features"),
                        "--sink", f"store={multi / 's.duckdb'}",
                        "--sink", f"evidence={multi / 'e.jsonl'}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    solo_jsonl = tmp_path / "solo-jsonl"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--out", str(solo_jsonl)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    solo_store = tmp_path / "solo-store.duckdb"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--sink", f"store={solo_store}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    solo_evidence = tmp_path / "solo-e.jsonl"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--sink", f"evidence={solo_evidence}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # Non-empty FIRST, for every artifact compared below (mirrors `verify_tier.sh`'s
    # own explicit reasoning): equality on two absent-or-empty files/tables agrees
    # trivially, so a comparison that skipped this could report an "identical"
    # archive that is identically EMPTY on both sides — a regression that zeroed
    # out both the multi-sink and solo runs alike would sail through every
    # equality check below undetected.
    for name in ("nodes.jsonl", "edges.jsonl", "windows.jsonl", "identity.jsonl"):
        assert (multi / "features" / name).stat().st_size > 0, f"multi-sink {name} is empty"
        assert (solo_jsonl / name).stat().st_size > 0, f"solo {name} is empty"
        assert (multi / "features" / name).read_bytes() == (solo_jsonl / name).read_bytes(), \
            f"{name} differs between the multi-sink run and the solo jsonl run"

    # `window_health` always has exactly one row per window regardless of
    # traffic (§5.4 #5), so it anchors non-emptiness for the table loop even if
    # a fixture ever stopped producing assertions or identity bindings.
    assert len(_table_rows(multi / "s.duckdb", "window_health")) == 3 == \
        len(_table_rows(solo_store, "window_health"))
    # Every table EXCEPT `runs` (`S3`, `NONDETERMINISTIC_TABLES`) — `runs`
    # legitimately differs (independent `started_at`/`finished_at` wall-clock
    # per invocation), which is exactly why it is excluded by import rather
    # than by the table list here happening not to mention it. `profile_facts`
    # (`Q29`) joined this list once the belief store gained a subject-less channel;
    # the fixture's own `ocsp.log` (above, code review `S8`) means this is a genuine,
    # non-empty comparison of two independent writes, not one that would agree
    # trivially by both sides being empty.
    # `entity_roster` (`Q1`) joined this list rather than `NONDETERMINISTIC_TABLES`, and that is
    # a deliberate design property being asserted here, not an accident: it is keyed on the RUN'S
    # SOURCE, not on a wall-clock run stamp, precisely so two runs over one source produce
    # identical rows and this comparison stays meaningful (`belief_store.write_entity_roster`).
    # The fixture's `dhcp.log` gives it real content — see the non-emptiness check below.
    compared = _comparable_tables(multi / "s.duckdb")
    assert "runs" not in compared and set(compared) == {
        "node_features", "edge_features", "assertions", "identity_bindings",
        "window_health", "profile_facts", "entity_roster"}, compared
    assert _table_rows(multi / "s.duckdb", "entity_roster"), \
        "entity_roster is empty, so comparing it would agree trivially (the fixture's " \
        "dhcp.log must anchor at least one entity)"
    for table in compared:
        assert _table_rows(multi / "s.duckdb", table) == _table_rows(solo_store, table), \
            f"{table} differs between the multi-sink run and the solo store run"

    assert multi.joinpath("e.jsonl").stat().st_size > 0, "multi-sink evidence log is empty"
    assert solo_evidence.stat().st_size > 0, "solo evidence log is empty"
    assert (multi / "e.jsonl").read_bytes() == solo_evidence.read_bytes()


def test_vocabulary_reaches_only_the_jsonl_sink_in_a_multisink_run(tmp_path):
    """D4, exercised where it matters: a store sink open in the SAME run as a
    pinned jsonl sink must not be offered — or choke on — the pin."""
    out_a = tmp_path / "out_a"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(_zeek_dir(tmp_path / "a", n_windows=1, service="smb")),
                        "--out", str(out_a)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    out_b, store_b = tmp_path / "out_b", tmp_path / "store_b.duckdb"
    r = subprocess.run(
        [sys.executable, "-m", "aximprint.cli", "features",
         "--source", str(_zeek_dir(tmp_path / "b", n_windows=1, service="ldap_tcp")),
         "--out", str(out_b), "--sink", f"store={store_b}",
         "--vocabulary", str(out_a / "manifest.json")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    manifest_b = json.loads((out_b / "manifest.json").read_text())
    assert manifest_b["categorical"]["pinned_to"] is not None, "jsonl was pinned as usual"
    proto = manifest_b["categorical"]["fields"]["proto_mix"]
    assert proto["oov"]["keys"], "the fixture must actually exercise an out-of-vocabulary key"

    con = duckdb.connect(str(store_b), read_only=True)
    assert con.execute("SELECT count(*) FROM node_features").fetchone()[0] > 0, \
        "the store sink opened and wrote fine despite never declaring `vocabulary`"
    con.close()


def test_open_sinks_dispatches_options_by_the_receiving_sinks_declared_accepts():
    """`M3`: the router keys off `type(sink).accepts` (a declared `frozenset`),
    never off introspecting `open`'s SIGNATURE. Reflection was replaced because
    it fails SILENTLY when a decorator on `open` lacks `functools.wraps`, or a
    parameter gets renamed — `inspect.signature` would just report the wrong
    thing, with nothing to raise; a declared set is either right, or the
    mismatch surfaces as a plain `TypeError` at the `open()` call site.

    Every test above proves the OBSERVABLE behavior — jsonl gets `vocabulary`,
    store/evidence do not — but that behavior is also exactly what a router
    hardcoded as `if name == "jsonl": pass kwargs` would produce, since jsonl is
    the only registered sink that has ever asked for anything. These throwaway
    sinks are named nothing like `jsonl` and ask for an option under a name
    that is not `vocabulary`, so only a real per-declaration router can satisfy
    this — a hardcoded name check has nothing to key off of here.

    The negative case matters MORE under declaration-based routing than it did
    under signature-based routing, not less: `_LooksLikeItWantsOne`'s `open()`
    has a parameter literally named `some_option` — exactly what the OLD
    signature-introspecting router would have keyed on — but its `accepts`
    does not list it, so it must NOT receive it. A signature-based router could
    not even express this distinction.
    """
    from aximprint.cli import _open_sinks

    seen: dict[str, object] = {}

    class _WantsAnOption(WindowSink):
        name = "test-wants-an-option"
        accepts = frozenset({"some_option"})

        def open(self, destination, run_meta, some_option=None):
            seen["wanted"] = some_option

        def write_window(self, win, assertions): ...
        def close(self): return {}

    class _WantsNoOptions(WindowSink):
        name = "test-wants-no-options"

        def open(self, destination, run_meta):
            seen["plain"] = "opened"

        def write_window(self, win, assertions): ...
        def close(self): return {}

    class _LooksLikeItWantsOne(WindowSink):
        """Same PARAMETER NAME as `_WantsAnOption`, but does not declare it."""
        name = "test-looks-like-it-wants-one"

        def open(self, destination, run_meta, some_option="untouched"):
            seen["looks_like"] = some_option

        def write_window(self, win, assertions): ...
        def close(self): return {}

    with _temporary_sink(_WantsAnOption), _temporary_sink(_WantsNoOptions), \
         _temporary_sink(_LooksLikeItWantsOne):
        specs = [("test-wants-an-option", "dest-a"), ("test-wants-no-options", "dest-b"),
                ("test-looks-like-it-wants-one", "dest-c")]
        # No `"vocabulary"` here (unlike the pre-`M2` version of this test):
        # an option no OPEN sink declares is now a hard `SystemExit` (`M2`),
        # covered separately above — this test is scoped to ROUTING alone.
        _open_sinks(specs, META, open_kwargs={"some_option": "picked-me"})

    assert seen["wanted"] == "picked-me", \
        "a sink whose OWN `accepts` declares the parameter must receive it"
    assert seen["plain"] == "opened", \
        "a sink that does not declare the parameter must open fine and never see it"
    assert seen["looks_like"] == "untouched", \
        "a matching PARAMETER NAME is not enough without a matching `accepts` entry"


# -- D2: the SAME assertions object reaches every sink, unmutated ----------------

def test_every_open_sink_receives_the_identical_assertions_object(tmp_path):
    """D2 says `extract_assertions(win)` runs ONCE per window and is handed to
    every open sink UNCHANGED — pin the identity, not just equal values, since
    `S8` also forbids mutating it: a sink that copied-then-mutated would still
    pass an equality check but would no longer prove the driver did not
    recompute or rebuild the list per sink."""
    from aximprint.cli import _pipeline

    seen_ids: list[int] = []
    also_seen_ids: list[int] = []

    class _RecordsAssertionsIdentity(WindowSink):
        name = "test-records-assertions-identity"

        def open(self, destination, run_meta): ...

        def write_window(self, win, assertions):
            seen_ids.append(id(assertions))

        def close(self): return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}

    class _AlsoRecords(WindowSink):
        name = "test-also-records-assertions-identity"

        def open(self, destination, run_meta): ...

        def write_window(self, win, assertions):
            also_seen_ids.append(id(assertions))

        def close(self): return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}

    zdir = _zeek_dir(tmp_path, n_windows=2)
    specs = [("test-records-assertions-identity", "x"),
            ("test-also-records-assertions-identity", "y")]
    with _temporary_sink(_RecordsAssertionsIdentity), _temporary_sink(_AlsoRecords):
        _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                 subbuckets=12, specs=specs)

    assert len(seen_ids) == 2 and len(also_seen_ids) == 2, "fixture must produce two windows"
    # Per window, both sinks must have seen the SAME object, not two equal ones.
    assert seen_ids == also_seen_ids


# -- partial failure (build step 4) ------------------------------------------------

@contextlib.contextmanager
def _temporary_sink(cls):
    """Register `cls` for the duration of the block, then remove it.

    `sinks.base.register` has no companion "unregister" — a registry only ever
    grows in real use — so a test double is the one legitimate reason to reach
    into `_REGISTRY` directly rather than adding an unregister function nothing
    else would ever call.
    """
    register(cls)
    try:
        yield
    finally:
        del _REGISTRY[cls.name]


class _FailOnSecondWindow(WindowSink):
    """Succeeds once, then raises — the minimum needed to prove a fan-out
    failure is loud and says what already happened."""

    name = "test-fails-second-window"

    def __init__(self):
        self.calls = 0
        self.destination = None

    def open(self, destination, run_meta):
        self.destination = destination

    def write_window(self, win, assertions):
        self.calls += 1
        if self.calls == 2:
            raise ValueError("synthetic failure injected by the test")

    def close(self):
        return {"destination": self.destination, "windows": self.calls,
                "rows": {"calls": self.calls}, "vocabulary": {}}


def test_partial_failure_is_loud_and_names_what_already_wrote(tmp_path):
    """Build step 4: a sink raising mid-run must not be a silent `continue`."""
    from aximprint.cli import _pipeline

    zdir = _zeek_dir(tmp_path, n_windows=2)
    jsonl_out = tmp_path / "features"
    # jsonl goes FIRST, so it has already written for the failing window too —
    # that write must survive on disk even though the run as a whole fails.
    specs = [("jsonl", str(jsonl_out)), ("test-fails-second-window", str(tmp_path / "x"))]

    with _temporary_sink(_FailOnSecondWindow):
        with pytest.raises(RuntimeError) as exc:
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=specs)

    msg = str(exc.value)
    assert "test-fails-second-window" in msg, msg
    assert "1 earlier window(s)" in msg, msg
    assert "jsonl" in msg, "the sink that had already written for THIS window must be named"
    assert isinstance(exc.value.__cause__, ValueError), \
        "the original error must chain (fail loud), not be swallowed into a bare message"

    # The jsonl sink already wrote real rows for the failing window (it comes
    # first in `specs`) — that write is NOT rolled back. Checked as "both
    # windows' health rows are on disk", not merely "some row exists": with
    # `n_windows=2` the FIRST window alone would already make a bare `> 0` row
    # count true, which would keep passing even if the SECOND (failing) window's
    # write never actually reached jsonl before the fan-out raised — exactly the
    # thing this assertion exists to prove. `windows.jsonl` is written LAST in
    # `JsonlSink.write_window` (after nodes/edges/identity), so its row count is
    # proof the whole window was fully persisted, not merely attempted.
    written_windows = (jsonl_out / "windows.jsonl").read_text().splitlines()
    assert len(written_windows) == 2, \
        f"expected both windows fully written to jsonl before the failure, got {len(written_windows)}"
    # ...but the run is still recognizable as unfinished: `close()` was never
    # called on ANY sink, so no sink can report a false "complete" (jsonl's
    # signal for that is the absence of manifest.json, see features/jsonl.py).
    # `abort()` (`M4`/`M5`) DID run — it closes the same file handles `close()`
    # would have, just without writing the manifest — so this is not merely
    # "close() happened not to run", it is "release happened, completion did not".
    assert not (jsonl_out / "manifest.json").exists(), \
        "a failed run must not leave behind the artifact that means 'this run finished'"


def test_partial_failure_when_the_first_sink_fails_reports_no_sink_written(tmp_path):
    """The other half of the message: nothing had a chance to write for this window."""
    from aximprint.cli import _pipeline

    zdir = _zeek_dir(tmp_path, n_windows=2)
    # The failing sink goes FIRST this time: jsonl never gets a chance to write
    # for the window it fails on.
    specs = [("test-fails-second-window", str(tmp_path / "x")),
             ("jsonl", str(tmp_path / "features"))]

    with _temporary_sink(_FailOnSecondWindow):
        with pytest.raises(RuntimeError) as exc:
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=specs)

    assert "no sink" in str(exc.value), str(exc.value)


# -- M4/M5: `abort()` on each of the three failure surfaces ----------------------

def test_open_sinks_releases_earlier_sinks_when_a_later_ones_open_fails(tmp_path):
    """Surface 1: opening sink 2 of 2 fails -> sink 1, already open, is
    RELEASED (`abort()`), not left dangling under a raw traceback."""
    from aximprint.cli import _open_sinks

    released: list[str] = []

    class _TracksAbortOnOpenFailure(WindowSink):
        name = "test-tracks-abort-on-open-failure"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self): return {}
        def abort(self): released.append(self.name)

    class _FailsToOpen(WindowSink):
        name = "test-fails-to-open"
        def open(self, destination, run_meta):
            raise OSError("synthetic open failure")
        def write_window(self, win, assertions): ...
        def close(self): return {}

    with _temporary_sink(_TracksAbortOnOpenFailure), _temporary_sink(_FailsToOpen):
        with pytest.raises(RuntimeError) as exc:
            _open_sinks([("test-tracks-abort-on-open-failure", "a"),
                        ("test-fails-to-open", "b")], META)

    assert released == ["test-tracks-abort-on-open-failure"]
    assert "test-fails-to-open" in str(exc.value)
    assert isinstance(exc.value.__cause__, OSError)


def test_write_window_failure_releases_every_opened_sink(tmp_path):
    """Surface 2: one sink's `write_window` raising releases EVERY opened
    sink, not just the one that failed — including a sink that itself wrote
    every window successfully so far."""
    from aximprint.cli import _pipeline

    released: list[str] = []

    class _TracksAbortOnWriteFailure(WindowSink):
        name = "test-tracks-abort-on-write-failure"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...  # never fails
        def close(self):
            return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}
        def abort(self): released.append(self.name)

    zdir = _zeek_dir(tmp_path, n_windows=2)
    specs = [("test-tracks-abort-on-write-failure", "a"),
            ("test-fails-second-window", str(tmp_path / "x"))]
    with _temporary_sink(_TracksAbortOnWriteFailure), _temporary_sink(_FailOnSecondWindow):
        with pytest.raises(RuntimeError):
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=specs)

    assert released == ["test-tracks-abort-on-write-failure"], \
        "a sink that wrote every window successfully must still be released, not left open"


def test_note_unparsed_log_types_failure_releases_every_opened_sink(tmp_path):
    """`M3` (code review): the `note_unparsed_log_types` fan-out (`cli._pipeline`,
    D5) sits INSIDE the same `try:` as `write_window`'s own fan-out, not after
    it — a sink whose override raises here must release every opened sink the
    identical way a `write_window` failure already does, not leak a
    connection/handle with a bare traceback to `main()`. Every window here
    writes successfully; the failure is specific to the NEW fan-out this test
    targets."""
    from aximprint.cli import _pipeline

    released: list[str] = []

    class _TracksAbortOnNoteFailure(WindowSink):
        name = "test-tracks-abort-on-note-failure"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...  # never fails
        def close(self):
            return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}
        def abort(self): released.append(self.name)

    class _FailsOnNoteUnparsedLogTypes(WindowSink):
        name = "test-fails-on-note-unparsed-log-types"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...  # every window succeeds
        def note_unparsed_log_types(self, counts):
            raise ValueError("synthetic failure injected by the test")
        def close(self):
            return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}

    zdir = _zeek_dir(tmp_path, n_windows=2)
    specs = [("test-tracks-abort-on-note-failure", "a"),
            ("test-fails-on-note-unparsed-log-types", "b")]
    with _temporary_sink(_TracksAbortOnNoteFailure), \
         _temporary_sink(_FailsOnNoteUnparsedLogTypes):
        with pytest.raises(ValueError, match="synthetic failure injected by the test"):
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=specs)

    assert released == ["test-tracks-abort-on-note-failure"], \
        "every window wrote successfully; the failure is still released, not left open"


def test_close_failure_releases_sinks_not_yet_closed(tmp_path):
    """Surface 3: sink 2 of 3 raises during `close()` -> sink 3 (not yet
    closed) is released via `abort()`; sink 1 (already closed cleanly before
    the failure) is untouched — its own completion already happened and is
    real, even though the run as a whole did not finish."""
    from aximprint.cli import _pipeline

    closed_cleanly: list[str] = []
    released: list[str] = []

    class _ClosesCleanly(WindowSink):
        name = "test-closes-cleanly"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self):
            closed_cleanly.append(self.name)
            return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}

    class _FailsToClose(WindowSink):
        name = "test-fails-to-close"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self):
            raise OSError("synthetic close failure")

    class _NeverGetsToClose(WindowSink):
        name = "test-never-gets-to-close"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self):
            closed_cleanly.append(self.name)  # must NOT be reached
            return {"destination": "-", "windows": 0, "rows": {}, "vocabulary": {}}
        def abort(self): released.append(self.name)

    zdir = _zeek_dir(tmp_path, n_windows=1)
    specs = [("test-closes-cleanly", "a"), ("test-fails-to-close", "b"),
            ("test-never-gets-to-close", "c")]
    with _temporary_sink(_ClosesCleanly), _temporary_sink(_FailsToClose), \
         _temporary_sink(_NeverGetsToClose):
        with pytest.raises(RuntimeError) as exc:
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=specs)

    assert closed_cleanly == ["test-closes-cleanly"], \
        "the sink ahead of the failure must close cleanly; the one behind it must never reach close()"
    assert released == ["test-never-gets-to-close"]
    assert "test-fails-to-close" in str(exc.value)
    assert isinstance(exc.value.__cause__, OSError)


# -- S9: a sink's own close() summary may not shadow the registry name ----------

def test_close_summary_may_not_shadow_the_registry_name(tmp_path):
    """`_close_all` builds `{"name": name, **sink.close()}` — a sink whose OWN
    summary defines a `"name"` key would silently win the dict-literal merge
    and shadow the registry name (`S9`). Rejected instead."""
    from aximprint.cli import _pipeline

    class _ShadowsName(WindowSink):
        name = "test-shadows-name"
        def open(self, destination, run_meta): ...
        def write_window(self, win, assertions): ...
        def close(self):
            return {"name": "spoofed", "destination": "-", "windows": 0,
                    "rows": {}, "vocabulary": {}}

    zdir = _zeek_dir(tmp_path, n_windows=1)
    with _temporary_sink(_ShadowsName):
        with pytest.raises(ValueError, match="reserved"):
            _pipeline("zeek", str(zdir), ts_from=None, ts_to=None, window_seconds=3600,
                     subbuckets=12, specs=[("test-shadows-name", "a")])
