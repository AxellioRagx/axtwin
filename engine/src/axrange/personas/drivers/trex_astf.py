"""The `trex-astf` driver: one TRex ASTF generator stands in for a crowd of
similar hosts (`catalog/twins/trex-test/README.md`, Phase A of the crowd work).

Pinned against TRex **v3.08** (upstream `cisco-system-traffic-generator/
trex-core`, tag `v3.0.8`, 2025-11-05; tarball `trex-tgn.cisco.com/trex/release/
v3.08.tar.gz`, sha256 `d2ac7362...4972b5f`), baked into the `trex-astf` golden
template under `/opt/trex/current`. Every TRex name used here was read out of
that release's `trex.astf.api` (`trex_astf_profile.py`, `trex_astf_client.py`,
`common/trex_client.py`) and `t-rex-64 --help`, not remembered.

Two sides, one persona each, both `drive: trex-astf`:

  * **server** -- `t-rex-64 -i --astf --astf-server-only`, its one real port as
    TRex port 1 (`interfaces: [dummy, <nic>]`).
  * **client** -- `t-rex-64 -i --astf --astf-client-mask 0x1`, its one real
    port as TRex port 0 (`interfaces: [<nic>, dummy]`).

Both run `low_end` (software mode, one core, the NIC via DPDK's af_packet
PMD -- a Linux interface name in `interfaces:` is af_packet in v3.08's
`dpdk_setup_ports.check_vdev`). Hugepages: `low_end` still wants
`20 + 40 * ports` 2 MB pages, which TRex's own setup script configures at
start; the template pre-sets `vm.nr_hugepages` so a small VM never races it.

The daemon is a detached transient unit (LES-086), like every other driver.
A second unit, the **controller** (`render_controller`), connects with the
ASTF Python client bundled in the release, builds the profile from this
persona's knobs (`build_astf_profile`), loads and starts it, then owns the
cadence: it applies the schedule (`mult_at`) with `ASTFClient.update(mult)`
and writes per-template stats to `stats.json`, which `heartbeat` returns.
The schedule runs in the guest because the traffic tier's `drive-persona`
op runs once at apply and the API process holds no per-persona loop; there is
no cron. `mult_at` and `build_astf_profile` are embedded in the controller
verbatim (`inspect.getsource`), so the engine tests exercise the exact code
the guest runs.

Persona knobs (twin-local personas, existing schema -- `Persona.knobs`):

    side: client | server
    templates:                      # client side; the server side may say
      - { name: http, pcap: avl/delay_10_http_browsing_0.pcap, cps: 20 }
      - { name: smb, port: 445, cps: 5,
          program: [ { send: "<hex>" }, { recv: 120 }, ... ] }
    crowd:   { range: 10.250.1.0/24, count: 50 }   # emulated clients
    servers: { range: 10.250.2.0/28 }              # default: the peer's IP
    peer_role: trex-server                         # whose IP is the default
    mult: 1.0                                      # base multiplier
    schedule: [ { at: 5m, mult: 2 }, { every: 10m, mult: [1, 0.5] },
                { daily: "14:00", mult: 3 }, { diurnal: {...} } ]
    profile_from: <client persona id>              # server: share templates
    vlan: <int>                                    # tagged leg (set_vlan)

The emulated ranges are NOT on-link: the twin's router carries a static route
per range to the generator's port IP (a router `install: script` recipe --
`catalog/twins/trex-test/scripts/serve-crowd-routes.sh`). Per-client ARP for
an on-link range (TRex EMU) is an open item, not built.
"""
from __future__ import annotations

import inspect
import ipaddress
import json
import math
import re
import shlex
from typing import Any

from axrange.config_exec.render import systemd_daemon_start_block
from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    instance_paths,
    instance_unit,
    safe_label,
    unsupported_os_family_error,
)
from axrange.personas.schema import Persona

TREX_VERSION = "v3.08"
TREX_HOME = "/opt/trex/current"
#: The bundled client package root inside the release (the `trex` package).
TREX_CLIENT_PATH = f"{TREX_HOME}/automation/trex_control_plane/interactive"
SIDES = ("client", "server")
#: Port 0 is the client side of a dual-port pair, port 1 the server side.
_CLIENT_PORT, _SERVER_PORT = 0, 1
#: Seconds between controller stats samples (and schedule evaluations).
STATS_INTERVAL_S = 10


