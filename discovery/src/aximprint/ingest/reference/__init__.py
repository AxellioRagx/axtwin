"""Reference ingest — out-of-band documents, not sensors.

`docs/WORK_ORDER_reference_ingest.md`. Two inputs are planned and one is built: the operator
assertions file (S2, here) and the LDIF directory export (S3, `ldif.py` when it lands). They share
this package because they share a shape — a file dropped beside the imprint, read once, carrying
its own time bound — and a registry, which is separate from the sensor plugins' on purpose (see
`base.py`).
"""
from .base import (  # noqa: F401
    ReferencePlugin, ReferenceSnapshot, get_reference_plugin, reference_plugin_names, register,
)
from . import assertions  # noqa: F401  (registers the assertions reference plugin)
