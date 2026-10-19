from .vocabulary import (
    MAX_KEYS, NODE_CATEGORICAL, OOV_KEY, WEIGHT_KINDS, CategoricalField,
    CategoricalSpecs, PinnedVocabulary, VocabularyObserver, coverage_summary,
    index_map_digest, load_pinned_vocabulary,
)
from . import jsonl  # noqa: F401  (registers the jsonl sink)

# The sink contract + registry (`WindowSink`, `get_sink`, `register`) moved to
# `aximprint.sinks` (WORK_ORDER_egress_sink_unification.md, D1): it is not a
# `features` concept, and `store`'s own sink needs it without `store` importing
# `features`. `features/base.py` is gone rather than left as a re-export shim —
# LES-001 treats a compatibility shim for an incubating contract as exactly the
# ceremony this project does not pay for. Import `get_sink` / `WindowSink` from
# `aximprint.sinks` directly.
