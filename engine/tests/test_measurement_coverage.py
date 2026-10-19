"""A4 measurement & scoring: axrange.measurement (docs/design/08-exercise-measurement.md).

Covers the three pieces that live outside a Loki query (so they're unit-
testable per the work order's validation requirement):
  - the [axr-truth] marker parser;
  - the truth x detections coverage join (technique+host+Δ-window);
  - detection -> ATT&CK technique resolution (self-label + fallback map).
"""
from __future__ import annotations

from datetime import datetime

from axrange.measurement.coverage import (
    Detection,
    RunCoverage,
    TruthMarker,
    join_coverage,
    parse_truth_marker,
)
from axrange.measurement.detections import (
    FALLBACK_RULE_TECHNIQUE_MAP,
    parse_technique_from_rule,
    technique_for_detection,
)

# ---------------------------------------------------------------------------
# render <-> parse round trip (code-review SHOULD-FIX #2)
#
# The marker line is DEFINED in config_exec/render.py (the echo template
# `_render_step` embeds) and INDEPENDENTLY RE-PARSED here in coverage.py
# (`_TRUTH_MARKER_RE`) -- the LES-025 leaf split (the engine never imports
# axrange.measurement) means the two can't share one literal format string,
# so they can drift silently if either side's separator/field-order changes.
# This test is the drift guard: it renders the REAL template via
# `render_activity_runner` (not a hand-typed guess of what render.py
# produces), substitutes the shell placeholders ($RUN_ID / $(hostname) /
# $(date ...)) with concrete sample values the way the GUEST shell would at
# runtime, and asserts `parse_truth_marker` recovers exactly those values.
# ---------------------------------------------------------------------------


def test_render_and_parse_truth_marker_round_trip():
    from axrange.config_exec.render import render_activity_runner

    out = render_activity_runner(
        [{"run": "true", "technique": "T1046"}], activity_id="rt-test",
    )
    marker_lines = [ln for ln in out.splitlines() if "[axr-truth]" in ln]
    assert len(marker_lines) == 1, marker_lines
    echo_line = marker_lines[0]
    # `echo "<content>" >> "$TRUTH_LOG"` -- pull out <content>, the exact
    # string the guest shell would build (module-private shape; if this
    # extraction ever breaks, the render-side template shape changed and
    # this test SHOULD be revisited alongside it).
    content = echo_line.split('echo "', 1)[1].rsplit('" >> "$TRUTH_LOG"', 1)[0]
    assert content.startswith("[axr-truth] run=$RUN_ID technique=T1046 host=$(hostname) ts=")

    # Substitute the shell placeholders with concrete sample values -- exactly
    # what $RUN_ID / $(hostname) / $(date -u +%Y-%m-%dT%H:%M:%SZ) resolve to
    # at guest runtime.
    expanded = (
        content
        .replace("$RUN_ID", "r1")
        .replace("$(hostname)", "h")
        .replace("$(date -u +%Y-%m-%dT%H:%M:%SZ)", "2026-07-11T10:00:00Z")
    )
    assert expanded == "[axr-truth] run=r1 technique=T1046 host=h ts=2026-07-11T10:00:00Z"

    marker = parse_truth_marker(expanded)
    assert marker == TruthMarker(
        run_id="r1", technique="T1046", host="h",
        ts=datetime(2026, 7, 11, 10, 0, 0),
    )


# ---------------------------------------------------------------------------
# parse_truth_marker
# ---------------------------------------------------------------------------


def test_parse_truth_marker_extracts_fields():
    line = "[axr-truth] run=abc-123 technique=T1046 host=attacker ts=2026-07-11T10:00:00Z"
    m = parse_truth_marker(line)
    assert m == TruthMarker(
        run_id="abc-123", technique="T1046", host="attacker",
        ts=datetime(2026, 7, 11, 10, 0, 0),
    )


def test_parse_truth_marker_tolerates_surrounding_text():
    """Log lines commonly carry extra context (a syslog/Loki prefix) around
    the marker -- `search`, not `match`, so the marker is still found."""
    line = '2026-07-11T10:00:01.5Z stdout F [axr-truth] run=r1 technique=T1003.006 host=dc01 ts=2026-07-11T10:00:01Z'
    m = parse_truth_marker(line)
    assert m is not None
    assert m.run_id == "r1"
    assert m.technique == "T1003.006"
    assert m.host == "dc01"


def test_parse_truth_marker_returns_none_for_non_matching_line():
    assert parse_truth_marker("[axrange] --- step 1 ---") is None
    assert parse_truth_marker("") is None


def test_parse_truth_marker_returns_none_for_unparseable_ts():
    line = "[axr-truth] run=r1 technique=T1046 host=h ts=not-a-timestamp"
    assert parse_truth_marker(line) is None


# ---------------------------------------------------------------------------
# join_coverage
# ---------------------------------------------------------------------------


def test_join_coverage_covered_when_detection_in_window():
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0))]
    det = [Detection("T1046", "attacker", datetime(2026, 7, 11, 10, 0, 30), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    r1 = cov["r1"]
    assert isinstance(r1, RunCoverage)
    assert r1.covered_count == 1
    assert r1.missed_count == 0
    assert r1.coverage_pct == 100.0
    assert r1.techniques[0].covered is True
    assert r1.techniques[0].matched_detections == tuple(det)


def test_join_coverage_missed_when_no_detection():
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0))]
    cov = join_coverage(truth, [], window_s=60)
    r1 = cov["r1"]
    assert r1.covered_count == 0
    assert r1.missed_count == 1
    assert r1.coverage_pct == 0.0
    assert r1.missed_techniques == ("T1046",)