# ---------------------------------------------------------------------------
# Pure renderers (engine-tested)
# ---------------------------------------------------------------------------
def render_trex_cfg(*, side: str, iface: str, ip: str, gateway: str) -> str:
    """`/etc/trex_cfg.yaml`-shaped config for one side: `low_end` software
    mode, the real NIC (af_packet) on the side's port and `dummy` on the other,
    the port's IP and default gateway from the node's own leg. The dummy port
    gets placeholder link-local addresses (TRex wants a `port_info` entry per
    interface; a dummy never transmits)."""
    if side not in SIDES:
        raise PersonaDriverError(f"trex side must be one of {SIDES}, got {side!r}")
    for what, v in (("ip", ip), ("gateway", gateway)):
        try:
            ipaddress.IPv4Address(v)
        except ValueError as e:
            raise PersonaDriverError(f"trex cfg: {what} {v!r} is not an IPv4 address") from e
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", iface or ""):
        raise PersonaDriverError(f"trex cfg: {iface!r} is not a Linux interface name")
    real = {"ip": ip, "default_gw": gateway}
    dummy = {"ip": "169.254.0.1", "default_gw": "169.254.0.2"}
    ports = [real, dummy] if side == "client" else [dummy, real]
    ifaces = [iface, "dummy"] if side == "client" else ["dummy", iface]
    lines = [
        f"# axrange trex-astf ({TREX_VERSION}), side={side}: rendered by the driver, do not edit",
        "- version: 2",
        "  port_limit: 2",
        "  low_end: true",
        "  low_end_core: 0",
        f"  interfaces: [{', '.join(json.dumps(i) for i in ifaces)}]",
        "  port_info:",
    ]
    for p in ports:
        lines += [f"    - ip: {p['ip']}", f"      default_gw: {p['default_gw']}"]
    return "\n".join(lines) + "\n"


_DUR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h|d)?\s*$")
_HHMM = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def _seconds(raw: Any, *, context: str) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if raw < 0:
            raise PersonaDriverError(f"{context}: duration must be >= 0, got {raw!r}")
        return int(raw)
    m = _DUR.match(str(raw))
    if not m:
        raise PersonaDriverError(f"{context}: {raw!r} is not a duration (e.g. 90s, 5m, 2h)")
    return int(float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2) or "s"])


def _sec_of_day(raw: Any, *, context: str) -> int:
    m = _HHMM.match(str(raw))
    if not m:
        raise PersonaDriverError(f"{context}: {raw!r} is not a UTC HH:MM time of day")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60


def _mult(raw: Any, *, context: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError) as e:
        raise PersonaDriverError(f"{context}: mult {raw!r} is not a number") from e
    if not (v >= 0 and math.isfinite(v)):
        raise PersonaDriverError(f"{context}: mult must be a finite number >= 0, got {raw!r}")
    return v


def diurnal_steps(*, peak: float, trough: float, peak_at: str = "14:00",
                  step: Any = "1h") -> list[dict[str, Any]]:
    """The diurnal shape helper: one `daily` step per `step` across 24 h, a
    cosine between `trough` and `peak` that tops out at `peak_at` (UTC)."""
    step_s = _seconds(step, context="diurnal.step")
    if step_s <= 0 or 86400 % step_s:
        raise PersonaDriverError(f"diurnal.step must divide 24h evenly, got {step!r}")
    top = _sec_of_day(peak_at, context="diurnal.peak_at")
    lo, hi = _mult(trough, context="diurnal.trough"), _mult(peak, context="diurnal.peak")
    out = []
    for t in range(0, 86400, step_s):
        phase = 2 * math.pi * ((t - top) % 86400) / 86400
        out.append({"kind": "daily", "at_s": t, "mult": round(lo + (hi - lo) * (1 + math.cos(phase)) / 2, 4)})
    return out


