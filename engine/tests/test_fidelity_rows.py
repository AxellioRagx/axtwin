"""The eleven rows + the §4.1 verdict rollup (S2), fixture-driven: no
cluster anywhere, and the state store is a real `StateStore` built by the
real planner/executor path (`tests/fidelity_support.py` explains why).

The §3.4 run-1 acceptance numbers are asserted against the REAL
`twin-configs-cast/corpapcn-twin/` bundle and the shipped topology export
(LES-185); every per-row branch that the standing twin cannot exercise —
the deploy gate actually running, `observed, below deploy threshold`, an
unrouted pair, an unapplied install — is exercised on the `mini-cast`
fixture bundle, which reproduces the real cast's degenerate shape on
purpose (one router legging every segment, `count:`-collapsed groups, a
synthetic segment).
"""
from __future__ import annotations

import json

import pytest

from axrange.fidelity import build_scorecard, load_reference_bundle
from axrange.fidelity.probe import INCONCLUSIVE, ProbeReport, ProbeResult, ProbeTarget
from axrange.fidelity.rows import (
    GateInfo,
    Reproduced,
    observed_listen_facts,
    row_1_asset,
    row_2_topology,
    row_3_configuration,
    row_5_protocol,
)
from axrange.fidelity.schema import (
    FAIL,
    GOLDEN,
    INCOMPLETE,
    NOT_ATTESTED,
    NOT_COMPUTED,
    NOT_GOLDEN,
    PASS,
    Clause,
    EvidenceMix,
    Row,
    gate_set,
    rollup_verdict,
    row_status,
)
from tests.fidelity_support import (
    CAST_BUNDLES,
    MINI_CAST,
    REAL_CAST,
    build_mini_deploy,
    mini_inputs,
)


@pytest.fixture
def mini(tmp_path):
    store, deploy_id = build_mini_deploy(tmp_path)
    try:
        yield mini_inputs(store, deploy_id)
    finally:
        store.close()


# ===========================================================================
# §4.1 — the output shape and the rollup
# ===========================================================================
def _row(number, verdict, **kw):
    return Row(
        row=number, dimension="d", category="c", channel="ch",
        verdict=verdict, **kw,
    )


def test_rollup_golden_only_when_every_gate_passes():
    rows = [_row(n, PASS) for n in (1, 2, 3, 5)]
    assert rollup_verdict(rows) == GOLDEN


def test_rollup_not_golden_on_a_failing_gate():
    rows = [_row(1, FAIL), _row(2, PASS), _row(3, PASS), _row(5, PASS)]
    assert rollup_verdict(rows) == NOT_GOLDEN


def test_rollup_incomplete_beats_not_golden():
    """§4.1's load-bearing rule: a gate that could not be evaluated is not a
    failure, and `incomplete` wins even when another gate genuinely failed."""
    rows = [
        _row(1, FAIL), _row(2, PASS), _row(3, PASS),
        _row(5, NOT_COMPUTED, reason="probe did not run"),
    ]
    assert rollup_verdict(rows) == INCOMPLETE


def test_rollup_treats_a_missing_gating_row_as_not_computed():
    assert rollup_verdict([_row(1, PASS)]) == INCOMPLETE


def test_provisional_rows_never_contribute_to_the_verdict():
    rows = [_row(n, PASS) for n in (1, 2, 3, 5)]
    rows += [_row(n, FAIL) for n in (6, 8, 9, 10)]
    assert rollup_verdict(rows) == GOLDEN
    assert all(_row(n, PASS).provisional for n in (6, 8, 9, 10))
    assert not _row(1, PASS).provisional


def test_row_status_never_contradicts_the_gate_set():
    gs = gate_set()
    for n in gs["gating"]:
        assert row_status(n) == "gates"
    for n in gs["deferred"]:
        assert row_status(n) == "deferred"
    # Rows 8 and 10 gate from spec v1.1 and are REPORTED on v1.
    assert row_status(8) == row_status(10) == "reported"


def test_not_computed_without_a_reason_is_refused():
    """`not-computed` is a first-class verdict WITH a mandatory reason
    (§4.1); constructing one without is a programming error, not a silently
    reason-less row."""
    with pytest.raises(ValueError, match="MUST carry a reason"):
        _row(1, NOT_COMPUTED)
    with pytest.raises(ValueError, match="MUST carry a reason"):
        Clause(clause="c", verdict=NOT_COMPUTED)


def test_evidence_mix_never_promotes_an_unknown_class():
    mix = EvidenceMix()
    mix.add("observed")
    mix.add(None)
    mix.add("defaulted")   # the pre-rename name -- not one of the six
    d = mix.to_dict()
    assert d["observed"] == 1
    assert d["unclassified"] == 2
    assert set(d) == {
        "asserted", "imported", "observed", "referenced", "imputed",
        "compiler", "unclassified",
    }


