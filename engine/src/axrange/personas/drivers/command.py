"""The `command` driver (docs/WORK_ORDER_protocol_faithful_traffic.md,
`docs/design/07-traffic-personas.md` "drive kinds"): render a per-persona
in-guest client that, at each emit's firing instant, **runs a real protocol
client tool** instead of opening a socket and pushing filler bytes at a port.

**Why this exists.** The sibling `scripted` driver emits connectivity SHAPE
and says so in its own docstring: peer, port, transport, timing and byte
volume are all correct, but the bytes on the wire are not the protocol named
in `proto:`. Measured on standing content: a `dns-resolver` twin node runs
real bind9 on :53, the persona pushes 256 bytes of `axrange-persona-payload`
filler at it, bind9 discards it as malformed, and Zeek records the exchange
as bare `udp` where the source capture records `dns`. **The servers are
already real; the clients are not.** A detection rule matches protocol
SEMANTICS (a DNS query name, an RC4 TGS-REP), never a port number — so
benign background traffic that cannot trigger a protocol-aware rule also
cannot produce a realistic false-positive rate, which is half of what a
detection-validation range is for.

**This is a new innermost step inside an existing pipeline, not a new
pipeline.** Everything around `fire()` is reused verbatim, not rebuilt:

* the persona document (`emits[]` already carries proto/ports/peer_role/
  cadence/volume/peer_scope — no new persona schema field);
* **orchestrator-side peer resolution** (`o2control.traffic._resolve_peers`/
  `_resolve_peer_sets` -> `scripted.resolve_emit_peers`), the load-bearing
  containment property — see below;
* the cadence/gate/volume resolver (`scripted._resolve_emit_schedule`),
  imported rather than copied;
* the launch primitive (`config_exec.render.systemd_daemon_start_block` —
  a detached transient `systemd-run` unit, NEVER `nohup ... &`, LES-086);
* the instance identity + lifecycle helpers (`drivers.base`);
* the delivery path (`ga.write_file` base64-in-command, LES-002);
* the dispatch (`drivers.get_driver`) — one new `_DRIVERS` entry is the
  entire wiring change.

**Containment is a NETWORK property, not a render-time argv check**
(2026-09-21, `docs/WORK_ORDER_protocol_engines.md` ruling 1, Authority:
**user**). This module originally called the IP-literal rule "a hard
requirement, structurally enforced — not a convention": every peer address
was resolved to a twin host IP in the orchestrator and baked into the argv,
and `_check_containment` refused any argv whose address host was a NAME.
**That requirement is reversed.** Guests now resolve names normally; only
the twin's own DNS answers (E3); a transparent any-IP sink impersonates
every external server (E2); and the router **denies real egress** for the
duration of a measurement window (E1, default-closed). Those three, not an
argv check, are what keep a twin's traffic inside the twin.

*Why it had to change:* a Kerberos ticket is issued against an **SPN**, not
an address — `kinit`, `kvno`, `ldapsearch -Y GSSAPI` and `smbclient` against
a domain simply cannot be driven by IP literal. The old rule did not make
those protocols contained; it made them **impossible**, which is why the
round that adds them reverses it. What the reversal trades is a
*structural* guarantee for an *operational* one, so E1's egress policy is
fail-safe **closed** and every capture window records the phase it ran in.

What survives unchanged: `_check_containment` still enforces check 1 (every
declared address really is in the argv) and check 3 (no **undeclared**
endpoint hides in the argv — a template cannot smuggle a second
destination). Only **check 2**, the IP-literal requirement, is reversed, and
it stays **reachable**: `CommandDriver(require_ip_literals=True)` (or
`render_command_client(..., require_ip_literals=True)`) restores the old
behaviour verbatim for a twin that has no containment triangle yet. The
`_bound_hosts` parser (`curl --resolve`/`--connect-to`) still works and is
still honoured — a legitimate pin, no longer load-bearing. Payload that
merely *looks* like a hostname (dig's query name) is still not an address.
The orchestrator still resolves every `peer_role` to a twin host IP and
still bakes it in: a name is used only where a persona asks for one
(`knobs.target_name`) or where the protocol cannot work without one.

**Prerequisite tools (§3.4).** `dig`/`curl`/`ntpdate` are not in the
`debian-12-cloud@v1` base image. `CommandDriver._start_linux` probes for
every binary the persona's emits need and raises `PersonaDriverError` naming
the missing binary + its Debian package — the same fail-loud posture
`ScriptedDriver._start_linux` already has for `python3`, never a client that
logs `command not found` on every fire for the life of the twin. The
in-guest client keeps a SECOND, per-emit guard (a `FileNotFoundError` counts
as a fail and logs `MISSING TOOL` once) so a binary that disappears after
start is visible in the guest log's counters rather than a silent skip. The
tool set is meant to be **baked into the image** (`docs/IMAGE_BUILD.md`),
not installed at deploy time: adding an `install:` action to a STANDING
twin's config trips `plandiff`'s mutation refusal (work order §5.2), so an
image change costs nothing on the next twin and everything on this one.

**Linux table here; the Windows table is a sibling module (2026-09-21,
docs/WORK_ORDER_protocol_engines.md §5 E6).** This module was Linux-only for
the reason it said — "a Windows `command` client would need a second,
independently-designed per-proto template table in PowerShell with no shared
argv table". That table now exists, as
`axrange.personas.drivers.command_windows`: the built-in domain-member
engine (`klist` / `gpupdate` / `nltest` / `net use` + `Copy-Item` /
`Get-WmiObject` / `w32tm` / `Resolve-DnsName` / `Invoke-WebRequest`).
`CommandDriver`'s three `os_family` dispatches route to it; **no new `drive:`
kind was minted**, so `DRIVE_KINDS`, PSN-030 and `VALIDATOR.md` are all
untouched. The split is by OS, exactly as `scripted.py` keeps
`render_scripted_client` and `render_scripted_client_windows` side by side —
the two tables share no argv vocabulary at all. An `os_family` that is
neither still raises loudly (LES-001), never falls through to Linux.

**Deterministic**, exactly like `render_scripted_client`: same persona +
peers + seed + instance_id renders byte-identical output; the only
seed-dependent content is the literal `SEED` int the guest script draws its
runtime jitter from.

---

**Choices this module makes that the work order left open** (all recorded
here so they are reviewable in one place, per §3.2/§3.3):

1. **Driver-owned template table, not an authored `emits[].command:`**
   (work order §3.2 option (a), its own recommendation). Personas stay
   portable and twin-agnostic; no new PSN rule and no injection-safety story
   for an operator-authored argv string. An unmapped `proto` is a fail-loud
   authoring error, exactly as an unmapped multicast proto already is in
   `scripted.resolve_emit_peers`.
2. **Twelve protos.** The original five — `dns`, `http`, `https`, `ntp`,
   `icmp` (the first three are the work order's own worked examples; `ntp`
   completes the corpapcn sink's 53/123 profile; `icmp` is the ONE template
   needing no image change at all, which is what made the first gated
   live-verify possible on a plain twin) — plus E5's seven real clients
   (2026-09-21, `docs/WORK_ORDER_protocol_engines.md` §5 E5): `smb`
   (smbclient) · `ldap` (ldapsearch) · `kerberos` (kinit + kvno) · `rpc`
   (rpcclient) · `rdp` (xfreerdp +auth-only) · `ssh` · `snmp` (snmpget).
   Those seven are where the DETECTION value lives — under 1% of measured
   flows but carrying the semantics a rule matches on. Everything else
   raises rather than guessing.
3. **`volume:` -> repetition** (§3.3): `messages` becomes the invocation
   count and `gap_s` the inter-invocation sleep. `bytes` is spent only by a
   template that genuinely can (`ping -s`); for every other template it is
   **ignored, and the ignoring is logged once at arm time** into the guest
   log (`bytes_note`), never silently dropped — the `fails=` counter is the
   precedent for making an invisible non-event visible.
4. **`dig`'s query name** comes from the persona's existing free-form
   `knobs: {dns_qname: ...}` (no schema change), defaulting to
   `axrange-probe.invalid.` — RFC 6761 guarantees `.invalid` never resolves
   globally, and the trailing dot plus `+nosearch` means the guest's own
   search list is never consulted.
5. **No shipped catalog persona uses `drive: command` yet.** The driver is
   available for a twin built fresh with the tools baked in; shipping one
   into the existing catalog would make it assignable onto guests that
   cannot run it (work order §5.2). Content is a separate, later step.
6. **A persona carries a credential REFERENCE, never a credential**
   (2026-09-21, E5). `knobs.auth_user`/`auth_domain`/`auth_realm` name WHO
   authenticates and `knobs.auth_credential: default|none` names the
   SOURCE; the secret itself is adapter.conf's lab-wide `DEFAULT_PASSWORD`,
   resolved at RUN time by `o2control.traffic.PersonaBackend` (the same
   source `config_exec.backend` uses for every identity it creates) and
   written into 0600 in-guest files by `CommandDriver._start_linux`. It is
   never an op input (no secret in `state.db` or a plan hash) and never in
   the rendered client — a knob that LOOKS like a literal secret is refused
   by name. See the "Credentials" section below.
7. **Volume, for a real client, is invocation count — not bytes.** None of
   E5's seven can be told how many bytes to spend, so all seven declare
   `spends_bytes=False` and announce the ignored budget once at arm time.
   `smb`'s byte volume is real but is the SERVER's choice: it comes from
   the size of the file `knobs.smb_file` names.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from axrange.personas.drivers import command_windows
from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    effective_seed,
    heartbeat_scheduled_task,
    heartbeat_unit,
    instance_paths,
    start_linux_python_client,
    start_windows_powershell_client,
    stop_scheduled_task,
    stop_unit,
    unsupported_os_family_error,
)
from axrange.personas.drivers.scripted import (
    _BUSINESS_END_MIN,
    _BUSINESS_START_MIN,
    _resolve_emit_schedule,
    render_scripted_launch,
    resolve_emit_peers,
)
from axrange.personas.schema import Emit, Persona

# `dig`'s default query name. RFC 6761 reserves `.invalid` as a name
# guaranteed never to resolve, so even a misconfigured guest cannot leak this
# query anywhere meaningful; the trailing dot makes it absolute, so the
# guest's `/etc/resolv.conf` search list is never appended. Overridable per
# persona via the EXISTING free-form `knobs:` dict (`dns_qname`) — no schema
# field, no new validator rule.
_DEFAULT_DNS_QNAME = "axrange-probe.invalid."
_QNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# `ping -s` (the ICMP data size) tops out at 65507 bytes — the IPv4 payload
# limit minus the IP and ICMP headers. A `volume: bulk` emit resolves to far
# more than that, so the value is clamped and the clamping is LOGGED (see
# `bytes_note`), never silently applied.
_PING_MAX_PAYLOAD = 65507

# ---------------------------------------------------------------------------
# Containment mode (2026-09-21 reversal — see the module docstring).
#
# `False` is the NEW model: an address may be a NAME, because the twin's own
# DNS is the only resolver that answers, a sink impersonates the world, and
# the router denies real egress while measuring. `True` restores the
# pre-2026-09-21 rule (every address host must be an IP literal or a
# `--resolve`-pinned name) for a twin that has no containment triangle yet —
# reachable via `CommandDriver(require_ip_literals=True)` or
# `render_command_client(..., require_ip_literals=True)`.
# ---------------------------------------------------------------------------
DEFAULT_REQUIRE_IP_LITERALS = False

# A rendered argv token of the form `axr-secret-file:/path` is expanded by
# the IN-GUEST client, at fire time, to the contents of that file. It exists
# for the one tool that can only take its credential on the command line
# (`snmpget -c <community>`): the rendered client — which an operator reads,
# which lands in the guest world-readable, and which this driver's tests
# assert over — therefore carries a PATH, never the secret itself. The file
# is written separately, 0600, by `CommandDriver._start_linux`.
_SECRET_FILE_PREFIX = "axr-secret-file:"

# Credential ARTIFACTS a template can ask for. Each is a file under the
# instance's own directory (`instance_paths(instance_id)["dir"]`) — instance-
# scoped, not host-scoped, because a host may now carry several persona
# instances at once (E0) and they must not share or overwrite one another's
# credentials.
_CRED_SMB_AUTH = "smb-auth"          # samba `-A` authfile (username/password/domain)
_CRED_PASSWORD = "password"          # the bare secret (ldapsearch -y, kinit/xfreerdp stdin)
_CRED_SNMP_COMMUNITY = "snmp-community"  # the SNMPv2c community string

# Knob keys that would carry a LITERAL secret in a persona document. A
# persona is catalog content — versioned in git, shared across twins, read
# by anyone — so it may only ever carry a credential REFERENCE
# (`auth_credential`), never the credential. Refused at render, by name.
_SECRET_KNOB_MARKERS = ("password", "passwd", "secret", "community", "privkey")

# A syntactically well-formed DNS name (one or more labels, optional trailing
# dot). Used by the REVERSED check 2: an address host must still be SOMETHING
# a resolver could be asked for — an empty/garbage host is a template bug
# either way, and check 2 stays meaningful rather than becoming a no-op.
_NAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(\.[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?$"
)

# Argv flags that carry their address INLINE rather than as the next token
# (`xfreerdp /v:host:port`). Stripped before an address token is parsed; kept
# as an explicit, short table rather than a general heuristic so a new shape
# has to be added deliberately.
_INLINE_ADDRESS_PREFIXES = ("/v:",)


# ---------------------------------------------------------------------------
# The per-proto template table (work order §3.2 option (a)).
#
# Mirrors `scripted._MULTICAST_GROUPS`'s precedent: a small, driver-owned
# table of wire detail that personas must not have to carry. Adding a proto
# is adding a row here, not new code elsewhere.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _CommandTemplate:
    """One proto's real client invocation.

    `build(peer, port, ctx)` returns `(argv, addresses)`. `addresses` names
    the argv tokens that are ADDRESSES — the things the tool will actually
    connect to — so `_check_containment` can verify each one carries a
    literal IP rather than a name the guest would have to resolve. Tokens
    that merely look like a hostname but are PAYLOAD (dig's query name) are
    deliberately excluded from that set.

    `uses_port=False` marks a tool with no port flag at all (`ntpdate`,
    `ping`). Such a template refuses an emit that pins a port other than its
    own well-known one rather than silently driving the wrong port (LES-001);
    `ports: []`/`[0]` (the schema default) is accepted.

    `spends_bytes=True` marks a template that can honour the volume-derived
    byte budget (`ping -s`). Every other template IGNORES `bytes`, and says
    so once in the guest log.
    """

    binary: str
    package: str
    default_port: int
    uses_port: bool
    spends_bytes: bool
    timeout_s: int
    build: Callable[[str, int, dict[str, Any]], tuple[list[str], tuple[str, ...]]]
    # The set of EXPLICITLY-authored `emits[].transport` values this
    # template's tool can actually honour on the wire (code review item 5).
    # A DERIVED default (`emit.transport` left unset, filled in by
    # `Emit.effective_transport()`) is NEVER checked against this set --
    # only an author who typed `transport:` explicitly can hit it. Required
    # on every template (no default) so a future 6th template author must
    # make this choice deliberately rather than silently getting "no
    # explicit transport honoured" by omission.
    explicit_transports: frozenset[str]

    # --- 2026-09-21, E5 (docs/WORK_ORDER_protocol_engines.md). Every field
    # below is additive with a default, so the five pre-existing rows are
    # unchanged and render byte-identically. ---
    #
    # This tool addresses its target by NAME and cannot be driven by IP
    # literal at all (`kinit`: a ticket is issued against a realm/SPN). Such
    # a template is refused OUTRIGHT in strict mode rather than rendering
    # something that could never work there.
    addresses_by_name: bool = False
    # The credential artifact this tool needs, if any (one of the `_CRED_*`
    # constants). `""` = none.
    credential: str = ""
    # The credential is MANDATORY for this tool (no anonymous mode), so
    # `knobs.auth_credential: none` is a loud refusal rather than a client
    # that authenticates with nothing and fails on every fire.
    credential_required: bool = False
    # The credential reaches the tool on STDIN (`kinit`, `xfreerdp
    # /from-stdin`) rather than through a file flag.
    credential_on_stdin: bool = False
    # Extra `(binary, package)` pairs the start-time probe must also check --
    # a follow-up step's tool (`kvno`) is as load-bearing as the primary's.
    extra_binaries: tuple[tuple[str, str], ...] = ()
    # `(binary, package)` pairs needed ONLY when this emit authenticates
    # (`knobs.auth_credential != none`) -- the ssh row's `sshpass`, which is
    # not in the argv at all in the anonymous/key-auth shape.
    credential_binaries: tuple[tuple[str, str], ...] = ()
    # Debian packages this row needs that provide NO binary, so no
    # `command -v` probe can ever catch them missing. The SASL mechanism
    # plugin is the case that cost a live run (2026-09-21): `ldap-utils` does
    # NOT depend on `libsasl2-modules-gssapi-mit`, and without it
    # `ldapsearch -Y GSSAPI` fails with the thoroughly misleading
    # `Unknown authentication method (-6) ... SASL(-4): no mechanism
    # available: No worthy mechs found` -- which reads as "the server refused
    # GSSAPI" and is in fact "this client cannot spell GSSAPI". Surfaced by
    # `required_packages()`, which is what a recipe/image must satisfy.
    extra_packages: tuple[str, ...] = ()
    # Environment neutralization (LES-201: an ambient config channel that
    # changes a tool's behaviour WITHOUT appearing in the argv must be
    # neutralized, not merely checked). Merged over the guest's environment
    # for this emit's invocations only. A value may carry the `{dir}`
    # placeholder, expanded to the INSTANCE's own directory -- which is what
    # keeps two persona instances on one host (E0) from sharing a Kerberos
    # ccache.
    env: tuple[tuple[str, str], ...] = ()
    # A second invocation run immediately after the primary in the SAME
    # firing, same `(peer, port, ctx)` contract. `kerberos` uses it for the
    # `kvno` TGS-REQ that follows `kinit`'s AS-REQ -- the TGS exchange is
    # where the detection value lives, so it is not optional.
    follow_up: Optional[
        Callable[[str, int, dict[str, Any]], tuple[list[str], tuple[str, ...]]]
    ] = None


def _build_dns(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    server = f"@{peer}"
    argv = [
        "dig", server,
        "-p", str(port),
        # -r: skip ~/.digrc -- the same "ignore ambient guest config that
        # could silently change client behaviour" property as curl's -q
        # below (code review nit).
        "-r",
        # +tries/+time bound the fire; +nosearch is belt-and-braces on dig's
        # own default (never append the guest's search list); +noall +answer
        # keeps stdout small — the client DEVNULLs it anyway, but a template
        # that would flood a log if ever redirected is a bad template.
        "+tries=1", "+time=2", "+nosearch", "+noall", "+answer",
    ]
    if ctx.get("force_tcp"):
        # Only reached for an EXPLICITLY-authored `transport: tcp` (never a
        # derived default -- see `build_emit_invocations`, code review item
        # 5): dig's own default is already UDP, matching `default_transport
        # ("dns")`, so there is nothing to force for the common case.
        argv.append("+tcp")
    argv += [str(ctx["qname"]), "A"]
    return argv, (server,)


def _build_http(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    url = f"http://{peer}:{port}/"
    return (
        # -q MUST be the FIRST argument -- it disables ~/.curlrc, which
        # curl otherwise reads (and could redirect this request) before any
        # other option takes effect. --noproxy * blocks ambient
        # http_proxy/https_proxy/ALL_PROXY from sending this request to an
        # uncontained, un-containment-checked proxy host instead of the
        # baked peer (code review MUST-FIX 1 -- demonstrated:
        # `http_proxy=http://proxy.does-not-exist.invalid:3128 curl ...`
        # fails resolving the PROXY name, which `_check_containment` never
        # sees because the proxy host never appears in this argv at all).
        ["curl", "-q", "-sS", "--noproxy", "*", "-o", "/dev/null", "--max-time", "5", url],
        (url,),
    )


def _build_https(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    # `-k`: a twin's TLS sink serves a self-signed cert; the point is a real
    # TLS handshake on the wire (Zeek records `ssl`), not certificate
    # validation. `-q`/`--noproxy *`: see `_build_http` -- same ambient
    # .curlrc/proxy-env containment hole, same fix (code review MUST-FIX 1).
    url = f"https://{peer}:{port}/"
    return (
        ["curl", "-q", "-sS", "-k", "--noproxy", "*", "-o", "/dev/null", "--max-time", "5", url],
        (url,),
    )


def _build_ntp(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    # `-q`: query only, never step/slew the guest clock. A persona must not
    # move a twin host's time (the exercise tier's Kerberos steps depend on
    # it). `-u`: use an unprivileged outgoing port rather than binding
    # :123 itself, so this doesn't collide with "NTP socket is in use" on a
    # guest already running chronyd/ntpd on :123 (code review item 4).
    # `-p 1`: exactly one sample, so a single fire comfortably clears the
    # in-guest client's `timeout_s` even under load.
    #
    # UNEXERCISED (code review item 4): `ntpdate` was NOT available on any
    # box this driver has been developed/tested against (unit tests only
    # exercise dns/http/https/icmp against real local listeners --
    # tests/test_personas.py's functional test). This template has never
    # actually run. The gated dev45 live-verify (work order §8) MUST cover
    # an `ntp` emit explicitly before this is trusted beyond "it parses".
    return (["ntpdate", "-q", "-u", "-p", "1", "-t", "2", peer], (peer,))


def _build_icmp(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    # `-n`: numeric output only — ping reverse-resolves the target by default
    # in some builds, which is exactly the guest-side name resolution this
    # driver must never cause.
    return (
        [
            "ping", "-n", "-c", "1", "-W", "2",
            "-s", str(int(ctx["ping_bytes"])), peer,
        ],
        (peer,),
    )


# ---------------------------------------------------------------------------
# E5's seven real clients (docs/WORK_ORDER_protocol_engines.md §5 E5).
#
# Every one of them is a REAL client speaking its real protocol
# non-interactively and failing loud: no `-y`-style "answer yes to anything",
# no interactive prompt that would hang under a systemd unit with no tty, and
# no credential literal anywhere in the rendered argv (see `_CRED_*`).
#
# Content rides the persona's free-form `knobs:` (§3.2 — no schema change),
# validated once per render by `_resolve_client_knobs`.
# ---------------------------------------------------------------------------
def _target(peer: str, ctx: dict[str, Any]) -> str:
    """The host this invocation addresses: the persona's `knobs.target_name`
    when it names one, else the orchestrator-resolved peer IP.

    A NAME is now a legitimate target (the 2026-09-21 reversal): the twin's
    own DNS is the only resolver that answers it. `target_name` exists
    because several of these protocols are name-bound in practice — an SMB
    share on a domain member, an LDAP server whose certificate/SPN carries
    its FQDN — and pinning the IP would produce a technically-connected
    session that fails the protocol's own identity check."""
    return str(ctx["knobs"].get("target_name") or peer)


def _build_smb(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`smbclient` fetching a real file, so the SMB byte volume on the wire
    is a real transfer rather than a bare negotiate/tree-connect.

    `-m SMB3` pins the dialect (never let an ambient default negotiate SMB1,
    which a modern server refuses and an old one answers differently);
    `-c 'get <file> /dev/null'` runs one non-interactive command and exits;
    `-N`/`-A <authfile>` is the credential split (never `-U user%password`,
    which would put the secret in the argv AND in `ps` output).

    AMBIENT CONFIG (LES-201, recorded not neutralized): samba tools read
    `/etc/samba/smb.conf`. It is image-baked and identical on every node of a
    twin, so it is deterministic rather than ambient in practice; `-s
    /dev/null` was considered and rejected because a samba build that needs
    `workgroup`/`client min protocol` from it would then fail in a way no
    test here can reproduce."""
    k = ctx["knobs"]
    unc = f"//{_target(peer, ctx)}/{k['smb_share']}"
    argv = ["smbclient", unc, "-p", str(port), "-m", "SMB3"]
    argv += _samba_auth_args(ctx)
    argv += ["-c", f"get {k['smb_file']} /dev/null"]
    return argv, (unc,)


def _samba_auth_args(ctx: dict[str, Any]) -> list[str]:
    """The shared samba-tool credential flags (`smbclient`/`rpcclient`)."""
    k = ctx["knobs"]
    if k["auth_credential"] == "none":
        return ["-N"]
    args = ["-A", ctx["cred_paths"][_CRED_SMB_AUTH]]
    if k["auth_domain"]:
        args += ["-W", k["auth_domain"]]
    return args


def _build_ldap(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`ldapsearch`, simple bind by default and GSSAPI where the persona says
    the credentials allow it (`knobs.ldap_sasl: gssapi` — which needs a
    ticket, i.e. a `kerberos` emit on the same persona).

    `-o nettimeout` + `-l`/`-z` bound the query so one slow server cannot
    wedge a firing; `-LLL` keeps stdout small. CLDAP (`cldap://`) is what an
    EXPLICIT `transport: udp` renders — the connectionless LDAP a Windows
    client uses for DC discovery, which is a distinct thing on the wire.

    **`-N` in GSSAPI mode is load-bearing — do not delete it.** OpenLDAP
    canonicalizes the SASL host name through **reverse DNS** before it builds
    the SPN it asks the KDC for. A twin has no PTR records (nothing in the
    name-service chain, E3, serves `in-addr.arpa`), so the reverse lookup
    either fails or returns something else, and `ldapsearch` then asks for
    `ldap/<whatever-came-back>` — a principal the KDC has never heard of.
    Measured on dev45 2026-09-21 (svc-engines): `ldapsearch -Y GSSAPI` →
    `Local error (-2) … GSSAPI Error … (Server not found in Kerberos
    database)`, exit 250, from the SAME ccache in which `kvno
    ldap/dc.corp.axr.lab` returned kvno=3. With `-N` (do not canonicalize the
    SASL host name via reverse DNS) the identical command binds:
    `SASL SSF: 256`, `SASL username: alice@CORP.AXR.LAB`, real DNs returned.
    The flag looks redundant — it is the difference between a working bind
    and a Kerberos error that blames the KDC for a DNS gap."""
    k = ctx["knobs"]
    scheme = "cldap" if ctx.get("explicit_transport") == "udp" else "ldap"
    uri = f"{scheme}://{_target(peer, ctx)}:{port}"
    argv = ["ldapsearch", "-H", uri, "-LLL", "-o", "nettimeout=5", "-l", "10", "-z", "50"]
    if k["ldap_sasl"] == "gssapi":
        argv += ["-Y", "GSSAPI", "-N"]
    else:
        argv += ["-x"]
        if k["auth_credential"] != "none":
            argv += ["-D", k["ldap_bind_dn"], "-y", ctx["cred_paths"][_CRED_PASSWORD]]
    argv += ["-b", k["ldap_base_dn"], "-s", "sub", k["ldap_filter"]]
    return argv, (uri,)


def _build_kerberos(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`kinit` — the AS-REQ/AS-REP that gets a TGT.

    NAME-BOUND BY CONSTRUCTION: a Kerberos client asks for a principal in a
    REALM, and the KDC is found from `krb5.conf`/DNS SRV — there is no argv
    slot for an IP at all. This is the template that makes the containment
    reversal non-optional (module docstring), which is why the row sets
    `addresses_by_name=True` and strict mode refuses it by name.

    The password arrives on STDIN (MIT `kinit` has no password-file flag),
    from a 0600 file this driver writes. `KRB5CCNAME` is pinned into the
    INSTANCE's own directory so two persona instances on one host (E0) never
    fight over the default `/tmp/krb5cc_<uid>` ccache."""
    k = ctx["knobs"]
    principal = f"{k['auth_user']}@{k['auth_realm']}"
    return ["kinit", principal], (principal,)


def _follow_up_kerberos(
    peer: str, port: int, ctx: dict[str, Any]
) -> tuple[list[str], tuple[str, ...]]:
    """`kvno <spn>` — the TGS-REQ/TGS-REP that follows the TGT.

    Not optional: the service-ticket exchange is where the detection value
    lives (an RC4 TGS-REP is the kerberoasting signature), and a `kinit`
    alone never produces one."""
    spn = ctx["knobs"]["kerberos_spn"]
    return ["kvno", spn], (spn,)


def _build_dcerpc(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`rpcclient` running one allowlisted RPC — `lsaquery` (LSA policy) or
    `enumdomusers` (SAMR user enumeration, the classic recon signature).

    `uses_port=False`: rpcclient has no port flag; it dials SMB (445, named
    pipes) and negotiates the RPC interface over it. Reaching the endpoint
    mapper on 135 directly is not expressible through this tool, so an emit
    that pins 135 is refused loudly rather than silently driving 445."""
    host = _target(peer, ctx)
    argv = ["rpcclient", "-c", ctx["knobs"]["rpc_command"]]
    argv += _samba_auth_args(ctx)
    argv += [host]
    return argv, (host,)


def _build_rdp(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`xfreerdp +auth-only` — a REAL TLS + CredSSP/NLA handshake that
    authenticates and then disconnects without ever opening a window, so it
    runs headless under a systemd unit with no display.

    The password arrives on STDIN (`/from-stdin`), never as `/p:<secret>`
    (which every `ps` on the host would show)."""
    k = ctx["knobs"]
    target = f"/v:{_target(peer, ctx)}:{port}"
    argv = [
        "xfreerdp", target, "+auth-only", "/sec:nla", "/cert-ignore",
        f"/u:{k['auth_user']}", "/timeout:5000",
    ]
    if k["auth_domain"]:
        argv.append(f"/d:{k['auth_domain']}")
    argv.append("/from-stdin")
    return argv, (target,)


def _build_ssh(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`ssh` running one short remote command, in one of two shapes.

    **Authenticated (the default, `knobs.auth_credential: default`)** —
    `sshpass -f <0600 file> ssh … -o PreferredAuthentications=password`. The
    session COMPLETES: a real KEX, a real userauth, a real channel, the
    remote command's exit status. Before 2026-09-21 this row was key-auth
    only and, on a twin that distributes no key, every firing ended in
    `Permission denied (publickey,password)` / exit 255 — a real KEX at the
    sensor, and a client that never once did what it claims to do (measured
    on dev45, svc-engines: `ssh -o BatchMode=yes axr@files.svc.axr.lab true`,
    every cycle, against a host where `axr` did not exist either).

    **Why `sshpass` and not a key.** The credential that already exists on
    every twin is the lab password (`AdapterConfig.default_password`,
    resolved at RUN time and written to a 0600 file by
    `CommandDriver._start_linux`); a key would need a generate-and-distribute
    slice nothing else in the platform has. `sshpass -f <path>` keeps the
    SECRET in that file — the rendered client and the argv carry a PATH, like
    `ldapsearch -y` and `smbclient -A` already do — at the cost of one extra
    package (`sshpass`, declared on the row via `credential_binaries`, so the
    start-time probe refuses a guest that lacks it rather than failing every
    firing). `BatchMode=yes` and a password are mutually exclusive: BatchMode
    refuses every prompt, which is exactly what sshpass must answer, so the
    authenticated shape drops it and pins
    `PreferredAuthentications=password` + `NumberOfPasswordPrompts=1` so a
    wrong password fails FAST instead of retrying or falling back.

    **Anonymous (`knobs.auth_credential: none`)** — the pre-existing key-auth
    shape, verbatim: `-o BatchMode=yes`, no credential, no `sshpass`. A
    keyless guest still fails after a real SSH-2 KEX. Kept for a persona that
    genuinely has a key, and so "no credential" never silently becomes "some
    credential".

    `-F /dev/null` neutralizes `~/.ssh/config` (LES-201) — a `Host`/
    `HostName` stanza there can silently re-point this connection at a
    completely different host without appearing in the argv."""
    k = ctx["knobs"]
    dest = f"{k['ssh_user']}@{_target(peer, ctx)}"
    common = [
        "-F", "/dev/null", "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
    ]
    if k["auth_credential"] == "none":
        argv = ["ssh", *common, "-o", "BatchMode=yes"]
    else:
        argv = [
            "sshpass", "-f", ctx["cred_paths"][_CRED_PASSWORD],
            "ssh", *common,
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password",
            "-o", "NumberOfPasswordPrompts=1",
        ]
    return (argv + [dest, k["ssh_remote_command"]], (dest,))


def _build_snmp(peer: str, port: int, ctx: dict[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """`snmpget` for one OID (sysDescr by default).

    The community string is the one credential no tool here can take from a
    file, so it is rendered as an `axr-secret-file:` REFERENCE the in-guest
    client expands at fire time — the rendered script carries a path, never
    the community. `SNMPCONFPATH` is neutralized in the row's `env`
    (LES-201): `/etc/snmp/snmp.conf` can otherwise set defaultversion/
    defcommunity and change what goes on the wire invisibly."""
    k = ctx["knobs"]
    agent = f"{_target(peer, ctx)}:{port}"
    community = _SECRET_FILE_PREFIX + ctx["cred_paths"][_CRED_SNMP_COMMUNITY]
    return (
        ["snmpget", "-v", "2c", "-c", community, "-r", "1", "-t", "2",
         agent, k["snmp_oid"]],
        (agent,),
    )


_COMMAND_TEMPLATES: dict[str, _CommandTemplate] = {
    "dns": _CommandTemplate(
        binary="dig", package="dnsutils", default_port=53,
        uses_port=True, spends_bytes=False, timeout_s=10, build=_build_dns,
        # dig's unforced default IS udp (matching `default_transport`); an
        # explicit `transport: tcp` is additionally honourable via +tcp.
        explicit_transports=frozenset({"tcp", "udp"}),
    ),
    "http": _CommandTemplate(
        binary="curl", package="curl", default_port=80,
        uses_port=True, spends_bytes=False, timeout_s=15, build=_build_http,
        # curl-over-HTTP is unconditionally TCP; there is no bare-UDP HTTP.
        explicit_transports=frozenset({"tcp"}),
    ),
    "https": _CommandTemplate(
        binary="curl", package="curl", default_port=443,
        uses_port=True, spends_bytes=False, timeout_s=15, build=_build_https,
        explicit_transports=frozenset({"tcp"}),
    ),
    "ntp": _CommandTemplate(
        binary="ntpdate", package="ntpdate", default_port=123,
        uses_port=False, spends_bytes=False, timeout_s=10, build=_build_ntp,
        # ntpdate is UDP-only; there is no NTP-over-TCP mode it can drive.
        explicit_transports=frozenset({"udp"}),
    ),
    "icmp": _CommandTemplate(
        binary="ping", package="iputils-ping", default_port=0,
        uses_port=False, spends_bytes=True, timeout_s=10, build=_build_icmp,
        # ICMP is its own IP protocol, neither tcp nor udp -- ping cannot
        # honour EITHER as an explicit transport choice.
        explicit_transports=frozenset(),
    ),
    # --- E5's seven (2026-09-21) -------------------------------------------
    "smb": _CommandTemplate(
        binary="smbclient", package="smbclient", default_port=445,
        uses_port=True, spends_bytes=False, timeout_s=30, build=_build_smb,
        # SMB is TCP; the NetBIOS-over-UDP era is not what this drives.
        explicit_transports=frozenset({"tcp"}),
        credential=_CRED_SMB_AUTH,
    ),
    "ldap": _CommandTemplate(
        binary="ldapsearch", package="ldap-utils", default_port=389,
        uses_port=True, spends_bytes=False, timeout_s=20, build=_build_ldap,
        # tcp = LDAP; udp = CLDAP (`cldap://`), a genuinely different
        # exchange on the wire, not the same query over a different socket.
        explicit_transports=frozenset({"tcp", "udp"}),
        credential=_CRED_PASSWORD,
        # The GSSAPI mech PLUGIN, which `ldap-utils` does not pull and which
        # provides no binary to probe for -- see `extra_packages`' own
        # comment for the misleading error its absence produces. Declared
        # unconditionally rather than only for `ldap_sasl: gssapi`: the row
        # is one row, the two packages are ~150 KB, and a per-knob package
        # list would be a second place for the GSSAPI mode to be half-set-up.
        extra_packages=("libsasl2-modules", "libsasl2-modules-gssapi-mit"),
        env=(("LDAPNOINIT", "1"),),  # ignore ldap.conf/.ldaprc (LES-201)
    ),
    "kerberos": _CommandTemplate(
        binary="kinit", package="krb5-user", default_port=88,
        uses_port=False, spends_bytes=False, timeout_s=20,
        build=_build_kerberos, follow_up=_follow_up_kerberos,
        # `kinit` picks udp-then-tcp per krb5.conf's `udp_preference_limit`;
        # there is no argv slot to force either, so -- like `ping` -- it can
        # honour NEITHER explicit transport. (It uses both on the wire: a
        # large TGS-REP falls back to tcp on its own.)
        explicit_transports=frozenset(),
        addresses_by_name=True,
        credential=_CRED_PASSWORD, credential_required=True,
        credential_on_stdin=True,
        extra_binaries=(("kvno", "krb5-user"),),
        # Per-INSTANCE credential cache: the default `/tmp/krb5cc_<uid>` is
        # per-UID, so two persona instances on one host (E0) authenticating
        # as different principals would overwrite each other's TGT.
        env=(("KRB5CCNAME", "FILE:{dir}/krb5cc"),),
    ),
    "rpc": _CommandTemplate(
        binary="rpcclient", package="smbclient", default_port=445,
        uses_port=False, spends_bytes=False, timeout_s=20, build=_build_dcerpc,
        explicit_transports=frozenset({"tcp"}),
        credential=_CRED_SMB_AUTH,
    ),
    "rdp": _CommandTemplate(
        binary="xfreerdp", package="freerdp2-x11", default_port=3389,
        uses_port=True, spends_bytes=False, timeout_s=20, build=_build_rdp,
        explicit_transports=frozenset({"tcp"}),
        credential=_CRED_PASSWORD, credential_required=True,
        credential_on_stdin=True,
    ),
    "ssh": _CommandTemplate(
        binary="ssh", package="openssh-client", default_port=22,
        uses_port=True, spends_bytes=False, timeout_s=20, build=_build_ssh,
        explicit_transports=frozenset({"tcp"}),
        # Not `credential_required`: `auth_credential: none` is the legal
        # key-auth shape (`_build_ssh`). When a credential IS in play the
        # password reaches ssh through `sshpass -f <path>`, so the tool set
        # for this row grows by one binary -- declared here so the start-time
        # probe refuses a guest without it (§3.4) instead of letting every
        # firing die with `sshpass: not found`.
        credential=_CRED_PASSWORD,
        credential_binaries=(("sshpass", "sshpass"),),
    ),
    "snmp": _CommandTemplate(
        binary="snmpget", package="snmp", default_port=161,
        uses_port=True, spends_bytes=False, timeout_s=10, build=_build_snmp,
        # SNMP-over-TCP exists but is vanishingly rare and net-snmp needs a
        # `tcp:` agent prefix to drive it; keeping this udp-only preserves
        # the `ntpdate` pattern -- a transport a row cannot honour is a loud
        # authoring error, never a silently ignored knob.
        explicit_transports=frozenset({"udp"}),
        credential=_CRED_SNMP_COMMUNITY, credential_required=True,
        env=(("SNMPCONFPATH", "/dev/null"),),  # ignore snmp.conf (LES-201)
    ),
}


def supported_protos() -> tuple[str, ...]:
    """The protos a `drive: command` persona can emit today (sorted)."""
    return tuple(sorted(_COMMAND_TEMPLATES))


def required_binaries(persona: Persona) -> tuple[tuple[str, str], ...]:
    """Every `(binary, debian-package)` this persona's emits need in the
    guest, deduped and sorted — the input to the fail-loud start-time probe
    (§3.4) and the thing an image bake must satisfy. An emit whose proto has
    no template is skipped here; `render_command_client` is the one place
    that fails loud about it, so this stays a pure query."""
    found: set[tuple[str, str]] = set()
    authenticating = _authenticates(persona)
    for emit in persona.emits:
        tpl = _COMMAND_TEMPLATES.get(emit.proto)
        if tpl is not None:
            found.add((tpl.binary, tpl.package))
            # A follow-up step's tool is as load-bearing as the primary's:
            # `kinit` without `kvno` gets a TGT and never asks for a service
            # ticket, which is the half that carries the detection value.
            found.update(tpl.extra_binaries)
            if authenticating:
                found.update(tpl.credential_binaries)
    return tuple(sorted(found))


def _authenticates(persona: Persona) -> bool:
    """Whether this persona's emits carry a credential at all
    (`knobs.auth_credential` is anything but `none`)."""
    knobs = persona.knobs if isinstance(persona.knobs, dict) else {}
    return str(knobs.get("auth_credential") or "default").lower() != "none"


def required_packages(persona: Persona) -> tuple[str, ...]:
    """Every Debian package this persona's emits need, sorted and deduped —
    a SUPERSET of `required_binaries`' package column.

    The difference is the packages that provide **no binary**, so no
    `command -v` probe can ever notice they are missing: today that is the
    SASL mechanism plugin `ldapsearch -Y GSSAPI` dlopen's
    (`libsasl2-modules-gssapi-mit`). Its absence surfaces as
    `Unknown authentication method (-6) … no mechanism available`, which
    accuses the SERVER of refusing GSSAPI. This is the list a recipe or an
    image bake has to satisfy (`docs/IMAGE_BUILD.md`, and
    `tests/test_svc_engines_recipes.py` for the shipped twins) — "a recipe
    must install the package that provides every binary AND every mechanism
    it uses"."""
    found: set[str] = {pkg for _, pkg in required_binaries(persona)}
    for emit in persona.emits:
        tpl = _COMMAND_TEMPLATES.get(emit.proto)
        if tpl is not None:
            found.update(tpl.extra_packages)
    return tuple(sorted(found))


def required_credentials(persona: Persona) -> tuple[str, ...]:
    """Every credential ARTIFACT (`_CRED_*`) this persona's emits need, given
    its `knobs.auth_credential` setting — the input to
    `render_credential_files` and to the start-time "is there a credential
    source at all?" check. Empty for every pre-2026-09-21 persona."""
    knobs = persona.knobs if isinstance(persona.knobs, dict) else {}
    if str(knobs.get("auth_credential") or "default").lower() == "none":
        return ()
    found: set[str] = set()
    for emit in persona.emits:
        tpl = _COMMAND_TEMPLATES.get(emit.proto)
        if tpl is not None and tpl.credential:
            if tpl.credential == _CRED_PASSWORD and str(
                knobs.get("ldap_sasl") or ""
            ).lower() == "gssapi" and emit.proto == "ldap":
                continue  # GSSAPI binds with the ticket cache, no password
            found.add(tpl.credential)
    return tuple(sorted(found))


# ---------------------------------------------------------------------------
# Credentials — a persona carries a REFERENCE, never a secret.
#
# A persona document is catalog content: versioned in git, shared across
# twins, readable by anyone with the repo. So it may name WHO authenticates
# (`knobs.auth_user`/`auth_domain`/`auth_realm`) and WHICH credential source
# to use (`knobs.auth_credential: default`), but never the credential itself
# — `_resolve_client_knobs` refuses a secret-looking knob by name.
#
# The secret reaches the guest the same way the rest of the system's
# identity material does: `AdapterConfig.default_password` (adapter.conf's
# `DEFAULT_PASSWORD`, docs/WORK_ORDER_access_facts.md Part A) resolved at RUN
# time by `o2control.traffic.PersonaBackend` — exactly where
# `config_exec.backend` resolves it for `create-identity`/`join-domain`. It
# is passed to `CommandDriver.start(credentials=...)` as a kwarg, so it is
# NEVER persisted in an op row (no secret in `state.db`, no secret in a plan
# hash) and NEVER rendered into the client script. The driver writes it into
# 0600 files under the instance's own directory at start; the argv references
# those paths.
# ---------------------------------------------------------------------------
def credential_paths(instance_id: str) -> dict[str, str]:
    """In-guest path per credential artifact, INSTANCE-scoped (not host-
    scoped): a host may carry several persona instances at once (E0), and
    two of them may legitimately authenticate as different principals."""
    d = instance_paths(instance_id)["dir"]
    return {
        _CRED_SMB_AUTH: f"{d}/smb-auth",
        _CRED_PASSWORD: f"{d}/password",
        _CRED_SNMP_COMMUNITY: f"{d}/snmp-community",
    }


def render_credential_files(
    persona: Persona, instance_id: str, *, secret: str,
) -> dict[str, str]:
    """`{path: content}` for every credential artifact this persona needs.

    Separate from `render_command_client` ON PURPOSE: the client script is
    world-readable in the guest and is what tests, reviewers and operators
    read, so it must stay secret-free. These files are written 0600 by
    `CommandDriver._start_linux` and by nothing else.

    `password` is written with NO trailing newline — `ldapsearch -y` treats
    the file's ENTIRE contents as the password, trailing newline included,
    which is a classic silent authentication failure. (`sshpass -f`, the
    other file-reading consumer, takes the first LINE and is therefore happy
    either way; the stdin consumers — `kinit`, `xfreerdp /from-stdin` — get
    the newline appended by the client at fire time.)"""
    knobs = _resolve_client_knobs(persona, instance_id)
    out: dict[str, str] = {}
    paths = credential_paths(instance_id)
    for artifact in required_credentials(persona):
        if artifact == _CRED_SMB_AUTH:
            out[paths[artifact]] = (
                f"username = {knobs['auth_user']}\n"
                f"password = {secret}\n"
                f"domain = {knobs['auth_domain']}\n"
            )
        elif artifact == _CRED_PASSWORD:
            out[paths[artifact]] = secret
        elif artifact == _CRED_SNMP_COMMUNITY:
            # `public` is the conventional read community of a lab SNMP
            # agent and is not a secret in any meaningful sense, but it
            # still goes through the same 0600 file + reference mechanism so
            # there is exactly ONE code path (and no persona ever carries a
            # community literal).
            out[paths[artifact]] = (
                "public" if knobs["snmp_community_source"] == "public" else secret
            )
    return out


# --- knobs -----------------------------------------------------------------
_USER_RE = re.compile(r"^[A-Za-z0-9._$-]{1,64}$")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]{1,63}$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9._$-]{1,80}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/\\-]{1,160}$")
_DN_RE = re.compile(r"^[A-Za-z0-9=,._ -]{1,256}$")
_FILTER_RE = re.compile(r"^\([A-Za-z0-9=*()&|!.,_ @-]{1,160}\)$")
_SPN_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}/[A-Za-z0-9._-]{1,253}$")
_OID_RE = re.compile(r"^[0-9]+(\.[0-9]+){2,}$")
_REMOTE_CMD_RE = re.compile(r"^[A-Za-z0-9 ._/-]{1,80}$")
_RPC_COMMANDS = frozenset({
    "lsaquery", "lsaenumsid", "enumdomusers", "enumdomgroups", "querydominfo",
    "srvinfo", "netshareenum", "netshareenumall", "getusername",
})
_AUTH_CREDENTIALS = frozenset({"default", "none"})
_DEFAULT_REALM = "TWIN.INVALID"


def _resolve_client_knobs(persona: Persona, instance_id: str) -> dict[str, Any]:
    """Validate + normalize this persona's client knobs, once per render.

    Fail-loud on anything malformed (LES-001): these strings become the
    identity on the wire (an SPN, a bind DN, a share name) and a wrong shape
    there is write-path garbage a sensor would faithfully record as truth.
    Every knob has a working default, so a persona that authors none still
    renders all seven templates."""
    knobs = persona.knobs if isinstance(persona.knobs, dict) else {}

    def fail(reason: str) -> PersonaDriverError:
        return PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): {reason}"
        )

    for key in knobs:
        low = str(key).lower()
        if low in ("auth_credential", "snmp_community_source"):
            continue
        if any(marker in low for marker in _SECRET_KNOB_MARKERS):
            raise fail(
                f"knobs.{key} looks like a credential LITERAL. A persona is "
                "catalog content (git-versioned, shared across twins) and "
                "must never carry a secret: name the credential SOURCE with "
                "`auth_credential: default` (the lab-wide DEFAULT_PASSWORD "
                "from adapter.conf, resolved at run time and written into a "
                "0600 in-guest file) or `auth_credential: none` for an "
                "anonymous client"
            )

    def text(key: str, default: str, pattern: re.Pattern[str], what: str) -> str:
        raw = knobs.get(key)
        value = str(raw) if raw not in (None, "") else default
        if value and not pattern.match(value):
            raise fail(f"knobs.{key} {value!r} is not {what}")
        return value

    auth_credential = str(knobs.get("auth_credential") or "default").lower()
    if auth_credential not in _AUTH_CREDENTIALS:
        raise fail(
            f"knobs.auth_credential {auth_credential!r} is not one of "
            f"{sorted(_AUTH_CREDENTIALS)}"
        )
    community_source = str(knobs.get("snmp_community_source") or "credential").lower()
    if community_source not in ("credential", "public"):
        raise fail(
            f"knobs.snmp_community_source {community_source!r} is not "
            "'credential' or 'public'"
        )
    auth_domain = text("auth_domain", "", _DOMAIN_RE, "a valid domain name")
    auth_realm = text(
        "auth_realm", auth_domain.upper() or _DEFAULT_REALM, _DOMAIN_RE,
        "a valid Kerberos realm",
    )
    target_name = text("target_name", "", _NAME_RE, "a valid hostname")
    ldap_sasl = str(knobs.get("ldap_sasl") or "").lower()
    if ldap_sasl not in ("", "gssapi"):
        raise fail(f"knobs.ldap_sasl {ldap_sasl!r} is not '' or 'gssapi'")
    rpc_command = str(knobs.get("rpc_command") or "lsaquery")
    if rpc_command not in _RPC_COMMANDS:
        raise fail(
            f"knobs.rpc_command {rpc_command!r} is not one of the allowlisted "
            f"rpcclient commands {sorted(_RPC_COMMANDS)}"
        )
    auth_user = text("auth_user", "axrange", _USER_RE, "a valid username")
    # A bind DN and a base DN default off the realm, so `auth_realm:
    # corp.example` alone produces a coherent LDAP identity.
    realm_dn = ",".join(f"dc={p.lower()}" for p in auth_realm.split("."))
    return {
        "auth_user": auth_user,
        "auth_domain": auth_domain,
        "auth_realm": auth_realm,
        "auth_credential": auth_credential,
        "target_name": target_name,
        "smb_share": text("smb_share", "share", _SHARE_RE, "a valid share name"),
        "smb_file": text("smb_file", "axrange-probe.bin", _PATH_RE, "a valid path"),
        "ldap_sasl": ldap_sasl,
        "ldap_base_dn": text("ldap_base_dn", realm_dn, _DN_RE, "a valid DN"),
        "ldap_bind_dn": text(
            "ldap_bind_dn", f"cn={auth_user},{realm_dn}", _DN_RE, "a valid DN",
        ),
        "ldap_filter": text(
            "ldap_filter", "(objectClass=*)", _FILTER_RE, "a valid LDAP filter",
        ),
        "rpc_command": rpc_command,
        "kerberos_spn": text(
            "kerberos_spn", f"host/{target_name or auth_realm.lower()}", _SPN_RE,
            "a valid SPN (service/host)",
        ),
        "ssh_user": text("ssh_user", auth_user, _USER_RE, "a valid username"),
        "ssh_remote_command": text(
            "ssh_remote_command", "true", _REMOTE_CMD_RE, "a simple remote command",
        ),
        "snmp_oid": text("snmp_oid", "1.3.6.1.2.1.1.1.0", _OID_RE, "a numeric OID"),
        "snmp_community_source": community_source,
    }


