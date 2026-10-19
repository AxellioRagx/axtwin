"""The **Windows half of `drive: command`** (docs/WORK_ORDER_protocol_engines.md
§5 E6): the per-proto template table a real, domain-JOINED Windows member runs
with **built-in tools only** — `klist` / `gpupdate` / `nltest` / `net use` +
`Copy-Item` / `Get-WmiObject` / `w32tm` / `Resolve-DnsName` /
`Invoke-WebRequest` — so a capture records Kerberos, LDAP, SMB, RPC (including
the **dynamic high ports** behind the cast's unlabelled ~6%), DNS, NTP and
HTTP/HTTPS produced by the operating system itself rather than filler bytes at
a port.

**No new `drive:` kind** (work order §4, deliberately). `drive: command`
already means *"run a real protocol client"*; the only reason
`command.py` refused `os_family: windows` was that nobody had written the
PowerShell table — its own docstring says exactly that ("a Windows `command`
client would need a second, independently-designed per-proto template table in
PowerShell"). This module IS that table. Extending the existing kind costs
**zero version-locked contract edits**: `DRIVE_KINDS` is unchanged, PSN-030's
enum is unchanged, `VALIDATOR.md`'s rule count and text are unchanged, and
`_DRIVERS` gains no entry. `CommandDriver`'s three `os_family == "windows"`
branches stop raising and dispatch here instead — that is the whole wiring
change.

**Why a separate module rather than more of `command.py`.** The two tables
share no argv vocabulary at all (a POSIX argv list vs. a PowerShell
cmdlet/exe step list), and `command.py` is already 45 KB. The split is by
OS, exactly the way `scripted.py` keeps `render_scripted_client` and
`render_scripted_client_windows` side by side. This module imports nothing
from `command.py` (which imports THIS one) — a one-directional edge, no
cycle (LES-025).

---

**Containment — INVERTED relative to the Linux table, on purpose.**
`command.py`'s check 2 refuses any argv whose address is a hostname. A domain
member cannot work that way and there is no version of this engine in which
it could:

* a Kerberos ticket is issued against an **SPN** (`HOST/dc.corp.example`),
  never against an address — `klist get 10.0.0.5` is not a thing;
* Group Policy, the DC locator and SMB session setup all run over
  **name-based** discovery (SRV records, CLDAP pings, `\\\\server\\share`);
* a member's resolver **is the DC** by construction (work order §5 E3), so
  guest-side resolution is the behaviour under test, not a leak.

So this module asserts the OPPOSITE of the Linux check: every load-bearing
target must be a **NAME and never an IP literal**
(`_check_windows_targets`), because an IP literal there silently produces a
*different, wrong* exchange — NTLM instead of Kerberos, no SNI on the TLS
handshake, a DC-locator query that never happens.

**What enforces containment instead** (ruling 1, 2026-09-21 — containment
becomes a NETWORK property, not a render-time argv check):

1. the **router denies WAN egress** during the measurement phase (work order
   §5 E1, fail-safe default-CLOSED), so a resolved name cannot leave the twin;
2. **only the twin's own DNS answers** — member → DC (AD-integrated DNS) →
   forwarder = twin resolver → sink (§5 E3), so no name resolves to a real
   internet address in the first place;
3. the **any-IP sink** (§5 E2) terminates whatever does get dialled.

A twin without that triangle in place must not run this engine against
external names; `knobs.web_names` is where that risk concentrates, and the
arm-time note says so in the guest log.

---

**Every row must put bytes on the WIRE, never read a cache** (LES-001, the
`--origin-to-force-quic-on` lesson from the browser slice: a client that only
speaks a protocol under a condition must be forced into it or refuse). Windows
caches aggressively — tickets for 10 hours, DC locator results, DNS answers,
Group Policy version stamps — so a naive invocation of most of these tools
emits **nothing at all** while exiting 0, which is the exact shape of a
persona that looks healthy and produces no traffic. Each row carries a
`forces_wire` string naming its lever, repeated as a comment on the row:

| proto | command | on the wire | the anti-cache lever |
|---|---|---|---|
| `kerberos` | `klist purge` → `klist get <SPN>` | AS-REQ/TGS-REQ, 88 | **`klist purge` first** — a bare `klist` prints the cache and emits nothing; `klist get` against an already-cached SPN also emits nothing |
| `ldap` | `gpupdate /force /wait:N` | LDAP 389 + SMB 445 (SYSVOL) + RPC 135 + Kerberos 88 | **`/force`** — a plain `gpupdate` compares GPO version stamps and does nothing when unchanged |
| `dns` | `Resolve-DnsName -Server <dc>` → `nltest /dsgetdc:<domain> /force` | DNS 53 (A) + DNS SRV + CLDAP 389/udp | **`-Server` bypasses the DNS client cache**; **`/force`** makes nltest ignore the cached DC |
| `smb` | `net use \\\\fs\\share` → `Copy-Item` (sized file) → cleanup | SMB2 session setup (Kerberos) + a real N-byte write | **`/delete` at the end** so the next fire re-negotiates; a WRITE is never served from a cache |
| `rpc` | `Get-WmiObject -ComputerName <dc>` | RPC EPM 135 **+ a dynamic high port** (49152-65535) | **`-ComputerName`** — without it the query is local and emits nothing; and `Get-WmiObject` is **DCOM**, where `Get-CimInstance` would be WSMan on 5985 |
| `ntp` | `w32tm /resync /rediscover` | SNTP 123/udp | **`/rediscover`** — a plain `/resync` can be satisfied without contacting a source |
| `http`/`https` | `Invoke-WebRequest -DisableKeepAlive` | TCP 80 / TLS 443 **with the real SNI** | **`-DisableKeepAlive`** forces a fresh connection (and therefore a fresh handshake) per fire; the URL host is what becomes the SNI |

**`Get-WmiObject`, not `Get-CimInstance`** (the work order left this open):
`Get-CimInstance -ComputerName` uses **WSMan/WinRM over 5985/tcp** — HTTP, one
fixed port, no endpoint mapper. `Get-WmiObject -ComputerName` uses **DCOM**:
a bind to the RPC endpoint mapper on **135/tcp** followed by the actual call on
a **dynamically-assigned high port**, which is precisely the 49667/49669/49677
signature the corp-apcn cast shows and this engine exists to reproduce (work
order §1). The cost is that `Get-WmiObject` exists only in **Windows
PowerShell 5.1** (PowerShell 7 removed it) — the Scheduled Task launches
`powershell.exe`, so that is what we get; the probe fails loud naming the
alternative (`New-CimSession -SessionOption (New-CimSessionOption -Protocol
Dcom)`) if it is ever missing.

**Kerberos over TCP** (`knobs.force_kerberos_tcp`, default **on**). Windows
picks UDP or TCP for the KDC exchange by `MaxPacketSize`, so a small exchange
can ride 88/**udp** — and the services work order's S3 warning is that the DC
is only re-inferred as a domain controller when something dials **88/tcp AND
389/tcp** at it, from an *accepted* flow. A member that talks to its KDC over
UDP therefore leaves the DC mis-inferred, silently. Setting
`HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa\\Kerberos\\Parameters\\
MaxPacketSize = 1` (a documented Microsoft knob) forces every Kerberos
exchange onto TCP. It runs ONCE per arm as a `Setup` step, is idempotent, and
is announced in the guest log; `knobs.force_kerberos_tcp: false` turns it off
for an operator who would rather observe the default mixture.

**The S3 pair, concretely:** a default member persona must carry **both** a
`kerberos` emit (88/tcp, via `klist get`) and an `ldap` emit (389/tcp, via
`gpupdate /force`) — neither alone closes S3's AND. `S3_LISTEN_PROOF_PROTOS`
names the pair and `dials_at_target()` reports what each row puts on the wire,
so a twin config can be checked rather than hoped at.

**Fail loud, never silent.** `CommandDriver._assert_windows_commands_present`
probes every cmdlet/exe the persona's emits need with `Get-Command` BEFORE the
client is launched and raises `PersonaDriverError` naming what is missing and
**which Windows component provides it** (`nltest.exe` is the one that really
does go missing — it is a Windows Server built-in but an RSAT/optional
component on some client SKUs). The rendered client keeps a SECOND per-step
`Get-Command` guard, so a tool that disappears after start shows up as
`missing_tool=` in the one-line-per-fire summary
(`ran <proto> tool=… runs=… ok=… fails=… timeouts=… missing_tool=…`) instead
of a persona that looks healthy and emits nothing (LES-245).

**Launch** is the existing Windows path, reused verbatim — a detached,
deterministically-named **Scheduled Task**
(`scripted.render_scripted_launch_windows` →
`config_exec.render.scheduled_task_start_block`), never a bare
`Start-Process` child of the QGA exec session (LES-086's Windows analog) — and
`stop`/`heartbeat` are `base.stop_scheduled_task`/`heartbeat_scheduled_task`,
the same ones `ScriptedDriver` uses. Nothing about the launch/stop/heartbeat
mechanism is new here.

**Deterministic**, exactly like every sibling: same persona + peers + seed +
instance_id renders byte-identical PowerShell. The seed shows up in exactly two
places — the literal `$Seed` the guest draws its runtime jitter from, and the
render-time shuffle of `knobs.web_names`.

**Honest limit — this runs as SYSTEM, i.e. as the MACHINE account.** The
launch path is the existing Scheduled Task, registered `-LogonType
ServiceAccount` with no `-User`/`-Password` (`config_exec.render.
scheduled_task_start_block`, because the Windows guest agent is itself
SYSTEM and there is no credential to store). Three consequences, none of them
hidden:

* Kerberos tickets are for the **computer account** (`WS01$`), not a user —
  real traffic, real SPNs, real AS-REQ/TGS-REQ, but a detection rule keyed on
  *user* principals sees a machine principal;
* `gpupdate /force` refreshes **computer** policy (a user policy refresh
  needs an interactive user session), which still drives the full LDAP + SMB
  + RPC + Kerberos cycle at the DC;
* the `smb` row's share must admit the **machine account** (`Domain
  Computers`) or `net use` is refused — which shows up honestly as
  `fails=`/`timeouts=` in the guest log, never as silence.

Running as a real domain user would mean threading a credential into the task
registration, which is a credential-plumbing decision beyond this engine (the
Linux table's `credentials=` kwarg is deliberately NOT forwarded here). Worth
doing if user-principal fidelity turns out to matter; not worth guessing at
now.

**Known operational hazards this engine sits behind** (work order §5 E6,
GAP-051): a domain join takes 1-2 minutes and is effectively serialised, and
the DC's promotion reboot precedes all of it — so a twin carries ONE Windows
client, and this engine's first fire is minutes after `tfc deploy` returns.
"""
from __future__ import annotations

