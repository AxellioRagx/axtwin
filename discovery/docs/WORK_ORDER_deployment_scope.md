# Work order — deployment scope: the cast describes, the deploy selects

**STATUS:** **OPEN, drafted 2026-09-04.**
**Authority:** **user**, 2026-09-04 — axRange `docs/design/13-golden-twin-fidelity.md` §3.4 and
`docs/CHANGELOG.md` same date: *"the cast should emit everything and carry an indicator that lets
axRange's deploy ignore nodes."* The occupancy-not-confidence gate and the 0.8 default were
proposed by implementation and accepted the same day. This draft — the block's placement, the
grouping change, and §8's options — is **implementation (Claude Code)**, overrulable.
**Genre:** work order (`docs/CONVENTIONS.md` §2). Companion to
`docs/WORK_ORDER_reference_ingest.md`; the two share the `population` block (§5).
**Depends-on:** nothing. Sequenced **before** the first golden (spec 13 §7), unlike reference
ingest.
**Blocks:** spec rows **1 (asset)** and **2 (topology)** for the fresh cast — the user's ruling is
that these are closed by this change, *not* by assertions.
**Supersedes:** `docs/WORK_ORDER_visibility_and_vantage.md` **D5/D5a** (role-bearing nodes only)
and **D11** (`--min-occupancy` as a compile-time filter) — both become **annotation**. D6
(disclosure) and D9 (the IR never filters) are untouched and are what this rests on.

---

## 1. What this actually is

The cast's two compile-time filters are policy decisions taken in the wrong place. `--min-occupancy`
threads a *deployment* question — how much of the observed population fits and is worth standing up
— into the artifact that describes the network, and the role-bearing filter throws away hosts on
the strength of an inference that failed. D11's own docstring already argued the principle:
*"one wide PIT serves many thresholds"* (`synth/compiler.py:144-147`). It just applied it one layer
too high. **One cast now serves every threshold**, and the gate moves to the deploy, where the
capacity question actually lives.

## 2. Measured 2026-09-04, before designing anything

Read-only against `/home/rocky/corpapcn-fidelity-artifacts/src-ir-rematerialized-20260902.duckdb`
(11 windows), which is the fresh cast's own source:

| | today | emit-all + a 0.8 deploy gate |
|---|---|---|
| observed hosts emitted by the cast | 122 (of 141 placed; 19 role-less dropped) | **141** |
| hosts a deploy would stand up | 122 | **64** (`windows_seen ≥ 8.8`), of which **5** are role-less |
| segments emitted | 26 (6 pruned) | **32** |
| segments with ≥ 1 deployable host | 26 | **23** |
| node groups, keyed `(role, segment)` | 37 | 43 emit-all → **87** once keyed on occupancy too (§3 D3) |

Host counts exclude the 15 synthetic internet-sink hosts and the gateway, which are unaffected
throughout (D5). The group row is measured **without** the activity band, which the shipped
compiler also keys on (`compiler.py:224-229`) — it is the *ratio* that matters: adding occupancy to
the key roughly doubles the group count, whatever the band does.

Three readings worth stating before any code. **The deployed twin gets smaller, not bigger** —
64 hosts against today's 122 — because the gate is a real bar and most of this capture's population
is transient (14 hosts appear in one window of eleven). **Row 1 gets its five hosts back**: the
high-occupancy role-less hosts the cast drops today are exactly what the spec names. And **six
segments return**, which is row 2's gap.

## 3. Decisions