# ---------------------------------------------------------------------------
# Containment — checks 1 and 3 structural, check 2 REVERSED (2026-09-21).
# ---------------------------------------------------------------------------
def _address_host(address: str) -> str:
    """The host component of an address token, for every shape the templates
    produce:

    * bare address / name — `10.0.0.5`, `files.twin.invalid`
    * dig server          — `@10.0.0.5`
    * URL                 — `https://10.0.0.5:443/`, `ldap://dc01:389`
    * SMB UNC             — `//files.twin.invalid/share`
    * inline-flag target  — `/v:dc01:3389` (xfreerdp)
    * host:port           — `dc01:161` (snmpget's agent spec)
    * ssh destination     — `svc@dc01`
    * Kerberos principal  — `svc@CORP.TWIN` (the host is the REALM)
    * Kerberos SPN        — `host/dc01.twin.invalid`
    """
    for prefix in _INLINE_ADDRESS_PREFIXES:
        if address.startswith(prefix):
            address = address[len(prefix):]
            break
    if "://" in address:
        return urlsplit(address).hostname or ""
    if address.startswith("//"):                      # SMB UNC
        return address[2:].split("/", 1)[0]
    if address.startswith("@"):                       # dig server
        address = address[1:]
    if "/" in address:                                # SPN: service/host
        address = address.rsplit("/", 1)[1]
    if "@" in address:                                # user@host / princ@REALM
        address = address.rsplit("@", 1)[1]
    if _is_ip_literal(address):                       # incl. bare IPv6
        return address
    if address.count(":") == 1:                       # host:port
        address = address.split(":", 1)[0]
    return address


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _bound_hosts(argv: list[str]) -> dict[str, str]:
    """`{host: literal}` for every host a curl invocation has explicitly
    pinned to a LITERAL address with `--resolve
    <host>:<port>:<address>[,<address>...]` or `--connect-to
    <h1>:<p1>:<h2>:<p2>` — the two escape hatches the work order names for a
    hostname-bearing URL that must NOT go through guest resolution. No
    shipped template uses one today; the parser exists so that adding one
    stays containment-checked instead of silently bypassing the check.

    A DICT, not a set (code review MUST-FIX 3's peer-consistency check):
    `_check_containment` only needs to know a host IS bound (`host not in
    bound` still works on a dict's keys, unchanged); `build_emit_invocations`
    additionally needs to know WHAT it's bound to, to confirm that value is
    the peer the invocation was actually asked to dial (a `--resolve`-bound
    address is legitimately never itself the literal peer string — that's
    the whole point of the escape hatch — so the peer-consistency check has
    to resolve THROUGH a binding, not just accept any bound host).
    `--resolve`'s multi-address form binds to its FIRST address.

    Code-review fix (SHOULD-FIX 2): the ORIGINAL host only counts as bound
    when the address/host it's pinned TO is itself an IP literal.
    `--resolve fileserver:443:not-an-ip` or `--connect-to
    fileserver:443:another-name:443` would just move the resolution problem
    one hop, not solve it — checking only that a binding flag is PRESENT
    (the old behaviour) blessed both of those.
    """
    bound: dict[str, str] = {}
    for i, tok in enumerate(argv[:-1]):
        if tok == "--resolve":
            parts = argv[i + 1].split(":")
            if len(parts) < 3 or not parts[0]:
                continue
            # curl's --resolve syntax is `host:port:address[,address...]`;
            # an IPv6 address itself contains colons, so everything past
            # the second colon (rejoined) is the address portion, and a
            # newer curl's comma-separated multi-address form is checked
            # address-by-address.
            addresses = ":".join(parts[2:]).split(",")
            if addresses and all(_is_ip_literal(a) for a in addresses if a):
                bound[parts[0].lstrip("+")] = addresses[0]
        elif tok == "--connect-to":
            parts = argv[i + 1].split(":")
            if len(parts) != 4 or not parts[0]:
                continue
            host1, _port1, host2, _port2 = parts
            if _is_ip_literal(host2):
                bound[host1.lstrip("+")] = host2
    return bound


