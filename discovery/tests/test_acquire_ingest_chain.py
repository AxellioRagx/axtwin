"""The end-to-end chain the two-repo split made untestable, now a single gate
(`docs/WORK_ORDER_merge_xtract_acquire.md`, "Validation"): `aximprint acquire harvest`
(`FakeZeekRunner`, no appliance, no `podman`) -> cold tier -> `aximprint ingest --source
<tree>` -> belief store -> `aximprint synth`. Fixtures only, offline, deterministic.

Built as a pytest test rather than a shell script on purpose: `zeek_runner_factory` (like
`client_factory`) is a Python-API-only injection seam with no CLI flag — `--dry-run` is
the only network/podman-free path reachable from a real shell invocation of `aximprint
acquire harvest`, and `--dry-run` never runs Zeek at all. Reusing `_FakeHarvestClient`/
`_NullContext`/`_written`/`BASE_ARGV` from `tests/acquire/test_cli_harvest.py` rather
than re-deriving them: they are the already-proven minimal harness for exactly this seam
(`test_full_run_extracts_and_zeeks_and_exits_zero`), so a bug here is a bug in the CHAIN,
not in a second, slightly-different fixture-construction path.
"""
from __future__ import annotations

from aximprint import cli as aximprint_cli
from aximprint.acquire import cli as acquire_cli
from aximprint.acquire.harvest.zeek import FakeZeekRunner
from tests.acquire.test_cli_harvest import (
    BASE_ARGV,
    _FakeHarvestClient,
    _NullContext,
    _written,
)

# Two 10s windows (1700000000, 1700000010) rather than one: a single-window fixture
# cannot distinguish "read as a window tree" from "read as a flat directory that happens
# to contain one file" — both degrade to the same one-stream case. Two window
# directories under <out>/zeek is the minimum that exercises the window-tree-specific
# per-stem consolidation path (`sources.py`, `docs/WORK_ORDER_multi_window_log_read.md`).
_HARVEST_ARGV = [
    "harvest", "--streams", "2-4", "--start", "1700000000", "--end", "1700000020",
    "--interval", "10s",
]


def _run_harvest(out_dir) -> int:
    client = _FakeHarvestClient(pcap_by_start={
        "1700000000": _written(),
        "1700000010": _written(),
    })
    return acquire_cli.main(
        [*BASE_ARGV, *_HARVEST_ARGV, "--out", str(out_dir)],
        client_factory=lambda args: _NullContext(client),
        zeek_runner_factory=lambda args: FakeZeekRunner(),
    )


def test_harvest_to_ingest_to_synth_chain_on_fixtures(tmp_path):
    """The finish line: no appliance, no podman, no real capture anywhere in this test."""
    out_dir = tmp_path / "harvest-out"
    assert _run_harvest(out_dir) == 0

    zeek_tree = out_dir / "zeek"
    # Confirms the cold-tier shape build step 4/D-series text describes:
    # <out>/zeek/<window_id>/<stem>.log, one directory per harvest window.
    assert (zeek_tree / "1700000000" / "conn.log").exists()
    assert (zeek_tree / "1700000010" / "conn.log").exists()
    assert not (out_dir / "spool" / "1700000000.pcap").exists()  # released after Zeek ok
    assert not (out_dir / "spool" / "1700000010.pcap").exists()

    store_path = tmp_path / "imprint.duckdb"
    evidence_path = tmp_path / "evidence.jsonl"
    ingest_code = aximprint_cli.main([
        "ingest", "--source", str(zeek_tree),
        "--store", str(store_path), "--evidence-log", str(evidence_path),
    ])
    assert ingest_code == 0
    assert store_path.exists() and store_path.stat().st_size > 0
    assert evidence_path.exists() and evidence_path.stat().st_size > 0

    yaml_path = tmp_path / "twin_draft.yaml"
    synth_code = aximprint_cli.main([
        "synth", "--store", str(store_path), "--out", str(yaml_path),
    ])
    assert synth_code == 0
    text = yaml_path.read_text()
    assert "twin_config_draft" in text


def test_ingest_source_must_be_the_zeek_subdirectory_not_the_harvest_root(tmp_path, capsys):
    """Answers the open question directly, empirically, rather than trusting the docstring
    claim: `--source <out>` (the harvest root, holding `spool/` and `zeek/` as siblings)
    is NOT the window-tree read `<out>/zeek` is. `<out>`'s immediate children are
    `spool`/`zeek` — neither parses as a window_id (`_looks_like_window_id`) — so the
    auto-detection gate in `ingest/zeek/sources.py` falls through to FLAT mode: an
    unbounded recursive walk (`rglob`) that still physically finds both `conn.log` files
    two levels down (so this small, clean, 2-window fixture happens to read the same
    6 rows -> 6 events either way — the distinction FLAT mode's docstring draws, "every
    file found is its own stream," is about merge/reorder-budget discipline across MANY
    windows or under disorder, not something a fixture this small and this orderly can
    show numerically). What IS observable, and what this asserts on: the plugin's own
    diagnostic says outright that this is a fallback, not an equally-valid alias for the
    window-tree read — measured, not inferred from the docstring's claim.
    """
    out_dir = tmp_path / "harvest-out"
    assert _run_harvest(out_dir) == 0
    zeek_tree = out_dir / "zeek"

    capsys.readouterr()  # drain harvest's own stdout/stderr first
    store_correct = tmp_path / "correct.duckdb"
    code_correct = aximprint_cli.main(
        ["ingest", "--source", str(zeek_tree), "--store", str(store_correct)]
    )
    assert code_correct == 0
    # The plugin's own progress line is a diagnostic (`plugin.py`'s `_log`), printed to
    # stderr, not stdout -- captured separately by capsys for exactly that reason.
    correct_err = capsys.readouterr().err
    assert "window tree" in correct_err, correct_err
    assert "2 window director" in correct_err, correct_err

    store_wrong = tmp_path / "wrong.duckdb"
    code_wrong = aximprint_cli.main(
        ["ingest", "--source", str(out_dir), "--store", str(store_wrong)]
    )
    # It does not crash -- rglob still finds both conn.log files -- but the plugin's own
    # diagnostic says outright that this is a fallback, and names why: `spool/`, the
    # harvest root's OTHER child, does not parse as a window_id, so the positive gate
    # refuses window-tree mode for the whole root instead of guessing.
    assert code_wrong == 0
    wrong_err = capsys.readouterr().err
    assert "reading as a flat directory instead of a window tree" in wrong_err, wrong_err
    assert "'spool'" in wrong_err, wrong_err