def test_join_coverage_missed_when_detection_outside_window():
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0))]
    det = [Detection("T1046", "attacker", datetime(2026, 7, 11, 10, 5, 0), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    assert cov["r1"].missed_count == 1


def test_join_coverage_missed_when_technique_mismatch():
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0))]
    det = [Detection("T1110", "attacker", datetime(2026, 7, 11, 10, 0, 10), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    assert cov["r1"].missed_count == 1


def test_join_coverage_missed_when_host_mismatch():
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0))]
    det = [Detection("T1046", "dc01", datetime(2026, 7, 11, 10, 0, 10), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    assert cov["r1"].missed_count == 1


def test_join_coverage_ignores_detection_before_truth_ts():
    """A detection that fires BEFORE the truth marker isn't evidence for it
    (the window is [ts, ts+Δ], not centered)."""
    truth = [TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 30))]
    det = [Detection("T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    assert cov["r1"].missed_count == 1


def test_join_coverage_groups_by_run_id():
    truth = [
        TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0)),
        TruthMarker("r2", "T1046", "attacker", datetime(2026, 7, 11, 11, 0, 0)),
    ]
    det = [Detection("T1046", "attacker", datetime(2026, 7, 11, 10, 0, 10), source="suricata")]
    cov = join_coverage(truth, det, window_s=60)
    assert set(cov.keys()) == {"r1", "r2"}
    assert cov["r1"].covered_count == 1
    assert cov["r2"].missed_count == 1  # the same detection doesn't reach r2's window


def test_run_coverage_mixed_matrix_pct_and_missed_list():
    truth = [
        TruthMarker("r1", "T1046", "attacker", datetime(2026, 7, 11, 10, 0, 0)),
        TruthMarker("r1", "T1003.006", "dc01", datetime(2026, 7, 11, 10, 5, 0)),
        TruthMarker("r1", "T1558.003", "dc01", datetime(2026, 7, 11, 10, 10, 0)),
    ]
    det = [
        Detection("T1046", "attacker", datetime(2026, 7, 11, 10, 0, 5), source="suricata"),
        Detection("T1003.006", "dc01", datetime(2026, 7, 11, 10, 5, 20), source="wazuh"),
    ]
    r1 = join_coverage(truth, det, window_s=60)["r1"]
    assert r1.covered_count == 2
    assert r1.missed_count == 1
    assert abs(r1.coverage_pct - (200 / 3)) < 0.01
    assert r1.missed_techniques == ("T1558.003",)


def test_join_coverage_empty_truth_returns_empty_dict():
    assert join_coverage([], []) == {}


def test_run_coverage_pct_zero_techniques_no_zerodiv():
    r = RunCoverage(run_id="r1", techniques=())
    assert r.coverage_pct == 0.0
    assert r.covered_count == 0
    assert r.missed_count == 0


# ---------------------------------------------------------------------------
# detection -> technique resolution
# ---------------------------------------------------------------------------


def test_parse_technique_from_rule_finds_bare_token():
    assert parse_technique_from_rule("ET POLICY DCSync attempt T1003.006") == "T1003.006"


def test_parse_technique_from_rule_first_arg_wins():
    assert parse_technique_from_rule("<mitre><id>T1558.003</id></mitre>", "T1046 nmap scan") == "T1558.003"


def test_parse_technique_from_rule_falls_through_empty_args():
    assert parse_technique_from_rule("", None, "signature mentions T1110 brute force") == "T1110"


def test_parse_technique_from_rule_returns_none_when_absent():
    assert parse_technique_from_rule("no technique here", "") is None


def test_technique_for_detection_self_label_from_mitre_raw():
    got = technique_for_detection(mitre_raw="<mitre><id>T1558.003</id></mitre>", signature="generic kerberos alert")
    assert got == "T1558.003"


def test_technique_for_detection_self_label_from_signature_token():
    got = technique_for_detection(signature="ET POLICY Possible T1046 port scan")
    assert got == "T1046"


def test_technique_for_detection_fallback_map_dcsync():
    got = technique_for_detection(signature="ET POLICY Possible DCSync replication request")
    assert got == FALLBACK_RULE_TECHNIQUE_MAP["dcsync"]


def test_technique_for_detection_fallback_map_case_insensitive():
    got = technique_for_detection(rule_id="WAZUH-KERBEROAST-001", signature="Kerberoast attempt detected")
    assert got == FALLBACK_RULE_TECHNIQUE_MAP["kerberoast"]


def test_technique_for_detection_unmapped_returns_none():
    assert technique_for_detection(signature="totally unrelated benign traffic") is None


def test_fallback_map_is_small_and_data_driven():
    """Guards the design intent (§4: 'keep it small and data-driven') -- not
    an exhaustive ruleset, just enough for the archetypes' current rules."""
    assert 5 <= len(FALLBACK_RULE_TECHNIQUE_MAP) <= 100
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in FALLBACK_RULE_TECHNIQUE_MAP.items())
