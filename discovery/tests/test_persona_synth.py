

def _pit_with_edges():
    """Two roles with deliberately different edge-row counts per host: `chatty` hosts each
    produce 4 edge rows, `quiet` hosts 1."""
    ents, edges, prof = [], [], []
    for i in range(4):
        k = f"address:10.0.0.{i}"
        ents.append({"entity_key": k, "role": "chatty", "locality": "local",
                     "listen_ports": "[]", "dial_ports": '["80/tcp","443/tcp","53/udp","123/udp"]'})
        prof.append({"entity_key": k, "bytes_per_window_q": [100, 200, 300],
                     "flows_per_window_q": [1, 2, 3]})
        for j, p in enumerate(("80", "443", "53", "123")):
            edges.append({"src_key": k, "dst_key": f"address:10.0.1.{j}", "proto": p})
    for i in range(4):
        k = f"address:10.0.2.{i}"
        ents.append({"entity_key": k, "role": "quiet", "locality": "local",
                     "listen_ports": "[]", "dial_ports": '["80/tcp"]'})
        prof.append({"entity_key": k, "bytes_per_window_q": [10, 20, 30],
                     "flows_per_window_q": [1, 1, 1]})
        edges.append({"src_key": k, "dst_key": "address:10.0.1.0", "proto": "80"})
    ents.append({"entity_key": "address:10.0.1.0", "role": "server", "locality": "local",
                 "listen_ports": '["80/tcp","443/tcp","53/udp","123/udp"]', "dial_ports": "[]"})
    return {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}


def test_emit_budget_is_measured_per_role_not_a_constant():
    """`fan_out_degree` counts EDGE ROWS per source, and `ir_edge` is keyed
    `(src, dst, proto)` -- so the quantity a persona controls is how many protocols it
    emits. A fixed cap cannot fit: on CorpAPCN the same constant over-served
    `generic-endpoint` (8 rows/host vs a source median of 3) and under-served
    `corp-workstation` (5 vs 16) at once."""
    from aximprint.synth.personas import emit_budgets

    budgets = emit_budgets(_pit_with_edges())
    assert budgets["chatty"] == 4
    assert budgets["quiet"] == 1


def test_emit_budget_bounds_the_synthesized_emit_count():
    from aximprint.synth.personas import synthesize_personas

    personas = synthesize_personas(_pit_with_edges())
    # The LOCAL-BEARING variant: every host of both roles talks to a local peer in this
    # fixture (bearer share 1.0), so that is the persona a host of them actually gets.
    assert len(personas["cast-quiet-local"]["persona"]["emits"]) == 1
    assert len(personas["cast-chatty-local"]["persona"]["emits"]) <= 4


def test_a_role_with_dial_evidence_never_budgets_to_zero():
    """A group that dials must still talk to someone -- a persona that emits nothing is
    indistinguishable downstream from a broken node."""
    from aximprint.synth.personas import emit_budgets

    pit = _pit_with_edges()
    pit["ir_edge"] = []            # no edges at all -> medians would be 0
    pit["ir_edge"] = [{"src_key": "address:10.0.2.0", "dst_key": "address:10.0.1.0",
                       "proto": "80"}]
    assert all(v >= 1 for v in emit_budgets(pit).values())


def test_emit_selection_ranks_by_conversation_weight_not_dial_frequency():
    """A budget says HOW MANY emits; selection decides WHICH. Ranking by dial-port
    frequency counts how many hosts touched a port and is blind to how much conversation it
    carries, so a small budget can be spent entirely on rare ports while the dominant edge is
    dropped -- measured on CorpAPCN as `generic-endpoint->web-tier` 298 -> 127."""
    from aximprint.synth.personas import synthesize_personas

    # `rare` (161) is dialled by MORE hosts (all six) but carries ONE edge row each; `heavy`
    # (80) is dialled by four of them and carries three rows each, to three web hosts. The
    # `flows` column is set the other way round on purpose -- SNMP is many tiny polls per
    # row -- so a ranking on flows would seat `mon` first (iteration 19: rows, never flows).
    ents, edges, prof = [], [], []
    for i in range(6):
        k = f"address:10.0.0.{i}"
        dials = '["161/udp","80/tcp"]' if i < 4 else '["161/udp"]'
        ents.append({"entity_key": k, "role": "client", "locality": "local",
                     "listen_ports": "[]", "dial_ports": dials})
        prof.append({"entity_key": k, "bytes_per_window_q": [100, 200, 300],
                     "flows_per_window_q": [1, 2, 3]})
        edges.append({"src_key": k, "dst_key": "address:10.0.1.0", "proto": "snmp",
                      "flows": 900})
        if i < 4:
            edges += [{"src_key": k, "dst_key": f"address:10.0.1.{j}", "proto": "http",
                       "flows": 1} for j in (1, 2, 3)]
    ents.append({"entity_key": "address:10.0.1.0", "role": "mon", "locality": "local",
                 "listen_ports": '["161/udp"]', "dial_ports": "[]"})
    ents += [{"entity_key": f"address:10.0.1.{j}", "role": "web", "locality": "local",
              "listen_ports": '["80/tcp"]', "dial_ports": "[]"} for j in (1, 2, 3)]
    pit = {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}

    emits = synthesize_personas(pit)["cast-client-local"]["persona"]["emits"]
    assert emits, "client dials, so it must emit"
    # The heavy conversation wins the first slot although the rare port is dialled by more
    # hosts and its rows carry far more flows.
    assert emits[0]["peer_role"] == "web"


def test_weights_ignore_edges_the_twin_can_never_place():
    """Weighting must count ROLE-TO-ROLE edges only, and an EXTERNAL destination must never be
    counted as a LOCAL target.

    That second half is the pin `3dedba8` put here and it still holds exactly: 202 of
    CorpAPCN's 204 `web-tier` entities are external, and treating those 295 edges as local
    web-tier targets drove the twin to manufacture local web-tier traffic to match an external
    total (measured: source 3, twin 225). What has changed is where they go INSTEAD -- since
    GAP-C17 they land in the `internet-sink` bucket, which is a destination the twin really
    has, rather than being dropped on the floor.

    A ROLE-LESS destination is still counted nowhere at all, local or not: nothing in the twin
    can stand in for a peer nothing could name."""
    from collections import Counter

    from aximprint.synth.personas import SINK_ROLE, proto_weights, role_edge_targets

    ents = [{"entity_key": "a", "role": "client", "locality": "local"},
            {"entity_key": "b", "role": "web", "locality": "local"},
            {"entity_key": "x", "role": None, "locality": "local"},
            # ROLED but EXTERNAL -- the case that made this a real defect: 202 of
            # CorpAPCN's 204 `web-tier` entities are external, so filtering on "has a
            # role" alone still counted 295 unplaceable edges as targets.
            {"entity_key": "e", "role": "web", "locality": "external"}]
    edges = [{"src_key": "a", "dst_key": "b", "proto": "http", "flows": 5},
             {"src_key": "a", "dst_key": "x", "proto": "ssl", "flows": 5000},
             {"src_key": "a", "dst_key": "e", "proto": "quic", "flows": 9000}]
    pit = {"ir_entity": ents, "ir_edge": edges}

    # The claim that must not regress: `web` gets ONE local target edge, not two.
    assert role_edge_targets(pit)["client"] == Counter({"web": 1, SINK_ROLE: 1})

    w = proto_weights(pit)
    assert w["client"]["http"] == 1     # one ROW, whatever its `flows` (iteration 19)
    assert "ssl" not in w["client"]     # role-less destination: placeable by nothing
    # Counted now, and only because the sink can carry it. `ssl` is 243 of the real source's
    # 711 outbound edge rows -- its largest outbound protocol -- so a local-only weighting
    # ranked the twin's heaviest protocol below its cheap background ports. One row here too:
    # 9000 flows on it change nothing, because the fingerprint counts rows.
    assert w["client"]["quic"] == 1


