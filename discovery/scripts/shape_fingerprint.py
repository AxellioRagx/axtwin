#!/usr/bin/env python3
"""Reduce a materialized IR store to a NETWORK-SHAPE fingerprint, and diff two of them.

`docs/JOURNAL_corpapcn_fidelity.md`. Two different networks share no addresses, so nothing
address-keyed can compare them; what can be compared is shape. Every metric here is therefore
keyed on ROLE or on a distribution, never on an entity.

Deliberately NOT a single similarity score. One number would hide which axis diverged, and the
axes fail for unrelated reasons: role coverage is aximprint's inference, proto_mix and the flow
quantiles are the persona driver's fidelity, and segment count is the vantage. A score that
averaged them would move for reasons a reader could not attribute.

    fingerprint <ir-store> [ext-segments]            -> JSON fingerprint
    compare <ir-store-A> <ir-store-B> [ext-segments]  -> per-axis diff, A=source B=twin

`ext-segments` is a comma/space-separated list of segment CIDRs to count as EXTERNAL
regardless of what the sensor stamped, and on `compare` it applies to the B (twin) side
ONLY -- the source has no synthetic sink and must be fingerprinted exactly as before.

It exists for the twin's synthetic "internet sink": a node group inside the twin that hosts
talk to so their outbound share stops being spent on local edges. `locality` is a
SENSOR-STAMPED entity flag, not an address property (`src/aximprint/windowing/segments.py`
`locality()`): XPress-SW-A's Zeek has `Site::local_nets` covering 10/8, so a sink anywhere in
private space arrives stamped `local` with observed/0.95 evidence. No choice of address range
avoids that, so the reclassification has to happen HERE, after ingest, and not by weakening
what the sensor is allowed to say.

Key on the CIDR (`ir_entity.segment`), never the segment NAME: names carry the observed
octets (`seg-10-80-72-0`) and `--relocate` does not rewrite them, so in a relocated twin a
name says where a segment USED to be.
"""
import json
import sys
from collections import Counter

import duckdb


def _parse_segments(arg) -> frozenset:
    """Comma/space-separated CIDRs -> a set. Blank/empty -> the empty set (no override).

    Blanks are dropped rather than kept, because a set containing "" or None would match
    every entity that has no segment at all and silently flip the un-placed locals external
    -- the opposite of the narrow, one-segment override this is for.
    """
    if not arg:
        return frozenset()
    return frozenset(p for p in arg.replace(",", " ").split() if p)


