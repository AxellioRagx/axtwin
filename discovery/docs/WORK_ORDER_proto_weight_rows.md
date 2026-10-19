# Work order — rank emits by the quantity the fingerprint counts, and rule on what decides the sink's role

**Status:** **DONE 2026-09-03.** Ruled 2026-09-02 (Authority: user — §4 now carries a "Ruling"
subsection: D1 = (a)+(b), D2 = no seam), built, and measured the same day as iteration 19 of the
CorpAPCN fidelity loop (`docs/JOURNAL_corpapcn_fidelity.md`) — the loop's own closing iteration
(`docs/CHANGELOG.md` 2026-09-03, "the convergence loop closes"). Outcome recorded in §6's new
"Outcome" note: **the target was hit and it cost a trade**, not a clean win — full axis table in
the journal.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** nothing at draft time; the ruling below now **supersedes** the
`dns-resolver`-before-`web-tier` declaration order `roles.yaml` has carried since synth v1 phase 2
(2026-08-14) — `roles.py:186-187`'s own comment called that within-band order "the author's
deliberate tiebreak." It also corrects a justification inside `proto_weights`, which `3dedba8` and
the iteration-18 alias table both left standing.

---

## 1. The gap, in two halves

The halves are separable, they are not independent, and **fixing only the first moves nothing**.
That is why they are one work order.

### Half one — the ranking column measures a different quantity than the axis it is aimed at

`proto_weights` (`src/aximprint/synth/personas.py:351`) ranks a group's candidate emit ports by
how much observed conversation each port's protocol carries. It accumulates **`ir_edge.flows`**:

```
src/aximprint/synth/personas.py:398
        out[group][ed.get("proto")] += int(ed.get("flows") or 0)
```

The fingerprint that grades the result counts **edge rows** — one row per `(src, dst, proto)` per
window, whatever its flow count. `role_edges`, `role_edges_local` and `fan_out_degree` all
increment by one per row (`scripts/shape_fingerprint.py:106` and its neighbours). DNS is many
small flows per row; TLS is fewer, fatter ones. **So the ranking is sorted on a quantity the score
does not use.**

The function's own docstring states the error in the one place that should have caught it
(`personas.py:369-372`, verbatim):

> *Weighted on `flows` rather than `bytes`: an edge row is one flow-shaped fact, and
> `fan_out`/`role_edges` both count edge rows, so flows is the quantity the fingerprint actually
> compares.*

"An edge row is one flow-shaped fact" is the conflation: a row is one flow-**shaped** fact and is
not one flow. The paragraph correctly rejects `bytes` and then picks the wrong one of the two
remaining columns. Note what is *right* in it and must survive any fix — the argument against
`bytes` ("a single bulk transfer above a hundred small conversations") is the same argument that
selects rows over flows, applied one column further.

### Half two — the catalog decides the sink's role by PRESENCE, and volume never enters

Even with the column fixed, `53/udp` still carries roughly **209 outbound emit rows** (measured at
iteration 18), so the sink sub-roles stay *dialled* on 53 and their responders keep binding it.
`ROLE_CATALOG` is **first-match-wins** (`src/aximprint/catalog/roles.py:294-297`), ordered
most-specific-first by `(-len(requires), -len(dials))` (`roles.py:193`), and the two rows that
matter are adjacent. Confirmed by importing the catalog rather than reading the file:

```
0  domain-controller  requires ['389/tcp', '88/tcp']
1  corp-workstation   requires ['3389/tcp']
...
5  dns-resolver       requires ['53/udp']       <- roles.yaml:156
6  web-tier           requires ['443/tcp']      <- roles.yaml:172
7  web-tier           requires ['80/tcp']       <- roles.yaml:187
```

Rows 5 and 6 tie on specificity (one required port each), so the tie is broken by declaration order
in `roles.yaml`, and **`dns-resolver` wins for any host that answers on both 53 and 443**. Volume
is not consulted at any point: `match_role` asks only whether the required ports are a subset of
the observed listen set.

This was **stated as a prediction before iteration 18's run and held**: the alias table put 168
outbound rows on 443 (0 before), the sink still inferred `dns-resolver`, and `role_edges` moved
only **0.887 → 0.878**. A protocol-mix fix cannot cross a first-match-wins boundary on its own.

---

## 2. The measurement

