"""OP-MIR-050 deploy-sensor — the in-scenario sensor VM provisioning (sensors
design Phase B, docs/design/06-taps-sensors.md).

Covers the per-type render (flow-based gretap decap iface + the tool the `type`
selects) and the ConfigBackend handler delivering it over a fake QGA channel.
Cluster-free (Phase B emission + live-verify are separate).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
import time

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    ConfigRenderError,
    _render_capture_pcap_watchdog,
    render_sensor_setup,
    sensor_script_path,
)
from axrange.ir.parser import parse_ir
from axrange.ops.registry import OpKind
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext
from axrange.validator import validate_full


# --- render --------------------------------------------------------------
def test_render_tcpdump_sensor():
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    # flow-based gretap decap iface is brought up (the receiver of the keyed gretaps)
    assert "ip link add gre-cap type gretap external" in s
    assert "ip link set gre-cap up" in s
    # the tool reads that iface
    assert "tcpdump -i gre-cap" in s
    assert s.startswith("#!/bin/bash")
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode == 0


def test_render_tcpdump_sensor_supervised_by_systemd_not_nohup():
    """Root cause fix (confirmed live 2026-07-17, GAP-020's failure class): a
    background `nohup tcpdump ... &` is REAPED the moment the qemu-guest-agent
    exec session that launched it ends. tcpdump is now launched as a DETACHED,
    deterministically-named transient systemd unit
    (`systemd-run --unit=axr-sensor-<safe> --collect`), mirroring the Loki
    shipper's own GAP-A fix -- no `pkill`+`nohup` re-up race on a redeploy
    (`--collect` + a leading `stop`/`reset-failed` make the restart idempotent
    without ever killing-then-losing the replacement to reaping)."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    assert "nohup" not in s
    assert "pkill" not in s
    assert "systemctl reset-failed axr-sensor-pcap" in s
    assert "systemctl stop axr-sensor-pcap" in s
    assert "systemd-run --unit=axr-sensor-pcap --collect bash -c" in s
    # fail-loud: a unit that didn't come up must fail the deploy script.
    assert "systemctl is-active --quiet axr-sensor-pcap ||" in s
    assert "exit 1" in s


def test_render_tcpdump_sensor_pcap_retention_accumulates_not_cyclic():
    """Retention fix (user decision 2026-07-17, disk-% watchdog, 85/70
    defaults): pcaps are timestamped + ACCUMULATING under a stable per-sensor
    dir (`-G`+`-C`, tcpdump's strftime-in-`-w` rotation, ported from
    o2/tcpdump-sensor/bin/capture-start) -- NOT the old cyclic `-W 50` ring
    that overwrote file `00` on every restart, silently discarding capture
    history across a redeploy."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    assert "-w /var/lib/axrange/capture/pcap/cap-%Y%m%d-%H%M%S.pcap" in s
    assert " -G " in s and " -C " in s
    # the cyclic file-count cap is gone -- accumulation + the disk-% watchdog
    # below replace it, not an overwrite ring.
    assert " -W " not in s


def test_render_tcpdump_sensor_installs_disk_watchdog_default_thresholds():
    """The in-guest disk-% prune watchdog (ported from
    o2/tcpdump-sensor/bin/capture-watchdog + capture-watchdog.timer) is
    installed as its own systemd timer, defaulting to 85%/70% high/low water
    marks when the caller doesn't override them."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    assert "HIGH=85" in s
    assert "LOW=70" in s
    assert "cat > /usr/local/bin/axr-cap-watchdog-pcap.sh" in s
    assert "cat > /etc/systemd/system/axr-cap-watchdog-pcap.service" in s
    assert "cat > /etc/systemd/system/axr-cap-watchdog-pcap.timer" in s
    assert "OnUnitActiveSec=60s" in s
    assert "systemctl enable --now axr-cap-watchdog-pcap.timer" in s
    # oldest-first prune. Glob is `cap-*` (NOT `cap-*.pcap` -- review
    # MUST-FIX 2026-07-17): tcpdump's `-C` size-rotation counter lands AFTER
    # the `.pcap` extension (`cap-<ts>.pcap1`, `.pcap2`, ...), so a
    # `.pcap`-suffixed glob would miss most rotated files under sustained
    # traffic. Safe to broaden -- capdir holds only this sensor's pcaps.
    assert 'find "$DIR" -maxdepth 1 -type f -name "cap-*"' in s
    assert 'find "$DIR" -maxdepth 1 -type f -name "cap-*.pcap"' not in s
    assert "sort -n" in s


def test_render_tcpdump_sensor_watchdog_thresholds_overridable():
    """Custom high/low thresholds (the adapter.conf CAPTURE_HIGH_WATER_PCT /
    CAPTURE_LOW_WATER_PCT override path) thread through to the rendered
    watchdog script."""
    s = render_sensor_setup(
        "pcap", "tcpdump-sensor", iface="gre-cap",
        capture_high_water_pct=90, capture_low_water_pct=60,
    )
    assert "HIGH=90" in s
    assert "LOW=60" in s


def test_render_tcpdump_sensor_watchdog_never_deletes_active_writer():
    """The watchdog script must protect the newest N pcaps (the file tcpdump
    currently has open, always the newest by mtime) -- it never blind-deletes
    the whole capture dir, and it is gated on EITHER trigger (disk pressure
    >= HIGH%, OR file count > MAX_FILES -- review SHOULD-FIX 2026-07-17),
    not an unconditional prune on every tick."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    assert 'head -n -"$MIN_KEEP"' in s
    assert 'MIN_KEEP=3' in s
    assert '[ "$usage" -ge "$HIGH" ] || [ "$total" -gt "$MAX_FILES" ] || exit 0' in s
    assert "rm -rf" not in s


def test_tcpdump_restart_does_not_delete_capture_dir():
    """A redeploy/restart of the tcpdump-sensor script ((re)starts the
    systemd unit -- `systemctl stop`/`reset-failed`, never a `pkill`) must
    never wipe the accumulating capture directory: pcaps must survive a
    re-render/re-run. `install -d` is idempotent (create-if-missing, never
    truncates); this is a regression guard that would FAIL if a future edit
    reintroduced any directory-level delete of the capture dir (e.g. an
    `rm -rf $CAPDIR` swept in before restarting the daemon)."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    capdir = "/var/lib/axrange/capture/pcap"
    assert f"install -d -m 0755 {capdir}" in s  # create-if-missing, never wipes
    assert "rm -rf" not in s
    for line in s.splitlines():
        assert not re.match(rf"^\s*rm\b.*{re.escape(capdir)}\b", line), (
            f"capture dir delete reintroduced: {line!r}"
        )
    # sanity: the restart path really is stop/reset-failed, not pkill.
    assert "systemctl stop axr-sensor-pcap" in s
    assert "systemctl reset-failed axr-sensor-pcap" in s
    assert "pkill" not in s


def _extract_watchdog_script(rendered: str) -> str:
    """Pull the actual watchdog shell BODY out of `_render_capture_pcap_
    watchdog`'s heredoc-wrapped output, so a test can execute the real prune
    logic (not just assert the emitted text) against a simulated capture
    dir."""
    m = re.search(r"<<'AXR_WATCHDOG_SH'\n(.*?)\nAXR_WATCHDOG_SH\n", rendered, re.DOTALL)
    assert m, "could not locate the AXR_WATCHDOG_SH heredoc in the rendered output"
    return m.group(1)


def _fake_df_reporting_usage_by_file_count(fakebin, *, base: int, per_file: int):
    """A fake `df` on PATH whose reported used% is a function of how many
    `cap-*` files remain in the queried dir -- lets a test drive the
    watchdog's disk-pressure loop toward a target usage% by actually
    deleting files, the same feedback loop `df` gives the real script on a
    real filesystem, without needing to fill an actual disk. Glob is `cap-*`
    (not `cap-*.pcap`) so size-rotated `.pcapN` fixture files count too --
    matches the watchdog script's own (fixed) glob, review MUST-FIX
    2026-07-17."""
    df_script = fakebin / "df"
    df_script.write_text(
        "#!/bin/bash\n"
        'dir="${@: -1}"\n'
        'n=$(ls "$dir"/cap-* 2>/dev/null | wc -l)\n'
        f'pct=$(( {base} + n * {per_file} ))\n'
        'echo "Use%"\n'
        'echo " ${pct}%"\n'
    )
    df_script.chmod(0o755)


def _write_aged_pcaps(capdir, names: list[str]) -> None:
    """Write `names` as real files under `capdir`, oldest-first (names[0] is
    the oldest by mtime -- what the watchdog's `sort -n` on `%T@` orders on)."""
    now = time.time()
    for i, name in enumerate(names):
        p = capdir / name
        p.write_bytes(b"x")
        ts = now - (len(names) - i) * 100
        os.utime(p, (ts, ts))


def test_watchdog_script_prunes_oldest_first_and_protects_min_keep(tmp_path):
    """Executes the RENDERED watchdog shell logic itself against a simulated
    capture dir of real files (not just asserting the script text was
    emitted -- the coverage gap the code-writer's tests left: the shell
    logic wasn't exercised, only its presence in the rendered string).
    6 files, disk pressure that never drops enough to satisfy LOW on its
    own -> the watchdog must still stop at MIN_KEEP=3 (protecting the
    actively-written newest file) and delete the oldest ones FIRST."""
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    rendered = _render_capture_pcap_watchdog(
        "pcap", "pcap", "__CAPDIR__", high_pct=85, low_pct=70,
    )
    script_body = _extract_watchdog_script(rendered)

    capdir = tmp_path / "capdir"
    capdir.mkdir()
    names = [f"cap-2026071{i}-000000.pcap" for i in range(6)]
    _write_aged_pcaps(capdir, names)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # n=6 -> 96% (>=HIGH, triggers); each deletion drops 6 points, but the
    # 3-file MIN_KEEP floor is reached (n=3 -> 78%, still > LOW=70) before
    # LOW is ever satisfied -- exercises the "stop at MIN_KEEP" branch.
    _fake_df_reporting_usage_by_file_count(fakebin, base=60, per_file=6)

    script_path = tmp_path / "watchdog.sh"
    script_path.write_text(script_body.replace("__CAPDIR__", str(capdir)))
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    res = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    remaining = sorted(p.name for p in capdir.iterdir())
    assert len(remaining) == 3, f"MIN_KEEP=3 floor violated: {remaining}"
    # the newest 3 (the actively-written file is always the newest by mtime)
    # must never be pruned.
    assert set(remaining) == set(names[-3:])
    deleted = [n for n in names if n not in remaining]
    # oldest-first: the deleted set must be a PREFIX of the age-sorted list,
    # not an arbitrary subset.
    assert deleted == names[: len(deleted)]


def test_watchdog_script_stops_once_below_low_water_mark(tmp_path):
    """A second scenario where disk pressure DOES relieve below LOW before
    every eligible (non-MIN_KEEP) victim is consumed -- the watchdog must
    stop early rather than pruning everything down to the MIN_KEEP floor."""
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    rendered = _render_capture_pcap_watchdog(
        "pcap", "pcap", "__CAPDIR__", high_pct=85, low_pct=70,
    )
    script_body = _extract_watchdog_script(rendered)

    capdir = tmp_path / "capdir"
    capdir.mkdir()
    names = [f"cap-2026071{i:02d}-000000.pcap" for i in range(10)]
    _write_aged_pcaps(capdir, names)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # n=10 -> 100% (trigger); each deletion drops 10 points: 90, 80, 70 --
    # LOW=70 satisfied after only 3 of the 7 eligible victims are pruned.
    _fake_df_reporting_usage_by_file_count(fakebin, base=0, per_file=10)

    script_path = tmp_path / "watchdog.sh"
    script_path.write_text(script_body.replace("__CAPDIR__", str(capdir)))
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    res = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    remaining = sorted(p.name for p in capdir.iterdir())
    # stopped early: 7 files remain (well above the MIN_KEEP=3 floor), only
    # the 3 oldest were pruned to satisfy LOW.
    assert len(remaining) == 7, f"expected an early stop at LOW%, got: {remaining}"
    deleted = [n for n in names if n not in remaining]
    assert deleted == names[:3]


def test_watchdog_script_noop_below_high_water_mark(tmp_path):
    """Below HIGH% AND below MAX_FILES, the watchdog must not touch the
    capture dir at all -- disk pressure / file count (not an unconditional
    tick-based prune) gate it. `max_files` defaults to 2000, well above the
    6 fixture files, so only the disk-% trigger is exercised here."""
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    rendered = _render_capture_pcap_watchdog(
        "pcap", "pcap", "__CAPDIR__", high_pct=85, low_pct=70,
    )
    script_body = _extract_watchdog_script(rendered)

    capdir = tmp_path / "capdir"
    capdir.mkdir()
    names = [f"cap-2026071{i}-000000.pcap" for i in range(6)]
    _write_aged_pcaps(capdir, names)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    df_script = fakebin / "df"
    df_script.write_text('#!/bin/bash\necho "Use%"\necho " 50%"\n')  # always < HIGH
    df_script.chmod(0o755)

    script_path = tmp_path / "watchdog.sh"
    script_path.write_text(script_body.replace("__CAPDIR__", str(capdir)))
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    res = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    remaining = sorted(p.name for p in capdir.iterdir())
    assert remaining == names  # untouched


def test_watchdog_glob_matches_size_rotated_pcapN_files(tmp_path):
    """MUST-FIX regression (reproduced on real tcpdump 4.99.4, review
    2026-07-17): with `-w cap-%Y%m%d-%H%M%S.pcap -G ... -C ...`, tcpdump
    appends the size-rotation counter AFTER the extension -- `cap-<ts>.pcap`,
    `cap-<ts>.pcap1`, `cap-<ts>.pcap2`, ... -- so under sustained traffic
    MOST rotated files are `.pcapN`, not plain `.pcap`. The old
    `cap-*.pcap` watchdog glob matched at most one file per `-G` window and
    left every `.pcapN` file invisible to the prune. This test creates a mix
    of plain-`.pcap` and size-rotated `.pcapN` files and asserts the
    watchdog actually prunes INTO the `.pcapN` set -- it would FAIL against
    the old `.pcap`-suffixed glob (the `.pcapN` files would never be
    touched, and disk pressure would never relieve)."""
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    rendered = _render_capture_pcap_watchdog(
        "pcap", "pcap", "__CAPDIR__", high_pct=85, low_pct=70,
    )
    script_body = _extract_watchdog_script(rendered)

    capdir = tmp_path / "capdir"
    capdir.mkdir()
    # size-rotated variants (.pcap1/.pcap2) interleaved with the plain .pcap
    # tcpdump writes at the start of each -G window -- exactly what a busy
    # mirror produces.
    names = [
        "cap-20260710-000000.pcap",
        "cap-20260710-000000.pcap1",
        "cap-20260710-000000.pcap2",
        "cap-20260710-010000.pcap",
        "cap-20260710-010000.pcap1",
        "cap-20260710-020000.pcap",
    ]
    _write_aged_pcaps(capdir, names)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_df_reporting_usage_by_file_count(fakebin, base=60, per_file=6)  # n=6 -> 96%

    script_path = tmp_path / "watchdog.sh"
    script_path.write_text(script_body.replace("__CAPDIR__", str(capdir)))
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    res = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    remaining = sorted(p.name for p in capdir.iterdir())
    # MIN_KEEP=3 floor: only the newest 3 by mtime survive, regardless of
    # which extension variant they are.
    assert set(remaining) == set(names[-3:])
    deleted = [n for n in names if n not in remaining]
    # at least one deleted file must be a size-rotated `.pcapN` variant --
    # proves the glob actually matched them (the regression this guards
    # against would leave every `.pcapN` file in place, untouched).
    assert any(re.search(r"\.pcap\d+$", n) for n in deleted), (
        f"no .pcapN file was pruned -- glob regression: deleted={deleted}"
    )


def test_watchdog_prunes_by_file_count_when_over_max_files(tmp_path):
    """SHOULD-FIX (review 2026-07-17): on an idle mirror, tiny near-empty
    rotated files barely move disk usage, so the disk-% trigger alone never
    fires -- but the file/inode count still grows unbounded. The watchdog
    must ALSO prune oldest-first once the file count exceeds `max_files`,
    independent of disk pressure, back down to AT MOST `max_files`."""
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    rendered = _render_capture_pcap_watchdog(
        "pcap", "pcap", "__CAPDIR__", high_pct=85, low_pct=70, max_files=5,
    )
    script_body = _extract_watchdog_script(rendered)

    capdir = tmp_path / "capdir"
    capdir.mkdir()
    names = [f"cap-2026071{i:02d}-000000.pcap" for i in range(8)]
    _write_aged_pcaps(capdir, names)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # disk usage NEVER crosses HIGH -- only the count trigger can fire.
    df_script = fakebin / "df"
    df_script.write_text('#!/bin/bash\necho "Use%"\necho " 10%"\n')
    df_script.chmod(0o755)

    script_path = tmp_path / "watchdog.sh"
    script_path.write_text(script_body.replace("__CAPDIR__", str(capdir)))
    script_path.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    res = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    remaining = sorted(p.name for p in capdir.iterdir())
    assert len(remaining) == 5, f"expected pruning down to MAX_FILES=5, got: {remaining}"
    # oldest-first: the survivors are the newest 5 by mtime.
    assert set(remaining) == set(names[-5:])


def test_watchdog_default_max_files_is_2000():
    """Default `max_files` (adapter.conf CAPTURE_MAX_FILES, default 2000 --
    review SHOULD-FIX 2026-07-17) shows up in the rendered script when the
    caller doesn't override it."""
    s = render_sensor_setup("pcap", "tcpdump-sensor", iface="gre-cap")
    assert "MAX_FILES=2000" in s


def test_watchdog_max_files_overridable():
    """Custom `capture_max_files` (the CAPTURE_MAX_FILES adapter.conf
    override path) threads through to the rendered watchdog script."""
    s = render_sensor_setup(
        "pcap", "tcpdump-sensor", iface="gre-cap", capture_max_files=100,
    )
    assert "MAX_FILES=100" in s


def test_render_suricata_sensor_uses_config_rules():
    s = render_sensor_setup(
        "ids", "suricata-sensor", iface="gre-cap",
        config_content='alert ip any any -> any any (msg:"x"; sid:1; rev:1;)',
    )
    assert "ip link add gre-cap type gretap external" in s
    assert "suricata -S /etc/suricata/rules/axrange.rules -i gre-cap" in s
    assert "sid:1" in s


def test_render_suricata_sensor_still_uses_pkill_pgrep_unchanged():
    """Regression guard: the systemd-run supervision fix targets ONLY the
    tcpdump/zeek daemon-start path (`systemd_daemon_start_block`) -- the
    suricata-sensor path keeps its pre-existing `_suricata_start_block`
    contract (pkill-then-start, `pgrep -f` liveness check), shared verbatim
    with the network sensor (`render_network_sensor_setup`, covered in
    tests/test_overlay_exec.py). This must NOT regress to systemd-run."""
    s = render_sensor_setup("ids", "suricata-sensor", iface="gre-cap")
    assert "pkill -f 'suricata.*axrange'" in s
    assert "pgrep -f 'suricata.*axrange'" in s
    assert "systemd-run" not in s


def test_render_zeek_sensor():
    s = render_sensor_setup("zk", "zeek-sensor", iface="gre-cap")
    assert "ip link add gre-cap type gretap external" in s
    assert "zeek -i gre-cap" in s


def test_render_zeek_sensor_kill_and_check_tokens_match():
    """Supersedes the original pkill/pgrep-token regression guard (code-review
    finding 2): zeek is now supervised as a named, detached systemd transient
    unit (`systemd-run --unit=axr-sensor-<safe> --collect`, GAP-020's failure
    class fix, 2026-07-17) rather than a `pkill`/`pgrep`-matched `nohup`
    background process, so there is no argv-token pkill/pgrep pair left to
    collide. `systemctl is-active` tracks zeek's REAL pid directly via the
    unit -- a stronger, not weaker, uniqueness guarantee than the old string
    match. Assert: no pkill/pgrep/nohup anywhere in the rendered script; the
    unit name is derived from (and thus distinct per) sensor id; and the
    `-U <status-file>` observability flag is still present (kept for its own
    sake, no longer load-bearing for supervision)."""
    s = render_sensor_setup("zk-one", "zeek-sensor", iface="gre-cap")
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode == 0
    assert "pkill" not in s
    assert "pgrep" not in s
    assert "nohup" not in s
    assert "systemd-run --unit=axr-sensor-zk-one --collect" in s
    assert "systemctl is-active --quiet axr-sensor-zk-one" in s
    assert "zeek -i gre-cap -U /run/zeek-zk-one.status" in s

    # a second, differently-id'd zeek sensor gets a DISTINCT unit name (no
    # collision across two sensors on one host).
    s2 = render_sensor_setup("zk-two", "zeek-sensor", iface="gre-cap")
    assert "axr-sensor-zk-two" in s2 and "axr-sensor-zk-one" not in s2


def test_render_unknown_type_raises():
    with pytest.raises(ConfigRenderError):
        render_sensor_setup("x", "wireshark-sensor", iface="gre-cap")


# --- C2: export.ship loki ------------------------------------------------
def test_c2_render_loki_shipper():
    from axrange.config_exec.render import (
        render_sensor_loki_shipper,
        sensor_loki_artifact,
    )
    assert sensor_loki_artifact("suricata-sensor") == "/var/log/suricata/eve.json"
    assert sensor_loki_artifact("tcpdump-sensor") is None  # pcaps don't ship to loki
    s = render_sensor_loki_shipper(
        "ids", "suricata-sensor",
        loki_url="http://10.0.0.5:3100", artifact="/var/log/suricata/eve.json",
    )
    assert "/loki/api/v1/push" in s
    assert "/var/log/suricata/eve.json" in s
    # label rendering is now shared with the A4 truth shipper via
    # `_render_loki_tail_shipper`, which embeds labels as JSON (double-quoted)
    # rather than python repr (single-quoted) -- either is valid embedded
    # python source; assert on the LABEL VALUE, not the quoting style.
    assert '"sensor": "ids"' in s


def test_c2_render_loki_shipper_no_pkill_uses_systemd_unit():
    """GAP-A fix (live-verified dev45 2026-07-15, docs/PRODUCT_GAPS.md): the
    bootstrap this backend delivers over QGA is `sensor_shipper_path(id) +
    ".sh"` (e.g. `.../ship-ids.py.sh`) while the OLD `pkill_pattern` was
    `ship-ids.py` -- a literal substring of that wrapper's own `.py.sh` path,
    so a `pkill -f 'ship-ids.py'` inside the wrapper matched and SIGTERMed the
    wrapper itself before it ever wrote/launched the shipper. The fixed
    bootstrap carries no pkill at all, and launches via a named, deterministic
    systemd transient unit so the shipper survives the QGA exec session
    (a bare `nohup ... &` gets reaped when that session ends)."""
    from axrange.config_exec.render import render_sensor_loki_shipper, sensor_shipper_path

    s = render_sensor_loki_shipper(
        "ids", "suricata-sensor",
        loki_url="http://10.0.0.5:3100", artifact="/var/log/suricata/eve.json",
    )
    assert "pkill" not in s
    assert "nohup" not in s
    # the wrapper's own delivery path (mirrors what ConfigBackend._deploy_sensor
    # writes it to) must never appear as the target of a self-matching kill --
    # trivially true now there is no pkill, but assert it explicitly as a
    # regression guard against reintroducing one.
    wrapper_path = sensor_shipper_path("ids") + ".sh"
    assert "pkill -f" not in s
    for line in s.splitlines():
        if "pkill" in line:
            assert False, f"unexpected pkill line reintroduced: {line!r}"
    # launched as a deterministic, named systemd transient unit that survives
    # the QGA exec session (--collect auto-reaps a stopped unit's state).
    assert "systemd-run --unit=axr-ship-ids --collect" in s
    assert "systemctl stop axr-ship-ids" in s
    assert "systemctl reset-failed axr-ship-ids" in s
    # a no-systemd fallback still detaches the process.
    assert "setsid python3" in s
    assert wrapper_path  # sanity: path helper still resolves


def test_render_loki_tail_shipper_never_self_matching_pkill():
    """Regression guard on the shared helper itself (`_render_loki_tail_shipper`):
    reproduce the EXACT GAP-A scenario -- a shipper written to `<x>.py` whose
    delivering bootstrap wrapper is written to `<x>.py.sh` (the real shape
    `ConfigBackend._deploy_sensor` uses: `sensor_shipper_path(id) + ".sh"`).
    A `.py` pattern is a literal substring of a `.py.sh` path, so ANY future
    reintroduction of a `pkill -f <pattern-derived-from-shipper_path>` in the
    bootstrap would self-kill the wrapper that delivers it. Assert the
    rendered bootstrap contains no `pkill` at all, regardless of how
    `shipper_path`/`unit` are named."""
    from axrange.config_exec.render import _render_loki_tail_shipper

    shipper_path = "/etc/axrange/sensor/ship-selfkill-check.py"
    wrapper_path = shipper_path + ".sh"  # what the bootstrap is actually delivered as
    out = _render_loki_tail_shipper(
        header_comment="regression", bootstrap_comment="regression",
        push_url="http://10.0.0.5:3100/loki/api/v1/push",
        artifact_path="/var/log/x.json", labels={"job": "x"},
        shipper_path=shipper_path,
        runner_log="/var/log/axrange/ship-selfkill-check.log",
        unit="axr-ship-selfkill-check",
        echo_label="regression",
    )
    assert "pkill" not in out
    # sanity: the scenario this guards against really would have collided --
    # the old pkill_pattern derived from shipper_path IS a substring of the
    # wrapper's own delivery path.
    assert shipper_path in wrapper_path


def test_c2_render_loki_shipper_unit_stable_and_unique_per_sensor():
    """The systemd unit name must be STABLE across re-renders of the same
    sensor (so `systemctl stop <unit>` on a redeploy targets the right prior
    instance) and DISTINCT across two different sensors (so two shippers on
    one host never collide)."""
    from axrange.config_exec.render import render_sensor_loki_shipper

    s1a = render_sensor_loki_shipper(
        "ids", "suricata-sensor", loki_url="http://10.0.0.5:3100",
        artifact="/var/log/suricata/eve.json",
    )
    s1b = render_sensor_loki_shipper(
        "ids", "suricata-sensor", loki_url="http://10.0.0.5:3100",
        artifact="/var/log/suricata/eve.json",
    )
    s2 = render_sensor_loki_shipper(
        "ids2", "suricata-sensor", loki_url="http://10.0.0.5:3100",
        artifact="/var/log/suricata/eve.json",
    )
    assert "axr-ship-ids " not in s2 and "axr-ship-ids2" in s2
    assert "systemd-run --unit=axr-ship-ids " in s1a  # stable across re-renders
    assert "systemd-run --unit=axr-ship-ids " in s1b
    assert "axr-ship-ids2" not in s1a


def test_c2_deploy_sensor_ships_to_loki():
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.config_exec.render import sensor_shipper_path

    class _GA:
        def __init__(self): self.written = {}; self.execs = []
        def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
            self.execs.append(tuple(argv)); return GuestExecResult(0, "", "")
        def write_file(self, vmid, path, content, *, mode=None):
            self.written[path] = content

    fake = _GA()
    be = ConfigBackend(AdapterConfig(loki_url="http://lk:3100"), rest_client=object(),
                       guest_agent_factory=lambda c, n: fake)
    op = OpInstance(op_id="p:deploy-sensor[ids]", op_name="deploy-sensor",
                    op_catalog_id="OP-MIR-050", kind=OpKind.ACTION, scope={"host": "ids"})
    be.run(op, {"vmid": 5, "sensor_id": "ids", "sensor_type": "suricata-sensor",
                "capture_iface": "gre-cap", "ship": "loki"}, _ctx())
    # the shipper bootstrap was delivered
    assert sensor_shipper_path("ids") + ".sh" in fake.written
    assert "/loki/api/v1/push" in fake.written[sensor_shipper_path("ids") + ".sh"]


def test_c2_no_loki_url_is_noop():
    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.config_exec.render import sensor_shipper_path

    class _GA:
        def __init__(self): self.written = {}
        def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
            return GuestExecResult(0, "", "")
        def write_file(self, vmid, path, content, *, mode=None):
            self.written[path] = content

    fake = _GA()
    be = ConfigBackend(AdapterConfig(loki_url=""), rest_client=object(),
                       guest_agent_factory=lambda c, n: fake)
    op = OpInstance(op_id="p:deploy-sensor[ids]", op_name="deploy-sensor",
                    op_catalog_id="OP-MIR-050", kind=OpKind.ACTION, scope={"host": "ids"})
    be.run(op, {"vmid": 5, "sensor_id": "ids", "sensor_type": "suricata-sensor",
                "capture_iface": "gre-cap", "ship": "loki"}, _ctx())
    # no LOKI_URL -> shipper is NOT delivered (silent no-op, not a failure)
    assert sensor_shipper_path("ids") + ".sh" not in fake.written


# --- backend handler -----------------------------------------------------
class _FakeGA:
    def __init__(self, *, run_exit=0):
        self.run_exit = run_exit
        self.execs: list[tuple] = []
        self.written: dict[str, str] = {}
        self.mkdirs: set[str] = set()

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:2] == ["mkdir", "-p"]:
            self.mkdirs.add(argv[2])
            return GuestExecResult(0, "", "")
        # running the delivered setup script
        return GuestExecResult(self.run_exit, "ok", "" if self.run_exit == 0 else "boom")

    def write_file(self, vmid, path, content, *, mode=None):
        parent = path.rsplit("/", 1)[0]
        if parent not in self.mkdirs:
            raise AdapterError(f"writing {path}: parent {parent} missing",
                               error_class="guest-write-failed")
        self.written[path] = content


