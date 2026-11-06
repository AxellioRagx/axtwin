"""PIT -> axRange `twin_config` + `tfc` + `tap` (`WORK_ORDER_network_ir.md` step 4).

Synthetic events only (`LES-003`).

The compiler is where four earlier findings have to actually hold, so most of this file is about
them rather than about YAML shape: nodes come from the PLACED set, provenance goes in a sidecar
because axRange's vocabulary is closed, `compiler` (renamed from `defaulted` 2026-09-04) enters
here and nowhere upstream, and `tap.sensors` is left unfilled rather than guessed.
"""
import itertools

import pytest

from aximprint.catalog import DEFAULT_NODE, UNROLED_ROLE
from aximprint.events import FlowEvent
from aximprint.ir.materialize import materialize
from aximprint.ir.store import IRStore
from aximprint.learners import extract_assertions
from aximprint.provenance import build_stamp
from aximprint.store.belief_store import BeliefStore
from aximprint.synth.compiler import (
    COMPILER_CLASS,
    DEPLOY_MIN_OCCUPANCY,
    GATEWAY_NAME,
    PROVENANCE_SCHEMA,
    SINK_CIDR,
    SINK_SEGMENT_NAME,
    Fact,
    compile_twin,
    declared_transformation,
    occupancy_rung,
    resolve_precedence,
    to_yaml,
)
from aximprint.windowing import WindowManager

WINDOW = 3600
T0 = 1_754_000_000.0
W1 = T0 - (T0 % WINDOW)


def _flow(ts, src, dst, dport=445, local_dst=True, proto="tcp"):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto=proto, service="", duration=1.0, bytes_out=100, bytes_in=200,
                     pkts_out=2, pkts_in=2, state="SF", state_class="established",
                     local_src=True, local_dst=local_dst)


def _pit(tmp_path, events, name="s.duckdb"):
    st = BeliefStore(str(tmp_path / name))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    st.write_entity_roster("/cap/one", wm.entity_roster())
    st.write_run({"source": "/cap/one", "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12}, build_stamp(), len(wins), W1, W1 + 1)
    pit = materialize(st)
    pit.pop("_stats")
    return pit


_TWO_SEGMENTS = [
    _flow(W1 + 1, "10.0.1.10", "10.0.1.20", 88),
    _flow(W1 + 2, "10.0.1.10", "10.0.1.20", 389),      # 10.0.1.20 -> domain-controller
    _flow(W1 + 3, "10.0.1.11", "10.0.2.30"),           # 10.0.2.30 -> file-server (default 445)
    _flow(W1 + 4, "10.0.1.12", "10.0.1.20"),
    _flow(W1 + 5, "10.0.1.10", "203.0.113.9", local_dst=False),   # external
    # Three file-servers in the FIRST segment — what a `count:` collapse is made of. They are
    # role-bearing on purpose: since D5 (`WORK_ORDER_visibility_and_vantage.md`) the twin holds
    # only role-bearing nodes, so a collapse fixture built from pure clients (.10/.11/.12, which
    # are still here and still correctly excluded) would test a group that no longer exists.
    _flow(W1 + 6, "10.0.1.10", "10.0.1.31"),
    _flow(W1 + 7, "10.0.1.10", "10.0.1.32"),
    _flow(W1 + 8, "10.0.1.10", "10.0.1.33"),
    # A cross-segment conversation between two hosts that BOTH survive the filter. The conduit
    # that proves segment adjacency is otherwise dropped along with its role-less endpoint —
    # which is correct behavior and would make the conduit test vacuous.
    _flow(W1 + 9, "10.0.1.20", "10.0.2.30"),
]


def _compiled(tmp_path, events=None, **kw):
    return compile_twin(_pit(tmp_path, events or _TWO_SEGMENTS), **kw)


def _tc(out):
    return out["twin_config"]["twin_config"]


# -- nodes come from the PLACED set ----------------------------------------------

def test_a_local_host_on_no_provisionable_network_is_not_a_node(tmp_path):
    """Phase 1 measured 117 of these on the real baseline (IPv6 link-local, multicast). Each is
    already in the twin under its routable address, so emitting them would duplicate hosts."""
    out = _compiled(tmp_path, _TWO_SEGMENTS + [_flow(W1 + 6, "fe80::1", "10.0.1.20")])
    names = {n["name"] for n in _tc(out)["nodes"]}
    assert not any("fe80" in n for n in names)
    assert any("no provisionable network" in c for c in out["caveats"])


def test_an_ipv6_segment_is_not_a_twin_segment(tmp_path):
    """A ULA address passes `is_provisionable`, so the windowing mints an IPv6 /64 segment and
    places hosts on it -- and axRange refuses a non-IPv4 segment CIDR outright (CV-012 on the
    canvas, the schema at deploy). The 2026-10-28 corp-apcn cast carried one and could not be
    saved. The cast names it in the caveats instead of emitting it."""
    v6 = [_flow(W1 + 6, "fd78:f376:bfe7:4d3d::1", "fd78:f376:bfe7:4d3d::2"),
          _flow(W1 + 7, "fd78:f376:bfe7:4d3d::3", "fd78:f376:bfe7:4d3d::2", 389)]
    out = _compiled(tmp_path, _TWO_SEGMENTS + v6)
    tc = _tc(out)
    assert not any(":" in sg["cidr"] for sg in tc["segments"])
    assert not any("fd78" in n["name"] for n in tc["nodes"])
    assert not any(":" in leg.get("segment", "") for n in tc["nodes"] for leg in n.get("nets", []))
    assert any("IPv6 segment(s)" in c and "fd78:f376:bfe7:4d3d::/64" in c for c in out["caveats"])
    # The IPv4 segments are untouched by it.
    assert {sg["cidr"] for sg in tc["segments"]} >= {"10.0.1.0/24", "10.0.2.0/24"}


def test_external_peers_are_not_nodes(tmp_path):
    """A twin cannot deploy the internet."""
    out = _compiled(tmp_path)
    assert not any("203-0-113" in n["name"] for n in _tc(out)["nodes"])


# -- collapse -------------------------------------------------------------------

def test_hosts_of_one_role_and_segment_collapse_into_one_entry_with_a_count(tmp_path):
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    # `-core` is the OCCUPANCY RUNG (`docs/WORK_ORDER_deployment_scope.md` §9): the group key is
    # `(role, band, segment, rung)`, so members of one group share a presence class and the
    # deploy gate can act on the whole group. Every host here is in the PIT's single window.
    assert nodes["file-server-10-0-1-core"]["count"] == 3   # .31, .32, .33 — one role, one segment
    assert "count" not in nodes["domain-controller-10-0-1-core"], "a single host needs no count"


def test_the_node_name_is_derived_from_the_cidr_not_an_ordinal(tmp_path):
    """An ordinal would renumber every node the moment a segment appeared or disappeared —
    exactly the instability a drift comparison would then report as change."""
    names = {n["name"] for n in _tc(_compiled(tmp_path))["nodes"]}
    # `file-server-10-0-2`, not `host-10-0-2`: the fixture's default port is 445, so that host
    # matches the file-server rule. Both halves of the name are what this asserts — the role and
    # the CIDR-derived suffix.
    assert "file-server-10-0-2-core" in names and "domain-controller-10-0-1-core" in names


def test_max_count_caps_and_says_so(tmp_path):
    """D3 is an open decision, so the default is uncapped — the network really does have that
    many hosts. A cap that bites is disclosed, never silently applied."""
    out = _compiled(tmp_path, max_count=2)
    nodes = {n["name"]: n for n in _tc(out)["nodes"]}
    assert nodes["file-server-10-0-1-core"]["count"] == 2
    assert any("capped to 2" in c and "really has 3" in c for c in out["caveats"])


def test_uncapped_is_the_default(tmp_path):
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    assert nodes["file-server-10-0-1-core"]["count"] == 3


# -- the synthetic gateway --------------------------------------------------------

def test_one_router_joins_every_segment(tmp_path):
    """axRange's `expand.py` errors on a declared conduit with no router joining both its
    segments, and `TwinConfigSegment.gateway` is auto-assigned network+1 from a router leg."""
    tc = _tc(_compiled(tmp_path))
    gw = next(n for n in tc["nodes"] if n["name"] == GATEWAY_NAME)
    assert gw["kind"] == "router"
    assert {leg["segment"] for leg in gw["nets"]} == {s["name"] for s in tc["segments"]}


def test_the_gateway_is_declared_synthetic_in_both_provenance_and_caveats(tmp_path):
    """Phase 1 measured that this vantage has neither of §8's gateway signals — no L2 addresses
    in `conn.log`, no TTL. Presenting `gw` as discovered would be a fabricated host."""
    out = _compiled(tmp_path)
    gw_prov = [v for k, v in out["provenance"]["fields"].items() if k.endswith("]")
               and v.get("evidence_class") == COMPILER_CLASS and "synthetic" in str(v)]
    assert gw_prov, "the gw node must be marked compiler-class in the sidecar"
    assert any("synthetic" in c for c in out["caveats"])


# -- provenance: the sidecar, and where `defaulted` enters ------------------------

def test_provenance_is_a_sidecar_never_inline(tmp_path):
    """axRange's `twin_config` is `extra="forbid"`, so the v0 stub's `{value, provenance}`
    wrappers are REJECTED by their schema. This is a real constraint, not a style choice."""
    out = _compiled(tmp_path)
    for node in _tc(out)["nodes"]:
        for value in node.values():
            assert not (isinstance(value, dict) and "provenance" in value)
    assert out["provenance"]["fields"], "the sidecar must carry the fields instead"


def test_a_role_reaches_the_twin_as_imputed(tmp_path):
    """The whole chain's point: a role is inferred from listening ports, never observed, and
    that has to survive into the twin's own per-field provenance."""
    out = _compiled(tmp_path)
    nodes = _tc(out)["nodes"]
    roles = [v for k, v in out["provenance"]["fields"].items()
             if k.endswith(".role")
             and nodes[int(k.split("[")[1].split("]")[0])].get("role") != UNROLED_ROLE]
    assert roles and all(r["evidence_class"] == "imputed" for r in roles)
    # And the other half, which is the point of keeping them apart: `UNROLED_ROLE` is
    # `COMPILER_CLASS`, never `imputed`. Nothing inferred it — it is what a COMPILER supplies when
    # no evidence rule matched, the same class as `image` (D4).
    unroled = [v for k, v in out["provenance"]["fields"].items()
               if k.endswith(".role")
               and nodes[int(k.split("[")[1].split("]")[0])].get("role") == UNROLED_ROLE]
    assert unroled and all(r["evidence_class"] == COMPILER_CLASS for r in unroled)
    # No `confidence` on either branch's compiler-class row: there is no inference to be
    # confident about, and a fabricated number is what axRange's Console would shade on
    # (`LES-006`, D7).
    assert all("confidence" not in r for r in unroled)
    assert all("confidence" in r for r in roles), "an imputed role carries its census score"


def test_an_image_is_always_compiler_class_however_well_the_role_matched(tmp_path):
    """`Q26` point 3's own prediction taken as the answer: the provenance marking matters more
    than the coverage."""
    out = _compiled(tmp_path)
    images = [v for k, v in out["provenance"]["fields"].items() if k.endswith(".image")]
    assert images and all(i["evidence_class"] == COMPILER_CLASS for i in images)
    # `source` is unchanged by the rename: `docs/WORK_ORDER_fidelity_scorecard.md` §8.4 names
    # this row by that field, so it is a cross-repo seam, not an internal detail.
    assert all(i["source"].startswith("catalog/roles.yaml") for i in images)


def _observed_cidr_prov(out):
    """Segment cidr provenance rows for the segments that were actually SEEN.

    The internet sink's segment (GAP-C17) is emitted alongside them and is deliberately
    `compiler`-class with `coverage_source: synthesized` — it is the one segment in the twin no
    capture observed, so every "what did we see" assertion has to exclude it or it would pin
    the opposite of what it means.
    """
    return {k: v for k, v in out["provenance"]["fields"].items()
            if k.startswith("twin_config.segments") and k.endswith(".cidr")
            and v.get("coverage_source") != "synthesized"}


def test_a_segment_reaches_the_twin_as_observed_with_its_coverage(tmp_path):
    out = _compiled(tmp_path)
    cidrs = list(_observed_cidr_prov(out).values())
    assert cidrs and all(c["evidence_class"] == "observed" for c in cidrs)
    assert all("windows_present" in c and "coverage_source" in c for c in cidrs)


def test_the_compiler_class_enters_here_and_is_absent_from_the_ir(tmp_path):
    """`ir/schema.py::EVIDENCE_CLASSES` does not carry `compiler` — it is what a COMPILER does
    when a catalog supplies what discovery never observed, and admitting it to the IR would let
    a twin's `image:` re-enter drift comparison looking like evidence about the network."""
    from aximprint.ir import EVIDENCE_CLASSES
    out = _compiled(tmp_path)
    classes = {v["evidence_class"] for v in out["provenance"]["fields"].values()}
    assert COMPILER_CLASS in classes
    assert COMPILER_CLASS not in EVIDENCE_CLASSES


def test_no_sidecar_row_still_says_defaulted(tmp_path):
    """The rename's acceptance, stated as the work order states it: **no `"defaulted"` remains in
    an emitted sidecar** (`docs/WORK_ORDER_reference_ingest.md` S0). Asserted over the whole
    serialized document rather than over `evidence_class` alone, because the old name also
    appeared inside prose `reason=` strings, where a class-only check would miss it."""
    import json as _json
    out = _compiled(tmp_path, relocate="10.200.0.0/16")
    assert "defaulted" not in _json.dumps(out["provenance"])


def test_every_sidecar_row_names_the_rule_or_stem_behind_it(tmp_path):
    """D7's `signal`: short, present on every row, never a sentence. Present on EVERY row rather
    than where it happened to be convenient — `prov()` takes it positionally so a new call site
    cannot omit one (`LES-114`), and this is the check that the signature is doing that job."""
    out = _compiled(tmp_path, relocate="10.200.0.0/16")
    rows = out["provenance"]["fields"]
    assert rows
    for path, row in rows.items():
        assert row.get("signal"), f"{path} carries no signal"
        # "Never a sentence" made checkable: a signal names a rule or a stem, so it fits on one
        # line of the Console's provenance table and carries no sentence break. Prose belongs in
        # `reason`/`note`, which several of these rows also carry.
        #
        # 120, not 80. The longest on a real cast is 68, but `_role_signal` is CONTENT-driven —
        # a catalog row with three ports on a long role name is an ordinary edit, and a bound
        # that a content change trips would fail with "signal is prose", which is a misleading
        # diagnosis of a legitimate edit (`LES-086`: a bound set at a currently-true value is a
        # landmine). 120 still refuses an actual sentence.
        assert len(row["signal"]) <= 120, f"{path}: signal is prose, not a rule name"
        assert ". " not in row["signal"], f"{path}: signal reads as a sentence"


def test_confidence_is_present_only_where_a_producer_supplied_one(tmp_path):
    """D7/`LES-006`: absent, never null and never invented. A `compiler`-class row has no
    producer to supply one, so it must carry no `confidence` key at all — axRange's Console
    shades on this field, and a fabricated 1.0 would shade a guess as certainty."""
    out = _compiled(tmp_path)
    rows = out["provenance"]["fields"]
    assert not any(row.get("confidence") is None and "confidence" in row
                   for row in rows.values()), "confidence must be absent, never null"
    compiler_rows = [r for r in rows.values() if r["evidence_class"] == COMPILER_CLASS]
    assert compiler_rows and all("confidence" not in r for r in compiler_rows)
    scored = [r for r in rows.values() if "confidence" in r]
    assert scored, "the role census supplies confidences and they must reach the sidecar"
    assert all(0.0 <= r["confidence"] <= 1.0 for r in scored)


def test_the_sidecar_declares_its_own_schema_distinct_from_the_ir_passthrough(tmp_path):
    """D6/D-b. Without it the `defaulted` -> `compiler` rename is indistinguishable, at a reader
    outside this producer, from a bug in that reader's own parsing. The IR's version is beside it
    and is a DIFFERENT artifact's version — conflating the two is exactly what
    `docs/WORK_ORDER_reference_ingest.md` §7.4 found in the topology export."""
    from aximprint.ir import SCHEMA_VERSION

    out = _compiled(tmp_path)
    assert out["provenance"]["schema"] == PROVENANCE_SCHEMA == "axr-cast-provenance/1"
    assert out["provenance"]["schema_version"] == SCHEMA_VERSION
    assert out["provenance"]["schema"] != out["provenance"]["schema_version"]


# -- conduits ---------------------------------------------------------------------

def test_a_conduit_is_emitted_for_each_segment_pair_that_carried_traffic(tmp_path):
    tc = _tc(_compiled(tmp_path))
    names = {s["name"] for s in tc["segments"]}
    assert tc["conduits"] == [{"from": "seg-10-0-1-0", "to": "seg-10-0-2-0"}]
    assert all(c["from"] in names and c["to"] in names for c in tc["conduits"])


# -- tfc ---------------------------------------------------------------------------

def test_a_role_with_a_persona_gets_an_assignment(tmp_path):
    """`seed` is now always present too (D5, `docs/WORK_ORDER_traffic_profiles.md`) — checked by
    the dedicated seed/schedule/intensity tests below, not this one, which only pins role and
    persona so it does not become a second, competing statement of D5's own shape."""
    out = _compiled(tmp_path)
    assignments = out["traffic_profile"]["traffic-profile"]["assignments"]
    dc = next(a for a in assignments if a["role"] == "domain-controller")
    assert dc["persona"] == "domain-controller"


def test_a_role_with_no_persona_reaches_the_twin_but_gets_no_assignment(tmp_path):
    """`dns-resolver` is the measured case — axRange ships no such persona, and its own
    `corp-workstation` persona says so. A real state, not a gap."""
    # udp, not tcp: the `dns-resolver` rule requires `53/udp`, and a rule that silently never
    # fires is exactly what `catalog/roles.py::_port` refuses to let a typo create.
    out = _compiled(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 7, "10.0.1.10", "10.0.1.40", 53, proto="udp")])
    tc = _tc(out)
    assert any(n.get("role") == "dns-resolver" for n in tc["nodes"])
    assigned = {a["role"] for a in out["traffic_profile"]["traffic-profile"]["assignments"]}
    assert "dns-resolver" not in assigned
    assert any("no axRange persona" in c for c in out["caveats"])


