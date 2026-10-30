"""SIEM ingest plugins — `ingest/siem/SPEC.md` is the design of record.

`http.py` is the scaffolding every `http` pull plugin shares (lifted from
`ingest/axquery/sources.py`, which now imports it back), `rows.py` the row semantics the
vendors share, and one module per vendor registers its plugin on import. `http` is imported
first on purpose: a vendor module needs it, and `axquery/sources.py` reaches it through
this package, so the order here is the import order.
"""
from . import (
    http,  # noqa: F401
    rows,  # noqa: F401
)
from .elastic import ElasticPlugin
from .splunk import SplunkPlugin

__all__ = ["ElasticPlugin", "SplunkPlugin"]
