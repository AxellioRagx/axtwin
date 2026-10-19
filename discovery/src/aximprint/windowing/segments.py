"""Address -> twin segment, and the locality verdict behind it (`WORK_ORDER_synth_v1.md` phase 1).

`twin_config.segments[].cidr` is a REQUIRED axRange field with no producer in this project until
now — DESIGN_BRIEF §13 step 3 named a "CIDR/gateway/domain" extractor and only the listen census
was ever built. This module is the CIDR half. It also supplies the internal/external partition,
which is the single largest reduction in the pipeline: on the 2026-08-11 baseline it takes 3,302
entities down to 307 twin nodes, because 2,995 of them are internet peers a twin cannot deploy.

**Two decisions are declared here rather than inferred, and both are reversible in one place.**

`SEGMENT_PREFIX = 24` is D2 of that work order, taken as recommended rather than user-ruled. A
FIXED prefix is what makes `segment_of()` a pure function of one address: no global pass over the
capture, so a segment can be computed at window close instead of at snapshot time. An inferred
prefix (from observed address density, or from a gateway's position) is a strictly better answer
and a strictly more expensive one — it needs the whole capture before it can label the first
window. Phase 4, if the twin's shape ever justifies it.

`locality()` prefers the SENSOR's judgment (`FlowEvent.local_src`/`local_dst`, from Zeek's
`Site::local_nets`) and falls back to an RFC1918-shaped test only when the sensor said nothing.
The fallback is marked, not hidden: it returns `imputed=True`, which becomes
`evidence_class="imputed"` on every assertion derived from it. That matters beyond bookkeeping —
**it is the first code path in this project that produces `imputed` at all.** The class has been
in the assertion vocabulary since v0 with no producer (`docs/PIPELINE_MAP.md`: *"`imputed` remains
the one evidence class no code emits"*), so until now the honesty claim it exists to support was
untested end to end.

Why the sensor's judgment is not merely more convenient: a site addressing internally out of
public space, or monitoring one carve-out of 10/8 rather than all of it, is invisible to an
address-shape test and correct in `local_nets`. The heuristic is the degraded mode, and the
output says which one produced it.

**No upward imports** (CONVENTIONS §3): stdlib only. `windowing` -> `events` is the only edge this
package is allowed, and this module does not even need that one.
"""
from __future__ import annotations

import ipaddress
from typing import Optional

#: D2, `docs/WORK_ORDER_synth_v1.md`. IPv4 only — see `SEGMENT_PREFIX_V6`.
SEGMENT_PREFIX = 24

#: IPv6's own aggregation, kept separate rather than reusing the v4 number: a /24 of IPv6 is a
#: routing-table entry, not a LAN. /64 is the standard single-subnet boundary and is what an
#: on-link address actually shares. Declared even though this baseline produces no v6 twin
#: segments, because the alternative — letting a v6 address fall through to a v4-shaped mask —
#: silently mints a segment nobody would recognize.
SEGMENT_PREFIX_V6 = 64

#: Verdicts `locality()` returns. `None` is deliberately NOT in this tuple: it is the absence of
#: a verdict, not a third value, and every consumer must handle it as such (`LES-006`).
LOCALITY_CLASSES = ("local", "external")

#: Where a locality verdict came from -> `(evidence_class, confidence)` for the `in_segment`
#: assertion built on it. The `SCOPE_EVIDENCE_CLASS` pattern one layer over
#: (`windowing/entities.py`): the two questions "did we see it" and "how much do we believe it"
#: are answered in ONE declared table, so a source cannot be recorded under one class and
#: published under another.
#:
#: `sensor` sits at 0.95 — the same figure the listen census uses — because Zeek's `local_nets`
#: is a deliberate operator statement about which address space is the monitored estate. It is
#: not 1.0: `local_nets` is configuration, and configuration can be stale or wrong.
#:
#: `address_shape` sits at 0.6, BELOW `bound_dns`'s 0.7, and that ordering is the point. A DNS
#: answer is at least an observation of something — a third party's claim, but a claim someone
#: actually made. RFC1918 membership is not an observation at all; it is this project inferring
#: a network's topology from the shape of a number. Two ways it is wrong in practice: a site
#: addressing its internal estate out of public space, and RFC1918 space that is routed but
#: outside the monitored estate. Both produce a confidently misplaced twin node, which is
#: exactly why the class is `imputed` and not `observed`.
#: **`vlan` is deliberately NOT a row here, and the measurement is why.**
#: `docs/WORK_ORDER_axquery_ingest.md` D3 added `FlowEvent.vlan_src`/`vlan_dst` and proposed
#: exactly such a row at `observed` — "a VLAN tag is direct segmentation evidence". Measured on
#: 50,796 real axquery flows (2026-08-20), it is neither thing it was proposed as:
#:
#:   * **not a locality signal** — **210 PUBLIC endpoints carry a tag.** A tag is the frame's
#:     tag at the capture point, and for routed traffic that is the NEXT HOP's VLAN, not the
#:     remote host's. A row here at `observed` would place internet hosts inside the twin
#:     wearing an observed face, which is strictly worse than the `address_shape` imputation it
#:     was meant to replace: that one at least declares itself imputed.
#:   * **not a refinement of the /24 either** — **7 of 27 private /24s span MULTIPLE VLANs**
#:     (`10.80.20.0/24` spans 20, 48, 64 and 192). VLAN does not subdivide the address-derived
#:     segment; it CROSS-CUTS it, because the two partitions answer different questions.
#:
#: Same shape as `docs/WORK_ORDER_axquery_ingest.md` D8's MAC finding: an L2 attribute observed
#: at a tap describes the PATH, not the endpoint. The fields are still collected (forensic
#: completeness) and a later analysis that treats a tag as a fact about the tap — which VLANs
#: this vantage sees — is well-founded. Treating it as a fact about a host is not.
SEGMENT_EVIDENCE: dict[str, tuple[str, float]] = {
    "sensor": ("observed", 0.95),
    "address_shape": ("imputed", 0.6),
}


