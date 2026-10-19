"""Role inference and the catalog behind it (`docs/WORK_ORDER_synth_v1.md` phase 2).

Synthetic events only (`LES-003`). Two things are being defended here and they pull in
opposite directions: that a role IS inferred where evidence supports one, and that no role is
invented where it does not — `role: null` reaching the twin is a correct output, not a gap.
"""
import textwrap

import pytest

from aximprint.catalog import ROLE_CATALOG, CatalogError, match_role
from aximprint.catalog.roles import RoleRule, _load
from aximprint.events import FlowEvent
from aximprint.learners import extract_assertions
from aximprint.store.belief_store import BeliefStore
from aximprint.windowing import WindowManager

T0 = 1_754_000_000.0
WINDOW = 3600
ALIGNED = T0 - (T0 % WINDOW)


def _serves(ts, host, port, proto="tcp"):
    """A flow the responder ACCEPTED — which is what proves a listener."""
    return FlowEvent(ts=ts, src_ip="10.80.99.9", dst_ip=host, src_port=51000,
                     dst_port=port, proto=proto, service="", duration=1.0,
                     bytes_out=100, bytes_in=200, pkts_out=2, pkts_in=2,
                     state="SF", state_class="established",
                     local_src=True, local_dst=True)


def _one_window(events):
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == 1
    return wins[0]


def _roles(win):
    return {a["subject"]: a for a in extract_assertions(win) if a["predicate"] == "has_role"}


# -- the catalog file -----------------------------------------------------------

def test_the_shipped_catalog_loads():
    assert ROLE_CATALOG, "the shipped catalog must parse — it is loaded at import"


def test_rules_are_ordered_most_specific_first():
    """What makes overlap safe rather than ambiguous."""
    sizes = [len(r.requires) for r in ROLE_CATALOG]
    assert sizes == sorted(sizes, reverse=True)


def test_every_rule_names_an_image_and_a_confidence_in_range():
    for r in ROLE_CATALOG:
        assert r.image, f"{r.role}: a twin_config node cannot omit `image`"
        assert 0 < r.confidence <= 1


def test_a_role_may_legitimately_have_no_persona():
    """`dns-resolver` is the measured case: axRange ships no such persona, and its own
    `corp-workstation` persona marks that peer `optional:` for exactly this reason."""
    by_role = {r.role: r for r in ROLE_CATALOG}
    assert by_role["dns-resolver"].persona is None
    assert by_role["domain-controller"].persona == "domain-controller"


def test_every_persona_declares_a_cadence_and_a_role_without_one_declares_neither():
    """D12's input. A cadence for a persona that does not exist would be a number about
    nothing; a persona with no cadence would silently never warn."""
    for r in ROLE_CATALOG:
        assert (r.persona is None) == (r.persona_cadence is None), r.role
        assert (r.persona is None) == (r.min_capture_seconds is None), r.role


def test_the_cadences_match_axranges_own_catalog_as_measured():
    """Copied from `axrange/personas/catalog/*.yaml` 2026-08-17 — the LONGEST emit cadence per
    persona, which is what bounds how short a capture can be and still have seen it. Pinned as
    values rather than re-derived: aximprint must not import axrange (`LES-111`), so drift
    against their catalog is caught by a human re-measuring, and this test at least catches a
    drift in OUR copy."""
    by_role = {r.role: r for r in ROLE_CATALOG}
    assert by_role["corp-workstation"].min_capture_seconds == 86_400   # diurnal
    assert by_role["file-server"].min_capture_seconds == 86_400        # diurnal
    assert by_role["domain-controller"].min_capture_seconds == 900     # cyclic:15m
    assert by_role["bastion-jump"].min_capture_seconds == 3_600        # cyclic:1h
    assert by_role["web-tier"].min_capture_seconds == 0                # 24x7 only
    assert by_role["dns-resolver"].min_capture_seconds is None         # no persona


# -- matching -------------------------------------------------------------------

def test_a_domain_controller_beats_file_server_on_the_same_host():
    """A DC serves 445 too. Specificity ordering is what stops it being demoted."""
    dc = {"88/tcp", "389/tcp", "445/tcp", "53/udp", "135/tcp"}
    assert match_role(dc).role == "domain-controller"


