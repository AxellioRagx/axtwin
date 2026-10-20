"""The `scripted` driver (docs/design/07-traffic-personas.md,
docs/WORK_ORDER_persona_library.md §2): render a per-persona in-guest
client from `emits[]` and launch it as a detached transient systemd unit.

**v1 fidelity note (deferred, not silently omitted):** the design brief
frames scripted personas as running real client tools (`smbclient` /
`ldapsearch` / `curl` / a Modbus client). Building a genuine per-protocol
client for every entry in `KNOWN_PROTOS` is out of this work order's scope
(schema + drivers + mechanism, not per-protocol content) — the renderer
below is a generic, protocol-agnostic connectivity-shape emitter: for each
`emits[]` entry it opens a TCP connection to the resolved peer:port at a
seeded cadence and sends a small tagged payload naming the protocol,
producing the RIGHT SHAPE of traffic (peer, port, timing, volume) without
faking protocol semantics. Layering real per-protocol clients on top is the
Traffic tier's / content curation's job — every hook needed to do so
(the emit's `proto`, resolved `peer_ip`, `ports`) is already threaded
through to the rendered script.

Deterministic: `render_scripted_client()` given the same persona + peers +
seed + instance_id renders byte-identical output — the only seed-dependent
content is a literal `SEED` int the guest script itself draws runtime
jitter from (same pattern as `config_exec.render.render_activity_scheduler`).

**Windows port (2026-07-25, `docs/WORK_ORDER_persona_library.md`):** a
`persona.os_family == "windows"` instance renders + launches a PowerShell
counterpart instead (`render_scripted_client_windows` /
`render_scripted_launch_windows`) — same connectivity-shape emitter (per-emit
TCP connect at a seeded cadence), reproducing the shape of the client above
in a language the (python3-free) Windows base images can actually run.
Dispatch lives in `ScriptedDriver.start`/`stop`/`heartbeat` on
`persona.os_family`; the LINUX path below is UNCHANGED (byte-identical,
live-verified/tested — this port only adds a second branch, it does not
touch the first).

**Schedule override (2026-07-25, docs/WORK_ORDER_traffic_tier.md):** an
optional `schedule=` argument (threaded from a traffic-profile assignment's
`schedule:` field, `o2control.traffic`) forces every emit's runtime GATE to
always-on (`"24x7"`) or business-hours-only (`"business-hours"`), overriding
the persona's own cadence-derived default. `schedule=None` (the default,
and every pre-2026-07-25 call site) is a no-op — the cadence-derived gate is
unchanged, byte-identical. See `_resolve_emit_schedule`.
"""
from __future__ import annotations

import re
from typing import Any

from axrange.ir.durations import DurationParseError, parse_duration
from axrange.config_exec.render import (
    scheduled_task_start_block,
    systemd_daemon_start_block,
)
from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    effective_seed,
    heartbeat_scheduled_task,
    heartbeat_unit,
    instance_paths,
    safe_label,
    start_linux_python_client,
    start_windows_powershell_client,
    stop_scheduled_task,
    stop_unit,
    unsupported_os_family_error,
)
from axrange.personas.schema import Emit, Persona

_CADENCE_RE = re.compile(r"^(cyclic|burst):(.+)$")

# volume -> (message count, BYTES per message, inter-message gap).
#
# **2026-07-28 — the volume lever.** Before this, `volume:` mapped to a
# message COUNT alone (`{"low": 2, "med": 8, "high": 24}`) and every message
# carried a fixed ~40-byte tag over a fresh TCP connection, with a hardcoded
# 0.2s inter-message gap. That capped a whole persona instance at ~5
# connections/second (~2 kbit/s at `volume: high`) no matter what the catalog
# authored — the client is a single-threaded loop over all emits, so the gap
# bounded the persona, not just the emit. `volume:` therefore could not
# express byte size at all: "a 2 GB nightly backup" and "a DNS query" were
# the same traffic.
#
# Now a level resolves all three knobs together, spanning ~4 orders of
# magnitude of offered load, and `bulk` is the elephant-flow tier:
#   low  ->      512 B / fire      med  ->    32 KiB / fire
#   high ->  1.5 MiB / fire        bulk ->   64 MiB / fire
# (per emit, at the default 60s interval; ~9 Mbit/s sustained for `bulk`).
# An emit can still pin `bytes:` explicitly to move byte size WITHOUT
# dragging message count and gap along with it (schema.Emit.bytes_per_msg).
_VOLUME_PROFILE: dict[str, dict[str, Any]] = {
    "low":  {"messages": 2,  "bytes": 256,             "gap_s": 0.5},
    "med":  {"messages": 8,  "bytes": 4 * 1024,        "gap_s": 0.2},
    "high": {"messages": 24, "bytes": 64 * 1024,       "gap_s": 0.05},
    "bulk": {"messages": 64, "bytes": 1024 * 1024,     "gap_s": 0.0},
}
_DEFAULT_INTERVAL_S = 60.0

