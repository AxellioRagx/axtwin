"""The operator assertions file — `axr-assertions/1` (`docs/WORK_ORDER_reference_ingest.md` S2).

Two halves, matching the module's own split: **shape errors refuse** (and every refusal names the
offending value and the accepted set, because a refusal that does not say what would have been
accepted just moves the guessing), and **the document round-trips** — parse, merge, re-emit, parse
again — which is what D15's *"a no-op save is byte-identical"* actually rests on.

Synthetic content only, plus one checked-in fixture exercising every record kind (`LES-003`).
"""
import pathlib

import pytest
import yaml

from aximprint.ingest.reference import get_reference_plugin, reference_plugin_names
from aximprint.operator_assertions import (
    ADD_KINDS,
    ASSERTED_CONFIDENCE,
    ASSERTIONS_EVIDENCE_CLASS,
    ASSERTIONS_SCHEMA,
    OVERRIDE_FIELDS,
    AssertionsError,
    conduit_key,
    dump_assertions,
    load_assertions,
    merge_assertions,
    parse_assertions,
    store_rows,
)

FIXTURE = (pathlib.Path(__file__).resolve().parent / "fixtures" / "assertions"
           / "corpapcn.assertions.yaml")


def _doc(**over):
    base = {"schema": ASSERTIONS_SCHEMA, "imprint": "t",
            "as_of": "2026-09-04T18:00:00Z", "source": "operator: raghu"}
    base.update(over)
    return base


def _parse(**over):
    return parse_assertions(_doc(**over))


# -- the fixture: every record kind, parsed ------------------------------------------------

def test_the_checked_in_fixture_carries_every_record_kind():
    """The work order's own unit-test requirement (§8, "Three checked-in fixtures"). It is also
    what the end-to-end check against a read-only copy of the CorpAPCN store is driven by, so a
    change that breaks it breaks the thing this slice was accepted on."""
    aset = load_assertions(FIXTURE)
    assert aset.schema == ASSERTIONS_SCHEMA
    assert aset.source == "operator:raghu", "the `operator: name` spelling canonicalizes"
    assert {r.kind for r in aset.adds} == set(ADD_KINDS)
    assert {r.field for r in aset.overrides} == {"role", "image", "resources", "group"}
    assert [r.kind for r in aset.confirm] == ["segment"] and [r.kind for r in aset.doubt] == ["host"]
    assert [r.key for r in aset.suppress] == ["address:1.1.1.1", "address:10.80.20.184"]
    assert all(r.note for r in (*aset.adds, *aset.overrides, *aset.suppress))


def test_the_records_come_back_in_canonical_order_not_file_order():
    """D16 as a property of the parsed value, not a discipline downstream. The compiler indexes
    into these by key and never iterates the document, but the ordering is what makes a re-emit
    byte-stable and what a reader compares two files by."""
    aset = _parse(add=[{"kind": "segment", "key": "10.0.9.0/24", "note": "b"},
                       {"kind": "conduit", "key": "10.0.9.0/24|10.0.1.0/24", "note": "a"}],
                  override=[{"key": "address:10.0.1.9", "field": "role", "value": "z",
                             "note": "n"},
                            {"key": "address:10.0.1.2", "field": "image", "value": "i",
                             "note": "n"}])
    assert [r.merge_key for r in aset.adds] == [
        ("conduit", "10.0.1.0/24|10.0.9.0/24"), ("segment", "10.0.9.0/24")]
    assert [r.merge_key for r in aset.overrides] == [
        ("address:10.0.1.2", "image"), ("address:10.0.1.9", "role")]


# -- refusals ------------------------------------------------------------------------------

def test_an_unknown_schema_is_refused_rather_than_guessed_at():
    with pytest.raises(AssertionsError, match="axr-assertions/2"):
        parse_assertions(_doc(schema="axr-assertions/9"))


def test_a_version_1_document_parses_and_re_emits_as_version_2():
    """axr-assertions/2 is a superset of /1 (docs/design/topology-layers.md §3.2): a file an
    operator wrote under /1 is read unchanged and migrates to /2 on its first save."""
    from aximprint.operator_assertions import ACCEPTED_SCHEMAS, to_document

    assert "axr-assertions/1" in ACCEPTED_SCHEMAS
    aset = parse_assertions(_doc(schema="axr-assertions/1"))
    assert aset.schema == "axr-assertions/2"
    assert to_document(aset)["schema"] == "axr-assertions/2"


