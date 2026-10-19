"""Normalized event model (INCUBATING — unversioned by design; CONVENTIONS.md §1).

All ingest plugins emit these. Four classes + sensor health; see DESIGN_BRIEF §4.
Fields are indicative and may change freely during incubation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Normalized transport outcome, one level above whatever state string the sensor
# uses. The brief's three-way split (attempted / answered-refused / established,
# §4) is the scanner-vs-real-host discriminator, but it has no room for the
# *reset after establishment* states that dominate a real capture — those are
# neither "never answered" nor "ran to a clean close", and folding them into
# either one loses the distinction. Closed vocabulary: a sensor state outside it
# maps to "unknown", counted in window health as `unknown_state_flows` and never
# bucketed with a neighbour (house rule: an unrecognized shape is reported, not
# passed through). That counter is what makes "closed table" a checkable claim
# rather than a comment: a new sensor state shows up as a number, not as a silent
# reclassification only visible by scanning every node's `state_mix`.
FLOW_STATE_CLASSES = (
    "established",   # responder accepted; ran to a normal or still-open end
    "reset_orig",    # established, then the originator aborted it
    "reset_resp",    # established, then the responder aborted it
    "refused",       # responder answered the attempt with a refusal
    "attempted",     # no reply from the responder at all
    "partial",       # only one direction seen — a vantage fact, not a host fact
    "other",         # midstream; no handshake observed
    "unknown",       # sensor state not in this vocabulary
)

#: Classes that prove the responder *accepted* the connection, i.e. a real
#: listener. "refused" is deliberately excluded: it proves the host is up, not
#: that the port is open. Membership test only — never iterated into an output.
FLOW_ACCEPTED = frozenset({"established", "reset_orig", "reset_resp"})


@dataclass
class Event:
    ts: float  # epoch seconds


@dataclass
class FlowEvent(Event):
    """One summarized flow (zeek conn.log row shape). Consumed by accumulators only."""
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    proto: str = ""          # transport: tcp/udp/icmp
    service: str = ""        # L7 label if the sensor knew it (dns, ssl, smb, ...)
    duration: float = 0.0
    bytes_out: int = 0       # originator -> responder
    bytes_in: int = 0
    pkts_out: int = 0
    pkts_in: int = 0
    state: str = ""          # sensor conn state verbatim: SF, S0, REJ, RSTR, RSTO, OTH, ...
    state_class: str = "unknown"  # normalized outcome; plugins MUST set it (FLOW_STATE_CLASSES)
    originator_is_src: bool = True
    #: The SENSOR's own judgment on whether each endpoint sits inside the monitored network
    #: (Zeek `conn.log`'s `local_orig`/`local_resp`, derived from its `Site::local_nets`).
    #: Named for this dataclass's src/dst vocabulary rather than Zeek's orig/resp, same as
    #: `src_ip`/`bytes_out` above.
    #:
    #: `None` means the sensor said NOTHING — **not** "external" (`LES-006`). The distinction is
    #: load-bearing: this field is the input to segment extraction, and a sensor that never
    #: declared `local_nets` would otherwise place every host outside the twin rather than
    #: admitting it does not know. A consumer that wants a verdict where this is `None` must
    #: impute one and say so; `windowing/segments.py` is where that happens, and it marks the
    #: result `imputed` rather than folding it in here.
    #:
    #: Why the sensor's judgment beats an RFC1918 test downstream: it reflects how the network
    #: is actually addressed. A site using public space internally, or carving only part of
    #: 10/8 into the monitored estate, is invisible to an address-shape heuristic and correct
    #: here.
    local_src: Optional[bool] = None
    local_dst: Optional[bool] = None
    #: 802.1Q VLAN id per DIRECTION, and TWO fields rather than one on purpose.
    #: `docs/WORK_ORDER_axquery_ingest.md` D3: axquery carries `OrigVlanId`/`RespVlanId`
    #: separately and states why in its own source -- *"Asymmetric VLAN tagging happens
    #: (capture point sees one side trunked, the other access), so a single connection-level
    #: VLAN was lossy."* Its REST `vlanId` key is the orig-side back-compat projection of
    #: exactly that loss; collapsing the pair here would rebuild it inside aximprint.
    #:
    #: `None` means the direction was not observed tagged, which is NOT the same as VLAN 0
    #: (`LES-006`, and axquery's own encoding: it stores 0 for "not observed tagged" and a
    #: plugin must translate rather than pass it through). Zeek's `conn.log` has no VLAN
    #: field at all, so the zeek plugin leaves both `None` -- and that is the honest reading
    #: for it, not a gap to fill.
    #:
    #: This is direct segmentation evidence: `windowing/segments.py` imputes locality from
    #: address shape at 0.6 confidence when a sensor declares nothing, and a VLAN tag is an
    #: `observed` alternative for the part of the estate that carries one.
    vlan_src: Optional[int] = None
    vlan_dst: Optional[int] = None
    #: True when the byte/packet/duration counters above were UNSET by the sensor and
    #: read here as 0 in a class where 0 is not the honest reading (a midstream or
    #: one-sided flow, where the sensor simply did not see the payload). Defaults
    #: False = observed; only a plugin knows, and only it may set it. Counted per
    #: window as `flows_imputed_counters` so the imputation is visible downstream
    #: instead of arriving wearing an observed face (CONVENTIONS §3).
    counters_imputed: bool = False


@dataclass
class IdentityEvent(Event):
    """An address-binding fact. Consumed statefully by the entity resolver.

    `lease_seconds` and `record_ttl` were ONE field (`valid_for`, "lease/ttl seconds if known")
    until `Q1`'s resolver landed, and one field answering two questions is the `LES-015` failure:
    a DHCP lease says how long the BINDING is true, a DNS TTL says how long the ANSWER may be
    cached and states nothing at all about the mapping's lifetime. The resolver bounds a binding's
    validity interval with the first and never with the second, so a single field structurally
    could not be consumed correctly — `windowing/entities.py` would have had to know which parser
    filled it, i.e. re-derive the split it was handed already merged. Exactly one is ever set;
    both are `0.0` when the sensor said nothing.
    """
    kind: str = ""           # dhcp | arp | dns | kerberos | netbios | mdns | ntlm | known_host
    ip: str = ""
    mac: str = ""
    hostname: str = ""
    principal: str = ""
    lease_seconds: float = 0.0   # DHCP: how long this ASSIGNMENT is valid
    record_ttl: float = 0.0      # DNS: how long this ANSWER may be cached (never a binding bound)


@dataclass
class ServiceEvent(Event):
    """A service/metadata fact (certs, banners, shares). Consumed by extractors."""
    kind: str = ""           # ssl | x509 | smb | http | snmp | ntp
    ip: str = ""
    port: int = 0
    attrs: dict = field(default_factory=dict)


@dataclass
class ProfileEvent(Event):
    """A measurement true of the CAPTURE, not of any host in it -- deliberately no subject field
    (`Q29`). Not a nullable-`ip` variant of `ServiceEvent`: the absence of a subject is the point,
    and a reserved sentinel entity (e.g. `__capture__`) would only re-encode the same ambiguity as
    a *value* every consumer must know to filter, rather than a property the schema itself states
    (`LES-006`'s "no evidence vs evidence of none" distinction, applied here to a subject rather
    than a field).

    `parameter` names WHAT was measured. Unlike `ServiceEvent.kind`/`IdentityEvent.kind` -- each
    of which echoes a classification the SENSOR itself already made (which Zeek log a row came
    from) -- `parameter` is PLUGIN-supplied: `ingest/zeek/plugin.py::_ocsp` mints the literal
    string `"ocsp_cache_lifetime"`; no sensor emits it (code review). It is still an open,
    declared vocabulary in the sense that matters for this table's own discipline:
    `windowing.windows.PROFILE_PARAMETERS` declares each admitted parameter's accumulator and
    unit (meaning is prose beside the entry, not a tuple element read by nothing), and a
    parameter absent from the table is a counted health fact, never a silent drop. `value` is the
    single number this one observation contributes to that parameter's window-scoped
    distribution -- whatever the parameter's own declared unit says it is.
    """
    parameter: str = ""
    value: float = 0.0


@dataclass
class AlertEvent(Event):
    """Sensor IOC/anomaly flag. Contamination marker only: taints windows, never accumulation."""
    ip: str = ""
    peer_ip: str = ""
    category: str = ""
    detail: str = ""


@dataclass
class SensorHealthEvent(Event):
    """Sensor's own health (capture loss, gaps). Inherited by window-health records.

    Also the channel by which the *ingest layer* reports on its own fidelity —
    `ingest_unordered` says a row could not be put back in ts order, which is a
    fact about the window's input that nothing else downstream could recover.
    A kind the window manager does not model is still counted there
    (`unmodelled_health_kinds`), never silently dropped.
    """
    kind: str = ""           # capture_loss | pkts_dropped | pkt_lag | ingest_unordered
    value: float = 0.0       # e.g. percent loss, dropped packets, seconds of displacement
    detail: str = ""         # sensor peer, or the log type for ingest-side kinds