def _check_containment(
    persona: Persona, instance_id: str, proto: str,
    argv: list[str], addresses: tuple[str, ...],
    *, require_ip_literals: bool = DEFAULT_REQUIRE_IP_LITERALS,
) -> None:
    """Refuse an argv whose destinations are not what the template declared.

    Three checks, in order of what they catch:

    1. every declared address really is in the argv (a template that
       computed an address and then forgot to use it is a bug, not a
       containment hole, but it makes checks 2-3 vacuous);
    2. every declared address has a host component that is an IP literal or
       a syntactically valid DNS name — and, **only when
       `require_ip_literals=True`**, that it is an IP literal (or a name the
       same argv pins to one via `--resolve`/`--connect-to`);
    3. no UNDECLARED endpoint hides in the argv — any `://` token or
       `@`-prefixed token the template did not declare as an address is
       refused, so a template cannot smuggle a second destination past
       check 2.

    **Check 2 is the one the 2026-09-21 ruling reversed** (module docstring):
    by default a NAME is now a legitimate destination, because the twin's own
    DNS is the only resolver that answers, a transparent sink impersonates
    every external server, and the router denies real egress while measuring.
    `require_ip_literals=True` restores the pre-reversal rule verbatim.
    Checks 1 and 3 are untouched by the reversal and run in both modes.
    """
    def fail(reason: str) -> PersonaDriverError:
        if require_ip_literals:
            why = (
                " — STRICT containment mode (require_ip_literals=True) is on, "
                "which restores the pre-2026-09-21 rule that every address "
                "must be an orchestrator-baked IP literal"
            )
        else:
            why = (
                " — a `drive: command` template must address exactly what it "
                "declares (docs/WORK_ORDER_protocol_engines.md §5 E5)"
            )
        return PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): the {proto!r} command "
            f"template {reason}{why}"
        )

    if not addresses:
        # Code review MUST-FIX 3: an empty `addresses` tuple makes every
        # check below vacuously pass (nothing to iterate) — a template that
        # talks to a peer at all must declare at least one address, or this
        # whole containment check is a no-op for it.
        raise fail("declares zero addresses, so containment is unchecked")

    for address in addresses:
        if address not in argv:
            raise fail(f"declared address {address!r} that is not in its own argv")
    bound = _bound_hosts(argv)
    for address in addresses:
        host = _address_host(address)
        if not host:
            raise fail(f"produced an address {address!r} with no host component")
        if _is_ip_literal(host) or host in bound:
            continue
        if require_ip_literals:
            raise fail(
                f"addresses host {host!r} by name with no --resolve/--connect-to "
                "binding"
            )
        # REVERSED check 2 (2026-09-21): a name is fine, but it still has to
        # BE a name. A template that interpolated `None`, an empty string or
        # a shell fragment into its address is a bug either way, and letting
        # it through would turn this check into a no-op rather than a
        # reversal.
        if not _NAME_RE.match(host):
            raise fail(
                f"produced an address host {host!r} that is neither an IP "
                "literal nor a well-formed DNS name"
            )
    for tok in argv:
        if tok in addresses:
            continue
        if "://" in tok or tok.startswith("@"):
            raise fail(f"carries an undeclared endpoint token {tok!r}")