def test_kind_route_is_refused_BY_NAME_with_the_reason():
    """D13/§11 D-c(a). A named refusal, not an unknown-kind one: `route` is a thing an operator
    will reasonably try, and "a twin config has no route object at all" is the answer they need."""
    with pytest.raises(AssertionsError) as exc:
        _parse(add=[{"kind": "route", "key": "10.0.0.0/24", "note": "n"}])
    assert "route" in str(exc.value)
    assert "no route object" in str(exc.value)
    assert "conduit" in str(exc.value), "the refusal names what to use instead"


def test_an_unknown_add_kind_names_the_accepted_set():
    with pytest.raises(AssertionsError,
                       match=r"\['conduit', 'device', 'host', 'link', 'segment', 'tap'\]"):
        _parse(add=[{"kind": "vlan", "key": "10.0.0.0/24", "note": "n"}])


@pytest.mark.parametrize("kind,key,field", [
    ("host", "address:10.0.0.1", "persona"),
    ("host", "address:10.0.0.1", "zone_type"),      # a SEGMENT field, on a host
    ("segment", "10.0.0.0/24", "role"),             # a HOST field, on a segment
])
def test_an_unknown_override_field_is_refused_naming_the_field_and_the_set(kind, key, field):
    """D12: closed, per kind, fail-closed. Never a silent skip — a silently ignored override is an
    operator's edit vanishing with no message, which is the failure the whole save/apply loop
    exists to prevent."""
    with pytest.raises(AssertionsError) as exc:
        _parse(override=[{"key": key, "field": field, "value": "x", "note": "n"}])
    assert field in str(exc.value)
    assert str(list(OVERRIDE_FIELDS[kind])) in str(exc.value)


def test_a_key_in_both_add_and_suppress_is_refused_not_ordered():
    """D15, verbatim: *"a key in both `add` and `suppress` is a refusal, not a precedence
    puzzle"* — the file says the entity should exist and should not, and nothing outside the file
    can decide which was meant."""
    with pytest.raises(AssertionsError, match="both `add` and `suppress`"):
        _parse(add=[{"kind": "host", "key": "address:10.0.0.1", "segment": "10.0.0.0/24",
                     "note": "n"}],
               suppress=[{"key": "address:10.0.0.1", "note": "n"}])


def test_one_field_declared_by_both_an_add_and_an_override_is_refused():
    """Two hand-authored values for one field are one class contradicting itself, and
    `resolve_precedence` refuses two facts of one class for exactly that reason. Caught here,
    where the message can name both records, rather than at the next compile."""
    with pytest.raises(AssertionsError, match="BOTH"):
        _parse(add=[{"kind": "host", "key": "address:10.0.0.1", "segment": "10.0.0.0/24",
                     "role": "web-tier", "note": "n"}],
               override=[{"key": "address:10.0.0.1", "field": "role", "value": "file-server",
                          "note": "n"}])


def test_a_duplicate_key_inside_one_file_is_refused():
    with pytest.raises(AssertionsError, match="declared twice"):
        _parse(suppress=[{"key": "address:10.0.0.1", "note": "a"},
                         {"key": "address:10.0.0.1", "note": "b"}])


def test_confidence_is_never_authored():
    """`14` §3. A file that could set 0.4 would let an operator tune the evidence mix that is the
    scorecard's only guardrail against a twin certifying its own inputs."""
    with pytest.raises(AssertionsError, match="never authored"):
        _parse(suppress=[{"key": "address:10.0.0.1", "note": "n", "confidence": 0.4}])


def test_every_record_needs_a_note():
    with pytest.raises(AssertionsError, match="non-empty `note`"):
        _parse(suppress=[{"key": "address:10.0.0.1"}])


@pytest.mark.parametrize("key", ["10.0.0.1", "nodes[3]", "dns:corp.example", "address:"])
def test_a_key_that_is_not_a_stable_entity_key_is_refused(key):
    """D10: the keys the IR already mints, never a twin-config index."""
    with pytest.raises(AssertionsError):
        _parse(suppress=[{"key": key, "note": "n"}])


def test_a_segment_key_must_be_the_network_address():
    """Parsed, not pattern-matched (`LES-008`). A key that is not the network address would join
    against nothing and silently assert about nothing."""
    with pytest.raises(AssertionsError, match="network CIDR"):
        _parse(add=[{"kind": "segment", "key": "10.0.0.5/24", "note": "n"}])