def normalize_schedule(raw: Any) -> list[dict[str, Any]]:
    """Persona `schedule:` -> canonical steps the controller evaluates:

      * `{at: 5m, mult: 2}`            -> `{kind: offset, at_s, mult}` (once, from start)
      * `{daily: "14:00", mult: 3}`    -> `{kind: daily, at_s, mult}` (every day, UTC)
      * `{every: 10m, mult: [1, .5]}`  -> `{kind: cycle, every_s, mults}` (round robin)
      * `{diurnal: {peak, trough, peak_at, step}}` -> `daily` steps (`diurnal_steps`)

    An empty or absent schedule is `[]` (the base multiplier, flat)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PersonaDriverError(f"schedule must be a list, got {raw!r}")
    out: list[dict[str, Any]] = []
    for i, s in enumerate(raw):
        ctx = f"schedule[{i}]"
        if not isinstance(s, dict):
            raise PersonaDriverError(f"{ctx} must be a mapping, got {s!r}")
        keys = {"at", "daily", "every", "diurnal"} & set(s)
        if len(keys) != 1:
            raise PersonaDriverError(f"{ctx} needs exactly one of at / daily / every / diurnal")
        if "diurnal" in s:
            d = s["diurnal"]
            if not isinstance(d, dict) or "peak" not in d or "trough" not in d:
                raise PersonaDriverError(f"{ctx}.diurnal needs peak and trough")
            out += diurnal_steps(peak=d["peak"], trough=d["trough"],
                                 peak_at=d.get("peak_at", "14:00"), step=d.get("step", "1h"))
            continue
        if "mult" not in s:
            raise PersonaDriverError(f"{ctx} needs mult")
        if "every" in s:
            every = _seconds(s["every"], context=f"{ctx}.every")
            mults = s["mult"] if isinstance(s["mult"], list) else [s["mult"]]
            if every <= 0 or not mults:
                raise PersonaDriverError(f"{ctx}: every must be > 0 and mult non-empty")
            out.append({"kind": "cycle", "every_s": every,
                        "mults": [_mult(m, context=ctx) for m in mults]})
        elif "at" in s:
            out.append({"kind": "offset", "at_s": _seconds(s["at"], context=f"{ctx}.at"),
                        "mult": _mult(s["mult"], context=ctx)})
        else:
            out.append({"kind": "daily", "at_s": _sec_of_day(s["daily"], context=f"{ctx}.daily"),
                        "mult": _mult(s["mult"], context=ctx)})
    return out


def mult_at(steps, base, elapsed_s, utc_sec_of_day):
    """The multiplier in force `elapsed_s` after start at UTC second-of-day
    `utc_sec_of_day`. Each step that applies yields a value; a later step in
    the list overrides an earlier one (author coarse first, fine after).
    `daily` steps: the latest one at or before now, wrapping to yesterday's
    last. Runs IN THE GUEST unchanged (embedded by `render_controller`), so it
    imports nothing and uses only builtins."""
    mult = base
    dailies = sorted((s for s in steps if s["kind"] == "daily"), key=lambda s: s["at_s"])
    for s in steps:
        if s["kind"] == "offset" and elapsed_s >= s["at_s"]:
            mult = s["mult"]
        elif s["kind"] == "cycle":
            mult = s["mults"][int(elapsed_s // s["every_s"]) % len(s["mults"])]
        elif s["kind"] == "daily" and s is dailies[0]:
            # All dailies resolve as one: the latest at or before now.
            past = [d for d in dailies if d["at_s"] <= utc_sec_of_day]
            mult = (past[-1] if past else dailies[-1])["mult"]
    return mult


def _range_hosts(cidr: str, count: Any, *, context: str) -> tuple[str, str]:
    try:
        net = ipaddress.IPv4Network(str(cidr), strict=True)
    except ValueError as e:
        raise PersonaDriverError(f"{context}: {cidr!r} is not an IPv4 network") from e
    hosts = net.num_addresses - 2 if net.prefixlen < 31 else net.num_addresses
    n = hosts if count in (None, "") else int(count)
    if not 1 <= n <= hosts:
        raise PersonaDriverError(f"{context}: count {n} does not fit {cidr} ({hosts} host addresses)")
    first = net.network_address + (1 if net.prefixlen < 31 else 0)
    return str(first), str(first + n - 1)


_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Program step directions: `send` client -> server, `reply` server -> client
#: (exact bytes), `recv` server -> client (that many zero bytes, the original
#: shape-only step).
_STEP_OPS = ("send", "reply", "recv")


def _hex(val: Any, ctx: str) -> str:
    s = str(val).replace(" ", "").lower()
    try:
        bytes.fromhex(s)
    except ValueError as e:
        raise PersonaDriverError(f"{ctx} must be hex") from e
    return s


def _program_step(st: Any, ctx: str) -> dict[str, Any]:
    """One L7 program step. `send`/`reply` carry hex and may name a total
    `size` larger than it, padded by repeating the hex `fill` (TRex's own
    `ASTFProgram.send(buf, size, fill)`): a file body is a header plus N
    bytes of fill, so a 256 KiB read costs ~600 hex characters, not 512 K."""
    if not isinstance(st, dict):
        raise PersonaDriverError(f"{ctx} must be a mapping")
    ops = [k for k in st if k in _STEP_OPS]
    extra = set(st) - set(_STEP_OPS) - {"size", "fill"}
    if len(ops) != 1 or extra:
        raise PersonaDriverError(
            f"{ctx} must be {{send: <hex>}}, {{reply: <hex>}} (each with optional size/fill) "
            f"or {{recv: <bytes>}}")
    op = ops[0]
    if op == "recv":
        if "size" in st or "fill" in st:
            raise PersonaDriverError(f"{ctx}: recv takes no size/fill")
        n = int(st["recv"])
        if n < 1:
            raise PersonaDriverError(f"{ctx}: recv must be >= 1")
        return {"recv": n}
    out: dict[str, Any] = {op: _hex(st[op], f"{ctx}.{op}")}
    blen = len(out[op]) // 2
    if "size" in st:
        size = st["size"]
        if not isinstance(size, int) or size < blen:
            raise PersonaDriverError(f"{ctx}: size must be an int >= the {op} bytes ({blen})")
        if size > blen:
            fill = _hex(st.get("fill") or "", f"{ctx}.fill")
            if not fill:
                raise PersonaDriverError(f"{ctx}: size > the {op} bytes needs a non-empty hex fill")
            out["size"], out["fill"] = size, fill
    elif "fill" in st:
        raise PersonaDriverError(f"{ctx}: fill needs size")
    if blen == 0 and "size" not in out:
        raise PersonaDriverError(f"{ctx}: {op} is empty")
    return out


def profile_spec(knobs: dict[str, Any], *, peers: dict[str, str] | None = None,
                 shared: dict[str, Any] | None = None) -> dict[str, Any]:
    """The persona knobs -> the JSON spec `build_astf_profile` consumes, every
    authoring error raised here (before anything reaches the guest).

    `shared` is the `profile_from` persona's knobs (a server persona reusing
    its client's templates and ranges, so both sides load the same profile, as
    ASTF's split mode needs)."""
    # The persona's own knobs win over the shared ones, key by key.
    k = {**(shared or {}), **knobs}
    side = str(knobs.get("side") or "")
    if side not in SIDES:
        raise PersonaDriverError(f"knob side must be one of {SIDES}, got {side!r}")
    templates = k.get("templates")
    if not isinstance(templates, list) or not templates:
        raise PersonaDriverError("knob templates must be a non-empty list")
    crowd = k.get("crowd") or {}
    if not isinstance(crowd, dict) or "range" not in crowd:
        raise PersonaDriverError("knob crowd needs range (the emulated client prefix)")
    c0, c1 = _range_hosts(crowd["range"], crowd.get("count"), context="crowd")
    servers = k.get("servers") or {}
    if servers.get("range"):
        s0, s1 = _range_hosts(servers["range"], servers.get("count"), context="servers")
    else:
        role = str(k.get("peer_role") or "")
        ip = (peers or {}).get(role)
        if not ip:
            raise PersonaDriverError(
                "the server range is the peer's address by default, but "
                f"peer_role {role!r} resolved to no IP; set servers.range or peer_role"
            )
        s0 = s1 = ip
    out_t = []
    seen: set[str] = set()
    for i, t in enumerate(templates):
        ctx = f"templates[{i}]"
        if not isinstance(t, dict):
            raise PersonaDriverError(f"{ctx} must be a mapping")
        name = str(t.get("name") or "")
        if not _NAME.match(name) or name in seen:
            raise PersonaDriverError(f"{ctx}: name {name!r} must be unique, [a-z0-9_-], <= 32 chars")
        seen.add(name)
        try:
            cps = float(t.get("cps"))
        except (TypeError, ValueError) as e:
            raise PersonaDriverError(f"{ctx}: cps is required and numeric") from e
        if cps < 0.5:
            raise PersonaDriverError(f"{ctx}: cps must be >= 0.5 (TRex's minimum), got {cps}")
        port = t.get("port")
        if port is not None and not (isinstance(port, int) and 0 < port < 65536):
            raise PersonaDriverError(f"{ctx}: port must be 1-65535")
        if ("pcap" in t) == ("program" in t):
            raise PersonaDriverError(f"{ctx} needs exactly one of pcap / program")
        entry: dict[str, Any] = {"name": name, "cps": cps, "port": port}
        # A template may name its own server range (an internal file server
        # vs. external web servers behind a different generator); unset, it
        # uses the profile-wide one. Routing decides which generator answers.
        t_servers = t.get("servers")
        if t_servers is not None:
            if not isinstance(t_servers, dict) or not t_servers.get("range"):
                raise PersonaDriverError(f"{ctx}.servers needs range")
            entry["server_range"] = list(_range_hosts(
                t_servers["range"], t_servers.get("count"), context=f"{ctx}.servers"))
        if "pcap" in t:
            pcap = str(t["pcap"])
            if ".." in pcap.split("/") or not pcap.endswith((".pcap", ".pcapng")):
                raise PersonaDriverError(f"{ctx}: pcap {pcap!r} must be a .pcap path without '..'")
            entry["pcap"] = pcap
        else:
            if port is None:
                raise PersonaDriverError(f"{ctx}: an L7 program needs port")
            steps = t["program"]
            if not isinstance(steps, list) or not steps:
                raise PersonaDriverError(f"{ctx}.program must be a non-empty list")
            entry["program"] = [_program_step(st, f"{ctx}.program[{j}]") for j, st in enumerate(steps)]
        out_t.append(entry)
    vlan = k.get("vlan")
    if vlan is not None and not (isinstance(vlan, int) and 1 <= vlan <= 4094):
        raise PersonaDriverError(f"knob vlan must be 1-4094, got {vlan!r}")
    return {
        "side": side,
        "client_range": [c0, c1],
        "server_range": [s0, s1],
        "templates": out_t,
        "mult": _mult(k.get("mult", 1.0), context="mult"),
        "schedule": normalize_schedule(k.get("schedule")),
        "vlan": vlan,
    }


