# Work order — the source side of three censuses: a measured A/B and a recorded ruling, not a correction

**Status:** **PARKED, 2026-09-03.** The convergence loop this A/B was designed to feed retired its
own objective the same day (`docs/CHANGELOG.md` 2026-09-03, "the convergence loop closes") — the
six-axis TVD comparison this document's experiment (§6) exists to move is no longer a convergence
target, only a tolerance-gated **behavioral** row in a wider fidelity spec. The question itself is
not resolved and is not wrong to ask again; it is simply not worth running now. **Relevant only if
that behavioral row fails its own bar** under the new golden-twin spec. Its **pre-delegated ruling
is withdrawn** along with the convergence objective it was written to serve — §7's "recorded
decision per census" did not happen, and nothing in this document should be read as decided.
Revisit from `wip/golden-twin` if and when the behavioral row needs it; nothing below is built.
Original framing, kept for the record: three censuses filter on `role` but not on `locality` on the
**source** side of an edge. Applying the filter is one line in each — and `LES-129` is exactly the
warning that fits: *a one-line change is a safety claim, not a convenience note; check what the one
line would let through.* **What this one lets through is the loss of a persona carrying 59% of the
twin's offered traffic.** The suite passes either way, so this needed a measured A/B against the
standing twin and a recorded ruling — not a blind correction, and not a quiet one.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** nothing yet. **If the ruling goes the "apply it" way, it supersedes a decision
already written into the code** — `local_population`'s docstring rules explicitly that
`role_edge_targets` should *not* filter its source side (§3). That is a `Supersedes:` entry to
write at ruling time, not a defect to fix in passing.

---

## 1. The three sites

All three are in `src/aximprint/synth/personas.py`, all three gate on "has a role" and none on
"is local", and in each the destination side has *already* been dealt with.

**`proto_weights` (`:351`)** — the emit ranking table.

```
src/aximprint/synth/personas.py:392-398
    for ed in pit.get("ir_edge") or []:
        base = role_of.get(ed.get("src_key"))
        if not base or not role_of.get(ed.get("dst_key")):
            continue
        …
        out[group][ed.get("proto")] += int(ed.get("flows") or 0)
```

Both sides test only `role_of`. The destination's two-branch locality handling
(`:374-388`, local weights the internal conversation, external weights what leaves via the sink)
is a *documented* decision; the source side is not mentioned.

**`role_edge_targets` (`:402`)** — the destination-role histogram the budget is spent against.

```
src/aximprint/synth/personas.py:436-445
    is_local = {e["entity_key"]: (e.get("locality") == "local") for e in ents}
    …
        src, dst = role_of.get(ed.get("src_key")), role_of.get(ed.get("dst_key"))
        if not (src and dst):
            continue
        if is_local.get(ed.get("dst_key")):
            out[src][dst] += 1
        else:
            out[src][SINK_ROLE] += 1
```

`is_local` is built and consulted for the destination only. The source key is never tested against
it.

**`serves_votes` (`:730-743`)** — the port→role peer-resolution table, built from the listen
census.

```
src/aximprint/synth/personas.py:731-741
    for e in ents:
        if not e.get("role"):
            continue
        for p in json.loads(e.get("listen_ports") or "[]"):
            …
            serves_votes[pn][e["role"]] += 1
```

Not an edge census, but the same shape of question and the same omission: every roled entity votes,
including the 233 the twin will never deploy.

---

## 2. The lineage

`3dedba8` (*"'roled' is not 'local' — weight only what a twin can place"*) established the
principle and applied it to the **destination** side of `proto_weights` and `role_edge_targets`.
Iteration 15 then completed it in **four more places** — the journal's own words: *"`3dedba8`'s
locality fix, completed in the four places it missed. Six sites now filter on locality rather than
on merely having a role."* Those six today:

```
personas.py:229    activity_bands       roster: roled AND local
personas.py:331    emit_budgets         source entity must be local (:339)
personas.py:436    role_edge_targets    DESTINATION only
personas.py:528    local_population     roled AND local, both sides
personas.py:535    local_population     the unplaceable-local set
personas.py:763    synthesize_personas  dial census: local members only
```

