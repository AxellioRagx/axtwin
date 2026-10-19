"""The REFERENCE side of the scorecard — the cast bundle on the cross-repo
file seam (`docs/WORK_ORDER_fidelity_scorecard.md` §2/§6 S1;
`docs/design/13-golden-twin-fidelity.md` §7).

A cast bundle is a directory `<root>/<id>/` holding up to three documents
produced by aximprint's compiler and installed by
`install_cast_as_twin.py`:

    <id>.yaml              the twin config (a genuine axRange `twin_config`)
    <id>.topology.json     the OBSERVED superset (nodes/edges/segments/windows)
    <id>.provenance.json   the per-field provenance sidecar   <- absent on
                           bundles installed before the installer copied it

A `twins/<id>/` folder holds the same three: the manifest's te file
(`te.yaml`) and `generated/<id>.{topology,provenance}.json`
(`axrange.twinconfig.bundle`, the one layout rule).

**No import in either direction, ever** (`docs/design/aximprint-integration/
WORK_ORDERS.md` §0): this module reads three FILES. It never imports the
`aximprint` package, and it is deliberately NOT
`axrange/api/imprints.py` — that reader consumes the Track-C *normalized*
sidecar shape the Console's provenance table wants, while the canonical
shape here is the one the real compiler emits (`{schema, pit_id,
code_fingerprint, vantage, population, fields}` with `overrides:` records;
§9.6, ruled 2026-09-04). Collapsing the two readers is a separate change
(work order §5).

**A sidecar that is absent is REPORTED, not papered over** (§8.1). A bundle
installed by a current `install_cast_as_twin.py` carries its sidecar and its
evidence classes are read from the producer (`twins/
corpapcn-golden/`, 2026-09-05: `axr-cast-provenance/1`, 687 fields, no
warnings). An OLDER bundle — installed before the installer copied it, or
produced by an older cast — has none, and then every evidence class the
scorecard publishes for rows 1/2/3 is RE-DERIVED by axRange from the
topology export's own `*_evidence_class` fields instead. That is the
imputed-as-observed hazard the spec exists to prevent, so
`provenance_sidecar: "absent"` goes in the document and a loud line goes in
`warnings` — never a silent re-derivation. The warning is attached to the
DERIVATION, not to the file's absence (LES-219), so it keeps firing for a
sidecar that lands but cannot be consumed.

Id resolution (LES-195/LES-198): the bundle is addressed by its DIRECTORY
NAME, `bundle_id` publishes the name THIS lookup resolved by, and the
content-declared `twin_config.id` is cross-checked against it (fail loud on
disagreement). The topology export's own `imprint_id` is a **PIT id**
(`pit-63994c9543-1787823960`) by producer design, not a bundle id — it is
published as `pit_id` and never compared against the directory name. The
caller PINS the directory (`--reference <dir>`); the default is a single
named root (`twins/`), never a first-hit chain across catalogs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from axrange.jsonnum import NumberFieldError, optional_finite_number, optional_whole_number
from axrange.twinconfig import (
    TwinConfig,
    TwinConfigError,
    TwinConfigNotFoundError,
    list_twin_configs,
    load_twin_config,
)
from axrange.twinconfig.bundle import bundle_artifact_path

# <repo-root>/twins -- axrange/fidelity/reference.py's parents:
# [0]=fidelity [1]=axrange [2]=repo root. Mirrors
# `twinconfig.manifest._DEFAULT_TWINS_DIR`'s derivation exactly. Was
# `twin-configs-cast/` until the twin consolidation moved the casts into
# `twins/<id>/` (`docs/WORK_ORDER_twin_consolidation.md` Phase 3); a legacy
# bundle is still read when pinned with `--reference <dir>`.
_DEFAULT_REFERENCE_ROOT = Path(__file__).resolve().parents[2] / "twins"

# What `reference.provenance_sidecar` / `.topology_export` publish. Stated
# values, never an absent key a reader has to interpret.
PRESENT = "present"
ABSENT = "absent"

# The canonical sidecar the real compiler emits (`aximprint synth/
# compiler.py`). A sidecar carrying any other `schema` -- notably the Track-C
# NORMALIZED fixture shape `axrange/api/imprints.py` reads -- is reported and
# NOT consumed for evidence classes: two shapes disagree (work order §2/§9.1),
# and reading one as the other is precisely the fabrication this file exists
# to avoid.
CAST_PROVENANCE_SCHEMA = "axr-cast-provenance/1"

# What `reference.evidence_source` publishes. Not decoration: the day the
# sidecar lands, a reader must be able to tell "these classes came from the
# producer" from "axRange re-derived them" WITHOUT inferring it from the
# presence of a warning (which is exactly how the distinction would go
# missing -- the warning is attached to the absence, the classes are used
# either way).
SOURCE_REDERIVED = "re-derived from topology export"
SOURCE_SIDECAR = "sidecar fields"

REDERIVED_WARNING = (
    "evidence classes are RE-DERIVED by axRange from the topology export, "
    "not read from the producer (docs/WORK_ORDER_fidelity_scorecard.md §8.1)"
)
# NOTE (2026-09-05): this used to assert `install_cast_as_twin.py` copies
# topology.json but NOT provenance.json. It does copy it now (aximprint D9),
# and `twins/corpapcn-golden/` (was `twin-configs-cast/`) is the proof. The honest statement
# is about THIS bundle -- older than that change, or produced by an older
# cast -- never about what the installer does in general.
SIDECAR_ABSENT_WARNING = (
    "provenance sidecar absent (this bundle was installed before the "
    "installer copied the sidecar, or was produced by an older cast) -- "
    + REDERIVED_WARNING
)


class ReferenceError(Exception):
    """A cast bundle could not be read (missing, malformed, or self-
    inconsistent). Typed so every caller's `except ReferenceError` covers it
    and a bad file never surfaces as a bare `ValueError`/`KeyError`
    traceback at the CLI/API layer (LES-110)."""


def default_reference_root() -> Path:
    return _DEFAULT_REFERENCE_ROOT


# ---------------------------------------------------------------------------
# Observed-side views (the topology export)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservedNode:
    """One `topology.json` `nodes[]` entry. `segment` is a CIDR; the segment
    NAME (`seg-10-80-20-0`) is joined through `ObservedSegment.cidr` -- the
    twin config's own segment CIDRs are RELOCATED away from the deployment
    target's address space (a declared deployment transformation, `docs/
    design/14-operator-assertions.md` §2), so a CIDR-to-CIDR join between
    the two sides would match nothing at all."""

    id: str
    address: Optional[str]
    segment: Optional[str]          # CIDR, as the export writes it
    segment_name: Optional[str]     # joined via the export's own segments[]
    role: Optional[str]
    role_confidence: Optional[float]
    role_evidence_class: Optional[str]
    segment_evidence_class: Optional[str]
    locality: Optional[str]
    windows_seen: int
    listen_ports: tuple[str, ...] = ()
    dial_ports: tuple[str, ...] = ()

    @property
    def placed(self) -> bool:
        """A host the cast could place: seen locally AND assigned a segment.
        The census's own `placed` count (141 on CorpAPCN) is this set."""
        return self.locality == "local" and bool(self.segment)


@dataclass(frozen=True)
class ObservedSegment:
    name: str
    cidr: Optional[str]
    entities: Optional[int] = None
    coverage_source: Optional[str] = None


@dataclass(frozen=True)
class ObservedEdge:
    src: str
    dst: str
    proto: Optional[str]
    flows: Optional[int]
    bytes: Optional[int]
    evidence_class: Optional[str]


# ---------------------------------------------------------------------------
# Twin-side views (the cast's twin config)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TwinGroup:
    """One `nodes[]` spec of the cast's twin config -- a `(role, segment)`
    GROUP with a `count:`, which is all the per-host identity that survives
    a cast (§3.2, and the reason row 1 is group-granular)."""

    name: str
    kind: str
    role: Optional[str]
    count: int
    segments: tuple[str, ...]         # every leg's segment name
    install: tuple[dict[str, Any], ...] = ()
    node_ids: tuple[str, ...] = ()    # the ids the expander fans this out to
    observation: Optional[Any] = None

    @property
    def segment(self) -> Optional[str]:
        return self.segments[0] if self.segments else None

    @property
    def is_router(self) -> bool:
        return (self.kind or "host").lower() == "router"


def _instance_ids_for(name: str, count: int) -> list[str]:
    """The node ids a spec fans out to.

    Imported from the EXPANDER rather than reimplemented: row 3 compares
    declared installs against `provision-app`/`run-script` op rows keyed by
    the real deployed `host_id`, so this rule must be the SAME rule
    `expand.py` used to mint those ids -- a second copy that drifts by one
    character silently reads a correct twin as broken (LES-037).
    """
    from axrange.twinconfig import instance_ids

    return instance_ids(name, count)


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------
@dataclass
class ReferenceBundle:
    bundle_id: str                    # the DIRECTORY name this lookup resolved by
    bundle_dir: Path
    twin_config: TwinConfig
    groups: list[TwinGroup] = field(default_factory=list)
    twin_segments: list[ObservedSegment] = field(default_factory=list)
    observed_nodes: list[ObservedNode] = field(default_factory=list)
    observed_segments: list[ObservedSegment] = field(default_factory=list)
    observed_edges: list[ObservedEdge] = field(default_factory=list)
    windows: Optional[int] = None
    census: dict[str, Any] = field(default_factory=dict)
    vantage: Optional[str] = None
    network: Optional[str] = None
    as_of: Optional[Any] = None
    pit_id: Optional[str] = None
    topology_schema_version: Optional[str] = None
    # The export's own BUNDLE schema (`axr-topology/1.4`), published from
    # 2026-09-05 casts and absent from older ones -- reported, never derived
    # from `schema_version` (which is the network-IR version, a different
    # number: the golden bundle carries `1.4` and `1.3` at once).
    bundle_schema: Optional[str] = None
    # The export's `cast_invocation` block (`axr-topology/1.5`+) -- the flags
    # the cast that produced this bundle actually ran with. Published RAW and
    # unvalidated: `axrange.api.recompile.read_cast_invocation` owns its
    # shape, and it is here only so that reader does not have to re-parse a
    # 732 KB export a caller has already loaded. `None` on an older bundle.
    cast_invocation: Optional[dict[str, Any]] = None
    topology_export: str = ABSENT
    provenance_sidecar: str = ABSENT
    provenance_schema: Optional[str] = None
    # The sidecar's `code_fingerprint`, which is the **PIT row's** build
    # stamp -- the identity of the aximprint run that materialized the
    # CAPTURE, not the build that ran the cast (GAP-088, corrected
    # 2026-09-16: this comment used to say "the compiler's own build
    # fingerprint", and a re-cast by an upgraded compiler leaves this value
    # untouched, which is why it could not be the tell). The build that ran a
    # cast is `cast_invocation.code_fingerprint` on the field above. NOT the
    # `<id>.fingerprint.json` SHAPE fingerprint rows 8/9/10 need (§8.2) --
    # that one is a separate, still-absent seam file (`to_dict`'s
    # `fingerprint` key), and conflating the two would report rows 8/9/10 as
    # computable the day a sidecar lands.
    code_fingerprint: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    # Twin-config field PATH -> evidence class, read from a canonical
    # sidecar's `fields` block (`twin_config.nodes[3].image` -> `compiler`).
    # Empty whenever the classes are re-derived instead.
    field_classes: dict[str, str] = field(default_factory=dict)
    # Paths whose sidecar record carries an `overrides:` entry -- a higher
    # class winning over a lower one. Published because the house rule is
    # "an override is visible, never an erasure" (`docs/design/
    # 14-operator-assertions.md` §2).
    overridden_fields: list[str] = field(default_factory=list)
    evidence_source: str = SOURCE_REDERIVED
    population: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # -- derived counts ---------------------------------------------------
    @property
    def expanded_nodes(self) -> int:
        """Every node the config expands to, routers included (138 on the
        shipped CorpAPCN bundle)."""
        return sum(g.count for g in self.groups)

    @property
    def expanded_hosts(self) -> int:
        """Non-router nodes only (137 on CorpAPCN, 42 on the corp-enterprise
        fixture). Published ALONGSIDE `expanded_nodes` because the two
        answer different questions and conflating them is how a router
        silently becomes a host in a coverage count (LES-210)."""
        return sum(g.count for g in self.groups if not g.is_router)

    @property
    def placed_observed(self) -> list[ObservedNode]:
        return [n for n in self.observed_nodes if n.placed]

    def classes_for_prefix(self, *prefixes: str) -> list[str]:
        """Every sidecar-declared evidence class whose field path starts with
        one of `prefixes`. Empty when no canonical sidecar was consumed, which
        is what makes a row's fallback explicit rather than accidental."""
        out: list[str] = []
        for path in sorted(self.field_classes):
            if any(path.startswith(pre) for pre in prefixes):
                out.append(self.field_classes[path])
        return out

    def to_dict(self) -> dict[str, Any]:
        """The scorecard document's `reference` block (§4.1)."""
        return {
            "bundle": str(self.bundle_dir),
            "bundle_id": self.bundle_id,
            "pit_id": self.pit_id,
            "network": self.network,
            "vantage": self.vantage,
            "windows": self.windows,
            "as_of": self.as_of,
            "census": dict(self.census),
            "topology_schema_version": self.topology_schema_version,
            "topology_export": self.topology_export,
            "provenance_sidecar": self.provenance_sidecar,
            "provenance_schema": self.provenance_schema,
            # Where the per-fact evidence classes in every row's mix came
            # from. Stated, never inferred from the warnings list.
            "evidence_source": self.evidence_source,
            "provenance_fields": len(self.field_classes),
            "overridden_fields": list(self.overridden_fields[:50]),
            "overridden_field_count": len(self.overridden_fields),
            # §8.2 -- the fingerprint seam file rows 8/9/10 need. Always
            # absent until aximprint ships it; stated, never assumed.
            "fingerprint": ABSENT,
            "twin_groups": len(self.groups),
            "twin_segments": len(self.twin_segments),
            "expanded_nodes": self.expanded_nodes,
            "expanded_hosts": self.expanded_hosts,
            "observed_nodes": len(self.observed_nodes),
            "observed_edges": len(self.observed_edges),
            "observed_segments": len(self.observed_segments),
        }


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ReferenceError(f"{what}: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ReferenceError(
            f"{what}: {path}: expected a JSON object, got {type(raw).__name__}"
        )
    return raw


