"""`axrange` CLI entry point.

The CLI is a uniform grid — deployable object types, one verb set
each (`docs/WORK_ORDER_cli_lifecycle.md`; `tfc` added by `docs/
WORK_ORDER_traffic_tier.md`):

  axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}
                           └─ spec ─┘  └── id / latest ──┘   └guard┘

  axrange te validate <scenario.yaml>        — runs Passes 1-3 + renders
  axrange te validate --explain <RULE-ID>    — prints rule definition
  axrange te validate --rules                — lists every rule
  axrange te plan <scenario.yaml>            — build DAG, print tree, no execute
  axrange te deploy <scenario.yaml> [--fresh]— build DAG + execute
  axrange te status [<id>]                   — show a deploy's state tree
  axrange te info [<id>|<scenario.yaml>]     — resolved config + access facts
  axrange te destroy [<id>] --allow-destroy  — reverse-of-create teardown

  axrange o2 {validate,plan,deploy,status,info,destroy} [<surface.yaml>|<id>]
      — the standing O2 surface (sensors + transport vnet), `axrange.o2control`

  axrange tap {validate,plan,deploy,status,info,destroy} [<tap-plan.yaml>|<id>]
      — the A2 tap-config playout (bind a deployed te's taps to standing O2
      sensors), `axrange.o2control.taps`

  axrange tfc {validate,plan,deploy,status,info,destroy} [<traffic-profile.yaml>|<id>]
      — the Traffic tier (bind personas onto a deployed te's host roles),
      `axrange.o2control.traffic`; a te destroy cascades to its tfc bindings

  axrange exr {run|deploy,status,destroy} [<exercise-spec.yaml>|<id>]
      — the Exercise tier (`docs/WORK_ORDER_exercise_tier.md`): run an attack
      playout (internal `activity` and/or a Sandcat/Caldera adversary plant)
      against an already-deployed twin, `axrange.o2control.exercise`.
      DELIBERATELY NARROWER than the tap/tfc grid — no `validate`/`plan`/
      `info` (see `_add_exr_deploy`'s comment). Ephemeral: every `deploy`/
      `run` mints a fresh deploy_id (unlike tap/tfc's converge-in-place
      binding); a te destroy cascades EVERY exr run bound to it. `run` is a
      first-class alias of `deploy`, not a deprecated shim.

Cross-cutting (outside the grid — not per-tier deploys):

  axrange list-deploys [--tier {te,o2,tap,tfc,exr}]  — show recent deploys
  axrange serve                              — control-plane API
  axrange image build <image>                — image build pipeline
  axrange twin-config list|show <id>         — the twin-config catalog (parameterized
                                                IR templates, docs/WORK_ORDER_twin_configs.md);
                                                deploy one via `te {validate,plan,deploy}
                                                --config <id> [--param k=v ...]` instead of a
                                                scenario.yaml positional (mutually exclusive)

`plan`/`deploy`/`info` need adapter configuration (default node etc.); read
from `--adapter-config`, env vars, or `config/adapter.conf`. See
`config/adapter.conf.example` for the keys. A `te_` deploy carries no sensor
identity of its own (sensor-agnostic te, docs/WORK_ORDER_sensor_agnostic_te.md)
— `axrange tap deploy` binds a deployed te's taps to a standing O2 sensor
after the fact.

**Transitional deprecation shims (one release cycle, then removed —
`docs/WORK_ORDER_cli_lifecycle.md` §3.5):** bare `validate`/`plan`/`deploy`/
`status`/`destroy` → `te <verb>`; `o2 up` → `o2 deploy`; `o2 down` →
`o2 destroy`; `o2 tap apply` → `tap deploy`; `o2 tap destroy` → `tap
destroy`. Each prints a one-line stderr notice, then runs the canonical
handler — grep `# DEPRECATED shim` in `_build_parser` below.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from axrange import __version__
from axrange.api.settings import default_db_path
from axrange.manifest.loader import ManifestError, load_registry
from axrange.planner.access import (  # noqa: F401 -- TeHostInfo/TeInfo/TeSubnetInfo
    TeHostInfo, TeInfo, TeSubnetInfo,  # re-exported for any external caller of
    fill_access_facts as _fill_access_facts,  # the CLI's historical shape
    te_info_from_ops as _te_info_from_ops,
)
from axrange.validator.catalog import list_rules
from axrange.validator.engine import (
    Disposition,
    ValidationResult,
    explain_rule,
    validate_full,
)
from axrange.validator.finding import Finding, Severity


# Default state-store location. Operators can override via --db / AXRANGE_DB.
# Shared with the API (code-review finding #7, 2026-07-07) — one place
# resolves AXRANGE_DB / ~/.axrange/state.db, not two independent copies.
_DEFAULT_DB_PATH = default_db_path()


#  te <verb> builders — one per verb, each returns the parser it built so a
#  DEPRECATED bare shim (`axrange validate` etc., §3.5) can call the SAME
#  builder against the top-level `sub` group instead of a second,
#  copy-pasted `add_argument` block. `set_defaults(tier=..., verb=...)` is
#  the argparse-native dispatch mechanism `main()` reads (work order §3.5:
#  "Implement as argparse dispatch ... set args.tier/args.verb").
# ---------------------------------------------------------------------------
def _add_config_flags(p) -> None:
    """`--config <id> --param k=v ...` — an alternative to the positional
    `scenario.yaml` (`docs/WORK_ORDER_twin_configs.md` §2 CLI choice: fit the
    existing `te {validate,plan,deploy}` grid rather than adding a parallel
    verb set). `_resolve_scenario_arg` below expands `--config` into a real
    materialized IR file and overwrites `args.scenario` with its path BEFORE
    any of the unchanged validate/plan/deploy logic runs — so `--config` is
    just another way to produce the `scenario.yaml` positional every other
    code path already expects, not a second deploy pipeline (LES-037)."""
    p.add_argument(
        "--config", metavar="TWIN-CONFIG-ID", default=None,
        help=("Expand a twin-config (`twins/<id>/te.yaml` or `twin-configs/<id>/"
              "<id>.yaml`) instead of a scenario.yaml positional; see "
              "`axrange twin-config list`"),
    )
    p.add_argument(
        "--param", metavar="KEY=VALUE", action="append", default=None,
        help="Override a twin-config param (repeatable); only valid with --config",
    )
    p.add_argument(
        "--twins-dir", "--twin-configs-dir", dest="twin_configs_dir", default=None,
        help=("Catalog directory for --config (default: the repo root's twins/, after "
              "the legacy twin-configs/ -- not relative to the current directory; "
              "--twin-configs-dir is the old spelling)"),
    )


def _add_te_validate(group, *, deprecated: Optional[str] = None):
    p = group.add_parser("validate", help="Validate a scenario IR")
    p.add_argument("scenario", nargs="?", metavar="scenario.yaml",
                   help="Path to scenario.yaml (or use --config)")
    p.add_argument("--explain", metavar="RULE-ID",
                   help="Print the definition of one rule and exit")
    p.add_argument("--images", default=None,
                   help=("Root directory containing images/manifests/ and "
                         "images/bindings/proxmox/ (defaults to scenario parent)"))
    p.add_argument("--verbose", action="store_true",
                   help="Show info-severity findings (default: suppressed)")
    p.add_argument("--rules", action="store_true",
                   help="List every rule in the catalog and exit")
    _add_config_flags(p)
    p.set_defaults(tier="te", verb="validate", command_label="te validate",
                   deprecated_notice=deprecated)
    return p


def _add_te_plan(group, *, deprecated: Optional[str] = None):
    p = group.add_parser("plan", help="Build a deploy plan (DAG) without executing it")
    p.add_argument("scenario", nargs="?", metavar="scenario.yaml",
                   help="Path to scenario.yaml (or use --config)")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (default: /etc/axrange/adapter.conf, then the checkout's config/adapter.conf, then ./adapter.conf)")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    _add_config_flags(p)
    p.set_defaults(tier="te", verb="plan", command_label="te plan", deprecated_notice=deprecated)
    return p


def _add_te_deploy(group, *, deprecated: Optional[str] = None):
    p = group.add_parser(
        "deploy", help="Build a plan and execute it against the configured Proxmox node"
    )
    p.add_argument("scenario", nargs="?", metavar="scenario.yaml",
                    help="Path to scenario.yaml (or use --config)")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--dry-run", action="store_true",
                   help="Build the plan and print it; do not execute (same as `plan`)")
    p.add_argument("--fresh", action="store_true",
                   help=("Force a brand-new deploy_id even if a prior deploy "
                         "for this plan already exists in the state DB. Default: "
                         "resume the latest prior deploy if one is present."))
    # Incremental update (`docs/WORK_ORDER_twin_incremental_update.md`) --
    # add/remove nodes on a STANDING deploy without redeploying the whole
    # twin. A deliberately REDUCED subset of `docs/PLANNER.md` §9
    # (re-planning): mutation (an existing node's fields changed) is always
    # refused, never redone -- see the work order for why.
    p.add_argument("--update", action="store_true",
                   help=("Incrementally update the standing deploy of this "
                         "scenario: run added ops, sweep removed ones "
                         "(--allow-destroy required for any removal), "
                         "refuse loudly on any mutated op. Mutually "
                         "exclusive with --fresh."))
    p.add_argument("--allow-destroy", action="store_true",
                   help="With --update: confirm a non-empty removed set may be swept")
    # Content-only re-apply (`docs/WORK_ORDER_responder_op_row_drift.md`
    # option A, Authority: user 2026-09-02) -- the ONE carve-out from the
    # mutation refusal above, opt-in per invocation.
    p.add_argument("--reapply", action="store_true",
                   help=("With --update: re-execute a mutated `run-script` "
                         "whose ONLY changed input is its script content "
                         "(instead of refusing it). Every other mutation "
                         "still refuses. Non-idempotent — the plan is "
                         "printed with old->new content hashes and "
                         "confirmed before anything runs."))
    p.add_argument("--yes", action="store_true",
                   help="With --update: skip the interactive confirmation prompt")
    _add_config_flags(p)
    p.set_defaults(tier="te", verb="deploy", command_label="te deploy", deprecated_notice=deprecated)
    return p


def _add_te_status(group, *, deprecated: Optional[str] = None):
    p = group.add_parser("status", help="Show a deploy's state-store tree")
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Deploy id (default: most recent te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="te", verb="status", deprecated_notice=deprecated)
    return p


def _add_te_info(group):
    p = group.add_parser(
        "info", help="Show a scenario/deploy's resolved config + access facts"
    )
    p.add_argument(
        "id_or_spec", nargs="?", metavar="[ID|scenario.yaml]",
        help=("Deploy id, OR a scenario.yaml (config-only if not yet deployed); "
              "default: the most recent te deploy"),
    )
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.add_argument("--no-secrets", action="store_true",
                   help="Mask access passwords (********) for screen-sharing; "
                        "--json always includes the real values regardless")
    p.set_defaults(tier="te", verb="info")
    return p


def _add_te_destroy(group, *, deprecated: Optional[str] = None):
    p = group.add_parser(
        "destroy",
        help="Tear down a deployed scenario (reverse-of-create; --allow-destroy required)",
    )
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Deploy id to destroy (default: most recent te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (teardown REST/SSH creds)")
    p.add_argument("--node", default=None,
                   help="Override the PVE node for node-scoped teardown ops")
    p.add_argument("--allow-destroy", action="store_true",
                   help="Required confirmation: actually remove resources")
    p.set_defaults(tier="te", verb="destroy", deprecated_notice=deprecated)
    return p


def _add_te_inventory(group):
    """`te inventory [ID]` — the twin's published binding contract
    (docs/design/02-twin-model.md, docs/WORK_ORDER_tier_split.md): the
    persona-assignable `hosts[]` roster (full attachment topology, incl.
    `nets[]`), read back from a DEPLOYED te's recorded state (`axrange.
    planner.inventory`). No `tap_points[]` any more (`docs/
    WORK_ORDER_tap_eligibility.md` D5) -- capture eligibility/scope/target
    are entirely the tap tier's business, resolved against this same
    `hosts[].nets[]` topology at bind time, never published from here.

    A new top-level verb rather than a `te info --inventory` flag: unlike
    `info` (which also supports a not-yet-deployed `scenario.yaml` — a
    config-only preview), a twin's inventory only exists once actually
    deployed (hosts need real vmids/placement) — giving it its own verb
    keeps that always-deploy-scoped semantic explicit instead of `info`
    silently behaving differently depending on whether its argument is a
    file or a deploy id."""
    p = group.add_parser(
        "inventory",
        help="Show a deployed twin's published inventory (hosts + tap points)",
    )
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Deploy id (default: most recent te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument(
        "--images", default=None,
        help=(
            "Root containing images/manifests/ — resolves each host's `os` "
            "family via image_id lookup. Omit to publish `os: null` for "
            "every host (no fabricated default; docs/WORK_ORDER_tier_split.md "
            "review MUST-FIX #2)."
        ),
    )
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="te", verb="inventory")
    return p


def _add_te_egress(group):
    """`te egress [TARGET] --show | --phase measure|provision` -- read or flip a
    deployed twin's LIVE router egress phase and record it where `te info`
    and the tap/tfc containment gate read it (`o2control.containment`,
    docs/WORK_ORDER_corp_twin.md §11 step 2). Deploy-scoped, like
    `inventory`/`scorecard`: there is no egress phase before a deploy."""
    p = group.add_parser(
        "egress",
        help="Read (--show) or flip (--phase) a deployed twin's live egress phase",
    )
    p.add_argument("target", nargs="?", metavar="[ID|TWIN]",
                   help="Deploy id or twin/scenario name (default: most recent te deploy)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true",
                      help="Read the router's live phase + healthcheck and record it")
    mode.add_argument("--phase", choices=["measure", "provision"], default=None,
                      help="Flip the router to this phase, then read back and record it")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--json", action="store_true", help="Emit the result as JSON")
    p.set_defaults(tier="te", verb="egress")
    return p


def _add_te_scorecard(group):
    """`te scorecard [ID]` — the golden-twin fidelity scorecard
    (`docs/design/13-golden-twin-fidelity.md` §6 "certify",
    `docs/WORK_ORDER_fidelity_scorecard.md` §4.2).

    A standalone verb outside the six-verb grid, for the SAME reason
    `inventory` is one: it is always deploy-scoped (a scorecard certifies a
    DEPLOYED twin against the cast bundle it came from; there is nothing to
    certify about an undeployed `scenario.yaml`).
    """
    p = group.add_parser(
        "scorecard",
        help="Certify a deployed twin against the golden-twin fidelity spec",
    )
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Deploy id (default: most recent te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ (resolves each host's os)")
    p.add_argument(
        "--reference", default=None, metavar="DIR",
        help=(
            "Cast bundle directory (default: ./twins/<scenario-id>, when it carries "
            "a cast's generated/ export). "
            "PINS which catalog answers — never a first-hit chain."
        ),
    )
    p.add_argument("--out", default=None, metavar="PATH",
                   help="Write the scorecard JSON here (default: <bundle>/"
                        "<id>.scorecard.<deploy-id>.json)")
    p.add_argument("--no-write", action="store_true",
                   help="Compute the scorecard without writing the artifact")
    p.add_argument("--no-probe", action="store_true",
                   help="Skip the in-guest probe (row 5 then reads not-computed "
                        "and the verdict is `incomplete`, never `not-golden`)")
    p.add_argument("--include-deferred", action="store_true",
                   help="Also run row 4's in-guest `ss -lnH -tu` (reported; "
                        "never enters the gate set)")
    p.add_argument("--attest-visual", default=None, metavar="NOTE",
                   help="Record an operator attestation for row 11 (visual). "
                        "Never inferred — an unmade attestation reads "
                        "`not-attested`, never `pass`.")
    p.add_argument("--fail-on-verdict", action="store_true",
                   help="Exit 3 on `not-golden` and 4 on `incomplete` so CI can "
                        "gate. Default is exit 0 whenever a scorecard was "
                        "produced — the verdict is data, not a tool failure.")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--json", action="store_true",
                   help="Emit the scorecard JSON on stdout and nothing else")
    p.set_defaults(tier="te", verb="scorecard")
    return p


# ---------------------------------------------------------------------------
#  o2 <verb> builders. `legacy_surface_flag`/`legacy_deploy_id_flag` reuse the
#  SAME builder for the DEPRECATED `up`/`down` shims by pointing an
#  old-spelled flag at the SAME dest the canonical positional uses
#  (`dest="spec"`/`dest="deploy_id"`) — no separate translation layer needed.
# ---------------------------------------------------------------------------
def _add_o2_validate(group):
    p = group.add_parser("validate", help="Validate the composed O2 surface IR")
    p.add_argument("spec", nargs="?", metavar="surface.yaml",
                   help="Surface manifest YAML (default: one primary tcpdump-sensor)")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/ (default: cwd)")
    p.set_defaults(tier="o2", verb="validate", command_label="o2 validate")
    return p


def _add_o2_plan(group):
    p = group.add_parser(
        "plan", help="Build the O2 surface's deploy plan (DAG) without executing it"
    )
    p.add_argument("spec", nargs="?", metavar="surface.yaml",
                   help="Surface manifest YAML (default: one primary tcpdump-sensor)")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/ (default: cwd)")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.set_defaults(tier="o2", verb="plan", command_label="o2 plan")
    return p


def _add_o2_deploy(group, *, verb_name="deploy", legacy_surface_flag=False,
                   deprecated: Optional[str] = None):
    p = group.add_parser(
        verb_name,
        help="Stand up the O2 surface (idempotent — adopts a prior standing deploy)",
    )
    if legacy_surface_flag:
        p.add_argument("--surface", dest="spec", default=None,
                       help="Surface manifest YAML (default: one primary tcpdump-sensor)")
    else:
        p.add_argument("spec", nargs="?", metavar="surface.yaml",
                       help="Surface manifest YAML (default: one primary tcpdump-sensor)")
    p.add_argument("--images", default=None,
                   help="Root containing images/manifests/ + images/bindings/ (default: cwd)")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.set_defaults(tier="o2", verb="deploy", deprecated_notice=deprecated)
    return p


def _add_o2_status(group):
    p = group.add_parser("status", help="Show the standing O2 surface's inventory")
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Surface deploy id (default: the most recent o2-tier deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="o2", verb="status")
    return p


def _add_o2_info(group):
    p = group.add_parser(
        "info", help="Show the O2 surface's resolved config + observability endpoints"
    )
    p.add_argument(
        "id_or_spec", nargs="?", metavar="[ID|surface.yaml]",
        help=("Deploy id, OR a surface manifest YAML (config-only if not applied); "
              "default: the standing surface"),
    )
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (for the O2_* observability endpoints)")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.add_argument("--no-secrets", action="store_true",
                   help="Mask external-sensor access passwords (********) for "
                        "screen-sharing; --json always includes the real values")
    p.set_defaults(tier="o2", verb="info")
    return p


def _add_o2_destroy(group, *, verb_name="destroy", legacy_deploy_id_flag=False,
                    deprecated: Optional[str] = None):
    p = group.add_parser(
        verb_name, help="Tear down the standing O2 surface (--allow-destroy required)"
    )
    if legacy_deploy_id_flag:
        p.add_argument("--deploy-id", dest="deploy_id", default=None,
                       help="Surface deploy id (default: the most recent o2-tier deploy)")
    else:
        p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                       help="Surface deploy id (default: the most recent o2-tier deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (teardown REST/SSH creds)")
    p.add_argument("--node", default=None,
                   help="Override the PVE node for node-scoped teardown ops")
    p.add_argument("--allow-destroy", action="store_true",
                   help="Required confirmation: actually remove resources")
    p.set_defaults(tier="o2", verb="destroy", deprecated_notice=deprecated)
    return p


# ---------------------------------------------------------------------------
#  tap <verb> builders (the A2 tap-config playout). `legacy_plan_flag`/
#  `legacy_flags` reuse the SAME builder for the DEPRECATED `o2 tap
#  apply|destroy` shims, same dest-pointing trick as the o2 builders above.
# ---------------------------------------------------------------------------
def _add_twin_flags(p) -> None:
    """`--twin ID [--variant NAME] [--twins-dir DIR]` -- pick a tap/tfc file
    through a `twins/<id>/twin.yaml` manifest instead of naming it
    (`docs/WORK_ORDER_twin_consolidation.md` §2, `docs/WORK_ORDER_corp_twin.md`
    S0). The positional still works; `_resolve_twin_tier_spec` below turns
    `--twin` into that positional before the unchanged handler runs, the
    same way `--config` becomes a scenario.yaml (LES-037)."""
    p.add_argument("--twin", metavar="TWIN-ID", default=None,
                   help="Use this tier's file from twins/<id>/twin.yaml instead of a path")
    p.add_argument("--variant", default=None,
                   help="With --twin: a declared variant (file stem) instead of the default")
    p.add_argument("--twins-dir", default=None,
                   help=("Root holding twin folders for --twin (default: the repo "
                         "root's twins/ -- not relative to the current directory)"))


def _add_allow_uncontained_flag(p) -> None:
    """`tap deploy`/`tfc deploy` refuse a target whose router reads back as
    NOT contained (`_containment_gate`); this is the deliberate override."""
    p.add_argument(
        "--allow-uncontained", action="store_true",
        help=("Run even though the target twin's egress phase reads back as not "
              "'measure' (real internet egress) -- see `axrange te info <deploy>`"),
    )


def _add_tap_validate(group):
    p = group.add_parser(
        "validate",
        help="Validate a tap-plan (static always; resolve when the state DB is reachable)",
    )
    p.add_argument("spec", nargs="?", metavar="tap-plan.yaml",
                   help="Tap-plan YAML file (or use --twin)")
    _add_twin_flags(p)
    p.add_argument("--target", default=None,
                   help="Override the plan's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy)")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (for config-declared EXTERNAL_SENSORS)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.set_defaults(tier="tap", verb="validate")
    return p


def _add_tap_plan(group):
    p = group.add_parser(
        "plan", help="Compose a tap-plan's OP-MIR ops without executing them"
    )
    p.add_argument("spec", nargs="?", metavar="tap-plan.yaml",
                   help="Tap-plan YAML file (or use --twin)")
    _add_twin_flags(p)
    p.add_argument("--target", default=None,
                   help="Override the plan's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy)")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.set_defaults(tier="tap", verb="plan")
    return p


def _add_tap_deploy(group, *, verb_name="deploy", legacy_plan_flag=False,
                    deprecated: Optional[str] = None):
    p = group.add_parser(
        verb_name, help="Apply a tap-plan (idempotent -- re-apply re-points)"
    )
    if legacy_plan_flag:
        p.add_argument("--plan", dest="spec", required=True, help="Tap-plan YAML file")
    else:
        p.add_argument("spec", nargs="?", metavar="tap-plan.yaml",
                       help="Tap-plan YAML file (or use --twin)")
        # Not on the DEPRECATED `o2 tap apply` shim: new flags go on the
        # current verb only.
        _add_twin_flags(p)
    _add_allow_uncontained_flag(p)
    p.add_argument("--target", default=None,
                   help="Override the plan's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy) "
                        "-- required if the plan itself declares none")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.set_defaults(tier="tap", verb="deploy", deprecated_notice=deprecated)
    return p


def _add_tap_status(group):
    p = group.add_parser("status", help="Show an applied tap-plan's bindings")
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Tap-plan deploy id (default: most recent tap-plan deploy)")
    p.add_argument("--target", default=None,
                   help="Alternative to [ID]: the target TE's deploy id")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="tap", verb="status")
    return p


def _add_tap_info(group):
    p = group.add_parser(
        "info",
        help="Show a tap-plan's resolved bindings + GRE keys + sensor transport IPs",
    )
    p.add_argument(
        "id_or_spec", nargs="?", metavar="[ID|tap-plan.yaml]",
        help=("Tap-plan deploy id, OR a tap-plan YAML (resolved-but-not-applied if "
              "unapplied); default: most recent tap-plan deploy"),
    )
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (for config-declared EXTERNAL_SENSORS)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.add_argument("--no-secrets", action="store_true",
                   help="Mask external-sensor access passwords (********) for "
                        "screen-sharing; --json always includes the real values")
    p.set_defaults(tier="tap", verb="info")
    return p


