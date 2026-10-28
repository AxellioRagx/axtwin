"""aximprint CLI: ingest | features | status | materialize | merge | drift | visibility | cast |
install-cast | assertions | synth | acquire.

`ingest` and `features` are thin verbs over ONE shared driver (`_pipeline`,
`WORK_ORDER_egress_sink_unification.md` D3): both build a plugin, open every
configured sink, run windows through them, extract assertions once per window
(D2), fan out, and close every sink. They differ only in which sinks they
default to and which sink-specific options they own (D4) — `ingest` defaults
to the belief store (`--store`) and can add the evidence log (`--evidence-log`)
or any other registered sink (`--sink NAME=DEST`); `features` defaults to the
jsonl archive (`--out`) and owns `--vocabulary`. Only the CLI imports both
`aximprint.sinks` (the registry: store, evidence, jsonl) and `aximprint.features`
(the vocabulary pin) — CONVENTIONS §3's layering.

`acquire` is the fifth verb, and different in kind: `aximprint acquire
{streams,pcap,harvest}` (`docs/WORK_ORDER_merge_xtract_acquire.md` D3, "ONE entry
point... no xtract console script, no alias") is not built from this module's own
`argparse` parser at all — `_dispatch_acquire` hands the raw argv straight to
`aximprint.acquire.cli.main`, which owns its own flags, help text, and exit codes
verbatim. This is also the one place this file imports `aximprint.acquire`, and it
does so LAZILY, inside the dispatch function, never at module import time — a plain
`aximprint ingest` must keep working with no acquisition stack installed at all
(`requests` is gated behind the optional `[acquire]` extra, CONVENTIONS §3 / D4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aximprint.features import load_pinned_vocabulary
from aximprint.ingest import get_plugin
from aximprint.learners import extract_assertions
from aximprint.sinks import WindowSink, get_sink
from aximprint.store import BeliefStore
from aximprint.synth.synthesizer import synthesize, to_yaml
from aximprint.windowing import WindowManager


def _slice(args) -> tuple[float | None, float | None]:
    """The requested time range, validated before anything is opened.

    Half-open `[from, to)`, matching how windows are cut — an event at exactly
    `to` belongs to the next slice, not to both. An inverted range is rejected
    rather than silently yielding nothing, because "no events in range" and "the
    range was impossible" are different findings and only one is a data fact.
    """
    lo, hi = args.ts_from, args.ts_to
    if lo is not None and hi is not None and lo >= hi:
        raise SystemExit(f"--from ({lo}) must be strictly less than --to ({hi})")
    return lo, hi


def _parse_sink_specs(raw: list[str]) -> list[tuple[str, str]]:
    """Parse repeatable `--sink NAME=DEST` entries. Fail loud on a malformed one.

    A bare name with no '=' is the shape of the older, single-destination
    `--sink` flag and is rejected explicitly rather than read as a
    destination-less sink — guessing a destination for the operator would be a
    silent default (CONVENTIONS §3, fail loud/fail closed).
    """
    specs = []
    for item in raw:
        name, sep, dest = item.partition("=")
        if not sep or not name or not dest:
            raise SystemExit(
                f"--sink must be NAME=DEST with both parts non-empty, got {item!r}")
        specs.append((name, dest))
    return specs


def _reject_repeated_destination(specs: list[tuple[str, str]]) -> None:
    """Fail closed on two specs that resolve to the same destination (`M1`).

    DuckDB does not refuse a second connection to a path already open in the
    same process with the same configuration — it happily opens one, and BOTH
    write (see `tests/test_sinks.py`'s note on the exact mechanism) — so
    `--store b.duckdb --sink store=b.duckdb` used to exit 0 having silently
    doubled every table: `snapshot()`'s `sum(flows)`/`sum(bytes)`, which the
    synthesizer compiles, along with it. Compared as `Path(dest).resolve()` so
    a relative and an absolute spelling of the same file collide too, and
    across every sink TYPE at once, not only within one — two destinations
    that happen to share a filesystem path corrupt each other regardless of
    which sink names each one.

    Deliberately NOT a check against `test_store_alias_and_an_explicit_sink_of_
    the_same_name_both_write`'s scenario: two DIFFERENT destinations under the
    same sink name are unaffected — this only catches destinations that are,
    once resolved, the identical path.
    """
    seen: dict[Path, tuple[str, str]] = {}
    for name, dest in specs:
        resolved = Path(dest).resolve()
        if resolved in seen:
            other_name, other_dest = seen[resolved]
            raise SystemExit(
                f"--sink {name}={dest} and --sink {other_name}={other_dest} resolve to "
                f"the same destination ({resolved}) — writing the same closed windows "
                "into it twice would double every row silently; use two different "
                "destinations, or drop one of the two --sink entries")
        seen[resolved] = (name, dest)


def _open_sinks(specs: list[tuple[str, str]], run_meta: dict,
                open_kwargs: dict | None = None) -> list[tuple[str, str, WindowSink]]:
    """Open one sink per `(name, destination)` spec, in the given (CLI) order.

    `open_kwargs` carries sink-specific options (the vocabulary pin, D4) by
    parameter name; a sink receives an entry only if its OWN class declares
    that name in `accepts` (`sinks.base.WindowSink.accepts`, `M3`) — never by
    inspecting `open`'s signature, which stays silent about a decorator on
    `open` missing `functools.wraps`, or a renamed parameter. A sink that does
    not declare an option never sees it and never has to accept-and-ignore one
    meant for a sibling sink. An option that no OPEN sink declared wanting is
    the caller's own mistake, not a silent no-op (`M2`): it is rejected once
    every sink has had a chance to claim it, naming the option and which
    sinks were actually opened.

    Two fail-closed checks happen before or around the opening itself. Every
    destination must be distinct once resolved to an absolute path (`M1`,
    `_reject_repeated_destination`) — checked before anything opens. And if
    opening sink N of `specs` raises (an unknown name, or a valid sink's own
    `open()` failing on a bad destination), every sink already opened (0..N-1)
    — and sink N's own partial state, if `get_sink` itself succeeded — is
    released via `abort()` before the failure propagates, wrapped with which
    sinks had already opened (`M4`/`M5`): a caller must not see a raw traceback
    with no idea which destinations were already touched.
    """
    if not specs:
        raise SystemExit("no sink configured — pass a sink alias "
                         "(--out / --store / --evidence-log) or --sink NAME=DEST")
    _reject_repeated_destination(specs)
    open_kwargs = open_kwargs or {}
    opened: list[tuple[str, str, WindowSink]] = []
    consumed: set[str] = set()
    for name, dest in specs:
        sink = None
        try:
            sink = get_sink(name)
            kwargs = {k: v for k, v in open_kwargs.items() if k in type(sink).accepts}
            consumed.update(kwargs)
            sink.open(dest, {**run_meta, "sink": name}, **kwargs)
        except Exception as exc:
            already = [n for n, _, _ in opened]
            if sink is not None:
                sink.abort()
            for _, _, opened_sink in opened:
                opened_sink.abort()
            raise RuntimeError(
                f"failed to open sink {name!r}={dest!r}: {exc!r}. sink(s) {already} "
                "had already opened before this failure; each was released (not "
                "closed), so none carries the artifact that means \"this run "
                "finished\"."
            ) from exc
        opened.append((name, dest, sink))

    unconsumed = sorted(open_kwargs.keys() - consumed)
    if unconsumed:
        names = [n for n, _, _ in opened]
        for _, _, sink in opened:
            sink.abort()
        raise SystemExit(
            f"option(s) {unconsumed} were given but no open sink declares wanting "
            f"{'them' if len(unconsumed) > 1 else 'it'} (open sink(s): {names}) — "
            "a sink-specific option that reaches no sink would silently do nothing "
            "while the CLI still claims it took effect; pass a sink that declares "
            f"it (see each sink's `accepts`), or drop {'them' if len(unconsumed) > 1 else 'it'}")
    return opened


#: The `close()` summary's own reserved key. `_close_all` builds
#: `{"name": name, **sink.close()}` — a sink whose OWN summary happened to
#: define a `"name"` key would silently shadow the registry name this function
#: attaches, since a later key wins in dict-literal merge order (`S9`). Same
#: shadowing hazard, same fix, as `features/jsonl.py`'s `_RESERVED`.
_SUMMARY_RESERVED = frozenset({"name"})


def _close_all(opened: list[tuple[str, str, WindowSink]]) -> list[dict]:
    """Close every sink, in open order; a mid-loop failure releases the rest.

    Without this, `[sink.close() for ... in opened]` stops at the first
    exception and leaves every sink AFTER the failing one neither closed nor
    released (`M4`/`M5`) — and the sinks BEFORE it already wrote their own
    completion artifact for what, as a whole multi-sink run, did not finish.
    This cannot undo those earlier artifacts (each is individually true: that
    sink's own output really is complete), but it does make sure nothing after
    the failure is left holding an open handle or connection.
    """
    summaries: list[dict] = []
    for i, (name, _dest, sink) in enumerate(opened):
        try:
            summary = sink.close()
        except Exception as exc:
            remaining = [n for n, _, _ in opened[i + 1:]]
            for _, _, later_sink in opened[i + 1:]:
                later_sink.abort()
            raise RuntimeError(
                f"sink {name!r} failed to close: {exc!r}. "
                f"{[n for n, _, _ in opened[:i]]} had already closed cleanly (their "
                f"completion artifact is real); {remaining} were released (not "
                "closed) rather than left dangling."
            ) from exc
        clash = _SUMMARY_RESERVED & summary.keys()
        if clash:  # fail loud: a sink's own summary must not shadow the registry name
            raise ValueError(f"sink {name!r} close() summary may not define reserved "
                             f"key(s) {sorted(clash)}: {summary}")
        summaries.append({"name": name, **summary})
    return summaries


def _resolve_vantage(plugin, source: str, declared: str | None) -> str | None:
    """Which tap this capture came from — D2's three tiers, applied once, before anything opens.

    `docs/WORK_ORDER_visibility_and_vantage.md` D2: **(1)** the harvest manifest `acquire` wrote
    beside the cold tier, which is authoritative because that layer is the only one that knows the
    appliance and the streams; **(2)** an operator-supplied `--vantage`, for a log directory of
    unknown origin or a `--from-pcaps` run with no appliance; **(3)** `None`, meaning *unknown* —
    never a default string and never the source path (`LES-006`).

    **A disagreement between (1) and (2) is refused, not resolved.** D2's ordering says the
    manifest wins, and quietly discarding an explicit `--vantage` in its favor would file the run
    under a tap the operator did not name while printing nothing — the silent-default failure this
    project refuses everywhere else. One of the two is wrong and nothing here can tell which, so
    both are named and the run stops. This costs nothing precisely because the probe reads a small
    file rather than the capture: the refusal lands before the first window, not after the last one
    (`IngestPlugin.probe_vantage`).
    """
    recorded = plugin.probe_vantage(source)
    known = recorded.vantage if recorded is not None else None
    if known and declared and known != declared:
        raise SystemExit(
            f"--vantage {declared!r} contradicts the vantage manifest at {source!r}, which "
            f"records {known!r}. A capture has one tap point; recording it under two names would "
            f"make two views of one network compare as drift (or two networks compare as one). "
            f"Drop --vantage to use the recorded value, or point --source at the capture you meant")
    return known or declared


def _pipeline(plugin_name: str, source: str, *, ts_from: float | None, ts_to: float | None,
             window_seconds: int, subbuckets: int, specs: list[tuple[str, str]],
             open_kwargs: dict | None = None,
             vantage: str | None = None, network: str | None = None) -> list[dict]:
    """The one driver both verbs call: ingest -> window -> fan out to every sink.

    Every parameter after `source` is keyword-only (`S1`): `window_seconds`
    and `subbuckets` are adjacent same-typed `int`s, so a positional call that
    swapped them would still type-check and would silently rewindow instead of
    raising.

    `run_meta` — what every sink stamps into its own provenance (§10) — is
    built HERE from this function's own parameters, rather than accepted as a
    caller-supplied dict (`S1`): `cmd_ingest` and `cmd_features` used to each
    hand-build an identical `{"source":..., "plugin":..., ...}` literal AND
    pass the same six values a second time as positional arguments — two
    copies of one fact that a future edit to one verb could leave silently
    disagreeing with what this function actually ran.

    Returns one `close()` summary per opened sink (in open order), each tagged
    with its registry `name`.

    **Fan-out does not hide a partial failure** (build step 4). If any sink
    raises on `write_window`, the run stops immediately: the raised error names
    the failing sink, which sinks had already written for THIS window before
    it, and how many windows were fully committed to every sink before this one
    — everything an operator needs to know which destinations are trustworthy.
    Every opened sink is then RELEASED, not closed (`M4`/`M5`, `WindowSink.abort`):
    a sink like `jsonl` signals "this run did not finish" by the ABSENCE of its
    summary artifact (`manifest.json` is written only on a clean close, see
    `features/jsonl.py`) — calling `close()` here would fabricate that signal
    for a run that, in fact, did not finish, so `abort()` releases the same
    handles/connections without it. The belief store's analogous signal (no
    `runs` row for this window range) is unambiguous on a FRESH store; on one
    that already accumulated rows from earlier, successful runs, "no NEW runs
    row" is real but requires reading the table, not just noticing an absence
    the way a missing `manifest.json` file can be noticed.
    """
    plugin = get_plugin(plugin_name)
    # Resolved BEFORE `run_meta` is frozen, unlike `unparsed_stems` below, and that ordering is
    # the whole design (`_resolve_vantage`): `vantage`/`network` are known from the source and the
    # flags, so they ride `run_meta` like `source` and `plugin` do, need no `note_*` channel, and
    # any conflict between them is refused before a window exists.
    run_meta = {"source": source, "plugin": plugin_name,
               "window_seconds": window_seconds, "subbuckets": subbuckets,
               "ts_from": ts_from, "ts_to": ts_to,
               "vantage": _resolve_vantage(plugin, source, vantage),
               # Operator-declared and nothing else (D3). No traffic identifies the network a
               # capture is of, so `None` here is the ordinary state, not a gap to be filled in
               # by inference — it just means two captures cannot be asserted to be of one
               # network, which `materialize`/`drift` then say out loud rather than assume.
               "network": network}
    if run_meta["vantage"]:
        print(f"vantage: {run_meta['vantage']}"
              + (f", network: {network}" if network else ""))
    # `unparsed_log_types_provider`/`unrecognized_log_types_provider` wire `plugin`
    # into `windowing` for D5's names half (`docs/WORK_ORDER_zeek_stem_coverage.md`,
    # the second one additive per code review `S4`) WITHOUT `windowing` ever
    # importing `ingest` (CONVENTIONS §3 layering: siblings, neither imports the
    # other) — a bare closure over this function's own `plugin` local is enough,
    # since the contract (`ingest.base.IngestPlugin.unparsed_stems`/
    # `.unrecognized_stems`) is duck-typed, not a declared `windowing` dependency
    # on `ingest`'s types. WHY a callable rather than the attribute read right
    # here is correct — including for window 1 specifically — is `windowing.windows
    # .WindowManager.__init__`'s own docstring to state, which in turn points at
    # `ingest/zeek/plugin.py::events` as the one canonical explanation (code
    # review nice-to-have: stop re-deriving this argument at every call site).
    wm = WindowManager(window_seconds=window_seconds, subbuckets=subbuckets,
                       unparsed_log_types_provider=lambda: plugin.unparsed_stems,
                       unrecognized_log_types_provider=lambda: plugin.unrecognized_stems)
    opened = _open_sinks(specs, run_meta, open_kwargs)
    windows = 0

    def handle(win) -> None:
        nonlocal windows
        # Computed ONCE per window and handed to every sink unchanged (D2): N
        # sinks cost one extraction, not N.
        assertions = extract_assertions(win)
        written: list[str] = []
        for name, _dest, sink in opened:
            try:
                sink.write_window(win, assertions)
            except Exception as exc:
                names = [n for n, _, _ in opened]
                raise RuntimeError(
                    f"sink {name!r} failed writing window {win.window_start:.0f}: {exc!r}. "
                    f"{windows} earlier window(s) were fully committed to every sink "
                    f"{names}; for THIS window, {written or 'no sink'} had already "
                    "written before the failure. Every opened sink is released "
                    "(not closed), so no destination is left holding an open handle, "
                    "and none is stamped with the artifact that means \"this run "
                    "finished\" — that artifact would be false for all of them."
                ) from exc
            written.append(name)
        windows += 1
        # `flush=True`: `axrange serve` reads this line as the run's progress while the
        # ingest is still going; block-buffered into a pipe it would arrive at exit.
        print(f"window {win.window_start:.0f}: "
              f"{len(win.nodes)} entities, {len(win.edges)} edges, "
              f"{len(assertions)} assertions, health={win.health['event_counts']}",
              flush=True)

    try:
        for win in wm.process(plugin.events(source, ts_from, ts_to)):
            handle(win)
        for win in wm.finalize():
            handle(win)
        # D5's counts half: source-level, so once per run rather than mirrored
        # onto every window (`windows.py::WindowManager`'s own comment on why
        # that would double-count). `plugin.unparsed_stems` is guaranteed to
        # hold its final, run-scoped value HERE — the two loops just above
        # cannot have produced or finalized a single window without already
        # pulling at least the plugin's first event, and that pull is what
        # makes the attribute final (see the comment on `wm`'s own
        # construction, above). Offered to every opened sink; only
        # `StoreSink` (`runs`) does anything with it (`WindowSink
        # .note_unparsed_log_types`'s default no-op) — not the `accepts`/
        # `open_kwargs` mechanism (D4), because reaching no sink here is the
        # ordinary case for most runs, not the caller's mistake `_open_sinks`'s
        # `M2` check exists to catch.
        #
        # INSIDE the try (code review `M3`): every other multi-sink fan-out in
        # this function is wrapped so a failure releases every opened sink
        # instead of leaking a connection or handle — a sink whose override
        # raises here is no different in kind from one whose `write_window`
        # raises, and deserves the identical `abort()`-everything-then-raise
        # treatment, not a bare traceback to `main()` with nothing released.
        #
        # `entity_roster` (`Q1`) rides the same channel and the same timing for the same
        # structural reason — the entity namespace is discovered by RESOLVING, so it is not known
        # at `open()` when `run_meta` is frozen — but it comes from `wm`, not from `plugin`, and
        # only after `finalize()`: the last window's own bindings are minted inside it.
        roster = wm.entity_roster()
        for _, _, sink in opened:
            sink.note_unparsed_log_types(plugin.unparsed_stems)
            sink.note_unrecognized_log_types(plugin.unrecognized_stems)  # `S4`
            sink.note_entity_roster(roster)
    except Exception:
        for _, _, sink in opened:
            sink.abort()
        raise

    return _close_all(opened)


def _print_sink_summary(summary: dict) -> None:
    """Human-readable half of one sink's `close()` summary — the same for every sink.

    Subscripted, not `.get`-ed (mirrors `WindowSink.close`'s own discipline): a
    sink that stopped reporting one of these keys is a sink bug and must
    surface as one, not print nothing and exit 0.
    """
    counts = ", ".join(f"{n}={c}" for n, c in sorted(summary["rows"].items()))
    tail = f" ({counts})" if counts else ""
    print(f"{summary['name']} sink complete: {summary['windows']} window(s) -> "
          f"{summary['destination']}{tail}")
    # A convenience echo of what the archive already carries — the archive is
    # where the asterisk lives; a terminal line travels nowhere (LES-006). `{}`
    # for a sink with no categorical fields (store, evidence), never absent.
    for field, v in summary["vocabulary"].items():
        cov = "n/a" if v["coverage"] is None else f"{v['coverage']:.4f}"
        note = (f", TRUNCATED (+{v['truncated_keys']} key occurrence(s) beyond the cap)"
                if v["truncated_keys"] else "")
        print(f"  {field}: {v['vocabulary']} vocabulary, {v['keys']} key(s), "
              f"coverage={cov}, oov_rows={v['oov_rows']}{note}")


def cmd_ingest(args) -> int:
    """ingest -> window -> fan out to every configured sink (default: the belief store).

    `--store` and `--evidence-log` are aliases (D3) that resolve to `store=` /
    `evidence=` entries ahead of any explicit `--sink NAME=DEST`; `--store`
    always contributes one, matching every prior invocation's behavior (a
    belief store was never optional before this sink existed).
    """
    ts_from, ts_to = _slice(args)
    specs = [("store", args.store)]
    if args.evidence_log:
        specs.append(("evidence", args.evidence_log))
    specs += _parse_sink_specs(args.sink)
    summaries = _pipeline(args.plugin, args.source, ts_from=ts_from, ts_to=ts_to,
                         window_seconds=args.window, subbuckets=args.subbuckets,
                         specs=specs, vantage=args.vantage, network=args.network)
    for summary in summaries:
        _print_sink_summary(summary)
    return 0


def cmd_features(args) -> int:
    """ingest -> window -> fan out to every configured sink (default: the jsonl archive).

    Deliberately still a separate verb from `ingest` (D3): the feature archive
    and the belief store are different shapes for different consumers. One run
    can now write both (add `--sink store=PATH`), but this verb's own default
    stays the jsonl archive, not a replacement for `ingest`'s.
    """
    ts_from, ts_to = _slice(args)
    # Loaded and validated before any destination exists: a malformed pin must
    # not leave a half-written archive behind (fail loud, fail closed).
    #
    # `is not None`, never truthiness (LES-008): the flag defaults to None, so
    # "the user did not pin" and "the user passed an empty string" are different
    # events. An unexpanded `--vocabulary $VOCAB` used to exit 0 with
    # `pinned_to: null` — an unpinned archive indistinguishable from a deliberate
    # one. It now reaches the loader and is rejected as an unreadable pin.
    vocabulary = (load_pinned_vocabulary(args.vocabulary)
                  if args.vocabulary is not None else None)
    if vocabulary is not None:
        print(f"vocabulary pinned to {vocabulary.path} "
              f"(map sha256 {vocabulary.map_digest[:16]}, "
              f"file sha256 {vocabulary.file_digest[:16]}, "
              f"fields: {sorted(vocabulary.indexes)})")
    specs = ([("jsonl", args.out)] if args.out is not None else [])
    specs += _parse_sink_specs(args.sink)
    # D4: `vocabulary` reaches only a sink whose OWN `accepts` declares the
    # option (today, only `jsonl`) — never threaded through the base contract.
    # If it reaches none of the sinks actually opened this run (e.g. `--sink
    # store=PATH` with no `--out`/jsonl at all), `_open_sinks` rejects the run
    # rather than silently pinning nothing while this print already claimed
    # success (`M2`).
    open_kwargs = {"vocabulary": vocabulary} if vocabulary is not None else {}
    summaries = _pipeline(args.plugin, args.source, ts_from=ts_from, ts_to=ts_to,
                         window_seconds=args.window, subbuckets=args.subbuckets,
                         specs=specs, open_kwargs=open_kwargs)
    for summary in summaries:
        _print_sink_summary(summary)
    return 0


def cmd_plugins(args) -> int:
    """`aximprint plugins`: the installed ingest plugins and each one's run-config schema,
    as JSON on stdout. What `axrange serve` reads to answer `GET /api/discovery/plugins`
    without importing this package (the engine never imports `aximprint`)."""
    from aximprint.ingest import list_plugins

    print(json.dumps({"plugins": list_plugins()}, indent=2))
    return 0


def cmd_probe(args) -> int:
    """`aximprint probe --plugin P --source S`: reach the source without pulling events
    (`IngestPlugin.probe`), as JSON on stdout. What `axrange serve` runs for a stored
    discovery source's "test" (`api/Spec.md` A7) and before a "last N days" run, to turn
    the server's own span into bounds. Exit 0 whether or not the source is reachable --
    unreachable is an ANSWER here (`reachable: false` + the reason), not a crash; exit 2
    only when the plugin cannot probe at all."""
    from aximprint.ingest import get_plugin

    try:
        plugin = get_plugin(args.plugin)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    answer = plugin.probe(args.source)
    print(json.dumps({"plugin": args.plugin, "source": args.source, **answer}, indent=2))
    return 0 if answer.get("supported") else 2


def cmd_status(args) -> int:
    store = BeliefStore(args.store)
    print(json.dumps(store.status(), indent=2, default=str))
    store.close()
    return 0


def cmd_synth(args) -> int:
    store = BeliefStore(args.store)
    doc = synthesize(store.snapshot(as_of=args.as_of))
    store.close()
    text = to_yaml(doc)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_bake(args) -> int:
    """Bake the belief store into a PIT (`docs/WORK_ORDER_network_ir.md` build step 3; the verb
    was `materialize` until 2026-10-28, kept as an alias).

    Prints the stats block as well as the summary: `unkeyable_entities` and `dangling_edges` are
    what a bake silently dropped, and an operator who cannot see them cannot tell a clean PIT
    from a lossy one. The summary carries `pit_id` (the identity, a hash of source and
    bounds) and `name` (the label: `--name`, else the id).
    """
    from aximprint.ir.materialize import bake
    from aximprint.ir.store import IRStore

    store = BeliefStore(args.store)
    pit = bake(store, as_of=args.as_of, since=args.since, name=args.name)
    store.close()
    stats = pit.pop("_stats")
    ir = IRStore(args.out)
    ir.write_pit(pit, replace=args.replace)
    row = ir.summary(pit["ir_pit"][0]["pit_id"])[0]
    ir.close()
    print(json.dumps({**row, "rows": {t: len(r) for t, r in pit.items()}, **stats},
                     indent=2, default=str))
    return 0


cmd_materialize = cmd_bake


def _resolve_pit_or_exit(ir, ref: str) -> str:
    """`--pit` takes an id or a name; an unknown or ambiguous one is exit 2 with the reason."""
    try:
        return ir.resolve_pit(ref)
    except KeyError as exc:
        print(f"{exc.args[0]}", file=sys.stderr)
        raise SystemExit(2)


def cmd_pit(args) -> int:
    """`pit list` / `pit rename`: the PITs in an IR store, and the one thing about a PIT that may
    change after it is baked -- its name. The id is the identity and never moves."""
    from aximprint.ir.store import IRStore, IRStoreError

    ir = IRStore(args.store)
    try:
        if args.action == "list":
            print(json.dumps({"pits": ir.pits()}, indent=2, default=str))
            return 0
        try:
            row = ir.rename_pit(args.pit, args.name)
        except KeyError as exc:
            print(f"{exc.args[0]}", file=sys.stderr)
            return 2
        except IRStoreError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(row, indent=2, default=str))
        return 0
    finally:
        ir.close()


def _write_serve_scripts(out: dict, pit: dict, dest, personas: dict) -> None:
    """Give every declared listen set a REAL server, and write the scripts beside the config.

    `axrange/docs/WORK_ORDER_protocol_faithful_services.md` **S2**, under the §8.2 ruling
    (*"implement realistic protocol clients and servers based on the observed data. avoid using
    synthetic data generation where possible"*). This replaces the echo-responder wiring that
    stood here: the echo survives only as a labelled fallback on ports no real server covers
    (D8(a)), and every port a recipe can serve is now served by the real daemon.

    **Two populations, two inputs, and keeping them apart is the whole design.**

      * **Observed hosts** take their declared set from `node["listen"]`, which `compile_twin`
        derived from the OBSERVED listen census. Nothing here can widen it.
      * **The internet sink** has no census -- nothing observed a synthetic node -- so its set is
        built here, from the persona dial set at that sink sub-role (the ports it exists to
        answer) unioned with `services.sink_real_services`, which is DERIVED from a census of the
        external listener population the sink stands in for. A `compiler`-class provenance row
        says so, and carries the derived list.

    ONE SCRIPT PER NODE GROUP, not per role. A role's members can have different observed listen
    sets (this source's four `dns-resolver` hosts answer on 1, 2 and 9 ports), so a role-level
    script would make three of them serve six ports nothing saw them serve.
    """
    from aximprint.synth.responder import is_windows_image, listener_ports
    from aximprint.synth.services import (install_actions, parse_port, plan_node_services,
                                          render_serve_script, sink_real_services,
                                          zone_names_from_pit)

    tc = out["twin_config"]["twin_config"]
    nodes = tc.get("nodes", []) or []
    segment_cidr = {s.get("name"): s.get("cidr") for s in tc.get("segments", []) or []}
    zone_names = zone_names_from_pit(pit)
    dialled = listener_ports(personas) if personas else {}
    # DERIVED from the external listen census, not hardcoded (§8.2's ruling is "based on the
    # observed data"): the ports enough of the population the sink REPLACES was observed
    # answering on, bounded by the recipe table so a port no real server covers can only ever
    # reach the echo anyway.
    sink_real = sink_real_services(pit)

    # THE SINK'S OWN DECLARED SET (§7.4). Written onto the node before the render loop so the
    # sink takes the identical path every other node takes -- a second code path for the sink is
    # how the two populations' rules drift apart.
    for idx, node in enumerate(nodes):
        role = str(node.get("role") or "")
        if not role.startswith("internet-sink") or node.get("listen"):
            continue
        ports = set(dialled.get(role) or set()) | set(sink_real)
        if not ports:
            continue
        node["listen"] = [{"port": p, "proto": t} for p, t in sorted(ports)]
        out["provenance"]["fields"][f"twin_config.nodes[{idx}].listen"] = {
            "evidence_class": "compiler",
            "signal": "synth/services.py sink_real_services + persona dial set",
            "reason": "the internet sink is SYNTHETIC: no capture observed it, so it has no "
                      "listen census of its own and its set cannot be observed. It is the ports "
                      "the twin's personas aim at it -- which exist because nothing LOCAL "
                      "answers them -- plus the ports enough of the EXTERNAL listener "
                      "population it stands in for was observed answering on, which are served "
                      "for real rather than echoed.",
            "real_services": [f"{p}/{t}" for p, t in sink_real],
            "ports": [f"{p}/{t}" for p, t in sorted(ports)],
        }

    # `install: [{script: ...}]` takes a PATH, not inline content: the planner reads the file
    # relative to the scenario dir (`pass1_emit._read_script`). Written beside the config so the
    # whole cast stays one relocatable folder.
    sdir = dest / "scripts"
    for idx, node in enumerate(nodes):
        declared = [parse_port(f"{e['port']}/{e['proto']}") for e in (node.get("listen") or [])]
        declared = [d for d in declared if d]
        if not declared:
            continue
        if is_windows_image(node.get("image", "")):
            # A POSIX script delivered to a Windows guest runs as PowerShell and dies on the
            # first heredoc (measured on dev45). Windows is NOT unserved, though, and the
            # distinction matters: a `domain-controller` carries `install: [{role: adds}]`, and
            # AD DS brings DNS, the KDC, LDAP, SMB, the GC and W32Time as a consequence of the
            # promotion. What is genuinely unserved is a Windows host with no promoting role.
            promotes = any(isinstance(a, dict) and a.get("role")
                           for a in (node.get("install") or []))
            out["caveats"].append(
                f"node {node['name']!r} (role {node.get('role')!r}) is a WINDOWS image, so it "
                f"gets no `serve-*.sh`: the recipes are POSIX shell delivered through "
                f"`run-script`, which a Windows guest runs as PowerShell. "
                + ("Its ports are served by the AD promotion it does carry "
                   "(`install: [{role: adds}]` -> DNS/KDC/LDAP/SMB/GC/W32Time)."
                   if promotes else
                   f"Its declared ports {[f'{p}/{t}' for p, t in declared]} are therefore "
                   f"UNSERVED, and a client dialling them still draws ICMP port-unreachable. "
                   f"A PowerShell recipe is scoped follow-on work, not a silent gap."))
            continue
        # A `ServiceRenderError` from here is a BUG in the recipe table, not an operator error,
        # so it PROPAGATES rather than being demoted to a caveat -- a guard that reports instead
        # of stopping is not a guard (`LES-014`).
        plan = plan_node_services(node["name"], declared,
                                  segment_cidr=segment_cidr.get(node.get("net")))
        sdir.mkdir(parents=True, exist_ok=True)
        rel = f"scripts/serve-{node['name']}.sh"
        (dest / rel).write_text(render_serve_script(
            plan, zone_names=zone_names,
            segment_cidr=segment_cidr.get(node.get("net")),
            origin=f"node group {node['name']!r}"))
        install = list(node.get("install") or [])
        # Idempotent: a re-cast must not stack duplicate actions, and the old `responder-*.sh`
        # spelling is filtered too so a folder carrying both never installs both.
        install = [a for a in install
                   if not (isinstance(a, dict)
                           and ("responder-" in str(a.get("script", ""))
                                or "serve-" in str(a.get("script", ""))))]
        have_pkgs = {str(a.get("pkg")) for a in install if isinstance(a, dict) and a.get("pkg")}
        for action in install_actions(plan, rel):
            if action.get("pkg") in have_pkgs:
                continue
            install.append(action)
        node["install"] = install
        if plan.echo:
            out["provenance"]["fields"][f"twin_config.nodes[{idx}].install.echo"] = {
                "evidence_class": "compiler",
                "signal": "synth/services.py plan_node_services (echo fallback)",
                "reason": "D8(a): no real server in this project's recipe table covers these "
                          "ports, so they are echoed rather than left silent -- an unserved "
                          "port draws an ICMP unreachable that Zeek scores as a FABRICATED "
                          "reverse local edge. The echo is a COMPILER choice, never an "
                          "observation, and it can never bind a port a real daemon on this "
                          "host owns (refused at render time, not skipped at runtime).",
                "ports": [f"{p}/{t}" for p, t in plan.echo],
            }
        if plan.recipe_named("isc-dhcp-server") is None and (67, "udp") in set(declared):
            out["caveats"].append(
                f"node {node['name']!r} was observed answering on 67/udp but gets no DHCP "
                f"server: its segment ({segment_cidr.get(node.get('net'))}) leaves no room for "
                f"a pool. The port falls back to the echo.")
    # THE CROSS-CHECK the work order asks for (§2: the dial set is a cross-check, never the
    # input). A port the twin's personas aim at a role whose nodes were never observed answering
    # it will draw an ICMP unreachable -- reported, never silently added to the declared set,
    # because adding it would put a fabricated listener in the artifact being measured.
    unanswered: dict[str, set] = {}
    for role, ports in sorted(dialled.items()):
        if role.startswith("internet-sink"):
            continue
        served = set()
        for node in nodes:
            if node.get("role") == role:
                served |= {(e["port"], e["proto"]) for e in (node.get("listen") or [])}
        missing = {p for p in ports if p not in served}
        if missing:
            unanswered[role] = missing
    for role, missing in sorted(unanswered.items()):
        out["caveats"].append(
            f"role {role!r}: personas dial {sorted(f'{p}/{t}' for p, t in missing)} at it, but "
            f"no node of that role was OBSERVED answering there, so nothing serves it. Reported "
            f"rather than added: the declared listen set is the observed census (§2), and "
            f"widening it from the dial set is exactly the fabricated-listener defect the "
            f"server half exists to remove.")


def _drop_unsynthesized_roles(prof: dict, personas: dict, caveats: list) -> list[str]:
    """Take out every role-level assignment whose persona this cast did not synthesize, and
    say so in the caveats. Returns the roles dropped, sorted.

    The compiler emits `{role, persona: <roles.yaml persona>}` for every role that has a
    catalog persona, and `plan_assignments` re-points each at the `cast-<role>` document
    synthesized from that role's observed dials. A role whose hosts were never seen dialling
    anything gets no such document (`synthesize_personas` skips a group with no emits), so its
    entry would still name the CATALOG persona -- `monitoring-collector`, say -- which is not
    a file in this cast's folder. Two things are wrong with letting that through, and the
    installer refuses it for the first: a profile must never reference a persona file that is
    not next to it (`install.py`, "no base-persona fallback"); and the catalog document is an
    author's archetype, not an observation -- axRange's `monitoring-collector` scrapes, syslogs
    and SNMP-polls every workstation, while the CorpAPCN capture that found this (2026-10-28,
    one hour) showed its collector speaking NTP and nothing else. Driving it with the archetype
    would put conversations on the wire the source never had, in the artifact being measured.

    So the role takes the same posture as a personaless one (D4): it is IN the twin, it serves
    what it was observed serving, and it generates nothing it was not observed generating. The
    remedy is the one the caveat names -- a longer capture, which is where the 30-day CorpAPCN
    cast got its `cast-monitoring-collector` from.
    """
    dropped = sorted({str(a["role"]) for a in prof.get("assignments", [])
                      if a.get("role") and a.get("persona") not in personas})
    if not dropped:
        return []
    kept_names = {str(a.get("persona")) for a in prof["assignments"]
                  if a.get("role") in dropped}
    prof["assignments"] = [a for a in prof["assignments"] if a.get("role") not in dropped]
    caveats.append(
        f"role(s) {dropped} reached the twin but NO persona was synthesized for them: none "
        f"of their hosts was observed dialling anything in this capture, so there is nothing "
        f"to emit. Their traffic assignment is DROPPED rather than left naming the catalog "
        f"persona ({sorted(kept_names)}), which this cast does not ship and which would "
        f"generate conversations the source was never seen having (D4). The hosts stand up "
        f"and serve what they were observed serving; capture longer to give them behaviour.")
    return dropped


def cmd_cast(args) -> int:
    """Compile a PIT into the three axRange documents (`WORK_ORDER_network_ir.md` step 4).

    Named `cast` after the project's own metaphor (DESIGN_BRIEF §12: "cast / synth — compile a
    snapshot into deployable twin YAML") rather than replacing `synth`, which still compiles the
    v0 draft straight off a belief store and is what tier 2 pins today.
    """
    import json as _json

    from aximprint.ir.store import IRStore
    from aximprint.synth.compiler import compile_twin, to_yaml

    from aximprint.operator_assertions import AssertionsError, load_assertions

    ir = IRStore(args.store)
    args.pit = _resolve_pit_or_exit(ir, args.pit)
    pit = ir.read_pit(args.pit)
    ir.close()
    if not pit["ir_pit"]:
        print(f"no PIT {args.pit!r} in {args.store}", file=sys.stderr)
        return 2
    # `--imprint-id` (`docs/WORK_ORDER_reference_ingest.md` D-f, ruled). The PIT id is the
    # default because it is the only stable name this CLI has: the human-readable imprint name is
    # chosen later, by `scripts/install_cast_as_twin.py`, and axRange's Console passes both the
    # id and the assertions path explicitly.
    imprint_id = args.imprint_id or args.pit
    # `--assertions`, defaulting BESIDE THE IR STORE (D-f). The two cases differ deliberately: a
    # DEFAULT path that does not exist means "this imprint has no assertions yet", which is the
    # ordinary state and the state of every cast before an operator writes one; an EXPLICIT path
    # that does not exist is a caller pointing at the wrong file, and is refused.
    assertions = None
    default_path = Path(args.store).resolve().parent / f"{imprint_id}.assertions.yaml"
    path = Path(args.assertions) if args.assertions else default_path
    if args.assertions or path.exists():
        try:
            assertions = load_assertions(path)
        except AssertionsError as exc:
            print(f"assertions: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"assertions: cannot read {path}: {exc}", file=sys.stderr)
            return 2
    try:
        out = compile_twin(pit, max_count=args.max_count, relocate=args.relocate,
                           assertions=assertions)
    except ValueError as exc:
        # A refusal the FILE caused but only a PIT could detect — an added host with no derivable
        # segment, a name collision. Reported as an operator error rather than as a traceback,
        # the same posture `AssertionsError` gets above.
        if assertions is not None:
            print(f"assertions ({path}): {exc}", file=sys.stderr)
            return 2
        raise
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    # Personas synthesized from the observed traffic, written BESIDE the twin config that
    # references them (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C9). A cast twin driven by the
    # starter archetypes emits what an author imagined rather than what the network was seen
    # doing; these emit the ports the roles' own members actually dialled, at the rates they
    # dialled them.
    from aximprint.synth.personas import (CHATTER_VARIANT_SUFFIX, LOCAL_VARIANT_SUFFIX,
                                          activity_bands, local_population, plan_assignments,
                                          synthesize_personas)

    image_of = {n.get("role"): n.get("image", "")
                for n in out["twin_config"]["twin_config"].get("nodes", [])
                if n.get("role")}
    personas = synthesize_personas(pit, image_of=image_of, chatter=not args.no_chatter)
    if personas:
        nodes_all = out["twin_config"]["twin_config"].get("nodes", [])
        # Re-point every assignment at its synthesized persona. A role with no dial evidence
        # has none to be re-pointed at; `_drop_unsynthesized_roles` below takes its entry out
        # rather than leaving it naming a catalog persona this cast does not ship.
        prof = out["traffic_profile"]["traffic-profile"]
        for a in prof.get("assignments", []):
            cand = f"cast-{a.get('role')}"
            if cand in personas:
                a["persona"] = cand

        # Node-target whatever has to be node-targeted, and split each group's hosts between
        # its local-bearing and external-only variants (`personas.plan_assignments`). Both the
        # band split (GAP-C12) and the population split (iteration 16) live in there: a `role:`
        # assignment covers every host of a role alike, which is exactly the flattening both
        # exist to undo.
        prof["assignments"] = plan_assignments(
            nodes_all, personas, local_population(pit, activity_bands(pit)),
            prof.get("assignments", []))
        _drop_unsynthesized_roles(prof, personas, out["caveats"])

        # Drop a variant nothing was assigned. BEFORE `_write_serve_scripts` below,
        # deliberately: the internet sink's declared listen set is read from every persona it is
        # handed, so an unassigned variant dialling a port nothing else dials would put that port
        # on a sink node -- a twin_config change, i.e. a REDEPLOY, for a persona no host runs.
        # The BASE variants are kept whether assigned or not, because they are the fallback
        # target for a band that never got its own persona.
        used = {a["persona"] for a in prof["assignments"] if a.get("persona")}
        suffixes = (LOCAL_VARIANT_SUFFIX, CHATTER_VARIANT_SUFFIX)
        personas = {pid: doc for pid, doc in personas.items()
                    if pid in used or not pid.endswith(suffixes)}

        pdir = dest / "personas"
        pdir.mkdir(parents=True, exist_ok=True)
        for pid, doc in personas.items():
            (pdir / f"{pid}.yaml").write_text(to_yaml(doc))

    # REAL PROTOCOL SERVERS, outside the `if personas:` block above and deliberately so: an
    # OBSERVED host's declared listen set comes from its own listen census, so it serves what it
    # was seen serving whether or not this PIT produced a single persona. Only the internet
    # sink's set depends on the personas, because nothing ever observed a synthetic node.
    _write_serve_scripts(out, pit, dest, personas)

    # The observed superset, exported beside the deployable projection (GAP-C11). The
    # twin_config drops role-less hosts and carries no edges; the Console's Logical (conversation
    # graph) and Physical (full observed census) views are about exactly what it drops.
    from aximprint.provenance import build_stamp
    from aximprint.synth.imprint_bundle import (
        build_cast_invocation,
        build_topology_bundle,
    )

    # `cast_invocation` (`axr-topology/1.5`). Assembled from the RESOLVED values, not from the
    # switches — `imprint_id` is already defaulted above and `chatter` is the effective setting
    # rather than the flag — so the block describes the cast that HAPPENED, which is what a
    # consumer re-running it needs. `build_stamp()` is `lru_cache`d, so "this run's fingerprint,
    # computed once and handed on unchanged" is structural rather than a convention (`LES-024`).
    #
    # Gated on `assertions is not None` rather than on `args.assertions`: `path` resolves to the
    # DEFAULT location beside the store whether or not a file is there, so recording it
    # unconditionally would claim an input this cast never read. `None` here means the ordinary
    # no-assertions cast, and a name means a file was genuinely applied — by either route.
    invocation = build_cast_invocation(
        pit_id=args.pit, imprint_id=imprint_id, relocate=args.relocate,
        max_count=args.max_count, chatter=not args.no_chatter,
        assertions=str(path) if assertions is not None else None,
        code_fingerprint=build_stamp().get("code_fingerprint"))
    (dest / "topology.json").write_text(
        _json.dumps(build_topology_bundle(pit, imprint_id=imprint_id,
                                          cast_invocation=invocation),
                    indent=2, sort_keys=True)
        + "\n"
    )

    for name, key in (("twin_config", "twin_config"), ("tfc", "traffic_profile"),
                      ("tap", "tap_plan")):
        (dest / f"{name}.yaml").write_text(to_yaml(out[key]))
    # The sidecar, not inline: axRange's twin_config is `extra="forbid"`, so per-field provenance
    # cannot live in the YAML it describes.
    (dest / "provenance.json").write_text(_json.dumps(out["provenance"], indent=2) + "\n")
    print(_json.dumps({"pit_id": args.pit, "imprint_id": imprint_id, "out": str(dest),
                       **({"assertions": {"path": str(path), **assertions.counts}}
                          if assertions is not None else {}),
                       "segments": len(out["twin_config"]["twin_config"]["segments"]),
                       "nodes": len(out["twin_config"]["twin_config"]["nodes"]),
                       "conduits": len(out["twin_config"]["twin_config"]["conduits"]),
                       "assignments": len(out["traffic_profile"]["traffic-profile"]["assignments"]),
                       "provenance_fields": len(out["provenance"]["fields"]),
                       "caveats": out["caveats"]}, indent=2))
    return 0


def cmd_install_cast(args) -> int:
    """Install a cast directory into axRange's twin-config catalog under a stable id.

    A verb rather than a `scripts/` invocation because the install is a step of the handoff an
    operator performs every iteration, not a maintenance chore: the sequence is
    `destroy -> cast -> install -> deploy`, and only one of those four was reachable by path.
    `scripts/install_cast_as_twin.py` still exists and still works — it is a thin wrapper around
    the same `install_cast()` this calls, since the fidelity loop's notes invoke it by path
    (`LES-101`: one body, two entry points, not two bodies).

    Exit codes come straight from `install_cast` and are three-valued on purpose: 0 installed,
    1 a finding in a well-formed cast, 2 usage or shape (`LES-134`).
    """
    from aximprint.synth.install import install_cast, split_sensors

    return install_cast(args.cast, args.root, args.stable, split_sensors(args.sensors))


def cmd_assertions(args) -> int:
    """The YAML-first half of the assertions channel (`docs/WORK_ORDER_reference_ingest.md` S2).

    Four verbs, because they are four different acts on one file and folding them into flags
    would hide that:

      * **schema** — print the published `axr-assertions/1` JSON Schema, verbatim. The only one
        that reads no file: it is the VOCABULARY rather than an act on a document, and it is here
        because an author asking "what may I write?" and an author asking "is this valid?" are
        one person, one minute apart. axRange vendors a byte-identical copy of what this prints.
      * **validate** — parse and refuse, printing the record counts. What a Console's `PUT` runs
        before it writes, and what a scripted author runs before a compile.
      * **merge** — D15's save: fold an incoming document into the standing one, replacing
        same-key records and appending the rest, then re-emit canonically. Reports whether the
        result was byte-identical, because *"a no-op save is a no-op recompile"* is the property
        the whole apply loop rests on and an operator should be able to see it hold.
      * **ingest** — D14's door into the belief store, so an override of a DISCOVERED entity's
        role or segment survives re-materialization. Prints what it could not carry, per record
        kind, rather than leaving a count mismatch for a reader to misdiagnose.

    `cast --assertions` is the fourth thing you can do with the file and is not here: it belongs
    to the compile, not to the file's own lifecycle.
    """
    from aximprint.operator_assertions import (
        AssertionsError, dump_assertions, load_assertions, merge_assertions, schema_text,
    )

    if args.action == "schema":
        # `end=""`: the artifact already ends in a newline, and a `print` that added a second
        # would make `aximprint assertions schema > copy.json` differ from the shipped file by
        # one byte -- in a document whose whole point is being compared byte for byte.
        print(schema_text(), end="")
        return 0

    try:
        aset = load_assertions(args.file)
    except AssertionsError as exc:
        print(f"{args.file}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    if args.action == "validate":
        print(json.dumps({"file": str(args.file), "schema": aset.schema,
                          "imprint": aset.imprint, "as_of": aset.as_of, "source": aset.source,
                          **aset.counts}, indent=2))
        return 0

    if args.action == "merge":
        try:
            incoming = load_assertions(args.incoming)
        except AssertionsError as exc:
            print(f"{args.incoming}: {exc}", file=sys.stderr)
            return 2
        try:
            merged = merge_assertions(aset, incoming)
        except AssertionsError as exc:
            # The merged document can be invalid where neither input was — a key one file adds
            # and the other suppresses. Refused here rather than at the next compile, where the
            # remedy is further away.
            print(f"merge refused: {exc}", file=sys.stderr)
            return 2
        text = dump_assertions(merged)
        dest = Path(args.out or args.file)
        # Compared against the STANDING file, not against whatever happens to be at `--out`:
        # D15's property is *"a no-op save is byte-identical"*, which is a question about the
        # document being saved into, and a fresh `--out` path would answer it `false` for the
        # uninteresting reason that the destination did not exist. (`false` on a hand-commented
        # file's FIRST save is the honest answer and not a bug — a canonical re-emit drops YAML
        # comments; see `dump_assertions`.)
        dest.write_text(text)
        print(json.dumps({"file": str(dest), "merged_from": str(args.incoming),
                          **merged.counts,
                          "byte_identical": Path(args.file).read_text() == text}, indent=2))
        return 0

    # ingest
    import dataclasses

    from aximprint.ingest.reference import get_reference_plugin

    snapshot = get_reference_plugin("assertions").read(str(args.file))
    store = BeliefStore(args.store)
    try:
        snapshot, unresolved = _resolve_reference_subjects(store, snapshot, dataclasses)
        summary = store.write_reference_snapshot(snapshot)
        last_window = store.status()["last_window"]
    finally:
        store.close()
    summary["unresolved_keys"] = unresolved
    # THE TIME BOUND, STATED RATHER THAN LEFT TO BE DISCOVERED. Every row lands at the file's own
    # `as_of` (D14), and `ir/materialize.py::snapshot_evidence` bounds an unqualified
    # materialization at the LAST WINDOW's start — so a file dated after the capture is knowledge
    # the store holds and a default PIT correctly does not carry. Correct under the as-of model
    # and a footgun in practice, so the remedy is printed instead of waiting to be found in a twin
    # that quietly lacks the operator's role.
    if last_window is not None and snapshot.as_of > last_window:
        summary["materialize_hint"] = (
            f"these rows are dated {snapshot.as_of}, after this store's last window "
            f"({last_window}). `aximprint materialize` bounds an unqualified read at the last "
            f"window, so pass `--as-of {snapshot.as_of}` (or later) for a PIT that carries them. "
            f"`aximprint cast --assertions` reads the FILE and is unaffected.")
    print(json.dumps({"store": args.store, **summary}, indent=2))
    return 0


def _resolve_reference_subjects(store, snapshot, dataclasses):
    """Map a reference document's IR `entity_key`s onto the belief store's own subject ids.

    **The two vocabularies are genuinely different and the gap is not cosmetic.** An assertions
    file keys on the stable, self-describing `entity_key` the IR mints (`address:10.80.20.5`,
    `hostname:axdc01`) — D10, because that is the key a twin, a sidecar and a Console all address.
    The belief store keys on the RESOLVER's own entity id, which is a bare address or a run-scoped
    `ent-NNNN`. Written without this map the rows land under a subject nothing joins to, and the
    failure is silent: the store gains four correct-looking rows and every PIT ignores them
    (measured exactly that way against a copy of the CorpAPCN store, which is why this exists).

    The inverse is REDERIVED through `materialize`'s own rule — `derive_entity_key(identifiers,
    addresses)` over the roster, with the bare-address fallback for an entity the roster never
    bound — rather than reimplemented, so the two cannot drift into different ideas of one key
    (`LES-105`). Lives in the CLI because it needs `ir` and `store` together, and the CLI is this
    project's declared wiring point (CONVENTIONS §3).

    Returns `(snapshot with store subjects, [keys that resolved to nothing])`. An unresolvable key
    is reported, never dropped silently and never guessed at: a host the capture never saw has no
    subject in this store, which is a true and useful thing to say.
    """
    from aximprint.ir.keys import derive_entity_key

    roster = {entity: (json.loads(identifiers), json.loads(addresses))
              for entity, identifiers, addresses in store.con.execute(
                  "SELECT entity, identifiers, addresses FROM entity_roster").fetchall()}
    canon = store.entity_aliases().get
    subject_of: dict[str, str] = {}
    for (entity,) in store.con.execute(
            "SELECT DISTINCT entity FROM node_features").fetchall():
        surviving = canon(entity, entity)
        identifiers, addresses = roster.get(surviving, ([], [surviving]))
        derived = derive_entity_key(identifiers, addresses)
        if derived is not None:
            subject_of.setdefault(derived[0], surviving)
    facts, unresolved = [], []
    for fact in snapshot.facts:
        subject = subject_of.get(fact["subject"])
        if subject is None:
            unresolved.append(fact["subject"])
            continue
        facts.append({**fact, "subject": subject})
    return (dataclasses.replace(snapshot, facts=tuple(facts)),
            sorted(set(unresolved)))


def cmd_merge(args) -> int:
    """Combine PITs along an explicit axis (`WORK_ORDER_visibility_and_vantage.md` D4/D10).

    The axis is **required**, with no default: across vantages the same windows are seen twice
    (coverage takes the best, volumes take the max) while across time they are disjoint (both
    accumulate), and a default would silently pick one of those for a caller who did not think
    about it. `ir/merge.py`'s own docstring is the full table.
    """
    from aximprint.ir.merge import MergeError, merge
    from aximprint.ir.store import IRStore

    ir = IRStore(args.store)
    try:
        combined = merge(ir, [_resolve_pit_or_exit(ir, p) for p in args.pits], args.axis)
        stats = combined.pop("_stats")
        ir.write_pit(combined, replace=args.replace)
        row = ir.summary(combined["ir_pit"][0]["pit_id"])[0]
    except MergeError as exc:
        # Exit 2 like `drift`'s refusal: the tool declining to answer is a usage-level outcome,
        # not a failure of the merge itself.
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        ir.close()
    print(json.dumps({**row, "rows": {t: len(r) for t, r in combined.items()}, **stats},
                     indent=2, default=str))
    return 0


def cmd_visibility(args) -> int:
    """What one PIT's vantage saw and knows it missed (`WORK_ORDER_visibility_and_vantage.md` D7).

    Read-only over an existing PIT — no store, no capture, nothing gated.
    """
    from aximprint.ir.store import IRStore
    from aximprint.ir.visibility import report

    ir = IRStore(args.store)
    try:
        out = report(ir, _resolve_pit_or_exit(ir, args.pit))
    except KeyError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    finally:
        ir.close()
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_drift(args) -> int:
    """Structural drift between two PITs (`docs/WORK_ORDER_network_ir.md` build step 5)."""
    from aximprint.ir.compare import IncommensurableError, compare
    from aximprint.ir.store import IRStore

    ir = IRStore(args.store)
    try:
        report = compare(ir, _resolve_pit_or_exit(ir, args.from_pit),
                         _resolve_pit_or_exit(ir, args.to_pit))
    except IncommensurableError as exc:
        # Exit 2, not 1: this is the tool declining to answer, which is a usage-level outcome
        # (the caller chose an unsuitable pair), not a failure of the comparison itself.
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        ir.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


def _dispatch_acquire(argv: list[str]) -> int:
    """`aximprint acquire {streams,pcap,harvest}` (`docs/WORK_ORDER_merge_xtract_acquire.md`
    D3: "ONE entry point... no xtract console script, no alias").

    Deliberately NOT built as a nested `add_subparsers` entry under `main`'s own parser:
    `acquire.cli` has its own top-level flags (`--host`, `--profile`, `--api-key-file`, ...)
    ahead of its own `{streams,pcap,harvest}` subcommand, and re-declaring that shape here
    would risk drifting from `acquire.cli._build_parser` — the one copy of truth for
    "each verb's existing flags and exit codes verbatim" (D3). Instead the raw argv (past
    the leading `"acquire"` token) is handed straight to `acquire.cli.main`, unparsed by
    anything in this module, so every flag and every exit code is verbatim by construction.

    The import is HERE, not at this module's top level, and that placement is load-bearing
    (D4): `requests` is gated behind the optional `[acquire]` extra, so `import
    aximprint.cli` — which happens on every invocation, including a plain `aximprint
    ingest` — must keep working with no acquisition stack installed at all. An absent
    extra therefore surfaces here, as a plain user-facing message naming the fix, never as
    a bare `ModuleNotFoundError` traceback.

    Catches `ModuleNotFoundError` specifically, not the broader `ImportError` it is a
    subclass of, and then discriminates on **`exc.name`** (the dotted module the import
    machinery actually failed to find) before relabeling anything (fix round item 5):
    `aximprint.acquire` ships INSIDE `aximprint` — the `[acquire]` extra supplies only
    `requests` — so `exc.name` rooted in `aximprint` means a submodule of an already-PRESENT
    package could not be found (a typo'd import inside `acquire`, say), which is a real bug
    that `pip install -e ".[acquire]"` cannot fix and must not claim to. Only a non-`aximprint`
    root (`requests`, one of ITS transitive deps, ...) is the actual missing-extra shape. A
    plain `ImportError` that is not a `ModuleNotFoundError` at all — e.g. "cannot import name
    'Credential' from 'aximprint.acquire.auth'", a real bug in a fully-installed package —
    is not caught here either way, and propagates as itself: relabeling THAT as a packaging
    problem is exactly the defect this discrimination exists to fix.
    """
    try:
        from aximprint.acquire.cli import main as acquire_main
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] == "aximprint":
            raise  # a bug inside a present acquire package, not a packaging state
        raise SystemExit(
            "aximprint acquire: the 'acquire' extra is not installed "
            f"({exc}) — run `pip install -e \".[acquire]\"` to enable the "
            "acquire verbs (streams/pcap/harvest)"
        ) from exc
    return acquire_main(argv)


def main(argv=None) -> int:
    # `acquire` is intercepted before this function's own parser ever sees the argv — see
    # `_dispatch_acquire`'s docstring for why. Resolve the same default `argparse` itself
    # would (`sys.argv[1:]` when `argv` is `None`) so this check applies uniformly whether
    # `main()` is called bare (the console script / `__main__` guard) or with an explicit
    # `argv` (tests).
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["acquire"]:
        return _dispatch_acquire(raw_argv[1:])

    p = argparse.ArgumentParser(
        prog="aximprint",
        description="learn a network's imprint; cast it as twin config",
        # `acquire` is a listed choice above, so this no longer has to announce the
        # verb's existence — only the two things the choice line has no room for.
        epilog="`acquire` needs its optional extra: `pip install -e \".[acquire]\"`. "
               "Its verbs and flags: `aximprint acquire --help`.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_source_args(p):
        """--source and its time slice. Shared so the two verbs cannot drift."""
        p.add_argument("--source", required=True,
                       help="plugin source (zeek: a log dir/file — flat, or a "
                            "tree of per-window log dirs — or "
                            "'parquet:<archive root>' for a metadata archive — see "
                            "docs/SPEC_zeek_metadata_archive.md)")
        # default=None, never 0.0: "not sliced" and "sliced from the epoch" are
        # different requests, and 0.0 is falsy (`LES-008`).
        p.add_argument("--from", type=float, default=None, dest="ts_from",
                       help="epoch seconds, inclusive; requires a parquet: source")
        p.add_argument("--to", type=float, default=None, dest="ts_to",
                       help="epoch seconds, exclusive; requires a parquet: source")

    def add_sink_arg(p):
        """--sink NAME=DEST, repeatable — the mechanism `ingest`/`features` share (D3)."""
        p.add_argument("--sink", action="append", default=[], metavar="NAME=DEST",
                       help="repeatable: also write to an additional registered sink, "
                            "e.g. store=imprint.duckdb, evidence=evidence.jsonl, "
                            "jsonl=./features")

    pi = sub.add_parser("ingest", help="run sensor metadata through the pipeline into the store")
    pi.add_argument("--plugin", default="zeek")
    add_source_args(pi)
    pi.add_argument("--store", default="imprint.duckdb",
                    help="belief-store path (alias for --sink store=PATH)")
    pi.add_argument("--evidence-log", default="",
                    help="optional JSONL evidence log path (alias for --sink evidence=PATH)")
    add_sink_arg(pi)
    # D2 tier 2 / D3. Both default to None — UNKNOWN, which is a real state and the common one, not
    # a value to be filled in (`LES-006`). `--vantage` is only consulted when the capture carries no
    # harvest manifest; a disagreement with one is refused rather than silently overridden.
    pi.add_argument("--vantage", default=None, metavar="TAP",
                    help="where the tap sat, for a capture with no harvest manifest (a log "
                         "directory of unknown origin, or a --from-pcaps run). Ignored when the "
                         "source records one; a CONTRADICTING value is refused, not overridden")
    pi.add_argument("--network", default=None, metavar="NAME",
                    help="which network this capture is OF, as distinct from where it was taken "
                         "and what it was written to. Operator-declared: nothing in traffic "
                         "identifies it. Two captures declaring one network may be compared and "
                         "merged across vantages; without it, only identical sources compare")
    pi.add_argument("--window", type=int, default=3600, help="window seconds (default 3600)")
    pi.add_argument("--subbuckets", type=int, default=12)
    pi.set_defaults(fn=cmd_ingest)

    pf = sub.add_parser("features",
                        help="run sensor metadata through the pipeline into feature files")
    pf.add_argument("--plugin", default="zeek")
    add_source_args(pf)
    # default=None, not required: `--sink jsonl=DEST` (or any other sink) is a
    # valid way to run this verb without `--out` at all.
    pf.add_argument("--out", default=None,
                    help="output directory for the jsonl feature files "
                         "(alias for --sink jsonl=DEST)")
    add_sink_arg(pf)
    # default=None, not "": an unset flag and an empty value are different events,
    # and only one of them may produce an unpinned archive.
    pf.add_argument("--vocabulary", default=None,
                    help="pin the categorical index map to a prior manifest.json (or a "
                         "vocabulary file) so two captures encode to the same columns; "
                         "keys outside it are counted as out-of-vocabulary, never appended")
    pf.add_argument("--window", type=int, default=3600, help="window seconds (default 3600)")
    pf.add_argument("--subbuckets", type=int, default=12)
    pf.set_defaults(fn=cmd_features)

    pp = sub.add_parser("plugins",
                        help="list the installed ingest plugins and their run-config schemas "
                             "(JSON)")
    pp.set_defaults(fn=cmd_plugins)

    ppr = sub.add_parser("probe",
                         help="reach a plugin source without pulling events: reachability, "
                              "span and vantage as JSON (exit 2 if the plugin cannot probe)")
    ppr.add_argument("--plugin", required=True)
    ppr.add_argument("--source", required=True)
    ppr.set_defaults(fn=cmd_probe)

    ps = sub.add_parser("status", help="belief store summary")
    ps.add_argument("--store", default="imprint.duckdb")
    ps.set_defaults(fn=cmd_status)

    pm = sub.add_parser("bake", aliases=["materialize"],
                        help="bake the belief store into a point-in-time network IR (a PIT) "
                             "and give it an id; `materialize` is the old spelling")
    pm.add_argument("--store", default="imprint.duckdb", help="belief-store path to read")
    pm.add_argument("--out", default="imprint-ir.duckdb",
                    help="IR store path to write (accumulates PITs; created if absent)")
    pm.add_argument("--as-of", type=float, default=None, dest="as_of",
                    help="epoch seconds; default is everything known, resolved to the latest "
                         "window start (a PIT always carries a concrete as_of)")
    # Not `--force`: this refuses only one specific thing, and naming it after that thing is what
    # makes the refusal message and the flag read as the same decision.
    pm.add_argument("--since", type=float, default=None,
                    help="epoch seconds; bound the observation from below too. Without it a PIT "
                         "is CUMULATIVE (everything known up to --as-of), and two cumulative "
                         "PITs from one store have nested extents and are refused as "
                         "incommensurable by `drift`")
    pm.add_argument("--replace", action="store_true",
                    help="overwrite an existing PIT with the same id. Refused by default: "
                         "something may already have compared against it")
    pm.add_argument("--name", default=None, metavar="NAME",
                    help="the PIT's label, distinct from its id; defaults to the id. Every "
                         "verb that takes --pit accepts either, and `pit rename` changes it")
    pm.set_defaults(fn=cmd_bake)

    pp_ = sub.add_parser("pit", help="list the PITs in an IR store, or rename one")
    pp_sub = pp_.add_subparsers(dest="action", required=True)
    pp_l = pp_sub.add_parser("list", help="every PIT with its id, name, as_of and size")
    pp_l.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    pp_r = pp_sub.add_parser("rename", help="give a PIT a new name (its id never changes)")
    pp_r.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    pp_r.add_argument("pit", metavar="PIT", help="the PIT's id or current name")
    pp_r.add_argument("name", metavar="NAME", help="the new name; must not be another PIT's "
                                                  "name or id")
    pp_.set_defaults(fn=cmd_pit)

    pc = sub.add_parser("cast", help="compile a PIT into axRange twin_config + tfc + tap")
    pc.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    pc.add_argument("--pit", required=True, metavar="PIT", help="a PIT id or name")
    pc.add_argument("--out", default="twin", help="output directory")
    pc.add_argument("--max-count", type=int, default=None, dest="max_count",
                    help="cap a collapsed node group's count. Uncapped by default — the network "
                         "really has that many hosts; a cap that bites is reported as a caveat")
    # `--min-occupancy` was REMOVED, not kept as a no-op (`docs/WORK_ORDER_deployment_scope.md`
    # D1, Authority: user). The threshold now lives in the emitted config's
    # `params.DEPLOY_MIN_OCCUPANCY`, where axRange's expander reads it and
    # `te deploy --param DEPLOY_MIN_OCCUPANCY=<x>` overrides it. Two places to set one policy is
    # the drift trap this change exists to close, and a flag that silently does nothing is worse
    # than one that is gone: a caller passing it now gets an argparse error naming it, not a
    # twin that quietly ignored the bar it was given. `--max-count` is a different question
    # (scale-down, not scope) and stays.
    # Iteration 16 shipped the population allocator and the chatter variant together and moved
    # five axes in both directions at once, so neither is attributable. Default OFF (i.e.
    # chatter stays ON) so the flag's existence changes no cast; passing it isolates the
    # allocator. There is deliberately no matching switch for the local-bearing variant -- that
    # IS the allocator, and it is what the comparison is being read against.
    pc.add_argument("--no-chatter", action="store_true", dest="no_chatter",
                    help="omit the CHATTER persona variant (a host whose only local presence "
                         "is link-local discovery). Default: emitted. Use to run the "
                         "population allocator on its own and attribute a fingerprint move to "
                         "one change rather than two")
    pc.add_argument("--imprint-id", default=None, dest="imprint_id", metavar="ID",
                    help="the imprint's stable name, used for the topology bundle's "
                         "`imprint_id` and for the default --assertions path. Defaults to the "
                         "PIT id, which is the only stable name this CLI has before "
                         "`install_cast_as_twin.py` chooses a human-readable one")
    pc.add_argument("--assertions", default=None, metavar="PATH",
                    help="an axr-assertions/1 file of operator assertions to apply "
                         "(`docs/WORK_ORDER_reference_ingest.md` §4). Defaults to "
                         "<dir of --store>/<imprint-id>.assertions.yaml, and its ABSENCE there "
                         "is the ordinary state; an explicit path that does not exist is an "
                         "error")
    pc.add_argument("--relocate", default=None, metavar="PREFIX",
                    help="re-address the twin into PREFIX (e.g. 10.200.0.0/16), segment i -> "
                         "<a>.<b>.i.0/24. A cast otherwise reproduces the SOURCE network's "
                         "CIDRs, which can collide with the lab the twin is deployed into — "
                         "including its default gateway. Originals are kept in provenance")
    pc.set_defaults(fn=cmd_cast)

    pic = sub.add_parser(
        "install-cast",
        help="install a cast directory into axRange's twin-config catalog under a stable id")
    pic.add_argument("cast", type=Path, metavar="CAST_DIR",
                     help="an `aximprint cast --out` output directory")
    pic.add_argument("root", type=Path, metavar="CATALOG_ROOT",
                     help="the twin-configs directory to install into; the cast lands in "
                          "<CATALOG_ROOT>/<STABLE_ID>/")
    pic.add_argument("stable", metavar="STABLE_ID",
                     help="the stable twin id the cast's PIT id is rewritten to. `te deploy "
                          "--update` finds a standing deploy by this name, so a re-cast under "
                          "the same id is an incremental update rather than a full rebuild")
    # `nargs="+"` here and comma-separated in `scripts/install_cast_as_twin.py`; `split_sensors`
    # accepts either, so `--sensors A B` and `--sensors A,B` both work at both entry points.
    pic.add_argument("--sensors", nargs="+", default=[], metavar="NAME",
                     help="o2/external sensor name(s) to fill into every tap binding, e.g. "
                          "--sensors XPress-SW-A. Omitted leaves `sensors:` absent, which is "
                          "what the cast emits and what axRange refuses to apply — a cast "
                          "cannot observe which sensor a lab has")
    pic.set_defaults(fn=cmd_install_cast)

    pn = sub.add_parser(
        "assertions",
        help="validate, merge or ingest an axr-assertions/1 operator assertions file")
    pn_sub = pn.add_subparsers(dest="action", required=True)
    pn_sub.add_parser(
        "schema",
        help="print the published axr-assertions/1 JSON Schema (draft 2020-12), verbatim")
    pn_v = pn_sub.add_parser("validate", help="parse and refuse; print the record counts")
    pn_v.add_argument("file", metavar="FILE")
    pn_m = pn_sub.add_parser(
        "merge", help="fold an incoming save into FILE and re-emit it canonically (D15)")
    pn_m.add_argument("file", metavar="FILE")
    pn_m.add_argument("--from", required=True, dest="incoming", metavar="PATH",
                      help="the incoming document; its records REPLACE same-key ones in FILE "
                           "and append otherwise, and its `as_of` becomes the merged one's")
    pn_m.add_argument("--out", default=None, metavar="PATH",
                      help="write here instead of in place")
    pn_i = pn_sub.add_parser(
        "ingest", help="write the file's facts into a belief store as `asserted` (D14)")
    pn_i.add_argument("file", metavar="FILE")
    pn_i.add_argument("--store", default="imprint.duckdb", help="belief store path")
    pn.set_defaults(fn=cmd_assertions)

    pg = sub.add_parser("merge", help="combine PITs across vantages, or across time")
    pg.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    # Required, deliberately: the two axes compute coverage and volume differently, and a default
    # would silently pick one for a caller who did not consider the difference.
    pg.add_argument("--axis", required=True, choices=("vantage", "time"),
                    help="vantage: several taps over one span, coverage takes the best. "
                         "time: one tap over adjacent spans, windows accumulate and overlapping "
                         "spans are refused")
    pg.add_argument("--pit", action="append", required=True, dest="pits", metavar="PIT_ID",
                    help="repeatable; at least two")
    pg.add_argument("--replace", action="store_true",
                    help="overwrite an existing merged PIT of the same id")
    pg.set_defaults(fn=cmd_merge)

    pv = sub.add_parser("visibility",
                        help="what a PIT's vantage saw, and who it knows it missed")
    pv.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    pv.add_argument("--pit", required=True, metavar="PIT", help="a PIT id or name")
    pv.set_defaults(fn=cmd_visibility)

    pd = sub.add_parser("drift", help="structural drift between two PITs in an IR store")
    pd.add_argument("--store", default="imprint-ir.duckdb", help="IR store path")
    pd.add_argument("--from", required=True, dest="from_pit", metavar="PIT_ID")
    pd.add_argument("--to", required=True, dest="to_pit", metavar="PIT_ID")
    pd.set_defaults(fn=cmd_drift)

    py = sub.add_parser("synth", help="cast a snapshot into draft twin YAML")
    py.add_argument("--store", default="imprint.duckdb")
    py.add_argument("--as-of", type=float, default=None, dest="as_of")
    py.add_argument("--out", default="")
    py.set_defaults(fn=cmd_synth)

    # `acquire` is registered as a real choice so it appears in the `{...}` list
    # `aximprint --help` prints — the only place most operators look. It used to
    # live in the epilog alone, on the reasoning that a subparser for an
    # intercepted verb would be decorative dead code; a user then read the choice
    # list, concluded `acquire` was not a subcommand, and reported it as missing.
    # Discoverability wins: the choice list is the interface, and prose below it
    # is not a substitute for being in it.
    #
    # Not decorative, either. `main()` intercepts `acquire` before this parser is
    # built, so in practice this branch never runs — but it forwards to the same
    # `_dispatch_acquire`, which makes the two paths agree by construction rather
    # than by comment. `add_help=False` + `REMAINDER` keep the forwarding
    # verbatim: acquire's own flags stay undeclared here (re-declaring them is
    # what `_dispatch_acquire`'s docstring forbids, and what would actually
    # drift), and `--help` reaches acquire's parser rather than this stub's.
    pa = sub.add_parser(
        "acquire",
        help="pcap/Zeek extraction from an axapi appliance (needs the [acquire] extra)",
        add_help=False,
        usage="aximprint acquire {streams,pcap,harvest} ... "
              "(run `aximprint acquire --help`)",
    )
    pa.add_argument("acquire_argv", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    pa.set_defaults(fn=lambda a: _dispatch_acquire(a.acquire_argv))

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