def test_listener_ports_are_exactly_what_something_dials():
    """A role must answer on the ports personas actually dial at it -- no more (a listener
    nothing talks to is a fabricated service) and no less (an unanswered port draws ICMP
    port-unreachable and manufactures a reverse edge)."""
    from aximprint.synth.responder import listener_ports

    personas = {"p": {"persona": {"emits": [
        {"peer_role": "web", "ports": [80, 443], "transport": "tcp"},
        {"peer_role": "resolver", "ports": [53], "transport": "udp"},
        {"peer_role": "broadcast", "ports": [137], "transport": "udp"},
    ]}}}
    got = listener_ports(personas)
    assert got["web"] == {(80, "tcp"), (443, "tcp")}
    assert got["resolver"] == {(53, "udp")}
    # Reserved pseudo-peers resolve to wire addresses, not to a host that could listen.
    assert "broadcast" not in got


# The four `responder_script()` tests that stood here MOVED to `tests/test_synth_services.py`
# on 2026-09-05, with the function itself (`synth/responder.py`'s own note). They assert
# properties of the ECHO -- detached start, fail-loud, bind-before-thread, EADDRINUSE-is-a-skip
# -- and the echo is now one section of a `serve-<node>.sh` rather than a script of its own, so
# asserting them against a renderer nothing calls would have tested a copy.


def test_budget_is_spent_in_the_source_s_destination_proportions():
    """A role edge is `sum(hosts x emits at that role)`, so the most NUMEROUS band decides
    the histogram. Ranking each persona by its own conversation weight fixes personas in
    isolation and still misses the aggregate: measured on CorpAPCN, the light band (47 of 102
    endpoints) bought dns+ntp and contributed zero web-tier edges, against a source whose
    heaviest edge is generic-endpoint->web-tier (298 vs the twin's 134)."""
    from aximprint.synth.personas import allocate_budget
    from collections import Counter

    targets = Counter({"web-tier": 298, "dns-resolver": 69, "corp-workstation": 2})
    # The dominant peer must survive even the smallest budget -- dropping it is the exact
    # failure being fixed.
    assert allocate_budget(1, targets) == Counter({"web-tier": 1})
    three = allocate_budget(3, targets)
    assert sum(three.values()) == 3           # largest-remainder: sums to the budget exactly
    assert three["web-tier"] >= three["dns-resolver"] >= three["corp-workstation"]


def test_allocation_is_empty_when_there_is_nothing_to_proportion_against():
    """A role the source shows no role-to-role edges for has no proportions to honour; the
    caller must fall back to plain weight ranking rather than emit nothing."""
    from aximprint.synth.personas import allocate_budget
    from collections import Counter

    assert allocate_budget(3, Counter()) == Counter()
    assert allocate_budget(0, Counter({"web-tier": 5})) == Counter()


# -- the internet sink (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C17) ----------------------


def _pit_with_external_peers():
    """One local client whose conversation is mostly OUTBOUND, mirroring the real shape.

    Two local roled peers and two external ones, plus an external SOURCE — the case that
    made `emit_budgets` and `activity_bands` wrong, since neither filtered on locality.
    """
    ents = [{"entity_key": "c", "role": "client", "locality": "local", "listen_ports": "[]",
             "dial_ports": '["80/tcp","53/udp","5228/tcp"]'},
            {"entity_key": "web", "role": "web", "locality": "local",
             "listen_ports": '["80/tcp"]', "dial_ports": "[]"},
            {"entity_key": "res", "role": "resolver", "locality": "local",
             "listen_ports": '["53/udp"]', "dial_ports": "[]"},
            # Roled but EXTERNAL: real peers on the internet, unplaceable as themselves.
            {"entity_key": "x1", "role": "web", "locality": "external",
             "listen_ports": '["80/tcp"]', "dial_ports": "[]"},
            {"entity_key": "x2", "role": "web", "locality": "external",
             "listen_ports": '["80/tcp"]', "dial_ports": "[]"}]
    edges = ([{"src_key": "c", "dst_key": "web", "proto": "http", "flows": 1},
              {"src_key": "c", "dst_key": "res", "proto": "dns", "flows": 1}]
             + [{"src_key": "c", "dst_key": "x1", "proto": "ssl", "flows": 9} for _ in range(4)]
             + [{"src_key": "c", "dst_key": "x2", "proto": "ssl", "flows": 9} for _ in range(4)])
    prof = [{"entity_key": e["entity_key"], "bytes_per_window_q": [100, 200, 300],
             "flows_per_window_q": [1, 2, 3]} for e in ents]
    return {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}


def test_an_external_destination_becomes_the_sink_bucket_not_a_local_target():
    """The measured requirement: 85% of the source's edges leave the network, and a twin with
    nowhere external to talk does not go quiet -- it manufactures LOCAL edges to stay busy
    (`generic-endpoint->web-tier` local, source 3, twin 225). The bucket is what
    `allocate_budget` proportions the external share against."""
    from aximprint.synth.personas import SINK_ROLE, role_edge_targets

    targets = role_edge_targets(_pit_with_external_peers())["client"]
    assert targets[SINK_ROLE] == 8        # both external peers, all 8 rows
    assert targets["web"] == 1            # the ONE local web edge, not 9
    assert targets["resolver"] == 1


def test_a_sink_emit_fans_out_by_role_and_never_by_address():
    """The safety property, asserted rather than trusted. axRange's `Emit` has no address or
    CIDR field at all (`axrange/personas/schema.py`), so a persona can only ever name a ROLE
    and peer resolution can only ever yield in-twin hosts. The fan-out therefore has to come
    from role MEMBERSHIP, which is what the binary `internet-sink-<n>` family is for."""
    from aximprint.synth.personas import SINK_BUCKETS, synthesize_personas

    emits = synthesize_personas(_pit_with_external_peers())["cast-client"]["persona"]["emits"]
    sink = [e for e in emits if e["peer_role"].startswith("internet-sink-")]
    assert sink, "a client whose conversation is 80% outbound must reach the sink"
    for e in sink:
        assert e["peer_scope"] == "all", "fan-out IS the peer_scope on a sink emit"
        assert int(e["peer_role"].rsplit("-", 1)[1]) in SINK_BUCKETS
        # No key anywhere in an emit may carry a literal address.
        assert not any(isinstance(v, str) and v.count(".") == 3 for v in e.values())
    assert all("peer_scope" not in e for e in emits if e not in sink), \
        "a local emit is left at the default scope, so a diff shows fan-out where it is"


