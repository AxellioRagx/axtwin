"""Ingest plugin contract + registry (INCUBATING).

A plugin adapts ONE sensor's output to the normalized event model. Plugins are thin,
stateless, and lossy by design: they map what the sensor provides and drop the rest.
Enriching what flows downstream is an event-model change, not a plugin hack (brief §3).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from aximprint.events import Event
from aximprint.vantage import Vantage

_REGISTRY: dict[str, type["IngestPlugin"]] = {}


def register(cls: type["IngestPlugin"]) -> type["IngestPlugin"]:
    if not cls.name:
        raise ValueError("plugin must set a name")
    if cls.name in _REGISTRY:
        raise ValueError(f"duplicate plugin name: {cls.name}")  # fail loud
    _REGISTRY[cls.name] = cls
    return cls


def get_plugin(name: str) -> "IngestPlugin":
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyError(f"no ingest plugin '{name}'; available: {sorted(_REGISTRY)}") from None


def list_plugins() -> list[dict]:
    """Every registered plugin with its declared run-config schema, name-sorted.

    The one listing the control-plane API (`axrange` `GET /api/discovery/plugins`)
    reads, through `aximprint plugins`. An empty registry lists as `[]`, not as an
    error: "no plugin installed" is a real state of a stripped install.
    """
    return [{"name": name, "config_schema": cls.config_schema()}
            for name, cls in sorted(_REGISTRY.items())]


#: The run options every plugin's `ingest` accepts, mirroring the `aximprint ingest`
#: flags that are not plugin-specific. Spelled once so the CLI, the schema a plugin
#: declares and the API's refusal name the same keys.
RUN_OPTION_SCHEMA: dict = {
    "ts_from": {"type": "number", "description": "epoch seconds, inclusive; needs a "
                                                 "source with a partition index"},
    "ts_to": {"type": "number", "description": "epoch seconds, exclusive; needs a "
                                               "source with a partition index"},
    "vantage": {"type": "string", "description": "where the tap sat, for a capture "
                                                 "with no harvest manifest"},
    "network": {"type": "string", "description": "which network the capture is OF "
                                                 "(operator-declared)"},
    "window": {"type": "integer", "description": "window seconds (default 3600)"},
    "subbuckets": {"type": "integer", "description": "sub-buckets per window "
                                                     "(default 12)"},
}


class IngestPlugin(ABC):
    """Contract: parse a sensor source and yield normalized events, roughly time-ordered."""

    name: str = ""

    #: What this plugin's `source` locator IS, in one line -- the only plugin-specific
    #: part of the run config a caller has to get right. Published through
    #: `config_schema()`; a plugin that leaves it empty publishes the contract's generic
    #: wording, which is honest but unhelpful, so every shipped plugin sets it.
    source_help: str = ""

    @classmethod
    def config_schema(cls) -> dict:
        """The JSON-schema-shaped description of ONE run's config for this plugin:
        `source` (required, plugin-defined locator) plus the shared run options
        (`RUN_OPTION_SCHEMA`). Closed (`additionalProperties: false`) so a caller
        passing a key no run honors is refused by name rather than silently ignored
        (CONVENTIONS §3)."""
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": cls.source_help
                    or "plugin source (a directory, file, or plugin-defined locator)",
                },
                **{k: dict(v) for k, v in RUN_OPTION_SCHEMA.items()},
            },
            "required": ["source"],
            "additionalProperties": False,
        }

    #: This run's source-level "present but not parsed" inventory: name -> count, in
    #: whatever unit the transport counts (a zeek stem -> file/partition count, e.g.).
    #: `None` is the honest default and stays `None` forever on a plugin with nothing
    #: analogous to report -- **not** the same finding as `{}`, which means a plugin
    #: DID look and found every stem accounted for (`docs/WORK_ORDER_zeek_stem_coverage.md`
    #: D5, `LES-006`: "no evidence" and "evidence of none" are different claims, and a
    #: field that cannot say "I did not look" reproduces the D5 provenance gap in a
    #: quieter form). For a plugin that DOES track this (`ingest/zeek/plugin.py`), the
    #: value is discovered LAZILY, no earlier than the first event pulled from its own
    #: `events()` (see that module's docstring on `events`) -- so a caller must not read
    #: this before iterating `events()` at least once; `cli._pipeline` is the one place
    #: that reads it, and only after the driver's loop has done so.
    unparsed_stems: dict[str, int] | None = None

    #: The SUBSET of `unparsed_stems` this plugin declined for no NAMED reason —
    #: mirrors `unparsed_stems` in every way (same lazy discovery, same `None`-means-
    #: unaudited discipline) and exists BESIDE it, not instead of it (code review `S4`,
    #: additive to `docs/WORK_ORDER_zeek_stem_coverage.md` D5, no reversal). D5's own
    #: merged field is right for the store's honesty claim (every one of these stems
    #: really did go unparsed) but loses a distinction nothing downstream can recover:
    #: a stem this build DECLINED BY NAME (`ingest/zeek/plugin.py`'s `_SKIP`) is a
    #: closed, reasoned decision, while one it has never heard of at all is an open
    #: coverage gap — and `_SKIP` lives in `ingest`, which every store consumer is
    #: forbidden to import (CONVENTIONS §3), so a reader of the STORE alone could not
    #: recompute this split even if the reason stayed stable across builds (it does
    #: not: `_SKIP` is THIS BUILD's table, while the store is read as-of-T across many
    #: builds, so a stem skipped today and parsed tomorrow would re-read history
    #: wrong if the split were derived at READ time instead of recorded at write time).
    unrecognized_stems: dict[str, int] | None = None

    def probe_vantage(self, source: str) -> Optional[Vantage]:
        """Where the tap sat, read from `source` **without pulling a single event**.

        `docs/WORK_ORDER_visibility_and_vantage.md` D2 tier 1. Unlike `unparsed_stems` above —
        discovered lazily, and only final once `events()` has been iterated — this is answerable
        before the run starts, and that difference is the whole reason it is a separate method
        rather than another lazily-set attribute. A vantage that disagreed with the operator's
        `--vantage` would otherwise surface at the END of a run, after every window was already
        written, and refusing there would throw away the run (an interrupted ingest leaves no
        `runs` row, which `materialize` then refuses outright). Probing costs one small file read.

        **`None` means "this plugin's sensor records no vantage", which is the honest default** and
        what every plugin inherits until it has something to report — never a fabricated identity.
        Two different captures that both answered "unknown" must not compare equal, so the caller
        treats `None` as *unknown*, not as a value (`LES-006`).
        """
        return None

    @abstractmethod
    def events(self, source: str, ts_from: float | None = None,
              ts_to: float | None = None) -> Iterator[Event]:
        """Yield events from `source` (a directory, file, or plugin-defined locator).

        `ts_from`/`ts_to` bound a half-open `[ts_from, ts_to)` slice in epoch
        seconds, `None` meaning unbounded on that side — matching `cli._slice`,
        which validates the range before any plugin sees it. Widened from
        `events(self, source)` (`M7`) to match `cli._pipeline`, which always
        calls with all three positional arguments: `zeek.plugin.ZeekPlugin`
        already carried this signature, so the CONTRACT, not the one
        implementation, had drifted (`LES-005` — a signature change on one
        side of a boundary is a change on both).

        **A plugin that cannot honor a slice must REFUSE it** — raise, naming why —
        never accept and ignore it. This line said the opposite ("may accept and ignore
        both and read all of `source`") until a code review caught the contradiction:
        both implementations already refuse (`ingest/zeek/sources.py::open_source` for a
        log directory, `ingest/suricata/plugin.py::events` for a bare eve.json), and
        they are right — CONVENTIONS §3 is "fail loud, fail closed", and a run that
        silently reads a whole capture after being asked for one hour of it reports
        windows outside the requested range while every artifact it writes claims the
        bound was applied. The permissive reading was the CONTRACT drifting from the
        code, the same `LES-005` shape the paragraph above records for the signature.
        A transport WITH a partition index (`ParquetArchiveSource`) honors the slice;
        one without refuses it. There is no third option.
        """