def build_astf_profile(spec, api, base_dir):
    """Build the `ASTFProfile` from `spec` with `api` (the `trex.astf.api`
    module, or a test fake). Runs IN THE GUEST unchanged. One template per
    spec entry, each its own template group (`tg_name`) so stats are per
    template. A pcap template is `ASTFCapInfo` (TCP or UDP, TRex reads the
    flow off the capture); a program template is a client/server
    `ASTFProgram` pair: the client's sends are the server's recvs, mirrored."""
    import copy
    import os

    # TRex v3.08 bug: the template scheduler's weighted pick
    # (`KxuNuRand::init`, called from `CAstfTemplatesRW::init_scheduler`)
    # aborts the daemon (`assert aIdx < p.size()`) for some EXACT cps ratios --
    # trex-test's 20/40/10/5 (4:8:2:1) at any scale, found live on dev45 and
    # reproduced with the release's own astf-sim, 2026-11-16. A proportional
    # nudge still fails; a distinct ~1e-6 relative nudge per template does not
    # (and changed the outcome of no other mix tried). The rate effect is
    # under 1e-5 relative.
    spec = copy.deepcopy(spec)
    for i, t in enumerate(spec["templates"]):
        t["cps"] = t["cps"] * (1 + 1e-6 * (i + 1))

    gens = {}

    def ip_gen(server_range):
        # One generator per distinct server range (templates sharing a range
        # share its pool, as every template did before per-template ranges).
        key = tuple(server_range)
        if key not in gens:
            gens[key] = api.ASTFIPGen(
                dist_client=api.ASTFIPGenDist(ip_range=list(spec["client_range"]), distribution="seq"),
                dist_server=api.ASTFIPGenDist(ip_range=list(server_range), distribution="seq"),
                glob=api.ASTFIPGenGlobal(ip_offset="1.0.0.0"),
            )
        return gens[key]

    def tx(prog, st, op):
        # send `op`'s bytes, padded to `size` with `fill` when given; returns
        # the byte count the peer must wait for.
        data = bytes.fromhex(st[op])
        size = int(st.get("size") or 0)
        if size > len(data):
            prog.send(data, size=size, fill=bytes.fromhex(st["fill"]))
            return size
        prog.send(data)
        return len(data)

    gen = ip_gen(spec["server_range"])
    caps, templates = [], []
    for t in spec["templates"]:
        t_gen = ip_gen(t["server_range"]) if t.get("server_range") else gen
        if "pcap" in t:
            path = t["pcap"] if t["pcap"].startswith("/") else os.path.join(base_dir, t["pcap"])
            kw = {"file": path, "cps": t["cps"], "tg_name": t["name"]}
            if t.get("port"):
                kw["port"] = t["port"]
            if t_gen is not gen:
                kw["ip_gen"] = t_gen
            caps.append(api.ASTFCapInfo(**kw))
            continue
        prog_c, prog_s = api.ASTFProgram(side="c"), api.ASTFProgram(side="s")
        for st in t["program"]:
            if "send" in st:
                prog_s.recv(tx(prog_c, st, "send"))
            elif "reply" in st:
                prog_c.recv(tx(prog_s, st, "reply"))
            else:
                n = int(st["recv"])
                prog_c.recv(n)
                prog_s.send(b"\x00" * n)
        client = api.ASTFTCPClientTemplate(program=prog_c, ip_gen=t_gen, port=t["port"], cps=t["cps"])
        # A template with its own server range is associated by port AND that
        # range: two templates on one port (two SMB sessions, one per file
        # server) are then distinct to the server side, which ASTF requires.
        rule = {"port": t["port"]}
        if t.get("server_range"):
            rule.update(ip_start=t["server_range"][0], ip_end=t["server_range"][1])
        server = api.ASTFTCPServerTemplate(program=prog_s, assoc=api.ASTFAssociationRule(**rule))
        templates.append(api.ASTFTemplate(client_template=client, server_template=server, tg_name=t["name"]))
    kw = {"default_ip_gen": gen}
    if caps:
        kw["cap_list"] = caps
    if templates:
        kw["templates"] = templates
    return api.ASTFProfile(**kw)