def test_fanout_decomposes_into_a_binary_subset_of_sink_roles():
    """`peer_scope` yields ONE peer or ALL of them, never a distribution, so a single 16-host
    sink role would give every host in the twin the identical fan-out of 16. Binary buckets
    reproduce 1..15 from the same machinery. The source's own distribution is median 2 /
    mean 4.485 / max 53, so 7 of its 132 externally-talking hosts are knowingly clamped."""
    from aximprint.synth.personas import SINK_MAX_FANOUT, sink_fanout_buckets

    assert sink_fanout_buckets(1) == [1]
    assert sink_fanout_buckets(3) == [2, 1]          # descending: widest on the heaviest port
    assert sink_fanout_buckets(15) == [8, 4, 2, 1]
    assert sum(sink_fanout_buckets(53)) == SINK_MAX_FANOUT == 15   # truncated, not split
    assert sink_fanout_buckets(0) == [] and sink_fanout_buckets(-1) == []


def test_a_port_nothing_local_answers_reaches_the_sink_instead_of_being_dropped():
    """19 distinct dialled port-specs died at this site on the real source -- `138/udp`,
    `5228/tcp`, `8883/tcp`, `873/tcp`, `2049/tcp` and more -- every one of them a conversation
    that left the network and that the twin then had nowhere to put."""
    from aximprint.synth.personas import synthesize_personas

    personas = synthesize_personas(_pit_with_external_peers())
    emits = personas["cast-client-local"]["persona"]["emits"]
    # 5228/tcp: nothing in this twin listens on it, so before the sink it was a silent drop.
    landed = [e for e in emits if 5228 in e["ports"]]
    assert landed and landed[0]["peer_role"].startswith("internet-sink-")
    # The EXTERNAL-ONLY variant of the same group spends the identical budget, all of it
    # outbound -- the population split must not lose rows, only move where they land.
    ext = personas["cast-client"]["persona"]["emits"]
    assert ext and all(e["peer_role"].startswith("internet-sink-") for e in ext)


def test_a_host_never_talks_to_itself_even_to_reach_the_sink():
    """The self-talk guard predates the sink and must survive it: loopback never crosses a tap
    point, so a role that is the only answerer for a port it dials still emits at the sink
    rather than at itself."""
    from aximprint.synth.personas import synthesize_personas

    pit = _pit_with_external_peers()
    # `web` both dials and answers 80 -- the only answerer for it is `web` itself.
    pit["ir_entity"][1]["dial_ports"] = '["80/tcp"]'
    pit["ir_edge"] += [{"src_key": "web", "dst_key": "x1", "proto": "ssl", "flows": 9}]
    emits = synthesize_personas(pit)["cast-web"]["persona"]["emits"]
    assert emits and all(e["peer_role"] != "web" for e in emits)
    assert any(e["peer_role"].startswith("internet-sink-") for e in emits)


def test_budgets_are_sized_over_local_sources_only():
    """The residual of `3dedba8`, which its own message says belonged in four places and made
    it in two. Measured on the real source: `generic-endpoint` had 103 local sources and 15
    external, and `file-server` had **0 local and 1 external** -- a whole budget group
    synthesized from a host that exists only on the internet."""
    from aximprint.synth.personas import emit_budgets

    pit = _pit_with_external_peers()
    # An external source with a huge edge-row count. It must move NO budget.
    pit["ir_entity"].append({"entity_key": "ext-src", "role": "client", "locality": "external",
                             "listen_ports": "[]", "dial_ports": "[]"})
    pit["ir_edge"] += [{"src_key": "ext-src", "dst_key": "web", "proto": "http", "flows": 1}
                       for _ in range(50)]
    # A role whose ONLY source is external disappears entirely rather than budgeting from it.
    pit["ir_entity"].append({"entity_key": "only-ext", "role": "ghost", "locality": "external",
                             "listen_ports": "[]", "dial_ports": "[]"})
    pit["ir_edge"] += [{"src_key": "only-ext", "dst_key": "web", "proto": "http", "flows": 1}]

    budgets = emit_budgets(pit)
    assert budgets["client"] == 10, "10 local edge rows, and the external source's 50 ignored"
    assert "ghost" not in budgets


def test_bands_are_measured_over_the_twins_own_population():
    """A band boundary is a role's own MEDIAN volume, so a roster that is 65% internet peers
    (233 of 356 roled entities on the real source) decides every band from hosts the twin
    does not contain. Measured effect there: `''/light/heavy` 54/48/21 -> 48/45/30."""
    from aximprint.synth.personas import activity_bands

    ents = [{"entity_key": f"loc{i}", "role": "r", "locality": "local"} for i in range(3)]
    ents += [{"entity_key": f"ext{i}", "role": "r", "locality": "external"} for i in range(9)]
    # The local hosts are quiet; the external ones are 1000x busier. With the externals in the
    # roster the median is theirs and every local host reads `light`.
    prof = ([{"entity_key": f"loc{i}", "bytes_per_window_q": [100, 100, 100]} for i in range(3)]
            + [{"entity_key": f"ext{i}", "bytes_per_window_q": [100_000] * 3} for i in range(9)])
    bands = activity_bands({"ir_entity": ents, "ir_traffic_profile": prof})

    assert set(bands) == {"loc0", "loc1", "loc2"}, "an external entity has no band at all"
    assert set(bands.values()) == {""}, "against their OWN median the local hosts are typical"


def test_the_sink_gets_a_listener_because_its_name_is_not_a_reserved_token():
    """Byte volume only materializes against a real listener -- "a refused connect is 2
    packets, not 64 MiB" -- so a sink nothing answers on would reproduce the SHAPE of the
    source's outbound traffic and none of its volume.

    This is also why the role is not spelled `internet` or `egress`: both are axRange
    RESERVED_PEER_ROLES that put nothing on the wire (GAP-048), and both are in
    `responder._NOT_A_HOST`, so a sink under either name would be a node nothing dials and
    nothing answers on."""
    from aximprint.synth.personas import SINK_ROLE, synthesize_personas
    from aximprint.synth.responder import _NOT_A_HOST, listener_ports

    personas = synthesize_personas(_pit_with_external_peers())
    wanted = listener_ports(personas)
    sink_roles = {r for r in wanted if r.startswith("internet-sink-")}
    assert sink_roles, "every sink sub-role a persona dials must answer on those ports"
    assert SINK_ROLE not in _NOT_A_HOST and not any(r in _NOT_A_HOST for r in sink_roles)
    # Exactly the ports something dials at it -- no more (a fabricated service), no less (an
    # ICMP port-unreachable that manufactures a reverse edge).
    dialled = {(p, e.get("transport") or "tcp")
               for doc in personas.values() for e in doc["persona"]["emits"]
               for p in e["ports"] if e["peer_role"] in sink_roles}
    assert set().union(*wanted.values()) >= dialled


