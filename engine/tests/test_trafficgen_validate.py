"""trafficgen B2 (`engine/src/axrange/trafficgen/SPEC.md`, "Validator rules"):
the tfc profile's budget / emulated / lines blocks (T2), the address
allocator, the TGN-0xx line checks and the PSN-120..140 app-mix rules, on a
fixture of `three-tier-dc-manual`."""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from axrange.o2control.traffic import TrafficPlanError, load_traffic_profile, parse_traffic_profile
from axrange.personas.registry import PERSONAS
from axrange.personas.schema import load_persona
from axrange.trafficgen.addresses import AllocationError, allocate
from axrange.trafficgen.apps import list_apps
from axrange.trafficgen.profile import EmulatedSpec
from axrange.trafficgen.twin import TwinView
from axrange.trafficgen.validate import check_lines, check_profile_file, estimate_mbps
from axrange.validator.checks.personas import validate_persona

FIX = Path(__file__).parent / "fixtures" / "trafficgen" / "three-tier-dc-manual"
PROFILE = FIX / "tfc" / "default.yaml"


@pytest.fixture(scope="module")
def twin() -> TwinView:
    return TwinView.from_te_path(FIX / "te.yaml")


@pytest.fixture(scope="module")
def apps():
    return list_apps()[0]


def _gate_raw() -> dict:
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(PROFILE.read_text())


def _spec(mutate=None):
    raw = copy.deepcopy(_gate_raw())
    if mutate:
        mutate(raw["traffic-profile"])
    return parse_traffic_profile(raw)


def _codes(spec, twin, apps, image="trex-astf@v1", personas=None):
    return [f.rule_id for f in check_lines(spec, twin, personas or PERSONAS, apps, generator_image=image)]


# --- T2 parse ---------------------------------------------------------------
def test_the_gate_profile_parses():
    spec = load_traffic_profile(PROFILE)
    assert len(spec.assignments) == 2
    assert (spec.budget.cps, spec.budget.mbps) == (100, 60)
    assert [(e.group, e.count, e.external) for e in spec.emulated] == [
        ("rack1-web", 20, False), ("rack3-db", 1, False), ("rack2-web", 5, False), ("internet-users", 200, True)]
    l0, l1 = spec.lines
    assert (l0.id, l0.rate_cps, l0.max_concurrent) == ("rack1-web--rack3-db--sql-oltp", 20, 200)
    assert l1.from_group == "internet-users" and l1.mix == "web-browse"


def test_a_profile_without_the_new_keys_parses_as_before():
    spec = parse_traffic_profile({"traffic-profile": {"assignments": [{"role": "web-tier", "persona": "web-tier"}]}})
    assert (spec.budget, spec.emulated, spec.lines) == (None, [], [])


def test_a_lines_only_profile_is_valid():
    spec = _spec(lambda b: b.pop("assignments"))
    assert spec.assignments == [] and len(spec.lines) == 2


@pytest.mark.parametrize("mutate,msg", [
    (lambda b: b.pop("budget"), "budget"),
    (lambda b: b["budget"].update(gbps=1), "budget"),
    (lambda b: b["budget"].update(cps=0), "budget.cps"),
    (lambda b: b["emulated"][0].update(colour="red"), "unknown key"),
    (lambda b: b["emulated"][0].update(count=0), "count"),
    (lambda b: b["emulated"][0].update(count=2.5), "integer"),
    (lambda b: b["emulated"].append({"group": "rack1-web", "count": 1}), "twice"),
    (lambda b: b["emulated"][3].pop("segment"), "segment"),
    (lambda b: b["emulated"][0].update(segment="rack1-lan"), "only for an external"),
    (lambda b: b["emulated"][0].update(start="10.200.0.999"), "IPv4"),
    (lambda b: b["lines"][0].update(rate="20"), "unknown key"),
    (lambda b: b["lines"][0].pop("mix"), "mix"),
    (lambda b: b["lines"][0].update(rate_cps=-1), "rate_cps"),
    (lambda b: b["lines"].append(dict(b["lines"][0])), "not unique"),
])
def test_t2_shape_errors_are_refused(mutate, msg):
    with pytest.raises(TrafficPlanError, match=msg):
        _spec(mutate)