def test_roleless_nodes_are_counted_in_the_caveats(tmp_path):
    """Most hosts are pure clients, so this is the common case and the report must say so."""
    assert any("match no evidence rule" in c and UNROLED_ROLE in c
               for c in _compiled(tmp_path)["caveats"])


# -- D1/D2/D4/D5/D6/D7: the cast describes, the deploy selects ---------------------------
#
# `docs/WORK_ORDER_deployment_scope.md`, ruled 2026-09-04. These REVERSE D5/D5a and D11 of
# `WORK_ORDER_visibility_and_vantage.md` (role-bearing only, `--min-occupancy`), which this file
# used to pin as filters. Each is as much a record of that reversal as a check on it, which is
# why the superseded reasoning is quoted rather than deleted.

def test_a_roleless_host_IS_a_node_now(tmp_path):
    """D1/D4, reversing D5. A role-less host used to be dropped — 130 of 141 placed hosts on the
    CorpAPCN capture — on the argument that it deploys as an inert VM generating no traffic.
    True, and beside the point: dropping it made "is every observed host above the bar placed?"
    unanswerable, because the cast had already removed the evidence. It is emitted under
    `UNROLED_ROLE` and the DEPLOY decides whether to stand it up.

    `.11`/`.12` are the fixture's role-less pair: they dial only 445 and the default port, which
    name no client role. (`.10` dials 88 to the DC, the corp-workstation signature, so it has
    carried a real role since GAP-L5.)"""
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    unroled = [n for n in nodes.values() if n.get("role") == UNROLED_ROLE]
    assert len(unroled) == 1 and unroled[0]["count"] == 2, nodes
    assert unroled[0]["net"] == "seg-10-0-1-0"
    # An image and NO persona: present because it was observed, generating nothing it was never
    # observed generating. Never `generic-endpoint`, which is an INFERRED role with a persona.
    assert unroled[0]["image"] == DEFAULT_NODE["image"]
    assigned = {a.get("role") for a in
                _compiled(tmp_path)["traffic_profile"]["traffic-profile"]["assignments"]}
    assert UNROLED_ROLE not in assigned


def test_the_unroled_role_is_never_the_inferred_generic_endpoint(tmp_path):
    """D4's whole argument, as a check. `generic-endpoint` is a real catalog rule with a
    traffic-generating persona held by most of the CorpAPCN population; a host that matched
    NOTHING did not match that either, and emitting both under one name would manufacture
    conversations the source never had in the artifact being measured on the wire."""
    from aximprint.catalog import ROLE_CATALOG

    assert UNROLED_ROLE not in {r.role for r in ROLE_CATALOG}
    assert "generic-endpoint" in {r.role for r in ROLE_CATALOG}
    assert next(r for r in ROLE_CATALOG if r.role == "generic-endpoint").persona


def test_the_placed_population_is_reconstructible_from_the_sidecar_alone(tmp_path):
    """D6. The identity survives the reversal and gets STRICTER: nothing is dropped, so it is an
    equality over `observed` rather than a sum across exclusion buckets. `excluded_by_occupancy`
    is gone — nothing at compile time excludes anything any more."""
    pop = _compiled(tmp_path)["provenance"]["population"]
    assert pop["placed_hosts"] == sum(g["observed"] for g in pop["node_groups"])
    assert pop["emitted_hosts"] == sum(g["emitted"] for g in pop["node_groups"])
    assert "excluded_by_occupancy" not in pop
    # `role_less` survives as ANNOTATION accounting: how much of the EMITTED population carries
    # no inferred role, not how much was thrown away.
    assert pop["role_less"]["hosts"] == 2          # .11, .12
    assert pop["role_less"]["by_segment"] == {"10.0.1.0/24": 2}
    assert UNROLED_ROLE in pop["role_less"]["policy"]
    assert pop["deploy_gate"]["value"] == DEPLOY_MIN_OCCUPANCY


def test_a_role_with_no_persona_still_reaches_the_twin(tmp_path):
    """D5a. `dns-resolver` has a role and no persona, yet the workstations need it — the reason
    the retired filter was role-BEARING rather than role-and-persona. Still true, and now the
    same shape `UNROLED_ROLE` takes for a different reason (D4)."""
    out = _compiled(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 10, "10.0.1.10", "10.0.1.40", 53, proto="udp")])
    assert any(n.get("role") == "dns-resolver" for n in _tc(out)["nodes"])
    assert any("dns-resolver" in c and UNROLED_ROLE in c for c in out["caveats"]), \
        "the personaless caveat must tell the two REASONS apart, not merge the populations"


def test_min_occupancy_is_gone_from_the_compiler_signature(tmp_path):
    """D1: REMOVED, not kept as a no-op. Two places to set one policy is the drift trap this
    change closes, and a flag that silently does nothing is worse than one that is gone — a
    caller who passes it gets an error naming it, not a twin that ignored the bar it was given."""
    with pytest.raises(TypeError, match="min_occupancy"):
        _compiled(tmp_path, min_occupancy=0.5)


# -- the observation block, and the ladder that keys the groups it describes -------------
#
# `docs/WORK_ORDER_deployment_scope.md` D2/D5/§4/§9 and its axRange consumer's §3 (the seam).
# The block is the whole reason a compile-time filter could be retired at all: it is what lets
# the DEPLOY answer the question the cast used to answer for it.


def _multi_window_events(n_windows: int = 5):
    """One client and four file-servers with deliberately different presence.

    `.31` all 5 windows (1.0), `.32` four (0.8), `.34` three (0.6), `.33` two (0.4) — one host
    per rung boundary plus a second `core` host, so both halves of the ladder can be checked:
    that it SPLITS a straddling population, and that a group of two carries the weaker member.
    """
    ev = []
    for w in range(n_windows):
        ts = W1 + w * WINDOW + 1
        ev.append(_flow(ts, "10.0.1.10", "10.0.1.31"))              # .31: 5 of 5 -> core
        if w < 4:
            ev.append(_flow(ts + 1, "10.0.1.10", "10.0.1.32"))      # .32: 4 of 5 -> core
        if w < 3:
            ev.append(_flow(ts + 2, "10.0.1.10", "10.0.1.34"))      # .34: 3 of 5 -> regular
        if w < 2:
            ev.append(_flow(ts + 3, "10.0.1.10", "10.0.1.33"))      # .33: 2 of 5 -> transient
    return ev


def _real_nodes(out):
    """Every node that describes an OBSERVED population — the sink and the gateway excluded."""
    return [n for n in _tc(out)["nodes"]
            if n["name"] != GATEWAY_NAME
            and not str(n.get("role", "")).startswith("internet-sink-")]


def test_every_emitted_node_and_segment_carries_an_observation_block(tmp_path):
    """Acceptance: EVERY emitted non-synthetic node and segment carries one. A block on some of
    them would make the gate's own answer depend on which entity it happened to be looking at."""
    out = _compiled(tmp_path, _multi_window_events())
    assert _real_nodes(out), "fixture must emit real nodes or this is vacuous"
    for n in _real_nodes(out):
        assert set(n["observation"]) <= {"windows_seen", "windows_total", "occupancy",
                                         "role_confidence"}, n
        assert {"windows_seen", "windows_total", "occupancy"} <= set(n["observation"]), n
    for s in _tc(out)["segments"]:
        if s["cidr"] == SINK_CIDR:
            continue
        assert {"windows_seen", "windows_total", "occupancy"} == set(s["observation"]), s


def test_compiler_class_nodes_carry_no_observation_block_at_all(tmp_path):
    """D5, asserted as an ABSENCE rather than as zeros. `occupancy: 0.0` would say the gateway
    and the sink were never SEEN; the truth is that nothing could see them (`LES-006`), and
    axRange reads a missing block as "never gated out", which is what makes the absence safe."""
    out = _compiled(tmp_path, _multi_window_events())
    gw = next(n for n in _tc(out)["nodes"] if n["name"] == GATEWAY_NAME)
    assert "observation" not in gw
    sink = [n for n in _tc(out)["nodes"] if str(n.get("role", "")).startswith("internet-sink-")]
    assert sink and all("observation" not in n for n in sink)
    seg = next(s for s in _tc(out)["segments"] if s["cidr"] == SINK_CIDR)
    assert "observation" not in seg


def test_role_confidence_is_absent_not_null_where_it_does_not_apply(tmp_path):
    """§4's second shape note. A segment has no role and an `UNROLED_ROLE` node has no
    confidence in one; `null` would be a VALUE, and "no evidence" is not "evidence of none"
    (`LES-006`). axRange must read absent as not-applicable, which it already must for the
    compiler-class nodes that carry no block at all."""
    out = _compiled(tmp_path, _multi_window_events())
    for s in _tc(out)["segments"]:
        assert "role_confidence" not in s.get("observation", {}), s
    unroled = [n for n in _real_nodes(out) if n["role"] == UNROLED_ROLE]
    assert unroled, "fixture must emit a role-less host or this is vacuous"
    assert all("role_confidence" not in n["observation"] for n in unroled)
    roled = [n for n in _real_nodes(out) if n["role"] != UNROLED_ROLE]
    assert roled and all("role_confidence" in n["observation"] for n in roled)