# Wire constants for the rendered client. A UDP message larger than the MTU
# is chunked into that many datagrams (rather than handed to the kernel as
# one oversized sendto, which would fragment or fail) — this is what turns a
# `bulk` UDP emit into a genuinely high PACKET rate, the load profile that
# actually overruns a capture ring.
_UDP_MTU = 1400
_TCP_CHUNK = 64 * 1024

# Literal wire addresses for the reserved segment-scoped peer roles
# (`schema.RESERVED_PEER_ROLES`). `broadcast` is the LIMITED broadcast
# address deliberately: it is never forwarded off the local segment, which is
# exactly the blast radius segment chatter should have (and exactly what a
# `segment`-scoped tap point sees).
_BROADCAST_ADDR = "255.255.255.255"
_MULTICAST_GROUPS = {
    "mdns": "224.0.0.251",
    "llmnr": "224.0.0.252",
    "ssdp": "239.255.255.250",
    "coap": "224.0.1.187",
    # HSRP v1's all-routers group. Added with the `network-device` persona work: aximprint
    # infers that role from a host dialling 1985/udp, so the persona has to actually emit it
    # (aximprint docs/JOURNAL_corpapcn_fidelity.md GAP-C8). It is genuinely multicast, not
    # broadcast -- mislabelling it to dodge this table would put the wrong address on the wire.
    "hsrp": "224.0.0.2",
}
# Business-hours gate for `diurnal` cadence (09:00-17:00 local, Mon-Fri) — a
# fixed default window; a per-knob override is a `traffic_tier` follow-on
# (personas carry no `schedule:` block of their own, unlike activities).
_BUSINESS_START_MIN = 9 * 60
_BUSINESS_END_MIN = 17 * 60


def _resolve_emit_schedule(
    emit: Emit, *, schedule_override: str | None = None
) -> dict[str, Any]:
    """cadence/volume -> a concrete runtime firing schedule. Degrades to the
    24x7 default on a malformed cadence rather than raising — PSN-060
    already flags a bad cadence at catalog-validate time; the renderer must
    still produce SOMETHING for a bad-but-loadable persona under test.

    `schedule_override` (2026-07-25, docs/WORK_ORDER_traffic_tier.md) is the
    traffic-profile ASSIGNMENT's `schedule:` field, already normalized by
    `o2control.traffic._normalize_schedule` to `"24x7"` / `"business-hours"`
    / `None`. When set, it forces every emit's GATE regardless of the
    persona's own cadence (an operator's on-demand override for a test run);
    `interval_s`/`burst_s`/`messages` stay cadence-derived either way —
    `schedule` is a gate override, not a full cadence replacement.

    **BACKWARD-COMPAT PIN:** `schedule_override=None` (the default, and every
    call site that existed before this override) leaves the cadence-derived
    `gate` exactly as computed below, untouched — this is what keeps every
    existing render byte-identical."""
    cadence = emit.cadence
    gate = "always"
    interval_s = _DEFAULT_INTERVAL_S
    is_burst = False
    if cadence == "24x7":
        pass
    elif cadence == "diurnal":
        gate = "business_hours"
    else:
        m = _CADENCE_RE.match(cadence)
        if m:
            kind, spec = m.group(1), m.group(2)
            try:
                dur = parse_duration(spec)
            except DurationParseError:
                dur = _DEFAULT_INTERVAL_S
            # **2026-07-28 burst fix.** BOTH kinds now mean "fire every
            # <duration>"; they differ in SHAPE, not period — a `cyclic`
            # emit paces its messages at the volume gap, a `burst` emit
            # fires them back-to-back (gap 0) so the traffic actually looks
            # like a burst on the wire.
            #
            # Before this, `burst:` hardcoded `interval_s = 86400.0`
            # REGARDLESS of the authored duration, and the duration only
            # controlled how thinly the messages were spread within that
            # daily firing. So `corp-workstation`'s `print` emit
            # (`burst:30m`, volume low) fired 2 connections PER DAY, and
            # `burst:30m` vs `burst:8h` differed only in spread, never in
            # frequency. Treated as a bug rather than a contract: nothing in
            # the catalog can plausibly have wanted a twice-daily print job,
            # and the naming symmetry with `cyclic:<dur>` ("every <dur>") is
            # unambiguous.
            interval_s = dur
            is_burst = kind == "burst"
    profile = _VOLUME_PROFILE.get(emit.volume, _VOLUME_PROFILE["low"])
    if schedule_override == "24x7":
        gate = "always"
    elif schedule_override == "business-hours":
        gate = "business_hours"
    return {
        "interval_s": interval_s,
        "gate": gate,
        "messages": profile["messages"],
        # An explicit `bytes:` overrides the volume-derived default; 0/unset
        # falls back to the profile (schema.Emit.bytes_per_msg).
        "bytes": emit.bytes_per_msg or profile["bytes"],
        "gap_s": 0.0 if is_burst else profile["gap_s"],
    }