def _effective_destinations(argv: list[str], addresses: tuple[str, ...]) -> set[str]:
    """Each declared address's REAL destination host: the address's own
    host if it's an IP literal, or whatever `--resolve`/`--connect-to`
    pinned it to otherwise (`_bound_hosts`). Assumes `_check_containment`
    has already run on the same `(argv, addresses)`, so every host here is
    guaranteed to be one or the other — never a bare unresolved name.

    Exists so `build_emit_invocations` can verify the peer it asked a
    template to dial is really who the argv ends up talking to (MUST-FIX
    3's second half): a `--resolve`-bound address's own host string is
    legitimately never the literal peer (that's the point of the escape
    hatch, `test_containment_accepts_a_name_explicitly_bound_with_curl_
    resolve`), so that peer-consistency check has to resolve THROUGH a
    binding rather than only accepting a direct literal match."""
    bound = _bound_hosts(argv)
    return {
        host if _is_ip_literal(host) else bound.get(host, host)
        for host in (_address_host(a) for a in addresses)
    }


# ---------------------------------------------------------------------------
# Per-emit invocation build
# ---------------------------------------------------------------------------
def _resolve_qname(persona: Persona, instance_id: str) -> str:
    """This persona's `knobs.dns_qname`, or the RFC-6761-safe default,
    validated as a well-formed DNS name.

    Observation, no behavior change (code review nit): `build_emit_invocations`
    calls this for EVERY emit, not only `dns` ones, so a non-dns persona
    with a malformed `knobs.dns_qname` fails to render even though the
    qname is never used on the wire for that emit. Harmless in the current
    5-template catalog (a qname override is always authored alongside the
    `dns` emit it's for) and arguably a feature — a garbage `dns_qname` knob
    surfaces at render time regardless of which emit is checked first,
    rather than only when a `dns` emit happens to be present too."""
    raw = persona.knobs.get("dns_qname") if isinstance(persona.knobs, dict) else None
    qname = str(raw) if raw else _DEFAULT_DNS_QNAME
    if not _QNAME_RE.match(qname) or len(qname) > 253:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): knobs.dns_qname "
            f"{qname!r} is not a well-formed DNS name "
            "([A-Za-z0-9._-], <=253 chars)"
        )
    return qname