Measured against the loop's own source IR (`capcn-ir6.duckdb`, the CorpAPCN ten-day materialize),
running `proto_weights` unmodified beside a row-counting twin of it, then scoring both through
`observed_weight` (`personas.py:117`) so composite Zeek labels alias exactly as they do in
production (`quic,ssl` folds into `ssl`, `gssapi,smb,krb` into both `smb` and `kerberos`):

```
group                     443/tcp                53/udp             winner
                       rows    flows          rows    flows       rows | flows
generic-endpoint-heavy   158      540            26      870       443  |  53
generic-endpoint          17      113            17      346        53* |  53
generic-endpoint-light    11       15             4       36       443  |  53
corp-workstation          47      254             6      261       443  |  53
dns-resolver-heavy        15       22             6      650       443  |  53
web-tier                 183      834             6     1007       443  |  53
```

`*` a genuine tie at 17/17, decided by the dial-frequency tiebreak, not by the weight.

**53 wins on flows in every one of the six groups. 443 wins on rows in five of them and ties in
the sixth.** The orderings are not merely different, they are opposite everywhere the weight
decides anything — and the ratio on the heavy band is 33×: 443 carries 3.4 rows per flow-weighted
unit where 53 carries 0.03.

The mechanism this feeds is the one iteration 18 established, and it is the reason a ranking
change moves a role histogram at all: the cast writes a responder for exactly the ports its own
personas dial at a role (`responder.py:112 listener_ports`), so **the emit table decides the
served set, the served set decides the inferred role, and the inferred role relabels every edge
that lands on it.** In iterations 15-16 the sink served 80/443 and read as `web-tier`; after the
iteration-17 repair it served 53 and read as `dns-resolver`, collapsing
`generic-endpoint->web-tier` from 315 to 3.

---

## 3. Goal

**The sink re-infers as `web-tier`, and `role_edges` falls from 0.878.**

That is the target because it is what the source actually is: **202 of this source's 204
`web-tier` entities are external** — `3dedba8`'s own measurement, re-verified directly against
the source IR (204 `web-tier` entities, 202 of them `locality != local`; the next largest role,
`generic-endpoint`, is 118 with only 15 external). The sink stands in for
"everything outside", and what is outside this network is overwhelmingly web. A sink that reads as
`dns-resolver` mislabels the twin's single largest edge class against a source whose DNS is 17 of
711 outbound rows.

---

## 4. The decision — what should decide the sink's role

**This work order deliberately does not answer this.** Four mechanisms would each produce the
target, they have different blast radii, and one of them is not a local change at all.

### D1 — Which of these, and in what combination

**(a) Fix the ranking column only.** `proto_weights` accumulates `1` per row instead of
`ed.get("flows")`, and the docstring paragraph at `:369-372` is rewritten to say why rows, not
flows. Smallest possible diff; entirely inside the synth layer; nothing outside the persona
emit table changes. **On its own it is measured not to be sufficient** — half two stands, and
iteration 18 already ran the experiment (a 168-row swing onto 443 with the catalog untouched moved
`role_edges` by 0.009).

**(b) Reorder the catalog** so `web-tier` precedes `dns-resolver`. Two lines in `roles.yaml`.
**This is the option that is not a local change.** `ROLE_CATALOG` is the project's single role
inference table, read by `learners` for inference on every capture and by `synth` for compilation
(`docs/CONVENTIONS.md` §3 — `catalog` sits below every layer precisely so both can read one copy).
Reordering it changes how **every** host that answers on both 53 and 443 is roled, on every
network this project ever ingests, forever — not only this twin's sink. On the source itself the
change is small (16 of 18 port-53 listen votes are `dns-resolver`), but "small on the one baseline
we have" is not the same claim as "safe", and `roles.py:186-187` records that within-band order is
the author's deliberate tiebreak, i.e. the current order is a decision, not an accident.

**(c) Require more than a single port to match.** A `dns-resolver` row requiring, say, `53/udp`
**and** `53/tcp` no longer matches a sink that only answers 53/udp. **Note the second-order effect,
which is not obvious from the row alone:** the catalog sorts on `-len(requires)` first
(`roles.py:193`), so lengthening a row's `requires` also **moves it earlier** in the table. Adding
a port to `dns-resolver` would promote it above every other single-port row while making it harder
to match — two changes at once, in opposite directions. `domain-controller` at index 0 is the
existing precedent for the shape and its comment (`roles.yaml:96-99`) is the argument for it:
*"Either alone is weak … but a host doing both IS the domain's KDC."* Whether the same is true of
DNS is a content question about the catalog, answerable from the baseline's listen census.

