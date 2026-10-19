*What "a faithful twin" means on this platform — a closed specification of eleven fidelity rows,
each tagged with its observation channel and evidence class — and how a build earns the label
golden. Supersedes the fidelity loop's convergence objective.*

> **STATUS: DESIGN RATIFIED 2026-09-03 (Authority: user); §8 RULED 2026-09-03 (Authority: user)
> — nothing built yet.** The four decisions §8 left open were ruled the same day; §8 records each
> ruling verbatim with its consequences, §3.3 and §4 carry the first golden's resulting gate set,
> and the CHANGELOG entry of the same date records the rulings. This is the
> design-of-record for what "a faithful twin" means on this platform and how a build earns the
> label **golden**. It **supersedes the convergence objective** of the CorpAPCN fidelity loop
> (`aximprint/docs/JOURNAL_corpapcn_fidelity.md`, iterations 10–19; `WORK_ORDER_proto_weight_rows.md`
> §3's goal framing): the loop's *measurements* stand and are reused below, its *objective* — drive
> a traffic-shape distance to zero — is retired. Cross-repo: axRange owns this file; aximprint owns
> the observation half (`docs/WORK_ORDER_reference_ingest.md`, to be written) and cites this file.
> Both sides work on a branch named **`wip/golden-twin`** — the same name in both repos, deliberately.

## 1. Why this exists

Nineteen iterations of the fidelity loop cast a twin from a real capture, deployed it, captured it
back, and tuned toward a smaller fingerprint distance. Three facts closed the loop:

- **The deciding axis never converged.** `role_edges_local` read 0.595 / 0.618 / 0.742 / 0.589 /
  0.533 / 0.593 across iterations 14–19 — a band, not a trend. Iteration 19 hit its own target
  (the sink re-inferred as `web-tier`, `role_edges` 0.878 → 0.406) and regressed two of its three
  must-not-regress axes: a trade, by the work order's own rule.
- **Two thirds of the reference's local edge keys cannot exist in the twin.** 12 of 18 source
  local role-edge keys were absent from the twin in iteration 19 (a Windows domain controller that
  answers nothing; no workstation-to-workstation traffic). Re-weighting the six present keys can
  only move mass between axes. That is the mechanism behind the band.
- **The fingerprint measures traffic shape, which is neither the goal nor a proxy for it.** The
  platform's purpose is attack-and-defense experiments. Those depend on real protocols (so
  detection rules fire), real identity (so attack paths exist), security posture, and
  configuration. None of that is a fan-out distribution. A twin can score zero on every axis while
  its DNS is a byte-pusher aimed at port 53 — which is what it was.

The loop was not wasted: it produced the cast pipeline, the personas, the internet sink, the
fingerprint tooling, and a long list of platform fixes found by driving the system hard. All of it
survives here as the **behavioral row's** generator and checker. What changes is what "done" means.

## 2. Three words, three different things

| term | what it is | when it applies | how it is judged |
|---|---|---|---|
| **fidelity** | how faithfully a twin reproduces the source **at imprint time** | acceptance of a build | a closed spec of dimensions, each pass/fail or within tolerance, each tagged with its evidence class |
| **golden** | a build that passed the fidelity spec at a stated spec version | the artifact experiments run against | the scorecard, versioned in git beside the twin config and its provenance |
| **drift** | how far the **live network** has moved away from a golden twin **since** it was certified | maintenance over time | a re-imprint at a later point in time, diffed against the golden spec |

The loop conflated all three by using a distance metric as a tuning objective. Distance survives
as a **measurement** (the behavioral row); it is no longer an **objective**.

## 3. The specification

Eleven rows: the ten dimensions of the standard taxonomy plus **visual** as an operator-trust row.
Every row carries its **observation channel** — where the evidence comes from — because that, not
ambition, decides what fidelity can mean for a network twin:

- **wire** — traffic metadata from a sensor (Zeek, Suricata, axquery ingest plugins). Recoverable:
  asset existence, L3 topology, dependency, protocol, behavior, temporal pattern, volume.
- **reference** — imported point-in-time snapshots under the `imported` evidence class
  (`14-operator-assertions.md` §2; this line said `referenced` until 2026-09-04): directory
  exports, host inventories, network device configs, security-tool baselines. Recoverable:
  configuration, identity, security posture, runtime state, asset attributes, L2.
- **operator** — asserted facts with a source note, class `asserted`, authored on the Console
  canvas or by hand (`14`). The fallback when no export exists; how a twin is seeded from a
  design document — and, since 2026-09-04, the first reference ingest to build.
