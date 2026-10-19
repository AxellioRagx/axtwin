#!/usr/bin/env python3
"""Split a flat Zeek log directory into a window tree — TEST TOOLING.

**This is not a harvester.** The real one, `xtract harvest`, writes a window
tree directly, one directory per pcap window; this script only exists so
aximprint's own gates and tests can turn a flat synthetic fixture into that
same shape, the way `make_zeek_archive.py` turns one into a Parquet archive.
Reused by `scripts/verify_pipeline_gates.sh`'s window-tree equivalence step
(`M4`, `docs/WORK_ORDER_multi_window_log_read.md`).

Deliberately does more than a clean split: a deterministic fraction of
`conn.log` rows are written into the NEXT window's directory instead of
their own — the late-closing-flow pathology `LogDirSource`'s module
docstring describes — so the gate this feeds exercises the actual trap
(`LES-013`: a directory-name sort is not evidence of ts order), not just a
shape a naive concatenation would also have passed.

`conn.log`'s TSV lines are copied VERBATIM (never re-parsed and
re-serialized): only the `ts` field (first column) is inspected, to decide
which window a line belongs in, so the split cannot introduce any byte
-level difference `_rows()` would not already treat as equivalent — the
whole point of the equivalence gate this feeds is comparing bytes a
consumer actually reads, and a re-encoded row is a needless way to fail that
honestly. Every other log type is copied whole into the FIRST window
directory rather than split — only `conn.log` carries enough volume for the
window-tree merge to be worth exercising here.

Usage:
  python scripts/split_zeek_window_tree.py <flat-zeek-dir> <tree-root> \
      [--window-seconds N] [--late-write-every N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aximprint.ingest.zeek.sources import _is_log_file, _stem  # noqa: E402

#: Every Nth `conn.log` row (by position in the SOURCE file, so the choice is
#: a function of the input bytes, never the clock or an RNG — `LES-009`) is
#: pushed one window LATER than its own `ts` would place it. Roughly matches
#: what this round's own hand validation used ("15% of rows written into a
#: later window's directory").
DEFAULT_LATE_WRITE_EVERY = 7

CONN_LOG = "conn.log"


def split_into_window_tree(src: Path, dest: Path, window_seconds: float = 900.0,
                           late_write_every: int = DEFAULT_LATE_WRITE_EVERY) -> Path:
    src, dest = Path(src), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    header_lines: list[str] = []
    buckets: dict[int, list[str]] = {}
    min_ts: float | None = None
    with open(src / CONN_LOG, "rt") as fh:
        row_index = 0
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                header_lines.append(line)
                continue
            ts = float(line.split("\t", 1)[0])
            if min_ts is None:
                min_ts = ts
            window = int((ts - min_ts) // window_seconds)
            if row_index % late_write_every == 0 and row_index > 0:
                window += 1  # deliberately late: lands in the NEXT window
            buckets.setdefault(window, []).append(line)
            row_index += 1

    if min_ts is None:
        raise ValueError(f"{src / CONN_LOG}: no data rows to split")

    def window_id(window: int) -> str:
        # An epoch-seconds string, matching the `timeutil.format_instant`
        # shape `window_id` carries in the real harvest cold tier — an
        # integer here since the synthetic fixture's own `ts` values are.
        return f"{int(min_ts + window * window_seconds)}"

    for window in sorted(buckets):
        wdir = dest / window_id(window)
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / CONN_LOG).write_text("\n".join(header_lines + buckets[window]) + "\n")

    # Every other log type, copied whole into the FIRST window directory —
    # not split; only conn.log's volume makes the window-tree merge worth
    # exercising, and every OTHER stem still needs to be found somewhere so
    # the equivalence comparison covers the whole feature archive, not just
    # the FlowEvent-derived files.
    first = dest / window_id(min(buckets))
    first.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if _is_log_file(p) and _stem(p) != "conn":
            (first / p.name).write_bytes(p.read_bytes())

    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="a flat Zeek log directory")
    ap.add_argument("dest", help="window-tree root to create")
    ap.add_argument("--window-seconds", type=float, default=900.0)
    ap.add_argument("--late-write-every", type=int, default=DEFAULT_LATE_WRITE_EVERY)
    args = ap.parse_args()
    out = split_into_window_tree(Path(args.source), Path(args.dest),
                                 args.window_seconds, args.late_write_every)
    n = len(list(out.iterdir()))
    print(f"window tree {out}: {n} window director{'y' if n == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
