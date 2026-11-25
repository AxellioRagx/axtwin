"""The tfc playout's trafficgen half (`SPEC.md`; T3): everything
`o2control.traffic.apply_traffic_profile` needs to add generators to a tfc
plan, and the live rate change.

* `prepare_lines` -- the twin's te file (read-only), the TGN checks (an error
  refuses the apply), the generator plan, the te deploy's recorded networks,
  the generator image's binding, and each line's recorded multiplier.
* `update_line_rate` -- `update-traffic` for one line of a standing tfc
  deploy: budget check, the op over the guest agent, the new multiplier
  recorded on the generator's `start-traffic` row so status and a later
  re-apply agree.
* `line_status` -- per-line counters for the tfc status.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from axrange.trafficgen.compose import te_segment_attach
from axrange.trafficgen.plan import TrafficGenPlan, TrafficGenPlanError, plan_lines
from axrange.trafficgen.twin import TwinView


class LinesError(ValueError):
    def __init__(self, message: str, *, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.message, self.status, self.detail = message, status, detail


@dataclass
class PreparedLines:
    tg_plan: TrafficGenPlan
    attach: dict[str, dict[str, Any]]
    binding: dict[str, Any]
    mults: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _row(r: Any) -> dict[str, Any]:
    return {"op_id": r.op_id, "op_name": r.op_name, "scope": json.loads(r.scope_json or "{}"),
            "inputs": json.loads(r.inputs_resolved_json or "{}"),
            "outputs": json.loads(r.outputs_json or "{}") if r.outputs_json else {},
            "status": getattr(r.status, "value", r.status)}


def te_twin_path(store: Any, te_deploy_id: str, spec_path: Optional[Any],
                 twin_dir: Optional[Any] = None) -> Path:
    """The twin's te file: `twin_dir` when the caller knows it (the API: the
    twin's folder in the serve's catalog), else the profile's own twin folder
    when it lives in one, else `catalog/twins/<te deploy's scenario id>/`."""
    from axrange.paths import TWINS_DIR
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file

    twin_dir = Path(twin_dir) if twin_dir else (twin_dir_for_file(spec_path) if spec_path else None)
    if twin_dir is None:
        d = store.get_deploy(te_deploy_id)
        if d is None:
            raise LinesError(f"te deploy {te_deploy_id!r} not found", status=404)
        twin_dir = TWINS_DIR / d.scenario_id
    try:
        return Path(load_twin_manifest(twin_dir).te_path)
    except TwinConfigError as e:
        raise LinesError(f"traffic lines need the twin's te file: {e}") from e


def recorded_mults(store: Any, tfc_deploy_id: Optional[str]) -> dict[str, float]:
    """Each line's live multiplier, as `update_line_rate` recorded it."""
    if not tfc_deploy_id:
        return {}
    out: dict[str, float] = {}
    for r in store.ops_for_deploy(tfc_deploy_id):
        if r.op_name == "start-traffic" and r.outputs_json:
            out.update({k: float(v) for k, v in (json.loads(r.outputs_json).get("mults") or {}).items()})
    return out


def generator_binding(manifests: Any, image_ref: str, platform: str) -> dict[str, Any]:
    image_id, _, version = image_ref.partition("@")
    if manifests is None:
        raise LinesError("traffic lines need the images root (--images / the API's images setting) "
                         f"to find the {image_ref} template")
    b = manifests.get_binding(image_id, platform)
    v = (b.get_version(version) if version else (b.versions[0] if b and b.versions else None)) if b else None
    if v is None or v.storage is None or not v.storage.template_vmid:
        raise LinesError(f"no {platform} binding with a built template for the generator image {image_ref}")
    ci = getattr(v, "cloud_init", None)
    hw = getattr(v, "hardware", None)
    return {"image_id": image_id, "template_vmid": int(v.storage.template_vmid),
            "clone_mode": v.clone_mode or "linked",
            "ci_user": getattr(ci, "user", None) or "debian",
            "machine": getattr(hw, "machine", None) or "pc", "bios": getattr(hw, "bios", None) or "seabios"}


def prepare_lines(spec: Any, *, spec_path: Optional[Any], store: Any, te_deploy_id: str, platform: str,
                  manifests: Any, tfc_deploy_id: Optional[str],
                  twin_dir: Optional[Any] = None) -> PreparedLines:
    from axrange.personas.registry import PERSONAS, twin_overlay
    from axrange.trafficgen.apps import list_apps
    from axrange.trafficgen.image import generator_image
    from axrange.trafficgen.validate import check_lines
    from axrange.validator.finding import Severity

    te_path = te_twin_path(store, te_deploy_id, spec_path, twin_dir)
    twin = TwinView.from_te_path(te_path)
    personas = {**PERSONAS, **twin_overlay(te_path.parent / "personas")}
    apps = list_apps()[0]
    image = generator_image(platform)
    findings = check_lines(spec, twin, personas, apps, generator_image=image)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    if errors:
        raise LinesError("traffic lines refused: " + "; ".join(f"{f.rule_id}: {f.message}" for f in errors),
                         detail={"findings": [{"rule_id": f.rule_id, "message": f.message} for f in errors]})
    try:
        tg = plan_lines(spec, twin, personas, apps)
    except TrafficGenPlanError as e:
        raise LinesError(f"trafficgen plan: {e}") from e
    attach = te_segment_attach([_row(r) for r in store.ops_for_deploy(te_deploy_id)])
    missing = sorted({leg.segment for g in tg.generators for leg in g.legs} - set(attach))
    if missing:
        raise LinesError(f"te deploy {te_deploy_id} records no network for segment(s) {missing}")
    known = {ln.id for ln in spec.lines}
    mults = {k: v for k, v in recorded_mults(store, tfc_deploy_id).items() if k in known}
    return PreparedLines(tg_plan=tg, attach=attach, binding=generator_binding(manifests, image, platform),
                         mults=mults,
                         warnings=[f"{len(tg.generators)} generator(s): " + ", ".join(g.host_id for g in tg.generators)])


