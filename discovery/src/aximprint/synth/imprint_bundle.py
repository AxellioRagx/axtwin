"""Export the IR's topology as a bundle artifact axRange can serve — `docs/JOURNAL_corpapcn_fidelity.md` GAP-C11.

The Console's **Logical** view is the IR's L3 structure *plus the conversation graph*, and its
**Physical** view is the IR's *full observed node census from the tap vantage* — every node,
fully and partially discovered, of which a deployed twin is a scaled-down SUBSET (Authority: user,
2026-08-27).

Neither can be drawn from the `twin_config` a cast emits. That document is a deliberately **lossy
projection**: `cast` drops role-less hosts (D5) — which on the CorpAPCN run was 92% of placed
hosts before this loop's role work, and is still the majority of the *observed* population — and it
carries no traffic edges at all. Rendering it and calling it the imprint's topology would present a
subset as the superset, which is the specific error the restored semantics exist to prevent.

So the census and the edges are exported **separately**, as a third file beside the twin config and
the provenance sidecar in an imprint bundle:

    <id>.yaml                 twin_config      (the deployable projection)
    <id>.provenance.json      per-field provenance, `axr-cast-provenance/1`
    <id>.topology.json        THIS             (the observed superset + conversation graph)

All three now travel together: `scripts/install_cast_as_twin.py` copies the sidecar as well as
this file, which it did not until 2026-09-04 (`docs/WORK_ORDER_reference_ingest.md` D9).

axRange reads the bundle and never imports aximprint — the boundary Track C already established
(`CLAUDE.md`: the engine never imports from sibling projects). This module is the producing half
of that contract; nothing here knows anything about axRange.

Every row carries its `evidence_class`, so a consumer can render observed and imputed facts
differently rather than flattening them — the same discipline the rest of the IR keeps.

Since `1.5` the bundle also carries **`cast_invocation`** — the flags that produced it, so a
consumer can re-run the cast that made it rather than guess at one. See `build_cast_invocation`.
"""
from __future__ import annotations

import json
import os
from typing import Any

from aximprint.synth.services import services_for

#: The BUNDLE's own version — this document's shape, not the IR's.
#:
#: `docs/WORK_ORDER_reference_ingest.md` §7.4/D-e. Until 2026-09-04 the export republished the
#: **IR's** `schema_version` and had none of its own, so "the topology export goes to 1.4" either
#: bumped the IR — a different artifact, on a different lifecycle — or meant nothing at all. Both
#: keys are emitted now and they answer different questions: `schema_version` says which IR a
#: reader is looking at a projection of, `bundle_schema` says which projection.
#:
#: `1.4` landed `services[]` + `observations`, exactly as `1.3`'s own note said would bump it.
#:
#: **`1.5` (2026-09-05) adds `cast_invocation`** — the flags that produced this bundle. Found by
#: axRange's Console apply path: a bundle could not reproduce its own cast. `--relocate` survived
#: only as per-field `transformation:` records inside the SIDECAR (a different file, and a record
#: of what changed rather than of what was asked for) and `--max-count` / `--no-chatter` /
#: `--assertions` survived nowhere at all, so a recompile driven from the bundle silently
#: reproduced the SOURCE network's CIDRs. Additive: every 1.4 key is present and unchanged.
#:
#: MAJOR is what a reader refuses on (axRange `docs/WORK_ORDER_console_canvas.md` §9.1); a MINOR
#: it does not know carries keys it can ignore.
BUNDLE_SCHEMA = "axr-topology/1.5"


