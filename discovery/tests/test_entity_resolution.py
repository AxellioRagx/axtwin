"""Entity resolution v1 (`Q1`, `docs/WORK_ORDER_entity_resolution.md`).

The work order's own framing is why this file exists at all: **the reference capture is a null
test, not a validation.** It can show the resolver does no harm; only synthetic churn can show it
works, because the churn this layer exists for — address reassignment, roaming — is not present in
the real data (measured: 14 addresses, 14 macs, 1:1, no reuse and no roaming). The eight cases the
work order's Validation section names are `test_churn_*` below, one test each, and they are the
acceptance criteria.

Everything here is deterministic and synthetic: no capture values, no fixtures read from disk.
"""
import json
import subprocess
import sys

from aximprint.events import FlowEvent, IdentityEvent
from aximprint.learners import extract_assertions
from aximprint.windowing.entities import (
    HOST, MAX_HOST_BINDINGS, NONHOST_WORKING_SET, REFERENCED, USER,
    EntityResolver, computer_name, kerberos_identifier, radius_username,
)
from aximprint.windowing.windows import WindowManager

T0 = 1_754_000_000.0
HOUR = 3600.0


def _dhcp(ts, ip, mac, host="", lease=0.0):
    return IdentityEvent(ts=ts, kind="dhcp", ip=ip, mac=mac, hostname=host, lease_seconds=lease)


def _flow(ts, src, dst="10.9.9.9"):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=5000, dst_port=443,
                     proto="tcp", service="ssl", duration=0.1, bytes_out=100, bytes_in=200,
                     pkts_out=1, pkts_in=1, state="SF", state_class="established")


def _windows(events, window_seconds=3600):
    wm = WindowManager(window_seconds=window_seconds)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    return wm, wins


# -- the work order's eight validation cases ------------------------------------------------

def test_churn_a_one_mac_two_addresses_is_one_entity_with_two_generations():
    """(a) A host that roams. One mac, two addresses in sequence -> ONE entity."""
    r = EntityResolver()
    r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"))
    r.observe(_dhcp(T0 + 600, "10.0.0.6", "aa:bb:cc:00:00:01"))
    assert r.resolve("10.0.0.5", T0 + 10) == "ent-0001"
    assert r.resolve("10.0.0.6", T0 + 700) == "ent-0001"
    (entity,) = r.roster()["bound"]
    assert entity["addresses"] == ["10.0.0.5", "10.0.0.6"]
    assert entity["generations"] == 2, "one entity, two address generations"
    assert r.roster()["state"]["entities_minted"] == 1


def test_churn_b_one_address_two_macs_is_two_entities():
    """(b) An address reused by a second host. Two macs in sequence -> TWO entities.

    The case a resolver that unioned on shared *addresses* would get wrong, and the reason
    bindings are intervals: after the second mac appears, the first generation is CLOSED, so the
    address's history is two hosts in sequence rather than one host with a confused one.
    """
    r = EntityResolver()
    r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"))
    r.take_merges()          # the first binding anchors a previously bare address; not the point
    r.observe(_dhcp(T0 + 600, "10.0.0.5", "aa:bb:cc:00:00:02"))
    assert r.take_merges() == [], \
        "a SPLIT is not a merge: the address was already anchored, so nothing joins the two"
    assert r.resolve("10.0.0.5", T0 + 10) == "ent-0001", "before the reassignment"
    assert r.resolve("10.0.0.5", T0 + 700) == "ent-0002", "after it"
    assert len(r.roster()["bound"]) == 2


def test_churn_c_a_user_principal_at_two_addresses_never_merges():
    """(c) D1's whole point: a user principal is a PERSON. One login at two workstations must
    not make them one host."""
    r = EntityResolver()
    for ip in ("10.0.0.5", "10.0.0.6"):
        r.observe(IdentityEvent(ts=T0, kind="kerberos", ip=ip,
                                principal="alice/CORP.EXAMPLE.TEST"))
    assert r.resolve("10.0.0.5", T0) == "10.0.0.5", "user scope anchors nothing (D4 applies)"
    assert r.resolve("10.0.0.6", T0) == "10.0.0.6"
    assert r.roster()["bound"] == [] and r.roster()["state"]["entities_minted"] == 0