def test_suppressing_a_segment_is_refused_with_the_reason():
    """v1 suppresses HOSTS (`14` §4's ruled first edit set). Removing a segment that still carries
    placed hosts would leave every node's `net:` dangling, which is a topology edit."""
    with pytest.raises(AssertionsError, match="Suppressing a SEGMENT"):
        _parse(suppress=[{"key": "10.0.0.0/24", "note": "n"}])


def test_a_conduit_key_is_two_different_cidrs():
    with pytest.raises(AssertionsError, match="two DIFFERENT segments"):
        _parse(add=[{"kind": "conduit", "key": "10.0.0.0/24|10.0.0.0/24", "note": "n"}])


def test_a_conduit_key_is_normalized_to_the_compilers_own_pair_order():
    """D10: matching `synth/compiler.py`'s `tuple(sorted(...))`. Written the other way round it
    would name the identical conduit under a second key, and the two would merge as different
    records."""
    aset = _parse(add=[{"kind": "conduit", "key": "10.0.9.0/24|10.0.1.0/24", "note": "n"}])
    assert aset.adds[0].key == conduit_key("10.0.1.0/24", "10.0.9.0/24")
    assert aset.adds[0].key == "10.0.1.0/24|10.0.9.0/24"


@pytest.mark.parametrize("value,match", [
    ({"cpu": 2, "gpu": 1}, "unknown resource key"),
    ({}, "asserts nothing"),
    ({"cpu": [1]}, "string or an integer"),
])
def test_a_resources_value_is_closed_and_typed(value, match):
    with pytest.raises(AssertionsError, match=match):
        _parse(override=[{"key": "address:10.0.0.1", "field": "resources", "value": value,
                          "note": "n"}])


def test_an_unknown_zone_type_names_the_accepted_set():
    with pytest.raises(AssertionsError, match=r"\['simple', 'vlan'\]"):
        _parse(override=[{"key": "10.0.0.0/24", "field": "zone_type", "value": "ovs",
                          "note": "n"}])


@pytest.mark.parametrize("entry,match", [
    ({"port": 0, "proto": "tcp"}, "1..65535"),
    ({"port": 88, "proto": "sctp"}, "unknown protocol"),
    ({"port": 88}, "unknown protocol"),
    ({"port": 88, "proto": "tcp", "iface": "eth0"}, "unknown key"),
])
def test_a_listen_entry_is_closed_and_typed(entry, match):
    with pytest.raises(AssertionsError, match=match):
        _parse(add=[{"kind": "host", "key": "address:10.0.0.1", "segment": "10.0.0.0/24",
                     "listen": [entry], "note": "n"}])


def test_a_listen_set_becomes_the_port_strings_the_ir_joins_against():
    aset = _parse(add=[{"kind": "host", "key": "address:10.0.0.1", "segment": "10.0.0.0/24",
                        "listen": [{"port": 445, "proto": "tcp"},
                                   {"port": 88, "proto": "tcp"}], "note": "n"}])
    assert aset.adds[0].fields["listen"] == ["445/tcp", "88/tcp"]


@pytest.mark.parametrize("source", [None, "raghu", "operator:", "export:ldif"])
def test_the_source_must_name_an_operator(source):
    with pytest.raises(AssertionsError, match="source"):
        parse_assertions(_doc(source=source))


def test_as_of_is_required_and_parsed():
    with pytest.raises(AssertionsError, match="as_of: required"):
        parse_assertions({k: v for k, v in _doc().items() if k != "as_of"})
    with pytest.raises(AssertionsError, match="ISO-8601"):
        parse_assertions(_doc(as_of="last tuesday"))


def test_an_unquoted_yaml_timestamp_parses_the_same_as_a_quoted_one():
    """PyYAML resolves an unquoted RFC-3339 stamp into a `datetime` before this module sees a
    string, so both shapes reach the parser and both have to canonicalize identically — otherwise
    quoting the timestamp would change the file's bytes on the next save."""
    quoted = parse_assertions(yaml.safe_load(
        f"schema: {ASSERTIONS_SCHEMA}\nas_of: '2026-09-04T18:00:00Z'\nsource: operator:r\n"))
    bare = parse_assertions(yaml.safe_load(
        f"schema: {ASSERTIONS_SCHEMA}\nas_of: 2026-09-04T18:00:00Z\nsource: operator:r\n"))
    assert quoted.as_of == bare.as_of == "2026-09-04T18:00:00Z"
    assert quoted.as_of_epoch == bare.as_of_epoch


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(AssertionsError, match="unknown key"):
        parse_assertions(_doc(remove=[]))