def _ctx():
    return ExecutionContext(plan_id="p", deploy_id="d",
                            cluster=ClusterContext(default_node="px1"), placement={})


def _backend(fake):
    return ConfigBackend(AdapterConfig(), rest_client=object(),
                         guest_agent_factory=lambda client, node, **kw: fake)


def _op():
    return OpInstance(
        op_id="p:deploy-sensor[pcap]", op_name="deploy-sensor",
        op_catalog_id="OP-MIR-050", kind=OpKind.ACTION,
        scope={"host": "pcap"},
        inputs_resolved={
            "vmid": 100123, "sensor_id": "pcap",
            "sensor_type": "tcpdump-sensor", "capture_iface": "gre-cap",
        },
    )


def test_deploy_sensor_delivers_and_runs():
    fake = _FakeGA()
    out = _backend(fake).run(_op(), {
        "vmid": 100123, "sensor_id": "pcap",
        "sensor_type": "tcpdump-sensor", "capture_iface": "gre-cap",
    }, _ctx())
    assert out == {"sensor_ready": True}
    # the setup script was written to the deterministic path and executed
    path = sensor_script_path("pcap")
    assert path in fake.written
    assert "tcpdump -i gre-cap" in fake.written[path]
    assert (path,) in fake.execs


def test_deploy_sensor_raises_on_nonzero():
    fake = _FakeGA(run_exit=7)
    with pytest.raises(AdapterError):
        _backend(fake).run(_op(), {
            "vmid": 100123, "sensor_id": "pcap",
            "sensor_type": "tcpdump-sensor", "capture_iface": "gre-cap",
        }, _ctx())