def test_churn_d_a_machine_principal_at_two_addresses_is_one_entity():
    """(d) The other half of the Kerberos split: a MACHINE account does anchor a host."""
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="kerberos", ip="10.0.0.5",
                            principal="HOST01$/CORP.EXAMPLE.TEST"))
    r.observe(IdentityEvent(ts=T0 + 600, kind="kerberos", ip="10.0.0.6",
                            principal="HOST01$/CORP.EXAMPLE.TEST"))
    assert r.resolve("10.0.0.5", T0) == r.resolve("10.0.0.6", T0 + 600) == "ent-0001"
    (entity,) = r.roster()["bound"]
    assert entity["identifiers"] == ["hostname:host01"]


def test_churn_e_a_name_with_several_answers_does_not_merge_and_is_referenced():
    """(e) The dangerous one. A CDN name answering with three addresses must not fold three
    unrelated servers into one entity, and every binding it produces is `referenced` — a claim
    about a host the sensor never saw at that address."""
    r = EntityResolver()
    records = []
    for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        records += r.observe(IdentityEvent(ts=T0, kind="dns", ip=ip,
                                           hostname="cdn.example.test", record_ttl=60.0))
    assert {rec["scope"] for rec in records} == {REFERENCED}
    assert r.roster()["state"]["entities_minted"] == 0
    assert [r.resolve(ip, T0) for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3")] == \
        ["203.0.113.1", "203.0.113.2", "203.0.113.3"]
    # The TTL is recorded and is NOT a validity bound: the binding stays open past it.
    assert records[0]["record_ttl"] == 60.0 and records[0]["lease_seconds"] is None
    assert records[0]["to_ts"] is None


def test_churn_f_an_out_of_order_binding_never_inverts_its_interval():
    """(f) `LES-005`, which this exact file got wrong once already. The ingest reorder buffer is
    bounded, so a refreshing observation can genuinely arrive BEHIND its predecessor; the
    observation interval must widen, never run backwards."""
    r = EntityResolver()
    r.observe(_dhcp(T0 + 100, "10.0.0.5", "aa:bb:cc:00:00:01"))
    r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"))          # 100 s late
    r.observe(_dhcp(T0 + 200, "10.0.0.5", "aa:bb:cc:00:00:01"))
    (final,) = [rec for rec in r.flush_open()]
    assert final["first_seen"] == T0 and final["last_seen"] == T0 + 200
    assert final["first_seen"] <= final["last_seen"]
    assert final["count"] == 3
    assert final["from_ts"] == T0 + 100, \
        "validity starts when the evidence was FIRST believed; only observation bounds move"


def test_churn_g_evidence_after_a_window_closed_asserts_forward_and_leaves_it_untouched():
    """(g) D2, end to end. A window that closed before the identifying evidence keeps the entity
    ids it published; the merge is an ASSERTION, never a rewrite."""
    events = [_flow(T0 + 10, "10.0.0.5"),
              _flow(T0 + HOUR + 10, "10.0.0.5"),
              _dhcp(T0 + HOUR + 20, "10.0.0.5", "aa:bb:cc:00:00:01"),
              _flow(T0 + HOUR + 30, "10.0.0.5")]
    _wm, wins = _windows(events)
    assert "10.0.0.5" in wins[0].nodes and "ent-0001" not in wins[0].nodes, \
        "window 1 closed before any evidence existed"
    assert wins[0].entity_merges == []
    assert {"10.0.0.5", "ent-0001"} <= set(wins[1].nodes), \
        "window 2 holds both: the flow before the evidence and the flow after it"
    assert [(m["from_entity"], m["to_entity"]) for m in wins[1].entity_merges] == \
        [("10.0.0.5", "ent-0001")]
    merge = [a for a in extract_assertions(wins[1]) if a["predicate"] == "same_entity_as"]
    assert [(a["subject"], a["object"]) for a in merge] == [("10.0.0.5", "ent-0001")]


def test_churn_h_an_address_with_no_identity_evidence_resolves_to_itself():
    """(h) D4, the safety property that makes the whole change reviewable."""
    r = EntityResolver()
    assert r.resolve("10.0.0.5", T0) == "10.0.0.5"
    r.observe(IdentityEvent(ts=T0, kind="dns", ip="203.0.113.1", hostname="x.example.test"))
    assert r.resolve("203.0.113.1", T0) == "203.0.113.1", \
        "a referenced binding is recorded and still anchors nothing"
    assert r.resolve("", T0) == "?", "the empty-address sentinel is its own mint path"


# -- D4's null test, at the artifact level --------------------------------------------------

def test_d4_a_capture_with_no_host_scoped_evidence_reproduces_the_v0_entity_keying():
    """The strongest single claim this change makes: where nothing anchors, nothing changes.

    Every node key, edge key and assertion subject is the raw address, exactly as `entity == ip`
    produced them — so on such a capture the feature archive's bytes are unchanged and any
    difference elsewhere is a merge with a name attached.
    """
    events = [_flow(T0 + 10, "10.0.0.5", "10.0.0.6"),
              IdentityEvent(ts=T0 + 20, kind="dns", ip="10.0.0.6", hostname="a.example.test"),
              IdentityEvent(ts=T0 + 30, kind="kerberos", ip="10.0.0.5",
                            principal="alice/CORP.EXAMPLE.TEST"),
              IdentityEvent(ts=T0 + 40, kind="known_host", ip="10.0.0.5"),
              _flow(T0 + 50, "10.0.0.6", "10.0.0.5")]
    _wm, (win,) = _windows(events)
    assert set(win.nodes) == {"10.0.0.5", "10.0.0.6"}
    assert {e["src"] for e in win.edges} | {e["dst"] for e in win.edges} == \
        {"10.0.0.5", "10.0.0.6"}
    assert win.entity_merges == []
    assert all(not a["subject"].startswith("ent-") for a in extract_assertions(win))
    assert win.health["unscoped_identity_kinds"] == 0, \
        "known_host is DECLARED as binding nothing, not an unruled kind falling through"


# -- evidence classification ----------------------------------------------------------------

def test_computer_name_parses_the_three_shapes_this_pipeline_sees():
    assert computer_name("WORKSTN1$") == "workstn1", "AD computer-account suffix"
    assert computer_name("workstn1.corp.example.test") == "workstn1", "FQDN suffix"
    assert computer_name("WORKSTN1") == "workstn1", "NetBIOS uppercase convention"
    assert computer_name("  ws$  ") == "ws"
    assert computer_name("$") == "", "normalizes away to nothing -> no identifier at all"


def test_kerberos_principals_are_parsed_not_pattern_matched():
    machine = kerberos_identifier("HOST01$/CORP.EXAMPLE.TEST")
    assert (machine.namespace, machine.value, machine.scope) == ("hostname", "host01", HOST)
    service = kerberos_identifier("host/host01.corp.example.test/CORP.EXAMPLE.TEST")
    assert (service.namespace, service.value, service.scope) == ("hostname", "host01", HOST)
    user = kerberos_identifier("alice/CORP.EXAMPLE.TEST")
    assert (user.namespace, user.value, user.scope) == ("principal", "alice/CORP.EXAMPLE.TEST",
                                                        USER)
    assert user.value == "alice/CORP.EXAMPLE.TEST", \
        "RFC 4120 principals are case-sensitive; folding them would invent an equivalence"
    assert kerberos_identifier("") is None and kerberos_identifier("///") is None


def test_ntlm_and_a_kerberos_machine_account_naming_one_host_land_on_one_entity():
    """The case measured on the real baseline: **7 addresses carry BOTH**, so leaving the two
    source formats unnormalized would have split seven real hosts in two."""
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="ntlm", ip="10.0.0.5",
                            hostname="WORKSTN1.corp.example.test"))
    r.observe(IdentityEvent(ts=T0 + 10, kind="kerberos", ip="10.0.0.5",
                            principal="WORKSTN1$/CORP.EXAMPLE.TEST"))
    assert r.resolve("10.0.0.5", T0 + 20) == "ent-0001"
    (entity,) = r.roster()["bound"]
    assert entity["identifiers"] == ["hostname:workstn1"], "one identifier, seen twice"
    assert entity["evidence"] == ["kerberos", "ntlm"], \
        "one binding per SOURCE: 'confirmed by two independent logs' is a stronger fact than " \
        "'seen twice', and keying only on the identifier would have dropped the second source"
    assert entity["generations"] == 1, \
        "still ONE address-identity period — two confirmations of it, not two of them"
    assert [m["reason"] for m in r.take_merges()] == ["address_anchored"], \
        "no identifier_link merge: the two sources never held different entities to join"


