"""Golden plans for twins without native switches (docs/design/native-switches.md
§11): their planner output must stay byte-identical to the pre-native-switch
engine. Only the sha256 of each snapshot is committed (corp-twin's is ~2 MB);
on a mismatch, diff `python -m tests.plan_golden_support --dump DIR` run here
against the same command at the commit that wrote the digests. Regenerate
ONLY when a change is meant to alter these plans:

    python -m tests.plan_golden_support   # from engine/
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "catalog" / "twins"
GOLDEN_FILE = Path(__file__).parent / "fixtures" / "plan_golden.json"
GOLDEN_TWINS = ("canvas-test", "datacenter", "corp-twin", "corp-enterprise")


def _plain(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if is_dataclass(v):
        return _plain(asdict(v))
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        items = [_plain(x) for x in v]
        return sorted(items, key=repr) if isinstance(v, (set, frozenset)) else items
    return v


def plan_snapshot(twin: str) -> str:
    from axrange.ir.parser import parse_ir
    from axrange.manifest.loader import load_registry
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.twinconfig.expand import expand_twin_config

    manifests = load_registry(root=Path(__file__).parent / "fixtures")
    ir, _ = expand_twin_config(twin, catalog_dir=CATALOG)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / f"{twin}.yaml"
        with path.open("w", encoding="utf-8") as f:
            YAML(typ="safe").dump(ir, f)
        parsed = parse_ir(path=str(path))
        plan = build_plan(parsed.ir, manifests, ClusterContext.single_node("px1"),
                          raw_ir=parsed.raw, scenario_dir=str(CATALOG / twin))
    snap = {
        "plan_id": plan.plan_id,
        "tier": plan.tier,
        "placement": dict(sorted(plan.placement.items())),
        "ops": {k: _plain(plan.ops[k]) for k in sorted(plan.ops)},
        "barriers": {k: _plain(plan.barriers[k]) for k in sorted(plan.barriers)},
        "edges": sorted([_plain(e) for e in plan.edges], key=lambda e: json.dumps(e, sort_keys=True)),
    }
    return json.dumps(snap, indent=1, sort_keys=True, default=str) + "\n"


def snapshot_digest(twin: str) -> str:
    return hashlib.sha256(plan_snapshot(twin).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        out = Path(sys.argv[2])
        out.mkdir(parents=True, exist_ok=True)
        for t in GOLDEN_TWINS:
            (out / f"{t}.json").write_text(plan_snapshot(t), encoding="utf-8")
        sys.exit(0)
    digests = {t: snapshot_digest(t) for t in GOLDEN_TWINS}
    GOLDEN_FILE.write_text(json.dumps(digests, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(digests, indent=1))