def _pit_with_a_busy_external_twin_of_every_local_host(external: bool):
    """One role, two quiet LOCAL members, and optionally three ENORMOUSLY busier EXTERNAL
    members of the same role — the exact shape the real source has (`web-tier`: 2 local, 202
    external; `generic-endpoint`: 103 local, 15 external)."""
    ents = [{"entity_key": "res", "role": "resolver", "locality": "local",
             "listen_ports": '["53/udp"]', "dial_ports": "[]"}]
    prof = [{"entity_key": "res", "bytes_per_window_q": [0, 0, 0],
             "flows_per_window_q": [1, 1, 1]}]
    for i in range(2):
        ents.append({"entity_key": f"loc{i}", "role": "client", "locality": "local",
                     "listen_ports": "[]", "dial_ports": '["53/udp"]'})
        # ~1 KB per flow -> the `low` tier.
        prof.append({"entity_key": f"loc{i}", "bytes_per_window_q": [1000, 1000, 1000],
                     "flows_per_window_q": [1, 1, 1]})
    if external:
        for i in range(3):
            ents.append({"entity_key": f"ext{i}", "role": "client", "locality": "external",
                         "listen_ports": "[]", "dial_ports": '["53/udp"]'})
            # ~500 MB per flow -> the `bulk` tier, 500,000x the local hosts.
            prof.append({"entity_key": f"ext{i}",
                         "bytes_per_window_q": [500_000_000] * 3,
                         "flows_per_window_q": [1, 1, 1]})
    edges = [{"src_key": f"loc{i}", "dst_key": "res", "proto": "dns", "flows": 1}
             for i in range(2)]
    return {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}


def test_a_persona_is_derived_only_from_the_hosts_the_twin_will_deploy():
    """The highest-consequence line in the sink change, pinned.

    `synthesize_personas`' member census decides `volume` and `cadence`, and it must count
    LOCAL members only. It is not merely impure otherwise — it inverts, because
    `activity_bands` IS locality-filtered, so every external entity loses its band and
    `.get(key, "")` files all of them under their role's BASE group. Measured on the real
    source when the filter was absent: base `generic-endpoint` mean per-window bytes went
    11,622 -> 85,726,890 (`low` -> `bulk`), and the cast offered 43.75 MB/s against a source
    that runs at 1.330 MB/s — a 32.9x capture-saturating twin, with the whole suite green.

    Asserted as an EQUIVALENCE (the persona is byte-identical with and without the external
    members) rather than only as a tier, so the pin covers the dial census and the cadence too,
    not just the one field that happened to move."""
    from aximprint.synth.personas import synthesize_personas

    without = synthesize_personas(_pit_with_a_busy_external_twin_of_every_local_host(False))
    with_ext = synthesize_personas(_pit_with_a_busy_external_twin_of_every_local_host(True))
    assert with_ext == without, (
        "external members of a role must not change its persona at all — they are internet "
        "peers the twin never deploys")
    # And the specific field whose blow-up floods the capture path, named outright.
    assert with_ext["cast-client-local"]["persona"]["emits"][0]["volume"] == "low", (
        "a role whose LOCAL hosts move ~1 KB/flow is `low`; reading its external namesakes' "
        "500 MB/flow would make it `bulk` — 64 MiB a fire, on every twin host of that role")


def test_the_synthesized_twin_does_not_outrun_the_network_it_replicates():
    """The aggregate form of the same claim, and the one an operator actually feels.

    A twin's offered byte rate is `Σ (tier_bytes x fan_out / interval x hosts)`. It has no
    business exceeding the rate the source itself ran at, and the capture path has a measured
    ~2.6 Gbit/s ceiling: `_cadence_for`'s own docstring is the standing warning that a twin
    which floods its own sensor is not a faithful twin. This computes the rate the way the
    driver will spend it — fan-out included, which is what our sink emits introduced."""
    import re

    from aximprint.synth.personas import _TIER_BYTES, synthesize_personas

    pit = _pit_with_a_busy_external_twin_of_every_local_host(True)
    offered = 0.0
    for doc in synthesize_personas(pit).values():
        for e in doc["persona"]["emits"]:
            fan = (int(e["peer_role"].rsplit("-", 1)[1])
                   if e["peer_role"].startswith("internet-sink-") else 1)
            interval = int(re.match(r"cyclic:(\d+)s", e["cadence"]).group(1))
            offered += _TIER_BYTES[e["volume"]] * fan / interval
    # The two local hosts move 1000 B/window each, i.e. ~33 B/s of source truth for the role.
    # A 10x headroom is generous and still catches the 500,000x blow-up by five orders.
    source_rate = 2 * 1000 / 60
    assert offered <= source_rate * 10, (
        f"twin offers {offered:.0f} B/s against a source running at {source_rate:.0f} B/s")


# -- population allocation (`docs/JOURNAL_corpapcn_fidelity.md`, iteration 16) --------------


def _pit_with_a_minority_that_talks_locally():
    """Ten endpoints shaped the way the source's own population is: THREE talk to a local
    peer, TWO do nothing local but link-local discovery, and FIVE talk only outbound.

    That is the distribution `allocate_budget` cannot express. A budget of 4 at a 3/38 local
    share allocates `round(0.32) == 0` -- or, at the real source's 21%, `round(0.84) == 1` --
    to EVERY host, because the smallest non-zero per-host allocation is one. Measured on
    CorpAPCN: `generic-endpoint`'s median local degree is 0 and only 30 of its 103 hosts have
    any local edge, against a twin in which 58 of them did.
    """
    ents = [{"entity_key": "res", "role": "resolver", "locality": "local",
             "segment": "10.0.0.0/24", "listen_ports": '["53/udp"]', "dial_ports": "[]"},
            # Roled + EXTERNAL: an internet peer, which is what the sink stands in for.
            {"entity_key": "ext", "role": "web", "locality": "external", "segment": None,
             "listen_ports": '["443/tcp"]', "dial_ports": "[]"},
            # LOCAL but unplaceable -- a multicast group. `compiler.py` keeps these out of the
            # node roster; `_GROUP_PORTS` is how a persona still talks to one.
            {"entity_key": "mcast", "role": None, "locality": "local", "segment": None,
             "listen_ports": "[]", "dial_ports": "[]"}]
    edges, prof = [], [{"entity_key": k, "bytes_per_window_q": [100, 200, 300],
                        "flows_per_window_q": [1, 2, 3]} for k in ("res", "ext", "mcast")]
    for i in range(10):
        k = f"ep{i}"
        ents.append({"entity_key": k, "role": "endpoint", "locality": "local",
                     "segment": "10.0.0.0/24", "listen_ports": "[]",
                     "dial_ports": '["53/udp","443/tcp","5353/udp"]'})
        prof.append({"entity_key": k, "bytes_per_window_q": [100, 200, 300],
                     "flows_per_window_q": [1, 2, 3]})
        if i < 3:                      # bearers: a real local peer
            edges.append({"src_key": k, "dst_key": "res", "proto": "dns", "flows": 1})
        elif i < 5:                    # chatterers: link-local discovery and nothing else
            edges.append({"src_key": k, "dst_key": "mcast", "proto": "mdns", "flows": 1})
        else:                          # outbound only
            edges.append({"src_key": k, "dst_key": "ext", "proto": "ssl", "flows": 1})
        edges += [{"src_key": k, "dst_key": "ext", "proto": "ssl", "flows": 1}
                  for _ in range(3)]
    return {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}


_NODES = [{"name": "endpoint-10-0-0", "role": "endpoint", "count": 10},
          {"name": "resolver-10-0-0", "role": "resolver"}]