import ipaddress
import random
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from axrange.personas.drivers.base import (
    PersonaDriverError,
    effective_seed,
    instance_paths,
    safe_instance_name,
)
from axrange.personas.drivers.scripted import (
    _BUSINESS_END_MIN,
    _BUSINESS_START_MIN,
    _resolve_emit_schedule,
    render_scripted_launch_windows,
    resolve_emit_peers,
)
from axrange.personas.schema import Emit, Persona

# The default `Resolve-DnsName` query name — the SAME RFC-6761-reserved
# default (and the same `knobs.dns_qname` override) the Linux `command` table
# uses, so one knob means one thing on both OSes.
_DEFAULT_DNS_QNAME = "axrange-probe.invalid."
_QNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# One DNS label: 1-63 chars of [A-Za-z0-9-], not starting or ending with `-`.
# (Same rules as `browser._check_browse_name`. Deliberately not shared: each
# caller's error message has to name its OWN knob, and a shared checker that
# returns a REASON string instead of raising is the obvious refactor once a
# third caller appears — not worth doing from inside this slice.)
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9.-]")
# `service/host[:port][/name]` — an SPN always has a host component; a bare
# service class would be issued against nothing.
_SPN_RE = re.compile(
    r"^[A-Za-z0-9_-]+/[A-Za-z0-9._-]+(:[0-9]{1,5})?(/[A-Za-z0-9._-]+)?$"
)
# An SMB share name: `Sales`, `SYSVOL`, `share$`.
_SHARE_RE = re.compile(r"^[A-Za-z0-9_.$-]{1,80}$")

# `Copy-Item`'s source file is the one Windows template that can genuinely
# SPEND a byte budget (the `ping -s` of this table). Clamped, and the clamping
# is logged, never silently applied.
_SMB_MAX_BYTES = 256 * 1024 * 1024

# `knobs.win_step_timeout_s` bounds (a per-persona override of every row's own
# per-step timeout).
_MIN_STEP_TIMEOUT_S = 2
_MAX_STEP_TIMEOUT_S = 1800

# The same `MISSING:`-tagged probe contract the Linux side uses
# (`command.render_tool_probe`). Duplicated rather than imported to keep this
# module free of an import cycle with `command.py`, which imports THIS module
# to dispatch its Windows branch.
_MISSING_TAG = "MISSING:"
# A self-identifying marker so a test fake (and an operator reading a
# guest-agent log) can tell this probe apart from the Linux tool probe and
# from `ScriptedDriver`'s Windows `New-Item` call.
_WIN_PROBE_MARKER = "axr-persona-wincmd-probe"

# Forcing the KDC exchange onto TCP (see the module docstring — the services
# work order's S3 AND).
_KRB_PARAMS_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\Kerberos\Parameters"

# The proto pair a default domain-member persona must carry for the DC to be
# re-inferred as a domain controller: a listen fact is written only from an
# ACCEPTED flow, and `roles.yaml`'s `domain-controller` row needs 88/tcp AND
# 389/tcp TOGETHER in the census. `kerberos` supplies the 88, `ldap` the 389.
S3_LISTEN_PROOF_PROTOS = ("kerberos", "ldap")


# ---------------------------------------------------------------------------
# The per-proto template table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _WindowsTemplate:
    """One proto's real, built-in Windows client invocation.

    `build(ctx)` returns `(steps, targets, endpoints)`:

    * **steps** — the ordered PowerShell steps for ONE invocation. A step is
      either `{"Kind": "exe", "File": ..., "Args": [...]}` (run as a real
      child process with a hard timeout) or `{"Kind": "cmdlet", "Cmdlet":
      ..., "Params": {...}}` (splatted in-process — a hashtable of DATA, never
      a rendered string handed to `Invoke-Expression`, so no eval surface).
      A step with `"Always": True` is CLEANUP: it runs even when an earlier
      step failed, and its own result is never counted.
    * **targets** — the NAMES this invocation is built for. They must be names
      and never IP literals (`_check_windows_targets`) — see the module
      docstring's containment inversion.
    * **endpoints** — addresses that may legitimately be an IP literal (the
      `Resolve-DnsName -Server` value, which is the orchestrator-resolved DC).

    `uses_port=False` marks a row whose tool has no port argument at all
    (every row but http/https). It refuses an emit pinning some other port
    rather than silently driving the well-known one (LES-001), exactly like
    `command._resolve_ports`.

    `targetless=True` marks the two rows that deliberately address NOTHING —
    `gpupdate` and `w32tm /resync` find the DC and the time source through the
    domain hierarchy, which IS the fidelity point (a real member does not get
    told where its DC is). For those rows the "declares at least one target"
    guard is waived, and the waiver is the row's own declaration rather than a
    silent empty tuple.

    `dials` / `dials_dynamic_high_ports` report what the row puts on the wire
    AT THE TARGET, so a twin config can be checked against the services work
    order's S3 AND instead of hoped at.

    `forces_wire` names the anti-cache lever (LES-001) — the reason this row
    emits packets rather than reading something local. Required on every row.
    """

    commands: tuple[tuple[str, str], ...]
    default_port: int
    uses_port: bool
    spends_bytes: bool
    timeout_s: int
    explicit_transports: frozenset[str]
    dials: tuple[tuple[int, str], ...]
    forces_wire: str
    label: str
    build: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], tuple[str, ...], tuple[str, ...]]]
    # Knob keys this row cannot be rendered without (fail loud, named).
    needs: tuple[str, ...] = ()
    targetless: bool = False
    dials_dynamic_high_ports: bool = False
    # Steps run ONCE at arm time rather than on every fire, plus the one-line
    # explanation the guest log carries for them (a setup step changes guest
    # STATE, so it is never silent).
    setup: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None
    setup_note: str = ""
    # The knob keys whose value supplies this row's per-invocation target(s).
    multi_target_knob: str = ""


def _exe(file: str, args: list[str], *, always: bool = False) -> dict[str, Any]:
    return {"Kind": "exe", "File": file, "Args": list(args), "Always": always}


def _cmdlet(
    name: str, params: dict[str, Any], *, always: bool = False
) -> dict[str, Any]:
    return {"Kind": "cmdlet", "Cmdlet": name, "Params": dict(params), "Always": always}