def test_each_measured_signature_from_the_real_baseline_still_matches():
    """Signatures taken verbatim from the 2026-08-11 capture (ports only — `LES-003`)."""
    cases = {
        ("88/tcp", "389/tcp", "445/tcp", "53/tcp", "53/udp", "3268/tcp"): "domain-controller",
        ("135/tcp", "137/udp", "3389/tcp"): "corp-workstation",
        ("111/udp", "40780/udp", "445/tcp"): "file-server",
        ("53/udp",): "dns-resolver",
        ("123/udp", "53/udp", "67/udp"): "dns-resolver",
        ("443/tcp",): "web-tier",
        ("80/tcp",): "web-tier",
    }
    for ports, expected in cases.items():
        got = match_role(set(ports))
        assert got is not None and got.role == expected, f"{sorted(ports)} -> {got}"


@pytest.mark.parametrize("ports", [
    ("5353/udp",),          # mDNS: printers, Macs, IoT — not a role
    ("9358/udp",),          # unassigned
    # 8006/tcp USED to sit here as "the hypervisor's own web UI — not a role". It is now the
    # `hypervisor` rule's own signature (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C2): on a real
    # capture, a Proxmox management endpoint is exactly the kind of host a twin should carry.
    # Replaced with a port that genuinely names nothing, so this test keeps testing what it says.
    ("8384/tcp",),          # Syncthing GUI — no role claims it
    ("137/udp",),           # NetBIOS name service alone
    (),                     # no listen evidence at all
])
def test_ports_that_name_no_role_yield_none_rather_than_a_guess(ports):
    """`None` is a supported outcome. `twin_config.role` is Optional, and inventing one
    would trade a declared gap for a fabricated fact."""
    assert match_role(set(ports)) is None


def test_matching_is_a_subset_test_not_an_equality_test():
    """A real server has a dozen ports; no fixed signature survives one."""
    assert match_role({"445/tcp"}).role == "file-server"
    assert match_role({"445/tcp", "1234/tcp", "9999/udp"}).role == "file-server"


# -- D1b: what decides the sink's role (`docs/WORK_ORDER_proto_weight_rows.md` §4) ---------
#
# Ruled 2026-09-02 (Authority: user): D1 = (a) + (b), D2 = no seam. On the single-port
# specificity tie between `53/udp` and `443/tcp` declaration order decides, and the `443/tcp`
# `web-tier` row is declared first. Applied to `ROLE_CATALOG` itself -- the sink is re-inferred
# through the ordinary catalog like any other host -- never to a sink-only rule.

def _index_of(role, requires):
    return next(i for i, r in enumerate(ROLE_CATALOG)
                if r.role == role and r.requires == frozenset(requires))


def test_web_tier_443_is_declared_before_dns_resolver_and_80_stays_behind():
    """The pin. Both rows demand one port, so nothing but declaration order separates them,
    and a re-sort of the file by role would silently hand the sink back to `dns-resolver`.
    The `80/tcp` row is deliberately NOT moved: cleartext HTTP is weaker evidence than TLS,
    and only the 53/443 tie was ruled on."""
    web443 = _index_of("web-tier", ["443/tcp"])
    dns = _index_of("dns-resolver", ["53/udp"])
    web80 = _index_of("web-tier", ["80/tcp"])
    assert web443 < dns < web80, (web443, dns, web80)
    # The specificity ordering itself is untouched: all three are single-port rows, so the
    # move changed a tiebreak and nothing above it.
    assert {len(ROLE_CATALOG[i].requires) for i in (web443, dns, web80)} == {1}


def test_a_sink_shaped_listen_set_is_web_tier_not_dns_resolver():
    """The measured defect. The cast's internet sink answers on exactly the ports the twin's
    personas dial at it (`synth/responder.py::listener_ports`) -- 53 among them, because the
    source's DNS does leave the network. On presence alone `dns-resolver` claimed it ahead of
    `web-tier` and relabelled every edge landing on it (iteration 17, `generic-endpoint->
    web-tier` 315 -> 3), against a source 202 of whose 204 `web-tier` entities are external.
    Volume never enters `match_role`; declaration order is the only lever, and it now says
    web-tier."""
    assert match_role({"53/udp", "443/tcp", "80/tcp", "123/udp"}).role == "web-tier"


