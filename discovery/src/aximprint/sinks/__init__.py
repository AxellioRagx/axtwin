"""The window-sink registry: `store` and `evidence` live here; `jsonl` does not.

Importing this package registers `store` and `evidence` (both live inside it),
the same way `import aximprint.ingest` registers `zeek` — but `jsonl` is
declared in `aximprint.features.jsonl` (a sibling package, not a submodule of
this one: `WORK_ORDER_egress_sink_unification.md` D1 deliberately keeps the
concrete jsonl writer where its vocabulary machinery already lives, and only
moved the CONTRACT here). `import aximprint.sinks` alone therefore does **not**
make `get_sink("jsonl")` succeed — a caller also needs `import aximprint.features`
(or `.features.jsonl`) somewhere in the process, or it fails loud with `no sink
'jsonl'; available: ['evidence', 'store']`, which is correct (fail loud) but
easy to be surprised by once. The CLI (`cli.py`) imports both; anything else
that wants the full registry must do the same.
"""
from .base import WindowSink, get_sink, register
from . import evidence_sink, store_sink  # noqa: F401  (register the store + evidence sinks)
