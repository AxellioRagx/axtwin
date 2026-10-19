"""`EvidenceSink`: the append-only evidence log as a registered window sink (D5).

The evidence log is the source of truth (DESIGN_BRIEF §7); the belief store
(`store_sink.py`) is a projection of it. `WORK_ORDER_egress_sink_unification.md`
D5 makes it a sink of its own, `evidence=<path>`, rather than an option of the
store sink — inverting that relationship in the CLI (`--evidence-log` as a
store flag) would make the derived artifact own the primary one in the
argument surface, backwards from §7's actual relationship.

Writes exactly what `cmd_ingest` wrote before this sink existed: one
`window_closed` record and one `assertion` record per assertion, per window.
Nothing else — no run-level record is appended here, so introducing this sink
does not change the evidence log's bytes for an existing invocation (Boundaries:
a sink must not change what it writes).

Import direction: this file imports `sinks.base` (down), `aximprint.store`
(down, to wrap `EvidenceLog`), and `aximprint.windowing` (down, for the type).
Nothing upward.
"""
from __future__ import annotations

from collections import Counter

from aximprint.sinks.base import WindowSink, register
from aximprint.store import EvidenceLog
from aximprint.windowing.windows import WindowResult


@register
class EvidenceSink(WindowSink):
    """Appends one `window_closed` + N `assertion` records per window.

    `vocabulary` is not a parameter of `open()` here — this sink does not
    declare it (D4). `rows` counts records appended THIS RUN, by record type
    (`window_closed`, `assertion`) — the log is append-only across runs the
    same way the belief store's tables are, so the same "this run, not the
    whole file" reading applies (see `StoreSink`).
    """

    name = "evidence"

    def __init__(self) -> None:
        self._log: EvidenceLog | None = None
        self._path: str = ""
        self._windows: int = 0
        self._rows: Counter = Counter()

    # -- lifecycle --------------------------------------------------------------

    def open(self, destination: str, run_meta: dict) -> None:
        # `run_meta` is accepted per the contract and unused: this sink has
        # nothing to stamp (no run-level record is appended — see module
        # docstring). Unlike `StoreSink`, there is no `runs`-table equivalent
        # to persist it into.
        del run_meta
        self._path = destination
        # `EvidenceLog.__init__` opens (and thereby validates) the file handle
        # eagerly now (`M6`) — a bad destination (a directory, a permission
        # error) raises HERE, before any sink ahead of this one in a multi-sink
        # run has written a single window, rather than surfacing later on the
        # first `write_window` call.
        self._log = EvidenceLog(destination)

    def write_window(self, win: WindowResult, assertions: list[dict]) -> None:
        if self._log is None:
            raise RuntimeError("write_window before open()")  # fail loud
        self._log.append({"type": "window_closed", "window_start": win.window_start,
                          "health": win.health})
        self._log.append_many({"type": "assertion", **a} for a in assertions)
        self._rows["window_closed"] += 1
        self._rows["assertion"] += len(assertions)
        self._windows += 1

    def close(self) -> dict:
        if self._log is None:
            raise RuntimeError("close before open()")  # fail loud, as write_window does
        summary = {"destination": self._path, "windows": self._windows,
                   "rows": dict(self._rows), "vocabulary": {}}
        self._log.close()
        self._log = None
        return summary

    def abort(self) -> None:
        """Close the file handle. There is no completion artifact to withhold —
        this sink appends no run-level record (see module docstring) — but the
        handle `open()` now holds open across the whole run (`M6`) still needs
        releasing rather than leaking on a failure surface (`M4`/`M5`)."""
        if self._log is not None:
            self._log.close()
            self._log = None