def render_controller() -> str:
    """The in-guest controller (python3, the release's bundled client on
    `sys.path`). Usage: `controller.py <spec.json> <state-dir>`. Waits for the
    daemon's RPC, acquires, applies VLAN (if any), loads the profile, starts
    it (the client side with `client_mask` = its one port), then every
    `STATS_INTERVAL_S` re-evaluates `mult_at` (calling `update` on a change)
    and writes `stats.json` atomically: the multiplier in force, and per
    template group the raw counters plus cps / established-per-second over
    the last sample and over a rolling 5-minute window."""
    return (
        "#!/usr/bin/env python3\n"
        f"# axrange trex-astf controller ({TREX_VERSION}): rendered by the driver, do not edit.\n"
        "import json, os, sys, time, collections\n"
        f"sys.path.insert(0, {TREX_CLIENT_PATH!r})\n"
        "from trex.astf import api\n\n"
        + inspect.getsource(mult_at) + "\n\n"
        + inspect.getsource(build_astf_profile) + "\n\n"
        + f"STATS_INTERVAL_S = {STATS_INTERVAL_S}\n"
        f"CLIENT_PORT, SERVER_PORT = {_CLIENT_PORT}, {_SERVER_PORT}\n"
        f"BASE_DIR = {TREX_HOME!r}\n"
        + _CONTROLLER_MAIN
    )


