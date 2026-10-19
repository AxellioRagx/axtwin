"""Window-sink contract + registry (INCUBATING).

A sink adapts a closed window's artifacts — and the assertions extracted from
it — to ONE output destination. Sinks are thin and **streaming**: they write on
window close and retain **no window**, so the cost of a 72-window run does not
grow with the number of windows. Feature *design* lives upstream in the
windowing layer (brief §5.4); a sink only decides how those artifacts land.

Three artifacts consume a closed window today — the belief store (`StoreSink`),
the evidence log (`EvidenceSink`), and the feature archive (`features/jsonl.py`
`JsonlSink`) — and until `WORK_ORDER_egress_sink_unification.md` only the last
one lived behind a contract; `cmd_ingest` hardcoded the other two. The contract
is not a *feature* concept — it is "something that consumes closed windows" —
so it lives in this neutral package rather than in `features/`, which `store`
may not import without inverting CONVENTIONS §3's layering (`store` is imported
by writers, not the reverse). Moving it here, instead of leaving it in
`features/` and letting `store` reach upward into it, or duplicating a second
registry inside `store/`, was the explicit choice in that work order's
"layering crux" section — this codebase has one registry pattern, not two,
matching `ingest/base.py`.

**A sink MAY hold run-level state — and if it does, the bound must be stated and
enforced.** Some artifacts are properties of the whole archive rather than of one
window; the categorical index map (`features/vocabulary.py`) is the first, and it
necessarily spans every window. "Holds nothing across windows" was the older,
simpler claim and it is no longer true, so it is replaced rather than quietly
narrowed. The replacement is not weaker only if the bound is real:

  * state the bound where the state is declared, in the units that can actually
    blow it up (distinct **keys**, not rows or windows);
  * enforce it in code, at insert time — a cap applied at close bounds the output
    and not the memory, which is `LES-010`'s whole point;
  * disclose every truncation in the artifact. A bounded map that reports itself
    as complete is a worse artifact than an unbounded one.

`JsonlSink`'s run-level state is one `VocabularyObserver`, bounded at
`2 * vocabulary.MAX_KEYS` keys per declared categorical field. `StoreSink` and
`EvidenceSink` hold no equivalent state — a DuckDB connection and a file handle,
both bounded independently of this contract.

**Assertions are computed once, upstream of every sink** (D2). `write_window`
takes `(win, assertions)`: the driver calls `learners.extract_assertions(win)`
exactly once per window and hands the identical list to every open sink, so the
per-window cost does not multiply by sink count, and no sink needs to import
`learners` itself to get them — this package imports only `windowing` outputs.
A sink with no use for assertions (the feature archive, today — see the "do not
add `assertions.jsonl`" boundary in the work order) still accepts the
parameter: the alternative, sinks with two different `write_window` arities,
would couple every sink to whether its neighbor happens to want assertions.

**Sink-specific options are not part of this contract** (D4). The categorical
vocabulary pin is the first one: it changes what exactly one sink emits and
nothing else, so it is declared as an EXTRA parameter on that sink's OWN `open`
override (see `features/jsonl.py`'s `vocabulary` keyword) rather than threaded
through `WindowSink.open` for every sink. A caller passes such an option only
to a sink whose `accepts` declares it (see `WindowSink.accepts`, and the CLI
driver's `_open_sinks`, `M3`) — never by inspecting the sink's `open` signature,
which stays silent about a decorator missing `functools.wraps` or a renamed
parameter. A sink that does not declare an option never sees it and never has
to accept — and silently ignore — a parameter that means nothing to it; an
option that reaches NO open sink at all is the caller's own error, not a silent
no-op (`M2` — see the CLI's own consumption check).

**A failure while opening, writing, or closing releases every sink that is
still holding something open** (`M4`/`M5`, `WindowSink.abort`). Fan-out means N
destinations are being written as if they were one operation, so a failure
partway through must not leave the ones that succeeded so far dangling (an
open DuckDB connection, an open file handle) OR stamped with a completion
artifact for a run that, as a whole, did not finish. `abort()` is the release
half of the lifecycle `close()`'s completion half already had.

Deliberately the same shape as the ingest-plugin registry (`ingest/base.py`) —
one registry pattern in this codebase, not two.

Import direction (CONVENTIONS §3): this file imports only `windowing` outputs.
Sibling files in this package (`store_sink.py`, `evidence_sink.py`) additionally
import `aximprint.store` — the sanctioned "writer imports store" direction, not
its reverse. Nothing outside `features`, this package itself, and the CLI
imports `sinks`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from aximprint.windowing.windows import WindowResult

_REGISTRY: dict[str, type["WindowSink"]] = {}


def register(cls: type["WindowSink"]) -> type["WindowSink"]:
    if not cls.name:
        raise ValueError("sink must set a name")
    if cls.name in _REGISTRY:
        raise ValueError(f"duplicate sink name: {cls.name}")  # fail loud
    _REGISTRY[cls.name] = cls
    return cls


def get_sink(name: str) -> "WindowSink":
    try:
        return _REGISTRY[name]()
    except KeyError:
        # `S10`: a sink registers on IMPORT of the package that DEFINES it —
        # `store`/`evidence` live here and register on `import aximprint.sinks`,
        # but `jsonl` is declared in `aximprint.features` (see this package's
        # own docstring) and registers only once that package is imported
        # somewhere in the process. A caller who only ever did
        # `import aximprint.sinks` and asked for "jsonl" would see a name
        # missing from `available` with no hint that it EXISTS, just
        # elsewhere — this names the cause, not only the absence.
        raise KeyError(
            f"no sink '{name}'; available: {sorted(_REGISTRY)} — a sink registers "
            "only when the package that DEFINES it is imported (e.g. 'jsonl' lives "
            "in aximprint.features, not in aximprint.sinks: importing aximprint.sinks "
            "alone registers only 'store'/'evidence'), so a name missing here may mean "
            "its defining package was never imported rather than that the sink does "
            "not exist"
        ) from None


class WindowSink(ABC):
    """Contract: open a destination, take closed windows (+ their assertions), close and summarize."""

    name: str = ""

    #: Sink-specific `open()` keyword parameter names this sink declares it
    #: wants (D4 — the vocabulary pin is the first). The driver
    #: (`cli._open_sinks`) routes `open_kwargs` by this DECLARATION, never by
    #: inspecting `open`'s signature (`M3`): reflection fails silently when a
    #: decorator on `open` lacks `functools.wraps`, or when a parameter is
    #: renamed — `inspect.signature` would just report the wrong thing, with
    #: nothing to raise. A sink that does not list an option here never
    #: receives it, so it never has to accept-and-ignore one meant for a
    #: sibling sink. If a name IS routed here but the sink's own `open()` does
    #: not actually accept it, that mismatch surfaces as a plain `TypeError` at
    #: the call site — the loud backstop this declaration narrows the odds of
    #: needing, but does not replace.
    accepts: frozenset[str] = frozenset()

    @abstractmethod
    def open(self, destination: str, run_meta: dict) -> None:
        """Prepare `destination` for writing.

        `run_meta` describes the run (source, plugin, window params) for the
        provenance stamp a sink is expected to persist — §10's first
        reconciliation invariant is "the same aximprint build on both sides",
        and a sink with no run stamp cannot satisfy it.

        This is the WHOLE signature every sink must accept — see the module
        docstring on why a sink-specific option (the vocabulary pin, `Q6`) is
        an extra parameter on ONE sink's own override rather than a parameter
        here that every sink would have to accept and ignore.
        """

    @abstractmethod
    def write_window(self, win: WindowResult, assertions: list[dict]) -> None:
        """Persist one closed window's artifacts. MUST NOT retain OR MUTATE them.

        `assertions` is `learners.extract_assertions(win)`, computed exactly
        once per window by the driver and handed to every open sink unchanged
        (see the module docstring, D2) — never recomputed here. It is the SAME
        list object for every sink opened this run (`S8`): a sink that mutated
        it — in place, or one of its element dicts — would corrupt what every
        sink opened AFTER it in the fan-out order sees, a bug that would depend
        on sink order and appear only when two mutating sinks (or `--sink` flag
        order) happened to combine.
        """

    def abort(self) -> None:
        """Release whatever this sink holds open, WITHOUT the `close()` ceremony.

        Default no-op — correct for a sink with nothing open yet (never
        `open()`-ed, or a fresh instance whose own `open()` failed before
        allocating anything). A sink that DOES hold a resource across calls
        (`StoreSink`'s DuckDB connection, `JsonlSink`/`EvidenceSink`'s file
        handles) overrides this to close it.

        Called instead of `close()` on every one of the fan-out's three
        failure surfaces (`M4`/`M5`): another sink's `open()` raising (this
        sink release what it already opened), a `write_window` raising
        (every opened sink releases, mid-run), and a sibling sink's `close()`
        raising (every sink that has not been closed yet releases rather than
        leaking). **Do not close; do release** — `close()`'s completion
        artifact (`manifest.json`, a `runs` row) means "this run finished", and
        writing it here would fabricate that claim for a run that did not.
        MUST NOT raise on a sink that was never opened, and MUST NOT perform
        any part of the completion ceremony `close()` performs.
        """
        return None

    def note_unparsed_log_types(self, counts: dict[str, int] | None) -> None:
        """Record the run's per-stem "present but not parsed" counts (`docs/
        WORK_ORDER_zeek_stem_coverage.md` D5) — the counts half of a fact whose
        NAMES half already reaches every window's own health record via
        `WindowResult.health["unparsed_log_types"]` (`windowing.windows.WindowManager`).

        Called AT MOST ONCE per run, by the driver (`cli._pipeline`), strictly AFTER
        the event stream has been fully consumed and BEFORE `close()` — never on a
        run that raises first (code review `S7`: "exactly once" overclaimed this; a
        run that fails mid-way calls `abort()` on every sink instead, zero times,
        the same "no clean close, no completion artifact" discipline `close()`
        itself already follows). The AFTER-the-stream timing is load-bearing, not
        incidental: `run_meta` (`open()`'s own parameter) is frozen the instant
        `open()` runs, before a single event has been read, and an ingest plugin's
        own inventory of what it will not parse is discovered LAZILY — partway
        through the run, not before it (`ingest/zeek/plugin.py::events`'s own
        sequencing comment) — so `run_meta` structurally cannot carry this fact, and
        this method is the channel a fact discovered that late needs.

        Default is a no-op. Most sinks decline it, but not for the same reason:
        `EvidenceSink` genuinely has no run-scoped artifact this belongs in (its own
        docstring: "no `runs`-table equivalent to persist into"); `JsonlSink` DOES
        have one (`manifest.json`) and declines it explicitly, by name, for a
        different reason of its own (code review `S3`: an earlier version of this
        docstring said "most sinks have no run-scoped table", which was true of
        `EvidenceSink` but not of `JsonlSink` — see that sink's own decline for why
        having a run-scoped artifact does not automatically mean using this one).
        `StoreSink` (`runs`) is the one sink that DOES use it today. Deliberately
        NOT the `accepts`/`open_kwargs` mechanism (D4): that one exists for
        OPERATOR-REQUESTED options, where reaching no open sink is the caller's
        mistake (`M2`, `cli._open_sinks`) — this is offered opportunistically to
        every opened sink on every run, and declining it is the ordinary case for
        most of them, not an error to reject the run over.
        """
        return None

    def note_unrecognized_log_types(self, counts: dict[str, int] | None) -> None:
        """The SUBSET of `note_unparsed_log_types`'s own counts with no `_SKIP`
        entry (`ingest/zeek/plugin.py`) — code review `S4`, additive to D5, no
        reversal of it. Identical contract in every other respect: same timing (at
        most once, after the event stream, before `close()`), same default no-op,
        same reasons each sink does or does not override it — see that method's
        own docstring rather than repeating it here.
        """
        return None

    def note_entity_roster(self, roster: dict | None) -> None:
        """Record the run's ENTITY NAMESPACE — every path that can mint an entity
        id, the bound entities enumerated, and the resolver's own bounds
        (`windowing.windows.WindowManager.entity_roster`, `Q1` build item 4).

        Same timing and same discipline as the two `note_*_log_types` methods
        above — at most once per run, after the event stream, before `close()`,
        never on a run that raises first — and for the same structural reason: the
        namespace is discovered by RESOLVING, so it is not known at `open()` when
        `run_meta` is frozen. Read those docstrings rather than expecting this one
        to restate them.

        Why every sink is offered it rather than only the one that wanted it:
        `node_features.entity`, `assertions.subject` and `edge_features.src`/`dst`
        all carry values from this namespace, and it has FOUR mint paths of which
        only one looks like an entity id — a bound `ent-NNNN`, a bare address (D4),
        the `"?"` empty-address sentinel, and `__longtail__`. A consumer holding
        only the rows cannot tell them apart, which is precisely `LES-017`'s
        failure. `JsonlSink` (`manifest.json`) and `StoreSink` (`entity_roster`)
        both override it; `EvidenceSink` declines, having no run-scoped artifact at
        all.
        """
        return None

    @abstractmethod
    def close(self) -> dict:
        """Finish the run and return a summary (what was written, and where).

        The summary carries **exactly** these keys, always present:

          destination  str        where the archive landed
          windows      int        windows written
          rows         dict       artifact/table name -> rows written
          vocabulary   dict       per categorical field: vocabulary kind, key
                                  count, oov rows, coverage, truncated keys —
                                  `{}` for a sink with no categorical fields,
                                  never absent

        Stated because a caller was reading one of them with a silent `.get`
        default two lines after subscripting another directly: a sink that
        stopped reporting coverage would have printed nothing and exited 0.
        A required key is subscripted; if it is missing, that is the sink's bug
        and it should surface as one (CONVENTIONS §3, fail loud).

        `rows` does not mean quite the same thing for every sink, and renaming
        it out from under every existing consumer is not this change's call to
        make (tracked as a finding, not resolved here). A JSONL sink opens a
        fresh archive every run, so "rows written" and "rows in the file"
        coincide; a DuckDB-backed sink accumulates across many runs, so `rows`
        there counts what THIS run inserted, never the table's running total.
        """