- **console** — the visual row only.

Evidence classes are the **six** of `14-operator-assertions.md` §2 — `asserted`, `imported`,
`observed`, `referenced`, `imputed`, `compiler`, in that precedence order (superseding this
section's original "aximprint's existing three", 2026-09-04) — and the house rule holds
everywhere: **no class masquerades as a higher one; an override is visible, never an erasure.**
A scorecard row therefore reports per-fact states, never one number — *observed-*,
*referenced-*, *imported-* and *asserted-and-reproduced*, *imputed*, *compiler (not scored)* —
and publishes its evidence mix.

### 3.1 The rows

| # | dimension | category | channel | what axRange reproduces today | v1 pass rule (rows 6/8/9/10 provisional until scorecard run 1, §8.3; first-golden status in §3.3) |
|---|---|---|---|---|---|
| 1 | asset | structural | wire + reference | VMs from catalog images; OS from role inference; image/resources are catalog defaults | every host observed in ≥ 80% of windows — the deploy threshold of §3.4 — is placed — **evaluated at (role, segment) group granularity, naming every high-occupancy host the cast dropped (ruled 2026-09-04; the cast keeps no per-host identity)**; OS family **reported as imputed-from-role, not scored, until the reference channel lands (ruled 2026-09-04)**; image and sizing reported as `compiler`, not scored |
| 2 | topology | structural | wire (L3) + reference (L2, routing) | SDN segments, router, fabric slice for routing protocols | every observed segment above the deploy threshold (§3.4) exists with its mapped CIDR, and a segment below it is reported as out of deployment scope; every observed cross-segment conversation between deployed segments has a route; L2 fabric out of scope v1 |
| 3 | configuration | structural | reference | role-level install lists, `pkg@version`, local users, defense agents | **v1 (ruled §8.2): every placed host carries its inferred role's install list from the role catalog, reported as imputed.** The inventory-backed rule — observed services present at policy version ≥ 90% of referenced — moves to the spec version that lands the host-inventory reference ingest |
| 4 | state | operational | reference + in-guest | services up; synthetic seeded datasets | 100% of observed listens answer in-guest (`ss -lnH -tu`) at certification time |
| 5 | protocol | behavioral | wire | **real servers configured from the observed listen set, built 2026-09-05** (`docs/WORK_ORDER_protocol_faithful_services.md`, S1/S2/S3/S5): the cast declares each host's observed listen set (`App.listen`, published by `te inventory`) and emits real-daemon `serve-*.sh` recipes — bind9, nginx (incl. TLS on 8006), chrony, isc-dhcp-server, an sshd assertion — replacing the echo responder, which survives only on ports no real server covers, labelled `compiler`; the **domain controller is a real AD DS forest** (`role: adds`) **under the LAB realm `corp.axr.lab`** (D4), with joined Windows members answering RDP. `drive: command` clients built. **Not yet deployed on a cluster** — the gated live-verify is pending; S4 (zones/vhosts from observed names) waits on the producer's TLS/HTTP/SMB carrier. **The baseline this replaces is now measured** (run 1, 2026-09-05, on the *pre*-server-half standing twin): **5 of 31** protocol-aware probes pass, 9 of 12 methods score none, and three failures are the echo returning the probe's own bytes | each observed (host, port, protocol) returns a protocol-valid response to a protocol-aware probe — **served by realistic protocol clients AND servers configured from the observed data, not synthetic filler (ruled §8.2)**. **As built 2026-09-05:** the denominator is the observed **service** listens on deployed hosts at or above the deploy threshold (§3.4), and every excluded fact is **published with its reason and count** — *not a service* (dynamic/ephemeral ≥ 49152, Windows Delivery Optimization 7680), *no protocol-aware probe* (proprietary 9997, RDP-UDP 3389/udp, QUIC 443/udp), *below deploy threshold* (reported, never failed). An unexplained filter is as dishonest as a vacuous pass |
| 6 | performance | operational | wire | persona volume levers; capture rate characterized at ~1.5 Gbit/s lossless | top-N edge volumes within ±50% of observed; twin capture lossless at the offered rate |
| 7 | security | operational | reference + wire | AD forest with SPNs, no-preauth, ACL edges, delegation, weak passwords; Suricata/Wazuh; routers permissive | with a directory export: ≥ 95% of referenced principals, groups, SPNs, delegation flags, ACL edges reproduced; password policy reproduced; **firewall policy: not reproducible in v1, recorded as a gap** |
| 8 | dependency | structural | wire | personas plus provisioning order | key-set Jaccard of local role-edge classes ≥ 0.8 (iteration 19: 0.333) |
| 9 | temporal | behavioral | wire | business-hours gate, cadence | reported only in v1: hourly activity envelope over a ≥ 24 h capture |
| 10 | behavioral | behavioral | wire | the six fingerprint axes | within tolerance, tolerances fixed after the first scorecard (§8) |
| 11 | visual | — | console | four-tab Twin Topology, confidence-shaded imprint view | learned and deployed topology render side by side; every evidence class visibly distinct; the same canvas authors assertions (`14` §4) |

### 3.2 Two corrections to earlier assumptions, recorded so they are not re-learned

- **Reachability is not policy.** The twin has segments and routes; the router ships a permissive
  forward policy, the twin-config conduit `policy` field was inert and removed on 2026-08-04, and
  per-rule firewall policy is an open item. Row 7's firewall clause cannot pass in v1 regardless of
  input quality. This is the one reproduction gap injection does not fill.
- **Identity was never an observation problem alone.** The deploy side builds a rich, deliberately
  weak AD forest, live-verified down to a DCSync. The cast never emits it because the imprint has
  no directory evidence to derive it from. Row 7 is filled by a reference channel, not by new
  reproduction work.

### 3.3 The first golden's gate set (ruled 2026-09-03, §8.1)

The first golden gates on every **structural** and every **behavioral** row whose own v1 rule is
not reported-only (row 9's is); the **operational** rows are deferred for it and the visual row is
reported. Rows 6, 8, 9 and 10 carry provisional
tolerances until scorecard run 1 (§8.3), so on that run they are reported rather than gating.
**Measured 2026-09-05 (run 1, `dev45`, read-only):** all four came back **`not-computed`** — rows
8/9/10 need the fingerprint seam (the scorecard work order's S5, unbuilt) plus a **recapture** of the
deployed twin, and row 6 is deferred by category — so **run 1 did not, and could not, fix their
tolerances**; §8.3 is corrected accordingly.

| # | dimension | category | first golden |
|---|---|---|---|
| 1 | asset | structural | **gates** |
| 2 | topology | structural | **gates** |
| 3 | configuration | structural | **gates** (v1 rule relaxed to role-level install lists, §8.2) |
| 8 | dependency | structural | **gates** once its tolerance is fixed in v1.1; reported on scorecard run 1 |
| 5 | protocol | behavioral | **gates** (real clients and servers from observed data, §8.2) |
| 9 | temporal | behavioral | reported (its own v1 rule is reported-only) |
| 10 | behavioral | behavioral | **gates** once its tolerances are fixed in v1.1; reported on scorecard run 1 |
| 4 | state | operational | deferred — "ignore for now" (§8.1) |
| 6 | performance | operational | deferred by the category rule (§8.1); its tolerance is still fixed in v1.1 for later goldens |
| 7 | security | operational | deferred — "ignore for now" (§8.1); the firewall clause stays a recorded gap |
| 11 | visual | — | reported (reading: neither category names it; §8.5) |

### 3.4 Deployment scope: the cast describes, the deploy selects (ruled 2026-09-04)

The cast no longer drops anything at compile time. Its two compile-time filters — the
role-bearing-only rule that discarded role-less hosts and the `--min-occupancy` threshold — become
**annotation**: every node and segment the cast emits carries an **observation block** (windows
seen, windows total, occupancy, role confidence), and role-less hosts are emitted as generic
endpoints. The **deploy** decides what to instantiate: the twin-config expander gates on the block
with a config param, `DEPLOY_MIN_OCCUPANCY`, **default 0.8 — the same bar row 1 uses**, so the
deployable set and the set row 1 expects to find are the same set by construction. Nodes below
the bar are not emitted; a segment with no emitted node is not emitted. The gate is on
**occupancy, not confidence**: a host seen in one of eleven windows is a transient and stays out;
a host present in all eleven whose role could not be inferred is a fixture and goes in, ungated.
Confidence drives the canvas shading and is available as a second, optional gate that by default
never keeps a present host out. Changing the threshold changes the node set and therefore the
`plan_id` — correctly, it is a different twin — and one cast serves every threshold, which the
compiler's own docstring had argued for and now gets on the deploy side. **Rows 1 and 2 judge
against the deployable set at the twin's recorded threshold**: a host or segment below it is
reported as *observed, below deploy threshold*, never failed. Authority: user for the policy
move; the occupancy-not-confidence gate and the 0.8 default were proposed by implementation and
accepted the same day. The twin-config block and the gate are an axRange code change
(`axrange/twinconfig/`, the numeric cousin of the existing `when:` gate), nothing version-locked;
the emit-all policy and the block are an aximprint hand-back.

**Refined 2026-09-04 by the rulings on the three drafts (Authority: user):** the 0.8 default is
**kept**, knowing it deploys about 64 observed hosts against the 122 the old cast placed under no
threshold — the twin is exactly the certified set, and any deploy can lower the bar with `--param`
at the cost of a different `plan_id`. Role-less hosts are emitted as **`unroled-endpoint`** — a
distinct role with an image rule and **no persona** — not as the inferred `generic-endpoint`, which
102 placed hosts already hold together with a traffic-generating persona. The cast's group key
gains a **fixed occupancy ladder** — *core* ≥ 0.8, *regular* 0.5–0.8, *transient* < 0.5 — rather
than an exact `windows_seen` (which would have split 43 groups into 87 and renamed every group on
every re-imprint): the block on a group carries its **minimum member's** `windows_seen` and
occupancy, so the gate compares an exact number and acts on whole groups, group names stay stable
across re-imprints unless a host changes presence class, and 0.8 is a rung. `role_confidence` is
**absent** where it does not apply (segments, `unroled-endpoint`); compiler-class nodes (the
gateway, the internet sink) carry **no block** and are always emitted. Honest limit: a re-imprint
that moves a host across a rung changes two groups' `count:`, which `te deploy --update` refuses as
an in-place mutation — whole-group adds and removes update incrementally, count changes do not.

**BUILT 2026-09-04 (axRange half; `docs/WORK_ORDER_deploy_gate.md` §11.2, CHANGELOG "Deploy gate
BUILT"). Two rules the build had to add, both Authority: implementation (Claude Code), because the
design as written broke on real content:**

- **A router follows its segments.** A router leg counts neither as evidence that a segment is
  occupied nor as a node that a gated-out segment was pulled out from under; a segment the gate
  removes has its router legs pruned in the same pass, and a router stranded of every leg is dropped
  and reported. Without it, the cast's single gateway — which legs every one of its 27–33 segments
  and is itself exempt from the gate — made every segment permanently occupied, so nothing could
  cascade, and turned every below-bar segment into a hard error: **every real cast refused**.
- **The cascade removes a segment only when the gate emptied it**, comparing non-router occupants
  before and after gating rather than testing emptiness outright. A router-only segment — a
  router↔router transit link, a WAN uplink — has no host occupant by construction, and a bare
  emptiness test dropped it at *every* threshold including 0.0, taking its routers with it. Measured
  on hand-authored content that opted into nothing: `dmz-perimeter` lost its internet uplink,
  `datacenter` its fabric link and both fabric routers.

**The effective thresholds are recorded in the deployed IR itself** — `scenario.labels`
(`deploy_min_occupancy` / `deploy_min_role_confidence`), emitted only when at least one observation
block exists, so a config carrying none expands byte-identically as before. Because the labels join
`ir_hash`, recovering "what threshold was this twin deployed at" is a **proof**, not a lookup:
re-expand the config at a candidate threshold and compare hashes. A match confirms the threshold and
makes the gated-out set exactly reproducible; anything else is reported as *threshold: UNCONFIRMED*
and rows 1–2 decline to score against a set they cannot reproduce. A `--param` override is persisted
nowhere, which is why confirmation, and not recomputation from the config's declared `params:`, is
the mechanism.

## 4. Weighting: the experiment decides which rows matter

Not all rows are equal, and treating them so is the difference between a golden twin and a very
well-shaped traffic generator. Weight follows the experiment a golden twin is certified **for**:

| experiment class | rows that gate it | rows that support it |
|---|---|---|
| attack path / kill chain (exercise tier) | 7 security, 3 configuration, 1 asset, 2 topology | 8 dependency, 4 state |
| detection validation (A4 measurement) | 5 protocol, 7 security | 10 behavioral, 9 temporal |
| false-positive baseline | 10 behavioral, 9 temporal, 5 protocol | 6 performance |
| data exfiltration scenarios | 4 state, 3 configuration | 2 topology |
| sensor and capture validation | 6 performance, 2 topology | 5 protocol |

**First golden — ruled 2026-09-03 (Authority: user; supersedes this section's original proposal,
attack path plus detection validation, which had rows 7, 5, 3, 1, 2 gating):** the first golden's
gate set is defined by **category, not by experiment class** — "all structural and behavioral
category dimensions to start with." Every structural row (1, 2, 3, 8) and every behavioral row (5,
9, 10) gates it; the operational rows (4, 6, 7) are deferred for it; the visual row is reported
(§3.3). In the table's terms that is the structural half of the attack-path class plus the
behavioral half of the detection-validation and false-positive classes, with each class's
operational rows left out. The table stays the model for later goldens certified for a named
experiment, which is when the operational rows return. On scorecard run 1 the hard gates are
therefore rows 1, 2, 3 and 5 (rows 8 and 10 gate from spec v1.1, once §8.3 fixes their
tolerances; row 9 is reported by its own rule). The July capability this section originally named
— the AD kill chain, a real DCSync, Suricata and host-audit detections, truth markers joined to
detections in Loki, replay with an identical playout hash — remains the experiment a later golden
is certified for.

The loop spent nineteen iterations on the row where the wire already gives the most (10) while the
rows the goal hinges on (7, 3, 4) had the least observation and the least reproduction. That
inversion is the finding this document exists to correct.

## 5. Filling the operational half: the reference channel

The operational and configuration rows are an **observation gap, not a reproduction gap**. They are
filled by injecting known information under the `referenced` evidence class, ranked here by
authority and cost:

1. **Directory export.** A read-only LDAP bind or a BloodHound-style collection: users, groups,
   OUs, computers with OS attribute, SPNs, the no-preauth flag, delegation, ACL edges, password
   policy, sites and subnets. Maps almost one to one onto the identity block the twin config already
   accepts and the `IdentityEntry` fields the IR already models. The cheapest route to a domain
   controller that answers. Sites and subnets are a topology hint the wire never gives.
   **Mechanism ruled 2026-09-03 (§8.4): an LDIF export, file-dropped into the imprint directory,
   run by the reference owner — no credential and no network reach from our side.** The owner runs
   the built-in `ldifde` (or `ldapsearch`) against their own DC with an attribute allowlist this
   project supplies, so LAPS and other secret-bearing attributes never leave their network, and
   hands over the files; ACL edges need the security-descriptor attribute included in the export.
   One LDIF parser serves a later live read-only bind unchanged, so drift re-imprints need no
   second format; BloodHound JSON may be accepted as a second input later if a site can only hand
   over that. Under §8.1 the export serves rows deferred from the first golden (7, 4) and row 3's
   inventory-backed rule, so reference ingest is sequenced after the first golden (§7) and pulled
   forward only if row 1 (OS attribute) or row 2 (sites and subnets) needs it.
2. **Host inventory.** An existing agent or scanner export, or a one-shot collection over WinRM
   and SSH: packages and versions, patch level, listening processes, local admins, firewall rules,
   shares. Versioned packages and local users reproduce today; Windows patch level means image
   builds.
3. **Network device configs and IPAM.** Running-configs, VLAN databases, DHCP scopes, DNS zones:
   the L2 picture, the routing protocols the fabric slice reproduces, and the firewall policy
   nothing reproduces yet.
4. **Security-tool baselines.** The existing Suricata ingest plugin is the pattern: alerts as
   evidence. A SIEM baseline on the real network doubles as the false-positive reference.
5. **Operator-asserted facts.** The assertions file of `14-operator-assertions.md` §3, class
   `asserted` — **promoted 2026-09-04 to the first reference ingest to build**, ahead of item 1,
   with the Console canvas as its author.
6. **Wire identity, strengthened.** Kerberos, NTLM, LDAP and SMB logs carry principals, hostnames
   and service names; deriving them as `observed` identity facts gives the cross-check that makes
   a scorecard honest: the directory says who should exist, the wire says who acts.
   **Ruled 2026-09-04 and built 2026-09-05 (D4, Authority: user;
   `docs/WORK_ORDER_protocol_faithful_services.md` §10.1): the realm is RECORDED, and the LAB realm
   is the twin's.** The 2026-09-04 finding that the source's forest name *is* on the wire — three
   independent stems, `kerberos.log`, `ntlm.log` and the CLDAP netlogon filter — stands and went
   back to the producer as a correction; the twin nonetheless serves **`corp.axr.lab`**, an
   `imputed`/`compiler`-class lab constant, so the single most identifying string in a synthetic
   environment is never a reproduction of a real one. What the wire *does* supply is used: the DC
   keeps its observed short hostname (`axdc01`), which appears in every Kerberos, LDAP and SMB frame
   the twin emits. The DC's user population is lab-default and `imputed` in full — the capture
   yields two principals, one of them `Guest`, and no credentials — which is item 1's job, not
   this one's.

**Mechanics.** aximprint gains a non-sensor ingest plugin kind — a reference ingest taking a
point-in-time snapshot, normalized into the belief store as `referenced` with the export timestamp
as its time bound — extractors that emit identity, posture and configuration assertions into the
existing `ir_identity` and `ir_unseen` tables (and new tables where needed; aximprint's schemas are
incubating, so no ceremony), and a compiler step that writes the identity block, versioned installs
and defense overlay into the cast. axRange's deploy machinery needs nothing new for identity.

**Inject posture, never secrets.** The twin reproduces *that* an account is Kerberoastable, *that*
the password policy is weak, using lab credentials — never real hashes. That line keeps a golden
twin a lab.

## 6. Lifecycle

```
imprint (wire + reference) → cast → deploy → certify (scorecard) → tag golden vN
      ↑                                                                  │
      │                                                        experiments run here
      │                                                                  │
re-imprint at PIT'  ←──── drift report (spec diff) ←────────────────────┘
      │
      └→ te deploy --update / --reapply → re-certify
```

Every box exists except *drift report* — ***certify* now exists** (`axrange te scorecard`, built
2026-09-05 for rows 1/2/3/5 and the document; CHANGELOG "Fidelity scorecard BUILT") **and has been
run on a cluster**: **scorecard run 1 executed read-only on `dev45` on 2026-09-05 and passed its own
acceptance** — the standing `corpapcn-twin-0e4f7d58` scored **`not-golden`** with the probe, and the
twin was neither destroyed, `--update`d nor added to (CHANGELOG 2026-09-05 (later) "Scorecard run 1
RAN on `dev45`"; `docs/WORK_ORDER_fidelity_scorecard.md` §7.1):

- **imprint / cast / deploy**: aximprint's pipeline, `install_cast_as_twin.py`, the twin-config
  expander, the `te`/`tap`/`tfc` tiers. The imprints API and the Console's "deploy as twin" are
  the same path from the UI.
- **certify**: the **fidelity scorecard** (axRange, `axrange/fidelity/`) — per row, observed /
  referenced / imputed against reproduced, computed from the provenance sidecar, the published twin
  inventory and in-guest checks, served over the API and shown behind the Console's imprint screen.
  **BUILT 2026-09-05** as **`axrange te scorecard [DEPLOY_ID]`** and its API peer
  `GET /api/deploys/{id}/scorecard`, a standalone deploy-scoped verb on `te inventory`'s precedent:
  rows 1/2/3/5 computed under §3.3's gate set, rows 4/6/7 deferred, rows 8/9/10 provisional and
  `not-computed` pending the fingerprint seam, row 11 operator-attested, and the document written
  beside the twin config as `<id>.scorecard.<deploy_id>.json`. The behavioral row reuses
  `shape_fingerprint.py` unchanged. The wire rows (8, 9, 10) need a **recapture of the deployed
  twin** — a tap plan, a traffic profile and an aximprint ingest of the twin's own capture — whose
  fingerprint reaches the scorecard as a file on the seam
  (`docs/WORK_ORDER_fidelity_scorecard.md` §8.2); certify therefore includes that recapture, which
  the diagram's one box did not say (reading, 2026-09-04). **Not yet built:** that seam (the work
  order's S5) and the Console screen.
- **drift**: a re-imprint at a later point-in-time id, diffed row by row against the golden spec.
  A structural drift (a new host, a removed segment) becomes a `te deploy --update`; a content
  drift on a standing host becomes `--update --reapply`. Both exist.

## 7. What changes in the work

- The convergence loop **closes at iteration 19**. Its closing entry records the trade and this
  decision.
- Work-order triage: Option A (`--reapply`) **kept** as the maintenance path; protocol-faithful
  traffic **promoted** to core with its real-service sink; the aximprint Console **kept** as the
  golden-twin UI; O2 sensor rules **promoted** (the experiment-outcome test needs detection rules on
  the standing sensor); per-rule firewall policy **promoted** (the one gap injection cannot fill);
  the Windows responder **superseded** (the problem stands, the solution is a real DC from a
  directory export); source-locality filters **parked** (behavior-row tolerance tuning).
- New work, in order — **ruled 2026-09-04 (Authority: user)**, replacing the implementation
  reading recorded here earlier that day: this spec (done by this file); then, in parallel,
  `WORK_ORDER_fidelity_scorecard.md` (axRange — scorecard run 1 against the standing twin,
  immediately, rows 6/8/9/10 flagged provisional), the row-5 build
  (`WORK_ORDER_protocol_faithful_services.md` — real protocol **clients and servers configured
  from observed data**; the `drive: command` clients already built plus the server half, a real
  DC under the lab realm included), and the **deployment-scope change of §3.4** (aximprint: emit
  all + the observation block; axRange: the `DEPLOY_MIN_OCCUPANCY` gate); spec v1.1 fixing the
  provisional tolerances from run 1; the fresh cast and its certification as the **first golden**
  — rows 1 and 2 closed by §3.4, not by assertions; then `WORK_ORDER_reference_ingest.md`
  (aximprint: the assertions file of `14-operator-assertions.md` first, the LDIF file drop of §5
  second, host inventory third) with the Console canvas built alongside it; then the
  firewall-policy IR extension (axRange, promoted from open item 5); and the runbook — the AD kill
  chain plus detection scoring against a twin cast from an enriched imprint — which validates
  the row weighting in §4 for the second golden. The reference channel and firewall policy serve
  the operational rows the first golden defers, so they follow it rather than gate it. The
  scorecard and the row-5 build meet at the file contract; the fixture imprint in axRange's API is
  the cross-repo test seam already built for it.
- Branches: `wip/golden-twin` in both repos after both masters are fast-forwarded. The repos stay
  separate: the file contract (twin config + provenance sidecar + topology export, no imports in
  either direction) is the architecture, and it is what let one session drive both pipelines.
  Revisit a merge when aximprint's schemas leave incubation and the contract stops moving.
- The standing `corpapcn-twin-0e4f7d58` stays up as the scorecard's first subject (scorecard run 1,
  ruled §8.2) and Option A's live proof, and is replaced only by the fresh cast that becomes the
  first golden. **Run 1 ran read-only on `dev45` 2026-09-05 and passed** — verdict `not-golden`,
  rows 1/2/5 failing for the reasons §8.2 predicted, nothing on the twin touched
  (`docs/WORK_ORDER_fidelity_scorecard.md` §7.1). **The fresh cast's candidate exists**: produced
  from the same source PIT by the services-build compiler and installed read-only-verified as
  `twin-configs-cast/corpapcn-golden/` (now `twins/corpapcn-golden/`; 88 nodes / 33 segments described; 80 hosts on 24 segments
  emitted at `DEPLOY_MIN_OCCUPANCY 0.8`; validates, plans 695 ops; **the first bundle carrying its
  own provenance sidecar**, so the scorecard reads evidence classes from the producer instead of
  re-deriving them). **Its deploy is still gated and has not run** — that deploy is the first
  golden's subject and the point at which the standing twin is replaced.

## 8. Decisions ruled 2026-09-03 (Authority: user)

Each item quotes the ruling as given, then records its consequences; the consequences are an
implementation reading (Authority: implementation (Claude Code)) and are what the work orders
build against unless the user corrects them. The original open questions are kept in each item's
title so the CHANGELOG trail stays readable.

1. **Which experiment class certifies the first golden — RULED by category instead.** *"Let's
   focus on all structural and behavioral category dimensions to start with"*; and, of rows 4 and
   7, *"rows 4/7 are operational, you can ignore them for now."* Consequence: the first golden
   gates on rows 1, 2, 3, 8 (structural) and 5, 9, 10 (behavioral); rows 4 and 7 are deferred by
   name and row 6 by the category rule (it is operational too; the user did not name it); row 11
   visual is reported (reading — it belongs to neither category). §4's proposal of attack path plus
   detection validation is superseded; §4's table stays the model for later goldens, which is when
   the operational rows return. §1's argument that rows 7, 3, 4 are where the goal hinges is not
   reversed by this — the ruling sequences, it does not drop rows from the spec.
2. **First golden: the standing twin re-certified, or a fresh cast — RULED both, in order.**
   *"Score the standing twin first; the fresh cast is the first golden."* Two row rules were
   amended in the same ruling: row 3 — *"role-level install lists will suffice"*; row 5 —
   *"implement realistic protocol clients and servers based on the observed data. avoid using
   synthetic data generation where possible."* Consequences: the standing `corpapcn-twin-0e4f7d58`
   is scorecard run 1 — expected to fail row 5 honestly (its Windows hosts answer nothing and its
   personas push filler bytes) and to report rows 6/8/9/10 without gating — and is destroyed when
   the fresh cast deploys (dev45 holds one 138-VM twin). **Measured 2026-09-05, and the expectation
   held to the letter:** row 5 scored **5 of 31** protocol-aware probes passing — the passes all DNS
   and one HTTP — with 17 timeouts, 6 connection-refused and **3 protocol mismatches that name the
   mechanism**: the echo responder returned the probe's own NTP mode-3 request and its own LDAP
   `searchRequest` tag, so an **open port scored zero**. `axdc01` timed out on all ten of its facts —
   §1's *"a Windows domain controller that answers nothing"*, measured rather than argued. Rows
   6/8/9/10 reported `not-computed` (see §8.3 as corrected). Row 3's v1 rule is the role catalog's
   install list per placed host, reported as imputed; the inventory-backed rule waits for the
   host-inventory reference ingest. Row 5's reproduction is real protocol **clients** (`drive:
   command`, built) and real **servers** on twin hosts and at the aximprint sink, both configured
   from observed data (observed DNS names, HTTP hosts, service listeners; §5.6's wire-derived
   principals for a real domain controller) — generated filler only where no observation exists.
   The protocol-faithful traffic work order gains a server half; §5.6 moves onto the first
   golden's path (§7). The Windows-responder problem returns to the critical path as a real DC
   answering real Kerberos/LDAP/SMB, not as a listener.
3. **v1 tolerances for rows 6, 8, 9, 10 — RULED provisional until scorecard run 1, then fixed as
   spec v1.1.** Consequences: on run 1 these rows are reported, not gating, and the scorecard's
   output flags them provisional; v1.1 fixes the numbers from run 1's measurements (row 6's too,
   for later goldens, although it is deferred for the first); the §3.1 placeholders stand until
   then. The v1.1 numbers themselves are the one part of this item still to rule.
   **Corrected 2026-09-05 by run 1 itself (Authority: implementation (Claude Code)):** run 1
   produced **no** numbers for these four rows and could not — 8/9/10 need the fingerprint seam
   (the scorecard's S5, unbuilt) *and* a recapture of the deployed twin, row 6 is deferred by
   category, and all four returned `not-computed`. **The v1.1 tolerances therefore wait on S5 plus a
   recapture, not on run 1**, and the ruling's sequencing — provisional until measured — is
   unchanged; only the event that supplies the measurement moves.
4. **The directory-export mechanism and acceptable access — RULED: an LDIF export, file-dropped,
   run by the owner, no access from our side.** Consequences in §5 item 1: `ldifde`/`ldapsearch`
   with a supplied attribute allowlist (secrets never leave the reference network), the
   security-descriptor attribute included for ACL edges, one parser that later takes a live
   read-only bind unchanged, BloodHound JSON as a possible second format. Sequenced after the
   first golden (§7) because it serves the deferred operational rows; the reference-ingest work
   order (aximprint) carries the allowlist and the file-drop contract.
5. **Still open** (to be ruled when they bite, not before): the v1.1 numbers (after run 1);
   whether row 11 visual ever gates a golden; and which experiment class the *second* golden is
   certified for, since that is when rows 4, 6, 7 gate. §7's sequence, an implementation reading
   when this item was written, was ruled with a variant later on 2026-09-04 (item 6).
6. **Rulings of 2026-09-04 (Authority: user; CHANGELOG same date).** Row 1's rule as amended in
   §3.1 (group-granular occupancy; OS family imputed-from-role, not scored). The evidence model
   and the operator channel — `14-operator-assertions.md`, which supersedes §3's class sentence
   and promotes §5 item 5 to the first ingest. Two work-order rulings recorded in the work orders
   and the CHANGELOG: `App.listen` is wired as the twin's declared listen set (a version-locked
   `IR_SCHEMA` touch with no version bump), and the real domain controller is promoted now under
   the **lab realm `corp.axr.lab`** with lab-default users marked imputed — the observed realm
   was offered and not chosen. **Later the same day:** §7's sequence is ruled — the reference
   channel (assertions first, LDIF second) and firewall policy follow the first golden, and the
   fresh cast's rows 1 and 2 are closed by the **deployment-scope change of §3.4** (the cast
   emits its whole observed population with an observation block; the deploy gates on occupancy
   at `DEPLOY_MIN_OCCUPANCY`, default 0.8; role-less hosts become generic endpoints) rather than
   by assertions. The canvas loop's own open items were ruled in `14` §7 (save and apply as two
   actions; drift flag-only; the first edit set). **On the three drafts, later still:** the 0.8
   default kept; role-less hosts as `unroled-endpoint`; the occupancy ladder in the group key —
   all in §3.4's refinement.

## 9. Non-goals and honest limits

A twin stays an approximation, and the spec version is what bounds "faithful." Out of scope for
v1: the L2 switch fabric (VMs on SDN do not reproduce it); payload content; real user behavior
beyond persona shape; secrets of any kind; Windows patch levels below image granularity; timing
below the hourly envelope. Each may become a row in a later spec version; none is a reason to
withhold the label from a build that passes v1.