def build_cast_invocation(*, pit_id: str | None, imprint_id: str | None,
                          relocate: str | None, max_count: int | None, chatter: bool,
                          assertions: str | None, code_fingerprint: str | None) -> dict:
    """The `cast_invocation` block (`1.5`) — what `aximprint cast` was asked to do.

    **Every value is the EFFECTIVE one, not the flag.** `chatter` is `not --no-chatter`, and
    `imprint_id` is the resolved id rather than `None` when `--imprint-id` was omitted, because
    the block's job is reproducing the RESULT — a reader reconstructing the argv from a record of
    which switches were typed would have to re-derive every default this CLI holds.

    **Two deliberate omissions, and they are not symmetric.**

      * **The store path is not recorded.** `--store` is a lab-local filesystem path; it is
        neither portable to the consumer nor a property of the cast, and publishing it would put
        one host's directory layout into an artifact another repo serves over HTTP. The consumer
        supplies the store from its own configuration; this block says *which PIT inside it*.
      * **`assertions` is a BASENAME.** Same reason, one notch weaker: the file is a real input
        whose identity matters, so the name travels and the directory does not. The default path
        is beside the store, so a basename is exactly what a consumer needs to find it again.

    `code_fingerprint` is the build that CAST, taken from `provenance.build_stamp()` by the
    caller. It is deliberately not the PIT's own fingerprint — that one is already in the
    sidecar, and it names the build that MATERIALIZED the PIT, which is a different question from
    "what compiled this bundle". A consequence, stated rather than discovered: this value moves
    with any edit to this package's sources, so two casts from different trees differ here even
    when everything else about them agrees. That is the field working, not a determinism break —
    two casts from ONE tree agree, which is the invariant `scripts/verify_determinism.py` and
    `CONVENTIONS.md` §3 actually assert.

    Declared in this module rather than assembled inline in `cli.py` so the bundle's shape lives
    in one place with the rest of it, and so the basename rule above is a property of the block
    rather than of one call site.
    """
    return {
        "pit_id": pit_id,
        "imprint_id": imprint_id,
        "relocate": relocate,
        "max_count": max_count,
        "chatter": bool(chatter),
        # `os.path.basename` on the string, never `Path(...).name`: the value may be `None`, and
        # a `Path(None)` is a TypeError inside an exporter whose whole job is to be readable.
        "assertions": os.path.basename(assertions) if assertions else None,
        "code_fingerprint": code_fingerprint,
    }