def test_row_to_dict_emits_every_key_even_when_none():
    d = _row(1, PASS).to_dict()
    assert d["reason"] is None and d["granularity"] is None
    assert set(d) >= {
        "row", "dimension", "category", "channel", "status", "verdict",
        "provisional", "reason", "granularity", "evidence", "measured",
        "clauses",
    }


# ===========================================================================
# §3.4 — the run-1 acceptance numbers, against the REAL bundle (LES-185)
# ===========================================================================
@pytest.fixture
def real_inputs(tmp_path):
    """The real CorpAPCN cast bundle against a REPRODUCED side standing in
    for the standing dev45 twin: 62 groups fanned out to their `count:`
    hosts, one `gw` router legging every segment. Built here rather than
    read from `~/.axrange/state.db` so the assertion is reproducible on any
    checkout (the live store is not in the repo)."""
    ref = load_reference_bundle(REAL_CAST)

    class _Net:
        def __init__(self, net_id, ip=None, external=False):
            self.net_id, self.ip, self.external = net_id, ip, external

    class _Host:
        def __init__(self, host_id, role, kind, nets):
            self.host_id, self.role, self.kind = host_id, role, kind
            self.nets = nets
            self.vmid = 1000 + abs(hash(host_id)) % 5000
            self.vm_status = "completed"

    hosts = []
    applied = {}
    for g in ref.groups:
        for host_id in g.node_ids:
            nets = [_Net(s, f"10.200.0.{len(hosts) % 250 + 2}") for s in g.segments]
            if g.is_router:
                nets.append(_Net("__wan__", "192.0.2.9", external=True))
            hosts.append(_Host(host_id, g.role, g.kind, nets))
            if g.install:
                from axrange.fidelity.rows import AppliedInstall

                entry = AppliedInstall(host_id=host_id)
                for action in g.install:
                    if "script" in action:
                        base = str(action["script"]).rsplit("/", 1)[-1]
                        entry.script_names.append(f"{host_id}-app-{base}")
                    else:
                        entry.install_actions.append(dict(action))
                applied[host_id] = entry

    rep = Reproduced(
        deploy_id="corpapcn-twin-0e4f7d58", scenario_id="corpapcn-twin",
        plan_id="corpapcn-twin-b6aaa3c5", ir_hash="b6aaa3c5" * 8,
        deploy_status="completed", tier="te", hosts=hosts, applied=applied,
        op_census={
            "provision-app": {"completed": 7},
            "run-script": {"completed": 20},
            "clone-vm": {"completed": 138},
        },
    )
    # The standing twin was cast under the OLD drop policy: its config has no
    # `observation:` block, so its gate never ran and it has no recorded
    # threshold. Confirmed (the ir_hash re-expansion matched), gate inactive.
    gate = GateInfo(confirmed=True, config_id="corpapcn-twin", gate_active=False,
                    emitted_nodes=138)
    return ref, rep, gate


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row1_fails_naming_the_five_role_less_hosts(real_inputs):
    ref, rep, gate = real_inputs
    row = row_1_asset(ref, rep, gate)
    m = row.measured
    assert row.verdict == FAIL
    assert row.granularity == "group"
    assert m["windows"] == 11
    assert m["threshold_occupancy"] == 0.8
    assert m["threshold_windows_seen"] == pytest.approx(8.8)
    assert m["observed_placed"] == 141
    assert m["observed_above_threshold"] == 64
    assert m["role_less_above_threshold"] == [
        "address:10.20.193.95", "address:10.20.205.56", "address:10.80.20.67",
        "address:10.80.72.13", "address:192.168.0.149",
    ]
    assert m["unreproduced_host_count"] == 5
    # No deploy gate ran, so NOTHING carries the `below deploy threshold`
    # state -- however many observed hosts sit under the bar.
    assert m["below_deploy_threshold_nodes"] == []
    assert m["observed_below_bar"] == 141 - 64
    assert any(c.clause == "not_evaluated_below_bar" for c in row.clauses)


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row1_os_family_is_reported_imputed_not_scored(real_inputs):
    ref, rep, gate = real_inputs
    clauses = {c.clause: c for c in row_1_asset(ref, rep, gate).clauses}
    assert clauses["os_family"].verdict == NOT_COMPUTED
    assert clauses["os_family"].evidence_class == "imputed"
    assert clauses["os_family"].scored is False
    assert clauses["image_and_sizing"].evidence_class == "compiler"


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row2_fails_naming_six_segments_and_six_pairs(real_inputs):
    ref, rep, gate = real_inputs
    row = row_2_topology(ref, rep, gate)
    m = row.measured
    assert row.verdict == FAIL
    assert m["observed_segments"] == 32
    assert m["twin_segments_deployed"] == 27
    assert m["missing_segments"] == [
        "seg-10-20-205-0", "seg-10-5-0-0", "seg-10-64-7-0",
        "seg-10-80-31-0", "seg-10-80-63-0", "seg-172-16-0-0",
    ]
    assert m["observed_cross_segment_pairs"] == 31
    assert len(m["pairs_touching_missing_segment"]) == 6
    # The other 25 all show a router leg at both ends (one `gw`).
    assert m["pairs_without_router_leg"] == []
    assert m["twin_only_segments"] == ["internet-sink"]


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row3_passes_over_11_groups_expanded_to_their_count_hosts(real_inputs):
    """The `count:` fan-out is why 11 groups yield 27 ops -- expand groups to
    hosts before comparing, or a correct twin reads as broken (§3.4)."""
    ref, rep, gate = real_inputs
    row = row_3_configuration(ref, rep, gate)
    m = row.measured
    assert row.verdict == PASS
    assert m["groups_with_install"] == 11
    assert m["declared_hosts"] == 22
    assert m["declared_actions"] == 27
    assert m["unapplied_count"] == 0
    assert m["provision_app_completed"] == 7
    assert m["run_script_completed"] == 20
    assert "CONDITIONAL" in m["conditional_ops_note"]


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row5_publishes_its_denominator_and_its_exclusions(real_inputs):
    """RULED 2026-09-05: the denominator is the observed SERVICE listens,
    with every exclusion counted and named. On the real bundle that is 31
    facts across 10 hosts, with 9 ports excluded (7 not-a-service, 2
    no-probe) out of the 41 raw listen facts."""
    ref, rep, gate = real_inputs
    row = row_5_protocol(ref, rep, gate, None)
    m = row.measured
    assert row.verdict == NOT_COMPUTED
    assert row.reason and "did not run" in row.reason
    assert m["observed_listen_facts"] == 31
    assert m["observed_listen_hosts"] == 10
    assert m["excluded_not_a_service"] == 7
    assert m["excluded_no_probe"] == 2
    # One more listen fact sits on an observed host under row 1's own bar.
    # The standing twin's gate never ran, so the phrase names THAT bar
    # rather than a deploy threshold it does not have.
    assert m["excluded_below_deploy_threshold"] == 1
    assert m["excluded_total"] == 10
    below = [e for e in m["excluded_facts"] if e["category"] == "below deploy threshold"]
    assert "below row 1's occupancy bar" in below[0]["reason"]
    port_excluded = {
        f'{e["port"]}/{e["transport"]}' for e in m["excluded_facts"]
        if e["category"] != "below deploy threshold"
    }
    assert port_excluded == {
        "49667/tcp", "49669/tcp", "49682/tcp", "55390/tcp", "57086/tcp",
        "63153/tcp", "7680/tcp", "9997/tcp", "3389/udp",
    }
    assert "53/udp" in m["port_set"]
    # 3389/TCP stays: it carries the RDP fact.
    assert "3389/tcp" in m["port_set"]
    assert any(c.clause == "excluded_ports" for c in row.clauses)


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_row5_has_no_unmapped_port_left(real_inputs):
    """The ruling's own acceptance: after the widened method set, zero facts
    in the denominator carry a non-protocol-aware method. One `connect` left
    in there would make row 5 permanently `not-computed` for a reason that
    has nothing to do with the twin."""
    ref, rep, gate = real_inputs
    facts = observed_listen_facts(ref, rep, gate)
    unmapped = [f for f in facts if f.method in ("connect", "udp-connect")]
    assert unmapped == [], [f"{f.port}/{f.transport}" for f in unmapped]


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_run1_overall_verdict_is_incomplete_without_the_probe(real_inputs):
    """§7 step 1's own acceptance criterion: `incomplete`, not `not-golden`,
    even though rows 1 and 2 genuinely fail."""
    ref, rep, gate = real_inputs
    card = build_scorecard(
        reference=ref, reproduced=rep, gate=gate,
        generated_at="2026-09-05T00:00:00Z",
    )
    assert card.verdict == INCOMPLETE
    assert card.row(1).verdict == FAIL and card.row(2).verdict == FAIL
    assert card.row(3).verdict == PASS
    for n in (4, 6, 7, 8, 9, 10):
        assert card.row(n).verdict == NOT_COMPUTED
        assert card.row(n).reason
    assert card.row(11).verdict == NOT_ATTESTED
    for n in (6, 8, 9, 10):
        assert card.row(n).provisional
    assert any("provenance sidecar absent" in w for w in card.warnings)
    assert card.to_dict()["reference"]["provenance_sidecar"] == "absent"
    assert card.to_dict()["reference"]["fingerprint"] == "absent"


