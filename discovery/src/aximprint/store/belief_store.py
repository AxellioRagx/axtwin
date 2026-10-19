"""Belief store: DuckDB projection of windowed features + assertions (v0).

Tables (INCUBATING, unversioned): node_features, edge_features, assertions,
identity_bindings, window_health, profile_facts, entity_roster, runs. `snapshot(as_of)` gives the
synthesizer a consistent as-of-T read. Decay/forgetting lands later (Q5).

`entity_roster` and the reshaped `identity_bindings` are new as of `docs/WORK_ORDER_entity_
resolution.md` (closes `Q1`). Two things worth knowing before reading either. **Entity ids are
RUN-scoped** (D3) — `entity_roster.run_started_at` joins back to `runs.started_at` and is the only
attribution this schema has for them; a store accumulating several runs holds several unrelated
`ent-0001`s. And `identity_bindings` is now a CHANGE log, not a state table: one row per
open/close/final transition (`Q9`), so the current state of a binding is its latest row by
`window_start`, not its only row.

`runs` is new as of `WORK_ORDER_egress_sink_unification.md` build step 6: the
store accumulates across many `ingest` invocations (no forgetting yet), so a
single overwritten "last run" row would lose which of several builds produced
which window range — one row is appended per run instead, only on a clean
close (`StoreSink.close`). A run that raises mid-way leaves no row for its
window range, the same "no clean close, no summary artifact" signal
`features/jsonl.py`'s manifest already relies on. It lives in its own table so
adding it cannot perturb the five tables everything else in this codebase
already reads: `node_features`, `edge_features`, `assertions`,
`identity_bindings`, `window_health`.

`profile_facts` is new as of `docs/WORK_ORDER_subject_less_facts.md` (closes `Q29`): the one
table with **no subject column at all**, for a fact true of the capture rather than of any host
in it (`ocsp`'s cache-lifetime cadence is the first occupant, carried on
`WindowResult.capture_profile` — named that, not `profile`, so it cannot be misread as a
per-entity channel the way a bare `profile` sitting beside `nodes` could, code review). Per-window
like `window_health`, not per-run like `runs` — DESIGN_BRIEF §10's overfitting guard ("learn on
one window, validate drift on a held-out window") needs the finer resolution, and a per-run row
could not decompose back down to it. Deliberately **not** in `NONDETERMINISTIC_TABLES`: a
window's profile facts are as pure a function of (events, resolver state, code build) as any
other window-scoped table — the *lack* of a subject does not make the write path stateful or
wall-clock-dependent. Carries a `unit` column (`seconds`, for the one parameter declared so far):
a `604800.0` crossing an artifact boundary with no unit would force a `tfc` reader to hardcode
what `PROFILE_PARAMETERS` already declares (`LES-015`, code review `M3`).
"""
from __future__ import annotations

import functools
import json
from typing import Optional

import duckdb

from aximprint.windowing.windows import PROFILE_PARAMETERS, WindowResult


def profile_facts_rows(win: WindowResult) -> list[tuple[float, str, str, float, str]]:
    """Flatten `win.capture_profile` into `profile_facts` rows: `(window_start, parameter,
    stat, value, unit)`.

    The ONE place this shape is computed. `write_window`'s own guard-and-insert and
    `sinks.store_sink.StoreSink`'s row-count summary both call this rather than each
    independently re-deriving "how many rows does this window contribute" — before this, the
    insert's guard read `win.capture_profile` (the two-level dict) while the sink's count read
    `sum(len(stats) for stats in ...)`, two separately hand-written statements of the identical
    rule with nothing checking they agreed (code review `S5`, the `LES-026` shape one level up:
    the count and the guard it exists to feed must describe exactly the same rows).

    `unit` is looked up from `PROFILE_PARAMETERS`, never carried on `win.capture_profile` itself
    — mixing a string unit into the same per-stat dict as the float quantile values would let a
    `stat="unit"` row corrupt `profile_facts.value` (a `DOUBLE` column) the moment anything
    iterated the dict generically. The `stat="n"` row (sample count, code review `S1`) is the one
    exception to "this row's unit is the parameter's own": a count is dimensionless, so it is
    always `"count"`, never whatever unit the quantiles themselves are in — `("ocsp_cache_lifetime",
    "n", 411.0, "seconds")` would misread as "411 seconds" rather than "411 observations".
    """
    ws = win.window_start
    return [(ws, param, stat, value, "count" if stat == "n" else PROFILE_PARAMETERS[param][1])
            for param, stats in win.capture_profile.items()
            for stat, value in stats.items()]


def _resolve_chain(start: str, direct: dict[str, str]) -> str:
    """Follow `start` through `direct` to the id nothing further absorbs.

    Guarded against a cycle rather than trusting the producer not to make one: a store
    accumulates rows across builds (`Q5` — no forgetting), so "the current resolver cannot emit a
    cycle" is a property of one build, not of the data this function will read. On a cycle it
    stops and returns the smallest id in it, which is deterministic and wrong in a bounded way,
    instead of looping forever.
    """
    seen = {start}
    node = start
    while True:
        nxt = direct.get(node)
        if nxt is None:
            return node
        if nxt in seen:
            return min(seen)
        seen.add(nxt)
        node = nxt


