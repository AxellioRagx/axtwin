"""Target preflight for validate / plan (`docs/design/targets-and-lab.md` §2.2).

For a chosen target, before any state write:

- every sensor a tap binding names must be in that target's lab --
  `sensor <id> not in lab of target <name>`, located at the binding
  (`tap/default.yaml: tap-plan.bindings[0].sensors[0]`). "In the lab" is the
  merged external-sensor list the tap tier resolves against (the lab block
  plus adapter.conf's `EXTERNAL_SENSORS`, read for one release) and the
  target's standing A1 sensors, so the preflight never refuses what the
  planner would accept;
- every node image must have a binding for the target's platform --
  `image <x> has no binding for platform <p>`, located at the host
  (`te.yaml: twin_config.nodes[2].image`).

Findings are `FindingDTO`s with `severity: error` (rule ids `TARGET-SENSOR`,
`TARGET-IMAGE`), so a validate answer folds them into its own findings and the
deploy dialog lists them as its preflight rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from axrange.api import dtos
from axrange.api.settings import ApiSettings

RULE_SENSOR = "TARGET-SENSOR"
RULE_IMAGE = "TARGET-IMAGE"


@dataclass
class Preflight:
    target: str
    platform: str
    findings: list[dtos.FindingDTO] = field(default_factory=list)
    images_total: int = 0
    images_bound: int = 0
    #: Every sensor id the target offers (lab + fallback + standing), sorted.
    sensors: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[dtos.FindingDTO]:
        return [f for f in self.findings if f.severity == "error"]


@dataclass(frozen=True)
class Sources:
    """Where the preflight reads the twin from. `te_path` is the file the
    image paths are reported against (a twin's te file, or a scenario IR);
    `te_label` is how that file is named in a location (`te.yaml`). `tap_path`
    is the tap plan, None when the twin declares none."""

    te_path: Optional[Path]
    te_label: str = "te.yaml"
    tap_path: Optional[Path] = None
    tap_label: str = "tap/default.yaml"


def _load_yaml(path: Path) -> Any:
    from ruamel.yaml import YAML

    from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw

    try:
        return load_yaml_raw(path, loader=YAML(typ="safe"))
    except YamlLoadError:
        return None


def _node_lists(raw: Any) -> Iterator[tuple[str, list]]:
    """`(path, nodes)` for the node list(s) of a twin config
    (`twin_config.nodes`) or a scenario IR (`scenario.<...>.nodes`)."""
    if not isinstance(raw, dict):
        return
    tc = raw.get("twin_config")
    if isinstance(tc, dict) and isinstance(tc.get("nodes"), list):
        yield "twin_config.nodes", tc["nodes"]
        return
    sc = raw.get("scenario")
    if isinstance(sc, dict):
        if isinstance(sc.get("nodes"), list):
            yield "scenario.nodes", sc["nodes"]
        for k, v in sc.items():
            if isinstance(v, dict) and isinstance(v.get("nodes"), list):
                yield f"scenario.{k}.nodes", v["nodes"]


def _image_refs(raw: Any) -> Iterator[tuple[str, str]]:
    for base, nodes in _node_lists(raw):
        for i, n in enumerate(nodes):
            if isinstance(n, dict) and isinstance(n.get("image"), str) and n["image"].strip():
                yield f"{base}[{i}].image", n["image"].strip()


def _tap_sensor_refs(raw: Any) -> Iterator[tuple[str, str]]:
    tp = raw.get("tap-plan") if isinstance(raw, dict) else None
    bindings = tp.get("bindings") if isinstance(tp, dict) else None
    for i, b in enumerate(bindings or []):
        if not isinstance(b, dict):
            continue
        sensors = b.get("sensors")
        if isinstance(sensors, str):
            sensors = [sensors]
        for j, s in enumerate(sensors or []):
            if isinstance(s, str) and s.strip():
                yield f"tap-plan.bindings[{i}].sensors[{j}]", s.strip()


def target_sensor_ids(facts: Any) -> list[str]:
    """Every sensor id a tap binding may name on this target: the merged
    external sensors (lab + adapter.conf fallback) and the standing A1
    sensors in its primary store."""
    ids = {str(x.id) for x in (getattr(facts.cfg, "external_sensors", None) or [])
           if getattr(x, "id", None)}
    db = facts.target.db_path
    if db and Path(db).is_file():
        try:
            from axrange.o2control.control import surface_status

            ids |= {s.sensor_id for s in surface_status(db_path=db).sensors}
        except Exception:  # noqa: BLE001 -- a store that cannot be read offers no standing sensors
            pass
    return sorted(ids)


def run_preflight(
    settings: ApiSettings, target: Optional[str], sources: Sources, manifests: Any,
) -> Preflight:
    """The preflight of `sources` against target `target` (the default one
    for None). Raises `UnknownTargetError` for a name the process does not
    serve. Never writes."""
    from axrange.api.targets import get_target, target_facts
    from axrange.platform import check_platform

    t = get_target(settings, target)
    facts = target_facts(t)
    platform = facts.platform or check_platform(t.platform)
    pf = Preflight(target=t.name, platform=platform)

    if sources.te_path is not None:
        seen: set[str] = set()
        for path, ref in _image_refs(_load_yaml(sources.te_path)):
            image_id = ref.partition("@")[0]
            if "${" in image_id:
                continue  # a parameter -- the expanded IR's validator covers it
            pf.images_total += 1
            if manifests is not None and manifests.get_binding(image_id, platform) is not None:
                pf.images_bound += 1
                continue
            if ref in seen:
                # One row per image is enough to fix it; the count still says how many hosts.
                continue
            seen.add(ref)
            pf.findings.append(dtos.FindingDTO(
                rule_id=RULE_IMAGE, severity="error",
                message=f"image {ref} has no binding for platform {platform}",
                location=f"{sources.te_label}: {path}",
            ))

    if sources.tap_path is not None and sources.tap_path.is_file():
        pf.sensors = target_sensor_ids(facts)
        known = set(pf.sensors)
        for path, sid in _tap_sensor_refs(_load_yaml(sources.tap_path)):
            if sid not in known:
                pf.findings.append(dtos.FindingDTO(
                    rule_id=RULE_SENSOR, severity="error",
                    message=f"sensor {sid} not in lab of target {t.name}",
                    location=f"{sources.tap_label}: {path}",
                ))
    else:
        pf.sensors = target_sensor_ids(facts)
    return pf


def config_sources(settings: ApiSettings, config_id: str) -> Sources:
    """The twin config's te file and its default tap plan (when the twin
    declares a tap tier), labelled relative to the twin folder."""
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file
    from axrange.twinconfig.registry import resolve_twin_config_path

    try:
        te = resolve_twin_config_path(config_id, catalog_dir=settings.twin_configs_dir or None,
                                      warnings=[])
    except TwinConfigError:
        return Sources(te_path=None)
    twin_dir = twin_dir_for_file(te)
    te_label = te.name
    tap: Optional[Path] = None
    tap_label = "tap/default.yaml"
    if twin_dir is not None:
        te_label = str(te.relative_to(twin_dir)) if te.is_relative_to(twin_dir) else te.name
        try:
            tap = load_twin_manifest(twin_dir).tier_path("tap")
            tap_label = str(tap.relative_to(twin_dir))
        except TwinConfigError:
            tap = None
    return Sources(te_path=te, te_label=te_label, tap_path=tap, tap_label=tap_label)


def fold_into(result: dtos.ValidateResultDTO, pf: Preflight) -> dtos.ValidateResultDTO:
    """A validate answer with the preflight's findings added (a FAIL when
    any is an error)."""
    if not pf.findings:
        return result
    errs = len(pf.errors)
    return dtos.ValidateResultDTO(
        disposition="FAIL" if errs else result.disposition,
        exit_code=1 if errs else result.exit_code,
        passes_run=list(result.passes_run),
        errors=result.errors + errs,
        warnings=result.warnings + (len(pf.findings) - errs),
        findings=list(result.findings) + list(pf.findings),
    )


def to_dto(pf: Preflight) -> dtos.PreflightDTO:
    return dtos.PreflightDTO(
        target=pf.target, platform=pf.platform, clean=not pf.errors,
        images_total=pf.images_total, images_bound=pf.images_bound,
        sensors=list(pf.sensors), findings=list(pf.findings),
    )
