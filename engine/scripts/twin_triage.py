#!/usr/bin/env python3
"""Read-only triage of the twin-config roots (WORK_ORDER_twin_consolidation, Phase 1).

Walks `twins/` (Phase 2's migrated folders), the legacy `twin-configs/` and
`twin-configs-cast/`, `target-environments/`, `scenarios/`, classifies every file by kind, and runs the matching OFFLINE
check as a subprocess of the real CLI (or, for the tiers without a CLI
validate verb -- `exr`, personas -- a one-shot `python -c` over the same
loader the CLI uses):

    te (twin-config)  axrange te validate|plan --config <id> --twin-configs-dir <root>
    te (scenario)     axrange te validate|plan <scenario.yaml>
    tap / tfc         axrange tap|tfc validate <file>   (static lint; the live
                      half resolves the target from the state DB)
    exr               load_exercise_spec(<file>)
    persona           load_persona + validate_persona (no role universe)

Never deploys, never writes to the cluster or the state DB. `te plan` reads
`adapter.conf` (plan is offline today); tap/tfc read the state DB to resolve
a target. The only side effect is the CLI's own `/tmp/twin-config-*.yaml`
expansion scratch file.

    .venv/bin/python scripts/twin_triage.py [--images deploy-dev45] [--db DB] [--json]

Emits a markdown table (path | kind | class | first finding) on stdout;
`--json` emits the rows instead, for Phase 2 re-runs to diff against.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
ROOTS = ("twins", "twin-configs", "twin-configs-cast", "target-environments", "scenarios")
AXRANGE = str(REPO / ".venv" / "bin" / "axrange")
PYTHON = str(REPO / ".venv" / "bin" / "python")
# The live-target half of tap/tfc validate+plan: nothing deployed to bind to.
NO_LIVE = "neither a deploy id nor a live te deploy"
# A deploy id is `<scenario>-<8 hex>`; a twin/scenario name has no such tail.
PINNED_DEPLOY = re.compile(r"-[0-9a-f]{8}$")
GENERATED_SUFFIXES = (".topology.json", ".provenance.json", "topology.json",
                      "provenance.json", "layout.json")


def _run(cmd: list[str], timeout: int = 300, env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _first_error(out: str) -> str:
    for line in out.splitlines():
        s = line.strip()
        if "[error]" in s or s.startswith("axrange ") and ":" in s or "Error" in s:
            return s[:200]
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return (lines[-1] if lines else "")[:200]


def _kind(path: Path) -> str:
    name = path.name
    if name.startswith("state.db"):
        return "runtime-state"
    if name.endswith(".json"):
        if name.endswith(GENERATED_SUFFIXES) or ".scorecard." in name:
            return "generated"
        return "data"
    if name.endswith((".yaml", ".yml")):
        try:
            raw = YAML(typ="safe").load(path.read_text())
        except Exception:  # noqa: BLE001 -- triage: report, never crash
            return "yaml-unparseable"
        if not isinstance(raw, dict):
            return "yaml-other"
        if "twin_config" in raw:
            return "te-config"
        if "scenario" in raw:
            return "te-scenario"
        if "tap-plan" in raw:
            return "tap"
        if "traffic-profile" in raw:
            tp = raw["traffic-profile"] or {}
            if isinstance(tp, dict) and "roles" in tp and "assignments" not in tp \
                    and "replay" not in tp:
                return "tfc-roles-fragment"
            return "tfc"
        if "exercise" in raw:
            return "exr"
        if "persona" in raw:
            return "persona"
        if "binding" in raw:
            return "image-binding"
        return "yaml-other"
    if name.endswith((".sh", ".ps1", ".py", ".rules", ".conf")) or ".snippet" in name:
        return "script/asset"
    if name.endswith(".md"):
        return "doc"
    return "other"


def _config_id(path: Path) -> str:
    raw = YAML(typ="safe").load(path.read_text())
    return raw["twin_config"]["id"]


def _check_te(path: Path, kind: str, root: str, images: str) -> tuple[str, str]:
    if kind == "te-config":
        src = ["--config", _config_id(path), "--twin-configs-dir", root]
    else:
        src = [str(path)]
    rc, out = _run([AXRANGE, "te", "validate", *src, "--images", images])
    if rc != 0 or "Result: FAIL" in out:
        return "validate-fails", _first_error(out)
    rc, out = _run([AXRANGE, "te", "plan", *src, "--images", images])
    if rc != 0:
        return "plan-fails", _first_error(out)
    warn = "PASS-WITH-WARNINGS" in out
    return "valid", "validate: PASS-WITH-WARNINGS" if warn else ""


def _declared_target(path: Path) -> str:
    try:
        raw = YAML(typ="safe").load(path.read_text()) or {}
        body = next(iter(raw.values())) if isinstance(raw, dict) and raw else {}
        return str((body or {}).get("target") or "")
    except Exception:  # noqa: BLE001
        return ""


def _check_live_tier(path: Path, tier: str, db: list[str]) -> tuple[str, str]:
    # A twin's bundle-local personas load only via AXRANGE_PERSONAS_DIR
    # (the README runbooks export it by hand); mirror that for the file's
    # sibling `personas/` so a twin-local persona is not a false failure.
    env = {}
    if (path.parent / "personas").is_dir():
        env["AXRANGE_PERSONAS_DIR"] = str(path.parent / "personas")
    rc, out = _run([AXRANGE, tier, "validate", *db, str(path)], env=env)
    static_ok = "static OK" in out
    errors = [l.strip() for l in out.splitlines() if "[error]" in l or
              l.startswith(f"axrange {tier}")]
    non_live = [e for e in errors if NO_LIVE not in e]
    if static_ok and non_live and PINNED_DEPLOY.search(_declared_target(path)):
        # Target pinned to a concrete (now destroyed) deploy id: the live
        # half resolves against a dead twin. Retarget to the twin id.
        return "target-pinned-dead-deploy", non_live[0][:200]
    if not static_ok or non_live:
        return "validate-fails", (non_live or [_first_error(out)])[0][:200]
    if any(NO_LIVE in e for e in errors):
        return "plan-needs-live-target", "static OK; no live te deploy to resolve"
    if "static lint only" in out:
        return "valid", "static OK (declares no target; fragment/--target at deploy)"
    rc, out = _run([AXRANGE, tier, "plan", *db, str(path)], env=env)
    return ("valid", "") if rc == 0 else ("plan-fails", _first_error(out))


_PY_EXR = ("import sys; from axrange.o2control.exercise import load_exercise_spec as l; "
           "l(sys.argv[1]); print('OK')")
_PY_PSN = ("import sys; from ruamel.yaml import YAML; "
           "from axrange.personas.schema import load_persona; "
           "from axrange.validator.checks.personas import validate_persona; "
           "p=load_persona(YAML(typ='safe').load(open(sys.argv[1]))); "
           "bad=[f for f in validate_persona(p) if str(getattr(f,'severity','')).endswith('error')]; "
           "print('ERR', bad[0]) if bad else print('OK')")


def _check_py(path: Path, prog: str) -> tuple[str, str]:
    rc, out = _run([PYTHON, "-c", prog, str(path)], timeout=60)
    if rc == 0 and out.strip().endswith("OK"):
        return "validates-loader-only", ""
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return "validate-fails", (lines[-1] if lines else f"rc={rc}")[:200]


def triage(images: str, db: str | None = None, roots: tuple[str, ...] = ROOTS) -> list[dict]:
    db_args = ["--db", db] if db else []
    rows = []
    for root in roots:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(REPO)
            link = f" -> {path.readlink()}" if path.is_symlink() else ""
            kind = _kind(path)
            cls, note = "", ""
            if path.is_symlink():
                cls = "symlink"
            elif kind in ("te-config", "te-scenario"):
                cls, note = _check_te(path, kind, root, images)
            elif kind in ("tap", "tfc", "tfc-roles-fragment"):
                cls, note = _check_live_tier(
                    path, "tfc" if kind.startswith("tfc") else "tap", db_args)
            elif kind == "exr":
                cls, note = _check_py(path, _PY_EXR)
            elif kind == "persona":
                cls, note = _check_py(path, _PY_PSN)
            elif kind == "generated":
                cls = "generated-artifact"
            else:
                cls = "asset"
            rows.append({"path": f"{rel}{link}", "kind": kind, "class": cls, "note": note})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", default="deploy-dev45",
                    help="images root for te validate/plan (default: deploy-dev45)")
    ap.add_argument("--db", default=None,
                    help="state DB for tap/tfc target resolution (read-only; default: "
                         "the CLI's own). A twin stood up with its own --db only plans "
                         "against that DB")
    ap.add_argument("--root", action="append", default=None,
                    help=f"repo-relative root to walk (repeatable; default: {', '.join(ROOTS)})")
    ap.add_argument("--json", action="store_true", help="emit rows as JSON")
    args = ap.parse_args()
    rows = triage(args.images, args.db, tuple(args.root or ROOTS))
    if args.json:
        json.dump(rows, sys.stdout, indent=1)
        return 0
    print("| path | kind | class | first finding |\n|---|---|---|---|")
    for r in rows:
        note = r["note"].replace("|", "\\|")
        print(f"| `{r['path']}` | {r['kind']} | {r['class']} | {note} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
