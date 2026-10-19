"""JSONL feature sink — DESIGN_BRIEF §5.4's window artifacts as files on disk.

One output directory, one file per artifact, one JSON object per line:

  nodes.jsonl     one row per (window_start, entity) — the GNN's node input and
                  the behavioral fitter's observation
  edges.jsonl     one row per (window_start, src, dst, proto) — the typed
                  conversation graph for the window
  windows.jsonl   one row per window — the health record (§5.4 #5)
  identity.jsonl  one row per binding delta — evidence + intervals
  manifest.json   one object for the run — source, window params, code build,
                  per-file row counts, event totals, the categorical index map,
                  wall-clock

Every row carries its `window_start` and its key, so a file is self-describing and
joinable without positional assumptions (brief §11, "named fields, not positions" —
the reasoning is sound today even though the versioning it belongs to is inert).

**Read the manifest first.** It is written last but is designed to be *read*
first: it carries the categorical index map (`features/vocabulary.py`, `Q6`), so
a consumer builds its columns from `manifest.json` alone and then streams
`nodes.jsonl`. Nothing about the encoding requires a prior pass over a row file.

Deterministic by construction: rows go out in key order and each line's keys are
sorted, so a file's bytes are a function of the window's contents and of nothing
else — never of an upstream dict's iteration order (CONVENTIONS §3). Wall-clock
enters this layer in exactly one place, `WALLCLOCK_FIELD` of the manifest.

`manifest.json` is written last and only on a clean close: an output directory
without one is the record of a run that did not finish.

The sink contract (`WindowSink`, `register`) lives in `aximprint.sinks` now,
not in this package — `WORK_ORDER_egress_sink_unification.md` moved it to a
neutral module so `store`'s own sink could implement it without `store`
importing `features` (see `sinks/base.py`'s docstring for the full "layering
crux"). This file imports *down* into `sinks`, same as it always imported down
into `windowing`.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from aximprint.features.vocabulary import (
    NODE_CATEGORICAL, PinnedVocabulary, VocabularyObserver, coverage_summary,
)
from aximprint.provenance import build_stamp
from aximprint.sinks.base import WindowSink, register
from aximprint.windowing.windows import WindowResult

#: One string, not two that can drift: the categorical spec set declares the
#: artifact its map applies to, and the sink reads the name from there.
NODES = NODE_CATEGORICAL.artifact
EDGES = "edges.jsonl"
WINDOWS = "windows.jsonl"
IDENTITY = "identity.jsonl"
MANIFEST = "manifest.json"

#: The one manifest key holding values that cannot reproduce across runs.
#: Everything else in the manifest — and every byte of the four JSONL files — is a
#: pure function of (input bytes, window params, code build). Tests and
#: `scripts/verify_determinism.py` import this rather than hard-coding the name,
#: so "wall-clock lives here and nowhere else" has a single source of truth.
WALLCLOCK_FIELD = "wallclock"

#: Manifest keys the sink owns; run_meta may not shadow them (checked in `open`).
_RESERVED = frozenset({"build", "windows", "rows", "event_counts", "categorical",
                       "entities", WALLCLOCK_FIELD})


def _row(key: dict, payload: dict) -> str:
    """One JSONL line: the row's key fields merged over its payload.

    A payload field colliding with a key field is rejected rather than silently
    overwritten — the key is what makes the file joinable, so a collision is a bug
    upstream, not a merge. Keys are sorted for byte-stability.
    """
    clash = key.keys() & payload.keys()
    if clash:
        raise ValueError(f"feature field(s) collide with the row key: {sorted(clash)}")
    return json.dumps({**key, **payload}, sort_keys=True, default=str)


@register
class JsonlSink(WindowSink):
    """Stdlib-only JSONL writer. Files are opened once, appended per window, closed once."""

    name = "jsonl"
    #: The one sink-specific option this codebase has (D4, `Q6`) — declared here
    #: so the driver's router (`cli._open_sinks`, `sinks.base.WindowSink.accepts`,
    #: `M3`) can route `vocabulary` to this sink WITHOUT inspecting `open`'s
    #: signature.
    accepts = frozenset({"vocabulary"})

    def __init__(self):
        self._dir: Path | None = None
        self._fh: dict = {}
        self._rows: Counter = Counter()    # rows written per file
        self._events: Counter = Counter()  # event totals across the run
        self._windows: int = 0
        self._meta: dict = {}
        self._started: float = 0.0
        # The one thing that spans windows on purpose: the run-level index map,
        # bounded per field by `vocabulary.MAX_KEYS` (see `sinks.base.WindowSink`).
        # None until `open()`, which is where the pin arrives — a default observer
        # here would be constructed and unconditionally discarded, reading as a
        # meaningful fallback that no code path can ever use.
        self._vocab: VocabularyObserver | None = None
        #: The run's entity namespace (`note_entity_roster`, `Q1`), written into `manifest.json`
        #: at `close()`. `None` until then, and `None` in the manifest means the driver never
        #: offered one — honestly different from a roster whose `bound` list is simply empty,
        #: which is what a capture with no host-scoped evidence produces (`LES-006`).
        self._entities: dict | None = None

    # -- lifecycle --------------------------------------------------------------

    def open(self, destination: str, run_meta: dict,
             vocabulary: PinnedVocabulary | None = None) -> None:
        clash = _RESERVED & run_meta.keys()
        if clash:  # fail closed, before a byte is written
            raise ValueError(f"run_meta may not shadow manifest key(s): {sorted(clash)}")
        self._dir = Path(destination)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Self-protecting output: features derived from a real capture inherit its
        # sensitivity (CONVENTIONS §3, LES-003), and `--out` may legitimately point
        # inside the repo. A `*` ignore in the output dir itself covers every
        # artifact — including `manifest.json`, which the repo's `*.jsonl` rule
        # misses — without guessing at directory names or shadowing a legitimate
        # manifest elsewhere in the tree.
        (self._dir / ".gitignore").write_text("# Feature archive — capture-derived, never committed (LES-003).\n*\n")
        self._meta = dict(run_meta)
        self._vocab = VocabularyObserver(pinned=vocabulary)
        # "w", not "a": a re-run replaces its output. Appending would double every
        # row and leave the second run's manifest disagreeing with its own files.
        self._fh = {name: (self._dir / name).open("w", encoding="utf-8")
                    for name in (NODES, EDGES, WINDOWS, IDENTITY)}
        self._started = time.time()  # the ONLY wall-clock read in this layer

    def write_window(self, win: WindowResult, assertions: list[dict]) -> None:
        if not self._fh:
            raise RuntimeError("write_window before open()")  # fail loud
        # `assertions` is accepted per the contract (`sinks.base.WindowSink`) and
        # deliberately unused: this round does not add a fifth artifact for them
        # (`assertions.jsonl`) — recorded as a finding in
        # WORK_ORDER_egress_sink_unification.md, not fixed here, because doing so
        # would change the archive's bytes, which this change must not do.
        del assertions
        # `win.capture_profile` (`Q29`) is similarly never read here: DESIGN_BRIEF §5.4
        # declares five artifacts, and a population-scoped parameter is not a node or edge
        # feature, so a sixth (`profile.jsonl`) is declined by name, not by omission
        # (`docs/WORK_ORDER_subject_less_facts.md` Boundaries). Whether the learner layer
        # eventually wants profile facts as training input is a real, open question, left
        # there rather than answered here (same work order, "What this deliberately does
        # NOT do").
        ws = win.window_start
        # Emitted in key order: row order then depends on the window's contents and
        # not on the accumulators' insertion order, one layer further from a
        # determinism break than the upstream dicts already are (LES-002).
        for ent in sorted(win.nodes):
            node = win.nodes[ent]
            self._write(NODES, _row({"window_start": ws, "entity": ent}, node))
            # Observed in the same sorted pass that writes the row, and only once
            # the row is persisted: the index map then describes exactly the rows
            # in the file, and depends on their content and on nothing else.
            self._vocab.observe(node)
        edges = sorted(win.edges, key=lambda e: (e["src"], e["dst"], e["proto"]))
        for edge in edges:
            self._write(EDGES, _row({"window_start": ws}, edge))
        # `change` and `from_ts` join the sort key (`Q1`/`Q9`): one binding now emits up to three
        # records in a run — `opened`, `closed`, `final` — and two of them can land in the same
        # window when supersession happens promptly, so a key of (kind, ip, bound_to) alone would
        # leave their order to `identity_delta`'s append order. That is deterministic today and is
        # exactly the kind of dependence on an upstream list's order this sort exists to remove
        # (`LES-009` #2).
        bindings = sorted(win.identity_delta,
                          key=lambda b: (b["kind"], b["ip"], b["bound_to"], b["change"],
                                         b["from_ts"]))
        for binding in bindings:
            self._write(IDENTITY, _row({"window_start": ws}, binding))
        # The health row goes out for EVERY window, including one with no entities.
        # A window that saw nothing is itself a finding — a downstream reader must
        # not have to infer it from a gap (§5.4 #5, the asterisk travels). The two
        # row counts are the sink's own bookkeeping, and they let a health row alone
        # say how thin a window was without opening nodes.jsonl or edges.jsonl.
        self._write(WINDOWS, _row(
            {"window_start": ws, "window_seconds": win.window_seconds},
            {**win.health, "edge_rows": len(edges), "identity_rows": len(bindings)}))
        self._events.update(win.health["event_counts"])
        self._windows += 1
        for fh in self._fh.values():
            fh.flush()  # a closed window is on disk before the next one opens

    def close(self) -> dict:
        if self._vocab is None or self._dir is None:
            raise RuntimeError("close before open()")  # fail loud, as write_window does
        for fh in self._fh.values():
            fh.close()
        self._fh = {}
        finished = time.time()
        rows = {name: self._rows[name] for name in (NODES, EDGES, WINDOWS, IDENTITY)}
        # Built ONCE. The run summary is projected from this same dict rather than
        # re-deriving it, so the terminal line cannot disagree with the archive and
        # `_mass()` is not paid for twice per field.
        categorical = self._vocab.manifest_section()
        manifest = {
            **self._meta,
            "build": build_stamp(),
            "windows": self._windows,
            "rows": rows,
            "event_counts": dict(sorted(self._events.items())),
            # The index map for every categorical field, and what it covered.
            # Derived from the rows, so it is reproducible: two runs of the same
            # source must agree here byte for byte, or determinism has broken.
            "categorical": categorical,
            # The entity namespace every `entity`/`src`/`dst` value in this archive is drawn
            # from, and what minted each kind of member (`Q1`, `LES-017`). Deterministic like
            # `categorical` beside it — a pure function of the events — so it needs no
            # carve-out in the determinism gate, and unlike `unparsed_log_types` it is not
            # transport-dependent either: two transports over one capture resolve identically.
            "entities": self._entities,
            WALLCLOCK_FIELD: {"started_at": self._started, "finished_at": finished,
                              "elapsed_seconds": round(finished - self._started, 3)},
        }
        (self._dir / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
        return {"destination": str(self._dir), "windows": self._windows, "rows": rows,
                "vocabulary": coverage_summary(categorical)}

    def note_unparsed_log_types(self, counts: dict[str, int] | None) -> None:
        """Declined by name (code review `S3`), not by omission — the same
        discipline `write_window`'s own `capture_profile`/`assertions` declines
        already use. `manifest.json` IS a real run-scoped artifact (unlike
        `EvidenceSink`, which genuinely has none — `WindowSink
        .note_unparsed_log_types`'s own base docstring), so this is not "nowhere to
        put it"; it is that putting it there would make `manifest.json`'s bytes
        transport-dependent the same way `windows.jsonl`'s own `unparsed_log_types`
        health key already is (`windowing.windows.TRANSPORT_DEPENDENT_HEALTH_FIELDS`)
        — forcing a SECOND gate carve-out (`scripts/verify_pipeline_gates.sh`) for a
        fact `windows.jsonl` already carries once per window. Whether this archive
        wants a run-level summary of it too is a real, open question, left there
        rather than answered here.
        """
        return None

    def note_unrecognized_log_types(self, counts: dict[str, int] | None) -> None:
        """Same decline, same reason — see `note_unparsed_log_types`, immediately
        above (code review `S4`'s additive field, not a separate decision)."""
        return None

    def note_entity_roster(self, roster: dict | None) -> None:
        """ACCEPTED, unlike the two declines above — and the difference is exactly
        the reason those two decline.

        Both of those are TRANSPORT-dependent (`windowing.windows
        .TRANSPORT_DEPENDENT_HEALTH_FIELDS`), so putting either in `manifest.json`
        would force a second carve-out in the transport-equivalence gate for a fact
        `windows.jsonl` already carries per window. The roster is neither: it is a
        pure function of the events, identical across every transport reading one
        capture, and it appears NOWHERE else in this archive. Without it, the four
        mint paths behind every `entity` value in `nodes.jsonl` are unreadable
        (`LES-017`), which is precisely what `manifest.json` is for — read the
        manifest, then stream the rows.
        """
        self._entities = roster

    def abort(self) -> None:
        """Close the row-file handles WITHOUT writing `manifest.json` (`M4`/`M5`).

        `manifest.json`'s sole meaning is "this run finished" (module
        docstring) — writing it here, on a failure surface, would fabricate
        that claim for a directory that a later reader must instead recognize
        as unfinished by the artifact's absence. Safe to call after a partial
        or complete `close()` already ran (idempotent: `close()` already empties
        `self._fh` before it gets to the manifest, so a second round of closes
        here is a no-op) and safe to call before `open()` (still `{}`).
        """
        for fh in self._fh.values():
            fh.close()
        self._fh = {}

    # -- internals --------------------------------------------------------------

    def _write(self, name: str, line: str) -> None:
        self._fh[name].write(line + "\n")
        self._rows[name] += 1
