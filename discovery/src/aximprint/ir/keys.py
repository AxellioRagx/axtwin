"""Cross-PIT entity identity — the natural key (`docs/WORK_ORDER_network_ir.md` D6, build step 2).

**The prerequisite for everything comparative.** `Q1` D3 made belief-store entity ids **run-scoped**:
`ent-0001` in Monday's run and `ent-0001` in Friday's name unrelated hosts. So two PITs cannot be
joined at all until an entity carries a key derived from something the network itself supplies. This
module derives it.

Pure by design — it takes identifiers and addresses and returns a key. It does not read the belief
store; that wiring is build step 3. Keeping it pure is what lets the precedence rules below be
tested against hand-written cases rather than against whatever a capture happened to contain.

**The key inherits the resolver's normalization and must not repeat it.** `windowing/entities.py`
already parses `WORKSTN1$`, `workstn1.corp.example.test` and DHCP option 12 down to one computer
name (`LES-107`); re-normalizing here would put that rule in two places, and two copies of a
normalization rule is how they drift. The consequence is worth stating loudly because it will not
be obvious later: **a change to `computer_name()` changes every key this module produces**, so PITs
written before and after such a change will not join even though both are schema-valid.
`ir_pit.code_fingerprint` is what lets a comparator notice it — a join across a build boundary is
a warnable event, not a silent one.
"""
from __future__ import annotations

from typing import Iterable, Optional

#: Identifier namespace -> `KEY_KINDS` value, **in precedence order**. First match wins.
#:
#: **Why `hostname` outranks `mac`, measured rather than assumed.** On the 2026-08-11 baseline, 19
#: of 25 bound entities carry a hostname, 6 carry only a mac, and 7 carry both — but the case that
#: decides it is `ent-0016`: one host, one computer name, **two MACs and two addresses** (a laptop
#: with wired and wireless NICs). Keyed on mac it would split into two entities across PITs
#: depending on which interface was active; keyed on hostname it stays one host, which is what it
#: is. The general form: a hostname survives a NIC change, a mac survives a rename, and NIC changes
#: — including a second NIC being used — are the commoner event.
KEY_NAMESPACES: tuple[tuple[str, str], ...] = (
    ("hostname", "hostname"),
    ("mac", "mac"),
)

#: Namespaces the resolver mints that may **never** key an entity — declined **by name, with a
#: reason**, the same discipline as `ingest/zeek/plugin.py::_SKIP` and `_SERVICE_NOT_PROJECTED`.
#: An omission and a decision look identical in code; only a table tells them apart.
NOT_A_KEY: dict[str, str] = {
    # `Q1` D1's whole point, one layer downstream: only HOST-scoped evidence may identify a host.
    # A Kerberos user principal identifies a PERSON. Keyed on it, every machine one administrator
    # logged into would collapse into a single entity across PITs — and would do so more
    # aggressively the more diligent the administrator.
    "principal": "a Kerberos user principal names a person, not a host (`Q1` D1, scope=user)",
    # A DNS answer binds a THIRD PARTY's address (scope=referenced). Many-to-many by construction,
    # and about a host the sensor never saw at that address.
    "dnsname": "a DNS answer is a third party's address (scope=referenced), not this host's name",
}


def _values(identifiers: Iterable[str], namespace: str) -> list[str]:
    """Every value in `identifiers` under `namespace`, sorted.

    Sorted, not first-seen: the roster hands these over as a JSON array whose order came from a
    `set`, and an output that depends on it would not be byte-stable across runs (`LES-002`).
    """
    out = []
    for raw in identifiers:
        ns, sep, value = raw.partition(":")
        if sep and ns == namespace and value:
            out.append(value)
    return sorted(out)


def unusable_namespaces(identifiers: Iterable[str]) -> dict[str, int]:
    """Namespaces present that cannot key an entity, counted — declined and unrecognized alike.

    Returned rather than logged so a caller can put the number in the artifact. A namespace this
    module has never heard of is counted here too: an identifier kind added upstream must surface
    as a number, not be silently ignored, which is the only way "the precedence table is complete"
    stays a checkable claim rather than a comment.
    """
    known = {ns for ns, _ in KEY_NAMESPACES}
    counts: dict[str, int] = {}
    for raw in identifiers:
        ns, sep, value = raw.partition(":")
        if sep and value and ns not in known:
            counts[ns] = counts.get(ns, 0) + 1
    return counts


def derive_entity_key(identifiers: Iterable[str],
                      addresses: Iterable[str]) -> Optional[tuple[str, str]]:
    """`(entity_key, key_kind)` for one entity, or `None` if nothing can key it.

    Precedence is `KEY_NAMESPACES` then the address fallback (`Q1` D4: an entity with no
    host-scoped evidence resolves to itself, so its address *is* its identity — a weak claim,
    labelled weak by `key_kind`, never silently equated with a hostname).

    **The key is self-describing** — `hostname:bw-l`, `mac:a0:…`, `address:10.80.72.13` — rather
    than a bare value beside a `key_kind` column. Redundant on purpose: consumers will join on
    `entity_key` alone because it is one column and it works, and a bare value lets a hostname
    that happens to look like an address join to one. Prefixing makes that mistake impossible
    instead of documenting it.

    `None` (no identifiers, no addresses) is a real state and the caller must count it rather than
    invent a key. A synthetic key would be indistinguishable in the artifact from a real one.
    """
    for namespace, kind in KEY_NAMESPACES:
        values = _values(identifiers, namespace)
        if values:
            # First of the sorted values, not a join of all of them: a key must be one identifier
            # so that an entity gaining a SECOND mac between two PITs still joins to itself.
            # `ent-0016` on the real baseline has two, and a composite key would make it a
            # different entity the moment either NIC appeared or stopped appearing.
            return f"{kind}:{values[0]}", kind
    addrs = sorted(a for a in addresses if a)
    if addrs:
        return f"address:{addrs[0]}", "address"
    return None


def find_collisions(keyed: dict[str, tuple[str, str]]) -> dict[str, list[str]]:
    """`{entity_key: [source_entity, …]}` for every key claimed by more than one entity.

    **Empty is the expected result, and a non-empty one must be disclosed, never deduplicated.**
    Two entities sharing a key means the artifact is about to describe two hosts as one, which is
    worse than describing one host as two: a merge destroys evidence a split merely fragments.

    The risk is inherited rather than introduced here. `LES-107`'s normalization deliberately
    strips the domain (`workstn1.corp.example.test` -> `workstn1`) so three sensor spellings unify,
    which means two hosts of the same short name in different domains collide — and they would
    already have collided *inside the resolver*, which merges on that same parsed name. So a
    collision here is a report about resolution, not about key derivation. Measured on the
    2026-08-11 baseline: **none** (25 entities, no shared hostname).
    """
    by_key: dict[str, list[str]] = {}
    for source_entity, (key, _kind) in sorted(keyed.items()):
        by_key.setdefault(key, []).append(source_entity)
    return {k: v for k, v in by_key.items() if len(v) > 1}