def _is_local(entity: dict, external_segments=frozenset()) -> bool:
    """Does this entity count as INSIDE the measured network?

    The single gate for every locality-sensitive axis in the fingerprint -- the local/placed/
    roled counts, `roled_unplaced`, `role_edges_local`, `fan_out_degree_local` and
    `external_dst_edge_share` all resolve through here, so the override is one predicate
    rather than four consistent-by-inspection copies.

    With the default empty `external_segments` this is character-for-character the sensor's
    own verdict, which is what keeps every earlier iteration's numbers comparable.
    """
    return (entity.get("locality") == "local"
            and entity.get("segment") not in external_segments)


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fingerprint(store: str, *, external_segments=frozenset()) -> dict:
    external_segments = frozenset(external_segments)
    con = duckdb.connect(store, read_only=True)
    pit = _rows(con, "select * from ir_pit")[0]
    ents = _rows(con, "select * from ir_entity")
    edges = _rows(con, "select * from ir_edge")

    local = [e for e in ents if _is_local(e, external_segments)]
    placed = [e for e in local if e.get("segment")]
    roled = [e for e in placed if e.get("role")]

    # role -> entity_key, so edges can be re-expressed role-to-role
    role_of = {e["entity_key"]: e["role"] for e in ents if e.get("role")}
    # Absent keys stay ABSENT: `is_local.get(k)` returning None for an edge endpoint that
    # never became an entity is falsy, so an unknown destination is counted external. That
    # fail-open direction is deliberate and is preserved -- an endpoint the materializer
    # could not resolve is, by construction, not a host the twin is standing up.
    is_local = {e["entity_key"]: _is_local(e, external_segments) for e in ents}

    role_edges = Counter()
    role_edges_local = Counter()
    for ed in edges:
        a, b = role_of.get(ed.get("src_key")), role_of.get(ed.get("dst_key"))
        if a and b:
            role_edges[f"{a}->{b}"] += 1
            # LOCAL destinations only -- the subset an air-gapped twin can actually
            # produce. `role_edges` conflates the two and rewards a twin for
            # manufacturing local traffic to match an externally-dominated total:
            # measured on CorpAPCN, `generic-endpoint->web-tier` is 298 edges of which
            # 295 are EXTERNAL and 3 local, while the twin scored 228 against it using
            # its 2 local web-tier hosts. That reads as convergence on the headline
            # metric and is a structural mismatch underneath. Same defect
            # `fan_out_degree_local` exists for, one metric over.
            if is_local.get(ed.get("dst_key")):
                role_edges_local[f"{a}->{b}"] += 1

    def _buckets(counter):
        # degree DISTRIBUTION, bucketed — raw per-host degrees are entity-keyed and uncomparable
        out = Counter()
        for _src, d in counter.items():
            out["1" if d == 1 else "2-5" if d <= 5 else "6-20" if d <= 20 else "21+"] += 1
        return out

    fan_out = Counter()
    fan_out_local = Counter()
    external_dst_edges = 0
    for ed in edges:
        fan_out[ed.get("src_key")] += 1
        if is_local.get(ed.get("dst_key")):
            fan_out_local[ed.get("src_key")] += 1
        else:
            external_dst_edges += 1
    deg_buckets = _buckets(fan_out)
    deg_buckets_local = _buckets(fan_out_local)

    return {
        "source": pit.get("source"),
        "network": pit.get("network"),
        "windows": pit.get("windows"),
        "span_s": (pit.get("observed_to") or 0) - (pit.get("observed_from") or 0),
        "counts": {
            "entities": len(ents), "local": len(local), "placed": len(placed),
            "roled": len(roled), "segments": pit.get("segments"), "edges": len(edges),
        },
        "role_coverage": round(len(roled) / len(placed), 4) if placed else 0.0,
        "role_census": dict(Counter(e["role"] for e in roled).most_common()),
        # Roled but NOT placed — an entity aximprint could name but not put on a network, so it
        # can never become a twin node. Mostly external peers (a public host answering on 443 is
        # a `web-tier` by the same rule an internal one is). Reported separately because it is
        # the difference between "the role vocabulary is too narrow" and "the role was inferred
        # and then discarded for want of a segment" — two different fixes.
        "roled_unplaced": dict(Counter(
            e["role"] for e in ents
            if e.get("role") and not (_is_local(e, external_segments) and e.get("segment"))
        ).most_common()),
        "role_edges": dict(role_edges.most_common(20)),
        "role_edges_local": dict(role_edges_local.most_common(20)),
        "fan_out_degree": dict(sorted(deg_buckets.items())),
        # The share of edges leaving the observed network. Reported because it BOUNDS what a
        # twin can reproduce: measured on CorpAPCN, 85% of source edges have a non-local
        # destination and 197 of its 230 degree-1 hosts talk ONLY to external peers, against
        # 6% / 56 in the deployed twin. An air-gapped twin has no internet to talk to
        # (axRange GAP-048: emits at the reserved `internet`/`egress` tokens put nothing on
        # the wire), so that share of the shape is structurally unreachable -- not a persona
        # tuning failure. Chasing `fan_out_degree` past this point is chasing a number the
        # twin cannot attain by construction.
        "external_dst_edge_share": round(external_dst_edges / len(edges), 4) if edges else 0.0,
        # The apples-to-apples companion: degree counted over LOCAL->LOCAL edges only, the
        # subset a twin can actually be held responsible for. Added alongside
        # `fan_out_degree` rather than replacing it, so every earlier iteration's numbers
        # stay comparable (the whole point of a per-axis fingerprint).
        "fan_out_degree_local": dict(sorted(deg_buckets_local.items())),
    }