#: Tables whose CONTENTS are expected to differ between two otherwise-equivalent
#: RUNS OF THE SAME TRANSPORT — today just `runs` itself (wall-clock + host build
#: info in every row, `write_run`). A comparison across two runs, or across two
#: sinks writing "the same" windows into separate stores, should hold every OTHER
#: table to byte/row equality and treat this one as exempt — imported rather than
#: hand-excluded by omission from a literal table list, the same discipline
#: `features/jsonl.py`'s `WALLCLOCK_FIELD` applies to the one manifest key that
#: may legitimately differ (`S3`, `LES-009` #3).
#:
#: `window_health` is a DIFFERENT axis, not a member of this tuple (code review
#: `S5`): two runs of the SAME transport over the same source produce
#: byte-identical `window_health.health` — the axis this tuple is about — but two
#: DIFFERENT transports over the identical logical capture do not, on exactly the
#: two keys `windowing.windows.TRANSPORT_DEPENDENT_HEALTH_FIELDS` declares
#: (`unparsed_log_types`/`unrecognized_log_types`, `docs/WORK_ORDER_zeek_stem_
#: coverage.md` D5 + `S4`): `ParquetArchiveSource` cannot audit what its own
#: converter never wrote into a manifest (`ingest/zeek/sources.py::ZeekSource
#: .inventory_is_complete`), so those two keys read `None` there and a real list
#: on a filesystem-walking transport, for the identical underlying source. That
#: is TRANSPORT-dependence, checked at tier 2 (`scripts/verify_pipeline_gates.sh`
#: imports `TRANSPORT_DEPENDENT_HEALTH_FIELDS` for exactly this comparison), not
#: NON-determinism in this table's sense, and `window_health` belongs in neither
#: this tuple nor an equivalent one for that axis.
#:
#: One more thing worth saying here rather than leaving a reader to assume it:
#: `runs.unparsed_log_types`/`.unrecognized_log_types` (per-RUN counts) and
#: `window_health.health`'s own same-named keys (per-WINDOW names) CANNOT be
#: joined against each other — this schema carries no run -> window attribution
#: at all (`BeliefStore.status`'s own docstring: "which run wrote which
#: `window_health` row... is not answerable from this schema today"). Two
#: same-named fields living in different tables is not evidence they are
#: cross-referenceable; nobody should go looking for a join key that is not here.
NONDETERMINISTIC_TABLES = ("runs",)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_features (
  window_start DOUBLE, entity VARCHAR, features JSON);
CREATE TABLE IF NOT EXISTS edge_features (
  window_start DOUBLE, src VARCHAR, dst VARCHAR, proto VARCHAR,
  flows BIGINT, bytes BIGINT, first_ts DOUBLE, last_ts DOUBLE);
CREATE TABLE IF NOT EXISTS assertions (
  window_start DOUBLE, subject VARCHAR, predicate VARCHAR, object VARCHAR,
  evidence_class VARCHAR, confidence DOUBLE, source VARCHAR);
CREATE TABLE IF NOT EXISTS identity_bindings (
  window_start DOUBLE, change VARCHAR, kind VARCHAR, ip VARCHAR, bound_to VARCHAR,
  namespace VARCHAR, identifier VARCHAR, scope VARCHAR, entity VARCHAR,
  from_ts DOUBLE, to_ts DOUBLE, first_seen DOUBLE, last_seen DOUBLE,
  lease_seconds DOUBLE, record_ttl DOUBLE, count BIGINT);
CREATE TABLE IF NOT EXISTS entity_roster (
  source VARCHAR, entity VARCHAR, mint_path VARCHAR, scope VARCHAR,
  identifiers JSON, addresses JSON, evidence JSON, merged_from JSON,
  generations INTEGER, first_seen DOUBLE, last_seen DOUBLE);
CREATE TABLE IF NOT EXISTS window_health (
  window_start DOUBLE, window_seconds INTEGER, health JSON);
CREATE TABLE IF NOT EXISTS profile_facts (
  window_start DOUBLE, parameter VARCHAR, stat VARCHAR, value DOUBLE, unit VARCHAR);
CREATE TABLE IF NOT EXISTS runs (
  started_at DOUBLE, finished_at DOUBLE, windows INTEGER,
  source VARCHAR, plugin VARCHAR, window_seconds INTEGER, subbuckets INTEGER,
  ts_from DOUBLE, ts_to DOUBLE,
  aximprint_version VARCHAR, code_fingerprint VARCHAR, git_commit VARCHAR,
  unparsed_log_types JSON, unrecognized_log_types JSON,
  vantage VARCHAR, network VARCHAR);