def test_the_bare_53_443_tie_goes_to_web_tier_at_lower_confidence():
    """The accepted wrinkle: on this tie the `web-tier` row (0.6 today) out-ranks the
    `dns-resolver` row (0.7 today). Confidence is what a row asserts about the evidence once
    it has matched; it has never been the tiebreak, and a test that "fixed" this by making it
    one would hand the sink back to `dns-resolver`. Only the ROLE is asserted: the two
    confidence values are content a retune may legitimately change, and the wrinkle itself is
    recorded in the row's own comment in `roles.yaml`."""
    assert match_role({"53/udp", "443/tcp"}).role == "web-tier"


def test_a_udp_only_port_53_listener_is_still_a_dns_resolver():
    """(b) reorders; it does not widen. A resolver that answers on 53/udp alone -- both of the
    2026-08-11 baseline's resolver signatures -- keeps its role; option (c), which would have
    required a second port, was NOT taken."""
    assert match_role({"53/udp"}).role == "dns-resolver"
    assert match_role({"123/udp", "53/udp", "67/udp"}).role == "dns-resolver"


def test_a_domain_controller_that_also_serves_dns_and_tls_stays_a_domain_controller():
    """The two-port band is untouched by a single-port tiebreak: a DC serving DNS on both
    transports, and even TLS, is still the DC, because specificity is decided before
    declaration order ever is."""
    dc = {"53/udp", "53/tcp", "88/tcp", "389/tcp"}
    assert match_role(dc).role == "domain-controller"
    assert match_role(dc | {"443/tcp"}).role == "domain-controller"


# -- assertions -----------------------------------------------------------------

def test_a_role_is_asserted_as_imputed_never_observed():
    """The central provenance call of this phase. Nobody observed "this is a DC"; what was
    observed is 88 and 389, and the role is this project's inference from that."""
    win = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 88),
                       _serves(ALIGNED + 2, "10.80.20.5", 389)])
    role = _roles(win)["10.80.20.5"]
    assert role["object"] == "domain-controller"
    assert role["evidence_class"] == "imputed"
    assert role["confidence"] == 0.9
    assert role["source"] == "extractor:role-census"


def test_the_listen_evidence_under_it_stays_observed():
    """The two must not be levelled: one is the observation, one is the inference."""
    win = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 88)])
    classes = {a["predicate"]: a["evidence_class"] for a in extract_assertions(win)}
    assert classes["listens_on"] == "observed"


def test_a_host_with_no_matching_ports_gets_no_role_assertion():
    win = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 5353, proto="udp")])
    # Scoped to the SERVER under test. Serving mDNS still names no role — that is the claim.
    # `_serves`'s synthetic client is a different matter: since the CorpAPCN work it dials 5353
    # and is a `generic-endpoint`, which is intended (`docs/JOURNAL_corpapcn_fidelity.md` C3).
    assert "10.80.20.5" not in _roles(win)


def test_at_most_one_role_per_entity_per_window():
    """A host has one role. Several rules may match; only the most specific fires."""
    win = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 88),
                       _serves(ALIGNED + 2, "10.80.20.5", 389),
                       _serves(ALIGNED + 3, "10.80.20.5", 445),
                       _serves(ALIGNED + 4, "10.80.20.5", 443)])
    # Scoped to the entity under test: since GAP-L5 the synthetic CLIENT in `_serves` also
    # earns a role (it dials 88), which is the intended new behavior. The invariant this test
    # guards is the one its docstring states — one role per entity, most specific wins.
    roles = [a for a in extract_assertions(win)
             if a["predicate"] == "has_role" and a["subject"] == "10.80.20.5"]
    assert [a["object"] for a in roles] == ["domain-controller"]


# -- the snapshot the compiler reads --------------------------------------------