**These three are the remainder.** Two of them (`proto_weights`, `role_edge_targets`) are the
*same two functions* `3dedba8` touched, corrected on one side and not the other.

**One correction to the record, because this work order is where it would otherwise propagate.**
`activity_bands`' docstring says (`personas.py:215-216`) *"the residual of `3dedba8`, whose own
message says the fix belonged in four places and made it in two."* The commit message does not say
"four places". It says *"Both `proto_weights` and `role_edge_targets` now require a LOCAL
destination"* and, separately, of the fingerprint metrics, *"it should have been made in both
places at once."* Two pairs of two, paraphrased into one four. The paraphrase is a fair reading of
the commit's intent and a poor reading of its text, and `LES-130` is exactly this failure mode: an
ordinal in prose that nothing polices. Fix the docstring in whichever direction this work order is
ruled; do not carry the count forward unchecked.

---

## 3. What the codebase currently encodes — and it is prose, not a test

There *is* a ruling in the code, for one of the three, and it says **do not apply the filter**:

```
src/aximprint/synth/personas.py:522-524
    (`role_edge_targets` deliberately does NOT filter its source side — it answers a different
    question, "where did this ROLE's conversation go", and it is left exactly as it is.)
```

That is a real argument and it must be answered rather than stepped over. `role_edge_targets` is
consumed by `allocate_budget` to decide, per source role, *what proportion of a persona's emits go
where* — a proportion, not a count. If the proportion is genuinely a property of the ROLE rather
than of the twin's membership, an external `web-tier` host's outbound mix is legitimate evidence
about what `web-tier` behaviour looks like, and filtering it discards evidence for no gain.

The counter-argument is `3dedba8`'s own and it is equally live: a proportion measured over a
population 65% of which the twin does not contain is a proportion about a different network. The
commit's measured example is precisely this shape — aiming at 295 unplaceable `web-tier` edges
drove the twin to manufacture 225 local ones against a source with 3.

**Neither argument is settled by the code, and nothing checks either one.** `proto_weights` and
`serves_votes` carry no statement at all — their source side is unfiltered by omission, not by
decision. So the honest summary is: **one prose ruling, unenforced; two silent omissions; zero
tests that would fail if any of the three flipped.** A convention nobody checks is `LES-014`'s
*"a bound stated in a comment is not a bound."*

---

## 4. What applying the filter would cost — measured

Measured directly against the loop's source IR (`capcn-ir6.duckdb`, 356 roled entities, 123 of them
local), by running each census unmodified beside a source-filtered twin of it.

### `role_edge_targets` — totals collapse; proportions mostly do not

```
src_role                unfiltered   src-local     sink share: unfilt -> filtered
generic-endpoint               414         393     0.800 -> 0.789
web-tier                       226           5     1.000 -> 1.000
corp-workstation               112         107     0.723 -> 0.710
dns-resolver                    36          36     0.528 -> 0.528
domain-controller                9           9     0.667 -> 0.667
monitoring-collector             2           1     0.500 -> 0.000
file-server                      1           0     1.000 -> (role disappears)
hypervisor                       1           1     0.000 -> 0.000
log-shipper                      1           1     0.000 -> 0.000
```

**`web-tier` is the headline: 226 → 5.** 202 of this source's 204 `web-tier` entities are external
(re-verified: 204 roled `web-tier`, 202 with `locality != local`), so almost the entire histogram
for that role is authored by hosts the twin does not contain. **`monitoring-collector` 2 → 1, and
its sink share 0.500 → 0.000.** `file-server` disappears entirely — its single edge has an external
source. Every other role moves by less than 0.02 in proportion.

Re-run against the newer source IR (`t-ir.duckdb`, the build that carries the `listen_ports` /
`dial_ports` columns `capcn-ir6` predates): **the table is identical, row for row.** The result is
a property of the source, not of one materialize.

Note what the table does *not* show: `web-tier`'s sink share is 1.000 either way. The filter does
not change where `web-tier` is judged to talk; it changes how much evidence that judgement rests
on, from 226 rows to 5.

### `serves_votes` — one port's winner changes, and it loses its server entirely

Measured on the source IR that carries a listen census (`t-ir.duckdb`; the same 356/123 split):

