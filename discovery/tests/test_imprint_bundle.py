"""`synth/imprint_bundle.py` — the IR topology export (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C11).

The property under test is not "it emits JSON" but that it emits the SUPERSET. The Console's
Physical view exists to show the observed node census of which a deployed twin is a scaled-down
subset; if this exporter ever starts filtering the way `cast` does, that view silently becomes a
second copy of the twin and nobody can tell.
"""
from aximprint.synth.imprint_bundle import build_topology_bundle

_PIT = {
    "ir_pit": [{"pit_id": "pit-abc-1", "as_of": 100.0, "network": "n1",
                "vantage": "https://sensor:0", "windows": 11, "schema_version": "1.3"}],
    "ir_entity": [
        {"entity_key": "address:10.0.0.1", "source_entity": "10.0.0.1", "locality": "local",
         "segment": "10.0.0.0/24", "segment_evidence_class": "observed", "role": "web-tier",
         "role_confidence": 0.6, "role_evidence_class": "imputed", "windows_seen": 11,
         "listen_ports": '["443/tcp"]', "dial_ports": '["53/udp"]'},
        # role-less AND placed: dropped by `cast`'s D5 filter, and the whole point of Physical
        {"entity_key": "address:10.0.0.2", "source_entity": "10.0.0.2", "locality": "local",
         "segment": "10.0.0.0/24", "segment_evidence_class": "observed", "role": None,
         "role_confidence": None, "role_evidence_class": None, "windows_seen": 3,
         "listen_ports": "[]", "dial_ports": '["443/tcp"]'},
        # unplaced external peer: still part of the observed census
        {"entity_key": "address:9.9.9.9", "source_entity": "9.9.9.9", "locality": "external",
         "segment": None, "segment_evidence_class": None, "role": None,
         "role_confidence": None, "role_evidence_class": None, "windows_seen": 1,
         "listen_ports": "[]", "dial_ports": "[]"},
    ],
    "ir_edge": [
        {"src_key": "address:10.0.0.2", "dst_key": "address:10.0.0.1", "proto": "tcp",
         "bytes": 4096, "flows": 7, "evidence_class": "observed"},
    ],
    "ir_segment": [{"cidr": "10.0.0.0/24", "name": "seg-10-0-0-0", "entities": 2,
                    "coverage_source": "inferred_from_traffic"}],
}


def test_the_census_is_the_superset_not_the_deployable_subset():
    """The regression that matters: role-less and unplaced entities must SURVIVE."""
    b = build_topology_bundle(_PIT)
    ids = {n["id"] for n in b["nodes"]}
    assert "address:10.0.0.2" in ids, "a role-less placed host is what Physical is about"
    assert "address:9.9.9.9" in ids, "an unplaced external peer is still observed"
    assert b["census"]["entities_total"] == 3
    assert b["census"]["with_role"] == 1, "1 of 3 -- the twin would carry only this one"
    assert b["census"]["placed"] == 2
    assert b["census"]["local"] == 2


def test_edges_carry_the_weight_a_conversation_graph_needs():
    """`bytes`/`flows` are the link weight. Their ABSENCE from the published DTO -- not from the
    IR -- is what had the Logical view unable to draw anything."""
    e = build_topology_bundle(_PIT)["edges"][0]
    assert (e["bytes"], e["flows"]) == (4096, 7)
    assert e["evidence_class"] == "observed", "observed vs imputed must survive the export"


def test_ports_are_decoded_not_passed_through_as_json_strings():
    n = {x["id"]: x for x in build_topology_bundle(_PIT)["nodes"]}["address:10.0.0.1"]
    assert n["listen_ports"] == ["443/tcp"] and n["dial_ports"] == ["53/udp"]


def test_a_malformed_port_blob_degrades_to_empty_rather_than_raising():
    """A diagnostic export must not be the thing that fails."""
    pit = {**_PIT, "ir_entity": [{**_PIT["ir_entity"][0], "listen_ports": "not-json"}]}
    assert build_topology_bundle(pit)["nodes"][0]["listen_ports"] == []