def _coerce_int(
    value: Any, path: Path, field_name: str, default: Optional[int] = 0,
) -> Optional[int]:
    """A whole number from the export -- the ONE bounded rule
    (`axrange.jsonnum`: a JSON number in 0 .. 2**53 - 1, never a string,
    never clamped), `None` reading as `default` (itself `None` for a field
    the export may simply omit) -- refused NAMING the field (GAP-082 review
    round 2, SF2)."""
    try:
        return optional_whole_number(value, field_name, default=default)
    except NumberFieldError as e:
        raise ReferenceError(f"{path.name}: {e}") from e


def _coerce_optional_float(value: Any, path: Path, field_name: str) -> Optional[float]:
    """A finite JSON number or `None` (`axrange.jsonnum`), refused naming the
    field."""
    try:
        return optional_finite_number(value, field_name)
    except NumberFieldError as e:
        raise ReferenceError(f"{path.name}: {e}") from e


def _opt_str(value: Any, path: Path, field_name: str) -> Optional[str]:
    """A string the export carries, or `None` -- anything else refused naming
    the field (GAP-082 review round 2). A list or an object where a CIDR, a
    segment or a ROLE belongs is UNHASHABLE, and 500'd `/cast`, `/provenance`
    and the scorecard as a TypeError the moment it was used as a key
    (`fidelity/rows.py`'s `_group_key(n.role, ...)` is the live one); a number
    was `str()`-ed into an id the producer never wrote. EVERY string-typed
    field of a row goes through this, not only the two a bug has named."""
    if value is None or isinstance(value, str):
        return value
    raise ReferenceError(
        f"{path.name}: `{field_name}` must be a string, got "
        f"{type(value).__name__} {value!r}"
    )