def resolve_emit_peers(
    emit: Emit,
    peers: dict[str, str] | None,
    peer_sets: dict[str, list[str]] | None = None,
) -> list[str]:
    """Every concrete wire address this emit should talk to.

    Three cases beyond the ordinary single resolved peer:

    * `peer_role: broadcast` / `multicast` — reserved SEGMENT-scoped tokens
      (`schema.RESERVED_PEER_ROLES`). They resolve to a literal wire address
      here in the driver, not to a twin host, because `o2control.traffic`
      deliberately knows nothing about wire semantics — it skips every
      reserved token. A multicast emit whose proto has no well-known group
      is a real authoring error and raises (LES-001: never silently
      substitute a plausible-looking wrong address).
    * `peer_scope: all` — fan out across every host holding the role, from
      the `peer_sets` map `_resolve_peer_sets` supplies. Falls back to the
      single `peers` entry when no set was threaded, so a driver called
      without `peer_sets` (the pre-2026-07-28 signature, and every
      `peer_scope: one` persona) behaves exactly as before.
    * unresolved — an empty list; the guest client logs-and-skips, matching
      the pre-existing graceful behavior for an unresolved `peer_role`.
    """
    role = emit.peer_role
    if role == "broadcast":
        return [_BROADCAST_ADDR]
    if role == "multicast":
        group = _MULTICAST_GROUPS.get(emit.proto)
        if not group:
            raise PersonaDriverError(
                f"emit proto {emit.proto!r} uses peer_role 'multicast' but has "
                "no well-known multicast group — use peer_role 'broadcast' for "
                "a broadcast-based protocol (e.g. nbns/dhcp), or add the group "
                "to scripted._MULTICAST_GROUPS"
            )
        return [group]
    if emit.peer_scope == "all":
        found = (peer_sets or {}).get(role) or []
        if found:
            return list(found)
    ip = (peers or {}).get(role)
    return [ip] if ip else []