def build_topology_bundle(pit: dict, *, imprint_id: str | None = None,
                          cast_invocation: dict | None = None) -> dict:
    """The `<id>.topology.json` document for one PIT.

    `nodes` is the FULL census — deliberately including entities with no role and no segment.
    Filtering them out here would reproduce the twin_config's lossiness in the very artifact that
    exists to correct it. A consumer that wants only placed hosts can filter; one that wants the
    superset cannot recover what was dropped.
    """
    pit_row = (pit.get("ir_pit") or [{}])[0]
    ents = pit.get("ir_entity") or []
    edges = pit.get("ir_edge") or []
    segs = pit.get("ir_segment") or []

    def _ports(raw: Any) -> list:
        try:
            return json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            return []

    # Inbound edge protocols per destination, for the `services[]` join below. Built once from
    # the edge list rather than re-scanned per entity: this is O(E + N) instead of O(E x N), and
    # on the CorpAPCN PIT that is 1,400 edges against 786 entities.
    inbound: dict[Any, list] = {}
    for ed in edges:
        inbound.setdefault(ed.get("dst_key"), []).append(ed.get("proto"))

    nodes = [{
        "id": e.get("entity_key"),
        "address": e.get("source_entity"),
        "locality": e.get("locality"),
        "segment": e.get("segment"),
        "segment_evidence_class": e.get("segment_evidence_class"),
        "role": e.get("role"),
        "role_confidence": e.get("role_confidence"),
        # `None` where no role was inferred — a supported outcome, not a gap. These are exactly
        # the hosts the twin_config drops and the Physical view is about.
        "role_evidence_class": e.get("role_evidence_class"),
        "windows_seen": e.get("windows_seen"),
        "listen_ports": _ports(e.get("listen_ports")),
        "dial_ports": _ports(e.get("dial_ports")),
        # `1.4`. The listen census JOINED to the protocol actually spoken on edges to that
        # listen, which is what lets a consumer pick *bind9* rather than *"something on 53"*
        # (axRange `docs/WORK_ORDER_protocol_faithful_services.md` §6.1). Empty list, not an
        # absent key, on a host that answered nothing — the shape a consumer branches on is the
        # port count, never the key's existence.
        "services": services_for(_ports(e.get("listen_ports")),
                                 inbound.get(e.get("entity_key"), [])),
    } for e in ents]

    conv = [{
        "src": ed.get("src_key"), "dst": ed.get("dst_key"),
        "proto": ed.get("proto"),
        # The conversation graph's weight. This is what an arc band / link width / sankey needs,
        # and its absence from the published DTO — not its absence from the IR — is what had the
        # Logical view unable to draw anything (the reason recorded as "the IR carries no traffic"
        # was wrong; `ir_edge` has carried these since the IR existed).
        "bytes": ed.get("bytes"), "flows": ed.get("flows"),
        "evidence_class": ed.get("evidence_class"),
    } for ed in edges]

    return {
        # This document's own version, distinct from the IR's `schema_version` passthrough
        # below. NOT "first" in the emitted file — `cli.py::cmd_cast` writes this bundle with
        # `sort_keys=True`, so declaration order here is not the file's order (the provenance
        # sidecar is written WITHOUT `sort_keys`, which is why the equivalent comment there can
        # say "first" and this one cannot).
        "bundle_schema": BUNDLE_SCHEMA,
        "imprint_id": imprint_id or pit_row.get("pit_id"),
        # `1.5`. ABSENT rather than `null` when the caller did not supply one, which is the
        # opposite posture from `observations` two keys down and deliberately so: an empty
        # `observations` is a real answer ("this PIT holds no DNS names"), while an absent
        # `cast_invocation` means "nobody told me how this was cast" — a bundle built by a
        # library caller rather than by the CLI. A consumer that found `{}` there could not tell
        # "no flags" from "not recorded", and "no flags" is itself a reproducible invocation.
        **({"cast_invocation": cast_invocation} if cast_invocation is not None else {}),
        "pit_id": pit_row.get("pit_id"),
        "as_of": pit_row.get("as_of"),
        "network": pit_row.get("network"),
        "vantage": pit_row.get("vantage"),
        "windows": pit_row.get("windows"),
        "schema_version": pit_row.get("schema_version"),
        "segments": [{
            "cidr": s.get("cidr"), "name": s.get("name"),
            "entities": s.get("entities"),
            "coverage_source": s.get("coverage_source"),
        } for s in segs],
        "nodes": nodes,
        "edges": conv,
        # `1.4`. The protocol CONTENT a server is configured FROM, as distinct from the port it
        # answers on. §6.1 of the work order; see `_observations` for what is here, what is
        # not, and why each absence is an absence rather than a gap.
        "observations": _observations(pit),
        # Stated, not implied: a consumer must be able to tell "the twin shows fewer hosts than
        # this" from "the capture saw fewer hosts", without recomputing either.
        "census": {
            "entities_total": len(nodes),
            "with_role": sum(1 for n in nodes if n["role"]),
            "placed": sum(1 for n in nodes if n["segment"]),
            "local": sum(1 for n in nodes if n["locality"] == "local"),
        },
    }


