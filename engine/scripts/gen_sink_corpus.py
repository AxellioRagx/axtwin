#!/usr/bin/env python3
"""Generate the twin's SINK corpus -- the DNS map and route set a cast's external world becomes.

    .venv/bin/python scripts/gen_sink_corpus.py \\
        --topology twins/pit-395a61f04e-1780405200/generated/pit-395a61f04e-1780405200.topology.json \\
        --out twins/<twin>/scripts

**What the sink is.** A twin's clients must run REAL protocol clients, and a real
client needs a real server on the other end of every destination the cast observed
(ruling, 2026-09-21: *"a proxy that all external IPs are routed to, which responds as
if the remote servers were responding"*). Egress to the real internet stays DENIED
during measurement, so the sink is the only thing out there. This script turns the
cast's observation of the external world into the two data files that sink needs:

  1. **`db.axr-sink-root`** -- a bind9 zone for `.` (the ROOT), carrying one A record
     per observed external name at its real observed address.
  2. **`external-cidrs.txt`** -- the external address set, collapsed, for whoever
     routes traffic at the sink (the twin's router) and for the sink's own
     accept-any-IP route.

plus **`install-sink-corpus.sh`**, a self-contained `run-script` payload that writes
both into `/etc/axr-sink/` in the guest, because `run-script` (OP-APP-030) delivers a
script and not a file tree. `twins/_shared/scripts/serve-sink.sh` consumes
what this installer leaves behind and fails loud when it is absent.

**Why a ROOT zone and not a hosts file.** The requirement is "authoritative for
everything, NXDOMAIN only for names not in the corpus". A resolver that is
authoritative for `.` gives exactly that semantics for free and by the book -- a name
in the zone answers, a name outside it is NXDOMAIN, nothing is ever forwarded, and no
upstream is ever consulted (there is none). The alternative -- dnsmasq over a
hosts-style `addn-hosts` -- needs a catch-all `local=/#/` to turn "I have no upstream"
(SERVFAIL/REFUSED) into NXDOMAIN, i.e. the right answer only as a side effect of a
wildcard forwarding rule. bind9 is also what the cast's own resolver recipes already
run (`twins/svc-verify/scripts/serve-dns-resolver-*.sh`), and the zone can be
checked OFFLINE with `named-checkzone`, which is worth a lot in a round that is not
allowed near a cluster. A hosts-style file is emitted too (`--format hosts|both`) for
the dnsmasq fallback and for eyeballing; the sink does not load it.

**Every observed address for a name is kept, not one of them** (LES-227): 301 of the
626 names in the reference cast resolve to several addresses. Collapsing them to one
would under-claim a fact the cast actually has, and a client that pins or round-robins
would see a twin that is wrong in a way that reads like a client bug. Both directions
of the many-to-many are handled: several names may share one address (the zone repeats
the address; the hosts file groups the names on one line) and one name may hold
several (several A records, sorted).

**IPv6 is SKIPPED by default** (`--include-aaaa` to keep it). The twins are v4-only:
an AAAA record pointing at a real external v6 address that nothing routes to the sink
turns a v6-preferring client into a timeout, which is strictly worse than the A-only
answer it falls back to. The count of skipped v6 answers is reported, never silent.

**Determinism is a property, not an aspiration.** Same topology in, byte-identical
files out: every collection is sorted, and the zone's SOA serial is derived from a
digest of the records themselves (the same "content-derived, never a clock" rule the
cast's own zone recipe follows). `tests/test_sink_corpus.py` asserts it.

**An empty corpus is a fatal error, never an output.** A sink with no names NXDOMAINs
every client in the twin, and the twin then fails in a shape that reads like a broken
client rather than a missing corpus.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import ipaddress
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# A DNS owner name we are willing to put in a zone file: LDH labels, plus the
# underscore that service names (`_ldap._tcp....`) carry. Anything else is counted
# and skipped rather than fed to `named-checkzone` to be discovered on-cluster.
_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?$")

_ZONE_EOF = "AXR_SINK_ZONE_EOF"
_CIDR_EOF = "AXR_SINK_CIDR_EOF"

#: Where the in-guest recipe (`twins/_shared/scripts/serve-sink.sh`) looks.
GUEST_DIR = "/etc/axr-sink"

ZONE_FILE = "db.axr-sink-root"
HOSTS_FILE = "sink-hosts"
CIDR_FILE = "external-cidrs.txt"
MANIFEST_FILE = "sink-corpus-manifest.json"
INSTALLER_FILE = "install-sink-corpus.sh"

#: Substituted in-guest by `serve-sink.sh` with the sink's own address -- the same
#: `@@IP@@` idiom the cast's resolver recipe uses, for the one record (the zone's NS)
#: whose value cannot be known until the twin is deployed.
SINK_IP_PLACEHOLDER = "@@SINK_IP@@"


class SinkCorpusError(RuntimeError):
    """A topology we refuse to generate from, or a corpus we refuse to emit."""


@dataclass
class Corpus:
    """The generated corpus plus the counts that justify it."""

    names: dict[str, list[str]] = field(default_factory=dict)
    cidrs: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def answer_count(self) -> int:
        return sum(len(v) for v in self.names.values())


# --------------------------------------------------------------------------- input


def load_topology(path: Path) -> dict:
    """Read a cast `topology.json`. Fail loud on anything that is not one."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SinkCorpusError(f"cannot read topology {path}: {exc}") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SinkCorpusError(f"topology {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SinkCorpusError(f"topology {path} is not a JSON object (got {type(doc).__name__})")
    return doc


def _require(doc: dict, key: str, kind: type, where: str):
    if key not in doc:
        raise SinkCorpusError(f"topology is missing '{where}' -- not a cast topology.json")
    value = doc[key]
    if not isinstance(value, kind):
        raise SinkCorpusError(
            f"topology '{where}' is a {type(value).__name__}, expected {kind.__name__}"
        )
    return value


def _entity_address(entity: str, where: str) -> ipaddress._BaseAddress:
    """`address:1.2.3.4` -> an ip address object. The `address:` form is the contract."""
    if not isinstance(entity, str) or not entity.startswith("address:"):
        raise SinkCorpusError(f"{where}: entity {entity!r} is not an 'address:<ip>' entity")
    literal = entity.split(":", 1)[1]
    try:
        return ipaddress.ip_address(literal)
    except ValueError as exc:
        raise SinkCorpusError(f"{where}: entity {entity!r} carries no valid IP: {exc}") from exc


#: Addresses no unicast sink can stand in for, spelled out rather than taken from
#: `ipaddress.is_private` -- that property also covers the documentation ranges
#: (192.0.2/24, 198.51.100/24, 203.0.113/24), which are exactly what a test fixture
#: and a lab write down, and dropping those would be a surprise with no upside.
_NON_SINKABLE_V4 = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",  # "this network" / the unspecified address
        "10.0.0.0/8",  # RFC1918 -- twin-internal space
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local
        "172.16.0.0/12",  # RFC1918
        "192.168.0.0/16",  # RFC1918
        "224.0.0.0/4",  # multicast (SSDP's 239.255.255.250 lives here)
        "240.0.0.0/4",  # reserved, incl. 255.255.255.255 broadcast
    )
)