def test_an_empty_file_is_refused_rather_than_read_as_no_assertions(tmp_path):
    """An absent file and an empty one are different states, and only the first means "no
    assertions" (`LES-006` at the filesystem)."""
    path = tmp_path / "a.yaml"
    path.write_text("")
    with pytest.raises(AssertionsError, match="is empty"):
        load_assertions(path)


# -- merge and re-emit (D15) ---------------------------------------------------------------

def test_a_save_replaces_a_same_key_record_and_appends_the_rest():
    base = _parse(override=[{"key": "address:10.0.0.1", "field": "role", "value": "web-tier",
                             "note": "old"}])
    incoming = parse_assertions(_doc(
        as_of="2026-09-05T09:00:00Z",
        override=[{"key": "address:10.0.0.1", "field": "role", "value": "file-server",
                   "note": "new"},
                  {"key": "address:10.0.0.2", "field": "image", "value": "i", "note": "n"}]))
    merged = merge_assertions(base, incoming)
    assert [(r.key, r.field, r.value) for r in merged.overrides] == [
        ("address:10.0.0.1", "role", "file-server"),
        ("address:10.0.0.2", "image", "i")]
    assert merged.as_of == "2026-09-05T09:00:00Z", "`as_of` is rewritten to the save's"


def test_a_merge_that_would_produce_an_invalid_document_is_refused():
    """A base that adds a key an incoming save suppresses is the same contradiction as one file
    declaring both — discovered here, where the message can name it, rather than at the next
    compile."""
    base = _parse(add=[{"kind": "host", "key": "address:10.0.0.1", "segment": "10.0.0.0/24",
                        "note": "n"}])
    incoming = _parse(suppress=[{"key": "address:10.0.0.1", "note": "n"}])
    with pytest.raises(AssertionsError, match="both `add` and `suppress`"):
        merge_assertions(base, incoming)


def test_a_no_op_save_of_a_canonical_document_is_byte_identical():
    """D15's headline property, and the one the whole apply loop rests on: a save that changes
    nothing must recompile to the same twin, which starts with the file's own bytes not moving."""
    aset = load_assertions(FIXTURE)
    once = dump_assertions(aset)
    twice = dump_assertions(merge_assertions(parse_assertions(yaml.safe_load(once)),
                                             parse_assertions(yaml.safe_load(once))))
    assert once == twice


def test_the_canonical_form_round_trips_through_the_parser():
    """A re-emit the parser refuses is a round trip that does not round-trip — found the first
    time a merge ran over a file with a `listen:` set, whose parsed `"445/tcp"` form is not the
    `{port, proto}` shape the schema accepts."""
    aset = load_assertions(FIXTURE)
    again = parse_assertions(yaml.safe_load(dump_assertions(aset)))
    assert dump_assertions(again) == dump_assertions(aset)
    assert [r.fields.get("listen") for r in again.adds] == [r.fields.get("listen")
                                                            for r in aset.adds]


def test_the_re_emitted_document_does_not_depend_on_the_authors_record_order():
    a = _parse(suppress=[{"key": "address:10.0.0.2", "note": "b"},
                         {"key": "address:10.0.0.1", "note": "a"}])
    b = _parse(suppress=[{"key": "address:10.0.0.1", "note": "a"},
                         {"key": "address:10.0.0.2", "note": "b"}])
    assert dump_assertions(a) == dump_assertions(b)


# -- the belief-store projection (D14) -----------------------------------------------------

def test_the_reference_registry_is_separate_from_the_sensor_one():
    """A reference reader in `ingest/base.py::_REGISTRY` would inflate "which sensors can this
    build read" with something that has never seen a packet — and would move the doc-stats
    `plugins` counter, which means exactly that."""
    from aximprint.ingest.base import _REGISTRY

    assert reference_plugin_names() == ["assertions"]
    assert "assertions" not in _REGISTRY


