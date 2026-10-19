"""Synthesize axRange personas from observed traffic — `docs/JOURNAL_corpapcn_fidelity.md` GAP-C9.

The starter persona catalog is a set of hand-authored ARCHETYPES: what a corp workstation or a
web tier typically does. That is the right thing to ship and the wrong thing to drive a twin cast
from a real capture with, for two reasons this loop measured:

  * An archetype emits what its author imagined, not what the network was seen doing, so a role
    inferred from one signature gets driven by a persona emitting another and the twin cannot
    describe itself (GAP-C8, seven catalog rows).
  * An archetype's peer list may name roles the observed network never ran, which made the whole
    traffic profile unappliable (GAP-C5).

A persona generated from the evidence has neither problem BY CONSTRUCTION: its emits are the
ports the role's own members were observed dialling, and its peers are roles this twin actually
carries. The fixed-point check becomes a tautology rather than something to maintain.

What is inferred, and from what:

    emits[].ports      the dial census (`ir_entity.dial_ports`) of the role's members
    emits[].peer_role  whichever role in THIS twin serves that port; reserved
                       `multicast`/`broadcast` for link-local groups; the synthetic
                       `internet-sink-<n>` where the conversation left the network
    emits[].peer_scope `all` on a sink emit (that IS the fan-out), absent elsewhere
    emits[].proto      the port's well-known protocol where there is one, else raw tcp/udp
    volume             `ir_traffic_profile.bytes_per_window_q` (p50) of the role's members
    cadence            `ir_traffic_profile.flows_per_window_q` (p50) over the window length
    os_family          the role's catalog image (windows-* -> windows)

Everything is `defaulted` provenance in the twin config that references it — a synthesized
persona is a construction from observations, not an observation.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from typing import Any, Optional

# `catalog` sits BELOW every layer and may be imported from any of them (CONVENTIONS §3); the
# sibling compiler already reads it. Needed here for the peer-resolution tiebreak below, which
# has to rank roles the way the catalog itself does rather than inventing a second order.
from aximprint.catalog import ROLE_CATALOG

# Well-known port -> protocol name, restricted to protocols axRange's persona schema accepts.
# Anything not here emits as raw `tcp`/`udp`, which is honest: the port was observed, the
# application protocol was not.
_PORT_PROTO = {
    53: "dns", 88: "kerberos", 123: "ntp", 137: "nbns", 161: "snmp", 389: "ldap",
    443: "https", 445: "smb", 514: "syslog", 636: "ldaps", 80: "http", 25: "smtp",
    587: "smtp", 993: "imaps", 1900: "ssdp", 1985: "hsrp", 2049: "nfs", 2055: "netflow",
    3128: "http", 3268: "ldap", 3389: "rdp", 5044: "beats", 5353: "mdns", 5355: "llmnr",
    5432: "postgres", 9090: "http", 9100: "http", 9997: "tcp", 22: "ssh", 8006: "https",
}
# -- the emit vocabulary is not the observation vocabulary (iteration 18) -------------------
#
# `_PORT_PROTO` names a protocol the way **axRange's persona schema** does, because that is
# what an emit has to be spelled as. `ir_edge.proto` names it the way **the sensor** does. The
# two vocabularies overlap on five values and disagree on nineteen, and the emit RANKING joins
# them by string equality -- so nineteen of the twenty-four protos this module can emit scored
# a permanent zero and sorted below every port that happened to agree.
#
# That is not cosmetic, and iteration 17 measured the cost. `443/tcp` is `https` here and `ssl`
# in the IR, so the source's single largest outbound protocol -- 243 of `generic-endpoint`'s
# 711 outbound rows -- could not be spent, while `53/udp` matched a real IR label and won the
# slot instead. The twin then dialled port 53 at the sink 773 times against a source that
# carries 17 DNS rows outbound, the sink's responders came up serving 53, aximprint re-inferred
# the sink as `dns-resolver` rather than `web-tier`, and several hundred edges relabelled:
# `generic-endpoint->web-tier` 315 -> 3, `->dns-resolver` -> 773, `role_edges` TVD 0.453 ->
# 0.887. **Protocol fidelity drives role attribution**, which is why a lookup that "only"
# affects ranking moved the second-largest axis in the fingerprint.
#
# Mapped to what the sensor RECORDS, never to what the port implies. Composite labels are
# token-matched (`quic,ssl` is `ssl`; `gssapi,smb,krb` is both `smb` and `krb`), since Zeek
# joins every analyzer that fired on one connection.
_IR_PROTO_ALIASES: dict[str, tuple[str, ...]] = {
    # -- agreeing already; listed so the table is the single place the join is stated --------
    "dns": ("dns",), "http": ("http",), "ntp": ("ntp",), "ssh": ("ssh",),
    # -- the nineteen that scored zero, where the sensor does have a counterpart ------------
    # TLS is `ssl`, and HTTP/3 arrives as the composite `quic,ssl`. 472 rows on this source:
    # the single biggest thing the ranking could not see.
    "https": ("ssl", "quic"),
    # Everything else carried inside TLS is `ssl` too -- the IR has no port column, so it
    # genuinely cannot tell one TLS session from another. Mapping them is truthful about that
    # ambiguity (they tie, and the dial-frequency tiebreak decides); on THIS source neither
    # port is dialled by anything, so both entries are inert here.
    "ldaps": ("ssl",), "imaps": ("ssl",),
    "kerberos": ("krb", "krb_tcp"),          # + the composite `gssapi,smb,krb`
    "ldap": ("ldap_tcp", "ldap_udp"),        # Zeek splits LDAP by transport
    "smb": ("smb",),                         # + `gssapi,smb,krb` / `gssapi,smb,ntlm`
    # Zeek analyzers whose service string is unambiguous, none of them observed on this
    # capture -- mapped anyway, because the table describes the sensor, not one capture.
    "smtp": ("smtp",), "rdp": ("rdp",), "snmp": ("snmp",), "syslog": ("syslog",),
    "nfs": ("nfs",),
}

#: Protos left DELIBERATELY unweighted, with the reason. Absence from `_IR_PROTO_ALIASES`
#: already means "scores zero"; this exists so that absence reads as a decision rather than an
#: oversight -- the whole defect being fixed above was an unexamined zero.
#:
#:   tcp            9997/tcp, and the fallback for every port `_PORT_PROTO` does not name.
#:                  The IR's `tcp`/`udp` labels mean "the sensor could not name the service"
#:                  -- 446 + 85 rows of UNIDENTIFIED traffic. Aliasing one arbitrary port to
#:                  that would hand it the entire unidentified mass, which is a bigger version
#:                  of the distortion this whole table exists to remove.
#:   mdns, llmnr,   GROUP ports. Their destination is a wire address, which carries no role,
#:   nbns, ssdp,    and `proto_weights` counts ROLE-to-role edges only -- so their own
#:   hsrp           conversations are structurally absent from the weight table. Any weight
#:                  they received would therefore be borrowed from a different conversation
#:                  (measured: the source's 14 multicast rows ARE labelled `dns`, so
#:                  `mdns -> dns` would credit 5353/udp with unicast port-53's 87 rows).
#:                  They also do not need it: a group port bypasses the quota and is spent
#:                  from the row count alone.
#:   beats,         No Zeek analyzer exists, so no label can be joined against.
#:   netflow
#:   postgres       Zeek's PostgreSQL analyzer is recent and its service string is pinned
#:                  nowhere in this repo. Unmapped rather than guessed -- a wrong token is a
#:                  silent zero, which is exactly the failure mode above.
_UNWEIGHTED_PROTOS = ("tcp", "mdns", "llmnr", "nbns", "ssdp", "hsrp", "beats", "netflow",
                      "postgres")


def observed_weight(weights: Counter, port: int) -> int:
    """How much observed conversation a dialled `port` carries, in the IR's own vocabulary.

    Token-matched against the comma-joined service label Zeek writes when several analyzers
    fire on one connection, so `quic,ssl` scores as `ssl` and `gssapi,smb,krb` scores as both
    `smb` and `kerberos`.
    """
    aliases = _IR_PROTO_ALIASES.get(_PORT_PROTO.get(port, ""))
    if not aliases:
        return 0
    wanted = frozenset(aliases)
    return sum(n for label, n in weights.items()
               if wanted & frozenset(str(label).split(",")))


# Ports whose "peer" is a multicast group or the local broadcast address, not a host.
_GROUP_PORTS = {1900: "multicast", 5353: "multicast", 5355: "multicast", 1985: "multicast",
                137: "broadcast", 67: "broadcast", 68: "broadcast"}

# -- the internet sink (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C17) ------------------------
#
# 85% of the source's edges LEAVE the network, and an air-gapped twin can place none of them.
# The twin's hosts do not go quiet as a result -- they manufacture LOCAL edges to stay busy, so
# the same missing thing bounds two fingerprint axes in opposite directions at once (measured:
# `generic-endpoint->web-tier` local, source 3 vs twin 225, while local fan-out degree ran 6-20
# for 104 twin hosts against a source's 8). No persona budget can satisfy both while the twin
# has nowhere external to talk.
#
# So the twin gets somewhere: an ORDINARY node group, inside the twin, on its own segment,
# holding a synthetic role that stands in for "everything outside". A persona reaches it the
# only way axRange lets a persona reach anything -- BY ROLE. That is the safety property, and it
# is structural rather than a matter of care: `axrange/personas/schema.py::Emit` has no address
# or CIDR field at all, so a persona cannot name an arbitrary destination even deliberately, and
# peer resolution only ever yields in-twin host addresses. Synthetic outbound traffic therefore
# cannot reach the lab's real uplink no matter how this module is edited.
#
# NOT spelled `internet`/`egress`: those are two of axRange's four RESERVED_PEER_ROLES
# (`schema.py:64`), which resolve to nothing on the wire (axRange GAP-048) and which
# `responder.py::_NOT_A_HOST` excludes from listener synthesis -- a sink under either name would
# be a node nothing dials and nothing answers on.
SINK_ROLE = "internet-sink"

#: How many hosts hold each of the sink's sub-roles. BINARY, and that is the whole trick:
#: `peer_scope` yields either ONE peer or ALL of them, never a distribution, so a single
#: 16-host sink role would give every host on the network the identical fan-out of 16. A
#: persona composes any fan-out in 1..15 by emitting at the subset of these roles whose sizes
#: sum to it -- reproducing a real fan-out distribution with nothing but existing axRange role
#: machinery. Measured on the source: median 2, mean 4.485, max 53 distinct external peers per
#: host, and 7 of the 132 hosts with external conversations exceed 15 (the 16..53 tail).
#: Those 7 are knowingly clamped, which is a documented under-representation of the
#: busiest hosts rather than a hidden one.
SINK_BUCKETS = (8, 4, 2, 1)

#: The largest fan-out the bucket family can express. Anything above it is clamped, not split
#: across a second sink -- see the truncation note above.
SINK_MAX_FANOUT = sum(SINK_BUCKETS)


def sink_sub_role(hosts: int) -> str:
    """The role name of the sink sub-group holding `hosts` instances."""
    return f"{SINK_ROLE}-{hosts}"


def sink_fanout_buckets(fanout: int) -> list[int]:
    """`5 -> [4, 1]` — the binary decomposition of a target external fan-out.

    Descending, so the widest fan-out lands on the highest-ranked (heaviest) port rather than
    on whatever rare port happens to sort last. Clamped to `SINK_MAX_FANOUT`; a negative or
    zero fan-out decomposes to nothing, which is a role that talked only locally.
    """
    n = max(0, min(SINK_MAX_FANOUT, int(fanout)))
    return [b for b in SINK_BUCKETS if n & b]


# Bands on bytes PER FLOW, not per window. Reading the per-window figure directly put every
# role in `bulk` — a synthesized persona that would have flooded the twin at 100x the rate its
# source ran at, which is the opposite of fidelity.
#: Multiples of a role's MEDIAN per-window volume that separate an outlier from the pack.
#: Wide on purpose: these are meant to isolate genuine heavy hitters and genuinely idle hosts,
#: not to slice a smooth distribution into arbitrary tiers. A role with no dispersion produces a
#: single band and the twin is unchanged.
_BAND_HEAVY, _BAND_LIGHT = 10.0, 0.1


def activity_bands(pit: dict) -> dict[str, str]:
    """`{entity_key: "heavy" | "light" | ""}` — where each host sits within its OWN role.

    The single most consequential property of a real network is that its traffic is dominated by
    a few hosts. Measured on CorpAPCN: `generic-endpoint` spans 102 hosts whose per-window bytes
    run 0 to 2,862,768 — the busiest is **1,423x the median**. Collapsing a role to one persona
    derived from its mean erases that, which is why the twin reproduced fan-out SHAPE well
    (TVD 0.055) and edge MIX badly (TVD 0.563): with no heavy hitters,
    `generic-endpoint -> web-tier` came out 38 against the source's 298.

    Shared by the compiler (which splits node groups) and the synthesizer (which gives each band
    its own emit rates). They must agree or a node lands in one band and is driven as another,
    so the rule lives here once rather than in both.

    LOCAL entities only, and that is a correction rather than a refinement (the residual of
    `3dedba8`, whose own message says the fix belonged in four places and made it in two). The
    roster here is role-bearing but was not locality-filtered: of 356 roled entities on this
    source, **233 are EXTERNAL** -- internet peers this twin will never deploy -- so every
    role's median, and therefore every band boundary, was decided by a population that is 65%
    hosts the twin does not contain. Measured effect on the 123 local roled entities:
    `''/light/heavy` 54/48/21 -> 48/45/30, moving 12 of them between bands.

    An external entity now falls out of the mapping entirely and `.get(key, "")` reads it as
    the base band, which is the honest default: it is not a twin host, so it has no band.
    """
    vol = {p["entity_key"]: _p50(p.get("bytes_per_window_q"))
           for p in pit.get("ir_traffic_profile", [])}
    ents = [e for e in pit.get("ir_entity", [])
            if e.get("role") and e.get("locality") == "local"]
    med: dict[str, float] = {}
    for role in {e["role"] for e in ents}:
        vals = sorted(vol.get(e["entity_key"], 0.0) for e in ents if e["role"] == role)
        med[role] = vals[len(vals) // 2] if vals else 0.0

    out: dict[str, str] = {}
    for e in ents:
        m = med.get(e["role"], 0.0)
        if m <= 0:
            out[e["entity_key"]] = ""
            continue
        v = vol.get(e["entity_key"], 0.0)
        out[e["entity_key"]] = ("heavy" if v >= m * _BAND_HEAVY
                                else "light" if v <= m * _BAND_LIGHT else "")
    return out


_VOLUME_BANDS = ((2048, "low"), (32768, "med"), (1024 * 1024, "high"))

#: Ports at or above this are ephemeral client ports. They are the local end of somebody's
#: outbound connection, never a service, so treating one as a peer's listening port invents an
#: emit aimed at a port nothing will ever answer on.
_EPHEMERAL_FLOOR = 32768


def _volume_for(median_bytes: float) -> str:
    for ceiling, name in _VOLUME_BANDS:
        if median_bytes <= ceiling:
            return name
    return "bulk"


#: Bytes one fire puts on the wire per volume tier, mirroring axRange's own
#: `personas.drivers.scripted._VOLUME_PROFILE` (messages x bytes-per-message). Copied rather
#: than imported for the same reason `persona_cadence` is copied into `roles.yaml`: that catalog
#: is a separate repo on its own release cadence and is not a dependency of this one.
_TIER_BYTES = {"low": 2 * 256, "med": 8 * 4096, "high": 24 * 65536, "bulk": 64 * 1024 * 1024}


def _cadence_for(tier: str, observed_bytes_per_s: float) -> str:
    """Seconds between fires, RATE-MATCHED to the observed byte rate.

    Deriving cadence from flow COUNT (the obvious reading of "how often did it talk") is wrong
    by an order of magnitude, because a fire is not a flow: one `bulk` fire is 64 MiB, so firing
    at the source's ~9 flows/minute would offer ~7 MB/s against an observed 0.83 MB/s — 8.7x
    too fast, and across 102 endpoints that alone would exceed the capture path's measured
    2.6 Gbit/s ceiling. A twin that floods its own sensor is not a faithful twin.

    So: pick the tier from bytes-per-flow, then space the fires so tier_bytes/interval matches
    the rate actually observed.

    Clamped to [5s, 10m]. Below 5s a synthesized persona becomes a load generator by accident;
    above 10 minutes it will not fire often enough to appear in a short capture at all, which
    would make the twin unobservable for exactly the reason this work exists.
    """
    rate = max(1.0, float(observed_bytes_per_s or 1.0))
    return f"cyclic:{max(5, min(600, int(_TIER_BYTES.get(tier, 512) / rate)))}s"


def _p50(raw: Any) -> float:
    q = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return float(q[len(q) // 2]) if q else 0.0


def emit_budgets(pit: dict, bands: Optional[dict] = None) -> dict[str, int]:
    """`{role_or_role-band: how many emits a persona of that group should carry}`, measured
    from the source's own edge rows.

    Keyed on the metric that is actually compared. `fan_out_degree` counts EDGE ROWS per
    source, and `ir_edge` is keyed `(src, dst, proto)` -- so a host talking to 2 peers over 4
    protocols scores 8, not 2. Budgeting distinct peer ROLES (the first attempt) therefore
    moved the wrong quantity: the twin's fan-out is driven by how many PROTOCOLS each persona
    emits, since every emit becomes its own edge row.

    Measured on CorpAPCN (iteration 10), rows-per-host medians:

        role                source   twin
        generic-endpoint       3.0    8.0   twin 2.7x too chatty
        corp-workstation      16.0    5.0   twin 3x too quiet
        dns-resolver           6.0    5.5   already right
        network-device         1.0    1.0   already right

    Note this budget is NOT uniformly a cap -- `corp-workstation` needs to emit MORE. The
    fixed `most_common(12)` was wrong in both directions at once, which is why a single
    hardcoded number could never fit: it over-served the twin's most numerous role and
    under-served its busiest one.

    Floor of 1: a group with dial evidence must still talk to someone, or it deploys and does
    nothing -- indistinguishable downstream from a broken node.

    Sized over LOCAL sources only -- the other half of `3dedba8`'s residual. A budget is "how
    many edge rows does a host of this group emit", and a host the twin will never deploy has
    no bearing on that. The unfiltered population was measurably wrong, not merely impure:
    `generic-endpoint` 103 local sources + 15 external, `web-tier` 2 + 1, and `file-server`
    **0 local + 1 external** -- a whole budget group synthesized from a host that exists only
    on the internet. Measured effect: `web-tier` 6 -> 5, `corp-workstation` 16 -> 17,
    `generic-endpoint` 3 -> 4, `generic-endpoint-heavy` 7 -> 9, and the groups `file-server`
    and `monitoring-collector-light` disappear entirely because both were purely external.
    """
    ents = pit.get("ir_entity") or []
    role_of = {e["entity_key"]: e.get("role") for e in ents}
    is_local = {e["entity_key"]: (e.get("locality") == "local") for e in ents}
    band_of = {e["entity_key"]: (bands or {}).get(e["entity_key"], "") for e in ents}
    rows: dict[str, int] = defaultdict(int)
    for ed in pit.get("ir_edge") or []:
        rows[ed.get("src_key")] += 1
    per_group: dict[str, list[int]] = defaultdict(list)
    for key, n in rows.items():
        base = role_of.get(key)
        if not base or not is_local.get(key):
            # Role-less background entities. Deliberately excluded from the BUDGET (they map
            # to no persona) but they are the bulk of the source's degree-1 bucket -- 211
            # hosts vs the twin's 56 -- and the twin_config drops role-less hosts by
            # construction. That part of the fan-out gap is structural, not tunable here.
            # External sources go with them, for the simpler reason that no twin host is one.
            continue
        b = band_of.get(key, "")
        per_group[f"{base}-{b}" if b else base].append(n)
    return {g: max(1, int(statistics.median(v))) for g, v in per_group.items() if v}


def proto_weights(pit: dict, bands: Optional[dict] = None) -> dict[str, Counter]:
    """`{role_or_role-band: Counter({proto: observed edge rows})}` -- how much of a group's
    actual conversation each protocol carries.

    An emit budget alone is not enough: it says HOW MANY emits a persona gets, and the
    selection then decides WHICH. Selecting by dial-port frequency (`ports.most_common`)
    counts how many hosts touched a port and is blind to how much of the conversation it
    carries, so a budget of 3 can be spent entirely on rare ports while the dominant one is
    dropped. Measured on CorpAPCN (iteration 11), which is exactly what happened:

        generic-endpoint->web-tier      source=298   twin=127   the heaviest edge, cut
        dns-resolver->generic-endpoint  source=3     twin=103   a cheap port over-served

    The two `role_edges` sub-metrics disagreed in the way that diagnoses this: key-set
    Jaccard IMPROVED (more of the source's distinct edges reproduced) while TVD WORSENED (the
    weights on them got further off) -- better coverage, worse proportion, which is the
    signature of a frequency-ranked selection.

    Weighted on edge ROWS -- one per `(src, dst, proto)` per window -- and on neither
    `flows` nor `bytes`. `fan_out`/`role_edges` increment by one per row whatever its flow
    count, so rows is the quantity the fingerprint actually compares; a row is one
    flow-SHAPED fact and is not one flow. This accumulated `ir_edge.flows` until iteration 19
    on the strength of exactly that conflation, and the two columns order the ports
    OPPOSITELY on this source (`docs/WORK_ORDER_proto_weight_rows.md` §2): DNS is many small
    flows per row and TLS fewer, fatter ones, so `53/udp` won every group on flows while
    `443/tcp` won five of six on rows -- 158 to 26 on the heavy band, where it lost 540 to
    870 on flows. The argument against `bytes` survives and is the same argument one column
    further: bytes would rank a single bulk transfer above a hundred small conversations that
    matter more to the shape, and flows would rank a hundred one-packet lookups on ONE edge
    above the same hundred conversations spread across many.

    **Two-branch on destination locality, since the sink exists.** A local roled destination
    weights the twin's internal conversation; an EXTERNAL roled one weights what now leaves
    via `SINK_ROLE`. Both are placeable, so both count -- and the difference is not marginal:
    `ssl` is 243 of this source's 711 outbound edge rows and its single largest outbound
    protocol, so a local-only weighting starved the twin's heaviest protocol out of the
    ranking entirely and left it emitting the cheap background ports instead.

    Excluding external destinations here was RIGHT while the twin had nowhere external to
    talk (`3dedba8`): aiming at 295 unplaceable web-tier edges drove it to manufacture local
    ones to match, measured `generic-endpoint->web-tier` local source 3 vs twin 225. That
    reasoning is now superseded by the sink absorbing them, not reversed -- and note what
    still holds: a ROLE-LESS destination is excluded either way, local or not, because
    nothing in the twin can stand in for a peer nothing could name.
    """
    ents = pit.get("ir_entity") or []
    role_of = {e["entity_key"]: e.get("role") for e in ents}
    band_of = {e["entity_key"]: (bands or {}).get(e["entity_key"], "") for e in ents}
    out: dict[str, Counter] = defaultdict(Counter)
    for ed in pit.get("ir_edge") or []:
        base = role_of.get(ed.get("src_key"))
        if not base or not role_of.get(ed.get("dst_key")):
            continue
        b = band_of.get(ed.get("src_key"), "")
        group = f"{base}-{b}" if b else base
        out[group][ed.get("proto")] += 1          # one per ROW, never `flows` (docstring)
    return out


def role_edge_targets(pit: dict) -> dict[str, Counter]:
    """`{src_role: Counter({dst_role_or_SINK_ROLE: observed edge rows})}` -- the source's own
    role-edge histogram, which is what `role_edges` compares against.

    Ranking emits by conversation weight (GAP-C18) fixes each persona in isolation and still
    misses the aggregate, because a role edge is `sum(hosts x emits at that role)` and BAND
    POPULATION dominates that sum. Measured on CorpAPCN (iteration 12/13): the light band is
    47 of 102 endpoints and its own weights bought dns+ntp, so it contributed ZERO web-tier
    edges no matter how well-shaped each persona was:

        generic-endpoint->dns-resolver  source=69    twin=207   3x too high
        generic-endpoint->web-tier      source=298   twin=134   2x too low

    Role-to-role, and since GAP-C17's sink exists the destination side is TWO-BRANCH: a local
    roled destination is itself, an external one is `SINK_ROLE`. The twin can now place both,
    the second as a fan-out at the sink rather than as a named peer -- which is the whole
    point of giving it an internet edge. `allocate_budget` then proportions the two against
    each other with no special case: the sink is just the largest entry in the Counter.

    Both branches require the destination to be ROLED, which keeps the histogram a role-to-role
    census on both sides. Counting unroled external peers in the sink bucket while unroled
    local ones stay excluded would tilt the proportion toward the sink by an artifact of role
    coverage rather than by anything the network did. Measured on this source, the resulting
    external share per role -- 0.800 `generic-endpoint`, 0.723 `corp-workstation`, 0.528
    `dns-resolver` -- lands within ~0.02 of the same shares measured directly off the edge
    table (0.804 / 0.733 / 0.548); admitting unroled external peers overshoots them by ~0.06.

    Superseded here (`3dedba8`): local-destination-only was right while the twin had nowhere
    external to talk, since the target `generic-endpoint->web-tier=298` is 295 external and 3
    local, and chasing it drove the twin to 225 local web-tier edges against a source with 3.
    Those 295 are now the sink's, which is where they belonged all along.
    """
    ents = pit.get("ir_entity") or []
    role_of = {e["entity_key"]: e.get("role") for e in ents}
    is_local = {e["entity_key"]: (e.get("locality") == "local") for e in ents}
    out: dict[str, Counter] = defaultdict(Counter)
    for ed in pit.get("ir_edge") or []:
        src, dst = role_of.get(ed.get("src_key")), role_of.get(ed.get("dst_key"))
        if not (src and dst):
            continue
        if is_local.get(ed.get("dst_key")):
            out[src][dst] += 1
        else:
            out[src][SINK_ROLE] += 1
    return out


def allocate_budget(budget: int, targets: Counter) -> Counter:
    """Split `budget` emit slots across destination roles in `targets`' proportions.

    Largest-remainder, so the allocation sums to exactly `budget` and the biggest share
    cannot be rounded away -- the failure that matters here is dropping the DOMINANT peer,
    which is precisely what a per-persona ranking did to `web-tier` (298 -> 134).
    """
    total = sum(targets.values())
    if budget <= 0 or total <= 0:
        return Counter()
    exact = {r: budget * n / total for r, n in targets.items()}
    alloc = Counter({r: int(v) for r, v in exact.items()})
    remaining = budget - sum(alloc.values())
    # Ties broken by target size so a deterministic cast stays deterministic.
    for role, _frac in sorted(exact.items(), key=lambda kv: (-(kv[1] - int(kv[1])), -targets[kv[0]], kv[0])):
        if remaining <= 0:
            break
        alloc[role] += 1
        remaining -= 1
    return +alloc


#: Suffix of a group's LOCAL-BEARING persona variant. The unsuffixed `cast-<group>` is the
#: EXTERNAL-ONLY one, and that asymmetry is deliberate in both directions: the plain name stays
#: the safe fallback target for a band whose own persona was never synthesized
#: (`cli.cmd_cast`, `scripts/install_cast_as_twin.py::_persona_for`), and the host that carries
#: local conversation is the one that has to say so.
LOCAL_VARIANT_SUFFIX = "-local"

#: Suffix of a group's CHATTER-ONLY variant: a host whose entire local presence is link-local
#: discovery (mDNS/LLMNR/SSDP/NBNS/DHCP) and which has no local peer at all. A real population
#: this size cannot be ignored -- 21 of this source's 122 twin-deployable hosts are exactly
#: that, 15 of them `generic-endpoint` -- and it is 19 of the 35 hosts in the source's own
#: single-local-edge bucket, i.e. most of the shape `fan_out_degree_local` is measured on.
CHATTER_VARIANT_SUFFIX = "-chatter"


def _round_half_up(x: float) -> int:
    """`round()` is banker's rounding — `round(0.5) == 0`, `round(1.5) == 2` — so two group
    sizes that should behave the same way don't. A cast is a deterministic artifact compared
    against its own previous run, so the tie has to break the same way every time regardless
    of parity."""
    return int(x + 0.5)


def local_population(pit: dict, bands: Optional[dict] = None) -> dict[str, dict]:
    """`{role_or_role-band: {hosts, bearers, rows, share, degree}}` — how the source spreads a
    group's LOCAL conversation across its own population.

    This is the measurement `allocate_budget` cannot make, and its absence is what bounded two
    fingerprint axes at once (`docs/JOURNAL_corpapcn_fidelity.md`, iteration 16). A budget is
    per HOST and integral, so a group whose local share is 21% of a budget of 4 allocates
    `round(0.84) == 1` local row to **every** host — the smallest non-zero allocation is one, so
    the twin acquires a floor of ~1 local edge per host that no proportion can go below.

    The source does not look like that. Measured on CorpAPCN, `generic-endpoint`:

        hosts                 103
        with any local edge     30      (median local degree is **0**)
        local edge rows         83      (2.77 per BEARER, not 0.81 per host)

    So local-versus-external has to be allocated across the POPULATION — most hosts entirely
    external, a minority carrying several local rows each — rather than inside each host, where
    integer rounding turns a minority behaviour into a universal one.

    Two numbers per group, both read off the source:

        share    bearers / hosts        — what fraction of the group talks locally at all
        degree   rows / bearers         — how much local conversation one bearer carries

    LOCAL roled sources with LOCAL roled destinations, on both sides. Same filter
    `emit_budgets` and `activity_bands` already apply, and for the same reason: a group's
    persona describes what its hosts IN THIS TWIN do, and 233 of this source's 356 roled
    entities are internet peers the twin never deploys. (`role_edge_targets` deliberately does
    NOT filter its source side — it answers a different question, "where did this ROLE's
    conversation go", and it is left exactly as it is.)
    """
    ents = pit.get("ir_entity") or []
    band_of = {e["entity_key"]: (bands or {}).get(e["entity_key"], "") for e in ents}
    local_roled = {e["entity_key"] for e in ents
                   if e.get("role") and e.get("locality") == "local"}
    # A local destination the twin cannot PLACE -- a multicast group, a broadcast address, a
    # link-local address. `compiler.py` uses exactly this test to keep them out of the node
    # roster ("49 local entities were observed on no provisionable network"), and the persona
    # side already has a vocabulary for them: `_GROUP_PORTS` resolves such an emit to the
    # reserved `multicast`/`broadcast` peer, which is a wire address rather than a host.
    unplaced_local = {e["entity_key"] for e in ents
                      if e.get("locality") == "local" and not e.get("segment")}
    rows: Counter = Counter()
    chatter: Counter = Counter()
    for ed in pit.get("ir_edge") or []:
        if ed.get("src_key") not in local_roled:
            continue
        if ed.get("dst_key") in local_roled:
            rows[ed["src_key"]] += 1
        elif ed.get("dst_key") in unplaced_local:
            chatter[ed["src_key"]] += 1

    groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for e in ents:
        if e["entity_key"] not in local_roled:
            continue
        b = band_of.get(e["entity_key"], "")
        groups[f"{e['role']}-{b}" if b else e["role"]].append(
            (rows.get(e["entity_key"], 0), chatter.get(e["entity_key"], 0)))

    out: dict[str, dict] = {}
    for group in sorted(groups):
        degs = groups[group]
        bearers = [r for r, _c in degs if r > 0]
        # DISJOINT from `bearers` by construction, because the two variants are alternatives:
        # a host that talks to a local peer is described by the local-bearing persona whether
        # or not it also chatters. This counts only the hosts whose local presence is chatter
        # and NOTHING else -- the ones the twin currently gives no local edge at all.
        chatterers = sum(1 for r, c in degs if r == 0 and c > 0)
        out[group] = {
            "hosts": len(degs), "bearers": len(bearers), "rows": sum(bearers),
            "share": len(bearers) / len(degs) if degs else 0.0,
            "chatter": chatterers,
            "chatter_share": chatterers / len(degs) if degs else 0.0,
            # Floor of 1 on a group that HAS bearers: a bearer with no local row is not a
            # bearer, so a mean that rounds to zero would silently empty the local side.
            "degree": max(1, _round_half_up(sum(bearers) / len(bearers))) if bearers else 0,
        }
    return out


def bearer_count(share: float, hosts: int) -> int:
    """How many of a node group's `hosts` carry the local-bearing variant.

    Rounded, not floored: a group of 3 hosts at a 29% bearer share is one bearer, and flooring
    would give a twin whose small segments are silently all-external. Clamped to `hosts` so a
    share of exactly 1.0 leaves nothing for the external-only variant to claim — the
    double-claim rule (axRange permits one persona instance per host) is upheld structurally by
    partitioning an ordered list, never by two independent selections agreeing.
    """
    if share <= 0 or hosts <= 0:
        return 0
    return max(1, min(hosts, _round_half_up(share * hosts)))


def select_bearers(instances: list[str], count: int) -> list[str]:
    """The `count` members of `instances` that carry local conversation — evenly spaced.

    Deterministic by CONSTRUCTION rather than by care: an arithmetic stride over a caller-sorted
    list, with no set iteration and no hashing anywhere in it (`LES-002`; the gates in
    `scripts/verify_pipeline_gates.sh` assert a cast is byte-identical across runs, and a set
    ordering that happened to be stable in one process is exactly the failure they exist to
    catch).

    Spaced rather than taken from the front, because a node group's instances are enumerated
    segment by segment: `instances[:count]` would put every locally-talking host of a role on
    the first one or two segments, which is a topology the source does not have and which the
    conduit census would then describe.
    """
    n = len(instances)
    if count <= 0 or n == 0:
        return []
    if count >= n:
        return list(instances)
    return [instances[(i * n) // count] for i in range(count)]


def _catalog_rank(role: str) -> int:
    """Where `role` sits in `ROLE_CATALOG`'s declaration order — earlier is more specific.

    `catalog/roles.py` sorts the file most-specific-first (`-len(requires)`, then `-len(dials)`)
    with a STABLE sort, so a role's index is "how much evidence its strongest rule demands, then
    where its author put it". First index wins for a role with several rows, which is the same
    rule `_rule_for` and `match_role` already apply.

    A role with no row at all — the synthetic `internet-sink-*` set, held out of `ROLE_CATALOG`
    by construction so no evidence can name one — sorts last, deterministically, rather than
    raising: it can legitimately appear in a vote if a future PIT carries one.
    """
    for i, rule in enumerate(ROLE_CATALOG):
        if rule.role == role:
            return i
    return len(ROLE_CATALOG)


def _resolve_serves(port: int, votes: Counter) -> str:
    """Which role answers `port` in this twin, from the observed listen census.

    Most votes wins. **A TIE is broken by the catalog's DECLARATION ORDER, never by row order**
    (`docs/WORK_ORDER_reference_ingest.md` §7.2, `LES-136`) — and the tie is real, not
    hypothetical: measured on the CorpAPCN PIT, port 389 draws `dns-resolver: 2`,
    `domain-controller: 2`, `file-server: 1`, and 445 draws 2-1-1.

    What it replaced, and why the replacement is in the mechanism rather than in the content:
    `Counter.most_common` breaks a tie on **insertion order**, which here is the order
    `pit["ir_entity"]` came back from DuckDB. So the peer every LDAP emit in the twin aimed at
    was decided by row order — `dns-resolver` won 389, and a cast twin's domain controller was
    never dialled for LDAP at all. Pinning `389/tcp` at the DC in the catalog would fix that one
    port and leave every other contended port (445 today, and whatever a wider capture adds)
    resolving by row order exactly as before; that is content papering over a mechanism, and the
    mechanism is what is wrong.

    Declaration order is the right tiebreak rather than merely a deterministic one: the catalog
    is already ordered most-specific-first, so "the role whose rule demanded the most evidence"
    wins a tie — `domain-controller` (88+389 jointly) over `dns-resolver` (53/udp alone). Role
    NAME is the final key so the order is total even between two rows of equal specificity that
    the catalog does not distinguish.
    """
    return min(votes.items(), key=lambda kv: (-kv[1], _catalog_rank(kv[0]), kv[0]))[0]


def _compose_emits(*, group: str, ranked: list, quota: Counter, budget: int,
                   serves: dict, gated: bool, sink_rows: int,
                   promote: Optional[str] = None) -> tuple[list[dict], int]:
    """One variant's emit list, and the number of edge ROWS it puts on the wire.

    Lifted out of `synthesize_personas` unchanged in behaviour so that one group can be spent
    several ways -- with a local quota, with none, with its link-local chatter promoted -- from
    the same ranked port list. `quota` is the LOCAL allocation only and `sink_rows` is what the
    caller has left for the sink, where previously the sink was just another entry in the quota
    Counter and the split was implicit.

    `promote` moves one port-spec to the front of the ranking. It exists for the chatter
    variant and nothing else: a group port carries no proto weight (its destination is a wire
    address, so `proto_weights` -- which counts ROLE-to-role edges -- can never score it), so
    it always ranks below every weighted protocol and is dropped the moment the budget's rows
    are spent. That is why the twin has 9 hosts in the source's 53-host single-local-edge
    bucket: it drops the one emit those hosts are made of.
    """
    if promote:
        ranked = ([kv for kv in ranked if kv[0] == promote]
                  + [kv for kv in ranked if kv[0] != promote])
    emits: list[dict] = []
    seen_ports: set = set()
    sink_left = sink_fanout_buckets(sink_rows)
    quota = +Counter(quota)
    rows = 0
    for port_proto, _n in ranked:
        if rows >= budget:
            break
        if port_proto in seen_ports:
            continue
        port, _, transport = port_proto.partition("/")
        pn = int(port)
        peer = _GROUP_PORTS.get(pn) or serves.get(pn)
        scope, weight = None, 1
        if pn not in _GROUP_PORTS and (peer is None or peer == group
                                       or (gated and quota.get(peer, 0) <= 0)):
            # Three ways a dialled port has no LOCAL home: nothing in this twin answers on
            # it (19 distinct port-specs on this source -- 138/udp, 5228/tcp, 8883/tcp,
            # 873/tcp, 2049/tcp and more, every one of them silently dropped before the
            # sink existed), the only answerer is this role itself, or the local peer's
            # share is already spent -- which, for the external-only variant, is every
            # peer, since its quota is empty by construction.
            #
            # The third case is what carries the heavy protocols. `443/tcp` resolves to a
            # local `web-tier`, so without it `ssl` would still be dropped after ranking
            # first: the source's 295 external web-tier edges buy no local quota, and the
            # port would be skipped rather than sent outbound.
            #
            # A multicast/broadcast port is deliberately NOT redirected: its peer is a wire
            # address on the local segment, which is a real local fact and not an
            # unsatisfied outbound one.
            if not sink_left:
                seen_ports.add(port_proto)
                continue
            weight = sink_left.pop(0)
            peer, scope = sink_sub_role(weight), "all"
        elif gated and pn not in _GROUP_PORTS:
            # A quota exists for this role: honour it, and skip a candidate whose peer is
            # already fully served. Falls through to plain ranking when the source shows this
            # role no role-to-role edges at all (nothing to proportion against).
            if quota.get(peer, 0) <= 0:
                continue
            quota[peer] -= 1
        # A GROUP PORT bypasses the quota entirely, and that is a fix rather than an
        # exemption. `multicast`/`broadcast` are wire addresses, so they can never BE a
        # quota key -- `role_edge_targets` only ever counts roles -- which made
        # `quota.get(peer, 0) <= 0` permanently true for them and skipped every one.
        # Link-local discovery is real observed traffic that costs a role no peer, so it is
        # spent from the budget's ROW count and nothing else.
        seen_ports.add(port_proto)
        rows += weight
        emit = {
            "proto": _PORT_PROTO.get(pn, transport or "tcp"),
            "ports": [pn], "peer_role": peer,
            "transport": transport or "tcp", "direction": "EW",
            # `optional`, always. Every peer here was resolved from THIS twin's own listen
            # census, so it exists by construction -- but axRange's PSN-090 requires any
            # non-optional peer to appear in `requires[]`, and declaring hard requires is
            # exactly what made a cast traffic profile unappliable to a twin missing one of
            # them (GAP-C5). "Talk to it if present" is both what satisfies the rule and
            # what is actually true of a synthesized persona.
            "optional": True,
            # `cadence` and `volume` are filled in by the caller: both need the group's
            # final row count, which is not known until every emit has been placed.
        }
        if scope:
            # Only ever set on a sink emit. Left ABSENT elsewhere rather than written as
            # the `one` default, so a diff of two casts shows fan-out where it actually is.
            emit["peer_scope"] = scope
        emits.append(emit)
    return emits, rows


def synthesize_personas(pit: dict, *, window_s: int = 60,
                        image_of: Optional[dict] = None,
                        chatter: bool = True) -> dict[str, dict]:
    """`{persona_id: persona document}` — one per role that has dial evidence.

    A role whose members were never seen dialling anything gets NO persona rather than an empty
    one: an empty persona is a node that deploys and does nothing, which is indistinguishable
    downstream from a broken one.

    `chatter` switches the CHATTER variant off (`aximprint cast --no-chatter`). Default ON, so
    the cast is unchanged by the option's existence. It exists because iteration 16 shipped two
    changes at once -- the population allocator and the chatter variant -- and moved five
    fingerprint axes in both directions, which makes neither change attributable:
    `fan_out_degree_local` 0.572 -> 0.162 (much better than projected) while
    `role_edges_local` 0.618 -> 0.742 and `external_dst_edge_share` 0.8054 -> 0.6653 (both
    worse, both away from the source). Running the allocator alone is the only way to read
    which half owns which axis. The `-local` variants are NOT switchable: they are the
    allocator, and the allocator is what this measures against.
    """
    ents = pit["ir_entity"]
    profiles = {p["entity_key"]: p for p in pit.get("ir_traffic_profile", [])}

    # Which role serves which port, in THIS twin — the peer resolution table. Built from the
    # observed listen census, so an emit can only ever point at a role that actually answers.
    serves_votes: dict[int, Counter] = defaultdict(Counter)
    for e in ents:
        if not e.get("role"):
            continue
        for p in json.loads(e.get("listen_ports") or "[]"):
            pn = int(p.split("/")[0])
            if pn >= _EPHEMERAL_FLOOR:
                continue
            # Count, do not first-wins: several roles may be observed listening on one port
            # (a DC and a file server both answer 445), and taking whichever entity happened
            # to sort first produced emits aimed at the wrong role — `smb -> dns-resolver`.
            serves_votes[pn][e["role"]] += 1

    serves: dict[int, str] = {p: _resolve_serves(p, v) for p, v in serves_votes.items()}

    bands = activity_bands(pit)
    dial_counts: dict[str, Counter] = defaultdict(Counter)
    byte_p50: dict[str, list] = defaultdict(list)
    flow_p50: dict[str, list] = defaultdict(list)
    for e in ents:
        base_role = e.get("role")
        # LOCAL members only, for the same reason every other census here is
        # (`3dedba8`): a persona for role R describes what R's hosts IN THIS TWIN do, and 233
        # of this source's 356 roled entities are internet peers the twin will never deploy.
        #
        # Not optional once `activity_bands` is locality-filtered, and the coupling is worth
        # spelling out because it inverts the sign of the fix. An external entity has no band
        # any more, so `.get(key, "")` files every one of them under its role's BASE group --
        # and measured on this source that turned the base `generic-endpoint` group's mean
        # per-window bytes from 11,622 into 85,726,890 (a factor of 7,375, `low` -> `bulk`)
        # on the strength of 15 external hosts, which would then have driven all 102 twin
        # endpoints at 64 MiB a fire. `_cadence_for`'s own docstring is the standing warning
        # here: a twin that floods its own sensor is not a faithful twin.
        if not base_role or e.get("locality") != "local":
            continue
        # Keyed on (role, band), so a heavy hitter's persona is derived from HEAVY hosts'
        # observations rather than from its role's mean. This is the whole point of banding:
        # without it every band would be driven at the same rate and the split would be cosmetic.
        b = bands.get(e["entity_key"], "")
        role = f"{base_role}-{b}" if b else base_role
        for p in json.loads(e.get("dial_ports") or "[]"):
            if int(p.split("/")[0]) < _EPHEMERAL_FLOOR:
                dial_counts[role][p] += 1
        tp = profiles.get(e["entity_key"])
        if tp:
            byte_p50[role].append(_p50(tp.get("bytes_per_window_q")))
            flow_p50[role].append(_p50(tp.get("flows_per_window_q")))

    budgets = emit_budgets(pit, bands)
    weights = proto_weights(pit, bands)
    edge_targets = role_edge_targets(pit)

    population = local_population(pit, bands)

    out: dict[str, dict] = {}
    for group, ports in sorted(dial_counts.items()):
        # How many emits this group's observed edge-row count supports. Replaces a fixed
        # `most_common(12)`, which over-served `generic-endpoint` (8 rows/host vs the
        # source's 3) and under-served `corp-workstation` (5 vs 16) simultaneously.
        budget = budgets.get(group, 1)
        # Rank candidate ports by how much of THIS group's observed conversation the port's
        # protocol actually carries, falling back to dial frequency for anything the edge
        # table cannot weight. `most_common` order is preserved as the tiebreak, so a group
        # with no usable weights behaves exactly as before.
        wt = weights.get(group) or Counter()
        ranked = sorted(
            ports.most_common(),
            key=lambda kv: (-observed_weight(wt, int(kv[0].partition("/")[0])), -kv[1]),
        )
        # Spend the budget across DESTINATION ROLES in the source's own proportions, not
        # purely by port weight. A role edge is `sum(hosts x emits at that role)`, so the
        # most numerous band decides the histogram: ranking alone left the light band (47 of
        # 102 endpoints) buying dns+ntp and contributing zero web-tier edges, against a
        # source whose heaviest edge IS generic-endpoint->web-tier (GAP-C21).
        base_role = group
        for suffix in ("-heavy", "-light"):
            if base_role.endswith(suffix):
                base_role = base_role[: -len(suffix)]
                break
        targets = edge_targets.get(base_role) or Counter()
        # Gated on `targets`, not on the quota. A role whose entire observed conversation left
        # the network has an EMPTY local quota, and the old `if quota:` test read that as "no
        # proportions to honour" and fell through to plain ranking -- manufacturing exactly the
        # local edges the sink exists to stop. Measured: `web-tier` has 0 local role-to-role
        # edges against 226 external ones.
        gated = bool(targets)
        local_targets = Counter({r: n for r, n in targets.items() if r != SINK_ROLE})

        # What this group's own dialled ports can actually reach LOCALLY, per destination role.
        # A quota slot at a role no port of this group resolves to is unspendable, and before
        # this it was simply LOST -- the loop ran out of candidates and the persona emitted
        # fewer rows than its budget (the journal's "its ports run out before its budget does",
        # `corp-workstation` 16 budgeted / 8 emitted). Capping the allocation at what the ports
        # can carry and handing the remainder to the SINK conserves the budget instead, which
        # is what keeps `external_dst_edge_share` where the source put it -- and it is the same
        # reading the port loop already applies one level down: a conversation this twin cannot
        # place locally is one that left the network.
        capacity: Counter = Counter()
        for port_proto, _n in ranked:
            pn = int(port_proto.partition("/")[0])
            if pn in _GROUP_PORTS:
                continue
            peer = serves.get(pn)
            if peer and peer != group and peer in local_targets:
                capacity[peer] += 1

        # POPULATION allocation (`local_population`). The local share is spent across the
        # group's HOSTS -- a minority carrying `degree` local rows each, the rest entirely
        # external -- rather than inside every host, where `round(budget x share)` bottoms out
        # at one and gives the twin a floor of ~1 local edge per host that the source does not
        # have (its `generic-endpoint` median local degree is 0).
        pop = population.get(group) or {}
        degree = min(budget, int(pop.get("degree", 0) or 0))
        # A role the source shows NO outbound conversation for gets no sink rows, ever.
        # Spilling an unspendable LOCAL quota to the sink is right when the source did leave
        # the network -- it is the same reading the port loop already applies -- but for
        # `hypervisor` and `log-shipper`, whose every observed edge stayed inside, it would
        # invent outbound traffic their source counterparts never carried.
        outbound = targets.get(SINK_ROLE, 0) > 0

        def _sink(rows: int, _outbound=outbound) -> int:
            return max(0, rows) if _outbound else 0

        # (suffix, local quota, rows left for the sink, port to promote)
        variants: list[tuple[str, Counter, int, Optional[str]]] = [
            ("", Counter(), _sink(budget), None)]
        # The chatter variant: one link-local emit, the rest of the budget outbound. Promoted
        # rather than merely allowed, because a group port cannot win a slot on rank (see
        # `_compose_emits`) -- and ONE, because the population it describes is the host whose
        # entire local presence is that single discovery conversation.
        top_group_port = next((pp for pp, _n in ranked
                               if int(pp.partition("/")[0]) in _GROUP_PORTS), None)
        if chatter and top_group_port and (pop.get("chatter_share") or 0) > 0:
            variants.append((CHATTER_VARIANT_SUFFIX, Counter(), _sink(budget - 1),
                             top_group_port))
        if degree and capacity and gated:
            local = allocate_budget(degree, local_targets)
            local = +Counter({r: min(n, capacity[r]) for r, n in local.items()})
            if local:
                variants.append((LOCAL_VARIANT_SUFFIX, local,
                                 _sink(budget - sum(local.values())), None))

        base_emits: Optional[list] = None
        for suffix, quota, sink_rows, promote in variants:
            emits, rows = _compose_emits(group=group, ranked=ranked, quota=quota,
                                         budget=budget, serves=serves, gated=gated,
                                         sink_rows=sink_rows, promote=promote)
            if not emits:
                continue
            if suffix == "":
                # A COPY, taken before `cadence`/`volume` are written below. Comparing against
                # the live list would compare a decorated emit to a bare one and never match,
                # so every group would ship a variant identical to its base and a partition
                # that means nothing (found by mutation-testing this very check).
                base_emits = [dict(e) for e in emits]
            elif emits == base_emits:
                # A variant that composes to the base persona is not a variant. Dropped here
                # rather than left to be assigned, so a group whose ports cannot express the
                # split does not ship two identical documents and a partition that means
                # nothing (`network-device`, whose one port IS its chatter).
                continue
            # RATE, over the rows this VARIANT actually put on the wire.
            #
            # The role's observed byte rate is shared across its emits, not repeated per emit:
            # a host seen moving 800 KB/s in total does not move 800 KB/s on each of four
            # ports. The denominator therefore has to be the number of EDGE ROWS emitted --
            # which is why it is computed here rather than inside the loop, since a
            # `peer_scope: all` emit contributes its whole fan-out and the total is not known
            # until the last one is placed.
            #
            # It used to be `min(len(ports), budget)`, whose comment already CLAIMED to be the
            # row count. That was true only while every emit was worth exactly one row. Our own
            # sink emits broke the identity -- rows are now the sum of the fan-out weights and
            # exceed `len(ports)` -- so the twin over-offered by the ratio between them:
            # measured `dns-resolver-heavy` 2.43x, `domain-controller` 2.33x,
            # `monitoring-collector` 2.00x. A fan-out emit is still not divided a second time:
            # this is the rate ONE row carries, and `peer_scope: all` sends it to each host of
            # the sub-role, so a weight-4 emit moves exactly the four rows' worth of the
            # observed rate that those four rows represent.
            #
            # PER VARIANT, and that is what keeps the twin's offered rate unchanged by the
            # split: both variants of a group spend the same `mean_bytes`, so a host offers its
            # role's observed rate whether it talks locally or not.
            mean_bytes = sum(byte_p50[group]) / max(1, len(byte_p50[group]))
            mean_flows = sum(flow_p50[group]) / max(1, len(flow_p50[group]))
            tier = _volume_for(mean_bytes / max(1.0, mean_flows))
            per_row_rate = (mean_bytes / window_s) / max(1, rows)
            cadence = _cadence_for(tier, per_row_rate)
            for emit in emits:
                emit["cadence"], emit["volume"] = cadence, tier
            img = (image_of or {}).get(group, "")
            pid = f"cast-{group}{suffix}"
            out[pid] = {"persona": {
                "id": pid,
                "os_family": "windows" if "win" in img else "linux",
                # Always `scripted`: a synthesized persona exists to EMIT. `real-service`
                # asserts a daemon the twin was never told to install, which is how six roles
                # ended up invisible in their own capture (GAP-C6).
                "drive": "scripted",
                "emits": emits,
                # Deliberately empty. Every peer here was resolved from this twin's own listen
                # census, so it exists by construction; declaring hard requires would only
                # re-open GAP-C5 for no gain.
                "requires": [],
                "knobs": {"intensity": 1.0, "schedule": "24x7", "seed_jitter": True},
            }}
    return out


def node_instances(node: dict) -> list[str]:
    """The node ids axRange will actually deploy for one twin-config node group.

    A `count: N` group fans out to `<name>01..<name>0N`; a `count: 1` group (or one with no
    `count:` at all) keeps its BARE name. That asymmetry is the whole trap — half the groups
    look like they need no expansion, so a rule that always appends an index is as wrong as one
    that never does.

    Verified against axRange's own expander rather than inferred: on the corpapcn twin this
    reproduces all 138 instance ids exactly, including `corp-workstation-10-80-2` (count 3) ->
    `...-201/-202/-203`, `corp-workstation-10-80-48` (count 1) -> unchanged, and
    `internet-sink-2` -> `internet-sink-201`/`-202`.

    Lives here rather than only in `scripts/install_cast_as_twin.py` because BOTH now need it
    and they must agree exactly: the installer validates every emitted assignment against the
    instance set it derives, so two copies of this rule drifting apart would fail a cast at
    install time with "is not a host in target twin" — after the previous twin is already
    destroyed. One rule, imported by both.
    """
    name = str(node["name"])
    count = int(node.get("count", 1) or 1)
    return [name] if count <= 1 else [f"{name}{i:02d}" for i in range(1, count + 1)]


def _group_of(node: dict) -> tuple[str, str]:
    """`(base_role, group)` for one twin-config node — the band read back off its NAME.

    `compiler.py` puts the band INTO the node name (`generic-endpoint-heavy-10-80-2`) precisely
    so a heavy hitter is visible as such in the twin config rather than hidden inside a count,
    and that is the only place the band survives into the deployable documents.
    """
    role = str(node.get("role") or "")
    name = str(node.get("name") or "")
    for band in ("heavy", "light"):
        if f"-{band}-" in name or name.endswith(f"-{band}"):
            return role, f"{role}-{band}"
    return role, role


def plan_assignments(nodes: list[dict], personas: dict[str, dict],
                     population: dict[str, dict],
                     assignments: list[dict]) -> list[dict]:
    """Re-point a cast's traffic assignments at the twin's own nodes, splitting each group's
    hosts between its LOCAL-BEARING and EXTERNAL-ONLY persona variants.

    The split is what makes `local_population`'s measurement expressible at all. A persona is
    one document applied to many hosts, so "29% of this role talks locally" cannot be said
    inside a persona -- only by giving 29% of the role's hosts a different one. axRange's
    traffic profile can already say that: an assignment may name a NODE, and the installer
    expands a node group to its instances, so a group's hosts can be partitioned without the
    twin config moving at all. That is the whole reason this is a `tfc`-tier change and needs
    no redeploy.

    Three rules it has to hold, all of them failure modes rather than preferences:

      * **One persona instance per host.** axRange refuses a whole profile when two assignments
        claim one host ("claimed by both 'role:X' and 'node:Y'"), so a role that goes
        node-targeted must go FULLY node-targeted -- its role-level entry is dropped, not left
        alongside. The partition here is a single pass over an ordered list, so the two variants
        cannot both claim a host even in principle.
      * **A silent host is worse than a failure.** A band whose own persona was never
        synthesized falls back to its role's base persona rather than losing its assignment: its
        role-level entry has just been dropped, so an unmatched band would generate nothing at
        all and quietly shrink the twin (dev45, 2026-08-27: `dns-resolver-light-10-21-161`).
      * **Determinism.** Node groups are sorted by name and instances taken in order, so which
        hosts carry local conversation is a function of the twin config alone (`LES-002`).

    A role stays ROLE-targeted when every one of its hosts resolves to the same persona and it
    carries no bands -- that keeps `seed` and `intensity` on the assignment, which the
    node-targeted shape has never carried, and keeps this change to the roles that actually
    need splitting.
    """
    roled = [n for n in nodes if n.get("role")]
    by_group: dict[str, list[dict]] = defaultdict(list)
    for node in sorted(roled, key=lambda n: str(n.get("name") or "")):
        by_group[_group_of(node)[1]].append(node)

    banded = {_group_of(n)[0] for n in roled if _group_of(n)[1] != _group_of(n)[0]}
    # {node name: {instance: persona id}} -- built per GROUP, because the bearer share is a
    # property of the role's whole population and not of any one segment's node group.
    chosen: dict[str, dict[str, str]] = defaultdict(dict)
    for group, group_nodes in sorted(by_group.items()):
        base_role = _group_of(group_nodes[0])[0]

        def _persona(suffix: str, _g=group, _b=base_role) -> Optional[str]:
            # The bare `cast-<base_role>` tail is the BAND fallback and applies to the plain
            # lookup only. On a variant lookup it would answer "is there a chatter variant for
            # this group?" with the group's ORDINARY persona -- so with `--no-chatter`, where
            # no chatter variant exists anywhere, a banded group would hand its base role's
            # plain persona to `chatter_share x N` of its hosts and call that a split. Latent
            # while chatter is always on (every group that could ask had one); live the moment
            # the flag can turn it off.
            tail = (f"cast-{_b}",) if not suffix else ()
            for pid in (f"cast-{_g}{suffix}", f"cast-{_b}{suffix}") + tail:
                if pid in personas:
                    return pid
            return None

        # A group whose whole budget is local has no external-only variant to emit at all
        # (`log-shipper`: one port, one local peer, no observed outbound). Its hosts still
        # need a persona, so the first variant that exists becomes the group's default --
        # never `None`, which would leave the node SILENT after its role-level assignment has
        # been dropped.
        plain = (_persona("") or _persona(LOCAL_VARIANT_SUFFIX)
                 or _persona(CHATTER_VARIANT_SUFFIX))
        if plain is None:
            continue
        pop = population.get(group) or {}
        instances = [(n, i) for n in group_nodes for i in node_instances(n)]
        ids = [i for _n, i in instances]
        # Partitioned in ONE pass over one ordered list, and that is what upholds the
        # double-claim rule structurally: axRange refuses a profile in which two assignments
        # name the same host, and a partition cannot produce one even if the shares are wrong.
        # Bearers are taken first because a host that talks to a local peer is described by
        # the local-bearing persona whether or not it also chatters -- the same precedence
        # `local_population` counts the two populations with.
        picked: dict[str, str] = {}
        for suffix, key in ((LOCAL_VARIANT_SUFFIX, "share"),
                            (CHATTER_VARIANT_SUFFIX, "chatter_share")):
            variant = _persona(suffix)
            # No such variant to give out (the group's dialled ports reach no local peer or
            # carry no group port, or the source shows it no such population) -- nothing is
            # selected at all, so a group that cannot split cannot accidentally.
            if not variant or variant == plain:
                continue
            free = [i for i in ids if i not in picked]
            for i in select_bearers(free, bearer_count(pop.get(key, 0.0), len(ids))):
                if i not in picked:
                    picked[i] = variant
        for node, inst in instances:
            chosen[str(node["name"])][inst] = picked.get(inst, plain)

    node_of_role: dict[str, set[str]] = defaultdict(set)
    for node in roled:
        for pid in chosen.get(str(node["name"]), {}).values():
            node_of_role[str(node["role"])].add(pid)
    # A role goes node-targeted when it is banded (as before), or when its hosts do not all
    # resolve to one persona -- which is exactly the case the population split creates.
    node_targeted = {r for r, pids in node_of_role.items() if len(pids) > 1} | banded

    out: list[dict] = []
    for a in assignments:
        role = a.get("role")
        if role and role in node_targeted:
            continue
        if role and node_of_role.get(role):
            # Not banded and not split: keep the role-level entry, with its seed and
            # intensity, and only re-point it at the persona its hosts actually resolved to.
            a = {**a, "persona": next(iter(node_of_role[role]))}
        out.append(a)

    for node in sorted(roled, key=lambda n: str(n.get("name") or "")):
        if str(node.get("role")) not in node_targeted:
            continue
        picked = chosen.get(str(node["name"])) or {}
        if not picked:
            continue
        distinct = set(picked.values())
        if len(distinct) == 1:
            # The whole node group takes one persona: emit ONE group-level assignment and let
            # the installer expand it, exactly as before. Only a group the split actually cuts
            # through is written out instance by instance.
            out.append({"node": str(node["name"]), "persona": distinct.pop(),
                        "schedule": "24x7"})
            continue
        # Insertion order, not `sorted()`: instance ids are zero-padded to TWO digits, so a
        # group of 100+ hosts sorts `...100` before `...11`. `picked` was built in the
        # expander's own order, which is the one an operator reading the file expects.
        for inst, pid in picked.items():
            out.append({"node": inst, "persona": pid, "schedule": "24x7"})
    return out
