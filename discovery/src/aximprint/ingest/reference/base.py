"""The REFERENCE-ingest plugin contract + registry (INCUBATING).

`docs/WORK_ORDER_reference_ingest.md` D14. A second plugin kind beside `ingest/base.py`'s sensor
plugins, and the differences are the whole reason it is a separate contract rather than a
subclass:

  * **Non-sensor.** A sensor plugin adapts one sensor's output to the normalized EVENT model and
    yields a roughly time-ordered stream (`IngestPlugin.events`). A reference plugin reads an
    out-of-band document — an operator's assertions file today, an LDIF directory export at S3 —
    and produces FACTS directly. There is no window to close, no locality verdict to reach, no
    health to report; running one through `WindowManager` would be inventing a capture.
  * **Snapshot-shaped.** A capture is an interval; a reference document is an instant. Its whole
    content carries ONE time bound, the document's own `as_of`, which becomes the `window_start`
    of every row it writes. That is what makes two imports of different ages legible as
    incomparable rather than silently blended (`docs/WORK_ORDER_reference_ingest.md` §6).
  * **A different registry, deliberately.** `ingest/base.py::_REGISTRY` answers "which sensors can
    this build read", and it is what `aximprint --help`, `--plugin` and the doc-stats `plugins`
    counter all mean. A reference reader appearing there would inflate that answer with something
    that has never seen a packet.

**No credential, no bind, no network reach** — from this contract or any implementation of it
(§6). `read()` takes a local path and nothing else; a live LDAP bind, if it is ever built, feeds a
parser rather than becoming one.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field as _field

_REFERENCE_REGISTRY: dict[str, type["ReferencePlugin"]] = {}


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    """One reference document, read into the store's uniform fact shape.

    `facts` are belief-store `assertions` rows — `(window_start, subject, predicate, object,
    evidence_class, confidence, source)` — which is the whole reason D14 needed no store column:
    every learner in this project already writes that shape, and a reference channel that needed
    its own table would be claiming its facts are a different KIND of fact rather than a different
    CLASS of evidence.

    `uncarried` counts what the document said that has no store predicate to say it with, per
    record kind. **Present rather than implied**: a reader comparing the document's record count
    against the row count would otherwise conclude the ingest lost records, when what actually
    happened is that some facts have no consumer in the belief store at all and live in the
    document their compiler reads (`operator_assertions.STORE_PREDICATES`). Absent evidence and
    an unrepresentable fact are different claims (`LES-006`).
    """

    #: The plugin that produced it (`"assertions"`), for messages and for the CLI's summary.
    kind: str
    #: The DOCUMENT's own schema string, e.g. `axr-assertions/1` — not this project's.
    schema: str
    #: The ONE evidence class every fact in this document carries (`ir/schema.py::
    #: EVIDENCE_CLASSES`). A property of the CHANNEL, not of a row: an operator's assertions file
    #: is `asserted` throughout and a directory export will be `imported` throughout, which is
    #: exactly how axRange `docs/design/14-operator-assertions.md` §2's table reads — one class
    #: per door. Carried on the snapshot rather than only on each row so a document with NO
    #: carryable facts still says which class's prior snapshot it replaces
    #: (`BeliefStore.write_reference_snapshot`), which a scan of zero rows could not.
    evidence_class: str
    #: Epoch seconds. The time bound every row carries.
    as_of: float
    #: `"operator:<name>"`, an export's id — who says so.
    source: str
    facts: tuple[dict, ...] = ()
    uncarried: dict[str, int] = _field(default_factory=dict)


def register(cls: type["ReferencePlugin"]) -> type["ReferencePlugin"]:
    if not cls.name:
        raise ValueError("reference plugin must set a name")
    if cls.name in _REFERENCE_REGISTRY:
        raise ValueError(f"duplicate reference plugin name: {cls.name}")   # fail loud
    _REFERENCE_REGISTRY[cls.name] = cls
    return cls


def get_reference_plugin(name: str) -> "ReferencePlugin":
    try:
        return _REFERENCE_REGISTRY[name]()
    except KeyError:
        raise KeyError(f"no reference-ingest plugin '{name}'; available: "
                       f"{sorted(_REFERENCE_REGISTRY)}") from None


def reference_plugin_names() -> list[str]:
    return sorted(_REFERENCE_REGISTRY)


class ReferencePlugin(ABC):
    """Contract: read one out-of-band reference document into facts.

    Deliberately narrow. There is no `probe_vantage` (a document has no tap), no
    `unparsed_stems` (there are no stems), and no slice arguments (a snapshot is one instant, so
    bounding it by time would either be a no-op or a lie).
    """

    name: str = ""

    #: The document schema this plugin reads, for a caller that wants to say what it is about to
    #: parse before parsing it.
    document_schema: str = ""

    @abstractmethod
    def read(self, source: str) -> ReferenceSnapshot:
        """Parse `source` (a local path) into a `ReferenceSnapshot`.

        **Refuses loudly on a shape it does not recognize** — it never returns a partial snapshot
        with the unreadable records dropped. A half-applied reference document produces a twin the
        operator did not author and cannot tell apart from one they did, which is the failure the
        evidence model exists to prevent, arriving through the door meant to fix it.
        """