def _is_routable_external(address) -> bool:
    """Is this an address the sink can plausibly BE?

    A cast's `external` set is whatever the vantage saw leave the local segments, and
    that includes things no unicast sink can stand in for: SSDP's 239.255.255.250, the
    all-ones broadcast, link-local, and any RFC1918 address that leaked in with an
    `external` label. Claiming those would be worse than dropping them -- a multicast
    group routed to a unicast sink breaks the discovery protocol it belongs to, and a
    private prefix claimed as external collides with the twin's OWN segments.
    """
    if address.version == 4:
        return not any(address in net for net in _NON_SINKABLE_V4)
    return not (
        address.is_multicast or address.is_loopback or address.is_link_local or address.is_unspecified
    )


def _normalize_name(name: str) -> str | None:
    """Lowercase + strip the root dot; `None` when it is not a name we will serve."""
    candidate = name.strip().rstrip(".").lower()
    if not candidate or len(candidate) > 253:
        return None
    labels = candidate.split(".")
    if len(labels) < 2:  # a bare label is not a routable external name
        return None
    if not all(len(lbl) <= 63 and _LABEL.match(lbl) for lbl in labels):
        return None
    return candidate


# ------------------------------------------------------------------------- building


def build_corpus(
    topology: dict,
    *,
    include_aaaa: bool = False,
    aggregate_prefix: int = 24,
    aggregate_min: int = 4,
) -> Corpus:
    """Turn a cast topology into the sink's name map + route set."""
    if not 8 <= aggregate_prefix <= 32:
        raise SinkCorpusError(f"--aggregate-prefix {aggregate_prefix} is outside 8..32")
    if aggregate_min < 0:
        raise SinkCorpusError(f"--aggregate-min {aggregate_min} is negative")

    observations = _require(topology, "observations", dict, "observations")
    dns = _require(observations, "dns", dict, "observations.dns")
    entries = _require(dns, "names", list, "observations.dns.names")
    nodes = _require(topology, "nodes", list, "nodes")

    stats = defaultdict(int)
    names: dict[str, set[str]] = defaultdict(set)
    answer_addrs: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()

    for index, entry in enumerate(entries):
        where = f"observations.dns.names[{index}]"
        if not isinstance(entry, dict):
            raise SinkCorpusError(f"{where} is a {type(entry).__name__}, expected an object")
        for key in ("entity", "name", "locality"):
            if key not in entry:
                raise SinkCorpusError(f"{where} is missing '{key}'")
        stats["observations"] += 1
        if entry["locality"] != "external":
            # Internal names belong to the twin's OWN resolver, not to the sink.
            stats["skipped_not_external"] += 1
            continue
        address = _entity_address(entry["entity"], where)
        if address.version == 6 and not include_aaaa:
            stats["skipped_ipv6"] += 1
            continue
        if not _is_routable_external(address):
            stats["skipped_non_unicast"] += 1
            continue
        if not isinstance(entry["name"], str):
            raise SinkCorpusError(f"{where}: 'name' is a {type(entry['name']).__name__}, expected a string")
        name = _normalize_name(entry["name"])
        if name is None:
            stats["skipped_unservable_name"] += 1
            continue
        names[name].add(str(address))
        answer_addrs.add(address)

    # The route set is EVERY external peer the cast saw, not only the ones a DNS answer
    # named: a client that dials a literal address (or one whose name was never
    # observed) must still land on the sink rather than on a denied-egress black hole.
    for index, node in enumerate(nodes):
        where = f"nodes[{index}]"
        if not isinstance(node, dict):
            raise SinkCorpusError(f"{where} is a {type(node).__name__}, expected an object")
        if node.get("locality") != "external":
            continue
        raw = node.get("address")
        if not isinstance(raw, str) or not raw:
            raise SinkCorpusError(f"{where}: external node has no 'address'")
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise SinkCorpusError(f"{where}: address {raw!r} is not an IP: {exc}") from exc
        stats["external_peers"] += 1
        if address.version == 6:
            stats["skipped_ipv6_peer"] += 1
            continue
        if not _is_routable_external(address):
            stats["skipped_non_unicast_peer"] += 1
            continue
        answer_addrs.add(address)

    v4 = sorted(
        (a for a in answer_addrs if a.version == 4), key=int  # type: ignore[arg-type]
    )
    cidrs = _collapse(v4, aggregate_prefix, aggregate_min)

    stats["names"] = len(names)
    stats["answers"] = sum(len(v) for v in names.values())
    stats["addresses"] = len(v4)
    stats["cidrs"] = len(cidrs)
    stats["claimed_addresses"] = sum(net.num_addresses for net in cidrs)

    corpus = Corpus(
        names={n: sorted(names[n], key=_addr_sort_key) for n in sorted(names)},
        cidrs=[str(net) for net in cidrs],
        stats={k: int(v) for k, v in sorted(stats.items())},
        provenance={
            key: str(topology[key])
            for key in ("imprint_id", "pit_id", "as_of", "schema_version")
            if isinstance(topology.get(key), (str, int, float))
        },
    )
    if not corpus.names:
        raise SinkCorpusError(
            "refusing to emit an empty corpus: the topology carries no servable external "
            "DNS name. A sink with no names NXDOMAINs every client in the twin, and the "
            "twin then looks broken in a way that reads like a client bug."
        )
    if not corpus.cidrs:
        raise SinkCorpusError(
            "refusing to emit an empty route set: the topology carries no external IPv4 "
            "address, so nothing would ever be routed to the sink."
        )
    return corpus