def _add_tap_destroy(group, *, verb_name="destroy", legacy_flags=False,
                     deprecated: Optional[str] = None):
    p = group.add_parser(
        verb_name,
        help="Tear down a tap-plan's tap_-tier resources (--allow-destroy required)",
    )
    if legacy_flags:
        p.add_argument("--target", default=None,
                       help="Target TE deploy id (the tap-plan bound to it)")
        p.add_argument("--deploy-id", dest="deploy_id", default=None,
                       help="Explicit tap-plan deploy id (alternative to --target)")
    else:
        p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                       help="Tap-plan deploy id (default: most recent tap-plan deploy)")
        p.add_argument("--target", default=None,
                       help="Alternative to [ID]: the target TE's deploy id")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (teardown REST/SSH creds)")
    p.add_argument("--node", default=None,
                   help="Override the PVE node for node-scoped teardown ops")
    p.add_argument("--allow-destroy", action="store_true",
                   help="Required confirmation: actually remove resources")
    p.set_defaults(tier="tap", verb="destroy", deprecated_notice=deprecated)
    return p


# ---------------------------------------------------------------------------
#  tfc <verb> builders (the Traffic tier, `docs/WORK_ORDER_traffic_tier.md`).
#  Fits the SAME grid shape `tap` uses -- validate/plan/deploy take a
#  `traffic-profile.yaml` positional; status/info/destroy take an optional
#  `[ID]` positional with a `--target` alternative (the target twin's deploy
#  id) -- `tfc` IS a real `_VALID_TIERS` deploy tier (unlike `tap`), so no
#  legacy-flag/deprecated-shim variants are needed here.
# ---------------------------------------------------------------------------
def _add_tfc_validate(group):
    p = group.add_parser(
        "validate",
        help="Validate a traffic profile (static always; resolve when the state DB is reachable)",
    )
    p.add_argument("spec", nargs="?", metavar="traffic-profile.yaml",
                   help="Traffic-profile YAML file (or use --twin)")
    _add_twin_flags(p)
    p.add_argument("--target", default=None,
                   help="Override the profile's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--images", default=None,
                   help=("Root containing images/manifests/ (the one te deploy used) -- "
                         "resolves each host's OS for the persona os_family check"))
    p.set_defaults(tier="tfc", verb="validate")
    return p


def _add_tfc_plan(group):
    p = group.add_parser(
        "plan", help="Compose a traffic profile's drive-persona ops without executing them"
    )
    p.add_argument("spec", nargs="?", metavar="traffic-profile.yaml",
                   help="Traffic-profile YAML file (or use --twin)")
    _add_twin_flags(p)
    p.add_argument("--target", default=None,
                   help="Override the profile's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy)")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--images", default=None,
                   help=("Root containing images/manifests/ (the one te deploy used) -- "
                         "resolves each host's OS for the persona os_family check"))
    p.set_defaults(tier="tfc", verb="plan")
    return p


def _add_tfc_deploy(group):
    p = group.add_parser(
        "deploy", help="Apply a traffic profile (idempotent -- re-apply re-asserts)"
    )
    p.add_argument("spec", nargs="?", metavar="traffic-profile.yaml",
                   help="Traffic-profile YAML file (or use --twin)")
    _add_twin_flags(p)
    _add_allow_uncontained_flag(p)
    p.add_argument("--target", default=None,
                   help="Override the profile's target: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy) "
                        "-- required if the profile itself declares none")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--images", default=None,
                   help=("Root containing images/manifests/ (the one te deploy used) -- "
                         "resolves each host's OS for the persona os_family check"))
    p.add_argument("--revert-host-edits", action="store_true",
                   help="Confirm that this profile may revert per-host persona edits "
                        "(`tfc set-host`); without it such an apply is refused, naming "
                        "each host")
    p.set_defaults(tier="tfc", verb="deploy")
    return p


def _add_tfc_set_host(group):
    p = group.add_parser(
        "set-host",
        help="Change ONE host's personas (and optionally role) on a standing twin; "
             "every other host's personas keep running untouched",
    )
    p.add_argument("target", help="The twin: a te deploy id, or a twin name "
                                  "(its newest live te deploy)")
    p.add_argument("host", help="The twin host id to edit")
    p.add_argument("--persona", action="append", default=[], metavar="P",
                   help="A persona for this host (repeatable). The list is the host's "
                        "COMPLETE set: personas not named are stopped")
    p.add_argument("--no-personas", action="store_true",
                   help="Stop every persona on this host (instead of --persona)")
    p.add_argument("--schedule", default=None,
                   help="Schedule override for every --persona (24x7 | business-hours)")
    role = p.add_mutually_exclusive_group()
    role.add_argument("--role", default=None, help="Set this host's tfc role")
    role.add_argument("--clear-role", action="store_true",
                      help="Drop this host's tfc role (back to the twin baseline)")
    p.add_argument("--update-dependents", action="store_true",
                   help="Also re-drive other hosts whose peer picks the role change "
                        "invalidates (listed by --plan)")
    p.add_argument("--allow-role-orphaning", action="store_true",
                   help="Allow a role change that leaves a role other running personas "
                        "hard-need with no holder (refused otherwise)")
    p.add_argument("--plan", action="store_true",
                   help="Preview only: what would start, stop and stay untouched")
    _add_allow_uncontained_flag(p)
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--images", default=None,
                   help=("Root containing images/manifests/ (the one te deploy used) -- "
                         "resolves each host's OS for the persona os_family check"))
    p.add_argument("--twins-dir", default=None,
                   help="Twin catalog root(s) for the twin's local personas/ "
                        "(default: ./twins)")
    p.add_argument("--json", action="store_true", help="Emit the plan/result as JSON")
    p.set_defaults(tier="tfc", verb="set-host")
    return p


def _add_tfc_status(group):
    p = group.add_parser("status", help="Show an applied traffic profile's assignments")
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Traffic-profile deploy id (default: most recent tfc deploy)")
    p.add_argument("--target", default=None,
                   help="Alternative to [ID]: the target twin's deploy id")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="tfc", verb="status")
    return p


def _add_tfc_info(group):
    p = group.add_parser(
        "info", help="Show a traffic profile's resolved assignments + peers + vmids",
    )
    p.add_argument(
        "id_or_spec", nargs="?", metavar="[ID|traffic-profile.yaml]",
        help=("Traffic-profile deploy id, OR a profile YAML (resolved-but-not-applied "
              "if unapplied); default: most recent tfc deploy"),
    )
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="tfc", verb="info")
    return p


def _add_tfc_destroy(group):
    p = group.add_parser(
        "destroy",
        help="Tear down a traffic profile's tfc_-tier resources (--allow-destroy required)",
    )
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Traffic-profile deploy id (default: most recent tfc deploy)")
    p.add_argument("--target", default=None,
                   help="Alternative to [ID]: the target twin's deploy id")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (teardown REST/SSH creds)")
    p.add_argument("--node", default=None,
                   help="Override the PVE node for node-scoped teardown ops")
    p.add_argument("--allow-destroy", action="store_true",
                   help="Required confirmation: actually remove resources")
    p.set_defaults(tier="tfc", verb="destroy")
    return p


# ---------------------------------------------------------------------------
#  exr <verb> builders (the Exercise tier, `docs/WORK_ORDER_exercise_tier.md`).
#  A DELIBERATELY NARROWER grid than `tap`/`tfc`: `{run|deploy, status,
#  destroy}` only -- no `validate`/`plan`/`info`. An exercise-spec is a plain
#  YAML an operator reads directly (no static-lint value `tap validate`/`tfc
#  validate` add for a tap-plan/traffic-profile's richer selector/persona
#  resolution), and there is no meaningfully separate "resolved but not
#  applied" view worth an `info` verb beyond what `status` already reports
#  (every apply is a fresh, ephemeral deploy -- there's no standing binding to
#  preview before committing). `run` is a FIRST-CLASS alias of `deploy` (not
#  a deprecated shim -- both spellings are current), matching the work
#  order's own `axrange exr run <twin> [--spec FILE]` phrasing while still
#  fitting the shared grid's argparse-dispatch shape (`set_defaults(tier=
#  "exr", verb="deploy")` for both).
# ---------------------------------------------------------------------------
def _add_exr_deploy(group, *, verb_name="deploy"):
    p = group.add_parser(
        verb_name,
        help="Run an exercise (attack playout) against an already-deployed twin",
    )
    p.add_argument("spec", metavar="exercise-spec.yaml", help="Exercise-spec YAML file")
    p.add_argument("--target", default=None,
                   help="Override the spec's twin: (a deploy id, or a scenario/"
                        "twin-config name resolved to its newest live te deploy) "
                        "-- required if the spec itself declares none")
    p.add_argument("--adapter-config", default=None, help="Path to adapter.conf")
    p.add_argument("--node", default=None, help="Override DEFAULT_NODE from adapter.conf")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.set_defaults(tier="exr", verb="deploy")
    return p


def _add_exr_status(group):
    p = group.add_parser("status", help="Show an exercise run's status + playout hash")
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Exercise deploy id (default: most recent exr deploy)")
    p.add_argument("--target", default=None,
                   help="Alternative to [ID]: show the latest exr run against this twin")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--json", action="store_true",
                   help="Emit a stable JSON shape instead of a human table")
    p.set_defaults(tier="exr", verb="status")
    return p