def test_deploy_sensor_threads_retention_thresholds_from_config():
    """`ConfigBackend._deploy_sensor` reads `AdapterConfig.capture_high_water_pct`
    /`capture_low_water_pct`/`capture_max_files` (adapter.conf
    `CAPTURE_HIGH_WATER_PCT`/`CAPTURE_LOW_WATER_PCT`/`CAPTURE_MAX_FILES`) and
    threads them into the rendered watchdog -- custom values set on the
    config object must show up in the delivered script, not just the
    render-level default."""
    fake = _FakeGA()
    be = ConfigBackend(
        AdapterConfig(
            capture_high_water_pct=90, capture_low_water_pct=55,
            capture_max_files=500,
        ),
        rest_client=object(), guest_agent_factory=lambda client, node: fake,
    )
    be.run(_op(), {
        "vmid": 100123, "sensor_id": "pcap",
        "sensor_type": "tcpdump-sensor", "capture_iface": "gre-cap",
    }, _ctx())
    script = fake.written[sensor_script_path("pcap")]
    assert "HIGH=90" in script
    assert "LOW=55" in script
    assert "MAX_FILES=500" in script


def test_deploy_sensor_default_retention_thresholds_are_85_70_2000():
    """No override on `AdapterConfig` -> the watchdog gets the documented
    85/70/2000 defaults (user decision + review follow-up 2026-07-17)."""
    fake = _FakeGA()
    out = _backend(fake).run(_op(), {
        "vmid": 100123, "sensor_id": "pcap",
        "sensor_type": "tcpdump-sensor", "capture_iface": "gre-cap",
    }, _ctx())
    assert out == {"sensor_ready": True}
    script = fake.written[sensor_script_path("pcap")]
    assert "HIGH=85" in script
    assert "LOW=70" in script
    assert "MAX_FILES=2000" in script