def _addr_sort_key(literal: str) -> tuple[int, int]:
    address = ipaddress.ip_address(literal)
    return (address.version, int(address))


def _collapse(
    addresses: list, prefix: int, minimum: int
) -> list[ipaddress.IPv4Network]:
    """Collapse addresses into the smallest set of CIDRs we are willing to claim.

    `minimum == 0` is LOSSLESS: `collapse_addresses` over the /32s, which merges only
    genuinely adjacent runs and claims not one address the cast did not observe (the
    reference cast: 2230 addresses -> 2063 CIDRs -- next to no saving, because a CDN's
    address set is sparse).

    `minimum > 0` trades precision for size: a /`prefix` holding at least `minimum`
    observed addresses is claimed WHOLE. Over-claiming is safe here in a way it would
    not be on a real network -- every address in the claimed block is external, egress
    is denied during measurement anyway, so the only effect of claiming a neighbour is
    that a client reaching a CDN address one rotation away from the observed one still
    finds the sink instead of a black hole. The default (/24, 4) takes the reference
    cast from 2063 CIDRs to 1279 while claiming ~28k unobserved external addresses.
    """
    if not addresses:
        return []
    nets = [ipaddress.ip_network(f"{a}/32") for a in addresses]
    if minimum:
        groups: dict[ipaddress.IPv4Network, list] = defaultdict(list)
        for net in nets:
            groups[net.supernet(new_prefix=prefix)].append(net)
        claimed: list[ipaddress.IPv4Network] = []
        for supernet in sorted(groups, key=lambda n: (int(n.network_address), n.prefixlen)):
            members = groups[supernet]
            if len(members) >= minimum:
                claimed.append(supernet)
            else:
                claimed.extend(members)
        nets = claimed
    return sorted(
        ipaddress.collapse_addresses(nets),
        key=lambda n: (int(n.network_address), n.prefixlen),
    )


