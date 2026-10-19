"""ATT&CK coverage join — truth x detections, on technique+host+Δ-window
(A4, docs/design/08-exercise-measurement.md).

The Grafana coverage dashboard (`o2/grafana/dashboards/attack-coverage.json`)
does the operator-facing version of this join as a LogQL query. This module
is the SAME join expressed as a pure, unit-testable python function, per the
A4 work order's validation requirement ("the coverage join must exist as
code, not only as a Grafana query") — a natural seam for a later automated
scorer (`o2control`, deferred) to call directly instead of re-deriving the
LogQL.

Data spine (docs/design/08-exercise-measurement.md):

    TRUTH      -- one TruthMarker per attempted technique-labeled step
                  (`[axr-truth] run=<run> technique=<T> host=<h> ts=<ts>`,
                  config_exec/render.py::render_activity_runner)
    DETECTIONS -- Wazuh/Suricata/host-audit alerts, already technique-resolved
                  via `axrange.measurement.detections.technique_for_detection`

    COVERED(technique, host, run) iff some Detection with the SAME technique
    and host has a timestamp in [truth.ts, truth.ts + window_s].

This module is O2-side analysis code; the engine does not import it
(LES-025 — a leaf, not a deploy-path dependency).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

# Matches the exact marker shape `_render_step`/`render_activity_runner`
# emit: `[axr-truth] run=<run> technique=<T> host=<h> ts=<iso8601>`. Tolerant
# of extra leading/trailing text (Alloy/Loki log lines commonly carry
# surrounding context) via `search`, not `match`.
_TRUTH_MARKER_RE = re.compile(
    r"\[axr-truth\]\s+run=(?P<run>\S+)\s+technique=(?P<technique>\S+)\s+"
    r"host=(?P<host>\S+)\s+ts=(?P<ts>\S+)"
)


def _parse_ts(raw: str) -> datetime:
    """Parse the marker's `ts=` value (`%Y-%m-%dT%H:%M:%SZ`, the format
    `render_activity_runner`'s `date -u +%Y-%m-%dT%H:%M:%SZ` produces) or any
    other ISO-8601 string a detection source might carry. Naive-UTC in, naive
    UTC out (both truth and detection timestamps are treated as UTC — no
    timezone math needed for a single-cluster lab)."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


@dataclass(frozen=True)
class TruthMarker:
    """One executed, technique-labeled activity step (or a Caldera/Atomic
    operation-report entry mapped into the same shape — the "clean seam" the
    work order asks for: anything producing a `TruthMarker` feeds this join,
    regardless of source)."""

    run_id: str
    technique: str
    host: str
    ts: datetime


@dataclass(frozen=True)
class Detection:
    """One O2 detection (Suricata/Wazuh/host-audit), already technique-resolved
    (`axrange.measurement.detections.technique_for_detection`). `technique`
    is `None`/empty for an alert that couldn't be mapped — such detections
    never match a truth marker (excluded from the join, not a false miss).

    `ts` MUST be naive-UTC (no tzinfo) — matching `parse_truth_marker`'s
    output (`_parse_ts` assumes a `Z`-suffixed/already-UTC input and just
    strips tzinfo, since that's all the runner's marker ever produces).
    `join_coverage` compares `Detection.ts`/`TruthMarker.ts` directly, and
    mixing an aware `ts` in here raises `TypeError: can't compare
    offset-naive and offset-aware datetimes`. A future Loki-backed detection
    scorer reading Suricata/Wazuh timestamps (which often arrive tz-aware,
    e.g. RFC3339 with a non-UTC offset) must convert to UTC FIRST
    (`dt.astimezone(timezone.utc)`) and only then strip tzinfo
    (`.replace(tzinfo=None)`) before constructing a `Detection` — stripping
    tzinfo off a non-UTC-offset timestamp without converting first silently
    mislabels its wall-clock time as UTC."""

    technique: str | None
    host: str
    ts: datetime
    source: str = ""      # "suricata" | "wazuh" | "host-audit" | ...
    signature: str = ""   # raw rule/signature text, kept for traceability


@dataclass(frozen=True)
class TechniqueCoverage:
    """One truth marker's coverage verdict."""

    technique: str
    host: str
    truth_ts: datetime
    covered: bool
    matched_detections: tuple[Detection, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunCoverage:
    """The full coverage matrix for one `run_id`."""

    run_id: str
    techniques: tuple[TechniqueCoverage, ...]

    @property
    def covered_count(self) -> int:
        return sum(1 for t in self.techniques if t.covered)

    @property
    def missed_count(self) -> int:
        return sum(1 for t in self.techniques if not t.covered)

    @property
    def coverage_pct(self) -> float:
        if not self.techniques:
            return 0.0
        return 100.0 * self.covered_count / len(self.techniques)

    @property
    def missed_techniques(self) -> tuple[str, ...]:
        return tuple(t.technique for t in self.techniques if not t.covered)


def parse_truth_marker(line: str) -> TruthMarker | None:
    """Parse one raw log line into a `TruthMarker`, or `None` if the line
    isn't (or doesn't contain) an `[axr-truth]` marker or its `ts=` isn't
    parseable."""
    m = _TRUTH_MARKER_RE.search(line)
    if not m:
        return None
    try:
        ts = _parse_ts(m.group("ts"))
    except ValueError:
        return None
    return TruthMarker(
        run_id=m.group("run"), technique=m.group("technique"),
        host=m.group("host"), ts=ts,
    )


def join_coverage(
    truth: Iterable[TruthMarker],
    detections: Iterable[Detection],
    *,
    window_s: float = 60.0,
) -> dict[str, RunCoverage]:
    """Join truth markers against detections on `(technique, host)` within a
    `[ts, ts + window_s]` window (docs/design/08-exercise-measurement.md: "a
    technique is covered ... if a detection ... fires within [ts, ts+Δ] of
    the truth marker"), grouped by `run_id`.

    Returns `{run_id: RunCoverage}` — one entry per distinct `run_id` present
    in `truth`. A technique is `covered` if AT LEAST ONE detection matches;
    all matching detections are kept on `matched_detections` (not just the
    first) so a caller can inspect e.g. detection latency.

    Note: detections carry no `run_id` (an alert is just an alert) — if two
    runs' truth-marker windows for the SAME (technique, host) overlap in
    time, a single detection can satisfy both runs' coverage. This mirrors
    the design brief's assumption that a coverage query is scoped to one
    run's time range in practice (§5/§6); resolving cross-run attribution
    more precisely is out of scope here (follow-on automated scorer).
    """
    truth_list = list(truth)
    detection_list = list(detections)

    by_run: dict[str, list[TruthMarker]] = {}
    for marker in truth_list:
        by_run.setdefault(marker.run_id, []).append(marker)

    runs: dict[str, RunCoverage] = {}
    for run_id, markers in by_run.items():
        techs: list[TechniqueCoverage] = []
        for marker in markers:
            window_end = marker.ts + timedelta(seconds=window_s)
            matches = tuple(
                d for d in detection_list
                if d.technique == marker.technique
                and d.host == marker.host
                and marker.ts <= d.ts <= window_end
            )
            techs.append(
                TechniqueCoverage(
                    technique=marker.technique, host=marker.host,
                    truth_ts=marker.ts, covered=bool(matches),
                    matched_detections=matches,
                )
            )
        runs[run_id] = RunCoverage(run_id=run_id, techniques=tuple(techs))
    return runs
