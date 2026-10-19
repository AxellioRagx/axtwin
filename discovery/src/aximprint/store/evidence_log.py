"""Append-only evidence log (JSONL). Source of truth; the DB is a projection.

Event-sourcing buys crash recovery, re-derivation with improved extractors, and
per-belief provenance (DESIGN_BRIEF §7). Contents derive from real traffic and may
carry secrets/PII — treat the log as sensitive.
"""
from __future__ import annotations

import json
from pathlib import Path


class EvidenceLog:
    """Append-only. The handle is opened once, here, and held until `close()`.

    Previously `append`/`append_many` each opened and closed the file on their
    own, so a bad destination (a directory, a permission error, a full
    filesystem) surfaced only on the FIRST write — which, in a multi-sink run,
    can be after every sink ahead of this one has already written a full
    window (`M6`), and it reopened the file twice per window (`S7`: once for
    the `window_closed` record, once for the `assertion` records). Opening
    eagerly, in append mode, validates the destination at construction time —
    matching `EvidenceSink.open()`'s place in the sink lifecycle, and the same
    "open once, write per call, close once" discipline `features/jsonl.py`
    already uses.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def append(self, record: dict) -> None:
        self._fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        # Flushed here, not batched until `close()`: a reader opening this same
        # path independently (a test comparing two logs byte for byte, or an
        # operator tailing the file mid-run) must see this record without
        # waiting for the whole run to finish — the same visibility the old
        # open-write-close-per-call version gave for free.
        self._fh.flush()

    def append_many(self, records) -> None:
        for r in records:
            self._fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