# ===========================================================================
# The deployment-scope paths the standing twin cannot exercise (§3.4)
# ===========================================================================
def test_gate_confirms_and_publishes_the_threshold(mini):
    _ref, _rep, gate = mini
    assert gate.confirmed and gate.gate_active
    assert gate.threshold == 0.8
    assert gate.gated_nodes == ("thin-endpoint",)
    assert gate.gated_segments == ("seg-thin",)
    assert "confirmed by ir_hash" in gate.threshold_source


def test_row1_reports_below_deploy_threshold_never_fails_on_it(mini):
    ref, rep, gate = mini
    row = row_1_asset(ref, rep, gate)
    m = row.measured
    assert m["threshold_windows_seen"] == pytest.approx(4.0)
    assert m["below_deploy_threshold_nodes"] == ["thin-endpoint"]
    clause = next(c for c in row.clauses if c.clause == "below_deploy_threshold")
    assert clause.verdict == NOT_COMPUTED and clause.scored is False
    # The gated-out host is NOT in the failure list.
    assert "address:10.1.2.11" not in m["unreproduced_hosts"]
    # It fails for the two real reasons: an uncovered group and a role-less host.
    assert row.verdict == FAIL
    assert m["unreproduced_hosts"] == [
        "address:10.1.0.13", "address:10.1.1.12", "address:10.1.3.11",
    ]
    assert [g["role"] for g in m["uncovered_groups"]] == ["db-tier", "generic-endpoint"]