_CLIENT_TEMPLATE = '''#!/usr/bin/env python3
# axRange scripted persona client: {persona_id} (instance {instance_id})
import os, random, socket, time
from datetime import datetime

INSTANCE = {instance_id!r}
PERSONA = {persona_id!r}
LOG = {log_path!r}
SEED = {seed!r}      # None -> unseeded (non-reproducible timing)
EMITS = {emits!r}
BIZ_START = {biz_start!r}
BIZ_END = {biz_end!r}
UDP_MTU = {udp_mtu!r}
TCP_CHUNK = {tcp_chunk!r}

rng = random.Random(SEED)
FILLER = b'axrange-persona-payload '


def log(msg):
    with open(LOG, 'a') as f:
        f.write('[axrange] persona ' + INSTANCE + ' ' + msg + ' '
                 + datetime.now().isoformat() + chr(10))


def in_business_hours(now):
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return BIZ_START <= minute < BIZ_END


def body(tag, n):
    # The payload stays self-identifying at every size: a capture of a 1 MiB
    # bulk message still opens with the persona/proto tag.
    if n <= len(tag):
        return tag[:n]
    reps = (n - len(tag)) // len(FILLER) + 1
    return (tag + FILLER * reps)[:n]


def send_tcp(tag, peer, port, nbytes):
    sent = 0
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((peer, port))
        while sent < nbytes:
            chunk = nbytes - sent
            if chunk > TCP_CHUNK:
                chunk = TCP_CHUNK
            s.sendall(body(tag, chunk))
            sent += chunk
    finally:
        s.close()
    return sent


def send_udp(emit, tag, peer, port, nbytes):
    sent = 0
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    try:
        if emit['broadcast']:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if emit['multicast']:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        while True:
            chunk = nbytes - sent
            if chunk > UDP_MTU:
                chunk = UDP_MTU
            s.sendto(body(tag, chunk), (peer, port))
            sent += chunk
            if sent >= nbytes:
                break
    finally:
        s.close()
    return sent


def fire(emit):
    peer_ips = emit['peer_ips']
    if not peer_ips:
        log('skip ' + emit['proto'] + ' -- peer_role ' + str(emit['peer_role'])
            + ' not resolved')
        return
    tag = ('axrange-persona ' + PERSONA + ' ' + emit['proto'] + ' ').encode()
    udp = emit['transport'] == 'udp'
    nbytes = emit['bytes']
    gap = emit['gap_s']
    total = 0
    fails = 0
    for peer in peer_ips:
        for i in range(emit['messages']):
            port = emit['ports'][i % len(emit['ports'])]
            try:
                if udp:
                    total += send_udp(emit, tag, peer, port, nbytes)
                else:
                    total += send_tcp(tag, peer, port, nbytes)
            except OSError as e:
                fails += 1
                if fails <= 3:
                    log('send ' + emit['proto'] + ' ' + peer + ':' + str(port)
                        + ' failed: ' + str(e))
            if gap > 0:
                time.sleep(gap)
    # ONE summary line per fire, not one per message: at `bulk` volume across
    # a fanned-out peer set, per-message logging would write more to disk than
    # the persona writes to the wire. The literal 'sent' token is kept so
    # existing log greps still match. `fails=` is the operator's tell that a
    # TCP emit's bytes never materialized because nothing was listening.
    log('sent ' + emit['proto'] + ' peers=' + str(len(peer_ips))
        + ' msgs=' + str(emit['messages'] * len(peer_ips))
        + ' bytes=' + str(total) + ' fails=' + str(fails))


log('armed pid=' + str(os.getpid()) + ' emits=' + str(len(EMITS)) + ' seed=' + str(SEED))
next_fire = [0.0] * len(EMITS)
while True:
    now = time.time()
    for idx, emit in enumerate(EMITS):
        if now < next_fire[idx]:
            continue
        if emit['gate'] == 'business_hours' and not in_business_hours(datetime.now()):
            next_fire[idx] = now + 60.0
            continue
        fire(emit)
        jitter = rng.uniform(-0.1, 0.1) * emit['interval_s']
        next_fire[idx] = now + emit['interval_s'] + jitter
    time.sleep(1.0)
'''