# --- kerberos ---------------------------------------------------------------
def _build_kerberos(ctx: dict[str, Any]):
    # ANTI-CACHE: `klist purge` destroys this logon session's ticket cache, so
    # the following `klist get <SPN>` MUST go to the KDC (AS-REQ for a TGT,
    # then TGS-REQ for the SPN). Without the purge, a second fire inside the
    # 10-hour ticket lifetime is a pure local cache read that exits 0 and puts
    # NOTHING on the wire — a persona that looks perfectly healthy and
    # produces no Kerberos at all. A bare `klist` (no `get`) is the same trap:
    # it prints the cache and never contacts anything.
    spn = ctx["spn"]
    return (
        [
            _exe("klist.exe", ["purge"]),
            _exe("klist.exe", ["get", spn]),
        ],
        (ctx["spn_host"],),
        (),
    )


def _setup_kerberos(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    # See the module docstring: force the KDC exchange onto TCP so the DC's
    # listen census actually records 88/TCP (services work order S3). Two
    # steps because the `Parameters` subkey does not exist on a stock install.
    if not ctx.get("force_kerberos_tcp"):
        return []
    return [
        _cmdlet(
            "New-Item",
            {"Path": _KRB_PARAMS_KEY, "Force": True, "ErrorAction": "SilentlyContinue"},
        ),
        _cmdlet(
            "Set-ItemProperty",
            {
                "Path": _KRB_PARAMS_KEY,
                "Name": "MaxPacketSize",
                "Value": 1,
                "Type": "DWord",
                "Force": True,
                "ErrorAction": "Stop",
            },
        ),
    ]


# --- ldap (a real Group Policy cycle) ---------------------------------------
def _build_ldap(ctx: dict[str, Any]):
    # ANTI-CACHE: `/force` reapplies EVERY policy instead of comparing GPO
    # version stamps and doing nothing — the common case on a member that has
    # already applied policy once, which would exit 0 having emitted only a
    # version check (or nothing at all).
    #
    # `/wait:` bounds gpupdate's own run so it returns before the step's hard
    # timeout kills it, and keeps the "a policy needs a logoff — log off now?"
    # prompt from parking the process forever on a non-interactive SYSTEM
    # session.
    #
    # TARGETLESS on purpose: a real member DISCOVERS its DC (DNS SRV → CLDAP
    # ping → LDAP bind). Handing gpupdate a DC would be the one thing that
    # makes this traffic unrealistic.
    wait_s = max(30, int(ctx["timeout_s"]) - 30)
    return ([_exe("gpupdate.exe", ["/force", f"/wait:{wait_s}"])], (), ())


# --- dns (resolution + DC locator) ------------------------------------------
def _build_dns(ctx: dict[str, Any]):
    # ANTI-CACHE, step 1: `-Server` sends the query straight at that server,
    # bypassing the Windows DNS client cache — without it, the second fire of
    # an identical query inside the record's TTL is answered locally and emits
    # nothing. `-DnsOnly` keeps it off LLMNR/NetBIOS (which would be a
    # DIFFERENT protocol wearing a `dns` label), `-NoHostsFile` keeps a hosts
    # entry from short-circuiting it, `-QuickTimeout` bounds it.
    #
    # ANTI-CACHE, step 2: `nltest /dsgetdc:<domain> /force` ignores the cached
    # DC and re-runs the full locator — an SRV lookup plus a CLDAP ping on
    # 389/udp. Without `/force` it answers from the cached DC and emits
    # nothing. This is the work order's "DC locator: DNS + LDAP ping" row; it
    # lives under `dns` rather than its own proto token so PSN-040's
    # vocabulary needs no new spelling.
    dns_params: dict[str, Any] = {
        "Name": ctx["qname"],
        "Type": "A",
        "DnsOnly": True,
        "NoHostsFile": True,
        "QuickTimeout": True,
        "ErrorAction": "Stop",
    }
    dns_params["Server"] = ctx["dns_server"]
    return (
        [
            _cmdlet("Resolve-DnsName", dns_params),
            _exe("nltest.exe", [f"/dsgetdc:{ctx['ad_domain']}", "/force"]),
        ],
        (ctx["qname"], ctx["ad_domain"]),
        (ctx["dns_server"],),
    )


# --- smb (a real authenticated session and real bytes) ----------------------
def _build_smb(ctx: dict[str, Any]):
    # ANTI-CACHE: a WRITE is never served from a cache — `Copy-Item` to a UNC
    # path puts the full N bytes on the wire every fire. `net use` in front of
    # it is what makes the session setup real (SMB2 NEGOTIATE + SESSION_SETUP
    # with a Kerberos AP-REQ against `cifs/<fileserver>`), and the trailing
    # `/delete` is the lever: without it the session persists and every later
    # fire skips the negotiation entirely.
    unc = ctx["unc"]
    dest = ctx["dest"]
    return (
        [
            _exe("net.exe", ["use", unc]),
            _cmdlet(
                "Copy-Item",
                {
                    "Path": ctx["payload_path"],
                    "Destination": dest,
                    "Force": True,
                    "ErrorAction": "Stop",
                },
            ),
            _cmdlet(
                "Remove-Item",
                {"Path": dest, "Force": True, "ErrorAction": "SilentlyContinue"},
                always=True,
            ),
            _exe("net.exe", ["use", unc, "/delete", "/y"], always=True),
        ],
        (ctx["fileserver"],),
        (),
    )


# --- rpc (DCOM: endpoint mapper + a dynamic high port) ----------------------
def _build_rpc(ctx: dict[str, Any]):
    # ANTI-CACHE: `-ComputerName` is the whole row. Drop it and the query is
    # answered by the LOCAL WMI service — exit 0, zero packets. There is no
    # result cache for a remote WMI query, so every fire is a full round trip:
    # a bind to the endpoint mapper on 135/tcp, then the actual DCOM call on a
    # dynamically-assigned high port (the 49667/49669/49677 signature).
    #
    # `Get-WmiObject` (DCOM), NOT `Get-CimInstance` (WSMan on 5985/tcp) — see
    # the module docstring.
    return (
        [
            _cmdlet(
                "Get-WmiObject",
                {
                    "Class": "Win32_OperatingSystem",
                    "ComputerName": ctx["dc_name"],
                    "ErrorAction": "Stop",
                },
            )
        ],
        (ctx["dc_name"],),
        (),
    )


# --- ntp --------------------------------------------------------------------
def _build_ntp(ctx: dict[str, Any]):
    # ANTI-CACHE: `/rediscover` re-detects the time source and resynchronises
    # against it. A plain `/resync` can be satisfied from the service's
    # existing state ("The computer did not resync because no time data was
    # available") and emit nothing.
    #
    # TARGETLESS on purpose: a domain member's time source IS the domain
    # hierarchy (the PDC emulator), discovered by the Windows Time service —
    # pinning `/computer:` would make it a different, less realistic exchange.
    return ([_exe("w32tm.exe", ["/resync", "/rediscover"])], (), ())


# --- http / https -----------------------------------------------------------
def _build_web(ctx: dict[str, Any]):
    # ANTI-CACHE: `-DisableKeepAlive` forces a fresh TCP connection (and, for
    # https, a fresh TLS handshake) per fire instead of riding .NET's
    # ServicePoint connection pool — the handshake is what carries the SNI,
    # and the SNI is the whole reason the NAME matters here. `Invoke-WebRequest`
    # itself does no response caching (it is HttpWebRequest, not WinINET).
    #
    # `-UseBasicParsing` avoids the Internet Explorer DOM engine, which
    # Windows PowerShell 5.1 otherwise requires and which errors out on a
    # never-first-run-configured IE (and does not exist on Server Core).
    name = ctx["name"]
    port = int(ctx["port"])
    scheme = ctx["scheme"]
    host_part = name if port == ctx["default_port"] else f"{name}:{port}"
    uri = f"{scheme}://{host_part}/"
    return (
        [
            _cmdlet(
                "Invoke-WebRequest",
                {
                    "Uri": uri,
                    "UseBasicParsing": True,
                    "DisableKeepAlive": True,
                    "TimeoutSec": int(ctx["timeout_s"]),
                    "ErrorAction": "Stop",
                },
            )
        ],
        (name,),
        (),
    )


_WINDOWS_TEMPLATES: dict[str, _WindowsTemplate] = {
    "kerberos": _WindowsTemplate(
        commands=(("klist.exe", "the Windows Kerberos client (built in)"),),
        default_port=88, uses_port=False, spends_bytes=False, timeout_s=30,
        # The KDC exchange rides UDP or TCP by `MaxPacketSize`; the setup step
        # pins it to TCP (see the module docstring), which is the form the
        # DC's listen census needs.
        explicit_transports=frozenset({"tcp", "udp"}),
        dials=((88, "tcp"), (88, "udp")),
        forces_wire="`klist purge` empties the ticket cache so `klist get <SPN>` "
                    "must issue a real AS-REQ/TGS-REQ",
        label="klist.exe", build=_build_kerberos, setup=_setup_kerberos,
        setup_note=(
            "forcing the KDC exchange onto TCP (Kerberos MaxPacketSize=1) so "
            "the DC's listen census records 88/tcp; "
            "knobs.force_kerberos_tcp: false disables it"
        ),
        needs=("dc_name",),
    ),
    "ldap": _WindowsTemplate(
        commands=(("gpupdate.exe", "the Group Policy client (built in)"),),
        default_port=389, uses_port=False, spends_bytes=False, timeout_s=240,
        explicit_transports=frozenset({"tcp"}),
        # A real GP cycle is four protocols at once, which is exactly why this
        # row is worth more than a synthetic LDAP bind.
        dials=((389, "tcp"), (445, "tcp"), (135, "tcp"), (88, "tcp")),
        forces_wire="`/force` reapplies every policy instead of comparing GPO "
                    "version stamps and doing nothing",
        label="gpupdate.exe", build=_build_ldap, targetless=True,
    ),
    "dns": _WindowsTemplate(
        commands=(
            ("Resolve-DnsName", "the DnsClient module (built in, Windows 8/2012+)"),
            ("nltest.exe", "Windows Server built-in; on client SKUs an RSAT / "
                           "optional-feature component"),
        ),
        default_port=53, uses_port=False, spends_bytes=False, timeout_s=30,
        explicit_transports=frozenset({"tcp", "udp"}),
        dials=((53, "udp"), (53, "tcp"), (389, "udp")),
        forces_wire="`-Server` bypasses the DNS client cache and `/force` makes "
                    "nltest ignore the cached DC",
        label="Resolve-DnsName+nltest.exe", build=_build_dns,
        needs=("ad_domain",),
    ),
    "smb": _WindowsTemplate(
        commands=(
            ("net.exe", "the Windows SMB client (built in)"),
            ("Copy-Item", "Microsoft.PowerShell.Management (built in)"),
            ("Remove-Item", "Microsoft.PowerShell.Management (built in)"),
        ),
        default_port=445, uses_port=False, spends_bytes=True, timeout_s=180,
        explicit_transports=frozenset({"tcp"}),
        dials=((445, "tcp"), (88, "tcp")),
        forces_wire="a WRITE is never cached, and the trailing `net use /delete` "
                    "makes the next fire re-negotiate the session",
        label="net.exe+Copy-Item", build=_build_smb,
        needs=("fileserver", "smb_share"),
    ),
    "rpc": _WindowsTemplate(
        commands=(
            ("Get-WmiObject", "Windows PowerShell 5.1 (PowerShell 7 removed it — "
                              "use New-CimSession -SessionOption "
                              "(New-CimSessionOption -Protocol Dcom) there)"),
        ),
        default_port=135, uses_port=False, spends_bytes=False, timeout_s=90,
        explicit_transports=frozenset({"tcp"}),
        dials=((135, "tcp"),), dials_dynamic_high_ports=True,
        forces_wire="`-ComputerName` makes the query REMOTE; without it WMI "
                    "answers locally and emits nothing",
        label="Get-WmiObject", build=_build_rpc,
        needs=("dc_name",),
    ),
    "ntp": _WindowsTemplate(
        commands=(("w32tm.exe", "the Windows Time service (built in)"),),
        default_port=123, uses_port=False, spends_bytes=False, timeout_s=90,
        explicit_transports=frozenset({"udp"}),
        dials=((123, "udp"),),
        forces_wire="`/rediscover` re-detects the time source instead of being "
                    "satisfied from the service's existing state",
        label="w32tm.exe", build=_build_ntp, targetless=True,
    ),
    "http": _WindowsTemplate(
        commands=(("Invoke-WebRequest", "Microsoft.PowerShell.Utility (built in)"),),
        default_port=80, uses_port=True, spends_bytes=False, timeout_s=60,
        explicit_transports=frozenset({"tcp"}),
        dials=((80, "tcp"),),
        forces_wire="`-DisableKeepAlive` forces a fresh connection per fire "
                    "rather than riding the .NET connection pool",
        label="Invoke-WebRequest", build=_build_web,
        needs=("web_names",), multi_target_knob="web_names",
    ),
    "https": _WindowsTemplate(
        commands=(("Invoke-WebRequest", "Microsoft.PowerShell.Utility (built in)"),),
        default_port=443, uses_port=True, spends_bytes=False, timeout_s=60,
        explicit_transports=frozenset({"tcp"}),
        dials=((443, "tcp"),),
        forces_wire="`-DisableKeepAlive` forces a fresh TLS handshake per fire, "
                    "and the URL host is what becomes the SNI",
        label="Invoke-WebRequest", build=_build_web,
        needs=("web_names",), multi_target_knob="web_names",
    ),
}


def supported_protos() -> tuple[str, ...]:
    """The protos a WINDOWS `drive: command` persona can emit today (sorted)."""
    return tuple(sorted(_WINDOWS_TEMPLATES))


def dials_at_target(proto: str) -> tuple[tuple[int, str], ...]:
    """What this row puts on the wire AT THE TARGET, as `(port, transport)`
    pairs — the input to a twin config's S3 check (the DC is re-inferred as a
    domain controller only from an accepted 88/tcp AND 389/tcp)."""
    tpl = _WINDOWS_TEMPLATES.get(proto)
    return tpl.dials if tpl else ()


def required_commands(persona: Persona) -> tuple[tuple[str, str], ...]:
    """Every `(command, what-provides-it)` this persona's emits need in the
    guest, deduped and sorted — the input to the fail-loud start-time probe
    and to whatever an image bake has to satisfy. An emit whose proto has no
    Windows template is skipped here; `render_command_client_windows` is the
    one place that fails loud about it, so this stays a pure query."""
    found: set[tuple[str, str]] = set()
    for emit in persona.emits:
        tpl = _WINDOWS_TEMPLATES.get(emit.proto)
        if tpl is not None:
            found.update(tpl.commands)
    return tuple(sorted(found))


# ---------------------------------------------------------------------------
# knobs (free-form `Persona.knobs`, work order §3.2 — no schema change)
# ---------------------------------------------------------------------------
def _fail(persona: Persona, instance_id: str, msg: str) -> PersonaDriverError:
    return PersonaDriverError(f"persona[{instance_id}] ({persona.id}): {msg}")


def _check_hostname(persona: Persona, instance_id: str, knob: str, raw: Any) -> str:
    """Refuse anything that is not a bare hostname.

    The same rules as `browser._check_browse_name`, and for the same reason:
    this string becomes a TLS **SNI**, an SMB **UNC server**, a **DCOM target**
    or an **SPN host**. A URL, a host:port, a path or an IP literal there does
    not fail at runtime — it produces a flow a sensor records as truth with the
    wrong name on it, or (for an IP) an NTLM fallback / an SNI-less handshake,
    which is the exact fidelity gap this engine exists to close.
    """
    if not isinstance(raw, str) or not raw:
        raise _fail(
            persona, instance_id,
            f"knobs.{knob} entry {raw!r} is not a non-empty string",
        )
    name = raw
    if _is_ip_literal(name):
        raise _fail(
            persona, instance_id,
            f"knobs.{knob} entry {name!r} is an IP literal — a Windows domain "
            "member addresses NAMES (a Kerberos ticket is issued against an "
            "SPN, not an address; an IP literal silently downgrades to NTLM or "
            "drops the TLS SNI). Containment is the router's egress phase + "
            "twin-only DNS + the sink, not an IP-literal argv "
            "(docs/WORK_ORDER_protocol_engines.md §2 ruling 1)",
        )
    bad_chars = sorted(set(_UNSAFE_NAME_CHARS.findall(name)))
    if bad_chars or name.endswith(".") or ".." in name or len(name) > 253:
        raise _fail(
            persona, instance_id,
            f"knobs.{knob} entry {name!r} is not a bare hostname (no scheme, no "
            "port, no path, no trailing dot, <=253 chars)"
            + (f"; offending characters: {bad_chars}" if bad_chars else ""),
        )
    for label in name.split("."):
        if not _LABEL_RE.match(label):
            raise _fail(
                persona, instance_id,
                f"knobs.{knob} entry {name!r} has a malformed DNS label "
                f"{label!r} (1-63 chars of [A-Za-z0-9-], not starting or ending "
                "with '-')",
            )
    return name


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def resolve_windows_knobs(persona: Persona, instance_id: str) -> dict[str, Any]:
    """Validate + normalize this persona's Windows-engine knobs, once per
    render.

    Fails loud on a malformed knob rather than falling back to a default: a
    typo'd `dc_name` that silently became a synthesized guess would send every
    Kerberos ticket request at the wrong SPN and every DCOM call at the wrong
    host, and the persona would look healthy while proving nothing (LES-001).
    """
    knobs = persona.knobs if isinstance(persona.knobs, dict) else {}
    out: dict[str, Any] = {}

    for key in ("ad_domain", "dc_name", "fileserver"):
        raw = knobs.get(key)
        out[key] = (
            _check_hostname(persona, instance_id, key, raw) if raw is not None else ""
        )

    raw_names = knobs.get("web_names")
    names: list[str] = []
    if raw_names is not None:
        if isinstance(raw_names, str) or not isinstance(raw_names, (list, tuple)):
            raise _fail(
                persona, instance_id,
                f"knobs.web_names must be a LIST of hostnames, got "
                f"{type(raw_names).__name__} ({raw_names!r})",
            )
        names = [
            _check_hostname(persona, instance_id, "web_names", n) for n in raw_names
        ]
        if not names:
            raise _fail(
                persona, instance_id,
                "knobs.web_names is present but empty — drop the knob, or list "
                "at least one hostname for the http/https rows to visit",
            )
    out["web_names"] = names

    share = knobs.get("smb_share")
    if share is not None:
        if not isinstance(share, str) or not _SHARE_RE.match(share):
            raise _fail(
                persona, instance_id,
                f"knobs.smb_share {share!r} is not a bare SMB share name "
                "(1-80 chars of [A-Za-z0-9_.$-]; the server comes from "
                "knobs.fileserver)",
            )
    out["smb_share"] = share or ""

    spn = knobs.get("kerberos_spn")
    if spn is not None:
        if not isinstance(spn, str) or not _SPN_RE.match(spn):
            raise _fail(
                persona, instance_id,
                f"knobs.kerberos_spn {spn!r} is not a well-formed SPN "
                "(service/host[:port][/name], e.g. 'HOST/dc.corp.example') — "
                "`klist get` against a malformed SPN fails locally and never "
                "reaches the KDC",
            )
    out["kerberos_spn"] = spn or ""

    qname = knobs.get("dns_qname")
    qname = str(qname) if qname else _DEFAULT_DNS_QNAME
    if not _QNAME_RE.match(qname) or len(qname) > 253:
        raise _fail(
            persona, instance_id,
            f"knobs.dns_qname {qname!r} is not a well-formed DNS name "
            "([A-Za-z0-9._-], <=253 chars)",
        )
    out["dns_qname"] = qname

    server = knobs.get("dns_server")
    if server is not None:
        if not isinstance(server, str) or not (
            _is_ip_literal(server) or not _UNSAFE_NAME_CHARS.findall(server)
        ):
            raise _fail(
                persona, instance_id,
                f"knobs.dns_server {server!r} is neither an IP literal nor a "
                "hostname — it is the server `Resolve-DnsName -Server` is "
                "pointed at (which is what bypasses the client cache)",
            )
    out["dns_server"] = server or ""

    raw_flag = knobs.get("force_kerberos_tcp", True)
    if not isinstance(raw_flag, bool):
        raise _fail(
            persona, instance_id,
            f"knobs.force_kerberos_tcp must be a boolean, got "
            f"{type(raw_flag).__name__} ({raw_flag!r})",
        )
    out["force_kerberos_tcp"] = raw_flag

    raw_timeout = knobs.get("win_step_timeout_s")
    if raw_timeout is not None:
        try:
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError):
            raise _fail(
                persona, instance_id,
                f"knobs.win_step_timeout_s must be an integer number of "
                f"seconds, got {raw_timeout!r}",
            ) from None
        if not _MIN_STEP_TIMEOUT_S <= timeout_s <= _MAX_STEP_TIMEOUT_S:
            raise _fail(
                persona, instance_id,
                f"knobs.win_step_timeout_s {timeout_s} is outside "
                f"{_MIN_STEP_TIMEOUT_S}..{_MAX_STEP_TIMEOUT_S} seconds",
            )
        out["win_step_timeout_s"] = timeout_s
    else:
        out["win_step_timeout_s"] = 0
    return out


