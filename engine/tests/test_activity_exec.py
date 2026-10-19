"""OP-ACT inline-steps activity engine (docs/SLICE_ACTIVITY.md).

Covers the three layers the slice adds:
  - render: inline steps -> a self-contained, syntactically-valid shell runner;
  - backend: deploy-behavior-agent / start-activity / wait-actor-ready over QGA;
  - planner: the OP-ACT trio emits with the right gating edges (host apps +
    mirror-ready), and the capture gate drops when the scenario has no taps.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    ActivityRenderError,
    activity_effective_seed,
    activity_paths,
    jitter_sequence,
    jitter_sequence_from_activity,
    render_activity_launch_script,
    render_activity_runner,
    render_activity_scheduler,
    render_activity_truth_shipper,
)
from axrange.ir.parser import parse_ir
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"
ACT_SCN = FIXTURES / "ir" / "valid_activity" / "scenario.yaml"


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_emits_tool_and_run_steps():
    steps = [
        {"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"},
        {"run": "echo hi >> /tmp/x"},
    ]
    out = render_activity_runner(steps, activity_id="a1")
    assert out.startswith("#!/bin/bash")
    assert "nmap -sn 10.0.0.0/24" in out
    assert "echo hi >> /tmp/x" in out
    # completion marker is touched last so wait-actor-ready can poll it.
    assert out.rstrip().endswith('touch "$MARKER"')
    assert activity_paths("a1")["marker"] in out


def test_render_is_valid_bash_with_operator_laden_run():
    """A `run:` step full of shell operators/quotes must stay one grouped step:
    the redirect + exit handler bind to the whole step, and the script parses."""
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("bash not available")
    steps = [
        {"run": 'for p in 22 80; do nc -z -w2 10.0.0.5 $p && echo "open:$p" >> /tmp/o; done; true'},
        {"tool": "curl", "args": "-s -m5", "target": "http://10.0.0.5/"},
    ]
    out = render_activity_runner(steps, activity_id="op-laden")
    r = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    # The step body is brace-grouped (so the LOG redirect wraps the whole step).
    assert "{\nfor p in 22 80" in out


def test_render_rejects_unknown_tool():
    with pytest.raises(ActivityRenderError) as e:
        render_activity_runner([{"tool": "msfconsole", "target": "x"}], activity_id="a")
    assert "vocabulary" in str(e.value)


def test_render_rejects_tool_and_run_together():
    with pytest.raises(ActivityRenderError):
        render_activity_runner([{"tool": "nmap", "run": "x"}], activity_id="a")


def test_render_rejects_step_without_tool_or_run():
    with pytest.raises(ActivityRenderError):
        render_activity_runner([{"target": "10.0.0.1"}], activity_id="a")


def test_render_rejects_empty_steps():
    with pytest.raises(ActivityRenderError):
        render_activity_runner([], activity_id="a")


def test_activity_paths_are_deterministic_and_sanitized():
    p1 = activity_paths("act/weird id")
    p2 = activity_paths("act/weird id")
    assert p1 == p2
    assert "/" not in Path(p1["runner"]).name  # sanitized into the filename
    assert p1["runner"].endswith("-runner.sh")


def test_activity_paths_two_activities_no_collision():
    """docs/SLICE_ACTIVITY.md §11.1: N activities on one host must never share
    a filename/marker/scheduler -- paths are keyed by activity_id alone (host
    is not part of the key), so two activities on the SAME host still get
    fully independent paths (including the new `scheduler` key)."""
    p1 = activity_paths("web-browsing")
    p2 = activity_paths("recon-sweep")
    assert p1["runner"] != p2["runner"]
    assert p1["marker"] != p2["marker"]
    assert p1["scheduler"] != p2["scheduler"]


# ---------------------------------------------------------------------------
# render -- A4 ground-truth marker (docs/design/08-exercise-measurement.md)
# ---------------------------------------------------------------------------


def test_render_emits_truth_marker_for_labeled_step():
    steps = [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24", "technique": "T1046"}]
    out = render_activity_runner(steps, activity_id="a1")
    assert '[axr-truth] run=$RUN_ID technique=T1046 host=$(hostname)' in out
    assert 'ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$TRUTH_LOG"' in out
    # the truth log is declared + touched, never truncated (unlike $LOG).
    assert f'TRUTH_LOG="{activity_paths("a1")["truth"]}"' in out
    assert 'touch "$TRUTH_LOG"' in out
    assert ': > "$TRUTH_LOG"' not in out


def test_render_no_truth_marker_without_technique():
    steps = [{"tool": "curl", "args": "-s", "target": "http://10.0.0.5/"}]
    out = render_activity_runner(steps, activity_id="a1")
    assert "[axr-truth]" not in out


def test_render_truth_marker_only_on_labeled_steps_in_mixed_chain():
    steps = [
        {"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24", "technique": "T1046"},
        {"run": "echo benign >> /tmp/x"},
        {"tool": "hydra", "args": "-l admin -P pw.txt", "target": "10.0.0.5", "technique": "T1110"},
    ]
    out = render_activity_runner(steps, activity_id="mixed")
    assert out.count("[axr-truth]") == 2
    assert "technique=T1046" in out
    assert "technique=T1110" in out


def test_render_run_id_generated_once_and_reused_by_every_marker():
    steps = [
        {"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24", "technique": "T1046"},
        {"tool": "hydra", "args": "-l admin -P pw.txt", "target": "10.0.0.5", "technique": "T1110"},
    ]
    out = render_activity_runner(steps, activity_id="a1")
    assert out.count('RUN_ID="$(cat /proc/sys/kernel/random/uuid') == 1
    # exactly one marker line per labeled step, each referencing the SAME
    # $RUN_ID (not the start/done bookend lines, which also say run=$RUN_ID).
    assert out.count('[axr-truth] run=$RUN_ID') == 2


def test_render_truth_marker_escapes_embedded_quote():
    """A stray `"` in an author-supplied technique value must not break the
    generated echo's quoting -- escaped at RENDER time (the value is IR
    content known when the script is rendered, not shell-resolved)."""
    steps = [{"run": "true", "technique": 'T1059" ; rm -rf /'}]
    out = render_activity_runner(steps, activity_id="a1")
    assert r'technique=T1059\" ; rm -rf /' in out
    if shutil.which("bash") is not None:
        r = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr


def test_render_activity_truth_shipper_is_valid_bash_and_embeds_url():
    out = render_activity_truth_shipper("a1", loki_url="http://10.0.0.5:3100/")
    assert out.startswith("#!/bin/bash")
    assert "http://10.0.0.5:3100/loki/api/v1/push" in out
    assert activity_paths("a1")["truth"] in out
    assert activity_paths("a1")["truth_shipper"] in out
    if shutil.which("bash") is not None:
        r = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
    body = out.split("<<'AXR_SHIP_PY'\n", 1)[1].split("\nAXR_SHIP_PY", 1)[0]
    compile(body, "<shipper>", "exec")  # the embedded python body is valid python


def test_render_activity_truth_shipper_two_activities_no_collision():
    out_a = render_activity_truth_shipper("act-a", loki_url="http://10.0.0.5:3100")
    out_b = render_activity_truth_shipper("act-b", loki_url="http://10.0.0.5:3100")
    assert activity_paths("act-a")["truth_shipper"] in out_a
    assert activity_paths("act-a")["truth_shipper"] not in out_b
    assert activity_paths("act-b")["truth_shipper"] in out_b
    assert activity_paths("act-b")["truth_shipper"] not in out_a


def test_render_activity_truth_shipper_no_pkill_uses_systemd_unit():
    """GAP-A fix (live-verified dev45 2026-07-15, docs/PRODUCT_GAPS.md,
    shared with `render_sensor_loki_shipper` via `_render_loki_tail_shipper`):
    the bootstrap must carry no `pkill` (a self-kill risk if the pattern is
    ever a substring of the delivering wrapper's own path) and must launch
    the shipper as a named systemd transient unit so it survives the QGA
    exec session that delivers it -- a bare `nohup ... &` gets reaped when
    that session ends."""
    out = render_activity_truth_shipper("a1", loki_url="http://10.0.0.5:3100")
    assert "pkill" not in out
    assert "nohup" not in out
    assert "systemd-run --unit=axr-truth-ship-a1 --collect" in out
    assert "systemctl stop axr-truth-ship-a1" in out
    assert "systemctl reset-failed axr-truth-ship-a1" in out
    assert "setsid python3" in out


def test_render_activity_truth_shipper_unit_stable_and_unique_per_activity():
    """Same stability/uniqueness contract as the sensor shipper's unit name:
    stable across re-renders of one activity, distinct across two."""
    out_a1 = render_activity_truth_shipper("act-a", loki_url="http://10.0.0.5:3100")
    out_a2 = render_activity_truth_shipper("act-a", loki_url="http://10.0.0.5:3100")
    out_b = render_activity_truth_shipper("act-b", loki_url="http://10.0.0.5:3100")
    assert "systemd-run --unit=axr-truth-ship-act-a " in out_a1
    assert "systemd-run --unit=axr-truth-ship-act-a " in out_a2
    assert "axr-truth-ship-act-b" not in out_a1
    assert "axr-truth-ship-act-b" in out_b


# ---------------------------------------------------------------------------
# render -- schedule executor (seeded jitter, docs/SLICE_ACTIVITY.md §11)
# ---------------------------------------------------------------------------

_STEPS = [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"}]
_INTERVAL_SCHEDULE = {"interval": {"min": "30s", "max": "5m"}}


def test_jitter_sequence_deterministic_with_same_seed():
    seq1 = jitter_sequence(30, 300, 12345, count=8)
    seq2 = jitter_sequence(30, 300, 12345, count=8)
    assert seq1 == seq2
    assert all(30 <= v <= 300 for v in seq1)


def test_jitter_sequence_differs_across_seeds():
    seq_a = jitter_sequence(30, 300, 1, count=8)
    seq_b = jitter_sequence(30, 300, 2, count=8)
    assert seq_a != seq_b


def test_activity_effective_seed_folds_activity_id_in():
    """Two activities sharing a raw seed value must NOT draw the same
    sequence -- the fold makes the effective seed activity-specific."""
    s1 = activity_effective_seed(1337, "web-browsing")
    s2 = activity_effective_seed(1337, "recon-sweep")
    assert s1 != s2
    assert jitter_sequence(30, 300, s1, count=5) != jitter_sequence(30, 300, s2, count=5)


def test_activity_effective_seed_none_when_no_seed():
    assert activity_effective_seed(None, "act") is None


def test_render_activity_scheduler_same_seed_same_script():
    """Same seed + same activity_id -> byte-identical rendered scheduler (the
    replay-loop determinism property, SLICE_ACTIVITY.md §11.2)."""
    out1 = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=_INTERVAL_SCHEDULE, seed=1337,
    )
    out2 = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=_INTERVAL_SCHEDULE, seed=1337,
    )
    assert out1 == out2


def test_render_activity_scheduler_is_valid_python():
    out = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=_INTERVAL_SCHEDULE, seed=1337,
    )
    compile(out, "<scheduler>", "exec")
    assert out.startswith("#!/usr/bin/env python3")
    eff = activity_effective_seed(1337, "web-browsing")
    assert f"SEED = {eff}" in out
    assert "MIN_S = 30.0" in out and "MAX_S = 300.0" in out


def test_render_activity_scheduler_embeds_runner_source():
    """The scheduler re-invokes the SAME step script one-shot activities use
    (byte-identical fire semantics), not a re-implementation."""
    runner = render_activity_runner(_STEPS, activity_id="web-browsing")
    out = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=_INTERVAL_SCHEDULE, seed=1337,
    )
    import base64
    import re

    m = re.search(r"RUNNER_B64 = '([^']+)'", out)
    assert m, out
    assert base64.b64decode(m.group(1)).decode("utf-8") == runner


def test_render_activity_scheduler_days_and_window_embedded():
    schedule = {
        "interval": {"min": "30s", "max": "5m"},
        "days": ["Mon", "Wed", "Fri"],
        "start": "09:00",
        "end": "17:00",
    }
    out = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=schedule, seed=1,
    )
    assert "DAYS = [0, 2, 4]" in out  # Mon=0, Wed=2, Fri=4
    assert "START_MIN = 540" in out  # 09:00
    assert "END_MIN = 1020" in out  # 17:00


def test_render_activity_scheduler_no_seed_still_renders():
    """No seed -> non-reproducible (SEED=None in the guest script), but the
    scheduler must still render and compile (ACT-070 only warns, never
    blocks the emission)."""
    out = render_activity_scheduler(
        _STEPS, activity_id="web-browsing", schedule=_INTERVAL_SCHEDULE, seed=None,
    )
    assert "SEED = None" in out
    compile(out, "<scheduler>", "exec")


def test_render_activity_scheduler_one_sided_window_ignored():
    """A `start` with no `end` (or vice versa) means "no time-of-day bound",
    not an error -- only a fully-paired start+end restricts the window."""
    out = render_activity_scheduler(
        _STEPS, activity_id="web-browsing",
        schedule={"interval": {"min": "30s", "max": "5m"}, "start": "09:00"},
        seed=1,
    )
    assert "START_MIN = None" in out
    assert "END_MIN = None" in out


def test_render_activity_scheduler_requires_min_and_max():
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a", schedule={"interval": {"min": "30s"}},
        )
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(_STEPS, activity_id="a", schedule={})


def test_render_activity_scheduler_max_less_than_min_raises():
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a",
            schedule={"interval": {"min": "5m", "max": "30s"}},
        )


def test_render_activity_scheduler_bad_duration_raises():
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a",
            schedule={"interval": {"min": "abc", "max": "5m"}},
        )


def test_render_activity_scheduler_bad_day_raises():
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a",
            schedule={"interval": {"min": "30s", "max": "5m"}, "days": ["Blursday"]},
        )


def test_render_activity_scheduler_two_activities_no_collision():
    """Two jittered activities on one host: independent scheduler paths AND
    independent runner paths embedded in each rendered script (no shared
    filename -- docs/SLICE_ACTIVITY.md §11.1)."""
    out_a = render_activity_scheduler(
        _STEPS, activity_id="act-a", schedule=_INTERVAL_SCHEDULE, seed=1,
    )
    out_b = render_activity_scheduler(
        _STEPS, activity_id="act-b", schedule=_INTERVAL_SCHEDULE, seed=1,
    )
    assert activity_paths("act-a")["scheduler"] != activity_paths("act-b")["scheduler"]
    assert activity_paths("act-a")["runner"] in out_a
    assert activity_paths("act-b")["runner"] in out_b
    assert activity_paths("act-a")["runner"] not in out_b
    assert activity_paths("act-b")["runner"] not in out_a
    assert out_a != out_b


# ---------------------------------------------------------------------------
# backend (fake QGA)
# ---------------------------------------------------------------------------


class _FakeGA:
    def __init__(self, *, marker_present=True, launch_ok=True, scheduler_running=True):
        self.marker_present = marker_present
        self.launch_ok = launch_ok
        self.scheduler_running = scheduler_running
        self.execs: list[tuple] = []
        self.written: dict[str, str] = {}
        self.mkdirs: set[str] = set()

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:2] == ["mkdir", "-p"]:
            self.mkdirs.add(argv[2])
        if argv[:2] == ["test", "-f"]:
            return GuestExecResult(0 if self.marker_present else 1, "", "")
        if argv[:1] == ["pgrep"]:  # jittered wait-actor-ready's liveness check
            return GuestExecResult(0 if self.scheduler_running else 1, "", "")
        if argv[:2] == ["sh", "-lc"]:  # the setsid launch
            return GuestExecResult(0 if self.launch_ok else 1, "", "boom")
        return GuestExecResult(0, "", "")  # mkdir etc.

    def write_file(self, vmid, path, content, *, mode=None):
        # Mirror the real QGA: writing into a dir that wasn't created first fails
        # (ENOENT). This regression-guards the dev45 live-verify bug where deploy
        # mkdir'd the provision dir but wrote the runner under the activity dir.
        parent = path.rsplit("/", 1)[0]
        if parent not in self.mkdirs:
            raise AdapterError(
                f"writing {path}: parent dir {parent} does not exist",
                error_class="guest-write-failed",
            )
        self.written[path] = content


def _ctx():
    return ExecutionContext(
        plan_id="p", deploy_id="d",
        cluster=ClusterContext(default_node="px1"), placement={},
    )


def _backend(fake, **kw) -> ConfigBackend:
    return ConfigBackend(
        AdapterConfig(), rest_client=object(),
        guest_agent_factory=lambda client, node: fake,
        sleep_fn=lambda s: None, **kw,
    )


def _op(name, catalog_id, *, kind=OpKind.ACTION, timeout_s=None):
    d = get_op(name)
    return OpInstance(
        op_id=f"p:{name}", op_name=name, op_catalog_id=catalog_id, kind=kind,
        scope={"act": "act-recon", "host": "attacker"},
        idempotency_class=IdempotencyClass.CONFIG,
        cleanup_strategy=d.cleanup_strategy,
        timeout_s=timeout_s,
    )


def test_deploy_behavior_agent_renders_and_writes_runner():
    fake = _FakeGA()
    out = _backend(fake).run(
        _op("deploy-behavior-agent", "OP-ACT-010"),
        {"vmid": 100, "activity_id": "act-recon",
         "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"}]},
        _ctx(),
    )
    assert out == {"agent_deployed": True}
    runner = activity_paths("act-recon")["runner"]
    assert runner in fake.written
    assert "nmap -sn 10.0.0.0/24" in fake.written[runner]
    # written, not executed, by deploy.
    assert not any(a[:2] == ("sh", "-lc") for a in fake.execs)


def test_deploy_behavior_agent_bad_step_raises():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA()).run(
            _op("deploy-behavior-agent", "OP-ACT-010"),
            {"vmid": 1, "activity_id": "act-recon",
             "inline_steps": [{"tool": "nope"}]},
            _ctx(),
        )
    assert e.value.error_class == "activity-render-failed"


def test_start_activity_launches_detached():
    fake = _FakeGA()
    out = _backend(fake).run(
        _op("start-activity", "OP-ACT-020"),
        {"vmid": 100, "activity_id": "act-recon"}, _ctx(),
    )
    assert out == {"activity_started": True}
    launch = next(a for a in fake.execs if a[:2] == ("sh", "-lc"))
    assert "setsid bash" in launch[2] and "&" in launch[2]


def test_start_activity_launch_failure_raises():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA(launch_ok=False)).run(
            _op("start-activity", "OP-ACT-020"),
            {"vmid": 1, "activity_id": "act-recon"}, _ctx(),
        )
    assert e.value.error_class == "activity-launch-failed"


def test_wait_actor_ready_returns_when_marker_present():
    out = _backend(_FakeGA(marker_present=True)).run(
        _op("wait-actor-ready", "OP-ACT-030", kind=OpKind.WAIT),
        {"vmid": 100, "activity_id": "act-recon"}, _ctx(),
    )
    assert out == {"actor_ready": True}


def test_wait_actor_ready_times_out_without_marker():
    op = _op("wait-actor-ready", "OP-ACT-030", kind=OpKind.WAIT, timeout_s=5)
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA(marker_present=False), actor_poll_interval_s=1.0).run(
            op, {"vmid": 1, "activity_id": "act-recon"}, _ctx(),
        )
    assert e.value.error_class == "activity-timeout"


# ---------------------------------------------------------------------------
# backend (fake QGA) -- schedule executor (jittered mode)
# ---------------------------------------------------------------------------

_JITTERED_INPUTS = {
    "vmid": 100, "activity_id": "act-recon",
    "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"}],
    "schedule": {"interval": {"min": "30s", "max": "5m"}},
    "seed": 1337,
}


def test_deploy_behavior_agent_writes_scheduler_when_jittered():
    fake = _FakeGA()
    out = _backend(fake).run(
        _op("deploy-behavior-agent", "OP-ACT-010"), _JITTERED_INPUTS, _ctx(),
    )
    assert out == {"agent_deployed": True}
    scheduler = activity_paths("act-recon")["scheduler"]
    runner = activity_paths("act-recon")["runner"]
    launch = activity_paths("act-recon")["launch"]
    assert scheduler in fake.written
    assert runner in fake.written  # the runner is STILL written (the scheduler invokes it)
    assert "nmap -sn 10.0.0.0/24" in fake.written[runner]
    # the kill-then-start LAUNCHER is also written as its own file (code-review
    # fix -- see test_start_activity_jittered_launch_argv_not_self_matched_by_pkill).
    assert launch in fake.written
    assert f'pkill -f "{scheduler}"' in fake.written[launch]
    assert f'setsid python3 "{scheduler}"' in fake.written[launch]


def test_deploy_behavior_agent_no_scheduler_when_not_jittered():
    """No schedule (or a schedule with no interval) -> unchanged one-shot
    behavior: no scheduler or launch file written."""
    fake = _FakeGA()
    _backend(fake).run(
        _op("deploy-behavior-agent", "OP-ACT-010"),
        {"vmid": 100, "activity_id": "act-recon",
         "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"}]},
        _ctx(),
    )
    paths = activity_paths("act-recon")
    assert paths["scheduler"] not in fake.written
    assert paths["launch"] not in fake.written


def test_deploy_behavior_agent_windowed_once_no_scheduler():
    """schedule present but no interval (windowed-`once`) -> still the
    unchanged one-shot path, no scheduler/launch written."""
    fake = _FakeGA()
    _backend(fake).run(
        _op("deploy-behavior-agent", "OP-ACT-010"),
        {"vmid": 100, "activity_id": "act-recon",
         "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24"}],
         "schedule": {"once": True}},
        _ctx(),
    )
    paths = activity_paths("act-recon")
    assert paths["scheduler"] not in fake.written
    assert paths["launch"] not in fake.written


def test_deploy_behavior_agent_bad_schedule_raises():
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA()).run(
            _op("deploy-behavior-agent", "OP-ACT-010"),
            {"vmid": 1, "activity_id": "act-recon",
             "inline_steps": [{"tool": "nmap"}],
             "schedule": {"interval": {"min": "5m", "max": "30s"}}},
            _ctx(),
        )
    assert e.value.error_class == "activity-render-failed"


def test_start_activity_jittered_launches_via_written_script_not_inline():
    """MUST-FIX regression: `start-activity` execs the written LAUNCH script
    as `["bash", <launch path>]` -- the kill-then-start command must NOT be
    inlined into a `sh -lc "..."` string."""
    fake = _FakeGA()
    backend = _backend(fake)
    backend.run(_op("deploy-behavior-agent", "OP-ACT-010"), _JITTERED_INPUTS, _ctx())
    out = backend.run(_op("start-activity", "OP-ACT-020"), _JITTERED_INPUTS, _ctx())
    assert out == {"activity_started": True}
    launch_path = activity_paths("act-recon")["launch"]
    launch_argv = next(a for a in fake.execs if a and a[0] == "bash")
    assert launch_argv == ("bash", launch_path)
    # no `sh -lc` exec at all for the jittered path.
    assert not any(a[:2] == ("sh", "-lc") for a in fake.execs)


def test_start_activity_jittered_launch_argv_not_self_matched_by_pkill():
    """MUST-FIX regression (code review): the argv actually passed to
    `ga.exec()` for the jittered launch must NOT itself contain the
    scheduler's path substring -- if it did, a `pkill -f "<scheduler>"`
    running as part of that SAME invocation would SIGTERM the invoking shell
    before `setsid python3` ever ran (the original bug: the launcher
    self-killed itself, the scheduler never armed, and `wait-actor-ready`
    timed out on EVERY jittered deploy). The pkill/restart logic must live in
    a FILE the launch argv only references by an UNRELATED path (`-launch.sh`,
    never `-scheduler.py`)."""
    fake = _FakeGA()
    backend = _backend(fake)
    backend.run(_op("deploy-behavior-agent", "OP-ACT-010"), _JITTERED_INPUTS, _ctx())
    backend.run(_op("start-activity", "OP-ACT-020"), _JITTERED_INPUTS, _ctx())

    scheduler_path = activity_paths("act-recon")["scheduler"]
    launch_argv = next(a for a in fake.execs if a and a[0] == "bash")
    joined_argv = " ".join(launch_argv)
    assert scheduler_path not in joined_argv, (
        "the launch argv must not contain the scheduler path -- otherwise "
        "pkill -f <scheduler> run as part of THIS invocation would self-match "
        "(and SIGTERM) the invoking shell, and the scheduler would never arm"
    )
    # the kill+restart logic DOES exist -- just relocated out of the argv,
    # into the written launch script.
    launch_script = fake.written[activity_paths("act-recon")["launch"]]
    assert f'pkill -f "{scheduler_path}"' in launch_script
    assert "setsid python3" in launch_script


def test_start_activity_one_shot_unchanged_when_not_jittered():
    fake = _FakeGA()
    _backend(fake).run(
        _op("start-activity", "OP-ACT-020"),
        {"vmid": 100, "activity_id": "act-recon"}, _ctx(),
    )
    launch = next(a for a in fake.execs if a[:2] == ("sh", "-lc"))
    assert "setsid bash" in launch[2]
    assert "python3" not in launch[2]


def test_wait_actor_ready_jittered_polls_scheduler_process():
    out = _backend(_FakeGA(scheduler_running=True)).run(
        _op("wait-actor-ready", "OP-ACT-030", kind=OpKind.WAIT), _JITTERED_INPUTS, _ctx(),
    )
    assert out == {"actor_ready": True}


def test_wait_actor_ready_jittered_times_out_when_process_absent():
    op = _op("wait-actor-ready", "OP-ACT-030", kind=OpKind.WAIT, timeout_s=5)
    with pytest.raises(AdapterError) as e:
        _backend(_FakeGA(scheduler_running=False), actor_poll_interval_s=1.0).run(
            op, _JITTERED_INPUTS, _ctx(),
        )
    assert e.value.error_class == "activity-timeout"
    assert "scheduler process" in str(e.value)


def test_wait_actor_ready_jittered_ignores_marker_state():
    """A jittered activity's wait must key off the scheduler PROCESS, not the
    one-shot completion marker -- an absent marker (the loop hasn't fired yet)
    must not fail the wait."""
    out = _backend(_FakeGA(marker_present=False, scheduler_running=True)).run(
        _op("wait-actor-ready", "OP-ACT-030", kind=OpKind.WAIT), _JITTERED_INPUTS, _ctx(),
    )
    assert out == {"actor_ready": True}


def test_deploy_then_start_two_jittered_activities_one_host_no_collision():
    """Two jittered activities bound to the same host: independent scheduler
    AND launch files, independent launch argvs (docs/SLICE_ACTIVITY.md §11.1)."""
    fake = _FakeGA()
    backend = _backend(fake)
    for act_id in ("act-a", "act-b"):
        backend.run(
            _op("deploy-behavior-agent", "OP-ACT-010"),
            {**_JITTERED_INPUTS, "activity_id": act_id}, _ctx(),
        )
        backend.run(
            _op("start-activity", "OP-ACT-020"),
            {**_JITTERED_INPUTS, "activity_id": act_id}, _ctx(),
        )
    sched_a = activity_paths("act-a")["scheduler"]
    sched_b = activity_paths("act-b")["scheduler"]
    launch_a = activity_paths("act-a")["launch"]
    launch_b = activity_paths("act-b")["launch"]
    assert sched_a != sched_b
    assert launch_a != launch_b
    assert sched_a in fake.written and sched_b in fake.written
    assert launch_a in fake.written and launch_b in fake.written
    # each activity's launch SCRIPT content only references its OWN scheduler.
    assert sched_a in fake.written[launch_a] and sched_b not in fake.written[launch_a]
    assert sched_b in fake.written[launch_b] and sched_a not in fake.written[launch_b]
    # each activity's launch ARGV is just its own path -- no collision, no
    # scheduler-path substring in the invoking argv (the MUST-FIX property).
    bash_execs = [a for a in fake.execs if a and a[0] == "bash"]
    assert ("bash", launch_a) in bash_execs
    assert ("bash", launch_b) in bash_execs
    for argv in bash_execs:
        joined = " ".join(argv)
        assert sched_a not in joined and sched_b not in joined


# ---------------------------------------------------------------------------
# backend (fake QGA) -- A4 ground-truth marker shipper
# (docs/design/08-exercise-measurement.md)
# ---------------------------------------------------------------------------


def _backend_with_loki(fake, loki_url: str, **kw) -> ConfigBackend:
    return ConfigBackend(
        AdapterConfig(loki_url=loki_url), rest_client=object(),
        guest_agent_factory=lambda client, node: fake,
        sleep_fn=lambda s: None, **kw,
    )


def test_deploy_behavior_agent_writes_and_starts_truth_shipper_when_loki_configured():
    fake = _FakeGA()
    out = _backend_with_loki(fake, "http://10.0.0.5:3100").run(
        _op("deploy-behavior-agent", "OP-ACT-010"),
        {"vmid": 100, "activity_id": "act-recon",
         "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24",
                            "technique": "T1046"}]},
        _ctx(),
    )
    assert out == {"agent_deployed": True}
    bootstrap = activity_paths("act-recon")["truth_ship_bootstrap"]
    assert bootstrap in fake.written
    assert "http://10.0.0.5:3100/loki/api/v1/push" in fake.written[bootstrap]
    # the bootstrap script is WRITTEN and EXECUTED (started), not just written.
    assert (bootstrap,) in fake.execs
    # SHOULD-FIX #1 (code review): the truth log is `touch`ed BEFORE the
    # shipper starts -- a fresh empty file guarantees the shipper's
    # seek-to-end lands at offset 0, so it can't skip a sub-second first
    # marker written before the shipper's existence-poll notices the file.
    truth = activity_paths("act-recon")["truth"]
    touch_idx = fake.execs.index(("touch", truth))
    start_idx = fake.execs.index((bootstrap,))
    assert touch_idx < start_idx


def test_deploy_behavior_agent_no_shipper_without_loki_url():
    """No LOKI_URL configured -> no shipper written/started, and the deploy
    still succeeds (best-effort telemetry, never a hard dependency)."""
    fake = _FakeGA()
    out = _backend(fake).run(
        _op("deploy-behavior-agent", "OP-ACT-010"),
        {"vmid": 100, "activity_id": "act-recon",
         "inline_steps": [{"tool": "nmap", "args": "-sn", "target": "10.0.0.0/24",
                            "technique": "T1046"}]},
        _ctx(),
    )
    assert out == {"agent_deployed": True}
    bootstrap = activity_paths("act-recon")["truth_ship_bootstrap"]
    assert bootstrap not in fake.written


def test_deploy_behavior_agent_ships_truth_even_when_jittered():
    """The shipper starts regardless of one-shot vs jittered -- ground truth
    should ship whenever the telemetry plane is wired, unlike the sensors'
    per-sensor opt-in `export.ship:`."""
    fake = _FakeGA()
    _backend_with_loki(fake, "http://10.0.0.5:3100").run(
        _op("deploy-behavior-agent", "OP-ACT-010"), _JITTERED_INPUTS, _ctx(),
    )
    bootstrap = activity_paths("act-recon")["truth_ship_bootstrap"]
    assert bootstrap in fake.written


# ---------------------------------------------------------------------------
# planner emission -- Exercise-tier scope-shift (2026-07-24,
# docs/WORK_ORDER_exercise_tier.md, docs/design/02-twin-model.md): a
# te deploy no longer emits the OP-ACT trio (or overlays.adversary's
# install-agent) at all -- it stands up ONLY the environment. `layers.
# activity:`/`overlays.adversary:` content still parses/validates (WARNING
# REF-260, not an error) but plans to ZERO activity/adversary ops; the trio's
# OWN schedule/seed-threading + fan-out behavior is now exercised against
# `axrange.o2control.exercise.compose_exercise_ops` (test_exercise.py), which
# composes the SAME ops by hand against an already-deployed twin -- mirrors
# the sensor-agnostic-te precedent (`test_dynamic_tap.py`'s "te emits zero
# mirror ops" shape).
# ---------------------------------------------------------------------------


def _activity_plan(manifests, *, capture=True):
    parsed = parse_ir(path=str(ACT_SCN))
    if not capture:
        for n in parsed.ir.layers.infra.nodes:
            n.tap = None
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    return plan


def test_te_plan_emits_zero_activity_ops(manifests):
    """The te-side scope-shift: a scenario carrying `activity:` still plans
    cleanly, but the OP-ACT trio never appears in a te deploy's own plan any
    more -- attack playout moved to the standalone Exercise (`exr`) tier."""
    plan = _activity_plan(manifests)
    names = {o.op_name for o in plan.ops.values()}
    assert not names & {"deploy-behavior-agent", "start-activity", "wait-actor-ready"}
    assert len(plan.ops) > 0  # the fixture's infra/app ops still emit


def test_te_plan_emits_zero_adversary_agents(manifests):
    """`overlays.adversary:` similarly plans to zero install-agent(kind=
    adversary) ops in a te deploy -- re-homed to the exr tier."""
    from axrange.ir.schema import AdversaryEntry

    parsed = parse_ir(path=str(ACT_SCN))
    if parsed.ir.overlays is None:
        from axrange.ir.schema import Overlays

        parsed.ir.overlays = Overlays()
    parsed.ir.overlays.adversary = [
        AdversaryEntry(id="red-sandcat", targets=["attacker"], server="http://caldera:8888")
    ]
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    adversary_ops = [
        o for o in plan.ops.values()
        if o.op_name == "install-agent" and o.inputs_resolved.get("agent_kind") == "adversary"
    ]
    assert adversary_ops == []


def test_activity_fixture_validates_and_plans(manifests):
    """The fixture passes the real validator + planner end-to-end (a WARNING
    -- REF-260 -- not an error, since the te deploy no longer runs its
    `activity:` block itself)."""
    from axrange.validator import validate_full

    res = validate_full(str(ACT_SCN), manifests=manifests)
    assert res.exit_code == 0, res.disposition
    codes = {f.rule_id for f in res.findings}
    assert "REF-260" in codes
    plan = _activity_plan(manifests)
    assert len(plan.ops) > 0


_TE_ROOT = Path(__file__).resolve().parents[1] / "target-environments"


@pytest.mark.parametrize(
    "scenario_path",
    [
        _TE_ROOT / "ad-domain-lab" / "scenario.yaml",
        _TE_ROOT / "purple-team-range" / "scenario.yaml",
        _TE_ROOT / "soc-detection-lab" / "scenario.yaml",
    ],
    ids=["ad-domain-lab", "purple-team-range", "soc-detection-lab"],
)
def test_shipped_legacy_archetypes_still_validate_with_ref_260_warning(
    manifests, scenario_path,
):
    """The Exercise-tier scope-shift's whole point (docs/
    WORK_ORDER_exercise_tier.md §2, "Twin decoupling") is that these three
    REAL, shipped archetypes -- which still author `layers.activity:`/
    `overlays.adversary:` in their own IR -- must keep validating cleanly
    (REF-260 fires as a WARNING, never blocking `exit_code`), exactly like
    REF-250 did for `sensors:` in the sensor-agnostic-te precedent. Runs
    against the actual `target-environments/` files, not just the synthetic
    ACT_SCN fixture above."""
    from axrange.validator import validate_full

    res = validate_full(str(scenario_path), manifests=manifests)
    assert res.exit_code == 0, res.disposition
    codes = {f.rule_id for f in res.findings}
    assert "REF-260" in codes


def test_render_activity_scheduler_window_logic_matches_days_and_clock():
    """The rendered scheduler's `in_window()` actually honors the resolved
    days/start/end bounds -- executed directly (not just string-matched) by
    running everything up to (not including) the scheduler's infinite loop."""
    from datetime import datetime

    schedule = {
        "interval": {"min": "30s", "max": "5m"},
        "days": ["Mon", "Wed"],
        "start": "09:00",
        "end": "17:00",
    }
    out = render_activity_scheduler(_STEPS, activity_id="w", schedule=schedule, seed=1)
    # Only run the header up through the function defs -- NOT the os.makedirs/
    # file-write side effects that follow (those touch the real guest
    # filesystem at runtime; irrelevant to in_window()'s pure logic).
    header, _, _ = out.partition("os.makedirs(")
    ns: dict = {}
    exec(compile(header, "<scheduler-header>", "exec"), ns)
    in_window = ns["in_window"]

    assert in_window(datetime(2026, 7, 13, 10, 0)) is True   # Mon 10:00 -- in window
    assert in_window(datetime(2026, 7, 13, 8, 0)) is False   # Mon 08:00 -- before window
    assert in_window(datetime(2026, 7, 13, 17, 0)) is False  # Mon 17:00 -- end is exclusive
    assert in_window(datetime(2026, 7, 14, 10, 0)) is False  # Tue -- wrong day
    assert in_window(datetime(2026, 7, 15, 10, 0)) is True   # Wed 10:00 -- in window


def test_render_activity_scheduler_overnight_window_wraps_midnight():
    """SHOULD-FIX #2 (code review): a window like start=22:00/end=06:00 wraps
    past midnight and must still fire overnight, not silently never-fire."""
    from datetime import datetime

    schedule = {"interval": {"min": "30s", "max": "5m"}, "start": "22:00", "end": "06:00"}
    out = render_activity_scheduler(_STEPS, activity_id="w", schedule=schedule, seed=1)
    header, _, _ = out.partition("os.makedirs(")
    ns: dict = {}
    exec(compile(header, "<scheduler-header>", "exec"), ns)
    in_window = ns["in_window"]

    assert in_window(datetime(2026, 7, 13, 23, 0)) is True   # 23:00 -- inside (late)
    assert in_window(datetime(2026, 7, 14, 3, 0)) is True    # 03:00 -- inside (early)
    assert in_window(datetime(2026, 7, 14, 12, 0)) is False  # noon -- outside
    assert in_window(datetime(2026, 7, 13, 21, 59)) is False  # just before start
    assert in_window(datetime(2026, 7, 14, 6, 0)) is False    # end is exclusive


def test_render_activity_scheduler_rejects_zero_interval():
    """SHOULD-FIX #3 (code review): a near-zero interval must be rejected at
    render time (it would busy-loop the guest), not just at max<min."""
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a",
            schedule={"interval": {"min": "0s", "max": "0s"}},
        )
    with pytest.raises(ActivityRenderError):
        render_activity_scheduler(
            _STEPS, activity_id="a",
            schedule={"interval": {"min": "0.5s", "max": "5m"}},
        )


