"""Capture-heartbeat sentinel render + `verify-capture` (OP-MIR-040) backend
(`docs/design/06-taps-sensors.md`).

Renamed/trimmed from `test_dynamic_tap.py` (`docs/WORK_ORDER_tap_eligibility.md`,
Authority: user, 2026-07-28): the twin substrate no longer resolves or
records ANY tap-related state at emission time (`nodes[].tap`/
`nodes[].nets[].tap`/`networks[].tap` removed from IR_SCHEMA; `record-tap-
intent`/OP-MIR-005 removed from the op catalog) -- so the IR-parse,
resolver, emission, and STR-190/REF-200/NET-110 validator coverage
`test_dynamic_tap.py` used to carry is gone with it (those rules are
retired; `axrange.o2control.taps`'s discovery is covered by
`tests/test_o2_taps.py`/`tests/test_twin_inventory.py` instead).

What SURVIVES here, unchanged in substance: `verify-capture` is still a
real op the A2 tap playout composes against a deployed twin's eligible
interfaces (`docs/OP_CATALOG.md` OP-MIR-040), and its render/backend
mechanics (the sentinel token/script, the Windows/Linux injector split, the
in-guest QGA exec + sensor confirm read) are entirely independent of HOW
the tap playout discovered the interface to verify.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from axrange.adapter_proxmox.config import AdapterConfig, load_config
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    ConfigRenderError,
    capture_confirm_command,
    capture_sentinel_hex,
    capture_sentinel_runner_path_windows,
    capture_sentinel_token,
    render_capture_sentinel,
    render_capture_sentinel_windows,
)
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Sentinel render
# ---------------------------------------------------------------------------


def test_sentinel_token_deterministic_and_small():
    a = capture_sentinel_token("deploy-1", "vbr_corp")
    b = capture_sentinel_token("deploy-1", "vbr_corp")
    c = capture_sentinel_token("deploy-2", "vbr_corp")
    assert a == b and a != c
    assert a.startswith("AXR") and len(a) == 11
    # hex pattern fits ping -p's 16-byte cap (small for the GRE MTU, §11).
    assert len(bytes.fromhex(capture_sentinel_hex(a))) <= 16


def test_sentinel_runner_carries_token_and_is_posix():
    tok = capture_sentinel_token("d", "br")
    runner = render_capture_sentinel(token=tok, count=5)
    assert runner.startswith("#!/bin/sh")
    assert tok in runner
    assert "ping" in runner and capture_sentinel_hex(tok) in runner


def test_confirm_command_matches_token_both_engines():
    tok = capture_sentinel_token("d", "br")
    cmd = capture_confirm_command(iface="gre-cap", token=tok, timeout_s=10)
    # prefers tshark, falls back to tcpdump; both reference the token/hex + iface.
    assert "tshark" in cmd and "tcpdump" in cmd
    assert capture_sentinel_hex(tok) in cmd  # tshark frame contains <hex>
    assert tok in cmd  # tcpdump -A | grep <ascii>
    assert "gre-cap" in cmd


# ---------------------------------------------------------------------------
# Windows sentinel variant (2026-07-24: an all-Windows segment's injector has
# no POSIX `ping -p` -- `render_capture_sentinel_windows` carries the SAME
# token bytes via .NET `Ping.Send`'s buffer arg instead).
# ---------------------------------------------------------------------------
def test_render_capture_sentinel_windows_carries_token_in_icmp_buffer():
    tok = capture_sentinel_token("d", "br")
    ps = render_capture_sentinel_windows(token=tok, target="10.0.0.1", count=7)
    assert "[System.Net.NetworkInformation.Ping]::new()" in ps  # LES-022: not New-Object
    assert "New-Object" not in ps
    assert f"$token = '{tok}'" in ps
    assert "[System.Text.Encoding]::ASCII.GetBytes($token)" in ps  # buffer IS the payload
    assert "$targetHost = '10.0.0.1'" in ps
    assert "$count = 7" in ps
    assert "$ping.Send($targetHost, 1000, $buffer)" in ps


def test_render_capture_sentinel_windows_requires_a_gateway_target():
    """Unlike the Linux sentinel (defaults to 255.255.255.255 broadcast), the
    Windows sentinel has no reliable broadcast path -- an empty target must
    fail loud, not silently fall back."""
    tok = capture_sentinel_token("d", "br")
    with pytest.raises(ConfigRenderError):
        render_capture_sentinel_windows(token=tok, target="")


def test_render_capture_sentinel_windows_escapes_single_quotes_les036():
    """LES-036: single-quoted PS literals need doubled embedded `'`, not
    double-quote-style backtick/`$` escaping."""
    tok = "AXRdeadbeef"
    ps = render_capture_sentinel_windows(token=tok, target="10.0.0.1's-router")
    assert "10.0.0.1''s-router" in ps
    # the literal quote must not terminate the string early / break parsing.
    assert ps.count("$targetHost = '10.0.0.1''s-router'") == 1


def test_render_capture_sentinel_windows_is_byte_stable():
    tok = capture_sentinel_token("d", "br")
    a = render_capture_sentinel_windows(token=tok, target="10.0.0.1")
    b = render_capture_sentinel_windows(token=tok, target="10.0.0.1")
    assert a == b


def test_capture_sentinel_runner_path_windows_uses_ps1_and_backslashes():
    path = capture_sentinel_runner_path_windows("corp", "pcap")
    assert path.endswith(".ps1")
    assert "\\" in path
    assert "/" not in path


def test_windows_and_linux_sentinels_carry_the_same_token_bytes_on_the_wire():
    """Both sentinel variants MUST put the identical bytes in the ICMP
    payload, because the sensor's `capture_confirm_command` is unchanged
    either way (one `frame contains <hex>` / `grep <token>` pair covers
    both). The Linux side sends `capture_sentinel_hex(token)` (the token's
    ASCII bytes, capped at 16, hex-encoded) via `ping -p`; the Windows side
    sends `[System.Text.Encoding]::ASCII.GetBytes($token)` (the token's full
    ASCII bytes) via `Ping.Send`'s buffer arg. A real `capture_sentinel_token`
    is always 11 ASCII chars (well under the 16-byte `ping -p` cap), so the
    two encodings must agree byte-for-byte."""
    tok = capture_sentinel_token("dep-x", "vbr_corp")
    linux_bytes = bytes.fromhex(capture_sentinel_hex(tok))
    windows_bytes = tok.encode("ascii")  # what $buffer's GetBytes($token) is
    assert linux_bytes == windows_bytes
    # And the sensor-side confirm command's hex pattern matches EITHER
    # encoding, proving one confirm command genuinely covers both injectors.
    confirm = capture_confirm_command(iface="gre-cap", token=tok, timeout_s=5)
    assert linux_bytes.hex() in confirm
    assert windows_bytes.hex() == linux_bytes.hex()


def test_render_capture_sentinel_windows_dollar_and_backtick_pass_through_unescaped():
    """A single-quoted PowerShell literal does NOT interpolate `$`/backtick
    (only double-quoted strings do) -- LES-036 says only `'` needs doubling.
    A token/target carrying `$env:PATH`-shaped or backtick content must ride
    through byte-for-byte, not be mangled by an unnecessary extra escape."""
    tricky_target = "10.0.0.1-$(whoami)-`n-end"
    ps = render_capture_sentinel_windows(token="AXRdeadbeef", target=tricky_target)
    # Passed through verbatim inside the single-quoted literal (no doubling
    # of anything but `'`, which this string doesn't contain).
    assert f"$targetHost = '{tricky_target}'" in ps
    # Sanity: this is inside a SINGLE-quoted literal, not a here-string or
    # double-quoted interpolation that could execute $(...).
    assert '"' not in ps.split("$targetHost = ")[1].split("\n")[0]


def test_render_capture_sentinel_linux_output_is_unchanged_by_the_windows_addition():
    """Regression pin (2026-07-24 Windows sentinel addition): the PRE-
    EXISTING Linux sentinel render must be byte-identical to its pre-change
    shape -- a live-verified path must never shift because a sibling
    function was added nearby."""
    tok = capture_sentinel_token("pin-dep", "vbr_pin")
    runner = render_capture_sentinel(token=tok, count=3)
    assert runner == (
        "#!/bin/sh\n"
        "# axRange verify-capture sentinel injector "
        "(docs/design/06-taps-sensors.md).\n"
        f"# token={tok}\n"
        "set -u\n"
        f"TOKEN='{tok}'\n"
        f"HEXPAT='{capture_sentinel_hex(tok)}'\n"
        "TARGET='255.255.255.255'\n"
        "COUNT=3\n"
        "# -b: allow broadcast; -p: payload pattern carrying the token bytes.\n"
        '# Best-effort; -W small so we do not block. Failure to get a reply is\n'
        "# fine -- the tap mirrors the egressing request frames either way.\n"
        'ping -b -c "$COUNT" -i 0.2 -W 1 -p "$HEXPAT" "$TARGET" '
        ">/dev/null 2>&1 || true\n"
        f'echo "axrange-sentinel-sent $TOKEN"\n'
    )


# ---------------------------------------------------------------------------
# verify-capture backend (fake QGA + fake sensor confirm)
# ---------------------------------------------------------------------------


class _FakeGA:
    def __init__(self):
        self.written: dict[str, str] = {}
        self.mkdirs: set[str] = set()
        self.execs: list[tuple] = []

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append(tuple(argv))
        if argv[:2] == ["mkdir", "-p"]:
            self.mkdirs.add(argv[2])
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        parent = path.rsplit("/", 1)[0]
        if parent not in self.mkdirs:
            raise AdapterError(
                f"writing {path}: parent {parent} not created", error_class="x"
            )
        self.written[path] = content


def _vc_op(timeout_s=30):
    d = get_op("verify-capture")
    return OpInstance(
        op_id="p:verify-capture", op_name="verify-capture",
        op_catalog_id="OP-MIR-040", kind=OpKind.ACTION,
        scope={"net": "corp", "sensor": "default", "host": "h-a"},
        idempotency_class=IdempotencyClass.CONFIG,
        cleanup_strategy=d.cleanup_strategy, timeout_s=timeout_s,
    )


def _exec_ctx():
    return ExecutionContext(
        plan_id="p", deploy_id="dep1",
        cluster=ClusterContext(default_node="px1"), placement={},
    )


def _vc_backend(fake):
    return ConfigBackend(
        AdapterConfig(),
        rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda s: None,
        actor_poll_interval_s=0.0,
    )


class _FakeGASensor(_FakeGA):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    verify-capture reads the sensor over QGA via `sensor_vmid` exclusively —
    no more injectable SENSOR_SSH_* confirm fn. This fake distinguishes the
    injector's vmid (fires the sentinel, always ok) from the sensor's vmid
    (the confirm read, whose result this fixture controls)."""

    def __init__(self, sensor_vmid, *, sensor_ok):
        super().__init__()
        self.sensor_vmid = sensor_vmid
        self.sensor_ok = sensor_ok
        self.sensor_execs: list[tuple] = []

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        if vmid == self.sensor_vmid and argv[:2] != ["mkdir", "-p"]:
            self.sensor_execs.append(tuple(argv))
            if self.sensor_ok:
                return GuestExecResult(0, "hit", "")
            return GuestExecResult(1, "", "nothing seen")
        return super().exec(vmid, argv, input_data=input_data, timeout_s=timeout_s)