def _resolve_ports(
    persona: Persona, instance_id: str, emit: Emit, tpl: _CommandTemplate
) -> list[int]:
    ports = list(emit.ports) or [0]
    if tpl.uses_port:
        # Port 0 is the schema's "no port authored" placeholder; a real
        # client tool cannot dial it, so fall back to the proto's well-known
        # port rather than rendering `-p 0`.
        return [p if p else tpl.default_port for p in ports]
    allowed = {0, tpl.default_port}
    bad = sorted({p for p in ports if p not in allowed})
    if bad:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): emit proto {emit.proto!r} "
            f"pins port(s) {bad} but the {tpl.binary!r} template has no port "
            f"flag (it always speaks {tpl.default_port or 'no port'}) — drop "
            "the port or use a proto whose template accepts one"
        )
    return [tpl.default_port]


def build_emit_invocations(
    persona: Persona,
    instance_id: str,
    emit: Emit,
    peer_ips: list[str],
    *,
    nbytes: int,
    knobs: dict[str, Any] | None = None,
    cred_paths: dict[str, str] | None = None,
    require_ip_literals: bool = DEFAULT_REQUIRE_IP_LITERALS,
) -> tuple[list[list[list[str]]], list[list[list[list[str]]]], _CommandTemplate, str]:
    """`(argv_by_peer_by_port, follow_up_by_peer_by_port, template,
    bytes_note)` for one emit.

    The returned structure mirrors the scripted client's `peer -> message ->
    ports[i % len(ports)]` iteration exactly, with every argv FULLY resolved
    at render time: no in-guest templating, so every destination in the
    delivered client is readable off the script. `follow_up` is the parallel
    structure for a template that runs a SECOND invocation in the same firing
    (`kerberos`: `kinit` then `kvno`) — an empty list for every other proto,
    and omitted entirely from the rendered client when empty.

    `knobs`/`cred_paths` are `_resolve_client_knobs` / `credential_paths`
    output, passed in so a multi-emit render validates them once rather than
    once per emit."""
    tpl = _COMMAND_TEMPLATES.get(emit.proto)
    if tpl is None:
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): emit proto {emit.proto!r} "
            f"has no `drive: command` template (supported: "
            f"{list(supported_protos())}) — add one to "
            "`personas.drivers.command._COMMAND_TEMPLATES`, or use `drive: "
            "scripted` for connectivity shape; refusing to substitute a "
            "plausible-looking wrong client"
        )
    if emit.peer_role in ("broadcast", "multicast"):
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): emit proto {emit.proto!r} "
            f"uses peer_role {emit.peer_role!r}, which a real client tool "
            "cannot speak a protocol to — use `drive: scripted` for segment "
            "chatter"
        )
    if tpl.addresses_by_name and require_ip_literals:
        # Refused UP FRONT, by name: strict mode cannot contain a tool whose
        # target is a realm/SPN, and rendering it anyway would produce a
        # client that fails every fire for the life of the twin (LES-001 --
        # never a plausible-looking wrong answer).
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): emit proto {emit.proto!r} "
            f"cannot run under STRICT containment (require_ip_literals=True): "
            f"{tpl.binary} addresses a realm/SPN, never an IP, so there is "
            "nothing to bake. Run this persona on a twin with the "
            "containment triangle in place (router egress denial + twin-only "
            "DNS + sink, docs/WORK_ORDER_protocol_engines.md E1/E2/E3) and "
            "leave require_ip_literals at its default"
        )
    knobs = knobs if knobs is not None else _resolve_client_knobs(persona, instance_id)
    if tpl.credential_required and knobs["auth_credential"] == "none":
        raise PersonaDriverError(
            f"persona[{instance_id}] ({persona.id}): emit proto {emit.proto!r} "
            f"has no anonymous mode -- {tpl.binary} must authenticate -- but "
            "this persona sets `knobs.auth_credential: none`; use "
            "`auth_credential: default` or drop the emit"
        )
    ports = _resolve_ports(persona, instance_id, emit, tpl)
    ctx: dict[str, Any] = {
        "qname": _resolve_qname(persona, instance_id),
        "knobs": knobs,
        "cred_paths": cred_paths if cred_paths is not None else credential_paths(instance_id),
        "explicit_transport": emit.transport,
    }
    if emit.transport:
        # EXPLICIT only -- `emit.transport` is `""` for a derived default
        # (see `Emit.effective_transport()`); a derived value must NEVER
        # reach this check ("derived defaults stay ignored", code review
        # item 5). Only an author who typed `transport:` can hit either
        # branch below.
        if emit.transport not in tpl.explicit_transports:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): emit proto "
                f"{emit.proto!r} pins transport {emit.transport!r} "
                f"explicitly, but the {tpl.binary!r} template can only "
                f"honour {sorted(tpl.explicit_transports) or ['(none)']} on "
                "the wire -- drop the explicit `transport:` or use a proto "
                "whose template can drive it"
            )
        # Only `_build_dns` reads this (dig's own unforced default is
        # already udp); every other template's `build()` ignores it, so
        # setting it here unconditionally for an allowed explicit "tcp" is
        # a no-op for http/https/ntp/icmp.
        ctx["force_tcp"] = emit.transport == "tcp"
    bytes_note = ""
    if tpl.spends_bytes:
        ping_bytes = max(0, min(int(nbytes), _PING_MAX_PAYLOAD))
        ctx["ping_bytes"] = ping_bytes
        if ping_bytes != int(nbytes):
            bytes_note = (
                f"bytes={int(nbytes)} clamped to {ping_bytes} -- "
                f"{tpl.binary}'s maximum payload"
            )
    else:
        bytes_note = (
            f"bytes={int(nbytes)} ignored -- the {tpl.binary} template has no "
            "byte budget to spend (volume drives invocation COUNT for a "
            "command emit)"
        )
    by_peer: list[list[list[str]]] = []
    follow_by_peer: list[list[list[list[str]]]] = []
    for peer in peer_ips:
        per_port: list[list[str]] = []
        follow_per_port: list[list[list[str]]] = []
        for port in ports:
            argv, addresses = tpl.build(peer, port, ctx)
            _check_containment(
                persona, instance_id, emit.proto, argv, addresses,
                require_ip_literals=require_ip_literals,
            )
            # A TEMPLATE bug, not a persona-authoring error (contrast
            # `_check_containment`'s checks, which ARE authoring-facing) --
            # every address `_check_containment` accepted resolves to SOME
            # real IP literal (directly or via a `--resolve`/`--connect-to`
            # binding), but none of those checks confirm it is the SAME peer
            # this invocation was asked to dial. Catches a future template
            # whose `build()` drifts from its own `peer` argument (code
            # review MUST-FIX 3's second half). `_effective_destinations`
            # resolves through a binding rather than comparing the address's
            # raw host string, so a legitimate `--resolve`-bound hostname
            # URL (whose own host is never the literal peer) is not a false
            # positive here.
            #
            # 2026-09-21: the check now applies only when the template
            # addressed an IP at all. A NAME target (`knobs.target_name`, or
            # a name-bound tool like `kinit`) deliberately does NOT carry the
            # peer IP -- the name is the point -- so there is nothing to
            # compare it against; the destination is whatever the twin's own
            # DNS answers. The drift guard stays live for every IP-addressed
            # template, which is all five pre-existing rows and the default
            # rendering of five of the seven new ones.
            destinations = _effective_destinations(argv, addresses)
            if any(_is_ip_literal(d) for d in destinations):
                assert peer in destinations, (
                    f"{tpl.binary} template for proto {emit.proto!r} built an "
                    f"argv addressing {sorted(destinations)} but was asked to "
                    f"dial peer {peer!r}"
                )
            per_port.append(argv)
            if tpl.follow_up is not None:
                f_argv, f_addresses = tpl.follow_up(peer, port, ctx)
                _check_containment(
                    persona, instance_id, emit.proto, f_argv, f_addresses,
                    require_ip_literals=require_ip_literals,
                )
                follow_per_port.append([f_argv])
            else:
                follow_per_port.append([])
        by_peer.append(per_port)
        follow_by_peer.append(follow_per_port)
    if tpl.follow_up is None:
        follow_by_peer = []
    return by_peer, follow_by_peer, tpl, bytes_note