def test_a_dhcp_row_links_its_mac_and_its_host_name_by_co_observation():
    """The only cross-namespace link, and it comes from ONE row rather than from co-location in
    time — the inference address reuse would poison."""
    r = EntityResolver()
    r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01", host="ws01"))
    # The same host later seen only by name, at a different address.
    r.observe(IdentityEvent(ts=T0 + 600, kind="ntlm", ip="10.0.0.9", hostname="ws01"))
    assert r.resolve("10.0.0.5", T0) == r.resolve("10.0.0.9", T0 + 600) == "ent-0001"
    (entity,) = r.roster()["bound"]
    assert entity["identifiers"] == ["hostname:ws01", "mac:aa:bb:cc:00:00:01"]


def test_two_identifiers_already_holding_different_entities_merge_forward():
    """A merge proper: each identifier was seen alone first, so each minted its own entity, and
    the row that names both is what joins them. The FIRST-minted id survives."""
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="ntlm", ip="10.0.0.9", hostname="ws01"))   # ent-0001
    r.observe(_dhcp(T0 + 10, "10.0.0.5", "aa:bb:cc:00:00:01"))                     # ent-0002
    merges_before = r.take_merges()
    assert [m["reason"] for m in merges_before] == ["address_anchored", "address_anchored"]
    r.observe(_dhcp(T0 + 20, "10.0.0.5", "aa:bb:cc:00:00:01", host="ws01"))
    (merge,) = r.take_merges()
    assert (merge["from_entity"], merge["to_entity"], merge["reason"]) == \
        ("ent-0002", "ent-0001", "identifier_link")
    assert r.resolve("10.0.0.5", T0 + 30) == "ent-0001", \
        "the absorbed id redirects; a published id keeps its meaning"
    assert r.resolve("10.0.0.9", T0 + 30) == "ent-0001"