| # | Decision | Ruling (proposed) |
|---|---|---|
| **D1** | Do the two filters become annotation, or optional? | **Annotation. Both filter code paths go.** `synth/compiler.py:181-190` (the occupancy filter) and `:200-201` (`roleless = [...]; placed = [...]`) are deleted, and **`cast --min-occupancy` is removed** (`cli.py:878`), not kept as a no-op — two places to set one policy is the drift trap this change exists to close, and a flag that silently does nothing is worse than one that is gone. `--max-count` is a different question (scale-down, not scope) and **stays**. |
| **D2** | Where does the block go? | **On every emitted node and every emitted segment**, as a sibling of `name:`/`role:`, using the names the axRange gate is being drafted against: `observation: {windows_seen, windows_total, occupancy, role_confidence}`. `occupancy` is emitted as the computed `windows_seen / windows_total` rather than left to the reader, so the gate and the scorecard cannot disagree about rounding. |
| **D3** | A node group has many members — whose `windows_seen`? | **The group key gains `windows_seen`**, so every member of a group shares one, and the block is an honest scalar rather than an aggregate. Groups go `(role, band, segment)` → `(role, band, segment, windows_seen)`; the node name gains a `-w<N>` element. Measured cost: 43 → 87 groups on this capture (§2). **The alternative — keep the group and publish an occupancy histogram, letting the gate reduce `count:` — is rejected on a hard downstream fact**: changing the threshold would then *mutate an existing node's `count:`*, which `axrange te deploy --update` refuses outright (`docs/WORK_ORDER_twin_incremental_update.md` §3.1 hard-refuses in-place field mutation), so a threshold change would force a full redeploy. Splitting makes a threshold change an add/remove of whole node groups, which `--update` handles. **Seam item** (§7) if axRange prefers the histogram. |
| **D4** | What role does a role-less host carry? | **A distinct role with an image rule and no persona — proposed `unroled-endpoint` — not `generic-endpoint`.** `generic-endpoint` is **already an inferred role in this catalog**, held by 102 of 141 placed hosts, with `persona: generic-endpoint` and the catalog's lowest confidence (0.35), and it asserts something specific: *"this host consumes services and serves none"* (`catalog/roles.yaml:397-409`). A host that matched **nothing** did not match that either. Emitting both under one name merges two different populations *and* hands hosts with no observed behaviour a traffic-generating persona — the cast would manufacture conversations the source never had, in the artifact being measured on the wire. A role with an image and no persona is an established, ruled shape (`visibility_and_vantage` D5a, `dns-resolver`): infrastructure, not a traffic source. **This is a deliberate deviation from the name the axRange draft uses — §7 seam item 2.** |
| **D5** | What do synthetic nodes carry? | **No `observation:` block at all** — the gateway (`compiler.py:474-480`) and the internet-sink groups (`:414-419`) observe nothing, and a block of zeros would say they were *never seen*, which is a different claim from *nothing could see them* (`LES-006`). axRange's proposal — **no block ⇒ always emitted** — is exactly right and is the reason absence must never be defaulted to zeros on either side. Its segment (`SINK_CIDR`) likewise carries none. |
| **D6** | Does the `population` block survive? | **Yes, and it is the accounting that keeps this honest.** `role_less` and `excluded_by_occupancy` stop describing *drops* and start describing *annotation*: `role_less` becomes a count with its `policy` string rewritten, and `excluded_by_occupancy` is **removed**, since nothing at compile time excludes anything any more. The reconstruction identity at `compiler.py:699-703` becomes `placed_hosts == sum(observed) + suppressed` — `suppressed` being the assertions channel's own sub-block (`docs/WORK_ORDER_reference_ingest.md` D8/D18). **The two work orders must land that identity together**, or the sidecar's stated rule is briefly false in whichever lands second. |
| **D7** | Do segments still get pruned? | **Only genuinely empty ones.** The prune at `compiler.py:325-329` stays as written — a segment with no emitted node is still not emitted, and axRange's expander still errors on a conduit whose segments have no router leg — but with every placed host emitted, a segment is pruned only if it had **zero** placed hosts, which is none of the 32 here. A segment falling below the *deploy* threshold is the deploy's business and is reported as *observed, below deploy threshold*, never as a cast drop. |
| **D8** | Determinism | Unchanged and re-pinned. The group key gains a field but keeps its total sort order (`compiler.py:356-358` sorts on `(cidr, role, band)`; it gains `windows_seen`), and `windows_seen` is an integer read straight off the IR row. Same PIT → same cast, byte for byte (`LES-002`, `CONVENTIONS.md` §3). **Every node name in every existing cast changes**, so `plan_id` changes and the twin is a different twin — correct, and the reason the fresh cast is the first golden rather than a re-deploy of the standing one. |