def _cmp_dict(name, a, b, out):
    keys = sorted(set(a) | set(b))
    only_a = [k for k in keys if k in a and k not in b]
    only_b = [k for k in keys if k in b and k not in a]
    both = [k for k in keys if k in a and k in b]
    out.append(f"\n{name}: {len(both)} shared, {len(only_a)} source-only, {len(only_b)} twin-only")
    if both:
        # Jaccard on the KEY SET: how much of the source's VOCABULARY the twin reproduces.
        j = len(both) / len(set(a) | set(b))
        out.append(f"  key-set Jaccard: {j:.3f}")
        # ...and, separately, whether the SHAPE matches. Key-set Jaccard cannot see
        # distribution, and that is not academic: after the peer_scope fan-out change the
        # fan-out buckets scored a perfect 1.000 while the distribution had INVERTED — the
        # source is dominated by degree-1 hosts and the twin had piled into 6-20. A
        # membership measure reported success on a twin that had swapped one wrong shape for
        # another (`docs/JOURNAL_corpapcn_fidelity.md` iteration 5).
        #
        # Total variation distance over the normalised distributions: 0.0 identical, 1.0
        # disjoint. Reported alongside Jaccard, never blended into it — they answer different
        # questions and averaging them would hide exactly the case that motivated this.
        sa, sb = sum(a.get(k, 0) for k in keys), sum(b.get(k, 0) for k in keys)
        if sa and sb:
            tvd = 0.5 * sum(abs(a.get(k, 0) / sa - b.get(k, 0) / sb) for k in keys)
            out.append(f"  distribution TVD: {tvd:.3f}   (0 = same shape, 1 = disjoint)")
    for k in both[:12]:
        out.append(f"    {k:<34} source={a[k]:<8} twin={b[k]}")
    if only_a:
        out.append(f"  MISSING FROM TWIN ({len(only_a)}): {', '.join(map(str, only_a[:14]))}")
    if only_b:
        out.append(f"  TWIN-ONLY ({len(only_b)}): {', '.join(map(str, only_b[:14]))}")


def compare(a_store: str, b_store: str, *, b_external_segments=frozenset()) -> str:
    b_external_segments = frozenset(b_external_segments)
    # B SIDE ONLY. The source network has no synthetic sink, and applying the override to
    # both sides would silently re-measure the reference the twin is being scored against.
    a = fingerprint(a_store)
    b = fingerprint(b_store, external_segments=b_external_segments)
    out = [f"SOURCE  network={a['network']} windows={a['windows']} span={a['span_s']:.0f}s",
           f"TWIN    network={b['network']} windows={b['windows']} span={b['span_s']:.0f}s",
           "", "counts:"]
    for k in a["counts"]:
        out.append(f"    {k:<12} source={str(a['counts'][k]):<8} twin={b['counts'][k]}")
    out.append(f"\nrole_coverage:  source={a['role_coverage']:.3f}   twin={b['role_coverage']:.3f}"
               "     (roled / placed — how much of what was SEEN got a role)")
    _cmp_dict("role_census", a["role_census"], b["role_census"], out)
    _cmp_dict("role_edges", a["role_edges"], b["role_edges"], out)
    _cmp_dict("role_edges_local", a.get("role_edges_local", {}),
              b.get("role_edges_local", {}), out)
    _cmp_dict("fan_out_degree", a["fan_out_degree"], b["fan_out_degree"], out)
    _cmp_dict("fan_out_degree_local", a.get("fan_out_degree_local", {}),
              b.get("fan_out_degree_local", {}), out)
    # PRINTED, not just computed. This is the axis the synthetic internet sink exists to
    # move, and it bounds what `fan_out_degree` above can ever reach (see its note in
    # `fingerprint`). It was reaching the JSON and being dropped by `compare`, which is the
    # view an operator actually reads -- a run could not be evaluated on the one number it
    # was run to change.
    out.append(f"\nexternal_dst_edge_share:  source={a['external_dst_edge_share']:.4f}"
               f"   twin={b['external_dst_edge_share']:.4f}"
               "     (share of edges whose DESTINATION is outside the measured network)")
    if b_external_segments:
        out.append("  twin external-segment override: "
                   + ", ".join(sorted(b_external_segments)))
        # Disclosed because `counts.segments` above is read from the PIT and is NOT
        # adjusted: the sink IS a segment the vantage observed, and quietly decrementing a
        # vantage count to flatter a shape metric is the kind of edit this file exists to
        # not make.
        out.append("  (counts.segments above still includes it -- that count is the "
                   "vantage's, not this override's)")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) in (3, 4) and sys.argv[1] == "fingerprint":
        ext = _parse_segments(sys.argv[3] if len(sys.argv) == 4 else None)
        print(json.dumps(fingerprint(sys.argv[2], external_segments=ext), indent=2))
    elif len(sys.argv) in (4, 5) and sys.argv[1] == "compare":
        ext = _parse_segments(sys.argv[4] if len(sys.argv) == 5 else None)
        print(compare(sys.argv[2], sys.argv[3], b_external_segments=ext))
    else:
        print(__doc__.strip(), file=sys.stderr)
        raise SystemExit(2)