# ---------------------------------------------------------------------------
# Containment — the INVERSE of the Linux table's check 2
# ---------------------------------------------------------------------------
def _step_strings(step: dict[str, Any]) -> list[str]:
    """Every string value in a step, for the endpoint scan below."""
    out: list[str] = []
    if step["Kind"] == "exe":
        out.append(str(step["File"]))
        out += [str(a) for a in step["Args"]]
    else:
        out.append(str(step["Cmdlet"]))
        out += [str(v) for v in step["Params"].values() if isinstance(v, str)]
    return out


def _check_windows_targets(
    persona: Persona,
    instance_id: str,
    proto: str,
    tpl: _WindowsTemplate,
    steps: list[dict[str, Any]],
    targets: tuple[str, ...],
    endpoints: tuple[str, ...],
) -> None:
    """Refuse an invocation whose targeting is not what the render intended.

    The Linux table's three checks, with **check 2 inverted** (module
    docstring, work order §2 ruling 1):

    1. every declared target/endpoint really appears in the steps (a row that
       computed a name and then forgot to use it is a bug, and it makes the
       checks below vacuous);
    2. every declared TARGET is a **NAME, never an IP literal** — the exact
       opposite of `command._check_containment`, because a domain member that
       addresses an IP gets NTLM instead of Kerberos, no SNI, and no DC
       locator exchange at all. (An `endpoint` — the `Resolve-DnsName -Server`
       value — may legitimately be the orchestrator-resolved IP.)
    3. no UNDECLARED endpoint hides in the steps: any `scheme://` or `\\\\unc`
       string whose host is not a declared target is refused, so a row cannot
       smuggle a second destination past check 2.
    """

    def fail(reason: str) -> PersonaDriverError:
        return _fail(
            persona, instance_id,
            f"the {proto!r} Windows command template {reason} "
            "(docs/WORK_ORDER_protocol_engines.md §5 E6)",
        )

    if not targets and not tpl.targetless:
        raise fail("declares zero targets, so its targeting is unchecked")
    blob = [s for step in steps for s in _step_strings(step)]
    for value in (*targets, *endpoints):
        if not any(value in s for s in blob):
            raise fail(f"declared target {value!r} that appears in none of its steps")
    for name in targets:
        if _is_ip_literal(name):
            raise fail(
                f"targets {name!r} by ADDRESS — a Windows domain member must "
                "address names (SPNs, UNC servers, SNI); containment is the "
                "router's egress phase plus twin-only DNS, not an IP literal"
            )
    for s in blob:
        if "://" in s:
            host = urlsplit(s).hostname or ""
            if host not in {t.lower() for t in targets}:
                raise fail(f"carries an undeclared URL endpoint {s!r}")
        elif s.startswith("\\\\"):
            parts = s.lstrip("\\").split("\\")
            if not parts or parts[0] not in targets:
                raise fail(f"carries an undeclared UNC endpoint {s!r}")


