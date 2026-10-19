"""Categorical vocabulary for the feature archive — the pinned index map (`Q6`).

Three node-row fields are categorical, and they are not the same kind of thing:

  proto_mix     open   keys are Zeek's `service` where set, the transport where
                       not — one map spanning two namespaces, byte-weighted
  listen_ports  open   keys are "<port>/<proto>" labels, discovered from the data
  connect_ports open   same key space as `listen_ports`, but ports DIALLED not answered
  state_mix     closed keys are `FLOW_STATE_CLASSES`, declared in the event model

The open two are why this module exists. Their keys are *data*, so a tensor
assembler could not build its first tensor without a full pass over
`nodes.jsonl` first, and two captures would encode to two different index maps.
This module observes them at the persistence boundary and writes the map into
`manifest.json`, so an archive becomes self-describing enough to encode from:
read the manifest, build the map, then stream the rows. Never the other way round.

**Position in `index` is the tensor column** — that is the whole contract. An
*observed* index is sorted, because sorting is what makes two captures that saw
the same keys agree on the same columns (byte-stability, `LES-009`). Sortedness
is how an observed map is *derived*, not a property a consumer may rely on: a
pinned map may legitimately be append-extended (a new key at the end preserves
every existing column — DESIGN_BRIEF §11's additive change), and a closed table
is emitted in its **declared** order for exactly the same reason. Read the list;
do not re-sort it.

**Open-world by default.** A key seen at encode time that is not in the map is
the normal case, not the error case — `listen_ports` on the reference capture
contains a Windows dynamic RPC port that will differ on the next capture. So an
unknown key gets a reserved column of its own (`OOV_KEY`, index `len(index)`;
the encoded width is therefore `len(index) + 1`) and is **counted**: rows and
mass outside the map, per field, in the manifest. It never renumbers a pinned
column, never silently vanishes, and never appends itself to the map. An archive
encoded against a vocabulary covering 60 % of its own traffic is data with an
asterisk, and the asterisk travels with it (§5.4 #5, `LES-006`).

**Composite keys are surfaced, not split.** Zeek's `service` is a comma-joined
*set*, so `gssapi,smb` and bare `smb` are unrelated columns to a model. The
composite block reports the fragmentation and what splitting on `,` would yield
(`index_atoms_if_split`) — informational only. Splitting changes what a
`proto_mix` *value* means, which is a feature-semantics decision, deliberately
deferred. Every member of that block names its own scope, because the two halves
do not share one: the key lists describe the **index**, the counts describe
everything **observed**, and under a pin they can disagree loudly (an index with
no composite key at all, alongside three quarters of the mass sitting in one).

**Not every key is a label a sensor chose.** `proto_mix` keys come from
`ev.service or ev.proto or "?"` upstream, so the map already mixes L7 analyzer
names with transport names, and two further members can appear that no sensor
ever emitted: `"?"` when both are unset, and `"other"`, the remainder `TopK`
spills once a host exceeds its k. Both would enter the index indistinguishably
from a real service, and `"other"` stands for a *different* set of protocols on
every row carrying it. Neither fired on the reference capture — but this is why
the manifest reports where an index came from rather than only what is in it: a
column has to be interpretable before a model is allowed to use it (§6).

**Cost axis, and the bound (`LES-010`, `MAX_KEYS`).** The observer holds counters
per key per field, so its cost is O(vocabulary) — but "vocabulary" is itself a
function of the rows for the two open fields, which is the trap:

  * `listen_ports` is bounded only in the *limit* — 65,536 ports x {tcp, udp} is
    ~131 k possible keys, and a host cycling dynamic ports mints a new one per
    connection. 131 k columns is not a vocabulary, it is a hash of the capture.
  * `proto_mix` is **genuinely unbounded**: its keys are Zeek's comma-joined
    service *set*, so the key space is the powerset of the analyzer names, not
    the 14 labels the reference capture happened to show.

Pinning does not help. A pin bounds the *index*; every unknown key still earns
its own entry in the out-of-vocabulary tail and in `manifest.json`, the artifact
a consumer is told to read first. So the bound is **enforced here**, the way
this codebase already bounds three other maps (`TopK(16)`, `TOP_PEERS = 64`, the
`__longtail__` spill row): a per-field cap on the observed index and a separate
per-field cap on the itemized out-of-vocabulary tail, each with a spill counter
in the manifest. Truncation is never silent — a capped map reporting full
coverage would be a worse artifact than an uncapped one.

**Hazard: `oov.keys` copies keys verbatim into an archive artifact.** Today all
three fields carry protocol labels and port numbers, which are structural and
reportable (`LES-003`), and the sink's own `.gitignore` covers the file — but the
mechanism is generic, so a future categorical field whose keys are
capture-derived strings (a hostname, a share path, a certificate subject) would
land those strings in `manifest.json` in full. Declaring such a field means
deciding what its OOV tail may disclose, not just what its columns mean.

Import direction (CONVENTIONS §3): `features` reads `windowing` outputs and
`events`. Nothing here imports upward.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from aximprint.events import FLOW_STATE_CLASSES

#: The reserved column every categorical field carries for keys outside its map.
#: Mirrors `__longtail__` in the edge artifact: a sentinel that reads as a
#: sentinel, so an out-of-vocabulary column can never be mistaken for a key.
OOV_KEY = "__oov__"

#: Per-field cap on distinct keys the observer retains — applied independently to
#: the observed index and to the itemized out-of-vocabulary tail, so a field holds
#: at most `2 * MAX_KEYS` keys however many the data contains.
#:
#: 512 is chosen against three numbers rather than for roundness:
#:   * the reference capture's largest categorical vocabulary is **14** keys, so
#:     this is ~36x headroom over the only real distribution measured;
#:   * for `listen_ports`, the part of the key space that recurs across captures
#:     is the registered range — the ephemeral/dynamic ports above it are exactly
#:     the keys a pin could never reuse, so refusing them a column costs a future
#:     encoder nothing it could have used;
#:   * it holds the worst case each map contributes to `manifest.json` to tens of
#:     KiB, against row files measured in MiB. Uncapped, one 5,000-label row
#:     produced a manifest 3.95x the `nodes.jsonl` it described.
#: A capture that legitimately needs more than 512 columns for one field has a
#: feature-design problem, not a cap problem — and the spill counters say so.
MAX_KEYS = 512

#: What a categorical weight *means*. `container` says how to read the value;
#: this says whether the numbers may be compared or summed across fields, which
#: a manifest-only consumer cannot otherwise tell: on the reference capture
#: `state_mix` sums to 1,535,320 (raw flow counts) while `proto_mix` sums to
#: ~1.0 per row (byte proportions), and both declared `container: "dict"`.
WEIGHT_KINDS = (
    "proportion",   # weights are a per-row distribution, ~1.0 per row: do not sum across rows
    "count",        # weights are raw event counts: additive, unnormalized
    "indicator",    # presence only, every key weighs exactly 1.0
)


# -- float discipline -----------------------------------------------------------

class _Sum:
    """Neumaier-compensated float accumulator.

    Plain `+=` over many small weights drifts with *arrival order* — measured at
    200 k rows the same key set summed to `...607.18976` one way and
    `...607.18758` the other, a difference that survives `round(x, 6)`. Neumaier
    carries the lost low-order bits in `c` and folds them back at read time,
    which removes the drift at any scale this sink will see.

    It is a *mitigation*, not a proof: compensated summation is not exactly
    order-independent either. The honest statement of the invariant is in
    `_KeyStats.mass`.
    """

    __slots__ = ("_total", "_c")

    def __init__(self) -> None:
        self._total = 0.0
        self._c = 0.0

    def add(self, x: float) -> None:
        t = self._total + x
        if abs(self._total) >= abs(x):
            self._c += (self._total - t) + x
        else:
            self._c += (x - t) + self._total
        self._total = t

    def value(self) -> float:
        return self._total + self._c


class _KeyStats:
    """Per-key row counts and mass over **at most `cap` distinct keys**.

    Retention is first-arrival: once `cap` keys are held, a key not already held
    finds no slot. Nothing vanishes — its occurrences and its mass move into the
    O(1) `dropped_*` accumulators, which the manifest publishes — it only stops
    being *itemized*. Eviction was rejected deliberately: evicting would make the
    retained set depend on a scoring rule applied to a stream, and a re-arriving
    evicted key could be counted as a new drop, so the spill counter would stop
    meaning anything.

    Which keys survive therefore depends on the order rows arrive in. That order
    is pinned upstream (`sorted(win.nodes)` per window, windows in time order), so
    the archive stays byte-reproducible — but the dependence is real and is why
    truncation is disclosed rather than assumed harmless.
    """

    __slots__ = ("cap", "dropped_occurrences", "_rows", "_mass", "_dropped_mass")

    def __init__(self, cap: int):
        self.cap = cap
        self.dropped_occurrences = 0
        self._rows: Counter = Counter()
        self._mass: dict[str, _Sum] = {}
        self._dropped_mass = _Sum()

    def add(self, key: str, weight: float) -> bool:
        """Credit one occurrence. False means the cap refused the key a slot."""
        acc = self._mass.get(key)
        if acc is None:
            if len(self._mass) >= self.cap:
                self.dropped_occurrences += 1
                self._dropped_mass.add(weight)
                return False
            acc = self._mass[key] = _Sum()
        self._rows[key] += 1
        acc.add(weight)
        return True

    def __len__(self) -> int:
        return len(self._mass)

    def keys(self) -> list[str]:
        return sorted(self._mass)

    def count_of(self, key: str) -> int:
        return self._rows.get(key, 0)

    def row_counts(self) -> dict[str, int]:
        return {k: self._rows[k] for k in sorted(self._rows)}

    def mass(self) -> float:
        """Summed retained weight — over the keys in sorted order, compensated.

        Two disciplines, and only one of them is a guarantee. The *aggregation*
        across keys depends on the key set and never on arrival order, because it
        sorts. The *accumulation* within a key is compensated (`_Sum`) rather than
        order-free: `mass_total` is a property of the row set to the precision
        `round(x, 6)` publishes, and it is exactly reproducible run to run because
        upstream fixes the row order — not because float addition became
        associative.
        """
        acc = _Sum()
        for key in sorted(self._mass):
            acc.add(self._mass[key].value())
        return acc.value()

    def dropped_mass(self) -> float:
        return self._dropped_mass.value()


# -- declarations ---------------------------------------------------------------

@dataclass(frozen=True)
class CategoricalField:
    """One categorical field of a node row, and how to read its value.

    `container` is what the consumer needs to know to encode without guessing:
    a `dict` field carries key -> weight; a `list` field carries labels.

    `weight` is what the consumer needs to know to *use* the number it decoded.
    `container: "dict"` alone cannot distinguish a byte proportion from a raw
    flow count, and the two want opposite treatment downstream — one is already
    normalized per row, the other is not. Declared, never inferred.

    `closed` names the declared members of a *closed* table. A closed field has
    no vocabulary problem — its index comes from this build's code, is identical
    on every capture, and needs no pin. It is here so a consumer learns from the
    manifest which fields are vocabulary-bearing and which are not, instead of
    hardcoding two names.

    `max_keys` is this field's share of the bound (`MAX_KEYS`).
    """

    name: str
    container: str                                  # "dict" | "list"
    weight: str                                     # one of WEIGHT_KINDS
    closed: tuple[str, ...] | None = None           # None => open vocabulary
    composite_separator: str | None = None          # keys are a joined set
    max_keys: int = MAX_KEYS

    def __post_init__(self) -> None:
        # Fail loud at import, not at emit: a field declared with a weight kind
        # nobody reads would ship a manifest a consumer cannot act on.
        if self.container not in ("dict", "list"):
            raise ValueError(f"categorical field {self.name!r}: unknown container "
                             f"{self.container!r}")
        if self.weight not in WEIGHT_KINDS:
            raise ValueError(f"categorical field {self.name!r}: weight must be one of "
                             f"{list(WEIGHT_KINDS)}, got {self.weight!r}")
        if self.closed is not None and len(self.closed) > self.max_keys:
            raise ValueError(f"categorical field {self.name!r}: a closed table of "
                             f"{len(self.closed)} members exceeds its cap ({self.max_keys}) "
                             "— row_counts could not cover its own index")


@dataclass(frozen=True)
class CategoricalSpecs:
    """The categorical fields of ONE artifact, and the artifact they belong to.

    `artifact` travels with the field set rather than being passed in at emit
    time: a caller that could name the artifact independently could pair the node
    fields with `edges.jsonl` and emit a manifest that lies about where its map
    applies. The name is declared here and re-exported by the sink, so there is
    one string, not two that can drift.

    `structural` names the artifact's other dict/list fields — the ones whose
    *shape* could be mistaken for a vocabulary but whose keys are a record shape
    (the per-quantile maps). It exists so the untracked-field guard has a
    baseline of its own: a guard that derived its baseline from the same place as
    the thing it guards could never fire.
    """

    artifact: str
    fields: tuple[CategoricalField, ...]
    structural: frozenset[str] = frozenset()


#: The categorical fields of a node feature row (DESIGN_BRIEF §5.4 #1).
NODE_CATEGORICAL = CategoricalSpecs(
    artifact="nodes.jsonl",
    fields=(
        # Byte proportions from `TopK.normalized()` — ~1.0 per row, already normalized.
        CategoricalField("proto_mix", "dict", "proportion", composite_separator=","),
        # Labels: a port either answered in this window or it did not.
        CategoricalField("listen_ports", "list", "indicator"),
        # The client-side mirror of `listen_ports` (`docs/JOURNAL_imprint_loop.md` GAP-L5):
        # a port this host DIALLED, indicator-coded the same way and drawing on the same
        # `<port>/<proto>` key space, so the two share a vocabulary shape and a pinned map
        # behaves identically across captures.
        #
        # Declared here rather than left to the untracked-field guard — which caught exactly
        # this omission, on the run that introduced the field, precisely as its own comment
        # says it is for.
        CategoricalField("connect_ports", "list", "indicator"),
        # Same shape and semantics one field over: a host either was observed in this
        # segment during the window or it was not (`WORK_ORDER_synth_v1.md` phase 1).
        # Declared rather than left to the untracked-field guard, which is what the guard
        # is FOR — its own comment names this exact case, "a new dict/list-of-str node
        # field written to the artifact and simply absent from the map".
        #
        # Worth knowing before pinning across captures: unlike `listen_ports`, whose key
        # space (`443/tcp`) recurs everywhere, a segment CIDR is specific to one network.
        # Pinning a second capture of the SAME network to the first's map is the intended
        # use and works; pinning a different network's capture yields near-total OOV. That
        # is disclosed by the OOV counter rather than hidden — it is a true statement about
        # two networks not sharing subnets, not a defect in the map.
        CategoricalField("segments", "list", "indicator"),
        # Raw flow counts, NOT normalized: sums to the window's flow total.
        CategoricalField("state_mix", "dict", "count", closed=FLOW_STATE_CLASSES),
    ),
    structural=frozenset({"flow_size_q", "duration_q", "interarrival_q"}),
)


def index_map_digest(indexes: dict[str, list[str] | tuple[str, ...]]) -> str:
    """sha256 of an index map — the columns themselves, and nothing else.

    This is the digest that answers "did these two archives encode to the same
    columns?". Digesting a *file* cannot: the documented pin target is a prior
    `manifest.json`, which carries the wall-clock block, so two archives pinned to
    the identical map reported different file digests. Normalizing to
    `{field: [key, ...]}` with sorted field names makes the digest a function of
    the map and of no other thing the file happened to contain.
    """
    payload = json.dumps({k: list(v) for k, v in indexes.items()}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# -- pinned input ---------------------------------------------------------------

@dataclass(frozen=True)
class PinnedVocabulary:
    """An index map loaded from a prior run, with the provenance to prove it.

    Two digests, because they answer two different questions and one of them was
    silently answering the wrong one:

      `file_digest`  over the pin file's bytes — identifies exactly which *file*
                     was read, and moves when anything in it moves (including the
                     prior run's wall-clock, if the pin was a manifest).
      `map_digest`   over the normalized index map — identifies the *columns*.
                     Two archives that encoded against the same map agree here
                     even when they were pinned to different files.

    Neither reads a clock, so both are reproducible.
    """

    path: str
    file_digest: str
    map_digest: str
    indexes: dict[str, tuple[str, ...]]

    def provenance(self) -> dict:
        return {"path": self.path,
                "file_sha256": self.file_digest,
                "map_sha256": self.map_digest,
                "fields": sorted(self.indexes)}


def load_pinned_vocabulary(path: str | Path,
                           specs: CategoricalSpecs = NODE_CATEGORICAL,
                           ) -> PinnedVocabulary:
    """Load and validate a pin. Every rejection is loud; nothing defaults through.

    Accepts either shape, so `manifest.json` from a prior run works as-is and so
    does the `categorical` block lifted out of one into a file of its own:

      {"categorical": {"fields": {...}}}   a prior manifest
      {"fields": {...}}                    a bare vocabulary document

    A pin naming a field this build does not know is rejected rather than
    ignored — an ignored entry would mean the archive claims to be pinned to a
    map it only partly used. A pin carrying a *closed* field is verified against
    this build's declared table rather than allowed to redefine it: a mismatch
    means the two builds disagree about a closed vocabulary, which is a real
    finding, not a merge. A pin longer than the field's cap is rejected rather
    than truncated: silently encoding against a prefix of the agreed map is the
    one failure the whole module exists to prevent.
    """
    p = Path(path)
    try:
        raw_bytes = p.read_bytes()
    except OSError as exc:
        raise ValueError(f"vocabulary pin unreadable: {p}: {exc}") from None
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"vocabulary pin is not valid JSON: {p}: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError(f"vocabulary pin must be a JSON object: {p}")

    doc = raw.get("categorical", raw)
    if not isinstance(doc, dict) or not isinstance(doc.get("fields"), dict):
        raise ValueError(
            f"vocabulary pin has no index map: {p} — expected a manifest with a "
            "'categorical' object, or a document with a 'fields' object")
    entries: dict = doc["fields"]

    known = {s.name: s for s in specs.fields}
    unknown = sorted(set(entries) - set(known))
    if unknown:
        raise ValueError(f"vocabulary pin names unknown categorical field(s): {unknown}; "
                         f"this build knows: {sorted(known)}")

    indexes: dict[str, tuple[str, ...]] = {}
    for name in sorted(entries):
        entry = entries[name]
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), list):
            raise ValueError(f"vocabulary pin field {name!r} has no 'index' list: {p}")
        index = entry["index"]
        if not all(isinstance(k, str) for k in index):
            raise ValueError(f"vocabulary pin field {name!r}: every index entry must be a string")
        if len(set(index)) != len(index):
            dupes = sorted({k for k in index if index.count(k) > 1})
            raise ValueError(f"vocabulary pin field {name!r} has duplicate index entries: "
                             f"{dupes} — the column for a key would be ambiguous")
        if OOV_KEY in index:
            raise ValueError(f"vocabulary pin field {name!r} contains the reserved "
                             f"out-of-vocabulary key {OOV_KEY!r}")
        spec = known[name]
        if len(index) > spec.max_keys:
            raise ValueError(f"vocabulary pin field {name!r} has {len(index)} index entries, "
                             f"over this build's cap of {spec.max_keys} — a pin cannot be "
                             "honored in part, and truncating one would silently encode "
                             "against a different map than it names")
        if spec.closed is not None and tuple(index) != spec.closed:
            raise ValueError(
                f"vocabulary pin field {name!r} is a closed table and disagrees with this "
                f"build's declared members ({len(spec.closed)} entries) — a closed vocabulary "
                "cannot be redefined by a pin")
        indexes[name] = tuple(index)

    return PinnedVocabulary(path=str(path),
                            file_digest=hashlib.sha256(raw_bytes).hexdigest(),
                            map_digest=index_map_digest(indexes),
                            indexes=indexes)


# -- observation ----------------------------------------------------------------

def _looks_categorical(value) -> bool:
    """Would this row value be readable as a vocabulary-bearing field?

    Deliberately shape-only, because that is all a guard against an *undeclared*
    field can use. An empty container is unclassifiable and is not flagged.
    """
    if isinstance(value, dict):
        return bool(value) and all(isinstance(k, str) for k in value) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(isinstance(k, str) for k in value)
    return False


class _FieldObserver:
    """Per-field accumulator. Bounded: at most `2 * spec.max_keys` keys retained."""

    def __init__(self, spec: CategoricalField, pinned: tuple[str, ...] | None):
        self.spec = spec
        # A closed table is its own map; a pin may not override it (checked at load).
        self.pinned = None if spec.closed is not None else pinned
        # Membership set. None => open and unpinned, i.e. every key is in-map by
        # construction and the index is whatever this run observed, up to the cap.
        self._allowed: frozenset[str] | None = (
            frozenset(spec.closed) if spec.closed is not None
            else frozenset(pinned) if pinned is not None else None)
        self.rows_total = 0
        self.rows_absent = 0      # the field was not on the row at all
        self.rows_empty = 0       # present with no keys — "evidence of none"
        self.rows_present = 0     # present with >= 1 key
        self.rows_uncovered = 0   # present, and >= 1 key found no column
        self.oov_rows = 0         # present, and >= 1 key was outside the map
        self.truncated_rows = 0   # present, and >= 1 key was refused by the cap
        self.composite_rows = 0
        # The two bounded maps. `in_map` itemizes keys that found a column;
        # `oov` itemizes keys outside the map while there is room under its own cap.
        self.in_map = _KeyStats(spec.max_keys)
        self.oov = _KeyStats(spec.max_keys)
        # Per-composite-key totals were never emitted, only their sum — so a
        # scalar, not a Counter. One fewer map whose key space is the data's.
        self._composite_mass = _Sum()

    def observe(self, row: dict) -> None:
        name = self.spec.name
        self.rows_total += 1
        if name not in row:
            self.rows_absent += 1
            return
        # NOT sorted. Every mutation below is per-key, so cross-key order cannot
        # reach a count; the float sums that used to justify a sort here are now
        # compensated and aggregated in sorted order at read time (`_KeyStats.mass`).
        # The sort cost O(k log k) per field per row and bought nothing.
        pairs = self._pairs(row[name])
        if not pairs:
            self.rows_empty += 1
            return
        self.rows_present += 1
        sep = self.spec.composite_separator
        saw_oov = saw_truncated = saw_composite = False
        for key, weight in pairs:
            if sep is not None and sep in key:
                saw_composite = True
                self._composite_mass.add(weight)
            if self._allowed is None or key in self._allowed:
                # In-map by membership, or by construction when open and unpinned.
                # The cap can still refuse it a column: that is a *truncation*, a
                # different finding from an out-of-vocabulary key, and counted as one.
                if not self.in_map.add(key, weight):
                    saw_truncated = True
            else:
                saw_oov = True
                # Its own cap. Whether or not the key gets itemized, its mass is
                # retained (here or in the spill counter), so `oov.mass` is exact.
                self.oov.add(key, weight)
        # Once per row, however many of its keys were unknown: "rows outside the
        # map" answers a different question from "occurrences outside the map".
        if saw_oov:
            self.oov_rows += 1
        if saw_truncated:
            self.truncated_rows += 1
        if saw_oov or saw_truncated:
            self.rows_uncovered += 1
        if saw_composite:
            self.composite_rows += 1

    def _pairs(self, value) -> list[tuple[str, float]]:
        """(key, weight) for one row's value. An unrecognized shape is rejected."""
        name = self.spec.name
        if self.spec.container == "dict":
            if not isinstance(value, dict):
                raise ValueError(f"categorical field {name!r} must be an object, "
                                 f"got {type(value).__name__}")
            out = []
            for k, w in value.items():
                if not isinstance(k, str):
                    raise ValueError(f"categorical field {name!r} has a non-string key: {k!r}")
                if isinstance(w, bool) or not isinstance(w, (int, float)):
                    raise ValueError(f"categorical field {name!r} key {k!r} has a "
                                     f"non-numeric weight: {w!r}")
                if k == OOV_KEY:
                    raise ValueError(f"categorical field {name!r} contains the reserved "
                                     f"out-of-vocabulary key {OOV_KEY!r}")
                out.append((k, float(w)))
            return out
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"categorical field {name!r} must be an array, "
                             f"got {type(value).__name__}")
        for k in value:
            if not isinstance(k, str):
                raise ValueError(f"categorical field {name!r} has a non-string entry: {k!r}")
            if k == OOV_KEY:
                raise ValueError(f"categorical field {name!r} contains the reserved "
                                 f"out-of-vocabulary key {OOV_KEY!r}")
        if len(set(value)) != len(value):
            # A label list is a set of labels. A repeat is ambiguous — two
            # observations, or an upstream bug? — and it would inflate the key's
            # row count, so it is rejected rather than interpreted.
            raise ValueError(f"categorical field {name!r} has duplicate entries: "
                             f"{sorted({k for k in value if value.count(k) > 1})}")
        return [(k, 1.0) for k in value]

    # -- emit -------------------------------------------------------------------

    def entry(self) -> dict:
        """This field's manifest block: the index map plus what it did and did not cover."""
        if self.spec.closed is not None:
            index, kind = list(self.spec.closed), "closed"
        elif self.pinned is not None:
            index, kind = list(self.pinned), "pinned"
        else:
            index, kind = self.in_map.keys(), "observed"

        in_mass = self.in_map.mass()                 # landed in a real column
        cut_mass = self.in_map.dropped_mass()        # would have, but the cap was full
        oov_mass = self.oov.mass() + self.oov.dropped_mass()   # outside the map
        total_mass = in_mass + cut_mass + oov_mass
        return {
            "container": self.spec.container,
            # What the weights MEAN — see WEIGHT_KINDS. Two dict fields on the same
            # row can be a proportion and a raw count, and nothing else says so.
            "weight": self.spec.weight,
            # Where this index came from, never inferred: "observed" from this
            # run's rows, "pinned" from a supplied map, "closed" from the code.
            "vocabulary": kind,
            "index": index,
            "oov_key": OOV_KEY,
            "oov_index": len(index),
            "rows_total": self.rows_total,
            "rows_present": self.rows_present,
            "rows_empty": self.rows_empty,
            "rows_absent": self.rows_absent,
            # Zero-filled over the whole index: a pinned or declared key this run
            # never saw is a finding, and a gap is not the same as an absence.
            "row_counts": {k: self.in_map.count_of(k) for k in index},
            "mass_total": round(total_mass, 6),
            # The headline honesty number: the fraction of this run's MASS that
            # landed inside the index. Exactly complementary — `1 - coverage` is
            # the fraction a consumer will find in the reserved column, whether it
            # got there by being unknown to a pin or by being refused by the cap.
            # None, not 1.0, when there was no mass to cover — "no evidence" is not
            # "evidence of none" (LES-006).
            "coverage": round(in_mass / total_mass, 6) if total_mass else None,
            # The same question counted in rows, and it is not redundant: a key
            # weighing 0.0 leaves `coverage` None while rows really did spill.
            # Read as a pair, the two are unambiguous — mass-null means "no mass to
            # weigh", rows-null means "no rows to count", and they cannot both be
            # null while anything was observed.
            "coverage_rows": (round((self.rows_present - self.rows_uncovered)
                                    / self.rows_present, 6)
                              if self.rows_present else None),
            "oov": {
                "rows": self.oov_rows,
                "mass": round(oov_mass, 6),
                # Itemized while there was room under the cap. The remainder is in
                # `oov_keys_truncated` — `mass` above stays exact either way.
                "keys": self.oov.row_counts(),
            },
            # The two spill counters. Both are zero on an untruncated run, and a
            # non-zero one is the disclosure that makes a capped map honest.
            "oov_keys_truncated": {
                "cap": self.oov.cap,
                "occurrences": self.oov.dropped_occurrences,
                "mass": round(self.oov.dropped_mass(), 6),
            },
            "vocabulary_truncated": {
                "cap": self.in_map.cap,
                "rows": self.truncated_rows,
                "occurrences": self.in_map.dropped_occurrences,
                "mass": round(cut_mass, 6),
            },
            "composite": self._composite(index, total_mass),
        }

    def _composite(self, index: list[str], total_mass: float) -> dict | None:
        """Fragmentation report for a field whose keys are a joined set.

        Every member names its scope, because the block has two. `index_*`
        describes the **index**; `observed_*` describes everything seen, in-map or
        not, because "how much traffic sits in composite keys" is a question about
        the data rather than about the map. Under a pin they legitimately
        disagree, and the emitted names say so rather than a docstring nobody
        downstream reads. Reporting only — nothing here splits anything.
        """
        sep = self.spec.composite_separator
        if sep is None:
            return None
        mass = self._composite_mass.value()
        return {
            "separator": sep,
            "index_keys": [k for k in index if sep in k],
            "index_atoms_if_split": sorted({a for k in index for a in k.split(sep)}),
            "observed_rows": self.composite_rows,
            "observed_mass": round(mass, 6),
            "observed_mass_fraction": round(mass / total_mass, 6) if total_mass else None,
        }