def render_scripted_client(
    persona: Persona,
    *,
    instance_id: str,
    peers: dict[str, str] | None = None,
    seed: Any = None,
    schedule: str | None = None,
    peer_sets: dict[str, list[str]] | None = None,
) -> str:
    """Render the deterministic in-guest python3 client for `persona` bound
    to `instance_id` (a specific twin node). `peers` resolves each emit's
    `peer_role` to a concrete IP (or the reserved `egress`/`internet` token
    to whatever address the caller supplies for it); an unresolved
    peer_role is not fatal — the guest script logs a skip and moves on
    (matching the "no profile/binding logic here" boundary: this driver
    doesn't know or enforce whether a role SHOULD be resolvable, only reacts
    to whether it WAS).

    `schedule` (2026-07-25) is an already-normalized traffic-profile
    assignment override ("24x7"/"business-hours") forcing every emit's gate
    — see `_resolve_emit_schedule`. `None` (the default) renders
    byte-identically to before this override existed (BACKWARD-COMPAT PIN)."""
    peers = peers or {}
    paths = instance_paths(instance_id)
    eff_seed = effective_seed(seed, instance_id)
    emit_configs = []
    for emit in persona.emits:
        sched = _resolve_emit_schedule(emit, schedule_override=schedule)
        emit_configs.append(
            {
                "proto": emit.proto,
                "ports": list(emit.ports) or [0],
                "peer_role": emit.peer_role,
                "peer_ips": resolve_emit_peers(emit, peers, peer_sets),
                "transport": emit.effective_transport(),
                "broadcast": emit.peer_role == "broadcast",
                "multicast": emit.peer_role == "multicast",
                **sched,
            }
        )
    return _CLIENT_TEMPLATE.format(
        persona_id=persona.id,
        instance_id=instance_id,
        log_path=paths["log"],
        seed=eff_seed,
        emits=emit_configs,
        biz_start=_BUSINESS_START_MIN,
        biz_end=_BUSINESS_END_MIN,
        udp_mtu=_UDP_MTU,
        tcp_chunk=_TCP_CHUNK,
    )


def render_scripted_launch(instance_id: str, persona: Persona, *, script_path: str) -> str:
    """The bash bootstrap (re)starting the already-written client as a
    detached, deterministically-named transient systemd unit — NEVER
    `nohup ... &` (LES-086)."""
    paths = instance_paths(instance_id)
    cmd = f"exec python3 {script_path}"
    # safe_label: the label is embedded inside systemd_daemon_start_block's
    # double-quoted `echo "..."` lines -- escape it there, not at every
    # call site's discretion (code-review fix).
    label = safe_label(f"persona {instance_id} ({persona.id})")
    return (
        "#!/bin/bash\nset -u\n"
        + systemd_daemon_start_block(
            paths["unit"], cmd, log_path=paths["log"], label=label,
        )
    )


# ---------------------------------------------------------------------------
# Windows port (2026-07-25) — same connectivity-shape emitter, PowerShell.
# ---------------------------------------------------------------------------