def _ports(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(p) for p in value if p is not None)


def _default_count(raw: Any, tc: Any) -> Optional[int]:
    """A `${NAME:default}` count at its no-override value, or None."""
    if not isinstance(raw, str) or "${" not in raw:
        return None
    from axrange.twinconfig.errors import TwinConfigParamError
    from axrange.twinconfig.params import substitute

    try:
        return int(substitute(raw, dict(getattr(tc, "params", None) or {})))
    except (TwinConfigParamError, TypeError, ValueError):
        return None


def load_reference_bundle(
    bundle_dir: str | Path, *, expected_id: Optional[str] = None,
    twin_config: Optional[TwinConfig] = None,
) -> ReferenceBundle:
    """Load the cast bundle at `bundle_dir`, failing loud on anything
    malformed (LES-110) and reporting -- never inventing -- what is absent.

    `expected_id` (optional) is cross-checked against the resolved directory
    name, so a caller that reached this directory through a deploy's
    `scenario_id` learns immediately if the two disagree instead of
    certifying one twin against another's reference (LES-195).

    `twin_config` (optional): a caller that has ALREADY resolved and parsed
    this bundle's `<id>.yaml` -- `axrange.api.imprints.load_reference`, after
    its addressing guard -- hands the model in, and the scan-and-load below,
    which parses the same file twice, is skipped (GAP-082 review round 2,
    SF1). Its id is still compared with the directory name.
    """
    d = Path(bundle_dir)
    if not d.is_dir():
        raise ReferenceError(f"cast bundle directory not found: {d}")
    bundle_id = d.name
    if expected_id and expected_id != bundle_id:
        raise ReferenceError(
            f"cast bundle {d} resolves to id {bundle_id!r} but the caller "
            f"expected {expected_id!r} -- refusing to certify a twin against "
            "another twin's reference"
        )

    warnings: list[str] = []

    # ---- the twin config (required) ------------------------------------
    if twin_config is None:
        tc = _load_bundle_twin_config(d, bundle_id)
    elif twin_config.id != bundle_id:
        raise ReferenceError(
            f"cast bundle {d}: directory name {bundle_id!r} disagrees with the "
            f"config's own twin_config.id [{twin_config.id!r}] -- serving one "
            "twin's reference under another's id is worse than refusing (LES-195)"
        )
    else:
        tc = twin_config

    groups = []
    for spec in tc.nodes or []:
        try:
            count = max(1, int(spec.count))
        except (TypeError, ValueError):
            # An unsubstituted `${N:25}` template count -- a hand-authored
            # `twin-configs/` config pointed at by `--reference`, not a cast.
            # Resolved to the DEFAULT a deploy with no overrides uses (the
            # config's declared param, else the token's own default) and SAID
            # so -- the same rule the imprint summary applies, so the two
            # never disagree (Unified Console fix round 2). A count that does
            # not resolve is still counted as 1, with a warning.
            resolved = _default_count(spec.count, tc)
            if resolved is None:
                warnings.append(
                    f"twin config node {spec.name!r}: count {spec.count!r} is not a "
                    "number (an unsubstituted param template?) -- counted as 1"
                )
                count = 1
            else:
                warnings.append(
                    f"twin config node {spec.name!r}: count {spec.count!r} is a param "
                    f"template -- counted at its default, {resolved}"
                )
                # No clamp: a param defaulting to 0 means ZERO hosts, which is
                # what the imprint summary (`api.imprints._resolved_count`)
                # counts too -- the two must agree.
                count = max(0, resolved)
        segs = tuple(
            leg.segment for leg in (spec.nets or []) if leg.segment
        ) or ((spec.net,) if spec.net else ())
        groups.append(
            TwinGroup(
                name=spec.name,
                kind=(spec.kind or "host"),
                role=spec.role,
                count=count,
                segments=tuple(s for s in segs if s),
                install=tuple(spec.install or ()),
                node_ids=tuple(_instance_ids_for(spec.name, count)),
                observation=spec.observation,
            )
        )
    twin_segments = [
        ObservedSegment(name=s.name, cidr=s.cidr) for s in (tc.segments or [])
    ]

    bundle = ReferenceBundle(
        bundle_id=bundle_id,
        bundle_dir=d,
        twin_config=tc,
        groups=groups,
        twin_segments=twin_segments,
        warnings=warnings,
    )

    # ---- the topology export (observed superset; optional) -------------
    topo_path = bundle_artifact_path(d, f"{bundle_id}.topology.json")
    if topo_path.is_file():
        _load_topology(bundle, topo_path)
    else:
        bundle.warnings.append(
            f"cast bundle {d}: no {bundle_id}.topology.json -- the OBSERVED "
            "side is unavailable, so rows 1/2/5 cannot be scored"
        )

    # ---- the provenance sidecar (§8.1; expected absent today) ----------
    prov_path = bundle_artifact_path(d, f"{bundle_id}.provenance.json")
    if prov_path.is_file():
        _load_provenance(bundle, prov_path)
    else:
        bundle.provenance_sidecar = ABSENT
        bundle.warnings.append(SIDECAR_ABSENT_WARNING)
    # The warning follows the RE-DERIVATION, not the file's absence: a
    # sidecar that lands but is not consumable (wrong schema, no classes)
    # must not make the warning vanish while the classes are still
    # re-derived -- `_load_provenance` appends its own specific reason above,
    # and this is the invariant that guarantees at least one is present.
    assert (bundle.evidence_source == SOURCE_SIDECAR) or any(
        REDERIVED_WARNING in w for w in bundle.warnings
    ), "a re-derived bundle must carry the re-derivation warning"

    return bundle


