"""IR YAML loading + include splicing per docs/IR_SCHEMA.md §1.2.

Includes are list-concatenated under each section's natural list field
(networks, nodes, apps, activity, bindings, overlays.identity, overlays.data,
overlays.defense, fabric.regions); fabric's scalar sub-keys (fabric_asn,
tenant) splice the same way a fragment may only *set*, not override, them.
Strict-no-override is enforced at the parser level by INC-010; a duplicate ID
in any of those known ID-bearing sections is an error (IR_SCHEMA §1.4) —
free-form value-maps elsewhere in the document (e.g. `apps[].env`,
`inline.steps[].id`) are not scanned.

The parser surfaces a few coarse failure modes as exceptions (file missing,
YAML unparseable, schema-version mismatch in a fragment). All other validation
— including the "is this YAML actually shaped like an IR" question — is
deferred to the validator's Pass 1.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from axrange.ir.schema import IR
from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw


class ParseError(Exception):
    """Base for all parser errors. Wrapped into Pass 1 findings by the engine."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class StructuralError(ParseError):
    """YAML loaded but isn't a mapping at the root, or includes is malformed."""


class IncludeError(ParseError):
    """An include path doesn't resolve, doesn't parse, circles, or collides."""


@dataclass
class ParseResult:
    """What the parser returns. The engine consumes both the IR and the raw
    merged dict (the latter so rules can inspect unknown keys without losing
    them through Pydantic normalization).
    """

    ir: IR
    raw: dict[str, Any]
    source_files: list[str] = field(default_factory=list)
    parse_findings: list["RawParseFinding"] = field(default_factory=list)


@dataclass
class RawParseFinding:
    """A finding produced during parsing — only the rule-IDs Pass 1 needs to
    surface before Pydantic gets the IR. Rendered by the engine.
    """

    rule_id: str
    message: str
    file: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _yaml_loader() -> YAML:
    # safe mode: no code here reads ruamel's round-trip line/comment metadata
    # (`_dict_from_loaded` converts to plain dict/list immediately after
    # load), and safe mode is faster — matches axrange.manifest.loader's
    # loader mode.
    return YAML(typ="safe")


def load_yaml(path: str | Path) -> Any:
    """Load a single YAML file. Raises IncludeError/StructuralError on failure."""
    p = Path(path)
    try:
        return load_yaml_raw(p, loader=_yaml_loader())
    except YamlLoadError as e:
        if e.not_found:
            raise IncludeError(str(e), path=str(p)) from e
        raise StructuralError(str(e), path=str(p)) from e


def parse_string(source: str, *, origin: str = "<string>") -> Any:
    try:
        return _yaml_loader().load(io.StringIO(source))
    except YAMLError as e:
        raise StructuralError(f"YAML parse error in {origin}: {e}", path=origin) from e


# Fields that are list-merged across includes. Anything not in this list,
# present in a fragment, that's already set in the main file, is an
# include-override error (INC-010 generalization).
_MERGEABLE_LIST_PATHS: tuple[tuple[str, ...], ...] = (
    ("layers", "infra", "networks"),
    ("layers", "infra", "nodes"),
    ("layers", "apps"),
    ("layers", "activity"),
    ("bindings",),
    ("sensors",),
    ("overlays", "identity"),
    ("overlays", "data"),
    ("overlays", "defense"),
    ("fabric", "regions"),
)