# ------------------------------------------------------------------------ rendering


def _serial(body: str) -> int:
    """A content-derived zone serial. Never a clock -- a clock breaks determinism."""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2_147_483_647


def render_zone(corpus: Corpus) -> str:
    """A bind9 master zone for `.` -- authoritative for everything, NXDOMAIN outside."""
    records: list[str] = []
    for name in corpus.names:
        for address in corpus.names[name]:
            rrtype = "AAAA" if ":" in address else "A"
            records.append(f"{name}. IN {rrtype} {address}")
    body = "\n".join(records)
    head = [
        "; axRange twin SINK -- the observed external world, as a ROOT zone.",
        "; GENERATED by scripts/gen_sink_corpus.py from a cast topology.json; do not hand-edit.",
        ";",
        "; This file makes the sink AUTHORITATIVE FOR '.', which is exactly the semantics",
        "; wanted: a name below is answered with the address the cast really observed, and",
        "; every other name in the world is NXDOMAIN. Nothing is forwarded -- there is no",
        "; upstream to forward to, by design (egress is denied during measurement).",
        f"; names={corpus.stats.get('names', 0)} answers={corpus.answer_count}",
        "$TTL 60",
        ". IN SOA ns.axr-sink. hostmaster.axr-sink. (",
        f"        {_serial(body)} ; serial (content-derived, never a clock)",
        "        3600       ; refresh",
        "        600        ; retry",
        "        604800     ; expire",
        "        60 )       ; negative TTL",
        ". IN NS ns.axr-sink.",
        f"ns.axr-sink. IN A {SINK_IP_PLACEHOLDER}",
        ";",
        "; --- observed external names ------------------------------------------------",
    ]
    return "\n".join(head + records) + "\n"