# --- B2 emission: sensors expand to in-scenario VMs ----------------------
def _emit_ir(sensors_block: str) -> str:
    return textwrap.dedent(f"""
        scenario_schema_version: 1
        scenario: {{ id: sen-emit, version: 1 }}
        layers:
          infra:
            networks: [{{ id: corp, cidr: 10.0.0.0/24, vlan: 10 }}]
            nodes:
              - {{ id: h-a, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }}
          apps: []
          activity: []
        {sensors_block}
        bindings: []
        overlays: {{}}
    """).strip()


def test_sensor_vm_emitted_no_gretap_wiring_o2_tier(manifests):
    """Sensor-agnostic te, review fix F1 (2026-07-13,
    docs/WORK_ORDER_sensor_agnostic_te.md): the Sensors-design Phase B
    synthesis (sensor VM + transport subnet) + deploy-sensor emission are
    TIER-GATED to `tier == "o2"` — the standing-surface path
    `axrange.o2control.control.stand_up_surface` builds (A1's ONLY mechanism
    for materializing standing O2 sensors, `build_plan_and_ctx(tier="o2")`).
    This test asserts A1's own path is UNCHANGED: with `tier="o2"`, a
    declared sensor VM still deploys (Phase B host lifecycle + deploy-sensor)
    — the te wires NO gretap to it (that wiring is entirely A2's job, once
    this VM is later bound via a tap-plan)."""
    src = _emit_ir("sensors:\n          - { id: pcap, type: tcpdump-sensor, primary: true }")
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")
    ops = list(plan.ops.values())

    # the sensor VM's host lifecycle is emitted (synthetic node "pcap")
    clone = next(o for o in ops if o.op_name == "clone-vm" and o.scope.get("host") == "pcap")
    # deploy-sensor is emitted for the sensor, reading its vmid + its type
    ds = next(o for o in ops if o.op_name == "deploy-sensor" and o.scope.get("host") == "pcap")
    assert ds.inputs_resolved["sensor_type"] == "tcpdump-sensor"
    assert ds.inputs_from_outputs["vmid"][0] == clone.op_id

    # the transport vnet is auto-provisioned + given a subnet/gateway (host-reachable)
    vnets = {o.inputs_resolved.get("net_id") for o in ops if o.op_name == "create-vnet"}
    subnets = {o.inputs_resolved.get("net_id") for o in ops if o.op_name == "create-subnet"}
    assert "axr-sensor-transport" in vnets
    assert "axr-sensor-transport" in subnets

    # NO gretap wiring at all — no tier emits attach-gre-tunnel/install-ovs-mirror
    # from the te planner any more (that's entirely A2's job, sensor-agnostic te).
    # Full-topology tap eligibility (2026-07-28, docs/WORK_ORDER_tap_
    # eligibility.md D4) retired record-tap-intent too -- the te records NO
    # tap/mirror-related op of any kind, sensor-VM synthesis included.
    assert not any(o.op_name == "attach-gre-tunnel" for o in ops)
    assert not any(o.op_name == "install-ovs-mirror" for o in ops)
    assert not any(o.op_name == "record-tap-intent" for o in ops)

    # placement covers the synthetic sensor VM
    assert plan.placement.get("pcap") == "px1"