## 4. The block, exactly

```yaml
nodes:
  - name: generic-endpoint-heavy-10-80-2-w11
    role: generic-endpoint
    image: debian-12-cloud@v1
    count: 30
    net: seg-10-80-2
    observation: {windows_seen: 11, windows_total: 11, occupancy: 1.0, role_confidence: 0.35}
  - name: unroled-endpoint-10-80-31-w9
    role: unroled-endpoint
    image: debian-12-cloud@v1
    count: 2
    net: seg-10-80-31
    observation: {windows_seen: 9, windows_total: 11, occupancy: 0.818}   # role_confidence absent
  - name: gw
    role: gw
    kind: router
    nets: [...]                                                            # no observation block
segments:
  - name: seg-10-80-31
    cidr: 10.80.31.0/24
    zone_type: simple
    observation: {windows_seen: 4, windows_total: 11, occupancy: 0.364}    # role_confidence absent
```

Two shape notes, both deviations to reconcile (§7). **`role_confidence` is absent, not null**, on a
segment and on a role-less node — a segment has no role, and a role-less node has no confidence in
one, and `LES-006` is explicit that "no evidence" and "evidence of none" are different claims;
`null` would be a value. **A segment's `windows_seen`** is `ir_segment.windows_present` against
`windows_total`, whose `coverage_source` is `inferred_from_traffic` — a proxy for coverage, never a
measurement (`ir/schema.py`'s own `COVERAGE_SOURCES` docstring), so the segment block is weaker
evidence than a node's and the gate should know it.

## 5. Knock-ons

- **`scripts/check_role_fixed_point.py`** gains a row it cannot satisfy: `unroled-endpoint` has no
  `requires:` ports at all, so the fixed-point question — *"would this node re-infer its own
  role?"* — is vacuous rather than failing. It must be **skipped by name with a stated reason**, not
  silently passed: a checker that reports a vacuous row as green is `LES-125`'s ANY-gate failure.
- **The personas / `tfc` cast is unaffected by construction.** Assignments are emitted per role
  that *has* a persona (`compiler.py:561-569`); `unroled-endpoint` has none, so it lands in
  `personaless_roles` and its existing caveat — *"they are in the twin because they run real
  services other roles depend on (D5a)"* — needs one clause added for the case where a role runs
  nothing at all and is present because it was observed.
- **`synth/personas.py`'s censuses** key on `(role, band)` (`personas.py:773-777`) and are
  unaffected by the new group field: banding and occupancy are independent axes, and a persona is
  still derived from a role's members' behaviour, not from a node group.
- **`imprint_bundle.build_topology_bundle`** is unchanged. The IR is still the complete view (D9),
  and the topology export already publishes `windows_seen` per node — the block does not duplicate
  it into a second source of truth so much as project it onto the deployable document, which is
  the artifact the gate reads.

## 6. Build steps

1. **Delete the two filters and the flag** (D1) — `compiler.py:181-190`, `:200-201`,
   `cli.py:878`, and the two `caveats` strings they feed (`:436-447`).
2. **Re-key the node groups** on `windows_seen` and extend `_node_suffix`'s naming to carry it
   (D3).
3. **Emit the block** on nodes and segments; **omit** it on the gateway and the sink (D2, D5),
   with a `prov(...)` entry per node recording it as `observed`.
4. **Add `unroled-endpoint`** to `catalog/roles.yaml` with an image, a sizing and **no persona**,
   and route role-less hosts to it (D4).
5. **Rework the `population` block** (D6) and its reconstruction-identity comment, coordinated with
   `docs/WORK_ORDER_reference_ingest.md`'s `suppressed`.
6. **Skip `unroled-endpoint` by name** in `check_role_fixed_point.py`, with the reason (§5).
7. **Re-pin determinism**: `scripts/verify_determinism.py`'s synth check regenerates its expected
   output, and the change is proven by re-running one PIT twice, not by regenerating and trusting
   (`LES-002`, `LES-098`).

Sizing: **small-to-medium** overall — step 2 is the only one with real blast radius, because it
renames every node in every cast.

## 7. Acceptance

- The CorpAPCN cast emits **141 hosts and 32 segments** with **zero drops**: `caveats` contains no
  role-less line and no occupancy line, and `population.node_groups`' `sum(observed)` equals
  `population.placed_hosts` exactly.
- **Every** emitted non-synthetic node and segment carries an `observation:` block; the gateway,
  the 8 sink groups and `internet-sink` carry **none** — asserted as an absence, not as zeros.
- Every member of a node group has the **same** `windows_seen` as the group's block (the property
  D3 exists to buy), checked over the whole cast, not on a sample.