def _load_bundle_twin_config(d: Path, bundle_id: str) -> TwinConfig:
    """ONE loader: `axrange.twinconfig.registry`, pinned to THIS bundle
    directory -- the same pinning `confirm_deploy_gate` gets, and the same
    parse/shape-check every other twin-config consumer runs. A second
    hand-rolled YAML+pydantic path here would be a second place for the
    config vocabulary to drift (LES-037)."""
    declared = list_twin_configs(catalog_dir=[d])
    if not declared:
        raise ReferenceError(
            f"cast bundle {d}: no twin config found (looked for a *.yaml with "
            "a top-level `twin_config:` block) -- a bundle without its twin "
            "config has no reference side to score against"
        )
    declared_ids = sorted({e.id for e in declared})
    if bundle_id not in declared_ids:
        raise ReferenceError(
            f"cast bundle {d}: directory name {bundle_id!r} disagrees with the "
            f"config's own twin_config.id {declared_ids} -- serving one twin's "
            "reference under another's id is worse than refusing (LES-195)"
        )
    try:
        return load_twin_config(bundle_id, catalog_dir=[d])
    except TwinConfigNotFoundError as e:
        raise ReferenceError(f"cast bundle {d}: {e}") from e
    except TwinConfigError as e:
        raise ReferenceError(f"cast bundle {d}: {bundle_id}.yaml: {e}") from e