def test_row1_never_counts_the_router_as_occupancy_evidence(mini):
    """LES-210: the cast's single `gw` legs every segment; counting it as a
    group member would make every group look covered."""
    ref, rep, gate = mini
    m = row_1_asset(ref, rep, gate).measured
    assert m["twin_groups_deployed"] == 5        # gw included in the group count
    uncovered = {g["role"]: g for g in m["uncovered_groups"]}
    assert uncovered["db-tier"]["emitted"] == 1  # not 2 (gw legs seg-data too)


def test_row2_separates_a_gated_segment_from_an_absent_one(mini):
    ref, rep, gate = mini
    row = row_2_topology(ref, rep, gate)
    m = row.measured
    assert m["missing_segments"] == ["seg-missing"]        # FAILS
    assert m["below_threshold_segments"] == ["seg-thin"]   # reported only
    assert m["pairs_out_of_deployment_scope"] == ["seg-corp|seg-thin"]
    assert m["pairs_touching_missing_segment"] == ["seg-corp|seg-missing"]
    assert m["twin_only_segments"] == ["internet-sink"]
    assert row.verdict == FAIL


def test_row2_fails_a_pair_with_no_router_leg(mini):
    """The router is what makes a cross-segment conversation reproducible;
    two deployed segments with no router leg on one of them is a real
    failure, distinct from a missing segment."""
    ref, rep, gate = mini
    stripped = Reproduced(
        deploy_id=rep.deploy_id, scenario_id=rep.scenario_id,
        plan_id=rep.plan_id, ir_hash=rep.ir_hash,
        deploy_status=rep.deploy_status, tier=rep.tier,
        hosts=[h for h in rep.hosts if (h.kind or "") != "router"],
        applied=rep.applied, op_census=rep.op_census,
    )
    row = row_2_topology(ref, stripped, gate)
    assert row.measured["pairs_without_router_leg"] == ["seg-corp|seg-data"]
    assert row.verdict == FAIL


def test_row3_fails_when_a_declared_action_never_ran(mini):
    ref, rep, gate = mini
    rep.applied["web-tier01"].script_names.clear()
    row = row_3_configuration(ref, rep, gate)
    assert row.verdict == FAIL
    assert row.measured["unapplied_count"] == 1
    assert row.measured["unapplied"][0]["host"] == "web-tier01"
    assert row.measured["unapplied"][0]["kind"] == "script"


def test_row3_fails_when_a_declared_host_is_not_in_the_roster(mini):
    ref, rep, gate = mini
    rep.hosts = [h for h in rep.hosts if h.host_id != "web-tier02"]
    row = row_3_configuration(ref, rep, gate)
    assert row.verdict == FAIL
    assert row.measured["hosts_missing_from_roster"] == ["web-tier02"]


