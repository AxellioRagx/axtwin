"""Tests for the `te`/`o2`/`tap` CLI lifecycle grid
(`docs/WORK_ORDER_cli_lifecycle.md`).

Covers, per the work order §4:
  - every (tier, verb) parses and dispatches, with a positional spec for
    validate/plan/deploy
  - `status`/`info`/`destroy` default to the latest deploy OF THAT TIER --
    including the `te` vs `tap` disambiguation (a tap-plan playout deploy is
    ALSO stamped tier="te" in the state store; `te`'s default must skip it)
  - `--allow-destroy` enforced identically on all three tiers
  - bare top-level aliases (`deploy`/`validate`/`plan`/`status`/`destroy`)
    dispatch to `te`; deprecated `o2 up`/`o2 down`/`o2 tap apply`/`o2 tap
    destroy` shims print the notice AND actually run (translated onto the
    canonical `spec`/`deploy_id` dest via `dest=` on the legacy flag, not a
    second translation layer)
  - `info --json` / `status --json` emit the defined dataclass->dict shape;
    `info` on nothing-live shows config only, `info` on a live deploy adds
    access facts
  - `tap validate` is static-only when the state DB is absent/unreadable,
    full resolve when it's present
  - `o2 info` prints configured `O2_*` endpoints and an actionable
    "(not configured — set O2_SERVICES_VMID ...)" for the rest

No live cluster anywhere in this file: every test drives a real `StateStore`
(tmp SQLite file), either hand-seeded (the `tests.test_o2_taps` idiom) or via
a real `cmd_deploy` run against a FAKE adapter (`tests.test_planner_executor.
_FakeAdapter`), matching `tests/test_cli.py`'s existing idiom.
"""
from __future__ import annotations

import argparse
import json as jsonlib
from pathlib import Path

import pytest

from axrange.cli import main as climain
from axrange.o2control.taps import TapBindingSpec, TapPlanSpec, apply_tap_plan
from axrange.planner import StateStore
from tests.test_o2_taps import (
    DEFAULT_SENSORS, O2_DEPLOY, TE_DEPLOY, _FakeMirAdapter, _seed_o2_surface,
    _seed_te,
)

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Grid coverage: every (tier, verb) parses and sets tier/verb, wired in
# `_HANDLERS` (work order §4: "every (tier, verb) parses and dispatches").
# ---------------------------------------------------------------------------
GRID = [
    ("te", "validate"), ("te", "plan"), ("te", "deploy"),
    ("te", "status"), ("te", "info"), ("te", "destroy"),
    ("o2", "validate"), ("o2", "plan"), ("o2", "deploy"),
    ("o2", "status"), ("o2", "info"), ("o2", "destroy"),
    ("tap", "validate"), ("tap", "plan"), ("tap", "deploy"),
    ("tap", "status"), ("tap", "info"), ("tap", "destroy"),
]


@pytest.mark.parametrize("tier,verb", GRID)
def test_grid_parses_and_sets_tier_verb(tier, verb):
    parser = climain._build_parser()
    argv = [tier, verb]
    if verb in ("validate", "plan", "deploy") and not (tier == "te" and verb == "validate"):
        # te validate's `scenario` is the only spec-optional slot in the
        # grid (--explain/--rules don't need one); everything else needs a
        # token to satisfy a required/positional spec arg.
        argv.append("dummy-spec.yaml")
    args = parser.parse_args(argv)
    assert args.tier == tier
    assert args.verb == verb
    assert (tier, verb) in climain._HANDLERS


@pytest.mark.parametrize("tier,verb", GRID)
def test_grid_main_dispatches_to_the_correct_handler(tier, verb, monkeypatch):
    """Stronger than `test_grid_parses_and_sets_tier_verb` above: that test
    only proves `(tier, verb)` is a KEY in `_HANDLERS`, not that `main()`'s
    parse -> tier/verb -> `_HANDLERS[(tier, verb)]` -> `handler(args)`
    pipeline actually reaches THAT handler and no other. Patch exactly one
    dict entry and drive the real `main()` end to end; a wrong-handler
    mapping (or a dispatch-order bug) would either call a different
    (unpatched) handler -- leaving `calls` empty -- or blow up trying to hit
    a real DB/adapter, not silently pass."""
    calls: list[tuple[str, str]] = []

    def fake_handler(args):
        calls.append((args.tier, args.verb))
        return 0

    monkeypatch.setitem(climain._HANDLERS, (tier, verb), fake_handler)

    argv = [tier, verb]
    if verb in ("validate", "plan", "deploy") and not (tier == "te" and verb == "validate"):
        argv.append("dummy-spec.yaml")

    rc = climain.main(argv)
    assert rc == 0
    assert calls == [(tier, verb)]


def test_te_deploy_parses_update_flags():
    """`docs/WORK_ORDER_twin_incremental_update.md`: `--update`/
    `--allow-destroy`/`--yes` parse on the canonical `te deploy` verb (and
    the bare `deploy` deprecation shim, since it reuses the SAME parser
    builder), default to `False`, and `--update --fresh` together parse
    fine (the refusal is a runtime check, not an argparse-level one).
    `--reapply` (`docs/WORK_ORDER_responder_op_row_drift.md` option A) joins
    the same set -- every other test in this module exercises `--reapply`
    via a hand-built `argparse.Namespace` (`_deploy_args`), never the real
    parser, so THIS is the one place a wiring typo in the actual
    `p.add_argument("--reapply", ...)` call (wrong flag spelling, wrong
    `dest`) would be caught."""
    parser = climain._build_parser()

    args = parser.parse_args(
        ["te", "deploy", "scenario.yaml", "--update", "--allow-destroy",
         "--reapply", "--yes"]
    )
    assert args.update is True
    assert args.allow_destroy is True
    assert args.reapply is True
    assert args.yes is True

    defaults = parser.parse_args(["te", "deploy", "scenario.yaml"])
    assert defaults.update is False
    assert defaults.allow_destroy is False
    assert defaults.reapply is False
    assert defaults.yes is False

    both = parser.parse_args(["te", "deploy", "scenario.yaml", "--update", "--fresh"])
    assert both.update is True and both.fresh is True

    shim = parser.parse_args(["deploy", "scenario.yaml", "--update"])
    assert shim.update is True


def test_canonical_o2_deploy_rejects_legacy_surface_flag():
    # `--surface` only exists on the DEPRECATED `o2 up` shim (`dest="spec"`
    # trick) -- the canonical `o2 deploy` takes the spec positionally only.
    parser = climain._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["o2", "deploy", "--surface", "surf.yaml"])


def test_canonical_tap_deploy_rejects_legacy_plan_flag():
    # `--plan` only exists on the DEPRECATED `o2 tap apply` shim -- the
    # canonical `tap deploy` takes the spec positionally only.
    parser = climain._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["tap", "deploy", "--plan", "plan.yaml"])


def test_te_validate_plan_deploy_take_spec_positionally():
    parser = climain._build_parser()
    for verb in ("validate", "plan", "deploy"):
        args = parser.parse_args(["te", verb, "scenario.yaml"])
        assert args.scenario == "scenario.yaml"


def test_o2_validate_plan_deploy_take_spec_positionally_and_optionally():
    parser = climain._build_parser()
    for verb in ("validate", "plan", "deploy"):
        args = parser.parse_args(["o2", verb, "surface.yaml"])
        assert args.spec == "surface.yaml"
        args = parser.parse_args(["o2", verb])
        assert args.spec is None


def test_tap_validate_plan_deploy_take_spec_positionally_and_required(capsys):
    """The positional is still how a tap-plan is named, and still required --
    but since `--twin` (docs/WORK_ORDER_corp_twin.md S0) can supply it instead,
    the requirement is enforced at dispatch (`_resolve_twin_tier_spec`, exit 2
    naming both ways), not by argparse."""
    parser = climain._build_parser()
    for verb in ("validate", "plan", "deploy"):
        args = parser.parse_args(["tap", verb, "tap-plan.yaml"])
        assert args.spec == "tap-plan.yaml"
        assert climain.main(["tap", verb]) == 2
        assert "a tap file path is required (or --twin)" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Bare deprecated shims -> te (work order §3.5)
