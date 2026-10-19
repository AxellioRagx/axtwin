"""Deterministic extractors (v0): rule-shaped facts -> assertions. DESIGN_BRIEF §6.

Every learner — heuristic or ML — writes assertions in one uniform shape:
(subject, predicate, object, evidence_class, confidence, window, source).
evidence_class is observed | referenced | imputed; imputed never masquerades
as observed. ML annotators land later and use the same shape.
"""
from __future__ import annotations

from aximprint.catalog import match_role
from aximprint.windowing.entities import SCOPE_EVIDENCE_CLASS
from aximprint.windowing.segments import SEGMENT_EVIDENCE, evidence_key
from aximprint.windowing.windows import (
    IDENTITY_PROJECTIONS, LONGTAIL_ENTITY, SERVICE_PROJECTIONS, WindowResult,
)


def _a(subject, predicate, obj, confidence, window_start, source,
       evidence_class="observed") -> dict:
    return {"subject": subject, "predicate": predicate, "object": obj,
            "evidence_class": evidence_class, "confidence": confidence,
            "window_start": window_start, "source": source}


def extract_assertions(win: WindowResult) -> list[dict]:
    out: list[dict] = []
    ws = win.window_start
    # listening-service census: an accepted connection proves a real listener
    # (events.FLOW_ACCEPTED — established or established-then-reset, never refused)
    for ent, feats in win.nodes.items():
        listen_ports = feats.get("listen_ports", [])
        for lp in listen_ports:
            out.append(_a(ent, "listens_on", lp, 0.95, ws, "extractor:listen-census"))
        # Role inference (`WORK_ORDER_synth_v1.md` phase 2) — the join key a `tfc` profile's
        # `assignments[].role` addresses, and `twin_config`'s optional `nodes[].role`.
        #
        # **`imputed`, and this is the honest class rather than a cautious one.** Nobody
        # observed "this host is a domain controller"; what was observed is that it answered on
        # 88 and 389, and the role is this project's inference from that. `listens_on` above
        # stays `observed` because it is the observation. Getting this backwards is precisely
        # what CONVENTIONS §3 forbids, and it would propagate: a role reaches the twin config's
        # per-field provenance, so an inference published as an observation there is a fidelity
        # claim nothing supports.
        #
        # PER-WINDOW, not accumulated, and that is measured rather than assumed: on the
        # 2026-08-11 baseline every window of every domain controller carries both 88 and 389,
        # and only 7 of 1,363 entities with listen evidence lose ANY port to per-window
        # splitting. It is also the shape DESIGN_BRIEF §6 describes for the ML annotator that
        # will eventually write this same predicate ("role=db-tier, conf 0.87, evidence:
        # gnn@window-14") — one assertion shape, either origin.
        #
        # The PERSONA is deliberately not asserted. A persona is catalog content, resolved at
        # compile time from the same row; putting it in the store would mean re-ingesting a
        # capture to change which persona a role maps to.
        # GAP-L5: dial evidence joins listen evidence. A pure client accepts nothing, so a
        # listen census alone leaves it role-less — and `cast`'s D5 filter then drops it from
        # the twin entirely. `connect_ports` is what it DIALLED, which is the only evidence
        # such a host ever produces.
        connect_ports = feats.get("connect_ports", [])
        # The dial census, mirroring `listens_on` above. Asserted rather than left in
        # `node_features` because persona SYNTHESIS reads the IR, not the belief store: a twin
        # cast from real traffic should be driven by personas that emit the ports the network
        # was observed using, and that evidence has to survive materialization to get there
        # (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C9). `observed` — unlike the role inferred
        # from it, dialling a port is something the sensor genuinely saw.
        for cp in connect_ports:
            out.append(_a(ent, "dials_port", cp, 0.95, ws, "extractor:dial-census"))
        if (rule := match_role(listen_ports, connect_ports)) is not None:
            out.append(_a(ent, "has_role", rule.role, rule.confidence, ws,
                          "extractor:role-census", evidence_class="imputed"))
        if feats.get("tainted"):
            out.append(_a(ent, "tainted_window", ws, 0.9, ws, "extractor:alert-taint"))
        # service census (`Q27`): durable properties projected from ServiceEvents by
        # `windowing.SERVICE_PROJECTIONS`, which also declares each kind's predicate and
        # confidence — one table, so a kind cannot be accumulated under one name and asserted
        # under another. Confidence varies by evidence quality, not by convention: Zeek's own
        # census is near-certain, an observed handshake is strong, and a `software` banner is
        # self-reported and trivially spoofable, which is why it sits lowest.
        # Segment membership (`WORK_ORDER_synth_v1.md` phase 1) — what places a twin node on a
        # network. `segments` is only ever populated for a `local` entity, so the internal/
        # external partition is expressed by the ABSENCE of these assertions for the 2,995
        # internet peers rather than by a separate "is_external" claim nothing would consume.
        #
        # `SEGMENT_EVIDENCE` supplies both class and confidence from one row, the same
        # discipline as `SERVICE_PROJECTIONS`/`IDENTITY_PROJECTIONS` above and below. Note what
        # this makes possible: when a sensor declares no `local_nets`, every assertion here is
        # `evidence_class="imputed"` — **the first `imputed` row this project has ever
        # written.** The class has been in the vocabulary since v0 with no producer, so the
        # honesty claim it exists to support was untested end to end until this path ran.
        # Subscripted, not `.get`-ed: `evidence_key` returns one of the table's own two keys,
        # so a miss is a bug that must surface rather than a silently skipped assertion.
        ev_class, seg_conf = SEGMENT_EVIDENCE[evidence_key(feats.get("locality_imputed", False))]
        for seg in feats.get("segments", []):
            out.append(_a(ent, "in_segment", seg, seg_conf, ws,
                          "extractor:segment-census", evidence_class=ev_class))
        for kind, facts in sorted(feats.get("services", {}).items()):
            proj = SERVICE_PROJECTIONS.get(kind)
            if proj is None:
                continue        # accumulated by a build that knew the kind; this one does not
            _, predicate, confidence = proj
            for fact in facts:
                out.append(_a(ent, predicate, fact, confidence, ws,
                              f"extractor:service-census:{kind}"))
    # identity bindings observed this window (`IDENTITY_PROJECTIONS`, `windowing.windows` —
    # mirrors `SERVICE_PROJECTIONS` above one layer over, `M5` code review). A kind absent from
    # the table was already counted at accumulation time
    # (`WindowManager._add`/`health["unmodelled_identity_kinds"]`), so this loop trusts the
    # declared table rather than re-deriving a predicate/confidence from an inline literal —
    # the shape that let `ntlm` silently inherit `dns`'s 0.7 ("a THIRD PARTY's claim") when it
    # was added, contradicting its own docstring's "AT THE ADDRESS ITSELF" (`M4`, fixed there).
    for b in win.identity_delta:
        # `opened` only (`Q1`/`Q9`). A binding now reaches this loop up to three times — when it
        # opens, when supersession closes it, and once more at end of run (`change="final"`,
        # carrying the observation extent) — and all three describe ONE fact. Asserting on each
        # would triple-count a binding for no new evidence; `closed`/`final` change the interval,
        # which `identity_bindings` records, not the claim, which this does.
        if b["change"] != "opened":
            continue
        proj = IDENTITY_PROJECTIONS.get(b["kind"])
        if proj is None:
            continue     # unruled kind; already counted, never silently assigned a predicate
        predicate, confidence = proj
        # Subject is the ENTITY, not the address. `bound_dhcp` says a HOST has that mac — the
        # address is where it was seen, and it is preserved on the binding record itself
        # (`identity_bindings.ip`), never lost. Under D4 an unanchored address resolves to itself,
        # so on a vantage with no host-scoped evidence this is character-for-character what v0
        # emitted; every difference is a resolved entity.
        #
        # `evidence_class` comes from the binding's SCOPE, which is the whole point of classifying
        # evidence before weighting it: a DNS-derived binding is `referenced` — a claim about a
        # host the sensor never saw at that address — and this is the first code path in the
        # project that ever produces that class. Subscripted, not `.get`-ed: every scope comes
        # from `IDENTITY_SCOPES`' closed table, so a missing one is a bug that must surface.
        out.append(_a(b["entity"], predicate, b["bound_to"], confidence, ws,
                      f"extractor:identity-{b['kind']}",
                      evidence_class=SCOPE_EVIDENCE_CLASS[b["scope"]]))
    # Entity merges (`Q1`, D2): resolution is forward-only, so a merge learned now cannot rewrite
    # the windows that already closed under the old id — this assertion is what joins them, and
    # `store/belief_store.py::snapshot` is what applies it. Confidence comes from the SAME
    # `IDENTITY_PROJECTIONS` row as the binding that caused the merge, rather than a fresh literal
    # minted here: a merge is exactly as good as the evidence behind it, and a second, independent
    # confidence table for the same kinds is how the two drift apart (`M5`'s own finding, one
    # channel over). An unruled kind is skipped here as well — it was already counted once at
    # accumulation time, so counting it again would double-report one kind.
    for m in win.entity_merges:
        proj = IDENTITY_PROJECTIONS.get(m["evidence"])
        if proj is None:
            continue
        _, confidence = proj
        out.append(_a(m["from_entity"], "same_entity_as", m["to_entity"], confidence, ws,
                      f"resolver:{m['reason']}"))
    # conversation edges as allow-floor evidence (role-pair matrix comes later)
    for e in win.edges:
        if e["dst"] == LONGTAIL_ENTITY:
            continue
        out.append(_a(e["src"], f"talks_{e['proto']}", e["dst"], 0.99, ws,
                      "extractor:conversation"))
    return out