def test_te_tier_sensors_block_not_stood_up_and_warns(manifests):
    """Review fix F1 (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md): a
    `tier="te"` deploy (the default -- every ordinary `axrange deploy`) of a
    scenario declaring `sensors:` emits NONE of the Phase-B synthesis — no
    sensor-VM clone-vm/deploy-sensor, no auto-provisioned transport vnet/
    subnet — closing the silent-blind gap (LES-001/004: a sensor VM that
    would otherwise receive zero mirrored traffic with no heartbeat to catch
    it). The IR still validates (REF-250 fires a WARNING, not an error, so
    the shipped target-environments that author `sensors:` keep validating)."""
    src = _emit_ir("sensors:\n          - { id: pcap, type: tcpdump-sensor, primary: true }")
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)  # tier="te" default
    ops = list(plan.ops.values())

    assert not any(o.scope.get("host") == "pcap" for o in ops)
    assert not any(o.op_name == "deploy-sensor" for o in ops)
    assert not any(
        o.op_name == "create-vnet" and o.inputs_resolved.get("net_id") == "axr-sensor-transport"
        for o in ops
    )
    assert not any(
        o.op_name == "create-subnet"
        and o.inputs_resolved.get("net_id") == "axr-sensor-transport"
        for o in ops
    )
    assert "pcap" not in plan.placement

    # Non-silent: REF-250 WARNING surfaces the suppression to the author.
    result = validate_full(text=src, manifests=manifests)
    assert result.exit_code == 0  # WARNING only -- still validates
    ref250 = [f for f in result.findings if f.rule_id == "REF-250"]
    assert len(ref250) == 1
    assert ref250[0].severity.value == "warning"