**(d) Some combination.** (a) is a correctness fix on its own terms and is defensible whatever is
decided about the rest; (b) and (c) are alternative answers to the same question and probably
should not both be taken.

**What the ruling needs to say:** which mechanism decides a role when two rules tie on
specificity — **presence of a distinguishing port** (c), **declared precedence** (b), or nothing
(a, leaving the sink `dns-resolver` and the goal in §3 unmet). The loop can measure any of them;
it cannot decide which the catalog is *for*.

### D2 — If (b) or (c) is taken, does the change apply to the SINK only or to the catalog?

The sink's sub-roles are `SYNTHETIC_ROLES` (`roles.py:279`), held out of `ROLE_CATALOG` by
construction so `match_role` can never return them. But the sink is **re-inferred from the twin's
own capture like any other host**, through the ordinary catalog — that is the entire point of the
round trip. There is no seam at which the sink could be given its own precedence rules without
inventing one, and inventing one would mean the twin's role inference no longer matches the
source's. **Recommendation: no seam. Whatever is ruled applies to the catalog.** Recorded as a
decision so it is not silently re-opened when (b)'s blast radius comes up.

### Ruling (2026-09-02, Authority: user)

**D1 = (a) + (b). D2 = no seam.** Option (c) — a `dns-resolver` row requiring `53/udp` **and**
`53/tcp` — was built, measured against the real source, and **not taken**. The census that decided
against it: 13 of the source's 15 `dns-resolver` hosts carry `{53/udp}` as their **entire** listen
evidence (only the two AD-integrated DNS hosts on `10.80.20.0/24` show `53/tcp` alongside it), so a
two-port `requires` row would strip the reference's own resolvers from 15 down to 1 — and the
twin's four real resolvers fare no better, since no cast persona dials any of them over TCP at all.
After the responder re-install the sink's 8 responder instances bind `53/tcp` (a responder serves
whatever its role's personas dial), so **8 of the 15 sink sub-role hosts would still read
`dns-resolver`** even under (c). This section's own "16 of 18 port-53 listen votes" estimate did
not reproduce against the real measurement: the closest reading is **15 of 16** distinct port-53
hosts, or **107 of 114** entity-windows.

(a) alone was reconfirmed, again, not to be sufficient on its own — see the journal's iteration 18
prediction, held again by iteration 19's own re-score.

**D2 taken as recommended: no seam.** The reorder applies to `ROLE_CATALOG` itself, not to a
sink-only exception — the sink is re-inferred through the ordinary catalog like any other host, and
a private precedence for it would stop the twin's inference matching the source's.

**Built.** `roles.yaml`'s `web-tier` row (`requires: ["443/tcp"]`) is declared ahead of the
`dns-resolver` row (`requires: ["53/udp"]`). `ROLE_CATALOG`'s post-sort order at the affected
indices: **5 `web-tier` (443)** <- `roles.yaml:156`, **6 `dns-resolver`** <- `roles.yaml:199`, 7
`web-tier` (80) <- `roles.yaml:219` (§1's indices 5/6 above describe the order BEFORE this ruling;
`roles.yaml` grew in the meantime, so `:156` there no longer names the row it named at draft time).
A brute-force sweep of the catalog's rule against every one of the source's `2**16` observed-listen-set
subsets confirms the reorder flips exactly the twin's 15 sink hosts and zero source hosts.

**The accepted wrinkle**, recorded beside the row in `roles.yaml`'s own comment: a lower-confidence
row (`web-tier`, 0.6) now outranks a higher-confidence one (`dns-resolver`, 0.7) on this one
specificity tie — confidence describes what a row asserts once matched, and has never been the
tiebreak; making it one would hand the sink back to `dns-resolver` by the back door.

---

## 5. Build steps

Contingent on §4. Steps 1 and 5 hold under every option.

1. **Fix the column** (D1a): `personas.py:406` (`:398` at draft time — the docstring rewrite below
   grew by 8 lines) accumulates rows; `:369-380` (`:369-372` at draft time) rewritten to state the
   rows-vs-flows distinction and keep the surviving argument against `bytes`. A unit test that
   fails on the flows spelling — a fixture where the two orderings disagree, which the §2 table
   shows is the ordinary case and not a contrived one.
2. **Apply the ruling** (D1b and/or D1c) in `roles.yaml`, with the reason in the row's own comment
   block, in the style the file already uses.
3. **Re-cast, install, `tfc deploy` against the standing twin.** No `te` step — see §7.
4. **Capture and fingerprint** through `scripts/compare_twin_to_source.sh`, 10-minute span, the
   same 60s ingest window every iteration has used.