# --- the twin view and the allocator ------------------------------------------
def test_the_twin_view_knows_every_real_address(twin):
    assert twin.groups["rack1-web"].instances == ("rack1-web01", "rack1-web02")
    assert twin.used["rack1-lan"] == {"10.200.0.1", "10.200.0.2", "10.200.0.3", "10.200.0.4", "10.200.0.10"}
    assert twin.used["internet"] == {"10.250.0.1", "10.250.0.2", "10.250.0.10"}


def test_allocation_is_deterministic_and_skips_real_addresses(twin):
    spec = _spec()
    a, b = allocate(twin, spec.emulated), allocate(twin, spec.emulated)
    assert a == b
    r1 = a["rack1-web"]
    # .5-.9 is a run of five: too short for twenty, so the block starts after rack1-db (.10).
    assert (r1[0].name, r1[0].ip, r1[0].role) == ("rack1-web-e01", "10.200.0.11", "web-tier")
    assert (r1[-1].name, r1[-1].ip) == ("rack1-web-e20", "10.200.0.30")
    assert a["rack3-db"][0].ip == "10.200.2.5"
    users = a["internet-users"]
    assert len(users) == 200 and users[0].name == "internet-users-e001" and users[0].ip == "10.250.0.11"  # after the sink (.10)
    assert not {i.ip for g in a.values() for i in g} & twin.real_ips()


def test_start_moves_the_first_address(twin):
    a = allocate(twin, [EmulatedSpec(group="rack1-web", count=2, start="10.200.0.100")])
    assert [i.ip for i in a["rack1-web"]] == ["10.200.0.100", "10.200.0.101"]


def test_a_full_segment_is_an_allocation_error(twin):
    with pytest.raises(AllocationError, match="consecutive free"):
        allocate(twin, [EmulatedSpec(group="rack1-web", count=250)])


# --- TGN rules ----------------------------------------------------------------
def test_the_gate_profile_is_clean(twin, apps):
    assert _codes(_spec(), twin, apps) == []


def test_tgn_010_a_line_from_an_all_real_group(twin, apps):
    spec = _spec(lambda b: b["lines"][0].update({"from": "rack2-db"}))
    assert _codes(spec, twin, apps) == ["TGN-010"]


def test_tgn_020_a_line_to_a_real_server_names_the_apps(twin, apps):
    spec = _spec(lambda b: b["lines"][1].update(to="rack3-web"))
    f = check_lines(spec, twin, PERSONAS, apps, generator_image="x")
    assert [x.rule_id for x in f] == ["TGN-020"]
    assert "tls-session" in f[0].message and "http-browse" not in f[0].message


def test_tgn_030_names_that_do_not_resolve(twin, apps):
    def m(b):
        b["lines"][0]["to"] = "nowhere"
        b["lines"][1]["mix"] = "web-tier"              # a real persona, not a mix
        b["emulated"].append({"group": "agg-1", "count": 1})             # a router
        b["emulated"].append({"group": "ghosts", "count": 1, "external": True, "segment": "mars", "role": "x"})
    msgs = [f.message for f in check_lines(_spec(m), twin, PERSONAS, apps, generator_image="x")]
    assert len(msgs) == 4 and all(" does not resolve" in x for x in msgs)
    assert any("'nowhere'" in x for x in msgs) and any("'web-tier'" in x for x in msgs)
    assert any("'agg-1'" in x for x in msgs) and any("'mars'" in x for x in msgs)