def _load_topology(bundle: ReferenceBundle, path: Path) -> None:
    raw = _read_json(path, f"cast bundle {bundle.bundle_dir}: topology export")
    bundle.topology_export = PRESENT
    bundle.topology_schema_version = (
        str(raw["schema_version"]) if raw.get("schema_version") is not None else None
    )
    bundle.bundle_schema = (
        str(raw["bundle_schema"]) if raw.get("bundle_schema") is not None else None
    )
    invocation = raw.get("cast_invocation")
    bundle.cast_invocation = invocation if isinstance(invocation, dict) else None
    # The export's `imprint_id` is a PIT id by producer design -- published,
    # never cross-checked against the directory name (see module docstring).
    # Only a STRING is published: a non-string (a damaged export) reached
    # `CastSummaryDTO.pit_id: Optional[str]` and 500'd the route. It is
    # reported and published as null instead -- never coerced into an id.
    pit_raw, baked_raw = raw.get("pit_id"), raw.get("imprint_id")
    for key, value in (("pit_id", pit_raw), ("imprint_id", baked_raw)):
        if value is not None and not isinstance(value, str):
            bundle.warnings.append(
                f"{path.name}: `{key}` is not a string ({value!r}) -- not "
                "published as the PIT id (never coerced)"
            )
    if pit_raw is None or pit_raw == "":
        bundle.pit_id = baked_raw if isinstance(baked_raw, str) and baked_raw else None
    else:
        bundle.pit_id = pit_raw if isinstance(pit_raw, str) else None
    bundle.network = raw.get("network")
    bundle.vantage = raw.get("vantage")
    bundle.as_of = raw.get("as_of")
    bundle.census = raw.get("census") if isinstance(raw.get("census"), dict) else {}
    if raw.get("windows") is not None:
        bundle.windows = _coerce_int(raw.get("windows"), path, "windows")

    segs = raw.get("segments")
    if not isinstance(segs, list):
        raise ReferenceError(f"{path}: `segments` must be a list")
    bundle.observed_segments = [
        ObservedSegment(
            name=_opt_str(s.get("name"), path, f"segments[{i}].name"),
            cidr=_opt_str(s.get("cidr"), path, f"segments[{i}].cidr"),
            entities=_coerce_int(
                s.get("entities"), path, f"segments[{i}].entities", default=None,
            ),
            coverage_source=_opt_str(
                s.get("coverage_source"), path, f"segments[{i}].coverage_source",
            ),
        )
        for i, s in enumerate(segs)
        if isinstance(s, dict) and s.get("name")
    ]
    cidr_to_name = {
        s.cidr: s.name for s in bundle.observed_segments if s.cidr
    }

    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        raise ReferenceError(f"{path}: `nodes` must be a list")
    observed: list[ObservedNode] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        seg = _opt_str(n.get("segment"), path, f"nodes[{i}].segment")
        node_id = _opt_str(n.get("id"), path, f"nodes[{i}].id")
        address = _opt_str(n.get("address"), path, f"nodes[{i}].address")
        observed.append(
            ObservedNode(
                id=node_id or address or "",
                address=address,
                segment=seg,
                segment_name=cidr_to_name.get(seg) if seg else None,
                role=_opt_str(n.get("role"), path, f"nodes[{i}].role"),
                role_confidence=_coerce_optional_float(
                    n.get("role_confidence"), path, f"nodes[{i}].role_confidence"
                ),
                role_evidence_class=_opt_str(
                    n.get("role_evidence_class"), path, f"nodes[{i}].role_evidence_class",
                ),
                segment_evidence_class=_opt_str(
                    n.get("segment_evidence_class"), path,
                    f"nodes[{i}].segment_evidence_class",
                ),
                locality=_opt_str(n.get("locality"), path, f"nodes[{i}].locality"),
                windows_seen=_coerce_int(
                    n.get("windows_seen"), path, f"nodes[{i}].windows_seen"
                ),
                listen_ports=_ports(n.get("listen_ports")),
                dial_ports=_ports(n.get("dial_ports")),
            )
        )
    bundle.observed_nodes = observed

    edges = raw.get("edges")
    if not isinstance(edges, list):
        raise ReferenceError(f"{path}: `edges` must be a list")
    bundle.observed_edges = [
        ObservedEdge(
            src=str(e.get("src")), dst=str(e.get("dst")),
            proto=e.get("proto"), flows=e.get("flows"), bytes=e.get("bytes"),
            evidence_class=e.get("evidence_class"),
        )
        for e in edges
        if isinstance(e, dict) and e.get("src") and e.get("dst")
    ]

    # A segment named by a node but absent from `segments[]` would silently
    # drop out of row 2's mapping -- surface it rather than let the join
    # quietly shrink (LES-101's shape: a degenerate filter must be explicit).
    unmapped = sorted(
        {n.segment for n in observed if n.segment and not n.segment_name}
    )
    if unmapped:
        bundle.warnings.append(
            f"{path.name}: {len(unmapped)} observed node segment CIDR(s) have "
            f"no entry in segments[] and cannot be named: {unmapped[:5]}"
        )