def render_hosts(corpus: Corpus) -> str:
    """The same map, hosts-style: `<address> <name> [<name> ...]`, one line per address.

    Not loaded by the sink (bind9 reads the zone). Kept for the dnsmasq fallback
    documented in `twins/_shared/scripts/README.md` and for eyeballing.
    """
    by_address: dict[str, list[str]] = defaultdict(list)
    for name, addresses in corpus.names.items():
        for address in addresses:
            by_address[address].append(name)
    lines = [
        "# axRange twin SINK -- observed external names, hosts format.",
        "# GENERATED by scripts/gen_sink_corpus.py; do not hand-edit.",
    ]
    for address in sorted(by_address, key=_addr_sort_key):
        lines.append(" ".join([address] + sorted(by_address[address])))
    return "\n".join(lines) + "\n"


def render_cidrs(corpus: Corpus) -> str:
    lines = [
        "# axRange twin SINK -- the external address set, one CIDR per line.",
        "# GENERATED by scripts/gen_sink_corpus.py; do not hand-edit.",
        f"# cidrs={corpus.stats.get('cidrs', 0)} observed_addresses={corpus.stats.get('addresses', 0)}",
    ]
    lines.extend(corpus.cidrs)
    return "\n".join(lines) + "\n"


def render_manifest(corpus: Corpus, files: dict[str, str], options: dict) -> str:
    payload = {
        "generator": "scripts/gen_sink_corpus.py",
        "schema": "axr-sink-corpus/1",
        "source": corpus.provenance,
        "options": options,
        "counts": corpus.stats,
        "files": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in files.items()},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _gz_b64(text: str, marker: str, label: str) -> str:
    """gzip + base64 one corpus file, deterministically, for a heredoc.

    `mtime=0` (never a clock) and a fixed compression level keep "same topology in,
    byte-identical files out" true through the compression too; CPython writes a
    constant OS byte, so the gzip member is a pure function of the input. The 76-column
    wrapping is `base64.encodebytes`'s, and `base64 -d` ignores the newlines.
    """
    blob = base64.encodebytes(gzip.compress(text.encode("utf-8"), 9, mtime=0)).decode("ascii")
    if marker in blob:  # unreachable: `_` is not in the base64 alphabet
        raise SinkCorpusError(
            f"the encoded {label} corpus contains the heredoc marker {marker!r}; "
            "refusing to emit an installer that would truncate silently"
        )
    return blob