def test_row3_reads_only_completed_op_rows(tmp_path):
    """LES-113/LES-159: `inputs_resolved_json` holds DESIRED inputs from
    plan-insert onward, so a non-`COMPLETED` row must not count as applied —
    and the filter is "is completed", never "is not destroyed"."""
    from axrange.fidelity import build_reproduced

    store, deploy_id = build_mini_deploy(tmp_path)
    try:
        target = next(
            op for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "run-script"
            and json.loads(op.scope_json).get("host") == "web-tier01"
        )
        store.fail_op(deploy_id, target.op_id)
        rep = build_reproduced(store, deploy_id)
        ref = load_reference_bundle(MINI_CAST)
        from axrange.fidelity import resolve_gate_info

        gate = resolve_gate_info(
            config_id=rep.scenario_id, ir_hash=rep.ir_hash,
            catalog_dir=[str(CAST_BUNDLES)],
        )
    finally:
        store.close()
    assert rep.applied["web-tier01"].script_names == []
    assert rep.op_census["run-script"]["failed"] == 1
    row = row_3_configuration(ref, rep, gate)
    assert row.verdict == FAIL
    assert row.measured["run_script_completed"] == 1


def test_reproduced_tolerates_an_op_row_missing_a_recently_added_key(tmp_path):
    """LES-096: an already-`completed` op's persisted inputs are frozen until
    `--fresh`, so a key added later is simply ABSENT. Every read is
    `.get(...)`-defensive: the host degrades to "nothing applied", never a
    KeyError."""
    import sqlite3

    from axrange.fidelity import build_reproduced

    store, deploy_id = build_mini_deploy(tmp_path)
    try:
        target = next(
            op for op in store.ops_for_deploy(deploy_id)
            if op.op_name == "provision-app"
            and json.loads(op.scope_json).get("host") == "web-tier01"
        )
        stripped = {
            k: v for k, v in json.loads(target.inputs_resolved_json).items()
            if k != "install_actions"
        }
    finally:
        store.close()
    # Rewritten in SQL on purpose: there is no `StateStore` method that
    # edits a completed op's persisted inputs, and there should not be one
    # (the freeze IS the behavior under test). This reproduces what an older
    # deploy's row shape actually looks like on disk.
    con = sqlite3.connect(str(tmp_path / "state.db"))
    con.execute(
        "UPDATE ops SET inputs_resolved_json=? WHERE deploy_id=? AND op_id=?",
        (json.dumps(stripped), deploy_id, target.op_id),
    )
    con.commit()
    con.close()

    from axrange.planner import StateStore

    store = StateStore(str(tmp_path / "state.db"))
    try:
        rep = build_reproduced(store, deploy_id)
    finally:
        store.close()
    assert rep.applied["web-tier01"].install_actions == []
    assert rep.applied["web-tier02"].install_actions == [{"pkg": "nginx"}]


# ===========================================================================
# Row 5 with a probe report
# ===========================================================================
def _target(host_id, port, transport, method, segment="seg-corp"):
    return ProbeTarget(
        key=f"{host_id}:{port}/{transport}", host_id=host_id, ip="10.90.0.9",
        segment=segment, port=port, transport=transport, method=method,
        peer_host_id="peer", peer_vmid=9,
    )


def test_row5_denominator_and_group_resolution(mini):
    ref, rep, gate = mini
    facts = observed_listen_facts(ref, rep, gate)
    assert len(facts) == 3
    by_port = {f.port: f for f in facts}
    assert set(by_port) == {80, 443, 5432}
    # The observed web-tier hosts collapse into ONE cast group with count 2.
    assert set(by_port[80].candidates) == {"web-tier01", "web-tier02"}
    assert by_port[5432].candidates == ("db-tier",)
    assert by_port[80].method == "http" and by_port[443].method == "https"
    assert by_port[5432].method == "connect"   # unmapped -> never a pass


def test_row5_passes_when_every_fact_answers(mini):
    ref, rep, gate = mini
    targets = [
        _target("web-tier01", 80, "tcp", "http"),
        _target("web-tier02", 80, "tcp", "http"),
        _target("web-tier01", 443, "tcp", "https"),
        _target("web-tier02", 443, "tcp", "https"),
        _target("db-tier", 5432, "tcp", "ldap", segment="seg-data"),
    ]
    report = ProbeReport(ran=True, targets=targets, results={
        t.key: ProbeResult(t.key, PASS, t.method, True, "ok") for t in targets
    })
    row = row_5_protocol(ref, rep, gate, report)
    assert row.verdict == PASS
    assert row.measured["passed"] == 3 and row.measured["failed"] == 0


def test_row5_one_answering_candidate_is_enough(mini):
    """A cast group's `count:` hosts are interchangeable — the observed fact
    is reproduced if ANY of them answers."""
    ref, rep, gate = mini
    targets = [_target("web-tier01", 80, "tcp", "http"),
               _target("web-tier02", 80, "tcp", "http")]
    report = ProbeReport(ran=True, targets=targets, results={
        "web-tier01:80/tcp": ProbeResult("web-tier01:80/tcp", FAIL, "http", True, "refused"),
        "web-tier02:80/tcp": ProbeResult("web-tier02:80/tcp", PASS, "http", True, "200"),
    })
    row = row_5_protocol(ref, rep, gate, report)
    assert row.measured["passed"] == 1
    # The other two facts have twin hosts but no probe result -> UNMEASURED,
    # a measurement gap, not a reproduction failure: the row cannot pass, and
    # it does not claim the services are absent either.
    assert row.verdict == NOT_COMPUTED
    assert row.measured["unmeasured"] == 2
    assert row.measured["unresolved"] == 0