_CONTROLLER_MAIN = r'''
def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg, flush=True)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def counters(tg):
    """One template group's counters, flattened to the few we report."""
    c, s = tg.get("client", {}) or {}, tg.get("server", {}) or {}
    return {
        "connects": c.get("tcps_connects", 0) + c.get("udps_connects", 0),
        "attempts": c.get("tcps_connattempt", 0) + c.get("udps_connects", 0),
        "accepts": s.get("tcps_accepts", 0) + s.get("udps_accepts", 0),
        "drops": c.get("tcps_drops", 0) + s.get("tcps_drops", 0) + c.get("udps_keepdrops", 0) + s.get("udps_keepdrops", 0),
        "bytes_tx": c.get("tcps_sndbyte", 0) + s.get("tcps_sndbyte", 0) + c.get("udps_sndbyte", 0) + s.get("udps_sndbyte", 0),
        "bytes_rx": c.get("tcps_rcvbyte", 0) + s.get("tcps_rcvbyte", 0) + c.get("udps_rcvbyte", 0) + s.get("udps_rcvbyte", 0),
    }


def main(spec_path, state_dir):
    spec = json.load(open(spec_path))
    side = spec["side"]
    stats_path = os.path.join(state_dir, "stats.json")
    c = api.ASTFClient(server="127.0.0.1")
    deadline = time.time() + 180
    while True:
        try:
            c.connect()
            break
        except Exception as e:
            if time.time() > deadline:
                log(f"FATAL: TRex RPC never answered: {e}")
                sys.exit(2)
            time.sleep(3)
    c.reset()
    port = CLIENT_PORT if side == "client" else SERVER_PORT
    if spec.get("vlan"):
        c.set_vlan(ports=[port], vlan=spec["vlan"])
    c.load_profile(build_astf_profile(spec, api, BASE_DIR))
    started = time.time()
    mult = mult_at(spec["schedule"], spec["mult"], 0, int(started % 86400))
    if side == "client":
        c.start(mult=mult, client_mask=1 << port)
    else:
        c.start(mult=mult)
    log(f"started side={side} mult={mult} templates={[t['name'] for t in spec['templates']]}")
    window = collections.deque()
    prev = None
    while True:
        time.sleep(STATS_INTERVAL_S)
        now = time.time()
        want = mult_at(spec["schedule"], spec["mult"], now - started, int(now % 86400))
        if want != mult and side == "client":
            c.update(mult=want)
            log(f"mult {mult} -> {want}")
            mult = want
        names = c.get_tg_names()
        raw = c.get_traffic_tg_stats(names, skip_zero=False) if names else {}
        cur = {n: counters(raw.get(n, {})) for n in names}
        window.append((now, cur))
        while window and now - window[0][0] > 300:
            window.popleft()
        out = {"side": side, "mult": mult, "at": now, "uptime_s": round(now - started, 1), "templates": {}}
        for n, v in cur.items():
            rec = dict(v)
            if prev is not None and n in prev[1]:
                dt = now - prev[0]
                rec["cps"] = round((v["connects"] - prev[1][n]["connects"]) / dt, 2)
                rec["accept_ps"] = round((v["accepts"] - prev[1][n]["accepts"]) / dt, 2)
            t0, first = window[0]
            if now > t0 and n in first:
                rec["cps_5m"] = round((v["connects"] - first[n]["connects"]) / (now - t0), 2)
                rec["accept_ps_5m"] = round((v["accepts"] - first[n]["accepts"]) / (now - t0), 2)
                rec["window_s"] = round(now - t0, 1)
            out["templates"][n] = rec
        write_json(stats_path, out)
        prev = (now, cur)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
'''


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------
def daemon_unit(instance_id: str) -> str:
    """The TRex daemon's unit; the controller takes the instance's own unit
    name (`instance_unit`), so the shared `stop_unit`/`heartbeat_unit`
    conventions keep naming the thing that owns the traffic."""
    return f"{instance_unit(instance_id)}-trex"