def test_render_activity_scheduler_accepts_one_second_floor():
    """The floor is inclusive: exactly 1s is allowed."""
    out = render_activity_scheduler(
        _STEPS, activity_id="a", schedule={"interval": {"min": "1s", "max": "5m"}},
    )
    assert "MIN_S = 1.0" in out


def test_jitter_sequence_from_activity_matches_low_level_call():
    """SHOULD-FIX #4 (code review): the raw-input wrapper must fold the seed
    and parse the durations EXACTLY like `render_activity_scheduler` does --
    protects A4's replay-delta callers from silently diverging by skipping
    either step."""
    eff = activity_effective_seed(1337, "web-browsing")
    expected = jitter_sequence(30.0, 300.0, eff, count=5)
    actual = jitter_sequence_from_activity(1337, "web-browsing", "30s", "5m", count=5)
    assert actual == expected


def test_render_activity_scheduler_rng_matches_jitter_sequence_from_activity():
    """SHOULD-FIX #4 (code review): drives the ACTUAL rendered scheduler's RNG
    object (not a re-implementation) and asserts its draw sequence matches
    `jitter_sequence_from_activity()` -- the seam A4's replay-delta depends on
    staying honest. Extracted the same way the window-logic test extracts
    `in_window`: run the header (constants + `rng = random.Random(SEED)`)
    without the file-write side effects, then draw from the SAME `rng` object
    the guest loop would draw from."""
    schedule = {"interval": {"min": "30s", "max": "5m"}}
    seed = 1337
    activity_id = "web-browsing"
    out = render_activity_scheduler(_STEPS, activity_id=activity_id, schedule=schedule, seed=seed)
    header, _, _ = out.partition("os.makedirs(")
    ns: dict = {}
    exec(compile(header, "<scheduler-header>", "exec"), ns)
    guest_draws = [ns["rng"].uniform(ns["MIN_S"], ns["MAX_S"]) for _ in range(6)]

    expected = jitter_sequence_from_activity(seed, activity_id, "30s", "5m", count=6)
    assert guest_draws == expected


# ---------------------------------------------------------------------------
# render -- launch wrapper script (code-review MUST-FIX)
# ---------------------------------------------------------------------------


def test_render_activity_launch_script_kills_then_starts_own_scheduler():
    out = render_activity_launch_script("act-recon")
    scheduler = activity_paths("act-recon")["scheduler"]
    assert out.startswith("#!/bin/bash")
    assert f'pkill -f "{scheduler}"' in out
    assert f'setsid python3 "{scheduler}"' in out
    if shutil.which("bash") is not None:
        r = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
        assert r.returncode == 0, r.stderr


def test_render_activity_launch_script_two_activities_no_collision():
    out_a = render_activity_launch_script("act-a")
    out_b = render_activity_launch_script("act-b")
    sched_a = activity_paths("act-a")["scheduler"]
    sched_b = activity_paths("act-b")["scheduler"]
    assert sched_a in out_a and sched_b not in out_a
    assert sched_b in out_b and sched_a not in out_b