def evidence_key(imputed: bool) -> str:
    """`SEGMENT_EVIDENCE` key for a verdict's `locality_imputed` flag.

    A named function rather than an inline conditional at the one call site, because the
    mapping from "was this imputed" to "which evidence row" is the thing a reader needs to
    check, and a bare `"address_shape" if x else "sensor"` buried in an f-string is where it
    would stop being checkable.
    """
    return "address_shape" if imputed else "sensor"


def _parse(addr: str) -> Optional[ipaddress._BaseAddress]:
    """The address, or None when it is not one.

    Never guesses. `_is_ip` in `ingest/zeek/plugin.py` learned the same lesson the expensive
    way: a `count(".") == 3` shape test read 34.4% of DNS-derived bindings' NAMES as addresses,
    each reaching the store wearing an `observed` face.
    """
    try:
        return ipaddress.ip_address(addr)
    except ValueError:
        return None


def locality(addr: str, sensor_says: Optional[bool]) -> tuple[Optional[str], bool]:
    """`(verdict, imputed)` — is this address inside the monitored network?

    `sensor_says` is `FlowEvent.local_src`/`local_dst`: `True`/`False` for a real judgment,
    `None` when the sensor left the field unset. The three cases are genuinely different and
    are kept different:

      * sensor said something -> its verdict, `imputed=False`.
      * sensor said nothing, address parses -> RFC1918/link-local/loopback shape,
        `imputed=True`.
      * address does not parse -> `(None, False)`. **Not** `("external", ...)`: an
        unparseable subject is no evidence at all, and calling it external would place a
        host outside the twin on the strength of a string that was never an address.

    `imputed` is False in the third case on purpose — nothing was imputed, because nothing was
    concluded. Returning True there would report an imputation that did not happen and would
    inflate the very counter that exists to disclose how much of the verdict was guessed.
    """
    if sensor_says is not None:
        return ("local" if sensor_says else "external"), False
    ip = _parse(addr)
    if ip is None:
        return None, False
    return ("local" if (ip.is_private or ip.is_loopback or ip.is_link_local)
            else "external"), True


def is_provisionable(ip: ipaddress._BaseAddress) -> bool:
    """Could a twin actually stand this address's network up and put a host on it?

    A **positive gate** on unicast host addressing (CONVENTIONS §3): everything that is not
    a host on a network somebody provisioned is rejected by name, rather than a blocklist
    that a new address family would slip past.

    Measured on the 2026-08-11 baseline, and this is why the function exists at all — the
    first end-to-end run of phase 1 minted **46** segments, of which five were not networks:

      | segment | hosts | what it actually is |
      |---|---|---|
      | `fe80::/64` | 105 | IPv6 link-local — every interface autoconfigures one |
      | `224.0.0.0/24` | 6 | multicast; a destination GROUP, never a host |
      | `169.254.169.0/24` | 1 | link-local (the cloud metadata endpoint) |
      | `239.255.255.0/24` | 1 | administratively-scoped multicast |
      | `::/64` | — | the unspecified address |

    Left in, `fe80::/64` would have been the LARGEST segment in the twin — 105 hosts on a
    network that cannot be deployed and that every one of them is simultaneously on. That is
    not a small error at the edges; it is the biggest single row of the output being fiction.

    Note what this deliberately does NOT do: it does not change the LOCALITY verdict. A host
    holding an `fe80::` address genuinely is on-link, and `locality()` still says so. "Is this
    inside the monitored network" and "which provisioned network is it on" are different
    questions, and only the second one is refused here.
    """
    return not (ip.is_multicast or ip.is_link_local or ip.is_loopback
                or ip.is_unspecified or ip.is_reserved)


def segment_of(addr: str) -> Optional[str]:
    """The segment CIDR this address belongs to, or None if it cannot name one.

    None for two different reasons — not an address at all, or an address on nothing a twin
    could provision (`is_provisionable`). The caller counts the second case separately
    (`health["segments_declined_endpoints"]`) so a decline stays visible rather than looking
    like an absence of traffic.

    Locality is NOT checked here — that is the caller's job, and separating them is what keeps
    this a pure address function. An external address has a well-defined /24; it simply must
    not become a twin segment, which is a decision about what to DO with the answer.
    """
    ip = _parse(addr)
    if ip is None or not is_provisionable(ip):
        return None
    prefix = SEGMENT_PREFIX if ip.version == 4 else SEGMENT_PREFIX_V6
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def segment_name(cidr: str) -> str:
    """A twin-config segment NAME for a CIDR — axRange requires both, and `name` is a
    node's `net:` reference, so it has to be an identifier rather than a CIDR string.

    `10.80.72.0/24` -> `seg-10-80-72-0`. Deterministic and collision-free by construction
    (the CIDR is recoverable from the name), which is what `LES-002` needs: a name that
    sorted differently between two runs would renumber a published column.
    """
    net, _, _bits = cidr.partition("/")
    return "seg-" + net.replace(".", "-").replace(":", "-")