def _generator_rows(store: Any, tfc_deploy_id: str) -> dict[str, dict[str, Any]]:
    """generator host id -> {vmid, start_op_id, document, lines, mults}."""
    rows = [_row(r) for r in store.ops_for_deploy(tfc_deploy_id)]
    vmids = {r["scope"].get("host"): (r["outputs"] or {}).get("vmid")
             for r in rows if r["op_name"] == "clone-vm" and r["status"] == "completed"}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = r["scope"].get("generator")
        if not g or r["status"] != "completed":
            continue
        rec = out.setdefault(g, {"vmid": vmids.get(g)})
        if r["op_name"] == "provision-generator":
            rec["document"] = r["inputs"].get("document") or {}
        if r["op_name"] == "start-traffic":
            rec["start_op_id"] = r["op_id"]
            rec["lines"] = r["inputs"].get("lines") or []
            rec["mults"] = {**(r["inputs"].get("mults") or {}), **((r["outputs"] or {}).get("mults") or {})}
    return out


def update_line_rate(store: Any, tfc_deploy_id: str, line_id: str, rate_cps: float, *,
                     run_update: Callable[[str, int, str, float], None]) -> dict[str, Any]:
    """Set one line's rate live. `run_update(generator, vmid, line, mult)` runs
    the `update-traffic` op (the caller binds the guest agent)."""
    if rate_cps <= 0:
        raise LinesError(f"rate_cps must be > 0, got {rate_cps}")
    gens = _generator_rows(store, tfc_deploy_id)
    owner = next((g for g, rec in gens.items() if line_id in rec.get("lines", [])), None)
    if owner is None:
        raise LinesError(f"no running traffic line {line_id!r} in {tfc_deploy_id}", status=404,
                         detail={"lines": sorted(l for rec in gens.values() for l in rec.get("lines", []))})
    rec = gens[owner]
    doc = rec.get("document") or {}
    line = next(ln for ln in doc.get("lines", []) if ln["id"] == line_id)
    planned = float(line["rate_cps"])
    budget = (doc.get("budget") or {}).get("cps")
    if budget:
        total = 0.0
        for g, r2 in gens.items():
            for ln in (r2.get("document") or {}).get("lines", []):
                m = r2.get("mults", {}).get(ln["id"], 1.0)
                total += rate_cps if ln["id"] == line_id else float(ln["rate_cps"]) * float(m)
        if total > float(budget):
            raise LinesError(f"rate {rate_cps:g} for {line_id} puts the twin at {total:g} cps, over budget {budget:g}",
                             status=409)
    mult = rate_cps / planned
    run_update(owner, int(rec["vmid"]), line_id, mult)
    mults = {**rec.get("mults", {}), line_id: mult}
    store.merge_op_outputs(tfc_deploy_id, rec["start_op_id"], {"mults": mults})
    return {"line": line_id, "generator": owner, "rate_cps": rate_cps, "planned_rate_cps": planned, "mult": mult}


def line_status(store: Any, tfc_deploy_id: str, *, read_status: Callable[[int], dict[str, Any]]) -> dict[str, Any]:
    """Per-line counters for the tfc status: `{line: {generator, rate_cps, mult,
    opened, established, errors, bytes, open, cps_now, state, ...}}`.
    `read_status(vmid)` returns the generator's controller stats."""
    out: dict[str, Any] = {}
    for g, rec in sorted(_generator_rows(store, tfc_deploy_id).items()):
        if not rec.get("vmid"):
            continue
        try:
            st = read_status(int(rec["vmid"]))
        except Exception as e:  # noqa: BLE001 -- one unreachable generator is reported, not fatal
            for lid in rec.get("lines", []):
                out[lid] = {"generator": g, "error": str(e)[:200]}
            continue
        doc_lines = {ln["id"]: ln for ln in (rec.get("document") or {}).get("lines", [])}
        for lid in rec.get("lines", []):
            live = (st.get("lines") or {}).get(lid, {})
            m = float(rec.get("mults", {}).get(lid, 1.0))
            planned = float(doc_lines.get(lid, {}).get("rate_cps", 0))
            out[lid] = {"generator": g, "from": doc_lines.get(lid, {}).get("from"), "to": doc_lines.get(lid, {}).get("to"),
                        "mix": doc_lines.get(lid, {}).get("mix"), "rate_cps": round(planned * m, 3), "mult": m,
                        **{k: live.get(k) for k in ("state", "opened", "established", "accepted", "errors", "bytes",
                                                    "open", "cps_now", "mult_effective", "capped", "error")
                           if k in live}}
    return out