_ROLE_ASSIGNMENTS = [{"role": "endpoint", "persona": "corp-workstation", "seed": 7},
                     {"role": "resolver", "persona": None, "seed": 9}]


def test_local_population_measures_the_source_s_own_split():
    """The measurement the fix rests on, read straight off the store. `share` is what
    fraction of a group talks to a local peer at all; `degree` is how much local conversation
    ONE of those carries -- 2.77 rows on the real source's `generic-endpoint`, against the
    0.81-per-host a proportional budget would give every one of its 103 hosts."""
    from aximprint.synth.personas import local_population

    pop = local_population(_pit_with_a_minority_that_talks_locally())["endpoint"]
    assert pop["hosts"] == 10
    assert pop["bearers"] == 3 and pop["share"] == 0.3
    assert pop["degree"] == 1
    # Disjoint from the bearers: a host that talks to a local peer is described by the
    # local-bearing persona whether or not it also chatters.
    assert pop["chatter"] == 2 and pop["chatter_share"] == 0.2


def test_a_group_is_split_between_variants_instead_of_every_host_getting_a_local_edge():
    """The headline. The twin must reproduce "most endpoints have NO local conversation",
    which one persona per group structurally cannot say."""
    from collections import Counter

    from aximprint.synth.personas import (local_population, plan_assignments,
                                          synthesize_personas)

    pit = _pit_with_a_minority_that_talks_locally()
    personas = synthesize_personas(pit)
    assigned = plan_assignments(_NODES, personas, local_population(pit), _ROLE_ASSIGNMENTS)

    got = Counter(a["persona"] for a in assigned if a.get("node"))
    assert got["cast-endpoint-local"] == 3, "the source's 3-in-10 bearer share, reproduced"
    assert got["cast-endpoint-chatter"] == 2
    assert got["cast-endpoint"] == 5, "half the group talks to nothing local at all"

    def peers(pid):
        return {e["peer_role"] for e in personas[pid]["persona"]["emits"]}

    assert "resolver" in peers("cast-endpoint-local")
    assert "resolver" not in peers("cast-endpoint")
    assert peers("cast-endpoint") <= {"internet-sink-1", "internet-sink-2", "internet-sink-4",
                                      "internet-sink-8"}


def test_the_split_claims_every_host_exactly_once():
    """axRange permits ONE persona instance per host and refuses the whole profile when two
    assignments claim one ("claimed by both 'role:X' and 'node:Y'"), so a split role has to go
    FULLY node-targeted. Asserted over the expanded instance set, which is what `tfc deploy`
    actually resolves."""
    from collections import Counter

    from aximprint.synth.personas import (local_population, node_instances, plan_assignments,
                                          synthesize_personas)

    pit = _pit_with_a_minority_that_talks_locally()
    assigned = plan_assignments(_NODES, synthesize_personas(pit), local_population(pit),
                                _ROLE_ASSIGNMENTS)
    every = {i for n in _NODES for i in node_instances(n)}
    claimed: Counter = Counter()
    for a in assigned:
        if a.get("node"):
            claimed[a["node"]] += 1
        elif a.get("role"):
            for n in _NODES:
                if n["role"] == a["role"]:
                    claimed.update(node_instances(n))
    assert not [h for h, n in claimed.items() if n > 1], "a host claimed twice"
    assert set(claimed) <= every, "an assignment names a node the twin does not have"
    # And the role-level entry of a SPLIT role is gone, which is what makes that true.
    assert "endpoint" not in {a.get("role") for a in assigned}


def test_a_role_that_does_not_split_keeps_its_role_level_assignment_and_its_seed():
    """`seed` and `intensity` live only on a role-level assignment (`compiler.py` D5/D6), so
    node-targeting a role that did not need it would silently drop a compiler choice."""
    from aximprint.synth.personas import (local_population, plan_assignments,
                                          synthesize_personas)

    pit = _pit_with_a_minority_that_talks_locally()
    # Every endpoint a bearer -> one persona for the whole group -> nothing to node-target.
    for e in pit["ir_edge"]:
        if e["dst_key"] in ("mcast", "ext") and e["src_key"].startswith("ep"):
            e["dst_key"] = "res" if e["proto"] != "ssl" else e["dst_key"]
    for i in range(10):
        pit["ir_edge"].append({"src_key": f"ep{i}", "dst_key": "res", "proto": "dns",
                               "flows": 1})
    assigned = plan_assignments(_NODES, synthesize_personas(pit), local_population(pit),
                                _ROLE_ASSIGNMENTS)
    role_entries = {a["role"]: a for a in assigned if a.get("role")}
    assert role_entries["endpoint"]["seed"] == 7
    assert role_entries["endpoint"]["persona"] == "cast-endpoint-local"
    assert not [a for a in assigned if a.get("node")]


def test_bearer_selection_is_deterministic_and_spread_across_the_group():
    """Determinism by construction, not by care (`LES-002`): an arithmetic stride over a
    caller-ordered list, no set iteration and no hashing. Spread rather than taken from the
    front, because instances are enumerated segment by segment and `[:k]` would put every
    locally-talking host of a role on the first one or two segments."""
    from aximprint.synth.personas import bearer_count, select_bearers

    ids = [f"h{i:02d}" for i in range(10)]
    assert select_bearers(ids, 3) == select_bearers(ids, 3)
    assert select_bearers(ids, 3) == ["h00", "h03", "h06"]
    assert select_bearers(ids, 0) == [] and select_bearers([], 3) == []
    assert select_bearers(ids, 99) == ids, "never more bearers than hosts"
    # Rounded, not floored: a 3-host group at a 29% share is one bearer, not none.
    assert bearer_count(0.29, 3) == 1
    assert bearer_count(0.0, 10) == 0 and bearer_count(1.0, 10) == 10
    assert bearer_count(0.5, 0) == 0


def test_unspendable_local_quota_is_spent_at_the_sink_rather_than_lost():
    """A quota slot at a role no port of this group resolves to used to be simply LOST -- the
    loop ran out of candidates and the persona emitted fewer rows than its budget (measured:
    `corp-workstation`, 16 budgeted / 8 emitted). Losing rows quietly moves
    `external_dst_edge_share`, which is the axis the sink exists to hold."""
    from aximprint.synth.personas import emit_budgets, synthesize_personas

    pit = _pit_with_a_minority_that_talks_locally()
    # The source shows endpoints talking to `web`, but no endpoint dials a port `web` answers
    # on that is not already claimed -- so that share of the local quota cannot be placed.
    pit["ir_edge"] += [{"src_key": "ep0", "dst_key": "webl", "proto": "https", "flows": 1}
                       for _ in range(20)]
    pit["ir_entity"].append({"entity_key": "webl", "role": "weblocal", "locality": "local",
                             "segment": "10.0.0.0/24", "listen_ports": '["8443/tcp"]',
                             "dial_ports": "[]"})
    pit["ir_traffic_profile"].append({"entity_key": "webl",
                                      "bytes_per_window_q": [1, 1, 1],
                                      "flows_per_window_q": [1, 1, 1]})
    personas = synthesize_personas(pit)
    budget = emit_budgets(pit)["endpoint"]
    for pid in ("cast-endpoint", "cast-endpoint-local"):
        rows = sum(int(e["peer_role"].rsplit("-", 1)[1])
                   if e["peer_role"].startswith("internet-sink-") else 1
                   for e in personas[pid]["persona"]["emits"])
        assert rows >= budget - 1, f"{pid} spent {rows} of a {budget}-row budget"