"""


@functools.lru_cache(maxsize=1)
def _canonical_columns() -> dict[str, frozenset[str]]:
    """table name -> the column names `_SCHEMA` (above) currently declares for it.

    Derived by actually RUNNING `_SCHEMA` against a throwaway in-memory connection,
    rather than re-parsing the DDL text or hand-maintaining a second list that could
    itself drift from `_SCHEMA` — the identical "two things duplicating one fact,
    nothing checking they agree" hazard `docs/WORK_ORDER_zeek_stem_coverage.md` D1
    named one layer over (`KNOWN_PARSED_STEMS`/`_PARSERS`). Cached (`LES-024`'s own
    pattern for `provenance.build_stamp()`): `_SCHEMA` is a module constant, so this
    answer cannot change within one process, and every `BeliefStore.__init__` call
    would otherwise pay for a throwaway connection + a schema replay it does not
    need to repeat.
    """
    con = duckdb.connect(":memory:")
    try:
        con.execute(_SCHEMA)
        return {t: _live_columns(con, t) for t in _live_tables(con)}
    finally:
        con.close()


def _live_tables(con: "duckdb.DuckDBPyConnection") -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'").fetchall()]


def _live_columns(con: "duckdb.DuckDBPyConnection", table: str) -> frozenset[str]:
    return frozenset(r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ?", [table]).fetchall())


class BeliefStore:
    def __init__(self, path: str):
        self.con = duckdb.connect(path)
        self.con.execute(_SCHEMA)
        self._check_schema_matches_this_build(path)

    def _check_schema_matches_this_build(self, path: str) -> None:
        """Fail loud, before a single row is written, if `path` was created by a
        DIFFERENT build's schema (`M2`, code review).

        `CREATE TABLE IF NOT EXISTS` (in `_SCHEMA`, executed just above) handles a
        new TABLE and does nothing at all for a new COLUMN on an existing one —
        `runs` gaining `unparsed_log_types` (`docs/WORK_ORDER_zeek_stem_coverage.md`
        D5) is the first column ever added to an existing table here, and it
        reproduces exactly: every window in a run commits successfully, and only
        the LAST statement — `write_run`'s own `INSERT`, at `close()` — raises
        `BinderException('table runs has 12 columns but 13 values were supplied')`,
        throwing away an hours-long run's own completion with the store left
        holding every window it already wrote but no `runs` row for them.

        This is a GUARD, not a migration and not a version check (`LES-001` still
        forbids version fields, bump ceremonies and compatibility shims on an
        incubating schema): it does not attempt to reconcile the two shapes, only
        to refuse touching a mismatched one before any write happens, with a
        message that names the mismatch instead of a raw `BinderException` three
        statements into a write. Compares LIVE columns (this connection's own
        `information_schema.columns`) against what `_SCHEMA` currently declares
        (`_canonical_columns`), for every table `_SCHEMA` names — generic over
        all seven, so a future column addition to ANY of them is covered by
        construction, not by remembering to extend this method too.
        """
        live = {t: _live_columns(self.con, t) for t in _live_tables(self.con)}
        for table, expected in _canonical_columns().items():
            got = live.get(table, frozenset())
            if got != expected:
                self.con.close()
                raise RuntimeError(
                    f"{path}: table {table!r} has columns {sorted(got)}, this "
                    f"build expects {sorted(expected)} — this store predates "
                    "this build's schema (INCUBATING, unversioned: LES-001 "
                    "forbids a migration or a compatibility shim here); "
                    "recreate the store")

    def write_window(self, win: WindowResult, assertions: list[dict]) -> None:
        """Persist one closed window's six statements as ONE transaction (`T1`/`S4`).

        Two things were wrong before this fix, and they compound. (1)
        `executemany` raises `InvalidInputException` on an empty parameter-set
        list, and only the `assertions` insert was guarded with `if assertions:`
        — a window with no entities at all (a network-quiet hour, or one that
        saw only a `SensorHealthEvent`) crashed outright, and a window with
        nodes but no edges (an `AlertEvent`-tainted host with no flow this
        window — `windows.py`'s `_node()` is reachable independent of
        `_add_flow`) got a `node_features` row inserted and THEN crashed on the
        next statement, leaving a stray row with no matching `window_health`
        row: a window `snapshot()` can see partially but `status()` cannot see
        at all. (2) with no transaction, that partial write was durable —
        `write_window` never rolled anything back on its own failure, so the
        five statements were never actually one unit despite `Q5`/the module
        docstring treating "a window" as the store's atomic fact. `begin()` /
        `commit()` / `rollback()` make that true: every statement below lands or
        none does, and a caller (`StoreSink.write_window`) that re-raises after
        this returns sees a store that still agrees with `status()`.

        `profile_facts` (`Q29`) joined as a sixth, same guard as the first three: most windows
        carry no profile evidence at all (`win.capture_profile == {}`, e.g. any capture with no
        `ocsp` traffic this window), and an unguarded `executemany` would raise on every one of
        them. The rows are built once, by `profile_facts_rows` (module-level, code review `S5`),
        not re-derived here — the guard below and that function's own output must describe
        identical rows, which sharing one call site makes structural rather than assumed.
        """
        ws = win.window_start
        self.con.begin()
        try:
            if win.nodes:
                self.con.executemany(
                    "INSERT INTO node_features VALUES (?, ?, ?)",
                    [(ws, ent, json.dumps(f)) for ent, f in win.nodes.items()])
            if win.edges:
                self.con.executemany(
                    "INSERT INTO edge_features VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ws, e["src"], e["dst"], e["proto"], e["flows"], e["bytes"],
                      e["first_ts"], e["last_ts"]) for e in win.edges])
            if assertions:
                self.con.executemany(
                    "INSERT INTO assertions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(a["window_start"], a["subject"], a["predicate"], str(a["object"]),
                      a["evidence_class"], a["confidence"], a["source"]) for a in assertions])
            profile_rows = profile_facts_rows(win)
            if profile_rows:
                self.con.executemany(
                    "INSERT INTO profile_facts VALUES (?, ?, ?, ?, ?)", profile_rows)
            if win.identity_delta:
                self.con.executemany(
                    "INSERT INTO identity_bindings VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ws, b["change"], b["kind"], b["ip"], b["bound_to"], b["namespace"],
                      b["identifier"], b["scope"], b["entity"], b["from_ts"], b["to_ts"],
                      b["first_seen"], b["last_seen"], b["lease_seconds"], b["record_ttl"],
                      b["count"]) for b in win.identity_delta])
            self.con.execute("INSERT INTO window_health VALUES (?, ?, ?)",
                             (ws, win.window_seconds, json.dumps(win.health)))
        except BaseException:
            self.con.rollback()
            raise
        else:
            self.con.commit()

    def write_run(self, run_meta: dict, build: dict, windows: int,
                  started_at: float, finished_at: float,
                  unparsed_log_types: Optional[dict[str, int]] = None,
                  unrecognized_log_types: Optional[dict[str, int]] = None) -> None:
        """One `runs` row: what this run was, what build produced it (§10).

        Called AT MOST once, at a clean close (`StoreSink.close`) — never per
        window, and never at all on a run that raises first (code review `S7`:
        "called once" overclaimed this) — so an interrupted run leaves no row
        rather than a half-filled one. Reads the specific keys it needs with
        `.get`, not a raw unpack: unlike `write_window`'s per-row dicts (which
        come from this codebase's own windowing/learners layers and are trusted
        to carry every key), `run_meta` here is CLI-assembled and may
        legitimately omit a field a given verb has no value for (`ingest` has
        no `--vocabulary`, but nothing here needs it).

        `unparsed_log_types` (`docs/WORK_ORDER_zeek_stem_coverage.md` D5): stem ->
        count, for stems this run's source reported but this build did not parse.
        `unrecognized_log_types` (code review `S4`, additive, no reversal of D5):
        the SUBSET of `unparsed_log_types` with no `_SKIP` entry (`ingest/zeek/
        plugin.py`) — a store consumer may not import `ingest` (CONVENTIONS §3),
        so this is the one place that split can still be read back once `_SKIP`
        itself has moved on to a later build. Both are separate PARAMETERS rather
        than `run_meta` keys, on purpose — unlike every other value here, neither
        is known at `open()` time (`run_meta` is frozen then; both are discovered
        partway through the run, see `StoreSink.note_unparsed_log_types`/
        `.note_unrecognized_log_types`) — passed explicitly by the one caller
        that has them, the same way `started_at`/`finished_at` already are.
        Source-level, so they belong HERE, once per run, rather than mirrored
        onto every `window_health` row — that would manufacture the exact
        double-counting the window health record's own NAMES-only lists
        (`windowing.windows.WindowManager`'s two providers) are built to avoid.
        `None` means unaudited (no plugin reported an inventory this run) — `{}`
        is the honest spelling of "audited, and nothing left over in this
        channel" — never coerced to one another (`LES-006`). JSON-encoded like
        every other dict-shaped column in this store (`node_features.features`,
        `window_health.health`), but with `sort_keys=True`, unlike either of
        those two: their own key order comes from a fixed dict LITERAL (or an
        already-`sorted()` spread) written once in `windows.py`, never from
        runtime iteration, so it is stable by construction. These two columns'
        key order instead comes from `ZeekSource.inventory`, which is populated
        differently per TRANSPORT — `ParquetArchiveSource` and `LogDirSource`'s
        window-tree mode both insert in `sorted(stem)` order, but flat-directory
        mode inserts in sorted-PATH order, which only coincides with
        sorted-STEM order for the common one-file-per-stem case. Relying on that
        coincidence would make these columns' bytes depend on which transport
        wrote them for the identical underlying set of stems — sorting
        explicitly here, at the persistence boundary, is what `LES-009`'s "sort
        rows by key at the persistence boundary, so a file's bytes depend on row
        content rather than an upstream dict's insertion order" means applied
        to a JSON column instead of a row order.
        """
        self.con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, windows,
             run_meta.get("source"), run_meta.get("plugin"),
             run_meta.get("window_seconds"), run_meta.get("subbuckets"),
             run_meta.get("ts_from"), run_meta.get("ts_to"),
             build.get("aximprint_version"), build.get("code_fingerprint"),
             build.get("git_commit"),
             json.dumps(unparsed_log_types, sort_keys=True)
             if unparsed_log_types is not None else None,
             json.dumps(unrecognized_log_types, sort_keys=True)
             if unrecognized_log_types is not None else None,
             # `vantage`/`network` (2026-08-17, `docs/WORK_ORDER_visibility_and_vantage.md`
             # D1-D3). Ordinary `run_meta` keys, unlike the two JSON columns above, because
             # both ARE known at `open()` time — the vantage is read from the capture's own
             # harvest manifest and the network is declared on the command line, so neither
             # needs a `note_*` channel. `None` means unknown throughout: a store whose runs
             # declare no network can still be materialized, it just cannot claim that two
             # captures describe one network (`ir/materialize._run_context`).
             run_meta.get("vantage"), run_meta.get("network")))

    def write_reference_snapshot(self, snapshot) -> dict:
        """Fold one reference document's facts into `assertions` (`docs/WORK_ORDER_reference_
        ingest.md` D14). Takes an `ingest.reference.base.ReferenceSnapshot`.

        **No column change**: the `assertions` table's `(window_start, subject, predicate, object,
        evidence_class, confidence, source)` shape already carries everything a reference fact
        needs, with the document's own `as_of` as `window_start` — which is the whole reason D14
        could rule "no belief-store column changes" rather than proposing a table.

        **REPLACE-BY-SOURCE, not append**, and that is a deliberate departure from how every
        other writer here behaves. A capture is an event stream and the store accumulates it
        (`Q5`, no forgetting): re-ingesting the same window twice is a duplicate, not a
        correction. A reference document is a SNAPSHOT — the whole point of the plugin kind — so
        its second reading supersedes its first, and appending would resurrect a record the
        operator deleted from the file. That is a correctness bug rather than untidiness: the
        deleted assertion would keep winning precedence forever with nothing in any artifact to
        say where it came from.

        The delete is scoped to `(evidence_class, source)`, so one operator's assertions file is
        one snapshot and re-ingesting it replaces that operator's prior snapshot wholesale. Two
        different files from ONE operator therefore clobber each other, which is honest rather
        than accidental: `source` is the only identity a reference fact carries in this schema,
        and two documents making different claims about one imprint under one name are already
        ambiguous before they reach a store. Named here so it is a known bound rather than a
        surprise (an implementation call, `docs/CHANGELOG.md` 2026-09-05).

        One transaction, like `write_window`: the delete and the insert land together or neither
        does, or a failed ingest would leave the store holding neither the old snapshot nor the
        new one.
        """
        removed = self.con.execute(
            "SELECT count(*) FROM assertions WHERE evidence_class = ? AND source = ?",
            (snapshot.evidence_class, snapshot.source)).fetchone()[0]
        self.con.begin()
        try:
            self.con.execute(
                "DELETE FROM assertions WHERE evidence_class = ? AND source = ?",
                (snapshot.evidence_class, snapshot.source))
            if snapshot.facts:
                self.con.executemany(
                    "INSERT INTO assertions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(f["window_start"], f["subject"], f["predicate"], str(f["object"]),
                      f["evidence_class"], f["confidence"], f["source"])
                     for f in snapshot.facts])
        except BaseException:
            self.con.rollback()
            raise
        else:
            self.con.commit()
        return {"kind": snapshot.kind, "schema": snapshot.schema, "source": snapshot.source,
                "evidence_class": snapshot.evidence_class,
                "as_of": snapshot.as_of, "rows_written": len(snapshot.facts),
                "rows_replaced": removed, "uncarried": dict(snapshot.uncarried)}

    def write_entity_roster(self, source: str, roster: dict) -> None:
        """One row per BOUND entity this run resolved (`Q1` build item 4, `LES-017`).

        Only the `bound` list lands here. The roster's other three mint paths — D4's bare-address
        fallback, the `"?"` sentinel and `__longtail__` — are counts and declarations, not
        entities with evidence behind them, and a table with one row per bare address would be
        the unbounded, data-keyed map the cap exists to prevent (`LES-014`). Those counts stay in
        the feature archive's `manifest.json`, which is a document rather than a table.

        `source` is the attribution key, and it is the SOURCE rather than the run because
        **entity ids are RUN-scoped** (D3) while this store accumulates across many runs with no
        forgetting (`Q5`) — so `ent-0007` written by two runs over two different captures is two
        different hosts, and something has to say which. A wall-clock run stamp (the obvious
        choice, and what `runs` itself uses) was rejected for two reasons: it would make this
        table non-reproducible, forcing it into `NONDETERMINISTIC_TABLES` and out of every
        cross-run comparison that currently checks it; and it answers a WEAKER question. Two runs
        over one source mint the same ids for the same hosts, so their rows are a duplicate
        rather than a conflict — exactly as `node_features` already duplicates on a re-run — while
        two runs over DIFFERENT sources are the case that actually needs telling apart, and
        `source` tells them apart where a timestamp only tells them apart by accident.

        What this does NOT fix: the tables that actually CARRY entity ids — `node_features`,
        `edge_features`, `assertions` — still have no run or source attribution of their own, so
        a store fed two captures still cannot say which run's `ent-0007` a given node row belongs
        to. That gap is pre-existing and store-wide (`status()`'s own docstring), not introduced
        here; recorded as a finding rather than half-solved for one table.
        """
        rows = [(source, e["entity"], e["mint_path"], e["scope"],
                 json.dumps(e["identifiers"]), json.dumps(e["addresses"]),
                 json.dumps(e["evidence"]), json.dumps(e["merged_from"]),
                 e["generations"], e["first_seen"], e["last_seen"])
                for e in roster.get("bound", [])]
        if rows:
            self.con.executemany(
                "INSERT INTO entity_roster VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def entity_aliases(self, as_of: Optional[float] = None) -> dict[str, str]:
        """`same_entity_as` assertions as-of-T, collapsed into `absorbed id -> surviving id`.

        This is the READ half of D2's forward-only merge. The resolver never rewrites a closed
        window, so a host that was `10.0.0.21` for windows 1-39 and `ent-0003` from window 40 has
        rows under both ids; the assertion says they are one host and this is where that is
        applied. Doing it at read time rather than write time is the whole point — a snapshot is
        as-of-T, and a merge learned after T must NOT be applied to a T that predates the evidence
        for it, which is why the `window_start <= as_of` bound is on this query too.

        The subject of a `same_entity_as` is always an id that was UNANCHORED when the merge was
        learned, and its object is always an anchored `ent-NNNN` that never becomes a subject
        itself (`windowing.entities.EntityResolver._observe_host`), so the relation is one hop
        deep by construction and no transitive closure is needed. `_resolve_chain` follows it
        anyway, with a cycle guard: this is a property of today's producer, not of the schema, and
        a store accumulating rows from several builds is exactly where such a property quietly
        stops holding. Sorted, so the result cannot depend on DuckDB's row order (`LES-002`).
        """
        cond, params = ("AND window_start <= ?", [as_of]) if as_of else ("", [])
        rows = self.con.execute(
            f"SELECT DISTINCT subject, object FROM assertions "
            f"WHERE predicate = 'same_entity_as' {cond} ORDER BY subject, object",
            params).fetchall()
        direct = {}
        for subject, obj in rows:
            direct.setdefault(subject, obj)   # first by (subject, object) order: deterministic
        return {s: _resolve_chain(s, direct) for s in sorted(direct)}

    def snapshot(self, as_of: Optional[float] = None,
                 since: Optional[float] = None) -> dict:
        """Consistent as-of-T read: everything with window_start <= as_of.

        **Entity merges are applied HERE** (`Q1`, D2) — this is the read half of forward-only
        resolution. Windows that closed before a merge was learned carry the id they closed with,
        so a host observed as a bare address for 39 windows and as `ent-0003` afterwards has rows
        under both; `entity_aliases` collapses them and every row set below is RE-AGGREGATED under
        the surviving id. That re-aggregation is why it cannot be done in SQL alone: relabelling
        after a `GROUP BY` merges groups, so the sums, counts and maxima have to be recomputed
        over the relabelled keys rather than returned as they came out of DuckDB.

        Applying it at read time is the design (D2), not a shortcut: a merge is evidence with a
        timestamp, so a snapshot as-of-T must use the merges known by T and no others — which is
        why `entity_aliases` takes the same `as_of` bound. A store that rewrote history at write
        time could not answer an as-of question at all.

        Does NOT read `profile_facts` (`Q29`, code review `S4`). Aggregating a per-window
        quantile summary across windows — pooling raw values, weighting by `n`, or something
        else entirely — is a real design question that belongs to whoever builds the `tfc`
        compiler; picking one silently here would pre-commit that design. So as of this
        snapshot the channel has somewhere to WRITE and no way to READ: `tfc`'s
        schedule/intensity half is un-blocked structurally (`docs/WORK_ORDER_subject_less_facts.md`
        Boundaries), not yet buildable end to end. Stated here so the gap is a decline, not an
        oversight a future reader has to rediscover.
        """
        # `since` (2026-08-14, `docs/WORK_ORDER_network_ir.md` build step 5) bounds the read from
        # BELOW as well. Without it every snapshot is cumulative — "everything known up to T" — so
        # any two taken from one store have nested extents and can never be compared as
        # observations of comparable spans. Both shapes are legitimate and wanted by different
        # consumers: the twin compiler wants everything known, a drift comparator wants what was
        # observed during a span. `as_of` keeps its meaning either way; this makes explicit a
        # lower bound that was previously implicit at the store's beginning.
        clauses, params = [], []
        if since is not None:
            clauses.append("window_start >= ?")
            params.append(since)
        if as_of is not None:
            clauses.append("window_start <= ?")
            params.append(as_of)
        cond = "WHERE " + " AND ".join(clauses) if clauses else ""
        # Aliases stay bounded by `as_of` ALONE, deliberately. A merge is knowledge the resolver
        # holds at T, not an event inside the observation span — a host identified before `since`
        # is still that host during it, and dropping those merges would fragment entities that a
        # cumulative snapshot of the same instant resolves correctly.
        alias = self.entity_aliases(as_of)
        canon = alias.get                                   # id -> surviving id, or None
        # DISTINCT windows per canonical entity, not a sum of per-spelling counts. The
        # difference is not cosmetic and it only bites the entities resolution worked hardest on:
        # a merged host has rows under BOTH its pre-merge address and its `ent-NNNN` id (`Q1` D2,
        # forward-only), and those window ranges OVERLAP, so summing them over-counts. Measured on
        # the 2026-08-11 baseline before the fix: 20 of 24 resolved entities reported
        # `windows_seen` ABOVE the store's own window count — `ent-0001` claimed 4 of 3.
        #
        # This corrupts the one field that expresses within-PIT presence shape (`WORK_ORDER_
        # network_ir.md` D4a), which is what a consumer uses to tell an always-on host from an
        # occasional one. Third instance of the pre-merge/post-merge trap (`LES-114`).
        # Aggregated in SQL for the common case — one returned row per entity — because the
        # pair-level alternative returns one row per (entity, window). On the 3-window baseline
        # that is 5,107 rows and irrelevant; over a 41-day capture it is ~1.7M, materialized into
        # Python twice (the fetch, then the sets). The distinct-window count is only *wrong* per
        # spelling for entities a merge touches, and those are few (24 of 3,302 on the baseline),
        # so they get a second, targeted query instead of making everything pay for them.
        merged = set(alias) | set(alias.values())
        nodes: dict[str, list] = {}
        for entity, last_ws, seen in self.con.execute(
                f"SELECT entity, max(window_start), count(DISTINCT window_start) "
                f"FROM node_features {cond} GROUP BY entity", params).fetchall():
            if entity not in merged:
                nodes[entity] = [entity, last_ws, seen]
        if merged:
            # Merged spellings only. Their window ranges OVERLAP — a host seen as an address in
            # window 1 and as `ent-0001` from window 2 shares window 2 — so summing per-spelling
            # counts over-counts and the union has to be taken over actual window values.
            marks = ",".join("?" * len(merged))
            joiner = "AND" if cond else "WHERE"
            windows: dict[str, set] = {}
            for entity, window_start in self.con.execute(
                    f"SELECT entity, window_start FROM node_features {cond} "
                    f"{joiner} entity IN ({marks}) GROUP BY entity, window_start",
                    params + sorted(merged)).fetchall():
                windows.setdefault(canon(entity, entity), set()).add(window_start)
            for key, wins in windows.items():
                nodes[key] = [key, max(wins), len(wins)]
        listens: dict[tuple, float] = {}
        for subject, obj, conf in self.con.execute(
                f"SELECT subject, object, max(confidence) FROM assertions "
                f"{cond + ' AND' if cond else 'WHERE'} predicate='listens_on' "
                "GROUP BY subject, object", params).fetchall():
            key = (canon(subject, subject), obj)
            listens[key] = max(listens.get(key, conf), conf)
        dials: dict[tuple, float] = {}
        for subject, obj, conf in self.con.execute(
                f"SELECT subject, object, max(confidence) FROM assertions "
                f"{cond + ' AND' if cond else 'WHERE'} predicate='dials_port' "
                "GROUP BY subject, object", params).fetchall():
            key = (canon(subject, subject), obj)
            dials[key] = max(dials.get(key, conf), conf)
        # Segment membership (`WORK_ORDER_synth_v1.md` phase 1) — `twin_config.segments[]` and
        # every node's `net:`. Carries `evidence_class`, unlike `listens` above, because this is
        # the first fact reaching the synthesizer that can legitimately be `imputed` (a sensor
        # with no `Site::local_nets` leaves locality to an RFC1918 inference) and the compiler
        # has to mark the field's provenance accordingly rather than publishing every segment
        # as observed.
        #
        # Reduction across WINDOWS keeps the strongest evidence within one class (`max`, in SQL).
        # One window with real sensor evidence is enough to call the membership observed; the
        # imputed windows do not weaken a fact something actually saw.
        #
        # CORRECTED 2026-09-05 — this comment used to claim the choice ACROSS classes was made
        # "explicitly rather than by relying on `max(confidence)` and `evidence_class` happening
        # to rank together". The loop picked on `conf > prev[0]` and nothing else, so it relied on
        # exactly that coupling. The claim was aspirational, and a comment asserting a property
        # the code does not have is the same defect class as a declared exception whose reason is
        # untrue (`LES-139`).
        #
        # **RESOLVED 2026-09-05 (`docs/WORK_ORDER_reference_ingest.md` S2) by DELETING the
        # cross-class reduction here rather than by ranking it here.** The store now returns
        # EVERY class's reading — one row per (entity, cidr, class) — and precedence is resolved
        # by the consumer, `ir/materialize.py::resolve`. Three reasons it belongs there and not
        # here:
        #
        #   * **The house rule this slice is built around is "the store keeps both readings, and
        #     an override is visible, never an erasure."** A reduction here erases the loser
        #     before anything can name it, which is the one thing the model forbids.
        #   * **D3 already said so** — *"`resolve()` is pure ... so the resolver belongs on the
        #     conclusions side"* — and this is the same statement one table over.
        #   * **`evidence_rank` lives in `aximprint.ir`, and `store` may not import it.**
        #     CONVENTIONS §3 puts `ir` ABOVE `store`, and the alternative (a second rank table
        #     spelled out down here) is exactly the vocabulary drift `LES-101` names: two tables
        #     ranking one vocabulary, nothing checking they agree.
        #
        # `SEGMENT_EVIDENCE` supplies (observed, 0.95) and (imputed, 0.6), so confidence and
        # class ranked together by arithmetic accident until now; S2 is what breaks the coupling
        # (the reference ingest writes `asserted`/1.0, and S3's export will write `imported` rows
        # that outrank `observed` at a per-source confidence which can sit below 0.95).
        segments: dict[tuple, tuple[float, str]] = {}
        for subject, obj, ev_class, conf in self.con.execute(
                f"SELECT subject, object, evidence_class, max(confidence) FROM assertions "
                f"{cond + ' AND' if cond else 'WHERE'} predicate='in_segment' "
                "GROUP BY subject, object, evidence_class", params).fetchall():
            key = (canon(subject, subject), obj, ev_class)
            prev = segments.get(key)
            if prev is None or conf > prev[0]:
                segments[key] = (conf, ev_class)
        # Roles (`WORK_ORDER_synth_v1.md` phase 2) — `twin_config.nodes[].role` and the join key
        # a `tfc` profile's `assignments[].role` addresses.
        #
        # **`evidence_class` is a COLUMN of these rows now, not a constant** (D4,
        # `docs/WORK_ORDER_reference_ingest.md`, landed with S2 on 2026-09-05 — the slice that
        # produced the first non-`imputed` role). The returned tuple's own comment below used to
        # read *"No `evidence_class` column, deliberately: every row is `imputed` by
        # construction"*, which
        # was true while the role census was the only producer of `has_role`. The reference
        # ingest is the second: an operator's `override: {field: role}` lands here as `asserted`,
        # and continuing to report it as `imputed` would be exactly the masquerade the class
        # exists to prevent, one field further along than anyone was watching. Corrected in the
        # same change as the code (`LES-043`).
        #
        # Grouped by class like `segments` above, and for the same three reasons. Within one
        # class the STRONGEST confidence survives, not the latest: an entity whose ports named a
        # role in one window and nothing in the next is still that role — absence of evidence in
        # a quiet window is not evidence the host changed jobs.
        roles: dict[tuple, tuple[float, str]] = {}
        for subject, obj, ev_class, conf in self.con.execute(
                f"SELECT subject, object, evidence_class, max(confidence) FROM assertions "
                f"{cond + ' AND' if cond else 'WHERE'} predicate='has_role' "
                "GROUP BY subject, object, evidence_class", params).fetchall():
            key = (canon(subject, subject), ev_class)
            prev = roles.get(key)
            if prev is None or conf > prev[0]:
                roles[key] = (conf, obj)
        edges: dict[tuple, list] = {}
        for src, dst, proto, flows, byts in self.con.execute(
                f"SELECT src, dst, proto, sum(flows), sum(bytes) FROM edge_features {cond} "
                "GROUP BY src, dst, proto", params).fetchall():
            key = (canon(src, src), canon(dst, dst), proto)
            row = edges.get(key)
            edges[key] = [flows, byts] if row is None else [row[0] + flows, row[1] + byts]
        # `entity`/`scope` are appended, never inserted: `synth/synthesizer.py` unpacks these
        # rows as `k, ip, bt, *_`, so a column added at the end costs it nothing and one added
        # in the middle would silently re-bind three names.
        # BOUNDED, like every other read in this method. It was not, and that was a real defect
        # rather than a deliberate exemption: a snapshot as-of-T returned every binding the store
        # ever held, including ones learned AFTER T, so an as-of read could report an identity the
        # resolver did not yet have — and a `since`-bounded PIT (the shape `drift` requires) carried
        # bindings from outside its own span, which `ir_unseen`'s referenced-but-unseen census then
        # inherited wholesale. Found while building that census
        # (`docs/WORK_ORDER_visibility_and_vantage.md` D7).
        #
        # `window_start` is the transition's own window (this table is a CHANGE log since `Q1`),
        # so the bound selects transitions that happened in the span. A binding whose opened/closed
        # rows both fall outside it is therefore absent — the same rule `node_features` already
        # follows, and the reason `first_seen`/`last_seen` ride every row: the interval stays
        # legible even when only one transition of it is in range.
        bindings = self.con.execute(
            f"SELECT kind, ip, bound_to, first_seen, last_seen, entity, scope "
            f"FROM identity_bindings {cond}", params
        ).fetchall()
        return {"as_of": as_of,
                "entities": sorted(tuple(v) for v in nodes.values()),
                "listens": sorted((s, o, c) for (s, o), c in listens.items()),
                # (entity, "<port>/<proto>", confidence) — the dial counterpart of `listens`,
                # and the evidence persona synthesis emits from (GAP-C9).
                "dials": sorted((s, o, c) for (s, o), c in dials.items()),
                # (entity, segment_cidr, confidence, evidence_class) — sorted like every other
                # row set here, so the compiler downstream inherits a byte-stable order for
                # free rather than re-sorting and hoping it matched (`LES-002`).
                "segments": sorted((s, o, c, k) for (s, o, _k), (c, k) in segments.items()),
                # (entity, role, confidence, evidence_class) — one row per (entity, CLASS), which
                # means an entity whose role two classes speak to appears TWICE and the consumer
                # resolves it (`ir/materialize.py::resolve`). **The fourth column landed
                # 2026-09-05** (D4): this used to be a 3-tuple whose comment read "no
                # `evidence_class` column, deliberately: every row is `imputed` by construction",
                # which held only while the role census was the sole producer of `has_role`. The
                # reference ingest is the second, and it writes `asserted`.
                "roles": sorted((e, r, c, k) for (e, k), (c, r) in roles.items()),
                "edges": sorted((s, d, p, f, b) for (s, d, p), (f, b) in edges.items()),
                "bindings": sorted((k, ip, bt, fs, ls, canon(e, e), sc)
                                   for k, ip, bt, fs, ls, e, sc in bindings)}

    def status(self) -> dict:
        """Summary a caller can act on without opening the tables itself.

        `S5` adds the `runs` table: count, and the last row by `finished_at` —
        which build closed most recently, and when. Deliberately NOT run→window
        attribution (which run wrote which `window_health` row): `write_run`
        records a run's window COUNT, not its `window_start` values, so that
        question is not answerable from this schema today — recorded as a
        finding rather than guessed at (a wrong attribution would be worse than
        none). `last_run_*` is `None` on a store nothing has ever cleanly closed
        into, same "no evidence" discipline as `window_health`'s own None-able
        fields (`LES-006`) — a store with rows but zero `runs` is real and must
        be readable as "accumulated, never cleanly closed", not crash here.

        `last_run_unparsed_log_types`/`last_run_unrecognized_log_types`
        (nice-to-have, code review): the SAME `None`-means-unaudited discipline,
        one column over — an operator running `aximprint status` should not have
        to already know `runs` has these two columns and query them by hand.
        Parsed back into a Python object here (`json.loads`), not left as the
        raw JSON string DuckDB hands back, so `cmd_status`'s own `json.dumps(...)`
        does not double-encode it into an escaped string-within-a-string.
        """
        q = lambda t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        wins = self.con.execute(
            "SELECT count(*), min(window_start), max(window_start) FROM window_health"
        ).fetchone()
        last_run = self.con.execute(
            "SELECT code_fingerprint, git_commit, finished_at, unparsed_log_types, "
            "unrecognized_log_types, vantage, network FROM runs "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return {"windows": wins[0], "first_window": wins[1], "last_window": wins[2],
                "node_feature_rows": q("node_features"), "edge_rows": q("edge_features"),
                "assertions": q("assertions"), "bindings": q("identity_bindings"),
                "profile_fact_rows": q("profile_facts"),
                "entity_roster_rows": q("entity_roster"),
                "runs": q("runs"),
                "last_run_code_fingerprint": last_run[0] if last_run else None,
                "last_run_git_commit": last_run[1] if last_run else None,
                "last_run_finished_at": last_run[2] if last_run else None,
                "last_run_unparsed_log_types":
                    json.loads(last_run[3]) if last_run and last_run[3] is not None else None,
                "last_run_unrecognized_log_types":
                    json.loads(last_run[4]) if last_run and last_run[4] is not None else None,
                # Same "no evidence" discipline one column over: `None` here means the run
                # recorded no tap / declared no network, which is the ordinary state for a
                # capture ingested from a bare log directory (`WORK_ORDER_visibility_and_
                # vantage.md` D2 tier 3).
                "last_run_vantage": last_run[5] if last_run else None,
                "last_run_network": last_run[6] if last_run else None}

    def close(self):
        self.con.close()