def test_the_projection_carries_only_predicates_the_store_already_reads():
    """Deliberately partial, and the gap is COUNTED rather than papered over. Minting predicates
    for `image`, a conduit or a suppression would put rows in the store nothing reads, which is
    `LES-101`'s documentation in a data costume."""
    aset = load_assertions(FIXTURE)
    rows, uncarried = store_rows(aset)
    assert {r["predicate"] for r in rows} == {"has_role", "listens_on"}
    assert all(r["evidence_class"] == ASSERTIONS_EVIDENCE_CLASS for r in rows)
    assert all(r["confidence"] == ASSERTED_CONFIDENCE for r in rows)
    assert all(r["source"] == "operator:raghu" for r in rows)
    assert all(r["window_start"] == aset.as_of_epoch for r in rows), "the file's own time bound"
    assert uncarried == {"add:conduit": 2, "add:device": 1, "add:link": 1, "add:segment": 1,
                         "add:tap": 1, "confirm": 1, "doubt": 1,
                         "override:host.group": 1, "override:host.image": 1,
                         "override:host.resources": 1, "suppress": 2}


def test_the_projected_rows_are_sorted_at_the_persistence_boundary():
    aset = load_assertions(FIXTURE)
    rows, _ = store_rows(aset)
    assert rows == sorted(rows, key=lambda r: (r["subject"], r["predicate"], r["object"]))


def test_the_plugin_declares_one_evidence_class_for_the_whole_document():
    """A property of the CHANNEL, not of a row: an assertions file is `asserted` throughout, which
    is what lets the store replace a prior snapshot from one source wholesale even when the new
    one carries no rows at all."""
    snapshot = get_reference_plugin("assertions").read(str(FIXTURE))
    assert snapshot.evidence_class == ASSERTIONS_EVIDENCE_CLASS
    assert snapshot.schema == ASSERTIONS_SCHEMA
    assert snapshot.kind == "assertions"


def test_an_unknown_reference_plugin_fails_loud():
    with pytest.raises(KeyError, match="no reference-ingest plugin"):
        get_reference_plugin("ldif")


# -- the belief-store door end to end (D14) ------------------------------------------------

def _tiny_store(tmp_path, name="s.duckdb"):
    """A belief store with two windows of synthetic flows, so `snapshot()` has real subjects."""
    from aximprint.events import FlowEvent
    from aximprint.learners import extract_assertions
    from aximprint.provenance import build_stamp
    from aximprint.store.belief_store import BeliefStore
    from aximprint.windowing import WindowManager

    window, t0 = 3600, 1_754_000_000.0
    w1 = t0 - (t0 % window)

    def flow(ts, src, dst, dport):
        return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                         proto="tcp", service="", duration=1.0, bytes_out=100, bytes_in=200,
                         pkts_out=2, pkts_in=2, state="SF", state_class="established",
                         local_src=True, local_dst=True)

    store = BeliefStore(str(tmp_path / name))
    wm = WindowManager(window_seconds=window)
    events = [flow(w1 + 1, "10.0.1.10", "10.0.1.20", 88),
              flow(w1 + 2, "10.0.1.10", "10.0.1.20", 389),
              flow(w1 + 3, "10.0.1.11", "10.0.1.30", 445)]
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for win in wins:
        store.write_window(win, extract_assertions(win))
    store.write_entity_roster("/cap/one", wm.entity_roster())
    store.write_run({"source": "/cap/one", "plugin": "zeek", "window_seconds": window,
                     "subbuckets": 12}, build_stamp(), len(wins), w1, w1 + 1)
    return store, w1


def _ingest(store, aset):
    """The CLI's own subject-resolution + write path, exercised directly."""
    import dataclasses

    from aximprint.cli import _resolve_reference_subjects
    from aximprint.ingest.reference.base import ReferenceSnapshot

    rows, uncarried = store_rows(aset)
    snapshot = ReferenceSnapshot(
        kind="assertions", schema=aset.schema,
        evidence_class=ASSERTIONS_EVIDENCE_CLASS, as_of=aset.as_of_epoch,
        source=aset.source, facts=tuple(rows), uncarried=uncarried)
    snapshot, unresolved = _resolve_reference_subjects(store, snapshot, dataclasses)
    return store.write_reference_snapshot(snapshot), unresolved


def test_an_ingested_role_reaches_the_snapshot_as_asserted(tmp_path):
    """The store door's whole purpose: an override of a DISCOVERED entity's role survives
    re-materialization instead of reverting to the census's reading."""
    store, _ = _tiny_store(tmp_path)
    summary, unresolved = _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "role", "value": "web-tier", "note": "n"}])))
    assert summary["rows_written"] == 1 and unresolved == []
    rows = [r for r in store.snapshot()["roles"] if r[0] == "10.0.1.20"]
    assert ("10.0.1.20", "web-tier", 1.0, "asserted") in rows
    assert ("10.0.1.20", "domain-controller", 0.9, "imputed") in rows, \
        "the store keeps BOTH readings — an override is visible, never an erasure"
    store.close()