def test_tgn_040_the_budget(twin, apps):
    assert _codes(_spec(lambda b: b["budget"].update(cps=49)), twin, apps) == ["TGN-040"]
    spec = _spec()
    mbps = estimate_mbps(spec.lines, PERSONAS, apps)
    assert 1 < mbps < 60
    assert _codes(_spec(lambda b: b["budget"].update(mbps=round(mbps / 2, 3))), twin, apps) == ["TGN-040"]


def test_tgn_050_addresses(twin, apps):
    assert _codes(_spec(lambda b: b["emulated"][0].update(count=250)), twin, apps) == ["TGN-050"]
    assert _codes(_spec(lambda b: b["emulated"][0].update(start="10.200.9.1")), twin, apps) == ["TGN-050"]


def test_tgn_060_a_mix_assigned_to_a_vm(twin, apps):
    spec = _spec(lambda b: b["assignments"].append({"role": "web-tier", "persona": "web-browse"}))
    assert _codes(spec, twin, apps) == ["TGN-060"]


def test_tgn_070_no_generator_image(twin, apps):
    assert _codes(_spec(), twin, apps, image=None) == ["TGN-070"]


def test_check_profile_file_reads_the_twin_folder():
    assert check_profile_file(load_traffic_profile(PROFILE), PROFILE, platform="proxmox") == []
    assert [f.rule_id for f in check_profile_file(load_traffic_profile(PROFILE), PROFILE, platform="libvirt")] == ["TGN-070"]


def test_tfc_validate_runs_the_line_checks(tmp_path):
    exe = Path(sys.executable).parent / "axrange"
    ok = subprocess.run([str(exe), "tfc", "validate", str(PROFILE), "--db", str(tmp_path / "none.db")],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "traffic lines: OK (2 line(s), 226 emulated instance(s))" in ok.stdout
    bad_dir = tmp_path / "three-tier-dc-manual"
    import shutil
    shutil.copytree(FIX, bad_dir)
    p = bad_dir / "tfc" / "default.yaml"
    p.write_text(p.read_text().replace("cps: 100", "cps: 10"))
    bad = subprocess.run([str(exe), "tfc", "validate", str(p), "--db", str(tmp_path / "none.db")],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "TGN-040" in bad.stderr


# --- PSN-120..140 ---------------------------------------------------------------
def _mix(**extra):
    base = {"id": "m", "os_family": "linux", "drive": "trex-astf",
            "emits": [{"proto": "mysql", "ports": [3306], "peer_role": "db-tier"}], "requires": ["db-tier"],
            "mix": [{"app": "sql-query", "weight": 1}]}
    base.update(extra)
    return load_persona({"persona": base})


def test_psn_rules_pass_for_the_catalog_mixes_and_the_per_vm_form(apps):
    for pid in ("web-browse", "api-east-west", "sql-oltp"):
        assert validate_persona(PERSONAS[pid], apps=apps) == []
    per_vm = load_persona({"persona": {"id": "s", "os_family": "linux", "drive": "trex-astf", "knobs": {"side": "server"}}})
    assert validate_persona(per_vm, apps=apps) == []


@pytest.mark.parametrize("extra,code", [
    ({"drive": "scripted"}, "PSN-120"),
    ({"knobs": {"side": "client"}}, "PSN-120"),
    ({"mix": [{"app": "nope", "weight": 1}]}, "PSN-130"),
    ({"mix": [{"app": "sql-query", "weight": 0}]}, "PSN-130"),
    ({"mix": [{"app": "sql-query"}, {"app": "dns-lookup"}]}, "PSN-140"),
])
def test_psn_mix_rules(apps, extra, code):
    assert code in [f.rule_id for f in validate_persona(_mix(**extra), apps=apps)]


def test_psn_120_a_trex_persona_with_neither_form(apps):
    p = load_persona({"persona": {"id": "z", "os_family": "linux", "drive": "trex-astf"}})
    assert [f.rule_id for f in validate_persona(p, apps=apps)] == ["PSN-120"]