def _add_exr_destroy(group):
    p = group.add_parser(
        "destroy",
        help="Tear down one exercise run's exr_-tier resources (--allow-destroy required)",
    )
    p.add_argument("deploy_id", nargs="?", metavar="[ID]",
                   help="Exercise deploy id (default: most recent exr deploy)")
    p.add_argument("--target", default=None,
                   help="Alternative to [ID]: tear down the latest exr run against this twin")
    p.add_argument("--db", default=_DEFAULT_DB_PATH,
                   help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p.add_argument("--adapter-config", default=None,
                   help="Path to adapter.conf (teardown REST/SSH creds)")
    p.add_argument("--node", default=None,
                   help="Override the PVE node for node-scoped teardown ops")
    p.add_argument("--allow-destroy", action="store_true",
                   help="Required confirmation: actually remove resources")
    p.set_defaults(tier="exr", verb="destroy")
    return p


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="axrange",
        description=(
            "axRange CLI: te / o2 / tap / tfc deploy lifecycles (validate, "
            "plan, deploy, status, info, destroy), plus list-deploys / serve "
            "/ image build."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"axrange {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- te (target-environment deploy lifecycle) ---
    p_te = sub.add_parser(
        "te", help="Target-environment deploy lifecycle (validate/plan/deploy/status/info/destroy/inventory)"
    )
    te_sub = p_te.add_subparsers(dest="te_verb", required=True)
    _add_te_validate(te_sub)
    _add_te_plan(te_sub)
    _add_te_deploy(te_sub)
    _add_te_status(te_sub)
    _add_te_info(te_sub)
    _add_te_destroy(te_sub)
    _add_te_inventory(te_sub)
    _add_te_scorecard(te_sub)
    _add_te_egress(te_sub)

    # --- o2 (the standing O2 surface — sensors + transport vnet) ---
    p_o2 = sub.add_parser(
        "o2", help="Standing O2 surface lifecycle (validate/plan/deploy/status/info/destroy)"
    )
    o2_sub = p_o2.add_subparsers(dest="o2_verb", required=True)
    _add_o2_validate(o2_sub)
    _add_o2_plan(o2_sub)
    _add_o2_deploy(o2_sub)
    _add_o2_status(o2_sub)
    _add_o2_info(o2_sub)
    _add_o2_destroy(o2_sub)
    # DEPRECATED shim -- remove after the next release cycle (work order §3.5).
    _add_o2_deploy(
        o2_sub, verb_name="up", legacy_surface_flag=True,
        deprecated="note: 'axrange o2 up' is deprecated; use 'axrange o2 deploy' (removed next release)",
    )
    # DEPRECATED shim -- remove after the next release cycle.
    _add_o2_destroy(
        o2_sub, verb_name="down", legacy_deploy_id_flag=True,
        deprecated="note: 'axrange o2 down' is deprecated; use 'axrange o2 destroy' (removed next release)",
    )
    # DEPRECATED shim -- remove after the next release cycle. Dispatches to the
    # SAME `tap deploy`/`tap destroy` handlers the top-level `tap` group uses
    # (the leaf parsers below set tier="tap", not "o2").
    p_o2_tap = o2_sub.add_parser(
        "tap", help="[deprecated] use `axrange tap deploy|destroy`"
    )
    o2tap_sub = p_o2_tap.add_subparsers(dest="o2_tap_verb", required=True)
    _add_tap_deploy(
        o2tap_sub, verb_name="apply", legacy_plan_flag=True,
        deprecated="note: 'axrange o2 tap apply' is deprecated; use 'axrange tap deploy' (removed next release)",
    )
    _add_tap_destroy(
        o2tap_sub, verb_name="destroy", legacy_flags=True,
        deprecated="note: 'axrange o2 tap destroy' is deprecated; use 'axrange tap destroy' (removed next release)",
    )

    # --- tap (A2 tap-config playout) ---
    p_tap = sub.add_parser(
        "tap", help="Tap-plan lifecycle: bind a deployed te's taps to standing O2 sensors"
    )
    tap_sub = p_tap.add_subparsers(dest="tap_verb", required=True)
    _add_tap_validate(tap_sub)
    _add_tap_plan(tap_sub)
    _add_tap_deploy(tap_sub)
    _add_tap_status(tap_sub)
    _add_tap_info(tap_sub)
    _add_tap_destroy(tap_sub)

    # --- tfc (the Traffic tier -- docs/WORK_ORDER_traffic_tier.md) ---
    p_tfc = sub.add_parser(
        "tfc",
        help="Traffic-profile lifecycle: bind personas onto a deployed twin's host roles",
    )
    tfc_sub = p_tfc.add_subparsers(dest="tfc_verb", required=True)
    _add_tfc_validate(tfc_sub)
    _add_tfc_plan(tfc_sub)
    _add_tfc_deploy(tfc_sub)
    _add_tfc_status(tfc_sub)
    _add_tfc_info(tfc_sub)
    _add_tfc_destroy(tfc_sub)
    _add_tfc_set_host(tfc_sub)

    # --- exr (the Exercise tier -- docs/WORK_ORDER_exercise_tier.md) ---
    # Deliberately narrower grid than tap/tfc: {run|deploy, status, destroy}
    # only -- see `_add_exr_deploy`'s comment for why.
    p_exr = sub.add_parser(
        "exr",
        help="Exercise lifecycle: run an attack playout against an already-deployed twin",
    )
    exr_sub = p_exr.add_subparsers(dest="exr_verb", required=True)
    _add_exr_deploy(exr_sub, verb_name="deploy")
    _add_exr_deploy(exr_sub, verb_name="run")  # first-class alias, not a deprecated shim
    _add_exr_status(exr_sub)
    _add_exr_destroy(exr_sub)

    # --- list-deploys (cross-cutting; stays outside the grid) ---
    p_list = sub.add_parser(
        "list-deploys",
        help=(
            "List recent deploys from the state store (most-recently-"
            "UPDATED first, so a just-destroyed deploy can lead the list)"
        ),
    )
    p_list.add_argument("--db", default=_DEFAULT_DB_PATH,
                        help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--tier", choices=("te", "o2", "tap", "tfc", "exr"), default=None,
                        help="Filter to one resource-ownership tier (default: all)")

    # --- serve (control-plane API) ---
    p_serve = sub.add_parser(
        "serve",
        help="Run the control-plane API (FastAPI/uvicorn); optionally co-serve the SPA",
    )
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="Bind address (default: 127.0.0.1; use 0.0.0.0 to expose)")
    p_serve.add_argument("--port", type=int, default=8080,
                         help="Bind port (default: 8080)")
    p_serve.add_argument("--db", default=_DEFAULT_DB_PATH,
                         help=f"State-store DB path (default: {_DEFAULT_DB_PATH})")
    p_serve.add_argument("--scenarios-dir", default=None,
                         help="Directory of scenario.yaml files the API can list/resolve")
    p_serve.add_argument("--images", default=None,
                         help="Images root (manifests + bindings); per-scenario default otherwise")
    p_serve.add_argument("--adapter-config", default=None,
                         help="Path to adapter.conf (plan/deploy/targets need this)")
    p_serve.add_argument("--api-token", default=None,
                         help="Bearer token required for write endpoints (deploy/run/destroy)")
    p_serve.add_argument("--spa-dir", default=None,
                         help="Built SPA directory to co-serve at / (Phase 4; headless if unset)")
    p_serve.add_argument("--twins-dir", "--twin-configs-dir", dest="twin_configs_dir",
                         default=None,
                         help="Twin-config catalog directory the API can list/resolve "
                              "(default: ./twins after the legacy ./twin-configs; "
                              "--twin-configs-dir is the old spelling; docs/WORK_ORDER_"
                              "console_selector_and_stale_deploys.md)")
    p_serve.add_argument("--targets", dest="targets_file", default=None,
                         help="Cluster targets file (YAML: targets: [{name, adapter_config, "
                              "db, nodes?}], first = default; env AXRANGE_TARGETS). Unset: "
                              "one target from --adapter-config/--db, named after its "
                              "DEFAULT_NODE (config/adapter.targets.example.yaml)")
    p_serve.add_argument("--extra-db", dest="extra_dbs", action="append", default=None,
                         metavar="PATH",
                         help="Another state store recording deploys on the SAME cluster "
                              "(repeatable; env AXRANGE_EXTRA_DBS, os.pathsep-separated). "
                              "Reads search every store; new deploys go to --db. With "
                              "--targets, use the file's extra_dbs: instead")
    p_serve.add_argument("--imprints-dir", default=None,
                         help="Imprint/cast-bundle catalog directory the Console reads "
                              "(default: the packaged Track-C fixtures; point it at "
                              "./twins for the real cast bundles; "
                              "docs/WORK_ORDER_console_canvas.md S1.8)")

    # --- image (build pipeline) ---
    p_image = sub.add_parser(
        "image", help="Image catalog operations (build templates)"
    )
    image_sub = p_image.add_subparsers(dest="image_command", required=True)
    p_build = image_sub.add_parser(
        "build",
        help="Build a Proxmox template for <image> from its upstream cloud image",
    )
    p_build.add_argument("image", help="Image id (e.g. debian-12-cloud)")
    p_build.add_argument("--images", default=None,
                         help="Root containing images/manifests/ + images/bindings/ (default: cwd)")
    p_build.add_argument("--adapter-config", default=None,
                         help="Path to adapter.conf (build creds: BUILD_* keys)")
    p_build.add_argument("--node", default=None,
                         help="Override BUILD_NODE / DEFAULT_NODE (build target node)")
    p_build.add_argument("--version", default=None,
                         help="Binding/manifest version id to build (default: current)")
    p_build.add_argument("--build-vmid", type=int, default=None,
                         help="Explicit build VMID (default: /cluster/nextid)")
    p_build.add_argument("--refresh-cache", action="store_true",
                         help="Re-download the base cloud image even if cached")
    p_build.add_argument("--ignore-conformance", action="store_true",
                         help="Proceed past failed probes / smoke-test (debugging only)")
    p_build.add_argument("--keep-build-vm", action="store_true",
                         help="Don't destroy the transient build VM on failure")
    p_build.add_argument("--skip-smoke", action="store_true",
                         help="Skip the one-host deploy smoke-test (probes still run)")

    # --- twin-config (parameterized IR templates + starter catalog) ---
    # Outside the grid (like list-deploys/serve/image build): a twin-config
    # is a CATALOG lookup, not a deploy lifecycle of its own -- deploying one
    # goes through the existing `te {validate,plan,deploy}` grid via the
    # `--config`/`--param` flags `_add_config_flags` adds there
    # (docs/WORK_ORDER_twin_configs.md §2).
    p_twinconfig = sub.add_parser(
        "twin-config", help="Twin-config catalog (parameterized IR templates)"
    )
    twinconfig_sub = p_twinconfig.add_subparsers(dest="twin_config_command", required=True)
    p_tc_list = twinconfig_sub.add_parser("list", help="List the twin-config catalog")
    p_tc_list.add_argument("--twins-dir", "--twin-configs-dir", dest="twin_configs_dir",
                           default=None,
                           help="Catalog directory (default: ./twins after the legacy "
                                "./twin-configs)")
    p_tc_show = twinconfig_sub.add_parser("show", help="Show one twin-config's declared shape")
    p_tc_show.add_argument("id", metavar="TWIN-CONFIG-ID")
    p_tc_show.add_argument("--twins-dir", "--twin-configs-dir", dest="twin_configs_dir",
                           default=None,
                           help="Catalog directory (default: ./twins after the legacy "
                                "./twin-configs)")
    # `--param` here (work order D4, `docs/WORK_ORDER_deploy_gate.md`) is the
    # ONE genuinely new flag the deploy gate adds -- `show` becomes the
    # "preview a threshold before deploying it" verb
    # (`--param DEPLOY_MIN_OCCUPANCY=0.5`). Everything else about the gate
    # reuses flags that already shipped on `te {validate,plan,deploy}`.
    p_tc_show.add_argument(
        "--param", metavar="KEY=VALUE", action="append", default=None,
        help=("Override a twin-config param for the `deploy gate:` preview "
              "(repeatable), e.g. --param DEPLOY_MIN_OCCUPANCY=0.5"),
    )
    p_tc_show.add_argument("--json", action="store_true",
                           help="Emit a stable JSON shape instead of a human summary")

    # --- DEPRECATED bare top-level shims (work order §3.5) ---
    # Bare `validate`/`plan`/`deploy`/`status`/`destroy` → `te <verb>`. ONE
    # release cycle, then removed -- reuse the SAME `_add_te_*` builders as
    # the canonical `te` group (no copy-pasted subparsers), just added
    # directly to the ROOT `sub` group instead of under `te`.
    _add_te_validate(
        sub, deprecated="note: 'axrange validate' is deprecated; use 'axrange te validate' (removed next release)",
    )
    _add_te_plan(
        sub, deprecated="note: 'axrange plan' is deprecated; use 'axrange te plan' (removed next release)",
    )
    _add_te_deploy(
        sub, deprecated="note: 'axrange deploy' is deprecated; use 'axrange te deploy' (removed next release)",
    )
    _add_te_status(
        sub, deprecated="note: 'axrange status' is deprecated; use 'axrange te status' (removed next release)",
    )
    _add_te_destroy(
        sub, deprecated="note: 'axrange destroy' is deprecated; use 'axrange te destroy' (removed next release)",
    )

    return parser


# ===========================================================================
# validate (unchanged from prior slice)
# ===========================================================================


def _scenario_dir(args: argparse.Namespace) -> str:
    """The directory relative `script:`/asset paths resolve against.

    For a `--config` deploy that is the TWIN CONFIG's own directory, not the
    tempdir its expansion landed in (`_resolve_scenario_or_config` records it).
    """
    src = getattr(args, "scenario_source_dir", None)
    return src or str(Path(args.scenario).parent)


def _resolve_scenario_or_config(
    args: argparse.Namespace, label: str, *, quiet: bool = False,
) -> None:
    """`--config <id> [--param k=v ...]` -> expand the named twin-config
    (`docs/WORK_ORDER_twin_configs.md`) into a materialized IR file and
    OVERWRITE `args.scenario` with its path, so every downstream call
    (`cmd_validate`, `_load_planning_inputs`) proceeds exactly as if that
    path had been the original `scenario.yaml` positional — ONE resolution
    point, not a second validate/plan/deploy pipeline for `--config`
    (LES-037). A no-op if `--config` wasn't given (the ordinary positional
    path, unchanged). Raises `SystemExit(2)` on a bad `--config`/`--param`
    combination or an expansion failure, reported to stderr like every other
    failure in this module. `quiet=True` (LES-083: thread quiet through
    every branch a future `--json` caller might reach, not just the tested
    one) suppresses the one stdout progress line on the success path.

    Also defaults `--images` (review S4) when `--config` is used and
    `--images` wasn't given: the materialized IR always lands in a system
    tempdir (never co-located with a real `images/` catalog), so the
    ordinary `images_root = scenario_path.parent` default every OTHER
    caller relies on would always resolve to an empty tempdir and fail
    REF-120 on every node. Default instead to the `--twin-configs-dir`'s
    parent (or cwd, when using the default catalog dir) — the natural place
    a repo-root invocation's co-located `images/` catalog would live. An
    explicit `--images` always wins.
    """
    config_id = getattr(args, "config", None)
    if not config_id:
        if getattr(args, "param", None):
            sys.stderr.write(f"axrange {label}: --param only makes sense with --config\n")
            raise SystemExit(2)
        return
    if getattr(args, "scenario", None):
        sys.stderr.write(
            f"axrange {label}: specify either a scenario.yaml positional or "
            "--config, not both\n"
        )
        raise SystemExit(2)

    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.expand import materialize_twin_config
    from axrange.twinconfig.params import parse_param_flags

    if not getattr(args, "images", None):
        twin_configs_dir = getattr(args, "twin_configs_dir", None)
        args.images = str(
            Path(twin_configs_dir).resolve().parent if twin_configs_dir else Path.cwd()
        )

    config_warnings: list[str] = []
    try:
        overrides = parse_param_flags(getattr(args, "param", None))
        materialized = materialize_twin_config(
            config_id,
            catalog_dir=getattr(args, "twin_configs_dir", None),
            param_overrides=overrides,
            warnings=config_warnings,
        )
    except TwinConfigError as e:
        sys.stderr.write(f"axrange {label}: --config {config_id}: {e}\n")
        raise SystemExit(2)
    # Warnings are never DROPPED, only redirected: `quiet` exists to keep
    # stdout parseable (a `--json` caller), not to hide that a deploy gate
    # removed nodes (LES-083 + LES-089).
    for w in config_warnings:
        stream = sys.stdout if not quiet else sys.stderr
        stream.write(f"[warn] {w}\n")
    if not quiet:
        sys.stdout.write(
            f"axrange {label}: expanded twin-config '{config_id}' -> {materialized}\n"
        )
    args.scenario = str(materialized)
    # Where the CONFIG lives, not where its expansion landed. A relative
    # `install: [{script: ...}]` path is resolved against `scenario_dir`
    # (`pass1_emit._read_script`), and the expansion always lands in a system
    # tempdir -- so without this a twin config could not use a script install at
    # all: every path resolved to `/tmp/<script>` and failed to read. A
    # scenario.yaml deploy is unaffected (it keeps using its own parent).
    # ONE helper, shared with both API resolvers (GAP-082 review, M2): it
    # resolves the SAME file the expander just read (this used to re-scan the
    # catalog, which could pick a different file on an id collision), with
    # SYMLINKS RESOLVED -- a cast bundle can be a directory of links whose
    # `scripts/` live beside the link's target.
    try:
        from axrange.twinconfig.registry import twin_config_source_dir

        args.scenario_source_dir = str(twin_config_source_dir(
            config_id, catalog_dir=getattr(args, "twin_configs_dir", None),
        ))
    except Exception:
        # Best-effort: a config that cannot be re-resolved here must not break
        # a deploy that has already materialized successfully. `_scenario_dir`
        # then falls back to the expansion's own parent (the pre-fix behavior).
        pass


def cmd_validate(args: argparse.Namespace) -> int:
    if args.explain:
        return _do_explain(args.explain)
    if args.rules:
        return _do_list_rules()
    try:
        _resolve_scenario_or_config(args, "te validate")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if not args.scenario:
        sys.stderr.write("axrange validate: scenario path required (or --config)\n")
        return 2

    scenario_path = Path(args.scenario)
    if not scenario_path.is_file():
        sys.stderr.write(f"axrange validate: file not found: {scenario_path}\n")
        return 2

    images_root = Path(args.images) if args.images else scenario_path.parent
    try:
        manifests = load_registry(root=images_root)
    except ManifestError as e:
        sys.stderr.write(f"axrange validate: manifest load failure: {e}\n")
        return 2

    sys.stdout.write(
        f"Validating {scenario_path} against scenario_schema_version=1...\n\n"
    )
    result = validate_full(str(scenario_path), manifests=manifests)
    _render_result(result, verbose=args.verbose)
    return result.exit_code


def _render_result(result: ValidationResult, *, verbose: bool) -> None:
    for pass_no in (1, 2, 3):
        findings = result.by_pass(pass_no)
        if pass_no not in result.passes_run:
            sys.stdout.write(f"  - Pass {pass_no}: not reached (halted by prior pass)\n")
            continue
        symbol = "x" if any(f.severity == Severity.ERROR for f in findings) else (
            "!" if any(f.severity == Severity.WARNING for f in findings) else "o"
        )
        title = {
            1: "structural + includes",
            2: "reference integrity",
            3: "semantic (capability/lifecycle/network/overlay/activity/defense)",
        }[pass_no]
        count = len(findings)
        sys.stdout.write(f"  {symbol} Pass {pass_no} ({title}): {count} finding(s)\n")
    sys.stdout.write("\n")
    errors = result.errors
    warnings = result.warnings
    infos = result.infos
    for f in errors:
        _print_finding(f)
    for f in warnings:
        _print_finding(f)
    if verbose:
        for f in infos:
            _print_finding(f)
    if result.disposition == Disposition.PASS:
        sys.stdout.write("\nResult: PASS\n")
    elif result.disposition == Disposition.PASS_WITH_WARNINGS:
        sys.stdout.write(f"\nResult: PASS ({len(warnings)} warning(s))\n")
    else:
        sys.stdout.write(
            f"\nResult: FAIL ({len(errors)} error(s), {len(warnings)} warning(s))\n"
        )


def _print_finding(f: Finding) -> None:
    tag = {"error": "[error]", "warning": "[warn] ", "info": "[info] "}[
        f.severity.value
    ]
    sys.stdout.write(f"{f.rule_id} {tag} {f.message}\n")
    if f.location:
        sys.stdout.write(f"                 at {f.location}\n")
    if f.related:
        sys.stdout.write(f"                 -> see also: {', '.join(f.related)}\n")
    sys.stdout.write(f"                 -> explain: axrange te validate --explain {f.rule_id}\n")
    sys.stdout.write("\n")


def _do_explain(rule_id: str) -> int:
    try:
        rule = explain_rule(rule_id)
    except KeyError:
        sys.stderr.write(f"axrange: unknown rule '{rule_id}'\n")
        return 2
    sys.stdout.write(f"{rule.id} [{rule.severity.value}] {rule.title}\n")
    sys.stdout.write(f"category: {rule.category.value}\n")
    sys.stdout.write(f"pass:     {rule.pass_}\n")
    if rule.reproducible_promotes_to_error:
        sys.stdout.write("promotes-to-error under reproducible: true\n")
    sys.stdout.write("\n")
    if rule.description:
        sys.stdout.write(f"Description:\n  {rule.description}\n\n")
    if rule.rationale:
        sys.stdout.write(f"Rationale:\n  {rule.rationale}\n\n")
    if rule.message_template:
        sys.stdout.write(f"Message template:\n  {rule.message_template}\n\n")
    if rule.examples.fails:
        sys.stdout.write(f"Example (fails):\n{rule.examples.fails}\n\n")
    if rule.examples.passes:
        sys.stdout.write(f"Example (passes):\n{rule.examples.passes}\n\n")
    if rule.related:
        sys.stdout.write(f"Related: {', '.join(rule.related)}\n")
    return 0


def _do_list_rules() -> int:
    for rule in list_rules():
        sys.stdout.write(
            f"  {rule.id} [{rule.severity.value:7}] pass={rule.pass_} {rule.title}\n"
        )
    return 0


# ===========================================================================
# plan / deploy (new)
# ===========================================================================


def _load_planning_inputs(args: argparse.Namespace, *, quiet: bool = False):
    """Validate the scenario, load adapter config, and build a Plan.

    Returns (plan, manifests, ir, raw_ir, cfg, ctx). Raises SystemExit(2)
    via stderr on user-visible failures.

    The load-config / build-ClusterContext / parse-IR / build-Plan sequence
    (post-validation) is delegated to `axrange.api.service.build_plan_and_ctx`
    — the SAME engine-side helper `axrange/api/` uses (code-review finding #3,
    2026-07-07). This function keeps its own scenario-path resolution and
    validation-result rendering (the CLI's console-output shape is different
    enough from the API's DTO shape that unifying those too was judged
    higher-risk than the value; `build_plan_and_ctx`'s docstring has the same
    note).

    `quiet=True` (used by `cmd_te_info --json`, work order §3.2) suppresses
    the two progress lines on the SUCCESS path only -- a failure still prints
    its diagnostic (stderr, or `_render_result` on a validation failure),
    since only a clean success is followed by a bare JSON payload on stdout
    that a "Validating ..." line would otherwise contaminate.
    """
    from axrange.api.service import ApiError, build_plan_and_ctx

    # `command_label` (set via `set_defaults` on every te leaf parser, e.g.
    # "te plan"/"te deploy") is the canonical-form prefix for these
    # messages; a Namespace built by hand (older tests) without that
    # attribute falls back to the bare `.command` it *does* set, unchanged
    # from before this restructure.
    label = getattr(args, "command_label", None) or getattr(args, "command", "deploy")

    _resolve_scenario_or_config(args, label, quiet=quiet)
    if not args.scenario:
        sys.stderr.write(f"axrange {label}: scenario path required (or --config)\n")
        raise SystemExit(2)
    scenario_path = Path(args.scenario)
    if not scenario_path.is_file():
        sys.stderr.write(f"axrange {label}: file not found: {scenario_path}\n")
        raise SystemExit(2)

    images_root = Path(args.images) if args.images else scenario_path.parent
    try:
        manifests = load_registry(root=images_root)
    except ManifestError as e:
        sys.stderr.write(f"axrange {label}: manifest load failure: {e}\n")
        raise SystemExit(2)

    if not quiet:
        sys.stdout.write(f"Validating {scenario_path}...\n")
    result = validate_full(str(scenario_path), manifests=manifests)
    if result.exit_code != 0:
        _render_result(result, verbose=False)
        sys.stderr.write(
            f"axrange {label}: validation failed; refusing to plan\n"
        )
        raise SystemExit(result.exit_code)
    if not quiet:
        sys.stdout.write(f"  validate: {result.disposition.value}\n")

    try:
        r = build_plan_and_ctx(
            scenario_path, manifests, adapter_config=args.adapter_config,
            target_node=args.node, scenario_dir=_scenario_dir(args),
        )
    except ApiError as e:
        sys.stderr.write(f"axrange {label}: {e.message}\n")
        raise SystemExit(2)
    return r.plan, manifests, r.ir, r.raw, r.cfg, r.ctx


def cmd_plan(args: argparse.Namespace) -> int:
    plan, _manifests, _ir, _raw, _cfg, _ctx = _load_planning_inputs(args)
    sys.stdout.write(f"\nPlan: {plan.plan_id}\n")
    sys.stdout.write(
        f"  scenario={plan.scenario_id} version={plan.scenario_version} "
        f"ir_hash={plan.ir_hash[:8]}\n"
    )
    sys.stdout.write(
        f"  ops={len(plan.ops)} barriers={len(plan.barriers)} "
        f"edges={len(plan.edges)}\n\n"
    )
    sys.stdout.write(plan.render_tree() + "\n")
    return 0


def _diff_group_key(scope: dict[str, Any]) -> str:
    """A `host/h-fin`-style entity label for grouping an `--update` diff by
    `docs/PLANNER.md` §9.3's shape (code review S7) -- the axis an operator
    actually reasons about (`host/h-fin` regardless of whether the specific
    op ALSO scopes on `net`/`app`/`domain`), not one flat line per op.
    `host` wins when present (the dominant case for an add/remove-node
    update); `net` next (a whole segment add/remove); `scenario` next
    (create-sdn-zone/apply-sdn-config-style, once-per-plan ops); falls back
    to the first scope key alphabetically, or a literal plan-level label
    when scope is empty."""
    for key in ("host", "net", "scenario"):
        if key in scope:
            return f"{key}/{scope[key]}"
    if scope:
        k = sorted(scope.keys())[0]
        return f"{k}/{scope[k]}"
    return "(plan)"


def _print_update_diff(
    deploy_id: str, plan: Any, diff: Any, reapply_ops: Any = frozenset(),
) -> None:
    """`docs/PLANNER.md` §9.3's shape, reduced to what `docs/
    WORK_ORDER_twin_incremental_update.md` §3.1 actually classifies: counts
    of unchanged (+ retried), then each re-applied/added/removed ENTITY
    (grouped by `_diff_group_key`, op names indented underneath — code
    review S7, replacing a flat one-line-per-op listing a 30-host update
    would print ~300 of). Mutated entries never reach here EXCEPT via
    `reapply_ops` -- `resolve_deploy_plan` refuses on any mutated op the
    `--reapply` predicate did not cover, so anything still in the diff by
    this point is either non-mutated or an approved re-apply.

    `reapply_ops` (`docs/WORK_ORDER_responder_op_row_drift.md` option A)
    renders as §9.3's own `↺` rows, each carrying the op label and the
    old->new `script_content` FINGERPRINT (`plandiff.content_digest`), never
    the content: an operator confirming a non-idempotent redo needs to see
    WHICH ops and THAT the content genuinely changed, not two shell scripts
    scrolling past. This section is what the confirmation prompt below it is
    a confirmation OF, so it prints even under `--yes` (which skips the
    prompt, not the disclosure).

    `deploy_id` is printed in the header BEFORE any confirmation prompt the
    caller shows next (code review M5-i) -- when more than one standing
    deploy could plausibly be in play (`--fresh` while a prior one is still
    live, a failed partial, ...) the operator confirming a destructive
    removal must be told WHICH twin, not find out only after answering.

    A removed op whose name isn't in `teardown.SWEEPABLE_OP_NAMES` (code
    review S8) is annotated -- the sweep has no action for it at all (e.g.
    `provision-app`, `install-defense-agent`), so printing it as removed
    without saying so implies a revert that never happens. SUPPRESSED
    (SHOULD-FIX 5, code review) for a barrier (nothing FOR the sweep to
    ever have acted on in the first place) and for any op grouped under an
    entity whose own `clone-vm[$host]`/`create-vnet[$net]` is ALSO in
    `diff.removed` -- that is the DOMINANT case (removing a whole node or
    segment) and the previous wording was actively wrong for it: the VM/
    vnet itself is swept, so every subordinate op's in-guest/on-wire effect
    goes with it. Only the residual case -- some ops removed from an
    entity that ITSELF survives (e.g. one `provision-app` entry dropped
    from a still-live host) -- keeps the annotation, now worded to not
    imply a revert exists at all rather than claim a specific one.
    """
    from axrange.ops.registry import get_barrier
    from axrange.planner import dag
    from axrange.planner.plandiff import content_digest
    from axrange.planner.teardown import SWEEPABLE_OP_NAMES

    sys.stdout.write(
        f"\nPlan diff for {plan.scenario_id} — deploy {deploy_id} "
        f"(plan_id={plan.plan_id}):\n\n"
    )
    sys.stdout.write(f"  Unchanged: {len(diff.unchanged)} op(s) (will skip)\n")
    if diff.retry:
        sys.stdout.write(
            f"  Retrying: {len(diff.retry)} op(s) from a prior incomplete run\n"
        )
    if diff.is_noop:
        sys.stdout.write("  Nothing to add or remove -- already up to date.\n")
    sys.stdout.write("\n")

    if reapply_ops:
        sys.stdout.write(
            f"  Re-applying (--reapply): {len(reapply_ops)} op(s) whose "
            "content changed. These are NON-IDEMPOTENT scripts and will be "
            "re-executed in the guest:\n"
        )
        # Named apart from `_grouped`'s own `groups` below (a re-applied op
        # is always still IN the plan, so its scope comes straight off the
        # node rather than from `diff.scopes`, which only covers added/
        # removed ids).
        reapply_groups: dict[str, list[str]] = {}
        for op_id in sorted(reapply_ops):
            node = plan.get(op_id)
            scope = dict(node.scope) if node is not None else {}
            fields = diff.mutations.get(op_id, {})
            details = ", ".join(
                f"{field} {content_digest(old)} -> {content_digest(new)}"
                for field, (old, new) in sorted(fields.items())
            )
            label = dag.label_for_op_id(plan, op_id)
            reapply_groups.setdefault(_diff_group_key(scope), []).append(
                f"{label}  {details}"
            )
        for key in sorted(reapply_groups):
            sys.stdout.write(f"  ↺ {key}\n")
            for line in reapply_groups[key]:
                sys.stdout.write(f"      {line}\n")
        sys.stdout.write("\n")

    def _entity_own_op_removed(scope: dict[str, str]) -> bool:
        """True when `scope`'s owning entity's own foundational op --
        `clone-vm[$host]` for a host-scoped op, `create-vnet[$net]` for a
        net-scoped op -- is ITSELF in `diff.removed` (mirrors
        `_diff_group_key`'s own host-wins-over-net precedence)."""
        host = scope.get("host")
        if host is not None:
            return dag._op_id(plan.plan_id, "clone-vm", {"host": host}) in diff.removed
        net = scope.get("net")
        if net is not None:
            return dag._op_id(plan.plan_id, "create-vnet", {"net": net}) in diff.removed
        return False

    def _grouped(op_ids: Any, marker: str) -> None:
        groups: dict[str, list[str]] = {}
        for op_id in sorted(op_ids):
            label = dag.label_for_op_id(plan, op_id)
            scope = diff.scopes.get(op_id, {})
            name = diff.op_names.get(op_id)
            if (
                marker == "-"
                and name not in SWEEPABLE_OP_NAMES
                and get_barrier(name) is None
                and not _entity_own_op_removed(scope)
            ):
                label += "  (no separate teardown)"
            groups.setdefault(_diff_group_key(scope), []).append(label)
        for key in sorted(groups):
            sys.stdout.write(f"  {marker} {key}\n")
            for label in groups[key]:
                sys.stdout.write(f"      {label}\n")

    if diff.added:
        _grouped(diff.added, "+")
    if diff.removed:
        _grouped(diff.removed, "-")
    sys.stdout.write("\n")


def cmd_deploy(args: argparse.Namespace, *, confirm_fn: Callable[[str], str] = input) -> int:
    plan, manifests, ir, raw, cfg, ctx = _load_planning_inputs(args)

    # `--update`/`--allow-destroy`/`--yes` (docs/WORK_ORDER_
    # twin_incremental_update.md) -- `getattr(..., default)` so a hand-built
    # `argparse.Namespace` from an existing test (no `update=` attribute at
    # all) behaves exactly as before this feature existed.
    update = getattr(args, "update", False)
    allow_destroy = getattr(args, "allow_destroy", False)
    yes = getattr(args, "yes", False)
    reapply = getattr(args, "reapply", False)

    # S5 (code review): a flag that only means something WITH --update must
    # not be silently ignored without it -- matches this module's existing
    # `--param only makes sense with --config` precedent
    # (`_resolve_scenario_or_config`). Checked first, before --dry-run and
    # before a StateStore is even opened: a fast, side-effect-free refusal
    # for a combination that can never do anything.
    if not update:
        bad = next(
            (name for name, given in (
                ("--allow-destroy", allow_destroy),
                ("--reapply", reapply),
                ("--yes", yes),
            ) if given),
            None,
        )
        if bad is not None:
            sys.stderr.write(
                f"axrange te deploy: {bad} only makes sense with --update\n"
            )
            return 2

    if args.dry_run and not update:
        sys.stdout.write(plan.render_tree() + "\n")
        return 0

    from axrange.api.service import (
        ApiError, apply_update_tail, release_deploy_resources,
        resolve_deploy_plan, run_deploy_executor,
    )
    from axrange.platform import adapter_for_config
    from axrange.config_exec import ConfigBackend
    from axrange.planner import StateStore
    from axrange.planner.locks import Heartbeat
    from axrange.planner.plandiff import barriers_to_reevaluate, ops_to_reexecute
    from axrange.planner.state import DeployStatus, default_process_info
    from axrange.planner.teardown import TeardownError

    store = StateStore(args.db)

    # `resolve_deploy_plan` replaces the plain `find_deploy_for_plan` lookup
    # this used to be — it ALSO handles ordinary resume/`--fresh` (unchanged
    # behavior) alongside `--update`'s resolve-by-scenario_id/pin/diff/
    # refuse sequence (shared with the API's `create_deploy`, LES-037).
    try:
        resolution = resolve_deploy_plan(
            store, plan, ir=ir, raw=raw, manifests=manifests, ctx=ctx,
            scenario_dir=_scenario_dir(args), tier="te",
            fresh=args.fresh, update=update, allow_destroy=allow_destroy,
            reapply=reapply,
        )
    except ApiError as e:
        sys.stderr.write(f"axrange te deploy: {e.message}\n")
        store.close()
        return 2

    plan = resolution.plan
    existing = resolution.existing
    diff = resolution.diff
    reapply_ops = resolution.reapply_ops

    if existing is not None and existing.status == DeployStatus.COMPLETED and diff is None:
        sys.stdout.write(
            f"\nScenario already deployed: {existing.deploy_id}\n"
            f"  plan_id={plan.plan_id} status=completed.\n"
            f"  Use --fresh to start a brand-new deploy from scratch, or\n"
            f"  `axrange te status {existing.deploy_id}` to inspect.\n"
        )
        store.close()
        return 0

    if diff is not None:
        # `existing` is ALWAYS set here (`resolve_deploy_plan`'s `update:`
        # branch never returns a diff without it) -- deploy_id is resolved
        # BEFORE the diff/prompt below (M5-i: the operator confirming a
        # destructive removal must be told which twin, not find out only
        # after answering).
        _print_update_diff(existing.deploy_id, plan, diff, reapply_ops)
        if args.dry_run:
            # S6 (code review): --update's OWN preview -- resolve, print the
            # diff, touch nothing. The ordinary (no --update) --dry-run
            # already returned above, before a StateStore was even opened;
            # this is reached only once resolve_deploy_plan has done the
            # pin/diff/refuse work a plain `plan.render_tree()` can't.
            store.close()
            return 0
        # `reapply_ops` joins `diff.removed` as a confirmation trigger: a
        # `--reapply` re-executes a NON-idempotent operator script against a
        # standing twin (`docs/WORK_ORDER_responder_op_row_drift.md` §4
        # option A, "Against"), which is exactly the class of thing this
        # prompt exists for. `--yes` still skips it, same as for a removal.
        if (diff.removed or reapply_ops) and not yes:
            try:
                answer = confirm_fn("Apply? [y/N] ")
            except (EOFError, KeyboardInterrupt):
                # S9 (code review): non-interactive stdin (e.g. `--update
                # --allow-destroy` in CI with no `--yes`) must abort like an
                # explicit "n", not surface as an uncaught traceback.
                answer = "n"
            if (answer or "").strip().lower() != "y":
                sys.stdout.write("Aborted; nothing executed.\n")
                store.close()
                return 1

    deploy_id = existing.deploy_id if existing is not None else (
        f"{plan.scenario_id}-{secrets.token_hex(4)}"
    )

    # Single-writer gate (PLANNER §6.2): take the per-scenario lease before
    # touching any deploy state. A valid lease held by another live process —
    # a concurrent deploy of the same scenario — refuses us here. A crashed
    # holder's lease has expired and is reclaimed transparently.
    lease = store.acquire_lease(
        plan.scenario_id, deploy_id, process_info=default_process_info()
    )
    if not lease.acquired:
        sys.stdout.write(
            f"\nAnother deploy of scenario '{plan.scenario_id}' is in progress "
            f"(deploy_id={lease.held_by_deploy_id}).\n"
            f"  Wait for it to finish, or `axrange te status {lease.held_by_deploy_id}` "
            f"to inspect. A crashed holder's lease clears automatically after "
            f"its lease expires.\n"
        )
        store.close()
        return 1

    # Renew the lease + any held resource locks for the life of the deploy —
    # spans the removal sweep too (§4.4: `apply_update_tail` runs INSIDE this
    # same try, before the `finally`'s release, mirroring the 2026-07-29
    # cascade-ordering fix's "teardown work stays inside the heartbeat"
    # precedent).
    heartbeat = Heartbeat(
        str(args.db), deploy_id=deploy_id, scenario_id=plan.scenario_id
    )
    try:
        if existing is not None:
            n_reset = store.reset_incomplete_ops(deploy_id)
            if diff is not None:
                # A barrier whose predecessor set gained an Added op must be
                # re-evaluated even though its row may already be
                # `completed` (work order §3.3) -- `reset_incomplete_ops`
                # above only touches non-completed rows, so this is a
                # separate, explicit force-reset. Extended past barriers
                # (MUST-FIX 1, code review) to non-barrier AGGREGATE ops
                # (e.g. apply-sdn-config's bare-star `create-vnet[*]`/
                # `create-subnet[*]` after-clause): their own literal
                # `inputs_resolved` never mentions what they commit, so
                # diff_plan's field comparison alone leaves them
                # stale-`completed` forever even though a newly-Added
                # `create-vnet`/`create-subnet` needs them to run again.
                stale_barriers = barriers_to_reevaluate(plan, diff.added)
                stale_ops = ops_to_reexecute(plan, diff.added)
                # `--reapply`'s approved content-only redo set joins the
                # same force-reset (`docs/WORK_ORDER_responder_op_row_
                # drift.md` option A): those rows are `completed` carrying
                # the OLD `script_content`, so the resume short-circuit
                # would skip them otherwise. The executor refreshes each
                # row's `inputs_resolved_json` at attempt start
                # (`set_op_running`, GAP-044/LES-157) -- that refresh is
                # what makes the NEXT `--update` classify them Unchanged,
                # and it is also why a FAILED re-apply stays retryable: the
                # row ends `failed`, which `diff_plan` buckets as `retry`,
                # never `unchanged`.
                stale = stale_barriers | stale_ops | set(reapply_ops)
                if stale:
                    store.force_reset_ops(deploy_id, stale)
            store.update_deploy_status(deploy_id, DeployStatus.RUNNING)
            sys.stdout.write(
                f"\nResuming deploy: {deploy_id}\n"
                f"  plan_id={plan.plan_id} prior status={existing.status.value}; "
                f"{n_reset} op(s) reset to pending\n\n"
            )
        else:
            store.create_deploy(
                deploy_id=deploy_id,
                scenario_id=plan.scenario_id,
                scenario_version=plan.scenario_version,
                scenario_schema_version=1,
                plan_id=plan.plan_id,
                ir_hash=plan.ir_hash,
                placement=plan.placement,
            )
            sys.stdout.write(f"\nDeploy: {deploy_id}\n")
            sys.stdout.write(
                f"  plan_id={plan.plan_id} ops={len(plan.ops)} "
                f"barriers={len(plan.barriers)}\n\n"
            )

        adapter = adapter_for_config(cfg).adapter
        config_backend = ConfigBackend(cfg)
        # Shared with the API's background runner (code-review finding #3,
        # 2026-07-07): build+persist+run is identical between the two
        # surfaces; only the outer success/failure handling differs (the CLI
        # renders + returns an exit code in the foreground; the API swallows
        # a crash into state.db from a background thread).
        result = run_deploy_executor(
            store, adapter, config_backend, plan, ctx, deploy_id, heartbeat
        )
        _render_deploy_result(result, plan)

        if diff is not None and result.succeeded:
            # The rest of the --update apply: scoped removal sweep +
            # generation bookkeeping (ir_hash/placement, plan_id stays
            # pinned) + sibling-tier dangling-host warning (§3.2/§3.4/§3.5).
            # Only on a SUCCESSFUL added-ops pass -- a partially-failed one
            # must not also sweep removals; a retried --update redoes the
            # failed ops and only then reaches this.
            try:
                tail = apply_update_tail(
                    store, cfg, deploy_id, plan, diff, node=args.node,
                    log=lambda m: sys.stdout.write(m + "\n"),
                )
            except TeardownError as e:
                # S9 (code review): defense-in-depth -- `apply_update_tail`'s
                # own docstring only promises no raise for an ORDINARY per-
                # resource teardown failure, not this orchestration-level
                # refusal (practically unreachable for a te-tier deploy,
                # whose own tier is always recognized, but a bare traceback
                # here would still be worse than a clean message).
                sys.stderr.write(f"axrange te deploy: {e}\n")
                return 1
            if not tail.clean:
                # M3 (code review): a non-clean removal sweep (a protect-
                # guard refusal, a resource that wouldn't clear) must be
                # reported as incomplete, not a silent rc=0 — mirrors
                # `cmd_destroy`'s own "Result: INCOMPLETE" wording. Also
                # downgrade the PERSISTED row to FAILED (SHOULD-FIX 3, code
                # review) -- mirrors `_proxmox_runner`'s `post_run` branch
                # (`api/service.py`): the executor's OWN successful pass
                # already wrote COMPLETED, and leaving that stand would mean
                # a later `te status`/`GET /api/deploys/{id}` reports a
                # green twin while protect-guard-refused VMs are still
                # running — the terminal operator was told here, but the
                # persisted row silently disagreed.
                store.update_deploy_status(deploy_id, DeployStatus.FAILED)
                sys.stdout.write(
                    f"\nResult: INCOMPLETE — {len(tail.errors)} resource(s) "
                    "not cleared; re-run --update to finish (idempotent).\n"
                )
                return 1

        return 0 if result.succeeded else 1
    finally:
        # Stop heartbeating, then drop the deploy's locks + lease so the next
        # deploy of this scenario (or a contender for a shared resource) can
        # proceed immediately rather than waiting for expiry.
        release_deploy_resources(store, plan.scenario_id, deploy_id, heartbeat)
        store.close()


# ===========================================================================
# status / list-deploys
# ===========================================================================


def _print_json(obj: dict[str, Any]) -> None:
    """The one place every `--json` output goes through -- a plain
    `json.dumps` over an already-`dataclasses.asdict()`'d shape (work order
    §3.2: "define a small dataclass → dict per tier, don't hand-roll dict
    literals")."""
    import json

    sys.stdout.write(json.dumps(obj, indent=2) + "\n")


def _latest_te_deploy(store: Any, *, exclude_destroyed: bool = False) -> Any:
    """Most recent GENUINE `te`-tier deploy -- excludes a `tap-<te>` playout
    deploy, which is ALSO stamped `tier="te"` in the state store
    (`o2control.taps`'s own docstring explains why: `_VALID_TIERS` has no
    separate `"tap"` deploy-level tier). Filtering out the `tap-` plan_id
    prefix here keeps `te status`/`te info`/`te destroy`'s bare-arg default
    pointed at an actual target-environment deploy, not a tap-plan playout
    that happens to share the same tier column value (LES-053: unbounded,
    filter-scoped query, not a capped scan).

    `exclude_destroyed=True` -- `te destroy`'s bare-default ONLY (code
    review finding, docs/WORK_ORDER_cli_lifecycle.md): `mark_destroyed`
    bumps `updated_at`, so a just-destroyed deploy sorts as the newest te
    row -- without this, a bare `te destroy` right after destroying an
    OLDER deploy would resolve to that tombstone (a safe but misleading
    no-op "DESTROYED" while a genuinely live, newer te deploy goes
    untouched). `te status`/`te info`'s bare-default deliberately leaves
    this `False` -- they WANT the tombstone on top, to inspect what you
    just destroyed."""
    rows = store.deploys_matching(
        tier="te", exclude_plan_id_prefix="tap-",
        exclude_destroyed=exclude_destroyed, limit=1,
    )
    return rows[0] if rows else None


def cmd_status(args: argparse.Namespace) -> int:
    from axrange.planner import StateStore

    store = StateStore(args.db)
    deploy_id = args.deploy_id
    if not deploy_id:
        row = _latest_te_deploy(store)
        if row is None:
            sys.stdout.write("No deploys recorded.\n")
            store.close()
            return 0
        deploy_id = row.deploy_id

    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        sys.stderr.write(f"axrange status: deploy not found: {deploy_id}\n")
        store.close()
        return 2

    ops = store.ops_for_deploy(deploy.deploy_id)
    store.close()

    if getattr(args, "json", False):
        _print_json({
            "deploy_id": deploy.deploy_id,
            "scenario_id": deploy.scenario_id,
            "scenario_version": deploy.scenario_version,
            "status": deploy.status.value,
            "ops": [
                {"op_name": op.op_name, "kind": op.kind, "status": op.status.value}
                for op in ops
            ],
        })
        return 0

    sys.stdout.write(
        f"Deploy {deploy.deploy_id} (scenario={deploy.scenario_id} "
        f"v{deploy.scenario_version}) -- status={deploy.status.value}\n"
    )
    for op in ops:
        symbol = {
            "completed": "o",
            "running": "~",
            "failed": "x",
            "halted-due-to-upstream": "/",
            "pending": ".",
            "destroyed": "#",
        }.get(op.status.value, "?")
        sys.stdout.write(f"  {symbol} {op.op_name:30s} ({op.kind}) [{op.status.value}]\n")
    return 0


# ---------------------------------------------------------------------------
# te info (new verb, work order §3.2) -- a read-only projection over
# persisted op rows, generalizing the pattern
# `o2control.control._inventory_from_ops` pioneered (do NOT invent a new
# data plane). Two-tier: a bare/`[id]` call reads a live deploy's persisted
# ops (config + access facts); a `<scenario.yaml>` call reads a freshly
# DRY-built Plan's in-memory ops (config only) -- `axrange.planner.opview.
# op_fields` normalizes both shapes uniformly (LES-037).
#
# The dataclasses + derivation live in `axrange.planner.access` (moved
# 2026-07-26, `docs/WORK_ORDER_access_facts.md`'s API-parity follow-on,
# imported at module top) so `axrange.api.service`'s `GET /api/deploys/{id}/
# access` can call the SAME `te_info_from_ops`/`fill_access_facts` this CLI
# verb does, instead of a second implementation (LES-037, mirroring
# `axrange.planner.inventory`'s CLI/API split). Re-bound to the historical
# `_te_info_from_ops`/`_fill_access_facts` names so existing tests/call
# sites in this module are unaffected.
# ---------------------------------------------------------------------------


def cmd_te_info(args: argparse.Namespace) -> int:
    from axrange.platform import load_config
    from axrange.planner import StateStore

    arg = args.id_or_spec
    if arg and Path(arg).is_file():
        plan_args = argparse.Namespace(
            scenario=arg, images=args.images, adapter_config=args.adapter_config,
            node=args.node, command_label="te info",
        )
        try:
            plan, _m, _ir, _raw, cfg, _ctx = _load_planning_inputs(
                plan_args, quiet=getattr(args, "json", False)
            )
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1

        store = StateStore(args.db)
        try:
            existing = store.find_deploy_for_plan(plan.plan_id)
            if existing is not None:
                info = _te_info_from_ops(
                    store.ops_for_deploy(existing.deploy_id),
                    scenario_id=plan.scenario_id, plan_id=plan.plan_id,
                )
                info.deploy_id = existing.deploy_id
                info.status = existing.status.value
            else:
                info = _te_info_from_ops(
                    plan.ops.values(), scenario_id=plan.scenario_id, plan_id=plan.plan_id,
                )
        finally:
            store.close()
    else:
        # docs/WORK_ORDER_access_facts.md Part D: the bare/`[id]` path now
        # loads adapter.conf too (previously this branch never touched
        # config at all) -- needed for `default_password` + the live-IP
        # fallback in `_fill_access_facts` below.
        cfg = load_config(args.adapter_config)
        store = StateStore(args.db)
        try:
            did = arg
            if did is None:
                row = _latest_te_deploy(store)
                did = row.deploy_id if row is not None else None
            if did is None:
                sys.stdout.write("No deploys recorded.\n")
                return 0
            d = store.get_deploy(did)
            if d is None:
                sys.stderr.write(f"axrange te info: deploy not found: {did}\n")
                return 2
            info = _te_info_from_ops(
                store.ops_for_deploy(did), scenario_id=d.scenario_id, plan_id=d.plan_id,
            )
            info.deploy_id = did
            info.status = d.status.value
        finally:
            store.close()

    _fill_access_facts(info, cfg)

    if getattr(args, "json", False):
        # --json always carries the REAL passwords (Part D: "it's
        # operator-local"), regardless of --no-secrets.
        _print_json(dataclasses.asdict(info))
        return 0

    no_secrets = bool(getattr(args, "no_secrets", False))
    rollup = f"{info.status or 'not-deployed'} · {info.ops_done}/{info.ops_total} ops"
    sys.stdout.write(f"TE {info.deploy_id or '(not deployed)'} -- {rollup}\n")
    sys.stdout.write(f"  scenario: {info.scenario_id}\n")
    if not info.hosts:
        sys.stdout.write("  hosts: (none)\n")
    for h in info.hosts:
        line = f"  - {h.host_id} ({h.kind})"
        if h.vmid is not None:
            line += f" vmid={h.vmid}"
        if h.ssh_user:
            line += f" ssh_user={h.ssh_user}"
        sys.stdout.write(line + "\n")
        for net_id, ip in sorted(h.ips.items()):
            sys.stdout.write(f"      {net_id}: {ip}\n")
        if h.wan_ip:
            sys.stdout.write(f"      wan_ip: {h.wan_ip}\n")
        # `taps:` rendering REMOVED here (docs/WORK_ORDER_tap_eligibility.md,
        # Authority: user, 2026-07-28) -- a te carries no tap-related state;
        # `axrange tap info` is the operator's view of what's captured.
        if h.access_password is None:
            # A router with no cloud-init-sourced login (VyOS et al.) has NO
            # default-password account -- `access_hint` already says
            # "(key auth)"/"(key auth -- no default password)", so don't
            # bolt on a misleading "(password=None)" (SHOULD-FIX 2, code
            # review 2026-07-25).
            sys.stdout.write(f"      access: {h.access_hint}\n")
        else:
            shown_pw = "********" if no_secrets else h.access_password
            sys.stdout.write(
                f"      access: {h.access_hint}  (password={shown_pw})\n"
            )
    if info.subnets:
        sys.stdout.write("  subnets:\n")
        for s in info.subnets:
            sys.stdout.write(
                f"    - {s.net_id}: {s.cidr or '?'} gw={s.gateway or '?'} snat={s.snat}\n"
            )
    _write_egress_line(info.egress, indent="  ")
    return 0


def _write_egress_line(posture: Optional[dict[str, Any]], *, indent: str) -> None:
    """Render a twin's containment posture (GAP-101), shared by `te info` and
    `te inventory` so the two can never word it differently.

    Prints NOTHING when the twin declares no posture: a twin that never asked
    for containment must not get a line that could be skim-read as one. When
    it did declare, the APPLIED phase leads and an unapplied declaration says
    so explicitly -- "declared measure, NOT APPLIED" is the state the
    2026-09-21 breach was actually in, and it must never render as `measure`.
    """
    if not posture:
        return
    declared = posture.get("phase_declared") or "measure"
    applied = posture.get("phase_applied")
    sink = posture.get("sink_ip") or "?"
    mode = posture.get("mode") or "default"
    if applied:
        verdict = "CONTAINED" if posture.get("contained") else "OPEN (real egress)"
        state = f"{applied} -- {verdict}"
        if applied != declared:
            state += f"  (declared: {declared})"
    else:
        state = f"declared {declared}, NOT APPLIED (configure-router has not reported)"
    read_at = posture.get("live_read_at")
    live = f"  (live read {read_at})" if read_at else ""
    sys.stdout.write(f"{indent}egress: {state}  sink={sink} mode={mode}{live}\n")


def cmd_te_inventory(args: argparse.Namespace) -> int:
    """`te inventory [ID]` — docs/design/02-twin-model.md,
    docs/WORK_ORDER_tier_split.md. See `_add_te_inventory` for why this is a
    standalone verb rather than a `te info` flag."""
    from axrange.planner import StateStore
    from axrange.o2control.traffic import effective_roles_for_twin
    from axrange.planner.inventory import InventoryError, build_twin_inventory

    # `--images` is OPTIONAL and best-effort: `os` just stays `None` for
    # every host if it's omitted, or if manifest loading fails outright — an
    # inventory read must never hard-fail just because a resolvable-but-
    # missing images root was passed (review MUST-FIX #2: never fabricate a
    # default `os`, but also never let a soft `os` lookup block the rest of
    # the inventory from being read).
    manifests = None
    images_root = getattr(args, "images", None)
    if images_root:
        try:
            from axrange.manifest.loader import load_registry

            manifests = load_registry(root=Path(images_root))
        except Exception:  # noqa: BLE001 — best-effort; os stays None on failure
            manifests = None

    store = StateStore(args.db)
    try:
        did = args.deploy_id
        if did is None:
            row = _latest_te_deploy(store)
            did = row.deploy_id if row is not None else None
        if did is None:
            sys.stdout.write("No deploys recorded.\n")
            return 0
        try:
            # Effective roles: a tfc-assigned role wins over the twin's own
            # baseline (`docs/WORK_ORDER_role_persona_tier.md` §4.2). Looked up
            # HERE, not inside the engine -- `planner.inventory` must not import
            # `o2control` (LES-025), so the tier knowledge lives on this side.
            inv = build_twin_inventory(
                store, did, manifests=manifests,
                role_overrides=effective_roles_for_twin(store, did),
            )
        except InventoryError as e:
            sys.stderr.write(f"axrange te inventory: {e}\n")
            return 2
    finally:
        store.close()

    if getattr(args, "json", False):
        _print_json(inv.to_dict())
        return 0

    sys.stdout.write(f"TE inventory {inv.deploy_id} -- deploy status: {inv.deploy_status}\n")
    # The containment posture FIRST, before the roster (GAP-101): "is this
    # twin contained?" is a question about the whole twin, and an operator
    # reading a 100-host roster must not have to scroll for it.
    _write_egress_line(inv.egress.to_dict() if inv.egress is not None else None, indent="  ")
    if not inv.hosts:
        sys.stdout.write("  hosts: (none)\n")
    for h in inv.hosts:
        line = f"  - {h.host_id} vm_name={h.vm_name}"
        if h.vmid is not None:
            line += f" vmid={h.vmid}"
        # Show the BASELINE alongside whenever a tfc-assigned role overrode it
        # (`docs/WORK_ORDER_role_persona_tier.md` §4.2), so an operator is never
        # left wondering why a host answers to a role its twin config never
        # declared. Human output only -- `--json` carries both fields raw
        # (LES-149: a display decoration must not leak into the machine-readable
        # shape).
        role_txt = h.role or "?"
        if h.role_baseline != h.role:
            role_txt += f" (tfc; baseline: {h.role_baseline or 'none'})"
        line += f" status={h.vm_status or '?'} os={h.os or '?'} role={role_txt}"
        # `kind` (`docs/WORK_ORDER_tap_scopes.md` M6) -- a router should be
        # identifiable at a glance, not just inferable from its multi-homed
        # `nets` list below.
        if h.kind:
            line += f" kind={h.kind}"
        # The DECLARED listen set (`docs/WORK_ORDER_protocol_faithful_
        # services.md` S1) -- what the twin SAYS this host serves. Shown only
        # when the host declares one (`None` = declares nothing, which is
        # every host of every twin deployed before the field existed), and
        # never invented: a host with no declaration prints no `listen=` at
        # all rather than an empty one.
        if h.listen:
            ports = ",".join(f"{li.port}/{li.proto}" for li in h.listen)
            line += f" listen={ports}"
        sys.stdout.write(line + "\n")
        for net in h.nets:
            # `net.external` (M2) -- the router's WAN uplink now appears
            # here instead of being silently absent; mark it so it never
            # reads like an ordinary twin segment.
            marker = " (external)" if net.external else ""
            sys.stdout.write(
                f"      {net.net_id}{marker}: ip={net.ip or '?'} nic{net.nic_index}\n"
            )
    # `tap_points:`/the derived tap-point catalog rendering REMOVED here
    # (`docs/WORK_ORDER_tap_eligibility.md` D5, Authority: user, 2026-07-28)
    # -- `te inventory` publishes ONLY raw topology (`hosts[].nets[]` above).
    # `derive_default_tap_points` stays INTERNAL resolution machinery,
    # consumed only by the tap tier at bind time (`axrange tap
    # {validate,plan,deploy,info}`) -- never surfaced here.
    return 0


def cmd_te_scorecard(args: argparse.Namespace) -> int:
    """`te scorecard [ID]` — docs/WORK_ORDER_fidelity_scorecard.md §4.2.

    Calls `axrange.fidelity.runner.scorecard_for_deploy`, the SAME helper
    `GET /api/deploys/{id}/scorecard` calls, so the CLI and the API can never
    produce different row data for one deploy (LES-037 — the exact
    `te inventory`-vs-endpoint divergence `planner/inventory.py` records as a
    past review finding).

    `--json` emits nothing but JSON on stdout on success AND failure: every
    diagnostic below goes to stderr and the exit code carries the failure,
    never half-JSON on stdout (LES-083).

    Exit code (ruled §9.6, 9.4 (c)): `0` whenever a scorecard was produced,
    whatever its verdict — a verdict is data, not a process failure, and CI
    reading "honestly not golden" as "the tool broke" is the trap. `1` when
    it could not be produced. `--fail-on-verdict` opts INTO 3/4.
    """
    import datetime as _dt
    import json

    from axrange.fidelity import ReferenceError, scorecard_output_path
    from axrange.fidelity.runner import ProbeRunError, scorecard_for_deploy
    from axrange.o2control.traffic import effective_roles_for_twin
    from axrange.planner import StateStore
    from axrange.planner.inventory import InventoryError

    as_json = bool(getattr(args, "json", False))

    manifests = None
    images_root = getattr(args, "images", None)
    if images_root:
        try:
            from axrange.manifest.loader import load_registry

            manifests = load_registry(root=Path(images_root))
        except Exception as e:  # noqa: BLE001 — best-effort; `os` stays None
            # Best-effort, but never SILENT (S8): the operator asked for
            # `--images` and would otherwise get a document with `os: null`
            # everywhere and no hint why. To stderr, so `--json`'s stdout
            # stays pure JSON (LES-083).
            sys.stderr.write(
                f"axrange te scorecard: --images {images_root}: {e} "
                "(continuing; each host's `os` will be null)\n"
            )
            manifests = None
        else:
            # `load_registry` is deliberately tolerant -- a missing or
            # unparseable manifests/ directory yields an EMPTY registry
            # rather than an error. That is the reachable failure, and it is
            # exactly as invisible as an exception would have been: the
            # document simply carries `os: null` for every host.
            if not getattr(manifests, "manifests", None):
                sys.stderr.write(
                    f"axrange te scorecard: --images {images_root}: no image "
                    "manifests found (continuing; each host's `os` will be "
                    "null)\n"
                )

    attestation = None
    if getattr(args, "attest_visual", None):
        attestation = {
            "note": args.attest_visual,
            "attested_at": _dt.datetime.now(_dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "evidence_class": "asserted",
        }

    store = StateStore(args.db)
    try:
        did = args.deploy_id
        if did is None:
            row = _latest_te_deploy(store)
            did = row.deploy_id if row is not None else None
        if did is None:
            sys.stderr.write("axrange te scorecard: no deploys recorded.\n")
            return 1
        try:
            card = scorecard_for_deploy(
                store, did,
                reference_dir=getattr(args, "reference", None),
                manifests=manifests,
                role_overrides=effective_roles_for_twin(store, did),
                probe=not getattr(args, "no_probe", False),
                include_deferred=bool(getattr(args, "include_deferred", False)),
                attestation=attestation,
                adapter_config=getattr(args, "adapter_config", None),
            )
        except (ReferenceError, ProbeRunError, InventoryError) as e:
            sys.stderr.write(f"axrange te scorecard: {e}\n")
            return 1
    finally:
        store.close()

    doc = card.to_dict()

    written: Optional[Path] = None
    if not getattr(args, "no_write", False):
        try:
            written = scorecard_output_path(
                doc["reference"]["bundle"], doc["reference"]["bundle_id"],
                card.subject["deploy_id"], out=getattr(args, "out", None),
            )
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(
                json.dumps(doc, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
        except (OSError, ReferenceError) as e:
            sys.stderr.write(f"axrange te scorecard: could not write artifact: {e}\n")
            written = None

    if as_json:
        _print_json(doc)
    else:
        _render_scorecard(doc, written)

    verdict = doc["verdict"]
    if getattr(args, "fail_on_verdict", False):
        if verdict == "not-golden":
            return 3
        if verdict == "incomplete":
            return 4
    return 0


def _render_scorecard(doc: dict[str, Any], written: Optional[Path]) -> None:
    """The human table. Kept beside the handler (not in `axrange.fidelity`)
    for the same reason every other render lives here: the engine-side
    package publishes data, the CLI publishes prose."""
    subj = doc["subject"]
    ref = doc["reference"]
    sys.stdout.write(
        f"Fidelity scorecard (spec v{doc['scorecard_version']}) — "
        f"{subj['deploy_id']}\n"
    )
    sys.stdout.write(f"  verdict: {doc['verdict'].upper()}\n")
    sys.stdout.write(
        f"  subject: {subj['hosts']} host(s), {subj['segments']} segment(s), "
        f"status={subj['deploy_status']}, ir_hash={(subj['ir_hash'] or '')[:12]}\n"
    )
    thr = subj.get("deploy_threshold") or {}
    sys.stdout.write(
        f"  deploy threshold: "
        f"{thr.get('threshold') if thr.get('confirmed') else 'UNCONFIRMED'} "
        f"({thr.get('threshold_source')})\n"
    )
    sys.stdout.write(
        f"  reference: {ref['bundle']} — sidecar {ref['provenance_sidecar']}, "
        f"topology {ref['topology_export']}, fingerprint {ref['fingerprint']}\n"
    )
    gs = doc["gate_set"]
    sys.stdout.write(
        f"  gate set: gating {gs['gating']} · provisional {gs['provisional']} "
        f"· deferred {gs['deferred']} · reported {gs['reported']}\n"
    )
    sys.stdout.write("  rows:\n")
    for r in doc["rows"]:
        flag = " [provisional]" if r["provisional"] else ""
        sys.stdout.write(
            f"    {r['row']:>2}. {r['dimension']:<14} {r['status']:<9} "
            f"{r['verdict']}{flag}\n"
        )
        if r.get("reason"):
            sys.stdout.write(f"        reason: {r['reason']}\n")
        for c in r.get("clauses", []):
            detail = c.get("detail") or c.get("reason") or ""
            sys.stdout.write(
                f"        - {c['clause']}: {c['verdict']}"
                + (f" — {detail}" if detail else "") + "\n"
            )
    for w in doc["warnings"]:
        sys.stdout.write(f"  [warn] {w}\n")
    if written is not None:
        sys.stdout.write(f"  written: {written}\n")


def cmd_list_deploys(args: argparse.Namespace) -> int:
    from axrange.planner import StateStore

    store = StateStore(args.db)
    tier = getattr(args, "tier", None)
    if tier == "te":
        deploys = store.deploys_matching(tier="te", exclude_plan_id_prefix="tap-", limit=args.limit)
    elif tier == "o2":
        deploys = store.deploys_matching(tier="o2", limit=args.limit)
    elif tier == "tap":
        # `plan_id_prefix="tap-"` is only the first-pass filter -- a genuine
        # `te` scenario literally named `tap-foo` also matches it; the
        # structural check (`is_tap_plan_deploy`, code review finding,
        # docs/WORK_ORDER_cli_lifecycle.md) is what tells the two apart, so
        # filter to it here (no `limit=` on the raw query -- must scan every
        # candidate before truncating to `args.limit`).
        from axrange.o2control.taps import is_tap_plan_deploy

        candidates = store.deploys_matching(tier="te", plan_id_prefix="tap-")
        deploys = [d for d in candidates if is_tap_plan_deploy(store, d)][: args.limit]
    elif tier == "tfc":
        # tfc IS a real `_VALID_TIERS` deploy tier (unlike tap) -- a plain
        # tier-filtered query, no structural discriminator needed.
        deploys = store.deploys_matching(tier="tfc", limit=args.limit)
    elif tier == "exr":
        # exr IS a real `_VALID_TIERS` deploy tier too -- same posture as tfc.
        deploys = store.deploys_matching(tier="exr", limit=args.limit)
    else:
        deploys = store.latest_deploys(limit=args.limit)
    if not deploys:
        sys.stdout.write("No deploys recorded.\n")
        store.close()
        return 0
    import datetime as dt

    headers = ("deploy_id", "tier", "scenario", "status", "started_at")
    rows = [
        (
            d.deploy_id,
            _display_tier(store, d),
            d.scenario_id,
            d.status.value,
            dt.datetime.fromtimestamp(d.started_at).isoformat(timespec="seconds"),
        )
        for d in deploys
    ]
    store.close()  # every `_display_tier` structural check is done resolving

    # Dynamic per-column widths so a long scenario/deploy id (e.g. a
    # `tap-`/`tfc-`/`exr-<te-deploy-id>` playout, which is longer than any
    # fixed width would allow) can't push the later columns out of alignment.
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))
    ]

    def _fmt(cols: tuple) -> str:
        # Pad every column but the last (no trailing whitespace on the line).
        return "  ".join(
            c.ljust(widths[i]) if i < len(cols) - 1 else c
            for i, c in enumerate(cols)
        )

    sys.stdout.write(_fmt(headers) + "\n")
    for r in rows:
        sys.stdout.write(_fmt(r) + "\n")
    return 0


def _display_tier(store: Any, d: Any) -> str:
    """The user-facing tier for a `list-deploys` row. A tap-plan playout is
    stored with `tier="te"` and a `tap-` `plan_id` (`naming.py` has no
    separate `"tap"` deploy tier), so surface it as `tap` here to match
    `list-deploys --tier tap`'s own `is_tap_plan_deploy` discrimination —
    otherwise a bound tap plan would read as an ordinary `te` deploy. The
    structural check only runs for the narrow `te` + `tap-`-prefixed
    candidate set, so ordinary rows cost nothing extra. One rule shared with
    the API (`o2control.cascade.display_tier`)."""
    from axrange.o2control.cascade import display_tier

    return display_tier(store, d)


def cmd_destroy(args: argparse.Namespace) -> int:
    from axrange.planner import StateStore, destroy_deploy
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info
    from axrange.platform import adapter_for_config, load_config

    store = StateStore(args.db)
    deploy_id = args.deploy_id
    if not deploy_id:
        # Bare default: PREFER the newest NON-destroyed te deploy, not just
        # the newest te deploy period -- a just-destroyed tombstone (its
        # `updated_at` was just bumped by `mark_destroyed`) must never
        # outrank a genuinely live, older-updated te deploy (code review
        # finding, docs/WORK_ORDER_cli_lifecycle.md). `te status`/`te info`
        # deliberately do NOT do this -- they want the tombstone on top.
        latest_any = _latest_te_deploy(store)
        if latest_any is None:
            sys.stdout.write("No deploys recorded.\n")
            store.close()
            return 0
        latest_active = _latest_te_deploy(store, exclude_destroyed=True)
        if latest_active is None:
            sys.stdout.write(
                f"latest te deploy {latest_any.deploy_id} is already "
                "destroyed; nothing to do (pass an explicit deploy_id to "
                "act on a specific deploy).\n"
            )
            store.close()
            return 0
        deploy_id = latest_active.deploy_id

    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        sys.stderr.write(f"axrange destroy: deploy not found: {deploy_id}\n")
        store.close()
        return 2
    if not args.allow_destroy:
        sys.stderr.write(
            f"axrange destroy: refusing to tear down {deploy_id} "
            f"(scenario={deploy.scenario_id}, status={deploy.status.value}) "
            "without --allow-destroy.\n"
            "  This removes the deploy's VMs, SDN vnets/zone, gretaps, mirror "
            "hooks, and (VLAN) OVS underlay bridge — this deploy's te/tap-tier "
            "resources only (never o2_/img_).\n"
        )
        store.close()
        return 2

    cfg = load_config(args.adapter_config)
    if args.node:
        cfg.default_node = args.node

    # Single-writer gate: refuse to destroy a scenario with a deploy in flight.
    lease = store.acquire_lease(
        deploy.scenario_id, deploy_id, process_info=default_process_info()
    )
    if not lease.acquired:
        sys.stdout.write(
            f"\nAnother deploy of scenario '{deploy.scenario_id}' is in progress "
            f"(deploy_id={lease.held_by_deploy_id}); refusing to destroy.\n"
        )
        store.close()
        return 1

    sys.stdout.write(
        f"\nDestroying {deploy_id} (scenario={deploy.scenario_id}, "
        f"plan_id={deploy.plan_id})...\n"
    )
    backend = adapter_for_config(cfg).teardown
    # Heartbeat the lease for the life of the teardown (code-review finding #2,
    # 2026-07-07): a multi-VM sweep can outrun the lease's fixed TTL with no
    # renewal, same fix as the deploy path's `heartbeat` above. It spans the
    # sibling cascades too (they run inside this `try` now), which is what keeps
    # the lease renewed across a critical section that is much longer than the
    # twin's own sweep alone.
    heartbeat = Heartbeat(str(args.db), deploy_id=deploy_id, scenario_id=deploy.scenario_id)
    heartbeat.start()
    try:
        # ---- Sibling-tier cascades run BEFORE the twin's own sweep ---------
        # `exr` → `tfc` → `tap`, all before the `te` sweep below: every sibling
        # binds into the twin's LIVE VMs, so a cascade sequenced after the VM
        # purge can only fail (LES-171). Order + per-sibling exception
        # isolation (LES-117) live in `o2control/cascade.py` — ONE helper both
        # this CLI verb and `api/service.py:destroy_deploy` call, so the
        # sequence can't drift between them (LES-037).
        from axrange.o2control.cascade import cascade_destroy_siblings_for_te

        for casc in cascade_destroy_siblings_for_te(
            db_path=str(args.db), te_deploy_id=deploy_id,
            adapter_config=args.adapter_config, node=args.node,
            teardown_backend=backend,
            log=lambda m: sys.stdout.write(m + "\n"),
        ):
            if casc.error is not None:
                sys.stdout.write(f"  ! {casc.label} cascade failed: {casc.error}\n")
                continue
            for r in casc.results:
                sys.stdout.write(
                    f"  {casc.label} cascade: {r.removed} resource(s) removed; "
                    f"{len(r.errors)} not cleared.\n"
                )

        # ---- The twin's own sweep, last ------------------------------------
        # The orchestrator releases the lease + resource locks at its end on a
        # clean sweep.
        result = destroy_deploy(
            store, backend, deploy_id, node=args.node,
            log=lambda m: sys.stdout.write(m + "\n"),
            protected_names=cfg.protected_external_names,
            protected_vmids=cfg.protected_external_vmids,
        )
    finally:
        heartbeat.stop()
        # Belt-and-suspenders (finding #5): `destroy_deploy` releases the
        # lease unconditionally at its own tail on the success path, but not
        # inside a try/finally there — an exception raised before that point
        # would otherwise leave this lease held until TTL expiry.
        # `release_lease` is a plain DELETE, idempotent, so releasing it again
        # here on the normal path is a harmless no-op.
        try:
            store.release_lease(deploy.scenario_id, deploy_id)
        except Exception:  # noqa: BLE001
            pass
        store.close()

    errs = result.errors
    sys.stdout.write(
        f"\n  {result.removed} resource(s) removed; {len(errs)} not cleared.\n"
    )

    # NOTE: the sibling-tier cascades (exr/tfc/tap) already ran ABOVE, before
    # the twin's own sweep, via `cascade_destroy_siblings_for_te` (LES-171).
    # A SECOND, individual set of after-sweep cascade calls used to live here
    # -- the pre-LES-171 anti-pattern the 2026-07-29 fix was supposed to
    # remove but left behind, so every `te destroy` ran the cascade twice: the
    # duplicate pass fired against VMs the first pass had already purged,
    # reproducing the exact `Configuration file ... does not exist` wall
    # LES-171 exists to prevent (harmless in outcome -- the helpers are
    # idempotent -- but real duplicate work, and CLI-only, exactly the drift
    # the ONE-shared-helper design forbids; `api/service.py` always called it
    # once). Deleted 2026-08-04 (W1 dev45 live-verify, finding #4).

    if result.destroyed:
        sys.stdout.write(f"Result: DESTROYED ({deploy_id})\n")
        return 0
    sys.stdout.write(
        "Result: INCOMPLETE — some resources were not cleared; re-run "
        "`axrange te destroy` to finish (idempotent).\n"
    )
    return 1


def _render_deploy_result(result, plan) -> None:
    """Render the post-run tree the executor returns."""
    succ = sum(1 for o in result.per_op.values() if o.final_status.value == "completed")
    fail = sum(1 for o in result.per_op.values() if o.final_status.value == "failed")
    halt = sum(
        1 for o in result.per_op.values()
        if o.final_status.value == "halted-due-to-upstream"
    )
    sys.stdout.write(f"  ops: {succ} ok, {fail} failed, {halt} halted\n\n")

    # Print one line per op in topological order.
    for op_id in plan.topological_order():
        node = plan.get(op_id)
        if node is None:
            continue
        outcome = result.per_op.get(op_id)
        if outcome is None:
            sys.stdout.write(f"  . {node.render():35s} [pending]\n")
            continue
        symbol = {
            "completed": "o",
            "failed": "x",
            "halted-due-to-upstream": "/",
        }.get(outcome.final_status.value, "?")
        tail = ""
        if outcome.attempts > 1:
            tail += f" attempts={outcome.attempts}"
        if outcome.error_message:
            tail += f" error={outcome.error_message[:70]}"
        sys.stdout.write(f"  {symbol} {node.render():35s}{tail}\n")
    sys.stdout.write(f"\nResult: {result.final_status.value.upper()}\n")


# ===========================================================================
# o2 (the standing O2 surface — `axrange.o2control`, docs/design/05-o2-surface.md)
#
# Thin transport only: all logic lives in `axrange.o2control` (`docs/
# CONVENTIONS.md` code-structure rule, mirroring the `config_exec` pattern).
# Grid-uniform verbs (work order §3.1): validate/plan/deploy/status/info/
# destroy, `deploy`/`destroy` reached under their NEW names (were `up`/`down`
# -- deprecated shims still route here via `main()`'s (tier,verb) dispatch).
# ===========================================================================


def _render_surface_status(status) -> None:
    sys.stdout.write(f"Surface deploy: {status.deploy_id or '(none)'}\n")
    sys.stdout.write(f"  status: {status.status}\n")
    if not status.sensors:
        sys.stdout.write("  sensors: (none)\n")
        return
    sys.stdout.write("  sensors:\n")
    for s in status.sensors:
        sys.stdout.write(
            f"    - {s.sensor_id:20s} type={s.type:16s} "
            f"transport_ip={s.transport_ip or '(unassigned)'}\n"
        )


def _render_o2_endpoints(endpoints: dict[str, str]) -> None:
    sys.stdout.write("  endpoints:\n")
    for name in ("grafana", "loki", "prometheus", "caldera", "wazuh"):
        val = endpoints.get(name)
        sys.stdout.write(
            f"    {name:10s} {val or '(not configured — see adapter.conf O2_*)'}\n"
        )


def _external_sensor_facts(
    cfg: Any, *, only_ids: Optional[set[str]] = None
) -> list[dict[str, Any]]:
    """Thin wrapper over the inventory seam's `external_sensor_facts`
    (`axrange.platform.protocols.Inventory`; moved into the adapter
    2026-07-26, `docs/WORK_ORDER_access_facts.md`'s API-parity follow-on, so
    `axrange.api.service.o2_info` can call the SAME derivation this CLI verb
    does instead of a second implementation, LES-037). Kept as a module-level
    name here since `cmd_o2_info`/`cmd_tap_info` already call it as
    `_external_sensor_facts`."""
    from axrange.platform import adapter_for_config

    return adapter_for_config(cfg).inventory.external_sensor_facts(only_ids=only_ids)


def _render_external_sensors(sensors: list[dict[str, Any]], *, no_secrets: bool = False) -> None:
    if not sensors:
        sys.stdout.write("  external sensors: (none declared)\n")
        return
    sys.stdout.write("  external sensors (operator-managed; axRange never reaches into these):\n")
    for s in sensors:
        pw = "********" if no_secrets else s["access_password"]
        sys.stdout.write(
            f"    - {s['id']:16s} type={s['type']:14s} vmid={s['vmid']} "
            f"transport_ip={s['transport_ip']}\n"
        )
        sys.stdout.write(
            f"        resolved: name={s['resolved_name'] or '(unresolved)'} "
            f"mgmt_ip={s['resolved_ip'] or '(unresolved)'}\n"
        )
        sys.stdout.write(f"        access: {s['access_hint']}  (password={pw})\n")


def cmd_o2_validate(args: argparse.Namespace) -> int:
    from axrange.o2control import O2ControlError, validate_surface

    images_root = Path(args.images) if args.images else Path.cwd()
    sys.stdout.write(f"Validating O2 surface (spec={args.spec or '(default)'})...\n\n")
    try:
        result = validate_surface(surface_path=args.spec, images_root=images_root)
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 validate: {e.message}\n")
        return 2
    _render_result(result, verbose=False)
    return result.exit_code


def cmd_o2_plan(args: argparse.Namespace) -> int:
    from axrange.o2control import O2ControlError, plan_surface

    images_root = Path(args.images) if args.images else Path.cwd()
    try:
        plan = plan_surface(
            surface_path=args.spec, images_root=images_root,
            adapter_config=args.adapter_config, target_node=args.node,
        )
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 plan: {e.message}\n")
        return 2
    sys.stdout.write(f"\nO2 surface plan: {plan.plan_id}\n")
    sys.stdout.write(
        f"  scenario={plan.scenario_id} version={plan.scenario_version} "
        f"ir_hash={plan.ir_hash[:8]}\n"
    )
    sys.stdout.write(
        f"  ops={len(plan.ops)} barriers={len(plan.barriers)} edges={len(plan.edges)}\n\n"
    )
    sys.stdout.write(plan.render_tree() + "\n")
    return 0


def cmd_o2_deploy(args: argparse.Namespace) -> int:
    from axrange.o2control import O2ControlError, stand_up_surface

    images_root = Path(args.images) if args.images else Path.cwd()
    sys.stdout.write(
        f"Standing up the O2 surface (surface={args.spec or '(default)'})...\n"
    )
    try:
        status = stand_up_surface(
            surface_path=args.spec,
            db_path=args.db,
            images_root=images_root,
            adapter_config=args.adapter_config,
            target_node=args.node,
        )
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 deploy: {e.message}\n")
        return 2 if e.status < 500 else 1

    sys.stdout.write("\n")
    _render_surface_status(status)
    return 0 if status.status == "completed" else 1


def cmd_o2_status(args: argparse.Namespace) -> int:
    from axrange.o2control import O2ControlError, surface_status

    try:
        status = surface_status(db_path=args.db, deploy_id=args.deploy_id)
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 status: {e.message}\n")
        return 1
    if getattr(args, "json", False):
        _print_json(dataclasses.asdict(status))
        return 0
    _render_surface_status(status)
    return 0


def cmd_o2_info(args: argparse.Namespace) -> int:
    from axrange.platform import load_config
    from axrange.o2control import O2ControlError, surface_status
    from axrange.o2control.manifest import (
        SurfaceManifestError, load_surface_manifest, resolve_manifest_endpoints,
    )

    cfg = load_config(args.adapter_config)
    no_secrets = bool(getattr(args, "no_secrets", False))
    # Part C: resolved regardless of which branch below runs -- declared via
    # adapter.conf's EXTERNAL_SENSORS, independent of a manifest/surface deploy.
    ext_sensors = _external_sensor_facts(cfg)
    arg = args.id_or_spec

    if arg and Path(arg).is_file():
        try:
            manifest = load_surface_manifest(arg)
        except SurfaceManifestError as e:
            sys.stderr.write(f"axrange o2 info: {e}\n")
            return 2
        # Part B item 5: a manifest's own `services:` stanza overrides
        # adapter.conf's declared services VM for THIS resolution only.
        endpoints = resolve_manifest_endpoints(cfg, manifest)
        if getattr(args, "json", False):
            _print_json({
                "surface_id": manifest.surface_id,
                "sensors": [{"id": s.id, "type": s.type} for s in manifest.sensors],
                "endpoints": endpoints,
                "external_sensors": ext_sensors,
            })
            return 0
        sys.stdout.write(f"Surface manifest: {arg} (id={manifest.surface_id})\n")
        sys.stdout.write("  sensors (declared, not necessarily standing):\n")
        for s in manifest.sensors:
            sys.stdout.write(f"    - {s.id:20s} type={s.type}\n")
        _render_o2_endpoints(endpoints)
        _render_external_sensors(ext_sensors, no_secrets=no_secrets)
        return 0

    endpoints = cfg.resolve_o2_endpoints()
    try:
        status = surface_status(db_path=args.db, deploy_id=arg)
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 info: {e.message}\n")
        return 1
    if getattr(args, "json", False):
        d = dataclasses.asdict(status)
        d["endpoints"] = endpoints
        d["external_sensors"] = ext_sensors
        _print_json(d)
        return 0
    sys.stdout.write(f"O2 surface {status.deploy_id or '(not deployed)'} -- {status.status}\n")
    _render_surface_status(status)
    _render_o2_endpoints(endpoints)
    _render_external_sensors(ext_sensors, no_secrets=no_secrets)
    return 0


def cmd_o2_destroy(args: argparse.Namespace) -> int:
    from axrange.o2control import O2ControlError, surface_status, tear_down_surface
    from axrange.o2control.control import TIER as O2_TIER
    from axrange.planner import StateStore

    deploy_id = args.deploy_id
    if not deploy_id:
        # Bare default: PREFER the newest NON-destroyed o2 deploy -- see
        # `cli.main._latest_te_deploy`'s docstring for why (`mark_destroyed`
        # bumps `updated_at`, so a just-destroyed tombstone would otherwise
        # outrank a genuinely live, newer o2 deploy). `o2 status`/`o2 info`
        # deliberately do NOT do this.
        store = StateStore(args.db)
        try:
            latest_any = store.latest_deploy_for_tier(O2_TIER)
            if latest_any is None:
                sys.stdout.write("No o2 surface deploy found.\n")
                return 0
            latest_active = store.latest_deploy_for_tier(O2_TIER, exclude_destroyed=True)
        finally:
            store.close()
        if latest_active is None:
            sys.stdout.write(
                f"latest o2 deploy {latest_any.deploy_id} is already "
                "destroyed; nothing to do (pass an explicit deploy_id to "
                "act on a specific deploy).\n"
            )
            return 0
        deploy_id = latest_active.deploy_id

    if not args.allow_destroy:
        try:
            current = surface_status(db_path=args.db, deploy_id=deploy_id)
        except O2ControlError as e:
            sys.stderr.write(f"axrange o2 destroy: {e.message}\n")
            return 2 if e.status < 500 else 1
        sys.stderr.write(
            f"axrange o2 destroy: refusing to tear down {current.deploy_id or '(none)'} "
            "without --allow-destroy.\n"
            "  This removes the standing surface's o2_-tier sensor VM(s) + "
            "transport vnet/zone (never a te_/tap_ target-environment deploy).\n"
        )
        return 2

    try:
        result = tear_down_surface(
            db_path=args.db, deploy_id=deploy_id,
            adapter_config=args.adapter_config, node=args.node,
            log=lambda m: sys.stdout.write(m + "\n"),
        )
    except O2ControlError as e:
        sys.stderr.write(f"axrange o2 destroy: {e.message}\n")
        return 2 if e.status < 500 else 1

    errs = result.errors
    sys.stdout.write(
        f"\n  {result.removed} resource(s) removed; {len(errs)} not cleared.\n"
    )
    if result.destroyed:
        sys.stdout.write(f"Result: DESTROYED ({result.deploy_id})\n")
        return 0
    sys.stdout.write(
        "Result: INCOMPLETE — some resources were not cleared; re-run "
        "`axrange o2 destroy` to finish (idempotent).\n"
    )
    return 1


# ===========================================================================
# tap (A2, the tap config playout -- `axrange.o2control.taps`,
# docs/design/06-taps-sensors.md). Thin transport only, same posture as the
# o2 surface commands above. Grid-uniform verbs (work order §3.1/§3.2/§3.3).
# ===========================================================================


def _render_tap_status(status) -> None:
    sys.stdout.write(f"Tap-plan deploy: {status.deploy_id or '(none)'}\n")
    sys.stdout.write(f"  target: {status.target}\n")
    sys.stdout.write(f"  status: {status.status}\n")
    if not status.bindings:
        sys.stdout.write("  bindings: (none)\n")
    else:
        sys.stdout.write("  bindings:\n")
        for b in status.bindings:
            # `direction` (`docs/WORK_ORDER_tap_scopes.md` M4/M6) -- the
            # resolved mirror direction this binding produced.
            direction = f"  [{b.direction}]" if getattr(b, "direction", None) else ""
            sys.stdout.write(f"    - {b.tap:20s} -> {', '.join(b.sensors)}{direction}\n")
    # Non-fatal apply-time findings (code review, follow-on to the
    # config-external-sensor feature) -- e.g. "capture UNVERIFIED for
    # external sensor ..." -- LES-001 forbids a silent "green deploy, no
    # capture" outcome, so these must be visible even on a `completed` apply.
    for w in getattr(status, "warnings", None) or []:
        sys.stdout.write(f"  [warn] {w}\n")


def cmd_tap_validate(args: argparse.Namespace) -> int:
    """Two modes, keyed on EFFECTIVE-target presence
    (`docs/WORK_ORDER_tap_target_resolution.md` §"tap validate"):

      * effective target PRESENT (`--target`, else the plan's own
        `target:`) -- resolve it (`resolve_live_target_deploy`: name or id,
        MUST land on a live/active `te` deploy or this FAILS outright), then
        validate the bindings against that twin's REAL published inventory
        (`resolve_tap_plan_bindings`, the pre-existing live-resolution
        path -- degrades to a WARNING, not a hard error, if the state DB
        itself is absent/unreadable, so an offline lint of a tap-plan file
        naming a target still works).
      * effective target ABSENT -- SEMANTICS-ONLY: the static
        `parse_tap_plan`/`load_tap_plan` checks above ARE the whole check --
        well-formed selector/scope, non-empty `sensors`, a valid `mode`
        (per-binding: `mode: exclusive` requires exactly 1 sensor), and a
        LITERAL duplicate-addressing dedup (the same `tap`/`tap_point`/
        `selector` string named twice) -- no `StateStore`/inventory lookup
        at all. **This does NOT catch two DIFFERENT selectors that would
        resolve to the SAME tap point once bound to a live twin** -- the
        FULL plan-wide `mode: exclusive` overlap check (`_resolve_bindings`'
        M3 enforcement) runs against RESOLVED interfaces and can only run in
        the target-PRESENT mode above. A target-less PASS here means
        "structurally sound," not "guaranteed collision-free against every
        twin it could ever bind to." This is how a PREDEFINED/library plan
        (no `target:`, no `--target`) validates.
    """
    from axrange.platform import ConfigError, load_config
    from axrange.o2control.taps import (
        TapPlanError, TapPlanSpec, binding_label, load_tap_plan,
        resolve_live_target_deploy, resolve_tap_plan_bindings,
    )
    from axrange.planner import StateStore

    try:
        spec = load_tap_plan(args.spec)
    except TapPlanError as e:
        sys.stderr.write(f"axrange tap validate: {e.message}\n")
        return 2 if e.status < 500 else 1

    effective_target = getattr(args, "target", None) or spec.target
    sys.stdout.write(
        f"tap-plan {args.spec}: static OK "
        f"(target={effective_target or '(none -- predefined plan)'}, "
        f"{len(spec.bindings)} binding(s))\n"
    )
    for b in spec.bindings:
        # `hint=True` (fix round F4, `docs/WORK_ORDER_tap_scopes.md`) --
        # this is the ONE static, pre-resolution render (no live twin yet,
        # so no RESOLVED direction is available); every other renderer
        # passes a real resolved `direction` instead and leaves `hint` off.
        sys.stdout.write(
            f"  {binding_label(b, hint=True)} -> {', '.join(b.sensors)}\n"
        )

    if not effective_target:
        # SEMANTICS-ONLY: the static parse above already enforced every
        # structural invariant a target-less plan CAN be checked WITHOUT a
        # live twin to resolve selectors/tap ids against -- no StateStore is
        # even opened. Code review (DOC-ACCURACY): a PASS here does NOT mean
        # "no exclusive-binding collision is possible" -- the full plan-wide
        # `mode: exclusive` overlap check runs against RESOLVED interfaces
        # (`_resolve_bindings`'s M3 enforcement) and can only run once a real
        # twin is bound (the target-PRESENT branch below); two different
        # selectors that happen to resolve to the SAME tap point on a given
        # twin are invisible to this static-only check.
        sys.stdout.write(
            "\n[info] no target (plan or --target) -- semantics-only "
            "validation, no state/inventory lookup\n"
        )
        sys.stdout.write("\nResult: PASS\n")
        return 0

    db_path = Path(args.db)
    if not db_path.is_file():
        sys.stdout.write(
            f"\n[warn] state DB not found at {db_path}; skipping resolve "
            "checks (static lint only)\n"
        )
        return 0

    try:
        store = StateStore(str(db_path))
    except Exception as e:  # noqa: BLE001 -- an unreadable/locked DB degrades
        # this to a static-only lint (work order §3.3), never a hard error
        # on an offline check.
        sys.stdout.write(
            f"\n[warn] state DB unreadable ({e}); skipping resolve checks "
            "(static lint only)\n"
        )
        return 0
    try:
        # cfg is loaded here (not just at `tap plan`/`tap apply`) so a tap-plan
        # naming a config-declared EXTERNAL sensor (adapter.conf
        # EXTERNAL_SENSORS) resolves IDENTICALLY at validate-time as it will
        # at apply-time -- LES-069, `resolve_tap_plan_bindings`'s docstring.
        # A malformed adapter.conf fails LOUD here too (a real misconfiguration
        # the operator needs to see), unlike the DB-unreadable case above
        # which is a legitimate "no DB yet" offline scenario.
        try:
            cfg = load_config(args.adapter_config)
        except ConfigError as e:
            sys.stderr.write(f"\n[error] adapter.conf: {e}\n")
            return 1
        try:
            # `resolve_live_target_deploy`: name-or-id resolution PLUS the
            # "must be a live, active te deploy" assertion this mode
            # requires (a resolved-but-destroyed/wrong-tier target is a
            # HARD failure here, not just a status-mismatch warning).
            target_row = resolve_live_target_deploy(store, effective_target)
        except TapPlanError as e:
            sys.stderr.write(f"\n[error] {e.message}\n")
            return 1
        if target_row.deploy_id != spec.target:
            spec = dataclasses.replace(spec, target=target_row.deploy_id)
        try:
            resolution = resolve_tap_plan_bindings(
                spec, store, db_path=str(db_path),
                external_sensors=cfg.external_sensors,
            )
        except TapPlanError as e:
            sys.stderr.write(f"\n[error] {e.message}\n")
            return 1
    finally:
        store.close()

    sys.stdout.write("\nresolved against live state:\n")
    sys.stdout.write(f"  target TE {spec.target}: status={resolution.target_status}\n")
    if resolution.target_status != "completed":
        sys.stdout.write(
            f"  [warn] target TE status is {resolution.target_status!r}, not 'completed'\n"
        )
    for (host, net_id, nic), sid_directions in sorted(resolution.iface_new_sensors.items()):
        # Per-sensor direction (`docs/WORK_ORDER_tap_scopes.md` M4/M6) --
        # already unioned per (port, sensor) by resolution.
        sensors_rendered = ", ".join(
            f"{sid}({sid_directions[sid]})" for sid in sorted(sid_directions)
        )
        sys.stdout.write(f"  {host}/{net_id}[nic{nic}] -> {sensors_rendered}\n")
    for w in resolution.warnings:
        sys.stdout.write(f"  [warn] {w}\n")
    sys.stdout.write("\nResult: PASS\n")
    return 0


def cmd_tap_plan(args: argparse.Namespace) -> int:
    from axrange.platform import load_config
    from axrange.o2control.taps import (
        TapPlanError, TapPlanSpec, compose_tap_ops, load_tap_plan,
        resolve_target_deploy, resolve_tap_plan_bindings,
    )
    from axrange.planner import StateStore

    try:
        spec = load_tap_plan(args.spec)
    except TapPlanError as e:
        sys.stderr.write(f"axrange tap plan: {e.message}\n")
        return 2 if e.status < 500 else 1

    effective_target = getattr(args, "target", None) or spec.target
    if not effective_target:
        sys.stderr.write(
            "axrange tap plan: no tap-plan target: set target: in the plan "
            "or pass --target\n"
        )
        return 2

    cfg = load_config(args.adapter_config)
    if args.node:
        cfg.default_node = args.node
    if not cfg.default_node:
        sys.stderr.write(
            "axrange tap plan: DEFAULT_NODE missing; set it in adapter.conf, "
            "or pass --node\n"
        )
        return 2

    store = StateStore(args.db)
    try:
        try:
            concrete_target = resolve_target_deploy(store, effective_target)
        except TapPlanError as e:
            sys.stderr.write(f"axrange tap plan: {e.message}\n")
            return 2 if e.status < 500 else 1
        if concrete_target != spec.target:
            spec = dataclasses.replace(spec, target=concrete_target)
        try:
            resolution = resolve_tap_plan_bindings(
                spec, store, db_path=args.db, external_sensors=cfg.external_sensors,
            )
        except TapPlanError as e:
            sys.stderr.write(f"axrange tap plan: {e.message}\n")
            return 2 if e.status < 500 else 1
        plan = compose_tap_ops(
            resolution, target=spec.target, store=store, target_node=cfg.default_node,
        )
    finally:
        store.close()

    sys.stdout.write(f"\nTap-plan: tap-{spec.target} (target={spec.target})\n")
    sys.stdout.write(
        f"  ops={len(plan.ops)} barriers={len(plan.barriers)} edges={len(plan.edges)}\n\n"
    )
    sys.stdout.write(plan.render_tree() + "\n")
    for w in resolution.warnings:
        sys.stdout.write(f"[warn] {w}\n")
    return 0


def _NodeResolvingGuestAgent(cfg: Any, fallback: str) -> Any:  # noqa: N802 -- old class name
    """Moved to `o2control.containment.NodeResolvingGuestAgent` so the API's
    per-host persona apply builds the identical agent (LES-037); this lazy
    factory keeps the old name without importing `o2control` at CLI import."""
    from axrange.o2control.containment import NodeResolvingGuestAgent

    return NodeResolvingGuestAgent(cfg, fallback)


def _egress_guest_agent(args: argparse.Namespace) -> Any:
    """The real guest agent for `te egress` and the tap/tfc containment
    gate's live read, from `--adapter-config`/`--node`; it execs on the node
    the router VM is on (`_NodeResolvingGuestAgent`). One function so the two
    callers cannot build it differently -- and the tests' one seam."""
    from axrange.platform import load_config

    cfg = load_config(getattr(args, "adapter_config", None))
    return _NodeResolvingGuestAgent(cfg, getattr(args, "node", None) or cfg.default_node)


def cmd_te_egress(args: argparse.Namespace, *, ga: Any = None) -> int:
    """`te egress` (see `_add_te_egress`). `ga` is a test seam: a
    GuestAgent-shaped fake; the real one is built from adapter.conf."""
    from axrange.platform import ConfigError
    from axrange.o2control.containment import ContainmentError, egress_live
    from axrange.o2control.taps import TapPlanError, resolve_target_deploy
    from axrange.planner import StateStore
    from axrange.planner.executor import AdapterError  # AdapterTimeout subclasses it

    action = "show" if args.show else args.phase
    if not Path(args.db).exists():
        sys.stderr.write(f"axrange te egress: no state DB at {args.db}\n")
        return 2
    store = StateStore(args.db)
    try:
        target = args.target
        if target is None:
            row = _latest_te_deploy(store, exclude_destroyed=True)
            if row is None:
                sys.stderr.write("axrange te egress: no live te deploy recorded\n")
                return 2
            deploy_id = row.deploy_id
        else:
            try:
                deploy_id = resolve_target_deploy(store, target)
            except TapPlanError as e:
                sys.stderr.write(f"axrange te egress: {e}\n")
                return 2
        try:
            if ga is None:
                ga = _egress_guest_agent(args)
            res = egress_live(store, deploy_id, action, ga=ga)
        except (ContainmentError, ConfigError, AdapterError) as e:
            # `egress_live` already recorded the row NOT contained on any
            # exec failure; a config/transport error before it reached the
            # guest left the record as it was (no guest was touched).
            sys.stderr.write(f"axrange te egress: {e}\n")
            failed = getattr(e, "result", None)
            if failed is not None and getattr(args, "json", False):
                # A failed flip still read the router back completely: a
                # --json caller gets that whole record (+ the error), rc 1.
                _print_json({**failed.to_dict(), "error": str(e)})
            return 1
    finally:
        store.close()

    if getattr(args, "json", False):
        _print_json(res.to_dict())
        return 0 if res.healthy else 1
    verdict = "CONTAINED" if res.contained else "NOT contained"
    sys.stdout.write(
        f"te egress {res.deploy_id}: router {res.router_host} (vmid {res.vmid}) "
        f"phase={res.phase or '?'} since={res.since or '?'} "
        f"healthcheck={'ok' if res.healthy else 'FAILED'} -> recorded "
        f"egress_phase={res.recorded} -- {verdict} (read {res.read_at})\n"
    )
    for line in res.health_lines:
        sys.stdout.write(f"  {line}\n")
    return 0 if res.healthy else 1


def _containment_gate(args: argparse.Namespace, tier: str) -> Optional[int]:
    """Refuse a `tap deploy`/`tfc deploy` whose target twin is NOT contained
    (LES-001/LES-004: never trust a declaration or a green deploy;
    `docs/WORK_ORDER_corp_twin.md` §11 step 2).

    The verdict is a LIVE read: when the target declares an `egress:`
    posture (`egress_posture_from_ops` over its recorded rows), the gate runs
    `o2control.containment.egress_live(..., "show")` -- the router's own
    `--show` + `--healthcheck` over QGA (one 60 s budget across both execs),
    recorded onto the same row `te info` reads -- and passes only on an
    asserted `measure`. A router that cannot be
    read (no QGA, unreachable cluster, engine missing) refuses: an unknown
    phase is not a contained one. This supersedes the earlier "the recorded
    row, not a second probe" implementation call (WO §11 step 2): a recorded
    `measure` says what the router was in when last read, not now.

    Returns 2 to refuse, None to carry on. A twin that declares no `egress:`
    is unaffected (no posture, no probe), as is any target this cannot
    resolve: the handler's own loader/resolver then reports that error
    exactly as before. `--allow-uncontained` proceeds with a loud warning."""
    from axrange.o2control.containment import containment_verdict
    from axrange.planner import StateStore

    if tier == "tap":
        from axrange.o2control.taps import TapPlanError as SpecError
        from axrange.o2control.taps import load_tap_plan as load_spec
        from axrange.o2control.taps import resolve_target_deploy as resolve
    else:
        from axrange.o2control.traffic import TrafficPlanError as SpecError
        from axrange.o2control.traffic import load_traffic_profile as load_spec
        from axrange.o2control.traffic import resolve_traffic_target as resolve

    try:
        spec = load_spec(args.spec)
    except SpecError:
        return None
    target = getattr(args, "target", None) or spec.target
    if not target or not Path(args.db).exists():
        return None
    try:
        store = StateStore(args.db)
    except Exception:  # noqa: BLE001 -- the handler opens it too, and reports it
        return None
    try:
        try:
            deploy_id = resolve(store, target)
        except SpecError:
            return None
        # One decision rule shared with the API's per-host persona apply
        # (`o2control.containment.containment_verdict`); it reads the
        # posture itself and builds the agent only when there is one.
        verdict = containment_verdict(
            store, deploy_id, ga_factory=lambda: _egress_guest_agent(args), timeout_s=60,
        )
    finally:
        store.close()
    if verdict is None or verdict.contained:
        return None
    why = verdict.why
    if getattr(args, "allow_uncontained", False):
        sys.stderr.write(
            f"[warn] axrange {tier} deploy: target {target} (deploy {deploy_id}) is NOT "
            f"contained: {why} -- proceeding: --allow-uncontained\n"
        )
        return None
    sys.stderr.write(
        f"axrange {tier} deploy: refusing -- target {target} (deploy {deploy_id}) is NOT "
        f"contained: {why}; real internet egress is possible "
        f"(`axrange te info {deploy_id}`). Contain it first: "
        f"`axrange te egress {deploy_id} --phase measure` "
        "(docs/WORK_ORDER_corp_twin.md §11 step 2), then re-run -- this gate reads "
        "the router live on every deploy. --allow-uncontained overrides.\n"
    )
    return 2


def cmd_tap_deploy(args: argparse.Namespace) -> int:
    from axrange.o2control.taps import TapPlanError, apply_tap_plan

    rc = _containment_gate(args, "tap")
    if rc is not None:
        return rc
    sys.stdout.write(f"Applying tap-plan {args.spec}...\n")
    try:
        status = apply_tap_plan(
            spec_path=args.spec, db_path=args.db,
            adapter_config=args.adapter_config, target_node=args.node,
            target=getattr(args, "target", None),
        )
    except TapPlanError as e:
        sys.stderr.write(f"axrange tap deploy: {e.message}\n")
        return 2 if e.status < 500 else 1

    sys.stdout.write("\n")
    _render_tap_status(status)
    return 0 if status.status == "completed" else 1


def cmd_tap_status(args: argparse.Namespace) -> int:
    from axrange.o2control.taps import tap_status

    status = tap_status(db_path=args.db, target=args.target, deploy_id=args.deploy_id)
    if getattr(args, "json", False):
        _print_json(dataclasses.asdict(status))
        return 0
    _render_tap_status(status)
    return 0


def cmd_tap_info(args: argparse.Namespace) -> int:
    from axrange.platform import ConfigError, load_config
    from axrange.o2control.control import surface_status
    from axrange.o2control.taps import (
        TapPlanError, latest_tap_plan_deploy, load_tap_plan, merge_external_sensors,
        resolve_tap_plan_bindings, tap_info_from_deploy, tap_info_from_resolution,
    )
    from axrange.planner import StateStore

    # Loaded once up front, same allowlist `tap validate`/`tap plan`/`tap
    # apply` resolve against (LES-069) -- every `sensor_by_id` build below
    # (there are three call shapes in this function) merges it in, so `tap
    # info` never shows an "unknown sensor" gap for a config-external one
    # that IS correctly wired on the wire.
    try:
        cfg = load_config(args.adapter_config)
    except ConfigError as e:
        sys.stderr.write(f"axrange tap info: adapter.conf: {e}\n")
        return 2

    arg = args.id_or_spec
    store = StateStore(args.db)
    try:
        if arg and Path(arg).is_file():
            try:
                spec = load_tap_plan(arg)
            except TapPlanError as e:
                sys.stderr.write(f"axrange tap info: {e.message}\n")
                return 2 if e.status < 500 else 1
            existing = store.find_deploy_for_plan(f"tap-{spec.target}")
            if existing is not None:
                surface = surface_status(db_path=args.db)
                sensor_by_id = {s.sensor_id: s for s in surface.sensors}
                merge_warnings: list[str] = []
                sensor_by_id = merge_external_sensors(
                    sensor_by_id, cfg.external_sensors, warnings=merge_warnings,
                )
                te = store.get_deploy(spec.target)
                info = tap_info_from_deploy(
                    store, existing.deploy_id, target=spec.target,
                    target_status=te.status.value if te else "unknown",
                    sensor_by_id=sensor_by_id, warnings=merge_warnings,
                )
            else:
                try:
                    resolution = resolve_tap_plan_bindings(
                        spec, store, db_path=args.db,
                        external_sensors=cfg.external_sensors,
                    )
                except TapPlanError as e:
                    sys.stderr.write(f"axrange tap info: {e.message}\n")
                    return 2 if e.status < 500 else 1
                info = tap_info_from_resolution(resolution, target=spec.target)
        else:
            did = arg
            target = None
            if did is not None:
                # The positional id may be either a tap deploy id
                # (`tap-<twin>-<hash>`) or a TWIN/target id (the tap plan's
                # `target`, e.g. the te deploy id). A twin id has a non-`tap-`
                # plan_id, so deriving `target` from its plan_id below yields
                # "?" and reads tap bindings off a te deploy (none) -- the
                # reported bug. Resolve a non-tap id to its applied tap deploy
                # the SAME way `tap status --target` does (find_deploy_for_plan).
                d0 = store.get_deploy(did)
                if d0 is not None and d0.plan_id.startswith("tap-"):
                    target = d0.plan_id[len("tap-"):]
                else:
                    existing = store.find_deploy_for_plan(f"tap-{did}")
                    if existing is None:
                        sys.stderr.write(
                            f"axrange tap info: '{did}' is not a tap-plan deploy, "
                            "and no tap plan targets it. Pass a tap deploy id "
                            "(tap-<twin>-<hash>) or a twin id with an applied "
                            "tap plan.\n"
                        )
                        return 2
                    target = did
                    did = existing.deploy_id
            else:
                row = latest_tap_plan_deploy(store)
                did = row.deploy_id if row is not None else None
            if did is None:
                sys.stdout.write("No tap-plan deploys found.\n")
                return 0
            d = store.get_deploy(did)
            if d is None:
                sys.stderr.write(f"axrange tap info: deploy not found: {did}\n")
                return 2
            if target is None:
                target = d.plan_id[len("tap-"):] if d.plan_id.startswith("tap-") else "?"
            te = store.get_deploy(target)
            surface = surface_status(db_path=args.db)
            sensor_by_id = {s.sensor_id: s for s in surface.sensors}
            merge_warnings = []
            sensor_by_id = merge_external_sensors(
                sensor_by_id, cfg.external_sensors, warnings=merge_warnings,
            )
            info = tap_info_from_deploy(
                store, did, target=target,
                target_status=te.status.value if te else "unknown",
                sensor_by_id=sensor_by_id, warnings=merge_warnings,
            )
    finally:
        store.close()

    # Part C (docs/WORK_ORDER_access_facts.md): resolve name/mgmt-ip/access
    # facts ONCE for the external sensor ids actually BOUND in this plan
    # (`only_ids` -- code review efficiency trim, 2026-07-25: no reason to
    # pay every declared EXTERNAL_SENSORS entry's best-effort resolve cost
    # for one that isn't even a target here) -- a binding below just looks
    # its `sid` up in the result, rather than re-resolving per binding
    # occurrence (fan-out could bind the same external sink several times).
    bound_sensor_ids = {sid for b in info.bindings for sid in b.sensors}
    ext_facts_by_id = {
        f["id"]: f for f in _external_sensor_facts(cfg, only_ids=bound_sensor_ids)
    }

    if getattr(args, "json", False):
        d = dataclasses.asdict(info)
        d["external_sensors"] = ext_facts_by_id
        _print_json(d)
        return 0

    no_secrets = bool(getattr(args, "no_secrets", False))
    sys.stdout.write(f"Tap-plan: {info.deploy_id or '(not applied)'} -- {info.status}\n")
    sys.stdout.write(f"  target: {info.target} (status={info.target_status})\n")
    if not info.bindings:
        sys.stdout.write("  bindings: (none)\n")
    else:
        sys.stdout.write("  bindings:\n")
        for b in info.bindings:
            sys.stdout.write(
                f"    - {b.host}/{b.net_id}[nic{b.nic_index}] -> "
                f"{', '.join(b.sensors)}\n"
            )
            for sid in b.sensors:
                line = (
                    f"        {sid}: gre={b.gre_ports.get(sid) or '(unresolved)'} "
                    f"ip={b.sensor_transport_ips.get(sid) or '(unassigned)'} "
                    f"direction={b.directions.get(sid) or '?'}"
                )
                # A config-external sensor gets NO verify-capture heartbeat
                # (axRange never QGA-reads an operator-managed sink) -- flag
                # it explicitly rather than leaving it looking identical to
                # a heartbeat-confirmed A1 sensor (LES-001 family, code
                # review, follow-on to the config-external-sensor feature).
                if not b.capture_verified.get(sid, True):
                    line += "  [capture: UNVERIFIED (external sink)]"
                sys.stdout.write(line + "\n")
                ext = ext_facts_by_id.get(sid)
                if ext is not None:
                    pw = "********" if no_secrets else ext["access_password"]
                    sys.stdout.write(
                        f"            resolved: name={ext['resolved_name'] or '(unresolved)'} "
                        f"mgmt_ip={ext['resolved_ip'] or '(unresolved)'}\n"
                    )
                    sys.stdout.write(
                        f"            access: {ext['access_hint']}  (password={pw})\n"
                    )
    if info.warnings:
        sys.stdout.write("\n")
        for w in info.warnings:
            sys.stdout.write(f"[warn] {w}\n")
    sys.stdout.write(f"\n{info.fanout_note}\n")
    return 0


def cmd_tap_destroy(args: argparse.Namespace) -> int:
    from axrange.o2control.taps import TapPlanError, destroy_taps, latest_tap_plan_deploy
    from axrange.planner import StateStore

    deploy_id = args.deploy_id
    if deploy_id is None and not args.target:
        # Bare default: PREFER the newest NON-destroyed tap-plan deploy --
        # see `cli.main._latest_te_deploy`'s docstring for why
        # (`mark_destroyed` bumps `updated_at`, so a just-destroyed
        # tombstone would otherwise outrank a genuinely live, newer
        # tap-plan deploy). `tap status`/`tap info` deliberately do NOT do
        # this.
        store = StateStore(args.db)
        try:
            latest_any = latest_tap_plan_deploy(store)
            if latest_any is None:
                sys.stdout.write("No tap-plan deploys found.\n")
                return 0
            latest_active = latest_tap_plan_deploy(store, exclude_destroyed=True)
        finally:
            store.close()
        if latest_active is None:
            sys.stdout.write(
                f"latest tap-plan deploy {latest_any.deploy_id} is already "
                "destroyed; nothing to do (pass an explicit deploy_id or "
                "--target to act on a specific deploy).\n"
            )
            return 0
        deploy_id = latest_active.deploy_id

    if not args.allow_destroy:
        sys.stderr.write(
            "axrange tap destroy: refusing to tear down without "
            "--allow-destroy.\n"
            "  This removes the tap-plan's tap_-tier gretaps/mirrors (never "
            "the target te_ deploy or the o2_ sensors).\n"
        )
        return 2

    try:
        result = destroy_taps(
            db_path=args.db, target=args.target, deploy_id=deploy_id,
            adapter_config=args.adapter_config, node=args.node,
            log=lambda m: sys.stdout.write(m + "\n"),
        )
    except TapPlanError as e:
        sys.stderr.write(f"axrange tap destroy: {e.message}\n")
        return 2 if e.status < 500 else 1

    errs = result.errors
    sys.stdout.write(
        f"\n  {result.removed} resource(s) removed; {len(errs)} not cleared.\n"
    )
    if result.destroyed:
        sys.stdout.write(f"Result: DESTROYED ({result.deploy_id})\n")
        return 0
    sys.stdout.write(
        "Result: INCOMPLETE — some resources were not cleared; re-run "
        "`axrange tap destroy` to finish (idempotent).\n"
    )
    return 1


# ---------------------------------------------------------------------------
# tfc (the Traffic tier, `docs/WORK_ORDER_traffic_tier.md`)
# ---------------------------------------------------------------------------
def _render_traffic_status(status) -> None:
    sys.stdout.write(f"Traffic-profile deploy: {status.deploy_id or '(none)'}\n")
    sys.stdout.write(f"  twin: {status.twin}\n")
    sys.stdout.write(f"  status: {status.status}\n")
    if not status.assignments:
        sys.stdout.write("  assignments: (none)\n")
    else:
        sys.stdout.write("  assignments:\n")
        for a in status.assignments:
            sys.stdout.write(f"    - {a.target:24s} -> {', '.join(a.hosts)}\n")
    for w in getattr(status, "warnings", None) or []:
        sys.stdout.write(f"  [warn] {w}\n")


def _tfc_manifests(args: argparse.Namespace, label: str) -> tuple[Any, Optional[int]]:
    """`(ManifestRegistry | None, exit code to stop with | None)` from a tfc
    verb's `--images` -- the host-OS source for the persona os_family check.
    Omitted -> `None` (the check falls back to the twin's recorded Windows
    hosts live, and the static twin check is skipped with a warning)."""
    images = getattr(args, "images", None)
    if not images:
        return None, None
    from axrange.manifest.loader import ManifestError, load_registry

    try:
        return load_registry(root=Path(images)), None
    except ManifestError as e:
        sys.stderr.write(f"axrange {label}: --images {images}: {e}\n")
        return None, 2


def _tfc_static_os_check(args: argparse.Namespace, spec: Any, manifests: Any) -> int:
    """`tfc validate`'s OFFLINE persona-os check (corp-twin live run 3): for a
    profile inside a twin folder, every assignment's persona os_family against
    the image OS of each host the twin's te.yaml declares for it. Prints and
    returns 1 on a mismatch, else 0. Needs `--images` (the manifests carry the
    OS); without it, it says so and skips -- the live resolve still refuses a
    Linux persona on a host the twin recorded as Windows."""
    from axrange.o2control.traffic import (
        check_persona_os_static, static_hosts_from_twin_config,
    )
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file

    twin_dir = twin_dir_for_file(args.spec)
    if twin_dir is None:
        return 0
    try:
        te_path = load_twin_manifest(twin_dir).te_path
    except TwinConfigError:
        return 0
    if manifests is None:
        sys.stdout.write(
            "\n[warn] persona os check vs the twin's images skipped: pass --images "
            "<root> (the root `te deploy` used)\n"
        )
        return 0
    from axrange.planner.pass1_emit import _os_family, _parse_image_ref

    hosts = static_hosts_from_twin_config(te_path)
    errors, notes = check_persona_os_static(
        spec, hosts, lambda image: _os_family(_parse_image_ref(image)[0], manifests),
    )
    for n in notes:
        sys.stdout.write(f"  [note] {n}\n")
    if errors:
        for e in errors:
            sys.stderr.write(f"[error] {e}\n")
        sys.stdout.write(f"\nResult: FAIL ({len(errors)} persona/os mismatch(es) vs {te_path.name})\n")
        return 1
    sys.stdout.write(f"persona os check vs {te_path.name}: OK ({len(hosts)} declared hosts)\n")
    return 0


def cmd_tfc_validate(args: argparse.Namespace) -> int:
    """Two-tier, same posture as `tap validate` (work order §3.3): static
    `parse_traffic_profile`/`load_traffic_profile` ALWAYS; `resolve_traffic_
    bindings` against the state DB when it's reachable, degrading to a
    warning (not a hard error) so an offline lint of a profile file still
    works."""
    from axrange.o2control.traffic import (
        TrafficPlanError, TrafficProfileSpec, assignment_label, load_traffic_profile,
        resolve_traffic_bindings, resolve_traffic_target,
    )
    from axrange.planner import StateStore

    try:
        spec = load_traffic_profile(args.spec)
    except TrafficPlanError as e:
        sys.stderr.write(f"axrange tfc validate: {e.message}\n")
        return 2 if e.status < 500 else 1

    effective_target = getattr(args, "target", None) or spec.target
    sys.stdout.write(
        f"traffic-profile {args.spec}: static OK "
        f"(target={effective_target or '(none -- pass --target at deploy)'}, "
        f"{len(spec.assignments)} assignment(s), {len(spec.replay)} replay binding(s))\n"
    )
    for a in spec.assignments:
        sys.stdout.write(f"  {assignment_label(a)} -> persona:{a.persona}\n")
    for r in spec.replay:
        sys.stdout.write(f"  replay:{r.template} -> role:{r.onto_role}\n")

    manifests, rc = _tfc_manifests(args, "tfc validate")
    if rc is not None:
        return rc
    if _tfc_static_os_check(args, spec, manifests):
        return 1

    # Two modes, matching `tap validate` (`docs/WORK_ORDER_tap_target_
    # resolution.md`): a profile with NO effective target is a legitimate
    # predefined library file, so static-lint it and stop -- never fail it
    # for a target it was deliberately authored without.
    if not effective_target:
        sys.stdout.write(
            "\nno target (profile declares none and no --target given); "
            "static lint only -- pass --target to also resolve against a "
            "live twin\n"
        )
        return 0

    db_path = Path(args.db)
    if not db_path.is_file():
        sys.stdout.write(
            f"\n[warn] state DB not found at {db_path}; skipping resolve "
            "checks (static lint only)\n"
        )
        return 0

    try:
        store = StateStore(str(db_path))
    except Exception as e:  # noqa: BLE001 -- degrades to a static-only lint
        sys.stdout.write(
            f"\n[warn] state DB unreadable ({e}); skipping resolve checks "
            "(static lint only)\n"
        )
        return 0
    try:
        try:
            concrete = resolve_traffic_target(store, effective_target)
            if concrete != spec.target:
                # `replace()` carries EVERY field (incl. the W1 `roles:` map);
                # hand-listing dropped it on a NAME target (W1 live-verify,
                # 2026-08-04, LES-037).
                spec = dataclasses.replace(spec, target=concrete)
            resolution = resolve_traffic_bindings(spec, store, manifests=manifests)
        except TrafficPlanError as e:
            sys.stderr.write(f"\n[error] {e.message}\n")
            return 1
    finally:
        store.close()

    sys.stdout.write("\nresolved against live state:\n")
    sys.stdout.write(f"  target twin {spec.target}: status={resolution.target_status}\n")
    if resolution.target_status != "completed":
        sys.stdout.write(
            f"  [warn] target twin status is {resolution.target_status!r}, not 'completed'\n"
        )
    for ra in resolution.resolved:
        sys.stdout.write(f"  {ra.host_id} (vmid={ra.vmid}) -> {ra.persona.id}\n")
    for w in resolution.warnings:
        sys.stdout.write(f"  [warn] {w}\n")
    sys.stdout.write("\nResult: PASS\n")
    return 0


def cmd_tfc_plan(args: argparse.Namespace) -> int:
    from axrange.o2control.traffic import (
        TrafficPlanError, TrafficProfileSpec, compose_traffic_ops, load_traffic_profile,
        resolve_traffic_bindings, resolve_traffic_target,
    )
    from axrange.planner import StateStore

    try:
        spec = load_traffic_profile(args.spec)
    except TrafficPlanError as e:
        sys.stderr.write(f"axrange tfc plan: {e.message}\n")
        return 2 if e.status < 500 else 1

    effective_target = getattr(args, "target", None) or spec.target
    if not effective_target:
        sys.stderr.write(
            "axrange tfc plan: no traffic-profile target: set target: in the "
            "profile or pass --target\n"
        )
        return 2
    manifests, rc = _tfc_manifests(args, "tfc plan")
    if rc is not None:
        return rc

    store = StateStore(args.db)
    try:
        try:
            # Resolve a scenario/twin-config NAME to its concrete deploy id
            # before composing -- `plan_id` keys off the minted id (LES-072).
            concrete = resolve_traffic_target(store, effective_target)
            if concrete != spec.target:
                # `replace()` carries EVERY field (incl. the W1 `roles:` map);
                # hand-listing dropped it on a NAME target (W1 live-verify,
                # 2026-08-04, LES-037).
                spec = dataclasses.replace(spec, target=concrete)
            resolution = resolve_traffic_bindings(spec, store, manifests=manifests)
        except TrafficPlanError as e:
            sys.stderr.write(f"axrange tfc plan: {e.message}\n")
            return 2 if e.status < 500 else 1
        plan = compose_traffic_ops(resolution, f"tfc-{spec.target}")
    finally:
        store.close()

    sys.stdout.write(f"\nTraffic profile: tfc-{spec.target} (twin={spec.target})\n")
    sys.stdout.write(f"  ops={len(plan.ops)} barriers={len(plan.barriers)} edges={len(plan.edges)}\n\n")
    sys.stdout.write(plan.render_tree() + "\n")
    for w in resolution.warnings:
        sys.stdout.write(f"[warn] {w}\n")
    return 0


def cmd_tfc_deploy(args: argparse.Namespace) -> int:
    from axrange.o2control.traffic import TrafficPlanError, apply_traffic_profile

    rc = _containment_gate(args, "tfc")
    if rc is not None:
        return rc
    manifests, rc = _tfc_manifests(args, "tfc deploy")
    if rc is not None:
        return rc
    sys.stdout.write(f"Applying traffic profile {args.spec}...\n")
    try:
        status = apply_traffic_profile(
            spec_path=args.spec, db_path=args.db,
            adapter_config=args.adapter_config, target_node=args.node,
            target=getattr(args, "target", None), manifests=manifests,
            allow_revert_host_edits=getattr(args, "revert_host_edits", False),
        )
    except TrafficPlanError as e:
        sys.stderr.write(f"axrange tfc deploy: {e.message}\n")
        return 2 if e.status < 500 else 1

    sys.stdout.write("\n")
    _render_traffic_status(status)
    return 0 if status.status == "completed" else 1


def _render_host_edit_plan(plan: Any) -> None:
    d = plan.to_dict()
    r = d["role"]
    sys.stdout.write(
        f"Host {d['host_id']} on twin {d['deploy_id']} "
        f"(tfc deploy: {d['tfc_deploy_id'] or '(none yet -- will be created)'})\n"
    )
    sys.stdout.write(
        f"  role: {r['current'] or '-'}"
        + (f" -> {r['new'] or '-'}" if r["changed"] else " (unchanged)") + "\n"
    )
    for x in d["start"]:
        sys.stdout.write(f"  start  {x['instance_id']} ({x['reason']})\n")
    for x in d["stop"]:
        sys.stdout.write(f"  stop   {x['instance_id']}\n")
    u = d["unchanged"]
    sys.stdout.write(
        f"  untouched: {u['instances']} persona instance(s) on {u['hosts']} other host(s)\n"
    )
    for dep in d["dependents"]:
        roles = ", ".join(sorted(dep["stale_peers"]))
        verb = "re-driven" if d["will_update_dependents"] else "left stale (--update-dependents)"
        if dep.get("no_new_holder"):
            verb = ("NO host holds the role after this change -- it would keep the old "
                    "address, which no longer has the role")
        sys.stdout.write(f"  dependent {dep['instance_id']}: peer(s) {roles} change -- {verb}\n")
    for w in d["warnings"]:
        sys.stdout.write(f"  [warn] {w}\n")
    for ref in d["refusals"]:
        sys.stdout.write(f"  REFUSED ({ref['code']}): {ref['message']}\n")


def cmd_tfc_set_host(args: argparse.Namespace) -> int:
    """`axrange tfc set-host <target> <host> --persona P [...]` -- the CLI
    peer of `PUT /api/deploys/{id}/hosts/{host}/personas` (Unified Console
    ruling §3.3): one host's personas replaced, every other host untouched.
    Same parity as `tfc deploy`: containment gate, `--images` OS check, the
    twin folder's own `personas/` (as a per-process overlay)."""
    from axrange.o2control.traffic import (
        HostPersonaAssignment, TrafficPlanError, apply_host_personas,
        current_host_assignments, plan_host_personas, resolve_traffic_target,
        twin_persona_overlay,
    )
    from axrange.personas import registry as persona_registry
    from axrange.planner import StateStore

    if not args.persona and not args.no_personas and args.role is None and not args.clear_role:
        sys.stderr.write(
            "axrange tfc set-host: name the host's personas (--persona, repeatable), "
            "or --no-personas to stop them all, or a role change\n"
        )
        return 2
    if args.persona and args.no_personas:
        sys.stderr.write("axrange tfc set-host: --persona and --no-personas conflict\n")
        return 2
    manifests, rc = _tfc_manifests(args, "tfc set-host")
    if rc is not None:
        return rc
    if not Path(args.db).exists():
        sys.stderr.write(f"axrange tfc set-host: no state DB at {args.db}\n")
        return 2
    role = "" if args.clear_role else args.role

    # Twin-local personas, resolved as an overlay (never the global registry).
    overlay: dict[str, Any] = {}
    store = StateStore(args.db)
    try:
        try:
            te_id = resolve_traffic_target(store, args.target)
        except TrafficPlanError as e:
            sys.stderr.write(f"axrange tfc set-host: {e.message}\n")
            return 2
        te = store.get_deploy(te_id)
    finally:
        store.close()
    overlay, overlay_warnings, _found = twin_persona_overlay(
        te.scenario_id if te else None, getattr(args, "twins_dir", None),
    )
    for w in overlay_warnings:
        if "failed to load" in w:
            sys.stderr.write(f"[warn] {w}\n")

    # With no --persona and a role-only change, keep the host's current set.
    keep_current = not args.persona and not args.no_personas
    with persona_registry.overlay(overlay):
        store = StateStore(args.db)
        try:
            assignments = (
                current_host_assignments(store, te_id, args.host) if keep_current
                else [HostPersonaAssignment(persona=p, schedule=args.schedule)
                      for p in args.persona]
            )
            try:
                plan = plan_host_personas(
                    store, te_id, args.host, assignments, role=role,
                    update_dependents=args.update_dependents, manifests=manifests,
                    allow_role_orphaning=args.allow_role_orphaning,
                )
            except TrafficPlanError as e:
                sys.stderr.write(f"axrange tfc set-host: {e.message}\n")
                return 2 if e.status < 500 else 1
        finally:
            store.close()
        if args.plan or not plan.ok:
            if getattr(args, "json", False):
                _print_json(plan.to_dict())
            else:
                _render_host_edit_plan(plan)
            return 0 if plan.ok else 2
        try:
            res = apply_host_personas(
                db_path=args.db, target=te_id, host_id=args.host,
                assignments=assignments, role=role,
                update_dependents=args.update_dependents,
                adapter_config=args.adapter_config, target_node=args.node,
                manifests=manifests, allow_uncontained=args.allow_uncontained,
                allow_role_orphaning=getattr(args, "allow_role_orphaning", False),
            )
        except TrafficPlanError as e:
            sys.stderr.write(f"axrange tfc set-host: {e.message}\n")
            return 2 if e.status < 500 else 1
    if getattr(args, "json", False):
        _print_json({"tfc_deploy_id": res.tfc_deploy_id, "status": res.status,
                     "plan": res.plan.to_dict(), "warnings": res.warnings})
    else:
        _render_host_edit_plan(res.plan)
        for w in res.warnings:
            sys.stdout.write(f"  [warn] {w}\n")
        sys.stdout.write(f"\ntfc deploy {res.tfc_deploy_id}: {res.status}\n")
    return 0 if res.status == "completed" else 1


def cmd_tfc_status(args: argparse.Namespace) -> int:
    from axrange.o2control.traffic import traffic_status

    status = traffic_status(db_path=args.db, target=args.target, deploy_id=args.deploy_id)
    if getattr(args, "json", False):
        _print_json(dataclasses.asdict(status))
        return 0
    _render_traffic_status(status)
    return 0


def cmd_tfc_info(args: argparse.Namespace) -> int:
    from axrange.o2control.traffic import (
        TrafficPlanError, TrafficProfileSpec, latest_traffic_deploy, load_traffic_profile,
        resolve_traffic_bindings, resolve_traffic_target, traffic_info_from_deploy,
        traffic_info_from_resolution,
    )
    from axrange.planner import StateStore

    arg = args.id_or_spec
    store = StateStore(args.db)
    try:
        if arg and Path(arg).is_file():
            try:
                spec = load_traffic_profile(arg)
            except TrafficPlanError as e:
                sys.stderr.write(f"axrange tfc info: {e.message}\n")
                return 2 if e.status < 500 else 1
            effective_target = getattr(args, "target", None) or spec.target
            if not effective_target:
                sys.stderr.write(
                    "axrange tfc info: profile declares no target: and no "
                    "--target given -- nothing to look up\n"
                )
                return 2
            try:
                concrete = resolve_traffic_target(store, effective_target)
            except TrafficPlanError as e:
                sys.stderr.write(f"axrange tfc info: {e.message}\n")
                return 2 if e.status < 500 else 1
            if concrete != spec.target:
                # `replace()` carries EVERY field (incl. the W1 `roles:` map);
                # hand-listing dropped it on a NAME target (W1 live-verify,
                # 2026-08-04, LES-037).
                spec = dataclasses.replace(spec, target=concrete)
            existing = store.find_deploy_for_plan(f"tfc-{spec.target}")
            if existing is not None:
                twin = store.get_deploy(spec.target)
                info = traffic_info_from_deploy(
                    store, existing.deploy_id, target=spec.target,
                    target_status=twin.status.value if twin else "unknown",
                )
            else:
                try:
                    resolution = resolve_traffic_bindings(spec, store)
                except TrafficPlanError as e:
                    sys.stderr.write(f"axrange tfc info: {e.message}\n")
                    return 2 if e.status < 500 else 1
                info = traffic_info_from_resolution(resolution, target=spec.target)
        else:
            did = arg
            if did is None:
                row = latest_traffic_deploy(store)
                did = row.deploy_id if row is not None else None
            if did is None:
                sys.stdout.write("No traffic-profile deploys found.\n")
                return 0
            d = store.get_deploy(did)
            if d is None:
                sys.stderr.write(f"axrange tfc info: deploy not found: {did}\n")
                return 2
            target = d.plan_id[len("tfc-"):] if d.plan_id.startswith("tfc-") else "?"
            twin = store.get_deploy(target)
            info = traffic_info_from_deploy(
                store, did, target=target,
                target_status=twin.status.value if twin else "unknown",
            )
    finally:
        store.close()

    if getattr(args, "json", False):
        _print_json(dataclasses.asdict(info))
        return 0

    sys.stdout.write(f"Traffic profile: {info.deploy_id or '(not applied)'} -- {info.status}\n")
    sys.stdout.write(f"  twin: {info.target} (status={info.target_status})\n")
    if not info.assignments:
        sys.stdout.write("  assignments: (none)\n")
    else:
        sys.stdout.write("  assignments:\n")
        for a in info.assignments:
            peers = ", ".join(f"{k}={v}" for k, v in sorted(a.peers.items())) or "(none)"
            sys.stdout.write(
                f"    - {a.host_id}/{a.persona} (vmid={a.vmid}, instance={a.instance_id})\n"
                f"        peers: {peers}\n"
            )
    for w in info.warnings:
        sys.stdout.write(f"  [warn] {w}\n")
    # Nice-to-have (code review 2026-07-25): `tfc info` shows persona/host/
    # vmid assignments, not per-node ip/user/password -- point at the verb
    # that actually has those (Part D's `te info`). Text output only; --json
    # already returned above.
    sys.stdout.write(
        f"  (run 'axrange te info {info.target}' for per-node access facts: "
        "ip / user / password)\n"
    )
    return 0


def cmd_tfc_destroy(args: argparse.Namespace) -> int:
    from axrange.o2control.traffic import (
        TrafficPlanError, destroy_traffic_profile, latest_traffic_deploy,
    )
    from axrange.planner import StateStore

    deploy_id = args.deploy_id
    if deploy_id is None and not args.target:
        # Bare default: PREFER the newest NON-destroyed tfc deploy -- same
        # "just-destroyed tombstone must not outrank a live deploy" posture
        # as `tap destroy`'s bare-default (LES-084).
        store = StateStore(args.db)
        try:
            latest_any = latest_traffic_deploy(store)
            if latest_any is None:
                sys.stdout.write("No traffic-profile deploys found.\n")
                return 0
            latest_active = latest_traffic_deploy(store, exclude_destroyed=True)
        finally:
            store.close()
        if latest_active is None:
            sys.stdout.write(
                f"latest traffic-profile deploy {latest_any.deploy_id} is "
                "already destroyed; nothing to do (pass an explicit deploy_id "
                "or --target to act on a specific deploy).\n"
            )
            return 0
        deploy_id = latest_active.deploy_id

    if not args.allow_destroy:
        sys.stderr.write(
            "axrange tfc destroy: refusing to tear down without "
            "--allow-destroy.\n"
            "  This stops the traffic profile's persona instances (never the "
            "target twin or any other tier).\n"
        )
        return 2

    try:
        result = destroy_traffic_profile(
            db_path=args.db, target=args.target, deploy_id=deploy_id,
            adapter_config=args.adapter_config, node=args.node,
            log=lambda m: sys.stdout.write(m + "\n"),
        )
    except TrafficPlanError as e:
        sys.stderr.write(f"axrange tfc destroy: {e.message}\n")
        return 2 if e.status < 500 else 1

    errs = result.errors
    sys.stdout.write(
        f"\n  {result.removed} resource(s) removed; {len(errs)} not cleared.\n"
    )
    if result.destroyed:
        sys.stdout.write(f"Result: DESTROYED ({result.deploy_id})\n")
        return 0
    sys.stdout.write(
        "Result: INCOMPLETE — some resources were not cleared; re-run "
        "`axrange tfc destroy` to finish (idempotent).\n"
    )
    return 1


# ===========================================================================
# exr (the Exercise tier -- docs/WORK_ORDER_exercise_tier.md). {run|deploy,
# status, destroy} only -- see `_add_exr_deploy`'s comment for the scope call.
# ===========================================================================


def _render_exercise_status(status) -> None:
    sys.stdout.write(f"Exercise deploy: {status.deploy_id or '(none)'}\n")
    sys.stdout.write(f"  twin: {status.twin}\n")
    sys.stdout.write(f"  status: {status.status}\n")
    if status.playout_hash:
        sys.stdout.write(f"  playout_hash: {status.playout_hash}\n")
    if status.activity_host:
        sys.stdout.write(f"  activity host: {status.activity_host}\n")
    if status.adversary_targets:
        sys.stdout.write(f"  adversary targets: {', '.join(status.adversary_targets)}\n")
    if status.measurement:
        sys.stdout.write(f"  measurement: {status.measurement}\n")
    if status.defense_notes:
        sys.stdout.write(f"  defense notes: {status.defense_notes}\n")
    for w in getattr(status, "warnings", None) or []:
        sys.stdout.write(f"  [warn] {w}\n")


def cmd_exr_deploy(args: argparse.Namespace) -> int:
    from axrange.o2control.exercise import ExercisePlanError, apply_exercise

    sys.stdout.write(f"Running exercise {args.spec}...\n")
    try:
        status = apply_exercise(
            spec_path=args.spec, db_path=args.db,
            adapter_config=args.adapter_config, target_node=args.node,
            target=getattr(args, "target", None),
        )
    except ExercisePlanError as e:
        sys.stderr.write(f"axrange exr deploy: {e.message}\n")
        return 2 if e.status < 500 else 1

    sys.stdout.write("\n")
    _render_exercise_status(status)
    return 0 if status.status == "completed" else 1


def cmd_exr_status(args: argparse.Namespace) -> int:
    from axrange.o2control.exercise import exercise_status

    status = exercise_status(db_path=args.db, deploy_id=args.deploy_id, twin=args.target)
    if getattr(args, "json", False):
        _print_json(dataclasses.asdict(status))
        return 0
    _render_exercise_status(status)
    return 0


def cmd_exr_destroy(args: argparse.Namespace) -> int:
    from axrange.o2control.exercise import (
        ExercisePlanError, destroy_exercise, latest_exercise_deploy,
    )
    from axrange.planner import StateStore

    deploy_id = args.deploy_id
    if deploy_id is None:
        # Bare/--target default: PREFER the newest NON-destroyed exr deploy
        # (LES-084 -- a just-destroyed tombstone must not outrank a live run).
        store = StateStore(args.db)
        try:
            latest_any = latest_exercise_deploy(store, twin=args.target)
            if latest_any is None:
                sys.stdout.write("No exercise deploys found.\n")
                return 0
            latest_active = latest_exercise_deploy(
                store, twin=args.target, exclude_destroyed=True,
            )
        finally:
            store.close()
        if latest_active is None:
            sys.stdout.write(
                f"latest exercise deploy {latest_any.deploy_id} is already "
                "destroyed; nothing to do (pass an explicit deploy_id to act "
                "on a specific run).\n"
            )
            return 0
        deploy_id = latest_active.deploy_id

    if not args.allow_destroy:
        sys.stderr.write(
            "axrange exr destroy: refusing to tear down without "
            "--allow-destroy.\n"
            "  This stops this exercise run's attack playout (never the "
            "target twin or any other tier).\n"
        )
        return 2

    try:
        result = destroy_exercise(
            db_path=args.db, deploy_id=deploy_id,
            adapter_config=args.adapter_config, node=args.node,
            log=lambda m: sys.stdout.write(m + "\n"),
        )
    except ExercisePlanError as e:
        sys.stderr.write(f"axrange exr destroy: {e.message}\n")
        return 2 if e.status < 500 else 1

    errs = result.errors
    sys.stdout.write(
        f"\n  {result.removed} resource(s) removed; {len(errs)} not cleared.\n"
    )
    if result.destroyed:
        sys.stdout.write(f"Result: DESTROYED ({result.deploy_id})\n")
        return 0
    sys.stdout.write(
        "Result: INCOMPLETE — some resources were not cleared; re-run "
        "`axrange exr destroy` to finish (idempotent).\n"
    )
    return 1


# ===========================================================================
# serve (control-plane API)
# ===========================================================================


def cmd_serve(args: argparse.Namespace) -> int:
    from axrange.api.serve import serve

    return serve(
        host=args.host,
        port=args.port,
        db_path=args.db,
        scenarios_dir=args.scenarios_dir,
        images_root=args.images,
        adapter_config=args.adapter_config,
        api_token=args.api_token,
        spa_dir=args.spa_dir,
        twin_configs_dir=args.twin_configs_dir,
        imprints_dir=args.imprints_dir,
        targets_file=getattr(args, "targets_file", None),
        extra_dbs=getattr(args, "extra_dbs", None),
    )


# ===========================================================================
# twin-config (catalog browsing; deploying goes through te {validate,plan,
# deploy} --config, docs/WORK_ORDER_twin_configs.md §2)
# ===========================================================================


def cmd_twin_config(args: argparse.Namespace) -> int:
    if getattr(args, "twin_config_command", None) == "list":
        return cmd_twin_config_list(args)
    if getattr(args, "twin_config_command", None) == "show":
        return cmd_twin_config_show(args)
    sys.stderr.write("axrange twin-config: unknown subcommand\n")
    return 2


def cmd_twin_config_list(args: argparse.Namespace) -> int:
    from axrange.twinconfig.registry import list_twin_configs

    entries = list_twin_configs(getattr(args, "twin_configs_dir", None))
    if not entries:
        sys.stdout.write("No twin-configs found.\n")
        return 0
    for e in entries:
        first_line = (e.description or "").strip().splitlines()[0] if e.description else ""
        sys.stdout.write(f"  {e.id:20s} {first_line}\n")
        if e.error:
            # Listed, never shown healthy: `--config` would refuse this one.
            sys.stdout.write(f"  {'':20s} [BROKEN twin.yaml] {e.error}\n")
    return 0


def cmd_twin_config_show(args: argparse.Namespace) -> int:
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.registry import load_twin_config

    try:
        tc = load_twin_config(args.id, catalog_dir=getattr(args, "twin_configs_dir", None))
    except TwinConfigError as e:
        sys.stderr.write(f"axrange twin-config show: {e}\n")
        return 2

    # The persona cross-check that used to live here is GONE with the
    # `nodes[].persona` field it read (2026-08-04,
    # `docs/WORK_ORDER_role_persona_tier.md`): a twin config declares no
    # behavior at all any more, so there is nothing here to cross-check.
    # Personas are validated for real -- against the live registry, fail-loud
    # rather than advisory -- at `axrange tfc validate|plan|deploy`, where
    # they are now authored.

    # Resolved BEFORE anything is printed, so a bad `--param` exits 2 without
    # first emitting a body that implies it worked.
    report, gate_rc, gate_note = _resolve_deploy_gate(args, tc.id)
    if gate_rc:
        return gate_rc

    if getattr(args, "json", False):
        # `--json` carries the gate as a `deploy_gate` key -- the gate's one
        # machine-readable surface, and what gives `DeployGateReport.to_dict()`
        # a real consumer. `null` when the gate could not be computed; the
        # reason goes to stderr rather than being folded into the payload.
        payload = tc.model_dump(mode="json")
        payload["deploy_gate"] = report.to_dict() if report is not None else None
        if gate_note:
            sys.stderr.write(f"axrange twin-config show: deploy gate: {gate_note}\n")
        _print_json(payload)
        return 0

    sys.stdout.write(f"twin-config: {tc.id}\n")
    if tc.description:
        for line in tc.description.strip().splitlines():
            sys.stdout.write(f"  {line}\n")
    # Annotated with the overrides actually in force (S9): the `params:` block
    # is the DECLARED table, so printing `{'DEPLOY_MIN_OCCUPANCY': 0.8}` two
    # lines above a gate section computed at 0.5 reads as a contradiction.
    overrides = _param_overrides_note(args)
    sys.stdout.write(f"  params: {dict(tc.params)}{overrides}\n")
    sys.stdout.write(f"  segments ({len(tc.segments)}):\n")
    for s in tc.segments:
        gate = f" [when: {s.when}]" if s.when else ""
        sys.stdout.write(f"    - {s.name} ({s.cidr}, {s.zone_type or 'simple'}){gate}\n")
    sys.stdout.write(f"  nodes ({len(tc.nodes)}):\n")
    for n in tc.nodes:
        gate = f" [when: {n.when}]" if n.when else ""
        # `name` is the id stem; `role` is the OPTIONAL semantic baseline a
        # traffic profile can override at runtime (2026-08-04,
        # `docs/WORK_ORDER_role_persona_tier.md`). Show the role only when it
        # differs from the name -- they match in every shipped config, and
        # echoing `ws (role=ws, ...)` on every line would be pure noise. No
        # `persona=` here any more: a twin config declares no behavior.
        role = f" role={n.role}" if n.role and n.role != n.name else ""
        sys.stdout.write(
            f"    - {n.name} (kind={n.kind}, image={n.image}, count={n.count}){role}{gate}\n"
        )
    if tc.identity and tc.identity.users:
        sys.stdout.write(f"  identity: domain={tc.identity.domain}, "
                         f"{len(tc.identity.users)} user(s)\n")
    if tc.data:
        sys.stdout.write(f"  data: {len(tc.data)} dataset(s)\n")
    # `tap_points` rendering REMOVED here (`docs/WORK_ORDER_tap_eligibility.md`
    # D3, Authority: user, 2026-07-28) -- the twin-config `tap_points:` block
    # is retired; a twin config carries no tap-related content at all.
    _print_deploy_gate_section(report, gate_note)
    return 0


def _param_overrides_note(args: argparse.Namespace) -> str:
    """` (overridden: K=V, ...)` for the `params:` line, or empty."""
    from axrange.twinconfig.params import parse_param_flags

    try:
        overrides = parse_param_flags(getattr(args, "param", None))
    except Exception:  # noqa: BLE001 -- already reported by _resolve_deploy_gate
        return ""
    if not overrides:
        return ""
    pairs = ", ".join(f"{k}={v}" for k, v in sorted(overrides.items()))
    return f"  (overridden: {pairs})"


def _resolve_deploy_gate(args: argparse.Namespace, config_id: str):
    """Expand `config_id` at the operator's `--param` thresholds and return
    `(report | None, exit_code, note | None)` for `twin-config show`.

    Three outcomes, deliberately distinct:

      * a bad `--param` the operator JUST typed is a usage error -- reported
        to stderr, exit code 2, nothing printed as if it had worked;
      * the deploy GATE refusing this threshold (`DeployGateRefusalError`: an
        `identity:`/`data:` entry pointing at a removed node, a segment gated
        out from under a host) is a REFUSED note -- the operator can turn the
        dial, so it is not the same thing as broken content;
      * any other expansion failure is an UNAVAILABLE note.

    A note is never silently swallowed (LES-089); `show`'s own job -- printing
    the declared shape -- has already succeeded either way, so both notes keep
    exit code 0.
    """
    from axrange.twinconfig import TwinConfigError, TwinConfigParamError
    from axrange.twinconfig.errors import DeployGateRefusalError
    from axrange.twinconfig.gate import deploy_gate_report
    from axrange.twinconfig.params import parse_param_flags

    try:
        overrides = parse_param_flags(getattr(args, "param", None))
        report = deploy_gate_report(
            config_id,
            catalog_dir=getattr(args, "twin_configs_dir", None),
            param_overrides=overrides,
        )
    except TwinConfigParamError as e:
        sys.stderr.write(f"axrange twin-config show: --param: {e}\n")
        return None, 2, None
    except DeployGateRefusalError as e:
        return None, 0, f"REFUSED at these thresholds -- {e}"
    except TwinConfigError as e:
        return None, 0, f"UNAVAILABLE -- config does not expand: {e}"
    return report, 0, None


def _print_deploy_gate_section(report, note: Optional[str]) -> None:
    """The UNCAPPED `deploy gate:` section (`docs/WORK_ORDER_deploy_gate.md`
    §4.5) -- thresholds, counts, and every gated-out node/segment, dropped
    conduit, pruned router leg and exempted router, at whatever `--param`
    thresholds the operator is previewing. The warnings channel caps its
    detail at 50 lines and points here; this is where the full table lives."""
    if note is not None:
        sys.stdout.write(f"  deploy gate: {note}\n")
        return
    if report is None or not report.active:
        # Said out loud, not omitted: an operator previewing a threshold on a
        # config with no `observation:` blocks would otherwise see nothing and
        # conclude the threshold had been applied.
        sys.stdout.write(
            "  deploy gate: not applicable (no observation: blocks in this config; "
            "every node and segment is emitted)\n"
        )
        return
    sys.stdout.write("  deploy gate:\n")
    for line in report.summary_lines():
        sys.stdout.write(f"    {line}\n")
    for line in report.detail_lines():
        sys.stdout.write(f"    - {line}\n")


# ===========================================================================
# image build
# ===========================================================================


def cmd_image(args: argparse.Namespace) -> int:
    if getattr(args, "image_command", None) == "build":
        return cmd_image_build(args)
    sys.stderr.write("axrange image: unknown subcommand\n")
    return 2


def cmd_image_build(args: argparse.Namespace) -> int:
    from axrange.imagebuild import (
        BuildOptions,
        ImageBuildError,
        build_image,
        load_build_config,
    )

    images_root = Path(args.images) if args.images else Path.cwd()
    if not (images_root / "images" / "manifests").is_dir():
        sys.stderr.write(
            f"axrange image build: no images/manifests/ under {images_root}; "
            "pass --images <root>\n"
        )
        return 2

    build_cfg = load_build_config(args.adapter_config)
    if args.node:
        build_cfg.node = args.node
        build_cfg.deploy.default_node = args.node
    if not build_cfg.node:
        sys.stderr.write(
            "axrange image build: build node missing; set BUILD_NODE / "
            "DEFAULT_NODE in adapter.conf or pass --node\n"
        )
        return 2

    options = BuildOptions(
        version=args.version,
        build_vmid=args.build_vmid,
        refresh_cache=args.refresh_cache,
        ignore_conformance=args.ignore_conformance,
        keep_build_vm=args.keep_build_vm,
        skip_smoke=args.skip_smoke,
    )

    def _log(msg: str) -> None:
        sys.stdout.write(f"  {msg}\n")
        sys.stdout.flush()

    sys.stdout.write(f"Building image {args.image} (node={build_cfg.node})...\n")
    try:
        report = build_image(
            args.image,
            images_root=images_root,
            build_cfg=build_cfg,
            options=options,
            log=_log,
        )
    except ImageBuildError as e:
        sys.stderr.write(f"axrange image build: [{e.stage}] {e}\n")
        return 1

    _render_build_report(report)
    return 0


def _render_build_report(report) -> None:
    sys.stdout.write("\n")
    sys.stdout.write(f"Built template for {report.image}@{report.version}\n")
    line = f"  template_vmid: {report.template_vmid}"
    if report.prior_template_vmid is not None:
        line += (
            f"  (was {report.prior_template_vmid}; the prior template is left "
            "on the cluster, still resolvable)"
        )
    sys.stdout.write(line + "\n")
    sys.stdout.write(f"  binding:       {report.binding_file}\n")

    if report.probe_report is not None:
        pr = report.probe_report
        n_ok = sum(1 for r in pr.results if r.ok)
        sys.stdout.write(f"  probes:        {n_ok}/{len(pr.results)} passed\n")
        for r in pr.results:
            sym = "o" if r.ok else "x"
            sys.stdout.write(f"    {sym} {r.capability}\n")
            if not r.ok and r.detail:
                sys.stdout.write(f"        {r.detail}\n")

    if report.smoke_report is not None:
        s = report.smoke_report
        sym = "o" if s.ok else "x"
        sys.stdout.write(
            f"  smoke-test:    {sym} {s.ops_ok}/{s.ops_total} ops "
            f"(deploy {s.deploy_id})\n"
        )
    else:
        sys.stdout.write("  smoke-test:    skipped\n")

    if report.conformance_ignored and not report.conformance_ok:
        sys.stdout.write(
            "  NOTE: conformance failures were IGNORED (--ignore-conformance); "
            "this template should not ship.\n"
        )
    sys.stdout.write(
        "\nReview the binding diff and commit it yourself (the build does not "
        "commit on your behalf).\n"
    )


# ===========================================================================
# main
# ===========================================================================

# One handler per (tier, verb) -- the entire grid, canonical AND deprecated
# shims alike (every leaf parser sets `tier`/`verb` via `set_defaults`;
# `main()` never branches on the literal invoked spelling). Work order §3.5:
# "Implement as argparse dispatch ... one handler per (tier, verb), no
# copy-pasted subparsers."
_HANDLERS: dict[tuple[str, str], Any] = {
    ("te", "validate"): cmd_validate,
    ("te", "plan"): cmd_plan,
    ("te", "deploy"): cmd_deploy,
    ("te", "status"): cmd_status,
    ("te", "info"): cmd_te_info,
    ("te", "destroy"): cmd_destroy,
    ("te", "inventory"): cmd_te_inventory,
    ("te", "scorecard"): cmd_te_scorecard,
    ("te", "egress"): cmd_te_egress,
    ("o2", "validate"): cmd_o2_validate,
    ("o2", "plan"): cmd_o2_plan,
    ("o2", "deploy"): cmd_o2_deploy,
    ("o2", "status"): cmd_o2_status,
    ("o2", "info"): cmd_o2_info,
    ("o2", "destroy"): cmd_o2_destroy,
    ("tap", "validate"): cmd_tap_validate,
    ("tap", "plan"): cmd_tap_plan,
    ("tap", "deploy"): cmd_tap_deploy,
    ("tap", "status"): cmd_tap_status,
    ("tap", "info"): cmd_tap_info,
    ("tap", "destroy"): cmd_tap_destroy,
    ("tfc", "validate"): cmd_tfc_validate,
    ("tfc", "plan"): cmd_tfc_plan,
    ("tfc", "deploy"): cmd_tfc_deploy,
    ("tfc", "status"): cmd_tfc_status,
    ("tfc", "info"): cmd_tfc_info,
    ("tfc", "destroy"): cmd_tfc_destroy,
    ("tfc", "set-host"): cmd_tfc_set_host,
    ("exr", "deploy"): cmd_exr_deploy,
    ("exr", "status"): cmd_exr_status,
    ("exr", "destroy"): cmd_exr_destroy,
}


def _resolve_twin_tier_spec(args: argparse.Namespace, tier: str, verb: str) -> Optional[int]:
    """Before a `tap|tfc validate|plan|deploy` handler runs: turn `--twin` into
    the spec positional, and apply the two defaults a tier file INSIDE a twin
    folder gets (`docs/WORK_ORDER_twin_consolidation.md` Phase 2 items 3-4):

    - the twin's `personas/` joins the persona search path, AHEAD of any
      `AXRANGE_PERSONAS_DIR` entries so the env var still overrides it (the
      persona registry's later-wins rule) -- no more hand `export`; an
      override is announced on stderr, never silent;
    - a file that declares no `target:` and gets no `--target` targets the
      twin id (a NAME, resolved to the newest live deploy -- LES-072),
      announced on stderr as `[info] target defaulted to twin <id>`.

    Returns an exit code to stop with, or None to carry on. A plain file
    outside any twin folder passes through untouched."""
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import (
        find_twin_dir, load_twin_manifest, twin_dir_for_file,
    )
    from axrange.twinconfig.registry import search_dirs

    label = f"{tier} {verb}"
    twin_id = getattr(args, "twin", None)
    variant = getattr(args, "variant", None)
    spec = getattr(args, "spec", None)
    if variant and not twin_id:
        sys.stderr.write(f"axrange {label}: --variant only makes sense with --twin\n")
        return 2
    try:
        if twin_id:
            if spec:
                sys.stderr.write(
                    f"axrange {label}: specify either a file positional or --twin, not both\n"
                )
                return 2
            twins_dir = getattr(args, "twins_dir", None)
            roots = search_dirs(twins_dir) if twins_dir else None
            loaded = load_twin_manifest(find_twin_dir(twin_id, roots))
            args.spec = str(loaded.tier_path(tier, variant))
        elif not spec:
            sys.stderr.write(f"axrange {label}: a {tier} file path is required (or --twin)\n")
            return 2
        else:
            twin_dir = twin_dir_for_file(spec)
            if twin_dir is None:
                return None
            loaded = load_twin_manifest(twin_dir)
    except TwinConfigError as e:
        sys.stderr.write(f"axrange {label}: {e}\n")
        return 2

    if loaded.personas_dir is not None:
        env_key = "AXRANGE_PERSONAS_DIR"
        existing = [p for p in os.environ.get(env_key, "").split(":") if p.strip()]
        mine = str(loaded.personas_dir)
        if mine not in existing:
            _announce_persona_overrides(label, mine, existing)
            os.environ[env_key] = ":".join([mine, *existing])
            from axrange.personas.registry import reload_personas

            reload_personas()
    if not getattr(args, "target", None) and not _tier_file_declares_target(args.spec, tier):
        args.target = loaded.id
        sys.stderr.write(f"[info] target defaulted to twin {loaded.id}\n")
    return None


def _announce_persona_overrides(label: str, mine: str, existing: list[str]) -> None:
    """One stderr line per `AXRANGE_PERSONAS_DIR` entry that overrides a
    persona id the twin's own `personas/` defines -- the env var wins by the
    registry's later-wins rule, which is right, but must not be silent (the
    twin would otherwise drive a persona its folder does not contain).
    Best-effort: an unreadable dir is left to `reload_personas` to report."""
    from axrange.personas.registry import PersonaRegistryError, load_catalog_dir

    try:
        mine_ids = set(load_catalog_dir(mine))
        winners: dict[str, str] = {}
        for d in existing:  # later wins, so a later dir overwrites the winner
            for pid in load_catalog_dir(d):
                if pid in mine_ids:
                    winners[pid] = d
    except PersonaRegistryError:
        return
    by_dir: dict[str, list[str]] = {}
    for pid, d in winners.items():
        by_dir.setdefault(d, []).append(pid)
    for d, ids in by_dir.items():
        sys.stderr.write(
            f"[info] axrange {label}: AXRANGE_PERSONAS_DIR entry {d} overrides the "
            f"twin's persona(s) {', '.join(sorted(ids))} (from {mine})\n"
        )


def _tier_file_declares_target(path: str, tier: str) -> bool:
    """True when the tap-plan / traffic-profile file sets its own `target:`.
    A parse failure answers True (no default applied) so the handler's own
    loader reports the real error instead of this helper masking it."""
    from ruamel.yaml import YAML

    from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw

    root_key = "tap-plan" if tier == "tap" else "traffic-profile"
    try:
        raw = load_yaml_raw(path, loader=YAML(typ="safe"))
    except YamlLoadError:
        return True
    block = raw.get(root_key) if isinstance(raw, dict) else None
    return not isinstance(block, dict) or bool(block.get("target"))


def main(argv: Sequence[str] | None = None) -> int:
    # Top-level `ConfigError` handler (code review, follow-on to the
    # config-external-sensor feature): `load_config` now raises `ConfigError`
    # loud on a malformed `EXTERNAL_SENSORS`/adapter.conf value, but only two
    # command handlers (`cmd_tap_validate`/`cmd_tap_info`) ever caught it --
    # every other path that loads config (`cmd_tap_plan`, `cmd_tap_deploy`
    # via `apply_tap_plan`'s internal `load_config`, `cmd_destroy`, `te
    # deploy`, `o2` verbs, `serve`, `image build`, ...) would otherwise dump
    # a raw traceback on the exact same bad input. Catching it here, ONCE,
    # around the entire dispatch, makes every path fail CLEANLY regardless
    # of whether that handler also has its own (now largely redundant, but
    # harmless) catch.
    from axrange.platform import ConfigError
    # Same belt-and-suspenders backstop for `--config`/twin-config expansion
    # (docs/WORK_ORDER_twin_configs.md) — `_resolve_scenario_or_config` and
    # `cmd_twin_config_*` already catch `TwinConfigError` locally, but a
    # future call site that forgets to is still covered here (LES-090).
    from axrange.twinconfig import TwinConfigError

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        tier = getattr(args, "tier", None)
        verb = getattr(args, "verb", None)
        if tier is not None and verb is not None:
            notice = getattr(args, "deprecated_notice", None)
            if notice:
                sys.stderr.write(notice + "\n")
            handler = _HANDLERS.get((tier, verb))
            if handler is not None:
                if tier in ("tap", "tfc") and verb in ("validate", "plan", "deploy"):
                    rc = _resolve_twin_tier_spec(args, tier, verb)
                    if rc is not None:
                        return rc
                return handler(args)
            parser.print_help()  # pragma: no cover -- defensive; every (tier, verb) is wired
            return 2

        if args.command == "list-deploys":
            return cmd_list_deploys(args)
        if args.command == "serve":
            return cmd_serve(args)
        if args.command == "image":
            return cmd_image(args)
        if args.command == "twin-config":
            return cmd_twin_config(args)
        parser.print_help()
        return 2
    except ConfigError as e:
        sys.stderr.write(f"[error] adapter.conf: {e}\n")
        return 2
    except TwinConfigError as e:
        sys.stderr.write(f"[error] twin-config: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