def test_role_confidence_is_all_or_nothing_across_a_groups_members(tmp_path):
    """A `min` over only the members that happen to carry a confidence would report a complete
    reading of a partial one — the inverse of `LES-127`, and invisible at the consumer, which
    reads absent as not-applicable and a value as a fact about the whole group."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    fs = [e for e in pit["ir_entity"] if e["role"] == "file-server" and e["segment"]]
    assert len(fs) > 1, "needs a multi-member group or the all/any distinction is invisible"
    with_all = compile_twin(pit)
    node = next(n for n in _tc(with_all)["nodes"] if n["name"] == "file-server-10-0-1-core")
    assert "role_confidence" in node["observation"]
    fs[0]["role_confidence"] = None
    node = next(n for n in _tc(compile_twin(pit))["nodes"]
                if n["name"] == "file-server-10-0-1-core")
    assert "role_confidence" not in node["observation"]
    # ...and the occupancy half is untouched: one missing confidence is not a missing presence.
    assert node["observation"]["occupancy"] == 1.0


def test_the_occupancy_ladder_keys_the_group(tmp_path):
    """§9's ruling, replacing D3's exact `windows_seen` key. One role on one segment, four hosts
    straddling the rungs, splits into three groups — not one (which no gate could act on) and
    not four (which the exact key would produce, renaming every group on every re-imprint)."""
    out = _compiled(tmp_path, _multi_window_events())
    fs = sorted(n["name"] for n in _real_nodes(out) if n["role"] == "file-server")
    # The ACTIVITY BAND is an independent axis and shows up here too (GAP-C12): the two hosts
    # present in fewer windows also moved fewer bytes, so they are `light` as well as less
    # present. That composition is the point — banding and occupancy are orthogonal, and the
    # rung is always the LAST name element whatever the band did.
    assert fs == ["file-server-10-0-1-core", "file-server-light-10-0-1-regular",
                  "file-server-light-10-0-1-transient"], fs
    assert sorted(n.rsplit("-", 1)[1] for n in fs) == ["core", "regular", "transient"]
    by_name = {n["name"]: n for n in _real_nodes(out)}
    assert by_name["file-server-10-0-1-core"]["count"] == 2              # .31 (5/5) + .32 (4/5)
    assert "count" not in by_name["file-server-light-10-0-1-regular"]    # .34 (3/5)
    assert "count" not in by_name["file-server-light-10-0-1-transient"]  # .33 (2/5)


def test_a_groups_block_carries_its_weakest_member(tmp_path):
    """§9: the block on a `count: N` group is its MINIMUM member's `windows_seen` and occupancy.
    The gate is all-or-nothing per group, so anything else would let a group pass a bar one of
    its own members sits below — the exact confusion the group key exists to prevent."""
    out = _compiled(tmp_path, _multi_window_events())
    core = next(n for n in _real_nodes(out) if n["name"] == "file-server-10-0-1-core")
    assert core["count"] == 2, "must be a MULTI-member group or the min is trivially the max"
    assert core["observation"]["windows_seen"] == 4          # .32, not .31's 5
    assert core["observation"]["occupancy"] == 0.8


def test_every_member_of_a_group_shares_the_groups_presence_class(tmp_path):
    """The property the ladder exists to buy, checked over the WHOLE cast rather than on a
    sample: a group's own rung must be the rung of its carried occupancy, so a consumer gating
    on the block is gating on a homogeneous set."""
    from aximprint.synth.compiler import occupancy_rung as _rung

    out = _compiled(tmp_path, _multi_window_events())
    for n in _real_nodes(out):
        assert n["name"].endswith("-" + _rung(n["observation"]["occupancy"])), n["name"]


def test_occupancy_is_the_carried_ratio_to_within_the_consumers_tolerance(tmp_path):
    """The seam's own cross-check (axRange `WORK_ORDER_deploy_gate.md` §3.2): expansion HARD
    ERRORS when `|occupancy - windows_seen/windows_total| > 1e-6`. Emitting the computed ratio
    rather than leaving it to the reader is what keeps the gate and the scorecard from
    disagreeing about rounding — so this is a contract with the consumer, not an arithmetic
    check on ourselves."""
    out = _compiled(tmp_path, _multi_window_events())
    blocks = [n["observation"] for n in _real_nodes(out)]
    blocks += [s["observation"] for s in _tc(out)["segments"] if s["cidr"] != SINK_CIDR]
    assert blocks
    for b in blocks:
        assert b["windows_total"] >= 1 and 0 <= b["windows_seen"] <= b["windows_total"], b
        assert abs(b["occupancy"] - b["windows_seen"] / b["windows_total"]) <= 1e-6, b


def test_the_ladder_boundary_is_the_deploy_bar(tmp_path):
    """0.8 IS a rung (§9), and it is the same 0.8 the gate defaults to — so a group NAMED `core`
    and a group that PASSES the default bar are one set. A float boundary flip between the two
    would silently produce a `core` group the deploy drops."""
    assert occupancy_rung(DEPLOY_MIN_OCCUPANCY) == "core"
    assert occupancy_rung(8 / 10) == "core" and occupancy_rung(4 / 5) == "core"
    assert occupancy_rung(0.7999) == "regular"
    assert occupancy_rung(0.5) == "regular" and occupancy_rung(0.4999) == "transient"
    assert occupancy_rung(0.0) == "transient"


def test_the_cast_emits_the_deploy_threshold_as_a_param(tmp_path):
    """§9. 0.8 is also axRange's built-in default, so this changes no behaviour — that is the
    point. An implicit default is a policy nobody can read off the artifact, and
    `twin-config show` prints `params:`."""
    out = _compiled(tmp_path)
    assert _tc(out)["params"] == {"DEPLOY_MIN_OCCUPANCY": DEPLOY_MIN_OCCUPANCY}
    assert out["provenance"]["fields"][
        "twin_config.params.DEPLOY_MIN_OCCUPANCY"]["evidence_class"] == COMPILER_CLASS
    assert any("DEPLOY_MIN_OCCUPANCY" in c for c in out["caveats"])


def test_max_count_leaves_the_blocks_untouched(tmp_path):
    """Acceptance: re-running with a different `--max-count` must not move a block. `max_count`
    bounds how much of an observed population is DEPLOYED; the block says what was OBSERVED, and
    a cap that rewrote it would make the twin misreport the network to the gate."""
    pit = _pit(tmp_path, _multi_window_events())
    plain = {n["name"]: n.get("observation") for n in _real_nodes(compile_twin(pit))}
    capped = {n["name"]: n.get("observation")
              for n in _real_nodes(compile_twin(pit, max_count=1))}
    assert plain == capped and any(v for v in plain.values())


def test_a_full_presence_pit_reproduces_the_pre_change_groups_modulo_the_suffix(tmp_path):
    """The NO-OP case (`LES-031`: verify by mutation, not by the test passing). A PIT whose
    every host is present in every window puts everything on one rung, so the ladder adds no
    groups at all and the only difference from the pre-change compiler is the name suffix."""
    out = _compiled(tmp_path)                      # the single-window fixture: everyone is 1.0
    names = [n["name"] for n in _real_nodes(out)]
    assert names and all(n.endswith("-core") for n in names), names
    stripped = sorted(n[: -len("-core")] for n in names)
    assert stripped == ["corp-workstation-10-0-1", "domain-controller-10-0-1",
                        "file-server-10-0-1", "file-server-10-0-2",
                        f"{UNROLED_ROLE}-10-0-1"], stripped


def test_a_pit_that_cannot_be_thresholded_gets_no_block_and_says_so(tmp_path):
    """`LES-006`. A PIT claiming zero windows has no denominator, so occupancy is unanswerable —
    and a host is never excluded for a denominator problem. It is emitted with NO block, which
    axRange reads as always-emitted, and the unmeasurable case is disclosed rather than
    silently filtered."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    pit["ir_pit"][0]["windows"] = 0
    out = compile_twin(pit)
    assert _real_nodes(out), "the hosts must still be emitted"
    assert all("observation" not in n for n in _real_nodes(out))
    assert any("cannot be computed" in c for c in out["caveats"])
    gate = out["provenance"]["population"]["deploy_gate"]
    assert gate["unmeasured_nodes"] == sorted(n["name"] for n in _real_nodes(out))


def test_an_impossible_occupancy_is_refused_rather_than_clamped(tmp_path):
    """Fail loud, fail closed (CONVENTIONS §3). `windows_seen > windows` is a CONTRADICTION, not
    an absence, and `ir/materialize.py` already pins `1 <= windows_seen <= windows` on both
    merge axes — so reaching this is a producer bug. Clamping it would hand axRange an occupancy
    its own validator refuses, one layer later and with less context."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    pit["ir_entity"][0]["windows_seen"] = 99
    with pytest.raises(ValueError, match="outside .0, 1."):
        compile_twin(pit)


def test_no_segment_of_a_real_pit_is_pruned_any_more(tmp_path):
    """D7's no-op case, and a finding worth pinning: the prune is now **unreachable from this
    project's own materializer**. `learners/extractors.py` writes an `in_segment` assertion only
    for a `local` entity, so every `ir_segment` row has at least one local entity holding that
    segment — which is exactly the definition of placed. Once nothing is filtered, "a segment
    with no emitted node" cannot arise.

    Asserted rather than assumed, because the guard below is only defensible while this holds:
    if a future producer or merge can mint an orphan segment, that is a new fact and this test
    is where it surfaces."""
    out = _compiled(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 3, "fe80::1", "fe80::2", 5353, proto="udp"),   # nothing provisionable
        _flow(W1 + 4, "10.0.1.10", "203.0.113.9", local_dst=False),   # external, no segment
    ])
    assert out["provenance"]["population"]["segments_pruned"] == []
    assert not any("carry no placed host at all" in c for c in out["caveats"])
    # And the orphan-producing shapes really were present and really produced no segment.
    assert {s["cidr"] for s in _tc(out)["segments"]} == {"10.0.1.0/24", "10.0.2.0/24", SINK_CIDR}


def test_a_segment_with_no_placed_host_is_still_pruned_with_its_conduits(tmp_path):
    """D7. The prune STAYS as written — axRange's expander ERRORS on a conduit whose segments
    carry no router leg, so an orphan is a deploy-time failure rather than an unused row — but
    it can now only fire on GENUINE emptiness, which the test above shows this repo's own
    materializer cannot produce. Pinned against a real PIT with one extra `ir_segment` row
    spliced in, which is the shape a foreign producer or a future merge would deliver.

    A segment below the DEPLOY bar is a different thing entirely: it is still emitted, carrying
    its block, and the deploy decides."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    pit["ir_segment"] = pit["ir_segment"] + [{
        "cidr": "10.0.9.0/24", "name": "seg-10-0-9-0", "entities": 0,
        "windows_present": 1, "windows_total": 1,
        "coverage_source": "inferred_from_traffic"}]
    out = compile_twin(pit)
    assert "10.0.9.0/24" not in {s["cidr"] for s in _tc(out)["segments"]}
    assert out["provenance"]["population"]["segments_pruned"] == ["10.0.9.0/24"]
    assert any("carry no placed host at all" in c for c in out["caveats"])


def test_a_segment_whose_hosts_are_all_roleless_now_SURVIVES(tmp_path):
    """The reversal's headline topology effect, and the reason six segments returned on the real
    capture (§2). This exact fixture used to prune `10.0.9.0/24` — its only host is role-less —
    which silently deleted an observed network from the twin AND made row 2 unjudgeable."""
    out = _compiled(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20", 88),
        _flow(W1 + 2, "10.0.1.10", "10.0.1.20", 389),
        _flow(W1 + 3, "10.0.1.10", "10.0.9.9", 5353, proto="udp"),   # a second segment, no role
    ])
    assert "10.0.9.0/24" in {s["cidr"] for s in _tc(out)["segments"]}
    assert out["provenance"]["population"]["segments_pruned"] == []
    assert any(n.get("role") == UNROLED_ROLE and n["net"] == "seg-10-0-9-0"
               for n in _tc(out)["nodes"])


def test_a_persona_slower_than_the_capture_warns(tmp_path):
    """D12. `file-server`'s persona is `diurnal`; this PIT is one hour, so the twin would
    otherwise claim daily behavior nothing in it could have observed."""
    caveats = _compiled(tmp_path)["caveats"]
    assert any("cadence diurnal" in c and "capture longer" in c for c in caveats)


def test_a_persona_the_capture_could_have_seen_does_not_warn(tmp_path):
    """The other half: `domain-controller`'s slowest emit is `cyclic:15m`, well inside one
    window, so a warning there would be noise that trains operators to ignore the real one."""
    caveats = _compiled(tmp_path)["caveats"]
    assert not any("domain-controller" in c and "cadence" in c for c in caveats)


def test_the_twin_records_which_tap_it_came_from(tmp_path):
    """D1 reaching the far end: a twin compiled from one vantage represents that vantage's view
    and nothing wider, and the sidecar is where a consumer can see which."""
    prov = _compiled(tmp_path)["provenance"]
    assert "vantage" in prov and "network" in prov


# -- D1-D6: schedule / seed / intensity (`docs/WORK_ORDER_traffic_profiles.md`) --------------

def _assignment(out, role):
    return next(a for a in out["traffic_profile"]["traffic-profile"]["assignments"]
                if a["role"] == role)


def _diurnal_flows(hours, days, src, dst, dport):
    """One flow per (day, hour) pair, each on its own calendar day starting at `W1` — mirrors
    `test_ir_materialize.py::_diurnal_events`, kept local rather than imported across test files
    so each stays independently readable."""
    hours = sorted(hours)
    events = []
    for day in range(days):
        for h in hours:
            events.append(_flow(W1 + day * 86400 + h * 3600 + 1, src, dst, dport))
    return events


def _busy_domain_controller():
    """48 busy-band windows (8 hrs/day * 6 days, right at `_DIURNAL_MIN_WINDOWS`), both ports the
    `domain-controller` rule needs, all in one contiguous UTC band."""
    events = _diurnal_flows(range(9, 17), 6, "10.0.1.10", "10.0.1.20", 88)
    events += _diurnal_flows(range(9, 17), 6, "10.0.1.10", "10.0.1.20", 389)
    return events


def _flat_domain_controller():
    """72 windows uniform across all 24 hours (3 days) — the DELIBERATE flat contrast to
    `_busy_domain_controller`."""
    events = _diurnal_flows(range(24), 3, "10.0.1.10", "10.0.1.20", 88)
    events += _diurnal_flows(range(24), 3, "10.0.1.10", "10.0.1.20", 389)
    return events


def _two_roles_different_intensity():
    """`domain-controller` busy 48 windows at 2 flows/window; `file-server` only 4 windows at 1
    flow/window — well below the diurnal floor but past the rate one. Two roles with genuinely
    different measured rates, which is what makes `intensity` a RELATIVE claim rather than a
    trivial 1.0 (a single-role PIT normalizes against its own rate and always reads 1.0)."""
    events = _busy_domain_controller()
    events += [_flow(W1 + i * WINDOW + 3, "10.0.1.11", "10.0.1.31") for i in range(4)]
    return sorted(events, key=lambda e: e.ts)


def test_seed_is_always_present_even_when_no_floor_clears(tmp_path):
    """D5: a compiler CHOICE, never gated by an evidence floor. `_TWO_SEGMENTS` is one window —
    below every floor D4 declares — and `domain-controller` still gets a seed."""
    dc = _assignment(_compiled(tmp_path), "domain-controller")
    assert isinstance(dc["seed"], int)
    assert "schedule" not in dc and "intensity" not in dc