def test_evidence_class_comes_from_scope_and_referenced_finally_has_a_producer():
    """`referenced` has been in the vocabulary since v0 with no code path at all — every
    assertion the project emitted was `observed`, including `bound_dns`."""
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"),
              IdentityEvent(ts=T0 + 10, kind="dns", ip="203.0.113.1",
                            hostname="a.example.test"),
              IdentityEvent(ts=T0 + 20, kind="kerberos", ip="10.0.0.7",
                            principal="alice/CORP.EXAMPLE.TEST")]
    _wm, (win,) = _windows(events)
    by_predicate = {a["predicate"]: a for a in extract_assertions(win)}
    assert by_predicate["bound_dhcp"]["evidence_class"] == "observed"
    assert by_predicate["bound_kerberos"]["evidence_class"] == "observed", \
        "a user principal IS observed at the address; scope and evidence_class are different " \
        "questions"
    assert by_predicate["bound_dns"]["evidence_class"] == "referenced"
    assert by_predicate["bound_dhcp"]["subject"] == "ent-0001", "subject is the entity"
    assert by_predicate["bound_dns"]["subject"] == "203.0.113.1", \
        "an unanchored answer address still stands for itself (D4)"


# -- ambiguity ------------------------------------------------------------------------------

def test_resolver_ambiguity_is_none_when_no_resolution_used_identity_evidence():
    _wm, (win,) = _windows([_flow(T0 + 10, "10.0.0.5", "10.0.0.6")])
    assert win.health["resolver_ambiguity"] is None
    assert win.health["resolver_anchored_resolutions"] == 0


def test_resolver_ambiguity_is_zero_when_evidence_resolved_and_nothing_was_disputed():
    """`0.0` and `None` are different findings and must stay distinguishable (`LES-006`)."""
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"), _flow(T0 + 10, "10.0.0.5", "10.0.0.6")]
    _wm, (win,) = _windows(events)
    assert win.health["resolver_ambiguity"] == 0.0
    assert win.health["resolver_anchored_resolutions"] == 1, \
        "one endpoint of the flow was anchored; the peer was not"