def test_row5_inconclusive_never_reads_as_a_pass(mini):
    """A bare TCP connect proves the port is open, not that the protocol
    answered. Nothing failed here, and the row still refuses to pass."""
    ref, rep, gate = mini
    targets = [
        _target("web-tier01", 80, "tcp", "http"),
        _target("web-tier02", 80, "tcp", "http"),
        _target("web-tier01", 443, "tcp", "https"),
        _target("web-tier02", 443, "tcp", "https"),
        _target("db-tier", 5432, "tcp", "connect", segment="seg-data"),
    ]
    results = {
        t.key: ProbeResult(t.key, PASS, t.method, True, "ok") for t in targets
    }
    results["db-tier:5432/tcp"] = ProbeResult(
        "db-tier:5432/tcp", INCONCLUSIVE, "connect", False,
        "TCP connect succeeded (not protocol-aware)",
    )
    row = row_5_protocol(ref, rep, gate, ProbeReport(
        ran=True, targets=targets, results=results,
    ))
    assert row.verdict == NOT_COMPUTED
    assert row.reason and "no protocol-aware probe method" in row.reason
    assert row.measured["inconclusive"] == 1


def test_row5_empty_listen_set_is_not_computed_with_a_denominator(mini):
    """LES-101: "100% of 0" is the classic vacuous pass."""
    ref, rep, gate = mini
    for n in ref.observed_nodes:
        object.__setattr__(n, "listen_ports", ())
    row = row_5_protocol(ref, rep, gate, ProbeReport(ran=True))
    assert row.verdict == NOT_COMPUTED
    assert "empty" in (row.reason or "")
    assert row.measured["observed_listen_facts"] == 0
    assert row.measured["observed_placed_hosts"] == 8


def test_row5_fails_a_fact_whose_group_has_no_twin_host(mini):
    """A group the cast never emitted is a REPRODUCTION gap, not a
    measurement one -- it fails, distinct from `unmeasured`."""
    ref, rep, gate = mini
    rep.hosts = [h for h in rep.hosts if not h.host_id.startswith("web-tier")]
    row = row_5_protocol(ref, rep, gate, ProbeReport(ran=True))
    assert row.verdict == FAIL
    assert row.measured["unresolved"] == 2
    assert all("no deployed twin host" in u["reason"]
               for u in row.measured["unresolved_facts"])


def test_row5_excludes_a_gated_out_host_s_listen_fact_instead_of_failing(mini):
    """M2 / spec §3.4: a host the deploy gate kept out resolves to no twin
    candidate, so before this it read as a REPRODUCTION FAILURE -- the exact
    inversion of "reported, never failed". The fixture's gated-out
    `thin-endpoint` observed host carries a real listen fact so the path is
    genuinely exercised (a gated-out host without one proves nothing)."""
    ref, rep, gate = mini
    thin = next(n for n in ref.observed_nodes if n.id == "address:10.1.2.11")
    assert thin.listen_ports, "the fixture must give the gated-out host a listen"
    assert gate.gate_active and "seg-thin" in gate.gated_segments

    row = row_5_protocol(ref, rep, gate, ProbeReport(ran=True))
    m = row.measured
    assert m["excluded_below_deploy_threshold"] == len(thin.listen_ports)
    below = [e for e in m["excluded_facts"] if e["fact"].startswith(thin.id)]
    assert below and all("below deploy threshold" in e["reason"] for e in below)
    # It is NOT in the denominator, NOT unresolved, and NOT a failure.
    assert not any(f.startswith(thin.id) for f in m["port_set"])
    assert all(not u["fact"].startswith(thin.id) for u in m["unresolved_facts"])
    assert any(c.clause == "below_deploy_threshold" for c in row.clauses)


def test_row5_scores_only_hosts_whose_clone_completed(mini):
    """M5: a planned-but-never-cloned host is not a candidate."""
    ref, rep, gate = mini
    for h in rep.hosts:
        if h.host_id == "web-tier02":
            h.vm_status = "pending"
    # `host_ids`/`deployed_host_ids` are `cached_property` (S11), so a test
    # that MUTATES `hosts` after construction must drop the cache. Production
    # callers never do -- `build_reproduced` fills `hosts` once, and the row
    # functions are pure readers.
    rep.__dict__.pop("deployed_host_ids", None)
    rep.__dict__.pop("host_ids", None)
    facts = observed_listen_facts(ref, rep, gate)
    for f in facts:
        assert "web-tier02" not in f.candidates


