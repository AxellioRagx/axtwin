"""Reference ingest for the operator assertions file (`docs/WORK_ORDER_reference_ingest.md` D14).

The document itself — schema, validation, merge, canonical re-emit — is
`aximprint/operator_assertions.py`, which sits at the bottom of the package because the cast
compiler needs the same parse (see that module's docstring). This is the thin half: the plugin
registration, and the projection into belief-store rows.

**One thing worth stating here rather than in a commit message.** This door and
`cast --assertions` are not alternatives, and running only one of them is a supported choice
rather than a half-configured pipeline:

  * `cast --assertions` applies the whole document to ONE compile. It is what adds a segment, a
    host or a conduit to a twin, because those are entities the store cannot mint (a belief-store
    fact is *about* a subject; `snapshot()` learns which subjects exist from `node_features`,
    which only a capture writes).
  * `assertions ingest` makes the four facts the store's own readers consume — segment
    membership, role, listen and dial census — survive **re-materialization**, so a fresh PIT
    built from the same store carries the operator's reading with `evidence_class: asserted`
    rather than reverting to the census's.
"""
from __future__ import annotations

from aximprint.ingest.reference.base import ReferencePlugin, ReferenceSnapshot, register
from aximprint.operator_assertions import (
    ASSERTIONS_SCHEMA, ASSERTIONS_EVIDENCE_CLASS, load_assertions, store_rows,
)


@register
class AssertionsPlugin(ReferencePlugin):
    """`axr-assertions/1` -> `asserted` belief-store facts, bounded by the file's `as_of`."""

    name = "assertions"
    document_schema = ASSERTIONS_SCHEMA

    def read(self, source: str) -> ReferenceSnapshot:
        aset = load_assertions(source)
        rows, uncarried = store_rows(aset)
        return ReferenceSnapshot(
            kind=self.name, schema=aset.schema,
            evidence_class=ASSERTIONS_EVIDENCE_CLASS,
            as_of=aset.as_of_epoch, source=aset.source,
            facts=tuple(rows), uncarried=uncarried)