def test_an_unexpired_lease_overlapping_a_second_host_is_ambiguity_not_supersession():
    """The ONE way two host-scoped claims can be open at once, and it is hand-checkable.

    DHCP declares a 1 h lease on `10.0.0.5`. Ten minutes in, a different host name is seen at the
    same address. Supersession does not apply — the lease has not expired — so both stay open,
    every resolution in the overlap is ambiguous, and DHCP wins because a declared lease
    outranks a point observation.
    """
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01", lease=HOUR),
              IdentityEvent(ts=T0 + 600, kind="ntlm", ip="10.0.0.5", hostname="intruder"),
              _flow(T0 + 700, "10.0.0.5", "10.0.0.6"),
              _flow(T0 + 800, "10.0.0.5", "10.0.0.6")]
    _wm, (win,) = _windows(events)
    assert win.health["resolver_anchored_resolutions"] == 2
    assert win.health["resolver_ambiguity"] == 1.0, "both resolutions landed in the dispute"
    assert "ent-0001" in win.nodes, "the lease wins the resolution, not the newer evidence"
    assert not any(rec["change"] == "closed" for rec in win.identity_delta), \
        "an unexpired declared validity is NOT superseded — closing it would erase the conflict"


def test_a_lapsed_lease_supersedes_instead_of_disputing():
    """The same shape with the lease expired first: a clean generation boundary, no ambiguity."""
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01", lease=60.0),
              IdentityEvent(ts=T0 + 600, kind="ntlm", ip="10.0.0.5", hostname="ws02"),
              _flow(T0 + 700, "10.0.0.5", "10.0.0.6")]
    _wm, (win,) = _windows(events)
    assert win.health["resolver_ambiguity"] == 0.0
    assert [rec["change"] for rec in win.identity_delta if rec["change"] == "closed"] == \
        ["closed"]
    assert "ent-0002" in win.nodes


# -- `Q9`: emit on change, not on observation -----------------------------------------------

def test_a_restated_binding_emits_nothing_but_still_widens_its_observation_extent():
    """`Q9`/`LES-010`. Measured on the real baseline: 144,886 DNS-derived observations are 6,414
    distinct bindings, so returning a record per observation made `identity.jsonl` 95.6 %
    restatement."""
    r = EntityResolver()
    assert len(r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"))) == 1
    for i in range(1, 50):
        assert r.observe(_dhcp(T0 + i, "10.0.0.5", "aa:bb:cc:00:00:01")) == [], \
            "last_seen moving forward is not a change"
    (final,) = r.flush_open()
    assert (final["change"], final["count"], final["last_seen"]) == ("final", 50, T0 + 49)


def test_every_still_open_binding_is_published_once_at_end_of_run():
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"),
              IdentityEvent(ts=T0 + 10, kind="dns", ip="203.0.113.1", hostname="a.example.test"),
              _flow(T0 + 20, "10.0.0.5")]
    _wm, (win,) = _windows(events)
    finals = [rec for rec in win.identity_delta if rec["change"] == "final"]
    assert {rec["identifier"] for rec in finals} == \
        {"mac:aa:bb:cc:00:00:01", "dnsname:a.example.test"}
    assert sum(1 for a in extract_assertions(win) if a["predicate"].startswith("bound_")) == 2, \
        "one assertion per binding, not one per record"


# -- bounds ---------------------------------------------------------------------------------

def test_host_bindings_are_capped_at_insert_and_the_refusal_is_disclosed(monkeypatch):
    """`LES-014`: cap at INSERT (not at close, which bounds output and not memory), count the
    refusal, and never let the artifact report itself complete once it has truncated."""
    monkeypatch.setattr("aximprint.windowing.entities.MAX_HOST_BINDINGS", 3)
    r = EntityResolver()
    for i in range(10):
        r.observe(_dhcp(T0 + i, f"10.0.0.{i}", f"aa:bb:cc:00:00:{i:02d}"))
    state = r.roster()["state"]
    assert state["host_bindings"] == 3 and state["host_bindings_refused"] == 7
    assert state["complete"] is False
    assert r.resolve("10.0.0.9", T0 + 9) == "10.0.0.9", \
        "a refused binding resolves to the bare address, not to a half-recorded entity"


