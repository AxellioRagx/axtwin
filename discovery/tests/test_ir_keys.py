"""Cross-PIT entity identity (`docs/WORK_ORDER_network_ir.md` D6, build step 2).

Synthetic identifiers only (`LES-003`); the *shapes* mirror what the real roster holds
(`hostname:bw-l`, `mac:a0:d3:…`), never a real capture's contents.

What is being defended: that two PITs of the same network join on the same hosts, that they do
**not** join two different hosts into one, and that the identifier kinds which must never key an
entity are refused by name rather than by nobody having thought of them.
"""
import pytest

from aximprint.ir import (
    KEY_KINDS, KEY_NAMESPACES, NOT_A_KEY, derive_entity_key, find_collisions,
    unusable_namespaces,
)


# -- precedence ------------------------------------------------------------------

def test_a_hostname_wins_over_a_mac():
    key, kind = derive_entity_key(["mac:a0:d3:c1:f9:b1:40", "hostname:bw-l"], ["10.80.78.114"])
    assert (key, kind) == ("hostname:bw-l", "hostname")


def test_a_host_with_two_nics_keys_on_its_NAME_not_either_mac():
    """The measured case that decides the precedence — one host, one name, two MACs, two
    addresses (a laptop, wired + wireless). Keyed on mac it would split in two across PITs
    depending on which interface was active."""
    key, kind = derive_entity_key(
        ["hostname:bw-mbp15", "mac:60:f8:1d:c7:a3:40", "mac:ec:1a:59:3d:5c:45"],
        ["10.80.20.169", "10.80.20.174"])
    assert (key, kind) == ("hostname:bw-mbp15", "hostname")


def test_a_mac_only_entity_keys_on_the_mac():
    """6 of 25 bound entities on the real baseline have no hostname at all."""
    key, kind = derive_entity_key(["mac:a0:d3:c1:f9:b1:40"], ["10.20.193.4"])
    assert (key, kind) == ("mac:a0:d3:c1:f9:b1:40", "mac")


def test_an_unanchored_entity_falls_back_to_its_address():
    """`Q1` D4: with no host-scoped evidence an entity resolves to itself, so the address IS
    its identity — a weak claim, and `key_kind` is what says so."""
    key, kind = derive_entity_key([], ["10.80.72.13"])
    assert (key, kind) == ("address:10.80.72.13", "address")


def test_nothing_at_all_yields_none_rather_than_a_synthetic_key():
    """A synthesized key would be indistinguishable in the artifact from a real one."""
    assert derive_entity_key([], []) is None


def test_every_kind_the_schema_declares_is_reachable():
    """A `KEY_KINDS` entry no derivation can produce is a vocabulary that lies."""
    produced = {derive_entity_key(["hostname:h"], [])[1],
                derive_entity_key(["mac:m"], [])[1],
                derive_entity_key([], ["1.2.3.4"])[1]}
    assert produced == set(KEY_KINDS)


# -- determinism -----------------------------------------------------------------

def test_the_key_does_not_depend_on_identifier_order():
    """The roster serializes these from a `set`; an output that depended on that order would
    not be byte-stable across runs (`LES-002`)."""
    a = derive_entity_key(["mac:bb", "mac:aa", "hostname:zz"], ["9.9.9.9", "1.1.1.1"])
    b = derive_entity_key(["hostname:zz", "mac:aa", "mac:bb"], ["1.1.1.1", "9.9.9.9"])
    assert a == b


def test_a_second_mac_appearing_does_not_change_the_key():
    """A composite key would make an entity a different entity the moment either NIC started
    or stopped appearing — which is exactly what breaks a cross-PIT join."""
    one = derive_entity_key(["mac:aa:aa"], ["10.0.0.1"])
    two = derive_entity_key(["mac:aa:aa", "mac:bb:bb"], ["10.0.0.1"])
    assert one == two


def test_an_address_only_entity_is_stable_when_a_second_address_appears():
    assert (derive_entity_key([], ["10.0.0.1"])
            == derive_entity_key([], ["10.0.0.9", "10.0.0.1"]))


