"""Imprint (aximprint-integration Track C) fixture reader.

`docs/WORK_ORDER_aximprint_console.md` / `docs/design/aximprint-integration/
WORK_ORDERS.md`: a read-only belief-store/provenance surface over a
**checked-in FIXTURE imprint**, standing in for aximprint's real compiled
output until Track A's live round-trip lands. Mirrors
`axrange.twinconfig.registry`'s directory-scan idiom (LES-025 -- reuse,
don't reimplement) but for a NEW, smaller catalog: `axrange/api/fixtures/
imprints/<id>/`, one subdirectory per imprint.

Two files are the ones THIS module reads (a third-party cast bundle -- a
legacy `<root>/<id>/` or a `twins/<id>/` folder, which `AXRANGE_IMPRINTS_DIR`
may point at instead -- carries more -- a topology export, the operator's
`<id>.assertions.yaml`/`layout.json`, a `.history/` of apply snapshots --
each owned by its own reader: `topology_export.py`, `console_files.py`,
`recompile.py`):

  * `<id>.yaml`             -- a genuine axRange `twin_config` document
                               (same `{twin_config: {...}}` shape/vocabulary
                               `axrange.twinconfig.schema.TwinConfigFile`
                               already validates; parsed with the SAME model,
                               not a hand-rolled dict walk).
  * `<id>.provenance.json`  -- the per-field provenance SIDECAR (`twin_config`
                               is `extra="forbid"`, so provenance cannot live
                               inline in the YAML it describes -- matches the
                               same provenance-sidecar reasoning aximprint's
                               own compiled output uses).

In a `twins/<id>/` folder (`AXRANGE_IMPRINTS_DIR=twins`; `docs/
WORK_ORDER_twin_consolidation.md`) the same two files are the manifest's te
file (`te.yaml`) and `generated/<id>.provenance.json` -- ONE layout rule,
`axrange.twinconfig.bundle`, shared with every sibling reader and writer.

**Coupling rule (hard constraint):** this module reads only those two
artifact FILES. It never imports anything from the `aximprint` package and
never adds a dependency on it -- axRange's engine stays a one-way consumer
of aximprint's *output shape*, never its code (`docs/design/
aximprint-integration/WORK_ORDERS.md` §0).

**The directory name is the CANONICAL id** (2026-08-25 review fix M2), not
`twin_config.id` and not any id baked into the bundle's other artifacts --
`list_imprints`/`load_imprint_summary` always publish the name
`_imprint_dir` resolved by, so `GET /api/imprints/{id}` round-trips against
whatever `GET /api/imprints` just listed.

**Exactly ONE id addresses a bundle** (LES-222; GAP-082, `docs/
WORK_ORDER_console_level0_rendering.md` R7, decision D3 "tolerate on read",
Authority: user 2026-09-11). Every other id-shaped field is classified by ONE
question -- *can any producer legitimately write a value different from the
addressing id?*:

  * the directory name -- IS the addressing id.
  * `twin_config.id` -- no -> REFUSE (`_check_addressing_id`). The file read
    is `<dir>/<dir>.yaml`, and the id it declares is what "deploy as twin"
    deploys under and what the fidelity reader resolves by, so a
    disagreement would serve one twin's content under another's URL
    (LES-195).
  * a NORMALIZED sidecar's `imprint_id` -- no: it names the imprint whose
    rows it carries, and only a copy-paste writes it differently (LES-195's
    own case) -> REFUSE a different value (`_sidecar_id_check`, restored by
    the GAP-082 review, S1). A MISSING one contradicts nothing -> WARN.
  * a CANONICAL sidecar -- declares no imprint id; nothing to compare.
  * the topology export's `imprint_id` -- YES: a cast bundle's export bakes
    the OBSERVED capture's id (`pit-395a61f04e-1780405200` is served while
    its export says `corpapcn-disco`), the argument `o2/console/src/model/
    graph.ts` `CastInput.imprintId` makes for the SPA (LES-263) -> WARN,
    never refuse (`_export_id_warnings`).
  * `pit_id` (export or sidecar) -- YES, it changes every capture ->
    published, never compared.

**One addressing guard, on every route** (GAP-082 review S2/C, round 2 SF4).
`_addressable_bundle` resolves the directory (the traversal guard: a 404),
parses its `<id>.yaml` and compares `twin_config.id` with the directory name
(a 400) -- and nothing else: no sidecar, no export. `check_imprint_addressable`
is its public face and every route runs it: the Console's operator routes
(`service._imprint_bundle_dir`), `/topology`'s imprint branch, the "deploy as
twin" pre-check, and `/cast` and `/provenance` through `load_reference`; the
summary and the listing run the same two steps inside `_summary_from`. So a
bundle one route refuses, every route refuses, and a deploy never depends on
the health of a sidecar or an export it does not read. The parse behind it is
CACHED per file (`_load_twin_config`), so after the first request the guard
costs a `stat`.

History, as measured: until 2026-09-11 both baked ids were cross-checked and
REFUSED, so `GET /api/imprints/{id}` answered 400 for every real cast bundle
-- and "deploy as twin" with it, since its pre-check ran the summary. R7
lifted that refusal and exposed the next one: every real cast bundle then
failed at PLAN BUILD, because its relative `scripts/...` installs resolved
against the tempdir the twin config had been materialized into.
`axrange.twinconfig.registry.twin_config_source_dir` fixes that for the CLI
and both API resolvers alike (GAP-082 review, M2); the plan preview of every
real cast bundle is pinned by `tests/test_api_console.py`.

**TWO sidecar shapes are read, and which one a bundle carries is DETECTED,
never guessed** (`docs/WORK_ORDER_console_canvas.md` S1.4; the adapter this
docstring used to call "future work" landed 2026-09-05):

  * `axr-cast-provenance/1` -- the CANONICAL shape the real compiler emits
    (`{schema, pit_id, code_fingerprint, vantage, population, fields}`,
    `fields` keyed by twin-config PATH, per-record `confidence`/`signal`/
    `source`/`note` and an `overrides:` record where a higher class won).
    Ruled canonical 2026-09-04 (`docs/WORK_ORDER_fidelity_scorecard.md`
    §9.6). `load_provenance` reads it through
    `axrange.fidelity.reference.load_reference_bundle` -- the ONE reader of
    that shape (LES-025/LES-037: no third copy of a cross-repo file parser)
    -- and folds it to the published row shape.
  * the Track-C NORMALIZED shape below, which the checked-in fixture uses,
    kept working unchanged so the shipped Grafana screen and its tests stay
    green.

`GET /api/imprints/{id}/provenance` reports which one it read
(`sidecar_shape: canonical | normalized | unrecognized | absent`; an
UNRECOGNIZED `schema` is present but not consumed) plus `evidence_source`,
so a consumer never has to infer "did these classes come from the producer"
from the presence of a warning (LES-219).

The NORMALIZED shape (chosen to match the Console's provenance table columns
directly -- field / signal / confidence / evidence):

    {
      "imprint_id": "...", "as_of": "...", "store": "...",
      "days_observed": <int>, "windows": <int>,
      "summary": {"services": <int>, "personas": <int>,
                  "mean_confidence": <float>, "out_of_vocab_pct": <float>},
      "segment_confidence": {"<segment name>": <float>, ...},
      "fields": {
        "<field path/label>": {"evidence_class": <str>, "confidence": <float>,
                                "signal": <str>, "evidence": <str>},
        ...
      }
    }

It carries no `schema` key -- which is how it is DETECTED
(`_detect_sidecar_shape`). Its `imprint_id` is READ and compared with the
canonical id: a DIFFERENT value refuses, a missing one is a published
warning (see above).

`hosts`/`segments` (+ each segment's `cidr`/`host_count`) are DERIVED from
the twin_config's real structural content (nodes/segments) -- they are
facts the twin_config actually carries. For a NORMALIZED sidecar,
`services`/`personas`/`mean_confidence`/`out_of_vocab_pct`/per-segment
`confidence` are read straight from its `summary`/`segment_confidence`
blocks, never fabricated or averaged from the small illustrative `fields`
sample. For every other shape (every real cast bundle), `services` and
`personas` are COUNTED from the bundle itself (2026-10-28, CorpAPCN `test2`
read 0/0 beside 17 persona files): `services` is the declared `listen:`
entries summed over host INSTANCES (a `count: 3` group serving two ports is
six), the same per-instance notion `hosts` uses; `personas` is the persona
documents in the bundle's `personas/` folder, which is what `install-cast`
ships and `AXRANGE_PERSONAS_DIR` loads. Persona intent has no home in the
twin-config vocabulary (removed from `TwinConfigNode` by the role/persona
split, docs/WORK_ORDER_role_persona_tier.md), so the folder is the only
place to count it. `out_of_vocab_pct` alone stays unmeasured there.

**A cast bundle SUMMARIZES too** (R7 -- superseding the S1 implementation
call D3 (a)'s "`GET /{id}` stays strict" half, `docs/
WORK_ORDER_console_canvas.md` §8.1). A present sidecar's shape is DETECTED
FIRST (`_detect_sidecar_shape`), and only a shape this reader consumes --
normalized or canonical -- must then meet the one precondition
`axrange.fidelity.reference` enforces too (a `fields` object,
`sidecar_fields_or_raise`), refused in the SAME words, so the listing, the
summary and `GET .../cast` agree on which bundles open. An UNRECOGNIZED
`schema` is held to no precondition: it is present and not consumed, and a
future schema that renames `fields` must not make its bundle vanish (round
2, SF3). A NORMALIZED sidecar keeps its strict reader; a CANONICAL,
UNRECOGNIZED or ABSENT one yields the numbers the listing publishes for it --
hosts/
segments from the twin config, `as_of`/`windows` from the topology export,
`mean_confidence` the canonical mean over FINITE confidences (or 0.0) with
`confidence_source` saying which -- and the three stats only a normalized
`summary` block carries (`services`, `personas`, `out_of_vocab_pct`) are
published as 0 for the shipped plugin's unguarded render AND named
unmeasured in `warnings` (D3 (a)'s own `mean_confidence` precedent), with
`sidecar_shape` stating what was parsed. The listing's entries are PROJECTED
from that same summary, so "lists" implies "summarizes" by construction.

**Fail-loud on author-supplied scalars** (2026-08-25 review fix M1): every
sidecar value this module publishes as a number goes through
`_coerce_int`/`_coerce_finite_float` (and their optional forms) -- the ONE
bounded rule in `axrange.jsonnum` (a whole number is a JSON number in
0 .. 2**53 - 1, a float is finite; GAP-082 review round 2, SF2) -- and every
string through `_coerce_optional_str`; each turns a bad value into a named
`ImprintDataError` (-> HTTP 400) instead
of letting a bare `ValueError`/`TypeError` (or, worse, a `pydantic.
ValidationError` once the value reaches a DTO downstream) surface as an
unhandled HTTP 500. `service.py`'s `imprint_summary`/`imprint_provenance`
wrap ONLY `axrange.api.imprints` calls in `try/except ImprintDataError`, so
every value that could fail type validation must already be the right type
BY THE TIME it leaves this module -- coercion cannot be deferred to DTO
construction. A value published as a MEASUREMENT must also be FINITE
(`_coerce_finite_float`): JSON `NaN`, `Infinity` and `1e400` all parse, a
non-finite float serializes as `null`, and `int(inf)` raises OverflowError
(GAP-082 review).

ONE deliberate CARVE-OUT (GAP-082 review round 2, 9a): a CANONICAL row's
`confidence` is not refused. A present value that is not a finite number is
IGNORED, COUNTED and reported -- in `warnings` and in the summary's
`confidence_source` -- by the one rule both readers share
(`_canonical_confidence`), so a value is on a provenance row exactly when it
is in the summary's mean. Refusing it would 400 the whole provenance route
over one unreadable cell while the summary silently skipped the same cell,
which is what it used to do. Do not restore that 400.
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML

from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw
from axrange.jsonnum import (
    NumberFieldError,
    finite_number,
    is_finite_number,
    optional_whole_number,
    whole_number,
)
from axrange.twinconfig.schema import TwinConfig, TwinConfigFile, TwinConfigNode

# <repo-root>/axrange/api/fixtures/imprints -- this file's parents:
# [0]=api [1]=axrange [2]=repo root (mirrors `twinconfig.registry`'s
# `_LEGACY_CATALOG_DIR` computation).
_DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "imprints"

_REQUIRED_SUMMARY_KEYS = ("services", "personas", "mean_confidence", "out_of_vocab_pct")

# The SIX evidence classes, in precedence order (`docs/design/
# 14-operator-assertions.md` §2). Published for the Console's legend and used
# to WARN about a class outside the vocabulary -- never to rewrite one: a
# reader that silently normalized an unknown class would hide exactly the
# producer/consumer drift this list exists to catch.
EVIDENCE_CLASSES = (
    "asserted", "imported", "observed", "referenced", "imputed", "compiler",
)

# What `ProvenanceDoc.sidecar_shape` publishes.
SIDECAR_CANONICAL = "canonical"
SIDECAR_NORMALIZED = "normalized"
SIDECAR_ABSENT = "absent"
#: A sidecar whose `schema` names a shape this reader does not know. PRESENT
#: but NOT consumed -- exactly as `axrange.fidelity.reference` treats any
#: non-canonical schema -- and never handed to the strict normalized reader,
#: which refused it and so hid the bundle from the listing while
#: `GET .../cast` opened it (GAP-082 review).
SIDECAR_UNRECOGNIZED = "unrecognized"

# What a catalog entry's `confidence_source` publishes. `mean_confidence`
# stays a NON-NULL float even when there is no sidecar to take it from
# (0.0 + `confidence_source: "absent"`), because the shipped Grafana screen
# calls `summary.mean_confidence.toFixed(2)` and a null would break it at
# runtime -- decision D3 (a), `docs/WORK_ORDER_console_canvas.md` §8.1. The
# companion field is what keeps that 0.0 from reading as evidence.
CONFIDENCE_FROM_SIDECAR = "sidecar"
CONFIDENCE_ABSENT = "absent"
#: S1: a CANONICAL sidecar has no `summary.mean_confidence`, but it does have
#: per-field confidences -- so publishing 0.0 for the one bundle that ships
#: full provenance made the Console's confidence bar read "nothing is known"
#: about the best-evidenced bundle in the catalog. The mean is taken over the
#: fields that CARRY one, and the coverage travels with it in
#: `confidence_source` ("canonical sidecar (151/687 fields)") so the number
#: is never mistaken for a mean over everything.
CONFIDENCE_FROM_CANONICAL = "canonical sidecar"
#: What an unrecognized sidecar contributes to `mean_confidence`: nothing.
CONFIDENCE_UNRECOGNIZED = "unrecognized sidecar (not consumed)"

#: The scorecard's own citation, rewritten out of Console-facing warnings
#: (nit): `GET .../cast` is not a fidelity endpoint and its reader should not
#: send a Console operator to a work order about scorecard rows.
_SCORECARD_CITATION = "docs/WORK_ORDER_fidelity_scorecard.md §8.1"
_CONSOLE_CITATION = "docs/WORK_ORDER_console_canvas.md S1.4"


def console_warning(text: str) -> str:
    return str(text).replace(_SCORECARD_CITATION, _CONSOLE_CITATION)


def is_canonical_sidecar(raw: Any) -> bool:
    """ONE predicate for "is this the compiler's canonical sidecar shape"
    (nit: it was spelled three different ways in this module, and three
    spellings of one question is three places for it to drift)."""
    from axrange.fidelity.reference import CAST_PROVENANCE_SCHEMA

    return isinstance(raw, dict) and raw.get("schema") == CAST_PROVENANCE_SCHEMA


def default_fixtures_dir() -> Path:
    return _DEFAULT_FIXTURES_DIR


class ImprintError(Exception):
    """Base for all imprint-fixture errors."""


class ImprintNotFoundError(ImprintError):
    """No imprint with the given id under the fixtures directory."""


class ImprintDataError(ImprintError):
    """An imprint's on-disk fixture is missing a required file, is
    malformed (bad YAML/JSON, doesn't match the twin_config schema, a
    required provenance field is missing or not numeric), or its directory
    name and `twin_config.id` disagree (the ADDRESSING guard -- a baked
    sidecar/export id that disagrees is a warning, never this error)."""


@dataclass(frozen=True)
class ImprintSegment:
    name: str
    cidr: str
    host_count: int
    # None, not 0.0, when the sidecar carries no confidence for this
    # segment -- a fabricated zero would read as "learned, low confidence"
    # rather than "unknown" (the project's own don't-invent-evidence
    # posture, matching aximprint's own compiled-output documentation).
    confidence: Optional[float]


@dataclass(frozen=True)
class ImprintSummary:
    id: str
    as_of: str
    store: Optional[str]
    days_observed: Optional[int]
    windows: Optional[int]
    hosts: int
    segments: int
    services: int
    personas: int
    mean_confidence: float
    out_of_vocab_pct: float
    topology_segments: list[ImprintSegment]
    #: R7: WHAT WAS PARSED (`canonical` | `normalized` | `unrecognized` |
    #: `absent`), in the vocabulary `CastSummary`/`ProvenanceDoc` already
    #: publish. `services`/`personas`/`out_of_vocab_pct` are measurements only
    #: when this reads `normalized`; otherwise they are the plugin's non-null
    #: placeholders and `warnings` says so in words. REQUIRED (no default):
    #: every construction site states it, so none can inherit a `normalized`
    #: it never parsed.
    sidecar_shape: str
    #: Where `mean_confidence` came from -- the listing entry's own field, so
    #: a list entry and its summary make the same statement (both are built
    #: by `_summary_from`). Required for the same reason.
    confidence_source: str
    #: The topology export's baked PIT id when it is a string; lineage, never an address.
    pit_id: Optional[str] = None
    #: Baked-id disagreements and unmeasured stats, STATED (R7) -- the
    #: channel that replaced the refusals.
    warnings: list[str] = dc_field(default_factory=list)


@dataclass(frozen=True)
class ImprintCatalogEntry:
    id: str
    as_of: str
    hosts: int
    segments: int
    mean_confidence: float
    # D3 (a): a bundle with NO provenance sidecar still lists -- the Console
    # is otherwise empty the moment `AXRANGE_IMPRINTS_DIR` points at the real
    # cast bundles. `mean_confidence` stays non-null (see CONFIDENCE_* above);
    # these two fields are what stop that 0.0 from being read as evidence.
    has_provenance: bool = True
    confidence_source: str = CONFIDENCE_FROM_SIDECAR
    #: The PIT this bundle was cast from (the topology export's baked id when
    #: it is a string), or None: lineage for the twin listing.
    pit_id: Optional[str] = None
    #: Unified Console: whether the bundle carries a topology export -- with
    #: `has_provenance`, what tells a CAPTURE-backed bundle from a
    #: hand-authored one in the Console's outline.
    has_topology_export: bool = False


@dataclass(frozen=True)
class ProvenanceOverride:
    """A higher class winning over a lower one, kept VISIBLE rather than
    erased (`docs/design/14-operator-assertions.md` §2's house rule). The
    sidecar spells the losing class `class` (or `evidence_class`); this DTO-
    facing shape spells it `evidence_class` throughout, so a row and its
    override read in one vocabulary."""

    evidence_class: Optional[str] = None
    value: Any = None
    confidence: Optional[float] = None
    drift: Optional[bool] = None


@dataclass(frozen=True)
class ProvenanceRow:
    field: str
    signal: str
    evidence_class: str
    confidence: float
    evidence: str
    # `False` means THIS ROW carries no `confidence` -- nothing more (S1).
    # It is NOT a synonym for "compiler row": measured on `corpapcn-golden`,
    # 250 of 333 `observed` rows and 1 of 69 `imputed` rows carry none, on
    # top of all 285 `compiler` rows. `confidence` stays a non-null float for
    # the shipped plugin (`row.confidence.toFixed(2)`); read the pair.
    has_confidence: bool = True
    source: Optional[str] = None
    note: Optional[str] = None
    overrides: Optional[ProvenanceOverride] = None


@dataclass(frozen=True)
class ProvenanceDoc:
    """What `GET /api/imprints/{id}/provenance` publishes: the rows PLUS
    which sidecar shape they came from and whether the producer supplied the
    classes at all. Both are stated fields, never inferred from the presence
    of a warning (LES-219)."""

    imprint_id: str
    #: `canonical` | `normalized` | `absent` -- WHAT WAS PARSED. The
    #: unambiguous field; read this one.
    sidecar_shape: str
    sidecar_schema: Optional[str]
    #: Where THESE PUBLISHED ROWS came from. **S2: the same field NAME means
    #: something adjacent-but-different on `CastSummary`** (there it is the
    #: fidelity reader's own consumption state -- whether the SCORECARD's
    #: evidence classes come from the producer). Cross-referenced rather than
    #: renamed, because both spellings already have consumers.
    evidence_source: str
    rows: list[ProvenanceRow] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    # The canonical sidecar's own accounting of what the cast dropped/kept
    # (`population.suppressed[]`, `role_less`, `node_groups`, ...). Empty for
    # the normalized shape, which has no such block.
    population: dict[str, Any] = dc_field(default_factory=dict)
    code_fingerprint: Optional[str] = None


@dataclass(frozen=True)
class CastConduit:
    """One `conduits[]` entry of the bundle's twin config, spelled EXACTLY as
    the config spells it (R5, GAP-079): both ends are segment NAMES -- the
    key the Console joins the twin config to the topology export on, never a
    CIDR -- directed as written."""

    from_: str
    to: str


@dataclass(frozen=True)
class CastSegment:
    """One `segments[]` entry of the bundle's twin config, spelled as the
    config spells it (Console S5.1.a, the segment roster): `name` is the key
    the Console joins the twin config to the topology export on, `cidr` is
    the DEPLOYED network -- on a relocated cast a different address from the
    export's observed one, which is why the join is by name -- and
    `zone_type` is `None` when the config declares none (never defaulted to
    the effective zone: that is a scenario-wide rule the expander applies)."""

    name: str
    cidr: str
    zone_type: Optional[str]


@dataclass(frozen=True)
class CastRouter:
    """One ROUTER the bundle's twin config builds (R5, reshaped by the
    GAP-082 review, S5 and round 2): one per expanded instance of a `kind:
    router` spec -- `gw01`, `gw02` for `count: 2`, the spec's own name at
    count 1 -- with the segment NAMES the spec has a leg on, in declaration
    order, each once."""

    name: str
    segments: tuple[str, ...]


@dataclass(frozen=True)
class CastNode:
    """One `nodes[]` spec of the bundle's twin config, as the fidelity reader
    already holds it (`TwinGroup`): its name, kind, role, the resolved `count`
    (a `${N:default}` template counts at its default, the same rule the
    `vms`/`hosts` counts use), every leg's segment NAME, and the node ids the
    expander fans it out to. The Console's DECLARED-DESIGN map for a bundle
    with no topology export (Unified Console design rule 1) draws from this --
    published as written, never interpreted."""

    name: str
    kind: str
    role: Optional[str]
    count: int
    segments: tuple[str, ...]
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class CastSummary:
    """`GET /api/imprints/{id}/cast` -- the COMPILED BUNDLE's summary, as
    distinct from `ImprintSummary`'s learned-topology headline stats. Every
    field aximprint does not emit for a given bundle is `None`/`False` and
    reported absent, never defaulted: `code_fingerprint: null` says "this
    bundle predates the fingerprint", where `0.0`/`""` would say something
    false."""

    id: str                       # the DIRECTORY name this lookup resolved by
    bundle_dir: str
    twin_config_id: str
    description: Optional[str]
    pit_id: Optional[str]
    bundle_schema: Optional[str]
    topology_schema_version: Optional[str]
    code_fingerprint: Optional[str]
    params: dict[str, Any]
    deploy_min_occupancy: float
    deploy_min_occupancy_source: str      # "declared" | "engine default"
    segments: int
    node_specs: int
    vms: int
    hosts: int
    conduits: int
    observed_nodes: int
    observed_edges: int
    observed_segments: int
    census: dict[str, Any]
    has_provenance: bool
    has_topology_export: bool
    has_assertions: bool
    has_layout: bool
    provenance_schema: Optional[str]
    provenance_path: Optional[str]
    sidecar_shape: str
    #: S2: the FIDELITY reader's consumption state, NOT the same statement as
    #: `ProvenanceDoc.evidence_source` (which describes the rows THAT endpoint
    #: publishes). `sidecar_shape` is the field to read for "what shape is
    #: this bundle's sidecar".
    evidence_source: str
    warnings: list[str] = dc_field(default_factory=list)
    #: R5 (GAP-079): the ROSTER behind the `conduits` count -- its length IS
    #: that count, in the config's own order, duplicates and all (the SPA
    #: resolves and dedupes; this reader publishes, it does not interpret).
    conduit_roster: list[CastConduit] = dc_field(default_factory=list)
    #: R5: every `kind: router` node, in declaration order, with the segments
    #: it has a leg on (each once, spelled as the config spells them --
    #: including a leg on a segment the topology export does not carry, the
    #: cast's `internet-sink`). Per ROUTER, never a flattened union (GAP-082
    #: review, S5): a union cannot say whether there is one gateway or three
    #: (`twins/datacenter`), and the Console names "the gateway" only
    #: when there is exactly one.
    routers: list[CastRouter] = dc_field(default_factory=list)
    #: Console S5.1.a: the ROSTER behind the `segments` count -- its length IS
    #: that count, in the config's own order. An observed segment whose NAME
    #: is absent from it is one the cast PRUNED, which is what lets the Console
    #: tell a segment re-addable by assertion from one the twin already has;
    #: the entries also carry the deployed CIDR and zone type the inspector
    #: states. Published as written, never interpreted.
    segment_roster: list[CastSegment] = dc_field(default_factory=list)
    #: The ROSTER behind `node_specs` (Unified Console, declared-design map):
    #: its length IS that count, in the config's own order, and the sum of its
    #: `count`s IS `vms`.
    node_roster: list[CastNode] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Author-supplied scalar coercion (M1) -- every sidecar value that becomes an
# int/float on a DTO downstream is validated HERE, never left for pydantic to
# reject at DTO-construction time (which `service.py`'s try/except doesn't
# reach -> would surface as an HTTP 500, not a 400).
# ---------------------------------------------------------------------------
def _coerce_int(value: Any, *, imprint_id: str, field: str) -> int:
    """A whole number a strict reader publishes -- the ONE bounded rule
    (`axrange.jsonnum.whole_number`: a JSON number in 0 .. 2**53 - 1, never a
    string, never clamped) -- refused as this module's typed error."""
    try:
        return whole_number(value, field)
    except NumberFieldError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e