def _get_nested(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _set_nested(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur = d
    for k in path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def _collect_ids(doc: dict[str, Any], into: dict[str, str], file: str, into_findings: list[RawParseFinding]) -> None:
    """Collect entity IDs from `doc`'s known ID-bearing sections (IR_SCHEMA
    §1.4: the scenario header plus every section in `_MERGEABLE_LIST_PATHS` —
    networks, nodes, apps, activity, bindings, sensors, overlays.*,
    fabric.regions) and record duplicates against IDs already seen (INC-010,
    strict-no-override).

    Deliberately does NOT recurse into free-form value-maps nested inside
    those entities (e.g. `activity[].inline.steps[].id`, `apps[].env`,
    `apps[].provides[]`, `scenario.labels`) — a dict key literally named `id`
    inside one of those isn't an IR entity identifier and must not trip
    strict-no-override.
    """

    def _note(ident: Any) -> None:
        if not isinstance(ident, str):
            return
        if ident in into:
            into_findings.append(
                RawParseFinding(
                    rule_id="INC-010",
                    message=(
                        f"ID '{ident}' defined in both '{into[ident]}' and '{file}' "
                        f"(strict-no-override violated)"
                    ),
                    file=file,
                    extra={"id": ident, "other_file": into[ident]},
                )
            )
        else:
            into[ident] = file

    scenario_block = doc.get("scenario")
    if isinstance(scenario_block, dict):
        _note(scenario_block.get("id"))

    for path in _MERGEABLE_LIST_PATHS:
        items = _get_nested(doc, path)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                _note(item.get("id"))


def _merge_fragment_into_main(
    main: dict[str, Any], fragment: dict[str, Any], fragment_file: str
) -> list[RawParseFinding]:
    """List-concat known list paths from fragment into main; reject overrides
    on non-list paths.
    """
    findings: list[RawParseFinding] = []

    # Mergeable list paths: concat fragment list onto main list.
    for path in _MERGEABLE_LIST_PATHS:
        frag_val = _get_nested(fragment, path)
        if frag_val is None:
            continue
        if not isinstance(frag_val, list):
            findings.append(
                RawParseFinding(
                    rule_id="INC-010",
                    message=(
                        f"Fragment '{fragment_file}' sets '{'.'.join(path)}' to "
                        f"non-list value; only list-typed sections may be merged"
                    ),
                    file=fragment_file,
                )
            )
            continue
        main_val = _get_nested(main, path)
        if main_val is None:
            _set_nested(main, path, list(frag_val))
        elif isinstance(main_val, list):
            main_val.extend(frag_val)
        else:
            findings.append(
                RawParseFinding(
                    rule_id="INC-010",
                    message=(
                        f"Cannot merge list section '{'.'.join(path)}' from "
                        f"'{fragment_file}' into main: main value is not a list"
                    ),
                    file=fragment_file,
                )
            )

    # fabric: scalar sub-keys splice across includes. `fabric.regions` is
    # already merged above via _MERGEABLE_LIST_PATHS; `fabric_asn`/`tenant`
    # follow the same strict-no-override semantics as other scalar sections —
    # a fragment may set them only if the main file hasn't already.
    frag_fabric = fragment.get("fabric")
    if isinstance(frag_fabric, dict):
        for key in ("fabric_asn", "tenant"):
            if key not in frag_fabric:
                continue
            frag_value = frag_fabric[key]
            main_fabric = main.get("fabric")
            if not isinstance(main_fabric, dict):
                main_fabric = {}
                main["fabric"] = main_fabric
            if key in main_fabric and main_fabric[key] != frag_value:
                findings.append(
                    RawParseFinding(
                        rule_id="INC-010",
                        message=(
                            f"Fragment '{fragment_file}' overrides top-level "
                            f"key 'fabric.{key}'"
                        ),
                        file=fragment_file,
                    )
                )
            else:
                main_fabric[key] = frag_value

    # Top-level keys we DON'T merge as lists (scenario:, scenario_schema_version):
    # a fragment may not redefine them at all (silent override is the failure
    # mode this protects against).
    for k, v in fragment.items():
        if k in ("scenario", "scenario_schema_version"):
            if k in main:
                findings.append(
                    RawParseFinding(
                        rule_id="INC-010",
                        message=(
                            f"Fragment '{fragment_file}' redefines top-level "
                            f"'{k}'; only the main file may declare it"
                        ),
                        file=fragment_file,
                    )
                )
            else:
                main[k] = v
        elif k in ("layers", "bindings", "sensors", "overlays", "fabric"):
            # Already handled above per mergeable list paths / fabric scalars.
            continue
        else:
            # Unknown top-level key — preserve into main but flag if collision.
            if k in main and main[k] != v:
                findings.append(
                    RawParseFinding(
                        rule_id="INC-010",
                        message=(
                            f"Fragment '{fragment_file}' overrides top-level "
                            f"key '{k}'"
                        ),
                        file=fragment_file,
                    )
                )
            else:
                main[k] = v

    return findings


def parse_ir(
    path: str | Path | None = None,
    *,
    text: str | None = None,
    base_dir: str | Path | None = None,
) -> ParseResult:
    """Parse a scenario IR from a file or a string.

    When `text` is provided, `base_dir` controls where relative includes
    resolve from. When `path` is provided, base_dir defaults to the file's
    parent.

    Raises ParseError on coarse failures (file not found, YAML unparseable).
    Returns a ParseResult containing the typed IR, the merged raw dict, and a
    list of parse-time findings (mostly INC-* duplicate-ID detections) that
    the engine emits during Pass 1.
    """
    findings: list[RawParseFinding] = []
    source_files: list[str] = []

    if text is not None:
        origin = "<inline>"
        main_raw = parse_string(text, origin=origin)
        base = Path(base_dir) if base_dir is not None else Path(".")
        source_files.append(origin)
    elif path is not None:
        main_path = Path(path).resolve()
        main_raw = load_yaml(main_path)
        base = main_path.parent
        source_files.append(str(main_path))
    else:
        raise ValueError("parse_ir requires either path= or text=")

    if main_raw is None:
        main_raw = {}
    if not isinstance(main_raw, dict):
        raise StructuralError(
            f"IR root must be a mapping, got {type(main_raw).__name__}",
            path=source_files[0],
        )

    main_dict: dict[str, Any] = _dict_from_loaded(main_raw)

    # Collect this file's own entity IDs first (attributed to the main file),
    # before any fragment merges. Fragments are then scanned against this
    # running set as they're processed so a duplicate is attributed to the
    # actual fragment file that introduced it, not always to the main file.
    id_origins: dict[str, str] = {}
    _collect_ids(main_dict, id_origins, str(source_files[0]), findings)

    # Process includes. Strict-no-override semantics.
    scenario_block = main_dict.get("scenario") or {}
    include_paths_raw = (scenario_block.get("includes") if isinstance(scenario_block, dict) else None) or []

    seen_includes: set[str] = set()
    include_queue: list[tuple[Path, str]] = []
    for inc in include_paths_raw:
        if not isinstance(inc, str):
            findings.append(
                RawParseFinding(
                    rule_id="STR-040",
                    message=f"scenario.includes entry must be a string, got {type(inc).__name__}",
                )
            )
            continue
        include_queue.append(((base / inc).resolve(), inc))

    main_schema_version = main_dict.get("scenario_schema_version")
    while include_queue:
        resolved, declared = include_queue.pop(0)
        key = str(resolved)
        if key in seen_includes:
            findings.append(
                RawParseFinding(
                    rule_id="INC-030",
                    message=f"Circular include of '{declared}'",
                    file=key,
                )
            )
            continue
        seen_includes.add(key)
        try:
            frag_raw = load_yaml(resolved)
        except IncludeError as e:
            findings.append(
                RawParseFinding(
                    rule_id="INC-020",
                    message=str(e),
                    file=str(resolved),
                )
            )
            continue
        except StructuralError as e:
            findings.append(
                RawParseFinding(
                    rule_id="INC-020",
                    message=str(e),
                    file=str(resolved),
                )
            )
            continue
        if not isinstance(frag_raw, dict):
            findings.append(
                RawParseFinding(
                    rule_id="INC-020",
                    message=(
                        f"Included fragment '{declared}' root must be a "
                        f"mapping, got {type(frag_raw).__name__ if frag_raw is not None else 'null'}"
                    ),
                    file=str(resolved),
                )
            )
            continue
        frag_dict = _dict_from_loaded(frag_raw)
        source_files.append(str(resolved))

        # Collect this fragment's own IDs against the running set BEFORE it's
        # merged into main, so a collision is attributed to the fragment that
        # actually introduced the duplicate (INC-010).
        _collect_ids(frag_dict, id_origins, str(resolved), findings)

        # INC-040: fragment schema-version (if set) must match main's.
        frag_ver = frag_dict.get("scenario_schema_version")
        if frag_ver is not None and main_schema_version is not None and frag_ver != main_schema_version:
            findings.append(
                RawParseFinding(
                    rule_id="INC-040",
                    message=(
                        f"Fragment '{declared}' scenario_schema_version="
                        f"{frag_ver} does not match main file's "
                        f"{main_schema_version}"
                    ),
                    file=str(resolved),
                )
            )
        # Strip the fragment's schema-version from merge to avoid spurious
        # override findings — INC-040 already covered any mismatch.
        frag_dict.pop("scenario_schema_version", None)

        # Queue this fragment's nested includes.
        nested_scn = frag_dict.get("scenario") or {}
        nested_includes = (
            nested_scn.get("includes") if isinstance(nested_scn, dict) else None
        ) or []
        for inc in nested_includes:
            if isinstance(inc, str):
                include_queue.append(((resolved.parent / inc).resolve(), inc))

        findings.extend(_merge_fragment_into_main(main_dict, frag_dict, str(resolved)))

    # Build the typed IR via Pydantic. We tolerate failures by wrapping into a
    # very loose IR object: rules will report the actual problems.
    try:
        ir = IR.model_validate(main_dict)
    except Exception as e:  # noqa: BLE001
        # Reduce a Pydantic error to a single Pass-1 finding tagged STR-040.
        findings.append(
            RawParseFinding(
                rule_id="STR-040",
                message=f"IR shape does not match schema: {e}",
            )
        )
        ir = IR()  # empty; rules will then catch missing required fields
    ir.source_files = source_files
    ir.raw = main_dict

    return ParseResult(
        ir=ir, raw=main_dict, source_files=source_files, parse_findings=findings
    )


def _dict_from_loaded(loaded: Any) -> dict[str, Any]:
    """Convert ruamel.yaml's commented containers into plain dict/list trees."""
    if isinstance(loaded, dict):
        return {k: _dict_from_loaded(v) for k, v in loaded.items()}
    if isinstance(loaded, list):
        return [_dict_from_loaded(v) for v in loaded]  # type: ignore[return-value]
    return loaded