class VocabularyObserver:
    """Builds the categorical section of a manifest from the rows a sink writes.

    A sink hands it every node row as it is persisted and asks for the section at
    close. Run-level by design — the index map spans the whole archive — and
    **bounded**: at most `2 * max_keys` keys per declared field, plus one entry
    per undeclared row field name. Nothing per-row or per-window is retained.
    """

    def __init__(self, specs: CategoricalSpecs = NODE_CATEGORICAL,
                 pinned: PinnedVocabulary | None = None):
        self._specs = specs
        self._pinned = pinned
        self._fields = {
            spec.name: _FieldObserver(
                spec, pinned.indexes.get(spec.name) if pinned is not None else None)
            for spec in specs.fields}
        # Row fields that read as categorical but that no spec declares. The key
        # space is the row's RECORD SHAPE (the writer's code), not the data, so it
        # is bounded by construction — capped anyway, and the cap discloses itself,
        # because "bounded by construction" is exactly what this module used to
        # believe about its own vocabulary.
        self._untracked = _KeyStats(MAX_KEYS)
        self._declared = frozenset(self._fields) | specs.structural

    def observe(self, row: dict) -> None:
        for observer in self._fields.values():
            observer.observe(row)
        # `NODE_CATEGORICAL` duplicates knowledge `windowing` owns, and nothing
        # reconciled the two: a new dict/list-of-str node field was written to the
        # artifact and was simply absent from the map, with no counter and no
        # warning. Counted here in the style of `unmodelled_health_kinds` — the
        # layer names what it models and discloses what it does not.
        for name, value in row.items():
            if name not in self._declared and _looks_categorical(value):
                self._untracked.add(name, 1.0)

    def manifest_section(self) -> dict:
        """The `categorical` block: which fields are vocabulary-bearing, and their maps.

        The artifact these fields live in comes from the spec set, so a consumer
        reading the manifest alone knows where to apply the map and no caller can
        pair one artifact's fields with another artifact's name.
        """
        fields = {name: self._fields[name].entry() for name in sorted(self._fields)}
        return {
            "artifact": self._specs.artifact,
            # The columns, digested. `pinned_to.map_sha256` answers "was this
            # pinned to that map?"; this answers "did these two archives, pinned
            # or not, encode to the same columns?" — which is the question a
            # file digest could never answer.
            "map_sha256": index_map_digest({n: e["index"] for n, e in fields.items()}),
            "pinned_to": self._pinned.provenance() if self._pinned is not None else None,
            "untracked_categorical_fields": {
                "fields": self._untracked.row_counts(),
                "cap": self._untracked.cap,
                "names_dropped": self._untracked.dropped_occurrences,
            },
            "fields": fields,
        }


def coverage_summary(section: dict) -> dict:
    """Compact per-field coverage for a run summary, **projected from the manifest block**.

    Takes the emitted section rather than re-deriving from the observer, so the
    terminal line and the archive cannot disagree — and so `close()` builds the
    section once instead of rebuilding every dict a second time.
    """
    out = {}
    for name in sorted(section["fields"]):
        entry = section["fields"][name]
        out[name] = {"vocabulary": entry["vocabulary"],
                     "keys": len(entry["index"]),
                     "oov_rows": entry["oov"]["rows"],
                     "coverage": entry["coverage"],
                     "truncated_keys": (entry["vocabulary_truncated"]["occurrences"]
                                        + entry["oov_keys_truncated"]["occurrences"])}
    return out