# Every exogenous value below is embedded via single-quoted PS literals with
# `''`-doubling and combined with literal text via string CONCATENATION
# (`'a' + $var + 'b'`), never double-quoted `"...$var..."` interpolation — so
# there is no double-quoted context in this template needing the SEPARATE
# double-quote escape rules LES-036 warns a single-quote-only escaper misses.
_CLIENT_TEMPLATE_WINDOWS = '''# axRange scripted persona client (Windows): {persona_id} (instance {instance_id})
$ErrorActionPreference = 'Continue'
$Instance = '{instance_id_sq}'
$PersonaId = '{persona_id_sq}'
$LogPath = '{log_path_sq}'
$Seed = {seed_literal}      # $null -> unseeded (non-reproducible timing)
$BizStart = {biz_start}
$BizEnd = {biz_end}
$UdpMtu = {udp_mtu}
$TcpChunk = {tcp_chunk}
$Emits = @(
{emit_entries}
)

if ($null -ne $Seed) {{ $Rng = [System.Random]::new([int]$Seed) }} else {{ $Rng = [System.Random]::new() }}

function Write-PersonaLog([string]$Msg) {{
    $line = '[axrange] persona ' + $Instance + ' ' + $Msg + ' ' + (Get-Date).ToString('o')
    Add-Content -LiteralPath $LogPath -Value $line
}}

function Test-AxrBizHours {{
    $now = Get-Date
    $dow = [int]$now.DayOfWeek
    if ($dow -eq 0 -or $dow -eq 6) {{ return $false }}
    $minute = $now.Hour * 60 + $now.Minute
    return ($minute -ge $BizStart -and $minute -lt $BizEnd)
}}

# Zero-filled buffer with the persona/proto tag copied over the front — the
# PowerShell counterpart of the Linux client's `body()`. Built with
# [byte[]]::new + [Array]::Copy rather than a per-byte loop, which at `bulk`
# volume (1 MiB/message) would spend far longer in the interpreter than on
# the wire.
function New-AxrBody([byte[]]$Tag, [int]$N) {{
    if ($N -le 0) {{ return [byte[]]::new(0) }}
    $buf = [byte[]]::new($N)
    [Array]::Copy($Tag, 0, $buf, 0, [Math]::Min($Tag.Length, $N))
    return $buf
}}

function Send-AxrTcp([byte[]]$Tag, [string]$Peer, [int]$Port, [int]$NBytes) {{
    $sent = 0
    # [Type]::new(...), not New-Object (LES-022).
    $client = [System.Net.Sockets.TcpClient]::new($Peer, $Port)
    try {{
        $stream = $client.GetStream()
        while ($sent -lt $NBytes) {{
            $chunk = $NBytes - $sent
            if ($chunk -gt $TcpChunk) {{ $chunk = $TcpChunk }}
            $buf = New-AxrBody $Tag $chunk
            $stream.Write($buf, 0, $buf.Length)
            $sent += $chunk
        }}
    }} finally {{
        $client.Close()
    }}
    return $sent
}}

function Send-AxrUdp($Emit, [byte[]]$Tag, [string]$Peer, [int]$Port, [int]$NBytes) {{
    $sent = 0
    $client = [System.Net.Sockets.UdpClient]::new()
    try {{
        if ($Emit.Broadcast) {{ $client.EnableBroadcast = $true }}
        if ($Emit.Multicast) {{ $client.Ttl = 1 }}
        while ($true) {{
            $chunk = $NBytes - $sent
            if ($chunk -gt $UdpMtu) {{ $chunk = $UdpMtu }}
            $buf = New-AxrBody $Tag $chunk
            [void]$client.Send($buf, $buf.Length, $Peer, $Port)
            $sent += $chunk
            if ($sent -ge $NBytes) {{ break }}
        }}
    }} finally {{
        $client.Close()
    }}
    return $sent
}}

function Invoke-PersonaEmit($Emit) {{
    $peerIps = $Emit.PeerIps
    if ($null -eq $peerIps -or $peerIps.Count -eq 0) {{
        Write-PersonaLog ('skip ' + $Emit.Proto + ' -- peer_role ' + $Emit.PeerRole + ' not resolved')
        return
    }}
    $tag = [System.Text.Encoding]::ASCII.GetBytes('axrange-persona ' + $PersonaId + ' ' + $Emit.Proto + ' ')
    $udp = ($Emit.Transport -eq 'udp')
    $gap = [double]$Emit.GapS
    $total = 0
    $fails = 0
    foreach ($peer in $peerIps) {{
        for ($i = 0; $i -lt $Emit.Messages; $i++) {{
            $port = $Emit.Ports[$i % $Emit.Ports.Count]
            try {{
                if ($udp) {{
                    $total += Send-AxrUdp $Emit $tag $peer ([int]$port) ([int]$Emit.Bytes)
                }} else {{
                    $total += Send-AxrTcp $tag $peer ([int]$port) ([int]$Emit.Bytes)
                }}
            }} catch {{
                $fails += 1
                if ($fails -le 3) {{
                    Write-PersonaLog ('send ' + $Emit.Proto + ' ' + $peer + ':' + $port + ' failed: ' + $_.Exception.Message)
                }}
            }}
            if ($gap -gt 0) {{ Start-Sleep -Milliseconds ([int]($gap * 1000)) }}
        }}
    }}
    # One summary line per fire (see the Linux client's `fire()` for why).
    Write-PersonaLog ('sent ' + $Emit.Proto + ' peers=' + $peerIps.Count + ' msgs=' + ($Emit.Messages * $peerIps.Count) + ' bytes=' + $total + ' fails=' + $fails)
}}

Write-PersonaLog ('armed pid=' + $PID + ' emits=' + $Emits.Count + ' seed=' + $Seed)
$NextFire = [double[]]::new($Emits.Count)
while ($true) {{
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    for ($idx = 0; $idx -lt $Emits.Count; $idx++) {{
        $emit = $Emits[$idx]
        if ($now -lt $NextFire[$idx]) {{ continue }}
        if ($emit.Gate -eq 'business_hours' -and -not (Test-AxrBizHours)) {{
            $NextFire[$idx] = $now + 60.0
            continue
        }}
        Invoke-PersonaEmit $emit
        $jitter = ($Rng.NextDouble() * 0.2 - 0.1) * $emit.IntervalS
        $NextFire[$idx] = $now + $emit.IntervalS + $jitter
    }}
    Start-Sleep -Seconds 1
}}
'''