def test_the_snapshot_carries_one_role_per_entity(tmp_path):
    """One row per (entity, CLASS) — with only the census producing `has_role`, that is one row
    per entity, and the fourth column is `imputed` on every one of them.

    The fourth column landed 2026-09-05 (`docs/WORK_ORDER_reference_ingest.md` D4): the reference
    ingest is the second producer of this predicate and writes `asserted`, so a shape that could
    not carry a class would have published an operator's assertion as an inference.
    """
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    win = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 88),
                       _serves(ALIGNED + 2, "10.80.20.5", 389),
                       _serves(ALIGNED + 3, "10.80.72.9", 445)])
    store.write_window(win, extract_assertions(win))
    snap = store.snapshot()
    # `_serves`'s synthetic client dials 88 and is a corp-workstation since GAP-L5; the two
    # SERVER rows are what this test is about and are asserted exactly.
    assert [r for r in snap["roles"] if r[0] in ("10.80.20.5", "10.80.72.9")] == [
        ("10.80.20.5", "domain-controller", 0.9, "imputed"),
        ("10.80.72.9", "file-server", 0.7, "imputed")]


def test_the_strongest_role_survives_a_quiet_window(tmp_path):
    """Absence of evidence in one window is not evidence the host changed jobs."""
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    strong = _one_window([_serves(ALIGNED + 1, "10.80.20.5", 88),
                          _serves(ALIGNED + 2, "10.80.20.5", 389)])
    store.write_window(strong, extract_assertions(strong))
    weak = _one_window([_serves(ALIGNED + WINDOW + 1, "10.80.20.5", 443)])
    store.write_window(weak, extract_assertions(weak))
    assert [r for r in store.snapshot()["roles"] if r[0] == "10.80.20.5"] == [
        ("10.80.20.5", "domain-controller", 0.9, "imputed")]


# -- the loader refuses what it cannot trust ------------------------------------
#
# A catalog is meant to be hand-edited, so every one of these is a plausible typo, and every
# one of them would otherwise produce a rule that silently matches wrongly or never at all.

def _write(tmp_path, body):
    p = tmp_path / "roles.yaml"
    p.write_text(textwrap.dedent(body))
    return p


_GOOD = """\
    roles:
      - role: r
        persona: p
        persona_cadence: diurnal
        requires: ["445/tcp"]
        confidence: 0.5
        measured_on_baseline: false
        image: img@v1
"""


def test_the_test_fixture_itself_is_valid(tmp_path):
    """Or every rejection test below would pass for the wrong reason."""
    assert len(_load(_write(tmp_path, _GOOD))) == 1


@pytest.mark.parametrize("bad,match", [
    (_GOOD.replace("requires:", "require:"), "unrecognized key"),
    (_GOOD.replace("        persona: p\n", ""), "missing required key"),
    # GAP-L5: a row with neither `requires` nor `dials` carries no evidence and would
    # match every host. Refused — the message now names both keys, since either satisfies it.
    (_GOOD.replace('["445/tcp"]', "[]"), "must declare evidence"),
    (_GOOD.replace('["445/tcp"]', '["445"]'), "must be '<port>/<proto>'"),
    (_GOOD.replace('["445/tcp"]', '["445/TCP"]'), "lowercase"),
    (_GOOD.replace('["445/tcp"]', '["smb/tcp"]'), "numeric"),
    (_GOOD.replace("confidence: 0.5", "confidence: 1.5"), "in \\(0, 1]"),
    (_GOOD.replace("confidence: 0.5", "confidence: true"), "in \\(0, 1]"),
    (_GOOD.replace("measured_on_baseline: false", "measured_on_baseline: nope"), "must be a bool"),
    # D12: a cadence this build cannot parse must fail to LOAD. Read as "0 seconds needed" it
    # would silently disable the span warning for exactly the persona somebody just re-spelled.
    (_GOOD.replace("persona_cadence: diurnal", "persona_cadence: every-day"), "persona_cadence"),
    (_GOOD.replace("persona_cadence: diurnal", 'persona_cadence: "cyclic:2H"'), "persona_cadence"),
    (_GOOD.replace("persona_cadence: diurnal", "persona_cadence: null"), "null together"),
    (_GOOD.replace("        persona_cadence: diurnal\n", ""), "missing required key"),
    ("nonsense: 1\n", "expected a top-level"),
])
def test_a_catalog_that_cannot_be_trusted_is_refused(tmp_path, bad, match):
    with pytest.raises(CatalogError, match=match):
        _load(_write(tmp_path, bad))


