"""Persona schema + loader (docs/design/07-traffic-personas.md,
docs/WORK_ORDER_persona_library.md §2).

A persona is a reusable, twin-agnostic, **benign** behavioral archetype: it
declares WHAT traffic a role emits (`emits[]`) and HOW code drives it
(`drive`), not WHERE it runs or WHO it's bound to (that binding is the future
Traffic tier's job — see `docs/design/07-traffic-personas.md`). Attacker
TTPs are never personas (they live in the Exercise tier).

This module is a pure data/shape layer: `load_persona()` turns a raw dict
(as parsed from a `catalog/personas/*.yaml` file) into a `Persona`, coercing
obviously-scalar fields and raising `PersonaSchemaError` on a shape the
loader itself can't make sense of (missing id, `emits` not a list, ...).
SEMANTIC validation (enum membership, known protocols, dangling peer_role,
well-formed cadence, `requires` satisfiability) is deliberately NOT done
here — that's `axrange.validator.checks.personas`'s job (the PSN-0xx rule
catalog; `personas/` stays a pure leaf the validator reads, mirroring how
`axrange.validator.checks.references` reads the sibling `axrange.sensors`
registry — validator -> registry, never the reverse), mirroring the IR
parser/validator split (`axrange.ir.parser` does shape, `axrange.validator`
does semantics) so a syntactically-loadable-but-semantically-bad persona can
still be loaded and then exercised against every PSN rule in a test, instead
of failing at parse time with no findings to assert against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Closed drive/os_family vocabularies (PSN-020/030 enforce these; kept here,
# not just in the validator, so a driver factory — axrange.personas.drivers —
# can do `if persona.drive not in DRIVE_KINDS: raise` without importing the
# validator).
OS_FAMILIES = ("windows", "linux")
# `command` (2026-09-02, docs/WORK_ORDER_protocol_faithful_traffic.md) is
# the protocol-faithful sibling of `scripted`: it runs a REAL client tool
# (dig/curl/ntpdate/ping) at each emit's firing instant instead of pushing
# tagged filler bytes at the right port, so a capture records the protocol
# named in `proto:` rather than bare tcp/udp. Additive: PSN-030's enum
# widened, no rule was added or removed (still 114).
#
# `browser` (2026-09-21, docs/WORK_ORDER_protocol_engines.md §5 E4) is the
# web-stack engine: a real headless Chromium at each firing instant, so ONE
# driver produces DNS + a real TLS handshake (real SNI, real JA3/JA4) +
# HTTP/1.1 + HTTP/2 + QUIC/HTTP-3. Additive on the same terms -- PSN-030's
# enum widened again, rule COUNT still 114.
DRIVE_KINDS = ("scripted", "replay", "real-service", "command", "browser")
CADENCE_KINDS = ("diurnal", "24x7")  # + "cyclic:<duration>" / "burst:<duration>"
# `bulk` (2026-07-28) is the elephant-flow tier above `high` — see
# `drivers.scripted._VOLUME_PROFILE`, where a volume level now resolves to
# (messages, BYTES-per-message, inter-message gap) instead of a message count
# alone. Pre-2026-07-28 `volume:` controlled connection COUNT only and never
# byte size, so no persona could express "a 2 GB transfer" at all.
VOLUME_LEVELS = ("low", "med", "high", "bulk")

# Closed transport vocabulary (PSN-100). An emit that leaves `transport:`
# unset derives it from `proto` via `default_transport()` — so every
# pre-2026-07-28 persona keeps working unchanged while datagram protocols
# stop being mis-driven over TCP.
TRANSPORTS = ("tcp", "udp")

# Closed peer-scope vocabulary (PSN-110). `one` (the default, and every
# pre-2026-07-28 persona's behavior) drives a single resolved peer per emit;
# `all` fans the emit out across EVERY twin host carrying that role, which is
# what a subnet-sweeping scanner or a peer-to-peer mesh actually does.
PEER_SCOPES = ("one", "all")

# Reserved `peer_role` tokens — a peer that is not a role the twin publishes.
# `egress`/`internet` mean "outside the twin" (no peer IP is threaded; the
# guest client logs-and-skips). `broadcast`/`multicast` (2026-07-28) mean
# "the local segment itself": the scripted driver resolves them to a literal
# wire address (255.255.255.255, or the proto's well-known multicast group)
# rather than to any twin host, which is what makes segment-wide chatter
# (mDNS/LLMNR/NBNS/SSDP/DHCP) expressible at all.
# See `docs/design/07-traffic-personas.md`.
RESERVED_PEER_ROLES = ("egress", "internet", "broadcast", "multicast")

# Open-but-curated protocol vocabulary (PSN-040) — covers every proto named
# in the §5.2 starter catalog. New personas needing a proto not yet listed
# add it here (open registry, like axrange.sensors' SENSOR_TYPES) rather than
# working around PSN-040.
KNOWN_PROTOS = frozenset(
    {
        "dns", "kerberos", "ldap", "ldaps", "smb", "nfs", "rpc", "ntp",
        "drsuapi", "http", "https", "smtp", "imap", "imaps", "pop3", "print",
        "git", "ssh", "rdp",
        "mysql", "postgres", "mssql", "rsync", "borg",
        "modbus", "dnp3", "ethernet-ip", "opcua", "mqtt", "coap",
        "ike", "ipsec", "wireguard", "ssl-vpn",
        "snmp", "syslog", "netflow", "ftp", "tls", "tcp", "udp", "icmp",
        # 2026-07-28 — the high-volume persona wave: real-time media
        # (sip/rtp/stun), link-local segment chatter (mdns/llmnr/nbns/ssdp/
        # dhcp/arp), and log shipping (beats).
        "sip", "rtp", "stun",
        "mdns", "llmnr", "nbns", "ssdp", "hsrp", "dhcp", "arp",
        # 2026-08-27 -- router-redundancy chatter, the `network-device` signature.
        "hsrp",
        "beats",
        # 2026-09-21 -- the browser engine (docs/WORK_ORDER_protocol_engines.md
        # §5 E4). `quic` is the HTTP/3-over-UDP emit a `drive: browser` persona
        # authors when it wants the origin forced to QUIC; plain `https` stays
        # the TCP TLS/ALPN (HTTP/1.1 or HTTP/2) form. There is deliberately NO
        # second `http3` spelling -- two tokens for one thing is how half a
        # catalog's HTTP/3 emits end up under each.
        "quic",
    }
)

# Protocols whose real-world transport is UDP — used by `default_transport()`
# to derive an unset `emits[].transport`. Everything not listed defaults to
# TCP (the pre-2026-07-28 behavior, which was TCP for EVERY emit).
#
# NOTE this CHANGES the driven transport of some pre-existing catalog emits
# (`dns`, `ntp`, `snmp`, `syslog`, `netflow`, `ike`, `wireguard`) from TCP to
# UDP. That is a deliberate fidelity fix, not an accident: driving DNS over a
# TCP connect to port 53 produced traffic that looked nothing like DNS on a
# capture. An emit can still pin the transport explicitly (`transport: tcp`)
# where the TCP form is the realistic one (e.g. DNS zone transfer).
_UDP_PROTOS = frozenset(
    {
        "dns", "ntp", "snmp", "syslog", "netflow", "dhcp",
        "mdns", "llmnr", "nbns", "ssdp",
        "coap", "rtp", "stun", "sip",
        "ike", "wireguard",
        # QUIC is HTTP/3's transport and is UDP by definition -- there is no
        # TCP form of it (that form is spelled `https`).
        "quic",
    }
)


def default_transport(proto: str) -> str:
    """The realistic transport for `proto` when an emit doesn't pin one."""
    return "udp" if proto in _UDP_PROTOS else "tcp"