```
port      unfiltered winner        source-local winner     votes
22/tcp    web-tier             ->  (nothing)               {web-tier: 3, bastion-jump: 2} -> {}
```

**Exactly one port flips, and it flips to nothing** — both voters are external, so under the filter
no role serves 22 and any emit at 22 has no peer to resolve to. Every other port keeps its winner
while its vote mass changes enormously (`443/tcp`: `web-tier` 199 votes → 1; `53/udp`:
`dns-resolver` 16 → 5). One tie is worth flagging for `docs/WORK_ORDER_windows_responder.md`,
which owns it: `389/tcp` is `{dns-resolver: 2, domain-controller: 2}` under both, decided by
`Counter.most_common`'s insertion order over an unordered DuckDB result — the filter does not cause
that hazard and does not fix it.

### `proto_weights` — not separately tabulated here, deliberately

Its source-side filter interacts with the ranking column, and
`docs/WORK_ORDER_proto_weight_rows.md` changes that column. Measuring the population change against
a ranking that is itself about to change would produce a number nobody can read afterwards. **See
§7 for the sequencing this forces.**

---

## 5. Why this is a decision and not a fix

**`cast-monitoring-collector` loses 1.147 MB/s of the twin's 1.931 MB/s of offered traffic — 59%
— and the loop's own measurement is that the persona disappears entirely.** The mechanism, pinned
against the source IR:

```
src (10.20.194.240, monitoring-collector, LOCAL)  -> log-shipper (local)   tcp   3 flows
src (10.20.194.240, monitoring-collector, LOCAL)  -> 23.142.248.9 (unroled, external)  ntp
src (10.20.194.240, monitoring-collector, LOCAL)  -> 23.186.168.125 (unroled, external) ntp
src (176.65.148.215, monitoring-collector, EXTERNAL) -> web-tier (external)  tcp  7 flows
src (176.65.148.215, monitoring-collector, EXTERNAL) -> 50.209.234.66 (unroled) tcp
```

The role has **one local member with exactly one roled destination** (`log-shipper`); its two other
edges go to unroled NTP servers and are excluded by the role gate either way. The **entire** sink
share comes from `176.65.148.215` — an external host — whose single roled-destination edge becomes
`SINK_ROLE` and produces the unfiltered 0.500. Filter the source side and the histogram is
`{log-shipper: 1}`: **the persona loses its sink emit, which is its only `peer_scope: all` fan-out
and where essentially all of its volume lives.**

**One place where this census-level A/B does not reproduce the loop's own result, and it is worth
resolving during the experiment rather than assuming.** At the census level the group does *not*
vanish: it survives `emit_budgets` (which is already source-filtered, `:339`) with a budget of 3,
and `role_edge_targets` leaves it with one target rather than none. So the measured 2 → 1 explains
the **traffic** loss cleanly and does not by itself explain a **persona** disappearing. Arm A
(§6) should record which of the two actually happens — a persona that vanishes and a persona that
survives with 0.001 MB/s are the same number on the offered-rate axis and very different facts
about the twin.

Two readings, and they are genuinely both available:

- **The persona is an artifact and losing it is the fix.** It is a whole behaviour class derived
  from one host the twin does not contain, which is `3dedba8`'s exact complaint with `file-server`
  (*"a whole budget group synthesized from a host that exists only on the internet"* —
  `personas.py:324-325`). By that reading, 59% of the twin's traffic is currently manufactured
  from external evidence and the offered rate is wrong by that much.
- **The persona is load the source really carries and losing it makes the twin quieter than the
  network it models.** Iteration 15 landed offered rate at **1.33 MB/s against the source's own
  1.330** — a 21.5× correction, and the closest match the loop has achieved on any volume axis.
  Removing 59% of offered traffic from that starting point is not obviously a move toward the
  source, and `_cadence_for`'s standing warning cuts both ways: a twin that floods its own sensor
  is not faithful, and neither is one that idles.

**Which reading is right is an empirical question the fingerprint can answer, and neither this
document nor the current code answers it.** That is the whole reason this is a work order and not a
commit.

---

## 6. The experiment