def test_pit_identity_travels_so_a_deployed_twin_can_be_joined_to_its_source():
    b = build_topology_bundle(_PIT, imprint_id="corpapcn-twin")
    assert b["imprint_id"] == "corpapcn-twin"
    assert b["pit_id"] == "pit-abc-1", "the join key, as a field rather than a description prefix"


# ---------------------------------------------------------------------------------------------
# `axr-topology/1.4` — the services seam
# ---------------------------------------------------------------------------------------------

_PIT_14 = {
    **_PIT,
    "ir_entity": [
        {"entity_key": "hostname:axdc01", "source_entity": "ent-0008", "locality": "local",
         "segment": "10.0.0.0/24", "segment_evidence_class": "observed",
         "role": "domain-controller", "role_confidence": 0.9, "role_evidence_class": "imputed",
         "windows_seen": 11,
         "listen_ports": '["53/udp", "88/tcp", "389/tcp", "3389/tcp", "49669/tcp"]',
         "dial_ports": "[]"},
        {"entity_key": "address:9.9.9.9", "source_entity": "9.9.9.9", "locality": "external",
         "segment": None, "segment_evidence_class": None, "role": None,
         "role_confidence": None, "role_evidence_class": None, "windows_seen": 1,
         "listen_ports": "[]", "dial_ports": "[]"},
    ],
    "ir_edge": [
        {"src_key": "address:9.9.9.9", "dst_key": "hostname:axdc01", "proto": "dns",
         "bytes": 1, "flows": 1, "evidence_class": "observed"},
        # a COMPOSITE: Zeek joins every analyzer that fired on one connection
        {"src_key": "address:9.9.9.9", "dst_key": "hostname:axdc01", "proto": "gssapi,smb,krb",
         "bytes": 1, "flows": 1, "evidence_class": "observed"},
        {"src_key": "address:9.9.9.9", "dst_key": "hostname:axdc01", "proto": "ldap_tcp",
         "bytes": 1, "flows": 1, "evidence_class": "observed"},
        # bare `tcp` is the sensor saying "I could not name this service"
        {"src_key": "address:9.9.9.9", "dst_key": "hostname:axdc01", "proto": "tcp",
         "bytes": 1, "flows": 1, "evidence_class": "observed"},
    ],
    "ir_identity": [
        {"kind": "dns", "namespace": "referenced", "identifier": "axdc01.axellio.dom",
         "entity_key": "hostname:axdc01", "evidence_class": "referenced"},
        {"kind": "dns", "namespace": "referenced", "identifier": "axdc01.axellio.dom",
         "entity_key": "hostname:axdc01", "evidence_class": "referenced"},
        {"kind": "kerberos", "namespace": "host", "identifier": "BW-L$/AXELLIO.DOM",
         "entity_key": "hostname:axdc01", "evidence_class": "observed"},
        {"kind": "ntlm", "namespace": "host", "identifier": "axdc01.Axellio.dom",
         "entity_key": "hostname:axdc01", "evidence_class": "observed"},
    ],
}


def test_the_bundle_declares_the_version_that_actually_names_its_shape():
    """`1.3`'s own note said the slice that lands `services[]` bumps it, and `1.4` was that
    slice; `1.5` is the one that lands `cast_invocation`. Stamping a number that announces
    absent content is the defect both bumps were avoiding.

    The pin lives here, on the constant, so a bump is a deliberate edit to a test rather than a
    number that drifts with whoever touched the module last."""
    from aximprint.synth.imprint_bundle import BUNDLE_SCHEMA
    b = build_topology_bundle(_PIT_14)
    assert BUNDLE_SCHEMA == "axr-topology/1.5"
    assert b["bundle_schema"] == "axr-topology/1.5"
    # ADDITIVE at every step: every 1.3 key survives 1.4, and every 1.4 key survives 1.5, so a
    # reader written against either stays correct and ignores keys it does not know.
    for key in ("nodes", "edges", "segments", "census", "pit_id", "imprint_id",
                "schema_version", "observations"):
        assert key in b
    assert "services" in b["nodes"][0], "1.4's per-node join, still here"