def test_a_null_persona_is_accepted_but_an_absent_one_is_not():
    """The distinction the loader exists to hold: "axRange has no persona for this role" is a
    fact; "nobody filled this in" is a defect (`LES-006`)."""
    assert RoleRule("r", None, None, None, frozenset({"1/tcp"}), frozenset(), 0.5,
                    False, "i", {}, ()).persona is None


def test_a_missing_catalog_file_fails_loud(tmp_path):
    with pytest.raises(CatalogError, match="unreadable"):
        _load(tmp_path / "does-not-exist.yaml")


# -- the `default:` block, and the role it now confers -----------------------------------
#
# `docs/WORK_ORDER_deployment_scope.md` D4 (Authority: user). The block used to be content only
# and a role-less host reached the twin with `role:` absent. Since the cast emits every placed
# host, a role-less population needs a name to group and deploy under.

_GOOD_DEFAULT = """\
    default:
      role: unroled-endpoint
      image: img@v1
      resources: {cpu: 1, ram: 2Gi, disk: 20Gi}
      install: []
""" + _GOOD


def test_the_shipped_default_block_names_a_role_with_an_image_and_no_persona():
    """The three properties D4 rules, read off the shipped catalog rather than a fixture."""
    from aximprint.catalog import DEFAULT_NODE, ROLE_CATALOG, UNROLED_ROLE

    assert UNROLED_ROLE == "unroled-endpoint"
    assert DEFAULT_NODE["image"]
    # No persona, because it is not a `roles:` row at all — there is nothing to carry one.
    assert UNROLED_ROLE not in {r.role for r in ROLE_CATALOG}
    # Content only: a caller copying `DEFAULT_NODE` into a node body must not splice a `role`
    # key in behind its back. The role is a separate export.
    assert "role" not in DEFAULT_NODE


def test_the_default_block_loads_its_role(tmp_path):
    from aximprint.catalog.roles import _load_default

    block = _load_default(_write(tmp_path, _GOOD_DEFAULT))
    assert block["role"] == "unroled-endpoint" and block["image"] == "img@v1"


@pytest.mark.parametrize("bad,match", [
    (_GOOD_DEFAULT.replace("      role: unroled-endpoint\n", ""), "must declare a `role`"),
    (_GOOD_DEFAULT.replace("      image: img@v1\n", ""), "must declare an `image`"),
    (_GOOD_DEFAULT.replace("      role:", "      rôle:"), "unrecognized key"),
    # The collision guard, and the reason it exists: the role a host gets when NOTHING matched
    # must not be one some evidence CAN name, or two different populations arrive under one
    # name and `match_role` becomes able to return it.
    (_GOOD_DEFAULT.replace("role: unroled-endpoint", "role: r"), "also an inference rule"),
])
def test_a_default_block_that_cannot_be_trusted_is_refused(tmp_path, bad, match):
    from aximprint.catalog.roles import _load_default

    with pytest.raises(CatalogError, match=match):
        _load_default(_write(tmp_path, bad))


def test_a_default_role_colliding_with_a_synthetic_role_is_refused(tmp_path):
    """The sharper of the two collisions. The compiler tells a compiler-class group from an
    observed one by `role in SYNTHETIC_ROLES`, so a default role sharing that name would give
    every role-less host the sink's naming, its `count` (parsed out of the role name) and its
    `observed: 0` accounting — a whole population silently reclassified as synthetic."""
    from aximprint.catalog.roles import _load_default

    body = _GOOD_DEFAULT.replace("role: unroled-endpoint", "role: internet-sink-1") + """
    synthetic:
      internet-sink-1:
        image: img@v1
"""
    with pytest.raises(CatalogError, match="also a `synthetic:` role"):
        _load_default(_write(tmp_path, body))
