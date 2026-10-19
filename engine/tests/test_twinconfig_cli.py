"""CLI wiring for twin-configs (docs/WORK_ORDER_twin_configs.md §2/§5):
`axrange twin-config list|show` (outside the grid) and `axrange te
{validate,plan} --config <id> --param k=v` (fits the existing grid; deploy
is exercised via `_load_planning_inputs`/`materialize_twin_config` directly
elsewhere since a real `te deploy` needs adapter.conf + a cluster)."""
from __future__ import annotations

import shutil
from pathlib import Path

import axrange.cli.main as climain
from axrange.twinconfig.registry import default_catalog_dir


def test_twin_config_list(capsys):
    rc = climain.main(["twin-config", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    for expected in ("sme-flat", "corp-enterprise", "datacenter", "ics-ot", "dmz-perimeter"):
        assert expected in out


def test_twin_config_show(capsys):
    rc = climain.main(["twin-config", "show", "sme-flat"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "twin-config: sme-flat" in out
    # No `tap_points` section any more (`docs/WORK_ORDER_tap_eligibility.md`
    # D3/D5, Authority: user, 2026-07-28) -- a twin config authors no
    # capture intent at all.
    assert "tap_points" not in out


def test_twin_config_show_json(capsys):
    rc = climain.main(["twin-config", "show", "sme-flat", "--json"])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "sme-flat"
    assert "tap_points" not in payload


def test_twin_config_show_heavily_templated_config_does_not_crash(capsys):
    """`corp-enterprise`/`datacenter` carry unsubstituted `${...}` tokens in
    MORE field shapes than `sme-flat` -- a bare-string `count:`, a `joins:`,
    an `identity.domain:`, a `when:`-gated node/segment. `show` loads the
    RAW (unsubstituted) TwinConfig (docs/WORK_ORDER_twin_configs.md's own
    "template-tolerant field types" lesson) -- confirm none of those crash
    the CLI formatter."""
    for config_id in ("corp-enterprise", "datacenter"):
        rc = climain.main(["twin-config", "show", config_id])
        assert rc == 0, config_id
        out = capsys.readouterr().out
        assert f"twin-config: {config_id}" in out
        assert "tap_points" not in out

        rc_json = climain.main(["twin-config", "show", config_id, "--json"])
        assert rc_json == 0, config_id
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["id"] == config_id


# `test_twin_config_show_warns_on_unknown_persona_to_stderr_only` REMOVED
# (2026-08-04, `docs/WORK_ORDER_role_persona_tier.md`): `twin-config show`
# no longer cross-checks personas, because a twin config no longer declares
# any. Personas are validated fail-loud against the live registry at
# `tfc validate|plan|deploy` instead -- see `tests/test_traffic_tier.py`.


def test_twin_config_show_unknown_id_errors(capsys):
    rc = climain.main(["twin-config", "show", "does-not-exist"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does-not-exist" in err


def test_te_validate_with_config_expands_and_validates(capsys):
    rc = climain.main([
        "te", "validate", "--config", "sme-flat", "--param", "N_WORKSTATIONS=2",
        "--images", "tests/fixtures",
    ])
    out = capsys.readouterr().out
    assert "expanded twin-config 'sme-flat'" in out
    assert rc == 0


def test_te_validate_config_and_positional_both_set_is_an_error(capsys):
    rc = climain.main([
        "te", "validate", "/nonexistent/scenario.yaml", "--config", "sme-flat",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "either a scenario.yaml positional or --config" in err


def test_te_validate_param_without_config_is_an_error(capsys):
    rc = climain.main(["te", "validate", "--param", "N_WORKSTATIONS=2"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--param only makes sense with --config" in err


def test_te_validate_unknown_config_id_errors(capsys):
    rc = climain.main(["te", "validate", "--config", "does-not-exist-config"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does-not-exist-config" in err


def test_te_plan_with_config_and_param_builds_a_plan(tmp_path, capsys, monkeypatch):
    # te plan needs adapter.conf -- point it at a minimal one so the plan
    # build succeeds without touching a real cluster (build_plan_and_ctx
    # only needs DEFAULT_NODE to construct a single-node ClusterContext).
    conf = tmp_path / "adapter.conf"
    conf.write_text("DEFAULT_NODE=px1\n")
    rc = climain.main([
        "te", "plan", "--config", "corp-enterprise", "--param", "N_WORKSTATIONS=2",
        "--adapter-config", str(conf), "--images", "tests/fixtures",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Plan:" in out


def _make_synthetic_repo_root(tmp_path: Path) -> Path:
    """A minimal repo-root layout: `twin-configs/sme-flat.yaml` (copied from
    the real catalog) + a co-located `images/` catalog (copied from the test
    fixtures -- the shipped `tests/fixtures/images` is the only COMPLETE
    manifest set covering every image id the starter twin-configs reference;
    the real repo doesn't ship a top-level `images/` yet, so this stands in
    for "a repo root with a co-located images/ catalog" per review S4)."""
    root = tmp_path / "synthetic-repo"
    tc_dir = root / "twin-configs"
    tc_dir.mkdir(parents=True)
    # Source is now `twins/sme-flat/te.yaml` (the twin-folder layout). Copy
    # into a FLAT dest here on purpose — the loader still accepts the flat
    # `twin-configs/<id>.yaml` layout (back-compat), so this synthetic repo
    # also exercises that path while the real catalog covers twin folders.
    shutil.copy(default_catalog_dir() / "sme-flat" / "te.yaml", tc_dir / "sme-flat.yaml")
    shutil.copytree(Path("tests/fixtures/images"), root / "images")
    return root


def test_config_images_root_defaults_to_cwd_when_omitted(tmp_path, capsys, monkeypatch):
    """Review S4: `te validate --config sme-flat` (no `--images`, no
    `--twin-configs-dir`) must find a co-located `images/` catalog at cwd --
    not fail REF-120 against the materialized tempfile's (always-empty)
    parent directory."""
    root = _make_synthetic_repo_root(tmp_path)
    monkeypatch.chdir(root)
    rc = climain.main(["te", "validate", "--config", "sme-flat"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "REF-120" not in out


def test_config_images_root_defaults_to_twin_configs_dir_parent(tmp_path, capsys):
    """Same as above, but invoked from an UNRELATED cwd with an explicit
    `--twin-configs-dir` -- the images-root default must follow the
    catalog dir's PARENT, not bare cwd, so this also works from outside the
    synthetic repo root."""
    root = _make_synthetic_repo_root(tmp_path)
    rc = climain.main([
        "te", "validate", "--config", "sme-flat",
        "--twin-configs-dir", str(root / "twin-configs"),
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "REF-120" not in out


def test_explicit_images_flag_overrides_the_config_default(tmp_path, capsys):
    """An explicit `--images` always wins over the `--config` default, even
    if it points somewhere with no manifests at all (REF-120 fires, proving
    the explicit flag -- not the default -- was actually used)."""
    root = _make_synthetic_repo_root(tmp_path)
    empty_images = tmp_path / "empty"
    empty_images.mkdir()
    rc = climain.main([
        "te", "validate", "--config", "sme-flat",
        "--twin-configs-dir", str(root / "twin-configs"),
        "--images", str(empty_images),
    ])
    out = capsys.readouterr().out
    assert rc != 0
    assert "REF-120" in out


# ---------------------------------------------------------------------------
# The deploy gate (`docs/WORK_ORDER_deploy_gate.md` §4.5 + D4): `twin-config
# show` gains an UNCAPPED `deploy gate:` section and the one genuinely new
# flag, `--param`, so a threshold can be previewed before it is deployed.
# ---------------------------------------------------------------------------
_GATE_FIXT_DIR = str(Path(__file__).parent / "fixtures" / "twin-configs")


def test_twin_config_show_prints_the_deploy_gate_section(capsys):
    rc = climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", _GATE_FIXT_DIR,
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # The declared bar (`params: {DEPLOY_MIN_OCCUPANCY: 0.8}`) is what it
    # gates at with no override.
    assert "deploy gate:" in out
    assert "effective DEPLOY_MIN_OCCUPANCY=0.8" in out
    assert "gated out node 'worker' (count: 3)" in out
    assert "gated out segment 'thin'" in out
    assert "dropped conduit 'core->thin'" in out
    assert "kept router 'gw'" in out


def test_twin_config_show_param_previews_a_different_threshold(capsys):
    rc = climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", _GATE_FIXT_DIR,
        "--param", "DEPLOY_MIN_OCCUPANCY=0.5",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "effective DEPLOY_MIN_OCCUPANCY=0.5" in out
    # ...and the DECLARED params line says an override is in force, so the
    # two numbers two lines apart never read as a contradiction.
    assert "(overridden: DEPLOY_MIN_OCCUPANCY=0.5)" in out
    # `worker` (8/11) is IN at 0.5, so it must not appear as gated out.
    assert "gated out node 'worker'" not in out
    assert "gated out node 'bridge-host'" in out


def test_twin_config_show_gate_section_is_uncapped(capsys):
    """The warnings channel caps at 50 lines and points here; here is where
    the full table has to actually be."""
    climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", _GATE_FIXT_DIR,
        "--param", "DEPLOY_MIN_OCCUPANCY=0.9",
    ])
    out = capsys.readouterr().out
    assert "and 1 more" not in out
    assert len([ln for ln in out.splitlines() if ln.strip().startswith("- gated out")]) == 6


def test_twin_config_show_says_so_when_there_is_no_observation_data(capsys):
    """A shipped, hand-authored config: the gate is structurally a no-op, and
    an operator previewing a threshold must be told that rather than shown
    nothing."""
    rc = climain.main(["twin-config", "show", "sme-flat"])
    assert rc == 0
    assert "deploy gate: not applicable" in capsys.readouterr().out


def test_twin_config_show_rejects_a_malformed_param(capsys):
    rc = climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", _GATE_FIXT_DIR,
        "--param", "NOT-A-PAIR",
    ])
    assert rc == 2
    assert "--param" in capsys.readouterr().err


def test_twin_config_show_json_carries_the_deploy_gate(capsys):
    """The gate's one machine-readable surface (S7) -- and what gives
    `DeployGateReport.to_dict()` a real consumer rather than leaving it a
    serializer nothing calls."""
    rc = climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", _GATE_FIXT_DIR,
        "--json", "--param", "DEPLOY_MIN_OCCUPANCY=0.5",
    ])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "gate-cast"
    # The observation blocks are in the dump (they are declared config)...
    worker = next(n for n in payload["nodes"] if n["name"] == "worker")
    assert worker["observation"]["windows_seen"] == 8
    # ...and `--param` is honored here too, not a silent no-op.
    gate = payload["deploy_gate"]
    assert gate["active"] is True
    assert gate["min_occupancy"] == 0.5
    assert {g["name"] for g in gate["gated_out"]} == {
        "bridge-host", "ghost-host", "thin", "ghost",
        "gw:thin", "gw:ghost", "core->thin", "core->ghost",
    }
    assert [g["name"] for g in gate["kept_routers"]] == ["gw"]


def test_twin_config_show_json_deploy_gate_is_inactive_for_a_blockless_config(capsys):
    """Present and INACTIVE, not absent and not `null`: a machine consumer
    can tell "this config carries no observation data" from "the gate could
    not be computed" (which IS `null`, see the REFUSED/UNAVAILABLE tests)."""
    climain.main(["twin-config", "show", "sme-flat", "--json"])
    import json

    gate = json.loads(capsys.readouterr().out)["deploy_gate"]
    assert gate is not None
    assert gate["active"] is False and gate["gated_out"] == []


def test_twin_config_show_says_REFUSED_when_the_gate_itself_refuses(tmp_path, capsys):
    """A gate refusal is not "this config is broken" -- the operator can turn
    the dial. The two must not read the same."""
    import shutil

    from ruamel.yaml import YAML

    shutil.copytree(Path(_GATE_FIXT_DIR) / "gate-cast", tmp_path / "gate-cast")
    f = tmp_path / "gate-cast" / "gate-cast.yaml"
    y = YAML()
    doc = y.load(f.read_text())
    doc["twin_config"]["data"][0]["on_role"] = "worker02"
    with f.open("w") as fh:
        y.dump(doc, fh)

    rc = climain.main([
        "twin-config", "show", "gate-cast", "--twin-configs-dir", str(tmp_path),
        "--param", "DEPLOY_MIN_OCCUPANCY=0.8",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deploy gate: REFUSED at these thresholds" in out
    assert "UNAVAILABLE" not in out


def test_twin_config_show_says_UNAVAILABLE_when_the_config_is_broken(tmp_path, capsys):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "broken.yaml").write_text(
        "twin_config:\n  id: broken\n  segments: []\n  nodes: []\n"
    )
    rc = climain.main([
        "twin-config", "show", "broken", "--twin-configs-dir", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deploy gate: UNAVAILABLE -- config does not expand" in out
    assert "REFUSED" not in out


def test_te_validate_with_config_prints_gate_warnings(capsys):
    """§4.5's "no CLI change" claim for the deploy path: the gate produces
    into the existing `warnings` channel `_resolve_scenario_or_config`
    already prints."""
    rc = climain.main([
        "te", "validate", "--config", "gate-cast",
        "--twin-configs-dir", _GATE_FIXT_DIR,
        "--param", "DEPLOY_MIN_OCCUPANCY=0.9",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[warn] deploy gate: effective DEPLOY_MIN_OCCUPANCY=0.9" in out
    assert "[warn] deploy gate: gated out node 'core-mid'" in out


def test_quiet_redirects_gate_warnings_to_stderr_instead_of_dropping_them(capsys):
    """`quiet` exists to keep stdout parseable, not to hide that a gate
    removed nodes (LES-083 + LES-089). Before this fix the warnings were
    dropped entirely on every quiet path."""
    import argparse

    args = argparse.Namespace(
        config="gate-cast", param=["DEPLOY_MIN_OCCUPANCY=0.9"],
        twin_configs_dir=_GATE_FIXT_DIR, scenario=None, images=None,
    )
    climain._resolve_scenario_or_config(args, "te deploy", quiet=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[warn] deploy gate: gated out node 'core-mid'" in captured.err
