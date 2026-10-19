#!/usr/bin/env python3
"""Build a Zeek metadata archive from a Zeek log directory — TEST TOOLING.

**This is not the production converter.** The real one is a standalone tool
outside this repo (user's call, CHANGELOG 2026-08-05): pcap extraction and Zeek
run once, expensively, and aximprint re-reads their output many times. What lives
here is the *reference* implementation of
`docs/SPEC_zeek_metadata_archive.md` — small enough to read as an executable
statement of the contract, and used by the test suite and `verify_tier.sh` to
produce an archive the reader can be checked against.

It deliberately reuses `ingest.zeek.sources._rows` to decode the text logs, so
what lands in the archive is exactly what the text path would have parsed. That
keeps the equivalence check in `tests/test_zeek_parquet_source.py` honest about
the thing it is actually testing: the *reader*, not a second decoder.

Usage:
  python scripts/make_zeek_archive.py <zeek-log-dir> <archive-root> [--node NAME]

Privacy (`LES-003`): an archive built from a real capture inherits that capture's
sensitivity in full — it carries raw Zeek records. Point this at synthetic logs
unless you have decided otherwise deliberately.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb  # noqa: E402

from aximprint.ingest.zeek import plugin as zp  # noqa: E402
from aximprint.ingest.zeek import sources as zs  # noqa: E402

#: Column -> DuckDB type, for columns whose type is the SAME regardless of which log stem
#: they appear on (a port number is a port number everywhere). Typing these once here is
#: what buys the reader its speed: `_f`/`_i`/`_num` stop running a try/except float() per
#: numeric field per row on every re-read. Everything unlisted here AND in `_STEM_TYPES`
#: below defaults to VARCHAR.
_TYPES = {
    "ts": "DOUBLE",
    "id.orig_p": "BIGINT", "id.resp_p": "BIGINT", "port_num": "BIGINT",
}

#: `(stem, column) -> DuckDB type`, for columns whose type depends on WHICH LOG they come
#: from. `duration` is `conn.log`'s own summarized-flow duration, read through a NAMED,
#: numeric-coercing field accessor (`_f`, which `float()`s the value regardless of the
#: archive column's own type) — but `snmp.log` ALSO has a `duration` field, reached only
#: through `ingest/zeek/plugin.py::_attrs()` (an opaque string bag, `_s()`), and typing both
#: under one bare-name key silently broke the second: DuckDB hands `_attrs()` a native float
#: instead of the string it promises, so `str(64.0) == "64.0"` on the archive transport
#: disagreed with Zeek's own `"64.000000"` on the TSV transport — same value, different bytes
#: (`LES-092`; code review `S1`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3, restoring the
#: field this table's OLD bare-name keying had forced `ingest/zeek/plugin.py::_snmp` to
#: exclude entirely). Keyed by `(stem, column)` so this class of collision cannot recur: a
#: column reached only through `_attrs()` must NEVER get an entry here (LES-092's own rule,
#: now structurally enforceable per stem instead of globally); a column read through a named
#: numeric accessor safely may, on exactly the stem(s) where it means that.
_STEM_TYPES: dict[tuple[str, str], str] = {
    ("conn", "duration"): "DOUBLE",
    ("conn", "orig_bytes"): "BIGINT", ("conn", "resp_bytes"): "BIGINT",
    ("conn", "orig_pkts"): "BIGINT", ("conn", "resp_pkts"): "BIGINT",
    ("dhcp", "lease_time"): "DOUBLE",
    ("capture_loss", "percent_lost"): "DOUBLE",
    ("stats", "pkts_dropped"): "DOUBLE", ("stats", "pkt_lag"): "DOUBLE",
    # `pkts_proc`/`bytes_recv` (`SensorHealthEvent` kinds added this round, user-ruled
    # addendum): also read through `_num()`, same safety as their two neighbours above.
    ("stats", "pkts_proc"): "DOUBLE", ("stats", "bytes_recv"): "DOUBLE",
    ("ldap_search", "result_count"): "BIGINT",
    # `ocsp.log`'s `thisUpdate`/`nextUpdate` (`Q29`) are read through `_num()` in
    # `ingest/zeek/plugin.py::_ocsp` -- a NAMED numeric-coercing accessor, exactly the `_f`/`_num`
    # shape `conn.duration`/`capture_loss.percent_lost` above already get DOUBLE for, and NOT
    # `_attrs()` (`ProfileEvent` has no attrs bag at all to route them through). `_ocsp` subtracts
    # the two directly; typing them DOUBLE here is what LES-092's own rule recommends for a
    # numeric-accessor column, and buys the same per-row float()-avoidance speedup the other
    # entries in this table exist for. Safe on both transports: `_num(v)`'s `v in _UNSET` check
    # tolerates a native Python float exactly as it tolerates a string (no TypeError, unlike the
    # `_s()`/list case `LES-092` also names), and `float()` on an already-float value is a no-op.
    ("ocsp", "thisUpdate"): "DOUBLE", ("ocsp", "nextUpdate"): "DOUBLE",
}


def _column_type(stem: str, name: str) -> str:
    """The DuckDB type for `stem`'s `name` column: stem-specific first (`_STEM_TYPES`), then
    universal (`_TYPES`), then the VARCHAR default. One function so the three call sites that
    need a column's type (`_coerce`, the CSV load's own `columns=`, and the manifest's
    `logs.<stem>.columns` block) can never disagree about which table wins.
    """
    return _STEM_TYPES.get((stem, name)) or _TYPES.get(name) or "VARCHAR"


_PROVENANCE = {"_pcap": "VARCHAR", "_zeek_run": "VARCHAR", "_row_no": "BIGINT",
               zs.DISPLACEMENT_COLUMN: "DOUBLE"}


def _archive_column_type(stem: str, name: str) -> str:
    """`_column_type`, with provenance columns (`_pcap` etc.) taking priority — they are
    never stem-derived data, so they must resolve the same way regardless of `stem`.
    """
    return _PROVENANCE.get(name) or _column_type(stem, name)

#: Null sentinel in the staging CSV. Explicit rather than an empty field, so a
#: genuinely empty string and an unset field stay distinguishable through the
#: bulk load — the `LES-007` distinction the archive exists to carry properly.
_NULL = "\\N"


def _column_union(paths) -> list[str]:
    """Column names in first-seen order, taken from each log's own header.

    Zeek TSV declares its columns in `#fields`, so one header line per file
    settles it without parsing a body; JSON-lines declares nothing, so those are
    scanned. First-seen order is a function of the input bytes and therefore
    reproducible (`LES-009`) — it decides column order in the emitted Parquet.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(keys):
        for k in keys:
            if k not in seen:
                seen.add(k)
                out.append(k)

    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", errors="replace") as fh:
            head = fh.readline()
        if head.startswith("#"):
            with opener(path, "rt", errors="replace") as fh:
                sep = "\t"
                for line in fh:
                    if not line.startswith("#"):
                        break
                    if line.startswith("#separator"):
                        val = line.split(" ", 1)[1].strip()
                        sep = (val.encode().decode("unicode_escape")
                               if val.startswith("\\x") else val)
                    elif line.startswith("#fields"):
                        add(line.rstrip("\n").split(sep)[1:])
                        break
        else:
            for r in zs._rows(path):
                add(r.keys())
    return out