def render_installer(zone: str, cidrs: str) -> str:
    """A self-contained `run-script` payload that lays the corpus down in the guest.

    `run-script` (OP-APP-030) delivers ONE script, not a file tree, so the corpus
    travels as heredocs inside a script of its own. It is deliberately separate from
    `serve-sink.sh`: the recipe is hand-written shared content, this is generated data,
    and a twin declares them as two `install:` actions in that order.

    **The corpus travels COMPRESSED, and that is a delivery constraint, not a
    preference.** `run-script` hands the whole script to the guest base64-embedded in a
    single `agent/exec` COMMAND ARGUMENT (`GuestAgent.write_file`, LES-002), and PVE
    refuses an argument of 131072 bytes or more -- measured on dev45/PVE 9.2 on
    2026-09-21, where it appears as `HTTP 596 Broken pipe` with no error body at all.
    The plain-text corpus is ~137 KB, which base64s to ~183 KB and burned a live deploy.
    gzip takes the same bytes to ~22 KB (~30 KB of base64): a DNS zone of repeated
    labels and addresses is about as compressible as text gets. The corpus itself is
    untouched -- 613 names, 2254 records, every observed address -- because hand-
    trimming it would quietly change what "the observed external world" means.
    (`guest_agent` also chunks now, so this is belt AND braces; keep both -- the small
    payload is also seconds faster to deliver and to `named-checkzone`.)
    """
    zone_b64 = _gz_b64(zone, _ZONE_EOF, "zone")
    cidr_b64 = _gz_b64(cidrs, _CIDR_EOF, "cidr")
    return (
        "#!/bin/sh\n"
        "# axRange twin SINK corpus -- the observed external world, as guest data files.\n"
        "# GENERATED by scripts/gen_sink_corpus.py from a cast topology.json; do not hand-edit.\n"
        "#\n"
        "# Runs IN-GUEST as root through `run-script` (OP-APP-030), BEFORE\n"
        f"# `serve-sink.sh`, which fails loud when {GUEST_DIR}/{ZONE_FILE} is absent.\n"
        "#\n"
        "# The two data files arrive gzip+base64 (see render_installer's docstring: a\n"
        "# plain-text corpus exceeds the QGA per-argument ceiling). gzip's CRC is the\n"
        "# integrity check -- a truncated heredoc cannot decompress quietly -- and\n"
        "# `set -e` on the pipeline turns that into a failed op.\n"
        "set -eu\n"
        "umask 022\n"
        'command -v gunzip >/dev/null 2>&1 || { echo "axr-sink: gunzip is missing;'
        ' the corpus is gzip+base64 (gzip is Essential on Debian -- check the image)" >&2;'
        " exit 1; }\n"
        f"mkdir -p {GUEST_DIR}\n"
        "\n"
        f"base64 -d <<'{_ZONE_EOF}' | gunzip > {GUEST_DIR}/{ZONE_FILE}\n"
        f"{zone_b64}"
        f"{_ZONE_EOF}\n"
        "\n"
        f"base64 -d <<'{_CIDR_EOF}' | gunzip > {GUEST_DIR}/{CIDR_FILE}\n"
        f"{cidr_b64}"
        f"{_CIDR_EOF}\n"
        "\n"
        "# Fail loud on an empty delivery: a zero-record zone NXDOMAINs every client in the\n"
        "# twin, and that reads downstream like a broken client, not like a missing corpus.\n"
        f"AXR_RECORDS=$(grep -c ' IN A' {GUEST_DIR}/{ZONE_FILE} || true)\n"
        f"AXR_CIDRS=$(grep -c '^[0-9]' {GUEST_DIR}/{CIDR_FILE} || true)\n"
        'if [ "$AXR_RECORDS" -lt 1 ] || [ "$AXR_CIDRS" -lt 1 ]; then\n'
        f'    echo "axr-sink: empty corpus delivered to {GUEST_DIR}'
        ' ($AXR_RECORDS records, $AXR_CIDRS cidrs)" >&2\n'
        "    exit 1\n"
        "fi\n"
        'echo "axr-sink: corpus installed ($AXR_RECORDS records, $AXR_CIDRS cidrs)"\n'
    )


# --------------------------------------------------------------------------- output