def test_verify_capture_passes_when_sentinel_confirmed():
    fake = _FakeGASensor(sensor_vmid=200, sensor_ok=True)

    out = _vc_backend(fake).run(
        _vc_op(),
        {"vmid": 100, "net_id": "corp", "sensor_id": "default",
         "bridge_name": "vbr_corp", "sensor_vmid": 200},
        _exec_ctx(),
    )
    assert out["capture_verified"] is True
    # the sentinel token encodes (deploy_id, bridge) and rides the confirm cmd.
    tok = capture_sentinel_token("dep1", "vbr_corp")
    assert out["token"] == tok
    assert any(tok in " ".join(a) for a in fake.sensor_execs)
    # the injector ran the sentinel script in-guest.
    assert any("sentinel" in w for w in fake.written)


@pytest.mark.slow
def test_verify_capture_fails_loud_when_no_sentinel():
    """The silent-zero-capture case must surface as a real failure (brief §6)."""
    fake = _FakeGASensor(sensor_vmid=200, sensor_ok=False)

    with pytest.raises(AdapterError) as e:
        # Small real-time budget: the loop is bounded by a monotonic deadline, so a
        # 1s budget keeps the test fast while still exercising the
        # inject->confirm->retry->fail path (>=1 round guaranteed).
        _vc_backend(fake).run(
            _vc_op(timeout_s=1),
            {"vmid": 100, "net_id": "corp", "sensor_id": "default",
             "bridge_name": "vbr_corp", "sensor_vmid": 200},
            _exec_ctx(),
        )
    assert e.value.error_class == "capture-not-verified"