def test_the_windows_host_is_never_a_probe_peer_but_stays_a_target(mini):
    """M1, end to end on the fixture: `wait-os-ready`'s recorded `os_family`
    reaches `Reproduced.host_os_family` through the real op rows, and
    `compose_probe_targets` never peers to a Windows guest -- while a
    Windows host remains a perfectly valid probe TARGET (it is being asked,
    not asking)."""
    from axrange.fidelity.runner import compose_probe_targets

    ref, rep, gate = mini
    assert rep.host_os_family["win-endpoint"] == "windows"
    assert not rep.is_linux("win-endpoint")
    facts = observed_listen_facts(ref, rep, gate)
    targets, _notes = compose_probe_targets(
        facts, rep.hosts, is_linux=rep.is_linux,
    )
    assert targets, "the fixture must produce targets for this to prove anything"
    assert "win-endpoint" not in {t.peer_host_id for t in targets}

    # ...and it WOULD be chosen without the filter, whenever it is the only
    # leaf left on the segment -- the measured failure this guards (28 of 45
    # targets on the standing twin). Asserted on the selector directly,
    # because on this fixture `web-tier01` merely happens to sort first.
    from axrange.fidelity.runner import select_probe_peer

    others = {"web-tier01", "web-tier02"}
    unfiltered, _ = select_probe_peer(rep.hosts, "seg-corp", exclude=others)
    assert unfiltered.host_id == "win-endpoint"
    filtered, router_only = select_probe_peer(
        rep.hosts, "seg-corp", exclude=others, is_linux=rep.is_linux,
    )
    # The router is a weaker vantage, and the caller is told so rather than
    # being handed it silently.
    assert filtered.host_id == "gw" and router_only is True


# ===========================================================================
# Unconfirmed threshold (spec §3.4) and a bundle with no topology
# ===========================================================================
def test_unconfirmed_threshold_declines_to_score_rows_1_and_2(mini):
    ref, rep, _gate = mini
    gate = GateInfo(
        confirmed=False, config_id="mini-cast",
        reason="the config drifted since deploy",
    )
    for row in (row_1_asset(ref, rep, gate), row_2_topology(ref, rep, gate)):
        assert row.verdict == NOT_COMPUTED
        assert "UNCONFIRMED" in (row.reason or "")
    card = build_scorecard(
        reference=ref, reproduced=rep, gate=gate,
        generated_at="2026-09-05T00:00:00Z",
    )
    # An unevaluable gate makes the document `incomplete`, NEVER `not-golden`.
    assert card.verdict == INCOMPLETE
    assert any("UNCONFIRMED" in w for w in card.warnings)


def test_bundle_without_a_topology_export_not_computes_the_wire_rows(mini):
    from tests.test_fidelity_reference import FIXTURE_IMPRINT

    _ref, rep, gate = mini
    ref = load_reference_bundle(FIXTURE_IMPRINT)
    for row in (row_1_asset(ref, rep, gate), row_2_topology(ref, rep, gate),
                row_5_protocol(ref, rep, gate, None)):
        assert row.verdict == NOT_COMPUTED
        assert "topology.json" in (row.reason or "")


def test_row11_is_attested_never_inferred(mini):
    ref, rep, gate = mini
    card = build_scorecard(
        reference=ref, reproduced=rep, gate=gate,
        generated_at="2026-09-05T00:00:00Z",
    )
    assert card.row(11).verdict == NOT_ATTESTED
    attested = build_scorecard(
        reference=ref, reproduced=rep, gate=gate,
        generated_at="2026-09-05T00:00:00Z",
        attestation={"note": "looked at both panels", "attested_at": "2026-09-05T00:00:00Z"},
    )
    assert attested.row(11).verdict == "attested"
    assert attested.row(11).evidence.to_dict()["asserted"] == 1
    # Row 11 is REPORTED -- an attestation cannot change the document verdict.
    assert attested.verdict == card.verdict

def test_row1_empty_denominator_is_not_computed_not_pass(mini):
    """M3: "covered 0 of 0" is the same vacuous pass row 5 already refuses
    (LES-101)."""
    ref, rep, gate = mini
    for n in ref.observed_nodes:
        object.__setattr__(n, "windows_seen", 0)
    row = row_1_asset(ref, rep, gate)
    assert row.verdict == NOT_COMPUTED
    assert "nothing to check coverage of" in (row.reason or "")
    assert row.measured["observed_placed"] == 8
    assert row.measured["observed_above_threshold"] == 0