def _observations(pit: dict) -> dict:
    """The protocol CONTENT the twin's servers are configured from (`axrange/docs/
    WORK_ORDER_protocol_faithful_services.md` §6.1).

    `services[]` above says a host answers DNS on 53. This block says **what names** it answered
    for — and that difference is the whole reason row 5 exists: a detection rule matches a query
    name or an RC4 TGS-REP, never a port number, so filler bytes at the right port can never
    trigger one.

    **Only what the store holds, and each absence is an absence rather than a gap.** The work
    order's §6.1 sketch names five sub-blocks; two ship here and three do not, for two different
    reasons that a reader must be able to tell apart:

      * **`dns`** — SHIPS. A straight projection of `ir_identity` rows of kind `dns`, which the
        materializer already writes. No new parsing.
      * **`identity.principals`** — SHIPS. The same projection over the `kerberos` / `ntlm` /
        `dhcp` kinds, all of them `observed`.
      * **`identity.realm`** — ABSENT, and deliberately. There is no realm fact at the IR
        boundary: `windowing/entities.py`'s Kerberos parse drops it by design
        (`components = parts[:-1]`), and it survives only inside `Identifier.raw`, which has no
        column here. Making it first class is `docs/WORK_ORDER_reference_ingest.md` **S5**
        (`ir_policy(scope='domain', parameter='realm')`), and the services work order's §6.1
        says the same in its own words: *"`realm`, `spns` and `smb.shares` need §5.2's changes,
        so they are S4 inputs, not slice-1 ones."* Nothing is lost by the absence: the twin's
        forest is the LAB realm by ruling (D4), not the observed one.
      * **`identity.spns`** — ABSENT, same reason. `ingest/zeek/plugin.py::_kerberos` reads
        `ts`/`client`/`id.orig_h` and drops the `service` column, which is where
        `ldap/axdc01.axellio.dom` lives.
      * **`tls.sni` / `http.vhosts` / `smb.shares`** — ABSENT, and this one is worth stating
        precisely because the facts DO exist and are one carrier short. The Zeek parsers project
        all three into the BELIEF STORE today (`windowing/windows.py::SERVICE_PROJECTIONS`:
        `ssl -> server_name`, `http -> host`, `smb -> share_type/service`), and measured on the
        CorpAPCN store they are real content — 8 distinct `serves_ssl` server names, and
        `serves_http` binds `test-api.swlab.axellio.dom` to the twin's own `10.80.75.171`. What
        is missing is the IR carrier: `ir/materialize.py` reads only the `listens_on`,
        `dials_port`, `in_segment` and `has_role` predicates, so `serves_*` never crosses into a
        PIT and this function cannot see it. Adding one is an additive IR MINOR — a
        version-locked change on the one schema this project has ratified — and is therefore a
        decision, not an implementation call.

    A block with no content at all is emitted as `{}` rather than omitted, so a consumer reads
    one shape (`observations.dns.names` -> a list, possibly empty) and never has to branch on
    which keys a given cast happened to produce.
    """
    ents = {e.get("entity_key"): e for e in pit.get("ir_entity") or []}
    identity = pit.get("ir_identity") or []

    # DNS. Aggregated per (name, bound entity) because `ir_identity` carries one row per
    # observation window, so a raw row count would report window coverage as query volume.
    # `count` is therefore the number of IR ROWS the name/entity pair holds, named as such, and
    # `locality` rides along because it is what tells an internal name from an external one
    # without the consumer re-joining against `nodes[]`.
    dns: dict[tuple, dict] = {}
    for row in identity:
        if row.get("kind") != "dns":
            continue
        name = str(row.get("identifier") or "")
        if not name:
            continue
        key = (name, row.get("entity_key"))
        cur = dns.get(key)
        if cur is None:
            ent = ents.get(row.get("entity_key")) or {}
            dns[key] = {"name": name, "entity": row.get("entity_key"),
                        "locality": ent.get("locality"), "rows": 1,
                        # The store's OWN class, not the work order sketch's `observed`. A DNS
                        # binding is `referenced` — the capture saw the name in an answer, it
                        # did not see the host (`LES-006`: report the class the producer wrote).
                        "evidence_class": row.get("evidence_class")}
        else:
            cur["rows"] += 1

    # Identity principals: the non-DNS kinds, every one of them `observed`. Carried VERBATIM,
    # including the realm suffix inside the identifier, because that string is what the wire
    # held; splitting it here would be the parse this block declines to do above.
    principals: dict[tuple, dict] = {}
    for row in identity:
        kind = row.get("kind")
        if kind in (None, "dns"):
            continue
        ident = str(row.get("identifier") or "")
        if not ident:
            continue
        key = (kind, ident, row.get("entity_key"))
        if key not in principals:
            principals[key] = {"kind": kind, "identifier": ident,
                               "entity": row.get("entity_key"),
                               "namespace": row.get("namespace"),
                               "evidence_class": row.get("evidence_class")}

    return {
        "dns": {"names": sorted(dns.values(), key=lambda r: (r["name"], str(r["entity"])))},
        "identity": {"principals": sorted(
            principals.values(),
            key=lambda r: (r["kind"], r["identifier"], str(r["entity"])))},
    }