def test_the_nonhost_working_set_evicts_losslessly_rather_than_refusing(monkeypatch):
    """**Rewritten 2026-08-14 — it encoded the opposite contract.** This used to assert that a
    full non-host map REFUSES new bindings, which is first-come-first-served: over a long capture
    the resolver kept whatever DNS names appeared first and discarded every later one.

    `NONHOST_WORKING_SET` is now a bounded LRU dedup window, and eviction PUBLISHES the binding it
    drops as a `final` record carrying its accumulated counters. Nothing consults this map
    (`resolve()` has zero references to it), so the bound governs memory and never what the store
    receives — the split `docs/WORK_ORDER_visibility_and_vantage.md` D9 requires.
    """
    monkeypatch.setattr("aximprint.windowing.entities.NONHOST_WORKING_SET", 2)
    r = EntityResolver()
    published = []
    for i in range(10):
        published += r.observe(IdentityEvent(ts=T0 + i, kind="dns", ip=f"203.0.113.{i}",
                                             hostname=f"h{i}.example.test"))
    r.observe(_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"))
    state = r.roster()["state"]

    assert state["nonhost_bindings"] == 2, "the working set is bounded"
    assert state["nonhost_bindings_evicted"] == 8
    # Every one of the ten reached the store: eight as opened+final, two still resident.
    opened = {c["bound_to"] for c in published if c["change"] == "opened"}
    assert len(opened) == 10, "no binding was refused"
    assert len({c["bound_to"] for c in published if c["change"] == "final"}) == 8
    assert state["host_bindings"] == 1, "the host map is untouched by referenced pressure"
    assert state["complete"] is True, \
        "a lossless eviction must not report the census as truncated"


def test_an_evicted_bindings_final_record_carries_its_accumulated_counters(monkeypatch):
    """`LES-108`: a delta artifact must flush what it stops tracking, or the archive says the
    thing was seen once at one instant. Eviction is a stop-tracking event like any other."""
    monkeypatch.setattr("aximprint.windowing.entities.NONHOST_WORKING_SET", 1)
    r = EntityResolver()
    for ts in (T0, T0 + 10, T0 + 20):        # same binding, three observations
        r.observe(IdentityEvent(ts=ts, kind="dns", ip="203.0.113.1", hostname="a.example.test"))
    evicting = r.observe(IdentityEvent(ts=T0 + 30, kind="dns", ip="203.0.113.2",
                                       hostname="b.example.test"))
    final = next(c for c in evicting if c["change"] == "final")
    assert final["bound_to"] == "a.example.test"
    assert final["count"] == 3, "all three observations survive the eviction"
    assert final["last_seen"] == T0 + 20, "and so does the interval it accumulated"


def test_the_lru_keeps_what_the_capture_keeps_seeing(monkeypatch):
    """The property first-in-first-out did not have. A name observed throughout must outlive one
    seen once at the start, or a long capture's census is just its opening minutes."""
    monkeypatch.setattr("aximprint.windowing.entities.NONHOST_WORKING_SET", 2)
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="dns", ip="203.0.113.1", hostname="hot.example.test"))
    r.observe(IdentityEvent(ts=T0 + 1, kind="dns", ip="203.0.113.2", hostname="cold.example.test"))
    for i in range(5):
        # keep touching `hot`, then admit a newcomer that forces an eviction
        r.observe(IdentityEvent(ts=T0 + 10 + i, kind="dns", ip="203.0.113.1",
                                hostname="hot.example.test"))
        r.observe(IdentityEvent(ts=T0 + 20 + i, kind="dns", ip=f"203.0.113.{50 + i}",
                                hostname=f"new{i}.example.test"))
    resident = {(b.ip, b.value) for b in r._other.values()}
    assert ("203.0.113.1", "hot.example.test") in resident, \
        "the repeatedly-observed name must survive; FIFO would have dropped it first"


def test_the_declared_caps_are_far_above_what_the_real_baseline_measured():
    """A cap is only a bound if the number is defensible; both are recorded against a measurement
    rather than picked (`LES-014`, `LES-017`'s "quiet counter is not a closed case")."""
    # The guard is "a real capture must not silently truncate": both numbers below are what the
    # 2026-08-11 baseline actually used, so lowering either cap under its measured working set
    # fails here rather than showing up later as a quietly incomplete roster.
    assert MAX_HOST_BINDINGS >= 41 * 50, \
        "measured 41 host-scoped bindings (25 entities) on the 2026-08-11 baseline"
    assert NONHOST_WORKING_SET >= 6416 * 2, \
        "measured 6,416 user/referenced bindings over ~3 h. This is no longer a census cap — " \
        "eviction is lossless — but the working set should still hold a realistic capture " \
        "resident, so that interval fragmentation stays rare rather than routine"


# -- the roster -----------------------------------------------------------------------------