# -- what may never key an entity ------------------------------------------------

def test_a_kerberos_user_principal_never_keys_an_entity():
    """`Q1` D1 one layer downstream: a user principal names a PERSON. Keyed on it, every
    machine one administrator logged into collapses into a single entity — and does so more
    aggressively the more diligent the administrator."""
    assert derive_entity_key(["principal:admin@CORP.TEST"], ["10.0.0.1"]) \
        == ("address:10.0.0.1", "address")


def test_a_dns_name_never_keys_an_entity():
    """A DNS answer binds a third party's address (scope=referenced), many-to-many."""
    assert derive_entity_key(["dnsname:www.example.test"], ["10.0.0.1"]) \
        == ("address:10.0.0.1", "address")


def test_the_refusals_are_declared_by_name_with_a_reason():
    """An omission and a decision look identical in code; only a table tells them apart."""
    assert set(NOT_A_KEY) == {"principal", "dnsname"}
    assert all(reason.strip() for reason in NOT_A_KEY.values())


def test_a_declined_or_unknown_namespace_is_counted_not_ignored():
    """An identifier kind added upstream must surface as a number. This is what keeps "the
    precedence table is complete" a checkable claim rather than a comment."""
    counts = unusable_namespaces(
        ["hostname:h", "principal:p@X", "dnsname:d", "somethingnew:v", "somethingnew:w"])
    assert counts == {"principal": 1, "dnsname": 1, "somethingnew": 2}


def test_a_namespace_that_can_key_is_not_counted_as_unusable():
    assert unusable_namespaces(["hostname:h", "mac:m"]) == {}


def test_every_declined_namespace_is_absent_from_the_precedence_table():
    """The two tables must not overlap, or precedence would silently win over a refusal."""
    assert not set(NOT_A_KEY) & {ns for ns, _ in KEY_NAMESPACES}


# -- malformed input -------------------------------------------------------------

@pytest.mark.parametrize("bad", ["hostname", "", ":", "hostname:"])
def test_an_identifier_without_a_usable_value_is_skipped(bad):
    """Not crashed on, and not turned into an empty key."""
    assert derive_entity_key([bad], ["10.0.0.1"]) == ("address:10.0.0.1", "address")


def test_an_empty_address_is_not_a_key():
    assert derive_entity_key([], ["", "10.0.0.1"]) == ("address:10.0.0.1", "address")


# -- collisions ------------------------------------------------------------------

def test_no_collision_is_the_expected_result():
    keyed = {"ent-0001": ("hostname:a", "hostname"), "ent-0002": ("hostname:b", "hostname")}
    assert find_collisions(keyed) == {}


def test_two_entities_sharing_a_key_are_reported_never_merged():
    """A merge destroys evidence a split merely fragments, so this must surface. The risk is
    inherited from `LES-107`'s domain-stripping normalization, not introduced here — and the
    resolver would already have merged them on the same parsed name."""
    keyed = {"ent-0001": ("hostname:workstn1", "hostname"),
             "ent-0007": ("hostname:workstn1", "hostname"),
             "ent-0003": ("mac:aa", "mac")}
    assert find_collisions(keyed) == {"hostname:workstn1": ["ent-0001", "ent-0007"]}


def test_collision_reporting_is_order_independent():
    a = find_collisions({"z": ("k", "mac"), "a": ("k", "mac")})
    b = find_collisions({"a": ("k", "mac"), "z": ("k", "mac")})
    assert a == b == {"k": ["a", "z"]}


def test_a_prefixed_key_cannot_collide_across_kinds():
    """Why the key is self-describing rather than a bare value beside a `key_kind` column:
    consumers will join on `entity_key` alone because it is one column and it works."""
    host = derive_entity_key(["hostname:10.0.0.1"], [])[0]
    addr = derive_entity_key([], ["10.0.0.1"])[0]
    assert host != addr
    assert find_collisions({"a": (host, "hostname"), "b": (addr, "address")}) == {}