def test_seed_is_deterministic_across_two_compiles_of_one_pit(tmp_path):
    """`LES-002`: two casts of one PIT must agree byte-for-byte, seed included."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    a = _assignment(compile_twin(pit), "domain-controller")
    b = _assignment(compile_twin(pit), "domain-controller")
    assert a["seed"] == b["seed"]


def test_seed_never_uses_pythons_own_salted_hash(tmp_path):
    """`hash()` is salted per PROCESS (`PYTHONHASHSEED`); two casts in two different processes
    would then disagree, which is exactly what `_role_seed`'s own `sha256` choice avoids. Proxy
    for that property here: the seed must be stable across two fresh compiler calls in THIS
    process, which a `hash()`-based implementation would also pass — the real guarantee is
    reviewed at the source (`synth/compiler.py::_role_seed`), this just pins the observable
    behavior a regression would have to preserve."""
    from aximprint.synth.compiler import _role_seed
    assert _role_seed("pit-x", "domain-controller") == _role_seed("pit-x", "domain-controller")


def test_two_roles_in_one_pit_get_different_seeds(tmp_path):
    out = _compiled(tmp_path)
    dc = _assignment(out, "domain-controller")
    fs = _assignment(out, "file-server")
    assert dc["seed"] != fs["seed"]


def test_a_concentrated_role_is_classified_business_hours(tmp_path):
    out = _compiled(tmp_path, _busy_domain_controller())
    dc = _assignment(out, "domain-controller")
    assert dc["schedule"] == "business-hours"
    assert any("schedule is set" in c and "domain-controller" in c for c in out["caveats"])


def test_a_flat_role_is_classified_24x7(tmp_path):
    out = _compiled(tmp_path, _flat_domain_controller())
    dc = _assignment(out, "domain-controller")
    assert dc["schedule"] == "24x7"


def test_schedule_is_always_the_closed_vocabulary_when_present(tmp_path):
    from aximprint.ir import SCHEDULE_CLASSES
    for out in (_compiled(tmp_path, _busy_domain_controller()),
               _compiled(tmp_path, _flat_domain_controller())):
        dc = _assignment(out, "domain-controller")
        assert dc["schedule"] in SCHEDULE_CLASSES


def test_a_role_below_the_diurnal_floor_carries_no_schedule(tmp_path):
    """D4: `_TWO_SEGMENTS` gives `domain-controller` exactly one window of evidence — nothing
    about shape may be published from that."""
    dc = _assignment(_compiled(tmp_path), "domain-controller")
    assert "schedule" not in dc


def test_intensity_is_relative_and_the_busier_role_reads_higher(tmp_path):
    """D6: `domain-controller` runs 2 flows/window against `file-server`'s 1, so its intensity —
    normalized against the PIT's own median across the two — must read higher than file-server's,
    without pinning an exact float (the claim is the relative ORDERING, not one implementation's
    arithmetic)."""
    out = _compiled(tmp_path, _two_roles_different_intensity())
    dc = _assignment(out, "domain-controller")
    fs = _assignment(out, "file-server")
    assert dc["intensity"] > fs["intensity"] > 0
    assert any("intensity is set" in c for c in out["caveats"])


def test_intensity_caveat_names_no_axrange_driver_consumes_it(tmp_path):
    """(h): the caveat has to exist, or a measured-but-inert field could be mistaken for
    something that changed the twin."""
    out = _compiled(tmp_path, _two_roles_different_intensity())
    caveat = next(c for c in out["caveats"] if "intensity is set" in c)
    assert "no axrange" in caveat.lower() and "driver" in caveat.lower()


def test_a_role_below_the_rate_floor_carries_no_intensity(tmp_path):
    """`_TWO_SEGMENTS` gives every host one window — below `_RATE_MIN_WINDOWS` too."""
    dc = _assignment(_compiled(tmp_path), "domain-controller")
    assert "intensity" not in dc


def test_a_pit_without_a_traffic_profile_table_still_compiles(tmp_path):
    """A PIT materialized before schema `1.3` (or hand-built without the key) must degrade to
    "no evidence", never a `KeyError` — `pit.get(...)`, not `pit[...]`, is the whole point."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    del pit["ir_traffic_profile"]
    out = compile_twin(pit)
    dc = _assignment(out, "domain-controller")
    assert isinstance(dc["seed"], int)
    assert "schedule" not in dc and "intensity" not in dc


def test_schedule_and_intensity_provenance_land_in_the_sidecar_not_inline(tmp_path):
    out = _compiled(tmp_path, _two_roles_different_intensity())
    fields = out["provenance"]["fields"]
    seed_fields = [k for k in fields if k.endswith(".seed")]
    schedule_fields = [k for k in fields if k.endswith(".schedule")]
    intensity_fields = [k for k in fields if k.endswith(".intensity")]
    assert seed_fields and schedule_fields and intensity_fields
    assert all(fields[k]["evidence_class"] == COMPILER_CLASS for k in seed_fields)
    assert all(fields[k]["evidence_class"] == "imputed" for k in schedule_fields)
    assert all(fields[k]["evidence_class"] == "observed" for k in intensity_fields)
    for assignment in out["traffic_profile"]["traffic-profile"]["assignments"]:
        for a in ("seed", "schedule", "intensity"):
            if a in assignment:
                assert not (isinstance(assignment[a], dict) and "provenance" in assignment[a])


def test_every_caveated_role_actually_carries_the_field_the_caveat_names(tmp_path):
    """`LES-133`: a caveat naming a role must agree with what that role's own assignment
    actually carries, not merely be non-empty — a caveat that fires for the wrong reason would
    pass a point check and fail this one."""
    out = _compiled(tmp_path, _two_roles_different_intensity())
    assignments = {a["role"]: a for a in out["traffic_profile"]["traffic-profile"]["assignments"]}
    schedule_caveat = next((c for c in out["caveats"] if "schedule is set" in c), None)
    if schedule_caveat:
        for role, a in assignments.items():
            assert (role in schedule_caveat) == ("schedule" in a)
    intensity_caveat = next((c for c in out["caveats"] if "intensity is set" in c), None)
    if intensity_caveat:
        for role, a in assignments.items():
            assert (role in intensity_caveat) == ("intensity" in a)


def test_the_compiler_is_deterministic_including_traffic_profile(tmp_path):
    """DESIGN_BRIEF §9, extended past `twin_config`: the `tfc` document — schedule, seed and
    intensity included — must be byte-identical across two casts of one PIT too."""
    pit = _pit(tmp_path, _two_roles_different_intensity())
    a = to_yaml(compile_twin(pit)["traffic_profile"])
    b = to_yaml(compile_twin(pit)["traffic_profile"])
    assert a == b


# -- tap ---------------------------------------------------------------------------

def test_the_tap_plan_omits_sensors_rather_than_guessing(tmp_path):
    """Which Lab sensor captures which link is an `o2` fact aximprint cannot observe. A plan
    that fails axRange's validator with "sensors missing" is a better artifact than one that
    passes carrying a fabricated sensor name."""
    out = _compiled(tmp_path)
    bindings = out["tap_plan"]["tap-plan"]["bindings"]
    assert bindings
    assert all("sensors" not in b for b in bindings)
    assert any("INCOMPLETE by design" in c for c in out["caveats"])


def test_every_observed_segment_gets_a_tap_selector_and_the_sink_does_not(tmp_path):
    """Every segment the capture saw is bound. The internet sink's is deliberately NOT.

    A sink flow already crosses a tapped wire — its source host's own segment — so binding the
    sink as well mirrors the identical flow twice. Measured: ~a third of the twin's offered
    bytes go to the sink and are echoed back, which inflates the flow and byte axes while the
    row-keyed ones are unaffected. Omitting it is strictly better, not a trade: nothing becomes
    invisible, and the sink's hosts still report `segment: SINK_CIDR`, which is what the
    fingerprint's twin-side locality override keys on — it matches a reported CIDR, not a tap."""
    out = _compiled(tmp_path)
    targets = {b["selector"]["target"] for b in out["tap_plan"]["tap-plan"]["bindings"]}
    observed = {s["name"] for s in _tc(out)["segments"] if s["cidr"] != SINK_CIDR}
    assert targets == observed
    assert SINK_SEGMENT_NAME not in targets
    assert len(targets) == len(_tc(out)["segments"]) - 1
    assert any("deliberately NOT" in c and SINK_CIDR in c for c in out["caveats"])


def test_a_cast_twin_never_carries_instrumentation_agents(tmp_path):
    """`default_agents: false`, unconditionally, emitted by the compiler rather than left to a
    hand-edit on the installed file — which is exactly how it was lost once already: a re-cast
    silently discarded it and put the sweep back ON.

    Op count is the visible symptom (274 extra ops on the real twin, 137 hosts x 2 agents) and
    the least of it. Both agents TALK: a Prometheus exporter's scrapes and a Wazuh agent's
    enrolment are conversations the source network never had, the tap captures them like any
    other traffic, and the fingerprint scores them as edges. A stopped o2-services host makes
    it worse, not safer — a refused connect is still an S0 conn to Zeek, so a failed agent
    still manufactures an edge. A twin that exists to be MEASURED cannot carry uninstrumented
    traffic generators."""
    out = _compiled(tmp_path)
    assert _tc(out)["default_agents"] is False
    prov = out["provenance"]["fields"]["twin_config.default_agents"]
    assert prov["evidence_class"] == COMPILER_CLASS
    assert "fingerprint" in prov["reason"] and "S0" in prov["reason"]


# -- determinism --------------------------------------------------------------------

def test_the_compiler_is_deterministic(tmp_path):
    """DESIGN_BRIEF §9: same snapshot -> same YAML. A compiler whose output varied would make
    every re-compile look like drift."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    assert to_yaml(compile_twin(pit)["twin_config"]) == to_yaml(compile_twin(pit)["twin_config"])


def test_segments_and_nodes_are_ordered_stably(tmp_path):
    tc = _tc(_compiled(tmp_path))
    assert [s["cidr"] for s in tc["segments"]] == sorted(s["cidr"] for s in tc["segments"])


def test_the_three_documents_round_trip_through_yaml(tmp_path):
    out = _compiled(tmp_path)
    for key in ("twin_config", "traffic_profile", "tap_plan"):
        assert yaml_roundtrip(out[key]) == out[key]


def yaml_roundtrip(doc):
    import yaml as _y
    return _y.safe_load(to_yaml(doc))


# -- the store round trip the CLI actually performs ----------------------------------

def test_a_pit_read_back_from_the_store_compiles_identically(tmp_path):
    """The CLI compiles from `IRStore.read_pit`, not from the in-memory materialize output —
    so a column that survived materialization but not persistence would break only there."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    assert to_yaml(compile_twin(ir.read_pit(pit["ir_pit"][0]["pit_id"]))["twin_config"]) == \
        to_yaml(compile_twin(pit)["twin_config"])


# --------------------------------------------------------------------------------------------
# GAP-C4 (`docs/JOURNAL_corpapcn_fidelity.md`) — `relocate`.
#
# A cast reproduces the SOURCE network's CIDRs, which is right for a description and can be fatal
# for a deployment: a CorpAPCN cast produced 10.80.64.0/24 while the target cluster's own default
# gateway was 10.80.64.1, so the twin's router carried its upstream gateway's address on a LAN leg
# and every guest lost egress.
#
# Both lookups below (node -> segment, and conduit endpoints) key on the OBSERVED cidr while
# `segments[]` holds the RELOCATED one. Each raised on the first relocated cast and each is
# pinned here, because neither is reachable without passing `relocate`.
# --------------------------------------------------------------------------------------------
def test_relocate_rewrites_every_segment_into_the_given_prefix(tmp_path):
    out = _compiled(tmp_path, relocate="10.200.0.0/16")
    cidrs = [s["cidr"] for s in _tc(out)["segments"]]
    assert cidrs, "fixture must produce segments"
    assert all(c.startswith("10.200.") and c.endswith("/24") for c in cidrs), cidrs
    assert len(set(cidrs)) == len(cidrs), "relocated segments must stay distinct"


def test_relocate_keeps_nodes_and_conduits_resolvable(tmp_path):
    """The two lookups that broke: a node's `net` and a conduit's endpoints both resolve by
    segment NAME, and names must still join after the cidr underneath them has moved."""
    out = _compiled(tmp_path, relocate="10.200.0.0/16")
    tc = _tc(out)
    names = {s["name"] for s in tc["segments"]}
    for n in tc["nodes"]:
        for net in ([n["net"]] if "net" in n else [x["segment"] for x in n.get("nets", [])]):
            assert net in names, f"node {n['name']} points at unknown segment {net}"
    for c in tc.get("conduits", []):
        assert c["from"] in names and c["to"] in names, c


def test_relocate_records_the_original_cidr_rather_than_losing_it(tmp_path):
    """Relocation MOVES an address; it must never silently discard what was observed."""
    # one PIT, compiled twice — the comparison is only meaningful if both sides describe the
    # same observation
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    plain = {s["cidr"] for s in _tc(compile_twin(pit))["segments"]} - {SINK_CIDR}
    out = compile_twin(pit, relocate="10.200.0.0/16")
    rows = _observed_cidr_prov(out)
    assert rows, "every segment must carry cidr provenance"
    recorded = {v["transformation"]["from"]["value"] for v in rows.values()}
    assert recorded == plain, "the observed cidrs must all be recoverable from provenance"
    assert all(v["evidence_class"] == COMPILER_CLASS for v in rows.values()), \
        "a relocated cidr is no longer an observation and must not claim to be one"