class PersonaSchemaError(Exception):
    """A persona dict the loader can't make sense of (missing/malformed shape)."""


@dataclass(frozen=True)
class Emit:
    """One traffic-signature entry (docs/design/07-traffic-personas.md)."""

    proto: str
    ports: tuple[int, ...] = ()
    peer_role: str = ""
    direction: str = ""       # "EW" | "NS" — descriptive only (§4.1's tap-point
    # model drops direction; this is an UNRELATED, persona-level concept: what
    # a flow's traffic looks like, not where a tap sits).
    cadence: str = "24x7"
    volume: str = "low"
    # "I talk to this peer IF PRESENT" (a dedicated DNS resolver, a print
    # server, ...) vs. the default "I structurally can't function without
    # this peer" (a domain-joined workstation NEEDS its DC). Additive,
    # backward-compatible: absent/omitted -> False -> the pre-2026-07-25
    # behavior (every non-reserved peer_role is a hard peer). Refines
    # PSN-090 (validator/checks/personas.py) and `_resolve_peers`
    # (o2control/traffic.py) -- see docs/WORK_ORDER_traffic_tier.md.
    optional: bool = False
    # --- 2026-07-28, the high-volume persona wave (all three additive; an
    # omitted value reproduces the pre-2026-07-28 behavior exactly) ---
    # "tcp" | "udp"; "" -> derived from `proto` via `default_transport()`.
    # Validated by PSN-100. The scripted driver sends a UDP emit as
    # datagrams (chunked at the MTU) instead of a TCP connect/send/close,
    # which is both more realistic AND the only way to reach a high packet
    # rate -- a TCP connect costs ~9 packets to carry one small payload.
    transport: str = ""
    # Bytes per message; 0 -> derived from `volume` via the driver's
    # `_VOLUME_PROFILE`. An explicit value overrides the volume default for
    # a persona whose byte size is its defining trait (a 4 KB syslog event
    # vs. a 200 MB container layer) without dragging the message COUNT and
    # inter-message gap along with it.
    bytes_per_msg: int = 0
    # "one" (default) | "all" -- see PEER_SCOPES. `all` makes the emit fan
    # out across every twin host holding `peer_role`, which is what a
    # subnet-sweeping scanner or a P2P mesh does; `_resolve_peer_sets`
    # (o2control/traffic.py) supplies the full list.
    peer_scope: str = "one"

    def effective_transport(self) -> str:
        """This emit's transport, resolving the derive-from-proto default."""
        return self.transport or default_transport(self.proto)


