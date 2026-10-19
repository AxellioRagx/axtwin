"""`StoreSink`: the belief store as a registered window sink.

Before `WORK_ORDER_egress_sink_unification.md`, `cmd_ingest` hardcoded
`BeliefStore` directly and never went through a sink contract at all, so a
second store-shaped destination (or a run stamp, build step 6) cost a CLI
change instead of a registration. This file is the adapter that lets the one
shared driver treat the store exactly like every other sink.

`store` remains the layer "imported by writers" (CONVENTIONS §3); this module
IS one of those writers, alongside `features/jsonl.py` and `evidence_sink.py` —
it does not change `store`'s own layering, only adds a consumer of it that
happens to also implement `sinks.base.WindowSink`.

Import direction: this file imports `sinks.base` (down, for the contract),
`aximprint.store` (down, to wrap it), `aximprint.provenance` (down, shared
build stamp — see that module's docstring for why it is shared and not
reimplemented), and `aximprint.windowing` (down, for the type). Nothing upward.
"""
from __future__ import annotations

import time
from collections import Counter

from aximprint.provenance import build_stamp
from aximprint.sinks.base import WindowSink, register
from aximprint.store import BeliefStore
from aximprint.store.belief_store import profile_facts_rows
from aximprint.windowing.windows import WindowResult


