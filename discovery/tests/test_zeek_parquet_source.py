"""The Parquet metadata archive transport (`docs/SPEC_zeek_metadata_archive.md`).

Synthetic fixtures only — a real capture carries PII and never enters a test
(`LES-003`), and an archive is the *most* identity-dense artifact in the project
because it carries raw Zeek records.

Two things are under test and they are different claims:

  1. **Equivalence.** The archive transport and the text transport must produce
     the same events from the same capture. Any divergence has to be nameable.
  2. **Refusal.** aximprint does not write this archive — an external converter
     does — so every way the contract can be violated must fail loud rather than
     read as a smaller-but-valid capture.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from aximprint.events import SensorHealthEvent, ServiceEvent
from aximprint.ingest.zeek import plugin as zp
from aximprint.ingest.zeek import sources as zs

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "make_zeek_archive", _SCRIPTS / "make_zeek_archive.py")
make_archive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_archive)

_SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "SPEC_zeek_metadata_archive.md"

T0 = 1754000000.0

CONN_FIELDS = ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "proto",
               "service", "duration", "orig_bytes", "resp_bytes", "conn_state",
               "orig_pkts", "resp_pkts")


def _tsv(path: Path, fields, rows):
    lines = ["#separator \\x09", "#unset_field\t-", "#empty_field\t(empty)",
             "#fields\t" + "\t".join(fields)]
    lines += ["\t".join(str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def _conn(ts, sip="10.0.0.21", dip="10.0.0.5", dport=88, state="SF"):
    return (f"{ts:.6f}", sip, 40000, dip, dport, "tcp", "krb", "0.5",
            1000, 2000, state, 5, 6)


def _capture(tmp_path: Path, rows=None) -> Path:
    src = tmp_path / "zeek"
    src.mkdir()
    _tsv(src / "conn.log", CONN_FIELDS,
         rows if rows is not None else [_conn(T0 + i) for i in range(60)])
    return src


def _plugin():
    p = zp.ZeekPlugin()
    p.progress_rows = 0  # keep test output clean; also silences the summary
    return p


def _events(locator, **kw):
    p = _plugin()
    for k, v in kw.items():
        setattr(p, k, v)
    return list(p.events(locator))


# -- equivalence ----------------------------------------------------------------

def test_archive_and_text_yield_identical_events(tmp_path):
    """The acceptance criterion: same capture, two transports, one event stream.

    Not "similar" and not "same count" — equal, including field values and order.
    On input with no disorder beyond the reorder horizon there is no licence for
    the two to differ at all.
    """
    src = _capture(tmp_path)
    make_archive.build_archive(src, tmp_path / "arch")
    assert _events(f"parquet:{tmp_path / 'arch'}") == _events(str(src))


def test_archive_preserves_unset_as_unset_not_zero(tmp_path):
    """A `-` counter must survive the round trip as *unknown*, never as observed 0.

    Parquet nulls carry this natively, which is why the archive is a correctness
    upgrade over the text marker convention and not just a faster read
    (`LES-007`). Checked through `counters_imputed`, which is the flag the whole
    distinction exists to set.
    """
    rows = [(f"{T0 + 1:.6f}", "10.0.0.21", 40000, "10.0.0.5", 88, "tcp", "krb",
             "-", "-", "-", "OTH", "-", "-")]
    src = _capture(tmp_path, rows=rows)
    make_archive.build_archive(src, tmp_path / "arch")
    text = _events(str(src))
    arch = _events(f"parquet:{tmp_path / 'arch'}")
    assert text == arch
    assert [e.counters_imputed for e in arch] == [True]


def test_archive_read_is_deterministic_across_runs(tmp_path):
    """Two reads of one archive agree, ties included.

    Equal timestamps must not resolve on whatever order the reader happened to
    produce; `ORDER BY ts, _zeek_run, _row_no` is what makes that total
    (`LES-009`). The fixture is all-ties on purpose.
    """
    src = _capture(tmp_path, rows=[_conn(T0, dport=80 + i) for i in range(40)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    assert _events(f"parquet:{arch}") == _events(f"parquet:{arch}")


def test_archive_spanning_two_date_partitions_stays_ordered(tmp_path):
    """Partitions are cut on the row's own ts, and each date's rows are
    genuinely disjoint in time here — so a merge across them (what the reader
    actually does — `_read`) comes back sorted, same as a concatenation would
    for THIS fixture specifically. See
    `test_partitions_within_one_date_merge_on_ts_not_on_part_filename` for the
    shape where only a merge, not a concatenation, gets it right.
    """
    day = 86400.0
    src = _capture(tmp_path, rows=[_conn(T0 + i * day / 4) for i in range(12)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    parts = list((Path(arch) / "parquet" / "log=conn").glob("date=*"))
    assert len(parts) > 1, "fixture must actually span a date boundary"
    ts = [e.ts for e in _events(f"parquet:{arch}")]
    assert ts == sorted(ts)


def test_partitions_within_one_date_merge_on_ts_not_on_part_filename(tmp_path):
    """The partition-concat bug, reproduced directly.

    `build_archive` always does one global sort before partitioning, so it can
    never by itself produce two mutually-out-of-order files in one date
    directory — this constructs that shape by hand, the way an INCREMENTAL
    converter run genuinely can (SPEC 'Layout': `OVERWRITE_OR_IGNORE` lets a
    later conversion batch add a `part-NNN.parquet` to an EARLIER date once it
    discovers a row that belongs there). `part-001.parquet` sorts AFTER
    `part-000.parquet` by name; its one row is chronologically BEFORE
    everything in it. A path-order concatenation reads `part-000` fully first
    and would emit that row last, out of order — `heapq.merge` must not.
    """
    import duckdb

    src = _capture(tmp_path, rows=[_conn(T0 + i) for i in range(5)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    dates = list((Path(arch) / "parquet" / "log=conn").glob("date=*"))
    assert len(dates) == 1, "fixture must land in exactly one date partition"
    (part0,) = dates[0].glob("*.parquet")
    assert part0.name == "part-000.parquet"

    con = duckdb.connect(":memory:")
    con.execute(
        f"COPY (SELECT * REPLACE (ts - 1000 AS ts, 0 AS _row_no, "
        f"'run-late' AS _zeek_run) FROM read_parquet('{part0}') LIMIT 1) "
        f"TO '{dates[0] / 'part-001.parquet'}' (FORMAT PARQUET)")
    con.close()

    ts = [e.ts for e in _events(f"parquet:{arch}")]
    assert ts == sorted(ts), "a true merge, not a path-order concatenation"
    assert ts[0] == T0 - 1000, (
        "the earlier row from the alphabetically LATER part file leads")


# -- inventory completeness (docs/WORK_ORDER_zeek_stem_coverage.md D5) -----------
#
# `unparsed_stems` (`ingest/base.py`) is only as honest as the SOURCE's own
# `inventory` — and the archive transport's `inventory` can only ever contain
# stems its manifest declares, which `make_zeek_archive.py`'s own stem gate
# (`if stem in zp._COLUMNS: ...`) restricts to EXACTLY `_PARSERS`'s key set.
# So `unparsed` (`plugin.py::events`) would read `{}` on every archive-transport
# run regardless of what the capture actually contained — not "audited, and
# clean", but "structurally incapable of finding out". `ZeekSource
# .inventory_is_complete` is the declared-contract fix: `False` on
# `ParquetArchiveSource` is what keeps `unparsed_stems` at `None` (unaudited)
# instead of asserting a false completeness the transport cannot back.

#: Guaranteed never a real Zeek log type (mirrors `tests/test_zeek_ingest.py`'s
#: own `_UNKNOWN_STEM` — a REAL stem name here is the exact `LES-086` landmine
#: that document's own D1 fixed four instances of).
_UNKNOWN_STEM = "__not_a_zeek_stem__"


def test_logdir_transport_over_a_flat_directory_declares_its_inventory_complete(tmp_path):
    """`LogDirSource` walks the filesystem directly and counts every matching
    file regardless of whether `plugin.py` can parse its stem — the
    filesystem itself is the census. NOT a class-level constant (code review
    `M1`): completeness varies WITHIN this one class by locator shape (a
    single-file locator cannot be complete — see `tests/test_zeek_ingest.py`'s
    per-branch coverage), so this is checked on the INSTANCE, after `streams()`
    has actually looked."""
    src = _capture(tmp_path)  # a flat directory: just conn.log
    logdir = zs.LogDirSource(str(src))
    logdir.streams(zp.wanted_columns())
    assert logdir.inventory_is_complete is True


def test_archive_transport_declares_its_inventory_incomplete():
    """The one override today — `make_zeek_archive.py`'s own stem gate means
    the manifest can never name a skipped or unrecognized stem at all."""
    assert zs.ParquetArchiveSource.inventory_is_complete is False


def test_unparsed_stems_diverges_between_transports_over_identical_content(tmp_path):
    """The bug this section exists to prevent recurring, pinned directly:
    identical capture content, read through two transports, must NOT agree —
    the flat directory audits every file on disk and reports the two genuinely
    left over; the archive's own converter never wrote either one into its
    manifest at all, so the honest answer there is `None` (unaudited), never
    `{}` (which would claim "audited, and clean" for a capture that plainly
    was not).
    """
    src = _capture(tmp_path)
    _tsv(src / "reporter.log", ("ts", "level", "message"),
         [(f"{T0:.6f}", "REPORTER_INFO", "test-only, never converted")])
    (src / f"{_UNKNOWN_STEM}.log").write_text(
        json.dumps({"ts": T0, "note": "never routed into _PARSERS or _SKIP"}) + "\n")
    arch = make_archive.build_archive(src, tmp_path / "arch")

    flat = zp.ZeekPlugin()
    flat.progress_rows = 0
    list(flat.events(str(src)))
    assert flat.unparsed_stems == {"reporter": 1, _UNKNOWN_STEM: 1}, (
        "the flat transport walks the directory directly and sees both extra "
        "files regardless of whether make_zeek_archive.py would ever convert them")

    archived = zp.ZeekPlugin()
    archived.progress_rows = 0
    list(archived.events(f"parquet:{arch}"))
    assert archived.unparsed_stems is None, (
        "the archive's own converter never wrote reporter/__not_a_zeek_stem__ into "
        "the manifest at all (neither is in _PARSERS, `make_zeek_archive.py`'s own "
        "stem gate), so this transport structurally cannot audit them — unaudited, "
        "never a false 'nothing left over'"
    )
    assert archived.unparsed_stems != {}, (
        "the actual regression: {} claims a complete audit found nothing left "
        "over, which this transport is never in a position to claim"
    )


def test_unparsed_stems_reset_survives_reusing_one_plugin_across_a_complete_then_incomplete_source(tmp_path):
    """Names the reason `events()` still resets `unparsed_stems` to `None` even
    though the assignment right below it is now conditional
    (`ZeekPlugin.events`'s own comment): a plugin instance driven over a
    COMPLETE-inventory source and then an INCOMPLETE one must not let the first
    call's dict survive into the second call's answer."""
    src = _capture(tmp_path)
    _tsv(src / "reporter.log", ("ts", "level", "message"),
         [(f"{T0:.6f}", "REPORTER_INFO", "test-only")])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    p = zp.ZeekPlugin()
    p.progress_rows = 0
    list(p.events(str(src)))
    assert p.unparsed_stems == {"reporter": 1}, "sanity: the flat pass found the extra file"

    list(p.events(f"parquet:{arch}"))
    assert p.unparsed_stems is None, (
        "must not still read {'reporter': 1} from the PREVIOUS, complete-inventory "
        "call — the archive pass can say only 'unaudited' for itself"
    )


# -- the disorder contract ------------------------------------------------------

def test_sorted_archive_still_reports_the_sensors_own_disorder(tmp_path):
    """A sort must not launder the sensor's disorder into a clean-looking window.

    The archive is ts-sorted, so `_in_order` sees nothing to repair. The original
    displacement survives in `_displacement_seconds`, and the reader re-emits it
    — otherwise every window built from a disordered sensor would read clean,
    which is `LES-006`'s "no evidence" posing as "evidence of none".
    """
    rows = [_conn(T0), _conn(T0 + 100), _conn(T0 + 1)]  # third row displaced 99 s
    src = _capture(tmp_path, rows=rows)
    arch = make_archive.build_archive(src, tmp_path / "arch")

    declared = json.loads((Path(arch) / "manifest.json").read_text())
    assert declared["logs"]["conn"]["disorder"]["rows_displaced"] == 1

    events = _events(f"parquet:{arch}", reorder_horizon_seconds=1.0)
    unordered = [e for e in events
                 if isinstance(e, SensorHealthEvent) and e.kind == "ingest_unordered"]
    assert len(unordered) == 1
    assert unordered[0].value == pytest.approx(99.0)
    assert unordered[0].detail == "conn"


def test_disorder_within_the_horizon_is_repaired_silently_on_both_paths(tmp_path):
    """Displacement the buffer *can* repair is not a health event on either side.

    The archive records displacement for every moved row, so the reader has to
    apply the same horizon the text path's buffer does — or it would report
    disorder the text path silently fixed, and the two transports would disagree
    about a window's health.
    """
    src = _capture(tmp_path, rows=[_conn(T0), _conn(T0 + 100), _conn(T0 + 1)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    for events in (_events(str(src)), _events(f"parquet:{arch}")):
        assert not [e for e in events if isinstance(e, SensorHealthEvent)
                    and e.kind == "ingest_unordered"]


def test_archive_without_disorder_stats_is_refused(tmp_path):
    """An unmeasured archive must not read as an archive that measured nothing.

    This is the one contract clause with no fallback: the converter sorted, so
    the evidence is gone, and reading anyway would silently publish clean health
    records for a sensor nobody checked.
    """
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    mpath = Path(arch) / "manifest.json"
    doc = json.loads(mpath.read_text())
    doc["logs"]["conn"]["disorder"] = {"measured": False}
    mpath.write_text(json.dumps(doc))
    with pytest.raises(zs.ArchiveContractError, match="disorder.measured"):
        _events(f"parquet:{arch}")


# -- refusal ---------------------------------------------------------------------

def test_missing_manifest_is_refused(tmp_path):
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    (Path(arch) / "manifest.json").unlink()
    with pytest.raises(zs.ArchiveContractError, match="manifest"):
        _events(f"parquet:{arch}")


def test_missing_required_column_names_the_log_and_the_column(tmp_path):
    """A parser starved of an endpoint column still yields events — empty ones.

    That is strictly worse than a parse error (`LES-007`), so a declared schema
    missing a required column is refused before a row is read.
    """
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    mpath = Path(arch) / "manifest.json"
    doc = json.loads(mpath.read_text())
    doc["logs"]["conn"]["columns"].pop("id.resp_h")
    mpath.write_text(json.dumps(doc))
    with pytest.raises(zs.ArchiveContractError) as e:
        _events(f"parquet:{arch}")
    assert "conn" in str(e.value) and "id.resp_h" in str(e.value)


def test_missing_provenance_column_is_refused(tmp_path):
    """`_row_no` is what makes the read order total; without it ties are arbitrary."""
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    mpath = Path(arch) / "manifest.json"
    doc = json.loads(mpath.read_text())
    doc["logs"]["conn"]["columns"].pop("_row_no")
    mpath.write_text(json.dumps(doc))
    with pytest.raises(zs.ArchiveContractError, match="_row_no"):
        _events(f"parquet:{arch}")


@pytest.mark.parametrize("col,wrong_type", [
    ("ts", "TIMESTAMP"),        # SPEC "`ts` is DOUBLE. Never TIMESTAMP."
    ("_zeek_run", "BIGINT"),
    ("_row_no", "VARCHAR"),
])
def test_archive_key_column_wrong_type_is_refused(tmp_path, col, wrong_type):
    """`S2`: before this declared type ever reached a comparison, a wrong
    `ts` type did not raise at all — `_row_ts` returns `None` for anything
    `float()` refuses, so every row became unorderable and the cross
    -partition merge silently degenerated back to `heapq.merge`'s own
    stream-position tie-break, i.e. path-order concatenation — exactly the
    bug this reader exists to refuse. Refused at `load_manifest`, before a
    single row is read, naming the column and the wrong type rather than
    letting it surface however (or however silently) the merge happens to
    react to it.
    """
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    mpath = Path(arch) / "manifest.json"
    doc = json.loads(mpath.read_text())
    doc["logs"]["conn"]["columns"][col] = wrong_type
    mpath.write_text(json.dumps(doc))
    with pytest.raises(zs.ArchiveContractError, match=re.escape(col)):
        _events(f"parquet:{arch}")


def test_undeclared_parquet_directory_is_refused(tmp_path):
    """A file nobody declared is ambiguous, not a bonus.

    aximprint reads only what the manifest declares, so data present outside it
    means the two disagree — and silently preferring either one would be a guess.
    """
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    stray = Path(arch) / "parquet" / "log=http" / "date=2026-08-01"
    stray.mkdir(parents=True)
    (stray / "part-000.parquet").write_bytes(b"")
    with pytest.raises(zs.ArchiveContractError, match="http"):
        _events(f"parquet:{arch}")


def test_declared_log_with_no_files_is_refused(tmp_path):
    src = _capture(tmp_path)
    arch = make_archive.build_archive(src, tmp_path / "arch")
    for p in (Path(arch) / "parquet" / "log=conn").rglob("*.parquet"):
        p.unlink()
    with pytest.raises(zs.ArchiveContractError, match="conn"):
        _events(f"parquet:{arch}")


def test_missing_archive_root_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        _events(f"parquet:{tmp_path / 'nope'}")


def test_archive_defends_its_own_output_directory(tmp_path):
    """An archive is capture-derived and may legitimately be written inside the
    repo, so it ignores itself rather than relying on a repo-wide rule that would
    shadow a legitimate `manifest.json` elsewhere in the tree (`LES-011`)."""
    arch = make_archive.build_archive(_capture(tmp_path), tmp_path / "arch")
    assert (Path(arch) / ".gitignore").read_text().strip().endswith("*")


# -- slicing ---------------------------------------------------------------------

def test_slice_returns_exactly_the_windowed_subset(tmp_path):
    """A slice must equal the corresponding subset of a full run, not approximate it."""
    src = _capture(tmp_path, rows=[_conn(T0 + i) for i in range(60)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    lo, hi = T0 + 10, T0 + 20
    p = _plugin()
    sliced = [e.ts for e in p.events(f"parquet:{arch}", lo, hi)]
    assert sliced == [e.ts for e in _events(f"parquet:{arch}") if lo <= e.ts < hi]
    assert len(sliced) == 10


def test_slice_is_half_open(tmp_path):
    """`[from, to)` — an event at exactly `to` belongs to the next slice, not both."""
    src = _capture(tmp_path, rows=[_conn(T0 + i) for i in range(5)])
    arch = make_archive.build_archive(src, tmp_path / "arch")
    p = _plugin()
    assert [e.ts for e in p.events(f"parquet:{arch}", T0, T0 + 2)] == [T0, T0 + 1]


def test_slicing_a_log_directory_is_refused_not_ignored(tmp_path):
    """A text directory has no partition index, so a slice it cannot honor must
    fail rather than produce a full run that merely looks sliced."""
    src = _capture(tmp_path)
    p = _plugin()
    with pytest.raises(ValueError, match="parquet"):
        list(p.events(str(src), T0, T0 + 5))


# -- projection ------------------------------------------------------------------

def test_projection_reads_only_the_columns_a_parser_uses(tmp_path):
    """A projection is what makes the archive small and a slice cheap.

    Not what makes a full scan fast — measured, it isn't (CHANGELOG 2026-08-05).
    `SELECT *` would still cost storage and slice selectivity, which is what this
    transport is actually for.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    # `local_orig` USED to be this test's example of a column no parser reads. It stopped
    # being one when segment extraction landed (`WORK_ORDER_synth_v1.md` phase 1) and is now
    # asserted PRESENT below — the projection has to widen when a parser starts reading a
    # field, or the archive transport would silently hand that parser nothing. `uid` and
    # `tunnel_parents` carry the original claim; both are still genuinely unread.
    _tsv(src / "conn.log", (*CONN_FIELDS, "uid", "local_orig", "tunnel_parents"),
         [(*_conn(T0 + i), "Cxyz", "T", "(empty)") for i in range(5)])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    source = zs.ParquetArchiveSource(str(arch))
    source.streams(zp.wanted_columns())
    projected = source._projection("conn", list(zp.wanted_columns()["conn"]))
    assert "uid" not in projected and "tunnel_parents" not in projected
    assert "local_orig" in projected, "a column a parser reads must reach the parser"
    assert "ts" in projected and "conn_state" in projected
    for c in zs.ARCHIVE_PROVENANCE:
        assert c in projected, "provenance is required regardless of the parser"


def test_extra_source_columns_do_not_change_the_events(tmp_path):
    """A superset schema is explicitly allowed, so it must be inert."""
    lean = tmp_path / "lean"
    lean.mkdir()
    _tsv(lean / "conn.log", CONN_FIELDS, [_conn(T0 + i) for i in range(5)])
    wide = tmp_path / "wide"
    wide.mkdir()
    _tsv(wide / "conn.log", (*CONN_FIELDS, "uid", "history"),
         [(*_conn(T0 + i), "Cxyz", "ShADad") for i in range(5)])
    a = make_archive.build_archive(lean, tmp_path / "a")
    b = make_archive.build_archive(wide, tmp_path / "b")
    assert _events(f"parquet:{a}") == _events(f"parquet:{b}")


def test_widened_service_columns_are_projected_on_the_archive_transport(tmp_path):
    """`docs/WORK_ORDER_zeek_stem_coverage.md` build step 2b widened `_COLUMNS["ldap_search"]`,
    `["smb_files"]` and `["ntp"]` alongside the parsers' own `_attrs()` calls — and on THIS
    transport, unlike the text one, the two tables are what makes the fields reachable at all.

    `LogDirSource.streams` ignores its `columns` argument outright ("a text row must be split in
    full before any field can be addressed, so there is nothing to push down") — every flat-log
    test already sees the new fields the moment `_attrs()` asks for them, `_COLUMNS` or no.
    `ParquetArchiveSource._projection` does not: it SELECTs only `wanted_columns()`, derived from
    `_COLUMNS`, intersected with what the archive declares (see
    `test_projection_reads_only_the_columns_a_parser_uses`, above). The reference converter
    (`make_zeek_archive.build_archive`, via `_column_union`) captures every column a source log
    actually has, independent of `_COLUMNS` — so an archive built from these fixtures carries
    `base_object`/`filter`/`attributes`/`diagnostic_message`/`name`/`poll` as real declared
    columns REGARDLESS of whether `_COLUMNS` asks for them. A `_COLUMNS` entry left narrow would
    silently under-select on this transport alone: the archive has the data, the parser's
    `_attrs()` call asks for it, and the row handed to the parser would still be missing it.

    Equivalence with the text path is the sharper check than a parquet-only value literal: if
    `_COLUMNS` regressed, the text-side event would still carry the new attrs (its path never
    consulted `_COLUMNS`) while the archive-side one would not, so `==` below would catch it
    where a hardcoded-parquet-side-only assertion could not distinguish "never collected" from
    "collected but not projected on this transport". The literal attrs are asserted too, so the
    test does not pass vacuously if both sides independently lost the same fields.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    _tsv(src / "conn.log", CONN_FIELDS, [_conn(T0 + i) for i in range(3)])
    _tsv(src / "ldap_search.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "scope",
          "base_object", "result_count", "result", "filter", "diagnostic_message",
          "attributes"),
         [(f"{T0 + 10:.6f}", "10.0.0.21", 40001, "10.0.0.5", 389, "sub",
           "dc=example", 3, "success", "(objectClass=*)", "0000208D: NameErr",
           "cn,mail")])
    _tsv(src / "smb_files.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "action",
          "path", "name"),
         [(f"{T0 + 11:.6f}", "10.0.0.21", 40002, "10.0.0.6", 445,
           "SMB::FILE_OPEN", "-", "secret-doc.xlsx")])
    _tsv(src / "ntp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "version",
          "mode", "stratum", "poll"),
         [(f"{T0 + 12:.6f}", "10.0.0.21", 123, "10.0.0.9", 123, 4, 3, 2, "64.000000")])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    text = _events(str(src))
    parquet = _events(f"parquet:{arch}")
    assert parquet == text, "the archive transport must not silently drop a widened column"

    services = {e.kind: e for e in parquet if isinstance(e, ServiceEvent)}
    assert services["ldap"].attrs == {
        "scope": "sub", "result": "success", "result_count": "3",
        "base_object": "dc=example", "filter": "(objectClass=*)",
        "diagnostic_message": "0000208D: NameErr", "attributes": "cn,mail",
    }
    assert services["smb_files"].attrs == {"action": "SMB::FILE_OPEN",
                                           "name": "secret-doc.xlsx"}
    assert services["ntp"].attrs == {"version": "4", "mode": "3", "stratum": "2",
                                     "poll": "64.000000"}


def test_new_stems_are_equivalent_across_transports(tmp_path):
    """`docs/WORK_ORDER_zeek_stem_coverage.md` step 3's ten stems, over the archive transport.

    Two claims: (1) ordinary equivalence with the text path, the same acceptance criterion
    every other stem gets; (2) `smtp`/`sip`'s `date` and `snmp`'s `duration` — RESTORED by
    code review `S1` after this stem's first landing excluded them — round-trip CORRECTLY,
    not merely present, on the archive transport: `date` via `hive_partitioning=false`
    (`sources.ParquetArchiveSource._read`), `duration` via `make_zeek_archive.py`'s
    `(stem, column)`-keyed typing (`_STEM_TYPES`) leaving `("snmp", "duration")` VARCHAR
    while `("conn", "duration")` stays DOUBLE.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    _tsv(src / "smtp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "date", "helo",
          "x_originating_ip"),
         [(f"{T0 + 1:.6f}", "10.0.0.21", 51000, "10.0.0.15", 25,
           "Wed, 06 Aug 2026 10:15:00 -0700", "mail.example.test", "198.51.100.7")])
    _tsv(src / "sip.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "date", "user_agent"),
         [(f"{T0 + 2:.6f}", "10.0.0.21", 51001, "10.0.0.16", 5060,
           "Wed, 06 Aug 2026 10:15:00 -0700", "Zeek-SIP-Test/1.0")])
    _tsv(src / "snmp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "duration",
          "community", "display_string"),
         [(f"{T0 + 3:.6f}", "10.0.0.21", 51002, "10.0.0.13", 161, "0.500000",
           "public", "Cisco IOS Software")])
    _tsv(src / "http.log", ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "host"),
         [(f"{T0 + 4:.6f}", "10.0.0.21", 51003, "10.0.0.10", 80, "svc.example.test")])
    _tsv(src / "ssh.log", ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server"),
         [(f"{T0 + 5:.6f}", "10.0.0.21", 51004, "10.0.0.11", 22, "SSH-2.0-OpenSSH_9.0")])
    _tsv(src / "quic.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server_name"),
         [(f"{T0 + 6:.6f}", "10.0.0.21", 51005, "10.0.0.12", 443, "svc.example.test")])
    _tsv(src / "tunnel.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "tunnel_type"),
         [(f"{T0 + 7:.6f}", "10.0.0.21", 51006, "10.0.0.14", 4789, "Tunnel::VXLAN")])
    _tsv(src / "ldap.log", ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "opcode"),
         [(f"{T0 + 8:.6f}", "10.0.0.21", 51007, "10.0.0.17", 389, "bindRequest")])
    _tsv(src / "ntlm.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "server_dns_computer_name"),
         [(f"{T0 + 9:.6f}", "10.0.0.21", 51008, "10.0.0.18", 445,
           "workstn1.corp.example.test")])
    _tsv(src / "x509.log", ("ts", "fingerprint", "san.dns"),
         [(f"{T0 + 10:.6f}", "aa:bb:cc", "svc.example.test")])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    text = _events(str(src))
    parquet = _events(f"parquet:{arch}")
    assert parquet == text, "the archive transport must not silently drop or corrupt a field"

    services = {e.kind: e for e in parquet if isinstance(e, ServiceEvent)}
    assert services["smtp"].attrs["date"] == "Wed, 06 Aug 2026 10:15:00 -0700"
    assert services["sip"].attrs["date"] == "Wed, 06 Aug 2026 10:15:00 -0700"
    assert services["snmp"].attrs["duration"] == "0.500000", \
        "the raw Zeek text, not a reformatted float"
    assert services["smtp"].attrs["helo"] == "mail.example.test"
    assert services["snmp"].attrs["community"] == "public"