def render_scripted_client_windows(
    persona: Persona,
    *,
    instance_id: str,
    peers: dict[str, str] | None = None,
    seed: Any = None,
    schedule: str | None = None,
    peer_sets: dict[str, list[str]] | None = None,
) -> str:
    """Windows counterpart of `render_scripted_client` — the SAME
    connectivity-shape emitter (per-emit TCP connect/send/close at a seeded
    cadence), rendered as PowerShell instead of python3 (no python3
    dependency baked into the Windows base images the way it is for Linux —
    `docs/IMAGE_BUILD.md`). `[System.Net.Sockets.TcpClient]::new($peer,$port)`
    (LES-022: `[Type]::new(...)`, not `New-Object`) opens+connects in one
    call, mirroring the Linux client's connect/sendall/close shape.

    Deterministic: same inputs -> byte-identical rendered PowerShell (the
    only seed-dependent CONTENT is the literal `$Seed` int the guest script
    itself draws runtime jitter from via `[System.Random]::new($Seed)` — same
    pattern as the Linux client's `random.Random(SEED)`). Reuses
    `_resolve_emit_schedule` — the cadence/volume -> schedule resolution is
    OS-agnostic, only the rendered client language differs.

    `effective_seed()` returns a full unsigned 32-bit value (up to
    `0xFFFFFFFF`), but .NET's `[System.Random]` constructor takes an `Int32`
    (max `0x7FFFFFFF`) — embedding the raw value verbatim would render a
    literal that OVERFLOWS on roughly half of all seeds, throwing at guest
    runtime (`[int]$Seed` on a >Int32.MaxValue value raises
    `OverflowException`). Masked to 31 bits (`& 0x7FFFFFFF`) here, at RENDER
    time, so the embedded literal is always a valid `Int32` — still fully
    deterministic given the seed, just a narrower derived value than the
    Linux client's (which has no such range constraint; Python ints are
    arbitrary-precision).

    `schedule` (2026-07-25): same already-normalized traffic-profile
    override as `render_scripted_client`'s, forcing every emit's gate; `None`
    (the default) renders byte-identically to before this override existed."""
    peers = peers or {}
    paths = instance_paths(instance_id, os_family="windows")
    eff_seed = effective_seed(seed, instance_id)
    seed_literal = (
        str(int(eff_seed) & 0x7FFFFFFF) if eff_seed is not None else "$null"
    )

    def sq(v: Any) -> str:
        return str(v).replace("'", "''")

    emit_lines = []
    for emit in persona.emits:
        sched = _resolve_emit_schedule(emit, schedule_override=schedule)
        peer_ips = resolve_emit_peers(emit, peers, peer_sets)
        # `@()` (an empty PS array) rather than `$null` for the unresolved
        # case — `Invoke-PersonaEmit` tests both, but an array keeps
        # `.Count` valid on every path.
        peer_ips_lit = "@(" + ",".join(f"'{sq(p)}'" for p in peer_ips) + ")"
        ports = list(emit.ports) or [0]
        ports_lit = "@(" + ",".join(str(int(p)) for p in ports) + ")"
        emit_lines.append(
            "    @{ Proto='" + sq(emit.proto) + "'; PeerRole='" + sq(emit.peer_role)
            + "'; PeerIps=" + peer_ips_lit + "; Ports=" + ports_lit
            + "; Transport='" + sq(emit.effective_transport())
            + "'; Broadcast=$" + str(emit.peer_role == "broadcast").lower()
            + "; Multicast=$" + str(emit.peer_role == "multicast").lower()
            + "; Gate='" + sq(sched["gate"]) + "'; IntervalS="
            + repr(float(sched["interval_s"]))
            + "; GapS=" + repr(float(sched["gap_s"]))
            + "; Bytes=" + str(int(sched["bytes"]))
            + "; Messages=" + str(int(sched["messages"])) + " }"
        )

    return _CLIENT_TEMPLATE_WINDOWS.format(
        persona_id=persona.id,
        instance_id=instance_id,
        instance_id_sq=sq(instance_id),
        persona_id_sq=sq(persona.id),
        log_path_sq=sq(paths["log"]),
        seed_literal=seed_literal,
        emit_entries=",\n".join(emit_lines),
        biz_start=_BUSINESS_START_MIN,
        biz_end=_BUSINESS_END_MIN,
        udp_mtu=_UDP_MTU,
        tcp_chunk=_TCP_CHUNK,
    )


