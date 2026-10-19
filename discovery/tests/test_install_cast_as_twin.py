"""`scripts/install_cast_as_twin.py` — a cast becomes a twin-config folder under a stable id.

Every claim here is about the HANDOFF between the two repos, which is where this script's
failures land and why they are expensive. axRange reads a node's `install: [{script: ...}]`
file at plan-BUILD time (`planner/pass1_emit.py::_read_script`, resolved against the config
dir) and no validator rule checks that the file exists — so an incomplete install passes
`te validate` with zero findings and dies at `te plan` with exit 2.

The iteration sequence is destroy -> cast -> install -> deploy, so that failure arrives with
the previous twin already gone. Synthetic fixtures only (`LES-003`); nothing here reaches
axRange or a cluster.
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_INSTALL = _ROOT / "scripts" / "install_cast_as_twin.py"


def _cast(tmp_path, *, with_scripts=True, assignments=None, nodes=None, personas=None):
    """A minimal cast dir in the shape `aximprint cast` writes."""
    d = tmp_path / "cast"
    (d / "personas").mkdir(parents=True)
    (d / "twin_config.yaml").write_text(yaml.safe_dump({"twin_config": {
        "id": "pit-deadbeef-123", "description": "Discovered from /cap",
        "segments": [{"name": "seg-a", "cidr": "10.200.0.0/24", "zone_type": "simple"}],
        "nodes": nodes if nodes is not None else [
            {"name": "web", "image": "debian-12-cloud@v1", "net": "seg-a", "role": "web-tier",
             "install": [{"script": "scripts/responder-web-tier.sh"}]},
            {"name": "internet-sink-8", "image": "debian-12-cloud@v1", "net": "seg-a",
             "role": "internet-sink-8", "count": 8,
             "install": [{"script": "scripts/responder-internet-sink-8.sh"}]},
        ],
        "conduits": []}}))
    (d / "tfc.yaml").write_text(yaml.safe_dump({"traffic-profile": {
        "target": "pit-deadbeef-123",
        "assignments": list(assignments if assignments is not None else [])}}))
    (d / "tap.yaml").write_text(yaml.safe_dump({"tap-plan": {
        "target": "pit-deadbeef-123",
        "bindings": [{"selector": {"scope": "segment", "target": "seg-a"},
                      "mode": "exclusive"}]}}))
    (d / "topology.json").write_text(
        '{"bundle_schema": "axr-topology/1.3", "nodes": []}')
    (d / "provenance.json").write_text(
        '{"schema": "axr-cast-provenance/1", "pit_id": "pit-deadbeef-123", "fields": {}}')
    # A cast always ships a persona file for every persona its profile names — `cmd_cast`
    # writes both in one pass — so the fixture does too, or the installer's own consistency
    # check would fire on the fixture rather than on what a test is asking about. Pass
    # `personas=[...]` to model the one case where they legitimately diverge (a band whose
    # persona was never synthesized).
    named = (personas if personas is not None
             else ["cast-web-tier"] + [a["persona"] for a in (assignments or [])
                                       if a.get("persona")])
    for pid in dict.fromkeys(named):
        (d / "personas" / f"{pid}.yaml").write_text(yaml.safe_dump({"persona": {"id": pid}}))
    if with_scripts:
        (d / "scripts").mkdir()
        (d / "scripts" / "responder-web-tier.sh").write_text("set -eu\n")
        (d / "scripts" / "responder-internet-sink-8.sh").write_text("set -eu\n")
    return d


def _install(cast, root, stable="corpapcn-twin", *extra):
    return subprocess.run(
        [sys.executable, str(_INSTALL), str(cast), str(root), stable, *extra],
        capture_output=True, text=True, check=False)


def test_install_scripts_travel_with_the_config(tmp_path):
    """The blocker this test exists for: the installer used to copy the config, the tfc, the
    personas and the topology and stop. Six responder scripts stayed behind in the cast dir,
    four of them the `internet-sink-*` names a stale installed folder could not already have,
    and `te plan` failed on the first one with the old twin already destroyed."""
    root = tmp_path / "twin-configs"
    assert _install(_cast(tmp_path), root).returncode == 0
    out = root / "corpapcn-twin"
    # Exactly what `_read_script(config_dir, rel_path)` will look for, at the path it looks.
    for rel in ("scripts/responder-web-tier.sh", "scripts/responder-internet-sink-8.sh"):
        assert (out / rel).is_file(), f"{rel} must resolve against the installed config dir"


def test_every_referenced_script_is_checked_not_assumed(tmp_path):
    """A missing script must fail HERE, where the remedy is free, rather than at `te plan`
    after the previous twin is gone. Silence would be indistinguishable from success."""
    root = tmp_path / "twin-configs"
    r = _install(_cast(tmp_path, with_scripts=False), root)
    assert r.returncode == 1, "an incomplete install must not report success"
    assert "responder-web-tier.sh" in r.stderr and "te plan" in r.stderr, r.stderr


def test_the_tap_plan_follows_the_stable_id_like_the_traffic_profile(tmp_path):
    """`tap-plan.target` is a twin NAME. Left at the cast's PIT id it resolves to no standing
    deploy, exactly as `tfc` would — and a tap plan left behind in the cast dir is a twin
    somebody deploys untapped."""
    root = tmp_path / "twin-configs"
    assert _install(_cast(tmp_path), root).returncode == 0
    tap = yaml.safe_load((root / "corpapcn-twin" / "tap-cast.yaml").read_text())
    assert tap["tap-plan"]["target"] == "corpapcn-twin"
    # The binding's own selector target is a SEGMENT name and must not be rewritten with it.
    assert tap["tap-plan"]["bindings"][0]["selector"]["target"] == "seg-a"


def test_the_pit_id_is_moved_into_the_description_never_lost(tmp_path):
    """The rewrite exists so `te deploy --update` can find the standing deploy across a
    re-cast; provenance is relocated, not discarded."""
    root = tmp_path / "twin-configs"
    assert _install(_cast(tmp_path), root).returncode == 0
    tc = yaml.safe_load((root / "corpapcn-twin" / "corpapcn-twin.yaml").read_text())
    assert tc["twin_config"]["id"] == "corpapcn-twin"
    assert "pit-deadbeef-123" in tc["twin_config"]["description"]


def test_sensors_are_filled_at_install_time_not_invented_at_cast_time(tmp_path):
    """The one Lab fact the cast genuinely cannot observe, supplied where the two repos meet.

    The compiler is right to leave `sensors:` absent — naming an `o2` sensor it never saw is
    the fabrication `evidence_class` exists to prevent — but an unfilled plan is unappliable,
    and "hand-edit it every iteration" is precisely how `default_agents: false` was lost: an
    edit that lives only in the installed file is one the next re-cast silently discards."""
    root = tmp_path / "twin-configs"
    assert _install(_cast(tmp_path), root, "corpapcn-twin", "--sensors", "XPress-SW-A"
                    ).returncode == 0
    tap = yaml.safe_load((root / "corpapcn-twin" / "tap-cast.yaml").read_text())["tap-plan"]
    assert tap["bindings"], "a plan with no bindings would fill nothing and look successful"
    assert all(b["sensors"] == ["XPress-SW-A"] for b in tap["bindings"])


def test_without_the_flag_sensors_stay_absent_rather_than_guessed(tmp_path):
    """Default behaviour is unchanged and honest: an unfilled plan that axRange REFUSES beats
    one that applies while pointing at a sensor nobody chose."""
    root = tmp_path / "twin-configs"
    r = _install(_cast(tmp_path), root)
    assert r.returncode == 0
    tap = yaml.safe_load((root / "corpapcn-twin" / "tap-cast.yaml").read_text())["tap-plan"]
    assert all("sensors" not in b for b in tap["bindings"])
    assert "UNFILLED" in r.stdout, "an unappliable plan must say so on the way out"


# -- node GROUPS -> node INSTANCES ------------------------------------------------
#
# The cast writes one assignment per node group, because a group is what a `twin_config`
# holds. axRange deploys INSTANCES and `tfc` binds one persona per host, so an unexpanded
# profile is refused outright:
#
#   assignment 'node:generic-endpoint-10-1-1': node 'generic-endpoint-10-1-1'
#   is not a host in target twin 'corpapcn-twin-0e4f7d58'
#
# The naming rule is verified against axRange's own expander, not inferred: it reproduces all
# 138 instance ids of the corpapcn twin exactly.

_GROUPS = [
    {"name": "ws", "image": "i", "net": "seg-a", "role": "corp-workstation", "count": 3},
    {"name": "solo", "image": "i", "net": "seg-a", "role": "corp-workstation"},
    {"name": "one", "image": "i", "net": "seg-a", "role": "db-tier", "count": 1},
]


def _profile(tmp_path, root, assignments, nodes=_GROUPS):
    r = _install(_cast(tmp_path, assignments=assignments, nodes=nodes), root)
    if r.returncode != 0:
        return r, None
    return r, yaml.safe_load(
        (root / "corpapcn-twin" / "tfc-cast.yaml").read_text())["traffic-profile"]


def test_a_counted_group_becomes_one_assignment_per_instance(tmp_path):
    """`count: 3` on `ws` deploys `ws01`, `ws02`, `ws03` — three hosts, three personas."""
    _r, prof = _profile(tmp_path, tmp_path / "t", [{"node": "ws", "persona": "cast-x"}])
    assert [a["node"] for a in prof["assignments"]] == ["ws01", "ws02", "ws03"]
    assert {a["persona"] for a in prof["assignments"]} == {"cast-x"}


def test_an_uncounted_group_keeps_its_bare_name(tmp_path):
    """The half of the rule that makes a blanket "always append an index" wrong: a `count: 1`
    group and one with no `count:` at all both deploy under the BARE name. Measured on the live
    twin — `corp-workstation-10-80-48` and `internet-sink-1` are both bare."""
    _r, prof = _profile(tmp_path, tmp_path / "t",
                        [{"node": "solo", "persona": "cast-x"},
                         {"node": "one", "persona": "cast-y"}])
    assert [a["node"] for a in prof["assignments"]] == ["solo", "one"]


def test_a_role_entry_that_double_claims_a_node_targeted_role_is_dropped(tmp_path):
    """axRange permits exactly ONE persona instance per host, so a surviving role-level entry
    alongside node-level ones for the same role claims every host of it twice and `tfc deploy`
    refuses the whole profile ("claimed by both 'role:X' and 'node:Y'"). A banded role has to
    go FULLY node-targeted — dropping the role entry is what makes the split appliable rather
    than merely descriptive."""
    r, prof = _profile(tmp_path, tmp_path / "t", [
        {"node": "ws", "persona": "cast-heavy"},
        {"role": "corp-workstation", "persona": "cast-base", "seed": 7},
    ])
    assert not [a for a in prof["assignments"] if a.get("role")], "the role entry must go"
    assert [a["node"] for a in prof["assignments"]] == ["ws01", "ws02", "ws03"]
    assert "dropped 1 double-claiming role" in r.stdout


def test_a_role_entry_with_no_node_assignments_is_left_alone(tmp_path):
    """The other side: an unbanded role is driven role-wide and must KEEP its entry, or every
    host of it silently generates nothing. Only a genuine conflict is dropped."""
    _r, prof = _profile(tmp_path, tmp_path / "t", [
        {"node": "ws", "persona": "cast-heavy"},
        {"role": "db-tier", "persona": "cast-db", "seed": 7},
    ])
    kept = [a for a in prof["assignments"] if a.get("role")]
    assert len(kept) == 1 and kept[0]["role"] == "db-tier" and kept[0]["seed"] == 7


def test_an_assignment_naming_no_real_instance_fails_at_install_time(tmp_path):
    """The guard that decides WHERE this costs something. Unchecked, the failure lands on
    `tfc deploy` against a twin that is already standing; here it costs a re-run."""
    r, _ = _profile(tmp_path, tmp_path / "t", [{"node": "ghost", "persona": "cast-x"}])
    assert r.returncode == 1
    assert "ghost" in r.stderr and "is not a host in target twin" in r.stderr


def test_expansion_is_idempotent_over_its_own_output(tmp_path):
    """Re-running the installer over an already-expanded profile must not expand twice.
    Iteration re-runs this script constantly; a second pass turning `ws01` into `ws0101` would
    be a slow-burning corruption rather than an error."""
    _r, once = _profile(tmp_path, tmp_path / "t",
                        [{"node": "ws01", "persona": "cast-x"}])
    assert [a["node"] for a in once["assignments"]] == ["ws01"]


def test_a_band_with_no_persona_file_falls_back_to_its_roles_base(tmp_path):
    """Silence is the worst outcome here: a host whose persona file does not exist generates
    NOTHING, and the twin still stands up looking healthy while under-generating. The fallback
    is re-checked at install time so a profile can never reference a persona that is not in the
    folder shipped beside it."""
    cast = _cast(tmp_path, assignments=[{"node": "ws", "persona": "cast-corp-workstation-heavy"}],
                 nodes=_GROUPS,
                 # Only the BASE persona exists on disk; the band's own was never synthesized.
                 personas=["cast-web-tier", "cast-corp-workstation"])
    root = tmp_path / "t"
    assert _install(cast, root).returncode == 0
    prof = yaml.safe_load((root / "corpapcn-twin" / "tfc-cast.yaml").read_text())
    personas = {a["persona"] for a in prof["traffic-profile"]["assignments"]}
    assert personas == {"cast-corp-workstation"}, personas


# -- the provenance sidecar (`docs/WORK_ORDER_reference_ingest.md` D9) ---------------------


def test_the_provenance_sidecar_is_installed_beside_the_twin_config(tmp_path):
    """Missing until 2026-09-04, and found independently by three drafts before anyone shipped
    the one `if`. It is not tidiness: `twin_config` is `extra="forbid"`, so per-field evidence
    CANNOT live in the YAML — the sidecar is the only carrier there is, and without it axRange
    re-derives every field's class from the twin config's shape, which is the imputed-as-observed
    hazard the class exists to prevent."""
    root = tmp_path / "twin-configs"
    r = _install(_cast(tmp_path), root)
    assert r.returncode == 0, r.stderr
    landed = root / "corpapcn-twin" / "corpapcn-twin.provenance.json"
    assert landed.is_file(), "the sidecar must travel with the twin config"
    assert json.loads(landed.read_text())["schema"] == "axr-cast-provenance/1"
    assert "corpapcn-twin.provenance.json" in r.stdout


def test_the_sidecar_is_copied_verbatim_so_its_pit_id_survives(tmp_path):
    """Unlike the twin config, the tfc target and the tap target — all three of which are
    REWRITTEN to the stable id because they are addressing. This is provenance: its `pit_id`
    names the PIT the twin was cast from, and rewriting it would destroy the only in-file
    record of the origin (the twin config keeps it in `[cast <origin>]` prose; this keeps it as
    a field)."""
    cast = _cast(tmp_path)
    root = tmp_path / "twin-configs"
    assert _install(cast, root).returncode == 0
    landed = json.loads(
        (root / "corpapcn-twin" / "corpapcn-twin.provenance.json").read_text())
    assert landed["pit_id"] == "pit-deadbeef-123"
    assert landed == json.loads((cast / "provenance.json").read_text())


def test_a_cast_with_no_sidecar_installs_but_says_so(tmp_path):
    """Named, not silent. A cast without a sidecar is one whose every evidence class the
    consumer has to re-derive, and an operator should hear that at install time rather than
    discover it in a scorecard that quietly re-counted (`LES-006`: an audit nobody sees is not
    an audit)."""
    cast = _cast(tmp_path)
    (cast / "provenance.json").unlink()
    root = tmp_path / "twin-configs"
    r = _install(cast, root)
    assert r.returncode == 0, r.stderr
    assert not (root / "corpapcn-twin" / "corpapcn-twin.provenance.json").exists()
    assert "no provenance.json" in r.stderr


# ---------------------------------------------------------------------------------------------
# `aximprint install-cast` — the same body, reached the other way (2026-09-05)
# ---------------------------------------------------------------------------------------------
#
# The install moved into `src/aximprint/synth/install.py` and the script above became a thin
# wrapper. Every claim already made in this file is therefore a claim about BOTH entry points;
# what is left to check is that they really are one body, that the subcommand's own argv shape
# works, and that the three-valued exit code says which kind of failure happened.


def _cli(cast, root, stable="corpapcn-twin", *extra):
    return subprocess.run(
        [sys.executable, "-m", "aximprint.cli", "install-cast", str(cast), str(root), stable,
         *extra],
        capture_output=True, text=True, check=False, cwd=str(_ROOT))


def test_the_script_is_a_WRAPPER_not_a_second_implementation(tmp_path):
    """`LES-101`'s rule, checked rather than promised: the script must hold no copy of the body.
    Two implementations of "install a cast" drift, and the failure they drift into is a twin
    that stands up looking healthy and quietly under-generates."""
    src = _INSTALL.read_text()
    assert "from aximprint.synth.install import install_cast" in src
    for gone in ("node_instances", "tfc-cast.yaml", "roles_with_node_assignment",
                 "is not an instance of"):
        assert gone not in src, f"{gone!r} is still in the script -- the body was copied, not moved"


def test_both_entry_points_produce_byte_identical_output(tmp_path):
    """The strongest statement of "one body": run each into its own root and compare every file.
    A divergence here means one caller is passing something the other is not."""
    a, b = tmp_path / "a", tmp_path / "b"
    cast = _cast(tmp_path)
    assert _install(cast, a, "corpapcn-twin", "--sensors", "XPress-SW-A").returncode == 0
    assert _cli(cast, b, "corpapcn-twin", "--sensors", "XPress-SW-A").returncode == 0
    files_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    assert files_a == files_b and files_a, files_a
    for rel in files_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_the_subcommand_installs_the_whole_folder(tmp_path):
    root = tmp_path / "twin-configs"
    r = _cli(_cast(tmp_path), root)
    assert r.returncode == 0, r.stderr
    out = root / "corpapcn-twin"
    assert (out / "corpapcn-twin.yaml").is_file()
    assert (out / "tfc-cast.yaml").is_file() and (out / "tap-cast.yaml").is_file()
    assert (out / "corpapcn-twin.topology.json").is_file()
    assert (out / "corpapcn-twin.provenance.json").is_file()
    assert (out / "scripts" / "responder-web-tier.sh").is_file()
    assert (out / "personas" / "cast-web-tier.yaml").is_file()


def test_the_subcommand_takes_sensors_as_repeated_words_or_as_one_comma_list(tmp_path):
    """`--sensors A B` is the CLI grid's shape and `--sensors A,B` is the script's; one splitter
    serves both, so an operator who learned either spelling is right at both doors."""
    cast = _cast(tmp_path)
    for extra in (("--sensors", "XPress-SW-A", "XPress-SW-B"),
                  ("--sensors", "XPress-SW-A,XPress-SW-B")):
        root = tmp_path / ("r" + str(len(extra)) + extra[1][-1])
        assert _cli(cast, root, "corpapcn-twin", *extra).returncode == 0, extra
        tap = yaml.safe_load(
            (root / "corpapcn-twin" / "tap-cast.yaml").read_text())["tap-plan"]
        assert all(b["sensors"] == ["XPress-SW-A", "XPress-SW-B"] for b in tap["bindings"]), extra


def test_a_missing_cast_directory_is_exit_2_not_a_traceback(tmp_path):
    """`LES-134`: a could-not-run result needs its OWN code. 2 says "you pointed me at the wrong
    thing and no cast would have worked"; 1 says "this cast is internally inconsistent". A
    caller that only checks nonzero loses the distinction, which is why it is written down."""
    r = _cli(tmp_path / "nope", tmp_path / "twin-configs")
    assert r.returncode == 2, r.stderr
    assert "not a directory" in r.stderr and "Traceback" not in r.stderr


def test_a_cast_directory_with_no_twin_config_is_exit_2(tmp_path):
    empty = tmp_path / "cast"
    empty.mkdir()
    r = _cli(empty, tmp_path / "twin-configs")
    assert r.returncode == 2 and "twin_config.yaml" in r.stderr, r.stderr


def test_a_twin_config_that_is_not_the_right_shape_is_exit_2(tmp_path):
    cast = _cast(tmp_path)
    (cast / "twin_config.yaml").write_text(yaml.safe_dump({"nodes": []}))
    r = _cli(cast, tmp_path / "twin-configs")
    assert r.returncode == 2 and "twin_config" in r.stderr, r.stderr


def test_an_empty_id_is_exit_2_because_the_description_would_lose_the_origin(tmp_path):
    cast = _cast(tmp_path)
    doc = yaml.safe_load((cast / "twin_config.yaml").read_text())
    doc["twin_config"]["id"] = ""
    (cast / "twin_config.yaml").write_text(yaml.safe_dump(doc))
    r = _cli(cast, tmp_path / "twin-configs")
    assert r.returncode == 2 and "id" in r.stderr, r.stderr


def test_a_finding_in_a_WELL_FORMED_cast_is_exit_1_not_exit_2(tmp_path):
    """The other side of the split: the cast parsed fine and its contents are wrong. This is the
    failure that would otherwise land at `te plan` with the previous twin already destroyed."""
    r = _cli(_cast(tmp_path, with_scripts=False), tmp_path / "twin-configs")
    assert r.returncode == 1, r.stderr
    assert "responder-web-tier.sh" in r.stderr


def test_install_cast_is_in_the_top_level_help(tmp_path):
    """`LES-073`: a verb absent from the parser's choice list is a verb nobody finds."""
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "--help"],
                       capture_output=True, text=True, cwd=str(_ROOT))
    assert "install-cast" in r.stdout