def test_verify_capture_requires_sensor_vmid_no_ssh_fallback():
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    no `sensor_vmid` -> a clear, immediate error — no legacy SENSOR_SSH_*
    mgmt-SSH read path to fall back to."""
    with pytest.raises(AdapterError) as e:
        _vc_backend(_FakeGA()).run(
            _vc_op(),
            {"vmid": 100, "net_id": "corp", "sensor_id": "default",
             "bridge_name": "vbr_corp"},  # no sensor_vmid
            _exec_ctx(),
        )
    assert e.value.error_class == "verify-capture-no-sensor-vmid"


def test_verify_capture_routes_through_config_backend_dispatch():
    """ConfigBackend.run dispatches verify-capture (executor routes config-class
    here by idempotency_class)."""
    fake = _FakeGASensor(sensor_vmid=1, sensor_ok=True)
    out = _vc_backend(fake).run(
        _vc_op(),
        {"vmid": 1, "net_id": "corp", "sensor_id": "default", "bridge_name": "b",
         "sensor_vmid": 1},
        _exec_ctx(),
    )
    assert out["capture_verified"] is True


# ---------------------------------------------------------------------------
# verify-capture backend -- Windows injector (2026-07-24). The sensor is
# ALWAYS Linux (`sensor_ga` stays the default), only the INJECTOR side
# dispatches to PowerShell when `os_family="windows"`.
# ---------------------------------------------------------------------------
class _FakeGAWindows(_FakeGASensor):
    """A Windows injector's GuestAgent surface: `shell_exec` for the mkdir
    (`New-Item`) + backgrounded inject (`Start-Process`) steps, `write_file`
    for the script delivery (no mkdirs-tracking precondition -- unlike
    `_FakeGA`'s Linux `exec(["mkdir", "-p", ...])` gate, a Windows
    `write_file` doesn't depend on a prior `.exec()` call at all)."""

    def __init__(self, sensor_vmid, *, sensor_ok):
        super().__init__(sensor_vmid, sensor_ok=sensor_ok)
        self.shell_execs: list[str] = []

    def shell_exec(self, vmid, command, *, timeout_s=300):
        self.shell_execs.append(command)
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        self.written[path] = content


def test_verify_capture_windows_injector_uses_powershell_sentinel():
    """`os_family=windows` + a resolved `gateway_ip` -> the PowerShell
    sentinel renderer, delivered/run via `shell_exec`/`write_file`, never the
    Linux `mkdir`/`nohup` argv path. The sensor confirm read is UNCHANGED
    (still a plain `sh -c` exec on the sensor vmid)."""
    fake = _FakeGAWindows(sensor_vmid=200, sensor_ok=True)
    out = _vc_backend(fake).run(
        _vc_op(),
        {"vmid": 100, "net_id": "corp", "sensor_id": "default",
         "bridge_name": "vbr_corp", "sensor_vmid": 200,
         "os_family": "windows", "gateway_ip": "10.0.0.1"},
        _exec_ctx(),
    )
    assert out["capture_verified"] is True
    # the sentinel script was written to a `.ps1` path and carries the token
    # + gateway target.
    tok = capture_sentinel_token("dep1", "vbr_corp")
    ps_paths = [p for p in fake.written if p.endswith(".ps1")]
    assert len(ps_paths) == 1
    assert tok in fake.written[ps_paths[0]]
    assert "10.0.0.1" in fake.written[ps_paths[0]]
    # mkdir + the backgrounded inject both went through shell_exec (PS), not
    # a POSIX argv exec.
    assert any("New-Item" in c for c in fake.shell_execs)
    assert any("Start-Process" in c and "Hidden" in c for c in fake.shell_execs)
    assert not any(argv[:2] == ["mkdir", "-p"] for argv in fake.execs)
    assert not any("nohup" in " ".join(argv) for argv in fake.execs)
    # the sensor confirm read is unchanged -- a plain `sh -c` on the sensor vmid.
    assert any(tok in " ".join(a) for a in fake.sensor_execs)


def test_verify_capture_windows_injector_without_gateway_fails_loud():
    """A Windows injector with NO resolvable gateway_ip must fail loud
    (`verify-capture-no-gateway`), never silently fall back to the Linux
    broadcast default."""
    fake = _FakeGAWindows(sensor_vmid=200, sensor_ok=True)
    with pytest.raises(AdapterError) as e:
        _vc_backend(fake).run(
            _vc_op(),
            {"vmid": 100, "net_id": "corp", "sensor_id": "default",
             "bridge_name": "vbr_corp", "sensor_vmid": 200,
             "os_family": "windows"},  # no gateway_ip
            _exec_ctx(),
        )
    assert e.value.error_class == "verify-capture-no-gateway"


def test_verify_capture_linux_injector_ignores_gateway_ip_keeps_broadcast():
    """A `gateway_ip` in inputs (now always threaded when resolvable,
    `taps.py._compose_ops`) must NOT change the Linux sentinel's behavior --
    it keeps rendering the broadcast-default POSIX script (`render_
    capture_sentinel` takes no `target` from `_verify_capture` at all)."""
    fake = _FakeGASensor(sensor_vmid=200, sensor_ok=True)
    out = _vc_backend(fake).run(
        _vc_op(),
        {"vmid": 100, "net_id": "corp", "sensor_id": "default",
         "bridge_name": "vbr_corp", "sensor_vmid": 200,
         "gateway_ip": "10.0.0.1"},  # present but irrelevant on Linux
        _exec_ctx(),
    )
    assert out["capture_verified"] is True
    sh_paths = [p for p in fake.written if p.endswith(".sh")]
    assert len(sh_paths) == 1
    assert "255.255.255.255" in fake.written[sh_paths[0]]


def test_verify_capture_uses_os_family_scoped_injector_channel_distinct_from_sensor():
    """The injector GuestAgent must be requested with the INJECTOR's real
    `os_family` and the sensor GuestAgent must always be requested with NO
    `os_family` override (sensors are always Linux) -- proving the two
    channels are genuinely independently resolved, not merely the same fake
    object silently answering to both roles (which every other test above
    uses, and would mask a bug where `os_family` never actually reaches the
    factory call)."""
    injector_fake = _FakeGAWindows(sensor_vmid=200, sensor_ok=True)
    sensor_fake = _FakeGASensor(sensor_vmid=200, sensor_ok=True)
    factory_calls: list[dict] = []

    def factory(client, node, **kw):
        factory_calls.append(kw)
        return injector_fake if kw.get("os_family") == "windows" else sensor_fake

    backend = ConfigBackend(
        AdapterConfig(), rest_client=object(), guest_agent_factory=factory,
        sleep_fn=lambda s: None, actor_poll_interval_s=0.0,
    )
    out = backend.run(
        _vc_op(),
        {"vmid": 100, "net_id": "corp", "sensor_id": "default",
         "bridge_name": "vbr_corp", "sensor_vmid": 200,
         "os_family": "windows", "gateway_ip": "10.0.0.1"},
        _exec_ctx(),
    )
    assert out["capture_verified"] is True
    assert any(c.get("os_family") == "windows" for c in factory_calls)
    assert any(c.get("os_family") in (None, "linux") for c in factory_calls)
    # the sentinel landed on the WINDOWS-resolved fake only.
    assert any(p.endswith(".ps1") for p in injector_fake.written)
    assert not any(p.endswith(".ps1") for p in sensor_fake.written)
    # the confirm read happened against the (distinct) sensor-resolved fake.
    assert sensor_fake.sensor_execs


# ---------------------------------------------------------------------------
# Config: no more SENSOR_* parsing at all
# ---------------------------------------------------------------------------


def test_config_no_longer_parses_sensor_star(tmp_path, monkeypatch):
    """Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md):
    no code path reads SENSOR_* from adapter.conf/env any more."""
    for k in list(__import__("os").environ):
        if k.startswith(("SENSOR_", "OVS_", "PROXMOX_")):
            monkeypatch.delenv(k, raising=False)
    cfg = load_config(
        config_path=tmp_path / "none.conf",
        overrides={
            "SENSOR_SSH_HOST": "10.255.255.10",
            "SENSOR_SSH_USER": "capture",
            "SENSOR_CAPTURE_IFACE": "gre0",
            "SENSOR_TRANSPORT_IP": "10.255.255.10",
        },
    )
    assert not hasattr(cfg, "sensor_ssh_host")
    assert not hasattr(cfg, "sensor_capture_iface")
    assert not hasattr(cfg, "sensor_transport_ip")
