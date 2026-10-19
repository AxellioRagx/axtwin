#!/usr/bin/env python3
"""inspect_json — count the samples in a JSON/JSONL file and print their schema.

Built for the feature archive (`aximprint features --out <dir>`), where the
questions are always the same: how many rows are in here, what fields does a row
carry, and which of those fields are *stable* across rows. The last one matters
most — `proto_mix` and `listen_ports` are open, sensor-discovered vocabularies
(`Q6`), so a field whose keys change from sample to sample is a finding, not a
detail. The schema output makes that visible by showing per-key presence rather
than a merged key list.

Formats: JSON Lines (streamed — the sample count never loads the file), a
top-level JSON array (parsed), or a single JSON object (one sample — useful on
`manifest.json`).

**Values are not printed by default.** These files derive from real capture data
and inherit its sensitivity (CONVENTIONS §3, `LES-003`); the schema alone answers
the structural question. Pass `--values` when you actually want examples, and
mind where you paste the output.

Usage:
  python scripts/inspect_json.py data/features-20260803/nodes.jsonl
  python scripts/inspect_json.py <file> -n 20 --offset 1000
  python scripts/inspect_json.py <file> --values          # include example values
  python scripts/inspect_json.py <file> --count-only      # just the sample count

Read-only, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

MAX_ENUM_KEYS = 24   # dicts with more distinct keys than this collapse to <key> -> type
VALUE_WIDTH = 58     # truncation for --values


# --- loading ----------------------------------------------------------------

def _sniff(path: Path) -> str:
    """jsonl | document — decided by whether the FIRST LINE is a complete JSON value.

    That is the only honest discriminator. A leading `{` says nothing: it opens a
    one-line JSONL record and a pretty-printed multi-line object identically, and
    guessing from it misreads a pretty-printed file as N records of garbage.
    """
    first = None
    more = False
    with path.open("rt", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            if first is None:
                first = line
                continue
            more = True
            break
    if first is None:
        raise SystemExit(f"empty file: {path}")
    try:
        json.loads(first)
    except json.JSONDecodeError:
        return "document"          # line 1 is a fragment -> one multi-line value
    return "jsonl" if more else "document"


def load(path: Path, offset: int, n: int) -> tuple[list, int, int, str]:
    """Return (samples, total, malformed, format). JSONL streams; the count is exact."""
    if _sniff(path) == "document":
        # No line structure to stream over, so this parses the whole file. The
        # archive's own row artifacts are JSONL and stay streamed.
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}: not valid JSON or JSON Lines — {exc}")
        if isinstance(data, list):
            return data[offset:offset + n], len(data), 0, "JSON array"
        return ([data] if offset == 0 else []), 1, 0, "JSON object"

    samples, total, malformed = [], 0, 0
    with path.open("rt", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            idx = total
            total += 1
            if offset <= idx < offset + n:
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    malformed += 1
    return samples, total, malformed, "JSON Lines"


# --- schema inference -------------------------------------------------------

def _tname(v) -> str:
    return {bool: "bool", int: "int", float: "float", str: "str",
            list: "list", dict: "dict", type(None): "null"}.get(type(v), type(v).__name__)


class Node:
    """One position in the schema: the merged shape of every value seen there."""

    def __init__(self):
        self.types: Counter = Counter()
        self.seen = 0                    # occurrences of this position
        self.fields: dict[str, Node] = {}   # dict children, insertion-ordered
        self.key_counts: list[int] = []     # keys per dict occurrence
        self.item: Node | None = None       # merged list element
        self.lengths: list[int] = []        # list lengths
        self.examples: list = []

    def add(self, value) -> None:
        self.seen += 1
        self.types[_tname(value)] += 1
        if isinstance(value, dict):
            self.key_counts.append(len(value))
            for k, v in value.items():
                self.fields.setdefault(k, Node()).add(v)
        elif isinstance(value, list):
            self.lengths.append(len(value))
            if self.item is None:
                self.item = Node()
            for v in value:
                self.item.add(v)
        elif len(self.examples) < 3 and value is not None:
            self.examples.append(value)

    # -- rendering helpers --

    def type_label(self) -> str:
        base = "|".join(t for t, _ in self.types.most_common())
        if "list" in self.types and self.item is not None:
            inner = "|".join(t for t, _ in self.item.types.most_common()) or "empty"
            if self.lengths and len(set(self.lengths)) == 1:
                base = f"list[{inner}] x{self.lengths[0]}"
            elif self.lengths:
                base = f"list[{inner}] len {min(self.lengths)}-{max(self.lengths)}"
        elif "dict" in self.types and self.key_counts:
            lo, hi = min(self.key_counts), max(self.key_counts)
            span = f"{lo}" if lo == hi else f"{lo}-{hi}"
            base = f"dict ({span} keys)"
        return base

    def is_open_vocabulary(self) -> bool:
        """True when the dict's keys are data, not a fixed record shape.

        Any child absent from some occurrence means the key set varies — the
        signature of a runtime-discovered vocabulary (Q6) rather than a record.
        """
        return bool(self.fields) and any(c.seen < self.seen for c in self.fields.values())


def render(node: Node, out: list, show_values: bool, indent: int = 0,
           parent_seen: int = 0) -> None:
    pad = "  " * (indent + 1)
    open_vocab = node.is_open_vocabulary()

    if open_vocab and len(node.fields) > MAX_ENUM_KEYS:
        merged = Node()
        for child in node.fields.values():
            merged.types.update(child.types)
            merged.seen += child.seen
        out.append(f"{pad}{'<key>':<{max(10, 34 - indent * 2)}} "
                   f"{merged.type_label():<20} "
                   f"({len(node.fields)} distinct keys in these samples)")
        return

    for name, child in node.fields.items():
        label = f"{pad}{name}"
        pad_to = max(len(label) + 2, 36)
        presence = f"{child.seen}/{parent_seen or node.seen}"
        line = f"{label:<{pad_to}}{child.type_label():<22}{presence:>7}"
        if open_vocab and child.seen < node.seen:
            line += "  ~"        # this key is not in every sample
        if show_values and child.examples:
            ex = ", ".join(_fmt(v) for v in child.examples[:2])
            line += f"   e.g. {ex[:VALUE_WIDTH]}"
        out.append(line)

        if child.fields:
            render(child, out, show_values, indent + 1, child.seen)
        elif child.item is not None and child.item.fields:
            out.append(f"{'  ' * (indent + 2)}[each item]")
            render(child.item, out, show_values, indent + 2, child.item.seen)


def _fmt(v) -> str:
    s = json.dumps(v, default=str)
    return s if len(s) <= VALUE_WIDTH else s[:VALUE_WIDTH - 1] + "…"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="JSON, JSON Lines, or a directory of them")
    ap.add_argument("-n", "--samples", type=int, default=5,
                    help="samples to infer the schema from (default 5)")
    ap.add_argument("--offset", type=int, default=0, help="first sample to read (default 0)")
    ap.add_argument("--values", action="store_true",
                    help="include example values — capture-derived, mind where it goes")
    ap.add_argument("--count-only", action="store_true", help="print the sample count and stop")
    args = ap.parse_args()

    root = Path(args.path)
    if not root.exists():
        raise SystemExit(f"no such path: {root}")
    if args.samples < 1 or args.offset < 0:
        raise SystemExit("--samples must be >= 1 and --offset >= 0")

    targets = (sorted(p for p in root.iterdir()
                      if p.suffix in (".json", ".jsonl") and p.is_file())
               if root.is_dir() else [root])
    if not targets:
        raise SystemExit(f"no .json/.jsonl files in {root}")

    for i, path in enumerate(targets):
        if i:
            print()
        samples, total, malformed, fmt = load(path, args.offset, args.samples)

        print(f"{path}")
        print(f"  format   {fmt}")
        print(f"  size     {_human(path.stat().st_size)}")
        print(f"  samples  {total:,}")
        if malformed:
            print(f"  MALFORMED {malformed} line(s) in the requested range could not be parsed")
        if args.count_only:
            continue

        if not samples:
            print(f"\n  offset {args.offset} is past the end ({total:,} samples)")
            continue

        merged = Node()
        for s in samples:
            merged.add(s)

        end = args.offset + len(samples) - 1
        print(f"\n  schema from {len(samples)} sample(s) at offset "
              f"{args.offset}{f'-{end}' if len(samples) > 1 else ''}")
        if not merged.fields:
            print(f"    (samples are {merged.type_label()}, not objects)")
            continue
        print(f"  {'field':<34}{'type':<22}{'seen':>7}")
        print("  " + "-" * 62)
        lines: list[str] = []
        render(merged, lines, args.values, 0, len(samples))
        print("\n".join(lines))
        if merged.is_open_vocabulary() or any(
                c.is_open_vocabulary() for c in merged.fields.values()):
            print("\n  ~ = absent from some samples: an optional field, or — where the"
                  " keys are data\n      rather than a record shape — an open vocabulary"
                  " (Q6). Widen -n to tell them apart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