@register
class StoreSink(WindowSink):
    """Persists every closed window's features + assertions into the belief store.

    `vocabulary` is not a parameter of `open()` here — this sink does not
    declare it, so the driver never offers it one (D4; see `sinks.base`).

    `rows` in the `close()` summary counts rows THIS RUN inserted, per table —
    not the table's running total. The belief store accumulates across many
    runs (no forgetting yet, `Q5`), so unlike a fresh JSONL archive, "rows
    written this run" and "rows in the table" are genuinely different
    quantities; this sink answers the one an operator watching a single
    invocation can act on (see the finding recorded in
    `WORK_ORDER_egress_sink_unification.md` about what `rows` means here).
    """

    name = "store"

    def __init__(self) -> None:
        self._store: BeliefStore | None = None
        self._path: str = ""
        self._run_meta: dict = {}
        self._windows: int = 0
        self._rows: Counter = Counter()
        self._started: float = 0.0
        #: D5's counts half, set by `note_unparsed_log_types` (called at most once,
        #: after the event stream is fully consumed, on a run that completes — code
        #: review `S7`) and read at `close()`. `None` until then — indistinguishable,
        #: on this field alone, from "the driver never called `note_unparsed_log_types`
        #: at all". Through `cli._pipeline` that ambiguity does not arise: the driver
        #: calls it unconditionally, once, for every opened sink, using the SAME
        #: plugin `WindowManager`'s own provider already reads from — so a `None`
        #: reaching `runs` there always means "the plugin behind this run had
        #: nothing to report", genuinely. It DOES arise for a caller that talks to
        #: `StoreSink` directly, bypassing `cli._pipeline` (an earlier version of
        #: this comment claimed it never surfaces at all -- wrong, and this
        #: codebase's own tests are the counterexample: `tests/test_sinks.py
        #: ::test_store_sink_runs_row_unparsed_log_types_is_null_when_never_told`
        #: relies on exactly this path to exercise the "never told" case, code
        #: review `S7`) -- for such a caller, `None` here is honestly ambiguous
        #: between "not audited" and "not asked", and `runs.unparsed_log_types`
        #: reads `NULL` either way, which is the conservative, correct answer for
        #: both.
        self._unparsed_log_types: dict[str, int] | None = None
        #: `S4`'s additive field, same discipline one line up.
        self._unrecognized_log_types: dict[str, int] | None = None
        #: The run's entity namespace (`note_entity_roster`, `Q1`), written into `entity_roster`
        #: at `close()` — same "set once after the stream, read at close" shape as the two above.
        self._entity_roster: dict | None = None

    # -- lifecycle --------------------------------------------------------------

    def open(self, destination: str, run_meta: dict) -> None:
        self._path = destination
        self._store = BeliefStore(destination)
        self._run_meta = dict(run_meta)
        self._started = time.time()  # the only wall-clock read in this sink

    def write_window(self, win: WindowResult, assertions: list[dict]) -> None:
        if self._store is None:
            raise RuntimeError("write_window before open()")  # fail loud
        self._store.write_window(win, assertions)
        self._rows["node_features"] += len(win.nodes)
        self._rows["edge_features"] += len(win.edges)
        self._rows["assertions"] += len(assertions)
        self._rows["identity_bindings"] += len(win.identity_delta)
        self._rows["window_health"] += 1
        # `profile_facts_rows` (`Q29`, code review `S5`) is the SAME flatten
        # `BeliefStore.write_window` guards and inserts from — imported rather than
        # re-derived here (`sum(len(stats) for stats in ...)` used to be a second,
        # independent statement of the identical rule, with nothing checking the two agreed).
        self._rows["profile_facts"] += len(profile_facts_rows(win))
        self._windows += 1

    def note_unparsed_log_types(self, counts: dict[str, int] | None) -> None:
        """D5's counts half — one of two overrides of `WindowSink`'s default no-op
        (the other is `note_unrecognized_log_types`, immediately below), because
        `runs` is the one run-scoped table this codebase has for a fact like this.
        Just an assignment, read back at `close()`; see that method and
        `belief_store.write_run` for where it actually lands."""
        self._unparsed_log_types = counts

    def note_unrecognized_log_types(self, counts: dict[str, int] | None) -> None:
        """`S4`'s additive field — same shape as `note_unparsed_log_types`,
        immediately above."""
        self._unrecognized_log_types = counts

    def note_entity_roster(self, roster: dict | None) -> None:
        """`Q1`'s roster — one row per BOUND entity, written at `close()` into
        `entity_roster` (see `belief_store.write_entity_roster` for why only the
        bound ones, and why the run's start time is the join key)."""
        self._entity_roster = roster

    def close(self) -> dict:
        if self._store is None:
            raise RuntimeError("close before open()")  # fail loud, as write_window does
        finished = time.time()
        # BEFORE `write_run`, deliberately: the `runs` row is this store's "the run finished"
        # marker (`belief_store.py`'s own module docstring), so anything else a clean close owes
        # the store has to land first. A failure here leaves no `runs` row, which is exactly the
        # signal a reader already knows how to interpret; the reverse order would leave a run
        # marked complete with its roster missing.
        if self._entity_roster is not None:
            self._store.write_entity_roster(self._run_meta.get("source", ""),
                                            self._entity_roster)
            self._rows["entity_roster"] += len(self._entity_roster.get("bound", []))
        # Stamped and persisted only on a clean close (§10's "same build on both
        # sides"): a run that raised mid-way leaves no `runs` row for its window
        # range — the same "no clean close, no summary artifact" signal
        # `features/jsonl.py`'s manifest already relies on (see belief_store.py).
        self._store.write_run(self._run_meta, build_stamp(), self._windows,
                              self._started, finished,
                              unparsed_log_types=self._unparsed_log_types,
                              unrecognized_log_types=self._unrecognized_log_types)
        summary = {"destination": self._path, "windows": self._windows,
                   "rows": dict(self._rows), "vocabulary": {}}
        self._store.close()
        self._store = None
        return summary

    def abort(self) -> None:
        """Close the connection WITHOUT a `runs` row (`M4`/`M5`).

        A no-op if `open()` never ran (or itself failed before `BeliefStore`
        was constructed) — `self._store` is `None` in both cases. Otherwise
        just `self._store.close()`: no `write_run` call, so this store carries
        no evidence that a run touching it ever finished — the same "no clean
        close, no summary" signal `close()`'s own docstring already relies on,
        now also reachable from a path that never gets there. Any transaction
        `write_window` had open is already resolved (committed or rolled back)
        by the time `abort()` can run, because `BeliefStore.write_window` never
        returns — successfully OR by raising — with one still open.
        """
        if self._store is not None:
            self._store.close()
            self._store = None