@dataclass(frozen=True)
class Persona:
    """One persona catalog entry."""

    id: str
    os_family: str
    drive: str
    emits: tuple[Emit, ...] = ()
    requires: tuple[str, ...] = ()
    knobs: dict[str, Any] = field(default_factory=dict)


def _load_emit(idx: int, raw: Any) -> Emit:
    if not isinstance(raw, dict):
        raise PersonaSchemaError(f"emits[{idx}] is not a mapping: {raw!r}")
    proto = raw.get("proto")
    if not proto:
        raise PersonaSchemaError(f"emits[{idx}] is missing proto")
    ports_raw = raw.get("ports") or []
    if not isinstance(ports_raw, (list, tuple)):
        raise PersonaSchemaError(f"emits[{idx}].ports must be a list, got {ports_raw!r}")
    try:
        ports = tuple(int(p) for p in ports_raw)
    except (TypeError, ValueError) as e:
        raise PersonaSchemaError(f"emits[{idx}].ports has a non-integer entry: {e}")
    # Shape-only coercion, per this module's parser/validator split: a
    # non-integer `bytes:` is a shape the loader genuinely can't make sense
    # of (like a non-integer port), whereas an out-of-enum `transport:`/
    # `peer_scope:` is well-shaped-but-wrong and belongs to PSN-100/PSN-110.
    bytes_raw = raw.get("bytes") or 0
    try:
        bytes_per_msg = int(bytes_raw)
    except (TypeError, ValueError) as e:
        raise PersonaSchemaError(f"emits[{idx}].bytes is not an integer: {e}")
    if bytes_per_msg < 0:
        raise PersonaSchemaError(
            f"emits[{idx}].bytes must be >= 0, got {bytes_per_msg}"
        )
    return Emit(
        proto=str(proto),
        ports=ports,
        peer_role=str(raw.get("peer_role") or ""),
        direction=str(raw.get("direction") or ""),
        cadence=str(raw.get("cadence") or "24x7"),
        volume=str(raw.get("volume") or "low"),
        optional=bool(raw.get("optional") or False),
        transport=str(raw.get("transport") or ""),
        bytes_per_msg=bytes_per_msg,
        peer_scope=str(raw.get("peer_scope") or "one"),
    )


def load_persona(raw: dict[str, Any]) -> Persona:
    """Parse one persona dict (the value under a catalog file's top-level
    `persona:` key, or the dict itself if it has no such wrapper). Raises
    PersonaSchemaError on a shape the loader can't parse — NOT on a
    semantically-bad-but-well-shaped value (an unknown os_family, a dangling
    peer_role, ...); those are PSN-0xx findings from
    `axrange.validator.checks.personas`, checked separately so a
    malformed-but-loadable persona can be validated."""
    if not isinstance(raw, dict):
        raise PersonaSchemaError(f"persona must be a mapping, got {raw!r}")
    body = raw.get("persona") if "persona" in raw and isinstance(raw.get("persona"), dict) else raw
    pid = body.get("id")
    if not pid:
        raise PersonaSchemaError("persona is missing a non-empty id")
    emits_raw = body.get("emits") or []
    if not isinstance(emits_raw, (list, tuple)):
        raise PersonaSchemaError(f"persona {pid!r}: emits must be a list, got {emits_raw!r}")
    emits = tuple(_load_emit(i, e) for i, e in enumerate(emits_raw))
    requires_raw = body.get("requires") or []
    if not isinstance(requires_raw, (list, tuple)):
        raise PersonaSchemaError(f"persona {pid!r}: requires must be a list, got {requires_raw!r}")
    knobs = body.get("knobs") or {}
    if not isinstance(knobs, dict):
        raise PersonaSchemaError(f"persona {pid!r}: knobs must be a mapping, got {knobs!r}")
    return Persona(
        id=str(pid),
        os_family=str(body.get("os_family") or ""),
        drive=str(body.get("drive") or ""),
        emits=emits,
        requires=tuple(str(r) for r in requires_raw),
        knobs=dict(knobs),
    )