def test_ocsp_is_equivalent_across_the_text_and_archive_transports(tmp_path):
    """`ocsp.log` (`Q29`) is the opposite case from `LES-092`'s "`_attrs()`-only column must
    stay VARCHAR" rule: `_ocsp` reads `thisUpdate`/`nextUpdate` through `_num()`, a NAMED
    numeric accessor -- `ProfileEvent` has no `attrs` bag at all to route them through instead
    -- so `make_zeek_archive.py`'s `_STEM_TYPES` types both columns DOUBLE, the same treatment
    `conn.duration`/`capture_loss.percent_lost` already get. This test is what proves that
    choice safe: the computed `nextUpdate - thisUpdate` delta must be byte-identical across
    transports, not merely close, or the archive would silently compute a different cache
    -lifetime number than the text path for the exact same evidence.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    _tsv(src / "ocsp.log",
         ("ts", "id", "certStatus", "thisUpdate", "nextUpdate"),
         [(f"{T0:.6f}", "resp-1", "good", f"{T0:.6f}", f"{T0 + 604800:.6f}"),
          (f"{T0 + 1:.6f}", "resp-2", "good", f"{T0 + 100:.6f}", f"{T0 + 100 + 601200:.6f}")])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    text = _events(str(src))
    parquet = _events(f"parquet:{arch}")
    assert parquet == text, "the archive transport must compute the identical cache-lifetime delta"
    assert not any(isinstance(e, ServiceEvent) for e in parquet), \
        "ocsp must never become a ServiceEvent on either transport"

    from aximprint.events import ProfileEvent
    deltas = sorted(e.value for e in parquet if isinstance(e, ProfileEvent))
    assert deltas == [601200.0, 604800.0]


def test_json_lines_vector_field_round_trips_through_the_archive_writer(tmp_path):
    """`M3`, code review (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3):
    `make_zeek_archive.py::_coerce` used to `str()` a native `list` (a JSON-lines source's own
    decode of a Zeek `vector`/`set` field) straight into a Python REPR
    (`"['10.62.0.2', '198.51.100.7']"`) instead of joining it on `,` first — silently
    violating this archive's own contract (`docs/SPEC_zeek_metadata_archive.md` "Columns": a
    vector/set field "may be either a Parquet LIST or a comma-joined VARCHAR"). `LES-092` only
    ever fixed the READER half (`ingest/zeek/plugin.py::_s()`); this is the WRITER half, caught
    only because neither field this test exercises (`smtp.path`, `x509.san.dns`) is PROJECTED
    (`LES-091`'s mirror trap) — asserted here at the EVENT level, not through a feature file, so
    a future projection change cannot make this regress invisibly again.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    (src / "smtp.log").write_text(json.dumps({
        "ts": T0, "id.orig_h": "10.0.0.21", "id.orig_p": 51000,
        "id.resp_h": "10.0.0.15", "id.resp_p": 25,
        "path": ["10.0.0.15", "198.51.100.7"], "helo": "mail.example.test",
    }) + "\n")
    (src / "x509.log").write_text(json.dumps({
        "ts": T0 + 1, "fingerprint": "aa:bb:cc",
        "san.dns": ["svc.example.test", "alt.example.test"],
    }) + "\n")
    arch = make_archive.build_archive(src, tmp_path / "arch")

    text = _events(str(src))
    parquet = _events(f"parquet:{arch}")
    assert parquet == text, "the archive transport must not silently corrupt a vector field"

    services = {e.kind: e for e in parquet if isinstance(e, ServiceEvent)}
    assert services["smtp"].attrs["path"] == "10.0.0.15,198.51.100.7"
    assert services["x509"].attrs["san.dns"] == "svc.example.test,alt.example.test"
    for attrs in (services["smtp"].attrs, services["x509"].attrs):
        for v in attrs.values():
            assert not v.startswith("["), f"a Python list repr leaked through: {v!r}"


def test_hive_partitioning_false_prevents_partition_key_from_shadowing_a_data_column(tmp_path):
    """Regression test for `S1` (code review, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3).
    This archive's own layout partitions on `log=<stem>/date=<D>` (Hive-style —
    `docs/SPEC_zeek_metadata_archive.md` "Layout"), and DuckDB's DEFAULT hive-partitioning
    inference silently SHADOWS a same-named DATA column (`smtp.date`, a real Zeek field) with
    the partition-derived value, raising nothing — verified once, when `date` was excluded
    from `_smtp`/`_sip` entirely to avoid it. `ParquetArchiveSource._read` now passes
    `hive_partitioning=false` explicitly, which is what let `date` be restored instead of
    excluded forever.

    Reproduced here directly against `ParquetArchiveSource._read`, with a `cols` list that
    deliberately asks for `date` — the exact request `_COLUMNS["smtp"]` now makes for real —
    to prove the FIX holds at the transport, not merely that today's parser choices happen to
    avoid the hazard. If this test ever starts failing because DuckDB's hive-partitioning
    default behavior changed in a way `hive_partitioning=false` no longer neutralizes, that is
    a real regression to chase, not a reason to delete the test.
    """
    src = tmp_path / "zeek"
    src.mkdir()
    _tsv(src / "smtp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "date", "helo"),
         [(f"{T0:.6f}", "10.0.0.21", 51000, "10.0.0.15", 25,
           "Wed, 06 Aug 2026 10:15:00 -0700", "mail.example.test")])
    arch = make_archive.build_archive(src, tmp_path / "arch")

    source = zs.ParquetArchiveSource(str(arch))
    source.streams(zp.wanted_columns())
    cols = ["ts", "date", "helo", *zs.ARCHIVE_PROVENANCE]
    rows = list(source._read("smtp", source._partitions("smtp"), cols, None))
    source.close()

    assert rows[0]["helo"] == "mail.example.test", "an unrelated column must round-trip untouched"
    assert rows[0]["date"] == "Wed, 06 Aug 2026 10:15:00 -0700", (
        "hive_partitioning=false should make the archive's own date= partition directory "
        "irrelevant to what this column reads back as")


# -- the parser/column tables stay in step ---------------------------------------

def test_every_parser_declares_its_columns():
    """`_COLUMNS` and `_PARSERS` are two halves of one fact.

    A parser with no declaration would project nothing on the archive transport
    and yield zero events from a log full of rows — the quiet failure `LES-017`
    is about. Asserted at import too; asserted here so the reason is written down.
    """
    assert set(zp._COLUMNS) == set(zp._PARSERS)


def test_required_columns_are_a_subset_of_wanted_columns():
    wanted = zp.wanted_columns()
    for stem in zp._PARSERS:
        assert set(zp.required_columns(stem)) <= set(wanted[stem])


def _parse_spec_columns_table(text: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Parse `docs/SPEC_zeek_metadata_archive.md`'s `### Columns` markdown table into the same
    shape as `plugin._COLUMNS`: `{stem: (required, optional)}`.

    Hand-rolled rather than reused from anything `_COLUMNS` itself feeds (`_schema()`,
    `wanted_columns()`) on purpose: the SPEC is prose a human wrote independently, which the
    code must match, not the other way around — a parser built FROM `_COLUMNS` could not
    catch `_COLUMNS` drifting away from what the SPEC still promises. A cell is either a
    comma-separated list of backtick-quoted field names, or a bare em-dash (`—`) meaning
    "nothing in this column" (`kerberos`/`known_hosts`'s empty optional set).
    """
    section = text.split("### Columns", 1)[1].split("### Provenance columns", 1)[0]

    def _fields(cell: str) -> tuple[str, ...]:
        cell = cell.strip()
        if cell == "—":
            return ()
        return tuple(f.strip().strip("`") for f in cell.split(","))

    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| log "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        stem = cells[0].strip("`")
        out[stem] = (_fields(cells[1]), _fields(cells[2]))
    return out


def test_spec_columns_table_matches_the_code():
    """`docs/SPEC_zeek_metadata_archive.md`'s Columns table is a SEVENTH table keyed by the
    Zeek-stem vocabulary — beside `_PARSERS`, `_COLUMNS`, `_SKIP` (`ingest/zeek/plugin.py`)
    and `KNOWN_PARSED_STEMS`/`KNOWN_SKIPPED_STEMS`/`_SCHEMAS` (`acquire/harvest/zeek.py`),
    `LES-087`'s own list of six. It is normative ("a missing column is fatal" for the
    required set), hand-maintained, and nothing checked it against `_COLUMNS` until this
    test: the 2026-08-11 `software` round left it with 16 rows against the code's 17,
    unnoticed until `docs/WORK_ORDER_zeek_stem_coverage.md` build step 2b's own column
    widening (`ldap_search`/`smb_files`/`ntp`) was hand-mirrored into it a second time with
    still nothing checking either round. Compared as sets, not ordered tuples — the SPEC
    table's cell order is prose, not a claim about `_COLUMNS`' own tuple order. Keep the
    table hand-written (the prose around it is doing real work); this only makes drift
    between it and the code fail loud instead of silently.
    """
    spec = _parse_spec_columns_table(_SPEC_PATH.read_text())
    assert set(spec) == set(zp._COLUMNS), (
        f"stems only in the SPEC table: {sorted(set(spec) - set(zp._COLUMNS))}; "
        f"stems only in _COLUMNS: {sorted(set(zp._COLUMNS) - set(spec))}")
    for stem, (required, optional) in zp._COLUMNS.items():
        spec_required, spec_optional = spec[stem]
        assert set(spec_required) == set(required), (
            f"{stem}: SPEC required {sorted(spec_required)} != code {sorted(required)}")
        assert set(spec_optional) == set(optional), (
            f"{stem}: SPEC optional {sorted(spec_optional)} != code {sorted(optional)}")