def render_launch(instance_id: str, persona: Persona, *, side: str, iface: str,
                  ip: str, prefix: int, gateway: str) -> str:
    """The bash bootstrap: fail loud without the baked TRex, hand the NIC to
    TRex (flush the kernel's address so only one stack answers ARP for it),
    start the daemon unit, then the controller unit."""
    paths = instance_paths(instance_id)
    d = paths["dir"]
    flag = "--astf-server-only" if side == "server" else f"--astf-client-mask {1 << _CLIENT_PORT:#x}"
    daemon_cmd = (
        f"cd {TREX_HOME} && exec ./t-rex-64 -i --astf {flag} --cfg {shlex.quote(d + '/trex_cfg.yaml')} "
        "--no-termio --iom 0"
    )
    ctl_cmd = f"exec python3 {shlex.quote(d + '/controller.py')} {shlex.quote(d + '/spec.json')} {shlex.quote(d)}"
    who = safe_label(f"persona {instance_id} ({persona.id})")
    return (
        "#!/bin/bash\nset -u\n"
        f"if [ ! -x {TREX_HOME}/t-rex-64 ]; then\n"
        f'  echo "[axrange] FATAL: TRex not baked at {TREX_HOME} for {who}; use the trex-astf template" >&2\n'
        "  exit 1\nfi\n"
        f"systemctl stop {instance_unit(instance_id)} {daemon_unit(instance_id)} 2>/dev/null || true\n"
        f"ip -4 addr flush dev {shlex.quote(iface)}\n"
        f"ip link set {shlex.quote(iface)} up\n"
        + systemd_daemon_start_block(daemon_unit(instance_id), daemon_cmd,
                                     log_path=f"{d}/trex.log", label=f"{who} trex daemon", settle_s=8)
        + systemd_daemon_start_block(paths["unit"], ctl_cmd,
                                     log_path=paths["log"], label=f"{who} trex controller", settle_s=3)
    )


def render_restore(iface: str, ip: str, prefix: int, gateway: str) -> str:
    """Give the NIC back to the kernel after the units stop (the address and
    default route `start` flushed)."""
    q = shlex.quote
    return (
        f"ip -4 addr replace {q(ip)}/{int(prefix)} dev {q(iface)} 2>/dev/null || true; "
        f"ip -4 route replace default via {q(gateway)} dev {q(iface)} 2>/dev/null || true"
    )


def parse_guest_net(addr_json: str, route_json: str) -> dict[str, Any]:
    """`ip -j -4 addr` + `ip -j -4 route show default` -> the leg TRex takes:
    the default route's device, its global IPv4 and prefix, the gateway."""
    try:
        addrs, routes = json.loads(addr_json or "[]"), json.loads(route_json or "[]")
    except ValueError as e:
        raise PersonaDriverError(f"trex: unreadable `ip -j` output: {e}") from e
    if not routes or not routes[0].get("gateway") or not routes[0].get("dev"):
        raise PersonaDriverError("trex: the generator has no IPv4 default route to take its gateway from")
    dev, gw = routes[0]["dev"], routes[0]["gateway"]
    for a in addrs:
        if a.get("ifname") != dev:
            continue
        for info in a.get("addr_info") or []:
            if info.get("family") == "inet" and info.get("scope") == "global":
                return {"iface": dev, "ip": info["local"], "prefix": int(info["prefixlen"]), "gateway": gw}
    raise PersonaDriverError(f"trex: {dev} (the default route's device) has no global IPv4 address")


