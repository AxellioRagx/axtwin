"""`scripts/twin_triage.py` (WORK_ORDER_twin_consolidation Phase 1) -- the
pure classification half: file-kind detection and the pinned-deploy-id
pattern. The subprocess half (the real CLI) is exercised by running the
script; these tests keep the kind table honest without a CLI round-trip.

Loaded via `spec_from_file_location` (LES-029)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "twin_triage.py"


@pytest.fixture(scope="module")
def triage():
    spec = importlib.util.spec_from_file_location("twin_triage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["twin_triage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name, body, kind",
    [
        ("x.yaml", "twin_config:\n  id: x\n", "te-config"),
        ("scenario.yaml", "scenario:\n  id: x\n", "te-scenario"),
        ("tap-a.yaml", "tap-plan:\n  target: x\n  bindings: []\n", "tap"),
        ("tfc-a.yaml", "traffic-profile:\n  target: x\n  assignments: []\n", "tfc"),
        ("roles.yaml", "traffic-profile:\n  roles: []\n", "tfc-roles-fragment"),
        ("exr-a.yaml", "exercise:\n  twin: x\n", "exr"),
        ("p.yaml", "persona:\n  id: p\n", "persona"),
        ("b.yaml", "binding:\n  image: i\n", "image-binding"),
        ("odd.yaml", "- a list\n", "yaml-other"),
        ("broken.yaml", "a: [unclosed\n", "yaml-unparseable"),
    ],
)
def test_yaml_kind_is_read_from_the_top_level_key(triage, tmp_path, name, body, kind):
    f = tmp_path / name
    f.write_text(body)
    assert triage._kind(f) == kind


@pytest.mark.parametrize(
    "name, kind",
    [
        ("x.topology.json", "generated"),
        ("x.provenance.json", "generated"),
        ("layout.json", "generated"),
        ("x.scorecard.x-0e4f7d58.json", "generated"),
        ("after.json", "data"),
        ("state.db-wal", "runtime-state"),
        ("serve-sink.sh", "script/asset"),
        ("enable-rdp.ps1", "script/asset"),
        ("README.md", "doc"),
    ],
)
def test_non_yaml_kind_is_read_from_the_name(triage, tmp_path, name, kind):
    f = tmp_path / name
    f.write_text("{}")
    assert triage._kind(f) == kind


def test_a_deploy_id_is_pinned_and_a_twin_name_is_not(triage):
    assert triage.PINNED_DEPLOY.search("pit-395a61f04e-1780405200-3caf94a1")
    assert not triage.PINNED_DEPLOY.search("pit-395a61f04e-1780405200")
    assert not triage.PINNED_DEPLOY.search("corp-enterprise")


def test_declared_target_reads_the_tier_body(triage, tmp_path):
    f = tmp_path / "tap.yaml"
    f.write_text("tap-plan:\n  target: corp-enterprise\n  bindings: []\n")
    assert triage._declared_target(f) == "corp-enterprise"
    g = tmp_path / "roles.yaml"
    g.write_text("traffic-profile:\n  roles: []\n")
    assert triage._declared_target(g) == ""