# ---------------------------------------------------------------------------
def test_bare_deploy_shim_prints_notice_and_dispatches(capsys):
    # `_load_planning_inputs` raises SystemExit(2) on a missing scenario
    # file (unchanged pre-existing behavior of `cmd_deploy` -- `main()` is
    # meant to be run as `sys.exit(main())`; a raw function call surfaces
    # that as an exception, which is what proves the shim actually reached
    # the real `te deploy` handler body).
    with pytest.raises(SystemExit) as exc:
        climain.main(["deploy", "/nonexistent/scenario.yaml"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange te deploy'" in captured.err


def test_bare_validate_shim_prints_notice_and_dispatches(capsys):
    rc = climain.main(["validate", "--rules"])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange te validate'" in captured.err
    assert rc == 0


def test_bare_plan_shim_prints_notice_and_dispatches(capsys):
    with pytest.raises(SystemExit) as exc:
        climain.main(["plan", "/nonexistent/scenario.yaml"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange te plan'" in captured.err


def test_bare_status_shim_prints_notice_and_dispatches(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    rc = climain.main(["status", "--db", db])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange te status'" in captured.err
    assert rc == 0
    assert "No deploys recorded" in captured.out


def test_bare_destroy_shim_requires_allow_destroy(tmp_path, capsys):
    from tests.test_teardown import seed_deploy

    db = str(tmp_path / "state.db")
    info = seed_deploy(db, deploy_id="sc-bare-destroy")
    rc = climain.main(["destroy", info["deploy_id"], "--db", db])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "--allow-destroy" in captured.err
    assert rc == 2


# ---------------------------------------------------------------------------
# Deprecated `o2 up`/`o2 down`/`o2 tap apply`/`o2 tap destroy` shims: print
# the notice AND actually run, with the legacy flag correctly landing on the
# SAME dest the canonical positional/flag uses (no second translation layer
# -- `dest="spec"`/`dest="deploy_id"` on the legacy flag itself).
# ---------------------------------------------------------------------------
def test_o2_up_shim_translates_surface_flag_and_dispatches(tmp_path, capsys, monkeypatch):
    import axrange.o2control as o2control_pkg
    from axrange.o2control.control import SurfaceStatus

    calls = {}

    def fake_stand_up_surface(*, surface_path, db_path, images_root, adapter_config, target_node):
        calls["surface_path"] = surface_path
        return SurfaceStatus(deploy_id="o2-x", status="completed", sensors=[])

    monkeypatch.setattr(o2control_pkg, "stand_up_surface", fake_stand_up_surface)

    db = str(tmp_path / "state.db")
    rc = climain.main([
        "o2", "up", "--surface", "surf.yaml", "--db", db, "--images", str(tmp_path),
    ])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange o2 deploy'" in captured.err
    assert rc == 0
    assert calls["surface_path"] == "surf.yaml"


def test_o2_down_shim_translates_deploy_id_flag_and_dispatches(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "state.db")
    rc = climain.main(["o2", "down", "--deploy-id", "o2-nope", "--db", db])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange o2 destroy'" in captured.err
    # No --allow-destroy -> refused before ever calling tear_down_surface;
    # this still proves the shim reached the canonical `cmd_o2_destroy` body
    # (it reports on the EXACT deploy-id the legacy flag supplied).
    assert "o2-nope" in captured.err or "(none)" in captured.err
    assert rc == 2


def test_o2_tap_apply_shim_translates_plan_flag_and_dispatches(tmp_path, capsys, monkeypatch):
    # `cmd_tap_deploy` imports `apply_tap_plan` from the SUBMODULE
    # (`axrange.o2control.taps`), not the package re-export -- matches the
    # pre-existing style of the tap handlers (unlike the o2-surface
    # handlers, which import from the `axrange.o2control` package).
    import axrange.o2control.taps as taps_mod
    from axrange.o2control.taps import TapPlanStatus

    calls = {}

    def fake_apply_tap_plan(*, spec_path, db_path, adapter_config, target_node,
                             target=None):
        calls["spec_path"] = spec_path
        return TapPlanStatus(deploy_id="tap-x", target=TE_DEPLOY, status="completed")

    monkeypatch.setattr(taps_mod, "apply_tap_plan", fake_apply_tap_plan)

    db = str(tmp_path / "state.db")
    rc = climain.main(["o2", "tap", "apply", "--plan", "plan.yaml", "--db", db])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange tap deploy'" in captured.err
    assert rc == 0
    assert calls["spec_path"] == "plan.yaml"


def test_o2_tap_destroy_shim_dispatches_without_allow_destroy(capsys):
    rc = climain.main(["o2", "tap", "destroy", "--target", TE_DEPLOY])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "'axrange tap destroy'" in captured.err
    assert "--allow-destroy" in captured.err
    assert rc == 2


# ---------------------------------------------------------------------------
# `status`/`info`/`destroy` default to latest-of-tier -- the `te` vs `tap`
# disambiguation is the one honest asymmetry (a tap-plan playout deploy is
# ALSO tier="te" in the state store).
# ---------------------------------------------------------------------------
def _seed_te_and_tap(tmp_path, *, apply: bool = True) -> str:
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    if apply:
        apply_tap_plan(
            spec=TapPlanSpec(
                target=TE_DEPLOY,
                bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
            ),
            db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
            target_node="pxtest1",
        )
    return db


def test_latest_te_deploy_excludes_tap_plan_playout(tmp_path):
    db = _seed_te_and_tap(tmp_path)
    store = StateStore(db)
    try:
        row = climain._latest_te_deploy(store)
    finally:
        store.close()
    assert row is not None
    assert row.deploy_id == TE_DEPLOY


def test_te_status_default_picks_te_not_tap_plan(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path)
    args = argparse.Namespace(deploy_id=None, db=db, json=True)
    rc = climain.cmd_status(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] == TE_DEPLOY
    assert payload["scenario_id"] == "te-scn"


def test_tap_status_default_picks_tap_plan_not_te(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path)
    args = argparse.Namespace(deploy_id=None, target=None, db=db, json=True)
    rc = climain.cmd_tap_status(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["target"] == TE_DEPLOY
    assert payload["status"] == "completed"
    assert payload["deploy_id"] != TE_DEPLOY


def test_tap_status_resolves_twin_id_positional_to_its_tap_deploy(tmp_path, capsys):
    """`tap status <twin-id>` (the te deploy id passed as the positional [ID],
    not --target) must resolve the twin's applied tap deploy -- the SAME DWIM
    `tap info` does -- not read tap bindings off the te deploy and report
    `bindings: (none)`."""
    db = _seed_te_and_tap(tmp_path, apply=True)
    args = argparse.Namespace(deploy_id=TE_DEPLOY, target=None, db=db, json=True)
    rc = climain.cmd_tap_status(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["target"] == TE_DEPLOY
    assert payload["status"] == "completed"
    assert payload["deploy_id"] != TE_DEPLOY   # resolved to tap-<twin>-<hash>
    assert payload["bindings"]                 # not empty (the bug printed none)


def test_tap_destroy_default_picks_latest_tap_plan(tmp_path, capsys, monkeypatch):
    from axrange.o2control import taps as taps_mod

    db = _seed_te_and_tap(tmp_path)
    store = StateStore(db)
    try:
        expected = taps_mod.latest_tap_plan_deploy(store)
    finally:
        store.close()
    assert expected is not None

    args = argparse.Namespace(
        deploy_id=None, target=None, db=db, adapter_config=None, node=None,
        allow_destroy=False,
    )
    rc = climain.cmd_tap_destroy(args)
    captured = capsys.readouterr()
    assert rc == 2
    assert "--allow-destroy" in captured.err


# ---------------------------------------------------------------------------
# `--allow-destroy` enforced identically on all three tiers.
# ---------------------------------------------------------------------------
def test_te_destroy_requires_allow_destroy(tmp_path, capsys):
    from tests.test_teardown import seed_deploy

    db = str(tmp_path / "state.db")
    info = seed_deploy(db, deploy_id="sc-guard-te")
    args = argparse.Namespace(
        deploy_id=info["deploy_id"], db=db, adapter_config=None, node=None,
        allow_destroy=False,
    )
    rc = climain.cmd_destroy(args)
    assert rc == 2
    assert "--allow-destroy" in capsys.readouterr().err


def test_o2_destroy_requires_allow_destroy(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    args = argparse.Namespace(
        deploy_id=None, db=db, adapter_config=None, node=None, allow_destroy=False,
    )
    rc = climain.cmd_o2_destroy(args)
    assert rc == 2
    assert "--allow-destroy" in capsys.readouterr().err


def test_tap_destroy_requires_allow_destroy(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path)
    args = argparse.Namespace(
        deploy_id=None, target=TE_DEPLOY, db=db, adapter_config=None, node=None,
        allow_destroy=False,
    )
    rc = climain.cmd_tap_destroy(args)
    assert rc == 2
    assert "--allow-destroy" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `te info` -- two-tier (config-only on a dry spec; config + access facts on
# a live deploy) + `--json`.
# ---------------------------------------------------------------------------
def test_te_info_dry_spec_shows_config_only(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(
        id_or_spec=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, json=True,
    )
    rc = climain.cmd_te_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] is None
    assert payload["status"] is None
    host_ids = {h["host_id"] for h in payload["hosts"]}
    assert host_ids == {"h-a", "h-b"}
    for h in payload["hosts"]:
        assert h["vmid"] is None
        assert h["ips"].get("corp") in ("10.10.0.10", "10.10.0.11")
        # docs/WORK_ORDER_access_facts.md Part D: access facts appear even on
        # a not-yet-deployed spec preview -- no adapter.conf (nonexistent
        # path) -> the built-in lab default password + the static IR IP,
        # never a live-resolve attempt (no vmid to resolve against anyway).
        assert h["access_password"] == "Axr4nge-Lab!"
        assert h["access_user"] == h["ssh_user"] or h["access_user"] == "axrange"
        assert h["access_ip"] == h["ips"].get("corp")
        assert h["access_hint"] == f"ssh {h['access_user']}@{h['access_ip']}"


def test_te_info_dry_spec_default_password_from_adapter_conf(tmp_path, capsys):
    conf = tmp_path / "adapter.conf"
    conf.write_text("DEFAULT_PASSWORD=CustomLabPw!\n")
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(
        id_or_spec=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config=str(conf), node="pxtest1",
        db=db, json=True,
    )
    rc = climain.cmd_te_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    for h in payload["hosts"]:
        assert h["access_password"] == "CustomLabPw!"


def test_te_info_no_secrets_masks_password_in_text_mode(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(
        id_or_spec=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, json=False, no_secrets=True,
    )
    rc = climain.cmd_te_info(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "********" in out
    assert "Axr4nge-Lab!" not in out


def test_te_info_json_always_shows_real_password_even_with_no_secrets(tmp_path, capsys):
    """--json carries the real password regardless of --no-secrets (Part D:
    "it's operator-local")."""
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(
        id_or_spec=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, json=True, no_secrets=True,
    )
    rc = climain.cmd_te_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    for h in payload["hosts"]:
        assert h["access_password"] == "Axr4nge-Lab!"


def test_te_info_text_render_vyos_router_omits_password_line(tmp_path, capsys):
    """SHOULD-FIX 2 (code review, 2026-07-25) end-to-end through `cmd_te_info`'s
    text renderer: a VyOS router's access line shows the key-auth hint with
    NO `(password=...)` suffix at all -- not even a misleading
    `(password=None)`."""
    from axrange.planner import StateStore
    from tests.test_o2_taps import _op_row

    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="vyos-te-1", scenario_id="vyos-scn", scenario_version=1,
            scenario_schema_version=1, plan_id="vyos-te-1-plan", ir_hash="h",
            placement={"gw": "pxtest1"}, tier="te",
        )
        rows = [
            _op_row("vyos-te-1", "op-clone-gw", "clone-vm", "OP-HOST-010",
                     {"host": "gw"}, outputs={"vmid": 700}),
            _op_row("vyos-te-1", "op-wan-gw", "attach-vm-to-vnet", "OP-HOST-040",
                     {"host": "gw", "net": "__wan__"}, inputs={"net_id": "__wan__"}),
            _op_row("vyos-te-1", "op-router-gw", "configure-router", "OP-HOST-090",
                     {"host": "gw"}, inputs={"router_os": "vyos"},
                     outputs={"wan_ip": "203.0.113.20"}),
        ]
        store.insert_ops("vyos-te-1", rows)
    finally:
        store.close()

    args = argparse.Namespace(
        id_or_spec="vyos-te-1", images=None, adapter_config="/nonexistent.conf",
        node="pxtest1", db=db, json=False,
    )
    rc = climain.cmd_te_info(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ssh vyos@203.0.113.20  (key auth)" in out
    assert "password=" not in out


def test_te_info_live_deploy_adds_access_facts(tmp_path, monkeypatch, capsys):
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db = str(tmp_path / "state.db")
    deploy_args = argparse.Namespace(
        command="deploy", scenario=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, dry_run=False, fresh=False,
    )
    rc = climain.cmd_deploy(deploy_args)
    assert rc == 0
    capsys.readouterr()  # drain the deploy's own output

    info_args = argparse.Namespace(
        id_or_spec=None, images=str(FIXT_ROOT), adapter_config="/nonexistent.conf",
        node="pxtest1", db=db, json=True,
    )
    rc = climain.cmd_te_info(info_args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] is not None
    assert payload["status"] == "completed"
    assert payload["ops_total"] > 0
    assert payload["ops_done"] == payload["ops_total"]
    host_ids = {h["host_id"] for h in payload["hosts"]}
    assert host_ids == {"h-a", "h-b"}
    for h in payload["hosts"]:
        assert h["vmid"] is not None
        # docs/WORK_ORDER_access_facts.md Part D: a live deploy's hosts carry
        # full access facts too -- IP comes from the persisted static IR IP
        # (no live-resolve needed since it's already known), password from
        # the built-in lab default (no adapter.conf/DEFAULT_PASSWORD here).
        assert h["access_ip"] == h["ips"].get("corp")
        assert h["access_password"] == "Axr4nge-Lab!"
        assert h["access_hint"].startswith("ssh ")


class _FakeOpRow:
    """Minimal OpRow-shaped stand-in for `_te_info_from_ops` unit tests below
    -- deliberately has NO `.scope` attribute so `op_fields` (opview.py)
    takes the OpRow branch (`scope_json`/`inputs_resolved_json`/
    `outputs_json`, which `_json_obj` accepts as plain dicts, not just JSON
    strings)."""

    def __init__(self, op_name, scope, inputs, outputs=None, status=None):
        self.op_name = op_name
        self.scope_json = scope
        self.inputs_resolved_json = inputs
        self.outputs_json = outputs or {}
        self.status = status


def test_fill_access_facts_windows_host_gets_rdp_hint():
    """docs/WORK_ORDER_access_facts.md Part D: a Windows host's access facts
    are Administrator + an `rdp <ip> (user=Administrator)` hint, not the
    Linux ssh hint."""
    from axrange.adapter_proxmox.config import AdapterConfig

    ops = [
        _FakeOpRow("clone-vm", {"host": "dc1"}, {}, {"vmid": 500}),
        _FakeOpRow("configure-cloud-init", {"host": "dc1"},
                    {"os_family": "windows", "ci_user": "Administrator"}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "dc1", "net": "corp"},
                    {"net_id": "corp", "ip": "10.10.0.50"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(default_password="Wpw!")
    climain._fill_access_facts(info, cfg, live_resolve=False)
    h = info.hosts[0]
    assert h.os_family == "windows"
    assert h.access_user == "Administrator"
    assert h.access_ip == "10.10.0.50"
    assert h.access_password == "Wpw!"
    assert h.access_hint == "rdp 10.10.0.50  (user=Administrator)"


def test_fill_access_facts_live_resolves_ip_when_no_static_ip(monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part D: a host with no persisted
    static IP (e.g. a DHCP-configured NIC) falls back to a best-effort live
    Proxmox lookup by vmid."""
    import axrange.adapter_proxmox.resolve as resolve_mod
    from axrange.adapter_proxmox.config import AdapterConfig

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.9.9.9")
    ops = [
        _FakeOpRow("clone-vm", {"host": "h1"}, {}, {"vmid": 501}),
        _FakeOpRow("configure-cloud-init", {"host": "h1"},
                    {"os_family": "linux", "ci_user": "debian"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(proxmox_api_url="https://test:8006/api2/json", default_node="px1")
    climain._fill_access_facts(info, cfg)
    h = info.hosts[0]
    assert h.access_ip == "10.9.9.9"
    assert h.access_hint == "ssh debian@10.9.9.9"


def test_fill_access_facts_degrades_to_unresolved_when_live_lookup_fails(monkeypatch):
    """Guardrail: an unreachable cluster / no-agent VM degrades `access_ip`
    to the literal `"(unresolved)"`, never raises."""
    import axrange.adapter_proxmox.resolve as resolve_mod
    from axrange.adapter_proxmox.config import AdapterConfig

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: None)
    ops = [
        _FakeOpRow("clone-vm", {"host": "h1"}, {}, {"vmid": 501}),
        _FakeOpRow("configure-cloud-init", {"host": "h1"},
                    {"os_family": "linux", "ci_user": "debian"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(proxmox_api_url="https://test:8006/api2/json", default_node="px1")
    climain._fill_access_facts(info, cfg)
    h = info.hosts[0]
    assert h.access_ip == "(unresolved)"
    assert h.access_hint == "ssh debian@(unresolved)"


def test_fill_access_facts_router_wan_ip_wins_over_lan_ip():
    """docs/WORK_ORDER_access_facts.md Part D priority #1: a router's
    discovered `wan_ip` (configure-router's persisted output) beats any
    persisted LAN-segment static IP."""
    from axrange.adapter_proxmox.config import AdapterConfig

    ops = [
        _FakeOpRow("clone-vm", {"host": "gw"}, {}, {"vmid": 502}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "gw", "net": "__wan__"}, {"net_id": "__wan__"}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "gw", "net": "corp"},
                    {"net_id": "corp", "ip": "10.10.0.1"}),
        _FakeOpRow("configure-router", {"host": "gw"}, {}, {"wan_ip": "203.0.113.5"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig()
    climain._fill_access_facts(info, cfg, live_resolve=False)
    h = info.hosts[0]
    assert h.kind == "router"
    assert h.access_ip == "203.0.113.5"


def test_fill_access_facts_vyos_router_gets_key_auth_hint_no_password():
    """SHOULD-FIX 2 (code review, 2026-07-25): a VyOS router has NO
    `configure-cloud-init` op (pass1_emit.py skips it entirely), so it must
    NOT show the default-password hint it would fall through to otherwise --
    `configure-router`'s own `router_os: "vyos"` input is the cheap signal
    for a proper `ssh vyos@<ip> (key auth)` hint with no password shown."""
    from axrange.adapter_proxmox.config import AdapterConfig

    ops = [
        _FakeOpRow("clone-vm", {"host": "gw"}, {}, {"vmid": 503}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "gw", "net": "__wan__"}, {"net_id": "__wan__"}),
        _FakeOpRow("configure-router", {"host": "gw"}, {"router_os": "vyos"},
                    {"wan_ip": "203.0.113.9"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(default_password="ShouldNotAppearPw!")
    climain._fill_access_facts(info, cfg, live_resolve=False)
    h = info.hosts[0]
    assert h.kind == "router"
    assert h.router_os == "vyos"
    assert h.access_password is None
    assert h.access_user == "vyos"
    assert h.access_hint == "ssh vyos@203.0.113.9  (key auth)"


def test_fill_access_facts_router_no_cloud_init_non_vyos_gets_generic_key_auth_hint():
    """The same no-default-password suppression applies even when the
    router isn't identifiably VyOS (no `router_os` signal at all) -- a
    generic key-auth hint, still no password."""
    from axrange.adapter_proxmox.config import AdapterConfig

    ops = [
        _FakeOpRow("clone-vm", {"host": "gw"}, {}, {"vmid": 504}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "gw", "net": "__wan__"}, {"net_id": "__wan__"}),
        _FakeOpRow("configure-router", {"host": "gw"}, {}, {"wan_ip": "203.0.113.10"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(default_password="ShouldNotAppearPw!")
    climain._fill_access_facts(info, cfg, live_resolve=False)
    h = info.hosts[0]
    assert h.access_password is None
    assert h.access_hint == "(203.0.113.10)  (key auth — no default password)"


def test_fill_access_facts_debian_router_with_cloud_init_still_shows_password():
    """Regression guard: a Debian/nftables router DOES get `configure-cloud-init`
    (`ssh_user` gets set) so it must NOT be swept into the key-auth-only
    branch -- it keeps the normal ssh + default-password hint."""
    from axrange.adapter_proxmox.config import AdapterConfig

    ops = [
        _FakeOpRow("clone-vm", {"host": "gw"}, {}, {"vmid": 505}),
        _FakeOpRow("configure-cloud-init", {"host": "gw"},
                    {"os_family": "linux", "ci_user": "debian"}),
        _FakeOpRow("attach-vm-to-vnet", {"host": "gw", "net": "__wan__"}, {"net_id": "__wan__"}),
        _FakeOpRow("configure-router", {"host": "gw"}, {}, {"wan_ip": "203.0.113.11"}),
    ]
    info = climain._te_info_from_ops(ops, scenario_id="s", plan_id=None)
    cfg = AdapterConfig(default_password="RouterPw!")
    climain._fill_access_facts(info, cfg, live_resolve=False)
    h = info.hosts[0]
    assert h.access_password == "RouterPw!"
    assert h.access_hint == "ssh debian@203.0.113.11"


def test_tap_info_no_secrets_masks_external_sensor_password(tmp_path, capsys, monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part D: `tap info --no-secrets` masks
    an external sensor's access password in text mode too (not just `o2
    info`/`te info`)."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_name", lambda cfg, vmid, **kw: "XPress-SW")
    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.5.0.20")
    db = _seed_te_and_tap(tmp_path, apply=False)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [XPress-SW]\n"
    )
    args = argparse.Namespace(
        id_or_spec=str(plan_path), adapter_config=str(conf), db=db, json=False,
        no_secrets=True,
    )
    rc = climain.cmd_tap_info(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "********" in out
    assert "Axr4nge-Lab!" not in out


# ---------------------------------------------------------------------------
# `te inventory` -- the twin-model keystone's CLI read path
# (docs/WORK_ORDER_tier_split.md).
# ---------------------------------------------------------------------------
def test_te_inventory_dispatches_and_shows_hosts(tmp_path, monkeypatch, capsys):
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db = str(tmp_path / "state.db")
    deploy_args = argparse.Namespace(
        command="deploy", scenario=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, dry_run=False, fresh=False,
    )
    rc = climain.cmd_deploy(deploy_args)
    assert rc == 0
    capsys.readouterr()  # drain the deploy's own output

    # Dispatch through the real (tier, verb) grid, not the handler directly --
    # proves ("te", "inventory") is actually wired in `_HANDLERS`.
    parser = climain._build_parser()
    args = parser.parse_args(["te", "inventory", "--db", db, "--json"])
    assert args.tier == "te" and args.verb == "inventory"
    rc = climain._HANDLERS[("te", "inventory")](args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)

    assert payload["deploy_id"] is not None
    host_ids = {h["host_id"] for h in payload["hosts"]}
    assert host_ids == {"h-a", "h-b"}
    for h in payload["hosts"]:
        assert h["vmid"] is not None
        # `vm_name`, not `node` (review SHOULD-FIX #2, second round): the
        # `node` back-compat alias was dropped -- `inventory.py` is
        # brand-new with zero shipped consumers, and the alias had already
        # produced a shape mismatch against the API's `HostInventoryDTO`
        # (which never carried it). Authorized test-file edit, per the
        # coordinator's review note.
        assert h["vm_name"] is not None
    # `tap_points` is REMOVED from the published inventory entirely (`docs/
    # WORK_ORDER_tap_eligibility.md` D5, Authority: user, 2026-07-28) -- a
    # twin publishes only `hosts[].nets[]`.
    assert "tap_points" not in payload
    net_ids_by_host = {h["host_id"]: {n["net_id"] for n in h["nets"]} for h in payload["hosts"]}
    assert net_ids_by_host == {"h-a": {"corp"}, "h-b": {"corp"}}


def test_te_inventory_no_deploys_recorded(tmp_path, capsys):
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(deploy_id=None, db=db, json=False)
    rc = climain.cmd_te_inventory(args)
    assert rc == 0
    assert "No deploys recorded" in capsys.readouterr().out


def test_te_inventory_unknown_deploy_id_fails_loud(tmp_path, monkeypatch, capsys):
    """An explicit, bad deploy id (not "no deploys at all") must fail loud --
    non-zero exit + a stderr message -- rather than silently falling back to
    the latest deploy or printing an empty inventory."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db = str(tmp_path / "state.db")
    deploy_args = argparse.Namespace(
        command="deploy", scenario=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, dry_run=False, fresh=False,
    )
    rc = climain.cmd_deploy(deploy_args)
    assert rc == 0
    capsys.readouterr()

    args = argparse.Namespace(deploy_id="does-not-exist-at-all", db=db, json=False)
    rc = climain.cmd_te_inventory(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "does-not-exist-at-all" in err


def test_te_inventory_text_output_happy_path(tmp_path, monkeypatch, capsys):
    """The human-readable (non-`--json`) rendering path -- every prior test
    of this verb only exercised `--json`."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db = str(tmp_path / "state.db")
    deploy_args = argparse.Namespace(
        command="deploy", scenario=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, dry_run=False, fresh=False,
    )
    rc = climain.cmd_deploy(deploy_args)
    assert rc == 0
    capsys.readouterr()

    args = argparse.Namespace(deploy_id=None, db=db, json=False)
    rc = climain.cmd_te_inventory(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "TE inventory" in out
    assert "h-a" in out and "h-b" in out
    # No `tap_points:` section any more (`docs/WORK_ORDER_tap_eligibility.md`
    # D5, Authority: user, 2026-07-28) -- the twin publishes only its hosts'
    # attached nets.
    assert "tap_points:" not in out


def test_te_inventory_text_output_marks_router_wan_leg_external(
    tmp_path, monkeypatch, capsys,
):
    """`docs/WORK_ORDER_tap_scopes.md` M2/M6 -- the `te inventory` text
    renderer marks a router's synthetic WAN uplink `(external)`, and does
    NOT mark an ordinary twin segment leg that way."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    ir_text = """
scenario_schema_version: 1
scenario: { id: wan-inventory-text, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.21.0.0/24, vlan: 21 }
    nodes:
      - { id: h-a, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - id: r-edge
        kind: router
        nets: [{ net: corp, ip: 10.21.0.1 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {cpu:1,ram:1Gi,disk:10Gi}
  apps: []
  activity: []
bindings: []
"""
    ir_path = tmp_path / "wan-inventory-text.yaml"
    ir_path.write_text(ir_text)

    db = str(tmp_path / "state.db")
    deploy_args = argparse.Namespace(
        command="deploy", scenario=str(ir_path),
        images=str(FIXT_ROOT), adapter_config="/nonexistent.conf", node="pxtest1",
        db=db, dry_run=False, fresh=False,
    )
    rc = climain.cmd_deploy(deploy_args)
    assert rc == 0
    capsys.readouterr()

    args = argparse.Namespace(deploy_id=None, db=db, json=False)
    rc = climain.cmd_te_inventory(args)
    assert rc == 0
    out = capsys.readouterr().out
    # Only the per-host `nets` rendering lines (`"      {net_id}{marker}: ip=..."`)
    # -- there is no more tap-point catalog section below to worry about
    # confusing this with (`docs/WORK_ORDER_tap_eligibility.md` D5).
    net_lines = [l for l in out.splitlines() if l.startswith("      ") and ": ip=" in l]
    wan_lines = [l for l in net_lines if l.strip().startswith("__wan__")]
    corp_lines = [l for l in net_lines if l.strip().startswith("corp")]
    assert wan_lines and all("(external)" in l for l in wan_lines)
    assert corp_lines and all("(external)" not in l for l in corp_lines)


# ---------------------------------------------------------------------------
# `o2 info` -- configured/unconfigured `O2_*` endpoints (work order §3.4).
# ---------------------------------------------------------------------------
def test_o2_info_prints_configured_and_unconfigured_endpoints(tmp_path, capsys):
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        "O2_GRAFANA_URL=http://10.0.0.90:3000\n"
        "O2_LOKI_URL=http://10.0.0.90:3100\n"
    )
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(id_or_spec=None, db=db, adapter_config=str(conf), json=False)
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "10.0.0.90:3000" in out
    assert "not configured — set O2_SERVICES_VMID" in out


def test_o2_info_json_includes_endpoints_dict(tmp_path, capsys):
    conf = tmp_path / "adapter.conf"
    conf.write_text("O2_CALDERA_URL=http://10.0.0.90:8888\n")
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(id_or_spec=None, db=db, adapter_config=str(conf), json=True)
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    # docs/WORK_ORDER_access_facts.md Part B: `resolve_o2_endpoints()` always
    # returns all five keys now (an explicit O2_*_URL wins verbatim; the rest
    # -- no services VM declared here -- fall back to an actionable
    # "(not configured — set O2_SERVICES_VMID or a per-service O2_<SVC>_URL
    # in adapter.conf)", not just the previously-set subset).
    assert payload["endpoints"]["caldera"] == "http://10.0.0.90:8888"
    for name in ("grafana", "loki", "prometheus", "wazuh"):
        assert payload["endpoints"][name] == (
            "(not configured — set O2_SERVICES_VMID or a per-service "
            "O2_<SVC>_URL in adapter.conf)"
        )
    assert payload["external_sensors"] == []


def test_o2_info_refuses_a_te_tier_deploy_id(tmp_path, capsys):
    """`o2 info <te-id>` must exit non-zero with a clear tier-mismatch
    message instead of misleadingly printing `O2 surface <te-id> --
    completed / sensors: (none)` -- the exact confusion a real operator hit
    (docs/CHANGELOG.md, this fix's entry)."""
    conf = tmp_path / "adapter.conf"
    conf.write_text("")
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-dep-1", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-1", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    args = argparse.Namespace(
        id_or_spec="te-dep-1", db=db, adapter_config=str(conf), json=False,
    )
    rc = climain.cmd_o2_info(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "not an O2 surface" in err


def test_o2_info_resolves_endpoints_from_services_vmid(tmp_path, capsys, monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part B: a declared O2_SERVICES_VMID
    (no explicit O2_*_URL) resolves via a live IP lookup."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.0.0.90")
    conf = tmp_path / "adapter.conf"
    conf.write_text("O2_SERVICES_VMID=102\n")
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(id_or_spec=None, db=db, adapter_config=str(conf), json=True)
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["endpoints"]["grafana"] == "http://10.0.0.90:3000"
    assert payload["endpoints"]["wazuh"] == "https://10.0.0.90:443"


def test_o2_info_shows_declared_external_sensors(tmp_path, capsys, monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part C: each EXTERNAL_SENSORS entry
    shows resolved name/mgmt-ip (best-effort) alongside declared fields +
    an access line."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_name", lambda cfg, vmid, **kw: "XPress-SW")
    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.5.0.20")
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(id_or_spec=None, db=db, adapter_config=str(conf), json=True)
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    ext = payload["external_sensors"]
    assert len(ext) == 1
    assert ext[0]["id"] == "XPress-SW"
    assert ext[0]["vmid"] == 250
    assert ext[0]["transport_ip"] == "10.255.250.20"
    assert ext[0]["resolved_name"] == "XPress-SW"
    assert ext[0]["resolved_ip"] == "10.5.0.20"
    assert ext[0]["access_password"] == "Axr4nge-Lab!"
    assert ext[0]["access_hint"] == "ssh axrange@10.5.0.20"


def test_o2_info_external_sensor_degrades_gracefully_on_unreachable_cluster(tmp_path, capsys):
    """No monkeypatch here -- the adapter.conf has no PROXMOX_API_URL/DEFAULT_NODE
    so live resolution short-circuits to None without ever touching a socket
    (guardrail: an unreachable cluster must not crash `info`)."""
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(id_or_spec=None, db=db, adapter_config=str(conf), json=True)
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    ext = payload["external_sensors"][0]
    assert ext["resolved_name"] is None
    assert ext["resolved_ip"] is None
    # declared fields still print regardless.
    assert ext["transport_ip"] == "10.255.250.20"


def test_o2_info_no_secrets_masks_external_sensor_password(tmp_path, capsys):
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    db = str(tmp_path / "state.db")
    args = argparse.Namespace(
        id_or_spec=None, db=db, adapter_config=str(conf), json=False, no_secrets=True,
    )
    rc = climain.cmd_o2_info(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "********" in out
    assert "Axr4nge-Lab!" not in out


# ---------------------------------------------------------------------------
# `tap validate` -- two-tier resolve (work order §3.3).
# ---------------------------------------------------------------------------
def _write_tap_plan(tmp_path, *, target=TE_DEPLOY) -> str:
    p = tmp_path / "plan.yaml"
    p.write_text(
        "tap-plan:\n"
        f"  target: {target}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [pktxpress-1]\n"
    )
    return str(p)


def test_tap_validate_static_only_when_db_absent(tmp_path, capsys):
    plan_path = _write_tap_plan(tmp_path)
    args = argparse.Namespace(spec=plan_path, db=str(tmp_path / "nope.db"))
    rc = climain.cmd_tap_validate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "static OK" in out
    assert "skipping resolve checks" in out


def test_tap_validate_resolves_when_db_present(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_tap_plan(tmp_path)
    args = argparse.Namespace(spec=plan_path, adapter_config=None, db=db)
    rc = climain.cmd_tap_validate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "resolved against live state" in out
    # `_seed_te` doesn't set an explicit deploy status (defaults to
    # "running") -- this also exercises the "target not yet completed"
    # warning, a real resolve finding, not a hard error (work order §3.3).
    assert "status=running" in out
    assert "not 'completed'" in out
    assert "h1/corp[nic0] -> pktxpress-1" in out
    assert "Result: PASS" in out


def test_tap_validate_static_rendering_uses_binding_label_for_tap_point(
    tmp_path, capsys,
):
    """`docs/WORK_ORDER_tap_points.md`: the STATIC (no-DB) rendering pass
    must render a `tap_point:`/`selector:`-addressed binding via
    `binding_label`, not a bare `b.tap` (which is `None` for those two
    addressing modes) -- would otherwise print a bare "None -> ..." line."""
    p = tmp_path / "plan.yaml"
    p.write_text(
        "tap-plan:\n"
        "  target: some-te\n"
        "  bindings:\n"
        "    - tap_point: corp-lan\n"
        "      sensors: [pktxpress-1]\n"
        "    - selector: { scope: boundary }\n"
        "      sensors: [pktxpress-2]\n"
    )
    args = argparse.Namespace(spec=str(p), db=str(tmp_path / "nope.db"))
    rc = climain.cmd_tap_validate(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "tap_point:corp-lan -> pktxpress-1" in out
    assert "selector:" in out and "pktxpress-2" in out
    assert "None ->" not in out


def test_tap_validate_reports_error_for_unknown_sensor(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [no-such-sensor]\n"
    )
    args = argparse.Namespace(spec=str(plan_path), adapter_config=None, db=db)
    rc = climain.cmd_tap_validate(args)
    assert rc == 1
    assert "no-such-sensor" in capsys.readouterr().err


def test_tap_validate_accepts_config_external_sensor(tmp_path, capsys):
    """LES-069 consistency check: a binding naming a config-declared
    EXTERNAL sensor (adapter.conf EXTERNAL_SENSORS) is NOT in A1's standing
    inventory at all, yet `tap validate` -- a read-only entry point -- must
    resolve it exactly like `tap apply` would, not reject it as unknown."""
    db = _seed_te_and_tap(tmp_path, apply=False)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [XPress-SW]\n"
    )
    args = argparse.Namespace(spec=str(plan_path), adapter_config=str(conf), db=db)
    rc = climain.cmd_tap_validate(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Result: PASS" in out
    assert "XPress-SW" in out


def test_tap_info_renders_capture_unverified_line_for_external_sensor(tmp_path, capsys):
    """MUST-FIX #1 (code review): `tap info` must render a per-sensor
    "UNVERIFIED" marker for a config-external sensor binding, not show it
    looking identical to a heartbeat-confirmed A1 sensor."""
    db = _seed_te_and_tap(tmp_path, apply=False)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [XPress-SW]\n"
    )
    args = argparse.Namespace(
        id_or_spec=str(plan_path), adapter_config=str(conf), db=db, json=False,
    )
    rc = climain.cmd_tap_info(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "capture: UNVERIFIED (external sink)" in out

    # The negative case -- a standing A1 sensor binding shows no such marker.
    plan_path2 = tmp_path / "plan2.yaml"
    plan_path2.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [pktxpress-1]\n"
    )
    args2 = argparse.Namespace(
        id_or_spec=str(plan_path2), adapter_config=str(conf), db=db, json=False,
    )
    rc2 = climain.cmd_tap_info(args2)
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "UNVERIFIED" not in out2


def test_tap_info_resolves_twin_id_to_its_tap_deploy(tmp_path, capsys):
    """Reported bug: `tap info <twin-id>` (the te deploy id, not the
    `tap-<twin>-<hash>` deploy id) printed `target: ? (status=unknown)` /
    `bindings: (none)` because it read tap bindings off the te deploy itself.
    It must resolve the twin's applied tap deploy the same way
    `tap status --target` does (find_deploy_for_plan)."""
    db = _seed_te_and_tap(tmp_path, apply=True)
    conf = tmp_path / "adapter.conf"
    conf.write_text("")
    args = argparse.Namespace(
        id_or_spec=TE_DEPLOY, adapter_config=str(conf), db=db, json=False,
    )
    rc = climain.cmd_tap_info(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"target: {TE_DEPLOY}" in out
    assert "status=unknown" not in out          # bug marker
    assert "bindings: (none)" not in out        # bug marker
    assert "pktxpress-1" in out                 # the seeded binding renders


def test_tap_info_rejects_id_that_is_neither_tap_deploy_nor_tapped_twin(tmp_path, capsys):
    """An id that is neither a tap deploy nor a twin with an applied tap plan
    must error (exit 2), not print an empty/garbage tap card."""
    db = _seed_te_and_tap(tmp_path, apply=True)
    conf = tmp_path / "adapter.conf"
    conf.write_text("")
    args = argparse.Namespace(
        id_or_spec="nonexistent-xyz", adapter_config=str(conf), db=db, json=False,
    )
    rc = climain.cmd_tap_info(args)
    err = capsys.readouterr().err
    assert rc == 2
    assert "is not a tap-plan deploy" in err


def test_tap_info_shows_resolved_facts_for_external_sensor_binding(tmp_path, capsys, monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part C: `tap info` resolves + shows
    name/mgmt-ip/access facts for an EXTERNAL_SENSORS entry that's actually
    bound in the plan."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "resolve_vm_name", lambda cfg, vmid, **kw: "XPress-SW")
    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", lambda cfg, vmid, **kw: "10.5.0.20")
    db = _seed_te_and_tap(tmp_path, apply=False)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [XPress-SW]\n"
    )
    args = argparse.Namespace(
        id_or_spec=str(plan_path), adapter_config=str(conf), db=db, json=False,
    )
    rc = climain.cmd_tap_info(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "name=XPress-SW" in out
    assert "mgmt_ip=10.5.0.20" in out
    assert "ssh axrange@10.5.0.20" in out


def test_tap_info_only_resolves_bound_external_sensors_not_every_declared_one(
    tmp_path, capsys, monkeypatch,
):
    """Efficiency trim (nice-to-have, code review 2026-07-25): `tap info`
    must NOT pay every declared `EXTERNAL_SENSORS` entry's best-effort
    resolve cost -- only the sensor id(s) actually bound in THIS plan."""
    import axrange.adapter_proxmox.resolve as resolve_mod

    resolved_for: list[int] = []

    def _fake_resolve_ip(cfg, vmid, **kw):
        resolved_for.append(vmid)
        return f"10.5.0.{vmid}"

    monkeypatch.setattr(resolve_mod, "resolve_vm_name", lambda cfg, vmid, **kw: f"vm-{vmid}")
    monkeypatch.setattr(resolve_mod, "resolve_vm_ip", _fake_resolve_ip)
    db = _seed_te_and_tap(tmp_path, apply=False)
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=['
        '{"id":"XPress-SW","vmid":250,"transport_ip":"10.255.250.20"},'
        '{"id":"OtherSink","vmid":260,"transport_ip":"10.255.250.30"}'
        ']\n'
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        f"  target: {TE_DEPLOY}\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [XPress-SW]\n"
    )
    args = argparse.Namespace(
        id_or_spec=str(plan_path), adapter_config=str(conf), db=db, json=True,
    )
    rc = climain.cmd_tap_info(args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert resolved_for == [250]  # OtherSink (260) never resolved -- not bound
    assert set(payload["external_sensors"].keys()) == {"XPress-SW"}


# ---------------------------------------------------------------------------
# Predefinable tap plans -- optional `target:` + `--target` override + name
# resolution (`docs/WORK_ORDER_tap_target_resolution.md`).
# ---------------------------------------------------------------------------
def _write_predefined_tap_plan(tmp_path) -> str:
    """A tap-plan with NO `target:` at all -- the predefined/library shape."""
    p = tmp_path / "predefined-plan.yaml"
    p.write_text(
        "tap-plan:\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [pktxpress-1]\n"
    )
    return str(p)


def test_tap_validate_predefined_plan_no_target_is_semantics_only_pass(tmp_path, capsys):
    """Effective target ABSENT (no `target:`, no `--target`) -> structural
    checks only, PASS, no StateStore/inventory lookup at all (work order
    §"tap validate", mode ii)."""
    plan_path = _write_predefined_tap_plan(tmp_path)
    args = argparse.Namespace(
        spec=plan_path, target=None, adapter_config=None,
        db=str(tmp_path / "nope.db"),
    )
    rc = climain.cmd_tap_validate(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "semantics-only" in out
    assert "Result: PASS" in out
    # No DB touched -- the file must not have been created.
    assert not (tmp_path / "nope.db").exists()


def test_tap_validate_target_flag_supplies_missing_plan_target(tmp_path, capsys):
    """`--target` lets a PREDEFINED plan (no `target:`) resolve + validate
    against a real, live twin -- mode (i), by NAME this time."""
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_predefined_tap_plan(tmp_path)
    args = argparse.Namespace(
        spec=plan_path, target="te-scn", adapter_config=None, db=db,
    )
    rc = climain.cmd_tap_validate(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Result: PASS" in out
    assert f"target TE {TE_DEPLOY}" in out


def test_tap_validate_target_flag_overrides_plan_target(tmp_path, capsys):
    """`--target` WINS over a `target:` the plan itself declares."""
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_tap_plan(tmp_path, target=TE_DEPLOY)
    args = argparse.Namespace(
        spec=plan_path, target="totally-does-not-exist",
        adapter_config=None, db=db,
    )
    rc = climain.cmd_tap_validate(args)
    err = capsys.readouterr().err
    # If the plan's OWN target: had won, this would resolve fine (TE_DEPLOY
    # is live) -- the override failing to resolve proves it took precedence.
    assert rc == 1
    assert "totally-does-not-exist" in err


def test_tap_validate_target_present_fails_when_not_live(tmp_path, capsys):
    """Work order §6: effective target PRESENT but resolves to a destroyed
    deploy -> validation FAILS outright (not a soft warning)."""
    db = _seed_te_and_tap(tmp_path, apply=False)
    store = StateStore(db)
    try:
        store.mark_destroyed(TE_DEPLOY)
    finally:
        store.close()
    plan_path = _write_tap_plan(tmp_path)
    args = argparse.Namespace(spec=plan_path, target=None, adapter_config=None, db=db)
    rc = climain.cmd_tap_validate(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "not a live, active te deploy" in err


def test_tap_validate_target_flag_rejects_tap_plan_playout_id(tmp_path, capsys):
    """SHOULD-FIX (code review): `--target <tap-plan-playout-deploy-id>`
    must fail fast with a clear message, not sail past the live/tier=te
    check and die confusingly later in bindings resolution."""
    db = _seed_te_and_tap(tmp_path, apply=True)
    store = StateStore(db)
    try:
        tap_deploy = store.find_deploy_for_plan(f"tap-{TE_DEPLOY}")
    finally:
        store.close()
    assert tap_deploy is not None

    plan_path = _write_predefined_tap_plan(tmp_path)
    args = argparse.Namespace(
        spec=plan_path, target=tap_deploy.deploy_id, adapter_config=None, db=db,
    )
    rc = climain.cmd_tap_validate(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "tap-plan playout" in err


def test_tap_validate_no_target_still_rejects_a_structural_exclusive_collision(
    tmp_path, capsys,
):
    """Work order §"tap validate": the target-ABSENT (semantics-only) mode
    still runs every static structural check -- including the plan-wide
    `mode: exclusive` consistency check (a tap point claimed by two
    bindings at once) -- with NO live resolution and NO state touched. Two
    bindings both addressing the SAME literal `tap:` id (one exclusive) is
    the structural collision `parse_tap_plan`'s duplicate-binding check
    catches independent of any target being present."""
    plan_path = tmp_path / "collision-plan.yaml"
    plan_path.write_text(
        "tap-plan:\n"
        "  bindings:\n"
        "    - tap: corp\n"
        "      sensors: [pktxpress-1]\n"
        "      mode: exclusive\n"
        "    - tap: corp\n"
        "      sensors: [pktxpress-2]\n"
    )
    args = argparse.Namespace(
        spec=str(plan_path), target=None, adapter_config=None,
        db=str(tmp_path / "nope.db"),
    )
    rc = climain.cmd_tap_validate(args)
    err = capsys.readouterr().err
    assert rc != 0
    assert "duplicate" in err.lower()
    # No StateStore ever opened -- the parse-time failure happens before
    # this function even checks effective-target presence.
    assert not (tmp_path / "nope.db").exists()


def test_tap_deploy_no_target_anywhere_adds_no_deploy_row_to_existing_db(tmp_path, capsys):
    """Stricter than `test_tap_deploy_no_target_anywhere_errors_with_no_
    state_written` above (which only checks the DB FILE was never
    created): here the DB already has real deploys on file (a `te` +
    standing tap-plan playout) BEFORE the no-target call, so a bare
    file-existence check wouldn't catch a stray row landing in an
    ALREADY-EXISTING state.db."""
    db = _seed_te_and_tap(tmp_path, apply=True)
    store = StateStore(db)
    try:
        before = {row.deploy_id for row in store.latest_deploys(limit=100)}
    finally:
        store.close()

    plan_path = _write_predefined_tap_plan(tmp_path)
    rc = climain.main(["tap", "deploy", plan_path, "--db", db])
    assert rc != 0
    assert "no tap-plan target" in capsys.readouterr().err

    store = StateStore(db)
    try:
        after = {row.deploy_id for row in store.latest_deploys(limit=100)}
    finally:
        store.close()
    assert after == before


def test_tap_plan_no_target_anywhere_errors(tmp_path, capsys):
    plan_path = _write_predefined_tap_plan(tmp_path)
    rc = climain.main([
        "tap", "plan", plan_path, "--db", str(tmp_path / "nope.db"),
    ])
    assert rc != 0
    assert "no tap-plan target" in capsys.readouterr().err


def test_tap_deploy_no_target_anywhere_errors_with_no_state_written(tmp_path, capsys):
    plan_path = _write_predefined_tap_plan(tmp_path)
    db = str(tmp_path / "state.db")
    rc = climain.main(["tap", "deploy", plan_path, "--db", db])
    assert rc != 0
    assert "no tap-plan target" in capsys.readouterr().err
    assert not Path(db).exists()


def test_tap_plan_target_flag_resolves_scenario_name(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_predefined_tap_plan(tmp_path)
    rc = climain.main([
        "tap", "plan", plan_path, "--target", "te-scn",
        "--node", "pxtest1", "--db", db,
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"tap-{TE_DEPLOY}" in out


def test_tap_deploy_target_flag_threaded_to_apply_tap_plan(tmp_path, monkeypatch):
    """Unit-level check (mirrors the `o2 tap apply` shim test above): `tap
    deploy --target ...` passes the override straight through to
    `apply_tap_plan`'s `target=` kwarg."""
    import axrange.o2control.taps as taps_mod
    from axrange.o2control.taps import TapPlanStatus

    calls = {}

    def fake_apply_tap_plan(*, spec_path, db_path, adapter_config, target_node,
                             target=None):
        calls["target"] = target
        return TapPlanStatus(deploy_id="tap-x", target="resolved-te", status="completed")

    monkeypatch.setattr(taps_mod, "apply_tap_plan", fake_apply_tap_plan)

    rc = climain.main([
        "tap", "deploy", "plan.yaml", "--target", "corp-enterprise",
        "--db", str(tmp_path / "state.db"),
    ])
    assert rc == 0
    assert calls["target"] == "corp-enterprise"


# ---------------------------------------------------------------------------
# `tap plan` / `tap info` -- dry compose + resolved-bindings projection.
# ---------------------------------------------------------------------------
def test_tap_plan_renders_composed_ops_without_executing(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_tap_plan(tmp_path)
    args = argparse.Namespace(
        spec=plan_path, adapter_config=None, node="pxtest1", db=db,
    )
    rc = climain.cmd_tap_plan(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "attach-gre-tunnel" in out
    assert "install-ovs-mirror" in out

    # Nothing was actually applied -- no tap-plan deploy exists yet.
    store = StateStore(db)
    try:
        assert store.find_deploy_for_plan(f"tap-{TE_DEPLOY}") is None
    finally:
        store.close()


def test_malformed_external_sensors_fails_clean_not_traceback_via_main(tmp_path, capsys):
    """SHOULD-FIX #2 (code review): `load_config` raises `ConfigError` LOUD
    on a malformed `EXTERNAL_SENSORS`, but only `cmd_tap_validate`/
    `cmd_tap_info` used to catch it -- every other config-loading path
    (`cmd_tap_plan` here) would dump a raw traceback on the exact same bad
    input. `main()`'s new top-level handler must catch it for EVERY
    dispatched command, not just the two that already had their own catch --
    drive this through `climain.main(...)` (full argv dispatch), not
    `cmd_tap_plan(args)` directly, since the fix lives in `main()`."""
    plan_path = _write_tap_plan(tmp_path)
    bad_conf = tmp_path / "adapter.conf"
    bad_conf.write_text("EXTERNAL_SENSORS=not-json\n")

    rc = climain.main([
        "tap", "plan", str(plan_path),
        "--adapter-config", str(bad_conf),
        "--db", str(tmp_path / "missing.db"),
    ])
    assert rc != 0
    err = capsys.readouterr().err
    assert "[error] adapter.conf:" in err
    assert "Traceback" not in err


def test_tap_info_dry_then_live(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path, apply=False)
    plan_path = _write_tap_plan(tmp_path)

    dry_args = argparse.Namespace(id_or_spec=plan_path, adapter_config=None, db=db, json=True)
    rc = climain.cmd_tap_info(dry_args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] is None
    assert payload["status"] == "not-applied"
    assert payload["bindings"]
    binding = payload["bindings"][0]
    assert binding["sensors"] == ["pktxpress-1"]
    assert binding["sensor_transport_ips"]["pktxpress-1"] == "10.255.250.11"

    apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
        target_node="pxtest1",
    )

    live_args = argparse.Namespace(id_or_spec=None, adapter_config=None, db=db, json=True)
    rc = climain.cmd_tap_info(live_args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] is not None
    assert payload["status"] == "completed"
    assert payload["bindings"]


# ---------------------------------------------------------------------------
# `list-deploys --tier`.
# ---------------------------------------------------------------------------
def _first_column_ids(output: str) -> set[str]:
    """The deploy_id column (leftmost, whitespace-padded) of `cmd_list_
    deploys`'s table -- NOT a raw substring search: the tap-plan playout's
    OWN deploy_id (`tap-<TE_DEPLOY>-<hex>`) literally CONTAINS `TE_DEPLOY`
    as a substring, so a naive `TE_DEPLOY in output` check would false-
    positive against the tap-only listing even though the te deploy itself
    is correctly excluded."""
    lines = output.strip().splitlines()[1:]  # skip the header row
    return {line.split()[0] for line in lines if line.strip()}


def test_list_deploys_tier_filter(tmp_path, capsys):
    db = _seed_te_and_tap(tmp_path)

    args = argparse.Namespace(db=db, limit=20, tier="te")
    rc = climain.cmd_list_deploys(args)
    assert rc == 0
    ids = _first_column_ids(capsys.readouterr().out)
    assert ids == {TE_DEPLOY}

    args = argparse.Namespace(db=db, limit=20, tier="tap")
    rc = climain.cmd_list_deploys(args)
    assert rc == 0
    ids = _first_column_ids(capsys.readouterr().out)
    assert len(ids) == 1
    assert next(iter(ids)).startswith("tap-")
    assert TE_DEPLOY not in ids

    args = argparse.Namespace(db=db, limit=20, tier="o2")
    rc = climain.cmd_list_deploys(args)
    assert rc == 0
    ids = _first_column_ids(capsys.readouterr().out)
    assert ids == {O2_DEPLOY}


def test_list_deploys_tier_tap_excludes_a_tap_named_te(tmp_path, capsys):
    """Code review finding: `list-deploys --tier tap` used to trust the
    `tap-` plan_id prefix alone -- a genuine `te` scenario literally named
    `tap-decoy` (ordinary te-shaped ops, not the three OP-MIR ops) must be
    excluded, and the genuine tap-plan playout must still be found."""
    db = _seed_te_and_tap(tmp_path)
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="decoy-te-1", scenario_id="tap-decoy", scenario_version=1,
            scenario_schema_version=1, plan_id="tap-decoy-deadbeef", ir_hash="h",
            placement={"h1": "pxtest1"}, tier="te",
        )
        from tests.test_o2_taps import _op_row

        store.insert_ops(
            "decoy-te-1",
            [
                _op_row(
                    "decoy-te-1", "op-clone-h1", "clone-vm", "OP-HOST-010",
                    {"host": "h1"}, outputs={"vmid": 500001},
                ),
            ],
        )
    finally:
        store.close()

    args = argparse.Namespace(db=db, limit=20, tier="tap")
    rc = climain.cmd_list_deploys(args)
    assert rc == 0
    ids = _first_column_ids(capsys.readouterr().out)
    assert "decoy-te-1" not in ids
    assert len(ids) == 1
    assert next(iter(ids)).startswith("tap-")


def _tier_by_id(output: str) -> dict[str, str]:
    """Map deploy_id -> the `tier` column (2nd whitespace field) of the
    `cmd_list_deploys` table, skipping the header row."""
    out: dict[str, str] = {}
    for line in output.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def test_list_deploys_shows_tier_column(tmp_path, capsys):
    """The unfiltered listing carries a `tier` column; a tap-plan playout —
    stored `tier="te"` with a `tap-` plan_id — must DISPLAY as `tap`
    (LES-082 structural discrimination via `is_tap_plan_deploy`), not `te`."""
    db = _seed_te_and_tap(tmp_path)
    args = argparse.Namespace(db=db, limit=20, tier=None)
    rc = climain.cmd_list_deploys(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "tier" in out.strip().splitlines()[0]  # header carries the column
    tiers = _tier_by_id(out)
    assert tiers[TE_DEPLOY] == "te"
    assert tiers[O2_DEPLOY] == "o2"
    tap_ids = [i for i in tiers if i.startswith("tap-")]
    assert len(tap_ids) == 1
    assert tiers[tap_ids[0]] == "tap"


# ---------------------------------------------------------------------------
# `te deploy` resume/revival semantics (code review finding,
# docs/WORK_ORDER_cli_lifecycle.md, Part B): a `destroyed` deploy is
# terminal and must never be silently revived by a plain (non-`--fresh`)
# re-deploy; every OTHER status keeps resuming the same deploy_id.
# ---------------------------------------------------------------------------
def _te_deploy_via_fake_adapter(db_path, monkeypatch, *, fresh=False):
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_cli import _deploy_args
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())
    return climain.cmd_deploy(_deploy_args(db=db_path, fresh=fresh))


def test_te_deploy_mints_fresh_id_after_destroy_not_revive(tmp_path, monkeypatch):
    """The exact dev45 repro (coordinator's Part B): re-deploying an
    unchanged scenario after `axrange te destroy` must mint a brand-new
    deploy_id, not resurrect the tombstoned row (status destroyed->running,
    ops reset, OLD deploy_id/started_at)."""
    db_path = str(tmp_path / "state.db")

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0
    store = StateStore(db_path)
    try:
        did1 = store.latest_deploys(limit=1)[0].deploy_id
        store.mark_destroyed(did1)
    finally:
        store.close()

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0

    store = StateStore(db_path)
    try:
        did2 = store.latest_deploys(limit=1)[0].deploy_id
        old = store.get_deploy(did1)
        new = store.get_deploy(did2)
    finally:
        store.close()
    assert did2 != did1
    assert old.status.value == "destroyed"  # untouched, still tombstoned
    assert new.status.value == "completed"


def test_te_deploy_resumes_completed_deploy_same_id(tmp_path, monkeypatch, capsys):
    """Resume-safety preserved: a `completed` deploy of an unchanged
    scenario is adopted (no-op, no new deploy_id) -- Part B excludes ONLY
    `destroyed`."""
    db_path = str(tmp_path / "state.db")

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0
    store = StateStore(db_path)
    try:
        did1 = store.latest_deploys(limit=1)[0].deploy_id
        assert store.get_deploy(did1).status.value == "completed"
    finally:
        store.close()
    capsys.readouterr()

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0
    assert "already deployed" in capsys.readouterr().out.lower()

    store = StateStore(db_path)
    try:
        did2 = store.latest_deploys(limit=1)[0].deploy_id
    finally:
        store.close()
    assert did2 == did1


def test_te_deploy_resumes_failed_deploy_same_id(tmp_path, monkeypatch):
    """Resume-safety preserved: a `failed` deploy of an unchanged scenario
    is RESUMED under the same deploy_id (ops reset + re-run), not replaced
    -- Part B excludes ONLY `destroyed`."""
    from axrange.planner.state import DeployStatus

    db_path = str(tmp_path / "state.db")

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0
    store = StateStore(db_path)
    try:
        did1 = store.latest_deploys(limit=1)[0].deploy_id
        store.update_deploy_status(did1, DeployStatus.FAILED)
    finally:
        store.close()

    rc = _te_deploy_via_fake_adapter(db_path, monkeypatch)
    assert rc == 0

    store = StateStore(db_path)
    try:
        did2 = store.latest_deploys(limit=1)[0].deploy_id
        resumed = store.get_deploy(did2)
    finally:
        store.close()
    assert did2 == did1  # SAME deploy_id -- resumed, not replaced
    assert resumed.status.value == "completed"


# ---------------------------------------------------------------------------
# Destroy bare-default "prefer newest ACTIVE" (code review residual, same
# family as the mint-fresh-after-destroy fix above): `mark_destroyed` bumps
# `updated_at`, so a just-destroyed deploy would otherwise be the TOP of the
# `updated_at`-ordered lookup -- a bare destroy right after destroying an
# OLDER deploy must not resolve to that tombstone while a genuinely live,
# newer deploy of the same tier goes untouched. `info`/`status` bare
# defaults are UNCHANGED (they want the tombstone on top).
# ---------------------------------------------------------------------------
def test_te_destroy_bare_default_prefers_newest_active_over_tombstone(
    tmp_path, monkeypatch, capsys,
):
    import time as _t

    import axrange.adapter_proxmox as adapter_mod
    from tests.test_teardown import _RecordTeardown, seed_deploy

    db_path = str(tmp_path / "state.db")
    # Y: older, seeded first.
    seed_deploy(db_path, deploy_id="dep-y", scenario_id="scenario-y", plan_id="plan-y")
    _t.sleep(0.01)
    # X: newer, seeded second -- genuinely live, unrelated to Y.
    seed_deploy(db_path, deploy_id="dep-x", scenario_id="scenario-x", plan_id="plan-x")

    fake_teardown = _RecordTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_teardown)

    # Explicitly destroy Y -- this bumps Y's updated_at to NOW, ranking it
    # ahead of X under plain updated_at ordering (the exact residual bug).
    destroy_y_args = argparse.Namespace(
        deploy_id="dep-y", db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(destroy_y_args)
    assert rc == 0
    calls_after_y = len(fake_teardown.calls)
    capsys.readouterr()

    # Bare destroy (no explicit id) must target X, NOT Y's tombstone.
    bare_args = argparse.Namespace(
        deploy_id=None, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(bare_args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dep-x" in out
    assert "dep-y" not in out

    store = StateStore(db_path)
    try:
        x = store.get_deploy("dep-x")
        y = store.get_deploy("dep-y")
    finally:
        store.close()
    assert x.status.value == "destroyed"  # X actually got torn down THIS time
    assert y.status.value == "destroyed"  # unchanged, still tombstoned
    # X's teardown genuinely ran (more backend calls than after Y alone).
    assert len(fake_teardown.calls) > calls_after_y


def test_te_destroy_bare_default_all_destroyed_prints_explicit_message(
    tmp_path, monkeypatch, capsys,
):
    """When every te deploy is already destroyed, the bare default must
    NEVER print the "Result: DESTROYED" success banner -- that would tell
    the operator their (nonexistent) live deploy is gone when nothing was
    touched."""
    import axrange.adapter_proxmox as adapter_mod
    from tests.test_teardown import _RecordTeardown, seed_deploy

    db_path = str(tmp_path / "state.db")
    seed_deploy(db_path, deploy_id="dep-only", scenario_id="scenario-only", plan_id="plan-only")

    fake_teardown = _RecordTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_teardown)

    destroy_args = argparse.Namespace(
        deploy_id="dep-only", db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(destroy_args)
    assert rc == 0
    capsys.readouterr()

    bare_args = argparse.Namespace(
        deploy_id=None, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(bare_args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "already destroyed" in out.lower()
    assert "nothing to do" in out.lower()
    assert "DESTROYED" not in out  # the misleading success banner must never print


def test_te_status_and_info_bare_default_still_show_tombstone(
    tmp_path, monkeypatch, capsys,
):
    """`te status`/`te info` bare defaults are UNCHANGED -- they must keep
    surfacing the just-destroyed tombstone (to inspect what you just
    destroyed), even though `te destroy`'s bare default now skips it."""
    import time as _t

    import axrange.adapter_proxmox as adapter_mod
    from tests.test_teardown import _RecordTeardown, seed_deploy

    db_path = str(tmp_path / "state.db")
    seed_deploy(db_path, deploy_id="dep-y2", scenario_id="scenario-y2", plan_id="plan-y2")
    _t.sleep(0.01)
    seed_deploy(db_path, deploy_id="dep-x2", scenario_id="scenario-x2", plan_id="plan-x2")

    fake_teardown = _RecordTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_teardown)

    destroy_y_args = argparse.Namespace(
        deploy_id="dep-y2", db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(destroy_y_args)
    assert rc == 0
    capsys.readouterr()

    status_args = argparse.Namespace(deploy_id=None, db=db_path, json=True)
    rc = climain.cmd_status(status_args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] == "dep-y2"
    assert payload["status"] == "destroyed"

    info_args = argparse.Namespace(
        id_or_spec=None, images=None, adapter_config=None, node=None,
        db=db_path, json=True,
    )
    rc = climain.cmd_te_info(info_args)
    assert rc == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["deploy_id"] == "dep-y2"
    assert payload["status"] == "destroyed"


def test_tap_destroy_bare_default_prefers_newest_active_over_tombstone(
    tmp_path, monkeypatch, capsys,
):
    """Same "prefer newest active" fix, tap tier."""
    import time as _t

    import axrange.adapter_proxmox as adapter_mod
    from tests.test_o2_taps import _FakeTeardown

    TE_2 = "te-taps-test-2"
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    try:
        _seed_te(store, deploy_id=TE_DEPLOY)
        _seed_te(store, deploy_id=TE_2)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY, bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
        target_node="pxtest1",
    )
    _t.sleep(0.01)
    apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_2, bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db, adapter=_FakeMirAdapter(), config_backend=_FakeMirAdapter(),
        target_node="pxtest1",
    )

    store = StateStore(db)
    try:
        tap_y = store.find_deploy_for_plan(f"tap-{TE_DEPLOY}")
        tap_x = store.find_deploy_for_plan(f"tap-{TE_2}")
    finally:
        store.close()
    assert tap_y is not None and tap_x is not None
    assert tap_y.deploy_id != tap_x.deploy_id

    fake_adapter = _FakeMirAdapter()
    fake_teardown = _FakeTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fake_adapter)
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_teardown)

    # Explicitly destroy the FIRST (Y) tap-plan.
    destroy_y_args = argparse.Namespace(
        deploy_id=tap_y.deploy_id, target=None, db=db,
        adapter_config="/nonexistent.conf", node="pxtest1", allow_destroy=True,
    )
    rc = climain.cmd_tap_destroy(destroy_y_args)
    assert rc == 0
    capsys.readouterr()

    # Bare `tap destroy` must target X's tap-plan, NOT Y's tombstone.
    bare_args = argparse.Namespace(
        deploy_id=None, target=None, db=db, adapter_config="/nonexistent.conf",
        node="pxtest1", allow_destroy=True,
    )
    rc = climain.cmd_tap_destroy(bare_args)
    assert rc == 0
    out = capsys.readouterr().out
    assert tap_x.deploy_id in out
    assert tap_y.deploy_id not in out

    store = StateStore(db)
    try:
        y_after = store.get_deploy(tap_y.deploy_id)
        x_after = store.get_deploy(tap_x.deploy_id)
    finally:
        store.close()
    assert y_after.status.value == "destroyed"
    assert x_after.status.value == "destroyed"  # X actually got torn down THIS time