def test_services_join_the_listen_census_to_the_protocol_ACTUALLY_spoken():
    """The whole point of `services[]`: it lets a consumer pick *bind9* rather than
    *"something on 53"* (§6.1)."""
    node = {n["id"]: n for n in build_topology_bundle(_PIT_14)["nodes"]}["hostname:axdc01"]
    svc = {(s["port"], s["proto"]): s for s in node["services"]}
    assert svc[(53, "udp")]["app"] == "dns"
    assert svc[(53, "udp")]["app_evidence_class"] == "observed"
    # 88/tcp is confirmed by the COMPOSITE `gssapi,smb,krb`... which names `krb`, not `krb_tcp`,
    # so the port convention stands and says `imputed`. Exactly the distinction the two classes
    # exist to keep legible.
    assert svc[(88, "tcp")]["app"] == "krb_tcp"
    assert svc[(88, "tcp")]["app_evidence_class"] == "imputed"
    assert svc[(389, "tcp")] == {"port": 389, "proto": "tcp", "evidence_class": "observed",
                                 "app": "ldap_tcp", "app_evidence_class": "observed"}
    # RDP was accepted but only bare `tcp` came back from the sensor.
    assert svc[(3389, "tcp")]["app_evidence_class"] == "imputed"
    # An ephemeral socket is OBSERVED-BUT-NOT-REPRODUCED: no app key at all, never a guess.
    assert svc[(49669, "tcp")] == {"port": 49669, "proto": "tcp", "evidence_class": "observed"}


def test_a_host_that_answered_nothing_carries_an_empty_list_not_a_missing_key():
    node = {n["id"]: n for n in build_topology_bundle(_PIT_14)["nodes"]}["address:9.9.9.9"]
    assert node["services"] == []


def test_observations_carry_the_dns_names_and_principals_the_store_actually_holds():
    obs = build_topology_bundle(_PIT_14)["observations"]
    name = obs["dns"]["names"][0]
    assert name["name"] == "axdc01.axellio.dom"
    assert name["entity"] == "hostname:axdc01" and name["locality"] == "local"
    # `referenced`, the class the PRODUCER wrote — not the `observed` the work order's sketch
    # guessed. A DNS binding is the capture seeing a NAME in an answer, not seeing the host.
    assert name["evidence_class"] == "referenced"
    # Aggregated per (name, entity): `ir_identity` carries one row per window, and a raw count
    # would report window coverage as query volume.
    assert name["rows"] == 2
    kinds = {p["kind"] for p in obs["identity"]["principals"]}
    assert kinds == {"kerberos", "ntlm"}
    # VERBATIM, realm suffix included: splitting it here would be the parse this block declines.
    assert any(p["identifier"] == "BW-L$/AXELLIO.DOM" for p in obs["identity"]["principals"])


def test_observations_omit_what_the_IR_cannot_carry_rather_than_fabricating_it():
    """`realm`, `spns`, `tls.sni`, `http.vhosts` and `smb.shares` are all named by the work
    order's §6.1 sketch and none of them crosses the IR boundary today. Absent keys, never
    empty or invented ones — an absent key is a readable statement, a fabricated value is not.
    """
    obs = build_topology_bundle(_PIT_14)["observations"]
    assert sorted(obs) == ["dns", "identity"]
    assert sorted(obs["identity"]) == ["principals"]


def test_the_observations_block_is_deterministic():
    a = build_topology_bundle(_PIT_14)["observations"]
    b = build_topology_bundle(_PIT_14)["observations"]
    assert a == b


# ---------------------------------------------------------------------------------------------
# `axr-topology/1.5` — the cast invocation
# ---------------------------------------------------------------------------------------------
#
# The defect this closes was found on axRange's Console apply path: a bundle could not
# reproduce its own cast. `--relocate` survived only as per-field `transformation:` records in
# the SIDECAR (a different file, recording what CHANGED rather than what was ASKED FOR) and the
# other flags survived nowhere, so a recompile driven from the bundle silently reproduced the
# SOURCE network's CIDRs -- a twin addressed into the lab it is deployed into, including its
# default gateway.

_INVOCATION_ARGS = dict(pit_id="pit-abc-1", imprint_id="corpapcn-golden",
                        relocate="10.200.0.0/16", max_count=None, chatter=True,
                        assertions=None, code_fingerprint="0123456789abcdef")