def test_a_relocated_cidr_goes_through_the_transformation_record_not_precedence(tmp_path):
    """§5.6, adopted as axRange `docs/design/14-operator-assertions.md` §2: a value the compiler
    replaces FOR DEPLOYABILITY is applied after resolution, never competes, and always records
    what it replaced. The distinction is load-bearing downstream, not cosmetic: a scorecard must
    read this row as *reproduced under a declared transformation* rather than as a mismatch, and
    it cannot if the row is shaped like a `compiler` value that was INVENTED from nothing."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), relocate="10.200.0.0/16")
    rows = _observed_cidr_prov(out)
    assert rows
    for path, row in rows.items():
        tr = row["transformation"]
        assert tr["kind"] == "relocate"
        # It replaced an OBSERVED reading, and says so — that is the whole record.
        assert tr["from"]["evidence_class"] == "observed"
        assert tr["to"] != tr["from"]["value"]
        assert "colliding" in tr["reason"]
        # NOT a precedence event: nothing was outranked, so no `overrides` row is minted.
        assert "overrides" not in row, f"{path}: relocation must not read as a precedence win"
    # And the sink, which IS an invention, must not borrow the transformation shape: there is no
    # pre-transformation value, because nothing was transformed.
    sink = next(v for k, v in out["provenance"]["fields"].items()
                if k.startswith("twin_config.segments") and k.endswith(".cidr")
                and v.get("coverage_source") == "synthesized")
    assert sink["evidence_class"] == COMPILER_CLASS and "transformation" not in sink


def test_without_relocate_the_observed_cidrs_are_emitted_unchanged(tmp_path):
    """The default must stay a faithful description — relocation is opt-in."""
    out = _compiled(tmp_path)
    rows = _observed_cidr_prov(out)
    assert all(v["evidence_class"] == "observed" for v in rows.values())
    assert all("transformation" not in v for v in rows.values())


# -- the internet sink (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C17) ----------------------


def _sink_nodes(out):
    return [n for n in _tc(out)["nodes"] if str(n.get("role", "")).startswith("internet-sink-")]


def test_the_sink_reaches_the_twin_as_a_node_group_on_its_own_segment(tmp_path):
    """The twin's missing internet edge, as topology. 85% of the source's edges leave the
    network and an air-gapped twin can place none of them, so its hosts manufacture LOCAL
    edges instead — measured, `generic-endpoint->web-tier` local: source 3, twin 225."""
    from aximprint.synth.personas import SINK_BUCKETS

    out = _compiled(tmp_path)
    tc = _tc(out)
    assert {s["cidr"] for s in tc["segments"]} & {SINK_CIDR} == {SINK_CIDR}
    sink = _sink_nodes(out)
    assert {n["name"] for n in sink} == {f"internet-sink-{b}" for b in SINK_BUCKETS}
    # The `count:` IS the fan-out mechanism, so the sizes are the contract, not a detail.
    assert sorted(n.get("count", 1) for n in sink) == sorted(SINK_BUCKETS)
    assert all(n["net"] == SINK_SEGMENT_NAME for n in sink)


def test_the_sink_gets_a_router_leg_despite_the_journal_saying_otherwise(tmp_path):
    """GAP-C17 specified "a segment the gw does not forward". Reversed deliberately: a sink
    nothing outside its own /24 can reach absorbs nothing, so the twin's hosts go straight
    back to manufacturing local edges and the sink is 15 idle VMs. Containment never rested
    on the leg — it rests on personas being unable to NAME an address at all."""
    gw = next(n for n in _tc(_compiled(tmp_path))["nodes"] if n["name"] == GATEWAY_NAME)
    assert {"segment": SINK_SEGMENT_NAME} in gw["nets"]


def test_the_sink_is_declared_synthetic_and_observes_nothing(tmp_path):
    """A twin that quietly counted 15 invented hosts as discovered would misrepresent the
    network it claims to replicate — the same failure as a redacted field."""
    out = _compiled(tmp_path)
    pop = out["provenance"]["population"]
    sink_rows = [g for g in pop["node_groups"] if g.get("synthesized") == "internet-sink"]
    assert len(sink_rows) == 4 and all(g["observed"] == 0 for g in sink_rows)
    # The sidecar's reconstruction identity must still hold with the sink present.
    assert pop["placed_hosts"] == sum(g["observed"] for g in pop["node_groups"])
    assert any("SYNTHETIC and observe nothing" in c for c in out["caveats"])
    seg = next(v for k, v in out["provenance"]["fields"].items()
               if k.startswith("twin_config.segments") and k.endswith(".cidr")
               and v.get("coverage_source") == "synthesized")
    assert seg["evidence_class"] == COMPILER_CLASS


def test_an_empty_twin_gets_no_sink(tmp_path):
    """"Nothing was placed" is a real, disclosed outcome. Injecting a sink into it would replace
    that with a twin containing only 15 nodes for absent hosts to talk to.

    Since D1 the ONLY way to reach an empty twin is an empty PLACED set — no filter can produce
    one — so the fixture is link-local traffic, which is not provisionable (`LES-110`). It used
    to be `--min-occupancy 1.01`, and that route is deliberately gone."""
    out = _compiled(tmp_path, [_flow(W1 + 1, "fe80::1", "fe80::2", 5353, proto="udp")])
    assert _tc(out)["nodes"] == [] and _tc(out)["segments"] == []
    assert any("EMPTY TWIN" in c for c in out["caveats"])
    assert not any("SYNTHETIC and observe nothing" in c for c in out["caveats"])


def test_max_count_does_not_shrink_the_sink(tmp_path):
    """`--max-count` bounds how much of an OBSERVED population is deployed, and there is none
    here. Capping `internet-sink-8` to 2 would silently halve the fan-out of every persona
    that emits at it, which is a wrong twin rather than a smaller one."""
    from aximprint.synth.personas import SINK_BUCKETS

    out = _compiled(tmp_path, max_count=2)
    assert sorted(n.get("count", 1) for n in _sink_nodes(out)) == sorted(SINK_BUCKETS)
    assert not any("internet-sink" in c and "capped" in c for c in out["caveats"])


def test_the_sink_keeps_its_address_through_relocation(tmp_path):
    """Relocation exists to move the twin out of the DEPLOYMENT TARGET's address space. The
    sink is not part of that problem, and a sink that moved per-prefix would need its
    containment argument re-made for every prefix a caller picks."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    plain = _tc(compile_twin(pit))
    moved = _tc(compile_twin(pit, relocate="10.200.0.0/16"))
    assert SINK_CIDR in {s["cidr"] for s in plain["segments"]}
    assert SINK_CIDR in {s["cidr"] for s in moved["segments"]}
    # And it must not consume a cursor index, or every real segment would shift by one the day
    # the sink was introduced and two otherwise-identical casts would stop comparing.
    real = sorted(s["cidr"] for s in moved["segments"] if s["cidr"] != SINK_CIDR)
    assert real == [f"10.200.{i}.0/24" for i in range(len(real))]


def _many_segment_pit(tmp_path, n):
    """A PIT with `n` live segments, each carrying one role-bearing host, so a relocated cast
    walks the cursor from index 0 to index n-1."""
    events = [_flow(W1 + i, f"10.{i // 254}.{i % 254}.10", "10.0.1.20", 88) for i in range(n)]
    events += [_flow(W1 + 1000 + i, f"10.{i // 254}.{i % 254}.10", "10.0.1.20", 389)
               for i in range(n)]
    return _pit(tmp_path, events)


@pytest.mark.slow
def test_a_relocation_cursor_that_reaches_the_sink_is_refused(tmp_path):
    """The sink's index is INSIDE the range the 256-segment guard permits, so this is a live
    path a wider capture reaches, not a theoretical one.

    Sized at the top of the range on purpose. `SINK_CIDR`'s third octet is 254 and the
    relocation guard refuses only above 256, so indices 0..255 are all reachable and 254 is
    the second-highest of them — exactly where an off-by-one would hide. The capture that
    drove this work has 26 live segments; 255 is what it takes to arrive, and arriving must
    be a refusal rather than twin hosts sharing the network the containment argument assumes
    is synthetic."""
    pit = _many_segment_pit(tmp_path, 255)
    compile_twin(pit)                       # unrelocated: no collision, so no refusal
    with pytest.raises(ValueError, match="collides with live segment"):
        compile_twin(pit, relocate="10.200.0.0/16")


@pytest.mark.slow
def test_one_segment_short_of_the_sink_still_compiles(tmp_path):
    """The other side of the off-by-one, and the reason the test above is not vacuous: 254
    segments fill indices 0..253 and stop one short, so the cast must succeed. A guard that
    fired here would be refusing correct casts, which reads downstream exactly like the
    collision it is meant to catch."""
    from aximprint.synth.personas import SINK_BUCKETS

    out = compile_twin(_many_segment_pit(tmp_path, 254), relocate="10.200.0.0/16")
    cidrs = {s["cidr"] for s in _tc(out)["segments"]}
    assert "10.200.253.0/24" in cidrs, "the cursor must actually reach index 253"
    assert SINK_CIDR in cidrs and "10.200.254.0/24" == SINK_CIDR
    assert len(_sink_nodes(out)) == len(SINK_BUCKETS)


def test_an_observed_segment_on_the_sinks_address_is_refused(tmp_path):
    """The other half: a capture whose own network IS the sink's would otherwise put real
    twin hosts and the sink on one segment."""
    net = SINK_CIDR.split("/")[0].rsplit(".", 1)[0]
    with pytest.raises(ValueError, match="collides with live segment"):
        _compiled(tmp_path, _TWO_SEGMENTS + [
            _flow(W1 + 20, f"{net}.10", f"{net}.20", 88),
            _flow(W1 + 21, f"{net}.10", f"{net}.20", 389)])


def test_the_sink_is_content_only_and_can_never_be_inferred(tmp_path):
    """Held out of `roles:` so `match_role` cannot reach it at all, rather than carrying a
    `synthetic: true` flag the matcher has to remember to honour. A sink role inferred from a
    real host's ports would label that host "the internet"."""
    from aximprint.catalog import ROLE_CATALOG, SYNTHETIC_ROLES

    assert set(SYNTHETIC_ROLES) == {"internet-sink-1", "internet-sink-2", "internet-sink-4",
                                    "internet-sink-8"}
    assert not any(r.role in SYNTHETIC_ROLES for r in ROLE_CATALOG)
    assert all(r.persona is None and r.min_capture_seconds is None
               for r in SYNTHETIC_ROLES.values())
    # Sized down: 15 VMs whose whole job is to echo. `DEFAULT_NODE`'s 2Gi/20Gi apiece would be
    # 30Gi of RAM and 300Gi of disk, more than the twin's real hosts.
    assert all(r.resources["ram"] == "1Gi" for r in SYNTHETIC_ROLES.values())
    # No persona, so it reaches the twin with a role and no `tfc` assignment (D5a).
    out = _compiled(tmp_path)
    assigned = {a.get("role") for a in out["traffic_profile"]["traffic-profile"]["assignments"]}
    assert not any(r in assigned for r in SYNTHETIC_ROLES)


# -- the precedence resolver (`docs/WORK_ORDER_reference_ingest.md` S1) -------------------
#
# Exercised with SYNTHETIC pairs, deliberately and of necessity: nothing in the store produces
# two classes for one fact yet (S2's assertions file is the first producer), so a test driven
# from a real PIT could only ever confirm the single-class path. The work order names exactly
# this — "the resolver must be exercised by unit tests with synthetic pairs".


def _f(cls, value, **kw):
    return Fact(cls, value, **kw)


def test_a_single_reading_wins_uncontested_and_mints_no_override():
    """Every field in every cast today, and the shape that must not change when S2 lands: a
    lone fact is not a competition, so there is nothing to name as a loser."""
    winner, overrides = resolve_precedence("nodes[0].role", [_f("imputed", "dns-resolver")])
    assert winner.value == "dns-resolver" and overrides is None


@pytest.mark.parametrize("higher,lower", [
    ("asserted", "imported"), ("asserted", "observed"), ("asserted", "referenced"),
    ("asserted", "imputed"), ("imported", "observed"), ("imported", "referenced"),
    ("imported", "imputed"), ("observed", "referenced"), ("observed", "imputed"),
    ("referenced", "imputed"),
])
def test_the_higher_class_wins_every_pair_in_the_declared_order(higher, lower):
    """Every ORDERED PAIR of the five, not a sample: the order is the model's whole content, and
    a table checked at three of its ten edges is a table checked nowhere in particular."""
    winner, overrides = resolve_precedence(
        "f", [_f(lower, "loser"), _f(higher, "winner")])
    assert winner.evidence_class == higher and winner.value == "winner"
    assert overrides == {"class": lower, "value": "loser"}


def test_the_winner_is_independent_of_the_order_the_facts_arrive_in():
    """`LES-009`: no output byte may depend on arrival order. Two classes for one field will
    arrive from two different producers, in whatever order the store hands them over."""
    facts = [_f("imputed", "a"), _f("asserted", "b"), _f("observed", "c")]
    for perm in itertools.permutations(facts):
        winner, overrides = resolve_precedence("f", perm)
        assert (winner.value, overrides) == ("b", {"class": "observed", "value": "c"})


def test_the_runner_up_is_named_not_the_whole_field():
    """The house rule is "the sidecar names the loser", singular: the reading that WOULD have won
    is the alternative a reader needs. The rest is not erased — the store keeps every reading —
    it simply is not restated here, where a third-place value would look like a second
    competition happened."""
    _winner, overrides = resolve_precedence(
        "f", [_f("asserted", "a"), _f("observed", "b"), _f("imputed", "c")])
    assert overrides == {"class": "observed", "value": "b"}


def test_two_facts_of_one_class_are_refused_rather_than_ordered():
    """A contradiction inside one producer is not a precedence question, and picking by arrival
    order is precisely the row-order dependence `LES-009` forbids. There is no honest tiebreak,
    so there is no tiebreak (CONVENTIONS §3)."""
    with pytest.raises(ValueError, match="two 'observed' facts"):
        resolve_precedence("nodes[0].role", [_f("observed", "x"), _f("observed", "y")])


def test_the_compiler_class_cannot_enter_a_precedence_comparison():
    """The structural half of "a declared transformation is not a precedence event". A
    `compiler` value beating an `imported` one would invert the order the model rests on, so it
    is refused at the rank function rather than trusted to stay out by convention."""
    with pytest.raises(ValueError, match="not an IR evidence class"):
        resolve_precedence("f", [_f(COMPILER_CLASS, "lab"), _f("imported", "real")])


def test_an_unknown_class_is_refused_rather_than_sorted_last():
    """Fail closed: a class this build does not know might outrank everything here, and
    defaulting it to last place would silently decide that it does not."""
    with pytest.raises(ValueError, match="not an IR evidence class"):
        resolve_precedence("f", [_f("hearsay", "x"), _f("observed", "y")])


def test_resolve_precedence_needs_a_fact():
    with pytest.raises(ValueError, match="at least one fact"):
        resolve_precedence("f", [])


def test_the_precedence_order_is_read_from_the_vocabulary_not_a_second_table():
    """`LES-101`/D1: `EVIDENCE_CLASSES` IS the ranking, so there is no second table to drift.
    Pinned by comparing the resolver's own verdicts against the tuple's order rather than
    against a re-typed list, which would be the very duplication this checks the absence of."""
    from aximprint.ir import EVIDENCE_CLASSES

    for stronger, weaker in itertools.combinations(EVIDENCE_CLASSES, 2):
        winner, _ = resolve_precedence("f", [_f(weaker, 0), _f(stronger, 1)])
        assert winner.evidence_class == stronger


def test_a_declared_transformation_records_what_it_replaced():
    """The other half: not a winner over a loser, but a value MOVED for deployability, with the
    pre-transformation reading beside it. `from` carries the replaced fact's own class, so a
    scorecard can tell "we moved something observed" from "we invented something"."""
    record = declared_transformation(
        "relocate", replaced=_f("observed", "10.80.64.0/24", signal="ir_segment.cidr"),
        reason="relocated to avoid colliding with the deployment target's own address space",
        to="10.200.0.0/24")
    assert record == {
        "kind": "relocate",
        "reason": "relocated to avoid colliding with the deployment target's own address space",
        # The replaced reading's PROVENANCE survives, not just its value: a pre-transformation
        # value stripped of what backed it has already lost half of itself (CONVENTIONS §3).
        "from": {"evidence_class": "observed", "value": "10.80.64.0/24",
                 "signal": "ir_segment.cidr"},
        "to": "10.200.0.0/24"}
    # It is a record, not a competitor: nothing about it is shaped like `overrides`.
    assert "class" not in record and "value" not in record
    # Absent, never null, where the producer supplied nothing (`LES-006`).
    bare = declared_transformation("relocate", replaced=_f("observed", "x"), reason="r")
    assert bare["from"] == {"evidence_class": "observed", "value": "x"}