def _coerce(stem: str, name: str, raw):
    """Text cell -> typed value. Unset becomes a real null, never a zero.

    `LES-007`: at a sensor boundary unset is a distinct value from zero, and this
    is the boundary where the distinction stops being a string convention and
    becomes a column property.

    A `list`/`tuple` `raw` — a JSON-lines source's native decode of a Zeek
    `vector`/`set` field, e.g. `smtp.path` — is joined on `,` FIRST, mirroring
    `ingest/zeek/plugin.py::_s()` exactly (matching Zeek's own TSV convention for the
    same field). Without this, `str(raw)` on a list produced a Python REPR
    (`"['10.62.0.2', '198.51.100.7']"`), not a comma-joined string — silently
    violating this archive's own contract
    (`docs/SPEC_zeek_metadata_archive.md` "Columns": "`answers`/`TTLs` may be either
    a Parquet `LIST` or a comma-joined `VARCHAR`"). This is the WRITER half of a bug
    `LES-092` only ever fixed on the READER side (code review `M3`,
    `docs/WORK_ORDER_zeek_stem_coverage.md` step 3) — it passed the transport
    -equivalence gate only because neither `smtp.path` nor `x509.san.dns` was ever
    PROJECTED, so `_add_service` never read the corrupted archive value back out at
    all (`LES-091`'s mirror trap, from the writer's side this time).
    """
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    if raw is None or raw == "" or raw == "-":
        return None
    t = _column_type(stem, name)
    if t == "VARCHAR":
        return str(raw)
    try:
        return float(raw) if t == "DOUBLE" else int(float(raw))
    except (TypeError, ValueError):
        return None