def test_the_invocation_block_carries_every_flag_that_changes_the_output():
    from aximprint.synth.imprint_bundle import build_cast_invocation

    block = build_cast_invocation(**_INVOCATION_ARGS)
    assert sorted(block) == ["assertions", "chatter", "code_fingerprint", "imprint_id",
                             "max_count", "pit_id", "relocate"]
    assert block["relocate"] == "10.200.0.0/16", "the field the defect was found through"
    assert block["pit_id"] == "pit-abc-1" and block["imprint_id"] == "corpapcn-golden"


def test_the_store_path_is_deliberately_not_recorded():
    """`--store` is a lab-local filesystem path: not portable to the consumer, not a property
    of the cast, and publishing it would put one host's directory layout into an artifact
    another repo serves over HTTP. The consumer supplies the store; this block says which PIT
    inside it."""
    from aximprint.synth.imprint_bundle import build_cast_invocation

    assert "store" not in build_cast_invocation(**_INVOCATION_ARGS)


def test_the_assertions_file_travels_as_a_BASENAME_not_a_path():
    """Same reason as the store, one notch weaker: the file is a real input whose identity
    matters, so the name travels and the directory does not."""
    from aximprint.synth.imprint_bundle import build_cast_invocation

    block = build_cast_invocation(
        **{**_INVOCATION_ARGS, "assertions": "/home/rocky/ir/corpapcn-golden.assertions.yaml"})
    assert block["assertions"] == "corpapcn-golden.assertions.yaml"
    assert "/" not in block["assertions"]


def test_chatter_records_the_EFFECTIVE_setting_not_the_switch():
    """`--no-chatter` is a negative flag with a default of OFF. A block that recorded the SWITCH
    would make the reader re-derive this CLI's defaults to know what actually happened."""
    from aximprint.synth.imprint_bundle import build_cast_invocation

    assert build_cast_invocation(**{**_INVOCATION_ARGS, "chatter": False})["chatter"] is False
    assert build_cast_invocation(**_INVOCATION_ARGS)["chatter"] is True


def test_an_absent_flag_is_null_rather_than_omitted():
    """A consumer reads one shape. `relocate: null` says "not relocated", which is a fact; an
    absent key would make it guess whether the producer knew."""
    from aximprint.synth.imprint_bundle import build_cast_invocation

    block = build_cast_invocation(**{**_INVOCATION_ARGS, "relocate": None, "max_count": None})
    assert block["relocate"] is None and block["max_count"] is None
    assert "relocate" in block and "max_count" in block


def test_the_block_reaches_the_emitted_bundle_under_its_own_key():
    from aximprint.synth.imprint_bundle import build_cast_invocation

    b = build_topology_bundle(_PIT_14, cast_invocation=build_cast_invocation(**_INVOCATION_ARGS))
    assert b["cast_invocation"]["relocate"] == "10.200.0.0/16"
    assert b["cast_invocation"]["code_fingerprint"] == "0123456789abcdef"


def test_a_bundle_built_with_no_invocation_omits_the_key_rather_than_nulling_it():
    """The opposite posture from `observations`, deliberately: an empty `observations` is a real
    answer ("this PIT holds no DNS names"), while an absent `cast_invocation` means "nobody told
    me how this was cast" -- a library caller rather than the CLI. `{}` could not tell "no
    flags" from "not recorded", and "no flags" is itself a reproducible invocation."""
    assert "cast_invocation" not in build_topology_bundle(_PIT_14)


def test_the_block_is_deterministic_so_two_identical_invocations_agree():
    """The whole bundle, not just the block: `code_fingerprint` moves with the SOURCE TREE, not
    with the run, so two casts from one tree are byte-identical and two from different trees
    differ exactly there -- which is the field working, not a determinism break (`LES-002`)."""
    import json

    from aximprint.synth.imprint_bundle import build_cast_invocation

    def once():
        return json.dumps(build_topology_bundle(
            _PIT_14, imprint_id="corpapcn-golden",
            cast_invocation=build_cast_invocation(**_INVOCATION_ARGS)),
            indent=2, sort_keys=True)

    assert once() == once()