# ---------------------------------------------------------------------------
# Per-emit invocation build
# ---------------------------------------------------------------------------
def _resolve_ports(
    persona: Persona, instance_id: str, emit: Emit, tpl: _WindowsTemplate
) -> list[int]:
    """Identical policy to `command._resolve_ports`: port 0 is the schema's
    "no port authored" placeholder, and a row with no port argument refuses a
    port it cannot actually drive rather than silently speaking its own."""
    ports = list(emit.ports) or [0]
    if tpl.uses_port:
        return [p if p else tpl.default_port for p in ports]
    allowed = {0, tpl.default_port}
    bad = sorted({p for p in ports if p not in allowed})
    if bad:
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} pins port(s) {bad} but the "
            f"{tpl.label} Windows template has no port argument (it always "
            f"speaks {tpl.default_port}) — drop the port, or use a proto whose "
            "template accepts one (http/https)",
        )
    return [tpl.default_port]


def _require_knobs(
    persona: Persona, instance_id: str, emit: Emit, tpl: _WindowsTemplate,
    knobs: dict[str, Any],
) -> None:
    missing = [k for k in tpl.needs if not knobs.get(k)]
    if missing:
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} needs knobs {missing} — the Windows "
            f"{tpl.label} template addresses the twin by NAME (work order §5 "
            "E6), and this driver will not guess one "
            "(e.g. `knobs: {dc_name: dc01.corp.example, ad_domain: "
            "corp.example, fileserver: fs01.corp.example, smb_share: Sales, "
            "web_names: [intranet.corp.example]}`)",
        )