def test_suricata_sensor_rules_file_threaded(manifests, tmp_path):
    """A suricata-sensor `rules:` file is read at emit time into deploy-sensor's
    config_content — the in-scenario AD-IDS blue-team ruleset (OP-OVL defense).
    `tier="o2"`: deploy-sensor is now tier-gated (review fix F1) to the standing-
    surface path this Phase-B mechanism belongs to."""
    rules = (
        'alert tcp any any -> any any (msg:"AXR-AD DCSYNC: DRSUAPI"; '
        "content:\"|35 42 51 e3|\"; sid:9000007; rev:1;)\n"
    )
    (tmp_path / "ad.rules").write_text(rules)
    src = _emit_ir(
        "sensors:\n          - { id: ad-ids, type: suricata-sensor, "
        "primary: true, rules: ./ad.rules }"
    )
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw, scenario_dir=str(tmp_path),
        tier="o2",
    )
    ds = next(
        o for o in plan.ops.values()
        if o.op_name == "deploy-sensor" and o.scope.get("host") == "ad-ids"
    )
    assert ds.inputs_resolved["sensor_type"] == "suricata-sensor"
    assert ds.inputs_resolved["config_content"] == rules


def test_b3_te_emits_no_verify_capture_even_with_in_scenario_sensor(manifests):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    even WITH a declared in-scenario sensor VM, the te emits NO verify-capture
    of its own — that heartbeat (reading the sensor VM's vmid over QGA) is now
    composed exclusively by the A2 tap playout once a tap-plan binds a tap to
    it (see test_o2_taps.py / config_exec `_verify_capture` unit tests for the
    QGA-read mechanics, which are unchanged)."""
    src = _emit_ir("sensors:\n          - { id: pcap, type: tcpdump-sensor, primary: true }")
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    vc = next((o for o in plan.ops.values() if o.op_name == "verify-capture"), None)
    assert vc is None


def test_b3_backend_confirms_over_qga():
    """The handler reads the sensor VM over QGA (exit 0 ⇒ verified) when a
    sensor_vmid is present — no SENSOR_SSH_*."""
    from axrange.ops.registry import OpKind as _K

    class _GA:
        def __init__(self): self.read_vmids = []
        def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
            # the sensor read is `sh -c <confirm>` on the sensor vmid
            if argv[:2] == ["sh", "-lc"] or argv[:2] == ["sh", "-c"]:
                self.read_vmids.append(vmid)
            return GuestExecResult(0, "found", "")
        def write_file(self, vmid, path, content, *, mode=None):
            pass

    fake = _GA()
    op = OpInstance(
        op_id="p:verify-capture[corp@pcap]", op_name="verify-capture",
        op_catalog_id="OP-MIR-040", kind=_K.ACTION,
        scope={"net": "corp", "sensor": "pcap", "host": "h-a"},
        inputs_resolved={}, timeout_s=12,
    )
    inp = {
        "vmid": 100001, "net_id": "corp", "sensor_id": "pcap",
        "bridge_name": "vn0", "sensor_vmid": 100002, "sensor_iface": "gre-cap",
    }
    out = _backend(fake).run(op, inp, _ctx())
    assert out["capture_verified"] is True
    # the confirm ran on the SENSOR vmid (100002), not the injector (100001)
    assert 100002 in fake.read_vmids


def test_c1_declared_sensors_still_deploy_but_no_fanout_wiring(manifests):
    """`tier="o2"` (A1's standing-surface path, review fix F1): two sensors
    with overlapping `captures: [corp]` still both deploy as VMs (Phase B
    host lifecycle is unaffected there), but the te wires NO gretap fan-out
    of its own any more — `captures:`-based fan-out resolution moves entirely
    to the A2 tap-plan's own `sensors:` binding list (docs/design/06-taps-sensors.md)."""
    src = _emit_ir(
        "sensors:\n"
        "          - { id: pcap, type: tcpdump-sensor, primary: true, captures: [corp] }\n"
        "          - { id: ids, type: suricata-sensor, captures: [corp] }"
    )
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")
    ops = list(plan.ops.values())
    assert not any(o.op_name == "attach-gre-tunnel" for o in ops)
    assert not any(o.op_name == "install-ovs-mirror" for o in ops)
    # both sensors still deployed as VMs.
    assert {o.scope["host"] for o in ops if o.op_name == "deploy-sensor"} == {"pcap", "ids"}
    # No record-tap-intent either (retired -- `docs/WORK_ORDER_tap_
    # eligibility.md` D4), independent of sensor count.
    assert not any(o.op_name == "record-tap-intent" for o in ops)


def test_c1_disjoint_captures_both_sensors_deploy_no_wiring(manifests):
    """`tier="o2"` (A1's standing-surface path, review fix F1): a sensor
    capturing only `dmz` still deploys, but neither sensor gets any gretap
    wiring from the te planner — that binding decision is entirely the A2
    tap-plan's (per-tap `sensors: [...]` list), not derived from `captures:`
    at te-emission time any more."""
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: sen-split, version: 1 }
        layers:
          infra:
            networks:
              - { id: corp, cidr: 10.0.0.0/24, vlan: 10 }
              - { id: dmz,  cidr: 10.1.0.0/24, vlan: 20 }
            nodes:
              - { id: h-a, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
              - { id: h-d, kind: host, net: dmz,  image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        sensors:
          - { id: pcap, type: tcpdump-sensor, primary: true, captures: [corp] }
          - { id: ids,  type: suricata-sensor, captures: [dmz] }
        bindings: []
        overlays: {}
    """).strip()
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")
    ops = list(plan.ops.values())
    assert not any(o.op_name == "attach-gre-tunnel" for o in ops)
    assert {o.scope["host"] for o in ops if o.op_name == "deploy-sensor"} == {"pcap", "ids"}
    # No record-tap-intent either (retired -- `docs/WORK_ORDER_tap_
    # eligibility.md` D4) -- neither host records anything tap-related.
    assert not any(o.op_name == "record-tap-intent" for o in ops)


def test_bake_sensor_type_image_selection(manifests):
    """A suricata-sensor clones the PRE-BAKED suricata-sensor image (its tool
    isn't installable in the isolated scenario VM); tcpdump-sensor clones the
    generic debian image. An explicit `image:` overrides. `tier="o2"`: sensor
    VM synthesis is now tier-gated (review fix F1) to A1's standing-surface path."""
    from axrange.sensors import default_image_for
    assert default_image_for("suricata-sensor") == "suricata-sensor@v1"
    assert default_image_for("tcpdump-sensor") == "debian-12-cloud@v1"
    assert default_image_for(None) == "debian-12-cloud@v1"

    src = _emit_ir(
        "sensors:\n"
        "          - { id: pcap, type: tcpdump-sensor, primary: true }\n"
        "          - { id: ids, type: suricata-sensor }"
    )
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")
    # clone-vm's image_id is the id (the @version is split off for the binding).
    img = {o.scope["host"]: o.inputs_resolved.get("image_id")
           for o in plan.ops.values()
           if o.op_name == "clone-vm" and o.scope.get("host") in ("pcap", "ids")}
    assert img["ids"] == "suricata-sensor"
    assert img["pcap"] == "debian-12-cloud"


def test_no_sensors_block_no_sensor_vm_no_gretap(manifests):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md),
    reinforced by full-topology tap eligibility (2026-07-28, docs/
    WORK_ORDER_tap_eligibility.md D4): no `sensors:` ⇒ no sensor VM, no
    auto-provisioned transport vnet, and (like every te plan now) no
    attach-gre-tunnel or record-tap-intent — the config-IP fallback this
    test used to exercise is retired along with the rest of te-side mirror
    emission."""
    src = _emit_ir("sensors: []")
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    ops = list(plan.ops.values())
    assert not any(o.op_name == "deploy-sensor" for o in ops)
    assert not any(o.op_name == "create-vnet"
                   and o.inputs_resolved.get("net_id") == "axr-sensor-transport"
                   for o in ops)
    assert not any(o.op_name == "attach-gre-tunnel" for o in ops)
    assert not any(o.op_name == "record-tap-intent" for o in ops)