5. **Record in the journal** as iteration 19: the axis table, whether the sink re-inferred, and —
   whichever way it goes — how §2's projection compared to the measurement. Iteration 17's
   correction is the standing instruction here: a projection disagreeing with a measurement is a
   question about which of the two is wrong, and the default assumption went the wrong way once
   already in this loop.

---

## 6. Acceptance

**Target.** The sink's entities re-infer as `web-tier`, and `role_edges` improves from 0.878.

**Must not regress** (iteration 18's measured values, the best of the arc on the two axes that
decide it):

```
role_edges_local TVD          0.533
external_dst_edge_share       0.8641      (source 0.8476)
fan_out_degree_local TVD      0.280
```

`role_edges_local` is the axis iteration 15's retraction established as the one that decides
convergence; `external_dst_edge_share` is the axis the sink exists to move. A run that hits the
target and loses either is not a pass — it is a trade, and it gets recorded as one rather than
reported as a win.

### Outcome (2026-09-03)

**The target was hit and it cost a trade, exactly per this section's own rule — not a pass.**
`role_edges` improved 0.878 -> **0.406** and the sink re-infers `web-tier` (14 of 15 sink hosts).
Two of the three must-not-regress axes regressed: `role_edges_local` 0.533 -> **0.593** (back near
iteration 17's 0.589) and `external_dst_edge_share` 0.8641 -> **0.8153**, moving AWAY from the
source's 0.8476 rather than toward it. `fan_out_degree_local` held roughly flat, 0.280 -> 0.290.
Full detail, the projection-vs-measurement check, and the resolver-census finding:
`docs/JOURNAL_corpapcn_fidelity.md`, "Iteration 19." This outcome is the trigger for
`docs/CHANGELOG.md` 2026-09-03 "the convergence loop closes" — the loop's own objective is retired
on the strength of this result plus the five iterations before it, not tuned again.

**Must stay `tfc`-tier.** `twin_config.yaml` **byte-identical**, verified per iteration and not
assumed — the standing 138-VM twin (`corpapcn-twin-0e4f7d58`) has carried iterations 16, 17 and 18
this way, and a roster change forces a full 138-VM rebuild. `proto_weights` feeds the persona emit
table only; a catalog `role:` change under D1b/c must be checked against the compiler's node
roster, because a role change that reaches `twin_config` is a redeploy, not an iteration.

**Suite.** Tier 1 green. `scripts/check_role_fixed_point.py` must not gain a new non-fixed-point
row: if D1c adds a `requires` port, that port must be served by something the row installs, or the
role stops being re-inferable and the change has traded one attribution defect for another.

**Determinism.** Two casts of one PIT byte-identical (`scripts/verify_determinism.py`, tier 2).

---

## 7. What this deliberately does not do

- **No `te`-tier change, and no redeploy.** The whole arc since iteration 16 has run against one
  standing twin, which is what makes the iterations comparable at all. Anything requiring a new
  `twin_config` is a different work order.
- **No responder change.** Iteration 17's durable rule — *emit ports and responder ports derive
  from the same census and must move together* — is honoured by re-casting and re-installing
  together, not by editing the responder. **But note the standing divergence it recorded:** 19
  guests on the current twin run responder text that differs from their persisted
  `script_content`, so a `te deploy --update` would read those ops as unchanged when they are not.
  Verify listeners **in-guest** with `ss -lnH -tu` after any install, never from
  `systemctl is-active` — the trap this journal already records, where a responder thread died on
  EADDRINUSE while its unit reported active and its op PASSED.
- **No new fingerprint axis.** The axes are fixed for the arc; adding one mid-loop makes the
  trajectory table unreadable.
- **No attempt at the Windows-role hole.** `docs/WORK_ORDER_windows_responder.md` owns it. It is
  the larger effect on `role_edges` (roughly a third of the source's local edge mass against this
  one's several hundred relabelled edges) and it is a **structural** blocker rather than a ranking
  one, so the two must not be run in the same iteration or neither is attributable.
- **No source-side locality correction.** `docs/WORK_ORDER_source_locality_filters.md` owns it,
  and it touches `proto_weights` — the same function this work order edits. **They conflict at
  `personas.py:392-399` and must be sequenced, not merged.** This one first: it changes what the
  ranking measures, which is a prerequisite for reading what the population change does to it.
- **No GAP-C number.** Consistent with iteration 18's own accounting: the alias table was a fix
  and not a gap, and so is this.