def test_the_role_row_is_produced_THROUGH_the_resolver_not_beside_it(tmp_path, monkeypatch):
    """The resolver is on the LIVE path, not parked next to it.

    `role` is the first field an assertions file overrides (axRange
    `docs/design/14-operator-assertions.md` §3's worked example is exactly this row), so it is
    routed through `resolve_precedence` with a one-element list today. That matters twice: a
    resolver nothing calls is a rule nobody has to keep working (`LES-101` one layer over), and
    S2's change becomes "append a Fact" rather than "start calling a function that has never
    run". Checked by intercepting the resolver rather than by reading its output, because the
    output of a one-fact resolution is indistinguishable from not resolving at all — which is
    the whole reason this could rot unnoticed."""
    import aximprint.synth.compiler as mod

    calls = []
    real = mod.resolve_precedence

    def spy(field, facts):
        facts = list(facts)
        calls.append((field, facts))
        return real(field, facts)

    monkeypatch.setattr(mod, "resolve_precedence", spy)
    out = _compiled(tmp_path)

    roled = [n for n in _tc(out)["nodes"]
             if n.get("role") not in (None, UNROLED_ROLE, "gw")
             and not str(n.get("role")).startswith("internet-sink-")]
    assert roled, "the fixture must produce at least one inferred role to resolve"
    assert len(calls) == len(roled), "every inferred role must go through the resolver"
    for field, facts in calls:
        assert field.endswith(".role")
        assert [f.evidence_class for f in facts] == ["imputed"]
        assert facts[0].signal.startswith("catalog/roles.yaml ")


def test_an_uncontested_resolution_emits_no_overrides_key(tmp_path):
    """Byte-compatibility of the routing above: with one reading there is no competition, so
    the sidecar must look exactly as it did before the resolver existed. An `overrides: null`
    would be a claim that something was beaten (`LES-006`)."""
    out = _compiled(tmp_path)
    assert not any("overrides" in v for v in out["provenance"]["fields"].values())


# -- `signal` names the row that MATCHED, not the first row of that name --------------------


#: Hosts that reach ONE role through DIFFERENT catalog rows. `web-tier` is pinned by
#: `(role, requires)` rather than by index (`LES-136`): the catalog ships one row requiring
#: `443/tcp` and another requiring `80/tcp`, and `_rule_for` returns whichever sorts first. A
#: host that answered only on 80 must not be published as evidence about 443.
_MULTI_ROW_ROLE = [
    _flow(W1 + 1, "10.0.1.10", "10.0.1.20", 443),   # 10.0.1.20 -> web-tier via 443/tcp
    _flow(W1 + 2, "10.0.1.11", "10.0.2.30", 80),    # 10.0.2.30 -> web-tier via 80/tcp
    _flow(W1 + 3, "10.0.1.12", "10.0.1.20", 443),
]


def test_the_role_signal_names_the_catalog_row_that_MATCHED_not_the_first_of_that_name(tmp_path):
    """The bug this pins, measured on the CorpAPCN PIT before the fix: **70 of 122 roled placed
    hosts** carried a `signal` naming ports that did not produce their match — 45 of them
    dialling `123/udp` and captioned `->443/tcp`, because `_rule_for` returns the FIRST row
    bearing a role's name and a role name maps to several rows with different evidence.

    Worse than an absent field: a specific, plausible, WRONG evidence string is unfalsifiable at
    the consumer, inside the one artifact whose job is to keep observed and invented apart."""
    out = _compiled(tmp_path, _MULTI_ROW_ROLE)
    tc = _tc(out)
    fields = out["provenance"]["fields"]

    by_port = {}
    for i, node in enumerate(tc["nodes"]):
        if node.get("role") != "web-tier":
            continue
        row = fields[f"twin_config.nodes[{i}].role"]
        listened = {p for m in ("443/tcp", "80/tcp") if m in row["signal"] for p in [m]}
        by_port.setdefault(frozenset(listened), []).append(row["signal"])

    assert by_port, "the fixture must place at least one web-tier node"
    # Both rows are represented, and NEITHER group borrows the other's ports.
    assert frozenset({"443/tcp"}) in by_port and frozenset({"80/tcp"}) in by_port, (
        f"one row's evidence is standing in for the other's: {by_port}")
    for ports, signals in by_port.items():
        other = "80/tcp" if "443/tcp" in ports else "443/tcp"
        assert all(other not in s for s in signals), f"{signals} names {other}, which it is not"


def test_a_dial_matched_role_is_not_published_under_a_listen_rows_ports(tmp_path):
    """The largest slice of the real defect: `generic-endpoint` is five DIAL-only rows, and the
    name lookup published every one of them under the first. A host that only ever dialled
    `123/udp` must not be captioned with `443/tcp` — the two are different claims about what
    the host was seen doing."""
    out = _compiled(tmp_path, [
        # Two hosts on DIFFERENT segments, so they form two groups rather than one
        # heterogeneous one, each reaching `generic-endpoint` through its own dial row.
        _flow(W1 + 1, "10.0.1.40", "10.0.9.9", 123, local_dst=False, proto="udp"),
        _flow(W1 + 2, "10.0.2.50", "10.0.9.9", 443, local_dst=False),
    ])
    tc = _tc(out)
    fields = out["provenance"]["fields"]
    signals = {}
    for i, node in enumerate(tc["nodes"]):
        if node.get("role") == "generic-endpoint":
            signals[node["net"]] = fields[f"twin_config.nodes[{i}].role"]["signal"]
    assert len(signals) == 2, f"the fixture must place two generic-endpoints: {signals}"
    ntp = next(v for v in signals.values() if "123/udp" in v)
    web = next(v for v in signals.values() if "443/tcp" in v)
    assert ntp == "catalog/roles.yaml generic-endpoint (->123/udp)", ntp
    assert web == "catalog/roles.yaml generic-endpoint (->443/tcp)", web
    assert "443" not in ntp, f"{ntp} publishes a port that host was never seen touching"


def test_a_heterogeneous_group_degrades_to_the_role_rather_than_naming_one_members_row(tmp_path):
    """A node group can legitimately hold hosts that reached one role by different rows. Naming
    either row would describe part of the group as if it were all of it, so the honest answer is
    the coarser TRUE statement — the file and the role, no port list (`LES-006`)."""
    from aximprint.synth.compiler import _matched_evidence

    web443 = {"listen_ports": '["443/tcp"]', "dial_ports": "[]"}
    web80 = {"listen_ports": '["80/tcp"]', "dial_ports": "[]"}
    assert _matched_evidence("web-tier", [web443, web443]) == (("443/tcp",), ())
    assert _matched_evidence("web-tier", [web443, web80]) is None   # two rows -> no one claim
    assert _matched_evidence("web-tier", []) is None                # a synthetic group has none
    assert _matched_evidence("web-tier", [{"listen_ports": "[]", "dial_ports": "[]"}]) is None


def test_a_rule_for_a_different_role_names_no_evidence_for_this_one(tmp_path):
    """Measured on the CorpAPCN PIT, not defensive: one host carries the stored role
    `dns-resolver` while its PIT-AGGREGATED ports (88 + 389) now match the `domain-controller`
    row. The stored role was inferred per window; the PIT unions ports across every window, so
    re-running the census over the union can land on a more specific row than the one that
    fired. Captioning that host's `dns-resolver` with a domain controller's evidence is a
    different fabrication from the one this fix removes, and just as unfalsifiable."""
    from aximprint.synth.compiler import _matched_evidence

    dc_shaped = {"listen_ports": '["88/tcp","389/tcp"]', "dial_ports": "[]"}
    assert _matched_evidence("domain-controller", [dc_shaped]) == (("389/tcp", "88/tcp"), ())
    assert _matched_evidence("dns-resolver", [dc_shaped]) is None


def test_the_signal_is_stable_regardless_of_frozenset_iteration_order():
    """`requires` is a frozenset, whose iteration order is not the document's order. Sorted, so
    the emitted byte string cannot vary between processes (`LES-002`, `LES-009`)."""
    from aximprint.synth.compiler import _role_signal

    assert _role_signal("domain-controller", (("389/tcp", "88/tcp"), ())) == \
        "catalog/roles.yaml domain-controller (389/tcp+88/tcp)"
    assert _role_signal("x", ((), ("88/tcp",))) == "catalog/roles.yaml x (->88/tcp)"
    assert _role_signal("x", None) == "catalog/roles.yaml x"
    assert _role_signal("internet-sink-8", None, synthetic=True) == \
        "catalog/roles.yaml (synthetic: internet-sink-8)"


# -- operator assertions (`docs/WORK_ORDER_reference_ingest.md` S2) ----------------

def _assertions(**blocks):
    """A parsed `AssertionSet` from the blocks a test cares about."""
    from aximprint.operator_assertions import parse_assertions

    return parse_assertions({
        "schema": "axr-assertions/1", "imprint": "t",
        "as_of": "2026-09-04T18:00:00Z", "source": "operator: raghu", **blocks},
        path="fixture.yaml")


def _named(out):
    return {n["name"]: n for n in _tc(out)["nodes"]}


def test_a_cast_with_no_assertions_is_byte_identical_to_one_compiled_before_the_mechanism(
        tmp_path):
    """The property that makes the whole mechanism safe to land: `assertions=None` must not move
    a single byte. Checked against `assertions=` an EMPTY set as well, because "no file" and "a
    file that says nothing" are different inputs and only the first is the pre-S2 path."""
    plain = to_yaml(_compiled(tmp_path)["twin_config"])
    explicit_none = to_yaml(compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "b.duckdb"),
                                         assertions=None)["twin_config"])
    empty = to_yaml(compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "c.duckdb"),
                                 assertions=_assertions())["twin_config"])
    assert plain == explicit_none == empty


def test_the_same_pit_and_the_same_file_compile_byte_identically(tmp_path):
    """D16. Two `AssertionSet`s parsed from one document are equal values in canonical order, so
    nothing downstream can iterate the file and reach a different answer (`LES-002`)."""
    aset = _assertions(override=[{"key": "address:10.0.1.20", "field": "role",
                                  "value": "web-tier", "note": "n"}])
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    one = to_yaml(compile_twin(pit, assertions=aset)["twin_config"])
    two = to_yaml(compile_twin(pit, assertions=_assertions(
        override=[{"key": "address:10.0.1.20", "field": "role", "value": "web-tier",
                   "note": "n"}]))["twin_config"])
    assert one == two


def test_an_asserted_role_beats_the_census_and_the_sidecar_names_the_loser(tmp_path):
    """The contest S1 built the resolver for and could not exercise: `asserted` outranks
    `imputed`, and `overrides` names the reading that WOULD have won — which is the alternative a
    reader needs, not the winner restated."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        override=[{"key": "address:10.0.1.20", "field": "role", "value": "web-tier",
                   "note": "it serves the intranet"}]))
    node = next(n for n in _tc(out)["nodes"] if n.get("role") == "web-tier")
    assert node["name"].endswith("-asserted"), \
        "an override is visible in the twin config, not only in the sidecar"
    idx = _tc(out)["nodes"].index(node)
    row = out["provenance"]["fields"][f"twin_config.nodes[{idx}].role"]
    assert row["evidence_class"] == "asserted"
    assert row["confidence"] == 1.0 and row["source"] == "operator:raghu"
    assert row["note"] == "it serves the intranet"
    assert row["overrides"] == {"class": "imputed", "value": "domain-controller"}


def test_an_asserted_host_never_shares_a_node_group_with_an_unasserted_one(tmp_path):
    """A group's sidecar row publishes ONE class for the whole group, so a hand-edited host
    sharing a group would publish its neighbours' inferred role as `asserted`. The group key
    carries an assertion signature precisely to make that impossible."""
    events = _TWO_SEGMENTS + [_flow(W1 + 20, "10.0.1.10", "10.0.1.34")]   # a 4th file-server
    out = compile_twin(_pit(tmp_path, events), assertions=_assertions(
        override=[{"key": "address:10.0.1.31", "field": "image", "value": "custom@v9",
                   "note": "a bespoke build"}]))
    nodes = _named(out)
    plain = nodes["file-server-10-0-1-core"]
    asserted = nodes["file-server-10-0-1-core-asserted"]
    assert plain["image"] != "custom@v9" and plain["count"] == 3
    assert asserted["image"] == "custom@v9" and "count" not in asserted


def test_an_asserted_image_records_the_catalog_value_it_displaced(tmp_path):
    """`image`/`resources` are `compiler`-class today; asserting one moves it into a SCORED class,
    which is the point (`14` §4). `overrides` carries the catalog default — "what it would have
    been" — and a `compiler` value appearing there is not a masquerade: it never passed through
    `evidence_rank`, which refuses the class outright."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        override=[{"key": "address:10.0.1.20", "field": "image", "value": "custom@v9",
                   "note": "a bespoke build"}]))
    fields = out["provenance"]["fields"]
    path = next(k for k, v in fields.items()
                if k.endswith(".image") and v["evidence_class"] == "asserted")
    idx = int(path.split("[")[1].split("]")[0])
    node = _tc(out)["nodes"][idx]
    assert node["image"] == "custom@v9"
    beaten = fields[path]["overrides"]
    assert beaten["class"] == COMPILER_CLASS
    # The value it displaced is the catalog's own row for the role this node ended up with —
    # "what the compiler would have chosen", which is exactly what the record claims.
    assert beaten["value"] != "custom@v9"
    assert fields[path]["confidence"] == 1.0 and fields[path]["source"] == "operator:raghu"