def generate(
    topology_path: Path,
    *,
    fmt: str = "zone",
    include_aaaa: bool = False,
    aggregate_prefix: int = 24,
    aggregate_min: int = 4,
    installer: bool = True,
) -> tuple[Corpus, dict[str, str]]:
    """Build the corpus and render every output file. Returns (corpus, {name: text})."""
    corpus = build_corpus(
        load_topology(topology_path),
        include_aaaa=include_aaaa,
        aggregate_prefix=aggregate_prefix,
        aggregate_min=aggregate_min,
    )
    files: dict[str, str] = {}
    zone = render_zone(corpus)
    hosts = render_hosts(corpus)
    cidrs = render_cidrs(corpus)
    if fmt in ("zone", "both"):
        files[ZONE_FILE] = zone
    if fmt in ("hosts", "both"):
        files[HOSTS_FILE] = hosts
    files[CIDR_FILE] = cidrs
    if installer:
        files[INSTALLER_FILE] = render_installer(zone, cidrs)
    files[MANIFEST_FILE] = render_manifest(
        corpus,
        {k: v for k, v in files.items()},
        {
            "format": fmt,
            "include_aaaa": include_aaaa,
            "aggregate_prefix": aggregate_prefix,
            "aggregate_min": aggregate_min,
        },
    )
    return corpus, files


def write_outputs(out_dir: Path, files: dict[str, str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(files.items()):
        path = out_dir / name
        path.write_text(text, encoding="utf-8")
        if name.endswith(".sh"):
            path.chmod(0o755)


def summarize(corpus: Corpus, out_dir: Path, files: dict[str, str]) -> str:
    stats = corpus.stats
    lines = [
        f"sink corpus -> {out_dir}",
        f"  dns names            : {stats.get('names', 0)}",
        f"  dns answers (records): {stats.get('answers', 0)}",
        f"  external addresses   : {stats.get('addresses', 0)}",
        f"  routed cidrs         : {stats.get('cidrs', 0)}"
        f" (claiming {stats.get('claimed_addresses', 0)} addresses)",
        f"  skipped: not-external={stats.get('skipped_not_external', 0)}"
        f" ipv6-answer={stats.get('skipped_ipv6', 0)}"
        f" ipv6-peer={stats.get('skipped_ipv6_peer', 0)}"
        f" non-unicast={stats.get('skipped_non_unicast', 0)}"
        f"/{stats.get('skipped_non_unicast_peer', 0)}"
        f" unservable-name={stats.get('skipped_unservable_name', 0)}",
        "  files: " + ", ".join(sorted(files)),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topology", required=True, type=Path, help="a cast topology.json")
    parser.add_argument("--out", required=True, type=Path, help="directory to write the corpus into")
    parser.add_argument(
        "--format",
        choices=("zone", "hosts", "both"),
        default="zone",
        help="name-map format; the sink loads the bind9 ROOT zone (default: zone)",
    )
    parser.add_argument(
        "--include-aaaa",
        action="store_true",
        help="keep IPv6 answers (default: skip them -- the twins are v4-only and nothing routes v6 to the sink)",
    )
    parser.add_argument(
        "--aggregate-prefix", type=int, default=24, help="supernet length to aggregate into (default: 24)"
    )
    parser.add_argument(
        "--aggregate-min",
        type=int,
        default=4,
        help="observed addresses needed before a supernet is claimed whole; 0 = lossless /32 collapse (default: 4)",
    )
    parser.add_argument(
        "--no-installer", action="store_true", help="do not emit the in-guest install-sink-corpus.sh"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    args = parser.parse_args(argv)

    try:
        corpus, files = generate(
            args.topology,
            fmt=args.format,
            include_aaaa=args.include_aaaa,
            aggregate_prefix=args.aggregate_prefix,
            aggregate_min=args.aggregate_min,
            installer=not args.no_installer,
        )
    except SinkCorpusError as exc:
        print(f"gen_sink_corpus: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        write_outputs(args.out, files)
    print(summarize(corpus, args.out, files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