def build_emit_invocations(
    persona: Persona,
    instance_id: str,
    emit: Emit,
    peer_ips: list[str],
    *,
    knobs: dict[str, Any],
    names: list[str],
    nbytes: int,
    payload_path: str,
) -> tuple[list[dict[str, Any]], _WindowsTemplate, list[str], int]:
    """`(invocations, template, notes, payload_bytes)` for one emit.

    Each invocation is `{"Target": <name-or-"">, "Steps": [...]}` — a flat list
    of HASHTABLES rather than a list of lists, deliberately: PowerShell's
    `@(@(...))` collapses a single-element array of arrays back into the inner
    array, so a nested-array config would silently lose one level on any emit
    with exactly one target (the classic PS array-flattening trap). A
    hashtable wrapper cannot flatten.

    The guest walks the list **round-robin** across an emit's `Runs`
    invocations per firing, rather than the Linux client's `messages` per peer:
    a Windows emit's targets come from `knobs` (several `web_names` is the
    normal case), so multiplying would turn a `volume: high` web emit into 120
    page fetches per firing instead of 24.
    """
    tpl = _WINDOWS_TEMPLATES.get(emit.proto)
    if tpl is None:
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} has no WINDOWS `drive: command` "
            f"template (supported on windows: {list(supported_protos())}) — "
            "add one to `personas.drivers.command_windows._WINDOWS_TEMPLATES`, "
            "or use `drive: scripted` for connectivity shape; refusing to "
            "substitute a plausible-looking wrong client",
        )
    if emit.peer_role in ("broadcast", "multicast"):
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} uses peer_role {emit.peer_role!r}, "
            "which a real client tool cannot speak a protocol to — use "
            "`drive: scripted` for segment chatter",
        )
    if emit.transport and emit.transport not in tpl.explicit_transports:
        # EXPLICIT only — `emit.transport` is `""` for a derived default, and a
        # derived value must never reach this check (the Linux table's code
        # review item 5, same rule).
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} pins transport {emit.transport!r} "
            f"explicitly, but the Windows {tpl.label} template can only honour "
            f"{sorted(tpl.explicit_transports) or ['(none)']} on the wire — "
            "drop the explicit `transport:` or use a proto whose template can "
            "drive it",
        )
    _require_knobs(persona, instance_id, emit, tpl, knobs)

    notes: list[str] = []
    timeout_s = knobs["win_step_timeout_s"] or tpl.timeout_s
    payload_bytes = 0
    if tpl.spends_bytes:
        payload_bytes = max(0, min(int(nbytes), _SMB_MAX_BYTES))
        if payload_bytes != int(nbytes):
            notes.append(
                f"bytes={int(nbytes)} clamped to {payload_bytes} -- the "
                f"{tpl.label} template's maximum staged payload"
            )
    else:
        notes.append(
            f"bytes={int(nbytes)} ignored -- the {tpl.label} template has no "
            "byte budget to spend (volume drives invocation COUNT for a "
            "command emit)"
        )

    ports = _resolve_ports(persona, instance_id, emit, tpl)
    base_ctx: dict[str, Any] = {
        "timeout_s": timeout_s,
        "default_port": tpl.default_port,
        "scheme": "https" if emit.proto == "https" else "http",
        "payload_path": payload_path,
        "ad_domain": knobs["ad_domain"],
        "dc_name": knobs["dc_name"],
        "fileserver": knobs["fileserver"],
        "qname": knobs["dns_qname"],
        "force_kerberos_tcp": knobs["force_kerberos_tcp"],
    }
    if emit.proto == "kerberos":
        spn = knobs["kerberos_spn"] or f"HOST/{knobs['dc_name']}"
        base_ctx["spn"] = spn
        base_ctx["spn_host"] = spn.split("/")[1].split(":")[0]
        if not knobs["kerberos_spn"]:
            notes.append(
                f"no knobs.kerberos_spn authored -- requesting a ticket for the "
                f"derived SPN {spn} (a host SPN on the DC); set the knob to "
                "exercise a service ticket instead"
            )
    if emit.proto == "dns":
        # The orchestrator-resolved peer is preferred as the `-Server` value:
        # it is an address, so pointing Resolve-DnsName at it cannot itself
        # depend on resolution, and it is what bypasses the client cache. Falls
        # back to an explicit knob, then to the DC's name.
        server = (peer_ips[0] if peer_ips else "") or knobs["dns_server"] or knobs["dc_name"]
        if not server:
            raise _fail(
                persona, instance_id,
                f"emit proto {emit.proto!r} has no DNS server to query: its "
                f"peer_role {emit.peer_role!r} resolved to nothing and neither "
                "knobs.dns_server nor knobs.dc_name is set — `Resolve-DnsName` "
                "without `-Server` is answered from the client cache and emits "
                "nothing (LES-001)",
            )
        base_ctx["dns_server"] = server
        if not peer_ips:
            notes.append(
                f"peer_role {emit.peer_role!r} did not resolve -- querying "
                f"{server} (from knobs) as the DNS server"
            )
    if emit.proto == "smb":
        unc = f"\\\\{knobs['fileserver']}\\{knobs['smb_share']}"
        base_ctx["unc"] = unc
        base_ctx["dest"] = f"{unc}\\axr-{safe_instance_name(instance_id)}.bin"

    targets_for_row: list[str] = (
        list(names) if tpl.multi_target_knob else [""]
    )
    invocations: list[dict[str, Any]] = []
    for port in ports:
        for target in targets_for_row:
            ctx = dict(base_ctx, port=port, name=target)
            steps, targets, endpoints = tpl.build(ctx)
            _check_windows_targets(
                persona, instance_id, emit.proto, tpl, steps, targets, endpoints
            )
            invocations.append(
                {"Target": target or (targets[0] if targets else ""), "Steps": steps}
            )
    return invocations, tpl, notes, payload_bytes


# ---------------------------------------------------------------------------
# PowerShell literal rendering
#
# Every exogenous value is embedded as a SINGLE-quoted PS literal with
# `''`-doubling and combined with literal text by CONCATENATION, never
# double-quoted interpolation — so this template never enters the
# double-quoted context whose separate escape rules LES-036 warns a
# single-quote-only escaper misses. No rendered string is ever handed to
# `Invoke-Expression`/`[scriptblock]::Create`: a cmdlet step is a hashtable of
# DATA that the client splats, so there is no eval surface at all.
# ---------------------------------------------------------------------------
_PS_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def ps_literal(value: Any) -> str:
    """Render a Python value as a PowerShell literal."""
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if value is None:
        return "$null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(float(value))
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, tuple)):
        return "@(" + ",".join(ps_literal(v) for v in value) + ")"
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if not _PS_KEY_RE.match(str(k)):
                raise PersonaDriverError(
                    f"refusing to render PowerShell hashtable key {k!r}: only "
                    "bare [A-Za-z][A-Za-z0-9]* keys are emitted, so a key can "
                    "never carry quoting of its own"
                )
            parts.append(f"{k}={ps_literal(v)}")
        return "@{" + "; ".join(parts) + "}"
    raise PersonaDriverError(
        f"refusing to render {type(value).__name__} {value!r} as a PowerShell "
        "literal (the rendered client must contain only data this module "
        "produced)"
    )