def test_row2_empty_denominator_is_not_computed_not_pass(mini):
    ref, rep, gate = mini
    ref.observed_segments = []
    ref.observed_edges = []
    for n in ref.observed_nodes:
        object.__setattr__(n, "segment_name", None)
    row = row_2_topology(ref, rep, gate)
    assert row.verdict == NOT_COMPUTED
    assert "nothing to map or route" in (row.reason or "")
    assert row.measured["observed_segments"] == 0
    assert row.measured["observed_cross_segment_pairs"] == 0


def test_rows_score_only_hosts_whose_clone_completed(mini):
    """M5, rows 1/2/3: `inputs_resolved_json` is persisted at plan-INSERT
    time, so a deploy that failed mid-run still publishes a full roster.
    Scoring those as reproduced lets a twin that never stood up read as
    covered."""
    ref, rep, gate = mini
    for h in rep.hosts:
        if h.host_id in ("web-tier02", "gw"):
            h.vm_status = "pending"
    # `host_ids`/`deployed_host_ids` are `cached_property` (S11), so a test
    # that MUTATES `hosts` after construction must drop the cache. Production
    # callers never do -- `build_reproduced` fills `hosts` once, and the row
    # functions are pure readers.
    rep.__dict__.pop("deployed_host_ids", None)
    rep.__dict__.pop("host_ids", None)
    assert rep.planned_not_deployed == ["gw", "web-tier02"]

    r1 = row_1_asset(ref, rep, gate)
    # web-tier's group now emits ONE live host against two observed.
    web = next(g for g in r1.measured["uncovered_groups"] if g["role"] == "web-tier")
    assert web["emitted"] == 1 and web["observed_above_threshold"] == 2
    assert r1.measured["planned_not_deployed"] == ["gw", "web-tier02"]

    # The router never cloned, so no segment has a router leg.
    r2 = row_2_topology(ref, rep, gate)
    assert r2.measured["router_segments"] == 0
    assert r2.measured["pairs_without_router_leg"] == ["seg-corp|seg-data"]

    r3 = row_3_configuration(ref, rep, gate)
    assert r3.verdict == FAIL
    assert r3.measured["hosts_missing_from_roster"] == ["web-tier02"]


def test_row2_reports_a_declared_segment_that_was_never_attached(mini):
    """S5: the reproduced side is the DEPLOYED twin, not the config that
    asked for it. A segment declared and never materialized must not pass
    row 2 by existing on paper."""
    ref, rep, gate = mini
    for h in rep.hosts:
        h.nets = [n for n in h.nets if n.net_id != "seg-data"]
    row = row_2_topology(ref, rep, gate)
    assert row.measured["declared_not_attached"] == ["seg-data"]
    assert row.verdict == FAIL
    assert any(c.clause == "declared_vs_attached" and c.verdict == FAIL
               for c in row.clauses)


def test_capped_lists_always_carry_their_full_count(mini):
    """S9: a truncated list with no count beside it silently understates
    every finding it holds -- "3 unreproduced hosts" reads the same whether
    there were 3 or 300."""
    ref, rep, gate = mini
    probed = row_5_protocol(ref, rep, gate, ProbeReport(ran=True, results={}))
    for row in (row_1_asset(ref, rep, gate), row_2_topology(ref, rep, gate),
                row_3_configuration(ref, rep, gate),
                row_5_protocol(ref, rep, gate, None), probed):
        m = row.measured
        for key, value in list(m.items()):
            if isinstance(value, list) and not key.endswith("_count"):
                assert f"{key}_count" in m, f"row {row.row}: {key} has no count"


def test_sidecar_backed_rows_read_their_classes_from_the_producer(tmp_path):
    """M4: with a canonical sidecar the twin-side facts of rows 1-3 come
    from `fields`, and `evidence_source` says so."""
    from axrange.fidelity import build_reproduced, resolve_gate_info
    from tests.fidelity_support import MINI_CAST_PROV

    store, deploy_id = build_mini_deploy(
        tmp_path, deploy_id="mini-prov-1", config_id="mini-cast-prov",
    )
    try:
        rep = build_reproduced(store, deploy_id)
    finally:
        store.close()
    ref = load_reference_bundle(MINI_CAST_PROV)
    gate = resolve_gate_info(
        config_id=rep.scenario_id, ir_hash=rep.ir_hash,
        catalog_dir=[str(CAST_BUNDLES)],
    )
    r1 = row_1_asset(ref, rep, gate)
    r3 = row_3_configuration(ref, rep, gate)
    assert r1.measured["evidence_source"] == "sidecar fields"
    # `asserted` can only come from the sidecar -- the topology export has no
    # such class anywhere, so its presence proves the fields were consumed.
    assert r1.evidence.to_dict()["asserted"] == 1
    assert r3.evidence.to_dict()["asserted"] == 1
    assert r3.measured["evidence_source"] == "sidecar fields"
