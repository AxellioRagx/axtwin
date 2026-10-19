"""In-guest probe RENDER + result parsing for rows 5 and 4
(`docs/WORK_ORDER_fidelity_scorecard.md` §3.3 / §6 S3). **Pure**: nothing
here opens a socket, reaches a cluster, or holds a `GuestAgent` --
`runner.py` is the one place that does, so the whole eleven-row computation
stays unit-testable from fixtures with no DB and no cluster (§5).

**Why in-guest at all.** Twin segments are isolated SDN networks with no
route from the orchestrator -- the reason cloud-init and Ansible are
QGA-mediated in the first place (CHANGELOG 2026-06-02) -- so "does this
service answer" is a question only another twin host can ask. The probe
mirrors `config_exec/backend.py::_verify_capture`: render a script, deliver
it with `GuestAgent.write_file` (base64-in-command, LES-002), execute it,
read structured output back, fail loud on no result (LES-001).

**Python 3, stdlib only.** `personas/drivers/base.py` already requires and
probes for `python3` in every Linux guest, so `socket`/`ssl`/`struct` are
available with no install -- which is what makes the probe read-only against
a STANDING twin (§9.6: no package install, no op row, no state-store write,
the script removed after the run). A missing interpreter is a loud refusal,
never a silent skip (LES-089) -- enforced in `runner.py`, before a single
target is fired.

**The protocol set follows the observed listen set, not a fixed v1 list**
(§3.3, reconciled with the row-5 server half 2026-09-04). CorpAPCN's
observed listens are an AD profile (`53/udp`, `135/tcp`, `389/tcp+udp`,
`445/tcp`, `88/tcp`, `137/udp`, `3389/tcp`), so alongside dns/http/https/ntp
the set carries **krb** (an AS-REQ expecting AS-REP or KRB-ERROR), **ldap**
(a rootDSE search), **smb** (an SMB2 NEGOTIATE) and **rdp** (an X.224
connection request) -- each a minimal binary handshake. Without them row 5
could never pass on the one host carrying 12 of the observed ports and the
gate would be hollow.

**`connect` is never a pass.** A port with no protocol-aware method gets a
bare TCP connect, labelled `protocol_aware: false`, whose best possible
result is `inconclusive` -- "the port is open" is not "the protocol
answered", and row 5's rule is the second one.

**Containment.** Every peer address is an orchestrator-resolved LITERAL
baked into the render; the guest never resolves a name. `check_containment`
refuses to render a target whose address is not an IP literal -- the same
structural property `drive: command` is built around (LES-201/LES-202),
enforced here at render time rather than trusted.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# Output framing. The guest prints exactly one JSON document between these,
# so a MOTD, a sudo lecture or a stray warning in the exec's stdout cannot
# corrupt the parse (the same framing discipline `capture_confirm_command`
# relies on for its grep).
BEGIN_MARKER = "AXR-PROBE-BEGIN"
END_MARKER = "AXR-PROBE-END"

# Per-target socket budget in the guest, and the whole script's own ceiling.
DEFAULT_TARGET_TIMEOUT_S = 3.0

# Result statuses, per (host, port, proto) FACT.
PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"   # reached, but not protocol-aware -- never a pass


class ProbeRenderError(Exception):
    """The probe could not be rendered (a non-literal address, an empty
    target set). Typed so the CLI/API surface it as a refusal rather than a
    traceback (LES-110)."""


# ---------------------------------------------------------------------------
# Method selection
# ---------------------------------------------------------------------------
# port -> protocol-aware method, per transport. Deliberately small and
# explicit: an unmapped port falls to `connect` (never a pass), it does not
# guess a protocol from a port number and then score against the guess.
_TCP_METHODS = {
    53: "dns", 80: "http", 443: "https", 88: "krb", 389: "ldap",
    3268: "ldap", 3269: "ldaps", 636: "ldaps", 445: "smb", 3389: "rdp",
    8006: "https", 135: "dcerpc",
}
_UDP_METHODS = {
    53: "dns", 123: "ntp", 88: "krb", 389: "cldap", 137: "nbns", 67: "dhcp",
}

PROTOCOL_AWARE_METHODS = frozenset({
    "dns", "http", "https", "ldap", "ldaps", "ntp", "krb", "smb", "rdp",
    "dcerpc", "cldap", "nbns", "dhcp",
})

# ---------------------------------------------------------------------------
# Row 5's denominator classification (RULED 2026-09-05, orchestrator, as an
# implementation refinement of `docs/WORK_ORDER_fidelity_scorecard.md` §3.2's
# "row 5's denominator is the observed listen set").
#
# The denominator is the observed **service** listens on deployed hosts at or
# above the deploy threshold, MINUS ports classified and published here with
# a reason. Two things go wrong without this. A dynamic/ephemeral port is not
# a service at all -- a Windows RPC endpoint-mapper allocation observed once
# is an artefact of the capture window, and scoring the twin for not
# reproducing `57086/tcp` is scoring noise. And a port with no protocol-aware
# probe can only ever return `inconclusive`, which blocks row 5 from ever
# passing for a reason that has nothing to do with the twin.
#
# Every exclusion is COUNTED and LISTED with its reason (never a silent
# filter, LES-101): a reader can always see what was taken out and why.
#
# **Each exclusion has a matching entry in `docs/PRODUCT_GAPS.md`** (the
# honest-gap register, `docs/WORK_ORDER_protocol_faithful_services.md` S5):
# GAP-061 QUIC 443/udp, GAP-062 proprietary 9997/tcp (and 3389/udp, same
# category, different reason), GAP-064 the ephemeral/dynamic range + 7680.
# GAP-063 records the one that is deliberately NOT excluded -- 8006/tcp is
# served as real HTTPS and probed as such, and the gap is that it is not
# Proxmox. Add a row here and the register gets an entry, or the two drift
# and a reader learns the twin's limits from a code comment.
# ---------------------------------------------------------------------------
# IANA dynamic/private range. Anything at or above this is an ephemeral
# allocation, not a declared service.
EPHEMERAL_PORT_FLOOR = 49152

EXCLUDED_NOT_A_SERVICE = "not a service"
EXCLUDED_NO_PROBE = "no protocol-aware probe"

# port/transport -> (category, human reason). Small and explicit: an
# exclusion is a decision, so each one is named rather than inferred.
_EXCLUSIONS: dict[tuple[int, str], tuple[str, str]] = {
    # Windows Delivery Optimization peer-to-peer update cache: a client-side
    # transfer port, not a service the twin is meant to reproduce.
    (7680, "tcp"): (EXCLUDED_NOT_A_SERVICE,
                    "Windows Delivery Optimization peer cache, not a service"),
    # Splunk universal-forwarder management/receiver: proprietary framing
    # with no stdlib-expressible handshake.
    (9997, "tcp"): (EXCLUDED_NO_PROBE,
                    "proprietary Splunk forwarder protocol -- no "
                    "protocol-aware probe exists in stdlib"),
    # RDP's UDP transport is a session ACCELERATION channel negotiated
    # inside an already-established TCP session; there is no standalone
    # datagram handshake to send. The 3389/tcp fact carries RDP.
    (3389, "udp"): (EXCLUDED_NO_PROBE,
                    "RDP-UDP is negotiated inside an established TCP "
                    "session -- 3389/tcp carries the RDP fact"),
    # QUIC (GAP-061, D7 (a)). Nothing axRange deploys speaks HTTP/3 --
    # Debian-main nginx is built without it -- so the twin cannot return a
    # protocol-valid QUIC response, and there is no stdlib-expressible
    # Initial packet to send it. Left UNEXCLUDED it would sit permanently
    # `inconclusive` (the `udp-connect` fallback) and block row 5 from ever
    # passing for a reason that has nothing to do with the twin. 33 of the
    # 34 observed 443/udp listens are EXTERNAL, so this is the sink's
    # problem before it is a twin host's (work order §7.4).
    (443, "udp"): (EXCLUDED_NO_PROBE,
                   "QUIC/HTTP-3: no real HTTP/3 server on the twin without "
                   "an image bake, and no stdlib-expressible probe "
                   "(GAP-061)"),
}


def classify_listen_port(port: int, transport: str) -> tuple[str, str, str]:
    """`(method, exclusion_category, reason)` for one observed listen.

    `exclusion_category` is `""` when the port stays in row 5's denominator;
    otherwise it is `EXCLUDED_NOT_A_SERVICE` / `EXCLUDED_NO_PROBE` and
    `method` is `""`.
    """
    p, t = int(port), (transport or "tcp").lower()
    if p >= EPHEMERAL_PORT_FLOOR:
        return "", EXCLUDED_NOT_A_SERVICE, (
            f"port {p} is in the dynamic/ephemeral range (>= "
            f"{EPHEMERAL_PORT_FLOOR}) -- an allocation observed in the "
            "capture window, not a declared service"
        )
    hit = _EXCLUSIONS.get((p, t))
    if hit is not None:
        return "", hit[0], hit[1]
    return method_for_port(p, t), "", ""


def method_for_port(port: int, transport: str) -> str:
    """The probe method for one observed `(port, transport)`.

    `connect` for anything unmapped on TCP; `udp-connect` on UDP, which
    cannot even establish reachability (an unanswered UDP datagram is
    indistinguishable from a filtered one) and is therefore always
    inconclusive. Both are labelled `protocol_aware: False` and can never
    count as a pass.
    """
    t = (transport or "tcp").lower()
    if t == "udp":
        return _UDP_METHODS.get(int(port), "udp-connect")
    return _TCP_METHODS.get(int(port), "connect")


def target_key(host_id: str, port: int, transport: str) -> str:
    """The ONE stable id for a `(twin host, port, transport)` probe target.

    `runner.py` mints it when composing targets and `rows.py` re-derives it
    when joining results back to observed facts; two independent f-strings
    that must agree character for character is the join silently returning
    nothing (LES-037).
    """
    return f"{host_id}:{int(port)}/{transport}"


def parse_listen_port(spec: str) -> Optional[tuple[int, str]]:
    """`"53/udp"` -> `(53, "udp")`. `None` for anything unparseable, so one
    malformed export entry degrades that ONE fact rather than the row."""
    text = str(spec or "").strip()
    if "/" not in text:
        return None
    port_s, _, transport = text.partition("/")
    try:
        port = int(port_s)
    except (TypeError, ValueError):
        return None
    t = transport.strip().lower()
    if t not in ("tcp", "udp"):
        return None
    if not (0 < port < 65536):
        return None
    return port, t


# ---------------------------------------------------------------------------
# Targets and results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbeTarget:
    """One `(twin host, port, transport)` to probe, plus the OBSERVED fact it
    stands for and the peer it is probed FROM."""

    key: str                 # stable id: "<host_id>:<port>/<transport>"
    host_id: str             # the twin host being probed
    ip: str                  # its literal address on `segment`
    segment: str
    port: int
    transport: str
    method: str
    observed_node_id: Optional[str] = None   # the observed host this reproduces
    peer_host_id: Optional[str] = None       # the twin host the probe runs FROM
    peer_vmid: Optional[int] = None
    peer_is_router: bool = False

    @property
    def protocol_aware(self) -> bool:
        return self.method in PROTOCOL_AWARE_METHODS


@dataclass(frozen=True)
class ProbeResult:
    key: str
    status: str
    method: str
    protocol_aware: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "status": self.status, "method": self.method,
            "protocol_aware": self.protocol_aware, "detail": self.detail,
        }


@dataclass
class ProbeReport:
    """What `runner.py` hands `rows.py`. `ran=False` (the `--no-probe` case)
    is DIFFERENT from `ran=True` with zero results, and row 5 renders the two
    differently -- `not-computed` with a reason either way, but never the same
    reason."""

    ran: bool = False
    targets: list[ProbeTarget] = field(default_factory=list)
    results: dict[str, ProbeResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    listens: dict[str, list[str]] = field(default_factory=dict)  # row 4: host -> "port/proto"
    listens_ran: bool = False

    def result_for(self, key: str) -> Optional[ProbeResult]:
        return self.results.get(key)


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------
def check_containment(targets: list[ProbeTarget]) -> None:
    """Refuse to render if any target address is not an IP literal.

    The guest must never be handed a NAME to resolve: that would make a twin
    host depend on a resolver being up, leak twin naming into guest DNS, and
    -- the reason it is structural rather than stylistic -- turn a probe into
    something whose destination the orchestrator cannot state (LES-201).
    """
    for t in targets:
        try:
            ipaddress.ip_address(t.ip)
        except ValueError:
            raise ProbeRenderError(
                f"probe target {t.key}: address {t.ip!r} is not an IP literal "
                "-- every peer address must be resolved by the ORCHESTRATOR "
                "and baked into the render; the guest never resolves a name "
                "(docs/WORK_ORDER_fidelity_scorecard.md §3.3)"
            ) from None


# ---------------------------------------------------------------------------
# The rendered client
# ---------------------------------------------------------------------------
_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# axRange fidelity probe (row 5) -- generated, do not edit.
# Read-only: opens sockets, writes nothing, installs nothing.
import json, socket, ssl, struct, sys

TARGETS = __TARGETS__
TIMEOUT = __TIMEOUT__
BEGIN = "__BEGIN__"
END = "__END__"


def _tcp(ip, port):
    s = socket.create_connection((ip, port), TIMEOUT)
    s.settimeout(TIMEOUT)
    return s


def _udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    return s


def _der(tag, body):
    n = len(body)
    if n < 0x80:
        return bytes([tag, n]) + body
    ln = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(ln)]) + ln + body


def _int(v):
    n = max(1, (v.bit_length() + 8) // 8)
    return _der(0x02, v.to_bytes(n, "big"))


def _gstr(s):
    return _der(0x1b, s.encode())


def _ctx(num, body):
    return _der(0xa0 | num, body)


def probe_dns(ip, port, transport):
    q = (b"\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00"
         b"\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01")
    if transport == "udp":
        s = _udp()
        try:
            s.sendto(q, (ip, port))
            data, _ = s.recvfrom(2048)
        finally:
            s.close()
    else:
        s = _tcp(ip, port)
        try:
            s.sendall(struct.pack("!H", len(q)) + q)
            head = s.recv(2)
            if len(head) < 2:
                return "fail", "short DNS/TCP length prefix"
            data = s.recv(struct.unpack("!H", head)[0])
        finally:
            s.close()
    if len(data) < 12:
        return "fail", "DNS response shorter than a header"
    if data[0:2] != b"\\x12\\x34":
        return "fail", "DNS transaction id mismatch"
    if not (data[2] & 0x80):
        return "fail", "DNS QR bit not set (not a response)"
    return "pass", "DNS response rcode=%d" % (data[3] & 0x0f)


def probe_http(ip, port, transport):
    s = _tcp(ip, port)
    try:
        s.sendall(("GET / HTTP/1.0\\r\\nHost: %s\\r\\n"
                   "User-Agent: axrange-fidelity-probe\\r\\n\\r\\n" % ip).encode())
        data = s.recv(256)
    finally:
        s.close()
    if data[:5] != b"HTTP/":
        return "fail", "no HTTP status line (got %r)" % data[:24]
    return "pass", data.split(b"\\r\\n", 1)[0].decode("latin-1")


def probe_https(ip, port, transport):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = _tcp(ip, port)
    try:
        tls = ctx.wrap_socket(raw)
    except (ssl.SSLError, OSError) as e:
        try:
            raw.close()
        except Exception:
            pass
        return "fail", "TLS handshake failed: %s" % e
    # The handshake IS the protocol-valid response; an HTTP body on top is a
    # bonus, and failing to read one must not demote a completed handshake.
    ver = tls.version()
    data = b""
    try:
        tls.sendall(("GET / HTTP/1.0\\r\\nHost: %s\\r\\n\\r\\n" % ip).encode())
        data = tls.recv(256)
    except Exception:
        data = b""
    finally:
        try:
            tls.close()
        except Exception:
            pass
    if data[:5] != b"HTTP/":
        return "pass", "TLS %s established (no HTTP response read)" % ver
    return "pass", "TLS %s; %s" % (ver, data.split(b"\\r\\n", 1)[0].decode("latin-1"))


def probe_ntp(ip, port, transport):
    s = _udp()
    try:
        s.sendto(b"\\x1b" + 47 * b"\\0", (ip, port))
        data, _ = s.recvfrom(256)
    finally:
        s.close()
    if len(data) < 48:
        return "fail", "NTP response shorter than 48 bytes"
    mode = data[0] & 0x07
    if mode != 4:
        return "fail", "NTP mode %d, expected 4 (server)" % mode
    return "pass", "NTP server mode, stratum %d" % data[1]


def _as_req():
    # AS-REQ [APPLICATION 10] over a KDC-REQ-BODY naming a principal that is
    # not expected to exist: a real KDC answers KRB-ERROR (or AS-REP), and
    # EITHER is a protocol-valid response. `till`/`nonce` are FIXED literals
    # so the render stays byte-deterministic.
    def principal(ntype, parts):
        names = b"".join(_gstr(p) for p in parts)
        return _der(0x30, _ctx(0, _int(ntype)) + _ctx(1, _der(0x30, names)))
    body = (
        _ctx(0, _der(0x03, b"\\x00\\x00\\x00\\x00\\x10")) +
        _ctx(1, principal(1, ["axrange-fidelity-probe"])) +
        _ctx(2, _gstr("AXRANGE.PROBE")) +
        _ctx(3, principal(2, ["krbtgt", "AXRANGE.PROBE"])) +
        _ctx(5, _der(0x18, b"20370101000000Z")) +
        _ctx(7, _int(1234567)) +
        _ctx(8, _der(0x30, _int(18) + _int(17) + _int(23)))
    )
    req = _ctx(1, _int(5)) + _ctx(2, _int(10)) + _ctx(4, _der(0x30, body))
    return _der(0x6a, _der(0x30, req))


def probe_krb(ip, port, transport):
    msg = _as_req()
    if transport == "udp":
        s = _udp()
        try:
            s.sendto(msg, (ip, port))
            data, _ = s.recvfrom(4096)
        finally:
            s.close()
    else:
        s = _tcp(ip, port)
        try:
            s.sendall(struct.pack("!I", len(msg)) + msg)
            head = s.recv(4)
            if len(head) < 4:
                return "fail", "short Kerberos/TCP length prefix"
            data = s.recv(min(65535, struct.unpack("!I", head)[0]))
        finally:
            s.close()
    if not data:
        return "fail", "no Kerberos response"
    tag = data[0]
    if tag == 0x7e:
        return "pass", "KRB-ERROR (a protocol-valid KDC response)"
    if tag == 0x6b:
        return "pass", "AS-REP"
    return "fail", "unexpected Kerberos application tag 0x%02x" % tag


def _ldap_op_tag(data):
    """The protocolOp tag of an LDAPMessage, or None if it does not parse.
    SEQUENCE { messageID INTEGER, protocolOp [APPLICATION n] ... }."""
    if len(data) < 5 or data[0] != 0x30:
        return None
    i = 1
    if data[i] & 0x80:
        i += 1 + (data[i] & 0x7f)
    else:
        i += 1
    if i >= len(data) or data[i] != 0x02:
        return None
    i += 1
    if i >= len(data):
        return None
    i += 1 + data[i]
    if i >= len(data):
        return None
    return data[i]


def probe_ldap(ip, port, transport, tls=False):
    # LDAPMessage { messageID 1, SearchRequest baseObject "" scope base
    # filter present("objectClass") attributes { supportedLDAPVersion } }
    search = (
        _der(0x04, b"") + _der(0x0a, b"\\x00") + _der(0x0a, b"\\x00") +
        _int(0) + _int(0) + _der(0x01, b"\\x00") +
        _der(0x87, b"objectClass") +
        _der(0x30, _der(0x04, b"supportedLDAPVersion"))
    )
    msg = _der(0x30, _int(1) + _der(0x63, search))
    raw = _tcp(ip, port)
    sock = raw
    try:
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(raw)
        sock.sendall(msg)
        data = sock.recv(4096)
    except ssl.SSLError as e:
        raw.close()
        return "fail", "LDAPS handshake failed: %s" % e
    finally:
        try:
            sock.close()
        except Exception:
            pass
    op = _ldap_op_tag(data)
    if op is None:
        return "fail", "no parseable LDAPMessage in response (got %r)" % data[:16]
    # 0x61 BindResponse, 0x64 SearchResultEntry, 0x65 SearchResultDone,
    # 0x73 SearchResultReference, 0x78 ExtendedResponse -- any of them is a
    # protocol-valid answer; "the directory refused" is still LDAP speaking.
    if op not in (0x61, 0x64, 0x65, 0x73, 0x78):
        return "fail", "unexpected LDAP protocolOp tag 0x%02x" % op
    return "pass", "LDAP rootDSE search answered, protocolOp 0x%02x" % op


def probe_ldaps(ip, port, transport):
    return probe_ldap(ip, port, transport, tls=True)


def probe_smb(ip, port, transport):
    # SMB2 NEGOTIATE over a NetBIOS session header.
    # SMB2 sync header, EXACTLY 64 bytes: ProtocolId 4, StructureSize 2,
    # CreditCharge 2, Status 4, Command 2, CreditRequest 2, Flags 4,
    # NextCommand 4, MessageId 8, Reserved 4, TreeId 4, SessionId 8,
    # Signature 16. A header that is not 64 bytes is dropped by a real
    # server, which would read as "nothing answers" -- the exact false
    # negative row 5 must not manufacture.
    header = (b"\\xfeSMB" + struct.pack("<HH", 64, 0) + struct.pack("<I", 0) +
              struct.pack("<HH", 0, 1) + struct.pack("<I", 0) +
              struct.pack("<I", 0) + struct.pack("<Q", 0) +
              struct.pack("<I", 0) + struct.pack("<I", 0) +
              struct.pack("<Q", 0) + 16 * b"\\0")
    # NEGOTIATE request: StructureSize 36, DialectCount 2, SecurityMode 1,
    # Reserved, Capabilities, ClientGuid 16, ClientStartTime 8, then the
    # dialects (SMB 2.0.2 / 2.1).
    body = (struct.pack("<HH", 36, 2) + struct.pack("<HH", 1, 0) +
            struct.pack("<I", 0) + 16 * b"\\0" + struct.pack("<Q", 0) +
            struct.pack("<HH", 0x0202, 0x0210))
    msg = header + body
    s = _tcp(ip, port)
    try:
        s.sendall(struct.pack("!I", len(msg)) + msg)
        data = s.recv(1024)
    finally:
        s.close()
    if len(data) < 8:
        return "fail", "no SMB2 response"
    if data[4:8] != b"\\xfeSMB":
        return "fail", "response is not SMB2 (got %r)" % data[4:8]
    return "pass", "SMB2 NEGOTIATE answered (%d bytes)" % len(data)


def probe_rdp(ip, port, transport):
    # X.224 Connection Request with an RDP negotiation request.
    x224 = b"\\xe0\\x00\\x00\\x00\\x00\\x00" + b"\\x01\\x00\\x08\\x00\\x00\\x00\\x00\\x00"
    tpkt = b"\\x03\\x00" + struct.pack("!H", 4 + 1 + len(x224)) + bytes([len(x224)]) + x224
    s = _tcp(ip, port)
    try:
        s.sendall(tpkt)
        data = s.recv(512)
    finally:
        s.close()
    if len(data) < 6 or data[0] != 0x03:
        return "fail", "no TPKT response (got %r)" % data[:8]
    if data[5] != 0xd0:
        return "fail", "X.224 code 0x%02x, expected 0xd0 (connection confirm)" % data[5]
    return "pass", "X.224 connection confirm"


def probe_cldap(ip, port, transport):
    # CLDAP (RFC 1798 / MS-ADTS): the SAME LDAP rootDSE SearchRequest, sent
    # as a single UDP datagram. An AD DC answers a connectionless rootDSE
    # search; a plain LDAP server usually does not, which is itself a
    # protocol-level answer about what the twin reproduces.
    search = (
        _der(0x04, b"") + _der(0x0a, b"\\x00") + _der(0x0a, b"\\x00") +
        _int(0) + _int(0) + _der(0x01, b"\\x00") +
        _der(0x87, b"objectClass") +
        _der(0x30, _der(0x04, b"supportedLDAPVersion"))
    )
    msg = _der(0x30, _int(1) + _der(0x63, search))
    s = _udp()
    try:
        s.sendto(msg, (ip, port))
        data, _ = s.recvfrom(4096)
    finally:
        s.close()
    op = _ldap_op_tag(data)
    if op is None:
        return "fail", "no parseable LDAPMessage in CLDAP response (got %r)" % data[:16]
    if op not in (0x61, 0x64, 0x65, 0x73, 0x78):
        return "fail", "unexpected CLDAP protocolOp tag 0x%02x" % op
    return "pass", "CLDAP rootDSE search answered, protocolOp 0x%02x" % op


def probe_nbns(ip, port, transport):
    # NBNS (RFC 1002) NBSTAT node-status query for the wildcard name `*` --
    # the query `nbtscan` sends. A NetBIOS name service answers with the
    # same transaction id, the response bit set, and its name table.
    name = b"*" + b"\\x00" * 15
    encoded = bytearray()
    for byte in name:
        encoded.append(0x41 + (byte >> 4))
        encoded.append(0x41 + (byte & 0x0F))
    q = (b"\\x4e\\x42" + b"\\x00\\x00" + b"\\x00\\x01" + b"\\x00\\x00" +
         b"\\x00\\x00" + b"\\x00\\x00" +
         bytes([len(encoded)]) + bytes(encoded) + b"\\x00" +
         b"\\x00\\x21" + b"\\x00\\x01")
    s = _udp()
    try:
        s.sendto(q, (ip, port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()
    if len(data) < 12:
        return "fail", "NBNS response shorter than a header"
    if data[0:2] != b"\\x4e\\x42":
        return "fail", "NBNS transaction id mismatch"
    if not (data[2] & 0x80):
        return "fail", "NBNS response bit not set"
    return "pass", "NBNS node-status answered (%d bytes)" % len(data)


def probe_dhcp(ip, port, transport):
    # DHCPINFORM (RFC 2131): a client that already has an address asking for
    # configuration. Chosen over DISCOVER deliberately -- INFORM asks a
    # server to answer without allocating anything, so the probe cannot
    # consume a lease from the twin's own pool.
    xid = b"\\xa1\\x02\\x03\\x04"
    pkt = bytearray()
    pkt += b"\\x01\\x01\\x06\\x00" + xid + b"\\x00\\x00" + b"\\x00\\x00"
    pkt += b"\\x00\\x00\\x00\\x00"                                   # ciaddr
    pkt += b"\\x00" * 12                                          # yi/si/giaddr
    pkt += b"\\x02\\x00\\x00\\xaa\\xbb\\xcc" + b"\\x00" * 10            # chaddr
    pkt += b"\\x00" * 192                                         # sname + file
    pkt += b"\\x63\\x82\\x53\\x63"                                   # magic cookie
    pkt += b"\\x35\\x01\\x08"                                       # DHCP INFORM
    pkt += b"\\x37\\x02\\x01\\x03"                                   # param req list
    pkt += b"\\xff"
    s = _udp()
    try:
        s.sendto(bytes(pkt), (ip, port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()
    if len(data) < 240:
        return "fail", "DHCP response shorter than a BOOTP header + cookie"
    if data[0] != 0x02:
        return "fail", "BOOTP op %d, expected 2 (BOOTREPLY)" % data[0]
    if data[4:8] != xid:
        return "fail", "DHCP transaction id mismatch"
    if data[236:240] != b"\\x63\\x82\\x53\\x63":
        return "fail", "no DHCP magic cookie in response"
    return "pass", "DHCP server answered a DHCPINFORM (%d bytes)" % len(data)


def probe_dcerpc(ip, port, transport):
    # DCE-RPC (MS-RPCE) bind to the Endpoint Mapper interface
    # (e1af8308-5d1f-11c9-91a4-08002b14a0fa v3.0), NDR32 transfer syntax.
    # A real endpoint mapper answers bind_ack (0x0c); a server that refuses
    # the abstract syntax answers bind_nak (0x0d) -- BOTH are protocol-valid
    # DCE-RPC responses, which is what row 5 asks for.
    ctx = (struct.pack("<HBB", 0, 1, 0) +
           b"\\x08\\x83\\xaf\\xe1\\x1f\\x5d\\xc9\\x11\\x91\\xa4\\x08\\x00\\x2b\\x14\\xa0\\xfa" +
           struct.pack("<HH", 3, 0) +
           b"\\x04\\x5d\\x88\\x8a\\xeb\\x1c\\xc9\\x11\\x9f\\xe8\\x08\\x00\\x2b\\x10\\x48\\x60" +
           struct.pack("<I", 2))
    body = struct.pack("<HHI", 5840, 5840, 0) + struct.pack("<I", 1) + ctx
    header = (b"\\x05\\x00\\x0b\\x03" + b"\\x10\\x00\\x00\\x00" +
              struct.pack("<HH", 16 + len(body), 0) +
              struct.pack("<I", 1))
    msg = header + body
    s = _tcp(ip, port)
    try:
        s.sendall(msg)
        data = s.recv(1024)
    finally:
        s.close()
    if len(data) < 4:
        return "fail", "no DCE-RPC response"
    if data[0] != 0x05:
        return "fail", "not DCE-RPC (major version %d)" % data[0]
    ptype = data[2]
    if ptype == 0x0c:
        return "pass", "DCE-RPC bind_ack"
    if ptype == 0x0d:
        return "pass", "DCE-RPC bind_nak (a protocol-valid refusal)"
    if ptype == 0x03:
        return "pass", "DCE-RPC fault (a protocol-valid response)"
    return "fail", "unexpected DCE-RPC PDU type 0x%02x" % ptype


def probe_connect(ip, port, transport):
    s = _tcp(ip, port)
    s.close()
    return "inconclusive", "TCP connect succeeded (not protocol-aware)"


def probe_udp_connect(ip, port, transport):
    return ("inconclusive",
            "UDP with no protocol-aware method: silence is indistinguishable "
            "from a filtered port (not protocol-aware)")


METHODS = {
    "dns": probe_dns, "http": probe_http, "https": probe_https,
    "ntp": probe_ntp, "krb": probe_krb, "ldap": probe_ldap,
    "ldaps": probe_ldaps, "smb": probe_smb, "rdp": probe_rdp,
    "cldap": probe_cldap, "nbns": probe_nbns, "dhcp": probe_dhcp,
    "dcerpc": probe_dcerpc,
    "connect": probe_connect, "udp-connect": probe_udp_connect,
}

out = []
for t in TARGETS:
    fn = METHODS.get(t["method"])
    if fn is None:
        out.append({"key": t["key"], "status": "fail",
                    "detail": "no probe method %r" % t["method"]})
        continue
    try:
        status, detail = fn(t["ip"], t["port"], t["transport"])
    except socket.timeout:
        status, detail = "fail", "timed out after %ss" % TIMEOUT
    except OSError as e:
        status, detail = "fail", "%s" % e
    except Exception as e:
        status, detail = "fail", "%s: %s" % (type(e).__name__, e)
    out.append({"key": t["key"], "status": status, "detail": detail})

sys.stdout.write(BEGIN + "\\n")
sys.stdout.write(json.dumps({"results": out}, sort_keys=True) + "\\n")
sys.stdout.write(END + "\\n")
'''


def render_probe_script(
    targets: list[ProbeTarget], *, timeout_s: float = DEFAULT_TARGET_TIMEOUT_S,
) -> str:
    """Render the in-guest probe for `targets`.

    Byte-deterministic for fixed input: the ONLY interpolated content is the
    sorted target list, the timeout and the two markers -- no timestamp, no
    hostname, no random nonce (the Kerberos `nonce`/`till` are fixed
    literals in the template for exactly this reason).
    """
    if not targets:
        raise ProbeRenderError(
            "render_probe_script: empty target set -- an empty probe would "
            "report a vacuous 100% (LES-101); the caller must render row 5 "
            "as not-computed WITH its denominator instead"
        )
    check_containment(targets)
    payload = [
        {
            "key": t.key, "ip": t.ip, "port": int(t.port),
            "transport": t.transport, "method": t.method,
        }
        for t in sorted(targets, key=lambda x: x.key)
    ]
    return (
        _SCRIPT_TEMPLATE
        .replace("__TARGETS__", json.dumps(payload, sort_keys=True, indent=None))
        .replace("__TIMEOUT__", repr(float(timeout_s)))
        .replace("__BEGIN__", BEGIN_MARKER)
        .replace("__END__", END_MARKER)
    )


def parse_probe_output(stdout: str, targets: list[ProbeTarget]) -> dict[str, ProbeResult]:
    """Parse the framed JSON the guest printed into `{key: ProbeResult}`.

    Raises `ProbeRenderError` when the framing is absent or the payload is
    not the expected shape -- a probe that produced no readable result must
    fail loud, never degrade to "no results, so everything passed"
    (LES-001).
    """
    text = stdout or ""
    if BEGIN_MARKER not in text or END_MARKER not in text:
        raise ProbeRenderError(
            "probe produced no framed result "
            f"({BEGIN_MARKER}/{END_MARKER} missing) -- got {text[:200]!r}"
        )
    body = text.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise ProbeRenderError(f"probe result is not JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise ProbeRenderError(f"probe result has no `results` list: {body[:200]!r}")

    by_key = {t.key: t for t in targets}
    out: dict[str, ProbeResult] = {}
    for entry in doc["results"]:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        target = by_key.get(key)
        if target is None:
            continue
        status = str(entry.get("status") or FAIL)
        # A protocol-UNAWARE method can never report `pass`, whatever the
        # guest said -- enforced HERE as well as in the template, so a future
        # template edit cannot quietly promote a bare connect into a gate
        # pass (§3.3).
        if status == PASS and not target.protocol_aware:
            status = INCONCLUSIVE
        out[key] = ProbeResult(
            key=key, status=status, method=target.method,
            protocol_aware=target.protocol_aware,
            detail=str(entry.get("detail") or ""),
        )
    return out


# ---------------------------------------------------------------------------
# Row 4 (deferred; `--include-deferred`)
# ---------------------------------------------------------------------------
LISTEN_COMMAND = "ss -lnH -tu"


def parse_listen_output(stdout: str) -> list[str]:
    """`ss -lnH -tu` -> a sorted, de-duplicated `["53/udp", "22/tcp", ...]`.

    `iproute2` is on the base image, so no install is needed. Only the local
    address column is read; the peer column and every process/credential
    column `ss` can be asked for are deliberately NOT requested (§6 S4: no
    credential reaches the document).
    """
    seen: set[str] = set()
    for line in (stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        transport = parts[0].lower()
        if transport not in ("tcp", "udp"):
            continue
        local = parts[4]
        _, _, port_s = local.rpartition(":")
        try:
            port = int(port_s)
        except (TypeError, ValueError):
            continue
        seen.add(f"{port}/{transport}")
    return sorted(seen, key=lambda s: (int(s.split("/")[0]), s))