# ---------------------------------------------------------------------------
# The rendered in-guest client
#
# Structurally the SAME arm/loop/gate/jitter/next_fire/one-summary-line-per-fire
# machinery as `command._CLIENT_TEMPLATE` and `scripted._CLIENT_TEMPLATE_WINDOWS`;
# only the step runner differs.
# ---------------------------------------------------------------------------
_CLIENT_TEMPLATE_WINDOWS = '''# axRange command persona client (Windows domain member): {persona_id} (instance {instance_id})
$ErrorActionPreference = 'Continue'
$Instance = '{instance_id_sq}'
$PersonaId = '{persona_id_sq}'
$LogPath = '{log_path_sq}'
$OutFile = '{out_file_sq}'
$ErrFile = '{err_file_sq}'
$Seed = {seed_literal}      # $null -> unseeded (non-reproducible timing)
$BizStart = {biz_start}
$BizEnd = {biz_end}
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

# Windows PowerShell 5.1 can default to a protocol set that no modern TLS
# server will accept, which would make every `https` emit fail at the
# handshake for a reason that has nothing to do with the twin. Best-effort and
# LOGGED: an older .NET without the Tls12 enum member must not kill the client.
try {{
    $spm = [System.Net.ServicePointManager]::SecurityProtocol
    [System.Net.ServicePointManager]::SecurityProtocol = $spm -bor [System.Net.SecurityProtocolType]::Tls12
}} catch {{
    Write-PersonaLog ('note tls could not enable Tls12: ' + $_.Exception.Message)
}}

function New-AxrPayload([string]$Path, [int]$N) {{
    # The staged file the `smb` row copies -- the ONE Windows template that can
    # genuinely spend a byte budget. SetLength, not a materialized buffer: a
    # 256 MiB [byte[]] would cost more RAM than the guest has to spare, and the
    # copy still puts N real bytes on the wire.
    try {{
        $fs = [System.IO.File]::Create($Path)
        $fs.SetLength($N)
        $fs.Close()
        return $true
    }} catch {{
        Write-PersonaLog ('PAYLOAD FAILED ' + $Path + ' -- ' + $_.Exception.Message)
        return $false
    }}
}}

function Invoke-AxrStep($Step, [int]$TimeoutS) {{
    # Returns @{{ Ok; TimedOut; Missing; Detail }} -- never throws, so one bad
    # step can never take the whole persona down.
    $name = $Step.Cmdlet
    if ($Step.Kind -eq 'exe') {{ $name = $Step.File }}
    # SECOND guard (the start-time Get-Command probe is the first): a tool that
    # disappears AFTER start must show up as missing_tool= in the summary, not
    # as a silent skip. Checked explicitly rather than by matching an exception
    # message, which is localized.
    if ($null -eq (Get-Command -Name $name -ErrorAction SilentlyContinue)) {{
        return @{{ Ok = $false; TimedOut = $false; Missing = $true; Detail = 'not found' }}
    }}
    try {{
        if ($Step.Kind -eq 'exe') {{
            $sp = @{{ FilePath = $Step.File; NoNewWindow = $true; PassThru = $true;
                     RedirectStandardOutput = $OutFile; RedirectStandardError = $ErrFile }}
            if ($Step.Args.Count -gt 0) {{ $sp['ArgumentList'] = $Step.Args }}
            $proc = Start-Process @sp
            if (-not $proc.WaitForExit($TimeoutS * 1000)) {{
                # `net use` prompts for credentials when Kerberos is refused and
                # `gpupdate` can ask to log off; on a non-interactive SYSTEM task
                # either can park forever. The hard kill is what turns that into
                # a counted timeout instead of a stalled persona.
                try {{ $proc.Kill() }} catch {{ }}
                return @{{ Ok = $false; TimedOut = $true; Missing = $false; Detail = 'timeout' }}
            }}
            return @{{ Ok = ($proc.ExitCode -eq 0); TimedOut = $false; Missing = $false;
                      Detail = ('exit=' + $proc.ExitCode) }}
        }}
        $params = $Step.Params
        $null = & $Step.Cmdlet @params
        return @{{ Ok = $true; TimedOut = $false; Missing = $false; Detail = 'ok' }}
    }} catch {{
        $timedOut = $false
        if ($_.Exception -is [System.TimeoutException]) {{ $timedOut = $true }}
        elseif ([string]$_.Exception.Status -eq 'Timeout') {{ $timedOut = $true }}
        $miss = ($_.Exception -is [System.Management.Automation.CommandNotFoundException])
        return @{{ Ok = $false; TimedOut = $timedOut; Missing = $miss;
                  Detail = $_.Exception.Message }}
    }}
}}

function Invoke-PersonaEmit($Emit) {{
    $invocations = $Emit.Invocations
    if ($null -eq $invocations -or $invocations.Count -eq 0) {{
        Write-PersonaLog ('skip ' + $Emit.Proto + ' -- no invocation resolved for peer_role ' + $Emit.PeerRole)
        return
    }}
    $ok = 0
    $fails = 0
    $timeouts = 0
    $missing = 0
    $runs = 0
    $gap = [double]$Emit.GapS
    for ($i = 0; $i -lt $Emit.Runs; $i++) {{
        # Round-robin across targets (see build_emit_invocations' docstring).
        $inv = $invocations[$i % $invocations.Count]
        $runs += 1
        $failed = $false
        foreach ($step in $inv.Steps) {{
            if ($step.Always) {{ continue }}
            $res = Invoke-AxrStep $step ([int]$Emit.TimeoutS)
            if (-not $res.Ok) {{
                $failed = $true
                if ($res.TimedOut) {{ $timeouts += 1 }}
                if ($res.Missing) {{
                    $missing += 1
                    if ($missing -eq 1) {{
                        Write-PersonaLog ('MISSING TOOL ' + $Emit.Proto + ' -- ' + $Emit.Label
                            + ' needs a command this guest does not have; see '
                            + 'docs/WORK_ORDER_protocol_engines.md S5 E7 (bake it into the image)')
                    }}
                }}
                if ($fails -lt 3) {{
                    Write-PersonaLog ('run ' + $Emit.Proto + ' ' + $inv.Target + ' step failed: ' + $res.Detail)
                }}
                break
            }}
        }}
        foreach ($step in $inv.Steps) {{
            # Cleanup steps run whether or not the invocation failed, and their
            # own result is never counted -- a failed `net use /delete` is not a
            # failed emit, but a leaked session would poison every later fire.
            if (-not $step.Always) {{ continue }}
            $null = Invoke-AxrStep $step ([int]$Emit.TimeoutS)
        }}
        if ($failed) {{ $fails += 1 }} else {{ $ok += 1 }}
        if ($gap -gt 0) {{ Start-Sleep -Milliseconds ([int]($gap * 1000)) }}
    }}
    # ONE summary line per fire, matching the Linux command client's contract
    # (LES-245): `ok=` is the positive control -- zero there with runs>0 means
    # the member produced no traffic at all, however healthy the task looks;
    # `timeouts=` separates "nothing answered" from "it answered with an
    # error"; `missing_tool=` separates "no server" from "no client".
    Write-PersonaLog ('ran ' + $Emit.Proto + ' tool=' + $Emit.Label
        + ' targets=' + $invocations.Count + ' runs=' + $runs
        + ' ok=' + $ok + ' fails=' + $fails + ' timeouts=' + $timeouts
        + ' missing_tool=' + $missing)
}}

Write-PersonaLog ('armed pid=' + $PID + ' emits=' + $Emits.Count + ' seed=' + $Seed)
foreach ($e in $Emits) {{
    # An ignored/clamped knob, a substituted SPN or an unresolved peer is
    # announced ONCE, at arm time, rather than silently applied.
    foreach ($n in $e.Notes) {{ Write-PersonaLog ('note ' + $e.Proto + ' ' + $n) }}
    if ($e.PayloadBytes -gt 0 -and $e.PayloadPath -ne '') {{
        $null = New-AxrPayload $e.PayloadPath ([int]$e.PayloadBytes)
    }}
    foreach ($s in $e.Setup) {{
        $r = Invoke-AxrStep $s ([int]$e.TimeoutS)
        if ($r.Ok) {{
            Write-PersonaLog ('setup ' + $e.Proto + ' ok -- ' + $e.SetupNote)
        }} else {{
            Write-PersonaLog ('SETUP FAILED ' + $e.Proto + ' -- ' + $e.SetupNote
                + ' -- ' + $r.Detail)
        }}
    }}
}}
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

def _assert_ascii(persona: Persona, instance_id: str, src: str) -> str:
    """Refuse a rendered client carrying any non-ASCII byte.

    `GuestAgent.write_file` delivers Windows content as **UTF-8 without a
    BOM**, and Windows PowerShell 5.1 — the interpreter the Scheduled Task
    launches — decodes a BOM-less `.ps1` as the system **ANSI** code page, not
    UTF-8. Any non-ASCII character therefore arrives mojibake'd: harmless in a
    log note, silently wrong in a hostname, a share name or an SPN, and
    invisible until someone reads a capture. Cheaper to keep the whole render
    ASCII and say why than to reason about which fields can survive the
    round trip (LES-036's family: one quoting/encoding assumption that does
    not cover every context).
    """
    bad = sorted({c for c in src if ord(c) > 127})
    if bad:
        raise _fail(
            persona, instance_id,
            f"the rendered Windows client carries non-ASCII characters {bad} "
            "— Windows PowerShell 5.1 reads a BOM-less .ps1 as the system ANSI "
            "code page, so they would arrive corrupted in the guest; keep "
            "persona ids, knobs and rendered notes ASCII",
        )
    return src


def windows_payload_path(instance_id: str) -> str:
    """The staged SMB payload file, under the instance's own directory — the
    source `Copy-Item` writes to the share, created by the client at arm time
    and torn down with the instance's directory."""
    return f"{instance_paths(instance_id, os_family='windows')['dir']}\\payload-smb.bin"


def render_command_client_windows(
    persona: Persona,
    *,
    instance_id: str,
    peers: dict[str, str] | None = None,
    seed: Any = None,
    schedule: str | None = None,
    peer_sets: dict[str, list[str]] | None = None,
) -> str:
    """Render the deterministic in-guest PowerShell client that runs REAL
    built-in Windows protocol clients for `persona`'s emits, bound to
    `instance_id`.

    Same contract as `render_command_client` (Linux): `peers`/`peer_sets` are
    already resolved by the orchestrator and `schedule` is the
    already-normalized traffic-profile gate override. The one difference is
    that an unresolved `peer_role` is usually NOT even a degradation here — a
    domain member addresses NAMES from `knobs`, and the only row that wants an
    address at all is `dns` (`Resolve-DnsName -Server`, which falls back to
    `knobs.dns_server`/`knobs.dc_name` and says so in the guest log).

    Raises `PersonaDriverError` — fail-loud, never a silent substitution —
    when the persona is not `os_family: windows`, when an emit's proto has no
    Windows template, when it uses a segment-scoped peer role, pins a port or
    transport its tool cannot drive, is missing a knob the row addresses by,
    or would produce an invocation that targets an IP literal.
    """
    if persona.os_family != "windows":
        raise _fail(
            persona, instance_id,
            f"the Windows `drive: command` table is for os_family 'windows', "
            f"not {persona.os_family!r} — the Linux table "
            "(`personas.drivers.command`) is a different set of tools "
            "entirely, and running PowerShell built-ins against a Linux guest "
            "would emit nothing at all",
        )
    peers = peers or {}
    paths = instance_paths(instance_id, os_family="windows")
    eff_seed = effective_seed(seed, instance_id)
    seed_literal = str(int(eff_seed) & 0x7FFFFFFF) if eff_seed is not None else "$null"
    knobs = resolve_windows_knobs(persona, instance_id)
    payload_path = windows_payload_path(instance_id)
    # One rng for the whole render, seeded exactly like the guest's (and the
    # browser driver's, LES-025): the web-name order is stable for a given
    # (persona, instance, seed) and DIFFERENT across instances, so two members
    # running one persona do not lock-step through the same sites. An unseeded
    # render keeps the authored order (still deterministic, just not shuffled).
    rng = random.Random(eff_seed) if eff_seed is not None else None

    entries: list[str] = []
    for emit in persona.emits:
        sched = _resolve_emit_schedule(emit, schedule_override=schedule)
        peer_ips = resolve_emit_peers(emit, peers, peer_sets)
        names = list(knobs["web_names"])
        if names and rng is not None:
            rng.shuffle(names)
        invocations, tpl, notes, payload_bytes = build_emit_invocations(
            persona, instance_id, emit, peer_ips,
            knobs=knobs, names=names, nbytes=sched["bytes"],
            payload_path=payload_path,
        )
        setup_steps = tpl.setup(dict(knobs, timeout_s=tpl.timeout_s)) if tpl.setup else []
        if setup_steps:
            notes.append(tpl.setup_note)
        if not tpl.targetless:
            notes.append(
                # ASCII only -- this string is rendered INTO the PowerShell
                # client (see `_assert_ascii`).
                "names are resolved IN THE GUEST (a domain member must); "
                "containment is the router's egress phase + twin-only DNS + "
                "the sink, not this client "
                "(docs/WORK_ORDER_protocol_engines.md S2 ruling 1)"
            )
        entries.append(
            "    " + ps_literal(
                {
                    "Proto": emit.proto,
                    "PeerRole": emit.peer_role,
                    "Label": tpl.label,
                    "Invocations": invocations,
                    "Setup": setup_steps,
                    "SetupNote": tpl.setup_note if setup_steps else "",
                    "Notes": notes,
                    "PayloadPath": payload_path if payload_bytes else "",
                    "PayloadBytes": payload_bytes,
                    "TimeoutS": knobs["win_step_timeout_s"] or tpl.timeout_s,
                    "Gate": sched["gate"],
                    "IntervalS": float(sched["interval_s"]),
                    "GapS": float(sched["gap_s"]),
                    "Runs": int(sched["messages"]),
                }
            )
        )

    def sq(v: Any) -> str:
        return str(v).replace("'", "''")

    return _assert_ascii(persona, instance_id, _CLIENT_TEMPLATE_WINDOWS.format(
        persona_id=persona.id,
        instance_id=instance_id,
        instance_id_sq=sq(instance_id),
        persona_id_sq=sq(persona.id),
        log_path_sq=sq(paths["log"]),
        out_file_sq=sq(f"{paths['dir']}\\last-stdout.txt"),
        err_file_sq=sq(f"{paths['dir']}\\last-stderr.txt"),
        seed_literal=seed_literal,
        emit_entries=",\n".join(entries),
        biz_start=_BUSINESS_START_MIN,
        biz_end=_BUSINESS_END_MIN,
    ))


def render_command_launch_windows(
    instance_id: str, persona: Persona, *, script_path: str
) -> str:
    """The Windows launch bootstrap: (re)registers + starts the already-written
    PowerShell client as a detached **Scheduled Task** — never a bare
    `Start-Process` child of the QGA exec session (LES-086's Windows analog).

    IDENTICAL to the scripted driver's Windows bootstrap (both clients are
    `powershell.exe -File <script>` under the same deterministically-named
    task with the same fail-loud `Get-ScheduledTask ... .State` check), so this
    DELEGATES rather than duplicating it (LES-025). It exists as a named entry
    point so a future divergence has an obvious home."""
    return render_scripted_launch_windows(instance_id, persona, script_path=script_path)


def render_windows_command_probe(commands: list[str]) -> str:
    """The PowerShell one-liner that prints one `MISSING:<command>`-tagged line
    per MISSING cmdlet/exe (and nothing at all when every one is present),
    always exiting 0.

    Same contract and same tag as the Linux `command.render_tool_probe`: only a
    TAGGED line counts, so whatever a profile/banner writes to the same stdout
    cannot be misread as a missing command. `Get-Command` covers cmdlets and
    `.exe`s alike, which is why one probe answers for both kinds of step.
    """
    names = ",".join("'" + c.replace("'", "''") + "'" for c in commands)
    return (
        f"'{_WIN_PROBE_MARKER}' | Out-Null; "
        f"foreach ($c in @({names})) {{ "
        "if ($null -eq (Get-Command -Name $c -ErrorAction SilentlyContinue)) { "
        f"Write-Output ('{_MISSING_TAG}' + $c) }} }}; exit 0"
    )


def assert_windows_commands_present(
    ga: Any, vmid: int, persona: Persona, instance_id: str
) -> None:
    """Fail loud on any missing built-in — rather than starting a client that
    would log `missing_tool=` on every fire for the life of the twin
    (`CommandDriver._assert_tools_present`'s posture, work order §3.4). One
    probe for all of them (one QGA round-trip, not N).

    The message names **what provides** each missing command, because the one
    that really does go missing is `nltest.exe`: a built-in on Windows Server,
    an RSAT/optional-feature component on some client SKUs.
    """
    needed = required_commands(persona)
    if not needed:
        return
    provider_for = dict(needed)
    res = ga.shell_exec(
        vmid, render_windows_command_probe([c for c, _ in needed]), timeout_s=60
    )
    if not res.ok:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): could not probe for the "
            f"Windows protocol-client commands in vmid={vmid} (exit "
            f"{res.exit_code}): {(res.stderr or res.stdout)[:200]}"
        )
    missing = [
        ln[len(_MISSING_TAG):].strip()
        for ln in (res.stdout or "").splitlines()
        if ln.startswith(_MISSING_TAG)
    ]
    if missing:
        detail = "; ".join(f"{c} ({provider_for.get(c, '?')})" for c in missing)
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): Windows protocol-client "
            f"command(s) missing in vmid={vmid}: {detail}; enable the feature "
            "in the IMAGE (docs/IMAGE_BUILD.md, "
            "docs/WORK_ORDER_protocol_engines.md §5 E7) rather than adding an "
            "`install:` action to a standing twin"
        )