def render_scripted_launch_windows(
    instance_id: str, persona: Persona, *, script_path: str
) -> str:
    """The Windows launch bootstrap: (re)registers + starts the already-
    written PowerShell client as a detached Scheduled Task — NEVER a bare
    `Start-Process` child of the QGA exec session (LES-086's Windows analog;
    see `config_exec.render.scheduled_task_start_block`'s docstring for the
    full "why a Scheduled Task, not `Start-Process`" reasoning — a persona is
    long-running and must survive a reboot, unlike Sandcat's one-shot
    `Start-Process -WindowStyle Hidden` enroll)."""
    paths = instance_paths(instance_id, os_family="windows")
    args = f'-NoProfile -ExecutionPolicy Bypass -File "{script_path}"'
    label = f"persona {instance_id} ({persona.id})"
    return scheduled_task_start_block(
        paths["unit"], "powershell.exe", args, label=label,
    )


class ScriptedDriver(PersonaDriver):
    """Renders + launches the generic connectivity-shape client (above) as a
    detached daemon — a systemd transient unit on Linux, a Scheduled Task on
    Windows (dispatch on `persona.os_family`)."""

    def start(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None = None,
        seed: Any = None,
        schedule: str | None = None,
        peer_sets: dict[str, list[str]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if persona.os_family == "windows":
            return self._start_windows(
                ga, vmid, persona, instance_id=instance_id, peers=peers, seed=seed,
                schedule=schedule, peer_sets=peer_sets,
            )
        elif persona.os_family == "linux":
            return self._start_linux(
                ga, vmid, persona, instance_id=instance_id, peers=peers, seed=seed,
                schedule=schedule, peer_sets=peer_sets,
            )
        raise unsupported_os_family_error(persona, instance_id)

    def _start_linux(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None,
        seed: Any,
        schedule: str | None = None,
        peer_sets: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        # Render FIRST (every authoring error surfaces before anything is
        # written into the guest), then hand off to the shared Linux
        # python3-client start sequence — code review item 7,
        # docs/WORK_ORDER_protocol_faithful_traffic.md: this body used to be
        # duplicated almost verbatim in `CommandDriver._start_linux`.
        client_src = render_scripted_client(
            persona, instance_id=instance_id, peers=peers, seed=seed, schedule=schedule,
            peer_sets=peer_sets,
        )
        return start_linux_python_client(ga, vmid, persona, instance_id, client_src)

    def _start_windows(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None,
        seed: Any,
        schedule: str | None = None,
        peer_sets: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        # Render FIRST (every authoring error surfaces before anything is
        # written into the guest), then hand off to the shared Windows
        # PowerShell-client start sequence — the Windows counterpart of the
        # `_start_linux` factoring above, extracted when the `command`
        # driver's Windows branch landed and would otherwise have duplicated
        # this body verbatim (LES-025).
        client_src = render_scripted_client_windows(
            persona, instance_id=instance_id, peers=peers, seed=seed, schedule=schedule,
            peer_sets=peer_sets,
        )
        return start_windows_powershell_client(
            ga, vmid, persona, instance_id, client_src
        )

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        if persona.os_family == "windows":
            return stop_scheduled_task(ga, vmid, instance_id)
        elif persona.os_family == "linux":
            return stop_unit(ga, vmid, instance_id)
        raise unsupported_os_family_error(persona, instance_id)

    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str
    ) -> dict[str, Any]:
        if persona.os_family == "windows":
            return heartbeat_scheduled_task(ga, vmid, instance_id)
        elif persona.os_family == "linux":
            return heartbeat_unit(ga, vmid, instance_id)
        raise unsupported_os_family_error(persona, instance_id)