def test_a_role_the_source_never_saw_leave_the_network_gets_no_sink_emit():
    """Spilling an unplaceable local quota to the sink is right when the source DID leave the
    network. For a role whose every observed edge stayed inside -- `hypervisor` and
    `log-shipper` on the real source -- it would invent outbound traffic it never carried."""
    from aximprint.synth.personas import synthesize_personas

    pit = {"ir_entity": [
        {"entity_key": "a", "role": "shipper", "locality": "local", "segment": "10.0.0.0/24",
         "listen_ports": "[]", "dial_ports": '["9997/tcp"]'},
        {"entity_key": "b", "role": "collector", "locality": "local", "segment": "10.0.0.0/24",
         "listen_ports": '["9997/tcp"]', "dial_ports": "[]"}],
        "ir_edge": [{"src_key": "a", "dst_key": "b", "proto": "tcp", "flows": 1}],
        "ir_traffic_profile": [{"entity_key": k, "bytes_per_window_q": [10, 10, 10],
                                "flows_per_window_q": [1, 1, 1]} for k in ("a", "b")]}
    emits = synthesize_personas(pit)["cast-shipper-local"]["persona"]["emits"]
    assert [e["peer_role"] for e in emits] == ["collector"]
    assert not any(p.startswith("cast-shipper") and p != "cast-shipper-local"
                   for p in synthesize_personas(pit))


def test_a_chatter_host_carries_exactly_one_link_local_emit():
    """19 of the 35 hosts in the source's single-local-edge bucket talk only to a multicast or
    broadcast address. The twin gave them none: a group port carries no proto weight (its
    destination is a wire address, so `proto_weights` can never score it), so it always ranks
    below every weighted protocol and is dropped the moment the budget's rows are spent."""
    from aximprint.synth.personas import synthesize_personas

    personas = synthesize_personas(_pit_with_a_minority_that_talks_locally())
    emits = personas["cast-endpoint-chatter"]["persona"]["emits"]
    group = [e for e in emits if e["peer_role"] in ("multicast", "broadcast")]
    assert len(group) == 1 and group[0]["ports"] == [5353]
    assert emits[0] is group[0], "promoted: it cannot win a slot on rank"
    # Everything else it does is outbound -- it has no local PEER, only a wire address.
    assert all(e["peer_role"].startswith("internet-sink-") for e in emits[1:])
    # And the plain variant, which has neither, is a different document.
    assert emits != personas["cast-endpoint"]["persona"]["emits"]


def test_a_group_that_cannot_express_a_split_ships_one_persona_not_two_identical_ones():
    """`network-device`'s only dialled port IS its chatter, so its chatter variant composes to
    exactly the base persona. Two identical documents and a partition that means nothing is
    worse than one document: it would make a diff of two casts show a split that is not there."""
    from aximprint.synth.personas import synthesize_personas

    from aximprint.synth.personas import local_population

    pit = {"ir_entity": [
        {"entity_key": "n", "role": "netdev", "locality": "local", "segment": "10.0.0.0/24",
         "listen_ports": "[]", "dial_ports": '["1985/udp"]'},
        {"entity_key": "mcast", "role": None, "locality": "local", "segment": None,
         "listen_ports": "[]", "dial_ports": "[]"}],
        "ir_edge": [{"src_key": "n", "dst_key": "mcast", "proto": "hsrp", "flows": 1}],
        "ir_traffic_profile": [
            {"entity_key": k, "bytes_per_window_q": [1, 1, 1],
             "flows_per_window_q": [1, 1, 1]} for k in ("n", "mcast")]}
    # The population DOES say every one of its hosts is a chatterer, so a chatter variant is
    # a live candidate here -- it is dropped for composing to the base, not for never having
    # been considered.
    assert local_population(pit)["netdev"]["chatter_share"] == 1.0
    assert sorted(synthesize_personas(pit)) == ["cast-netdev"]


# -- the chatter variant is switchable (iteration 17 attribution) ---------------------------


def _driven(nodes, personas, population, assignments):
    """`{instance: persona id}` — the mapping `tfc deploy` actually resolves, with node-group
    assignments expanded the way `scripts/install_cast_as_twin.py` expands them."""
    from aximprint.synth.personas import node_instances, plan_assignments

    role_of = {str(n["name"]): n.get("role") for n in nodes}
    inst_role = {i: n.get("role") for n in nodes for i in node_instances(n)}
    groups = {str(n["name"]): node_instances(n) for n in nodes}
    out, node_roles = {}, set()
    planned = plan_assignments(nodes, personas, population, assignments)
    for a in planned:
        if a.get("node"):
            if role_of.get(str(a["node"])):
                node_roles.add(role_of[str(a["node"])])
            for i in groups.get(str(a["node"]), [str(a["node"])]):
                out[i] = a["persona"]
    for a in planned:
        if a.get("role") and a["role"] not in node_roles and a.get("persona"):
            for i, r in inst_role.items():
                if r == a["role"]:
                    out[i] = a["persona"]
    return out


def test_the_chatter_variant_is_switchable_and_switches_nothing_else():
    """Iteration 16 shipped the population allocator and the chatter variant together, and the
    cluster run moved five axes in both directions: `fan_out_degree_local` 0.572 -> 0.162 while
    `role_edges_local` 0.618 -> 0.742 and `external_dst_edge_share` 0.8054 -> 0.6653. Neither
    change is attributable from that, so the chatter half has to be removable ON ITS OWN --
    which is only useful if removing it leaves every other persona byte-identical."""
    from aximprint.synth.personas import synthesize_personas

    pit = _pit_with_a_minority_that_talks_locally()
    on, off = synthesize_personas(pit), synthesize_personas(pit, chatter=False)
    assert "cast-endpoint-chatter" in on, "default ON: the flag's existence changes no cast"
    assert not [p for p in off if p.endswith("-chatter")]
    assert set(on) - set(off) == {"cast-endpoint-chatter"}
    # Every persona that survives is the SAME DOCUMENT, so a fingerprint move between the two
    # runs can only be the chatter hosts.
    assert all(off[p] == on[p] for p in off)


def test_switching_chatter_off_moves_only_the_chatter_hosts():
    """The claim the attribution run rests on: the same hosts are driven, and the ones that
    move go to their OWN group's plain variant -- never to some other group's."""
    from aximprint.synth.personas import local_population, synthesize_personas

    pit = _pit_with_a_minority_that_talks_locally()
    pop = local_population(pit)
    on = _driven(_NODES, synthesize_personas(pit), pop, _ROLE_ASSIGNMENTS)
    off = _driven(_NODES, synthesize_personas(pit, chatter=False), pop, _ROLE_ASSIGNMENTS)

    assert set(on) == set(off), "the same hosts must still be driven"
    moved = {h: (on[h], off[h]) for h in on if on[h] != off[h]}
    assert moved, "the fixture must actually have chatter hosts, or this proves nothing"
    assert all(a == b + "-chatter" for a, b in moved.values()), \
        "a moved host must land on its own group's plain variant"
    assert len(moved) == 2, "the fixture's 2-in-10 chatter share, and no other host"