# ---------------------------------------------------------------------------
# The rendered in-guest client
#
# Structurally the SAME loop/gate/jitter/next_fire/one-summary-line-per-fire
# machinery as `scripted._CLIENT_TEMPLATE`; only `fire()`'s body differs
# (`subprocess.run(argv)` instead of `send_tcp`/`send_udp`).
# ---------------------------------------------------------------------------
_CLIENT_TEMPLATE = '''#!/usr/bin/env python3
# axRange command persona client: {persona_id} (instance {instance_id})
import os, random, subprocess, time
from datetime import datetime

INSTANCE = {instance_id!r}
PERSONA = {persona_id!r}
LOG = {log_path!r}
SEED = {seed!r}      # None -> unseeded (non-reproducible timing)
EMITS = {emits!r}
BIZ_START = {biz_start!r}
BIZ_END = {biz_end!r}
SECRET_PREFIX = {secret_prefix!r}

rng = random.Random(SEED)


def log(msg):
    with open(LOG, 'a') as f:
        f.write('[axrange] persona ' + INSTANCE + ' ' + msg + ' '
                 + datetime.now().isoformat() + chr(10))


def in_business_hours(now):
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return BIZ_START <= minute < BIZ_END


def read_secret(path):
    # The credential files this driver writes are 0600 and are read fresh on
    # every fire, so rotating one takes effect without restarting the unit.
    with open(path) as f:
        return f.read().strip()


def resolve_argv(argv):
    # A rendered argv token may carry a credential REFERENCE
    # (`axr-secret-file:<path>`) for the one tool that can only take its
    # credential on the command line. The script itself never holds a secret.
    out = []
    for tok in argv:
        if tok.startswith(SECRET_PREFIX):
            out.append(read_secret(tok[len(SECRET_PREFIX):]))
        else:
            out.append(tok)
    return out


def run_one(emit, argv, env, c):
    try:
        real = resolve_argv(argv)
        stdin_data = None
        if emit.get('stdin_file'):
            stdin_data = read_secret(emit['stdin_file']) + chr(10)
    except OSError as e:
        # A credential file that is missing or unreadable is a FAILED fire,
        # announced once -- never a silent skip and never a run with no
        # credential at all (LES-001, the `MISSING TOOL` precedent).
        c['missing_cred'] += 1
        c['fails'] += 1
        if c['missing_cred'] == 1:
            log('MISSING CREDENTIAL for ' + emit['proto'] + ': ' + str(e)
                + ' -- the driver writes it at start from DEFAULT_PASSWORD')
        return
    c['runs'] += 1
    try:
        res = subprocess.run(real, input=stdin_data, text=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             env=env, timeout=emit['timeout_s'])
        if res.returncode == 0:
            c['ok'] += 1
        else:
            c['fails'] += 1
            if c['fails'] <= 3:
                log('run ' + emit['proto'] + ' ' + real[0]
                    + ' exit=' + str(res.returncode))
    except FileNotFoundError:
        # The start-time probe already refuses a missing tool loudly;
        # this is the second guard, for a binary that disappears
        # AFTER start. Never a silent skip.
        c['missing'] += 1
        c['fails'] += 1
        if c['missing'] == 1:
            log('MISSING TOOL ' + real[0] + ' for '
                + emit['proto'] + ' -- install ' + emit['package']
                + ' (bake it into the image, docs/IMAGE_BUILD.md)')
    except (OSError, subprocess.SubprocessError) as e:
        c['fails'] += 1
        if c['fails'] <= 3:
            log('run ' + emit['proto'] + ' ' + real[0] + ' failed: ' + str(e))


def fire(emit):
    argv_by_peer = emit['argv']
    if not argv_by_peer:
        log('skip ' + emit['proto'] + ' -- peer_role ' + str(emit['peer_role'])
            + ' not resolved')
        return
    c = {{'ok': 0, 'fails': 0, 'missing': 0, 'missing_cred': 0, 'runs': 0}}
    gap = emit['gap_s']
    env = None
    if emit.get('env'):
        # Ambient tool configuration is NEUTRALIZED, not merely checked
        # (LES-201): ldap.conf/snmp.conf can re-point a query without ever
        # appearing in the argv.
        env = dict(os.environ)
        env.update(emit['env'])
    follow_by_peer = emit.get('follow_up') or []
    for pi, per_port in enumerate(argv_by_peer):
        follow_ports = follow_by_peer[pi] if pi < len(follow_by_peer) else []
        for i in range(emit['messages']):
            slot = i % len(per_port)
            run_one(emit, per_port[slot], env, c)
            # A follow-up (kerberos: kvno after kinit) runs in the SAME
            # firing, right after the primary -- the TGS exchange is the half
            # that carries the detection value.
            if slot < len(follow_ports):
                for extra in follow_ports[slot]:
                    run_one(emit, extra, env, c)
            if gap > 0:
                time.sleep(gap)
    # ONE summary line per fire, matching the scripted client's contract.
    # `fails=` is the operator's tell that the tool ran but the far side
    # never answered; `missing_tool=` separates "no server" from "no client",
    # and `missing_cred=` separates both from "no credential".
    log('ran ' + emit['proto'] + ' tool=' + emit['binary']
        + ' peers=' + str(len(argv_by_peer)) + ' runs=' + str(c['runs'])
        + ' ok=' + str(c['ok']) + ' fails=' + str(c['fails'])
        + ' missing_tool=' + str(c['missing'])
        + ' missing_cred=' + str(c['missing_cred']))


log('armed pid=' + str(os.getpid()) + ' emits=' + str(len(EMITS)) + ' seed=' + str(SEED))
for _e in EMITS:
    # An ignored/clamped knob is announced ONCE, at arm time, rather than
    # silently dropped (work order 3.3).
    if _e['bytes_note']:
        log('note ' + _e['proto'] + ' ' + _e['bytes_note'])
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


def render_command_client(
    persona: Persona,
    *,
    instance_id: str,
    peers: dict[str, str] | None = None,
    seed: Any = None,
    schedule: str | None = None,
    peer_sets: dict[str, list[str]] | None = None,
    require_ip_literals: bool | None = None,
) -> str:
    """Render the deterministic in-guest python3 client that runs REAL
    protocol clients for `persona`'s emits, bound to `instance_id`.

    `require_ip_literals` selects the containment mode (module docstring):
    `None` takes `DEFAULT_REQUIRE_IP_LITERALS` (the post-2026-09-21 default,
    names allowed); `True` restores the pre-reversal strict rule. The
    rendered client NEVER contains a credential — only paths to the 0600
    files `CommandDriver._start_linux` writes.

    Same contract as `render_scripted_client`: `peers`/`peer_sets` are
    already resolved by the orchestrator, an unresolved `peer_role` is not
    fatal (the guest logs a skip), and `schedule` is the already-normalized
    traffic-profile gate override.

    Raises `PersonaDriverError` — fail-loud, never a silent substitution —
    when an emit's proto has no template, uses a segment-scoped peer role,
    pins a port its tool cannot dial, carries a malformed `knobs.dns_qname`,
    or would produce an argv that could cause guest-side name resolution.
    """
    peers = peers or {}
    paths = instance_paths(instance_id)
    eff_seed = effective_seed(seed, instance_id)
    strict = (
        DEFAULT_REQUIRE_IP_LITERALS if require_ip_literals is None
        else bool(require_ip_literals)
    )
    knobs = _resolve_client_knobs(persona, instance_id)
    cred_paths = credential_paths(instance_id)
    emit_configs = []
    for emit in persona.emits:
        sched = _resolve_emit_schedule(emit, schedule_override=schedule)
        peer_ips = resolve_emit_peers(emit, peers, peer_sets)
        argv_by_peer, follow_by_peer, tpl, bytes_note = build_emit_invocations(
            persona, instance_id, emit, peer_ips, nbytes=sched["bytes"],
            knobs=knobs, cred_paths=cred_paths, require_ip_literals=strict,
        )
        config = {
            "proto": emit.proto,
            "peer_role": emit.peer_role,
            "peer_ips": peer_ips,
            "binary": tpl.binary,
            # Every package this emit's INVOCATION needs, so the in-guest
            # `MISSING TOOL` line names the package that provides whatever
            # actually went missing -- `sshpass`, for an authenticated ssh
            # emit, is argv[0] and is not in `openssh-client`.
            "package": ", ".join(
                dict.fromkeys(
                    [tpl.package]
                    + [
                        pkg for _, pkg in tpl.credential_binaries
                        if knobs["auth_credential"] != "none"
                    ]
                )
            ),
            "timeout_s": tpl.timeout_s,
            "argv": argv_by_peer,
            "bytes_note": bytes_note,
            # `bytes` is deliberately NOT threaded into the guest config:
            # for a command emit it is either already spent inside the
            # argv (`ping -s`) or ignored-and-announced. Carrying it
            # would imply the client still honours it.
            "gate": sched["gate"],
            "interval_s": sched["interval_s"],
            "messages": sched["messages"],
            "gap_s": sched["gap_s"],
        }
        # Every key below is OMITTED when it has nothing to say, so a
        # pre-2026-09-21 persona's rendered client is byte-identical to what
        # it was before E5 existed (the five original templates set none of
        # them).
        if follow_by_peer:
            config["follow_up"] = follow_by_peer
        if tpl.env:
            config["env"] = {
                k: v.format(dir=paths["dir"]) for k, v in tpl.env
            }
        if tpl.credential and tpl.credential_on_stdin and knobs["auth_credential"] != "none":
            config["stdin_file"] = cred_paths[tpl.credential]
        emit_configs.append(config)
    return _CLIENT_TEMPLATE.format(
        persona_id=persona.id,
        instance_id=instance_id,
        log_path=paths["log"],
        seed=eff_seed,
        emits=emit_configs,
        biz_start=_BUSINESS_START_MIN,
        biz_end=_BUSINESS_END_MIN,
        secret_prefix=_SECRET_FILE_PREFIX,
    )


def render_command_launch(
    instance_id: str, persona: Persona, *, script_path: str
) -> str:
    """The bash bootstrap (re)starting the already-written client as a
    detached, deterministically-named transient systemd unit — NEVER
    `nohup ... &` (LES-086).

    IDENTICAL to the scripted driver's bootstrap (both clients are
    `exec python3 <script>` under the same transient unit with the same
    fail-loud `systemctl is-active` check), so this DELEGATES rather than
    duplicating it (LES-025 — reuse downward, don't reimplement). It exists
    as a named entry point so a future divergence has an obvious home."""
    return render_scripted_launch(instance_id, persona, script_path=script_path)


# A self-identifying marker in the tool probe, so a test fake (and an
# operator reading a guest-agent log) can tell this exec apart from the
# `command -v python3` probe the scripted driver's Linux path also runs.
_TOOL_PROBE_MARKER = "axr-persona-tool-probe"


_MISSING_TAG = "MISSING:"


def render_tool_probe(binaries: list[str]) -> str:
    """The shell one-liner that prints one `MISSING:<binary>`-tagged line
    per MISSING binary (and nothing at all when every tool is present).
    Kept as a function so the probe's exact shape is pinnable in a test
    rather than being an inline f-string in the middle of `_start_linux`.

    The `MISSING:` tag (code review item 6) matters because this probe runs
    under `sh -lc` — a LOGIN shell, so it sources `/etc/profile` and friends
    before the probe body ever executes. A real guest image's login banner
    (a `/etc/profile.d/*.sh` `echo`, an MOTD-style line, ...) lands on the
    SAME stdout stream. `_assert_tools_present` used to treat every
    non-empty stdout line as a missing binary, so a banner line would be
    misread as one; filtering on the tag means only the probe's OWN output
    counts, no matter what a login shell prints ahead of it."""
    names = " ".join(binaries)
    return (
        f": {_TOOL_PROBE_MARKER}; "
        f'for b in {names}; do command -v "$b" >/dev/null 2>&1 || '
        f"printf '{_MISSING_TAG}%s\\n' \"$b\"; done"
    )


class CommandDriver(PersonaDriver):
    """Renders + launches a real-protocol-client client — the Linux one above
    as a detached transient systemd unit, or (`os_family: windows`) the
    built-in domain-member engine in `command_windows` as a detached Scheduled
    Task. One driver, one `drive:` kind, two per-OS tables.

    `require_ip_literals` is the driver-level containment switch (module
    docstring): leave it `None` for the post-2026-09-21 default (a name is a
    legitimate destination, containment is the network's job) or pass `True`
    to restore the pre-reversal strict rule for a twin with no containment
    triangle. The registered instance in `drivers/__init__._DRIVERS` uses the
    default; a caller that wants strict mode constructs its own."""

    def __init__(self, *, require_ip_literals: bool | None = None) -> None:
        self.require_ip_literals = require_ip_literals

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
        credentials: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if persona.os_family == "linux":
            return self._start_linux(
                ga, vmid, persona, instance_id=instance_id, peers=peers,
                seed=seed, schedule=schedule, peer_sets=peer_sets,
                credentials=credentials,
            )
        if persona.os_family == "windows":
            return self._start_windows(
                ga, vmid, persona, instance_id=instance_id, peers=peers,
                seed=seed, schedule=schedule, peer_sets=peer_sets,
            )
        raise unsupported_os_family_error(persona, instance_id)

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
        # Render FIRST (every authoring error — un-templated proto, missing
        # knob, IP-literal target — surfaces before anything is written into
        # the guest), then the shared Windows PowerShell-client start
        # sequence, with the built-in-command probe threaded in as
        # `pre_launch` so it runs before the Scheduled Task is registered.
        #
        # `credentials` is deliberately NOT threaded through: a domain member
        # authenticates as its own machine/logon identity over Kerberos, which
        # is the entire point of the engine — there is no password for this
        # client to hold (docs/WORK_ORDER_protocol_engines.md §5 E6).
        client_src = command_windows.render_command_client_windows(
            persona, instance_id=instance_id, peers=peers, seed=seed,
            schedule=schedule, peer_sets=peer_sets,
        )
        return start_windows_powershell_client(
            ga, vmid, persona, instance_id, client_src,
            pre_launch=lambda: command_windows.assert_windows_commands_present(
                ga, vmid, persona, instance_id
            ),
        )

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
        credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Render FIRST: every authoring error (unmapped proto, uncontained
        # address, un-dialable port) surfaces before anything is written into
        # the guest. Everything else (write, python3 check, launch) is the
        # shared Linux python3-client start sequence — code review item 7,
        # docs/WORK_ORDER_protocol_faithful_traffic.md: this body used to
        # duplicate `ScriptedDriver._start_linux` almost verbatim. The one
        # thing this driver adds is the protocol-client tool probe (§3.4),
        # threaded in as `pre_launch` -- it must run AFTER the python3 check
        # and BEFORE the launch script is written, exactly where it ran
        # before this refactor.
        client_src = render_command_client(
            persona, instance_id=instance_id, peers=peers, seed=seed,
            schedule=schedule, peer_sets=peer_sets,
            require_ip_literals=self.require_ip_literals,
        )

        def pre_launch() -> None:
            self._assert_tools_present(ga, vmid, persona, instance_id)
            self._write_credentials(ga, vmid, persona, instance_id, credentials)

        return start_linux_python_client(
            ga, vmid, persona, instance_id, client_src, pre_launch=pre_launch,
        )

    def _write_credentials(
        self, ga: Any, vmid: int, persona: Persona, instance_id: str,
        credentials: dict[str, Any] | None,
    ) -> None:
        """Write this instance's credential files 0600, immediately before
        launch — the ONLY place a secret touches the guest.

        The secret is never rendered into the client script, never recorded
        in an op row, and never logged. A persona that needs one but was
        started with no credential source fails LOUD here (LES-001): the
        alternative is a client that authenticates with nothing and logs a
        failure on every fire for the life of the twin."""
        needed = required_credentials(persona)
        if not needed:
            return
        secret = str((credentials or {}).get("default_password") or "")
        if not secret:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): emits "
                f"{[e.proto for e in persona.emits]} need a credential "
                f"({list(needed)}) but no credential source was supplied. The "
                "traffic tier passes the lab-wide DEFAULT_PASSWORD "
                "(adapter.conf, docs/WORK_ORDER_access_facts.md Part A) as "
                "`credentials={'default_password': ...}`; set it, or author "
                "`knobs.auth_credential: none` for an anonymous client"
            )
        for path, content in render_credential_files(
            persona, instance_id, secret=secret,
        ).items():
            ga.write_file(vmid, path, content, mode="0600")
            # `write_file`'s `mode=` is belt; the explicit chmod is braces --
            # a pre-existing file's mode is not necessarily reset by a
            # rewrite, and this file must never be world-readable.
            res = ga.exec(vmid, ["chmod", "0600", path], timeout_s=30)
            if not res.ok:
                raise PersonaDriverError(
                    f"persona[{instance_id}] ({persona.id}): could not chmod "
                    f"0600 the credential file {path} in vmid={vmid} (exit "
                    f"{res.exit_code}) -- refusing to run a client whose "
                    "credential may be world-readable"
                )

    def _assert_tools_present(
        self, ga: Any, vmid: int, persona: Persona, instance_id: str
    ) -> None:
        """Fail loud, per §3.4, on any missing protocol-client binary —
        rather than starting a client that would log `command not found` on
        every fire for the life of the twin. One probe for all of them (one
        QGA round-trip, not N)."""
        needed = required_binaries(persona)
        if not needed:
            return
        pkg_for = dict(needed)
        res = ga.exec(
            vmid, ["sh", "-lc", render_tool_probe([b for b, _ in needed])],
            timeout_s=30,
        )
        if not res.ok:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): could not probe for "
                f"the protocol client tools in vmid={vmid} (exit "
                f"{res.exit_code}): {(res.stderr or res.stdout)[:200]}"
            )
        # `MISSING:`-prefix filtering (code review item 6): `sh -lc` is a
        # LOGIN shell, which sources `/etc/profile` and friends before the
        # probe body runs -- a real guest image's login banner lands on the
        # same stdout and, un-tagged, would be misread as a missing tool.
        # Only a tagged line counts; see `render_tool_probe`'s docstring.
        missing = [
            ln[len(_MISSING_TAG):].strip()
            for ln in (res.stdout or "").splitlines()
            if ln.startswith(_MISSING_TAG)
        ]
        if missing:
            detail = ", ".join(f"{b} (apt: {pkg_for.get(b, '?')})" for b in missing)
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): protocol client "
                f"tool(s) missing in vmid={vmid}: {detail}; bake them into "
                "the image (docs/IMAGE_BUILD.md) rather than adding an "
                "`install:` action to a standing twin "
                "(docs/WORK_ORDER_protocol_faithful_traffic.md §5.2)"
            )

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        if persona.os_family == "linux":
            return stop_unit(ga, vmid, instance_id)
        if persona.os_family == "windows":
            return stop_scheduled_task(ga, vmid, instance_id)
        raise unsupported_os_family_error(persona, instance_id)

    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str
    ) -> dict[str, Any]:
        if persona.os_family == "linux":
            return heartbeat_unit(ga, vmid, instance_id)
        if persona.os_family == "windows":
            return heartbeat_scheduled_task(ga, vmid, instance_id)
        raise unsupported_os_family_error(persona, instance_id)