def _utc_date(ts: float) -> str:
    """Partition label from the row's OWN ts, in UTC.

    UTC explicitly, not the session's timezone: a partition boundary that moved
    with the machine's locale would put the same row in different files on
    different hosts, and the archive would stop being reproducible (`LES-009`).
    """
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def build_archive(src: Path, dest: Path, node: str = "synthetic",
                  zeek_run: str = "run-000", pcap: str = "synthetic.pcap",
                  zeek_version: str = "test-fixture") -> Path:
    src, dest = Path(src), Path(dest)
    (dest / "parquet").mkdir(parents=True, exist_ok=True)
    # Self-protecting output, exactly as `features/jsonl.py` does it (`LES-011`):
    # an archive is capture-derived and MORE identity-dense than a feature archive
    # — raw Zeek records — and the destination may legitimately sit inside the
    # repo. A `*` ignore in the archive root covers every artifact including
    # `manifest.json`, without a repo-wide rule that would shadow a legitimate
    # manifest elsewhere in the tree.
    (dest / ".gitignore").write_text(
        "# Zeek metadata archive — capture-derived, never committed (LES-003).\n*\n")
    con = duckdb.connect(":memory:")

    by_stem: dict[str, list[Path]] = {}
    for p in sorted(src.rglob("*")):
        if not zs._is_log_file(p):
            continue
        stem = zs._stem(p)
        if stem in zp._COLUMNS:     # skipped/unrecognized types are not converted
            by_stem.setdefault(stem, []).append(p)

    logs_meta: dict[str, dict] = {}
    min_ts, max_ts = None, None

    for stem, paths in sorted(by_stem.items()):
        # Streamed in bounded batches, never materialized. The first cut of this
        # held every row in a Python list and bound them one at a time through
        # `executemany`: 400k rows cost 19 min of CPU and 917 MB RSS. A reference
        # implementation that collapses at the scale the archive exists for is
        # worse than none, because someone will build the real converter from it.
        # Column union first, from the logs' own `#fields` headers — one line per
        # file for TSV, a full scan only for JSON-lines (which have no header).
        data_cols = _column_union(paths)
        all_cols = [*_PROVENANCE, *data_cols]

        # Decode to a typed staging CSV, then let DuckDB bulk-load it in C++.
        # Two rejected alternatives, both measured on 400k rows: `executemany`
        # cost 19 min of CPU and 917 MB RSS, and batched multi-row INSERT was
        # still CPU-bound at 5k rows per statement. Parameter binding is simply
        # the wrong tool for bulk load; the CSV reader is the right one, and it
        # is what SPEC "Reference conversion" describes.
        running_max = float("-inf")
        max_disp = 0.0
        n_disp = n_beyond = n_rows = 0
        lo_ts = hi_ts = None
        dates: set[str] = set()
        staging = dest / f".staging-{stem}.csv"
        with open(staging, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(all_cols)
            for path in paths:
                for r in zs._rows(path):
                    try:
                        ts = float(r.get("ts"))
                    except (TypeError, ValueError):
                        continue
                    disp = None
                    if running_max > ts:
                        disp = running_max - ts
                        max_disp = max(max_disp, disp)
                        n_disp += 1
                        if disp > zp.REORDER_HORIZON_SECONDS:
                            n_beyond += 1
                    else:
                        running_max = ts
                    w.writerow([
                        pcap, zeek_run, n_rows, _NULL if disp is None else repr(disp),
                        *(_NULL if (v := _coerce(stem, c, r.get(c))) is None else v
                          for c in data_cols)])
                    n_rows += 1
                    lo_ts = ts if lo_ts is None else min(lo_ts, ts)
                    hi_ts = ts if hi_ts is None else max(hi_ts, ts)
                    dates.add(_utc_date(ts))
        if not n_rows:
            staging.unlink()
            continue

        cols_sql = ", ".join(
            f"'{c}': '{_archive_column_type(stem, c)}'" for c in all_cols)
        src_sql = (f"read_csv('{staging}', header=true, delim=',', "
                   f"nullstr='{_NULL}', columns={{{cols_sql}}})")

        # Partition on the row's own ts; sorted within each file.
        base = dest / "parquet" / f"log={stem}"
        for d in sorted(dates):
            part = base / f"date={d}"
            part.mkdir(parents=True, exist_ok=True)
            lo = datetime.datetime.strptime(d, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc).timestamp()
            con.execute(
                f'COPY (SELECT * FROM {src_sql} WHERE "ts" >= {lo} '
                f'AND "ts" < {lo + 86400} ORDER BY "ts", "_zeek_run", "_row_no") '
                f"TO '{part / 'part-000.parquet'}' (FORMAT PARQUET)")
        staging.unlink()

        min_ts = lo_ts if min_ts is None else min(min_ts, lo_ts)
        max_ts = hi_ts if max_ts is None else max(max_ts, hi_ts)
        logs_meta[stem] = {
            "rows": n_rows,
            "files": len(dates),
            "columns": {c: _archive_column_type(stem, c)
                        for c in (*_PROVENANCE, *data_cols)},
            "sorted_by": "ts",
            # Measured, not assumed. An archive that cannot state this is refused
            # by the reader, because the sort has erased the only other evidence
            # (`LES-006`, `LES-013`).
            "disorder": {
                "measured": True,
                "max_displacement_seconds": max_disp,
                "rows_displaced": n_disp,
                f"rows_beyond_{int(zp.REORDER_HORIZON_SECONDS)}s": n_beyond,
            },
        }

    manifest = {
        "capture_node": node,
        "producer": {"tool": "scripts/make_zeek_archive.py (reference/test)",
                     "version": "0", "zeek_versions": [zeek_version]},
        "wallclock": {"converted_at": None},   # the one non-reproducible key
        "time_bounds": {"min_ts": min_ts, "max_ts": max_ts},
        "pcaps": {"count": 1, "spans": [{"name": pcap,
                                         "min_ts": min_ts, "max_ts": max_ts}]},
        "boundary": {"policy": "time-aligned pcaps; split flows accepted",
                     "flows_within_1s_of_boundary": None},
        "dedup": {"policy": "none", "rows_removed": 0},
        "logs": logs_meta,
    }
    # Written last, and only now that every parquet file is on disk: a manifest
    # in a half-written archive is worse than no archive (features/jsonl.py's
    # discipline, same reason).
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    con.close()
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="Zeek log directory")
    ap.add_argument("dest", help="archive root to create")
    ap.add_argument("--node", default="synthetic")
    args = ap.parse_args()
    out = build_archive(Path(args.source), Path(args.dest), node=args.node)
    doc = json.loads((out / "manifest.json").read_text())
    total = sum(v["rows"] for v in doc["logs"].values())
    print(f"archive {out}: {len(doc['logs'])} log type(s), {total:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
