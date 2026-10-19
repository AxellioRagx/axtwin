"""Reader for the aximprint cast-topology-export artifact (`docs/CHANGELOG.md`
2026-08-27) — `<id>.topology.json`, shipped BESIDE the twin-config/imprint
`<id>.yaml` it casts from a capture
(`aximprint-axrange/scripts/install_cast_as_twin.py`; in a `twins/<id>/` folder
it sits under `generated/`, e.g. `twins/corpapcn-twin/generated/
corpapcn-twin.topology.json` -- `axrange.twinconfig.bundle`).

Mirrors `axrange.api.imprints`'s posture exactly (LES-025 -- same shape, new
catalog): reads a checked-in/located FILE only, never imports `aximprint` or
touches its duckdb store. Optional enrichment, not a required artifact —
`load_topology_export` returns `None` (not an error) when the sibling file is
simply absent, which is the ordinary case for a twin-config/imprint that was
never cast from a capture.

Every row keeps its `evidence_class` (constraint 2, `docs/WORK_ORDER_
console_selector_and_stale_deploys.md`: render observed vs. imputed
differently, never flatten). `role: None` on a node is not "missing data" —
it is exactly the population `census.with_role`/`census.entities_total`
describes: an entity the tap saw but couldn't role-fingerprint. The shipped
`twin_config` a deployed twin compiles down to is a scaled-down SUBSET of
this census (it drops role-less/partially-discovered hosts and carries no
edges at all) — this module is the only source for the FULL picture.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from axrange.jsonnum import optional_finite_number, optional_whole_number


class TopologyExportError(Exception):
    """The sibling file exists but failed to read/parse/shape-check."""


class TopologyExportNotFoundError(TopologyExportError):
    """No `<id>.topology.json` beside this config. `load_topology_export`
    returns `None` for this case rather than raising (an absent export is
    the ordinary case, not a data problem) — kept as a typed miss for a
    caller that wants to build its own message around a concrete
    exception rather than branching on `None`."""

    def __init__(self, config_id: str) -> None:
        super().__init__(f"no topology-export for {config_id!r}")
        self.config_id = config_id


@dataclass
class ExportSegment:
    cidr: str
    name: str
    entities: int = 0
    coverage_source: Optional[str] = None


@dataclass
class ExportNode:
    id: str
    address: Optional[str] = None
    locality: Optional[str] = None
    segment: Optional[str] = None
    segment_evidence_class: Optional[str] = None
    role: Optional[str] = None
    role_confidence: Optional[float] = None
    role_evidence_class: Optional[str] = None
    windows_seen: Optional[int] = None
    listen_ports: list[str] = field(default_factory=list)
    dial_ports: list[str] = field(default_factory=list)


@dataclass
class ExportEdge:
    src: str
    dst: str
    proto: Optional[str] = None
    bytes: int = 0
    flows: int = 0
    evidence_class: Optional[str] = None


@dataclass
class ExportCensus:
    entities_total: int = 0
    with_role: int = 0
    placed: int = 0
    local: int = 0


@dataclass
class TopologyExport:
    imprint_id: str
    pit_id: Optional[str]
    as_of: Optional[float]
    network: Optional[str]
    vantage: Optional[str]
    windows: Optional[int]
    schema_version: Optional[str]
    segments: list[ExportSegment]
    nodes: list[ExportNode]
    edges: list[ExportEdge]
    census: ExportCensus
    source_path: Path


def sibling_path(config_id: str, source_yaml_dir: Path) -> Path:
    """Where the export WOULD live for `config_id`, whether or not it does
    -- callers that only need to check existence (e.g. `TwinConfigDetailDTO`
    enrichment) can stat this without paying the full parse. Under
    `generated/` in a `twins/<id>/` folder (`axrange.twinconfig.bundle`)."""
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(source_yaml_dir, f"{config_id}.topology.json")


def load_topology_export(config_id: str, source_yaml_dir: Path) -> Optional[TopologyExport]:
    """Look for `<source_yaml_dir>/<config_id>.topology.json` (the REAL,
    non-materialized directory a resolved twin-config/imprint's `<id>.yaml`
    lives in — e.g. `axrange.twinconfig.registry.load_twin_config_raw`'s
    second return value's `.parent`, NOT `materialize_twin_config`'s
    throwaway tempfile path, which has no sibling anything; in a `twins/<id>/`
    folder the export sits under `generated/`, `axrange.twinconfig.bundle`).
    Returns `None`
    if the file doesn't exist — raises `TopologyExportError` only for a
    file that EXISTS but fails to read/parse/shape-check (a real data
    problem, not an absence)."""
    path = sibling_path(config_id, source_yaml_dir)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        # ValueError: a UnicodeDecodeError (invalid UTF-8, or a UTF-16 BOM)
        # is a ValueError, not an OSError -- it used to escape as a 500.
        raise TopologyExportError(f"{path}: unreadable: {e}") from e
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise TopologyExportError(f"{path}: {e}") from e
    if not isinstance(raw, dict):
        raise TopologyExportError(f"{path}: top-level JSON must be an object")
    try:
        return _parse(raw, path)
    except (KeyError, TypeError, ValueError, OverflowError) as e:
        raise TopologyExportError(f"{path}: {e}") from e


def _opt_str(obj: dict[str, Any], key: str, at: str = "") -> Optional[str]:
    """A value the DTO types `Optional[str]`, checked HERE (M1) and NAMED when
    wrong (`nodes[3].address`): a wrong type used to pass straight into the
    typed DTO and 500 there -- or be `str()`-ed into a value the producer
    never wrote (GAP-082 review round 2)."""
    value = obj.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"`{at}{key}` must be a string, got {type(value).__name__} {value!r}")


def _req_str(obj: dict[str, Any], key: str, at: str) -> str:
    value = obj.get(key)
    if isinstance(value, str):
        return value
    if value is None:
        raise ValueError(f"`{at}{key}` is required (a string)")
    raise ValueError(f"`{at}{key}` must be a string, got {type(value).__name__} {value!r}")


def _str_list(obj: dict[str, Any], key: str, at: str) -> list[str]:
    value = obj.get(key)
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"`{at}{key}` must be a list of strings, got {value!r}")


def _objects(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """`raw[key]` as a list of objects (absent or null: empty), refused
    naming the field otherwise -- iterating an object walked its KEYS."""
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"`{key}` must be a list, got {type(value).__name__} {value!r}")
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"`{key}[{i}]` must be an object, got {type(item).__name__} {item!r}")
    return value


def _parse(raw: dict[str, Any], path: Path) -> TopologyExport:
    """Every value typed HERE and NAMED when wrong -- a string is a string, a
    whole number is the ONE bounded rule (`axrange.jsonnum`: a JSON number in
    0 .. 2**53 - 1), a float is finite -- so nothing reaches
    `TopologyExportDTO` with a type it does not publish, and nothing is
    coerced into a value the producer never wrote (GAP-082 review, round 2:
    a non-object `census` 500'd the route; a list `cidr` or a numeric node
    `id` came back as a string)."""
    segments: list[ExportSegment] = []
    for i, s in enumerate(_objects(raw, "segments")):
        at = f"segments[{i}]."
        segments.append(ExportSegment(
            cidr=_req_str(s, "cidr", at),
            name=_req_str(s, "name", at),
            entities=optional_whole_number(s.get("entities"), f"{at}entities", default=0),
            coverage_source=_opt_str(s, "coverage_source", at),
        ))
    nodes: list[ExportNode] = []
    for i, n in enumerate(_objects(raw, "nodes")):
        at = f"nodes[{i}]."
        nodes.append(ExportNode(
            id=_req_str(n, "id", at),
            address=_opt_str(n, "address", at),
            locality=_opt_str(n, "locality", at),
            segment=_opt_str(n, "segment", at),
            segment_evidence_class=_opt_str(n, "segment_evidence_class", at),
            role=_opt_str(n, "role", at),
            role_confidence=optional_finite_number(
                n.get("role_confidence"), f"{at}role_confidence",
            ),
            role_evidence_class=_opt_str(n, "role_evidence_class", at),
            windows_seen=optional_whole_number(n.get("windows_seen"), f"{at}windows_seen"),
            listen_ports=_str_list(n, "listen_ports", at),
            dial_ports=_str_list(n, "dial_ports", at),
        ))
    edges: list[ExportEdge] = []
    for i, e in enumerate(_objects(raw, "edges")):
        at = f"edges[{i}]."
        edges.append(ExportEdge(
            src=_req_str(e, "src", at),
            dst=_req_str(e, "dst", at),
            proto=_opt_str(e, "proto", at),
            bytes=optional_whole_number(e.get("bytes"), f"{at}bytes", default=0),
            flows=optional_whole_number(e.get("flows"), f"{at}flows", default=0),
            evidence_class=_opt_str(e, "evidence_class", at),
        ))
    census_raw = raw.get("census")
    if census_raw is None:
        census_raw = {}
    if not isinstance(census_raw, dict):
        raise ValueError(
            f"`census` must be an object, got {type(census_raw).__name__} {census_raw!r}"
        )
    census = ExportCensus(**{
        key: optional_whole_number(census_raw.get(key), f"census.{key}", default=0)
        for key in ("entities_total", "with_role", "placed", "local")
    })
    return TopologyExport(
        imprint_id=_opt_str(raw, "imprint_id") or "",
        pit_id=_opt_str(raw, "pit_id"),
        as_of=optional_finite_number(raw.get("as_of"), "as_of"),
        network=_opt_str(raw, "network"),
        vantage=_opt_str(raw, "vantage"),
        windows=optional_whole_number(raw.get("windows"), "windows"),
        schema_version=(
            str(raw["schema_version"]) if raw.get("schema_version") is not None else None
        ),
        segments=segments, nodes=nodes, edges=edges, census=census,
        source_path=path,
    )