- The cast is **byte-identical across two runs** of the same PIT, and re-running after a `cast`
  with a different `--max-count` leaves the blocks unchanged.
- A cast of a PIT whose every host is present in all windows produces exactly the groups the
  pre-change compiler produced, modulo the name suffix — the no-op case (`LES-031`: verify by
  mutation, not by the test passing).
- `check_role_fixed_point.py` passes with `unroled-endpoint` skipped **by name**, and fails if the
  skip is removed.

## 8. Boundaries, seam items, and one decision for the user

**Not here:** the deploy gate itself (`DEPLOY_MIN_OCCUPANCY`, axRange `twinconfig/`); the
scorecard's use of the block; anything about *confidence* as a gate — the ruling is occupancy, and
confidence stays a shading input.

**Seam items to reconcile with the axRange side:**

1. **`role_confidence` is absent, not null**, on segments and role-less nodes (§4). The gate must
   treat absent as not-applicable — which it already must, since synthetic nodes carry no block.
2. **`unroled-endpoint`, not `generic-endpoint`** (D4). A deliberate deviation, measured: 102 hosts
   already hold `generic-endpoint` and it carries a persona. If axRange needs the literal name, the
   cost is that role-less hosts start generating traffic they were never observed generating.
3. **Node names change** for every cast (D3), so no standing twin's `plan_id` survives this.
4. **Group splitting vs an occupancy histogram** (D3) — decided here on `te deploy --update`'s
   refusal of in-place `count:` mutation; reopen only if that refusal changes.
5. **The `population` reconstruction identity** must land with
   `docs/WORK_ORDER_reference_ingest.md`'s `suppressed` sub-block (D6).

**Decision for the user.** D4's name. **(a)** `unroled-endpoint` — recommended, keeps
`generic-endpoint`'s meaning and generates nothing; **(b)** `generic-endpoint` as the axRange draft
names it — one word simpler, at the cost of merging two populations and manufacturing traffic;
**(c)** emit no `role:` at all — honest, but breaks row 1's (role, segment) grouping and falls back
to `DEFAULT_NODE`'s 2Gi/20Gi sizing for every one of them.

## 9. Rulings, 2026-09-04 (Authority: user; axRange CHANGELOG same date)

- **D3 is replaced.** The group key gains a **fixed occupancy ladder**, not `windows_seen`: *core*
  ≥ 0.8, *regular* 0.5–0.8, *transient* < 0.5. The block on a group carries its **minimum
  member's** `windows_seen` and occupancy (an exact number the gate compares; whole-group gating is
  preserved), group names stay stable across re-imprints unless a host changes presence class, and
  0.8 is a rung. Measured cost is bounded at three groups per today's one instead of the 43 → 87
  the exact key produced. The histogram alternative stays rejected for the reason D3 gave.
- **D4 ruled (a): `unroled-endpoint`.** An image rule, no persona; `generic-endpoint` keeps its
  meaning.
- **D5 confirmed:** compiler-class nodes carry no block; axRange treats no block as always emitted
  and absent `role_confidence` as not applicable.
- **The 0.8 default is kept** on the axRange side, with its consequence stated to the user: about 64
  observed hosts deploy against the 122 the old cast placed under no threshold. Emit `params:
  {DEPLOY_MIN_OCCUPANCY: 0.8}` in every cast so the bar is visible.
- **Honest limit:** a rung crossing on re-imprint changes two groups' `count:`, which `te deploy
  --update` refuses as an in-place mutation; whole-group adds and removes update incrementally.