def test_an_added_segment_enters_the_cast_with_no_observation_block(tmp_path):
    """It survives the emptiness prune (an operator declared it), and it is NOT counted as
    unmeasured: nothing observed it, so there is no denominator missing — there is no measurement
    to have."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "segment", "key": "10.0.9.0/24", "name": "seg-ot",
              "zone_type": "vlan", "note": "the OT lab"}]))
    seg = next(s for s in _tc(out)["segments"] if s["name"] == "seg-ot")
    assert seg["cidr"] == "10.0.9.0/24" and seg["zone_type"] == "vlan"
    assert "observation" not in seg
    idx = _tc(out)["segments"].index(seg)
    row = out["provenance"]["fields"][f"twin_config.segments[{idx}].cidr"]
    assert row["evidence_class"] == "asserted" and row["confidence"] == 1.0
    assert out["provenance"]["population"]["assertions"]["added_segments"] == ["10.0.9.0/24"]
    assert not any("cannot be computed" in c and "10.0.9.0/24" in c for c in out["caveats"])


def test_an_asserted_zone_type_displaces_the_compilers_constant_on_an_observed_segment(tmp_path):
    """D11(c). The compiler's `ZONE_TYPE` is a `compiler` choice made because *"no VLAN evidence
    exists at this vantage"*; once somebody supplies it, the choice is not made at all."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "segment", "key": "10.0.1.0/24", "zone_type": "vlan",
              "note": "a tagged VLAN"}]))
    seg = next(s for s in _tc(out)["segments"] if s["cidr"] == "10.0.1.0/24")
    assert seg["zone_type"] == "vlan"
    idx = _tc(out)["segments"].index(seg)
    row = out["provenance"]["fields"][f"twin_config.segments[{idx}].zone_type"]
    assert row["evidence_class"] == "asserted"
    assert row["overrides"] == {"class": COMPILER_CLASS, "value": "simple"}
    # It was already observed, so nothing was CREATED — and saying so beats treating a redundant
    # add as a new segment.
    assert out["provenance"]["population"]["assertions"]["added_segments"] == []


def test_an_added_host_carries_its_listen_set_its_own_rung_and_no_observation_block(tmp_path):
    """`windows_seen` is `None`, not 0 — nothing observed it, so it has no presence to report and
    a zero would assert it was looked for and never seen (`LES-006`). `ASSERTED_RUNG` keeps it off
    the occupancy ladder and out of the unmeasured caveat."""
    from aximprint.synth.compiler import ASSERTED_RUNG

    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "host", "key": "address:10.0.1.99", "role": "file-server",
              "listen": [{"port": 445, "proto": "tcp"}], "note": "never seen at this tap"}]))
    node = next(n for n in _tc(out)["nodes"] if n["name"].endswith(f"-{ASSERTED_RUNG}"))
    assert node["role"] == "file-server"
    assert "observation" not in node
    group = next(g for g in out["provenance"]["population"]["node_groups"]
                 if g["node"] == node["name"])
    assert group["observed"] == 0 and group["asserted"] == 1, \
        "an asserted host must not inflate the population the capture claims to have FOUND"
    idx = _tc(out)["nodes"].index(node)
    row = out["provenance"]["fields"][f"twin_config.nodes[{idx}].role"]
    assert row["evidence_class"] == "asserted"
    assert "overrides" not in row, "nothing imputed anything about a host nothing observed"


def test_an_added_hosts_segment_is_derived_by_containment_when_it_can_be(tmp_path):
    """D11(a). By CONTAINMENT, never by string prefix (`LES-008`): `10.0.11.5` is not in
    `10.0.1.0/24`, and a prefix comparison says it is."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "host", "key": "address:10.0.2.99", "note": "n"}]))
    node = next(n for n in _tc(out)["nodes"] if n["name"].endswith("-asserted"))
    assert node["net"] == "seg-10-0-2-0"


@pytest.mark.parametrize("key,extra", [
    ("hostname:ot-fs01", {}),                       # a hostname names no network
    ("address:203.0.113.7", {}),                    # inside no known segment
])
def test_an_added_host_with_no_derivable_segment_is_refused_loudly(tmp_path, key, extra):
    """D11(a) rules "required otherwise; refused loudly if neither". A hard refusal rather than an
    ineffective-record line, because it is not a stale record: a host with no network is not a
    twin node under ANY PIT, so the file is wrong rather than out of date."""
    with pytest.raises(ValueError, match="names no `segment:`"):
        compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
            add=[{"kind": "host", "key": key, "note": "n", **extra}]))


def test_an_added_host_on_an_undeclared_segment_is_refused_naming_the_remedy(tmp_path):
    with pytest.raises(ValueError, match="neither an observed segment nor one this file adds"):
        compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
            add=[{"kind": "host", "key": "address:10.9.9.9", "segment": "10.9.9.0/24",
                  "note": "n"}]))


def test_an_added_conduit_joins_two_live_segments(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "segment", "key": "10.0.9.0/24", "name": "seg-ot", "note": "the OT lab"},
             {"kind": "host", "key": "address:10.0.9.5", "role": "file-server",
              "note": "the OT lab's server"},
             {"kind": "conduit", "key": "10.0.9.0/24|10.0.1.0/24",
              "note": "the OT lab reaches the DC"}]))
    assert {"from": "seg-10-0-1-0", "to": "seg-ot"} in _tc(out)["conduits"] or \
           {"from": "seg-ot", "to": "seg-10-0-1-0"} in _tc(out)["conduits"]
    assert out["provenance"]["population"]["assertions"]["added_conduits"] == [
        "10.0.1.0/24|10.0.9.0/24"]


def test_a_conduit_naming_a_segment_the_twin_lacks_is_reported_not_fatal(tmp_path):
    """A resolution MISS, not a shape error: the PIT moves underneath a standing file, so this
    lands in `caveats` and in `population.assertions.ineffective` rather than failing the
    compile."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "conduit", "key": "10.0.1.0/24|10.9.9.0/24", "note": "n"}]))
    report = out["provenance"]["population"]["assertions"]["ineffective"]
    assert [r["kind"] for r in report] == ["add:conduit"]
    assert "10.9.9.0/24" in report[0]["reason"]
    assert any("had NO EFFECT" in c for c in out["caveats"])


def test_a_suppressed_placed_host_leaves_the_twin_and_the_identity_still_closes(tmp_path):
    """D18: *a suppressed entity never silently vanishes*. It is out of `placed`, in
    `population.suppressed` with its note, named in `caveats`, and the reconstruction identity
    becomes `placed_hosts == sum(observed) + suppressed`."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        suppress=[{"key": "address:10.0.1.31", "note": "out of scope: a contractor laptop"}]))
    pop = out["provenance"]["population"]
    assert _named(out)["file-server-10-0-1-core"]["count"] == 2, "was 3"
    row = next(r for r in pop["suppressed"] if r["key"] == "address:10.0.1.31")
    assert row["placed"] is True and row["matched"] is True
    assert row["note"] == "out of scope: a contractor laptop"
    assert pop["placed_hosts"] == sum(g["observed"] for g in pop["node_groups"]) + 1
    assert any("SUPPRESSED by assertion" in c for c in out["caveats"])


def test_suppressing_an_entity_that_was_never_a_twin_node_is_accounting_only(tmp_path):
    """An external peer is not in `placed`, so nothing changes in the emitted documents — and it
    still appears in `population.suppressed`, flagged `placed: false`, so the identity is not
    quietly broken by a record that removed nothing."""
    base = _compiled(tmp_path)
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "b.duckdb"), assertions=_assertions(
        suppress=[{"key": "address:203.0.113.9", "note": "out of scope: a CDN edge"}]))
    assert to_yaml(base["twin_config"]) == to_yaml(out["twin_config"])
    pop = out["provenance"]["population"]
    assert pop["suppressed"] == [{"key": "address:203.0.113.9",
                                  "note": "out of scope: a CDN edge",
                                  "placed": False, "matched": True}]
    assert pop["placed_hosts"] == sum(g["observed"] for g in pop["node_groups"])


def test_a_suppression_matching_nothing_is_reported_not_refused(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        suppress=[{"key": "address:10.9.9.9", "note": "gone since last month"}]))
    pop = out["provenance"]["population"]
    assert pop["suppressed"][0]["matched"] is False
    assert [r["kind"] for r in pop["assertions"]["ineffective"]] == ["suppress"]


def test_an_override_on_an_entity_that_is_not_a_twin_node_is_reported(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        override=[{"key": "address:203.0.113.9", "field": "role", "value": "web-tier",
                   "note": "n"}]))
    report = out["provenance"]["population"]["assertions"]["ineffective"]
    assert report == [{"kind": "override:host", "key": "address:203.0.113.9", "field": "role",
                       "reason": report[0]["reason"]}]
    assert "external" in report[0]["reason"]


# -- drift (D17) ------------------------------------------------------------------

def _later_pit(pit, after):
    """The same PIT, observed AFTER `after` (an `AssertionSet`) was written.

    Derived from the file's own `as_of` rather than by adding a fixed offset to the fixture's
    epoch: the two live years apart (`T0` is 2025-08, the fixture stamp is 2026-09), so a
    constant would have to be re-tuned every time either moved.
    """
    row = dict(pit["ir_pit"][0])
    row["observed_to"] = after.as_of_epoch + 86_400
    return {**pit, "ir_pit": [row]}


def test_a_later_pit_contradicting_a_standing_assertion_is_flagged_and_still_loses(tmp_path):
    """D17, flag only. The assertion keeps winning, both readings stay in the artifact, and a
    caveat names it — data never changes precedence."""
    aset = _assertions(override=[{"key": "address:10.0.1.20", "field": "role",
                                  "value": "web-tier", "note": "n"}])
    pit = _later_pit(_pit(tmp_path, _TWO_SEGMENTS), aset)
    # The file is dated before this PIT's last observation, which is what makes it "standing".
    assert aset.as_of_epoch < pit["ir_pit"][0]["observed_to"]
    out = compile_twin(pit, assertions=aset)
    fields = out["provenance"]["fields"]
    path = next(k for k, v in fields.items()
                if k.endswith(".role") and v["evidence_class"] == "asserted")
    assert fields[path]["overrides"]["drift"] == {
        "class": "imputed", "value": "domain-controller",
        "pit_id": pit["ir_pit"][0]["pit_id"]}
    assert any("ASSERTION DRIFT" in c for c in out["caveats"])


def test_drift_does_not_fire_on_the_very_pit_the_assertion_was_written_against(tmp_path):
    """Without the "later" clause the flag fires on EVERY override — an operator asserts a role
    precisely because this PIT's census said something else, so the contradiction the assertion
    exists to fix would be reported as evidence it had gone stale. A flag that is always on says
    nothing. Measured on the CorpAPCN cast before the clause landed."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        override=[{"key": "address:10.0.1.20", "field": "role", "value": "web-tier",
                   "note": "n"}]))
    fields = out["provenance"]["fields"]
    path = next(k for k, v in fields.items()
                if k.endswith(".role") and v["evidence_class"] == "asserted")
    assert "drift" not in fields[path]["overrides"]
    assert not any("ASSERTION DRIFT" in c for c in out["caveats"])


def test_drift_does_not_fire_when_the_later_pit_agrees(tmp_path):
    """An `overrides` whose value AGREES with the winner is not drift, which keeps the flag
    meaning "the wire now says something else"."""
    aset = _assertions(override=[{"key": "address:10.0.1.20", "field": "role",
                                  "value": "domain-controller",
                                  "note": "confirming the census"}])
    out = compile_twin(_later_pit(_pit(tmp_path, _TWO_SEGMENTS), aset), assertions=aset)
    fields = out["provenance"]["fields"]
    path = next(k for k, v in fields.items()
                if k.endswith(".role") and v["evidence_class"] == "asserted")
    assert "drift" not in fields[path].get("overrides", {})


def test_a_role_already_asserted_in_the_pit_is_not_counted_as_a_second_reading(tmp_path):
    """An assertion can reach a cast through two doors — the file, and a belief store that
    already ingested it. `resolve_precedence` refuses two facts of one class, correctly, so the
    duplicate has to be removed where the two meet rather than surface as a crash."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    for e in pit["ir_entity"]:
        if e["entity_key"] == "address:10.0.1.20":
            e["role"], e["role_evidence_class"] = "web-tier", "asserted"
    out = compile_twin(pit, assertions=_assertions(
        override=[{"key": "address:10.0.1.20", "field": "role", "value": "web-tier",
                   "note": "n"}]))
    fields = out["provenance"]["fields"]
    path = next(k for k, v in fields.items()
                if k.endswith(".role") and v["evidence_class"] == "asserted")
    assert "overrides" not in fields[path], "one fact reaching through two doors is one fact"


def test_the_role_class_is_read_from_the_pit_not_assumed_to_be_imputed(tmp_path):
    """D4 one layer on. A role the belief store carries as `asserted` must not be republished as
    `imputed` just because a cast was compiled without a file — the value would be right and the
    class wrong, which is invisible at the consumer."""
    pit = _pit(tmp_path, _TWO_SEGMENTS)
    for e in pit["ir_entity"]:
        if e["entity_key"] == "address:10.0.1.20":
            e["role_evidence_class"] = "asserted"
    out = compile_twin(pit)
    fields = out["provenance"]["fields"]
    dc = next(k for k, v in fields.items()
              if k.endswith(".role") and v.get("signal", "").startswith("ir_entity"))
    assert fields[dc]["evidence_class"] == "asserted"


def test_two_node_groups_can_never_compile_to_one_name(tmp_path):
    """A positive gate on the property every downstream reference depends on: `net:`, a `tfc`
    assignment and a tap binding all address a node BY NAME, so a duplicate is an ambiguous
    document rather than an untidy one."""
    out = _compiled(tmp_path)
    names = [n["name"] for n in _tc(out)["nodes"]]
    assert len(names) == len(set(names))


def test_an_asserted_segment_placement_is_not_published_as_observed(tmp_path):
    """The easiest masquerade to ship by accident: the group key already carries the effective
    segment, so a `.net` row's VALUE is right while its class describes where the value came from
    BEFORE the override. Nothing in the emitted twin config shows it — only the sidecar does, and
    only if you ask for that row."""
    aset = _assertions(
        add=[{"kind": "segment", "key": "10.0.9.0/24", "name": "seg-ot", "note": "the OT lab"}],
        override=[{"key": "address:10.0.1.20", "field": "segment", "value": "10.0.9.0/24",
                   "note": "actually on the OT VLAN"}])
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=aset)
    node = next(n for n in _tc(out)["nodes"] if n.get("net") == "seg-ot")
    idx = _tc(out)["nodes"].index(node)
    row = out["provenance"]["fields"][f"twin_config.nodes[{idx}].net"]
    assert row["evidence_class"] == "asserted" and row["confidence"] == 1.0
    # The census's own 0.95 belongs to the reading that LOST, not to the assertion.
    assert row["overrides"] == {"class": "observed", "value": "10.0.1.0/24", "confidence": 0.95}


def test_a_later_pit_still_placing_the_host_elsewhere_flags_segment_drift(tmp_path):
    aset = _assertions(
        add=[{"kind": "segment", "key": "10.0.9.0/24", "name": "seg-ot", "note": "the OT lab"}],
        override=[{"key": "address:10.0.1.20", "field": "segment", "value": "10.0.9.0/24",
                   "note": "actually on the OT VLAN"}])
    out = compile_twin(_later_pit(_pit(tmp_path, _TWO_SEGMENTS), aset), assertions=aset)
    node = next(n for n in _tc(out)["nodes"] if n.get("net") == "seg-ot")
    idx = _tc(out)["nodes"].index(node)
    drift = out["provenance"]["fields"][f"twin_config.nodes[{idx}].net"]["overrides"]["drift"]
    assert drift["class"] == "observed" and drift["value"] == "10.0.1.0/24"
    assert any("ASSERTION DRIFT" in c for c in out["caveats"])


# =============================================================================================
# Real protocol servers: the declared listen set, and a real DC
# (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` S2/S3, D1(a) + D2(b) + D4)
# =============================================================================================

def _rekey(pit, old, new):
    """Re-key one entity across the PIT, so a fixture can carry a HOSTNAME-keyed host.

    The natural key an entity gets is the entity resolver's business and depends on identity
    evidence a synthetic flow fixture does not carry. Re-keying the finished PIT is the smallest
    honest way to test what the compiler does with a hostname-keyed host, without asserting
    anything about how one is minted.
    """
    for e in pit["ir_entity"]:
        if e["entity_key"] == old:
            e["entity_key"] = new
            e["key_kind"] = new.split(":", 1)[0]
    for ed in pit["ir_edge"]:
        if ed["src_key"] == old:
            ed["src_key"] = new
        if ed["dst_key"] == old:
            ed["dst_key"] = new
    return pit


def test_a_node_declares_the_listen_set_it_was_OBSERVED_answering_on(tmp_path):
    """D2(b), and §2's ruling underneath it: the declared set is the observed listen census,
    NEVER the persona dial set. The echo responder's whole defect was taking the other one."""
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    dc = nodes["domain-controller-10-0-1-core"]
    # 445 is here because the fixture's `.12` host talks SMB to it — a DC serves SMB too, and
    # the declared set follows what was ACCEPTED rather than what the role's rule matched on.
    assert dc["listen"] == [{"port": 88, "proto": "tcp"}, {"port": 389, "proto": "tcp"},
                            {"port": 445, "proto": "tcp"}]
    fs = nodes["file-server-10-0-2-core"]
    assert fs["listen"] == [{"port": 445, "proto": "tcp"}]