def test_a_variant_lookup_never_falls_back_to_the_plain_persona():
    """A regression pin on the lookup, not on the flag. The band fallback (`cast-<role>` for a
    band that never got its own persona) must apply to the PLAIN lookup only: on a variant
    lookup it answers "is there a chatter variant here?" with the group's ordinary persona, so
    a banded group would hand `chatter_share x N` of its hosts a persona that is not a variant
    of anything and call that a split.

    Latent while chatter is always on -- every group that could ask had one -- and live the
    moment `--no-chatter` exists, which is why it is fixed with the flag rather than after it.
    """
    from aximprint.synth.personas import plan_assignments

    doc = {"persona": {"id": "x", "emits": [], "drive": "scripted"}}
    nodes = [{"name": "endpoint-heavy-10-0-0", "role": "endpoint", "count": 4}]
    # The band HAS its own persona, the base role has one too, and NEITHER has a chatter
    # variant -- exactly the state `--no-chatter` puts every group in.
    personas = {"cast-endpoint": doc, "cast-endpoint-heavy": doc}
    population = {"endpoint-heavy": {"share": 0.0, "chatter_share": 0.5, "degree": 0}}
    assigned = plan_assignments(nodes, personas, population, [])
    assert {a["persona"] for a in assigned} == {"cast-endpoint-heavy"}, \
        "the base role's plain persona is not a chatter variant of the band"


# -- the emit vocabulary vs the observation vocabulary (iteration 18) -----------------------


def test_every_emittable_proto_has_an_explicit_weighting_decision():
    """THE regression pin for this whole class of defect, and the one that would have caught
    it: `_PORT_PROTO` names protocols the way axRange's persona schema does, `ir_edge.proto`
    the way the sensor does, and the ranking joins them by string equality. Nineteen of the
    twenty-four values disagreed and scored a permanent zero -- silently, because a zero is
    indistinguishable from "this port carries no conversation".

    So absence from the alias table has to be a DECISION rather than an oversight. Adding a
    port to `_PORT_PROTO` without saying which sensor label it joins against now fails here.
    """
    from aximprint.synth.personas import (_IR_PROTO_ALIASES, _PORT_PROTO,
                                          _UNWEIGHTED_PROTOS)

    emittable = set(_PORT_PROTO.values())
    decided = set(_IR_PROTO_ALIASES) | set(_UNWEIGHTED_PROTOS)
    assert emittable == decided, (
        f"no weighting decision recorded for {sorted(emittable - decided)}; "
        f"decision recorded for a proto nothing emits: {sorted(decided - emittable)}")
    assert not (set(_IR_PROTO_ALIASES) & set(_UNWEIGHTED_PROTOS)), "mapped AND unweighted"


def test_tls_ports_score_the_sensor_s_ssl_label_not_their_own_name():
    """The measured headline: `443/tcp` is `https` in the emit vocabulary and `ssl` in the IR,
    so the source's single largest outbound protocol -- 243 of `generic-endpoint`'s 711
    outbound rows -- could not be spent, and `53/udp` won the slot instead. HTTP/3 arrives as
    the composite `quic,ssl`, which must score too."""
    from collections import Counter

    from aximprint.synth.personas import observed_weight

    # `quic` appears BOTH as the composite `quic,ssl` and, when no TLS analyzer matched, on
    # its own -- so the alias has to carry it explicitly; token-matching `ssl` covers only the
    # composite and would silently drop the bare case.
    wt = Counter({"ssl": 243, "quic,ssl": 24, "quic": 7, "dns": 17, "http": 13})
    assert observed_weight(wt, 443) == 274, "443 must see ssl, quic,ssl AND a bare quic"
    assert observed_weight(wt, 53) == 17
    assert observed_weight(wt, 80) == 13
    assert observed_weight(wt, 443) > observed_weight(wt, 53), \
        "the ranking this fixes: ssl outranks dns on the source's own outbound mix"


def test_a_composite_service_label_scores_for_every_analyzer_that_fired():
    """Zeek joins every analyzer that fired on one connection, so `gssapi,smb,krb` is evidence
    for BOTH the SMB port and the Kerberos port. Exact-string matching sees neither."""
    from collections import Counter

    from aximprint.synth.personas import observed_weight

    wt = Counter({"gssapi,smb,krb": 6, "krb_tcp": 4, "ldap_udp": 6, "ldap_tcp": 6})
    assert observed_weight(wt, 445) == 6            # smb
    assert observed_weight(wt, 88) == 10            # krb + krb_tcp
    assert observed_weight(wt, 389) == 12           # both LDAP transports
    assert observed_weight(wt, 3268) == 12          # the global-catalog port is LDAP too


def test_the_deliberate_zeros_stay_zero():
    """Three of them, each for its own reason, and none of them an oversight:

      * `tcp`/`udp` are the sensor saying it could NOT name the service -- 446 + 85 rows of
        unidentified traffic on this source. Aliasing `9997/tcp` to that would hand one
        arbitrary port the entire unidentified mass.
      * A GROUP port's destination is a wire address, which carries no role, and
        `proto_weights` counts role-to-role edges only -- so its own conversations are
        structurally absent from the weight table and any weight it received would be borrowed.
        Measured: the source's 14 multicast rows ARE labelled `dns`, so `mdns -> dns` would
        credit `5353/udp` with unicast port-53's traffic.
      * `beats`/`netflow` have no Zeek analyzer at all, so there is no label to join against.
    """
    from collections import Counter

    from aximprint.synth.personas import observed_weight

    wt = Counter({"tcp": 446, "udp": 85, "dns": 87, "ssl": 243})
    for port in (9997, 5353, 5355, 137, 1900, 1985, 5044, 2055, 5432):
        assert observed_weight(wt, port) == 0, f"port {port} must stay unweighted"
    # A port `_PORT_PROTO` does not name at all scores zero and cannot raise.
    assert observed_weight(wt, 8883) == 0
    assert observed_weight(Counter(), 443) == 0