Stated as an experiment, with no predicted outcome, because a projection written into the work
order is the thing iterations 16 and 17 between them showed is hardest to read honestly afterwards.

1. **Baseline.** Re-fingerprint the standing twin with nothing changed, to establish that day's
   numbers rather than reusing iteration 18's. Every capture in this arc has been 300/300 windows,
   0 empty, 0 failed; anything else invalidates the comparison before it starts.
2. **Arm A — `role_edge_targets` source-filtered, alone.** Re-cast, install, `tfc deploy`, capture,
   fingerprint. This is the arm that removes `cast-monitoring-collector`.
3. **Arm B — `serves_votes` source-filtered, alone.** Same cycle. Expect the 22/tcp emits to have
   no peer; **decide and record what happens to an emit whose port no local role serves** — dropped,
   or resolved to a fallback. Today that case cannot arise, so there is no existing behaviour to
   preserve, and inventing one silently is `LES-006`.
4. **Arm C — `proto_weights` source-filtered, alone.** After
   `docs/WORK_ORDER_proto_weight_rows.md` has landed and been measured (§7).
5. **Offered rate on every arm**, not only the fingerprint axes. The 1.931 → 0.784 MB/s question is
   the one this work order exists to answer and it is not visible in any TVD.
6. **One arm per iteration.** Iteration 16 bundled two changes, moved five axes in both directions,
   and its reading of the result was wrong — established by iteration 17, which found the cause was
   neither of the two changes.

**Axes of record**, unchanged: `role_edges_local`, `external_dst_edge_share`, `role_edges`,
`fan_out_degree_local`, `fan_out_degree`, `role_census`. Iteration 18's values are the reference:
0.533 / 0.8641 / 0.878 / 0.280 / 0.419 / 0.033.

**Must stay `tfc`-tier.** `twin_config.yaml` byte-identical, verified per arm. All three censuses
feed the persona emit table, so this should hold — but `role_edge_targets` and `emit_budgets` also
influence which node groups exist, and a group that disappears from the *config* rather than from
the *profile* is a 138-VM rebuild. **Check, per arm, before deploying.**

---

## 7. The ruling to record

Whatever the arms show, the outcome of this work order is a **recorded decision per census**, in
the code, in the form the project already uses — a docstring paragraph naming the population, the
measurement behind it, and the date. Three separate rulings, because the three answer different
questions:

- **`role_edge_targets`** — a proportion. The existing prose ruling (§3) is the incumbent and wins
  by default; overturning it needs `Supersedes:` and a measurement.
- **`proto_weights`** — a ranking weight. Its destination side is already two-branch by decision;
  the source side should say something either way rather than remain silent.
- **`serves_votes`** — a peer-resolution table about *this twin*, which is the census where the
  "hosts the twin does not contain" argument is strongest and the measured cost is smallest (one
  port).

**And whichever way each goes, land a test.** The reason this document exists is that all three
sites pass the suite in both states — §3's ruling is prose that nothing enforces. A ruling with no
test is the same artifact this work order is written about, one iteration later.

---

## 8. What this deliberately does not do

- **It does not apply the filter.** Not as a "small consistency fix", not as a drive-by inside
  another change. The 59% number is the reason.
- **It does not change the fingerprint.** The `_is_local` override
  (`scripts/shape_fingerprint.py:51`) and the six axes stay exactly as they are; changing the ruler
  and the thing measured in one iteration is unreadable.
- **It does not touch the six sites that already filter.** They were measured and landed at
  iteration 15 and are not re-litigated here.
- **It does not fix the `389/tcp` tie or the unordered `serves` vote.** Measured in §4 and handed
  to `docs/WORK_ORDER_windows_responder.md` §4, which needs it for a different reason.
- **It runs after `docs/WORK_ORDER_proto_weight_rows.md`.** Both edit `personas.py:392-399`; they
  conflict textually and, worse, they conflict in interpretation — one changes what the ranking
  measures and the other changes which population it measures over. Arm C is explicitly gated on
  the other landing first.
- **No GAP-C number.** Consistent with the arc's accounting: iteration 15 deliberately left the
  allocator floor unnumbered and this is the same residual, still under measurement rather than
  shipped.