def test_a_node_that_answered_nothing_declares_no_listen_key_at_all(tmp_path):
    """Absent, not empty: a host observed answering nothing and a host with an empty declared
    set are the same thing here, and one shape is enough."""
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    roleless = [n for n in nodes.values() if n.get("role") == UNROLED_ROLE]
    assert roleless and all("listen" not in n for n in roleless)


def test_the_listen_set_is_the_UNION_of_a_groups_members(tmp_path):
    """A group is `count` identical clones, so a union across its members is a statement about
    one population. A union across a ROLE would not be — which is why the recipe is per group."""
    pit = _pit(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 20, "10.0.1.10", "10.0.1.31", 445),
        _flow(W1 + 21, "10.0.1.10", "10.0.1.32", 139),
    ])
    nodes = {n["name"]: n for n in _tc(compile_twin(pit))["nodes"]}
    fs = nodes["file-server-10-0-1-core"]
    ports = {(e["port"], e["proto"]) for e in fs["listen"]}
    assert (445, "tcp") in ports and (139, "tcp") in ports


def test_the_listen_set_is_published_as_observed_in_the_sidecar(tmp_path):
    out = _compiled(tmp_path)
    rows = {k: v for k, v in out["provenance"]["fields"].items() if k.endswith(".listen")}
    assert rows, "a declared listen set with no provenance row is an unattributed claim"
    for row in rows.values():
        assert row["evidence_class"] == "observed"
        assert row["signal"] == "ir_entity.listen_ports"
        assert "never the persona dial set" in row["reason"].lower() or \
               "NOT the persona dial set" in row["reason"]


def test_a_domain_controller_promotes_a_REAL_domain(tmp_path):
    """D1(a), ruled by the user. `install: [{role: adds}]` comes from the catalog; `provides:`
    is emitted beside it here, because axRange errors on a promotion with no domain."""
    from aximprint.synth.services import LAB_REALM
    nodes = {n["name"]: n for n in _tc(_compiled(tmp_path))["nodes"]}
    dc = nodes["domain-controller-10-0-1-core"]
    assert dc["install"] == [{"role": "adds"}]
    assert dc["provides"] == [{"domain": LAB_REALM}]


def test_the_forest_name_is_the_LAB_realm_and_is_declared_compiler_class(tmp_path):
    """D4: the observed realm is deliberately NOT reproduced. A `compiler` row, not an
    `imputed` one — nothing inferred it; the platform chose it."""
    out = _compiled(tmp_path)
    row = next(v for k, v in out["provenance"]["fields"].items() if k.endswith(".provides"))
    assert row["evidence_class"] == COMPILER_CLASS
    assert "corp.axr.lab" in row["reason"]


def test_a_domain_bearing_twin_carries_an_IMPUTED_lab_user_population(tmp_path):
    """§5.3's honest limit: this source's wire carries two principals, one of them `Guest`, and
    no credentials at all. The users are lab defaults chosen to make the domain EXERCISABLE."""
    from aximprint.synth.services import LAB_REALM
    out = _compiled(tmp_path)
    identity = out["twin_config"]["twin_config"]["identity"]
    assert identity["domain"] == LAB_REALM
    ids = {u["id"] for u in identity["users"]}
    assert ids == {"alice", "bob-admin", "svc-sql", "jsmith"}
    # The two that make it attackable rather than decorative.
    assert any(u.get("spn") for u in identity["users"])
    assert any(u.get("asrep_roastable") for u in identity["users"])
    row = out["provenance"]["fields"]["twin_config.identity"]
    assert row["evidence_class"] == "imputed"


def test_windows_workstations_join_the_domain_the_twin_stands_up(tmp_path):
    from aximprint.synth.services import LAB_REALM
    pit = _pit(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 30, "10.0.1.10", "10.0.1.40", 3389),   # -> corp-workstation (Windows image)
    ])
    nodes = {n["name"]: n for n in _tc(compile_twin(pit))["nodes"]}
    ws = nodes["corp-workstation-10-0-1-core"]
    assert ws["joins"] == LAB_REALM
    assert "win" in ws["image"]


def test_nothing_joins_and_no_identity_is_emitted_when_no_node_promotes_a_domain(tmp_path):
    """A `joins:` against a forest nothing stands up is a deploy that fails minutes in, at the
    join, rather than at validation."""
    pit = _pit(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.40", 3389),    # a workstation, and no DC anywhere
        _flow(W1 + 2, "10.0.1.11", "10.0.1.40", 3389),
    ])
    out = compile_twin(pit)
    assert "identity" not in out["twin_config"]["twin_config"]
    assert not any(n.get("joins") for n in _tc(out)["nodes"])


def test_the_dc_keeps_its_OBSERVED_short_hostname_where_the_wire_gave_one(tmp_path):
    """D4's implementation reading. axRange sets a guest's hostname from its node NAME, and a
    domain controller's hostname is on the wire in every Kerberos, LDAP and SMB frame the twin
    emits — the exact strings a detection rule reads."""
    pit = _rekey(_pit(tmp_path, _TWO_SEGMENTS), "address:10.0.1.20", "hostname:axdc01")
    names = {n["name"] for n in _tc(compile_twin(pit))["nodes"]}
    assert "axdc01" in names
    assert "domain-controller-10-0-1-core" not in names


def test_the_hostname_rename_is_scoped_to_the_dc_and_leaves_every_other_group_alone(tmp_path):
    """A node name is addressed by `net:`, by every `tfc` assignment and by the installer's
    instance expansion, so the rename is bought for one stated reason and not applied broadly."""
    pit = _rekey(_pit(tmp_path, _TWO_SEGMENTS), "address:10.0.2.30", "hostname:fileserver01")
    names = {n["name"] for n in _tc(compile_twin(pit))["nodes"]}
    assert "fileserver01" not in names
    assert "file-server-10-0-2-core" in names


def test_the_cast_stays_byte_identical_across_two_compiles_with_the_new_blocks(tmp_path):
    pit = _rekey(_pit(tmp_path, _TWO_SEGMENTS), "address:10.0.1.20", "hostname:axdc01")
    assert to_yaml(compile_twin(pit)) == to_yaml(compile_twin(pit))


def test_an_ASSERTED_hosts_listen_set_reaches_the_node_as_asserted_not_observed(tmp_path):
    """An `add: host` record may carry `listen:` (reference-ingest S2), and that set must not
    reach the twin wearing the class of a capture that never saw the host — the masquerade the
    whole evidence model exists to prevent."""
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(add=[
        {"kind": "host", "key": "address:10.0.1.99", "segment": "10.0.1.0/24",
         "role": "file-server", "listen": [{"port": 445, "proto": "tcp"},
                                           {"port": 139, "proto": "tcp"}],
         "note": "a host north of the tap"}]))
    node = next(n for n in _tc(out)["nodes"] if n["name"].endswith("-asserted"))
    assert node["listen"] == [{"port": 139, "proto": "tcp"}, {"port": 445, "proto": "tcp"}]
    idx = _tc(out)["nodes"].index(node)
    row = out["provenance"]["fields"][f"twin_config.nodes[{idx}].listen"]
    assert row["evidence_class"] == "asserted"
    assert row["ports"] == ["139/tcp", "445/tcp"]


# -- _node_suffix collision fix (2026-09-08) -------------------------------------

def test_node_suffix_drops_whole_zero_octets_not_a_character_run():
    """`rstrip('.0')` ate individual chars, collapsing a round third octet onto a shorter sibling
    (`10.20.200.0` -> `10-20-2`, indistinguishable from `10.20.2.0`). Octet-wise stripping keeps
    them apart while leaving the common non-round case untouched."""
    from aximprint.synth.compiler import _node_suffix

    assert _node_suffix("10.20.200.0/24") == "10-20-200"
    assert _node_suffix("10.80.20.0/24") == "10-80-20"
    assert _node_suffix("10.20.2.0/24") == "10-20-2"
    assert _node_suffix("10.20.200.0/24") != _node_suffix("10.20.2.0/24")
    # the common, non-round cases are UNCHANGED (no rename churn for them)
    assert _node_suffix("10.80.72.0/24") == "10-80-72"
    assert _node_suffix("192.168.0.0/24") == "192-168"
    assert _node_suffix("10.0.1.0/24") == "10-0-1"


def test_node_suffix_is_collision_safe_under_axrange_count_fan_out():
    """The reported blocker, reproduced at the naming layer (`LES-031`: verify by mutation). Even
    WITHOUT the golden rung suffix, a `.200` node fanned out by axRange's separator-less
    `f'{name}{i:02d}'` must not collide with the singleton `.201`/`.204` segments — which the old
    `10-20-2` suffix did (`...-201`, `...-204`)."""
    from aximprint.synth.compiler import _node_suffix

    def fan(name, count):
        return [name] if count == 1 else [f"{name}{i:02d}" for i in range(1, count + 1)]

    ids: list[str] = []
    for cidr, count in [("10.20.200.0/24", 4), ("10.20.201.0/24", 1), ("10.20.204.0/24", 1)]:
        ids += fan(f"generic-endpoint-heavy-{_node_suffix(cidr)}", count)
    assert len(ids) == len(set(ids)), sorted(ids)


def test_a_second_domain_controller_on_one_segment_is_clamped_to_one_forest_root(tmp_path):
    """axRange promotes a NEW AD DS forest per `role: adds` host and emits one
    `domain-ready[<fqdn>]` barrier for each, so a domain-controller group with count>1 collides on
    that op and the plan is refused (`axrange/planner/pass1_emit.py`). A forest has ONE root, so a
    same-realm promoter group deploys count 1 — with the observed count kept in the sidecar and a
    caveat, never silently dropped (`LES-031`: verify by mutation — a bare `_TWO_SEGMENTS` has one
    DC and needs no clamp; adding a SECOND on the same segment is what triggers it)."""
    from aximprint.synth.services import LAB_REALM

    out = _compiled(tmp_path, _TWO_SEGMENTS + [
        _flow(W1 + 3, "10.0.1.10", "10.0.1.30", 88),
        _flow(W1 + 4, "10.0.1.10", "10.0.1.30", 389)])   # a SECOND DC on 10.0.1.0/24
    dcs = [n for n in _real_nodes(out) if n.get("role") == "domain-controller"]
    assert len(dcs) == 1, [n["name"] for n in dcs]
    dc = dcs[0]
    assert "count" not in dc, "a forest root deploys as a single instance, never count>1"
    assert dc.get("provides") == [{"domain": LAB_REALM}]
    prow = next(p for p in out["provenance"]["population"]["node_groups"]
                if p["node"] == dc["name"])
    assert prow["observed"] == 2 and prow["emitted"] == 1
    assert prow["capped_by"] == "single_forest_root"
    assert any("forest root" in c and dc["name"] in c for c in out["caveats"])


# -- the count row names the group's observed members ---------------------------

def test_the_count_row_names_each_groups_observed_entity_keys(tmp_path):
    """A twin-config node is a GROUP named `role-band-segment-rung`, which the assertions seam
    cannot address: its keys are the IR's `<key_kind>:<value>` entity keys (D10). The sidecar's
    `count` row therefore carries `members`, the sorted entity keys the group collapsed, so a
    consumer (the design canvas's promote-to-assertion) can assert on one host of a group by
    the key the compiler joins on. The members partition the placed set: every observed node's
    members are placed entities, no key sits in two groups, and the count is their number."""
    out = _compiled(tmp_path)
    tc = _tc(out)
    fields = out["provenance"]["fields"]
    seen: set[str] = set()
    observed_rows = 0
    for i, node in enumerate(tc["nodes"]):
        row = fields.get(f"twin_config.nodes[{i}].count")
        if row is None:
            # A router or a synthetic sink group has no observed count row at all.
            assert "observation" not in node, f"nodes[{i}]: an observed group has a count row"
            continue
        members = row["members"]
        assert members == sorted(members), f"nodes[{i}]: members are sorted"
        assert len(members) == row["hosts_observed"], f"nodes[{i}]: one key per observed host"
        assert all(m.split(":", 1)[0] in {"address", "hostname", "mac"} for m in members), members
        assert not (set(members) & seen), f"nodes[{i}]: a key in two groups"
        seen.update(members)
        if "observation" in node:
            observed_rows += 1
            assert members, f"nodes[{i}]: an observed group has members"
        else:
            assert members == [], f"nodes[{i}]: a synthetic group observed no one"
    assert observed_rows, "the fixture must place at least one observed group"