class TrexAstfDriver(PersonaDriver):
    """Launches one TRex ASTF side plus its controller (module docstring)."""

    def _spec(self, persona: Persona, peers: dict[str, str] | None) -> dict[str, Any]:
        shared = None
        src = persona.knobs.get("profile_from")
        if src:
            from axrange.personas import registry

            other = registry.get(str(src))
            if other is None:
                raise PersonaDriverError(f"persona {persona.id}: profile_from {src!r} is not a known persona")
            shared = other.knobs
        return profile_spec(persona.knobs, peers=peers, shared=shared)

    def start(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str,
              peers: dict[str, str] | None = None, seed: Any = None, **kwargs: Any) -> dict[str, Any]:
        if persona.os_family != "linux":
            raise unsupported_os_family_error(persona, instance_id)
        spec = self._spec(persona, peers)  # every authoring error before the guest is touched
        paths = instance_paths(instance_id)
        d = paths["dir"]
        ga.exec(vmid, ["mkdir", "-p", d], timeout_s=30)
        # The leg TRex takes, read once and kept: a re-start after the flush
        # finds no address on the NIC, so it reuses what the first start saw.
        net_path = f"{d}/net.json"
        cached = ga.exec(vmid, ["cat", net_path], timeout_s=30)
        if cached.ok and cached.stdout.strip():
            net = json.loads(cached.stdout)
        else:
            a = ga.exec(vmid, ["ip", "-j", "-4", "addr"], timeout_s=30)
            r = ga.exec(vmid, ["ip", "-j", "-4", "route", "show", "default"], timeout_s=30)
            if not (a.ok and r.ok):
                raise PersonaDriverError(f"persona[{instance_id}]: could not read the guest's addressing")
            net = parse_guest_net(a.stdout, r.stdout)
            ga.write_file(vmid, net_path, json.dumps(net))
        ga.write_file(vmid, f"{d}/trex_cfg.yaml",
                      render_trex_cfg(side=spec["side"], iface=net["iface"], ip=net["ip"], gateway=net["gateway"]))
        ga.write_file(vmid, f"{d}/spec.json", json.dumps(spec, indent=1))
        ga.write_file(vmid, f"{d}/controller.py", render_controller(), mode="0755")
        launch = f"{d}/launch.sh"
        ga.write_file(vmid, launch, render_launch(instance_id, persona, side=spec["side"], **net), mode="0755")
        res = ga.exec(vmid, ["bash", launch], timeout_s=120)
        if not res.ok:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): trex launch failed (exit {res.exit_code}) "
                f"in vmid={vmid}: {(res.stdout or res.stderr)[-400:]}"
            )
        return {"started": True, "unit": paths["unit"], "side": spec["side"]}

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        paths = instance_paths(instance_id)
        units = f"{paths['unit']} {daemon_unit(instance_id)}"
        cmd = f"systemctl stop {units} 2>/dev/null || true; systemctl reset-failed {units} 2>/dev/null || true"
        ga.exec(vmid, ["sh", "-c", cmd], timeout_s=60)
        cached = ga.exec(vmid, ["cat", f"{paths['dir']}/net.json"], timeout_s=30)
        if cached.ok and cached.stdout.strip():
            net = json.loads(cached.stdout)
            ga.exec(vmid, ["sh", "-c", render_restore(net["iface"], net["ip"], net["prefix"], net["gateway"])], timeout_s=30)
        return {"stopped": True, "unit": paths["unit"]}

    def heartbeat(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        paths = instance_paths(instance_id)
        units = [paths["unit"], daemon_unit(instance_id)]
        alive = all(ga.exec(vmid, ["systemctl", "is-active", "--quiet", u], timeout_s=30).ok for u in units)
        out: dict[str, Any] = {"alive": alive, "unit": paths["unit"]}
        res = ga.exec(vmid, ["cat", f"{paths['dir']}/stats.json"], timeout_s=30)
        if res.ok and res.stdout.strip():
            try:
                out["stats"] = json.loads(res.stdout)
            except ValueError:
                out["note"] = "stats.json unreadable"
        elif alive:
            out["note"] = "no stats yet"
        return out