def test_the_roster_declares_every_mint_path_including_the_two_nobody_emitted():
    """`LES-017`, the reason this artifact exists: a published namespace is the union of every
    path that can mint a member. Two of these four are sentinels no sensor ever produced, and one
    of them is minted by a different layer entirely."""
    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01"),
              _flow(T0 + 10, "10.0.0.5", "10.0.0.6"),
              FlowEvent(ts=T0 + 20, src_ip="", dst_ip="10.0.0.6", proto="tcp",
                        state_class="established")]
    wm, _wins = _windows(events)
    roster = wm.entity_roster()
    paths = {p["path"]: p for p in roster["mint_paths"]}
    assert set(paths) == {"bound", "address", "unaddressed", "longtail"}
    assert paths["bound"]["entities"] == 1
    assert paths["address"]["enumerated"] is False, \
        "unbounded by construction: counted, never listed (LES-014)"
    assert paths["address"]["resolutions"] >= 1
    assert paths["unaddressed"]["resolutions"] == 1 and paths["unaddressed"]["entities"] == 1
    assert paths["longtail"]["entities"] == 0, "this run never spilled a peer tail"
    assert roster["id_scope"] == "run", "D3, stated in the artifact rather than inferred"


def test_the_roster_reaches_the_feature_manifest(tmp_path):
    zdir = tmp_path / "zeek"
    zdir.mkdir()
    (zdir / "conn.log").write_text("\n".join([
        "#separator \\x09",
        "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
        "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts",
        f"{T0 + 30:.6f}\t10.0.0.21\t51000\t10.0.0.5\t445\ttcp\tsmb\t0.2\t500\t1500\tSF\t5\t5",
    ]) + "\n")
    (zdir / "dhcp.log").write_text(json.dumps(
        {"ts": T0 + 10, "mac": "aa:bb:cc:dd:ee:01", "assigned_addr": "10.0.0.21",
         "host_name": "ws01", "lease_time": 86400}) + "\n")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "aximprint.cli", "features",
                        "--source", str(zdir), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    entities = manifest["entities"]
    assert entities["id_scope"] == "run"
    assert [e["entity"] for e in entities["bound"]] == ["ent-0001"]
    assert entities["bound"][0]["identifiers"] == ["hostname:ws01", "mac:aa:bb:cc:dd:ee:01"]
    assert entities["state"]["complete"] is True
    # The manifest is the only place the four mint paths are written down; without it a consumer
    # holding nodes.jsonl cannot tell an `ent-NNNN` from an address from a sentinel.
    assert {p["path"] for p in entities["mint_paths"]} == \
        {"bound", "address", "unaddressed", "longtail"}


def test_the_roster_reaches_the_belief_store_keyed_on_the_source(tmp_path):
    from aximprint.store import BeliefStore

    events = [_dhcp(T0, "10.0.0.5", "aa:bb:cc:00:00:01", host="ws01"), _flow(T0 + 10, "10.0.0.5")]
    wm, wins = _windows(events)
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    for win in wins:
        store.write_window(win, extract_assertions(win))
    store.write_entity_roster("/some/source", wm.entity_roster())
    rows = store.con.execute(
        "SELECT source, entity, mint_path, scope, identifiers, generations FROM entity_roster"
    ).fetchall()
    assert rows == [("/some/source", "ent-0001", "bound", HOST,
                     '["hostname:ws01", "mac:aa:bb:cc:00:00:01"]', 2)]
    assert store.status()["entity_roster_rows"] == 1
    store.close()


# -- the snapshot applies merges ------------------------------------------------------------

def test_the_snapshot_folds_pre_anchor_rows_into_the_entity_as_of_t(tmp_path):
    """D2's read half, and the `as_of` bound on it: a merge learned in window 2 must not be
    applied to a snapshot taken at the end of window 1, because the evidence did not exist yet."""
    from aximprint.store import BeliefStore

    events = [_flow(T0 + 10, "10.0.0.5", "10.0.0.6"),
              _dhcp(T0 + HOUR + 10, "10.0.0.5", "aa:bb:cc:00:00:01"),
              _flow(T0 + HOUR + 20, "10.0.0.5", "10.0.0.6")]
    _wm, wins = _windows(events)
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    for win in wins:
        store.write_window(win, extract_assertions(win))

    early = {e for e, *_ in store.snapshot(as_of=T0)["entities"]}
    assert early == {"10.0.0.5", "10.0.0.6"}, "as of window 1 there was no merge to apply"

    late = store.snapshot()
    assert {e for e, *_ in late["entities"]} == {"ent-0001", "10.0.0.6"}
    (folded,) = [row for row in late["entities"] if row[0] == "ent-0001"]
    assert folded[2] == 2, "both windows' node rows for this host, summed under the surviving id"
    assert store.entity_aliases() == {"10.0.0.5": "ent-0001"}
    store.close()