def _coerce_finite_float(value: Any, *, imprint_id: str, field: str) -> float:
    """A measurement a strict reader publishes: a FINITE JSON number
    (`axrange.jsonnum.finite_number`). JSON `NaN`, `Infinity` and `1e400` all
    parse, and a non-finite float serializes as `null` -- breaking the
    non-null contract the shipped plugin relies on (GAP-082 review)."""
    try:
        return finite_number(value, field)
    except NumberFieldError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e


def _coerce_optional_finite_float(
    value: Any, *, imprint_id: str, field: str,
) -> Optional[float]:
    if value is None:
        return None
    return _coerce_finite_float(value, imprint_id=imprint_id, field=field)


def _coerce_optional_int(value: Any, *, imprint_id: str, field: str) -> Optional[int]:
    if value is None:
        return None
    return _coerce_int(value, imprint_id=imprint_id, field=field)


def _coerce_param_float(value: Any, *, imprint_id: str, field: str) -> float:
    """A twin-config PARAM read as a number (`DEPLOY_MIN_OCCUPANCY`). A param
    is a YAML scalar an author may quote ("0.8") -- params substitute into
    templates as text -- so a numeric string parses here, which a JSON
    artifact's number never gets to; the result must still be finite."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as e:
        raise ImprintDataError(
            f"imprint {imprint_id!r}: {field} is not numeric: {value!r}"
        ) from e
    if not math.isfinite(number):
        raise ImprintDataError(
            f"imprint {imprint_id!r}: {field} is not a finite number: {value!r}"
        )
    return number


def _coerce_optional_str(value: Any, *, imprint_id: str, field: str) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ImprintDataError(f"imprint {imprint_id!r}: {field} is not a string: {value!r}")


# ---------------------------------------------------------------------------
# Directory / file resolution
# ---------------------------------------------------------------------------
#: A catalog id is a DIRECTORY NAME under a trusted root, and the guard is
#: POSITIVE (M1). The previous negative guard -- reject `/`, `\\` and `..` --
#: let a lone `.` through, and `.` is a directory that always exists: a
#: percent-encoded `PUT /api/imprints/%2e/layout` resolved to the catalog
#: ROOT and wrote `layout.json` into it. A deny-list of the traversal
#: spellings someone thought of is not the same as an allow-list of the ids
#: that are legal. Leading character is alphanumeric, so `.`, `..` and every
#: dotfile are refused by construction.
_CATALOG_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_safe_catalog_id(value: str) -> bool:
    """THE one predicate for "is this string usable as a catalog directory
    name". `service._is_safe_catalog_ref` calls this rather than carrying a
    second copy -- two guards on one question is how `%2e` gets through the
    one nobody updated."""
    return bool(value) and _CATALOG_ID_RE.fullmatch(value) is not None


def resolve_imprint_dir(fixtures_dir: Optional[str | Path], imprint_id: str) -> Path:
    """Resolve `<fixtures_dir>/<imprint_id>/` -- THE one guarded path into a
    bundle, and the only place this module's readers and writers derive one.

    `service._imprint_bundle_dir` is the single funnel every Console endpoint
    goes through (the sibling modules `console_files`/`recompile` are handed
    an already-resolved directory rather than an id, so they cannot bypass
    it). `service._is_safe_catalog_ref` shares this module's
    `is_safe_catalog_id` predicate for the same reason."""
    root = Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES_DIR
    if not is_safe_catalog_id(imprint_id):
        raise ImprintNotFoundError(f"no imprint {imprint_id!r}")
    d = root / imprint_id
    if not d.is_dir():
        raise ImprintNotFoundError(f"no imprint {imprint_id!r} in {root}")
    return d


def _imprint_dir(fixtures_dir: Optional[str | Path], imprint_id: str) -> Path:
    """Back-compat spelling of `resolve_imprint_dir` for this module's own
    readers. One implementation, so the guard cannot diverge."""
    return resolve_imprint_dir(fixtures_dir, imprint_id)


def _yaml_loader() -> YAML:
    return YAML(typ="safe")


#: `_load_twin_config`'s parse cache -- bounded, and locked because sync
#: routes run on a thread pool. Its key is described on the function.
_TWIN_CONFIG_CACHE: "OrderedDict[tuple[Any, ...], TwinConfig]" = OrderedDict()
_TWIN_CONFIG_CACHE_MAX = 64
_TWIN_CONFIG_CACHE_LOCK = threading.Lock()


def _load_twin_config(dir_: Path, imprint_id: str) -> TwinConfig:
    """Deliberately NOT `axrange.twinconfig.registry.load_twin_config`: this
    module needs its OWN typed errors (`ImprintNotFoundError`/
    `ImprintDataError`, mapped to the 404/400 split in `service.py`, vs. the
    registry's `TwinConfigNotFoundError`/`TwinConfigShapeError`), the strict
    `<dir>/<imprint_id>.yaml` filename match ONLY -- deliberately no
    scan-by-declared-id rename fallback, since an imprint's directory name
    IS its canonical id (cross-checked below, not merely a hint the
    registry's fallback would let drift) -- and relies on `_imprint_dir`'s
    path-traversal guard already applied by the caller (the registry has
    none of its own; it trusts callers under a trusted catalog root).

    CACHED per FILE (GAP-082 review round 2, SF1): every route runs the
    addressing guard, and one parse of the real pit bundle's twin config
    costs ~210 ms -- which a pin drop paid on every layout write. The key is
    the file as it is NOW: its resolved path (symlinks followed) plus the
    inode, size, mtime and ctime `stat` reports. An apply's install stage --
    which rewrites `<id>.yaml` -- or any other edit therefore invalidates the
    entry, and so does a restore that puts an old mtime back (ctime cannot be
    set). A failed parse is never cached.

    Every caller gets its OWN deep copy (~4 ms on the 109-node pit config,
    against the ~210 ms its parse costs). The twin-config models are mutable
    and one IS mutated in place today -- `expand.py`'s IP allocation assigns
    `leg.ip` -- so `frozen=True` is not available to make sharing safe; and a
    shared instance would let one in-place edit anywhere corrupt every later
    request for that file, process-wide and silently, until its mtime/ctime
    changed. A docstring asking callers not to mutate is not a guarantee."""
    path = _config_path(dir_, imprint_id)
    if not path.is_file():
        raise ImprintDataError(f"imprint {imprint_id!r}: missing {path.name}")
    try:
        st = path.stat()
        key = (str(path.resolve()), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except OSError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {path.name}: {e}") from e
    with _TWIN_CONFIG_CACHE_LOCK:
        cached = _TWIN_CONFIG_CACHE.get(key)
        if cached is not None:
            _TWIN_CONFIG_CACHE.move_to_end(key)
            return cached.model_copy(deep=True)
    tc = _parse_twin_config(path, imprint_id)
    with _TWIN_CONFIG_CACHE_LOCK:
        _TWIN_CONFIG_CACHE[key] = tc
        while len(_TWIN_CONFIG_CACHE) > _TWIN_CONFIG_CACHE_MAX:
            _TWIN_CONFIG_CACHE.popitem(last=False)
    return tc.model_copy(deep=True)


def _config_path(dir_: Path, imprint_id: str) -> Path:
    """The bundle's twin-config file -- `<dir>/<id>.yaml`, or a `twins/<id>/`
    folder's manifest te file (`axrange.twinconfig.bundle`, the one layout
    rule). A broken `twin.yaml` is this module's `ImprintDataError` (400)."""
    from axrange.twinconfig.bundle import bundle_config_path
    from axrange.twinconfig.errors import TwinConfigError

    try:
        return bundle_config_path(dir_, imprint_id)
    except TwinConfigError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e


def _artifact_path(dir_: Path, name: str) -> Path:
    """A machine-written bundle file (`<id>.provenance.json`,
    `<id>.topology.json`): at the root of a legacy bundle, under
    `generated/` in a `twins/<id>/` folder (`axrange.twinconfig.bundle`)."""
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(dir_, name)


def _parse_twin_config(path: Path, imprint_id: str) -> TwinConfig:
    """The uncached parse behind `_load_twin_config`."""
    try:
        raw = load_yaml_raw(path, loader=_yaml_loader())
    except YamlLoadError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e
    except (OSError, ValueError) as e:
        # Neither is wrapped by `load_yaml_raw`: a permission error, and a
        # UnicodeDecodeError (a ValueError) from a file that is not UTF-8.
        raise ImprintDataError(f"imprint {imprint_id!r}: {path.name}: {e}") from e
    if not isinstance(raw, dict) or "twin_config" not in raw:
        raise ImprintDataError(
            f"imprint {imprint_id!r}: {path.name} missing top-level 'twin_config:' key"
        )
    try:
        wrapped = TwinConfigFile.model_validate(raw)
    except Exception as e:  # noqa: BLE001 -- reduced to one typed error, like registry.load_twin_config
        raise ImprintDataError(f"imprint {imprint_id!r}: twin_config shape error: {e}") from e
    return wrapped.twin_config


def _load_provenance_raw(dir_: Path, imprint_id: str) -> dict[str, Any]:
    path = _artifact_path(dir_, f"{imprint_id}.provenance.json")
    if not path.is_file():
        raise ImprintDataError(f"imprint {imprint_id!r}: missing {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # ValueError: a JSONDecodeError AND a UnicodeDecodeError (invalid
        # UTF-8) -- the latter is not a JSONDecodeError (GAP-082 review).
        raise ImprintDataError(f"imprint {imprint_id!r}: bad {path.name}: {e}") from e
    if not isinstance(data, dict):
        raise ImprintDataError(f"imprint {imprint_id!r}: {path.name} must be a JSON object")
    return data


def _read_sidecar(dir_: Path, imprint_id: str) -> Optional[Any]:
    """The sidecar document as parsed, or `None` when there is no sidecar
    FILE -- an absent sidecar is a publishable state (D3 (a)); an unreadable
    one is broken content. ONE read (S4): a caller that needs the sidecar for
    a summary reads it here and hands it on, never re-reading it."""
    path = _artifact_path(dir_, f"{imprint_id}.provenance.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # ValueError covers a JSONDecodeError AND a UnicodeDecodeError
        # (invalid UTF-8, a UTF-16 BOM) -- the latter is not a JSONDecodeError
        # and escaped as a 500 on the summary, the plan pre-check and the
        # WHOLE listing (GAP-082 review).
        raise ImprintDataError(
            f"imprint {imprint_id!r}: provenance sidecar is unreadable: {e}"
        ) from e


def _check_addressing_id(imprint_id: str, tc: TwinConfig) -> None:
    """THE addressing guard (LES-195, LES-222): the directory name a lookup
    resolved by must be the id the twin config itself declares.

    The one id check that REFUSES, because it is the one protecting an
    address: the file read is `<dir>/<dir>.yaml`, and the id it declares is
    what "deploy as twin" deploys under and what `axrange.fidelity.reference`
    resolves the bundle by. A copy-pasted, renamed directory whose content
    still names another id would otherwise serve one twin's content under
    another's URL. Every other id-shaped field is classified in the module
    docstring's table (`_sidecar_id_check`, `_export_id_warnings`)."""
    if tc.id != imprint_id:
        raise ImprintDataError(
            f"imprint {imprint_id!r}: directory name (canonical) disagrees with "
            f"twin_config.id={tc.id!r} -- the twin config is what this bundle "
            "deploys and scores as, so it is not served under another id "
            "(LES-195)"
        )


def _addressable_bundle(
    imprint_id: str, fixtures_dir: Optional[str | Path],
) -> tuple[Path, TwinConfig]:
    """THE addressing guard, returning what it read: the directory (the
    traversal guard; `ImprintNotFoundError` -> 404) and its twin config,
    parsed (cached, `_load_twin_config`) and compared with the directory name
    (`ImprintDataError` -> 400). Nothing else: no sidecar, no export."""
    dir_ = resolve_imprint_dir(fixtures_dir, imprint_id)
    tc = _load_twin_config(dir_, imprint_id)
    _check_addressing_id(imprint_id, tc)
    return dir_, tc


def check_imprint_addressable(
    imprint_id: str, *, fixtures_dir: Optional[str | Path] = None,
) -> Path:
    """The NARROW addressing check (`_addressable_bundle`), returning the
    directory.

    EVERY route that addresses a bundle by id runs it (GAP-082 review S2/C,
    round 2 SF4): the Console's operator routes (`service._imprint_bundle_
    dir`), the "deploy as twin" pre-check (`service._resolve_imprint_
    scenario_path`), `/topology`'s imprint branch, and `/cast` and
    `/provenance` (`load_reference`). So the routes agree on what an
    addressable bundle is -- a renamed one, or a directory with no `<id>.yaml`
    beside a stray compiler file, is refused everywhere -- and a deploy never
    depends on the health of a sidecar or an export it does not read. Free
    after the first request: the parse is cached per file."""
    return _addressable_bundle(imprint_id, fixtures_dir)[0]


def _sidecar_id_check(imprint_id: str, prov: dict[str, Any]) -> list[str]:
    """A NORMALIZED sidecar's own `imprint_id` (the module docstring's
    table). Nothing legitimately writes a value different from the
    addressing id -- it names the imprint whose rows the sidecar carries --
    so a DIFFERENT one, a string that is not the canonical id or any
    non-string, REFUSES: a copied or stale sidecar is LES-195's own case
    (restored by the GAP-082 review, S1, after R7 had relaxed it). A MISSING
    one contradicts nothing, so it is published as a warning. A canonical
    sidecar declares no imprint id and is never passed here."""
    declared = prov.get("imprint_id")
    if declared == imprint_id:
        return []
    if declared is None or declared == "":
        return [
            f"imprint {imprint_id!r}: the provenance sidecar declares no "
            f"imprint_id -- its rows are served under the canonical id "
            f"{imprint_id!r} (the directory name = twin_config.id), which "
            "nothing in the sidecar confirms"
        ]
    raise ImprintDataError(
        f"imprint {imprint_id!r}: the provenance sidecar declares "
        f"imprint_id={declared!r}, not this bundle's canonical id "
        f"{imprint_id!r} (the directory name = twin_config.id) -- a copied or "
        "stale sidecar, whose rows would be served under the wrong imprint "
        "(LES-195)"
    )


@dataclass(frozen=True)
class _ExportScalars:
    """The few top-level scalars a summary takes from the topology export,
    read WITHOUT building its node/edge object graph (S4: a real export is
    0.7-3 MB; parsed once here, never walked). `imprint_id`/`pit_id` are the
    producer's BAKED ids, kept RAW (any JSON type) so a non-string one is
    reported as what it is -- data, never an address (LES-222)."""

    #: The file exists.
    present: bool = False
    #: It was read and is a JSON object: the fields below mean something only
    #: when this is true (the J nit -- an unreadable export and a readable one
    #: with one bad scalar are different states).
    parsed: bool = False
    as_of: str = ""
    windows: Optional[int] = None
    imprint_id: Any = None
    pit_id: Any = None
    #: Why a PRESENT export's value could not be read, stated rather than
    #: dropped (LES-235); the affected value is then published empty.
    problems: tuple[str, ...] = ()


def _export_scalars(dir_: Path, imprint_id: str) -> _ExportScalars:
    """Tolerant by design: the export is not a summary's primary artifact
    (the twin config is), so a damaged one degrades the values it supplies
    to empty-with-a-reason rather than refusing a bundle that also LISTS --
    and NOTHING here can raise or reach a DTO with the wrong type (M1):
    `(OSError, ValueError)` covers a UnicodeDecodeError, and `windows` goes
    through the ONE bounded rule (`axrange.jsonnum`): a value that is not a
    whole number in 0 .. 2**53 - 1 -- a browser reads a 400-digit one as
    Infinity and then draws every host as a ghost -- is published as `null`
    WITH the reason, never clamped (GAP-082 review A, round 2 SF2)."""
    path = _artifact_path(dir_, f"{imprint_id}.topology.json")
    if not path.is_file():
        return _ExportScalars()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return _ExportScalars(present=True, problems=(f"{path.name} is unreadable: {e}",))
    if not isinstance(raw, dict):
        return _ExportScalars(present=True, problems=(f"{path.name} is not a JSON object",))
    problems: list[str] = []
    windows: Optional[int] = None
    try:
        windows = optional_whole_number(raw.get("windows"), "windows")
    except NumberFieldError as e:
        problems.append(f"{path.name}: {e}")
    as_of = ""
    if raw.get("as_of") is not None:
        as_of = _iso_utc(raw["as_of"])
        if not as_of:
            problems.append(f"{path.name}: `as_of` is not a timestamp: {raw['as_of']!r}")
    return _ExportScalars(
        present=True,
        parsed=True,
        as_of=as_of,
        windows=windows,
        imprint_id=raw.get("imprint_id"),
        pit_id=raw.get("pit_id"),
        problems=tuple(problems),
    )


def _detect_sidecar_shape(sidecar: Optional[dict[str, Any]]) -> str:
    """ONE rule for "what shape is this sidecar" -- DETECTED, never guessed:
    no document -> absent; no `schema` key -> the Track-C normalized shape
    (which carries none); `axr-cast-provenance/1` -> canonical; any other
    `schema` -> unrecognized: PRESENT, NOT consumed, exactly as the fidelity
    reader treats it (GAP-082 review). `_sidecar_shape` applies the same rule
    to the fidelity reader's view of the file, for `GET .../cast`."""
    if sidecar is None:
        return SIDECAR_ABSENT
    if sidecar.get("schema") is None:
        return SIDECAR_NORMALIZED
    if is_canonical_sidecar(sidecar):
        return SIDECAR_CANONICAL
    return SIDECAR_UNRECOGNIZED


def _export_id_warnings(imprint_id: str, export: _ExportScalars) -> list[str]:
    """The topology export's BAKED `imprint_id`, compared with the canonical
    id and PUBLISHED -- never refused (R7 / GAP-082). A cast bundle's export
    bakes the producer's id for the observed capture (a PIT id, or the
    observed imprint's own name -- `corpapcn-disco` inside
    `pit-395a61f04e-1780405200/`), so a difference is ordinary rather than a
    fault; the warning exists so nobody mistakes the export's field for the
    address, which the SPA once did (LES-263).

    Keyed on what was READ, not on whether any scalar had a problem (the J
    nit): a readable export with a bad `windows` still has its id compared;
    only an export that could not be parsed at all has nothing to say."""
    if not export.parsed:
        return []
    baked = export.imprint_id
    if baked == imprint_id:
        return []
    if baked is None or baked == "":
        return [
            f"imprint {imprint_id!r}: the topology export declares no "
            f"imprint_id -- it is served under the canonical id {imprint_id!r} "
            "(the directory name = twin_config.id)"
        ]
    if not isinstance(baked, str):
        # Never "declares no imprint_id" (the J nit): it declares one, of the
        # wrong type, and saying so is the only honest account of it.
        return [
            f"imprint {imprint_id!r}: the topology export bakes a NON-STRING "
            f"imprint_id ({type(baked).__name__} {baked!r}) -- published as "
            "data, never used as an address or coerced into one (LES-222)"
        ]
    return [
        f"imprint {imprint_id!r}: the topology export bakes imprint_id="
        f"{baked!r} (pit_id={export.pit_id!r}) -- the producer's id for the "
        f"observed capture, not this bundle's canonical id {imprint_id!r}; "
        "published as provenance and never used as an address (LES-222)"
    ]


def _unmeasured_stats_warning(imprint_id: str, shape: str) -> str:
    what = {
        SIDECAR_ABSENT: "no provenance sidecar",
        SIDECAR_CANONICAL: "a canonical provenance sidecar",
        SIDECAR_UNRECOGNIZED: "an unrecognized provenance sidecar (not consumed)",
    }.get(shape, f"a {shape} provenance sidecar")
    return (
        f"imprint {imprint_id!r}: out_of_vocab_pct comes only from a NORMALIZED "
        f"sidecar's `summary` block, and this bundle has {what} -- it reads 0, "
        "which is NOT a measurement (sidecar_shape says what was parsed; "
        "confidence_source says where mean_confidence came from). services is "
        "the declared `listen:` entries summed over host instances and personas "
        "the persona documents in the bundle's personas/ folder, both counted "
        "from the bundle"
    )


def _declared_service_count(tc: TwinConfig, *, imprint_id: str) -> int:
    """Declared `listen:` entries summed over host INSTANCES, routers excluded --
    the per-instance notion `_host_counts_by_segment` uses, so "262 hosts, 424
    services" reads as served ports across those hosts. A node with no
    `listen:` key makes no claim and counts nothing."""
    total = 0
    for node in tc.nodes:
        if node.kind == "router" or not node.listen:
            continue
        if node.count in (None, ""):
            count = 1
        else:
            try:
                count = int(_resolved_count(node.count, tc))
            except (TypeError, ValueError) as e:
                raise ImprintDataError(
                    f"imprint {imprint_id!r}: node {node.name!r} has a "
                    f"non-numeric count: {node.count!r}"
                ) from e
        total += len(node.listen) * count
    return total


def _persona_document_count(dir_: Path) -> int:
    """Persona documents shipped with the bundle (`<dir>/personas/*.yaml`, what
    `install-cast` copies beside the twin config). No folder is 0."""
    personas = dir_ / "personas"
    if not personas.is_dir():
        return 0
    return sum(1 for p in personas.iterdir() if p.is_file() and p.suffix in (".yaml", ".yml"))


# ---------------------------------------------------------------------------
# Structural derivation (from the REAL twin_config content)
# ---------------------------------------------------------------------------
def _node_segments(node: TwinConfigNode) -> list[str]:
    if node.nets:
        return [leg.segment for leg in node.nets]
    if node.net:
        return [node.net]
    return []


def _resolved_count(raw: Any, tc: TwinConfig) -> Any:
    """A node's `count:` with a `${NAME:default}` token resolved against the
    twin's declared params and the token's own default -- the value a deploy
    with no overrides would use (fix round 2: a parameterized bundle is a
    normal bundle, not an unreadable one). An unresolvable token is left as
    is, so the caller's non-numeric refusal still names it."""
    if not isinstance(raw, str) or "${" not in raw:
        return raw
    from axrange.twinconfig.errors import TwinConfigParamError
    from axrange.twinconfig.params import substitute

    try:
        return substitute(raw, dict(tc.params or {}))
    except TwinConfigParamError:
        return raw


def _host_counts_by_segment(
    tc: TwinConfig, *, imprint_id: str,
) -> tuple[dict[str, int], int]:
    """Per-segment host MEMBERSHIP + total host count, EXCLUDING routers --
    the same "role-bearing/deployable host" notion the console's stat tiles
    use (`docs/design/aximprint-integration/Console.dc.html`: "Hosts: 42" is
    42 workstation/server nodes behind the one router, not 43).

    `by_segment` counts segment MEMBERSHIP, not a partition of hosts across
    segments: a multi-homed non-router host touches more than one segment
    and is counted ONCE in `total` but on EVERY segment it's attached to in
    `by_segment` -- so `sum(by_segment.values())` can legitimately EXCEED
    `total` when any host is multi-homed. Callers must not assume the two
    reconcile by addition (`docs/WORK_ORDER_aximprint_console.md` review S1).
    """
    by_segment: dict[str, int] = {}
    total = 0
    for node in tc.nodes:
        if node.kind == "router":
            continue
        if node.count in (None, ""):
            count = 1
        else:
            try:
                count = int(_resolved_count(node.count, tc))
            except (TypeError, ValueError) as e:
                raise ImprintDataError(
                    f"imprint {imprint_id!r}: node {node.name!r} has a "
                    f"non-numeric count: {node.count!r}"
                ) from e
        total += count
        for seg_name in _node_segments(node):
            by_segment[seg_name] = by_segment.get(seg_name, 0) + count
    return by_segment, total


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def load_imprint_summary(
    imprint_id: str, *, fixtures_dir: Optional[str | Path] = None,
) -> ImprintSummary:
    """`<imprint_id>`'s summary, for ANY sidecar shape (R7).

    A NORMALIZED sidecar is read strictly: a missing or malformed headline
    stat fails loudly (`ImprintDataError`) rather than defaulting to zero,
    since that shape exists to carry them -- a silent `mean_confidence: 0.0`
    there would read as "learned, zero confidence" instead of "not
    recorded". A CANONICAL, UNRECOGNIZED or ABSENT sidecar (every real cast
    bundle is canonical or absent) yields the cast summary, its unmeasured
    stats named in `warnings`. On ids, only `twin_config.id` and a
    normalized sidecar's contradicting `imprint_id` refuse (module
    docstring)."""
    dir_ = _imprint_dir(fixtures_dir, imprint_id)
    return _summary_from(dir_, imprint_id, sidecar=_read_sidecar(dir_, imprint_id))


def _summary_from(dir_: Path, imprint_id: str, *, sidecar: Optional[Any]) -> ImprintSummary:
    """THE summary builder: `GET /api/imprints/{id}` and every listing entry
    come out of this one function (R7), so a bundle that lists also
    summarizes, with the same numbers.

    `sidecar` is the ALREADY-PARSED document, or `None` for no sidecar file
    (S4: the caller read it once). Its shape is DETECTED FIRST
    (`_detect_sidecar_shape`), and a shape this reader consumes -- normalized
    or canonical -- must then meet the precondition the fidelity reader
    enforces (`sidecar_fields_or_raise`: a `fields` object), refused with
    the SAME words `GET .../cast` answers with, so the listing, the summary
    and the cast agree on which bundles open (GAP-082 review). An
    UNRECOGNIZED schema is held to no precondition -- present, not consumed
    (round 2, SF3). A normalized sidecar then gets the strict reader; a
    canonical, unrecognized or absent one the cast reader."""
    from axrange.fidelity.reference import ReferenceError, sidecar_fields_or_raise

    tc = _load_twin_config(dir_, imprint_id)
    _check_addressing_id(imprint_id, tc)
    if sidecar is not None and not isinstance(sidecar, dict):
        raise ImprintDataError(
            f"imprint {imprint_id!r}: {imprint_id}.provenance.json must be a JSON object"
        )
    shape = _detect_sidecar_shape(sidecar)
    if shape in (SIDECAR_NORMALIZED, SIDECAR_CANONICAL):
        try:
            sidecar_fields_or_raise(
                sidecar, _artifact_path(dir_, f"{imprint_id}.provenance.json"),
            )
        except ReferenceError as e:
            # The SAME wrapping `load_reference` applies, so the reason is
            # identical to `GET .../cast`'s for the same file.
            raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e
    export = _export_scalars(dir_, imprint_id)
    warnings = _export_id_warnings(imprint_id, export) + [
        f"imprint {imprint_id!r}: topology export: {problem} -- the affected "
        "value is published empty, never invented"
        for problem in export.problems
    ]
    if shape == SIDECAR_NORMALIZED:
        return _normalized_summary(imprint_id, tc, sidecar, warnings=warnings)
    return _cast_summary_of(
        imprint_id, tc, sidecar, export, dir_=dir_, shape=shape, warnings=warnings,
    )


def _cast_summary_of(
    imprint_id: str,
    tc: TwinConfig,
    sidecar: Optional[dict[str, Any]],
    export: _ExportScalars,
    *,
    dir_: Path,
    shape: str,
    warnings: list[str],
) -> ImprintSummary:
    """A cast bundle's summary -- a CANONICAL, UNRECOGNIZED or ABSENT sidecar
    (R7).

    Structure from the twin config, time from the export, confidence from
    the canonical sidecar's own per-field records (S1's mean over FINITE
    confidences, its coverage in `confidence_source`), services from the
    declared listeners and personas from the bundle's persona folder (module
    docstring). `out_of_vocab_pct` has no source in any of these shapes:
    published as 0 so the shipped plugin's unguarded render keeps working
    (D3 (a)'s `mean_confidence` precedent), and named unmeasured."""
    by_segment, total_hosts = _host_counts_by_segment(tc, imprint_id=imprint_id)
    out = list(warnings)
    if shape == SIDECAR_CANONICAL:
        mean_confidence, confidence_source, ignored = _canonical_confidence_stats(sidecar)
        if ignored:
            out.append(
                f"imprint {imprint_id!r}: {ignored} canonical confidence "
                "value(s) are not finite numbers (NaN, Infinity, out of range, "
                "a boolean or a string) -- treated as ABSENT and counted, never "
                "averaged; the provenance rows say `has_confidence: false` for "
                "exactly the same values"
            )
    elif shape == SIDECAR_UNRECOGNIZED:
        mean_confidence, confidence_source = 0.0, CONFIDENCE_UNRECOGNIZED
        out.append(
            f"imprint {imprint_id!r}: the provenance sidecar's schema "
            f"{sidecar.get('schema')!r} is not one this reader consumes "
            "(`axr-cast-provenance/1`, or the schema-less normalized shape) -- "
            "PRESENT, NOT consumed, exactly as `GET .../cast` treats it"
        )
    else:
        mean_confidence, confidence_source = 0.0, CONFIDENCE_ABSENT
    out.append(_unmeasured_stats_warning(imprint_id, shape))
    return ImprintSummary(
        id=imprint_id,
        as_of=export.as_of,
        # Neither a canonical sidecar nor the export carries these: absent.
        store=None,
        days_observed=None,
        windows=export.windows,
        hosts=total_hosts,
        segments=len(tc.segments),
        services=_declared_service_count(tc, imprint_id=imprint_id),
        personas=_persona_document_count(dir_),
        mean_confidence=mean_confidence,
        out_of_vocab_pct=0.0,
        topology_segments=[
            # No per-segment confidence in either shape: `None`, never 0.0.
            ImprintSegment(
                name=seg.name, cidr=seg.cidr,
                host_count=by_segment.get(seg.name, 0), confidence=None,
            )
            for seg in tc.segments
        ],
        sidecar_shape=shape,
        confidence_source=confidence_source,
        pit_id=export.pit_id if isinstance(export.pit_id, str) and export.pit_id else None,
        warnings=out,
    )


def _normalized_summary(
    imprint_id: str, tc: TwinConfig, prov: dict[str, Any], *, warnings: list[str],
) -> ImprintSummary:
    """The Track-C NORMALIZED sidecar's strict reader. Its own `imprint_id`
    is checked FIRST (`_sidecar_id_check`: a different one refuses, a missing
    one is a warning). A normalized sidecar missing a headline stat, or
    carrying a non-finite one, is BROKEN content (a 400), not a degraded
    state: that shape exists to carry them."""
    id_warnings = _sidecar_id_check(imprint_id, prov)
    as_of = prov.get("as_of")
    if not isinstance(as_of, str) or not as_of:
        raise ImprintDataError(f"imprint {imprint_id!r}: provenance sidecar missing 'as_of'")

    summary_block = prov.get("summary")
    if not isinstance(summary_block, dict):
        raise ImprintDataError(f"imprint {imprint_id!r}: provenance sidecar missing 'summary'")
    missing = [k for k in _REQUIRED_SUMMARY_KEYS if k not in summary_block]
    if missing:
        raise ImprintDataError(
            f"imprint {imprint_id!r}: provenance 'summary' missing {missing}"
        )

    seg_confidence = prov.get("segment_confidence")
    if not isinstance(seg_confidence, dict):
        seg_confidence = {}

    by_segment, total_hosts = _host_counts_by_segment(tc, imprint_id=imprint_id)
    topology_segments = [
        ImprintSegment(
            name=seg.name,
            cidr=seg.cidr,
            host_count=by_segment.get(seg.name, 0),
            confidence=_coerce_optional_finite_float(
                seg_confidence.get(seg.name),
                imprint_id=imprint_id,
                field=f"segment_confidence[{seg.name!r}]",
            ),
        )
        for seg in tc.segments
    ]

    return ImprintSummary(
        # Canonical id is the DIRECTORY NAME (M2), not `tc.id` -- the two
        # are guaranteed equal past `_summary_from`'s addressing guard, but publishing
        # `imprint_id` directly (what the caller asked for / what
        # `list_imprints` iterates by) rather than a re-derived `tc.id`
        # keeps this the single source of truth for "what did we resolve".
        id=imprint_id,
        as_of=as_of,
        store=_coerce_optional_str(prov.get("store"), imprint_id=imprint_id, field="store"),
        days_observed=_coerce_optional_int(
            prov.get("days_observed"), imprint_id=imprint_id, field="days_observed",
        ),
        windows=_coerce_optional_int(
            prov.get("windows"), imprint_id=imprint_id, field="windows",
        ),
        hosts=total_hosts,
        segments=len(tc.segments),
        services=_coerce_int(summary_block["services"], imprint_id=imprint_id, field="summary.services"),
        personas=_coerce_int(summary_block["personas"], imprint_id=imprint_id, field="summary.personas"),
        mean_confidence=_coerce_finite_float(
            summary_block["mean_confidence"], imprint_id=imprint_id, field="summary.mean_confidence",
        ),
        out_of_vocab_pct=_coerce_finite_float(
            summary_block["out_of_vocab_pct"], imprint_id=imprint_id, field="summary.out_of_vocab_pct",
        ),
        topology_segments=topology_segments,
        sidecar_shape=SIDECAR_NORMALIZED,
        confidence_source=CONFIDENCE_FROM_SIDECAR,
        warnings=id_warnings + list(warnings),
    )




def load_imprint_provenance(
    imprint_id: str, *, fixtures_dir: Optional[str | Path] = None,
) -> list[ProvenanceRow]:
    """The per-field provenance rows for a NORMALIZED sidecar, sorted by
    field label. Kept as its own entry point because the shipped Grafana
    screen's contract is this list; `load_provenance` is what the Console
    endpoint calls and it reads both shapes.

    The same id rule as every other reader (module docstring): a sidecar
    whose own `imprint_id` is DIFFERENT refuses; a MISSING one is served,
    and since a bare list has no channel to say so, that warning is
    published by `load_provenance(...).warnings` and the summary instead."""
    dir_ = resolve_imprint_dir(fixtures_dir, imprint_id)
    # Confirm the imprint itself is well-formed (consistent 404 vs 400
    # split with `load_imprint_summary` even if only `/provenance` is hit).
    tc = _load_twin_config(dir_, imprint_id)
    prov = _load_provenance_raw(dir_, imprint_id)
    _check_addressing_id(imprint_id, tc)
    _sidecar_id_check(imprint_id, prov)
    return _rows_from_normalized(prov, imprint_id=imprint_id)


def _rows_from_normalized(prov: dict[str, Any], *, imprint_id: str) -> list[ProvenanceRow]:
    """Fold an ALREADY-PARSED normalized sidecar into rows (S4: the document
    is parsed once per request and handed here, never re-read)."""
    fields = prov.get("fields")
    if not isinstance(fields, dict):
        raise ImprintDataError(f"imprint {imprint_id!r}: provenance sidecar missing 'fields'")

    rows: list[ProvenanceRow] = []
    for field_path, rec in sorted(fields.items()):
        if not isinstance(rec, dict):
            raise ImprintDataError(
                f"imprint {imprint_id!r}: provenance field {field_path!r} is not an object"
            )
        try:
            signal = str(rec["signal"])
            evidence_class = str(rec["evidence_class"])
            raw_confidence = rec["confidence"]
            evidence = str(rec["evidence"])
        except KeyError as e:
            raise ImprintDataError(
                f"imprint {imprint_id!r}: provenance field {field_path!r} missing {e}"
            ) from e
        confidence = _coerce_finite_float(
            raw_confidence, imprint_id=imprint_id,
            field=f"fields[{field_path!r}].confidence",
        )
        rows.append(
            ProvenanceRow(
                field=field_path, signal=signal, evidence_class=evidence_class,
                confidence=confidence, evidence=evidence,
            )
        )
    return rows


def list_imprints(fixtures_dir: Optional[str | Path] = None) -> list[ImprintCatalogEntry]:
    """Every subdirectory of the catalog root that holds a loadable
    `<id>.yaml`, sorted by id -- with or without a provenance sidecar
    (**D3 (a)**, `docs/WORK_ORDER_console_canvas.md` §8.1).

    Thin wrapper over `list_imprints_with_skips`, kept because it is the
    shipped signature. Prefer the pair-returning form: a browsing listing
    that silently drops what it cannot read leaves an operator with no way
    to find out which bundle vanished or why (S5)."""
    return list_imprints_with_skips(fixtures_dir)[0]


def list_imprints_with_skips(
    fixtures_dir: Optional[str | Path] = None,
) -> tuple[list[ImprintCatalogEntry], list[tuple[str, str]]]:
    """`(entries, [(id, reason), ...])`.

    Every entry is a PROJECTION of the summary `GET /api/imprints/{id}`
    serves for it (`_summary_from`, R7), so "lists" implies "summarizes",
    with the same numbers, by construction rather than by two readers
    happening to agree. Four publishable states, all honest states of a real
    bundle:

      * a NORMALIZED sidecar with its `summary` block (the packaged fixture);
      * a CANONICAL sidecar (`has_provenance: true`), whose per-field finite
        confidences are meaned for the headline number (S1) -- without this
        branch the one bundle that ships full provenance would be the one
        bundle the Console cannot see;
      * an UNRECOGNIZED sidecar (`has_provenance: true`, nothing consumed),
        as `GET .../cast` opens it too;
      * NO sidecar at all (`has_provenance: false`, `mean_confidence: 0.0`
        with `confidence_source: "absent"`), the case D3 was written for.

    Everything else is SKIPPED with the same reason the summary route answers
    with (S5): an unreadable sidecar, one with no `fields` object (the same
    words `GET .../cast` refuses it in), a normalized one missing a required
    stat or declaring ANOTHER imprint's id (S1), a directory whose twin
    config declares another id. A topology export's baked id that disagrees
    is NOT a skip (R7, D3): it is a warning on the summary route.
    """
    root = Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES_DIR
    if not root.is_dir():
        return [], []
    out: list[ImprintCatalogEntry] = []
    skipped: list[tuple[str, str]] = []
    for d in sorted(root.iterdir()):
        # `.`-hidden, and `_`-prefixed: a `twins/` root's cross-twin assets
        # folder (`twins/_shared/`) is by convention never a twin.
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        if not is_safe_catalog_id(d.name):
            skipped.append((d.name, "not a usable catalog id"))
            continue
        try:
            config = _config_path(d, d.name)
        except ImprintDataError as e:
            skipped.append((d.name, str(e)))
            continue
        if not config.is_file():
            skipped.append((d.name, f"no {config.name} -- not an imprint bundle"))
            continue
        # ONE read of each artifact per bundle (S4): the sidecar is read here
        # and handed to the builder, which never re-reads it.
        try:
            s = _summary_from(d, d.name, sidecar=_read_sidecar(d, d.name))
        except ImprintError as e:
            skipped.append((d.name, str(e)))
            continue
        out.append(
            # `id=d.name` -- the addressable id a caller will `GET`, which the
            # addressing guard has just proved equal to `twin_config.id`
            # (LES-195).
            ImprintCatalogEntry(
                id=d.name, as_of=s.as_of, hosts=s.hosts, segments=s.segments,
                mean_confidence=s.mean_confidence,
                has_provenance=s.sidecar_shape != SIDECAR_ABSENT,
                confidence_source=s.confidence_source,
                pit_id=s.pit_id,
                has_topology_export=_artifact_path(d, f"{d.name}.topology.json").is_file(),
            )
        )
    return sorted(out, key=lambda e: e.id), skipped


def _canonical_confidence(value: Any) -> Optional[float]:
    """ONE rule for a canonical record's `confidence`, shared by the summary's
    mean and the provenance rows (GAP-082 review round 2): a FINITE JSON
    number (`axrange.jsonnum.is_finite_number`) is a confidence. Anything
    else that is present -- NaN, Infinity, an integer beyond any float, a
    boolean, a string such as "0.5" -- returns `None` and both callers treat
    it as ABSENT and COUNT it, so no value is ever shown on a row and left out
    of the mean, or the reverse. A missing key is simply absent."""
    if value is None or not is_finite_number(value):
        return None
    return float(value)


def _canonical_confidence_stats(raw: dict[str, Any]) -> tuple[float, str, int]:
    """S1: the canonical sidecar's mean over the fields that CARRY a
    confidence (`_canonical_confidence`), with the coverage in the source
    string -- `(mean, confidence_source, ignored_count)`.

    A mean over "all fields, absent counted as zero" would be arithmetic
    about a number nothing measured; a flat 0.0 said the best-evidenced
    bundle in the catalog knows nothing. The pair -- the mean plus
    "canonical sidecar (151/687 fields)" -- is the honest publication.

    A confidence that is present but not a finite number is IGNORED and
    COUNTED (GAP-082 review): averaged in, a NaN made the mean non-finite,
    which serializes as `null` and breaks the non-null `mean_confidence` the
    shipped plugin calls `.toFixed(2)` on (Python's own `json.dump` writes
    `NaN` by default). The source string names what was ignored, so the
    number is never read as a mean over more than it is."""
    fields = raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        return 0.0, CONFIDENCE_ABSENT, 0
    values: list[float] = []
    ignored = 0
    for rec in fields.values():
        if not isinstance(rec, dict) or rec.get("confidence") is None:
            continue
        number = _canonical_confidence(rec["confidence"])
        if number is None:
            ignored += 1
        else:
            values.append(number)
    if not values:
        return 0.0, CONFIDENCE_ABSENT, ignored
    mean = sum(values) / len(values)
    if not math.isfinite(mean):  # finite terms whose SUM overflowed
        mean = sum(v / len(values) for v in values)
    note = f"; {ignored} ignored, not a finite number" if ignored else ""
    return (
        round(mean, 6),
        f"{CONFIDENCE_FROM_CANONICAL} ({len(values)}/{len(fields)} fields{note})",
        ignored,
    )


def _canonical_mean_confidence(raw: dict[str, Any]) -> tuple[float, str]:
    """The pair-returning spelling of `_canonical_confidence_stats`, kept for
    callers that do not report the ignored count."""
    mean, source, _ignored = _canonical_confidence_stats(raw)
    return mean, source


def _iso_utc(as_of: Any) -> str:
    """The export writes `as_of` as an epoch float; the catalog publishes a
    string. Same instant, rendered -- not a derived or rounded fact."""
    from datetime import datetime, timezone

    if isinstance(as_of, str):
        return as_of
    try:
        return datetime.fromtimestamp(float(as_of), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


# ---------------------------------------------------------------------------
# The compiled bundle (S1.2) + the two-shape provenance adapter (S1.4)
# ---------------------------------------------------------------------------
def load_reference(imprint_id: str, fixtures_dir: Optional[str | Path] = None):
    """One reader for the cast bundle: `axrange.fidelity.reference`
    (LES-025/LES-037 -- it already parses the twin config, the topology
    export and the canonical sidecar, cross-checks the directory name, and
    reports what is absent). This module adapts its output to the imprint
    endpoints' vocabulary; it does not re-parse anything.

    PUBLIC on purpose (S4): `service.imprint_apply` loads the bundle once and
    derives both its summary and its history snapshot from that one object,
    where it used to call `load_cast_summary` twice and `load_reference_bundle`
    a third time for one request.

    `ReferenceError` is mapped to this module's own typed error so
    `service.py`'s existing `except ImprintDataError` -> HTTP 400 keeps
    covering it (LES-110).

    The addressing guard runs FIRST (GAP-082 review round 2, SF4): the
    fidelity reader accepts any `*.yaml` in the directory that declares the
    directory's id, so a bundle with no `<id>.yaml` beside a compiler-named
    `twin_config.yaml`, or an `<id>.yaml` naming another twin beside a stray
    file naming this one, used to open on `/cast` and `/provenance` while
    every other route refused it -- the second served another twin's content
    under this id (LES-195). The guard's cached parse is then HANDED to the
    fidelity reader, which would otherwise parse the same file twice more."""
    from axrange.fidelity.reference import ReferenceError, load_reference_bundle

    dir_, tc = _addressable_bundle(imprint_id, fixtures_dir)
    try:
        return load_reference_bundle(dir_, expected_id=imprint_id, twin_config=tc)
    except ReferenceError as e:
        raise ImprintDataError(f"imprint {imprint_id!r}: {e}") from e


def _reference_bundle(imprint_id: str, fixtures_dir: Optional[str | Path]):
    """Back-compat spelling of `load_reference`."""
    return load_reference(imprint_id, fixtures_dir)


def load_cast_summary(
    imprint_id: str, *, fixtures_dir: Optional[str | Path] = None,
) -> CastSummary:
    """`GET /api/imprints/{id}/cast` -- the compiled bundle's summary
    (`docs/WORK_ORDER_console_canvas.md` S1.2)."""
    return cast_summary_from_bundle(load_reference(imprint_id, fixtures_dir), imprint_id)


def cast_summary_from_bundle(bundle: Any, imprint_id: str) -> CastSummary:
    """The summary derived from an ALREADY-LOADED bundle (S4)."""
    from axrange.fidelity.reference import PRESENT, SIDECAR_ABSENT_WARNING
    from axrange.twinconfig.gate import DEFAULT_MIN_OCCUPANCY, PARAM_MIN_OCCUPANCY

    from axrange.api.console_files import assertions_path, layout_path

    tc = bundle.twin_config
    params = dict(tc.params or {})

    declared = params.get(PARAM_MIN_OCCUPANCY)
    if declared is None:
        min_occupancy = DEFAULT_MIN_OCCUPANCY
        occupancy_source = "engine default"
    else:
        min_occupancy = _coerce_param_float(
            declared, imprint_id=imprint_id, field=f"params.{PARAM_MIN_OCCUPANCY}",
        )
        occupancy_source = "declared"

    prov_path = _artifact_path(bundle.bundle_dir, f"{imprint_id}.provenance.json")
    has_provenance = bundle.provenance_sidecar == PRESENT
    warnings = [console_warning(w) for w in bundle.warnings]
    if not has_provenance and console_warning(SIDECAR_ABSENT_WARNING) not in warnings:
        warnings.append(console_warning(SIDECAR_ABSENT_WARNING))

    return CastSummary(
        id=bundle.bundle_id,
        bundle_dir=str(bundle.bundle_dir),
        twin_config_id=tc.id,
        description=tc.description,
        pit_id=bundle.pit_id,
        bundle_schema=bundle.bundle_schema,
        topology_schema_version=bundle.topology_schema_version,
        code_fingerprint=bundle.code_fingerprint,
        params=params,
        deploy_min_occupancy=min_occupancy,
        deploy_min_occupancy_source=occupancy_source,
        segments=len(bundle.twin_segments),
        node_specs=len(bundle.groups),
        vms=bundle.expanded_nodes,
        hosts=bundle.expanded_hosts,
        conduits=len(tc.conduits or []),
        observed_nodes=len(bundle.observed_nodes),
        observed_edges=len(bundle.observed_edges),
        observed_segments=len(bundle.observed_segments),
        census=dict(bundle.census),
        has_provenance=has_provenance,
        has_topology_export=bundle.topology_export == PRESENT,
        has_assertions=assertions_path(bundle.bundle_dir, imprint_id).is_file(),
        has_layout=layout_path(bundle.bundle_dir).is_file(),
        provenance_schema=bundle.provenance_schema,
        provenance_path=str(prov_path) if has_provenance else None,
        sidecar_shape=_sidecar_shape(bundle.provenance_sidecar, bundle.provenance),
        evidence_source=bundle.evidence_source,
        warnings=warnings,
        # R5 (GAP-079): the roster behind the `conduits` count, and the
        # gateway's legs -- from the SAME twin config the counts came from,
        # never a second read.
        conduit_roster=[CastConduit(from_=c.from_, to=c.to) for c in (tc.conduits or [])],
        routers=_routers(bundle.groups),
        # Console S5.1.a: the roster behind the `segments` count, from the SAME
        # twin config -- `bundle.twin_segments` is built from `tc.segments`
        # too, but drops `zone_type`, so the config itself is read.
        segment_roster=[
            CastSegment(name=seg.name, cidr=seg.cidr, zone_type=seg.zone_type)
            for seg in (tc.segments or [])
        ],
        node_roster=[
            CastNode(
                name=g.name, kind=g.kind, role=g.role, count=g.count,
                segments=tuple(g.segments), node_ids=tuple(g.node_ids),
            )
            for g in bundle.groups
        ],
    )


def _routers(groups: Any) -> list[CastRouter]:
    """R5: every ROUTER the engine would build, in declaration order, with
    the segments it has a leg on -- each once, spelled as the config spells
    them. `kind == "router"` exactly -- the test `axrange.twinconfig.expand`
    itself applies (and `_host_counts_by_segment` above).

    One entry per EXPANDED router (GAP-082 review round 2): a `count: 2` spec
    fans out to `gw01` and `gw02`, and the Console -- which names "the
    gateway" only for exactly one router -- counts routers that exist. Read
    off the fidelity reader's `TwinGroup`s, never re-derived: each already
    carries the spec's instance ids (the expander's own `instance_ids`, under
    the reader's one lenient count rule, which WARNS on a template count), so
    a second copy of that rule here would be a second place for it to drift.
    Per router, never one flattened union (S5): a union cannot say whether
    there is one gateway or three."""
    out: list[CastRouter] = []
    for group in groups:
        if group.kind != "router":
            continue
        legs = tuple(dict.fromkeys(seg for seg in group.segments if seg))
        out.extend(CastRouter(name=node_id, segments=legs) for node_id in group.node_ids)
    return out


def _sidecar_shape(sidecar_state: str, raw: Optional[dict[str, Any]]) -> str:
    """`GET .../cast`'s statement of the sidecar's shape: `_detect_sidecar_
    shape` itself -- the summary's rule -- applied to the DOCUMENT the
    fidelity reader read, never to a stringified `schema` (GAP-082 review
    round 2: a non-string `schema` is unrecognized, and `str(5)` is a schema
    name the producer never wrote)."""
    from axrange.fidelity.reference import PRESENT

    if sidecar_state != PRESENT:
        return SIDECAR_ABSENT
    return _detect_sidecar_shape(raw if isinstance(raw, dict) else {})


def load_provenance(
    imprint_id: str, *, fixtures_dir: Optional[str | Path] = None,
) -> ProvenanceDoc:
    """The provenance endpoint's reader: DETECT the sidecar shape, then read
    it with the reader that owns it (`docs/WORK_ORDER_console_canvas.md`
    S1.4). Four outcomes, each stated in `sidecar_shape`:

      * `canonical`  -- `axr-cast-provenance/1`, read via
                        `axrange.fidelity.reference` and folded to rows here.
      * `normalized` -- the Track-C fixture shape (no `schema` key).
      * `unrecognized` -- any other `schema`: present, NOT consumed (zero
                        rows, the fidelity reader's own warning), never fed
                        to the strict normalized reader (GAP-082 review).
      * `absent`     -- no sidecar at all. Zero rows and a 200, not a 400:
                        a real cast bundle installed before the sidecar was
                        copied legitimately has none, and the Console must be
                        able to render it while SAYING so.

    ONE load per request (S4): the bundle is read once and every branch reads
    the sidecar out of it, where this used to parse the sidecar itself and
    then hand the whole bundle -- 732 KB of topology export included -- to a
    second parser for the same file.
    """
    from axrange.fidelity.reference import PRESENT, SIDECAR_ABSENT_WARNING, SOURCE_SIDECAR

    bundle = load_reference(imprint_id, fixtures_dir)
    if bundle.provenance_sidecar != PRESENT:
        return ProvenanceDoc(
            imprint_id=imprint_id, sidecar_shape=SIDECAR_ABSENT,
            sidecar_schema=None,
            # The ONE definition of this string lives in
            # `fidelity/reference.py` -- a second literal here would drift
            # from the scorecard's, and the two are meant to be the same
            # statement about the same absence (LES-037).
            evidence_source=bundle.evidence_source,
            rows=[], warnings=[console_warning(SIDECAR_ABSENT_WARNING)],
        )

    raw = dict(bundle.provenance)
    shape = _detect_sidecar_shape(raw)
    if shape == SIDECAR_UNRECOGNIZED:
        return ProvenanceDoc(
            imprint_id=imprint_id, sidecar_shape=SIDECAR_UNRECOGNIZED,
            sidecar_schema=bundle.provenance_schema,
            evidence_source=bundle.evidence_source,
            rows=[],
            # The fidelity reader's own "not consumed" statement.
            warnings=[console_warning(w) for w in bundle.warnings],
        )
    if shape == SIDECAR_CANONICAL:
        rows, warnings = _rows_from_canonical(bundle, imprint_id=imprint_id)
        return ProvenanceDoc(
            imprint_id=imprint_id, sidecar_shape=SIDECAR_CANONICAL,
            sidecar_schema=bundle.provenance_schema,
            evidence_source=bundle.evidence_source,
            rows=rows,
            warnings=[console_warning(w) for w in bundle.warnings] + warnings,
            population=dict(bundle.population),
            code_fingerprint=bundle.code_fingerprint,
        )

    # The normalized shape carries its own `imprint_id`: the ADDRESSING guard
    # and the sidecar-id check run through the SAME helpers the summary uses
    # (nit: one spelling of each message) -- a different id refuses, a
    # missing one is a warning (S1).
    _check_addressing_id(imprint_id, bundle.twin_config)
    id_warnings = _sidecar_id_check(imprint_id, raw)
    rows = _rows_from_normalized(raw, imprint_id=imprint_id)
    return ProvenanceDoc(
        imprint_id=imprint_id, sidecar_shape=SIDECAR_NORMALIZED,
        # The normalized shape carries no `schema` key -- that is how it is
        # detected -- so there is nothing to publish here.
        sidecar_schema=None,
        # The classes ARE the producer's here (a hand-written fixture stands
        # in for the producer) -- nothing is re-derived, so no LES-219 caveat.
        evidence_source=SOURCE_SIDECAR,
        rows=rows,
        warnings=id_warnings + _class_warnings(rows, imprint_id=imprint_id),
    )


def _rows_from_canonical(bundle: Any, *, imprint_id: str) -> tuple[list[ProvenanceRow], list[str]]:
    """Fold a canonical sidecar's `fields` records into published rows.

    The canonical record has no single `evidence` column; `source` (where the
    value came from: `catalog/roles.yaml`, `role census`, `operator: name`)
    is its closest analogue, with the compiler's `reason`/`note` behind it.
    Both are ALSO published under their own names, so nothing is lost to the
    compatibility column."""
    fields = (bundle.provenance or {}).get("fields")
    if not isinstance(fields, dict):  # pragma: no cover -- reference.py raises first
        raise ImprintDataError(f"imprint {imprint_id!r}: provenance sidecar has no 'fields'")

    rows: list[ProvenanceRow] = []
    ignored = 0
    for path, rec in sorted(fields.items()):
        if not isinstance(rec, dict):
            raise ImprintDataError(
                f"imprint {imprint_id!r}: provenance field {path!r} is not an object"
            )
        cls = rec.get("evidence_class") or rec.get("class") or ""
        raw_conf = rec.get("confidence")
        # The SAME rule the summary's mean applies (round 2): a present value
        # that is not a finite number is absent here and counted.
        number = _canonical_confidence(raw_conf)
        if raw_conf is not None and number is None:
            ignored += 1
        source = _coerce_optional_str(
            rec.get("source"), imprint_id=imprint_id, field=f"fields[{path!r}].source",
        )
        note = rec.get("note") if isinstance(rec.get("note"), str) else None
        reason = rec.get("reason") if isinstance(rec.get("reason"), str) else None
        rows.append(
            ProvenanceRow(
                field=str(path),
                signal=str(rec.get("signal") or ""),
                evidence_class=str(cls).strip().lower(),
                # 0.0 is the documented non-null placeholder when there is no
                # confidence; `has_confidence` says which.
                confidence=number if number is not None else 0.0,
                evidence=str(source or reason or note or ""),
                has_confidence=number is not None,
                source=source,
                note=note or reason,
                overrides=_override_from(rec.get("overrides"), imprint_id=imprint_id, path=path),
            )
        )
    warnings = _class_warnings(rows, imprint_id=imprint_id)
    if ignored:
        warnings.append(
            f"imprint {imprint_id!r}: {ignored} provenance row(s) carry a "
            "`confidence` that is not a finite number (NaN, Infinity, out of "
            "range, a boolean or a string) -- published as `has_confidence: "
            "false`, never as a number, and left out of the summary's mean by "
            "the same rule"
        )
    return rows, warnings


def _override_from(
    raw: Any, *, imprint_id: str, path: str,
) -> Optional[ProvenanceOverride]:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ImprintDataError(
            f"imprint {imprint_id!r}: provenance field {path!r} has a "
            f"non-object `overrides` record: {raw!r}"
        )
    drift = raw.get("drift")
    return ProvenanceOverride(
        # The sidecar spells the losing class `class` OR `evidence_class`
        # depending on its vintage; read both, publish one.
        evidence_class=_coerce_optional_str(
            raw.get("evidence_class") if raw.get("evidence_class") is not None
            else raw.get("class"),
            imprint_id=imprint_id, field=f"fields[{path!r}].overrides.class",
        ),
        value=raw.get("value"),
        confidence=_coerce_optional_finite_float(
            raw.get("confidence"), imprint_id=imprint_id,
            field=f"fields[{path!r}].overrides.confidence",
        ),
        drift=bool(drift) if drift is not None else None,
    )


def _class_warnings(rows: list[ProvenanceRow], *, imprint_id: str) -> list[str]:
    """Two statements about the published rows, both measured rather than
    assumed: any class outside the six, and how many rows carry no
    confidence, BY CLASS (S1).

    The second exists because `has_confidence: false` was being read as "this
    is a compiler row", which the real data flatly contradicts -- on
    `corpapcn-golden`, 250 of 333 `observed` rows carry no confidence. A
    consumer shading a bar by `mean(confidence)` over all rows needs the
    denominator said out loud."""
    out: list[str] = []
    unknown = sorted({r.evidence_class for r in rows
                      if r.evidence_class and r.evidence_class not in EVIDENCE_CLASSES})
    if unknown:
        out.append(
            f"imprint {imprint_id!r}: provenance carries evidence class(es) "
            f"{unknown} outside the six-class vocabulary {list(EVIDENCE_CLASSES)} "
            "-- published as-is, never rewritten"
        )
    missing: dict[str, int] = {}
    for r in rows:
        if not r.has_confidence:
            missing[r.evidence_class or "(unclassified)"] = (
                missing.get(r.evidence_class or "(unclassified)", 0) + 1
            )
    if missing:
        by_class = ", ".join(f"{cls}: {n}" for cls, n in sorted(missing.items()))
        out.append(
            f"imprint {imprint_id!r}: {sum(missing.values())} of {len(rows)} "
            f"provenance row(s) carry no `confidence` ({by_class}) -- "
            "`has_confidence: false` means only that, never 'a compiler row', "
            "and any mean over these rows must exclude them"
        )
    return out


def _unknown_class_warnings(
    pairs: list[tuple[str, str]], *, imprint_id: str,
) -> list[str]:
    """Report, never rewrite: a class outside the six is the producer and the
    consumer having drifted, and silently normalizing it is how that goes
    unnoticed (`docs/design/14-operator-assertions.md` §2). Kept as the
    pair-taking spelling for callers that have no rows to hand."""
    unknown = sorted({cls for _f, cls in pairs if cls and cls not in EVIDENCE_CLASSES})
    if not unknown:
        return []
    return [
        f"imprint {imprint_id!r}: provenance carries evidence class(es) "
        f"{unknown} outside the six-class vocabulary {list(EVIDENCE_CLASSES)} "
        "-- published as-is, never rewritten"
    ]