def sidecar_fields_or_raise(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    """THE precondition a provenance sidecar of a shape that is READ -- the
    canonical `axr-cast-provenance/1` or the schema-less normalized one --
    must meet before anything reads it: a `fields` object. An unrecognized
    schema is held to none: it is present and not consumed, so it is never
    read (GAP-082 review round 2, SF3). ONE definition (LES-037), shared with
    `axrange.api.imprints`'s summary/listing reader, so `GET .../cast` and
    `GET /api/imprints[/{id}]` refuse the SAME sidecar in the SAME words --
    rather than the listing publishing a bundle whose cast then refuses it
    (GAP-082 review: a canonical sidecar with no `fields` listed and
    summarized 200 while `/cast` answered 400)."""
    fields = raw.get("fields")
    if not isinstance(fields, dict):
        raise ReferenceError(
            f"{path}: a provenance sidecar must carry a `fields` object "
            "(docs/WORK_ORDER_fidelity_scorecard.md §2)"
        )
    return fields


def _not_consumed_warning(path: Path, schema: Any) -> str:
    return (
        f"{path.name}: provenance sidecar schema {schema!r} is not "
        f"{CAST_PROVENANCE_SCHEMA!r} -- its `fields` are NOT consumed (two "
        f"sidecar shapes exist and disagree, work order §2); {REDERIVED_WARNING}"
    )


def _load_provenance(bundle: ReferenceBundle, path: Path) -> None:
    """Read a provenance sidecar when one IS installed (§8.1's future), and
    CONSUME it when it is the canonical shape.

    The canonical shape is the real compiler's, `schema:
    "axr-cast-provenance/1"` with `{pit_id, code_fingerprint, vantage,
    population, fields}` -- `fields` keyed by twin-config PATH
    (`twin_config.nodes[3].image`), each record carrying its evidence class,
    an optional `source`, an optional `confidence` (absent on `compiler`
    rows, which have none by definition) and an optional `overrides:` record
    when a higher class won over a lower one.

    A sidecar carrying some OTHER schema -- notably the Track-C normalized
    fixture shape -- is reported `present` and deliberately NOT consumed:
    two shapes exist and disagree (work order §2), and reading one as the
    other is the imputed-as-observed fabrication the spec exists to prevent.
    `evidence_source` stays `re-derived` and the re-derivation warning stays
    with it, which is the whole point of publishing the field.
    """
    raw = _read_json(path, f"cast bundle {bundle.bundle_dir}: provenance sidecar")
    bundle.provenance = raw
    bundle.provenance_sidecar = PRESENT
    schema = raw.get("schema")
    # A non-string `schema` is UNRECOGNIZED and published as absent, never
    # `str()`-ed into a schema NAME the producer did not write (GAP-082
    # review round 2).
    bundle.provenance_schema = schema if isinstance(schema, str) else None
    if schema is not None and not isinstance(schema, str):
        bundle.warnings.append(
            f"{path.name}: provenance sidecar `schema` is not a string "
            f"({type(schema).__name__} {schema!r}) -- an unrecognized sidecar, "
            "never coerced into a schema name"
        )
    bundle.code_fingerprint = (
        str(raw["code_fingerprint"]) if raw.get("code_fingerprint") is not None else None
    )
    if isinstance(raw.get("population"), dict):
        bundle.population = dict(raw["population"])
    # Shape FIRST, then preconditions (GAP-082 review round 2, SF3): the
    # `fields` object is required of the two shapes that are READ -- this
    # one, the canonical, and the schema-less normalized one the Console
    # reads rows from -- and of nothing else. An UNRECOGNIZED schema is
    # present and NOT consumed, so it is held to no precondition: a future
    # schema that renames the key must not make its bundle vanish.
    if schema is not None and schema != CAST_PROVENANCE_SCHEMA:
        bundle.warnings.append(_not_consumed_warning(path, schema))
        return
    fields = sidecar_fields_or_raise(raw, path)

    if schema != CAST_PROVENANCE_SCHEMA:  # the normalized shape: never consumed here
        bundle.warnings.append(_not_consumed_warning(path, schema))
        return

    classes: dict[str, str] = {}
    overridden: list[str] = []
    for fpath, record in fields.items():
        if not isinstance(record, dict):
            continue
        cls = record.get("evidence_class") or record.get("class")
        if cls:
            classes[str(fpath)] = str(cls).strip().lower()
        if record.get("overrides"):
            overridden.append(str(fpath))
    if not classes:
        bundle.warnings.append(
            f"{path.name}: provenance sidecar declares {len(fields)} field(s) "
            "but none carries an evidence class -- nothing to consume; "
            + REDERIVED_WARNING
        )
        return

    bundle.field_classes = classes
    bundle.overridden_fields = sorted(overridden)
    bundle.evidence_source = SOURCE_SIDECAR
    if not bundle.population:
        bundle.warnings.append(
            f"{path.name}: provenance sidecar carries no `population` block -- "
            "row 1's dropped-host accounting is RE-DERIVED from the topology "
            "export rather than read from the producer (§8.1)"
        )


def resolve_bundle_dir(
    *, reference_dir: Optional[str | Path] = None,
    reference_root: Optional[str | Path] = None,
    bundle_id: Optional[str] = None,
) -> Optional[Path]:
    """Resolve WHICH bundle directory to read -- pinned by the caller, never
    a first-hit chain across catalogs (LES-198).

    `reference_dir` wins outright (the CLI's `--reference`). Otherwise the
    bundle is `<reference_root>/<bundle_id>`, with `reference_root`
    defaulting to the ONE named root `twins/`. Returns `None` when nothing
    resolves, so the caller can report `reference: absent` with a reason
    instead of raising -- a twin deployed from a hand-authored config
    legitimately has no cast bundle. Since casts and hand-authored twins
    share `twins/`, a TWIN FOLDER resolves only when it carries a cast's
    topology export or provenance sidecar (`twinconfig.bundle.
    has_cast_artifacts`); a legacy bundle directory resolves as it always did.
    """
    from axrange.twinconfig.bundle import has_cast_artifacts, is_twin_folder

    if reference_dir:
        return Path(reference_dir)
    if not bundle_id:
        return None
    root = Path(reference_root) if reference_root else default_reference_root()
    candidate = root / bundle_id
    if not candidate.is_dir():
        return None
    if is_twin_folder(candidate) and not has_cast_artifacts(candidate, bundle_id):
        return None
    return candidate
