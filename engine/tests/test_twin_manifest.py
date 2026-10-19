"""`twins/<id>/twin.yaml` — the minimal Phase 2 loader
(`docs/WORK_ORDER_twin_consolidation.md` §2, `docs/WORK_ORDER_corp_twin.md` S0).

Three groups: the manifest's own checks (tmp fixtures), catalog resolution
through a twin folder (old catalogs must keep resolving), the CLI's tap/tfc
`--twin` + folder defaults — and content guards on the real `corp-twin`, whose
tier files name node ids and segments that only exist in its expansion.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.twinconfig.errors import TwinConfigNotFoundError, TwinManifestError
from axrange.twinconfig.manifest import (
    default_twins_dir,
    find_twin_dir,
    load_twin_manifest,
    twin_dir_for_file,
)
from axrange.twinconfig.registry import (
    default_catalog_dir,
    resolve_twin_config_path,
    twin_config_source_dir,
)

CORP = default_twins_dir() / "corp-twin"


def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _twin(root: Path, twin_id: str = "t1", *, containment: str = "uncontained",
          egress: bool = False, te_name: str = "te.yaml", te_id: str | None = None,
          extra_tiers: str = "") -> Path:
    d = root / twin_id
    eg = "  egress: { phase: measure }\n" if egress else ""
    _w(d / te_name, f"twin_config:\n  id: {te_id or twin_id}\n{eg}")
    _w(d / "twin.yaml",
       f"twin:\n  id: {twin_id}\n  containment: {containment}\n"
       f"  tiers:\n    te: {{ default: {te_name} }}\n{extra_tiers}")
    return d


# ---------------------------------------------------------------------------
# the manifest's own checks
# ---------------------------------------------------------------------------
def test_minimal_manifest_loads(tmp_path):
    d = _twin(tmp_path)
    t = load_twin_manifest(d)
    assert t.id == "t1" and t.te_path == d / "te.yaml"
    assert t.personas_dir is None


def test_id_must_equal_folder_name(tmp_path):
    d = _twin(tmp_path)
    (d / "twin.yaml").write_text((d / "twin.yaml").read_text().replace("id: t1", "id: other", 1))
    with pytest.raises(TwinManifestError, match="folder"):
        load_twin_manifest(d)


def test_te_file_must_carry_the_twin_id(tmp_path):
    with pytest.raises(TwinManifestError, match="twin_config.id"):
        load_twin_manifest(_twin(tmp_path, te_id="someone-else"))


def test_containment_contained_needs_an_egress_block(tmp_path):
    with pytest.raises(TwinManifestError, match="declares no twin_config.egress"):
        load_twin_manifest(_twin(tmp_path, containment="contained", egress=False))
    load_twin_manifest(_twin(tmp_path, "t2", containment="contained", egress=True))


def test_containment_uncontained_refuses_an_egress_block(tmp_path):
    with pytest.raises(TwinManifestError, match="'contained'"):
        load_twin_manifest(_twin(tmp_path, containment="uncontained", egress=True))


def test_unknown_containment_value_refused(tmp_path):
    with pytest.raises(TwinManifestError, match="containment"):
        load_twin_manifest(_twin(tmp_path, containment="sealed"))


def test_missing_tier_file_refused(tmp_path):
    d = _twin(tmp_path, extra_tiers="    tap: { default: tap/default.yaml }\n")
    with pytest.raises(TwinManifestError, match="does not exist"):
        load_twin_manifest(d)


@pytest.mark.parametrize("rel", ["../t0/tap.yaml", "/etc/passwd.yaml"])
def test_tier_file_must_stay_inside_the_folder(tmp_path, rel):
    d = _twin(tmp_path, extra_tiers=f"    tap: {{ default: {rel!r} }}\n")
    with pytest.raises(TwinManifestError, match="inside the twin"):
        load_twin_manifest(d)


def test_unknown_manifest_key_refused(tmp_path):
    d = _twin(tmp_path)
    _w(d / "twin.yaml", (d / "twin.yaml").read_text() + "  colour: blue\n")
    with pytest.raises(TwinManifestError):
        load_twin_manifest(d)


def test_variant_by_stem_or_path_and_undeclared_refused(tmp_path):
    d = _twin(tmp_path, extra_tiers=(
        "    tap: { default: tap/default.yaml, variants: [tap/wide.yaml] }\n"
        "    exr: { variants: [exr/kill.yaml] }\n"
    ))
    for rel in ("tap/default.yaml", "tap/wide.yaml", "tap/stray.yaml", "exr/kill.yaml"):
        _w(d / rel, "x: 1\n")
    t = load_twin_manifest(d)
    assert t.tier_path("tap") == d / "tap/default.yaml"
    assert t.tier_path("tap", "wide") == d / "tap/wide.yaml"
    assert t.tier_path("tap", "tap/wide.yaml") == d / "tap/wide.yaml"
    assert t.tier_path("tap", "default") == d / "tap/default.yaml"
    with pytest.raises(TwinManifestError, match="no 'tap' variant 'stray'"):
        t.tier_path("tap", "stray")  # present on disk, not declared
    with pytest.raises(TwinManifestError, match="has no default"):
        t.tier_path("exr")
    with pytest.raises(TwinManifestError, match="declares no 'tfc' tier"):
        t.tier_path("tfc")


def test_find_twin_dir_later_root_wins_and_not_found(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _twin(a)
    _twin(b)
    warnings: list[str] = []
    assert find_twin_dir("t1", [a, b], warnings=warnings) == b / "t1"
    assert len(warnings) == 1 and str(a / "t1") in warnings[0]
    with pytest.raises(TwinConfigNotFoundError):
        find_twin_dir("nope", [a, b])


def test_twin_dir_for_file(tmp_path):
    d = _twin(tmp_path)
    assert twin_dir_for_file(d / "te.yaml") == d
    assert twin_dir_for_file(d / "tap" / "x.yaml") == d
    assert twin_dir_for_file(tmp_path / "loose.yaml") is None


# ---------------------------------------------------------------------------
# catalog resolution through a twin folder
# ---------------------------------------------------------------------------
def test_resolve_prefers_the_manifest_te_default(tmp_path):
    d = _twin(tmp_path, te_name="main.yaml")
    _w(d / "t1.yaml", "twin_config:\n  id: t1\n")  # the OLD name, must lose
    assert resolve_twin_config_path("t1", catalog_dir=tmp_path) == d / "main.yaml"


def test_resolve_te_yaml_without_manifest(tmp_path):
    p = _w(tmp_path / "t1" / "te.yaml", "twin_config:\n  id: t1\n")
    assert resolve_twin_config_path("t1", catalog_dir=tmp_path) == p


def test_resolve_twins_root_shadows_old_catalog_copy(tmp_path):
    old = _w(tmp_path / "old" / "t1" / "t1.yaml", "twin_config:\n  id: t1\n")
    new_dir = _twin(tmp_path / "new")
    assert resolve_twin_config_path(
        "t1", catalog_dir=f"{old.parent.parent}:{tmp_path / 'new'}"
    ) == new_dir / "te.yaml"


def test_a_broken_manifest_fails_loud_rather_than_being_stepped_around(tmp_path):
    d = _twin(tmp_path, te_id="wrong")
    with pytest.raises(TwinManifestError):
        resolve_twin_config_path("t1", catalog_dir=tmp_path)
    assert d.is_dir()


def test_default_search_path_finds_corp_twin_and_still_finds_old_ids():
    assert resolve_twin_config_path("corp-twin") == CORP / "te.yaml"
    assert twin_config_source_dir("corp-twin") == CORP.resolve()
    # svc-engines moved from the legacy root into `twins/svc-engines/`
    # (twin consolidation Phase 2); its id resolves unchanged.
    assert resolve_twin_config_path("svc-engines") == (
        default_catalog_dir() / "svc-engines" / "te.yaml"
    )


# ---------------------------------------------------------------------------
# CLI: tap/tfc --twin and the folder defaults
# ---------------------------------------------------------------------------
@pytest.fixture
def persona_env(monkeypatch):
    """Run with no AXRANGE_PERSONAS_DIR, and put the global persona catalog
    back afterwards -- the resolver reloads it in place."""
    from axrange.personas.registry import reload_personas

    monkeypatch.delenv("AXRANGE_PERSONAS_DIR", raising=False)
    yield monkeypatch
    monkeypatch.undo()
    reload_personas()


def _ns(**kw) -> argparse.Namespace:
    base = dict(spec=None, twin=None, variant=None, twins_dir=None, target=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_twin_flag_picks_default_and_variant(persona_env):
    from axrange.cli.main import _resolve_twin_tier_spec
    from axrange.personas.registry import is_known_id

    a = _ns(twin="corp-twin")
    assert _resolve_twin_tier_spec(a, "tfc", "validate") is None
    assert a.spec == str(CORP / "tfc" / "default.yaml")
    # the file declares `target: corp-twin`, so no default was needed
    assert a.target is None
    assert os.environ["AXRANGE_PERSONAS_DIR"].split(":")[0] == str(CORP / "personas")
    assert is_known_id("cast-generic-endpoint-local")

    b = _ns(twin="corp-twin", variant="uplink-span")
    assert _resolve_twin_tier_spec(b, "tap", "plan") is None
    assert b.spec == str(CORP / "tap" / "uplink-span.yaml")


def test_cli_env_var_still_overrides_twin_personas(persona_env, tmp_path):
    from axrange.cli.main import _resolve_twin_tier_spec

    persona_env.setenv("AXRANGE_PERSONAS_DIR", str(tmp_path))
    assert _resolve_twin_tier_spec(_ns(twin="corp-twin"), "tfc", "plan") is None
    # twin dir FIRST, the operator's dir LAST -- later wins a persona id collision
    assert os.environ["AXRANGE_PERSONAS_DIR"].split(":") == [str(CORP / "personas"), str(tmp_path)]


def test_cli_file_in_twin_folder_defaults_target_to_twin_id(persona_env, tmp_path):
    from axrange.cli.main import _resolve_twin_tier_spec

    d = _twin(tmp_path, extra_tiers="    tap: { default: tap/a.yaml }\n")
    _w(d / "tap" / "a.yaml", "tap-plan:\n  bindings: []\n")
    a = _ns(spec=str(d / "tap" / "a.yaml"))
    assert _resolve_twin_tier_spec(a, "tap", "validate") is None
    assert a.target == "t1"
    b = _ns(spec=str(d / "tap" / "a.yaml"), target="explicit")
    assert _resolve_twin_tier_spec(b, "tap", "validate") is None
    assert b.target == "explicit"


def test_cli_file_outside_any_twin_is_untouched(persona_env, tmp_path):
    from axrange.cli.main import _resolve_twin_tier_spec

    f = _w(tmp_path / "loose.yaml", "tap-plan:\n  bindings: []\n")
    a = _ns(spec=str(f))
    assert _resolve_twin_tier_spec(a, "tap", "validate") is None
    assert a.target is None and "AXRANGE_PERSONAS_DIR" not in os.environ


@pytest.mark.parametrize("kw,msg", [
    (dict(variant="x"), "--variant only makes sense with --twin"),
    (dict(twin="corp-twin", spec="a.yaml"), "not both"),
    (dict(), "is required"),
    (dict(twin="no-such-twin"), "no twin 'no-such-twin'"),
    (dict(twin="corp-twin", variant="nope"), "no 'tap' variant 'nope'"),
])
def test_cli_twin_flag_errors(persona_env, capsys, kw, msg):
    from axrange.cli.main import _resolve_twin_tier_spec

    assert _resolve_twin_tier_spec(_ns(**kw), "tap", "validate") == 2
    assert msg in capsys.readouterr().err


# ---------------------------------------------------------------------------
# content guards on the real corp-twin
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corp_ir():
    from axrange.twinconfig.expand import expand_twin_config

    ir, _params = expand_twin_config("corp-twin")
    return ir


def test_corp_twin_manifest_is_valid_and_contained():
    t = load_twin_manifest(CORP)
    assert t.manifest.cluster == "dev45" and t.manifest.containment == "contained"
    assert t.personas_dir == CORP / "personas"


def test_corp_twin_expands_contained_behind_one_debian_router(corp_ir):
    infra = corp_ir["layers"]["infra"]
    routers = [n for n in infra["nodes"] if n.get("kind") == "router"]
    assert [r["id"] for r in routers] == ["hq-mx"]
    assert "vyos" not in routers[0]["image"]  # VyOS is refused under egress:
    assert corp_ir["egress"]["phase"] in ("provision", "measure")
    assert [n["id"] for n in infra["nodes"] if n.get("role") == "sink"] == ["sink"]
    # PVE's ceiling is net0..net31: the router's legs + its WAN NIC must fit
    assert len(routers[0]["nets"]) + 1 <= 32


def test_corp_twin_tfc_nodes_exist_in_the_default_expansion(corp_ir):
    """A `node:` that is not a host is a hard error at apply -- guard the
    derived default profile against drift from te.yaml."""
    ids = {n["id"] for n in corp_ir["layers"]["infra"]["nodes"]}
    roles = {n.get("role") for n in corp_ir["layers"]["infra"]["nodes"]}
    y = YAML(typ="safe")
    for name in ("default", "roles", "exercise"):
        spec = y.load((CORP / "tfc" / f"{name}.yaml").read_text())["traffic-profile"]
        assert spec["target"] == "corp-twin"
        for a in spec["assignments"]:
            if "node" in a:
                assert a["node"] in ids, (name, a["node"])
            else:
                assert a["role"] in roles, (name, a["role"])


def test_corp_twin_tap_targets_exist_in_the_default_expansion(corp_ir):
    nets = {n["id"] for n in corp_ir["layers"]["infra"]["networks"]}
    y = YAML(typ="safe")
    for f in sorted((CORP / "tap").glob("*.yaml")):
        plan = y.load(f.read_text())["tap-plan"]
        assert plan["target"] == "corp-twin"
        for b in plan["bindings"]:
            assert b["selector"]["target"] in nets, (f.name, b["selector"]["target"])


def test_corp_twin_personas_dial_the_one_sink():
    text = "".join(p.read_text() for p in (CORP / "personas").glob("*.yaml"))
    assert not re.search(r"peer_role:\s*internet-sink", text)


# ---------------------------------------------------------------------------
# fix round 1: hardening
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["../corp-twin", "a/b", "..", "."])
def test_find_twin_dir_refuses_a_path_as_an_id(tmp_path, bad):
    with pytest.raises(TwinManifestError, match="not a folder name"):
        find_twin_dir(bad, [tmp_path])


def test_tier_file_symlinked_outside_the_folder_refused(tmp_path):
    outside = _w(tmp_path / "elsewhere" / "x.yaml", "tap-plan: {}\n")
    d = _twin(tmp_path / "root", extra_tiers="    tap: { default: tap/x.yaml }\n")
    (d / "tap").mkdir()
    (d / "tap" / "x.yaml").symlink_to(outside)
    with pytest.raises(TwinManifestError, match="resolves outside"):
        load_twin_manifest(d)


def test_manifest_models_are_frozen_all_the_way_down(tmp_path):
    """LES-284/287: the loaded twin is memoised, so the object a cache HIT
    returns must refuse attribute AND container mutation."""
    d = _twin(tmp_path, extra_tiers="    tap: { default: tap/a.yaml, variants: [tap/b.yaml] }\n")
    _w(d / "tap" / "a.yaml", "x: 1\n")
    _w(d / "tap" / "b.yaml", "x: 1\n")
    load_twin_manifest(d)
    hit = load_twin_manifest(d)
    with pytest.raises(Exception):
        hit.manifest.id = "other"
    with pytest.raises(Exception):
        hit.manifest.tiers.tap.default = "tap/b.yaml"
    assert isinstance(hit.manifest.tiers.tap.variants, tuple)
    with pytest.raises(AttributeError):
        hit.manifest.tiers.tap.variants.append("tap/c.yaml")  # type: ignore[attr-defined]
    third = load_twin_manifest(d)
    assert third.id == "t1" and third.manifest.tiers.tap.variants == ("tap/b.yaml",)


def test_load_twin_manifest_is_memoised_and_invalidated_by_either_file(tmp_path, monkeypatch):
    import axrange.twinconfig.manifest as mf

    d = _twin(tmp_path, containment="contained", egress=True)
    calls = []
    real = mf.load_yaml_raw
    monkeypatch.setattr(mf, "load_yaml_raw", lambda *a, **k: (calls.append(a[0]), real(*a, **k))[1])
    first = load_twin_manifest(d)
    n = len(calls)
    assert n == 2  # twin.yaml + te.yaml
    assert load_twin_manifest(d) is first and len(calls) == n  # a stat, not a parse
    # the te file changes: its egress block goes, so the cached "contained"
    # verdict must NOT survive
    (d / "te.yaml").write_text("twin_config:\n  id: t1\n  description: now uncontained\n")
    with pytest.raises(TwinManifestError, match="declares no twin_config.egress"):
        load_twin_manifest(d)
    # a failure is never cached: fixing the manifest is seen at once
    (d / "twin.yaml").write_text((d / "twin.yaml").read_text().replace("contained", "uncontained"))
    assert load_twin_manifest(d).manifest.containment == "uncontained"


def test_resolve_warns_when_a_later_root_shadows_an_earlier_one(tmp_path, capsys):
    old = _w(tmp_path / "old" / "t1" / "t1.yaml", "twin_config:\n  id: t1\n")
    _twin(tmp_path / "new")
    roots = f"{tmp_path / 'old'}:{tmp_path / 'new'}"
    warnings: list[str] = []
    hit = resolve_twin_config_path("t1", catalog_dir=roots, warnings=warnings)
    assert hit == tmp_path / "new" / "t1" / "te.yaml"  # later root still wins
    assert len(warnings) == 1 and str(old) in warnings[0] and "shadows" in warnings[0]
    # no list passed -> stderr, never silent
    resolve_twin_config_path("t1", catalog_dir=roots)
    assert "shadows" in capsys.readouterr().err
    # no collision -> no warning
    quiet: list[str] = []
    resolve_twin_config_path("t1", catalog_dir=str(tmp_path / "new"), warnings=quiet)
    assert quiet == []


def test_materialize_threads_the_shadow_warning(tmp_path):
    from axrange.twinconfig.expand import materialize_twin_config

    src = (CORP / "te.yaml").read_text()
    _w(tmp_path / "old" / "corp-twin" / "corp-twin.yaml", src)
    warnings: list[str] = []
    out = materialize_twin_config(
        "corp-twin", catalog_dir=[tmp_path / "old", default_twins_dir()], warnings=warnings,
    )
    out.unlink()
    assert any("shadows" in w and "corp-twin.yaml" in w for w in warnings)


def test_list_dedupes_across_roots_later_wins(tmp_path):
    from axrange.twinconfig.registry import list_twin_configs

    _w(tmp_path / "old" / "t1" / "t1.yaml", "twin_config:\n  id: t1\n  description: old\n")
    _w(tmp_path / "old" / "t2" / "t2.yaml", "twin_config:\n  id: t2\n")
    d = _twin(tmp_path / "new")
    entries = list_twin_configs([tmp_path / "old", tmp_path / "new"])
    assert [(e.id, e.path) for e in entries] == [
        ("t1", d / "te.yaml"), ("t2", tmp_path / "old" / "t2" / "t2.yaml"),
    ]


def test_list_flags_a_broken_manifest_and_skips_generated(tmp_path):
    from axrange.twinconfig.registry import list_twin_configs

    good = _twin(tmp_path, "good")
    _w(good / "generated" / "expanded.yaml", "twin_config:\n  id: good-expanded\n")
    _twin(tmp_path, "bad", te_id="bad")
    (tmp_path / "bad" / "twin.yaml").write_text(
        (tmp_path / "bad" / "twin.yaml").read_text().replace("uncontained", "sealed")
    )
    by_id = {e.id: e for e in list_twin_configs(tmp_path)}
    assert set(by_id) == {"good", "bad"}  # generated/ is build output, not catalog
    assert by_id["good"].error is None
    assert by_id["bad"].error and "sealed" in by_id["bad"].error


def test_cli_announces_a_persona_override(persona_env, tmp_path, capsys):
    from axrange.cli.main import _resolve_twin_tier_spec

    src = CORP / "personas" / "cast-generic-endpoint-local.yaml"
    _w(tmp_path / "cast-generic-endpoint-local.yaml", src.read_text())
    persona_env.setenv("AXRANGE_PERSONAS_DIR", str(tmp_path))
    assert _resolve_twin_tier_spec(_ns(twin="corp-twin"), "tfc", "plan") is None
    err = capsys.readouterr().err
    assert "overrides" in err and "cast-generic-endpoint-local" in err and str(tmp_path) in err
    # nothing overridden -> nothing said
    persona_env.setenv("AXRANGE_PERSONAS_DIR", str(tmp_path / "empty"))
    assert _resolve_twin_tier_spec(_ns(twin="corp-twin"), "tfc", "plan") is None
    assert "overrides" not in capsys.readouterr().err


def test_cli_announces_the_defaulted_target(persona_env, tmp_path, capsys):
    from axrange.cli.main import _resolve_twin_tier_spec

    d = _twin(tmp_path, extra_tiers="    tap: { default: tap/a.yaml }\n")
    _w(d / "tap" / "a.yaml", "tap-plan:\n  bindings: []\n")
    assert _resolve_twin_tier_spec(_ns(spec=str(d / "tap" / "a.yaml")), "tap", "validate") is None
    assert "[info] target defaulted to twin t1" in capsys.readouterr().err


def test_e2e_tfc_validate_twin_loads_the_twins_personas(persona_env, tmp_path, capsys):
    """Through `main()`'s dispatch, not the helper: `--twin` picks the file
    AND puts the twin's personas on the registry."""
    from axrange.cli import main as climain
    from axrange.personas.registry import is_known_id, reload_personas

    reload_personas()  # the env var is unset (fixture): shipped catalog only
    assert not is_known_id("cast-generic-endpoint-local")
    rc = climain.main(["tfc", "validate", "--twin", "corp-twin",
                       "--db", str(tmp_path / "absent.db")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert str(CORP / "tfc" / "default.yaml") in out and "static OK" in out
    assert "persona:cast-" in out
    assert is_known_id("cast-generic-endpoint-local")


# ---------------------------------------------------------------------------
# the containment read-back gate on tap/tfc deploy
# ---------------------------------------------------------------------------
@pytest.fixture
def gate_env(persona_env, tmp_path, monkeypatch):
    """A real (empty) state DB, the target resolved to a fixed deploy id, the
    posture read-back canned, and `apply_*` replaced by a sentinel so a test
    sees whether the gate let the deploy through."""
    from axrange.o2control import taps, traffic
    from axrange.planner import StateStore
    from axrange.planner import egress as egress_mod

    db = tmp_path / "state.db"
    StateStore(str(db)).close()
    state = {"posture": None, "resolved": []}

    def _resolve(store, target):
        state["resolved"].append(target)
        return "corp-twin-abc123"

    monkeypatch.setattr(taps, "resolve_target_deploy", _resolve)
    monkeypatch.setattr(egress_mod, "egress_posture_from_ops", lambda ops: state["posture"])

    # The gate's LIVE read (`containment.egress_live(..., "show")`): canned
    # from the same posture -- an unapplied one reads as a router that has
    # not finished configure-router (what the real read raises).
    from axrange.cli import main as climain
    from axrange.o2control import containment

    def _live(store, deploy_id, action, *, ga, timeout_s=300, now=None):
        assert action == "show"
        state["probed"].append(deploy_id)
        p = state["posture"]
        if p.phase_applied is None:
            raise containment.ContainmentError("configure-router[hq-mx] has not completed")
        return containment.EgressLiveResult(
            deploy_id=deploy_id, router_host="hq-mx", vmid=1, action="show",
            phase=p.phase_applied, since=None, healthy=True,
            recorded=p.phase_applied, read_at="2026-09-26T00:00:00Z")

    state["probed"] = []
    monkeypatch.setattr(containment, "egress_live", _live)
    monkeypatch.setattr(climain, "_egress_guest_agent", lambda args: object())

    def _applied(**kw):
        raise (taps.TapPlanError if "tap" in str(kw["spec_path"]).split("/")[-2]
               else traffic.TrafficPlanError)("APPLY-REACHED", status=400)

    monkeypatch.setattr(taps, "apply_tap_plan", _applied)
    monkeypatch.setattr(traffic, "apply_traffic_profile", _applied)
    return db, state


def _posture(phase_applied, phase_declared="provision"):
    from axrange.planner.egress import EgressPosture

    return EgressPosture(router_host="hq-mx", phase_declared=phase_declared,
                         phase_applied=phase_applied, sink_ip="10.1.1.1")


@pytest.mark.parametrize("tier", ["tap", "tfc"])
def test_deploy_refuses_an_uncontained_target(gate_env, capsys, tier):
    from axrange.cli import main as climain

    db, state = gate_env
    state["posture"] = _posture("provision")
    rc = climain.main([tier, "deploy", "--twin", "corp-twin", "--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "APPLY-REACHED" not in err
    assert "corp-twin" in err and "corp-twin-abc123" in err and "'provision'" in err
    assert "te egress corp-twin-abc123 --phase measure" in err and "§11" in err
    assert "--allow-uncontained" in err
    assert state["resolved"] == ["corp-twin"]


@pytest.mark.parametrize("tier", ["tap", "tfc"])
def test_deploy_refuses_a_declared_but_unapplied_posture(gate_env, capsys, tier):
    from axrange.cli import main as climain

    db, state = gate_env
    state["posture"] = _posture(None, "measure")
    assert climain.main([tier, "deploy", "--twin", "corp-twin", "--db", str(db)]) == 2
    err = capsys.readouterr().err
    assert "could not be read" in err and "has not completed" in err
    assert "APPLY-REACHED" not in err


@pytest.mark.parametrize("tier", ["tap", "tfc"])
def test_allow_uncontained_proceeds_loudly(gate_env, capsys, tier):
    from axrange.cli import main as climain

    db, state = gate_env
    state["posture"] = _posture("provision")
    rc = climain.main([tier, "deploy", "--twin", "corp-twin", "--db", str(db),
                       "--allow-uncontained"])
    err = capsys.readouterr().err
    assert rc == 2 and "APPLY-REACHED" in err  # got past the gate
    assert "[warn]" in err and "NOT contained" in err


@pytest.mark.parametrize("tier", ["tap", "tfc"])
@pytest.mark.parametrize("posture", [None, "measure"])
def test_no_egress_or_contained_target_is_unaffected(gate_env, capsys, tier, posture):
    from axrange.cli import main as climain

    db, state = gate_env
    state["posture"] = _posture("measure", "measure") if posture else None
    rc = climain.main([tier, "deploy", "--twin", "corp-twin", "--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2 and "APPLY-REACHED" in err
    assert "refusing" not in err and "[warn]" not in err
    # No declared posture -> no live probe; a declared one is always probed.
    assert state["probed"] == ([] if posture is None else ["corp-twin-abc123"])