def test_the_ir_entity_key_is_mapped_onto_the_stores_own_subject_id(tmp_path):
    """The two vocabularies genuinely differ: a file keys on `address:10.0.1.20` (D10) and the
    store keys on the resolver's `10.0.1.20`. Written without the map the rows land under a
    subject nothing joins to, and the failure is SILENT — found exactly that way against a copy
    of the CorpAPCN store."""
    store, _ = _tiny_store(tmp_path)
    _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "role", "value": "web-tier", "note": "n"}])))
    subjects = [r[0] for r in store.con.execute(
        "SELECT subject FROM assertions WHERE evidence_class='asserted'").fetchall()]
    assert subjects == ["10.0.1.20"]
    store.close()


def test_a_key_the_capture_never_saw_is_reported_rather_than_written(tmp_path):
    """A belief-store row is a fact ABOUT a subject; it does not mint one. An `add: host` for an
    address nothing observed has no subject in this store, and saying so beats writing a row that
    joins to nothing."""
    store, _ = _tiny_store(tmp_path)
    summary, unresolved = _ingest(store, parse_assertions(_doc(add=[
        {"kind": "host", "key": "address:10.0.1.99", "segment": "10.0.1.0/24",
         "role": "file-server", "note": "n"}])))
    assert unresolved == ["address:10.0.1.99"] and summary["rows_written"] == 0
    store.close()


def test_a_second_ingest_REPLACES_the_first_rather_than_accumulating(tmp_path):
    """A reference document is a SNAPSHOT, not an event stream. Appending would resurrect a record
    the operator deleted from the file, and it would keep winning precedence forever with nothing
    in any artifact to say where it came from."""
    store, _ = _tiny_store(tmp_path)
    _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "role", "value": "web-tier", "note": "n"}])))
    summary, _ = _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "role", "value": "file-server", "note": "n"}])))
    assert summary["rows_replaced"] == 1 and summary["rows_written"] == 1
    asserted = [r for r in store.snapshot()["roles"]
                if r[0] == "10.0.1.20" and r[3] == "asserted"]
    assert asserted == [("10.0.1.20", "file-server", 1.0, "asserted")]
    store.close()


def test_a_document_with_no_carryable_facts_still_clears_its_prior_snapshot(tmp_path):
    """Which is why the evidence class rides on the SNAPSHOT rather than being scanned off its
    rows: a scan of zero rows could not say which class's prior snapshot to replace."""
    store, _ = _tiny_store(tmp_path)
    _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "role", "value": "web-tier", "note": "n"}])))
    summary, _ = _ingest(store, parse_assertions(_doc(suppress=[
        {"key": "address:10.0.1.20", "note": "out of scope"}])))
    assert summary["rows_replaced"] == 1 and summary["rows_written"] == 0
    assert not [r for r in store.snapshot()["roles"] if r[3] == "asserted"]
    store.close()


def test_an_asserted_segment_membership_outranks_the_observed_one_by_RULE(tmp_path):
    """The reduction the store used to make on `max(confidence)` alone — right by arithmetic
    accident while `SEGMENT_EVIDENCE`'s two rows ranked the same way twice, and wrong the moment a
    class could break the coupling. Resolved on the conclusions side now
    (`ir/materialize.py::resolve`), which is why the store returns BOTH readings here."""
    from aximprint.ir.materialize import materialize

    store, _ = _tiny_store(tmp_path)
    _ingest(store, parse_assertions(_doc(override=[
        {"key": "address:10.0.1.20", "field": "segment", "value": "10.9.9.0/24",
         "note": "actually on the OT VLAN"}])))
    seg_rows = [r for r in store.snapshot()["segments"] if r[0] == "10.0.1.20"]
    assert {r[3] for r in seg_rows} == {"observed", "asserted"}
    # `--as-of` past the file's own stamp, deliberately: `snapshot_evidence` bounds an
    # unqualified materialization at the LAST WINDOW's start, and this file is dated after the
    # capture — so a default PIT correctly does not carry it. That is the as-of model working, and
    # it is the reason `assertions ingest` prints a `materialize_hint`.
    pit = materialize(store, as_of=_parse().as_of_epoch)
    entity = next(e for e in pit["ir_entity"] if e["entity_key"] == "address:10.0.1.20")
    assert entity["segment"] == "10.9.9.0/24"
    assert entity["segment_evidence_class"] == "asserted"
    store.close()