def test_the_ranking_spends_the_heaviest_observed_protocol_first():
    """End to end, through `synthesize_personas`: the port carrying the most observed
    conversation must win the first slot. Before the alias table `443/tcp` scored zero and
    sorted last no matter how much `ssl` the group carried."""
    from aximprint.synth.personas import synthesize_personas

    ents = [{"entity_key": "res", "role": "resolver", "locality": "local",
             "segment": "10.0.0.0/24", "listen_ports": '["53/udp"]', "dial_ports": "[]"}]
    ents += [{"entity_key": f"web{j}", "role": "web", "locality": "local",
              "segment": "10.0.0.0/24", "listen_ports": '["443/tcp"]', "dial_ports": "[]"}
             for j in range(3)]
    edges, prof = [], []
    for i in range(4):
        k = f"c{i}"
        ents.append({"entity_key": k, "role": "client", "locality": "local",
                     "segment": "10.0.0.0/24", "listen_ports": "[]",
                     "dial_ports": '["53/udp","443/tcp"]'})
        prof.append({"entity_key": k, "bytes_per_window_q": [100, 200, 300],
                     "flows_per_window_q": [1, 2, 3]})
        # `ssl` carries far more conversation than `dns` -- three edge ROWS to one, which is
        # what the fingerprint counts -- and the IR spells it `ssl`. The `flows` column says
        # the opposite (one lookup-heavy DNS row against three single-flow TLS rows), so a
        # ranking on flows would hand the first slot to 53 (iteration 19).
        edges += [{"src_key": k, "dst_key": f"web{j}", "proto": "ssl", "flows": 1}
                  for j in range(3)]
        edges += [{"src_key": k, "dst_key": "res", "proto": "dns", "flows": 50}]
    prof += [{"entity_key": k, "bytes_per_window_q": [1, 1, 1],
              "flows_per_window_q": [1, 1, 1]} for k in ("res", "web0", "web1", "web2")]
    pit = {"ir_entity": ents, "ir_edge": edges, "ir_traffic_profile": prof}
    emits = synthesize_personas(pit)["cast-client-local"]["persona"]["emits"]
    assert emits[0]["ports"] == [443], \
        f"the heaviest observed protocol must win the first slot, got {emits[0]}"


# -- iteration 19: the ranking column measures what the fingerprint counts ---------------
#
# `docs/WORK_ORDER_proto_weight_rows.md` D1a.


def test_proto_weights_count_edge_rows_not_flows():
    """`role_edges` / `role_edges_local` / `fan_out_degree` all increment by ONE per edge row
    (`scripts/shape_fingerprint.py`), whatever the row's flow count, and the ranking that
    decides which ports a persona spends its budget on has to sort on that same quantity. It
    sorted on `ir_edge.flows` until iteration 19, and on the real source the two columns
    disagree everywhere the weight decides anything -- `53/udp` (many one-packet lookups per
    row) won all six groups on flows, `443/tcp` (fewer, fatter conversations) won five on
    rows. This fixture is that shape: three TLS rows of two flows each against one DNS row of
    fifty."""
    from collections import Counter

    from aximprint.synth.personas import observed_weight, proto_weights

    ents = [{"entity_key": "c", "role": "client", "locality": "local"},
            {"entity_key": "res", "role": "resolver", "locality": "local"}]
    ents += [{"entity_key": f"web{j}", "role": "web", "locality": "local"} for j in range(3)]
    edges = [{"src_key": "c", "dst_key": f"web{j}", "proto": "ssl", "flows": 2}
             for j in range(3)]
    edges += [{"src_key": "c", "dst_key": "res", "proto": "dns", "flows": 50}]
    # The fixture's own discriminating property, asserted so it cannot silently stop
    # discriminating: the two columns must order the two protocols OPPOSITELY.
    flows = Counter()
    for ed in edges:
        flows[ed["proto"]] += ed["flows"]
    assert flows["dns"] > flows["ssl"], "fixture: dns must win on flows"

    w = proto_weights({"ir_entity": ents, "ir_edge": edges})["client"]
    assert w == Counter({"ssl": 3, "dns": 1}), "one per edge row, never the row's flow count"
    # The cross-field invariant (`LES-133`): a group's weights sum to its roled-to-roled row
    # count, which no flows-based accumulation satisfies unless every row carries one flow.
    assert sum(w.values()) == len(edges)
    assert observed_weight(w, 443) > observed_weight(w, 53), \
        "443 must outrank 53 on rows, the quantity the fingerprint counts"


# -- peer resolution: the port-389 tie (`docs/WORK_ORDER_reference_ingest.md` §7.2) --------


def _tied_listen_pit(order=None):
    """A PIT with a GENUINE 2-2 tie on 389 between `domain-controller` and `dns-resolver`,
    reproducing what `docs/WORK_ORDER_reference_ingest.md` §2 measured on the CorpAPCN PIT
    (`dns-resolver: 2`, `domain-controller: 2`, `file-server: 1`).

    Roles are pinned by NAME against the real catalog rather than invented, because the tiebreak
    under test is the catalog's own declaration order — a fixture using made-up role names would
    exercise only the name fallback (`LES-136`: pin by identity, not position).
    """
    ents = []
    for i, (role, ports) in enumerate([
            ("domain-controller", '["88/tcp","389/tcp"]'),
            ("domain-controller", '["88/tcp","389/tcp"]'),
            ("dns-resolver", '["53/udp","389/tcp"]'),
            ("dns-resolver", '["53/udp","389/tcp"]'),
            ("file-server", '["445/tcp","389/tcp"]')]):
        ents.append({"entity_key": f"address:10.0.9.{i}", "role": role, "locality": "local",
                     "listen_ports": ports, "dial_ports": "[]"})
    if order is not None:
        ents = [ents[i] for i in order]
    ents.append({"entity_key": "address:10.0.9.90", "role": "client", "locality": "local",
                 "listen_ports": "[]", "dial_ports": '["389/tcp"]'})
    return {"ir_entity": ents, "ir_edge": [], "ir_traffic_profile": []}


def test_a_tied_serves_vote_resolves_to_the_most_specific_catalog_role():
    """§2 measured a real 2-2 tie on 389 and `Counter.most_common` broke it on INSERTION order,
    so `dns-resolver` won and a cast twin's domain controller was never dialled for LDAP.
    Declaration order in a most-specific-first catalog is the tiebreak: `domain-controller`
    demands 88+389 jointly, `dns-resolver` demands 53/udp alone."""
    from aximprint.synth.personas import _resolve_serves
    from collections import Counter

    votes = Counter({"dns-resolver": 2, "domain-controller": 2, "file-server": 1})
    assert _resolve_serves(389, votes) == "domain-controller"


def test_a_clear_majority_still_beats_catalog_order():
    """The tiebreak must only break TIES. `file-server` is declared after `domain-controller`,
    so a rank-first resolver would hand it 445 to the DC — which is the bug's mirror image."""
    from aximprint.synth.personas import _resolve_serves
    from collections import Counter

    assert _resolve_serves(445, Counter({"file-server": 2, "domain-controller": 1,
                                         "dns-resolver": 1})) == "file-server"


def test_peer_resolution_is_identical_under_ten_shuffled_row_orders():
    """`LES-009`: no output byte may depend on arrival order. `ir_entity` comes back in whatever
    order DuckDB hands over, and before this fix that order decided which role every LDAP emit
    in the twin aimed at. Ten SEEDED shuffles, so the test is itself deterministic."""
    import random

    from aximprint.synth.personas import synthesize_personas

    def _ldap_peers(pit):
        personas = synthesize_personas(pit)
        return sorted({(pid, e["peer_role"])
                       for pid, doc in personas.items()
                       for e in doc["persona"]["emits"] if 389 in e.get("ports", [])})

    rng = random.Random(20260904)
    baseline = _ldap_peers(_tied_listen_pit())
    assert baseline, "the fixture must actually produce an LDAP emit to compare"
    assert all(role == "domain-controller" for _pid, role in baseline)
    for _ in range(10):
        order = list(range(5))
        rng.shuffle(order)
        assert _ldap_peers(_tied_listen_pit(order)) == baseline, f"row order {order} changed it"