def test_snapshot_edges_are_relabelled_on_both_ends(tmp_path):
    from aximprint.store import BeliefStore

    events = [_flow(T0 + 10, "10.0.0.5", "10.0.0.6"),
              _dhcp(T0 + 20, "10.0.0.5", "aa:bb:cc:00:00:01"),
              _dhcp(T0 + 30, "10.0.0.6", "aa:bb:cc:00:00:02"),
              _flow(T0 + 40, "10.0.0.5", "10.0.0.6")]
    _wm, wins = _windows(events)
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    for win in wins:
        store.write_window(win, extract_assertions(win))
    edges = store.snapshot()["edges"]
    assert [(s, d, p, f) for s, d, p, f, _b in edges] == [("ent-0001", "ent-0002", "ssl", 2)], \
        "the pre-anchor and post-anchor edge are ONE conduit once both ends resolve"
    store.close()


# -- radius (docs/WORK_ORDER_zeek_stems_wide_vantage.md build step 1, D1/R2) ------

def test_radius_username_machine_account_is_host_scoped():
    """802.1X machine auth puts an AD computer account through the username field."""
    ident = radius_username("CORP\\WORKSTN1$")
    assert ident is not None
    assert (ident.namespace, ident.value, ident.scope) == ("hostname", "workstn1", HOST)
    assert ident.raw == "CORP\\WORKSTN1$", "the sensor's own string is kept verbatim"


def test_radius_username_host_service_principal_is_host_scoped():
    ident = radius_username("host/pc4.corp.example.test@CORP.EXAMPLE.TEST")
    assert ident is not None
    assert (ident.namespace, ident.value, ident.scope) == ("hostname", "pc4", HOST)


def test_radius_username_person_is_user_scoped_and_anchors_nothing():
    """A person is not a machine: recorded, never able to merge two addresses (D1)."""
    ident = radius_username("alice@corp.example.test")
    assert ident is not None
    assert (ident.namespace, ident.value, ident.scope) == ("principal", "alice", USER)


def test_radius_username_downlevel_domain_is_stripped():
    ident = radius_username("CORP\\bob")
    assert ident is not None
    assert (ident.namespace, ident.value, ident.scope) == ("principal", "bob", USER)


def test_radius_username_empty_mints_nothing():
    assert radius_username("") is None
    assert radius_username("   ") is None
    assert radius_username("CORP\\") is None


def test_radius_is_not_parsed_as_a_kerberos_principal():
    """The two grammars differ, and running one parser over the other's strings is the
    shape-guessing LES-008 forbids. `host/pc4...` proves they disagree: Kerberos joins the
    realm with `/`, so it reads the LAST component as the realm and never sees `pc4`.
    """
    assert radius_username("host/pc4.corp@CORP").value == "pc4"
    assert kerberos_identifier("host/pc4.corp@CORP").value != "pc4"


def test_radius_mac_merges_two_addresses_the_same_host_held():
    """The payoff the stem was admitted for: one machine, two addresses over time."""
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="radius", ip="10.0.0.55",
                            mac="aa:bb:cc:dd:ee:ff", principal="alice"))
    r.observe(IdentityEvent(ts=T0 + 3600, kind="radius", ip="10.0.0.56",
                            mac="AA:BB:CC:DD:EE:FF", principal="alice"))
    assert r.resolve("10.0.0.55", T0 + 7200) == r.resolve("10.0.0.56", T0 + 7200)


def test_radius_user_alone_never_merges_two_addresses():
    """Two people on two addresses stay two entities -- a username is not a machine (D1)."""
    r = EntityResolver()
    r.observe(IdentityEvent(ts=T0, kind="radius", ip="10.0.0.55", principal="alice"))
    r.observe(IdentityEvent(ts=T0 + 10, kind="radius", ip="10.0.0.56", principal="alice"))
    assert r.resolve("10.0.0.55", T0 + 20) != r.resolve("10.0.0.56", T0 + 20)
